# encoding: utf-8
"""P0 修复针对性测试：防错位与防假成功。

覆盖：
- P0-1   MUP 窗口回退死代码：_try_get_window_by_handle 支持 "0x…" 十六进制句柄
- P0-2   键盘导航防错位：全来源显示值校验 + 相对导航（重跑不错位）
- P0-3   Toggle 闸门修正：_dropdown_currently_expanded 三态 + 候选可见矩形判定
- P0-4a  空步骤列表/丢弃步骤名告警（_resolve_steps_to_run）
- P0-4c  runId 毫秒唯一 + 报告原子写 + 无 summary 不崩溃
- P0-4b  AI 介入无 continueWhen → 未验证失败
- P0-5   fallback 链成功后未满足 continueWhen → 尝试下一级/返回 None
"""
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator
import wt_run_reporting
import wt_flow_executor
import WT_AUT_recorded


class TryGetWindowByHandleTests(unittest.TestCase):
    """P0-1：十六进制字符串句柄必须能被解析，MUP 窗口回退分支不再是死代码。"""

    def _patch_desktop(self):
        calls = []
        desktop_mock = MagicMock()
        desktop_mock.window.side_effect = lambda handle=None, **kw: calls.append(handle) or desktop_mock
        stack = ExitStack()
        stack.enter_context(patch.object(wt_flow_locator, "Desktop", return_value=desktop_mock))
        return stack, calls

    def test_hex_string_handle_is_parsed(self):
        stack, calls = self._patch_desktop()
        with stack:
            wt_flow_locator._try_get_window_by_handle("0x1a0c")
        self.assertEqual(calls, [6668])

    def test_int_handle_passes_through(self):
        stack, calls = self._patch_desktop()
        with stack:
            wt_flow_locator._try_get_window_by_handle(6668)
        self.assertEqual(calls, [6668])

    def test_decimal_string_handle_is_parsed(self):
        stack, calls = self._patch_desktop()
        with stack:
            wt_flow_locator._try_get_window_by_handle("6668")
        self.assertEqual(calls, [6668])

    def test_empty_handle_returns_none(self):
        self.assertIsNone(wt_flow_locator._try_get_window_by_handle(""))
        self.assertIsNone(wt_flow_locator._try_get_window_by_handle(None))


class DropdownExpandedStateTests(unittest.TestCase):
    """P0-3：ToggleState 不可读时回退 ExpandCollapsePattern，未知返回 None。"""

    def _wrapper(self, toggle_result="", expand_value=None, expand_raises=False):
        wrapper = MagicMock()
        element = MagicMock()
        if expand_raises:
            element.CurrentExpandCollapseState = MagicMock(side_effect=RuntimeError("no pattern"))
        else:
            element.CurrentExpandCollapseState = expand_value
        wrapper.element_info.element = element
        with patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value=toggle_result):
            return wt_flow_locator._dropdown_currently_expanded(wrapper)

    def test_toggle_on_returns_true(self):
        self.assertIs(self._wrapper(toggle_result="1"), True)
        self.assertIs(self._wrapper(toggle_result="On"), True)

    def test_toggle_off_returns_false(self):
        self.assertIs(self._wrapper(toggle_result="0"), False)
        self.assertIs(self._wrapper(toggle_result="Off"), False)

    def test_expand_pattern_expanded_when_toggle_unreadable(self):
        self.assertIs(self._wrapper(toggle_result="", expand_value="ExpandCollapseState.Expanded"), True)

    def test_expand_pattern_collapsed_when_toggle_unreadable(self):
        self.assertIs(self._wrapper(toggle_result="", expand_value="ExpandCollapseState.Collapsed"), False)

    def test_both_unreadable_returns_none(self):
        self.assertIsNone(self._wrapper(toggle_result="", expand_raises=True))

    def test_none_wrapper_returns_none(self):
        self.assertIsNone(wt_flow_locator._dropdown_currently_expanded(None))


