# encoding: utf-8
"""P1 修复针对性测试：可靠性批次。

覆盖：
- P1-1   窗口最小化恢复（wait_until_main_window_ready 就绪前先恢复）
- P1-2/5/6 定位器：send_keys 转义、UIPI 锁存 TTL、窗口存活 IsWindow
- P1-3   MUP 铁证：attach 判定含 deleted、错误留痕；diff 容差/目录漂移/快照元数据
- P1-4   投影后置校验：目标改读动作值、resolve 异常留痕
- P1-7   stepPolicy 深拷贝不写穿缓存、is_setup_step 元数据、未绑定步骤 failed、盲发 ENTER 禁止
"""
import os
import re
import sys
import tempfile
import threading
import time as time_mod
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator
import wt_window_helpers
import wt_flow_executor
import WT_AUT_recorded
import mup_data_files


# ---------------------------------------------------------------------------
# P1-1 窗口最小化恢复
# ---------------------------------------------------------------------------

class WindowMinimizeRestoreTests(unittest.TestCase):
    def _fake_user32(self):
        calls = {"restore": 0, "show": [], "iconic_before_restore": True}

        class FakeUser32:
            def IsWindowVisible(self, hwnd):
                return True

            def IsIconic(self, hwnd):
                return calls["iconic_before_restore"]

            def ShowWindow(self, hwnd, cmd):
                calls["show"].append(cmd)
                if cmd == 9:
                    calls["restore"] += 1
                    calls["iconic_before_restore"] = False
                return True

            def GetClassNameW(self, hwnd, buf, size):
                return 0

            def EnumWindows(self, callback, lparam):
                callback(1001, 0)
                return True

            def SendMessageTimeoutW(self, *args, **kwargs):
                return 1  # responsive

        return FakeUser32(), calls

    def test_wait_ready_restores_minimized_window(self):
        user32, calls = self._fake_user32()
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_window_helpers.time, "sleep", side_effect=lambda *a, **k: None))
            stack.enter_context(patch.object(wt_window_helpers.time, "time", return_value=100.0))
            wt_window_helpers.configure_wt_window_helpers(
                user32=user32,
                enum_windows_proc=lambda func: func,
                get_window_text=lambda hwnd: "Meteodyn Universe",
                get_window_rect=lambda hwnd: type("R", (), {"left": 0, "top": 0, "right": 800, "bottom": 600})(),
                log_step=lambda message: None,
            )
            hwnd = wt_window_helpers.wait_until_main_window_ready(
                re.compile("Meteodyn Universe"), timeout_seconds=30
            )
        self.assertEqual(hwnd, 1001)
        self.assertGreaterEqual(calls["restore"], 1, "最小化窗口必须在就绪判定前被恢复")


class OpenDialogNoBlindEnterTests(unittest.TestCase):
    def test_confirm_open_file_dialog_raises_when_dialog_missing(self):
        with patch.object(wt_window_helpers, "find_open_dialog", return_value=None):
            with self.assertRaises(RuntimeError):
                wt_window_helpers.confirm_open_file_dialog(timeout_seconds=1)


# ---------------------------------------------------------------------------
# P1-3 MUP 数据文件差异
# ---------------------------------------------------------------------------

class MupDataFilesDiffTests(unittest.TestCase):
    def _file_entry(self, name, size=100, mtime=1000.0):
        return {name: {"size": size, "mtime": mtime}}

    def test_small_mtime_delta_is_not_changed(self):
        before = {"terrain": self._file_entry("a.tif", size=100, mtime=1000.0)}
        after = {"terrain": self._file_entry("a.tif", size=100, mtime=1001.0)}  # 1s 内，容差 2s
        result = mup_data_files.diff(before, after)
        self.assertEqual(result["changedCount"], 0)

    def test_large_mtime_delta_is_changed(self):
        before = {"terrain": self._file_entry("a.tif", size=100, mtime=1000.0)}
        after = {"terrain": self._file_entry("a.tif", size=100, mtime=1005.0)}
        result = mup_data_files.diff(before, after)
        self.assertEqual(result["changedCount"], 1)

    def test_dir_changed_only_reports_drift(self):
        before = {"_meta": {"data_dir": r"C:\ProgramData\Meteodyn\MUP\local_a"}, "terrain": self._file_entry("a.tif")}
        after = {"_meta": {"data_dir": r"C:\ProgramData\Meteodyn\MUP\local_b"}, "terrain": {}}
        result = mup_data_files.diff(before, after)
        self.assertTrue(result.get("dirChanged"))
        self.assertEqual(result["newCount"], 0)
        self.assertEqual(result["deletedCount"], 0)

    def test_snapshot_records_meta(self):
        with patch.object(mup_data_files, "locate_data_dir", return_value=r"C:\data"):
            snap = mup_data_files.snapshot()
        self.assertIn("_meta", snap)
        self.assertEqual(snap["_meta"]["data_dir"], r"C:\data")

    def test_clear_cache_is_defensive(self):
        # locate_data_dir 无 lru_cache，clear_cache 不应抛错
        mup_data_files.clear_cache()


