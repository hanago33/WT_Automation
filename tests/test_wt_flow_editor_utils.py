import unittest

import wt_flow_editor_utils


class FlowEditorUtilsTests(unittest.TestCase):
    def test_normalize_inspect_scalar_removes_wrapping_quotes(self):
        self.assertEqual(wt_flow_editor_utils.normalize_inspect_scalar('"ButtonName"'), "ButtonName")
        self.assertEqual(wt_flow_editor_utils.normalize_inspect_scalar("property does not exist"), "")

    def test_parse_inspect_text_builds_recommended_locator(self):
        raw_text = """
Name: "打开"
ControlType: Button
AutomationId: "btnOpen"
ClassName: "Button"
"""
        parsed = wt_flow_editor_utils.parse_inspect_text(raw_text)
        self.assertEqual(parsed["recommendedTargetMethod"], "automation_id")
        self.assertEqual(parsed["recommendedTargetValue"], "btnOpen")


if __name__ == "__main__":
    unittest.main()
