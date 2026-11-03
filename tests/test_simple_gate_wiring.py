# encoding: utf-8
"""P0-1/P0-2/P0-4 接线自检：Simple 段间完成门 + 失败即停 + 覆盖审计硬失败。

三个接线用例对应任务要求的三条：
  1. CFD 门未完成 → 不推进下一板块（abort）；
  2. 综合门未完成 → 不推进；且期望综合号集合按 paramTable 行数生成并传给门；
  3. 门 fail-closed → abort 并打印门诊断（不是静默继续）。

另加一条 P0-4：覆盖审计 0 命中 / 项目参数键缺失 → 拒绝启动的诊断文本。

全部用假进程 / 假门 / 假报告路径：不连 MUP、不启动子进程、不读真实运行报告。
真实判据逻辑（mup_completion.CompletionGate）由 tests/test_mup_completion.py 覆盖。
"""
import os
import queue
import sys
import tempfile

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import mup_paths
import WT_Launcher


class FakeProc(object):
    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class FakeRoot(object):
    """记录 root.after 调度；调用 after 即视为「推进到下一板块」。"""

    def __init__(self):
        self.calls = []

    def after(self, _delay, callback):
        self.calls.append(callback)
        return "after-1"


class StubGate(object):
    """脚本化完成门：返回预置帧并记录构造/入参（判据本身由 test_mup_completion 覆盖）。"""

    frames = {}
    instances = []

    def __init__(self, vault_root, timeout_seconds=None, sleep=None):
        self.vault_root = vault_root
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep
        self.cfd_ids = []
        self.site = None
        self.expected_ns = None
        self.send_wall = None
        StubGate.instances.append(self)

    def wait_cfd(self, cfd_id, send_wall=None):
        self.cfd_ids.append(cfd_id)
        self.send_wall = send_wall
        return dict(StubGate.frames["cfd"])

    def wait_comprehensive(self, site, send_wall=None, expected_ns=None,
                           case_prefix=None, site_label=None):
        self.site = site
        self.site_label = site_label
        self.case_prefix = case_prefix
        self.expected_ns = list(expected_ns or [])
        self.send_wall = send_wall
        return dict(StubGate.frames["comprehensive"])


def _make_app(section_key, gate_ctx, returncode=0, queue_keys=None):
    """构造一个不初始化 Tk 的 LauncherApp 实例（object.__new__：只用方法，不建界面）。"""
    app = object.__new__(WT_Launcher.LauncherApp)
    app.process = FakeProc(returncode)
    app._simple_run_queue = list(queue_keys or [section_key])
    app._simple_gate_cancel = False
    app.output_queue = queue.Queue()
    app.root = FakeRoot()
    app.started = []          # 记录推进（root.after 回调）
    app.aborted = []          # 记录 aborted 原因
    app._post_ui = lambda callback: callback()
    app._simple_abort_queue = lambda reason: app.aborted.append(reason)
    app._simple_gate_sleep = lambda _seconds: None
    app.section_key = section_key
    app.gate_ctx = gate_ctx
    return app


def _run_wait(app, ctx):
    """走真实的 _simple_wait_for_next（含退出码/报告/门判定）。"""
    WT_Launcher.LauncherApp._simple_wait_for_next(
        app, 1, 1, "测试板块", 30.0,
        section_key=app.section_key, gate_ctx=ctx)


def _captured_logs(app):
    lines = []
    while not app.output_queue.empty():
        kind, payload = app.output_queue.get_nowait()
        if kind == "log":
            lines.append("{0}|{1}".format(payload[1], payload[0]))
    return lines


def _fresh_vault(tmpdir, logout_ids=()):
    vault = os.path.join(tmpdir, "local_fake_guid")
    os.makedirs(os.path.join(vault, "BIN"), exist_ok=True)
    os.makedirs(os.path.join(vault, "SYNTHESIS_FILES"), exist_ok=True)
    os.makedirs(os.path.join(vault, "RESULT_FILES"), exist_ok=True)
    for cfd_id in logout_ids:
        with open(os.path.join(vault, "BIN", "CALCUL_log_out_%s.xml" % cfd_id),
                  "w", encoding="utf-8") as fh:
            fh.write("<calc/>")
    return vault


