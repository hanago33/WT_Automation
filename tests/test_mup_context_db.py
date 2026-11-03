"""Tests for the LocalDB → ExportContext adapter."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mup_context_db as context_db  # noqa: E402
import mup_excel_export_p1 as p1  # noqa: E402
import mup_matrix_expander as expander  # noqa: E402

VAULT = Path(
    r"H:\ProgramData\Meteodyn\MUP\local_liao_be917e43-a15c-4db7-8573-822cecb16f78"
    r"\SYNTHESIS_FILES"
)
WRP_PATH = VAULT / "ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_2.wrp"
S2_PATH = Path(r"E:\My_RF_Project\RepresentYearAnalyst\data\demo_wt\DEMO (1)_S2.xlsx")
IEC_PATH = Path(r"E:\My_RF_Project\RepresentYearAnalyst\data\demo_wt\DEMO (1)_S2_IEC.xlsx")
GUID = "ea368c7f-9810-487d-be4c-bbcc9339c83e"

requires_db = pytest.mark.skipif(
    shutil.which("sqlcmd") is None
    and not Path(context_db.DEFAULT_SQLCMD).is_file(),
    reason="sqlcmd / LocalDB is not available",
)


def test_inverse_transverse_mercator_matches_mup_coordinate():
    longitude, latitude = context_db.inverse_transverse_mercator(
        38556424.0,
        3163553.0,
        central_meridian=114.0,
        false_easting=38500000.0,
        semi_major=6378140.0,
        inverse_flattening=298.257,
    )
    assert round(longitude, 6) == 114.576783
    assert round(latitude, 6) == 28.586279


@requires_db
def test_build_context_from_localdb():
    build = context_db.build_context(GUID, 2)
    assert build.missing == []
    assert build.data["case_name"] == "DEMO"
    assert build.data["projection"] == "Xian 1980 / 3-degree Gauss-Kruger zone 38"
    assert build.data["met_mast_label"] == "M1"
    assert build.data["met_mast_height"] == "90 m"
    assert build.data["iec"]["iec_class"] == "IA"
    assert build.data["iec"]["iref"] == pytest.approx(0.16)
    assert build.data["iec"]["wohler"] == pytest.approx(10)
    assert build.data["iec"]["edition"] == "4.0"

    w1 = build.data["turbines"]["W1"]
    assert w1["model"] == "testWTT"
    assert w1["version"] == "版本 (1)"
    assert w1["turbulence_class"] == "未指定"
    assert w1["longitude"] == pytest.approx(114.576783)
    assert w1["latitude"] == pytest.approx(28.586279)
    assert build.data["turbines"]["M1"]["longitude"] == pytest.approx(114.62412)


@requires_db
@pytest.mark.skipif(
    not (WRP_PATH.exists() and S2_PATH.exists() and IEC_PATH.exists()),
    reason="full P1 fixtures are not available",
)
def test_db_context_reaches_full_gold(tmp_path: Path):
    build = context_db.build_context(
        GUID,
        2,
        overrides={"case_name": "DEMO (1)", "met_mast_height": "80 m"},
    )
    context = p1.ExportContext.from_dict(build.data)
    outputs = p1.export_case(
        expander.open_wrp(WRP_PATH),
        context,
        tmp_path,
        "DEMO (1)_S2",
    )

    synthesis = p1.reconcile_workbooks(S2_PATH, outputs["synthesis"])
    iec = p1.reconcile_workbooks(IEC_PATH, outputs["iec"])
    assert synthesis["totals"] == {
        "cells": 6868,
        "matched": 6868,
        "boundary": 0,
        "mismatched": 0,
        "missing": 0,
    }
    assert iec["totals"] == {
        "cells": 1632,
        "matched": 1632,
        "boundary": 0,
        "mismatched": 0,
        "missing": 0,
    }
