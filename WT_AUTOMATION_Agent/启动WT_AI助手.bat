@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0.."
title WT AI 助手 — 快速启动

echo ==========================================
echo    WT Automation — AI Agent 快速启动
echo ==========================================
echo.
echo  [1] 启动 AI 对话 GUI (浏览器界面)
echo  [2] 启动流程编辑器 (含 AI 助手 Tab)
echo  [3] 启动总控台 (WT Launcher)
echo  [0] 退出
echo.
set /p CHOICE="请输入选项 (0/1/2/3): "

if "%CHOICE%"=="1" goto gui
if "%CHOICE%"=="2" goto editor
if "%CHOICE%"=="3" goto launcher
if "%CHOICE%"=="0" goto end
echo 无效选项，请重新运行。
pause
exit /b

:gui
echo.
echo 正在启动 AI 对话 GUI...
echo 浏览器将自动打开 http://127.0.0.1:8765
echo 按 Ctrl+C 可停止服务。
echo.
call :find_python
if "%PYCMD%"=="" (
    echo 未找到 Python 解释器！
    pause
    exit /b 1
)
%PYCMD% -m WT_AUTOMATION_Agent.gui
goto end

:editor
echo.
echo 正在启动 WT 流程编辑器...
echo AI 助手功能已集成在"AI 助手"Tab 中。
echo.
call :find_python
if "%PYCMD%"=="" (
    echo 未找到 Python 解释器！
    pause
    exit /b 1
)
%PYCMD% -c "from WT_Flow_Editor import main; main()"
goto end

:launcher
echo.
echo 正在启动 WT 总控台...
call :find_python
if "%PYCMD%"=="" (
    echo 未找到 Python 解释器！
    pause
    exit /b 1
)
%PYCMD% "%~dp0..\WT_Launcher.py"
goto end

:find_python
where py >nul 2>nul
if not errorlevel 1 (
    set "PYCMD=py"
    exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
    set "PYCMD=python"
    exit /b
)
where python3 >nul 2>nul
if not errorlevel 1 (
    set "PYCMD=python3"
    exit /b
)
set "PYCMD="
exit /b

:end
pause
