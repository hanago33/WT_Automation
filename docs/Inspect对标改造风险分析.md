# Inspect 对标改造 —— 改动风险分析
> 日期：2026-07-29　配套文档：`docs/Inspect对标差距调研报告.md`
> 结论先行：**P0 方案技术上可行，但"直接把实时探测嵌进控件库维护窗口"一步到位的风险偏高，建议按三阶段推进，每阶段独立可回退。**

---

## 1. 改动面盘点

| 改动项 | 涉及文件 | 性质 |
|---|---|---|
| 持久跟随高亮 overlay | `build_control_map_library.py`（现有 `_show_locator_highlight` L5462） | 改造现有 |
| 全局热键（冻结/导航/采集） | 新增热键管理 + 探测循环接入 | 新增 |
| 悬停→树联动"只看不采"模式 | `build_control_map_library.py`（`_hover_tick` L5770、runtimeId 反查 L5222） | 改造现有 |
| live_inspector 组件抽取 | 新文件 + C 内改引用 | 重构 |
| 控件库维护内嵌实时面板 | `WT_Flow_Editor.py`（L2755+，9200 行巨石文件） | 侵入式新增 |
| 采集回流自动刷新 | B 树构建 L3434-3493 | 改造现有 |

---

## 2. 高风险项（必须先解决，否则引入新问题）

### R1【严重】持久 overlay 会被探测"自伤"
- 现有高亮是 Tk Toplevel + `-transparentcolor magenta`（L5480-5496）。透明色区域可穿透，但**红色边框像素本身可被 hit-test 命中**。
- 改成"持久跟随"后：overlay 永远套在光标目标外圈 → `from_point` 极易命中 overlay 边框 → 探测结果变成 overlay 自己 → 高亮跳到 overlay 上 → 死循环/错采。
- 更隐蔽：`_hover_probe_once` 采子树时排除了自身 pid（L5814 `excluded_process_ids=[os.getpid()]`），**但去重探针 `_probe_hover_hit_key`（L5822-5829）没有任何自进程排除** —— 目前靠"3 秒后 overlay 自毁"侥幸避开，持久化后必炸。
- **缓解（必做）**：overlay 创建后用 `SetWindowLong` 加 `WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE`（鼠标完全穿透）；同时给 `_probe_hover_hit_key` 补自进程 pid 过滤作双保险。全仓目前 **无任何 WS_EX_TRANSPARENT 使用先例**，需新写并实测。

### R2【严重】同进程嵌入会冻死编辑器主线程
- 悬停探测循环是 `root.after` 驱动、**同步跑在 Tk 主线程**（`_hover_tick` L5770-5780），`collect_subtree_at_point` 对 WPF 深树是秒级操作。
- C 是独立进程，卡一下无所谓；**嵌进 WT_Flow_Editor 后每次探测都会卡死整个编辑器 UI**（用户正在编辑的流程界面失去响应）。
- **缓解（必做）**：探测/采集放 worker 线程，结果经 `queue` + `after` 回 UI 线程；轻量 hit-test（from_point 单点）可留主线程，子树遍历必须下线程。

### R3【高】COM 线程模型冲突
- C 在 import pywinauto **之前** 设 `sys.coinit_flags = 0`（MTA，L15-18）；WT_Flow_Editor **没设**，且 pywinauto 是函数内 lazy import（L8738 等处）。
- 若编辑器先以默认 STA 初始化了 COM，再 import C 模块，`coinit_flags` 静默失效 → 跨线程 UIA 调用性能骤降或偶发 `CoInitialize` 异常，且**症状是随机的、难复现的**。
- 叠加 R2 的 worker 线程后，线程内必须显式 `CoInitializeEx(MTA)`，否则 comtypes 在线程里行为不确定。
- **缓解**：Phase 2 动 WT_Flow_Editor 时，在其文件头同样设置 `sys.coinit_flags = 0`（需验证对现有录制器/执行器无影响）；worker 线程统一入口做 COM 初始化。

### R4【高】全局热键与现有录制器 pynput 监听冲突
- WT_Flow_Editor 的交互采集已在用 pynput 全局监听，**且占用了 F8**（L1902-1923，F8=捕获鼠标指向控件）。
- 新探测面板再起一套全局监听：① 两个 keyboard.Listener 并存虽可行，但同一按键双触发；② 若照抄 Inspect 用 Ctrl+Shift+F6~F9，需确认目标软件（Meteodyn WT）不占用；③ `RegisterHotKey` 方案需要消息泵，与 Tk 主循环集成复杂且注册失败（键被占）要有降级。
- **缓解**：统一走 pynput（依赖已存在，不引新库）；键位避开 F8，用 `Ctrl+Alt+方向键/空格` 系列；**录制模式与探测模式互斥**（开一个自动停另一个）；热键注册失败必须提示而非静默。

---

## 3. 中风险项

