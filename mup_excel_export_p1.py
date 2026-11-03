"""MUP Excel 数据等效导出（P1）。

从 MUP 综合计算结果 ``.wrp`` 离线合成两个交付工作簿，全部为数据层读取，零 Excel COM、
零许可调用、零 HPC：

* **17-sheet 综合总表**（平均 + 16 个方向）：sheet 名 ``平均`` / ``0`` / ``22.5`` ...
  ``337.5``；``平均`` 为 39 行元信息 + 表头 + 机位行，方向 sheet 为 33 列机位表。
* **IEC 工作簿**：Ed.4 为 4 张载荷表 + 双段 ``风机 - Ed. 4``；
  Ed.3.1 为 2 张载荷表 + 28 列单平铺 ``风机 - Ed. 3.1``（按 ``DataSense 10`` 分发）。

列语义来自对 ``DEMO (1)_S2.xlsx`` / ``DEMO (1)_S2_IEC.xlsx`` 金样的逐列反推，坐标见
``docs/逆向矩阵展开器落地.md`` §5。所有数值均由 ``.wrp`` data sense 直接读取（不复算、
不插值）；``.wrp`` 没有的项目级元信息（工程名、投影系、经纬度、机型、IEC 等级等）由
``--context`` JSON 传入，缺失时输出 ``-`` 并记入报告，不允许凭猜测填充。

数值格式遵循金样观察：MUP 导出的是"按列显示精度四舍五入后的值"（``ROUND_HALF_UP``），
扇区号则截断取整（112.5 -> 112，67.5 -> 67）。P1 只保证数据/数值/sheet 结构等效，
样式、合并单元格、公式、图表、本地化属 P2。

用法::

    python mup_excel_export_p1.py --wrp <x.wrp> --out-dir out --name "DEMO (1)_S2" \
        --context ctx.json --gold-s2 "DEMO (1)_S2.xlsx" --gold-iec "DEMO (1)_S2_IEC.xlsx"

红线：只读。不加载目标程序集、不起 MUP、不写 Vault、不触 hasp/licence。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.utils import get_column_letter

import mup_matrix_expander as expander

__all__ = [
    "ExportContext",
    "ExportError",
    "SynthesisLayout",
    "build_synthesis_workbook",
    "build_iec_workbook",
    "export_case",
    "reconcile_workbooks",
]

DIRECTION_SHEET_COLUMNS = 33
AVERAGE_HEADER_ROW = 39
IEC_SPEED_MIN = 2
IEC_SPEED_MAX = 23

SENSE = expander.DATA_SENSE
_SYNTHESIS_RE = re.compile(r"_synthesis_(\d+)\.wrp$", re.IGNORECASE)


class ExportError(Exception):
    """Raised when the .wrp or context cannot produce a faithful workbook."""


# --------------------------------------------------------------------------
# Numeric formatting (the display precision observed in the GUI workbook)
# --------------------------------------------------------------------------


def _quantize(value: float, digits: int) -> float:
    """Round half-up to `digits` (double precision, for computed aggregates)."""

    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))


def _single_repr(value: float) -> str:
    """Shortest decimal string that round-trips to the same float32.

    ``.wrp`` stores single-precision numbers; MUP's export serialises the float with
    .NET's shortest round-trip formatting and then rounds that decimal.  Using the
    raw double expansion (``0.016499999910593033``) would round half a cent low,
    so the export must round the shortest single-precision representation
    (``0.0165`` -> ``0.017``).
    """

    target = struct.unpack("<f", struct.pack("<f", float(value)))[0]
    if not math.isfinite(target):
        raise ExportError(f"non-finite value {value!r}")
    for precision in range(1, 10):
        text = format(target, f".{precision}g")
        if struct.unpack("<f", struct.pack("<f", float(text)))[0] == target:
            return text
    return repr(target)


def _quantize_single(value: float, digits: int) -> float:
    """Round a ``.wrp`` single-precision value the way MUP formats it."""

    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(_single_repr(value)).quantize(quantum, rounding=ROUND_HALF_UP))


def _sector(value: float) -> int:
    """Sector labels are truncated, not rounded (112.5 -> 112, 67.5 -> 67)."""

    return int(math.floor(float(value) + 1e-9))


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and not math.isfinite(value))


# --------------------------------------------------------------------------
# Context: project-level facts that the .wrp does not carry
# --------------------------------------------------------------------------


@dataclass
class TurbineContext:
    model: str | None = None
    version: str | None = None
    turbulence_class: str | None = None
    power_curve_ref: str | None = None
    longitude: float | None = None
    latitude: float | None = None
    trust: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> TurbineContext:
        data = data or {}
        return cls(
            model=data.get("model"),
            version=data.get("version"),
            turbulence_class=data.get("turbulence_class"),
            power_curve_ref=data.get("power_curve_ref"),
            longitude=data.get("longitude"),
            latitude=data.get("latitude"),
            trust=data.get("trust"),
            extra=data,
        )


@dataclass
class IECContext:
    iec_class: str | None = None
    iref: float | None = None
    wohler: float | None = None
    reference_mast: str | None = None
    cct_class: str | None = None
    air_density: float | None = None
    min_shear: float | None = None
    max_shear: float | None = None
    eta: float | None = None
    vref: float | None = None
    ve50: float | None = None
    edition: str | None = None
    norm: int | None = None
    maximum_inflow_angle: float | None = None
    maximum_air_density: float | None = None
    ed3_min_shear: float | None = None
    ed3_max_shear: float | None = None
    speed_min: int = IEC_SPEED_MIN
    speed_max: int = IEC_SPEED_MAX

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> IECContext:
        data = data or {}
        return cls(
            iec_class=data.get("iec_class"),
            iref=data.get("iref"),
            wohler=data.get("wohler"),
            reference_mast=data.get("reference_mast"),
            cct_class=data.get("cct_class"),
            air_density=data.get("air_density"),
            min_shear=data.get("min_shear"),
            max_shear=data.get("max_shear"),
            eta=data.get("eta"),
            vref=data.get("vref"),
            ve50=data.get("ve50"),
            edition=data.get("edition"),
            norm=data.get("norm"),
            maximum_inflow_angle=data.get("maximum_inflow_angle"),
            maximum_air_density=data.get("maximum_air_density"),
            ed3_min_shear=data.get("ed3_min_shear"),
            ed3_max_shear=data.get("ed3_max_shear"),
            speed_min=int(data.get("speed_min", IEC_SPEED_MIN)),
            speed_max=int(data.get("speed_max", IEC_SPEED_MAX)),
        )


@dataclass
class ExportContext:
    """Project metadata that the .wrp does not store.

    Every field is optional: a missing value is written as ``-`` and reported as a
    context gap instead of being invented.
    """

    case_name: str | None = None
    synthesis: int | None = None
    description: str | None = None
    projection: str | None = None
    reference_density: float | None = None
    reference_density_at: str | None = None
    weighting: str | None = None
    met_mast_label: str | None = None
    met_mast_type: str | None = None
    met_mast_height: str | None = None
    wake_enabled: bool | None = None
    turbines: dict[str, TurbineContext] = field(default_factory=dict)
    iec: IECContext = field(default_factory=IECContext)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExportContext:
        return cls(
            case_name=data.get("case_name"),
            synthesis=data.get("synthesis"),
            description=data.get("description"),
            projection=data.get("projection"),
            reference_density=data.get("reference_density"),
            reference_density_at=data.get("reference_density_at"),
            weighting=data.get("weighting"),
            met_mast_label=data.get("met_mast_label"),
            met_mast_type=data.get("met_mast_type"),
            met_mast_height=data.get("met_mast_height"),
            wake_enabled=data.get("wake_enabled"),
            turbines={
                label: TurbineContext.from_dict(value)
                for label, value in (data.get("turbines") or {}).items()
            },
            iec=IECContext.from_dict(data.get("iec")),
        )

    @classmethod
    def load(cls, path: str | Path | None) -> ExportContext:
        if path is None:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ExportError(f"{path}: context JSON must be an object")
        return cls.from_dict(data)


# --------------------------------------------------------------------------
# .wrp view helpers
# --------------------------------------------------------------------------


@dataclass
class PointSeries:
    """Per-point reader that tolerates senses absent from a given .wrp."""

    wrp: expander.WrpFile
    index: int
    label: str
    is_turbine: bool

    def value(self, sense: int, direction: float = -1.0, speed: float = -1.0) -> float | None:
        try:
            raw = self.wrp.values(sense, direction, speed)[self.index]
        except (expander.MatrixExpandError, IndexError, ValueError):
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return None if _is_missing(value) else value

    def dtype(self, sense: int, direction: float = -1.0, speed: float = -1.0) -> int:
        return self.wrp.block(sense, direction, speed).dtype

    def q(self, sense: int, digits: int, direction: float = -1.0, speed: float = -1.0):
        """Display-rounded value; float32 blocks round via their shortest decimal."""

        value = self.value(sense, direction, speed)
        if value is None:
            return None
        if self.dtype(sense, direction, speed) == 0:
            return _quantize_single(value, digits)
        return _quantize(value, digits)

    def per_direction(self, sense: int) -> list[float | None]:
        step = self.wrp.direction_step
        return [self.value(sense, d * step) for d in range(self.wrp.nb_directions)]

    def numbers(self, sense: int) -> list[float]:
        return [v for v in self.per_direction(sense) if v is not None]


_NATURAL_RE = re.compile(r"(\d+)")


def _natural_key(label: str) -> list[Any]:
    """Sort M1/W2/W10 like the GUI instead of lexicographically."""

    return [
        int(part) if part.isdigit() else part.lower()
        for part in _NATURAL_RE.split(str(label))
    ]


def _sorted_labels(wrp: expander.WrpFile) -> list[str]:
    return sorted(wrp.labels, key=_natural_key)


def _points(wrp: expander.WrpFile) -> list[PointSeries]:
    turbines = set(wrp.wind_turbine_labels())
    return [
        PointSeries(wrp=wrp, index=wrp.label_index(label), label=label,
                    is_turbine=label in turbines)
        for label in _sorted_labels(wrp)
    ]


def _neighbour(points: Sequence[PointSeries], point: PointSeries) -> tuple[str, float, float] | None:
    """Closest turbine from WRP sense 62, then distance/bearing from coordinates.

    MUP's Excel exporter does not search for the nearest point.  It reads the
    precomputed ``ClosestWindTurbine`` UID (data sense 62) and resolves it through
    the point UID (sense 46).  Masts are never candidates.
    """

    if not point.is_turbine:
        return None
    closest_uid = point.value(SENSE["closest_wind_turbine"])
    if closest_uid is None or closest_uid <= 0:
        return None
    target_uid = int(round(closest_uid))
    target = None
    for other in points:
        if other.label == point.label or not other.is_turbine:
            continue
        uid = other.value(SENSE["uid"])
        if uid is not None and int(round(uid)) == target_uid:
            target = other
            break
    if target is None:
        return None
    dx = (target.value(SENSE["coordinate_x"]) or 0.0) - (point.value(SENSE["coordinate_x"]) or 0.0)
    dy = (target.value(SENSE["coordinate_y"]) or 0.0) - (point.value(SENSE["coordinate_y"]) or 0.0)
    distance = math.hypot(dx, dy)
    bearing = math.degrees(math.atan2(dx, dy)) % 360.0
    return (target.label, distance, bearing)


# --------------------------------------------------------------------------
# 17-sheet synthesis workbook
# --------------------------------------------------------------------------


DIRECTION_HEADERS: tuple[str, ...] = (
    "风机类型", "版本", "湍流强度等级", "功率曲线对应风速", "标签",
    "X (m)", "Y (m)", "Z (m)", "H (m)", "经度", "纬度", "信任系数",
    "扇区（度）", "风向偏转（度）", "频率（%）",
    "韦布尔参数A （米/秒）", "韦布尔参数 k  (-)", "能量密度（瓦特/平方米）",
    "年发电量 (兆瓦时/年)", "平均风速(米/秒)", "平均 REWS (米/秒)",
    "考虑尾流效应的发电量（兆瓦时/年）", "考虑尾流效应的平均风速（米/秒）",
    "考虑尾流的平均 REWS (米/秒)", "考虑限功率的年发电量 (兆瓦时/年)",
    "考虑尾流和限功率的年发电量(兆瓦时/年)", "考虑尾流和限功率的平均风速 (m/s)",
    "强风状态下的环境湍流强度", "强风状态下的总体湍流强度", "入流角(度)",
    "风切变指数", "风加速因数", "垂直方向风向偏转（度）",
)

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


@dataclass
class SynthesisLayout:
    """Column plan for the two synthesis sheet flavours."""

    direction_sheets: bool = True
    average_sheet: bool = True


def _direction_row(point: PointSeries, angle: float, ctx: ExportContext) -> list[Any]:
    turbine = ctx.turbines.get(point.label, TurbineContext())
    freq = point.value(SENSE["frequency"], angle)
    production = point.value(SENSE["production"], angle)
    wake_production = point.value(SENSE["wake_production"], angle)
    curtailed = point.value(SENSE["curtailed_production"], angle)
    curtailed_wake = point.value(SENSE["curtailed_wake_production"], angle)
    veer = point.value(SENSE["veer"], angle)

    def num(sense: int, digits: int, *, hide_zero: bool = False) -> Any:
        value = point.value(sense, angle)
        if value is None:
            return "-"
        if hide_zero and (value == 0 or not point.is_turbine):
            return "-"
        return point.q(sense, digits, angle)

    row: list[Any] = [
        turbine.model,
        turbine.version,
        turbine.turbulence_class,
        turbine.power_curve_ref,
        point.label,
        point.q(SENSE["coordinate_x"], 0),
        point.q(SENSE["coordinate_y"], 0),
        point.q(SENSE["coordinate_z"], 1),
        point.q(SENSE["coordinate_h"], 0),
        turbine.longitude,
        turbine.latitude,
        turbine.trust if point.is_turbine else "-",
        angle,
        num(SENSE["deviation"], 1),
        "-" if freq is None else _quantize_single(freq * 100.0, 0),
        num(SENSE["weibull_a"], 2),
        num(SENSE["weibull_k"], 3),
        num(SENSE["power_density"], 1),
        "-" if (production is None or production == 0) else _quantize_single(production, 2),
        num(SENSE["wind_speed"], 2),
        "-",
        "-" if (wake_production is None or wake_production == 0) else _quantize_single(wake_production, 2),
        num(SENSE["wake_wind_speed"], 2),
        "-",
        "-" if (curtailed is None or curtailed == 0) else _quantize_single(curtailed, 2),
        "-" if (curtailed_wake is None or curtailed_wake == 0) else _quantize_single(curtailed_wake, 2),
        num(SENSE["curtailed_wake_wind_speed"], 2),
        num(SENSE["turbulence_ambient_strong_wind"], 3),
        num(SENSE["turbulence_total_strong_wind"], 3),
        num(SENSE["inflow_angle"], 1),
        num(SENSE["shear"], 2),
        num(SENSE["speed_up_factor"], 3),
        "-" if (veer is None or not point.is_turbine) else _quantize_single(veer, 3),
    ]
    if len(row) != DIRECTION_SHEET_COLUMNS:
        raise ExportError(f"{point.label}: direction row has {len(row)} columns")
    return row


def _average_row(point: PointSeries, points: Sequence[PointSeries], ctx: ExportContext) -> list[Any]:
    turbine = ctx.turbines.get(point.label, TurbineContext())
    shear_values = point.numbers(SENSE["shear"])
    inflow_values = point.per_direction(SENSE["inflow_angle"])
    inflow_pairs = [(d * point.wrp.direction_step, v) for d, v in enumerate(inflow_values) if v is not None]
    nearest = _neighbour(points, point) if point.is_turbine else None
    rotor_diameter = (point.value(SENSE["rotor_radius"]) or 0.0) * 2.0

    def mean(sense: int, digits: int) -> Any:
        value = point.value(sense)
        return "-" if value is None else point.q(sense, digits)

    def mean_or_dash(sense: int, digits: int, *, hide_zero: bool = False) -> Any:
        value = point.value(sense)
        if value is None or (hide_zero and (value == 0 or not point.is_turbine)):
            return "-"
        return point.q(sense, digits)

    production = point.value(SENSE["production"])
    wind_speed = point.value(SENSE["wind_speed"])
    capacity = point.value(5) if points else None
    max_shear = max(shear_values) if shear_values else None
    max_shear_sector = None
    if shear_values:
        peak = max(shear_values)
        index = point.per_direction(SENSE["shear"]).index(peak)
        max_shear_sector = _sector(index * point.wrp.direction_step)
    max_inflow = max(inflow_pairs, key=lambda item: item[1]) if inflow_pairs else None
    mean_inflow = point.value(SENSE["inflow_angle"])
    mean_veer = point.value(SENSE["veer"])
    max_veer = point.value(119)
    max_veer_sector = point.value(120)

    row: list[Any] = [
        turbine.model,
        turbine.version,
        turbine.turbulence_class,
        turbine.power_curve_ref,
        point.label,
        point.q(SENSE["coordinate_x"], 0),
        point.q(SENSE["coordinate_y"], 0),
        point.q(SENSE["coordinate_z"], 1),
        point.q(SENSE["coordinate_h"], 0),
        turbine.longitude,
        turbine.latitude,
        turbine.trust if point.is_turbine else "-",
        mean(SENSE["weibull_a"], 2),
        mean(SENSE["weibull_k"], 3),
        mean_or_dash(SENSE["power_density"], 1),
        mean_or_dash(SENSE["production"], 2, hide_zero=True),
        mean_or_dash(SENSE["wind_speed"], 2),
        "-",
        mean_or_dash(SENSE["wake_production"], 2, hide_zero=True),
        mean_or_dash(SENSE["wake_wind_speed"], 2),
        "-",
        mean_or_dash(SENSE["curtailed_production"], 2, hide_zero=True),
        mean_or_dash(SENSE["curtailed_wake_production"], 2, hide_zero=True),
        mean_or_dash(SENSE["curtailed_wake_wind_speed"], 2),
        "-" if (capacity in (None, 0) or not point.is_turbine) else _quantize_single(capacity, 3),
        mean_or_dash(SENSE["turbulence_ambient_strong_wind"], 3),
        mean_or_dash(SENSE["turbulence_total_strong_wind"], 3),
        "-",
        "-",
        "-" if not point.is_turbine else mean_or_dash(29, 1, hide_zero=True),
        mean_or_dash(1, 3),
        mean_or_dash(SENSE["shear"], 2),
        "-" if max_shear is None else _quantize_single(max_shear, 2),
        "-" if max_shear is None else max_shear_sector,
        "-" if mean_inflow is None else _quantize_single(mean_inflow, 1),
        "-" if max_inflow is None else _quantize_single(max_inflow[1], 1),
        "-" if max_inflow is None else _sector(max_inflow[0]),
        nearest[0] if nearest else "-",
        "-" if not nearest else _quantize(nearest[1], 0),
        "-" if not point.is_turbine else _quantize(rotor_diameter, 0),
        "-" if not nearest else _quantize(nearest[1] / rotor_diameter, 0),
        "-" if not nearest else _quantize(nearest[2], 0),
        point.wrp.nb_directions,
        "-" if mean_veer is None or not point.is_turbine else _quantize_single(mean_veer, 3),
        "-" if max_veer is None or not point.is_turbine else _quantize_single(max_veer, 3),
        "-" if max_veer_sector is None or not point.is_turbine else _sector(max_veer_sector),
    ]
    if len(row) != len(AVERAGE_HEADERS):
        raise ExportError(f"{point.label}: average row has {len(row)} columns")
    return row


def _write_average_sheet(ws, points: Sequence[PointSeries], ctx: ExportContext) -> None:
    wrp = points[0].wrp
    met = ctx.met_mast_label or next((p.label for p in points if not p.is_turbine), None)
    synthesis = ctx.synthesis
    model = ctx.case_name or ""
    if synthesis is not None:
        model = f"{model} (综合计算 {synthesis} - 综合{synthesis})".strip()

    ws["A1"] = "建模 : "
    ws["D1"] = model or "-"
    ws["A2"] = "描述 : "
    ws["D2"] = ctx.description if ctx.description is not None else "-"
    ws["A3"] = "投影系 : "
    ws["D3"] = ctx.projection if ctx.projection is not None else "-"
    ws["A5"] = "气象数据"
    ws["A6"] = "空气密度（千克/立方米）"
    ws["D6"] = ctx.reference_density if ctx.reference_density is not None else "-"
    ws["E6"] = "at"
    ws["F6"] = ctx.reference_density_at if ctx.reference_density_at is not None else "-"
    ws["A7"] = "权重"
    ws["D7"] = ctx.weighting if ctx.weighting is not None else "-"
    ws["A9"] = "气象参考"
    ws["A10"] = "元素 :"
    ws["D10"] = met or "-"
    ws["A11"] = "气象对象 : "
    ws["D11"] = met or "-"
    ws["A12"] = "类型 : "
    ws["D12"] = ctx.met_mast_type if ctx.met_mast_type is not None else "-"
    ws["A13"] = "高度 : "
    ws["D13"] = ctx.met_mast_height if ctx.met_mast_height is not None else "-"
    ws["A15"] = "元素"
    ws["A16"] = "尾流效应 : "
    ws["D16"] = "-" if ctx.wake_enabled is None else str(bool(ctx.wake_enabled))
    ws["A18"] = "结果 :"
    for row, label in enumerate(
        f"{_sector((index + 1) * wrp.direction_step):03d} {synthesis if synthesis is not None else ''}".strip()
        for index in range(wrp.nb_directions)
    ):
        ws.cell(row=18 + row, column=4, value=label)

    # MUP sums the displayed cell values, not the stored float32 payload.
    total_production = sum(
        _quantize_single(p.value(SENSE["production"]), 2)
        for p in points
        if p.is_turbine and p.value(SENSE["production"]) is not None
    )
    total_wake = sum(
        _quantize_single(p.value(SENSE["wake_production"]), 2)
        for p in points
        if p.is_turbine and p.value(SENSE["wake_production"]) is not None
    )
    reference_speed = next(
        (p.value(SENSE["wind_speed"]) for p in points if p.label == met), None
    )
    ws["A35"] = "不考虑尾流和限功率的风电场总发电量： "
    ws["F35"] = f"{_quantize(total_production, 2)} 兆瓦时/年"
    ws["A36"] = "考虑尾流效应的风电场总发电量：  "
    ws["F36"] = f"{_quantize(total_wake, 2)} 兆瓦时/年"
    ws["A37"] = "参考点处的平均风速： "
    ws["F37"] = (
        "-"
        if reference_speed is None
        else f"{_quantize_single(reference_speed, 2)} 米/秒"
    )

    for column, header in enumerate(AVERAGE_HEADERS, start=1):
        ws.cell(row=AVERAGE_HEADER_ROW, column=column, value=header)
    for offset, point in enumerate(points):
        for column, value in enumerate(_average_row(point, points, ctx), start=1):
            if value is not None:
                ws.cell(row=AVERAGE_HEADER_ROW + 1 + offset, column=column, value=value)


def _direction_sheet_name(angle: float) -> str:
    return str(int(angle)) if float(angle).is_integer() else str(angle)


def build_synthesis_workbook(
    wrp: expander.WrpFile,
    ctx: ExportContext,
    layout: SynthesisLayout | None = None,
) -> Workbook:
    layout = layout or SynthesisLayout()
    points = _points(wrp)
    if not points:
        raise ExportError(f"{wrp.path}: no points to export")

    workbook = Workbook()
    workbook.remove(workbook.active)

    if layout.average_sheet:
        sheet = workbook.create_sheet("平均")
        _write_average_sheet(sheet, points, ctx)

    if layout.direction_sheets:
        for index in range(wrp.nb_directions):
            angle = round(index * wrp.direction_step, 6)
            sheet = workbook.create_sheet(_direction_sheet_name(angle))
            for column, header in enumerate(DIRECTION_HEADERS, start=1):
                sheet.cell(row=2, column=column, value=header)
            for offset, point in enumerate(points):
                for column, value in enumerate(_direction_row(point, angle, ctx), start=1):
                    if value is not None:
                        sheet.cell(row=3 + offset, column=column, value=value)

    return workbook


# --------------------------------------------------------------------------
# 5-sheet IEC workbook
# --------------------------------------------------------------------------


IEC_TURBINE_HEADERS: tuple[str, ...] = (
    "标签", "X (m)", "Y (m)", "Z (m)", "H (m)", "等级", "IEC Iref", "Wöhler 指数",
    "IEC湍流强度计算的参考测风点", "地形复杂度系数Cct", "地形复杂度类别",
    "韦布尔参数A (米/秒)", "韦布尔参数 k  (-)", "风流分布分析",
    "风流分布分析 - 韦布尔k值", "强风状态下的平均有效湍流强度", "有效湍流强度",
    "IEC绝对入流角（度）", "以能量为权重的平均绝对入流角（度）", "IEC最小风切变指数",
    "IEC最大风切变指数", "以能量为权重的平均绝对风切变", "最大风切变指数",
    "IEC空气密度(千克/立方米)", "空气密度(千克/立方米)", "风压",
)

IEC_EXTREME_HEADERS: tuple[str, ...] = (
    "标签", "X (m)", "Y (m)", "Z (m)", "H (m)", "等级", "IEC Iref", "Wöhler 指数",
    "IEC湍流强度计算的参考测风点", "地形复杂度系数Cct", "地形复杂度类别", "ETA 系数",
    "IEC Vref (m/s)", "10分钟平均极风速（50年回归期）（米/秒）", "IEC Ve50 (m/s)",
    "3秒钟阵风极风速（50年回归期）（米/秒）", "极限载荷风压",
)

# name -> (sense, speed_multiplier)
IEC_LOAD_SHEETS: tuple[tuple[str, int, bool], ...] = (
    ("IEC4.0版疲劳载荷湍流强度", SENSE["turbulence_effective"], False),
    ("IEC4.0版极限载荷湍流强度", 40, False),
    ("IEC4.0版尾迹中心最大湍流", 84, True),
    ("IEC4.0版极端湍流", 83, True),
)


# IEC 61400-1 Ed. 4 reference wind speeds per class ("S" is project-defined).
CLASS_VREF: dict[str, float] = {"IA": 50.0, "IB": 50.0, "IC": 50.0, "II": 42.5, "III": 37.5}
GUST_FACTOR = 1.4


def _iec_extremes(ctx: ExportContext) -> tuple[Any, Any, Any]:
    iec = ctx.iec
    vref = iec.vref
    if vref is None and iec.iec_class:
        vref = CLASS_VREF.get(iec.iec_class.upper())
    ve50 = iec.ve50
    if ve50 is None and vref is not None:
        ve50 = round(vref * GUST_FACTOR, 6)
    eta = 1.0 if iec.eta is None else iec.eta
    return (
        "-" if eta is None else eta,
        "-" if vref is None else vref,
        "-" if ve50 is None else ve50,
    )


def _iec_speeds(wrp: expander.WrpFile, ctx: ExportContext) -> list[int]:
    upper = min(ctx.iec.speed_max, wrp.nb_windspeeds - 1)
    return [speed for speed in range(ctx.iec.speed_min, upper + 1)]


def _need(value: Any) -> Any:
    return "-" if value is None else value


def _bool_text(value: float | None, *, truth: str = "真", falsehood: str = "假") -> str:
    if value is None:
        return "-"
    return truth if int(round(value)) else falsehood


def _write_iec_turbine_sheet(ws, turbines: Sequence[PointSeries], ctx: ExportContext) -> None:
    iec = ctx.iec
    ws["A1"] = (
        f"综合计算 {ctx.synthesis} - 综合{ctx.synthesis}"
        if ctx.synthesis is not None
        else (ctx.case_name or "-")
    )
    ws["A4"] = "以疲劳载荷为衡量标准的场址适应性"
    ws["J7"] = "地形复杂程度"
    ws["L7"] = "疲劳载荷"
    for column, label in ((12, "1 - 风流分布"), (16, "2 - 湍流分析"), (18, "3 - 风流倾角"),
                          (20, "4 - 风切变"), (24, "5 - 空气密度")):
        ws.cell(row=9, column=column, value=label)
    for column, header in enumerate(IEC_TURBINE_HEADERS, start=1):
        ws.cell(row=11, column=column, value=header)

    for offset, point in enumerate(turbines):
        row = 12 + offset
        shear = point.numbers(SENSE["shear"])
        inflow = point.numbers(SENSE["inflow_angle"])
        abs_inflow = sum(abs(v) for v in inflow) / len(inflow) if inflow else None
        values: list[Any] = [
            point.label,
            point.q(SENSE["coordinate_x"], 0),
            point.q(SENSE["coordinate_y"], 0),
            point.q(SENSE["coordinate_z"], 1),
            point.q(SENSE["coordinate_h"], 0),
            _need(iec.iec_class),
            _need(iec.iref),
            _need(iec.wohler),
            _need(iec.reference_mast),
            _need(None if point.value(71) is None else _quantize_single(point.value(71), 2)),
            _need(iec.cct_class),
            "-" if point.value(SENSE["weibull_a"]) is None else _quantize_single(point.value(SENSE["weibull_a"]), 2),
            "-" if point.value(SENSE["weibull_k"]) is None else _quantize(point.value(SENSE["weibull_k"]), 3),
            _bool_text(point.value(74)),
            _bool_text(point.value(75)),
            "-" if point.value(19) is None else _quantize(point.value(19), 3),
            _bool_text(point.value(76), truth="假", falsehood="真"),
            _need(None if abs_inflow is None else _quantize(abs_inflow, 0)),
            "-" if point.value(72) is None else _quantize_single(point.value(72), 1),
            _need(iec.min_shear),
            _need(iec.max_shear),
            "-" if point.value(73) is None else _quantize_single(point.value(73), 2),
            "-" if not shear else _quantize_single(max(shear), 2),
            _need(iec.air_density),
            "-" if point.value(1) is None else _quantize_single(point.value(1), 3),
            _bool_text(point.value(21)),
        ]
        for column, value in enumerate(values, start=1):
            if value is not None:
                ws.cell(row=row, column=column, value=value)

    ws["A25"] = "以极限载荷为衡量标准的场址适应性"
    ws["J28"] = "地形复杂程度"
    ws["L28"] = "极限载荷"
    for column, label in ((12, "6 -极风速"), (18, "7 - 以极限载荷为衡量标准的湍流强度分析"),
                          (19, " 8 - 极端湍流分析"), (20, "9 - 最大中心尾流风速标准偏差")):
        ws.cell(row=30, column=column, value=label)
    for column, header in enumerate(IEC_EXTREME_HEADERS, start=1):
        ws.cell(row=32, column=column, value=header)

    eta, vref, ve50 = _iec_extremes(ctx)
    for offset, point in enumerate(turbines):
        row = 33 + offset
        values = [
            point.label,
            point.q(SENSE["coordinate_x"], 0),
            point.q(SENSE["coordinate_y"], 0),
            point.q(SENSE["coordinate_z"], 1),
            point.q(SENSE["coordinate_h"], 0),
            _need(iec.iec_class),
            _need(iec.iref),
            _need(iec.wohler),
            _need(iec.reference_mast),
            _need(None if point.value(71) is None else _quantize_single(point.value(71), 2)),
            _need(iec.cct_class),
            _need(eta),
            _need(vref),
            "-",
            _need(ve50),
            "-",
            _bool_text(point.value(21)),
            _bool_text(point.value(77)),
            _bool_text(point.value(78)),
            _bool_text(point.value(80)),
        ]
        for column, value in enumerate(values, start=1):
            if value is not None:
                ws.cell(row=row, column=column, value=value)



IEC31_LOAD_SHEETS: tuple[tuple[str, int, bool], ...] = (
    ("湍流", SENSE["turbulence_effective"], False),
    ("特定极端湍流", SENSE["turbulence_iec"], False),
)

IEC31_TURBINE_HEADERS: tuple[str, ...] = (
    "标签", "X (m)", "Y (m)", "Z (m)", "H (m)", "等级", "IEC Iref",
    "Wöhler 指数", "IEC湍流强度计算的参考测风点", "IEC Vref",
    "50年回归的10分钟平均极风速（米/秒）", "韦布尔参数A (米/秒)", "韦布尔参数 k  (-)",
    "风流分布分析", "强风状态下的平均有效湍流强度", "湍流分析",
    "IEC绝对入流角（度）", "最大入流角绝对值(度)", "出现最大入流角的风向扇区 （度）",
    "IEC最小风切变指数", "IEC最大风切变指数", "最小风切变指数", "最大风切变",
    "IEC空气密度(千克/立方米)", "空气密度(千克/立方米)", "5 - 特定极端湍流分析",
    "地形复杂度系数Cct", "地形复杂度指数Ic",
)


def _wrp_iec_norm(wrp: expander.WrpFile) -> int:
    """Read the first turbine's DataSense 10 IEC edition enum (0..3)."""

    for point in _points(wrp):
        if not point.is_turbine:
            continue
        value = point.value(SENSE["iec_edition"])
        if value is not None:
            return int(round(value))
    raise ExportError(f"{wrp.path}: no turbine DataSense 10 (IECEdition) value")


