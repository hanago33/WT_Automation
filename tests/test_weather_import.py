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


class TestVariableWidthReadChannel(unittest.TestCase):
    """P1-A 的归属边界：sqlcmd 的 `-W` 与 `-y` 互斥，读变长列必须换通道。

    这一族断言直接钉住 argv —— 旧实现同时给 `-h -1 -W` 和变长值，sqlcmd 按默认
    256 个显示字符裁剪并在 rc=0 下返回：长值（生产 NSParameters_kl_cmu 108 字符）
    静默变成 64 字符，写后校验还报 [OK]。只在调用方断言「读到几段」抓不住这个回归，
    因为裁剪发生在参数层。
    """

    @staticmethod
    def _capture_run():
        seen = {}

        class FakeProc:
            returncode = 0
            stdout = b""
            stderr = b""

        def fake_run(command, **kwargs):
            seen["command"] = list(command)
            seen["kwargs"] = kwargs
            return FakeProc()

        return fake_run, seen

    def test_default_channel_uses_w_and_has_no_width_flag(self):
        fake_run, seen = self._capture_run()
        with unittest.mock.patch.object(w.subprocess, "run", side_effect=fake_run):
            ok, _out, _err = w._run_sql("MUPLocalDb_verify", "SELECT 1;")
        self.assertTrue(ok)
        self.assertIn("-W", seen["command"])
        self.assertNotIn("-y", seen["command"])

    def test_var_width_channel_uses_y_and_never_w(self):
        # 互斥是根因：带 -y 时再给 -W 会让 -y 失效，值又被裁回 256 字符。
        fake_run, seen = self._capture_run()
        with unittest.mock.patch.object(w.subprocess, "run", side_effect=fake_run):
            w._run_sql("MUPLocalDb_verify", "SELECT 1;", var_width=912)
        self.assertIn("-y", seen["command"])
        self.assertEqual(seen["command"][seen["command"].index("-y") + 1], "912")
        self.assertNotIn("-W", seen["command"])

    def test_read_sized_rows_carries_the_width_to_the_channel(self):
        fake_run, seen = self._capture_run()
        with unittest.mock.patch.object(w.subprocess, "run", side_effect=fake_run):
            w.read_sized_rows("MUPLocalDb_verify", "SELECT 1;", var_width=4008)
        self.assertIn("-y", seen["command"])
        self.assertEqual(seen["command"][seen["command"].index("-y") + 1], "4008")
        self.assertNotIn("-W", seen["command"])

    def test_var_column_width_sizes_the_hex_channel(self):
        # hex 每字节两个显示字符 + 余量；宽度只影响填充，正确性由完整性断言保证。
        with unittest.mock.patch.object(w, "_query_rows", return_value=[["452"]]):
            self.assertEqual(w.var_column_width("MUPLocalDb_verify", "SELECT 1;"), 912)
        with unittest.mock.patch.object(w, "_query_rows", return_value=[["1"]]):
            self.assertEqual(w.var_column_width("MUPLocalDb_verify", "SELECT 1;"),
                             w.VAR_READ_MIN_WIDTH)

    def test_var_column_width_refuses_over_the_sqlcmd_cap(self):
        # 超过 -y 上限时主动抛错（调用方 fail-closed），而不是让 sqlcmd 静默裁剪。
        with unittest.mock.patch.object(w, "_query_rows", return_value=[["4000"]]):
            with self.assertRaises(RuntimeError) as ctx:
                w.var_column_width("MUPLocalDb_verify", "SELECT 1;")
        self.assertIn("上限", str(ctx.exception))

    def test_var_column_width_does_not_guess_a_missing_probe_result(self):
        for rows in ([], [[]], [["  "]], [["abc"]], [["-3"]]):
            with unittest.mock.patch.object(w, "_query_rows", return_value=rows):
                with self.assertRaises(RuntimeError):
                    w.var_column_width("MUPLocalDb_verify", "SELECT 1;")

    def test_hex_length_reason_catches_truncation_before_decoding(self):
        # 生产 dbo.Parameters 的 NSParameters_kl_cmu 真值：108 个字符 / 10 段，
        # nvarchar 的 DATALENGTH = 216 字节 ⇒ hex 通道需要 432 个显示字符；
        # 旧通道按 256 个字符裁剪，解码只剩 6 段，这里必须在解码前抓住。
        value = ("0.02022549|0.01315743|0.01472236|0.01825944|0.02210621|0.02653888|"
                 "0.0298916|0.03274137|0.03031528|0.01277354")
        self.assertEqual(len(value), 108)
        self.assertEqual(len(value.split("|")), 10)
        data_length = len(value.encode("utf-16-le"))
        full = value.encode("utf-16-le").hex().upper()
        self.assertEqual(len(full), 2 * data_length)
        self.assertIsNone(w.hex_length_reason(full, str(data_length)))
        reason = w.hex_length_reason(full[:256], str(data_length))
        self.assertIsNotNone(reason)
        self.assertIn("裁剪", reason)
        self.assertIn("2×DATALENGTH=432", reason)
        # 按残缺值解码只剩 64 个字符 / 6 段——正是 P1-A 报告里的观测值；
        # 断言的作用就是不让这种残值走到解码这一步。
        clipped = bytes.fromhex(full[:256]).decode("utf-16-le")
        self.assertEqual(len(clipped), 64)
        self.assertEqual(len(clipped.split("|")), 6)

    def test_hex_length_reason_shapes(self):
        self.assertIsNone(w.hex_length_reason("", "0"))      # 零长度列
        self.assertIsNone(w.hex_length_reason("zz", "-1"))   # NULL 哨兵
        self.assertIn("奇数", w.hex_length_reason("ABC", "1"))
        self.assertIn("不是整数", w.hex_length_reason("AB", None))
        self.assertIn("负数", w.hex_length_reason("AB", "-7"))
        # 长度自洽但不是 hex（库里手写的文本值）：这里只判完整性，保留原样由调用方决定。
        self.assertIsNone(w.hex_length_reason("zz", "1"))

if __name__ == "__main__":
    unittest.main()
