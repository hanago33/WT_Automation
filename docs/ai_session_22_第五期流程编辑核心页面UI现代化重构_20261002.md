# ai_session_22：第五期流程编辑核心页面UI现代化重构

> **会话编号**：ai_session_22  
> **分支**：`ai/session-5`（Worktree：`E:\My_RF_Project\WT_automation_ai5`）  
> **日期**：2026-10-02  
> **主题**：流程编辑器高频关键页面现代化（新建链路保存弹窗、细分控件精确编辑、半自动控件采集器）  
> **状态**：已完成并通过全量回归测试（1491 passed，176 subtests passed）

---

## 1. 重构背景与定位

在前期（Phase 1~4，ai_session_18~21）中，我们先后完成了日志查看器、快捷启动配置、流程画布节点面板、控件库管理与服务监控面板的现代化重构。

通过全面复盘与全项目页面扫描（基于规划书 `ui_ux_modernization_plan_phase5.md`），用户在日常编写与调试自动化流程时，**高频停留在细分控件配置与半自动采集场景**。然而，旧界面存在以下严重影响操作效率与视觉体验的问题：
1. **`NewDefaultChainDialog`（新建链路保存对话框）**：
   - 样式简陋（古老灰底粗边框），缺少快捷路径填充（每次新建都需要层层点击文件系统找工作区目录）；
   - 缺少目标路径探测，重名覆盖前无任何视觉警示。
2. **`ControlEditorDialog`（细分控件精确编辑对话框）**：
   - Inspect 属性多达 24 个，平铺在右侧滚动条中，寻找特定属性耗时；
   - 缺少当前定位状态有效性反馈（缺少 ID 或定位要素时无即时徽标提示）；
   - 无法一键将解析到的属性回填为定位目标，缺乏内联定位检验入口与表达式复制。
3. **`SemiAutoInspectCollectorDialog`（半自动控件采集器）**：
   - 顶栏静态说明卡片常驻占用约 140px 纵向高度，严重挤压候选列表与预览视窗；
   - 监听状态缺乏呼吸式指示徽标，用户无法一眼看出当前是交互监听、剪贴板监听还是停止状态；
   - 采集的候选列表缺乏实时搜索过滤功能；右侧候选预览无直接复制定位与快速编辑动作。

本次重构旨在统一现代 Slate 视觉风格体系，消除操作卡点，实现高效率、低认知的现代化流程编辑体验。

---

## 2. 核心重构页面与功能特性

### 2.1 Candidate C: `NewDefaultChainDialog`（新建链路保存对话框）
- **现代化视觉卡片**：升级为 Slate 主题白底卡片容器与强调标题栏。
- **快捷直达芯片胶囊（Quick-Jump Chips）**：
  - 提供 `[📁 当前工作区]`、`[⚡ 项目根目录]`、`[🖥️ 桌面]` 及本机可用盘符胶囊；
  - 提供常用链路命名预设：`[标准全流程]`、`[参数扫描子流程]`、`[快捷测试链路]`。
- **即时存在性探针条（Live Existence Probe）**：
  - 监听目录与名称输入，动态探测目标路径；
  - 目标就绪时展示绿色 `✓ 目标路径就绪：...` 徽标；
  - 目标已存在时展示黄色预警 `⚠ 同名文件已存在：...，确定后将提示覆盖`。
- **键盘加速支持**：绑定 `<Return>` 确定新建，`<Escape>` 取消关闭。

### 2.2 Candidate A: `ControlEditorDialog`（细分控件精确编辑对话框）
- **实时定位三要素校验徽标（Live Validation Badge）**：
  - 动态监听控件 ID、target_method、target_value；
  - 完整时呈现绿色胶囊 `✓ 定位三要素完整 [method]`；
  - 缺失关键要素时即时呈现黄色预警 `⚠ 缺少 target_value *` 或红色 `❌ 缺少控件ID *`。
- **属性快速检索筛选（Inspect Attribute Search Filter）**：
  - Inspect 解析属性面板顶部集成搜索框 `🔍 筛选属性:`；
  - 支持即时过滤 23 项属性（如输入 "class"、"name"、"auto" 即刻收拢无关行）。
- **属性一键填入定位（Quick-Apply Chips）**：
  - 在 ClassName、AutomationId、Name、ControlType 等属性行旁提供 `[填入定位]` 按钮；
  - 点击即可秒级填入定位策略对应的值并标记已修改。
