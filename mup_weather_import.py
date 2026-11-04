# encoding: utf-8
"""气象数据导入（模式 B）：C# 服务算矩阵 + 数据层写入 LocalDB。

职责
----
把「新建气象数据」从 GUI 流程中拆出来，做成可编排、可 dry-run、可回滚的 CLI：

  原始文件（tim / TI / TISD） + 元参数（名称/高度/经纬度/海拔/访问级别）
      -> MUP Climatology C# 服务（外部进程，免 GUI）
      -> 三块矩阵字节（风频 / 湍流 / 湍流标准差）
      -> 8 表子树写入（Climatologies / Heights / Stabilities / Freq / Turb / TISD / TimeSeries / Files）

红线
----
  · 默认 dry-run：只算、只产出 plan.json / *.bin / *.sql，不写库、不写 Vault。
  · 写库必须 --apply，且 --db 必须显式；默认拒绝 MUPLocalDb（含大小写变体），除非 --danger-write-prod。
  · 写文件必须显式 --vault-root（沙盒/副本 vault）：缺省不回退生产 Vault，副本库也不得配对生产 Vault。
  · 不启动 MUP、不触 HASP/GUI；C# 宿主为独立子进程（APPBASE 重定向是进程级副作用）。
  · 单事务 + 写前快照 + 回滚脚本 + 写后校验；同名对象默认拒绝（fail-closed）。

已知环境差异（2026-11-04 用户实测复核）
--------------------------------------
  · TI/TISD -> 矩阵：本机与内网均通过（C1831 17451 B，400x3 与 GUI 金样零差异）。
  · 风频 FillStatisticsFromTimeSeries：内网正常；本机报"内存不足"（本机环境限制，不是算法缺陷）。
    因此风频失败默认只告警（warn），--require-freq 才 fail-closed。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

__all__ = [
    "ImportIssue",
    "ImportResult",
    "MastSpec",
    "SourceFiles",
    "MatrixBlobs",
    "WritePlan",
    "build_plan",
    "compute_matrices",
    "run",
    "main",
]

MUP_DIR_DEFAULT = r"H:\Program Files\Meteodyn\MeteodynUniverse"
SQLCMD_DEFAULT = r"C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\110\Tools\Binn\SQLCMD.EXE"
DEFAULT_DB = "MUPLocalDb"
PROD_DB_NAMES = {"MUPLocalDb"}


def is_prod_db(db: str) -> bool:
    """生产库判定：去空白 + 大小写归一化。

    为什么必须归一化：SQL Server 库名本身不区分大小写，sqlcmd -d muplocaldb
    会直接命中生产库 MUPLocalDb；而 PROD_DB_NAMES 是区分大小写的
    set，直接 db in PROD_DB_NAMES 会让小写变体旁路门禁。
    """
    return str(db or "").strip().lower() in {name.lower() for name in PROD_DB_NAMES}


ACCESS_LEVELS = {"public": 0, "group": 1, "private": 2}
DISC_CLIMATOLOGY = "MTDClimatologyMast"
DISC_HEIGHT = "MTDClimatologyHeight"
DISC_TIMESERIES_FILE = "MTDFileTimeSeriesData"

# --- 错误代数（tagged result，不做静默降级）--------------------------------
class ImportCode:
    OK = "ok"
    INPUT = "input_invalid"
    COMPUTE = "compute_failed"
    FREQ_UNAVAILABLE = "freq_unavailable"
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
            ImportCode.FREQ_UNAVAILABLE: 3,
            ImportCode.DB_GUARD: 4,
            ImportCode.DB_WRITE: 4,
            ImportCode.VERIFY: 5,
            ImportCode.CONFLICT: 6,
            ImportCode.UNSUPPORTED: 2,
        }.get(self.code, 1)


# --- 输入模型 ---------------------------------------------------------------
@dataclass
class MastSpec:
    name: str
    latitude: float
    longitude: float
    elevation: float
    height: float
    access_level: int = 2
    owner_id: Optional[str] = None
    description: str = ""

    def validate(self) -> list[ImportIssue]:
        issues: list[ImportIssue] = []
        if not str(self.name or "").strip():
            issues.append(ImportIssue(ImportCode.INPUT, "名称不能为空"))
        if not (-90.0 <= float(self.latitude) <= 90.0):
            issues.append(ImportIssue(ImportCode.INPUT, "纬度超出 [-90,90]"))
        if not (-180.0 <= float(self.longitude) <= 180.0):
            issues.append(ImportIssue(ImportCode.INPUT, "经度超出 [-180,180]"))
        if float(self.height) <= 0:
            issues.append(ImportIssue(ImportCode.INPUT, "高度必须为正数"))
        if int(self.access_level) not in (0, 1, 2):
            issues.append(ImportIssue(ImportCode.INPUT, "访问级别只能是 0/1/2"))
        return issues


@dataclass
class SourceFiles:
    tim: Optional[Path] = None
    ti: Optional[Path] = None
    tisd: Optional[Path] = None

    def validate(self) -> list[ImportIssue]:
        issues: list[ImportIssue] = []
        if not self.ti and not self.tisd and not self.tim:
            issues.append(ImportIssue(ImportCode.INPUT, "至少提供 tim / TI / TISD 之一"))
        for label, p in (("tim", self.tim), ("TI", self.ti), ("TISD", self.tisd)):
            if p is not None and not Path(p).is_file():
                issues.append(ImportIssue(ImportCode.INPUT, f"{label} 文件不存在: {p}"))
        return issues


@dataclass
class MatrixBlobs:
    freq: Optional[bytes] = None
    percentages: Optional[bytes] = None
    turb: Optional[bytes] = None
    tisd: Optional[bytes] = None
    tim_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_meta(self) -> dict:
        return {
            "freq_len": len(self.freq or b""),
            "percentages_len": len(self.percentages or b""),
            "turb_len": len(self.turb or b""),
            "tisd_len": len(self.tisd or b""),
            "tim_count": self.tim_count,
            "warnings": list(self.warnings),
        }


# --- 工具 -------------------------------------------------------------------
def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _md5_bytes(data: bytes) -> bytes:
    return hashlib.md5(data).digest()


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


def _sql_datetime(value: datetime) -> str:
    return "CONVERT(datetime2, '" + value.strftime("%Y-%m-%dT%H:%M:%S.%f0") + "', 126)"


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


def _prod_vault_root() -> Optional[Path]:
    # 生产 Vault 根（只读解析）：仅用于读取源 .tim 与「副本库不得配对生产 vault」比对。
    try:
        import mup_paths  # 复用既有解析（env -> ini -> 盘符探测）
        base = mup_paths.vault_root(None) or mup_paths.newest_local(mup_paths.data_root())
        return Path(base) if base else None
    except Exception:
        return None


def _resolve_vault_root(explicit: Optional[str]) -> Path:
    # 解析「写入用」vault 根：缺省一律报错，绝不静默回退生产 Vault。
    # 红线：文件只允许复制到显式指定的沙盒/副本 vault。缺省回退生产 vault 会让
    # 副本库写入把测试 .tim 落进生产 Vault，而 rollback.sql 只删库行、不清理文件。
    if not explicit:
        raise RuntimeError("必须显式 --vault-root（沙盒/副本 vault）；缺省不回退生产 Vault")
    root = Path(explicit)
    if not root.is_dir():
        raise RuntimeError(f"vault root 不存在: {root}")
    return root


# --- C# 宿主 ----------------------------------------------------------------
PS_SCRIPT = r'''
$ErrorActionPreference = 'Continue'
$mup = $env:MUP_DIR
$work = $env:WORK_DIR
$out = $env:OUT_DIR
New-Item -ItemType Directory -Force -Path $out | Out-Null
[AppDomain]::CurrentDomain.SetData('APPBASE', ($work.TrimEnd([char]92) + [char]92))
Get-ChildItem $mup -Filter *.dll |
  Where-Object { $_.Name -match '^(MTD|MUP|MBA|DAS|Meteodyn|HPC|EntityFramework)' } |
  ForEach-Object { try { [void][Reflection.Assembly]::LoadFrom($_.FullName) } catch {} }

$lines = New-Object System.Collections.Generic.List[string]
function Emit($k, $v) { $lines.Add([string]$k + "=" + [string]$v) }
function DumpBin($path, $tsv) {
  try {
    $bf = New-Object System.Runtime.Serialization.Formatters.Binary.BinaryFormatter
    $fs = [IO.File]::OpenRead($path); $ds = $bf.Deserialize($fs); $fs.Close()
    $t = $ds.Tables[0]
    $sb = New-Object System.Text.StringBuilder
    foreach ($r in $t.Rows) { [void]$sb.AppendLine($r[0].ToString() + [char]9 + $r[1].ToString() + [char]9 + $r[2].ToString()) }
    [IO.File]::WriteAllText($tsv, $sb.ToString(), [Text.Encoding]::UTF8)
    Emit ($tsv + "_ROWS") $t.Rows.Count
  } catch { Emit ($tsv + "_ERROR") $_.Exception.Message }
}

try {
  $rd = New-Object 'MTD.Climatology.Services.Importer.MTDClimatologyReadFileService'
  $imp = New-Object 'MTD.Climatology.Services.Importer.MTDClimatologyImporterService'
  $mast = New-Object 'MTD.Climatology.Model.MTDClimatologyMast'($env:MAST_NAME, [double]$env:ELEV)
  $height = [double]$env:HEIGHT
  $h = New-Object 'MTD.Climatology.Model.MTDClimatologyHeight'($height)
  [void]$mast.Heights.Add($h)

  if ($env:IN_TI) {
    try {
      $c = $rd.ReadTurbulenceTableFileAsync($env:IN_TI).GetAwaiter().GetResult()
      $m = $imp.GetType().GetMethod('ImportTurbulenceIntensityTableAsync')
      [void]$m.Invoke($imp.PSObject.BaseObject, [object[]]@($mast.PSObject.BaseObject, $c.PSObject.BaseObject, $height)).GetAwaiter().GetResult()
      $p = Join-Path $out 'turb.bin'
      [IO.File]::WriteAllBytes($p, $mast.Heights[0].Turbulence.PersistedTurbulenceMatrix)
      Emit 'TURB_STATUS' 'OK'; Emit 'TURB_LEN' (Get-Item $p).Length
      DumpBin $p (Join-Path $out 'turb_rows.tsv')
    } catch { Emit 'TURB_STATUS' 'FAIL'; Emit 'TURB_ERROR' $_.Exception.Message }
  }

  if ($env:IN_TISD) {
    try {
      $c2 = $rd.ReadStandardDeviationOfTurbulenceTableFileAsync($env:IN_TISD).GetAwaiter().GetResult()
      $m2 = $imp.GetType().GetMethod('ImportTurbulenceStandardDeviationTableAsync')
      [void]$m2.Invoke($imp.PSObject.BaseObject, [object[]]@($mast.PSObject.BaseObject, $height, $c2.PSObject.BaseObject)).GetAwaiter().GetResult()
      $p2 = Join-Path $out 'tisd.bin'
      [IO.File]::WriteAllBytes($p2, $mast.Heights[0].TurbulenceStandardDeviation.PersistedTurbulenceStandardDeviationMatrix)
      Emit 'TISD_STATUS' 'OK'; Emit 'TISD_LEN' (Get-Item $p2).Length
      DumpBin $p2 (Join-Path $out 'tisd_rows.tsv')
    } catch { Emit 'TISD_STATUS' 'FAIL'; Emit 'TISD_ERROR' $_.Exception.Message }
  }

  if ($env:IN_TIM) {
    try {
      $f = New-Object 'MTD.File.Model.Other.MTDFileTimeSeriesData'($env:IN_TIM)
      $rwT = [Type]'MTD.Climatology.File.Services.MTDClimatologyTimeSeriesFileReaderWriter'
      $src = $rwT::ReadTimeSeriesDataFrom($f.PSObject.BaseObject)
      Emit 'TIM_COUNT' $src.Count
      $dates = $src.GetAllStoredDates()
      $open = [Type]'MTDTemplate.MTDTimeSeriesStorage`1'
      $closed = $open.MakeGenericType([Type]'MTD.Climatology.Model.MTDClimatologyTimeSeriesValues')
      $dst = [Activator]::CreateInstance($closed)
      foreach ($d in $dates) {
        $v = $src.GetValue($d)
        [void]$dst.InsertValue($d, (New-Object 'MTD.Climatology.Model.MTDClimatologyTimeSeriesValues'($v)))
      }
      $dictOpen = [Type]'System.Collections.Generic.Dictionary`2'
      $dictT = $dictOpen.MakeGenericType([Type]'System.String', $closed)
      $dict = [Activator]::CreateInstance($dictT)
      $dict.Add([string]$height, $dst)
      $heights = New-Object 'System.Collections.Generic.List[double]'; [void]$heights.Add($height)
      $mi = $imp.GetType().GetMethod('ImportTimeSeriesAsync')
      [void]$mi.Invoke($imp.PSObject.BaseObject, [object[]]@($mast.PSObject.BaseObject, $heights.PSObject.BaseObject, $dict.PSObject.BaseObject)).GetAwaiter().GetResult()
      $svc = New-Object 'MTD.Climatology.Services.MTDClimatologyServices'
      $cand = $svc.GetType().GetMethods() | Where-Object { $_.Name -eq 'FillStatisticsFromTimeSeries' -and $_.GetParameters().Count -eq 6 -and $_.GetParameters()[0].ParameterType.Name -eq 'MTDClimatologyObject' }
      [void]$cand[0].Invoke($svc.PSObject.BaseObject, [object[]]@($mast.PSObject.BaseObject, $height, [int]16, [single]1.0, [single]40.0, [single]1.0))
      if ($mast.Heights[0].HasStatistics) {
        $fc = $mast.Heights[0].Statistics[0].FrequenciesContainer
        $pf = Join-Path $out 'freq.bin'; [IO.File]::WriteAllBytes($pf, $fc.PersistedOccurrencyMatrix)
        $pp = Join-Path $out 'pct.bin'; [IO.File]::WriteAllBytes($pp, $fc.PersistedPercentagesOfWindFrequencyPerDirection)
        Emit 'FREQ_STATUS' 'OK'; Emit 'FREQ_LEN' (Get-Item $pf).Length; Emit 'PCT_LEN' (Get-Item $pp).Length
        DumpBin $pf (Join-Path $out 'freq_rows.tsv')
      } else { Emit 'FREQ_STATUS' 'FAIL'; Emit 'FREQ_ERROR' 'no statistics after FillStatistics' }
    } catch { Emit 'FREQ_STATUS' 'FAIL'; Emit 'FREQ_ERROR' $_.Exception.Message }
  }
  Emit 'STATUS' 'OK'
} catch { Emit 'STATUS' 'FAIL'; Emit 'ERROR' $_.Exception.Message }
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


def compute_matrices(spec: MastSpec, files: SourceFiles, work_dir: Path,
                     mup_dir: str = MUP_DIR_DEFAULT, timeout: int = 600) -> ImportResult:
    """调用 MUP C# 服务，产出三块矩阵字节（纯内存，不落库）。"""
    issues = spec.validate() + files.validate()
    if issues:
        return ImportResult(False, ImportCode.INPUT, "输入不合法", issues=issues)

    work_dir.mkdir(parents=True, exist_ok=True)
    out_dir = work_dir / "artifacts"
    out_dir.mkdir(parents=True, exist_ok=True)
    ps1 = work_dir / "mup_weather_compute.ps1"
    ps1.write_text(PS_SCRIPT, encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "MUP_DIR": mup_dir,
        "WORK_DIR": str(work_dir),
        "OUT_DIR": str(out_dir),
        "MAST_NAME": spec.name,
        "ELEV": str(spec.elevation),
        "HEIGHT": str(spec.height),
        "IN_TIM": str(files.tim or ""),
        "IN_TI": str(files.ti or ""),
        "IN_TISD": str(files.tisd or ""),
    })
    command = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ImportResult(False, ImportCode.COMPUTE, f"C# 宿主执行失败: {exc}")

    kv = _parse_kv(out_dir / "result.txt")
    if kv.get("STATUS") != "OK":
        return ImportResult(
            False, ImportCode.COMPUTE,
            kv.get("ERROR") or "C# 宿主未返回 STATUS=OK",
            data={"returncode": proc.returncode, "stdout": proc.stdout.decode("utf-8", "replace")[-2000:]},
        )

    blobs = MatrixBlobs(tim_count=int(kv.get("TIM_COUNT") or 0))
    for key, name in (("turb.bin", "turb"), ("tisd.bin", "tisd"), ("freq.bin", "freq"), ("pct.bin", "percentages")):
        p = out_dir / key
        if p.is_file() and p.stat().st_size > 0:
            setattr(blobs, name, p.read_bytes())
    if kv.get("FREQ_STATUS") != "OK":
        blobs.warnings.append("freq: " + (kv.get("FREQ_ERROR") or "unavailable"))
    if kv.get("TURB_STATUS") == "FAIL":
        blobs.warnings.append("turb: " + (kv.get("TURB_ERROR") or "failed"))
    if kv.get("TISD_STATUS") == "FAIL":
        blobs.warnings.append("tisd: " + (kv.get("TISD_ERROR") or "failed"))

    data = blobs.to_meta()
    data["artifacts"] = str(out_dir)
    data["kv"] = {k: v for k, v in kv.items() if k.endswith("_STATUS") or k == "TIM_COUNT"}
    return ImportResult(True, ImportCode.OK, "矩阵计算完成", data=data)


