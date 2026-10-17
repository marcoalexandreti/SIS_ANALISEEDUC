@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo ATENCAO: Esta operacao apenas RENOMEIA o ambiente virtual antigo.
echo O banco SQLite e todos os arquivos da rede serao preservados.
echo.
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%~dp0bootstrap.py" --reparar
) else (
    python "%~dp0bootstrap.py" --reparar
)
if errorlevel 1 pause
