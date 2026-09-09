# [OPEN] start-validation-regression

## 背景
- 症状：用户原本可运行的 `step_26 -> step_29`，在最近一次窗口匹配修复后，最新运行报告显示流程在 `step_26` 就失败。
- 最新报告 `wt_run_20260701_193801.json` 显示：`step_26` 先触发 `action click_relative_region 未命中父窗口相对区域`，随后进入模板兜底，但 `click_relative_region` 当前不支持模板兜底执行，最终直接失败。
- 目标：确认 `step_26` 为什么返回 `False`，判断是前台窗口/父窗口匹配回归，还是点击链路里存在新的未记录失败点。

## 本轮假设
1. `step_26` 执行时前台窗口已经不是“导入时间序列文件”，导致相对区域匹配不到目标父窗口。
2. 最近收紧的窗口评分逻辑误伤了 `step_26`，使它原本依赖的合法匹配路径也被排除。
3. `step_26` 在 `waitBefore` 之后被监视器或其他窗口抢焦点，导致 `find_flow_window_for_relative_region(...)` 选错候选或直接返回 `None`。
4. `step_26` 的失败点不在找窗，而在相对区域求点或点击前链路，但当前日志被统一收口成“未命中父窗口相对区域”。

## 调试计划
1. 先读取最新运行报告，确认失败发生在 `step_26`，而不是用户读取了旧日志。
2. 仅为 `step_26` 增加最小插桩，记录点击前前台窗口、目标父窗口、候选窗口、解析出的绝对区域与点击点。
3. 让用户再次复现 `step_26 -> step_29`，依据新 ndjson 判断是否回归自上次窗口评分修复。

## 运行时证据
- 最新报告 `wt_run_20260701_194340.json` 显示流程确实只执行到 `step_26`，失败信息仍是“`click_relative_region` 未命中父窗口相对区域”后进入不支持的模板兜底分支。
- `trae-debug-log-start-validation-regression.ndjson` 显示，`step_26` 开始时前台窗口是 `GM自动化流程监视器 | TkTopLevel | Win32`，不是 WT 的 WPF 窗口。
- 同一份日志里，`find_flow_window_for_relative_region(...)` 的 `topCandidates` 能看到无标题主窗口 `Window_Main | Window | WPF`，它的子节点里包含 `导入时间序列文件 | Window | Window`，但桌面顶层枚举并没有直接返回这个子窗口。
- 因为最近的窗口评分修复禁止了非前台空标题窗口直接冒充 titled dialog，`Window_Main` 被正确打成 `-1`，但同时也失去了继续下钻到真实子窗口的机会。

## 结论
- `step_26` 的回归根因不是目标窗口不存在，而是窗口查找只停留在顶层窗口，没继续进入 `Window_Main` 的子窗口去找真正的“导入时间序列文件”对话框。
- 这与 `step_27` 的旧问题不同：`step_27` 之前是错误地直接把主窗口当父窗口；`step_26` 则是在禁止这种错误匹配后，缺少“顶层未命中时下钻子窗口”的补偿路径。

## 当前修复
1. 保留之前对 `step_27/step_29` 的收紧规则，不再让非前台空标题主窗口直接冒充 titled dialog。
2. 新增最小补偿逻辑：`find_flow_window_for_relative_region(...)` 在顶层窗口未命中时，会从候选顶层窗口的 descendants 中继续查找真正匹配 `title/className/frameworkId` 的子窗口，并优先返回该子窗口。

## 下一步
1. 再次复现 `step_26 -> step_29`。
2. 对比 `post-fix` 日志，确认 `step_26` 是否能拿到真正的“导入时间序列文件”窗口并恢复点击。
