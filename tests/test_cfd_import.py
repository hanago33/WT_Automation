# encoding: utf-8
"""mup_cfd_import 单元测试（不写库、不启动 GUI/HASP、不触发计算）。

覆盖：气象风频矩阵 → CFD.Direction 的金样规则复现（生产 16 个风向）、表单值 → 快照键值、
输入与前置门（风向空/越界、稳定度、Atlas、无地形、无绘图、无测风塔元素）、
写入 SQL 的行配对与回滚范围、写后校验能发现问题、生产库/非白名单库拒写、
计算作业优先级拒绝静默丢弃，以及 blob .NET 宿主的往返自检（真跑，缺 PowerShell 时跳过）。
"""
import argparse
import base64
import contextlib
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mup_cfd_import as cf
import mup_weather_import as mw

SITE_ID = "276b9616-655b-4ec5-a52b-539e01519489"
CASE_ID = "3fda28d9-ba92-4aec-b7cf-a099350aed3a"
CLIM_ID = "e61119b7-6d3c-4a04-9b6a-6d1a5e2c0001"
TEMPLATE_ID = "31ca750d-cc60-4af1-99f6-53da268c6e80"
FAKE_BLOB = b"BINARYFORMATTER-FIXTURE-BYTES"
FAKE_SHA = hashlib.sha256(FAKE_BLOB).hexdigest().upper()
COPY_DB = "MUPLocalDb_mapcfd_verify"

# 生产 C1831-125m 气象对象的 PersistedPercentagesOfWindFrequencyPerDirection 原文
# （2026-11-04 用 .NET SqlClient 从 dbo.ClimatologyFrequenciesContainers 取回）
PCT_C1831_125M = (
    "0|1.780061;22.5|3.051297;45|5.570564;67.5|4.793238;90|3.918124;112.5|3.961217;"
    "135|3.309853;157.5|2.131433;180|2.380045;202.5|3.720892;225|8.701417;"
    "247.5|29.55333;270|15.11395;292.5|6.599817;315|3.903207;337.5|1.51156;")

# 生产 DEMO 案例里 16 个 CFD.Direction：与上面的扇区一一对应（金样）
GOLDEN_DIRECTIONS = [22, 45, 67, 90, 112, 135, 157, 180, 202, 225, 247, 270, 292, 315, 337, 360]

# dbo.Parameters 里 DAS_/NSParameters_ 默认值的代表性子集（真库形状在下面合成完整表）
DEFAULTS = {
    "DAS_InitialResolution_Horizontal": "25",
    "DAS_InitialResolution_Vertical": "4",
    "DAS_ExpansionCoefficient_Horizontal": "1.1",
    "DAS_NumberIterations": "25",
    "DAS_NumberThreads": "1",
    "DAS_ConvergenceMonitoring": "True",
    "DAS_AutomaticCVG": "False",
    "DAS_ForceAllIteration": "False",
    "DAS_TurbulenceModel_Type": "KL",
    "DAS_Stability": "2",
    "NSParameters_nfirst": "10",
    "NSParameters_ratiomax": "100",
    "NSParameters_coefzdomain": "5",
    "NSParameters_kl_nbrelax": "1",
    "NSParameters_kl_falsdt": "500",
    "NSParameters_kl_falsdtk": "500",
    "NSParameters_kl_relin": "0.3",
    "NSParameters_kl_relink": "0.3",
    "NSParameters_rlat": "45",
}

# 真库形状（2026-11-05 逐键核对生产 dbo.Parameters）：117 个金样快照键里只有
# GOLDEN_SNAPSHOT_CONSTANTS 那 5 个没有默认值行，其余 112 个都有。mock 必须同构，
# 否则 built-fresh 路径测的是「假库缺键」而不是真库行为。
_REPRESENTATIVE = dict(DEFAULTS)
DEFAULTS = {key: "0" for key in list(cf.GOLDEN_DAS_KEYS) + list(cf.GOLDEN_NS_KEYS)
            if key not in cf.GOLDEN_SNAPSHOT_CONSTANTS}
DEFAULTS.update(_REPRESENTATIVE)
for _key in cf.GOLDEN_SNAPSHOT_CONSTANTS:
    DEFAULTS.pop(_key, None)


def db_rows(env_rows=None, mapping_rows=None, element_rows=None, existing_rows=None,
            site_rows=None, case_rows=None, clim_rows=None, params=None, cfd_rows=None,
            taken_rows=None):
    """按 SQL 语义返回行的 mock；显式传 [] 表示查不到，None 表示用金样默认。

    形参收 **_kw：读通道现在带 timeout/var_width（P1-A 定宽通道），mock 不参与那些细节。
    """
    def inner(db, sql, **_kw):
        table = DEFAULTS if params is None else params
        if "MAX(DATALENGTH(Value))" in sql:
            # 变长列定宽探测：mock 也要按「字节数」回答，否则探测不到宽就等于没测到通道
            return [[str(max([2 * len(v) for v in table.values()] + [0]))]]
        if "FROM dbo.Parameters" in sql:
            # 生产读取走 hex 通道（值里可能有 '|'），mock 同构：键 + DATALENGTH + hex
            return [[key, str(2 * len(value)), value.encode("utf-16-le").hex().upper()]
                    for key, value in sorted(table.items())]
        if "SELECT DISTINCT c.Direction" in sql:
            # (Direction, Stability) 重复拒闸门的读取（P2-D）：默认库里没有占用
            return [] if taken_rows is None else taken_rows
        if "FROM dbo.Sites" in sql:
            return ([[SITE_ID, "test", "43554124.0", "5145316.0"]] if site_rows is None
                    else site_rows)
        if "FROM dbo.Cases" in sql:
            return [[CASE_ID, "testWT Case"]] if case_rows is None else case_rows
        if "dbo.Case_EnvironmentDatas" in sql:
            if env_rows is not None:
                return env_rows
            return [["MTDSiteOrographyData"], ["MTDSiteRoughnessDataWithDatabase"]]
        if "dbo.ComputationObject" in sql:
            return [["0"]] if existing_rows is None else existing_rows
        if "FROM dbo.Climatologies" in sql:
            return [] if clim_rows == [] else (clim_rows or [[
                CLIM_ID, "C1831", CLIM_ID, "125", CLIM_ID, "-1", "100", CLIM_ID]])
        if "SELECT COUNT(*) FROM dbo.Elements" in sql:
            if "MTDElementMapping" in sql:
                return [["1"]] if mapping_rows is None else mapping_rows
            return [["5"]] if element_rows is None else element_rows
        if "FROM dbo.CFD" in sql:
            if "WHERE Id=" in sql:
                # 显式模板查询：只有真的问到 TEMPLATE_ID 才算命中
                return [[TEMPLATE_ID]] if ("'%s'" % TEMPLATE_ID) in sql else []
            return [[TEMPLATE_ID]] if cfd_rows is None else cfd_rows
        return []
    return inner


def blob_result(**kv_extra) -> mw.ImportResult:
    kv = {"SOURCE": "template", "SAME_AS_TEMPLATE": "False", "PART1_COUNT": "40",
          "PART2_COUNT": "77", "RT_PART1_COUNT": "40", "RT_PART2_COUNT": "77",
          "BLOB_LEN": str(len(FAKE_BLOB)), "BLOB_SHA": FAKE_SHA,
          "RT_PART1_TYPE": "System.Collections.Generic.List`1[[System.Collections."
                           "Generic.KeyValuePair`2", "ROUNDTRIP_MISMATCH": "0",
          "TEMPLATE_ONLY_KEYS": "DAS_ResolutionCoef"}
    kv.update(kv_extra)
    return mw.ImportResult(True, mw.ImportCode.OK, "ok",
                           data={"blob": FAKE_BLOB, "sha": FAKE_SHA, "kv": kv})


def blob_default(pairs_payload, work_dir, timeout=120) -> mw.ImportResult:
    # 宿主会按「有没有模板文件」如实回报 SOURCE，mock 也要跟着走，否则 built-fresh
    # 分支的备注文案根本不会被测到
    return blob_result(SOURCE="template" if pairs_payload.get("template") else "built-fresh")


def readback_ok(db, rows, timeout=180) -> mw.ImportResult:
    """模拟「库内 blob 反解回来的键值就是计划请求的那些」的直证结果。

    真正的反解在副本库重跑里做（需要 .NET 宿主 + 库内字节）；单测只关心 _verify 有没有
    把这条与 SHA 无关的断言接上、以及断言失败时会不会判失败。
    """
    checks: dict[str, bool] = {}
    for row in rows:
        tag = str(row["id"])[:8]
        checks["readback_row_" + tag] = True
        checks["readback_streams_" + tag] = True
        checks["readback_type_" + tag] = True
        checks["readback_pairs_" + tag] = True
    return mw.ImportResult(True, mw.ImportCode.OK, "库内参数快照内容直证通过",
                           data={"checks": checks, "readback": {}})


