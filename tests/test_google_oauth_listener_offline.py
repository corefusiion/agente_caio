"""Real listener and service logic with in-memory HTTP and fake Google transport.

No TCP bind/connect or DNS is used. The in-memory server is NOT a test of OS
socket behavior or a claim that the disabled dashboard bridge is operational.
"""
import http.client
import io
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import pytest

import caiocore.server.google_oauth_listener as module
from caiocore.server.google_oauth_listener import GoogleOAuthLoopbackListener, ListenerError
from caiocore.server.google_calendar_service import GoogleCalendarService, ServiceError
from caiocore.server.google_session_authority import CalendarSessionAuthority
from test_google_calendar_service_offline import Google, TOKEN_URL

HOST, PORT = '127.0.0.1', 8765
PATH = '/google/oauth/callback'
REAL_SERVER = module._Server


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs): raise AssertionError('network forbidden')
    for name in ('bind', 'connect', 'connect_ex', 'listen'):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


class MemoryServer:
    def __init__(self, adapter):
        self.adapter = adapter
        self.timeout = .1
        self.closed = False
        self.closed_event = threading.Event()
    def handle_request(self):
        self.closed_event.wait(self.timeout)
    def server_close(self):
        self.closed = True
        self.closed_event.set()


class MemorySocket:
    def __init__(self, request):
        self.input = io.BytesIO(request)
        self.output = bytearray()
    def makefile(self, mode, *args, **kwargs):
        assert mode == 'rb'
        return self.input
    def sendall(self, data): self.output.extend(data)


class ResponseSocket:
    def __init__(self, data): self.data = data
    def makefile(self, *args, **kwargs): return io.BytesIO(self.data)


@pytest.fixture
def system(tmp_path, monkeypatch):
    monkeypatch.setattr(module, '_Server', MemoryServer)
    tmp_path.chmod(0o700)
    cfg = tmp_path / 'google-client.json'
    cfg.write_text(json.dumps({'client_id': 'fake-client', 'client_secret': 'fake-secret'}))
    cfg.chmod(0o600)
    authority = CalendarSessionAuthority()
    ctx = authority.issue()
    fake = Google()
    service = GoogleCalendarService(tmp_path, authority, transport=fake)
    listeners = []
    def start(timeout=2):
        obj = GoogleOAuthLoopbackListener(service, timeout=timeout)
        listeners.append(obj)
        url = obj.start(ctx)['authorization_url']
        return obj, parse_qs(urlsplit(url).query)['state'][0]
    yield service, fake, authority, ctx, tmp_path, start
    for obj in listeners:
        obj.cancel()
        assert obj.wait_closed(4)
    service.close()


def req(listener, target, method='GET', host='127.0.0.1:8765', extra=(), peer=HOST):
    raw = (f'{method} {target} HTTP/1.1\r\nHost: {host}\r\n' +
           ''.join(f'{k}: {v}\r\n' for k, v in extra) + '\r\n').encode('ascii')
    sock = MemorySocket(raw)
    module._Handler(sock, (peer, 12345), listener._server)
    response = http.client.HTTPResponse(ResponseSocket(bytes(sock.output)))
    response.begin()
    body = response.read()
    headers = dict(response.getheaders())
    assert headers['Cache-Control'] == 'no-store'
    assert headers['Referrer-Policy'] == 'no-referrer'
    assert "default-src 'none'" in headers['Content-Security-Policy']
    assert b'fake-code' not in body and b'fake-refresh' not in body
    return response.status, body


def test_real_service_success_and_replay(system, capsys):
    service, fake, _, ctx, path, start = system
    listener, st = start()
    status, body = req(listener, f'{PATH}?state={st}&code=fake-code')
    assert status == 200 and 'conectado' in body.decode()
    assert listener.wait_closed(3) and listener._server.closed
    assert [call[1] for call in fake.calls] == [TOKEN_URL]
    assert service.status(ctx)['status'] == 'stored_unverified'
    assert (path / 'google-token.json').exists()
    assert 'fake-code' not in repr(capsys.readouterr())
    with pytest.raises(ServiceError):
        service.callback(ctx, [('state', st), ('code', 'fake-code')])