def _build_iec31(wrp: expander.WrpFile, ctx: ExportContext) -> Workbook:
    """IEC Ed.3.1: 2 load sheets + one flat 28-column turbine table."""

    points = [point for point in _points(wrp) if point.is_turbine]
    if not points:
        raise ExportError(f"{wrp.path}: no wind turbines for the IEC workbook")
    workbook = Workbook()
    workbook.remove(workbook.active)

    for name, sense, scale_by_speed in IEC31_LOAD_SHEETS:
        sheet = workbook.create_sheet(name)
        sheet.cell(row=1, column=1, value="风速（米/秒）")
        for column, point in enumerate(points, start=2):
            sheet.cell(row=1, column=column, value=point.label)
        for row, speed in enumerate(range(3, 26), start=2):
            sheet.cell(row=row, column=1, value=speed)
            for column, point in enumerate(points, start=2):
                if sense == SENSE["turbulence_iec"]:
                    # Ed.3.1 specific-extreme: max over 16 directions of sense 39, then x V.
                    values = [
                        point.value(sense, direction * point.wrp.direction_step, float(speed))
                        for direction in range(point.wrp.nb_directions)
                    ]
                    values = [value for value in values if value is not None]
                    if not values:
                        continue
                    value = max(values) * speed
                else:
                    value = point.value(sense, -1.0, float(speed))
                    if value is None:
                        continue
                    if scale_by_speed:
                        value *= speed
                sheet.cell(row=row, column=column, value=_quantize(value, 3))

    sheet = workbook.create_sheet("风机 - Ed. 3.1")
    sheet.cell(
        row=1,
        column=1,
        value=f"综合计算 {ctx.synthesis or 1} - 综合{ctx.synthesis or 1} m={ctx.iec.wohler or 1:g}",
    )
    for column, header in enumerate(IEC31_TURBINE_HEADERS, start=1):
        sheet.cell(row=2, column=column, value=header)

    _, vref, _ = _iec_extremes(ctx)
    for offset, point in enumerate(points):
        row = 3 + offset
        ic = point.value(SENSE["site_complexity_ic"])
        inflow_abs = point.value(SENSE["iec_inflow_angle_max"])
        if inflow_abs is not None:
            inflow_abs = abs(_quantize_single(inflow_abs, 1))
        values = [
            point.label,
            point.q(SENSE["coordinate_x"], 1),
            point.q(SENSE["coordinate_y"], 1),
            point.q(SENSE["coordinate_z"], 1),
            point.q(SENSE["coordinate_h"], 1),
            _need(ctx.iec.iec_class),
            _need(None if ctx.iec.iref is None else _quantize(ctx.iec.iref, 3)),
            _need(ctx.iec.wohler),
            _need(ctx.iec.reference_mast),
            _need(None if vref == "-" else _quantize(float(vref), 2)),
            "-" if point.value(SENSE["extreme_wind_speed"]) is None else _quantize_single(point.value(SENSE["extreme_wind_speed"]), 2),
            "-" if point.value(SENSE["weibull_a"]) is None else _quantize_single(point.value(SENSE["weibull_a"]), 2),
            "-" if point.value(SENSE["weibull_k"]) is None else _quantize(point.value(SENSE["weibull_k"]), 3),
            _bool_text(point.value(SENSE["iec_validation_distribution"])),
            "-" if point.value(SENSE["iec_turbulence_effective_strong_wind"]) is None else _quantize_single(point.value(SENSE["iec_turbulence_effective_strong_wind"]), 3),
            _bool_text(point.value(SENSE["iec_validation_turbulence_analysis"])),
            _need(ctx.iec.maximum_inflow_angle),
            "-" if inflow_abs is None else inflow_abs,
            _need(point.value(SENSE["iec_inflow_angle_max_direction"])),
            _need(ctx.iec.ed3_min_shear if ctx.iec.ed3_min_shear is not None else ctx.iec.min_shear),
            _need(ctx.iec.ed3_max_shear if ctx.iec.ed3_max_shear is not None else ctx.iec.max_shear),
            "-" if point.value(SENSE["iec_shear_min"]) is None else _quantize_single(point.value(SENSE["iec_shear_min"]), 2),
            "-" if point.value(SENSE["iec_shear_max"]) is None else _quantize_single(point.value(SENSE["iec_shear_max"]), 2),
            _need(None if ctx.iec.maximum_air_density is None else _quantize(ctx.iec.maximum_air_density, 3)),
            "-" if point.value(SENSE["air_density"]) is None else _quantize_single(point.value(SENSE["air_density"]), 3),
            _bool_text(point.value(SENSE["iec_validation_specific_extreme_turbulence"])),
            "-" if ic is None else _quantize(1.0 + 0.15 * ic, 3),
            "-" if ic is None else _quantize(ic, 3),
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=value)
            if column in (13, 25):
                cell.number_format = "@"

    workbook.create_sheet("Sheet1")
    return workbook


