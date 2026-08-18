# encoding: utf-8
"""审核增强测试：内嵌控件反查、禁用/弱定位/空下拉目标值告警、控件摘要。"""
import unittest

from WT_AUTOMATION_Agent import flow_audit, flow_ops


class FlowAuditEmbeddedTests(unittest.TestCase):
    def test_embedded_control_is_not_reported_missing(self):
        flow = {
            "steps": [{
                "id": "s1",
                "name": "点击",
                "actionConfig": {"action": "click", "controlId": "Custom_1_2"},
                "controls": [{
                    "id": "Custom_1_2",
                    "name": "按钮",
                    "targetValue": "Custom_1_2,Button",
                    "targetMethod": "automation_id",
                }],
            }]
        }

        issues = flow_audit.audit_flow(flow)["issues"]

        self.assertFalse(any("控件库中不存在" in issue["message"] for issue in issues))

    def test_disabled_control_raises_warning(self):
        flow = {
            "steps": [{
                "id": "s2",
                "name": "点击",
                "actionConfig": {"action": "click", "controlId": "DisableBtn"},
                "controls": [{
                    "id": "DisableBtn",
                    "name": "按钮",
                    "isEnabled": "False",
                    "targetValue": "DisableBtn,Button",
                }],
            }]
        }

        issues = flow_audit.audit_flow(flow)["issues"]

        self.assertTrue(any("禁用" in issue["message"] for issue in issues))

    def test_weak_locator_raises_warning(self):
        flow = {
            "steps": [{
                "id": "s3",
                "name": "点击",
                "actionConfig": {"action": "click", "controlId": "Custom_1_2"},
                "controls": [{
                    "id": "Custom_1_2",
                    "name": "按钮",
                    "targetValue": "Custom#[1,2]%(-36.59,-86.59)",
                    "targetMethod": "name",
                }],
            }]
        }

        issues = flow_audit.audit_flow(flow)["issues"]

        self.assertTrue(any("弱定位" in issue["message"] for issue in issues))

    def test_dropdown_without_target_value_raises_warning(self):
        flow = {
            "steps": [{
                "id": "s4",
                "name": "选择",
                "actionConfig": {
                    "action": "select_dropdown_item_runtime",
                    "controlId": "Combo1",
                },
                "controls": [{
                    "id": "Combo1",
                    "name": "下拉框",
                    "targetValue": "Combo1,ComboBox",
                }],
            }]
        }

        issues = flow_audit.audit_flow(flow)["issues"]

        self.assertTrue(any("未指定下拉目标值" in issue["message"] for issue in issues))

    def test_flow_to_text_include_controls(self):
        flow = {
            "steps": [{
                "id": "s5",
                "name": "选择",
                "actionConfig": {"action": "set_combobox", "controlId": "ShiftCombo"},
                "controls": [{
                    "id": "ShiftCombo",
                    "name": "班次下拉框",
                    "functionText": "班次",
                    "helpText": "班次帮助",
                    "controlType": "ComboBox",
                    "automationId": "ShiftCombo",
                    "targetValue": "ShiftCombo,ComboBox",
                    "windowTitle": "主窗口",
                    "notes": "[待确认: 多实例]",
                }],
            }]
        }

        text = flow_ops.flow_to_text(flow, include_controls=True)
        default_text = flow_ops.flow_to_text(flow)

        self.assertIn("控件[0]", text)
        self.assertIn("functionText=班次", text)
        self.assertIn("automationId=ShiftCombo", text)
        self.assertIn("windowTitle=主窗口", text)
        self.assertIn("待确认=是", text)
        self.assertNotIn("控件[0]", default_text)


if __name__ == "__main__":
    unittest.main()
