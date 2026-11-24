"""Additional failure and session-lifetime tests; no external I/O."""
import pytest
from test_google_calendar_service_offline import authority_setup, no_network, connect, state, TOKEN_URL, EVENTS_URL, REVOKE_URL
from caiocore.server.google_calendar_service import ServiceError


def test_expiry_during_event_request_does_not_return_private_events(authority_setup):
    s, fake, auth, _ = authority_setup
    connect(s)
    def before(url):
        if url.startswith(EVENTS_URL): auth.revoke(s._test_owner)
    fake.before_response = before
    with pytest.raises(ServiceError) as e:
        s.list_events(s._test_owner)
    assert 'private-event' not in str(e.value)


def test_storage_failure_revocation_unconfirmed_is_explicit(authority_setup, monkeypatch):
    s, fake, _, path = authority_setup
    st = state(s)
    fake.fail_revoke = True
    def fail(*a): raise OSError('fake-refresh')
    monkeypatch.setattr(s._store, 'save', fail)
    with pytest.raises(ServiceError) as e:
        s.callback(s._test_owner, [('state', st), ('code', 'fake-code')])
    assert 'unconfirmed' in str(e.value)
    assert 'fake-' not in str(e.value)
    assert [c[1] for c in fake.calls] == [TOKEN_URL, REVOKE_URL]
    assert not (path / 'google-token.json').exists()
    assert (path / 'calendar-transaction.pending').exists()
    with pytest.raises(ServiceError): s.status(s._test_owner)
