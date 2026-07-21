@echo off
setlocal
cd /d "%~dp0"
set "SCRIPT=%~dp0WT_Launcher.py"
title WT Launcher Bootstrap

:: Self-elevate: UiaPeek / AxeBridge for external control capture requires administrator rights.
:: If not currently admin, restart this .bat with PowerShell's runas. Fallback to normal user if UAC is denied.
net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    if errorlevel 1 (
        echo Admin rights not granted. Running with normal privileges.
    ) else (
        exit /b
    )
)

echo Starting WT Launcher...
echo Script: "%SCRIPT%"
echo.

where py >nul 2>nul
if not errorlevel 1 (
    echo Trying: py -3.11
    py -3.11 "%SCRIPT%"
    if %errorlevel%==0 exit /b 0
    echo.
    echo Fallback: py
    py "%SCRIPT%"
    pause
    exit /b %errorlevel%
)

where python >nul 2>nul
if not errorlevel 1 (
    echo Trying: python
    python "%SCRIPT%"
    pause
    exit /b %errorlevel%
)

where pyw >nul 2>nul
if not errorlevel 1 (
    echo Trying background launch with pyw...
    start "" pyw -3.11 "%SCRIPT%"
    if not errorlevel 1 exit /b 0
    start "" pyw "%SCRIPT%"
    exit /b 0
)

where pythonw >nul 2>nul
if not errorlevel 1 (
    echo Trying background launch with pythonw...
    start "" pythonw "%SCRIPT%"
    exit /b 0
)

echo No usable Python interpreter was found.
pause
