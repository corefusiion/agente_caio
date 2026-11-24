"""Offline regression and adversarial tests for restricted skill reading."""
import asyncio
import os
from pathlib import Path

import pytest

from caiocore.agent.tools.registry import ToolRegistry
from caiocore.agent.tools.shell import ExecTool
from caiocore.agent.tools import skill_read
from caiocore.agent.tools.skill_read import ReadSkillTool, SafeSkillsLoader


def run(coro):
    return asyncio.run(coro)


def put(root, name, text):
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(text, encoding="utf-8")


@pytest.fixture
def roots(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    monkeypatch.setattr(skill_read, "BUILTIN_SKILLS_DIR", builtin)
    return workspace, builtin


def test_named_builtin_and_workspace_precedence(roots):
    workspace, builtin = roots
    put(builtin, "builtin-skill", "built in")
    put(builtin, "shared", "built in version")
    put(workspace / "skills", "shared", "local version")
    tool = ReadSkillTool(workspace)
    reg = ToolRegistry()
    reg.register(tool)
    assert run(reg.execute("read_skill", {"name": "builtin-skill"})) == "built in"
    assert run(reg.execute("read_skill", {"name": "shared"})) == "local version"
    assert run(reg.execute("read_skill", {"name": "missing"})).startswith("Error")
    assert run(reg.execute("read_skill", {"name": "shared", "path": "/etc/passwd"})).startswith("Error")


@pytest.mark.parametrize("name", ["../secret", "/etc/passwd", "x/SKILL.md", "..", ".", "x\\SKILL.md", "", "-x", "x\x00y"])
def test_no_arbitrary_paths(roots, name):
    workspace, _ = roots
    assert run(ReadSkillTool(workspace).execute(name)).startswith("Error")


def test_real_packaged_skill(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    result = run(ReadSkillTool(workspace).execute("github"))
    assert "Error: skill not found" not in result
    assert len(result) > 50


def test_symlinks_and_hardlinks_blocked(roots, tmp_path):
    workspace, builtin = roots
    secret = tmp_path / "secret"
    secret.write_text("private secret")
    skills = workspace / "skills"
    skills.mkdir()
    (skills / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    (skills / "linked-file").mkdir()
    (skills / "linked-file" / "SKILL.md").symlink_to(secret)
    (skills / "hardlink").mkdir()
    os.link(secret, skills / "hardlink" / "SKILL.md")
    put(builtin, "linked-file", "builtin should not bypass unsafe local entry")
    tool = ReadSkillTool(workspace)
    loader = SafeSkillsLoader(workspace)
    for name in ("linked-dir", "linked-file", "hardlink"):
        assert run(tool.execute(name)).startswith("Error")
        assert loader.load_skill(name) is None
    names = {s["name"] for s in loader.list_skills(False)}
    assert not names.intersection({"linked-dir", "linked-file", "hardlink"})


def test_context_summary_and_always_loading_do_not_follow_symlinks(roots, tmp_path):
    from caiocore.agent.loop import SafeSkillContextBuilder
    workspace, _ = roots
    secret = tmp_path / "secret"
    secret.write_text('---\nalways: true\ndescription: TOP_SECRET\n---\nPRIVATE_CONTENT')
    skill_dir = workspace / "skills" / "evil"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").symlink_to(secret)
    prompt = SafeSkillContextBuilder(workspace).build_system_prompt()
    assert "TOP_SECRET" not in prompt
    assert "PRIVATE_CONTENT" not in prompt
    assert "<name>evil</name>" not in prompt


def test_root_symlink_blocked(roots, tmp_path):
    workspace, _ = roots
    outside = tmp_path / "outside"
    put(outside, "secret", "outside content")
    (workspace / "skills").symlink_to(outside, target_is_directory=True)
    assert run(ReadSkillTool(workspace).execute("secret")).startswith("Error")
    assert "secret" not in {s["name"] for s in SafeSkillsLoader(workspace).list_skills(False)}


@pytest.mark.parametrize("part", ["directory", "file"])
def test_swapping_for_symlink_before_open_is_blocked(roots, tmp_path, monkeypatch, part):
    workspace, _ = roots
    local = workspace / "skills"
    put(local, "candidate", "safe")
    outside = tmp_path / "outside"
    put(outside, "candidate", "private")
    original = skill_read._open_directory
    swapped = False

    def swap(root):
        nonlocal swapped
        fd = original(root)
        if root == local and not swapped:
            swapped = True
            if part == "directory":
                (local / "candidate").rename(local / "old")
                (local / "candidate").symlink_to(outside / "candidate", target_is_directory=True)
            else:
                (local / "candidate" / "SKILL.md").unlink()
                (local / "candidate" / "SKILL.md").symlink_to(outside / "candidate" / "SKILL.md")
        return fd

    monkeypatch.setattr(skill_read, "_open_directory", swap)
    assert run(ReadSkillTool(workspace).execute("candidate")).startswith("Error")


def test_limits_regular_file_and_platform_fail_closed(roots, monkeypatch):
    workspace, _ = roots
    put(workspace / "skills", "big", "x" * (skill_read._MAX_BYTES + 1))
    assert run(ReadSkillTool(workspace).execute("big")).startswith("Error")
    monkeypatch.setattr(skill_read.os, "supports_dir_fd", set())
    assert run(ReadSkillTool(workspace).execute("big")).startswith("Error")


def test_safe_context_and_default_registration(tmp_path):
    from caiocore.agent.loop import AgentLoop, SafeSkillContextBuilder
    from caiocore.bus.queue import MessageBus
    from unittest.mock import Mock
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    context = SafeSkillContextBuilder(workspace)
    prompt = context.build_system_prompt()
    assert "chame read_skill com o nome" in prompt
    assert "usando a tool read_file" not in prompt
    provider = Mock()
    provider.get_default_model.return_value = "test"
    loop = AgentLoop(bus=MessageBus(), provider=provider, workspace=workspace)
    assert isinstance(loop.context.skills, SafeSkillsLoader)
    assert loop.tools.has("read_skill")
    assert "Error: skill not found" not in run(loop.tools.execute("read_skill", {"name": "github"}))
    assert not loop.tools.has("exec")
    assert not loop.tools.has("git")
    assert "blocked" in run(ExecTool().execute("git status"))
