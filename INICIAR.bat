@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title Fluxo Escolar - Inicializacao e diagnostico
cls
echo ===========================================================
echo        FLUXO ESCOLAR 2025-2026 - VERSAO 1.2.1
echo ===========================================================
echo.
set "LAUNCHER="
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>nul
    if not errorlevel 1 set "LAUNCHER=py -3"
)
if not defined LAUNCHER (
    where python >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys" >nul 2>nul
        if not errorlevel 1 set "LAUNCHER=python"
    )
)
if not defined LAUNCHER (
    echo ERRO: Python nao encontrado.
    echo Instale Python 3.11 a 3.14 de 64 bits, habilitando o comando py.
    echo Depois execute este arquivo novamente.
    echo.
    pause
    exit /b 1
)
%LAUNCHER% "%~dp0bootstrap.py"
set "CODIGO=%ERRORLEVEL%"
if not "%CODIGO%"=="0" (
    echo.
    echo ==========================================================
    echo O programa nao iniciou. Codigo: %CODIGO%
    echo O diagnostico foi salvo em: logs\ultima_execucao.log
    echo Envie esse arquivo para que o erro possa ser corrigido.
    echo ==========================================================
    echo.
    pause
    exit /b %CODIGO%
)
echo Programa encerrado normalmente.
exit /b 0