def test_cfd_gate_incomplete_does_not_advance(monkeypatch, tmp_path):
    """CFD 门未完成 → abort，且不调度下一板块。"""
    vault = _fresh_vault(str(tmp_path), logout_ids=(7,))
    monkeypatch.setattr(mup_paths, "vault_root", lambda *_a, **_k: vault)
    monkeypatch.setattr(WT_Launcher, "LAST_RUN_REPORT_FILE",
                        str(tmp_path / "no_such_report.json"))
    StubGate.instances = []
    StubGate.frames = {"cfd": {"ok": False, "status": "timeout",
                               "reason": "CFD 超时未完成：进程存在=True 消失=0轮"}}
    monkeypatch.setattr(WT_Launcher, "_completion_gate_module",
                        lambda: _stub_completion(), raising=False)

    import time as _time
    ctx = {"section": "cfd", "site": "", "expected_ns": [], "bin_baseline": {},
           "launch_wall": _time.time() - 30.0, "build_error": ""}
    app = _make_app("cfd", ctx)
    _run_wait(app, ctx)

    assert len(StubGate.instances) == 1
    assert StubGate.instances[0].cfd_ids == [7], "应以发送后新发现的 cfdId 过门"
    assert app.aborted and "CFD" in app.aborted[0]
    assert app.aborted[0].startswith("测试板块 未完成")
    assert app.root.calls == [], "门未通过时不得推进下一板块"


def test_comprehensive_gate_incomplete_does_not_advance(monkeypatch, tmp_path):
    """综合门未完成 → 不推进；期望综合号集合由 paramTable 行数生成（4 行 → [1..4]）。

    另钉死 §5.5-b-7 接线：发送前解析 `case_prefix` 并同时传 site / prefix / label；
    本用例的假 vault 里没有 `{site}_` 文件也没有 GUID 产物，故解析落到 unresolved，
    `case_prefix` 与站点名同值（门据此 fail-closed，不会静默改口径）。
    """
    vault = _fresh_vault(str(tmp_path))
    monkeypatch.setattr(mup_paths, "vault_root", lambda *_a, **_k: vault)
    monkeypatch.setattr(WT_Launcher, "LAST_RUN_REPORT_FILE",
                        str(tmp_path / "no_such_report.json"))
    # 不 spawn 真实 sqlcmd：身份解析只走 vault 目录启发
    monkeypatch.setattr(WT_Launcher.LauncherApp, "_simple_query_case_guid",
                        lambda _self, _site: "", raising=True)
    StubGate.instances = []
    StubGate.frames = {"comprehensive": {
        "ok": False, "status": "timeout",
        "reason": "综合超时未完成，期望综合号=[1, 2, 3, 4] 缺失=2:.wrp"}}
    monkeypatch.setattr(WT_Launcher, "_completion_gate_module",
                        lambda: _stub_completion(), raising=False)

    param_table = tmp_path / "param_table.json"
    param_table.write_text(
        '[{"synthesisName":"综合1","wohler":"1"},'
        '{"synthesisName":"","wohler":"4"},'
        '{"synthesisName":"","wohler":"10"},'
        '{"synthesisName":"","wohler":"1"}]', encoding="utf-8")
    flow = tmp_path / "flow.json"
    flow.write_text('{"paramTable": "param_table.json", "steps": []}', encoding="utf-8")

    work_dir = tmp_path / "YunNanSite"
    work_dir.mkdir()
    app = _make_app("comprehensive", None)
    app.project_work_dir = str(work_dir)
    ctx = WT_Launcher.LauncherApp._simple_gate_build_context(app, "comprehensive", str(flow))
    assert ctx["site"] == "YunNanSite"
    assert ctx["site_label"] == "YunNanSite"
    assert ctx["case_prefix"] == "YunNanSite"
    assert ctx["case_source"] == "unresolved"
    assert ctx["expected_ns"] == [1, 2, 3, 4]

    _run_wait(app, ctx)

    gate = StubGate.instances[0]
    assert gate.site == "YunNanSite"
    assert gate.case_prefix == "YunNanSite", "产物前缀必须传进门（§5.5-b-7）"
    assert gate.site_label == "YunNanSite"
    assert gate.expected_ns == [1, 2, 3, 4], "期望综合号集合必须来自 paramTable 行数"
    assert app.aborted and "综合" in app.aborted[0]
    assert app.root.calls == []
    logs = "\n".join(_captured_logs(app))
    assert "产物前缀" in logs, "日志须同时打印站点与产物前缀，便于现场对账"


