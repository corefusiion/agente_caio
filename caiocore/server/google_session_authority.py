"""Process-local session authority for the internal Calendar controller.

Only trusted authentication code may issue handles. All replacement/revocation
must go through this object. It is not an HTTP authentication implementation.
"""
import os
import secrets
import threading
import time
from contextlib import contextmanager


class SessionError(Exception):
    pass


class CalendarSessionAuthority:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._pid = os.getpid()
        self._current = None
        self._deadline = 0
        self._in_transaction = False
        self._poisoned = False
        self._sealed = False

    def _check_process(self):
        if self._pid != os.getpid():
            raise SessionError('Forked session authority')

    def _mutation(self):
        self._check_process()
        if self._in_transaction:
            # RLock permits this thread to re-enter. Fail the outer commit too,
            # even if an injected callback catches this exception.
            if not self._sealed:
                self._poisoned = True
            raise SessionError('Reentrant session mutation')

    def issue(self, lifetime=1800):
        self._check_process()
        if isinstance(lifetime, bool) or not isinstance(lifetime, (int, float)) or not 0 < lifetime <= 86400:
            raise SessionError('Invalid lifetime')
        with self._lock:
            self._mutation()
            handle = object()
            self._current = (handle, secrets.token_bytes(32))
            self._deadline = self._clock() + lifetime
            return handle

    def revoke(self, handle):
        self._check_process()
        with self._lock:
            self._mutation()
            if self._current is not None and self._current[0] is handle:
                self._current = None
                self._deadline = 0

    def _validate(self, handle):
        self._check_process()
        if (self._poisoned or self._current is None or self._current[0] is not handle
                or self._clock() >= self._deadline):
            raise SessionError('Invalid session')
        return self._current[1]

    @contextmanager
    def transaction(self, handle, expected=None):
        """Serialize commit/publication with every cooperating session mutation.

        Caller must call validate() after work and before its linearization point.
        Never hold over network. Reentrant mutation poisons the transaction.
        """
        self._check_process()
        with self._lock:
            self._check_process()
            if self._in_transaction:
                self._poisoned = True
                raise SessionError('Reentrant transaction')
            self._in_transaction = True
            self._poisoned = False
            self._sealed = False
            try:
                value = self._validate(handle)
                if expected is not None and value != expected:
                    raise SessionError('Changed session')
                yield value
            finally:
                self._in_transaction = False
                self._poisoned = False
                self._sealed = False

    def validate(self, handle, expected=None):
        # Only valid from inside a transaction on this thread.
        if not self._in_transaction or not self._lock._is_owned():
            raise SessionError('Transaction required')
        value = self._validate(handle)
        if expected is not None and value != expected:
            raise SessionError('Changed session')
        return value

    def seal(self, handle, expected):
        # Check is the linearization point, before durable marker removal.
        value = self.validate(handle, expected)
        self._sealed = True
        return value