- **内联定位透视台与表达式复制**：
  - 增加 `🧪 校验此定位` 按钮，直接唤起 Phase 1 的 `ControlLocatorTesterDialog` 屏幕透视台；
  - 增加 `📋 复制定位表达` 按钮，快速将 `method:value` 复制到系统剪贴板。
- **键盘加速支持**：绑定 `<Control-s>` 快速保存，`<Escape>` 取消。

### 2.3 Candidate B: `SemiAutoInspectCollectorDialog`（半自动控件采集器）
- **动态监听状态呼吸徽标（Active Listener Pill Badge）**：
  - 待命状态：`○ 监听已停止 (待命)`（淡灰 Slate 胶囊）；
  - 剪贴板监听：`● 剪贴板监听中 (在 Inspect 中复制即可抓取)`（柔和蓝底胶囊）；
  - 交互监听：`● 交互点击监听中 (点击或按 F8 抓取)`（翠绿底呼吸徽标）。
- **可折叠式操作指南卡片（Collapsible Guide Banner）**：
  - 默认紧凑折叠为 1 行说明，点击 `[▼ 展开指南]` / `[▲ 收起指南]` 自由切换；
  - 释放超过 100px 纵向高度给候选控件树与详情预览。
- **候选列表实时搜索过滤（Candidate Search Filter）**：
  - 候选列表左上方配备 `🔍 过滤候选控件...` 搜索框与数量徽标 `候选控件 (N 个)`；
  - 支持按控件名称、定位表达式或窗口标题即时过滤。
- **候选详情操作栏（Candidate Actions）**：
  - 详情预览区顶部增加 `[📋 复制定位]`、`[✏️ 立即编辑]`、`[🧪 检验定位]` 快捷操作按钮。
- **操作按钮优化**：
  - 底部操作栏统一定义为 `[📥 导入全部候选 (N)]`、`[📥 导入所选候选 (1)]`、`[取消 (Esc)]`。

---

## 3. 架构设计与兼容性保障

1. **100% 契约兼容**：
   - 严格保留全部原有变量名称（如 `self.var_id`, `self.var_target_method`, `self.raw_text`, `self.aux_checks_text`, `self.captured_controls` 等 40+ 属性）；
   - 保留外部调用的关键回调与契约（`on_import_anchor_control`, `build_control()`, `apply_changes()`, `on_confirm()`, `import_selected()`, `import_all()`）。
2. **轻量测试替身（Headless Mocks）防护**：
   - 在所有逻辑与更新方法中严密增加了 `hasattr(self, ...)` 守卫，确保现有及新增的轻量模拟测试（不启动真实 Tk GUI 循环）均能无障碍运行。
3. **依赖与资源清理保障**：
   - 弹窗关闭及导入时彻底停止后台 `pynput` 监听线程与 `Win32InputHook` 钩子，杜绝线程残留与内存泄漏。

---

## 4. 验证与回归测试

1. **新建专属测试套件**：`tests/test_ui_modernized_dialogs_batch5.py`
   - 包含 25 个精细测试用例，覆盖路径构造、实时探针、三要素验证、属性过滤、定位复制、折叠指南、监听状态转换与候选导入全流程。
2. **相关回归测试**：
   - `tests/test_ui_feedback_regressions.py`：28 passed
   - `tests/test_ui_modernized_dialogs_batch2.py`：25 passed
   - `tests/test_ui_modernized_dialogs_batch5.py`：25 passed
3. **全项目全量测试结果**：
   - 命令：`python -m pytest tests/ -q`
   - 结果：**1491 passed, 1 warning, 176 subtests passed in 96.63s**
   - 达到 100% 绿色无回退。

---

## 5. 变更文件清单

| 文件路径 | 变更类型 | 说明 |
|---|---|---|
| `WT_Flow_Editor.py` | 修改 | 重构 `NewDefaultChainDialog`、`ControlEditorDialog` 与 `SemiAutoInspectCollectorDialog` |
| `tests/test_ui_modernized_dialogs_batch5.py` | 新增 | 第五期重构页面专属单元测试套件（25 个测试用例） |
| `docs/ai_session_22_第五期流程编辑核心页面UI现代化重构_20261002.md` | 新增 | 本次重构设计与沉淀文档 |

---

*遵照 AGENTS.md 规范：AI 仅在 `WT_automation_ai5`（分支 `ai/session-5`）开发与测试，不执行 git push，待人工审查合并。*
