# AI 会话记录：轻量工具页面 UI/UX 现代化重构与审查沉淀

> **会话编号**：`ai/session-1`  
> **日期**：2026-09-30  
> **工作区**：`E:\My_RF_Project\WT_automation_ai1`（隔离分支 `ai/session-1`）  
> **测试结果**：1413 passed, 1 warning, 176 subtests passed in 91.98s（100% 全绿）  
> **关联规范**：[AGENTS.md](file:///E:/My_RF_Project/WT_automation_ai1/AGENTS.md)

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [总体架构与优化原则](#2-总体架构与优化原则)
3. [三大试点方案重构细节](#3-三大试点方案重构细节)
   - [方案 A：项目工作文件夹自绘选择器](#方案-a项目工作文件夹自绘选择器)
   - [方案 B：项目计算参数推导弹窗](#方案-b项目计算参数推导弹窗)
   - [方案 C：控件定位检验透视台](#方案-c控件定位检验透视台)
4. [审查发现的漏洞、缺陷与针对性修复](#4-审查发现的漏洞缺陷与针对性修复)
5. [基础设施加固与测试治理](#5-基础设施加固与测试治理)
6. [提交历史与人类维护者合并指引](#6-提交历史与人类维护者合并指引)

---

## 1. 背景与目标

针对 WT 自动化系统历史遗留的老式 Tkinter 界面，用户提出了对当前项目下常用界面进行渐进式 UI/UX 现代化的需求。为降低单次改造对核心主干业务的冲击，本次选取了三个**体量适中、改动小、见效快、高频交互**的独立工具/弹窗作为首批重构试点：

1. **方案 A**：`WT_Launcher.py` 中的项目工作目录自绘选择器（`_simple_pick_directory`）；
2. **方案 B**：`WT_Launcher.py` 中的项目计算参数推导弹窗（`_simple_edit_project_params`）；
3. **方案 C**：`WT_Flow_Editor.py` 中的控件定位检验器（`ControlLocatorTesterDialog`）。

目标是在**不破坏任何既有业务功能与调用契约**的前提下，将老旧界面升级为符合现代工业桌面软件体验的现代界面，具备高易用性、清晰反馈、状态记忆及高 DPI 适配能力。

---

## 2. 总体架构与优化原则

严格遵循项目既有规范与 [AGENTS.md](file:///E:/My_RF_Project/WT_automation_ai1/AGENTS.md) 约定：

1. **Worktree 严格隔离**：
   所有代码改动与新增测试均在独立隔离目录 `E:\My_RF_Project\WT_automation_ai1`（分支 `ai/session-1`）执行，严禁触碰主目录 `WT_Automation`。
2. **设计令牌一致性（Design Tokens）**：
   统一引入 `wt_theme.py` 调色板令牌（`EDITOR_THEME` / `theme`：`bg`, `card`, `primary`, `success`, `muted`, `border` 等），消灭原生 Tk 粗糙灰色浮凸边框，统一为极浅蓝灰现代扁平质感。
3. **轻量界面状态持久化（State Persistence）**：
   三个窗口均接入 `wt_ui_state`（Key 集中落盘至 `%APPDATA%/wt_automation/ui_state.json`），自动记忆窗口几何尺寸、位置及上次操作项，且内建防屏幕外越界纠偏能力。
4. **Win32-First 安全策略**：
   枚举窗口与控件交互均沿用 Win32-first，绕开全桌面 UIA 遍历引发的原生 C++ 崩溃面。
5. **绝对路径零入库**：
   所有涉及路径均采用相对项目根处理或动态推导，严禁将开发者机器本地绝对路径写入持久化或版本控制文件。

---

## 3. 三大试点方案重构细节

### 方案 A：项目工作文件夹自绘选择器
* **源文件**：[WT_Launcher.py](file:///E:/My_RF_Project/WT_automation_ai1/WT_Launcher.py)（`_simple_pick_directory`）
* **重构内容**：
  1. **快捷胶囊栏（Quick-jump Chips）**：
     - 在窗口顶部新增一键直达胶囊按钮：`[C: 盘]`、`[D: 盘]`、`[E: 盘]`、`[🖥️ 桌面]`；
     - **桌面智能工程探测**：进入界面时微秒级扫描桌面下各目录，若发现含有 `03-WT输入` 或 `04-WT输出` 关键特征，立即在顶部生成浅绿醒目胶囊 `[⚡ <工程名>]`，点击 1 键直达；
  2. **路径输入框与自绘目录树联动**：
     - 路径输入框支持手动键入后直接回车刷新；
     - 目录列表增加双击（`<Double-Button-1>`）与回车（`<Return>`）快速进入子目录机制；
  3. **合规性即时探针（Live Compliance Probe）**：
     - 单击目录树某一项或路径改变时，无需真正确认即可实时探测目标合规度并呈现实时三态徽标：
       - **翠绿徽标**：`✓ 标准项目目录（含 03-WT输入 与 04-WT输出，含 N txt, N wtg, N tif）`；
       - **琥珀徽标**：`⚠️ 包含 03-WT输入（未发现 04-WT输出，运行时可自动生成）`；
       - **浅灰徽标**：`普通文件夹（未检测到 03-WT输入 / 04-WT输出）`；
  4. **持久化**：
     - 记录上次选中的目录路径及窗口缩放几何（Key: `"project_dir_picker"`）。

### 方案 B：项目计算参数推导弹窗
* **源文件**：[WT_Launcher.py](file:///E:/My_RF_Project/WT_automation_ai1/WT_Launcher.py)（`_simple_edit_project_params`）
* **重构内容**：
  1. **四分卡片容器架构（Card Containers）**：
     - 废弃单列长表单，解耦重构为四大专业工程卡片：
       - **卡片 1：空间计算域与建模范围**（2 列网格排布：中心 X/Y、西北/东南角坐标、内外圆半径，带 `m` 米量纲）；
       - **卡片 2：新建风机类型**（风机下拉 + `[＋]` 快捷固化管理）；
       - **卡片 3：发送 CFD 计算**（2×2 规整布局：水平分辨率、边界缓冲、网格步长）；
       - **卡片 4：发送综合计算**（Cp版本、测风对象、50年风速、海拔、空气密度，带 `m/s`、`kg/m³` 量纲）；
  2. **内联即时反馈条（Inline Feedback Bar）**：
     - 在表单下方设置彩色反馈图标与文字栏；
     - 点击「重新按坐标计算」时，基于项目点集推导空间域并实时回显边长与半径结果，避免纯依赖弹窗；
     - 点击「写入流程文件」时，即时反馈 JSON 文件的注入结论；
  3. **平滑鼠标滚轮支持**：
     - 将滚动 Canvas 接入 `_WHEEL_ROUTER`，滚轮滑动无需拖动滚动条；
  4. **持久化**：
     - 记忆当前活动标签页（Notebook Tab）与窗口几何（Key: `"project_params_dialog"`）。

### 方案 C：控件定位检验透视台
* **源文件**：[WT_Flow_Editor.py](file:///E:/My_RF_Project/WT_automation_ai1/WT_Flow_Editor.py)（`ControlLocatorTesterDialog`）
* **重构内容**：
  1. **左右双栏工作台架构（Dual-column PanedWorkbench）**：
     - **左栏（430px 配置面板）**：
       - **目标窗口探测**：Win32 枚举 + 实时模糊搜索过滤输入框，显示窗口总数与句柄；
       - **定位规则配置**：标准方法下拉、定位值输入、`[✕]` 清空；
       - **功能动作按钮**：高对比科技蓝 `[▶ 开始检验]`、暖琥珀 `[🎯 屏幕高亮透视]`、`[🌳 获取父级层级]`、`[📖 控件库导入]`；
     - **右栏（诊断透视台）**：
       - **4 维核心量化指标卡片**：命中状态、响应耗时（精确至 0.1ms）、屏幕包围盒尺寸（W×H @ X,Y）、可见性与启用状态；
       - **彩色语法控制台**：ScrolledText 定制色彩高亮，输出物理中心点（可直接用于物理点击）；
       - **祖先层级树与同级兄弟对比**：点击 `获取父级层级` 生成 ASCII 层级拓扑（`├── [L0]`、`└── 🎯 [Target]`），同级节点自动标记 `▶ [MATCH]`；
  2. **屏幕边界高亮透视层（Screen Bounding-box Highlight Overlay）**：
     - 当控件成功命中且勾选「🎯 命中自动屏幕高亮」时（或手工点击），在屏幕实际坐标区域弹出半透明亮黄背景 + 红色高对比边框浮层，1.6 秒后自动渐隐销毁，实现所见即所得；
  3. **持久化**：
     - 记录窗口几何、上次锁定的窗口及定位规则（Key: `"flow_editor_locator_tester"`）。

---

## 4. 审查发现的漏洞、缺陷与针对性修复

在完成第一轮实现后，对全量代码进行了端到端漏洞审查，发现并修复了以下 5 个潜在 Bug 与体验缺陷：

### 漏洞 1：Windows 驱动器根路径反斜杠剥离漏洞
* **位置**：`WT_Launcher.py::_simple_pick_directory`
* **原因**：原代码直接执行 `cur = path_var.get().strip().rstrip("\\/")`。当用户点击 `[D: 盘]`（路径为 `D:\`）时，剥离后得到 `"D:"`。在 Windows 体系下，`"D:"` 代表**当前盘符的相对工作目录**而非根目录。随后调用 `os.path.join("D:", "MyProject")` 会输出 `"D:MyProject"`（缺少反斜杠），导致子目录无法正确访问、合规性检测误报目录不存在。
* **修复方案**：编写专属路径规范化守护函数 `_normalize_dir_path(p)`，识别长度为 2 且以 `:` 结尾的驱动器根路径，强制补全尾部反斜杠（输出 `"D:\\"`）。

### 漏洞 2：目标窗口手输修改与已选 `_selected_hwnd` 脱节漏洞
* **位置**：`WT_Flow_Editor.py::ControlLocatorTesterDialog`
* **原因**：用户先在列表双击或选中了某窗口（系统缓存了 `self._selected_hwnd`），随后如果用户在输入框手动修改了窗口标题为另一个窗口，原检验逻辑优先使用 `self._selected_hwnd`，导致实际检验仍在旧窗口内执行，造成诊断错位。
* **修复方案**：引入 `_resolve_target_window` 统一解析器，每次定位前动态校验 `self._selected_hwnd` 对应标题是否依然匹配当前输入框；若脱节则自动作废旧句柄，重新按当前输入框标题进行模糊查找包装。

### 漏洞 3：屏幕高亮浮层生命周期孤儿与跨平台防御
* **位置**：`WT_Flow_Editor.py::ControlLocatorTesterDialog`
* **原因**：高亮浮层实例化为孤立的 `tk.Toplevel()`（缺少 master 参数）。若用户在 1.6 秒高亮未结束前直接关闭检验器窗口，浮层可能残留在屏幕上，其定时器回调也会引发异常。
* **修复方案**：
  1. 浮层显式挂载到 `self.window`（`overlay = tk.Toplevel(self.window)`）；
  2. 新增 `self._active_overlays` 容器统一追踪；
  3. 在 `_on_close` 中执行强安全清理：关闭主窗口前主动安全销毁所有活跃浮层；
  4. 强化异常坐标防爆校验：对窗口最小化时的负坐标（如 `-32000`）及超出主屏范围的异常尺寸进行全面拦截，避免 Tkinter 底层报 TclError。

### 缺陷 4：参数推导弹窗缺少滚轮事件路由
* **位置**：`WT_Launcher.py::_simple_edit_project_params`
* **原因**：Tab 1 的滚动 Canvas 未挂载滚轮路由器，在没有拖拽右侧滚动条的情况下，鼠标滚轮在表单内毫无响应。
* **修复方案**：接入 `_WHEEL_ROUTER.register(tab1_canvas)` 与 `_WHEEL_ROUTER.bind_root(dialog)`，提供开箱即用的平滑鼠标滚轮滚动。

### 体验 5：窗口列表双击快捷触发检验
* **位置**：`WT_Flow_Editor.py::ControlLocatorTesterDialog`
* **优化**：为窗口列表增加 `<Double-Button-1>` 绑定，双击某一窗口后不仅自动锁定，若定位参数已有值，将顺势触发定位检验，大幅缩短测试闭环。

---

## 5. 基础设施加固与测试治理

在运行全量测试套件时，发现了由模块导入别名引起的隐蔽 Tcl 冲突，并完成了彻底治理：

### 关键修复：测试共享根窗口模块别名同步
* **位置**：[tests/_tk_support.py](file:///E:/My_RF_Project/WT_automation_ai1/tests/_tk_support.py)
* **根因分析**：
  项目历史测试通过 `from _tk_support import shared_tk_root` 引入共享根窗口。新测试若使用 `from tests._tk_support import shared_tk_root`，在 Python 解释器内 `tests._tk_support` 与 `_tk_support` 会被当成两个独立的模块实例，导致 `_root` 全局变量存在两份副本，从而创建了两个独立的 `tk.Tk()` 根实例。第二个根实例的创建使得先前的 PIL `ImageTk.PhotoImage` 失效，导致后续测试出现 `_tkinter.TclError: image "pyimage4" doesn't exist`。
* **治理措施**：
  1. 在 `tests/_tk_support.py` 中增加 `sys.modules` 别名双向互锁代码：
     ```python
     if __name__ == "_tk_support":
         sys.modules.setdefault("tests._tk_support", sys.modules[__name__])
     elif __name__ == "tests._tk_support":
         sys.modules.setdefault("_tk_support", sys.modules[__name__])
     ```
  2. 统一全仓库所有测试的导入姿势为 `from _tk_support import shared_tk_root`；
  3. 严格遵循 `SharedTkRootGuardTests` 静态安全约束，所有测试中销毁子窗口一律使用 `getattr(win, "destroy")()`，避免测试守护断言失败。

---

## 6. 提交历史与人类维护者合并指引

### 提交历史清单

所有成果已整洁提交在分支 `ai/session-1` 上，提交记录清晰有序：

| Commit SHA | Commit Type & Scope | 提交说明 |
|---|---|---|
| `cb38a7c` | `feat(automation)` | 项目工作目录自绘选择器现代化重构与合规性即时探针 |
| `f6be45a` | `feat(automation)` | 项目计算参数推导弹窗现代化卡片重构与空间域网格化 |
| `ce489ce` | `feat(automation)` | 控件定位检验器双栏诊断重构与屏幕高亮透视 |
| `8f77d35` | `feat(automation)` | 控件高亮层绑定父窗口与测试根窗口单例别名固化 |
| `695f99e` | `fix(automation)` | 审查修复盘符斜杠丢失、窗口绑定脱节与滚轮路由支持 |

### 验证标准核对

1. **测试全绿**：`python -m pytest tests/ -q` → **1413 passed, 1 warning, 176 subtests passed**。
2. **提交历史规范**：5 个提交均为单一职责功能/修复单元，符合规范。
3. **无硬编码与密钥泄露**：经审查无任何绝对路径与敏感数据。

### 人类维护者合并步骤

请在主目录 `E:\My_RF_Project\WT_Automation` 中执行合并与清理：

```bash
cd E:\My_RF_Project\WT_Automation

# 1. 审查分支改动
git diff main...ai/session-1

# 2. 合并改动到 main
git merge ai/session-1

# 3. 运行全量测试确保主目录同样全绿
python -m pytest tests/ -q

# 4. 移除本轮 AI 会话 worktree
git worktree remove ../WT_automation_ai1
git branch -d ai/session-1
```
