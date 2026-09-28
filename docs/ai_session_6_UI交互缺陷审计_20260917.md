# UI 交互缺陷审计（ai/session-6，2026-09-17）

> **性质**：只读审计，**未改动任何业务代码**。
> **审计基准**：`main @ 3f2b8be`（行号均为该版本）。
> **动机**：修完 Simple「项目计算参数」下拉选项（快照不刷新 / 只增不删 / 删空复活）后，
> 排查全项目是否存在**同类**界面交互缺陷。
> **方法**：三路只读深读（`WT_Launcher.py` / `WT_Flow_Editor.py` / 队列窗口等）＋ 主代理用
> AST 作用域分析、真实运行日志、运行时求值逐条复核最高优先级结论。
> **证据等级**：✅ = 主代理亲自复核并附证据；◐ = 子代理报告、未逐条复核（修前请自行确认）。

## 行号偏移提示

本次审计基于 `main @ 3f2b8be`。合并 `ai/session-6` 后 `WT_Launcher.py` 净增 303 行
（实测 +373 / −70），**该文件 L37 之后的行号整体下移 +303**；`WT_Flow_Editor.py`、
`wt_task_queue_window.py` 等未受本次改动影响，行号不变。

---

## P0（4 项，全部 ✅ 已核实）

### P0-1 ✅ `WT_Launcher.py:3695` — `uuid` 未导入，远程提交 100% 崩溃

```python
batch_prefix = "bat_{}".format(uuid.uuid4().hex[:8])   # L3695, in _submit_sections_to_remote
```

- **证据**：`hasattr(WT_Launcher, 'uuid') is False`；AST 确认全文无 `import uuid`、无
  `from ... import *`、无 `globals()` 注入；在模块命名空间内 `exec("uuid.uuid4()")`
  直接抛 `NameError: name 'uuid' is not defined`。
- **可达性**：`_submit_sections_to_remote` 由 L3789 / L3822 的远程提交回调调用，是活路径。
- **引入**：`af918e2`（2026-09-09，"Simple远程模式主按钮动态联动与冗余清理"）。
- **用户可见症状**：点「提交所选板块到远程队列」后界面毫无反应（异常被 Tk 回调吞掉，
  只打到 stderr），也无任何错误提示。
- **修法**：文件顶部补 `import uuid`（1 行）。

### P0-2 ✅ `WT_Flow_Editor.py:3572-3587` — 控件库「合并去重并保存」永久锁死

`ControlMapImportDialog`（L3116-5290）**从未给 `self.root` 赋值**，但有三处使用：
`_progress` L3577、`_worker` L3583 / L3585。

- **证据（三重）**：
  1. AST 作用域分析：该类区间内 `self.root` 赋值点为空集，使用点 3 处；
  2. `tools/merge_standard_control_library.py:599-609` 在 `run_merge` 开头即无条件回调
     `progress_callback`（0/25/50/75%），所以必然触发；
  3. 真实运行日志 `logs/launcher_flow_editor_20260819_154655.log` 完整记录了两次
     `AttributeError: 'ControlMapImportDialog' object has no attribute 'root'` ——
     第一次在 `_progress`，第二次在 `except` 分支里 `self.root.after(...)` 再次抛错。
- **后果链**：`_merge_running` 在 L3566 置 True → worker 线程死亡 → `_on_merge_done`（L3589）
  永不执行 → 标志永不复位。此后每次点击都停在 L3563-3565 的
  "控件库合并正在进行中，请稍候…"，**该功能永久不可用**，状态栏也永久卡在"正在合并…"。
- **修法**：构造时 `self.root = parent.winfo_toplevel()`；并把标志复位放进 `_worker` 的
  `finally`，经 `after` 回主线程执行。

### P0-3 ✅ `WT_Flow_Editor.py:5078-5212`（关键 L5161-5189）— 列表视图删除会删错控件并直接落盘

