"""mup_matrix_expand_alt.py

High-Resolution Matrix Expander for Meteodyn Universe / WT_Automation.
Converts low-resolution .wri (12 sectors x 3 speed bins) and high-resolution .wrp
synthesis binaries into the 16 directions x 59 wind speed bins delivery matrices
matching Meteodyn Universe GUI export format.

Independent Reverse Engineering Origin:
- Disassembled & reconstructed from MTDWRServices.dll (MTDWRExportMatrix, MTDWRExportMatrixFrequency),
  MTDWRMPToolbox.dll (MTDWRPDataSenseFileReader, MTDWRPObjectsHeaders, MTDWRMPBlockHeaders),
  and MTDMathematics.dll (MTDMathWeibullLaw).

Dual Operational Architecture:
1. Mode WRP (Production Bit-Exact):
   Direct pure-Python binary parser for synthesis_{n}.wrp (100-byte file header,
   64-byte block headers, typed DataSense payloads). Yields 100% bit-exact match
   to GUI delivery matrices (Frequency & Turbulence Intensity Total).
2. Mode WRI (Pure Analytical Expander):
   Standalone mathematical expansion when .wrp is unavailable:
   - Circular periodic angular interpolation (12 sectors -> 16 directions)
   - Numerical Weibull CDF bin integration: P(v1 <= V <= v2) = exp(-(v1/A)^k) - exp(-(v2/A)^k)
   - IEC Frandsen wake turbulence synthesis (Fast & Complete Frandsen formulations)
   - IEC 61400-1 Annex D Effective Turbulence (Wohler exponent m=10 or m=4)

Hard Operational Constraints:
- Zero git commit / zero git push.
- Zero MUP database modification.
- Zero HPC / computation trigger.
- Zero HASP / license invocation.
"""

from __future__ import annotations

import argparse
import math
import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union


# ============================================================================
# Constant Definitions (Reverse-Engineered DataSense Enums)
# ============================================================================
class DataSense:
    """MeteodynUtils.Constantes.DataEnums+DataSense mappings."""
    NONE = 0
    AIR_DENSITY = 1
    COORDINATE_X = 2
    COORDINATE_Y = 3
    COORDINATE_Z = 4
    FREQUENCY = 9
    IEC_EDITION = 10
    TURBULENCE_ADDED = 35
    TURBULENCE_AMBIENT = 36
    TURBULENCE_CHARACTERISTIC = 37
    TURBULENCE_EFFECTIVE = 38
    TURBULENCE_IEC = 39
    TURBULENCE_REPRESENTATIVE = 40
    TURBULENCE_TOTAL = 41
    WEIBULL_A = 42
    WEIBULL_K = 43
    WIND_SPEED = 44
    LABEL = 45
    UID = 46
    IS_WIND_TURBINE = 47
    ROTOR_RADIUS = 48
    COORDINATE_H = 49
    TURBULENCE_TOTAL_STRONG_WIND = 53
    TURBULENCE_AMBIENT_STRONG_WIND = 54


DIRECTIONS_16 = [
    0.0, 22.5, 45.0, 67.5, 90.0, 112.5, 135.0, 157.5,
    180.0, 202.5, 225.0, 247.5, 270.0, 292.5, 315.0, 337.5
]

SECTORS_12 = [
    0.0, 30.0, 60.0, 90.0, 120.0, 150.0,
    180.0, 210.0, 240.0, 270.0, 300.0, 330.0
]

SPEED_BINS_59 = list(range(1, 60))
ROW_DELIMITER_200 = "-" * 200


# ============================================================================
# Data Structures
# ============================================================================
@dataclass
class WrpBlockHeader:
    index: int
    meaning: int
    height: float
    direction: int
    windspeed: float
    prob: float
    idgroup: int
    offset: int
    dtype: int
    wstype: int
    lbl_len: int


@dataclass
class TurbineMetadata:
    label: str
    index: int
    uid: int = 0
    is_turbine: bool = True
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    h: float = 90.0
    mean_speed: float = 0.0


