"""Local-only dashboard and same-origin API proxy; no credential reaches the browser.

Run behind a loopback-only published port. Other processes on the host and
containers on the private Compose network are trusted; this is not remote auth.
"""
import http.client
import mimetypes
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

TOKEN = os.environ.get('CAIO_API_TOKEN', '')
BACKEND = os.environ.get('CAIO_PROXY_BACKEND', '127.0.0.1')
BIND = os.environ.get('CAIO_PROXY_BIND', '127.0.0.1')
PORT = int(os.environ.get('CAIO_PROXY_PORT', '8080'))
ROOT = Path(os.environ.get('CAIO_DASHBOARD_DIST', str(Path(__file__).parent / 'dist'))).resolve()
MAX_BODY = 10 * 1024 * 1024
MAX_CONNECTIONS = 64
CLIENT_TIMEOUT = 30

if not re.fullmatch(r'[0-9a-f]{64}', TOKEN):
    sys.exit('CAIO_API_TOKEN must be 64 lowercase hexadecimal characters (32 random bytes)')
if BACKEND not in ('127.0.0.1', 'caio-agent') or BIND not in ('127.0.0.1', '0.0.0.0'):
    sys.exit('Invalid proxy backend or bind address')
if BIND == '0.0.0.0' and BACKEND != 'caio-agent':
    sys.exit('Non-loopback bind is only supported for the private Compose network')
if not ROOT.is_dir() or not (ROOT / 'index.html').is_file():
    sys.exit('Dashboard build missing: build dashboard/dist first')

