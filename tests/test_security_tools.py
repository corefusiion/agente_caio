"""Adversarial checks for fail-closed tool policy (no network or real shell)."""
import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from caiocore.agent.tools.base import Tool
from caiocore.agent.tools.registry import ToolRegistry
from caiocore.agent.tools.filesystem import ReadFileTool, ListDirTool, _resolve_path
from caiocore.agent.tools.shell import ExecTool
from caiocore.config.schema import Config


def run(coro):
    return asyncio.run(coro)


class Fake(Tool):
    name = "email_send"
    description = "fake"
    parameters = {"type": "object", "properties": {}, "required": []}
    def __init__(self, name="email_send", approval=False):
        self.name = name
        self.called = False
        self.approval = approval
    @property
    def requires_approval(self):
        return self.approval
    async def execute(self, **kwargs):
        self.called = True
        return "executed"


@pytest.mark.parametrize("name", ["email_send", "email_delete", "cron", "message", "google_calendar", "workflow", "spawn", "exec", "sandbox_exec", "desktop", "mcp_server_do", "write_file", "edit_file", "unknown", "web_search"])
def test_unknown_and_critical_tools_are_blocked(name):
    registry = ToolRegistry()
    fake = Fake(name)
    registry.register(fake)
    # A generic confirmation, autonomous mode or altered payload cannot affect
    # the registry; test multiple independent requests with changed arguments.
    for params in ({}, {"approved": True}, {"approved": True, "payload": "different"}):
        assert "BLOQUEADO" in run(registry.execute(name, params))
    assert not fake.called


def test_approval_required_always_blocked():
    reg = ToolRegistry()
    tool = Fake("email_send", approval=True)
    reg.register(tool)
    assert "BLOQUEADO" in run(reg.execute(tool.name, {}))
    assert not tool.called


def test_legacy_config_and_safe_defaults():
    default = Config()
    assert default.agents.defaults.autonomous is False
    assert default.tools.restrict_to_workspace is True
    assert default.tools.enable_workspace_file_tools is False
    old = Config.model_validate({"agents": {"defaults": {"autonomous": True}},
                                 "tools": {"restrictToWorkspace": False}})
    assert old.agents.defaults.autonomous is True  # parsed, never authorizes tools
    reg = ToolRegistry()
    fake = Fake()
    reg.register(fake)
    assert "BLOQUEADO" in run(reg.execute(fake.name, {"autonomous": old.agents.defaults.autonomous}))
    assert not fake.called


def test_filesystem_component_boundaries_and_symlinks(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    sibling = tmp_path / "work-other"
    sibling.mkdir()
    secret = sibling / "secret"
    secret.write_text("secret")
    (workspace / "link").symlink_to(secret)
    (workspace / "linkdir").symlink_to(sibling, target_is_directory=True)
    for path in (str(secret), "../work-other/secret", "link", "linkdir/secret"):
        with pytest.raises(PermissionError):
            _resolve_path(path, workspace, workspace)
    (workspace / "ok").write_text("safe")
    assert _resolve_path("ok", workspace, workspace) == workspace / "ok"


def test_files_disabled_by_default_and_even_when_opted_in(tmp_path):
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "ok").write_text("safe")
    outside = tmp_path / "outside"
    outside.write_text("secret")
    (workspace / "link").symlink_to(outside)
    t = ReadFileTool(workspace=workspace, allowed_dir=workspace)
    r = ToolRegistry()
    r.register(t)
    assert "BLOQUEADO" in run(r.execute("read_file", {"path": "ok"}))
    r = ToolRegistry(enable_workspace_file_tools=True)
    r.register(t)
    assert "disabled" in run(r.execute("read_file", {"path": "ok"}))
    assert "Error" in run(r.execute("read_file", {"path": "link"}))
    unbounded = ReadFileTool(workspace=workspace, allowed_dir=None)
    r.register(unbounded)
    assert "BLOQUEADO" in run(r.execute("read_file", {"path": "ok"}))


def test_shell_never_invokes_subprocess():
    async def check():
        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as subprocess:
            for command in ("echo hello", "$(cat /etc/passwd)", "python -c 'print(1)'", "cat /etc/passwd"):
                result = await ExecTool(restrict_to_workspace=True).execute(command, working_dir="/tmp")
                assert "blocked" in result
            subprocess.assert_not_awaited()
    run(check())