# --- 计划与写入 -------------------------------------------------------------
@dataclass
class WritePlan:
    spec: MastSpec
    climatology_id: str
    height_id: str
    stability_id: str
    freq_id: str
    turb_id: str
    tisd_id: str
    ts_id: str
    file_id: str
    blobs: MatrixBlobs
    tim_path: Optional[Path]
    tim_bytes: Optional[bytes]
    owner_id: str
    period_begin: datetime
    period_end: datetime
    sql: str = ""
    rollback_sql: str = ""
    vault_rel_path: str = ""

    def to_dict(self) -> dict:
        return {
            "climatology_id": self.climatology_id,
            "height_id": self.height_id,
            "stability_id": self.stability_id,
            "freq_id": self.freq_id,
            "turb_id": self.turb_id,
            "tisd_id": self.tisd_id,
            "ts_id": self.ts_id,
            "file_id": self.file_id,
            "owner_id": self.owner_id,
            "vault_rel_path": self.vault_rel_path,
            "matrices": self.blobs.to_meta(),
        }


def _parse_tim_span(tim_path: Path) -> tuple[datetime, datetime]:
    """从 MUP 内部 .tim 解析首末时间（ticks -> datetime）。"""
    first = last = None
    with open(tim_path, "r", encoding="latin1", errors="ignore") as fh:
        for line in fh:
            # 记录行形如  *<ticks>;44$..;92$..;   标记可能是 * ( & 等
            match = re.match(r"[^0-9]*(\d+);", line)
            if not match:
                continue
            try:
                ticks = int(match.group(1))
            except ValueError:
                continue
            dt = datetime(1, 1, 1) + _timedelta_from_ticks(ticks)
            if first is None:
                first = dt
            last = dt
    if first is None or last is None:
        raise ValueError("tim 中没有可解析的时间戳")
    return first, last


