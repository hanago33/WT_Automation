"""Tests for mup_matrix_expander (REA task #4a).

Real fixtures: the DEMO synthesis_2 .wri/.wrp pair and the GUI-exported
``DEMO (1)_WRA2_Frequency_Matrix.txt``.  Tests that need those files skip when
the local Vault / analysis directories are absent; parser-only tests use
temporary synthetic files.
"""

from __future__ import annotations

import json
import math
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mup_matrix_expander as m  # noqa: E402

VAULT = Path(
    r"H:\ProgramData\Meteodyn\MUP\local_liao_be917e43-a15c-4db7-8573-822cecb16f78"
    r"\SYNTHESIS_FILES"
)
WRI_PATH = VAULT / "ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_2.wri"
WRP_PATH = VAULT / "ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_2.wrp"
GUI_PATH = Path(
    r"E:\My_RF_Project\RepresentYearAnalyst\data\demo_wt"
    r"\DEMO (1)_WRA2_Frequency_Matrix.txt"
)
TURB_PATH = GUI_PATH.with_name("DEMO (1)_WRA2_TurbulenceIntensityTotal_Matrix.txt")

requires_real = pytest.mark.skipif(
    not (WRI_PATH.exists() and WRP_PATH.exists() and GUI_PATH.exists()),
    reason="real DEMO .wri/.wrp/GUI matrix fixtures are not available",
)


# --------------------------------------------------------------------------
# .wri schema
# --------------------------------------------------------------------------


@requires_real
def test_wri_header_and_schema():
    wri = m.parse_wri(WRI_PATH)
    assert wri.version == 2
    assert wri.point_count == 6
    assert len(wri.records) == 6 * 13 * 3
    assert wri.labels == ["W4", "W2", "W5", "W1", "W6", "W3"]
    assert set(wri.labels) == {"W1", "W2", "W3", "W4", "W5", "W6"}
    assert {record.direction_deg for record in wri.records} == {
        -1.0, 0.0, 30.0, 60.0, 90.0, 120.0, 150.0,
        180.0, 210.0, 240.0, 270.0, 300.0, 330.0,
    }
    assert {record.iec_radius_m for record in wri.records} == {5.0, 10.0, 20.0}


@requires_real
def test_wri_first_record_is_iec_summary_not_frequency():
    wri = m.parse_wri(WRI_PATH)
    first = wri.records[0]
    # IL: isComputed, IECEdition, Label, UID, Direction, IECRadius,
    # IECSlope, IECTerrainVariation, EnergyPart, IECTvi, IECTsi,
    # IECClassTvi, IECClassTsi.
    assert first.label == "W4"
    assert first.uid == 89
    assert first.direction_deg == 0.0
    assert first.iec_radius_m == 5.0
    assert first.iec_slope == pytest.approx(24.65299, abs=1e-6)
    assert first.iec_terrain_variation == pytest.approx(4.34057, abs=1e-6)
    assert first.energy_part == pytest.approx(0.5446804, abs=1e-7)
    assert first.iec_class_tvi == 0
    assert first.iec_class_tsi == 0


@requires_real
def test_wri_each_point_has_39_records_and_energy_part_sums_to_one():
    wri = m.parse_wri(WRI_PATH)
    for label in wri.labels:
        rows = wri.by_label(label)
        assert len(rows) == 39
        for radius in (5.0, 10.0, 20.0):
            subset = [
                row.energy_part
                for row in rows
                if row.iec_radius_m == radius and row.direction_deg >= 0
            ]
            assert len(subset) == 12
            assert sum(subset) == pytest.approx(1.0, abs=1e-6)


@requires_real
def test_wri_cannot_expand_to_frequency_matrix():
    wri = m.parse_wri(WRI_PATH)
    with pytest.raises(m.WriNotFrequencyData) as excinfo:
        wri.frequency_matrix()
    message = str(excinfo.value)
    assert "IEC terrain-complexity" in message
    assert "Frequency" in message

    with pytest.raises(m.WriNotFrequencyData):
        m.expand_from_wri(WRI_PATH)


def test_wri_rejects_column_drift(tmp_path: Path):
    path = tmp_path / "bad.wri"
    path.write_text("2\n1\nTrue\t3\tW1\t1\t0\t5\t1\t2\t0.5\n", encoding="utf-8")
    with pytest.raises(m.WriFormatError, match="tab columns"):
        m.parse_wri(path)


