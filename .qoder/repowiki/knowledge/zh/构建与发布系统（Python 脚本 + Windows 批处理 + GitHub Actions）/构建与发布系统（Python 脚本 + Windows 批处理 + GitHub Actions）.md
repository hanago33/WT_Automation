---
kind: build_system
name: 构建与发布系统（Python 脚本 + Windows 批处理 + GitHub Actions）
category: build_system
scope:
    - '**'
source_files:
    - requirements.txt
    - requirements-template-builder.txt
    - 启动WT自动化总控台.bat
    - 同步录制文件.bat
    - upload_website.bat
    - build_control_map_library.py
    - build_image_template_library.py
    - .github/workflows/deploy-website.yml
    - ui_tars_runner.js
    - WT_Automation.robot
---

该仓库是一个纯 Python 桌面 RPA Agent 项目，没有传统的 Makefile/Docker/编译型构建系统，而是通过一组 Python 脚本、Windows 批处理文件和 GitHub Actions 完成依赖安装、工具链构建、UI 控件库/图像模板库生成以及网站部署。核心特点如下：

1) 依赖管理
- 统一依赖清单位于 requirements.txt，按功能分组注释（桌面自动化、控件库/图像模板、Agent/网络、Excel 读写、可选 OCR），并明确说明平台为 Windows。
- 模板构建器有独立依赖 requirements-template-builder.txt（opencv-python、numpy、Pillow）。
- 部分依赖不在 PyPI（如 pywinauto_recorder.player），通过本地路径或注释说明降级策略，保证 GUI/采集/控件库构建在缺少可选依赖时仍可运行。

2) 启动与运行入口
- 启动总控台由 启动WT自动化总控台.bat 负责：自动检测管理员权限（UAC）、依次尝试 py -3.11 / python / pyw / pythonw 解释器，失败时给出提示；这是用户级“构建后运行”的统一入口。
- 同步录制文件由 同步录制文件.bat 调用 tools/sync_recorded.py 执行。
- 其他辅助脚本包括 _launch_pywinauto_recorder.cmd（用于本地安装/启动 Recorder）等。

3) 资源构建脚本
- build_control_map_library.py：基于 pywinauto UIA/win32 后端扫描目标窗口控件树，结合 C# 子进程 uia_tree_dumper.exe 补采深层控件，输出 control_maps 下的 library/*.json 标准控件库，支持质量分级、定位策略推荐、MSAA 角色/状态解码等。
- build_image_template_library.py：Tkinter GUI 工具，对界面截图进行边缘检测+形态学候选区域提取，支持 OCR（pytesseract 或内置 tesseract.exe）命名、批量保存、布局 JSON 导入导出，输出 image_templates 目录的模板索引与分类。
- 这两个脚本既是“构建阶段”的资源生产工具，也是开发期交互式采集工具。

4) CI/CD 与发布
- .github/workflows/deploy-website.yml：GitHub Actions 工作流，仅在 website/** 或 workflow 文件变更时触发，使用 actions/configure-pages@v5 + upload-pages-artifact@v3 将 website 目录部署到 GitHub Pages，支持手动触发和并发控制。
- upload_website.bat：本地一键提交 website 目录及 workflow 配置并 push 到远端，配合 GitHub Pages 设置 Source=main + /website 实现站点发布。
- ui_tars_runner.js：Node.js 包装器，加载本地 UI-TARS 仓库 SDK，读取环境变量/配置文件中的模型 baseURL、apiKey、model，注入 system prompt 后驱动桌面 GUI Agent，作为 AI 驱动的 UI 操作执行层。

5) Robot Framework 测试
- WT_Automation.robot 定义了一个完整流程用例，通过 Resource 引用 resources/dispatch_keywords.resource 组织关键字，属于行为级验收测试而非构建步骤。

6) 约定与约束
- 所有构建/采集类脚本均为 Python 单文件可执行模块，通过命令行参数或 Tkinter GUI 交互驱动，不依赖外部构建系统。
- Windows 专属能力（COM、Win32 API、pywinauto、ctypes）集中在 build_* 脚本中，非 Windows 环境会静默降级。
- 网站部署采用 GitOps 模式：代码即配置，push 触发 Pages 构建，无需额外打包步骤。
- 无统一的版本化产物（如 wheel/zip），发布以源码+脚本为主，运行时依赖通过 pip 安装。

关键文件
- requirements.txt、requirements-template-builder.txt
- 启动WT自动化总控台.bat、同步录制文件.bat、upload_website.bat
- build_control_map_library.py、build_image_template_library.py
- .github/workflows/deploy-website.yml
- ui_tars_runner.js
- WT_Automation.robot
- resources/dispatch_keywords.resource