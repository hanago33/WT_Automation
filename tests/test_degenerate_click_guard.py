# encoding: utf-8
"""回归测试：点击点退化守卫 —— 从源头堵死盲点 (0,0)。

背景：控件矩形 0x0（未渲染/未实例化）时——
  ① pywinauto 的 click_input 用矩形中心（0x0 → (0,0)）派发点击，落到屏幕左上角
     却报 success（假成功，实测 step_28 保存后 HPC 按钮 rect=(0,0,0,0)）；
  ② 由矩形推导的 get_wrapper_center 返回 (0,0)，而 (0,0) 是 truthy，调用方
     `if not center` 判空失效 → pyautogui.click(0,0)。

两者都会把鼠标停在屏幕角落，而 pyautogui.FAILSAFE 为 True（见 WT_AUT_recorded.py），
其后每一次 pyautogui 调用都会在动作发出前直接抛 FailSafeException —— 症状正是
"点一下 (0,0) 之后后续所有步骤秒级失败、日志里没有点击记录"。

修复：矩形退化时一律不产生物理点击 —— 按钮类改用 UIA Invoke 程序化触发；
不支持 Invoke 则判未就绪失败；所有物理点击出口统一拒绝退化点击点。
"""
import os
import sys
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_executor
import wt_flow_locator


def _rect(left, top, right, bottom):
    """构造 pywinauto RECT 形状的对象（只需 left/top/right/bottom 四个字段）。"""
    return SimpleNamespace(left=left, top=top, right=right, bottom=bottom)


class RectDegenerateTests(unittest.TestCase):
    def test_zero_size_rect_is_degenerate(self):
        self.assertTrue(wt_flow_locator._rect_is_degenerate(
            {"left": 0, "top": 0, "right": 0, "bottom": 0, "width": 0, "height": 0}))

    def test_zero_width_or_height_is_degenerate(self):
        self.assertTrue(wt_flow_locator._rect_is_degenerate({"width": 0, "height": 50}))
        self.assertTrue(wt_flow_locator._rect_is_degenerate({"width": 50, "height": 0}))

    def test_normal_rect_not_degenerate(self):
        self.assertFalse(wt_flow_locator._rect_is_degenerate({"width": 10, "height": 5}))

    def test_missing_or_invalid_rect_is_degenerate(self):
        self.assertTrue(wt_flow_locator._rect_is_degenerate(None))
        self.assertTrue(wt_flow_locator._rect_is_degenerate("nonsense"))


class ClickPointDegenerateTests(unittest.TestCase):
    def test_origin_is_degenerate(self):
        self.assertTrue(wt_flow_locator._click_point_is_degenerate((0, 0)))

    def test_none_is_degenerate(self):
        self.assertTrue(wt_flow_locator._click_point_is_degenerate(None))

    def test_valid_point_not_degenerate(self):
        self.assertFalse(wt_flow_locator._click_point_is_degenerate((100, 100)))
        # 单轴贴边可能是合法布局，只有同时落到左上角才视为盲点
        self.assertFalse(wt_flow_locator._click_point_is_degenerate((0, 100)))
        self.assertFalse(wt_flow_locator._click_point_is_degenerate((100, 0)))


class GetWrapperCenterTests(unittest.TestCase):
    def test_zero_size_rect_returns_none(self):
        control = MagicMock()
        control.rectangle.return_value = _rect(0, 0, 0, 0)
        self.assertIsNone(wt_flow_locator.get_wrapper_center(control))

    def test_normal_rect_returns_center(self):
        control = MagicMock()
        control.rectangle.return_value = _rect(10, 20, 30, 40)
        self.assertEqual(wt_flow_locator.get_wrapper_center(control), (20, 30))

    def test_rectangle_exception_returns_none(self):
        control = MagicMock()
        control.rectangle.side_effect = RuntimeError("gone")
        self.assertIsNone(wt_flow_locator.get_wrapper_center(control))


class PerformRelativeRegionClickGuardTests(unittest.TestCase):
    def test_zero_point_refused_without_dispatch(self):
        with patch.object(wt_flow_locator, "pyautogui") as gui:
            with self.assertRaises(ValueError):
                wt_flow_locator._perform_relative_region_click((0, 0), "single")
            gui.click.assert_not_called()

    def test_valid_point_dispatched(self):
        with patch.object(wt_flow_locator, "pyautogui") as gui:
            wt_flow_locator._perform_relative_region_click((300, 200), "single")
            gui.click.assert_called_once_with(300, 200)


class ClickWrapperCenterGuardTests(unittest.TestCase):
    def test_zero_size_wrapper_not_clicked(self):
        wrapper = MagicMock()
        wrapper.rectangle.return_value = _rect(0, 0, 0, 0)
        with patch.object(wt_flow_locator, "pyautogui") as gui:
            ok, point = wt_flow_locator.click_wrapper_center(wrapper)
        self.assertFalse(ok)
        self.assertIsNone(point)
        gui.click.assert_not_called()