class CandidateVisibleRectTests(unittest.TestCase):
    """P0-3：无可见矩形（离屏/未渲染）的候选不可点击。"""

    def _has_rect(self, width, height):
        wrapper = MagicMock()
        with patch.object(wt_flow_locator, "get_wrapper_rectangle", return_value={"width": width, "height": height}):
            return wt_flow_locator._candidate_has_visible_rect(wrapper)

    def test_visible_rect_true(self):
        self.assertTrue(self._has_rect(100, 20))

    def test_zero_rect_false(self):
        self.assertFalse(self._has_rect(0, 0))

    def test_negative_rect_false(self):
        self.assertFalse(self._has_rect(-5, 20))

    def test_none_wrapper_false(self):
        self.assertFalse(wt_flow_locator._candidate_has_visible_rect(None))


class DropdownNavDeltaTests(unittest.TestCase):
    """P0-2：相对导航 delta 计算（重跑不错位）。"""

    OPTIONS = ["Item1", "Item2", "Item3", "Item4", "Item5"]

    def test_forward_delta_from_current(self):
        self.assertEqual(wt_flow_locator._dropdown_nav_delta(self.OPTIONS, "Item2", 3), (2, False))

    def test_backward_delta_from_current(self):
        self.assertEqual(wt_flow_locator._dropdown_nav_delta(self.OPTIONS, "Item4", 1), (-2, False))

    def test_no_movement_when_current_is_target(self):
        self.assertEqual(wt_flow_locator._dropdown_nav_delta(self.OPTIONS, "Item3", 2), (0, False))

    def test_unresolvable_current_needs_home(self):
        self.assertEqual(wt_flow_locator._dropdown_nav_delta(self.OPTIONS, "Unknown", 3), (3, True))

    def test_empty_current_needs_home(self):
        self.assertEqual(wt_flow_locator._dropdown_nav_delta(self.OPTIONS, "", 2), (2, True))

    def test_case_insensitive_match(self):
        with patch.object(wt_flow_locator, "normalize_match_text", side_effect=lambda v: str(v).lower()):
            self.assertEqual(wt_flow_locator._dropdown_nav_delta(self.OPTIONS, "item2", 3), (2, False))


