import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

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
        self.click_calls = 0
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

    def _configure_executor(self, locate_template_center_by_path, click_flow_control=None):
        def get_step_definition(step_id):
            return self.step_definition if step_id == "step_ai" else {}

        def _click_flow_control(*args, **kwargs):
            self.click_calls += 1
            if click_flow_control is None:
                return False
            return click_flow_control(*args, **kwargs)

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
            click_flow_control=_click_flow_control,
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

    def test_ai_intervention_without_continue_when_is_unverified_failure(self):
        """防"试过即成功"：AI 介入后若无 continueWhen 校验结果，步骤必须判失败。"""
        def locate_template_center_by_path(*args, **kwargs):
            raise RuntimeError("template not found")

        self._configure_executor(locate_template_center_by_path)
        context = {"run_report": {"stepResults": []}}
        with self.assertRaises(RuntimeError):
            wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(len(self.ai_calls), 1)
        self.assertEqual(self.reported[-1]["status"], "failed")
        self.assertIn("AI 介入结果未验证", self.reported[-1]["error"])
        self.assertTrue(self.reported[-1]["extra"].get("aiInterventionUnverified"))
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

    def test_prefer_template_succeeds_without_main_flow(self):
        self.step_definition["actionConfig"].update({"preferTemplate": True})

        def locate_template_center_by_path(*args, **kwargs):
            return (10, 10)

        self._configure_executor(locate_template_center_by_path)
        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        # 模板优先命中：主流程控件定位应完全跳过
        self.assertEqual(self.click_calls, 0)
        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertEqual(
            self.reported[-1]["extra"].get("templatePreferred"),
            r"image_templates\missing.png",
        )

    def test_prefer_template_supports_click_relative_anchor(self):
        # click_relative_anchor：模板命中锚点中心后作为最终点击点，忽略 offsetX/offsetY
        self.step_definition["actionConfig"] = {
            "action": "click_relative_anchor",
            "controlId": "anchor_control",
            "offsetX": 10,
            "offsetY": -5,
            "preferTemplate": True,
            "fallbackTemplate": r"image_templates\WT.png",
        }
        clicked = []
        original_click = wt_flow_executor.pyautogui.click
        wt_flow_executor.pyautogui.click = lambda x, y: clicked.append((x, y))

        def locate_template_center_by_path(*args, **kwargs):
            return (100, 100)

        self._configure_executor(locate_template_center_by_path)
        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)
        wt_flow_executor.pyautogui.click = original_click

        self.assertEqual(self.click_calls, 0)  # 主流程控件定位完全跳过
        self.assertEqual(clicked, [(100, 100)])  # 模板命中点即点击点，offsetX/offsetY 被忽略
        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertTrue(self.reported[-1]["extra"].get("templatePreferred"))

    def test_prefer_template_miss_falls_back_to_main_flow(self):
        self.step_definition["actionConfig"].update({"preferTemplate": True})

        def locate_template_center_by_path(*args, **kwargs):
            raise RuntimeError("template not found")

        def click_flow_control(*args, **kwargs):
            return True

        self._configure_executor(locate_template_center_by_path, click_flow_control)
        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        # 模板未命中：应回退主流程控件定位并成功
        self.assertEqual(self.click_calls, 1)
        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertTrue(self.reported[-1]["extra"].get("templatePreferredAttempted"))

    def test_prefer_template_not_set_keeps_original_fallback_only(self):
        # 默认未勾选 preferTemplate：主流程成功时不做模板识别，行为与旧版一致
        locate_calls = []

        def locate_template_center_by_path(*args, **kwargs):
            locate_calls.append(args)
            return (10, 10)

        def click_flow_control(*args, **kwargs):
            return True

        self._configure_executor(locate_template_center_by_path, click_flow_control)
        context = {"run_report": {"stepResults": []}}
        wt_flow_executor.execute_step_by_id("step_ai", {"step_ai": {"id": "step_ai"}}, context)

        self.assertEqual(locate_calls, [])
        self.assertEqual(self.click_calls, 1)
        self.assertEqual(self.reported[-1]["status"], "success")
        self.assertNotIn("templatePreferred", self.reported[-1]["extra"])
        self.assertNotIn("templatePreferredAttempted", self.reported[-1]["extra"])


