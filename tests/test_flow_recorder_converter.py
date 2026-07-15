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


if __name__ == "__main__":
    unittest.main()
