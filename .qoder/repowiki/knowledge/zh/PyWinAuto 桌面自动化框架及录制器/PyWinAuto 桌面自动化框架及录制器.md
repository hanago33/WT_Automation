---
kind: external_dependency
name: PyWinAuto 桌面自动化框架及录制器
slug: pywinauto
category: external_dependency
category_hints:
    - vendor_identity
    - framework_behavior
scope:
    - '**'
---

### PyWinAuto 及其录制器在 WT Automation 中的集成

**角色定位**：作为底层 UI 自动化引擎，负责 Windows 应用程序的控件识别、操作模拟和事件捕获。

**核心集成点**：
- `flow_recorder_converter.py`：解析 pywinauto_recorder 生成的 .py 脚本，提取 UIPath 元素路径和操作语义
- `wt_flow_locator.py`：基于 UIA/Win32 API 进行控件定位，支持多策略匹配（automationId、name、controlType）
- `WT_AUT_recorded.py`：执行入口，调用 pywinauto 进行实际的 UI 操作

**关键行为模式**：
- 录制阶段：通过 pywinauto_recorder 捕获用户操作，生成包含 UIPath 的 Python 脚本
- 转换阶段：AST 解析脚本，将低级操作转换为高层 flow_definition.json 步骤
- 执行阶段：使用 pywinauto 的 UIA 或 Win32 后端定位控件并执行操作

**重要约束**：
- 录制脚本格式必须兼容 pywinauto_recorder 的输出规范
- UIPath 路径解析需要处理嵌套窗口、通配符（*-）等复杂场景
- 控件定位失败时需要 fallback 机制（模板匹配、坐标点击等）

verify exact API/params against official docs