# Inspect 运行逻辑调研手册（基于 UIA 获取控件信息）

> 整理日期：2026-07-24
> 用途：面向 WT_Automation 团队，讲解 Inspect.exe 的运行原理、底层 UIA 机制，以及本项目中对应的实现代码位置，方便排错与扩展。
> 资料来源：微软官方文档（Inspect、UI Automation Tree Overview）、项目内 `uia_tree_dumper`（无头版 Inspect）、`wt_flow_editor_utils.parse_inspect_text`。

---

## 一、Inspect 是什么

- 微软 **Windows SDK** 自带的 GUI 工具（`\bin\<version>\<platform>\Inspect.exe`），随 SDK 安装，无需单独下载，通常无需管理员权限。
- 核心能力：**选中任意 UI 元素 → 查看其辅助功能数据**，同时支持 **UI Automation (UIA)** 与现代框架，也支持老框架 **MSAA**（Active Accessibility）。
- 官方现状：微软文档明确标注其为 **legacy（遗留）工具，推荐用 Accessibility Insights 替代**；但它仍是理解 UIA 运行机制最直观的入口，也是 Accessibility Insights / axe-windows 的 GUI 祖辈。

---

## 二、核心功能清单

| 功能 | 说明 |
|---|---|
| 属性检视 | 显示元素暴露的全部 UIA / MSAA 属性（可配置显示子集，可勾选 "Display unsupported properties" 显示不支持属性）|
| 树导航 | 树视图 + 菜单/工具栏，验证父/子/兄弟等导航关系 |
| 模式交互 | UIA 模式下按元素支持的 Control Pattern 动态列出操作（如按钮出现 Invoke）→ 调用 `IUIAutomationInvokePattern::Invoke` |
| 多视图 | MSAA / UIA 模式；Raw / Control / Content 三种树视图（后两者仅 UIA）|
| 视觉辅助 | 高亮选中元素边框、插入符高亮、信息工具提示、始终置顶 |
| 快捷键 | 即使非激活也能触发，如 `Ctrl+Shift+F4` 对光标下对象设焦点 |
| 配置持久化 | 关闭时保存显示/查看选项，下次启动自动应用 |

---

## 三、UI 组成与菜单

窗口五区：**标题栏(HWND) / 菜单栏 / 工具栏(与菜单一一对应) / 树视图(左) / 数据视图(右，显示选中元素所有属性)**。

关键菜单项：
- **Options**：UI Automation Mode / MSAA Mode、Raw / Control / Content View、Show Highlight Rectangle、Show Information Tooltip、Watch Focus / Caret / Cursor / Tooltips、Show Tree 等。
- **Settings 对话框**：分别选择"主窗口显示"与"工具提示显示"的属性列表；可勾选显示不支持属性。
- **Navigation**：父/子/兄弟导航（随选中位置动态变化）。
- **Action**：通用 Refresh / Focus；UIA 模式下列出元素支持的 Pattern 操作；MSAA 模式下有 Default Action / Select / Extend Selection / Add to Selection / Remove from Selection / SetAccValue / Focused Child / HitTest 等。

---

## 四、底层原理：UIA 如何获取控件信息

### 4.1 三层架构（Provider / Core / Client）

```
[UI 框架 WPF / Win32]  →  Provider(片段 Fragment，实现片段内导航)
        ↓
   UIA Core(UIAutomationCore.dll)  →  整合各片段、维护逻辑树、跨控件导航
        ↓
   Client(Inspect / pywinauto / WT_Automation 脚本)  →  用 IUIAutomation 接口按需取视图
```

- **Provider**：只负责自己"片段（Fragment）"内的元素导航，片段根叫 fragment root（通常挂在一个窗口里）。
- **Core**：利用默认窗口提供者把不同片段拼成完整逻辑树，管理跨控件/跨片段导航。
- **Client**：通过 Core API 取树、读属性、调 Pattern。

### 4.2 UIA 树与三种视图（TreeWalker）

树根 = Windows 桌面，子节点 = 应用窗口，再往下是菜单/按钮/列表等。**树是动态构建的**，随 UI 增删动态变化，客户端通常只构建所需部分。

| 视图 | 过滤条件 | 包含 | 取数方式 |
|---|---|---|---|
| **Raw View** | 不过滤 | 全部元素，最贴合原生编程结构（WPF 与 Win32 按钮结构不同）| `RawViewWalker` |
| **Control View** | `IsControlElement == TRUE` | Raw 子集，交互控件 + 贡献逻辑结构的非交互项（列表头、工具栏、状态栏、对话框静态文本等），排除纯布局/装饰面板 | `ControlViewWalker` |
| **Content View** | `IsControlElement` 且 `IsContentElement == TRUE` | Control 子集，承载真实内容的项（屏幕阅读器最关注；组合框在此仅表示为可选集合，不关心展开状态）| `ContentViewWalker` |

