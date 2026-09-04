@echo off
setlocal
cd /d "%~dp0"
set "SCRIPT=%~dp0WT_Launcher.py"
set "LOG_DIR=%~dp0logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
set "BOOT_LOG=%LOG_DIR%\launcher_headless.log"

rem ============================================================
rem  WT Launcher - headless launcher (always elevated)
rem  - Always starts as Administrator (external control capture
rem    and elevated MUP targets need it). UAC prompt appears once.
rem  - Launches WT_Launcher.py via pyw/pythonw, so no console
rem    window stays open once the tkinter UI is running.
rem  - Startup stderr is appended to logs\launcher_headless.log.
rem ============================================================

set "HEADLESS_EXE="
set "HEADLESS_ARGS="

where pyw.exe >nul 2>nul
if not errorlevel 1 (
    for /f "delims=" %%P in ('where pyw.exe 2^>nul') do set "HEADLESS_EXE=%%P"
)
if defined HEADLESS_EXE (
    pyw -3.11 --version >nul 2>&1
    if not errorlevel 1 set "HEADLESS_ARGS=-3.11"
)
if not defined HEADLESS_EXE (
    where pythonw.exe >nul 2>nul
    if not errorlevel 1 (
        for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do set "HEADLESS_EXE=%%P"
    )
)

net session >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator privileges...
    if defined HEADLESS_EXE (
        rem Elevate straight into the windowless interpreter: no cmd flash.
        powershell -NoProfile -Command "Start-Process -FilePath '%HEADLESS_EXE%' -ArgumentList '%HEADLESS_ARGS% \"%SCRIPT%\"' -Verb RunAs" >nul 2>&1
    ) else (
        rem No windowless interpreter found: elevate this script again.
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

if defined HEADLESS_EXE (
    start "" "%HEADLESS_EXE%" %HEADLESS_ARGS% "%SCRIPT%" >> "%BOOT_LOG%" 2>&1
    exit /b 0
)

rem No pyw/pythonw found: fall back to a visible elevated console so errors are readable.
where py >nul 2>nul
if not errorlevel 1 (
    py -3.11 "%SCRIPT%"
    if %errorlevel%==0 exit /b 0
    py "%SCRIPT%"
    pause
    exit /b %errorlevel%
)

where python >nul 2>nul
if not errorlevel 1 (
    python "%SCRIPT%"
    pause
    exit /b %errorlevel%
)

echo No usable Python interpreter was found.
pause
