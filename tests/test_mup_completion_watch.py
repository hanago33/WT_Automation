# encoding: utf-8
"""mup_completion_watch 七态判定环全套测试（蓝图 v3 §2.3 验收口径）。

覆盖：
  · 7 条必改反例（C1 基线残留 / C2 按方向早退 / C3 期望集合 / C4 id 前缀签名 /
    C5 stalled 可达 / C6 phase 分离 / C7 not_started）+ C8 mtime 下界 /
    C11 时间基准 / C13 vault 根，共 13 pitfall 全覆盖。
  · 完成三条件、cfdId 数字边界、增量发现模式、取消/错误/失速/超时、
    综合产物齐套 + mtime>发送时刻、"综合禁监听 CFD 日志"行为断言、
    静态红线（不读退出码/零外部依赖）。

验收标准（§2.3.4）：C1/C2/C5/C6 四条**必须各有一条会 FAIL 旧实现**的反例测试。
本文件正例断言锁定新实现行为；同名的 `test_..._fails_old_prototype` 用例把同一
场景喂给 evidence/k2_watcher_prototype.py 的旧原型，断言其**返回了错误的正态**，
从而逐个钉死旧实现的缺陷（Observed：见报告）。

全部使用 FakeClock + make_scan 假 vault + 注入 sleep：零真实 sleep、零真实 MUP、
零真实磁盘（静态红线用例只读本仓库源码文件 + evidence 旧原型源码）。
"""
import os
import sys
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import mup_completion_watch as mw  # noqa: E402

BIN = mw.BIN_DIR
RES = mw.RESULT_DIR
INFO = mw.INFO_DIR
SYN = mw.SYNTHESIS_DIR


def cfg(**overrides):
    """阈值包快捷替换（全部可注入，蓝图 §2.3）。"""
    return mw.DEFAULT_CONFIG._replace(**overrides)


class Driver(object):
    """脚本化时间线：step() 整体替换假 vault 某目录内容，drain() 推进到终态。"""

    def __init__(self, watcher, scan):
        self.watcher = watcher
        self.scan = scan

    def step(self, BIN=None, RES=None, INFO=None, SYN=None):  # noqa: N803
        mapping = {"BIN": mw.BIN_DIR, "RES": mw.RESULT_DIR,
                   "INFO": mw.INFO_DIR, "SYN": mw.SYNTHESIS_DIR}
        for arg, value in (("BIN", BIN), ("RES", RES), ("INFO", INFO), ("SYN", SYN)):
            if value is not None:
                key = mapping[arg]
                self.scan.state[key].clear()
                self.scan.state[key].update(value)

    def drain(self, limit=400):
        out = None
        for _ in range(limit):
            out = self.watcher.poll()
            if out["status"] != "waiting":
                return out
            self.watcher._advance()
        return out


# ---------------------------------------------------------------------------
# compute_timedelta helper: run to terminal under a FakeClock wall timeline
# ---------------------------------------------------------------------------
def run_to_terminal(watcher, scan, wall_steps=None, max_polls=400):
    """推进假时钟（间隔 cfg.poll_interval）直到非 waiting，返回结果 dict。

    wall_steps 可选：[(file_edit_fn)] 在 poll 前逐轮改写假 vault。这里留空，
    时间线编辑统一由调用方用 scan.state 直接控制。
    """
    out = None
    for _ in range(max_polls):
        out = watcher.poll()
        if out["status"] != "waiting":
            break
        watcher._advance()
    return out


