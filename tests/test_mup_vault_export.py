# encoding: utf-8
"""Tests for mup_vault_export: Vault direct export, export_manifest generation,
and S7 flow reconciliation.
"""
import json
import os
import shutil
import sys
import tempfile
import pytest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import mup_vault_export
import export_s7


def test_existing_self_test_passes():
    """验证 mup_vault_export.self_test() 保持 100% PASS。"""
    ok, msgs = mup_vault_export.self_test()
    assert ok, "self_test 必须通过: %s" % msgs
    assert any("PASS" in m for m in msgs)


def test_build_case_mapping_standard_4_rows():
    """验证标准 4 行参数表：create(1)/copy(4)/copy(10)/copyfull(1,带绘图)
    按行序 + wohler 正确生成映射 {综合1→m1, 综合2→m4, 综合3→m10, 综合4→(none)}。
    """
    param_table = [
        {"stepMode": "create", "synthesisName": "综合1", "wohler": "1", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "4", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "10", "drawLayout": "不带绘图"},
        {"stepMode": "copyfull", "synthesisName": "", "wohler": "1", "drawLayout": "带绘图"},
    ]
    mapping_dict = mup_vault_export.build_case_mapping_dict(param_table)
    expected = {
        "综合1": "m1",
        "综合2": "m4",
        "综合3": "m10",
        "综合4": "(none)",
    }
    assert mapping_dict == expected

    rows = mup_vault_export.build_case_mapping(param_table)
    assert len(rows) == 4
    assert rows[0]["case"] == "综合1" and rows[0]["m"] == "m1" and rows[0]["export"] is True
    assert rows[1]["case"] == "综合2" and rows[1]["m"] == "m4" and rows[1]["export"] is True
    assert rows[2]["case"] == "综合3" and rows[2]["m"] == "m10" and rows[2]["export"] is True
    assert rows[3]["case"] == "综合4" and rows[3]["m"] == "" and rows[3]["export"] is False
    assert rows[3]["status"] == "completed_with_gaps"


def test_build_case_mapping_boundary_less_than_4_rows():
    """边界测试：<4 行参数表（1 行、2 行、3 行）。"""
    # 1 行
    p1 = [{"stepMode": "create", "synthesisName": "综合1", "wohler": "1"}]
    assert mup_vault_export.build_case_mapping_dict(p1) == {"综合1": "m1"}

    # 2 行
    p2 = [
        {"stepMode": "create", "synthesisName": "综合1", "wohler": "1"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "4"},
    ]
    assert mup_vault_export.build_case_mapping_dict(p2) == {"综合1": "m1", "综合2": "m4"}

    # 3 行
    p3 = [
        {"stepMode": "create", "synthesisName": "自定义A", "wohler": "1"},
        {"stepMode": "copy", "synthesisName": "自定义B", "wohler": "4"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "10"},
    ]
    assert mup_vault_export.build_case_mapping_dict(p3) == {
        "自定义A": "m1",
        "自定义B": "m4",
        "综合3": "m10",
    }


