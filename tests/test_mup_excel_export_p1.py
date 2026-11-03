"""Integration test for the full P1 Excel exporter (parallel implementation)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mup_excel_export_p1 as p1  # noqa: E402
import mup_matrix_expander as expander  # noqa: E402

VAULT = Path(
    r"H:\ProgramData\Meteodyn\MUP\local_liao_be917e43-a15c-4db7-8573-822cecb16f78"
    r"\SYNTHESIS_FILES"
)
WRP_PATH = VAULT / "ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_2.wrp"
CONTEXT_PATH = ROOT / "_scratch_probes" / "demo_s2_context.json"
S2_PATH = Path(r"E:\My_RF_Project\RepresentYearAnalyst\data\demo_wt\DEMO (1)_S2.xlsx")
IEC_PATH = Path(r"E:\My_RF_Project\RepresentYearAnalyst\data\demo_wt\DEMO (1)_S2_IEC.xlsx")

requires_real = pytest.mark.skipif(
    not all(path.exists() for path in (WRP_PATH, CONTEXT_PATH, S2_PATH, IEC_PATH)),
    reason="full P1 fixtures are not available",
)


@requires_real
def test_full_p1_workbooks_match_mup_gold(tmp_path: Path):
    wrp = expander.open_wrp(WRP_PATH)
    context = p1.ExportContext.load(CONTEXT_PATH)
    outputs = p1.export_case(wrp, context, tmp_path, "DEMO (1)_S2")

    synthesis = p1.reconcile_workbooks(S2_PATH, outputs["synthesis"])
    assert synthesis["totals"]["mismatched"] == 0
    assert synthesis["totals"]["missing"] == 0
    assert synthesis["totals"]["matched"] == 6868
    assert synthesis["display_equivalence"] == 1.0

    iec = p1.reconcile_workbooks(IEC_PATH, outputs["iec"])
    assert iec["totals"]["mismatched"] == 0
    assert iec["totals"]["missing"] == 0
    assert iec["totals"]["matched"] == 1632
    assert iec["totals"]["boundary"] == 0
    assert iec["display_equivalence"] == 1.0


WRP3_PATH = VAULT / "ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_3.wrp"


@pytest.mark.skipif(not WRP3_PATH.exists(), reason="144-turbine WRP is not available")
def test_neighbour_uses_closest_uid_not_distance():
    wrp = expander.open_wrp(WRP3_PATH)
    points = p1._points(wrp)
    by_label = {point.label: point for point in points}
    neighbour = p1._neighbour(points, by_label["WT12"])
    assert neighbour is not None
    assert neighbour[0] == "WT11"
    assert p1._neighbour(points, by_label["M1"]) is None




def _wrp_with_iec_norm(tmp_path: Path, norm: int) -> Path:
    import struct

    data = bytearray(WRP_PATH.read_bytes())
    count = struct.unpack_from("<H", data, 44)[0]
    for index in range(count):
        offset = 100 + index * 64
        sense = struct.unpack_from("<H", data, offset)[0]
        if sense != 10:
            continue
        payload = struct.unpack_from("<q", data, offset + 24)[0]
        if data[offset + 32] != 0:
            raise AssertionError("IECEdition block is not float32")
        for point in range(struct.unpack_from("<I", data, 40)[0]):
            struct.pack_into("<f", data, payload + point * 4, float(norm))
        path = tmp_path / f"iec_norm_{norm}.wrp"
        path.write_bytes(bytes(data))
        return path
    raise AssertionError("sense 10 block not found")


@requires_real
def test_iec31_structure_branch(tmp_path: Path):
    wrp = expander.open_wrp(_wrp_with_iec_norm(tmp_path, 2))
    context = p1.ExportContext.from_dict({
        "synthesis": 2,
        "iec": {
            "edition": "3.1",
            "norm": 2,
            "iec_class": "IA",
            "iref": 0.16,
            "wohler": 10,
            "reference_mast": "默认",
            "cct_class": "H",
            "air_density": 1.225,
            "min_shear": 0.05,
            "max_shear": 0.25,
        },
    })
    workbook = p1.build_iec_workbook(wrp, context)
    assert workbook.sheetnames == ["湍流", "特定极端湍流", "风机 - Ed. 3.1", "Sheet1"]
    assert workbook["湍流"].max_row == 24
    assert workbook["湍流"].cell(2, 1).value == 3
    assert workbook["湍流"].cell(24, 1).value == 25
    assert workbook["风机 - Ed. 3.1"].cell(2, 1).value == "标签"
    assert workbook["Sheet1"].max_row == 1


def test_iec_half_up_boundary_is_away_from_zero():
    # Python round(0.2345, 3) -> 0.234 (banker's); MUP is 0.235.
    assert p1._quantize(0.2345, 3) == pytest.approx(0.235)
    assert round(0.2345, 3) == 0.234


ED31_GOLD = Path(
    r"E:\My_RF_Project\MicroSitingDecisionAnalyst\data\demo_机位载荷功能测试"
    r"\20261030_test_S2_IEC m=1.xlsx"
)


@pytest.mark.skipif(not ED31_GOLD.exists(), reason="Ed.3.1 gold workbook is not available")
def test_iec31_gold_sheet_structure(tmp_path: Path):
    wrp = expander.open_wrp(_wrp_with_iec_norm(tmp_path, 2))
    context = p1.ExportContext.from_dict({
        "synthesis": 2,
        "iec": {"edition": "3.1", "norm": 2, "iec_class": "IA", "iref": 0.16, "wohler": 10,
                "reference_mast": "默认", "cct_class": "H", "air_density": 1.225,
                "min_shear": 0.05, "max_shear": 0.25,
                "maximum_inflow_angle": 8, "maximum_air_density": 1.225},
    })
    produced = p1.build_iec_workbook(wrp, context)
    gold = load_workbook(ED31_GOLD, data_only=True, read_only=True)
    try:
        assert produced.sheetnames == gold.sheetnames
        for name in ("湍流", "特定极端湍流"):
            assert produced[name].max_row == gold[name].max_row
            assert produced[name].cell(1, 1).value == gold[name].cell(1, 1).value
            assert produced[name].cell(2, 1).value == gold[name].cell(2, 1).value
        summary = "风机 - Ed. 3.1"
        assert produced[summary].cell(2, 1).value == gold[summary].cell(2, 1).value
        assert produced[summary].max_column == 28  # authoritative 28-column spec
    finally:
        gold.close()


@requires_real
def test_iec31_specific_extreme_is_sense39_max_times_speed(tmp_path: Path):
    wrp_path = _wrp_with_iec_norm(tmp_path, 2)
    wrp = expander.open_wrp(wrp_path)
    context = p1.ExportContext.from_dict({
        "synthesis": 2,
        "iec": {"edition": "3.1", "norm": 2, "iec_class": "IA", "iref": 0.16, "wohler": 10,
                "reference_mast": "默认", "cct_class": "H", "air_density": 1.225,
                "min_shear": 0.05, "max_shear": 0.25,
                "maximum_inflow_angle": 8, "maximum_air_density": 1.225},
    })
    workbook = p1.build_iec_workbook(wrp, context)
    sheet = workbook["特定极端湍流"]
    point = next(p for p in p1._points(wrp) if p.label == "W1")
    raw = max(
        point.value(expander.DATA_SENSE["turbulence_iec"], d * wrp.direction_step, 3.0)
        for d in range(wrp.nb_directions)
    )
    assert sheet.cell(2, 2).value == p1._quantize(raw * 3.0, 3)

    summary = workbook["风机 - Ed. 3.1"]
    assert summary.cell(3, 13).number_format == "@"
    assert summary.cell(3, 25).number_format == "@"


@requires_real
def test_iec_norm_conflict_is_rejected(tmp_path: Path):
    wrp = expander.open_wrp(_wrp_with_iec_norm(tmp_path, 2))
    context = p1.ExportContext.from_dict({"synthesis": 2, "iec": {"norm": 3}})
    with pytest.raises(p1.ExportError, match="conflicts with WRP DataSense 10"):
        p1.build_iec_workbook(wrp, context)
