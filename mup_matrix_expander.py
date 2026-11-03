"""High-resolution WRA matrix expander (REA task #4a).

Reverse-engineered from the shipped Meteodyn Universe assemblies:

* ``MTDWRServices.dll`` ``MTD.WR.Services.Exports.Type.MTDWRExportMatrix``
  (``ResultMatrixFrequency``, IL RVA 0x34258) writes the GUI matrix text.
  Every data cell is a direct ``wrp.GetValue(key, dataSense, floor(angle), speed)``
  read: 16 directions (360 / NBDIRECTIONS) x speeds 1..NBWINDSPEEDS-1.
* ``MTDWRServices.dll`` ``MTD.WR.Services.Writers.MTDWriterWRResultIEC.Write``
  (IL RVA 0x30aa8) defines the 13-column ``.wri`` schema.  The ``.wri`` is an
  *IEC terrain-complexity* summary, not a low-resolution frequency matrix; it
  carries no Frequency (data sense 9), Weibull A/K (42/43) or mean wind speed
  (44) values.
* ``MTDWRMPToolbox.dll`` ``MTDWRPObjectsHeaders`` / ``MTDWRPBlockHeaders``
  define the binary ``.wrp`` layout used below.

Consequently the physically valid expansion path is ``.wrp -> 16 x 59``.
``expand_from_wri()`` fails closed instead of inventing a distribution.

All numbers are read as stored.  No provider, licence or HPC call is made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import sys
from datetime import datetime, timezone
from dataclasses import dataclass, field
from decimal import Context, Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Iterable, Sequence

__all__ = [
    "DIRECTIONS",
    "SPEED_BINS",
    "DATA_SENSE",
    "MatrixExpandError",
    "WriFormatError",
    "WriNotFrequencyData",
    "WrpFormatError",
    "WriRecord",
    "WriFile",
    "WrpBlock",
    "WrpFile",
    "FrequencyMatrix",
    "TurbulenceBlock",
    "TurbulenceMatrix",
    "GuiTurbulence",
    "TurbulenceReport",
    "GateReport",
    "GateError",
    "GuiMatrix",
    "ReconciliationReport",
    "SelfCheck",
    "parse_wri",
    "open_wrp",
    "discover_vault_root",
    "discover_wrp",
    "expand_from_wri",
    "read_gui_matrix",
    "reconcile",
    "read_gui_turbulence",
    "reconcile_turbulence",
    "verify_artifact",
    "self_check",
]

# --------------------------------------------------------------------------
# Constants (MeteodynUtils.Constantes.DataSense)
# --------------------------------------------------------------------------

DATA_SENSE = {
    "coordinate_x": 2,
    "coordinate_y": 3,
    "coordinate_z": 4,
    "frequency": 9,
    "iec_edition": 10,
    "extreme_wind_speed": 8,
    "iec_inflow_angle_max": 11,
    "iec_inflow_angle_max_direction": 12,
    "iec_shear_max": 13,
    "iec_shear_min": 15,
    "iec_turbulence_effective_strong_wind": 19,
    "iec_validation_distribution": 22,
    "iec_validation_specific_extreme_turbulence": 26,
    "iec_validation_turbulence_analysis": 27,
    "site_complexity_ic": 69,
    "turbulence_added": 35,
    "turbulence_ambient": 36,
    "turbulence_characteristic": 37,
    "turbulence_effective": 38,
    "turbulence_iec": 39,
    "turbulence_representative": 40,
    "turbulence_extreme_ambient_1etm": 83,
    "turbulence_max_wake_direction_iec": 84,
    "turbulence_total": 41,
    "turbulence_total_strong": 53,
    "weibull_a": 42,
    "weibull_k": 43,
    "wind_speed": 44,
    "label": 45,
    "uid": 46,
    "is_wind_turbine": 47,
    "rotor_radius": 48,
    "coordinate_h": 49,
    "air_density": 1,
    "capacity_factor": 5,
    "deviation": 6,
    "lack_coef": 29,
    "inflow_angle": 28,
    "power_density": 30,
    "production": 31,
    "shear": 32,
    "speed_up_factor": 34,
    "wake_wind_speed": 50,
    "wake_production": 51,
    "closest_wind_turbine": 62,
    "turbulence_total_strong_wind": 53,
    "turbulence_ambient_strong_wind": 54,
    "veer": 118,
    "curtailed_wake_wind_speed": 125,
    "curtailed_production": 126,
    "curtailed_wake_production": 127,
    "iec_radius": 91,
    "direction": 92,
}

DIRECTION_COUNT = 16
SPEED_BIN_COUNT = 59
DIRECTIONS: tuple[float, ...] = tuple(round(i * 22.5, 6) for i in range(DIRECTION_COUNT))
SPEED_BINS: tuple[int, ...] = tuple(range(1, SPEED_BIN_COUNT + 1))

# .wri schema: (name, kind) in physical column order.
# MTDWriterWRResultIEC.Write appends these in exactly this order.
WRI_COLUMNS: tuple[tuple[str, str], ...] = (
    ("is_computed", "bool"),
    ("iec_edition", "int"),
    ("label", "str"),
    ("uid", "int"),
    ("direction_deg", "float"),
    ("iec_radius_m", "float"),
    ("iec_slope", "float"),
    ("iec_terrain_variation", "float"),
    ("energy_part", "float"),
    ("iec_tvi", "float"),
    ("iec_tsi", "float"),
    ("iec_class_tvi", "int"),
    ("iec_class_tsi", "int"),
)

WRI_HEADER_BYTES = 100
WRP_BLOCK_BYTES = 64
WRP_MAGIC = 1001
WRP_VERSION = 2

_WRP_HEADER = struct.Struct("<HHBB")
_WRP_DIMS = struct.Struct("<HHIH")
_WRP_BLOCK = struct.Struct("<HfhfdIqBBH")


class MatrixExpandError(Exception):
    """Base class for expander failures."""


class WriFormatError(MatrixExpandError):
    """The .wri file does not match the reverse-engineered schema."""


class WriNotFrequencyData(MatrixExpandError):
    """The .wri schema cannot produce a frequency matrix (fail closed)."""


class WrpFormatError(MatrixExpandError):
    """The .wrp file does not match the reverse-engineered binary layout."""


# --------------------------------------------------------------------------
# Numeric helpers
# --------------------------------------------------------------------------


def _round(value: float, ndigits: int, single: bool = True) -> float:
    """Match MTDFunctionsMath.Round on a .NET Single/Double.

    .NET's Convert.ToDecimal(Single) keeps 7 significant digits before
    Math.Round; that is why 0.817499995 is exported as 0.818, not 0.817.
    Double values (coordinates) keep their full decimal representation.
    """

    if not math.isfinite(value):
        raise MatrixExpandError(f"cannot format non-finite value {value!r}")
    raw = Decimal(str(float(value)))
    if single:
        with localcontext() as context:
            context.prec = 7
            raw = +raw
    quantum = Decimal(1).scaleb(-ndigits)
    return float(raw.quantize(quantum, rounding=ROUND_HALF_UP, context=Context(prec=28)))


def _format_number(value: float) -> str:
    """Invariant, no-exponent number formatting used by the GUI writer."""
    value = float(value)
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_fixed(value: float, ndigits: int, single: bool = True) -> str:
    return _format_number(_round(value, ndigits, single=single))


def _natural_key(label: str) -> tuple:
    return tuple(
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", label)
    )


def _decode_label(raw: bytes) -> str:
    """Decode a fixed-width WRP label (ASCII, UTF-8 or legacy CJK ANSI)."""

    raw = raw.rstrip(b"\x00 ")
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1").strip()


# --------------------------------------------------------------------------
# .wri: IEC terrain-complexity summary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WriRecord:
    is_computed: bool
    iec_edition: int
    label: str
    uid: int
    direction_deg: float
    iec_radius_m: float
    iec_slope: float
    iec_terrain_variation: float
    energy_part: float
    iec_tvi: float
    iec_tsi: float
    iec_class_tvi: int
    iec_class_tsi: int


@dataclass
class WriFile:
    path: Path
    version: int
    point_count: int
    records: list[WriRecord]

    @property
    def labels(self) -> list[str]:
        seen: list[str] = []
        for record in self.records:
            if record.label not in seen:
                seen.append(record.label)
        return seen

    def by_label(self, label: str) -> list[WriRecord]:
        rows = [record for record in self.records if record.label == label]
        if not rows:
            raise WriFormatError(f"label {label!r} is not present in {self.path}")
        return rows

    def explain(self) -> str:
        directions = sorted({record.direction_deg for record in self.records})
        radii = sorted({record.iec_radius_m for record in self.records})
        return (
            f"{self.path.name}: version={self.version}, points={self.point_count}, "
            f"records={len(self.records)}, directions={directions}, radii={radii}. "
            "Columns = " + ", ".join(name for name, _ in WRI_COLUMNS) + "."
        )

    def frequency_matrix(self, label: str | None = None) -> FrequencyMatrix:
        """Fail closed: the .wri schema has no frequency or Weibull sense."""

        raise WriNotFrequencyData(
            "The .wri file is an IEC terrain-complexity summary, not a "
            "low-resolution frequency matrix. MTDWriterWRResultIEC.Write "
            "(MTDWRServices.dll, IL RVA 0x30aa8) writes exactly 13 columns: "
            "isComputed, IECEdition, Label, UID, Direction, IECRadius, "
            "IECSlope, IECTerrainVariation, EnergyPart, IECTvi, IECTsi, "
            "IECClassTvi, IECClassTsi. It contains no Frequency (data sense 9), "
            "Weibull A/K (42/43) or mean wind speed (44). Use the companion "
            ".wrp with open_wrp()/FrequencyMatrix.from_wrp()."
        )


def _write_text(path: Path, text: str) -> None:
    """Atomically replace a text export so a crash never leaves a half file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8-sig", newline="\n")
        os.replace(temporary, path)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _read_text(path: Path) -> str:
    """Read a MUP text export, tolerating UTF-8 BOM and legacy CJK ANSI."""

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MatrixExpandError(f"cannot read {path}: {exc}") from exc
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise MatrixExpandError(f"{path}: text is neither UTF-8 nor GB18030")


