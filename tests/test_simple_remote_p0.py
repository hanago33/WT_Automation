# encoding: utf-8
"""Simple 远程队列 P0 修复针对性测试（P0-1/P0-2/P0-3）。

覆盖：
- P0-1  LauncherApp._post_ui 存在，且窗口销毁后调用不抛异常
- P0-2  提交防重入：_remote_submit_ready 在提交中标志置位时拒绝；
        取消/空提交路径回调 ([], [])，避免标志与按钮永久卡死
- P0-3  提交中停止：stop 分支终止已登记任务、清标志、收尾 note=已停止提交；
        正常路径保留增量登记任务并固定 total、启动轮询
"""
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from wt_task_queue_window import TaskQueueWindow
from WT_Launcher import LauncherApp


class MockWindow(object):
    def __init__(self):
        self.calls = []

    def control_task(self, task_id, action):
        self.calls.append((task_id, action))
        return True


class P0PostUITests(unittest.TestCase):
    """P0-1：LauncherApp 必须提供 _post_ui（远程轮询/本地队列回调依赖它）。"""

    def test_post_ui_method_exists(self):
        self.assertTrue(hasattr(LauncherApp, "_post_ui"))

    def test_post_ui_silent_after_window_closed(self):
        class FakeRoot(object):
            def after(self, *_a, **_k):
                raise RuntimeError("window closed")

        obj = LauncherApp.__new__(LauncherApp)
        obj.root = FakeRoot()
        obj._post_ui(lambda: None)  # 不应抛异常

    def test_post_ui_dispatches_callback(self):
        fired = []

        class FakeRoot(object):
            def after(self, _ms, callback):
                callback()

        obj = LauncherApp.__new__(LauncherApp)
        obj.root = FakeRoot()
        obj._post_ui(lambda: fired.append(True))
        self.assertEqual(fired, [True])


class P0SubmitReadyTests(unittest.TestCase):
    """P0-2：提交防重入——提交中标志置位时拒绝再次提交。"""

    def test_rejected_while_submitting(self):
        obj = LauncherApp.__new__(LauncherApp)
        obj._simple_remote_submitting = True
        with patch("WT_Launcher.messagebox.showinfo"):
            self.assertFalse(obj._remote_submit_ready())

    def test_rejected_while_running(self):
        obj = LauncherApp.__new__(LauncherApp)
        obj._simple_remote_submitting = False
        obj._simple_remote_task_ids = {"t1": True}
        with patch("WT_Launcher.messagebox.showinfo"):
            self.assertFalse(obj._remote_submit_ready())


class P0SubmitCallbackTests(unittest.TestCase):
    """P0-2/P0-3：取消回调与增量终止。"""

    def test_cancel_callback_fires_empty(self):
        calls = []
        TaskQueueWindow._call_submit_callback(
            lambda ids, results: calls.append((ids, results))
        )
        self.assertEqual(calls, [([], [])])

    def test_cancel_callback_none_is_safe(self):
        TaskQueueWindow._call_submit_callback(None)

    def test_terminate_remote_tasks_iterates(self):
        logs = []

        class MockSelf(object):
            def _append_log(self, text, tag=""):
                logs.append(text)

        window = MockWindow()
        LauncherApp._terminate_remote_tasks(MockSelf(), ["t1", "t2"], window)
        self.assertEqual(window.calls, [("t1", "terminate"), ("t2", "terminate")])
        self.assertTrue(any("已终止 2 个" in line for line in logs))


class _BaseRemoteSelf(object):
    """LauncherApp._simple_remote_submitted 的轻量桩。"""

    def __init__(self, stop=False):
        self._simple_remote_lock = threading.Lock()
        self._simple_remote_submitting = True
        self._simple_remote_stop = stop
        self._simple_remote_task_ids = {"t1": True} if stop else {}
        self._simple_remote_outcomes = {}
        self._task_queue_window = MockWindow()
        self.logs = []
        self.status = []
        self.finish_notes = []
        self.confirm_calls = []
        self.poll_calls = []
        self._terminate_remote_tasks = LauncherApp._terminate_remote_tasks.__get__(self)

    def _simple_finish_remote_run(self, note=""):
        self.finish_notes.append(note)

    def _simple_set_status(self, text, tag=""):
        self.status.append((text, tag))

    def _append_log(self, text, tag=""):
        self.logs.append(text)

    def _confirm_remote_tasks(self, task_ids, window):
        self.confirm_calls.append(task_ids)
        return []

    def _simple_remote_poll(self):
        self.poll_calls.append(True)


class P0SubmittedStopTests(unittest.TestCase):
    """P0-3：提交进行中停止——终止已登记任务并收尾，不产生孤儿。"""

    def test_stop_branch_terminates_and_finishes(self):
        self_obj = _BaseRemoteSelf(stop=True)
        LauncherApp._simple_remote_submitted(self_obj, [], [])
        self.assertFalse(self_obj._simple_remote_submitting)
        self.assertEqual(self_obj.finish_notes, ["已停止提交"])
        time.sleep(0.4)  # 等待后台终止线程
        self.assertEqual(
            sorted(call[0] for call in self_obj._task_queue_window.calls),
            ["t1"],
        )
        self.assertEqual(self_obj._simple_remote_task_ids, {})


class P0SubmittedCancelTests(unittest.TestCase):
    """P0-2：空提交（对话框取消/未选板块）走中性收尾而非报错。"""

    def test_cancel_path_note(self):
        self_obj = _BaseRemoteSelf(stop=False)
        LauncherApp._simple_remote_submitted(self_obj, [], [])
        self.assertEqual(self_obj.finish_notes, ["已取消提交"])
        self.assertFalse(self_obj._simple_remote_submitting)

    def test_failed_path_note(self):
        self_obj = _BaseRemoteSelf(stop=False)
        LauncherApp._simple_remote_submitted(
            self_obj, [], [("板块A", "失败：连接拒绝")]
        )
        self.assertEqual(self_obj.finish_notes, ["远程提交失败，未获取到任务 ID"])


class P0SubmittedNormalTests(unittest.TestCase):
    """P0-3：正常提交保留增量登记、固定 total 并启动轮询。"""

    @patch("WT_Launcher.messagebox.showinfo")
    def test_normal_path_keeps_incremental_ids(self, mock_info):
        self_obj = _BaseRemoteSelf(stop=False)
        self_obj._simple_remote_task_ids = {"t1": True}  # 模拟增量登记已存在
        LauncherApp._simple_remote_submitted(
            self_obj, ["t1"], [("板块A", "已提交")]
        )
        self.assertEqual(self_obj._simple_remote_task_ids, {"t1": True})
        self.assertEqual(self_obj._simple_remote_total, 1)
        self.assertFalse(self_obj._simple_remote_submitting)
        time.sleep(0.3)
        self.assertGreaterEqual(len(self_obj.poll_calls), 1)


if __name__ == "__main__":
    unittest.main()
