@echo off
setlocal
pushd "%~dp0" || (echo [ERRO] Nao foi possivel acessar a pasta do instalador. & exit /b 1)
echo CaioCore - Iniciando Instalacao Automatica (Windows) ...

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERRO] Python nao encontrado. Instale Python 3.11 ou superior.
    goto :error
)
python -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 (
    echo [ERRO] E necessario Python 3.11 ou superior.
    goto :error
)

if not exist ".venv" (
    echo [1/3] Criando ambiente virtual .venv ...
    python -m venv .venv
    if errorlevel 1 goto :error
)

.venv\Scripts\python.exe -c "import sys; sys.exit(sys.version_info < (3, 11))"
if errorlevel 1 (
    echo [ERRO] .venv invalido ou com Python anterior a 3.11.
    goto :error
)
echo [2/3] Instalando dependencias ...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto :error
.venv\Scripts\python.exe -m pip install -e .
if errorlevel 1 goto :error

if exist "config.json\NUL" (
    echo [ERRO] config.json existe, mas nao e um arquivo comum.
    goto :error
)
if not exist "config.json" (
    echo [3/3] Criando arquivo de configuracao inicial ...
    type nul > .caio-setup-pending
    if errorlevel 1 goto :error
    copy config.example.json config.json >nul
    if errorlevel 1 goto :error
)
if exist ".caio-setup-pending" (
    .venv\Scripts\caio.exe setup
    if errorlevel 1 goto :setup_error
    del .caio-setup-pending
    if errorlevel 1 goto :error
) else (
    echo [3/3] Configuracao ja existente encontrada.
)

echo.
echo Instalacao Concluida!
echo Para iniciar o agente, use: .venv\Scripts\caio gateway
popd
exit /b 0

:setup_error
echo [ERRO] Setup falhou. Configuracao preservada; execute o instalador novamente.
:error
echo [ERRO] Instalacao interrompida. Corrija o erro acima e tente novamente.
popd
exit /b 1
