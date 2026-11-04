# encoding: utf-8
"""mup_weather_import 纯函数测试（不写库、不起 C# 宿主）。"""
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mup_weather_import as w


class TestWeatherImportUnit(unittest.TestCase):
    def test_apply_ok_whitelist(self):
        self.assertTrue(w.apply_ok("MUPLocalDb_verify"))
        self.assertTrue(w.apply_ok("MUPLocalDb_test"))
        self.assertTrue(w.apply_ok("x_sandbox"))
        self.assertFalse(w.apply_ok("MUPLocalDb"))
        self.assertFalse(w.apply_ok("master"))

    def test_mast_spec_validate(self):
        ok = w.MastSpec(name="副本", latitude=46.3, longitude=129.7, elevation=101, height=125)
        self.assertEqual(ok.validate(), [])
        bad = w.MastSpec(name="", latitude=91, longitude=181, elevation=0, height=0, access_level=9)
        codes = [i.code for i in bad.validate()]
        self.assertEqual(len(codes), 5)
        self.assertTrue(all(c == w.ImportCode.INPUT for c in codes))

    def test_parse_stat_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "TI.txt"
            p.write_text("2\n1 " + " ".join(["0.1"] * 16) + "\n2 " + " ".join(["0.2"] * 16) + "\n", encoding="utf-8")
            rows = w._parse_stat_table(p)
            self.assertEqual(len(rows), 32)
            self.assertEqual(rows[0], ("1", "0", "0.1"))
            self.assertEqual(rows[1], ("1", "22.5", "0.1"))
            self.assertEqual(rows[16], ("2", "0", "0.2"))

    def test_sql_literal_escapes_quotes(self):
        self.assertEqual(w._sql_literal("a'b"), "N'a''b'")
        self.assertEqual(w._sql_literal(None), "NULL")

    def test_tim_must_be_internal_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            tim = Path(tmp) / "x.tim"
            tim.write_text("Created by Windographer\n", encoding="utf-8")
            spec = w.MastSpec(name="x", latitude=0, longitude=0, elevation=0, height=10)
            files = w.SourceFiles(tim=tim)
            res = w.run(spec, files, "MUPLocalDb_verify", apply=False,
                        out_dir=Path(tmp) / "out")
            self.assertFalse(res.ok)
            self.assertEqual(res.code, w.ImportCode.INPUT)




