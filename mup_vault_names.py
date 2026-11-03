# encoding: utf-8
"""MUP Vault 文件名匹配与新鲜度判定（单一归一 leaf 模块）。

按 2026-11-02 审计定案 (§5.1 职责表) 与蓝图 v3 (§2.3)，归一以下各处的文件名及新鲜度判定：
1. `mup_completion.py` 中的 parse_logout_id / is_logout_for / is_result_for / is_synthesis_product；
2. `mup_completion_watch.py` 侧同义实现 (:166/:188/:237)；
3. `WT_Launcher._simple_wait_new_cfd_ids` (:6272) 内联的 mtime 新鲜度及基线判定。

消除各处大小写敏感度、None 语义漂移、内联判定重复，削成单一权威实现。

2026-11-02 判据改口（蓝图 §0.1 / §5.5-b）：综合必出内核改为物理 Vault 的
`.wri + .wrp`（`synthesis_vault_mandatory_products`），族识别扩到 `.wrt/.wrtd×7/
timeserie*.dat/双 GUID .wrm`；旧口径 `synthesis_required_products`（xml/txt/matrix）
仅保留兼容，不再由生产 gate 调用。前缀是 `Cases.Id`（解析见 `mup_case_identity`）。
"""
import os
import re

# ---------------------------------------------------------------------------
# 常量定义（与蓝图 §2.3 保持完全一致）
# ---------------------------------------------------------------------------
BIN_DIR = "BIN"
RESULT_DIR = "RESULT_FILES"
SYNTHESIS_DIR = "SYNTHESIS_FILES"
INFO_DIR = "INFORMATION"

LOGOUT_PREFIX = "calcul_log_out_"
LOGOUT_EXT = ".xml"
CANCEL_PREFIX = "calcul_cancel_"
STOPJOB_PREFIX = "stopjob_"
ERROR_LOG = "error.log"
DEBUG_PREFIX = "debug_calcul_"

# 综合产物族扩展名与前缀
#
# 2026-11-02 判据改口（蓝图 §0.1 / §5.5-b）：物理 Vault 的综合必出内核是
# `{CaseGUID}_synthesis_{n}.wri` + `.wrp` 两件套；`{site}_synthesis_{n}.xml/.txt`
# 与 `{site}_matrix_{n}.txt` 在真实 DEMO 的 17 个文件里**零命中**
# （`2026-11-02-demo-export-directfetch.md` §1.1：计算引擎从不调用
# `MTDPath::SynthesisFileXml/SynthesisFileTxt`；matrix.txt 是 GUI 导出副产物）。
# `SYNTH_EXTENSIONS` 保留为 **legacy** 常量（旧口径 5 扩名族），生产 gate 不再消费。
SYNTH_EXTENSIONS = (".wri", ".wrt", ".txt", ".xml", ".wrp")  # legacy 5扩展名族
SYNTH_FAMILY_PREFIX = "_synthesis_"

# 物理 Vault 的真实必出内核（2 件套）与条件/诊断族。
VAULT_MANDATORY_EXTENSIONS = (".wri", ".wrp")
SYNTH_WRTD_KINDS = ("deviation", "inflow", "rewsshear", "shear",
                    "speedup", "turbulence", "veer")
# 诊断族扩展名（只影响"是否已起算"/failed(partial) 的活动判据，永不作必出）：
# .wrt 尾流、.wrtd 风流分布、.dat 时序、.wrm/.pdr 地图。
VAULT_DIAGNOSTIC_EXTENSIONS = (".wrt", ".wrtd", ".dat", ".wrm", ".pdr")

SOLVER_IMAGE_PREFIX = "dassolver_"
SOLVER_IMAGE_SUFFIX = ".exe"

DEFAULT_CLOCK_GRACE = 2.0          # mtime/墙钟容差（秒）
DEFAULT_STABLE_POLLS = 2           # 产物签名稳定轮数
DEFAULT_SILENCE_SECONDS = 60.0     # 默认静默秒数
DEFAULT_TIMEOUT_SECONDS = 3600.0   # 默认超时秒数
DEFAULT_POLL_INTERVAL = 2.0        # 默认轮询间隔秒数


