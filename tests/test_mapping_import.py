# encoding: utf-8
"""mup_mapping_import 单元测试（不连库、不启动 GUI/HASP）。

覆盖：生产金样几何复现（test 站与 DEMO 站两条）、角点顺序与「天上地下」闸门、
高度 0-9000 前置门、同名查重、Configuration_Element 挂载与降级、
回滚脚本只删本行、生产库/非白名单库拒写、写后校验能发现问题。
"""
import os
import sys
import json
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mup_mapping_import as mp
import mup_weather_import as mw

SITE_ID = "276b9616-655b-4ec5-a52b-539e01519489"
CASE_ID = "3fda28d9-ba92-4aec-b7cf-a099350aed3a"
CONF_ID = "33d4c0cd-4ba7-4d2d-9150-8100aceb4af8"
DEMO_ID = "323235aa-bc89-45cd-9c3a-6757e3a1f469"
DEMO_CASE_ID = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
# 生产 test 站 Mapping(1) 金样（2026-11-04 侦察实测）
GOLDEN = dict(nw_x=43538274.0, nw_y=5160116.0, se_x=43569974.0, se_y=5130516.0,
              height=125.0, step=100.0)


def make_spec(**kwargs):
    fields = dict(site="test")
    fields.update(GOLDEN)
    fields.update(kwargs)
    return mp.MappingSpec(**fields)


def rows_for(sql: str) -> list:
    """按 SQL 语义返回行，避免依赖调用顺序（比 side_effect 列表稳）。"""
    if "FROM dbo.Sites" in sql:
        # 站点定位：mock 按查询里的站点名/Id 回对应的行（几何不同，闸门也不同）
        if "DEMO" in sql:
            return [[DEMO_ID, "DEMO", "38560818.0", "3164215.0", "8000.0", "607"]]
        return [[SITE_ID, "test", "43554124.0", "5145316.0", "22185.537", "725"]]
    if "FROM dbo.Cases" in sql:
        return [[DEMO_CASE_ID]] if "DEMO" in sql else [[CASE_ID]]
    if "dbo.Configurations" in sql:
        return [[CONF_ID]]
    if "Point_HZ BETWEEN" in sql:
        return [["5"]]
    if "FROM dbo.Elements" in sql and "Label" in sql:
        return [["Mapping(1)"]]
    return []


def build(spec=None):
    spec = spec or make_spec()
    with unittest.mock.patch.object(mw, "_query_rows", side_effect=lambda db, sql: rows_for(sql)):
        return spec, mp.build_plan(spec, "MUPLocalDb_mapcfd_verify")