客户端用 `IUIAutomation::RawViewWalker / ControlViewWalker / ContentViewWalker` 得到 `IUIAutomationTreeWalker`，在视图内做父/子/兄弟遍历。视图过滤由 Provider 通过 `IsControlElement` / `IsContentElement` 属性参与。

### 4.3 AutomationElement 与属性读取

树中每个节点是 **AutomationElement**（`IUIAutomationElement`）。Inspect 的"数据视图"本质就是逐个读这些属性：

`Name`、`AutomationId`、`ControlType`、`ClassName`、`BoundingRectangle`、`IsEnabled`、`IsOffscreen`、`IsKeyboardFocusable`、`FrameworkId`、`ProcessId`、`RuntimeId`、`NativeWindowHandle`、`ProviderDescription`、`LegacyIAccessible.*` 等。

### 4.4 Control Patterns（控制模式）

属性之外，UIA 元素可支持 **Pattern**（行为接口）。Inspect 的 Action 菜单对每个支持的 Pattern 列一项，点击即调用对应 COM 接口，如：

- `InvokePattern` → `Invoke()`（按钮点击）
- `ValuePattern` → `SetValue()`
- `ExpandCollapsePattern` → `Expand()` / `Collapse()`
- `TogglePattern`、`SelectionPattern` 等

### 4.5 MSAA 模式（对照）

Inspect 还支持旧版 MSAA：走 `AccessibleObjectFromPoint` 等，暴露 `LegacyIAccessible.Name / Role / State`。这也是为什么 Inspect 输出里会带 `LegacyIAccessible.*` 字段。

### 4.6 元素定位方式

Inspect 把"光标/焦点/鼠标指向"的元素映射成 UIA 元素，主要靠：
1. 鼠标悬停 HitTest；
2. 跟踪键盘/鼠标焦点（Watch Focus，约 1 秒刷新）；
3. 树节点点击；
4. Navigation 菜单。

---

## 五、实现手册：项目里现成的"无头版 Inspect"

`uia_tree_dumper/uia_tree_dumper/Program.cs` 是一个用 C# / .NET UIAutomation 实现的 Inspect 核心（输出 JSON）。它把上面所有原理落成代码，逐段对应——Inspect 在 GUI 里做的每件事，这套代码都用 UIA API 做到了，只是不画界面、只吐 JSON。

### 5.1 定位根元素（`Program.cs:36-74`）

- `--hwnd` → `AutomationElement.FromHandle(targetHwnd)`
- `--pid`  → `AutomationElement.RootElement.FindFirst(TreeScope.Children, new PropertyCondition(AutomationElement.ProcessIdProperty, targetPid))`
- `--title`→ 遍历 `RootElement.FindAll(TreeScope.Children, Condition.TrueCondition)`，按 `child.Current.Name` 做不区分大小写子串匹配

### 5.2 遍历树（`Program.cs:77-104`）

使用 **`TreeWalker.RawViewWalker`**（即 Inspect 的 Raw View），递归 `walker.GetFirstChild(el)` / `walker.GetNextSibling(child)`，带 `maxDepth`（默认 40）与 `timeoutMs`（默认 30000）熔断；外层用 `Stopwatch` 计时，避免遍历卡死。

### 5.3 构建单条记录（`Program.cs:117-187`，对应 Inspect 数据视图）

- **基础属性**：`el.Current` 读取 `ControlType / Name / AutomationId / ClassName / HelpText / IsOffscreen / IsEnabled / IsKeyboardFocusable / ProcessId / BoundingRectangle`（其中 BoundingRectangle 转为 `Rect{X,Y,W,H}`）。
- **Pattern 列表**：`el.GetSupportedPatterns()` → 取每个 Pattern 的 `ProgrammaticName` 去前缀拼接（等价于 Inspect 的 Patterns）。
- **行为取值**：`el.TryGetCurrentPattern(ExpandCollapsePattern.Pattern, out var pat)` 读展开状态；`TryGetCurrentPattern(ValuePattern.Pattern, out var pat)` 读当前值。
- **健壮性**：每个 `el.Current` / Pattern 读取都包 try，越界或不可访问元素返回 `error: "inaccessible"`。

### 5.4 数据模型（`Program.cs:191-213`）