def readback_pairs_bad(db, rows, timeout=180) -> mw.ImportResult:
    """模拟并发污染后的真实结局：库里的 blob 反解出的是**别人**的表单项。"""
    checks: dict[str, bool] = {}
    detail: dict[str, dict[str, object]] = {}
    for row in rows:
        tag = str(row["id"])[:8]
        bad = sorted(row["expected"])
        checks["readback_row_" + tag] = True
        checks["readback_streams_" + tag] = True
        checks["readback_type_" + tag] = True
        checks["readback_pairs_" + tag] = False
        detail[str(row["id"])] = {"bad": len(bad), "bad_keys": ";".join(bad)}
    return mw.ImportResult(False, mw.ImportCode.VERIFY, "库内参数快照内容与计划请求的表单项不一致",
                           data={"checks": checks, "readback": detail})


def verify_row(row, plan, sha=None, parent=None, micro_atlas="") -> list:
    """_verify 主查询的一行读数（9 列，末列是 ComputationObject.Micro_AtlasId）。"""
    return [row["cfd_id"], row["direction"], row["stability"], cf.DISC_CFD_CONTAINER,
            sha or FAKE_SHA, len(plan.blob), row["computation_id"],
            parent or plan.case_id, micro_atlas]


def rows_atlas(main_rows, atlas_rows):
    """让 _query_rows 分别回答「主 CFD 行」和「Atlas 三行组」两条查询。"""
    def inner(db, sql, **_kw):
        if "dbo.CFDAtlas" in sql:
            return atlas_rows
        return main_rows
    return inner


def hex_row(key: str, value: str) -> list:
    """按生产读通道的列形状造一行默认值：键 + DATALENGTH(字节) + hex。"""
    # nvarchar 的 DATALENGTH 就是 UTF-16LE 的字节数（2 字节/字符）
    return [key, str(len(value.encode("utf-16-le"))), value.encode("utf-16-le").hex().upper()]


def params_rows(rows):
    """回答 _default_parameters 的两条查询：先探测最大字节数，再按 -y 定宽读数据。"""
    def inner(db, sql, **_kw):
        if "MAX(DATALENGTH(Value))" in sql:
            return [[str(max([int(r[1]) for r in rows if len(r) > 1] or [0]))]]
        return rows
    return inner


@contextlib.contextmanager
def offline(build=None, **row_kwargs):
    """只读探测 + blob 宿主全 mock：测试只关心计划与 SQL 装配。"""
    ok = mw.ImportResult(True, mw.ImportCode.OK, "ok", data={"size": 8682})
    with unittest.mock.patch.object(mw, "_query_rows", side_effect=db_rows(**row_kwargs)), \
            unittest.mock.patch.object(cf, "_download_blob", return_value=ok), \
            unittest.mock.patch.object(cf, "_dotnet_scalar_to_file",
                                       return_value=mw.ImportResult(
                                           True, mw.ImportCode.OK, "ok",
                                           data={"text": PCT_C1831_125M})), \
            unittest.mock.patch.object(cf, "_readback_blob_pairs", side_effect=readback_ok), \
            unittest.mock.patch.object(cf, "build_blob",
                                       side_effect=blob_default if build is None else build):
        yield


def plan_for(**kwargs) -> mw.ImportResult:
    row_kwargs = {key: kwargs.pop(key) for key in
                  ("env_rows", "mapping_rows", "element_rows", "existing_rows", "site_rows",
                   "case_rows", "clim_rows", "params", "cfd_rows", "taken_rows") if key in kwargs}
    target = cf.CfdSpec(**kwargs)
    with offline(**row_kwargs):
        return cf.build_plan(target, COPY_DB)


class TestWindRoseProvenance(unittest.TestCase):
    """风向/热稳定度来自气象对象的风频矩阵，不是 Climatology 名称本身。"""

    def test_percentage_text_decodes_sixteen_sectors(self):
        pairs = cf._decode_percentages(PCT_C1831_125M)
        self.assertEqual(len(pairs), 16)
        self.assertEqual(pairs[0], (0.0, 1.780061))
        self.assertEqual(pairs[-1][0], 337.5)

    def test_sector_to_direction_reproduces_production_golden(self):
        # 金样规则 trunc(扇区起点 + 扇区宽)：0→22 … 337.5→360
        dirs = cf._sector_directions(cf._decode_percentages(PCT_C1831_125M))
        self.assertEqual(dirs, GOLDEN_DIRECTIONS)

    def test_zero_frequency_sector_is_dropped(self):
        pairs = cf._decode_percentages(PCT_C1831_125M)
        muted = [(start, 0.0 if start == 247.5 else pct) for start, pct in pairs]
        dirs = cf._sector_directions(muted)
        self.assertNotIn(270, dirs)                     # 247.5 扇区被清零
        self.assertEqual(len(dirs), 15)

    def test_empty_or_garbage_text_yields_nothing(self):
        self.assertEqual(cf._decode_percentages(""), [])
        self.assertEqual(cf._decode_percentages("not-a-rose"), [])
        self.assertEqual(cf._sector_directions([]), [])

    def test_base64_wrapped_channel_is_accepted(self):
        wrapped = base64.b64encode(PCT_C1831_125M.encode("utf-8")).decode("ascii")
        self.assertEqual(len(cf._decode_percentages(wrapped)), 16)


class TestFormValueTranslation(unittest.TestCase):
    def test_booleans_and_numbers_follow_blob_style(self):
        self.assertEqual(cf._form_value("DAS_ConvergenceMonitoring", "true"), "True")
        self.assertEqual(cf._form_value("DAS_AutomaticCVG", "false"), "False")
        self.assertEqual(cf._form_value("DAS_NumberIterations", "25"), "25")
        self.assertEqual(cf._form_value("DAS_ExpansionCoefficient_Horizontal", "1.1"), "1.1")
        self.assertEqual(cf._form_value("DAS_MesoMicroCouplingMode", "None"), "None")

    def test_collect_overrides_maps_form_fields_to_keys(self):
        args = argparse.Namespace(min_h_res="25", iterations="25",
                                  convergence_monitoring="true",
                                  sets=["NSParameters_nfirst=10"])
        got = cf._collect_overrides(args)
        self.assertEqual(got["DAS_InitialResolution_Horizontal"], "25")
        self.assertEqual(got["DAS_NumberIterations"], "25")
        self.assertEqual(got["DAS_ConvergenceMonitoring"], "True")
        self.assertEqual(got["NSParameters_nfirst"], "10")

    def test_convergence_threshold_lands_on_the_das_key(self):
        # UI 锚点 DASParameters_NumericUpDownWithWarning_ConvergenceThreshold
        # ⇔ 快照键 DAS_ConvergenceThreshold（默认表 95，应用下限键 90）
        args = argparse.Namespace(convergence_threshold="95", sets=[])
        self.assertEqual(cf._collect_overrides(args), {"DAS_ConvergenceThreshold": "95"})

    def test_set_without_equals_is_rejected(self):
        with self.assertRaises(ValueError):
            cf._collect_overrides(argparse.Namespace(sets=["DAS_Stability"]))

    def test_unknown_parameter_key_is_input_error(self):
        res = plan_for(site="test", directions=[22], overrides={"Whatever": "1"})
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)