def test_gate_fail_closed_aborts_with_diagnostic(monkeypatch, tmp_path):
    """门 fail-closed（进程枚举失败）→ abort，并把门的诊断原样打出来。"""
    vault = _fresh_vault(str(tmp_path), logout_ids=(11,))
    monkeypatch.setattr(mup_paths, "vault_root", lambda *_a, **_k: vault)
    monkeypatch.setattr(WT_Launcher, "LAST_RUN_REPORT_FILE",
                        str(tmp_path / "no_such_report.json"))
    StubGate.instances = []
    StubGate.frames = {"cfd": {"ok": False, "status": "fail-closed",
                               "reason": "DASSolver 进程枚举失败，无法判消失"}}
    monkeypatch.setattr(WT_Launcher, "_completion_gate_module",
                        lambda: _stub_completion(), raising=False)

    import time as _time
    ctx = {"section": "cfd", "site": "", "expected_ns": [], "bin_baseline": {},
           "launch_wall": _time.time() - 30.0, "build_error": ""}
    app = _make_app("cfd", ctx)
    _run_wait(app, ctx)

    logs = "\n".join(_captured_logs(app))
    assert "fail-closed" in logs, "门诊断必须打印出来（不得静默）"
    assert "进程枚举失败" in logs
    assert app.aborted and "fail-closed" in app.aborted[0]
    assert app.root.calls == []


def test_section_exit_code_failure_aborts_queue(monkeypatch, tmp_path):
    """P0-2：非门板块（如 terrain）子进程退出码非 0 → 立即 abort，不推进。"""
    monkeypatch.setattr(WT_Launcher, "LAST_RUN_REPORT_FILE",
                        str(tmp_path / "no_such_report.json"))
    app = _make_app("terrain", None, returncode=1)
    _run_wait(app, None)

    assert app.aborted and "退出码 1" in app.aborted[0]
    assert app.root.calls == []


def test_override_audit_hard_fail_reasons():
    """P0-4：覆盖 0 命中 / 关键项目参数键缺失 → 返回拒绝启动诊断。"""
    app = object.__new__(WT_Launcher.LauncherApp)
    ok_rc = {"projectName": "P", "terrainName": "T", "projectionAbbrev": "CGCS2000_43"}
    clean = {WT_Launcher.wt_project_workdir_parser.OVERRIDE_AUDIT_KEY:
             {"zero_hit": [], "hits": {}, "required": []}}
    assert app._override_audit_problem(clean, ok_rc) == ""

    zero_hit = {WT_Launcher.wt_project_workdir_parser.OVERRIDE_AUDIT_KEY:
                {"zero_hit": ["C:\\old\\mast-tim.txt"], "hits": {}, "required": []}}
    problem = app._override_audit_problem(zero_hit, ok_rc)
    assert "0 命中" in problem and "mast-tim.txt" in problem

    missing = app._override_audit_problem(clean, {"projectName": "P"})
    assert "terrainName" in missing and "projectionAbbrev" in missing


def _stub_completion():
    """用脚本化门替身替换 mup_completion 模块（只替换门类，保留真实匹配器）。"""
    import mup_completion
    import types
    stub = types.SimpleNamespace(
        CompletionGate=StubGate,
        DEFAULT_CLOCK_GRACE=mup_completion.DEFAULT_CLOCK_GRACE,
        parse_logout_id=mup_completion.parse_logout_id,
    )
    return stub