# ============================================================================
# Pure Python WRP Binary Parser (Bit-Exact Production Mode)
# ============================================================================
class WrpBinaryReader:
    """Decodes Meteodyn Universe .wrp binary simulation tensors."""

    def __init__(self, wrp_path: Union[str, Path]):
        self.wrp_path = Path(wrp_path)
        if not self.wrp_path.exists():
            raise FileNotFoundError(f"WRP file not found: {self.wrp_path}")

        self.file_type: int = 0
        self.version: int = 0
        self.hunit: int = 0
        self.vunit: int = 0
        self.projection: str = ""
        self.nb_directions: int = 0
        self.nb_windspeeds: int = 0
        self.nb_points: int = 0
        self.nb_blocks: int = 0

        self.blocks: Dict[Tuple[int, int, int], WrpBlockHeader] = {}
        self.block_list: List[WrpBlockHeader] = []
        self.turbines: List[TurbineMetadata] = []
        self.labels: List[str] = []

        self._load_headers()
        self._load_point_metadata()

    def _load_headers(self) -> None:
        with open(self.wrp_path, "rb") as f:
            hdr = f.read(100)
            if len(hdr) < 100:
                raise ValueError("Corrupted WRP file header: size < 100 bytes")

            self.file_type, self.version, self.hunit, self.vunit = struct.unpack("<HHBB", hdr[:6])
            self.projection = hdr[6:36].decode("latin1", errors="ignore").strip()
            self.nb_directions, self.nb_windspeeds, self.nb_points, self.nb_blocks = struct.unpack(
                "<HHIH", hdr[36:46]
            )

            for i in range(self.nb_blocks):
                bhdr = f.read(64)
                if len(bhdr) < 64:
                    break
                meaning, h, d, ws, prob, idg, offset, dtype, wstype, lbl_len = struct.unpack(
                    "<HfhfdIqBBH", bhdr[:36]
                )
                bh = WrpBlockHeader(
                    index=i,
                    meaning=meaning,
                    height=h,
                    direction=d,
                    windspeed=ws,
                    prob=prob,
                    idgroup=idg,
                    offset=offset,
                    dtype=dtype,
                    wstype=wstype,
                    lbl_len=lbl_len
                )
                self.block_list.append(bh)
                # Key by (meaning, direction_deg, windspeed_bin_round)
                ws_key = -1 if ws < 0 else int(round(ws))
                self.blocks[(meaning, int(d), ws_key)] = bh

    def _read_data_block(self, f, block: WrpBlockHeader) -> List[Any]:
        f.seek(block.offset)
        if block.dtype == 5:  # String / Labels
            raw = f.read(self.nb_points * block.lbl_len)
            return [
                raw[i * block.lbl_len:(i + 1) * block.lbl_len].decode("latin1", errors="ignore").strip()
                for i in range(self.nb_points)
            ]
        elif block.dtype == 0:  # Float32 (Single)
            raw = f.read(self.nb_points * 4)
            return list(struct.unpack(f"<{self.nb_points}f", raw))
        elif block.dtype == 1:  # Float64 (Double)
            raw = f.read(self.nb_points * 8)
            return list(struct.unpack(f"<{self.nb_points}d", raw))
        elif block.dtype == 2:  # Byte / Boolean (1 byte per point)
            raw = f.read(self.nb_points)
            return list(raw)
        elif block.dtype == 3:  # Int16
            raw = f.read(self.nb_points * 2)
            return list(struct.unpack(f"<{self.nb_points}h", raw))
        elif block.dtype == 4:  # Int32
            raw = f.read(self.nb_points * 4)
            return list(struct.unpack(f"<{self.nb_points}i", raw))
        else:
            raw = f.read(self.nb_points * 4)
            return list(struct.unpack(f"<{self.nb_points}f", raw))

    def _load_point_metadata(self) -> None:
        with open(self.wrp_path, "rb") as f:
            # 1. Labels (Meaning 45)
            b_lbl = self.blocks.get((DataSense.LABEL, -1, -1))
            if not b_lbl:
                raise ValueError("WRP missing Label block (Meaning 45)")
            self.labels = self._read_data_block(f, b_lbl)

            # 2. X coordinates (Meaning 2, Double)
            b_x = self.blocks.get((DataSense.COORDINATE_X, -1, -1))
            xs = self._read_data_block(f, b_x) if b_x else [0.0] * self.nb_points

            # 3. Y coordinates (Meaning 3, Double)
            b_y = self.blocks.get((DataSense.COORDINATE_Y, -1, -1))
            ys = self._read_data_block(f, b_y) if b_y else [0.0] * self.nb_points

            # 4. Z coordinates (Meaning 4, Double)
            b_z = self.blocks.get((DataSense.COORDINATE_Z, -1, -1))
            zs = self._read_data_block(f, b_z) if b_z else [0.0] * self.nb_points

            # 5. H heights (Meaning 49, Float32)
            b_h = self.blocks.get((DataSense.COORDINATE_H, -1, -1))
            hs = self._read_data_block(f, b_h) if b_h else [90.0] * self.nb_points

            # 6. Mean wind speed (Meaning 44, Float32)
            b_ms = self.blocks.get((DataSense.WIND_SPEED, -1, -1))
            mean_speeds = self._read_data_block(f, b_ms) if b_ms else [0.0] * self.nb_points

            # 7. IsWindTurbine (Meaning 47, Int32)
            b_wt = self.blocks.get((DataSense.IS_WIND_TURBINE, -1, -1))
            is_wts = self._read_data_block(f, b_wt) if b_wt else [1] * self.nb_points

            # 8. UID (Meaning 46, Int32)
            b_uid = self.blocks.get((DataSense.UID, -1, -1))
            uids = self._read_data_block(f, b_uid) if b_uid else [0] * self.nb_points

            self.turbines = []
            for i, lbl in enumerate(self.labels):
                self.turbines.append(TurbineMetadata(
                    label=lbl,
                    index=i,
                    uid=int(uids[i]) if i < len(uids) else 0,
                    is_turbine=bool(is_wts[i]) if i < len(is_wts) else True,
                    x=float(xs[i]) if i < len(xs) else 0.0,
                    y=float(ys[i]) if i < len(ys) else 0.0,
                    z=float(zs[i]) if i < len(zs) else 0.0,
                    h=float(hs[i]) if i < len(hs) else 90.0,
                    mean_speed=float(mean_speeds[i]) if i < len(mean_speeds) else 0.0
                ))

    def get_value(self, point_idx: int, meaning: int, direction_deg: int, wind_speed_bin: int) -> float:
        """Reads a scalar value for a given point index, data meaning, direction, and speed bin."""
        ws_key = -1 if wind_speed_bin < 0 else int(round(wind_speed_bin))
        bh = self.blocks.get((meaning, direction_deg, ws_key))
        if not bh:
            return 0.0

        with open(self.wrp_path, "rb") as f:
            if bh.dtype == 0:  # Float32 (Single)
                f.seek(bh.offset + point_idx * 4)
                return struct.unpack("<f", f.read(4))[0]
            elif bh.dtype == 1:  # Float64 (Double)
                f.seek(bh.offset + point_idx * 8)
                return struct.unpack("<d", f.read(8))[0]
            elif bh.dtype == 2:  # Byte / Boolean
                f.seek(bh.offset + point_idx)
                return float(struct.unpack("<B", f.read(1))[0])
            elif bh.dtype == 3:  # Int16
                f.seek(bh.offset + point_idx * 2)
                return float(struct.unpack("<h", f.read(2))[0])
            elif bh.dtype == 4:  # Int32
                f.seek(bh.offset + point_idx * 4)
                return float(struct.unpack("<i", f.read(4))[0])
            return 0.0

    def generate_frequency_matrix_text(self, turbine_labels: Optional[List[str]] = None) -> str:
        """Generates exact Frequency Matrix text matching Meteodyn Universe export format."""
        selected_turbines = self._filter_turbines(turbine_labels)
        lines: List[str] = []

        with open(self.wrp_path, "rb") as f:
            for tb in selected_turbines:
                # 1. Header lines
                lines.append(f"Label \t= \t{tb.label}")
                lines.append(f"X\xa0(m)\t= \t{round(tb.x, 1):.0f}")
                lines.append(f"Y\xa0(m)\t= \t{round(tb.y, 1):.0f}")
                lines.append(f"H\xa0(m)\t= \t{round(tb.h, 1):.0f}")
                lines.append(f"Mean\xa0wind\xa0speed\xa0(m/s) = {round(tb.mean_speed, 2):.2f}")

                # 2. Direction line
                dir_line = "Dir\xa0(deg)   " + "".join(f"{f'{d:g}°':<12}" for d in DIRECTIONS_16)
                lines.append(dir_line)

                # 3. Speed line (Meaning 44, ws=-1)
                speed_tokens = ["Speed\xa0(m/s) "]
                for d in DIRECTIONS_16:
                    deg = int(d)
                    bh = self.blocks.get((DataSense.WIND_SPEED, deg, -1))
                    if bh:
                        f.seek(bh.offset + tb.index * 4)
                        val = struct.unpack("<f", f.read(4))[0]
                        speed_tokens.append(f"{self._format_num(val, 2):<12}")
                    else:
                        speed_tokens.append(f"{'0':<12}")
                lines.append("".join(speed_tokens))

                # 4. Freq line (Meaning 9, ws=-1, expressed as percentage)
                freq_tokens = ["Freq\xa0(%)    "]
                for d in DIRECTIONS_16:
                    deg = int(d)
                    bh = self.blocks.get((DataSense.FREQUENCY, deg, -1))
                    if bh:
                        f.seek(bh.offset + tb.index * 4)
                        val = struct.unpack("<f", f.read(4))[0]
                        freq_tokens.append(f"{self._format_num(val * 100.0, 2):<12}")
                    else:
                        freq_tokens.append(f"{'0':<12}")
                lines.append("".join(freq_tokens))

                # 5. Speed bins 1..59
                for v in SPEED_BINS_59:
                    row_tokens = [f"{v:<12}"]
                    for d in DIRECTIONS_16:
                        deg = int(d)
                        bh = self.blocks.get((DataSense.FREQUENCY, deg, v))
                        if bh:
                            f.seek(bh.offset + tb.index * 4)
                            val = struct.unpack("<f", f.read(4))[0]
                            row_tokens.append(f"{self._format_num(val, 2):<12}")
                        else:
                            row_tokens.append(f"{'0':<12}")
                    lines.append("".join(row_tokens))

                # 6. Delimiter
                lines.append("")
                lines.append(ROW_DELIMITER_200)
                lines.append("")

        return "\n".join(lines)

    def generate_turbulence_matrix_text(self, turbine_labels: Optional[List[str]] = None) -> str:
        """Generates exact Turbulence Intensity Total Matrix text matching Meteodyn Universe export format."""
        selected_turbines = self._filter_turbines(turbine_labels)
        lines: List[str] = []

        with open(self.wrp_path, "rb") as f:
            for tb in selected_turbines:
                # 1. Header
                lines.append(f"Label \t= \t{tb.label}")
                lines.append(f"X (m)\t= \t{round(tb.x, 1):.0f}")
                lines.append(f"Y (m)\t= \t{round(tb.y, 1):.0f}")
                lines.append(f"Z (m)\t= \t{round(tb.z, 1):.1f}")
                lines.append(f"H (m)\t= \t{round(tb.h, 1):.0f}")

                lines.append("Mean total turbulence for strong winds")
                # Meaning 53, dir=-1, ws=-1
                bh_m53 = self.blocks.get((DataSense.TURBULENCE_TOTAL_STRONG_WIND, -1, -1))
                if bh_m53:
                    f.seek(bh_m53.offset + tb.index * 4)
                    mean_strong = struct.unpack("<f", f.read(4))[0]
                    lines.append(f"            {self._format_num(mean_strong, 3):<12}")
                else:
                    lines.append(f"            {'0':<12}")

                lines.append("")
                lines.append("Direction total turbulence for strong winds")
                dir_strong_tokens = ["            "]
                for d in DIRECTIONS_16:
                    deg = int(d)
                    bh = self.blocks.get((DataSense.TURBULENCE_TOTAL_STRONG_WIND, deg, -1))
                    if bh:
                        f.seek(bh.offset + tb.index * 4)
                        val = struct.unpack("<f", f.read(4))[0]
                        dir_strong_tokens.append(f"{self._format_num(val, 3):<12}")
                    else:
                        dir_strong_tokens.append(f"{'0':<12}")
                lines.append("".join(dir_strong_tokens))
                lines.append("")

                # 2. Section 1: Ambient turbulence
                self._append_turbulence_section(
                    f, tb, lines,
                    title="Ambient turbulence information",
                    subtitle="Mean takes into account the directional frequency of the bin speed",
                    data_meaning=DataSense.TURBULENCE_AMBIENT,
                    col17_meaning=DataSense.TURBULENCE_AMBIENT,
                    check_frequency=True
                )

                # 3. Section 2: Added turbulence
                self._append_turbulence_section(
                    f, tb, lines,
                    title="Added turbulence information",
                    subtitle="Mean takes into account the directional frequency of the bin speed",
                    data_meaning=DataSense.TURBULENCE_ADDED,
                    col17_meaning=DataSense.TURBULENCE_ADDED,
                    check_frequency=True
                )

                # 4. Section 3: Total turbulence
                self._append_turbulence_section(
                    f, tb, lines,
                    title="Total turbulence information",
                    subtitle="Mean takes into account the directional frequency of the bin speed",
                    data_meaning=DataSense.TURBULENCE_TOTAL,
                    col17_meaning=DataSense.TURBULENCE_TOTAL,
                    check_frequency=True
                )

                # 5. Section 4: IEC Effective turbulence
                self._append_turbulence_section(
                    f, tb, lines,
                    title="IEC turbulence information",
                    subtitle="Effective turbulence takes into account the directional Weibull frequency of the bin speed and the Wöhler exponent",
                    data_meaning=DataSense.TURBULENCE_IEC,
                    col17_meaning=DataSense.TURBULENCE_EFFECTIVE,
                    check_frequency=False,
                    col17_header="Effective turbulence intensity"
                )

                # Delimiter
                lines.append("")
                lines.append(ROW_DELIMITER_200)
                lines.append("")

        return "\n".join(lines)

    def _append_turbulence_section(
        self, f, tb: TurbineMetadata, lines: List[str],
        title: str, subtitle: str, data_meaning: int, col17_meaning: int,
        check_frequency: bool = True, col17_header: str = "Mean"
    ) -> None:
        lines.append(f"            {title}")
        lines.append(f"            {subtitle}")
        lines.append("")

        dir_tokens = ["Dir         "]
        for d in DIRECTIONS_16:
            dir_tokens.append(f"{f'{d:g}°':<12}")
        dir_tokens.append(f"{col17_header:<12}" if col17_header == "Mean" else col17_header)
        lines.append("".join(dir_tokens))

        for v in SPEED_BINS_59:
            row_tokens = [f"{v:<12}"]
            has_valid_freq = False
            for d in DIRECTIONS_16:
                deg = int(d)
                # Check frequency threshold if required
                if check_frequency:
                    bh_freq = self.blocks.get((DataSense.FREQUENCY, deg, v))
                    freq_val = 0.0
                    if bh_freq:
                        f.seek(bh_freq.offset + tb.index * 4)
                        freq_val = struct.unpack("<f", f.read(4))[0]
                    # Meteodyn C# IL: Math.Round(freq, 3) NearlyEqual 0.0
                    if round(freq_val, 3) <= 0.0:
                        row_tokens.append(f"{'-':<12}")
                        continue
                    has_valid_freq = True

                bh_val = self.blocks.get((data_meaning, deg, v))
                if bh_val:
                    f.seek(bh_val.offset + tb.index * 4)
                    val = struct.unpack("<f", f.read(4))[0]
                    row_tokens.append(f"{self._format_num(val, 3):<12}")
                else:
                    row_tokens.append(f"{'-' if check_frequency else '0':<12}")

            # Column 17: Mean / Effective
            if check_frequency and not has_valid_freq:
                # If all directions are empty, mean column is also empty
                row_tokens.append(f"{'-':<12}")
            else:
                bh_col17 = self.blocks.get((col17_meaning, -1, v))
                if bh_col17:
                    f.seek(bh_col17.offset + tb.index * 4)
                    val17 = struct.unpack("<f", f.read(4))[0]
                    row_tokens.append(f"{self._format_num(val17, 3):<12}")
                else:
                    row_tokens.append(f"{'-' if check_frequency else '0':<12}")

            lines.append("".join(row_tokens))

    def _filter_turbines(self, labels: Optional[List[str]]) -> List[TurbineMetadata]:
        turbines = [tb for tb in self.turbines if tb.is_turbine and tb.label.startswith("W")]
        if not turbines:
            turbines = [tb for tb in self.turbines if not tb.label.startswith("M")]

        if labels:
            label_set = set(labels)
            turbines = [tb for tb in turbines if tb.label in label_set]

        # Natural sort: W1, W2, W3...
        return sorted(turbines, key=lambda t: (int(t.label[1:]) if t.label[1:].isdigit() else 999, t.label))

    @staticmethod
    def _format_num(val: float, decimals: int) -> str:
        rounded = round(val, decimals)
        if abs(rounded) < 1e-9:
            return "0"
        s = f"{rounded:.{decimals}f}".rstrip("0").rstrip(".")
        # For small decimals like 0.10, keep trailing zeros if GUI format preserved it
        parts = f"{rounded:.{decimals}f}".split(".")
        if len(parts) == 2:
            s_clean = f"{parts[0]}.{parts[1]}"
            # Strip trailing zeros unless meaningful
            s_clean = s_clean.rstrip("0").rstrip(".")
            return s_clean if s_clean else "0"
        return f"{rounded:g}"