# ---------------------------------------------------------------------------
# CFD 相关文件名匹配与提取
# ---------------------------------------------------------------------------
def parse_logout_id(name):
    """从 `CALCUL_log_out_<id>.xml` 或 DUS 变体 `<id>_<dir>.xml` 提取数字 id。

    单一解析 owner：大小写不敏感，严格要求 .xml 扩展名。
    数字边界：首段数字后必须是结束或 `_`/`-` 分隔符，否则返回 None（cfdId=1 不被 _12 吞）。
    非本族或空名返回 None。
    """
    base = os.path.basename(str(name or "")).lower()
    if not base.startswith(LOGOUT_PREFIX):
        return None
    stem, ext = os.path.splitext(base[len(LOGOUT_PREFIX):])
    if ext.lower() != LOGOUT_EXT or not stem:
        return None
    digits = ""
    for ch in stem:
        if ch.isdigit():
            digits += ch
        else:
            break
    if not digits:
        return None
    rest = stem[len(digits):]
    if rest and rest[0] not in ("_", "-"):
        return None
    return int(digits)


def is_logout_for(name, cfd_id=None):
    """CFD logout 家族判定：精确名 + 数字边界，大小写不敏感。

    - cfd_id is None: 只要是有效的 CALCUL_log_out_<id>.xml 均返回 True（增量发现模式）。
    - cfd_id is not None: 要求解析出来的 cfd_id == int(cfd_id)（精确匹配）。
    """
    parsed = parse_logout_id(name)
    if parsed is None:
        return False
    if cfd_id is None:
        return True
    wanted = str(cfd_id)
    if not wanted.isdigit():
        return False
    return parsed == int(wanted)


def is_result_for(name, cfd_id=None):
    """RESULT_FILES 增量项判定：`{cfdId}_*` 前缀收敛。

    大小写不敏感。
    - cfd_id is None: 任意非空文件名返回 True。
    - cfd_id is not None: 要求文件名以 `<cfd_id>_` 前缀开头。
    """
    base = os.path.basename(str(name or "")).lower()
    if not base:
        return False
    if cfd_id is None:
        return True
    return base.startswith("%s_" % str(cfd_id).lower())


# 别名，保持与 watcher 侧同义调用兼容
is_new_result_for = is_result_for


def is_cancel_marker(name):
    """取消/停止信号：BIN 下 calcul_cancel_*.txt 或 STOPJOB_<cfdId>。"""
    lowered = os.path.basename(str(name or "")).lower()
    return lowered.startswith(CANCEL_PREFIX) or lowered.startswith(STOPJOB_PREFIX)


def is_debug_calcul(name):
    """诊断件判定：debug_calcul_* 只进 diagnostics，永不进完成门条件。"""
    return os.path.basename(str(name or "")).lower().startswith(DEBUG_PREFIX)


def is_error_log(name):
    """错误日志判定：INFORMATION/Error.log。"""
    return os.path.basename(str(name or "")).lower() == ERROR_LOG


# ---------------------------------------------------------------------------
# CFD 新鲜度判定与自动发现（消灭第三套内联）
# ---------------------------------------------------------------------------
def is_fresh_logout(name, mtime, send_wall, baseline=None, clock_grace=DEFAULT_CLOCK_GRACE):
    """判断一个 CALCUL_log_out_<id>.xml 是否新鲜（未被上轮残留污染）。

    权威判据 (§2.3.1):
    ① 文件名符合 logout 命名族且能解析出 cfd_id；
    ② mtime >= send_wall - clock_grace（时间新鲜度下界，防止秒回上轮残留）；
    ③ 文件不在基线中，或者 mtime > 基线 mtime + 0.5（文件被本轮重写）。
    """
    if parse_logout_id(name) is None:
        return False
    if mtime is None:
        return False
    try:
        mt = float(mtime)
    except (ValueError, TypeError):
        return False
    if mt < float(send_wall) - float(clock_grace):
        return False
    if baseline and isinstance(baseline, dict):
        old_val = baseline.get(name)
        if old_val is not None:
            old_mt = old_val[1] if isinstance(old_val, (tuple, list)) else old_val
            try:
                if mt <= float(old_mt) + 0.5:
                    return False
            except (ValueError, TypeError):
                pass
    return True


