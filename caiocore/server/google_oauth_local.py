"""Offline Google OAuth primitives. NOT an HTTP endpoint or a working integration.

Never pass an untrusted client-supplied session ID. No network or logging here.
See GOOGLE_OAUTH_LOCAL.md before connecting any of these pieces to a server.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
import secrets
import stat
import threading
import time
from urllib.parse import urlencode

try:
    import fcntl
except ImportError:
    fcntl = None

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
REDIRECT_URI = 'http://127.0.0.1:8765/google/oauth/callback'
SCOPES = {
    'calendar_read': 'https://www.googleapis.com/auth/calendar.readonly',
    'gmail_read': 'https://www.googleapis.com/auth/gmail.readonly',
}


class OAuthError(Exception):
    """Generic error only; never include codes, tokens or provider responses."""


def _session(value):
    # The caller must create this from a CSPRNG and keep it on the server side.
    if not isinstance(value, bytes) or len(value) != 32:
        raise OAuthError('Invalid server session')
    return hashlib.sha256(value).digest()


class PendingAuthorizations:
    """Per-process, single-use, session-bound state. Restart cancels flows."""

    def __init__(self, client_id: str, clock=time.monotonic):
        if not isinstance(client_id, str) or not client_id or len(client_id) > 512 or any(c.isspace() for c in client_id):
            raise OAuthError('Invalid client ID')
        self._client_id = client_id
        self._clock = clock
        self._pending = {}
        self._lock = threading.Lock()

    def start(self, session: bytes, features: list[str]) -> str:
        sid = _session(session)
        if not isinstance(features, list) or not features or any(not isinstance(f, str) or f not in SCOPES for f in features) or len(features) != len(set(features)):
            raise OAuthError('Explicit supported scopes required')
        scopes = tuple(sorted(SCOPES[f] for f in features))
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        with self._lock:
            now = self._clock()
            self._pending = {k: v for k, v in self._pending.items() if v[0] > now}
            if len(self._pending) >= 128:
                raise OAuthError('Too many pending authorizations')
            self._pending[state] = (now + 300, sid, verifier, scopes)
        return AUTH_URL + '?' + urlencode({
            'client_id': self._client_id, 'redirect_uri': REDIRECT_URI,
            'response_type': 'code', 'scope': ' '.join(scopes), 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256',
            'access_type': 'offline',
        })

    def consume(self, session: bytes, state: str) -> tuple[str, tuple[str, ...]]:
        sid = _session(session)
        if not isinstance(state, str) or len(state) != 43 or not state.isascii():
            raise OAuthError('Invalid authorization')
        with self._lock:
            item = self._pending.get(state)
            if item is None or not hmac.compare_digest(item[1], sid):
                raise OAuthError('Invalid authorization')
            del self._pending[state]  # Consume atomically, including expired states.
            if item[0] <= self._clock():
                raise OAuthError('Expired authorization')
            return item[2], item[3]

    def cancel_all(self):
        with self._lock:
            self._pending.clear()


def accept_token_response(response: dict, expected_scopes: tuple[str, ...]) -> dict:
    """Validate a mock/trusted token-endpoint response; return minimal local record.

    No network client is provided. Returned record contains a secret; do not log it.
    """
    if not isinstance(response, dict):
        raise OAuthError('Invalid token response')
    refresh = response.get('refresh_token')
    scope = response.get('scope')
    access = response.get('access_token')
    token_type = response.get('token_type')
    if (not isinstance(refresh, str) or not refresh or len(refresh) > 8192
            or not isinstance(access, str) or not access or len(access) > 8192
            or not isinstance(token_type, str) or token_type.lower() != 'bearer'
            or not isinstance(scope, str)):
        raise OAuthError('Invalid token response')
    granted = scope.split()
    if (not expected_scopes or len(granted) != len(set(granted))
            or set(granted) != set(expected_scopes)
            or not set(expected_scopes).issubset(SCOPES.values())):
        raise OAuthError('Unexpected granted scopes')
    return {'refresh_token': refresh, 'scopes': sorted(granted)}


class PrivateTokenStore:
    """POSIX-only local file store; directory must already exist and be private.

    Protects against other OS users, not malicious processes running as the same
    user, root, backups, or a compromised host. No Windows ACL support.
    All writers must cooperate with its advisory directory lock; older writers
    must be stopped. A leftover .google-token-<32 lowercase hex digits> entry
    blocks operations: its name and metadata do not establish ownership. It
    must be inspected and handled separately by the directory owner.
    Instances must not be used or closed in a forked child. The inherited
    descriptor remains open until the child exits or execs; if a lock was held
    at fork it can affect other stores. Prefer fork followed promptly by exec.
    """
    _name = 'google-token.json'

    def __init__(self, directory):
        if os.name != 'posix' or fcntl is None or not all(hasattr(os, n) for n in ('O_NOFOLLOW', 'O_DIRECTORY', 'getuid')):
            raise OAuthError('Secure storage unavailable on this platform')
        self._fd = None
        self._mutex = threading.Lock()
        self._pid = os.getpid()
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            self._fd = os.open(directory, flags)
            s = os.fstat(self._fd)
            if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
                os.close(self._fd)
                self._fd = None
                raise OAuthError('Private directory required')
        except OSError:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            raise OAuthError('Private directory unavailable') from None
        try:
            with self._operation():
                pass
        except BaseException:
            self.close()
            raise

    def close(self):
        # A lock held by a vanished parent thread stays locked after fork.
        # Never unlock the inherited flock or touch the inherited descriptor:
        # fail before acquiring the mutex, just as _operation does.
        if self._pid != os.getpid():
            raise OAuthError('Private directory unavailable')
        with self._mutex:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _check_directory(self):
        if self._fd is None or self._pid != os.getpid():
            raise OAuthError('Private directory unavailable')
        try:
            s = os.fstat(self._fd)
        except OSError:
            raise OAuthError('Private directory unavailable') from None
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700:
            raise OAuthError('Private directory required')

    @contextmanager
    def _operation(self):
        # Lock the directory itself: there is no separate lock file to replace.
        # A mutex is also needed for threads sharing this open file description.
        if self._pid != os.getpid():
            raise OAuthError('Private directory unavailable')
        with self._mutex:
            self._check_directory()
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX)
            except OSError:
                raise OAuthError('Private directory lock unavailable') from None
            try:
                self._check_directory()
                self._recover_temps()
                yield
            finally:
                try:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
                except OSError:
                    raise OAuthError('Private directory lock unavailable') from None

    def _recover_temps(self):
        # A matching name, mode, or owner is not proof that we created it.
        # This includes genuine leftovers from a crash, which require manual
        # inspection; deleting an unrelated file would be irreversible.
        prefix = '.google-token-'
        try:
            names = os.listdir(self._fd)
            if any(n.startswith(prefix) and len(n) == len(prefix) + 32
                   and all(c in '0123456789abcdef' for c in n[len(prefix):])
                   for n in names):
                raise OAuthError('Ambiguous temporary token file; inspection required')
            # Retry a directory sync even when a previous sync failed after a
            # successful rename or cleanup and no temporary entry remains.
            os.fsync(self._fd)
        except OSError:
            raise OAuthError('Temporary token cleanup failed') from None

    def _open_checked(self):
        self._check_directory()
        try:
            fd = os.open(self._name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fd)
        except FileNotFoundError:
            return None
        except OSError:
            raise OAuthError('Unsafe token file') from None
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1 or s.st_size > 16384:
            os.close(fd)
            raise OAuthError('Unsafe token file')
        return fd

    def save(self, record: dict):
        if not isinstance(record, dict) or set(record) != {'refresh_token', 'scopes'}:
            raise OAuthError('Invalid record')
        refresh, scopes = record['refresh_token'], record['scopes']
        if (not isinstance(refresh, str) or not refresh or len(refresh) > 8192
            or not isinstance(scopes, list) or not scopes or any(not isinstance(x, str) for x in scopes)
            or len(set(scopes)) != len(scopes) or not set(scopes).issubset(SCOPES.values())):
            raise OAuthError('Invalid record')
        data = json.dumps(record, separators=(',', ':')).encode('utf-8')
        if len(data) > 16384:
            raise OAuthError('Invalid record')
        with self._operation():
            self._save(data)

    def _save(self, data):
        # Refuse suspicious existing files rather than replacing them silently.
        existing = self._open_checked()
        if existing is not None:
            os.close(existing)
        name = '.google-token-' + secrets.token_hex(16)
        fd = None
        try:
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._fd)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'wb') as out:
                fd = None
                out.write(data)
                out.flush()
                os.fsync(out.fileno())
            os.replace(name, self._name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
            os.fsync(self._fd)
        except OSError:
            raise OAuthError('Token storage failed') from None
        finally:
            if fd is not None:
                os.close(fd)
            # Do not unlink by name here. After a failure or a successful rename,
            # that name could refer to a different file. A leftover is private
            # but requires inspection before this store can be used again.

    def load(self):
        with self._operation():
            return self._load()

    def _load(self):
        fd = self._open_checked()
        if fd is None:
            return None
        try:
            with os.fdopen(fd, 'rb') as f:
                raw = f.read(16385)
            if len(raw) > 16384:
                raise ValueError()
            record = json.loads(raw)
            if (not isinstance(record, dict) or set(record) != {'refresh_token', 'scopes'}
                or not isinstance(record['refresh_token'], str) or not record['refresh_token']
                or len(record['refresh_token']) > 8192 or not isinstance(record['scopes'], list)
                or not record['scopes'] or any(not isinstance(x, str) for x in record['scopes'])
                or len(set(record['scopes'])) != len(record['scopes'])
                or not set(record['scopes']).issubset(SCOPES.values())):
                raise ValueError()
            return record
        except (OSError, ValueError, UnicodeDecodeError):
            raise OAuthError('Invalid token file') from None

    def status(self):
        record = self.load()
        return {'status': 'stored_unverified' if record else 'not_configured',
                'scopes': record['scopes'] if record else []}

    def delete_local(self):
        with self._operation():
            self._delete_local()

    def _delete_local(self):
        fd = self._open_checked()
        if fd is not None:
            os.close(fd)
            try:
                os.unlink(self._name, dir_fd=self._fd)
                os.fsync(self._fd)
            except OSError:
                raise OAuthError('Local deletion failed') from None
        # Does not imply that Google's authorization was revoked.
