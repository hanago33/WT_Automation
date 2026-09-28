# E1 · 动作（action）字典

> 本篇是**写步骤时查得最多的一篇**：19 种动作的全字段、默认值、约束与示例。
> 读者：写/改流程的每一个人。
>
> 模型与校验原理在 B2；本篇是**可直接照抄的字典**。
> 来源：`wt_action_schema.py`（2026-09-11 实测）+ 真实流程定义。

---

## 1. `actionConfig` 通用字段

任何 `actionType: "action"` 的步骤，其 `actionConfig` 都支持下列字段。

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `action` | str | `click` | 动作名，必须是 19 种之一 |
| `controlId` | str | — | 目标控件 ID，**必须存在于本步骤的 `controls[]` 里**（`send_keys` 可豁免） |
| `timeoutSeconds` | float | 见 `wt_action_defaults` | 定位/等待超时。`click` 默认 2.5 |
| `waitBefore` | float | 0.0 | 动作前等待秒数 |
| `waitAfter` | float | 视动作 | 动作后等待秒数（`click` 0.12，`double_click` 0.18） |
| `retryCount` | int | 0 | 重试次数；实际总尝试 = `retryCount + 1` |
| `retryInterval` | float | 1.0 | 重试间隔秒数 |
| `onError` | enum | `stop` | `continue` / `retry` / `stop` / `fallback` / `ask` |
| `stepPolicy` | dict | — | 新策略字段，**优先级高于 `onError`**；`onFail ∈ {skip, retry, fallback, abort, ask}` |
| `continueWhen` | dict | — | 动作后状态校验，见 §3 |
| `precondition` | dict | — | 动作前求值，满足则跳过，见 §4 |
| `fallbackTemplate` | str | — | 兜底模板路径；未显式配置时从 `controls[].templateKey` 自动接线 |
| `preferTemplate` | bool | false | 为 true 时**先尝试模板路径** |

**最小可用示例**（真实取自 `flow_definition_发送综合计算.json` step_1）：

```json
"actionConfig": {
  "timeoutSeconds": 2.5,
  "waitBefore": 0.0,
  "waitAfter": 0.12,
  "action": "click",
  "controlId": "control_map_439",
  "retryCount": 0,
  "retryInterval": 1.0,
  "onError": "continue"
}
```

---

## 2. 19 种动作

> ✅ 必须配 `controlId`　⭕ 可选（`send_keys` 特例）　❌ 不需要

### 2.1 基础点击类

| 动作 | 中文 | 目标 | 输入键 | 说明 |
|---|---|:--:|:--:|---|
| `click` | 单击 | ✅ | — | 对目标控件执行一次左键点击 |
| `right_click` | 右键 | ✅ | — | 右键点击 |
| `double_click` | 双击 | ✅ | — | 双击 |
| `double_right_click` | 右键双击 | ✅ | — | 连续执行两次右键点击 |
| `mouse_wheel` | 滚轮 | ✅ | `delta` | 正数向上，负数向下 |

### 2.2 输入类

| 动作 | 中文 | 目标 | 输入键 | 说明 |
|---|---|:--:|:--:|---|
| `type_text` | 输入文本 | ✅ | `text` | 先聚焦目标控件，再输入文本 |
| `send_keys` | 发送按键 | ⭕ | `text` | 向目标控件发送键盘内容或快捷键。**唯一允许无目标控件**的动作（发给当前焦点） |
| `type_text_relative` | 父窗口区域输入 | ❌ | `text` | 先定位父窗口，再按亲戚区域点击并输入；若界面需要失焦提交，配 `postInputKeys` 为 `{TAB}` 或 `{ENTER}` |
| `set_combobox` | 设置下拉框 | ✅ | `value` | 直接设置 ComboBox 的选中值（解析自 recorder 的 `set_combobox`） |

### 2.3 亲戚区域 / 锚点类（WPF 控件不可达时用）

| 动作 | 中文 | 目标 | 说明与专用字段 |
|---|---|:--:|---|
| `click_relative_region` | 父窗口区域点击 | ❌ | 专用字段：`parentWindow`、`relativeRegion` |
| `click_relative_anchor` | 锚点相对点击 | ✅ | 专用字段：`anchorAlign`、`offsetX`、`offsetY`、`clickKind` |

