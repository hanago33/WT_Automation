@echo off
setlocal
cd /d "%~dp0"
set "SCRIPT=%~dp0WT_Launcher.py"
rem Portable Python path on the intranet machine (edit if extracted elsewhere)
set "PYTHON=D:\wt_python\python.exe"
set "PYTHONW=D:\wt_python\pythonw.exe"
set "LOG_DIR=%~dp0logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "BOOT_LOG=%LOG_DIR%\launcher_headless.log"

rem ============================================================
rem  WT Launcher (Internal) - headless launcher (always elevated)
rem  - Always starts as Administrator. UAC prompt appears once.
rem  - Uses D:\wt_python\pythonw.exe when present, so no console
rem    window stays open once the tkinter UI is running.
rem  - Startup stderr is appended to logs\launcher_headless.log.
rem ============================================================

if not exist "%PYTHON%" (
    echo [ERROR] Portable Python not found: "%PYTHON%"
    echo Please extract wt_python_portable.zip to D:\wt_python,
    echo or edit the PYTHON path at the top of this file.
    pause
    exit /b 1
)

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    if exist "%PYTHONW%" (
        rem Elevate straight into pythonw: no cmd flash.
        powershell -NoProfile -Command "Start-Process -FilePath '%PYTHONW%' -ArgumentList '\"%SCRIPT%\"' -Verb RunAs" >nul 2>&1
    ) else (
        rem pythonw.exe missing: elevate this script again.
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    )
    if errorlevel 1 (
        echo.
        echo WT Automation must run as Administrator.
        echo Please re-run this launcher and accept the UAC prompt.
        pause
        exit /b 1
    )
    exit /b 0
)

if exist "%PYTHONW%" (
    start "" "%PYTHONW%" "%SCRIPT%" >> "%BOOT_LOG%" 2>&1
    exit /b 0
)

rem pythonw.exe missing: fall back to console mode (still elevated).
echo [WARN] "%PYTHONW%" not found. Falling back to console mode.
"%PYTHON%" "%SCRIPT%"
pause