def build_iec_workbook(wrp: expander.WrpFile, ctx: ExportContext) -> Workbook:
    try:
        norm = _wrp_iec_norm(wrp)
    except ExportError:
        if ctx.iec.norm is None:
            raise
        norm = int(ctx.iec.norm)
    if ctx.iec.norm is not None and int(ctx.iec.norm) != norm:
        raise ExportError(
            f"{wrp.path}: context IEC norm {ctx.iec.norm} conflicts with WRP DataSense 10 ({norm})"
        )
    if norm != 3:
        return _build_iec31(wrp, ctx)
    points = [point for point in _points(wrp) if point.is_turbine]
    if not points:
        raise ExportError(f"{wrp.path}: no wind turbines for the IEC workbook")
    speeds = _iec_speeds(wrp, ctx)

    workbook = Workbook()
    workbook.remove(workbook.active)

    for name, sense, scale_by_speed in IEC_LOAD_SHEETS:
        sheet = workbook.create_sheet(name)
        sheet.cell(row=1, column=1, value="风速（米/秒）")
        for column, point in enumerate(points, start=2):
            sheet.cell(row=1, column=column, value=point.label)
        for row, speed in enumerate(speeds, start=2):
            sheet.cell(row=row, column=1, value=speed)
            for column, point in enumerate(points, start=2):
                value = point.value(sense, -1.0, float(speed))
                if value is None:
                    continue
                if scale_by_speed:
                    value *= speed
                # IEC load sheets use Math.Round on the double value; the 7-digit
                # Single pre-round used by the matrix writer would shift 31 ties.
                sheet.cell(row=row, column=column, value=_quantize(value, 3))

    sheet = workbook.create_sheet("风机 - Ed. 4")
    _write_iec_turbine_sheet(sheet, points, ctx)
    return workbook


