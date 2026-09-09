# Inspect 实时定位交互 vs WT 悬停补采：流畅度差异与优化

> 整理日期：2026-07-30
> 定位：承接《Inspect对标差距调研报告》《Inspect对标改造风险分析》，聚焦**流畅度**这一最痛的点，逐行对照当前代码给出可落地的 Phase 0 优化项。
> 结论先行：**WT 悬停补采的"卡"不是单点 bug，而是"轮询 + 停顿门控 + 悬停即全子树采集"三件事叠加，把 Inspect 的 O(1) 单元素即时查看，替换成了"每 350ms 轮询 → 停顿 0.7s 确认 → 后台跑一次 12 层深、最长 12s 的子树 BFS → 主线程整树刷新"的链路。**

---

## 〇、必须先更正的前期认知（重要）

早期判断里说"高亮只有补采后 3 秒""补采在主线程同步""`_probe_hover_hit_key` 无自进程排除""无穿透 overlay"，**这些在当前代码里都已不成立**。逐一核对真实状态：

| 早期判断 | 当前代码实际（已具备） | 位置 |
|---|---|---|
| 高亮仅补采后 3 秒闪烁 | 已有 `_PersistentHighlight` 持久红框，跟随命中矩形 | `class _PersistentHighlight` L4910 |
| 无鼠标穿透 | overlay 已设 `WS_EX_TRANSPARENT\|WS_EX_LAYERED\|WS_EX_NOACTIVATE`，鼠标完全穿透 | L4921-4955 |
| 补采在主线程同步卡死 | 已抽 `_worker_loop` 后台线程 + 队列 + 防重入，秒级采集卸载 | L6728-6831 |
| `_probe_hover_hit_key` 无自进程排除 | 已排除 `pid == os.getpid()` | L6482-6488 |
| 无全局热键 | 已有 pynput 全局监听 F6 冻结 / F7 采集 / Ctrl+Shift 方向键导航 | L6518-6570 |
| 查看与采集未分离 | 已有"只看不采"复选框 `var_look_only` + `_hover_look_only` | L6505-6514 |

**也就是说，采集器的"骨架"已经相当 Inspect 化了。** 流畅度问题剩下的是"骨架搭好了但行为参数与 Inspect 相反"——核心矛盾见下文根因 1/2/3。

**但仍有一个真实 bug（根因 0）**：`_hover_look_only`（只看不采）**形同虚设**——它只在日志里被打印（L6448），并未在补采入队处（L6450-6465）做 `if self._hover_look_only: return` 拦截。开启"只看不采"后，子树采集照常被塞进 worker 队列。这意味着"只想看看"的模式本质不存在，用户一旦悬停就必然触发秒级子树采集。

---

## 一、Inspect 的实时定位交互模型（为什么它"跟手"）

Inspect 的 **Watch Cursor / Hover** 模式本质是**事件驱动 + 单元素 O(1)**：

1. 通过 `SetWinEventHook` 注册 `EVENT_OBJECT_LOCATIONCHANGE` / 鼠标命中事件，并带 `WINEVENT_OUTOFCONTEXT`（回调在注册线程的上下文外执行）、`WINEVENT_SKIPOWNPROCESS`（跳过自身进程，天然"不自伤"）、`WINEVENT_SKIPOWNTHREAD` 标志。
2. **鼠标移到新元素的瞬间**，系统推送 WinEvent → Inspect 回调拿到 `HWND`/`IAccessible`/`IUIAutomationElement`。
3. 回调里**只读这一个元素的属性**（Name/ClassName/ControlType/Rect/Pattern 列表），填入右侧属性网格（固定大小、就地更新，无整树重建）。
4. 高亮矩形（spy++ 风格）**立即跟随当前命中元素**移动，移动中持续跟手。
5. **它从不主动枚举子树**。要展开子树必须用户主动点 Tree 或 Refresh——"查看"和"采集"是彻底分离的两条路径。

关键点：**从"鼠标动"到"信息更新"的延迟 ≈ 一次属性读取（亚 100ms），且只依赖元素变更事件，与轮询周期、停顿判定、子树大小全部无关。**

---

## 二、WT 悬停补采的当前真实链路（逐行映射）

开启悬停跟踪（`cmd_toggle_hover_supplement` L6252）后，实际发生：

