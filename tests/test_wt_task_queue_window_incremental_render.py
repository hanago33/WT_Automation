# -*- coding: utf-8 -*-

"""增量渲染逻辑测试：任务树三路 diff 与日志增量追加（fake 控件，无真实 GUI）。

覆盖 wt_task_queue_window 2026-09 渲染优化：
  - _render_task_list 增量更新：未变行零操作 / 值变行 itemconfigure /
    新行 insert / 消失行 delete / 选择恢复仅在丢失时发生
  - _append_lines_incremental：稳态零绘制 / 追加型只 insert 新行 /
    行数回退时全量兜底 / 超上限头部 trim
"""

import wt_task_queue_window


class FakeTree:
    """最小 ttk.Treeview 替身：记录操作历史供断言。"""

    def __init__(self):
        self.items = {}          # iid -> values tuple
        self.selection_value = ()
        self.log = []           # ("delete"/"insert"/"item"/"selection", ...)

    def get_children(self):
        return tuple(self.items.keys())

    def delete(self, *iids):
        for iid in iids:
            self.items.pop(iid, None)
            self.log.append(("delete", iid))

    def insert(self, parent, index, iid=None, values=()):
        assert parent == "" and index == "end"
        self.items[iid] = tuple(values)
        self.log.append(("insert", iid))

    def item(self, iid, values=None):
        if values is None:
            # 与真实 Tk 一致：无关键字调用返回 dict，values 为字符串列表
            return {"values": [str(v) for v in self.items[iid]]}
        self.items[iid] = tuple(values)
        self.log.append(("item", iid))

    def exists(self, iid):
        return iid in self.items

    def set(self, iid, column=None, value=None):
        # 与真实 ttk.Treeview.set 一致：无参调用返回 {列名: 值} dict
        if column is None:
            return {"#%d" % (i + 1): str(v) for i, v in enumerate(self.items[iid])}
        raise AssertionError("set() with column not expected in tests")

    def selection(self):
        return self.selection_value

    def selection_set(self, iid):
        self.selection_value = (iid,)
        self.log.append(("selection", iid))

    def see(self, iid):
        self.log.append(("see", iid))


class FakeText:
    """最小 tk.Text 替身：行内容列表 + 操作历史。"""

    def __init__(self):
        self.lines = []
        self.state_value = "normal"
        self.log = []

    def config(self, **kw):
        if "state" in kw:
            self.state_value = kw["state"]

    def index(self, spec):
        # 对齐真实 Tk 语义：内容 N 行（每行以 \n 结尾）时 end-1c 返回 "(N+1).0"
        if spec == "end-1c":
            return "%d.0" % (len(self.lines) + 1)
        raise AssertionError("unexpected index spec: %r" % spec)

    def delete(self, start, end):
        s = 1 if start == "1.0" else int(start.split(".")[0])
        e = len(self.lines) + 1 if end == "end" else int(end.split(".")[0])
        del self.lines[s - 1:e - 1]
        self.log.append(("delete", start, end))

    def insert(self, where, text, tag=None):
        assert where == "end"
        self.lines.append((text.rstrip("\n"), tag))
        self.log.append(("insert", text, tag))

    def get(self, start, end):
        """模拟 tk.Text.get(start, end)。

        支持生产代码用到的两种读取：
          - "1.0" -> "end-1c"：全文内容（不含末尾换行）；
          - "N.0" -> "N.end"：第 N 行内容。
        """
        if end == "end-1c":
            return "\n".join(text for text, _tag in self.lines)
        s_row = int(start.split(".")[0])
        e_row = int(end.split(".")[0])
        if s_row == e_row and end.endswith(".end"):
            line_idx = s_row - 1  # Tk 行号从 1 计，转为 0-based
            if 0 <= line_idx < len(self.lines):
                return self.lines[line_idx][0]
            return ""
        raise AssertionError("FakeText.get() 不支持的范围: %r -> %r" % (start, end))

    def see(self, where):
        self.log.append(("see", where))


def make_window():
    window = object.__new__(wt_task_queue_window.TaskQueueWindow)
    window.task_tree = FakeTree()
    window.log_text = FakeText()

    class _Var:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

    window.status_filter_var = _Var("全部")
    window.keyword_var = _Var("")
    window._update_control_buttons = lambda: None
    return window


