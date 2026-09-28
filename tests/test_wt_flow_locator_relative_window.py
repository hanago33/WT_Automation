import os
import sys
import unittest
from unittest.mock import patch


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator


class RelativeRegionWindowResolutionTests(unittest.TestCase):
    def _make_wrapper(self, title, class_name, framework_id, process_id, rect):
        return {
            "title": title,
            "className": class_name,
            "frameworkId": framework_id,
            "processId": process_id,
            "rect": rect,
        }

    def test_keep_explicit_titled_window_as_anchor(self):
        original_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "27920",
            {"left": 115, "top": 25, "right": 2444, "bottom": 1490, "width": 2329, "height": 1465},
        )
        focused_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "27920",
            {"left": 128, "top": 76, "right": 2432, "bottom": 1440, "width": 2304, "height": 1364},
        )
        parent_window = {"title": "导入时间序列文件", "className": "Window", "frameworkId": "WPF"}

        with patch.object(wt_flow_locator, "get_wrapper_text", side_effect=lambda wrapper: wrapper["title"]), patch.object(
            wt_flow_locator, "get_wrapper_class_name", side_effect=lambda wrapper: wrapper["className"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_framework_id", side_effect=lambda wrapper: wrapper["frameworkId"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_process_id", side_effect=lambda wrapper: wrapper["processId"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_rectangle", side_effect=lambda wrapper: wrapper["rect"]
        ), patch.object(
            wt_flow_locator, "get_foreground_window_handle", return_value=1
        ), patch.object(
            wt_flow_locator, "_try_get_window_by_handle", return_value=focused_window
        ):
            resolved_window = wt_flow_locator.resolve_effective_relative_region_window(
                original_window,
                parent_window,
            )

        self.assertIs(resolved_window, original_window)

    def test_upgrade_generic_window_to_focused_titled_window(self):
        original_window = self._make_wrapper(
            "",
            "Window",
            "WPF",
            "27920",
            {"left": 0, "top": 0, "right": 2560, "bottom": 1516, "width": 2560, "height": 1516},
        )
        focused_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "27920",
            {"left": 115, "top": 25, "right": 2444, "bottom": 1490, "width": 2329, "height": 1465},
        )
        parent_window = {"title": "导入时间序列文件", "className": "Window", "frameworkId": "WPF"}

        with patch.object(wt_flow_locator, "get_wrapper_text", side_effect=lambda wrapper: wrapper["title"]), patch.object(
            wt_flow_locator, "get_wrapper_class_name", side_effect=lambda wrapper: wrapper["className"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_framework_id", side_effect=lambda wrapper: wrapper["frameworkId"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_process_id", side_effect=lambda wrapper: wrapper["processId"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_rectangle", side_effect=lambda wrapper: wrapper["rect"]
        ), patch.object(
            wt_flow_locator, "get_foreground_window_handle", return_value=1
        ), patch.object(
            wt_flow_locator, "_try_get_window_by_handle", return_value=focused_window
        ):
            resolved_window = wt_flow_locator.resolve_effective_relative_region_window(
                original_window,
                parent_window,
            )

        self.assertIs(resolved_window, focused_window)

    def test_replace_same_score_with_larger_explicit_wpf_window(self):
        smaller_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "36800",
            {"left": 128, "top": 76, "right": 2432, "bottom": 1440, "width": 2304, "height": 1364},
        )
        larger_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "36800",
            {"left": 115, "top": 25, "right": 2444, "bottom": 1490, "width": 2329, "height": 1465},
        )
        window_spec = {"title": "导入时间序列文件", "className": "Window", "frameworkId": "WPF"}

        with patch.object(wt_flow_locator, "get_wrapper_text", side_effect=lambda wrapper: wrapper["title"]), patch.object(
            wt_flow_locator, "get_wrapper_handle_text", side_effect=lambda wrapper: "0x1" if wrapper is smaller_window else "0x2"
        ), patch.object(
            wt_flow_locator, "get_wrapper_rectangle", side_effect=lambda wrapper: wrapper["rect"]
        ):
            should_replace = wt_flow_locator.should_replace_flow_window_candidate(
                larger_window,
                39,
                smaller_window,
                39,
                window_spec,
            )

        self.assertTrue(should_replace)

    def test_keep_same_score_when_larger_window_does_not_contain_current(self):
        current_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "36800",
            {"left": 128, "top": 76, "right": 2432, "bottom": 1440, "width": 2304, "height": 1364},
        )
        shifted_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "36800",
            {"left": 60, "top": 60, "right": 2500, "bottom": 1400, "width": 2440, "height": 1340},
        )
        window_spec = {"title": "导入时间序列文件", "className": "Window", "frameworkId": "WPF"}

        with patch.object(wt_flow_locator, "get_wrapper_text", side_effect=lambda wrapper: wrapper["title"]), patch.object(
            wt_flow_locator, "get_wrapper_handle_text", side_effect=lambda wrapper: "0x2" if wrapper is shifted_window else "0x1"
        ), patch.object(
            wt_flow_locator, "get_wrapper_rectangle", side_effect=lambda wrapper: wrapper["rect"]
        ):
            should_replace = wt_flow_locator.should_replace_flow_window_candidate(
                shifted_window,
                39,
                current_window,
                39,
                window_spec,
            )

        self.assertFalse(should_replace)



    def test_relative_region_prefers_explicit_window_rect_over_reference(self):
        runtime_rect = {"left": 128, "top": 76, "right": 2432, "bottom": 1440, "width": 2304, "height": 1364}
        reference_rect = {"left": 115, "top": 25, "right": 2444, "bottom": 1490, "width": 2329, "height": 1465}
        relative_region = {
            "x": 0.3033,
            "y": 0.1812,
            "width": 0.0743,
            "height": 0.028,
            "anchor": "center",
            "referenceWindowRect": reference_rect,
        }

        absolute_rect = wt_flow_locator.resolve_relative_region_absolute_rect(
            None,
            relative_region,
            window_rect=runtime_rect,
        )

        # 运行时矩形（含调用方显式传入）始终优先于录制快照：快照只作为拿不到运行时矩形时的兜底。
        self.assertEqual(absolute_rect["windowRect"], runtime_rect)
        self.assertEqual(absolute_rect["windowRectSource"], "runtime")
        self.assertEqual(wt_flow_locator.resolve_relative_region_anchor_point(absolute_rect), (911, 342))

    def test_relative_region_keeps_runtime_rect_without_reference(self):
        runtime_rect = {"left": 128, "top": 76, "right": 2432, "bottom": 1440, "width": 2304, "height": 1364}
        relative_region = {"x": 0.3033, "y": 0.1812, "width": 0.0743, "height": 0.028, "anchor": "center"}

        absolute_rect = wt_flow_locator.resolve_relative_region_absolute_rect(
            None,
            relative_region,
            window_rect=runtime_rect,
        )

        self.assertEqual(absolute_rect["windowRect"], runtime_rect)
        self.assertEqual(absolute_rect["windowRectSource"], "runtime")
        self.assertEqual(wt_flow_locator.resolve_relative_region_anchor_point(absolute_rect), (911, 342))

    def test_relative_region_trusts_runtime_rect_even_when_base(self):
        runtime_rect = {"left": 128, "top": 76, "right": 2432, "bottom": 1440, "width": 2304, "height": 1364}
        reference_rect = {"left": 115, "top": 25, "right": 2444, "bottom": 1490, "width": 2329, "height": 1465}
        relative_region = {
            "x": 0.3033,
            "y": 0.1812,
            "width": 0.0743,
            "height": 0.028,
            "anchor": "center",
            "referenceWindowRect": reference_rect,
        }

        with patch.object(
            wt_flow_locator,
            "_resolve_wrapper_rectangle",
            return_value=(runtime_rect, {"selectedSource": "base"}),
        ):
            absolute_rect = wt_flow_locator.resolve_relative_region_absolute_rect("dummy", relative_region)

        # 关键：_resolve_wrapper_rectangle 内部优先走 GetWindowRect(handle)，返回完整外框。
        # 即使 selectedSource 为 base（未触发帧升级），该矩形仍是窗口当前真实外框，必须信任，
        # 否则就会因快照与运行时窗口位置不一致而整体偏移（step_16~27 此前的故障根因）。
        self.assertEqual(absolute_rect["windowRect"], runtime_rect)
        self.assertEqual(absolute_rect["windowRectSource"], "runtime")
        self.assertEqual(wt_flow_locator.resolve_relative_region_anchor_point(absolute_rect), (911, 342))

    def test_relative_region_falls_back_to_reference_when_runtime_missing(self):
        reference_rect = {"left": 115, "top": 25, "right": 2444, "bottom": 1490, "width": 2329, "height": 1465}
        relative_region = {
            "x": 0.3033,
            "y": 0.1812,
            "width": 0.0743,
            "height": 0.028,
            "anchor": "center",
            "referenceWindowRect": reference_rect,
        }

        with patch.object(
            wt_flow_locator,
            "_resolve_wrapper_rectangle",
            return_value=(None, None),
        ):
            absolute_rect = wt_flow_locator.resolve_relative_region_absolute_rect("dummy", relative_region)

        # 运行时完全拿不到窗口矩形时，才退回收录基准作为最后兜底。
        self.assertEqual(absolute_rect["windowRect"], reference_rect)
        self.assertEqual(absolute_rect["windowRectSource"], "referenceWindowRect")
        self.assertEqual(wt_flow_locator.resolve_relative_region_anchor_point(absolute_rect), (907, 310))
