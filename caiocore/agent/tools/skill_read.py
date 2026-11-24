"""Read only named SKILL.md files without following filesystem symlinks.

This intentionally does not provide a general file reader. POSIX dir-fd and
O_NOFOLLOW are required; unsupported platforms fail closed.
"""
import os
import re
import stat
from pathlib import Path
from typing import Any

from caiocore.agent.skills import BUILTIN_SKILLS_DIR, SkillsLoader
from caiocore.agent.tools.base import Tool

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z", re.ASCII)
_MAX_BYTES = 256 * 1024


def _open_directory(path: Path) -> int:
    """Open each component, refusing symlinks, even in ancestors of the root."""
    if os.name != "posix" or not all(hasattr(os, flag) for flag in ("O_NOFOLLOW", "O_DIRECTORY")) or os.open not in os.supports_dir_fd:
        raise OSError("secure directory operations unavailable")
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open("/", flags)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _read_in_root(root: Path, name: str) -> str:
    directory_fd = _open_directory(root)
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        skill_fd = os.open(name, flags, dir_fd=directory_fd)
        try:
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
            file_fd = os.open("SKILL.md", flags, dir_fd=skill_fd)
            try:
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > _MAX_BYTES:
                    raise PermissionError("unsafe skill file")
                with os.fdopen(file_fd, "rb", closefd=False) as stream:
                    data = stream.read(_MAX_BYTES + 1)
                if len(data) > _MAX_BYTES:
                    raise PermissionError("skill file too large")
                return data.decode("utf-8", errors="replace")
            finally:
                os.close(file_fd)
        finally:
            os.close(skill_fd)
    finally:
        os.close(directory_fd)


class SafeSkillsLoader(SkillsLoader):
    """Safe reads also cover summary and automatically loaded skill content."""

    def __init__(self, workspace: Path):
        super().__init__(workspace, BUILTIN_SKILLS_DIR)

    def load_skill(self, name: str) -> str | None:
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            return None
        for root in (self.workspace_skills, self.builtin_skills):
            try:
                return _read_in_root(root, name)
            except FileNotFoundError:
                continue
            except (OSError, ValueError):
                # Do not fall back after a permission, symlink, or safety error.
                return None
        return None

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        skills = []
        seen = set()
        for root, source in ((self.workspace_skills, "workspace"), (self.builtin_skills, "builtin")):
            try:
                fd = _open_directory(root)
                try:
                    names = sorted(os.listdir(fd))
                finally:
                    os.close(fd)
            except OSError:
                continue
            for name in names:
                if name in seen or not _NAME.fullmatch(name):
                    continue
                try:
                    _read_in_root(root, name)
                except (OSError, ValueError):
                    # An unsafe workspace entry shadows a built-in of the same name.
                    seen.add(name)
                    continue
                seen.add(name)
                skills.append({"name": name, "path": str(root / name / "SKILL.md"), "source": source})
        if filter_unavailable:
            return [s for s in skills if self._check_requirements(self._get_skill_meta(s["name"]))]
        return skills


class ReadSkillTool(Tool):
    def __init__(self, workspace: Path):
        self._loader = SafeSkillsLoader(workspace)

    @property
    def name(self) -> str:
        return "read_skill"

    @property
    def description(self) -> str:
        return "Read the SKILL.md of a named workspace or built-in skill; no file paths."

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {"name": {"type": "string", "description": "Skill name, not a path"}}, "required": ["name"], "additionalProperties": False}

    async def execute(self, name: str, **kwargs: Any) -> str:
        if kwargs:
            return "Error: unexpected parameters"
        content = self._loader.load_skill(name)
        return content if content is not None else "Error: skill not found or unavailable"