class TestPlanShape(unittest.TestCase):
    def test_directions_come_from_climatology_and_stability_from_the_form_default(self):
        # 校准：UI 锚点 DASParameters_ComboBoxWithWarning_Stability 实测 “2 - 中性”，
        # dbo.Parameters 默认 DAS_Stability=2，DEMO 金样也用 2；
        # 而站点气象分桶是 -1（未按稳定度分类），两者不是一套编号 ⇒ 默认取表单默认。
        captured = {}

        def record(pairs_payload, work_dir, timeout=120):
            captured["pairs"] = pairs_payload
            return blob_result()

        with offline(build=record):
            res = cf.build_plan(cf.CfdSpec(site="test"), COPY_DB)
        self.assertTrue(res.ok, res.message)
        plan = res.data["plan"]
        self.assertEqual(plan["directions"], GOLDEN_DIRECTIONS)
        self.assertEqual(plan["stability"], 2)
        self.assertEqual(plan["row_count"], 16)
        self.assertEqual(plan["template_source"], "template")
        self.assertEqual(plan["blob_sha256"], FAKE_SHA)
        self.assertIn("风频矩阵", " ".join(plan["notes"]))
        joined = " ".join(plan["notes"])
        self.assertIn("表单默认", joined)
        self.assertIn("分桶", joined)          # -1 与 2 的差异要显式说出来
        template = captured["pairs"]["template"]
        self.assertEqual(Path(template).name, "template_%s.bin" % TEMPLATE_ID)
        # 目录形状 = <tmp>/mup_cfd_import/<pid>-<uuid>：并发调用不再共用同一个
        # pairs.json / template / 宿主产物（P1-B）
        scratch = Path(template).parent
        self.assertEqual(scratch.parent.name, "mup_cfd_import")
        self.assertTrue(scratch.name.startswith("%d-" % os.getpid()))
        self.assertGreater(len(scratch.name), len("%d-" % os.getpid()))

    def test_explicit_stability_beats_the_form_default(self):
        with offline():
            res = cf.build_plan(cf.CfdSpec(site="test", directions=[22], stability=-1), COPY_DB)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.data["plan"]["stability"], -1)

    def test_one_cfd_row_paired_with_one_computation_row(self):
        res = plan_for(site="test", directions=[22, 247])
        plan = res.data["plan"]
        self.assertEqual(len(plan["cfd_ids"]), 2)
        self.assertEqual(len(plan["computation_ids"]), 2)
        self.assertNotEqual(plan["cfd_ids"], plan["computation_ids"])

    def test_atlas_off_writes_no_atlas_row(self):
        res = plan_for(site="test", directions=[22])
        self.assertEqual(res.data["plan_obj"].atlas_rows, [])
        self.assertNotIn("dbo.CFDAtlas", res.data["plan_obj"].sql)
        self.assertIn(",NULL);", res.data["plan_obj"].sql)   # 不勾 Atlas 时 Micro_AtlasId = NULL

    def test_atlas_needs_explicit_unverified_ack(self):
        # 实测：只写 CFDAtlas 会被 FK_CFDAtlas_CFD 拒；本库也没有 Atlas 金样 ⇒ 默认拒绝
        res = plan_for(site="test", directions=[22], atlas=True, atlas_nb_micro_x=4,
                       atlas_nb_micro_y=5)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)
        self.assertTrue(any("--allow-unverified-atlas" in i.message for i in res.issues))

    def test_atlas_on_writes_the_three_row_group(self):
        res = plan_for(site="test", directions=[22, 247], atlas=True, atlas_nb_micro_x=4,
                       atlas_nb_micro_y=5, allow_unverified_atlas=True)
        self.assertTrue(res.ok, res.message)
        plan = res.data["plan_obj"]
        self.assertEqual(len(plan.atlas_rows), 2)
        sql = plan.sql
        self.assertEqual(sql.count("INSERT INTO dbo.CFDAtlas (Id,NbMicroX,NbMicroY) VALUES"), 2)
        self.assertEqual(sql.count("'DASAtlasDataContainer'"), 2)
        self.assertEqual(sql.count("'DASAtlasObject'"), 2)
        for atlas in plan.atlas_rows:
            # TPT：CFD 父行与 CFDAtlas 子表共用同一个 Id
            self.assertIn("'DASAtlasDataContainer',%d,%d," % (atlas["direction"],
                                                              atlas["stability"]), sql)
            self.assertIn("N'%s',4,5);" % atlas["atlas_cfd_id"], sql)
            # 计算对象的 Micro_AtlasId 指向 Atlas 的 CFD 父行 Id
            self.assertIn(atlas["atlas_cfd_id"] + "');", sql)
        # 删除顺序必须按 FK：ComputationObject → CFDAtlas → CFD
        rb = plan.rollback_sql
        self.assertLess(rb.index("dbo.ComputationObject"), rb.index("dbo.CFDAtlas"))
        self.assertLess(rb.index("dbo.CFDAtlas"), rb.index("dbo.CFD WHERE"))
        for atlas in plan.atlas_rows:
            self.assertIn(atlas["atlas_computation_id"], rb)

    def test_atlas_without_micro_counts_is_input_error(self):
        res = plan_for(site="test", directions=[22], atlas=True, allow_unverified_atlas=True)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)

    def test_form_overrides_reach_the_blob_payload(self):
        captured = {}

        def record(pairs_payload, work_dir, timeout=120):
            captured["pairs"] = pairs_payload
            return blob_result()

        with offline(build=record):
            res = cf.build_plan(cf.CfdSpec(
                site="test", directions=[22], stability=2,
                overrides={"DAS_ExpansionCoefficient_Horizontal": "1.1",
                           "NSParameters_nfirst": "10"}), COPY_DB)
        self.assertTrue(res.ok, res.message)
        pairs = captured["pairs"]
        self.assertIn({"k": "DAS_ExpansionCoefficient_Horizontal", "v": "1.1"},
                      pairs["das_overrides"])
        self.assertIn({"k": "NSParameters_nfirst", "v": "10"}, pairs["ns_overrides"])
        self.assertIn({"k": "DAS_Stability", "v": "2"}, pairs["das_overrides"])
        # 有模板时以模板快照为基：dbo.Parameters 默认值不再整表覆盖，
        # 否则会把 profile 型键（如 DAS_ExpansionCoefficient_Vertical='1.05|1.1|1.1'）压成标量
        self.assertEqual(pairs["das_base"], [])
        self.assertEqual(pairs["ns_base"], [])

    def test_without_template_the_base_comes_from_db_defaults(self):
        captured = {}

        def record(pairs_payload, work_dir, timeout=120):
            captured["pairs"] = pairs_payload
            return blob_result(SOURCE="built-fresh", TPL_KEY_COUNT="0")

        with unittest.mock.patch.object(mw, "_query_rows",
                                        side_effect=db_rows(cfd_rows=[])), \
                unittest.mock.patch.object(cf, "_download_blob",
                                           return_value=mw.ImportResult(
                                               True, mw.ImportCode.OK, "ok",
                                               data={"size": 8682})), \
                unittest.mock.patch.object(cf, "_dotnet_scalar_to_file",
                                           return_value=mw.ImportResult(
                                               True, mw.ImportCode.OK, "ok",
                                               data={"text": PCT_C1831_125M})), \
                unittest.mock.patch.object(cf, "build_blob", side_effect=record):
            res = cf.build_plan(cf.CfdSpec(site="test", directions=[22]), COPY_DB)
        self.assertTrue(res.ok, res.message)
        pairs = captured["pairs"]
        self.assertEqual(pairs["template"], "")
        self.assertIn({"k": "DAS_NumberIterations", "v": "25"}, pairs["das_base"])
        self.assertIn({"k": "NSParameters_nfirst", "v": "10"}, pairs["ns_base"])

    def test_template_only_keys_are_surfaced_as_notes(self):
        res = plan_for(site="test", directions=[22])
        self.assertTrue(any("DAS_ResolutionCoef" in n for n in res.data["plan"]["notes"]),
                        res.data["plan"]["notes"])

    def test_no_template_in_db_falls_back_to_fresh_build(self):
        res = plan_for(site="test", directions=[22], cfd_rows=[])
        self.assertTrue(res.ok, res.message)
        self.assertTrue(any("模板" in n for n in res.data["plan"]["notes"]),
                        res.data["plan"]["notes"])

    def test_duplicate_and_unsorted_directions_are_normalised(self):
        res = plan_for(site="test", directions=[247, 22, 22, 360])
        self.assertEqual(res.data["plan"]["directions"], [22, 247, 360])

    def test_existing_computation_objects_are_noted_not_fatal(self):
        res = plan_for(site="test", directions=[22], existing_rows=[["16"]])
        self.assertTrue(res.ok, res.message)
        self.assertTrue(any("追加" in n for n in res.data["plan"]["notes"]),
                        res.data["plan"]["notes"])

    def test_missing_roughness_is_noted(self):
        res = plan_for(site="test", directions=[22], env_rows=[["MTDSiteOrographyData"]])
        self.assertTrue(res.ok, res.message)
        self.assertTrue(any("粗糙度" in n for n in res.data["plan"]["notes"]),
                        res.data["plan"]["notes"])


