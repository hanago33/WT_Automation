# encoding: utf-8
"""波-3 接线回归：完成门 → Vault 直取 → 残余 GUI（Excel/IEC + 矩阵块保留）。

覆盖新口径：
  · U-1：复制卡按 `综合{max_n+1}` 递增，不按“副本”匹配；
  · U-6：n 漂移时按 paramTable + 基线 max 落账实际卡号；
  · U-10：DEMO 综合1 被 Cancelled、无产物 → expected_ns 不含 1，gate 不等 1；
  · U-9：.wri/.wrp 直取，matrix/Excel/IEC 保留 GUI；残余流 36 步 Excel/IEC
    + 10 步矩阵块（3 case = 46 步）；
  · manifest 双计数：完成集合（含 copyfull）⊃ 交付集合（mN 非空）。

全部假 vault / 假 app：不跑 MUP、不连真实 DB、不触许可。
"""
import json
import os
import queue
import sys
import time

import pytest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import mup_paths
import WT_Launcher
import export_s7_wiring as wiring


GUID = "ea368c7f-9810-487d-be4c-bbcc9339c83e"


def _make_vault(base, cases=(2, 3, 4), missing=()):
    vault = os.path.join(str(base), "vault", "local_fake")
    syn = os.path.join(vault, "SYNTHESIS_FILES")
    os.makedirs(syn, exist_ok=True)
    for n in cases:
        for ext in (".wri", ".wrp"):
            if (n, ext) in missing:
                continue
            with open(os.path.join(syn, "%s_synthesis_%d%s" % (GUID, n, ext)),
                      "w", encoding="utf-8") as fh:
                fh.write("content:%d%s" % (n, ext))
    return vault


def _demo_rows():
    return [
        {"stepMode": "create", "synthesisName": "综合2", "wohler": "4",
         "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "综合3", "wohler": "10",
         "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "综合4", "wohler": "1",
         "drawLayout": "不带绘图"},
    ]


def test_actual_card_numbers_u1_u6_u10():
    """显式卡号优先；空名按 max+1 递增；取消卡剔除但序号仍前进。"""
    standard = [{"synthesisName": "综合1"}] + [{"synthesisName": ""}] * 3
    assert wiring.actual_card_numbers(standard, baseline_max=0) == [1, 2, 3, 4]

    # DEMO：只含 2/3/4（综合1 被 Cancel）→ 不能假设 [1..N]
    demo = _demo_rows()
    assert wiring.actual_card_numbers(demo, baseline_max=4) == [2, 3, 4]

    # n 漂移：基线 max=4，4 个 copy 行 → 实际新卡 5/6/7/8
    assert wiring.actual_card_numbers([{"synthesisName": ""}] * 4,
                                      baseline_max=4) == [5, 6, 7, 8]

    # 取消卡 1 从期望集合剔除
    assert wiring.actual_card_numbers(standard, baseline_max=0,
                                      excluded_ns=[1]) == [2, 3, 4]


def test_db_expected_numbers_new_and_cancelled():
    baseline = [1, 2, 3, 4]
    rows = [
        {"n": 1, "error": "1000#Cancelled"},
        {"n": 2, "error": ""},
        {"n": 3, "error": ""},
        {"n": 4, "error": ""},
    ]
    assert wiring.db_expected_numbers(rows, baseline) == ([], "db-none", [1])

    # 新增 5（成功）+ 6（取消）→ 只等 5
    assert wiring.db_expected_numbers(
        rows + [{"n": 5, "error": ""}, {"n": 6, "error": "1000#Cancelled"}],
        baseline) == ([5], "db-new", [1, 6])

    # 新增卡全部取消 → fail-closed，不得回退成 [1..N]
    assert wiring.db_expected_numbers(
        rows + [{"n": 5, "error": "1000#Cancelled"}],
        baseline) == ([], "db-new-all-cancelled", [1, 5])


def test_annotate_rows_filters_cancelled_one_and_fills_names():
    selected, missing, simulated = wiring.annotate_rows_for_expected(
        [{"synthesisName": "综合1", "wohler": "1"},
         {"synthesisName": "", "wohler": "4"},
         {"synthesisName": "", "wohler": "10"}],
        expected_ns=[2, 3], baseline_max=1)
    assert simulated == [1, 2, 3]
    assert missing == []
    assert [r["index"] for r in selected] == [2, 3]
    assert [r["synthesisName"] for r in selected] == ["综合2", "综合3"]


