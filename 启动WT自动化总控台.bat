@echo off
setlocal
cd /d "%~dp0"
set "SCRIPT=%~dp0WT_Launcher.py"
title WT Launcher Bootstrap

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