class TestInputGuards(unittest.TestCase):
    def test_convergence_threshold_must_be_a_percentage(self):
        for bad in ("150", "0", "abc"):
            res = plan_for(site="test", directions=[22],
                           overrides={"DAS_ConvergenceThreshold": bad})
            self.assertFalse(res.ok, bad)
            self.assertEqual(res.code, mw.ImportCode.INPUT)
        res = plan_for(site="test", directions=[22],
                       overrides={"DAS_ConvergenceThreshold": "95"})
        self.assertTrue(res.ok, res.message)

    def test_integer_snapshot_keys_reject_non_numbers(self):
        # 把关 P1-2：DAS_NumberIterations / DAS_NumberThreads 在 MUP 侧按 int 解释。
        # _form_value 只是格式化器（float() 失败就原样返回文本），所以「abc」必须在
        # validate 就被挡住 —— 否则 GUI 和 CLI 都会把读不出的值写进参数快照。
        for key in cf.INTEGER_SNAPSHOT_KEYS:
            for bad in ("abc", "", "0", "-3", "10.5", "1e3", "007", "  "):
                res = plan_for(site="test", directions=[22], overrides={key: bad})
                self.assertFalse(res.ok, "%s=%r" % (key, bad))
                self.assertEqual(res.code, mw.ImportCode.INPUT)
                self.assertTrue(any(key in i.message for i in res.issues),
                                "%s=%s 的报错必须点名键" % (key, bad))
            for good in ("1", "25", " 100 "):
                res = plan_for(site="test", directions=[22], overrides={key: good})
                self.assertTrue(res.ok, "%s=%s: %s" % (key, good, res.message))

    def test_integer_snapshot_gate_covers_both_channels(self):
        # 同一条门要同时管住表单键与 --set 直通键：两者都落在 spec.overrides 里。
        res = plan_for(site="test", directions=[22],
                       overrides={"DAS_NumberIterations": "abc",
                                  "NSParameters_icycle": "not-a-number"})
        self.assertEqual(res.code, mw.ImportCode.INPUT)
        self.assertTrue(any("DAS_NumberIterations" in i.message for i in res.issues))
        # NSParameters_ 不是本轮设门的键：非数字不该由它报错（只查 DAS 整数键）
        self.assertEqual(len([i for i in res.issues
                              if "需要是正整数" in i.message]), 1)

    def test_empty_direction_list_is_rejected(self):
        # GUI 里「0 个选中」= 不产生计算对象，脚本也不该静默写 0 行
        res = plan_for(site="test", directions=[])
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)
        self.assertTrue(any("风向列表为空" in i.message for i in res.issues))

    def test_out_of_range_direction_is_rejected(self):
        self.assertEqual(plan_for(site="test", directions=[999]).code, mw.ImportCode.INPUT)

    def test_stability_below_minus_one_is_rejected(self):
        self.assertEqual(plan_for(site="test", directions=[22], stability=-2).code,
                         mw.ImportCode.INPUT)

    def test_missing_site_name_is_rejected(self):
        self.assertEqual(plan_for(site="").code, mw.ImportCode.INPUT)

    def test_db_name_must_be_bare_identifier(self):
        self.assertFalse(cf._check_db_name("MUPLocalDb; DROP TABLE x").ok)
        self.assertTrue(cf._check_db_name(COPY_DB).ok)

    def test_unknown_site_is_db_guard(self):
        res = plan_for(site="test", site_rows=[])
        self.assertEqual(res.code, mw.ImportCode.DB_GUARD)

    def test_missing_case_is_precondition_not_db_guard(self):
        # P4：站点下没有 Case 属「缺前置条件」（precondition_missing/7），不是库守卫；
        # 与 mup_mapping_import.py 同口径，编排层按 code 分辨「去补工况」与「换库名重试」。
        res = plan_for(site="test", case_rows=[])
        self.assertEqual(res.code, mw.ImportCode.PRECONDITION)
        self.assertEqual(res.exit_code(), 7)
        self.assertIn("mup_site_import.py", res.message)
        self.assertTrue(any(i.detail.get("missing") == "dbo.Cases" for i in res.issues),
                        res.issues)


class TestPreconditionGates(unittest.TestCase):
    """CFD 前置：地形（环境数据）+ 绘图 Mapping + 测风塔/风机元素。"""

    def test_missing_orography_blocks_and_points_to_terrain_script(self):
        res = plan_for(site="test", directions=[22], env_rows=[])
        # 缺前置条件不是重名冲突（P4）：编排层要能按 code 分辨「去补地形」与「换个名字再来」
        self.assertEqual(res.code, mw.ImportCode.PRECONDITION)
        self.assertEqual(res.exit_code(), 7)
        self.assertIn("mup_terrain_import.py", res.message)

    def test_missing_mapping_blocks_and_points_to_mapping_script(self):
        res = plan_for(site="test", directions=[22], mapping_rows=[["0"]])
        self.assertEqual(res.code, mw.ImportCode.PRECONDITION)
        self.assertIn("mup_mapping_import.py", res.message)

    def test_missing_mast_or_turbine_blocks(self):
        self.assertEqual(plan_for(site="test", directions=[22], element_rows=[["0"]]).code,
                         mw.ImportCode.PRECONDITION)

    def test_allow_flags_downgrade_the_gates(self):
        self.assertFalse(plan_for(site="test", directions=[22], env_rows=[]).ok)
        res = plan_for(site="test", directions=[22], env_rows=[], require_environment=False)
        self.assertTrue(res.ok, res.message)
        res = plan_for(site="test", directions=[22], mapping_rows=[["0"]], require_mapping=False)
        self.assertTrue(res.ok, res.message)
        res = plan_for(site="test", directions=[22], element_rows=[["0"]], require_element=False)
        self.assertTrue(res.ok, res.message)

    def test_no_wind_rose_candidates_asks_for_explicit_directions(self):
        res = plan_for(site="test", clim_rows=[])
        self.assertEqual(res.code, mw.ImportCode.PRECONDITION)
        self.assertIn("--directions", res.message)

    def test_climatology_filter_can_empty_the_candidates(self):
        res = plan_for(site="test", climatology="NOT_THERE")
        self.assertEqual(res.code, mw.ImportCode.PRECONDITION)

    def test_empty_parameters_table_is_db_guard(self):
        res = plan_for(site="test", directions=[22], params={})
        self.assertEqual(res.code, mw.ImportCode.DB_GUARD)


class TestWrittenSqlAndRollback(unittest.TestCase):
    def test_insert_pairs_cfd_and_computation_rows_in_one_transaction(self):
        res = plan_for(site="test", directions=[22, 247], stability=2)
        plan = res.data["plan_obj"]
        sql = plan.sql
        self.assertTrue(sql.startswith("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;"))
        self.assertIn("COMMIT;", sql)
        self.assertEqual(sql.count("INSERT INTO dbo.CFD ("), 2)
        self.assertEqual(sql.count("INSERT INTO dbo.ComputationObject ("), 2)
        self.assertIn("INSERT INTO dbo.CFD (Id,Discriminator,Direction,Stability,"
                      "Parameters_PersistedParameters) VALUES (N'%s',N'%s',22,2,%s);"
                      % (plan.rows[0]["cfd_id"], cf.DISC_CFD_CONTAINER,
                         mw._sql_hex(FAKE_BLOB)), sql)
        self.assertIn("INSERT INTO dbo.ComputationObject (Id,Discriminator,Parent_Id,CFD_Id,"
                      "Micro_AtlasId) VALUES (N'%s',N'%s',N'%s',N'%s',NULL);"
                      % (plan.rows[0]["computation_id"], cf.DISC_COMPUTATION,
                         CASE_ID, plan.rows[0]["cfd_id"]), sql)

    def test_rollback_only_touches_our_own_ids(self):
        res = plan_for(site="test", directions=[22, 247])
        plan = res.data["plan_obj"]
        rb = plan.rollback_sql
        self.assertIn("DELETE FROM dbo.ComputationObject WHERE Id IN", rb)
        self.assertIn("DELETE FROM dbo.CFD WHERE Id IN", rb)
        self.assertNotIn("Parent_Id", rb)
        self.assertNotIn("Direction", rb)
        self.assertNotIn("Sites", rb)
        for row in plan.rows:
            self.assertIn(row["computation_id"], rb)
            self.assertIn(row["cfd_id"], rb)

    def test_atlas_rollback_includes_atlas_table(self):
        res = plan_for(site="test", directions=[22], atlas=True, atlas_nb_micro_x=2,
                       atlas_nb_micro_y=3, allow_unverified_atlas=True)
        rb = res.data["plan_obj"].rollback_sql
        self.assertIn("DELETE FROM dbo.CFDAtlas", rb)
        self.assertEqual(rb.count("dbo.CFD WHERE Id IN"), 1)


