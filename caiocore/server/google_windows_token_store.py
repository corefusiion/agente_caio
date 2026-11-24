"""Windows-only, per-user DPAPI token storage; not yet wired into Calendar.

WindowsTokenStore implements save/load/status/delete_local and context management
like PrivateTokenStore. Its only accepted directory is the current user's Windows
LocalAppData/Caio; it is obtained from the Windows known-folder API, not an
untrusted environment variable. DPAPI uses the current user profile and forbids
UI; the file contains only DPAPI ciphertext. No network or logging is used.

LIMITATIONS: Python directory creation inherits Windows ACLs; this module does NOT
establish or validate ACLs. Before production, an administrator must ensure the
entire directory hierarchy is trusted and inaccessible to untrusted writers,
and Windows-specific ACL/reparse/race behavior must be tested. Checks below are
best effort, not a defense against a concurrent attacker modifying the hierarchy.
DPAPI does not protect against malicious code running as this user, admins, host
compromise, user-profile backups, ciphertext rollback or physical remnants. No
cross-process locking; use a single owner/process. Deletion is local only, does
not revoke Google access or guarantee physical deletion. API/client-config
integration and real Windows validation are NOT provided by this module.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import secrets
import stat
import uuid

from .google_oauth_local import OAuthError, SCOPES

_MAX_PLAIN = 16384
_MAX_CIPHER = 65536
_REPARSE = 0x400


def _is_windows():
    return os.name == 'nt'


def _local_app_data():
    """Use Windows' LocalAppData known folder for the current process user."""
    class GUID(ctypes.Structure):
        _fields_ = [('data1', ctypes.c_uint32), ('data2', ctypes.c_uint16),
                    ('data3', ctypes.c_uint16), ('data4', ctypes.c_ubyte * 8)]
    folder = GUID.from_buffer_copy(uuid.UUID('f1b32785-6fba-4fcf-9d55-7b8e7f157091').bytes_le)
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    ole = ctypes.WinDLL('ole32', use_last_error=True)
    shell.SHGetKnownFolderPath.argtypes = [ctypes.POINTER(GUID), ctypes.c_uint32,
                                            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    shell.SHGetKnownFolderPath.restype = ctypes.c_long
    ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole.CoTaskMemFree.restype = None
    out = ctypes.c_void_p()
    if shell.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(out)) != 0 or not out.value:
        raise OSError('Known folder unavailable')
    try:
        return Path(ctypes.wstring_at(out.value))
    finally:
        ole.CoTaskMemFree(out)


class _DPAPI:
    class _Blob(ctypes.Structure):
        _fields_ = [('size', ctypes.c_uint32), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    def __init__(self):
        self._crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        self._kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self._kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self._kernel.LocalFree.restype = ctypes.c_void_p
        blob_p = ctypes.POINTER(self._Blob)
        self._crypt.CryptProtectData.argtypes = [blob_p, ctypes.c_wchar_p, blob_p,
                                                  ctypes.c_void_p, ctypes.c_void_p,
                                                  ctypes.c_uint32, blob_p]
        self._crypt.CryptProtectData.restype = ctypes.c_int
        self._crypt.CryptUnprotectData.argtypes = [blob_p, ctypes.c_void_p, blob_p,
                                                    ctypes.c_void_p, ctypes.c_void_p,
                                                    ctypes.c_uint32, blob_p]
        self._crypt.CryptUnprotectData.restype = ctypes.c_int

    def _transform(self, data, protect):
        if not isinstance(data, bytes) or not data:
            raise ValueError('Invalid input')
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source = self._Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        output = self._Blob()
        try:
            fn = self._crypt.CryptProtectData if protect else self._crypt.CryptUnprotectData
            # No CRYPTPROTECT_LOCAL_MACHINE: bind to the current Windows user.
            if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
                raise OSError('DPAPI unavailable')
            if not output.data or not output.size or output.size > (_MAX_CIPHER if protect else _MAX_PLAIN):
                raise OSError('Invalid DPAPI output')
            return ctypes.string_at(output.data, output.size)
        finally:
            ctypes.memset(buf, 0, len(data))
            if output.data:
                ctypes.memset(output.data, 0, output.size)
                self._kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))

    def protect(self, data):
        return self._transform(data, True)

    def unprotect(self, data):
        return self._transform(data, False)


def _valid_record(record):
    if not isinstance(record, dict) or set(record) != {'refresh_token', 'scopes'}:
        return False
    refresh, scopes = record['refresh_token'], record['scopes']
    return (isinstance(refresh, str) and bool(refresh) and len(refresh) <= 8192
            and isinstance(scopes, list) and bool(scopes)
            and all(isinstance(x, str) for x in scopes)
            and len(set(scopes)) == len(scopes) and set(scopes).issubset(SCOPES.values()))


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key')
        result[key] = value
    return result


