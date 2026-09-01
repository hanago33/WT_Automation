# -*- coding: utf-8 -*-
"""step_22 幂等返回的前置条件测试（列表标记控件可见 => 已在列表 => 跳过 GoBack）。"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_executor
import wt_flow_locator


class PreconditionVisibleSkipTests(unittest.TestCase):
    """actionConfig.precondition(condition=visible, expected=off) 的幂等返回语义。

    step_22 从元素结果视图"返回-综合结果列表"：若界面已在列表（标记控件
    WRAResults_ListBox_WRAResults 可见），GoBack 按钮不存在，应跳过点击而不是
    判定失败；若在元素结果视图（标记控件不可见），则正常执行 GoBack 点击。
    """

    def _action_config(self):
        return {
            "action": "click",
            "controlId": "step_18_control_1",
            "precondition": {
                "condition": "visible",
                "expected": "off",
                "controlId": "step_18_control_2",
                "timeoutSeconds": 1.5,
            },
        }

    def _step_def(self):
        return {"id": "step_22", "controls": []}

    def test_visible_marker_skips_action(self):
        """列表标记控件可见 -> 已在列表 -> 返回非空 skip 原因。"""
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_executor, "_LOG_STEP", MagicMock()))
            stack.enter_context(patch.object(wt_flow_executor, "_LOCATE_FLOW_CONTROL", return_value=MagicMock()))
            stack.enter_context(patch.object(
                wt_flow_executor, "_call_with_control_map_path",
                side_effect=lambda fn, *a, **k: fn(*a, **k)))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_is_offscreen", return_value="False"))
            reason = wt_flow_executor._eval_precondition_skip("step_22", self._action_config(), self._step_def())
        self.assertIsNotNone(reason)
        self.assertIn("跳过", reason)

    def test_invisible_marker_executes_action(self):
        """列表标记控件不可见（在元素结果视图）-> 执行 GoBack 点击。"""
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_executor, "_LOG_STEP", MagicMock()))
            stack.enter_context(patch.object(wt_flow_executor, "_LOCATE_FLOW_CONTROL", return_value=None))
            stack.enter_context(patch.object(
                wt_flow_executor, "_call_with_control_map_path",
                side_effect=lambda fn, *a, **k: fn(*a, **k)))
            reason = wt_flow_executor._eval_precondition_skip("step_22", self._action_config(), self._step_def())
        self.assertIsNone(reason)

    def test_marker_locate_exception_executes_action(self):
        """标记控件定位抛异常视为不可见 -> 执行点击（保守不漏操作）。"""
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_executor, "_LOG_STEP", MagicMock()))
            stack.enter_context(patch.object(wt_flow_executor, "_LOCATE_FLOW_CONTROL", side_effect=RuntimeError("boom")))
            stack.enter_context(patch.object(
                wt_flow_executor, "_call_with_control_map_path",
                side_effect=lambda fn, *a, **k: fn(*a, **k)))
            reason = wt_flow_executor._eval_precondition_skip("step_22", self._action_config(), self._step_def())
        self.assertIsNone(reason)

    def test_no_precondition_passthrough(self):
        """未配置 precondition 的步骤完全不受影响（返回 None，继续执行）。"""
        action_config = {"action": "click", "controlId": "step_18_control_1"}
        reason = wt_flow_executor._eval_precondition_skip("step_22", action_config, self._step_def())
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
