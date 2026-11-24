"""Short-lived IPv4 loopback adapter; not registered with the web application.

Only trusted owner-side code may instantiate/start this object. The injected
service is expected to provide the GoogleCalendarService start/callback contract.
Never log request targets, parameters, context, exceptions, or authorization URLs.
"""
from __future__ import annotations

import hmac
import re
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qsl, urlsplit

from .google_oauth_local import REDIRECT_URI

_HOST, _PORT, _PATH = '127.0.0.1', 8765, '/google/oauth/callback'
_MAX_URI = 8192
_ALLOWED = {'state', 'code', 'error', 'error_description', 'error_uri', 'scope', 'authuser', 'prompt', 'hd'}
_BAD_ESCAPE = re.compile(r'%(?![0-9a-fA-F]{2})')


class ListenerError(Exception):
    """Generic error with no provider data."""


class _Server(socketserver.ThreadingMixIn, HTTPServer):
    address_family = socket.AF_INET
    # POSIX SO_REUSEADDR allows reuse after TIME_WAIT, not an active bind.
    # On Windows, SO_REUSEADDR can steal an active port: use exclusive bind.
    allow_reuse_address = hasattr(socket, 'SO_REUSEADDR') and not hasattr(socket, 'SO_EXCLUSIVEADDRUSE')
    daemon_threads = True
    block_on_close = False
    request_queue_size = 8

    def __init__(self, adapter):
        self.adapter = adapter
        self.slots = threading.BoundedSemaphore(8)
        super().__init__((_HOST, _PORT), _Handler)
        self.timeout = .1

    def server_bind(self):
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        socketserver.TCPServer.server_bind(self)
        # HTTPServer.server_bind performs reverse DNS, which is unnecessary.
        self.server_name, self.server_port = _HOST, _PORT

    def get_request(self):
        conn, peer = super().get_request()
        conn.settimeout(2)
        return conn, peer

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # No traceback, request, or exception logging.


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'LocalOAuth'
    sys_version = ''

    def log_message(self, *args):
        pass

    def log_request(self, *args):
        pass  # Also safe before the request line has been parsed.

    def send_error(self, code, message=None, explain=None):
        self._response(code, 'Solicitação inválida.')

    def _response(self, status, text):
        body = ('<!doctype html><html lang="pt-br"><meta charset="utf-8">'
                '<title>Google Agenda</title><body><p>' + text +
                '</p><p>Pode fechar esta janela.</p></body></html>').encode('utf-8')
        self.close_connection = True
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(body)
        except (OSError, ValueError):
            pass

    def parse_request(self):
        # Check raw target before BaseHTTPRequestHandler normalizes leading '//'.
        parts = self.raw_requestline.rstrip(b'\r\n').split(b' ')
        if (len(parts) != 3 or parts[2] != b'HTTP/1.1' or len(parts[1]) > _MAX_URI):
            self.request_version = 'HTTP/1.1'
            self.command = None
            self.send_error(400)
            return False
        return super().parse_request()

    def do_GET(self):
        try:
            if (self.client_address[0] != _HOST or self.headers.get_all('Host') != [f'{_HOST}:{_PORT}']
                    or self.headers.get_all('Transfer-Encoding') is not None
                    or self.headers.get_all('Content-Length') is not None):
                raise ValueError()
            # Only origin-form, exact path; no fragments, malformed escapes or aliases.
            target = self.raw_requestline.rstrip(b'\r\n').split(b' ')[1].decode('ascii')
            path, sep, query = target.partition('?')
            if path != _PATH or not sep or '#' in query or _BAD_ESCAPE.search(query):
                raise ValueError()
            pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True,
                              encoding='utf-8', errors='strict', max_num_fields=len(_ALLOWED), separator='&')
            keys = [k for k, _ in pairs]
            if (len(keys) != len(set(keys)) or not set(keys) <= _ALLOWED
                    or 'state' not in keys or (('code' in keys) == ('error' in keys))
                    or any(not v or len(v) > _MAX_URI or any(ord(c) < 32 or ord(c) == 127 for c in v)
                           for _, v in pairs)):
                raise ValueError()
            state = dict(pairs)['state']
            if not self.server.adapter._claim(state):
                self._response(400, 'Autorização inválida ou expirada. Tente novamente.')
                return
        except (ValueError, UnicodeError):
            self._response(400, 'Solicitação inválida.')
            return
        try:
            result = self.server.adapter._service.callback(self.server.adapter._context, pairs)
            if isinstance(result, dict) and result.get('status') == 'verified_at_last_check':
                self._response(200, 'Google Agenda conectado. A conta não foi identificada por e-mail.')
            elif isinstance(result, dict) and result.get('status') == 'authorization_denied':
                self._response(400, 'Autorização negada. Tente novamente se desejar.')
            else:
                self._response(400, 'Não foi possível conectar. Tente novamente.')
        except Exception:
            self._response(400, 'Não foi possível conectar. Verifique o estado local e tente novamente.')
        finally:
            self.server.adapter._finish()

    def __getattr__(self, name):
        # All other HTTP methods have the same non-reflective response.
        if name.startswith('do_'):
            return lambda: self._response(405, 'Método não permitido.')
        raise AttributeError(name)


