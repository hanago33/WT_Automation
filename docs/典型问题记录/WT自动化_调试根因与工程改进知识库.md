# WT 自动化 · 调试根因与工程改进知识库

> 面向人的综合知识库。内容来自两条线：①项目内 10 份 `docs/debug/debug-*.md` 调试记录；②围绕定位/执行/转换链路的工程改进对话。
> 目的：让后续类似问题能快速对号入座、复用修复范式；也作为 Agent skill（`.codebuddy/skills/wt-automation-lessons/`）与内置 skill 的同源出处。

---

## 一、核心心智模型（先读这个）

1. **"动作执行成功" ≠ "业务生效"。** 日志打印"点击成功/输入成功"只代表控件被操作，不代表 UI 真的响应。必须校验状态变化（窗口出现、值提交、行新增）。绝大多数疑难 bug 都是"假成功"。
2. **报错步往往是连带受害者，不是首因。** step_39 报错，真因常在 step_36→37 的某个假成功。定位时先回溯到"第一个界面没按预期变化"的步骤。
3. **运行时定位是"全遍历 + 打分取最高"，不是条件查询。** `window.descendants()` 逐个 `get_control_definition_match_score` 打分，分最高者胜。因此"评分过宽"会误命中，"缓存未校验归属"会命中错对象。
4. **定位是分层降级的**：复合精确定位 → ui_path 路径对齐 → 模板图像 → 坐标。越靠后越脆，命中靠后层要视为"退化告警"并触发自愈/反馈。
5. **修复要最小化 + 取证驱动。** 先最小插桩（ndjson）证伪假设，再做最小改动，最后 pre-fix / post-fix 对比验证。不要凭猜叠加逻辑。

---

## 二、运行时根因模式库（症状 → 根因 → 修复范式）

### 模式 A：假成功 · 命中文本展示层
- **症状指纹**：日志成功但界面无任何变化；命中控件 `controlType=Text/TextBlock`，`IsKeyboardFocusable=False`，无 `Invoke/Selection/Value/Toggle` 任一模式。
- **根因**：打分命中了只读的显示层文字，而非真正可交互控件。
- **修复范式**：为该步补真实可点击目标或改用 `click_relative_region`；在评分/候选里降权纯展示型控件。
- **代码锚点**：`click_flow_control`、`get_control_definition_match_score`。
- **来源**：`debug-private-group-click.md`。

### 模式 B：假成功 · 输入未提交
- **症状指纹**：输入这步"成功"，但下一步依赖该值的操作失败，或业务未生效；常见于日期/高度/路径等文本框。
- **根因**：值写入了控件但未失焦/未回车，程序未提交。
- **修复范式**：输入动作补 `postInputKeys:{TAB}`（或 `{ENTER}`）强制提交与失焦。
- **代码锚点**：步骤 `postInputKeys`、`flow_definition.json`。
- **来源**：`debug-step37-add-data-miss.md`、`debug-time-series-input-regression.md`、`debug-default-height-relative-input.md`。

### 模式 C：错窗口 · 无标题主窗冒充弹窗
- **症状指纹**：相对区域点击落到主窗口（如 `Window_Main`，rect≈`(0,0,2560,1516)`）而非目标弹窗。
- **根因**：空标题 WPF 窗口评分过宽，被当成候选弹窗。
- **修复范式**：前台窗口优先；空标题窗口只在"确为前台"时作兜底；收紧标题匹配评分。
- **代码锚点**：`find_flow_window_for_relative_region`、`score_window_against_spec`。
- **来源**：`debug-add-data-false-hit.md`。

### 模式 D：矩形基准漂移
- **症状指纹**：整段相对区域点击统一偏移；外框 rect 与内容区 rect 不一致。
- **根因**：`rectangle()` 返回的基准不稳定（外框 vs 客户区）。
- **修复范式**：优先用原生窗口句柄 `GetWindowRect` 取稳定基准。
- **代码锚点**：`get_wrapper_rectangle`。
- **来源**：`debug-relative-region-offset.md`。

### 模式 E：缓存污染 · 未校验窗口归属
- **症状指纹**：改了评分逻辑仍反复误命中同一个错对象；换窗口后仍拿旧结果。
- **根因**：`FLOW_CONTROL_CACHE` 命中未校验当前窗口归属。
- **修复范式**：缓存键/命中处加"窗口归属校验"，归属不符即失效重查。
- **代码锚点**：`get_cached_flow_control`。
- **来源**：`debug-private-group-click.md`（第 3 轮）。