def test_annotate_rows_uses_created_order_for_n_drift():
    """n 漂移：paramTable 首行仍写“综合1”，实际建卡 5..8 → 按创建顺序对齐。"""
    rows = [
        {"stepMode": "copy", "synthesisName": "综合1", "wohler": "1"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "4"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "10"},
        {"stepMode": "copy", "synthesisName": "", "wohler": "1"},
    ]
    selected, missing, simulated = wiring.annotate_rows_for_expected(
        rows, expected_ns=[5, 6, 7, 8], baseline_max=4,
        created_ns=[5, 6, 7, 8])
    assert missing == []
    assert simulated == [5, 6, 7, 8]
    assert [r["index"] for r in selected] == [5, 6, 7, 8]
    assert [r["synthesisName"] for r in selected] == ["综合5", "综合6", "综合7", "综合8"]

    # 中间卡被取消：仍按创建顺序映射，只保留未取消的期望号
    selected2, missing2, _ = wiring.annotate_rows_for_expected(
        rows, expected_ns=[5, 7, 8], baseline_max=4,
        created_ns=[5, 6, 7, 8])
    assert missing2 == []
    assert [r["index"] for r in selected2] == [5, 7, 8]
    assert [r["synthesisName"] for r in selected2] == ["综合5", "综合7", "综合8"]

    # create 行的自定义名保留（流程会键入该名）
    selected3, missing3, _ = wiring.annotate_rows_for_expected(
        [{"stepMode": "create", "synthesisName": "自定义A", "wohler": "1"}],
        expected_ns=[1], baseline_max=0, created_ns=[1])
    assert missing3 == []
    assert selected3[0]["synthesisName"] == "自定义A"


def test_export_and_residual_flow_double_count_and_matrix_retained(tmp_path):
    vault = _make_vault(tmp_path)
    out = tmp_path / "04-WT输出"
    flow_path = tmp_path / "flow.json"

    res = wiring.export_and_build_residual(
        site_label="DEMO", case_prefix=GUID, param_rows=_demo_rows(),
        expected_ns=[2, 3, 4], output_dir=str(out), vault_root=vault,
        baseline_max=4, flow_output_path=str(flow_path), include_matrix=True)

    assert res["ok"] is True
    assert res["completed_cases_count"] == 3
    assert res["delivered_cases_count"] == 3
    assert res["copied_count"] == 6
    assert os.path.isfile(res["manifest_path"])
    manifest = json.load(open(res["manifest_path"], encoding="utf-8"))
    assert manifest["site"] == "DEMO"
    assert manifest["case_prefix"] == GUID
    assert manifest["mapping"] == {"综合2": "m4", "综合3": "m10", "综合4": "m1"}
    assert manifest["expected_ns"] == [2, 3, 4]
    assert manifest["completed_cases_count"] == 3
    assert manifest["delivered_cases_count"] == 3
    assert [c["index"] for c in manifest["cases"]] == [2, 3, 4]
    assert all(c["mandatory_present"] == 2 for c in manifest["cases"])

    # 直取落盘：m4/m10/m1 两件套
    for m_dir, n in (("m4", 2), ("m10", 3), ("m1", 4)):
        for ext in (".wri", ".wrp"):
            assert (out / m_dir / ("%s_synthesis_%d%s" % (GUID, n, ext))).is_file()

    # 残余 GUI：36 步 Excel/IEC + 10 步矩阵块 = 46 步（3 case）
    flow = res["residual_flow"]
    assert flow["totalSteps"] == 46
    assert flow["excelIecSteps"] == 36
    assert flow["matrixStepsRetained"] == 10
    assert flow["matrixStepsRemoved"] == 0
    assert flow["exportedCases"] == ["综合2", "综合3", "综合4"]
    names = [s["name"] for s in flow["steps"]]
    assert not any("综合1" in n for n in names), "DEMO 无综合1，不得生成综合1步骤"
    assert any("矩阵" in n or "输出到txt" in n for n in names), "U-9：矩阵块必须保留"

    # 首个 case 选择必须按卡名（不能沿用 ListItem found_index=0 选中综合1）
    select = next(s for s in flow["steps"] if s["id"] == "s7_case1_step_1")
    ctrl = next(c for c in select["controls"] if c["name"] == "综合2")
    assert ctrl["targetMethod"] == "name,control_type"
    assert ctrl["targetValue"] == "综合2,Text"