def _task(task_id, status="pending", step=""):
    return {
        "taskId": task_id,
        "user": "alice",
        "status": status,
        "priority": 0,
        "scheduledAt": "-",
        "attempts": 1,
        "maxAttempts": 3,
        "progressTotal": 5,
        "progressCurrent": 2,
        "progressPercent": 40.0,
        "currentStepName": step,
        "createdAt": "2026-09-09 10:00:00",
        "updatedAt": "2026-09-09 10:01:00",
    }


def test_task_render_no_change_is_zero_ops():
    window = make_window()
    window._render_task_list([_task("t1"), _task("t2")])
    baseline_ops = len(window.task_tree.log)
    # 第二轮完全相同的数据：不应有任何 delete/insert/item 操作
    window.task_tree.log.clear()
    window._render_task_list([_task("t1"), _task("t2")])
    assert window.task_tree.log == []


def test_task_render_changed_row_is_updated_in_place():
    window = make_window()
    window._render_task_list([_task("t1", status="pending")])
    window.task_tree.log.clear()
    window._render_task_list([_task("t1", status="running")])
    # 值变化：仅 item 原地更新，无 delete/insert
    assert ("item", "t1") in window.task_tree.log
    assert not any(op in ("delete", "insert") for op, *_ in window.task_tree.log)
    assert window.task_tree.items["t1"][2] == "运行中"


def test_task_render_add_and_remove_rows():
    window = make_window()
    window._render_task_list([_task("t1"), _task("t2")])
    window.task_tree.log.clear()
    # t2 消失、t3 新增、t1 保留
    window._render_task_list([_task("t1"), _task("t3")])
    assert ("delete", "t2") in window.task_tree.log
    assert ("insert", "t3") in window.task_tree.log
    assert "t3" in window.task_tree.items
    assert "t2" not in window.task_tree.items


def test_task_render_keeps_selection_without_reselect():
    window = make_window()
    window._render_task_list([_task("t1"), _task("t2")])
    window.task_tree.selection_value = ("t1",)
    window.task_tree.log.clear()
    window._render_task_list([_task("t1"), _task("t2")])
    # 选择未丢失：不应触发 selection_set（每轮重设会打断用户交互）
    assert ("selection", "t1") not in window.task_tree.log


def test_log_incremental_append_only_new_lines():
    window = make_window()
    window._render_logs(["a", "b", "c"])
    text = window.log_text
    assert [l for l, _ in text.lines] == ["a", "b", "c"]
    text.log.clear()
    # 追加型：只 insert 新行 d
    window._render_logs(["a", "b", "c", "d"])
    inserts = [op for op in text.log if op[0] == "insert"]
    assert len(inserts) == 1 and inserts[0][1].rstrip("\n") == "d"
    deletes = [op for op in text.log if op[0] == "delete"]
    assert not deletes


def test_log_steady_state_zero_ops():
    window = make_window()
    window._render_logs(["a", "b"])
    window.log_text.log.clear()
    # 完全相同的快照：零绘制
    window._render_logs(["a", "b"])
    assert window.log_text.log == [("config",)] or not [
        op for op in window.log_text.log if op[0] in ("insert", "delete")
    ]


def test_log_shrunk_snapshot_falls_back_to_full_redraw():
    window = make_window()
    window._render_logs(["a", "b", "c"])
    window.log_text.log.clear()
    # 服务器日志轮转/换任务：快照变短 → 全量重绘兜底
    window._render_logs(["x", "y"])
    lines = [l for l, _ in window.log_text.lines]
    assert lines == ["x", "y"]


def _ops(text):
    inserts = [op for op in text.log if op[0] == "insert"]
    deletes = [op for op in text.log if op[0] == "delete"]
    return inserts, deletes


_DETECT_SHIFT = wt_task_queue_window.TaskQueueWindow._detect_log_shift