class FindFlowWindowForRelativeRegionGateTests(unittest.TestCase):
    """标题门控轮询：空标题/不匹配候选不得立即返回或阻塞枚举；

    回归背景：step_16（导入时间序列文件）窗口加载慢时，前台 MUP 主窗口
    （空标题 WPF）被空标题 fallback 误判为匹配并立即返回，相对区域点击
    落到错误窗口左上角。
    """

    EXPECTED_TITLE = "导入时间序列文件"
    PARENT_WINDOW = {"title": "导入时间序列文件", "className": "Window", "frameworkId": "WPF"}

    def _make_wrapper(self, title, class_name="Window", framework_id="WPF"):
        return {"title": title, "className": class_name, "frameworkId": framework_id}

    def _run_find(self, foreground_wrapper, enumerated_windows, parent_window, timeout_seconds=0.3):
        logs = []
        # sleep 置空：轮询循环用真实 time.time() 判定 deadline，测试不会久等
        with patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=123), patch.object(
            wt_flow_locator, "_try_get_window_by_handle", return_value=foreground_wrapper
        ), patch.object(
            wt_flow_locator, "iter_flow_search_windows", return_value=list(enumerated_windows or [])
        ), patch.object(
            wt_flow_locator, "get_wrapper_text", side_effect=lambda wrapper: wrapper["title"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_class_name", side_effect=lambda wrapper: wrapper["className"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_framework_id", side_effect=lambda wrapper: wrapper["frameworkId"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_handle_text", return_value=""
        ), patch.object(
            wt_flow_locator, "_LOG_STEP", side_effect=logs.append
        ), patch.object(
            wt_flow_locator.time, "sleep", lambda *_args, **_kwargs: None
        ):
            result = wt_flow_locator.find_flow_window_for_relative_region(
                step_definition={"id": "unit_gate"},
                parent_window=parent_window,
                timeout_seconds=timeout_seconds,
            )
        return result, logs

    def test_empty_title_foreground_does_not_block_titled_window(self):
        # 前台是空标题 MUP 主窗口（fallback 得 8 分），枚举里已有标题匹配窗口（36 分）
        foreground = self._make_wrapper("")
        target = self._make_wrapper(self.EXPECTED_TITLE)

        result, _logs = self._run_find(foreground, [target], self.PARENT_WINDOW)

        self.assertIs(result, target)

    def test_explicit_title_timeout_returns_none_instead_of_empty_title_fallback(self):
        # 目标窗口始终未出现：超时后必须干净失败，不得回退空标题主窗口去点击
        foreground = self._make_wrapper("")

        result, logs = self._run_find(foreground, [], self.PARENT_WINDOW, timeout_seconds=0.2)

        self.assertIsNone(result)
        self.assertTrue(any("继续等待" in message for message in logs))
        self.assertTrue(any("放弃点击" in message for message in logs))
        self.assertFalse(any("回退空标题候选" in message for message in logs))

    def test_foreign_titled_foreground_does_not_skip_enumeration(self):
        # 前台是无关的有标题窗口（score=-1）：只跳过该候选，不得跳过整轮枚举
        foreign = self._make_wrapper("其他应用", "Chrome_WidgetWin_1", "Win32")
        target = self._make_wrapper(self.EXPECTED_TITLE)

        result, _logs = self._run_find(foreign, [target], self.PARENT_WINDOW)

        self.assertIs(result, target)

    def test_titleless_spec_keeps_legacy_fallback(self):
        # 无标题规格时保持旧行为：前台空标题窗口仍可作为兜底候选立即返回
        foreground = self._make_wrapper("")

        result, _logs = self._run_find(foreground, [], {}, timeout_seconds=0.2)

        self.assertIs(result, foreground)


class PerformRelativeRegionClickFailsafeTests(unittest.TestCase):
    """相对区域点击的 pyautogui 失效保护自恢复。

    回归背景：鼠标停在屏幕角落时 pyautogui 任何调用立即抛 FailSafeException
    （点击不执行），click_relative_region 的 except 分支对非 debug 步骤静默
    吞异常，步骤秒级失败报"未命中父窗口相对区域"且无任何点击日志。
    """

    def test_failsafe_exception_recovers_and_retries(self):
        class FailSafeException(Exception):
            pass

        with patch.object(
            wt_flow_locator.pyautogui, "click", side_effect=[FailSafeException("corner"), None]
        ) as mock_click, patch.object(
            wt_flow_locator, "_move_cursor_to_screen_center", return_value=(960, 540)
        ), patch.object(
            wt_flow_locator, "_LOG_STEP"
        ):
            wt_flow_locator._perform_relative_region_click((100, 100), "single")

        self.assertEqual(mock_click.call_count, 2)

    def test_other_exception_propagates_without_retry(self):
        class OtherError(Exception):
            pass

        with patch.object(
            wt_flow_locator.pyautogui, "click", side_effect=OtherError("boom")
        ) as mock_click, patch.object(
            wt_flow_locator, "_LOG_STEP"
        ):
            with self.assertRaises(OtherError):
                wt_flow_locator._perform_relative_region_click((100, 100), "single")

        self.assertEqual(mock_click.call_count, 1)

    def test_recover_returns_false_for_non_failsafe(self):
        self.assertFalse(wt_flow_locator._recover_pyautogui_failsafe(ValueError("x")))


if __name__ == "__main__":
    unittest.main()