class TestWeatherGuards(unittest.TestCase):
    """门禁 / 写后校验（P1-A、P1-B、P2-A、P2-B）——全程不连库、不写 Vault。"""

    def _plan(self, blobs=None, tim_bytes=None, rel=""):
        from datetime import datetime
        spec = w.MastSpec(name="x", latitude=0, longitude=0, elevation=0, height=10)
        return w.WritePlan(
            spec, "c", "h", "s", "f", "t", "tsd", "ts", "fi",
            blobs or w.MatrixBlobs(), None, tim_bytes, "owner",
            datetime(2023, 1, 1), datetime(2023, 1, 2), "", "", rel)

    def test_is_prod_db_case_insensitive(self):
        for name in ("MUPLocalDb", "muplocaldb", " Muplocaldb ", "MUPLOCALDB"):
            self.assertTrue(w.is_prod_db(name), name)
        for name in ("MUPLocalDb_verify", "MUPLocalDb_test", "MUPLocalDb_sandbox", "master"):
            self.assertFalse(w.is_prod_db(name), name)

    def test_resolve_vault_root_never_falls_back_to_prod(self):
        with self.assertRaises(RuntimeError):
            w._resolve_vault_root(None)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(w._resolve_vault_root(tmp), Path(tmp))

    def test_apply_plan_rejects_lowercase_prod_db(self):
        res = w.apply_plan("muplocaldb", self._plan())
        self.assertFalse(res.ok)
        self.assertEqual(res.code, w.ImportCode.DB_GUARD)

    def test_apply_plan_rejects_non_whitelisted_copy_db(self):
        res = w.apply_plan("SomeOtherDb", self._plan())
        self.assertFalse(res.ok)
        self.assertEqual(res.code, w.ImportCode.DB_GUARD)

    def test_danger_write_prod_actually_releases_guard(self):
        ok = w.ImportResult(True, w.ImportCode.OK, "ok")
        with unittest.mock.patch.object(w, "_run_sql", return_value=(True, "", "")), \
                unittest.mock.patch.object(w, "_verify", return_value=ok):
            res = w.apply_plan("muplocaldb", self._plan(), allow_prod=True)
        self.assertTrue(res.ok, res.message)

    def test_danger_write_prod_off_still_rejects(self):
        res = w.apply_plan("MUPLocalDb", self._plan())
        self.assertFalse(res.ok)
        self.assertEqual(res.code, w.ImportCode.DB_GUARD)

    def test_copy_db_cannot_pair_prod_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = w.apply_plan(
                "MUPLocalDb_verify",
                self._plan(w.MatrixBlobs(turb=b"zz"), b"tim", "DATAFILES/X.tim"),
                Path(tmp), prod_vault_root=Path(tmp))
        self.assertFalse(res.ok)
        self.assertEqual(res.code, w.ImportCode.DB_GUARD)
        self.assertIn("生产 vault", res.message)

    def test_apply_without_vault_root_fails_closed_before_compute(self):
        with tempfile.TemporaryDirectory() as tmp:
            tim = Path(tmp) / "x.tim"
            tim.write_bytes(b"VERSION2.0\n*630822816000000000;44$1;92$1;\n")
            spec = w.MastSpec(name="x", latitude=46.3, longitude=129.7, elevation=101, height=125)
            res = w.run(spec, w.SourceFiles(tim=tim), "MUPLocalDb_verify", apply=True,
                        out_dir=Path(tmp) / "out")
        self.assertFalse(res.ok)
        self.assertEqual(res.code, w.ImportCode.DB_GUARD)
        self.assertIn("--vault-root", res.message)

    def test_verify_catches_missing_files_row(self):
        plan = self._plan(w.MatrixBlobs(turb=b"abcd"), b"timdata", "DATAFILES/X.tim")
        row = ["1", "1", "1", "0", "0", "0", "0", "0", "4", "0", "0"]
        with unittest.mock.patch.object(w, "_query_rows", return_value=[row]):
            res = w._verify("MUPLocalDb_verify", plan)
        self.assertFalse(res.ok)
        self.assertEqual(res.code, w.ImportCode.VERIFY)
        self.assertIn("files", res.message)

    def test_verify_catches_missing_stability_row(self):
        plan = self._plan(w.MatrixBlobs(freq=b"12", percentages=b"3"))
        row = ["1", "1", "0", "0", "0", "0", "0", "0", "0", "0", "2"]
        with unittest.mock.patch.object(w, "_query_rows", return_value=[row]):
            res = w._verify("MUPLocalDb_verify", plan)
        self.assertFalse(res.ok)
        self.assertIn("stability", res.message)

    def test_verify_catches_missing_vault_file(self):
        plan = self._plan(w.MatrixBlobs(turb=b"abcd"), b"timdata", "DATAFILES/X.tim")
        row = ["1", "1", "1", "0", "0", "0", "1", "1", "4", "0", "0"]
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(w, "_query_rows", return_value=[row]):
            res = w._verify("MUPLocalDb_verify", plan, Path(tmp))
        self.assertFalse(res.ok)
        self.assertIn("vault 文件缺失", res.message)

    def test_verify_passes_with_vault_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = self._plan(w.MatrixBlobs(turb=b"abcd"), b"timdata", "DATAFILES/X.tim")
            target = Path(tmp) / plan.vault_rel_path
            target.parent.mkdir(parents=True)
            target.write_bytes(b"timdata")
            row = ["1", "1", "1", "0", "0", "0", "1", "1", "4", "0", "0"]
            with unittest.mock.patch.object(w, "_query_rows", return_value=[row]):
                res = w._verify("MUPLocalDb_verify", plan, Path(tmp))
        self.assertTrue(res.ok, res.message)
        self.assertTrue(res.data["vault"]["checked"])


class TestWeatherPlanSql(unittest.TestCase):
    def test_description_empty_writes_empty_string_not_null(self):
        spec = w.MastSpec(name="x", latitude=46.3, longitude=129.7, elevation=101, height=125)
        blobs = w.MatrixBlobs(turb=b"abcd")
        with unittest.mock.patch.object(w, "_query_rows", return_value=[]), \
                unittest.mock.patch.object(w, "_pick_owner", return_value=("owner", "")):
            res = w.build_plan(spec, w.SourceFiles(), blobs, "MUPLocalDb_verify")
        self.assertTrue(res.ok, res.message)
        sql = res.data["plan_obj"].sql
        self.assertIn("N''" + ",", sql)

if __name__ == "__main__":
    unittest.main()
