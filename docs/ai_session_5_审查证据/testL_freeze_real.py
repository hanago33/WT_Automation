# encoding: utf-8
"""Test L: 用真实 tk.Text 复现日志面板冻结（此前仅为模型仿真）。

直接调用 wt_task_queue_window.TaskQueueWindow._append_lines_incremental 的
**真实方法体**（不复制逻辑），喂入服务端 tail=300 的等长位移快照。
"""
import os
import sys

ROOT = r"C:\Users\14830\AppData\Local\Temp\dsh-pNekd0\ai5_testrun"
sys.path.insert(0, ROOT)

import tkinter as tk  # noqa: E402

import wt_task_queue_window as Q  # noqa: E402

root = tk.Tk()
root.withdraw()
widget = tk.Text(root, wrap=tk.WORD)
widget.config(state=tk.DISABLED)


class FakeWindow(object):
    """借壳：取真实的 _append_lines_incremental 与其依赖的 _classify_line。"""

    _LOG_MAX_LINES = Q.TaskQueueWindow._LOG_MAX_LINES
    _append_lines_incremental = Q.TaskQueueWindow._append_lines_incremental
    # _classify_line 在真实类是 @staticmethod；经类访问已得到普通函数，
    # 直接赋到类属性会变成实例方法（多收 self），故需包回 staticmethod。
    _classify_line = staticmethod(Q.TaskQueueWindow._classify_line)


holder = FakeWindow()
state_attr = "_probe_rendered"
setattr(holder, state_attr, None)

SERVER_TAIL = 300
print("服务端 DEFAULT_TAIL = %d" % SERVER_TAIL)
print("控件 _LOG_MAX_LINES = %d" % holder._LOG_MAX_LINES)
print()


def snapshot(total):
    return ["第 %d 行" % i for i in range(total - SERVER_TAIL, total)]


def widget_last_line():
    content = widget.get("1.0", "end-1c")
    lines = [l for l in content.split("\n") if l != ""]
    return lines[-1] if lines else None


def widget_line_count():
    row = int(widget.index("end-1c").split(".")[0] or 0)
    return max(0, row - 1)


total = SERVER_TAIL
holder._append_lines_incremental(widget, snapshot(total), state_attr)
print("轮 1：服务端 %d 行 -> 控件 %d 行，末行=%r"
      % (total, widget_line_count(), widget_last_line()))

frozen_at = None
for step in range(1, 6):
    total += 1
    holder._append_lines_incremental(widget, snapshot(total), state_attr)
    last = widget_last_line()
    expected = "第 %d 行" % (total - 1)
    mark = ""
    if last != expected:
        mark = "  <-- 未跟上（期望 %s）" % expected
        if frozen_at is None:
            frozen_at = step
    print("轮 %d：服务端 %d 行 -> 控件 %d 行，末行=%r%s"
          % (step + 1, total, widget_line_count(), last, mark))

print()
print("服务端真实最新行 = %r" % ("第 %d 行" % (total - 1),))
print("控件实际显示末行 = %r" % (widget_last_line(),))
print()
if frozen_at is not None:
    print("*** 真实 Tk 控件上复现成功：从第 %d 轮起面板不再更新 ***" % (frozen_at + 1))
    print("*** 控件上限 %d > 服务端 tail %d，trim 永不触发 ***"
          % (holder._LOG_MAX_LINES, SERVER_TAIL))
else:
    print("未复现")
root.destroy()
print("RESULT=L_OK")