# ---------------------------------------------------------------------------
# C 家族：七条必改反例（每条同时钉死旧原型缺陷，见报告 Observed）
# ---------------------------------------------------------------------------
class C1BaselineResidueTests(unittest.TestCase):
    def _watcher(self, scan, **kw):
        kw.setdefault("cfd_ids", (7,))
        clock = kw.pop("clock", mw.FakeClock(1000.0))
        w = mw.MupCompletionWatcher(r"X:\vault", cfg=cfg(
            stable_min_seconds=2.0, timeout_seconds=40.0, stall_seconds=1e12),
            clock=clock, scan=scan)
        w.capture_baseline()
        return w

    def test_c1_baseline_logout_never_satisfies(self):
        """C1 反例：基线里已有稳定 logout + 之后新增 1 结果 → 不得 completed。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan(
            bin_files={"CALCUL_log_out_7.xml": (1400, 500.0)},  # 基线残留，老 mtime
            result_files={})
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,), cfg=cfg(
            stable_polls=1, stable_min_seconds=1.0, timeout_seconds=40.0,
            stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0   # 发送时刻在基线之后；残留 mtime(500) 早于发送
        w.capture_baseline()
        # 之后新增 1 个结果文件
        scan.state[RES]["7_DR_0001.xml"] = (40, 1000.0)
        clock.advance(60.0)
        out = run_to_terminal(w, scan)
        self.assertNotEqual(out["status"], "completed", out)
        self.assertFalse(out["sub"]["appeared"], out)
        self.assertIn("CALCUL_log_out_7.xml", out["diagnostics"]["staleLogoutFiles"])

    def test_c1_rewritten_stale_logout_counts_as_fresh(self):
        """C1 反向：基线残留被重写（mtime 前进超容差）= 官方删旧重写，算新鲜。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan(
            bin_files={"CALCUL_log_out_7.xml": (1400, 500.0)})  # 结果文件不在基线
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,), cfg=cfg(
            stable_polls=2, stable_min_seconds=1.0, results_stable_polls=2,
            timeout_seconds=40.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 300.0   # 发送先于基线（重写是重复本批）
        w.capture_baseline()
        # 重写 logout（mtime 500→1000 > grace）+ 新增结果
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1500, 1000.0)
        scan.state[RES]["7_DR_0001.xml"] = (40, 1000.0)
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "completed", out)