### R5【中】抽取 live_inspector 破坏现有测试与函数契约
- **5 个测试文件直接 import `build_control_map_library`**：`test_acquisition_coverage / test_control_map_label_association / test_label_companion_and_inspect_fields / test_wt_flow_editor_utils / test_subtree_supplement`。
- 抽取组件时若移动/改签名 `collect_subtree_at_point`、`_build_wrapper_identity` 等被测函数 → 测试全红。教训库明确：**公共入口签名变更必须同步全部调用点**。
- **缓解**：抽取只做"新文件 + 原文件保留同名转发"（re-export），原 import 路径不变；每次抽取后立即跑全量 `tests/`。

### R6【中】双 Tk root / 窗口生命周期
- C 的 GUI 类绑定自建 `tk.Tk()` root；嵌入 B 必须改为接受 parent 注入（Toplevel）。两个 Tk root 并存会导致 `after`/变量作用域错乱。
- 三个 topmost 竞争者（编辑器主窗、探测面板、overlay）z-order 需明确策略，否则出现"高亮框被自己面板挡住"。
- **缓解**：live_inspector 设计为"必须传入 parent"的 Frame/Toplevel 组件；overlay 永远最顶（每次移动时重新 `-topmost` lift）。

### R7【中】GUI 无自动化测试，回归靠人工
- 控件库维护窗口（L2755+）和 C 的 GUI 均无 UI 级测试，`test_wt_flow_editor_utils` 只覆盖纯函数。改动后的回归完全依赖人工操作验证。
- **缓解**：把新逻辑尽量写成纯函数（探测状态机、去重键、树定位路径计算）放 utils 层并配单测；GUI 层只做薄封装；每阶段附一份 5 分钟人工回归清单（见 §5）。

### R8【中】实时整树枚举性能
- 深树枚举（`FULLTREE_MIN_DEPTH=18` + RawViewWalker/.NET dumper）本身秒~十秒级。若"打开实时面板"即整树枚举，体验反而倒退。
- **缓解**：实时树懒加载（只枚举可见层，展开时再取子级）；悬停命中时只补齐"命中节点的祖先链"而非全树刷新。

---

## 4. 低风险项（提示即可）

| 项 | 说明 |
|---|---|
| R9 字段契约 | "只看不采"模式不写库，不触碰 normalize 白名单/Excel 往返 5 同步点；只有"热键采集入库"复用 C 现有 `_finish_supplement` 通道，不新增字段则无风险。**若后续新增字段，必须过白名单**（教训库头号静默坑） |
| R10 入口/打包 | 新增 py 文件对 `.bat` 启动器无影响（源码运行）；`csv_to_xlsx.spec` 与本改动无关。注意 `.bat` 新增引用时的 `%~dp0` 规则 |
| R11 A 工具下线 | `control_live_detector.py` 无人 import（独立脚本），下线零风险；但先别删，Phase 2 完成后再淘汰 |

---

## 5. 降险实施策略（三阶段，每阶段可独立回退）

### Phase 0：只动 C，独立进程内验证三大机制（风险最低）
- 在 `build_control_map_library.py` 内实现：持久穿透 overlay（R1 方案）+ "只看不采"模式开关 + pynput 全局热键（冻结/采集/父子导航）。
- **不碰 WT_Flow_Editor，不抽文件**。出问题回退 = 还原单文件。
- 验收：悬停时红框实时跟手不闪、探测不命中 overlay 自身、目标窗口有焦点时热键仍生效、Esc/冻结可靠。

### Phase 1：抽取 live_inspector 组件（纯重构，行为不变）
- 新文件承载探测状态机 + overlay + 热键管理；`build_control_map_library.py` 保留同名转发，import 契约不变。
- 验收：全量 `tests/` 通过（重点上述 5 个文件）；C 的采集功能与 Phase 0 行为逐项一致。

### Phase 2：嵌入控件库维护（风险集中释放点）
- WT_Flow_Editor 文件头补 `sys.coinit_flags = 0`（先单独验证录制/执行不受影响）；探测走 worker 线程 + 队列（R2/R3）；录制与探测互斥（R4）；实时树懒加载（R8）；采集入库后增量刷新静态树。
- 验收：探测全程编辑器 UI 不卡；录制器 F8 功能不受干扰；库树自动刷新无需手动"刷新控件库"。

### 每阶段人工回归清单（5 分钟）
1. 对 Meteodyn WT 主窗悬停 10 个控件：高亮跟手、属性正确、无 overlay 自命中。
2. 冻结热键 → 树上走父/子/兄弟 → 高亮同步。
3. 采集 1 个控件入库 → JSON 落盘 → 维护树可见。
4. 打开/关闭探测 3 次：无残留 overlay、无残留监听线程、topmost 恢复。
5. 跑 `python -m pytest tests/ -q` 全绿。

---

## 6. 明确不做（防止范围蔓延）
- 不动定位评分算法、fallbackChain、self-heal 逻辑。
- 不重构 WT_Flow_Editor 巨石类本身，只以"组件注入"方式挂面板。
- 不改 normalize 白名单与 Excel 往返字段（除非采集确需新字段，届时按 5 同步点走）。
- 不引入新第三方依赖（热键复用 pynput，穿透窗口用 ctypes/user32）。
