# encoding: utf-8
"""mup_terrain_import 纯函数测试（不写库、不写 vault、不触 MUP）。

数据库写测只在副本库（MUPLocalDb_*_verify，见
99_synthesis/2026-11-04-terrain-import-api-zcode.md）。
金样 FootprintJSON 四角值取自生产 MUPLocalDb GUI 金样（只读查询，2026-11-04）。
"""
import hashlib
import math
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mup_terrain_import as t

# 金样 FootprintJSON 四角（NW,NE,SE,SW）——生产 MUPLocalDb dbo.GeoData 只读观测
GOLD_CORNERS = [
    (129.57474541486596, 46.518838730807843),
    (129.75183239665461, 46.517811826286447),
    (129.7494390261937, 46.343483097757868),
    (129.57291569321518, 46.344503806109508),
]
GOLD_TIEPOINT = (43544111.17029979, 5153911.16993208)   # 金样 tif ModelTiepoint (0,0)
GOLD_W, GOLD_H, GOLD_SCALE = 906, 1292, 15.0


def _write_tif(path: Path, width=GOLD_W, height=GOLD_H, sx=GOLD_SCALE, sy=GOLD_SCALE,
               tie=GOLD_TIEPOINT, projected_epsg=None, user_defined=True,
               nodata="-32767", bigtiff=False, bad_magic=False, with_geo=True):
    """合成最小 GeoTIFF（float32、单条带、GeoKeys 与金样同形态）。"""
    geokeys = []
    doubles = []
    if with_geo:
        if user_defined:
            geokeys = [
                (1024, 0, 1, 1), (1025, 0, 1, 2), (2048, 0, 1, 4326), (2056, 0, 1, 7030),
                (3072, 0, 1, 32767), (3075, 0, 1, 1), (3076, 0, 1, 9001),
                (3080, 0, 1, 129), (3082, 34736, 1, 0), (3092, 34736, 1, 1),
            ]
            doubles = [43500000.0, 1.0]
        else:
            geokeys = [
                (1024, 0, 1, 1), (2048, 0, 1, 4490), (2056, 0, 1, 1024),
                (3072, 0, 1, projected_epsg or 4531), (3075, 0, 1, 1), (3076, 0, 1, 9001),
                (3080, 0, 1, 129), (3082, 34736, 1, 0), (3092, 34736, 1, 1),
            ]
            doubles = [43500000.0, 1.0]

    pixel = b"\x00\x00\x00\x00" * width * height
    entries = []          # (tag, type, count, raw_value_or_None, external_bytes)
    ext = b""

    def add(tag, typ, values):
        nonlocal ext
        if typ == 3:
            raw = struct.pack("<HH", values[0], 0) if len(values) == 1 else None
            data = b"" if raw else struct.pack("<%dH" % len(values), *values)
        elif typ == 4:
            raw = struct.pack("<I", values[0]) if len(values) == 1 else None
            data = b"" if raw else struct.pack("<%dI" % len(values), *values)
        elif typ == 12:
            data = struct.pack("<%dd" % len(values), *values)
            raw = None
        elif typ == 2:
            data = values.encode("ascii") + b"\x00"
            raw = None
        else:
            raise ValueError(typ)
        entries.append([tag, typ, len(values), raw, data])

    add(256, 3, [width])                       # ImageWidth
    add(257, 3, [height])                      # ImageLength
    add(258, 3, [32])                          # BitsPerSample
    add(259, 3, [1])                           # Compression=none
    add(262, 3, [1])                           # Photometric
    add(273, 4, [0])                           # StripOffsets（占位，后填）
    add(277, 3, [1])                           # SamplesPerPixel
    add(278, 3, [height])                      # RowsPerStrip
    add(279, 4, [len(pixel)])                  # StripByteCounts
    add(339, 3, [3])                           # SampleFormat=float
    add(33550, 12, [sx, sy, 1.0])              # ModelPixelScale
    add(33922, 12, [0.0, 0.0, 0.0, tie[0], tie[1], 0.0])  # ModelTiepoint (0,0)→(x,y)
    if with_geo:
        gk = [1, 1, 0, len(geokeys)]
        for kid, loc, cnt, val in geokeys:
            gk += [kid, loc, cnt, val]
        add(34735, 3, gk)                      # GeoKeyDirectory
        if doubles:
            add(34736, 12, doubles)            # GeoDoubleParams
    if nodata:
        add(42113, 2, nodata)                  # GDAL_NODATA

    ifd_offset = 8
    ifd_size = 2 + 12 * len(entries) + 4
    data_offset = ifd_offset + ifd_size
    # 外部值区
    externals = []
    pos = data_offset
    for e in entries:
        if e[3] is None:
            externals.append(pos)
            e[3] = struct.pack("<I", pos)
            pos += len(e[4])
            ext += e[4]
        else:
            externals.append(None)
    strip_offset = pos
    entries[5][3] = struct.pack("<I", strip_offset)   # StripOffsets（第 6 个标签）

    out = bytearray()
    out += b"II"
    out += struct.pack("<H", 43 if bigtiff else (4987 if bad_magic else 42))
    out += struct.pack("<I", ifd_offset)
    out += struct.pack("<H", len(entries))
    for e in entries:
        out += struct.pack("<HHI", e[0], e[1], e[2]) + (e[3] if e[3] is not None else b"\x00" * 4)
    out += struct.pack("<I", 0)
    out += ext
    out += pixel
    path.write_bytes(bytes(out))
    return path