class TestWriteGuardAndVerify(unittest.TestCase):
    def test_apply_refuses_production(self):
        res = plan_for(site="test", directions=[22])
        got = cf.apply_plan("MUPLocalDb", res.data["plan_obj"])
        self.assertEqual(got.code, mw.ImportCode.DB_GUARD)
        self.assertTrue(mw.is_prod_db("MUPLocalDb"))

    def test_apply_refuses_non_whitelisted_copy(self):
        res = plan_for(site="test", directions=[22])
        self.assertEqual(cf.apply_plan("MUPLocalDb_somethingelse",
                                       res.data["plan_obj"]).code, mw.ImportCode.DB_GUARD)

    def test_run_refuses_before_planning(self):
        with unittest.mock.patch.object(cf, "build_plan",
                                        side_effect=AssertionError("不应被调用")):
            got = cf.run(cf.CfdSpec(site="test", directions=[22]), "MUPLocalDb", apply=True)
        self.assertEqual(got.code, mw.ImportCode.DB_GUARD)

    def test_verify_accepts_matching_rows(self):
        res = plan_for(site="test", directions=[22, 247])
        plan = res.data["plan_obj"]
        rows = [verify_row(row, plan) for row in plan.rows]
        with unittest.mock.patch.object(mw, "_query_rows", return_value=rows), \
                unittest.mock.patch.object(cf, "_readback_blob_pairs", side_effect=readback_ok):
            got = cf._verify(COPY_DB, plan)
        self.assertTrue(got.ok, got.message)
        self.assertEqual(got.data["blob_len"], len(plan.blob))
        self.assertEqual(got.data["rows"], 2)
        self.assertTrue(any(k.startswith("readback_pairs_") for k in got.data["checks"]),
                        "SHA 无关的内容直证必须进了校验负载")
        self.assertIn("DAS_Stability", got.data["asserted_form_keys"])

    def test_verify_catches_silent_parent_change(self):
        res = plan_for(site="test", directions=[22, 247])
        plan = res.data["plan_obj"]
        rows = [verify_row(row, plan, parent="00000000-0000-4000-8000-000000000000")
                for row in plan.rows]
        with unittest.mock.patch.object(mw, "_query_rows", return_value=rows), \
                unittest.mock.patch.object(cf, "_readback_blob_pairs", side_effect=readback_ok):
            got = cf._verify(COPY_DB, plan)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)

    def test_verify_catches_missing_row(self):
        res = plan_for(site="test", directions=[22, 247])
        plan = res.data["plan_obj"]
        rows = [verify_row(plan.rows[0], plan)]
        with unittest.mock.patch.object(mw, "_query_rows", return_value=rows), \
                unittest.mock.patch.object(cf, "_readback_blob_pairs", side_effect=readback_ok):
            self.assertEqual(cf._verify(COPY_DB, plan).code, mw.ImportCode.VERIFY)

    def test_verify_catches_blob_drift(self):
        res = plan_for(site="test", directions=[22])
        plan = res.data["plan_obj"]
        rows = [verify_row(plan.rows[0], plan, sha="0" * 64)]
        with unittest.mock.patch.object(mw, "_query_rows", return_value=rows), \
                unittest.mock.patch.object(cf, "_readback_blob_pairs", side_effect=readback_ok):
            self.assertEqual(cf._verify(COPY_DB, plan).code, mw.ImportCode.VERIFY)

    def test_verify_catches_wrong_micro_atlas_link(self):
        # P3-G：勾 Atlas 时 ComputationObject.Micro_AtlasId 必须等于配对 Atlas 的 CFD Id
        res = plan_for(site="test", directions=[22], atlas=True, atlas_nb_micro_x=4,
                       atlas_nb_micro_y=5, allow_unverified_atlas=True)
        plan = res.data["plan_obj"]
        rows = [verify_row(plan.rows[0], plan,
                           micro_atlas="00000000-0000-4000-8000-000000000000")]
        atlas_rows = [[plan.atlas_rows[0]["atlas_cfd_id"], cf.DISC_ATLAS_CONTAINER,
                       plan.spec.atlas_nb_micro_x, plan.spec.atlas_nb_micro_y,
                       plan.atlas_rows[0]["atlas_computation_id"], ""]]
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=rows_atlas(rows, atlas_rows)), \
                unittest.mock.patch.object(cf, "_readback_blob_pairs", side_effect=readback_ok):
            got = cf._verify(COPY_DB, plan)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertFalse(got.data["checks"]["atlas_link_" + plan.rows[0]["cfd_id"][:8]])

    def test_verify_accepts_atlas_link_when_correct(self):
        res = plan_for(site="test", directions=[22], atlas=True, atlas_nb_micro_x=4,
                       atlas_nb_micro_y=5, allow_unverified_atlas=True)
        plan = res.data["plan_obj"]
        rows = [verify_row(plan.rows[0], plan,
                           micro_atlas=plan.atlas_rows[0]["atlas_cfd_id"])]
        atlas_rows = [[plan.atlas_rows[0]["atlas_cfd_id"], cf.DISC_ATLAS_CONTAINER,
                       plan.spec.atlas_nb_micro_x, plan.spec.atlas_nb_micro_y,
                       plan.atlas_rows[0]["atlas_computation_id"], ""]]
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=rows_atlas(rows, atlas_rows)), \
                unittest.mock.patch.object(cf, "_readback_blob_pairs", side_effect=readback_ok):
            got = cf._verify(COPY_DB, plan)
        self.assertTrue(got.ok, got.message)

    def test_verify_catches_contaminated_blob_content_without_sha_proof(self):
        # P1-B：库里 blob 的 SHA 与计划一致（并发时两边同时错），但反解出来的表单项是别人的
        res = plan_for(site="test", directions=[22],
                       overrides={"DAS_InitialResolution_Horizontal": "33"})
        plan = res.data["plan_obj"]
        rows = [verify_row(plan.rows[0], plan)]
        with unittest.mock.patch.object(mw, "_query_rows", return_value=rows), \
                unittest.mock.patch.object(cf, "_readback_blob_pairs",
                                           side_effect=readback_pairs_bad):
            got = cf._verify(COPY_DB, plan)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertIn("DAS_InitialResolution_Horizontal",
                      got.data["readback"][plan.rows[0]["cfd_id"]]["bad_keys"])

    def test_verify_fails_when_plan_declares_no_form_pairs(self):
        # 「没有断言可跑」不能等于「通过」：expected_pairs 为空必须显式失败
        res = plan_for(site="test", directions=[22])
        plan = res.data["plan_obj"]
        plan.expected_pairs = {}
        rows = [verify_row(plan.rows[0], plan)]
        with unittest.mock.patch.object(mw, "_query_rows", return_value=rows), \
                unittest.mock.patch.object(cf, "_readback_blob_pairs",
                                           side_effect=AssertionError("不该被调用")):
            got = cf._verify(COPY_DB, plan)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertFalse(got.data["checks"]["expected_pairs_declared"])

    def test_write_failure_is_reported_as_db_write(self):
        res = plan_for(site="test", directions=[22])
        with unittest.mock.patch.object(mw, "_run_sql", return_value=(False, "", "boom")):
            got = cf.apply_plan(COPY_DB, res.data["plan_obj"])
        self.assertEqual(got.code, mw.ImportCode.DB_WRITE)

    def test_blob_host_failure_propagates(self):
        def failing(pairs_payload, work_dir, timeout=120):
            return mw.ImportResult(False, mw.ImportCode.VERIFY, "blob 反解自检有 1 个键值与期望不符")
        with offline(build=failing):
            res = cf.build_plan(cf.CfdSpec(site="test", directions=[22]), COPY_DB)
        self.assertEqual(res.code, mw.ImportCode.VERIFY)


class TestRunArtifactsAndCli(unittest.TestCase):
    def test_dry_run_leaves_plan_sql_and_blob_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with offline():
                got = cf.run(cf.CfdSpec(site="test", directions=[22, 247]), COPY_DB,
                             apply=False, out_dir=out)
            self.assertTrue(got.ok, got.message)
            self.assertIn("dry-run", got.message)
            work = Path(got.data["out_dir"])
            for name in ("plan.json", "apply.sql", "rollback.sql", "cfd_params.bin"):
                self.assertTrue((work / name).is_file(), name)
            self.assertEqual((work / "cfd_params.bin").read_bytes(), FAKE_BLOB)
            # P1-B：产物目录必须是本次调用独占的 <out>/mup_cfd_import/<pid>-<uuid>，
            # 而不是共享的 <out> 本身 —— 否则并发 apply 会互盖 rollback.sql/cfd_params.bin。
            self.assertEqual(work.parent, out / "mup_cfd_import")
            self.assertNotEqual(work, out)
            self.assertEqual(Path(got.data["work_dir"]), work)
            self.assertEqual(Path(got.data["blob"]), work / "cfd_params.bin")
            self.assertEqual(Path(got.data["rollback"]), work / "rollback.sql")

    def test_default_artifact_dir_is_not_shared_between_runs(self):
        """不带 --out-dir 时两个并发 run 的产物路径也必须不同（P1-B 残留面）。"""
        with tempfile.TemporaryDirectory() as tmp:
            seen = []
            with unittest.mock.patch.object(tempfile, "gettempdir", return_value=str(tmp)):
                for _ in range(2):
                    with offline():
                        got = cf.run(cf.CfdSpec(site="test", directions=[22]), COPY_DB,
                                     apply=False, out_dir=None)
                    self.assertTrue(got.ok, got.message)
                    seen.append(Path(got.data["out_dir"]))
            self.assertEqual(len({str(p) for p in seen}), 2, seen)
            self.assertEqual({p.parent.name for p in seen}, {"mup_cfd_import"})
            self.assertEqual({p.parent.parent for p in seen}, {Path(tmp)})

    def test_cli_refuses_to_drop_job_priority(self):
        rc = cf.main(["--site", "test", "--directions", "22", "--db", COPY_DB,
                      "--job-priority", "low"])
        self.assertEqual(rc, 2)

    def test_cli_parses_full_form_into_plan(self):
        argv = ["--site", "test", "--directions", "0,22,45", "--stability", "-1",
                "--min-h-res", "25", "--min-v-res", "4", "--exp-coef-h", "1.1",
                "--iterations", "25", "--threads", "1", "--convergence-monitoring", "true",
                "--auto-cvg", "false", "--force-all-iteration", "false",
                "--meso-micro-coupling", "None", "--set", "NSParameters_nfirst=10",
                "--db", COPY_DB, "--out-dir", tempfile.gettempdir()]
        with offline() as _ctx:
            with unittest.mock.patch("builtins.print"):
                rc = cf.main(argv)
        self.assertEqual(rc, 0)

    def test_cli_rejects_non_integer_iterations_and_threads(self):
        # --iterations/--threads 是「无类型字符串」（把关 P1-2 同源）：整数门落在
        # CfdSpec.validate()，CLI 不必加 type=int 也能拒，且拒在计划期（不写库）。
        for flag, key in (("--iterations", "DAS_NumberIterations"),
                          ("--threads", "DAS_NumberThreads")):
            argv = ["--site", "test", "--directions", "22", "--db", COPY_DB,
                    "--out-dir", tempfile.gettempdir(), flag, "abc"]
            with offline():
                with unittest.mock.patch("builtins.print") as printer:
                    rc = cf.main(argv)
            self.assertEqual(rc, 2, flag)
            printed = "\n".join(str(call) for call in printer.call_args_list)
            self.assertIn(key, printed)
            self.assertIn("需要是正整数", printed)

