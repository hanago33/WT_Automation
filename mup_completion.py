# encoding: utf-8
"""MUP 计算完成门 —— **接线适配壳**（判据唯一实现者 = `mup_completion_watch`）。

2026-11-02 完成判定归一（审计 `2026-11-02-completion-judge-unify.md` §5.1/§5.2/§5.3）：

1. **判据归 watcher**（§5.3-2）：`wait_cfd` / `wait_comprehensive` 不再自带 oracle，
   一律委托 `MupCompletionWatcher` / `SynthesisCompletionWatcher`；七态终态
   （cancelled / completed / completed_with_gaps / failed / not_started / stalled /
   timeout / waiting）原样透传，本类只投影 `ok = (status == "completed")`。
   `completed_with_gaps` 属"算出但有缺口"：`ok=False` + `exportAllowed=False`
   （§5.2-4 默认禁止放行导出），终态本身照原样透传给调用方日志。
2. **止血条款随委托生效**（§5.2-1、§5.2-2）：
   - CFD 无 DASSolver 进程硬条件（进程面只进 `diagnostics.solverSeen`）；
   - CFD 无 `status` 五词硬条件（status 只进 `diagnostics.logoutStatus`，U-1 金样前不作门条件）；
   - 综合必出集合 = **物理 Vault 的 2 件套** `{CaseGUID}_synthesis_{case}.wri/.wrp`
     （2026-11-02 §0.1 改口：真实 DEMO 17 个文件里 `.xml/.txt/matrix.txt` 零命中），
     `.wrt/.wrtd/timeserie*.dat/{elem}.wrm` 只进 watcher diagnostics。
3. **名字匹配归 leaf**（§5.1）：文件名/产物家族判定与新鲜度全部来自 `mup_vault_names`，
   本模块保留 re-export 以兼容既有 import（如 `mup_vault_export` 的 `is_result_for`）。
4. **身份解析归 leaf**（§5.5-b-5）：`site` → `case_prefix`（Cases.Id）走
   `mup_case_identity`，纯 stdlib、不 spawn 子进程，避免与 `mup_vault_export` 成环。
5. **fail-closed 骨架保留**：logout XML 真·解析异常 → `status="fail-closed"`；
   vault 不可定位 → watcher 的 `not_started`（`notReady=True`）。

相对旧门的**行为差异**（已由新测试钉住）：
- 综合不再叠加"齐套后静默 N 秒"闸：齐套即终态（§2.3.2 只以家族 + mtime 判据）；
  `silence_seconds` 现在只作用于 CFD（映射为 watcher 的 `stable_min_seconds`）。
- 综合到点仍缺必出项但已见本轮家族产物 → `failed` + `failedKind="partial"`
  （§5.2-5），不再一律报 `timeout`。
- `require_all_exts` 参数随 §5.2-2 必出集合定稿移除（"至少一个产物"不再视为完成）。
"""
import os
import time

from mup_calcul_logout_parser import parse_calcul_logout_file as _parse_logout_file
from mup_case_identity import resolve_case_identity
from mup_completion_watch import (
    DEFAULT_CONFIG as _WATCH_CONFIG,
    MupCompletionWatcher,
    SynthesisCompletionWatcher,
    WAITING,
    three_state_of,
)
from mup_vault_names import (
    BIN_DIR,
    RESULT_DIR,
    SYNTHESIS_DIR,
    INFO_DIR,
    DEFAULT_SILENCE_SECONDS,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_CLOCK_GRACE,
    DEFAULT_STABLE_POLLS,
    parse_synthesis_filename,
    parse_logout_id,  # 兼容 re-export：接线测试的替身模块从这里取
    is_logout_for,
    is_result_for,   # 兼容 re-export：mup_vault_export 仍从这里取
    SYNTH_EXTENSIONS,  # 兼容 re-export：测试/探针用于构造五扩展名族
)

# ---------------------------------------------------------------------------
# 常量（保留向后兼容）
# ---------------------------------------------------------------------------
SOLVER_IMAGE_PREFIX = "DASSolver_"
SOLVER_IMAGE_SUFFIX = ".exe"

# CFD logout `status` 词表（已降为 diagnostics，保留常量供外部引用）
CFD_COMPLETE_STATUSES = ("completed", "finished", "success", "done", "ok")


# ---------------------------------------------------------------------------
# 观测注入点（clock/scan/list_procs/sleep 全部可注入，测试零真实进程/磁盘）
# ---------------------------------------------------------------------------
class _RealClock(object):
    def monotonic(self):
        return time.monotonic()

    def wall(self):
        return time.time()


