# -*- coding: utf-8 -*-
"""Unit tests for WT_Launcher Phase 8 Epic 1:
- StepTimelineBar timeline state management (set_steps, running, finished, sync_with_report)
- Pipe-separated keyword matching in LogFilter
- Launcher log quick mode switching (all, milestones, errors)
- Stop button transition logic
"""
import unittest
from unittest.mock import MagicMock, patch

import wt_log_query
import wt_theme
import WT_Launcher


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class TestStepTimelineBar(unittest.TestCase):
    def setUp(self):
        self.bar = object.__new__(WT_Launcher.StepTimelineBar)
        self.bar.theme = wt_theme.get_palette()
        self.bar.steps = []
        self.bar.active_step_id = None
        self.bar.step_start_time = None
        self.bar._timer_id = None
        self.bar.canvas = MagicMock()
        self.bar.canvas.winfo_width.return_value = 600
        self.bar.summary_var = FakeVar("")
        self.bar.timer_var = FakeVar("")
        self.bar.after = MagicMock(return_value="timer_123")
        self.bar.after_cancel = MagicMock()

    def test_set_steps_initializes_properly(self):
        input_steps = [
            {"id": "step_01", "name": "启动程序"},
            {"id": "step_02", "name": "导入数据"},
            {"id": "step_03", "name": "执行计算"},
        ]
        self.bar.set_steps(input_steps)
        self.assertEqual(len(self.bar.steps), 3)
        self.assertEqual(self.bar.steps[0]["status"], "pending")
        self.assertIn("3 步", self.bar.summary_var.get())

    def test_step_progression(self):
        self.bar.set_steps([
            {"id": "step_01", "name": "第1步"},
            {"id": "step_02", "name": "第2步"},
        ])
        # 开始运行第1步
        self.bar.set_running("step_01")
        self.assertEqual(self.bar.steps[0]["status"], "running")
        self.assertEqual(self.bar.active_step_id, "step_01")
        self.assertIn("正在执行第 1 步", self.bar.summary_var.get())

        # 完成第1步
        self.bar.set_step_finished("step_01", status="success", elapsed=1.5)
        self.assertEqual(self.bar.steps[0]["status"], "success")
        self.assertEqual(self.bar.steps[0]["elapsed"], 1.5)
        self.assertIsNone(self.bar.active_step_id)

        # 开始并完成第2步
        self.bar.set_running("step_02")
        self.bar.set_step_finished("step_02", status="success", elapsed=2.0)
        self.assertIn("全部 2 步成功", self.bar.summary_var.get())

    def test_finish_all_on_error(self):
        self.bar.set_steps([
            {"id": "step_01", "name": "第1步"},
            {"id": "step_02", "name": "第2步"},
        ])
        self.bar.set_running("step_01")
        self.bar.finish_all(return_code=1)
        self.assertEqual(self.bar.steps[0]["status"], "failed")
        self.assertIn("失败", self.bar.summary_var.get())

    def test_sync_with_report(self):
        self.bar.set_steps([
            {"id": "step_01", "name": "第1步"},
            {"id": "step_02", "name": "第2步"},
        ])
        report = {
            "stepResults": [
                {"stepId": "step_01", "status": "success", "elapsedSeconds": 3.2},
                {"stepId": "step_02", "status": "failed", "elapsedSeconds": 0.8},
            ]
        }
        self.bar.sync_with_report(report)
        self.assertEqual(self.bar.steps[0]["status"], "success")
        self.assertEqual(self.bar.steps[0]["elapsed"], 3.2)
        self.assertEqual(self.bar.steps[1]["status"], "failed")
        self.assertEqual(self.bar.steps[1]["elapsed"], 0.8)
        self.assertIn("1 步失败", self.bar.summary_var.get())


class TestLogQuickModes(unittest.TestCase):
    def test_pipe_keyword_matching(self):
        filter_obj = wt_log_query.LogFilter(keyword="里程碑|步骤|落盘")
        self.assertTrue(filter_obj.matches("[INFO] 正在执行步骤 01"))
        self.assertTrue(filter_obj.matches("[INFO] 数据已成功落盘"))
        self.assertTrue(filter_obj.matches("[INFO] 到达关键里程碑"))
        self.assertFalse(filter_obj.matches("[INFO] 正在休眠等待 5 秒"))

    def test_launcher_quick_modes_switching(self):
        app = object.__new__(WT_Launcher.LauncherApp)
        app.theme = wt_theme.get_palette()
        app.log_quick_mode_var = FakeVar("all")
        app.log_level_var = FakeVar(wt_log_query.LEVEL_CHOICES[0][0])
        app.log_keyword_var = FakeVar("")
        app.log_step_var = FakeVar("")
        app._quick_mode_buttons = {}
        app._apply_log_filter = MagicMock()

        # 切到关键里程碑
        app._set_log_quick_mode("milestones")
        self.assertEqual(app.log_quick_mode_var.get(), "milestones")
        self.assertIn("里程碑", app.log_keyword_var.get())
        app._apply_log_filter.assert_called()

        # 切到仅看异常
        app._set_log_quick_mode("errors")
        self.assertEqual(app.log_quick_mode_var.get(), "errors")
        self.assertEqual(app.log_level_var.get(), "ERROR 及以上")
        self.assertEqual(app.log_keyword_var.get(), "")

        # 切回全部
        app._set_log_quick_mode("all")
        self.assertEqual(app.log_quick_mode_var.get(), "all")
        self.assertEqual(app.log_level_var.get(), "全部级别")
        self.assertEqual(app.log_keyword_var.get(), "")


class TestStopButtonImmediateFeedback(unittest.TestCase):
    def test_stop_button_state_reset_in_set_running(self):
        app = object.__new__(WT_Launcher.LauncherApp)
        app.start_button = MagicMock()
        app.stop_button = MagicMock()

        # 启动时
        app._set_running_state(True)
        app.start_button.config.assert_called_with(state=WT_Launcher.tk.DISABLED)
        app.stop_button.config.assert_called_with(text="⏹ 停止当前", state=WT_Launcher.tk.NORMAL)

        # 停止时
        app._set_running_state(False)
        app.start_button.config.assert_called_with(state=WT_Launcher.tk.NORMAL)
        app.stop_button.config.assert_called_with(text="⏹ 停止当前", state=WT_Launcher.tk.DISABLED)


if __name__ == "__main__":
    unittest.main()
