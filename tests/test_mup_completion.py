# encoding: utf-8
"""保守完成门 `mup_completion` 的离线自检测试。

覆盖：
1. 综合 oracle（2026-11-02 判据改口：必出 = `.wri + .wrp`，前缀 = CaseGUID）：
   - 必出 2 件套齐套 + 新鲜 → completed；
   - S4 新预期：只有旧口径 xml/txt/matrix → 不认，不 completed（旧预期是 completed）；
   - 缺必出（只 .wri 缺 .wrp）→ failed(partial)；
   - 发送时刻前残留拒绝；
   - expected_ns 集合齐套判定；
   - 站点自动发现。
2. CFD oracle：
   - S1 反例：本地无 DASSolver 进程（list_procs=None 或 []），旧实现超时/fail-closed，新实现放行并记 solverSeen=False；
   - S2 反例：status 为五词表外词（如 Finished_With_Warnings），旧实现超时，新实现放行并记 diagnostics.logoutStatus；
   - S3 反例：上轮残留同号 logout 未重写，随后新结果到达，旧实现秒回假阳性，新实现正确拒绝 stale 并超时；
   - 坏 XML / 文件不可读真解析异常 → fail-closed。
3. 文件名匹配器与新鲜度判定（委托 mup_vault_names）。

全部用 FakeClock + 假扫描 + 假进程枚举 + 假 sleep：零真实 MUP、零真实进程、零真实盘。
"""
import os
import sys
import tempfile
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import mup_completion as m
import mup_vault_names as mvn
from mup_calcul_logout_parser import CompletionState, ProgressReport


class FakeScan(object):
    """假 vault 扫描器：{dir: {name: (size, mtime)}}，测试直接改 state 脚本化时间线。"""

    def __init__(self, state):
        self.state = state

    def __call__(self, root, subs):
        return {sub: dict(self.state.get(sub, {})) for sub in (subs or [])}


def full_syn(site="SiteA", n=1, mt=2000.0, size=100):
    """生成"必出 2 件套 + 诊断族 + 旧口径残留"混合字典（新口径下必出=.wri/.wrp）。"""
    entries = {("%s_synthesis_%d%s" % (site, n, e)): (size, mt)
               for e in m.SYNTH_EXTENSIONS}
    entries["%s_synthesis_deviation_%d.wrtd" % (site, n)] = (size, mt)
    entries["%s_timeserie_%d.dat" % (site, n)] = (size, mt)
    entries["%s_matrix_%d.txt" % (site, n)] = (size, mt)
    return entries


def mandatory_syn(site="SiteA", n=1, mt=2000.0, size=100):
    """只放物理 Vault 的必出 2 件套（.wri + .wrp）。"""
    return {("%s_synthesis_%d%s" % (site, n, e)): (size, mt)
            for e in mvn.VAULT_MANDATORY_EXTENSIONS}