def parse_wri(path: str | Path) -> WriFile:
    """Parse the real 13-column .wri schema; raise WriFormatError on drift."""

    path = Path(path)
    try:
        text = _read_text(path)
    except MatrixExpandError as exc:
        raise WriFormatError(str(exc)) from exc

    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        raise WriFormatError(f"{path}: expected version and point count lines")

    try:
        version = int(lines[0].strip())
        point_count = int(lines[1].strip())
    except ValueError as exc:
        raise WriFormatError(f"{path}: invalid version/point-count header") from exc
    if point_count <= 0:
        raise WriFormatError(f"{path}: point count must be positive")

    records: list[WriRecord] = []
    for offset, line in enumerate(lines[2:], start=3):
        parts = line.split("\t")
        if len(parts) != len(WRI_COLUMNS):
            raise WriFormatError(
                f"{path}:{offset}: expected {len(WRI_COLUMNS)} tab columns, "
                f"found {len(parts)}"
            )
        try:
            records.append(
                WriRecord(
                    is_computed=parts[0].strip().lower() == "true",
                    iec_edition=int(parts[1]),
                    label=parts[2].strip(),
                    uid=int(parts[3]),
                    direction_deg=float(parts[4]),
                    iec_radius_m=float(parts[5]),
                    iec_slope=float(parts[6]),
                    iec_terrain_variation=float(parts[7]),
                    energy_part=float(parts[8]),
                    iec_tvi=float(parts[9]),
                    iec_tsi=float(parts[10]),
                    iec_class_tvi=int(parts[11]),
                    iec_class_tsi=int(parts[12]),
                )
            )
        except ValueError as exc:
            raise WriFormatError(f"{path}:{offset}: {exc}") from exc

    directions = {record.direction_deg for record in records}
    radii = {record.iec_radius_m for record in records}
    expected = point_count * len(directions) * len(radii)
    if len(records) != expected:
        raise WriFormatError(
            f"{path}: expected {expected} records "
            f"({point_count} points x {len(directions)} directions x {len(radii)} radii), "
            f"found {len(records)}"
        )

    expected_combinations = {(direction, radius) for direction in directions for radius in radii}
    grouped: dict[str, list[WriRecord]] = {}
    seen: dict[str, set[tuple[float, float]]] = {}
    for record in records:
        if not record.label:
            raise WriFormatError(f"{path}: empty point label")
        for name in (
            "direction_deg",
            "iec_radius_m",
            "iec_slope",
            "iec_terrain_variation",
            "energy_part",
            "iec_tvi",
            "iec_tsi",
        ):
            if not math.isfinite(getattr(record, name)):
                raise WriFormatError(f"{path}: non-finite {name} for {record.label}")
        grouped.setdefault(record.label, []).append(record)
        key = (record.direction_deg, record.iec_radius_m)
        bucket = seen.setdefault(record.label, set())
        if key in bucket:
            raise WriFormatError(
                f"{path}: duplicate record for {record.label} "
                f"direction={key[0]} radius={key[1]}"
            )
        bucket.add(key)

    if len(grouped) != point_count:
        raise WriFormatError(
            f"{path}: header declares {point_count} points but {len(grouped)} labels were found"
        )
    for label, combinations in seen.items():
        if combinations != expected_combinations:
            missing = expected_combinations - combinations
            extra = combinations - expected_combinations
            raise WriFormatError(
                f"{path}: {label} does not cover the full direction/radius grid "
                f"(missing={sorted(missing)[:4]}, extra={sorted(extra)[:4]})"
            )

    return WriFile(path=path, version=version, point_count=point_count, records=records)


def expand_from_wri(path: str | Path) -> FrequencyMatrix:
    """Present for API symmetry; always raises WriNotFrequencyData."""

    wri = parse_wri(path)
    return wri.frequency_matrix()


# --------------------------------------------------------------------------
# .wrp: binary WRP objects
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class WrpBlock:
    index: int
    sense: int
    height: float
    direction_raw: int
    speed: float
    probability: float
    offset: int
    dtype: int
    value_type: int
    label_width: int


_DTYPE_WIDTH = {0: 4, 1: 8, 2: 1, 3: 2, 4: 4}


