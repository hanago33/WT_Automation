# encoding: utf-8
"""地形图导入（模式 B，对标 mup_weather_import / mup_turbine_import）：
GeoTIFF 元数据解析 + WGS84 footprint + GeoData 全局库两行落库（可选站点挂载）。

职责
----
  GeoTIFF（地形/DEM .tif） + 元参数（名称/访问级别/目标 EPSG）
      -> stdlib 解析 TIFF/GeoTIFF 元数据（尺寸/像元/NoData/GeoKeys；不读像元、不依赖 GDAL）
      -> 16 字节 MD5 + FootprintJSON（WGS84 四角，横轴墨卡托逆变换，纯 Python）
      -> 两行落库（Files: MTDFileOrography + GeoData: MTDGeoDataFile）
      -> vault 原样字节复制 DATAFILES\\OROGRAPHY\\<源文件名>
  可选 --site：站点挂载扩展（接口预留）—— DataSource(MTDSiteEnvironmentFileDataSource)
      + Sites.Projection（官方 725 字符 WKT）+ Cases.CenterX/CenterY 联动，快照回滚。

证据锚点（99_synthesis/2026-11-04-terrain-*.md 四轮验证 + 本轮独立复算，均为观测）
----
  · GUI 金样（生产 MUPLocalDb，TEST1_Project_CGCS2000_43_1，2026-11-04 用户导入）：
      Files:   Discriminator='MTDFileOrography'，Path='DATAFILES\\OROGRAPHY\\<名>.tif'，
               RemoteStatus=0，Hash=16 字节原始 MD5，Size，DisplayName=<名>.tif，
               WktProjection=NULL，TimeStampStr/OfflineTimeStampStr/IsFromPostCFD/
               IsEncrypted/CustomLocation_Id/ParentFile_Id 全 NULL。
      GeoData: Discriminator='MTDGeoDataFile'，FileExtension=5（枚举 TIF），Nature=1（Orography），
               Epsg=4531，Longitude/Latitude=0.0（CreateNewGeoDataFor 从不填坐标），
               AccessLevel=2，FootprintJSON=WGS84 四角 GeoJSON，
               OGCStream_Id=NULL，Description=''（GUI 金样为空串），File_Id 指向 Files。
  · 本轮新观测（金样 tif 只读复制出 vault 后逐标签复算）：
      1) 金样 tif 本身就是 user-defined CRS（ProjectedCSTypeGeoKey=32767）+ 完整 TM GeoKeys
         （CM129/FE43500000/k1/WGS84 椭球）+ PCS citation "PCS Name = <目录名>"；
         GUI 仍记 Epsg=4531 ⇒ GeoData.Epsg 来自 GUI 目录/用户选择，不是 GeoKeys 回显。
      2) 金样 FootprintJSON = 栅格矩形整体西移 sx/2、北移 sy/2（MUP 把 ModelTiepoint 当
         (0,0) 像元中心）；四角 xy 已用 MUP 自家投影栈（MTDProjectionConvertion 反射探针）
         逐角确证与金样逐位一致。本模块纯 Python 反算与之残差：
         lon ≤ 2.2e-11°，lat ≤ 4.3e-8°（≈4.8mm，DotSpatial 子午线弧长实现差，footprint 无影响）。
  · 消费端：GeoData+Files 两行冷启动即被「地理信息数据」识别（codebuddy PoC 用户复核通过）；
    站点挂载消费端亦被识别（地形面板无点击列出 TIF）；投影双重卡点根因 =
    WKT 硬编码字符串全等比较（必须用官方 725 字符串）+ dbo.Cases 中心坐标漏更。
  · 站点挂载投影接受与地图定位属 [推断]（官方 WKT + Cases 联动后的预期修复），GUI 待验。

红线
----
  · 默认 dry-run；写库必须 --apply 且 --db 显式；默认拒绝 MUPLocalDb（--danger-write-prod 才放行）。
  · 副本库写库必须配对副本 vault：--vault-root 显式且不得指向生产 vault（文件只进副本/沙盒 vault）。
  · 单事务 + 回滚脚本（站点挂载部分为快照回滚）+ 写后校验（行数 + Hash 二进制等值
    + vault 文件 MD5 + 与金样模板逐字段比对）。
  · 同名拒绝（Files.Path / GeoData.Title / vault 目标文件已存在）；不启动 MUP、不触 HASP/GUI。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import struct
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import mup_weather_import as mw  # 错误代数 / SQL 工具 / vault 白名单（与 mup_site_import 同款复用）

# 错误代数重导出（API 使用方从本模块取，语义与气象/站点接口完全一致）
ImportCode = mw.ImportCode
ImportIssue = mw.ImportIssue
ImportResult = mw.ImportResult

__all__ = [
    "ImportIssue",
    "ImportResult",
    "ImportCode",
    "TerrainSpec",
    "GeoTiffMeta",
    "SiteMount",
    "WritePlan",
    "parse_geotiff",
    "footprint_corners",
    "footprint_json",
    "tm_forward",
    "tm_inverse",
    "resolve_epsg_params",
    "load_official_wkt",
    "build_plan",
    "apply_plan",
    "apply_ok",
    "run",
    "main",
]

DEFAULT_TARGET_EPSG = 4531
# 官方 EPSG:4531 WKT（MUP 内置目录 725 字符排版；MBASiteViewModel 对 dbo.Sites.Projection
# 做硬编码字符串字面全等比较，必须逐字符一致，antigravity CIL 证据）。
# 模块常量为权威回退：load_official_wkt 优先取本地文件（--wkt-file 显式或脚本同目录
# 缺省），无文件时降级用此常量——摆脱对 99_synthesis 临时研究目录的硬编码依赖。
OFFICIAL_WKT_4531 = 'PROJCS["CGCS2000 / 3-degree Gauss-Kruger zone 43", GEOGCS["China Geodetic Coordinate System 2000", DATUM["China 2000", SPHEROID["CGCS2000", 6378137, 298.257222101, AUTHORITY["EPSG", "1024"]], AUTHORITY["EPSG", "1043"]], PRIMEM["Greenwich", 0, AUTHORITY["EPSG", "8901"]], UNIT["degree", 0.0174532925199433], AXIS["Geodetic longitude", EAST], AXIS["Geodetic latitude", NORTH], AUTHORITY["EPSG", "4490"]], UNIT["m", 1], PROJECTION["Transverse_Mercator", AUTHORITY["EPSG", "9807"]], PARAMETER["central_meridian", 129], PARAMETER["latitude_of_origin", 0], PARAMETER["scale_factor", 1], PARAMETER["false_easting", 43500000], PARAMETER["false_northing", 0], AUTHORITY["EPSG", "4531"], AXIS["Easting", EAST], AXIS["Northing", NORTH]]'
DEFAULT_WKT_PATH = Path(__file__).with_name("wkt_4531_official.txt")
OROGRAPHY_REL = "DATAFILES\\OROGRAPHY"
DISC_FILE_OROGRAPHY = "MTDFileOrography"
DISC_GEODATA_FILE = "MTDGeoDataFile"
DISC_DS_FILE = "MTDSiteEnvironmentFileDataSource"
DISC_DS_OGC = "MTDSiteEnvironmentOgcStreamDataSource"
DISC_ENV_OROGRAPHY = "MTDSiteOrographyData"
FILE_EXTENSION_TIF = 5   # MTD.File.Model.ExtensionType.TIF（金样观测）
NATURE_OROGRAPHY = 1     # MTD.File.Model.NatureType.Orography（金样观测）
EPSG_USER_DEFINED = 32767


# --- GeoTIFF 解析（纯 stdlib，仅元数据；参照 terrain_review_antigravity/parse_geotiff.py）---
TIFF_TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8}

TAG_NAMES = {
    256: "ImageWidth", 257: "ImageLength", 258: "BitsPerSample", 259: "Compression",
    262: "PhotometricInterpretation", 273: "StripOffsets", 277: "SamplesPerPixel",
    278: "RowsPerStrip", 279: "StripByteCounts", 284: "PlanarConfiguration",
    322: "TileWidth", 323: "TileLength", 324: "TileOffsets", 325: "TileByteCounts",
    330: "SubIFDs", 339: "SampleFormat", 33550: "ModelPixelScale", 33922: "ModelTiepoint",
    34264: "ModelTransformation", 34735: "GeoKeyDirectory", 34736: "GeoDoubleParams",
    34737: "GeoAsciiParams", 42112: "GDAL_METADATA", 42113: "GDAL_NODATA",
}

GEOKEY_NAMES = {
    1024: "GTModelTypeGeoKey", 1025: "GTRasterTypeGeoKey", 1026: "GTCitationGeoKey",
    2048: "GeographicTypeGeoKey", 2049: "GeogCitationGeoKey", 2056: "GeogEllipsoidGeoKey",
    2057: "GeogSemiMajorAxisGeoKey", 2058: "GeogSemiMinorAxisGeoKey",
    2059: "GeogInvFlatteningGeoKey",
    3072: "ProjectedCSTypeGeoKey", 3073: "PCSCitationGeoKey", 3074: "ProjectionGeoKey",
    3075: "ProjCoordTransGeoKey", 3076: "ProjLinearUnitsGeoKey", 3078: "ProjStdParallel1GeoKey",
    3080: "ProjNatOriginLongGeoKey", 3081: "ProjNatOriginLatGeoKey",
    3082: "ProjFalseEastingGeoKey", 3083: "ProjFalseNorthingGeoKey",
    3092: "ProjScaleAtNatOriginGeoKey", 3093: "ProjScaleAtCenterGeoKey",
}

# GeogEllipsoidGeoKey (2056) 内置映射（金样=7030 WGS84）
ELLIPSOID_GEOKEYS = {7030: "WGS84", 1024: "CGCS2000", 7049: "Xian1980"}
# GeographicTypeGeoKey (2048) 内置映射（金样=4326 WGS84）
GEOGRAPHIC_GEOKEYS = {4326: "WGS84", 4490: "CGCS2000", 4610: "Xian1980"}


class GeoTiffError(ValueError):
    """GeoTIFF 元数据解析失败（格式不支持/结构非法）。"""


@dataclass
class GeoTiffMeta:
    path: Path
    size_bytes: int
    md5: bytes
    width: int
    height: int
    bits_per_sample: Any
    sample_format: Any
    compression: Any
    is_tiled: bool
    tile_width: Any = None
    tile_length: Any = None
    pixel_scale_x: float = 0.0
    pixel_scale_y: float = 0.0
    bbox: dict[str, float] = field(default_factory=dict)
    nodata: Optional[float] = None
    nodata_raw: Optional[str] = None
    geokeys: dict[str, Any] = field(default_factory=dict)
    epsg: Optional[int] = None          # ProjectedCSTypeGeoKey（无则为 None）
    geographic_epsg: Optional[int] = None
    citation: str = ""

    def to_dict(self) -> dict:
        return {
            "path": str(self.path), "size_bytes": self.size_bytes,
            "md5": self.md5.hex().upper(), "width": self.width, "height": self.height,
            "bits_per_sample": self.bits_per_sample, "sample_format": self.sample_format,
            "compression": self.compression, "is_tiled": self.is_tiled,
            "tile_width": self.tile_width, "tile_length": self.tile_length,
            "pixel_scale": [self.pixel_scale_x, self.pixel_scale_y],
            "bbox": dict(self.bbox), "nodata": self.nodata,
            "epsg": self.epsg, "geographic_epsg": self.geographic_epsg,
            "citation": self.citation,
            "geokeys": {k: v for k, v in self.geokeys.items()},
        }


def _tiff_values(data: bytes, endian: str, typ: int, count: int, raw: bytes) -> Any:
    if typ == 2:  # ASCII
        return raw.decode("ascii", errors="replace").rstrip("\x00")
    fmt_by_type = {1: "B", 3: "H", 4: "I", 6: "b", 7: "B", 8: "h", 9: "i",
                   11: "f", 12: "d"}
    if typ in (5, 10):  # RATIONAL / SRATIONAL
        out = []
        step = 8
        for idx in range(count):
            chunk = raw[idx * step:(idx + 1) * step]
            if typ == 5:
                num, den = struct.unpack(endian + "II", chunk)
            else:
                num, den = struct.unpack(endian + "ii", chunk)
            out.append((num, den))
        return out[0] if count == 1 else out
    base = fmt_by_type.get(typ)
    if base is None:
        return raw
    vals = list(struct.unpack(endian + str(count) + base, raw))
    return vals[0] if count == 1 else vals


def parse_geotiff(path: Path) -> mw.ImportResult:
    """解析 GeoTIFF 元数据（IFD0 + GeoKeys），同一次读出 MD5。不读像元、不依赖 GDAL。"""
    path = Path(path)
    if not path.is_file():
        return mw.ImportResult(False, mw.ImportCode.INPUT, f"tif 文件不存在: {path}")
    data = path.read_bytes()
    if len(data) < 8:
        return mw.ImportResult(False, mw.ImportCode.INPUT, f"tif 文件过小（{len(data)} B）")
    md5 = hashlib.md5(data).digest()

    endian = {b"II": "<", b"MM": ">"}.get(data[:2])
    if endian is None:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "不是 TIFF（字节序头非 II/MM）")
    magic = struct.unpack(endian + "H", data[2:4])[0]
    if magic == 43:
        return mw.ImportResult(False, mw.ImportCode.UNSUPPORTED, "BigTIFF 不支持（请另存为普通 TIFF）")
    if magic != 42:
        return mw.ImportResult(False, mw.ImportCode.INPUT, f"不是标准 TIFF（magic={magic}）")

    ifd_offset = struct.unpack(endian + "I", data[4:8])[0]
    try:
        num_entries = struct.unpack(endian + "H", data[ifd_offset:ifd_offset + 2])[0]
        entries: dict[str, dict] = {}
        pos = ifd_offset + 2
        for _ in range(num_entries):
            tag, typ, count = struct.unpack(endian + "HHI", data[pos:pos + 8])
            raw_field = data[pos + 8:pos + 12]
            size = count * TIFF_TYPE_SIZES.get(typ, 1)
            if size <= 4:
                raw = raw_field[:size]
            else:
                val_offset = struct.unpack(endian + "I", raw_field)[0]
                raw = data[val_offset:val_offset + size]
                if len(raw) < size:
                    return mw.ImportResult(False, mw.ImportCode.INPUT,
                                           f"标签 {TAG_NAMES.get(tag, tag)} 值越界（文件截断）")
            entries[TAG_NAMES.get(tag, f"Tag_{tag}")] = {
                "tag": tag, "type": typ, "count": count,
                "value": _tiff_values(data, endian, typ, count, raw),
            }
            pos += 12
    except (struct.error, IndexError) as exc:
        return mw.ImportResult(False, mw.ImportCode.INPUT, f"TIFF IFD 解析失败: {exc}")

    def _entry_int(name: str) -> Any:
        v = entries.get(name, {}).get("value")
        return v

    width, height = _entry_int("ImageWidth"), _entry_int("ImageLength")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return mw.ImportResult(False, mw.ImportCode.INPUT, f"尺寸非法: {width} x {height}")

    scale = entries.get("ModelPixelScale", {}).get("value")
    tiepoint = entries.get("ModelTiepoint", {}).get("value")
    transform = entries.get("ModelTransformation", {}).get("value")
    sx = sy = 0.0
    x00 = y00 = 0.0
    if isinstance(scale, list) and len(scale) >= 2 and isinstance(tiepoint, list) and len(tiepoint) >= 6:
        sx, sy = float(scale[0]), float(scale[1])
        i, j, _k, x, y = (float(v) for v in tiepoint[:5])
        if sx <= 0 or sy <= 0:
            return mw.ImportResult(False, mw.ImportCode.INPUT, f"像元尺寸非法: {sx} x {sy}")
        x00, y00 = x - i * sx, y + j * sy          # 像素 (0,0) 左上角 = (xmin, ymax)
    elif isinstance(transform, list) and len(transform) == 16:
        # 仅接受正北朝上（无旋转/剪切）的仿射：x=m0*i+m3, y=m5*j+m7（m5<0）
        if not (transform[1] == 0 and transform[2] == 0 and transform[4] == 0
                and transform[6] == 0 and transform[5] < 0 < transform[0]):
            return mw.ImportResult(False, mw.ImportCode.UNSUPPORTED,
                                   "ModelTransformation 带旋转/剪切（非正北朝上），不支持")
        sx, sy = float(transform[0]), float(-transform[5])
        x00, y00 = float(transform[3]), float(transform[7])
    else:
        return mw.ImportResult(False, mw.ImportCode.INPUT,
                               "缺少 GeoTIFF 定位标签（ModelPixelScale+ModelTiepoint 或 ModelTransformation）")

    bbox = {
        "xmin": x00, "xmax": x00 + width * sx,
        "ymin": y00 - height * sy, "ymax": y00,
    }

    nodata_raw = entries.get("GDAL_NODATA", {}).get("value")
    nodata: Optional[float] = None
    if isinstance(nodata_raw, str) and nodata_raw.strip():
        try:
            nodata = float(nodata_raw.strip().strip("\x00"))
        except ValueError:
            nodata = None

    geokeys: dict[str, Any] = {}
    epsg: Optional[int] = None
    geographic_epsg: Optional[int] = None
    citation = ""
    gkd = entries.get("GeoKeyDirectory", {}).get("value")
    if isinstance(gkd, list) and len(gkd) >= 4:
        num_keys = gkd[3]
        doubles = entries.get("GeoDoubleParams", {}).get("value", [])
        if isinstance(doubles, (int, float)):
            doubles = [doubles]
        ascii_params = entries.get("GeoAsciiParams", {}).get("value") or ""
        if not isinstance(ascii_params, str):
            ascii_params = ""
        for k in range(num_keys):
            idx = 4 + k * 4
            if idx + 4 > len(gkd):
                break
            kid, loc, count, offset = gkd[idx:idx + 4]
            name = GEOKEY_NAMES.get(kid, f"GeoKey_{kid}")
            if loc == 0:
                val: Any = offset
            elif loc == 34736 and isinstance(doubles, list):
                val = doubles[offset:offset + count]
                val = val[0] if count == 1 and val else val
            elif loc == 34737:
                val = ascii_params[offset:offset + count].rstrip("|").rstrip("\x00")
            else:
                val = f"loc_{loc}@{offset}x{count}"
            geokeys[name] = val
            if kid == 3072 and isinstance(val, int):
                epsg = val
            if kid == 2048 and isinstance(val, int):
                geographic_epsg = val
            if kid in (1026, 3073) and isinstance(val, str):
                citation = citation or val

    meta = GeoTiffMeta(
        path=path, size_bytes=len(data), md5=md5, width=width, height=height,
        bits_per_sample=entries.get("BitsPerSample", {}).get("value"),
        sample_format=entries.get("SampleFormat", {}).get("value"),
        compression=entries.get("Compression", {}).get("value"),
        is_tiled="TileWidth" in entries,
        tile_width=entries.get("TileWidth", {}).get("value"),
        tile_length=entries.get("TileLength", {}).get("value"),
        pixel_scale_x=sx, pixel_scale_y=sy, bbox=bbox, nodata=nodata,
        nodata_raw=nodata_raw if isinstance(nodata_raw, str) else None,
        geokeys=geokeys, epsg=epsg, geographic_epsg=geographic_epsg, citation=citation,
    )
    return mw.ImportResult(True, mw.ImportCode.OK, "GeoTIFF 元数据解析完成",
                           data={"meta_obj": meta, "meta": meta.to_dict()})


# --- 投影参数表（横轴墨卡托）与 WGS84 footprint -------------------------------
_ELLIPSOIDS = {
    "CGCS2000": (6378137.0, 298.257222101),   # EPSG:1024
    "Xian1980": (6378140.0, 298.257),         # EPSG:7049（DEMO 站点 WKT 观测）
    "WGS84": (6378137.0, 298.257223563),
}


def _gk_series(first_epsg: int, first_zone: int, count: int, ellipsoid: str,
               prefixed: bool, degree: int) -> dict[int, tuple]:
    """高斯-克吕格系列：zone n -> CM=degree*n（3°带）或 degree*n-3（6°带），FE=带号前缀+50万。"""
    out = {}
    for i in range(count):
        zone = first_zone + i
        cm = float(degree * zone if degree == 3 else degree * (zone + 1) - 3)
        fe = zone * 1_000_000.0 + 500_000.0 if prefixed else 500_000.0
        out[first_epsg + i] = (ellipsoid, cm, fe, 0.0, 1.0)
    return out


def _utm_series(first_epsg: int, south: bool) -> dict[int, tuple]:
    out = {}
    for i in range(60):
        zone = i + 1
        out[first_epsg + i] = ("WGS84", 6.0 * zone - 183.0, 500_000.0,
                               10_000_000.0 if south else 0.0, 0.9996)
    return out


# 仅收录有依据的族：CGCS2000 GK 系列（EPSG 公开定义），
# Xian 1980 3° 带以 DEMO 站点 WKT 观测（2362=zone38, CM114, FE38500000, 6378140/298.257）为锚，
# UTM 为公开定义。其余 EPSG 一律 UNSUPPORTED（fail-closed），可用 --footprint-json 提供四角。
SUPPORTED_TM_PARAMS: dict[int, tuple] = {
    **_gk_series(4491, 13, 11, "CGCS2000", True, 6),    # 4491..4501 6°带 带号前缀
    **_gk_series(4502, 13, 11, "CGCS2000", False, 6),   # 4502..4512 6°带 CM 命名
    **_gk_series(4513, 25, 21, "CGCS2000", True, 3),    # 4513..4533 3°带 带号前缀（4531=zone43）
    **_gk_series(4534, 25, 21, "CGCS2000", False, 3),   # 4534..4554 3°带 CM 命名
    **_gk_series(2349, 25, 21, "Xian1980", True, 3),    # 2349..2369 3°带（2362=zone38 已锚定）
    **_utm_series(32601, False),                        # WGS84 UTM north
    **_utm_series(32701, True),                         # WGS84 UTM south
}
GEOGRAPHIC_EPSGS = {4326: "WGS84", 4490: "CGCS2000"}  # 地理坐标：四角即经纬度


def resolve_epsg_params(epsg: int) -> Optional[tuple[str, float, float, float, float]]:
    """返回 (椭球, 中央经线, 假东, 假北, 比例因子)；地理坐标系/未知返回 None。"""
    return SUPPORTED_TM_PARAMS.get(int(epsg))


def _params_from_geokeys(geokeys: dict[str, Any]) -> Optional[tuple[str, float, float, float, float]]:
    """从 GeoKeys 重建 user-defined 横轴墨卡托参数（金样正是此形态）。

    需要 ProjCoordTransGeoKey=1（TM）+ ProjNatOriginLong + ProjFalseEasting；
    椭球按 2056/2057+2059/2058/2048 解析；缺任一项返回 None（fail-closed）。
    """
    if geokeys.get("ProjCoordTransGeoKey") != 1:
        return None
    cm = geokeys.get("ProjNatOriginLongGeoKey")
    fe = geokeys.get("ProjFalseEastingGeoKey")
    if not isinstance(cm, (int, float)) or not isinstance(fe, (int, float)):
        return None
    fn = geokeys.get("ProjFalseNorthingGeoKey", 0.0)
    k0 = geokeys.get("ProjScaleAtNatOriginGeoKey", 1.0)
    if not isinstance(fn, (int, float)) or not isinstance(k0, (int, float)):
        return None
    ell = None
    code = geokeys.get("GeogEllipsoidGeoKey")
    if isinstance(code, int) and code in ELLIPSOID_GEOKEYS:
        ell = ELLIPSOID_GEOKEYS[code]
    else:
        a_val = geokeys.get("GeogSemiMajorAxisGeoKey")
        rf_val = geokeys.get("GeogInvFlatteningGeoKey")
        b_val = geokeys.get("GeogSemiMinorAxisGeoKey")
        if isinstance(a_val, (int, float)) and a_val > 0:
            if isinstance(rf_val, (int, float)) and rf_val > 0:
                ell = (float(a_val), float(rf_val))       # 自定义 (a, 1/f)
            elif isinstance(b_val, (int, float)) and 0 < b_val < a_val:
                ell = (float(a_val), float(a_val) / (float(a_val) - float(b_val)))
    if ell is None:
        gcode = geokeys.get("GeographicTypeGeoKey")
        if isinstance(gcode, int) and gcode in GEOGRAPHIC_GEOKEYS:
            ell = GEOGRAPHIC_GEOKEYS[gcode]
    if ell is None:
        return None
    return (ell, float(cm), float(fe), float(fn), float(k0))


def _ellipsoid_constants(spec) -> tuple[float, float, float, float]:
    """spec 为 _ELLIPSOIDS 键名或 (a, 1/f) 元组（GeoKeys 自定义椭球）。"""
    if isinstance(spec, (tuple, list)):
        a, rf = spec
    else:
        a, rf = _ELLIPSOIDS[spec]
    f = 1.0 / rf
    e2 = f * (2.0 - f)
    return a, f, e2, e2 / (1.0 - e2)


def _gauss_legendre(n: int) -> list[tuple[float, float]]:
    """[0,1] 区间 n 点 Gauss-Legendre 节点/权重（Legendre 多项式 Newton 求根）。"""
    nodes = []
    for i in range(1, n + 1):
        x = math.cos(math.pi * (i - 0.25) / (n + 0.5))
        dp = 0.0
        for _ in range(100):
            p0, p1 = 1.0, x
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
            dp = n * (x * p1 - p0) / (x * x - 1.0)
            dx = p1 / dp
            x -= dx
            if abs(dx) < 1e-15:
                break
        nodes.append((x, 2.0 / ((1.0 - x * x) * dp * dp)))
    return nodes


_GL_NODES = _gauss_legendre(32)


def _meridian_arc(phi: float, a: float, e2: float) -> float:
    """M(φ)=∫₀^φ a(1-e²)/(1-e²sin²θ)^{3/2}dθ，Gauss-Legendre 32 点（机器精度；金样对齐用）。"""
    if phi == 0.0:
        return 0.0
    half = phi / 2.0
    total = 0.0
    for xi, wi in _GL_NODES:
        t = half * (xi + 1.0)
        total += wi / (1.0 - e2 * math.sin(t) ** 2) ** 1.5
    return a * (1.0 - e2) * half * total


def tm_forward(lat_deg: float, lon_deg: float, params: tuple) -> tuple[float, float]:
    """横轴墨卡托正算（EPSG Guidance 7-2 近似式，带内精度毫米级）。"""
    ell, cm, fe, fn, k0 = params
    a, _f, e2, ep2 = _ellipsoid_constants(ell)
    phi = math.radians(lat_deg)
    dlam = math.radians(lon_deg - cm)
    sin_p, cos_p, tan_p = math.sin(phi), math.cos(phi), math.tan(phi)
    n = a / math.sqrt(1.0 - e2 * sin_p ** 2)
    t = tan_p ** 2
    c = ep2 * cos_p ** 2
    aa = dlam * cos_p
    m = _meridian_arc(phi, a, e2)
    x = fe + k0 * n * (aa + (1 - t + c) * aa ** 3 / 6.0
                       + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * aa ** 5 / 120.0)
    y = fn + k0 * (m + n * tan_p * (aa ** 2 / 2.0
                  + (5 - t + 9 * c + 4 * c ** 2) * aa ** 4 / 24.0
                  + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * aa ** 6 / 720.0))
    return x, y


def tm_inverse(x: float, y: float, params: tuple) -> tuple[float, float]:
    """横轴墨卡托反算 -> (lat_deg, lon_deg)。"""
    ell, cm, fe, fn, k0 = params
    a, _f, e2, ep2 = _ellipsoid_constants(ell)
    m1 = (y - fn) / k0
    mu1 = m1 / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = mu1 + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu1) \
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu1) \
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu1) \
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu1)
    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1 = ep2 * cos1 ** 2
    t1 = tan1 ** 2
    n1 = a / math.sqrt(1 - e2 * sin1 ** 2)
    r1 = a * (1 - e2) / (1 - e2 * sin1 ** 2) ** 1.5
    d = (x - fe) / (n1 * k0)
    phi = phi1 - (n1 * tan1 / r1) * (d ** 2 / 2.0
          - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24.0
          + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720.0)
    lam = math.radians(cm) + (d - (1 + 2 * t1 + c1) * d ** 3 / 6.0
          + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120.0) / cos1
    return math.degrees(phi), math.degrees(lam)


def footprint_corners(meta: GeoTiffMeta, epsg: Optional[int] = None) -> ImportResult:
    """footprint 四角 WGS84 经纬度，角序与金样一致（NW→NE→SE→SW，闭合环由 footprint_json 补）。

    两个金样观测（2026-11-04 对 GUI 金样 tif/行独立复算）：
    1. MUP 把 ModelTiepoint 当作 (0,0) 像元的**中心** ⇒ footprint = 栅格矩形
       整体西移 sx/2、北移 sy/2（金样四角逐角复算 dlon≤2e-11 / dlat≤4e-8）。
    2. 投影参数优先取 tif 自身 GeoKeys（金样 tif 即 user-defined CRS 32767 + 完整 TM
       GeoKeys + WGS84 椭球）；GeoKeys 不完整时才回退 --target-epsg 的内置参数表。
    地理坐标系（4326/4490）四角即经纬度直通。
    """
    epsg = int(epsg if epsg is not None else (meta.epsg or 0))
    params = _params_from_geokeys(meta.geokeys)
    param_src = "geokeys"
    if params is None:
        params = resolve_epsg_params(epsg)
        param_src = "epsg_table"
    if params is None:
        return mw.ImportResult(
            False, mw.ImportCode.UNSUPPORTED,
            f"EPSG:{epsg} 不在内置投影参数表且 tif GeoKeys 未定义完整 TM 参数，"
            "无法计算 WGS84 footprint；请用 --footprint-json 提供四角 GeoJSON",
            data={"supported": sorted(SUPPORTED_TM_PARAMS) + sorted(GEOGRAPHIC_EPSGS)})
    b = meta.bbox
    hx, hy = meta.pixel_scale_x / 2.0, meta.pixel_scale_y / 2.0
    corners_xy = [(b["xmin"] - hx, b["ymax"] + hy), (b["xmax"] - hx, b["ymax"] + hy),
                  (b["xmax"] - hx, b["ymin"] + hy), (b["xmin"] - hx, b["ymin"] + hy)]
    if epsg in GEOGRAPHIC_EPSGS and param_src == "epsg_table":
        corners = [(x, y) for x, y in corners_xy]  # 已是 (lon, lat)
        tm_params = None
    else:
        corners = []
        for x, y in corners_xy:
            lat, lon = tm_inverse(x, y, params)
            corners.append((lon, lat))
        tm_params = params
    # 自检：正算闭合（毫米级）
    if tm_params is not None:
        for (x0, y0), (lon, lat) in zip(corners_xy, corners):
            xb, yb = tm_forward(lat, lon, tm_params)
            if math.hypot(xb - x0, yb - y0) > 1e-3:
                return mw.ImportResult(False, mw.ImportCode.VERIFY,
                                       f"投影往返闭合超差（>1mm）@({x0},{y0})")
    return mw.ImportResult(True, mw.ImportCode.OK, "footprint 四角计算完成",
                           data={"corners": corners, "ring_order": "NW,NE,SE,SW",
                                 "param_source": param_src})


def _net_double_str(x: float) -> str:
    """.NET Framework double 'R' 排版（金样 FootprintJSON 序列化约定）：
    15 位有效数字，不能精确回读则 17 位。Python repr（最短表示）会丢尾数
    （如 129.75183239665461 → 129.7518323966546），与金样字节不符。"""
    s15 = "%.15g" % x
    try:
        if float(s15) == x:
            return s15
    except (ValueError, OverflowError):
        pass
    return "%.17g" % x


def footprint_json(corners: list[tuple[float, float]]) -> str:
    pts = ",".join("[%s,%s]" % (_net_double_str(lon), _net_double_str(lat))
                   for lon, lat in corners)
    first = "[%s,%s]" % (_net_double_str(corners[0][0]), _net_double_str(corners[0][1]))
    return '{"type":"Polygon","coordinates":[[' + pts + "," + first + "]]}"


def load_official_wkt(path: Optional[Path] = None) -> str:
    """官方 EPSG WKT（MUP 内置目录 725 字符排版；UI 投影选择器做字符串全等比较）。

    优先取本地文件（--wkt-file 显式指定，或脚本同目录缺省路径）；缺省路径无文件时
    降级用内嵌常量 OFFICIAL_WKT_4531。显式指定的文件不存在则 fail-closed 报错。
    """
    if path is None and not DEFAULT_WKT_PATH.is_file():
        return OFFICIAL_WKT_4531
    p = Path(path or DEFAULT_WKT_PATH)
    if not p.is_file():
        raise RuntimeError(f"官方 WKT 文件不存在: {p}")
    text = p.read_text(encoding="utf-8-sig").strip()
    if "PROJCS[" not in text:
        raise RuntimeError(f"WKT 文件内容不合法: {p}")
    return text


# --- 输入模型 ----------------------------------------------------------------
@dataclass
class TerrainSpec:
    terrain_name: str                # GeoData.Title（--terrain-name，默认取 tif 主名）
    access_level: int = 2            # 金样=2（Private）
    owner_id: Optional[str] = None
    description: Optional[str] = None

    def validate(self) -> list[mw.ImportIssue]:
        issues: list[mw.ImportIssue] = []
        if not str(self.terrain_name or "").strip():
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "地形名称不能为空"))
        if int(self.access_level) not in (0, 1, 2):
            issues.append(mw.ImportIssue(mw.ImportCode.INPUT, "访问级别只能是 0/1/2"))
        return issues


@dataclass
class SiteMount:
    """站点挂载扩展（接口预留）：对既有站点写 DataSource + 投影/中心联动。"""
    site_selector: str               # 站点 Name 或 Id
    site_id: str = ""
    site_name: str = ""
    env_id: str = ""                 # MTDSiteOrographyData 行
    ds_id: str = ""                  # 新增 DataSource 行
    projection_wkt: str = ""
    projection_old: Optional[str] = None
    center_x: float = 0.0
    center_y: float = 0.0
    center_old_x: float = 0.0
    center_old_y: float = 0.0
    cases: list[dict[str, Any]] = field(default_factory=list)   # [{id, name, x, y}]
    retired_ogc: list[dict[str, Any]] = field(default_factory=list)  # 让位的 OGC 流 DataSource
    projection_overwrite_allowed: bool = False

    def to_dict(self) -> dict:
        return {
            "site_selector": self.site_selector, "site_id": self.site_id,
            "site_name": self.site_name, "env_id": self.env_id, "ds_id": self.ds_id,
            "projection_len": len(self.projection_wkt or ""),
            "projection_old_len": (len(self.projection_old) if self.projection_old else 0),
            "center": [self.center_x, self.center_y],
            "center_old": [self.center_old_x, self.center_old_y],
            "cases": list(self.cases), "retired_ogc_rows": len(self.retired_ogc),
            "verification_status": "db_verified_gui_pending",
        }


@dataclass
class WritePlan:
    spec: TerrainSpec
    meta: GeoTiffMeta
    file_id: str
    geodata_id: str
    display_name: str
    vault_rel_path: str
    footprint: str
    epsg: int
    owner_id: str
    site: Optional[SiteMount] = None
    sql: str = ""
    rollback_sql: str = ""

    def to_dict(self) -> dict:
        return {
            "file_id": self.file_id, "geodata_id": self.geodata_id,
            "display_name": self.display_name, "vault_rel_path": self.vault_rel_path,
            "epsg": self.epsg, "footprint_len": len(self.footprint),
            "owner_id": self.owner_id, "tif": self.meta.to_dict(),
            "site_mount": self.site.to_dict() if self.site else None,
        }


# --- 计划 --------------------------------------------------------------------
def _lit(value: Optional[str]) -> str:
    return mw._sql_literal(value)


def _lit_or_null(value: Optional[str]) -> str:
    """快照回读值：sqlcmd 把 NULL 渲染为文本 'NULL'，还原时必须回成 NULL 关键字。"""
    if value is None or (isinstance(value, str) and value.strip().upper() == "NULL"):
        return "NULL"
    return _lit(value)


def _sql_float(value: float) -> str:
    return repr(float(value))


def _sql_hex(data: bytes) -> str:
    return "0x" + data.hex().upper()


def _resolve_vault(explicit: Optional[Path]) -> tuple[Path, Optional[Path]]:
    """返回 (写入用 vault 根, 生产 vault 根或 None)。显式参数接受 base 根或 local_* 根。"""
    import mup_paths  # 唯一解析器（env -> ini -> 盘符）
    prod = mup_paths.vault_root(None)
    if explicit:
        root = mup_paths.vault_root(str(explicit))
        if not root:
            raise RuntimeError(f"vault root 不存在: {explicit}")
        return Path(root), Path(prod) if prod else None
    if prod:
        return Path(prod), Path(prod)
    raise RuntimeError("无法定位 MUP Vault；请用 --vault-root 显式指定")


def _same_name_conflicts(db: str, spec: TerrainSpec, display_name: str) -> list[mw.ImportIssue]:
    issues: list[mw.ImportIssue] = []
    rel = f"{OROGRAPHY_REL}\\{display_name}"
    try:
        if mw._query_rows(db, f"SELECT TOP 1 Id FROM dbo.Files WHERE Path={_lit(rel)};"):
            issues.append(mw.ImportIssue(mw.ImportCode.CONFLICT, f"Files.Path 已存在: {rel}"))
        if mw._query_rows(db, f"SELECT TOP 1 Id FROM dbo.GeoData WHERE Title={_lit(spec.terrain_name)};"):
            issues.append(mw.ImportIssue(mw.ImportCode.CONFLICT, f"GeoData.Title 已存在: {spec.terrain_name}"))
    except Exception as exc:
        issues.append(mw.ImportIssue(mw.ImportCode.DB_GUARD, f"无法读取目标库: {exc}"))
    return issues


def _plan_site_mount(db: str, selector: str, meta: GeoTiffMeta, epsg: int,
                     wkt_file: Optional[Path], center_x: Optional[float],
                     center_y: Optional[float],
                     allow_overwrite: bool) -> mw.ImportResult:
    """解析既有站点并产出挂载参数（不写库）。"""
    try:
        rows = mw._query_rows(
            db, "SELECT Id, Name, CenterX, CenterY, Radius, Projection FROM dbo.Sites "
                f"WHERE Name={_lit(selector)} OR Id={_lit(selector)};")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"无法读取站点: {exc}")
    if not rows:
        return mw.ImportResult(False, mw.ImportCode.INPUT, f"站点不存在: {selector}")
    if len(rows) > 1:
        return mw.ImportResult(False, mw.ImportCode.INPUT,
                               f"站点名不唯一（{len(rows)} 行），请用站点 Id")
    site_id, site_name, sx, sy, radius, proj_old = rows[0]

    try:
        env_rows = mw._query_rows(
            db, "SELECT Id FROM dbo.EnvironmentData WHERE Parent_Id=" + _lit(site_id) +
            f" AND Discriminator={_lit(DISC_ENV_OROGRAPHY)};")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"无法读取 EnvironmentData: {exc}")
    if not env_rows:
        return mw.ImportResult(False, mw.ImportCode.INPUT,
                               f"站点 {site_name} 没有地形 EnvironmentData({DISC_ENV_OROGRAPHY}) 行")
    if len(env_rows) > 1:
        return mw.ImportResult(False, mw.ImportCode.INPUT,
                               f"站点 {site_name} 有 {len(env_rows)} 行地形 EnvironmentData，拒绝挂载")
    env_id = env_rows[0][0]

    try:
        ds_rows = mw._query_rows(
            db, "SELECT Id, File_Id FROM dbo.DataSource WHERE EnvironmentData_Id=" +
            _lit(env_id) + f" AND Discriminator={_lit(DISC_DS_FILE)};")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"无法读取 DataSource: {exc}")
    if ds_rows:
        return mw.ImportResult(False, mw.ImportCode.CONFLICT,
                               f"站点 {site_name} 已挂载文件地形 File_Id={ds_rows[0][1]}")
    try:
        ogc_rows = mw._query_rows(
            db, "SELECT Id, Discriminator, File_Id, TopographyStream_Id, EnvironmentData_Id "
                "FROM dbo.DataSource WHERE EnvironmentData_Id=" + _lit(env_id) +
            f" AND Discriminator={_lit(DISC_DS_OGC)};")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"无法读取 DataSource: {exc}")

    if epsg != DEFAULT_TARGET_EPSG and not wkt_file:
        return mw.ImportResult(
            False, mw.ImportCode.UNSUPPORTED,
            f"站点挂载需要官方投影 WKT；当前仅内置 EPSG:{DEFAULT_TARGET_EPSG}，"
            "其余请用 --wkt-file 提供")
    try:
        wkt = load_official_wkt(wkt_file)
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.INPUT, str(exc))

    # sqlcmd 把 SQL NULL 渲染为文本 'NULL'（4 字符）：无投影站点必须视为无投影，
    # 否则误判为已有投影，触发虚假 CONFLICT 强制 --allow-projection-overwrite（P1 修复）。
    proj_old_real = bool(proj_old) and bool(proj_old.strip()) \
        and proj_old.strip().upper() != "NULL"
    if proj_old_real and proj_old != wkt and not allow_overwrite:
        return mw.ImportResult(
            False, mw.ImportCode.CONFLICT,
            f"站点 {site_name} 已有其他投影（{len(proj_old)} 字符）；"
            "覆盖需显式 --allow-projection-overwrite")

    cx = float(center_x) if center_x is not None else (meta.bbox["xmin"] + meta.bbox["xmax"]) / 2.0
    cy = float(center_y) if center_y is not None else (meta.bbox["ymin"] + meta.bbox["ymax"]) / 2.0

    try:
        case_rows = mw._query_rows(
            db, "SELECT Id, Name, CenterX, CenterY FROM dbo.Cases WHERE Parent_Id=" +
            _lit(site_id) + ";")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"无法读取 Cases: {exc}")

    mount = SiteMount(
        site_selector=selector, site_id=site_id, site_name=site_name, env_id=env_id,
        ds_id=str(uuid.uuid4()), projection_wkt=wkt, projection_old=proj_old,
        center_x=cx, center_y=cy, center_old_x=float(sx), center_old_y=float(sy),
        cases=[{"id": r[0], "name": r[1], "x": float(r[2]), "y": float(r[3])} for r in case_rows],
        retired_ogc=[{"id": r[0], "disc": r[1], "file_id": r[2], "stream_id": r[3],
                      "env_id": r[4]} for r in ogc_rows],
        projection_overwrite_allowed=allow_overwrite)
    warnings: list[mw.ImportIssue] = []
    if not case_rows:
        warnings.append(mw.ImportIssue(mw.ImportCode.INPUT,
                                       f"站点 {site_name} 无 Cases 行；Cases 中心联动无行可更"))
    radius = float(radius or 0.0)
    if center_x is None and center_y is None:
        # 默认重定位到 DEM 中心：告警只看半径能否覆盖 DEM 范围
        half_diag = math.hypot(meta.bbox["xmax"] - meta.bbox["xmin"],
                               meta.bbox["ymax"] - meta.bbox["ymin"]) / 2.0
        if radius and half_diag > radius:
            warnings.append(mw.ImportIssue(
                mw.ImportCode.INPUT,
                f"DEM 半对角线 {half_diag:.0f} m 超出站点半径 {radius:.0f} m，"
                "站点范围不能完整覆盖 DEM（数据范围问题，不阻塞写入）"))
    elif radius:
        dist = math.hypot(cx - meta.bbox["xmin"] - (meta.bbox["xmax"] - meta.bbox["xmin"]) / 2.0,
                          cy - meta.bbox["ymin"] - (meta.bbox["ymax"] - meta.bbox["ymin"]) / 2.0)
        if dist > radius:
            warnings.append(mw.ImportIssue(
                mw.ImportCode.INPUT,
                f"显式站点中心距 DEM 中心 {dist:.0f} m，超出站点半径 {radius:.0f} m"
                "（数据覆盖范围问题，不阻塞写入）"))
    return mw.ImportResult(True, mw.ImportCode.OK, "站点挂载参数已解析",
                           data={"mount": mount}, issues=warnings)


def build_plan(spec: TerrainSpec, meta: GeoTiffMeta, db: str, owner_id: Optional[str] = None,
               target_epsg: int = DEFAULT_TARGET_EPSG, footprint_override: Optional[str] = None,
               site: Optional[str] = None, wkt_file: Optional[Path] = None,
               center_x: Optional[float] = None, center_y: Optional[float] = None,
               allow_projection_overwrite: bool = False) -> mw.ImportResult:
    issues = spec.validate()
    if issues:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "输入不合法", issues=issues)

    # 投影一致性：GeoData.Epsg 以 --target-epsg（调用方权威，GUI 同样从目录/用户选择得出）
    # 为准；tif GeoKeys 若携带真实 EPSG 且与之不一致则拒（user-defined 32767 允许——金样正是此形态）。
    file_epsg = meta.epsg
    if file_epsg is not None and file_epsg not in (0, EPSG_USER_DEFINED) \
            and int(target_epsg) != int(file_epsg):
        return mw.ImportResult(
            False, mw.ImportCode.INPUT,
            f"--target-epsg({target_epsg}) 与 tif GeoKeys({file_epsg}) 不一致；"
            "请传与文件一致的投影（user-defined CRS 可任选目录内 EPSG，金样先例 32767+4531）")
    epsg = int(target_epsg)

    if footprint_override:
        try:
            obj = json.loads(Path(footprint_override).read_text(encoding="utf-8-sig")
                             if Path(footprint_override).is_file() else footprint_override)
            ring = obj["coordinates"][0]
            corners = [(float(p[0]), float(p[1])) for p in ring[:-1]] if ring[0] == ring[-1] \
                else [(float(p[0]), float(p[1])) for p in ring]
            if len(corners) != 4:
                raise ValueError("footprint 须为四角多边形")
        except Exception as exc:
            return mw.ImportResult(False, mw.ImportCode.INPUT, f"--footprint-json 解析失败: {exc}")
    else:
        fp = footprint_corners(meta, epsg)
        if not fp.ok:
            return fp
        corners = fp.data["corners"]
    fp_text = footprint_json(corners)

    conflicts = _same_name_conflicts(db, spec, meta.path.name)
    if conflicts:
        return mw.ImportResult(False, conflicts[0].code, "同名/冲突拒绝（fail-closed）",
                               issues=conflicts)

    resolved_owner, owner_err = mw._pick_owner(db, owner_id or spec.owner_id)
    if not resolved_owner:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               "无法确定 OwnerId: " + (owner_err or "unknown"))

    mount_result = None
    if site:
        mount_result = _plan_site_mount(db, site, meta, epsg, wkt_file, center_x,
                                        center_y, allow_projection_overwrite)
        if not mount_result.ok:
            return mount_result

    file_id, geodata_id = str(uuid.uuid4()), str(uuid.uuid4())
    display_name = meta.path.name
    rel = f"{OROGRAPHY_REL}\\{display_name}"
    stmts: list[str] = [
        "INSERT INTO dbo.Files (Id,Path,RemoteStatus,Hash,Size,Discriminator,DisplayName) VALUES ("
        f"{_lit(file_id)},{_lit(rel)},0,{_sql_hex(meta.md5)},{int(meta.size_bytes)},"
        f"{_lit(DISC_FILE_OROGRAPHY)},{_lit(display_name)});",
        "INSERT INTO dbo.GeoData (Id,Title,Description,FileName,FileExtension,Size,Nature,"
        "Longitude,Latitude,FootprintJSON,Epsg,Discriminator,File_Id,OGCStream_Id,"
        "CreatorId,CreationDate,OwnerId,AccessLevel) VALUES ("
        f"{_lit(geodata_id)},{_lit(spec.terrain_name)},{_lit(spec.description or '')},"
        f"{_lit(display_name)},{FILE_EXTENSION_TIF},{int(meta.size_bytes)},{NATURE_OROGRAPHY},"
        f"0.0,0.0,{_lit(fp_text)},{epsg},{_lit(DISC_GEODATA_FILE)},{_lit(file_id)},NULL,"
        f"{_lit(resolved_owner)},SYSDATETIME(),{_lit(resolved_owner)},{int(spec.access_level)});",
    ]
    rollback: list[str] = [
        f"DELETE FROM dbo.GeoData WHERE Id={_lit(geodata_id)};",
        f"DELETE FROM dbo.Files WHERE Id={_lit(file_id)};",
    ]
    mount_obj: Optional[SiteMount] = None
    if mount_result is not None:
        mount: SiteMount = mount_result.data["mount"]
        mount.ds_id = str(uuid.uuid4())
        for r in mount.retired_ogc:
            stmts.append(
                f"DELETE FROM dbo.DataSource WHERE Id={_lit(r['id'])};")
        stmts.append(
            "INSERT INTO dbo.DataSource (Id,Discriminator,File_Id,TopographyStream_Id,"
            "EnvironmentData_Id) VALUES ("
            f"{_lit(mount.ds_id)},{_lit(DISC_DS_FILE)},{_lit(file_id)},NULL,{_lit(mount.env_id)});")
        stmts.append(
            "UPDATE dbo.Sites SET Projection=" + _lit(mount.projection_wkt) +
            f", CenterX={_sql_float(mount.center_x)}, CenterY={_sql_float(mount.center_y)} "
            f"WHERE Id={_lit(mount.site_id)};")
        for case in mount.cases:
            stmts.append(
                f"UPDATE dbo.Cases SET CenterX={_sql_float(mount.center_x)}, "
                f"CenterY={_sql_float(mount.center_y)} WHERE Id={_lit(case['id'])};")
        # 快照回滚：新 DataSource 删除 -> 让位行还原 -> Sites/Cases 还原 -> 地形两行删除
        rollback.insert(0, f"DELETE FROM dbo.DataSource WHERE Id={_lit(mount.ds_id)};")
        for r in reversed(mount.retired_ogc):
            rollback.insert(1,
                "INSERT INTO dbo.DataSource (Id,Discriminator,File_Id,TopographyStream_Id,"
                "EnvironmentData_Id) VALUES ("
                f"{_lit_or_null(r['id'])},{_lit(r['disc'])},{_lit_or_null(r['file_id'])},"
                f"{_lit_or_null(r['stream_id'])},{_lit(r['env_id'])});")
        rollback.insert(1 + len(mount.retired_ogc),
            "UPDATE dbo.Sites SET Projection=" + _lit_or_null(mount.projection_old) +
            f", CenterX={_sql_float(mount.center_old_x)}, "
            f"CenterY={_sql_float(mount.center_old_y)} WHERE Id={_lit(mount.site_id)};")
        for case in reversed(mount.cases):
            rollback.insert(2 + len(mount.retired_ogc),
                f"UPDATE dbo.Cases SET CenterX={_sql_float(case['x'])}, "
                f"CenterY={_sql_float(case['y'])} WHERE Id={_lit(case['id'])};")
        mount_obj = mount

    sql = ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;\n" + "\n".join(stmts)
           + "\nCOMMIT;\n")
    rollback_sql = ("SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;\n"
                    + "\n".join(rollback) + "\nCOMMIT;\n")
    plan = WritePlan(
        spec=spec, meta=meta, file_id=file_id, geodata_id=geodata_id,
        display_name=display_name, vault_rel_path=rel, footprint=fp_text, epsg=epsg,
        owner_id=resolved_owner, site=mount_obj, sql=sql, rollback_sql=rollback_sql)
    result_data = {"plan": plan.to_dict(), "plan_obj": plan, "footprint_corners": corners}
    if mount_result is not None and mount_result.issues:
        result_data["warnings"] = [i.to_dict() for i in mount_result.issues]
    return mw.ImportResult(True, mw.ImportCode.OK, "写入计划已生成", data=result_data)


# --- 写后校验（行数 + Hash 二进制等值 + 金样模板逐字段 + vault 文件 MD5）--------
def _verify(db: str, plan: WritePlan, vault_root: Path) -> mw.ImportResult:
    lit, hx = _lit, _sql_hex
    try:
        rows = mw._query_rows(
            db,
            "SELECT "
            f"(SELECT COUNT(*) FROM dbo.Files WHERE Id={lit(plan.file_id)}),"
            f"(SELECT COUNT(*) FROM dbo.GeoData WHERE Id={lit(plan.geodata_id)}),"
            f"(SELECT COUNT(*) FROM dbo.GeoData WHERE File_Id={lit(plan.file_id)} "
            f"AND Discriminator={lit(DISC_GEODATA_FILE)}),"
            f"ISNULL((SELECT DATALENGTH(Hash) FROM dbo.Files WHERE Id={lit(plan.file_id)}),0),"
            f"(SELECT CASE WHEN Hash={hx(plan.meta.md5)} THEN 1 ELSE 0 END "
            f"FROM dbo.Files WHERE Id={lit(plan.file_id)}),"
            f"ISNULL((SELECT Size FROM dbo.GeoData WHERE Id={lit(plan.geodata_id)}),0),"
            f"(SELECT COUNT(*) FROM dbo.Files WHERE Path={lit(plan.vault_rel_path)});")
        if not rows:
            return mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验无返回")
        expected = [1, 1, 1, 16, 1, int(plan.meta.size_bytes), 1]
        got = [int(v) for v in rows[0]]
        if got != expected:
            return mw.ImportResult(False, mw.ImportCode.VERIFY,
                                   f"写后校验不一致: got={got} expected={expected}")
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, f"写后校验查询失败: {exc}")

    # 金样模板逐字段（Files 22 列的审计列全 NULL + GeoData 关键字段）
    try:
        frow = mw._query_rows(
            db,
            "SELECT Path, RemoteStatus, Size, DisplayName, TimeStampStr, OfflineTimeStampStr, "
            "WktProjection, IsFromPostCFD, Discriminator, CustomLocation_Id, ParentFile_Id, "
            "OPTWTLInput_Id, ClimatologyTimeSeries_Id, LargeParkFile_Id, WindResourceMap_Id, "
            "ProjectDocument_Id, ClimatoMultiMesoAssociation_File_Id, "
            "ClimatoMultiMesoData_File_Id, Category_Id, IsEncrypted "
            f"FROM dbo.Files WHERE Id={lit(plan.file_id)};")
        grow = mw._query_rows(
            db,
            "SELECT Title, FileName, FileExtension, Nature, Longitude, Latitude, Epsg, "
            "Discriminator, File_Id, OGCStream_Id, Description, AccessLevel, FootprintJSON, "
            f"CreatorId, OwnerId FROM dbo.GeoData WHERE Id={lit(plan.geodata_id)};")
        checks = {}
        if frow:
            f = frow[0]
            checks["files_path"] = f[0] == plan.vault_rel_path
            checks["files_remote_status"] = int(f[1]) == 0
            checks["files_size"] = int(f[2]) == int(plan.meta.size_bytes)
            checks["files_display"] = f[3] == plan.display_name
            checks["files_disc"] = f[8] == DISC_FILE_OROGRAPHY
            checks["files_audit_null"] = all(v == "NULL" for v in (f[4], f[5], f[6], f[7],
                                                                    f[9], f[10], f[11], f[12],
                                                                    f[13], f[14], f[15], f[16],
                                                                    f[17], f[18], f[19]))
        if grow:
            g = grow[0]
            checks["geodata_title"] = g[0] == plan.spec.terrain_name
            checks["geodata_filename"] = g[1] == plan.display_name
            checks["geodata_ext_nature"] = int(g[2]) == FILE_EXTENSION_TIF and int(g[3]) == NATURE_OROGRAPHY
            checks["geodata_lonlat_zero"] = float(g[4]) == 0.0 and float(g[5]) == 0.0
            checks["geodata_epsg"] = int(g[6]) == plan.epsg
            checks["geodata_disc_fileid"] = g[7] == DISC_GEODATA_FILE and g[8] == plan.file_id
            checks["geodata_stream_null"] = g[9] == "NULL"
            checks["geodata_access"] = int(g[11]) == int(plan.spec.access_level)
            checks["geodata_footprint"] = g[12] == plan.footprint
            checks["geodata_owner"] = g[13] == plan.owner_id and g[14] == plan.owner_id
        bad = [k for k, v in checks.items() if not v]
        if not frow or not grow or bad:
            return mw.ImportResult(False, mw.ImportCode.VERIFY, "金样模板逐字段校验不一致",
                                   data={"failed": bad, "checks": checks})
    except Exception as exc:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, f"金样模板校验失败: {exc}")

    # vault 文件字节校验
    target = vault_root / plan.vault_rel_path
    if not target.is_file():
        return mw.ImportResult(False, mw.ImportCode.VERIFY, f"vault 文件缺失: {target}")
    digest = hashlib.md5(target.read_bytes()).digest()
    if digest != plan.meta.md5:
        return mw.ImportResult(False, mw.ImportCode.VERIFY, "vault 文件 MD5 与源不一致")

    if plan.site is not None:
        m = plan.site
        try:
            srows = mw._query_rows(
                db, "SELECT CenterX, CenterY, Projection FROM dbo.Sites WHERE Id=" +
                lit(m.site_id) + ";")
            drows = mw._query_rows(
                db, "SELECT COUNT(*) FROM dbo.DataSource WHERE EnvironmentData_Id=" +
                lit(m.env_id) + f" AND Discriminator={lit(DISC_DS_FILE)} AND File_Id=" +
                lit(plan.file_id) + ";")
            orows = mw._query_rows(
                db, "SELECT COUNT(*) FROM dbo.DataSource WHERE EnvironmentData_Id=" +
                lit(m.env_id) + f" AND Discriminator={lit(DISC_DS_OGC)};")
            crows = mw._query_rows(
                db, "SELECT COUNT(*) FROM dbo.Cases WHERE Parent_Id=" + lit(m.site_id) +
                f" AND CenterX={_sql_float(m.center_x)} AND CenterY={_sql_float(m.center_y)};")
        except Exception as exc:
            return mw.ImportResult(False, mw.ImportCode.VERIFY, f"站点挂载校验失败: {exc}")
        site_checks = {
            "site_center": bool(srows) and abs(float(srows[0][0]) - m.center_x) < 1e-6
                           and abs(float(srows[0][1]) - m.center_y) < 1e-6,
            "site_projection": bool(srows) and srows[0][2] == m.projection_wkt,
            "ds_file_mounted": drows and int(drows[0][0]) == 1,
            "ogc_retired": orows and int(orows[0][0]) == 0,
            "cases_synced": crows and int(crows[0][0]) == len(m.cases),
        }
        bad = [k for k, v in site_checks.items() if not v]
        if bad:
            return mw.ImportResult(False, mw.ImportCode.VERIFY, "站点挂载校验不一致",
                                   data={"failed": bad, "checks": site_checks})
        return mw.ImportResult(True, mw.ImportCode.OK, "写后校验通过（含站点挂载）",
                               data={"counts": expected, "site": site_checks})
    return mw.ImportResult(True, mw.ImportCode.OK, "写后校验通过", data={"counts": expected})


def apply_plan(db: str, plan: WritePlan, vault_root: Path,
               allow_prod: bool = False) -> mw.ImportResult:
    if db in mw.PROD_DB_NAMES and not allow_prod:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               f"拒绝写生产库 {db}；请用副本库，或显式 --danger-write-prod")
    target = vault_root / plan.vault_rel_path
    if target.exists():
        return mw.ImportResult(False, mw.ImportCode.CONFLICT, f"vault 目标文件已存在: {target}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plan.meta.path, target)
    except OSError as exc:
        return mw.ImportResult(False, mw.ImportCode.DB_WRITE, f"Vault 复制失败: {exc}")
    ok, out, err = mw._run_sql(db, plan.sql)
    if not ok:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        return mw.ImportResult(False, mw.ImportCode.DB_WRITE, f"写库失败: {err or out}")
    verified = _verify(db, plan, vault_root)
    if not verified.ok:
        return verified
    return verified


def apply_ok(db: str) -> bool:
    """写库白名单（复用气象接口口径）：*_verify / *_test / *_sandbox。"""
    return mw.apply_ok(db)


# --- 编排 --------------------------------------------------------------------
def run(spec: TerrainSpec, tif_path: Path, db: str, apply: bool = False,
        allow_prod: bool = False, out_dir: Optional[Path] = None,
        vault_root: Optional[Path] = None, site: Optional[str] = None,
        center_x: Optional[float] = None, center_y: Optional[float] = None,
        footprint_override: Optional[str] = None, wkt_file: Optional[Path] = None,
        allow_projection_overwrite: bool = False,
        target_epsg: int = DEFAULT_TARGET_EPSG) -> mw.ImportResult:
    if apply and db in mw.PROD_DB_NAMES and not allow_prod:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               f"拒绝写生产库 {db}；请用副本库，或显式 --danger-write-prod")
    if apply and not apply_ok(db):
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")

    issues = spec.validate()
    if issues:
        return mw.ImportResult(False, mw.ImportCode.INPUT, "输入不合法", issues=issues)

    parsed = parse_geotiff(Path(tif_path))
    if not parsed.ok:
        return parsed
    meta: GeoTiffMeta = parsed.data["meta_obj"]

    work = Path(out_dir or (Path.cwd() / "terrain_import_out"))
    work.mkdir(parents=True, exist_ok=True)

    planned = build_plan(
        spec, meta, db, owner_id=spec.owner_id, target_epsg=target_epsg,
        footprint_override=footprint_override, site=site, wkt_file=wkt_file,
        center_x=center_x, center_y=center_y,
        allow_projection_overwrite=allow_projection_overwrite)
    if not planned.ok:
        return planned
    plan: WritePlan = planned.data["plan_obj"]

    try:
        write_root, prod_root = _resolve_vault(vault_root)
        vault_note = {"write_root": str(write_root), "production_root": str(prod_root) if prod_root else None}
    except Exception as exc:
        write_root, prod_root, vault_note = None, None, {"error": str(exc)}

    (work / "plan.json").write_text(json.dumps({
        "spec": spec.__dict__, "tif": meta.to_dict(), "plan": plan.to_dict(),
        "vault": vault_note,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (work / "apply.sql").write_text(plan.sql, encoding="utf-8")
    (work / "rollback.sql").write_text(plan.rollback_sql, encoding="utf-8")

    if not apply:
        return mw.ImportResult(True, mw.ImportCode.OK, "dry-run 完成（未写库、未复制文件）",
                               data={"plan": plan.to_dict(), "out_dir": str(work),
                                     "vault": vault_note,
                                     "warnings": planned.data.get("warnings")})

    if write_root is None:
        return mw.ImportResult(False, mw.ImportCode.DB_GUARD,
                               "无法定位 vault：请显式 --vault-root",
                               data={"vault": vault_note})
    if db not in mw.PROD_DB_NAMES and prod_root and \
            write_root.resolve() == prod_root.resolve():
        return mw.ImportResult(
            False, mw.ImportCode.DB_GUARD,
            f"副本库 {db} 需配对副本 vault：--vault-root 不得指向生产 vault（{prod_root}）；"
            "文件只允许复制到副本/沙盒 vault")
    applied = apply_plan(db, plan, write_root, allow_prod=allow_prod)
    if not applied.ok:
        return mw.ImportResult(False, applied.code, applied.message, data={
            "plan": plan.to_dict(), "rollback": str(work / "rollback.sql"),
            "out_dir": str(work), "vault": vault_note, "verify": applied.data,
            "warnings": planned.data.get("warnings")})
    return mw.ImportResult(True, mw.ImportCode.OK, "写入并校验通过", data={
        "plan": plan.to_dict(), "verify": applied.data, "out_dir": str(work),
        "vault_file": str(write_root / plan.vault_rel_path), "vault": vault_note,
        "warnings": planned.data.get("warnings")})


# --- CLI --------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="地形图导入（模式 B）：GeoTIFF 元数据 + footprint + GeoData 两行落库")
    p.add_argument("--tif", required=True, help="GeoTIFF 路径（原样字节复制进 vault）")
    p.add_argument("--terrain-name", help="GeoData.Title（默认取 tif 主名）")
    p.add_argument("--target-epsg", type=int, default=DEFAULT_TARGET_EPSG,
                   help=f"目标投影（默认 {DEFAULT_TARGET_EPSG}；必须与 tif GeoKeys 一致）")
    p.add_argument("--access-level", type=int, default=2, choices=(0, 1, 2),
                   help="0=Public / 1=Group / 2=Private（金样=2）")
    p.add_argument("--description", help="GeoData.Description（未提供时写空串 N''，与 GUI 金样同构）")
    p.add_argument("--owner-id")
    p.add_argument("--footprint-json",
                   help="四角 GeoJSON 文件或字面量（内置投影表覆盖不到的 EPSG 用）")
    p.add_argument("--site", help="站点挂载扩展：既有站点 Name 或 Id（接口预留）")
    p.add_argument("--wkt-file",
                   help=f"站点挂载用官方 WKT（缺省优先脚本同目录 {DEFAULT_WKT_PATH.name}，"
                        "无文件时用内置 4531 常量）")
    p.add_argument("--center-x", type=float, help="站点中心 X（默认 DEM bbox 中心）")
    p.add_argument("--center-y", type=float, help="站点中心 Y（默认 DEM bbox 中心）")
    p.add_argument("--allow-projection-overwrite", action="store_true",
                   help="允许覆盖站点已有投影（默认拒绝）")
    p.add_argument("--db", required=True, help="目标数据库名（默认拒绝 MUPLocalDb）")
    p.add_argument("--apply", action="store_true", help="真正写库+复制文件（默认 dry-run）")
    p.add_argument("--danger-write-prod", action="store_true", help="允许写 MUPLocalDb（需人工授权）")
    p.add_argument("--vault-root", help="vault 根（副本库必填且不得指向生产 vault）")
    p.add_argument("--out-dir")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    tif_path = Path(args.tif)
    spec = TerrainSpec(
        terrain_name=(args.terrain_name or tif_path.stem).strip(),
        access_level=args.access_level, owner_id=args.owner_id,
        description=args.description)
    result = run(
        spec, tif_path, args.db, apply=args.apply, allow_prod=args.danger_write_prod,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        vault_root=Path(args.vault_root) if args.vault_root else None,
        site=args.site, center_x=args.center_x, center_y=args.center_y,
        footprint_override=args.footprint_json,
        wkt_file=Path(args.wkt_file) if args.wkt_file else None,
        allow_projection_overwrite=args.allow_projection_overwrite,
        target_epsg=args.target_epsg)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(("[OK] " if result.ok else "[FAIL] ") + result.message)
        if result.data.get("plan"):
            plan = result.data["plan"]
            print("  geodata_id=" + plan.get("geodata_id", ""))
            print("  file_id=" + plan.get("file_id", ""))
            print("  vault_rel=" + plan.get("vault_rel_path", ""))
            print("  epsg=" + str(plan.get("epsg", "")))
            if plan.get("site_mount"):
                print("  site_mount=" + json.dumps(plan["site_mount"], ensure_ascii=False))
        if result.data.get("vault_file"):
            print("  vault_file=" + str(result.data["vault_file"]))
        for w in result.data.get("warnings") or []:
            print("  warn: " + w.get("message", ""))
        for issue in result.issues:
            print("  issue: " + issue.code + " " + issue.message)
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
