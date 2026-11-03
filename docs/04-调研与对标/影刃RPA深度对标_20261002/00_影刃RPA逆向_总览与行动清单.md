# 影刃 RPA 本机引擎逆向 —— 总览与行动清单

> 配套主报告：`10_REA逆向_影刃核心自动化引擎.md`（§1–§13）。
> 本文件是**一页速览**：结论、已证事实、长处速查、WT 落地优先级、下一步行动。

---

## 0. 一句话结论

**影刃自动化引擎 ≠ 全加密 Python**。实测其结构为：

```
原生 apphost 薄壳 (ShadowBot.exe / Provider.exe)
        │ hostfxr 启动
        ▼
.NET 6 引擎程序集 (ShadowBot.UIAutomation.* / JavaBridge)  ← IL 未混淆，dnSpy 可完整还原
        │ 命名管道 IPC
        ▼
加密 Python 编排层 (winro.pyb)  ← 仅此层不可见
```

- **REA 能做的**：原生 C++ 反编译（Ghidra）、.NET 元数据定位（类型/方法/引用）。
- **REA 不能做的**：把 .NET CIL 反编译成可读 C#（需 dnSpy）；解密 `winro.pyb`（需配套 `pyb.*.pyd`）。
- **直接可读的**：`Resources/Code-Activity/Zh-CN/xbot/*` 桩代码（影刃自带 API 源码 + docstring）。

---

## 1. REA 已证实的关键事实

| # | 事实 | REA 证据 |
|---|---|---|
| 1 | 两个 exe 都是 **.NET 6 apphost 薄壳**，无原生自动化逻辑 | `FUN_140013ecc` 含 `__scrt_is_managed_app()`；`FUN_1400176c0` 含 `apphost`/`6.0.24` 字符串；`FUN_140016af0`=hostfxr 启动 |
| 2 | 原生 C++ 仅 `ShadowBot.NativeHelper.dll`（线程劫持注入）+ 第三方驱动（interception/AutoIt） | `inspect_managed_artifact` 判定 `managed:false`；反编译 `FUN_180018600` 见 `NtSuspendThread→NtGetContextThread→SetContextThread→NtResumeThread` |
| 3 | 命名管道服务端 / 指令分发 / Selector 匹配 / DriverInput 全在 **.NET** `Provider.dll` | 搜索 Provider.exe 无 `CreateNamedPipe`/`hostfxr` 静态导入；`UIAutomation.dll` 引用 `Interop.UIAutomationClient` 等 |
| 4 | Selector 模型覆盖 **11 种后端** | `selector.py`：`_selector_node_types={web,mac,wnd,uia,acc,java,hlay,sap,cv,mobile,linux}` |
| 5 | 元素定位支持 `xpath`/`anchor`/`normal` + 图像选择器 | `selector.Selector`；`win32/image.py` |
| 6 | 选择器可**集中式、版本化**管理并支持 `{{变量}}` 模板 | `SelectorStore` / `RemoteSelectors` |
| 7 | **AI 元素自愈**：定位失败调候选池重定位并回写 | `web/ai_element_repair.py`：`/api/client/aiElement/run/pool`、`save_repair_result` |
| 8 | **显式同步原语** `timeout` 三态（0 不等 / >0 超时 / -1 一直等） | `win32/image.py` `wait_appear`；`win32/window.py` `wait_active`/`wait_close` |
| 9 | 窗口即自动化**作用域**，且可关联到进程 | `win32/window.py`：`Win32Window.get_detail('process_name')`、`find_all(selector)` |
| 10 | 双模式输入 + 输入法鲁棒 | `win32/element.py`：`simulative`、`force_ime_eng`、`clipboard_input` |
| 11 | 统一重试/校验基础设施贯穿全模块 | `xbot/_core/retry.Retry`、`xbot/_core/validation.valid/valid_multi` |
| 12 | 语义缓存 + 事件触发器驱动 | `web/browser.py`：`_semantic_selector_cache`、`trigger_manager` |

---

## 2. 可借鉴长处速查（12 项）

