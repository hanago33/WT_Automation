# 调试经验教训（Debug Lessons Learned）

> 从项目 debug-*.md 文件中提取的关键经验，按问题类型分类

## 一、控件定位与命中

### 假命中 (false-hit)
- **现象**：日志显示控件已找到并操作成功，但界面无反应
- **根因**：定位到了同名但非目标控件（如同名 Static 标签而非 Edit 输入框）
- **修复**：加强 `targetMethod` 中的类型约束（如 `name,class_name` ← `name,Edit`）
- **相关文件**：`debug-add-data-false-hit.md`、`debug-step37-add-data-miss.md`

### 误命中 (wrong-target)
- **现象**：点击到了相邻或重叠的其他控件
- **根因**：多个控件 `automationId` 相同或 `name` 相似
- **修复**：加 `control_type` 或 `ancestors` 约束做歧义消除

### 多选下拉 CheckBox 子节点文本 (combobox-multiselect-checkbox)
- **现象**：Telerik 多选下拉（如热稳定度 `MTDGroupComboBoxMultiSelection`）展开后有多个同 `automationId` 的 CheckBox，用 `name` 定位点错等级（误勾 0/3），用 `label_text` 定位则"未找到匹配控件"
- **根因**：等级文本在 CheckBox 的**子节点 Text** 上，CheckBox 自身 UIA Name 为空、文本也非兄弟节点；Raw View 兄弟标签预过滤把真实候选全砍掉
- **修复**：
  1. 流程定义用 `targetMethod="automation_id,control_type,label_text"` + `labelText=等级文本`
  2. 定位引擎 Raw View 预过滤支持**子节点文本匹配**（`_raw_element_child_text_matches`，兄弟→子节点双路）
  3. `wrapper_matches_label_text` 增加子 Text/TextBlock 匹配（`_match_child_text_block_label`）
  4. precondition `{"condition":"toggle","expected":"off"}` 实现幂等勾选（已勾选跳过、未勾选点击）
- **相关文件**：`debug-combobox-multiselect-checkbox.md`

## 二、窗口与相对区域

### 窗口漂移 (window-drift)
- **现象**：相同的相对区域坐标，在不同运行中点击位置偏移
- **根因**：参考窗口（reference window）的尺寸/位置在不同条件下变化（如窗口最大化/还原、多监视器）
- **修复**：每次操作前重新选择参考窗口；使用 `parentWindow` 而非绝对坐标
- **相关文件**：`debug-cft02-step16-drift.md`

### 相对区域偏移 (relative-region-offset)
- **现象**：`click_relative_region` 的偏移量与预期不符
- **根因**：计算基点不正确（center vs left_center vs right_center 选择错误）
- **修复**：确认 `relativeRegionAnchor` 设置正确；必要时用 `click_relative_anchor` 替代
- **相关文件**：`debug-relative-region-offset.md`

## 三、输入相关

### 时序输入回归 (time-series-input-regression)
- **现象**：之前能正常输入的步骤更新后失败
- **根因**：输入法切换、焦点丢失、系统剪贴板冲突
- **修复**：确保输入前 `waitForControl` + `type_text` 使用 send_keys 后验证
- **相关文件**：`debug-time-series-input-regression.md`

### 默认高度相对输入 (default-height-relative-input)
- **现象**：父窗口区域输入的默认高度计算错误
- **根因**：未正确获取父窗口实际高度
- **修复**：运行时动态计算父窗口矩形，勿用硬编码高度
- **相关文件**：`debug-default-height-relative-input.md`

## 四、验证与回归

### 启动激活回归 (start-validation-regression)
- **现象**：新的修改导致之前能通过验证的步骤失败
- **根因**：修改了共享的窗口激活/验证逻辑
- **修复**：修改底层函数后运行全量回归测试
- **相关文件**：`debug-start-validation-regression.md`

## 五、通用教训

1. **每次修改组件后必须跑回归测试**（防止改了 A 坏了 B）
2. **优先用 automationId**：比 name/xpath 稳定得多（WPF 控件有稳定的 automationId）
3. **WPF 弹窗优先用相对区域**：自绘控件拿不到 UIA 信息时，切相对区域是最稳的
4. **输入框定位必须加类型约束**：`name,Edit` 而不是孤立的 `name`
5. **单步验证优先于全流程运行**：新增步骤后先跑单步，减少调试成本

