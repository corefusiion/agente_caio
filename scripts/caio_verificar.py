#!/usr/bin/env python3
"""Verificações locais selecionadas; não substituem isolamento contra código hostil."""
from __future__ import annotations

import ctypes
import errno
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GROUPS = {
    "verificador": ["tests/test_caio_verificar.py"],
    "guardas-arquitetura": ["tests/test_guardrails_arquitetura.py", "tests/test_guardrails_git.py"],
    "seguranca": [
        "tests/test_security_api_boundary.py", "tests/test_security_boundary_fixes_oauth.py",
        "tests/test_security_channels_whatsapp.py", "tests/test_security_git_skills.py",
        "tests/test_security_tools.py", "tests/test_security_tools_approval.py",
        "tests/test_security_workspace_fail_closed.py", "tests/test_git_fail_closed.py",
        "tests/test_document_download_security.py", "tests/test_document_download_security_race.py",
        "tests/test_document_download_security_response.py",
    ],
    # Os outros casos de proxy precisam de sockets TCP de loopback: não rodamos
    # com a política de rede desta ferramenta. Ver documentação.
    "proxy": ["tests/test_interface_secure_proxy.py::test_startup_fails_closed"],
    "github-somente-leitura": ["tests/test_github_readonly.py", "tests/test_github_readonly_adversarial.py", "tests/test_github_readonly_transport.py"],
    "bem-estar-loja": ["tests/test_wellbeing.py", "tests/test_shopping_mercado_livre.py", "tests/test_shopping_timeout.py"],
}


def restrict_network():
    """Linux seccomp: permite criar sockets apenas AF_UNIX, inclusive em filhos."""
    numbers = {"x86_64": (41, 53), "aarch64": (198, 199)}
    if sys.platform != "linux" or platform.machine() not in numbers:
        raise RuntimeError("isolamento seccomp indisponível")
    machine = platform.machine()
    nr_socket, nr_pair = numbers[machine]
    arch = {"x86_64": 0xC000003E, "aarch64": 0xC00000B7}[machine]
    # struct seccomp_data: nr em 0, arquitetura em 4 e argumento 0 em 16.
    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]
    class Program(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]
    deny = 0x00050000 | errno.EACCES
    ins = (Filter * 19)(
        Filter(0x20, 0, 0, 4),                 # arquitetura nativa obrigatória
        Filter(0x15, 1, 0, arch),
        Filter(0x06, 0, 0, 0x80000000),        # KILL_PROCESS
        Filter(0x20, 0, 0, 0),                 # LD syscall
        Filter(0x45, 0, 1, 0x40000000),        # bloqueia ABI x32
        Filter(0x06, 0, 0, deny),
        Filter(0x15, 0, 1, 425),               # io_uring_setup
        Filter(0x06, 0, 0, deny),
        Filter(0x15, 0, 1, 426),               # io_uring_enter
        Filter(0x06, 0, 0, deny),
        Filter(0x15, 0, 1, 427),               # io_uring_register
        Filter(0x06, 0, 0, deny),
        Filter(0x15, 1, 0, nr_socket),          # socket -> verificar domínio
        Filter(0x15, 0, 3, nr_pair),            # socketpair -> verificar; outros -> allow
        Filter(0x20, 0, 0, 16),                # LD domínio
        Filter(0x15, 1, 0, socket.AF_UNIX),      # AF_UNIX -> allow
        Filter(0x06, 0, 0, deny),
        Filter(0x06, 0, 0, 0x7fff0000),        # ALLOW
        Filter(0x06, 0, 0, deny),              # inatingível, mantenha saltos explícitos
    )
    prog = Program(len(ins), ins)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.prctl(22, 2, ctypes.byref(prog), 0, 0) != 0:
        raise RuntimeError("falha ao ativar seccomp")
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            s = socket.socket(family, socket.SOCK_STREAM)
        except OSError as exc:
            if exc.errno != errno.EACCES:
                raise RuntimeError("isolamento não verificado") from None
        else:
            s.close()
            raise RuntimeError("isolamento não verificado")
    a, b = socket.socketpair()
    a.close()
    b.close()


