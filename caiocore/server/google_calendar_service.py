"""Single-owner Calendar controller. Internal API ONLY; this is not an HTTP server.

The trusted caller must authenticate the owner and use CalendarSessionAuthority
for all session issuance, replacement and revocation. See contract.
No argument to this controller is a substitute for an authenticated session.
"""
from __future__ import annotations

import json
import os
import stat
import threading

try:
    import fcntl
except ImportError:  # Windows: this POSIX service is not supported.
    fcntl = None

from .google_calendar_client import GoogleCalendarClient, CALENDAR_SCOPE, https_transport
from .google_oauth_local import OAuthError, PrivateTokenStore, _session
from .google_session_authority import CalendarSessionAuthority


class ServiceError(Exception):
    """Safe, generic failure; do not log exception tracebacks or arguments."""


def _unique(pairs):
    result = {}
    for k, v in pairs:
        if k in result:
            raise ValueError('duplicate')
        result[k] = v
    return result


def load_private_client_config(directory):
    """Read ONLY google-client.json in a private, trusted POSIX directory.

    Expected JSON: {"client_id":"...", "client_secret":"..."}. Never create
    this file in the repo or accept its contents from an unauthenticated request.
    """
    try:
        with PrivateTokenStore(directory) as store:
            store._check_directory()
            fd = os.open('google-client.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=store._fd)
            try:
                s = os.fstat(fd)
                if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid()
                        or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1 or s.st_size > 16384):
                    raise ValueError('unsafe')
                with os.fdopen(fd, 'rb') as inp:
                    fd = None
                    raw = inp.read(16385)
                if len(raw) > 16384:
                    raise ValueError('oversize')
                cfg = json.loads(raw, object_pairs_hook=_unique)
                if not isinstance(cfg, dict) or set(cfg) != {'client_id', 'client_secret'}:
                    raise ValueError('invalid')
                # Constructor checks format and size; no request is made.
                GoogleCalendarClient(cfg['client_id'], client_secret=cfg['client_secret'])
                if not isinstance(cfg['client_secret'], str) or not cfg['client_secret']:
                    raise ValueError('missing secret')
                return cfg
            finally:
                if fd is not None:
                    os.close(fd)
    except Exception:
        raise ServiceError('Private client configuration unavailable') from None


class _ServiceLock:
    def __init__(self):
        self._lock = threading.Lock()
        self._owner = None
        self._pid = os.getpid()

    def __enter__(self):
        if self._pid != os.getpid():
            raise ServiceError('Forked service')
        ident = threading.get_ident()
        if self._owner == ident:
            raise ServiceError('Reentrant service operation')
        self._lock.acquire()
        self._owner = ident
        return self

    def __exit__(self, *args):
        self._owner = None
        self._lock.release()


class _TransactionalCalendarClient(GoogleCalendarClient):
    """Create the intent after state consumption, before the exchange request."""
    def __init__(self, *args, mark_exchange, **kwargs):
        super().__init__(*args, **kwargs)
        self._mark_exchange = mark_exchange
        self._exchanging = False

    def exchange(self, session, state, code):
        if self._exchanging:
            raise OAuthError('Reentrant authorization')
        self._exchanging = True
        try:
            return super().exchange(session, state, code)
        finally:
            self._exchanging = False

    def _request(self, *args, **kwargs):
        # The inherited exchange has validated the code and consumed the
        # session-bound state before it reaches this point. No network occurs
        # until after the durable marker has been written.
        if self._exchanging:
            self._mark_exchange()
        return super()._request(*args, **kwargs)