class TestGoldenGeometry(unittest.TestCase):
    def test_test_site_golden_is_reproduced(self):
        _, res = build()
        self.assertTrue(res.ok, res.message)
        plan = res.data["plan"]
        self.assertEqual(plan["nb_step_x"], 317)
        self.assertEqual(plan["nb_step_y"], 296)
        self.assertEqual(plan["origin"], [43538274.0, 5130516.0])
        self.assertEqual(plan["height"], 125.0)
        self.assertEqual(plan["step"], 100.0)
        self.assertEqual(plan["hz_coord_type"], 1)

    def test_demo_golden_is_reproduced(self):
        # DEMO 站 Mapping(1)：Origin(38555118,3161959,80) 步长 100 → 123 × 61
        spec = make_spec(site="DEMO", nw_x=38555118.0, nw_y=3168059.0,
                         se_x=38567418.0, se_y=3161959.0, height=80.0)
        with unittest.mock.patch.object(mw, "_query_rows",
                                        side_effect=lambda db, sql: rows_for(sql)):
            res = mp.build_plan(spec, "MUPLocalDb_mapcfd_verify")
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.data["plan"]["nb_step_x"], 123)
        self.assertEqual(res.data["plan"]["nb_step_y"], 61)
        self.assertEqual(res.data["plan"]["origin"], [38555118.0, 3161959.0])

    def test_insert_sql_carries_every_geometry_column(self):
        _, res = build()
        sql = res.data["plan_obj"].sql
        for token in ("MTDElementMapping", SITE_ID, "43538274.0", "5130516.0", "125.0",
                      "317,", "296,", "100.0", "Quad_HZCoordType", "State", "0,0"):
            self.assertIn(token, sql, token)
        self.assertIn("dbo.Configuration_Element", sql)
        self.assertTrue(sql.startswith("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;"))
        self.assertIn("COMMIT;", sql)

    def test_label_auto_numbers_past_existing(self):
        _, res = build()
        self.assertEqual(res.data["plan"]["label"], "Mapping(2)")

    def test_non_integer_span_is_rejected_at_plan_time(self):
        # G-1（zcode 双轮实机）：过去只 note+取整，apply 会真写一行、写后校验才报 exit 5，
        # 调用方以为输入被拒实则库里已有残留行。现在计划期硬拒。
        spec = make_spec(se_x=GOLDEN["se_x"] + 50.0)
        self.assertTrue(any(i.code == mw.ImportCode.INPUT and "不是步长" in i.message
                            for i in spec.validate()), spec.validate())
        _, res = build(spec)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)
        self.assertEqual(res.exit_code(), 2)
        detail = [i.detail for i in res.issues if i.detail]
        self.assertTrue(detail, res.issues)
        self.assertEqual(detail[0]["nb_step_rounded"], 318)
        self.assertAlmostEqual(detail[0]["corner_delta_m"], 50.0, places=6)

    def test_non_integer_north_south_span_also_rejected(self):
        _, res = build(make_spec(nw_y=GOLDEN["nw_y"] + 55.0))
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)
        self.assertTrue(any("南北向" in i.message for i in res.issues), res.issues)

    def test_integer_span_carries_no_span_note(self):
        # 超期的「取整 note」表达已删除：整数跨度路径不再出现「不是步长」字样。
        _, res = build()
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.data["plan"]["notes"], [])

    def test_span_gate_rejects_before_any_db_read(self):
        # 计划期硬拒 ⇒ 连站点解析都不该发生（自动化不能拿到半程状态）。
        with unittest.mock.patch.object(
                mw, "_query_rows",
                side_effect=AssertionError("must not query the db")) as query:
            res = mp.build_plan(make_spec(se_x=GOLDEN["se_x"] + 50.0),
                                "MUPLocalDb_mapcfd_verify")
        self.assertEqual(query.call_count, 0)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)

    def test_non_finite_corner_does_not_crash_the_span_gate(self):
        for bad_val in (float("nan"), float("inf"), float("-inf")):
            issues = make_spec(nw_x=bad_val).validate()
            self.assertTrue(any(i.code == mw.ImportCode.INPUT and "有限数值" in i.message for i in issues), (bad_val, issues))
            issues_y = make_spec(nw_y=bad_val).validate()
            self.assertTrue(any(i.code == mw.ImportCode.INPUT and "有限数值" in i.message for i in issues_y), (bad_val, issues_y))
        for bad_step in (float("nan"), float("inf"), float("-inf"), 0.0, -10.0):
            issues_step = make_spec(step=bad_step).validate()
            self.assertTrue(any(i.code == mw.ImportCode.INPUT for i in issues_step), (bad_step, issues_step))


class TestGates(unittest.TestCase):
    def test_reversed_corners_rejected(self):
        _, res = build(make_spec(se_y=5200000.0))
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)
        self.assertTrue(any("角点顺序" in i.message for i in res.issues))

    def test_coordinates_far_from_site_rejected(self):
        # 「别天上地下」：跨度和顺序都合法，但离站点 500+ km
        _, res = build(make_spec(nw_x=43000000.0, nw_y=5200000.0,
                                 se_x=43010000.0, se_y=5190000.0))
        self.assertFalse(res.ok)
        self.assertTrue(any("合理性闸门" in res.message or "坐标系" in i.message
                            for i in res.issues), res.message)

    def test_height_outside_0_9000_rejected(self):
        _, res = build(make_spec(height=9500.0))
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)

    def test_zero_step_rejected(self):
        self.assertTrue(any("步长" in i.message for i in make_spec(step=0.0).validate()))

    def test_missing_site_fails_closed(self):
        with unittest.mock.patch.object(mw, "_query_rows", return_value=[]):
            res = mp.build_plan(make_spec(), "MUPLocalDb_mapcfd_verify")
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.DB_GUARD)

    def test_site_without_projection_rejected(self):
        def fake(db, sql):
            if "FROM dbo.Sites" in sql:
                return [[SITE_ID, "test", "0", "0", "0", "0"]]
            return rows_for(sql)
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=fake):
            res = mp.build_plan(make_spec(), "MUPLocalDb_mapcfd_verify")
        self.assertFalse(res.ok)
        self.assertIn("投影", res.message)

    def test_no_reference_element_blocks_like_gui(self):
        def fake(db, sql):
            if "Point_HZ BETWEEN" in sql:
                return [["0"]]
            return rows_for(sql)
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=fake):
            res = mp.build_plan(make_spec(), "MUPLocalDb_mapcfd_verify")
        self.assertFalse(res.ok)
        # P4：这是「缺前置条件」（要先补元素），不是「同名已存在」，也不能与库守卫混成一个 code。
        self.assertEqual(res.code, mw.ImportCode.PRECONDITION)
        self.assertEqual(res.exit_code(), 7)
        # 显式放行后应当通过
        spec = make_spec(require_reference_element=False)
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=fake):
            ok = mp.build_plan(spec, "MUPLocalDb_mapcfd_verify")
        self.assertTrue(ok.ok, ok.message)

    def test_duplicate_label_conflicts(self):
        _, res = build(make_spec(label="Mapping(1)"))
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.CONFLICT)

    def test_missing_configuration_downgrades_link(self):
        def fake(db, sql):
            if "dbo.Configurations" in sql:
                return []
            return rows_for(sql)
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=fake):
            res = mp.build_plan(make_spec(), "MUPLocalDb_mapcfd_verify")
        self.assertTrue(res.ok, res.message)
        self.assertFalse(res.data["plan"]["link_configuration"])
        self.assertIn("dbo.Elements", res.data["plan_obj"].sql)
        self.assertNotIn("Configuration_Element", res.data["plan_obj"].sql)


