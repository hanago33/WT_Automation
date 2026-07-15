import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_executor


class FlowExecutorAiFallbackTests(unittest.TestCase):
    def setUp(self):
        self.reported = []
        self.ai_calls = []
        self.original_pyautogui_click = wt_flow_executor.pyautogui.click
        wt_flow_executor.pyautogui.click = lambda *args, **kwargs: None
        self.step_definition = {
            "id": "step_ai",
            "name": "测试 AI 介入",
            "enabled": True,
            "actionType": "action",
            "strategy": "action",
            "actionConfig": {
                "action": "click",
                "controlId": "missing_control",
                "onError": "fallback",
                "fallbackTemplate": r"image_templates\missing.png",
            },
        }

    def tearDown(self):
        wt_flow_executor.pyautogui.click = self.original_pyautogui_click

    def _configure_executor(self, locate_template_center_by_path):
        def get_step_definition(step_id):
            return self.step_definition if step_id == "step_ai" else {}

        def click_flow_control(*args, **kwargs):
            return False

        def report_step_result(run_report, step_id, step_name, status, **kwargs):
            self.reported.append(
                {
                    "step_id": step_id,
                    "step_name": step_name,
                    "status": status,
                    "extra": kwargs.get("extra", {}),
                    "error": kwargs.get("error", ""),
                }
            )

        def run_ai_intervention(step_id, context, original_error=None, fallback_error=None):
            self.ai_calls.append(
                {
                    "step_id": step_id,
                    "original_error": str(original_error or ""),
                    "fallback_error": str(fallback_error or ""),
                }
            )
            return {"aiInterventionUsed": True, "aiInterventionMode": "ui_tars_desktop"}

        wt_flow_executor.configure_flow_executor(
            get_step_definition=get_step_definition,
            get_flow_package=lambda package_id: {},
            get_step_params=lambda step_id: {},
            resolve_dynamic_value=lambda value, step_id, context: value,
            log_step=lambda message: None,
            click_flow_control=click_flow_control,
            click_relative_region=lambda *args, **kwargs: (False, {}),
            focus_flow_control=lambda *args, **kwargs: False,
            type_text_into_flow_control=lambda *args, **kwargs: False,
            type_text_into_relative_region=lambda *args, **kwargs: (False, {}),
            select_dropdown_item_runtime=lambda *args, **kwargs: (False, {}),
            drag_between_flow_controls=lambda *args, **kwargs: False,
            mouse_wheel_on_flow_control=lambda *args, **kwargs: False,
            wait_for_flow_control_condition=lambda *args, **kwargs: False,
            locate_template_center_by_path=locate_template_center_by_path,
            report_step_result=report_step_result,
            run_ai_intervention_after_failure=run_ai_intervention,
        )

    def test_ai_intervention_runs_after_template_fallback_failure(self):
        def locate_template_center_by_path(*args, **kwargs):
            raise RuntimeError("template not found")

        self._configure_executor(locate_template_center_by_path)
        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(len(self.ai_calls), 1)
        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertTrue(self.reported[-1]["extra"].get("aiInterventionUsed"))
        self.assertIn("template not found", self.ai_calls[0]["fallback_error"])

    def test_ai_intervention_does_not_run_when_template_fallback_succeeds(self):
        def locate_template_center_by_path(*args, **kwargs):
            return (10, 10)

        self._configure_executor(locate_template_center_by_path)
        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(len(self.ai_calls), 0)
        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertEqual(
            self.reported[-1]["extra"].get("fallbackTemplateUsed"),
            r"image_templates\missing.png",
        )

    def test_ai_intervention_still_fails_when_continue_condition_not_met(self):
        def locate_template_center_by_path(*args, **kwargs):
            raise RuntimeError("template not found")

        self.step_definition["actionConfig"]["continueWhen"] = {
            "controlId": "result_ready",
            "condition": "visible",
            "timeoutSeconds": 0.2,
        }

        def get_step_definition(step_id):
            return self.step_definition if step_id == "step_ai" else {}

        def click_flow_control(*args, **kwargs):
            return False

        def report_step_result(run_report, step_id, step_name, status, **kwargs):
            self.reported.append(
                {
                    "step_id": step_id,
                    "step_name": step_name,
                    "status": status,
                    "extra": kwargs.get("extra", {}),
                    "error": kwargs.get("error", ""),
                }
            )

        def run_ai_intervention(step_id, context, original_error=None, fallback_error=None):
            self.ai_calls.append({"step_id": step_id})
            return {"aiInterventionUsed": True}

        wt_flow_executor.configure_flow_executor(
            get_step_definition=get_step_definition,
            get_flow_package=lambda package_id: {},
            get_step_params=lambda step_id: {},
            resolve_dynamic_value=lambda value, step_id, context: value,
            log_step=lambda message: None,
            click_flow_control=click_flow_control,
            click_relative_region=lambda *args, **kwargs: (False, {}),
            focus_flow_control=lambda *args, **kwargs: False,
            type_text_into_flow_control=lambda *args, **kwargs: False,
            type_text_into_relative_region=lambda *args, **kwargs: (False, {}),
            select_dropdown_item_runtime=lambda *args, **kwargs: (False, {}),
            drag_between_flow_controls=lambda *args, **kwargs: False,
            mouse_wheel_on_flow_control=lambda *args, **kwargs: False,
            wait_for_flow_control_condition=lambda *args, **kwargs: False,
            locate_template_center_by_path=locate_template_center_by_path,
            report_step_result=report_step_result,
            run_ai_intervention_after_failure=run_ai_intervention,
        )

        context = {"run_report": {"stepResults": []}}
        with self.assertRaises(RuntimeError):
            wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(len(self.ai_calls), 1)
        self.assertEqual(self.reported[-1]["status"], "failed")
        self.assertIn("步骤续跑条件未满足", self.reported[-1]["error"])

    def test_action_waits_for_continue_condition_before_reporting_success(self):
        self.step_definition["actionConfig"] = {
            "action": "click",
            "controlId": "ok_button",
            "continueWhen": {
                "controlId": "result_ready",
                "condition": "visible",
                "timeoutSeconds": 0.2,
            },
        }

        def get_step_definition(step_id):
            return self.step_definition if step_id == "step_ai" else {}

        def click_flow_control(*args, **kwargs):
            return True

        def wait_for_flow_control_condition(*args, **kwargs):
            return kwargs.get("control_id") == "result_ready" and kwargs.get("condition") == "visible"

        def report_step_result(run_report, step_id, step_name, status, **kwargs):
            self.reported.append(
                {
                    "step_id": step_id,
                    "step_name": step_name,
                    "status": status,
                    "extra": kwargs.get("extra", {}),
                    "error": kwargs.get("error", ""),
                }
            )

        wt_flow_executor.configure_flow_executor(
            get_step_definition=get_step_definition,
            get_flow_package=lambda package_id: {},
            get_step_params=lambda step_id: {},
            resolve_dynamic_value=lambda value, step_id, context: value,
            log_step=lambda message: None,
            click_flow_control=click_flow_control,
            click_relative_region=lambda *args, **kwargs: (False, {}),
            focus_flow_control=lambda *args, **kwargs: False,
            type_text_into_flow_control=lambda *args, **kwargs: False,
            type_text_into_relative_region=lambda *args, **kwargs: (False, {}),
            select_dropdown_item_runtime=lambda *args, **kwargs: (False, {}),
            drag_between_flow_controls=lambda *args, **kwargs: False,
            mouse_wheel_on_flow_control=lambda *args, **kwargs: False,
            wait_for_flow_control_condition=wait_for_flow_control_condition,
            locate_template_center_by_path=lambda *args, **kwargs: None,
            report_step_result=report_step_result,
            run_ai_intervention_after_failure=lambda *args, **kwargs: {},
        )

        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertTrue(self.reported[-1]["extra"].get("continueWhenSatisfied"))
        self.assertEqual(self.reported[-1]["extra"].get("continueWhenPhase"), "action")

    def test_type_text_relative_passes_post_input_keys(self):
        captured = {}
        self.step_definition["actionConfig"] = {
            "action": "type_text_relative",
            "text": "120.5",
            "postInputKeys": "{TAB}",
            "parentWindow": {
                "title": "创建一个新的气象对象",
                "className": "Window",
                "frameworkId": "WPF",
            },
            "relativeRegion": {
                "x": 0.48,
                "y": 0.76,
                "width": 0.43,
                "height": 0.06,
                "anchor": "center",
            },
        }

        def get_step_definition(step_id):
            return self.step_definition if step_id == "step_ai" else {}

        def type_text_into_relative_region(step_definition, parent_window, relative_region, text, **kwargs):
            captured["post_input_keys"] = kwargs.get("post_input_keys")
            captured["text"] = text
            return True, {"clickPoint": {"x": 1, "y": 1}}

        def report_step_result(run_report, step_id, step_name, status, **kwargs):
            self.reported.append(
                {
                    "step_id": step_id,
                    "step_name": step_name,
                    "status": status,
                    "extra": kwargs.get("extra", {}),
                    "error": kwargs.get("error", ""),
                }
            )

        wt_flow_executor.configure_flow_executor(
            get_step_definition=get_step_definition,
            get_flow_package=lambda package_id: {},
            get_step_params=lambda step_id: {},
            resolve_dynamic_value=lambda value, step_id, context: value,
            log_step=lambda message: None,
            click_flow_control=lambda *args, **kwargs: False,
            click_relative_region=lambda *args, **kwargs: (False, {}),
            focus_flow_control=lambda *args, **kwargs: False,
            type_text_into_flow_control=lambda *args, **kwargs: False,
            type_text_into_relative_region=type_text_into_relative_region,
            select_dropdown_item_runtime=lambda *args, **kwargs: (False, {}),
            drag_between_flow_controls=lambda *args, **kwargs: False,
            mouse_wheel_on_flow_control=lambda *args, **kwargs: False,
            wait_for_flow_control_condition=lambda *args, **kwargs: False,
            locate_template_center_by_path=lambda *args, **kwargs: None,
            report_step_result=report_step_result,
            run_ai_intervention_after_failure=lambda *args, **kwargs: {},
        )

        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(captured["text"], "120.5")
        self.assertEqual(captured["post_input_keys"], "{TAB}")
        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertEqual(self.reported[-1]["extra"].get("postInputKeys"), "{TAB}")

    def test_type_text_relative_falls_back_for_legacy_signature(self):
        captured = {}
        self.step_definition["actionConfig"] = {
            "action": "type_text_relative",
            "text": "120.5",
            "postInputKeys": "{TAB}",
            "parentWindow": {
                "title": "创建一个新的气象对象",
                "className": "Window",
                "frameworkId": "WPF",
            },
            "relativeRegion": {
                "x": 0.48,
                "y": 0.76,
                "width": 0.43,
                "height": 0.06,
                "anchor": "center",
            },
        }

        def get_step_definition(step_id):
            return self.step_definition if step_id == "step_ai" else {}

        def legacy_type_text_into_relative_region(step_definition, parent_window, relative_region, text, timeout_seconds=3, window_title_hint=""):
            captured["text"] = text
            captured["window_title_hint"] = window_title_hint
            return True, {"clickPoint": {"x": 2, "y": 2}}

        def report_step_result(run_report, step_id, step_name, status, **kwargs):
            self.reported.append(
                {
                    "step_id": step_id,
                    "step_name": step_name,
                    "status": status,
                    "extra": kwargs.get("extra", {}),
                    "error": kwargs.get("error", ""),
                }
            )

        wt_flow_executor.configure_flow_executor(
            get_step_definition=get_step_definition,
            get_flow_package=lambda package_id: {},
            get_step_params=lambda step_id: {},
            resolve_dynamic_value=lambda value, step_id, context: value,
            log_step=lambda message: None,
            click_flow_control=lambda *args, **kwargs: False,
            click_relative_region=lambda *args, **kwargs: (False, {}),
            focus_flow_control=lambda *args, **kwargs: False,
            type_text_into_flow_control=lambda *args, **kwargs: False,
            type_text_into_relative_region=legacy_type_text_into_relative_region,
            select_dropdown_item_runtime=lambda *args, **kwargs: (False, {}),
            drag_between_flow_controls=lambda *args, **kwargs: False,
            mouse_wheel_on_flow_control=lambda *args, **kwargs: False,
            wait_for_flow_control_condition=lambda *args, **kwargs: False,
            locate_template_center_by_path=lambda *args, **kwargs: None,
            report_step_result=report_step_result,
            run_ai_intervention_after_failure=lambda *args, **kwargs: {},
        )

        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(captured["text"], "120.5")
        self.assertEqual(captured["window_title_hint"], "")
        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertEqual(self.reported[-1]["extra"].get("postInputKeys"), "{TAB}")

    def test_on_error_continue_keeps_flow_running_and_reports_failed_step(self):
        self.step_definition["actionConfig"] = {
            "action": "click",
            "controlId": "missing_control",
            "onError": "continue",
        }

        def get_step_definition(step_id):
            return self.step_definition if step_id == "step_ai" else {}

        def report_step_result(run_report, step_id, step_name, status, **kwargs):
            self.reported.append(
                {
                    "step_id": step_id,
                    "step_name": step_name,
                    "status": status,
                    "extra": kwargs.get("extra", {}),
                    "error": kwargs.get("error", ""),
                }
            )

        wt_flow_executor.configure_flow_executor(
            get_step_definition=get_step_definition,
            get_flow_package=lambda package_id: {},
            get_step_params=lambda step_id: {},
            resolve_dynamic_value=lambda value, step_id, context: value,
            log_step=lambda message: None,
            click_flow_control=lambda *args, **kwargs: False,
            click_relative_region=lambda *args, **kwargs: (False, {}),
            focus_flow_control=lambda *args, **kwargs: False,
            type_text_into_flow_control=lambda *args, **kwargs: False,
            type_text_into_relative_region=lambda *args, **kwargs: (False, {}),
            select_dropdown_item_runtime=lambda *args, **kwargs: (False, {}),
            drag_between_flow_controls=lambda *args, **kwargs: False,
            mouse_wheel_on_flow_control=lambda *args, **kwargs: False,
            wait_for_flow_control_condition=lambda *args, **kwargs: False,
            locate_template_center_by_path=lambda *args, **kwargs: None,
            report_step_result=report_step_result,
            run_ai_intervention_after_failure=lambda *args, **kwargs: {},
        )

        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(self.reported[-1]["status"], "failed")
        self.assertEqual(self.reported[-1]["extra"].get("onErrorHandled"), "continue")
        self.assertIn("未命中控件", self.reported[-1]["error"])

    def test_retry_count_retries_before_success(self):
        attempts = {"count": 0}
        self.step_definition["actionConfig"] = {
            "action": "click",
            "controlId": "ok_button",
            "retryCount": 1,
            "retryInterval": 0,
            "onError": "stop",
        }

        def get_step_definition(step_id):
            return self.step_definition if step_id == "step_ai" else {}

        def click_flow_control(*args, **kwargs):
            attempts["count"] += 1
            return attempts["count"] >= 2

        def report_step_result(run_report, step_id, step_name, status, **kwargs):
            self.reported.append(
                {
                    "step_id": step_id,
                    "step_name": step_name,
                    "status": status,
                    "extra": kwargs.get("extra", {}),
                    "error": kwargs.get("error", ""),
                }
            )

        wt_flow_executor.configure_flow_executor(
            get_step_definition=get_step_definition,
            get_flow_package=lambda package_id: {},
            get_step_params=lambda step_id: {},
            resolve_dynamic_value=lambda value, step_id, context: value,
            log_step=lambda message: None,
            click_flow_control=click_flow_control,
            click_relative_region=lambda *args, **kwargs: (False, {}),
            focus_flow_control=lambda *args, **kwargs: False,
            type_text_into_flow_control=lambda *args, **kwargs: False,
            type_text_into_relative_region=lambda *args, **kwargs: (False, {}),
            select_dropdown_item_runtime=lambda *args, **kwargs: (False, {}),
            drag_between_flow_controls=lambda *args, **kwargs: False,
            mouse_wheel_on_flow_control=lambda *args, **kwargs: False,
            wait_for_flow_control_condition=lambda *args, **kwargs: False,
            locate_template_center_by_path=lambda *args, **kwargs: None,
            report_step_result=report_step_result,
            run_ai_intervention_after_failure=lambda *args, **kwargs: {},
        )

        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(attempts["count"], 2)
        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertEqual(self.reported[-1]["extra"].get("attemptCount"), 2)
        self.assertEqual(self.reported[-1]["extra"].get("retryCountConfigured"), 1)


if __name__ == "__main__":
    unittest.main()