def test_completed_set_superset_of_delivery_set_with_copyfull(tmp_path):
    """copyfull/带绘图（综合4）计入完成集合、不进交付集合；manifest 两个数分开。"""
    vault = _make_vault(tmp_path, cases=(1, 2, 3, 4))
    out = tmp_path / "04-WT输出"
    rows = [
        {"stepMode": "create", "synthesisName": "综合1", "wohler": "1",
         "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "综合2", "wohler": "4",
         "drawLayout": "不带绘图"},
        {"stepMode": "copy", "synthesisName": "综合3", "wohler": "10",
         "drawLayout": "不带绘图"},
        {"stepMode": "copyfull", "synthesisName": "综合4", "wohler": "1",
         "drawLayout": "带绘图"},
    ]
    res = wiring.export_and_build_residual(
        site_label="DEMO", case_prefix=GUID, param_rows=rows,
        expected_ns=[1, 2, 3, 4], output_dir=str(out), vault_root=vault,
        baseline_max=0, flow_output_path=str(tmp_path / "flow.json"),
        include_matrix=True)
    assert res["ok"] is True
    assert res["completed_cases_count"] == 4, "完成集合含 copyfull 综合4"
    assert res["delivered_cases_count"] == 3, "交付集合仅 mN 非空的 1/2/3"

    manifest = json.load(open(res["manifest_path"], encoding="utf-8"))
    assert manifest["completed_cases_count"] == 4
    assert manifest["delivered_cases_count"] == 3
    c4 = next(c for c in manifest["cases"] if c["index"] == 4)
    assert c4["status"] == "completed_with_gaps"
    assert c4["m_dir"] == "(none)"
    assert c4["mandatory_present"] == 2  # 算完了，只是不交付

    # 残余 GUI 只为交付集合生成：3 case → 36 步 Excel/IEC + 10 步矩阵 = 46
    flow = res["residual_flow"]
    assert flow["exportedCases"] == ["综合1", "综合2", "综合3"]
    assert flow["totalSteps"] == 46
    assert not (out / "mNone").exists()
    assert not (out / "(none)").exists()


def test_export_and_residual_missing_wrp_fail_closed(tmp_path):
    vault = _make_vault(tmp_path, cases=(2, 3, 4), missing={(3, ".wrp")})
    out = tmp_path / "04-WT输出"
    res = wiring.export_and_build_residual(
        site_label="DEMO", case_prefix=GUID, param_rows=_demo_rows(),
        expected_ns=[2, 3, 4], output_dir=str(out), vault_root=vault,
        baseline_max=4, flow_output_path=str(tmp_path / "flow.json"),
        include_matrix=True)
    assert res["ok"] is False
    assert "EXPECTED_NS_NOT_COMPLETED" in res["failure_reason"]
    assert res["missing_expected"] == [3]
    assert res["residual_flow"] is None


def test_wrong_site_zero_match_fail_closed_with_reason(tmp_path):
    """错 site：不得生成空 manifest 当成功；必须零匹配 + 明确原因。"""
    vault = _make_vault(tmp_path)
    out = tmp_path / "04-WT输出_wrong"
    diag = {}

    def _db_zero(_site, case_name=None):
        # 与生产 _default_db_query_cases 同语义：DB 明确 0 命中，禁止单 GUID 启发误套
        diag["db_cases_found"] = 0
        return []

    res = wiring.export_and_build_residual(
        site_label="NoSuchSite_DefinitelyWrong", case_prefix="",
        param_rows=_demo_rows(), expected_ns=[2, 3, 4],
        output_dir=str(out), vault_root=vault, baseline_max=4,
        flow_output_path=str(tmp_path / "flow.json"),
        diagnostics=diag, db_query_fn=_db_zero)
    assert res["ok"] is False
    assert "ZERO_MATCH_OR_CASE_PREFIX_UNRESOLVED" in res["failure_reason"]
    assert res["completed_cases_count"] == 0
    assert res["delivered_cases_count"] == 0
    assert res["residual_flow"] is None
    saved = json.load(open(res["manifest_path"], encoding="utf-8"))
    assert saved["overall_status"] == "failed"
    assert saved["case_prefix"] == ""


