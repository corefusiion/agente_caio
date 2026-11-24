"""Offline, explicit opt-in preferences for one caller-selected private directory.

This is storage, not authentication, authorization, a search or a scheduler.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows ACLs and locking are not implemented.
    fcntl = None

_FIELDS = frozenset({"team", "sports", "films", "series", "streaming", "betting"})
_CONSENTS = frozenset({"memory", "questions", "external_search", "proactivity"})
_FILE = "preferences.json"
_LOCK = ".preferences.lock"
_MAX_BYTES = 32768


class ConsentRequired(PermissionError):
    """The owner has not opted in to persistent preferences."""


def _empty():
    return {"version": 1, "consents": {key: False for key in sorted(_CONSENTS)},
            "preferences": {}}


def _validate_preferences(preferences):
    if type(preferences) is not dict or not preferences.keys() <= _FIELDS:
        raise ValueError("unknown preference field")
    for values in preferences.values():
        if type(values) is not list or len(values) > 20:
            raise ValueError("preferences must be lists of at most 20 items")
        for value in values:
            if (type(value) is not str or value != value.strip() or not 1 <= len(value) <= 80
                    or not value.isprintable()):
                raise ValueError("each preference must be a nonempty printable label of up to 80 characters")
        if len(values) != len(set(values)):
            raise ValueError("duplicate preference")


def _validate_state(state):
    if type(state) is not dict or set(state) != {"version", "consents", "preferences"}:
        raise ValueError("invalid profile structure")
    if type(state["version"]) is not int or state["version"] != 1:
        raise ValueError("unsupported profile version")
    consents = state["consents"]
    if (type(consents) is not dict or set(consents) != _CONSENTS
            or any(type(value) is not bool for value in consents.values())
            or not consents["memory"]):
        raise ValueError("invalid stored consent")
    _validate_preferences(state["preferences"])


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _secure_file(fd):
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_nlink != 1 or info.st_mode & 0o077):
        raise PermissionError("profile file must be private, owned and regular")


class ProfileStore:
    """One profile per pre-existing private directory; caller must authorize access."""

    def __init__(self, directory):
        if fcntl is None or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "geteuid"):
            raise NotImplementedError("secure POSIX filesystem support is required")
        self.directory = Path(directory)
        if not self.directory.is_absolute():
            raise ValueError("private directory must be an absolute path")

    @contextmanager
    def _locked(self):
        # The supplied directory and its ancestors must be under trusted control.
        dfd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(dfd)
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise PermissionError("profile directory must be private and owned")
            fd = os.open(_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=dfd)
            try:
                _secure_file(fd)
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    # Remove leftovers from a process interrupted during atomic write.
                    removed = False
                    for name in os.listdir(dfd):
                        if re.fullmatch(r"\.preferences-[0-9a-f]{32}\.tmp", name):
                            os.unlink(name, dir_fd=dfd)
                            removed = True
                    if removed:
                        os.fsync(dfd)
                    yield dfd
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
        finally:
            os.close(dfd)

    def _read(self, dfd):
        try:
            fd = os.open(_FILE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
        except FileNotFoundError:
            return _empty()
        try:
            _secure_file(fd)
            with os.fdopen(fd, "rb", closefd=False) as stream:
                data = stream.read(_MAX_BYTES + 1)
            if len(data) > _MAX_BYTES:
                raise ValueError("profile exceeds size limit")
            state = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
            _validate_state(state)
            return state
        finally:
            os.close(fd)

    def _write(self, dfd, state):
        _validate_state(state)
        data = json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(data) > _MAX_BYTES:
            raise ValueError("profile exceeds size limit")
        temp = ".preferences-" + secrets.token_hex(16) + ".tmp"
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=dfd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, _FILE, src_dir_fd=dfd, dst_dir_fd=dfd)
            os.fsync(dfd)
        finally:
            try:
                os.unlink(temp, dir_fd=dfd)
            except FileNotFoundError:
                pass

    def _clear(self, dfd):
        try:
            os.unlink(_FILE, dir_fd=dfd)
            os.fsync(dfd)
        except FileNotFoundError:
            pass

    def view(self):
        """Return a detached snapshot; with no opt-in all flags are false."""
        with self._locked() as dfd:
            return self._read(dfd)

    def set_consent(self, purpose, granted):
        """Set one explicit consent. Revoking memory deletes the profile entirely."""
        if type(purpose) is not str or purpose not in _CONSENTS or type(granted) is not bool:
            raise ValueError("invalid consent")
        with self._locked() as dfd:
            if purpose == "memory" and not granted:
                self._clear(dfd)
                return _empty()
            state = self._read(dfd)
            if purpose != "memory" and not state["consents"]["memory"]:
                raise ConsentRequired("opt in to memory first")
            state["consents"][purpose] = granted
            self._write(dfd, state)
            return state

    def set_preferences(self, updates):
        """Replace named label lists, or pass None to remove an individual field."""
        if type(updates) is not dict or not updates or not updates.keys() <= _FIELDS:
            raise ValueError("invalid preference update")
        _validate_preferences({k: v for k, v in updates.items() if v is not None})
        # Detach from caller-owned lists before obtaining the lock.
        updates = {k: None if v is None else list(v) for k, v in updates.items()}
        with self._locked() as dfd:
            state = self._read(dfd)
            if not state["consents"]["memory"]:
                raise ConsentRequired("opt in to memory first")
            for key, values in updates.items():
                if values is None:
                    state["preferences"].pop(key, None)
                else:
                    state["preferences"][key] = values
            self._write(dfd, state)
            return state

    def clear(self):
        """Erase stored preferences and every consent; also works on corrupt data."""
        with self._locked() as dfd:
            self._clear(dfd)