def clean_env(home):
    # Sem tokens, variáveis Python, configurações Git, proxies ou plugins do usuário.
    sites = [p for p in sys.path if p and Path(p).is_absolute() and Path(p).name in ("site-packages", "dist-packages")]
    return {"PATH": os.defpath, "HOME": home, "TMPDIR": home,
            "PYTHONPATH": os.pathsep.join([str(ROOT), *sites]),
            "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONNOUSERSITE": "1",
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}


def pytest_child(group):
    restrict_network()
    import pytest
    class Results:
        session = None
        def pytest_sessionfinish(self, session, exitstatus):
            self.session = session
    plugin = Results()
    # Saída completa é descartada pelo processo pai; nunca mostrada ao usuário.
    result = int(pytest.main(["-q", "-p", "no:cacheprovider", *GROUPS[group]], plugins=[plugin]))
    if plugin.session is None:
        return {"ok": False}
    stats = plugin.session.config.pluginmanager.get_plugin("terminalreporter").stats
    counts = {key: len(stats.get(key, [])) for key in ("passed", "failed", "error", "skipped", "xfailed", "xpassed")}
    collected = plugin.session.testscollected
    ok = result == 0 and collected > 0 and counts["passed"] == collected and not any(counts[k] for k in ("failed", "error", "skipped", "xfailed", "xpassed"))
    return {"ok": ok, "collected": collected, **counts}


def run_child(args, env, timeout=180):
    try:
        p = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), *args],
                           cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                           close_fds=True)
        if p.returncode != 0:
            return {"ok": False}
        # Apenas objeto gerado por nós, com valores estritamente tipados e limitados.
        data = json.loads(p.stdout.decode("utf-8").splitlines()[-1])
        if not isinstance(data, dict) or not isinstance(data.get("ok"), bool):
            return {"ok": False}
        return data
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError, UnicodeError):
        return {"ok": False}


def main():
    with tempfile.TemporaryDirectory(prefix="caio-verificar-") as tmp:
        env = clean_env(tmp)
        stages = [("git diff --check", ["--_git", "worktree"]),
                  ("git diff --cached --check", ["--_git", "index"])]
        stages += [(name, ["--_pytest", name]) for name in GROUPS]
        failed = False
        for name, args in stages:
            if failed:
                print(f"{name}: NÃO EXECUTADO")
                continue
            result = run_child(args, env)
            if result.get("ok") is True:
                count = result.get("passed")
                suffix = f" ({count} testes)" if type(count) is int and 0 < count < 100000 else ""
                print(f"{name}: OK{suffix}", flush=True)
            else:
                print(f"{name}: FALHOU (saída detalhada suprimida)", flush=True)
                failed = True
        print("Resultado: " + ("FALHOU" if failed else "OK — escopo limitado; leia docs/VERIFICACAO_AUTOMATICA.md"))
        return int(failed)


def child():
    if len(sys.argv) == 3 and sys.argv[1] == "--_pytest" and sys.argv[2] in GROUPS:
        result = pytest_child(sys.argv[2])
    elif len(sys.argv) == 3 and sys.argv[1] == "--_git" and sys.argv[2] in ("worktree", "index"):
        restrict_network()
        git = shutil.which("git", path=os.environ.get("PATH"))
        if not git:
            return 1
        cmd = [git, "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "diff", "--no-ext-diff", "--no-textconv", "--ignore-submodules", "--check"]
        if sys.argv[2] == "index":
            cmd.append("--cached")
        p = subprocess.run(cmd, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        result = {"ok": p.returncode == 0}
    else:
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() if len(sys.argv) == 1 else child())
    except Exception:
        # Não ecoar erros, variáveis, caminhos, saída de subprocessos ou segredos.
        print('{"ok":false}' if len(sys.argv) != 1 else "Verificação falhou antes de concluir.")
        sys.exit(1)