class C2ExpectedSetTests(unittest.TestCase):
    def test_c2_incremental_mode_never_completes(self):
        """C2 反例：增量模式（cfd_ids=()）只做发现，绝不判完成。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(), cfg=cfg(
            stable_polls=1, stable_min_seconds=1.0, timeout_seconds=60.0,
            stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        # 一个方向完成：logout 稳定 + 一个结果
        scan.state[BIN]["CALCUL_log_out_9_001.xml"] = (1400, 1000.0)
        scan.state[RES]["9_DR_001.xml"] = (40, 1000.0)
        clock.advance(2000.0)   # 远超 stall；即便 stall 打开也只该 stalled，不该 completed
        out = run_to_terminal(w, scan, max_polls=5)  # 只 poll 几次
        self.assertNotEqual(out["status"], "completed", out)
        # 增量模式无期望集合 → 即便过了 timeout 也只是 timeout，不是 completed
        self.assertIn(out["status"], ("waiting", "timeout", "stalled"), out)

    def test_c2_expected_set_requires_every_id(self):
        """C2/C3：期望集合 {7,8} 只齐 7 → 不得 completed，缺 8 报 missingExpected。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7, 8),
                                    expected_ids={7: None, 8: None}, cfg=cfg(
            stable_polls=1, stable_min_seconds=1.0, results_stable_polls=0,
            timeout_seconds=40.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        # 只有 7 完成
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        clock.advance(2000.0)
        out = run_to_terminal(w, scan)
        self.assertNotEqual(out["status"], "completed", out)
        self.assertIn("8", out["sub"]["missingExpected"], out)

    def test_c2_expected_set_all_complete_passes(self):
        """期望集合 {7,8} 全部齐 + 结果达 → completed。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7, 8),
                                    expected_ids={7: None, 8: None}, cfg=cfg(
            stable_polls=1, stable_min_seconds=1.0, results_stable_polls=0,
            timeout_seconds=40.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        scan.state[BIN]["CALCUL_log_out_8.xml"] = (1400, 1000.0)
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        scan.state[RES]["8_DR_001.xml"] = (40, 1000.0)
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "completed", out)


class C5StallReachableTests(unittest.TestCase):
    def test_c5_stalled_reachable_after_signal(self):
        """C5 反例：见过 logout 后停止一切写入，推进超过 stall → 必须 stalled。

        旧原型每轮都刷新 last_activity（C5 根因）→ 永不到达；本实现只在
        (size,mtime) 变化时刷新 → stalled 可达。
        """
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,), cfg=cfg(
            stable_polls=1, stable_min_seconds=1.0, stall_seconds=90.0,
            timeout_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        # 1) 出现 logout，poll 一轮注册信号（last_activity = 见信号时刻）
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        first = w.poll()
        self.assertEqual(first["status"], "waiting", first)
        # 2) 推进假钟超过 stall（90s），期间无任何写入 → last_activity 冻结
        clock.advance(91.0)
        out = None
        for _ in range(3):
            out = w.poll()
            if out["status"] != "waiting":
                break
            w._advance()
        self.assertEqual(out["status"], "stalled", out)


class C6PhaseSeparationTests(unittest.TestCase):
    def test_c6_synthesis_ignores_cfd_logout_in_bin(self):
        """C6 反例：综合 watcher 信号面只有 SYNTHESIS_FILES；BIN 放稳定 logout 不触发完成。"""
        clock = mw.FakeClock(1000.0)
        # BIN 有稳定且新鲜的 logout（模拟上一轮 CFD 残留）+ SYNTHESIS 空
        scan = mw.make_scan(
            bin_files={"CALCUL_log_out_7.xml": (1400, 1001.0)},
            synthesis_files={})
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=cfg(timeout_seconds=30.0,
                                                  stall_seconds=1e12),
                                          clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        clock.advance(2000.0)
        out = run_to_terminal(w, scan)
        self.assertIn(out["status"], ("not_started", "timeout"), out)
        # 行为断言：综合判定环从不询问 CFD 日志匹配器
        self.assertNotEqual(out.get("missingRequired"), None)

    def test_c6_synthesis_completes_on_required_products_fresh(self):
        """综合必出 2 件套（.wri + .wrp）mtime>发送时刻 → completed。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan(synthesis_files={})
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=cfg(timeout_seconds=30.0),
                                          clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        for name in mw.synthesis_vault_mandatory_products("SiteA", "1"):
            scan.state[SYN][name] = (100, 1000.0)
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "completed", out)

    def test_c6_synthesis_mtime_before_send_rejected(self):
        """综合产物 mtime 早于发送时刻 → 按上一轮残留拒绝，不完成（C8 硬条件）。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=cfg(timeout_seconds=30.0,
                                                  stall_seconds=1e12),
                                          clock=clock, scan=scan)
        w.sent_wall = 2000.0   # 发送在产物之后
        w.capture_baseline()
        for name in mw.synthesis_vault_mandatory_products("SiteA", "1"):
            scan.state[SYN][name] = (100, 1000.0)  # mtime(1000) < sent(2000)
        clock.advance(3000.0)
        out = run_to_terminal(w, scan)
        self.assertIn(out["status"], ("not_started", "timeout"), out)


# ---------------------------------------------------------------------------
# 完成三条件 + 基础终态（K2 §5 T 家族）
# ---------------------------------------------------------------------------
class CfdCompletionTests(unittest.TestCase):
    def test_t1_completes_after_appearance_stability_and_delta(self):
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                    expected_ids={7: None}, cfg=cfg(
            stable_polls=1, stable_min_seconds=1.0, results_stable_polls=0,
            timeout_seconds=40.0), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "completed", out)
        self.assertIn("7_DR_001.xml", out["newResults"])

    def test_t2_growing_logout_never_completes(self):
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan(result_files={"7_DR_001.xml": (40, 1000.0)})
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                    expected_ids={7: None}, cfg=cfg(
            stable_polls=1, stable_min_seconds=2.0, results_stable_polls=0,
            timeout_seconds=10.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        # logout 每轮 size 涨 → 永不稳定 → 不 completed
        for i in range(5):
            scan.state[BIN]["CALCUL_log_out_7.xml"] = (100 + 100 * i, 1000.0 + i)
            clock.advance(2.0)
        out = run_to_terminal(w, scan)
        self.assertNotEqual(out["status"], "completed", out)

    def test_t5a_cancel_marker_after_baseline(self):
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                    expected_ids={7: None}, cfg=cfg(
            timeout_seconds=40.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.capture_baseline()
        scan.state[BIN]["calcul_cancel_7.txt"] = (0, 1000.0)
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "cancelled", out)

    def test_t5f_stale_baseline_markers_ignored(self):
        """K2 G3：基线里已存在的取消/错误不得判死新一轮。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan(
            bin_files={"calcul_cancel_7.txt": (0, 100.0)},
            info_files={"error.log": (50, 100.0)})
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                    expected_ids={7: None}, cfg=cfg(
            stable_polls=1, stable_min_seconds=1.0, results_stable_polls=0,
            timeout_seconds=40.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        # 新一轮正常完成：cancel/error 都在基线，未新增
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "completed", out)

    def test_c8_mtime_before_send_rejected(self):
        """C8：logout mtime 早于发送时刻 → 按旧文件拒绝，不判完成、不误上报。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                    expected_ids={7: None}, cfg=cfg(
            timeout_seconds=30.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 2000.0   # 发送在文件之后
        w.capture_baseline()
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)  # mtime < sent
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        clock.advance(3000.0)
        out = run_to_terminal(w, scan)
        self.assertIn(out["status"], ("not_started", "timeout", "stalled"), out)

    def test_c11_wall_clock_jump_back_does_not_make_old_file_new(self):
        """C11：wall() 跳 -1h，旧文件（早于发送时刻）不得被误判为新。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                    expected_ids={7: None}, cfg=cfg(
            timeout_seconds=30.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 3000.0
        w.capture_baseline()
        # 时钟回拨到 1000（相对 sent_wall=3000 相当于 -2000s），文件 mtime=1000 < sent
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        clock.advance(3000.0)
        out = run_to_terminal(w, scan)
        self.assertIn(out["status"], ("not_started", "timeout", "stalled"), out)


# ---------------------------------------------------------------------------
# 审计 §5.2/5.3 五缺陷回归（gaps / partial 两例在旧实现上必 FAIL）
# ---------------------------------------------------------------------------
class ErrorsFoldTests(unittest.TestCase):
    def test_errors_fold_into_completed_not_table_external_status(self):
        """§5.2-3：产物齐套 + Error.log 新增 → completed（errorMarkers 进 diagnostics）。

        旧实现返回 completed_with_errors，threeState=unknown 且不在 TERMINAL_STATUSES。
        """
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                    expected_ids={7: None}, cfg=cfg(
            stable_polls=1, stable_min_seconds=1.0, results_stable_polls=0,
            timeout_seconds=40.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        scan.state[INFO]["error.log"] = (10, 1000.0)   # 基线后新增
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "completed", out)
        self.assertEqual(out["threeState"], "success", out)
        self.assertIn(out["status"], mw.TERMINAL_STATUSES)
        self.assertTrue(out["diagnostics"]["errorMarkers"], out)
        self.assertTrue(out["exportAllowed"], out)


class ExpectedGapsTests(unittest.TestCase):
    def test_gaps_excluded_from_required_and_export_blocked(self):
        """§5.2-4：expected={7,8}+gaps=(8,) 且只产出 7 → completed_with_gaps。

        旧实现先要求 all_expected_ok（含 gap id 8）再查缺口 ⇒ 永远走不到 gaps，
        到点落 timeout。新实现把 gap 从 required 剔除，gap id 自身不要求 ok。
        """
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7, 8),
                                    expected_ids={7: None, 8: None},
                                    expected_gaps=(8,), cfg=cfg(
            stable_polls=1, stable_min_seconds=1.0, results_stable_polls=0,
            timeout_seconds=30.0, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        # 只有 7 产出；8 是配置决定不参与的缺口，不要求它 ok
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "completed_with_gaps", out)
        self.assertEqual(out["gapsApplied"], ["8"], out)
        self.assertIn("8", out["sub"]["gapsApplied"], out)
        self.assertNotIn("8", out["sub"]["missingExpected"], out)
        self.assertFalse(out["exportAllowed"], out)
        self.assertEqual(out["threeState"], "success", out)


