"""Real localhost HTTP stack, using a deterministic offline agent, not a real LLM/UI."""
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOKEN = 'b' * 64  # synthetic and never used outside the test
WEBHOOK = 'fake-webhook-only'

# The real FastAPI routes/middleware run under Uvicorn. Only the agent is fake.
# The thin ASGI probe observes the headers delivered to the real API without
# returning their values; contextvars keep concurrent SSE and POST isolated.
API_RUNNER = r'''
import asyncio, contextvars, json, os, socket
import uvicorn
from caiocore.server import api
from caiocore.agents.events import broadcaster
api._DOCS_OUT = os.environ['TEST_DOCS_OUT']
from starlette.responses import RedirectResponse
@api.app.get('/api/test-redirect')
async def test_redirect(target: str):
    return RedirectResponse(target)

headers_var = contextvars.ContextVar('headers')
class OfflineAgent:
    model = 'offline-fake'
    def __init__(self): self.calls = 0
    async def handle_message(self, message, session_id, channel, agent_id):
        self.calls += 1
        h = headers_var.get()
        if message == 'emit-event':
            for _ in range(100):
                if broadcaster.subscribers: break
                await asyncio.sleep(.01)
            if not broadcaster.subscribers: raise RuntimeError('SSE subscriber absent')
            await broadcaster.broadcast({'type': 'offline-test', 'message': 'evento local'})
        return json.dumps({'message': message, 'session': session_id, 'channel': channel,
            'agent_id': agent_id, 'calls': self.calls,
            'bearer_ok': h.get(b'authorization') == b'Bearer ' + os.environ['CAIO_API_TOKEN'].encode(),
            'cookie_seen': b'cookie' in h, 'forwarded_seen': b'x-forwarded-for' in h,
            'webhook_header_seen': b'x-evolution-webhook-token' in h})
api._agent = OfflineAgent()
async def wrapped(scope, receive, send):
    mark = headers_var.set(dict(scope.get('headers', [])))
    try: await api.app(scope, receive, send)
    finally: headers_var.reset(mark)
sock = socket.fromfd(int(os.environ['TEST_LISTENER_FD']), socket.AF_INET, socket.SOCK_STREAM)
config = uvicorn.Config(wrapped, lifespan='off', proxy_headers=False, log_level='warning', access_log=False)
uvicorn.Server(config).run(sockets=[sock])
'''

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


def listener(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('127.0.0.1', port))
        s.listen(128)
        return s
    except OSError:
        s.close()
        raise


def request(port, method, path, headers=None, body=None):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=4)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def wait_http(proc, port, expected):
    end = time.monotonic() + 12
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise AssertionError(f'local server exited: {proc.returncode}')
        try:
            if request(port, 'GET', '/api/chat/ping')[0] == expected:
                return
        except (OSError, http.client.HTTPException):
            pass
        time.sleep(.05)
    raise AssertionError(f'local server on {port} did not become ready')