@dataclass
class WrpFile:
    path: Path
    file_type: int
    version: int
    h_unit: int
    v_unit: int
    projection: str
    nb_directions: int
    nb_windspeeds: int
    nb_points: int
    nb_blocks: int
    labels: list[str]
    _data: bytes = field(repr=False)
    blocks: list[WrpBlock] = field(default_factory=list)
    _index: dict[tuple[int, int, int], WrpBlock] = field(default_factory=dict, repr=False)

    # -- construction -----------------------------------------------------

    @property
    def direction_step(self) -> float:
        return 360.0 / self.nb_directions

    def direction_key(self, raw: int) -> int:
        if raw < 0:
            return -1
        index = int(round(raw / self.direction_step))
        return 0 if index == self.nb_directions else index

    def speed_key(self, raw: float) -> int:
        if not math.isfinite(raw):
            raise WrpFormatError(f"{self.path}: non-finite speed {raw!r}")
        return -1 if raw < 0 else int(round(raw))

    @classmethod
    def open(cls, path: str | Path) -> WrpFile:
        path = Path(path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise WrpFormatError(f"cannot read {path}: {exc}") from exc
        if len(data) < WRI_HEADER_BYTES:
            raise WrpFormatError(f"{path}: file shorter than {WRI_HEADER_BYTES} bytes")

        file_type, version, h_unit, v_unit = _WRP_HEADER.unpack_from(data, 0)
        projection = data[6:36].decode("latin1", errors="replace").strip("\x00 ")
        nb_directions, nb_windspeeds, nb_points, nb_blocks = _WRP_DIMS.unpack_from(data, 36)

        if file_type != WRP_MAGIC:
            raise WrpFormatError(f"{path}: unexpected file type {file_type}, expected {WRP_MAGIC}")
        if version != WRP_VERSION:
            raise WrpFormatError(f"{path}: unsupported WRP version {version}")
        for name, value in (
            ("directions", nb_directions),
            ("windspeeds", nb_windspeeds),
            ("points", nb_points),
            ("blocks", nb_blocks),
        ):
            if value <= 0:
                raise WrpFormatError(f"{path}: {name} count must be positive, found {value}")
        if nb_windspeeds < 2:
            raise WrpFormatError(
                f"{path}: windspeed count {nb_windspeeds} leaves no GUI speed bins"
            )

        expected = WRI_HEADER_BYTES + nb_blocks * WRP_BLOCK_BYTES
        if len(data) < expected:
            raise WrpFormatError(
                f"{path}: truncated block table, need {expected} bytes, found {len(data)}"
            )

        blocks: list[WrpBlock] = []
        for index in range(nb_blocks):
            offset = WRI_HEADER_BYTES + index * WRP_BLOCK_BYTES
            (
                sense,
                height,
                direction,
                speed,
                probability,
                _idgroup,
                data_offset,
                dtype,
                value_type,
                label_width,
            ) = _WRP_BLOCK.unpack_from(data, offset)
            if data_offset < expected:
                raise WrpFormatError(
                    f"{path}: block {index} payload offset {data_offset} points into "
                    f"the {expected}-byte header/table area"
                )
            blocks.append(
                WrpBlock(
                    index=index,
                    sense=sense,
                    height=height,
                    direction_raw=direction,
                    speed=speed,
                    probability=probability,
                    offset=data_offset,
                    dtype=dtype,
                    value_type=value_type,
                    label_width=label_width,
                )
            )

        wrp = cls(
            path=path,
            file_type=file_type,
            version=version,
            h_unit=h_unit,
            v_unit=v_unit,
            projection=projection,
            nb_directions=nb_directions,
            nb_windspeeds=nb_windspeeds,
            nb_points=nb_points,
            nb_blocks=nb_blocks,
            labels=[],
            _data=data,
            blocks=blocks,
        )
        index: dict[tuple[int, int, int], WrpBlock] = {}
        for block in blocks:
            key = (
                block.sense,
                wrp.direction_key(block.direction_raw),
                wrp.speed_key(block.speed),
            )
            previous = index.get(key)
            if previous is not None:
                raise WrpFormatError(
                    f"{path}: duplicate block key sense={key[0]} direction={key[1]} "
                    f"speed={key[2]} at blocks {previous.index} and {block.index}; "
                    "this layout variant (for example a height dimension) is not supported"
                )
            index[key] = block
        wrp._index = index
        wrp.labels = wrp._read_labels()
        if len(set(wrp.labels)) != len(wrp.labels):
            raise WrpFormatError(f"{path}: duplicate point labels {wrp.labels}")
        return wrp

    # -- payload ----------------------------------------------------------

    def _payload_width(self, block: WrpBlock) -> int:
        if block.dtype == 5:
            if block.label_width <= 0:
                raise WrpFormatError(
                    f"{self.path}: block {block.index} has string dtype but "
                    f"label width {block.label_width}"
                )
            return block.label_width
        try:
            return _DTYPE_WIDTH[block.dtype]
        except KeyError as exc:
            raise WrpFormatError(
                f"{self.path}: block {block.index} has unknown dtype {block.dtype}"
            ) from exc

    def _read_block(self, block: WrpBlock) -> list:
        width = self._payload_width(block)
        end = block.offset + self.nb_points * width
        if block.offset < 0 or end > len(self._data):
            raise WrpFormatError(
                f"{self.path}: block {block.index} payload [{block.offset}:{end}] "
                f"outside {len(self._data)}-byte file"
            )
        raw = self._data[block.offset:end]
        if block.dtype == 5:
            return [
                _decode_label(raw[i * width:(i + 1) * width])
                for i in range(self.nb_points)
            ]
        if block.dtype == 0:
            return list(struct.unpack_from(f"<{self.nb_points}f", raw))
        if block.dtype == 1:
            return list(struct.unpack_from(f"<{self.nb_points}d", raw))
        if block.dtype == 2:
            return list(raw)
        if block.dtype == 3:
            return list(struct.unpack_from(f"<{self.nb_points}h", raw))
        if block.dtype == 4:
            return list(struct.unpack_from(f"<{self.nb_points}i", raw))
        raise WrpFormatError(f"{self.path}: unsupported dtype {block.dtype}")

    def _read_labels(self) -> list[str]:
        candidates = [block for block in self.blocks if block.sense == DATA_SENSE["label"]]
        if not candidates:
            raise WrpFormatError(f"{self.path}: missing label block (sense 45)")
        block = next(
            (candidate for candidate in candidates if candidate.direction_raw < 0 and candidate.speed < 0),
            candidates[0],
        )
        labels = self._read_block(block)
        if len(labels) != self.nb_points:
            raise WrpFormatError(
                f"{self.path}: label block produced {len(labels)} values "
                f"for {self.nb_points} points"
            )
        return [str(label) for label in labels]

    # -- lookup -----------------------------------------------------------

    def block(self, sense: int, direction: int = -1, speed: float = -1.0) -> WrpBlock:
        key = (sense, -1 if direction < 0 else self.direction_key(direction),
               self.speed_key(speed))
        try:
            return self._index[key]
        except KeyError as exc:
            available = [k for k in self._index if k[0] == sense]
            raise WrpFormatError(
                f"{self.path}: no block for sense {sense}, direction {direction}, "
                f"speed {speed}; {len(available)} blocks exist for this sense"
            ) from exc

    def values(self, sense: int, direction: int = -1, speed: float = -1.0) -> list:
        return self._read_block(self.block(sense, direction, speed))

    def label_index(self, label: str) -> int:
        try:
            return self.labels.index(label)
        except ValueError as exc:
            raise WrpFormatError(
                f"{self.path}: label {label!r} not found; labels={self.labels}"
            ) from exc

    def wind_turbine_labels(self) -> list[str]:
        try:
            flags = self.values(DATA_SENSE["is_wind_turbine"])
        except WrpFormatError:
            return list(self.labels)
        return [label for label, flag in zip(self.labels, flags) if int(flag)]

    # -- derived products -------------------------------------------------

    def _optional_value(self, sense: int, index: int, default=None):
        try:
            return float(self.values(sense)[index])
        except (WrpFormatError, ValueError, IndexError):
            return default

    def metadata(self, label: str) -> dict:
        index = self.label_index(label)
        uid = self._optional_value(DATA_SENSE["uid"], index)
        rotor = self._optional_value(DATA_SENSE["rotor_radius"], index)
        turbine = self._optional_value(DATA_SENSE["is_wind_turbine"], index, default=1.0)
        return {
            "label": label,
            "uid": int(round(uid)) if uid is not None else None,
            "x": float(self.values(DATA_SENSE["coordinate_x"])[index]),
            "y": float(self.values(DATA_SENSE["coordinate_y"])[index]),
            "z": self._optional_value(DATA_SENSE["coordinate_z"], index, default=0.0),
            "h": float(self.values(DATA_SENSE["coordinate_h"])[index]),
            "mean_wind_speed": float(self.values(DATA_SENSE["wind_speed"])[index]),
            "rotor_radius": rotor,
            "is_wind_turbine": bool(int(turbine)),
        }

    def frequency_by_direction(self, label: str) -> list[float]:
        """Directional frequency in percent (sums to 100)."""

        index = self.label_index(label)
        out = []
        for direction in range(self.nb_directions):
            angle = direction * self.direction_step
            out.append(float(self.values(DATA_SENSE["frequency"], angle, -1.0)[index]) * 100.0)
        return out

    def mean_speed_by_direction(self, label: str) -> list[float]:
        index = self.label_index(label)
        return [
            float(self.values(DATA_SENSE["wind_speed"], direction * self.direction_step, -1.0)[index])
            for direction in range(self.nb_directions)
        ]

    def frequency_matrix(self, label: str) -> FrequencyMatrix:
        index = self.label_index(label)
        speeds = tuple(range(1, self.nb_windspeeds))
        matrix: list[list[float]] = []
        for direction in range(self.nb_directions):
            angle = direction * self.direction_step
            row: list[float] = []
            for speed in speeds:
                value = float(self.values(DATA_SENSE["frequency"], angle, float(speed))[index])
                if not math.isfinite(value):
                    raise WrpFormatError(
                        f"{self.path}: non-finite frequency for {label} "
                        f"direction={angle:g} speed={speed}"
                    )
                row.append(value)
            matrix.append(row)
        return FrequencyMatrix(
            label=label,
            values=matrix,
            direction_count=self.nb_directions,
            speed_bin_count=len(speeds),
        )

    def turbulence_matrix(self, label: str) -> TurbulenceMatrix:
        """Replicate MTDWRExportMatrix.ResultMatrixTurbulence (IL RVA 0x34694)."""

        index = self.label_index(label)
        speeds = range(1, self.nb_windspeeds)

        def block(sense: int, gate: bool = True, zero_sense: int | None = None) -> TurbulenceBlock:
            directions: list[list[float]] = []
            for direction in range(self.nb_directions):
                angle = direction * self.direction_step
                column: list[float] = []
                for speed in speeds:
                    frequency = _round(
                        float(self.values(DATA_SENSE["frequency"], angle, float(speed))[index]), 3
                    )
                    value = _round(
                        float(self.values(sense, angle, float(speed))[index]), 3
                    )
                    if gate and abs(frequency) < 1e-5:
                        value = -1.0
                    column.append(value)
                directions.append(column)

            mean: list[float] = []
            fallback_sense = zero_sense if zero_sense is not None else sense
            for speed_index, speed in enumerate(speeds):
                if any(directions[direction][speed_index] >= 0 for direction in range(self.nb_directions)):
                    mean.append(
                        _round(
                            float(self.values(fallback_sense, -1, float(speed))[index]), 3
                        )
                    )
                else:
                    mean.append(-1.0)
            return TurbulenceBlock(values=directions, mean=mean)

        edition = int(round(float(self.values(DATA_SENSE["iec_edition"], -1, -1.0)[index])))
        ambient = block(DATA_SENSE["turbulence_ambient"])
        added = block(DATA_SENSE["turbulence_added"])
        total = block(DATA_SENSE["turbulence_total"])

        iec: TurbulenceBlock | None
        try:
            iec = block(
                DATA_SENSE["turbulence_iec"],
                gate=False,
                zero_sense=DATA_SENSE["turbulence_effective"] if edition != 0 else DATA_SENSE["turbulence_characteristic"],
            )
        except WrpFormatError:
            iec = None

        mean_strong = _round(float(self.values(DATA_SENSE["turbulence_total_strong"], -1, -1.0)[index]), 3)
        direction_strong = [
            _round(
                float(self.values(DATA_SENSE["turbulence_total_strong"], direction * self.direction_step, -1.0)[index]), 3
            )
            for direction in range(self.nb_directions)
        ]

        meta = self.metadata(label)
        return TurbulenceMatrix(
            label=label,
            x=meta["x"],
            y=meta["y"],
            z=meta["z"],
            h=meta["h"],
            mean_total_strong=mean_strong,
            direction_total_strong=direction_strong,
            ambient=ambient,
            added=added,
            total=total,
            iec=iec,
            iec_edition=edition,
            direction_count=self.nb_directions,
            speed_bin_count=len(speeds),
        )

    def render_turbulence_gui_text(self, labels: Sequence[str] | None = None) -> str:
        if labels is None:
            selected = self.wind_turbine_labels()
            selected.sort(key=_natural_key)
        else:
            selected = list(labels)
        if not selected:
            raise WrpFormatError(f"{self.path}: no wind-turbine labels to render")
        blocks = [self.turbulence_matrix(label).render_block() for label in selected]
        return "\n".join(blocks) + "\n"

    def render_gui_text(self, labels: Sequence[str] | None = None) -> str:
        if labels is None:
            selected = self.wind_turbine_labels()
            selected.sort(key=_natural_key)
        else:
            selected = list(labels)
        if not selected:
            raise WrpFormatError(f"{self.path}: no wind-turbine labels to render")
        blocks = [self._render_block(label) for label in selected]
        return "\n".join(blocks) + "\n"

    def _render_block(self, label: str) -> str:
        index = self.label_index(label)
        meta = self.metadata(label)
        lines: list[str] = [
            f"Label = {label}",
            f"X (m)= {_format_fixed(meta['x'], 1, single=False)}",
            f"Y (m)= {_format_fixed(meta['y'], 1, single=False)}",
            f"H (m)= {_format_fixed(meta['h'], 1, single=False)}",
            f"Mean wind speed (m/s) = {_format_fixed(meta['mean_wind_speed'], 2)}",
        ]
        directions = range(self.nb_directions)
        speeds = range(1, self.nb_windspeeds)

        header = "Dir (deg)".ljust(12)
        for direction in directions:
            header += (_format_number(direction * self.direction_step) + "\u00b0").ljust(12)
        lines.append(header)

        speed_line = "Speed (m/s)".ljust(12)
        for direction in directions:
            value = self.values(DATA_SENSE["wind_speed"], direction * self.direction_step, -1.0)[index]
            speed_line += _format_fixed(float(value), 2).ljust(12)
        lines.append(speed_line)

        freq_line = "Freq (%)".ljust(12)
        for direction in directions:
            value = self.values(DATA_SENSE["frequency"], direction * self.direction_step, -1.0)[index]
            freq_line += _format_fixed(float(value) * 100.0, 2).ljust(12)
        lines.append(freq_line)

        for speed in speeds:
            line = str(speed).ljust(12)
            for direction in directions:
                value = self.values(DATA_SENSE["frequency"], direction * self.direction_step, float(speed))[index]
                line += _format_fixed(float(value), 2).ljust(12)
            lines.append(line)

        lines.extend(["", "-" * 200, ""])
        return "\n".join(lines)


def open_wrp(path: str | Path) -> WrpFile:
    return WrpFile.open(path)


# --------------------------------------------------------------------------
# MUP Vault discovery: the user never needs to know where the .wrp lives
# --------------------------------------------------------------------------

_SYNTHESIS_RE = re.compile(r"_synthesis_(\d+)\.wrp$", re.IGNORECASE)


def discover_vault_root(explicit: str | Path | None = None) -> Path:
    """Resolve the MUP `local_*` Vault root using the project's single owner."""

    if explicit:
        root = Path(explicit).expanduser()
        if (root / "SYNTHESIS_FILES").is_dir():
            return root
        locals_ = sorted(root.glob("local_*"), key=lambda path: path.stat().st_mtime, reverse=True)
        for candidate in locals_:
            if (candidate / "SYNTHESIS_FILES").is_dir():
                return candidate
        raise MatrixExpandError(f"{root}: no SYNTHESIS_FILES directory found")

    try:
        import mup_paths  # project-owned resolver (env -> ini -> drive -> default)

        resolved = mup_paths.vault_root()
        if resolved and (Path(resolved) / "SYNTHESIS_FILES").is_dir():
            return Path(resolved)
    except Exception:
        pass

    bases: list[Path] = []
    for key in ("MUP_VAULT_ROOT", "MUP_DATA_DIR"):
        value = os.environ.get(key)
        if value:
            bases.append(Path(value))
    for drive in ("H:", "C:", "D:", "E:"):
        bases.append(Path(drive) / "ProgramData" / "Meteodyn" / "MUP")
    bases.append(Path.home() / "AppData" / "Roaming" / "Meteodyn" / "MUP")
    for base in bases:
        if not base.is_dir():
            continue
        if (base / "SYNTHESIS_FILES").is_dir():
            return base
        locals_ = sorted(base.glob("local_*"), key=lambda path: path.stat().st_mtime, reverse=True)
        for candidate in locals_:
            if (candidate / "SYNTHESIS_FILES").is_dir():
                return candidate
    raise MatrixExpandError(
        "MUP Vault not found; set MUP_VAULT_ROOT, pass --vault, or pass --wrp directly"
    )


def discover_wrp(
    case: str | None = None,
    synthesis: int | None = None,
    vault: str | Path | None = None,
) -> Path:
    """Find a synthesis .wrp by CaseGUID/site label without hard-coded paths."""

    root = discover_vault_root(vault)
    synthesis_dir = root / "SYNTHESIS_FILES"
    prefix = ""
    if case:
        text = str(case).strip()
        try:
            from mup_case_identity import is_case_guid, resolve_case_identity

            if is_case_guid(text):
                prefix = text
            else:
                prefix = resolve_case_identity(text, str(root)).case_prefix or ""
        except Exception:
            prefix = text

    candidates = sorted(synthesis_dir.glob(f"{prefix}_synthesis_*.wrp" if prefix else "*_synthesis_*.wrp"))
    if synthesis is not None:
        candidates = [
            path
            for path in candidates
            if (match := _SYNTHESIS_RE.search(path.name)) and int(match.group(1)) == synthesis
        ]
    if not candidates:
        searched = f"case={case!r} synthesis={synthesis!r}" if case or synthesis else "any case"
        raise MatrixExpandError(f"{synthesis_dir}: no .wrp found for {searched}")
    if len(candidates) == 1:
        return candidates[0]

    listing = "\n  ".join(path.name for path in candidates)
    raise MatrixExpandError(
        f"{synthesis_dir}: multiple .wrp candidates; pass --synthesis or --wrp:\n  {listing}"
    )



# --------------------------------------------------------------------------
# Export gates: identity, freshness, format/reconciliation
# --------------------------------------------------------------------------


class GateError(MatrixExpandError):
    """A hard gate failure with a stable reason code."""


@dataclass
class GateReport:
    path: Path
    sha256: str
    mtime: float
    case_expected: str | None
    case_actual: str | None
    synthesis_expected: int | None
    synthesis_actual: int | None
    identity_ok: bool
    freshness_checked: bool
    freshness_ok: bool
    format_ok: bool
    reconcile_ok: bool | None
    reasons: list[str]
    frequency: dict | None = None
    turbulence: dict | None = None

    @property
    def passed(self) -> bool:
        return (
            self.identity_ok
            and self.freshness_checked
            and self.freshness_ok
            and self.format_ok
            and self.reconcile_ok is not False
        )

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "mtime": self.mtime,
            "case_expected": self.case_expected,
            "case_actual": self.case_actual,
            "synthesis_expected": self.synthesis_expected,
            "synthesis_actual": self.synthesis_actual,
            "identity_ok": self.identity_ok,
            "freshness_checked": self.freshness_checked,
            "freshness_ok": self.freshness_ok,
            "format_ok": self.format_ok,
            "reconcile_ok": self.reconcile_ok,
            "reasons": self.reasons,
            "passed": self.passed,
            "frequency": self.frequency,
            "turbulence": self.turbulence,
        }