class SynthesisGateTests(unittest.TestCase):
    def _gate(self, state, **kw):
        kw.setdefault("poll_interval", 2.0)
        kw.setdefault("silence_seconds", 10.0)
        kw.setdefault("list_procs", lambda: [])
        kw.setdefault("sleep", lambda s: None)
        clock = kw.pop("clock", m.FakeClock(2000.0))
        return m.CompletionGate(r"X:\vault", clock=clock,
                                scan=FakeScan(state), **kw), clock

    def test_comprehensive_success_after_complete_and_silence(self):
        gate, clk = self._gate({m.SYNTHESIS_DIR: full_syn()})
        clk.advance(12.0)  # 静默 12s > 10s
        r = gate.wait_comprehensive("SiteA", send_wall=1999.0)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["targetN"], 1)
        self.assertEqual(r["missingRequired"], [])

    def test_case_prefix_resolved_from_vault_when_caller_omits_it(self):
        """调用方只给人类站点名：门内用 Vault 唯一 GUID 启发解析前缀（假阴 #1 修复）。

        生产 wires（Launcher）会在发送前解析；这里是兜底：即使漏传，也不会把
        `DEMO_synthesis_*` 当成前缀而直接 not_started。
        """
        guid = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
        vault = tempfile.mkdtemp()
        os.makedirs(os.path.join(vault, m.SYNTHESIS_DIR), exist_ok=True)
        for name in mvn.synthesis_vault_mandatory_products(guid, 2):
            open(os.path.join(vault, m.SYNTHESIS_DIR, name), "wb").close()
        gate = m.CompletionGate(vault, clock=m.FakeClock(2000.0),
                                scan=FakeScan({m.SYNTHESIS_DIR: mandatory_syn(guid, 2)}),
                                sleep=lambda s: None, list_procs=lambda: [])
        r = gate.wait_comprehensive("DEMO", send_wall=1999.0, expected_ns=[2])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["casePrefix"], guid)
        self.assertEqual(r["caseSource"], "vault-single-guid")
        self.assertEqual(r["siteLabel"], "DEMO")

    def test_comprehensive_missing_required_is_failed_partial(self):
        """§5.2-5 + §0.1：已见本轮家族产物（.wri）但到点仍缺 .wrp → failed(partial)。

        旧门对同一场景一律报 timeout（丢掉了"综合已起算但产物不全"的信息）；
        改口后 .wri 进 family、.wrp 是必出，故判 failed + failedKind="partial"。
        """
        partial = {"SiteA_synthesis_1.wri": (100, 2000.0)}
        gate, clk = self._gate({m.SYNTHESIS_DIR: partial}, timeout_seconds=6.0)
        clk.advance(200.0)
        r = gate.wait_comprehensive("SiteA", send_wall=1999.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["failedKind"], "partial")
        self.assertTrue(r["missingRequired"])
        self.assertIn("SiteA_synthesis_1.wrp", r["missingRequired"])

    def test_comprehensive_residue_before_send_rejected(self):
        # mtime(1000) 早于发送时刻(1999) → 全部按上轮残留 → 不会齐套 → 超时
        gate, clk = self._gate({m.SYNTHESIS_DIR: full_syn(mt=1000.0)},
                               timeout_seconds=6.0)
        clk.advance(200.0)
        r = gate.wait_comprehensive("SiteA", send_wall=1999.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "timeout")

    def test_comprehensive_expected_ns_requires_every_synthesis(self):
        """期望集合 [1,2] 只齐了 n=1 → 不放行（多张综合不得提前放行）。"""
        gate, clk = self._gate({m.SYNTHESIS_DIR: full_syn(n=1)}, timeout_seconds=6.0)
        clk.advance(200.0)
        r = gate.wait_comprehensive("SiteA", send_wall=1999.0, expected_ns=[1, 2])
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "timeout")
        self.assertEqual(r["expectedNs"], [1, 2])
        self.assertIn(2, r["missingByN"])
        self.assertNotIn(1, r["missingByN"])

    def test_comprehensive_expected_ns_all_complete_passes(self):
        """期望集合 [1,2] 全部齐套 + 静默 → 放行。"""
        state = {m.SYNTHESIS_DIR: dict(full_syn(n=1), **full_syn(n=2))}
        gate, clk = self._gate(state)
        clk.advance(12.0)
        r = gate.wait_comprehensive("SiteA", send_wall=1999.0, expected_ns=[1, 2])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["expectedNs"], [1, 2])

    def test_comprehensive_auto_discovers_site(self):
        """site 缺省 → 从 SYNTHESIS_FILES 命名族自动发现站点并保留原大小写。"""
        gate, clk = self._gate({m.SYNTHESIS_DIR: full_syn(site="SiteB")})
        clk.advance(12.0)
        r = gate.wait_comprehensive("", send_wall=1999.0)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["targetSite"], "SiteB")

    def test_comprehensive_s4_legacy_products_are_not_completion(self):
        """S4 新预期（2026-11-02 §0.1）：只有旧口径 xml/txt/matrix → **不 completed**。

        旧预期是 completed（三项即放行）。真实 DEMO 里这些文件零命中，它们是 GUI 导出
        副产物而非计算产物；新口径只认 `.wri + .wrp`，故它们连"已起算"都不算。
        """
        s4_files = {
            "SiteA_synthesis_1.xml": (100, 2000.0),
            "SiteA_synthesis_1.txt": (100, 2000.0),
            "SiteA_matrix_1.txt": (100, 2000.0),
        }
        gate, clk = self._gate({m.SYNTHESIS_DIR: s4_files}, timeout_seconds=6.0)
        clk.advance(200.0)
        r = gate.wait_comprehensive("SiteA", send_wall=1999.0)
        self.assertFalse(r["ok"], "S4: 旧口径三项不得再放行")
        self.assertNotEqual(r["status"], "completed")
        self.assertIn("SiteA_synthesis_1.wri", r["missingRequired"])
        self.assertIn("SiteA_synthesis_1.wrp", r["missingRequired"])

    def test_case_prefix_passed_by_caller_is_authoritative(self):
        """接线层已解析的 case_prefix 优先：watcher 按 GUID 前缀找产物，label 只作报告。"""
        guid = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
        gate, clk = self._gate({m.SYNTHESIS_DIR: mandatory_syn(site=guid, n=2)})
        clk.advance(12.0)
        r = gate.wait_comprehensive("DEMO", send_wall=1999.0, case_prefix=guid,
                                    site_label="DEMO", expected_ns=[2])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["casePrefix"], guid)
        self.assertEqual(r["siteLabel"], "DEMO")
        self.assertEqual(r["caseSource"], "caller")


