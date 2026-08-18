# encoding: utf-8
"""A 组正确性缺口修复测试。

覆盖：
- B8   Raw View 预过滤失败不再剪枝子树：无 automationId 目标可达
- B7   label 候选按原始分排序（不再"第一个枚举即胜出"）
- BUG-5 wait_for_control 坐标兜底不产生点击
- ④    HwndWrapper 类名跨机器兼容（不再绑定固定 GUID）
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator
import wt_flow_executor


class RawViewDescentTests(unittest.TestCase):
    """B8：预过滤失败只跳过节点自身，仍下沉到子节点（无 aid 目标可达）。"""

    def test_prefilter_failure_does_not_prune_subtree(self):
        root = MagicMock()
        anc = MagicMock()
        leaf = MagicMock()
        walker = MagicMock()
        walker.GetFirstChildElement.side_effect = (
            lambda e: {id(root): anc, id(anc): leaf}.get(id(e), None)
        )
        walker.GetNextSiblingElement.side_effect = lambda e: None
        uia_instance = MagicMock()
        uia_instance.iuia.RawViewWalker = walker

        window = MagicMock()
        window.element_info.element = root

        checked = []

        def fake_prefilter(element, target_name, target_automation_id, target_type_id, props):
            checked.append(element)
            return False  # 全部拒收：只验证"是否下沉到子节点"

        with ExitStack() as stack:
            stack.enter_context(patch("pywinauto.uia_defines.IUIA", return_value=uia_instance))
            stack.enter_context(patch.object(wt_flow_locator, "_raw_element_passes_prefilter", side_effect=fake_prefilter))
            for _ in wt_flow_locator.iter_raw_view_fallback_candidates(window, {}):
                pass

        checked_ids = {id(e) for e in checked}
        self.assertIn(id(leaf), checked_ids, "祖先预过滤失败后必须仍下沉访问叶子（否则无 aid 目标不可达）")
        self.assertIn(id(anc), checked_ids)


class LabelFallbackRankingTests(unittest.TestCase):
    """B7：同窗口多 label 候选按原始分选最优，而非枚举顺序。"""

    def test_prefers_higher_score_over_enumeration_order(self):
        cand_low = MagicMock()
        cand_high = MagicMock()
        window = MagicMock()
        window.descendants.return_value = [cand_low, cand_high]
        control_definition = {"name": "输入框", "labelText": "名称", "controlType": "Edit"}

        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value="Edit"))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_automation_id", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True))
            stack.enter_context(patch.object(
                wt_flow_locator, "score_control_match",
                side_effect=lambda c, cd: 50 if c is cand_low else 90,
            ))
            result = wt_flow_locator._try_label_to_input_fallback([window], control_definition)

        self.assertIs(result, cand_high, "必须选择原始分更高的候选，而不是第一个枚举到的")


class CoordinateWaitForControlTests(unittest.TestCase):
    """BUG-5：wait_for_control 坐标兜底只确认存在，不产生点击。"""

    def test_wait_for_control_does_not_click(self):
        with patch.object(wt_flow_executor, "pyautogui") as pyautogui_mock:
            wt_flow_executor._perform_coordinate_action("wait_for_control", (10, 20))
            pyautogui_mock.click.assert_not_called()

    def test_click_still_clicks(self):
        with patch.object(wt_flow_executor, "pyautogui") as pyautogui_mock:
            wt_flow_executor._perform_coordinate_action("click", (10, 20))
            pyautogui_mock.click.assert_called_with(10, 20)

    def test_type_text_still_clicks_then_types(self):
        with patch.object(wt_flow_executor, "pyautogui") as pyautogui_mock, \
             patch.object(wt_flow_executor, "send_keys") as send_keys_mock:
            wt_flow_executor._perform_coordinate_action("type_text", (10, 20), "abc")
            pyautogui_mock.click.assert_called_with(10, 20)
            send_keys_mock.assert_called_with("abc")


class WindowClassHashTests(unittest.TestCase):
    """④：HwndWrapper 类名只按进程名匹配，GUID 随机器变化不失效。"""

    def _match(self, class_name):
        wrapper = MagicMock()
        top = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_top_level_window", return_value=top))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_framework_id", return_value="WPF"))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_class_name", return_value=class_name))
            return wt_flow_locator.wrapper_matches_expected_window_title(wrapper, "Meteodyn Universe")

    def test_mup_class_matches_with_different_guid(self):
        self.assertTrue(
            self._match("HwndWrapper[MUPSmartClient.exe;;dead-beef-other-machine-guid]"),
            "GUID 因机器/安装而异，不能绑定固定值",
        )

    def test_window_class_matches(self):
        self.assertTrue(self._match("Window"))

    def test_other_app_class_does_not_match(self):
        self.assertFalse(self._match("HwndWrapper[OtherApp.exe;;some-guid]"))


if __name__ == "__main__":
    unittest.main()