import json
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_control_index


class StandardCatalogSchemaTests(unittest.TestCase):
    def _write_catalog(self, tmp_dir, payload):
        std_dir = os.path.join(tmp_dir, "standard")
        os.makedirs(std_dir, exist_ok=True)
        catalog_path = os.path.join(std_dir, "standard_control_catalog.json")
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        return catalog_path

    def test_groups_schema_collects_controls_and_builds_index(self):
        payload = {
            "schemaVersion": "standard-catalog-1.1",
            "groups": [
                {
                    "windowTitle": "Main",
                    "frameworkId": "WPF",
                    "controls": [
                        {
                            "name": "OK Button",
                            "controlType": "Button",
                            "className": "Button",
                            "targetMethod": "automation_id,control_type",
                            "targetValue": "OKButton,Button",
                            "automationId": "OKButton",
                            "authority": "high",
                        }
                    ],
                },
                {
                    "windowTitle": "",
                    "frameworkId": "Win32",
                    "controls": [
                        {
                            "name": "Input",
                            "controlType": "Edit",
                            "targetMethod": "control_type",
                            "targetValue": "Edit",
                            "automationId": "",
                        }
                    ],
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            catalog_path = self._write_catalog(tmp_dir, payload)
            controls = wt_control_index._collect_controls_from_catalog(catalog_path)

            self.assertIn("OKButton,Button", controls)
            info = controls["OKButton,Button"]
            self.assertEqual(info["targetMethod"], "automation_id,control_type")
            self.assertEqual(info["windowTitle"], "Main")
            self.assertEqual(info["frameworkId"], "WPF")

            text = wt_control_index.build_control_index_text(
                flow_path=os.path.join(tmp_dir, "missing_flow.json"),
                control_map_dir=tmp_dir,
            )
            self.assertIn('control_id="OKButton,Button"', text)
            self.assertIn("定位方式=automation_id,control_type", text)
            self.assertIn("定位值=OKButton,Button", text)
            self.assertIn('control_id="Edit"', text)

    def test_legacy_flat_schema_remains_supported(self):
        payload = {
            "legacy_control": {
                "name": "Legacy Button",
                "controlType": "Button",
                "className": "Button",
                "targetMethod": "automation_id",
                "targetValue": "LegacyButton,Button",
            }
        }
        with tempfile.TemporaryDirectory() as tmp_dir:
            catalog_path = self._write_catalog(tmp_dir, payload)
            controls = wt_control_index._collect_controls_from_catalog(catalog_path)
            self.assertIn("LegacyButton,Button", controls)
            self.assertEqual(controls["LegacyButton,Button"]["targetMethod"], "automation_id")


if __name__ == "__main__":
    unittest.main()