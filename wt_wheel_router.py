# encoding: utf-8

"""WT 自动化统一鼠标滚轮路由器（跨窗口共享）。

设计依据（2026-09 社区交叉验证，详见 docs/界面渲染优化_社区交叉验证_20260909.md）：

1. Tk 8.6 语义（Tcler's Wiki「Mousewheel」页）：``<MouseWheel>`` 事件送给鼠标
   指针下的窗口（8.6.0 起），但 ttk 控件（含 ttk::scrollbar）自身不响应滚轮
   ——8.7 的 TIP 563 才补上。因此 8.6.x 必须自己路由，本模块即该路由。

2. 路由姿势与社区收敛实现同构（CustomTkinter 的 ``_mouse_wheel_all`` /
   ``_check_if_valid_scroll``）：``bind_all`` 一次 + 沿 ``master`` 向上找已注册
   的可滚动 canvas；本实现从 ``winfo_containing(x_root, y_root)`` 取命中点
   起步（比从 ``event.widget`` 起步更可靠：grab、透明覆盖层、边缘坐标下
   event.widget 会骗人）。命中失败或无桌面交互权限的环境（服务会话）里
   ``winfo_containing`` 恒为 None，此时降级沿 ``event.widget`` 向上找
   （即 CustomTkinter 的起步方式），保证滚轮不因此失效。

3. 废弃 Enter/Leave 动态 bind/unbind（历史写法）：``unbind_all`` 会把其他
   区域的常驻绑定一并拆掉。ttkbootstrap 2.0 的源码注释独立证实了这个坑
   （"bare <Leave> on the frame also fires when the pointer crosses onto
   a child … makes the bar flicker on and off"），并已同样废弃该模式。

4. 高精度触控板（本模块相对早期版本的关键修复）：Windows 上普通滚轮的
   event.delta 是 ±120 的倍数，但高精度触控板自 Tk 8.6.9 起会发出
   |delta| < 120 的精细值（Wiki 原文："high resolution mousepads may emit
   smaller values which may accumulate"）。直接 ``int(delta / 120)`` 会把
   1..119 全部截断为 0 —— 触控板上滚轮完全失效（零响应，不是慢）。
   本模块用余数累积器（同 ttkbootstrap ``wheel.PixelAccumulator`` 思路）：
   精细值逐次累加，凑满 ±120 才滚动一格；整倍数事件直除不累积，
   保持普通滚轮的即时响应。

用法::

    import wt_wheel_router
    wt_wheel_router.register(canvas)          # 注册可滚动 canvas
    wt_wheel_router.bind_root(root)           # root 上一次性绑定
"""

import tkinter as tk

# 普通滚轮一格的 delta 基准（Windows 标准值；Wiki「Mousewheel」页）
_WHEEL_UNIT = 120


class WheelRouter:
    """单进程单例滚轮路由器。

    所有窗口（主控台、链路编辑器等）注册的可滚动 canvas 共用一张路由表；
    绑定挂在进程根窗口上，一次安装全局生效。
    """

    def __init__(self):
        self._canvases = []
        self._bound = False
        # 余数累积器：{canvas: 累积 delta}。每个 canvas 独立累积，
        # 切换滚动区域时互不干扰
        self._remainders = {}

    # ── 公共接口 ─────────────────────────────────────────────
    def register(self, canvas):
        """注册一个可滚动 canvas；滚轮事件命中其子树时滚动它。"""
        if canvas not in self._canvases:
            self._canvases.append(canvas)

    def bind_root(self, root):
        """在根窗口上安装全局滚轮绑定（幂等，重复调用只装一次）。"""
        if self._bound:
            return
        root.bind_all("<MouseWheel>", self._handle, add="+")
        root.bind_all("<Button-4>", self._handle, add="+")
        root.bind_all("<Button-5>", self._handle, add="+")
        self._bound = True

    # ── 内部实现 ─────────────────────────────────────────────
    def _scroll_amount(self, canvas, raw_delta):
        """把 event.delta 换算成滚动格数。

        整倍数（普通滚轮）：直接整除，无累积、即时响应；
        精细值 0<|delta|<120（高精度触控板）：逐次累积进该 canvas 的
        余数池，凑满 ±120 消耗掉并滚动 1 格 —— 避免截断为 0 的零响应。
        """
        amount = 0
        remainder = self._remainders.get(canvas, 0)
        if raw_delta % _WHEEL_UNIT == 0:
            amount = -1 * (raw_delta // _WHEEL_UNIT)
        else:
            accumulated = remainder + raw_delta
            amount = -1 * int(accumulated / _WHEEL_UNIT)  # 截断取整（向零）
            if amount:
                # 消耗掉已折算的部分，保留未满一格的余量继续累积
                accumulated -= -amount * _WHEEL_UNIT
            self._remainders[canvas] = accumulated
        return amount

    def _handle(self, event):
        raw_delta = 0
        if getattr(event, "delta", 0):
            raw_delta = int(event.delta)
        elif getattr(event, "num", None) == 4:
            raw_delta = -_WHEEL_UNIT   # X11 滚上 → Tk Windows 语义向下为负
        elif getattr(event, "num", None) == 5:
            raw_delta = _WHEEL_UNIT
        if not raw_delta:
            return None
        # 首选按指针命中点定位（winfo_containing）：比 event.widget 可靠
        # （grab、透明覆盖层、边缘坐标下 event.widget 会骗人）。
        # 某些环境（服务/无桌面交互会话）下 winfo_containing 恒返回 None，
        # 此时降级沿 event.widget 向上找（CustomTkinter 的起步方式）。
        candidates = []
        try:
            under = event.widget.winfo_containing(event.x_root, event.y_root)
        except Exception:
            under = None
        if under is not None:
            candidates.append(under)
        widget = getattr(event, "widget", None)
        if widget is not None and (not candidates or candidates[0] is not widget):
            candidates.append(widget)
        for start in candidates:
            node = start
            while node is not None:
                for canvas in self._canvases:
                    if node is canvas:
                        amount = self._scroll_amount(canvas, raw_delta)
                        if amount:
                            canvas.yview_scroll(amount, "units")
                        return "break"
                try:
                    node = node.master
                except Exception:
                    node = None
        return None


# 进程级共享单例（多窗口共用一张路由表与一套余数池）
ROUTER = WheelRouter()


def register(canvas):
    ROUTER.register(canvas)


def bind_root(root):
    ROUTER.bind_root(root)