class CfdGateTests(unittest.TestCase):
    def test_cfd_success_solver_gone_logout_done_result_delta(self):
        """标准成功用例：起算后 logout 与结果出现，结果稳定后放行。"""
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, m.BIN_DIR), exist_ok=True)
        os.makedirs(os.path.join(tmp, m.RESULT_DIR), exist_ok=True)

        state = {m.BIN_DIR: {}, m.RESULT_DIR: {}}
        phase = {"n": 0}

        def scan(root, subs):
            if phase["n"] >= 1:
                # 真实 logout 写完即固定 (size,mtime)；结果文件按批落盘后亦不再变。
                state[m.BIN_DIR]["CALCUL_log_out_7.xml"] = (20, 1001.0)
                state[m.RESULT_DIR]["7_DR_0001.xml"] = (40, 1001.0)
            return {sub: dict(state.get(sub, {})) for sub in (subs or [])}

        def procs():
            return ["DASSolver_1.2.3.exe"] if phase["n"] == 0 else []

        def sleeping(_s):
            phase["n"] += 1

        def fake_logout_reader(_path):
            return ProgressReport(mesh="m"), CompletionState(status="completed")

        gate = m.CompletionGate(
            tmp, silence_seconds=2.0, poll_interval=2.0,
            clock=m.FakeClock(1000.0),
            scan=scan, list_procs=procs, sleep=sleeping,
            logout_reader=fake_logout_reader)
        gate.clock.advance(30.0)
        r = gate.wait_cfd(7, send_wall=998.0)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["status"], "completed")
        self.assertIn("7_DR_0001.xml", r["newResults"])

    def test_cfd_logout_parse_error_fails_closed(self):
        """logout 真·解析异常（reader 抛）⇒ fail-closed，不静默继续。"""
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, m.BIN_DIR), exist_ok=True)
        os.makedirs(os.path.join(tmp, m.RESULT_DIR), exist_ok=True)
        state = {m.BIN_DIR: {"CALCUL_log_out_7.xml": (20, 1001.0)},
                 m.RESULT_DIR: {"7_DR_0001.xml": (40, 1001.0)}}

        def scan(root, subs):
            return {sub: dict(state.get(sub, {})) for sub in (subs or [])}

        def boom(_p):
            raise IOError("unreadable xml file")

        gate = m.CompletionGate(
            tmp, silence_seconds=2.0, poll_interval=2.0,
            clock=m.FakeClock(1000.0),
            scan=scan, list_procs=lambda: ["DASSolver_1.exe"],
            sleep=lambda s: None, logout_reader=boom)
        gate.clock.advance(6.0)
        r = gate.wait_cfd(7, send_wall=999.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "fail-closed")
        self.assertIn("解析异常", r["reason"])

    def test_cfd_s1_no_dassolver_process_still_completes(self):
        """S1 反例：本地无 DASSolver 进程（list_procs=lambda: [] 或 None）。

        旧实现把 DASSolver 进程消失作为硬条件，本机无 solver 导致永远"消失=0轮"必超时（假阴性）；
        新实现将进程枚举降为 diagnostics.solverSeen，即使本地无进程亦能正常 completed。
        """
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, m.BIN_DIR), exist_ok=True)
        os.makedirs(os.path.join(tmp, m.RESULT_DIR), exist_ok=True)
        state = {m.BIN_DIR: {}, m.RESULT_DIR: {}}
        phase = {"n": 0}

        def scan(root, subs):
            if phase["n"] >= 1:
                state[m.BIN_DIR]["CALCUL_log_out_7.xml"] = (20, 1001.0)
                state[m.RESULT_DIR]["7_DR_0001.xml"] = (40, 1001.0)
            return {sub: dict(state.get(sub, {})) for sub in (subs or [])}

        def reader(_p):
            return ProgressReport(), CompletionState(status="completed")

        def sleeping(_s):
            phase["n"] += 1

        gate = m.CompletionGate(
            tmp, silence_seconds=0.0, poll_interval=2.0,
            clock=m.FakeClock(1000.0),
            scan=scan,
            list_procs=lambda: [],   # 本机完全无 DASSolver 进程
            sleep=sleeping, logout_reader=reader)
        gate.clock.advance(10.0)
        r = gate.wait_cfd(7, send_wall=999.0)
        self.assertTrue(r["ok"], "S1: 无 DASSolver 进程不应超时，应完成放行")
        self.assertEqual(r["status"], "completed")
        self.assertFalse(r["solverSeen"], "进程未出现应如实反映在 solverSeen")
        self.assertFalse(r["diagnostics"]["solverSeen"])

    def test_cfd_s2_status_outside_five_words_completes(self):
        """S2 反例：logout status 为五词完成表外词（Finished_With_Warnings）。

        旧实现硬断言 status ∈ ('completed','finished','success','done','ok')，导致表外状态永远超时（假阴性）；
        新实现按 U-1 裁决将 status 降为 diagnostics，不作完成硬门，正常放行。
        """
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, m.BIN_DIR), exist_ok=True)
        os.makedirs(os.path.join(tmp, m.RESULT_DIR), exist_ok=True)
        state = {m.BIN_DIR: {}, m.RESULT_DIR: {}}
        phase = {"n": 0}

        def scan(root, subs):
            if phase["n"] >= 1:
                state[m.BIN_DIR]["CALCUL_log_out_7.xml"] = (20, 1001.0)
                state[m.RESULT_DIR]["7_DR_0001.xml"] = (40, 1001.0)
            return {sub: dict(state.get(sub, {})) for sub in (subs or [])}

        def reader(_p):
            return ProgressReport(), CompletionState(status="Finished_With_Warnings")

        def sleeping(_s):
            phase["n"] += 1

        gate = m.CompletionGate(
            tmp, silence_seconds=0.0, poll_interval=2.0,
            clock=m.FakeClock(1000.0),
            scan=scan, list_procs=lambda: [],
            sleep=sleeping, logout_reader=reader)
        gate.clock.advance(10.0)
        r = gate.wait_cfd(7, send_wall=999.0)
        self.assertTrue(r["ok"], "S2: status 为 Finished_With_Warnings 应放行，不应硬性超时")
        self.assertEqual(r["status"], "completed")
        self.assertEqual(r["logoutStatus"], "finished_with_warnings")
        self.assertEqual(r["diagnostics"]["logoutStatus"], "finished_with_warnings")

    def test_cfd_s3_stale_logout_rejected_times_out(self):
        """S3 反例：BIN 里只有上一轮残留的同号 completed logout（mtime 过旧），随后新结果到达。

        旧实现无 mtime 下界检查，直接命中旧 logout 发生秒回（假阳性）；
        新实现依 §2.3.1 严格要求 mtime ≥ send_wall - clock_grace 且不在基线（或已更新），拒绝残留并超时。
        """
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, m.BIN_DIR), exist_ok=True)
        os.makedirs(os.path.join(tmp, m.RESULT_DIR), exist_ok=True)
        # 上一轮残留：mtime 为 500.0（远远早于 send_wall 999.0）
        state = {m.BIN_DIR: {"CALCUL_log_out_7.xml": (20, 500.0)},
                 m.RESULT_DIR: {"7_DR_0001.xml": (40, 1001.0)}}

        def scan(root, subs):
            return {sub: dict(state.get(sub, {})) for sub in (subs or [])}

        def reader(_p):
            return ProgressReport(), CompletionState(status="completed")

        gate = m.CompletionGate(
            tmp, silence_seconds=0.0, poll_interval=2.0,
            timeout_seconds=6.0,
            clock=m.FakeClock(1000.0),
            scan=scan, list_procs=lambda: [],
            sleep=lambda s: None, logout_reader=reader)
        gate.clock.advance(20.0)
        r = gate.wait_cfd(7, send_wall=999.0)
        self.assertFalse(r["ok"], "S3: 上轮残留 logout 不应放行")
        self.assertEqual(r["status"], "timeout", "未见本轮新鲜 logout 应超时")


