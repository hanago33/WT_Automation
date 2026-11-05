# encoding: utf-8
"""离线导出流水线的端到端测试：字节格式、命名、manifest、流程执行。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import mup_matrix_expander as expander
import mup_offline_export as offline

ROOT = Path(__file__).resolve().parents[1]
VAULT = Path(
    r"H:\ProgramData\Meteodyn\MUP\local_liao_be917e43-a15c-4db7-8573-822cecb16f78"
    r"\SYNTHESIS_FILES"
)
WRP_PATH = VAULT / "ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_2.wrp"
GOLD_DIR = Path(r"E:\My_RF_Project\RepresentYearAnalyst\data\demo_wt")
GOLD_FREQ = GOLD_DIR / "DEMO (1)_WRA2_Frequency_Matrix.txt"
GOLD_TURB = GOLD_DIR / "DEMO (1)_WRA2_TurbulenceIntensityTotal_Matrix.txt"
CONTEXT = ROOT / "_scratch_probes" / "demo_s2_context.json"
FLOW = ROOT / "flow_packages" / "flow_definition_离线导出计算结果.json"

requires_real = pytest.mark.skipif(
    not (WRP_PATH.exists() and GOLD_FREQ.exists() and GOLD_TURB.exists()),
    reason="real DEMO .wrp or matrix gold samples are not available",
)


def _export(tmp_path: Path, **kwargs):
    options = dict(
        out_dir=tmp_path,
        wrp_path=WRP_PATH,
        synthesis=2,
        name="DEMO (1)",
        context=CONTEXT if CONTEXT.exists() else None,
        overwrite=True,
    )
    options.update(kwargs)
    return offline.export_case(**options)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------


def test_output_names_follow_mup_baseline():
    names = offline.build_output_names("DEMO (1)", 3)
    assert names["frequency"] == "DEMO (1)_WRA3_Frequency_Matrix.txt"
    assert names["turbulence"] == "DEMO (1)_WRA3_TurbulenceIntensityTotal_Matrix.txt"
    assert names["production"] == "DEMO (1)_WRA3_Production_Matrix.txt"
    assert names["synthesis"] == "DEMO (1)_S3.xlsx"
    assert names["iec"] == "DEMO (1)_S3_IEC.xlsx"
    assert names["manifest"] == "DEMO (1)_offline_manifest.json"


def test_write_mup_text_emits_bom_and_lf(tmp_path: Path):
    path = offline.write_mup_text(tmp_path / "a.txt", "Label \t= \tW1\n")
    payload = path.read_bytes()
    assert payload.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in payload
    assert payload.endswith(b"W1\n")


def test_write_mup_text_rejects_crlf(tmp_path: Path):
    with pytest.raises(offline.OfflineExportError, match="LF"):
        offline.write_mup_text(tmp_path / "b.txt", "a\r\nb\n")


# --------------------------------------------------------------------------
# Byte-identical matrix output
# --------------------------------------------------------------------------


@requires_real
def test_frequency_matrix_is_byte_identical_to_mup(tmp_path: Path):
    result = _export(tmp_path, include_excel=False)
    produced = result.files["frequency"].read_bytes()
    assert produced == GOLD_FREQ.read_bytes()


@requires_real
def test_turbulence_matrix_is_byte_identical_to_mup(tmp_path: Path):
    result = _export(tmp_path, include_excel=False)
    produced = result.files["turbulence"].read_bytes()
    assert produced == GOLD_TURB.read_bytes()


@requires_real
def test_production_matrix_sums_to_annual_energy(tmp_path: Path):
    wrp = expander.open_wrp(WRP_PATH)
    for kind in expander.PRODUCTION_KINDS:
        matrix = wrp.production_matrix("W1", kind)
        assert abs(matrix.checksum() - matrix.total) <= 0.01, kind
    rendered = wrp.production_matrix("W1").render_block().splitlines()
    assert rendered[0] == "Label \t= \tW1"
    assert rendered[4].startswith("Reference\t=\tM1")
    assert "Annual energy production with wake effect (MWh/year) =" in rendered[4]
    assert rendered[-1] == "-" * 200
    assert rendered[-2] == ""


@requires_real
def test_production_matrix_detail_rows_are_kilowatt_hours(tmp_path: Path):
    result = _export(tmp_path, include_excel=False)
    lines = result.files["production"].read_text(encoding="utf-8-sig").splitlines()
    header_index = next(i for i, line in enumerate(lines) if line.startswith("Speed\xa0(m/s)") and "%" in line)
    first_row = lines[header_index + 1]
    assert first_row[:24].strip() == "1"
    # 每一列都必须是整数（kWh，0 位小数）
    for token in first_row[36:].split():
        assert token.lstrip("-").isdigit(), token


# --------------------------------------------------------------------------
# Manifest / guards
# --------------------------------------------------------------------------


@requires_real
def test_manifest_records_every_artifact(tmp_path: Path):
    result = _export(tmp_path, include_excel=False)
    payload = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert payload["generator"] == "mup_offline_export"
    assert payload["synthesis"] == 2
    assert payload["source"]["sha256"] == offline._sha256(WRP_PATH)
    roles = {item["role"] for item in payload["outputs"]}
    assert {"frequency", "turbulence", "production"} <= roles
    for item in payload["outputs"]:
        assert item["sha256"] == offline._sha256(Path(item["path"]))
        assert item["size"] == Path(item["path"]).stat().st_size


@requires_real
def test_refuses_overwrite_without_flag(tmp_path: Path):
    _export(tmp_path, include_excel=False, overwrite=True)
    with pytest.raises(offline.OfflineExportError, match="refusing to overwrite"):
        _export(tmp_path, include_excel=False, overwrite=False)


@requires_real
def test_excel_outputs_match_mup_cells(tmp_path: Path):
    gold_s2 = GOLD_DIR / "DEMO (1)_S2.xlsx"
    gold_iec = GOLD_DIR / "DEMO (1)_S2_IEC.xlsx"
    if not (gold_s2.exists() and gold_iec.exists() and CONTEXT.exists()):
        pytest.skip("Excel gold samples or context are not available")
    result = _export(tmp_path, include_excel=True)
    import mup_excel_export_p1 as excel

    for role, gold in (("synthesis", gold_s2), ("iec", gold_iec)):
        diff = excel.reconcile_workbooks(gold, result.files[role])
        assert diff["totals"]["mismatched"] == 0, role
        assert diff["totals"]["missing"] == 0, role


# --------------------------------------------------------------------------
# Declarative flow
# --------------------------------------------------------------------------


def test_offline_flow_definition_is_marked_offline():
    payload = json.loads(FLOW.read_text(encoding="utf-8"))
    assert payload["runtimeConfig"]["mode"] == "offline"
    assert payload["runtimeConfig"]["requiresMup"] is False
    roles = {
        (step.get("stepParams") or {}).get("role")
        for step in payload["steps"]
        if step.get("enabled", True)
    }
    assert {"frequency", "turbulence", "production", "synthesis", "iec", "manifest"} <= roles


@requires_real
def test_run_flow_produces_every_declared_role(tmp_path: Path):
    result = offline.run_flow(
        FLOW,
        out_dir=tmp_path,
        wrp_path=WRP_PATH,
        synthesis=2,
        name="DEMO (1)",
        context=CONTEXT if CONTEXT.exists() else None,
        overwrite=True,
    )
    for role in ("frequency", "turbulence", "production", "manifest"):
        assert role in result.files


def test_run_flow_rejects_gui_flow(tmp_path: Path):
    gui_flow = ROOT / "flow_packages" / "flow_definition_导出综合计算结果.json"
    if not gui_flow.exists():
        pytest.skip("GUI export flow is not available")
    with pytest.raises(offline.OfflineExportError, match="runtimeConfig.mode"):
        offline.run_flow(gui_flow, out_dir=tmp_path)


def test_resolve_case_prefix_strips_copy_suffix():
    guid, note = offline.resolve_case_prefix("DEMO (1)")
    assert guid == "ea368c7f-9810-487d-be4c-bbcc9339c83e", note
    assert "DEMO" in note


def test_resolve_case_prefix_accepts_guid():
    guid = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
    assert offline.resolve_case_prefix(guid) == (guid, "already a CaseGUID")


def test_offline_flow_passes_project_validator():
    import wt_flow_validation

    payload = json.loads(FLOW.read_text(encoding="utf-8"))
    assert wt_flow_validation.validate_flow_definition(payload) == []


def test_list_vault_cases_reports_demo():
    cases = offline.list_vault_cases()
    assert cases, "Vault should contain at least one synthesis case"
    demo = next(item for item in cases if item["case"] == "ea368c7f-9810-487d-be4c-bbcc9339c83e")
    assert demo["syntheses"] == [2, 3, 4]
    assert demo["wrp_count"] == 3
    assert demo["size"] > 0


def test_resolve_case_prefix_does_not_silently_pick_the_only_project():
    guid, note = offline.resolve_case_prefix("完全不存在的项目")
    assert guid == ""
    assert "no LocalDB case matched" in note


def test_resolve_case_prefix_unique_fallback_is_opt_in():
    guid, note = offline.resolve_case_prefix("完全不存在的项目", allow_unique_fallback=True)
    assert guid == "ea368c7f-9810-487d-be4c-bbcc9339c83e"
    assert "unique CaseGUID" in note


def test_cli_list_cases(capsys):
    code = offline.main(["--list-cases"])
    assert code == 0
    out = capsys.readouterr().out
    assert "ea368c7f-9810-487d-be4c-bbcc9339c83e" in out
    assert "[2, 3, 4]" in out