## 六、原生崩溃与值断言（2026-08-19 新增）

### comtypes GC 崩溃（段错误/堆损坏）
- **现象**：日志在"开始执行步骤"后直接"自动化流程执行失败"，无"步骤结束"也无"错误："；faulthandler 显示 `Windows fatal exception: access violation` / `code 0xc0000374`，栈顶是 `Garbage-collecting` → `comtypes Release` → `__del__` → `elements_from_uia_array`。
- **根因**：pywinauto/comtypes 的 UIA 元素数组在垃圾回收时释放失效 COM 指针，Python try/except 无法捕获，进程直接死亡。可发生在任何 `descendants()`/`children()`/`FindAll`/Raw View 遍历处。
- **修复**：UIA 遍历期间禁用 GC（`gc.disable()`），结束恢复（`gc.enable()`）。最稳是流程级：`run_automation` 入口禁用、`finally` 恢复 + `gc.collect()`；`find_flow_control` 入口加包装函数兜底。同时启用 `faulthandler` 以便崩溃时 dump 调用栈。
- **相关文件**：`WT_AUT_recorded.py:run_automation`、`wt_flow_locator.py:find_flow_control`

### 原生崩溃 · 跨线程 Tk/COM 收尾变体（步骤全成功仍报执行失败）
- **现象**：每一步 `status=success`、运行报告与 `run_status.json` 均为 `success`，但 Launcher 仍提示"自动化流程执行失败"，监测窗口瞬间关闭，子进程退出码非 0（如 `-1073741819` = `0xC0000005`），且无 "错误：" / traceback / faulthandler dump（崩在 Tcl/Tk 或 COM 的 C 代码）。
- **根因**：`WT_Launcher._handle_process_exit` 只看子进程退出码判断成败（`==0` 才算完成），报告 status 与退出码是两套信号。`run_automation` 运行在后台 `automation_thread`，但收尾代码直接跨线程调用 Tk：`set_success()/log()` 里的 `self.root.update()`（跨线程递归进入 Tcl 事件循环 → access violation）、`_update_progress_title` 里的 `root.title()`；以及后台线程直接调 `_TaskbarProgress` 的 COM vtable（无 marshalling）。次因：`finally` 里 `gc.collect()` 集中回收 comtypes/UIA 对象、`Py_Finalize` 释放失效 COM 指针。Python try/except 无法捕获原生崩溃，进程直接死亡。
- **修复**（三层）：① MonitorWindow 的 `log/update_status/set_success/set_error` 去掉 `self.root.update()`；新增 `_ui_safe_call(callback)`（有 mainloop 时 `root.after(0, cb)`，否则直调兜底），所有后台线程里的 `monitor_window.*` 与 `_update_progress_title`/`_update_taskbar_progress` 调用统一经它调度（`log_step`、`_record_step_result`、run_automation 成功/失败收尾）。② `finally` 只恢复 GC（去掉 `gc.collect()`）。③ `main()` 两分支末尾 `os._exit(0|1)` 跳过 `Py_Finalize`。
- **相关文件**：`WT_AUT_recorded.py:_ui_safe_call / MonitorWindow / log_step / _record_step_result / run_automation`、`WT_Launcher.py:_handle_process_exit`

### PART_ContentHost 值断言假失败（扩展为"读不到值的控件"通用模式）
- **现象**：键入/下拉动作成功（"已通过流程链路匹配输入文本"、"键入过滤后点击选中下拉项"），但随后 `continueWhen value_equals` 超时失败，步骤被判 failed；每次续跑等待重新全量定位 → 单步拖到 60-90 秒。目标控件包括：`PART_ContentHost`（Pane）、`PART_DropDownButton`（下拉展开按钮）、`全文检索,Text`（文本标签）、无 label 消歧的裸 `textbox`。
- **根因**：`_resolve_continue_when` 对 `type_text/send_keys/select_dropdown_item_runtime` 自动生成 `value_equals` 断言。但上述控件即使动作成功也读不到输入结果：PART_ContentHost 无 ValuePattern；PART_DropDownButton 是展开按钮无值；Text 标签读到的不是输入值；裸 textbox 可能命中多个/离屏。
- **修复**：自动值断言前判断控件是否"可可靠读值"（`_is_unreadable_value_control` + `_is_internal_content_host_control`），不可读则跳过自动断言，动作成功即视为通过。
- **相关文件**：`wt_flow_executor.py:_resolve_continue_when / _is_unreadable_value_control`

