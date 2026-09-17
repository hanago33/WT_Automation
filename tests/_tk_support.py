# encoding: utf-8
"""测试用 Tk 根窗口：**进程内只创建一次并全局复用**。

为什么需要这个模块
------------------
同一进程内反复创建/销毁 Tk 根窗口会让后续 `Tk()` 失败：

- 前一个测试类 `Tk()` 后 `destroy()`，后续类再 `Tk()` 抛
  `TclError: invalid command name "tcl_findLibrary"`
  （另一种表现是 `Can't find a usable init.tcl`）
- 由于 pytest 按文件名顺序收集，失败会落在**后一个**用到 Tk 的测试类上，
  表现为「整组被 skip」—— 很容易误判成那组测试自己的问题

因此本模块提供唯一入口 `shared_tk_root()`：全局只建一次、`withdraw()` 隐藏，
**任何测试类都不要 destroy 它**（进程退出时由解释器回收）。

用法::

    from _tk_support import shared_tk_root

    class MyTkTests(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.tk, cls.root = shared_tk_root()

不需要 tearDownClass。
"""

import unittest

_root = None
_tk = None


def shared_tk_root():
    """返回 ``(tkinter, 共享根窗口)``；环境不可用时抛 ``unittest.SkipTest``。

    skip 理由带上底层异常，避免「无显示环境」这类笼统说法掩盖真实原因。
    """
    global _root, _tk
    if _root is not None:
        return _tk, _root

    try:
        import tkinter as tk
    except Exception as exc:  # pragma: no cover - 环境相关
        raise unittest.SkipTest("无 tkinter 可用：%r" % (exc,))
    try:
        root = tk.Tk()
    except Exception as exc:  # pragma: no cover - 环境相关
        raise unittest.SkipTest("无法创建 Tk 根窗口：%r" % (exc,))
    try:
        root.withdraw()
    except Exception:
        pass

    _tk = tk
    _root = root
    return _tk, _root
