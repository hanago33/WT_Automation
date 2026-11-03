"""Tests for the P1 data-equivalent Excel synthesizer."""

from __future__ import annotations

import sys
from pathlib import Path

import openpyxl
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mup_excel_synth as x  # noqa: E402

VAULT = Path(
    r"H:\ProgramData\Meteodyn\MUP\local_liao_be917e43-a15c-4db7-8573-822cecb16f78"
    r"\SYNTHESIS_FILES"
)
WRP_PATH = VAULT / "ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_2.wrp"
S2_PATH = Path(
    r"E:\My_RF_Project\RepresentYearAnalyst\data\demo_wt\DEMO (1)_S2.xlsx"
)
IEC_PATH = Path(
    r"E:\My_RF_Project\RepresentYearAnalyst\data\demo_wt\DEMO (1)_S2_IEC.xlsx"
)

requires_real = pytest.mark.skipif(
    not (WRP_PATH.exists() and S2_PATH.exists()),
    reason="real DEMO .wrp / S2 workbook fixtures are not available",
)


@requires_real
def test_direction_sheets_match_mup_workbook(tmp_path: Path):
    wrp = x.open_wrp(WRP_PATH)
    metadata = x.read_metadata_from_xlsx(S2_PATH)
    output = x.build_direction_workbook(wrp, tmp_path / "s2.xlsx", metadata)
    diff = x.reconcile_direction_workbook(output, S2_PATH)
    assert diff.cells == 16 * 33 * 9
    assert diff.diffs == 0
    assert diff.max_numeric_diff == 0.0


@requires_real
def test_workbook_has_average_and_sixteen_directions(tmp_path: Path):
    wrp = x.open_wrp(WRP_PATH)
    metadata = x.read_metadata_from_xlsx(S2_PATH)
    output = x.build_direction_workbook(wrp, tmp_path / "s2.xlsx", metadata)
    workbook = openpyxl.load_workbook(output, data_only=True, read_only=True)
    try:
        assert workbook.sheetnames == ["平均"] + [
            f"{index * 22.5:g}" for index in range(16)
        ]
        sheet = workbook["0"]
        assert sheet.max_row == 9
        assert sheet.max_column == 33
        assert sheet.cell(2, 5).value == "标签"
        assert sheet.cell(4, 1).value == "testWTT"
    finally:
        workbook.close()


@requires_real
def test_average_table_matches_mup_workbook(tmp_path: Path):
    wrp = x.open_wrp(WRP_PATH)
    metadata = x.read_metadata_from_xlsx(S2_PATH)
    output = x.build_direction_workbook(wrp, tmp_path / "s2.xlsx", metadata)

    produced = openpyxl.load_workbook(output, data_only=True, read_only=True)["平均"]
    reference = openpyxl.load_workbook(S2_PATH, data_only=True, read_only=True)["平均"]
    diffs = []
    try:
        for row in range(35, 47):
            for column in range(1, 47):
                left = produced.cell(row, column).value
                right = reference.cell(row, column).value
                if isinstance(left, (int, float)) and isinstance(right, (int, float)):
                    if abs(float(left) - float(right)) > 1e-9:
                        diffs.append((row, column, left, right))
                elif str(left or "") != str(right or ""):
                    diffs.append((row, column, left, right))
    finally:
        produced.parent.close()
        reference.parent.close()

    assert diffs == []


@requires_real
def test_cli_builds_and_reconciles(tmp_path: Path, capsys):
    output = tmp_path / "s2_cli.xlsx"
    code = x.main([
        "--wrp", str(WRP_PATH),
        "--out", str(output),
        "--metadata-from-xlsx", str(S2_PATH),
        "--reconcile", str(S2_PATH),
    ])
    assert code == 0
    assert output.exists()
    captured = capsys.readouterr()
    assert "diffs=0" in captured.out


def test_metadata_dataclass_defaults():
    meta = x.SiteMetadata()
    cells = meta.to_cells()
    assert len(cells) == 12
    assert cells[0] == ""
    assert cells[11] == "-"


@requires_real
def test_iec_numeric_sheets_match_mup_workbook(tmp_path: Path):
    if not IEC_PATH.exists():
        pytest.skip("IEC workbook fixture is not available")
    wrp = x.open_wrp(WRP_PATH)
    output = x.build_iec_workbook(wrp, tmp_path / "iec.xlsx")
    diff = x.reconcile_iec_workbook(output, IEC_PATH)
    assert diff.cells == 4 * 23 * 7
    assert diff.diffs == 0
    assert diff.max_numeric_diff == 0.0


@requires_real
def test_iec_workbook_contains_four_p1_sheets(tmp_path: Path):
    if not IEC_PATH.exists():
        pytest.skip("IEC workbook fixture is not available")
    wrp = x.open_wrp(WRP_PATH)
    output = x.build_iec_workbook(wrp, tmp_path / "iec.xlsx")
    workbook = openpyxl.load_workbook(output, data_only=True, read_only=True)
    try:
        assert len(workbook.sheetnames) == 4
        assert workbook.sheetnames[0].startswith("IEC4.0")
        sheet = workbook[workbook.sheetnames[0]]
        assert sheet.cell(1, 1).value == "风速（米/秒）"
        assert sheet.cell(1, 2).value == "W1"
        assert sheet.cell(2, 1).value == 2
    finally:
        workbook.close()


@requires_real
def test_cli_iec_export_and_reconcile(tmp_path: Path, capsys):
    if not IEC_PATH.exists():
        pytest.skip("IEC workbook fixture is not available")
    output = tmp_path / "iec_cli.xlsx"
    code = x.main([
        "--wrp", str(WRP_PATH),
        "--iec-out", str(output),
        "--iec-reconcile", str(IEC_PATH),
    ])
    assert code == 0
    captured = capsys.readouterr()
    assert "diffs=0" in captured.out
