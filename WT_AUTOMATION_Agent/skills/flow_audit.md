# 流程链路检查审核纠错（Flow Audit & Fix）

> 从项目实战沉淀的流程文件检查标准流程。用于审核/修复 flow_definition.json 中步骤的动作、控件、参数与顺序问题，可反复调用。

## 适用场景

- 拿到一份 flow_definition.json，怀疑有步骤配置错误、执行失败、或生成结果质量不佳
- 步骤报错：动作不支持、控件不存在、控件类型与动作不匹配、参数缺失
- 需要系统性排查"为什么某一步跑不过"，并给出可执行的修正建议

## 标准工作流

1. **加载并文本化流程**：用 `flow_ops.load_flow` 读取，`flow_ops.flow_to_text` 压成紧凑文本供模型阅读
2. **确定性规则检查**（不依赖模型，必做）：调用 `flow_audit.audit_flow(flow)`，覆盖：
   - 步骤 `id` / `name` 非空（执行器 `wt_flow_validation` 第一关）
   - 动作是否被执行器支持（`set_combobox` / `menu_select` / `double_right_click` 等）
   - `target_required` 动作是否缺 `controlId`；`controlId` 是否在控件库存在（`control_search.best_control_for_step`）
   - 控件类型 ↔ 动作类型匹配（如 `set_combobox` 应指向 ComboBox；输入类动作不应指向纯展示控件）
   - 输入参数完整性（`input_key`：text / value / menuPath / delta / seconds）
   - 相对区域动作（`type_text_relative` / `click_relative_region`）的 parentWindow 与 relativeRegion 参数
   - 控件 targetMethod / targetValue 段数一致性
   - 重复步骤名
3. **控件库反查**：对每个 `control_id` 用 `control_search.resolve_control` / `best_control_for_step` 反查真实记录，用库内字段（name / labelText / controlType / qualityTier）核对是否张冠李戴
4. **模型语义审核**（规则之外的"合不合理"）：
   - 动作选型不合理（click 选下拉项、type_text 点按钮）
   - 控件与业务意图不匹配
   - 参数值明显异常（空值 / 格式 / 单位）
   - 顺序问题（未等待控件、窗口未就绪、缺前置步骤）
   - 冗余或缺失关键步骤
   - 输出 JSON 数组 `[{step_index, issue, suggestion}]`
5. **给出修正建议并落地**：
   - 明确的确定性错误 → 直接建议改字段（动作 / control_id / 参数）
   - 控件不存在 → 建议 `find_control` 检索替换，或改用相对区域定位
   - 修正后走 `wt_flow_validation.validate_step_definition` 复验，确保执行器可加载
6. **沉淀经验**：把本次发现的"新类型问题 + 修复方式"追加到本 skill，形成可复用清单

## 高频问题速查

| 现象 | 大概率原因 | 修复 |
|---|---|---|
| 执行器报"缺少步骤ID" | 步骤 `id` 为空 | 生成时补唯一 id（`step_<uuid>`） |
| 动作非法 | 用了执行器不支持的 action | 换成 `get_action_names()` 内动作 |
| 目标控件不存在 | control_id 编造 / 库未刷新 | `find_control` 检索真实控件；或 `control_search.reload()` |
| 输入类动作失败 | 控件是纯展示类型（Text/Image） | 换可输入控件，或改相对区域输入 |
| set_combobox 不生效 | 控件不是 ComboBox | 换下拉框控件，确认 optionValues |
| 相对区域点击偏移 | parentWindow / relativeRegion 缺失 | 先锁父窗口标题/类名，再补 0~1 归一化矩形 |

## 工具与校验锚点

- `control_search.best_control_for_step(action, control_id)`：控件反查（exact / 模糊）
- `flow_audit.audit_flow(flow)`：确定性规则检查
- `wt_flow_validation.validate_step_definition(step)`：执行器同源校验（最终锚点）
- `schemas.get_action_names()`：合法动作全集（已与执行器对齐）
