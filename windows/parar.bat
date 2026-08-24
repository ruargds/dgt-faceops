@echo off
REM DGT FaceOps - para o painel
REM
REM Nao apaga nada: banco, backups e gravacoes ficam nos volumes.
title DGT FaceOps - Parar
cd /d "%~dp0.."

echo Parando o DGT FaceOps...
echo.
echo   Atencao: com o painel parado, os agendamentos de backup NAO rodam.
echo.

docker compose stop

echo.
echo   Parado. Banco, backups e gravacoes foram preservados.
echo   Para subir de novo: iniciar.bat
echo.
pause
