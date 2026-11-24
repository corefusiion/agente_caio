"""Deterministic single-process authority/commit tests. No real credentials or network."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from caiocore.server.google_calendar_service import GoogleCalendarService, ServiceError
from caiocore.server.google_session_authority import CalendarSessionAuthority, SessionError
from test_google_calendar_service_offline import Google, no_network, state, TOKEN_URL, REVOKE_URL


@pytest.fixture
def system(tmp_path):
    tmp_path.chmod(0o700)
    cfg = tmp_path / 'google-client.json'
    cfg.write_text(json.dumps({'client_id': 'fake-client', 'client_secret': 'fake-secret'}))
    cfg.chmod(0o600)
    clock = [100.0]
    auth = CalendarSessionAuthority(clock=lambda: clock[0])
    ctx = auth.issue(lifetime=10)
    fake = Google()
    service = GoogleCalendarService(tmp_path, auth, transport=fake)
    yield service, fake, auth, ctx, clock, tmp_path
    service.close()


def pending(s, ctx):
    return state(s, ctx)


def cb(s, ctx, st):
    return s.callback(ctx, [('state', st), ('code', 'fake-code')])


def test_success_rotation_and_disconnect(system):
    s, fake, a, ctx, _, path = system
    assert cb(s, ctx, pending(s, ctx))['status'] == 'verified_at_last_check'
    fake.rotation = True
    assert s.verify(ctx)['status'] == 'verified_at_last_check'
    assert json.loads((path / 'google-token.json').read_text())['refresh_token'] == 'rotated-refresh'
    assert s.list_events(ctx)['items'][0]['summary'] == 'private-event'
    assert s.disconnect(ctx)['revocation'] == 'confirmed'
    assert not (path / 'google-token.json').exists()
    assert not (path / 'calendar-transaction.pending').exists()


def test_status_does_not_claim_new_stored_record_was_verified(system):
    from caiocore.server.google_oauth_local import PrivateTokenStore
    s, _, _, ctx, _, path = system
    cb(s, ctx, pending(s, ctx))
    assert s.status(ctx)['status'] == 'stored_unverified'
    other = PrivateTokenStore(path)
    try:
        record = other.load()
        record['refresh_token'] = 'different-fake-token'
        other.save(record)
    finally:
        other.close()
    assert s.status(ctx)['status'] == 'stored_unverified'


def test_revocation_during_exchange_prevents_save(system):
    s, fake, a, ctx, _, path = system
    st = pending(s, ctx)
    fake.before_response = lambda url: a.revoke(ctx) if url == TOKEN_URL else None
    with pytest.raises(ServiceError): cb(s, ctx, st)
    assert not (path / 'google-token.json').exists()
    assert [c[1] for c in fake.calls] == [TOKEN_URL, REVOKE_URL]
    assert not (path / 'calendar-transaction.pending').exists()


def test_concurrent_revoke_waits_for_save_then_linearizes(system, monkeypatch):
    s, _, a, ctx, _, path = system
    st = pending(s, ctx)
    entered, resume, attempting = threading.Event(), threading.Event(), threading.Event()
    save = s._store.save
    def paused(record):
        save(record)
        entered.set()
        assert resume.wait(5)
    monkeypatch.setattr(s._store, 'save', paused)
    def revoke():
        attempting.set()
        a.revoke(ctx)
    with ThreadPoolExecutor(max_workers=2) as pool:
        callback = pool.submit(cb, s, ctx, st)
        assert entered.wait(5)
        revoker = pool.submit(revoke)
        assert attempting.wait(5)
        assert not revoker.done()
        resume.set()
        assert callback.result(5)['status'] == 'verified_at_last_check'
        revoker.result(5)
    # Revocation after commit ends the browser session, not the Google grant.
    assert (path / 'google-token.json').exists()
    with pytest.raises(ServiceError): s.status(ctx)
    new = a.issue()
    # Current stored content cannot be tied atomically to the earlier check.
    assert s.status(new)['status'] == 'stored_unverified'


def test_reentrant_revoke_cannot_deadlock_or_commit_even_if_caught(system, monkeypatch):
    s, _, a, ctx, _, path = system
    st = pending(s, ctx)
    save = s._store.save
    def attack(record):
        save(record)
        with pytest.raises(SessionError): a.revoke(ctx)
    monkeypatch.setattr(s._store, 'save', attack)
    with pytest.raises(ServiceError): cb(s, ctx, st)
    assert not (path / 'google-token.json').exists()
    assert not (path / 'calendar-transaction.pending').exists()


def test_expiry_during_save_rolls_back(system, monkeypatch):
    s, _, _, ctx, clock, path = system
    st = pending(s, ctx)
    save = s._store.save
    def expires(record):
        save(record)
        clock[0] = 110.0
    monkeypatch.setattr(s._store, 'save', expires)
    with pytest.raises(ServiceError): cb(s, ctx, st)
    assert not (path / 'google-token.json').exists()


def test_failed_cleanup_quarantines_restart(system, monkeypatch):
    s, fake, a, ctx, clock, path = system
    st = pending(s, ctx)
    save = s._store.save
    def expires(record):
        save(record)
        clock[0] = 110.0
    monkeypatch.setattr(s._store, 'save', expires)
    monkeypatch.setattr(s._store, 'delete_local', lambda: (_ for _ in ()).throw(OSError()))
    with pytest.raises(ServiceError, match='local removal unconfirmed'): cb(s, ctx, st)
    assert (path / 'calendar-transaction.pending').exists()
    assert (path / 'google-token.json').exists()
    fresh = a.issue()
    with pytest.raises(ServiceError): s.status(fresh)
    s.close()
    other = GoogleCalendarService(path, a, transport=fake)
    try:
        with pytest.raises(ServiceError): other.status(fresh)
    finally:
        other.close()


def test_ambiguous_exchange_failure_quarantines(system):
    s, fake, a, ctx, _, path = system
    st = pending(s, ctx)
    def fail(*args): raise OSError('offline')
    s._client._transport = fail
    with pytest.raises(ServiceError): cb(s, ctx, st)
    assert (path / 'calendar-transaction.pending').exists()
    with pytest.raises(ServiceError): s.start(ctx)


def test_reentrant_service_call_fails_fast(system):
    s, fake, _, ctx, _, _ = system
    st = pending(s, ctx)
    def reenter(url):
        if url == TOKEN_URL:
            with pytest.raises(ServiceError, match='Reentrant'): s.status(ctx)
    fake.before_response = reenter
    assert cb(s, ctx, st)['status'] == 'verified_at_last_check'


def test_revocation_during_rotation_never_publishes_or_recreates(system):
    s, fake, a, ctx, _, path = system
    cb(s, ctx, pending(s, ctx))
    fake.rotation = True
    fake.before_response = lambda url: a.revoke(ctx) if url == TOKEN_URL else None
    with pytest.raises(ServiceError): s.verify(ctx)
    assert not (path / 'google-token.json').exists()
    assert not (path / 'calendar-transaction.pending').exists()
    fresh = a.issue()
    assert s.status(fresh)['status'] == 'not_configured'


def test_remote_cleanup_failure_keeps_quarantine(system):
    s, fake, a, ctx, _, path = system
    st = pending(s, ctx)
    fake.fail_revoke = True
    fake.before_response = lambda url: a.revoke(ctx) if url == TOKEN_URL else None
    with pytest.raises(ServiceError, match='Google revocation unconfirmed'): cb(s, ctx, st)
    assert not (path / 'google-token.json').exists()
    assert (path / 'calendar-transaction.pending').exists()
    with pytest.raises(ServiceError): s.status(a.issue())


def test_session_replacement_during_exchange_blocks_commit(system):
    s, fake, a, ctx, _, path = system
    st = pending(s, ctx)
    new = []
    fake.before_response = lambda url: new.append(a.issue()) if url == TOKEN_URL else None
    with pytest.raises(ServiceError): cb(s, ctx, st)
    assert not (path / 'google-token.json').exists()
    assert s.status(new[0])['status'] == 'not_configured'


def test_reentrant_replacement_poisoned_transaction(system, monkeypatch):
    s, _, a, ctx, _, path = system
    st = pending(s, ctx)
    save = s._store.save
    def replace(record):
        save(record)
        with pytest.raises(SessionError): a.issue()
    monkeypatch.setattr(s._store, 'save', replace)
    with pytest.raises(ServiceError): cb(s, ctx, st)
    assert not (path / 'google-token.json').exists()


def test_bridge_constructor_is_disabled(tmp_path):
    from dashboard.calendar_local_bridge import CalendarBridge
    with pytest.raises(RuntimeError, match='disabled'):
        CalendarBridge(tmp_path)


def test_crash_after_save_keeps_marker_and_restart_quarantines(system):
    import subprocess
    import sys
    s, fake, a, _, _, path = system
    s.close()
    script = r"""
