# -*- coding: utf-8 -*-

"""wt_wheel_router 单元测试（fake 控件，无真实 GUI）。

核心场景（社区调研 2026-09 确认的输入退化）：
高精度触控板在 Windows/Tk>=8.6.9 上会发出 |delta|<120 的精细值，
旧的 int(delta/120) 写法会把它截断为 0 —— 触控板滚轮完全失效。
"""

import wt_wheel_router
from wt_wheel_router import WheelRouter


class FakeEvent:
    def __init__(self, delta=0, num=None, widget=None, x_root=0, y_root=0):
        self.delta = delta
        self.num = num
        self.widget = widget
        self.x_root = x_root
        self.y_root = y_root


# 真实 Tk 的 winfo_containing 是全局语义：任意 widget 上调用都查整个屏幕。
# fake 用类级共享指针表模拟（各测试用例通过 pointer_at() 设置当前命中控件）。
_POINTER_HOLDER = {"hit": None}


def pointer_at(widget):
    """设定当前屏幕指针命中的控件（模拟鼠标位置）。"""
    _POINTER_HOLDER["hit"] = widget


class FakeWidget:
    def __init__(self, master=None):
        self.master = master
        self.scrolled = []          # [("units", n), ...]
        self.children = []

    def winfo_containing(self, x, y):
        return _POINTER_HOLDER["hit"]

    def yview_scroll(self, amount, unit):
        self.scrolled.append((unit, amount))


class FakeCanvas(FakeWidget):
    pass


class FakeRoot(FakeWidget):
    def __init__(self):
        super().__init__(None)
        self.binds = {}

    def bind_all(self, sequence, func, add=None):
        self.binds.setdefault(sequence, []).append((func, add))


def make_router_with_canvas():
    router = WheelRouter()
    root = FakeRoot()
    canvas = FakeCanvas(root)
    router.register(canvas)
    router.bind_root(root)
    # 模拟真实 Tk 的 winfo_containing：指针悬在 canvas 上（最深命中控件即
    # canvas 本身；真实场景中常是 canvas 的子控件，由 test_pointer_routes_
    # to_enclosing_canvas 覆盖上溯路径）
    pointer_at(canvas)
    return router, root, canvas


def test_bind_root_is_idempotent():
    router = WheelRouter()
    root = FakeRoot()
    router.bind_root(root)
    router.bind_root(root)
    assert len(root.binds["<MouseWheel>"]) == 1


def test_standard_wheel_scrolls_one_unit():
    router, root, canvas = make_router_with_canvas()
    result = router._handle(FakeEvent(delta=-120, widget=root))
    assert canvas.scrolled == [("units", 1)]      # 滚轮向上 → 内容上移
    assert result == "break"


def test_standard_wheel_fast_notch():
    router, root, canvas = make_router_with_canvas()
    router._handle(FakeEvent(delta=-360, widget=root))
    assert canvas.scrolled == [("units", 3)]


def test_precision_trackpad_accumulates_small_deltas():
    """核心回归：|delta|<120 的精细值不得截断为 0。"""
    router, root, canvas = make_router_with_canvas()
    # 触控板轻扫：一串 delta=30 的事件（不凑整）
    for _ in range(3):
        router._handle(FakeEvent(delta=-30, widget=root))
    # 3×30=90 < 120：还不满一格，0 次滚动但余量被记住
    assert canvas.scrolled == []
    # 第 4 次：120 满格 → 滚 1 格
    router._handle(FakeEvent(delta=-30, widget=root))
    assert canvas.scrolled == [("units", 1)]


def test_precision_trackpad_continuous_swipe():
    """连续轻扫应当持续滚动，余量跨事件保留。"""
    router, root, canvas = make_router_with_canvas()
    for _ in range(10):
        router._handle(FakeEvent(delta=-40, widget=root))
    # 10×40=400 → 3 格（400//120=3），余 40 继续留池
    total = sum(n for _, n in canvas.scrolled)
    assert total == 3


def test_remainder_pool_is_per_canvas():
    router, root, canvas = make_router_with_canvas()
    other = FakeCanvas(root)
    router.register(other)
    # canvas 攒了 90 的余量
    for _ in range(3):
        router._handle(FakeEvent(delta=-30, widget=root))
    assert router._remainders.get(canvas) == -90
    # 换到 other 上滚：不继承 canvas 的余量
    pointer_at(other)
    router._handle(FakeEvent(delta=-120, widget=root))
    assert other.scrolled == [("units", 1)]
    assert router._remainders.get(canvas) == -90   # 余量未被误消耗


def test_x11_button4_5_mapped():
    router, root, canvas = make_router_with_canvas()
    router._handle(FakeEvent(num=4, widget=root))   # X11 滚上
    assert canvas.scrolled == [("units", 1)]
    router._handle(FakeEvent(num=5, widget=root))   # X11 滚下
    assert canvas.scrolled[-1] == ("units", -1)


def test_pointer_routes_to_enclosing_canvas():
    """命中 canvas 子树内的深层控件时，向上找到注册的 canvas。"""
    router, root, canvas = make_router_with_canvas()
    leaf = FakeWidget(FakeWidget(canvas))           # canvas → mid → leaf
    pointer_at(leaf)                                # 指针命中深层子控件
    router._handle(FakeEvent(delta=-120, widget=root))
    assert canvas.scrolled == [("units", 1)]


def test_pointer_outside_registered_canvases_is_noop():
    router, root, canvas = make_router_with_canvas()
    stranger = FakeWidget(root)                     # root 直下，不在 canvas 子树
    pointer_at(stranger)
    result = router._handle(FakeEvent(delta=-120, widget=root))
    assert canvas.scrolled == []
    assert result is None                            # 不吞事件，交给其他处理链


def test_zero_delta_is_ignored():
    router, root, canvas = make_router_with_canvas()
    result = router._handle(FakeEvent(delta=0, widget=root))
    assert canvas.scrolled == []
    assert result is None


def test_winfo_containing_failure_falls_back_to_event_widget():
    """无桌面交互环境（服务会话）下 winfo_containing 恒 None：
    必须降级沿 event.widget 向上找，滚轮不能因此失效。"""
    router, root, canvas = make_router_with_canvas()
    # 事件 widget 是 canvas 的深层子控件；指针定位不可用
    leaf = FakeWidget(FakeWidget(canvas))
    pointer_at(None)                              # winfo_containing -> None
    result = router._handle(FakeEvent(delta=-120, widget=leaf))
    assert canvas.scrolled == [("units", 1)]
    assert result == "break"


def test_pointer_hit_wins_over_event_widget():
    """指针命中点与事件 widget 不一致时，以指针命中为准。"""
    router, root, canvas = make_router_with_canvas()
    other = FakeCanvas(root)
    router.register(other)
    # 事件 widget 在 canvas 子树（旧焦点），但指针此刻在 other 上
    leaf = FakeWidget(FakeWidget(canvas))
    pointer_at(other)
    router._handle(FakeEvent(delta=-120, widget=leaf))
    assert canvas.scrolled == []
    assert other.scrolled == [("units", 1)]