### 模式 F：同名/同类控件误命中（→ found_index 消歧）
- **症状指纹**：一排同 `control_type`、`name` 通用（如"列表项"）的控件里点错序号；`automation_id` 含 `_5`，版本升级后变 `_6`，仅靠 id/name 全失效。
- **根因**：缺少稳定的"同级序号"消歧手段；ancestors 仅作加分项。
- **修复范式**：录制序号 `Name||ControlType#[范围,N]` 保留为 `inspectData.foundIndex` → 运行时 `get_wrapper_found_index`（父容器内同类同级 0 基序号）→ 作**最低优先回退候选** + 打分 **+12 并列消歧**（永不覆盖可靠 id/name）。
- **代码锚点**：`wt_flow_locator.py:get_wrapper_found_index / wrapper_matches_locator / build_common_locator_candidates / get_control_definition_match_score`；`flow_recorder_converter.py:_extract_segment_found_index`。
- **来源**：`debug-click-9-miss.md`、`debug-cft02-step16-drift.md` + 本轮改进。

### 模式 G：连带失败 · 首因≠报错步
- **症状指纹**：末尾某步报"找不到控件"，但该步定位本身没问题。
- **根因**：上游某步假成功（未真正打开面板/未提交），导致后续界面前置条件不满足。
- **修复范式**：回溯到第一个"界面未按预期变化"的步骤修复；不要在报错步打补丁。
- **来源**：`debug-add-data-false-hit.md`、`debug-step37-add-data-miss.md`。

### 模式 H：原生崩溃 · comtypes GC 释放失效 COM 指针（段错误/堆损坏）
- **症状指纹**：日志在"开始执行步骤"后**直接**"自动化流程执行失败"，既无"步骤结束"也无"错误："；启用 faulthandler 后出现 `Windows fatal exception: access violation` 或 `code 0xc0000374`（堆损坏），调用栈顶部是 `Garbage-collecting` → `comtypes/_post_coinit/unknwn.py: Release` → `__del__` → `pywinauto/uia_element_info.py: elements_from_uia_array`。
- **根因**：pywinauto/comtypes 的 UIA 元素数组在**垃圾回收**时释放 COM 对象（`__del__` → `Release`），若目标软件 UIA provider 已失效，会 `Release` 一个失效指针 → 段错误/堆损坏。Python 的 try/except/finally **无法捕获**，进程直接死亡，故无任何错误详情。可发生在**任何** `descendants()` / `children()` / `FindAll` / Raw View 遍历处（`_find_label_rects_for_wrapper`、`_iter_raw_view_guided_candidates` 等），逐个打补丁治标不治本。
- **修复范式**：**在 UIA 遍历期间禁用 GC**（`gc.disable()`），遍历结束恢复（`gc.enable()`）。最稳妥是**流程级**禁用：`run_automation` 入口 `gc.disable()`、`finally` 里 `gc.enable()` + `gc.collect()`，覆盖定位/点击/键入/下拉所有 UIA 操作；`find_flow_control` 入口再加一层包装函数兜底（覆盖编辑器等其他入口）。同时**启用 faulthandler**（`faulthandler.enable(file=sys.stderr, all_threads=True)`）以便原生崩溃时 dump 调用栈定位。
- **代码锚点**：`WT_AUT_recorded.py : run_automation`（流程级 GC 禁用）、`wt_flow_locator.py : find_flow_control`（包装函数）、`wt_flow_locator.py : _find_label_rects_for_wrapper`。
- **来源**：2026-08-19 step_1/step_2 崩溃排查。