class AttachMupDataDiffTests(unittest.TestCase):
    def _run_attach(self, diff_result):
        report = {}
        # before 必须为真值快照，否则函数按"无基线"分支直接返回，不会执行 diff
        context = {"mupDataSnapshotBefore": {"_meta": {"data_dir": r"C:\data"}, "terrain": {}}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(WT_AUT_recorded, "log_step", lambda m: None))
            stack.enter_context(patch.object(mup_data_files, "snapshot", return_value={}))
            stack.enter_context(patch.object(mup_data_files, "diff", return_value=diff_result))
            WT_AUT_recorded._attach_mup_data_diff(report, context)
        return report

    def test_pure_delete_is_recorded(self):
        report = self._run_attach({"new": {}, "changed": {}, "deleted": {"terrain": ["old.tif"]}, "newCount": 0, "changedCount": 0, "deletedCount": 1})
        self.assertIn("mupDataFiles", report, "纯删除场景也应写入铁证字段")

    def test_exception_records_error_field(self):
        report = {}
        context = {"mupDataSnapshotBefore": {"_meta": {"data_dir": r"C:\data"}, "terrain": {}}}
        with ExitStack() as stack:
            stack.enter_context(patch.object(WT_AUT_recorded, "log_step", lambda m: None))
            stack.enter_context(patch.object(mup_data_files, "snapshot", side_effect=OSError("locked")))
            WT_AUT_recorded._attach_mup_data_diff(report, context)
        self.assertIn("mupDataDiffError", report)


# ---------------------------------------------------------------------------
# P1-4 投影后置校验
# ---------------------------------------------------------------------------

class ProjectionVerifyTests(unittest.TestCase):
    def test_target_prefers_action_config_value(self):
        step = {
            "name": "选择投影坐标系",
            "actionConfig": {"action": "select", "value": "CGCS2000 / 3-degree Gauss-Kruger zone 41"},
            "controls": [{"name": "投影下拉框"}],
        }
        self.assertEqual(
            wt_flow_executor._get_projection_verify_target(step),
            "CGCS2000 / 3-degree Gauss-Kruger zone 41",
        )

    def test_control_name_without_projection_keyword_is_rejected(self):
        step = {
            "name": "选择投影坐标系",
            "controls": [{"name": "下拉框列表"}],
        }
        self.assertIsNone(wt_flow_executor._get_projection_verify_target(step))

    def test_non_select_step_returns_none(self):
        step = {"name": "键入查找投影坐标系", "actionConfig": {"value": "X"}}
        self.assertIsNone(wt_flow_executor._get_projection_verify_target(step))

    def test_verify_records_error_on_resolve_failure(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_executor, "_LOG_STEP", lambda m: None))
            import mup_user_config
            stack.enter_context(patch.object(mup_user_config, "resolve_projection", side_effect=RuntimeError("boom")))
            result = wt_flow_executor._verify_projection_choice("WGS 84")
        self.assertIn("projectionVerifyError", result)


# ---------------------------------------------------------------------------
# P1-2 / P1-5 / P1-7 定位器单点
# ---------------------------------------------------------------------------

class LocatorSinglePointTests(unittest.TestCase):
    def test_escape_send_keys_text(self):
        self.assertEqual(
            wt_flow_locator._escape_send_keys_text("C++ 100% {a} ~b"),
            "C{+}{+} 100{%} {{}a{}} {~}b",
        )

    def test_uipi_latch_ttl_expiry(self):
        wt_flow_locator._UIPI_BLOCK_DETECTED = {"timestamp": 100.0, "diagnostic": {"reason": "x"}}
        with patch.object(wt_flow_locator.time, "time", return_value=200.0):
            self.assertIsNone(wt_flow_locator._uipi_block_active(0.0), "超出 TTL 的锁存应失效")

    def test_uipi_latch_ignores_old_marker(self):
        wt_flow_locator._UIPI_BLOCK_DETECTED = {"timestamp": 100.0, "diagnostic": {"reason": "x"}}
        with patch.object(wt_flow_locator.time, "time", return_value=105.0):
            self.assertIsNone(wt_flow_locator._uipi_block_active(101.0), "早于 marker 的锁存不应触发短路")

    def test_is_wrapper_alive_checks_window(self):
        wrapper = MagicMock()
        wrapper.element_info.handle = 1234
        with patch("wt_flow_locator.ctypes.windll.user32.IsWindow", return_value=0) as mock_is_window:
            self.assertFalse(wt_flow_locator.is_wrapper_alive(wrapper))
            mock_is_window.assert_called_with(1234)

    def test_is_wrapper_alive_valid_handle(self):
        wrapper = MagicMock()
        wrapper.element_info.handle = 1234
        with patch("wt_flow_locator.ctypes.windll.user32.IsWindow", return_value=1):
            self.assertTrue(wt_flow_locator.is_wrapper_alive(wrapper))

    def test_release_com_pointer_safe(self):
        wt_flow_locator._release_com_pointer(None)
        wt_flow_locator._release_com_pointer(0)


