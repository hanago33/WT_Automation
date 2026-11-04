# encoding: utf-8
"""mup_site_import 单元测试（不连库、不写 Vault、不触发 GUI/HASP）。

覆盖：725 字符官方 WKT 写入、查重、同名 + --replace 从属数据 fail-closed、
Cases 中心联动、回滚脚本（只删本批次、无裸 DELETE）、生产库大小写门禁、
缺省输出目录落到系统临时目录。
"""
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mup_site_import as s
import mup_weather_import as mw

WKT = s.OFFICIAL_WKT_4531
CENTER_X = 43552948.938
CENTER_Y = 5144983.1655
RADIUS = 12691.707


def make_spec(**kwargs):
    fields = dict(name="SITE_UNIT_A", projection_wkt=WKT,
                  center_x=CENTER_X, center_y=CENTER_Y, radius=RADIUS)
    fields.update(kwargs)
    return s.SiteSpec(**fields)


def build(spec=None, responses=None, owner=("owner-id", ""), replace=False):
    # responses 是 _query_rows 的逐次返回：每次返回「行列表」。
    spec = spec or make_spec()
    calls = list(responses if responses is not None else [[]])
    with unittest.mock.patch.object(mw, "_query_rows", side_effect=calls), \
            unittest.mock.patch.object(mw, "_pick_owner", return_value=owner):
        return s.build_plan(spec, "MUPLocalDb_site_verify", replace=replace)


class TestOfficialWkt(unittest.TestCase):
    def test_constant_is_official_725(self):
        self.assertEqual(len(WKT), 725)
        self.assertTrue(WKT.startswith('PROJCS["CGCS2000 / 3-degree Gauss-Kruger zone 43"'))
        self.assertIn('AUTHORITY["EPSG", "4531"]', WKT)
        self.assertTrue(WKT.endswith('AXIS["Northing", NORTH]]'))

    def test_default_path_is_script_local_asset(self):
        self.assertEqual(s.DEFAULT_WKT_PATH.parent, Path(s.__file__).parent)
        self.assertEqual(s.DEFAULT_WKT_PATH.name, "wkt_4531_official.txt")
        self.assertTrue(s.DEFAULT_WKT_PATH.is_file())
        self.assertEqual(s.load_official_wkt(), WKT)

    def test_missing_default_asset_falls_back_to_constant(self):
        with unittest.mock.patch.object(s, "DEFAULT_WKT_PATH", Path("no/such/wkt.txt")):
            self.assertEqual(s.load_official_wkt(), WKT)

    def test_explicit_missing_file_fails_closed(self):
        with self.assertRaises(RuntimeError):
            s.load_official_wkt(Path("no/such/explicit.txt"))


class TestSiteSpecValidate(unittest.TestCase):
    def test_valid_spec_has_no_issues(self):
        self.assertEqual(make_spec().validate(), [])

    def test_zero_radius_is_rejected(self):
        issues = make_spec(radius=0).validate()
        self.assertTrue(any("半径" in i.message for i in issues), [i.message for i in issues])

    def test_zero_center_is_rejected_for_epsg4531(self):
        issues = make_spec(center_x=0.0, center_y=0.0).validate()
        self.assertTrue(any("(0, 0)" in i.message for i in issues), [i.message for i in issues])