def test_wri_rejects_record_count_mismatch(tmp_path: Path):
    path = tmp_path / "short.wri"
    path.write_text(
        "2\n2\n"
        "True\t3\tW1\t1\t0\t5\t1\t2\t0.5\t0\t0\t0\t0\n",
        encoding="utf-8",
    )
    with pytest.raises(m.WriFormatError, match="expected"):
        m.parse_wri(path)


# --------------------------------------------------------------------------
# .wrp layout
# --------------------------------------------------------------------------


@requires_real
def test_wrp_header_and_labels():
    wrp = m.open_wrp(WRP_PATH)
    assert wrp.file_type == m.WRP_MAGIC
    assert wrp.version == m.WRP_VERSION
    assert wrp.nb_directions == 16
    assert wrp.nb_windspeeds == 60
    assert wrp.nb_points == 7
    assert wrp.nb_blocks == 9644
    assert wrp.labels == ["W4", "W2", "W5", "W1", "M1", "W6", "W3"]
    assert wrp.wind_turbine_labels() == ["W4", "W2", "W5", "W1", "W6", "W3"]
    assert wrp.direction_step == 22.5


@requires_real
def test_wrp_metadata_matches_gui_header():
    wrp = m.open_wrp(WRP_PATH)
    meta = wrp.metadata("W1")
    assert meta["x"] == 38556424.0
    assert meta["y"] == 3163553.0
    assert meta["h"] == 90.0
    assert meta["uid"] == 1091
    assert meta["mean_wind_speed"] == pytest.approx(6.5614, abs=1e-3)
    assert meta["rotor_radius"] == 65.5
    assert meta["is_wind_turbine"] is True


@requires_real
def test_frequency_matrix_shape_and_closure():
    wrp = m.open_wrp(WRP_PATH)
    matrix = wrp.frequency_matrix("W1")
    assert len(matrix.values) == 16
    assert all(len(row) == 59 for row in matrix.values)
    for row_sum in matrix.row_sums():
        assert row_sum == pytest.approx(1000.0, abs=1e-2)
    assert matrix.checksum() == pytest.approx(16000.0, abs=1e-2)


@requires_real
def test_all_direction_block_and_directional_blocks_are_consistent():
    wrp = m.open_wrp(WRP_PATH)
    index = wrp.label_index("W1")

    all_direction = sum(
        wrp.values(m.DATA_SENSE["frequency"], -1, float(speed))[index]
        for speed in range(1, 61)
    )
    assert all_direction == pytest.approx(1.0, abs=1e-3)

    for direction in range(16):
        total = sum(
            wrp.values(m.DATA_SENSE["frequency"], direction * 22.5, float(speed))[index]
            for speed in range(1, 61)
        )
        assert total == pytest.approx(1000.0, abs=1e-2)


def test_wrp_rejects_unknown_magic(tmp_path: Path):
    path = tmp_path / "bad.wrp"
    data = bytearray(100 + 64)
    struct.pack_into("<HHBB", data, 0, 1234, 2, 1, 1)
    struct.pack_into("<HHIH", data, 36, 16, 60, 7, 1)
    path.write_bytes(bytes(data))
    with pytest.raises(m.WrpFormatError, match="file type"):
        m.open_wrp(path)


def test_wrp_rejects_truncated_block_table(tmp_path: Path):
    path = tmp_path / "short.wrp"
    data = bytearray(100)
    struct.pack_into("<HHBB", data, 0, m.WRP_MAGIC, 2, 1, 1)
    struct.pack_into("<HHIH", data, 36, 16, 60, 7, 10)
    path.write_bytes(bytes(data))
    with pytest.raises(m.WrpFormatError, match="truncated block table"):
        m.open_wrp(path)


# --------------------------------------------------------------------------
# GUI matrix rendering and cell-by-cell reconciliation
# --------------------------------------------------------------------------


@requires_real
def test_reconcile_all_real_cells():
    wrp = m.open_wrp(WRP_PATH)
    report = m.reconcile(wrp, GUI_PATH)
    assert report.cells == 6 * 16 * 59
    assert report.exact == 5664
    assert report.max_abs_diff == 0.0
    assert report.passed is True

    for item in report.labels:
        assert item.mismatches == []
        assert item.frequency_row_max_diff == 0.0
        assert item.speed_row_max_diff == 0.0
        assert item.mean_speed_diff == 0.0


