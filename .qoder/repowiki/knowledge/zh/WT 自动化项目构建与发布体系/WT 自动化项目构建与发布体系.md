---
kind: build_system
name: WT 自动化项目构建与发布体系
category: build_system
scope:
    - '**'
source_files:
    - 启动WT自动化总控台.bat
    - upload_website.bat
    - .github/workflows/deploy-website.yml
    - requirements-template-builder.txt
    - build_control_map_library.py
    - build_image_template_library.py
    - tools/generate_flow_package.py
    - WT_Automation.robot
    - WT_Launcher.py
---

本仓库采用“脚本驱动 + GitHub Actions”的轻量级构建与发布模式，围绕 Windows 桌面 UI 自动化场景组织。核心特点如下：

1. 运行时依赖与环境
- Python 包依赖通过根目录 requirements-template-builder.txt 声明（opencv-python、numpy、Pillow），其余依赖由顶层 README 指引使用 pip/conda 安装。
- 启动入口以 .bat 批处理为主：`启动WT自动化总控台.bat` 负责自动提升管理员权限并尝试 py -3.11 / python / pyw / pythonw 多种解释器路径，最终调用 `WT_Launcher.py`；另有 `_launch_pywinauto_recorder.cmd` 用于录制辅助。
- OCR 子模块内嵌 Tesseract 二进制（`tools/ORC/tesseract.exe`）及训练数据，模板采集器会按多候选路径探测 tesseract 可执行文件。

2. 构建与资产生成脚本
- 控件库构建：`build_control_map_library.py` 提供 Tkinter GUI，基于 pywinauto 遍历目标窗口 UIA/Win32 树，输出 `control_maps/*.json` 标准控件目录与 window control map。
- 图像模板库构建：`build_image_template_library.py` 提供截图→候选区域检测→OCR 命名→保存 PNG 模板到 `image_templates/` 并维护 `templates_index.json` 索引。
- 流程包生成：`tools/generate_flow_package.py` 将步骤定义转换为 flow package JSON；`flow_packages/` 下集中存放各业务场景的 flow_definition_* 与 steps Excel/JSON。
- 工具脚本：`tools/merge_standard_control_library.py` 合并标准控件库；`wait_global_mapper_ready.py` 等待外部软件就绪；`ui_tars_runner.js` 为 Node 侧运行器。

3. 测试与验证
- 单元测试位于 `tests/`，使用 pytest 框架（`.gitignore` 包含 `.pytest_cache/`、`.mypy_cache/`）。README 明确列出 `pytest` 作为测试命令。
- Robot Framework 端到端用例：`WT_Automation.robot` 通过 `resources/dispatch_keywords.resource` 调度完整 WT 流程，适合回归验证。
- 示例与录制脚本：`samples/flows/` 提供 JSON 流程样例与 Excel 步骤表；`samples/recorder_scripts/` 与 `WT_AUT_recorded.py` 记录原始 pywinauto-recorder 操作。

4. CI 与网站发布
- GitHub Actions 仅包含站点部署流水线：`.github/workflows/deploy-website.yml` 监听 `website/**` 与 workflow 变更，在 push/main 或手动触发时，使用 `actions/configure-pages@v5` + `actions/upload-pages-artifact@v3` + `actions/deploy-pages@v4` 将 `website/` 静态资源发布到 GitHub Pages。
- 本地一键上传脚本 `upload_website.bat` 封装 git add/commit/push 流程，引导开发者完成 Pages 配置。

5. 版本与产物管理
- 无 Dockerfile、Makefile、setup.py/pyproject.toml 等标准化打包清单；版本信息未集中管理，主要依赖 Git 提交历史与 `backups/<timestamp>/` 快照目录进行人工归档。
- 运行期日志与报告落盘：`logs/run_reports/wt_run_<timestamp>.json`、`artifacts/legacy_logs/` 中的 Robot HTML/XML 报告、`debug_screenshots/` 截图等。

6. 开发者应遵循的约定
- 新增 Python 依赖请同步更新 `requirements-template-builder.txt` 并在 README 中说明。
- 新增构建/采集类工具优先放在根目录或 `tools/` 下，保持单文件可独立运行，并通过 Tkinter CLI 暴露参数。
- 新增流程包放入 `flow_packages/`，并在 `flow_package_registry.json` 注册；对应步骤 Excel/JSON 保持命名一致。
- 新增图片模板通过 `build_image_template_library.py` 录入至 `image_templates/<category>/`，确保 `templates_index.json` 被重建。
- 修改 website 内容后使用 `upload_website.bat` 推送，GitHub Actions 会自动部署到 Pages。
- 运行需要管理员权限的 UI 捕获功能时，务必通过 `启动WT自动化总控台.bat` 启动，以便自动提权。