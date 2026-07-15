import json
import os
import sys
import tempfile
import unittest

try:
    import openpyxl  # noqa: F401
except Exception:  # pragma: no cover
    openpyxl = None

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from flow_excel_io import (
    ROUNDTRIP_AUDIT_BASELINE_PATH_FIELD,
    ROUNDTRIP_AUDIT_STEP_IDS_FIELD,
    audit_flow_excel_roundtrip,
    export_flow_to_excel,
    import_flow_from_excel,
    load_flow_excel_meta,
    load_flow_payload_from_excel,
)


@unittest.skipIf(openpyxl is None, "openpyxl 未安装，跳过 Excel 导入导出测试")
class FlowExcelIoTests(unittest.TestCase):
    def test_export_then_import_preserves_engineering_columns(self):
        payload = {
            "version": "1.0",
            "project": "WT_Automation",
            "description": "excel roundtrip",
            "runtimeConfig": {
                "gmExe": r"C:\demo\wt.exe",
                "sourceFilePath": r"C:\demo\source.csv",
                "outputDir": r"C:\demo\output",
                "projectionFilePath": r"C:\demo\projection.prj",
            },
            "flowPackages": [
                {
                    "id": "pkg_demo",
                    "name": "演示流程包",
                    "description": "demo",
                    "stepIds": ["step_1"],
                }
            ],
            "steps": [
                {
                    "id": "step_1",
                    "name": "父窗口点击",
                    "stage": "demo",
                    "strategy": "action",
                    "actionType": "action",
                    "topLevel": True,
                    "enabled": True,
                    "packageRef": "pkg_demo",
                    "windowTitle": "导入时间序列文件",
                    "successLog": "完成",
                    "description": "demo step",
                    "notes": "demo notes",
                    "inspectHints": {
                        "controlName": "添加到数据",
                        "className": "Button",
                        "automationId": "btn_add",
                        "controlType": "Button",
                        "uiPath": "Window/Button[1]",
                        "templateKey": "projection.add_data",
                    },
                    "stepParams": {"demo": "value"},
                    "actionConfig": {
                        "action": "type_text_relative",
                        "timeoutSeconds": 12,
                        "waitBefore": 0.5,
                        "waitAfter": 1.2,
                        "postInputKeys": "{TAB}",
                        "continueWhen": {
                            "controlId": "control_add_data",
                            "condition": "visible",
                            "timeoutSeconds": 4,
                            "windowTitleHint": "导入时间序列文件",
                        },
                        "retryCount": 2,
                        "retryInterval": 0.8,
                        "onError": "fallback",
                        "fallbackMode": "template_match",
                        "fallbackTemplate": r"image_templates\Icons\projection\添加到数据.png",
                        "parentWindow": {
                            "title": "导入时间序列文件",
                            "className": "Window",
                            "frameworkId": "WPF",
                        },
                        "relativeRegion": {
                            "x": 0.87,
                            "y": 0.94,
                            "width": 0.05,
                            "height": 0.03,
                            "anchor": "center",
                        },
                    },
                    "controls": [
                        {
                            "id": "control_add_data",
                            "name": "添加到数据",
                            "role": "Button",
                            "targetMethod": "name,class_name",
                            "targetValue": "添加到数据,Button",
                        }
                    ],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = os.path.join(temp_dir, "flow_definition.json")
            xlsx_path = os.path.join(temp_dir, "flow_steps.xlsx")
            imported_json_path = os.path.join(temp_dir, "flow_imported.json")
            with open(json_path, "w", encoding="utf-8") as file_obj:
                json.dump(payload, file_obj, ensure_ascii=False, indent=2)

            export_flow_to_excel(json_path, xlsx_path)
            import_flow_from_excel(xlsx_path, imported_json_path)

            with open(imported_json_path, "r", encoding="utf-8") as file_obj:
                imported_payload = json.load(file_obj)

        self.assertEqual(imported_payload["runtimeConfig"]["gmExe"], payload["runtimeConfig"]["gmExe"])
        self.assertEqual(imported_payload["flowPackages"][0]["id"], "pkg_demo")
        step = imported_payload["steps"][0]
        self.assertEqual(step["id"], "step_1")
        self.assertEqual(step["windowTitle"], "导入时间序列文件")
        self.assertEqual(step["inspectHints"]["controlName"], "添加到数据")
        self.assertEqual(step["actionConfig"]["action"], "type_text_relative")
        self.assertEqual(step["actionConfig"]["postInputKeys"], "{TAB}")
        self.assertEqual(step["actionConfig"]["fallbackMode"], "template_match")
        self.assertEqual(
            step["actionConfig"]["fallbackTemplate"],
            r"image_templates\Icons\projection\添加到数据.png",
        )
        self.assertEqual(step["actionConfig"]["continueWhen"]["controlId"], "control_add_data")
        self.assertEqual(step["actionConfig"]["continueWhen"]["condition"], "visible")
        self.assertEqual(step["actionConfig"]["continueWhen"]["windowTitleHint"], "导入时间序列文件")
        self.assertEqual(step["actionConfig"]["continueWhen"]["timeoutSeconds"], 4)
        self.assertEqual(step["actionConfig"]["parentWindow"]["title"], "导入时间序列文件")
        self.assertEqual(step["actionConfig"]["parentWindow"]["frameworkId"], "WPF")
        self.assertEqual(step["actionConfig"]["relativeRegion"]["anchor"], "center")
        self.assertEqual(step["controls"][0]["targetMethod"], "name,class_name")

    def test_export_selected_steps_only(self):
        payload = {
            "version": "1.0",
            "project": "WT_Automation",
            "description": "subset export",
            "runtimeConfig": {},
            "flowPackages": [
                {
                    "id": "pkg_demo",
                    "name": "演示流程包",
                    "description": "demo",
                    "stepIds": ["step_1", "step_2"],
                }
            ],
            "steps": [
                {
                    "id": "step_1",
                    "name": "步骤一",
                    "strategy": "action",
                    "actionType": "action",
                    "enabled": True,
                    "packageRef": "pkg_demo",
                    "actionConfig": {"action": "click", "controlId": "control_one"},
                },
                {
                    "id": "step_2",
                    "name": "步骤二",
                    "strategy": "action",
                    "actionType": "action",
                    "enabled": True,
                    "packageRef": "pkg_demo",
                    "actionConfig": {"action": "click", "controlId": "control_two"},
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = os.path.join(temp_dir, "flow_definition.json")
            xlsx_path = os.path.join(temp_dir, "flow_steps.xlsx")
            imported_json_path = os.path.join(temp_dir, "flow_imported.json")
            with open(json_path, "w", encoding="utf-8") as file_obj:
                json.dump(payload, file_obj, ensure_ascii=False, indent=2)

            export_flow_to_excel(json_path, xlsx_path, selected_step_ids=["step_2"])
            import_flow_from_excel(xlsx_path, imported_json_path)

            with open(imported_json_path, "r", encoding="utf-8") as file_obj:
                imported_payload = json.load(file_obj)

        self.assertEqual([step["id"] for step in imported_payload["steps"]], ["step_2"])
        self.assertEqual(imported_payload["flowPackages"][0]["stepIds"], ["step_2"])

    def test_export_writes_roundtrip_audit_meta_and_audit_passes(self):
        payload = {
            "version": "1.0",
            "project": "WT_Automation",
            "description": "audit meta",
            "runtimeConfig": {},
            "flowPackages": [],
            "steps": [
                {
                    "id": "step_16",
                    "name": "输入-默认高度",
                    "strategy": "action",
                    "actionType": "action",
                    "enabled": True,
                    "windowTitle": "导入时间序列文件",
                    "actionConfig": {
                        "action": "type_text_relative",
                        "fallbackMode": "template_match",
                        "fallbackTemplate": r"image_templates\Icons\projection\添加到数据.png",
                        "continueWhen": {
                            "controlId": "control_done",
                            "condition": "visible",
                            "timeoutSeconds": 4,
                        },
                        "parentWindow": {
                            "title": "导入时间序列文件",
                            "className": "Window",
                            "frameworkId": "WPF",
                        },
                        "relativeRegion": {
                            "x": 0.5,
                            "y": 0.5,
                            "width": 0.1,
                            "height": 0.05,
                        },
                    },
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = os.path.join(temp_dir, "flow_definition.json")
            xlsx_path = os.path.join(temp_dir, "flow_steps.xlsx")
            with open(json_path, "w", encoding="utf-8") as file_obj:
                json.dump(payload, file_obj, ensure_ascii=False, indent=2)

            export_flow_to_excel(json_path, xlsx_path)
            meta = load_flow_excel_meta(xlsx_path)
            imported_payload = load_flow_payload_from_excel(xlsx_path)
            audit_result = audit_flow_excel_roundtrip(xlsx_path, candidate_payload=imported_payload)

        self.assertEqual(meta[ROUNDTRIP_AUDIT_BASELINE_PATH_FIELD], os.path.abspath(json_path))
        self.assertEqual(json.loads(meta[ROUNDTRIP_AUDIT_STEP_IDS_FIELD]), ["step_16"])
        self.assertTrue(audit_result["available"])
        self.assertFalse(audit_result["hasIssues"])
        self.assertEqual(audit_result["stepIds"], ["step_16"])

    def test_roundtrip_audit_uses_exported_subset_step_ids(self):
        payload = {
            "version": "1.0",
            "project": "WT_Automation",
            "description": "subset audit",
            "runtimeConfig": {},
            "flowPackages": [],
            "steps": [
                {
                    "id": "step_1",
                    "name": "稳定步骤",
                    "strategy": "action",
                    "actionType": "action",
                    "enabled": True,
                    "windowTitle": "窗口A",
                    "actionConfig": {
                        "action": "click_relative_region",
                        "parentWindow": {"title": "窗口A", "className": "Window", "frameworkId": "WPF"},
                        "relativeRegion": {"x": 0.2, "y": 0.3, "width": 0.1, "height": 0.1},
                    },
                },
                {
                    "id": "step_2",
                    "name": "目标步骤",
                    "strategy": "action",
                    "actionType": "action",
                    "enabled": True,
                    "windowTitle": "窗口B",
                    "actionConfig": {
                        "action": "type_text_relative",
                        "parentWindow": {"title": "窗口B", "className": "Window", "frameworkId": "WPF"},
                        "relativeRegion": {"x": 0.4, "y": 0.5, "width": 0.1, "height": 0.1},
                    },
                },
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = os.path.join(temp_dir, "flow_definition.json")
            xlsx_path = os.path.join(temp_dir, "flow_steps.xlsx")
            with open(json_path, "w", encoding="utf-8") as file_obj:
                json.dump(payload, file_obj, ensure_ascii=False, indent=2)

            export_flow_to_excel(json_path, xlsx_path, selected_step_ids=["step_2"])
            imported_payload = load_flow_payload_from_excel(xlsx_path)
            imported_payload["steps"][0]["windowTitle"] = "窗口B-已修改"
            audit_result = audit_flow_excel_roundtrip(xlsx_path, candidate_payload=imported_payload)

        self.assertTrue(audit_result["available"])
        self.assertEqual(audit_result["stepIds"], ["step_2"])
        self.assertTrue(audit_result["hasIssues"])
        self.assertEqual(len(audit_result["issues"]), 1)
        self.assertEqual(audit_result["issues"][0]["stepId"], "step_2")
        self.assertEqual(audit_result["issues"][0]["field"], "windowTitle")

    def test_import_preserves_post_input_keys_for_non_relative_input_actions(self):
        payload = {
            "version": "1.0",
            "project": "WT_Automation",
            "description": "post input keys",
            "runtimeConfig": {},
            "flowPackages": [],
            "steps": [
                {
                    "id": "step_type_text",
                    "name": "普通输入",
                    "strategy": "action",
                    "actionType": "action",
                    "enabled": True,
                    "actionConfig": {
                        "action": "type_text",
                        "controlId": "control_input",
                        "text": "demo",
                        "postInputKeys": "{ENTER}",
                    },
                    "controls": [
                        {
                            "id": "control_input",
                            "name": "输入框",
                            "role": "Edit",
                            "targetMethod": "automation_id",
                            "targetValue": "InputBox",
                        }
                    ],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = os.path.join(temp_dir, "flow_definition.json")
            xlsx_path = os.path.join(temp_dir, "flow_steps.xlsx")
            imported_json_path = os.path.join(temp_dir, "flow_imported.json")
            with open(json_path, "w", encoding="utf-8") as file_obj:
                json.dump(payload, file_obj, ensure_ascii=False, indent=2)

            export_flow_to_excel(json_path, xlsx_path)
            import_flow_from_excel(xlsx_path, imported_json_path)

            with open(imported_json_path, "r", encoding="utf-8") as file_obj:
                imported_payload = json.load(file_obj)

        self.assertEqual(imported_payload["steps"][0]["actionConfig"]["postInputKeys"], "{ENTER}")


if __name__ == "__main__":
    unittest.main()
