# encoding: utf-8
"""阶段一验收测试：任务队列窗口 UI 现代化与 Simple 模式工具栏重构。

覆盖：
1. TaskQueueWindow Master-Detail 详情卡片联动与状态药丸更新；
2. 终端日志复制与清屏功能；
3. WT_Launcher Simple 模式工具栏「远程模式」智能联动与冗余按钮消除。
"""

import tkinter as tk
import unittest

from wt_task_queue_window import TaskQueueWindow


class TestTaskQueueWindowPhase1(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError:
            self.skipTest("No GUI environment available")

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()

    def test_detail_card_updates_with_selected_task(self):
        """测试选中任务时右侧看板卡片及进度条联动更新。"""
        win = TaskQueueWindow(self.root, "http://127.0.0.1:8768", "testuser", "token123")
        self.assertEqual(win.detail_task_id_var.get(), "未选中任务")
        self.assertIn("未选择", win.detail_status_badge.cget("text"))

        # 模拟选中进行中任务
        running_task = {
            "taskId": "task-test-001",
            "status": "running",
            "user": "alice",
            "priority": 10,
            "scheduledAt": "2026-09-09 12:00:00",
            "createdAt": "2026-09-09 11:55:00",
            "currentStepName": "计算网格数据",
            "currentStepIndex": 3,
            "totalSteps": 6,
        }
        win._update_task_detail_card(running_task)

        self.assertEqual(win.detail_task_id_var.get(), "任务: task-test-001")
        self.assertEqual(win.detail_status_badge.cget("text"), "运行中")
        self.assertIn("alice", win.detail_meta_var.get())
        self.assertIn("优先级: 10", win.detail_meta_var.get())
        self.assertIn("计算网格数据", win.detail_step_var.get())
        self.assertEqual(win.detail_progressbar["value"], 3)
        self.assertEqual(win.detail_progressbar["maximum"], 6)
        self.assertIn("50%", win.detail_progress_var.get())

        # 模拟选中成功任务
        success_task = {
            "taskId": "task-test-002",
            "status": "success",
            "user": "bob",
            "priority": 0,
        }
        win._update_task_detail_card(success_task)
        self.assertEqual(win.detail_task_id_var.get(), "任务: task-test-002")
        self.assertEqual(win.detail_status_badge.cget("text"), "成功")
        self.assertEqual(win.detail_progressbar["value"], 100)
        self.assertEqual(win.detail_progress_var.get(), "100%")

        # 模拟切回无任务状态
        win._update_task_detail_card(None)
        self.assertEqual(win.detail_task_id_var.get(), "未选中任务")
        self.assertEqual(win.detail_progressbar["value"], 0)

    def test_log_copy_and_clear(self):
        """测试日志文本复制与清空。"""
        win = TaskQueueWindow(self.root, "http://127.0.0.1:8768", "testuser", "token123")
        win._render_logs(["Line 1: init server", "Line 2: running task"])
        self.assertIn("Line 1", win.log_text.get("1.0", tk.END))

        win._copy_log_text()
        clipboard_content = self.root.clipboard_get()
        self.assertIn("Line 1: init server", clipboard_content)
        self.assertIn("Line 2: running task", clipboard_content)

        win._clear_log_text()
        content_after = win.log_text.get("1.0", tk.END).strip()
        self.assertEqual(content_after, "")
        self.assertEqual(win._log_rendered_lines, 0)


class TestLauncherSimpleToolbarPhase1(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
            self.root.withdraw()
        except tk.TclError:
            self.skipTest("No GUI environment available")

    def tearDown(self):
        if hasattr(self, "root"):
            self.root.destroy()

    def test_simple_remote_mode_toggle_updates_main_button(self):
        """测试勾选/取消勾选远程模式时，主按钮文案与颜色的智能联动。"""
        import WT_Launcher

        # 构造轻量级假环境避免启动整个完整应用
        app = object.__new__(WT_Launcher.LauncherApp)
        app.simple_remote_var = tk.BooleanVar(value=False)
        toolbar = tk.Frame(self.root)
        toolbar.pack()

        app.btn_simple_run = tk.Button(
            toolbar, text="▶ 运行所选板块", bg="#059669"
        )
        app.btn_simple_run.pack(side=tk.LEFT)

        app.btn_simple_submit_remote = tk.Button(
            toolbar, text="提交所选板块到远程队列"
        )
        # 初始未 pack

        app.simple_remote_var.trace_add("write", app._on_simple_remote_mode_changed)
        app._on_simple_remote_mode_changed()

        # 本地模式断言
        self.assertEqual(app.btn_simple_run.cget("text"), "▶ 运行所选板块")
        self.assertEqual(app.btn_simple_run.cget("bg"), "#059669")
        self.assertFalse(bool(app.btn_simple_submit_remote.winfo_ismapped()))

        # 切换到远程模式断言
        app.simple_remote_var.set(True)
        self.assertEqual(app.btn_simple_run.cget("text"), "☁ 提交远程队列")
        self.assertEqual(app.btn_simple_run.cget("bg"), "#2563eb")
        self.assertFalse(bool(app.btn_simple_submit_remote.winfo_ismapped()))

        # 再次切回本地模式
        app.simple_remote_var.set(False)
        self.assertEqual(app.btn_simple_run.cget("text"), "▶ 运行所选板块")
        self.assertEqual(app.btn_simple_run.cget("bg"), "#059669")
        self.assertFalse(bool(app.btn_simple_submit_remote.winfo_ismapped()))


if __name__ == "__main__":
    unittest.main()