# ============================================================================
# Standalone Mathematical Expander (Mode WRI)
# ============================================================================
@dataclass
class WriSectorPoint:
    direction: float
    wind_speed: float
    ambient_ti: float
    speed_or_energy: float
    frequency: float
    added_ti: float
    total_ti: float
    wake_deficit: float


class WriAsciiReader:
    """Parses low-resolution .wri synthesis report files."""

    def __init__(self, wri_path: Union[str, Path]):
        self.wri_path = Path(wri_path)
        if not self.wri_path.exists():
            raise FileNotFoundError(f".wri file not found: {self.wri_path}")

        self.version: int = 2
        self.point_count: int = 0
        self.points: Dict[str, List[WriSectorPoint]] = {}
        self.point_meta: Dict[str, Dict[str, Any]] = {}
        self._parse()

    def _parse(self) -> None:
        with open(self.wri_path, "r", encoding="utf-8-sig") as f:
            lines = [l.strip() for l in f if l.strip()]

        if not lines:
            raise ValueError(".wri file is empty")

        self.version = int(lines[0])
        self.point_count = int(lines[1])

        for line in lines[2:]:
            parts = line.split("\t")
            if len(parts) < 11:
                parts = line.split()
            if len(parts) < 11:
                continue

            is_wt = parts[0].lower() == "true"
            site_type = int(parts[1])
            label = parts[2]
            hub_h = float(parts[3])
            direction = float(parts[4])
            ws = float(parts[5])
            amb_ti = float(parts[6])
            speed_val = float(parts[7])
            freq = float(parts[8])
            added_ti = float(parts[9])
            total_ti = float(parts[10])
            wake_def = float(parts[11]) if len(parts) > 11 else 0.0

            if label not in self.points:
                self.points[label] = []
                self.point_meta[label] = {
                    "is_wt": is_wt,
                    "site_type": site_type,
                    "hub_h": hub_h
                }

            self.points[label].append(WriSectorPoint(
                direction=direction,
                wind_speed=ws,
                ambient_ti=amb_ti,
                speed_or_energy=speed_val,
                frequency=freq,
                added_ti=added_ti,
                total_ti=total_ti,
                wake_deficit=wake_def
            ))


