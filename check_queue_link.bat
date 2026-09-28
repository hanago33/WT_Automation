@echo off
setlocal
cd /d "%~dp0"
set "PY=D:\wt_python\python.exe"
if not exist "%PY%" set "PY=%~dp0..\wt_python\python.exe"
if not exist "%PY%" set "PY=%~dp0wt_python\python.exe"
if not exist "%PY%" (
  echo [ERROR] Portable python not found. Edit this bat: set PY=path\to\python.exe
  pause
  exit /b 1
)
set "SERVER_IP="
set /p "SERVER_IP=Enter server IP (e.g. 192.168.1.10): "
if "%SERVER_IP%"=="" (
  echo [ERROR] Empty IP.
  pause
  exit /b 1
)
"%PY%" "%~dp0wt_queue_selfcheck.py" --check %SERVER_IP%
echo.
pause
