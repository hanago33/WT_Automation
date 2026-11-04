# encoding: utf-8
"""mup_synthesis_import 纯函数测试（不写库）。

副本库 e2e（apply→verify→rollback）见 99_synthesis/2026-11-04-synthesis-datalayer-zcode.md；
库结构 fail-closed 校验依赖真实 sys.columns，不在离线测试内模拟。

侧审 S-P2-4 整改：build_plan/_clone_literals/_verify/run 守卫/apply_plan 回滚在此补齐，
用带查询匹配的假读通道驱动完整计划/校验管道（不触库）。
P1-A 之后有两条读通道：定宽读 `_rows`、变长读 `_var_width`+`_sized_rows`，
测试统一由 ReadChannels 接管（见该类的期望行构造 `_sized_row`）。
"""
import json
import os
import sys
import tempfile
import contextlib
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mup_weather_import as mw
import mup_synthesis_import as si

DB_OK = "MUPLocalDb_site_verify"
SITE_ID = "11111111-1111-1111-1111-111111111111"
CASE_ID = "22222222-2222-2222-2222-222222222222"
CFG_ID = "33333333-3333-3333-3333-333333333333"
SRC_A = "aaaaaaaa-0000-0000-0000-000000000001"
SRC_CO = "aaaaaaaa-0000-0000-0000-000000000002"
SRC_CC = "aaaaaaaa-0000-0000-0000-000000000003"
SRC_LP = "aaaaaaaa-0000-0000-0000-000000000004"
EWR_GUID = "56535ECB-1111-2222-3333-444444444444"
# 「场区 1」的 UTF-16LE hex（CONVERT(varbinary)→style 2 文本通道），手工固定防自证
FIELD_NAME_HEX = "3A573A5320003100"


def _fake_rows(expectations):
    """expectations: list of (needle, rows)；按首次命中消费一次，未命中即失败。"""
    def fake(db, sql, timeout=600):
        s = " ".join(sql.split())
        for i, (needle, rows) in enumerate(expectations):
            if needle in s:
                del expectations[i]
                return rows
        raise AssertionError("未预期的查询: " + s[:300])
    return fake


def _schema_rows(schema):
    return [[col, "nvarchar"] for col in schema]


def _hex_of(text: str) -> str:
    """UTF-16LE hex（与 sqlcmd style 2 输出同形）；期望值由测试自己算，不借被测解码器。"""
    return text.encode("utf-16-le").hex().upper()


def _sized_row(schema, values):
    """按 `_clone_literals` 的列序造一行：每个 t 列后面紧跟它的 DATALENGTH。

    DATALENGTH 取自「文本的 UTF-16LE 字节数」，不是从 hex token 反推 ⇒ 想造截断负例
    只要把长度值改大（库里 216 字节、通道只回读 128），断言才会真的抓得住。
    """
    row = []
    for name, kind in schema.items():
        value = values[name]
        if kind != "t":
            row.append("NULL" if value is None else str(value))
            continue
        if value is None:
            row.extend(["NULL", "-1"])
        else:
            encoded = str(value).encode("utf-16-le")
            row.extend([encoded.hex().upper(), str(len(encoded))])
    return row


class ReadChannels:
    """接管两条读通道：普通定宽读 `_rows` + P1-A 的变长定宽读 `_sized_rows`/`_var_width`。"""

    def __init__(self, expectations):
        self.fake = _fake_rows(expectations)
        self.probes: list[str] = []
        self.widths: list[int] = []
        self.sized_sqls: list[str] = []

    def _rows(self, db, sql, timeout=600):
        return self.fake(db, sql)

    def _sized(self, db, sql, width, timeout=600):
        # 没有宽度就不许走变长通道：这正是 P1-A 的失效形态（默认 -W 按 256 字符裁）。
        self.widths.append(width)
        self.sized_sqls.append(sql)
        return self.fake(db, sql)

    def _width(self, db, probe_sql, timeout=120):
        self.probes.append(probe_sql)
        return 8000

    def __enter__(self):
        self._patches = [
            mock.patch.object(si, "_rows", side_effect=self._rows),
            mock.patch.object(si, "_sized_rows", side_effect=self._sized),
            mock.patch.object(si, "_var_width", side_effect=self._width),
        ]
        for patcher in self._patches:
            patcher.start()
        return self

    def __exit__(self, *exc):
        for patcher in self._patches:
            patcher.stop()
        return False


