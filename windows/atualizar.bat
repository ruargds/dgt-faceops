@echo off
REM DGT FaceOps - atualiza para a versao mais recente
REM
REM Preserva .env, banco, backups e gravacoes. Reconstroi as imagens.
title DGT FaceOps - Atualizar
cd /d "%~dp0.."

echo ====================================================
echo   DGT FaceOps - atualizacao
echo ====================================================
echo.

docker info >nul 2>&1
if %errorLevel% neq 0 (
    echo   O Docker Desktop nao esta rodando. Abra e tente de novo.
    pause
    exit /b 1
)

if not exist ".env" (
    echo   .env nao encontrado. Rode instalar.bat primeiro.
    pause
    exit /b 1
)

REM O .env guarda a SECRET_KEY, da qual deriva o cofre das chaves SSH.
REM Perder esse arquivo torna todas as credenciais guardadas ilegiveis.
echo [1/4] Copia de seguranca do .env...
copy /y ".env" ".env.backup" >nul
echo       OK

echo [2/4] Buscando a versao mais recente...
where git >nul 2>&1
if %errorLevel% equ 0 (
    git pull --ff-only
) else (
    echo       git nao encontrado - usando o codigo local
)

echo [3/4] Reconstruindo as imagens...
docker compose build
if %errorLevel% neq 0 (
    echo.
    echo   O build falhou. Nada foi trocado - o painel antigo continua no ar.
    pause
    exit /b 1
)

echo [4/4] Reiniciando os servicos...
docker compose up -d --remove-orphans

echo.
echo   Atualizado. As migracoes de banco rodam sozinhas na subida.
echo.
for /f "tokens=2 delims==" %%p in ('findstr /b "PORTA_HTTP=" .env') do set PORTA=%%p
if "%PORTA%"=="" set PORTA=8080
echo   http://localhost:%PORTA%
echo.
pause