| # | 长处 | 影刃实现落点 | WT 价值 | 详见 |
|---|---|---|---|---|
| 1 | 进程隔离 + 命名管道 IPC 引擎 | 主程序 ↔ 独立 Provider 进程 | 崩溃隔离、跨会话/用户 | 主报 §3/§5 |
| 2 | 统一多后端 Selector + 集中仓库 | `selector.py` / `RemoteSelectors` | 一套语言覆盖全技术栈 | §5/§11.2#2 |
| 3 | **AI 元素自愈** | `ai_element_repair.py` | UI 变更不崩，脚本寿命↑ | §11.2#3 / **§13.1** |
| 4 | 双模式输入 + 输入法鲁棒 | `win32/element.py` | 抗检测 + 中文不串字 | §6 / **§10** |
| 5 | 语义缓存 + 事件触发器 | `web/browser.py` | 智能定位 + 事件驱动 | §11.2#5 |
| 6 | 统一重试/校验 | `xbot/_core/retry`/`validation` | 工程化健壮性 | §11.2#6 |
| 7 | 线程级注入 attach | `NativeHelper.dll` | 覆盖 Java/SAP | §4.2 |
| 8 | 能力广度（桌面/浏览器/Office/移动/库/FTP/AI/HTTP） | `xbot/*` 模块族 | 一个引擎全覆盖 | §11.2#8 |
| 9 | 变量与资源治理 | `primitives.py` `VariableDict`/`ResourceReader` | 数据驱动 + 资产复用 | §11.2#9 |
| 10 | 图像/CV 自动化（一等后端） | `win32/image.py` | 覆盖无句柄界面 | §12.2 |
| 11 | **显式同步原语** | `wait_appear`/`wait_active`/`wait_close` | 等条件替代盲等 sleep | §12.3 / **§13.2** |
| 12 | 窗口/进程作用域 | `win32/window.py` | 元素绑定窗口、进程可追溯 | §12.4 |
| 13 | **Excel 数据驱动** + 内置数据表 | `excel/`（office/wps/openpyxl/auto_check 四后端）、`app/databook.py`（数据表格循环） | 标准数据驱动 RPA（批量录单/对账） | 主报 §14.2 |
| 14 | **AI/OCR/验证码** | `ai/`（阿里/百度/腾讯/自研 + 图鉴打码）、`click_text_in_screen`、`ShadowBot` 票据识别 | 找字即点、多云 OCR 兜底、验证码/票据识别 | 主报 §14.3 |

---

## 3. WT Automation 落地优先级（按收益排序）

1. **【最高】集中式/版本化选择器仓库 + AI 自愈** → 直接提升脚本寿命。路由：先建 `SelectorStore`（XML/库）+ `RemoteSelectors`，再接 `SelfHealingResolver`（§13.1）。
2. **【高】统一多后端 Selector 抽象** → WT 已有元素模型，抽 `IElementLocator` 接口，新增后端不影响上层（§11.2#2）。
3. **【高】双模式输入 + 输入法鲁棒** → 中文环境必做，§10 已给 `InputInjector` 骨架。
4. **【中】显式同步原语 `wait_for`** → 收敛零散 `os_sleep`（§13.2）。
5. **【中】窗口作用域 + 进程关联** → 元素查找绑定窗口句柄（§12.4）。
6. **【中】图像/CV 定位后端** → 模板匹配覆盖无句柄界面（§12.2）。
7. **【中】统一重试/校验装饰器** → 复用 `Retry`/`valid`（§11.2#6）。
8. **【低】进程隔离引擎** → 崩溃隔离（§11.2#1）。

> 所有代码均**仅写入文档作为设计骨架**（§10 / §13），未改动 WT Automation 项目代码。

---

## 4. 下一步行动清单（可执行）

### A. 用 dnSpy 还原 .NET 引擎（性价比最高）
1. 下载 dnSpy，打开 `shadowbot-6.2.21\ShadowBot.UIAutomation.Provider.dll`（同目录有 PDB `ShadowBot.UIAutomation.Provider.pdb`，21,566 符号）。
2. 搜索关键类：`Provider.Common.DriverInput`（多通道输入）、`Provider.UIA*`（定位后端）、`UIAutomation.Tools` 的 `SelectorHelper`（匹配算法）、命名管道宿主类（IPC 协议）。
3. 重点导出：Selector 匹配算法、DriverInput 通道实现、管道帧格式/操作码。
4. 如需原生细节：Ghidra 反编译 `ShadowBot.NativeHelper.dll`（已验证可用，REA 会话可复用）。

### B. 扩读 `xbot` 桩代码（按需求选读，均位于 `Resources/Code-Activity/Zh-CN/xbot/`）
- 数据驱动：`excel/`、`adoapi/`（库连接）
- 移动端：`mobile/`
- AI：`ai/`
- HTTP：`request.py`
- 文本/系统：`strings.py`、`system_utils.py`