@requires_real
def test_rendered_frequency_matrix_is_byte_identical_to_mup():
    wrp = m.open_wrp(WRP_PATH)
    produced = b"\xef\xbb\xbf" + wrp.render_gui_text().encode("utf-8")
    assert produced == GUI_PATH.read_bytes()


@requires_real
def test_rendered_block_has_16_by_59_grid_and_separator():
    wrp = m.open_wrp(WRP_PATH)
    lines = wrp.render_gui_text(["W1"]).splitlines()
    assert len(lines) == 5 + 3 + 59 + 3
    assert lines[0] == "Label \t= \tW1"
    assert lines[1] == "X\u00a0(m)\t= \t38556424"
    assert lines[4].startswith("Mean\u00a0wind\u00a0speed\u00a0(m/s) = ")
    assert lines[5].startswith("Dir\u00a0(deg)")
    assert lines[6].startswith("Speed\u00a0(m/s)")
    assert lines[7].startswith("Freq\u00a0(%)")
    assert lines[8].startswith("1 ")
    assert len(lines[8]) == 12 + 16 * 12
    assert lines[-1] == ""
    assert lines[-2] == "-" * 200


@requires_real
def test_self_check_all_real_cases():
    for number, turbines in ((2, 6), (3, 144), (4, 6)):
        path = VAULT / f"ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_{number}.wrp"
        if not path.exists():
            continue
        checks = m.self_check(m.open_wrp(path))
        assert len(checks) == turbines
        assert all(check.passed for check in checks), number


@requires_real
def test_cli_certify_exits_0(capsys):
    code = m.main(["--wrp", str(WRP_PATH), "--certify"])
    assert code == 0
    captured = capsys.readouterr()
    assert "WRP SELF-CHECK" in captured.out
    assert "PASS" in captured.out


@requires_real
def test_other_real_cases_parse_and_close():
    """synthesis_3 (144 turbines) and synthesis_4 must work without code changes."""
    for number, points, turbines in ((3, 146, 144), (4, 7, 6)):
        path = VAULT / f"ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_{number}.wrp"
        if not path.exists():
            continue
        wrp = m.open_wrp(path)
        assert wrp.nb_points == points
        assert len(wrp.wind_turbine_labels()) == turbines

        label = wrp.wind_turbine_labels()[0]
        matrix = wrp.frequency_matrix(label)
        assert matrix.checksum() == pytest.approx(16000.0, abs=1e-2)
        assert all(row_sum == pytest.approx(1000.0, abs=1e-2) for row_sum in matrix.row_sums())

        text = wrp.render_gui_text()
        assert text.count("Label 	= 	") == turbines


