@echo off
setlocal
cd /d "%~dp0"
set "SCRIPT=%~dp0WT_Launcher.py"
:: Portable Python path on the intranet machine (edit if extracted elsewhere)
set "PYTHON=D:\wt_python\python.exe"
title WT Launcher (Internal - Portable Python)

if not exist "%PYTHON%" (
    echo [ERROR] Portable Python not found: "%PYTHON%"
    echo Please extract wt_python_portable.zip to D:\wt_python,
    echo or edit the PYTHON path at the top of this file.
    pause
    exit /b 1
)

echo Using portable Python: "%PYTHON%"
"%PYTHON%" --version
echo.

:: Self-elevate: external control capture (UiaPeek / AxeBridge) requires admin rights
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
"%PYTHON%" "%SCRIPT%"
pause
