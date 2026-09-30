---
kind: dependency_management
name: Python 依赖管理（无统一清单，按功能域散落声明）
category: dependency_management
scope:
    - '**'
source_files:
    - requirements-template-builder.txt
    - README.md
    - WT_Launcher.py
    - wt_flow_executor.py
    - WT_AUTOMATION_Agent/agent.py
    - WT_AUTOMATION_Agent/parameter_scan.py
    - WT_AUTOMATION_Agent/skill_bridge.py
    - tools/external_capture/uiapeek_client.py
---

本仓库未采用统一的 Python 包清单与锁定机制（不存在 `requirements.txt`、`pyproject.toml`、`setup.py`、`poetry.lock`、`Pipfile` 等标准文件），第三方依赖以“散落式”方式在代码中通过 `import` 引入，并在个别脚本中以运行时提示告知安装命令。整体呈现以下特征：

1. **唯一显式清单**：仅 `requirements-template-builder.txt` 列出图像/数值处理相关依赖（`opencv-python`、`numpy`、`Pillow`），该文件由 README 中的 `pip install -r requirements-template-builder.txt` 指引安装，但未被 CI 或构建流程强制引用。
2. **核心 UI 自动化依赖未集中声明**：`WT_Launcher.py`、`wt_flow_executor.py` 等主入口直接 `import pywinauto`、`pyautogui`、`pywinauto_recorder.player`；Agent 模块的 HTTP 客户端使用 `requests`；Excel 能力依赖 `openpyxl`；外部 UIA 录制桥接依赖 `signalrcore`。这些包均未出现在任何清单文件中，而是通过运行时的 `ImportError` 捕获 + 弹出 `messagebox.showerror(...)` 或字符串提示（如 `需要 openpyxl 库。请执行: pip install openpyxl`）来引导用户手动安装。
3. **可选/按需导入模式**：对非核心依赖（如 `openpyxl`、`requests`、`signalrcore`）普遍采用函数体内 `import xxx` 的懒加载方式，使基础功能在无这些包时仍可启动，仅在调用对应路径时才报错并给出安装提示。这降低了最小可运行环境的门槛，但也意味着无法通过静态扫描发现缺失依赖。
4. **无版本约束与锁定**：所有依赖均以裸包名形式出现，无任何 `==`、`>=` 等版本限定，也不存在 `pip freeze > requirements.txt` 生成的锁定文件或 `vendor/` 目录，导致不同机器/环境间行为可能因上游包升级而漂移。
5. **C/C++ 扩展与二进制资源**：OCR 引擎 Tesseract 及其 `tessdata` 语言包以预编译二进制形式随仓库分发于 `tools/ORC/` 下，属于“vendored 二进制依赖”，不经过 pip 管理。
6. **CI 层面缺失依赖校验**：`.github/workflows/deploy-website.yml` 仅部署静态网站，未包含 Python 测试或依赖安装步骤，因此 CI 不会验证依赖是否齐全。

开发者应遵循的约定与约束：
- 新增第三方依赖时，优先将其加入 `requirements-template-builder.txt` 并更新 README 安装说明，避免继续以“运行时提示”方式暴露。
- 对可选依赖保持函数内 `import` 的懒加载风格，并提供清晰的 `pip install <pkg>` 错误提示。
- 如需固定版本，应在清单中使用 `==` 锁定，并同步提交到版本控制。
- 新增 C/C++ 扩展或二进制工具时，沿用 `tools/<tool>/` 子目录 vendoring 模式，并在文档中说明其来源与许可证。