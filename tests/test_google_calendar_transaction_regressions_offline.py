"""Offline regressions for ambiguous revoke and locally rejected OAuth callbacks."""
import pytest

from caiocore.server.google_calendar_service import GoogleCalendarService, ServiceError
from caiocore.server.google_calendar_client import REVOKE_URL, TOKEN_URL, TransportResponse
from test_google_calendar_authority_offline import system, pending, cb
from test_google_calendar_service_offline import no_network


def assert_blocked(service, ctx):
    for op in (lambda: service.status(ctx), lambda: service.start(ctx),
               lambda: service.callback(ctx, [('state', 'x'), ('code', 'x')]),
               lambda: service.verify(ctx), lambda: service.list_events(ctx),
               lambda: service.disconnect(ctx)):
        with pytest.raises(ServiceError, match='recovery required'):
            op()


@pytest.mark.parametrize('failure', ['http', 'exception', 'invalid_response'])
def test_disconnect_unconfirmed_quarantines_across_restart(system, failure):
    s, fake, auth, ctx, _, path = system
    cb(s, ctx, pending(s, ctx))
    original = s._client._transport
    calls = []
    def transport(method, url, headers, body):
        calls.append(url)
        if url == REVOKE_URL:
            if failure == 'exception':
                raise OSError('offline')
            if failure == 'invalid_response':
                return object()
            return TransportResponse(400, b'')
        return original(method, url, headers, body)
    s._client._transport = transport
    result = s.disconnect(ctx)
    assert result == {'status': 'recovery_required', 'local_removal': 'confirmed', 'revocation': 'unconfirmed'}
    assert calls == [REVOKE_URL]
    assert not (path / 'google-token.json').exists()
    assert (path / 'calendar-transaction.pending').exists()
    assert_blocked(s, ctx)
    s.close()
    restarted = GoogleCalendarService(path, auth, transport=transport)
    try:
        assert_blocked(restarted, ctx)
        assert calls == [REVOKE_URL]
    finally:
        restarted.close()


def test_invalid_and_stale_state_do_not_quarantine_or_consume_valid_state(system):
    s, fake, _, ctx, _, path = system
    stale = pending(s, ctx)
    valid = pending(s, ctx)  # start cancels the previous state
    for invalid in ('wrong', 'z' * 43, stale):
        with pytest.raises(ServiceError):
            cb(s, ctx, invalid)
        assert not (path / 'calendar-transaction.pending').exists()
        assert not fake.calls
        assert s.status(ctx)['status'] == 'not_configured'
    assert cb(s, ctx, valid)['status'] == 'verified_at_last_check'
    assert [c[1] for c in fake.calls] == [TOKEN_URL]
    assert not (path / 'calendar-transaction.pending').exists()


def test_state_bound_to_old_session_rejected_without_quarantine(system):
    s, fake, auth, ctx, _, path = system
    old_state = pending(s, ctx)
    new_ctx = auth.issue()
    with pytest.raises(ServiceError):
        cb(s, new_ctx, old_state)
    assert not (path / 'calendar-transaction.pending').exists()
    assert not fake.calls
    assert cb(s, new_ctx, pending(s, new_ctx))['status'] == 'verified_at_last_check'


def test_expired_state_rejected_before_marker(system, monkeypatch):
    s, fake, _, ctx, _, path = system
    clock = [100.0]
    monkeypatch.setattr(s._client._pending, '_clock', lambda: clock[0])
    state = pending(s, ctx)
    clock[0] += 301
    with pytest.raises(ServiceError):
        cb(s, ctx, state)
    assert not (path / 'calendar-transaction.pending').exists()
    assert not fake.calls
    assert cb(s, ctx, pending(s, ctx))['status'] == 'verified_at_last_check'


def test_failure_during_actual_exchange_keeps_durable_marker(system):
    s, _, auth, ctx, _, path = system
    state = pending(s, ctx)
    seen = []
    def fail(method, url, headers, body):
        seen.append(url)
        assert (path / 'calendar-transaction.pending').exists()
        raise OSError('ambiguous')
    s._client._transport = fail
    with pytest.raises(ServiceError):
        cb(s, ctx, state)
    assert seen == [TOKEN_URL]
    assert_blocked(s, ctx)
    s.close()
    restarted = GoogleCalendarService(path, auth, transport=fail)
    try:
        assert_blocked(restarted, ctx)
        assert seen == [TOKEN_URL]
    finally:
        restarted.close()


def test_denied_state_replay_does_not_quarantine(system):
    s, fake, _, ctx, _, path = system
    state = pending(s, ctx)
    assert s.callback(ctx, [('state', state), ('error', 'access_denied')]) == {'status': 'authorization_denied'}
    with pytest.raises(ServiceError):
        cb(s, ctx, state)
    assert not (path / 'calendar-transaction.pending').exists()
    assert not fake.calls
    assert cb(s, ctx, pending(s, ctx))['status'] == 'verified_at_last_check'


def test_marker_write_failure_never_sends_exchange(system, monkeypatch):
    from caiocore.server import google_calendar_service as module
    s, fake, _, ctx, _, path = system
    state = pending(s, ctx)
    mark = s._client._mark_exchange
    def fail_mark():
        with monkeypatch.context() as patch:
            patch.setattr(module.os, 'fsync', lambda fd: (_ for _ in ()).throw(OSError('sync failed')))
            mark()
    monkeypatch.setattr(s._client, '_mark_exchange', fail_mark)
    with pytest.raises(ServiceError):
        cb(s, ctx, state)
    assert not fake.calls
    assert (path / 'calendar-transaction.pending').exists()
    assert_blocked(s, ctx)


def test_revoke_confirmed_allows_future_use(system):
    s, fake, _, ctx, _, path = system
    cb(s, ctx, pending(s, ctx))
    assert s.disconnect(ctx) == {'status': 'not_configured', 'revocation': 'confirmed'}
    assert not (path / 'calendar-transaction.pending').exists()
    assert cb(s, ctx, pending(s, ctx))['status'] == 'verified_at_last_check'
