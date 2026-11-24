"""Offline crash recovery and serialization tests; all secrets are synthetic."""
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from caiocore.server import google_oauth_local as mod

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX only')
REC = {'refresh_token': 'synthetic-only', 'scopes': [mod.SCOPES['calendar_read']]}
PREFIX = '.google-token-'


@pytest.fixture
def private(tmp_path):
    tmp_path.chmod(0o700)
    return tmp_path


def temp(path, digit, content='synthetic-only'):
    p = path / (PREFIX + digit * 32)
    p.write_text(content)
    p.chmod(0o600)
    return p


def crash_writer(path, phase):
    script = '''
import os, sys
from caiocore.server import google_oauth_local as m
p, phase = sys.argv[1:]
s = m.PrivateTokenStore(p)
if phase == 'before_replace':
    m.os.replace = lambda *a, **kw: os._exit(91)
else:
    original = m.os.replace
    def replace(*a, **kw):
        original(*a, **kw)
        os._exit(92)
    m.os.replace = replace
s.save({'refresh_token': 'synthetic-only', 'scopes': [m.SCOPES['calendar_read']]})
'''
    return subprocess.run([sys.executable, '-c', script, str(path), phase],
                          cwd=str(Path(__file__).resolve().parents[1]), timeout=10)


@pytest.mark.parametrize('existing', [False, True])
def test_real_process_crash_after_temp_fsync_requires_manual_recovery(private, existing):
    previous = {'refresh_token': 'older-synthetic', 'scopes': [mod.SCOPES['calendar_read']]}
    if existing:
        with mod.PrivateTokenStore(private) as store:
            store.save(previous)
    token = private / 'google-token.json'
    token_before = token.read_bytes() if existing else None
    assert crash_writer(private, 'before_replace').returncode == 91
    old = list(private.glob(PREFIX + '*'))
    assert len(old) == 1
    assert old[0].stat().st_mode & 0o777 == 0o600
    assert 'synthetic-only' in old[0].read_text()
    content = old[0].read_bytes()
    with pytest.raises(mod.OAuthError, match='Ambiguous temporary'):
        mod.PrivateTokenStore(private)
    assert old[0].read_bytes() == content
    assert (token.read_bytes() if token.exists() else None) == token_before
    old[0].unlink()  # Simulate owner inspection and resolution.
    with mod.PrivateTokenStore(private) as restarted:
        assert restarted.load() == (previous if existing else None)
        restarted.delete_local()


def test_crash_after_replace_keeps_committed_file(private):
    assert crash_writer(private, 'after_replace').returncode == 92
    with mod.PrivateTokenStore(private) as store:
        assert store.load() == REC
        store.delete_local()
        assert store.load() is None


def test_multiple_temps_and_unrelated_names(private):
    with mod.PrivateTokenStore(private) as store:
        store.save(REC)
        paths = [temp(private, x) for x in '123']
        outsiders = [private / (PREFIX + 'x' * 32), private / (PREFIX + 'a' * 31),
                     private / (PREFIX + 'b' * 32 + '.tmp'), private / 'unrelated']
        for p in outsiders:
            p.write_text('keep')
            p.chmod(0o600)
        token = private / 'google-token.json'
        token_content = token.read_bytes()
        for operation in (store.load, lambda: store.save(REC), store.status, store.delete_local):
            with pytest.raises(mod.OAuthError, match='Ambiguous temporary'):
                operation()
            assert all(p.read_text() == 'synthetic-only' for p in paths)
            assert token.read_bytes() == token_content
        assert all(p.read_text() == 'keep' for p in outsiders)
        for p in paths:
            p.unlink()  # Simulate manual inspection and resolution.
        store.delete_local()
        assert store.load() is None
        assert all(p.read_text() == 'keep' for p in outsiders)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'mode', 'directory'])
def test_suspicious_reserved_entry_refused_without_touching_anything(private, kind):
    good = temp(private, '1')
    bad = private / (PREFIX + '2' * 32)
    target = private / 'other'
    target.write_text('do-not-touch')
    target.chmod(0o600)
    if kind == 'symlink':
        bad.symlink_to(target)
    elif kind == 'hardlink':
        os.link(target, bad)
    elif kind == 'mode':
        bad.write_text('do-not-touch')
        bad.chmod(0o644)
    else:
        bad.mkdir()
    with pytest.raises(mod.OAuthError):
        mod.PrivateTokenStore(private)
    assert good.exists() and target.read_text() == 'do-not-touch'
    assert bad.is_symlink() if kind == 'symlink' else bad.exists()


def test_committed_token_unlink_failure_is_not_success_and_retry_works(private, monkeypatch):
    store = mod.PrivateTokenStore(private)
    store.save(REC)
    original = mod.os.unlink
    calls = []
    def fail_token(name, *args, **kwargs):
        calls.append(name)
        if name == 'google-token.json':
            raise PermissionError('synthetic')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(mod.os, 'unlink', fail_token)
    with pytest.raises(mod.OAuthError, match='Local deletion failed'):
        store.delete_local()
    assert calls == ['google-token.json']
    assert store.load() == REC
    monkeypatch.setattr(mod.os, 'unlink', original)
    store.delete_local()
    assert store.load() is None
    store.close()