- **机制**：`_refresh_flat_view`（L3825-3867，"列表视图"）用
  `for index, control in enumerate(self._get_filtered_controls(), start=1)` 插入
  `iid=str(index - 1)`（L3848-3857）——**iid 是筛选/排序后的位置**，且该函数**不更新**
  `self._tree_node_index`（该字典在 L3951、L4182 被重置为 `{}`）。
  而删除路径 `delete_selected_controls`（L5078-5212，选中项取自 L5087
  `self.control_tree.selection()`）只查 `_tree_node_index`，未命中就回落 `int(idx)`，
  再 `del control_defs[i]`（L5187-5189）——把**筛选后位置**当成**原始数组下标**。
- **同文件已有正确写法作对照**：编辑路径 L4683-4696 带明确注释
  「解析控件在原始数组中的真实下标（**不能用显示位置 iid，排序后会错位**）」，
  优先取 `_sourceIndex`（该字段确实被写入，见 L3626 / L3660）。
  → 两条实现分叉，删除路径漏了同一处保护。
- **用户可见症状**：列表视图（默认"质量优先"排序）里删一个控件，被删掉的是**另一个**
  控件，并立刻写回 JSON 文件，**无法撤销**。
- **修法**：把 L4683-4696 的下标解析抽成一个共用函数，删除路径复用；解析失败时
  报错退出而不是回落 `int(idx)`。

### P0-4 ✅ `wt_task_queue_window.py:1920-1940` — 日志面板渲染满 300 行后永久冻结

- **机制**：服务器每轮返回固定长度快照 `/api/logs?tail=300`（L1415、L1883）。
  `_append_lines_incremental` 的增量判定为
  `need_full = rendered_count() > len(text_lines)`，否则
  `if len(text_lines) > current: insert(text_lines[current:])`。
  当控件已渲染 300 行、快照也是 300 行时：`300 > 300` 与 `300 > 300` **同时为假** →
  本轮零绘制。而日志内容是**滚动窗口**（新行进、旧行出），行数不变但整体位移一行。
  另外 `_LOG_MAX_LINES = 400` **大于**服务器上限 300，trim 分支永远不会触发，
  所以必然稳定停在这个等长状态。
- **用户可见症状**：任务运行几分钟后，「运行日志」与队列页日志**停止更新**，
  用户会误判任务卡死。
- **修法**：等长时比较首行/尾行是否变化；有变化则整体重绘（或删首行再追加尾行）。

---

## P1（✅ 已核实 3 项）

### P1-1 ✅ `WT_Flow_Editor.py` — `FlowEditorApp` 缺 `self.window`，12 处使用

`FlowEditorApp`（L5570-10614）内 `self.window` 赋值点为空集（AST 确认），使用点：
`cmd_refresh_step_controls_from_library` L9281/9287/9293/9336/9346、
`cmd_repair_flow_audit_issues` L9352/9359/9363/9369/9381、
`cmd_renumber_step_ids` L9640/9642。
`self.window` 只存在于各 *Dialog 类中（L1141、L1244、L1404…），
`FlowEditorApp` 只有 `self.root`（L5572）。
**症状**：「重排序ID」确认后 ID 已改、界面无完成提示并抛 traceback；另两个方法
（本文件未见按钮绑定，需确认是否有外部调用方）一旦调用即崩。
**修法**：统一改 `parent=self.root`。

### P1-2 ✅ `WT_Flow_Editor.py:8998-9002` — 未选中步骤时「新增控件」抛 `TypeError`

```python
step = self.steps[self.selected_index] if self.selected_index is not None and 0 <= self.selected_index < len(self.steps) else {}   # L8998 有保护
...
"id": f"..._{len(self.steps[self.selected_index].get('controls', [])) + 1}",   # L9001-9002 无保护
```
同一函数内保护不一致。**症状**：删光步骤或新建默认链路后点「新增控件」直接抛
`TypeError`，无提示。

