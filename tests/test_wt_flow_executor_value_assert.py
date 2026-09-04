# encoding: utf-8
"""值断言(value_equals / nonempty)与自动取动作值 的单测。"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_executor
import wt_flow_locator


class WaitValueConditionTests(unittest.TestCase):
    """wait_for_flow_control_condition 的值断言分支（mock 定位与取值，无真实 UI）。"""

    def _patch(self, fake_exists=True, actual_value=""):
        original_find = wt_flow_locator.find_flow_control
        original_value = wt_flow_locator.get_wrapper_value
        wt_flow_locator.find_flow_control = lambda *a, **k: (object() if fake_exists else None)
        wt_flow_locator.get_wrapper_value = lambda *a, **k: actual_value
        self.addCleanup(setattr, wt_flow_locator, "find_flow_control", original_find)
        self.addCleanup(setattr, wt_flow_locator, "get_wrapper_value", original_value)

    def test_nonempty_passes_when_value_present(self):
        self._patch(actual_value="CFT01")
        self.assertTrue(
            wt_flow_locator.wait_for_flow_control_condition(
                "s1", "c", condition="nonempty", timeout_seconds=0.5, poll_interval_seconds=0.05
            )
        )

    def test_nonempty_fails_when_empty(self):
        self._patch(actual_value="")
        self.assertFalse(
            wt_flow_locator.wait_for_flow_control_condition(
                "s1", "c", condition="nonempty", timeout_seconds=0.2, poll_interval_seconds=0.05
            )
        )

    def test_nonempty_fails_when_control_missing(self):
        self._patch(fake_exists=False, actual_value="")
        self.assertFalse(
            wt_flow_locator.wait_for_flow_control_condition(
                "s1", "c", condition="nonempty", timeout_seconds=0.2, poll_interval_seconds=0.05
            )
        )

    def test_value_equals_passes_on_exact_match(self):
        self._patch(actual_value="CFT01")
        self.assertTrue(
            wt_flow_locator.wait_for_flow_control_condition(
                "s1", "c", condition="value_equals", timeout_seconds=0.5,
                poll_interval_seconds=0.05, expected_value="CFT01",
            )
        )

    def test_value_equals_fails_on_mismatch(self):
        self._patch(actual_value="OTHER")
        self.assertFalse(
            wt_flow_locator.wait_for_flow_control_condition(
                "s1", "c", condition="value_equals", timeout_seconds=0.2,
                poll_interval_seconds=0.05, expected_value="CFT01",
            )
        )

    def test_value_equals_tolerates_surrounding_whitespace_and_case(self):
        self._patch(actual_value="  CFT01\n")
        self.assertTrue(
            wt_flow_locator.wait_for_flow_control_condition(
                "s1", "c", condition="value_equals", timeout_seconds=0.5,
                poll_interval_seconds=0.05, expected_value="cft01",
            )
        )

    def test_value_equals_with_empty_expected_acts_as_nonempty(self):
        self._patch(actual_value="anything")
        self.assertTrue(
            wt_flow_locator.wait_for_flow_control_condition(
                "s1", "c", condition="value_equals", timeout_seconds=0.5,
                poll_interval_seconds=0.05, expected_value="",
            )
        )


class ResolveContinueWhenTests(unittest.TestCase):
    """_resolve_continue_when 的纯逻辑：自动取动作值 + 显式值条件降级。"""

    def test_no_continue_when_and_non_value_action_returns_none(self):
        # 无显式 continueWhen 且动作非输入/选择 → 不生成自动断言
        ac = {"action": "click", "controlId": "btn", "inputText": "CFT01"}
        self.assertIsNone(wt_flow_executor._resolve_continue_when(ac))

    def test_auto_assert_from_input_text(self):
        # 键入类动作无 continueWhen → 自动生成 value_equals 断言
        ac = {"action": "type_text", "controlId": "name_input", "inputText": "CFT01"}
        cw = wt_flow_executor._resolve_continue_when(ac)
        self.assertIsNotNone(cw)
        self.assertEqual(cw["condition"], "value_equals")
        self.assertEqual(cw["expectedValue"], "CFT01")
        self.assertEqual(cw["controlId"], "name_input")
        self.assertTrue(cw["autoAssert"])

    def test_auto_assert_degrades_to_nonempty_when_no_value(self):
        # 输入类动作无明确输入值 → nonempty 断言，仍能拦空值假成功
        ac = {"action": "set_combobox", "controlId": "combo"}
        cw = wt_flow_executor._resolve_continue_when(ac)
        self.assertIsNotNone(cw)
        self.assertEqual(cw["condition"], "nonempty")

    def test_auto_assert_skipped_for_list_item_option_control(self):
        # 下拉选项项（targetValue 含 ListBoxItem/ListItem）无 ValuePattern，
        # select_dropdown_item_runtime 点击成功后不得再 auto-assert nonempty
        # （否则反复定位轮询直到超时，每步拖慢 10s+ 且误报 failed）。
        ac = {"action": "select_dropdown_item_runtime", "controlId": "dropdown_item"}
        sd = {
            "id": "s1",
            "controls": [{
                "id": "dropdown_item",
                "targetMethod": "class_name,label_text",
                "targetValue": "ListBoxItem,日期时间",
                "inspectData": {"controlType": "ListItem", "className": "ListBoxItem"},
            }],
        }
        cw = wt_flow_executor._resolve_continue_when(ac, sd)
        self.assertIsNone(cw)

    def test_explicit_value_equals_without_expected_takes_from_action(self):
        # 显式配置 value_equals 但未填 expectedValue → 从动作值自动取
        ac = {
            "action": "type_text",
            "inputText": "99",
            "continueWhen": {"controlId": "c", "condition": "value_equals", "timeoutSeconds": 2},
        }
        cw = wt_flow_executor._resolve_continue_when(ac)
        self.assertEqual(cw["condition"], "value_equals")
        self.assertEqual(cw["expectedValue"], "99")

    def test_explicit_value_equals_no_value_at_all_degrades_to_nonempty(self):
        ac = {
            "action": "click",
            "controlId": "c",
            "continueWhen": {"controlId": "c", "condition": "value_equals"},
        }
        cw = wt_flow_executor._resolve_continue_when(ac)
        self.assertEqual(cw["condition"], "nonempty")

    def test_existing_exists_condition_unchanged(self):
        ac = {
            "action": "click",
            "controlId": "c",
            "continueWhen": {"controlId": "c", "condition": "visible"},
        }
        cw = wt_flow_executor._resolve_continue_when(ac)
        self.assertEqual(cw["condition"], "visible")
        # 非值条件不会新增 expectedValue
        self.assertIn("expectedValue", cw)
        self.assertEqual(cw["expectedValue"], "")


if __name__ == "__main__":
    unittest.main()