class WindowsTokenStore:
    _name = 'google-token.dpapi'

    def __init__(self, directory=None):
        self._closed = True
        if not _is_windows():
            raise OAuthError('Secure Windows storage unavailable')
        try:
            base = _local_app_data()
            if not base.is_absolute():
                raise ValueError('Invalid known folder')
            self._directory = base / 'Caio'
            if directory is not None and os.path.normcase(os.path.abspath(os.fspath(directory))) != os.path.normcase(os.path.abspath(self._directory)):
                raise ValueError('Invalid directory')
            self._dpapi = _DPAPI()
            # Fail closed even for an empty store if this user profile cannot use DPAPI.
            probe = secrets.token_bytes(32)
            protected = self._dpapi.protect(probe)
            if (not isinstance(protected, bytes) or not protected or protected == probe
                    or self._dpapi.unprotect(protected) != probe):
                raise ValueError('DPAPI unavailable')
            self._check_path(base, directory=True)
            self._directory.mkdir(exist_ok=True, mode=0o700)
            self._check_path(self._directory, directory=True)
            self._closed = False
        except Exception:
            raise OAuthError('Secure Windows storage unavailable') from None

    @staticmethod
    def _check_path(path, *, directory=False):
        # Check ancestors as well; these checks cannot eliminate races on Windows.
        for item in reversed((path, *path.parents)):
            s = item.lstat()
            if stat.S_ISLNK(s.st_mode) or getattr(s, 'st_file_attributes', 0) & _REPARSE:
                raise ValueError('Reparse point')
            if item == path:
                if directory and not stat.S_ISDIR(s.st_mode):
                    raise ValueError('Not a directory')
                if not directory and (not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > _MAX_CIPHER):
                    raise ValueError('Unsafe file')
            elif not stat.S_ISDIR(s.st_mode):
                raise ValueError('Unsafe parent')

    def _check_directory(self):
        if self._closed:
            raise OAuthError('Secure Windows storage unavailable')
        try:
            self._check_path(self._directory, directory=True)
        except Exception:
            raise OAuthError('Secure Windows storage unavailable') from None

    def close(self):
        self._closed = True
        self._dpapi = None

    def __enter__(self):
        self._check_directory()
        return self

    def __exit__(self, *args):
        self.close()

    def _read_cipher(self):
        self._check_directory()
        path = self._directory / self._name
        try:
            try:
                self._check_path(path)
            except FileNotFoundError:
                # Directory must still exist; missing parents are not an absent token.
                self._check_directory()
                return None
            flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0) | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, 'rb') as f:
                s = os.fstat(f.fileno())
                if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1 or s.st_size > _MAX_CIPHER:
                    raise ValueError('Unsafe file')
                data = f.read(_MAX_CIPHER + 1)
            if not data or len(data) > _MAX_CIPHER:
                raise ValueError('Invalid file')
            return data
        except Exception:
            raise OAuthError('Token storage unavailable') from None

    def load(self):
        data = self._read_cipher()
        if data is None:
            return None
        try:
            plain = self._dpapi.unprotect(data)
            if not isinstance(plain, bytes) or not plain or len(plain) > _MAX_PLAIN:
                raise ValueError('Invalid plaintext')
            record = json.loads(plain, object_pairs_hook=_no_duplicates)
            if not _valid_record(record):
                raise ValueError('Invalid record')
            return record
        except Exception:
            raise OAuthError('Token storage unavailable') from None

    def status(self):
        record = self.load()
        return {'status': 'stored_unverified' if record else 'not_configured',
                'scopes': record['scopes'] if record else []}

    def save(self, record):
        if not _valid_record(record):
            raise OAuthError('Invalid record')
        try:
            plain = json.dumps(record, separators=(',', ':')).encode('utf-8')
            if len(plain) > _MAX_PLAIN:
                raise ValueError('Invalid record')
            # Refuse to replace unreadable or corrupt existing credentials.
            self.load()
            cipher = self._dpapi.protect(plain)
            if not isinstance(cipher, bytes) or not cipher or len(cipher) > _MAX_CIPHER or cipher == plain:
                raise ValueError('Invalid ciphertext')
            self._check_directory()
            tmp = self._directory / ('.google-token-' + secrets.token_hex(16) + '.tmp')
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOINHERIT', 0) | getattr(os, 'O_NOFOLLOW', 0)
            created = False
            try:
                fd = os.open(tmp, flags, 0o600)
                created = True
                with os.fdopen(fd, 'wb') as f:
                    f.write(cipher)
                    f.flush()
                    os.fsync(f.fileno())
                self._check_directory()
                os.replace(tmp, self._directory / self._name)
                created = False
            finally:
                if created:
                    try:
                        os.unlink(tmp)
                    except FileNotFoundError:
                        pass
        except Exception:
            raise OAuthError('Token storage failed') from None

    def delete_local(self):
        # Refuse deleting suspicious or corrupt files, and do not imply revocation.
        if self.load() is None:
            return
        try:
            self._check_directory()
            path = self._directory / self._name
            self._check_path(path)
            os.unlink(path)
        except Exception:
            raise OAuthError('Local deletion failed') from None