class TestWriteGuards(unittest.TestCase):
    def test_prod_db_refused_case_insensitively(self):
        _, res = build()
        for name in ("MUPLocalDb", "muplocaldb", " MUPLocalDb "):
            got = mp.apply_plan(name, res.data["plan_obj"])
            self.assertFalse(got.ok)
            self.assertEqual(got.code, mw.ImportCode.DB_GUARD, name)

    def test_non_whitelisted_db_refused(self):
        _, res = build()
        got = mp.apply_plan("SomeOtherDb", res.data["plan_obj"])
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.DB_GUARD)

    def test_run_refuses_apply_on_prod_before_reading_plan(self):
        got = mp.run(make_spec(), "MUPLocalDb", apply=True)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.DB_GUARD)

    def test_rollback_only_touches_own_element(self):
        _, res = build()
        sql = res.data["plan_obj"].rollback_sql
        self.assertIn("WHERE ElementId=", sql)
        self.assertIn("WHERE Id=", sql)
        self.assertNotIn("WHERE Parent_Id", sql)
        self.assertNotIn("WHERE Label", sql)
        self.assertNotIn("DELETE FROM dbo.Elements;", sql)
        self.assertNotIn("dbo.Cases", sql)
        self.assertNotIn("dbo.Sites", sql)

    def test_verify_catches_wrong_nbstep(self):
        _, res = build()
        plan = res.data["plan_obj"]
        row = [plan.label, mp.DISC_MAPPING, plan.site_id, "0", "1",
               "43538274.0", "5130516.0", "125.0",
               "1", "0", "999", "100.0", "0", "1", "296", "100.0", "1"]
        with unittest.mock.patch.object(mw, "_query_rows", return_value=[row]):
            got = mp._verify("MUPLocalDb_mapcfd_verify", plan)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertFalse(got.data["checks"]["edge1_nbstep"])

    def test_verify_passes_on_matching_row(self):
        _, res = build()
        plan = res.data["plan_obj"]
        row = [plan.label, mp.DISC_MAPPING, plan.site_id, "0", "1",
               "43538274.0", "5130516.0", "125.0",
               "1", "0", "317", "100.0", "0", "1", "296", "100.0", "1"]
        with unittest.mock.patch.object(mw, "_query_rows", return_value=[row]):
            got = mp._verify("MUPLocalDb_mapcfd_verify", plan)
        self.assertTrue(got.ok, got.message)
        self.assertTrue(all(got.data["checks"].values()))


