@echo off
REM 打包 txt2wtg 为 exe（需要 PyInstaller）
REM 用法：双击本文件，或在该目录 cmd 中执行 build.bat

cd /d %~dp0

where pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 pyinstaller，请先执行：pip install pyinstaller
    pause
    exit /b 1
)

echo [1/2] 构建 GUI 版（窗口程序，无控制台）...
pyinstaller --noconfirm --clean build_gui.spec
if errorlevel 1 (
    echo [失败] GUI 构建出错
    pause
    exit /b 1
)

echo [2/2] 构建完成，输出位于 dist\txt2wtg_gui\txt2wtg_gui.exe
echo 提示：CLI 版可单独打包：pyinstaller --onefile -w txt2wtg.py  （改用 --console 如需命令行）
pause