class TestSynthesisImportUnit(unittest.TestCase):
    def test_apply_ok_whitelist(self):
        self.assertTrue(si.apply_ok("MUPLocalDb_site_verify"))
        self.assertTrue(si.apply_ok("x_test"))
        self.assertTrue(si.apply_ok("x_sandbox"))
        self.assertFalse(si.apply_ok("MUPLocalDb"))
        self.assertFalse(si.apply_ok("master"))

    def test_spec_validate(self):
        ok = si.SynthesisSpec(site="DEMO")
        self.assertEqual(ok.validate(), [])
        bad = si.SynthesisSpec(site="", stability=-1, density=-1, altitude=-1,
                               sector_step=-1, height=-1)
        self.assertEqual(len(bad.validate()), 6)  # site + 5 个负值

    def test_form_defaults_match_synthesis6(self):
        """综合6 表单口径：密度1.255/海拔0/分离区22.5、全不勾。"""
        s = si.SynthesisSpec(site="DEMO")
        self.assertEqual((s.density, s.altitude, s.sector_step), (1.255, 0.0, 22.5))
        self.assertFalse(s.use_turbulence)
        self.assertFalse(s.compute_profiles)
        self.assertFalse(s.compute_wake)
        self.assertFalse(s.compute_curtailment)
        self.assertFalse(s.compute_temporal)
        self.assertIsNone(s.compute_iec)          # 表单外字段沿用克隆源
        self.assertIsNone(s.compute_extreme_wind)
        self.assertFalse(s.clear_extreme_wind_reference)

    def test_lit_pure_vs_or_null(self):
        """S-P2-2：_lit 不再折叠字面 'NULL'；sqlcmd 渲染文本走 _lit_or_null。"""
        self.assertEqual(si._lit(None), "NULL")
        self.assertEqual(si._lit("NULL"), "N'NULL'")     # 名称/描述恰为 'NULL' 时保留文本
        self.assertEqual(si._lit("abc"), "N'abc'")
        self.assertEqual(si._lit("a'b"), "N'a''b'")
        self.assertEqual(si._lit_or_null(None), "NULL")
        self.assertEqual(si._lit_or_null("NULL"), "NULL")
        self.assertEqual(si._lit_or_null(" null "), "NULL")
        self.assertEqual(si._lit_or_null("a|b"), "N'a|b'")

    def test_num_formatting(self):
        self.assertEqual(si._num(1.255), "1.255")
        self.assertEqual(si._num(22.5), "22.5")
        self.assertEqual(si._num(0.0), "0.0")
        self.assertEqual(si._num(6), "6")
        self.assertEqual(si._num(True), "1")
        self.assertEqual(si._num(None), "NULL")

    def test_column_contracts_complete(self):
        """克隆/写入涉及的表必须有列契约，且主键/外键列在契约内（含 EWR 两列）。"""
        required = {
            "ComputationObject": ["Id", "Discriminator", "Parent_Id"],
            "WRAAnalysis": ["Id", "Name", "SynthesisNumber", "ReferenceClimatology_Id",
                            "ComputationObject_Id", "ExtremeWindReference_Id",
                            "ExtremeWindReferencePointId"],
            "WRAAnalysisParameters": ["Id", "WRAAnalysis_Id",
                                      "Climatology_ReferenceAirDensity",
                                      "Global_SectorStep", "Global_ComputeWake"],
            "WRAAnalysisElements": ["Id", "Discriminator", "Element_Id",
                                    "WindTurbineType_Id", "WRAAnalysis_Id",
                                    "SurfaceZone_Id"],
            "WRAAnalysisComputationPointConfidenceMethod": ["Id", "ComputationId",
                                                            "WRAAnalysisElement_Id"],
            "WRAAnalysisClimatologyConfiguration": ["Id", "ComputationId", "Element_Id",
                                                    "Climatology_Id", "SelectedHeight",
                                                    "UseTurbulence"],
        }
        tables = {"ComputationObject": si.T_COMPUTATION_OBJECT,
                  "WRAAnalysis": si.T_WRA_ANALYSIS,
                  "WRAAnalysisParameters": si.T_PARAMETERS,
                  "WRAAnalysisElements": si.T_ELEMENTS,
                  "WRAAnalysisComputationPointConfidenceMethod": si.T_CPM,
                  "WRAAnalysisClimatologyConfiguration": si.T_CLIMATO_CONFIG}
        for table, cols in required.items():
            for col in cols:
                self.assertIn(col, tables[table], f"{table}.{col} 缺契约")
        # 类型类合法；自由文本列必须走 hex 通道（'t'）
        for schema in tables.values():
            for kind in schema.values():
                self.assertIn(kind, ("s", "n", "t"))
        self.assertEqual(si.T_WRA_ANALYSIS["Name"], "t")
        self.assertEqual(si.T_WRA_ANALYSIS["Description"], "t")
        self.assertEqual(si.T_LARGE_PARK["LargeParkParameters_Name"], "t")

    def test_link_tables_shape(self):
        self.assertEqual(set(si.T_LINK_CFD), {"WRAAnalysis_Id", "CFD_Id"})
        self.assertEqual(set(si.T_LINK_CLIMATO),
                         {"WRAAnalysis_Id", "WRAAnalysisClimatologyConfiguration_Id"})
        self.assertEqual(si.T_LINK_CLIMATO, si.T_LINK_TEMPORAL)

    def test_parser_defaults(self):
        p = si._build_parser()
        args = p.parse_args(["--site", "DEMO", "--db", "MUPLocalDb_site_verify"])
        self.assertEqual(args.configuration, "CaseElementConfiguration")
        self.assertEqual(args.density, 1.255)
        self.assertEqual(args.sector_step, 22.5)
        self.assertFalse(args.use_turbulence)
        self.assertFalse(args.compute_temporal)
        self.assertIsNone(args.compute_iec)
        self.assertFalse(args.clear_extreme_wind_reference)

    def test_danger_write_prod_flag_removed(self):
        """S-P2-1：假开关 --danger-write-prod 已删除（argparse 直接报错）。"""
        p = si._build_parser()
        with self.assertRaises(SystemExit):
            p.parse_args(["--site", "x", "--db", "y", "--danger-write-prod"])


