# [OPEN] time-series-input-regression

## 背景
- 症状：用户调整后，原本已恢复正常的“时间序列文件路径”步骤再次出现“日志执行但没有成功键入”的回归问题。
- 时间：2026-06-30

## 当前事实
- 之前已确认过一次根因：`step_14` 误命中了同名 `Static/Text` 标签，而不是右侧真实 `Edit` 输入框。
- 当时已将步骤修正为：限定 `windowTitle=打开`，并按 `name + class_name=Edit` 执行 `type_text`。
- 用户这次明确反馈“我改了一下，又改出问题了”，说明当前问题大概率是配置回归，而不是运行层新缺陷。

## 本轮假设
1. `step_14` 的 `targetMethod/targetValue` 被用户改回成过宽匹配，重新命中了同名标签而不是 `Edit`。
2. `step_14` 的 `action` 被改成了 `click/double_click` 之类的非输入动作，导致日志看似执行但没有写值。
3. `step_14` 的 `text/value` 被重新包上外层引号、变量名或空值，导致输入内容异常或未输入。
4. `step_13` 到 `step_14` 之间的窗口上下文发生变化，`step_14` 虽存在但已不在“打开”对话框中执行。

## 调试计划
1. 先静态核对当前 `flow_definition.json` / `flow_package_registry.json` 中 `step_14` 的最新实际配置。
2. 如果现有 `time-series-path-input` 插桩仍在，就优先读取最新运行日志；若日志不足，再补最小插桩。
3. 仅在证据明确后做最小修复，并要求用户只复现 `step_13 -> step_14` 做对比验证。