class FakeClock(object):
    """脚本化时钟：只有 advance() 被调用时间才前进（测试注入）。"""

    def __init__(self, start=1000.0):
        self.now = float(start)

    def monotonic(self):
        return self.now

    def wall(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def scan_vault(vault_root, sub_dirs):
    """窄扫描 {sub_dir: {name: (size, mtime)}}；目录缺失/读取失败 → 空 dict（不抛）。"""
    out = {}
    for sub in sub_dirs or ():
        entries = {}
        base = os.path.join(vault_root, sub) if vault_root else ""
        if base and os.path.isdir(base):
            try:
                with os.scandir(base) as it:
                    for e in it:
                        try:
                            st = e.stat(follow_symlinks=False)
                            entries[e.name] = (st.st_size, st.st_mtime)
                        except OSError:
                            continue
            except OSError:
                pass
        out[sub] = entries
    return out


def _tasklist_solver_images():
    """真实进程枚举：返回当前存在的 `DASSolver_*.exe` 名列表。纯 stdlib。

    仅作 diagnostics.solverSeen 观察之用，不作任何门禁条件（§5.2-1 禁进程面）。
    """
    try:
        import subprocess
        raw = subprocess.run(
            ["tasklist", "/NH", "/FO", "CSV"],
            capture_output=True, text=True, timeout=15.0,
            check=False).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return None
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line[0] != '"':
            continue
        image = line.split('",')[0].strip('"').strip().lower()
        if _is_solver(image):
            out.append(image)
    return out


def _is_solver(image):
    """一个 Image Name 是否命中 DASSolver 进程（大小写不敏感）。"""
    image = (image or "").lower()
    return (image.startswith(SOLVER_IMAGE_PREFIX.lower())
            and image.endswith(SOLVER_IMAGE_SUFFIX.lower()))


# ---------------------------------------------------------------------------
# 完成门（适配壳：watcher 判据 → (ok, status, reason)）
# ---------------------------------------------------------------------------
class CompletionGate(object):
    """保守完成门：**只投影，不判据**。

    - 判据：`mup_completion_watch`（唯一实现者，七态 + 三态投影）；
    - 本类职责：legacy 参数 → `WatchConfig` 映射；综合站点/综合号发现；
      七态 → `ok` 投影；CFD 诊断面（logout status / solverSeen）记录；fail-closed 骨架。
    - 非线程安全，每次等待单独建；所有观测可注入（测试零真实 MUP/进程/盘写）。
    """

    # 综合多号聚合时的"最坏终态"优先级（§2.3.3：越靠前越需要调用方中止/干预）。
    _WORST_ORDER = ("cancelled", "failed", "not_started", "stalled", "timeout",
                    "completed_with_gaps", "completed")

    def __init__(self, vault_root, silence_seconds=DEFAULT_SILENCE_SECONDS,
                 poll_interval=DEFAULT_POLL_INTERVAL,
                 timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                 clock_grace=DEFAULT_CLOCK_GRACE,
                 stable_polls=DEFAULT_STABLE_POLLS,
                 clock=None, scan=None, list_procs=None, sleep=None,
                 logout_reader=None, bin_baseline=None):
        self.vault_root = vault_root or ""
        self.silence_seconds = float(silence_seconds)
        self.poll_interval = float(poll_interval)
        self.timeout_seconds = float(timeout_seconds)
        self.clock_grace = float(clock_grace)
        self.stable_polls = int(stable_polls)
        self.clock = clock if clock is not None else _RealClock()
        self.scan = scan if scan is not None else (
            lambda root, subs: scan_vault(root, subs))
        self.list_procs = list_procs if list_procs is not None else _tasklist_solver_images
        self.sleep = sleep if sleep is not None else time.sleep
        # logout 状态读取注入点：默认用纯 stdlib 解析器读真实文件，测试可注入假 reader。
        self.logout_reader = (logout_reader if logout_reader is not None
                              else _parse_logout_file)
        self.bin_baseline = dict(bin_baseline) if bin_baseline is not None else None
        # legacy 构造参数 → watcher 阈值包（silence_seconds 即"条件满足后的静默下界"）。
        self._cfg = _WATCH_CONFIG._replace(
            poll_interval=self.poll_interval,
            timeout_seconds=self.timeout_seconds,
            stable_polls=self.stable_polls,
            stable_min_seconds=self.silence_seconds,
            clock_grace_seconds=self.clock_grace,
        )

    # -- 基础设施 ------------------------------------------------------
    def _mono(self):
        return float(self.clock.monotonic())

    def _wall(self):
        return float(self.clock.wall())

    def _scan_dirs(self, dirs):
        """把 legacy 双参 scan(root, subs) 适配成 watcher 的单参 scan(root)。"""
        return lambda root: self.scan(root, dirs)

    def _tick(self, poll_interval):
        """假时钟推进协议：真时钟无 advance（自然流逝）。"""
        advance = getattr(self.clock, "advance", None)
        if callable(advance):
            advance(poll_interval)

    def _sample_solver(self):
        """诊断面：当前是否有 DASSolver 进程（永不作门条件，§5.2-1）。"""
        if not callable(self.list_procs):
            return False
        try:
            procs = self.list_procs()
        except Exception:  # noqa: BLE001 —— 诊断面失败不得影响判定
            return False
        return any(_is_solver(p) for p in (procs or ()))

    def _logout_status(self, cfd_id, frame):
        """从 watcher 帧定位目标 logout 文件并解析 status（仅 diagnostics）。"""
        name = None
        logout_files = frame.get("logoutFiles") or []
        if logout_files:
            name = logout_files[0].get("name")
        if name is None:
            # 未新鲜（基线残留/未重写）：仍读一次，保住"坏 XML → fail-closed"骨架。
            stale = [n for n in (frame.get("diagnostics") or {}).get("staleLogoutFiles") or ()
                     if is_logout_for(n, cfd_id)]
            name = stale[0] if stale else None
        if name is None:
            return None, None, None
        path = os.path.join(self.vault_root, BIN_DIR, name)
        try:
            _progress, completion = self.logout_reader(path)
        except FileNotFoundError:
            # 官方"删旧看文件"（R1 §B.3.1）：扫描到读取之间被删不是解析异常。
            return name, None, None
        except Exception as exc:  # noqa: BLE001 —— 保守门：解析异常 fail-closed
            return name, None, exc
        status = str(getattr(completion, "status", "") or "").strip().lower()
        return name, (status or None), None

    # -- CFD -----------------------------------------------------------
    def wait_cfd(self, cfd_id, send_wall=None, timeout_seconds=None,
                 min_new_results=1, result_sig_stable=2, bin_baseline=None):
        """CFD 完成门：委托 `MupCompletionWatcher`（logout 新鲜稳定 + 目标前缀结果增量齐套）。

        判据（§5.2-1）：logout 精确名新鲜（基线增量 + `mtime >= send-grace`）+
        `(size,mtime)` 稳定 N 轮/静默 M 秒 + `{cfdId}_*` 结果达期望 + 无 cancel 标记；
        **不含**进程面、**不含** `status` 词表（二者只进 diagnostics）。
        """
        cfd_id = int(cfd_id)
        sent = self._wall() if send_wall is None else float(send_wall)
        cfg = self._cfg._replace(
            timeout_seconds=(float(timeout_seconds) if timeout_seconds is not None
                             else self.timeout_seconds),
            min_new_results=int(min_new_results),
            results_stable_polls=int(result_sig_stable),
        )
        watcher = MupCompletionWatcher(
            self.vault_root, cfd_ids=(cfd_id,), expected_ids={cfd_id: None},
            cfg=cfg, clock=self.clock,
            scan=self._scan_dirs((BIN_DIR, RESULT_DIR, INFO_DIR)),
            sleep=self.sleep, sent_wall=sent)
        watcher.capture_baseline()
        # 调用方发送前快照（Launcher 的 bin_baseline）优先：比 wait 起点快照更接近"发送前"。
        baseline_bin = bin_baseline if bin_baseline is not None else self.bin_baseline
        if baseline_bin:
            watcher.baseline[BIN_DIR] = dict(baseline_bin)

        solver_seen = self._sample_solver()
        logout_name = None
        logout_status = None
        parse_error = None
        last_logout_name = None
        while True:
            frame = watcher.poll()
            name, status_now, error_now = self._logout_status(cfd_id, frame)
            if name is not None and name != last_logout_name:
                last_logout_name = name
                logout_name, logout_status, parse_error = name, status_now, error_now
            if parse_error is not None or frame.get("status") != WAITING:
                break
            self.sleep(cfg.poll_interval)
            self._tick(cfg.poll_interval)
        solver_seen = self._sample_solver() or solver_seen

        status = frame.get("status") or "timeout"
        reason = frame.get("reason") or ""
        if parse_error is not None:
            status = "fail-closed"
            reason = "logout 解析异常: %r（判据终态=%s：%s）" % (
                parse_error, frame.get("status"), frame.get("reason"))

        diagnostics = frame.get("diagnostics") or {}
        return self._frame(
            "cfd", ok=(status == "completed"), status=status, reason=reason,
            cfdId=cfd_id, logoutName=logout_name, logoutStatus=logout_status,
            solverSeen=bool(solver_seen),
            newResults=list(frame.get("newResults") or ()),
            threeState=frame.get("threeState"),
            expectedIds=dict(frame.get("expectedIds") or {}),
            exportAllowed=bool(frame.get("exportAllowed")),
            elapsedSeconds=frame.get("elapsedSeconds"),
            pollCount=frame.get("pollCount"),
            diagnostics={
                "solverSeen": bool(solver_seen),
                "logoutStatus": logout_status,
                "logoutName": logout_name,
                "watcherStatus": frame.get("status"),
                "errorMarkers": list(diagnostics.get("errorMarkers") or ()),
                "cancelMarkers": list(diagnostics.get("cancelMarkers") or ()),
                "staleLogoutFiles": list(diagnostics.get("staleLogoutFiles") or ()),
            })

    # -- 综合 ----------------------------------------------------------
    def wait_comprehensive(self, site, send_wall=None, timeout_seconds=None,
                           expected_ns=None, case_prefix=None, site_label=None):
        """综合完成门：委托 `SynthesisCompletionWatcher`（每个综合号一个判定环）。

        判据（2026-11-02 §0.1 改口）：只扫 `SYNTHESIS_FILES`；必出 =
        `{CaseGUID}_synthesis_{case}.wri/.wrp`；`mtime >= send_wall - clock_grace` 硬闸。
        `expected_ns` 给出多个综合号时逐个判定并按最坏终态聚合；未给时对"已发现的新鲜综合号"
        逐个追加判定环（后续出现的更大综合号也会追加，不会提前放行）。

        身份（§5.5-b-5/7）：
          - `case_prefix` 已由接线层在**发送前**解析并落账时直接采信（推荐路径）；
          - 否则在此用 `mup_case_identity` 解析（GUID 直通 / `{site}_` mock /
            Vault 唯一 GUID 启发）；无 DB 注入，故 source 会如实标成
            `guid|mock-prefix|vault-single-guid|unresolved`，`unresolved` 时
            watcher 必然等不到（family 前缀是 GUID），reason 里会点明；
          - `site_label` 仅用于报告，与产物前缀分开记。
        """
        site_label = str(site_label if site_label is not None else (site or ""))
        target_site = str(case_prefix or "").strip()
        case_source = "caller"
        case_candidates = ()
        if not target_site:
            identity = resolve_case_identity(site, vault_root=self.vault_root)
            target_site = identity.case_prefix
            case_source = identity.source
            case_candidates = identity.candidates

        sent = self._wall() if send_wall is None else float(send_wall)
        timeout = (float(timeout_seconds) if timeout_seconds is not None
                   else self.timeout_seconds)
        cfg = self._cfg._replace(timeout_seconds=timeout)
        expected = sorted({int(n) for n in (expected_ns or ())})
        scan_syn = self._scan_dirs((SYNTHESIS_DIR,))
        deadline = self._mono() + timeout
        target_site = target_site or None
        target_n = max(expected) if expected else None
        watchers = {}

        while True:
            snapshot = scan_syn(self.vault_root)
            fresh_map = _collect_fresh_synthesis_all(
                snapshot.get(SYNTHESIS_DIR) or {}, sent, self.clock_grace)
            if not target_site:
                target_site = _pick_family_site_from_map(fresh_map)
            if target_site:
                cases = list(expected)
                if not cases:
                    present = sorted(
                        fresh_map.get(target_site.lower(), {}).get("cases", {}))
                    if present:
                        if target_n is not None:
                            present = present + [target_n]
                        target_n = max(present)
                        cases = [target_n]
                for case in cases:
                    if case in watchers:
                        continue
                    watcher = SynthesisCompletionWatcher(
                        self.vault_root, site=target_site, case=case, cfg=cfg,
                        clock=self.clock, scan=scan_syn, sleep=self.sleep,
                        sent_wall=sent, site_label=site_label)
                    # gate 无"发送前综合快照"注入点（Launcher 只快照 BIN），
                    # 综合防残留由 mtime 下界承担；基线留空即"不额外否决"。
                    watcher.baseline = {SYNTHESIS_DIR: {}}
                    watcher.baseline_wall = sent
                    watcher.started = self._mono()
                    watcher.last_activity = watcher.started
                    watchers[case] = watcher

            frames = {case: watcher.poll() for case, watcher in watchers.items()}
            if frames and all(f.get("status") != WAITING for f in frames.values()):
                break
            if self._mono() >= deadline + cfg.poll_interval:
                frames = {case: (frame if frame.get("status") != WAITING else dict(
                    frame, status="timeout", threeState=three_state_of("timeout"),
                    reason="综合等待到门截止时间仍未齐套（门层保底投影）"))
                    for case, frame in frames.items()}
                break
            self.sleep(cfg.poll_interval)
            self._tick(cfg.poll_interval)

        return self._project_synthesis(
            frames, target_site, target_n, expected,
            site_label=site_label, case_source=case_source,
            case_candidates=case_candidates)

    def _project_synthesis(self, frames, target_site, target_n, expected,
                           site_label=None, case_source=None, case_candidates=()):
        missing_by_n = {}
        for case, frame in sorted(frames.items()):
            if frame.get("status") != "completed":
                missing_by_n[case] = list(frame.get("missingRequired") or ())
        missing = sorted({name for miss in missing_by_n.values() for name in miss})
        statuses = [f.get("status") for f in frames.values()]
        status = next((s for s in self._WORST_ORDER if s in statuses), "timeout")
        if not frames:
            status = "timeout"
            reason = ("综合超时未完成（%s），站点=%s 产物前缀=%s（来源=%s）期望综合号=%s："
                      "SYNTHESIS_FILES 未出现任何新鲜家族产物"
                      % (self.timeout_seconds, site_label or "-",
                         target_site or "未解析", case_source or "-",
                         expected or "自动发现"))
        elif len(frames) == 1:
            reason = next(iter(frames.values())).get("reason") or ""
        else:
            reason = "；".join("%s=%s:%s" % (case, f.get("status"), f.get("reason"))
                              for case, f in sorted(frames.items()))
        failed_kinds = sorted({str(f.get("failedKind")) for f in frames.values()
                               if f.get("failedKind")})
        return self._frame(
            "comprehensive", ok=(status == "completed"), status=status, reason=reason,
            targetN=target_n, targetSite=target_site,
            casePrefix=target_site, siteLabel=site_label or "",
            caseSource=case_source, caseCandidates=list(case_candidates or ()),
            expectedNs=list(expected), missingRequired=missing,
            missingByN=missing_by_n,
            failedKind=failed_kinds[0] if failed_kinds else None,
            threeState=three_state_of(status),
            exportAllowed=bool(frames) and all(bool(f.get("exportAllowed"))
                                               for f in frames.values()),
            pollCount=max([int(f.get("pollCount") or 0) for f in frames.values()] or [0]),
            elapsedSeconds=max([float(f.get("elapsedSeconds") or 0.0)
                                for f in frames.values()] or [0.0]),
            diagnostics={
                "missingRequired": missing,
                "missingByN": missing_by_n,
                "casePrefix": target_site,
                "caseSource": case_source,
                "caseCandidates": list(case_candidates or ()),
                "cases": {str(case): f.get("status") for case, f in sorted(frames.items())},
            })

    # -- 帧 -------------------------------------------------------------
    def _frame(self, flow, **kw):
        base = {"ok": False, "status": "waiting", "reason": "",
                "flow": flow, "vaultRoot": self.vault_root}
        base.update(kw)
        return base


# ---------------------------------------------------------------------------
# 辅助函数（纯函数，便于测试与复用）
# ---------------------------------------------------------------------------
def _collect_fresh_synthesis_all(synnow, sent, clock_grace):
    """按产物前缀与综合号收集新鲜产物。

    返回: {prefix_lower: {"_orig_name": orig_prefix, "cases": {n: {filename_lower: (orig_name, size, mtime)}}}}
    前缀在真实 Vault 是 `Cases.Id`（GUID），mock vault 是站点名。
    """
    out = {}
    cutoff = float(sent) - float(clock_grace)
    for name, value in (synnow or {}).items():
        if value[1] < cutoff:
            continue
        parsed = parse_synthesis_filename(name)
        if not parsed:
            continue
        matched_site, n, _kind, _ext = parsed
        site_key = matched_site.lower()
        if site_key not in out:
            out[site_key] = {"_orig_name": matched_site, "cases": {}}
        cases = out[site_key]["cases"]
        if n not in cases:
            cases[n] = {}
        cases[n][os.path.basename(name).lower()] = (name, value[0], value[1])
    return out


def _pick_family_site_from_map(fresh_map):
    """从发现的新鲜产物族中选出产物最多的前缀（保留原大小写；真实 Vault 即 CaseGUID）。"""
    if not fresh_map:
        return None

    def _weight(item):
        _site_key, data = item
        total_files = sum(len(fmap) for fmap in data["cases"].values())
        return (-total_files, data["_orig_name"])

    return sorted(fresh_map.items(), key=_weight)[0][1]["_orig_name"]