class TestBlobHost(unittest.TestCase):
    def test_profile_valued_defaults_survive_the_pipe_column_separator(self):
        # dbo.Parameters 的 kl_coefcd/kl_cmu 值本身就带 '|'：
        # 用 '|' 切列的读取通道会把它截成 '1.30'，所以必须走 hex 通道
        value = "1.30|1.05|1.0|1.0|1.0|1.0|1.0|0.95|0.9|0.85"
        rows = [hex_row("NSParameters_kl_coefcd", value), hex_row("DAS_Stability", "2")]
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=params_rows(rows)) as qr:
            got = cf._default_parameters(COPY_DB)
        self.assertTrue(got.ok, got.message)
        params = got.data["params"]
        self.assertEqual(params["NSParameters_kl_coefcd"], value)
        self.assertEqual(params["DAS_Stability"], "2")
        self.assertIn("CONVERT(varchar(max)", qr.call_args[0][1])
        self.assertIn("DATALENGTH(Value)", qr.call_args[0][1])

    def test_default_row_missing_datalength_column_is_not_guessed(self):
        # 旧的两列读数（没有 DATALENGTH）不能继续被接受：那是 P1-A 静默截断的来源
        rows = [["DAS_Stability", "3200"]]
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=params_rows(rows)):
            got = cf._default_parameters(COPY_DB)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)

    def test_default_108_char_profile_is_read_in_full(self):
        # 生产 NSParameters_kl_cmu 真值 = 108 字符 / 10 段；默认通道只读回 64 字符 / 6 段
        value = ("0.02022549|0.01315743|0.01472236|0.01825944|0.02210621|0.02653888|"
                 "0.0298916|0.03274137|0.03031528|0.01277354")
        self.assertEqual(len(value), 108)
        rows = [hex_row("NSParameters_kl_cmu", value)]
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=params_rows(rows)):
            got = cf._default_parameters(COPY_DB)
        self.assertTrue(got.ok, got.message)
        read_back = got.data["params"]["NSParameters_kl_cmu"]
        self.assertEqual(len(read_back), 108)
        self.assertEqual(len(read_back.split("|")), 10)

    def test_truncated_default_value_fails_closed_instead_of_decoding_partial(self):
        # 读通道若又裁剪（hex 短于 2*DATALENGTH），必须整体失败，不能拿残值拼快照
        value = ("0.02022549|0.01315743|0.01472236|0.01825944|0.02210621|0.02653888|"
                 "0.0298916|0.03274137|0.03031528|0.01277354")
        row = hex_row("NSParameters_kl_cmu", value)
        row[2] = row[2][:128]            # 模拟显示宽裁剪：只剩 64 个字符 / 6 段
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=params_rows([row])):
            got = cf._default_parameters(COPY_DB)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertIn("裁剪", got.message)

    def test_odd_hex_length_is_refused(self):
        row = ["DAS_Odd", "6", "30002"]   # 奇数个 hex 字符 ⇒ 不可能是完整字节序列
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=params_rows([row])):
            got = cf._default_parameters(COPY_DB)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)

    def test_non_hex_cell_is_kept_verbatim_after_length_check(self):
        # 长度对得上但不是 hex（例如库里的值是手写的文本）：保留原样，不静默丢键
        row = ["DAS_Odd", "2", "zzzz"]     # 4 个 hex 字符 = 2*DATALENGTH，长度自洽但不是合法 hex
        with unittest.mock.patch.object(mw, "_query_rows", side_effect=params_rows([row])):
            got = cf._default_parameters(COPY_DB)
        self.assertTrue(got.ok, got.message)
        self.assertEqual(got.data["params"]["DAS_Odd"], "zzzz")

    @unittest.skipUnless(shutil.which("powershell"), "本机没有 powershell")
    def test_fresh_blob_roundtrips_as_two_keyvalue_lists(self):
        # 无模板路径：真跑 .NET 宿主，确认产物能反解成 List<KeyValuePair> 且键值与期望一致
        pairs = {"template": "",
                 "das_base": [{"k": "DAS_InitialResolution_Horizontal", "v": "25"},
                              {"k": "DAS_NumberIterations", "v": "25"}],
                 "ns_base": [{"k": "NSParameters_nfirst", "v": "10"}],
                 "das_overrides": [{"k": "DAS_Stability", "v": "-1"}],
                 "ns_overrides": []}
        with tempfile.TemporaryDirectory() as tmp:
            got = cf.build_blob(pairs, Path(tmp))
        self.assertTrue(got.ok, got.message)
        kv = got.data["kv"]
        self.assertEqual(kv["SOURCE"], "built-fresh")
        self.assertEqual(kv["ROUNDTRIP_MISMATCH"], "0")
        self.assertIn("System.Collections.Generic.List", kv["RT_PART1_TYPE"])
        self.assertEqual(int(kv["RT_PART1_COUNT"]), 3)
        self.assertEqual(int(kv["RT_PART2_COUNT"]), 1)
        self.assertEqual(int(kv["PART1_COUNT"]), 3)

    @unittest.skipUnless(shutil.which("powershell"), "本机没有 powershell")
    def test_template_path_reuses_the_same_list_instance(self):
        # 模板路径：不改任何值时应与模板逐字节相同（SAME_AS_TEMPLATE=True）
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            tpl = work / "tpl.bin"
            pairs = {"template": "",
                     "das_base": [{"k": "DAS_NumberIterations", "v": "25"}],
                     "ns_base": [{"k": "NSParameters_nfirst", "v": "10"}],
                     "das_overrides": [], "ns_overrides": []}
            seed = cf.build_blob(pairs, work / "seed")
            self.assertTrue(seed.ok, seed.message)
            tpl.write_bytes(seed.data["blob"])
            got = cf.build_blob({"template": str(tpl), "das_base": [], "ns_base": [],
                                 "das_overrides": [], "ns_overrides": []}, work)
        self.assertTrue(got.ok, got.message)
        kv = got.data["kv"]
        self.assertEqual(kv["SOURCE"], "template")
        self.assertEqual(str(kv["SAME_AS_TEMPLATE"]).lower(), "true")
        self.assertEqual(got.data["sha"], seed.data["sha"])

    @unittest.skipUnless(shutil.which("powershell"), "本机没有 powershell")
    def test_roundtrip_mismatch_is_refused(self):
        # 宿主自检失败时不能返回 ok，否则会把读不回参数的 blob 写进库
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "artifacts").mkdir()
            (work / "artifacts" / "result.txt").write_text(
                "STATUS=OK\nSOURCE=built-fresh\nROUNDTRIP_MISMATCH=2\nRT_PART1_TYPE="
                "System.Collections.Generic.List`1\n", encoding="utf-8")
            fake = unittest.mock.Mock(returncode=0, stderr=b"", stdout=b"")
            with unittest.mock.patch.object(cf.subprocess, "run", return_value=fake):
                got = cf.build_blob({"template": "", "das_base": [], "ns_base": [],
                                     "das_overrides": [], "ns_overrides": []}, work)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)

    @unittest.skipUnless(shutil.which("powershell"), "本机没有 powershell")
    def test_truncated_second_stream_is_refused(self):
        # 类型仍是 List 但条数对不上：第二段流被截断时也不能放行
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "artifacts").mkdir()
            (work / "artifacts" / "result.txt").write_text(
                "STATUS=OK\nSOURCE=built-fresh\nROUNDTRIP_MISMATCH=0\nRT_PART1_TYPE="
                "System.Collections.Generic.List`1\nPART1_COUNT=40\nRT_PART1_COUNT=40\n"
                "PART2_COUNT=77\nRT_PART2_COUNT=12\n", encoding="utf-8")
            fake = unittest.mock.Mock(returncode=0, stderr=b"", stdout=b"")
            with unittest.mock.patch.object(cf.subprocess, "run", return_value=fake):
                got = cf.build_blob({"template": "", "das_base": [], "ns_base": [],
                                     "das_overrides": [], "ns_overrides": []}, work)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)

    @unittest.skipUnless(shutil.which("powershell"), "本机没有 powershell")
    def test_missing_blob_artifact_is_refused(self):
        # result.txt 说 OK 但产物没落盘：不能当作成功
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "artifacts").mkdir()
            (work / "artifacts" / "result.txt").write_text(
                "STATUS=OK\nSOURCE=built-fresh\nROUNDTRIP_MISMATCH=0\nRT_PART1_TYPE="
                "System.Collections.Generic.List`1\nPART1_COUNT=1\nRT_PART1_COUNT=1\n"
                "PART2_COUNT=0\nRT_PART2_COUNT=0\nBLOB_LEN=8\nBLOB_SHA=ZZ\n",
                encoding="utf-8")
            fake = unittest.mock.Mock(returncode=0, stderr=b"", stdout=b"")
            with unittest.mock.patch.object(cf.subprocess, "run", return_value=fake):
                got = cf.build_blob({"template": "", "das_base": [], "ns_base": [],
                                     "das_overrides": [], "ns_overrides": []}, work)
        self.assertFalse(got.ok)
        self.assertEqual(got.code, mw.ImportCode.COMPUTE)


