@echo off
rem One-click start for Windows: install dependencies, fetch models on first run, launch.
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo uv is not installed. Open PowerShell and run:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo then close this window and double-click start.bat again.
    pause
    exit /b 1
)

uv sync --extra app
if errorlevel 1 goto failed

if not exist "models\llama" (
    uv run valoroyce setup
    if errorlevel 1 goto failed
)

uv run valoroyce %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Something went wrong. The messages above say what; see README.md for help.
pause
exit /b 1
