# 调试记录：MUP 分区瓦片（气象/建模/求解器）折叠/展开判定

- **日期**：2026-08-21
- **模块**：`wt_flow_executor.py` 的 `_eval_precondition_skip`（前置条件判定）；流程 `flow_packages/flow_definition_发送CFD计算.json` 的 `step_3 / step_10 / step_13` precondition
- **现象**：发送 CFD 计算流程里，三个分区切换瓦片（气象和环境参数 / 建模区域参数 / 求解器参数）在"已展开时被点击折叠"或"明明折叠却跳过不展开"，导致下游步骤定位失败。单独跑一个分区成功，完整流程连跑就失败。

---

## 一、控件事实（来自录音 `control_maps/recordings/20260730_155717_CFD新计算1_window_control_map.json`）

三个分区瓦片 `automationId = MTDTileView_Button_ToggleState`，位于左侧导航，`Name` 即分区名。关键差异：

| 瓦片 | supportedPatterns | 折叠态 | 展开态 |
|---|---|---|---|
| 气象和环境参数 | `Invoke, LegacyIAccessible, SynchronizedInput`（**无 Toggle**） | `IsOffscreen=True` / `LegacyIAccessible.State=98320` | `IsOffscreen=False` / `State=1048592` |
| 建模区域参数 | `LegacyIAccessible, SynchronizedInput, Toggle`（**有 Toggle**） | `IsOffscreen=True` / `State=98320` | `IsOffscreen=False` / `State=1048592` |
| 求解器参数 | `LegacyIAccessible, SynchronizedInput, Toggle`（**有 Toggle**） | `IsOffscreen=True` / `State=98320` | `IsOffscreen=False` / `State=1048592` |

要点：
- 瓦片自身 `IsOffscreen` 随折叠/展开变化（折叠=True，展开=False），但**瓦片标题即便折叠也常显示在导航里**，所以"标题可见"≠"分区已展开"。
- 建模/求解器瓦片有 `Toggle` 模式，`ToggleState`：`0=Off(折叠)`、`1=On(展开)`，是展开/折叠的真实判据。
- 气象瓦片**无 Toggle**，只能用 `IsOffscreen` 间接判断（且其默认展开，误判风险低）。

---

## 二、修复演进（三阶段，逐一踩坑）

### 阶段 1：用子控件可见性当"已展开"哨兵 —— 错误
- 做法：`visible` 前置引用分区内深层子控件（风向选择 / 最小水平分辨率 / 核数）作哨兵，`IsOffscreen=False` 即认为已展开→skip。
- 坑：子控件在复杂 WPF 树里定位极不稳定，**已展开时也常定位不到** → 误判"折叠" → 执行点击 → 把已展开分区**误折叠** → 下游失败。
- 结论：不能用深层子控件当判据。

### 阶段 2：`visible` 读瓦片自身 IsOffscreen —— 假成功
- 做法：去掉哨兵 `controlId`，让引擎 fallback 读**动作目标（瓦片标题）自身** `IsOffscreen`。
- 坑：瓦片标题折叠时仍留在导航可视区（`IsOffscreen=False` 恒为真）→ 前置永远判"已可见"→ **该展开的分区被 skip（假成功）**。日志显示 `skipped` 但分区实际没展开。
- 结论：瓦片标题的 `IsOffscreen` 不是折叠/展开的有效判据。

### 阶段 3：`toggle` 读 ToggleState —— 失焦误读（本次核心问题）
- 做法：建模/求解器瓦片改用 `toggle, expected:off`（支持 Toggle）；气象仍用 `visible`（无 Toggle）。
- 坑（用户观察）：单独跑一个分区成功，完整流程连跑就失败（求解器被 skip）。
- 根因：`get_wrapper_toggle_state` 读的是 `wrapper.element_info.element.CurrentToggleState`（WPF UIA 属性）。**窗口失焦 / 控件被虚拟化时，UIA 缓存不刷新，折叠的瓦片被读成 `ToggleState=1(展开)`** → 引擎判"相反状态"→ skip。完整流程跑了一堆步骤后焦点漂移，误读概率骤增；单独跑时窗口干净聚焦，读得对。
- 修复（`wt_flow_executor.py`，`_eval_precondition_skip` 的 `toggle` 分支）：
  1. **读取前先激活所属主窗口**：`control.window().set_focus()`，强制刷新 UIA 缓存再读 `ToggleState`，规避失焦陈旧值。
  2. **加诊断日志**：`前置[toggle] step=...: 控件 ... ToggleState=X (expected=off)`，便于下轮确诊。

---

## 三、最终生效配置

### 引擎 `wt_flow_executor.py`
- `toggle` 分支：定位控件后、读 `ToggleState` 前先做 `control.window().set_focus()`；读后打印 `ToggleState` 诊断。
- `visible` 分支：保留（仅用于无 Toggle 的气象瓦片，读瓦片自身 `IsOffscreen`）。
- `get_wrapper_toggle_state`（`wt_flow_locator.py`）原本即读 `CurrentToggleState`，无需改。

### 流程 `flow_definition_发送CFD计算.json`
- `step_3` 气象：`"condition":"visible","expected":"off"`（无 Toggle，靠标题 IsOffscreen，默认展开场景可接受）
- `step_10` 建模：`"condition":"toggle","expected":"off"`
- `step_13` 求解器：`"condition":"toggle","expected":"off"`

---

## 四、验证结果（2026-08-21 11:46 运行）

三个分区默认折叠、跑完整流程，**全部 success**，无 skip/failed：

```
[前置[toggle] step=step_10: 控件 建模区域参数_Button ToggleState=0 (expected=off)  → 点击展开 → success
[前置[toggle] step=step_13: 控件 求解器参数_Button ToggleState=0 (expected=off)  → 点击展开 → success
[前置[visible] step=step_3: ... 未定位(分区折叠) → 执行动作(点击展开)             → success
status=success, executed=10, success=10, failed=0, skipped=0
```

诊断日志确认 `ToggleState=0`（折叠）被正确读出 → 点击展开，失焦误读已根除。

---

## 五、可复用结论

1. **WPF 分区/可折叠瓦片的状态判定**：优先用控件自身的 `Toggle` 模式（`ToggleState`），**不要用子控件可见性**（定位不稳定）也**不要用标题 `IsOffscreen`**（折叠仍可见）。
2. **读 UIA 状态前先 `set_focus()`**：WPF 在失焦/虚拟化时 `CurrentXxx` 属性会返回陈旧缓存值，导致"连跑失败、单跑成功"这类焦点污染问题。读取前激活主窗口是最稳的兜底。
3. **前置条件判定务必打诊断日志**：明确打印"读到的真实状态 + expected"，否则 `skipped`/`执行` 的静默结果极易被误判为"没判断"。
4. **同屏多个同类型瓦片**（automationId 相同、靠 found_index 消歧）用 `ToggleState` 判态比用 `IsOffscreen` 更可靠。

> 遗留：气象瓦片仅支持 `Invoke`，用 `visible` 是其最佳可行方案；若后续发现气象也出现误判，考虑改用"读分区内容区可见性"或运行时先做收起再点的策略。