class TestScratchIsolation(unittest.TestCase):
    """P1-B：blob 工作目录每次独占，并发不能互相污染，模板取不回就停。"""

    def test_fresh_work_dir_is_unique_per_call(self):
        a = cf._fresh_work_dir(Path("out"))
        b = cf._fresh_work_dir(Path("out"))
        self.assertNotEqual(a, b)
        self.assertEqual(a.parent.name, "mup_cfd_import")
        self.assertEqual(a.parent.parent, Path("out"))

    def test_concurrent_plans_keep_their_own_requested_values(self):
        # 复刻实机污染场景：两个线程同风向同稳定度、不同表单项，各自必须拿到自己的值
        recorded = []
        lock = threading.Lock()

        def record(pairs_payload, work_dir, timeout=120):
            want = {item["k"]: item["v"] for item in pairs_payload["das_overrides"]}
            blob = json.dumps(want, sort_keys=True).encode("utf-8")
            with lock:
                recorded.append((str(work_dir), dict(want)))
            return mw.ImportResult(True, mw.ImportCode.OK, "ok",
                                   data={"blob": blob,
                                         "sha": hashlib.sha256(blob).hexdigest().upper(),
                                         "kv": {"SOURCE": "template", "PART1_COUNT": "40",
                                                "PART2_COUNT": "77", "RT_PART1_COUNT": "40",
                                                "RT_PART2_COUNT": "77", "ROUNDTRIP_MISMATCH": "0",
                                                "RT_PART1_TYPE": "System.Collections.Generic.List`1"}})

        specs = [cf.CfdSpec(site="test", directions=[88], stability=13,
                            overrides={"DAS_InitialResolution_Horizontal": str(h),
                                       "DAS_InitialResolution_Vertical": str(v)})
                 for h, v in ((11, 2), (33, 4))]
        with tempfile.TemporaryDirectory() as tmp:
            with offline(build=record):
                threads = [threading.Thread(target=cf.build_plan, args=(spec, COPY_DB),
                                            kwargs={"out_dir": Path(tmp)}) for spec in specs]
                for th in threads:
                    th.start()
                for th in threads:
                    th.join()
        self.assertEqual(len(recorded), 2)
        self.assertEqual(len({item[0] for item in recorded}), 2, "两个并发调用共用了 blob 工作目录")
        by_value = {item[1]["DAS_InitialResolution_Horizontal"] for item in recorded}
        self.assertEqual(by_value, {"11", "33"}, "有请求的表单项被别人的 pairs 覆盖了")

    def test_scratch_dir_is_removed_after_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            with offline():
                res = cf.build_plan(cf.CfdSpec(site="test", directions=[22]), COPY_DB,
                                    out_dir=Path(tmp))
            self.assertTrue(res.ok, res.message)
            leftover = list((Path(tmp) / "mup_cfd_import").glob("*")) \
                if (Path(tmp) / "mup_cfd_import").exists() else []
            self.assertEqual(leftover, [], "成功路径要清掉本次的独占工作目录")

    def test_template_download_failure_is_not_downgraded_to_built_fresh(self):
        # P1-B/P3-H：模板取不回来就停，不能静默改用少键的自建快照
        bad = mw.ImportResult(False, mw.ImportCode.COMPUTE, "读取模板 blob 失败: boom")
        with offline():
            with unittest.mock.patch.object(cf, "_download_blob", return_value=bad):
                res = cf.build_plan(cf.CfdSpec(site="test", directions=[22]), COPY_DB)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.COMPUTE)
        self.assertIn("拒绝退化成 built-fresh", res.message)

    def test_explicit_template_that_does_not_exist_is_refused(self):
        res = plan_for(site="test", directions=[22], template_cfd_id="no-such-cfd")
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.INPUT)
        self.assertIn("--template-cfd", res.message)


class TestDuplicateAndCaseGates(unittest.TestCase):
    """P2-D 重复拒 + P2-E 多工况必须显式 --case。"""

    def test_same_direction_and_stability_is_rejected(self):
        res = plan_for(site="test", directions=[22], stability=2, taken_rows=[["22", "2"]])
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.CONFLICT)
        self.assertIn("Direction, Stability", res.message)
        self.assertEqual(res.data["clash"], [[22, 2]])

    def test_same_direction_other_stability_is_allowed(self):
        # (22,7) 已占用不等于 (22,2) 已占用：不同稳定度是不同计算对象，不能误拒
        res = plan_for(site="test", directions=[22], stability=2, taken_rows=[["22", "7"]])
        self.assertTrue(res.ok, res.message)

    def test_other_direction_is_allowed(self):
        res = plan_for(site="test", directions=[45], stability=2, taken_rows=[["22", "2"]])
        self.assertTrue(res.ok, res.message)

    def test_duplicate_inside_one_batch_collapses_to_one_row(self):
        res = plan_for(site="test", directions=[22, 22, 22], stability=2)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.data["plan"]["row_count"], 1)

    def test_multiple_cases_without_case_selector_fails_closed(self):
        rows = [[CASE_ID, "WT Case"], ["11111111-2222-3333-4444-555555555555", "AAlphaSecond"]]
        res = plan_for(site="test", directions=[22], case_rows=rows)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.PRECONDITION)
        self.assertIn("--case", res.message)
        self.assertIn("AAlphaSecond", res.message)

    def test_case_selector_picks_the_named_case(self):
        second = "11111111-2222-3333-4444-555555555555"
        rows = [[CASE_ID, "WT Case"], [second, "AAlphaSecond"]]
        res = plan_for(site="test", case="AAlphaSecond", directions=[22], case_rows=rows)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.data["plan"]["case_id"], second)

    def test_unknown_case_selector_is_input_error(self):
        rows = [[CASE_ID, "WT Case"], ["11111111-2222-3333-4444-555555555555", "AAlphaSecond"]]
        res = plan_for(site="test", case="NOT_THERE", directions=[22], case_rows=rows)
        self.assertEqual(res.code, mw.ImportCode.INPUT)

    def test_duplicate_site_selector_asks_for_id(self):
        res = plan_for(site="test", directions=[22],
                       site_rows=[["1", "test", "1.0", "2.0"], ["2", "test", "3.0", "4.0"]])
        self.assertEqual(res.code, mw.ImportCode.INPUT)
        self.assertIn("Sites.Id", res.message)