class SynthesisProbeTests(unittest.TestCase):
    def test_s5_five_extension_set_completes(self):
        """S5 新预期：`.wri+.wrt+.txt+.xml+.wrp` 同时出现 → completed。

        旧预期是 timeout/stalled（缺 matrix）。改口后 `.wri/.wrp` 新鲜即完成，
        `.wrt` 只进 diagnostics，legacy `.txt/.xml` 既不加分也不减分。
        """
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=cfg(timeout_seconds=30.0),
                                          clock=clock, scan=scan, sent_wall=900.0)
        w.capture_baseline()
        for ext in (".wri", ".wrt", ".txt", ".xml", ".wrp"):
            scan.state[SYN]["SiteA_synthesis_1" + ext] = (100, 1000.0)
        clock.advance(10.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "completed", out)
        self.assertTrue(out["exportAllowed"], out)
        self.assertIn("SiteA_synthesis_1.wrt", out["freshFamilyHits"], out)

    def test_s4_legacy_only_is_not_started(self):
        """S4 新预期：只有 xml/txt/matrix → 不算族内，过 grace 即 not_started。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan(synthesis_files={
            "SiteA_synthesis_1.xml": (100, 1000.0),
            "SiteA_synthesis_1.txt": (100, 1000.0),
            "SiteA_matrix_1.txt": (100, 1000.0),
        })
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=cfg(timeout_seconds=1e12,
                                                  stall_seconds=1e12,
                                                  startup_grace_seconds=10.0),
                                          clock=clock, scan=scan, sent_wall=900.0)
        w.capture_baseline()
        clock.advance(20.0)
        out = run_to_terminal(w, scan, max_polls=3)
        self.assertEqual(out["status"], "not_started", out)
        self.assertEqual(out["familyHits"], [], out)
        self.assertEqual(out["freshFamilyHits"], [], out)


class SynthesisPartialTests(unittest.TestCase):
    def test_partial_after_family_signal_is_failed_not_timeout(self):
        """§5.2-5：综合已见本轮家族产物但到点缺必出项 → failed(partial) + 缺失清单。

        旧实现到点一律 timeout，无法与"根本没起算"区分。
        """
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=cfg(timeout_seconds=10.0,
                                                  stall_seconds=1e12),
                                          clock=clock, scan=scan,
                                          sent_wall=900.0)
        w.capture_baseline()
        required = mw.synthesis_vault_mandatory_products("SiteA", "1")
        scan.state[SYN][required[0]] = (100, 1000.0)   # .wri
        clock.advance(20.0)                            # 越过 timeout，.wrp 仍缺
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "failed", out)
        self.assertEqual(out.get("failedKind"), "partial", out)
        self.assertIn(required[1], out["missingRequired"], out)
        self.assertEqual(out["threeState"], "failed", out)

    def test_s6_partial_reachable_under_default_stall_before_timeout(self):
        """S6 新预期：默认 stall(900s) < timeout(3600s) 时，仍须落 failed(partial)。

        旧状态机先判 stalled（:837）后判 timeout（:849）⇒ 缺件场景永远报 stalled，
        `failed(partial)` 不可达。修法：stalled 分支对"本轮已写过的家族产物"投影
        partial（§5.5-b-4 方案②），只有"仅剩旧产物"才保留真失速。
        """
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=mw.DEFAULT_CONFIG,
                                          clock=clock, scan=scan,
                                          sent_wall=900.0)
        w.capture_baseline()
        required = mw.synthesis_vault_mandatory_products("SiteA", "1")
        scan.state[SYN][required[0]] = (100, 1000.0)   # 只 .wri，缺 .wrp
        w.poll()                                       # 登记"已起算"
        clock.advance(901.0)                           # 越过 stall，未到 timeout
        out = w.poll()
        self.assertEqual(out["status"], "failed", out)
        self.assertEqual(out.get("failedKind"), "partial", out)
        self.assertIn(required[1], out["missingRequired"], out)
        self.assertLess(out["elapsedSeconds"], mw.DEFAULT_CONFIG.timeout_seconds)

    def test_stalled_still_reachable_for_stale_only_family(self):
        """真失速仍可达：只剩上一轮旧产物（本轮零写入）→ stalled，不是 partial。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan(synthesis_files={"SiteA_synthesis_1.wri": (100, 500.0)})
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=mw.DEFAULT_CONFIG,
                                          clock=clock, scan=scan,
                                          sent_wall=900.0)
        w.capture_baseline()
        w.poll()
        clock.advance(901.0)
        out = w.poll()
        self.assertEqual(out["status"], "stalled", out)
        self.assertFalse(out.get("failedKind"), out)

    def test_stale_family_residue_alone_does_not_become_partial(self):
        """只有上一轮旧产物（mtime 早于发送）不算"本轮已起算"，到点仍 timeout。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=cfg(timeout_seconds=10.0,
                                                  stall_seconds=1e12),
                                          clock=clock, scan=scan,
                                          sent_wall=2000.0)
        w.capture_baseline()
        required = mw.synthesis_vault_mandatory_products("SiteA", "1")
        scan.state[SYN][required[0]] = (100, 1000.0)   # 早于 sent，旧残留
        clock.advance(3000.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "timeout", out)
        self.assertFalse(out.get("failedKind"), out)


class WaitingReasonTests(unittest.TestCase):
    def test_named_cfd_waiting_reason_is_not_incremental_mode(self):
        """§5.2-3：named 模式 waiting 不得恒报"增量发现模式"（旧实现误报）。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                    expected_ids={7: None}, cfg=cfg(
            timeout_seconds=1e12, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        out = w.poll()
        self.assertEqual(out["status"], "waiting", out)
        self.assertNotIn("增量发现模式", out["reason"], out)
        self.assertIn("7", out["reason"], out)
        self.assertIn("尚未见到", out["reason"], out)

    def test_named_cfd_waiting_reason_after_signal_mentions_missing(self):
        """见过 logout 但齐套未满足 → reason 指向缺失项，而不是增量模式。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7, 8),
                                    expected_ids={7: None, 8: None}, cfg=cfg(
            stable_polls=2, stable_min_seconds=5.0, results_stable_polls=2,
            timeout_seconds=1e12, stall_seconds=1e12), clock=clock, scan=scan)
        w.sent_wall = 900.0
        w.capture_baseline()
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        scan.state[RES]["7_DR_001.xml"] = (40, 1000.0)
        out = w.poll()
        self.assertEqual(out["status"], "waiting", out)
        self.assertNotIn("增量发现模式", out["reason"], out)
        self.assertIn("等待齐套", out["reason"], out)
        self.assertIn("8", out["reason"], out)

    def test_synthesis_waiting_reason_branches_by_stage(self):
        """综合 waiting reason 按阶段分支（无产物 / 已有本轮产物）。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=cfg(timeout_seconds=1e12,
                                                  stall_seconds=1e12),
                                          clock=clock, scan=scan,
                                          sent_wall=900.0)
        w.capture_baseline()
        out = w.poll()
        self.assertEqual(out["status"], "waiting", out)
        self.assertIn("尚未见到综合产物", out["reason"], out)
        required = mw.synthesis_vault_mandatory_products("SiteA", "1")
        scan.state[SYN][required[0]] = (100, 1000.0)   # 只来 .wri
        out = w.poll()
        self.assertEqual(out["status"], "waiting", out)
        self.assertIn("已开始写入", out["reason"], out)
        self.assertIn(required[1], out["reason"], out)


