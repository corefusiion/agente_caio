"""Offline data-preservation and multithreaded-fork regressions."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from caiocore.server import google_oauth_local as mod

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX store')
ROOT = Path(__file__).resolve().parents[1]
PREFIX = '.google-token-'
REC = {'refresh_token': 'synthetic', 'scopes': [mod.SCOPES['calendar_read']]}


@pytest.fixture
def private(tmp_path):
    tmp_path.chmod(0o700)
    return tmp_path


@pytest.mark.parametrize('kind', ['regular', 'symlink', 'hardlink', 'directory'])
def test_preexisting_matching_entries_are_not_owned_or_removed(private, kind):
    path = private / (PREFIX + 'a' * 32)
    target = private / 'unrelated'
    target.write_bytes(b'important unrelated data')
    target.chmod(0o600)
    if kind == 'regular':
        path.write_bytes(b'important unrelated data')
        path.chmod(0o600)
    elif kind == 'symlink':
        path.symlink_to(target)
    elif kind == 'hardlink':
        os.link(target, path)
    else:
        path.mkdir()
    before = path.lstat()
    with pytest.raises(mod.OAuthError, match='inspection required') as exc:
        mod.PrivateTokenStore(private)
    assert 'important unrelated data' not in str(exc.value)
    assert path.lstat().st_ino == before.st_ino
    assert target.read_bytes() == b'important unrelated data'
    if kind == 'regular':
        assert path.read_bytes() == b'important unrelated data'


def test_suspicious_entry_blocks_every_operation_without_changing_existing_data(private):
    store = mod.PrivateTokenStore(private)
    store.save(REC)
    committed = (private / 'google-token.json').read_bytes()
    paths = []
    for c in 'ab':
        path = private / (PREFIX + c * 32)
        path.write_bytes(b'unrelated')
        path.chmod(0o600)
        paths.append(path)
    for operation in (store.load, store.status, lambda: store.save(REC), store.delete_local):
        with pytest.raises(mod.OAuthError, match='inspection required'):
            operation()
        assert (private / 'google-token.json').read_bytes() == committed
        assert all(p.read_bytes() == b'unrelated' for p in paths)
    # Simulate a deliberate, separate inspection and handling by the owner.
    for path in paths:
        path.unlink()
    assert store.load() == REC
    store.close()
    store.close()


def test_other_names_do_not_block_or_get_removed(private):
    for name in (PREFIX + 'g' * 32, PREFIX + 'A' * 32, PREFIX + 'a' * 31,
                 PREFIX + 'a' * 32 + '.other', 'unrelated'):
        (private / name).write_bytes(b'preserve')
    with mod.PrivateTokenStore(private) as store:
        store.save(REC)
        assert store.load() == REC
        store.delete_local()
    assert all(p.read_bytes() == b'preserve' for p in private.iterdir())


def test_directory_fsync_failure_is_retried_when_no_temp_exists(private, monkeypatch):
    store = mod.PrivateTokenStore(private)
    original = mod.os.fsync
    calls = []
    def fail(fd):
        calls.append(fd)
        raise OSError('synthetic')
    monkeypatch.setattr(mod.os, 'fsync', fail)
    for operation in (store.load, store.delete_local):
        with pytest.raises(mod.OAuthError, match='cleanup failed'):
            operation()
    assert len(calls) == 2
    monkeypatch.setattr(mod.os, 'fsync', original)
    assert store.load() is None
    store.close()


def test_failed_save_leaves_temp_and_blocks_until_inspected(private, monkeypatch):
    store = mod.PrivateTokenStore(private)
    monkeypatch.setattr(mod.os, 'replace', lambda *a, **kw: (_ for _ in ()).throw(OSError('synthetic')))
    with pytest.raises(mod.OAuthError, match='Token storage failed'):
        store.save(REC)
    leftovers = list(private.glob(PREFIX + '*'))
    assert len(leftovers) == 1
    assert leftovers[0].stat().st_mode & 0o777 == 0o600
    assert b'synthetic' in leftovers[0].read_bytes()
    with pytest.raises(mod.OAuthError, match='inspection required'):
        store.load()
    assert leftovers[0].exists()
    store.close()


def test_successful_rename_does_not_unlink_reused_temporary_name(private, monkeypatch):
    store = mod.PrivateTokenStore(private)
    real_replace = mod.os.replace
    def replace_and_reuse(src, dst, **kw):
        real_replace(src, dst, **kw)
        p = private / src
        p.write_bytes(b'new unrelated file')
        p.chmod(0o600)
    monkeypatch.setattr(mod.os, 'replace', replace_and_reuse)
    store.save(REC)
    paths = list(private.glob(PREFIX + '*'))
    assert len(paths) == 1 and paths[0].read_bytes() == b'new unrelated file'
    with pytest.raises(mod.OAuthError, match='inspection required'):
        store.load()
    store.close()


def test_crashed_writer_leftover_is_preserved(private):
    script = '''
import os, sys
from caiocore.server import google_oauth_local as m
s = m.PrivateTokenStore(sys.argv[1])
m.os.replace = lambda *a, **kw: os._exit(91)
s.save({'refresh_token': 'synthetic', 'scopes': [m.SCOPES['calendar_read']]})
'''
    proc = subprocess.run([sys.executable, '-c', script, str(private)], cwd=ROOT, timeout=10)
    assert proc.returncode == 91
    leftovers = list(private.glob(PREFIX + '*'))
    assert len(leftovers) == 1
    before = leftovers[0].read_bytes()
    with pytest.raises(mod.OAuthError, match='inspection required'):
        mod.PrivateTokenStore(private)
    assert leftovers[0].read_bytes() == before


@pytest.mark.skipif(not hasattr(os, 'fork'), reason='fork unavailable')
@pytest.mark.parametrize('locked', [True, False])
def test_inherited_close_fails_before_mutex_in_forked_child(private, locked):
    # The outer subprocess timeout and the parent's inner deadline make even
    # the original deadlock safe: the forked child is killed and reaped.
    script = r'''
import os, select, signal, sys, threading, time
from caiocore.server import google_oauth_local as m
s = m.PrivateTokenStore(sys.argv[1])
held = threading.Event()
release = threading.Event()
def worker():
    with s._mutex:
        held.set()
        if not release.wait(8):
            os._exit(50)
t = None
if sys.argv[2] == '1':
    t = threading.Thread(target=worker)
    t.start()
    if not held.wait(3):
        os._exit(51)
r, w = os.pipe()
pid = os.fork()
if pid == 0:
    os.close(r)
    try:
        for action in (s.close, s.close, s.load, lambda: s.__exit__(None, None, None)):
            try:
                action()
            except m.OAuthError:
                pass
            else:
                os._exit(52)
        os.fstat(s._fd)  # child must not unlock or close parent's inherited handle
        os.write(w, b'ok')
    except BaseException:
        os._exit(53)
    os._exit(0)
os.close(w)
try:
    ready = select.select([r], [], [], 3)[0]
    if not ready:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        raise RuntimeError('forked child hung')
    msg = os.read(r, 10)
    _, status = os.waitpid(pid, 0)
    assert msg == b'ok' and os.waitstatus_to_exitcode(status) == 0, (msg, status)
finally:
    os.close(r)
    release.set()
    if t:
        t.join(timeout=3)
        assert not t.is_alive()
assert s.load() is None
s.close()
s.close()
'''
    result = subprocess.run([sys.executable, '-c', script, str(private), '1' if locked else '0'],
                            cwd=ROOT, capture_output=True, text=True, timeout=12)
    assert result.returncode == 0, result.stderr
