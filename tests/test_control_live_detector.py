# encoding: utf-8
"""control_live_detector 匹配逻辑与 wt_flow_locator 统一后的回归测试。"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import control_live_detector as cld


def _score(ctrl_info, lib_ctrl):
    return cld._score_single_control(
        ctrl_info,
        lib_ctrl,
        str(ctrl_info.get("automationId", "")).strip().lower(),
        str(ctrl_info.get("name", "")).strip().lower(),
        str(ctrl_info.get("controlType", "")).strip().lower(),
        str(ctrl_info.get("className", "")).strip().lower(),
        str(ctrl_info.get("windowTitle", "")).strip().lower(),
        "test",
    )


class LiveDetectorLocatorMatchTests(unittest.TestCase):
    """实时检测匹配复用 wt_flow_locator 定位候选。"""

    def test_locator_match_via_target_value(self):
        # 捕获控件命中库条目的 targetValue（PART_ContentHost,Pane）→ 定位器匹配
        ctrl = {"automationId": "PART_ContentHost", "controlType": "Pane",
                "className": "ScrollViewer", "name": "", "windowTitle": ""}
        lib = {"name": "查找", "targetMethod": "automation_id,control_type",
               "targetValue": "PART_ContentHost,Pane", "inspectData": {}}
        result = _score(ctrl, lib)
        self.assertIsNotNone(result)
        self.assertIn("定位器匹配", result["reasons"])
        self.assertGreaterEqual(result["score"], 120)

    def test_no_locator_match_falls_back_to_field_score(self):
        # 不命中定位器，但仍可按字段评分
        ctrl = {"automationId": "OtherBox", "controlType": "Edit",
                "className": "TextBox", "name": "查找", "windowTitle": ""}
        lib = {"name": "查找", "targetMethod": "automation_id,control_type",
               "targetValue": "PART_ContentHost,Pane", "inspectData": {}}
        result = _score(ctrl, lib)
        if result is not None:
            self.assertNotIn("定位器匹配", result["reasons"])

    def test_pseudo_wrapper_fields(self):
        wrapper = cld._make_pseudo_wrapper({"automationId": "A", "controlType": "Edit",
                                            "className": "TextBox", "name": "n"})
        self.assertEqual(wrapper.window_text(), "n")
        self.assertEqual(wrapper.class_name(), "TextBox")
        self.assertEqual(wrapper.element_info.automation_id, "A")
        self.assertEqual(wrapper.element_info.control_type, "Edit")


if __name__ == "__main__":
    unittest.main()