# --------------------------------------------------------------------------
# Export entry point
# --------------------------------------------------------------------------


def export_case(
    wrp: expander.WrpFile,
    ctx: ExportContext,
    out_dir: str | Path,
    name: str,
) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    synthesis_path = out / f"{name}.xlsx"
    iec_path = out / f"{name}_IEC.xlsx"

    build_synthesis_workbook(wrp, ctx).save(synthesis_path)
    build_iec_workbook(wrp, ctx).save(iec_path)
    return {"synthesis": synthesis_path, "iec": iec_path}


# --------------------------------------------------------------------------
# Cell-by-cell reconciliation
# --------------------------------------------------------------------------


@dataclass
class SheetDiff:
    sheet: str
    cells: int = 0
    matched: int = 0
    boundary: int = 0
    mismatched: int = 0
    missing: int = 0
    examples: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sheet": self.sheet,
            "cells": self.cells,
            "matched": self.matched,
            "boundary": self.boundary,
            "mismatched": self.mismatched,
            "missing": self.missing,
            "examples": self.examples,
        }


def _decimals(value: float) -> int:
    """Decimals used by the displayed value, e.g. 2.309 -> 3, 0.31 -> 2."""

    text = repr(float(value))
    if "e" in text or "E" in text:
        return 12
    return len(text.split(".", 1)[1].rstrip("0")) if "." in text else 0