def _build_synthetic_wrp(
    path: Path,
    directions: int = 12,
    speeds: int = 6,
    points: int = 2,
    include_weibull: bool = True,
) -> Path:
    """Build a minimal valid WRP with a non-standard 12 x 5 GUI grid."""

    labels = [f"T{i + 1}" for i in range(points)]
    label_width = 8

    blocks: list[tuple[int, int, float, int, bytes]] = []

    def add(sense: int, direction: int, speed: float, dtype: int, payload: bytes) -> None:
        blocks.append((sense, direction, speed, dtype, payload))

    add(45, -1, -1.0, 5, b"".join(label.encode().ljust(label_width, b" ") for label in labels))
    add(46, -1, -1.0, 0, struct.pack(f"<{points}f", *range(1, points + 1)))
    add(47, -1, -1.0, 2, bytes([1] * points))
    add(2, -1, -1.0, 1, struct.pack(f"<{points}d", *[100.0 + i for i in range(points)]))
    add(3, -1, -1.0, 1, struct.pack(f"<{points}d", *[200.0 + i for i in range(points)]))
    add(49, -1, -1.0, 0, struct.pack(f"<{points}f", *[90.0] * points))
    add(48, -1, -1.0, 0, struct.pack(f"<{points}f", *[50.0] * points))
    add(44, -1, -1.0, 0, struct.pack(f"<{points}f", *[5.0] * points))
    if include_weibull:
        # A * Gamma(1 + 1/K) == 5.0 for K=2, so the internal Weibull check passes.
        a = 5.0 / math.gamma(1.5)
        add(42, -1, -1.0, 0, struct.pack(f"<{points}f", *[a] * points))
        add(43, -1, -1.0, 0, struct.pack(f"<{points}f", *[2.0] * points))
    for direction in range(directions):
        add(44, int(360 / directions * direction), -1.0, 0, struct.pack(f"<{points}f", *[5.0] * points))
    for direction in range(directions):
        add(
            9,
            int(360 / directions * direction),
            -1.0,
            0,
            struct.pack(f"<{points}f", *[100.0 / directions] * points),
        )
    add(9, -1, -1.0, 0, struct.pack(f"<{points}f", *[1.0 / (speeds - 1)] * points))
    for direction in range(directions):
        for speed in range(1, speeds):
            add(
                9,
                int(360 / directions * direction),
                float(speed),
                0,
                struct.pack(f"<{points}f", *[1000.0 / (speeds - 1)] * points),
            )

    table_size = m.WRI_HEADER_BYTES + len(blocks) * m.WRP_BLOCK_BYTES
    payloads: list[bytes] = []
    offsets: list[int] = []
    cursor = table_size
    for _sense, _direction, _speed, _dtype, payload in blocks:
        offsets.append(cursor)
        payloads.append(payload)
        cursor += len(payload)

    header = bytearray(m.WRI_HEADER_BYTES)
    struct.pack_into("<HHBB", header, 0, m.WRP_MAGIC, m.WRP_VERSION, 1, 1)
    struct.pack_into("<HHIH", header, 36, directions, speeds, points, len(blocks))

    table = bytearray()
    for (sense, direction, speed, dtype, payload), offset in zip(blocks, offsets):
        width = label_width if dtype == 5 else 0
        table += m._WRP_BLOCK.pack(
            sense, -1.0, direction, speed, 1.0, 0, offset, dtype, 0, width
        )
        table += b"\x00" * (m.WRP_BLOCK_BYTES - m._WRP_BLOCK.size)

    path.write_bytes(bytes(header) + bytes(table) + b"".join(payloads))
    return path


def test_non_standard_grid_is_not_hardcoded(tmp_path: Path):
    wrp_path = _build_synthetic_wrp(tmp_path / "synthetic.wrp")
    wrp = m.open_wrp(wrp_path)
    assert wrp.nb_directions == 12
    assert wrp.nb_windspeeds == 6

    matrix = wrp.frequency_matrix("T1")
    assert matrix.direction_count == 12
    assert matrix.speed_bin_count == 5
    assert matrix.checksum() == pytest.approx(12000.0, abs=1e-3)

    text = wrp.render_gui_text()
    lines = text.splitlines()
    assert lines[5].count("\u00b0") == 12
    assert lines[8].startswith("1 ")
    assert lines[12].startswith("5 ")
    assert not any(line.startswith("6 ") for line in lines)

    gui_path = tmp_path / "synthetic_matrix.txt"
    gui_path.write_text(text, encoding="utf-8-sig")
    parsed = m.read_gui_matrix(gui_path)
    assert len(parsed.blocks[0].directions) == 12
    assert len(parsed.blocks[0].values[0]) == 5

    report = m.reconcile(wrp, gui_path)
    assert report.cells == 2 * 12 * 5
    assert report.exact == report.cells
    assert report.passed is True


@requires_real
def test_cli_wri_diagnostic_exits_2(capsys):
    code = m.main(["--wri", str(WRI_PATH)])
    assert code == 2
    captured = capsys.readouterr()
    assert "IEC terrain-complexity" in captured.out


@requires_real
def test_cli_wrp_reconcile_exits_0(capsys):
    code = m.main(["--wrp", str(WRP_PATH), "--matrix", str(GUI_PATH)])
    assert code == 0
    captured = capsys.readouterr()
    assert "exact=5664" in captured.out
    assert "PASS" in captured.out


@requires_real
def test_cli_wrp_write_output(tmp_path: Path):
    out = tmp_path / "matrix.txt"
    code = m.main(["--wrp", str(WRP_PATH), "--out", str(out), "--label", "W1"])
    assert code == 0
    text = out.read_text(encoding="utf-8-sig")
    assert text.startswith("Label \t= \tW1")
    assert text.count("Label \t= \t") == 1
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in out.read_bytes()


def test_wri_reads_gb18030_labels(tmp_path: Path):
    path = tmp_path / "cn.wri"
    row = "True\t3\t风机1\t1\t0\t5\t1\t2\t0.5\t0\t0\t0\t0\n"
    path.write_bytes(("2\n1\n" + row).encode("gb18030"))
    wri = m.parse_wri(path)
    assert wri.labels == ["风机1"]