class DropdownKeyboardNavigationTests(unittest.TestCase):
    """P0-2：select_dropdown_item_runtime 键盘导航路径（集成级，相对导航 + 全来源校验）。"""

    OPTIONS = ["Item1", "Item2", "Item3", "Item4", "Item5"]
    TARGET = "Item4"

    def _select(self, current_display, post_display):
        dropdown_wrapper = MagicMock()
        sent_keys = []
        display_reads = [current_display, post_display]

        def read_display(_wrapper):
            return display_reads.pop(0) if display_reads else ""

        def record_keys(*args, **kwargs):
            sent_keys.append(args[0] if args else kwargs.get("keys", ""))

        time_state = {"n": 0}

        def fake_time():
            # 前若干次返回 0.0 让循环进入并执行展开逻辑，之后返回大值让循环退出
            time_state["n"] += 1
            return 0.0 if time_state["n"] <= 8 else 10.0

        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_GET_STEP_DEFINITION", return_value={"id": "s1"}))
            stack.enter_context(patch.object(wt_flow_locator, "get_flow_control_definition", return_value={
                "id": "dd", "name": "RoughnessIndex", "inspectData": {"optionValues": list(self.OPTIONS)},
            }))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_target_texts", return_value=[self.TARGET]))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_expected_window_titles", return_value=["Window"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=123))
            stack.enter_context(patch.object(wt_flow_locator, "_try_get_window_by_handle", return_value=MagicMock()))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=0))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "_collect_dropdown_windows", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "find_flow_control", return_value=dropdown_wrapper))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_value", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value="1"))
            stack.enter_context(patch.object(wt_flow_locator, "click_wrapper_center", return_value=(False, {})))
            stack.enter_context(patch.object(wt_flow_locator, "iter_dropdown_runtime_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_raw_view_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "score_dropdown_runtime_candidate", return_value=90))
            stack.enter_context(patch.object(wt_flow_locator, "click_dropdown_runtime_candidate", return_value=(True, {"method": "click_input"})))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_debug_snapshot", return_value={}))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_class_name", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "_read_dropdown_display_text", side_effect=read_display))
            stack.enter_context(patch.object(wt_flow_locator, "_dropdown_currently_expanded", return_value=True))
            stack.enter_context(patch.object(wt_flow_locator, "normalize_match_text", side_effect=lambda v: str(v).lower()))
            stack.enter_context(patch.object(wt_flow_locator, "is_placeholder_text", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator, "send_keys", side_effect=record_keys))
            stack.enter_context(patch.object(wt_flow_locator.time, "sleep", side_effect=lambda *a, **k: None))
            stack.enter_context(patch.object(wt_flow_locator.time, "time", side_effect=fake_time))
            return wt_flow_locator.select_dropdown_item_runtime(
                "s1", "dd", timeout_seconds=0.2, target_option=""
            ), sent_keys

    def test_keyboard_nav_uses_relative_delta_and_verifies_value(self):
        # 当前值 Item2(idx1)、目标 Item4(idx3)：相对导航 DOWN×2 + ENTER，且所有来源都做显示值校验
        (ok, meta), sent_keys = self._select(current_display="Item2", post_display="Item4")
        self.assertTrue(ok)
        self.assertEqual(meta.get("method"), "keyboard_navigate")
        self.assertEqual(meta.get("valueVerified"), "Item4")
        self.assertEqual(sent_keys, ["{DOWN}", "{DOWN}", "{ENTER}"])

    def test_keyboard_nav_fails_when_display_not_verified(self):
        # 显示值读不到（不可验证）→ 不判定成功（无搜索文本时最终整体失败）
        (ok, _), _ = self._select(current_display="Item2", post_display="")
        self.assertIs(ok, False)

    def test_keyboard_nav_not_sent_when_dropdown_not_located(self):
        # 下拉框本体未定位：禁止盲发按键（nav_ready=False），整体失败
        dropdown_wrapper = MagicMock()
        sent_keys = []
        time_state = {"n": 0}

        def fake_time_fast():
            time_state["n"] += 1
            return 0.0 if time_state["n"] <= 8 else 100.0

        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_GET_STEP_DEFINITION", return_value={"id": "s1"}))
            stack.enter_context(patch.object(wt_flow_locator, "get_flow_control_definition", return_value={
                "id": "dd", "name": "RoughnessIndex", "inspectData": {"optionValues": list(self.OPTIONS)},
            }))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_target_texts", return_value=[self.TARGET]))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_expected_window_titles", return_value=["Window"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=123))
            stack.enter_context(patch.object(wt_flow_locator, "_try_get_window_by_handle", return_value=MagicMock()))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=0))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "_collect_dropdown_windows", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "find_flow_control", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator, "iter_dropdown_runtime_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_raw_view_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "normalize_match_text", side_effect=lambda v: str(v).lower()))
            stack.enter_context(patch.object(wt_flow_locator, "is_placeholder_text", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator, "send_keys", side_effect=lambda *a, **k: sent_keys.append(a[0])))
            stack.enter_context(patch.object(wt_flow_locator.time, "sleep", side_effect=lambda *a, **k: None))
            stack.enter_context(patch.object(wt_flow_locator.time, "time", side_effect=fake_time_fast))
            ok, _ = wt_flow_locator.select_dropdown_item_runtime(
                "s1", "dd", timeout_seconds=0.2, target_option=""
            )
        self.assertIs(ok, False)
        self.assertEqual(sent_keys, [])


class ResolveStepsToRunTests(unittest.TestCase):
    """P0-4a：无效步骤名告警 + 空列表可识别。"""

    STEP_IDS = ["step_a", "step_b", "step_c"]

    def _resolve(self, steps_arg):
        logged = []
        with patch.object(WT_AUT_recorded, "log_step", side_effect=lambda msg: logged.append(msg)):
            result = WT_AUT_recorded._resolve_steps_to_run(
                self.STEP_IDS, steps_arg=steps_arg
            )
        return result, logged

    def test_valid_steps_kept_and_logged_dropped(self):
        result, logged = self._resolve("step_a,no_such_step,step_b")
        self.assertEqual(result, ["step_a", "step_b"])
        self.assertTrue(any("no_such_step" in line for line in logged))
        self.assertTrue(any("已忽略" in line for line in logged))

    def test_all_invalid_returns_empty(self):
        result, logged = self._resolve("totally_wrong")
        self.assertEqual(result, [])
        self.assertTrue(any("未匹配" in line for line in logged))