**`parentWindow`**

```json
"parentWindow": { "title": "地理信息数据", "className": "Window", "frameworkId": "WPF" }
```

- `title` 可为空，**仅当同时给出 `className` + `frameworkId` 时**允许无标题；
- `frameworkId ∈ {WPF, Win32, uia, WinForm}`。

**`relativeRegion`**

```json
"relativeRegion": { "x": 0.5, "y": 0.5, "width": 0.1, "height": 0.1, "anchor": "center" }
```

- `x` / `y`：**0 ≤ v ≤ 1**
- `width` / `height`：**0 < v ≤ 1**
- `anchor ∈ {center, left_center, right_center}`

**`click_relative_anchor` 的对齐基准**

`anchorAlign ∈ {center, left, right, top, bottom}`。
> 💡 `anchorAlign: "right"` 是高频技巧：点控件**最右侧的内部图标**（如 `MTDFlexibleComboBox` 的配置按钮）。它相对锚点控件本身，比固定区域坐标更抗布局/缩放漂移。

### 2.4 选择 / 勾选类

| 动作 | 中文 | 目标 | 输入键 | 说明 |
|---|---|:--:|:--:|---|
| `select_dropdown_item_runtime` | 运行时下拉选择 | ✅ | — | 先展开下拉框，再在运行时枚举 Popup 中的可见 `ListBoxItem` / `MenuItem` 并点击 |
| `check_all_toggles` | 批量勾选 CheckBox | ✅ | — | 逐行读 `ToggleState`：已勾选跳过，未勾选**先物理点击、未翻转再程序化 Toggle 收敛**，禁用项跳过。存在勾选失败或未找到目标时**步骤判失败** |
| `select_list_items` | 批量选中候选项 | ✅ | `text` | 候选名单逗号分隔；**空或 `ALL` = 全选**；名单未命中任一项时**步骤判失败** |

### 2.5 等待 / 控制 / 辅助类

| 动作 | 中文 | 目标 | 输入键 | 说明 |
|---|---|:--:|:--:|---|
| `wait_for_control` | 等待控件 | ✅ | — | 等待目标控件出现、可见或满足条件 |
| `drag_and_drop` | 拖拽 | ✅ | — | 源控件 → 目标控件；`sourceControlId` / `targetControlId` / `durationSeconds` |
| `menu_select` | 菜单选择 | ❌ | `menuPath` | 按路径依次点击菜单项，如 `File->Open` |
| `log` | 记录日志 | ❌ | `message` | 向运行日志输出一条文本（**`show_timeout: false`**） |
| `sleep` | 等待 | ❌ | `seconds` | 纯等待，不依赖控件（**`show_timeout: false`**） |

---

## 3. `continueWhen`（动作后状态校验）

```json
"continueWhen": { "controlId": "control_map_87", "condition": "visible", "timeoutSeconds": 3.0 }
```

| 字段 | 说明 |
|---|---|
| `controlId` | 必须是本步骤 `controls[]` 中的 ID |
| `condition` | 见下表 |
| `expectedValue` | `value_equals` 时使用 |
| `expected` | `toggle` / `toggle_state` / `checked` 时用 `on` / `off` |
| `timeoutSeconds` | 等待超时 |

**合法条件** `ALLOWED_CONTINUE_WHEN_CONDITIONS`：

| 条件 | 含义 |
|---|---|
| `exists` / `present` | 控件存在 |
| `visible` / `enabled` | 可见 / 可用 |
| `gone` | 控件消失（等弹窗关闭） |
| `nonempty` / `non_empty` | 值非空 |
| `value_equals` | 值等于 `expectedValue`（**自动值断言**） |
| `toggle` / `checked` / `toggle_state` | ToggleState 达到期望 |

> ⚠️ **自动值断言的假失败**：`type_text` / `send_keys` / `select_dropdown_item_runtime` 等输入类动作会**自动生成 `value_equals` 断言**。但 `PART_ContentHost`、`PART_DropDownButton`、Text 标签、无 label 消歧的裸 `textbox` **读不到值** → 必然假失败。判定函数 `_is_unreadable_value_control` 会跳过这类自动断言（详见 B5 §2.2）。