```
root.after(350ms) ──► _hover_tick (L6345)
   └─ _hover_probe_once (L6374)
       1. GetCursorPos 取鼠标位置
       2. 若鼠标落在采集器自身窗口矩形内 → 重置，跳过        (L6378-6386)
       3. 与上一次位置比较：移动 > 6px → stable_count 清零    (L6389-6391)
       4. stable_count += 1；仅当 == HOVER_STABLE_TICKS(2) 才继续  (L6392-6394)
          → 即：必须连续 2 个 tick（约 0.7s）几乎不动才触发
       5. _probe_hover_hit_key：from_point + 取 identity（10-50ms，主线程）  (L6467)
       6. 命中键 == 上次 → 跳过（去重）
       7. 若已在 flatControls → 仅聚焦树节点，不采集          (L6416-6435)
       8. 否则 enqueue collect_subtree_at_point 进 worker：   (L6450)
            max_depth=HOVER_SUPPLEMENT_MAX_DEPTH(12)
            scan_timeout_seconds=HOVER_SUPPLEMENT_TIMEOUT_SECONDS(12)
            excluded_process_ids=[os.getpid()]
       9. worker 后台跑 RawViewWalker BFS 整子树（秒级，最长 12s 熔断）(L1769-)
      10. 结果经 root.after(0) 回主线程 → _on_subtree_collected → _finish_supplement
          _finish_supplement 内 merge + _rebuild_control_groups + _refresh_tree 整树刷新 (L6962-7022)
```

对应常量（L1695-1700）：`HOVER_TICK_MS=350`、`HOVER_STABLE_TICKS=2`、`HOVER_SUPPLEMENT_MAX_DEPTH=12`、`HOVER_SUPPLEMENT_TIMEOUT_SECONDS=12`。

---

## 三、流畅度差距根因（按影响排序）

### 根因 0（Bug）："只看不采"未真正拦截采集
- `L6448` 仅日志；`L6450` 的 `self._worker_queue.put(...)` 无条件执行。
- 后果：用户无法"只悬停看信息不写库"，悬停必触发秒级子树采集，体感直接卡。

### 根因 1（最大）：轮询模型 → 350ms 地板延迟
- 一切以 `root.after(350ms)` 为节拍。鼠标移动后，最快也要等下一个 tick 才被感知，单纯"看到更新"的下界就是 350ms。
- Inspect 是事件驱动，无轮询周期，延迟与"轮询节拍"无关。

### 根因 2：停顿门控 6px / 2 tick ≈ 0.7s 才触发
- 必须连续 2 个 tick 位移 < 6px 才认为"停住"，才去探测。**Inspect 在你移动过程中就持续显示当前元素；WT 要求你"停下来等它确认"**。
- 这对"看信息"是反人性的：用户希望鼠标指到即出信息，而不是指到后凝固 0.7 秒。

### 根因 3（架构级）：悬停 = 全子树补采，把"查看"和"采集"耦合
- 悬停一个新控件，WT 跑的是 `collect_subtree_at_point`：**从命中元素往下 BFS 12 层、最长 12s**（WPF 深嵌套一个大容器可能数千节点）。
- 即便采集在 worker 线程（不冻 UI），**结果回流那一刻** `_finish_supplement` 要做 merge + 重建分组 + 整树 `_refresh_tree`（主线程），且大子树会让这次刷新可见卡顿；同时 `_worker_busy` 期间新悬停被跳过（L6439），导致连续悬停多个新控件时信息"迟到/丢失"。
- Inspect 悬停**只读单元素属性**，从不枚举子树——所以"看信息"永远是亚 100ms；"采子树"是用户另一次主动操作。
- **这是与 Inspect 流畅度差距的本质：WT 把"看"等同于"采"，而 Inspect 两者解耦。**

### 根因 4：高亮不跟手，只在"稳定命中"后更新
- `_PersistentHighlight.show` 在 `L6411-6415` 调用，但位于 `stable_count==2` 判定通过之后（L6393-6397）。也就是说，**鼠标移动过程中旧红框僵在原地，要等新稳定点出现才跳过去**。
- Inspect 的高亮在每次元素变更即时跟随，移动中持续跟手。

### 根因 5：`from_point` 每 tick 跨 COM，且自身窗口 topmost
- `_probe_hover_hit_key` 每个 tick 都 `Desktop(backend="uia").from_point`（L6477），跨 COM 边界 10-50ms，350ms 一次累加。
- 悬停模式 `self.root.attributes("-topmost", True)`（L6281）使采集器常驻最前，若盖住目标软件会遮挡查看/交互（Inspect 窗口是普通窗口，可挪开）。

### 根因 6：整树刷新而非就地增量
- `_finish_supplement` 对新增做了增量同步尝试（`_sync_hierarchy_after_supplement` L5719），但分组 `_rebuild_control_groups` 与回退全量 `_refresh_tree` 仍是主线程大操作。对比 Inspect 固定属性网格就地更新，WT 的"看到补采结果"伴随整树重建。

---

## 四、优化方案（全部落在 Phase 0：只动采集器，只新增不修改既有共享点）

