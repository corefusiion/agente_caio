"""Fail-closed bridge/proxy contracts; HTTP parsing runs in memory, no sockets."""
import http.client
import importlib.util
import io
import json
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from dashboard.calendar_local_bridge import CalendarBridge

PREFIX = '/api/local/calendar'
KEY = 'ab' * 32
PORT = 8080


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('network forbidden')
    for name in ('bind', 'connect', 'connect_ex', 'listen'):
        monkeypatch.setattr(socket.socket, name, forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    monkeypatch.setattr(socket, 'getaddrinfo', forbidden)


class MemorySocket:
    def __init__(self, request):
        self.request = io.BytesIO(request)
        self.response = bytearray()
    def makefile(self, mode, *args, **kwargs):
        assert mode == 'rb'
        return self.request
    def sendall(self, data):
        self.response.extend(data)


class ResponseSocket:
    def __init__(self, data): self.data = data
    def makefile(self, *args, **kwargs): return io.BytesIO(self.data)


def request(proxy, path, method='POST', extra=()):
    headers = [('Host', f'127.0.0.1:{PORT}'), ('Origin', f'http://127.0.0.1:{PORT}'),
               ('Content-Type', 'application/json'), ('X-Caio-Calendar-Request', '1')]
    headers.extend(extra)
    raw = (f'{method} {path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers)
           + 'Content-Length: 2\r\n\r\n{}').encode()
    sock = MemorySocket(raw)
    proxy.Handler(sock, ('127.0.0.1', 1234), SimpleNamespace(server_port=PORT))
    response = http.client.HTTPResponse(ResponseSocket(bytes(sock.response)))
    response.begin()
    return response.status, response.getheaders(), response.read()


def load_proxy(tmp_path, monkeypatch, configured=False, remote=False):
    dist = tmp_path / 'dist'
    dist.mkdir(exist_ok=True)
    (dist / 'index.html').write_text('ok')
    monkeypatch.setenv('CAIO_API_TOKEN', 'cd' * 32)
    monkeypatch.setenv('CAIO_DASHBOARD_DIST', str(dist))
    monkeypatch.setenv('CAIO_PROXY_PORT', str(PORT))
    monkeypatch.setenv('CAIO_PROXY_BIND', '0.0.0.0' if remote else '127.0.0.1')
    monkeypatch.setenv('CAIO_PROXY_BACKEND', 'caio-agent' if remote else '127.0.0.1')
    if configured:
        monkeypatch.setenv('CAIO_CALENDAR_PRIVATE_DIR', str(tmp_path / 'private'))
    else:
        monkeypatch.delenv('CAIO_CALENDAR_PRIVATE_DIR', raising=False)
    spec = importlib.util.spec_from_file_location('test_secure_proxy_calendar',
        Path(__file__).resolve().parents[1] / 'dashboard' / 'secure_proxy.py')
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    return proxy


@pytest.mark.parametrize('directory_kind', ['valid', 'missing'])
def test_bridge_construction_fails_before_side_effects(tmp_path, directory_kind):
    directory = tmp_path / 'private'
    if directory_kind == 'valid':
        directory.mkdir(mode=0o700)
        (directory / 'calendar-owner-key').write_text(KEY)
        (directory / 'calendar-owner-key').chmod(0o600)
        (directory / 'google-client.json').write_text(json.dumps({'client_id': 'fake', 'client_secret': 'fake'}))
        (directory / 'google-client.json').chmod(0o600)
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
    calls = []
    with pytest.raises(RuntimeError, match='Calendar bridge disabled: session integration incomplete'):
        CalendarBridge(directory, transport=lambda *args: calls.append(args))
    assert calls == []
    if directory_kind == 'valid':
        assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    else:
        assert not directory.exists()


@pytest.mark.parametrize('configured,remote,expected', [(False, False, 404), (True, False, 503), (True, True, 503)])
def test_proxy_calendar_routes_fail_closed_without_forwarding(tmp_path, monkeypatch, configured, remote, expected):
    proxy = load_proxy(tmp_path, monkeypatch, configured, remote)
    assert proxy.CALENDAR_BRIDGE is None
    assert proxy.CALENDAR_CONFIGURED is configured
    def forbidden(*args, **kwargs): raise AssertionError('calendar route must not reach backend')
    monkeypatch.setattr(proxy.Handler, 'proxy', forbidden)
    for suffix in ('', '/login', '/status', '/start', '/disconnect', '/logout', '/login?owner_key=' + KEY):
        status, headers, body = request(proxy, PREFIX + suffix)
        assert status == expected
        assert dict(headers)['Cache-Control'] == 'no-store'
        assert body == b'' and KEY.encode() not in body
    status, _, body = request(proxy, '/', method='GET')
    assert status == 200 and body == b'ok'


def test_proxy_validation_precedes_disabled_route(tmp_path, monkeypatch):
    proxy = load_proxy(tmp_path, monkeypatch, configured=True)
    assert request(proxy, PREFIX + '/login', extra=(('Host', 'evil.test'),))[0] == 403
    assert request(proxy, PREFIX + '/login', extra=(('Origin', 'http://evil.test'),))[0] == 403
    assert request(proxy, PREFIX + '/login', extra=(('Sec-Fetch-Site', 'cross-site'),))[0] == 403