class TestCloneLiterals(unittest.TestCase):
    LP_SCHEMA = {"Id": "s", "LargeParkParameters_Name": "t"}
    TN_SCHEMA = {"Name": "t", "Id": "s"}

    @contextlib.contextmanager
    def channel(self, rows):
        """把定宽/变长两条读缝都接到同一份行数据（P1-A 后 t 列走 _sized_rows）。"""
        def same(db, sql, *args, **kwargs):
            return rows
        with mock.patch.object(si, "_rows", side_effect=same), \
                mock.patch.object(si, "_sized_rows", side_effect=same), \
                mock.patch.object(si, "_var_width", return_value=8000):
            yield

    def test_text_columns_via_hex_channel(self):
        """S-P2-3：'t' 列走 hex 通道，'|' 与引号不再错位/破坏。"""
        rows = [_sized_row(self.LP_SCHEMA,
                           {"Id": SRC_LP, "LargeParkParameters_Name": "场区 1"})]
        self.assertEqual(rows[0], [SRC_LP, FIELD_NAME_HEX, "8"])   # 期望值自己算，防自证
        with self.channel(rows):
            out = si._clone_literals(DB_OK, "WRAAnalysisLargeParkConfiguration",
                                     self.LP_SCHEMA, f"Id=N'{SRC_LP}'", {})
        self.assertEqual(out["Id"], f"N'{SRC_LP}'")
        self.assertEqual(out["LargeParkParameters_Name"], "N'场区 1'")

    def test_text_columns_null_via_hex_channel(self):
        rows = [_sized_row(self.TN_SCHEMA, {"Name": None, "Id": SRC_LP})]
        self.assertEqual(rows[0], ["NULL", "-1", SRC_LP])
        with self.channel(rows):
            out = si._clone_literals(DB_OK, "T", self.TN_SCHEMA, f"Id=N'{SRC_LP}'", {})
        self.assertEqual(out["Name"], "NULL")

    def test_pipe_misalignment_fails_closed(self):
        """S-P2-3：token 数 ≠ 列数+长度列数（文本含 '|'）必须 fail-closed，不得静默错位。"""
        rows = [["a|b", "2", "c", "d"]]     # 2 列 schema ⇒ 期望 3 token，实得 4
        with self.channel(rows):
            with self.assertRaises(LookupError) as ctx:
                si._clone_literals(DB_OK, "T", self.TN_SCHEMA, f"Id=N'{SRC_LP}'", {})
        self.assertIn("fail-closed", str(ctx.exception))

    def test_long_text_is_read_whole_and_not_truncated(self):
        """P1-A 正例：>64 字符（旧通道被裁到 256 个 hex 字符 = 64 个 UTF-16 字符）读全。"""
        long_text = "综" * 145
        schema = {"Name": "t", "Id": "s"}
        row = _sized_row(schema, {"Name": long_text, "Id": SRC_LP})
        self.assertGreater(len(row[0]), 256, "用例本身要能触发旧通道的裁剪长度")
        with self.channel([row]) as _ctx:
            out = si._clone_literals(DB_OK, "T", schema, f"Id=N'{SRC_LP}'", {})
        self.assertEqual(out["Name"], "N'" + long_text + "'")
        self.assertEqual(len(out["Name"]) - 3, 145)

    def test_width_probe_is_asked_before_reading_variable_columns(self):
        """P1-A：读变长列前必须先探宽度（否则默认 -W 通道按 256 字符裁）。"""
        seen = {}
        schema = {"Name": "t", "Id": "s"}
        row = _sized_row(schema, {"Name": "abc", "Id": SRC_LP})

        def width(db, probe_sql, timeout=120):
            seen["probe"] = probe_sql
            return 8000

        def sized(db, sql, width_value, timeout=600):
            seen["sql"] = sql
            seen["width"] = width_value
            return [row]

        with mock.patch.object(si, "_var_width", side_effect=width), \
                mock.patch.object(si, "_sized_rows", side_effect=sized):
            si._clone_literals(DB_OK, "T", schema, f"Id=N'{SRC_LP}'", {})
        self.assertIn("DATALENGTH(Name)", seen["probe"])
        read = " ".join(seen["sql"].split())
        self.assertIn("FROM dbo.T WHERE", read)
        self.assertIn("CONVERT(varbinary(max), Name), 2) END", read)
        # 读 SQL 自己也要带回 DATALENGTH：完整性断言不能依赖「宽度够就行」的假设
        self.assertIn("CASE WHEN Name IS NULL THEN -1 ELSE DATALENGTH(Name) END", read)
        self.assertIn(", Id FROM", read)
        self.assertGreaterEqual(seen["width"], 512)

    def test_truncated_hex_vs_datalength_fails_closed(self):
        """P1-A 负例：库里 216 字节、通道只回读 128 字节 ⇒ 解码前就停，不按残缺值克隆。"""
        schema = {"Name": "t", "Id": "s"}
        row = _sized_row(schema, {"Name": "x" * 64, "Id": SRC_LP})
        row[1] = str(108)      # DATALENGTH 报 108 字节（真值），hex 只有 64*2=128 字符
        row[0] = row[0][:128]  # 模拟 -W 通道把 hex 裁到 256 显示字符内
        with self.channel([row]):
            with self.assertRaises(LookupError) as ctx:
                si._clone_literals(DB_OK, "T", schema, f"Id=N'{SRC_LP}'", {})
        self.assertIn("定宽读取不完整", str(ctx.exception))
        self.assertIn("2×DATALENGTH", str(ctx.exception))

    def test_odd_length_hex_fails_closed(self):
        schema = {"Name": "t", "Id": "s"}
        with self.channel([["ABC", "1", SRC_LP]]):
            with self.assertRaises(LookupError) as ctx:
                si._clone_literals(DB_OK, "T", schema, f"Id=N'{SRC_LP}'", {})
        self.assertIn("奇数", str(ctx.exception))

    def test_column_over_the_y_limit_stops_instead_of_guessing(self):
        """P1-A：单列超过 sqlcmd -y 上限时 var_column_width 抛错，必须一路 fail-closed。"""
        schema = {"Name": "t", "Id": "s"}
        with mock.patch.object(si, "_var_width",
                               side_effect=RuntimeError("超过 sqlcmd -y 上限 8000")):
            literals, res = si._clone_literals_result(DB_OK, "T", schema,
                                                      f"Id=N'{SRC_LP}'", {})
        self.assertIsNone(literals)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.DB_GUARD)
        self.assertIn("未写库", res.message)
        self.assertEqual(res.exit_code(), 4)

    def test_extreme_wind_reference_clone_value(self):
        """S-P1-1：克隆行的 EWR 列原样带出（含 NOT NULL 形态）。"""
        schema = {"Id": "s", "ExtremeWindReference_Id": "s",
                  "ExtremeWindReferencePointId": "n"}
        rows = [[SRC_A, EWR_GUID, "1157"]]
        with mock.patch.object(si, "_rows", return_value=rows):
            out = si._clone_literals(DB_OK, "WRAAnalysis", schema,
                                     f"Id=N'{SRC_A}'", {})
        self.assertEqual(out["ExtremeWindReference_Id"], f"N'{EWR_GUID}'")
        self.assertEqual(out["ExtremeWindReferencePointId"], "1157")


