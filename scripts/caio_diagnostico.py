#!/usr/bin/env python3
"""Diagnóstico informativo local: não instala, executa ferramentas ou lê configuração."""
import importlib.metadata
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("git", "node", "npm", "docker")
NAME = re.compile(r"^\s*([A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*)")


def requirement_names(dependencies):
    """Nomes das dependências diretas, sem importar nem executar os pacotes."""
    names = set()
    for item in dependencies:
        if not isinstance(item, str):
            raise ValueError("dependência inválida")
        match = NAME.match(item)
        if not match:
            raise ValueError("dependência inválida")
        names.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
    return sorted(names)


def read_requirements(root):
    # Python < 3.11 pode executar o diagnóstico, mas não tem tomllib na stdlib.
    import tomllib
    with (root / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    return requirement_names(project["dependencies"])


def check_dependencies(names):
    present, missing, unknown = [], [], []
    for name in names:
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
        except Exception:
            # Não vazar caminhos ou dados da exceção nem reportar erro como ausência.
            unknown.append(name)
        else:
            present.append(name)
    return present, missing, unknown


def check_tools():
    result = {}
    for name in TOOLS:
        try:
            result[name] = bool(shutil.which(name))
        except (OSError, ValueError):
            result[name] = None
    return result


def main():
    print("Diagnóstico Caio — offline, somente leitura")
    system = "Windows" if sys.platform == "win32" else "Linux" if sys.platform.startswith("linux") else "outro"
    print("Sistema:", system, "(identificação, não certificação de suporte)")
    v = sys.version_info
    print("Python: {}.{}.{}".format(v.major, v.minor, v.micro))
    supported = (v.major, v.minor) >= (3, 11)
    print("Requisito declarado Python >=3.11:", "atendido" if supported else "não atendido")
    if v.major == 3 and v.minor > 12:
        print("Aviso: esta versão é mais recente que as versões classificadas no projeto; compatibilidade não comprovada.")
    if system == "outro":
        print("Aviso: Windows e Linux são os ambientes abordados na documentação; este sistema não foi avaliado.")

    for tool, found in check_tools().items():
        status = "encontrado no PATH" if found is True else "não encontrado no PATH" if found is False else "verificação indisponível"
        print("Ferramenta {}: {}".format(tool, status))
    print("Ferramentas não foram executadas; versão, funcionamento, Docker Compose e daemon não foram verificados.")

    ok = True
    if supported:
        try:
            names = read_requirements(ROOT)
        except (OSError, ValueError, KeyError, TypeError, ImportError):
            print("Dependências: não foi possível ler os requisitos declarados em pyproject.toml.")
            ok = False
        else:
            present, missing, unknown = check_dependencies(names)
            print("Dependências Python diretas: {} detectadas de {}.".format(len(present), len(names)))
            if missing:
                print("Não detectadas: " + ", ".join(missing))
            if unknown:
                print("Não foi possível verificar: " + ", ".join(unknown))
            print("Apenas presença por metadados no Python atual; versões, extras, dependências transitivas e funcionamento não foram verificados.")
    else:
        print("Dependências: não verificadas; requer Python 3.11+ para ler pyproject.toml com a biblioteca padrão.")

    print("Dashboard: lockfile {} e build {}.".format(
        "presente" if (ROOT / "dashboard" / "package-lock.json").is_file() else "ausente",
        "presente" if (ROOT / "dashboard" / "dist" / "index.html").is_file() else "ausente"))
    print("Presença de build não garante atualização ou funcionamento; módulos npm não foram validados.")
    print("Próximos passos e limites: docs/INSTALACAO_FACIL.md")
    print("Nenhuma credencial/configuração foi lida; sem instalação, rede ou execução de ferramentas.")
    print("Resultado informativo; não certifica segurança, disponibilidade ou prontidão para instalação.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