class ProgrammaticInvokeGuardTests(unittest.TestCase):
    def test_invoke_method_used_when_present(self):
        control = MagicMock()
        self.assertTrue(wt_flow_locator._try_programmatic_invoke(control))
        control.invoke.assert_called_once()

    def test_patterns_invoke_fallback(self):
        control = MagicMock(spec=[])  # 无 invoke 属性 → 走 patterns.Invoke
        control.patterns = MagicMock()
        self.assertTrue(wt_flow_locator._try_programmatic_invoke(control))
        control.patterns.Invoke.Invoke.assert_called_once()

    def test_returns_false_when_invoke_unavailable(self):
        control = MagicMock(spec=[])  # 访问 patterns 抛 AttributeError
        self.assertFalse(wt_flow_locator._try_programmatic_invoke(control))

    def test_returns_false_for_none(self):
        self.assertFalse(wt_flow_locator._try_programmatic_invoke(None))


class SafeClickInputTests(unittest.TestCase):
    def test_none_control(self):
        ok, invoked, reason = wt_flow_locator._safe_click_input(None)
        self.assertFalse(ok)
        self.assertFalse(invoked)
        self.assertEqual(reason, "control-is-none")

    def test_degenerate_rect_prefers_invoke(self):
        control = MagicMock()
        with patch.object(wt_flow_locator, "get_wrapper_rectangle",
                          return_value={"width": 0, "height": 0}):
            ok, invoked, reason = wt_flow_locator._safe_click_input(control)
        self.assertTrue(ok)
        self.assertTrue(invoked)
        self.assertEqual(reason, "invoke-on-degenerate-rect")

    def test_degenerate_rect_without_invoke_refuses_click(self):
        control = MagicMock(spec=["click_input", "rectangle"])
        with patch.object(wt_flow_locator, "get_wrapper_rectangle",
                          return_value={"width": 0, "height": 0}):
            ok, invoked, reason = wt_flow_locator._safe_click_input(control)
        self.assertFalse(ok)
        self.assertFalse(invoked)
        self.assertEqual(reason, "degenerate-rect-no-invoke")
        control.click_input.assert_not_called()

    def test_valid_rect_clicks(self):
        control = MagicMock()
        with patch.object(wt_flow_locator, "get_wrapper_rectangle",
                          return_value={"width": 10, "height": 10}):
            ok, invoked, reason = wt_flow_locator._safe_click_input(control)
        self.assertTrue(ok)
        self.assertFalse(invoked)
        control.click_input.assert_called_once()

    def test_click_exception_reported(self):
        control = MagicMock()
        control.click_input.side_effect = RuntimeError("boom")
        with patch.object(wt_flow_locator, "get_wrapper_rectangle",
                          return_value={"width": 10, "height": 10}):
            ok, invoked, reason = wt_flow_locator._safe_click_input(control)
        self.assertFalse(ok)
        self.assertTrue(reason.startswith("click-exception:"))


class ClickFlowControlGuardTests(unittest.TestCase):
    """集成：0x0 且不支持 Invoke 时 click_flow_control 必须失败且不派发物理点击。"""

    def _run(self, control):
        with ExitStack() as stack:
            for name, replacement in (
                ("find_flow_control", lambda *a, **k: control),
                ("_LOG_STEP", lambda message: None),
                ("_emit_debug_event", lambda *a, **k: None),
                ("_scroll_flow_control_into_view", lambda *a, **k: None),
                ("_GET_STEP_DEFINITION", lambda step_id: {}),
                ("_toggle_fixup_desired_state", lambda step_def, control_id: None),
                ("_is_list_item_wrapper", lambda w: False),
                ("_find_list_item_ancestor", lambda w, max_depth=6: None),
                ("get_wrapper_debug_snapshot", lambda w: {}),
                ("get_flow_control_definition", lambda *a, **k: {}),
                ("get_foreground_window_handle", lambda: None),
                ("_try_get_window_by_handle", lambda handle: None),
                ("get_wrapper_control_type", lambda w: "Button"),
                ("get_wrapper_class_name", lambda w: "Button"),
                ("get_wrapper_text", lambda w: "保存"),
                ("get_wrapper_rectangle", lambda w: {"width": 0, "height": 0}),
            ):
                stack.enter_context(patch.object(wt_flow_locator, name, replacement))
            return wt_flow_locator.click_flow_control(
                "step_28", "save_button", timeout_seconds=0.1)

    def test_degenerate_rect_without_invoke_returns_false_without_click(self):
        control = MagicMock(spec=["click_input", "rectangle", "set_focus"])
        self.assertFalse(self._run(control))
        control.click_input.assert_not_called()


class CoordinateFallbackGuardTests(unittest.TestCase):
    """executor 坐标兜底出口同样拒绝退化点，避免 fallback 链点到屏幕角落。"""

    def test_executor_rejects_origin_point(self):
        with patch.object(wt_flow_executor, "pyautogui") as gui:
            wt_flow_executor._perform_coordinate_action("click", (0, 0))
            gui.click.assert_not_called()

    def test_executor_rejects_none_point(self):
        with patch.object(wt_flow_executor, "pyautogui") as gui:
            wt_flow_executor._perform_coordinate_action("click", None)
            gui.click.assert_not_called()

    def test_executor_allows_valid_point(self):
        with patch.object(wt_flow_executor, "pyautogui") as gui:
            wt_flow_executor._perform_coordinate_action("click", (120, 80))
            gui.click.assert_called_once_with(120, 80)

    def test_is_degenerate_click_point(self):
        self.assertTrue(wt_flow_executor._is_degenerate_click_point((0, 0)))
        self.assertTrue(wt_flow_executor._is_degenerate_click_point(None))
        self.assertFalse(wt_flow_executor._is_degenerate_click_point((10, 10)))


if __name__ == "__main__":
    unittest.main()
