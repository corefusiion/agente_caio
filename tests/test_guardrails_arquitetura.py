"""Executable security boundaries; no real network, Git, shell, or approvals.

The reviewed policy is intentionally independent of the implementation. A policy
change must be reviewed together with the isolation/approval design, not merely
accepted because the registry itself says it is allowed.
"""
import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from caiocore.agent.loop import AgentLoop
from caiocore.agent.tools.base import Tool
from caiocore.agent.tools.filesystem import ListDirTool, ReadFileTool
from caiocore.agent.tools.registry import ToolRegistry
from caiocore.agent.tools.sandbox import SandboxExecTool
from caiocore.agent.tools.shell import ExecTool
from caiocore.bus.queue import MessageBus
from caiocore.config.schema import Config


REVIEWED_READS = {
    "web_search": ("web", "WebSearchTool"),
    "get_weather": ("weather", "WeatherTool"),
    "email_read": ("email_read", "EmailReadTool"),
    "get_system_status": ("system_status", "SystemStatusTool"),
    "read_skill": ("skill_read", "ReadSkillTool"),
}
REVIEWED_FILE_READS = {
    "read_file": ("filesystem", "ReadFileTool"),
    "list_dir": ("filesystem", "ListDirTool"),
}


def run(coro):
    return asyncio.run(coro)


def test_reviewed_registry_policy_cannot_expand_silently():
    assert ToolRegistry._READ_ONLY == REVIEWED_READS
    assert ToolRegistry._FILE_READ_ONLY == REVIEWED_FILE_READS
    assert Config().tools.enable_workspace_file_tools is False


class Impostor(Tool):
    description = "inert test tool"
    parameters = {"type": "object", "properties": {}}

    def __init__(self, name):
        self._name = name
        self.called = False

    @property
    def name(self):
        return self._name

    async def execute(self, **kwargs):
        self.called = True
        return "UNAUTHORIZED_EXECUTION"


@pytest.mark.parametrize("name", [
    *REVIEWED_READS, *REVIEWED_FILE_READS, "unknown", "exec", "sandbox_exec",
    "git", "git_status", "git_diff", "git_log", "git_push", "message",
    "email_send", "email_delete", "web_fetch", "mcp_anything", "write_file",
])
@pytest.mark.parametrize("opt_in", [False, True])
def test_injected_tool_even_using_reviewed_name_never_runs(name, opt_in):
    registry = ToolRegistry(enable_workspace_file_tools=opt_in)
    tool = Impostor(name)
    registry.register(tool)
    for params in ({}, {"approved": True}, {"autonomous": True}):
        response = run(registry.execute(name, params))
        assert response.startswith("BLOQUEADO:"), (name, response)
    assert not tool.called


def valid_params(tool):
    """Build valid inert values from each real tool's required parameter schema."""
    schema = tool.parameters
    values = {"string": "guardrail-test", "integer": 1, "number": 1,
              "boolean": False, "array": [], "object": {}}
    params = {}
    for name in schema.get("required", []):
        prop = schema.get("properties", {}).get(name, {})
        params[name] = prop.get("enum", [values.get(prop.get("type"), "guardrail-test")])[0]
    return params


@pytest.mark.parametrize("opt_in", [False, True])
def test_real_agent_registration_and_dispatch_enforce_reviewed_boundary(tmp_path, opt_in):
    config = Config.model_validate({"tools": {"enableWorkspaceFileTools": opt_in},
                                    "agents": {"defaults": {"autonomous": True}}})
    provider = Mock()
    provider.get_default_model.return_value = "offline-test"
    agent = AgentLoop(MessageBus(), provider, tmp_path, config=config,
                      mcp_servers={"untrusted": {"command": "never-run"}},
                      restrict_to_workspace=False)
    assert agent._mcp_servers == {}
    assert not ({"exec", "sandbox_exec", "git", "git_status", "git_diff", "git_log"} & set(agent.tools.tool_names))
    assert ("read_file" in agent.tools) is opt_in
    assert ("list_dir" in agent.tools) is opt_in

    # Exercise actual dispatch, with each real tool's execute replaced by an
    # inert spy: a regression cannot contact a service or mutate the machine.
    for name in agent.tools.tool_names:
        tool = agent.tools.get(name)
        allowed = name in REVIEWED_READS or (opt_in and name in REVIEWED_FILE_READS)
        with patch.object(tool, "execute", new_callable=AsyncMock, return_value="SPY_CALLED") as spy:
            result = run(agent.tools.execute(name, valid_params(tool)))
            if allowed:
                assert result == "SPY_CALLED", (name, result)
                spy.assert_awaited_once()
            else:
                assert result.startswith("BLOQUEADO:"), (name, result)
                spy.assert_not_awaited()


@pytest.mark.parametrize("cls", [ReadFileTool, ListDirTool])
def test_workspace_opt_in_does_not_enable_actual_file_access(tmp_path, cls):
    (tmp_path / "data").write_text("SYNTHETIC_SECRET", encoding="utf-8")
    tool = cls(workspace=tmp_path, allowed_dir=tmp_path)
    for enabled in (False, True):
        registry = ToolRegistry(enable_workspace_file_tools=enabled)
        registry.register(tool)
        # Spy on the real operation and still run it: enabling registration is
        # not proof of filesystem containment; the implementation must fail closed.
        with patch.object(tool, "execute", wraps=tool.execute) as execute:
            result = run(registry.execute(tool.name, {"path": "data"}))
            if enabled:
                execute.assert_awaited_once()
                assert "disabled" in result
            else:
                execute.assert_not_awaited()
                assert result.startswith("BLOQUEADO:")
            assert "SYNTHETIC_SECRET" not in result


@pytest.mark.parametrize("command", ["echo guardrail", "git status", "git -C /tmp log", "python -c pass"])
def test_direct_shell_cannot_spawn_process(command):
    with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as shell, \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as exec_, \
         patch("subprocess.Popen", side_effect=AssertionError("process blocked")) as popen:
        result = run(ExecTool(restrict_to_workspace=False).execute(command))
        assert "blocked" in result.lower()
        shell.assert_not_awaited()
        exec_.assert_not_awaited()
        popen.assert_not_called()


def test_real_shell_and_docker_tools_denied_by_registry_even_if_injected(tmp_path):
    registry = ToolRegistry(enable_workspace_file_tools=True)
    for tool in (ExecTool(), SandboxExecTool(tmp_path)):
        registry.register(tool)
        with patch.object(tool, "execute", new_callable=AsyncMock, return_value="UNAUTHORIZED") as spy:
            result = run(registry.execute(tool.name, {"command": "git status", "approved": True}))
            assert result.startswith("BLOQUEADO:")
            spy.assert_not_awaited()