class TestTerrainImportUnit(unittest.TestCase):
    def test_apply_ok_whitelist(self):
        self.assertTrue(t.apply_ok("MUPLocalDb_terrain_verify"))
        self.assertTrue(t.apply_ok("x_test"))
        self.assertTrue(t.apply_ok("x_sandbox"))
        self.assertFalse(t.apply_ok("MUPLocalDb"))
        self.assertFalse(t.apply_ok("master"))
        self.assertFalse(t.apply_ok("MUPLocalDb_terrain_poc"))

    def test_spec_validate(self):
        ok = t.TerrainSpec(terrain_name="TEST1")
        self.assertEqual(ok.validate(), [])
        bad = t.TerrainSpec(terrain_name="", access_level=9)
        self.assertEqual(len(bad.validate()), 2)

    def test_parse_geotiff_gold_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_tif(Path(tmp) / "a.tif")
            res = t.parse_geotiff(p)
            self.assertTrue(res.ok, res.message)
            meta = res.data["meta_obj"]
            self.assertEqual((meta.width, meta.height), (GOLD_W, GOLD_H))
            self.assertEqual(meta.md5, hashlib.md5(p.read_bytes()).digest())
            self.assertEqual((meta.pixel_scale_x, meta.pixel_scale_y), (15.0, 15.0))
            self.assertEqual(meta.nodata, -32767.0)
            self.assertEqual(meta.epsg, 32767)      # 金样 tif 即 user-defined
            self.assertEqual(meta.geographic_epsg, 4326)
            self.assertAlmostEqual(meta.bbox["xmin"], GOLD_TIEPOINT[0], places=6)
            self.assertAlmostEqual(meta.bbox["ymax"], GOLD_TIEPOINT[1], places=6)
            self.assertAlmostEqual(meta.bbox["xmax"], GOLD_TIEPOINT[0] + GOLD_W * 15.0, places=6)
            self.assertAlmostEqual(meta.bbox["ymin"], GOLD_TIEPOINT[1] - GOLD_H * 15.0, places=6)

    def test_parse_geotiff_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = t.parse_geotiff(Path(tmp) / "missing.tif")
            self.assertEqual(res.code, t.ImportCode.INPUT)
            junk = Path(tmp) / "junk.tif"
            junk.write_bytes(b"not a tif at all........")
            self.assertEqual(t.parse_geotiff(junk).code, t.ImportCode.INPUT)
            big = _write_tif(Path(tmp) / "big.tif", bigtiff=True)
            self.assertEqual(t.parse_geotiff(big).code, t.ImportCode.UNSUPPORTED)
            badmagic = _write_tif(Path(tmp) / "badmagic.tif", bad_magic=True)
            self.assertEqual(t.parse_geotiff(badmagic).code, t.ImportCode.INPUT)
            tiny = Path(tmp) / "tiny.tif"   # 无定位标签（无 PixelScale/Tiepoint）
            tiny.write_bytes(b"II" + struct.pack("<H", 42) + struct.pack("<I", 8)
                             + struct.pack("<H", 0) + struct.pack("<I", 0))
            self.assertEqual(t.parse_geotiff(tiny).code, t.ImportCode.INPUT)

    def test_footprint_matches_gold_corners(self):
        """合成金样几何 → 四角须与 GUI 金样 FootprintJSON 逐角一致（<1e-7°）。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_tif(Path(tmp) / "gold_shape.tif")
            res = t.parse_geotiff(p)
            meta = res.data["meta_obj"]
            fp = t.footprint_corners(meta, 4531)
            self.assertTrue(fp.ok, fp.message)
            self.assertEqual(fp.data["param_source"], "geokeys")
            for (lon, lat), (glon, glat) in zip(fp.data["corners"], GOLD_CORNERS):
                self.assertLess(abs(lon - glon), 1e-7, "lon 偏差超限")
                self.assertLess(abs(lat - glat), 1e-7, "lat 偏差超限")

    def test_footprint_json_format(self):
        ring = t.footprint_json(GOLD_CORNERS)
        self.assertEqual(
            ring,
            '{"type":"Polygon","coordinates":[[[129.57474541486596,46.518838730807843],'
            '[129.75183239665461,46.517811826286447],[129.7494390261937,46.343483097757868],'
            '[129.57291569321518,46.344503806109508],[129.57474541486596,46.518838730807843]]]}')

    def test_tm_roundtrip_closure(self):
        p = t.SUPPORTED_TM_PARAMS[4531]
        for lat, lon in ((46.4, 129.3), (46.6, 129.7), (45.0, 129.0), (48.0, 129.5)):
            x, y = t.tm_forward(lat, lon, p)
            lat2, lon2 = t.tm_inverse(x, y, p)
            self.assertLess(math.hypot(lat2 - lat, lon2 - lon) * 111_320.0, 1e-3)

    def test_target_epsg_mismatch_refused(self):
        """tif GeoKeys 携带真实 EPSG 且与 --target-epsg 不一致 → INPUT（不触库）。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_tif(Path(tmp) / "fixed.tif", user_defined=False, projected_epsg=4531)
            meta = t.parse_geotiff(p).data["meta_obj"]
            res = t.build_plan(t.TerrainSpec(terrain_name="X"), meta, "MUPLocalDb_terrain_verify",
                               target_epsg=32650)
            self.assertEqual(res.code, t.ImportCode.INPUT)
            self.assertIn("不一致", res.message)

    def test_user_defined_crs_allowed(self):
        """user-defined(32767) + 完整 TM GeoKeys 允许（金样先例），Epsg 取 --target-epsg。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_tif(Path(tmp) / "gold.tif")
            meta = t.parse_geotiff(p).data["meta_obj"]
            self.assertEqual(meta.epsg, 32767)
            fp = t.footprint_corners(meta, 4531)
            self.assertTrue(fp.ok, fp.message)

    def test_footprint_unsupported_epsg(self):
        """无 TM GeoKeys 且 EPSG 不在参数表 → UNSUPPORTED（fail-closed）。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_tif(Path(tmp) / "etrs.tif", with_geo=False)
            meta = t.parse_geotiff(p).data["meta_obj"]
            self.assertIsNone(t.resolve_epsg_params(25832))
            self.assertIsNone(t._params_from_geokeys({}))
            fp = t.footprint_corners(meta, 25832)
            self.assertEqual(fp.code, t.ImportCode.UNSUPPORTED)

    def test_run_prod_guard(self):
        """--db MUPLocalDb --apply 无 --danger-write-prod → DB_GUARD（不触库不触 vault）。"""
        with tempfile.TemporaryDirectory() as tmp:
            p = _write_tif(Path(tmp) / "a.tif", width=4, height=4)
            spec = t.TerrainSpec(terrain_name="A")
            res = t.run(spec, p, "MUPLocalDb", apply=True)
            self.assertEqual(res.code, t.ImportCode.DB_GUARD)
            res2 = t.run(spec, p, "MUPLocalDb_not_whitelisted", apply=True)
            self.assertEqual(res2.code, t.ImportCode.DB_GUARD)

    def test_official_wkt_loader(self):
        """P2 修复：官方 WKT 内嵌为常量（权威回退），本地文件优先且与常量逐字符一致。"""
        self.assertEqual(len(t.OFFICIAL_WKT_4531), 725)
        self.assertIn("PROJCS[", t.OFFICIAL_WKT_4531)
        self.assertIn('AUTHORITY["EPSG", "4531"]', t.OFFICIAL_WKT_4531)
        self.assertEqual(t.load_official_wkt(), t.OFFICIAL_WKT_4531)

    def test_lit_or_null(self):
        """快照回读的文本 'NULL' 必须还原为 NULL 关键字（OGC 流 File_Id 回滚回归）。"""
        self.assertEqual(t._lit_or_null("NULL"), "NULL")
        self.assertEqual(t._lit_or_null("null"), "NULL")
        self.assertEqual(t._lit_or_null(None), "NULL")
        self.assertEqual(t._lit_or_null("abc"), "N'abc'")

    def test_site_mount_empty_projection_no_false_conflict(self):
        """P1 回归：Sites.Projection 为 SQL NULL（sqlcmd 回读文本 'NULL'）的站点
        不得虚假 CONFLICT 强制 --allow-projection-overwrite；回滚必须还原真
        NULL 关键字，而非 N'NULL' 脏字符串。"""
        from unittest import mock

        def fake_query(db, sql):
            s = sql.upper()
            if "FROM DBO.GEODATA WHERE TITLE" in s or "FROM DBO.FILES WHERE PATH" in s:
                return []
            if "MUPUSEROBJECT" in s:
                return [["owner-1"]]
            if "FROM DBO.SITES WHERE" in s:
                return [["site-1", "DEMO", 38560818.0, 3164215.0, 8000.0, "NULL"]]
            if "FROM DBO.ENVIRONMENTDATA WHERE PARENT_ID" in s:
                return [["env-1"]]
            if "MTDSITEENVIRONMENTFILEDATASOURCE" in s:
                return []
            if "MTDSITEENVIRONMENTOGCSTREAMDATASOURCE" in s:
                return []
            if "FROM DBO.CASES WHERE PARENT_ID" in s:
                return []
            raise AssertionError("未预期的查询: " + sql)

        with tempfile.TemporaryDirectory() as tmp:
            p = _write_tif(Path(tmp) / "s.tif", width=4, height=4,
                           tie=(43550906.0, 5144221.0))
            meta = t.parse_geotiff(p).data["meta_obj"]
            with mock.patch.object(t.mw, "_query_rows", side_effect=fake_query):
                res = t.build_plan(t.TerrainSpec(terrain_name="S"), meta,
                                   "MUPLocalDb_terrain_verify", site="DEMO")
        self.assertTrue(res.ok, res.message)                  # 不再虚假 CONFLICT
        rb = res.data["plan_obj"].rollback_sql
        self.assertIn("SET Projection=NULL,", rb)             # 真 NULL 关键字还原
        self.assertNotIn("N'NULL'", rb)

    def test_site_mount_rollback_sql(self):
        """站点挂载回滚脚本：NULL 快照还原 + UPDATE 无杂散括号 + 顺序先 DS 后地形行。"""
        from unittest import mock
        try:
            t.load_official_wkt()
        except Exception as exc:
            self.skipTest(f"官方 WKT 文件不可用: {exc}")

        def fake_query(db, sql):
            s = sql.upper()
            if "FROM DBO.GEODATA WHERE TITLE" in s or "FROM DBO.FILES WHERE PATH" in s:
                return []
            if "MUPUSEROBJECT" in s:
                return [["owner-1"]]
            if "FROM DBO.SITES WHERE" in s:
                return [["site-1", "DEMO", 38560818.0, 3164215.0, 8000.0, "PROJCS[OLD]"]]
            if "FROM DBO.ENVIRONMENTDATA WHERE PARENT_ID" in s:
                return [["env-1"]]
            if "MTDSITEENVIRONMENTFILEDATASOURCE" in s:
                return []
            if "MTDSITEENVIRONMENTOGCSTREAMDATASOURCE" in s:
                return [["ogc-1", "MTDSiteEnvironmentOgcStreamDataSource", "NULL",
                         "stream-1", "env-1"]]
            if "FROM DBO.CASES WHERE PARENT_ID" in s:
                return [["case-1", "WT", 38560818.0, 3164215.0]]
            raise AssertionError("未预期的查询: " + sql)

        with tempfile.TemporaryDirectory() as tmp:
            p = _write_tif(Path(tmp) / "s.tif", width=4, height=4,
                           tie=(43550906.0, 5144221.0))
            meta = t.parse_geotiff(p).data["meta_obj"]
            with mock.patch.object(t.mw, "_query_rows", side_effect=fake_query):
                res = t.build_plan(t.TerrainSpec(terrain_name="S"), meta,
                                   "MUPLocalDb_terrain_verify", site="DEMO",
                                   allow_projection_overwrite=True)
        self.assertTrue(res.ok, res.message)
        plan = res.data["plan_obj"]
        self.assertIsNotNone(plan.site)
        rb = plan.rollback_sql
        self.assertNotIn("N'NULL'", rb)
        for line in rb.splitlines():
            if line.startswith("UPDATE"):
                self.assertFalse(line.rstrip().endswith(");"),
                                 "UPDATE 语句有杂散括号: " + line)
        self.assertIn(",NULL,", rb)                      # OGC 流 File_Id 以 NULL 关键字还原
        ds_delete = rb.index("DELETE FROM dbo.DataSource")
        geo_delete = rb.index("DELETE FROM dbo.GeoData")
        self.assertLess(ds_delete, geo_delete)           # 先站点挂载行后地形行
        self.assertIn("PROJCS[OLD]", rb)                 # 站点投影快照还原

    def test_launcher_wiring_present(self):
        import inspect
        import WT_Launcher
        self.assertTrue(hasattr(WT_Launcher.LauncherApp, "_simple_terrain_data_layer"))
        src = inspect.getsource(WT_Launcher)
        self.assertIn("mup_terrain_import", src)
        self.assertIn('key == "terrain"', src)


if __name__ == "__main__":
    unittest.main()