@pytest.fixture(scope='module')
def stack(tmp_path_factory):
    tmp = tmp_path_factory.mktemp('runtime-stack')
    dist = tmp / 'fake-dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<html>fixture only; not a React build</html>')
    (dist / 'fixture.js').write_text('/* synthetic asset */')
    docs = tmp / 'out'
    docs.mkdir()
    (docs / 'example.txt').write_bytes(b'0123456789')
    # Never inherit user credentials, proxy settings, or permissive CAIO_* settings.
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp),
           'PYTHONPATH': os.pathsep.join([str(ROOT)] + [p for p in sys.path if p and p.endswith('site-packages')]),
           'PYTHONDONTWRITEBYTECODE': '1',
           'CAIO_API_TOKEN': TOKEN, 'CAIO_EVOLUTION_WEBHOOK_TOKEN': WEBHOOK,
           'CAIO_PROXY_BACKEND': '127.0.0.1', 'CAIO_PROXY_BIND': '127.0.0.1',
           'CAIO_DASHBOARD_DIST': str(dist), 'TEST_PROXY_PATH': str(ROOT / 'dashboard/secure_proxy.py'),
           'TEST_DOCS_OUT': str(docs)}
    # Bind before subprocess launch; fail rather than touch an existing service.
    try:
        api_socket = listener(18790)  # the production proxy hardcodes this upstream port
    except OSError as exc:
        pytest.fail(f'cannot reserve local upstream 127.0.0.1:18790; refusing to use another service: {exc}')
    proxy_socket = None
    processes = []
    log_path = tmp / 'servers.log'
    with log_path.open('wb') as log:
        try:
            proxy_socket = listener(0)
            port = proxy_socket.getsockname()[1]
            for code, sock, extra in ((API_RUNNER, api_socket, {}),
                                      (PROXY_RUNNER, proxy_socket, {'CAIO_PROXY_PORT': str(port)})):
                child_env = dict(env, TEST_LISTENER_FD=str(sock.fileno()), **extra)
                proc = subprocess.Popen([sys.executable, '-c', code], cwd=tmp, env=child_env,
                                        pass_fds=(sock.fileno(),), stdout=log, stderr=log)
                processes.append(proc)
                sock.close()
            wait_http(processes[0], 18790, 401)
            wait_http(processes[1], port, 200)
            yield port, log_path
        finally:
            api_socket.close()
            if proxy_socket is not None:
                proxy_socket.close()
            for proc in processes:
                proc.terminate()
            for proc in processes:
                try: proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)


def assert_no_secret(headers, body):
    assert TOKEN.encode() not in body
    assert TOKEN not in repr(headers)
    assert WEBHOOK.encode() not in body
    assert WEBHOOK not in repr(headers)


def post(port, message, headers=None):
    h = {'Origin': f'http://127.0.0.1:{port}', 'Content-Type': 'application/json'}
    h.update(headers or {})
    return request(port, 'POST', '/api/chat', h,
                   json.dumps({'message': message, 'session_id': 'runtime-session', 'agent_id': 'fake-id'}))


def test_real_api_status_and_auth(stack):
    port, _ = stack
    assert request(18790, 'GET', '/api/chat/ping')[0] == 401
    assert request(18790, 'GET', '/api/chat/ping', {'Authorization': 'Bearer wrong'})[0] == 401
    status, headers, body = request(18790, 'GET', '/api/chat/ping', {'Authorization': 'Bearer ' + TOKEN})
    assert status == 200 and json.loads(body)['model'] == 'offline-fake'
    assert_no_secret(headers, body)
    status, headers, body = request(port, 'GET', '/api/status')
    payload = json.loads(body)
    assert status == 200 and payload['agents_total'] >= 1 and payload['agents_online'] >= 1
    assert_no_secret(headers, body)
    assert 'access-control-allow-origin' not in {key.lower() for key in headers}


def test_real_chat_response_and_header_allowlist(stack):
    port, _ = stack
    status, headers, body = post(port, 'olá local', {'Authorization': 'Bearer wrong',
        'Cookie': 'sensitive-cookie=fake', 'X-Forwarded-For': '192.0.2.1',
        'X-Evolution-Webhook-Token': 'fake-webhook-only'})
    assert status == 200
    result = json.loads(body)
    details = json.loads(result['content'])
    assert result['role'] == 'assistant' and result['status'] == 'completed'
    assert details['message'] == 'olá local' and details['session'] == 'runtime-session'
    assert details['channel'] == 'dashboard' and details['agent_id'] == 'fake-id'
    assert details['bearer_ok'] is True
    assert not details['cookie_seen'] and not details['forwarded_seen'] and not details['webhook_header_seen']
    assert_no_secret(headers, body)


