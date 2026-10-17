@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0bootstrap.py" --diagnose
) else (
    python "%~dp0bootstrap.py" --diagnose
)
echo.
echo O relatorio esta na pasta logs, arquivo ultima_execucao.log
pause
