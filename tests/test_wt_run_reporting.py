import json
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_run_reporting


class RunReportingTests(unittest.TestCase):
    def test_finalize_run_report_writes_summary_and_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wt_run_reporting.configure_run_reporting(base_dir=temp_dir, log_step=lambda message: None)
            report = wt_run_reporting.start_run_report(["step_a", "step_b"], {"gmExe": "demo.exe"})
            wt_run_reporting.report_step_result(
                report,
                "step_a",
                "步骤A",
                "success",
                action_type="action",
                strategy="action",
                elapsed=1.25,
                extra={"fallbackTemplateUsed": "demo.png"},
            )
            wt_run_reporting.report_step_result(
                report,
                "step_b",
                "步骤B",
                "skipped",
                action_type="script",
                strategy="script",
                elapsed=0.5,
                error="步骤已停用",
            )

            report_path = wt_run_reporting.finalize_run_report(report, "success")

            self.assertTrue(os.path.exists(report_path))
            self.assertTrue(os.path.exists(os.path.join(temp_dir, "logs", "last_run_report.json")))
            self.assertEqual(report["summary"]["requestedCount"], 2)
            self.assertEqual(report["summary"]["executedCount"], 2)
            self.assertEqual(report["summary"]["fallbackCount"], 1)
            self.assertAlmostEqual(report["summary"]["totalElapsedSeconds"], 1.75, places=2)

            with open(report_path, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)

            self.assertEqual(payload["status"], "success")
            self.assertEqual(payload["reportPath"], report_path)
            self.assertTrue(payload["lastReportPath"].endswith("last_run_report.json"))

    def test_finalize_run_report_marks_partial_success_when_failed_steps_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            wt_run_reporting.configure_run_reporting(base_dir=temp_dir, log_step=lambda message: None)
            report = wt_run_reporting.start_run_report(["step_a", "step_b"], {"gmExe": "demo.exe"})
            wt_run_reporting.report_step_result(
                report,
                "step_a",
                "步骤A",
                "success",
                action_type="action",
                strategy="action",
                elapsed=1.0,
            )
            wt_run_reporting.report_step_result(
                report,
                "step_b",
                "步骤B",
                "failed",
                action_type="action",
                strategy="action",
                elapsed=0.5,
                error="步骤失败但允许继续",
            )

            report_path = wt_run_reporting.finalize_run_report(report, "success")

            with open(report_path, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)

            self.assertEqual(report["status"], "partial_success")
            self.assertEqual(payload["status"], "partial_success")
            self.assertEqual(payload["summary"]["failedCount"], 1)


if __name__ == "__main__":
    unittest.main()
