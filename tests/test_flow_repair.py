# encoding: utf-8
"""flow_repair 共享修复层测试：automationId 回填、名称清洗、多实例与 schema 补齐。"""
import json
import tempfile
import unittest
from pathlib import Path

from WT_AUTOMATION_Agent import flow_repair


class FlowRepairTests(unittest.TestCase):
    def _write_master(self, records):
        path = Path(tempfile.mkdtemp()) / "总控件信息.json"
        path.write_text(json.dumps({"flatControls": records}, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def test_automation_id_backfill_and_name_cleanup(self):
        master = self._write_master([{
            "automationId": "BtnSave",
            "recommendedTargetMethod": "automation_id",
            "recommendedTargetValue": "BtnSave,Button",
            "helpText": "保存帮助",
            "functionText": "保存",
            "uiPath": "窗口->保存||Button",
            "labelText": "保存",
        }])
        flow = {
            "steps": [{
                "id": "step_1",
                "name": "点击 保存%(-1,-2)",
                "actionConfig": {"action": "click", "controlId": "ctrl_old"},
                "controls": [{
                    "id": "ctrl_1",
                    "name": "M 1.5 1.5 L 10 20",
                    "inspectData": {"automationId": "BtnSave"},
                }],
            }]
        }

        repaired, report = flow_repair.repair_flow_definition(flow, master)
        control = repaired["steps"][0]["controls"][0]
        step = repaired["steps"][0]

        self.assertEqual(control["targetMethod"], "automation_id")
        self.assertEqual(control["targetValue"], "BtnSave,Button")
        self.assertEqual(control["helpText"], "保存帮助")
        self.assertEqual(control["functionText"], "保存")
        self.assertEqual(control["name"], "保存")
        self.assertEqual(step["actionConfig"]["controlId"], "ctrl_1")
        self.assertEqual(step["name"], "点击 保存")
        self.assertGreaterEqual(report["auto_fixed_count"], 4)

    def test_multi_instance_aid_is_not_auto_guessed(self):
        master = self._write_master([
            {"automationId": "MultiAid", "labelText": "甲", "recommendedTargetValue": "A,Button"},
            {"automationId": "MultiAid", "labelText": "乙", "recommendedTargetValue": "B,Button"},
        ])
        flow = {
            "steps": [{
                "id": "step_2",
                "name": "点击",
                "actionConfig": {"action": "click", "controlId": "ctrl_2"},
                "controls": [{
                    "id": "ctrl_2",
                    "name": "按钮",
                    "inspectData": {"automationId": "MultiAid"},
                }],
            }]
        }

        repaired, report = flow_repair.repair_flow_definition(flow, master)
        control = repaired["steps"][0]["controls"][0]
        self.assertEqual(control.get("targetValue", ""), "")
        self.assertTrue(any(
            item.get("category") == "control" and "多实例" in item.get("message", "")
            for item in report["pending_confirm"]
        ))

    def test_schema_input_fields_filled_for_combobox(self):
        master = self._write_master([{
            "automationId": "Combo1",
            "recommendedTargetValue": "选项B",
            "targetMethod": "automation_id",
            "targetValue": "Combo1,ComboBox",
        }])
        flow = {
            "steps": [{
                "id": "step_3",
                "name": "选择",
                "actionConfig": {"action": "set_combobox", "controlId": "ctrl_3"},
                "controls": [{
                    "id": "ctrl_3",
                    "name": "下拉框",
                    "inspectData": {
                        "automationId": "Combo1",
                        "recommendedTargetValue": "选项B",
                    },
                }],
            }]
        }

        repaired, _report = flow_repair.repair_flow_definition(flow, master)
        ac = repaired["steps"][0]["actionConfig"]
        self.assertEqual(ac["recommendedTargetValue"], "选项B")
        self.assertEqual(ac["value"], "选项B")
        self.assertEqual(ac["text"], "选项B")

    def test_save_with_backup_writes_bak_then_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "flow.json"
            path.write_text(json.dumps({"steps": [{"id": "old"}]}, ensure_ascii=False), encoding="utf-8")
            new_flow = {"steps": [{"id": "new"}]}

            backup = flow_repair.save_with_backup(str(path), new_flow, {"summary": "ok"})

            self.assertTrue(Path(backup).is_file())
            self.assertEqual(json.loads(Path(backup).read_text(encoding="utf-8")), {"steps": [{"id": "old"}]})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), new_flow)


if __name__ == "__main__":
    unittest.main()