def _within_tick(expected: float, actual: float) -> bool:
    """True when the two values differ by at most one unit of the displayed digit."""

    tick = 10.0 ** -_decimals(expected)
    return abs(float(expected) - float(actual)) <= tick * (1.0 + 1e-9)


def _compare_cell(expected: Any, actual: Any, tolerance: float, digits: int | None) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        if digits is not None:
            return _quantize(float(expected), digits) == _quantize(float(actual), digits)
        return abs(float(expected) - float(actual)) <= tolerance
    return str(expected) == str(actual)


def reconcile_workbooks(
    expected_path: str | Path,
    actual_path: str | Path,
    *,
    tolerance: float = 1e-9,
    digits: int | None = None,
    max_examples: int = 25,
) -> dict[str, Any]:
    """Compare two workbooks cell by cell.

    ``missing`` counts cells the generated book leaves empty because the value is not
    derivable from the ``.wrp`` (a context gap, reported separately from a mismatch).
    """

    expected = load_workbook(expected_path, data_only=True)
    actual = load_workbook(actual_path, data_only=True)
    sheets: list[SheetDiff] = []
    sheet_names = list(expected.sheetnames)
    missing_sheets = [name for name in sheet_names if name not in actual.sheetnames]
    extra_sheets = [name for name in actual.sheetnames if name not in sheet_names]

    for name in sheet_names:
        diff = SheetDiff(sheet=name)
        if name not in actual.sheetnames:
            diff.missing = expected[name].max_row * expected[name].max_column
            sheets.append(diff)
            continue
        gold_sheet, out_sheet = expected[name], actual[name]
        rows = max(gold_sheet.max_row, out_sheet.max_row)
        columns = max(gold_sheet.max_column, out_sheet.max_column)
        for row in range(1, rows + 1):
            for column in range(1, columns + 1):
                gold = gold_sheet.cell(row=row, column=column).value
                made = out_sheet.cell(row=row, column=column).value
                diff.cells += 1
                if gold is None and made is None:
                    diff.matched += 1
                    continue
                if made is None and gold is not None:
                    diff.missing += 1
                    if len(diff.examples) < max_examples:
                        diff.examples.append({
                            "cell": f"{get_column_letter(column)}{row}",
                            "kind": "missing",
                            "expected": gold,
                        })
                    continue
                if _compare_cell(gold, made, tolerance, digits):
                    diff.matched += 1
                    continue
                if (
                    digits is None
                    and isinstance(gold, (int, float))
                    and isinstance(made, (int, float))
                    and _within_tick(gold, made)
                ):
                    diff.boundary += 1
                    if len(diff.examples) < max_examples:
                        diff.examples.append({
                            "cell": f"{get_column_letter(column)}{row}",
                            "kind": "boundary",
                            "expected": gold,
                            "actual": made,
                        })
                    continue
                diff.mismatched += 1
                if len(diff.examples) < max_examples:
                    diff.examples.append({
                        "cell": f"{get_column_letter(column)}{row}",
                        "kind": "mismatch",
                        "expected": gold,
                        "actual": made,
                    })
        sheets.append(diff)

    totals = {
        "cells": sum(item.cells for item in sheets),
        "matched": sum(item.matched for item in sheets),
        "boundary": sum(item.boundary for item in sheets),
        "mismatched": sum(item.mismatched for item in sheets),
        "missing": sum(item.missing for item in sheets),
    }
    total = totals["cells"] or 1
    return {
        "expected": str(expected_path),
        "actual": str(actual_path),
        "sheets": [item.as_dict() for item in sheets],
        "missing_sheets": missing_sheets,
        "extra_sheets": extra_sheets,
        "totals": totals,
        "match_rate": round(totals["matched"] / total, 6),
        "display_equivalence": round(
            (totals["matched"] + totals["boundary"]) / total, 6
        ),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export MUP .wrp results to data-equivalent xlsx workbooks (P1)."
    )
    source = parser.add_argument_group("source")
    source.add_argument("--wrp", help="path to a synthesis .wrp")
    source.add_argument("--case", help="CaseGUID or site label (with --synthesis)")
    source.add_argument("--synthesis", type=int, help="synthesis index")
    source.add_argument("--vault", help="MUP local_* Vault root")
    parser.add_argument("--out-dir", required=True, help="directory for the workbooks")
    parser.add_argument("--name", help="output basename (default: <wrp stem>)")
    parser.add_argument("--context", help="project metadata JSON (see module docstring)")
    parser.add_argument("--gold-s2", help="reference 17-sheet workbook for reconciliation")
    parser.add_argument("--gold-iec", help="reference IEC workbook for reconciliation")
    parser.add_argument("--report", help="write the reconciliation summary as JSON")
    parser.add_argument("--tolerance", type=float, default=1e-9)
    parser.add_argument("--digits", type=int, help="compare after rounding both sides")
    return parser