def test_blocks_prevent_agent_dispatch(stack):
    port, _ = stack
    _, _, before = post(port, 'before')
    calls = json.loads(json.loads(before)['content'])['calls']
    for h in ({'Origin': 'http://evil.invalid'}, {'Host': 'evil.invalid'},
              {'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'}):
        assert post(port, 'must-not-run', h)[0] == 403
    assert request(port, 'POST', '/api/chat', {'Content-Type': 'application/json'}, '{}')[0] == 403
    assert request(port, 'POST', '/api/webhooks/evolution',
                   {'Origin': f'http://127.0.0.1:{port}', 'X-Evolution-Webhook-Token': WEBHOOK}, '{}')[0] == 404
    assert request(port, 'GET', '/%2e%2e/secret')[0] == 403
    _, _, after = post(port, 'after')
    assert json.loads(json.loads(after)['content'])['calls'] == calls + 1


def test_live_sse_fails_closed_and_chat_still_works(stack):
    port, _ = stack
    status, headers, body = request(port, 'GET', '/api/tracing/stream', {'Accept': 'text/event-stream'})
    assert status == 503
    assert not {k.lower(): v for k, v in headers.items()}['content-type'].startswith('text/event-stream')
    assert b'Tracing stream disabled' in body
    assert_no_secret(headers, body)
    status, headers, body = post(port, 'normal chat continues')
    assert status == 200 and json.loads(body)['status'] == 'completed'
    assert_no_secret(headers, body)


def test_static_fixture_and_no_secret_in_logs(stack):
    port, log_path = stack
    for path, status in [('/', 200), ('/fixture.js', 200), ('/missing.js', 404)]:
        got, headers, body = request(port, 'GET', path)
        assert got == status
        assert_no_secret(headers, body)
    # Known requests only; this is not a general proof that arbitrary upstream
    # responses or exception text cannot disclose a credential.
    assert_no_secret({}, log_path.read_bytes())


def test_api_trailing_slash_redirect_has_usable_location(stack):
    port, _ = stack
    status, headers, _ = request(port, 'GET', '/api/status/')
    assert status == 307
    location = {k.lower(): v for k, v in headers.items()}['location']
    assert location == f'http://127.0.0.1:{port}/api/status'
    assert request(port, 'GET', '/api/status')[0] == 200


def test_upstream_redirect_cannot_escape_origin(stack):
    port, _ = stack
    for target in ('https://evil.invalid/path', '//evil.invalid/path',
                   '/%2f%2fevil.invalid/path', '/%5cevil.invalid/path'):
        status, headers, body = request(port, 'GET', '/api/test-redirect?target=' + quote(target, safe=''))
        assert status == 502
        assert 'location' not in {k.lower() for k in headers}
        assert_no_secret(headers, body)
    status, headers, _ = request(port, 'GET', '/api/test-redirect?target=' + quote('/api/status', safe=''))
    assert status == 307 and {k.lower(): v for k, v in headers.items()}['location'] == '/api/status'


def test_document_range_download_through_proxy(stack):
    port, _ = stack
    path = '/api/documents/example.txt/download'
    status, headers, body = request(18790, 'GET', path,
                                    {'Authorization': 'Bearer ' + TOKEN, 'Range': 'bytes=2-5'})
    assert status == 206 and body == b'2345'
    status, headers, body = request(port, 'GET', path, {'Range': 'bytes=2-5'})
    lower = {k.lower(): v for k, v in headers.items()}
    assert status == 206 and body == b'2345'
    assert lower['content-range'] == 'bytes 2-5/10'
    assert lower['accept-ranges'] == 'bytes'
    assert_no_secret(headers, body)
    etag = lower['etag']
    status, headers, body = request(port, 'GET', path, {'Range': 'bytes=2-5', 'If-Range': etag})
    assert status == 206 and body == b'2345'
    status, headers, body = request(port, 'GET', path, {'Range': 'bytes=2-5', 'If-Range': '"different"'})
    assert status == 200 and body == b'0123456789'
    status, headers, _ = request(port, 'GET', path, {'Range': 'bytes=99-100'})
    assert status == 416 and {k.lower(): v for k, v in headers.items()}['content-range'] == 'bytes */10'
