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
        self.assertEqual(parsed["recommendedTargetMethod"], "automation_id,control_type")
        self.assertEqual(parsed["recommendedTargetValue"], "btnOpen,Button")


class SlugifyFilenameTests(unittest.TestCase):
    def test_basic_and_whitespace(self):
        self.assertEqual(wt_flow_editor_utils.slugify_filename("My Window"), "My_Window")
        self.assertEqual(wt_flow_editor_utils.slugify_filename("a   b"), "a_b")

    def test_illegal_chars_become_single_underscore(self):
        self.assertEqual(wt_flow_editor_utils.slugify_filename("a/b:c*?"), "a_b_c")

    def test_empty_and_only_illegal_return_fallback(self):
        self.assertEqual(wt_flow_editor_utils.slugify_filename(""), "window")
        self.assertEqual(wt_flow_editor_utils.slugify_filename("   ", fallback="x"), "x")
        self.assertEqual(wt_flow_editor_utils.slugify_filename("///", fallback="fb"), "fb")
        self.assertEqual(wt_flow_editor_utils.slugify_filename(None), "window")

    def test_truncated_to_80(self):
        self.assertEqual(len(wt_flow_editor_utils.slugify_filename("a" * 200)), 80)

    def test_fallback_default_differs_per_caller(self):
        # 行为保留：调用方自带默认 fallback（editor=common / build=window）
        self.assertEqual(wt_flow_editor_utils.slugify_filename("", fallback="common"), "common")

    def test_build_and_converter_delegate_to_utils(self):
        import flow_recorder_converter
        self.assertEqual(
            flow_recorder_converter._strip_wrapping_quotes('"v"'),
            wt_flow_editor_utils.strip_wrapping_quotes('"v"'),
        )
        self.assertEqual(flow_recorder_converter._strip_wrapping_quotes('"v"'), "v")


class StripWrappingQuotesTests(unittest.TestCase):
    def test_strips_matching_quotes(self):
        self.assertEqual(wt_flow_editor_utils.strip_wrapping_quotes('"abc"'), "abc")
        self.assertEqual(wt_flow_editor_utils.strip_wrapping_quotes("'x'"), "x")

    def test_strips_nested_quotes(self):
        self.assertEqual(wt_flow_editor_utils.strip_wrapping_quotes('"\'x\'"'), "x")

    def test_leaves_unquoted_text(self):
        self.assertEqual(wt_flow_editor_utils.strip_wrapping_quotes("no quotes"), "no quotes")
        self.assertEqual(wt_flow_editor_utils.strip_wrapping_quotes(None), "")


if __name__ == "__main__":
    unittest.main()