### 模式 H-2：原生崩溃 · 跨线程 Tk/COM 收尾（步骤与报告全 success，但 Launcher 报"执行失败"）
- **症状指纹**：日志里每一步 `status=success`、末尾"运行结果摘要已写入"也完整打出，`logs/run_reports/*.json` 与 `run_status.json` 均为 `status: success`，**但 Launcher 仍提示"========== 自动化流程执行失败 =========="**，监测窗口随即瞬间关闭；子进程退出码非 0（如负值 `-1073741819` = `0xC0000005`），却无任何 "错误：" 或 Python traceback，faulthandler 也无 dump（崩在 Tcl/Tk 或 COM 的 C 代码，不在 Python 栈上）。
- **根因**：`WT_Launcher._handle_process_exit` 以**子进程退出码**判断成败（`return_code == 0` 才显示完成），而运行报告 status 与退出码是**两套信号**。崩溃发生在**报告落盘之后**的收尾阶段，主要触发点：
  1. **跨线程 Tk 重入（头号原因）**：`run_automation` 在后台 `automation_thread` 中执行，而成功路径的 `monitor_window.set_success()` 内部调用 `self.root.update()`、`log()` 也调 `self.root.update()`、`_update_progress_title` 直接调 `monitor_window.root.title()`。从非主线程调用 `root.update()` 会**递归进入 Tcl 事件循环**，Tcl 解释器被两个线程同时使用 → access violation 原生崩溃。`root.title()` 等跨线程 Tk 调用同样属未定义行为。这是"报告已 success、随后瞬间失败"的最典型原因。
  2. **跨线程 COM 创建+调用（本次 step_6 复现的真因）**：`_TaskbarProgress` 对象实际在**后台自动化线程**里由 `run_automation` 内的 `_init_taskbar_progress()` 创建（`CoCreateInstance` 自动 `CoInitialize(MTA)` 拿 STA 对象指针），而成功/失败收尾经 `_ui_safe_call` 又把它调度回**主线程**调用 `SetProgressValue/SetProgressState`（ctypes vtable、无 marshalling），形成"后台创建、主线程调用"的跨公寓直连 → access violation 原生崩溃。旧版文档写"对象在主线程创建"与代码实际不符，正是 H-2 修复被漏掉的关键。
  3. `finally` 里 `gc.collect()` 一次性回收累积 comtypes/UIA 对象，以及进程退出时 `Py_Finalize` 释放失效 COM 指针（模式 H 原始机制）。
  Python 的 try/except/finally **无法捕获**原生崩溃，进程直接死亡 → 退出码非 0、监测窗口随进程消失。
- **修复范式**（三层）：
  1. **Tk 跨线程一律调度到主线程**：MonitorWindow 的 `log/update_status/set_success/set_error` **去掉 `self.root.update()`**（Tk 会在 idle 周期自动刷新，无需手动 pump）；新增 `_ui_safe_call(callback)`（有 mainloop 时 `root.after(0, cb)`，否则直调兜底），所有后台线程中的 `monitor_window.*` 与 `_update_progress_title`/`_update_taskbar_progress` 调用统一经它调度。**注意**：`_TaskbarProgress` 这个 COM 对象本身也必须在【主线程】创建——monitor 模式在 `main()` 的 `MonitorWindow()` 之后、启动 daemon 线程之前预先调一次 `_init_taskbar_progress()`，`run_automation` 内检测到已存在会跳过；否则"后台线程创建、主线程调用"的跨公寓 ctypes COM 直连仍会崩溃（本次 step_6 真因）。
  2. 收尾只恢复 GC、不再手动 `gc.collect()`；`main()` 两分支末尾用 `os._exit(exit_code)` 硬退出跳过 `Py_Finalize`，成功 0 / 异常 1。
  3. 保留 faulthandler 以便意外原生崩溃时 dump 栈。
- **代码锚点**：`WT_AUT_recorded.py : _ui_safe_call / log_step / _record_step_result / MonitorWindow(log,set_success,set_error,update_status) / run_automation(成功·失败收尾) / main()`、`WT_Launcher.py : _handle_process_exit`（退出码判断）。
- **来源**：2026-08-19 step_4/step_5 与 2026-08-20 step_6 启动步骤测试（步骤全成功仍报失败；第二轮定位到跨线程 Tk 重入为真因）。