def _resolve_wrp(args: argparse.Namespace) -> Path:
    if args.wrp:
        return Path(args.wrp)
    if args.case or args.synthesis is not None or args.vault:
        return expander.discover_wrp(
            case=args.case, synthesis=args.synthesis, vault=args.vault
        )
    raise ExportError("one of --wrp, --case or --vault is required")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        wrp_path = _resolve_wrp(args)
        wrp = expander.open_wrp(wrp_path)
        ctx = ExportContext.load(args.context)
        if ctx.synthesis is None:
            match = _SYNTHESIS_RE.search(wrp_path.name)
            if match:
                ctx.synthesis = int(match.group(1))
        name = args.name or wrp_path.stem
        paths = export_case(wrp, ctx, args.out_dir, name)
    except (ExportError, expander.MatrixExpandError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"synthesis: {paths['synthesis']}")
    print(f"iec:       {paths['iec']}")

    report: dict[str, Any] = {"wrp": str(wrp_path), "outputs": {k: str(v) for k, v in paths.items()}}
    status = 0
    for key, gold in (("synthesis", args.gold_s2), ("iec", args.gold_iec)):
        if not gold:
            continue
        result = reconcile_workbooks(
            gold, paths[key], tolerance=args.tolerance, digits=args.digits
        )
        report[key] = result
        totals = result["totals"]
        print(
            f"reconcile {key}: {totals['matched']}/{totals['cells']} exact, "
            f"{totals['boundary']} display-boundary, {totals['mismatched']} mismatch, "
            f"{totals['missing']} missing, "
            f"equivalence={result['display_equivalence']:.6f}"
        )
        if totals["mismatched"]:
            status = 1
    if args.report:
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"report:    {args.report}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