### P1-3 ✅ `WT_Flow_Editor.py:8077-8079` — 切换动作会静默清空已选目标控件

`_update_action_editor_visibility` 在切到 `sleep` / `type_text_relative` /
`click_relative_region` 时执行 `self.var_target_control_id.set("")`（L8077、L8079），
但切回「点击」的 `else` 分支（L8100-8101）**不恢复**。
**症状**：只是在下拉里把动作切到「固定等待」再切回「点击」，已选好的"目标控件"就没了。
**性质**：属于"界面可见性刷新顺手做了破坏性归一化"，与本次修的 empty/missing 同类。

---

## P1（◐ 子代理报告，未逐条复核 —— 修前请自行确认）

| 位置 | 症状 |
|---|---|
| `WT_Flow_Editor.py:1843-1861` | 细分控件编辑弹窗必填项为空时点保存，弹窗无反应也不报错（`build_control()` 抛 `ValueError` 被 Tk 回调吞掉） |
| `WT_Flow_Editor.py:8701-8712`、`10593-10601` | 表单无脏标记：改了但没点「应用到当前步骤」就切步骤 → 改动无声消失；关窗只有"退出/取消"，无法先保存 |
| `WT_Flow_Editor.py:5130`（实现 5218-5269） | 控件库删一个控件会**静默改写磁盘上所有** `flow_packages/flow_definition_*.json`（打 `sourceDeleted`），确认框未说明影响范围，且不回灌编辑器内存副本 |
| `wt_task_queue_window.py:2777-2794` | 「终止」立即杀死运行中 worker，无二次确认；而「删除」有确认，行为不一致 |
| `wt_task_queue_window.py:2048`、`1381-1386`、`2143` | 后台线程直接读 Tk 变量（`token_var.get()` / `user_var.get()`），写路径都走 `_post_ui` 而读路径没有 → 偶发 `main thread is not in main loop` 或原生崩溃 |
| `wt_task_queue_window.py:2336` | 选择本地流程 JSON 时在 UI 线程同步发网络请求，窗口卡住数秒且无进度 |
| `wt_task_queue_window.py:2729-2743` | `control_task` `except Exception: return False` 吞掉原因，调用方与用户都不知道失败原因 |
| `launcher_panel.py:289-293`、`721-723` | 手工输入的 `AxeWindowsCLI.exe` 路径 / 服务地址，关窗即丢（只有"浏览"按钮才触发持久化） |
| `launcher_panel.py:387-398`（实现 91-98） | 停止 UiaPeek 用 `taskkill /F /IM UiaPeek.exe` 强杀**所有**实例，无确认，而提示语声称"仅可停止本对话框启动的" |
| `WT_AUT_recorded.py:411-426` | `MonitorWindow.log` 无行数上限（队列窗口有 400 行 trim），长流程下持续增长变卡 |
| `WT_AUT_recorded.py:2726` | 流程异常最后一行 `monitor_window.log(error_msg)` 未传 `kind="error"`，按普通信息着色，不易察觉 |

---

## P2（✅ 已核实 2 项 + ◐ 汇总）

- ✅ `WT_Flow_Editor.py:10349` — `self.default_font` 全文**无定义**（仅此一处使用），
  `_show_toast_notification` 抛 `AttributeError` 且被 `except: pass` 吞掉 →
  **伴随拾取的成功/失败 Toast 永远不显示**（红框仍会画）。
- ✅ `WT_Flow_Editor.py:9904` vs `10018` — `import queue as queue_module` 在
  `_start_concurrent_mode`（L9897-9980）内局部导入，而 `queue_module.Empty` 在
  `_poll_concurrent_events`（L10018）使用 → `NameError`，伴随拾取事件循环每轮提前退出。