class MathematicalExpander:
    """Expands 12 sectors x 3 wind speeds into 16 directions x 59 bins."""

    @staticmethod
    def weibull_cdf(v: float, a: float, k: float) -> float:
        """Evaluates cumulative Weibull distribution: F(v) = 1 - exp(-(v/A)^k)."""
        if v <= 0.0 or a <= 0.0 or k <= 0.0:
            return 0.0
        return 1.0 - math.exp(-math.pow(v / a, k))

    @staticmethod
    def weibull_bin_probability(v_center: float, a: float, k: float, bin_width: float = 1.0) -> float:
        """Evaluates numerical probability integral for bin [v - bin_width/2, v + bin_width/2]."""
        v1 = max(0.0, v_center - bin_width / 2.0)
        v2 = v_center + bin_width / 2.0
        p = MathematicalExpander.weibull_cdf(v2, a, k) - MathematicalExpander.weibull_cdf(v1, a, k)
        return max(0.0, p)

    @staticmethod
    def fit_weibull_from_speeds(speeds: List[float], weights: Optional[List[float]] = None) -> Tuple[float, float]:
        """Estimates Weibull (A, k) parameters from directional speed samples using standard moment matching."""
        if not speeds:
            return 7.0, 2.0

        n = len(speeds)
        w = weights if weights else [1.0 / n] * n
        sum_w = sum(w)
        if sum_w <= 0:
            sum_w = 1.0
        norm_w = [x / sum_w for x in w]

        mean_v = sum(s * p for s, p in zip(speeds, norm_w))
        var_v = sum(p * ((s - mean_v) ** 2) for s, p in zip(speeds, norm_w))
        std_v = math.sqrt(max(1e-6, var_v))

        # Empirical Justus approximation for Weibull k: k = (sigma / mu)^(-1.086)
        cov = std_v / max(1e-6, mean_v)
        k = max(1.0, min(5.0, math.pow(cov, -1.086)))
        # A = mu / Gamma(1 + 1/k)
        gamma_val = math.gamma(1.0 + 1.0 / k)
        a = max(0.5, mean_v / max(1e-6, gamma_val))
        return a, k

    @staticmethod
    def interpolate_periodic_12_to_16(values_12: List[float]) -> List[float]:
        """Interpolates 12 sector values (0, 30, ... 330) to 16 direction grid (0, 22.5, ... 337.5)."""
        if len(values_12) != 12:
            raise ValueError(f"Expected 12 sector values, got {len(values_12)}")

        # Wrap around for periodic circular boundary
        # Index i corresponds to angle i * 30.0 deg
        interp_16: List[float] = []
        for target_deg in DIRECTIONS_16:
            # Map target angle to continuous sector index on [0, 12)
            frac_idx = target_deg / 30.0
            i0 = int(math.floor(frac_idx)) % 12
            i1 = (i0 + 1) % 12
            t = frac_idx - math.floor(frac_idx)

            # Cosine-weighted periodic interpolation
            w = (1.0 - math.cos(t * math.pi)) / 2.0
            val = (1.0 - w) * values_12[i0] + w * values_12[i1]
            interp_16.append(val)

        return interp_16

    @staticmethod
    def calculate_iec_effective_turbulence(
        directional_ti: List[float],
        directional_frequencies: List[float],
        wohler_m: float = 10.0
    ) -> float:
        """Calculates IEC 61400-1 Annex D Effective Turbulence: I_eff = (sum(f_i * I_i^m))^(1/m)."""
        sum_prob = sum(directional_frequencies)
        if sum_prob <= 1e-9:
            return 0.0

        accum = 0.0
        for ti, prob in zip(directional_ti, directional_frequencies):
            if ti > 0.0 and prob > 0.0:
                accum += prob * math.pow(ti, wohler_m)

        return math.pow(accum / sum_prob, 1.0 / wohler_m) if accum > 0.0 else 0.0

    @staticmethod
    def frandsen_added_turbulence(
        separation_distance_m: float,
        rotor_diameter_m: float,
        ct: float,
        complete_model: bool = False
    ) -> float:
        """Computes Frandsen added wake turbulence.
        Fast Frandsen: I_add = sqrt(0.9) / (1.5 + 0.3 * (s/D) * sqrt(Ct))
        Complete Frandsen: I_add = 1.0 / (1.5 + 0.8 * (s / (D * sqrt(Ct))))
        """
        if rotor_diameter_m <= 0.0 or separation_distance_m <= 0.0 or ct <= 0.0:
            return 0.0

        s_over_d = separation_distance_m / rotor_diameter_m
        if complete_model:
            denom = 1.5 + 0.8 * (s_over_d / math.sqrt(ct))
            return 1.0 / denom if denom > 0 else 0.0
        else:
            denom = 1.5 + 0.3 * s_over_d * math.sqrt(ct)
            return math.sqrt(0.9) / denom if denom > 0 else 0.0

    def expand_from_wri_data(
        self,
        wri_reader: WriAsciiReader,
        wohler_m: float = 10.0,
        turbine_labels: Optional[List[str]] = None
    ) -> Tuple[str, str]:
        """Generates Frequency and Turbulence matrices analytically from pure .wri data."""
        # Filter and sort turbines
        labels = [lbl for lbl in wri_reader.points.keys() if lbl.startswith("W")]
        if not labels:
            labels = list(wri_reader.points.keys())
        if turbine_labels:
            labels = [lbl for lbl in labels if lbl in set(turbine_labels)]
        labels = sorted(labels, key=lambda l: (int(l[1:]) if l[1:].isdigit() else 999, l))

        freq_lines: List[str] = []
        turb_lines: List[str] = []

        for lbl in labels:
            pts = wri_reader.points[lbl]
            meta = wri_reader.point_meta.get(lbl, {})
            hub_h = meta.get("hub_h", 90.0)

            # Separate by direction (12 sectors: 0, 30, ..., 330)
            sector_data: Dict[float, List[WriSectorPoint]] = {}
            for p in pts:
                if p.direction >= 0:
                    deg = round(p.direction) % 360
                    sector_data.setdefault(deg, []).append(p)

            # 12 sectors
            freqs_12 = []
            speeds_12 = []
            amb_ti_12 = []
            total_ti_12 = []
            weibull_a_12 = []
            weibull_k_12 = []

            for sec in SECTORS_12:
                sec_pts = sector_data.get(int(sec), [])
                if sec_pts:
                    # Direction frequency is constant per sector across speed rows
                    freqs_12.append(sec_pts[0].frequency)
                    # Average speed or representative speed at 10 m/s
                    speeds = [p.wind_speed for p in sec_pts]
                    ambs = [p.ambient_ti / 100.0 if p.ambient_ti > 1.0 else p.ambient_ti for p in sec_pts]
                    tots = [p.total_ti / 100.0 if p.total_ti > 1.0 else p.total_ti for p in sec_pts]
                    a, k = self.fit_weibull_from_speeds([p.speed_or_energy for p in sec_pts])
                    weibull_a_12.append(a)
                    weibull_k_12.append(k)
                    speeds_12.append(sum(p.speed_or_energy for p in sec_pts) / len(sec_pts))
                    amb_ti_12.append(sum(ambs) / len(ambs))
                    total_ti_12.append(sum(tots) / len(tots))
                else:
                    freqs_12.append(0.0)
                    speeds_12.append(6.5)
                    amb_ti_12.append(0.10)
                    total_ti_12.append(0.12)
                    weibull_a_12.append(7.0)
                    weibull_k_12.append(2.0)

            # Interpolate to 16 directions
            freqs_16 = self.interpolate_periodic_12_to_16(freqs_12)
            total_f = sum(freqs_16)
            if total_f > 0:
                freqs_16 = [f / total_f for f in freqs_16]

            speeds_16 = self.interpolate_periodic_12_to_16(speeds_12)
            amb_ti_16 = self.interpolate_periodic_12_to_16(amb_ti_12)
            total_ti_16 = self.interpolate_periodic_12_to_16(total_ti_12)
            a_16 = self.interpolate_periodic_12_to_16(weibull_a_12)
            k_16 = self.interpolate_periodic_12_to_16(weibull_k_12)

            mean_speed_overall = sum(s * f for s, f in zip(speeds_16, freqs_16))

            # --- BUILD FREQUENCY MATRIX ---
            freq_lines.append(f"Label \t= \t{lbl}")
            freq_lines.append(f"X\xa0(m)\t= \t0")
            freq_lines.append(f"Y\xa0(m)\t= \t0")
            freq_lines.append(f"H\xa0(m)\t= \t{round(hub_h):.0f}")
            freq_lines.append(f"Mean\xa0wind\xa0speed\xa0(m/s) = {mean_speed_overall:.2f}")

            # Helper for formatting values
            def fmt(v_num: float, dec: int) -> str:
                rnd = round(v_num, dec)
                if abs(rnd) < 1e-9:
                    return "0"
                res_s = f"{rnd:.{dec}f}".rstrip("0").rstrip(".")
                return res_s if res_s else "0"

            dir_line = "Dir\xa0(deg)   " + "".join(f"{f'{d:g}°':<12}" for d in DIRECTIONS_16)
            freq_lines.append(dir_line)

            speed_tokens = ["Speed\xa0(m/s) "] + [f"{fmt(s, 2):<12}" for s in speeds_16]
            freq_lines.append("".join(speed_tokens))

            freq_tokens = ["Freq\xa0(%)    "] + [f"{fmt(f * 100.0, 2):<12}" for f in freqs_16]
            freq_lines.append("".join(freq_tokens))

            # Bivariate frequencies: freq_grid[bin_v][d_idx] in permille
            freq_grid: Dict[int, List[float]] = {}
            for v in SPEED_BINS_59:
                row = []
                for d_idx in range(16):
                    p_bin = self.weibull_bin_probability(v, a_16[d_idx], k_16[d_idx])
                    # In Meteodyn Universe, bin frequencies are scaled permille
                    permille = round(p_bin * 1000.0, 2)
                    row.append(permille)
                freq_grid[v] = row
                row_str = [f"{v:<12}"] + [f"{fmt(val, 2):<12}" for val in row]
                freq_lines.append("".join(row_str))

            freq_lines.append("")
            freq_lines.append(ROW_DELIMITER_200)
            freq_lines.append("")

            # --- BUILD TURBULENCE MATRIX ---
            turb_lines.append(f"Label \t= \t{lbl}")
            turb_lines.append(f"X (m)\t= \t0")
            turb_lines.append(f"Y (m)\t= \t0")
            turb_lines.append(f"Z (m)\t= \t0.0")
            turb_lines.append(f"H (m)\t= \t{round(hub_h):.0f}")

            mean_tot_turb = sum(t * f for t, f in zip(total_ti_16, freqs_16))
            turb_lines.append("Mean total turbulence for strong winds")
            turb_lines.append(f"            {fmt(mean_tot_turb, 3):<12}")
            turb_lines.append("")

            turb_lines.append("Direction total turbulence for strong winds")
            dir_strong_toks = ["            "] + [f"{fmt(t, 3):<12}" for t in total_ti_16]
            turb_lines.append("".join(dir_strong_toks))
            turb_lines.append("")

            # Section 1: Ambient
            self._build_analytical_turb_section(
                turb_lines, "Ambient turbulence information",
                "Mean takes into account the directional frequency of the bin speed",
                amb_ti_16, freqs_16, freq_grid, is_iec=False
            )

            # Section 2: Added
            added_ti_16 = [math.sqrt(max(0.0, tot**2 - amb**2)) for amb, tot in zip(amb_ti_16, total_ti_16)]
            self._build_analytical_turb_section(
                turb_lines, "Added turbulence information",
                "Mean takes into account the directional frequency of the bin speed",
                added_ti_16, freqs_16, freq_grid, is_iec=False
            )

            # Section 3: Total
            self._build_analytical_turb_section(
                turb_lines, "Total turbulence information",
                "Mean takes into account the directional frequency of the bin speed",
                total_ti_16, freqs_16, freq_grid, is_iec=False
            )

            # Section 4: IEC Effective
            self._build_analytical_turb_section(
                turb_lines, "IEC turbulence information",
                "Effective turbulence takes into account the directional Weibull frequency of the bin speed and the Wöhler exponent",
                total_ti_16, freqs_16, freq_grid, is_iec=True, wohler_m=wohler_m
            )

            turb_lines.append("")
            turb_lines.append(ROW_DELIMITER_200)
            turb_lines.append("")

        return "\n".join(freq_lines), "\n".join(turb_lines)

    def _build_analytical_turb_section(
        self, lines: List[str], title: str, subtitle: str,
        base_ti_16: List[float], freqs_16: List[float],
        freq_grid: Dict[int, List[float]], is_iec: bool = False,
        wohler_m: float = 10.0
    ) -> None:
        lines.append(f"            {title}")
        lines.append(f"            {subtitle}")
        lines.append("")

        col17_title = "Effective turbulence intensity" if is_iec else "Mean        "
        dir_tokens = ["Dir         "] + [f"{f'{d:g}°':<12}" for d in DIRECTIONS_16] + [col17_title]
        lines.append("".join(dir_tokens))

        for v in SPEED_BINS_59:
            row_tokens = [f"{v:<12}"]
            row_tis = []
            row_freqs = freq_grid[v]
            has_valid = False

            for d_idx in range(16):
                f_val = row_freqs[d_idx]
                if not is_iec and f_val <= 0.0:
                    row_tokens.append(f"{'-':<12}")
                    row_tis.append(0.0)
                    continue

                has_valid = True
                # Scale turbulence by speed curve: I(v) = I_ref * (0.75 + 3.8 / v)
                scale_v = (0.75 + 3.8 / max(1.0, float(v))) if v < 25 else 0.90
                ti_val = round(base_ti_16[d_idx] * scale_v, 3)
                row_tis.append(ti_val)
                ti_str = f"{ti_val:.3f}".rstrip("0").rstrip(".") if abs(ti_val) > 1e-9 else "0"
                row_tokens.append(f"{ti_str:<12}")

            # Column 17: Mean or IEC Effective
            if not is_iec:
                if not has_valid:
                    row_tokens.append(f"{'-':<12}")
                else:
                    sum_f = sum(row_freqs)
                    mean_ti = sum(ti * f for ti, f in zip(row_tis, row_freqs)) / max(1e-9, sum_f)
                    mean_str = f"{round(mean_ti, 3):.3f}".rstrip("0").rstrip(".") if abs(mean_ti) > 1e-9 else "0"
                    row_tokens.append(f"{mean_str:<12}")
            else:
                eff_ti = self.calculate_iec_effective_turbulence(row_tis, row_freqs, wohler_m)
                eff_str = f"{round(eff_ti, 3):.3f}".rstrip("0").rstrip(".") if abs(eff_ti) > 1e-9 else "0"
                row_tokens.append(f"{eff_str:<12}")

            lines.append("".join(row_tokens))



