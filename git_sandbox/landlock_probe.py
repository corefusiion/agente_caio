"""Offline disposable Landlock experiment, NOT a Git worker or security attestation.

Run with --probe only. The parent creates synthetic files; the child restricts
itself, never the caller. Never pass a real repository or secrets to this tool.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

# Linux generic syscall numbers, valid for these ABIs only.
_ARCHES = {"x86_64", "aarch64"}
_CREATE, _ADD, _RESTRICT = 444, 445, 446
_READ_FILE, _READ_DIR = 1 << 2, 1 << 3


class _Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathRule(ctypes.Structure):
    _layout_ = 'ms'
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def _call(libc, number, *args):
    result = libc.syscall(number, *args)
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return result


def _blocked(call):
    try:
        call()
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            return True
        raise
    return False


def _child(root: Path):
    if sys.platform != 'linux' or platform.machine() not in _ARCHES or not hasattr(os, 'O_PATH'):
        raise RuntimeError('unsupported platform')
    libc = ctypes.CDLL(None, use_errno=True)
    abi = _call(libc, _CREATE, 0, 0, 1)
    if abi < 3:
        raise RuntimeError('Landlock ABI 3+ required for truncate control')
    # Rights introduced in ABI 1, REFER in ABI 2, TRUNCATE in ABI 3,
    # IOCTL_DEV in ABI 5. Restrict every known applicable filesystem right.
    handled = (1 << 15) - 1
    if abi >= 5:
        handled |= 1 << 15
    attr = _Ruleset(handled)
    ruleset = _call(libc, _CREATE, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    try:
        folder = os.open(root / 'allowed', os.O_PATH | os.O_CLOEXEC)
        try:
            rule = _PathRule(_READ_FILE | _READ_DIR, folder)
            _call(libc, _ADD, ruleset, 1, ctypes.byref(rule), 0)
        finally:
            os.close(folder)
        if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
            err = ctypes.get_errno()
            raise OSError(err, os.strerror(err))
        _call(libc, _RESTRICT, ruleset, 0)
    finally:
        os.close(ruleset)

    allowed = root / 'allowed'
    outside = root / 'outside'
    def write():
        with (allowed / 'ordinary').open('w') as stream:
            stream.write('changed')
    def create():
        with (allowed / 'new').open('w') as stream:
            stream.write('changed')
    def execute():
        # exec in a child so failure to enforce execution never replaces probe.
        pid = os.fork()
        if pid == 0:
            try:
                os.execve(str(allowed / 'executable'), ['executable'], {})
            except OSError as exc:
                os._exit(77 if exc.errno in (errno.EACCES, errno.EPERM) else 78)
            os._exit(79)
        _, status = os.waitpid(pid, 0)
        return os.WIFEXITED(status) and os.WEXITSTATUS(status) == 77
    # Test writes while the file is still owner-writable, before the chmod test.
    write_denied = _blocked(write)
    create_denied = _blocked(create)
    # Landlock does not restrict chmod: demonstrate on synthetic data only.
    os.chmod(allowed / 'ordinary', 0o400)
    chmod_not_blocked = (os.stat(allowed / 'ordinary').st_mode & 0o777) == 0o400
    return {
        'abi': abi,
        'chmod_not_blocked': chmod_not_blocked,
        'allowed_read': (allowed / 'ordinary').read_text() == 'fixture',
        'outside_read_denied': _blocked(lambda: (outside / 'secret').read_text()),
        'symlink_escape_denied': _blocked(lambda: (allowed / 'symlink').read_text()),
        'write_denied': write_denied,
        'create_denied': create_denied,
        'execute_denied': execute(),
        # Important counterexample: inode aliases are NOT confined by path rules.
        'hardlink_exposes_outside_inode': (allowed / 'hardlink').read_text() == 'synthetic-secret',
    }


def probe():
    """Run with fabricated data only. Failure is never a reason to enable Git."""
    with tempfile.TemporaryDirectory(prefix='caio-git-probe-') as temp:
        root = Path(temp)
        allowed, outside = root / 'allowed', root / 'outside'
        allowed.mkdir()
        outside.mkdir()
        (allowed / 'ordinary').write_text('fixture')
        (outside / 'secret').write_text('synthetic-secret')
        (allowed / 'symlink').symlink_to(outside / 'secret')
        os.link(outside / 'secret', allowed / 'hardlink')
        executable = allowed / 'executable'
        # A real ELF, not a script: a denied interpreter cannot mask an exec bug.
        shutil.copyfile(sys.executable, executable)
        executable.chmod(0o700)
        try:
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), '--child', str(root)],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
                env={'PATH': os.defpath, 'LANG': 'C'}, close_fds=True, check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {'ok': False, 'error': type(exc).__name__, 'git_enabled': False}
        if result.returncode != 0:
            return {'ok': False, 'error': 'probe failed', 'git_enabled': False}
        try:
            details = json.loads(result.stdout)
            checks = ('allowed_read', 'outside_read_denied', 'symlink_escape_denied',
                      'write_denied', 'create_denied', 'execute_denied',
                      'hardlink_exposes_outside_inode')
            ok = all(details.get(k) is True for k in checks)
            return {'ok': ok, 'details': details, 'git_enabled': False}
        except (ValueError, AttributeError):
            return {'ok': False, 'error': 'invalid probe response', 'git_enabled': False}


if __name__ == '__main__':
    if sys.argv[1:] == ['--probe']:
        try:
            print(json.dumps(probe(), sort_keys=True))
        except (OSError, RuntimeError) as exc:
            print(json.dumps({'ok': False, 'error': type(exc).__name__, 'git_enabled': False}))
    elif len(sys.argv) == 3 and sys.argv[1] == '--child':
        try:
            print(json.dumps(_child(Path(sys.argv[2])), sort_keys=True))
        except (OSError, RuntimeError) as exc:
            print(json.dumps({'error': type(exc).__name__}))
            sys.exit(1)
    else:
        sys.exit('Use --probe with synthetic fixtures only')