class TestDryRunArtifacts(unittest.TestCase):
    def test_default_out_dir_is_system_temp(self):
        with unittest.mock.patch.object(mw, "_query_rows",
                                        side_effect=lambda db, sql: rows_for(sql)), \
                unittest.mock.patch.object(mw, "_run_sql") as run_sql:
            got = mp.run(make_spec(), "MUPLocalDb_mapcfd_verify", apply=False)
        run_sql.assert_not_called()          # dry-run 绝不碰库
        self.assertTrue(got.ok, got.message)
        self.assertEqual(Path(got.data["out_dir"]).parent, Path(tempfile.gettempdir()))
        self.assertIn("mup_mapping_import", got.data["out_dir"])

    def test_dry_run_writes_three_scripts_and_touches_no_db(self):
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=lambda db, sql: rows_for(sql)), \
                unittest.mock.patch.object(mw, "_run_sql") as run_sql:
            work = Path(tempfile.mkdtemp(prefix="mup_map_test_"))
            got = mp.run(make_spec(), "MUPLocalDb_mapcfd_verify", apply=False, out_dir=work)
        self.assertTrue(got.ok, got.message)
        run_sql.assert_not_called()
        for name in ("plan.json", "apply.sql", "rollback.sql"):
            self.assertTrue((work / name).is_file(), name)

    def test_apply_writes_then_verifies(self):
        _, res = build()
        plan = res.data["plan_obj"]
        row = [plan.label, mp.DISC_MAPPING, plan.site_id, "0", "1",
               "43538274.0", "5130516.0", "125.0",
               "1", "0", "317", "100.0", "0", "1", "296", "100.0", "1"]
        with unittest.mock.patch.object(mw, "_run_sql", return_value=(True, "", "")), \
                unittest.mock.patch.object(mw, "_query_rows", return_value=[row]):
            got = mp.apply_plan("MUPLocalDb_mapcfd_verify", plan)
        self.assertTrue(got.ok, got.message)


class TestCli(unittest.TestCase):
    def test_main_reports_failure_exit_code(self):
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=lambda db, sql: rows_for(sql)):
            code = mp.main(["--site", "test", "--nw-x", "43538274", "--nw-y", "5160116",
                            "--se-x", "43569974", "--se-y", "5130516",
                            "--height", "9001", "--db", "MUPLocalDb_mapcfd_verify", "--json"])
        self.assertEqual(code, 2)

    def test_main_accepts_bounds_style_corners(self):
        argv = ["--site", "test", "--x-min", "43538274", "--x-max", "43569974",
                "--y-min", "5130516", "--y-max", "5160116", "--height", "125",
                "--step", "100", "--db", "MUPLocalDb_mapcfd_verify"]
        with unittest.mock.patch.object(mw, "_query_rows",
                                        side_effect=lambda db, sql: rows_for(sql)):
            with unittest.mock.patch("builtins.print"):
                code = mp.main(argv)
        self.assertEqual(code, 0)


class TestUiBoundsInput(unittest.TestCase):
    """校准 2026-11-05：GUI 属性行是 XMin/XMax/YMin/YMax（MBAMappingViewModel_PropertyLine_*），
    库里是西南角原点 + 两条边 ⇒ 两种写法必须落到同一行。"""

    def test_from_bounds_matches_the_corner_form(self):
        spec = mp.MappingSpec.from_bounds("test", 43538274.0, 43569974.0,
                                          5130516.0, 5160116.0, height=125.0, step=100.0)
        self.assertEqual((spec.nw_x, spec.nw_y), (43538274.0, 5160116.0))
        self.assertEqual((spec.se_x, spec.se_y), (43569974.0, 5130516.0))
        _, res = build(spec)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.data["plan"]["nb_step_x"], 317)
        self.assertEqual(res.data["plan"]["nb_step_y"], 296)
        self.assertEqual(res.data["plan"]["origin"], [43538274.0, 5130516.0])

    def test_cli_bounds_group_parses(self):
        args = mp._build_parser().parse_args(
            ["--site", "test", "--x-min", "43538274", "--x-max", "43569974",
             "--y-min", "5130516", "--y-max", "5160116", "--db", "MUPLocalDb_mapcfd_verify"])
        spec, warning = mp._spec_from_args(args)
        self.assertEqual(warning, "")
        self.assertEqual((spec.nw_x, spec.nw_y, spec.se_x, spec.se_y),
                         (43538274.0, 5160116.0, 43569974.0, 5130516.0))

    def test_half_given_group_is_rejected_not_guessed(self):
        args = mp._build_parser().parse_args(
            ["--site", "test", "--x-min", "43538274", "--x-max", "43569974",
             "--db", "MUPLocalDb_mapcfd_verify"])
        spec, warning = mp._spec_from_args(args)
        self.assertIsNone(spec)
        self.assertIn("必须给全", warning)

    def test_conflicting_styles_prefer_the_bounds_row(self):
        args = mp._build_parser().parse_args(
            ["--site", "test", "--x-min", "43538274", "--x-max", "43569974",
             "--y-min", "5130516", "--y-max", "5160116",
             "--nw-x", "1", "--nw-y", "2", "--se-x", "3", "--se-y", "4",
             "--db", "MUPLocalDb_mapcfd_verify"])
        spec, warning = mp._spec_from_args(args)
        self.assertIn("为准", warning)
        self.assertEqual(spec.nw_x, 43538274.0)