class TestBuiltFreshGoldenKeys(unittest.TestCase):
    """P3-H：built-fresh 键集合与金样同为 40+77，备注不再谎称沿用模板值。"""

    def test_base_covers_every_golden_key(self):
        captured = {}

        def record(pairs_payload, work_dir, timeout=120):
            captured["pairs"] = pairs_payload
            return blob_result()
        with offline(build=record, cfd_rows=[]):
            res = cf.build_plan(cf.CfdSpec(site="test", directions=[22]), COPY_DB)
        self.assertTrue(res.ok, res.message)
        self.assertEqual([item["k"] for item in captured["pairs"]["das_base"]],
                         list(cf.GOLDEN_DAS_KEYS))
        self.assertEqual([item["k"] for item in captured["pairs"]["ns_base"]],
                         list(cf.GOLDEN_NS_KEYS))
        values = {item["k"]: item["v"] for item in captured["pairs"]["das_base"]}
        for key, value in cf.GOLDEN_SNAPSHOT_CONSTANTS.items():
            self.assertEqual(values[key], value)

    def test_missing_golden_key_without_constant_is_refused(self):
        # 少键快照不能写进库：宁可停下也不写一个键集合不同构的快照
        def short_rows(db, sql, **_kw):
            if "MAX(DATALENGTH(Value))" in sql:
                return [["216"]]
            if "FROM dbo.Parameters" in sql:
                return [hex_row("DAS_Stability", "2")]
            return db_rows(cfd_rows=[])(db, sql, **_kw)
        with offline(cfd_rows=[]):
            with unittest.mock.patch.object(mw, "_query_rows", side_effect=short_rows):
                res = cf.build_plan(cf.CfdSpec(site="test", directions=[22]), COPY_DB)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, mw.ImportCode.VERIFY)
        self.assertIn("不同构", res.message)

    def test_built_fresh_note_does_not_claim_template_values(self):
        with offline(cfd_rows=[]):
            res = cf.build_plan(cf.CfdSpec(site="test", directions=[22]), COPY_DB)
        joined = " ".join(res.data["plan"]["notes"])
        self.assertIn("没有可参考的 CFD blob 模板", joined)
        self.assertIn("不是本站点/MUP 实产快照", joined)
        self.assertNotIn("沿用模板快照的值", joined)
        self.assertIn("金样常量表", joined)

    def test_plan_records_the_requested_form_pairs(self):
        res = plan_for(site="test", directions=[22], stability=7,
                       overrides={"DAS_InitialResolution_Horizontal": "33"})
        self.assertEqual(res.data["plan"]["expected_pairs"]["DAS_Stability"], "7")
        self.assertEqual(res.data["plan"]["expected_pairs"]["DAS_InitialResolution_Horizontal"],
                         "33")


class TestReadbackParsing(unittest.TestCase):
    """库内 blob 直证的读数折成判定：与 SHA 无关的那条断言（P1-B 自检补位）。"""

    ROWS = [{"id": "aaaa1111-2222-3333-4444-555555555555",
             "expected": {"DAS_Stability": "2", "DAS_InitialResolution_Horizontal": "33"}}]

    def kv(self, **over):
        rid = self.ROWS[0]["id"]
        base = {"STATUS": "OK", "META_" + rid: "8682|3189|40|77",
                "BAD_" + rid: "0", "ABSENT_" + rid: "0"}
        base.update(over)
        return base

    def test_matching_pairs_pass(self):
        got = cf._parse_readback_kv(self.kv(), self.ROWS)
        self.assertTrue(got.ok, got.message)

    def test_wrong_value_fails_with_key_name(self):
        rid = self.ROWS[0]["id"]
        got = cf._parse_readback_kv(
            self.kv(**{"BAD_" + rid: "1",
                       "BADKEYS_" + rid: "DAS_InitialResolution_Horizontal!=11"}), self.ROWS)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertIn("DAS_InitialResolution_Horizontal!=11",
                      got.data["readback"][rid]["bad_keys"])

    def test_absent_expected_key_fails(self):
        rid = self.ROWS[0]["id"]
        got = cf._parse_readback_kv(self.kv(**{"ABSENT_" + rid: "1",
                                               "ABSENTKEYS_" + rid: "DAS_Stability"}), self.ROWS)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)

    def test_missing_row_fails(self):
        self.assertEqual(cf._parse_readback_kv({"STATUS": "OK"}, self.ROWS).code,
                         mw.ImportCode.VERIFY)

    def test_non_list_stream_fails(self):
        rid = self.ROWS[0]["id"]
        self.assertEqual(cf._parse_readback_kv(self.kv(**{"NOTLIST1_" + rid: "Object[]"}),
                                               self.ROWS).code, mw.ImportCode.VERIFY)

    def test_empty_streams_fail(self):
        rid = self.ROWS[0]["id"]
        self.assertEqual(cf._parse_readback_kv(self.kv(**{"META_" + rid: "8682|3189|0|0"}),
                                               self.ROWS).code, mw.ImportCode.VERIFY)

    def test_missing_counters_are_not_guessed_as_zero(self):
        kv = self.kv()
        kv.pop("BAD_" + self.ROWS[0]["id"])
        self.assertEqual(cf._parse_readback_kv(kv, self.ROWS).code, mw.ImportCode.VERIFY)


class TestApplyAutoRollback(unittest.TestCase):
    """P2-C：CFD 写后校验失败要自动执行 rollback.sql 并写进负载（综合/元素已有）。"""

    def test_verify_failure_triggers_rollback(self):
        res = plan_for(site="test", directions=[22])
        plan = res.data["plan_obj"]
        failed = mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致",
                                 data={"checks": {"dir_ae38a3ff": False}})
        calls = []

        def fake_run(db, sql, timeout=120, var_width=None):
            calls.append(sql)
            return (True, "", "")
        with unittest.mock.patch.object(mw, "_run_sql", side_effect=fake_run), \
                unittest.mock.patch.object(cf, "_verify", return_value=failed):
            got = cf.apply_plan(COPY_DB, plan)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertIn("已自动回滚", got.message)
        self.assertTrue(got.data["rollback"]["executed"])
        self.assertTrue(got.data["rollback"]["ok"])
        self.assertEqual(len(calls), 2)
        self.assertIn("DELETE FROM dbo.ComputationObject", calls[1])
        self.assertIn("DELETE FROM dbo.CFD", calls[1])

    def test_rollback_failure_is_reported_as_manual_intervention(self):
        res = plan_for(site="test", directions=[22])
        plan = res.data["plan_obj"]
        failed = mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致", data={})
        with unittest.mock.patch.object(mw, "_run_sql",
                                        side_effect=[(True, "", ""), (False, "", "locked")]), \
                unittest.mock.patch.object(cf, "_verify", return_value=failed):
            got = cf.apply_plan(COPY_DB, plan)
        self.assertEqual(got.code, mw.ImportCode.VERIFY)
        self.assertIn("需人工介入", got.message)
        self.assertFalse(got.data["rollback"]["ok"])
        self.assertEqual(got.data["rollback"]["error"], "locked")

    def test_write_failure_does_not_run_rollback(self):
        # 事务内失败已由 XACT_ABORT 整批回滚，再跑一次 DELETE 只会掩盖真实原因
        res = plan_for(site="test", directions=[22])
        plan = res.data["plan_obj"]
        calls = []

        def fake_run(db, sql, timeout=120, var_width=None):
            calls.append(sql)
            return (False, "", "Msg 2627")
        with unittest.mock.patch.object(mw, "_run_sql", side_effect=fake_run):
            got = cf.apply_plan(COPY_DB, plan)
        self.assertEqual(got.code, mw.ImportCode.DB_WRITE)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("rollback", got.data)

    def test_run_reports_rollback_artifact_and_verify_payload(self):
        res = plan_for(site="test", directions=[22])
        plan = res.data["plan_obj"]
        failed = mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验失败，已自动回滚",
                                 data={"rollback": {"executed": True, "ok": True}})
        with tempfile.TemporaryDirectory() as tmp:
            with offline():
                with unittest.mock.patch.object(cf, "apply_plan", return_value=failed):
                    got = cf.run(cf.CfdSpec(site="test", directions=[22]), COPY_DB, apply=True,
                                 out_dir=Path(tmp))
            self.assertEqual(got.code, mw.ImportCode.VERIFY)
            work = Path(got.data["out_dir"])
            self.assertEqual(got.data["rollback"], str(work / "rollback.sql"))
            self.assertTrue((work / "rollback.sql").is_file())
            self.assertTrue(got.data["verify"]["rollback"]["executed"])
            del plan

    def test_apply_failure_message_keeps_the_verify_reason(self):
        """回滚负载不能把「为什么校验失败」吃掉：编排要靠这句话分岔。"""
        res = plan_for(site="test", directions=[22])
        plan = res.data["plan_obj"]
        failed = mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验不一致：blob_sha",
                                 data={"checks": {"blob_sha": False}})
        with unittest.mock.patch.object(mw, "_run_sql", return_value=(True, "", "")), \
                unittest.mock.patch.object(cf, "_verify", return_value=failed):
            got = cf.apply_plan(COPY_DB, plan)
        self.assertIn("blob_sha", got.message)
        self.assertIn("已自动回滚", got.message)
        self.assertEqual(got.data["verify_message"], failed.message)
        self.assertFalse(got.data["checks"]["blob_sha"])


if __name__ == "__main__":
    unittest.main()
