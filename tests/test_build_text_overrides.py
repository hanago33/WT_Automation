# encoding: utf-8
"""针对总纲 §4-A6 的单元测试：build_text_overrides 槽位化覆盖映射与多槽位冲突拒启。

验收标准（§4-A6）：
1. 覆盖表按槽位而不是 runtime 键配对：
   - mastCoordFilePath 只与坐标槽配对；
   - mastImportFilePath 只与时序槽配对；
2. 构造同时含坐标与时序字面量的流程，映射不得交叉；
3. 同一旧文本命中多个槽位时拒绝启动（fail-closed），不再静默选一个（抛出 ValueError）。
"""
import json
import os
import tempfile
import unittest

import wt_project_workdir_parser as P


class BuildTextOverridesSlotwiseTests(unittest.TestCase):
    """A6 验收测试集：槽位化配对与防串槽。"""

    def _create_temp_flow(self, steps_texts, runtime_config=None):
        payload = {
            "version": "1.0",
            "runtimeConfig": runtime_config or {},
            "steps": [
                {
                    "id": f"step_{i}",
                    "actionConfig": {"text": text}
                }
                for i, text in enumerate(steps_texts)
            ]
        }
        fd, path = tempfile.mkstemp(prefix="flow_test_a6_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            return path
        except Exception:
            try:
                os.remove(path)
            except OSError:
                pass
            raise

    def test_simultaneous_coord_and_tim_literals_do_not_cross(self):
        """验收 1：构造同时含坐标与时序字面量的流程，映射不得交叉。"""
        coord_literal = r"C:\old_project\03-WT输入\01-测风塔及机位点坐标\CFT_Project_01.txt"
        tim_literal = r"C:\old_project\03-WT输入\03-测风塔数据\C1831\1831-20230402-20240524-tim.txt"

        flow_path = self._create_temp_flow([coord_literal, tim_literal])
        self.addCleanup(lambda: os.remove(flow_path) if os.path.exists(flow_path) else None)

        rc = {
            "mastCoordFilePath": r"D:\new_project\03-WT输入\CFT_New_99.txt",
            "mastImportFilePath": r"D:\new_project\03-WT输入\New_10m_tim.txt",
        }

        overrides = P.build_text_overrides(flow_path, rc)

        # 坐标字面量必须映射到 mastCoordFilePath，不得映射到 mastImportFilePath
        self.assertEqual(overrides.get(coord_literal), rc["mastCoordFilePath"])
        self.assertNotEqual(overrides.get(coord_literal), rc["mastImportFilePath"])

        # 时序字面量必须映射到 mastImportFilePath，不得映射到 mastCoordFilePath
        self.assertEqual(overrides.get(tim_literal), rc["mastImportFilePath"])
        self.assertNotEqual(overrides.get(tim_literal), rc["mastCoordFilePath"])

    def test_multiple_slots_hit_file_raises_error(self):
        """验收 2：同一旧文本命中多个槽位（如兼具 CFT 与 tim 语义）即报错拒绝启动（fail-closed）。"""
        ambiguous_literal = r"C:\old_project\CFT_1831_tim.txt"
        flow_path = self._create_temp_flow([ambiguous_literal])
        self.addCleanup(lambda: os.remove(flow_path) if os.path.exists(flow_path) else None)

        rc = {
            "mastCoordFilePath": r"D:\new\CFT_New.txt",
            "mastImportFilePath": r"D:\new\New_tim.txt",
        }

        with self.assertRaises(ValueError) as ctx:
            P.build_text_overrides(flow_path, rc)

        err_msg = str(ctx.exception)
        self.assertIn("同时命中多个槽位", err_msg)
        self.assertIn("fail-closed", err_msg)

    def test_multiple_slots_hit_basename_raises_error(self):
        """验收 3：文件名主体（无扩展名）若命中多个槽位同样报错拒绝启动。"""
        ambiguous_base = "CFT_tim"
        flow_path = self._create_temp_flow([ambiguous_base])
        self.addCleanup(lambda: os.remove(flow_path) if os.path.exists(flow_path) else None)

        rc = {
            "mastCoordFilePath": r"D:\new\CFT_New.txt",
            "mastImportFilePath": r"D:\new\New_tim.txt",
        }

        with self.assertRaises(ValueError) as ctx:
            P.build_text_overrides(flow_path, rc)

        err_msg = str(ctx.exception)
        self.assertIn("同时命中多个槽位", err_msg)

    def test_shared_old_text_in_runtime_config_raises_error(self):
        """验收 4：若旧流程 runtimeConfig 中将同一路径同时绑定到坐标槽与时序槽，判定多槽冲突报错。"""
        shared_path = r"C:\data\common_data.txt"
        old_runtime = {
            "mastCoordFilePath": shared_path,
            "mastImportFilePath": shared_path,
        }
        flow_path = self._create_temp_flow([shared_path], runtime_config=old_runtime)
        self.addCleanup(lambda: os.remove(flow_path) if os.path.exists(flow_path) else None)

        rc = {
            "mastCoordFilePath": r"D:\new\CFT_New.txt",
            "mastImportFilePath": r"D:\new\New_tim.txt",
        }

        with self.assertRaises(ValueError) as ctx:
            P.build_text_overrides(flow_path, rc)

        self.assertIn("同时命中多个槽位", str(ctx.exception))

    def test_element_flow_cft_never_mapped_to_tim(self):
        """防回归（§4-A6 实证）：导入并配置元素.json 中旧 mastImportFilePath 是 CFT，修复后绝不映射为 tim。"""
        flow_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "flow_packages",
            "flow_definition_导入并配置元素.json"
        )
        if not os.path.isfile(flow_path):
            self.skipTest("flow_definition_导入并配置元素.json 不存在")

        old_cft = r"C:\Users\14830\Desktop\20241227黑龙江依兰泰霆项目\03-WT输入\CFT_Project_43.txt"
        new_cft = r"D:\target\CFT_New_Target.txt"
        new_tim = r"D:\target\New_Target_10m_tim.txt"

        rc = {
            "mastCoordFilePath": new_cft,
            "mastImportFilePath": new_tim,
        }

        overrides = P.build_text_overrides(flow_path, rc)

        # CFT 文件旧路径必须且仅能替换为 new_cft，绝不得被替换为 new_tim
        self.assertEqual(overrides.get(old_cft), new_cft)
        self.assertNotEqual(overrides.get(old_cft), new_tim)

        # CFT 文件名主体也绝不得替换为 tim 文件名主体
        self.assertEqual(overrides.get("CFT_Project_43"), "CFT_New_Target")
        self.assertNotEqual(overrides.get("CFT_Project_43"), "New_Target_10m_tim")

    def test_turbine_coord_and_curve_slot_separation(self):
        """扩展槽位隔离：机位坐标槽（JWD）与功率曲线槽（wtg）亦精确隔离。"""
        jwd_literal = r"C:\old\JWD_Project_01.txt"
        wtg_literal = r"C:\old\Turbine_Power.wtg"

        flow_path = self._create_temp_flow([jwd_literal, wtg_literal])
        self.addCleanup(lambda: os.remove(flow_path) if os.path.exists(flow_path) else None)

        rc = {
            "turbineCoordFilePath": r"D:\new\JWD_New.txt",
            "turbineImportFilePath": r"D:\new\New_Curve.wtg",
        }

        overrides = P.build_text_overrides(flow_path, rc)
        self.assertEqual(overrides.get(jwd_literal), rc["turbineCoordFilePath"])
        self.assertEqual(overrides.get(wtg_literal), rc["turbineImportFilePath"])


if __name__ == "__main__":
    unittest.main()
