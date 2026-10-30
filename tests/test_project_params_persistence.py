"""项目计算参数（Cp 版本等）持久化与运行期补齐的回归测试。

背景：界面上设置 Cp 版本后"不起效"，根因有两类——
1. 参数只存在本机 launcher_state，`<项目>/project.params.json` 此前只读不写，
   换会话/换机器/远程队列 worker 都拿不到人工设置值；
2. 远程队列任务若从非 Simple 入口提交，runtimeConfig 为空，运行期没有可覆盖的值。

修复后约定：
- 保存项目参数时同步写 `<项目>/project.params.json`（原子写，清空即删文件）；
- 运行期（本地/队列 worker 一致）用该文件补齐 runtime 缺失项，只填空缺不覆盖。
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_AUT_recorded
import wt_project_workdir_parser


class SaveProjectParamsTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as work_dir:
            ok, info = wt_project_workdir_parser.save_project_params_to_work_dir(
                work_dir, {"cpVersion": "Cp0.425", "turbineType": "WT6250D220", "radius": "  "}
            )
            self.assertTrue(ok, info)
            params_path = os.path.join(work_dir, "project.params.json")
            self.assertTrue(os.path.isfile(params_path))
            with open(params_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload.get("cpVersion"), "Cp0.425")
            self.assertEqual(payload.get("turbineType"), "WT6250D220")
            self.assertNotIn("radius", payload, "空值不应写入")
            self.assertEqual(
                wt_project_workdir_parser.load_project_params_from_work_dir(work_dir).get("cpVersion"),
                "Cp0.425",
            )

    def test_empty_params_removes_stale_file(self):
        with tempfile.TemporaryDirectory() as work_dir:
            wt_project_workdir_parser.save_project_params_to_work_dir(work_dir, {"cpVersion": "Cp0.425"})
            ok, _info = wt_project_workdir_parser.save_project_params_to_work_dir(work_dir, {})
            self.assertTrue(ok)
            self.assertEqual(wt_project_workdir_parser.load_project_params_from_work_dir(work_dir), {})

    def test_missing_work_dir_reports_failure(self):
        ok, message = wt_project_workdir_parser.save_project_params_to_work_dir(
            os.path.join(tempfile.gettempdir(), "wt_not_exists_dir_12345"), {"cpVersion": "Cp0.425"}
        )
        self.assertFalse(ok)
        self.assertIn("不存在", message)


class FillRuntimeFromProjectParamsTests(unittest.TestCase):
    def _prepare(self, work_dir, params):
        wt_project_workdir_parser.save_project_params_to_work_dir(work_dir, params)

    def test_fills_missing_cp_version(self):
        with tempfile.TemporaryDirectory() as work_dir:
            self._prepare(work_dir, {"cpVersion": "Cp0.425", "turbineType": "WT6250D220_A.4"})
            runtime = {"projectWorkDir": work_dir}
            with patch.object(WT_AUT_recorded, "log_step"):
                filled = WT_AUT_recorded._fill_runtime_from_project_params(runtime)
            self.assertEqual(filled.get("cpVersion"), "Cp0.425")
            self.assertEqual(filled.get("turbineType"), "WT6250D220_A.4")

    def test_existing_values_win(self):
        with tempfile.TemporaryDirectory() as work_dir:
            self._prepare(work_dir, {"cpVersion": "Cp0.425"})
            runtime = {"projectWorkDir": work_dir, "cpVersion": "Cp0.429"}
            with patch.object(WT_AUT_recorded, "log_step"):
                filled = WT_AUT_recorded._fill_runtime_from_project_params(runtime)
            self.assertEqual(filled.get("cpVersion"), "Cp0.429")

    def test_no_work_dir_is_noop(self):
        runtime = {"cpVersion": ""}
        filled = WT_AUT_recorded._fill_runtime_from_project_params(runtime)
        self.assertEqual(filled, {"cpVersion": ""})

    def test_missing_file_keeps_runtime(self):
        with tempfile.TemporaryDirectory() as work_dir:
            runtime = {"projectWorkDir": work_dir}
            with patch.object(WT_AUT_recorded, "log_step"):
                filled = WT_AUT_recorded._fill_runtime_from_project_params(runtime)
            self.assertNotIn("cpVersion", filled)


if __name__ == "__main__":
    unittest.main()
