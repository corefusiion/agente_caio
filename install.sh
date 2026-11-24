#!/bin/bash
set -Ee
trap 'echo "[ERRO] Instalação interrompida. Corrija o erro acima e tente novamente." >&2' ERR

cd -- "$(dirname -- "$0")"
echo "🐱 CaioCore - Iniciando Instalação Automática (Linux/VPS) ..."

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERRO] Python 3.11+ não encontrado. Instale também o pacote de venv." >&2
    exit 1
fi
if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "[ERRO] É necessário Python 3.11 ou superior." >&2
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "[1/3] Criando ambiente virtual .venv ..."
    python3 -m venv .venv
fi

# Use sempre o Python do ambiente virtual, mesmo se outro venv estiver ativo.
if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    echo "[ERRO] .venv inválido ou com Python anterior à versão 3.11." >&2
    exit 1
fi
echo "[2/3] Instalando dependências ..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .

# Preserve a configuração e registre setup pendente antes de criar uma nova.
# O marcador permite retomar após falha sem sobrescrever config.json.
if [ -e "config.json" ] && [ ! -f "config.json" ]; then
    echo "[ERRO] config.json existe, mas não é um arquivo comum." >&2
    exit 1
fi
if [ ! -f "config.json" ]; then
    echo "[3/3] Criando arquivo de configuração inicial ..."
    touch .caio-setup-pending
    cp -n config.example.json config.json
fi
if [ -e .caio-setup-pending ]; then
    if ! .venv/bin/caio setup; then
        echo "[ERRO] Setup falhou. Configuração preservada; execute o instalador novamente." >&2
        exit 1
    fi
    rm -- .caio-setup-pending
else
    echo "[3/3] Configuração já existente encontrada."
fi

echo ""
echo "✅ Instalação Concluída!"
echo "Para iniciar o agente, use: source .venv/bin/activate && caio gateway"
