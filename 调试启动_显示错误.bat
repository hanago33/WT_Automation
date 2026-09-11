@echo off
cd /d "%~dp0"
set "PYTHON=D:\wt_python\python.exe"
if not exist "%PYTHON%" (
    echo [WARN] D:\wt_python\python.exe 不存在，改用系统 python...
    set "PYTHON=python"
)
echo ============================================================
echo  WT_Automation 调试启动（显示完整错误信息）
echo  Python: %PYTHON%
echo ============================================================
echo.
"%PYTHON%" WT_Launcher.py
echo.
echo ============================================================
echo  程序已退出，请截图上方的错误信息。
echo ============================================================
pause
