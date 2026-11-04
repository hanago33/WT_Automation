# encoding: utf-8
"""风机类型导入（模式 B，对标 mup_weather_import）：.wtg 解析 + blob 重建 + 12 行落库。

职责
----
把「新建风机类型」从 GUI 向导中拆出来，做成可编排、可 dry-run、可回滚的 CLI：

  .wtg（Windographer XML） + 元参数（名称/型号/曲线版本名/认证等级/访问级别）
      -> Python 解析 DataPoint（唯一解析方）
      -> PowerShell + .NET DataSet 重建两条 PersistedValues blob
         （BinaryFormatter + RemotingFormat=Binary + CultureInfo(30724)，
          依据 2026-11-04 zcode/antigravity 双验证：与 GUI 金样 6491/6491 byte-diff=0）
      -> 12 行子树写入 8 张 WTType* 表（WTType / CurveCollection×4 / HubCollection /
         DocumentCollection / Certification / Design / Curve×2 / Version）

派生规则（antigravity 从 MTDWindTurbineCurves.dll `RefreshCharacteristics()` IL 确证）
  · CutInSpeed  = 曲线风速 Keys.Min()
  · CutOutSpeed = 曲线风速 Keys.Max()      （不是 .wtg 的 HighSpeedCutOut）
  · RatedPower(MW) = (功率 kW/1000).Max()
  · RatedWindSpeed = 首个（最小风速）达 RatedPower 的点
  · TurbineModel 为用户输入；CLI 默认取 .wtg Description 前缀仅是便捷初始值。

红线
----
  · 默认 dry-run：只解析、只产出 plan.json / *.bin / *.sql，不写库。
  · 写库必须 --apply，且 --db 必须显式；默认拒绝 MUPLocalDb，除非 --danger-write-prod。
  · 不启动 MUP、不触 HASP/GUI；PowerShell 仅做 .NET 序列化（独立子进程）。
  · 单事务 + 回滚脚本 + 写后校验（行数 + blob 逐字节比对）；同名类型默认拒绝（fail-closed）。
  · .wtg 原文不留存（与 GUI 一致：Vault WTTYPEDOCUMENTS 为空）；轮毂高度/海拔无库级落点，
    仅记录进 plan.json（GUI 建型后 WTTypeHubCollection 也为空）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "ImportIssue",
    "ImportResult",
    "TurbineSpec",
    "CurvePoint",
    "WindTurbineFile",
    "DerivedDesign",
    "WritePlan",
    "parse_wtg",
    "derive_design",
    "description_prefix",
    "build_blobs",
    "build_plan",
    "apply_plan",
    "apply_ok",
    "run",
    "main",
]

SQLCMD_DEFAULT = r"C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\110\Tools\Binn\SQLCMD.EXE"
DEFAULT_DB = "MUPLocalDb"
PROD_DB_NAMES = {"MUPLocalDb"}
DEFAULT_LOCALE_LCID = 30724  # GUI SelectedCulture='zh'；blob byte-exact 的关键②

DISC_POWER_COLL = "MTDWindTurbineTypeAirDensityTurbulenceIntensityCurveCollection"
DISC_THRUST_COLL = "MTDWindTurbineTypeAirDensityCurveCollection"
DISC_NOISE_COLL = "MTDWindTurbineTypeNoiseCurveCollection"
DISC_POWER_CURVE = "MUPWindTurbineTypeAirDensityTurbulenceIntensityCurve"
DISC_THRUST_CURVE = "MUPWindTurbineTypeAirDensityCurve"
TI_UNSET_SENTINEL = -100.0  # WTTypeCurve.TurbulenceIntensity 哨兵 = 未指定

# WTTypeCertification 默认 = GUI 向导 IEC 61400-1 Class I（金样 0fd1fbf1 实测）。
CERT_DEFAULTS = {
    "certification_norm": 3,      # Iec_4（MTDWindTurbineTypeEnums.CertificationNorm）
    "wohler_exponent": 1,         # 金样=1（出厂类型为 10，可 --wohler-exponent 覆盖）
    "speed_class": 1,             # Class I
    "turbulence_class": 1,        # Cat A
    "vref": 50.0,
    "iref": 0.16,
    "maximum_inflow_angle": 8,
    "minimum_wind_shear_exponent": 0.0,
    "maximum_wind_shear_exponent": 0.2,
    "maximum_air_density": 1.225,
    "vaverage": 10.0,
    "is_tropical": 0,
}
DESIGN_DEFAULTS = {"number_of_blades": 3, "power_control": 1,
                   "min_rotational_speed": 0.0, "max_rotational_speed": 0.0}


# --- 错误代数（tagged result，不做静默降级）--------------------------------
class ImportCode:
    OK = "ok"
    INPUT = "input_invalid"
    COMPUTE = "compute_failed"
    DB_GUARD = "db_guard"
    DB_WRITE = "db_write_failed"
    VERIFY = "verify_failed"
    CONFLICT = "name_exists"
    UNSUPPORTED = "unsupported"


@dataclass
class ImportIssue:
    code: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class ImportResult:
    ok: bool
    code: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    issues: list[ImportIssue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "data": self.data,
            "issues": [i.to_dict() for i in self.issues],
        }

    def exit_code(self) -> int:
        if self.ok:
            return 0
        return {
            ImportCode.INPUT: 2,
            ImportCode.COMPUTE: 3,
            ImportCode.DB_GUARD: 4,
            ImportCode.DB_WRITE: 4,
            ImportCode.VERIFY: 5,
            ImportCode.CONFLICT: 6,
            ImportCode.UNSUPPORTED: 2,
        }.get(self.code, 1)


# --- 输入模型 ---------------------------------------------------------------
@dataclass
class TurbineSpec:
    name: str                       # WTType.Name（用户输入，如 'WT6250D220-Test'）
    version_name: str               # 曲线版本名（WTTypeVersion.Name，如 'Cp0.429'）
    turbine_model: str = ""         # WTType.TurbineModel（GUI 为纯用户输入）
    access_level: int = 2           # 0=Public / 1=Group / 2=Private（金样=2）
    owner_id: Optional[str] = None
    country: Optional[str] = None
    description: Optional[str] = None
    cost_by_kw: float = 0.0
    currency: str = "EUR"
    number_of_blades: int = DESIGN_DEFAULTS["number_of_blades"]
    power_control: int = DESIGN_DEFAULTS["power_control"]
    cert: dict[str, Any] = field(default_factory=lambda: dict(CERT_DEFAULTS))
    # 以下元参数无库级落点（GUI 建型同样不落库），仅进 plan.json 存档：
    hub_height: Optional[float] = None
    elevation: Optional[float] = None

    def validate(self) -> list[ImportIssue]:
        issues: list[ImportIssue] = []
        if not str(self.name or "").strip():
            issues.append(ImportIssue(ImportCode.INPUT, "名称不能为空"))
        if not str(self.version_name or "").strip():
            issues.append(ImportIssue(ImportCode.INPUT, "曲线版本名不能为空（GUI 向导亦为必输）"))
        if int(self.access_level) not in (0, 1, 2):
            issues.append(ImportIssue(ImportCode.INPUT, "访问级别只能是 0/1/2"))
        return issues


@dataclass
class CurvePoint:
    wind_speed: float
    power_kw: float
    thrust_ct: float


@dataclass
class WindTurbineFile:
    path: Path
    description: str = ""
    manufacturer: str = ""
    rotor_diameter: Optional[float] = None
    suggested_heights: list[float] = field(default_factory=list)
    air_density: Optional[float] = None
    points: list[CurvePoint] = field(default_factory=list)

    def to_meta(self) -> dict:
        return {
            "path": str(self.path),
            "description": self.description,
            "manufacturer": self.manufacturer,
            "rotor_diameter": self.rotor_diameter,
            "suggested_heights": list(self.suggested_heights),
            "air_density": self.air_density,
            "points": len(self.points),
        }


@dataclass
class DerivedDesign:
    cut_in_speed: float
    cut_out_speed: float
    rated_power_mw: float
    rated_wind_speed: float
    theoretical_rated_power_kw: float

    def to_dict(self) -> dict:
        return {
            "cut_in_speed": self.cut_in_speed,
            "cut_out_speed": self.cut_out_speed,
            "rated_power_mw": self.rated_power_mw,
            "rated_wind_speed": self.rated_wind_speed,
            "theoretical_rated_power_kw": self.theoretical_rated_power_kw,
        }


@dataclass
class WritePlan:
    spec: TurbineSpec
    wtg: WindTurbineFile
    derived: DerivedDesign
    air_density: float
    wttype_id: str
    design_id: str
    cert_id: str
    version_id: str
    hub_collection_id: str
    document_collection_id: str
    power_coll_id: str
    thrust_coll_id: str
    noise_freq_coll_id: str
    noise_ws_coll_id: str
    power_curve_id: str
    thrust_curve_id: str
    power_blob: bytes
    thrust_blob: bytes
    owner_id: str
    sql: str = ""
    rollback_sql: str = ""
    not_persisted: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "wttype_id": self.wttype_id,
            "design_id": self.design_id,
            "cert_id": self.cert_id,
            "version_id": self.version_id,
            "hub_collection_id": self.hub_collection_id,
            "document_collection_id": self.document_collection_id,
            "power_coll_id": self.power_coll_id,
            "thrust_coll_id": self.thrust_coll_id,
            "noise_freq_coll_id": self.noise_freq_coll_id,
            "noise_ws_coll_id": self.noise_ws_coll_id,
            "power_curve_id": self.power_curve_id,
            "thrust_curve_id": self.thrust_curve_id,
            "owner_id": self.owner_id,
            "derived": self.derived.to_dict(),
            "air_density": self.air_density,
            "power_blob_len": len(self.power_blob),
            "thrust_blob_len": len(self.thrust_blob),
            "wtg": self.wtg.to_meta(),
            "not_persisted": dict(self.not_persisted),
        }


# --- 工具 -------------------------------------------------------------------
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _f32(x: float) -> float:
    """按 IEEE single 量化（库列 real / blob Single 均为 4 字节浮点）。"""
    return struct.unpack("<f", struct.pack("<f", float(x)))[0]


def _sqlcmd() -> str:
    configured = os.environ.get("MUP_SQLCMD")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("sqlcmd")
    if found:
        return found
    if Path(SQLCMD_DEFAULT).is_file():
        return SQLCMD_DEFAULT
    raise RuntimeError("sqlcmd not found; set MUP_SQLCMD")


def _sql_literal(value: Optional[str]) -> str:
    if value is None:
        return "NULL"
    return "N'" + str(value).replace("'", "''") + "'"


def _sql_float(value: float) -> str:
    return repr(float(value))


def _sql_hex(data: bytes) -> str:
    return "0x" + data.hex().upper()


def _run_sql(db: str, sql: str, timeout: int = 120) -> tuple[bool, str, str]:
    """执行 SQL（stdin 传脚本，避免 Windows 路径转换问题）。"""
    command = [_sqlcmd(), "-S", os.environ.get("MUP_LOCALDB_SERVER", r"(localdb)\Meteodyn"),
               "-d", db, "-b", "-r1", "-h", "-1", "-W", "-s", "|", "-f", "65001"]
    try:
        proc = subprocess.run(command, input=sql.encode("utf-8"), capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, "", str(exc)
    out = _decode_console(proc.stdout).strip()
    err = _decode_console(proc.stderr).strip()
    return proc.returncode == 0, out, err


def _decode_console(data: bytes) -> str:
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _query_rows(db: str, sql: str) -> list[list[str]]:
    ok, out, err = _run_sql(db, "SET NOCOUNT ON;\n" + sql)
    if not ok:
        raise RuntimeError(err or out or "sqlcmd failed")
    rows = []
    for line in out.splitlines():
        if line.strip():
            rows.append([p.strip() for p in line.split("|")])
    return rows


def _pick_owner(db: str, explicit: Optional[str]) -> tuple[Optional[str], str]:
    if explicit:
        return explicit, ""
    try:
        rows = _query_rows(db, "SELECT TOP 1 Id FROM dbo.Identities WHERE Discriminator='MUPUserObject' ORDER BY Id;")
        if rows and rows[0]:
            return rows[0][0], ""
    except Exception as exc:
        return None, str(exc)
    return None, "no MUPUserObject in Identities"


def apply_ok(db: str) -> bool:
    """写库白名单：必须以 _verify / _test / _sandbox 结尾（大小写不敏感）。"""
    name = str(db or "").strip().lower()
    return name.endswith(("_verify", "_test", "_sandbox"))


# --- .wtg 解析（Python 是唯一解析方；PS 只做 .NET 序列化）--------------------
def parse_wtg(path: Path) -> ImportResult:
    """解析 Windographer .wtg：根属性 + PerformanceTable DataPoint。

    依据侦查：曲线数据点在 `//DataPoint`（WindSpeed/PowerOutput/ThrustCoEfficient），
    AirDensity 在 PerformanceTable，RotorDiameter 在根节点。
    """
    path = Path(path)
    if not path.is_file():
        return ImportResult(False, ImportCode.INPUT, f".wtg 文件不存在: {path}")
    try:
        root = ET.parse(str(path)).getroot()
    except ET.ParseError as exc:
        return ImportResult(False, ImportCode.INPUT, f".wtg XML 解析失败: {exc}")
    if root.tag != "WindTurbineGenerator":
        return ImportResult(False, ImportCode.INPUT,
                            f"不是 Windographer .wtg（根节点 {root.tag}）")

    wtg = WindTurbineFile(path=path, description=root.get("Description") or "",
                          manufacturer=root.get("ManufacturerName") or "")
    if root.get("RotorDiameter"):
        try:
            wtg.rotor_diameter = float(root.get("RotorDiameter"))
        except ValueError:
            return ImportResult(False, ImportCode.INPUT, "根节点 RotorDiameter 不是数值")
    for h in root.findall("./SuggestedHeights/Height"):
        try:
            wtg.suggested_heights.append(float((h.text or "").strip()))
        except ValueError:
            pass

    table = root.find("./PerformanceTable")
    if table is None:
        return ImportResult(False, ImportCode.INPUT, ".wtg 缺少 PerformanceTable（无曲线可导入）")
    if table.get("AirDensity"):
        try:
            wtg.air_density = float(table.get("AirDensity"))
        except ValueError:
            return ImportResult(False, ImportCode.INPUT, "PerformanceTable@AirDensity 不是数值")

    for idx, dp in enumerate(table.findall("./DataTable/DataPoint")):
        try:
            wtg.points.append(CurvePoint(
                wind_speed=float(dp.get("WindSpeed")),
                power_kw=float(dp.get("PowerOutput")),
                thrust_ct=float(dp.get("ThrustCoEfficient")),
            ))
        except (TypeError, ValueError):
            return ImportResult(
                False, ImportCode.INPUT,
                f"第 {idx + 1} 个 DataPoint 的 WindSpeed/PowerOutput/ThrustCoEfficient 缺失或不是数值")
    if not wtg.points:
        return ImportResult(False, ImportCode.INPUT, ".wtg 无 DataPoint（NoCurvesInFile）")
    return ImportResult(True, ImportCode.OK, ".wtg 解析完成",
                        data={"wtg_meta": wtg.to_meta(),
                              "points": [{"wind_speed": p.wind_speed, "power_kw": p.power_kw,
                                          "thrust_ct": p.thrust_ct} for p in wtg.points]})


def derive_design(points: list[CurvePoint]) -> DerivedDesign:
    """曲线派生设计值（对应 RefreshCharacteristics() 的 IL 规则，single 域运算）。"""
    speeds = [p.wind_speed for p in points]
    mw_values = [_f32(p.power_kw / 1000.0) for p in points]
    rated_mw = max(mw_values)
    rated_ws = min(s for s, v in zip(speeds, mw_values) if v == rated_mw)
    return DerivedDesign(
        cut_in_speed=min(speeds),
        cut_out_speed=max(speeds),
        rated_power_mw=rated_mw,
        rated_wind_speed=rated_ws,
        theoretical_rated_power_kw=max(p.power_kw for p in points),
    )


# --- blob 重建（PowerShell + .NET DataSet，双验证 byte-exact 配方）------------
PS_SCRIPT = r'''
$ErrorActionPreference = 'Stop'
$out = $env:OUT_DIR
New-Item -ItemType Directory -Force -Path $out | Out-Null
Add-Type -AssemblyName System.Data
$lines = New-Object System.Collections.Generic.List[string]
function Emit($k, $v) { $lines.Add([string]$k + "=" + [string]$v) }

try {
  $loc = New-Object Globalization.CultureInfo ([int]$env:LCID)
  $json = [IO.File]::ReadAllText($env:POINTS_JSON, [Text.Encoding]::UTF8) | ConvertFrom-Json
  $pts = @($json.points)
  Emit 'POINTS' $pts.Count

  function Convert-Point([object]$p, [string]$mode) {
    $bin = [single]$p.bin
    if ($mode -eq 'power') { $val = [single]([double]$p.power / 1000.0) }
    else { $val = [single]$p.ct }
    return ,@($bin, $val)
  }

  function Build-Blob([object[]]$points, [string]$mode, [Globalization.CultureInfo]$loc, [string]$outPath) {
    $ds = New-Object System.Data.DataSet
    $ds.RemotingFormat = [System.Data.SerializationFormat]::Binary
    $ds.Locale = $loc
    $dt = New-Object System.Data.DataTable
    $dt.TableName = 'curvevalues'
    $dt.Locale = $loc
    $null = $dt.Columns.Add('bin', [single])
    $null = $dt.Columns.Add('value', [single])
    foreach ($p in $points) {
      $pair = Convert-Point $p $mode
      $null = $dt.Rows.Add($pair[0], $pair[1])
    }
    $null = $ds.Tables.Add($dt)
    $ms = New-Object IO.MemoryStream
    $fmt = New-Object Runtime.Serialization.Formatters.Binary.BinaryFormatter
    $fmt.Serialize($ms, $ds)
    $bytes = $ms.ToArray()
    [IO.File]::WriteAllBytes($outPath, $bytes)
    return ,$bytes
  }

  function Check-Roundtrip([byte[]]$bytes, [object[]]$points, [string]$mode, [Globalization.CultureInfo]$loc) {
    $fmt = New-Object Runtime.Serialization.Formatters.Binary.BinaryFormatter
    $ms = New-Object IO.MemoryStream (,$bytes)
    $ds = $fmt.Deserialize($ms)
    if ($ds.Tables.Count -ne 1) { return 'FAIL:tables' }
    $t = $ds.Tables[0]
    if ($t.TableName -ne 'curvevalues') { return 'FAIL:tablename' }
    if ($t.Columns[0].ColumnName -ne 'bin' -or $t.Columns[1].ColumnName -ne 'value') { return 'FAIL:columns' }
    if ($t.Rows.Count -ne $points.Count) { return 'FAIL:rows' }
    for ($i = 0; $i -lt $t.Rows.Count; $i++) {
      $pair = Convert-Point $points[$i] $mode
      if (-not ($t.Rows[$i][0] -eq $pair[0])) { return ('FAIL:bin@' + $i) }
      if (-not ($t.Rows[$i][1] -eq $pair[1])) { return ('FAIL:value@' + $i) }
    }
    if ($ds.Locale.LCID -ne $loc.LCID) { return 'FAIL:locale' }
    return 'OK'
  }

  function Compare-Golden([byte[]]$bytes, [string]$goldenPath) {
    if ([string]::IsNullOrEmpty($goldenPath) -or -not ([IO.File]::Exists($goldenPath))) { return 'SKIP' }
    $g = [IO.File]::ReadAllBytes($goldenPath)
    if ($g.Length -ne $bytes.Length) { return ('FAIL:len ' + $g.Length + '!=' + $bytes.Length) }
    $d = 0; $first = -1
    for ($i = 0; $i -lt $g.Length; $i++) {
      if ($g[$i] -ne $bytes[$i]) { $d++; if ($first -lt 0) { $first = $i } }
    }
    if ($d -eq 0) { return 'OK' }
    return ('FAIL:diff ' + $d + '@offset' + $first)
  }

  foreach ($spec in @(@('power', 'GOLDEN_POWER'), @('thrust', 'GOLDEN_THRUST'))) {
    $mode = $spec[0]
    $golden = [Environment]::GetEnvironmentVariable($spec[1])
    $outPath = Join-Path $out ($mode + '.bin')
    $bytes = Build-Blob $pts $mode $loc $outPath
    $sha = [System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    Emit ($mode.ToUpper() + '_LEN') $bytes.Length
    Emit ($mode.ToUpper() + '_SHA') (([BitConverter]::ToString($sha)) -replace '-', '')
    Emit ($mode.ToUpper() + '_RT') (Check-Roundtrip $bytes $pts $mode $loc)
    Emit ($mode.ToUpper() + '_GOLDEN') (Compare-Golden $bytes $golden)
    # 反解 TSV 供人工/测试核对（列：bin=value 原样文本）
    $fmt2 = New-Object Runtime.Serialization.Formatters.Binary.BinaryFormatter
    $ms2 = New-Object IO.MemoryStream (,$bytes)
    $ds2 = $fmt2.Deserialize($ms2)
    $sb = New-Object Text.StringBuilder
    foreach ($r in $ds2.Tables[0].Rows) {
      [void]$sb.AppendLine($r[0].ToString() + [char]9 + $r[1].ToString())
    }
    [IO.File]::WriteAllText((Join-Path $out ($mode + '_rows.tsv')), $sb.ToString(), [Text.Encoding]::UTF8)
  }
  Emit 'STATUS' 'OK'
} catch {
  Emit 'STATUS' 'FAIL'
  Emit 'ERROR' $_.Exception.Message
}
[IO.File]::WriteAllLines((Join-Path $out 'result.txt'), $lines, [Text.Encoding]::UTF8)
'''


def _parse_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def build_blobs(points: list[CurvePoint], work_dir: Path, lcid: int = DEFAULT_LOCALE_LCID,
                golden_power: Optional[Path] = None, golden_thrust: Optional[Path] = None,
                timeout: int = 180) -> ImportResult:
    """重建两条 PersistedValues blob，并做反解自检 + 可选金样逐字节比对。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir = work_dir / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    points_json = work_dir / "points.json"
    points_json.write_text(json.dumps(
        {"points": [{"bin": p.wind_speed, "power": p.power_kw, "ct": p.thrust_ct} for p in points]},
        ensure_ascii=False), encoding="utf-8")
    ps1 = work_dir / "turbine_blob_build.ps1"
    ps1.write_text(PS_SCRIPT, encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "OUT_DIR": str(out_dir),
        "POINTS_JSON": str(points_json),
        "LCID": str(int(lcid)),
        "GOLDEN_POWER": str(golden_power or ""),
        "GOLDEN_THRUST": str(golden_thrust or ""),
    })
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ImportResult(False, ImportCode.COMPUTE, f"PowerShell 序列化宿主执行失败: {exc}")

    kv = _parse_kv(out_dir / "result.txt")
    if kv.get("STATUS") != "OK":
        return ImportResult(
            False, ImportCode.COMPUTE,
            kv.get("ERROR") or "PowerShell 宿主未返回 STATUS=OK",
            data={"returncode": proc.returncode,
                  "stdout": proc.stdout.decode("utf-8", "replace")[-2000:]})

    power = (out_dir / "power.bin").read_bytes()
    thrust = (out_dir / "thrust.bin").read_bytes()
    issues: list[ImportIssue] = []
    for mode, blob, sha_kv in (("POWER", power, kv.get("POWER_SHA")),
                               ("THRUST", thrust, kv.get("THRUST_SHA"))):
        if not blob:
            issues.append(ImportIssue(ImportCode.COMPUTE, f"{mode} blob 为空"))
            continue
        if sha_kv and _sha256_bytes(blob) != sha_kv:
            issues.append(ImportIssue(ImportCode.VERIFY,
                                      f"{mode} blob SHA 不一致（产物文件被改动？）",
                                      {"expected": sha_kv, "actual": _sha256_bytes(blob)}))
        if kv.get(mode + "_RT") != "OK":
            issues.append(ImportIssue(ImportCode.VERIFY,
                                      f"{mode} blob 反解自检失败: {kv.get(mode + '_RT')}"))
        golden_state = kv.get(mode + "_GOLDEN") or "SKIP"
        if golden_state.startswith("FAIL"):
            issues.append(ImportIssue(ImportCode.VERIFY,
                                      f"{mode} 与金样不一致: {golden_state}"
                                      "（若仅差 2 字节请核对 --locale-lcid）",
                                      {"detail": golden_state}))
    if issues:
        return ImportResult(False, ImportCode.VERIFY, "blob 自检未通过，拒绝继续",
                            data={"kv": kv}, issues=issues)
    return ImportResult(True, ImportCode.OK, "blob 重建完成（反解自检通过）",
                        data={"power_blob": power, "thrust_blob": thrust, "kv": kv,
                              "artifacts": str(out_dir)})


