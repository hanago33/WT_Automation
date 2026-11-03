"""P1 data-equivalent Excel synthesis for WRA synthesis results.

This is deliberately *not* a pixel-perfect clone of MUP's Excel COM writer.
It writes the same sheets / headers / numeric values so the output can be
validated cell-by-cell against a real MUP workbook.

Current scope (P1):
  * S2 workbook: average table + 16 direction sheets.
  * IEC workbook: four numeric load sheets (the Ed.4 summary is handled by
    ``mup_excel_export_p1.py`` with an explicit project context).
  * Metadata columns are injected; DB context resolution remains a separate
    integration step.

No Excel installation is required; openpyxl writes the workbook directly.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import openpyxl
from openpyxl import Workbook

from mup_matrix_expander import (
    DATA_SENSE,
    MatrixExpandError,
    WrpFile,
    _natural_key,
    _round,
    open_wrp,
)

__all__ = [
    "DIRECTION_HEADERS",
    "SiteMetadata",
    "build_direction_rows",
    "build_direction_workbook",
    "read_metadata_from_xlsx",
    "reconcile_direction_workbook",
    "build_iec_workbook",
    "reconcile_iec_workbook",
]

DIRECTION_HEADERS: tuple[str, ...] = (
    "风机类型",
    "版本",
    "湍流强度等级",
    "功率曲线对应风速",
    "标签",
    "X (m)",
    "Y (m)",
    "Z (m)",
    "H (m)",
    "经度",
    "纬度",
    "信任系数",
    "扇区（度）",
    "风向偏转（度）",
    "频率（%）",
    "韦布尔参数A （米/秒）",
    "韦布尔参数 k  (-)",
    "能量密度（瓦特/平方米）",
    "年发电量 (兆瓦时/年)",
    "平均风速(米/秒)",
    "平均 REWS (米/秒)",
    "考虑尾流效应的发电量（兆瓦时/年）",
    "考虑尾流效应的平均风速（米/秒）",
    "考虑尾流的平均 REWS (米/秒)",
    "考虑限功率的年发电量 (兆瓦时/年)",
    "考虑尾流和限功率的年发电量(兆瓦时/年)",
    "考虑尾流和限功率的平均风速 (m/s)",
    "强风状态下的环境湍流强度",
    "强风状态下的总体湍流强度",
    "入流角(度)",
    "风切变指数",
    "风加速因数",
    "垂直方向风向偏转（度）",
)


@dataclass
class SiteMetadata:
    """Column metadata that is not stored in the .wrp."""

    type_name: str = ""
    version: str = ""
    ti_class: str = ""
    power_curve: str = ""
    longitude: float | None = None
    latitude: float | None = None
    confidence: str = "-"

    def to_cells(self) -> list[Any]:
        return [
            self.type_name,
            self.version,
            self.ti_class,
            self.power_curve,
            None,  # label
            None,  # X
            None,  # Y
            None,  # Z
            None,  # H
            self.longitude,
            self.latitude,
            self.confidence,
        ]


def _int_or_float(value: float) -> int | float:
    return int(value) if abs(value - round(value)) < 1e-9 else value


def _cell(value: float | None, digits: int) -> Any:
    if value is None:
        return "-"
    return _int_or_float(_round(value, digits))


def _ordered_labels(wrp: WrpFile) -> list[str]:
    turbine_flags = {
        label: bool(int(wrp.values(DATA_SENSE["is_wind_turbine"])[index]))
        for index, label in enumerate(wrp.labels)
    }
    return sorted(
        wrp.labels,
        key=lambda label: (turbine_flags.get(label, True), _natural_key(label)),
    )


def _optional(wrp: WrpFile, sense: int, label: str, direction: int, speed: float) -> float | None:
    try:
        return float(wrp.values(sense, direction, speed)[wrp.label_index(label)])
    except (MatrixExpandError, ValueError, IndexError):
        return None


def build_direction_rows(
    wrp: WrpFile,
    angle: float,
    metadata: dict[str, SiteMetadata] | None = None,
) -> list[list[Any]]:
    """Build the rows of one direction sheet (header + point rows)."""

    metadata = metadata or {}
    rows: list[list[Any]] = [[None] * len(DIRECTION_HEADERS), list(DIRECTION_HEADERS)]
    for label in _ordered_labels(wrp):
        index = wrp.label_index(label)
        meta = metadata.get(label, SiteMetadata())
        is_turbine = bool(int(wrp.values(DATA_SENSE["is_wind_turbine"])[index]))

        x = float(wrp.values(DATA_SENSE["coordinate_x"])[index])
        y = float(wrp.values(DATA_SENSE["coordinate_y"])[index])
        z = _optional(wrp, DATA_SENSE["coordinate_z"], label, -1, -1.0)
        h = float(wrp.values(DATA_SENSE["coordinate_h"])[index])

        def value(sense: int, digits: int) -> Any:
            raw = _optional(wrp, sense, label, angle, -1.0)
            return _cell(raw, digits)

        frequency = _optional(wrp, DATA_SENSE["frequency"], label, angle, -1.0)
        frequency_pct = _cell(frequency * 100.0 if frequency is not None else None, 0)

        row: list[Any] = [
            meta.type_name if is_turbine else "",
            meta.version if is_turbine else "",
            meta.ti_class if is_turbine else "",
            meta.power_curve if is_turbine else "",
            label,
            _int_or_float(x),
            _int_or_float(y),
            _cell(z, 1),
            _int_or_float(h),
            meta.longitude,
            meta.latitude,
            meta.confidence if is_turbine else "-",
            _int_or_float(angle),
            value(DATA_SENSE["deviation"], 1),
            frequency_pct,
            value(DATA_SENSE["weibull_a"], 2),
            value(DATA_SENSE["weibull_k"], 3),
            value(DATA_SENSE["power_density"], 1),
            value(DATA_SENSE["production"], 2) if is_turbine else "-",
            value(DATA_SENSE["wind_speed"], 2),
            "-",  # REWS is not enabled in the DEMO export
            value(DATA_SENSE["wake_production"], 2) if is_turbine else "-",
            value(DATA_SENSE["wake_wind_speed"], 2) if is_turbine else value(DATA_SENSE["wind_speed"], 2),
            "-",  # wake REWS
            value(DATA_SENSE["curtailed_production"], 2) if is_turbine else "-",
            value(DATA_SENSE["curtailed_wake_production"], 2) if is_turbine else "-",
            value(DATA_SENSE["curtailed_wake_wind_speed"], 2)
            if is_turbine
            else value(DATA_SENSE["wind_speed"], 2),
            value(DATA_SENSE["turbulence_ambient_strong_wind"], 3),
            value(DATA_SENSE["turbulence_total_strong_wind"], 3),
            value(DATA_SENSE["inflow_angle"], 1),
            value(DATA_SENSE["shear"], 2),
            value(DATA_SENSE["speed_up_factor"], 3),
            value(DATA_SENSE["veer"], 3) if is_turbine else "-",
        ]
        rows.append(row)
    return rows


def _sheet_name(angle: float) -> str:
    return f"{angle:g}"




AVERAGE_HEADERS: tuple[str, ...] = (
    "风机类型",
    "版本",
    "湍流强度等级",
    "功率曲线对应风速",
    "标签",
    "X (m)",
    "Y (m)",
    "Z (m)",
    "H (m)",
    "经度",
    "纬度",
    "信任系数",
    "韦布尔参数A (米/秒)",
    "韦布尔参数 k  (-)",
    "能量密度(瓦特/平方米)",
    "年发电量 (兆瓦时/年)",
    "平均风速(米/秒)",
    "平均 REWS (米/秒)",
    "考虑尾流效应的发电量(兆瓦时/年)",
    "考虑尾流效应的平均风速（米/秒）",
    "考虑尾流的平均 REWS (米/秒)",
    "考虑限功率的年发电量 (兆瓦时/年)",
    "考虑尾流和限功率的年发电量(兆瓦时/年)",
    "考虑尾流和限功率的平均风速 (m/s)",
    "容量系数 (-) ",
    "强风状态下的平均环境湍流强度",
    "强风状态下的平均总体湍流强度",
    "50年回归的10分钟平均极风速（米/秒）",
    "50年回归的阵风极风速（米/秒）",
    "尾流效应导致的平均折减率(%)",
    "该点的空气密度(千克/立方米)",
    "平均风切变指数",
    "最大风切变指数",
    "最大风切变指数对应方向扇区(度)",
    "绝对值平均入流角(度)",
    "最大入流角（度）",
    "出现最大入流角的风向扇区 （度）",
    "最近相邻风机的标签",
    "相邻风机的最近距离（米）",
    "叶轮直径（米）",
    "以叶轮直径为单位的相邻风机最近距离（D）",
    "最近相邻风机的方位角（度）",
    "扇区数量",
    "平均垂直方向风向偏转（度）",
    "最大垂直方向风向偏转（度）",
    "出现最大垂直方向风向偏转的扇区（度）",
)


def _nearest(wrp: WrpFile, turbines: list[str], label: str):
    """Resolve MUP's precomputed ClosestWindTurbine UID (sense 62)."""

    index = wrp.label_index(label)
    if not bool(int(wrp.values(DATA_SENSE["is_wind_turbine"])[index])):
        return None, None, None
    closest = _optional(wrp, DATA_SENSE["closest_wind_turbine"], label, -1, -1.0)
    if closest is None or closest <= 0:
        return None, None, None
    target_uid = int(round(closest))
    x = float(wrp.values(DATA_SENSE["coordinate_x"])[index])
    y = float(wrp.values(DATA_SENSE["coordinate_y"])[index])
    for other in wrp.labels:
        if other == label:
            continue
        other_index = wrp.label_index(other)
        if not bool(int(wrp.values(DATA_SENSE["is_wind_turbine"])[other_index])):
            continue
        uid = _optional(wrp, DATA_SENSE["uid"], other, -1, -1.0)
        if uid is None or int(round(uid)) != target_uid:
            continue
        dx = float(wrp.values(DATA_SENSE["coordinate_x"])[other_index]) - x
        dy = float(wrp.values(DATA_SENSE["coordinate_y"])[other_index]) - y
        return other, math.hypot(dx, dy), (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
    return None, None, None


def build_average_sheet_rows(
    wrp: WrpFile,
    metadata: dict[str, SiteMetadata] | None = None,
) -> list[list[Any]]:
    """Build the average table (rows 35-46); top metadata block is P2."""

    metadata = metadata or {}
    rows: list[list[Any]] = [[None] * 46 for _ in range(46)]
    labels = _ordered_labels(wrp)
    turbines = [
        label for label in labels
        if bool(int(wrp.values(DATA_SENSE["is_wind_turbine"])[wrp.label_index(label)]))
    ]

    total_production = sum(
        _round(float(wrp.values(DATA_SENSE["production"], -1, -1.0)[wrp.label_index(label)]), 2)
        for label in turbines
    )
    total_wake = sum(
        _round(float(wrp.values(DATA_SENSE["wake_production"], -1, -1.0)[wrp.label_index(label)]), 2)
        for label in turbines
    )
    mast = next((label for label in labels if label not in turbines), None)
    mast_speed = (
        float(wrp.values(DATA_SENSE["wind_speed"], -1, -1.0)[wrp.label_index(mast)])
        if mast else 0.0
    )

    rows[34][0] = "不考虑尾流和限功率的风电场总发电量： "
    rows[34][5] = f"{total_production:.2f} 兆瓦时/年"
    rows[35][0] = "考虑尾流效应的风电场总发电量：  "
    rows[35][5] = f"{total_wake:.2f} 兆瓦时/年"
    rows[36][0] = "参考点处的平均风速： "
    rows[36][5] = f"{mast_speed:.2f} 米/秒"
    rows[38] = list(AVERAGE_HEADERS)

    for row_index, label in enumerate(labels, start=39):
        index = wrp.label_index(label)
        meta = metadata.get(label, SiteMetadata())
        is_turbine = bool(int(wrp.values(DATA_SENSE["is_wind_turbine"])[index]))

        def value(sense: int, digits: int, direction: int = -1, speed: float = -1.0):
            return _cell(_optional(wrp, sense, label, direction, speed), digits)

        nearest_label, distance, azimuth = _nearest(wrp, turbines, label) if is_turbine else (None, None, None)
        rotor = _optional(wrp, DATA_SENSE["rotor_radius"], label, -1, -1.0)
        diameter = rotor * 2.0 if rotor is not None else None

        shear_values = [
            (direction * wrp.direction_step,
             _optional(wrp, DATA_SENSE["shear"], label, direction * wrp.direction_step, -1.0))
            for direction in range(wrp.nb_directions)
        ]
        shear_values = [(angle, val) for angle, val in shear_values if val is not None]
        max_shear_angle, max_shear = max(shear_values, key=lambda item: item[1]) if shear_values else (None, None)

        inflow_values = [
            (direction * wrp.direction_step,
             _optional(wrp, DATA_SENSE["inflow_angle"], label, direction * wrp.direction_step, -1.0))
            for direction in range(wrp.nb_directions)
        ]
        inflow_values = [(angle, val) for angle, val in inflow_values if val is not None]
        max_inflow_angle, max_inflow = max(inflow_values, key=lambda item: item[1]) if inflow_values else (None, None)

        rows[row_index] = [
            meta.type_name if is_turbine else "",
            meta.version if is_turbine else "",
            meta.ti_class if is_turbine else "",
            meta.power_curve if is_turbine else "",
            label,
            _int_or_float(float(wrp.values(DATA_SENSE["coordinate_x"])[index])),
            _int_or_float(float(wrp.values(DATA_SENSE["coordinate_y"])[index])),
            value(DATA_SENSE["coordinate_z"], 1),
            _int_or_float(float(wrp.values(DATA_SENSE["coordinate_h"])[index])),
            meta.longitude,
            meta.latitude,
            meta.confidence if is_turbine else "-",
            value(DATA_SENSE["weibull_a"], 2),
            value(DATA_SENSE["weibull_k"], 3),
            value(DATA_SENSE["power_density"], 1),
            value(DATA_SENSE["production"], 2) if is_turbine else "-",
            value(DATA_SENSE["wind_speed"], 2),
            "-",
            value(DATA_SENSE["wake_production"], 2) if is_turbine else "-",
            value(DATA_SENSE["wake_wind_speed"], 2) if is_turbine else value(DATA_SENSE["wind_speed"], 2),
            "-",
            value(DATA_SENSE["curtailed_production"], 2) if is_turbine else "-",
            value(DATA_SENSE["curtailed_wake_production"], 2) if is_turbine else "-",
            value(DATA_SENSE["curtailed_wake_wind_speed"], 2) if is_turbine else value(DATA_SENSE["wind_speed"], 2),
            value(DATA_SENSE["capacity_factor"], 3) if is_turbine else "-",
            value(DATA_SENSE["turbulence_ambient_strong_wind"], 3),
            value(DATA_SENSE["turbulence_total_strong_wind"], 3),
            "-",
            "-",
            value(DATA_SENSE["lack_coef"], 1) if is_turbine else "-",
            value(DATA_SENSE["air_density"], 3),
            value(DATA_SENSE["shear"], 2),
            _cell(max_shear, 2) if max_shear is not None else "-",
            int(max_shear_angle) if max_shear_angle is not None else "-",
            value(DATA_SENSE["inflow_angle"], 1),
            _cell(max_inflow, 1) if max_inflow is not None else "-",
            int(max_inflow_angle) if max_inflow_angle is not None else "-",
            nearest_label if nearest_label else "-",
            _int_or_float(round(distance, 0)) if distance is not None else "-",
            _int_or_float(diameter) if diameter is not None and is_turbine else "-",
            _int_or_float(round(distance / diameter, 0)) if distance is not None and diameter and is_turbine else "-",
            _int_or_float(round(azimuth, 0)) if azimuth is not None else "-",
            wrp.nb_directions,
            value(DATA_SENSE["veer"], 3) if is_turbine else "-",
            value(119, 3) if is_turbine else "-",
            int(value(120, 1)) if is_turbine else "-",
        ]
    return rows


def build_direction_workbook(
    wrp: WrpFile,
    output: str | Path,
    metadata: dict[str, SiteMetadata] | None = None,
) -> Path:
    """Write the average table plus the 16 direction sheets (data-only P1)."""

    output = Path(output)
    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)

    summary = workbook.create_sheet("平均")
    for row in build_average_sheet_rows(wrp, metadata):
        summary.append(row)

    for index in range(wrp.nb_directions):
        angle = index * (360.0 / wrp.nb_directions)
        sheet = workbook.create_sheet(_sheet_name(angle))
        rows = build_direction_rows(wrp, angle, metadata)
        for row in rows:
            sheet.append(row)

    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def read_metadata_from_xlsx(
    path: str | Path,
    sheet_name: str = "0",
) -> dict[str, SiteMetadata]:
    """Bootstrap metadata from a real MUP workbook (validation only).

    Production should fill this from the MUP DB; this helper lets the P1
    reconciler isolate the WRP-derived columns from DB-derived columns.
    """

    path = Path(path)
    workbook = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        sheet = workbook[sheet_name]
        metadata: dict[str, SiteMetadata] = {}
        for row in sheet.iter_rows(min_row=3, values_only=True):
            label = row[4]
            if not label:
                continue
            metadata[str(label)] = SiteMetadata(
                type_name=str(row[0] or ""),
                version=str(row[1] or ""),
                ti_class=str(row[2] or ""),
                power_curve=str(row[3] or ""),
                longitude=row[9],
                latitude=row[10],
                confidence=str(row[11] or "-"),
            )
        return metadata
    finally:
        workbook.close()


