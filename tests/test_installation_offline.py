"""Offline checks for declared dependencies and fail-closed installers."""
import os
from pathlib import Path
import shutil
import subprocess
import tomllib

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def test_core_dependencies():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = [Requirement(item) for item in project["dependencies"]]
    names = [canonicalize_name(req.name) for req in reqs]
    for name in ("reportlab", "python-pptx", "openpyxl", "aiohttp", "openai", "python-dotenv", "python-multipart"):
        assert names.count(name) == 1
    multipart = next(req for req in reqs if req.name == "python-multipart")
    assert "0.0.26" not in multipart.specifier
    assert "0.0.27" in multipart.specifier
    assert "0.0.29" in multipart.specifier
    assert "1.0.0" not in multipart.specifier


def make_executable(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def run_shell(tmp_path, fail="", existing=False, example=True):
    project = tmp_path / "project"
    project.mkdir()
    shutil.copy(ROOT / "install.sh", project / "install.sh")
    if example:
        (project / "config.example.json").write_text("example", encoding="utf-8")
    if existing:
        (project / "config.json").write_text("existing", encoding="utf-8")
    # Stub Python/venv/pip/setup: no network, packages or real config touched.
    bin_dir = tmp_path / "bin"
    make_executable(bin_dir / "python3", '''#!/bin/sh
if [ "$1" = "-c" ]; then [ "$FAIL" != python_version ]; exit $?; fi
if [ "$FAIL" = venv ]; then exit 42; fi
mkdir -p .venv/bin
cp "$STUB_DIR/venv-python" .venv/bin/python
cp "$STUB_DIR/caio" .venv/bin/caio
''')
    make_executable(bin_dir / "venv-python", '''#!/bin/sh
if [ "$1" = "-c" ]; then [ "$FAIL" != venv_version ]; exit $?; fi
case "$*" in
  *--upgrade*) [ "$FAIL" != pip_upgrade ] ;;
  *install*'-e .'*) [ "$FAIL" != pip_install ] ;;
  *) exit 43 ;;
esac
''')
    make_executable(bin_dir / "caio", '''#!/bin/sh
[ "$FAIL" != setup ]
''')
    env = os.environ.copy()
    env.update(PATH=str(bin_dir) + os.pathsep + os.defpath, STUB_DIR=str(bin_dir), FAIL=fail)
    result = subprocess.run(["bash", str(project / "install.sh")], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=10)
    return project, result


@pytest.mark.parametrize("failure", ["python_version", "venv", "venv_version", "pip_upgrade", "pip_install", "setup"])
def test_shell_fails_closed(tmp_path, failure):
    project, result = run_shell(tmp_path, fail=failure)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Instalação Concluída" not in result.stdout
    if failure == "setup":
        assert (project / "config.json").read_text() == "example"
        assert "instalador novamente" in result.stderr
        assert (project / ".caio-setup-pending").is_file()
    else:
        assert not (project / "config.json").exists()


def test_shell_copy_failure(tmp_path):
    _, result = run_shell(tmp_path, example=False)
    assert result.returncode != 0
    assert "Instalação Concluída" not in result.stdout


def test_shell_success_and_existing_config(tmp_path):
    project, result = run_shell(tmp_path, existing=True, fail="setup")
    assert result.returncode == 0, result.stderr
    assert "Instalação Concluída" in result.stdout
    assert (project / "config.json").read_text() == "existing"


def test_shell_success_new_config(tmp_path):
    project, result = run_shell(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (project / "config.json").read_text() == "example"


def test_shell_setup_retries_until_success_without_overwriting_config(tmp_path):
    project, first = run_shell(tmp_path, fail="setup")
    assert first.returncode != 0
    config = project / "config.json"
    config.write_text("user changes", encoding="utf-8")
    marker = project / ".caio-setup-pending"
    assert marker.is_file()
    bin_dir = tmp_path / "bin"
    env = os.environ.copy()
    env.update(PATH=str(bin_dir) + os.pathsep + os.defpath, STUB_DIR=str(bin_dir), FAIL="setup")
    second = subprocess.run(["bash", str(project / "install.sh")], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=10)
    assert second.returncode != 0
    assert "Instalação Concluída" not in second.stdout
    assert marker.is_file()
    assert config.read_text() == "user changes"
    env["FAIL"] = ""
    third = subprocess.run(["bash", str(project / "install.sh")], cwd=tmp_path, env=env,
                           text=True, capture_output=True, timeout=10)
    assert third.returncode == 0, third.stderr
    assert not marker.exists()
    assert config.read_text() == "user changes"


def test_shell_config_directory_fails_without_copying_into_it(tmp_path):
    project, first = run_shell(tmp_path, fail="setup")
    assert first.returncode != 0
    (project / "config.json").unlink()
    (project / "config.json").mkdir()
    bin_dir = tmp_path / "bin"
    env = os.environ.copy()
    env.update(PATH=str(bin_dir) + os.pathsep + os.defpath, STUB_DIR=str(bin_dir), FAIL="")
    result = subprocess.run(["bash", str(project / "install.sh")], cwd=tmp_path, env=env,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert "Instalação Concluída" not in result.stdout
    assert not (project / "config.json" / "config.example.json").exists()


def test_windows_installer_failure_guards_static():
    # On non-Windows this checks structure only, not cmd.exe semantics.
    lines = (ROOT / "install.bat").read_text(encoding="utf-8").splitlines()
    steps = ("python -m venv .venv", "-m pip install --upgrade pip", "-m pip install -e .", "copy config.example.json config.json", "caio.exe setup")
    for step in steps:
        index = next(i for i, line in enumerate(lines) if step in line)
        assert lines[index + 1].strip().lower().startswith("if errorlevel 1 goto :")
    assert ":error" in lines
    assert "exit /b 1" in lines
    assert any("instalador novamente" in line for line in lines)
    text = "\n".join(lines).lower()
    assert text.index("type nul > .caio-setup-pending") < text.index("copy config.example.json config.json")
    assert text.index("if exist \".caio-setup-pending\"") < text.index("caio.exe setup") < text.index("del .caio-setup-pending")
    assert 'if exist "config.json\\nul"' in text