# --- 计划与写入 -------------------------------------------------------------
def build_plan(spec: TurbineSpec, wtg: WindTurbineFile, derived: DerivedDesign,
               power_blob: bytes, thrust_blob: bytes, db: str,
               owner_id: Optional[str] = None,
               air_density: Optional[float] = None) -> ImportResult:
    issues = spec.validate()
    if issues:
        return ImportResult(False, ImportCode.INPUT, "输入不合法", issues=issues)
    rho = float(air_density if air_density is not None else (wtg.air_density or 1.225))

    try:
        exists = _query_rows(db, f"SELECT TOP 1 Id FROM dbo.WTType WHERE Name={_sql_literal(spec.name)};")
    except Exception as exc:
        return ImportResult(False, ImportCode.DB_GUARD, f"无法读取目标库: {exc}")
    if exists:
        return ImportResult(False, ImportCode.CONFLICT, f"同名风机类型已存在: {spec.name}")

    resolved_owner, owner_err = _pick_owner(db, owner_id or spec.owner_id)
    if not resolved_owner:
        return ImportResult(False, ImportCode.DB_GUARD, "无法确定 OwnerId: " + (owner_err or "unknown"))

    ids = {k: str(uuid.uuid4()) for k in (
        "wttype", "design", "cert", "version", "hub", "doc",
        "power_coll", "thrust_coll", "noise_freq_coll", "noise_ws_coll",
        "power_curve", "thrust_curve")}
    cert = {k: spec.cert.get(k, v) for k, v in CERT_DEFAULTS.items()}
    design_kw = derived.theoretical_rated_power_kw

    stmts: list[str] = []
    # 父表先行（集合 → 认证/设计 → 曲线 → 主表 → 版本），单事务 + XACT_ABORT 兜底。
    stmts.append(
        "INSERT INTO dbo.WTTypeCurveCollection (Id,Discriminator) VALUES ("
        f"{_sql_literal(ids['power_coll'])},{_sql_literal(DISC_POWER_COLL)});")
    stmts.append(
        "INSERT INTO dbo.WTTypeCurveCollection (Id,Discriminator) VALUES ("
        f"{_sql_literal(ids['thrust_coll'])},{_sql_literal(DISC_THRUST_COLL)});")
    stmts.append(
        "INSERT INTO dbo.WTTypeCurveCollection (Id,Discriminator) VALUES ("
        f"{_sql_literal(ids['noise_freq_coll'])},{_sql_literal(DISC_NOISE_COLL)});")
    stmts.append(
        "INSERT INTO dbo.WTTypeCurveCollection (Id,Discriminator) VALUES ("
        f"{_sql_literal(ids['noise_ws_coll'])},{_sql_literal(DISC_NOISE_COLL)});")
    stmts.append(f"INSERT INTO dbo.WTTypeHubCollection (Id) VALUES ({_sql_literal(ids['hub'])});")
    stmts.append(f"INSERT INTO dbo.WTTypeDocumentCollection (Id) VALUES ({_sql_literal(ids['doc'])});")
    stmts.append(
        "INSERT INTO dbo.WTTypeCertification (Id,CertificationNorm,WohlerExponent,SpeedClass,"
        "TurbulenceClass,Vref,Iref,MaximumInflowAngle,MinimumWindShearExponent,"
        "MaximumWindShearExponent,MaximumAirDensity,Vaverage,IsTropical) VALUES ("
        f"{_sql_literal(ids['cert'])},{int(cert['certification_norm'])},{int(cert['wohler_exponent'])},"
        f"{int(cert['speed_class'])},{int(cert['turbulence_class'])},{_sql_float(cert['vref'])},"
        f"{_sql_float(cert['iref'])},{int(cert['maximum_inflow_angle'])},"
        f"{_sql_float(cert['minimum_wind_shear_exponent'])},"
        f"{_sql_float(cert['maximum_wind_shear_exponent'])},{_sql_float(cert['maximum_air_density'])},"
        f"{_sql_float(cert['vaverage'])},{int(cert['is_tropical'])});")
    stmts.append(
        "INSERT INTO dbo.WTTypeDesign (Id,RotorDiameter,NumberOfBlades,PowerControl,"
        "MinRotationalSpeed,MaxRotationalSpeed,CutInSpeed,CutOutSpeed,RatedPower,RatedWindSpeed,"
        "TheoreticalRatedPower) VALUES ("
        f"{_sql_literal(ids['design'])},{_sql_float(wtg.rotor_diameter if wtg.rotor_diameter is not None else 0.0)},"
        f"{int(spec.number_of_blades)},{int(spec.power_control)},"
        f"{_sql_float(DESIGN_DEFAULTS['min_rotational_speed'])},"
        f"{_sql_float(DESIGN_DEFAULTS['max_rotational_speed'])},"
        f"{_sql_float(derived.cut_in_speed)},{_sql_float(derived.cut_out_speed)},"
        f"{_sql_float(derived.rated_power_mw)},{_sql_float(derived.rated_wind_speed)},"
        f"{_sql_float(design_kw)});")
    # 功率曲线：TI=-100 哨兵（未指定）；推力曲线：TI=NULL（金样一致）。
    stmts.append(
        "INSERT INTO dbo.WTTypeCurve (Id,PersistedValues,AirDensity,WTTypeCurveCollection_Id,"
        "IsRews,Version,Discriminator,TurbulenceIntensity) VALUES ("
        f"{_sql_literal(ids['power_curve'])},{_sql_hex(power_blob)},{_sql_float(rho)},"
        f"{_sql_literal(ids['power_coll'])},0,{_sql_literal(spec.version_name)},"
        f"{_sql_literal(DISC_POWER_CURVE)},{_sql_float(TI_UNSET_SENTINEL)});")
    stmts.append(
        "INSERT INTO dbo.WTTypeCurve (Id,PersistedValues,AirDensity,WTTypeCurveCollection_Id,"
        "IsRews,Version,Discriminator,TurbulenceIntensity) VALUES ("
        f"{_sql_literal(ids['thrust_curve'])},{_sql_hex(thrust_blob)},{_sql_float(rho)},"
        f"{_sql_literal(ids['thrust_coll'])},0,{_sql_literal(spec.version_name)},"
        f"{_sql_literal(DISC_THRUST_CURVE)},NULL);")
    stmts.append(
        "INSERT INTO dbo.WTType (Id,Name,TurbineModel,Country,Description,Certification_Id,"
        "Design_Id,Documents_Id,Image_Id,PowerCurves_Id,ThrustCurves_Id,"
        "Financial_WindTurbineCostByKW_Value,Financial_WindTurbineCostByKW_Currency,"
        "WTTypeHubCollection_Id,NoisesFrequencyCurves_Id,NoisesWindSpeedCurves_Id,"
        "CreationDate,ModificationDate,CreatorId,OwnerId,AccessLevel) VALUES ("
        f"{_sql_literal(ids['wttype'])},{_sql_literal(spec.name)},"
        f"{_sql_literal(spec.turbine_model or None)},{_sql_literal(spec.country)},"
        f"{_sql_literal(spec.description)},{_sql_literal(ids['cert'])},{_sql_literal(ids['design'])},"
        f"{_sql_literal(ids['doc'])},NULL,{_sql_literal(ids['power_coll'])},"
        f"{_sql_literal(ids['thrust_coll'])},{_sql_float(spec.cost_by_kw)},"
        f"{_sql_literal(spec.currency)},{_sql_literal(ids['hub'])},"
        f"{_sql_literal(ids['noise_freq_coll'])},{_sql_literal(ids['noise_ws_coll'])},"
        "SYSDATETIME(),SYSDATETIME()," + _sql_literal(resolved_owner) + "," +
        _sql_literal(resolved_owner) + f",{int(spec.access_level)});")
    stmts.append(
        "INSERT INTO dbo.WTTypeVersion (Id,Name,Description,CreationDateTime,ModificationDateTime,"
        "CreationUserId,ModificationUserId,Type,WTType_Id) VALUES ("
        f"{_sql_literal(ids['version'])},{_sql_literal(spec.version_name)},NULL,"
        "SYSDATETIME(),SYSDATETIME()," + _sql_literal(resolved_owner) + "," +
        _sql_literal(resolved_owner) + f",0,{_sql_literal(ids['wttype'])});")

    sql = "SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;\n" + "\n".join(stmts) + "\nCOMMIT;\n"
    rollback = (
        "SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;\n"
        f"DELETE FROM dbo.WTTypeVersion WHERE Id={_sql_literal(ids['version'])};\n"
        f"DELETE FROM dbo.WTType WHERE Id={_sql_literal(ids['wttype'])};\n"
        f"DELETE FROM dbo.WTTypeCurve WHERE Id IN ({_sql_literal(ids['power_curve'])},{_sql_literal(ids['thrust_curve'])});\n"
        f"DELETE FROM dbo.WTTypeDesign WHERE Id={_sql_literal(ids['design'])};\n"
        f"DELETE FROM dbo.WTTypeCertification WHERE Id={_sql_literal(ids['cert'])};\n"
        f"DELETE FROM dbo.WTTypeHubCollection WHERE Id={_sql_literal(ids['hub'])};\n"
        f"DELETE FROM dbo.WTTypeDocumentCollection WHERE Id={_sql_literal(ids['doc'])};\n"
        f"DELETE FROM dbo.WTTypeCurveCollection WHERE Id IN ({_sql_literal(ids['power_coll'])},"
        f"{_sql_literal(ids['thrust_coll'])},{_sql_literal(ids['noise_freq_coll'])},"
        f"{_sql_literal(ids['noise_ws_coll'])});\n"
        "COMMIT;\n"
    )

    not_persisted = {k: v for k, v in {
        "hub_height_m": spec.hub_height,
        "elevation_m": spec.elevation,
        "manufacturer": wtg.manufacturer,
        "suggested_heights": list(wtg.suggested_heights),
        "wtg_file": str(wtg.path),
    }.items() if v not in (None, [], "")}
    plan = WritePlan(
        spec=spec, wtg=wtg, derived=derived, air_density=rho,
        wttype_id=ids["wttype"], design_id=ids["design"], cert_id=ids["cert"],
        version_id=ids["version"], hub_collection_id=ids["hub"],
        document_collection_id=ids["doc"], power_coll_id=ids["power_coll"],
        thrust_coll_id=ids["thrust_coll"], noise_freq_coll_id=ids["noise_freq_coll"],
        noise_ws_coll_id=ids["noise_ws_coll"], power_curve_id=ids["power_curve"],
        thrust_curve_id=ids["thrust_curve"], power_blob=power_blob,
        thrust_blob=thrust_blob, owner_id=resolved_owner,
        sql=sql, rollback_sql=rollback, not_persisted=not_persisted)
    return ImportResult(True, ImportCode.OK, "写入计划已生成",
                        data={"plan": plan.to_dict(), "plan_obj": plan})


