# encoding: utf-8
"""Simple 远程队列 P1 加固测试（阶段二）。

覆盖：
- P1-4  重试移出 UI 线程：_retry_failed_remote_tasks 异步执行 + 结果回主线程
- P1-5  轮询间隔对齐标称 2s：_poll_delay_ms(0) == POLL_MS
- P1-6(3) 轮询线程顶层兜底：_simple_remote_poll 异常时收尾而非静默死亡
- P1-7  运行中日志自动刷新：_maybe_refresh_selected_logs 的守卫与触发
- 提交前连通检测：_test_queue_connection 识别未配置/可达/令牌错/服务未启动
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_task_server as ts
from wt_task_queue_window import TaskQueueWindow, POLL_MS
from WT_Launcher import LauncherApp


class FakeThread(object):
    """同步执行 target 的假线程，让测试确定无竞态。"""

    def __init__(self, target, args=(), kwargs=None, **kw):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


class P1PollDelayTests(unittest.TestCase):
    """P1-5：轮询间隔稳态回到标称 2s，失败退避 2→4→8→16→30s。"""

    def test_steady_state_is_poll_ms(self):
        self.assertEqual(TaskQueueWindow._poll_delay_ms(0), POLL_MS)

    def test_first_failure_keeps_2s(self):
        self.assertEqual(TaskQueueWindow._poll_delay_ms(1), 2000)

    def test_backoff_grows(self):
        self.assertEqual(TaskQueueWindow._poll_delay_ms(3), 8000)

    def test_cap_at_30s(self):
        self.assertEqual(TaskQueueWindow._poll_delay_ms(10), 30000)

    def test_negative_streak_clamped(self):
        self.assertEqual(TaskQueueWindow._poll_delay_ms(-1), POLL_MS)


class P1RetryThreadTests(unittest.TestCase):
    """P1-4：重试在后台线程执行，界面不阻塞。"""

    def test_retry_runs_async_and_reports_via_post_ui(self):
        calls = []
        statuses = []
        retry_result = []

        class Win(object):
            def control_task(self, task_id, action):
                calls.append((task_id, action))
                return True

        class Self(object):
            def __init__(self):
                self._task_queue_window = Win()

            def _simple_set_status(self, text, tag=""):
                statuses.append((text, tag))

            def _post_ui(self, callback):
                callback()  # 模拟主线程执行

            def _retry_finished(self, ok, fail):
                retry_result.append((ok, fail))

        self_obj = Self()
        with patch("WT_Launcher.threading.Thread", FakeThread):
            LauncherApp._retry_failed_remote_tasks(self_obj, ["t1", "t2"])

        self.assertEqual(calls, [("t1", "resume"), ("t2", "resume")])
        self.assertEqual(retry_result, [(2, 0)])
        self.assertTrue(any("正在重试 2 个" in s for s, _ in statuses))

    def test_retry_empty_ids_returns_early(self):
        class Self(object):
            pass

        self_obj = Self()
        with patch("WT_Launcher.threading.Thread", FakeThread) as fake_thread:
            LauncherApp._retry_failed_remote_tasks(self_obj, [])
        # 不应启动任何线程/窗口


class P1PollWrapperTests(unittest.TestCase):
    """阶段二-3：_simple_remote_poll 顶层异常兜底，避免线程静默死亡。"""

    def test_unexpected_exception_finishes_run(self):
        logs = []
        finished = []

        obj = LauncherApp.__new__(LauncherApp)
        obj._append_log = lambda text, tag="": logs.append(text)
        obj._post_ui = lambda cb: cb()
        obj._simple_finish_remote_run = lambda note="": finished.append(note)

        def boom():
            raise RuntimeError("boom")

        obj._simple_remote_poll_impl = boom
        LauncherApp._simple_remote_poll(obj)

        self.assertTrue(any("远程轮询异常终止" in line for line in logs))
        self.assertEqual(len(finished), 1)
        self.assertIn("远程轮询异常终止", finished[0])


class P1AutoLogRefreshTests(unittest.TestCase):
    """P1-7：运行中任务日志随轮询自动刷新（含防并发守卫）。"""

    def _make_obj(self):
        obj = TaskQueueWindow.__new__(TaskQueueWindow)
        obj._log_fetching = False
        obj._selected_task_id = lambda: "t1"
        obj._started = []
        obj._fetch_logs_worker = lambda task_id: obj._started.append(task_id)
        return obj

    def test_running_selected_triggers_fetch(self):
        obj = self._make_obj()
        with patch("wt_task_queue_window.threading.Thread", FakeThread):
            obj._maybe_refresh_selected_logs([{"taskId": "t1", "status": "running"}])
        self.assertEqual(obj._started, ["t1"])
        self.assertTrue(obj._log_fetching)

    def test_non_running_does_not_fetch(self):
        obj = self._make_obj()
        with patch("wt_task_queue_window.threading.Thread", FakeThread):
            obj._maybe_refresh_selected_logs([{"taskId": "t1", "status": "pending"}])
        self.assertEqual(obj._started, [])
        self.assertFalse(obj._log_fetching)

    def test_in_flight_guard_prevents_duplicate(self):
        obj = self._make_obj()
        obj._log_fetching = True
        with patch("wt_task_queue_window.threading.Thread", FakeThread):
            obj._maybe_refresh_selected_logs([{"taskId": "t1", "status": "running"}])
        self.assertEqual(obj._started, [])
        self.assertTrue(obj._log_fetching)

    def test_missing_task_does_not_fetch(self):
        obj = self._make_obj()
        with patch("wt_task_queue_window.threading.Thread", FakeThread):
            obj._maybe_refresh_selected_logs([{"taskId": "t2", "status": "running"}])
        self.assertEqual(obj._started, [])


class P1HttpErrorBodyTests(unittest.TestCase):
    """修复：_queue_request 抛出的 HTTPError 必须带响应体，exc.read() 可用。

    之前 fp=None 导致调用方 exc.read() 抛 KeyError（如 409/404 时后台线程崩溃）。
    """

    def test_http_error_read_returns_body(self):
        import urllib.error

        with tempfile.TemporaryDirectory() as tmp:
            srv = ts.TaskServer(
                ("127.0.0.1", 0), auth_token="good",
                queue_db=os.path.join(tmp, "q.db"),
                task_log_dir=os.path.join(tmp, "tasks"),
                server_log_path=os.path.join(tmp, "server.log"),
            )
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                time.sleep(0.3)
                holder = type("H", (), {})()
                holder.base_url = "http://127.0.0.1:{}".format(port)
                holder.token_var = type(
                    "V", (), {"get": lambda self: "good", "set": lambda self, v: None}
                )()
                holder._http_lock = threading.Lock()
                holder._http_conn = None
                holder._make_conn = TaskQueueWindow._make_conn.__get__(holder)
                holder._queue_request = TaskQueueWindow._queue_request.__get__(holder)
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    holder._queue_request("GET", "/api/tasks/no_such/report")
                body = ctx.exception.read().decode("utf-8", errors="replace")
                self.assertIn("error", body)
            finally:
                srv.shutdown()
                srv.server_close()


class P1QueueConnectionTests(unittest.TestCase):
    """提交前连通检测：识别未配置 / 服务可达 / 令牌错误 / 服务未启动。"""

    @staticmethod
    def _make_app(url, token):
        app = LauncherApp.__new__(LauncherApp)
        app.task_queue_url = url
        app.task_queue_user = "alice"
        app.task_queue_token = token
        return app

    def test_unconfigured_url(self):
        ok, msg = LauncherApp._test_queue_connection(self._make_app("", "t"))
        self.assertFalse(ok)
        self.assertIn("服务器地址", msg)

    def test_unconfigured_token(self):
        ok, msg = LauncherApp._test_queue_connection(
            self._make_app("http://127.0.0.1:8768", "")
        )
        self.assertFalse(ok)
        self.assertIn("令牌", msg)

    def test_connection_ok_and_wrong_token_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "q.db")
            srv = ts.TaskServer(
                ("127.0.0.1", 0), auth_token="good", queue_db=db,
                task_log_dir=os.path.join(tmp, "tasks"),
                server_log_path=os.path.join(tmp, "server.log"),
            )
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                time.sleep(0.3)
                base = "http://127.0.0.1:{}".format(port)
                ok, msg = LauncherApp._test_queue_connection(
                    self._make_app(base, "good")
                )
                self.assertTrue(ok)
                self.assertIn("连接成功", msg)
                ok, msg = LauncherApp._test_queue_connection(
                    self._make_app(base, "bad")
                )
                self.assertFalse(ok)
                self.assertIn("401", msg)
            finally:
                srv.shutdown()
                srv.server_close()

    def test_server_down(self):
        ok, msg = LauncherApp._test_queue_connection(
            self._make_app("http://127.0.0.1:1", "t")
        )
        self.assertFalse(ok)
        self.assertIn("无法连接", msg)


if __name__ == "__main__":
    unittest.main()