class FeedbackAndClickDispatchTests(unittest.TestCase):
    """回归测试：反馈写入降级日志、点击类动作分发。"""

    def setUp(self):
        self.reported = []

    def test_feedback_logging_handles_corrupt_flow_definition(self):
        logged = []

        def log_step(message):
            logged.append(message)

        wt_flow_executor.configure_flow_executor(log_step=log_step)
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            corrupt_path = f.name
        try:
            # 损坏 JSON 触发 load 异常分支，必须只记一条单参日志、不崩溃
            wt_flow_executor._write_feedback_to_flow(
                {"flowDefinitionPath": corrupt_path}, "s1", {"type": "test"}
            )
        finally:
            os.unlink(corrupt_path)
        self.assertEqual(len(logged), 1)
        self.assertIn("Failed to load flow definition", logged[0])

    def test_click_family_dispatches_with_correct_kind_and_count(self):
        calls = []
        action_config = {"action": "click", "controlId": "c1", "waitAfter": 0}

        def get_step_definition(step_id):
            return {"id": step_id, "actionConfig": action_config}

        def click_flow_control(step_id, control_id, **kwargs):
            calls.append(kwargs.get("click_kind"))
            return True

        wt_flow_executor.configure_flow_executor(
            get_step_definition=get_step_definition,
            log_step=lambda message: None,
            click_flow_control=click_flow_control,
            report_step_result=lambda *args, **kwargs: None,
        )

        cases = [
            ("click", "left", 1),
            ("double_click", "double", 1),
            ("right_click", "right", 1),
            ("double_right_click", "right", 2),
        ]
        for action_name, expected_kind, expected_count in cases:
            calls.clear()
            action_config["action"] = action_name
            wt_flow_executor.run_action_step("s1", {"step_outputs": {}})
            self.assertEqual(calls, [expected_kind] * expected_count, action_name)


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
        """stepPolicy 写入旧字段，返回归一化后的副本。"""
        ac = {
            "stepPolicy": {
                "onFail": "fallback",
                "maxRetries": 2,
                "retryInterval": 3.0,
                "continueWhen": {"controlId": "x", "condition": "visible", "timeoutSeconds": 5},
            }
        }
        result = wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(result["onError"], "fallback")
        self.assertEqual(result["retryCount"], 2)
        self.assertEqual(result["retryInterval"], 3.0)
        self.assertDictEqual(result["continueWhen"], {"controlId": "x", "condition": "visible", "timeoutSeconds": 5})

    def test_step_policy_on_fail_skip_maps_to_continue(self):
        ac = {"stepPolicy": {"onFail": "skip"}}
        result = wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(result["onError"], "continue")

    def test_step_policy_on_fail_abort_maps_to_stop(self):
        ac = {"stepPolicy": {"onFail": "abort"}}
        result = wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(result["onError"], "stop")

    def test_step_policy_on_fail_retry_maps_to_retry(self):
        ac = {"stepPolicy": {"onFail": "retry", "maxRetries": 3}}
        result = wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(result["onError"], "retry")
        self.assertEqual(result["retryCount"], 3)

    def test_step_policy_on_fail_ask_maps_to_ask(self):
        ac = {"stepPolicy": {"onFail": "ask"}}
        result = wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(result["onError"], "ask")

    def test_step_policy_on_fail_unknown_defaults_to_stop(self):
        ac = {"stepPolicy": {"onFail": "bogus"}}
        result = wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(result["onError"], "stop")

    def test_step_policy_without_continue_when_erases_legacy(self):
        ac = {"stepPolicy": {"onFail": "skip"}, "continueWhen": {"controlId": "stale"}}
        result = wt_flow_executor._resolve_step_policy(ac)
        self.assertNotIn("continueWhen", result)

    def test_step_policy_sets_retry_defaults(self):
        ac = {"stepPolicy": {"onFail": "fallback"}}
        result = wt_flow_executor._resolve_step_policy(ac)
        self.assertEqual(result["retryCount"], 0)
        self.assertEqual(result["retryInterval"], 1.0)

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


