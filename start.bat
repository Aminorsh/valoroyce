@echo off
rem One-click start for Windows: install dependencies, then launch. Models are
rem downloaded from the program's own settings window on the first start.
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

rem --inexact: keep what the settings window installed (the Whisper libraries).
"%UV%" sync --extra app --inexact
if errorlevel 1 goto failed

rem The settings window installs the Whisper libraries with this uv.
set "VALOROYCE_UV=%UV%"
"%UV%" run --no-sync valoroyce %*
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo Something went wrong. The messages above say what; see README.md for help.
pause
exit /b 1
