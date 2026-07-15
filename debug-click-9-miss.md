[OPEN] Debug Session: click-9-miss

# Bug Summary
- Symptom: 流程包测试执行到 `click_9` 时失败，报错 `action click 未命中控件: step=click_9, control=target_control`
- Expected: 通过总控台发起流程包测试时，第一步能命中并点击“气象数据”相关目标控件

# Initial Hypotheses
1. `click_9` 的 `controls` 中 `target_control` 根本没有保存有效的定位信息，执行时拿到的是空 locator。
2. `click_9` 的控件信息已保存，但转换后的 `targetMethod` / `targetValue` 与 `_run_action_step()` 的匹配逻辑不兼容。
3. `click_9` 的窗口标题或 UI 上下文不对，导致控件查找范围错了，实际控件存在但在错误窗口下查找。
4. 总控台/编辑器显示的是流程包仓库中的步骤，但执行器运行时读取到的是另一份步骤定义，导致控件信息不同步。
5. `click_9` 的目标控件实际依赖坐标偏移或附加检查，但这些字段没有被传入执行期匹配逻辑。

# Evidence Plan
- Inspect `click_9` 的步骤定义与 `target_control` 配置
- 在执行器中为 `click_9` 添加最小化调试日志，记录 action step 读取到的 locator、窗口标题、control 定义与匹配结果
- 复现一次流程包测试，依据日志排除/确认假设

# Status
- Current phase: hypothesis
- Business logic modified: no