def test_recovery_fsync_failure_fails_closed_and_is_retried(private, monkeypatch):
    store = mod.PrivateTokenStore(private)
    store.save(REC)
    token = private / 'google-token.json'
    before = token.read_bytes()
    original = mod.os.fsync
    calls = []
    def fail(fd):
        calls.append(fd)
        raise OSError('synthetic')
    monkeypatch.setattr(mod.os, 'fsync', fail)
    with pytest.raises(mod.OAuthError, match='cleanup failed'):
        store.load()
    assert token.read_bytes() == before
    with pytest.raises(mod.OAuthError, match='cleanup failed'):
        store.delete_local()
    assert len(calls) == 2
    assert token.read_bytes() == before
    monkeypatch.setattr(mod.os, 'fsync', original)
    assert store.load() == REC
    store.delete_local()
    assert store.load() is None
    store.close()


def test_directory_scan_failure_preserves_token_and_retries(private, monkeypatch):
    with mod.PrivateTokenStore(private) as store:
        store.save(REC)
        token = private / 'google-token.json'
        before = token.read_bytes()
        original = mod.os.listdir
        def fail_scan(*args, **kwargs):
            raise PermissionError('synthetic')
        monkeypatch.setattr(mod.os, 'listdir', fail_scan)
        with pytest.raises(mod.OAuthError, match='cleanup failed'):
            store.delete_local()
        assert token.read_bytes() == before
        monkeypatch.setattr(mod.os, 'listdir', original)
        assert store.load() == REC
        store.delete_local()
        assert store.load() is None


def test_fsync_after_replace_failure_reports_error_and_retry_reads_commit(private, monkeypatch):
    with mod.PrivateTokenStore(private) as store:
        original = mod.os.fsync
        directory_syncs = []
        def fail_after_replace(fd):
            if fd == store._fd:
                directory_syncs.append(fd)
                if len(directory_syncs) == 2:
                    raise OSError('synthetic')
            return original(fd)
        monkeypatch.setattr(mod.os, 'fsync', fail_after_replace)
        with pytest.raises(mod.OAuthError, match='Token storage failed'):
            store.save(REC)
        assert len(directory_syncs) == 2
        assert not list(private.glob(PREFIX + '*'))
        assert (private / 'google-token.json').exists()
        monkeypatch.setattr(mod.os, 'fsync', original)
        assert store.load() == REC


def test_failed_save_leaves_temp_untouched_and_requires_manual_recovery(private, monkeypatch):
    store = mod.PrivateTokenStore(private)
    original_unlink = mod.os.unlink
    original_replace = mod.os.replace
    unlink_calls = []
    def fail_replace(*args, **kwargs):
        raise OSError('synthetic')
    def fail_unlink(name, *args, **kwargs):
        unlink_calls.append(name)
        if name.startswith(PREFIX):
            raise PermissionError('synthetic')
        return original_unlink(name, *args, **kwargs)
    monkeypatch.setattr(mod.os, 'replace', fail_replace)
    monkeypatch.setattr(mod.os, 'unlink', fail_unlink)
    with pytest.raises(mod.OAuthError, match='Token storage failed'):
        store.save(REC)
    leftovers = list(private.glob(PREFIX + '*'))
    assert len(leftovers) == 1
    assert leftovers[0].stat().st_mode & 0o777 == 0o600
    assert 'synthetic-only' in leftovers[0].read_text()
    assert unlink_calls == []  # No cleanup by name, even for our failed save.
    monkeypatch.setattr(mod.os, 'replace', original_replace)
    monkeypatch.setattr(mod.os, 'unlink', original_unlink)
    with pytest.raises(mod.OAuthError, match='Ambiguous temporary'):
        store.delete_local()
    assert leftovers[0].exists()
    leftovers[0].unlink()  # Simulate manual inspection and resolution.
    store.delete_local()
    assert not list(private.glob(PREFIX + '*'))
    store.close()


def test_concurrent_instances_cannot_cleanup_live_save(private, monkeypatch):
    first = mod.PrivateTokenStore(private)
    second = mod.PrivateTokenStore(private)
    entered, release, started = threading.Event(), threading.Event(), threading.Event()
    original_replace = mod.os.replace
    def blocked_replace(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original_replace(*args, **kwargs)
    monkeypatch.setattr(mod.os, 'replace', blocked_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        saving = pool.submit(first.save, REC)
        assert entered.wait(5)
        def deleting():
            started.set()
            second.delete_local()
        deleting_future = pool.submit(deleting)
        assert started.wait(5)
        assert not deleting_future.done()
        assert len(list(private.glob(PREFIX + '*'))) == 1
        release.set()
        saving.result(timeout=5)
        deleting_future.result(timeout=5)
    assert first.load() is None
    first.close()
    second.close()


def test_directory_permissions_rechecked_before_recovery(private):
    store = mod.PrivateTokenStore(private)
    left = temp(private, 'a')
    private.chmod(0o755)
    with pytest.raises(mod.OAuthError):
        store.delete_local()
    assert left.exists()
    private.chmod(0o700)
    with pytest.raises(mod.OAuthError, match='Ambiguous temporary'):
        store.delete_local()
    assert left.read_text() == 'synthetic-only'
    left.unlink()  # Simulate manual inspection and resolution.
    store.delete_local()
    assert store.load() is None
    store.close()
