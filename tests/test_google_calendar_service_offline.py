"""Offline controller tests: no real Google requests or credentials."""
import json
import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest

from caiocore.server.google_calendar_client import TransportResponse, TOKEN_URL, REVOKE_URL, EVENTS_URL, CALENDAR_SCOPE
from caiocore.server.google_calendar_service import GoogleCalendarService, ServiceError, load_private_client_config
from caiocore.server.google_oauth_local import REDIRECT_URI, OAuthError
from caiocore.server.google_session_authority import CalendarSessionAuthority

S1 = b'1' * 32
S2 = b'2' * 32

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def block(*a, **kw): raise AssertionError('network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', block)
    monkeypatch.setattr(socket.socket, 'connect_ex', block)
    monkeypatch.setattr(socket, 'create_connection', block)
    monkeypatch.setattr(socket, 'getaddrinfo', block)

class Google:
    def __init__(self):
        self.calls = []
        self.fail_revoke = False
        self.rotation = False
        self.before_response = None
    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        if self.before_response:
            self.before_response(url)
        if url == REVOKE_URL:
            return TransportResponse(400 if self.fail_revoke else 200, b'')
        if url == TOKEN_URL:
            values = parse_qs(body.decode())
            data = {'access_token': 'fake-access', 'token_type': 'Bearer', 'scope': CALENDAR_SCOPE, 'expires_in': 3600}
            if values['grant_type'] == ['authorization_code']:
                data['refresh_token'] = 'fake-refresh'
            if self.rotation and values['grant_type'] == ['refresh_token']:
                data['refresh_token'] = 'rotated-refresh'
            return TransportResponse(200, json.dumps(data).encode())
        assert url.startswith(EVENTS_URL + '?')
        return TransportResponse(200, b'{"items":[{"summary":"private-event"}]}')

@pytest.fixture
def setup(tmp_path):
    tmp_path.chmod(0o700)
    cfg = tmp_path / 'google-client.json'
    cfg.write_text(json.dumps({'client_id': 'fake-client', 'client_secret': 'fake-secret'}))
    cfg.chmod(0o600)
    auth = {'owner': S1, 'other-session': S2}
    def resolve(ctx):
        if ctx not in auth: raise ValueError('unauthorized')
        return auth[ctx]
    fake = Google()
    service = GoogleCalendarService(tmp_path, resolve, transport=fake)
    yield service, fake, auth, tmp_path
    service.close()

@pytest.fixture
def authority_setup(tmp_path):
    tmp_path.chmod(0o700)
    cfg = tmp_path / 'google-client.json'
    cfg.write_text(json.dumps({'client_id': 'fake-client', 'client_secret': 'fake-secret'}))
    cfg.chmod(0o600)
    authority = CalendarSessionAuthority()
    owner = authority.issue()
    fake = Google()
    service = GoogleCalendarService(tmp_path, authority, transport=fake)
    service._test_owner = owner
    yield service, fake, authority, tmp_path
    service.close()


def state(s, ctx='owner'):
    if ctx == 'owner' and hasattr(s, '_test_owner'):
        ctx = s._test_owner
    url = s.start(ctx)['authorization_url']
    assert url.startswith('https://accounts.google.com/o/oauth2/v2/auth?')
    query = parse_qs(urlsplit(url).query)
    assert query['redirect_uri'] == [REDIRECT_URI]
    assert query['scope'] == [CALENDAR_SCOPE]
    assert query['code_challenge_method'] == ['S256']
    return query['state'][0]

def connect(s):
    return s.callback(getattr(s, '_test_owner', 'owner'), [('state', state(s)), ('code', 'fake-code')])

def test_full_flow_status_restart_refresh_events_disconnect(authority_setup):
    s, fake, auth, path = authority_setup
    assert s.status(s._test_owner)['status'] == 'not_configured'
    assert connect(s)['status'] == 'verified_at_last_check'
    assert (path / 'google-token.json').stat().st_mode & 0o777 == 0o600
    assert s.status(s._test_owner)['status'] == 'stored_unverified'
    assert 'fake-' not in repr(s.status(s._test_owner))
    fake.rotation = True
    assert s.verify(s._test_owner)['status'] == 'verified_at_last_check'
    assert json.loads((path / 'google-token.json').read_text())['refresh_token'] == 'rotated-refresh'
    assert s.list_events(s._test_owner)['items'][0]['summary'] == 'private-event'
    with pytest.raises(ServiceError):
        GoogleCalendarService(path, auth, transport=fake)
    s.close()
    second = GoogleCalendarService(path, auth, transport=fake)
    try:
        assert second.status(s._test_owner)['status'] == 'stored_unverified'
        assert second.disconnect(s._test_owner) == {'status': 'not_configured', 'revocation': 'confirmed'}
        assert not (path / 'google-token.json').exists()
        assert second.disconnect(s._test_owner)['revocation'] == 'not_attempted'
    finally:
        second.close()

def test_failed_revoke_deletes_locally_without_claiming_remote_success(authority_setup):
    s, fake, _, path = authority_setup
    connect(s)
    fake.fail_revoke = True
    assert s.disconnect(s._test_owner) == {'status': 'recovery_required', 'local_removal': 'confirmed', 'revocation': 'unconfirmed'}
    assert (path / 'calendar-transaction.pending').exists()
    with pytest.raises(ServiceError): s.status(s._test_owner)
    assert not (path / 'google-token.json').exists()

def test_reject_unauthenticated_every_operation(authority_setup):
    s, fake, _, _ = authority_setup
    for action in (lambda: s.start('x'), lambda: s.status('x'), lambda: s.verify('x'),
                   lambda: s.list_events('x'), lambda: s.disconnect('x'),
                   lambda: s.callback('x', [])):
        with pytest.raises(ServiceError): action()
    assert not fake.calls