def test_launcher_export_after_gate_mounts_residual_flow(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    monkeypatch.setattr(WT_Launcher, "BASE_DIR", str(tmp_path))
    work = tmp_path / "Project"
    work.mkdir()

    app = object.__new__(WT_Launcher.LauncherApp)
    app.project_work_dir = str(work)
    app.output_queue = queue.Queue()
    app.simple_section_vars = {"export_result": {"path": ""}}
    app._simple_run_queue = ["export_result"]
    app._simple_gate_cancel = False

    ctx = {
        "site": "DEMO", "site_label": "DEMO", "case_prefix": GUID,
        "case_source": "db", "expected_ns": [2, 3, 4],
        "expected_ns_source": "db-new", "param_rows": _demo_rows(),
        "baseline_synthesis": [1, 2, 3, 4], "flow_path": "",
    }
    frame = {"casePrefix": GUID, "siteLabel": "DEMO"}
    ok, detail = app._simple_export_after_gate(ctx, frame, vault, [2, 3, 4])
    assert ok is True
    assert "完成集合=3" in detail and "交付集合=3" in detail
    assert os.path.isfile(ctx["export_manifest_path"])
    flow_path = app._simple_runtime_flow_overrides["export_result"]
    assert os.path.isfile(flow_path)
    flow = json.load(open(flow_path, encoding="utf-8"))
    assert flow["totalSteps"] == 46
    assert flow["runtimeConfig"]["outputDir"] == str(work / "04-WT输出")

    logs = []
    while not app.output_queue.empty():
        kind, payload = app.output_queue.get_nowait()
        if kind == "log":
            logs.append(payload[0])
    text = "\n".join(logs)
    assert "完成集合=3" in text
    assert "交付集合=3" in text
    assert "残余 GUI 已挂载" in text


def test_gate_pass_calls_export_and_mounts_flow(tmp_path, monkeypatch):
    """端到端（假 gate/假 vault）：门通过 → 直取 → manifest → 残余流挂载。"""
    vault = _make_vault(tmp_path, cases=(2, 3, 4))
    monkeypatch.setattr(WT_Launcher, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(mup_paths, "vault_root", lambda *_a, **_k: vault)
    work = tmp_path / "DEMO"
    work.mkdir()

    flow = tmp_path / "flow.json"
    param = tmp_path / "param.json"
    param.write_text(json.dumps(_demo_rows(), ensure_ascii=False), encoding="utf-8")
    flow.write_text(json.dumps({"paramTable": "param.json", "steps": []},
                               ensure_ascii=False), encoding="utf-8")

    rows = [
        {"n": 1, "error": "1000#Cancelled"},
        {"n": 2, "error": ""},
        {"n": 3, "error": ""},
        {"n": 4, "error": ""},
    ]

    class StubGate:
        instances = []

        def __init__(self, *a, **k):
            self.expected_ns = None
            StubGate.instances.append(self)

        def wait_comprehensive(self, site, send_wall=None, expected_ns=None,
                               case_prefix=None, site_label=None):
            self.expected_ns = list(expected_ns or [])
            return {"ok": True, "status": "completed", "reason": "stub",
                    "casePrefix": GUID, "siteLabel": "DEMO",
                    "expectedNs": self.expected_ns}

    import types
    monkeypatch.setattr(
        WT_Launcher, "_completion_gate_module",
        lambda: types.SimpleNamespace(CompletionGate=StubGate,
                                      DEFAULT_CLOCK_GRACE=2.0), raising=False)

    app = object.__new__(WT_Launcher.LauncherApp)
    app.project_work_dir = str(work)
    app.output_queue = queue.Queue()
    app.simple_section_vars = {"export_result": {"path": ""}}
    app._simple_run_queue = ["comprehensive", "export_result"]
    app._simple_gate_cancel = False
    app._simple_gate_sleep = lambda _s: None
    app._simple_query_case_guid = lambda _site: GUID
    app._simple_query_synthesis_numbers = lambda _prefix: list(rows)

    ctx = app._simple_gate_build_context("comprehensive", str(flow))
    assert ctx["expected_ns"] == [2, 3, 4]  # 不等被 Cancel 的综合1
    ok, detail = app._simple_gate_wait_section("comprehensive", ctx)

    assert ok is True, detail
    assert StubGate.instances[0].expected_ns == [2, 3, 4]
    assert "完成集合=3" in detail and "交付集合=3" in detail
    assert os.path.isfile(ctx["export_manifest_path"])
    manifest = json.load(open(ctx["export_manifest_path"], encoding="utf-8"))
    assert [c["index"] for c in manifest["cases"]] == [2, 3, 4]
    flow_path = app._simple_runtime_flow_overrides["export_result"]
    assert os.path.isfile(flow_path)
    assert json.load(open(flow_path, encoding="utf-8"))["totalSteps"] == 46


def test_launcher_context_expected_ns_for_demo_without_one(tmp_path, monkeypatch):
    """DEMO 用例：综合1 卡在但被 Cancel、无产物 → ctx.expected_ns 不得含 1。"""
    work = tmp_path / "DEMO"
    work.mkdir()
    flow = tmp_path / "flow.json"
    param = tmp_path / "param.json"
    param.write_text(json.dumps(_demo_rows(), ensure_ascii=False), encoding="utf-8")
    flow.write_text(json.dumps({"paramTable": "param.json", "steps": []},
                               ensure_ascii=False), encoding="utf-8")

    app = object.__new__(WT_Launcher.LauncherApp)
    app.project_work_dir = str(work)
    app.output_queue = queue.Queue()
    app._simple_query_case_guid = lambda _site: GUID
    app._simple_query_synthesis_numbers = lambda _prefix: [
        {"n": 1, "error": "1000#Cancelled"},
        {"n": 2, "error": ""},
        {"n": 3, "error": ""},
        {"n": 4, "error": ""},
    ]

    ctx = app._simple_gate_build_context("comprehensive", str(flow))
    assert ctx["case_prefix"] == GUID
    assert ctx["expected_ns"] == [2, 3, 4]
    assert 1 not in ctx["expected_ns"]


def test_gate_passes_actual_new_card_numbers(monkeypatch, tmp_path):
    """发送后 DB 出现新卡 5/6/7/8 → gate 用实际新卡集合，而非 paramTable 的 1..N。"""
    work = tmp_path / "DEMO"
    work.mkdir()
    flow = tmp_path / "flow.json"
    param = tmp_path / "param.json"
    param.write_text(json.dumps(
        [{"synthesisName": ""} for _ in range(4)], ensure_ascii=False), encoding="utf-8")
    flow.write_text(json.dumps({"paramTable": "param.json", "steps": []},
                               ensure_ascii=False), encoding="utf-8")

    vault = tmp_path / "vault"
    (vault / "SYNTHESIS_FILES").mkdir(parents=True)
    import mup_paths
    monkeypatch.setattr(mup_paths, "vault_root", lambda *_a, **_k: str(vault))

    app = object.__new__(WT_Launcher.LauncherApp)
    app.project_work_dir = str(work)
    app.output_queue = queue.Queue()
    app._simple_query_case_guid = lambda _site: GUID
    app._simple_query_synthesis_numbers = lambda _prefix: [
        {"n": 1, "error": ""}, {"n": 2, "error": ""}, {"n": 3, "error": ""},
        {"n": 4, "error": ""},
    ]

    ctx = app._simple_gate_build_context("comprehensive", str(flow))
    assert ctx["baseline_synthesis"] == [1, 2, 3, 4]
    # 重跑发送后快照：基线 1..4，新卡 5..8
    app._simple_query_synthesis_numbers = lambda _prefix: [
        {"n": 1, "error": ""}, {"n": 2, "error": ""}, {"n": 3, "error": ""},
        {"n": 4, "error": ""}, {"n": 5, "error": ""}, {"n": 6, "error": ""},
        {"n": 7, "error": ""}, {"n": 8, "error": ""},
    ]

    class StubGate:
        instances = []

        def __init__(self, *a, **k):
            self.expected_ns = None
            self.frame = {"ok": False, "status": "timeout", "reason": "stub"}
            StubGate.instances.append(self)

        def wait_comprehensive(self, site, send_wall=None, expected_ns=None,
                               case_prefix=None, site_label=None):
            self.expected_ns = list(expected_ns or [])
            return dict(self.frame)

    import types
    monkeypatch.setattr(
        WT_Launcher, "_completion_gate_module",
        lambda: types.SimpleNamespace(CompletionGate=StubGate,
                                      DEFAULT_CLOCK_GRACE=2.0), raising=False)
    ok, _reason = app._simple_gate_wait_section("comprehensive", ctx)
    assert ok is False
    assert StubGate.instances[0].expected_ns == [5, 6, 7, 8]