class TestBuildPlan(unittest.TestCase):
    def _expectations(self, ewr_token=EWR_GUID):
        exp = [
            ("WHERE t.name=N'ComputationObject'", _schema_rows(si.T_COMPUTATION_OBJECT)),
            ("WHERE t.name=N'WRAAnalysis'", _schema_rows(si.T_WRA_ANALYSIS)),
            ("WHERE t.name=N'WRAAnalysisClimatologyConfiguration'",
             _schema_rows(si.T_CLIMATO_CONFIG)),
            ("WHERE t.name=N'WRAAnalysis_ClimatologyConfiguration'",
             _schema_rows(si.T_LINK_CLIMATO)),
            ("WHERE t.name=N'WRAAnalysis_TemporalClimatology'",
             _schema_rows(si.T_LINK_TEMPORAL)),
            ("WHERE t.name=N'WRAAnalysis_CFDs'", _schema_rows(si.T_LINK_CFD)),
            ("WHERE t.name=N'WRAAnalysisElements'", _schema_rows(si.T_ELEMENTS)),
            ("WHERE t.name=N'WRAAnalysisComputationPointConfidenceMethod'",
             _schema_rows(si.T_CPM)),
            ("WHERE t.name=N'WRAAnalysisLargeParkConfiguration'",
             _schema_rows(si.T_LARGE_PARK)),
            ("WHERE t.name=N'WRAAnalysisParameters'", _schema_rows(si.T_PARAMETERS)),
            ("FROM dbo.Sites WHERE Id=", [[SITE_ID, "DEMO"]]),
            ("FROM dbo.Cases WHERE Parent_Id=", [[CASE_ID, "WT"]]),
            ("FROM dbo.Configurations WHERE Case_Id=", [[CFG_ID, "CaseElementConfiguration"]]),
            ("FROM dbo.Configuration_Element WHERE ConfigurationId=", [["2"]]),
            # _analyses_of_case 被调两次（克隆源选择 + 编号基线）
            ("ORDER BY a.SynthesisNumber", [[SRC_A, "综合1", "1", SRC_CO, "2"]]),
            ("ORDER BY a.SynthesisNumber", [[SRC_A, "综合1", "1", SRC_CO, "2"]]),
            ("AND NOT EXISTS", [["0"]]),
            ("JOIN dbo.CFD cfd ON", [["CFO1", "22", "2"], ["CFO2", "45", "2"],
                                     ["CFO3", "67", "2"]]),
            ("OR a.SynthesisNumber=", [["0"]]),
            ("FROM dbo.WRAAnalysis WHERE Id=",
             [_sized_row(si.T_WRA_ANALYSIS,
                         {"Id": SRC_A, "Description": None, "Name": "综合1",
                          "SynthesisNumber": "1", "ExtremeWindReferencePointId": "1157",
                          "ExtremeWindReference_Id": ewr_token,
                          "ReferenceClimatology_Id": SRC_CC,
                          "ComputationObject_Id": SRC_CO})]),
            ("SELECT Wake_AlphaMax, SpeedReductionModel, Weibull_MethodMapping",
             [["0.075", "3", "1"]]),
            ("FROM dbo.WRAAnalysis_ClimatologyConfiguration WHERE WRAAnalysis_Id=",
             [[SRC_CC]]),
            ("FROM dbo.WRAAnalysisClimatologyConfiguration WHERE Id=",
             [[SRC_CC, "5", "0", "80.0", "0.1", "CLIM-1", "EL-1"]]),
            ("FROM dbo.WRAAnalysisClimatologyConfiguration WHERE Id=",
             [[SRC_CC, "5", "0", "80.0", "0.1", "CLIM-1", "EL-1"]]),
            ("FROM dbo.WRAAnalysisLargeParkConfiguration WHERE WRAAnalysis_Id=",
             [[SRC_LP]]),
            ("FROM dbo.WRAAnalysisLargeParkConfiguration WHERE Id=",
             [_sized_row(si.T_LARGE_PARK,
                         {"Id": SRC_LP, "LargeParkParameters_HMean": "100",
                          "LargeParkParameters_DMean": "100", "LargeParkParameters_Z0": "0.05",
                          "LargeParkParameters_HStart": "0", "LargeParkParameters_Ibl1": "0.05",
                          "LargeParkParameters_Ibl2": "0.3", "LargeParkThrustCurve_Id": "THR-1",
                          "WRAAnalysis_Id": SRC_A,
                          "LargeParkParameters_Name": "场区 1"})]),
            ("SELECT Id, Discriminator, Element_Id, WindTurbineType_Id, SurfaceZone_Id",
             [["E1", "MTDWRAAnalysisElementsTurbine", "EL-1", "WT-1", "NULL"],
              ["E2", "MTDWRAAnalysisElementsTurbine", "EL-2", "WT-2", "NULL"]]),
            ("FROM dbo.WRAAnalysisComputationPointConfidenceMethod",
             [["CP1", "1157", "0", "NULL", "E1", "0"],
              ["CP2", "1158", "0", "NULL", "E2", "0"]]),
            ("FROM dbo.WRAAnalysisParameters WHERE WRAAnalysis_Id=",
             [["0.0"] * len(si.T_PARAMETERS)]),
        ]
        return exp

    def _build_plan(self, clear_ewr=False):
        spec = si.SynthesisSpec(site="DEMO", clone_from="综合1",
                                clear_extreme_wind_reference=clear_ewr)
        with ReadChannels(self._expectations()):
            result = si.build_plan(spec, DB_OK)
        self.assertTrue(result.ok, result.message)
        return result.data["plan_obj"]

    @staticmethod
    def _wra_insert_mapping(sql: str) -> dict:
        stmt = [ln for ln in sql.splitlines()
                if ln.startswith("INSERT INTO dbo.WRAAnalysis ")][0]
        names = stmt[stmt.index("(") + 1:stmt.index(") VALUES")].split(", ")
        vals = stmt[stmt.index("VALUES (") + len("VALUES ("):stmt.rindex(");")].split(", ")
        return dict(zip(names, vals))

    def test_build_plan_clones_extreme_wind_reference_by_default(self):
        """S-P1-1：默认克隆 EWR 列（非空金样形态）。"""
        plan = self._build_plan(clear_ewr=False)
        mapping = self._wra_insert_mapping(plan.sql)
        self.assertEqual(mapping["ExtremeWindReference_Id"], f"N'{EWR_GUID}'")
        self.assertEqual(mapping["ExtremeWindReferencePointId"], "1157")
        self.assertEqual(mapping["Name"], "N'综合2'")
        self.assertFalse(plan.clear_extreme_wind_reference)
        # hex 通道：大型风电场配置名称解码回中文
        lp_stmt = [ln for ln in plan.sql.splitlines()
                   if ln.startswith("INSERT INTO dbo.WRAAnalysisLargeParkConfiguration")][0]
        self.assertIn("N'场区 1'", lp_stmt)

    def test_build_plan_clear_extreme_wind_reference_switch(self):
        """P3-F：清空开关必须成对 (_Id=NULL, PointId=0)。

        过去只清 _Id 而 PointId 仍克隆 1157，造出生产从未观测的「半清空」行形状
        （生产 5 行只有 (NULL,0)×2 与 (非空,1157)×3）。
        """
        plan = self._build_plan(clear_ewr=True)
        mapping = self._wra_insert_mapping(plan.sql)
        self.assertEqual(mapping["ExtremeWindReference_Id"], "NULL")
        self.assertEqual(mapping["ExtremeWindReferencePointId"], "0")
        self.assertTrue(plan.clear_extreme_wind_reference)

    def test_build_plan_null_ewr_clone_stays_null(self):
        """S-P1-1：克隆源该列为 NULL 时默认仍写 NULL（两种形态都可克隆）。"""
        exp = self._expectations(ewr_token="NULL")
        with ReadChannels(exp):
            result = si.build_plan(si.SynthesisSpec(site="DEMO", clone_from="综合1"), DB_OK)
        self.assertTrue(result.ok, result.message)
        mapping = self._wra_insert_mapping(result.data["plan_obj"].sql)
        self.assertEqual(mapping["ExtremeWindReference_Id"], "NULL")
        self.assertEqual(mapping["ExtremeWindReferencePointId"], "1157")

    def test_build_plan_never_writes_half_cleared_extreme_wind_shape(self):
        """P3-F 形状锁：两列只能是 (NULL,0) 或 (非空,非0)，不许出现 (NULL,1157)。"""
        for clear in (False, True):
            mapping = self._wra_insert_mapping(self._build_plan(clear_ewr=clear).sql)
            ref_is_null = mapping["ExtremeWindReference_Id"] == "NULL"
            point_is_zero = mapping["ExtremeWindReferencePointId"] == "0"
            self.assertEqual(ref_is_null, point_is_zero,
                             "半清空形状: %r" % (mapping,))

    def test_sqlcmd_derived_null_tokens_stay_null(self):
        """S-P2-2 联动：sqlcmd 'NULL' token 进 INSERT 必须还原 NULL 关键字
        （SurfaceZone_Id/Zone_Id），且任何位置不得把 NULL 文本写成 N'NULL' 字符串。"""
        plan = self._build_plan()
        self.assertNotIn("N'NULL'", plan.sql)
        el_stmt = [ln for ln in plan.sql.splitlines()
                   if ln.startswith("INSERT INTO dbo.WRAAnalysisElements")][0]
        names = el_stmt[el_stmt.index("(") + 1:el_stmt.index(") VALUES")].split(", ")
        vals = el_stmt[el_stmt.index("VALUES (") + len("VALUES ("):el_stmt.rindex(");")].split(", ")
        mapping = dict(zip(names, vals))
        self.assertEqual(mapping["SurfaceZone_Id"], "NULL")
        cpm_stmt = [ln for ln in plan.sql.splitlines()
                    if ln.startswith("INSERT INTO dbo.WRAAnalysisComputationPointConfidenceMethod")][0]
        names = cpm_stmt[cpm_stmt.index("(") + 1:cpm_stmt.index(") VALUES")].split(", ")
        vals = cpm_stmt[cpm_stmt.index("VALUES (") + len("VALUES ("):cpm_stmt.rindex(");")].split(", ")
        mapping = dict(zip(names, vals))
        self.assertEqual(mapping["Zone_Id"], "NULL")

    def test_build_plan_reads_text_columns_through_the_sized_channel(self):
        """P1-A 管道级：整条 build_plan 必须用「先探宽度、再定宽读」的通道取 t 列。"""
        channels = ReadChannels(self._expectations())
        with channels:
            result = si.build_plan(si.SynthesisSpec(site="DEMO", clone_from="综合1"), DB_OK)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(channels.probes, "没有做过 DATALENGTH 宽度探测")
        self.assertTrue(all("DATALENGTH(" in p for p in channels.probes), channels.probes)
        self.assertTrue(channels.widths and all(w >= 512 for w in channels.widths),
                        channels.widths)
        # WRAAnalysis（Description+Name）与 LargePark（Name）两处克隆读取都走定宽通道
        joined = " ".join(channels.sized_sqls)
        self.assertIn("FROM dbo.WRAAnalysis WHERE", joined)
        self.assertIn("FROM dbo.WRAAnalysisLargeParkConfiguration WHERE", joined)

    def test_build_plan_turns_clone_read_failure_into_fail_closed_result(self):
        """P1-A：读不出克隆源时不能抛异常穿出 main()，要返回「未写库」的明确负载。"""
        channels = ReadChannels(self._expectations())
        with channels:
            with mock.patch.object(si, "_var_width",
                                   side_effect=RuntimeError("超过 sqlcmd -y 上限 8000")):
                result = si.build_plan(si.SynthesisSpec(site="DEMO", clone_from="综合1"), DB_OK)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
        self.assertIn("未写库", result.message)
        self.assertEqual(result.exit_code(), 4)
        self.assertNotIn("plan_obj", result.data)

    def test_build_plan_reads_text_columns_through_the_sized_channel(self):
        """P1-A 管道级：整条 build_plan 必须用「先探宽度、再定宽读」的通道取 t 列。"""
        channels = ReadChannels(self._expectations())
        with channels:
            result = si.build_plan(si.SynthesisSpec(site="DEMO", clone_from="综合1"), DB_OK)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(channels.probes, "没有做过 DATALENGTH 宽度探测")
        self.assertTrue(all("DATALENGTH(" in p for p in channels.probes), channels.probes)
        self.assertTrue(channels.widths and all(w >= 512 for w in channels.widths),
                        channels.widths)
        # WRAAnalysis（Description+Name）与 LargePark（Name）两处克隆读取都走定宽通道
        joined = " ".join(" ".join(s) for s in [channels.sized_sqls])
        self.assertIn("FROM dbo.WRAAnalysis WHERE", joined)
        self.assertIn("FROM dbo.WRAAnalysisLargeParkConfiguration WHERE", joined)

    def test_build_plan_turns_clone_read_failure_into_fail_closed_result(self):
        """P1-A：读不出克隆源时不能抛异常穿出 main()，要返回「未写库」的明确负载。"""
        exp = self._expectations()
        channels = ReadChannels(exp)
        with channels:
            with mock.patch.object(si, "_var_width",
                                   side_effect=RuntimeError("超过 sqlcmd -y 上限 8000")):
                result = si.build_plan(si.SynthesisSpec(site="DEMO", clone_from="综合1"), DB_OK)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
        self.assertIn("未写库", result.message)
        self.assertEqual(result.exit_code(), 4)
        self.assertNotIn("plan_obj", result.data)