import os, sys, json, socket
from urllib.parse import parse_qs, urlsplit
from caiocore.server.google_calendar_client import TransportResponse, CALENDAR_SCOPE
from caiocore.server.google_calendar_service import GoogleCalendarService
from caiocore.server.google_session_authority import CalendarSessionAuthority
socket.socket.connect = lambda *a: (_ for _ in ()).throw(RuntimeError('no network'))
def transport(*args):
    return TransportResponse(200, json.dumps({'access_token': 'fake-access', 'token_type': 'Bearer', 'scope': CALENDAR_SCOPE, 'expires_in': 3600, 'refresh_token': 'fake-refresh'}).encode())
a = CalendarSessionAuthority()
c = a.issue()
s = GoogleCalendarService(sys.argv[1], a, transport=transport)
st = parse_qs(urlsplit(s.start(c)['authorization_url']).query)['state'][0]
save = s._store.save
def crash(record):
    save(record)
    os._exit(42)
s._store.save = crash
s.callback(c, [('state', st), ('code', 'fake-code')])
"""
    child = subprocess.run([sys.executable, '-c', script, str(path)], timeout=10, capture_output=True)
    assert child.returncode == 42, child.stderr
    assert (path / 'google-token.json').exists()
    assert (path / 'calendar-transaction.pending').exists()
    restart = GoogleCalendarService(path, a, transport=fake)
    try:
        with pytest.raises(ServiceError): restart.status(a.issue())
    finally:
        restart.close()


def test_other_process_with_authority_cannot_open_same_directory(system):
    import subprocess
    import sys
    s, _, _, _, _, path = system
    script = """