# Opt-in only. Unlike the legacy proxy, this bridge requires a direct loopback
# bind and its own owner authentication. Never enable in Compose or a tunnel.
CALENDAR_PREFIX = '/api/local/calendar'
CALENDAR_BRIDGE = None
CALENDAR_CONFIGURED = bool(os.environ.get('CAIO_CALENDAR_PRIVATE_DIR'))
if CALENDAR_CONFIGURED and BIND == '127.0.0.1' and BACKEND == '127.0.0.1':
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from dashboard.calendar_local_bridge import CalendarBridge
        CALENDAR_BRIDGE = CalendarBridge(os.environ['CAIO_CALENDAR_PRIVATE_DIR'])
    except Exception:
        # Keep the legacy proxy working, but never expose a half-initialized bridge.
        CALENDAR_BRIDGE = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'  # EOF frames streams; flush each SSE chunk
    server_version = 'CaioLocal'
    sys_version = ''

    def log_message(self, fmt, *args):
        # Request paths can contain sensitive query parameters. Never log them.
        pass

    def reject(self, status):
        self.send_response(status)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', '0')
        self.end_headers()
        self.close_connection = True

    def validate(self):
        # Reject duplicates rather than letting proxy and API interpret them differently.
        for name in ('Host', 'Origin', 'Content-Length', 'Transfer-Encoding', 'Authorization'):
            if len(self.headers.get_all(name, [])) > 1:
                return False
        host = self.headers.get('Host', '')
        if host not in (f'localhost:{PORT}', f'127.0.0.1:{PORT}'):
            return False
        origin = self.headers.get('Origin')
        if origin is not None and origin != f'http://{host}':
            return False
        if self.headers.get('Sec-Fetch-Site', '').lower() in ('cross-site', 'same-site'):
            return False
        if self.command not in ('GET', 'HEAD', 'OPTIONS') and not origin:
            return False
        if self.headers.get('Transfer-Encoding') is not None:
            return False
        try:
            raw_length = self.headers.get('Content-Length', '0')
            if not re.fullmatch(r'[0-9]+', raw_length) or int(raw_length) > MAX_BODY:
                return False
        except ValueError:
            return False
        if not self.path.startswith('/') or self.path.startswith('//'):
            return False
        parsed = urlsplit(self.path)
        decoded = unquote(parsed.path)
        if '\\' in decoded or '\x00' in decoded or any(p in ('.', '..') for p in decoded.split('/')):
            return False
        return True

    def handle_request(self):
        if not self.validate():
            return self.reject(403)
        path = unquote(urlsplit(self.path).path)
        if path == CALENDAR_PREFIX or path.startswith(CALENDAR_PREFIX + '/'):
            if CALENDAR_BRIDGE is None:
                return self.reject(503 if CALENDAR_CONFIGURED else 404)
            return CALENDAR_BRIDGE.handle(self)
        # The UI also uses this exact legacy read-only API endpoint.
        if path == '/events' and self.command == 'GET':
            return self.proxy()
        if path == '/api' or path.startswith('/api/'):
            if path == '/api/webhooks' or path.startswith('/api/webhooks/'):
                return self.reject(404)
            return self.proxy()
        if self.command not in ('GET', 'HEAD'):
            return self.reject(405)
        return self.static(path)

    def safe_location(self, value):
        decoded = unquote(value)
        if any(ord(c) < 32 or ord(c) == 127 or c == '\\' for c in decoded):
            return False
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        if value.startswith('/'):
            return not value.startswith('//') and not decoded.startswith('//')
        return parsed.scheme == 'http' and parsed.netloc == self.headers['Host']

    def proxy(self):
        body = b''
        length = int(self.headers.get('Content-Length', '0'))
        if length:
            body = self.rfile.read(length)
            if len(body) != length:
                return self.reject(400)
        # Allowlist only: in particular never forward client Authorization,
        # forwarding headers, cookies or the Evolution webhook credential.
        headers = {'Host': self.headers['Host'], 'Authorization': 'Bearer ' + TOKEN}
        for name in ('Origin', 'Content-Type', 'Accept', 'Last-Event-ID', 'Range', 'If-Range'):
            if name in self.headers:
                headers[name] = self.headers[name]
        if length:
            headers['Content-Length'] = str(length)
        connection = http.client.HTTPConnection(BACKEND, 18790, timeout=150)
        started = False
        try:
            connection.request(self.command, self.path, body=body if length else None, headers=headers)
            response = connection.getresponse()
            # Preserve framework redirects only when they remain on this origin.
            # Never relay an arbitrary upstream Location to a browser.
            locations = response.headers.get_all('Location', [])
            if response.status in (301, 302, 303, 307, 308):
                if len(locations) != 1 or not self.safe_location(locations[0]):
                    return self.reject(502)
            self.send_response(response.status)
            started = True
            for name, value in response.getheaders():
                if name.lower() in ('content-type', 'content-length', 'content-disposition', 'etag',
                                    'last-modified', 'accept-ranges', 'content-range'):
                    self.send_header(name, value)
                elif name.lower() == 'location' and response.status in (301, 302, 303, 307, 308):
                    self.send_header(name, value)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if self.command != 'HEAD':
                while True:
                    data = response.read1(8192)
                    if not data:
                        break
                    self.wfile.write(data)
                    self.wfile.flush()
        except (OSError, http.client.HTTPException):
            # Don't emit a second HTTP response if upstream closed mid-stream.
            if not started:
                try:
                    self.reject(502)
                except OSError:
                    pass
            self.close_connection = True
        finally:
            connection.close()

    def static(self, path):
        candidate = (ROOT / path.lstrip('/')).resolve()
        if not candidate.is_relative_to(ROOT):
            return self.reject(403)
        if not candidate.is_file():
            # SPA navigation only; don't return HTML for missing JS/CSS/assets.
            if Path(path).suffix:
                return self.reject(404)
            candidate = ROOT / 'index.html'
        try:
            content = candidate.read_bytes()
        except OSError:
            return self.reject(404)
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(str(candidate))[0] or 'application/octet-stream')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(content)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = handle_request


class LimitedHTTPServer(ThreadingHTTPServer):
    """Bound threads and stalled client operations; SSE occupies a slot.

    This is a local safeguard, not a production-grade rate limiter.
    """
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(CLIENT_TIMEOUT)
        return request, address

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


if __name__ == '__main__':
    LimitedHTTPServer((BIND, PORT), Handler).serve_forever()