@pytest.mark.parametrize('target,method,host,extra,peer,expected', [
    (PATH+'?state={state}&code=fake-code', 'POST', '127.0.0.1:8765', (), HOST, 405),
    (PATH+'?state={state}&code=fake-code', 'GET', 'localhost:8765', (), HOST, 400),
    (PATH+'?state={state}&code=fake-code', 'GET', '127.0.0.1:8765', (('Host','evil'),), HOST, 400),
    (PATH+'?state={state}&code=fake-code', 'GET', '127.0.0.1:8765', (('Content-Length','0'),), HOST, 400),
    (PATH+'?state={state}&code=fake-code', 'GET', '127.0.0.1:8765', (('Transfer-Encoding','chunked'),), HOST, 400),
    (PATH+'?state={state}&code=fake-code', 'GET', '127.0.0.1:8765', (), '192.0.2.1', 400),
    ('//google/oauth/callback?state={state}&code=fake-code', 'GET', '127.0.0.1:8765', (), HOST, 400),
    (PATH+'?state={state}&state={state}&code=fake-code', 'GET', '127.0.0.1:8765', (), HOST, 400),
    (PATH+'?state={state}&code=fake-code&error=access_denied', 'GET', '127.0.0.1:8765', (), HOST, 400),
    (PATH+'?state={state}&code=fake-code&extra=1', 'GET', '127.0.0.1:8765', (), HOST, 400),
    (PATH+'?state={state}&code=%zz', 'GET', '127.0.0.1:8765', (), HOST, 400),
    (PATH+'?state={state}&code='+('a'*8300), 'GET', '127.0.0.1:8765', (), HOST, 400),
], ids=['method', 'host', 'duplicate-host', 'content-length', 'transfer-encoding',
        'peer', 'path', 'duplicate-state', 'code-and-error', 'extra-parameter',
        'bad-escape', 'oversized-uri'])
def test_invalid_requests_do_not_exchange(system, target, method, host, extra, peer, expected):
    service, fake, _, ctx, path, start = system
    listener, st = start()
    assert req(listener, target.format(state=st), method, host, extra, peer)[0] == expected
    assert not fake.calls and not (path / 'google-token.json').exists()
    # The rejection must not claim or consume the valid authorization.
    assert req(listener, f'{PATH}?state={st}&code=fake-code')[0] == 200
    assert listener.wait_closed(3)
    assert [call[1] for call in fake.calls] == [TOKEN_URL]
    assert service.status(ctx)['status'] == 'stored_unverified'
    assert (path / 'google-token.json').exists()


def test_wrong_state_does_not_exchange_or_consume_valid_state(system):
    service, fake, _, ctx, path, start = system
    listener, st = start()
    wrong = ('A' if st[0] != 'A' else 'B') + st[1:]
    assert req(listener, f'{PATH}?state={wrong}&code=fake-code')[0] == 400
    assert not fake.calls and not (path / 'google-token.json').exists()
    assert req(listener, f'{PATH}?state={st}&code=fake-code')[0] == 200
    assert listener.wait_closed(3)
    assert [call[1] for call in fake.calls] == [TOKEN_URL]
    assert service.status(ctx)['status'] == 'stored_unverified'


def test_cancel_consumes_pending(system):
    service, fake, _, ctx, _, start = system
    listener, st = start()
    assert listener.cancel() and listener.wait_closed(3)
    with pytest.raises(ServiceError): service.callback(ctx, [('state', st), ('code', 'x')])
    assert not fake.calls


def test_timeout_consumes_pending(system):
    service, fake, _, ctx, _, start = system
    listener, st = start(timeout=.15)
    assert listener.wait_closed(3)
    with pytest.raises(ServiceError): service.callback(ctx, [('state', st), ('code', 'x')])
    assert not fake.calls