def _timedelta_from_ticks(ticks: int) -> Any:
    from datetime import timedelta
    return timedelta(microseconds=ticks // 10)


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


def build_plan(spec: MastSpec, files: SourceFiles, blobs: MatrixBlobs, db: str,
               owner_id: Optional[str] = None) -> ImportResult:
    issues = spec.validate()
    if issues:
        return ImportResult(False, ImportCode.INPUT, "输入不合法", issues=issues)
    if not blobs.turb and not blobs.tisd and not blobs.freq:
        return ImportResult(False, ImportCode.COMPUTE, "没有任何矩阵可写")

    try:
        exists = _query_rows(db, f"SELECT TOP 1 Id FROM dbo.Climatologies WHERE Name={_sql_literal(spec.name)};")
    except Exception as exc:
        return ImportResult(False, ImportCode.DB_GUARD, f"无法读取目标库: {exc}")
    if exists:
        return ImportResult(False, ImportCode.CONFLICT, f"同名气象对象已存在: {spec.name}")

    resolved_owner, owner_err = _pick_owner(db, owner_id or spec.owner_id)
    if not resolved_owner:
        return ImportResult(False, ImportCode.DB_GUARD, "无法确定 OwnerId: " + (owner_err or "unknown"))

    if files.tim:
        try:
            begin, end = _parse_tim_span(Path(files.tim))
        except Exception as exc:
            return ImportResult(False, ImportCode.INPUT, f"tim 时间解析失败: {exc}")
        tim_bytes = Path(files.tim).read_bytes()
    else:
        begin = datetime(1900, 1, 1)
        end = datetime(1900, 1, 1)
        tim_bytes = None

    cid = str(uuid.uuid4())
    hid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    fid = str(uuid.uuid4())
    tid = str(uuid.uuid4())
    tsd = str(uuid.uuid4())
    tsid = str(uuid.uuid4())
    fileid = str(uuid.uuid4())
    rel = f"DATAFILES\\TIMESERIES\\{tsid}.tim" if tim_bytes is not None else ""

    stmts: list[str] = []
    stmts.append(
        "INSERT INTO dbo.Climatologies (Id,Name,Summary_Description,Summary_Latitude,Summary_Longitude,"
        "Summary_MeasurementPeriodBeginning,Summary_MeasurementPeriodEnd,GroundElevation,Discriminator,"
        "CreationDate,ModificationDate,CreatorId,OwnerId,AccessLevel) VALUES ("
        f"{_sql_literal(cid)},{_sql_literal(spec.name)},{_sql_literal(spec.description or "")},"
        f"{float(spec.latitude)},{float(spec.longitude)},{_sql_datetime(begin)},{_sql_datetime(end)},"
        f"{float(spec.elevation)},{_sql_literal(DISC_CLIMATOLOGY)},SYSDATETIME(),SYSDATETIME(),"
        f"{_sql_literal(resolved_owner)},{_sql_literal(resolved_owner)},{int(spec.access_level)});"
    )
    stmts.append(
        "INSERT INTO dbo.ClimatologyHeights (Id,Meter,UseCorrectedTimeSeries,Climatology_Id,Discriminator) VALUES ("
        f"{_sql_literal(hid)},{float(spec.height)},0,{_sql_literal(cid)},{_sql_literal(DISC_HEIGHT)});"
    )
    if blobs.freq:
        stmts.append(
            "INSERT INTO dbo.ClimatologyStabilities (Id,Stability,FrequencyPercentage,ClimatologyHeight_Id) VALUES ("
            f"{_sql_literal(sid)},-1,100.0,{_sql_literal(hid)});"
        )
        stmts.append(
            "INSERT INTO dbo.ClimatologyFrequenciesContainers (Id,WindSpeedFactor,DirectionOffset,"
            "PersistedTabFileHeader,PersistedOccurrencyMatrix,ClimatologyStability_Id,"
            "PersistedPercentagesOfWindFrequencyPerDirection) VALUES ("
            f"{_sql_literal(fid)},1.0,0.0,NULL,{_sql_hex(blobs.freq)},{_sql_literal(sid)},"
            f"{_sql_hex(blobs.percentages or b'')});"
        )
    if blobs.turb:
        stmts.append(
            "INSERT INTO dbo.ClimatologyTurbulences (Id,CreatedFromTimeSeries,PersistedTurbulenceMatrix,"
            "PersistedTurbulenceVector,ClimatologyHeight_Id) VALUES ("
            f"{_sql_literal(tid)},0,{_sql_hex(blobs.turb)},NULL,{_sql_literal(hid)});"
        )
    if blobs.tisd:
        stmts.append(
            "INSERT INTO dbo.ClimatologyTurbulenceStandardDeviations (Id,CreatedFromTimeSeries,"
            "PersistedTurbulenceStandardDeviationMatrix,ClimatologyHeight_Id) VALUES ("
            f"{_sql_literal(tsd)},0,{_sql_hex(blobs.tisd)},{_sql_literal(hid)});"
        )
    if tim_bytes is not None:
        stmts.append(
            "INSERT INTO dbo.ClimatologyTimeSeries (Id,ClimatologyHeight_CorrectedTimeSeries_Id,"
            f"ClimatologyHeight_TimeSeries_Id) VALUES ({_sql_literal(tsid)},NULL,{_sql_literal(hid)});"
        )
        stmts.append(
            "INSERT INTO dbo.Files (Id,Path,RemoteStatus,Hash,Size,OfflineTimeStampStr,TimeStampStr,"
            "WktProjection,IsFromPostCFD,Discriminator,CustomLocation_Id,ParentFile_Id,DisplayName,"
            "ClimatologyTimeSeries_Id) VALUES ("
            f"{_sql_literal(fileid)},{_sql_literal(rel)},0,{_sql_hex(_md5_bytes(tim_bytes))},"
            f"{len(tim_bytes)},NULL,NULL,NULL,NULL,{_sql_literal(DISC_TIMESERIES_FILE)},NULL,NULL,"
            f"{_sql_literal(Path(files.tim).name)},{_sql_literal(tsid)});"
        )

    sql = "SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;\n" + "\n".join(stmts) + "\nCOMMIT;\n"
    rollback = (
        "SET NOCOUNT ON; SET XACT_ABORT ON; BEGIN TRANSACTION;\n"
        f"DELETE FROM dbo.Files WHERE Id={_sql_literal(fileid)};\n"
        f"DELETE FROM dbo.ClimatologyTimeSeries WHERE Id={_sql_literal(tsid)};\n"
        f"DELETE FROM dbo.ClimatologyTurbulenceStandardDeviations WHERE Id={_sql_literal(tsd)};\n"
        f"DELETE FROM dbo.ClimatologyTurbulences WHERE Id={_sql_literal(tid)};\n"
        f"DELETE FROM dbo.ClimatologyFrequenciesContainers WHERE Id={_sql_literal(fid)};\n"
        f"DELETE FROM dbo.ClimatologyStabilities WHERE Id={_sql_literal(sid)};\n"
        f"DELETE FROM dbo.ClimatologyHeights WHERE Id={_sql_literal(hid)};\n"
        f"DELETE FROM dbo.Climatologies WHERE Id={_sql_literal(cid)};\nCOMMIT;\n"
    )

    plan = WritePlan(spec, cid, hid, sid, fid, tid, tsd, tsid, fileid, blobs,
                     Path(files.tim) if files.tim else None, tim_bytes, resolved_owner,
                     begin, end, sql, rollback, rel)
    return ImportResult(True, ImportCode.OK, "写入计划已生成",
                        data={"plan": plan.to_dict(), "plan_obj": plan})


def _verify(db: str, plan: WritePlan, vault_root: Optional[Path] = None) -> ImportResult:
    # 写后校验：不只验计数，还要验「子表行真的插进去了」与 vault 物理文件（存在/大小/MD5）。
    lit = _sql_literal
    has_ts = plan.tim_bytes is not None
    want_counts = [
        (1, "climatology", f"SELECT COUNT(*) FROM dbo.Climatologies WHERE Id={lit(plan.climatology_id)}"),
        (1, "height", f"SELECT COUNT(*) FROM dbo.ClimatologyHeights WHERE Id={lit(plan.height_id)}"),
        (1 if plan.blobs.turb else 0, "turbulence",
         f"SELECT COUNT(*) FROM dbo.ClimatologyTurbulences WHERE Id={lit(plan.turb_id)}"),
        (1 if plan.blobs.tisd else 0, "turbulence_std",
         f"SELECT COUNT(*) FROM dbo.ClimatologyTurbulenceStandardDeviations WHERE Id={lit(plan.tisd_id)}"),
        (1 if plan.blobs.freq else 0, "stability",
         f"SELECT COUNT(*) FROM dbo.ClimatologyStabilities WHERE Id={lit(plan.stability_id)}"),
        (1 if plan.blobs.freq else 0, "freq_container",
         f"SELECT COUNT(*) FROM dbo.ClimatologyFrequenciesContainers WHERE Id={lit(plan.freq_id)}"),
        (1 if has_ts else 0, "timeseries",
         f"SELECT COUNT(*) FROM dbo.ClimatologyTimeSeries WHERE Id={lit(plan.ts_id)}"),
        (1 if has_ts else 0, "files",
         f"SELECT COUNT(*) FROM dbo.Files WHERE Id={lit(plan.file_id)} AND ClimatologyTimeSeries_Id={lit(plan.ts_id)}"),
    ]
    selects = ",".join("(" + sql + ")" for _, _, sql in want_counts)
    selects += (
        ",ISNULL((SELECT DATALENGTH(PersistedTurbulenceMatrix) FROM dbo.ClimatologyTurbulences"
        f" WHERE Id={lit(plan.turb_id)}),0)"
        ",ISNULL((SELECT DATALENGTH(PersistedTurbulenceStandardDeviationMatrix)"
        f" FROM dbo.ClimatologyTurbulenceStandardDeviations WHERE Id={lit(plan.tisd_id)}),0)"
        ",ISNULL((SELECT DATALENGTH(PersistedOccurrencyMatrix) FROM dbo.ClimatologyFrequenciesContainers"
        f" WHERE Id={lit(plan.freq_id)}),0)"
    )
    try:
        rows = _query_rows(db, "SELECT " + selects + ";")
    except Exception as exc:
        return ImportResult(False, ImportCode.VERIFY, f"写后校验查询失败: {exc}")
    if not rows:
        return ImportResult(False, ImportCode.VERIFY, "写后校验无返回")
    try:
        vals = [int(v) for v in rows[0]]
    except ValueError:
        return ImportResult(False, ImportCode.VERIFY, f"写后校验返回值非数值: {rows[0]}")
    names = [name for _, name, _ in want_counts]
    got_map = dict(zip(names, vals[:len(names)]))
    want_map = {name: want for want, name, _ in want_counts}
    got_lens = vals[len(names):]
    want_lens = [len(plan.blobs.turb or b""), len(plan.blobs.tisd or b""), len(plan.blobs.freq or b"")]

    bad = [k for k in names if got_map[k] != want_map[k]]
    if bad:
        return ImportResult(
            False, ImportCode.VERIFY,
            "写后校验行数不一致: " + ", ".join(f"{k} got={got_map[k]} want={want_map[k]}" for k in bad),
            data={"counts": got_map, "expected": want_map},
        )
    if got_lens != want_lens:
        return ImportResult(False, ImportCode.VERIFY,
                            f"矩阵长度不一致: got={got_lens} want={want_lens}",
                            data={"counts": got_map})

    vault_note: dict[str, Any] = {"checked": False}
    if plan.tim_bytes is not None and vault_root is not None:
        target = Path(vault_root) / plan.vault_rel_path
        vault_note = {"checked": True, "path": str(target)}
        if not target.is_file():
            return ImportResult(False, ImportCode.VERIFY, f"vault 文件缺失: {target}",
                                data={"counts": got_map, "vault": vault_note})
        size = target.stat().st_size
        digest = hashlib.md5(target.read_bytes()).digest()
        vault_note.update({"size": size, "md5": digest.hex().upper()})
        if size != len(plan.tim_bytes):
            return ImportResult(False, ImportCode.VERIFY,
                                f"vault 文件大小不一致: got={size} want={len(plan.tim_bytes)}",
                                data={"counts": got_map, "vault": vault_note})
        if digest != _md5_bytes(plan.tim_bytes):
            return ImportResult(False, ImportCode.VERIFY, "vault 文件 MD5 与源 .tim 不一致",
                                data={"counts": got_map, "vault": vault_note})

    # 兼容旧调用方：counts 仍按 [climatology, height, turb_len, tisd_len, freq_len] 返回
    legacy = [got_map["climatology"], got_map["height"], got_lens[0], got_lens[1], got_lens[2]]
    return ImportResult(True, ImportCode.OK, "写后校验通过",
                        data={"counts": legacy, "count_map": got_map, "vault": vault_note})


def apply_plan(db: str, plan: WritePlan, vault_root: Optional[Path] = None,
               allow_prod: bool = False, prod_vault_root: Optional[Path] = None) -> ImportResult:
    # 门禁：生产库默认拒绝（大小写归一化，防 muplocaldb 旁路）；副本库必须后缀白名单。
    if is_prod_db(db):
        if not allow_prod:
            return ImportResult(False, ImportCode.DB_GUARD,
                                f"拒绝写生产库 {db}；请用副本库，或显式 --danger-write-prod")
    elif not apply_ok(db):
        return ImportResult(False, ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")

    target: Optional[Path] = None
    if plan.tim_bytes is not None:
        if vault_root is None:
            return ImportResult(False, ImportCode.DB_GUARD,
                                "缺少 vault 根：文件只允许写入显式指定的沙盒/副本 vault")
        if prod_vault_root is None:
            prod_vault_root = _prod_vault_root()
        if not is_prod_db(db) and prod_vault_root and \
                Path(vault_root).resolve() == Path(prod_vault_root).resolve():
            return ImportResult(
                False, ImportCode.DB_GUARD,
                f"副本库 {db} 需配对副本 vault：--vault-root 不得指向生产 vault（{prod_vault_root}）；"
                "文件只允许复制到副本/沙盒 vault")
        target = Path(vault_root) / plan.vault_rel_path
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(plan.tim_bytes)
        except OSError as exc:
            return ImportResult(False, ImportCode.DB_WRITE, f"Vault 写入失败: {exc}")
    ok, out, err = _run_sql(db, plan.sql)
    if not ok:
        if target is not None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        return ImportResult(False, ImportCode.DB_WRITE, f"写库失败: {err or out}")
    return _verify(db, plan, vault_root)


# --- 自检：矩阵 TSV 与源文件逐格比对 ---------------------------------------
def _read_tsv(path: Path) -> list[tuple[str, str, str]]:
    rows = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append((parts[0], parts[1], parts[2]))
    return rows


def _parse_stat_table(path: Path) -> list[tuple[str, str, str]]:
    """解析 TI/TISD 文本：首行仓数，随后每行 'bin v0..v15'。"""
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8-sig", errors="replace").splitlines() if ln.strip()]
    if not lines:
        return []
    rows = []
    for line in lines[1:]:
        parts = line.replace(",", " ").split()
        if len(parts) < 17:
            continue
        speed = parts[0]
        for k, val in enumerate(parts[1:17]):
            rows.append((speed, f"{k * 22.5:g}", val))
    return rows


def self_check(artifacts: Path, files: SourceFiles) -> ImportResult:
    checks = []
    for name, src in (("turb", files.ti), ("tisd", files.tisd)):
        tsv = artifacts / f"{name}_rows.tsv"
        if not src:
            continue
        if not tsv.is_file():
            checks.append({"matrix": name, "match": False, "error": "rows_tsv_missing"})
            continue
        got = _read_tsv(tsv)
        want = _parse_stat_table(Path(src))
        if got == want:
            checks.append({"matrix": name, "rows": len(got), "match": True})
        else:
            first = next((i for i, (a, b) in enumerate(zip(got, want)) if a != b), None)
            checks.append({"matrix": name, "rows": len(got), "expected": len(want), "match": False, "first_diff": first})
    ok = all(c.get("match") for c in checks)
    return ImportResult(ok, ImportCode.OK if ok else ImportCode.VERIFY,
                        "矩阵自检通过" if ok else "矩阵自检不一致", data={"checks": checks})


# --- 编排 -------------------------------------------------------------------
def run(spec: MastSpec, files: SourceFiles, db: str, apply: bool,
        allow_prod: bool = False, out_dir: Optional[Path] = None,
        vault_root: Optional[Path] = None, mup_dir: str = MUP_DIR_DEFAULT,
        require_freq: bool = False, tim_from: Optional[str] = None,
        source_db: str = "MUPLocalDb") -> ImportResult:
    if apply and is_prod_db(db) and not allow_prod:
        return ImportResult(False, ImportCode.DB_GUARD,
                            f"拒绝写生产库 {db}；请用副本库，或显式 --danger-write-prod")
    if apply and not is_prod_db(db) and not apply_ok(db):
        return ImportResult(False, ImportCode.DB_GUARD, f"目标库不在允许列表: {db}")
    if files.tim is None and tim_from:
        try:
            # 源文件永远从真实 Vault 反查；vault_root 只决定写入落点
            src_root = _prod_vault_root()
            if src_root is None:
                return ImportResult(False, ImportCode.DB_GUARD,
                                    "无法定位生产 Vault（只读源）：请检查 mup_paths 解析")
            found = resolve_internal_tim(source_db, tim_from, src_root)
        except Exception as exc:
            return ImportResult(False, ImportCode.DB_GUARD, f"无法按名反查 .tim: {exc}")
        if not found:
            return ImportResult(False, ImportCode.INPUT, f"库内找不到 {tim_from} 的内部 .tim")
        files = SourceFiles(tim=found, ti=files.ti, tisd=files.tisd)
    if files.tim:
        head = Path(files.tim).read_bytes()[:32]
        if b"VERSION2.0" not in head:
            return ImportResult(
                False, ImportCode.INPUT,
                "--tim 必须是 MUP 内部 .tim（首行 VERSION2.0）；"
                "Windographer *-tim.txt 请先在 GUI 导入，或改传 Vault 内 DATAFILES/TIMESERIES/*.tim",
            )

    # 写库前先把「写入用 vault 根」定死：缺省不回退生产 Vault（P1-A 红线），
    # 并在算矩阵之前 fail-closed，避免白跑 C# 宿主。
    write_root: Optional[Path] = Path(vault_root) if vault_root else None
    if apply and files.tim is not None:
        try:
            write_root = _resolve_vault_root(str(vault_root) if vault_root else None)
        except RuntimeError as exc:
            return ImportResult(False, ImportCode.DB_GUARD, str(exc))

    work = Path(out_dir or (Path.cwd() / "weather_import_out"))
    work.mkdir(parents=True, exist_ok=True)

    computed = compute_matrices(spec, files, work, mup_dir=mup_dir)
    if not computed.ok:
        return computed
    blobs = MatrixBlobs(
        freq=_read_optional(work / "artifacts" / "freq.bin"),
        percentages=_read_optional(work / "artifacts" / "pct.bin"),
        turb=_read_optional(work / "artifacts" / "turb.bin"),
        tisd=_read_optional(work / "artifacts" / "tisd.bin"),
        tim_count=int(computed.data.get("tim_count") or 0),
        warnings=list(computed.data.get("warnings") or []),
    )
    check = self_check(work / "artifacts", files)

    planned = build_plan(spec, files, blobs, db, owner_id=spec.owner_id)
    if not planned.ok:
        return planned
    plan = planned.data["plan_obj"]

    (work / "plan.json").write_text(json.dumps({
        "spec": spec.__dict__, "files": {k: str(v) if v else None for k, v in files.__dict__.items()},
        "plan": plan.to_dict(), "self_check": check.data,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    (work / "apply.sql").write_text(plan.sql, encoding="utf-8")
    (work / "rollback.sql").write_text(plan.rollback_sql, encoding="utf-8")

    if require_freq and not blobs.freq:
        return ImportResult(False, ImportCode.FREQ_UNAVAILABLE, "缺少风频矩阵（--require-freq）",
                            data={"warnings": blobs.warnings})
    if not check.ok:
        return ImportResult(False, ImportCode.VERIFY, "矩阵自检未通过，拒绝继续",
                            data={"checks": check.data.get("checks")})

    if not apply:
        return ImportResult(True, ImportCode.OK, "dry-run 完成（未写库）", data={
            "plan": plan.to_dict(), "self_check": check.data, "out_dir": str(work),
            "warnings": blobs.warnings,
        })

    prod_root = _prod_vault_root()
    applied = apply_plan(db, plan, write_root, allow_prod=allow_prod, prod_vault_root=prod_root)
    if not applied.ok:
        return ImportResult(False, applied.code, applied.message, data={
            "plan": plan.to_dict(), "rollback": str(work / "rollback.sql"), "out_dir": str(work),
        })
    vault_file = str(write_root / plan.vault_rel_path) if (write_root and plan.vault_rel_path) else None
    return ImportResult(True, ImportCode.OK, "写入并校验通过", data={
        "plan": plan.to_dict(), "self_check": check.data, "verify": applied.data,
        "out_dir": str(work), "vault_file": vault_file,
    })


def apply_ok(db: str) -> bool:
    """写库白名单：必须以 _verify / _test / _sandbox 结尾（大小写不敏感）。"""
    name = str(db or "").strip().lower()
    return name.endswith(("_verify", "_test", "_sandbox"))


def resolve_internal_tim(db: str, climatology_name: str, vault_root: Path) -> Optional[Path]:
    """按气象对象名，从库内 Files 反查其内部 .tim 的 Vault 绝对路径。"""
    rows = _query_rows(
        db,
        "SELECT TOP 1 f.Path FROM dbo.Climatologies c "
        "JOIN dbo.ClimatologyHeights h ON h.Climatology_Id=c.Id "
        "JOIN dbo.ClimatologyTimeSeries ts ON ts.ClimatologyHeight_TimeSeries_Id=h.Id "
        "JOIN dbo.Files f ON f.ClimatologyTimeSeries_Id=ts.Id "
        f"WHERE c.Name={_sql_literal(climatology_name)};"
    )
    if not rows or not rows[0]:
        return None
    rel = rows[0][0].replace("\\", os.sep)
    candidate = vault_root / rel
    return candidate if candidate.is_file() else None


def _read_optional(path: Path) -> Optional[bytes]:
    if path.is_file() and path.stat().st_size > 0:
        return path.read_bytes()
    return None


# --- CLI --------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="气象数据导入（模式 B）：C# 服务算矩阵 + 数据层写入")
    p.add_argument("--name", required=True, help="气象对象名称（如 副本）")
    p.add_argument("--tim", help="MUP 内部 .tim（VERSION2.0）")
    p.add_argument("--tim-from", help="按库内气象对象名反查其内部 .tim")
    p.add_argument("--source-db", default="MUPLocalDb", help="--tim-from 的源库（只读）")
    p.add_argument("--ti", help="湍流强度表 TI.txt")
    p.add_argument("--tisd", help="湍流标准差表 TISD.txt")
    p.add_argument("--height", type=float, required=True, help="测量高度（米）")
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--elev", type=float, default=0.0)
    p.add_argument("--access-level", default="private", choices=sorted(ACCESS_LEVELS))
    p.add_argument("--owner-id")
    p.add_argument("--db", required=True, help="目标数据库名（默认拒绝 MUPLocalDb）")
    p.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    p.add_argument("--danger-write-prod", action="store_true", help="允许写 MUPLocalDb（需人工授权）")
    p.add_argument("--vault-root")
    p.add_argument("--out-dir")
    p.add_argument("--require-freq", action="store_true", help="缺风频矩阵时 fail-closed")
    p.add_argument("--mup-dir", default=MUP_DIR_DEFAULT)
    p.add_argument("--json", action="store_true", help="输出 JSON")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    spec = MastSpec(
        name=args.name, latitude=args.lat, longitude=args.lon, elevation=args.elev,
        height=args.height, access_level=ACCESS_LEVELS[args.access_level], owner_id=args.owner_id,
    )
    files = SourceFiles(
        tim=Path(args.tim) if args.tim else None,
        ti=Path(args.ti) if args.ti else None,
        tisd=Path(args.tisd) if args.tisd else None,
    )
    result = run(
        spec, files, args.db, apply=args.apply, allow_prod=args.danger_write_prod,
        out_dir=Path(args.out_dir) if args.out_dir else None,
        vault_root=Path(args.vault_root) if args.vault_root else None,
        mup_dir=args.mup_dir, require_freq=args.require_freq,
        tim_from=args.tim_from, source_db=args.source_db,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(("[OK] " if result.ok else "[FAIL] ") + result.message)
        if result.data.get("plan"):
            plan = result.data["plan"]
            print("  climatology_id=" + plan.get("climatology_id", ""))
            print("  vault_rel=" + plan.get("vault_rel_path", ""))
        for w in result.data.get("warnings") or []:
            print("  warn: " + w)
        for issue in result.issues:
            print("  issue: " + issue.code + " " + issue.message)
    return result.exit_code()


if __name__ == "__main__":
    raise SystemExit(main())