def _parse_sent_at(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise GateError(f"invalid --sent-at {value!r}; use ISO-8601 or epoch seconds") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(
    path: str | Path,
    case: str | None = None,
    synthesis: int | None = None,
    sent_at=None,
    frequency_gui: str | Path | None = None,
    turbulence_gui: str | Path | None = None,
    tolerance: float = 0.011,
) -> GateReport:
    """Apply the three production gates to one synthesis .wrp.

    Identity   : filename prefix/number must match the requested case/n.
    Freshness  : mtime must be at or after the send timestamp (2 s FS grace).
    Format     : WRP parses, every point passes self_check, and optional GUI
                 gold samples reconcile cell-by-cell.
    """

    path = Path(path)
    if not path.is_file():
        raise GateError(f"{path}: artifact does not exist")
    name = path.name
    match = re.match(r"^(?P<case>.+)_synthesis_(?P<n>\d+)\.wrp$", name, re.IGNORECASE)
    if not match:
        raise GateError(f"{path}: filename is not '<CaseGUID>_synthesis_<n>.wrp'")
    actual_case = match.group("case")
    actual_n = int(match.group("n"))

    reasons: list[str] = []
    expected_case = str(case).strip() if case else None
    if expected_case:
        expected_guid = expected_case
        try:
            from mup_case_identity import is_case_guid, resolve_case_identity

            if not is_case_guid(expected_case):
                vault = path.parent.parent
                expected_guid = resolve_case_identity(expected_case, str(vault)).case_prefix
        except Exception:
            expected_guid = expected_case
        identity_ok = expected_guid.lower() == actual_case.lower()
        if not identity_ok:
            reasons.append(
                f"identity: expected prefix {expected_guid!r}, filename has {actual_case!r}"
            )
    else:
        identity_ok = True
        reasons.append("identity: no expected case supplied; filename prefix accepted as-is")

    synthesis_ok = synthesis is None or int(synthesis) == actual_n
    identity_ok = identity_ok and synthesis_ok
    if synthesis is not None and not synthesis_ok:
        reasons.append(f"identity: expected synthesis {synthesis}, filename has {actual_n}")

    timestamp = _parse_sent_at(sent_at)
    freshness_checked = timestamp is not None
    mtime = path.stat().st_mtime
    if timestamp is None:
        freshness_ok = False
        reasons.append("freshness: no sent_at supplied; cannot certify this artifact")
    else:
        freshness_ok = mtime + 2.0 >= timestamp
        if not freshness_ok:
            reasons.append(
                f"freshness: mtime {mtime:.3f} is older than sent_at {timestamp:.3f}"
            )

    format_ok = True
    frequency_payload = None
    turbulence_payload = None
    try:
        wrp = open_wrp(path)
        checks = self_check(wrp)
        failed = [check.label for check in checks if not check.passed]
        if failed:
            format_ok = False
            reasons.append(f"format: self_check failed for {failed[:5]}")
        if format_ok:
            for label in wrp.wind_turbine_labels():
                wrp.turbulence_matrix(label)
    except MatrixExpandError as exc:
        format_ok = False
        reasons.append(f"format: {exc}")
        wrp = None

    reconcile_ok: bool | None = None
    if format_ok and wrp is not None and (frequency_gui or turbulence_gui):
        reconcile_ok = True
        if frequency_gui:
            report = reconcile(wrp, frequency_gui, tolerance=tolerance)
            frequency_payload = report.to_dict()
            if not report.passed:
                reconcile_ok = False
                reasons.append("reconcile: frequency matrix differs from GUI gold")
        if turbulence_gui:
            report = reconcile_turbulence(wrp, turbulence_gui, tolerance=tolerance)
            turbulence_payload = report.to_dict()
            if not report.passed:
                reconcile_ok = False
                reasons.append("reconcile: turbulence matrix differs from GUI gold")
    elif format_ok:
        reasons.append("reconcile: no GUI gold supplied; format-only certification")

    return GateReport(
        path=path,
        sha256=_sha256(path),
        mtime=mtime,
        case_expected=expected_case,
        case_actual=actual_case,
        synthesis_expected=synthesis,
        synthesis_actual=actual_n,
        identity_ok=identity_ok,
        freshness_checked=freshness_checked,
        freshness_ok=freshness_ok,
        format_ok=format_ok,
        reconcile_ok=reconcile_ok,
        reasons=reasons,
        frequency=frequency_payload,
        turbulence=turbulence_payload,
    )


# --------------------------------------------------------------------------
# Expanded matrix
# --------------------------------------------------------------------------


@dataclass
class FrequencyMatrix:
    label: str
    values: list[list[float]]  # directions x speeds, per-mille
    direction_count: int = DIRECTION_COUNT
    speed_bin_count: int = SPEED_BIN_COUNT

    def __post_init__(self) -> None:
        if len(self.values) != self.direction_count:
            raise MatrixExpandError(
                f"{self.label}: expected {self.direction_count} directions, "
                f"found {len(self.values)}"
            )
        for direction, row in enumerate(self.values):
            if len(row) != self.speed_bin_count:
                raise MatrixExpandError(
                    f"{self.label}: direction {direction} has {len(row)} speeds, "
                    f"expected {self.speed_bin_count}"
                )

    @classmethod
    def from_wrp(cls, wrp: WrpFile, label: str) -> FrequencyMatrix:
        return wrp.frequency_matrix(label)

    def row_sums(self) -> list[float]:
        return [sum(row) for row in self.values]

    def checksum(self) -> float:
        return sum(sum(row) for row in self.values)

    def rows(self) -> Iterable[tuple[float, list[float]]]:
        step = 360.0 / self.direction_count
        for direction, row in zip(range(self.direction_count), self.values):
            yield direction * step, row


@dataclass
class TurbulenceBlock:
    values: list[list[float]]  # directions x speeds
    mean: list[float]  # last column


@dataclass
class TurbulenceMatrix:
    label: str
    x: float
    y: float
    z: float
    h: float
    mean_total_strong: float
    direction_total_strong: list[float]
    ambient: TurbulenceBlock
    added: TurbulenceBlock
    total: TurbulenceBlock
    iec: TurbulenceBlock | None
    iec_edition: int
    direction_count: int = DIRECTION_COUNT
    speed_bin_count: int = SPEED_BIN_COUNT

    def _header(self, last: str, last_width: int) -> str:
        step = 360.0 / self.direction_count
        line = "Dir".ljust(12)
        for direction in range(self.direction_count):
            line += (_format_number(direction * step) + "\u00b0").ljust(12)
        return line + last.ljust(last_width)

    @staticmethod
    def _value(value: float, width: int = 12) -> str:
        return ("-" if value < 0 else _format_fixed(value, 3)).ljust(width)

    def _section(
        self,
        title: str,
        note: str,
        block: TurbulenceBlock,
        last: str,
        last_width: int,
    ) -> list[str]:
        lines = [" " * 12 + title, " " * 12 + note, "", self._header(last, last_width)]
        for speed_index in range(self.speed_bin_count):
            line = str(speed_index + 1).ljust(12)
            for direction in range(self.direction_count):
                line += self._value(block.values[direction][speed_index])
            line += self._value(block.mean[speed_index], last_width)
            lines.append(line)
        lines.append("")
        return lines

    def render_block(self) -> str:
        lines: list[str] = [
            f"Label = {self.label}",
            f"X (m)= {_format_fixed(self.x, 1, single=False)}",
            f"Y (m)= {_format_fixed(self.y, 1, single=False)}",
            f"Z (m)= {_format_fixed(self.z, 1, single=False)}",
            f"H (m)= {_format_fixed(self.h, 1, single=False)}",
            "Mean total turbulence for strong winds",
            " " * 12 + _format_fixed(self.mean_total_strong, 3).ljust(12),
            "",
            "Direction total turbulence for strong winds",
        ]
        direction_line = " " * 12
        for value in self.direction_total_strong:
            direction_line += _format_fixed(value, 3).ljust(12)
        lines.append(direction_line)
        lines.append("")

        note = "Mean takes into account the directional frequency of the bin speed"
        lines.extend(self._section("Ambient turbulence information", note, self.ambient, "Mean", 12))
        lines.extend(self._section("Added turbulence information", note, self.added, "Mean", 12))
        lines.extend(self._section("Total turbulence information", note, self.total, "Mean", 12))

        if self.iec is not None:
            if self.iec_edition != 0:
                title = "IEC turbulence information"
                note = (
                    "Effective turbulence takes into account the directional Weibull "
                    "frequency of the bin speed and the W\u00f6hler exponent"
                )
                last = "Effective turbulence intensity"
                last_width = 30
            else:
                title = "Characteristic turbulence information"
                note = "Mean takes into account the directional frequency of the bin speed"
                last = "Mean"
                last_width = 12
            lines.extend(self._section(title, note, self.iec, last, last_width))

        lines.extend(["-" * 200, ""])
        return "\n".join(lines)

    def render_gui_text(self) -> str:
        return self.render_block() + "\n"



@dataclass
class SelfCheck:
    """Per-label structural certificate that does not need a GUI gold sample."""

    label: str
    directions: int
    speeds: int
    row_sum_min: float
    row_sum_max: float
    all_direction_sum: float
    weibull_mean_rel_err: float
    missing_blocks: int
    weibull_checked: bool = False

    @property
    def passed(self) -> bool:
        return (
            self.missing_blocks == 0
            and self.weibull_checked
            and abs(self.row_sum_min - 1000.0) <= 1.0
            and abs(self.row_sum_max - 1000.0) <= 1.0
            and abs(self.all_direction_sum - 1.0) <= 0.002
            and self.weibull_mean_rel_err <= 0.02
        )

    def to_dict(self) -> dict:
        data = {
            "label": self.label,
            "directions": self.directions,
            "speeds": self.speeds,
            "row_sum_min": self.row_sum_min,
            "row_sum_max": self.row_sum_max,
            "all_direction_sum": self.all_direction_sum,
            "weibull_mean_rel_err": self.weibull_mean_rel_err,
            "missing_blocks": self.missing_blocks,
            "weibull_checked": self.weibull_checked,
            "passed": self.passed,
        }
        return data


def self_check(wrp: WrpFile, labels: Sequence[str] | None = None) -> list[SelfCheck]:
    """Check every point using only internal WRP invariants.

    Catches wrong block indexing, unit changes and truncated cases before any
    GUI export is available.  It is not a substitute for a GUI gold sample.
    """

    selected = list(labels) if labels else wrp.wind_turbine_labels()
    if not selected:
        raise WrpFormatError(f"{wrp.path}: no labels selected for self-check")
    results: list[SelfCheck] = []
    for label in selected:
        index = wrp.label_index(label)
        try:
            matrix = wrp.frequency_matrix(label)
            row_sums = matrix.row_sums()
            all_direction = sum(
                wrp.values(DATA_SENSE["frequency"], -1, float(speed))[index]
                for speed in range(1, wrp.nb_windspeeds)
            )
            a = float(wrp.values(DATA_SENSE["weibull_a"], -1, -1.0)[index])
            k = float(wrp.values(DATA_SENSE["weibull_k"], -1, -1.0)[index])
            mean = float(wrp.values(DATA_SENSE["wind_speed"], -1, -1.0)[index])
            expected = a * math.gamma(1.0 + 1.0 / k) if a > 0 and k > 0 else 0.0
            rel_err = abs(expected - mean) / mean if mean else 1.0
        except (MatrixExpandError, ValueError, ZeroDivisionError, IndexError):
            results.append(
                SelfCheck(
                    label=label,
                    directions=wrp.nb_directions,
                    speeds=max(0, wrp.nb_windspeeds - 1),
                    row_sum_min=0.0,
                    row_sum_max=0.0,
                    all_direction_sum=0.0,
                    weibull_mean_rel_err=1.0,
                    missing_blocks=1,
                    weibull_checked=False,
                )
            )
            continue

        results.append(
            SelfCheck(
                label=label,
                directions=matrix.direction_count,
                speeds=matrix.speed_bin_count,
                row_sum_min=min(row_sums),
                row_sum_max=max(row_sums),
                all_direction_sum=all_direction,
                weibull_mean_rel_err=rel_err,
                missing_blocks=0,
                weibull_checked=True,
            )
        )
    return results





@dataclass
class GuiMatrixBlock:
    label: str
    x: float
    y: float
    h: float
    mean_wind_speed: float
    directions: list[float]
    speed_by_direction: list[float]
    frequency_pct_by_direction: list[float]
    values: list[list[float]]  # 16 x 59, per-mille


@dataclass
class GuiMatrix:
    path: Path
    blocks: list[GuiMatrixBlock]

    def by_label(self) -> dict[str, GuiMatrixBlock]:
        return {block.label: block for block in self.blocks}


def read_gui_matrix(path: str | Path) -> GuiMatrix:
    path = Path(path)
    try:
        lines = _read_text(path).splitlines()
    except MatrixExpandError as exc:
        raise MatrixExpandError(str(exc)) from exc

    starts = [i for i, line in enumerate(lines) if line.startswith("Label")]
    if not starts:
        raise MatrixExpandError(f"{path}: no 'Label' blocks found")

    blocks: list[GuiMatrixBlock] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)

        label = lines[start].split("=", 1)[1].strip()
        x = _parse_meta(lines[start + 1], "X")
        y = _parse_meta(lines[start + 2], "Y")
        h = _parse_meta(lines[start + 3], "H")
        try:
            mean_speed = float(lines[start + 4].split("=", 1)[1])
        except (IndexError, ValueError) as exc:
            raise MatrixExpandError(f"{path}:{start + 5}: invalid mean wind speed line") from exc

        directions = _parse_suffixed_numbers(lines[start + 5])
        direction_count = len(directions)
        speed_row = [float(token) for token in lines[start + 6].split()[2:]]
        freq_row = [float(token) for token in lines[start + 7].split()[2:]]
        if len(speed_row) != direction_count or len(freq_row) != direction_count:
            raise MatrixExpandError(
                f"{path}:{start + 6}: header has {direction_count} directions but "
                f"Speed/Freq rows have {len(speed_row)}/{len(freq_row)} values"
            )

        rows: list[list[str]] = []
        for line in lines[start + 8:end]:
            stripped = line.strip()
            if not stripped or stripped.startswith("-") or stripped.startswith("Label"):
                break
            parts = stripped.split()
            if len(parts) < 1 + direction_count:
                raise MatrixExpandError(
                    f"{path}: speed row {stripped[:16]!r} has {len(parts) - 1} values, "
                    f"expected {direction_count}"
                )
            try:
                speed_label = int(parts[0])
            except ValueError as exc:
                raise MatrixExpandError(
                    f"{path}: invalid speed label {parts[0]!r}"
                ) from exc
            if speed_label != len(rows) + 1:
                raise MatrixExpandError(
                    f"{path}: speed rows are not contiguous: expected "
                    f"{len(rows) + 1}, found {speed_label}"
                )
            rows.append(parts)
        if not rows:
            raise MatrixExpandError(f"{path}:{start + 8}: no speed rows found")
        if direction_count == 0:
            raise MatrixExpandError(f"{path}:{start + 5}: no directions found")

        values: list[list[float]] = [
            [float(rows[speed_index][1 + direction]) for speed_index in range(len(rows))]
            for direction in range(direction_count)
        ]

        blocks.append(
            GuiMatrixBlock(
                label=label,
                x=x,
                y=y,
                h=h,
                mean_wind_speed=mean_speed,
                directions=directions,
                speed_by_direction=speed_row,
                frequency_pct_by_direction=freq_row,
                values=values,
            )
        )

    if len({block.label for block in blocks}) != len(blocks):
        raise MatrixExpandError(f"{path}: duplicate 'Label' blocks")
    return GuiMatrix(path=path, blocks=blocks)