class GoogleOAuthLoopbackListener:
    """Single-use listener. start(context) binds then invokes service.start(context).

    context must remain a trusted, revalidated owner-session handle; never derive
    it from the callback. Until start succeeds no request is dispatched. The
    injected service does its own session/state/PKCE checks. Caller must not run
    independent starts on the same service concurrently.
    """
    def __init__(self, service, *, timeout=300):
        if (not callable(getattr(service, 'start', None)) or
                not callable(getattr(service, 'callback', None)) or
                isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or
                not 0 < timeout <= 300):
            raise ListenerError('Invalid listener configuration')
        self._service = service
        self._timeout = timeout
        self._lock = threading.Lock()
        self._phase = 'new'
        self._server = None
        self._state = None
        self._context = None
        self._deadline = 0
        self._stop = threading.Event()
        self._thread = None

    def start(self, context):
        with self._lock:
            if self._phase != 'new':
                raise ListenerError('Listener already used')
            self._phase = 'starting'
        server = None
        started = False
        state = None
        try:
            server = _Server(self)  # Fail before authorizing if port is busy.
            result = self._service.start(context)
            started = True
            url = result['authorization_url']
            parsed = urlsplit(url)
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
            data = dict(pairs)
            candidate = data.get('state')
            if (len(data) == len(pairs) and isinstance(candidate, str)
                    and re.fullmatch(r'[A-Za-z0-9_-]{43}', candidate)):
                state = candidate
            if state is None or data.get('redirect_uri') != REDIRECT_URI:
                raise ValueError()
            with self._lock:
                if self._phase != 'starting':
                    raise ValueError()
                self._server, self._context, self._state = server, context, state
                self._deadline = time.monotonic() + self._timeout
                self._phase = 'waiting'
                thread = threading.Thread(target=self._run, name='google-oauth-loopback', daemon=True)
                self._thread = thread
                thread.start()
            return {'authorization_url': url}
        except Exception:
            if server is not None:
                server.server_close()
            with self._lock:
                self._phase = 'closed'
                self._stop.set()
            if started and state:
                self._cancel_state(context, state)
            raise ListenerError('Authorization listener could not start') from None

    def _claim(self, state):
        with self._lock:
            if (self._phase != 'waiting' or time.monotonic() >= self._deadline
                    or not state.isascii() or not hmac.compare_digest(self._state, state)):
                return False
            self._phase = 'in_flight'
            return True

    def _finish(self):
        with self._lock:
            if self._phase == 'in_flight':
                self._phase = 'closed'
            self._state = None
            self._stop.set()

    def _cancel_state(self, context, state):
        try:
            # Existing service verifies owner and consumes state without network.
            self._service.callback(context, [('state', state), ('error', 'access_denied')])
        except Exception:
            pass

    def cancel(self):
        """Cancel while waiting. False means already closed or callback in flight.

        Once callback has begun, its token exchange cannot be interrupted; caller
        must wait for completion, then use service.disconnect if appropriate.
        """
        with self._lock:
            if self._phase != 'waiting':
                return False
            self._phase = 'closed'
            state, context = self._state, self._context
            self._state = None
            self._stop.set()
        self._cancel_state(context, state)
        return True

    def _run(self):
        try:
            while not self._stop.is_set():
                remaining = self._deadline - time.monotonic()
                if remaining <= 0:
                    # In-flight exchange is not cancellable, but stop accepting.
                    if not self.cancel():
                        self._stop.set()
                    break
                self._server.timeout = min(.1, remaining)
                self._server.handle_request()
        except Exception:
            self.cancel()
        finally:
            self._server.server_close()

    def wait_closed(self, timeout=None):
        """Wait for the listening socket to close, not for an in-flight exchange."""
        thread = self._thread
        if thread is None:
            return self._stop.is_set()
        thread.join(timeout)
        return not thread.is_alive()