---

## 4. `precondition`（动作前跳过判定）

```json
"precondition": {
  "condition": "visible",
  "expected": "off",
  "controlId": "WindDirectionSelector_Button_OpenSelector_风向选择_Button"
}
```

- 仅对**显式配置** `precondition` 的步骤生效，未配置**完全不受影响**；
- 仅对实现 **TogglePattern** 的控件有效；
- 满足则**跳过动作**并记 `skipped`；
- 典型用法：`{"condition": "toggle", "expected": "off"}` → 已勾选就不再点，实现**幂等勾选**。

---

## 5. `stepPolicy`（新策略字段）

| `stepPolicy.onFail` | 等价旧 `onError` | 行为 |
|---|---|---|
| `skip` | `continue` | 记 **`failed`** 但流程继续 |
| `retry` | `retry` | 重试 |
| `fallback` | `fallback` | 模板兜底 |
| `abort` | `stop`（默认） | 抛出，流程中止 |
| `ask` | `ask` | AI / 人工介入 |

> 💡 **排查提示**：看到"流程报成功但中间有 failed 步骤"，先查是不是 `onError: continue`。这不是 bug，是设计（B5 §7）。

---

## 6. 专用字段速查

| 字段 | 属于哪些动作 | 说明 |
|---|---|---|
| `postInputKeys` | `type_text` / `type_text_relative` | 输入后发送按键以**强制提交**，常用 `{TAB}` / `{ENTER}`。防"输入未提交"的假成功 |
| `parentWindow` | `click_relative_region` / `type_text_relative` | 父窗口定位（title + className + frameworkId） |
| `relativeRegion` | `click_relative_region` / `type_text_relative` | 归一化区域 + anchor |
| `anchorAlign` / `offsetX` / `offsetY` / `clickKind` | `click_relative_anchor` | 锚点对齐与像素偏移 |
| `sourceControlId` / `targetControlId` / `durationSeconds` | `drag_and_drop` | 拖拽两端 |
| `menuPath` | `menu_select` | 菜单路径 |
| `delta` | `mouse_wheel` | 滚轮量（正上负下） |
| `fallbackTemplate` / `preferTemplate` | 任意 | 模板兜底与"模板优先" |

---

## 7. 各动作的"建议列"（Excel 往返用）

`wt_action_schema` 为每个动作提供 `suggested_columns`，导出 Excel 时用作填写指引：

| 动作 | 建议列 |
|---|---|
| `type_text_relative` | `inputText`, `postInputKeys`, `parentWindowTitle/className/frameworkId`, `regionX/Y/Width/Height` |
| `click_relative_region` | `parentWindowTitle/className/frameworkId`, `regionX/Y/Width/Height` |
| `click_relative_anchor` | `controlId(锚点)`, `anchorAlign`, `offsetX`, `offsetY`, `clickKind` |
| `check_all_toggles` | `controlId(批量 CheckBox)` |
| `select_list_items` | `controlId(类别锚)`, `候选名单` |
| `drag_and_drop` | `sourceControlId`, `targetControlId`, `durationSeconds` |

---

## 8. 写动作的三条经验法则

| # | 法则 |
|---|---|
| 1 | **凡是输入，必配提交手段**：`postInputKeys` 或紧跟一个 `send_keys`。否则大概率"假成功" |
| 2 | **凡是勾选，必配幂等**：`precondition` 或 `check_all_toggles`。否则重跑会翻状态 |
| 3 | **凡是关键动作，必配 `continueWhen`**。没有它，执行器无法判断"点了有没有生效"，AI 介入结果也不会被采信 |

---

核验：2026-09-11 / 涉及：`wt_action_schema.py`(L3–246)、`wt_action_defaults.py`、`wt_flow_validation.py`、`flow_packages/flow_definition_发送综合计算.json`、`flow_definition_发送CFD计算.json`、`flow_definition_geo_import_data_file.json`、`flow_definition_导入地形图.json`、`flow_definition_创建一个新建模.json`