def _parse_meta(line: str, name: str) -> float:
    text = line.replace("\u00a0", " ").replace("\t", " ")
    try:
        return float(text.split("=", 1)[1].strip())
    except (IndexError, ValueError) as exc:
        raise MatrixExpandError(f"invalid {name} header line: {line!r}") from exc


def _parse_suffixed_numbers(line: str) -> list[float]:
    text = line.replace("\u00a0", " ").replace("\u00b0", "")
    try:
        return [float(token) for token in text.split()[2:] if token]
    except ValueError as exc:
        raise MatrixExpandError(f"invalid direction header line: {line!r}") from exc


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------


@dataclass
class LabelReconciliation:
    label: str
    cells: int
    exact: int
    within_tolerance: int
    max_abs_diff: float
    mismatches: list[tuple[int, int, float, float]]
    frequency_row_max_diff: float
    speed_row_max_diff: float
    mean_speed_diff: float
    direction_count: int = DIRECTION_COUNT

    @property
    def passed(self) -> bool:
        return not self.mismatches


@dataclass
class ReconciliationReport:
    labels: list[LabelReconciliation]
    tolerance: float

    @property
    def cells(self) -> int:
        return sum(item.cells for item in self.labels)

    @property
    def exact(self) -> int:
        return sum(item.exact for item in self.labels)

    @property
    def max_abs_diff(self) -> float:
        return max((item.max_abs_diff for item in self.labels), default=0.0)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.labels)

    def to_dict(self) -> dict:
        return {
            "tolerance": self.tolerance,
            "cells": self.cells,
            "exact": self.exact,
            "max_abs_diff": self.max_abs_diff,
            "passed": self.passed,
            "labels": [
                {
                    "label": item.label,
                    "cells": item.cells,
                    "exact": item.exact,
                    "within_tolerance": item.within_tolerance,
                    "max_abs_diff": item.max_abs_diff,
                    "mismatches": item.mismatches,
                    "frequency_row_max_diff": item.frequency_row_max_diff,
                    "speed_row_max_diff": item.speed_row_max_diff,
                    "mean_speed_diff": item.mean_speed_diff,
                    "direction_count": item.direction_count,
                }
                for item in self.labels
            ],
        }


