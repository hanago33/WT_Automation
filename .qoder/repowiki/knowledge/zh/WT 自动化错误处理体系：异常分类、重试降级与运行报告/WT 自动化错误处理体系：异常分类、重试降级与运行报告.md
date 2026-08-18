---
kind: error_handling
name: WT 自动化错误处理体系：异常分类、重试降级与运行报告
category: error_handling
scope:
    - '**'
source_files:
    - wt_flow_executor.py
    - wt_run_reporting.py
    - wt_action_schema.py
    - wt_flow_validation.py
    - WT_AUTOMATION_Agent/schemas.py
    - WT_AUTOMATION_Agent/agent.py
    - WT_AUTOMATION_Agent/log_diagnosis.py
    - wt_projection_helpers.py
---

## 1. 系统与方法
该仓库采用「标准 Python 异常 + 结构化运行报告」的混合模式，未定义自定义 Exception 基类，而是通过以下机制实现统一的错误处理：
- 使用 `ValueError` / `RuntimeError` / `json.JSONDecodeError` / `OSError` 等内置异常表达参数校验失败、执行失败、JSON 解析失败和 I/O 错误。
- 在步骤执行层集中实现「重试 → Fallback 链 → 模板兜底 → AI 干预」的多级恢复策略，并通过 `wt_run_reporting.py` 将每一步的执行状态写入 JSON 运行报告。
- Agent 侧（`agent.py`）对 LLM 返回进行强校验，遇到空 choices、缺失 tool_calls、JSON 解析失败等情况直接抛出 `ValueError`，由上层捕获并提示修正。

## 2. 关键文件与位置
- 步骤执行与错误恢复核心：`wt_flow_executor.py`
- 运行报告与统计：`wt_run_reporting.py`
- Action Schema 与 stepPolicy 映射：`wt_action_schema.py`、`WT_AUTOMATION_Agent/schemas.py`
- 步骤/流程定义校验：`wt_flow_validation.py`、`WT_AUTOMATION_Agent/agent.py`（`validate_step`）
- 诊断辅助（从运行报告提取失败步骤）：`WT_AUTOMATION_Agent/log_diagnosis.py`
- 阶段执行专用异常：`wt_projection_helpers.py`（`StageExecutionError` 继承 `RuntimeError`）

## 3. 架构与约定
### 3.1 异常类型与用途
| 异常类型 | 典型触发点 | 说明 |
|---|---|---|
| `ValueError` | 缺少必填字段（如 `controlId`）、不支持的 action、参数非法（offsetX/Y 非数字） | 用于输入/配置校验失败 |
| `RuntimeError` | 控件定位失败、超时、循环引用检测、续跑条件不满足、模板兜底失败 | 用于运行时执行失败 |
| `json.JSONDecodeError` | LLM 返回的 arguments 不是合法 JSON | 被包装为 `ValueError` 再抛出 |
| `OSError` / `ConnectionAbortedError` 等 | 文件读取、网络请求失败 | 在 GUI、history_store、control_search 等处统一捕获并降级 |

### 3.2 重试与降级策略（wt_flow_executor.py）
- `_run_action_step_with_retry`：根据 `actionConfig.stepPolicy`（或旧字段 `onError/retryCount/retryInterval`）计算总尝试次数，每次失败记录 `attemptCount`、`lastActionError` 后按间隔重试。
- 所有重试耗尽后进入 `_try_fallback_chain`：遍历 `fallbackChain`（支持 `template`、`coordinate`、`ui_path_search` 三种方法），成功则标记 `_fallback_level` 并回写反馈。
- 若 fallback 链也失败，且 `onError == "fallback"` 且配置了 `fallbackTemplate`，则调用 `run_action_step_with_template_fallback` 基于屏幕截图模板匹配坐标兜底执行。
- 模板兜底仍失败时，最终重新抛出原始异常，由上层决定是停止还是继续。

### 3.3 运行报告（wt_run_reporting.py）
- `start_run_report` 初始化包含 `runId`、`status`、`summary`（executed/success/failed/skipped/fallback 计数）的报告对象。
- `report_step_result` 追加单步结果，自动累计耗时与各类计数；当检测到 `fallbackTemplateUsed` 或 `fallbackUsed` 时累加 `fallbackCount`。
- `finalize_run_report` 计算最终状态（若有失败则降为 `partial_success`），写入 `logs/run_reports/<runId>.json` 和 `logs/last_run_report.json`。

### 3.4 校验层（wt_flow_validation.py & agent.py）
- `validate_step_definition` 检查 action 名称合法性、必填控件/输入、父窗口 frameworkId 白名单、相对区域数值范围、续跑条件枚举、flow_ref 引用存在性等，返回错误字符串列表。
- `validate_flow_definition` 额外检查步骤 ID 唯一性、流程包 ID 唯一性、流程包引用的 stepId 是否存在。
- Agent 的 `_parse_tool_calls` 对 LLM 返回做严格校验，空 choices、无 tool_calls、JSON 解析失败均抛 `ValueError`。

### 3.5 诊断与反馈闭环
- `log_diagnosis.py` 提供 `load_run_report`、`extract_failures`、`build_diagnosis_prompt`，把运行报告中的失败步骤抽取出来，构造给 LLM 的诊断提示。
- 执行器在 fallback 成功时通过 `_write_feedback_to_flow` 向 `flow_definition.json` 的 `feedbackHistory` 追加记录，限制最多 500 条，便于后续分析。

## 4. 约定与约束
- **异常选择**：参数/配置错误用 `ValueError`，运行时不可恢复错误用 `RuntimeError`，I/O 和网络错误用 `OSError` 及其子类，JSON 解析错误用 `json.JSONDecodeError`。未见自定义异常基类（除 `StageExecutionError` 外）。
- **stepPolicy 优先级**：新字段 `stepPolicy.onFail` 优先于旧字段 `onError`，但 `_resolve_step_policy` 保证零副作用——若无 `stepPolicy`，旧字段完全保持原样。
- **onError 模式**：仅允许 `continue` / `retry` / `stop` / `fallback` / `ask` 五种（`ALLOWED_ON_ERROR_MODES`），其他值会被归一化为默认行为。
- **续跑条件**：仅允许 `exists` / `present` / `visible` / `enabled` / `gone`（`ALLOWED_CONTINUE_WHEN_CONDITIONS`），非法值在校验阶段报错。
- **父窗口框架**：仅允许 `WPF` / `Win32` / `uia` / `WinForm`（`ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS`），超出范围在校验时报错。
- **相对区域锚点**：仅允许 `center` / `left_center` / `right_center`（`ALLOWED_RELATIVE_REGION_ANCHORS`）。
- **报告格式**：所有运行报告必须包含 `stepResults` 数组，每项含 `stepId`、`stepName`、`status`、`elapsedSeconds`、`error`、`extra`，并由 `finalize_run_report` 统一落盘。
- **诊断输出**：`log_diagnosis.build_diagnosis_prompt` 要求以「失败定位 → 原因分析 → 修复建议」三段式中文回答，确保 LLM 诊断一致性。

## 5. 总结
该项目的错误处理围绕「轻量异常 + 多层恢复 + 结构化报告」展开：校验层尽早暴露配置错误，执行层通过重试/fallback/模板兜底/AI 干预逐级自愈，最后通过 JSON 运行报告固化执行轨迹，再由诊断模块驱动 LLM 给出修复建议。整体设计简洁、可观测性强，适合 RPA 场景中对 UI 不稳定性的容错需求。