### 模式 H-3：原生崩溃 · run_automation finally 重新启用 GC（结束/异常 unwind 阶段收集 comtypes）
- **症状指纹**：日志里报告**已落盘**（"运行结果摘要已写入"打出，可能 status=failed 因为前面有步骤失败），随后立刻 `Windows fatal exception: access violation`，faulthandler 栈：`Garbage-collecting` → `comtypes/_post_coinit/unknwn.py: Release` → `__del__` → `WT_AUT_recorded.py: _run_automation_and_mark`（daemon 线程，run_automation 调用处）→ `threading.py: run`。
- **根因**：`run_automation` 入口 `gc.disable()` 禁用 GC，但 `finally` 里 `gc.enable()` **重新打开 GC**。当流程失败 `raise` 后异常 unwind 到调用方（`_run_automation_and_mark`），异常对象/栈帧持有的 comtypes/UIA 对象（`__del__ → Release`）在**下一次 GC 触发**时被收集 → Release 失效 COM 指针 → 原生崩溃。此时 mainloop 还在运行、`os._exit` 还未执行，进程以非 0 退出码死亡 → Launcher 误判失败。与 H-2 的区别：崩溃点在 daemon 线程的 run_automation **结束/异常路径**，而不是 Tk 跨线程收尾；触发源是 GC 被重新启用。
- **修复范式**：**`run_automation` 的 finally 不再 `gc.enable()`，保持 GC 禁用直到进程退出**。本进程是专用自动化子进程（任务队列 worker 也是 subprocess 独立进程），GC 由 `main()` 两分支在 `os._exit` 前保持禁用即可；`_run_automation_and_mark` 的 finally 也加 `gc.disable()` 兜底。若未来确需同一进程内多次调用 run_automation，应在**调用方**显式恢复 GC，而不是在 run_automation 内恢复。
- **代码锚点**：`WT_AUT_recorded.py : run_automation(入口 disable / finally 保持禁用) / _run_automation_and_mark(finally disable) / main(两分支 os._exit 前 disable)`。
- **来源**：2026-08-20 step_7~step_11 启动步骤测试（step_8 失败后崩溃；faulthandler 栈定位到 finally 的 gc.enable()）。

### 模式 I：假失败 · 自动值断言对"读不到值的控件"误报（PART_ContentHost / PART_DropDownButton / Text 标签 / 无消歧 textbox）
- **症状指纹**：键入/下拉动作日志显示动作成功（"已通过流程链路匹配输入文本: text=Test1"、"键入过滤后点击选中下拉项"），随后 `等待步骤续跑条件: ... condition=value_equals, expectedValue=...` 超时失败，步骤被判 failed；且每次续跑等待都重新做一次全量定位（单步拖到 60-90 秒）。涉及控件：`PART_ContentHost`（Pane）、`PART_DropDownButton`（下拉展开按钮）、`全文检索,Text`（文本标签）、无 label 消歧的 `textbox`。
- **根因**：`_resolve_continue_when` 对 `type_text`/`send_keys`/`select_dropdown_item_runtime` 等输入/选择动作**自动生成 `value_equals` 值断言**（防假成功）。但以下控件即使动作成功也**读不到输入结果**，断言必然假失败：
  1. `PART_ContentHost`：WPF TextBox 内部编辑宿主（Pane/ScrollViewer），自身无 ValuePattern，父链也常读不到 TextBox 值；
  2. `PART_DropDownButton`：下拉框的展开按钮，无 ValuePattern；
  3. Text/TextBlock 标签（如"全文检索"）：读到的不是输入框的值；
  4. 无 label_text 消歧的裸 `textbox`：目标可能是多个同名输入框之一，读到的可能是错误/离屏控件。
- **修复范式**：自动值断言前先判断控件是否"可可靠读值"，不可读则**跳过自动值断言**（动作本身成功即视为通过）。`_is_unreadable_value_control` 依据 targetValue/targetMethod/inspectData 识别上述特征；`_is_internal_content_host_control` 专门识别 PART_ContentHost。
- **代码锚点**：`wt_flow_executor.py : _resolve_continue_when / _is_unreadable_value_control / _is_internal_content_host_control`。
- **来源**：2026-08-19 step_2/step_3 与 2026-08-20 发送综合计算全流程（step_3/7/9/19 假失败，单步拖 60-90 秒）。

### 模式 J：性能 · label 矩形全树扫描每步重复支付（整树 20-36 秒）
- **症状指纹**：日志 `[定位耗时] ... 整树=19000~36000ms`，fast 阶段毫秒级命中或 FindAll 阶段命中，但 `t_descendants_ms` 仍高达 20-36 秒；27 步流程大量时间耗在定位上。
- **根因**：`wrapper_matches_label_text` 里 `_find_label_rects_for_wrapper` 对候选做 **parent/top_window 全子树扫描**（巨大 WPF 窗口 20-36 秒），且：
  1. 廉价兄弟 TextBlock 匹配（`_match_sibling_text_block_label`）排在全树扫描**之后**，多数场景被跳过；
  2. label 矩形缓存（按 顶层窗口句柄+labelText）在 `find_flow_control` **每次调用开始时硬清空**，同窗口连续步骤每步重复扫描。
