@echo off
cd /d "%~dp0"
python tools/sync_recorded.py
echo.
echo 按任意键退出...
pause >nul
