# encoding: utf-8
"""Test G: 验证 wt_task_queue_window._append_lines_incremental 的「等长位移」冻结。

不依赖 Tk：用模型精确复刻该函数对 rendered_count() 的使用语义
（Tk 语义：内容 "a\nb\n" 的 end-1c 在第 3 行行首 -> 真实行数 = row-1）。

场景：服务端 /api/logs?tail=300 恒返回最近 300 行。控件渲染满 300 行后，
日志继续增长 -> 服务端仍返回 300 行但内容整体位移一行。
"""
SERVER_TAIL = 300          # wt_server_monitor.DEFAULT_TAIL
LOG_MAX_LINES = 400        # wt_task_queue_window._LOG_MAX_LINES


class FakeText(object):
    """复刻 Tk Text 的行语义（只实现该函数用到的最小面）。"""

    def __init__(self):
        self.lines = []

    def delete_all(self):
        self.lines = []

    def append(self, line):
        self.lines.append(line)

    def rendered_count(self):
        # Tk: index("end-1c") 行号 = len(lines)+1（每行含尾随 \n），故减 1
        return max(0, len(self.lines))

    def trim(self, cap):
        excess = self.rendered_count() - cap
        if excess > 0:
            del self.lines[:excess]
            return excess
        return 0


def apply_incremental(widget, text_lines, state):
    """逐行复刻 _append_lines_incremental 的判定。"""
    need_full = state["rendered"] is None
    if not need_full and widget.rendered_count() > len(text_lines):
        need_full = True
    appended = 0
    if need_full:
        widget.delete_all()
        for line in text_lines:
            widget.append(line)
        appended = len(text_lines)
    else:
        current = widget.rendered_count()
        if len(text_lines) > current:
            for line in text_lines[current:]:
                widget.append(line)
                appended += 1
    widget.trim(LOG_MAX_LINES)
    state["rendered"] = len(text_lines)
    return appended


print("场景：服务端 tail=%d，控件上限 LOG_MAX_LINES=%d" % (SERVER_TAIL, LOG_MAX_LINES))
print()

widget = FakeText()
state = {"rendered": None}
total_server_lines = 0

# 第 1 轮：服务端有 300 行（初始全量）
total_server_lines = 300
server_view = ["第 %d 行" % i for i in range(total_server_lines - SERVER_TAIL, total_server_lines)]
n = apply_incremental(widget, server_view, state)
print("轮 1：服务端共 %d 行，tail 视图 %d 行 -> 追加 %d 行，控件 %d 行"
      % (total_server_lines, len(server_view), n, widget.rendered_count()))

# 第 2 轮：日志继续增长 5 行，服务端仍返回最近 300 行（内容整体位移）
for _ in range(5):
    total_server_lines += 1
    server_view = ["第 %d 行" % i for i in range(total_server_lines - SERVER_TAIL, total_server_lines)]
    n = apply_incremental(widget, server_view, state)
    print("轮 %d：服务端共 %d 行，tail 视图 %d 行 -> 追加 %d 行，控件 %d 行，最新行=%r"
          % (2 + _ , total_server_lines, len(server_view), n, widget.rendered_count(),
             widget.lines[-1] if widget.lines else None))

print()
print("服务端真实最新行 = %r" % server_view[-1])
print("控件实际显示最新行 = %r" % (widget.lines[-1] if widget.lines else None))
frozen = widget.lines and widget.lines[-1] != server_view[-1]
print()
if frozen:
    print("*** 控件已冻结：日志继续增长但面板内容不再更新 ***")
    print("*** 根因1：len(text_lines) > current 在等长位移时恒为假 ***")
    print("*** 根因2：LOG_MAX_LINES(%d) > SERVER_TAIL(%d)，trim 永不触发 ***" % (LOG_MAX_LINES, SERVER_TAIL))
else:
    print("未复现冻结")
print("RESULT=G_OK")
