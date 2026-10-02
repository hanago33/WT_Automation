# -*- coding: utf-8 -*-
"""Unit tests for WT_Launcher Phase 7 UI/UX enhancements:
- Simple Mode data readiness badge check
- Range step selection (replace & append modes)
- Step selection count badge with filtering state
- Diagnostic report formatting with heuristic advice
- Quick actions: single step rerun, editor open, screenshot open
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import WT_Launcher
import wt_theme


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class LauncherHarness(WT_Launcher.LauncherApp):
    pass


def make_launcher_harness():
    app = object.__new__(LauncherHarness)
    app.root = MagicMock()
    app.theme = wt_theme.get_palette()
    app.step_filter_var = FakeVar("")
    app.step_only_selected_var = FakeVar(False)
    app.step_selection_count_var = FakeVar("")
    app.skip_setup_var = FakeVar(False)
    app.step_check_vars = {}
    app.flow_steps = []
    app._append_log = MagicMock()
    app._launch_automation = MagicMock()
    app.open_flow_editor = MagicMock()
    app.open_step_screenshot = MagicMock()
    return app


class TestLauncherPhase7UX(unittest.TestCase):
    def test_check_flow_data_readiness_valid_file(self):
        app = make_launcher_harness()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as flow_f:
            with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as data_f:
                data_path = data_f.name
            flow_content = {
                "runtimeConfig": {
                    "sourceFilePath": data_path
                }
            }
            json.dump(flow_content, flow_f)
            flow_path = flow_f.name

        try:
            ready, msg = app._check_flow_data_readiness(flow_path)
            self.assertTrue(ready)
            self.assertEqual(msg, "数据源就绪")
        finally:
            if os.path.exists(flow_path):
                os.remove(flow_path)
            if os.path.exists(data_path):
                os.remove(data_path)

    def test_check_flow_data_readiness_missing_file(self):
        app = make_launcher_harness()
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as flow_f:
            flow_content = {
                "runtimeConfig": {
                    "sourceFilePath": "D:/non_existent_path_xyz_12345.dat"
                }
            }
            json.dump(flow_content, flow_f)
            flow_path = flow_f.name

        try:
            ready, msg = app._check_flow_data_readiness(flow_path)
            self.assertFalse(ready)
            self.assertEqual(msg, "数据源待配置")
        finally:
            if os.path.exists(flow_path):
                os.remove(flow_path)

    def test_select_step_range_replace_mode(self):
        app = make_launcher_harness()
        app.flow_steps = [
            {"id": "step_01", "name": "Step 1"},
            {"id": "step_02", "name": "Step 2"},
            {"id": "step_03", "name": "Step 3"},
            {"id": "step_04", "name": "Step 4"},
            {"id": "step_05", "name": "Step 5"},
        ]
        app.step_check_vars = {
            "step_01": FakeVar(True),
            "step_02": FakeVar(False),
            "step_03": FakeVar(False),
            "step_04": FakeVar(False),
            "step_05": FakeVar(True),
        }

        # 闭区间选择 step 1 到 step 3 (索引 1 到 3，即 step_02, step_03, step_04)
        count = app.select_step_range(1, 3, mode="replace")
        self.assertEqual(count, 3)
        self.assertFalse(app.step_check_vars["step_01"].get())
        self.assertTrue(app.step_check_vars["step_02"].get())
        self.assertTrue(app.step_check_vars["step_03"].get())
        self.assertTrue(app.step_check_vars["step_04"].get())
        self.assertFalse(app.step_check_vars["step_05"].get())

    def test_select_step_range_append_mode(self):
        app = make_launcher_harness()
        app.flow_steps = [
            {"id": "step_01", "name": "Step 1"},
            {"id": "step_02", "name": "Step 2"},
            {"id": "step_03", "name": "Step 3"},
        ]
        app.step_check_vars = {
            "step_01": FakeVar(True),
            "step_02": FakeVar(False),
            "step_03": FakeVar(False),
        }

        # 追加模式：保留 step_01 为 True，把 step_02 到 step_03 也勾选上
        count = app.select_step_range(1, 2, mode="append")
        self.assertEqual(count, 2)
        self.assertTrue(app.step_check_vars["step_01"].get())
        self.assertTrue(app.step_check_vars["step_02"].get())
        self.assertTrue(app.step_check_vars["step_03"].get())

    def test_update_step_selection_count_filter_label(self):
        app = make_launcher_harness()
        app.step_check_vars = {
            "step_01": FakeVar(True),
            "step_02": FakeVar(False),
        }
        app._update_step_selection_count()
        self.assertEqual(app.step_selection_count_var.get(), "已选 1/2 步")

        app.step_filter_var.set("test")
        app._update_step_selection_count()
        self.assertEqual(app.step_selection_count_var.get(), "已选 1/2 步 (过滤中)")

    def test_format_report_step_diagnostic_success(self):
        app = make_launcher_harness()
        item = {
            "stepId": "step_01",
            "stepName": "启动软件",
            "status": "success",
            "actionType": "launch",
            "strategy": "normal",
            "elapsedSeconds": 1.234,
        }
        text = app._format_report_step_diagnostic(item)
        self.assertIn("🟢 执行成功", text)
        self.assertIn("step_01 - 启动软件", text)
        self.assertIn("1.234 秒", text)
        self.assertNotIn("错误分析", text)

    def test_format_report_step_diagnostic_error_heuristic(self):
        app = make_launcher_harness()
        item = {
            "stepId": "step_05",
            "stepName": "等待主界面加载",
            "status": "failed",
            "actionType": "action",
            "strategy": "normal",
            "elapsedSeconds": 10.0,
            "error": "Wait timeout: element not found on screen",
        }
        text = app._format_report_step_diagnostic(item)
        self.assertIn("🔴 执行失败", text)
        self.assertIn("❌ 错误分析", text)
        self.assertIn("💡 启发式排查建议:", text)
        # 应包含针对超时和未找到的启发式提示
        self.assertIn("超时", text)
        self.assertIn("控件", text)

    def test_start_single_step_invokes_automation(self):
        app = make_launcher_harness()
        app.start_single_step("step_03")
        app._launch_automation.assert_called_once()
        args, kwargs = app._launch_automation.call_args
        self.assertEqual(args[0], ["--steps", "step_03"])
        self.assertIn("step_03", kwargs.get("banner", ""))

    def test_open_step_in_flow_editor_invokes_editor(self):
        app = make_launcher_harness()
        app.open_step_in_flow_editor("step_09")
        app.open_flow_editor.assert_called_once_with(step_id="step_09")

    def test_timeline_output_line_handling_precedence_and_regex(self):
        """测试时间线在输出行中优先匹配失败，以及使用 regex 提取 step-id（验证 re 模块已正确导入）。"""
        app = make_launcher_harness()
        app.process_var = FakeVar("")
        app.current_step_var = FakeVar("")
        app.status_var = FakeVar("")
        app._tag_for_line = MagicMock(return_value="info")
        app.step_timeline = MagicMock()

        # 行中同时包含成功和失败字眼（例如 "完成前检测到失败"），失败必须优先判定
        app._handle_output_line("[step-calc] 步骤在完成前发生异常失败")
        app.step_timeline.set_step_finished.assert_called_with("step-calc", status="failed")

        # 正常成功行
        app.step_timeline.reset_mock()
        app._handle_output_line("[step_upload] 流程步骤执行完成")
        app.step_timeline.set_step_finished.assert_called_with("step_upload", status="success")

    def test_poll_output_queue_resilience_to_exceptions(self):
        """测试即使某行日志处理抛出异常，输出队列轮询的 root.after 也必定被重新调度。"""
        import queue
        app = make_launcher_harness()
        app.output_queue = queue.Queue()
        app.output_queue.put(("line", "crash_line"))

        # 模拟 _handle_output_line 抛异常
        app._handle_output_line = MagicMock(side_effect=RuntimeError("unexpected log error"))

        try:
            app._poll_output_queue()
        except RuntimeError:
            pass

        # 验证 root.after(120, ...) 依然被调用
        app.root.after.assert_called_with(120, app._poll_output_queue)


if __name__ == "__main__":
    unittest.main()
