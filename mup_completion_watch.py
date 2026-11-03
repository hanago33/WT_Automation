# encoding: utf-8
"""MUP CFD/综合计算完成信号**七态判定环**（蓝图 v3 §2.3 验收口径）。

与 `mup_completion` 的分工（2026-11-02 归一后）：
  · `mup_completion.CompletionGate` —— **接线适配壳**：把本模块的七态终态投影成
    `(ok, status, reason)` 供 `_simple_gate_wait_section` 段间 fail-closed；本身不再
    持有任何 oracle（原先的进程面/status 五词/五扩齐套判据已删除）。
  · `mup_completion_watch`（本模块）—— **判据唯一实现者**：七态机（K2 §2.3 + R3 §C
    13 条必改 + 蓝图 §2.3），返回 cancelled / completed / completed_with_gaps /
    failed / not_started / stalled / timeout / waiting 及三态投影。
  边界：接线层只消费本模块的稳定终态（status/threeState），不消费内部中间态；
  本模块只判据、不点发送、不建站、不导数据。两者共享 `mup_paths.vault_root`
  为 vault 根唯一解析者（mup_paths 是 WT_Automation 内路径唯一 owner）。
  不重复制造第三个完成判定：tower/扇区门禁（§2.5 2-A）由其它文件实现，本模块不接。

上游证据链（docstring 逐条引用 §，便于 review 交叉核对）：
- R3  99_synthesis/2026-10-31-r3-codebuddy-audit.md §C（13 条必改，本轮全部落地）：
      C1 基线残留不计完成 / C2 按方向早退 / C3 期望集合 / C4 id 前缀签名 /
      C5 stalled 可达 / C6 phase 分离 / C7 not_started / C8 mtime 时间下界 /
      C11 时间基准 / C13 vault 根选择。
- 蓝图 v3 §2.3.3 七态优先级：cancelled > completed / completed_with_gaps >
  failed > not_started > stalled > timeout > waiting。
- S1  99_synthesis/2026-10-31-s1-zcode-watcher-impl.md（**权威实现基础**，917 行，
      本轮在其既有骨架上的验收硬化，非重写）。
- R1  §B.1.3 MTDPath 命名族 / §B.3.1 官方删旧看文件 / §C-2 基线增量等价。
- K2  99_synthesis/evidence/k2_watcher_prototype.py（旧原型；C1/C2/C5/C6 反例的
      FAIL 靶，见 tests/ 与报告）。

红线（全程）：只读 vault 文件；判定环**不读退出码/不持进程句柄/不等待进程/
不查进程表**（静态断言钉在 tests 里）；纯 stdlib；不连 LocalDB；不触 HASP 许可。
每条反例测试同时证明"旧原型会 FAIL、本实现不 FAIL"，见报告 §Observed。

零外部依赖，纯 stdlib 轮询；测试注入 clock/scan/sleep 假件，零真实 sleep、
零真实 MUP、零真实盘写。
"""
import os
import time
from collections import namedtuple

import mup_paths  # noqa: F401   vault 根唯一解析者（K2 §3.4 双处同源）

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
# 文件名族字面量（LOGOUT_PREFIX / LOGOUT_EXT / CANCEL_PREFIX / STOPJOB_PREFIX /
# DEBUG_PREFIX / ERROR_LOG）的唯一 owner 是 leaf `mup_vault_names`（见下方 import 区）；
# 本模块只保留目录名与阈值，不再复制一份字符串表。
BIN_DIR = "BIN"
RESULT_DIR = "RESULT_FILES"
INFO_DIR = "INFORMATION"
SYNTHESIS_DIR = "SYNTHESIS_FILES"

CFD_WATCH_DIRS = (BIN_DIR, RESULT_DIR, INFO_DIR)
SYNTHESIS_WATCH_DIRS = (SYNTHESIS_DIR,)

# 终态与中间态（蓝图 §2.3.3 七态；每个成员都必须有三态投影）
TERMINAL_STATUSES = ("completed", "completed_with_gaps", "cancelled",
                     "stalled", "failed", "timeout", "not_started")
WAITING = "waiting"

WatchConfig = namedtuple(
    "WatchConfig",
    ["poll_interval", "timeout_seconds", "stable_polls", "stable_min_seconds",
     "results_stable_polls", "min_new_results", "startup_grace_seconds",
     "stall_seconds", "clock_grace_seconds"],
)
# 蓝图 §2.3 阈值表（Proposed：与仓库既有常数同族、可测；金样后按实测调整）
DEFAULT_CONFIG = WatchConfig(
    poll_interval=2.0,            # wait_global_mapper_ready.py 的 2s 同族
    timeout_seconds=3600.0,       # 单批起点；编排层按段配（CFD/综合 8~12h）
    stable_polls=3,               # (size,mtime) 连续稳定轮数（§2.3.1 稳定行）
    stable_min_seconds=4.0,       # 与轮询间隔解耦的绝对静默下界（§2.3.1）
    results_stable_polls=2,       # RESULT_FILES 目标前缀签名稳定轮数（C4）
    min_new_results=1,            # 仅当未知期望集合时的下限（C3：期望集合优先）
    startup_grace_seconds=120.0,  # 发送宽限；窗口内无新文件 ⇒ not_started（C7）
    stall_seconds=900.0,          # 失速：见过信号后 15 分钟无任何写入（C5）
    clock_grace_seconds=2.0,      # mtime 比较容差（沿用 mup_data_files.diff 2.0s）
)


