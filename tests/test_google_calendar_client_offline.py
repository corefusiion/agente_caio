"""Synthetic offline tests. No credentials and no external network calls."""
import base64
import hashlib
import json
import socket
from urllib.parse import parse_qs, urlparse

import pytest

from caiocore.server.google_oauth_local import OAuthError, REDIRECT_URI, SCOPES
from caiocore.server import google_calendar_client as mod

SESSION = b'x' * 32
SCOPE = SCOPES['calendar_read']


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError('Network is forbidden in offline tests')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)


def token(**changes):
    return {'access_token': 'fake-access', 'refresh_token': 'fake-refresh',
            'token_type': 'Bearer', 'scope': SCOPE, 'expires_in': 3600, **changes}


def record():
    return {'refresh_token': 'fake-refresh', 'scopes': [SCOPE]}


class Fake:
    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = json.dumps(token()).encode() if body is None else body

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return mod.TransportResponse(self.status, self.body)


def authorization(client):
    return {k: v[0] for k, v in parse_qs(urlparse(client.start(SESSION)).query).items()}


def test_exchange_binds_state_session_pkce_and_scope():
    fake = Fake()
    client = mod.GoogleCalendarClient('fake-client', client_secret='fake-client-secret', transport=fake)
    q = authorization(client)
    assert q['scope'] == SCOPE
    with pytest.raises(OAuthError):
        client.exchange(b'y' * 32, q['state'], 'fake-code')
    assert not fake.calls
    result = client.exchange(SESSION, q['state'], 'fake-code')
    method, url, headers, body = fake.calls[0]
    values = parse_qs(body.decode())
    assert (method, url) == ('POST', mod.TOKEN_URL)
    assert values['redirect_uri'] == [REDIRECT_URI]
    assert values['code'] == ['fake-code'] and values['client_secret'] == ['fake-client-secret']
    challenge = base64.urlsafe_b64encode(hashlib.sha256(values['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
    assert challenge == q['code_challenge'] and q['code_challenge_method'] == 'S256'
    assert headers['Content-Type'] == 'application/x-www-form-urlencoded'
    assert result.access_token == 'fake-access' and result.record == record()
    assert 'fake-access' not in repr(result) and 'fake-refresh' not in repr(result)
    with pytest.raises(OAuthError):
        client.exchange(SESSION, q['state'], 'fake-code')
    assert len(fake.calls) == 1


def test_failed_exchange_consumes_state_and_sanitizes_errors():
    fake = Fake(status=400, body=b'fake-code fake-refresh fake-client-secret')
    client = mod.GoogleCalendarClient('fake-client', client_secret='fake-client-secret', transport=fake)
    q = authorization(client)
    with pytest.raises(OAuthError) as exc:
        client.exchange(SESSION, q['state'], 'fake-code')
    assert all(x not in str(exc.value) for x in ('fake-code', 'fake-refresh', 'fake-client-secret'))
    with pytest.raises(OAuthError):
        client.exchange(SESSION, q['state'], 'fake-code')
    assert len(fake.calls) == 1


def test_refresh_omitted_scope_and_refresh_and_rotation():
    fake = Fake(body=json.dumps({'access_token': 'new-access', 'token_type': 'bearer', 'expires_in': 120}).encode())
    client = mod.GoogleCalendarClient('fake-client', transport=fake)
    result = client.refresh(record())
    assert result.access_token == 'new-access' and result.record == record()
    assert parse_qs(fake.calls[0][3].decode())['refresh_token'] == ['fake-refresh']
    fake.body = json.dumps(token(refresh_token='rotated')).encode()
    assert client.refresh(record()).record['refresh_token'] == 'rotated'


def test_revocation_and_paged_events_are_separate():
    fake = Fake(body=b'')
    client = mod.GoogleCalendarClient('fake-client', transport=fake)
    assert client.revoke(record()) is None
    assert fake.calls[0][1] == mod.REVOKE_URL
    assert parse_qs(fake.calls[0][3].decode()) == {'token': ['fake-refresh']}
    fake.body = json.dumps({'items': [{'summary': 'private'}], 'nextPageToken': 'next-1'}).encode()
    data = client.list_events('fake-access', page_token='page+one', max_results=2)
    method, url, headers, body = fake.calls[-1]
    assert method == 'GET' and url.startswith(mod.EVENTS_URL + '?') and body is None
    assert parse_qs(urlparse(url).query) == {'maxResults': ['2'], 'pageToken': ['page+one']}
    assert headers['Authorization'] == 'Bearer fake-access'
    assert data['items'][0]['summary'] == 'private' and data['nextPageToken'] == 'next-1'


@pytest.mark.parametrize('bad', [
    {}, {'refresh_token': 'fake', 'scopes': []},
    {'refresh_token': 'fake', 'scopes': [SCOPE, SCOPES['gmail_read']]},
    {'refresh_token': 'bad\nheader', 'scopes': [SCOPE]},
])
def test_record_rejects_wider_scopes_and_injection(bad):
    fake = Fake()
    client = mod.GoogleCalendarClient('fake-client', transport=fake)
    for operation in (client.refresh, client.revoke):
        with pytest.raises(OAuthError):
            operation(bad)
    assert not fake.calls


@pytest.mark.parametrize('bad', [
    token(scope=SCOPES['gmail_read']), token(scope=SCOPE + ' ' + SCOPE),
    token(scope=''), token(refresh_token=''), token(access_token='bad\r\nX:evil'),
    token(token_type='Basic'), token(expires_in=True), token(expires_in=-1),
])
def test_token_response_fail_closed(bad):
    fake = Fake(body=json.dumps(bad).encode())
    client = mod.GoogleCalendarClient('fake-client', transport=fake)
    q = authorization(client)
    with pytest.raises(OAuthError):
        client.exchange(SESSION, q['state'], 'fake-code')


def test_refresh_rejects_explicit_wider_scope():
    fake = Fake(body=json.dumps(token(scope=SCOPE + ' ' + SCOPES['gmail_read'])).encode())
    with pytest.raises(OAuthError):
        mod.GoogleCalendarClient('fake-client', transport=fake).refresh(record())


@pytest.mark.parametrize('status,body', [
    (302, b'location secret'), (401, b'secret'), (500, b'secret'),
    (200, b'not json'), (200, b'[]'), (200, b'{"items":[],"items":[]}'),
    (200, b'{"items":[],"n":NaN}'), (200, b'X' * (mod.MAX_RESPONSE_BYTES + 1)),
])
def test_bad_http_response_is_safe(status, body):
    fake = Fake(status, body)
    client = mod.GoogleCalendarClient('fake-client', transport=fake)
    with pytest.raises(OAuthError) as exc:
        client.list_events('fake-access')
    assert 'secret' not in str(exc.value) and len(fake.calls) == 1


def test_exception_from_transport_does_not_expose_secret():
    def bad(*args):
        raise RuntimeError('fake-access fake-refresh')
    client = mod.GoogleCalendarClient('fake-client', transport=bad)
    with pytest.raises(OAuthError) as exc:
        client.refresh(record())
    assert str(exc.value) == 'Google request failed'
    assert exc.value.__suppress_context__


@pytest.mark.parametrize('access,page,maximum', [
    ('abc\r\nInjected:yes', None, 1), ('abc', 'page\n', 1), ('abc', None, 0),
    ('abc', None, 2501), ('abc', None, True),
])
def test_events_input_validation(access, page, maximum):
    fake = Fake()
    client = mod.GoogleCalendarClient('fake-client', transport=fake)
    with pytest.raises(OAuthError):
        client.list_events(access, page_token=page, max_results=maximum)
    assert not fake.calls


def test_real_transport_is_allowlisted_verified_no_redirect_timeout_and_bounded(monkeypatch):
    calls = []
    class Response:
        status = 302
        def read(self, limit):
            calls.append(('read', limit))
            return b'redirect'
    class Connection:
        def __init__(self, host, timeout, context):
            calls.append(('connect', host, timeout, context))
        def set_debuglevel(self, level):
            calls.append(('debug', level))
        def request(self, method, path, body, headers):
            calls.append(('request', method, path, body, headers))
        def getresponse(self):
            return Response()
        def close(self):
            calls.append(('close',))
    monkeypatch.setattr(mod.http.client, 'HTTPSConnection', Connection)
    response = mod.https_transport('POST', mod.TOKEN_URL, {}, b'fake')
    assert response.status == 302
    assert calls[0][1:3] == ('oauth2.googleapis.com', mod.TIMEOUT_SECONDS)
    assert calls[0][3].check_hostname and calls[0][3].verify_mode == mod.ssl.CERT_REQUIRED
    assert ('debug', 0) in calls and ('read', mod.MAX_RESPONSE_BYTES + 1) in calls
    assert sum(c[0] == 'request' for c in calls) == 1
    for method, url in [('POST', 'https://attacker.test/token'), ('GET', 'http://www.googleapis.com/calendar/v3/calendars/primary/events'), ('POST', mod.TOKEN_URL + '?x=1')]:
        with pytest.raises(OAuthError):
            mod.https_transport(method, url, {}, None)


def test_real_transport_limit_and_exception_sanitization(monkeypatch):
    class TooBig:
        def __init__(self, *args, **kwargs): pass
        def set_debuglevel(self, level): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return self
        def read(self, limit): return b'x' * limit
        def close(self): pass
    monkeypatch.setattr(mod.http.client, 'HTTPSConnection', TooBig)
    with pytest.raises(OAuthError, match='too large'):
        mod.https_transport('GET', mod.EVENTS_URL, {}, None)
    class Broken(TooBig):
        def request(self, *args, **kwargs): raise RuntimeError('secret')
    monkeypatch.setattr(mod.http.client, 'HTTPSConnection', Broken)
    with pytest.raises(OAuthError) as exc:
        mod.https_transport('GET', mod.EVENTS_URL, {}, None)
    assert str(exc.value) == 'Google request failed'