def _verify(db: str, plan: WritePlan) -> ImportResult:
    """写后校验：12 行逐表计数 + 两条 blob 逐字节比对（SQL 内二进制等值）。"""
    try:
        rows = _query_rows(
            db,
            "SELECT "
            "(SELECT COUNT(*) FROM dbo.WTType WHERE Id=%s),"
            "(SELECT COUNT(*) FROM dbo.WTTypeDesign WHERE Id=%s),"
            "(SELECT COUNT(*) FROM dbo.WTTypeCertification WHERE Id=%s),"
            "(SELECT COUNT(*) FROM dbo.WTTypeVersion WHERE Id=%s AND WTType_Id=%s),"
            "(SELECT COUNT(*) FROM dbo.WTTypeHubCollection WHERE Id=%s),"
            "(SELECT COUNT(*) FROM dbo.WTTypeDocumentCollection WHERE Id=%s),"
            "(SELECT COUNT(*) FROM dbo.WTTypeCurveCollection WHERE Id IN (%s,%s,%s,%s)),"
            "(SELECT COUNT(*) FROM dbo.WTTypeCurve WHERE WTTypeCurveCollection_Id IN (%s,%s)),"
            "ISNULL((SELECT DATALENGTH(PersistedValues) FROM dbo.WTTypeCurve WHERE Id=%s),0),"
            "ISNULL((SELECT DATALENGTH(PersistedValues) FROM dbo.WTTypeCurve WHERE Id=%s),0);"
            % (_sql_literal(plan.wttype_id), _sql_literal(plan.design_id),
               _sql_literal(plan.cert_id), _sql_literal(plan.version_id),
               _sql_literal(plan.wttype_id), _sql_literal(plan.hub_collection_id),
               _sql_literal(plan.document_collection_id),
               _sql_literal(plan.power_coll_id), _sql_literal(plan.thrust_coll_id),
               _sql_literal(plan.noise_freq_coll_id), _sql_literal(plan.noise_ws_coll_id),
               _sql_literal(plan.power_coll_id), _sql_literal(plan.thrust_coll_id),
               _sql_literal(plan.power_curve_id), _sql_literal(plan.thrust_curve_id)))
        if not rows:
            return ImportResult(False, ImportCode.VERIFY, "写后校验无返回")
        expected = [1, 1, 1, 1, 1, 1, 4, 2, len(plan.power_blob), len(plan.thrust_blob)]
        got = [int(v) for v in rows[0]]
        if got != expected:
            return ImportResult(False, ImportCode.VERIFY,
                                f"写后校验不一致: got={got} expected={expected}")
    except Exception as exc:
        return ImportResult(False, ImportCode.VERIFY, f"写后校验查询失败: {exc}")

    # blob 逐字节比对（在 SQL 内以二进制字面量等值判定，绕开 sqlcmd 列宽限制）。
    try:
        rows = _query_rows(
            db,
            "SELECT "
            "(SELECT CASE WHEN PersistedValues=%s THEN 1 ELSE 0 END FROM dbo.WTTypeCurve WHERE Id=%s),"
            "(SELECT CASE WHEN PersistedValues=%s THEN 1 ELSE 0 END FROM dbo.WTTypeCurve WHERE Id=%s);"
            % (_sql_hex(plan.power_blob), _sql_literal(plan.power_curve_id),
               _sql_hex(plan.thrust_blob), _sql_literal(plan.thrust_curve_id)))
        got = [int(v) for v in rows[0]] if rows else [0, 0]
        if got != [1, 1]:
            return ImportResult(False, ImportCode.VERIFY,
                                f"blob 写入比对不一致: power={got[0]} thrust={got[1]}")
    except Exception as exc:
        return ImportResult(False, ImportCode.VERIFY, f"blob 写入比对失败: {exc}")
    return ImportResult(True, ImportCode.OK, "写后校验通过",
                        data={"counts": expected})


