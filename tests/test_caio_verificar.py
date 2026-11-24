"""Testes autocontidos (unittest; não exigem pytest nem acesso à rede)."""
import contextlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "caio_verificar.py"
spec = importlib.util.spec_from_file_location("caio_verificar", SCRIPT)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


class VerifyTests(unittest.TestCase):
    def test_clean_env_never_inherits_secrets_or_plugin_settings(self):
        with mock.patch.dict(os.environ, {"SECRET_TOKEN": "segredo", "PYTEST_ADDOPTS": "--bad", "GIT_DIR": "/x"}):
            env = verify.clean_env("/tmp/home-ficticio")
        self.assertNotIn("SECRET_TOKEN", env)
        self.assertNotIn("PYTEST_ADDOPTS", env)
        self.assertNotIn("GIT_DIR", env)
        self.assertEqual(env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"], "1")

    def test_fail_closed_and_do_not_display_sensitive_output(self):
        calls = []
        def simulate(args, env, timeout=180):
            calls.append(args)
            return {"ok": len(calls) == 1, "secret": "nao-exibir-segredo"}
        out = io.StringIO()
        with mock.patch.object(verify, "run_child", side_effect=simulate), contextlib.redirect_stdout(out):
            self.assertEqual(verify.main(), 1)
        self.assertEqual(len(calls), 2)
        self.assertIn("NÃO EXECUTADO", out.getvalue())
        self.assertNotIn("nao-exibir-segredo", out.getvalue())

    def test_run_child_fails_closed_for_missing_executable_timeout_or_bad_json(self):
        with mock.patch.object(verify.subprocess, "run", side_effect=FileNotFoundError()):
            self.assertEqual(verify.run_child([], {}), {"ok": False})
        with mock.patch.object(verify.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)):
            self.assertEqual(verify.run_child([], {}), {"ok": False})
        for stdout, code in [(b"secret", 0), (b'{"ok":true}', 1), (b'{"ok":"yes"}', 0), (b'', 0)]:
            with mock.patch.object(verify.subprocess, "run", return_value=types.SimpleNamespace(returncode=code, stdout=stdout)):
                self.assertEqual(verify.run_child([], {}), {"ok": False})

    def test_pytest_skips_xfails_empty_and_failures_never_report_success(self):
        def fake_pytest(collected, categories, exitcode=0):
            def main(args, plugins):
                reporter = types.SimpleNamespace(stats={k: [object()] * v for k, v in categories.items()})
                manager = types.SimpleNamespace(get_plugin=lambda name: reporter)
                session = types.SimpleNamespace(testscollected=collected, config=types.SimpleNamespace(pluginmanager=manager))
                plugins[0].pytest_sessionfinish(session, exitcode)
                return exitcode
            return types.SimpleNamespace(main=main)
        scenarios = [(0, {}, 0), (2, {"passed": 1, "skipped": 1}, 0),
                     (2, {"passed": 1, "xfailed": 1}, 0),
                     (2, {"passed": 2, "error": 1}, 1), (1, {"passed": 1}, 0)]
        for i, (collected, stats, code) in enumerate(scenarios):
            with self.subTest(i=i), mock.patch.object(verify, "restrict_network"), mock.patch.dict(sys.modules, {"pytest": fake_pytest(collected, stats, code)}):
                self.assertEqual(verify.pytest_child("proxy")["ok"], i == 4)

    def test_real_seccomp_blocks_inet_and_is_inherited(self):
        # Filtrar no subprocesso, nunca no processo de unittest.
        code = '''import importlib.util, socket, subprocess, sys
s=importlib.util.spec_from_file_location("v",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
m.restrict_network()
for family in (socket.AF_INET, socket.AF_INET6):
    try: socket.socket(family, socket.SOCK_DGRAM)
    except PermissionError: pass
    else: raise AssertionError("INET permitido")
a,b=socket.socketpair(); a.close(); b.close()
p=subprocess.run([sys.executable,"-c", "import socket; socket.socket(socket.AF_INET)"], capture_output=True)
assert p.returncode != 0
'''
        with tempfile.TemporaryDirectory() as tmp:
            env = verify.clean_env(tmp)
            p = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPT)], env=env, capture_output=True, timeout=15)
        self.assertEqual(p.returncode, 0, "seccomp deve funcionar; não existe fallback sem rede")

    def test_invalid_group_and_missing_dependency_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = verify.clean_env(tmp)
            p = subprocess.run([sys.executable, "-B", str(SCRIPT), "--_pytest", "inexistente"],
                               env=env, capture_output=True, timeout=15)
            self.assertNotEqual(p.returncode, 0)
            env.pop("PYTHONPATH")
            p = subprocess.run([sys.executable, "-S", "-B", str(SCRIPT), "--_pytest", "proxy"],
                               env=env, capture_output=True, timeout=15)
            self.assertNotEqual(p.returncode, 0)

    def test_missing_test_path_fails_closed(self):
        # Simula a resposta do pytest para um arquivo ausente sem depender dele.
        def fake_main(args, plugins):
            self.assertIn("tests/arquivo-nao-existe.py", args)
            self.assertFalse((verify.ROOT / args[-1]).exists())
            reporter = types.SimpleNamespace(stats={})
            manager = types.SimpleNamespace(get_plugin=lambda name: reporter)
            session = types.SimpleNamespace(testscollected=0, config=types.SimpleNamespace(pluginmanager=manager))
            plugins[0].pytest_sessionfinish(session, 4)
            return 4
        fake = types.SimpleNamespace(main=fake_main)
        with mock.patch.object(verify, "restrict_network"), mock.patch.dict(sys.modules, {"pytest": fake}), mock.patch.dict(verify.GROUPS, {"proxy": ["tests/arquivo-nao-existe.py"]}):
            self.assertFalse(verify.pytest_child("proxy")["ok"])


if __name__ == "__main__":
    unittest.main()
