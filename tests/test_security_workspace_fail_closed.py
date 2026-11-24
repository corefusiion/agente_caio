"""Regression tests: filesystem read/list tools fail closed, including opt-in."""
import asyncio
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from caiocore.agent.tools.filesystem import ListDirTool, ReadFileTool
from caiocore.agent.tools.registry import ToolRegistry


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def paths(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("SYNTHETIC_PRIVATE_DATA")
    (work / "ordinary.txt").write_text("ordinary")
    (work / "nested").mkdir()
    (work / "file-link").symlink_to(secret)
    (work / "dir-link").symlink_to(outside, target_is_directory=True)
    os.link(secret, work / "hardlink.txt")
    return work, outside, secret


@pytest.mark.parametrize("name", ["ordinary.txt", "hardlink.txt", "file-link", "dir-link/secret.txt", "../outside/secret.txt", "nested", "missing"])
def test_read_never_returns_content_even_opted_in(paths, name):
    work, _, _ = paths
    tool = ReadFileTool(workspace=work, allowed_dir=work)
    reg = ToolRegistry(enable_workspace_file_tools=True)
    reg.register(tool)
    assert "disabled" in run(reg.execute("read_file", {"path": name}))
    assert "disabled" in run(tool.execute(name))


def test_single_link_still_has_untrusted_provenance(paths):
    work, _, secret = paths
    secret.unlink()
    assert (work / "hardlink.txt").stat().st_nlink == 1
    result = run(ReadFileTool(work, work).execute("hardlink.txt"))
    assert "disabled" in result and "SYNTHETIC_PRIVATE_DATA" not in result


@pytest.mark.parametrize("name", [".", "nested", "dir-link", "../outside", "missing", "ordinary.txt"])
def test_list_never_leaks_names_even_opted_in(paths, name):
    work, _, _ = paths
    tool = ListDirTool(workspace=work, allowed_dir=work)
    reg = ToolRegistry(enable_workspace_file_tools=True)
    reg.register(tool)
    result = run(reg.execute("list_dir", {"path": name}))
    assert "disabled" in result and "secret.txt" not in result
    assert "disabled" in run(tool.execute(name))


@pytest.mark.parametrize("tool_cls", [ReadFileTool, ListDirTool])
def test_direct_execution_does_not_resolve_or_open_any_path(paths, tool_cls):
    work, _, _ = paths
    tool = tool_cls(workspace=work, allowed_dir=work)
    # A swapped directory or symlink cannot win a race if there is no check/use.
    with patch("caiocore.agent.tools.filesystem._resolve_path", side_effect=AssertionError("resolved")), \
         patch.object(Path, "open", side_effect=AssertionError("opened")), \
         patch.object(Path, "iterdir", side_effect=AssertionError("listed")), \
         patch.object(Path, "stat", side_effect=AssertionError("statted")):
        assert "disabled" in run(tool.execute("dir-link/secret.txt"))


@pytest.mark.parametrize("tool_cls", [ReadFileTool, ListDirTool])
def test_default_registry_still_blocks(paths, tool_cls):
    work, _, _ = paths
    tool = tool_cls(workspace=work, allowed_dir=work)
    reg = ToolRegistry()
    reg.register(tool)
    assert "BLOQUEADO" in run(reg.execute(tool.name, {"path": "."}))