### 定位性能 · label 矩形全树扫描每步重复支付（整树 20-36 秒）
- **现象**：`[定位耗时] ... 整树=19000~36000ms`，多步骤命中但慢。
- **根因**：`wrapper_matches_label_text`→`_find_label_rects_for_wrapper` 对候选做 parent/top_window 全子树扫描（巨大 WPF 窗口 20-36 秒）；廉价兄弟 TextBlock 匹配排在全树扫描之后；label 矩形缓存每次 `find_flow_control` 硬清空。
- **修复**（三层）：① 兄弟 TextBlock 匹配提前；② label 矩形缓存改 TTL 软失效（30s）跨调用保留（reset 保留硬清空给测试用）；③ `_iter_raw_view_findall_candidates` 先做 Raw View 兄弟标签预过滤再完整评分。
- **相关文件**：`wt_flow_locator.py:wrapper_matches_label_text / _match_sibling_text_block_label / _label_rect_cache_* / _iter_raw_view_findall_candidates`

## 七、流程步骤里的"假动作"（2026-08-20 新增）

### 假动作 (fake-action) · 录制/合并时未过动作校验
- **现象**：flow 的 `actionConfig.action` 里出现运行时根本不存在的动作名（如 `expand`、`check_only_if_unchecked`、`uncheck_only_if_checked`），执行时会被判"不支持的动作"；若 `onError=continue` 还会**静默跳过**，导致下游依赖（如折叠面板里的导出按钮）不可用。
- **根因**：这些动作名在生成/合并流程（录制回放、脚本批量追加步骤）时被直接写进 JSON，但 **`WT_AUTOMATION_Agent/schemas.py` 的 `ACTION_SCHEMAS` 里没有对应的 schema**，`flow_ops.py` 的 `_validate_patch_step_action` 只在"增量 patch"时校验，全量写回/录制阶段不校验，于是漏网。
- **真实动作对照**：
  - WPF `CheckBox`（TogglePattern）：正确动作就是 `click`（一次中心点击翻转勾选态）；要"按需勾选"用 `actionConfig.precondition:{condition:"toggle",expected:"on"/"off"}`（运行时已支持，见 `wt_flow_executor._eval_precondition_skip` + `wt_flow_locator.wait_for_flow_control_condition` 的 `toggle` 分支）。
  - WPF `Expander`（ExpandCollapsePattern）：**正确动作应走 `ExpandCollapsePattern.Expand()/Collapse()`**，而非对内容 Group 做中心 `click`（中心点落在内容区、不是头部开关，点了不会展开）。
- **临时处理（本次）**：`expand` 步骤（step_29/33/43/57）先改成 `click`，并在各 step 的 `description` 里标注 `[临时]...`；`check/uncheck` 已改为 `click`+`precondition`。
- **待优化（后续）**：给 `schemas.py` / `wt_flow_executor.py` 增加真实 `expand` / `collapse` 动作（调用 wrapper 的 `ExpandCollapsePattern`，找不到该 Pattern 时回退 `click`），再把上述临时 `click` 替回。同时建议录制/合并阶段也过一遍 `_validate_patch_step_action`，从源头拦截假动作。
- **相关文件**：`WT_AUTOMATION_Agent/schemas.py:ACTION_SCHEMAS`、`wt_flow_executor.py`、`wt_flow_locator.py`、`flow_packages/flow_definition_导出综合计算结果.json`
- **审计命令**：遍历所有 `steps[].actionConfig.action`，与 `ACTION_SCHEMAS` 的 key 集合比对，不在集合内的即假动作。

### 下拉枚举漏 Popup · 前置步骤已展开时窗口列表不含 Popup
- **现象**：`select_dropdown_item_runtime` 报 `运行时下拉项未命中且未枚举到候选项: ... rawProbe={"count": 0, "samples": []}`；前置步骤已点击展开下拉（toggle=On）。
- **根因**：窗口收集（`_collect_dropdown_windows`）只在 `should_click=True`（本次点击展开）分支执行；toggle 已 On 时 `should_click=False`，Popup 未并入 `dropdown_windows` → RadComboBoxItem 枚举不到。
- **修复**：读取 toggle 后无论是否点击，都重新收集并合并 Popup 窗口到 `dropdown_windows`。
- **相关文件**：`wt_flow_locator.py:select_dropdown_item_runtime`
