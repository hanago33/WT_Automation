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


class StepPolicyResolutionTests(unittest.TestCase):
    """验证 _resolve_step_policy 归一化层：stepPolicy（新格式）→ 旧字段迁移，
    以及无 stepPolicy 时的零副作用（向后兼容）。
    """

    def test_no_step_policy_no_op(self):
        """无 stepPolicy 时，旧字段原样保留。"""
        ac = {"onError": "fallback", "retryCount": 3, "retryInterval": 2.0, "continueWhen": {"controlId": "c1"}}
        wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(ac["onError"], "fallback")
        self.assertEqual(ac["retryCount"], 3)
        self.assertEqual(ac["retryInterval"], 2.0)
        self.assertDictEqual(ac["continueWhen"], {"controlId": "c1"})

    def test_step_policy_populates_legacy_fields(self):
        """stepPolicy 写入旧字段。"""
        ac = {
            "stepPolicy": {
                "onFail": "fallback",
                "maxRetries": 2,
                "retryInterval": 3.0,
                "continueWhen": {"controlId": "x", "condition": "visible", "timeoutSeconds": 5},
            }
        }
        wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(ac["onError"], "fallback")
        self.assertEqual(ac["retryCount"], 2)
        self.assertEqual(ac["retryInterval"], 3.0)
        self.assertDictEqual(ac["continueWhen"], {"controlId": "x", "condition": "visible", "timeoutSeconds": 5})

    def test_step_policy_on_fail_skip_maps_to_continue(self):
        ac = {"stepPolicy": {"onFail": "skip"}}
        wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(ac["onError"], "continue")

    def test_step_policy_on_fail_abort_maps_to_stop(self):
        ac = {"stepPolicy": {"onFail": "abort"}}
        wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(ac["onError"], "stop")

    def test_step_policy_on_fail_retry_maps_to_retry(self):
        ac = {"stepPolicy": {"onFail": "retry", "maxRetries": 3}}
        wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(ac["onError"], "retry")
        self.assertEqual(ac["retryCount"], 3)

    def test_step_policy_on_fail_ask_maps_to_ask(self):
        ac = {"stepPolicy": {"onFail": "ask"}}
        wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(ac["onError"], "ask")

    def test_step_policy_on_fail_unknown_defaults_to_stop(self):
        ac = {"stepPolicy": {"onFail": "bogus"}}
        wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(ac["onError"], "stop")

    def test_step_policy_without_continue_when_erases_legacy(self):
        ac = {"stepPolicy": {"onFail": "skip"}, "continueWhen": {"controlId": "stale"}}
        wt_flow_executor._resolve_step_policy(ac)
        self.assertNotIn("continueWhen", ac)

    def test_step_policy_sets_retry_defaults(self):
        ac = {"stepPolicy": {"onFail": "fallback"}}
        wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(ac["retryCount"], 0)
        self.assertEqual(ac["retryInterval"], 1.0)

    def test_legacy_fields_unchanged_without_step_policy(self):
        """旧格式流程走不带 stepPolicy 的 actionConfig，返回值字段完全一致。"""
        ac = {
            "action": "click",
            "controlId": "ok_button",
            "onError": "stop",
            "retryCount": 0,
            "retryInterval": 1.0,
        }
        wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(ac["onError"], "stop")
        self.assertEqual(ac["retryCount"], 0)
        self.assertEqual(ac["retryInterval"], 1.0)


if __name__ == "__main__":
    unittest.main()
