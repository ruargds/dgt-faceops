@echo off
REM DGT FaceOps - sobe o painel
title DGT FaceOps - Iniciar
cd /d "%~dp0.."

docker info >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo   O Docker Desktop nao esta rodando.
    echo   Abra o Docker Desktop, espere ficar verde e tente de novo.
    echo.
    pause
    exit /b 1
)

echo Subindo o DGT FaceOps...
docker compose up -d

if %errorLevel% neq 0 (
    echo.
    echo   Falha ao subir. Veja o log:
    echo     docker compose logs --tail 60 backend
    echo.
    pause
    exit /b 1
)

echo.
echo   Pronto. O painel leva alguns segundos para responder.
echo.
for /f "tokens=2 delims==" %%p in ('findstr /b "PORTA_HTTP=" .env') do set PORTA=%%p
if "%PORTA%"=="" set PORTA=8080
echo   http://localhost:%PORTA%
echo.
timeout /t 5 >nul
start http://localhost:%PORTA%