def discover_fresh_cfd_ids(entries, send_wall, baseline=None,
                           clock_grace=DEFAULT_CLOCK_GRACE, expected_count=None):
    """从 BIN 目录条目中发现新鲜 cfdId。

    entries: dict {name: mtime 或 (size, mtime)} 或 list of (name, mtime)
    返回: (picked, all_ids)
      - picked: 按 mtime 降序排序、去重、至多 expected_count 个 cfdId 列表；
      - all_ids: 本轮发现的所有去重新鲜 cfdId（升序排序）。
    """
    if isinstance(entries, dict):
        item_list = [(name, val[1] if isinstance(val, (tuple, list)) else val)
                     for name, val in entries.items()]
    else:
        item_list = list(entries or [])

    fresh = []
    for name, mtime in item_list:
        if is_fresh_logout(name, mtime, send_wall, baseline=baseline, clock_grace=clock_grace):
            cfd_id = parse_logout_id(name)
            if cfd_id is not None:
                fresh.append((float(mtime), cfd_id))

    fresh.sort(reverse=True)
    picked = []
    for _mt, cid in fresh:
        if cid not in picked:
            picked.append(cid)
        if expected_count is not None and len(picked) >= int(expected_count):
            break
    all_ids = sorted({cid for _mt, cid in fresh})
    return picked, all_ids


# ---------------------------------------------------------------------------
# 综合产物匹配
#   · 生产口径（真实 Vault）：mandatory = .wri + .wrp；条件族只进 diagnostics。
#   · 旧口径（xml/txt/matrix）：保留 `synthesis_required_products` 供旧测试/旧环境，
#     **不再由生产 gate 调用**（蓝图 §5.5-b-1）。
# ---------------------------------------------------------------------------
def synthesis_vault_mandatory_products(case_prefix, case):
    """物理 Vault 的综合必出内核（2 件套，2026-11-02 真实 DEMO 唯一 100% 命中）：
    `{case_prefix}_synthesis_{case}.wri`、`{case_prefix}_synthesis_{case}.wrp`。

    `case_prefix` 是 `Cases.Id`（GUID），不是人类可读站点名；解析见
    `mup_case_identity.resolve_case_identity`。
    """
    case_prefix, case = str(case_prefix), str(case)
    return tuple("%s_synthesis_%s%s" % (case_prefix, case, ext)
                 for ext in VAULT_MANDATORY_EXTENSIONS)


def synthesis_required_products(site, case):
    """**legacy** 旧口径必出（§5.2-2 静态模板，真实 Vault 零命中）：
    {site}_synthesis_{case}.xml、{site}_synthesis_{case}.txt、{site}_matrix_{case}.txt。

    生产 gate 已改用 `synthesis_vault_mandatory_products`；本函数只服务旧测试与
    旧 mock vault（`{site}_` 前缀的假产物），不要在新代码里消费。
    """
    site, case = str(site), str(case)
    return (
        "%s_synthesis_%s.xml" % (site, case),
        "%s_synthesis_%s.txt" % (site, case),
        "%s_matrix_%s.txt" % (site, case),
    )


def synthesis_optional_products(case_prefix, case):
    """综合**诊断面**产物（真实 DEMO 的条件族 + 旧 mock 映射族）：

    `.wrt` 尾流、7 个 `.wrtd`（deviation/inflow/rewsshear/shear/speedup/
    turbulence/veer）、`timeserie{,park}.dat`、`_map_.wrm/.pdr`、`_losswake_.txt`。

    只参与"是否已起算"的活动判定与 diagnostics，永不作必出
    （`require_map` 保持 False，蓝图 §5.5）。
    """
    case_prefix, case = str(case_prefix), str(case)
    out = [
        "%s_synthesis_%s.wrt" % (case_prefix, case),
    ]
    out.extend("%s_synthesis_%s_%s.wrtd" % (case_prefix, kind, case)
               for kind in SYNTH_WRTD_KINDS)
    out.extend((
        "%s_timeserie_%s.dat" % (case_prefix, case),
        "%s_timeseriepark_%s.dat" % (case_prefix, case),
        "%s_map_%s.wrm" % (case_prefix, case),
        "%s_map_%s.pdr" % (case_prefix, case),
        "%s_losswake_%s.txt" % (case_prefix, case),
    ))
    return tuple(out)


def synthesis_map_product(case_prefix, case):
    """综合地图产物（`require_map=True` 时并入必出）：`{case_prefix}_map_{case}.wrm`。"""
    return "%s_map_%s.wrm" % (str(case_prefix), str(case))


