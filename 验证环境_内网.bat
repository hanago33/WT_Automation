@echo off
setlocal
set "PYTHON=D:\wt_python\python.exe"

echo === Step 1: Portable Python ===
if exist "%PYTHON%" (
    echo FOUND: %PYTHON%
    "%PYTHON%" --version
) else (
    echo [ERROR] Not found: %PYTHON%
    echo Please extract wt_python_portable.zip to D:\wt_python first.
    echo If extracted elsewhere, edit the PYTHON path in this file.
    pause
    exit /b 1
)
echo.

echo === Step 2: Dependencies import test ===
"%PYTHON%" -c "import pywinauto, cv2, numpy, PIL, openpyxl, requests, pynput, pyautogui; print('DEPS OK')"
echo.

echo === Step 3: Project files ===
if exist "%~dp0WT_Launcher.py" (
    echo WT_Launcher.py found.
) else (
    echo [ERROR] WT_Launcher.py not found. Check full package extraction.
)
echo.

echo === Step 4: Launch test (short) ===
"%PYTHON%" -c "import WT_Launcher" 2>&1 | findstr /v "^$"
echo.
echo If Step 2/3/4 all passed, the environment is OK.
pause
