# encoding: utf-8
"""mup_element_import 纯函数测试（不写库）。

数据库写测只在副本库做（见 99_synthesis/2026-11-04-element-datalayer-zcode.md）。
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mup_weather_import as mw
import mup_element_import as el


class TestParseElementArg(unittest.TestCase):
    def test_mast_four_parts(self):
        spec, err = el.parse_element_arg("mast", "C1831, 43555698, 5133880.9, 125")
        self.assertIsNone(err)
        self.assertEqual(spec.label, "C1831")
        self.assertAlmostEqual(spec.x, 43555698.0)
        self.assertAlmostEqual(spec.z, 125.0)
        self.assertIsNone(spec.wtg)

    def test_turbine_with_and_without_wtg(self):
        spec, err = el.parse_element_arg("turbine", "JWD1,1,2,3,WT6250D220")
        self.assertIsNone(err)
        self.assertEqual(spec.wtg, "WT6250D220")
        spec2, err2 = el.parse_element_arg("turbine", "JWD1,1,2,3")
        self.assertIsNone(err2)
        self.assertIsNone(spec2.wtg)

    def test_result_requires_coords(self):
        spec, err = el.parse_element_arg("result", "Mapping(1)")
        self.assertIsNone(spec)
        self.assertIn("4 段", err)
        spec2, err2 = el.parse_element_arg("result", "R1,43556000,5133900,125")
        self.assertIsNone(err2)
        self.assertEqual(spec2.label, "R1")
        self.assertAlmostEqual(spec2.x, 43556000.0)
        self.assertAlmostEqual(spec2.z, 125.0)

    def test_bad_arity(self):
        self.assertIsNotNone(el.parse_element_arg("mast", "A,1,2")[1])
        self.assertIsNotNone(el.parse_element_arg("turbine", "A,1,2,3,4,5")[1])
        self.assertIsNotNone(el.parse_element_arg("result", "A,1,2")[1])

    def test_bad_coord(self):
        self.assertIsNotNone(el.parse_element_arg("mast", "A,x,y,z")[1])

    def test_spec_validate(self):
        ok = el.ElementSpec(kind="mast", label="M", x=1.0, y=2.0, z=3.0)
        self.assertEqual(ok.validate(), [])
        bad = el.ElementSpec(kind="mast", label="", x=None, y=None, z=None)
        self.assertEqual(len(bad.validate()), 2)


def _patch_queries(steps):
    """按调用序返回预设行；steps: list[list[row]]。"""
    it = iter(steps)
    return mock.patch.object(mw, "_query_rows", side_effect=lambda db, sql: next(it))


class TestBuildPlan(unittest.TestCase):
    def _make_specs(self, with_result: bool = False):
        specs = [
            el.ElementSpec(kind="mast", label="ZM1", x=43555698.0, y=5133880.9, z=125.0),
            el.ElementSpec(kind="turbine", label="ZW1", x=43557986.5, y=5136125.6, z=125.0,
                           wtg="WT6250D220"),
        ]
        if with_result:
            specs.append(el.ElementSpec(kind="result", label="ZR1",
                                        x=43556000.0, y=5133900.0, z=120.0))
        return specs

    def test_plan_sql_shape_and_dependency_order(self):
        steps = [
            [["site-1", "test"]],          # Sites
            [["case-1"]],                  # Cases
            [["cfg-1"]],                   # Configurations
            [],                            # existing labels
            [["wtg-1"]],                   # WTType
        ]
        with _patch_queries(steps):
            result = el.build_plan(self._make_specs(with_result=True),
                                   "MUPLocalDb_element_verify", site="test",
                                   allow_unverified_result=True)
        self.assertTrue(result.ok, result.message)
        plan = result.data["plan_obj"]
        sql = plan.sql
        self.assertEqual(sql.count("INSERT INTO dbo.Elements"), 3)
        self.assertEqual(sql.count("INSERT INTO dbo.VertProfiles"), 1)
        self.assertEqual(sql.count("INSERT INTO dbo.Configuration_Element"), 3)
        self.assertIn("MTDElementMast", sql)
        self.assertIn("MTDElementWindTurbine", sql)
        self.assertIn("MTDElementPoint", sql)
        self.assertNotIn("MTDElementMapping", sql)   # 绘图归 mup_mapping_import.py
        self.assertNotIn("MTDElementPointBase", sql) # 防前缀误写
        # 风机金样列：HZCoordType=1、直径 0/PosRes 0
        self.assertIn(",1,NULL,0.0,0,N'MTDElementWindTurbine'", sql)
        mast_row = [ln for ln in sql.splitlines() if "N'ZM1'" in ln][0]
        self.assertIn(",125.0,1,NULL,NULL,NULL,N'MTDElementMast'", mast_row)
        self.assertIn("VALUES (", sql)
        self.assertIn("BEGIN TRANSACTION", sql)
        # 回滚逆序：Configuration_Element → VertProfiles → Elements
        rb = plan.rollback_sql
        self.assertLess(rb.index("DELETE FROM dbo.Configuration_Element"),
                        rb.index("DELETE FROM dbo.VertProfiles"))
        self.assertLess(rb.index("DELETE FROM dbo.VertProfiles"),
                        rb.index("DELETE FROM dbo.Elements"))
        # 结果点：MTDElementPoint + 坐标齐全 + HZCoordType=1 + Quad_* 不写
        result_row = [ln for ln in sql.splitlines() if "N'ZR1'" in ln][0]
        self.assertIn("N'MTDElementPoint'", result_row)
        self.assertIn(",120.0,1,NULL,NULL,NULL,N'MTDElementPoint'", result_row)
        # E-P1-2：风机 VP 占位默认 = 主导金样 20/200/20
        vp_row = [ln for ln in sql.splitlines() if "INSERT INTO dbo.VertProfiles" in ln][0]
        self.assertIn(",20.0,200.0,20.0,", vp_row)
        self.assertNotIn(",0.0,0.0,0.0,", vp_row)

    def test_result_point_requires_confirmation_gate(self):
        """E-P1-1：--result 判别值属 inference，无确认参数直接拒（INPUT）。"""
        result = el.build_plan(
            [el.ElementSpec(kind="result", label="R1", x=1.0, y=2.0, z=3.0)],
            "MUPLocalDb_element_verify", site="s")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.INPUT)
        self.assertTrue(any("--allow-unverified-result-point" in i.message
                            for i in result.issues), result.issues)

    def test_result_point_gate_confirmed_writes(self):
        steps = [
            [["site-1", "test"]],
            [["case-1"]],
            [["cfg-1"]],
            [],        # existing
        ]
        with _patch_queries(steps):
            result = el.build_plan(
                [el.ElementSpec(kind="result", label="R1", x=1.0, y=2.0, z=3.0)],
                "MUPLocalDb_element_verify", site="test", allow_unverified_result=True)
        self.assertTrue(result.ok, result.message)
        self.assertIn("N'MTDElementPoint'", result.data["plan_obj"].sql)

    def test_turbine_vp_overrides(self):
        """E-P1-2：--vp-* 可覆盖默认（含复现 test 站 0/0/0 少数态）。"""
        steps = [
            [["site-1", "test"]],
            [["case-1"]],
            [["cfg-1"]],
            [],        # existing
        ]
        with _patch_queries(steps):
            result = el.build_plan(
                [el.ElementSpec(kind="turbine", label="ZW1", x=1.0, y=2.0, z=3.0)],
                "MUPLocalDb_element_verify", site="test",
                vp_hmin=0.0, vp_hmax=0.0, vp_step=0.0)
        self.assertTrue(result.ok, result.message)
        vp_row = [ln for ln in result.data["plan_obj"].sql.splitlines()
                  if "INSERT INTO dbo.VertProfiles" in ln][0]
        self.assertIn(",0.0,0.0,0.0,", vp_row)

    def test_vp_invalid_params_rejected(self):
        result = el.build_plan(
            [el.ElementSpec(kind="turbine", label="ZW1", x=1.0, y=2.0, z=3.0)],
            "x_verify", site="s", vp_step=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.INPUT)
        result2 = el.build_plan(
            [el.ElementSpec(kind="turbine", label="ZW1", x=1.0, y=2.0, z=3.0)],
            "x_verify", site="s", vp_hmin=100.0, vp_hmax=50.0)
        self.assertFalse(result2.ok)
        self.assertEqual(result2.code, mw.ImportCode.INPUT)

    def test_duplicate_label_in_batch_rejected(self):
        specs = [
            el.ElementSpec(kind="mast", label="A", x=1.0, y=2.0, z=3.0),
            el.ElementSpec(kind="mast", label="A", x=4.0, y=5.0, z=6.0),
        ]
        result = el.build_plan(specs, "x_verify", site="s")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.INPUT)

    def test_no_specs_rejected(self):
        result = el.build_plan([], "x_verify", site="s")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.INPUT)

    def test_existing_same_kind_label_conflict(self):
        steps = [
            [["site-1", "test"]],
            [["case-1"]],
            [["cfg-1"]],
            [["MTDElementMast", "ZM1"]],   # 已有同型同名
        ]
        with _patch_queries(steps):
            result = el.build_plan(self._make_specs(), "MUPLocalDb_element_verify", site="test")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.CONFLICT)

    def test_cross_kind_same_label_allowed(self):
        steps = [
            [["site-1", "test"]],
            [["case-1"]],
            [["cfg-1"]],
            [["MTDElementWindTurbine", "ZM1"]],  # 同名但异型：放行
            [["wtg-1"]],
        ]
        specs = [el.ElementSpec(kind="mast", label="ZM1", x=1.0, y=2.0, z=3.0)]
        with _patch_queries(steps):
            result = el.build_plan(specs, "MUPLocalDb_element_verify", site="test")
        self.assertTrue(result.ok, result.message)

    def test_wtg_missing_rejected(self):
        steps = [
            [["site-1", "test"]],
            [["case-1"]],
            [["cfg-1"]],
            [],        # existing
            [],        # WTType 无
            [],        # WTTypeVersion 无
        ]
        with _patch_queries(steps):
            result = el.build_plan(self._make_specs(), "MUPLocalDb_element_verify", site="test")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.INPUT)
        self.assertIn("mup_turbine_import", result.message)

    def test_ambiguous_case_without_site(self):
        with _patch_queries([[["c1", "s1", "DEMO"], ["c2", "s2", "test"]]]):
            result = el.build_plan(self._make_specs(), "MUPLocalDb_element_verify", case="WT")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
        self.assertIn("--site", result.message)

    def test_config_missing_lists_available(self):
        steps = [
            [["site-1", "test"]],
            [["case-1"]],
            [],                            # 目标配置无
            [["CaseElementConfiguration"]],  # 可用列表
        ]
        with _patch_queries(steps):
            result = el.build_plan(self._make_specs(), "MUPLocalDb_element_verify",
                                   site="test", config_name="不存在")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
        self.assertIn("CaseElementConfiguration", result.message)


class TestGuardsAndVerify(unittest.TestCase):
    def test_run_refuses_prod_db_before_any_query(self):
        with mock.patch.object(mw, "_query_rows") as q:
            result = el.run([el.ElementSpec(kind="mast", label="A", x=1, y=2, z=3)],
                            "MUPLocalDb", apply=True)
            self.assertFalse(result.ok)
            self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
            q.assert_not_called()

    def test_run_refuses_non_whitelist(self):
        with mock.patch.object(mw, "_query_rows") as q:
            result = el.run([el.ElementSpec(kind="mast", label="A", x=1, y=2, z=3)],
                            "MUPLocalDb_prod", apply=True)
            self.assertFalse(result.ok)
            self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
            q.assert_not_called()

    def test_apply_plan_refuses_prod_db_before_write(self):
        """侧审测试缺口：apply_plan 的生产守卫此前无单测。"""
        plan = el.ElementPlan(site_id="s", site_name="t", case_id="c", config_id="cfg",
                              config_name="CaseElementConfiguration", elements=[])
        with mock.patch.object(mw, "_run_sql") as rs:
            result = el.apply_plan("MUPLocalDb", plan)
            self.assertFalse(result.ok)
            self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
            rs.assert_not_called()
        with mock.patch.object(mw, "_run_sql") as rs:
            result2 = el.apply_plan("muplocaldb", plan)   # 大小写归一化
            self.assertFalse(result2.ok)
            self.assertEqual(result2.code, mw.ImportCode.DB_GUARD)
            rs.assert_not_called()

    @staticmethod
    def _verify_plan():
        return el.ElementPlan(
            site_id="site-1", site_name="test", case_id="case-1", config_id="cfg-1",
            config_name="CaseElementConfiguration",
            elements=[
                {"id": "e1", "kind": "mast", "label": "ZM1", "x": 43555698.0,
                 "y": 5133880.9, "z": 125.0, "discriminator": el.DISC_MAST,
                 "vp_id": None, "vp": None},
                {"id": "e2", "kind": "turbine", "label": "ZW1", "x": 1.0, "y": 2.0, "z": 3.0,
                 "discriminator": el.DISC_TURBINE, "vp_id": "vp1",
                 "vp": (20.0, 200.0, 20.0)},
            ])

    # _verify 读回列序：Id,Label,Disc,State,Parent,X,Y,HZ,HZCT,QuadCT,cfgN,vpN,HMin,HMax,Step
    _OK_ROWS = [
        ["e1", "ZM1", el.DISC_MAST, "0", "site-1", "43555698", "5133880.9", "125",
         "1", "-1", "1", "0", "-1e+18", "-1e+18", "-1e+18"],
        ["e2", "ZW1", el.DISC_TURBINE, "0", "site-1", "1", "2", "3",
         "1", "-1", "1", "1", "20.0", "200.0", "20.0"],
    ]

    def test_verify_matches_plan(self):
        with _patch_queries([self._OK_ROWS]):
            result = el._verify("MUPLocalDb_element_verify", self._verify_plan())
        self.assertTrue(result.ok, result.message)

    def test_verify_compares_label(self):
        """E-P2-1：写后校验必须比对 Label，插入端转义漂移不得静默。"""
        rows = [list(r) for r in self._OK_ROWS]
        rows[0][1] = "ZM1?"        # Label 漂移
        with _patch_queries([rows]):
            result = el._verify("MUPLocalDb_element_verify", self._verify_plan())
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.VERIFY)

    def test_verify_checks_vp_values(self):
        """E-P1-2：VP 占位值漂移（如仍写 0/0/0）必须被抓。"""
        rows = [list(r) for r in self._OK_ROWS]
        rows[1][12], rows[1][13], rows[1][14] = "0.0", "0.0", "0.0"
        with _patch_queries([rows]):
            result = el._verify("MUPLocalDb_element_verify", self._verify_plan())
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.VERIFY)

    def test_verify_detects_drift(self):
        plan = el.ElementPlan(
            site_id="site-1", site_name="test", case_id="case-1", config_id="cfg-1",
            config_name="CaseElementConfiguration",
            elements=[{"id": "e1", "kind": "mast", "label": "ZM1", "x": 43555698.0,
                       "y": 5133880.9, "z": 125.0, "discriminator": el.DISC_MAST,
                       "vp_id": None, "vp": None}])
        rows = [["e1", "ZM1", el.DISC_MAST, "0", "site-1", "999", "5133880.9", "125",
                 "1", "-1", "1", "0", "-1e+18", "-1e+18", "-1e+18"]]
        with _patch_queries([rows]):
            result = el._verify("MUPLocalDb_element_verify", plan)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.VERIFY)

    def test_apply_plan_auto_rolls_back_on_verify_failure(self):
        """E-P2-2：写后校验失败 → 自动执行 rollback.sql 并把结果写回负载。"""
        plan = el.ElementPlan(site_id="s", site_name="t", case_id="c", config_id="cfg",
                              config_name="CaseElementConfiguration",
                              elements=self._verify_plan().elements)
        plan.sql, plan.rollback_sql = "APPLY", "ROLLBACK"
        failed = mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致",
                                 data={"checks": {"ZM1": False}})
        with mock.patch.object(mw, "_run_sql",
                               side_effect=[(True, "applied", None), (True, "rb-out", None)]) as rs, \
                mock.patch.object(el, "_verify", return_value=failed):
            result = el.apply_plan("MUPLocalDb_element_verify", plan)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.VERIFY)
        self.assertIn("已自动回滚", result.message)
        self.assertTrue(result.data["rollback"]["ok"])
        rs.assert_any_call("MUPLocalDb_element_verify", "ROLLBACK")

    def test_apply_plan_rollback_failure_escalates(self):
        failed = mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致", data={})
        plan = el.ElementPlan(site_id="s", site_name="t", case_id="c", config_id="cfg",
                              config_name="CaseElementConfiguration", elements=[])
        plan.sql, plan.rollback_sql = "APPLY", "ROLLBACK"
        with mock.patch.object(mw, "_run_sql",
                               side_effect=[(True, "applied", None), (False, "", "boom")]), \
                mock.patch.object(el, "_verify", return_value=failed):
            result = el.apply_plan("MUPLocalDb_element_verify", plan)
        self.assertFalse(result.ok)
        self.assertIn("需人工介入", result.message)
        self.assertFalse(result.data["rollback"]["ok"])

    def test_run_result_gate_and_plan_json_evidence(self):
        """E-P1-1：run 级门禁 + plan.json 标注判别值证据分级。"""
        import tempfile as _tf
        with mock.patch.object(mw, "_query_rows") as q, \
                mock.patch.object(mw, "_run_sql") as rs:
            result = el.run(
                [el.ElementSpec(kind="result", label="R1", x=1.0, y=2.0, z=3.0)],
                "MUPLocalDb", apply=False)
            self.assertFalse(result.ok)
            self.assertTrue(any("--allow-unverified-result-point" in i.message
                                for i in result.issues), result.issues)
            q.assert_not_called()
            rs.assert_not_called()
        steps = [
            [["site-1", "test"]],
            [["case-1"]],
            [["cfg-1"]],
            [],        # existing
        ]
        out_dir = Path(_tf.mkdtemp(prefix="elem_gate_"))
        with _patch_queries(steps):
            ok_result = el.run(
                [el.ElementSpec(kind="result", label="R1", x=1.0, y=2.0, z=3.0)],
                "MUPLocalDb_element_verify", site="test", apply=False,
                out_dir=out_dir, allow_unverified_result=True)
        self.assertTrue(ok_result.ok, ok_result.message)
        plan_json = json.loads((out_dir / "plan.json").read_text(encoding="utf-8"))
        self.assertIn(el.DISC_RESULT, plan_json["discriminator_evidence"])
        self.assertIn("inference", plan_json["discriminator_evidence"][el.DISC_RESULT])
        # 成功/失败输出都带 rollback.sql 路径（E-P1-1 整改项）
        self.assertTrue(ok_result.data["rollback"].endswith("rollback.sql"))
        self.assertEqual(plan_json["case"], "WT")   # E-P3-1 死表达式已简化（恒 = 传入 case）
        self.assertEqual(plan_json["vp_defaults"],
                         {"hmin": 20.0, "hmax": 200.0, "step": 20.0})

    def test_main_json_reports_parse_error(self):
        from io import StringIO
        buf = StringIO()
        with mock.patch("sys.stdout", buf):
            code = el.main(["--db", "x_verify", "--mast", "A,1,2", "--json"])
        self.assertEqual(code, 2)
        payload = json.loads(buf.getvalue())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], mw.ImportCode.INPUT)


if __name__ == "__main__":
    unittest.main()
