@echo off
REM DGT FaceOps - desinstalacao
title DGT FaceOps - Desinstalar
cd /d "%~dp0.."

echo ====================================================
echo   DGT FaceOps - desinstalacao
echo ====================================================
echo.
echo   Isto remove os containers e as imagens do painel.
echo.
echo   O que E APAGADO:
echo     - containers e imagens Docker do FaceOps
echo.
echo   O que E PRESERVADO por padrao:
echo     - data\backups   (artefatos de backup)
echo     - data\sessions  (gravacoes de terminal)
echo     - .env           (SECRET_KEY e configuracao)
echo     - volume do banco (cadastro de servidores, historico, auditoria)
echo.

set /p CONFIRMA="   Digite REMOVER para continuar: "
if /i not "%CONFIRMA%"=="REMOVER" (
    echo.
    echo   Cancelado. Nada foi alterado.
    pause
    exit /b 0
)

echo.
echo Removendo containers...
docker compose down --remove-orphans

echo Removendo imagens...
docker image rm dgt-faceops-backend dgt-faceops-frontend 2>nul
docker image rm faceops-backend faceops-frontend 2>nul

echo.
echo   Containers removidos.
echo.
echo   Para apagar TAMBEM o banco do painel (cadastro de servidores,
echo   historico de backups e auditoria), rode:
echo.
echo     docker volume rm dgt-faceops_postgres_data
echo.
echo   As credenciais guardadas so sao legiveis com a SECRET_KEY do .env.
echo   Se for descartar a maquina, apague o .env por ultimo.
echo.
pause