### C. 把文档骨架落到 WT（当你决定动手时）
- 多通道输入：`§10` → `wt_automation/input/injector.py`
- AI 自愈：`§13.1` → `wt_automation/locating/self_healing.py`
- 显式同步：`§13.2` → `wt_automation/flow/wait.py`
- **统一 Selector 抽象**：`doc11 §8` → `wt_automation/locating/selector.py`（归一 UIA/CV/Web，接 `wt_flow_locator` + `locate_template_center_by_path`）
- 接 WT 现有元素对象（`.rect`/`.handle`/`.exists()`/`.is_visible()`）即可运行。

---

## 5. 文档索引

| 文件 | 内容 |
|---|---|
| `10_REA逆向_影刃核心自动化引擎.md` | 主报告（§0–§18）：架构判定、原生/托管分界、线程劫持反编译、能力全景、12+2 项长处、WT 适配骨架（§10 输入 / §13 自愈+同步 / §15 数据驱动循环+OCR找字+触发器看门狗）、§16 收尾总结、**§17 结论审计与勘误**、**§18 可靠性内核二挖（错误模型门控 AI 修复 / Retry 双界迭代器 / WebSocket 触发器 / OCR 供应商抽象 / 子会话）** |
| `11_影刃元素抓取存储复用机制_vs_WT.md` | **元素链路深读**：Capture→Store→Use 全生命周期、11 后端 Selector 模型/XML 存储、具体控件操作、**§7 REA 元数据确认的匹配模型(SelectorHelper/SelectorModel/SelectorNode)**、对比 WT 路线、**§8 WT 统一 Selector 抽象骨架** |
| `12_WT改进总蓝图_预审批稿.md` | **预审批实施规划**：Phase 0–3 分阶段措施表（文件级改动点/验收标准）、双实现合并独立项、实施原则（双轨不迁移/主实现唯一/可回滚）、风险表、**审批清单** |
| `00_影刃RPA逆向_总览与行动清单.md` | **本文件**：一页总览 + 行动清单 |
| `01_影刃架构全景与运行机制.md` `02_*` `03_*` | 既有对标报告（架构/指令系统/差距分析） |

---

## 6. 状态更新（本轮）

- **WT Automation 三大能力现状已盘点**（主报告 §15.1）：数据驱动迭代=有（参数表加载期展开）、OCR 找字点击=无、触发器看门狗=无。
- **已为 WT 扩写可运行级骨架（仅落文档、未改项目代码）**：
  - `click_text_on_screen` / `hover_text_on_screen`（OCR 找字点击，§15.3a）
  - `TriggerWatchdog` + `MonitorRecord` + `build_popup_guard`（流程内事件触发器/看门狗，§15.3b）
  - `ParamTableRunner` + `loop_while`（数据驱动循环，§15.2）