- ◐ **重复实现分叉（4 份日志级别分类器 + 2 份状态保存）**：
  `wt_task_queue_window.py:1972-1982`（5 类，含 `system`）、
  `WT_Launcher.py:1787-1795`（4 类，无 `system`，但 L1715-1722 定义了 system 颜色 → 死配置）、
  `WT_Launcher.py:5405-5409`（LauncherApp，info `#f8fafc` / success `#4ade80`）、
  `WT_AUT_recorded.py:498-505`（浅色，含 `跳过→warning`，不含 ERROR/Traceback）；
  配色亦不一致（队列窗口 L698-705 为 info `#e2e8f0` / success `#34d399`）。
  状态保存：`WT_Launcher.py:2686`（读改写 + 吞异常）vs `5946`（全量覆盖 + 异常外抛）。
- ◐ 其余体验项：同一弹窗可重复打开（无单例守卫，如 `wt_task_queue_window.py:2178-2199`、
  `WT_Launcher.py` 项目参数弹窗 / 相对区域助手 / 模板制作器 / 控件库采集器）；
  `after()` 回调未 `after_cancel`（`wt_flow_graph.py:197`、`WT_Flow_Editor.py:4868-4875`）；
  队列状态筛选器缺「已取消/已终止」（`wt_task_queue_window.py:367-382`，而 `STATUS_LABELS`
  L24-32 有 7 种）；设置逐键落盘无去抖（`wt_task_queue_window.py:1301-1354` →
  `WT_Launcher.py:5946`）；`wt_flow_graph.py:86-87` 解析流程 JSON 无 try/except，
  下拉切换时抛未捕获异常。

---

## 与 `ai/session-6` 的关系

- 本次审计基准是 `main @ 3f2b8be`，**不含** `ai/session-6` 的改动。
- `ai/session-6` 已修掉的同类项（本报告不重复列出）：Simple 项目参数里 Cp 版本 /
  风机类型-型号下拉的「快照不刷新」「只增不删」「删空被默认值复活」。
- `WT_Launcher.py` 的其余条目（P0-1、P1-4 状态保存分叉、P2 重复分类器等）**尚未修**。

## 建议修复顺序

1. **先修「一按就废 / 一按就错」的三个**：P0-1（1 行）、P0-2（3 行）、P0-3（复用编辑路径的
   下标解析）。这三个都是用户可复现、后果不可逆（数据被改错 / 功能永久锁死）的。
2. **再修 P0-4**（队列日志冻结）—— 它会让用户误判任务卡死，排查成本高。
3. **然后 P1 的静默失败与无确认类**：`build_control` 校验、`control_task` 失败原因、
   终止/取消确认、表单脏标记。
4. **最后做收敛**：4 份日志分类器 + 配色 → 单一模块（`wt_theme` 已有权威色板，
   与 2026-09-16 日志审计结论一致）；2 份状态保存 → 单一实现。

## 覆盖度与未覆盖

- 已逐段通读：`WT_Launcher.py` 全文 9192 行、`WT_Flow_Editor.py` 全文 10726 行、
  `wt_task_queue_window.py` 全文 2966 行、`wt_flow_graph.py` 全文 427 行、
  `launcher_panel.py` 全文 723 行、`WT_AUT_recorded.py` 的 MonitorWindow 相关区段。
- **未审计**：`build_control_map_library.py`、`build_image_template_library.py`、
  `text_tools.py`、`txt_merge_tool.py`、`control_live_detector.py`、
  `tools/generic_flow/generic_launcher.py`、`tools/` 下若干诊断脚本 ——
  这些也含 Tk 界面，本次未覆盖。
- 历史日志 `logs/launcher_flow_editor_20260819_154655.log` 中另有一处
  `file_listbox` 缺失报错，经核实该属性现已在 L3240 赋值、且 `_on_file_scope_change`
  只由 L3206-3208 的 Radiobutton 点击触发（构造期不触发），**该历史缺陷已修复**，
  不作为现存问题。
