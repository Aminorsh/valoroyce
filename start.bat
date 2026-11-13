@echo off
rem One-click start for Windows: install dependencies, fetch models on first run, launch.
cd /d "%~dp0"

rem The release zip ships uv.exe next to this file; otherwise use an installed one.
set "UV=uv"
if exist "%~dp0uv.exe" set "UV=%~dp0uv.exe"

"%UV%" --version >nul 2>nul
if errorlevel 1 (
    echo uv is not installed. Open PowerShell and run:
    echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo then close this window and double-click start.bat again.
    pause
    exit /b 1
)

"%UV%" sync --extra app
if errorlevel 1 goto failed

if not exist "models\llama" (
    "%UV%" run valoroyce setup
    if errorlevel 1 goto failed
)

"%UV%" run valoroyce %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Something went wrong. The messages above say what; see README.md for help.
pause
exit /b 1
