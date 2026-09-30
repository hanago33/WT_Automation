---
kind: dependency_management
name: Python 依赖管理（requirements.txt + 可选依赖懒加载）
category: dependency_management
scope:
    - '**'
source_files:
    - requirements.txt
    - requirements-template-builder.txt
    - tools/ORC/
    - uia_tree_dumper/uia_tree_dumper.csproj
---

本仓库采用 Python 标准依赖管理方式，通过 `requirements.txt` 集中声明第三方库版本下限，未使用 lockfile、虚拟环境文件或私有源配置。

**1. 依赖声明与版本策略**
- 主依赖清单位于根目录 `requirements.txt`，按功能模块分组注释（桌面自动化核心、控件库/图像模板构建、Agent/网络请求、Excel 读写、可选 OCR），所有包均使用 `>=` 指定最低版本，如 `pywinauto>=0.6.8`、`opencv-python>=4.7.0`、`requests>=2.28.0` 等。
- 独立工具依赖 `requirements-template-builder.txt` 仅包含 `opencv-python`、`numpy`、`Pillow`，用于图像模板构建。
- 未使用 `pip freeze` 生成锁定文件，也未发现 `setup.py`、`pyproject.toml`、`poetry.lock`、`Pipfile` 等其它依赖管理格式。

**2. 可选依赖与懒加载模式**
- 关键第三方库采用运行时懒加载 + 友好错误提示的模式：`openpyxl`、`pyautogui`、`signalrcore` 等在代码中通过 `try/except ImportError` 或条件 import 引入，缺失时返回明确安装指引（如 `需要 openpyxl 库。请执行: pip install openpyxl`）。
- `pynput` 作为半自动采集首选方案，缺失时自动降级到零依赖的 Windows 原生钩子（`win32_input_hook.py`，仅用标准库 `ctypes`）。
- `pytesseract` 被注释掉，说明 OCR 能力为可选。

**3. 外部二进制依赖**
- `tools/ORC/` 目录下内嵌 Tesseract OCR 完整可执行程序及 DLL（约 50+ 个 `.exe`、`.dll` 文件），无需系统安装即可运行。
- `uia_tree_dumper/` 包含 C# 源码项目（`.csproj`），需 Visual Studio 编译生成二进制。
- `vendor/` 目录为空，未使用 vendoring 策略。

**4. 平台约束**
- 注释明确标注「平台：Windows（桌面 RPA 自动化，依赖 pywinauto / 原生 Win32 API）」，所有 GUI 自动化相关依赖均绑定 Windows 环境。
- 安装命令示例使用 `py -3.11 -m pip install ...`，暗示推荐使用 Python 3.11。

**5. 已知限制**
- `pywinauto_recorder.player` 不在 PyPI，需从本地安装目录获取，未纳入依赖清单。
- 无依赖更新自动化流程（未发现 CI 中的依赖检查脚本）。