def test_wri_rejects_duplicate_and_missing_combinations(tmp_path: Path):
    path = tmp_path / "bad_grid.wri"
    rows = [
        "True\t3\tA\t1\t0\t5\t1\t2\t0.5\t0\t0\t0\t0",
        "True\t3\tA\t1\t0\t5\t1\t2\t0.5\t0\t0\t0\t0",
        "True\t3\tA\t1\t30\t5\t1\t2\t0.5\t0\t0\t0\t0",
        "True\t3\tB\t2\t0\t5\t1\t2\t0.5\t0\t0\t0\t0",
    ]
    path.write_text("2\n2\n" + "\n".join(rows) + "\n", encoding="utf-8")
    with pytest.raises(m.WriFormatError, match="duplicate record"):
        m.parse_wri(path)


def test_wrp_rejects_duplicate_block_keys(tmp_path: Path):
    path = _build_synthetic_wrp(tmp_path / "dup.wrp")
    data = bytearray(path.read_bytes())
    # Block 1 (UID) is retagged as a second Label block with the same key.
    struct.pack_into("<H", data, m.WRI_HEADER_BYTES + m.WRP_BLOCK_BYTES, 45)
    path.write_bytes(bytes(data))
    with pytest.raises(m.WrpFormatError, match="duplicate block key"):
        m.open_wrp(path)


def test_gui_rejects_non_contiguous_speed_rows(tmp_path: Path):
    wrp = m.open_wrp(_build_synthetic_wrp(tmp_path / "synthetic.wrp"))
    lines = wrp.render_gui_text(["T1"]).splitlines()
    lines[10] = re.sub(r"^\d+", "9", lines[10])
    path = tmp_path / "bad_matrix.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(m.MatrixExpandError, match="not contiguous"):
        m.read_gui_matrix(path)


def test_self_check_fails_when_weibull_blocks_are_missing(tmp_path: Path):
    wrp = m.open_wrp(_build_synthetic_wrp(tmp_path / "no_weibull.wrp", include_weibull=False))
    checks = m.self_check(wrp)
    assert checks and all(not check.weibull_checked for check in checks)
    assert all(not check.passed for check in checks)


@requires_real
def test_cli_rejects_negative_tolerance(capsys):
    with pytest.raises(SystemExit) as excinfo:
        m.main(["--wrp", str(WRP_PATH), "--tolerance", "-1"])
    assert excinfo.value.code == 2


@requires_real
def test_explicit_label_order_is_preserved():
    wrp = m.open_wrp(WRP_PATH)
    lines = wrp.render_gui_text(["W3", "W1"]).splitlines()
    assert lines[0] == "Label \t= \tW3"
    assert "Label \t= \tW1" in lines
    assert lines.index("Label \t= \tW3") < lines.index("Label \t= \tW1")


def test_discover_wrp_by_case_guid(tmp_path: Path):
    guid = "11111111-2222-3333-4444-555555555555"
    synthesis = tmp_path / "SYNTHESIS_FILES"
    synthesis.mkdir()
    source = _build_synthetic_wrp(tmp_path / "source.wrp")
    target = synthesis / f"{guid}_synthesis_2.wrp"
    target.write_bytes(source.read_bytes())

    found = m.discover_wrp(case=guid, synthesis=2, vault=tmp_path)
    assert found == target
    assert m.discover_vault_root(tmp_path) == tmp_path


def test_discover_wrp_rejects_ambiguous_case(tmp_path: Path):
    guid = "11111111-2222-3333-4444-555555555555"
    synthesis = tmp_path / "SYNTHESIS_FILES"
    synthesis.mkdir()
    source = _build_synthetic_wrp(tmp_path / "source.wrp").read_bytes()
    for number in (2, 3):
        (synthesis / f"{guid}_synthesis_{number}.wrp").write_bytes(source)

    with pytest.raises(m.MatrixExpandError, match="multiple .wrp candidates"):
        m.discover_wrp(case=guid, vault=tmp_path)


@requires_real
def test_cli_discovers_case_from_real_vault(capsys):
    guid = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
    code = m.main(["--case", guid, "--synthesis", "2", "--certify"])
    assert code == 0
    captured = capsys.readouterr()
    assert "discovered" in captured.out
    assert "PASS" in captured.out


