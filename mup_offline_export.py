# encoding: utf-8
"""离线导出：从 MUP Vault 的 ``.wrp`` 直接产出交付文件，不启动 MUP、不点 GUI。

产出五件套（文件名与 MUP 基准命名一致，供下游程序按原路径解析）::

    {name}_WRA{n}_Frequency_Matrix.txt                  风频矩阵（16 方向 x 59 仓）
    {name}_WRA{n}_TurbulenceIntensityTotal_Matrix.txt   湍流矩阵（4 块 x 17 列 x 59 仓）
    {name}_WRA{n}_Production_Matrix.txt                 发电量矩阵（IL 复刻布局）
    {name}_S{n}.xlsx                                    综合总表（平均 + 16 方向）
    {name}_S{n}_IEC.xlsx                                IEC 总结

三份 txt 采用 MUP 原样字节格式：UTF-8 BOM + LF；频率与湍流矩阵已对真实金样逐字节相等。
工作簿的 sheet 名、行列位置、单元格类型与 MUP 导出相同（金样本身无样式，故数据等效即格式等效）。

除五件套外写 ``{name}_offline_manifest.json``：源 ``.wrp`` 与每个产物的 sha256 / 大小 /
门禁结论，便于交付核对与复现。

红线：只读 Vault 与 LocalDB，不写 Vault，不调用 MUP，不触 HASP/licence，不跑 HPC。

用法::

    python mup_offline_export.py --case <CaseGUID或站点名> --synthesis 2 --out-dir 04-WT输出
    python mup_offline_export.py --wrp <x.wrp> --name "DEMO (1)" --synthesis 2 --out-dir out \
        --context ctx.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import mup_excel_export_p1 as excel
import mup_matrix_expander as expander

__all__ = [
    "OfflineExportError",
    "OfflineExportResult",
    "build_output_names",
    "is_case_guid",
    "resolve_case_prefix",
    "list_vault_cases",
    "run_flow",
    "export_case",
    "main",
    "write_mup_text",
]

MUP_TEXT_ENCODING = "utf-8-sig"  # MUP 写出带 BOM
MANIFEST_SUFFIX = "_offline_manifest.json"
PRODUCTION_KIND = "with_wake"


class OfflineExportError(Exception):
    """离线导出无法安全完成（缺源、格式漂移、目标已存在等）。"""


# --------------------------------------------------------------------------
# 输出命名（与 MUP 基准命名一致）
# --------------------------------------------------------------------------


def build_output_names(name: str, synthesis: int) -> dict[str, str]:
    """返回五件套 + manifest 的文件名，规则与 MUP 导出对照。"""

    token = f"WRA{synthesis}"
    return {
        "frequency": f"{name}_{token}_Frequency_Matrix.txt",
        "turbulence": f"{name}_{token}_TurbulenceIntensityTotal_Matrix.txt",
        "production": f"{name}_{token}_Production_Matrix.txt",
        "synthesis": f"{name}_S{synthesis}.xlsx",
        "iec": f"{name}_S{synthesis}_IEC.xlsx",
        "manifest": f"{name}{MANIFEST_SUFFIX}",
    }


# --------------------------------------------------------------------------
# 原子写：MUP 文本格式（BOM + LF）
# --------------------------------------------------------------------------


def write_mup_text(path: str | Path, text: str) -> Path:
    """按 MUP 原样格式落盘：UTF-8 BOM、LF 换行、临时文件 + 原子替换。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = text.encode("utf-8") if text.startswith("\ufeff") else ("\ufeff" + text).encode("utf-8")
    if b"\r\n" in payload:
        raise OfflineExportError(f"{target}: payload must use LF line endings")
    handle, tmp = tempfile.mkstemp(prefix=".mup_offline_", dir=str(target.parent))
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# 结果模型
# --------------------------------------------------------------------------


