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
"%PY%" "%~dp0wt_queue_selfcheck.py" --server
echo.
pause