class TestDetectLogShift:
    """_detect_log_shift 纯逻辑。"""

    def test_identical_returns_zero(self):
        assert _DETECT_SHIFT(["a", "b"], ["a", "b"]) == 0

    def test_rolling_window_shift(self):
        assert _DETECT_SHIFT(["a", "b", "c"], ["b", "c", "d"]) == 1
        assert _DETECT_SHIFT(["a", "b", "c"], ["c", "d", "e"]) == 2

    def test_repeated_lines_are_detected(self):
        # 末行内容重复时也必须识别出前移（只比末行会漏检）
        assert _DETECT_SHIFT(["x", "y", "same"], ["y", "same", "same"]) == 1

    def test_no_common_prefix_degrades_to_full_replace(self):
        # 完全不同的等长内容：shift == 行数，等价于整体替换
        assert _DETECT_SHIFT(["a", "b"], ["x", "y"]) == 2

    def test_length_mismatch_returns_none(self):
        assert _DETECT_SHIFT(["a"], ["a", "b"]) is None

    def test_empty(self):
        assert _DETECT_SHIFT([], []) == 0


def test_log_equal_length_shift_is_incremental():
    """tail 饱和后日志滚动推进：内容必须跟上，且保持增量绘制（不全量重绘）。"""
    window = make_window()
    window._render_logs(["a", "b", "c"])
    text = window.log_text
    text.log.clear()
    window._render_logs(["b", "c", "d"])
    assert [l for l, _ in text.lines] == ["b", "c", "d"]
    inserts, deletes = _ops(text)
    assert len(deletes) == 1, "应只丢弃首部 1 行，而不是全量重绘"
    assert len(inserts) == 1 and inserts[0][1].rstrip("\n") == "d"


def test_log_equal_length_repeated_tail_still_refreshes():
    """末行内容重复时也要刷新（只比末行的实现会漏检）。"""
    window = make_window()
    window._render_logs(["x", "y", "same"])
    window.log_text.log.clear()
    window._render_logs(["y", "same", "same"])
    assert [l for l, _ in window.log_text.lines] == ["y", "same", "same"]


def test_log_tail_saturated_rolling_window_keeps_up():
    """模拟 /api/logs?tail=300 的真实场景：行数恒为 300，内容持续推进。"""
    size = 300
    window = make_window()
    window._render_logs(["line-%03d" % i for i in range(size)])
    text = window.log_text
    text.log.clear()
    window._render_logs(["line-%03d" % i for i in range(1, size + 1)])
    rendered = [l for l, _ in text.lines]
    assert rendered[-1] == "line-300"
    assert rendered[0] == "line-001"
    inserts, deletes = _ops(text)
    assert len(deletes) == 1 and len(inserts) == 1, "应增量推进，而不是全量重绘 300 行"


def test_log_head_trim_over_limit():
    window = make_window()
    big = ["line-%d" % i for i in range(500)]
    window._render_logs(big)
    lines = [l for l, _ in window.log_text.lines]
    assert len(lines) <= wt_task_queue_window.TaskQueueWindow._LOG_MAX_LINES
    # 保留的是尾部（最新的行）
    assert lines[-1] == "line-499"
    assert lines[0] == "line-%d" % (500 - len(lines))


def test_log_tags_classified_on_increment():
    window = make_window()
    window._render_logs(["正常开始"])
    window._render_logs(["正常开始", "步骤失败: x"])
    tags = [t for _, t in window.log_text.lines]
    assert tags == ["system", "error"]


def test_log_append_stream_lines():
    window = make_window()
    # 首次增量
    window._append_stream_lines(window.log_text, ["stream line 1", "stream line 2"], "_log_rendered_lines")
    lines = [l for l, _ in window.log_text.lines]
    assert lines == ["stream line 1", "stream line 2"]
    assert getattr(window, "_log_rendered_lines", 0) == 2

    # 二次增量（绝不清除前序日志，直接追加并正确分类标签）
    window._append_stream_lines(window.log_text, ["stream line 3 [ERROR]"], "_log_rendered_lines")
    lines = [l for l, _ in window.log_text.lines]
    assert lines == ["stream line 1", "stream line 2", "stream line 3 [ERROR]"]
    tags = [t for _, t in window.log_text.lines]
    assert tags == ["info", "info", "error"]