class RunReportingFixTests(unittest.TestCase):
    """P0-4c：runId 毫秒唯一 + 原子写 + 无 summary 不崩溃。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        wt_run_reporting.configure_run_reporting(base_dir=self._tmp.name, log_step=lambda message: None)

    def test_run_id_unique_within_same_second(self):
        ids = {wt_run_reporting.start_run_report([], {})["runId"] for _ in range(20)}
        self.assertEqual(len(ids), 20)

    def test_finalize_writes_valid_json_atomically(self):
        report = wt_run_reporting.start_run_report(["step_a"], {})
        wt_run_reporting.report_step_result(report, "step_a", "A", "success", elapsed=0.5)
        path = wt_run_reporting.finalize_run_report(report, "success")
        self.assertTrue(os.path.exists(path))
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        self.assertEqual(payload["status"], "success")

    def test_finalize_without_summary_does_not_crash(self):
        report = {"runId": "wt_run_demo"}
        path = wt_run_reporting.finalize_run_report(report, "success")
        self.assertTrue(os.path.exists(path))


class ExecutorP0FixTests(unittest.TestCase):
    """P0-4b/P0-5：AI 未验证失败 + fallback 链 continueWhen。"""

    def test_fallback_chain_requires_continue_when(self):
        step_def = {
            "id": "s",
            "actionConfig": {
                "action": "click",
                "onError": "fallback",
                "continueWhen": {"controlId": "x", "condition": "visible", "timeoutSeconds": 0.2},
            },
            "fallbackChain": [{"method": "coords", "type": "coordinate", "value": {"x": 10, "y": 20}}],
        }
        logs = []
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_executor, "_GET_STEP_DEFINITION", return_value=step_def))
            stack.enter_context(patch.object(wt_flow_executor, "_RESOLVE_DYNAMIC_VALUE", side_effect=lambda value, step_id, context: value))
            stack.enter_context(patch.object(wt_flow_executor, "_execute_fallback_action", return_value={}))
            stack.enter_context(patch.object(wt_flow_executor, "_wait_for_continue_when", side_effect=RuntimeError("未满足")))
            stack.enter_context(patch.object(wt_flow_executor, "apply_position_offset", side_effect=lambda center, cfg: center))
            stack.enter_context(patch.object(wt_flow_executor.time, "sleep", side_effect=lambda *a, **k: None))
            stack.enter_context(patch.object(wt_flow_executor, "_LOG_STEP", side_effect=lambda msg: logs.append(msg)))
            result = wt_flow_executor._try_fallback_chain("s", {}, RuntimeError("orig"))
        self.assertIsNone(result)
        self.assertTrue(any("续跑条件未满足" in line for line in logs))

    def test_fallback_chain_without_continue_when_succeeds(self):
        step_def = {
            "id": "s",
            "actionConfig": {"action": "click", "onError": "fallback"},
            "fallbackChain": [{"method": "coords", "type": "coordinate", "value": {"x": 10, "y": 20}}],
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_executor, "_GET_STEP_DEFINITION", return_value=step_def))
            stack.enter_context(patch.object(wt_flow_executor, "_RESOLVE_DYNAMIC_VALUE", side_effect=lambda value, step_id, context: value))
            stack.enter_context(patch.object(wt_flow_executor, "_execute_fallback_action", return_value={}))
            stack.enter_context(patch.object(wt_flow_executor, "apply_position_offset", side_effect=lambda center, cfg: center))
            stack.enter_context(patch.object(wt_flow_executor.time, "sleep", side_effect=lambda *a, **k: None))
            stack.enter_context(patch.object(wt_flow_executor, "_LOG_STEP", lambda msg: None))
            result = wt_flow_executor._try_fallback_chain("s", {}, RuntimeError("orig"))
        self.assertIsNotNone(result)
        self.assertEqual(result.get("_fallback_level"), 1)


if __name__ == "__main__":
    unittest.main()