# ---------------------------------------------------------------------------
# 时钟与扫描注入点（测试假时钟 + 假 vault，零真实盘/进程）
# ---------------------------------------------------------------------------
class FakeClock(object):
    """可脚本化时钟：只有 advance() 被调用时间才前进。

    monotonic()（超时/静默/失速）与 wall()（与 st_mtime 比较）分开是 C11 要点；
    假实现两者同源是有意简化，生产用 _RealClock 分开。
    """

    def __init__(self, start=1000.0):
        self.now = float(start)

    def monotonic(self):
        return self.now

    def wall(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


class _RealClock(object):
    """生产时钟：monotonic 用于超时/失速，wall 用于与 st_mtime 比较（不混用）。"""

    def monotonic(self):
        return time.monotonic()

    def wall(self):
        return time.time()


def scan_watch_dirs(vault_root, sub_dirs=CFD_WATCH_DIRS):
    """真实窄扫描：{dir: {name: (size, mtime)}}；os.scandir 逐项容错。

    每轮只枚举本原语需要的 2~3 个目录；不读文件内容、不算哈希（C12 金样后
    才考虑 XML 终态节点）。目录缺失/读取失败 → 空 dict（不抛）。
    """
    out = {}
    for sub in sub_dirs:
        entries = {}
        base = os.path.join(vault_root, sub) if vault_root else ""
        if base and os.path.isdir(base):
            try:
                with os.scandir(base) as iterator:
                    for entry in iterator:
                        try:
                            info = entry.stat(follow_symlinks=False)
                            entries[entry.name] = (info.st_size, info.st_mtime)
                        except OSError:
                            continue
            except OSError:
                pass
        out[sub] = entries
    return out


def make_scan(bin_files=None, result_files=None, info_files=None, synthesis_files=None):
    """假 vault 构造器：返回 scan(vault_root) 注入点，带 .state 供测试改写。"""
    state = {
        BIN_DIR: dict(bin_files or {}),
        RESULT_DIR: dict(result_files or {}),
        INFO_DIR: dict(info_files or {}),
        SYNTHESIS_DIR: dict(synthesis_files or {}),
    }

    def scan(_vault_root):
        return {key: dict(value) for key, value in state.items()}

    scan.state = state
    return scan


# ---------------------------------------------------------------------------
# 文件名匹配器（单一 owner = `mup_vault_names`；本模块只 re-export 保持既有 import）
# ---------------------------------------------------------------------------
# 2026-11-02 归一（审计 §5.1 名字匹配 owner 一行）：本文件曾自带第二套同义实现
# （is_logout_for / is_new_result_for / is_synthesis_product / synthesis_*_products），
# 与 leaf 的大写敏感度、None 语义、可选族成员三点漂移。归一后：
#   · 前缀匹配统一大小写不敏感（Windows 文件系统语义，leaf 一处实现）；
#   · 2026-11-02 判据改口（蓝图 §0.1）：必出 = leaf.synthesis_vault_mandatory_products
#     （`.wri + .wrp`）；族识别 = leaf.is_vault_synthesis_product（多族正则，前缀为
#     Cases.Id）；诊断族只影响"是否已起算"与 failed(partial)，不影响必出集合；
#   · cfdId 解析统一走 leaf.parse_logout_id（数字边界一处实现）。
from mup_vault_names import (  # noqa: E402  (导入位置跟随既有分区，便于对照 diff)
    is_logout_for,
    is_new_result_for,
    is_cancel_marker,
    is_debug_calcul,
    is_error_log,
    is_synthesis_product,
    is_vault_synthesis_product,
    parse_logout_id,
    synthesis_required_products,          # legacy 旧口径（生产不再消费，保留 re-export）
    synthesis_vault_mandatory_products,   # 生产必出 2 件套（.wri + .wrp）
    synthesis_optional_products,
    synthesis_map_product,
)


# ---------------------------------------------------------------------------
# 七态 → 三态投影（蓝图 §2.3.3 状态机 + R3 §C 终态归因）
# ---------------------------------------------------------------------------
_THREE_STATE_MAP = {
    WAITING: "in_progress",
    "completed": "success",
    # completed_with_gaps 默认禁止放行导出，但本身是"算出了"的成功态（需人确认缺口）。
    "completed_with_gaps": "success",
    # 调度视角：取消/失速/失败/超时本批无成功产物，后续段必须中止。
    "cancelled": "failed",
    "stalled": "failed",
    "failed": "failed",
    "timeout": "failed",
    "not_started": "failed",
    # 表外旧状态仅作兼容别名，不再产出：
    # completed_with_errors 折叠进 completed（diagnostics.errorMarkers 非空）；
    # not_ready 折叠进 not_started（result.notReady=True + reason）。
    "completed_with_errors": "success",
    "not_ready": "failed",
}


def three_state_of(status):
    """七态 → 三态投影（in_progress/success/failed）。

    未知状态 fail-closed 落到 failed：三态词表里没有 unknown，调度面不得收到
    threeState="unknown"（审计 §5.2-3）。
    """
    return _THREE_STATE_MAP.get(str(status), "failed")


def _id_key(value):
    """cfdId 归一化键：'8' / 8 视为同一个；非数字原样返回。"""
    text = str(value).strip()
    return int(text) if text.isdigit() else value


# ---------------------------------------------------------------------------
# watcher 公共骨架（clock/scan/sleep 注入；poll 幂等、wait 阻塞；假时钟推进协议）
# ---------------------------------------------------------------------------
class _WatcherBase(object):
    """生命周期：定位校验、基线捕获、poll/wait 循环、假时钟推进。

    K2 §2.3：vault_root 为空/不存在 ⇒ not_started(notReady=True)（显式降级，
    不伪装成 timeout；词表只有七态，not_ready 不再作为表外状态泄漏）。
    注入 scan 时跳过真实存在性检查（测试假 vault 不在真实盘上），只对空根短路。
    """

    WATCH_DIRS = CFD_WATCH_DIRS

    def __init__(self, vault_root, cfg, clock, scan, sleep,
                 sent_wall=None, not_ready_reason=None):
        self.vault_root = str(vault_root or "")
        self.cfg = cfg if cfg is not None else DEFAULT_CONFIG
        self.clock = clock if clock is not None else _RealClock()
        if scan is not None:
            self.scan = scan
        else:
            self.scan = lambda root, _dirs=self.WATCH_DIRS: scan_watch_dirs(root, _dirs)
        self.sleep = sleep if sleep is not None else time.sleep
        self.sent_wall = None if sent_wall is None else float(sent_wall)
        self._not_ready_reason = not_ready_reason
        self.baseline = None
        self.baseline_wall = None
        self.polls = 0
        self.started = None
        self.last_activity = None
        if not self.vault_root:
            self._not_ready_reason = (
                "vault_root 为空：mup_paths.vault_root() 未命中"
                "（env MUP_VAULT_ROOT/MUP_DATA_DIR / PROD ini [data] path / 盘符，mup_paths）")
        elif scan is None and not os.path.isdir(self.vault_root):
            self._not_ready_reason = "vault_root 不存在：%s" % self.vault_root

    def capture_baseline(self):
        """基线 = 窄扫描快照 + 挂钟锚点（防秒回"记基线做增量"侧，R1 §C-2）。"""
        if self._not_ready_reason:
            self.baseline = {}
            return self.baseline
        self.baseline = self.scan(self.vault_root)
        self.baseline_wall = self.clock.wall()
        self.started = self.clock.monotonic()
        self.last_activity = self.started
        return self.baseline

    def _advance(self):
        """假时钟推进协议：真时钟无 advance（自然流逝），假时钟按 poll_interval 前进。"""
        advance = getattr(self.clock, "advance", None)
        if callable(advance):
            advance(self.cfg.poll_interval)

    def _not_ready_result(self):
        """vault 不可定位：折叠进 not_started（词表内），用 notReady + reason 保留归因。"""
        return {
            "status": "not_started",
            "threeState": three_state_of("not_started"),
            "notReady": True,
            "reason": "vault 未就绪（not_ready）：%s"
                      % (self._not_ready_reason or "vault 不可用"),
            "pollCount": self.polls,
            "elapsedSeconds": 0.0,
            "vaultRoot": self.vault_root,
        }

    def poll(self):
        """单轮非阻塞判定：返回状态 dict，waiting 表示继续；幂等。"""
        if self._not_ready_reason:
            return self._not_ready_result()
        if self.baseline is None:
            raise RuntimeError("capture_baseline() 必须先调用（或 wait()/门面函数）")
        self.polls += 1
        now_mono = self.clock.monotonic()
        snapshot = self.scan(self.vault_root)
        self._observe(snapshot, now_mono)
        return self.evaluate(now_mono)

    def wait(self, on_progress=None, progress_every_seconds=30.0):
        """阻塞至终态（CLI/队列 worker 用；Tk 里不要直接调）。"""
        if self.baseline is None:
            self.capture_baseline()
        if self._not_ready_reason:
            return self._not_ready_result()
        last_report = None
        while True:
            result = self.poll()
            if result["status"] != WAITING:
                return result
            if on_progress is not None:
                now_mono = self.clock.monotonic()
                if last_report is None or now_mono - last_report >= progress_every_seconds:
                    last_report = now_mono
                    on_progress(result)
            self.sleep(self.cfg.poll_interval)
            self._advance()


# ---------------------------------------------------------------------------
# CFD 完成 watcher（phase="cfd"；C1/C3/C4/C5/C7/C8 硬化）
# ---------------------------------------------------------------------------
class MupCompletionWatcher(_WatcherBase):
    """监听一批 CFD 计算是否完成（蓝图 §2.3.1 CFD 判据表）。

    完成三条件（R1 §D + R3 §C 修正，合取）：
      条件1 CALCUL_log_out_<cfdId>.xml 家族"新鲜"出现（基线增量 + mtime 下界，C1/C8）
      条件2 该文件 (size,mtime) 连续 stable_polls 轮不变且静默 ≥ stable_min_seconds
      条件3 RESULT_FILES 目标 id 前缀基线增量 ≥ min_new_results 且目标前缀签名稳定
            （C4 id 前缀收窄，不共享整目录签名）
      期望集合（C2/C3）：completed 必须以 named 模式 + expected_ids 断言全部命中；
            增量模式（cfd_ids=()）只能做 id 发现，**永不判完成**（C2 反例靶）。
      否决项 取消/停止标记 → cancelled（优先级最高，蓝图 §2.3.3）。
      缺口项 expected_gaps 显式声明"配置决定不参与"的缺项 → completed_with_gaps。
    """

    WATCH_DIRS = CFD_WATCH_DIRS

    def __init__(self, vault_root, cfd_ids=(), expected_ids=None, expected_gaps=(),
                 cfg=None, clock=None, scan=None, sleep=None,
                 sent_wall=None, not_ready_reason=None):
        _WatcherBase.__init__(self, vault_root, cfg, clock, scan, sleep,
                              sent_wall=sent_wall,
                              not_ready_reason=not_ready_reason)
        self.cfd_ids = tuple(cfd_ids or ())
        # expected_ids: {cfdId: set(期望结果名家族前缀)}；None/空 = 增量发现模式。
        self.expected_ids = dict(expected_ids or {})
        if not self.expected_ids and self.cfd_ids:
            # named 模式下期望集合缺省会要求每个 id 至少 min_new_results 个结果。
            self.expected_ids = {int(c): None for c in self.cfd_ids}
        self.expected_gaps = tuple(expected_gaps or ())
        # 缺口 id = 配置决定不参与的期望项；判定前必须从 required 集合剔除（§5.2-4）。
        self._gap_ids = frozenset(_id_key(g) for g in self.expected_gaps)
        self._seen = {}            # name -> {size, mtime, stable_polls, first_seen_poll, last_change}
        self._new_results = set()  # 基线之后新增且 cfdId 前缀命中
        self._res_sig = {}         # {id: signature}（C4：按 id 前缀分桶）
        self._res_stable_polls = {}  # {id: n}
        self._cancel_markers = []
        self._error_markers = []
        self._error_watermark = {}  # name -> 已记录过的最大 size（增量去重）
        self._debug_calcul = []
        self._stale_logout = []     # 基线残留且未被重写（诊断可见，不进判定）
        # not_started / 缺口归因
        self._any_signal = False
        self._gaps_hit = set()

    # -- 新鲜性（C1/C8：基线增量 + mtime 时间下界） -----------------------
    def _is_fresh(self, name, current, baseline_entries):
        """基线增量判新鲜（R1 §C-2 等价官方删旧，只读不删盘）。

        C8：mtime >= sent_wall - clock_grace 是硬下界（早于发送时刻一律旧）。
        C1：基线里已有且 mtime 未前进超容差 = 上一轮残留，不得当本轮信号。
        """
        if self.sent_wall is not None and current[1] < (self.sent_wall - self.cfg.clock_grace_seconds):
            return False
        previous = baseline_entries.get(name)
        if previous is None:
            return True
        return current[1] > (previous[1] + self.cfg.clock_grace_seconds)

    # -- 单轮观察 -------------------------------------------------------
    def _observe(self, snapshot, now_mono):
        bin_now = snapshot.get(BIN_DIR) or {}
        res_now = snapshot.get(RESULT_DIR) or {}
        info_now = snapshot.get(INFO_DIR) or {}
        base_bin = self.baseline.get(BIN_DIR) or {}
        base_res = self.baseline.get(RESULT_DIR) or {}
        base_info = self.baseline.get(INFO_DIR) or {}
        targets = list(self.expected_ids) or (self.cfd_ids or (None,))
        changed = False

        # 取消/诊断标记：只认基线后的新增（历史残留不得判死新一轮）
        for name in sorted(set(bin_now) - set(base_bin)):
            if is_cancel_marker(name):
                self._cancel_markers.append(name)
                changed = True
            elif is_debug_calcul(name):
                self._debug_calcul.append(name)
                changed = True

        # Error.log 水位：新增或增长记标记（K2 §2.5：不抢占完成，只影响终态归因）
        for name in sorted(set(info_now) | set(base_info)):
            if not is_error_log(name):
                continue
            current = info_now.get(name)
            current_size = current[0] if current else None
            watermark = self._error_watermark.get(name)
            if watermark is None:
                if name in base_info:
                    self._error_watermark[name] = base_info[name][0] or 0
                    continue
                if current_size is None:
                    continue
                self._error_watermark[name] = current_size
                self._error_markers.append("new:%s" % name)
                changed = True
            elif current_size is not None and current_size > watermark:
                self._error_watermark[name] = current_size
                self._error_markers.append("grown:%s" % name)
                changed = True

        # 条件 1/2：logout 家族（先过新鲜闸门，再计稳定轮数）
        for name in sorted(set(bin_now) | set(base_bin)):
            if not is_logout_for(name):
                continue
            current = bin_now.get(name)
            if current is None:
                continue  # 基线有、现在没了 = 官方删旧动作，正常
            if not self._is_fresh(name, current, base_bin):
                if name not in self._stale_logout:
                    self._stale_logout.append(name)
                continue
            record = self._seen.get(name)
            if record is None:
                self._seen[name] = {"size": current[0], "mtime": current[1],
                                    "stable_polls": 1, "first_seen_poll": self.polls,
                                    "last_change": now_mono}
                changed = True
            elif record["size"] == current[0] and record["mtime"] == current[1]:
                record["stable_polls"] += 1
            else:
                record.update({"size": current[0], "mtime": current[1],
                               "stable_polls": 1, "last_change": now_mono})
                changed = True

        # 条件 3：RESULT_FILES 基线增量 × 目标 id 前缀收窄（C4 per-id 归因）
        for name in sorted(set(res_now) - set(base_res)):
            if any(is_new_result_for(name, cid) for cid in targets):
                if name not in self._new_results:
                    self._new_results.add(name)
                    changed = True

        # 目标前缀签名稳定（C4：只对属于本 watcher 目标的前缀文件做签名）
        target_names = set()
        for name in res_now:
            if any(is_new_result_for(name, cid) for cid in targets):
                target_names.add(name)
        ids = list(dict.fromkeys(
            int(c) for c in targets if isinstance(c, int) or str(c).isdigit()))
        for cid in ids:
            subset = tuple(sorted(
                (n, res_now[n][0], round(res_now[n][1], 3))
                for n in target_names if is_new_result_for(n, cid)))
            prev = self._res_sig.get(cid)
            if prev is None:
                self._res_sig[cid] = subset
            elif subset == prev:
                self._res_stable_polls[cid] = self._res_stable_polls.get(cid, 0) + 1
            else:
                self._res_sig[cid] = subset
                self._res_stable_polls[cid] = 1
                changed = True

        if self._seen or self._new_results or self._cancel_markers or self._error_markers:
            self._any_signal = True
        # C5：last_activity 只在 (size,mtime) 变化时刷新（changed 恒非真则 stall 可达）。
        if changed:
            self.last_activity = now_mono

    # -- 状态机（蓝图 §2.3.3 优先级） -----------------------------------
    def evaluate(self, now_mono):
        elapsed = now_mono - self.started if self.started is not None else 0.0
        targets = list(self.expected_ids) or (self.cfd_ids or (None,))
        not_started = (elapsed > self.cfg.startup_grace_seconds and not self._any_signal)

        # §5.2-4：缺口项先从 required 剔除再判定；gap id 自身不要求 ok。
        required_ids = [cid for cid in self.expected_ids
                        if _id_key(cid) not in self._gap_ids]
        required_keys = set(_id_key(cid) for cid in required_ids)
        gaps_applied = sorted(str(cid) for cid in self.expected_ids
                              if _id_key(cid) in self._gap_ids)

        per_cid = {}
        all_expected_ok = True
        missing_expected = []
        for cid in targets:
            key = "增量(未指名)" if cid is None else str(cid)
            appeared, stable, growing = [], [], []
            for name, record in self._seen.items():
                if not is_logout_for(name, cid):
                    continue
                appeared.append(name)
                quiet = now_mono - record["last_change"]
                if (record["stable_polls"] >= self.cfg.stable_polls
                        and quiet >= self.cfg.stable_min_seconds):
                    stable.append(name)
                else:
                    growing.append(name)
            matched = sorted(r for r in self._new_results if is_new_result_for(r, cid))
            per_cid[key] = {"appeared": appeared, "stable": stable,
                            "growing": growing, "newResults": matched}
            # named 期望模式：required id 必须出现 + 稳定 + 结果达 min_new_results。
            if _id_key(cid) in required_keys:
                id_ok = bool(stable) and len(matched) >= self.cfg.min_new_results
                if not id_ok:
                    all_expected_ok = False
                    missing_expected.append(str(cid))

        # 结果签名稳定：每个 required id 的目标前缀签名都要稳定（C4 per-id）。
        res_ok = True
        if required_ids:
            for cid in required_ids:
                key = str(cid)
                matched = per_cid.get(key, {}).get("newResults", [])
                stab = self._res_stable_polls.get(_id_key(cid), 0)
                if len(matched) >= self.cfg.min_new_results \
                        and stab >= self.cfg.results_stable_polls:
                    continue
                res_ok = False

        delta_done = all_expected_ok and res_ok
        # C2/C3：增量模式（无期望集合）永不判完成 —— 只做发现，无"完成"权。
        has_expected = bool(self.expected_ids)
        completed_conditions = (has_expected and all_expected_ok and res_ok)
        stalled = (self._any_signal
                   and (now_mono - self.last_activity) >= self.cfg.stall_seconds)

        sub = {
            "appeared": bool(self._seen),
            "stable": completed_conditions,
            "delta": delta_done,
            "notStarted": not_started,
            "hasExpected": has_expected,
            "requiredIds": sorted(str(cid) for cid in required_ids),
            "gapsApplied": list(gaps_applied),
            "missingExpected": list(missing_expected),
            "elapsedOverGrace": elapsed > self.cfg.startup_grace_seconds,
        }
        diagnostics = {
            "errorMarkers": list(self._error_markers),
            "cancelMarkers": list(self._cancel_markers),
            "debugCalcul": list(self._debug_calcul),
            "staleLogoutFiles": list(self._stale_logout),
            "gapsApplied": list(gaps_applied),
            "solverProcesses": "unknown",  # 红线：进程面不进判定环
        }
        result = self._result_frame(now_mono, sub, diagnostics)
        result["perCfd"] = per_cid

        # 蓝图 §2.3.3 优先级：cancelled > completed/gaps > failed > not_started > stalled > timeout > waiting
        if self._cancel_markers:
            result["status"] = "cancelled"
            result["reason"] = ("BIN 基线后出现取消/停止标记 %s" % self._cancel_markers[0])
            result["threeState"] = three_state_of(result["status"])
            return result

        if completed_conditions:
            result["exportAllowed"] = True
            if gaps_applied:
                # 缺口是"配置决定不参与"，命中其余 required 即 gaps 完成；
                # 默认禁止放行导出，必须人工确认（§2.3.3 / §5.2-4）。
                result["status"] = "completed_with_gaps"
                result["exportAllowed"] = False
                result["reason"] = ("期望集合（剔除不参与缺口 %s 后）全部命中；"
                                    "缺口=%s（默认禁止放行导出，需人确认）"
                                    % (sorted(str(c) for c in required_ids),
                                       gaps_applied))
            else:
                result["status"] = "completed"
                if self._error_markers:
                    # §5.2-3：completed_with_errors 折叠进 completed，不抢占完成；
                    # 错误面只进 diagnostics.errorMarkers，三态恒为 success。
                    result["reason"] = ("三条件满足：logout 新鲜+稳定 + 目标前缀结果增量齐套"
                                        "（期望 cfdId=%s）；Error.log 有新增/增长，"
                                        "已折叠进 diagnostics.errorMarkers（不抢占完成，需人工复核）"
                                        % sorted(self.expected_ids))
                else:
                    result["reason"] = ("三条件满足：logout 新鲜+稳定 + 目标前缀结果增量齐套"
                                        "（期望 cfdId=%s）" % sorted(self.expected_ids))
            result["threeState"] = three_state_of(result["status"])
            return result

        if not_started:
            result["status"] = "not_started"
            result["reason"] = ("startup_grace %.0fs 内无任何新文件：发送可能 fail-open"
                                "（S5 step_15 onError=continue），独立终态，显式报因"
                                % self.cfg.startup_grace_seconds)
            result["threeState"] = three_state_of(result["status"])
            return result

        if stalled:
            result["status"] = "stalled"
            result["reason"] = ("已见过信号，但最近 %.0fs vault 无任何写入（卡死/排队）"
                                % self.cfg.stall_seconds)
            result["threeState"] = three_state_of(result["status"])
            return result

        if elapsed >= self.cfg.timeout_seconds:
            if self._error_markers:
                result["status"] = "failed"
                result["reason"] = ("到点未完成且 Error.log 有增长（产物缺 + 错误面 → 失败）")
            else:
                result["status"] = "timeout"
                result["reason"] = ("超时未完成（%s）" % (
                    "全程未见到任何信号，可能根本没起算" if not_started
                    else "见过信号但期望集合未满"))
            result["threeState"] = three_state_of(result["status"])
            return result

        # waiting reason 分支化：named 模式不得再误报"增量发现模式"（§5.2-3）。
        if not has_expected:
            reason = ("增量发现模式：仅做 cfdId 发现，不判完成"
                      "（C2：需要 expected_ids 才能判定齐套）")
        elif not self._seen and not self._new_results:
            reason = ("已登记期望 cfdId=%s，尚未见到新鲜 logout"
                      "（未起算或写入尚未落盘），继续等待"
                      % sorted(str(cid) for cid in required_ids))
        else:
            parts = []
            if missing_expected:
                parts.append("期望 cfdId 未就绪 %s" % missing_expected)
            if not res_ok:
                pending_sig = [str(cid) for cid in required_ids
                               if self._res_stable_polls.get(_id_key(cid), 0)
                               < self.cfg.results_stable_polls]
                if pending_sig:
                    parts.append("目标前缀结果签名未稳定 %s" % pending_sig)
            if not parts:
                parts.append("等待稳定轮数/静默下界满足")
            reason = "已见信号，等待齐套：" + "；".join(parts)
        result["status"] = WAITING
        result["reason"] = reason
        result["threeState"] = three_state_of(WAITING)
        return result

    def _result_frame(self, now_mono, sub, diagnostics):
        elapsed = now_mono - self.started if self.started is not None else 0.0
        return {
            "status": WAITING,
            "reason": "",
            "threeState": three_state_of(WAITING),
            "cfdIds": list(self.cfd_ids),
            "expectedIds": {str(k): (sorted(v) if v else None)
                            for k, v in self.expected_ids.items()},
            "expectedGaps": [str(g) for g in self.expected_gaps],
            "gapsApplied": sorted(str(cid) for cid in self.expected_ids
                                  if _id_key(cid) in self._gap_ids),
            "exportAllowed": False,
            "seenCfdIds": self._seen_cfd_ids(),
            "logoutFiles": [
                {"name": name, "path": os.path.join(self.vault_root, BIN_DIR, name),
                 "size": record["size"], "mtime": record["mtime"],
                 "firstSeenPoll": record["first_seen_poll"],
                 "stablePolls": record["stable_polls"]}
                for name, record in sorted(self._seen.items())
            ],
            "newResults": sorted(self._new_results),
            "newResultsCount": len(self._new_results),
            "elapsedSeconds": round(elapsed, 3),
            "pollCount": self.polls,
            "vaultRoot": self.vault_root,
            "sub": sub,
            "diagnostics": diagnostics,
            "evidence": {
                "baselineCounts": {key: len(value or {})
                                   for key, value in (self.baseline or {}).items()},
                "finalCounts": {
                    BIN_DIR: len(self._seen) + len(self._stale_logout),
                    RESULT_DIR: len(self._new_results),
                    INFO_DIR: len(self._error_watermark),
                },
            },
        }

    def _seen_cfd_ids(self):
        """增量模式把观察到的 cfdId 回吐（cfd_map 的最便宜来源候选）。

        cfdId 解析走 leaf `parse_logout_id`（数字边界一处实现，不再本文件内联）。
        """
        return sorted({cid for cid in map(parse_logout_id, self._seen)
                       if cid is not None})


# ---------------------------------------------------------------------------
# 综合完成 watcher（phase="synthesis"；C6：信号面只有 SYNTHESIS_FILES，禁 BIN）
# ---------------------------------------------------------------------------
class SynthesisCompletionWatcher(_WatcherBase):
    """监听综合（WRA/后处理）计算是否完成（蓝图 §2.3.2 + 2026-11-02 §0.1 改口）。

    判据：SYNTHESIS_FILES 下**必出 2 件套**
    `{case_prefix}_synthesis_{case}.wri` / `.wrp` 全部"新鲜"
    （mtime >= send_ts - clock_grace 硬条件，R1 §B.1.2 幂等闸 ⇒ 只判存在会把
    上一轮结果当本轮）。缺一不判完成；见过本轮家族产物但到点仍缺 → failed(partial)。

    `case_prefix` 是 `Cases.Id`（GUID）；人类可读站点名只进 `site_label` 供报告
    （真实 DEMO 的产物前缀是 GUID，传站点名会一个 family 都不命中，蓝图 §0.1）。

    诊断族（`.wrt/.wrtd×7/timeserie*.dat/{case}_{elem}_{n}.wrm`）只影响"是否已起算"
    与 diagnostics，永不进必出集合（`require_map` 保持默认 False）。

    C6 phase 分离：本类 WATCH_DIRS 只有 SYNTHESIS_FILES；判定环**从不询问**
    BIN/CALCUL_log_out_*（test_s7 行为断言钉死）。
    """

    WATCH_DIRS = SYNTHESIS_WATCH_DIRS

    def __init__(self, vault_root, site, case, cfg=None, clock=None, scan=None,
                 sleep=None, sent_wall=None, require_map=False,
                 not_ready_reason=None, site_label=None):
        _WatcherBase.__init__(self, vault_root, cfg, clock, scan, sleep,
                              sent_wall=sent_wall,
                              not_ready_reason=not_ready_reason)
        # site 形参沿用旧名，语义已是"产物前缀"（真实 Vault 为 CaseGUID）。
        self.case_prefix = str(site)
        self.site_label = str(site_label) if site_label is not None else self.case_prefix
        self.case = str(case)
        self.sent_wall = None if sent_wall is None else float(sent_wall)
        self.require_map = bool(require_map)
        self._required_state = {}
        self._family_hits = []
        self._fresh_family_hits = []
        self._family_sig = None
        self._any_signal = False

    def mark_sent(self, wall=None):
        """记录发送时刻（任务口径：mtime > 发送时刻）。wall 缺省取当前挂钟。"""
        self.sent_wall = self.clock.wall() if wall is None else float(wall)
        return self.sent_wall

    def prepare(self):
        """发送前调用：同一时刻记录基线 + 发送时刻（防秒回契约，R1 §C-2）。"""
        self.mark_sent()
        return self.capture_baseline()

    def _is_fresh(self, name, current, baseline_entries):
        """综合新鲜性双闸（蓝图 §2.3.2 + §5.2-7 时间闸统一 + R1 §C-2 基线增量）：
        1) 已知发送时刻时，mtime 早于 send_ts - clock_grace 的文件一票否决；
        2) 基线后新出现 → 新鲜；基线已有但 mtime 前进超容差 → 官方删旧重写同款；
        3) 其余 = 上一轮残留，拒绝（防 case 复用秒回）。
        """
        if self.sent_wall is not None and \
                current[1] < (self.sent_wall - self.cfg.clock_grace_seconds):
            return False
        previous = baseline_entries.get(name)
        if previous is None:
            return True
        return current[1] > (previous[1] + self.cfg.clock_grace_seconds)

    def _observe(self, snapshot, now_mono):
        syn_now = snapshot.get(SYNTHESIS_DIR) or {}
        base_syn = self.baseline.get(SYNTHESIS_DIR) or {}
        base_lower = {name.lower(): value for name, value in base_syn.items()}
        lower_map = {}
        for name, value in syn_now.items():
            lower_map[name.lower()] = (name, value)
        changed = False

        required = list(synthesis_vault_mandatory_products(self.case_prefix, self.case))
        if self.require_map:
            required.append(synthesis_map_product(self.case_prefix, self.case))
        self._required_state = {}
        for required_name in required:
            hit = lower_map.get(required_name.lower())
            fresh = bool(hit) and self._is_fresh(hit[0], hit[1], base_lower)
            self._required_state[required_name] = {
                "name": hit[0] if hit else None,
                "present": hit is not None,
                "fresh": fresh,
                "size": hit[1][0] if hit else None,
                "mtime": hit[1][1] if hit else None,
            }
            # 注意：持续"新鲜"不是活动 —— 活动只由家族签名变化驱动（C5 stall 可达）。

        self._family_hits = sorted(
            name for name in syn_now
            if is_vault_synthesis_product(name, self.case_prefix, self.case))
        # 本轮真正写过的家族文件（旧残留不算）：failed(partial) 只在"综合已起算"时成立。
        self._fresh_family_hits = sorted(
            name for name in self._family_hits
            if self._is_fresh(lower_map[name.lower()][0],
                              lower_map[name.lower()][1], base_lower))
        signature = tuple(sorted(
            (name, syn_now[name][0], round(syn_now[name][1], 3))
            for name in self._family_hits))
        if self._family_sig is None:
            self._family_sig = signature  # 首轮建立签名基准，不计活动
        elif signature != self._family_sig:
            self._family_sig = signature
            changed = True

        if self._family_hits:
            self._any_signal = True
        # C5：last_activity 只在 (size,mtime) 变化时刷新。
        if changed:
            self.last_activity = now_mono

    def evaluate(self, now_mono):
        elapsed = now_mono - self.started if self.started is not None else 0.0
        missing = [name for name, state in sorted(self._required_state.items())
                   if not state["fresh"]]
        any_activity = bool(self._family_hits)
        stalled = any_activity and (now_mono - self.last_activity) >= self.cfg.stall_seconds
        not_started = (elapsed > self.cfg.startup_grace_seconds and not self._family_hits)
        sub = {"notStarted": not_started, "missingRequired": list(missing),
               "elapsedOverGrace": elapsed > self.cfg.startup_grace_seconds,
               "hasExpected": True}
        diagnostics = {
            "familyHits": list(self._family_hits),
            "freshFamilyHits": list(self._fresh_family_hits),
            "requiredProducts": [dict(state, product=name)
                                 for name, state in sorted(self._required_state.items())],
            "sentWall": self.sent_wall,
        }
        result = {
            "status": WAITING,
            "reason": "",
            "threeState": three_state_of(WAITING),
            "site": self.case_prefix,
            "casePrefix": self.case_prefix,
            "siteLabel": self.site_label,
            "case": self.case,
            "requireMap": self.require_map,
            "missingRequired": list(missing),
            "familyHits": list(self._family_hits),
            "freshFamilyHits": list(self._fresh_family_hits),
            "exportAllowed": False,
            "elapsedSeconds": round(elapsed, 3),
            "pollCount": self.polls,
            "vaultRoot": self.vault_root,
            "sub": sub,
            "diagnostics": diagnostics,
        }
        # C6：综合判定环绝不因 BIN logout 判完成（本类只扫 SYNTHESIS_FILES）。
        if not missing:
            result["status"] = "completed"
            result["exportAllowed"] = True
            result["reason"] = ("综合必出内核齐套且全部 mtime>=发送时刻-%.1fs（%s）"
                                % (self.cfg.clock_grace_seconds,
                                   ", ".join(sorted(self._required_state))))
            result["threeState"] = three_state_of(result["status"])
            return result
        if stalled:
            # 2026-11-02 判据修正（蓝图 §5.5-b-4 方案②）：默认 stall(900s) <
            # timeout(3600s)，若先判 stalled，"已起算但缺件"永远报成 stalled，
            # failed(partial) 不可达。这里对"本轮已写过的家族产物"直接投影为
            # failed(partial)；只有"仅剩上一轮旧产物"才保留真失速。
            if self._fresh_family_hits:
                result["status"] = "failed"
                result["failedKind"] = "partial"
                result["reason"] = ("综合 partial：本轮已见家族产物 %s，但最近 %.0fs "
                                    "无任何写入且仍缺必出项 %s（失速前已起算）"
                                    % (", ".join(self._fresh_family_hits),
                                       self.cfg.stall_seconds,
                                       ", ".join(missing) or "-"))
            else:
                result["status"] = "stalled"
                result["reason"] = ("综合产物已开始出现，但最近 %.0fs 无任何写入且仍缺 %s"
                                    % (self.cfg.stall_seconds, ", ".join(missing) or "-"))
            result["threeState"] = three_state_of(result["status"])
            return result
        if not_started:
            result["status"] = "not_started"
            result["reason"] = ("startup_grace %.0fs 内无任何综合产物（发送 fail-open？）"
                                % self.cfg.startup_grace_seconds)
            result["threeState"] = three_state_of(result["status"])
            return result
        if elapsed >= self.cfg.timeout_seconds:
            if self._fresh_family_hits:
                # §5.2-5：综合已起算（见过本轮家族文件）但必出项不全 → failed(partial)，
                # 携带缺失清单，不再一律报 timeout。
                result["status"] = "failed"
                result["failedKind"] = "partial"
                result["reason"] = ("综合 partial：已见本轮家族产物 %s，但到点仍缺必出项 %s"
                                    % (", ".join(self._fresh_family_hits),
                                       ", ".join(missing) or "-"))
            else:
                result["status"] = "timeout"
                result["reason"] = ("综合超时未完成，仍缺：%s（%s）" % (
                    ", ".join(missing) or "-",
                    "全程未见任何综合产物，综合可能根本没起算"
                    if not self._family_hits else "仅见旧产物（早于发送时刻/未重写）"))
            result["threeState"] = three_state_of(result["status"])
            return result
        # waiting reason 分支化：按"未起算 / 只有旧产物 / 已开始写入"给准确原因。
        required_names = sorted(self._required_state)
        if not self._family_hits:
            reason = ("尚未见到综合产物，等待起算/写入；期望 %s"
                      % ", ".join(required_names))
        elif not self._fresh_family_hits:
            reason = ("SYNTHESIS_FILES 只有上一轮旧产物（mtime 早于发送时刻-%.1fs 或未重写），"
                      "等待本轮写入；仍缺 %s"
                      % (self.cfg.clock_grace_seconds, ", ".join(missing) or "-"))
        else:
            reason = ("综合已开始写入，等待齐套；仍缺 %s（已见本轮家族产物：%s）"
                      % (", ".join(missing) or "-",
                         ", ".join(self._fresh_family_hits)))
        result["reason"] = reason
        return result


# ---------------------------------------------------------------------------
# 模块级门面（K2 §2.3 + C13：vault 根经 mup_paths；定位失败 → not_started(notReady)）
# ---------------------------------------------------------------------------
def _not_ready_payload(what):
    return {
        "status": "not_started",
        "threeState": three_state_of("not_started"),
        "notReady": True,
        "reason": ("vault 不可定位（not_ready，%s）：请检查 mup_paths（env MUP_VAULT_ROOT / "
                   "MUP_DATA_DIR / PROD ini [data] path / 盘符）" % what),
        "vaultRoot": "",
        "pollCount": 0,
        "elapsedSeconds": 0.0,
    }


def wait_for_calculation_completion(cfd_ids=(), expected_ids=None, vault_root=None,
                                    cfg=None, on_progress=None):
    """阻塞等待一批 CFD 计算完成。vault 定位失败 → not_started(notReady=True)。

    C13：vault_root 显式传入优先；否则走 mup_paths.vault_root()（WT_Automation
    路径唯一 owner），与接线层同源。
    """
    root = vault_root or mup_paths.vault_root()
    if not root:
        return _not_ready_payload("CFD 完成监听")
    watcher = MupCompletionWatcher(root, cfd_ids=cfd_ids or (),
                                   expected_ids=expected_ids or {}, cfg=cfg or DEFAULT_CONFIG)
    watcher.sent_wall = watcher.clock.wall()
    watcher.capture_baseline()
    result = watcher.wait(on_progress=on_progress)
    result["vaultRoot"] = root
    return result


def wait_for_synthesis_completion(site, case, vault_root=None, cfg=None,
                                  on_progress=None, require_map=False, sent_wall=None,
                                  site_label=None):
    """阻塞等待综合计算完成。内部先 prepare()（基线+发送时刻同点，R1 §C-2）。

    `site` 是产物前缀（真实 Vault = CaseGUID）；人类站点名放 `site_label`
    （解析见 `mup_case_identity.resolve_case_identity`）。
    """
    root = vault_root or mup_paths.vault_root()
    if not root:
        return _not_ready_payload("综合完成监听")
    watcher = SynthesisCompletionWatcher(root, site=site, case=case,
                                         cfg=cfg or DEFAULT_CONFIG,
                                         require_map=require_map,
                                         sent_wall=sent_wall,
                                         site_label=site_label)
    watcher.prepare()
    result = watcher.wait(on_progress=on_progress)
    result["vaultRoot"] = root
    return result