- **修复范式**（三层）：
  1. `wrapper_matches_label_text` 中把**兄弟 TextBlock 匹配提前到全树扫描之前**（WPF 标签+控件多为同层兄弟）；
  2. label 矩形缓存改为 **TTL 软失效（30s）跨调用保留**，`_label_rect_cache_reset` 保留硬清空语义（测试隔离用），`find_flow_control` 不再每步硬清空；
  3. `_iter_raw_view_findall_candidates` 对带 label_text 的候选先做 **Raw View 兄弟标签预过滤**（毫秒级）再完整评分，避免对海量同名候选逐个触发全树扫描。
- **代码锚点**：`wt_flow_locator.py : wrapper_matches_label_text / _match_sibling_text_block_label / _find_label_rects_for_wrapper / _label_rect_cache_* / _iter_raw_view_findall_candidates`。
- **来源**：2026-08-20 发送综合计算全流程（step_3/9/16/17 整树 27-38 秒）。

### 模式 K：性能 · EnumWindows 传参错误 + raw_view 迭代 None（窗枚举 3 秒 + 定位链提前炸穿）
- **症状指纹**：日志 `[FlowLocator] 窗口过滤严格无命中，回退前置窗口单候选` 每步反复出现数十次；`窗枚举=3000ms+`；失败步 `last_error='NoneType' object is not iterable` + traceback 指向 `wt_flow_locator.py:6462 for candidate in _iter_raw_view_findall_candidates(...)`。
- **根因**（两个叠加）：
  1. `_enum_visible_mup_win32_windows()` 的 `EnumWindows(enum_proc, 0)` **误传类型工厂 `enum_proc` 而非回调实例 `_callback`** → ctypes 抛 `ArgumentError` 被 `except Exception: return []` 吞掉 → 函数**永远返回空列表** → MUP 主窗口永远进不了候选 → 每次定位都走"严格标题无命中 → 回退前置窗口单候选"（前置窗口是 Launcher/监视器，扫描必然空转），窗枚举 3 秒+；
  2. `_iter_raw_view_findall_candidates` 在 pywinauto import 失败分支 `return`（返回 None）而非 `return []` → 调用处 `for candidate in <None>` 抛 `TypeError` → **整个定位链在该步骤提前炸穿**（fast 命中/descendants 兜底/JSON 兜底全被跳过）。
- **修复范式**：① `EnumWindows` 必须传 `_callback` 实例（同文件其余两处 `_enum_titled_top_level_candidates` 等已是正确写法，可对照）；② 所有"返回候选列表"的函数在异常/提前返回路径**一律返回 `[]` 而非 None**，调用方直接迭代返回值时永不炸 TypeError。
- **代码锚点**：`wt_flow_locator.py : _enum_visible_mup_win32_windows`（EnumWindows 传参）、`_iter_raw_view_findall_candidates`（import 失败返回 []）。
- **来源**：2026-08-20 step_3 键入-综合描述持续定位失败；UIA 探针实测 `descendants=0`、`_enum_visible_mup_win32_windows()` 返回 `[]` 而 Win32 EnumWindows 能枚举到 MUP 主窗口（hwnd=0x162170c, pid=18540）。

### 模式 L：fast 查询被 SVG path name 劫持（automation_id 分支被 elif 跳过）
- **症状指纹**：图标/图形按钮（WPF Path 图标）步骤 fast 阶段空转超时（2-3s）后掉进整树，整树/JSON 也失败，`windows=1 未找到匹配控件`；控件定义 `name`/`inspectData.name` 是超长 SVG/Geometry path（`M21032.418,1987.8691C21025.985,...` 几百到几千字符），而 `automationId` 是稳定唯一值（如 `WRAAnalysisReferenceIEC_Button_GoBack`）。
- **根因**：`build_fast_locator_queries` 的优先级是 `if name: (name,...) elif automation_id: ...`——只要 name 非空就走 pywinauto `descendants(title=超长SVG)`（每次属性比较都是字符串匹配，慢且不稳），稳定快速的 UIA 原生 `FindAll(AutomationId)` 分支被 `elif` 跳过。运行日志特征：`快查=2xxx ms`（deadline 空转）+ `整树=xxx ms` + `JSON=xxx ms` 全失败。
- **修复范式**：① fast 查询 **automation_id 优先**（UIA FindAll 精确毫秒级）；② name 仅作补充查询，且 `_is_svg_path_name()` 识别超长 SVG/Geometry path 时跳过 name 查询（automation_id 已覆盖）；③ class_name 兜底仅在无 automation_id 且无有效 name 时走。
- **代码锚点**：`wt_flow_locator.py : build_fast_locator_queries / _is_svg_path_name`。
- **来源**：2026-08-20 step_18 点击-返回按钮（WRAAnalysisReferenceIEC_Button_GoBack）定位失败 4.7s；控件库 `name` 为 SVG path 图标按钮。