class TestPreconditionCode(unittest.TestCase):
    """P4：缺前置 ≠ 库守卫。站点下没有 Case 时必须报 precondition_missing(7)。"""

    def test_site_without_case_is_precondition(self):
        def fake(db, sql):
            if "FROM dbo.Cases" in sql:
                return []
            return rows_for(sql)
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=fake):
            res = mp.build_plan(make_spec(), "MUPLocalDb_mapcfd_verify")
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.PRECONDITION)
        self.assertEqual(res.exit_code(), 7)
        self.assertTrue(any(i.detail.get("missing") == "dbo.Cases" for i in res.issues),
                        res.issues)


class TestApplyAutoRollback(unittest.TestCase):
    """G-2：写后校验失败必须自动执行 rollback.sql（对齐 element 的 E-P2-2）。"""

    # 库里 NbStep=999 与计划 317 不一致 ⇒ 校验失败（G-1 时代的取整残留形状）。
    BAD_ROW = ["Mapping(2)", mp.DISC_MAPPING, SITE_ID, "0", "1",
               "43538274.0", "5130516.0", "125.0",
               "1", "0", "999", "100.0", "0", "1", "296", "100.0", "1"]
    GOOD_ROW = ["Mapping(2)", mp.DISC_MAPPING, SITE_ID, "0", "1",
                "43538274.0", "5130516.0", "125.0",
                "1", "0", "317", "100.0", "0", "1", "296", "100.0", "1"]

    def _rows(self, readback):
        """计划期查询走语义 mock，写后回读查询返回指定行。"""
        def fake(db, sql):
            if "FROM dbo.Elements WHERE Id=" in sql:
                return readback
            return rows_for(sql)
        return fake

    def test_verify_failure_executes_rollback_and_reports_it(self):
        _, res = build()
        plan = res.data["plan_obj"]
        seen: list[str] = []

        def fake_run(db, sql, *args, **kwargs):
            seen.append(sql)
            return (True, "", "")

        with unittest.mock.patch.object(mw, "_run_sql", side_effect=fake_run), \
                unittest.mock.patch.object(mw, "_query_rows", side_effect=self._rows([self.BAD_ROW])):
            got = mp.apply_plan("MUPLocalDb_mapcfd_verify", plan)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertIn("已自动回滚", got.message)
        self.assertEqual(len(seen), 2, seen)
        self.assertIn("INSERT INTO dbo.Elements", seen[0])
        self.assertIn("DELETE FROM dbo.Elements", seen[1])
        self.assertIn("WHERE Id=N'" + plan.element_id + "'", seen[1])
        self.assertIn("WHERE ElementId=N'" + plan.element_id + "'", seen[1])
        self.assertNotIn("WHERE Parent_Id", seen[1])
        self.assertTrue(got.data["rollback"]["executed"])
        self.assertTrue(got.data["rollback"]["ok"])
        self.assertFalse(got.data["checks"]["edge1_nbstep"])

    def test_rollback_failure_is_reported_loudly(self):
        _, res = build()
        plan = res.data["plan_obj"]
        calls: list[str] = []

        def fake_run(db, sql, *args, **kwargs):
            calls.append(sql)
            return (True, "", "") if len(calls) == 1 else (False, "", "boom")

        with unittest.mock.patch.object(mw, "_run_sql", side_effect=fake_run), \
                unittest.mock.patch.object(mw, "_query_rows", side_effect=self._rows([self.BAD_ROW])):
            got = mp.apply_plan("MUPLocalDb_mapcfd_verify", plan)
        self.assertFalse(got.ok)
        self.assertIn("需人工介入", got.message)
        self.assertFalse(got.data["rollback"]["ok"])
        self.assertEqual(got.data["rollback"]["error"], "boom")

    def test_write_failure_does_not_run_rollback(self):
        _, res = build()
        plan = res.data["plan_obj"]
        with unittest.mock.patch.object(mw, "_run_sql",
                                        return_value=(False, "", "insert blew up")) as run_sql, \
                unittest.mock.patch.object(mw, "_query_rows") as query:
            got = mp.apply_plan("MUPLocalDb_mapcfd_verify", plan)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.DB_WRITE)
        self.assertEqual(run_sql.call_count, 1)
        query.assert_not_called()

    def test_success_path_runs_only_the_insert(self):
        seen: list[str] = []

        def fake_run(db, sql, *args, **kwargs):
            seen.append(sql)
            return (True, "", "")

        with unittest.mock.patch.object(mw, "_run_sql", side_effect=fake_run), \
                unittest.mock.patch.object(mw, "_query_rows", side_effect=self._rows([self.GOOD_ROW])):
            got = mp.run(make_spec(), "MUPLocalDb_mapcfd_verify", apply=True)
        self.assertTrue(got.ok, got.message)
        self.assertEqual(len(seen), 1, seen)
        self.assertIn("rollback.sql", got.data["rollback"])

    def test_failure_payload_carries_rollback_path_and_verify(self):
        work = Path(tempfile.mkdtemp(prefix="mup_map_rb_"))
        with unittest.mock.patch.object(mw, "_run_sql", return_value=(True, "", "")), \
                unittest.mock.patch.object(mw, "_query_rows", side_effect=self._rows([self.BAD_ROW])):
            got = mp.run(make_spec(), "MUPLocalDb_mapcfd_verify", apply=True, out_dir=work)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertEqual(Path(got.data["rollback"]), work / "rollback.sql")
        self.assertTrue((work / "rollback.sql").is_file())
        self.assertIn("rollback", got.data["verify"])

    def test_cli_failure_payload_carries_rollback_and_auto_rollback_ran(self):
        """CLI 出口也要能看到回滚：自动化编排只读 --json 负载。"""
        work = tempfile.mkdtemp(prefix="mup_map_cli_")
        argv = ["--site", "test", "--nw-x", "43538274", "--nw-y", "5160116",
                "--se-x", "43569974", "--se-y", "5130516", "--height", "125",
                "--step", "100", "--db", "MUPLocalDb_mapcfd_verify",
                "--apply", "--out-dir", work, "--json"]
        seen: list[str] = []

        def fake_run(db, sql, *args, **kwargs):
            seen.append(sql)
            return (True, "", "")

        with unittest.mock.patch.object(mw, "_query_rows", side_effect=self._rows([self.BAD_ROW])), \
                unittest.mock.patch.object(mw, "_run_sql", side_effect=fake_run), \
                unittest.mock.patch("builtins.print") as out:
            code = mp.main(argv)
        self.assertEqual(code, 5)
        payload = json.loads(out.call_args_list[-1].args[0])
        self.assertEqual(payload["code"], mw.ImportCode.VERIFY)
        self.assertTrue(payload["data"]["rollback"].endswith("rollback.sql"))
        self.assertTrue(payload["data"]["verify"]["rollback"]["executed"])
        self.assertEqual(len(seen), 2, seen)
        self.assertIn("DELETE FROM dbo.Elements", seen[1])

    def test_malformed_readback_fails_closed_instead_of_crashing(self):
        """回读列缺（例如被 sqlcmd 裁剪/错位）必须是 verify_failed + 自动回滚，不是 IndexError。"""
        _, res = build()
        plan = res.data["plan_obj"]
        seen: list[str] = []

        def fake_run(db, sql, *args, **kwargs):
            seen.append(sql)
            return (True, "", "")

        with unittest.mock.patch.object(mw, "_run_sql", side_effect=fake_run), \
                unittest.mock.patch.object(mw, "_query_rows",
                                           side_effect=self._rows([["Mapping(2)"]])):
            got = mp.apply_plan("MUPLocalDb_mapcfd_verify", plan)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertIn("列数不足", got.message)
        self.assertEqual(len(seen), 2, seen)

    def test_unparseable_readback_fails_closed_instead_of_crashing(self):
        _, res = build()
        plan = res.data["plan_obj"]
        row = list(self.GOOD_ROW)
        row[5] = "43538274.0...truncated"
        with unittest.mock.patch.object(mw, "_run_sql", return_value=(True, "", "")), \
                unittest.mock.patch.object(mw, "_query_rows", side_effect=self._rows([row])):
            got = mp.apply_plan("MUPLocalDb_mapcfd_verify", plan)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertIn("无法解析", got.message)


if __name__ == "__main__":
    unittest.main()
