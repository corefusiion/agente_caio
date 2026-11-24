import io
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from unittest.mock import patch

from scripts import caio_diagnostico as diag


class DiagnosticTests(unittest.TestCase):
    def test_requisitos_normalizados_sem_duplicatas(self):
        self.assertEqual(
            diag.requirement_names(["python-dotenv>=1", "python_dotenv>=2", "Foo.Bar[x]>=3"]),
            ["foo-bar", "python-dotenv"],
        )
        with self.assertRaises(ValueError):
            diag.requirement_names(["!invalid"])

    def test_le_pyproject_real_sem_opcionais(self):
        names = diag.read_requirements(diag.ROOT)
        self.assertIn("fastapi", names)
        self.assertIn("litellm", names)
        self.assertNotIn("pytest", names)
        self.assertEqual(names.count("python-dotenv"), 1)

    def test_presenca_ausencia_e_erro_sem_importar_pacotes(self):
        def metadata(name):
            if name == "absent":
                raise diag.importlib.metadata.PackageNotFoundError(name)
            if name == "broken":
                raise OSError("segredo não deve ser impresso")
            return "1"
        with patch.object(diag.importlib.metadata, "version", side_effect=metadata):
            self.assertEqual(diag.check_dependencies(["ok", "absent", "broken"]),
                             (["ok"], ["absent"], ["broken"]))

    def test_path_so_mostra_presenca_e_nao_executa_ferramentas(self):
        with patch.object(diag.shutil, "which", return_value="/caminho/pessoal/bin/programa"), \
             patch("subprocess.run", side_effect=AssertionError("executou")), \
             patch("subprocess.Popen", side_effect=AssertionError("executou")), \
             patch("socket.socket", side_effect=AssertionError("rede")), \
             patch.object(diag, "read_requirements", return_value=["fake"]), \
             patch.object(diag, "check_dependencies", return_value=([], ["fake"], [])):
            out = io.StringIO()
            with redirect_stdout(out):
                code = diag.main()
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn("/caminho/pessoal", text)
        self.assertIn("fake", text)
        self.assertIn("não certifica", text)

    def test_windows_com_python_antigo_e_outro_sistema(self):
        version = namedtuple("Version", "major minor micro")(3, 10, 5)
        for platform, system in (("win32", "Windows"), ("darwin", "outro")):
            with self.subTest(platform=platform), patch.object(diag.sys, "platform", platform), \
                 patch.object(diag.sys, "version_info", version), \
                 patch.object(diag, "check_tools", return_value={}), \
                 patch.object(diag, "read_requirements", side_effect=AssertionError("não deveria ler")):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(diag.main(), 0)
                self.assertIn("Sistema: " + system, out.getvalue())
                self.assertIn("não atendido", out.getvalue())
                self.assertIn("não verificadas", out.getvalue())

    def test_falha_na_leitura_sem_vazar_excecao(self):
        with patch.object(diag, "read_requirements", side_effect=OSError("caminho privado")), \
             patch.object(diag, "check_tools", return_value={}):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(diag.main(), 1)
            self.assertIn("não foi possível ler", out.getvalue())
            self.assertNotIn("caminho privado", out.getvalue())

    def test_erro_no_path_nao_e_tratado_como_ausencia(self):
        with patch.object(diag.shutil, "which", side_effect=OSError("privado")):
            self.assertTrue(all(v is None for v in diag.check_tools().values()))


if __name__ == "__main__":
    unittest.main()
