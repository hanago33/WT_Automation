@echo off
setlocal
cd /d "%~dp0"
set "SCRIPT=%~dp0WT_Launcher.py"
title WT Launcher Bootstrap

:: 自我提权：外部控件采集的 UiaPeek / AxeBridge 都需要管理员权限（全局钩子/跨完整性 UI 枚举）。
:: 若当前非管理员，用 PowerShell runas 重新以管理员启动本 bat；若 UAC 被拒则降级为普通权限运行。
net session >nul 2>&1
if errorlevel 1 (
    echo 请求管理员权限（UiaPeek / AxeBridge 需要）...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" >nul 2>&1
    if errorlevel 1 (
        echo 未获得管理员权限，将以普通权限运行（UiaPeek/axe 可能需手动提权）。
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
