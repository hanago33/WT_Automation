# encoding: utf-8
"""mup_turbine_import 纯函数测试（不写库、不起 PowerShell 宿主）。

PS 序列化宿主与金样逐字节比对走可选集成测试（WT_TURBINE_PS_IT=1），
数据库写测只在副本库（见 99_synthesis/2026-11-04-turbine-import-api-zcode.md）。
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mup_turbine_import as t


def _write_wtg(path: Path, points="", air_density="1.225", rotor="220",
               description="WT6250D220 (6250 kW)", root="WindTurbineGenerator"):
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<{root} FormatVersion="1.01" Description="{desc}" ManufacturerName="CRRC" '
        'RotorDiameter="{rotor}">\n'
        '  <PerformanceTable AirDensity="{rho}">\n'
        '    <StartStopStrategy HighSpeedCutOut="25.0"/>\n'
        '    <DataTable>\n{points}\n    </DataTable>\n'
        '  </PerformanceTable>\n'
        '</{root}>\n'.format(root=root, desc=description, rotor=rotor, rho=air_density,
                             points=points),
        encoding="utf-8")
    return path


GOLDEN_POINTS = "".join(
    '      <DataPoint WindSpeed="{s}" PowerOutput="{p}" ThrustCoEfficient="{c}"/>\n'.format(
        s=3.0 + 0.5 * i,
        p=[0.0, 42.0, 115.0, 235.0, 410.0, 645.0, 950.0, 1330.0, 1800.0, 2370.0, 3050.0,
           3830.0, 4700.0, 5600.0, 6250.0][i] if i < 15 else 6250.0,
        c=round(0.82 - 0.01 * i, 2))
    for i in range(35))


class TestTurbineImportUnit(unittest.TestCase):
    def test_apply_ok_whitelist(self):
        self.assertTrue(t.apply_ok("MUPLocalDb_mupx_verify"))
        self.assertTrue(t.apply_ok("x_test"))
        self.assertTrue(t.apply_ok("x_sandbox"))
        self.assertFalse(t.apply_ok("MUPLocalDb"))
        self.assertFalse(t.apply_ok("master"))

    def test_turbine_spec_validate(self):
        ok = t.TurbineSpec(name="WT6250D220-Test", version_name="Cp0.429")
        self.assertEqual(ok.validate(), [])
        bad = t.TurbineSpec(name="", version_name="", access_level=9)
        self.assertEqual(len(bad.validate()), 3)

    def test_parse_wtg_golden_sample(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_wtg(Path(tmp) / "a.wtg", points=GOLDEN_POINTS)
            res = t.parse_wtg(p)
            self.assertTrue(res.ok, res.message)
            meta = res.data["wtg_meta"]
            self.assertEqual(meta["rotor_diameter"], 220.0)
            self.assertEqual(meta["air_density"], 1.225)
            self.assertEqual(meta["description"], "WT6250D220 (6250 kW)")
            self.assertEqual(len(res.data["points"]), 35)
            self.assertEqual(res.data["points"][0]["wind_speed"], 3.0)
            self.assertEqual(res.data["points"][-1]["power_kw"], 6250.0)

    def test_parse_wtg_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = t.parse_wtg(Path(tmp) / "missing.wtg")
            self.assertEqual(res.code, t.ImportCode.INPUT)
            bad_root = _write_wtg(Path(tmp) / "bad_root.wtg", root="SomethingElse")
            self.assertEqual(t.parse_wtg(bad_root).code, t.ImportCode.INPUT)
            no_table = ('<WindTurbineGenerator RotorDiameter="1"><Comments/></WindTurbineGenerator>')
            p2 = Path(tmp) / "no_table.wtg"
            p2.write_text(no_table, encoding="utf-8")
            self.assertEqual(t.parse_wtg(p2).code, t.ImportCode.INPUT)
            no_points = _write_wtg(Path(tmp) / "no_points.wtg")
            self.assertEqual(t.parse_wtg(no_points).code, t.ImportCode.INPUT)

    def test_derive_design_matches_il_rules(self):
        """CutIn/CutOut/RatedPower/RatedWindSpeed 对应 RefreshCharacteristics() 的 IL 规则。"""
        pts = [t.CurvePoint(3.0 + 0.5 * i, [0.0, 42.0, 115.0, 235.0, 410.0, 645.0, 950.0,
                                            1330.0, 1800.0, 2370.0, 3050.0, 3830.0, 4700.0,
                                            5600.0, 6250.0][i] if i < 15 else 6250.0, 0.5)
               for i in range(35)]
        d = t.derive_design(pts)
        self.assertEqual((d.cut_in_speed, d.cut_out_speed), (3.0, 20.0))
        self.assertEqual(d.rated_power_mw, 6.25)
        self.assertEqual(d.rated_wind_speed, 10.0)   # 首个达额定点（10.0 与 10.5 同值取小）
        self.assertEqual(d.theoretical_rated_power_kw, 6250.0)
        # .wtg 的 HighSpeedCutOut(25) 不参与：CutOut 只看曲线末点。

    def test_f32_single_domain_equality(self):
        self.assertEqual(t._f32(6250 / 1000.0), t._f32(6.25))
        rated = max(t._f32(p / 1000.0) for p in (6250.0, 6250.0))
        self.assertEqual(t._f32(6250 / 1000.0), rated)

    def test_sql_helpers(self):
        self.assertEqual(t._sql_literal("a'b"), "N'a''b'")
        self.assertEqual(t._sql_literal(None), "NULL")
        self.assertEqual(t._sql_hex(b"\xab\x01"), "0xAB01")
        self.assertEqual(t._sql_float(6.25), "6.25")

    def _plan_with_stub_db(self, tmp):
        """打桩 _query_rows（无同名冲突 + 显式 owner），纯离线生成 12 行计划。"""
        with mock.patch.object(t, "_query_rows", return_value=[]) as m:
            spec = t.TurbineSpec(name="WT6250D220-Test", version_name="Cp0.429",
                                 turbine_model="WT6250D220", owner_id="owner-1")
            parsed = t.parse_wtg(_write_wtg(Path(tmp) / "a.wtg", points=GOLDEN_POINTS))
            self.assertTrue(parsed.ok)
            meta = parsed.data["wtg_meta"]
            wtg = t.WindTurbineFile(
                path=Path(tmp) / "a.wtg", description=meta["description"],
                manufacturer=meta["manufacturer"], rotor_diameter=meta["rotor_diameter"],
                suggested_heights=meta["suggested_heights"], air_density=meta["air_density"],
                points=[t.CurvePoint(**p) for p in parsed.data["points"]])
            derived = t.derive_design(wtg.points)
            planned = t.build_plan(spec, wtg, derived, b"\x01power", b"\x02thrust",
                                   "MUPLocalDb_mupx_verify", owner_id="owner-1")
            self.assertTrue(planned.ok, planned.message)
            return planned.data["plan_obj"], m

    def test_build_plan_12_inserts_in_dependency_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan, q = self._plan_with_stub_db(tmp)
            self.assertEqual(q.call_count, 1)  # 仅同名冲突检查；owner 走显式 ID
            order = [ln.split("INSERT INTO dbo.", 1)[1].split(" ", 1)[0]
                     for ln in plan.sql.splitlines() if "INSERT INTO dbo." in ln]
            self.assertEqual(len(order), 12)
            self.assertEqual(order[-1], "WTTypeVersion")   # 版本行是 WTType 的子行，最后写
            self.assertEqual(order[-2], "WTType")
            self.assertEqual(order[:4], ["WTTypeCurveCollection"] * 4)
            # 推力 TI=NULL、功率 TI=-100 哨兵；blob 以 0x 字面量入库
            self.assertIn(t._sql_hex(b"\x01power"), plan.sql)
            self.assertIn(t._sql_hex(b"\x02thrust"), plan.sql)
            self.assertIn("-100.0", plan.sql)
            self.assertIn("MUPWindTurbineTypeAirDensityTurbulenceIntensityCurve", plan.sql)
            self.assertIn("MTDWindTurbineTypeAirDensityCurveCollection", plan.sql)
            self.assertIn("MTDWindTurbineTypeNoiseCurveCollection", plan.sql)
            # 认证默认（金样 0fd1fbf1）：Norm=3(Iec_4)/Wohler=1/ClassI/Vref50/Iref0.16
            self.assertIn(",3,1,1,1,50.0,0.16,8,0.0,0.2,1.225,10.0,0);", plan.sql)
            # 设计派生（金样 96a1f3b4）：220/3 blades/3.0/20.0/6.25/10.0/6250
            self.assertIn(",220.0,3,1,0.0,0.0,3.0,20.0,6.25,10.0,6250.0);", plan.sql)
            # 回滚为逆序（版本 → 主表 → 曲线 → … → 集合）
            r_order = [ln.split("DELETE FROM dbo.", 1)[1].split(" ", 1)[0]
                       for ln in plan.rollback_sql.splitlines() if "DELETE FROM dbo." in ln]
            self.assertEqual(r_order[0], "WTTypeVersion")
            self.assertEqual(r_order[1], "WTType")
            self.assertEqual(r_order[-1], "WTTypeCurveCollection")
            # 无库级落点仅入档
            self.assertEqual(plan.not_persisted.get("manufacturer"), "CRRC")

    def test_build_plan_rejects_duplicate_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(t, "_query_rows", return_value=[["some-id"]]):
                spec = t.TurbineSpec(name="WT6250D220-Test", version_name="Cp0.429",
                                     owner_id="owner-1")
                parsed = t.parse_wtg(_write_wtg(Path(tmp) / "a.wtg", points=GOLDEN_POINTS))
                wtg = t.WindTurbineFile(
                    path=Path(tmp) / "a.wtg",
                    rotor_diameter=parsed.data["wtg_meta"]["rotor_diameter"],
                    air_density=parsed.data["wtg_meta"]["air_density"],
                    points=[t.CurvePoint(**p) for p in parsed.data["points"]])
                res = t.build_plan(spec, wtg, t.derive_design(wtg.points),
                                   b"p", b"t", "MUPLocalDb_mupx_verify")
                self.assertEqual(res.code, t.ImportCode.CONFLICT)

    def test_run_guards_before_any_write(self):
        spec = t.TurbineSpec(name="x", version_name="Cp1.0")
        with tempfile.TemporaryDirectory() as tmp:
            res = t.run(spec, Path(tmp) / "no.wtg", "MUPLocalDb", apply=True)
            self.assertEqual(res.code, t.ImportCode.DB_GUARD)   # 生产库先拒
            res = t.run(spec, Path(tmp) / "no.wtg", "bad_db", apply=True)
            self.assertEqual(res.code, t.ImportCode.DB_GUARD)   # 白名单外拒绝
            res = t.run(spec, Path(tmp) / "no.wtg", "MUPLocalDb_mupx_verify", apply=False)
            self.assertEqual(res.code, t.ImportCode.INPUT)      # 缺文件再拒

    def test_run_dry_run_end_to_end_with_ps(self):
        """PowerShell 可用时的完整 dry-run（不写库）；CI 无 PS 时跳过。"""
        if not shutil.which("powershell"):
            self.skipTest("powershell unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            wtg = _write_wtg(Path(tmp) / "a.wtg", points=GOLDEN_POINTS)
            spec = t.TurbineSpec(name="WT6250D220-Test", version_name="Cp0.429",
                                 owner_id="owner-1")
            res = t.run(spec, wtg, "MUPLocalDb_mupx_verify", apply=False,
                        out_dir=Path(tmp) / "out")
            self.assertTrue(res.ok, res.message)
            kv = res.data["self_check"]
            self.assertEqual(kv["POWER_RT"], "OK")
            self.assertEqual(kv["THRUST_RT"], "OK")
            self.assertEqual(int(kv["POWER_LEN"]), int(kv["THRUST_LEN"]))
            self.assertTrue((Path(tmp) / "out" / "apply.sql").is_file())
            self.assertTrue((Path(tmp) / "out" / "rollback.sql").is_file())
            self.assertTrue((Path(tmp) / "out" / "plan.json").is_file())

    def test_launcher_wiring_present(self):
        import inspect
        import WT_Launcher
        self.assertTrue(hasattr(WT_Launcher.LauncherApp, "_simple_turbine_data_layer"))
        src = inspect.getsource(WT_Launcher)
        self.assertIn("mup_turbine_import", src)


if __name__ == "__main__":
    unittest.main()