class TestVerify(unittest.TestCase):
    def _verify_expectations(self, ew_new=None, ew_src=None):
        ew_new = [["1157", EWR_GUID]] if ew_new is None else ew_new
        ew_src = [["1157", EWR_GUID]] if ew_src is None else ew_src
        return [
            ("COUNT(*) FROM dbo.ComputationObject WHERE Id=",
             [["1", "1", "1", "1", "1", "2", "2", "3", "1", "0"]]),
            ("JOIN dbo.CFD cfd", [["3"]]),
            ("ISNULL(SUM(ComputationId),0), COUNT(DISTINCT ComputationId)",
             [["2315", "2"]]),
            ("ISNULL(SUM(ComputationId),0), COUNT(DISTINCT ComputationId)",
             [["2315", "2"]]),
            ("FROM dbo.WRAAnalysisElements s", [["2"]]),
            ("WHERE Id=N'", [["0", "80.0", "CLIM-1", "EL-1", "5"]]),
            ("WHERE Id=(SELECT", [["0", "80.0", "CLIM-1", "EL-1", "5"]]),
            ("SELECT Climatology_ReferenceAirDensity",
             [["1.255", "0.0", "22.5", "0", "0", "0", "0", "0.075", "3", "1"]]),
            ("SELECT ExtremeWindReferencePointId, ExtremeWindReference_Id", ew_new),
            ("SELECT ExtremeWindReferencePointId, ExtremeWindReference_Id", ew_src),
            ("AND a.Id<>", [["0"]]),
        ]

    def test_verify_passes_with_cloned_extreme_wind(self):
        plan = TestBuildPlan()._build_plan()
        with mock.patch.object(si, "_rows",
                               side_effect=_fake_rows(self._verify_expectations())):
            result = si._verify(DB_OK, plan)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.data["checks"]["extreme_wind_reference"])

    def test_verify_detects_extreme_wind_drift(self):
        """S-P1-1：EWR 列漂移必须被抓（此前完全不查）。"""
        plan = TestBuildPlan()._build_plan()
        exp = self._verify_expectations(ew_new=[["1157", EWR_GUID]],
                                        ew_src=[["1157", "NULL"]])
        with mock.patch.object(si, "_rows", side_effect=_fake_rows(exp)):
            result = si._verify(DB_OK, plan)
        self.assertFalse(result.ok)
        self.assertIn("extreme_wind_reference", result.data["failed"])

    def test_verify_extreme_wind_cleared_branch(self):
        """P3-F：清空分支要同时断言两列成对 (_Id=NULL, PointId=0)。"""
        plan = TestBuildPlan()._build_plan(clear_ewr=True)
        exp = self._verify_expectations(ew_new=[["0", "NULL"]],
                                        ew_src=[["1157", EWR_GUID]])
        with mock.patch.object(si, "_rows", side_effect=_fake_rows(exp)):
            result = si._verify(DB_OK, plan)
        self.assertTrue(result.ok, result.message)
        self.assertTrue(result.data["checks"]["extreme_wind_reference"])
        self.assertTrue(result.data["checks"]["extreme_wind_reference_point_zeroed"])

    def test_verify_rejects_half_cleared_extreme_wind_shape(self):
        """P3-F 负例：_Id 清空但 PointId 仍是 1157 ⇒ 校验失败（过去这条没人查）。"""
        plan = TestBuildPlan()._build_plan(clear_ewr=True)
        exp = self._verify_expectations(ew_new=[["1157", "NULL"]],
                                        ew_src=[["1157", EWR_GUID]])
        with mock.patch.object(si, "_rows", side_effect=_fake_rows(exp)):
            result = si._verify(DB_OK, plan)
        self.assertFalse(result.ok)
        self.assertIn("extreme_wind_reference_point_zeroed", result.data["failed"])

    def test_verify_cleared_branch_still_checks_the_id_column(self):
        plan = TestBuildPlan()._build_plan(clear_ewr=True)
        exp = self._verify_expectations(ew_new=[["0", EWR_GUID]],
                                        ew_src=[["1157", EWR_GUID]])
        with mock.patch.object(si, "_rows", side_effect=_fake_rows(exp)):
            result = si._verify(DB_OK, plan)
        self.assertFalse(result.ok)
        self.assertIn("extreme_wind_reference", result.data["failed"])