### 模式 K2：下拉枚举漏 Popup · 前置步骤已展开时窗口列表不含 Popup
- **症状指纹**：`select_dropdown_item_runtime` 日志 `运行时下拉项未命中且未枚举到候选项: ... rawProbe={"count": 0, "samples": []}`；前置步骤（如 step_16）已点击展开下拉框（toggle=On），本步骤（step_17）枚举选项却 count=0。
- **根因**：`select_dropdown_item_runtime` 的窗口收集（`_collect_dropdown_windows`）只在 `should_click=True`（本次点击展开）分支执行；当前置步骤已把下拉展开（toggle 已 On）时，`should_click=False`，Popup 窗口未并入枚举窗口列表 → RadComboBoxItem 枚举不到。
- **修复范式**：无论本次是否点击展开，都重新收集并合并 Popup 窗口到 `dropdown_windows`（在检查 toggle 之后、点击分支之前统一做）。
- **代码锚点**：`wt_flow_locator.py : select_dropdown_item_runtime`（展开分支的窗口收集逻辑）。
- **来源**：2026-08-20 发送综合计算 step_16/step_17（IEC 参考下拉项未命中）。

### 模式 L：同名/同类模板复制控件被"字段标签"污染 → 按面板标题消歧
- **症状指纹**：测风点/风机/结果点/绘图等各节点共用同一套图标按钮（`InterestAreas_Button_Add/Edit/Delete/Import/ToggleTileState`，UIA name 是 M6/M19/M20… SVG path，功能语义靠 helpText/functionText），流程"猜不到/区分不了哪个节点的按钮"；主库对应条目 `relatedLabelName`/`labelText` 被污染成 `载入/计算尾流效应/类型/高度 (m)` 等字段标签而非节点名。
- **根因**：采集端 `_extract_panel_title` 取"父容器内**第一个**非 SVG 短文本兄弟"，Edit/Delete/Import 的同父容器里还有字段标签兄弟排在前面，取到错误标签；合并工具 `_discriminator` 读 labelText 分桶 → 没按节点分桶。（Add 的唯一短文本兄弟恰是标题，故正常。）
  - **「合并入库」实际丢失的另有其处**：「📥 合并入库」对话框走的是**独立的去重路径** `_merge_dedup_key`（`build_control_map_library.py`）与 `control_live_detector.py : _build_dedup_key`，其键为 `(aid, automationId, controlType, name)`，而各节点图标按钮 `name` 为空（仅是相同 SVG path）→ 8 个节点键完全相同被误并为 1 条。canonical `run_merge`（`normalize_control`）虽已按节点分桶，但对话框路径并未，故"重跑合并入库又没了"。
- **修复范式**：`_extract_panel_title` **优先返回 automationId=`InterestAreasView_Tile_Header` 兄弟的 name**（权威面板节点名），找不到才回退旧逻辑；合并工具 `normalize_control` 对 `InterestAreas_Button_*` 用该 `panel_title` 统一覆盖 `relatedLabelName`/`labelText`，无 TileHeader 时回退 `targetValue` 第 3 段节点名。对话框两条去重路径的**三种去重模式**（`aid` / `uiPath` / `name+ct`）均追加 `"ia:<节点>"` 区分符（优先 TileHeader 面板节点名 → labelText/relatedLabelName → 兜底 rtv 第 3 段节点名），与 canonical 判定一致。TileHeader 兄弟查找靠 `parentIndex` 共享（按钮与同面板标题同父），可覆盖旧采集格式（`labelText` 为空、rtv 第 3 段为数字 `,1/,2/,3`）。注意 uiPath 模式下各节点按钮共享同一 uiPath（`...MUPMSCInterestAreasViewModel > <SVG-path>`），此前 7 个节点被并成 1 条，追加标签后同样按节点区分。
- **代码锚点**：
  - 采集端：`build_control_map_library.py : _extract_panel_title`、`_disambiguate_duplicate_locators`。
  - canonical 合并：`tools/merge_standard_control_library.py : load_all`（`panel_title_by_parent`）/ `normalize_control`（`_discriminator`）。
  - **合并入库对话框去重（本项目真正丢失点）**：`build_control_map_library.py : _merge_dedup_key` + `_interestarea_node_label` + `_build_ia_panel_title_map`；`control_live_detector.py : _build_dedup_key` + `_interestarea_node_label` + `_build_ia_panel_title_map`。