# 解析族（GAP-1/2/3，`2026-11-02-demo-export-directfetch.md` §6.1）：
#   ① 双 GUID 地图 `{CaseGUID}_{ElementGUID}_{n}.wrm|.pdr`
#   ② 风流分布 `{prefix}_synthesis_{kind}_{n}.wrtd`（7 kind）
#   ③ 时序 `{prefix}_timeserie{,park}_{n}.dat`
#   ④ 主族 `{prefix}_{synthesis|matrix|losswake|map}_{n}.{ext}`
_GUID = (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
         r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

_SYNTH_PARSER_RULES = (
    (re.compile(r"^(?P<site>%s)_(?P<elem>%s)_(?P<n>\d+)(?P<ext>\.wrm|\.pdr)$"
                % (_GUID, _GUID), re.IGNORECASE),
     "map"),
    (re.compile(r"^(?P<site>.+?)_synthesis_(?P<subkind>%s)_(?P<n>\d+)(?P<ext>\.wrtd)$"
                % "|".join(SYNTH_WRTD_KINDS), re.IGNORECASE),
     "wrtd"),
    (re.compile(r"^(?P<site>.+?)_timeserie(?:park)?_(?P<n>\d+)(?P<ext>\.dat)$",
                re.IGNORECASE),
     "timeserie"),
    (re.compile(r"^(?P<site>.+?)_(?P<kind>synthesis|matrix|losswake|map)_(?P<n>\d+)"
                r"(?P<ext>\.[A-Za-z0-9]+)$", re.IGNORECASE),
     None),
)


def parse_synthesis_filename(name, site=None):
    """解析综合产物文件名（多族正则，2026-11-02 改口后覆盖真实 Vault 全族）。

    返回 `(prefix, n_int, kind_str, ext_str)` 或 None。
      · kind `synthesis` / `matrix` / `losswake` / `map` / `wrtd` / `timeserie`；
      · 双 GUID 地图 `{CaseGUID}_{ElementGUID}_{n}.wrm` 的 prefix 取 **首段 CaseGUID**
        （ElementGUID 属元素身份，不参与综合号/站点匹配）；
      · prefix 是产物前缀（真实 Vault 为 CaseGUID，mock 为站点名）。
    若指定 `site`，则严格要求 prefix 与之相同（大小写不敏感）。
    """
    base = os.path.basename(str(name or ""))
    for pattern, fixed_kind in _SYNTH_PARSER_RULES:
        match = pattern.match(base)
        if not match:
            continue
        matched = match.group("site")
        if site and matched.lower() != str(site).lower():
            return None
        kind = fixed_kind or match.group("kind").lower()
        return (matched, int(match.group("n")), kind, match.group("ext").lower())
    return None


def is_vault_synthesis_product(name, case_prefix, case):
    """**生产**综合族判定：真实 Vault 里属于 (case_prefix, case) 的产物。

    族 = 必出 `.wri/.wrp` + 条件 `/.wrt/ .wrtd/ .dat/ .wrm/ .pdr`；
    **不含** legacy 的 `.xml/.txt/matrix.txt`（真实引擎从不写它们，
    把这些当"已起算"会把假产物洗成 partial，蓝图 §0.1 S4）。
    """
    parsed = parse_synthesis_filename(name, site=case_prefix)
    if parsed is None:
        return False
    _prefix, n, kind, ext = parsed
    if str(case) != "" and n != int(case):
        return False
    if ext in VAULT_MANDATORY_EXTENSIONS or ext in VAULT_DIAGNOSTIC_EXTENSIONS:
        return True
    # 尾流损失是真实 Vault 产物，但扩展名与 legacy `.txt` 撞车：只按 kind 收口。
    return kind == "losswake"


def is_synthesis_product(name, site, case=None):
    """综合产物家族判定（统一大小写/None 语义）。

    - case is None: 兼容旧 `mup_completion` 契约，仅匹配 `{site}_synthesis_{n}.{ext}`
      （含 legacy `.xml/.txt`），成功返回 (n, ext.lower())，失败返回 None。
    - case is not None: **生产契约**，等价 `is_vault_synthesis_product`
      （多族正则 + CaseGUID 前缀 + 综合号精确匹配），返回 bool。
    """
    if case is None:
        parsed = parse_synthesis_filename(name, site=site)
        if parsed and parsed[2] == "synthesis":
            return parsed[1], parsed[3]
        return None
    return is_vault_synthesis_product(name, site, case)