class TestRunAndApplyGuards(unittest.TestCase):
    @staticmethod
    def _plan():
        return si.WritePlan(
            spec=si.SynthesisSpec(site="DEMO"), db=DB_OK,
            site_id=SITE_ID, site_name="DEMO", case_id=CASE_ID, case_name="WT",
            analysis_id="A", computation_object_id="CO", climato_config_id="CC",
            name="综合9", synthesis_number=9, clone_from_name="综合1",
            clone_from_id=SRC_A, element_count=2, cpm_count=2, cpm_insert_count=2,
            cfd_count=3, temporal=False, clear_extreme_wind_reference=False,
            overrides={"reference_param_wake": {"Wake_AlphaMax": "0.075",
                                                "SpeedReductionModel": "3",
                                                "Weibull_MethodMapping": "1"}},
            sql="APPLY-SQL", rollback_sql="ROLLBACK-SQL")

    def test_run_refuses_prod_db_before_any_query(self):
        with mock.patch.object(si, "_rows") as rows:
            result = si.run(si.SynthesisSpec(site="DEMO"), "MUPLocalDb", apply=True)
            self.assertFalse(result.ok)
            self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
            rows.assert_not_called()

    def test_run_refuses_prod_db_case_insensitive(self):
        with mock.patch.object(si, "_rows") as rows:
            result = si.run(si.SynthesisSpec(site="DEMO"), "muplocaldb", apply=True)
            self.assertFalse(result.ok)
            self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
            rows.assert_not_called()

    def test_run_refuses_non_whitelist(self):
        with mock.patch.object(si, "_rows") as rows:
            result = si.run(si.SynthesisSpec(site="DEMO"), "MUPLocalDb_copy", apply=True)
            self.assertFalse(result.ok)
            self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
            rows.assert_not_called()

    def test_apply_plan_refuses_prod_db_before_write(self):
        with mock.patch.object(mw, "_run_sql") as rs:
            result = si.apply_plan("MUPLocalDb", self._plan())
            self.assertFalse(result.ok)
            self.assertEqual(result.code, mw.ImportCode.DB_GUARD)
            rs.assert_not_called()

    def test_default_out_dir_is_temp_not_cwd(self):
        """S-P3-1：默认产物目录落 %TEMP%，不再落仓库。"""
        plan_builder = TestBuildPlan()
        with ReadChannels(plan_builder._expectations()):
            result = si.run(si.SynthesisSpec(site="DEMO", clone_from="综合1"), DB_OK,
                            apply=False)
        self.assertTrue(result.ok, result.message)
        out_dir = Path(result.data["out_dir"])
        self.assertIn("mup_synthesis_import", out_dir.name)
        self.assertEqual(out_dir.parent, Path(tempfile.gettempdir()))
        self.assertTrue((out_dir / "plan.json").exists())
        self.assertTrue((out_dir / "rollback.sql").exists())

    def test_apply_plan_auto_rolls_back_on_verify_failure(self):
        """S-P2-5：写后校验失败 → 自动执行 rollback.sql 并写回结果。"""
        failed = mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致",
                                 data={"failed": ["counts"]})
        with mock.patch.object(mw, "_run_sql",
                               side_effect=[(True, "applied", None), (True, "rb-out", None)]) as rs, \
                mock.patch.object(si, "_verify", return_value=failed):
            result = si.apply_plan(DB_OK, self._plan())
        self.assertFalse(result.ok)
        self.assertEqual(result.code, mw.ImportCode.VERIFY)
        self.assertIn("已自动回滚", result.message)
        self.assertTrue(result.data["rollback"]["ok"])
        rs.assert_any_call(DB_OK, "ROLLBACK-SQL")

    def test_apply_plan_rollback_failure_escalates(self):
        failed = mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致", data={})
        with mock.patch.object(mw, "_run_sql",
                               side_effect=[(True, "applied", None), (False, "", "boom")]), \
                mock.patch.object(si, "_verify", return_value=failed):
            result = si.apply_plan(DB_OK, self._plan())
        self.assertFalse(result.ok)
        self.assertIn("需人工介入", result.message)
        self.assertFalse(result.data["rollback"]["ok"])


if __name__ == "__main__":
    unittest.main()