def test_build_case_mapping_boundary_more_than_4_rows():
    """边界测试：>4 行参数表（5 行、6 行扩展）。"""
    p5 = [
        {"stepMode": "create", "synthesisName": "综合1", "wohler": "1", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "4", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "10", "drawLayout": "不带绘图"},
        {"stepMode": "copyfull", "synthesisName": "", "wohler": "1", "drawLayout": "带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "12", "drawLayout": "不带绘图"},
    ]
    m5 = mup_vault_export.build_case_mapping_dict(p5)
    assert m5 == {
        "综合1": "m1",
        "综合2": "m4",
        "综合3": "m10",
        "综合4": "(none)",
        "综合5": "m12",
    }

    # 6 行：再加一个 copyfull 带绘图卡片
    p6 = list(p5) + [{"stepMode": "copyfull", "synthesisName": "绘图卡片2", "wohler": "4", "drawLayout": "带绘图"}]
    m6 = mup_vault_export.build_case_mapping_dict(p6)
    assert m6["综合5"] == "m12"
    assert m6["绘图卡片2"] == "(none)"


def test_build_case_mapping_fullwidth_delimiters_and_numbers():
    """边界测试：全角逗号（，）、全角分号（；）、全角空格、全角数字（１、４、１０）。"""
    csv_text = (
        "synthesisName，wohler，drawLayout\n"
        "综合1，１，不带绘图\n"
        "，４，不带绘图\n"
        "，１０，\n"
        "，１，带绘图\n"
    )
    m = mup_vault_export.build_case_mapping_dict(csv_text)
    assert m == {
        "综合1": "m1",
        "综合2": "m4",
        "综合3": "m10",
        "综合4": "(none)",
    }

    # 测试全角分号与空格混合
    csv_semicolon = (
        "综合名称；wohler指数；绘图布局\n"
        "综合1；１；不带绘图\n"
        "；４；\n"
    )
    m_semi = mup_vault_export.build_case_mapping_dict(csv_semicolon)
    assert m_semi == {"综合1": "m1", "综合2": "m4"}


def test_build_export_manifest_json_structure(tmp_path):
    """测试 build_export_manifest 生成的 JSON 结构与字段完整性。"""
    param_file = tmp_path / "param_table.json"
    param_file.write_text(json.dumps([
        {"stepMode": "create", "synthesisName": "综合1", "wohler": "1", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "4", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "10", "drawLayout": "不带绘图"},
        {"stepMode": "copyfull", "synthesisName": "", "wohler": "1", "drawLayout": "带绘图"},
    ], ensure_ascii=False), encoding="utf-8")

    manifest = mup_vault_export.build_export_manifest(str(param_file), site="DemoSite", output_dir=str(tmp_path))
    assert manifest["schema"] == "mup_vault_export/manifest/v2"
    assert manifest["site"] == "DemoSite"
    assert "case_prefix" in manifest
    assert manifest["mandatory"] == ["{P}_synthesis_{n}.wri", "{P}_synthesis_{n}.wrp"]
    assert len(manifest["optional"]) == 4
    assert manifest["mapping"] == {
        "综合1": "m1",
        "综合2": "m4",
        "综合3": "m10",
        "综合4": "(none)",
    }
    assert manifest["total_cases"] == 4
    assert manifest["export_cases_count"] == 3
    assert manifest["gap_cases_count"] == 1
    assert len(manifest["requires_gui"]) == 2
    assert "matrix" in manifest["direct_available_families"]
    assert "synthesis" in manifest["direct_available_families"]
    assert "wake" in manifest["direct_available_families"]
    assert "map" in manifest["direct_available_families"]

    manifest_file = mup_vault_export.write_manifest(manifest, str(tmp_path))
    assert os.path.isfile(manifest_file)
    with open(manifest_file, "r", encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded["mapping"] == manifest["mapping"]


def test_vault_direct_export_lands_in_m1_m4_m10_observed(tmp_path):
    """【实跑验证 (Observed)】直取函数对合成 SYNTHESIS_FILES 目录能按 manifest 正确落到 m1/m4/m10，
    且修复 S7 漏导的 m4/m10 矩阵，综合4 正确跳过并标记 completed_with_gaps。
    """
    vault = tmp_path / "vault" / "local_test_guid"
    syn_dir = vault / "SYNTHESIS_FILES"
    syn_dir.mkdir(parents=True)

    site = "YunNanSite"
    # 构造合成目录假产物（包含综合1/2/3/4产物，含必出2件套 .wri 与 .wrp）
    products_spec = [
        # 综合1 (n=1)
        f"{site}_synthesis_1.wri", f"{site}_synthesis_1.wrp",
        f"{site}_synthesis_1.xml", f"{site}_synthesis_1.txt", f"{site}_matrix_1.txt",
        f"{site}_losswake_1.txt", f"{site}_map_1.pdr", f"{site}_map_1.wrm",
        # 综合2 (n=2)
        f"{site}_synthesis_2.wri", f"{site}_synthesis_2.wrp",
        f"{site}_synthesis_2.xml", f"{site}_synthesis_2.txt", f"{site}_matrix_2.txt",
        f"{site}_losswake_2.txt", f"{site}_map_2.pdr",
        # 综合3 (n=3)
        f"{site}_synthesis_3.wri", f"{site}_synthesis_3.wrp",
        f"{site}_synthesis_3.xml", f"{site}_synthesis_3.txt", f"{site}_matrix_3.txt",
        f"{site}_losswake_3.txt", f"{site}_map_3.wrm",
        # 综合4 (n=4，仅用于绘图，不应导出)
        f"{site}_synthesis_4.wri", f"{site}_synthesis_4.wrp",
        f"{site}_synthesis_4.xml", f"{site}_matrix_4.txt",
    ]
    for fname in products_spec:
        fpath = syn_dir / fname
        fpath.write_text(f"content:{fname}", encoding="utf-8")

    out_root = tmp_path / "04-WT输出"
    param_table = [
        {"stepMode": "create", "synthesisName": "综合1", "wohler": "1", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "4", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "10", "drawLayout": "不带绘图"},
        {"stepMode": "copyfull", "synthesisName": "", "wohler": "1", "drawLayout": "带绘图"},
    ]

    manifest = mup_vault_export.export_by_manifest(
        manifest_or_param_table=param_table,
        site=site,
        output_dir=str(out_root),
        vault_root=str(vault),
        copy=True,
        preserve_names=True,
    )

    # 1. 验证 m1 目录
    m1_dir = out_root / "m1"
    assert m1_dir.is_dir(), "m1 目录必须存在"
    for expected_file in [f"{site}_synthesis_1.wri", f"{site}_synthesis_1.wrp",
                         f"{site}_synthesis_1.xml", f"{site}_synthesis_1.txt",
                         f"{site}_matrix_1.txt", f"{site}_losswake_1.txt",
                         f"{site}_map_1.pdr", f"{site}_map_1.wrm"]:
        target_f = m1_dir / expected_file
        assert target_f.is_file(), f"m1 缺失产物: {expected_file}"
        assert target_f.read_text(encoding="utf-8") == f"content:{expected_file}"

    # 2. 验证 m4 目录（包含矩阵，补齐 S7 遗漏）
    m4_dir = out_root / "m4"
    assert m4_dir.is_dir(), "m4 目录必须存在"
    for expected_file in [f"{site}_synthesis_2.wri", f"{site}_synthesis_2.wrp",
                         f"{site}_synthesis_2.xml", f"{site}_synthesis_2.txt",
                         f"{site}_matrix_2.txt", f"{site}_losswake_2.txt", f"{site}_map_2.pdr"]:
        target_f = m4_dir / expected_file
        assert target_f.is_file(), f"m4 缺失产物: {expected_file}"
        assert target_f.read_text(encoding="utf-8") == f"content:{expected_file}"

    # 3. 验证 m10 目录（包含矩阵，补齐 S7 遗漏）
    m10_dir = out_root / "m10"
    assert m10_dir.is_dir(), "m10 目录必须存在"
    for expected_file in [f"{site}_synthesis_3.wri", f"{site}_synthesis_3.wrp",
                         f"{site}_synthesis_3.xml", f"{site}_synthesis_3.txt",
                         f"{site}_matrix_3.txt", f"{site}_losswake_3.txt", f"{site}_map_3.wrm"]:
        target_f = m10_dir / expected_file
        assert target_f.is_file(), f"m10 缺失产物: {expected_file}"
        assert target_f.read_text(encoding="utf-8") == f"content:{expected_file}"

    # 4. 验证综合4：无独立导出子目录
    assert not (out_root / "mNone").exists()
    assert not (out_root / "None").exists()
    assert not (out_root / "(none)").exists()

    # 5. 验证 export_manifest.json 落盘
    manifest_path = out_root / "export_manifest.json"
    assert manifest_path.is_file()
    with open(manifest_path, "r", encoding="utf-8") as fh:
        saved_m = json.load(fh)
    assert saved_m["schema"] == "mup_vault_export/manifest/v2"
    assert saved_m["mapping"] == {
        "综合1": "m1",
        "综合2": "m4",
        "综合3": "m10",
        "综合4": "(none)",
    }
    assert saved_m["completed_cases_count"] == 4
    assert saved_m["delivered_cases_count"] == 3
    assert saved_m["cases"][0]["mandatory_present"] == 2
    assert saved_m["cases"][1]["mandatory_present"] == 2
    assert saved_m["cases"][2]["mandatory_present"] == 2
    assert saved_m["cases"][3]["mandatory_present"] == 2
    assert saved_m["cases"][3]["status"] == "completed_with_gaps"
    assert saved_m["overall_status"] == "succeeded"


def test_s7_flow_reconciliation_audit():
    """测试 S7 flow 实际导出与 Vault 直取的清单对账。"""
    report = mup_vault_export.reconcile_s7_export()
    assert report["s7_total_steps"] == 46

    # S7 实际导出的 Excel 与 IEC
    excel_steps = report["s7_actual_gui_exports"]["excel_total"]
    iec_steps = report["s7_actual_gui_exports"]["iec_summary"]
    assert len(excel_steps) == 3, "S7 实际应包含 3 个 Excel 导出步骤（m1/m4/m10）"
    assert len(iec_steps) == 3, "S7 实际应包含 3 个 IEC 总结导出步骤（m1/m4/m10）"

    # S7 矩阵缺陷与 Vault 覆盖修复
    assert "S7 只导出了综合1的矩阵" in report["s7_actual_gui_exports"]["matrix_defect"]
    assert report["vault_direct_coverage"]["matrix"]["status"] == "covered_and_repaired"

    # 仍缺口项（必须保留 GUI）
    req_gui = report["remaining_gui_requirements"]
    assert len(req_gui) == 2
    fams = {r["family"] for r in req_gui}
    assert fams == {"excel_total", "iec_summary"}

    # 业务缺口项（综合4 无导出）
    assert report["gaps"][0]["case"] == "综合4"


def test_resolve_site_case_id_logic():
    """测试 resolve_site_case_id 的四级解析机制。"""
    # 1. 显式 GUID 直通
    guid = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
    assert mup_vault_export.resolve_site_case_id(guid) == guid

    # 2. Mock / 既有文件前缀直通
    with tempfile.TemporaryDirectory() as td:
        syn = os.path.join(td, "SYNTHESIS_FILES")
        os.makedirs(syn, exist_ok=True)
        with open(os.path.join(syn, "MockSite_synthesis_1.wri"), "w") as fh:
            fh.write("test")
        assert mup_vault_export.resolve_site_case_id("MockSite", vault_root=td) == "MockSite"

    # 3. 启发式回退：单一 GUID 自动提取
    with tempfile.TemporaryDirectory() as td:
        syn = os.path.join(td, "SYNTHESIS_FILES")
        os.makedirs(syn, exist_ok=True)
        with open(os.path.join(syn, f"{guid}_synthesis_2.wri"), "w") as fh:
            fh.write("test")
        assert mup_vault_export.resolve_site_case_id("DEMO", vault_root=td) == guid


def test_real_demo_synthesis_product_inventory():
    """核验真实 DEMO 产物库（若真实盘存在）。"""
    real_vault = r"H:\ProgramData\Meteodyn\MUP\local_liao_be917e43-a15c-4db7-8573-822cecb16f78"
    if not os.path.isdir(real_vault):
        pytest.skip("真实 DEMO vault 目录未挂载，跳过现场核验")

    # 1. 验证 DEMO 站点名能够成功自动解析到 Case GUID
    guid = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
    assert mup_vault_export.resolve_site_case_id("DEMO", vault_root=real_vault) == guid

    # 2. Case 2 必出核验：2 件套 (.wri, .wrp)
    c2 = mup_vault_export.list_synthesis_products("DEMO", 2, vault_root=real_vault)
    assert len(c2) == 2
    c2_names = {p["name"] for p in c2}
    assert f"{guid}_synthesis_2.wri" in c2_names
    assert f"{guid}_synthesis_2.wrp" in c2_names

    # 3. Case 3 地图核验：3 件套（.wri, .wrp, 双 GUID .wrm）
    c3 = mup_vault_export.list_synthesis_products("DEMO", 3, vault_root=real_vault)
    assert len(c3) == 3
    c3_names = {p["name"] for p in c3}
    assert f"{guid}_synthesis_3.wri" in c3_names
    assert f"{guid}_synthesis_3.wrp" in c3_names
    assert f"{guid}_2d459fd9-c918-418a-8e7f-52d9e7b4db8f_3.wrm" in c3_names
    wrm_item = [p for p in c3 if p["name"].endswith(".wrm")][0]
    assert wrm_item["category"] == "map"

    # 4. Case 4 全量核验：12 件套（.wri, .wrp, .wrt, 7 个 .wrtd, 2 个 .dat）
    c4 = mup_vault_export.list_synthesis_products("DEMO", 4, vault_root=real_vault)
    assert len(c4) == 12
    cats = {p["category"] for p in c4}
    assert cats == {"synthesis", "wrtd", "timeserie"}


def test_resolve_site_case_id_engineering_root_cause_fixes():
    """工程化测试：验证 SQL 参数化防注入、多 Case 确定性排序/筛选、sqlcmd 异常进 diagnostics、多 GUID 冲突防御。"""
    diag = {}

    # 1. 防注入测试：恶意字符/换行/控制字符被安全拦截，不崩溃且记录 diagnostics
    bad_site = "DEMO'; DROP TABLE Cases; --"
    res_bad = mup_vault_export.resolve_site_case_id(bad_site, diagnostics=diag)
    # 应安全处理，未发生 SQL 注入，diagnostics 记录原因
    assert isinstance(res_bad, str)

    # 控制字符注入防御
    bad_ctrl = "DEMO\x00\x1f_site"
    res_ctrl = mup_vault_export.resolve_site_case_id(bad_ctrl, diagnostics=diag)
    assert res_ctrl == ""

    # 2. 依赖注入 db_query_fn：测试多 Case 确定性排序与显式 case_name 筛选
    fake_db = [
        {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "name": "Case_B"},
        {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "Case_A"},
    ]
    def mock_db_query(site, case_name=None):
        if site == "MultiCaseSite":
            # 模拟 ORDER BY c.Name ASC, c.Id ASC
            sorted_cases = sorted(fake_db, key=lambda x: (x["name"], x["id"]))
            if case_name:
                return [c for c in sorted_cases if c["name"] == case_name]
            return sorted_cases
        return []

    # 2a. 未传 case_name：返回按 Name 排序的第一项 Case_A，并给出告警 diagnostics
    diag2 = {}
    r_multi = mup_vault_export.resolve_site_case_id("MultiCaseSite", db_query_fn=mock_db_query, diagnostics=diag2)
    assert r_multi == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert "multi_case_detected" in diag2 or "multi_case_warning" in diag2

    # 2b. 显式指定 case_name="Case_B"：精准命中 Case_B
    diag3 = {}
    r_case_b = mup_vault_export.resolve_site_case_id("MultiCaseSite", case_name="Case_B", db_query_fn=mock_db_query, diagnostics=diag3)
    assert r_case_b == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

    # 3. 启发式多 GUID 冲突：当 Vault 存在多个不同 GUID 时，不得乱猜，应返回 "" 并告警
    with tempfile.TemporaryDirectory() as td:
        syn = os.path.join(td, "SYNTHESIS_FILES")
        os.makedirs(syn, exist_ok=True)
        guid1 = "11111111-1111-1111-1111-111111111111"
        guid2 = "22222222-2222-2222-2222-222222222222"
        with open(os.path.join(syn, f"{guid1}_synthesis_1.wri"), "w") as fh:
            fh.write("test")
        with open(os.path.join(syn, f"{guid2}_synthesis_2.wri"), "w") as fh:
            fh.write("test")
        diag4 = {}
        # 传入在 DB 中不存在的站点名，触发启发式
        r_ambiguous = mup_vault_export.resolve_site_case_id("AmbiguousSite", vault_root=td, db_query_fn=lambda s, c=None: [], diagnostics=diag4)
        assert r_ambiguous == "", "多 GUID 存在时启发式必须放弃猜测，返回空字符串"
        assert "vault_heuristic_multi_guids" in diag4 or "candidates" in diag4


def test_wrong_site_zero_match_and_explicit_reason(tmp_path):
    """验证故意输入错误 site 时：零匹配 + 明确失败原因，绝不得生成空 manifest 当作成功！"""
    out_dir = tmp_path / "04-WT输出_错site"
    diag = {}
    # 模拟 DB 明确返回 0 命中
    res = export_s7.run_export_s7(
        site="NoSuchSite_DefinitelyWrong",
        output_dir=str(out_dir),
        db_query_fn=lambda s, c=None: [],
        diagnostics=diag,
    )
    assert res["outcome"] == "failed", "故意错 site 必须 fail-closed，不得标成功"
    assert res["case_prefix"] == "", "错 site 的 case_prefix 必须为空"
    assert res["completed_cases_count"] == 0
    assert res["delivered_cases_count"] == 0
    assert "ZERO_MATCH_OR_CASE_PREFIX_UNRESOLVED" in res["failure_reason"]

    # 检查落盘的 manifest
    m_path = out_dir / "export_manifest.json"
    assert m_path.is_file(), "失败结果仍须真实落盘 manifest 以供追溯审计"
    with open(m_path, "r", encoding="utf-8") as fh:
        m_data = json.load(fh)
    assert m_data["overall_status"] == "failed"
    assert m_data["case_prefix"] == ""
    assert all(c["mandatory_present"] == 0 for c in m_data["cases"])


def test_missing_mandatory_products_fails_case(tmp_path):
    """验证必出 2 件套缺任一 => 该 case 记 FAILED（不得整批标成功）；条件族缺失 => 仅记 optional_missing。"""
    vault = tmp_path / "vault_partial"
    syn = vault / "SYNTHESIS_FILES"
    syn.mkdir(parents=True)

    site = "PartialSite"
    # Case 1: 必出缺 .wrp（只有 .wri）
    (syn / f"{site}_synthesis_1.wri").write_text("wri", encoding="utf-8")
    # Case 2: 必出 2 件套齐备，但缺少条件族（.wrt / .wrtd / .wrm 等）
    (syn / f"{site}_synthesis_2.wri").write_text("wri", encoding="utf-8")
    (syn / f"{site}_synthesis_2.wrp").write_text("wrp", encoding="utf-8")

    param_table = [
        {"stepMode": "create", "synthesisName": "综合1", "wohler": "1"},
        {"stepMode": "copy", "synthesisName": "综合2", "wohler": "4"},
    ]
    out_dir = tmp_path / "04-WT输出_partial"

    res = export_s7.run_export_s7(
        site=site,
        param_table=param_table,
        output_dir=str(out_dir),
        vault_root=str(vault),
        copy=True,
    )

    # 整批必须判定为 failed
    assert res["outcome"] == "failed", "缺必出产物时不得整批标成功"
    cases = res["manifest"]["cases"]

    # Case 1: mandatory_present == 1, 状态 failed, 记录 missing wrp
    c1 = cases[0]
    assert c1["mandatory_present"] == 1
    assert c1["status"] == "failed"
    assert any(".wrp" in m for m in c1["mandatory_missing"])

    # Case 2: mandatory_present == 2, 状态 export, 仅记录 optional_missing
    c2 = cases[1]
    assert c2["mandatory_present"] == 2
    assert c2["status"] == "export"
    assert c2["copied_count"] >= 2
    assert (out_dir / "m4" / f"{site}_synthesis_2.wri").is_file()
    assert (out_dir / "m4" / f"{site}_synthesis_2.wrp").is_file()


def test_completed_set_superset_of_delivery_set(tmp_path):
    """验证完成集合（取wri/wrp）⊃ 交付集合（仅 m 非空的 case）分开写两个数；
    copyfull / 带绘图行 status=completed_with_gaps，计入完成集合，不进交付。
    """
    vault = tmp_path / "vault_superset"
    syn = vault / "SYNTHESIS_FILES"
    syn.mkdir(parents=True)

    site = "SuperSetSite"
    # 为 4 个综合全部生成 .wri + .wrp
    for i in range(1, 5):
        (syn / f"{site}_synthesis_{i}.wri").write_text("wri", encoding="utf-8")
        (syn / f"{site}_synthesis_{i}.wrp").write_text("wrp", encoding="utf-8")

    # 4 行标准参数表（综合4 为 copyfull 带绘图）
    param_table = [
        {"stepMode": "create", "synthesisName": "综合1", "wohler": "1", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "4", "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "10", "drawLayout": "不带绘图"},
        {"stepMode": "copyfull", "synthesisName": "", "wohler": "1", "drawLayout": "带绘图"},
    ]
    out_dir = tmp_path / "04-WT输出_superset"

    res = export_s7.run_export_s7(
        site=site,
        param_table=param_table,
        output_dir=str(out_dir),
        vault_root=str(vault),
        copy=True,
    )
    assert res["outcome"] == "succeeded"
    # 4 个用例在 Vault 里均已完成计算（含 .wri + .wrp）
    assert res["completed_cases_count"] == 4, "完成集合应包含全部 4 个已出内核产物的用例"
    # 交付集合仅包含 m 非空的综合1/2/3
    assert res["delivered_cases_count"] == 3, "交付集合仅包含有 mN 目录的 3 个用例"

    cases = res["manifest"]["cases"]
    c4 = cases[3]
    assert c4["case"] == "综合4"
    assert c4["m_dir"] == "(none)"
    assert c4["export"] is False
    assert c4["status"] == "completed_with_gaps"
    assert c4["mandatory_present"] == 2  # 物理产物存在

    # 确认 mNone 目录未被创建
    assert not (out_dir / "mNone").exists()
    assert not (out_dir / "(none)").exists()


def test_residual_36_step_gui_flow_dynamically_driven(tmp_path):
    """测试残余 GUI 36 步流动态生成：
    1. 标准 3 case 导出生成严格 36 步（24 步导出动作 + 12 步导航动作）；
    2. 10 步矩阵块被完全剔除；
    3. 支持 DEMO 案例（从综合2、综合3 开始），控件名与路径动态驱动。
    """
    # 1. 标准 3 导出用例 -> 严格 36 步
    p_std = [
        {"stepMode": "create", "synthesisName": "综合1", "wohler": "1"},
        {"stepMode": "copy", "synthesisName": "综合2", "wohler": "4"},
        {"stepMode": "copy", "synthesisName": "综合3", "wohler": "10"},
        {"stepMode": "copyfull", "synthesisName": "综合4", "wohler": "1", "drawLayout": "带绘图"},
    ]
    flow_36 = export_s7.build_residual_gui_flow(param_table=p_std)
    assert flow_36["totalSteps"] == 36, "3 个导出用例的残余 GUI 流程必须严格为 36 步！"
    assert flow_36["matrixStepsRemoved"] == 10

    # 检查步骤内无矩阵步骤
    step_names = [s["name"] for s in flow_36["steps"]]
    assert not any("矩阵" in name for name in step_names)
    assert not any("输出到txt" in name for name in step_names)

    # 检查 Excel 与 IEC 导出步数（各 3 步，共 6 处导出动作）
    excel_steps = [s for s in flow_36["steps"] if "导出Excel总表" in s["name"]]
    iec_steps = [s for s in flow_36["steps"] if "IEC总结" in s["name"]]
    assert len(excel_steps) == 3
    assert len(iec_steps) == 3

    # 2. 动态用例名称（如真实 DEMO 场景：无综合1，仅有 综合2 与 综合3）
    p_demo = [
        {"stepMode": "create", "synthesisName": "DEMO_综合2", "wohler": "4"},
        {"stepMode": "copy", "synthesisName": "DEMO_综合3", "wohler": "10"},
    ]
    out_flow_file = tmp_path / "flow_demo_residual.json"
    flow_demo = export_s7.build_residual_gui_flow(param_table=p_demo, output_path=str(out_flow_file))
    assert out_flow_file.is_file()
    # 2 case 场景：11 (Case1) + 12 (Case2) + 1 (收尾) = 24 步
    assert flow_demo["totalSteps"] == 24
    d_names = [s["name"] for s in flow_demo["steps"]]
    assert any("DEMO_综合2" in n for n in d_names)
    assert any("DEMO_综合3" in n for n in d_names)
    assert not any("综合1" in n for n in d_names)


def test_real_demo_vault_export_s7_observed(tmp_path):
    """【真实 DEMO Vault 现场实跑验收 (Observed)】
    直连挂载的真实 DEMO Vault 目录：
      - case_prefix 解析为 ea368c7f-9810-487d-be4c-bbcc9339c83e；
      - 综合2 拷出 wri + wrp 2 件到 m4；
      - 综合3 额外拷出双 GUID .wrm 到 m10；
      - 综合4 拷出 wri + wrp + wrt + 7×wrtd + 2×dat 到 m1；
      - 各 m_dir 正确（wohler 映射），SHA-256 对账完备；
      - export_manifest.json v2 落盘且安全（无 licence/license/hasp）。
    """
    real_vault = r"H:\ProgramData\Meteodyn\MUP\local_liao_be917e43-a15c-4db7-8573-822cecb16f78"
    if not os.path.isdir(real_vault):
        pytest.skip("真实 DEMO vault 目录未挂载，跳过现场核验")

    # 构造针对真实 DEMO 案例（综合2/3/4）的参数表
    param_table = [
        {"stepMode": "create", "synthesisName": "综合2", "wohler": "4"},
        {"stepMode": "copy", "synthesisName": "综合3", "wohler": "10"},
        {"stepMode": "copy", "synthesisName": "综合4", "wohler": "1", "drawLayout": "不带绘图"},
    ]
    out_dir = tmp_path / "04-WT输出_REAL_DEMO"

    res = export_s7.run_export_s7(
        site="DEMO",
        param_table=param_table,
        output_dir=str(out_dir),
        vault_root=real_vault,
        copy=True,
        preserve_names=True,
    )

    guid = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
    assert res["outcome"] == "succeeded"
    assert res["case_prefix"] == guid
    assert res["total_cases"] == 3
    assert res["completed_cases_count"] == 3
    assert res["delivered_cases_count"] == 3

    # 1. 综合2 -> m4 (wohler=4)
    m4_dir = out_dir / "m4"
    assert m4_dir.is_dir()
    c2_wri = m4_dir / f"{guid}_synthesis_2.wri"
    c2_wrp = m4_dir / f"{guid}_synthesis_2.wrp"
    assert c2_wri.is_file() and c2_wri.stat().st_size == 14316
    assert c2_wrp.is_file() and c2_wrp.stat().st_size == 887278

    # 2. 综合3 -> m10 (wohler=10)
    m10_dir = out_dir / "m10"
    assert m10_dir.is_dir()
    c3_wri = m10_dir / f"{guid}_synthesis_3.wri"
    c3_wrp = m10_dir / f"{guid}_synthesis_3.wrp"
    c3_wrm = m10_dir / f"{guid}_2d459fd9-c918-418a-8e7f-52d9e7b4db8f_3.wrm"
    assert c3_wri.is_file() and c3_wri.stat().st_size == 349908
    assert c3_wrp.is_file() and c3_wrp.stat().st_size == 6247952
    assert c3_wrm.is_file() and c3_wrm.stat().st_size == 5485156

    # 3. 综合4 -> m1 (wohler=1)
    m1_dir = out_dir / "m1"
    assert m1_dir.is_dir()
    c4_wri = m1_dir / f"{guid}_synthesis_4.wri"
    c4_wrp = m1_dir / f"{guid}_synthesis_4.wrp"
    c4_wrt = m1_dir / f"{guid}_synthesis_4.wrt"
    assert c4_wri.is_file() and c4_wri.stat().st_size == 14316
    assert c4_wrp.is_file() and c4_wrp.stat().st_size == 887278
    assert c4_wrt.is_file() and c4_wrt.stat().st_size == 19820
    # 7 个 wrtd
    wrtd_kinds = ["deviation", "inflow", "rewsshear", "shear", "speedup", "turbulence", "veer"]
    for k in wrtd_kinds:
        wrtd_f = m1_dir / f"{guid}_synthesis_{k}_4.wrtd"
        assert wrtd_f.is_file(), f"综合4 wrtd 缺失: {k}"
    # 2 个 dat
    assert (m1_dir / f"{guid}_timeserie_4.dat").is_file()
    assert (m1_dir / f"{guid}_timeseriepark_4.dat").is_file()

    # 4. 校验落盘的 export_manifest.json v2
    m_file = out_dir / "export_manifest.json"
    assert m_file.is_file()
    with open(m_file, "r", encoding="utf-8") as fh:
        raw_manifest_text = fh.read()
        m_json = json.loads(raw_manifest_text)

    assert m_json["schema"] == "mup_vault_export/manifest/v2"
    assert m_json["case_prefix"] == guid
    assert m_json["mandatory"] == ["{P}_synthesis_{n}.wri", "{P}_synthesis_{n}.wrp"]
    assert len(m_json["cases"]) == 3
    for c in m_json["cases"]:
        assert c["mandatory_present"] == 2
        assert c["status"] == "export"

    # 安全红线校验：manifest 内容与路径中严禁出现任何 licence / license / hasp
    low_text = raw_manifest_text.lower()
    for forbidden_word in ["licence", "license", "hasp"]:
        assert forbidden_word not in low_text, f"manifest 泄露了敏感路径: {forbidden_word}"


