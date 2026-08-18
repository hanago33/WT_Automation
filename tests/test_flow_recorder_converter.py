import unittest

import flow_recorder_converter


class FlowRecorderConverterTests(unittest.TestCase):
    def test_infer_runtime_binding_for_projection_file(self):
        binding = flow_recorder_converter._infer_runtime_binding(r"D:\demo\sample.prj")
        self.assertIsNotNone(binding)
        self.assertEqual(binding["runtimeKey"], "projectionFilePath")

    def test_apply_runtime_binding_updates_step(self):
        step = {
            "stepParams": {},
            "notes": "",
        }
        binding = {
            "runtimeKey": "sourceFilePath",
            "placeholder": "${runtime.sourceFilePath}",
        }
        stats = {"runtimeParamBindings": 0}
        flow_recorder_converter._apply_runtime_binding_to_step(step, binding, r"D:\data\site.dwg", 12, stats=stats)
        self.assertEqual(step["stepParams"]["runtimeBindings"][0]["key"], "sourceFilePath")
        self.assertEqual(stats["runtimeParamBindings"], 1)

    def test_parse_segment_cleans_coordinate_suffix_then_index(self):
        parsed = flow_recorder_converter._parse_segment("||Custom#[1,2]%(-36.59,-86.59)")
        self.assertEqual(parsed["controlType"], "Custom")
        self.assertEqual(parsed["coords"], {"x": -36.59, "y": -86.59})
        self.assertEqual(
            flow_recorder_converter._extract_segment_found_index("||Custom#[1,2]%(-36.59,-86.59)"),
            2,
        )

    def test_parse_segment_keeps_normal_segment(self):
        parsed = flow_recorder_converter._parse_segment("保存||Button")
        self.assertEqual(parsed["name"], "保存")
        self.assertEqual(parsed["controlType"], "Button")
        self.assertIsNone(parsed["coords"])


if __name__ == "__main__":
    unittest.main()
