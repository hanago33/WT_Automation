# [OPEN] default-height-relative-input

## 背景
- 症状：`step_14 -> step_15 -> step_16` 链路中，`step_16` 报错 `action type_text_relative 未命中父窗口相对区域`。
- 运行日志显示候选窗口只有 `XamlExplorerHostIslandWindow`，未命中期望的 `导入时间序列文件 | Window | WPF`。
- 目标：先确认点击“打开”后真实前台窗口与候选窗口结构，再决定是修步骤配置还是修相对区域窗口匹配逻辑。

## 本轮假设
1. `step_15` 点击“打开”后，真实前台窗口并非 `导入时间序列文件`。
2. `step_16` 的父窗口匹配条件过严，漏掉了真实可用的 WPF 宿主窗口。
3. `step_15` 之后窗口矩形或层级发生切换，但 `type_text_relative` 没有拿到新的有效父窗口。
4. 当前候选中的 `XamlExplorerHostIslandWindow` 是宿主层，真实目标窗口存在但未被当前评分逻辑接受。

## 调试计划
1. 读取 `step_16` 当前定义，确认父窗口与相对区域配置。
2. 只在 `type_text_relative` 对应的父窗口搜索链路中增加最小插桩，不先改业务逻辑。
3. 让用户复现一次 `step_14 -> step_15 -> step_16`，读取 ndjson 后再决定修复点。

## 运行时证据
- `step_16` 当前配置：
  - `windowTitle = 导入时间序列文件`
  - `parentWindow = {title: 导入时间序列文件, className: Window, frameworkId: WPF}`
- `trae-debug-log-default-height-relative-input.ndjson` 显示：
  - 调用 `type_text_relative` 前，前台窗口已经变成 `XamlExplorerHostIslandWindow | Window | Win32`
  - 搜索期间和失败后，前台窗口保持不变
  - 当前候选窗口只有 `XamlExplorerHostIslandWindow`，没有命中 `导入时间序列文件 | Window | WPF`
- 控件库采样 `20260701_152138_window_control_map.json` 显示：
  - 主界面顶层窗口为无标题 `Window | WPF`
  - `automationId = Window_Main`
  - 说明点击“打开”后业务上下文已经回到主界面，而不是继续停留在“导入时间序列文件”弹窗

## 结论
- 假设 1：成立。`step_15` 后真实前台窗口不再是 `导入时间序列文件`。
- 假设 2：部分成立。不是相对区域比例问题，而是父窗口规格仍指向旧弹窗，导致搜索链路无法接受主界面窗口。
- 假设 3：成立。点击“打开”后窗口层级发生切换。
- 假设 4：成立。`XamlExplorerHostIslandWindow` 是当前前台宿主层，不是本步骤应绑定的业务父窗口。

## 最小修复计划
1. 先不改相对区域比例。
2. 将 `step_16` 的 `windowTitle` 与 `parentWindow.title` 从 `导入时间序列文件` 改为主界面实际状态：空标题 `Window | WPF`。
3. 同步更新 `flow_definition.json` 与 `flow_package_registry.json`。
4. 保留调试插桩，等待用户复现 `post-fix` 结果。

## Post-Fix 第一次复现
- 运行报告 `wt_run_20260701_172942.json` 显示：
  - `step_14` 成功
  - `step_15` 成功
  - `step_16` 失败原因已变化为 `action type_text_relative 缺少 parentWindow.title 或步骤目标窗口`
- 结论：
  - 上一轮“改成无标题主窗口”的方向正确
  - 当前剩余问题不是窗口没找到，而是执行器仍然要求 `parentWindow.title` 非空，导致无标题主窗口配置被提前拦截

## 修复补充
1. 放宽 `click_relative_region` / `type_text_relative` 的执行器前置校验。
2. 允许 `parentWindow.title` 为空，只要 `className` 或 `frameworkId` 任一存在即可继续执行。
3. 保留当前调试插桩，等待第二次 `post-fix` 复现确认。
