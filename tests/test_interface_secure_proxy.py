"""Integration tests with the real ASGI API and a live proxy, no external services."""
import http.client
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'a' * 64
WEBHOOK = 'independent-test-secret'


def listener(port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
        sock.listen(128)
        return sock
    except OSError:
        sock.close()
        raise


PROXY_RUNNER = r'''
import importlib.util, os, socket
spec = importlib.util.spec_from_file_location('proxy_under_test', os.environ['TEST_PROXY_PATH'])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sock = socket.fromfd(int(os.environ['TEST_LISTENER_FD']), socket.AF_INET, socket.SOCK_STREAM)
server = module.LimitedHTTPServer(sock.getsockname(), module.Handler, bind_and_activate=False)
server.socket.close()
server.socket = sock
server.server_address = sock.getsockname()
server.server_name, server.server_port = server.server_address
server.serve_forever()
'''


def wait_for(port, process):
    for _ in range(120):
        if process.poll() is not None:
            raise AssertionError(f'process exited: {process.returncode}')
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=.2):
                return
        except OSError:
            time.sleep(.05)
    raise AssertionError(f'port {port} failed to start')


@pytest.fixture(scope='module')
def stack(tmp_path_factory):
    dist = tmp_path_factory.mktemp('dist')
    (dist / 'index.html').write_text('<html>test dashboard</html>')
    (dist / 'app.js').write_text('const demo = 1;')
    # Do not inherit real credentials, configuration, or network proxy settings.
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(dist),
           'PYTHONPATH': os.pathsep.join([str(ROOT)] + [p for p in sys.path if p and p.endswith('site-packages')]),
           'PYTHONDONTWRITEBYTECODE': '1', 'CAIO_API_TOKEN': TOKEN,
           'CAIO_EVOLUTION_WEBHOOK_TOKEN': WEBHOOK,
           'CAIO_DASHBOARD_DIST': str(dist), 'CAIO_PROXY_BIND': '127.0.0.1',
           'CAIO_PROXY_BACKEND': '127.0.0.1', 'TEST_PROXY_PATH': str(ROOT / 'dashboard/secure_proxy.py')}
    try:
        api_socket = listener(18790)
    except OSError as exc:
        pytest.fail(f'cannot reserve local upstream 127.0.0.1:18790; refusing to use another service: {exc}')
    proxy_socket = None
    processes = []
    try:
        proxy_socket = listener(0)
        port = proxy_socket.getsockname()[1]
        # Run in the temporary directory, not against a real project config.json.
        api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'caiocore.server.api:app',
                                '--fd', str(api_socket.fileno()), '--no-proxy-headers', '--lifespan', 'off'],
                               cwd=dist, env=env, pass_fds=(api_socket.fileno(),),
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(api)
        api_socket.close()
        env.update(CAIO_PROXY_PORT=str(port), TEST_LISTENER_FD=str(proxy_socket.fileno()))
        proxy = subprocess.Popen([sys.executable, '-c', PROXY_RUNNER], cwd=dist, env=env,
                                 pass_fds=(proxy_socket.fileno(),),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(proxy)
        proxy_socket.close()
        wait_for(18790, api)
        wait_for(port, proxy)
        yield port
    finally:
        api_socket.close()
        if proxy_socket is not None:
            proxy_socket.close()
        for proc in processes:
            proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def req(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    conn.close()
    return result


def test_same_origin_and_boundary(stack):
    port = stack
    origin = f'http://127.0.0.1:{port}'
    status, headers, body = req(port, 'GET', '/')
    assert status == 200 and b'test dashboard' in body and TOKEN.encode() not in body
    assert req(port, 'GET', '/app.js')[0] == 200
    assert req(port, 'GET', '/missing.js')[0] == 404
    assert req(port, 'GET', '/%2e%2e/secret')[0] == 403
    status, headers, body = req(port, 'GET', '/api/chat/ping')
    assert status == 200 and TOKEN.encode() not in body and TOKEN not in str(headers)
    assert 'Access-Control-Allow-Origin' not in headers
    # API really saw the request; no agent initialized so this is 503, not 403/401.
    assert req(port, 'POST', '/api/chat', {'Origin': origin, 'Content-Type': 'application/json'}, '{"message":"hi"}')[0] == 503
    assert req(port, 'POST', '/api/chat', {'Content-Type': 'application/json'}, '{}')[0] == 403
    assert req(port, 'POST', '/api/chat', {'Origin': 'http://evil.example'}, '{}')[0] == 403
    assert req(port, 'GET', '/api/chat/ping', {'Host': 'evil.example'})[0] == 403
    assert req(port, 'GET', '/api/chat/ping', {'Origin': 'http://evil.example'})[0] == 403
    assert req(port, 'GET', '/api/chat/ping', {'Sec-Fetch-Site': 'cross-site'})[0] == 403
    assert req(port, 'OPTIONS', '/api/chat', {'Origin': 'http://evil.example', 'Access-Control-Request-Method': 'POST'})[0] == 403
    assert req(port, 'POST', '/api/webhooks/evolution', {'Origin': origin, 'X-Evolution-Webhook-Token': WEBHOOK}, '{}')[0] == 404
    assert req(port, 'GET', '/api/chat/ping', {'Authorization': 'Bearer wrong'})[0] == 200
    # API remains independently protected, including webhook, without proxy.
    assert req(18790, 'GET', '/api/chat/ping')[0] == 401
    assert req(18790, 'GET', '/api/chat/ping', {'Authorization': 'Bearer ' + TOKEN})[0] == 200
    assert req(18790, 'POST', '/api/webhooks/evolution', {'Content-Type': 'application/json'}, '{}')[0] == 403
    assert req(18790, 'POST', '/api/webhooks/evolution', {'Content-Type': 'application/json', 'X-Evolution-Webhook-Token': 'wrong'}, '{}')[0] == 403
    # Secret passes auth; no channel running, so the API returns its own response.
    assert req(18790, 'POST', '/api/webhooks/evolution', {'Content-Type': 'application/json', 'X-Evolution-Webhook-Token': WEBHOOK}, '{}')[0] != 403


def test_legacy_events_route_is_real_api(stack):
    status, headers, body = req(stack, 'GET', '/events?limit=1')
    assert status == 200 and {k.lower(): v for k, v in headers.items()}['content-type'].startswith('application/json')
    assert body == b'[]' and TOKEN.encode() not in body
    assert req(stack, 'GET', '/events?limit=1', {'Origin': 'http://evil.example'})[0] == 403
    assert req(stack, 'POST', '/events', {'Origin': f'http://127.0.0.1:{stack}'}, '')[0] == 405


def raw_status(port, data):
    with socket.create_connection(('127.0.0.1', port), timeout=3) as sock:
        sock.sendall(data)
        return sock.recv(4096).split(b'\r\n', 1)[0]


def test_malformed_framing_and_paths(stack):
    host = f'127.0.0.1:{stack}'.encode()
    prefix = b'GET /api/chat/ping HTTP/1.1\r\nHost: ' + host + b'\r\n'
    for extra in (
        b'Host: evil.example\r\n',
        b'Origin: http://evil.example\r\nOrigin: http://evil.example\r\n',
        b'Content-Length: 0\r\nContent-Length: 0\r\n',
        b'Transfer-Encoding: chunked\r\nContent-Length: 0\r\n',
        b'Content-Length: 10485761\r\n',
        b'Authorization: a\r\nAuthorization: b\r\n',
    ):
        assert b' 403 ' in raw_status(stack, prefix + extra + b'\r\n')
    for path in ('/api/%2e%2e/secret', '/api/%5csecret', '/api/%00secret'):
        assert req(stack, 'GET', path)[0] == 403
    assert req(stack, 'GET', '/api/%77ebhooks/evolution')[0] == 404
    assert req(stack, 'GET', '/api/chat/ping', {'Host': f'localhost.evil.example:{stack}'})[0] == 403
    assert req(stack, 'GET', '/api/chat/ping', {'Origin': f'http://localhost:{stack}'})[0] == 403


def test_connection_cap_and_release(stack):
    sockets = []
    try:
        for _ in range(64):
            s = socket.create_connection(('127.0.0.1', stack), timeout=3)
            s.sendall(b'GET / HTTP/1.1\r\n')  # leave each request incomplete
            sockets.append(s)
        time.sleep(.5)
        with socket.create_connection(('127.0.0.1', stack), timeout=3) as extra:
            extra.settimeout(3)
            try:
                extra.sendall(b'GET / HTTP/1.1\r\n')
                assert extra.recv(1) == b''
            except ConnectionResetError:
                pass
    finally:
        for s in sockets:
            s.close()
    for _ in range(50):
        try:
            if req(stack, 'GET', '/')[0] == 200:
                break
        except (OSError, http.client.HTTPException):
            time.sleep(.05)
    else:
        pytest.fail('connection slots were not released')


def test_sse_disabled_through_real_proxy(stack):
    status, headers, body = req(stack, 'GET', '/api/tracing/stream', {'Accept': 'text/event-stream'})
    assert status == 503
    assert not {k.lower(): v for k, v in headers.items()}['content-type'].startswith('text/event-stream')
    assert b'Tracing stream disabled' in body
    assert TOKEN.encode() not in body
    assert req(18790, 'GET', '/api/tracing/stream')[0] == 401
    assert req(18790, 'GET', '/api/tracing/stream', {'Authorization': 'Bearer ' + TOKEN})[0] == 503


def test_startup_fails_closed(tmp_path):
    (tmp_path / 'index.html').write_text('test')
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp_path),
           'CAIO_API_TOKEN': 'weak', 'CAIO_DASHBOARD_DIST': str(tmp_path), 'CAIO_PROXY_BIND': '127.0.0.1'}
    proc = subprocess.run([sys.executable, str(ROOT / 'dashboard/secure_proxy.py')], env=env,
                          capture_output=True, timeout=3)
    assert proc.returncode != 0 and b'64 lowercase hexadecimal' in proc.stderr
