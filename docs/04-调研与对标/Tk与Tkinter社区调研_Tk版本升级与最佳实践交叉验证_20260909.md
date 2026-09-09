# Tk 与 Tkinter 社区调研：Tk 版本升级与最佳实践交叉验证

> 调研日期：2026-09-09
> 目的：对"升级 Python/Tk 版本 + 四个 UI 手法"这批修改做**外部交叉验证**，
> 确认其与社区收敛实践是否一致，并找出此前未覆盖的风险。
> 结论用途：判断这批修改的可信度，不直接构成代码改动。

---

## 0. 结论速览

| 手法 | 外部证据 | 判定 |
|---|---|---|
| 滚轮 `bind_all` + `winfo_containing` 上溯 | Tcler's Wiki 8.6 语义变更 + Wiki 配方 + CustomTkinter 一手源码 | 与社区收敛实践一致，且严于 CustomTkinter 版本 |
| 废弃 Enter/Leave 动态 bind/unbind | ttkbootstrap 2.0 注释明确该缺陷并已废弃 `unbind_all` | 有第三方回归实证 |
| `after_idle` 合帧 | Tcl `after` 手册官方语义 | 手法正确；**措辞需从"每帧一次"改为"队列排空后一次"** |
| 滚动条先 pack | `pack.n` 算法原文（含 cavity 归零即 unmap） | 有官方原文背书，解释力强于原推导 |
| 增量 diff 渲染 | `ttk_treeview.n` 的 `detach` 语义旁证 | 同族但无外部案例；可信度仍靠本地实测 + 回归测试 |
| 升级 Tk 8.6.12+ | CPython `PCbuild/tcltk.props` | 硬证据成立 |

**新增风险**：高精度触控板下 `int(delta/120)` 归零会导致滚轮完全失效（详见 §5）。

---

## 1. 滚轮统一路由

### 1.1 官方语义：8.6.x 必须自己路由

Tcler's Wiki「MouseWheel」页记录的版本演进：

- **Tk 8.6.0**：`<MouseWheel>` 改为投递给**鼠标指针下的窗口**（此前 Windows 上是焦点窗口），取代 TIP 171 的替代方案。
- **Tk 8.6.9**（patchlevel 待确认）：Windows 精细粒度滚轮支持（增量小于 120）。
- **Tk 8.6.10**：硬件横向滚动在 Windows 触发 `Shift-MouseWheel`、X11 触发 `Shift-4/5`。
- **Tk 8.7.0**：`ttk::scrollbar` 自身才对滚轮敏感（TIP 563）。

→ 在 8.6.x 上**没有内建的滚动条滚轮响应**，必须自行路由。自研路由器不是绕开问题，而是踩在官方设计意图上。

### 1.2 社区配方与之同构

Wiki 中 MG 的配方：

```tcl
bind all <MouseWheel> [list mouseWheel %W %D]
proc mouseWheel {widget delta} {
    ...
    set over [winfo containing -displayof $widget {*}[winfo pointerxy $widget]]
    if {$over == "" || [catch {$over {*}$cmd}]} { catch {$widget {*}$cmd} }
}
```

`bind all` → `winfo containing` → 回退焦点窗口，与本项目的 `_WheelRouter` 结构同构。

### 1.3 第三方项目实证：CustomTkinter

`customtkinter/windows/widgets/ctk_scrollable_frame.py`：

```python
self.bind_all("<MouseWheel>", self._mouse_wheel_all, add=True)
...
def _check_if_valid_scroll(self, widget):
    if widget == self._parent_canvas:  return True
    elif isinstance(widget, (CTkScrollbar, CTkSlider, CTkTextbox)): return False
    elif widget.master is not None:    return self._check_if_valid_scroll(widget.master)
```

同样是 `bind_all` + 沿 `master` 向上找可滚动目标。

**本项目实现更严格**：从 `winfo_containing(x_root, y_root)` 取坐标命中点起步，
而 CustomTkinter 从 `event.widget` 起步。在存在 grab、透明覆盖层、边缘坐标时后者会失真。

### 1.4 Enter/Leave 动态绑定是真实存在的坑

ttkbootstrap 2.0（`src/ttkbootstrap/widgets/scrolled.py`）源码注释：

```
A bare <Leave> on the frame also fires when the pointer crosses onto a child --
the text or the scrollbar itself -- so hiding on it makes the bar flicker on and off
```

其修正手段正是 `winfo_containing` + 沿 `master` 上溯判断指针是否真的离开。
同时该版本**废弃了 `unbind_all`**，改为 `add="+"` 记录 funcid 后精确 `unbind`。

本项目代码注释中"unbind_all 会拆掉其他区域的常驻绑定"的判断，被主流主题库的历史回归独立证实。

### 1.5 更进一步的现代做法（技术债，非当前缺陷）

ttkbootstrap 2.0 已把 `bind_all` 升级为 **per-instance bind tag**：

```python
self._wheel_tag = f"ScrolledFrame_{id(self)}"
self.bind_class(self._wheel_tag, seq, self._on_mousewheel, "+")
# 插入每个 widget 的 bindtags 第 1 位
tags.insert(1, self._wheel_tag); widget.bindtags(tuple(tags))
```

注释给出的理由：`no O(subtree) rebinding, no clobbering the app's own wheel handlers`。

本项目的 `add="+"` 已解决 clobber 问题，但 `bind_all` 仍是全局的（靠 canvas 白名单兜底）。
**当前不必改**；若将来出现"弹窗内滚轮被主窗口吃掉"，按 bindtags 方案重构是社区现成答案。

---

## 2. `after_idle` 合帧：措辞需修正