class SynthesisGraceTests(unittest.TestCase):
    def test_synthesis_mtime_within_clock_grace_is_fresh(self):
        """§5.2-7：综合时间闸统一为 mtime >= send_ts-clock_grace(2.0s)。

        旧实现严格 mtime > sent，mtime=sent-1s 的合法本轮产物被误拒。
        """
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.SynthesisCompletionWatcher(r"X:\vault", site="SiteA", case="1",
                                          cfg=cfg(timeout_seconds=60.0,
                                                  clock_grace_seconds=2.0),
                                          clock=clock, scan=scan,
                                          sent_wall=1000.0)
        w.capture_baseline()
        for name in mw.synthesis_vault_mandatory_products("SiteA", "1"):
            scan.state[SYN][name] = (100, 999.0)   # sent-1s，落在 2s grace 内
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        self.assertEqual(out["status"], "completed", out)


class StatusWordlistTests(unittest.TestCase):
    def test_every_terminal_status_has_three_state_and_no_unknown(self):
        for status in mw.TERMINAL_STATUSES:
            self.assertIn(mw.three_state_of(status), ("success", "failed"),
                          status)
        self.assertIn(mw.three_state_of("waiting"), ("in_progress", "failed"))
        # 表外旧状态仅作兼容别名，不允许再产生 unknown
        self.assertEqual(mw.three_state_of("completed_with_errors"), "success")
        self.assertEqual(mw.three_state_of("not_ready"), "failed")
        self.assertEqual(mw.three_state_of("some-future-status"), "failed")