输出 `FlatControl` 记录：`index / depth / parentIndex / controlType / name / automationId / className / helpText / isOffscreen / isEnabled / isKeyboardFocusable / processId / rect / value / patterns / expandState / error`，序列化后以 UTF-8 JSON 写入 stdout。

---

## 六、项目实际消费的 Inspect 字段

`wt_flow_editor_utils.parse_inspect_text`（`wt_flow_editor_utils.py:70-177`）把 Inspect 文本解析成结构化字典，映射的字段正好等于 Inspect 数据视图里的键（见 `wt_flow_editor_utils.py:140-177` 的 key_map）：

```
How found / Name / ControlType / LocalizedControlType / BoundingRectangle
IsEnabled / IsOffscreen / IsKeyboardFocusable / HasKeyboardFocus
ProcessId / RuntimeId / FrameworkId / ClassName / AutomationId
NativeWindowHandle / ProviderDescription
LegacyIAccessible.Name / .Role / .State
FirstChild / LastChild / Next / Previous / Children / Ancestors
```

- `Children` / `Ancestors` 为多行列表，解析器对 `Children` / `Ancestors` 做了多行聚合（`wt_flow_editor_utils.py:121-138`）。
- 原文里 `"property does not exist"` 会被归一为空字符串（`wt_flow_editor_utils.py:119`），这正是 Inspect 对"不支持属性"的占位写法——印证了第四节 4.3 的 "Display unsupported properties" 机制。

---

## 七、本项目"真实抓控件"手段与 Inspect 的关系

| 手段 | 本质 | 与 Inspect 关系 |
|---|---|---|
| `build_control_map_library.py`（pywinauto uia 后端）| 调 UIA/COM 读树 | 同根，无 GUI |
| `tools/external_capture/uiapeek_client.py` | HTTP 调 UiaPeek（开源 UIA 检查器）| 现代版 "Inspect + 录制"，按坐标/焦点 peek 祖先链 |
| `tools/external_capture/axewindows_client.py` | 调 Axe.Windows（AccessibilityInsights 底层）| 拿 Properties + Patterns，补 Pattern 校验维度 |

三者与 Inspect **共用同一套 UIA 树**：Inspect 是 GUI 祖辈，本项目是程序化/无头继承者。其中 **axe-windows 正是 Accessibility Insights 的引擎**——把"FastPass 调研"与"Inspect 调研"串成同一条技术链。

---

## 八、结论

1. Inspect 的本质 = **Windows SDK 提供的 UIA（兼 MSAA）图形化客户端**，底层走 `UIAutomationCore.dll` 的 Provider → Core → Client 三层架构，用 TreeWalker 取 Raw / Control / Content 三视图，读 AutomationElement 属性、调 Control Pattern。
2. 它的"帮助手册"即微软 [Inspect 文档](https://learn.microsoft.com/en-us/windows/win32/winauto/inspect-objects) 与 [UI Automation Tree Overview](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-treeoverview)；**"开发手册"级别的实现本项目里已有**——`uia_tree_dumper/Program.cs` 把核心逻辑完整代码化。
3. 对 WT_Automation 而言，Inspect 不是必须依赖：pywinauto / uiapeek / axewindows 都直接读同一棵 UIA 树，且 `parse_inspect_text` 已能消费 Inspect 文本；Inspect 更适合**人工排错时对照 `uiPath` / 祖先链 / Pattern**。

---

## 附：关键源码行号速查

| 内容 | 文件 | 行号 |
|---|---|---|
| 定位根元素（FromHandle / FindFirst / 标题匹配）| `uia_tree_dumper/uia_tree_dumper/Program.cs` | 36-74 |
| 使用 RawViewWalker 遍历 | `uia_tree_dumper/uia_tree_dumper/Program.cs` | 77 |
| 递归 Walk（GetFirstChild / GetNextSibling + 熔断）| `uia_tree_dumper/uia_tree_dumper/Program.cs` | 81-104 |
| BuildRecord 读属性/Patterns/值 | `uia_tree_dumper/uia_tree_dumper/Program.cs` | 117-187 |
| FlatControl 数据模型 | `uia_tree_dumper/uia_tree_dumper/Program.cs` | 191-213 |
| parse_inspect_text 入口与字段初始化 | `wt_flow_editor_utils.py` | 70-99 |
| "property does not exist" 归一 | `wt_flow_editor_utils.py` | 119 |
| Inspect 字段 → 内部键映射表 | `wt_flow_editor_utils.py` | 140-177 |
| 外部采集适配器说明（uiapeek / axewindows）| `tools/external_capture/README.md` | 全文 |
