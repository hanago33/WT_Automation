# [OPEN] private-group-click

## 背景
- 症状：`step_8`“点击-私有分组”已经从占位步骤改成真实点击步骤，但用户反馈点击没有成功。
- 时间：2026-06-30

## 当前事实
- `step_8` 当前配置为 `actionType=action`，`action=click`，`controlId=step_8_control_1`。
- 目标控件来自弹窗“创建一个新的气象对象”中的文本控件，当前控件信息显示 `className=TextBlock`、`controlType=Text`、`name=私有`。
- 该控件 `IsKeyboardFocusable=False`，存在“可识别但不可直接交互”的风险。

## 本轮假设
1. `step_8` 已经能定位到 `TextBlock`，但该控件只是显示层，不响应 `click_input()`，所以日志可能会显示成功但界面无变化。
2. 实际可点击的是 `TextBlock` 的父容器或邻近可交互控件，当前 `controlId` 指向错层级。
3. 当前步骤虽然配置了 `windowTitle`，但运行时仍可能命中错误的同名/同框架元素，需要补充点击前后控件与前台窗口证据。
4. “私有分组”需要的不是常规单击，而是点击文本所在区域的特定位置或相对区域，而不是控件中心点。

## 调试计划
1. 阅读 `step_8` 当前控件定义与点击执行路径，确认哪里最适合加最小插桩。
2. 只增加运行时插桩，记录 `step_8` 点击前后的目标控件、控件矩形、前台窗口和点击结果。
3. 让用户仅复现 `step_8`，读取 ndjson，确认是“定位错对象”还是“对象对但不可点击”。
4. 证据明确后再做最小修复。

## 本轮证据
- `trae-debug-log-private-group-click.ndjson` 显示，`step_8` 命中的确实是目标弹窗中的 `TextBlock(name=私有, className=TextBlock, controlType=Text)`，因此“完全定位错对象”的假设暂时不成立。
- 点击前前台窗口仍是 `GM自动化流程监视器`，点击后前台才切换为 `创建一个新的气象对象`，说明首击先承担了“把真实弹窗切回前台”的作用。
- 该目标控件依旧满足 `IsKeyboardFocusable=False`，且 `rawInspectText` 中不存在 `Invoke/SelectionItem/Value/Toggle` 等可交互模式，符合“文本展示层不可直接交互”的风险特征。

## 当前结论
1. 假设 1 基本成立：目标命中了文本展示层，不能把 `click_input()` 成功返回等同于业务点击成功。
2. 假设 3 部分成立：运行时真正的问题不是命中错窗口，而是点击前焦点停留在自动化监视器，导致首击更像是激活业务弹窗。
3. 假设 2 与假设 4 暂未被完全排除，但在当前证据下可以先采用更小范围的修复验证，而不立即改步骤定义。

## 已实施修复
- 在 `wt_flow_locator.py` 的 `click_flow_control(...)` 中增加了一个最小修复：
  - 若首击后检测到前台从其他窗口切回了业务窗口，且目标是 `TextBlock`/不可聚焦文本类控件，则自动在同一点补一次真实鼠标点击。
  - 该补点击会继续写入 ndjson，便于对比 `pre-fix` 与 `post-fix-v1`。

## post-fix-v1 复测结论
- `post-fix-v1` 的 `refined` 事件已经证明补点击确实执行了，但点击点为 `x=2418,y=323`。
- 同次日志中的真实弹窗矩形是 `left=664,right=1896,top=378,bottom=1138`，因此该点击点并不在目标弹窗内部。
- 这说明当前命中的 `TextBlock(name=私有)` 实际来自同进程中的另一个窗口/视图，而不是“创建一个新的气象对象”弹窗中的目标元素。

## 第二轮最小修复
- 已将 `iter_flow_search_windows(...)` 的窗口排序收紧：
  - 当步骤/控件显式提供 `windowTitle` 时，标题命中的窗口会被大幅优先。
  - 仅靠同进程匹配但标题未命中的窗口会被明显降权，避免主界面同名控件抢在目标弹窗之前被扫描到。
- 本轮修复对应新的运行标识：`post-fix-v2`。

## post-fix-v2 复测结论
- `post-fix-v2` 仍然命中了同一个错误对象，矩形依旧是 `left=2313, top=309, right=2523, bottom=337`，并未进入目标弹窗矩形范围。
- 这说明仅靠窗口排序调优还不够，因为错误对象已经可能被 `FLOW_CONTROL_CACHE` 缓存，且缓存校验之前没有验证“该控件是否属于期望窗口标题”。

## 第三轮最小修复
- 已在 `wt_flow_locator.py` 中新增顶层窗口归属校验：
  - 为控件计算顶层 `Window` 祖先。
  - 在 `score_control_match(...)` 中将“控件是否属于期望窗口标题”纳入评分。
  - 在 `get_cached_flow_control(...)` 中加入同样的窗口归属校验，缓存命中若不属于目标窗口则直接失效。
- 本轮修复对应新的运行标识：`post-fix-v3`。

## post-fix-v3 复测结论
- `post-fix-v3` 不再误命中主界面上的同名文本，运行报告已从“假成功”转为真实失败：`action click 未命中控件: step=step_8, control=step_8_control_1`。
- 这说明运行层筛选已经把错误对象排除掉了，但目标弹窗内并没有稳定暴露出可用的 `name=私有` 控件链路。

## 第四轮修复
- 结合 `rawInspectText` 中的原始矩形 `BoundingRectangle [l=845,t=1002,r=1816,b=1035]` 与真实弹窗矩形，已将 `step_8` 的业务动作从 `click` 切换为 `click_relative_region`。
- 同步修改文件：
  - `flow_definition.json`
  - `flow_packages/flow_package_registry.json`
- 当前使用的相对区域：
  - `x=0.1469`
  - `y=0.8211`
  - `width=0.7881`
  - `height=0.0434`
  - `anchor=center`

## 下一步
1. 仅复测一次 `step_8`。
2. 对比 ndjson 中 `post-fix-v1` 的 `after/refined` 事件。
3. 请先验证第四轮修复；若仍无效，再回到运行时证据，缩小区域或改为更贴近父容器中心的点击点。
