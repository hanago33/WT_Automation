import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from wt_action_schema import (
    ALLOWED_ON_ERROR_MODES,
    ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS,
    build_action_schema_hint,
    get_action_schema,
)
from wt_flow_validation import validate_flow_definition, validate_step_definition


class ActionSchemaValidationTests(unittest.TestCase):
    def test_sleep_action_does_not_require_control(self):
        schema = get_action_schema("sleep")
        self.assertFalse(schema["target_required"])
        self.assertEqual(schema["input_key"], "seconds")

    def test_type_text_relative_hint_mentions_post_input_keys(self):
        hint = build_action_schema_hint("type_text_relative")
        self.assertIn("postInputKeys", hint)

    def test_shared_constants_cover_editor_and_excel_options(self):
        self.assertIn("continue", ALLOWED_ON_ERROR_MODES)
        self.assertIn("retry", ALLOWED_ON_ERROR_MODES)
        self.assertIn("stop", ALLOWED_ON_ERROR_MODES)
        self.assertIn("fallback", ALLOWED_ON_ERROR_MODES)
        self.assertIn("uia", ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS)

    def test_click_action_requires_control(self):
        errors = validate_step_definition(
            {
                "id": "step_click",
                "name": "点击按钮",
                "actionType": "action",
                "controls": [],
                "actionConfig": {"action": "click"},
            }
        )
        self.assertTrue(any("缺少目标控件" in item for item in errors))

    def test_type_text_relative_requires_parent_window_and_region(self):
        schema = get_action_schema("type_text_relative")
        self.assertFalse(schema["target_required"])
        errors = validate_step_definition(
            {
                "id": "step_relative",
                "name": "父窗口区域输入",
                "windowTitle": "",
                "actionType": "action",
                "controls": [],
                "actionConfig": {
                    "action": "type_text_relative",
                    "text": "demo",
                    "parentWindow": {},
                    "relativeRegion": {"x": 1.2, "y": 0.1, "width": 0, "height": 0.08},
                },
            }
        )
        self.assertTrue(any("父窗口标题" in item for item in errors))
        self.assertTrue(any("`x`" in item for item in errors))
        self.assertTrue(any("`width`" in item for item in errors))

    def test_click_relative_region_uses_same_parent_window_validation(self):
        schema = get_action_schema("click_relative_region")
        self.assertFalse(schema["target_required"])
        self.assertFalse(schema["input_required"])
        errors = validate_step_definition(
            {
                "id": "step_relative_click",
                "name": "父窗口区域点击",
                "windowTitle": "",
                "actionType": "action",
                "controls": [],
                "actionConfig": {
                    "action": "click_relative_region",
                    "parentWindow": {"title": ""},
                    "relativeRegion": {"x": -0.1, "y": 0.1, "width": 0.4, "height": 1.2},
                },
            }
        )
        self.assertTrue(any("父窗口标题" in item for item in errors))
        self.assertTrue(any("`x`" in item for item in errors))
        self.assertTrue(any("`height`" in item for item in errors))

    def test_relative_region_allows_untitled_parent_with_class_and_framework(self):
        errors = validate_step_definition(
            {
                "id": "step_relative_untitled",
                "name": "无标题父窗口区域输入",
                "windowTitle": "",
                "actionType": "action",
                "controls": [],
                "actionConfig": {
                    "action": "type_text_relative",
                    "text": "125",
                    "parentWindow": {"title": "", "className": "Window", "frameworkId": "WPF"},
                    "relativeRegion": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1},
                },
            }
        )
        self.assertFalse(any("父窗口标题" in item for item in errors))

    def test_relative_region_anchor_must_be_known_value(self):
        errors = validate_step_definition(
            {
                "id": "step_relative_anchor",
                "name": "父窗口区域点击",
                "windowTitle": "导入统计数据文件",
                "actionType": "action",
                "controls": [],
                "actionConfig": {
                    "action": "click_relative_region",
                    "parentWindow": {"title": "导入统计数据文件"},
                    "relativeRegion": {"x": 0.4, "y": 0.3, "width": 0.2, "height": 0.1, "anchor": "top_left"},
                },
            }
        )
        self.assertTrue(any("`anchor` 非法" in item for item in errors))

    def test_continue_when_condition_must_be_known_value(self):
        errors = validate_step_definition(
            {
                "id": "step_continue_when",
                "name": "点击并等待",
                "windowTitle": "导入统计数据文件",
                "actionType": "action",
                "controls": [{"id": "control_done", "name": "完成标记"}],
                "actionConfig": {
                    "action": "click",
                    "controlId": "control_done",
                    "continueWhen": {"controlId": "control_done", "condition": "ready"},
                },
            }
        )
        self.assertTrue(any("续跑条件 `ready` 非法" in item for item in errors))

    def test_unknown_action_is_rejected_during_validation(self):
        errors = validate_step_definition(
            {
                "id": "step_unknown_action",
                "name": "未知动作",
                "actionType": "action",
                "controls": [{"id": "control_one", "name": "按钮"}],
                "actionConfig": {"action": "click_typo", "controlId": "control_one"},
            }
        )
        self.assertTrue(any("动作 `click_typo` 非法" in item for item in errors))

    def test_relative_region_framework_id_must_be_known_value(self):
        errors = validate_step_definition(
            {
                "id": "step_relative_framework",
                "name": "父窗口区域输入",
                "windowTitle": "",
                "actionType": "action",
                "controls": [],
                "actionConfig": {
                    "action": "type_text_relative",
                    "text": "demo",
                    "parentWindow": {"title": "", "className": "Window", "frameworkId": "Wpf"},
                    "relativeRegion": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.1},
                },
            }
        )
        self.assertTrue(any("frameworkId` 非法" in item for item in errors))

    def test_flow_definition_detects_missing_package_step(self):
        errors = validate_flow_definition(
            {
                "flowPackages": [{"id": "pkg_a", "stepIds": ["step_1", "missing_step"]}],
                "steps": [{"id": "step_1", "name": "步骤1", "actionType": "script"}],
            }
        )
        self.assertTrue(any("missing_step" in item for item in errors))

    def test_flow_definition_detects_duplicate_package_ids(self):
        errors = validate_flow_definition(
            {
                "flowPackages": [
                    {"id": "pkg_a", "stepIds": ["step_1"]},
                    {"id": "pkg_a", "stepIds": ["step_1"]},
                ],
                "steps": [{"id": "step_1", "name": "步骤1", "actionType": "script"}],
            }
        )
        self.assertTrue(any("流程包ID `pkg_a` 重复出现 2 次" in item for item in errors))


class WindowTitleConsistencyValidationTests(unittest.TestCase):
    def _step(self, window_title, ui_path):
        return {
            "id": "step_main",
            "name": "主窗口内控件",
            "actionType": "action",
            "actionConfig": {"action": "click", "controlId": "c1"},
            "controls": [
                {"id": "c1", "name": "控件", "windowTitle": window_title, "uiPath": ui_path}
            ],
        }

    def test_pseudo_window_title_is_auto_repaired_not_reported(self):
        errors = validate_step_definition(self._step("主面板", "Window > MicroScaleMainView_View_Main > A > B"))
        self.assertFalse(any("伪标题" in item for item in errors))

    def test_wildcard_window_title_allowed_for_main_window_root(self):
        errors = validate_step_definition(self._step("*", "Window > MicroScaleMainView_View_Main > A > B"))
        self.assertFalse(any("伪标题" in item for item in errors))

    def test_real_dialog_title_not_reported(self):
        errors = validate_step_definition(self._step("打开", "打开 > 文件名(N): > Edit"))
        self.assertFalse(any("伪标题" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