- **元素抓取-存储-复用机制已深读**（`11_影刃元素抓取存储复用机制_vs_WT.md`）：用 xbot 未加密源码 + REA .NET 元数据，还原影刃 Capture→Store→Use 全链路、11 后端 Selector 模型、XML 存储格式，并**对照 WT 真实代码**（external_capture 抓取 / 总控件信息.json 存储 / flow_repair LLM 自愈）给出差异与互补补足方案。
- **REA 已把".NET 匹配内核"推到类型模型层**（doc11 §7）：成功枚举 `UIAutomation.dll` 中的 `SelectorHelper`(18)/`SelectorModel`(46)/`SelectorNode`(24)/`SelectorAttribute`(16)/`SelectorOperator`(15)/`UIFramework`(20)/`Anchor`/`Semantic`/`ImageSelector`/`ElementInfo`/`BuildSelectorWithAnchorResponse` 等——**纠正旧说"SelectorHelper 在 Tools.dll"**（Tools.dll 超 REA 元数据预算）；并指出匹配内核存在轻量混淆（如 187 方法的重命名类）。
- **WT 统一 Selector 抽象骨架已落地**（doc11 §8）：把 UIA/Win32/CV/Web 归一为同一 Selector 模型（SelectorNode/Attr/Op/Anchor/SudokuPart + SelectorRepository + SelectorMatcher），对齐影刃 SelectorModel/SelectorHelper，可接 WT 的 `wt_flow_locator`(UIA) 与 `locate_template_center_by_path`(CV)。
- **结论审计完成**（主报告 §17，2026-10-15）：逐条复核既有断言——WT 侧符号级验证（`wt_projection_helpers.py:261`、`wt_flow_locator.py:7797/11190`、`wt_action_schema.py`、`总控件信息.json`、`flow_repair.py`、`param_table_*.json` 全部属实）；影刃侧源码行号/签名核对（11 后端、`selectors.xml`、`select(mode='fuzzy')` 等全部属实）；.NET 侧 REA 复跑（SelectorHelper 归属 `UIAutomation.dll`、Tools.dll/Provider.dll `resource-limit`、内核混淆均确认）。**勘误 2 项**（旧报告"全加密不可见"不成立；"SelectorHelper 在 Tools.dll"已纠正）。**新发现**：`tools/generic_flow/generic_flow_locator.py` 与 `wt_flow_locator.py` 存在同名函数双实现（漂移风险）。
- **BackendResolver 可运行实现已落地**（doc11 §9）：`UiaBackendResolver` 接 `get_flow_control_definition`（控件卡，含 label 软匹配）、`CvBackendResolver` 接 `locate_template_center_by_path`（CV 一等后端）、`control_card_to_selector` 适配器——UIA 与 CV 成为同一 locator 的两种后端。
- **可靠性内核二挖完成**（主报告 §18，2026-10-15）：① 影刃 AI 自愈按**错误类型门控**（`UIAError` 可修复并带 `is_loading_error/is_not_found_error` 分类器；`EngineError` 注释明确"无法使用AI修复"）；② `Retry` 为**双界迭代器原语**（timeout/times/interval=0.2）；③ 触发器运行时为 **WebSocket 事件驱动**（trigger_manager/server + websocket_* + executor）——§15.3b 轮询骨架的 v2 路径；④ OCR 为**供应商抽象**（aliyun/baidu/tencent/shadowbot + captcha）+ 四源，`hover_on_text_in_screen(anchor, occurrence)` 与 §15.3a 骨架签名级一致；⑤ .NET 实用层：`ChildSessionLauncher`（子会话无人值守）、`WindowsVirtualKeyboardTool`、`EngineErrorAnalyzer`/栈分析器。§18.7 已更新补足措施优先级表（7 项）。
- **工具边界补充**：REA `search_strings` 对托管目标不可用（Ghidra 拒 managed / Hopper 仅 mac / IDA 未配置）——`DriverInput` 落点与管道端点验证需本机 dnSpy/ILSpy（§18.6）。
- **IPC 与遥测闭环已实证**（2026-10-15 三挖）：`_core/uidriver.py` 证实 Python→引擎为**命名管道 + `execute(method,args)` 方法名分发 RPC**（`_Connection.connect(pipeid,timeout)`）；`element_stats.py` 证实自愈是**遥测闭环**——`_calculate_selector_hash`（注释要求"shell 侧算法保持一致，否则 key 不一致"）+ `report_normal_error(selector, invalid_reason, element_count)`（失败原因+候选数）+ 失败截图上传 OSS 聚合；`_core/validation.py` 证实**操作后校验**（`valid/valid_multi/ValidPattern`）为一等概念。
- **预审批改进总蓝图已产出**（`12_WT改进总蓝图_预审批稿.md`）：Phase 0（错误码/Retry/Selector 模型，纯新增）→ Phase 1（UIA/CV 定位归一双轨）→ Phase 2（错误门控接 flow_repair + 本地失败遥测 + 候选池）→ Phase 3（触发器事件化/OCR 抽象/类型化 API/多通道输入/子会话评估）+ 独立项（generic_flow 双实现合并）；含验收标准、风险回滚与**逐项审批清单**——等你勾选后才动 WT 代码。
- **剩余高价值动作**：用 **dnSpy** 还原匹配内核的**方法体**（`Utilities.SelectorHelper` 18 方法、混淆的 187 方法内部类 `GuwBZAWohQ92tIs0TO.WqvWUdBjXjZdpSFeKY`、以及 `Tools.dll`/`Provider.dll` 跨进程调度与 `DriverInput`），见 doc11 §7.3。

---

*证据来源：REA（`binary_session` / `open_binary(Ghidra)` / `inspect_managed_artifact`）对 `E:\影刃RPA\ShadowBot\` 的实测；影刃自带 `Resources/Code-Activity/Zh-CN/xbot/*` 桩代码；以及 WT Automation 项目 `E:\My_RF_Project\WT_Automation` 的只读梳理。逆向仅用于改进自有项目 WT Automation，不涉及破解授权、许可绕过或密钥提取。*
