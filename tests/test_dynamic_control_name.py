# -*- coding: utf-8 -*-
"""验证运行期控件名 ${...} 动态解析（跟随导入文件/设置命名变化的控件）。

核心：运行时把当前步骤 context 放入 _ACTIVE_FLOW_CTX，流程定义取器 _get_flow_step_resolved
对 controls 做 _resolve_dynamic_value 替换，使 'testMESO'/'综合3' 这类写死名可改为占位符，
定位器经 get_flow_control_definition 拿到的即运行时真实名称。
"""
import unittest

import WT_AUT_recorded


def _fake_flow(target_value=None, inspect_name=None):
    tid = target_value if target_value is not None else "${stepParams.气象名},ListViewItem"
    iname = inspect_name if inspect_name is not None else "${stepParams.气象名}"
    return {
        "step_find": {
            "id": "step_find",
            "name": "选择-气象数据",
            "actionType": "action",
            "controls": [
                {
                    "id": "气象数据",
                    "name": tid,
                    "targetMethod": "name,control_type",
                    "targetValue": tid,
                    "windowTitle": "*",
                    "inspectData": {"controlType": "ListItem", "name": iname},
                }
            ],
            "actionConfig": {"action": "click", "controlId": "气象数据"},
        }
    }


class TestDynamicControlName(unittest.TestCase):
    def setUp(self):
        self._orig_flow_def = WT_AUT_recorded._load_flow_definition
        WT_AUT_recorded._load_flow_definition = _fake_flow
        self._orig_active = getattr(WT_AUT_recorded._ACTIVE_FLOW_CTX, "snapshot", None)
        WT_AUT_recorded._ACTIVE_FLOW_CTX.snapshot = None

    def tearDown(self):
        WT_AUT_recorded._load_flow_definition = self._orig_flow_def
        WT_AUT_recorded._ACTIVE_FLOW_CTX.snapshot = self._orig_active

    def _run(self, context, step_id="step_find", active_step="step_find"):
        WT_AUT_recorded._ACTIVE_FLOW_CTX.snapshot = {"step_id": active_step, "context": context}
        try:
            return WT_AUT_recorded._get_flow_step_resolved(step_id)
        finally:
            WT_AUT_recorded._ACTIVE_FLOW_CTX.snapshot = None

    def base_context(self, **over):
        ctx = {
            "step_params": {"step_find": {"气象名": "testMESO_导入"}},
            "step_outputs": {},
            "runtime_config": {},
            "flow_ref_param_stack": [],
        }
        ctx.update(over)
        return ctx

    def test_resolves_step_params_in_control_name(self):
        step = self._run(self.base_context())
        control = step["controls"][0]
        self.assertEqual(control["targetValue"], "testMESO_导入,ListViewItem")
        self.assertEqual(control["name"], "testMESO_导入,ListViewItem")
        self.assertEqual(control["inspectData"]["name"], "testMESO_导入")

    def test_resolves_step_outputs_reference(self):
        # 引用上一步输出：${steps.<stepId>.<output>}
        WT_AUT_recorded._load_flow_definition = lambda: _fake_flow(
            target_value="${steps.step_import.产物名},ListItem"
        )
        ctx = self.base_context(step_params={}, step_outputs={"step_import": {"产物名": "风电场_26机组"}})
        step = self._run(ctx)
        self.assertEqual(step["controls"][0]["targetValue"], "风电场_26机组,ListItem")

    def test_no_active_context_returns_raw(self):
        step = WT_AUT_recorded._get_flow_step_resolved("step_find")
        self.assertEqual(step["controls"][0]["targetValue"], "${stepParams.气象名},ListViewItem")

    def test_does_not_mutate_cached_flow_definition(self):
        self._run(self.base_context())
        raw = WT_AUT_recorded._get_flow_step("step_find")
        self.assertEqual(raw["controls"][0]["targetValue"], "${stepParams.气象名},ListViewItem")

    def test_mismatched_step_id_returns_raw(self):
        # snapshot 属于 step_other，请求 step_find → 不解析
        step = self._run(self.base_context(), active_step="step_other")
        self.assertEqual(step["controls"][0]["targetValue"], "${stepParams.气象名},ListViewItem")

    def test_unknown_param_keeps_literal(self):
        ctx = self.base_context(step_params={"step_find": {}})  # 无"气象名"
        step = self._run(ctx)
        self.assertEqual(step["controls"][0]["targetValue"], "${stepParams.气象名},ListViewItem")


if __name__ == "__main__":
    unittest.main()