@requires_real
def test_turbulence_reconcile_all_real_cells():
    if not TURB_PATH.exists():
        pytest.skip("turbulence GUI gold sample is not available")
    wrp = m.open_wrp(WRP_PATH)
    report = m.reconcile_turbulence(wrp, TURB_PATH)
    assert report.cells == 6 * 4 * 17 * 59
    assert report.exact == report.cells
    assert report.max_abs_diff == 0.0
    assert report.passed is True
    for item in report.labels:
        assert item.mismatches == []
        assert item.header_max_diff == 0.0


@requires_real
def test_turbulence_render_and_sections():
    if not TURB_PATH.exists():
        pytest.skip("turbulence GUI gold sample is not available")
    wrp = m.open_wrp(WRP_PATH)
    matrix = wrp.turbulence_matrix("W1")
    assert matrix.iec_edition == 3
    assert matrix.iec is not None
    assert len(matrix.ambient.values) == 16
    assert len(matrix.ambient.values[0]) == 59

    rendered = matrix.render_block().splitlines()
    reference = TURB_PATH.read_text(encoding="utf-8-sig").splitlines()
    assert len(rendered) == 268
    assert rendered[0] == "Label \t= \tW1"
    assert rendered[1] == "X (m)\t= \t38556424"
    assert rendered[5] == "Mean total turbulence for strong winds"
    assert rendered[6].strip() == "0.424"

    assert rendered == reference[: len(rendered)]


@requires_real
def test_cli_turbulence_export_and_reconcile(tmp_path: Path, capsys):
    if not TURB_PATH.exists():
        pytest.skip("turbulence GUI gold sample is not available")
    out = tmp_path / "turb.txt"
    code = m.main([
        "--wrp", str(WRP_PATH),
        "--turb-out", str(out),
        "--turb-matrix", str(TURB_PATH),
        "--label", "W1",
    ])
    assert code == 0
    text = out.read_text(encoding="utf-8-sig")
    assert text.startswith("Label \t= \tW1")
    assert text.count("Label \t= \t") == 1
    captured = capsys.readouterr()
    assert "TURBULENCE MATRIX RECONCILIATION" in captured.out
    assert "exact=4012/4012" in captured.out


@requires_real
def test_gate_passes_with_identity_freshness_and_gold():
    if not TURB_PATH.exists():
        pytest.skip("turbulence GUI gold sample is not available")
    guid = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
    sent_at = WRP_PATH.stat().st_mtime - 60.0
    gate = m.verify_artifact(
        WRP_PATH,
        case=guid,
        synthesis=2,
        sent_at=sent_at,
        frequency_gui=GUI_PATH,
        turbulence_gui=TURB_PATH,
    )
    assert gate.identity_ok is True
    assert gate.freshness_checked is True
    assert gate.freshness_ok is True
    assert gate.format_ok is True
    assert gate.reconcile_ok is True
    assert gate.passed is True
    assert len(gate.sha256) == 64
    assert gate.frequency["exact"] == 5664
    assert gate.turbulence["exact"] == 24072


@requires_real
def test_gate_rejects_wrong_synthesis_and_stale_artifact():
    guid = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
    gate = m.verify_artifact(
        WRP_PATH,
        case=guid,
        synthesis=3,
        sent_at=WRP_PATH.stat().st_mtime + 600.0,
    )
    assert gate.identity_ok is False
    assert gate.freshness_ok is False
    assert gate.passed is False
    assert any("synthesis" in reason for reason in gate.reasons)
    assert any("older than sent_at" in reason for reason in gate.reasons)


@requires_real
def test_gate_requires_sent_at():
    gate = m.verify_artifact(WRP_PATH, case="ea368c7f-9810-487d-be4c-bbcc9339c83e", synthesis=2)
    assert gate.freshness_checked is False
    assert gate.passed is False


@requires_real
def test_cli_gate_exit_zero(capsys):
    sent_at = WRP_PATH.stat().st_mtime - 60.0
    code = m.main([
        "--wrp", str(WRP_PATH),
        "--case", "ea368c7f-9810-487d-be4c-bbcc9339c83e",
        "--synthesis", "2",
        "--sent-at", str(sent_at),
        "--gate",
        "--json",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed"] is True
    assert payload["identity_ok"] is True