Tcl `after` 手册（`doc/after.n`）原文：

> The script will be run **exactly once, the next time the event loop is entered
> and there are no events to process**.

即"**事件队列排空后运行一次**"，**不是"每帧一次"**。
Configure 风暴期间该回调根本不会执行，风暴结束才跑一次——效果上等于合帧，
但机制描述必须准确，否则后人会误以为它周期性触发。

手册还给出用 idle 做长任务分片的官方示例 `after idle [list after 0 doOneStep]`，
同样印证 idle 的语义是"队列排空后"。

**如实说明**：Tk 官方 `library/ttk/treeview.tcl` 中**没有** `after idle` 合帧用法
（仅有 `after 0` 用于 heading 命令）。因此"Tk 官方自己在 treeview 里用 idle 合帧"的说法不成立，
本条的背书只有 `after` 手册语义本身。

---

## 3. 滚动条先 pack：官方依据强于原推导

`doc/pack.n`「THE PACKER ALGORITHM」原文：

> The packer arranges the content windows for a container by **scanning the packing list in order**…
> Once a given content has been packed, the area of its parcel is subtracted from the cavity…
> If the cavity should become too small to meet the needs of a content then the content will be
> given whatever space is left in the cavity.
> **If the cavity shrinks to zero size, then all remaining content on the packing list will be
> unmapped from the screen until the container window becomes large enough to hold them again.**

最后一句是硬证据：排在后面的控件在容器收缩时会被压缩，**最终被 unmap（直接从屏幕消失）**。
这比"先到先分配"的朴素推导强得多，直接解释了"控件不见了"的症状。

**可采纳的增强**：`pack.n` 提供 `-before` / `-after` 显式指定 packing order。
依赖"调用顺序"隐含顺序较脆（后人重排代码即失效），显式声明更稳。

---

## 4. 增量 diff 渲染：官方同族手段，但无外部案例

`doc/ttk_treeview.n` 对 `detach` 的原文：

> Unlinks all of the specified items… The items and all of their descendants are **still present**
> and may be reinserted at another point in the tree with the `move` operation,
> but will not be displayed until that is done.

即官方原生提供"保留 item、只解除显示"的能力，与本项目的"避免全量重建"同族。

差异：`detach` 适合**行集合稳定、频繁显隐**；本项目的 diff 适合**行集合频繁增删**。
若将来任务树出现大量行反复显隐，`detach`/`move` 可能比 `delete`/`insert` 更省。

**诚实结论**：社区中关于 treeview 增量刷新的检索结果被 SEO 站点污染，
未取得可信的一手项目案例。本条仍是"通用工程实践 + 官方 detach 语义旁证 + 本地实测"，
不能升级为"社区就是这么做的"。

---

## 5. ⚠️ 新发现的风险：高精度触控板下滚轮完全失效

两个独立来源指向同一风险。

**Tcler's Wiki 原文**：

> Windows: A mousewheel click is translated by a delta of 120.
> **Take care, that high resolution mousepads may emit smaller values which may accumulate.**

同一页的版本记录显示 Tk 8.6.9 起 Windows 支持精细粒度滚轮——**正是本项目的当前版本**。

**ttkbootstrap 2.0** 为此专门实现 `wheel.PixelAccumulator`，注释说明：

> Tk reports these roughly **sixty times a second in pixels**… the pixels are accumulated
> and spent as whole units.

**当前实现**：

```python
delta = -1 * int(event.delta / 120)
```

当触控板发出 `delta = 1..119` 的精细值时，`int(1/120) == 0` → **完全不滚动**
（不是慢，是零响应）。而 8.6.9 恰恰引入该行为——**升级到 8.6.12+ 不会修复它，反而会让更多设备触发**。

**建议**：对 `0 < |delta| < 120` 的情况做余数累积，或改按像素滚动（`yview_scroll(-delta, "pixels")`）。
应作为独立 fix 处理，不宜与版本升级混在一起。

---

## 6. 主题库与迁移：不作为替代方案

- **ttkbootstrap 2.0** 的滚动容器自身就在适配 Tk 版本差异（`has_touchpad_scroll()`、`precise_deltas`、
  触控板模拟），说明**主题库本身依赖底层 Tk 行为，换主题不解决输入/渲染问题**。
- **sv_ttk / Azure-ttk-theme**：本次未取得源码证据，不做结论。
- **PySide6 迁移经验**：检索结果均为选型营销文，无一手迁移案例。**没有可信证据，
  不应基于网文做该决策。**

---

## 7. 证据来源清单

| 来源 | 类型 | 用途 |
|---|---|---|
| https://wiki.tcl-lang.org/page/Mousewheel | 社区 Wiki | 8.6 语义变更、精细滚轮警告、社区配方 |
| `tcltk/tk` `doc/pack.n` | 官方手册源 | packer 算法原文 |
| `tcltk/tk` `doc/ttk_treeview.n` | 官方手册源 | `detach` 语义 |
| `tcltk/tcl` `doc/after.n` | 官方手册源 | `after idle` 语义 |
| `python/cpython` `PCbuild/tcltk.props`（3.11 / 3.12） | 官方构建配置 | 3.11→Tk 8.6.12.1、3.12→Tk 8.6.15.2 |
| `TomSchimansky/CustomTkinter` `ctk_scrollable_frame.py` | 第三方源码 | `bind_all` + 向上查找实证 |
| `israel-dryer/ttkbootstrap` `widgets/scrolled.py` | 第三方源码 | Enter/Leave 缺陷、bindtags 升级、像素累积 |

> 注：`python/cpython` 的 `PCbuild/tcltk.props` 为本次**二次核实**，与首次调研结论一致。
