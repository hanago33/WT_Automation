[OPEN] CFT02 Step16 Drift Debug Session

## Session
- session_id: cft02-step16-drift
- created_at: 2026-07-03
- symptom: 流程包2执行 CFT02 创建时，从 step16 开始出现连续点击漂移
- scope: 先只读排查最新运行日志、流程包定义与窗口定位链路；在拿到证据前不修改业务逻辑

## Hypotheses
1. Excel 导入导出后，step16-26 丢失了 `parentWindow.title` 等关键锚点元数据。
2. step15 到 step16 之间缺少足够等待，窗口尚未稳定便开始相对区域点击。
3. `wt_flow_locator.py` 在本次运行中未取到原生窗口矩形，回退到不稳定矩形。
4. CFT02 导入包与稳定主包的关键字段存在差异。
5. 最近窗口重选或焦点切换逻辑出现回归，导致错误参考窗口持续沿用。

## Evidence Log
- 最新运行日志 `wt_run_20260703_200437.json` 显示 `step_16_2` 命中的 `windowTitle` 仍是 `导入时间序列文件`，但 `windowRect` 为 `left=128, top=76, width=2304, height=1364`。
- 同次运行中 `step_17_2 / step_19_2 / step_22_2` 的相对点击都沿用了这套矩形，随后三个 `select_dropdown_item_runtime` 全部未枚举到候选项，符合“下拉框未真正展开”的表现。
- 历史跑通报告 `wt_run_20260703_145924.json` 中，同一窗口标题对应的稳定矩形为 `left=115, top=25, width=2329, height=1465`，并且三个下拉项选择全部成功。
- 现有矩形 trace 证明：失败会话里 `GetWindowRect(handle)` 与基础矩形一致，父级 `Window_Main` 又过大，因此当前逻辑会落回错误的内容区矩形。
- 根因收敛为：WPF 对话框在部分会话中暴露了“内容区 HWND”，导致相对区域基准取成内容区而非外框。

## Actions
- 已为 `select_dropdown_item_runtime` 增加失败日志，输出预期窗口与候选项信息。
- 已修改 `wt_flow_locator.py`：矩形解析优先回查“同进程、同标题”的顶层 HWND 外框；若命中且仅比当前矩形多出合理边框/标题栏，则使用该外框。
- 已将矩形 trace 调试范围扩展到 `step_16_2 / step_26_2`，便于直接观察 CFT02 运行时的矩形来源。
- 已追加 `GW_OWNER / GetParent` 候选链路，并把 `EnumWindows(title/process)` 的被拒绝候选也写入 trace，用于判断坏句柄是否只是内容区 HWND。