class FallbackHardeningTests(unittest.TestCase):
    """回归：滚轮坐标兜底真实滚动、下拉兜底拒绝假成功、反馈并发回写不丢记录。"""

    def test_mouse_wheel_coordinate_fallback_executes_scroll(self):
        scroll_calls = []
        move_calls = []
        original_scroll = wt_flow_executor.pyautogui.scroll
        original_move = wt_flow_executor.pyautogui.moveTo
        wt_flow_executor.pyautogui.scroll = lambda delta: scroll_calls.append(delta)
        wt_flow_executor.pyautogui.moveTo = lambda x, y: move_calls.append((x, y))
        try:
            result = wt_flow_executor._execute_fallback_action(
                "mouse_wheel",
                (120, 240),
                action_config={"action": "mouse_wheel", "delta": -3},
                step_id="s1",
            )
        finally:
            wt_flow_executor.pyautogui.scroll = original_scroll
            wt_flow_executor.pyautogui.moveTo = original_move

        self.assertEqual(scroll_calls, [-3])
        self.assertEqual(move_calls, [(120, 240)])
        self.assertEqual(result["delta"], -3)
        self.assertIn("-3", result["detail"])

    def test_dropdown_fallback_without_runtime_hit_raises(self):
        with patch.object(wt_flow_executor, "_try_dropdown_runtime_fallback", return_value=None):
            with self.assertRaises(RuntimeError):
                wt_flow_executor._execute_fallback_action(
                    "select_dropdown_item_runtime",
                    (10, 10),
                    step_id="s1",
                    action_config={"action": "select_dropdown_item_runtime", "controlId": "dd"},
                )

    def test_fallback_chain_continues_on_unconfirmed_result(self):
        step_definition = {
            "id": "s1",
            "actionConfig": {
                "action": "select_dropdown_item_runtime",
                "controlId": "dd",
                "text": "Target",
                "waitAfter": 0,
            },
            "fallbackChain": [
                {"type": "coordinate", "method": "coordinate", "value": {"x": 10, "y": 10}},
                {"type": "coordinate", "method": "coordinate", "value": {"x": 20, "y": 20}},
                {"type": "coordinate", "method": "coordinate", "value": {"x": 30, "y": 30}},
            ],
        }
        calls = []

        def fake_dropdown_fallback(*args, **kwargs):
            calls.append(kwargs.get("action_config", {}))
            if len(calls) == 1:
                return None
            if len(calls) == 2:
                return {"method": "select_dropdown_item_runtime", "valueConfirmed": False}
            return {
                "method": "select_dropdown_item_runtime",
                "valueConfirmed": True,
                "detail": "ok",
            }

        context = {"step_outputs": {}}
        # 第 3 级成功落值后,P0-5 + 值断言要求 continueWhen 满足才算成功:
        # 这里用命中真值模拟"下拉值确实写入",与 select_dropdown_item_runtime 的
        # 自动值断言(_resolve_continue_when 从 text=Target 生成)配合,链在第 3 级收敛。
        with patch.object(wt_flow_executor, "_GET_STEP_DEFINITION", side_effect=lambda step_id: step_definition):
            with patch.object(wt_flow_executor, "_try_dropdown_runtime_fallback", side_effect=fake_dropdown_fallback):
                with patch.object(wt_flow_executor, "_WAIT_FOR_FLOW_CONTROL_CONDITION", return_value=True):
                    result = wt_flow_executor._try_fallback_chain("s1", context, RuntimeError("main failed"))

        self.assertEqual(len(calls), 3)
        self.assertEqual(result["_fallback_level"], 3)
        self.assertEqual(result["valueConfirmed"], True)

    def test_template_fallback_dropdown_unconfirmed_raises(self):
        step_definition = {
            "id": "s1",
            "actionConfig": {
                "action": "select_dropdown_item_runtime",
                "controlId": "dd",
                "text": "Target",
                "fallbackMode": "template_match",
                "fallbackTemplate": "image_templates/foo.png",
                "waitAfter": 0,
            },
        }
        with patch.object(wt_flow_executor, "_GET_STEP_DEFINITION", side_effect=lambda step_id: step_definition):
            with patch.object(wt_flow_executor, "_LOCATE_TEMPLATE_CENTER_BY_PATH", return_value=(10, 10)):
                with patch.object(wt_flow_executor, "_try_dropdown_runtime_fallback", return_value=None):
                    with self.assertRaises(RuntimeError):
                        wt_flow_executor.run_action_step_with_template_fallback(
                            "s1", {"step_outputs": {}}
                        )

    def test_feedback_concurrent_writes_preserve_both_entries(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            flow_path = os.path.join(tmp_dir, "flow.json")
            with open(flow_path, "w", encoding="utf-8") as f:
                json.dump({"steps": [], "feedbackHistory": []}, f)

            errors = []

            def write_feedback(label):
                try:
                    wt_flow_executor._write_feedback_to_flow(
                        {"flowDefinitionPath": flow_path}, "s1", {"type": label}
                    )
                except Exception as exc:
                    errors.append(exc)

            threads = [
                threading.Thread(target=write_feedback, args=(label,))
                for label in ("alpha", "beta")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            with open(flow_path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(len(saved["feedbackHistory"]), 2)
            self.assertEqual(
                {entry["type"] for entry in saved["feedbackHistory"]},
                {"alpha", "beta"},
            )


if __name__ == "__main__":
    unittest.main()