class FacadePassthroughTests(unittest.TestCase):
    """§5.3-2：CompletionGate 只投影 watcher 终态，七态原样透传（门层不再自造 waiting/timeout）。"""

    def _gate(self, state, **kw):
        kw.setdefault("poll_interval", 2.0)
        kw.setdefault("sleep", lambda s: None)
        kw.setdefault("list_procs", lambda: [])
        clock = kw.pop("clock", m.FakeClock(2000.0))
        return m.CompletionGate(r"X:\vault", clock=clock,
                                scan=FakeScan(state), **kw)

    def test_cancelled_status_passthrough(self):
        """BIN 基线后出现 calcul_cancel_* → cancelled（投影 ok=False, threeState=failed）。"""
        state = {m.BIN_DIR: {}, m.RESULT_DIR: {}}
        base_scan = FakeScan(state)
        calls = {"n": 0}

        def scan(root, subs):
            calls["n"] += 1
            if calls["n"] >= 2:
                state[m.BIN_DIR]["calcul_cancel_7.txt"] = (10, 2001.0)
            return base_scan(root, subs)

        gate = m.CompletionGate(r"X:\vault", clock=m.FakeClock(2000.0),
                                scan=scan, sleep=lambda s: None,
                                list_procs=lambda: [], timeout_seconds=60.0)
        r = gate.wait_cfd(7, send_wall=2000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(r["threeState"], "failed")
        self.assertIn("calcul_cancel_7.txt", r["diagnostics"]["cancelMarkers"])

    def test_not_started_passthrough_when_vault_quiet(self):
        """发送宽限内无任何新文件 → not_started（不再被压成 timeout）。"""
        gate = self._gate({m.BIN_DIR: {}, m.RESULT_DIR: {}, m.INFO_DIR: {}})
        r = gate.wait_cfd(7, send_wall=2000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "not_started")
        self.assertEqual(r["threeState"], "failed")
        self.assertIn("startup_grace", r["reason"])

    def test_stalled_passthrough_after_signal(self):
        """见过信号后长时间无写入 → stalled（§2.3.3 独立终态）。

        时间线：基线空 → 出现新鲜 logout（信号成立）→ 但结果永远不来 → 静默超 stall_seconds。
        """
        state = {m.BIN_DIR: {}, m.RESULT_DIR: {}}
        base_scan = FakeScan(state)
        calls = {"n": 0}

        def scan(root, subs):
            calls["n"] += 1
            if calls["n"] >= 2:
                state[m.BIN_DIR]["CALCUL_log_out_7.xml"] = (20, 2001.0)
            return base_scan(root, subs)

        gate = m.CompletionGate(r"X:\vault", clock=m.FakeClock(2000.0),
                                silence_seconds=0.0, scan=scan,
                                sleep=lambda s: None, list_procs=lambda: [])
        r = gate.wait_cfd(7, send_wall=2000.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["status"], "stalled")
        self.assertEqual(r["threeState"], "failed")


class MatchFunctionTests(unittest.TestCase):
    def test_logout_digit_boundary(self):
        self.assertTrue(mvn.is_logout_for("CALCUL_log_out_7.xml", 7))
        # cfdId=7 不被 _17 / _12 / _70 吞
        self.assertFalse(mvn.is_logout_for("CALCUL_log_out_17.xml", 7))
        self.assertFalse(mvn.is_logout_for("CALCUL_log_out_12.xml", 7))
        self.assertFalse(mvn.is_logout_for("CALCUL_log_out_70.xml", 7))
        # DUS 变体（分隔符后数字边界）
        self.assertTrue(mvn.is_logout_for("CALCUL_log_out_7_010.xml", 7))
        # cfd_id=None 模式
        self.assertTrue(mvn.is_logout_for("CALCUL_log_out_7.xml", None))
        self.assertFalse(mvn.is_logout_for("not_a_logout.txt", None))

    def test_synthesis_product(self):
        # legacy 2 参模式：返回 (n, ext)，只认 `{site}_synthesis_{n}.{ext}`
        self.assertEqual(mvn.is_synthesis_product("SiteA_synthesis_1.xml", "SiteA"),
                         (1, ".xml"))
        self.assertEqual(mvn.is_synthesis_product("SiteA_synthesis_3.wrp", "SiteA"),
                         (3, ".wrp"))
        self.assertIsNone(mvn.is_synthesis_product("SiteB_synthesis_1.xml", "SiteA"))
        self.assertIsNone(mvn.is_synthesis_product("SiteA_matrix_1.txt", "SiteA"))
        # 3 参生产模式：只认物理 Vault 族（.wri/.wrp/.wrt/.wrtd/.dat/.wrm/.pdr）
        self.assertTrue(mvn.is_synthesis_product("SiteA_synthesis_1.wri", "SiteA", 1))
        self.assertTrue(mvn.is_synthesis_product("SiteA_synthesis_1.wrp", "SiteA", 1))
        self.assertTrue(mvn.is_synthesis_product("SiteA_synthesis_deviation_1.wrtd",
                                                 "SiteA", 1))
        self.assertTrue(mvn.is_synthesis_product("SiteA_timeseriepark_1.dat", "SiteA", 1))
        self.assertTrue(mvn.is_synthesis_product("SiteA_map_1.wrm", "SiteA", 1))
        # 旧口径 xml/txt/matrix 不再算族内（§0.1 S4）
        self.assertFalse(mvn.is_synthesis_product("SiteA_synthesis_1.xml", "SiteA", 1))
        self.assertFalse(mvn.is_synthesis_product("SiteA_matrix_1.txt", "SiteA", 1))
        # 综合号必须精确（matrix_2 不属于综合 1）
        self.assertFalse(mvn.is_synthesis_product("SiteA_matrix_2.txt", "SiteA", 1))
        self.assertFalse(mvn.is_synthesis_product("SiteB_synthesis_1.wri", "SiteA", 1))

    def test_result_prefix(self):
        self.assertTrue(mvn.is_result_for("7_DR_0001.xml", 7))
        self.assertTrue(mvn.is_result_for("7_XYGRID_001.grd", 7))
        self.assertFalse(mvn.is_result_for("12_DR_0001.xml", 7))
        self.assertTrue(mvn.is_result_for("7_DR_0001.xml", None))

    def test_parse_logout_id_boundary(self):
        """cfdId 自动发现的单一解析器：只认本族 + 数字边界。"""
        self.assertEqual(mvn.parse_logout_id("CALCUL_log_out_12.xml"), 12)
        self.assertEqual(mvn.parse_logout_id("calcul_log_out_7_010.xml"), 7)
        self.assertIsNone(mvn.parse_logout_id("CALCUL_log_out.xml"))
        self.assertIsNone(mvn.parse_logout_id("CALCUL_log_out_12abc.xml"))
        self.assertIsNone(mvn.parse_logout_id("STOPJOB_7.xml"))

    def test_is_fresh_logout(self):
        send_wall = 1000.0
        # 正常新鲜文件
        self.assertTrue(mvn.is_fresh_logout("CALCUL_log_out_7.xml", 1005.0, send_wall))
        # mtime 过旧（比 send_wall - grace 早）
        self.assertFalse(mvn.is_fresh_logout("CALCUL_log_out_7.xml", 995.0, send_wall, clock_grace=2.0))
        # 在基线中且 mtime 未推进
        baseline = {"CALCUL_log_out_7.xml": 1005.0}
        self.assertFalse(mvn.is_fresh_logout("CALCUL_log_out_7.xml", 1005.2, send_wall, baseline=baseline))
        # 在基线中但 mtime 已被重写（推进 > 0.5s）
        self.assertTrue(mvn.is_fresh_logout("CALCUL_log_out_7.xml", 1006.0, send_wall, baseline=baseline))

    def test_discover_fresh_cfd_ids(self):
        send_wall = 1000.0
        entries = {
            "CALCUL_log_out_7.xml": 1002.0,
            "CALCUL_log_out_8.xml": 1005.0,
            "CALCUL_log_out_1.xml": 500.0,   # 过旧
            "other_file.txt": 1006.0,        # 非本族
        }
        picked, all_ids = mvn.discover_fresh_cfd_ids(entries, send_wall, expected_count=1)
        self.assertEqual(picked, [8], "应取最新 mtime 的 cfdId")
        self.assertEqual(all_ids, [7, 8], "发现集合应包含全部新鲜 id")

    def test_synthesis_required_optional_products(self):
        # 生产必出 = 物理 Vault 2 件套
        mand = mvn.synthesis_vault_mandatory_products("SiteA", 1)
        self.assertEqual(len(mand), 2)
        self.assertEqual(mand, ("SiteA_synthesis_1.wri", "SiteA_synthesis_1.wrp"))
        self.assertEqual(mvn.VAULT_MANDATORY_EXTENSIONS, (".wri", ".wrp"))

        # legacy 旧口径保留（只服务旧测试/mock vault，生产不再消费）
        req = mvn.synthesis_required_products("SiteA", 1)
        self.assertEqual(len(req), 3)
        self.assertIn("SiteA_synthesis_1.xml", req)
        self.assertIn("SiteA_synthesis_1.txt", req)
        self.assertIn("SiteA_matrix_1.txt", req)

        opt = mvn.synthesis_optional_products("SiteA", 1)
        self.assertIn("SiteA_synthesis_1.wrt", opt)
        self.assertEqual(len([n for n in opt if n.endswith(".wrtd")]), 7)
        self.assertIn("SiteA_timeserie_1.dat", opt)
        self.assertIn("SiteA_timeseriepark_1.dat", opt)
        self.assertIn("SiteA_map_1.wrm", opt)


if __name__ == "__main__":
    unittest.main()
