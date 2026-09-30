# 调试记录：Telerik 多选下拉 CheckBox 定位与幂等勾选（热稳定度 2 - 中性）

- **日期**：2026-08-21
- **模块**：`wt_flow_locator.py`（Raw View 标签预过滤 / `wrapper_matches_label_text` / `wrapper_matches_locator`）；流程 `flow_packages/flow_definition_发送CFD计算.json` 的 `step_8 / step_9`
- **现象**：发送 CFD 计算流程 step_9「选择-热稳定度2-中性」运行时**定位不到 checkbox**（日志 `未找到匹配控件`），precondition `toggle expected=off` 失效；修改定位策略后曾出现**误勾选 0 和 3 两个等级**（未识别出 2 - 中性 已勾选）。

---

## 一、控件事实（来自录音 `control_maps/recordings/20260821_134447_window_control_发送CFD计算_map.json`）

Telerik 多选下拉框（`MTDGroupComboBoxMultiSelection`）展开后，每个等级是一个 checkbox，结构如下：

```
MTDGroupComboBoxMultiSelection_ComboBoxItem (ListItem, name='2 - 中性')   ← 父
  └── MTDGroupComboBoxMultiSelection_CheckBox (CheckBox, name='')          ← checkbox 自身 name 空！
        ├── Image (name='')
        └── Text (name='2 - 中性')                                          ← 等级文本在子节点
```

要点：
- **checkbox 自身 UIA Name 为空**，等级文本 `2 - 中性` 在**子节点 Text** 上，**不是** checkbox 的 Name，也**不是**兄弟节点。
- 10 个等级 checkbox 的 `automationId` 完全相同（`MTDGroupComboBoxMultiSelection_CheckBox`），只有父 ListItem 名 / 子 TextBlock 文本能区分。
- 已勾选的 checkbox `toggleState='1'`（新采集时下拉收起，离屏实例 toggle 不可靠，默认 '0'；展开后运行时读 `CurrentToggleState` 才可靠）。
- dropdown 根窗口是 `Window_Main`（Telerik 把 Popup 渲染进主窗口 AdornerDecorator），**不是**独立顶层窗口。

---

## 二、修复演进（三阶段，逐一踩坑）

### 阶段 1：`name,control_type` 定位 —— 点错等级（0 和 3）
- 原配置：`targetMethod="name,control_type"`，`targetValue="2 - 中性,CheckBox"`。
- 坑：运行时 checkbox 自身 name 空 → `name` 匹配失败 → 只剩 `automation_id` 匹配 → **10 个 checkbox 全部命中、评分平局** → 选中错误实例（0 或 3）。
- 后果：precondition 读到错误 checkbox 的 `toggle=0`（未勾选）→ 判"需要点击"→ 点击勾选了 0 和 3；真正已勾选的 2 - 中性 从未被正确定位 → "没识别出已勾选"。

### 阶段 2：`automation_id,control_type,label_text` —— 全被预过滤（彻底失败）
- 改为 `automation_id,control_type,label_text` + `labelText="2 - 中性"`。
- 坑：fast 阶段 `_iter_uia_findall_by_automation_id` 按 automationId FindAll 到 10 个 checkbox 后，**Raw View 兄弟标签预过滤**（`_raw_sibling_label_matches`）只查候选的**兄弟节点**，而等级文本在**子节点** → **10 个 checkbox 全被过滤** → 候选为空 → `未找到匹配控件`。
- 日志指纹：`[定位耗时-失败] ... 快查=2xxx ms, 整树=xxx ms, JSON=xxx ms`，`[FlowLocator] 窗口枚举概要: count=1`（只有主窗口）。

### 阶段 3：子节点文本匹配（本次修复）
- `wt_flow_locator.py` 新增 `_raw_element_child_text_matches`：Raw View 级别检查目标元素**子节点树**（深度≤2）中是否有 Name 等于标签的 Text/TextBlock。
- `_iter_uia_findall_by_automation_id` 与 `_iter_raw_view_findall_candidates` 的 label 预过滤改为「兄弟匹配失败 → 回退子节点文本匹配」。
- `wrapper_matches_label_text` 新增 `_match_child_text_block_label`（wrapper 子节点 Text/TextBlock/Static/Label 匹配）。
- `wrapper_matches_locator` 的 `name` 分支：自身 name 空时回退 `get_wrapper_runtime_text_candidates`（含子节点文本）匹配。

---

## 三、最终生效配置

### 引擎 `wt_flow_locator.py`
- `_raw_element_child_text_matches(element, label_text, walker, props, max_depth=2)`：子节点树文本匹配。
- 两处 FindAll 候选的 label 预过滤：兄弟匹配 → 子节点匹配 → 都不过才跳过。
- `wrapper_matches_label_text`：自身 → 兄弟 TextBlock → **子节点 TextBlock** → rect 兜底。
- `wrapper_matches_locator` `name` 分支：自身文本 → 运行时文本候选（含子节点）。

### 流程 `flow_definition_发送CFD计算.json`
- `step_9`：`"targetMethod":"automation_id,control_type,label_text"`，`"targetValue":"MTDGroupComboBoxMultiSelection_CheckBox,CheckBox,2 - 中性"`，`"labelText":"2 - 中性"`。
- precondition：`{"condition":"toggle","expected":"off"}`（已勾选则跳过，未勾选才点击勾选——幂等）。

---

## 四、验证结果（2026-08-21 13:59 运行）

分别测了"2 - 中性 已勾选"和"未勾选"两种初始状态，均成功：

**已勾选场景**（precondition 正确识别 → 跳过，不误勾其它等级）：
```
流程控件定位命中(FindAll): step=step_9, control=MTDGroupComboBoxMultiSelection_CheckBox_2-中性_CheckBox, score=206
前置[toggle] step=step_9: 控件 ... ToggleState=1 (expected=off)
前置条件满足，跳过动作执行: step=step_9, status=skipped
```

**未勾选场景**（precondition 识别未勾选 → 点击勾选）：
```
前置[toggle] step=step_9: 控件 ... ToggleState=0 (expected=off)
→ 执行点击勾选 → status=success
```

关键：`score=206`（automation_id + controlType + labelText 子匹配 + uiPath 父链消歧），唯一命中 2 - 中性，不再误勾 0/3。

---

## 五、可复用结论

1. **Telerik 多选下拉 CheckBox 的等级文本在"子节点 Text"上**：定位必须用 `automation_id,control_type,label_text`，且 label 匹配要支持**子节点文本**。用 `name` 匹配（checkbox 自身 name 空）会退化成 automationId 平局 → 点错等级。
2. **Raw View 标签预过滤必须兼容"子节点文本"**：凡带 `label_text` 的 fast FindAll 预过滤，兄弟匹配不到时应回退子节点匹配，否则真实候选被全砍 → "未找到匹配控件"。
3. **checkbox 勾选状态读取用 `toggle` precondition**：`condition:"toggle", expected:"off"` 实现幂等（已勾选跳过、未勾选点击）。precondition 依赖定位正确，定位错了读到的状态就是错的。
4. **诊断日志是定位这类问题的关键**：`前置[toggle] step=X: 控件 ... ToggleState=Y (expected=off)` 一眼看出读的是哪个控件、什么状态。

> 遗留：运行时 dropdown 必须已展开（step_8 点击展开）才能定位到 checkbox；若未来需要"未展开也能勾选"，需在 click checkbox 前自动展开所属下拉框。