# ---------------------------------------------------------------------------
# P1-7 执行器单点
# ---------------------------------------------------------------------------

class ExecutorSinglePointTests(unittest.TestCase):
    def test_resolve_step_policy_does_not_mutate_cached_config(self):
        cached = {
            "action": "click",
            "onError": "retry",
            "retryCount": 2,
            "stepPolicy": {"onFail": "stop", "maxRetries": 5, "continueWhen": {"controlId": "x"}},
        }
        original = dict(cached)
        resolved = wt_flow_executor._resolve_step_policy(cached)
        self.assertEqual(cached, original, "归一化不得写穿 lru 缓存的流程定义")
        self.assertEqual(resolved["onError"], "stop")
        self.assertEqual(resolved["retryCount"], 5)
        self.assertIn("continueWhen", resolved)

    def test_is_setup_step_honors_metadata(self):
        with patch.object(wt_flow_executor, "_GET_STEP_DEFINITION", side_effect=lambda sid: {"setup": True} if sid == "custom_setup" else {}):
            self.assertTrue(wt_flow_executor.is_setup_step("custom_setup"))
            self.assertFalse(wt_flow_executor.is_setup_step("ordinary"))

    def test_unbound_script_step_is_failed_not_skipped(self):
        reported = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_executor, "_GET_STEP_DEFINITION", return_value={
                "id": "step_x", "name": "关键步骤", "enabled": True, "actionType": "script",
            }))
            stack.enter_context(patch.object(wt_flow_executor, "_REPORT_STEP_RESULT", side_effect=lambda *a, **k: reported.append(a)))
            wt_flow_executor.execute_step_by_id("step_x", {"step_x": {"id": "step_x"}}, {"run_report": {"stepResults": []}})
        self.assertTrue(reported, "未绑定执行函数的关键步骤必须被上报")
        # _REPORT_STEP_RESULT(run_report, step_id, step_name, status, ...)：status 是位置参数
        self.assertEqual(reported[-1][3], "failed")

    def test_step_policy_continue_when_reaches_run_action_step(self):
        """一致性：_resolve_step_policy 改为深拷贝归一化后，run_action_step 重新读取
        缓存 action_config 也必须能看到 stepPolicy 提供的 continueWhen（动作后置校验）。"""
        wait_calls = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_executor, "_GET_STEP_DEFINITION", return_value={
                "id": "step_p", "name": "动作", "enabled": True, "actionType": "action",
                "actionConfig": {
                    "action": "click", "controlId": "btn",
                    "stepPolicy": {"onFail": "stop", "maxRetries": 1,
                                   "continueWhen": {"controlId": "result", "condition": "visible", "timeoutSeconds": 0.2}},
                },
            }))
            stack.enter_context(patch.object(wt_flow_executor, "_RESOLVE_DYNAMIC_VALUE", side_effect=lambda value, step_id, ctx: value))
            stack.enter_context(patch.object(wt_flow_executor, "_CLICK_FLOW_CONTROL", return_value=True))
            stack.enter_context(patch.object(wt_flow_executor, "_WAIT_FOR_FLOW_CONTROL_CONDITION", side_effect=lambda *a, **k: wait_calls.append(k) or False))
            stack.enter_context(patch.object(wt_flow_executor, "_REPORT_STEP_RESULT", lambda *a, **k: None))
            stack.enter_context(patch.object(wt_flow_executor, "_LOG_STEP", lambda m: None))
            with self.assertRaises(RuntimeError):
                wt_flow_executor.execute_step_by_id("step_p", {"step_p": {"id": "step_p"}}, {"run_report": {"stepResults": []}})
        self.assertTrue(wait_calls, "stepPolicy 的 continueWhen 必须被 run_action_step 消费")
        self.assertEqual(wait_calls[0].get("control_id"), "result")

    def test_executed_step_dedup(self):
        calls = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_executor, "_GET_STEP_DEFINITION", return_value={
                "id": "step_once", "name": "执行", "enabled": True, "actionType": "script",
            }))
            stack.enter_context(patch.object(wt_flow_executor, "_REPORT_STEP_RESULT", lambda *a, **k: None))
            plan = {"step_once": {"id": "step_once", "func": lambda ctx: calls.append(1)}}
            context = {"run_report": {"stepResults": []}}
            wt_flow_executor.execute_step_by_id("step_once", plan, context)
            wt_flow_executor.execute_step_by_id("step_once", plan, context)  # 第二次应跳过
        self.assertEqual(len(calls), 1, "同一步骤同一轮运行只允许执行一次")


if __name__ == "__main__":
    unittest.main()