class GoogleCalendarService:
    """One owner; exclusive POSIX lock for this token directory’s lifetime.

    Advisory locking requires all directory users to cooperate; it does not
    protect against malicious same-UID code or an untrusted directory.
    """
    def __init__(self, directory, resolve_owner_session, *, transport=https_transport):
        if not callable(resolve_owner_session) and type(resolve_owner_session) is not CalendarSessionAuthority:
            raise ServiceError('Owner authentication required')
        config = load_private_client_config(directory)
        self._store = None
        self._lock_fd = None
        try:
            self._client = _TransactionalCalendarClient(config['client_id'], client_secret=config['client_secret'], transport=transport, mark_exchange=self._mark)
            self._store = PrivateTokenStore(directory)
            if fcntl is None:
                raise OSError('POSIX locking unavailable')
            self._store._check_directory()
            self._lock_fd = os.open('calendar-service.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=self._store._fd)
            self._check_lock_file()
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._pid = os.getpid()
        except Exception:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
            if self._store is not None:
                self._store.close()
            raise ServiceError('Service unavailable') from None
        self._resolve = resolve_owner_session
        self._authority = resolve_owner_session if type(resolve_owner_session) is CalendarSessionAuthority else None
        self._lock = _ServiceLock()
        self._uncertain = False
        self._verified = False

    def _check_lock_file(self):
        self._store._check_directory()
        info = os.fstat(self._lock_fd)
        path_info = os.stat('calendar-service.lock', dir_fd=self._store._fd, follow_symlinks=False)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or (info.st_dev, info.st_ino) != (path_info.st_dev, path_info.st_ino)):
            raise ValueError('Unsafe service lock')

    def _owner(self, context):
        if self._authority is None:
            raise ServiceError('Owner session transaction unavailable')
        try:
            if os.getpid() != self._pid:
                raise ValueError('Forked service')
            self._check_lock_file()
            if self._uncertain:
                raise ValueError('Recovery required')
            try:
                os.stat('calendar-transaction.pending', dir_fd=self._store._fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('Recovery required')
            with self._authority.transaction(context) as session:
                _session(session)
                self._authority.validate(context, session)
                return session
        except Exception:
            raise ServiceError('Owner authentication or recovery required') from None

    def _mark(self):
        # Intent record prevents use after a crash/partial write. Never overwrite.
        self._uncertain = True
        fd = os.open('calendar-transaction.pending', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._store._fd)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self._store._fd)

    def _clear_mark(self):
        os.unlink('calendar-transaction.pending', dir_fd=self._store._fd)
        try:
            os.fsync(self._store._fd)
        except Exception:
            # The unlink may already have taken effect. Best effort to leave a
            # restart-visible quarantine. Arbitrary filesystem failures still
            # prevent a durability guarantee; the in-memory latch stays set.
            try:
                fd = os.open('calendar-transaction.pending', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self._store._fd)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.fsync(self._store._fd)
            except Exception:
                pass
            raise
        self._uncertain = False

    def _publish(self, context, session, value):
        with self._authority.transaction(context, session):
            # This is the publication linearization point, not network delivery.
            self._authority.validate(context, session)
            return value

    def _commit_record(self, context, session, record):
        with self._authority.transaction(context, session):
            self._store.save(record)
            # If expiry or reentrant revocation happened during save, rollback.
            self._authority.seal(context, session)
            # Commit linearizes at validate, under the session authority lock.
            # A crash before marker removal leaves the directory quarantined.
            self._clear_mark()

    def _cleanup(self, record):
        try:
            self._store.delete_local()
            local = 'confirmed'
        except Exception:
            local = 'unconfirmed'
        try:
            self._client.revoke(record)
            remote = 'confirmed'
        except Exception:
            remote = 'unconfirmed'
        if local == remote == 'confirmed':
            try:
                self._clear_mark()
            except Exception:
                pass  # Remain quarantined on uncertain durability.
        return local, remote

    def _load(self):
        record = self._store.load()
        if record is not None and record['scopes'] != [CALENDAR_SCOPE]:
            raise OAuthError('Invalid calendar credential')
        return record

    def start(self, context):
        with self._lock:
            session = self._owner(context)
            try:
                if self._load() is not None:
                    raise ServiceError('Disconnect existing calendar first')
                self._client.cancel_all()
                url = self._client.start(session)
                try:
                    return self._publish(context, session, {'authorization_url': url})
                except Exception:
                    self._client.cancel_all()
                    raise
            except ServiceError:
                raise
            except Exception:
                raise ServiceError('Authorization could not start') from None

    def callback(self, context, params):
        """params: list of decoded (key, value) pairs, retaining duplicates."""
        with self._lock:
            session = self._owner(context)
            try:
                allowed = {'state', 'code', 'error', 'error_description', 'error_uri', 'scope', 'authuser', 'prompt', 'hd'}
                if not isinstance(params, list) or len(params) > len(allowed):
                    raise ValueError()
                data = {}
                for item in params:
                    if not isinstance(item, (list, tuple)) or len(item) != 2:
                        raise ValueError()
                    key, value = item
                    if (not isinstance(key, str) or key not in allowed or key in data
                            or not isinstance(value, str) or not value or len(value) > 8192):
                        raise ValueError()
                    data[key] = value
                if 'state' not in data or ('code' in data) == ('error' in data):
                    raise ValueError()
                if self._load() is not None:
                    raise ValueError()
                if 'error' in data:
                    self._client.cancel(session, data['state'])
                    return self._publish(context, session, {'status': 'authorization_denied' if data['error'] == 'access_denied' else 'authorization_failed'})
                # The client consumes state, then marks before its request.
                # A failed exchange after that point is ambiguous.
                result = self._client.exchange(session, data['state'], data['code'])
                try:
                    self._commit_record(context, session, result.record)
                except Exception:
                    local, outcome = self._cleanup(result.record)
                    raise ServiceError('Authorization failed; local removal ' + local + '; Google revocation ' + outcome) from None
                self._client.cancel_all()
                self._verified = True
                return {'status': 'verified_at_last_check', 'scopes': [CALENDAR_SCOPE]}
            except ServiceError:
                self._verified = False
                raise
            except Exception:
                self._verified = False
                raise ServiceError('Authorization failed') from None

    def status(self, context):
        with self._lock:
            session = self._owner(context)
            try:
                record = self._load()
                # An independent store instance can replace the record while the
                # service is alive. Until verification is tied to an atomic store
                # generation, do not attribute a past check to the current record.
                return self._publish(context, session, {'status': 'stored_unverified' if record else 'not_configured',
                        'scopes': [CALENDAR_SCOPE] if record else []})
            except Exception:
                raise ServiceError('Calendar status unavailable') from None

    def _refresh(self, context, session):
        record = self._load()
        if record is None:
            raise OAuthError('No calendar credential')
        self._mark()
        result = self._client.refresh(record)
        try:
            # Persist even unrotated records through the same commit protocol.
            self._commit_record(context, session, result.record)
        except Exception:
            local, remote = self._cleanup(result.record)
            raise OAuthError('Refresh failed; local removal ' + local + '; Google revocation ' + remote) from None
        self._verified = True
        return result.access_token

    def verify(self, context):
        with self._lock:
            session = self._owner(context)
            try:
                self._refresh(context, session)
                return self._publish(context, session, {'status': 'verified_at_last_check', 'scopes': [CALENDAR_SCOPE]})
            except Exception:
                self._verified = False
                raise ServiceError('Calendar verification failed') from None

    def list_events(self, context, *, page_token=None, max_results=250):
        with self._lock:
            session = self._owner(context)
            try:
                access = self._refresh(context, session)
                events = self._client.list_events(access, page_token=page_token, max_results=max_results)
                return self._publish(context, session, events)
            except Exception:
                self._verified = False
                raise ServiceError('Calendar read failed') from None

    def disconnect(self, context):
        with self._lock:
            session = self._owner(context)
            self._client.cancel_all()
            self._verified = False
            try:
                record = self._load()
                if record is None:
                    return self._publish(context, session, {'status': 'not_configured', 'revocation': 'not_attempted'})
                self._mark()
                revocation = 'confirmed'
                try:
                    self._client.revoke(record)
                except Exception:
                    revocation = 'unconfirmed'
                with self._authority.transaction(context, session):
                    self._store.delete_local()
                    self._authority.seal(context, session)
                    if revocation == 'confirmed':
                        self._clear_mark()
                if revocation == 'unconfirmed':
                    return {'status': 'recovery_required', 'local_removal': 'confirmed', 'revocation': revocation}
                return {'status': 'not_configured', 'revocation': revocation}
            except Exception:
                raise ServiceError('Local disconnect failed; recovery may be required') from None

    def close(self):
        with self._lock:
            self._client.cancel_all()
            self._verified = False
            self._store.close()
            if self._lock_fd is not None:
                # No explicit unlock: a forked child must not unlock its parent.
                os.close(self._lock_fd)
                self._lock_fd = None
