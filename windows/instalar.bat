@echo off
REM ==========================================================================
REM DGT FaceOps - instalacao no Windows
REM
REM Chama o instalador PowerShell com a politica de execucao liberada apenas
REM para este processo. Nao altera a politica da maquina.
REM ==========================================================================
title DGT FaceOps - Instalacao

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo   Este instalador precisa ser executado como Administrador.
    echo   Clique com o botao direito e escolha "Executar como administrador".
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