def reconcile(
    wrp: WrpFile,
    gui_path: str | Path,
    tolerance: float = 0.011,
    labels: Sequence[str] | None = None,
) -> ReconciliationReport:
    """Compare every expanded cell with a real GUI matrix export."""

    gui = read_gui_matrix(gui_path)
    selected = list(labels) if labels else [block.label for block in gui.blocks]
    results: list[LabelReconciliation] = []

    for label in selected:
        try:
            expected = gui.by_label()[label]
        except KeyError as exc:
            raise MatrixExpandError(f"{gui.path}: label {label!r} not found") from exc

        matrix = wrp.frequency_matrix(label)
        if (
            len(matrix.values) != len(expected.values)
            or len(matrix.values[0]) != len(expected.values[0])
        ):
            raise MatrixExpandError(
                f"{label}: produced {len(matrix.values)}x{len(matrix.values[0])} but "
                f"reference is {len(expected.values)}x{len(expected.values[0])}"
            )

        mismatches: list[tuple[int, int, float, float]] = []
        exact = 0
        within = 0
        max_diff = 0.0
        for direction in range(len(expected.values)):
            for speed_index in range(len(expected.values[direction])):
                produced = _round(matrix.values[direction][speed_index], 2)
                reference = expected.values[direction][speed_index]
                diff = abs(produced - reference)
                max_diff = max(max_diff, diff)
                if diff == 0.0:
                    exact += 1
                if diff <= tolerance:
                    within += 1
                else:
                    mismatches.append((direction, speed_index + 1, produced, reference))

        freq_diff = max(
            abs(_round(value, 2) - reference)
            for value, reference in zip(
                wrp.frequency_by_direction(label),
                expected.frequency_pct_by_direction,
            )
        )
        speed_diff = max(
            abs(_round(value, 2) - reference)
            for value, reference in zip(
                wrp.mean_speed_by_direction(label),
                expected.speed_by_direction,
            )
        )
        mean_diff = abs(
            _round(wrp.metadata(label)["mean_wind_speed"], 2)
            - expected.mean_wind_speed
        )

        results.append(
            LabelReconciliation(
                label=label,
                cells=sum(len(row) for row in expected.values),
                exact=exact,
                within_tolerance=within,
                max_abs_diff=max_diff,
                mismatches=mismatches,
                frequency_row_max_diff=freq_diff,
                speed_row_max_diff=speed_diff,
                mean_speed_diff=mean_diff,
                direction_count=matrix.direction_count,
            )
        )

    return ReconciliationReport(labels=results, tolerance=tolerance)