def test_bind_failure_prevents_service_start(system, monkeypatch):
    service, fake, _, ctx, _, _ = system
    calls = []
    original = service.start
    def record(context):
        calls.append(context)
        return original(context)
    monkeypatch.setattr(service, 'start', record)
    # Exercise the real server initialization with a wholly fake socket, even
    # where the kernel would reject socket creation before bind is reached.
    created, binds, closed = [], [], []
    class FailingSocket:
        def __init__(self, family, kind):
            created.append((family, kind))
        def setsockopt(self, *args):
            pass
        def bind(self, address):
            binds.append(address)
            raise OSError('bind blocked or port unavailable')
        def close(self):
            closed.append(True)
    monkeypatch.setattr(socket, 'socket', FailingSocket)
    monkeypatch.setattr(module, '_Server', REAL_SERVER)
    listener = GoogleOAuthLoopbackListener(service)
    with pytest.raises(ListenerError, match='could not start'): listener.start(ctx)
    assert created == [(socket.AF_INET, socket.SOCK_STREAM)]
    assert binds == [(HOST, PORT)] and closed == [True]
    assert listener.wait_closed(0) and calls == [] and fake.calls == []
    assert service.status(ctx)['status'] == 'not_configured'


def test_legacy_resolver_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(module, '_Server', MemoryServer)
    tmp_path.chmod(0o700)
    cfg = tmp_path / 'google-client.json'
    cfg.write_text(json.dumps({'client_id': 'fake-client', 'client_secret': 'fake-secret'}))
    cfg.chmod(0o600)
    fake = Google()
    service = GoogleCalendarService(tmp_path, lambda ctx: b'1' * 32, transport=fake)
    listener = GoogleOAuthLoopbackListener(service)
    try:
        with pytest.raises(ListenerError, match='could not start'):
            listener.start('owner')
        assert listener.wait_closed(0)
        with pytest.raises(ServiceError, match='Owner session transaction unavailable'):
            service.status('owner')
        assert not fake.calls
        assert not (tmp_path / 'google-token.json').exists()
    finally:
        service.close()


def test_two_callbacks_only_one_exchanges(system):
    _, fake, _, _, _, start = system
    listener, st = start()
    entered, release = threading.Event(), threading.Event()
    def before(url):
        if url == TOKEN_URL:
            entered.set()
            if not release.wait(3): raise RuntimeError('test timeout')
    fake.before_response = before
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(req, listener, f'{PATH}?state={st}&code=fake-code')
        try:
            assert entered.wait(2)
            assert listener.cancel() is False
            second = pool.submit(req, listener, f'{PATH}?state={st}&code=fake-code')
            assert second.result(2)[0] == 400
        finally:
            release.set()
        assert first.result(3)[0] == 200
    assert listener.wait_closed(3)
    assert [call[1] for call in fake.calls] == [TOKEN_URL]


def test_revoked_owner_never_exchanges(system):
    _, fake, authority, ctx, path, start = system
    listener, st = start()
    authority.revoke(ctx)
    status, body = req(listener, f'{PATH}?state={st}&code=fake-code')
    assert status == 400 and 'Não foi possível' in body.decode()
    assert listener.wait_closed(3)
    assert not fake.calls and not (path / 'google-token.json').exists()


def test_inflight_timeout_closes_listener_not_exchange(system):
    _, fake, _, _, _, start = system
    listener, st = start(timeout=.3)
    entered, release = threading.Event(), threading.Event()
    def before(url):
        if url == TOKEN_URL:
            entered.set()
            if not release.wait(3): raise RuntimeError('test timeout')
    fake.before_response = before
    with ThreadPoolExecutor(max_workers=1) as pool:
        cb = pool.submit(req, listener, f'{PATH}?state={st}&code=fake-code')
        try:
            assert entered.wait(2)
            assert listener.wait_closed(2) and listener._server.closed
            assert listener.cancel() is False
        finally:
            release.set()
        assert cb.result(3)[0] == 200
    assert [call[1] for call in fake.calls] == [TOKEN_URL]


def test_fixed_production_address_and_denial(system):
    _, fake, _, _, _, start = system
    assert (module._HOST, module._PORT, module._PATH) == (HOST, PORT, PATH)
    assert module.REDIRECT_URI == f'http://{HOST}:{PORT}{PATH}'
    listener, st = start()
    assert req(listener, f'{PATH}?state={st}&error=access_denied')[0] == 400
    assert listener.wait_closed(3) and not fake.calls