@dataclass
class OfflineExportResult:
    case: str
    synthesis: int
    name: str
    source_wrp: Path
    output_dir: Path
    files: dict[str, Path]
    manifest_path: Path
    gate: dict[str, Any]
    labels: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case,
            "synthesis": self.synthesis,
            "name": self.name,
            "source_wrp": str(self.source_wrp),
            "output_dir": str(self.output_dir),
            "files": {key: str(value) for key, value in self.files.items()},
            "manifest": str(self.manifest_path),
            "gate": self.gate,
            "labels": self.labels,
            "warnings": self.warnings,
        }


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def export_case(
    out_dir: str | Path,
    *,
    case: str | None = None,
    synthesis: int | None = None,
    wrp_path: str | Path | None = None,
    name: str | None = None,
    context: excel.ExportContext | str | Path | None = None,
    sent_at: Any = None,
    labels: Sequence[str] | None = None,
    production_kind: str = PRODUCTION_KIND,
    include_excel: bool = True,
    overwrite: bool = False,
    vault: str | Path | None = None,
) -> OfflineExportResult:
    """把一份 ``.wrp`` 导出为 MUP 基准格式的五件套并写 manifest。"""

    if wrp_path is None:
        if case is None:
            raise OfflineExportError("provide either wrp_path or case")
        wrp_path = expander.discover_wrp(case=case, synthesis=synthesis, vault=vault)
    source = Path(wrp_path)
    if not source.is_file():
        raise OfflineExportError(f"{source}: .wrp does not exist")

    if synthesis is None:
        synthesis = _synthesis_from_name(source.name)
    if name is None:
        name = _derive_name(case, source)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    names = build_output_names(name, synthesis)
    targets = {key: out / value for key, value in names.items() if key != "manifest"}
    if not overwrite:
        existing = [str(path) for path in targets.values() if path.exists()]
        if existing:
            raise OfflineExportError(
                "refusing to overwrite existing outputs (pass overwrite=True): "
                + ", ".join(existing[:3])
            )

    wrp = expander.open_wrp(source)
    report = expander.verify_artifact(
        source,
        case=case if case and _looks_like_guid(case) else None,
        synthesis=synthesis,
        sent_at=sent_at,
    )
    gate_dict = report.to_dict()
    if not report.identity_ok:
        raise OfflineExportError(
            "identity gate failed: " + "; ".join(
                reason for reason in report.reasons if reason.startswith("identity")
            )
        )
    if not report.format_ok:
        raise OfflineExportError(
            "format gate failed: " + "; ".join(
                reason for reason in report.reasons if reason.startswith("format")
            )
        )
    if report.freshness_checked and not report.freshness_ok:
        raise OfflineExportError(
            "freshness gate failed: " + "; ".join(
                reason for reason in report.reasons if reason.startswith("freshness")
            )
        )

    turbine_labels = list(labels) if labels else wrp.wind_turbine_labels()
    if not turbine_labels:
        raise OfflineExportError(f"{source}: no wind-turbine labels to export")
    # MUP writes blocks in natural label order (W1, W2, ...), not WRP storage order.
    render_labels = list(labels) if labels else None

    warnings: list[str] = []
    if not report.freshness_checked:
        warnings.append("freshness gate not evaluated (no sent_at supplied)")

    written: dict[str, Path] = {}

    frequency = wrp.render_gui_text(render_labels)
    written["frequency"] = write_mup_text(targets["frequency"], frequency)

    turbulence = wrp.render_turbulence_gui_text(render_labels)
    written["turbulence"] = write_mup_text(targets["turbulence"], turbulence)

    production = wrp.render_production_gui_text(render_labels, kind=production_kind)
    written["production"] = write_mup_text(targets["production"], production)

    context_missing: list[str] = []
    if include_excel:
        ctx = _resolve_context(context, synthesis=synthesis, name=name, source=source)
        context_missing = list(getattr(ctx, "missing", []) or [])
        paths = excel.export_case(wrp, ctx, out, f"{name}_S{synthesis}")
        written["synthesis"] = paths["synthesis"]
        written["iec"] = paths["iec"]

    manifest = {
        "generator": "mup_offline_export",
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "case": case,
        "synthesis": synthesis,
        "name": name,
        "production_kind": production_kind,
        "source": {
            "path": str(source),
            "size": source.stat().st_size,
            "mtime": source.stat().st_mtime,
            "sha256": _sha256(source),
        },
        "gate": gate_dict,
        "context_missing": context_missing,
        "labels": ([str(item) for item in render_labels] if render_labels else sorted(turbine_labels, key=expander._natural_key)),
        "outputs": [
            {
                "role": role,
                "path": str(path),
                "name": path.name,
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for role, path in written.items()
        ],
        "warnings": warnings,
    }
    manifest_path = out / names["manifest"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    written["manifest"] = manifest_path

    return OfflineExportResult(
        case=case or source.name,
        synthesis=synthesis,
        name=name,
        source_wrp=source,
        output_dir=out,
        files=written,
        manifest_path=manifest_path,
        gate=gate_dict,
        labels=turbine_labels,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------


def run_flow(flow_path: str | Path, **kwargs: Any) -> OfflineExportResult:
    """Execute the declarative offline step flow (``flow_definition_离线导出计算结果.json``).

    The flow lists one step per delivered artifact; every enabled step must produce
    its ``stepParams.role`` or the run fails closed.
    """

    path = Path(flow_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise OfflineExportError(f"cannot read flow {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise OfflineExportError(f"{path}: invalid JSON ({exc})") from exc
    if not isinstance(payload, dict):
        raise OfflineExportError(f"{path}: flow definition must be an object")
    runtime = payload.get("runtimeConfig") or {}
    if str(runtime.get("mode") or "").strip().lower() != "offline":
        raise OfflineExportError(
            f"{path}: runtimeConfig.mode must be 'offline' for the offline runner"
        )

    steps = [
        step
        for step in payload.get("steps") or []
        if isinstance(step, dict) and step.get("enabled", True)
    ]
    if not steps:
        raise OfflineExportError(f"{path}: flow has no enabled steps")

    roles: list[str] = []
    for step in steps:
        params = step.get("stepParams") or {}
        role = str(params.get("role") or "").strip()
        if role and role != "source":
            roles.append(role)

    result = export_case(**kwargs)
    missing = [role for role in roles if role not in result.files]
    if missing:
        raise OfflineExportError(
            f"{path}: steps did not produce {sorted(set(missing))}"
        )
    return result


def is_case_guid(text: str) -> bool:
    """True when *text* is a 36-character Case GUID (Vault 产物前缀)."""

    return _looks_like_guid(text)


def _looks_like_guid(text: str) -> bool:
    import re

    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", str(text).strip()))


def _synthesis_from_name(filename: str) -> int:
    import re

    match = re.search(r"_synthesis_(\d+)\.wrp$", filename, flags=re.IGNORECASE)
    if not match:
        raise OfflineExportError(
            f"{filename}: cannot derive synthesis number from the .wrp name; pass synthesis"
        )
    return int(match.group(1))


def list_vault_cases(vault_root: str | Path | None = None) -> list[dict[str, Any]]:
    """列出 Vault 里所有带综合产物的 Case（按最近修改倒序）。

    返回 ``[{case, syntheses, wrp_count, size, latest_mtime, path}]``；
    只读目录，不查库、不碰 MUP。
    """

    import re

    import mup_vault_export

    root = Path(vault_root) if vault_root else Path(mup_vault_export.resolve_vault_root())
    synthesis_dir = root / "SYNTHESIS_FILES"
    cases: dict[str, dict[str, Any]] = {}
    try:
        entries = list(synthesis_dir.iterdir())
    except OSError as exc:
        raise OfflineExportError(f"cannot scan {synthesis_dir}: {exc}") from exc

    for entry in entries:
        match = re.match(
            r"^(?P<case>[0-9a-fA-F-]{36})_synthesis_(?P<n>\d+)\.wrp$",
            entry.name,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        case = match.group("case").lower()
        try:
            stat = entry.stat()
        except OSError:
            continue
        item = cases.setdefault(
            case,
            {"case": case, "syntheses": [], "wrp_count": 0, "size": 0,
             "latest_mtime": 0.0, "path": str(entry.parent)},
        )
        item["syntheses"].append(int(match.group("n")))
        item["wrp_count"] += 1
        item["size"] += stat.st_size
        item["latest_mtime"] = max(item["latest_mtime"], stat.st_mtime)

    result = sorted(cases.values(), key=lambda item: item["latest_mtime"], reverse=True)
    for item in result:
        item["syntheses"].sort()
    return result


def resolve_case_prefix(
    site: str,
    vault_root: str | Path | None = None,
    allow_unique_fallback: bool = False,
) -> tuple[str, str]:
    """站点名 → Vault Case GUID，返回 ``(guid, 说明)``；解析不到时 guid 为空。

    工程目录名常带 MUP 复制后缀（``DEMO (1)``），LocalDB 里只有 ``DEMO``，
    因此按二级回退：原名 → 去掉 ``(n)`` 后缀。

    ``allow_unique_fallback=True`` 时才接受"Vault 里只有一个 Case"的兜底；
    默认关闭，避免在只开了一个项目时把别的项目当成目标悄悄导出。
    """

    import re

    import mup_vault_export

    text = str(site or "").strip()
    if not text:
        return "", "empty site name"
    if _looks_like_guid(text):
        return text, "already a CaseGUID"

    for candidate in (text, re.sub(r"\s*[(（]\d+[)）]\s*$", "", text)):
        if not candidate:
            continue
        diagnostics: dict[str, Any] = {}
        try:
            prefix = mup_vault_export.resolve_site_case_id(
                candidate, vault_root, diagnostics=diagnostics
            )
        except Exception as exc:  # noqa: BLE001 — 解析失败按未命中处理
            prefix = ""
            diagnostics["reason"] = str(exc)
        if prefix and _looks_like_guid(prefix):
            return prefix, f"resolved from {candidate!r}"

    if not allow_unique_fallback:
        return "", f"no LocalDB case matched {text!r}"

    cases = list_vault_cases(vault_root)
    if len(cases) == 1:
        return cases[0]["case"], f"unique CaseGUID in Vault ({cases[0]['case']})"
    if not cases:
        return "", "no synthesis .wrp in Vault"
    return "", "ambiguous: {} cases in Vault, pick one explicitly".format(len(cases))


def _derive_name(case: str | None, source: Path) -> str:
    """默认输出名前缀：优先用 context/case 的可读名，退回 CaseGUID。"""

    if case:
        text = str(case).strip()
        if not _looks_like_guid(text):
            return text
        return text
    return source.name.split("_synthesis_", 1)[0]


def _resolve_context(
    context: excel.ExportContext | str | Path | None,
    *,
    synthesis: int,
    name: str,
    source: Path,
) -> excel.ExportContext:
    if isinstance(context, excel.ExportContext):
        ctx = context
    else:
        ctx = excel.ExportContext.load(context)
    if ctx.synthesis is None:
        ctx.synthesis = synthesis
    if not ctx.case_name:
        ctx.case_name = name
    return ctx


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export MUP synthesis results offline in MUP's baseline format.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--case", help="CaseGUID or site label used to discover the .wrp")
    parser.add_argument("--synthesis", type=int, help="synthesis number (e.g. 2)")
    parser.add_argument("--wrp", help="explicit .wrp path (skips discovery)")
    parser.add_argument("--vault", help="vault root override")
    parser.add_argument("--out-dir", help="output directory (required unless --list-cases)")
    parser.add_argument("--name", help="output name prefix (default: case label or CaseGUID)")
    parser.add_argument("--context", help="export context JSON (metadata for the workbooks)")
    parser.add_argument("--sent-at", help="send timestamp for the freshness gate (ISO-8601 or epoch)")
    parser.add_argument("--label", action="append", help="limit export to these labels (repeatable)")
    parser.add_argument(
        "--production-kind",
        default=PRODUCTION_KIND,
        choices=sorted(expander.PRODUCTION_KINDS),
        help="production matrix data origin",
    )
    parser.add_argument("--skip-excel", action="store_true", help="only write the three .txt files")
    parser.add_argument("--overwrite", action="store_true", help="allow replacing existing outputs")
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="list synthesis cases present in the Vault and exit (identity check)",
    )
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.list_cases:
        try:
            cases = list_vault_cases(args.vault)
        except OfflineExportError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        if not cases:
            print("(no synthesis .wrp found in the Vault)")
            return 1
        for index, item in enumerate(cases, start=1):
            stamp = datetime.fromtimestamp(item["latest_mtime"]).strftime("%Y-%m-%d %H:%M")
            print("{}. {}  syntheses={}  wrp={}  size={}  modified={}".format(
                index, item["case"], item["syntheses"], item["wrp_count"],
                item["size"], stamp))
        return 0

    if not args.out_dir:
        parser.error("--out-dir is required unless --list-cases is used")
    if not args.wrp and not args.case:
        parser.error("one of --wrp or --case is required")

    try:
        result = export_case(
            args.out_dir,
            case=args.case,
            synthesis=args.synthesis,
            wrp_path=args.wrp,
            name=args.name,
            context=args.context,
            sent_at=args.sent_at,
            labels=args.label,
            production_kind=args.production_kind,
            include_excel=not args.skip_excel,
            overwrite=args.overwrite,
            vault=args.vault,
        )
    except (OfflineExportError, expander.MatrixExpandError, excel.ExportError) as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(f"source : {result.source_wrp}")
    print(f"case   : {result.case}  synthesis={result.synthesis}  labels={len(result.labels)}")
    for role, path in result.files.items():
        print(f"  {role:11s} {path.name}  ({path.stat().st_size} bytes)")
    print(f"manifest: {result.manifest_path}")
    for warning in result.warnings:
        print(f"[WARN] {warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