def apply_plan(db: str, plan: WritePlan, allow_prod: bool = False) -> ImportResult:
    if db in PROD_DB_NAMES and not allow_prod:
        return ImportResult(False, ImportCode.DB_GUARD,
                            f"拒绝写生产库 {db}；请用副本库，或显式 --danger-write-prod")
    ok, out, err = _run_sql(db, plan.sql)
    if not ok:
        return ImportResult(False, ImportCode.DB_WRITE, f"写库失败: {err or out}")
    return _verify(db, plan)


# --- 编排 -------------------------------------------------------------------
def run(spec: TurbineSpec, wtg_path: Path, db: str, apply: bool,
        allow_prod: bool = False, out_dir: Optional[Path] = None,
        golden_power: Optional[Path] = None, golden_thrust: Optional[Path] = None,
        lcid: int = DEFAULT_LOCALE_LCID, air_density: Optional[float] = None) -> ImportResult:
    if apply and db in PROD_DB_NAMES and not allow_prod:
        return ImportResult(False, ImportCode.DB_GUARD,
                            f"拒绝写生产库 {db}；请用副本库，或显式 --danger-write-prod")
    if apply and not apply_ok(db):
        return ImportResult(False, ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")

    issues = spec.validate()
    if issues:
        return ImportResult(False, ImportCode.INPUT, "输入不合法", issues=issues)
    if air_density is not None and float(air_density) <= 0:
        return ImportResult(False, ImportCode.INPUT, "空气密度必须为正数")

    parsed = parse_wtg(Path(wtg_path))
    if not parsed.ok:
        return parsed
    meta = parsed.data["wtg_meta"]
    wtg = WindTurbineFile(
        path=Path(wtg_path),
        description=meta["description"], manufacturer=meta["manufacturer"],
        rotor_diameter=meta["rotor_diameter"], suggested_heights=meta["suggested_heights"],
        air_density=meta["air_density"],
        points=[CurvePoint(**p) for p in parsed.data["points"]])

    derived = derive_design(wtg.points)
    if derived.cut_out_speed <= derived.cut_in_speed:
        return ImportResult(False, ImportCode.INPUT,
                            f"曲线风速区间非法: CutIn={derived.cut_in_speed} >= CutOut={derived.cut_out_speed}")

    work = Path(out_dir or (Path.cwd() / "turbine_import_out"))
    work.mkdir(parents=True, exist_ok=True)

    built = build_blobs(wtg.points, work, lcid=lcid,
                        golden_power=golden_power, golden_thrust=golden_thrust)
    if not built.ok:
        return built

    planned = build_plan(spec, wtg, derived, built.data["power_blob"], built.data["thrust_blob"],
                         db, owner_id=spec.owner_id, air_density=air_density)
    if not planned.ok:
        return planned
    plan: WritePlan = planned.data["plan_obj"]

    (work / "plan.json").write_text(json.dumps({
        "spec": {k: v for k, v in spec.__dict__.items()},
        "wtg": wtg.to_meta(), "derived": derived.to_dict(),
        "air_density": plan.air_density,
        "plan": plan.to_dict(),
        "self_check": built.data.get("kv", {}),
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (work / "apply.sql").write_text(plan.sql, encoding="utf-8")
    (work / "rollback.sql").write_text(plan.rollback_sql, encoding="utf-8")

    if not apply:
        return ImportResult(True, ImportCode.OK, "dry-run 完成（未写库）", data={
            "plan": plan.to_dict(), "derived": derived.to_dict(),
            "self_check": built.data.get("kv", {}), "out_dir": str(work),
        })

    applied = apply_plan(db, plan, allow_prod=allow_prod)
    if not applied.ok:
        return ImportResult(False, applied.code, applied.message, data={
            "plan": plan.to_dict(), "rollback": str(work / "rollback.sql"),
            "out_dir": str(work), "verify": applied.data,
        })
    return ImportResult(True, ImportCode.OK, "写入并校验通过", data={
        "plan": plan.to_dict(), "self_check": built.data.get("kv", {}),
        "verify": applied.data, "out_dir": str(work),
    })


# --- CLI --------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="风机类型导入（模式 B）：.wtg 解析 + blob 重建 + 12 行落库")
    p.add_argument("--wtg", required=True, help="Windographer .wtg 路径（原文不留存）")
    p.add_argument("--name", required=True, help="风机类型名称（如 WT6250D220-Test）")
    p.add_argument("--model", help="TurbineModel（GUI 为用户输入；默认取 .wtg Description 前缀）")
    p.add_argument("--version-name", required=True, help="曲线版本名（WTTypeVersion.Name，如 Cp0.429）")
    p.add_argument("--country")
    p.add_argument("--description")
    p.add_argument("--cost-by-kw", type=float, default=0.0)
    p.add_argument("--currency", default="EUR")
    p.add_argument("--blades", type=int, default=DESIGN_DEFAULTS["number_of_blades"])
    p.add_argument("--power-control", type=int, default=DESIGN_DEFAULTS["power_control"])
    p.add_argument("--cert-norm", type=int, default=CERT_DEFAULTS["certification_norm"],
                   help="认证规范（3=IEC 61400-1 Ed.4，1=Ed.3，2=Ed.3.1）")
    p.add_argument("--wohler-exponent", type=int, default=CERT_DEFAULTS["wohler_exponent"])
    p.add_argument("--speed-class", type=int, default=CERT_DEFAULTS["speed_class"])
    p.add_argument("--turbulence-class", type=int, default=CERT_DEFAULTS["turbulence_class"])
    p.add_argument("--access-level", type=int, default=2, choices=(0, 1, 2),
                   help="0=Public / 1=Group / 2=Private")
    p.add_argument("--air-density", type=float, help="覆盖 .wtg 的 PerformanceTable@AirDensity")
    p.add_argument("--locale-lcid", type=int, default=DEFAULT_LOCALE_LCID,
                   help="blob DataSet.Locale LCID（默认 30724=zh，与 GUI SelectedCulture 一致）")
    p.add_argument("--hub-height", type=float, help="轮毂高度（无库级落点，仅记入 plan.json）")
    p.add_argument("--elevation", type=float, help="海拔（无库级落点，仅记入 plan.json）")
    p.add_argument("--owner-id")
    p.add_argument("--golden-power", help="功率 blob 金样（提供则强制逐字节比对）")
    p.add_argument("--golden-thrust", help="推力 blob 金样（提供则强制逐字节比对）")
    p.add_argument("--db", required=True, help="目标数据库名（默认拒绝 MUPLocalDb）")
    p.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    p.add_argument("--danger-write-prod", action="store_true", help="允许写 MUPLocalDb（需人工授权）")
    p.add_argument("--out-dir")
    p.add_argument("--json", action="store_true", help="输出 JSON")
    return p


def description_prefix(description: str) -> str:
    """'WT6250D220 (6250 kW)' -> 'WT6250D220'（便捷初始值；GUI 中该字段为纯用户输入）。"""
    text = str(description or "").strip()
    for sep in (" (", "（"):
        idx = text.find(sep)
        if idx > 0:
            return text[:idx].strip()
    return text


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    if not args.model:
        parsed = parse_wtg(Path(args.wtg))
        args.model = description_prefix(parsed.data["wtg_meta"]["description"]) if parsed.ok else ""
    spec = TurbineSpec(
        name=args.name, version_name=args.version_name, turbine_model=args.model,
        access_level=args.access_level, owner_id=args.owner_id, country=args.country,
        description=args.description, cost_by_kw=args.cost_by_kw, currency=args.currency,
        number_of_blades=args.blades, power_control=args.power_control,
        cert={
            "certification_norm": args.cert_norm, "wohler_exponent": args.wohler_exponent,
            "speed_class": args.speed_class, "turbulence_class": args.turbulence_class,
        },
        hub_height=args.hub_height, elevation=args.elevation,
    )
    result = run(
        spec, Path(args.wtg), args.db, apply=args.apply, allow_prod=args.danger_write_prod,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        golden_power=Path(args.golden_power) if args.golden_power else None,
        golden_thrust=Path(args.golden_thrust) if args.golden_thrust else None,
        lcid=args.locale_lcid, air_density=args.air_density,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        print(("[OK] " if result.ok else "[FAIL] ") + result.message)
        if result.data.get("plan"):
            plan = result.data["plan"]
            print("  wttype_id=" + plan.get("wttype_id", ""))
            print("  derived=" + json.dumps(plan.get("derived", {}), ensure_ascii=False))
        if result.data.get("verify"):
            print("  verify=" + json.dumps(result.data["verify"].get("counts", [])))
        for issue in result.issues:
            print("  issue: " + issue.code + " " + issue.message)
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
