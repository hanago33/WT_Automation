---
name: asset_assisted_flow_building
description: 构建 WT 自动化流程步骤时，先复用 control_maps 控件库资产、再生成步骤的标准方法论；用于"用自然语言生成真实可用流程"的场景。
---

# 资产辅助流程构建（Asset-Assisted Flow Building）

本 Skill 规定：在把自然语言需求转换成 `add_step` / `add_sequence` 时，**必须优先复用项目已有的控件资产（control_maps）**，而不是凭空编造 `control_id`。这样才能产出"真实可用、一次跑通率高"的流程。

## 何时使用
- 用户要求"生成/新建/自动化某个操作流程""把这段话变成步骤"等。
- 用户描述里出现了可对应到界面控件的目标（按钮、下拉框、输入框、选项卡、树、表格等）。

## 标准流程（务必遵循）
1. **先检索，再填写**：当你不确定 `control_id`，或想确认某个控件是否存在时，调用 `find_control(query)` 工具。
   - 中文描述直接用中文：`find_control("风机类型下拉框")`。
   - 英文标识片段也可用：`find_control("GeographicalData")`。
2. **从候选里选最匹配项**：工具返回按相关度排序的候选，每项带 `control_id`（即 `targetValue`）、名称、类型、权威度、来源。优先选 `source=standard_catalog` 且权威度高的。
3. **把选中的 `control_id` 填入 `add_step` 的 `control_id` 字段**。不要自己编造 targetValue。
4. **控件库命中失败时兜底**：若 `find_control` 无结果，按 WT/WPF 三层兜底链改用：
   - 相对区域定位 `click_relative_region`；
   - 参考窗口重选；
   - 模板/AI 图像兜底（image_templates）。
   并在步骤 `fallbackChain` 中标注，而不是硬填一个可能不存在的 ID。
5. **生成后自检**：每个 `add_step` 的 `actionConfig` 字段应闭合（action / controlId / text / onError / timeoutSeconds 等），避免"点了不存在的控件""假成功"等常见问题。

## 复用已有流程（维护场景）
- **解释/诊断**：对已存在的 `flow_definition.json`，可用"流程助手"的解释、日志诊断能力定位脆弱点。
- **编辑/比对**：修改已有流程时，优先在原有步骤结构上增量改动，保持字段完整；复用既有的 `control_id`，不要引入新控件除非已通过 `find_control` 验证。

## 收益
- 显著降低"点击不存在控件"类失败（最常见痛点）。
- 生成的步骤直接对齐 `control_maps` 真实标识，一次跑通率更高。
- 方法论沉淀为 Skill 后，每次构建走统一路径，又快又省（减少反复试错与幻觉）。