import sys
from caiocore.server.google_calendar_service import GoogleCalendarService, ServiceError
from caiocore.server.google_session_authority import CalendarSessionAuthority
try:
    s = GoogleCalendarService(sys.argv[1], CalendarSessionAuthority())
except ServiceError:
    sys.exit(7)
s.close()
"""
    child = subprocess.run([sys.executable, '-c', script, str(path)], timeout=10, capture_output=True)
    assert child.returncode == 7, child.stderr


def test_expiry_during_events_never_returns_private_data(system):
    from caiocore.server.google_calendar_client import EVENTS_URL
    s, fake, _, ctx, clock, _ = system
    cb(s, ctx, pending(s, ctx))
    fake.before_response = lambda url: clock.__setitem__(0, 110.0) if url.startswith(EVENTS_URL) else None
    with pytest.raises(ServiceError) as err:
        s.list_events(ctx)
    assert 'private-event' not in str(err.value)


def test_disconnect_queued_after_callback_cannot_resurrect(system):
    s, fake, _, ctx, _, path = system
    st = pending(s, ctx)
    entered, release = threading.Event(), threading.Event()
    def slow(url):
        if url == TOKEN_URL:
            entered.set()
            assert release.wait(5)
    fake.before_response = slow
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cb, s, ctx, st)
        assert entered.wait(5)
        second = pool.submit(s.disconnect, ctx)
        release.set()
        assert first.result(5)['status'] == 'verified_at_last_check'
        assert second.result(5)['status'] == 'not_configured'
    assert not (path / 'google-token.json').exists()