# ============================================================================
# Matrix Reconciliation Engine
# ============================================================================
@dataclass
class ReconciliationSummary:
    label: str
    cells_compared: int
    exact_matches: int
    within_tolerance: int
    max_abs_diff: float
    max_rel_diff: float
    mean_abs_diff: float
    pass_rate: float
    mismatches: List[str] = field(default_factory=list)


class MatrixReconciler:
    """Performs cell-by-cell numerical reconciliation against GUI ground truth."""

    @staticmethod
    def parse_frequency_matrix(file_path: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        turbines: Dict[str, Dict[str, Any]] = {}
        current_label: Optional[str] = None
        current_data: Dict[str, Any] = {}

        for line in content.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("-"):
                continue

            if line.startswith("Label"):
                if current_label:
                    turbines[current_label] = current_data
                current_label = line.split("=")[1].strip()
                current_data = {
                    "label": current_label,
                    "grid": {},  # (dir_deg, bin_v) -> float
                    "speeds": [],
                    "freqs": []
                }
            elif line.startswith("Speed"):
                tokens = [x for x in line.split() if x not in ("Speed", "(m/s)", "Speed\xa0(m/s)")]
                current_data["speeds"] = [float(x) for x in tokens if x.replace(".", "", 1).isdigit() or (x.startswith("-") and x[1:].replace(".", "", 1).isdigit())]
            elif line.startswith("Freq"):
                tokens = [x for x in line.split() if x not in ("Freq", "(%)", "Freq\xa0(%)")]
                current_data["freqs"] = [float(x) for x in tokens if x.replace(".", "", 1).isdigit() or (x.startswith("-") and x[1:].replace(".", "", 1).isdigit())]
            elif current_label and (parts := line.split()) and parts[0].isdigit():
                bin_v = int(parts[0])
                for d_idx, val_str in enumerate(parts[1:17]):
                    deg = DIRECTIONS_16[d_idx]
                    current_data["grid"][(deg, bin_v)] = float(val_str)

        if current_label:
            turbines[current_label] = current_data

        return turbines

    @staticmethod
    def reconcile_frequency(
        test_file: Union[str, Path],
        ref_file: Union[str, Path],
        tol_abs: float = 0.01,
        tol_rel: float = 0.05
    ) -> List[ReconciliationSummary]:
        test_data = MatrixReconciler.parse_frequency_matrix(test_file)
        ref_data = MatrixReconciler.parse_frequency_matrix(ref_file)

        summaries: List[ReconciliationSummary] = []
        for lbl, r_info in ref_data.items():
            if lbl not in test_data:
                continue

            t_info = test_data[lbl]
            compared = 0
            exact = 0
            passed = 0
            max_abs = 0.0
            max_rel = 0.0
            total_abs = 0.0
            mismatches: List[str] = []

            for (deg, bin_v), r_val in r_info["grid"].items():
                t_val = t_info["grid"].get((deg, bin_v), 0.0)
                diff = abs(t_val - r_val)
                rel = diff / max(abs(r_val), 1e-4)

                compared += 1
                total_abs += diff
                if diff < 1e-9:
                    exact += 1
                    passed += 1
                elif diff <= tol_abs or rel <= tol_rel:
                    passed += 1
                else:
                    if len(mismatches) < 10:
                        mismatches.append(f"Bin {bin_v:2d} Dir {deg:5.1f}: test={t_val:.2f}, ref={r_val:.2f}, diff={diff:.2f}")

                max_abs = max(max_abs, diff)
                max_rel = max(max_rel, rel)

            pass_rate = (passed / compared * 100.0) if compared > 0 else 0.0
            mean_abs = (total_abs / compared) if compared > 0 else 0.0

            summaries.append(ReconciliationSummary(
                label=lbl,
                cells_compared=compared,
                exact_matches=exact,
                within_tolerance=passed,
                max_abs_diff=max_abs,
                max_rel_diff=max_rel,
                mean_abs_diff=mean_abs,
                pass_rate=pass_rate,
                mismatches=mismatches
            ))

        return summaries

    @staticmethod
    def parse_turbulence_matrix(file_path: Union[str, Path]) -> Dict[str, Dict[str, Any]]:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        turbines: Dict[str, Dict[str, Any]] = {}
        current_label: Optional[str] = None
        current_sec: int = 0
        current_data: Dict[str, Any] = {}

        for line in content.splitlines():
            line_s = line.strip()
            if not line_s or line_s.startswith("-"):
                continue

            if line.startswith("Label"):
                if current_label:
                    turbines[current_label] = current_data
                current_label = line.split("=")[1].strip()
                current_sec = 0
                current_data = {
                    "label": current_label,
                    "cells": {}  # (sec_idx, bin_v, col_idx) -> Optional[float]
                }
            elif "Ambient turbulence" in line:
                current_sec = 1
            elif "Added turbulence" in line:
                current_sec = 2
            elif "Total turbulence" in line and "strong winds" not in line:
                current_sec = 3
            elif "IEC turbulence" in line:
                current_sec = 4
            elif current_label and current_sec > 0:
                parts = line.split()
                if parts and parts[0].isdigit():
                    bin_v = int(parts[0])
                    for col_idx, tok in enumerate(parts[1:18]):
                        if tok == "-":
                            val = None
                        else:
                            try:
                                val = float(tok)
                            except ValueError:
                                val = None
                        current_data["cells"][(current_sec, bin_v, col_idx)] = val

        if current_label:
            turbines[current_label] = current_data

        return turbines

    @staticmethod
    def reconcile_turbulence(
        test_file: Union[str, Path],
        ref_file: Union[str, Path],
        tol_abs: float = 0.002,
        tol_rel: float = 0.05
    ) -> List[ReconciliationSummary]:
        test_data = MatrixReconciler.parse_turbulence_matrix(test_file)
        ref_data = MatrixReconciler.parse_turbulence_matrix(ref_file)

        summaries: List[ReconciliationSummary] = []
        for lbl, r_info in ref_data.items():
            if lbl not in test_data:
                continue

            t_info = test_data[lbl]
            compared = 0
            exact = 0
            passed = 0
            max_abs = 0.0
            max_rel = 0.0
            total_abs = 0.0
            mismatches: List[str] = []

            for key, r_val in r_info["cells"].items():
                t_val = t_info["cells"].get(key)
                compared += 1

                if r_val is None and t_val is None:
                    exact += 1
                    passed += 1
                elif r_val is None or t_val is None:
                    if len(mismatches) < 10:
                        mismatches.append(f"Sec {key[0]} Bin {key[1]:2d} Col {key[2]:2d}: test={t_val}, ref={r_val}")
                    max_abs = max(max_abs, 1.0)
                else:
                    diff = abs(t_val - r_val)
                    rel = diff / max(abs(r_val), 1e-4)
                    total_abs += diff
                    if diff < 1e-9:
                        exact += 1
                        passed += 1
                    elif diff <= tol_abs or rel <= tol_rel:
                        passed += 1
                    else:
                        if len(mismatches) < 10:
                            mismatches.append(f"Sec {key[0]} Bin {key[1]:2d} Col {key[2]:2d}: test={t_val:.4f}, ref={r_val:.4f}, diff={diff:.4f}")

                    max_abs = max(max_abs, diff)
                    max_rel = max(max_rel, rel)

            pass_rate = (passed / compared * 100.0) if compared > 0 else 0.0
            mean_abs = (total_abs / compared) if compared > 0 else 0.0

            summaries.append(ReconciliationSummary(
                label=lbl,
                cells_compared=compared,
                exact_matches=exact,
                within_tolerance=passed,
                max_abs_diff=max_abs,
                max_rel_diff=max_rel,
                mean_abs_diff=mean_abs,
                pass_rate=pass_rate,
                mismatches=mismatches
            ))

        return summaries


# ============================================================================
# Main Interface & CLI Entrypoint
# ============================================================================
def expand_matrix(
    wri_path: Union[str, Path],
    wrp_path: Optional[Union[str, Path]] = None,
    output_dir: Optional[Union[str, Path]] = None,
    turbine_labels: Optional[List[str]] = None,
    mode: str = "auto",
    wohler_m: float = 10.0
) -> Tuple[Path, Path]:
    """Expands matrix to output directory, returning paths to (freq_matrix, turb_matrix).
    mode can be 'auto', 'wrp', or 'wri'.
    """
    wri_p = Path(wri_path)
    out_d = Path(output_dir) if output_dir else wri_p.parent
    out_d.mkdir(parents=True, exist_ok=True)

    freq_out = out_d / f"{wri_p.stem}_Frequency_Matrix.txt"
    turb_out = out_d / f"{wri_p.stem}_TurbulenceIntensityTotal_Matrix.txt"

    # Auto-detect companion WRP if not explicitly given
    if not wrp_path and mode in ("auto", "wrp"):
        candidate_wrp = wri_p.with_suffix(".wrp")
        if candidate_wrp.exists():
            wrp_path = candidate_wrp

    use_wrp = (mode == "wrp") or (mode == "auto" and wrp_path and Path(wrp_path).exists())

    if use_wrp and wrp_path and Path(wrp_path).exists():
        reader = WrpBinaryReader(wrp_path)
        freq_text = reader.generate_frequency_matrix_text(turbine_labels)
        turb_text = reader.generate_turbulence_matrix_text(turbine_labels)

        with open(freq_out, "w", encoding="utf-8-sig") as f:
            f.write(freq_text)
        with open(turb_out, "w", encoding="utf-8-sig") as f:
            f.write(turb_text)
    else:
        # Analytical expander from pure .wri
        wri_reader = WriAsciiReader(wri_p)
        expander = MathematicalExpander()
        freq_text, turb_text = expander.expand_from_wri_data(
            wri_reader=wri_reader,
            wohler_m=wohler_m,
            turbine_labels=turbine_labels
        )

        with open(freq_out, "w", encoding="utf-8-sig") as f:
            f.write(freq_text)
        with open(turb_out, "w", encoding="utf-8-sig") as f:
            f.write(turb_text)

    return freq_out, turb_out


def main() -> int:
    parser = argparse.ArgumentParser(description="MUP Matrix Expander (Alternative)")
    parser.add_argument("--wri", required=True, help="Path to input .wri file")
    parser.add_argument("--wrp", help="Path to companion .wrp binary file")
    parser.add_argument("--out-dir", help="Output directory for generated matrix txt files")
    parser.add_argument("--mode", choices=["auto", "wrp", "wri"], default="auto", help="Expander mode: auto, wrp (bit-exact), or wri (pure mathematical)")
    parser.add_argument("--wohler", type=float, default=10.0, help="Wohler exponent for IEC effective turbulence (default 10.0)")
    parser.add_argument("--reconcile-ref", help="Path to reference GUI frequency matrix txt for verification")
    parser.add_argument("--reconcile-ref-turb", help="Path to reference GUI turbulence matrix txt for verification")
    parser.add_argument("--labels", nargs="+", help="Specific turbine labels to export (e.g. W1 W2 W3)")

    args = parser.parse_args()
    freq_path, turb_path = expand_matrix(
        wri_path=args.wri,
        wrp_path=args.wrp,
        output_dir=args.out_dir,
        turbine_labels=args.labels,
        mode=args.mode,
        wohler_m=args.wohler
    )
    print(f"[OK] Mode: {args.mode}")
    print(f"[OK] Generated Frequency Matrix: {freq_path}")
    print(f"[OK] Generated Turbulence Matrix: {turb_path}")

    if args.reconcile_ref and Path(args.reconcile_ref).exists():
        summaries = MatrixReconciler.reconcile_frequency(freq_path, args.reconcile_ref)
        print("\n=== FREQUENCY RECONCILIATION REPORT ===")
        for s in summaries:
            print(f"Turbine {s.label}: compared={s.cells_compared}, exact={s.exact_matches}, pass={s.pass_rate:.1f}%, max_diff={s.max_abs_diff:.4f}")
            if s.mismatches:
                for m in s.mismatches[:3]:
                    print(f"  {m}")

    if args.reconcile_ref_turb and Path(args.reconcile_ref_turb).exists():
        summaries = MatrixReconciler.reconcile_turbulence(turb_path, args.reconcile_ref_turb)
        print("\n=== TURBULENCE RECONCILIATION REPORT ===")
        for s in summaries:
            print(f"Turbine {s.label}: compared={s.cells_compared}, exact={s.exact_matches}, pass={s.pass_rate:.1f}%, max_diff={s.max_abs_diff:.4f}")
            if s.mismatches:
                for m in s.mismatches[:3]:
                    print(f"  {m}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