> 纪律：复用现有 `_PersistentHighlight` / `_worker_loop` / pynput 热键 / `collect_subtree_at_point`，**新增**轻量查看通道；不改动 `_show_locator_highlight`、`_finish_supplement`、检验定位等共享逻辑。

### O1（最高收益）解耦"实时查看"与"补采写库"
- 新增"Watch 视图"：悬停/移动时只做**单元素** `from_point` + 取属性（Name/ClassName/ControlType/Rect/支持的 Pattern 列表），填入一个轻量信息面板（Text 或固定网格），**绝不跑子树 BFS**。
- 子树补采仅在以下之一发生时才触发：
  - 用户按 **F7**（已有热键 `_hotkey_capture_current`）手动采集；
  - 或显式开启"自动补采"开关（默认关）。
- 这就把 Inspect 的"Watch=看 / 手动=采"原样搬过来。**预期体感：指哪看哪，亚 100ms，永不卡。**

### O2（次高收益）事件驱动替代轮询
- 用 `SetWinEventHook(EVENT_OBJECT_LOCATIONCHANGE, …)` + `WINEVENT_OUTOFCONTEXT|WINEVENT_SKIPOWNPROCESS|WINEVENT_SKIPOWNTHREAD`，在**独立线程**注册并跑消息泵（避免阻塞 Tk 主线程、规避 COM 线程模型冲突——worker 线程已证明需独立 `CoInitializeEx`）。
- 回调仅做：取命中元素 → 经 `root.after(0, …)` 把"单元素属性 + 矩形"抛回主线程更新 Watch 视图与高亮。
- 彻底消除 350ms 轮询地板延迟；元素一变信息即更新，与 Inspect 同构。

### O3 高亮跟手
- 把 `_PersistentHighlight.show(rect)` 从"稳定 tick 之后"移到"每次元素变更即调用"（无论是否稳定、是否采集）。移动中红框持续跟随当前元素。
- 6px/2tick 门控**只保留给"触发补采"**，不再约束"查看+高亮"。

### O4 修复根因 0：让"只看不采"真正生效
- 在 `L6450` 入队前加 `if self._hover_look_only: return`（仅更新 Watch 视图/高亮，不入队）。一行修复，立竿见影。

### O5 降深度 + 节流刷新 + 渐进显示
- 悬停自动补采默认 `HOVER_SUPPLEMENT_MAX_DEPTH` 从 12 降到更合理值（如 4-6）；深度是给"定点/画框"用的，悬停自动采 12 层过犹不及。
- `_finish_supplement` 的整树刷新改为：合并后只增量插入新节点（已有 `_sync_hierarchy_after_supplement` 路径，强化其覆盖率，减少回退 `_refresh_tree`）。
- worker 结果回流时先更新 Watch 面板再异步刷新树，避免主线程一次性大卡。

### O6 交互细节
- 悬停模式下采集器主窗**不强制 topmost**（或提供可关开关），避免遮挡目标；红框 overlay 保持穿透。
- `from_point` 在事件驱动（O2）下本就只在元素变更时调用一次，无需每 tick 轮询；若暂未做 O2，至少把 `HOVER_TICK_MS` 降到 100-150ms 缓解根因 1。

---

## 五、实施顺序与风险边界

| 步骤 | 内容 | 收益 | 风险 |
|---|---|---|---|
| 1 | O4：修复 `_hover_look_only` 拦截 | 中（立即止血） | 极低，单行 |
| 2 | O1+O3：新增 Watch 视图 + 高亮跟手 | 高（指哪看哪） | 低，纯新增 |
| 3 | O5：降悬停深度 + 增量刷新 | 中 | 低 |
| 4 | O2：事件驱动替代轮询 | 高（跟手级提升） | 中（COM/消息泵线程，需参考 worker 线程的 `CoInitializeEx` 经验，且要 `WINEVENT_SKIPOWNPROCESS` 防自伤） |

- 全部在 `build_control_map_library.py` 内，不触碰编辑器（Phase 0 边界），不破坏既有共享点 `_show_locator_highlight` / `_finish_supplement` / 检验定位。
- 外部契约不变：库 JSON 格式、纯函数 `build_locator_recommendation` 不受影响，既有测试（`test_subtree_supplement.py` 等）不受影响。

## 六、验收标准

1. 开启悬停跟踪后，鼠标移动到任意控件，**红框即时跟手**、右侧 Watch 面板**亚 100ms 显示单元素属性**，且**不触发**子树采集（除非按 F7 或开自动补采）。
2. "只看不采"模式下，连续悬停多个控件**零**子树采集、零主线程卡顿。
3. 按 F7 才补采写库，补采结果经 worker 后台回流、增量刷新，主线程无可见冻结。
4. 连续快速悬停多个新控件时，信息不迟到、不丢失（worker 队列不跳过必要查看；采集按需串行）。