def test_wrong_session_and_replay_and_start_cancels_previous(authority_setup):
    s, fake, _, _ = authority_setup
    old = state(s)
    current = state(s)
    with pytest.raises(ServiceError): s.callback(s._test_owner, [('state', old), ('code', 'x')])
    with pytest.raises(ServiceError): s.callback(object(), [('state', current), ('code', 'x')])
    assert not fake.calls
    s.callback(s._test_owner, [('state', current), ('code', 'x')])
    with pytest.raises(ServiceError): s.callback(s._test_owner, [('state', current), ('code', 'x')])
    with pytest.raises(ServiceError): s.start(s._test_owner)
    assert len(fake.calls) == 1

def test_replaced_session_cannot_use_old_authorization(authority_setup):
    s, fake, auth, path = authority_setup
    old_owner = s._test_owner
    st = state(s)
    replacement = auth.issue()
    for ctx in (old_owner, replacement):
        with pytest.raises(ServiceError):
            s.callback(ctx, [('state', st), ('code', 'fake-code')])
    assert not fake.calls
    assert not (path / 'google-token.json').exists()
    assert not (path / 'calendar-transaction.pending').exists()


@pytest.mark.parametrize('params', [[], [('state','x'), ('state','y'), ('code','z')],
    [('state','x'), ('code','z'), ('error','x')], [('state','x'), ('code','z'), ('extra','x')],
    [('state','x'), ('code','')], [('state','x'), ('code', 'x'*8193)], [('code','x')]])
def test_malformed_callbacks_do_not_exchange(authority_setup, params):
    s, fake, _, _ = authority_setup
    with pytest.raises(ServiceError): s.callback(s._test_owner, params)
    assert not fake.calls

def test_denial_consumed_and_disconnect_cancels_pending(authority_setup):
    s, fake, _, _ = authority_setup
    st = state(s)
    assert s.callback(s._test_owner, [('state', st), ('error', 'access_denied')]) == {'status': 'authorization_denied'}
    with pytest.raises(ServiceError): s.callback(s._test_owner, [('state', st), ('code', 'x')])
    st = state(s)
    s.disconnect(s._test_owner)
    with pytest.raises(ServiceError): s.callback(s._test_owner, [('state', st), ('code', 'x')])
    assert not fake.calls

def test_session_revoked_during_exchange_prevents_save_and_attempts_revoke(authority_setup):
    s, fake, auth, path = authority_setup
    st = state(s)
    def before(url):
        if url == TOKEN_URL: auth.revoke(s._test_owner)
    fake.before_response = before
    with pytest.raises(ServiceError): s.callback(s._test_owner, [('state', st), ('code', 'fake-code')])
    assert not (path / 'google-token.json').exists()
    assert [x[1] for x in fake.calls] == [TOKEN_URL, REVOKE_URL]

def test_callback_disconnect_race_serializes_and_cannot_resurrect(authority_setup):
    s, fake, _, path = authority_setup
    st = state(s)
    entered, release = threading.Event(), threading.Event()
    def before(url):
        if url == TOKEN_URL:
            entered.set()
            assert release.wait(5)
    fake.before_response = before
    with ThreadPoolExecutor(2) as pool:
        cb = pool.submit(s.callback, s._test_owner, [('state', st), ('code', 'x')])
        assert entered.wait(5)
        disc = pool.submit(s.disconnect, s._test_owner)
        release.set()
        assert cb.result(5)['status'] == 'verified_at_last_check'
        assert disc.result(5)['status'] == 'not_configured'
    assert not (path / 'google-token.json').exists()

def test_failed_exchange_consumes_state_no_secret_in_error(authority_setup):
    s, _, auth, path = authority_setup
    st = state(s)
    calls = []
    def bad(*a):
        calls.append(True)
        raise RuntimeError('fake-code fake-secret fake-refresh')
    s._client._transport = bad
    for _ in range(2):
        with pytest.raises(ServiceError) as e: s.callback(s._test_owner, [('state', st), ('code', 'fake-code')])
        assert 'fake-' not in str(e.value)
    assert calls == [True]
    assert (path / 'calendar-transaction.pending').exists()
    with auth.transaction(s._test_owner) as session:
        with pytest.raises(OAuthError): s._client._pending.consume(session, st)

def test_client_config_permissions_symlink_hardlink_and_duplicates(authority_setup):
    _, _, _, path = authority_setup
    cfg = path / 'google-client.json'
    cfg.chmod(0o644)
    with pytest.raises(ServiceError): load_private_client_config(path)
    cfg.chmod(0o600)
    hard = path / 'hard'; os.link(cfg, hard)
    with pytest.raises(ServiceError): load_private_client_config(path)
    hard.unlink()
    cfg.write_text('{"client_id":"a", "client_id":"b", "client_secret":"c"}')
    with pytest.raises(ServiceError): load_private_client_config(path)
    cfg.unlink(); cfg.symlink_to('/etc/passwd')
    with pytest.raises(ServiceError): load_private_client_config(path)
    path.chmod(0o755)
    with pytest.raises(ServiceError): load_private_client_config(path)
    path.chmod(0o700)

def test_wrong_scope_and_damaged_store_fail_closed(authority_setup):
    s, fake, _, path = authority_setup
    p = path / 'google-token.json'
    p.write_text(json.dumps({'refresh_token':'fake', 'scopes':['https://www.googleapis.com/auth/gmail.readonly']})); p.chmod(0o600)
    for op in (lambda: s.start(s._test_owner), lambda: s.status(s._test_owner), lambda: s.verify(s._test_owner), lambda: s.disconnect(s._test_owner)):
        with pytest.raises(ServiceError): op()
    assert not fake.calls
