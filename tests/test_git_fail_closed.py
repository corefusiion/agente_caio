"""Regression tests: Git remains unavailable until a real isolation boundary exists.

These tests exercise the actual registry and agent; they do not assert that a
hypothetical Git implementation would contain the hostile repositories.
"""
import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from caiocore.agent.tools.base import Tool
from caiocore.agent.tools.registry import ToolRegistry


@pytest.fixture
def hostile_repo(tmp_path, monkeypatch):
    git = shutil.which('git')
    if not git:
        pytest.skip('Git is unavailable')
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    env = {'PATH': os.defpath, 'HOME': str(tmp_path), 'GIT_CONFIG_NOSYSTEM': '1',
           'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}
    subprocess.run([git, 'init', '-q', str(workspace)], env=env, check=True,
                   timeout=10, capture_output=True)
    outside = tmp_path / 'outside'
    outside.mkdir()
    secret = outside / 'secret'
    secret.write_text('GIT_TEST_PRIVATE_SENTINEL', encoding='utf-8')
    marker = workspace / 'marker'
    # This executable should NEVER be launched by a read operation.
    hook = workspace / 'malicious-hook'
    hook.write_text('#!/bin/sh\nprintf exploited > marker\n', encoding='utf-8')
    hook.chmod(0o700)
    (workspace / '.git' / 'config').write_text(
        '[core]\n\trepositoryformatversion = 0\n\tbare = false\n'
        '\tfsmonitor = ./malicious-hook\n\thooksPath = .\n'
        '[diff]\n\texternal = ./malicious-hook\n'
        '[include]\n\tpath = ' + str(outside / 'config') + '\n', encoding='utf-8')
    (outside / 'config').write_text('[core]\n\tfsmonitor = ./malicious-hook\n', encoding='utf-8')
    (workspace / 'external-file').symlink_to(secret)
    (workspace / 'external-dir').symlink_to(outside, target_is_directory=True)
    (workspace / 'large.txt').write_text('X' * 200_000, encoding='utf-8')
    (workspace / '.git' / 'objects' / 'info' / 'alternates').write_text(
        str(outside) + '\n', encoding='utf-8')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(outside / 'config'))
    monkeypatch.setenv('GIT_EXTERNAL_DIFF', str(hook))
    monkeypatch.setenv('GIT_DIR', str(outside))
    return workspace, secret, marker


class FakeGitTool(Tool):
    """A pretend registration must not punch a hole in the allowlist."""
    def __init__(self, name):
        self._name = name
        self.executed = False

    @property
    def name(self):
        return self._name

    @property
    def description(self):
        return 'fake git'

    @property
    def parameters(self):
        return {'type': 'object', 'properties': {}}

    async def execute(self, **kwargs):
        self.executed = True
        return 'SHOULD_NOT_RUN'


@pytest.mark.parametrize('name', ['git', 'git_status', 'git_diff', 'git_branch', 'git_log'])
def test_registry_fails_closed_for_git_even_if_injected(hostile_repo, name):
    workspace, secret, marker = hostile_repo
    registry = ToolRegistry(enable_workspace_file_tools=True)
    tool = FakeGitTool(name)
    registry.register(tool)
    response = asyncio.run(registry.execute(name, {}))
    assert response.startswith('BLOQUEADO:')
    assert not tool.executed
    assert 'GIT_TEST_PRIVATE_SENTINEL' not in response
    assert not marker.exists()


def test_agent_has_no_git_capability_for_hostile_repo(hostile_repo):
    from caiocore.agent.loop import AgentLoop
    from caiocore.bus.queue import MessageBus
    workspace, secret, marker = hostile_repo
    provider = Mock()
    provider.get_default_model.return_value = 'test'
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=workspace)
    for name in ('git', 'git_status', 'git_diff', 'git_branch', 'git_log', 'exec'):
        assert not agent.tools.has(name)
        for params in ({}, {'path': '../outside/secret'}, {'path': str(secret)},
                       {'args': ['--ext-diff', '--output=' + str(secret)]},
                       {'operation': 'push', 'limit': 999999999}):
            result = asyncio.run(agent.tools.execute(name, params))
            assert 'not found' in result
            assert 'GIT_TEST_PRIVATE_SENTINEL' not in result
    assert not marker.exists()


def test_external_gitdir_and_worktree_not_exposed(tmp_path):
    from caiocore.agent.loop import AgentLoop
    from caiocore.bus.queue import MessageBus
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    # A linked-worktree gitfile or external gitdir must not become a capability.
    (workspace / '.git').write_text('gitdir: ' + str(tmp_path / 'external') + '\n')
    provider = Mock()
    provider.get_default_model.return_value = 'test'
    agent = AgentLoop(bus=MessageBus(), provider=provider, workspace=workspace)
    for name in ('git', 'git_status', 'git_diff', 'git_branch', 'git_log'):
        assert not agent.tools.has(name)
        assert 'not found' in asyncio.run(agent.tools.execute(name, {}))