@dataclass
class SheetDiff:
    sheet: str
    cells: int
    diffs: list[tuple[str, Any, Any]]
    max_numeric_diff: float


@dataclass
class WorkbookDiff:
    sheets: list[SheetDiff]

    @property
    def cells(self) -> int:
        return sum(sheet.cells for sheet in self.sheets)

    @property
    def diffs(self) -> int:
        return sum(len(sheet.diffs) for sheet in self.sheets)

    @property
    def max_numeric_diff(self) -> float:
        return max((sheet.max_numeric_diff for sheet in self.sheets), default=0.0)

    @property
    def passed(self) -> bool:
        return self.diffs == 0


def _normalise(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return value


def _values_equal(left: Any, right: Any, tolerance: float) -> bool:
    left = _normalise(left)
    right = _normalise(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return abs(float(left) - float(right)) <= tolerance
    return str(left).strip() == str(right).strip()


def reconcile_direction_workbook(
    produced: str | Path,
    reference: str | Path,
    tolerance: float = 1e-9,
) -> WorkbookDiff:
    """Compare every cell of every direction sheet against a MUP workbook."""

    produced_book = openpyxl.load_workbook(produced, data_only=True, read_only=True)
    reference_book = openpyxl.load_workbook(reference, data_only=True, read_only=True)
    try:
        sheets: list[SheetDiff] = []
        for sheet_name in reference_book.sheetnames:
            if sheet_name == "平均":
                continue
            if sheet_name not in produced_book.sheetnames:
                sheets.append(SheetDiff(sheet_name, 0, [("sheet", "missing", "present")], 0.0))
                continue
            left = produced_book[sheet_name]
            right = reference_book[sheet_name]
            diffs: list[tuple[str, Any, Any]] = []
            cells = 0
            max_diff = 0.0
            rows = max(left.max_row, right.max_row)
            columns = max(left.max_column, right.max_column)
            for row in range(1, rows + 1):
                for column in range(1, columns + 1):
                    cells += 1
                    produced_value = left.cell(row, column).value
                    reference_value = right.cell(row, column).value
                    if not _values_equal(produced_value, reference_value, tolerance):
                        diffs.append(
                            (
                                f"{openpyxl.utils.get_column_letter(column)}{row}",
                                produced_value,
                                reference_value,
                            )
                        )
                    elif isinstance(produced_value, (int, float)) and isinstance(reference_value, (int, float)):
                        max_diff = max(max_diff, abs(float(produced_value) - float(reference_value)))
            sheets.append(SheetDiff(sheet_name, cells, diffs, max_diff))
        return WorkbookDiff(sheets=sheets)
    finally:
        produced_book.close()
        reference_book.close()




IEC_SHEETS: dict[str, tuple[int, float]] = {
    "IEC4.0版疲劳载荷湍流强度": (DATA_SENSE["turbulence_effective"], 1.0),
    "IEC4.0版极限载荷湍流强度": (DATA_SENSE["turbulence_representative"], 1.0),
    "IEC4.0版尾迹中心最大湍流": (DATA_SENSE["turbulence_max_wake_direction_iec"], 1.0),
    "IEC4.0版极端湍流": (DATA_SENSE["turbulence_extreme_ambient_1etm"], 1.0),
}
IEC_SPEEDS = tuple(range(2, 24))


def build_iec_workbook(wrp: WrpFile, output: str | Path) -> Path:
    """Write the four IEC numeric sheets (P1); the Ed.4 summary stays P2."""

    labels = [label for label in _ordered_labels(wrp) if bool(int(
        wrp.values(DATA_SENSE["is_wind_turbine"])[wrp.label_index(label)]
    ))]
    workbook = Workbook()
    default = workbook.active
    workbook.remove(default)

    for sheet_name, (sense, factor) in IEC_SHEETS.items():
        sheet = workbook.create_sheet(sheet_name)
        sheet.append(["风速（米/秒）"] + labels)
        for speed in IEC_SPEEDS:
            row = [speed]
            for label in labels:
                index = wrp.label_index(label)
                raw = float(wrp.values(sense, -1, float(speed))[index])
                # Sigma1 sheets (wake-centre / extreme) store TI * wind speed.
                if sense in (DATA_SENSE["turbulence_max_wake_direction_iec"],
                             DATA_SENSE["turbulence_extreme_ambient_1etm"]):
                    raw *= speed
                # The IEC workbook uses Math.Round on the already-computed value.
                row.append(round(raw * factor, 3))
            sheet.append(row)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    return output


def reconcile_iec_workbook(
    produced: str | Path,
    reference: str | Path,
    tolerance: float = 1e-9,
) -> WorkbookDiff:
    produced_book = openpyxl.load_workbook(produced, data_only=True, read_only=True)
    reference_book = openpyxl.load_workbook(reference, data_only=True, read_only=True)
    try:
        sheets: list[SheetDiff] = []
        for sheet_name in IEC_SHEETS:
            if sheet_name not in reference_book.sheetnames:
                continue
            left = produced_book[sheet_name]
            right = reference_book[sheet_name]
            diffs: list[tuple[str, Any, Any]] = []
            cells = 0
            max_diff = 0.0
            for row in range(1, 24):
                for column in range(1, 8):
                    cells += 1
                    produced_value = left.cell(row, column).value
                    reference_value = right.cell(row, column).value
                    if not _values_equal(produced_value, reference_value, tolerance):
                        diffs.append(
                            (
                                f"{openpyxl.utils.get_column_letter(column)}{row}",
                                produced_value,
                                reference_value,
                            )
                        )
                    elif isinstance(produced_value, (int, float)) and isinstance(reference_value, (int, float)):
                        max_diff = max(max_diff, abs(float(produced_value) - float(reference_value)))
            sheets.append(SheetDiff(sheet_name, cells, diffs, max_diff))
        return WorkbookDiff(sheets=sheets)
    finally:
        produced_book.close()
        reference_book.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P1 data-equivalent WRA Excel export")
    parser.add_argument("--wrp", required=True)
    parser.add_argument("--out", help="S2 workbook output path")
    parser.add_argument("--iec-out", help="IEC workbook output path")
    parser.add_argument("--iec-reconcile", help="reference MUP IEC workbook")
    parser.add_argument("--metadata-json", help="JSON map label -> metadata fields")
    parser.add_argument("--metadata-from-xlsx", help="bootstrap metadata from a real MUP workbook")
    parser.add_argument("--reconcile", help="reference MUP workbook")
    parser.add_argument("--tolerance", type=float, default=1e-9)
    args = parser.parse_args(argv)
    if not args.out and not args.iec_out:
        parser.error("one of --out or --iec-out is required")

    try:
        wrp = open_wrp(args.wrp)
    except MatrixExpandError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    metadata: dict[str, SiteMetadata] = {}
    if args.metadata_from_xlsx:
        metadata = read_metadata_from_xlsx(args.metadata_from_xlsx)
    if args.metadata_json:
        raw = json.loads(Path(args.metadata_json).read_text(encoding="utf-8"))
        for label, values in raw.items():
            metadata[label] = SiteMetadata(**values)

    status = 0
    if args.out:
        try:
            output = build_direction_workbook(wrp, args.out, metadata)
        except (OSError, ValueError) as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        print(f"[OK] wrote {output}")

        if args.reconcile:
            diff = reconcile_direction_workbook(output, args.reconcile, tolerance=args.tolerance)
            print(
                f"sheets={len(diff.sheets)} cells={diff.cells} diffs={diff.diffs} "
                f"max_numeric_diff={diff.max_numeric_diff:g}"
            )
            for sheet in diff.sheets:
                if sheet.diffs:
                    print(f"  {sheet.sheet}: {len(sheet.diffs)} diffs; first={sheet.diffs[:3]}")
            if not diff.passed:
                status = 3
        else:
            return 0

    if args.iec_out:
        try:
            iec_output = build_iec_workbook(wrp, args.iec_out)
        except (OSError, ValueError) as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        print(f"[OK] wrote {iec_output}")
        if args.iec_reconcile:
            diff = reconcile_iec_workbook(iec_output, args.iec_reconcile, tolerance=args.tolerance)
            print(
                f"IEC sheets={len(diff.sheets)} cells={diff.cells} diffs={diff.diffs} "
                f"max_numeric_diff={diff.max_numeric_diff:g}"
            )
            for sheet in diff.sheets:
                if sheet.diffs:
                    print(f"  {sheet.sheet}: {len(sheet.diffs)} diffs; first={sheet.diffs[:3]}")
            if not diff.passed:
                status = 3
    return status


if __name__ == "__main__":
    raise SystemExit(main())