class TestBuildPlan(unittest.TestCase):
    def test_duplicate_name_without_replace_conflicts(self):
        res = build(responses=[[["existing-site-id"]]])
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.CONFLICT)
        self.assertIn("同名站点已存在", res.message)

    def test_same_name_with_replace_rebuilds(self):
        res = build(responses=[[["old-site-id"]], [["0", "0", "0", "0"]]], replace=True)
        self.assertTrue(res.ok, res.message)
        plan = res.data["plan_obj"]
        self.assertIn("DELETE FROM dbo.Sites WHERE Id=N'old-site-id';", plan.sql)
        self.assertIn("DELETE FROM dbo.Cases WHERE Parent_Id=N'old-site-id';", plan.sql)

    def test_replace_blocked_by_dependent_rows(self):
        res = build(responses=[[["old-site-id"]], [["0", "1", "0", "2"]]], replace=True)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.CONFLICT)
        self.assertIn("case_environment_data=1", res.message)
        self.assertIn("wind_resource_map=2", res.message)
        self.assertNotIn("plan_obj", res.data)  # 阻断时不产出任何可执行计划

    def test_dependency_query_failure_is_closed(self):
        with unittest.mock.patch.object(mw, "_query_rows",
                                        side_effect=[["old-site-id"], RuntimeError("boom")]), \
                unittest.mock.patch.object(mw, "_pick_owner", return_value=("owner-id", "")):
            res = s.build_plan(make_spec(), "MUPLocalDb_site_verify", replace=True)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.DB_GUARD)

    def test_plan_writes_full_725_wkt(self):
        res = build()
        self.assertTrue(res.ok, res.message)
        plan = res.data["plan_obj"]
        self.assertIn(mw._sql_literal(WKT), plan.sql)
        self.assertEqual(plan.to_dict()["projection_len"], 725)

    def test_cases_row_mirrors_site_center_and_radius(self):
        res = build()
        plan = res.data["plan_obj"]
        cases_line = next(ln for ln in plan.sql.splitlines() if "INSERT INTO dbo.Cases" in ln)
        self.assertIn(plan.site_id, cases_line)
        self.assertIn(str(CENTER_X), cases_line)
        self.assertIn(str(CENTER_Y), cases_line)
        self.assertIn(str(RADIUS), cases_line)
        self.assertIn(mw._sql_literal(s.DISC_CASE), cases_line)

    def test_default_configuration_inserted(self):
        res = build()
        plan = res.data["plan_obj"]
        self.assertIn(mw._sql_literal(s.DEFAULT_CONFIG_NAME), plan.sql)
        self.assertIn(plan.case_id, plan.sql)

    def test_rollback_only_touches_this_batch(self):
        res = build()
        plan = res.data["plan_obj"]
        deletes = [ln for ln in plan.rollback_sql.splitlines() if "DELETE" in ln]
        self.assertEqual(len(deletes), 3)
        for line in deletes:
            self.assertIn(" WHERE ", line)
        ids = [plan.config_id, plan.case_id, plan.site_id]
        for ident in ids:
            self.assertIn(mw._sql_literal(ident), plan.rollback_sql)
        # 逆序：Configurations -> Cases -> Sites
        self.assertIn("dbo.Configurations", deletes[0])
        self.assertIn("dbo.Cases", deletes[1])
        self.assertIn("dbo.Sites", deletes[2])

    def test_apply_sql_is_single_transaction(self):
        plan = build().data["plan_obj"]
        self.assertEqual(plan.sql.count("BEGIN TRANSACTION"), 1)
        self.assertEqual(plan.sql.count("COMMIT"), 1)
        self.assertIn("SET XACT_ABORT ON", plan.sql)


class TestGuards(unittest.TestCase):
    def _plan(self):
        return build().data["plan_obj"]

    def test_prod_db_rejected_case_insensitively(self):
        for name in ("MUPLocalDb", "muplocaldb", " MUPLOCALDB "):
            res = s.apply_plan(name, self._plan())
            self.assertFalse(res.ok, name)
            self.assertEqual(res.code, mw.ImportCode.DB_GUARD, name)

    def test_non_whitelisted_copy_db_rejected(self):
        res = s.apply_plan("SomeOtherDb", self._plan())
        self.assertEqual(res.code, mw.ImportCode.DB_GUARD)

    def test_guard_blocks_before_any_sql(self):
        with unittest.mock.patch.object(mw, "_run_sql") as run_sql:
            s.apply_plan("muplocaldb", self._plan())
        run_sql.assert_not_called()

    def test_apply_on_copy_db_runs_and_verifies(self):
        ok = mw.ImportResult(True, mw.ImportCode.OK, "ok")
        with unittest.mock.patch.object(mw, "_run_sql", return_value=(True, "", "")) as run_sql, \
                unittest.mock.patch.object(s, "_verify", return_value=ok):
            res = s.apply_plan("MUPLocalDb_site_verify", self._plan())
        self.assertTrue(res.ok)
        run_sql.assert_called_once()

    def test_write_failure_surfaces_db_write(self):
        with unittest.mock.patch.object(mw, "_run_sql", return_value=(False, "", "fk violation")):
            res = s.apply_plan("MUPLocalDb_site_verify", self._plan())
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.DB_WRITE)
        self.assertIn("fk violation", res.message)


class TestRun(unittest.TestCase):
    def test_dry_run_default_out_dir_is_temp(self):
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(mw, "_query_rows", return_value=[]), \
                unittest.mock.patch.object(mw, "_pick_owner", return_value=("owner-id", "")), \
                unittest.mock.patch.object(s.tempfile, "gettempdir", return_value=tmp):
            res = s.run(make_spec(), "MUPLocalDb_site_verify", apply=False)
            self.assertTrue(res.ok, res.message)
            out = Path(res.data["out_dir"])
            self.assertEqual(out.name, "mup_site_import")
            self.assertEqual(out.parent, Path(tmp))
            self.assertTrue((out / "apply.sql").is_file())
            self.assertTrue((out / "rollback.sql").is_file())

    def test_run_rejects_prod_before_build(self):
        with unittest.mock.patch.object(mw, "_query_rows") as q:
            res = s.run(make_spec(), "MUPLocalDb", apply=True)
        self.assertEqual(res.code, mw.ImportCode.DB_GUARD)
        q.assert_not_called()


if __name__ == "__main__":
    unittest.main()