- **来源**：2026-08-20 全量修复 → `docs/InterestAreas控件按节点消歧修复记录_20260820.md`。

### 模式 M：多选下拉 CheckBox · 等级文本在子节点（name 空 → 误勾/定位失败）
- **症状指纹**：Telerik 多选下拉（如热稳定度 `MTDGroupComboBoxMultiSelection`）展开后有 10 个同 automationId 的 CheckBox，等级文本在**子节点 Text** 上、checkbox 自身 UIA Name 为空。
  - 用 `name,control_type` 定位：name 匹配失败 → automationId 平局 → **点错等级**（曾误勾 0 和 3），precondition 读到错误 checkbox 的 toggle → 未识别已勾选。
  - 用 `automation_id,control_type,label_text`：fast FindAll 的 **Raw View 兄弟标签预过滤**只查兄弟节点 → 10 个 checkbox **全被过滤** → `未找到匹配控件`（日志指纹：`快查=2xxx ms` + `整树/JSON 全失败`）。
- **根因**：等级文本在 checkbox **子节点**而非自身/兄弟；Raw View 预过滤仅做兄弟匹配。
- **修复范式**：① `_raw_element_child_text_matches`（Raw View 子节点树深度≤2 文本匹配）并入两处 FindAll 候选的 label 预过滤（兄弟→子节点双路）；② `wrapper_matches_label_text` 增 `_match_child_text_block_label`（子 Text/TextBlock/Static/Label 匹配）；③ `wrapper_matches_locator` name 分支回退 `get_wrapper_runtime_text_candidates`（含子节点文本）；④ 流程侧 `targetMethod="automation_id,control_type,label_text"` + `labelText=等级文本`，precondition `{"condition":"toggle","expected":"off"}` 实现幂等勾选。
- **代码锚点**：`wt_flow_locator.py : _raw_element_child_text_matches / _iter_uia_findall_by_automation_id / _iter_raw_view_findall_candidates / _match_child_text_block_label / wrapper_matches_locator`；`flow_definition_发送CFD计算.json : step_9`。
- **来源**：2026-08-21 发送CFD计算 step_8/step_9 修复 → `docs/debug/debug-combobox-multiselect-checkbox.md`。

---

## 三、工程改进决策与契约（定位/执行/转换链路）

### 1. 分层定位与复合定位器
- **复合定位器**：`method/value` 逗号成对拼接（如 `automation_id,control_type`），运行时 split 后逐段 **AND** 匹配，全命中才算命中。
- **优先级（得分从高到低）**：`automation_id,control_type`(100) > `automation_id` > `ui_path`(深度≥2) > `name,control_type` > `name` > `class_name,...` > `control_type,found_index`(回退)。
- **ui_path**：录成 `主窗||Window->组||Group->确定||Button`，运行时从叶子 `.parent()` 逐级重建实际路径尾部对齐比对；深度≥2 才启用，避免过泛。
- **要点**：动态控件优先靠"组合"稳住（id+type 或 name+type），复杂嵌套靠 ui_path 尾部对齐，最后才落 found_index / 模板 / 坐标。

### 2. fallbackChain 自适应降级链
- 与旧的字符串 `fallbacks` 解耦，结构化为 4 级：**L1 主定位 → L2 ui_path_search → L3 template（模板图像）→ L4 coordinate（坐标）**。
- 命中非 L1 即视为"退化"，应触发反馈记录与自愈学习，而非静默通过。

### 3. 运行时反馈闭环
- 执行上下文携带 `flowDefinitionPath` / `runId`，运行结果回写 `feedbackHistory`：`fallback_recovery` / `fallback_template_recovery` / `step_failure` 等类型。
- 汇总进质量报告，形成"运行 → 反馈 → 优化定义"的闭环。

### 4. 自愈式选择器
- `detect_healed_locator` 发现降级命中（priority>0）→ `record_self_heal` 持久化 override 到 `self_heal_store.json` → 下次该控件提到 priority 0，逐步收敛。

### 5. found_index 父链引导消歧（本轮新增）
- 语义：**父容器内同类同级的 0 基序号**（区别于采集侧 `len(flat_controls)+1` 的全局遍历序号）。
- 判同三重：对象同一 > `runtime_id` > 矩形（`_is_same_wrapper`）。
- 定位策略：**低优先回退 + 打分 +12 消歧**。因录制范围有时是"窗口内匹配路径第 N 个"，不严格等于"直接父容器第 N 个"，故绝不用作高优先硬匹配去覆盖可靠 id/name。

