"""Opt-in local-only, single-owner HTTP bridge for Calendar.

Loaded only by secure_proxy after explicit configuration; not an ASGI router.
See GOOGLE_CALENDAR_BRIDGE.md for the trust boundary and deployment limits.
"""
import fcntl
import hmac
import json
import os
import re
import secrets
import stat
import threading
import time

from caiocore.server.google_calendar_service import GoogleCalendarService
from caiocore.server.google_oauth_listener import GoogleOAuthLoopbackListener
from caiocore.server.google_oauth_local import PrivateTokenStore

PREFIX = '/api/local/calendar'
_COOKIE = 'caio_calendar_owner'
_HEX = re.compile(r'[0-9a-f]{64}\Z')


class _Session:
    def __init__(self):
        self.cookie = secrets.token_hex(32)
        self.csrf = secrets.token_hex(32)
        self.internal = secrets.token_bytes(32)
        self.expires = time.monotonic() + 1800


class CalendarBridge:
    def __init__(self, directory, *, transport=None):
        # The browser session and response delivery do not yet participate in
        # CalendarSessionAuthority transactions. Do not expose even opt-in routes.
        raise RuntimeError('Calendar bridge disabled: session integration incomplete')
        self._directory = PrivateTokenStore(directory)
        self._lock_fd = None
        self._guard = threading.RLock()
        self._operations = threading.Lock()
        self._session = None
        self._failures = []
        self._listener = None
        self._service = None
        try:
            # Directory is preexisting, owned by this UID, exactly 0700.
            # The flock enforces the service's single-process limitation.
            self._directory._check_directory()
            flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
            self._lock_fd = os.open('calendar-bridge.lock', flags, 0o600, dir_fd=self._directory._fd)
            info = os.fstat(self._lock_fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                raise ValueError('unsafe lock')
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._owner_key = self._read_key()
            kw = {} if transport is None else {'transport': transport}
            self._service = GoogleCalendarService(directory, self._resolve, **kw)
        except Exception:
            self.close()
            raise RuntimeError('Calendar bridge unavailable') from None

    def _read_key(self):
        self._directory._check_directory()
        fd = os.open('calendar-owner-key', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._directory._fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size not in (64, 65):
                raise ValueError('unsafe key')
            data = os.read(fd, 66).decode('ascii')
            if data.endswith('\n'):
                data = data[:-1]
            if not _HEX.fullmatch(data):
                raise ValueError('invalid key')
            return data
        finally:
            os.close(fd)

    def _resolve(self, context):
        with self._guard:
            if context is not self._session or context is None or time.monotonic() >= context.expires:
                raise ValueError('expired')
            return context.internal

    def _login(self, key):
        with self._guard:
            now = time.monotonic()
            self._failures = [t for t in self._failures if now - t < 60]
            if len(self._failures) >= 5:
                return None
            if not isinstance(key, str) or not _HEX.fullmatch(key) or not hmac.compare_digest(key, self._owner_key):
                self._failures.append(now)
                return None
            self._failures.clear()
            self._session = _Session()
            return self._session

    def _authorize(self, handler):
        cookies = handler.headers.get_all('Cookie', [])
        csrf = handler.headers.get_all('X-Caio-Calendar-CSRF', [])
        if len(cookies) != 1 or len(csrf) != 1 or not _HEX.fullmatch(csrf[0]):
            return None
        # Reject duplicate cookie names rather than relying on parser precedence.
        parts = cookies[0].split(';')
        pairs = [p.strip().split('=', 1) for p in parts]
        found = [p[1] for p in pairs if len(p) == 2 and p[0] == _COOKIE]
        if len(found) != 1 or not _HEX.fullmatch(found[0]):
            return None
        with self._guard:
            session = self._session
            if (session is None or time.monotonic() >= session.expires
                    or not hmac.compare_digest(session.cookie, found[0])
                    or not hmac.compare_digest(session.csrf, csrf[0])):
                return None
            return session

    @staticmethod
    def _reply(handler, status, data, cookie=None):
        body = json.dumps(data, separators=(',', ':')).encode('utf-8')
        handler.send_response(status)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('Content-Length', str(len(body)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Referrer-Policy', 'no-referrer')
        handler.send_header('X-Content-Type-Options', 'nosniff')
        handler.send_header('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
        if cookie is not None:
            handler.send_header('Set-Cookie', _COOKIE + '=' + cookie + '; Path=' + PREFIX + '; HttpOnly; SameSite=Strict; Max-Age=' + ('1800' if cookie else '0'))
        handler.end_headers()
        handler.wfile.write(body)
        handler.close_connection = True

    def handle(self, h):
        # HTTP Host, peer and Origin independently checked; never accept proxies.
        # POST plus JSON and a non-simple header makes browser cross-origin CSRF
        # require a preflight, for which no CORS permission is returned.
        if (h.client_address[0] != '127.0.0.1' or h.command != 'POST'
                or h.headers.get_all('Host') != [f'127.0.0.1:{h.server.server_port}']
                or h.headers.get_all('Origin') != [f'http://127.0.0.1:{h.server.server_port}']
                or h.headers.get_all('Content-Type') != ['application/json']
                or h.headers.get_all('X-Caio-Calendar-Request') != ['1']
                or any(h.headers.get_all(n) for n in ('Forwarded', 'X-Forwarded-For', 'X-Forwarded-Host', 'X-Forwarded-Proto', 'X-Forwarded-Port', 'X-Real-IP', 'Transfer-Encoding'))
                or h.headers.get('Sec-Fetch-Site', '').lower() not in ('', 'same-origin')
                or h.headers.get_all('Content-Length') is None or len(h.headers.get_all('Content-Length')) != 1):
            return self._reply(h, 403, {'error': 'access_denied'})
        raw_length = h.headers['Content-Length']
        if not re.fullmatch(r'[0-9]{1,4}', raw_length) or int(raw_length) > 1024:
            return self._reply(h, 400, {'error': 'invalid_request'})
        raw = h.rfile.read(int(raw_length))
        try:
            def unique(pairs):
                d = {}
                for k, v in pairs:
                    if k in d: raise ValueError()
                    d[k] = v
                return d
            if len(raw) != int(raw_length): raise ValueError()
            data = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
            if not isinstance(data, dict): raise ValueError()
        except (ValueError, UnicodeError):
            return self._reply(h, 400, {'error': 'invalid_request'})
        path = h.path
        if path == PREFIX + '/login':
            if set(data) != {'owner_key'}:
                return self._reply(h, 400, {'error': 'invalid_request'})
            session = self._login(data['owner_key'])
            if session is None: return self._reply(h, 401, {'error': 'access_denied'})
            return self._reply(h, 200, {'csrf': session.csrf, 'expires_in': 1800}, session.cookie)
        if path not in {PREFIX + '/status', PREFIX + '/start', PREFIX + '/disconnect', PREFIX + '/logout'}:
            return self._reply(h, 404, {'error': 'not_found'})
        if data:
            return self._reply(h, 400, {'error': 'invalid_request'})
        session = self._authorize(h)
        if session is None: return self._reply(h, 401, {'error': 'access_denied'})
        try:
            if path.endswith('/status'):
                result = self._service.status(session)
            elif path.endswith('/logout'):
                with self._guard:
                    if self._session is session: self._session = None
                return self._reply(h, 200, {'status': 'signed_out'}, '')
            else:
                with self._operations:
                    self._resolve(session)
                    if path.endswith('/start'):
                        if self._listener is not None:
                            if not self._listener.wait_closed(0):
                                return self._reply(h, 409, {'error': 'authorization_in_progress'})
                        listener = GoogleOAuthLoopbackListener(self._service)
                        result = listener.start(session)
                        self._listener = listener
                    else:
                        if self._listener is not None: self._listener.cancel()
                        result = self._service.disconnect(session)
            return self._reply(h, 200, result)
        except Exception:
            return self._reply(h, 503, {'error': 'calendar_unavailable'})

    def close(self):
        with self._guard:
            self._session = None
        if self._listener is not None:
            self._listener.cancel()
            self._listener.wait_closed(2)
        if self._service is not None: self._service.close()
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None
        self._directory.close()
