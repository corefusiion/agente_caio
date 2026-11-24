"""Offline Git gate and disposable Landlock experiment; no real repositories."""
import os
import shutil
import subprocess
import threading

import pytest

from git_sandbox import contract, landlock_probe


@pytest.mark.parametrize('value,expected', [
    ({'operation': 'status'}, ('status', None)),
    ({'operation': 'branches'}, ('branches', None)),
    ({'operation': 'log'}, ('log', 20)),
    ({'operation': 'log', 'limit': 1}, ('log', 1)),
    ({'operation': 'log', 'limit': 50}, ('log', 50)),
])
def test_contract_accepts_only_narrow_shapes(value, expected):
    assert contract.validate_request(value) == expected
    for opt_in in (False, True):
        with pytest.raises(contract.GitUnavailable):
            contract.execute(value, opt_in=opt_in)


@pytest.mark.parametrize('value', [None, [], 'status', {},
    {'operation': 'push'}, {'operation': 'diff'}, {'operation': '--exec=sh'},
    {'operation': 'status', 'path': '/etc/passwd'},
    {'operation': 'status', 'args': ['--ext-diff']},
    {'operation': 'status', 'limit': 1}, {'operation': 'log', 'limit': None},
    {'operation': 'log', 'limit': True}, {'operation': 'log', 'limit': 0},
    {'operation': 'log', 'limit': 51}, {'operation': 'log', 'limit': '1'},
    {'operation': 'log', 'repo': '../outside'},
])
def test_invalid_inputs_fail_closed(value):
    with pytest.raises(contract.InvalidRequest):
        contract.execute(value, opt_in=True)


def test_kernel_probe_demonstrates_real_landlock_boundaries_and_gaps():
    result = landlock_probe.probe()
    assert result['git_enabled'] is False
    if not result['ok']:
        pytest.skip('Landlock experiment unsupported or prohibited in this environment')
    details = result['details']
    assert details['abi'] >= 3
    assert all(details[key] for key in ('allowed_read', 'outside_read_denied',
        'symlink_escape_denied', 'write_denied', 'create_denied', 'execute_denied',
        'hardlink_exposes_outside_inode', 'chmod_not_blocked'))


def test_probe_failure_cannot_enable_git(monkeypatch):
    def fail(*args, **kwargs):
        raise OSError('sandbox unavailable')
    monkeypatch.setattr(landlock_probe.subprocess, 'run', fail)
    result = landlock_probe.probe()
    assert result['ok'] is False
    assert result['git_enabled'] is False
    with pytest.raises(contract.GitUnavailable):
        contract.execute({'operation': 'status'}, opt_in=True)


def test_hostile_repository_and_racing_symlinks_never_trigger_git(tmp_path, monkeypatch):
    git = shutil.which('git')
    if not git:
        pytest.skip('git is not installed for the disposable fixture')
    repo = tmp_path / 'repo'
    repo.mkdir()
    env = {'PATH': os.defpath, 'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
    subprocess.run([git, 'init', '-q', str(repo)], env=env, check=True,
                   timeout=10, capture_output=True)
    secret = tmp_path / 'secret'
    secret.write_text('SYNTHETIC_PRIVATE_SENTINEL')
    marker = repo / 'marker'
    hook = repo / 'hook'
    hook.write_text('#!/bin/sh\nprintf exploited > marker\n')
    hook.chmod(0o700)
    (repo / '.git' / 'config').write_text(
        '[core]\n repositoryformatversion = 0\n bare = false\n fsmonitor = ./hook\n'
        ' hooksPath = .\n[diff]\n external = ./hook\n[include]\n path = ' + str(secret) + '\n')
    (repo / '.git' / 'objects' / 'info' / 'alternates').write_text(str(tmp_path) + '\n')
    os.link(secret, repo / 'hardlink')
    symlink = repo / 'symlink'
    symlink.symlink_to(secret)
    stop = threading.Event()
    def race():
        while not stop.is_set():
            symlink.unlink(missing_ok=True)
            symlink.symlink_to(secret)
    thread = threading.Thread(target=race)
    thread.start()
    # Any execution would be a security failure, regardless of plausible output.
    def forbidden(*args, **kwargs):
        pytest.fail('Git or another child was launched')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    try:
        for op in ('status', 'branches', 'log'):
            for _ in range(200):
                with pytest.raises(contract.GitUnavailable) as exc:
                    contract.execute({'operation': op}, opt_in=True)
                assert 'SYNTHETIC_PRIVATE_SENTINEL' not in str(exc.value)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not marker.exists()
    assert secret.read_text() == 'SYNTHETIC_PRIVATE_SENTINEL'