### 6. 转换器（recorder → flow definition）
- 保留录制 UIPath 段尾 `#[范围,N]` 的序号为 `foundIndex`（此前被 `_strip_segment_index` 直接丢弃）。
- 具备增量合并、截图关联、质量报告（`conversionMeta`、`stats`）能力。
- **注意**：`_clean_control_definition` 会把 `inspectData` 值统一 `normalize`（int→str），运行时须用 `int()` 容错解析。

---

## 四、字段契约贯通纪律（最易踩的坑）

- **白名单重建陷阱**：`normalize_step` 用白名单重建步骤时，任何新增字段（如 `fallbackChain`、`_` 前缀内部字段）必须显式放行，否则会被静默丢弃 → 表现为"改了没生效"。
- **Excel 往返五处同步**：新增一个字段要同时改 **列定义 / 规范化 / 写出 / 读入 / 清理** 五处，缺一处就往返丢数据。
- **公共入口签名变更要同步调用点**：曾因 `configure_flow_executor` 签名缺参导致 `NameError` 阻断整个执行器。改公共函数签名时，务必全仓搜索调用点同步。
- **测试对齐更强行为，而非掩盖**：行为增强后过期测试应改为断言"更强的新行为"，不要放宽断言掩盖回归。

---

## 五、调试方法论（可直接照做的流程）

1. **复现 + 最小插桩**：在关键分支落 ndjson 取证日志（见 `.dbg/trae-debug-log-*.ndjson`），记录候选、得分、命中窗口 rect、控件模式。
2. **假设 → 证伪**：写下 2–3 个可证伪假设，用取证数据逐个排除，锁定唯一根因。
3. **pre-fix / post-fix 对比**：修改前后各跑一次，对比同一步的候选/得分/命中，确认因果。
4. **最小修复**：只改根因点，避免叠加"防御式补丁"污染打分逻辑。
5. **回归**：跑相关单测（`tests/`），确认无连带回归；必要时补断言更强行为的新测试。
6. **沉淀**：按"背景/症状 → 假设 → 调试计划 → 运行时证据 → 结论 → 修复 → 验证"结构记进 `docs/debug/`。

---

## 六、代码锚点速查表

| 关注点 | 文件 : 符号 |
|---|---|
| 复合定位匹配 | `wt_flow_locator.py : wrapper_matches_locator` |
| 候选生成 | `wt_flow_locator.py : build_common_locator_candidates` |
| 打分 | `wt_flow_locator.py : get_control_definition_match_score` |
| 同级序号 | `wt_flow_locator.py : get_wrapper_found_index / _is_same_wrapper` |
| 矩形基准 | `wt_flow_locator.py : get_wrapper_rectangle` |
| 自愈 | `wt_flow_locator.py : detect_healed_locator / record_self_heal` |
| 点击 | `wt_flow_executor.py : click_flow_control` |
| 窗口选择 | `wt_flow_locator.py : find_flow_window_for_relative_region / score_window_against_spec` |
| 缓存 | `wt_flow_locator.py : get_cached_flow_control` |
| 定位推荐 | `build_control_map_library.py : build_locator_recommendation` |
| 序号保留 | `flow_recorder_converter.py : _extract_segment_found_index / _build_control_definition` |
| 字段白名单 | `wt_flow_validation.py : normalize_step` |
| Excel 往返 | `flow_excel_io.py` |
| 流程级 GC 禁用（防 comtypes 崩溃） | `WT_AUT_recorded.py : run_automation` |
| 定位 GC 兜底 | `wt_flow_locator.py : find_flow_control`（包装函数） |
| 自动值断言 | `wt_flow_executor.py : _resolve_continue_when / _is_internal_content_host_control` |
| 原生崩溃栈捕获 | `WT_AUT_recorded.py : faulthandler.enable` |

---

## 七、维护约定

- 本文件是**人读同源出处**。Agent 侧同源产物：`.codebuddy/skills/wt-automation-lessons/SKILL.md`（skill 包）+ `skill_bridge.py` 内置 skill（保证被 Agent 上下文加载）。
- 新增一类根因或工程改进时，先更新本文件，再同步 skill 侧的精简规则。
- 暂不产出机器可读的 JSON 症状指纹库（按当前决策"先只做人读的 md"）。
