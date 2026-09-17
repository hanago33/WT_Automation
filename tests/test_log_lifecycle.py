# encoding: utf-8
"""日志生命周期测试（P2）：文件按大小轮转、UI 日志区行数上限。

设计约束：测试自身不删除文件（环境的批量删除守卫会拦截），
改用 ``tempfile.mkdtemp`` 每次创建独立目录，互不干扰。
"""
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_logging


class LogRotationTests(unittest.TestCase):
    """按大小轮转：path -> path.1 -> path.2 …，超出份数的最旧备份被覆盖。"""

    def setUp(self):
        base = os.path.join(TESTS_DIR, ".tmp_rotate")
        os.makedirs(base, exist_ok=True)
        self.dir = tempfile.mkdtemp(prefix="rot_", dir=base)
        self.path = os.path.join(self.dir, "wt_automation.log")

    def _write(self, text):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text)

    def _read(self, suffix=""):
        with open(self.path + suffix, "r", encoding="utf-8") as f:
            return f.read()

    def test_should_rotate_handles_missing_file(self):
        """文件不存在时不应抛错（首次运行场景）。"""
        self.assertFalse(wt_logging.should_rotate(os.path.join(self.dir, "不存在.log")))

    def test_should_rotate_by_threshold(self):
        self._write("x" * 50)
        self.assertFalse(wt_logging.should_rotate(self.path, max_bytes=100))
        self.assertTrue(wt_logging.should_rotate(self.path, max_bytes=50))
        self.assertTrue(wt_logging.should_rotate(self.path, max_bytes=10))

    def test_rotate_moves_main_to_backup_one(self):
        payload = "内容" * 40
        self._write(payload)
        self.assertTrue(wt_logging.rotate_log(self.path, max_bytes=10, backup_count=3))
        self.assertFalse(os.path.exists(self.path), "轮转后主文件应被移走")
        self.assertEqual(self._read(".1"), payload)

    def test_rotate_is_noop_below_threshold(self):
        payload = "短"
        self._write(payload)
        self.assertFalse(
            wt_logging.rotate_log(self.path, max_bytes=1024 * 1024, backup_count=3)
        )
        self.assertEqual(self._read(), payload)
        self.assertFalse(os.path.exists(self.path + ".1"))

    def test_rotate_is_noop_for_missing_file(self):
        self.assertFalse(wt_logging.rotate_log(self.path, max_bytes=1, backup_count=3))

    def test_backups_never_exceed_backup_count(self):
        """连续轮转后，备份份数必须封顶在 backup_count。"""
        backup_count = 2
        for _ in range(backup_count + 4):
            self._write("y" * 64)
            wt_logging.rotate_log(self.path, max_bytes=32, backup_count=backup_count)
        for index in range(1, backup_count + 1):
            with self.subTest(index=index):
                self.assertTrue(
                    os.path.exists("%s.%d" % (self.path, index)),
                    "第 %d 份备份应存在" % index,
                )
        self.assertFalse(
            os.path.exists("%s.%d" % (self.path, backup_count + 1)),
            "超出 backup_count 的备份不应存在",
        )

    def test_newest_backup_holds_most_recent_content(self):
        self._write("第一次" * 20)
        wt_logging.rotate_log(self.path, max_bytes=10, backup_count=3)
        self._write("第二次" * 20)
        wt_logging.rotate_log(self.path, max_bytes=10, backup_count=3)
        self.assertIn("第二次", self._read(".1"))
        self.assertIn("第一次", self._read(".2"))

    def test_defaults_are_sane(self):
        self.assertEqual(wt_logging.LOG_MAX_BYTES, 5 * 1024 * 1024)
        self.assertGreaterEqual(wt_logging.LOG_BACKUP_COUNT, 1)


class MonitorWindowLineCapTests(unittest.TestCase):
    """监视器窗口日志区行数上限：长流程下 Text 不得无限增长。"""

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
        except Exception:
            raise unittest.SkipTest("无 tkinter 可用")
        cls.tk = tk
        try:
            cls.root = tk.Tk()
        except Exception:
            raise unittest.SkipTest("无法创建 Tk 根窗口（无显示环境）")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def _make_window(self):
        """绕过 __init__ 直接构造，避免创建可见窗口。"""
        import WT_AUT_recorded

        window = WT_AUT_recorded.MonitorWindow.__new__(WT_AUT_recorded.MonitorWindow)
        window.text_widget = self.tk.Text(self.root, wrap=self.tk.WORD)
        return window

    def test_trim_keeps_line_count_at_cap(self):
        window = self._make_window()
        window.LOG_MAX_LINES = 10
        for i in range(35):
            window.text_widget.insert(self.tk.END, "第 %d 行\n" % i)
            window._trim_to_max_lines()
        total = int(window.text_widget.index("end-1c").split(".")[0])
        self.assertLessEqual(total, 11, "行数应被裁剪到上限附近，实际 %d" % total)

    def test_trim_drops_oldest_lines_first(self):
        window = self._make_window()
        window.LOG_MAX_LINES = 5
        for i in range(20):
            window.text_widget.insert(self.tk.END, "第 %d 行\n" % i)
            window._trim_to_max_lines()
        content = window.text_widget.get("1.0", self.tk.END)
        self.assertIn("第 19 行", content, "最新行必须保留")
        self.assertNotIn("第 0 行", content, "最旧行应被裁掉")

    def test_trim_is_noop_below_cap(self):
        window = self._make_window()
        window.LOG_MAX_LINES = 100
        for i in range(5):
            window.text_widget.insert(self.tk.END, "第 %d 行\n" % i)
            window._trim_to_max_lines()
        content = window.text_widget.get("1.0", self.tk.END)
        self.assertIn("第 0 行", content)
        self.assertIn("第 4 行", content)

    def test_cap_matches_queue_window_for_consistency(self):
        import WT_AUT_recorded
        import wt_task_queue_window

        self.assertEqual(
            WT_AUT_recorded.MonitorWindow.LOG_MAX_LINES,
            wt_task_queue_window.TaskQueueWindow._LOG_MAX_LINES,
            "各日志区的行数上限应保持一致",
        )


if __name__ == "__main__":
    unittest.main()