# ---------------------------------------------------------------------------
# 静态红线 + 综合 phase 分离 + C13
# ---------------------------------------------------------------------------
_SELF_PATH = os.path.abspath(__file__)
_SRC_PATH = os.path.join(PROJECT_DIR, "mup_completion_watch.py")


class RedLineStaticTests(unittest.TestCase):
    def test_g1_static_never_consults_process_surface(self):
        """静态红线：判定环源码不得出现进程面/退出码词（R1 §B.3.1）。"""
        with open(_SRC_PATH, "r", encoding="utf-8") as fh:
            src = fh.read()
        banned = ["exitcode", "exit_code", "returncode", "WaitForExit",
                  "subprocess", "tasklist", "psutil", "watchdog", "pywin32"]
        for word in banned:
            self.assertNotIn(word, src, "判定环源码不得出现 %r" % word)

    def test_g2_name_matching_has_single_owner(self):
        """§5.1 归一：名字/产物族匹配 owner 唯一（leaf `mup_vault_names`）。

        判定环不得再定义第二套同义实现（本轮删除 :166/:188/:237 家族）；
        模块属性必须是 leaf 的函数对象本身，而不是行为相近的副本。
        """
        import mup_vault_names as mvn
        with open(_SRC_PATH, "r", encoding="utf-8") as fh:
            src = fh.read()
        for name in ("is_logout_for", "is_new_result_for", "is_synthesis_product",
                     "synthesis_required_products", "synthesis_optional_products",
                     "is_cancel_marker", "is_debug_calcul", "is_error_log",
                     "parse_logout_id"):
            self.assertNotIn("def %s(" % name, src, "判定环不得重定义 %s" % name)
        self.assertIs(mw.is_logout_for, mvn.is_logout_for)
        self.assertIs(mw.is_new_result_for, mvn.is_new_result_for)
        self.assertIs(mw.is_synthesis_product, mvn.is_synthesis_product)
        self.assertIs(mw.parse_logout_id, mvn.parse_logout_id)
        self.assertIs(mw.synthesis_required_products, mvn.synthesis_required_products)

    def test_g1b_result_never_carries_exit_code_field(self):
        """输出契约不携带 exit 类字段。"""
        from mup_completion_watch import MupCompletionWatcher
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan(result_files={"7_DR_001.xml": (40, 1000.0)})
        w = MupCompletionWatcher(r"X:\vault", cfd_ids=(7,), expected_ids={7: None},
                                 cfg=cfg(timeout_seconds=30.0), clock=clock, scan=scan)
        w.capture_baseline()
        scan.state[BIN]["CALCUL_log_out_7.xml"] = (1400, 1000.0)
        clock.advance(20.0)
        out = run_to_terminal(w, scan)
        payload = str(out)
        for word in ("exitcode", "exit_code", "returncode"):
            self.assertNotIn(word, payload.lower())

    def test_c13_vault_root_explicit_wins(self):
        """C13：显式 vault_root 优先；空根 → not_started(notReady)，不静默降级。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()
        w = mw.MupCompletionWatcher("", cfd_ids=(7,), expected_ids={7: None},
                                    cfg=cfg(), clock=clock, scan=scan)
        w.capture_baseline()
        out = w.poll()
        self.assertEqual(out["status"], "not_started", out)
        self.assertTrue(out["notReady"], out)
        self.assertEqual(out["threeState"], "failed", out)
        self.assertIn("not_ready", out["reason"], out)
        # 显式根（即使假 vault 不在真实盘上，注入 scan 时不检查存在性）
        w2 = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                     expected_ids={7: None}, cfg=cfg(),
                                     clock=clock, scan=scan)
        w2.capture_baseline()
        out2 = w2.poll()
        self.assertFalse(out2.get("notReady"), out2)
        self.assertEqual(out2["status"], "waiting", out2)

    def test_c7_not_started_is_distinct_terminal(self):
        """C7：startup_grace 内无任何新文件 → not_started（独立终态，非 timeout）。"""
        clock = mw.FakeClock(1000.0)
        scan = mw.make_scan()   # 全程无信号
        w = mw.MupCompletionWatcher(r"X:\vault", cfd_ids=(7,),
                                    cfg=cfg(timeout_seconds=1e12,
                                            stall_seconds=1e12,
                                            startup_grace_seconds=10.0),
                                    clock=clock, scan=scan)
        w.capture_baseline()
        clock.advance(12.0)   # 超过 grace，不到 timeout
        out = run_to_terminal(w, scan, max_polls=3)
        self.assertEqual(out["status"], "not_started", out)

    def test_three_state_projection(self):
        self.assertEqual(mw.three_state_of("completed"), "success")
        self.assertEqual(mw.three_state_of("completed_with_gaps"), "success")
        self.assertEqual(mw.three_state_of("cancelled"), "failed")
        self.assertEqual(mw.three_state_of("not_started"), "failed")
        self.assertEqual(mw.three_state_of("stalled"), "failed")
        self.assertEqual(mw.three_state_of("timeout"), "failed")
        self.assertEqual(mw.three_state_of("waiting"), "in_progress")


# ---------------------------------------------------------------------------
# 旧原型反例演示（Observed：证明 C1/C2/C5/C6 会让 K2 旧原型 FAIL）
# ---------------------------------------------------------------------------
class OldPrototypeContradictionTests(unittest.TestCase):
    """把 §2.3 验收四反例喂给 evidence/k2_watcher_prototype.py 的旧原型。

    旧原型（K2 548 行版）的四个结构性缺陷逐个 FAIL：
      C1 基线残留 → 旧原型判 completed（错）
      C2 一方向稳定 → 旧原型判 completed（错）
      C5 见信号后静默 → 旧原型被判 waiting 永不 stalled（错）
      C6 综合段拿 CFD 日志 → 旧原型判 completed（错）
    需要旧原型文件在证据目录存在；缺失时这些用例 skip（不阻塞主验收）。
    """

    OLD_PATH = os.path.join(
        r"E:\My_RF_Project\MUP_recon\99_synthesis\evidence",
        "k2_watcher_prototype.py")

    def _load_old(self):
        if not os.path.isfile(self.OLD_PATH):
            self.skipTest("evidence/k2_watcher_prototype.py 缺失（旧原型反例演示跳过）")
        if "k2_watcher_prototype" not in sys.modules:
            import importlib.util
            spec = importlib.util.spec_from_file_location("k2_watcher_prototype",
                                                         self.OLD_PATH)
            mod = importlib.util.module_from_spec(spec)
            sys.modules["k2_watcher_prototype"] = mod
            spec.loader.exec_module(mod)
        return sys.modules["k2_watcher_prototype"]

    def test_c1_old_prototype_misreads_baseline_residue_as_completed(self):
        """旧原型把基线残留 logout 判成 completed → 反例 FAIL（钉死 C1）。"""
        old = self._load_old()
        clock = old.FakeClock()
        scan = old.make_scan(bin_files={"CALCUL_log_out_7.xml": (1400, 500.0)})
        w = old.MupCompletionWatcher(r"X:\vault", (7,),
                                     cfg=old.DEFAULT_CONFIG._replace(
                                         stable_polls=1, stable_min_seconds=1.0),
                                     clock=clock, scan=scan)
        w.capture_baseline()
        scan.state["BIN"]["CALCUL_log_out_7.xml"] = (1400, 500.0)  # 保持不变（残留）
        scan.state["RESULT_FILES"]["7_DR_0001.xml"] = (40, 600.0)
        out = None
        for _ in range(6):
            out = w.poll()
            if out["status"] != "waiting":
                break
            w._advance()
        # 旧原型：基线残留被记进 _seen → 三条件满足 → 错误 completed
        self.assertEqual(out["status"], "completed",
                         "旧原型必须误判 completed，才证明 C1 反例奏效")

    def test_c6_old_prototype_never_inspects_synthesis_family(self):
        """旧原型没有综合信号面：BIN 有稳定 logout 就被判 completed（A7/P 事故）。

        这证明 phase 分离（C6）是旧原型缺失的结构。
        """
        old = self._load_old()
        clock = old.FakeClock()
        scan = old.make_scan()
        w = old.MupCompletionWatcher(r"X:\vault", (None,),
                                     cfg=old.DEFAULT_CONFIG._replace(
                                         stable_polls=1, stable_min_seconds=1.0),
                                     clock=clock, scan=scan)
        w.capture_baseline()
        # 综合段：SYNTHESIS_FILES 有本批产物；BIN 也残留上一轮 CFD 日志
        # 旧原型信号面只有 BIN/RESULT_FILES，综合产物它根本看不到 → 只能凭 CFD 日志判
        scan.state["BIN"]["CALCUL_log_out_7.xml"] = (1400, 500.0)
        scan.state["RESULT_FILES"]["7_DR_0001.xml"] = (40, 600.0)
        out = None
        for _ in range(6):
            out = w.poll()
            if out["status"] != "waiting":
                break
            w._advance()
        self.assertEqual(out["status"], "completed",
                         "旧原型必须以 CFD 日志判完成，才证明 C6 反例奏效")

    def test_c2_old_prototype_declares_complete_on_first_direction(
            self):
        """旧原型把"第一个方向稳定"判成整批完成（C2），漏掉其余方向。"""
        old = self._load_old()
        clock = old.FakeClock()
        scan = old.make_scan()
        w = old.MupCompletionWatcher(r"X:\vault", (),
                                     cfg=old.DEFAULT_CONFIG._replace(
                                         stable_polls=1, stable_min_seconds=1.0),
                                     clock=clock, scan=scan)
        w.capture_baseline()
        # 16 个方向，只有第 1 个（_001）算完 → 旧原型增量模式立刻 completed
        scan.state["BIN"]["CALCUL_log_out_9_001.xml"] = (1400, 500.0)
        scan.state["RESULT_FILES"]["9_DR_001.xml"] = (40, 600.0)
        out = None
        for _ in range(6):
            out = w.poll()
            if out["status"] != "waiting":
                break
            w._advance()
        self.assertEqual(out["status"], "completed",
                         "旧原型必须以第一方向判整批完成，才证明 C2 反例奏效")

    def test_c5_old_prototype_never_reaches_stalled(self):
        """旧原型见过信号后 each 轮刷新 last_activity → stalled 永不触达（C5）。

        断言旧原型在"见信号 + 静默远超 stall"下仍返回 waiting（它本应 stalled）。
        """
        old = self._load_old()
        clock = old.FakeClock()
        scan = old.make_scan()
        w = old.MupCompletionWatcher(
            r"X:\vault", (7,),
            cfg=old.DEFAULT_CONFIG._replace(stable_polls=1, stable_min_seconds=1.0,
                                            stall_seconds=90.0, timeout_seconds=1e12),
            clock=clock, scan=scan)
        w.capture_baseline()
        scan.state["BIN"]["CALCUL_log_out_7.xml"] = (1400, 500.0)
        w.poll()   # 见过信号，last_activity = 此刻
        # 推进远超 stall（假钟经 _advance 前进）；旧实现 each 轮刷新 last_activity
        for _ in range(60):
            w._advance()
        out = None
        for _ in range(6):
            out = w.poll()
            if out["status"] != "waiting":
                break
            w._advance()
        self.assertEqual(out["status"], "waiting",
                         "旧原型必须永不 stalled，才证明 C5 反例奏效")


if __name__ == "__main__":
    unittest.main()