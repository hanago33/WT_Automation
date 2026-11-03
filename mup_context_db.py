"""Build the P1 Excel ``ExportContext`` from MUP LocalDB.

The .wrp does not carry project-level metadata (site name, projection, turbine
model/version, IEC certification, coordinates in lon/lat).  This module reads the
read-only LocalDB and emits the same JSON accepted by
``mup_excel_export_p1.ExportContext.load``.

Only fields present in LocalDB are invented from DB rows; everything else is
either a documented default or must be supplied through ``overrides``.  Missing
values are listed in ``missing`` instead of being silently guessed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["ContextDBError", "ContextBuild", "build_context", "inverse_transverse_mercator"]

DEFAULT_SQLCMD = r"C:\Program Files\Microsoft SQL Server\Client SDK\ODBC\110\Tools\Binn\SQLCMD.EXE"
DEFAULT_DATABASE = os.environ.get("MUP_LOCALDB_SERVER", r"(localdb)\Meteodyn")
DEFAULT_DB_NAME = os.environ.get("MUP_LOCALDB_NAME", "MUPLocalDb")
# WTTypeCertification.CertificationNorm -> IEC workbook edition.
IEC_EDITIONS = {1: "2.0", 2: "3.1", 3: "4.0"}


class ContextDBError(Exception):
    """LocalDB read failed or returned an unusable row."""


@dataclass
class ContextBuild:
    data: dict[str, Any]
    missing: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"context": self.data, "missing": self.missing, "diagnostics": self.diagnostics}


def _sqlcmd() -> str:
    configured = os.environ.get("MUP_SQLCMD")
    if configured and Path(configured).is_file():
        return configured
    found = shutil.which("sqlcmd")
    if found:
        return found
    if Path(DEFAULT_SQLCMD).is_file():
        return DEFAULT_SQLCMD
    raise ContextDBError("sqlcmd not found; set MUP_SQLCMD")


def _quote(value: str) -> str:
    return str(value).replace("'", "''")


def _query(sql: str, database: str = DEFAULT_DB_NAME) -> list[list[str]]:
    command = [
        _sqlcmd(),
        "-S", DEFAULT_DATABASE,
        "-d", database,
        "-h", "-1",
        "-W",
        "-s", "|",
        "-Q", "SET NOCOUNT ON; " + sql,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContextDBError(f"sqlcmd failed: {exc}") from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("gb18030", errors="replace").strip()
        raise ContextDBError(f"sqlcmd returned {completed.returncode}: {stderr}")
    text = completed.stdout.decode("gb18030", errors="replace")
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        rows.append([part.strip() for part in line.split("|")])
    return rows


def _float(value: str | None) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def inverse_transverse_mercator(
    x: float,
    y: float,
    central_meridian: float,
    false_easting: float,
    semi_major: float,
    inverse_flattening: float,
    scale_factor: float = 1.0,
) -> tuple[float, float]:
    """Inverse Transverse Mercator (Snyder), accurate to <0.1 m for this use."""

    f = 1.0 / inverse_flattening
    e2 = f * (2.0 - f)
    ep2 = e2 / (1.0 - e2)
    x -= false_easting
    m = y / scale_factor
    mu = m / (semi_major * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (
        mu
        + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
        + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
        + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
        + (1097 * e1 ** 4 / 512) * math.sin(8 * mu)
    )
    c1 = ep2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = semi_major / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    r1 = semi_major * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * scale_factor)
    phi = phi1 - (n1 * math.tan(phi1) / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720
    )
    lam = math.radians(central_meridian) + (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
    ) / math.cos(phi1)
    return math.degrees(lam), math.degrees(phi)


def _projection_parameters(wkt: str) -> dict[str, float] | None:
    if "Transverse_Mercator" not in wkt:
        return None
    def number(name: str) -> float | None:
        match = re.search(rf'PARAMETER\["{name}",\s*([-+0-9.eE]+)\]', wkt)
        return float(match.group(1)) if match else None

    spheroid = re.search(r'SPHEROID\["[^"]+",\s*([0-9.]+),\s*([0-9.]+)', wkt)
    if not spheroid:
        return None
    return {
        "central_meridian": number("central_meridian") or 0.0,
        "false_easting": number("false_easting") or 0.0,
        "scale_factor": number("scale_factor") or 1.0,
        "semi_major": float(spheroid.group(1)),
        "inverse_flattening": float(spheroid.group(2)),
    }


def _projection_name(wkt: str) -> str | None:
    match = re.match(r'\s*PROJCS\["([^"]+)"', wkt)
    return match.group(1) if match else None


def _iec_class(speed_class: str, turbulence_class: str) -> str | None:
    speed = {"1": "A", "2": "B", "3": "C"}.get(str(speed_class))
    turbulence = {"1": "I", "2": "II", "3": "III"}.get(str(turbulence_class))
    if speed and turbulence:
        return turbulence + speed
    return None


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def build_context(
    case: str,
    synthesis: int,
    overrides: dict[str, Any] | None = None,
    database: str = DEFAULT_DB_NAME,
) -> ContextBuild:
    case = str(case).strip()
    synthesis = int(synthesis)
    missing: list[str] = []

    site_rows = _query(
        f"""
        SELECT s.Id, s.Name, ISNULL(s.Description, ''), s.Projection,
               w.Id, w.SynthesisNumber, c.Id
        FROM dbo.WRAAnalysis w
        JOIN dbo.ComputationObject co ON co.Id = w.ComputationObject_Id
        JOIN dbo.Cases c ON c.Id = co.Parent_Id
        JOIN dbo.Sites s ON s.Id = c.Parent_Id
        WHERE w.SynthesisNumber = {synthesis}
          AND (c.Id = '{_quote(case)}' OR s.Name = '{_quote(case)}')
        """,
        database,
    )
    if not site_rows:
        raise ContextDBError(f"no LocalDB case/site for case={case!r} synthesis={synthesis}")
    site_id, site_name, site_description, wkt, wra_id = (
        site_rows[0][0], site_rows[0][1], site_rows[0][2], site_rows[0][3], site_rows[0][4]
    )

    element_rows = _query(
        f"""
        SELECT e.Id, e.Label, e.Discriminator, e.Point_X, e.Point_Y, e.Point_HZ
        FROM dbo.WRAAnalysisElements wae
        JOIN dbo.Elements e ON e.Id = wae.Element_Id
        WHERE wae.WRAAnalysis_Id = '{_quote(wra_id)}'
        ORDER BY e.Label
        """,
        database,
    )
    if not element_rows:
        raise ContextDBError(f"no elements for site {site_name!r}")

    type_rows = _query(
        f"""
        SELECT e.Label, ISNULL(t.Name, ''), ISNULL(t.Version, ''),
               t.TurbulenceIntensity, ISNULL(t.Certification_Id, '')
        FROM dbo.WRAAnalysisElements wae
        JOIN dbo.Elements e ON e.Id = wae.Element_Id
        LEFT JOIN dbo.WRAWTType t ON t.Id = wae.WindTurbineType_Id
        WHERE wae.WRAAnalysis_Id = '{_quote(wra_id)}'
        """,
        database,
    )
    types = {row[0]: row for row in type_rows if len(row) >= 5}

    certification_id = next(
        (row[4] for row in types.values() if len(row) >= 5 and row[4]),
        "",
    )
    cert_rows = (
        _query(
            f"""
            SELECT CertificationNorm, WohlerExponent, SpeedClass, TurbulenceClass,
                   Iref, Vref, MaximumWindShearExponent, MinimumWindShearExponent,
                   MaximumInflowAngle, MaximumAirDensity
            FROM dbo.WTTypeCertification
            WHERE Id = '{_quote(certification_id)}'
            """,
            database,
        )
        if certification_id else []
    )
    cert = cert_rows[0] if cert_rows else []

    projection = _projection_name(wkt) or wkt or None
    parameters = _projection_parameters(wkt or "")
    if parameters is None:
        missing.append("projection: unsupported or missing Transverse_Mercator WKT")

    turbines: dict[str, Any] = {}
    mast_label = None
    mast_height = None
    for element in element_rows:
        if len(element) < 6:
            continue
        _id, label, discriminator, px, py, pz = element[:6]
        if "Mast" in discriminator:
            mast_label = label
            mast_height = _float(pz)
            longitude = latitude = None
            if parameters is not None:
                x, y = _float(px), _float(py)
                if x is not None and y is not None:
                    longitude, latitude = inverse_transverse_mercator(
                        x,
                        y,
                        parameters["central_meridian"],
                        parameters["false_easting"],
                        parameters["semi_major"],
                        parameters["inverse_flattening"],
                        parameters["scale_factor"],
                    )
                    longitude = round(longitude, 6)
                    latitude = round(latitude, 6)
            turbines[label] = {"longitude": longitude, "latitude": latitude}
            continue
        if "WindTurbine" not in discriminator:
            continue
        type_row = types.get(label, ["", "", "", "", ""])
        longitude = latitude = None
        if parameters is not None:
            x, y = _float(px), _float(py)
            if x is not None and y is not None:
                longitude, latitude = inverse_transverse_mercator(
                    x,
                    y,
                    parameters["central_meridian"],
                    parameters["false_easting"],
                    parameters["semi_major"],
                    parameters["inverse_flattening"],
                    parameters["scale_factor"],
                )
                longitude = round(longitude, 6)
                latitude = round(latitude, 6)
        if longitude is None:
            missing.append(f"turbines.{label}.longitude")
            missing.append(f"turbines.{label}.latitude")
        trust = "默认 (all=1)"
        turbines[label] = {
            "model": type_row[1] or None,
            "version": type_row[2] or None,
            "turbulence_class": "未指定" if (_float(type_row[3]) or 0) < 0 else str(type_row[3] or ""),
            "power_curve_ref": "轮毂高度",
            "longitude": longitude,
            "latitude": latitude,
            "trust": trust,
        }

    speed_class = cert[2] if len(cert) > 2 else ""
    turbulence_class = cert[3] if len(cert) > 3 else ""
    edition = IEC_EDITIONS.get(int(_float(cert[0]) or 0)) if cert else None
    iec_class = _iec_class(speed_class, turbulence_class)
    iref = _float(cert[4]) if len(cert) > 4 else None
    wohler = _float(cert[1]) if len(cert) > 1 else None
    max_inflow = _float(cert[8]) if len(cert) > 8 else None
    max_density = _float(cert[9]) if len(cert) > 9 else None
    if not iec_class:
        missing.append("iec.iec_class")
    if iref is None:
        missing.append("iec.iref")
    if wohler is None:
        missing.append("iec.wohler")
    if edition is None:
        missing.append("iec.edition")

    data: dict[str, Any] = {
        "case_name": site_name,
        "synthesis": synthesis,
        "description": site_description or "-",
        "projection": projection,
        "reference_density": 1.225,
        "reference_density_at": "0 m",
        "weighting": "距离平方",
        "met_mast_label": mast_label,
        "met_mast_type": "测风塔" if mast_label else None,
        "met_mast_height": f"{mast_height:g} m" if mast_height is not None else None,
        "wake_enabled": True,
        "turbines": turbines,
        "iec": {
            "iec_class": iec_class,
            "iref": iref,
            "wohler": wohler,
            "reference_mast": "默认",
            "edition": edition,
            "cct_class": "H",
            "air_density": 1.225,
            "min_shear": 0.05,
            "max_shear": 0.25,
            "maximum_inflow_angle": max_inflow,
            "maximum_air_density": max_density,
            "ed3_min_shear": _float(cert[7]) if len(cert) > 7 else None,
            "ed3_max_shear": _float(cert[6]) if len(cert) > 6 else None,
            "norm": int(round(_float(cert[0]) or 0)) if cert else None,
            "speed_min": 2,
            "speed_max": 23,
        },
    }
    if overrides:
        data = _deep_merge(data, overrides)

    diagnostics = {
        "site_id": site_id,
        "site_name": site_name,
        "wra_id": wra_id,
        "certification_id": certification_id or None,
        "turbines": len(turbines),
        "mast": mast_label,
        "projection_supported": parameters is not None,
    }
    return ContextBuild(data=data, missing=missing, diagnostics=diagnostics)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build P1 Excel context from MUP LocalDB")
    parser.add_argument("--case", required=True, help="CaseGUID or site name")
    parser.add_argument("--synthesis", type=int, required=True)
    parser.add_argument("--overrides", help="JSON with fields LocalDB cannot supply")
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    overrides = json.loads(Path(args.overrides).read_text(encoding="utf-8")) if args.overrides else None
    try:
        build = build_context(args.case, args.synthesis, overrides)
    except ContextDBError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2
    Path(args.out).write_text(json.dumps(build.data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] wrote {args.out}")
    if build.missing:
        print("missing: " + ", ".join(build.missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