@dataclass
class GuiTurbulenceBlock:
    values: list[list[float]]
    mean: list[float]


@dataclass
class GuiTurbulence:
    path: Path
    blocks: dict[str, dict]


def _parse_gui_turbulence_block(path: Path, lines: list[str], start: int, end: int) -> dict:
    label = lines[start].split("=", 1)[1].strip()
    x = _parse_meta(lines[start + 1], "X")
    y = _parse_meta(lines[start + 2], "Y")
    z = _parse_meta(lines[start + 3], "Z")
    h = _parse_meta(lines[start + 4], "H")
    try:
        mean_total = float(lines[start + 6].split()[0])
        direction_total = [float(token) for token in lines[start + 9].split()]
    except (IndexError, ValueError) as exc:
        raise MatrixExpandError(f"{path}:{start + 7}: invalid turbulence header") from exc

    sections: dict[str, GuiTurbulenceBlock] = {}
    for index in range(start, end):
        stripped = lines[index].strip()
        if stripped == "Ambient turbulence information":
            key = "ambient"
        elif stripped == "Added turbulence information":
            key = "added"
        elif stripped == "Total turbulence information":
            key = "total"
        elif stripped in ("IEC turbulence information", "Characteristic turbulence information"):
            key = "iec"
        else:
            continue
        header = lines[index + 3] if index + 3 < end else ""
        direction_count = sum(1 for token in header.split() if token.endswith("\u00b0"))
        if direction_count <= 0:
            raise MatrixExpandError(f"{path}:{index + 4}: invalid turbulence direction header")
        rows: list[list[float]] = []
        offset = 0
        while index + 4 + offset < end:
            row_index = index + 4 + offset
            stripped_row = lines[row_index].strip()
            if not stripped_row or stripped_row.startswith("-"):
                break
            parts = stripped_row.split()
            if not parts[0].isdigit():
                break
            if len(parts) < 1 + direction_count + 1:
                raise MatrixExpandError(
                    f"{path}:{row_index + 1}: expected {direction_count + 1} values"
                )
            rows.append([
                -1.0 if token == "-" else float(token)
                for token in parts[1:1 + direction_count + 1]
            ])
            offset += 1
        if not rows:
            raise MatrixExpandError(f"{path}:{index + 5}: empty turbulence section")
        speed_count = len(rows)
        values = [
            [rows[speed][direction] for speed in range(speed_count)]
            for direction in range(direction_count)
        ]
        sections[key] = GuiTurbulenceBlock(
            values=values,
            mean=[rows[speed][direction_count] for speed in range(speed_count)],
        )

    return {
        "label": label,
        "x": x,
        "y": y,
        "z": z,
        "h": h,
        "mean_total": mean_total,
        "direction_total": direction_total,
        "sections": sections,
    }


def read_gui_turbulence(path: str | Path) -> GuiTurbulence:
    path = Path(path)
    lines = _read_text(path).splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("Label")]
    if not starts:
        raise MatrixExpandError(f"{path}: no 'Label' blocks found")
    blocks: dict[str, dict] = {}
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        parsed = _parse_gui_turbulence_block(path, lines, start, end)
        if parsed["label"] in blocks:
            raise MatrixExpandError(f"{path}: duplicate 'Label' block {parsed['label']!r}")
        blocks[parsed["label"]] = parsed
    return GuiTurbulence(path=path, blocks=blocks)


@dataclass
class TurbulenceReconciliation:
    label: str
    cells: int
    exact: int
    within_tolerance: int
    max_abs_diff: float
    mismatches: list[tuple[str, int, int, float, float]]
    header_max_diff: float

    @property
    def passed(self) -> bool:
        return not self.mismatches


@dataclass
class TurbulenceReport:
    labels: list[TurbulenceReconciliation]
    tolerance: float

    @property
    def cells(self) -> int:
        return sum(item.cells for item in self.labels)

    @property
    def exact(self) -> int:
        return sum(item.exact for item in self.labels)

    @property
    def max_abs_diff(self) -> float:
        return max((item.max_abs_diff for item in self.labels), default=0.0)

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.labels)

    def to_dict(self) -> dict:
        return {
            "tolerance": self.tolerance,
            "cells": self.cells,
            "exact": self.exact,
            "max_abs_diff": self.max_abs_diff,
            "passed": self.passed,
            "labels": [
                {
                    "label": item.label,
                    "cells": item.cells,
                    "exact": item.exact,
                    "within_tolerance": item.within_tolerance,
                    "max_abs_diff": item.max_abs_diff,
                    "mismatches": item.mismatches,
                    "header_max_diff": item.header_max_diff,
                }
                for item in self.labels
            ],
        }


def reconcile_turbulence(
    wrp: WrpFile,
    gui_path: str | Path,
    tolerance: float = 0.0011,
    labels: Sequence[str] | None = None,
) -> TurbulenceReport:
    gui = read_gui_turbulence(gui_path)
    selected = list(labels) if labels else list(gui.blocks)
    results: list[TurbulenceReconciliation] = []

    for label in selected:
        if label not in gui.blocks:
            raise MatrixExpandError(f"{gui.path}: label {label!r} not found")
        expected = gui.blocks[label]
        produced = wrp.turbulence_matrix(label)
        produced_sections = {
            "ambient": produced.ambient,
            "added": produced.added,
            "total": produced.total,
        }
        if produced.iec is not None:
            produced_sections["iec"] = produced.iec

        mismatches: list[tuple[str, int, int, float, float]] = []
        exact = 0
        within = 0
        max_diff = 0.0
        cells = 0
        for key, reference in expected["sections"].items():
            if key not in produced_sections:
                raise MatrixExpandError(f"{label}: missing produced turbulence section {key}")
            source = produced_sections[key]
            if (
                len(source.values) != len(reference.values)
                or len(source.values[0]) != len(reference.values[0])
                or len(source.mean) != len(reference.mean)
            ):
                raise MatrixExpandError(
                    f"{label}/{key}: produced {len(source.values)}x{len(source.values[0])} "
                    f"but reference is {len(reference.values)}x{len(reference.values[0])}"
                )
            for direction in range(len(reference.values)):
                for speed in range(len(reference.values[direction])):
                    produced_value = source.values[direction][speed]
                    reference_value = reference.values[direction][speed]
                    diff = abs(produced_value - reference_value)
                    cells += 1
                    max_diff = max(max_diff, diff)
                    exact += diff == 0
                    within += diff <= tolerance
                    if diff > tolerance:
                        mismatches.append((key, direction, speed + 1, produced_value, reference_value))
            for speed in range(len(reference.mean)):
                diff = abs(source.mean[speed] - reference.mean[speed])
                cells += 1
                max_diff = max(max_diff, diff)
                exact += diff == 0
                within += diff <= tolerance
                if diff > tolerance:
                    mismatches.append((key + ".mean", -1, speed + 1, source.mean[speed], reference.mean[speed]))

        header_diff = max(
            [
                abs(_round(produced.mean_total_strong, 3) - expected["mean_total"]),
                *[
                    abs(_round(a, 3) - b)
                    for a, b in zip(produced.direction_total_strong, expected["direction_total"])
                ],
            ]
        )
        results.append(
            TurbulenceReconciliation(
                label=label,
                cells=cells,
                exact=exact,
                within_tolerance=within,
                max_abs_diff=max_diff,
                mismatches=mismatches,
                header_max_diff=header_diff,
            )
        )
    return TurbulenceReport(labels=results, tolerance=tolerance)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _print_wri_diagnostic(wri: WriFile) -> None:
    print(wri.explain())
    print()
    print("Column schema (MTDWriterWRResultIEC.Write, IL RVA 0x30aa8):")
    for position, (name, kind) in enumerate(WRI_COLUMNS):
        print(f"  {position:2d}  {name:<24} {kind}")
    print()
    print("Cannot expand to a frequency matrix:")
    try:
        wri.frequency_matrix()
    except WriNotFrequencyData as exc:
        print(str(exc))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expand a Meteodyn WRA synthesis object to the GUI 16 x 59 matrix."
    )
    parser.add_argument("--wri", help="low-resolution .wri (IEC summary; diagnostic only)")
    parser.add_argument("--wrp", help="binary .wrp synthesis object (frequency source)")
    parser.add_argument("--case", help="CaseGUID or site label; find its .wrp in the MUP Vault")
    parser.add_argument("--synthesis", type=int, help="synthesis number when a case has several")
    parser.add_argument("--vault", help="MUP local_* Vault root (auto-discovered by default)")
    parser.add_argument("--matrix", help="reference GUI *_Frequency_Matrix.txt for reconciliation")
    parser.add_argument("--turb-out", help="write the turbulence matrix text to this path")
    parser.add_argument(
        "--turb-matrix",
        help="reference GUI *_TurbulenceIntensityTotal_Matrix.txt for reconciliation",
    )
    parser.add_argument("--out", help="write the rendered GUI text to this path")
    parser.add_argument("--label", action="append", default=None, help="limit to one label")
    parser.add_argument("--tolerance", type=float, default=0.011, help="per-cell tolerance")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument(
        "--certify",
        action="store_true",
        help="run internal invariants (no GUI matrix needed) and exit 0/3",
    )
    parser.add_argument(
        "--gate",
        action="store_true",
        help="apply identity/freshness/format gates (requires --sent-at)",
    )
    parser.add_argument(
        "--sent-at",
        help="send timestamp (ISO-8601 or epoch seconds) for the freshness gate",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.tolerance < 0:
        parser.error("--tolerance must be non-negative")
    if args.certify and (args.out or args.matrix or args.turb_out or args.turb_matrix or args.gate):
        parser.error("--certify cannot be combined with export or reconciliation options")
    if args.gate and (args.out or args.turb_out):
        parser.error("--gate is verification-only; do not combine with --out/--turb-out")
    if args.wri and args.wrp:
        print("[NOTE] --wri is diagnostic only; expanding from --wrp", file=sys.stderr)

    if not args.wrp and (args.case or args.vault or args.synthesis is not None):
        try:
            args.wrp = str(
                discover_wrp(case=args.case, synthesis=args.synthesis, vault=args.vault)
            )
            print(f"[OK] discovered {args.wrp}")
        except MatrixExpandError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2

    if args.wri and not args.wrp:
        try:
            _print_wri_diagnostic(parse_wri(args.wri))
        except MatrixExpandError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        return 2

    if not args.wrp:
        parser.error("one of --wrp, --case, or --vault is required")

    try:
        wrp = open_wrp(args.wrp)
    except MatrixExpandError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 2

    labels = args.label
    if args.gate:
        try:
            gate = verify_artifact(
                args.wrp,
                case=args.case,
                synthesis=args.synthesis,
                sent_at=args.sent_at,
                frequency_gui=args.matrix,
                turbulence_gui=args.turb_matrix,
                tolerance=args.tolerance,
            )
        except MatrixExpandError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps(gate.to_dict(), indent=2))
        else:
            print("=== EXPORT GATES ===")
            print(f"  path={gate.path}")
            print(f"  identity={'PASS' if gate.identity_ok else 'FAIL'}")
            print(
                f"  freshness={'PASS' if gate.freshness_ok else 'FAIL'}"
                f"{'' if gate.freshness_checked else ' (no --sent-at)'}"
            )
            print(f"  format={'PASS' if gate.format_ok else 'FAIL'}")
            print(
                "  reconcile="
                + ("not-run" if gate.reconcile_ok is None else ("PASS" if gate.reconcile_ok else "FAIL"))
            )
            for reason in gate.reasons:
                print(f"  - {reason}")
            print(f"  sha256={gate.sha256}")
        return 0 if gate.passed else 3

    if args.certify:
        try:
            checks = self_check(wrp, labels)
        except MatrixExpandError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps([check.to_dict() for check in checks], indent=2))
        else:
            print("=== WRP SELF-CHECK (no GUI gold required) ===")
            for check in checks:
                print(
                    f"  {check.label}: {check.directions}x{check.speeds} "
                    f"row_sum=[{check.row_sum_min:.2f},{check.row_sum_max:.2f}] "
                    f"all_dir={check.all_direction_sum:.6f} "
                    f"weibull_err={check.weibull_mean_rel_err:.4f} "
                    f"{'PASS' if check.passed else 'FAIL'}"
                )
        return 0 if all(check.passed for check in checks) else 3

    status = 0
    wants_frequency_text = args.out or (
        not args.matrix and not args.turb_out and not args.turb_matrix
    )
    if wants_frequency_text:
        try:
            text = wrp.render_gui_text(labels)
            if args.out:
                try:
                    _write_text(Path(args.out), text)
                except OSError as exc:
                    print(f"[FAIL] cannot write {args.out}: {exc}", file=sys.stderr)
                    return 2
                print(f"[OK] wrote {args.out}")
            else:
                print(text, end="")
        except MatrixExpandError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2

    frequency_report = None
    if args.matrix:
        try:
            frequency_report = reconcile(
                wrp, args.matrix, tolerance=args.tolerance, labels=labels
            )
        except MatrixExpandError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        if not frequency_report.passed:
            status = 3

    turbulence_report = None
    if args.turb_out:
        try:
            turbulence_text = wrp.render_turbulence_gui_text(labels)
            _write_text(Path(args.turb_out), turbulence_text)
        except (MatrixExpandError, OSError) as exc:
            print(f"[FAIL] cannot write {args.turb_out}: {exc}", file=sys.stderr)
            return 2
        print(f"[OK] wrote {args.turb_out}")

    if args.turb_matrix:
        try:
            turbulence_report = reconcile_turbulence(
                wrp, args.turb_matrix, tolerance=args.tolerance, labels=labels
            )
        except MatrixExpandError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 2
        if not turbulence_report.passed:
            status = 3

    if args.json:
        payload = {}
        if frequency_report is not None:
            payload["frequency"] = frequency_report.to_dict()
        if turbulence_report is not None:
            payload["turbulence"] = turbulence_report.to_dict()
        if payload:
            print(json.dumps(payload, indent=2))
    else:
        if frequency_report is not None:
            print("\n=== FREQUENCY MATRIX RECONCILIATION ===")
            print(
                f"cells={frequency_report.cells} exact={frequency_report.exact} "
                f"max_abs_diff={frequency_report.max_abs_diff}"
            )
            for item in frequency_report.labels:
                print(
                    f"  {item.label}: exact={item.exact}/{item.cells} "
                    f"within={item.within_tolerance} max={item.max_abs_diff:g} "
                    f"freq_max={item.frequency_row_max_diff:g} "
                    f"speed_max={item.speed_row_max_diff:g} "
                    f"mean_diff={item.mean_speed_diff:g} "
                    f"{'PASS' if item.passed else 'FAIL'}"
                )
                for direction, speed, produced, reference in item.mismatches[:5]:
                    angle = direction * 360.0 / item.direction_count
                    print(
                        f"    dir={angle:g} speed={speed} "
                        f"produced={produced:g} reference={reference:g}"
                    )
        if turbulence_report is not None:
            print("\n=== TURBULENCE MATRIX RECONCILIATION ===")
            print(
                f"cells={turbulence_report.cells} exact={turbulence_report.exact} "
                f"max_abs_diff={turbulence_report.max_abs_diff}"
            )
            for item in turbulence_report.labels:
                print(
                    f"  {item.label}: exact={item.exact}/{item.cells} "
                    f"within={item.within_tolerance} max={item.max_abs_diff:g} "
                    f"header_max={item.header_max_diff:g} "
                    f"{'PASS' if item.passed else 'FAIL'}"
                )
                for section, direction, speed, produced, reference in item.mismatches[:5]:
                    print(
                        f"    {section} dir={direction} speed={speed} "
                        f"produced={produced:g} reference={reference:g}"
                    )

    return status


if __name__ == "__main__":
    raise SystemExit(main())
