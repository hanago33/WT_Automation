# encoding: utf-8
"""综合产物身份解析 leaf：人类站点名 → Vault 产物前缀（CaseGUID）。

为什么单独成 leaf（`2026-11-02-input-automation-blueprint.md` §5.5-b-5）：
  · 真实 DEMO 证据（`2026-11-02-demo-export-directfetch.md` §1）证明 SYNTHESIS_FILES
    里的产物前缀是 `Cases.Id`（GUID），不是人类可读站点名；
  · 完成门需要"发送前解析出前缀"，导出器需要同一份解析结果；
  · 若 `mup_completion` 直接 import `mup_vault_export`，则
    export → completion → export 成环（export 已 import completion.is_result_for）。
  故抽本 leaf：**纯 stdlib、无 subprocess、不 import 任何同仓模块**，
  DB 查询由调用方以 `query_case_id(site) -> str` 注入（生产为 sqlcmd/LocalDB，
  只允许出现在接线层）。

解析四级链（顺序不可换，命中即返回）：
  1. `guid`          —— 入参本身已是 36 位标准 GUID（幂等直通）；
  2. `mock-prefix`   —— Vault 下存在 `{site}_` 前缀文件（mock/旧口径 vault，保留原串）；
  3. `db`            —— 调用方注入的 `query_case_id(site)` 返回 GUID；
  4. `vault-single-guid` —— Vault 目录里只有唯一一个 Case GUID 前缀（双 GUID 地图
     文件只取首段，不把 ElementGUID 当 Case）；
  5. `unresolved`    —— 全部落空：原样返回 site，并由调用方照实报"未解析"，
     **不得**把它当保证（多站同 vault 是 Unknown，蓝图 §8 U-8）。

失败语义：任何一步异常都只降级到下一步，不抛出；`CaseIdentity.source` 保留
归因字符串，供日志/对账把"猜的"和"查到的"分开。
"""
import os
import re
from collections import namedtuple

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
CASE_GUID_PREFIX = "_"
SYNTHESIS_DIR = "SYNTHESIS_FILES"
RESULT_DIR = "RESULT_FILES"

GUID_PATTERN = (r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
CASE_GUID_RE = re.compile(r"^%s$" % GUID_PATTERN)
# 目录内任意文件的 GUID 前缀（`{CaseGUID}_...`）。双 GUID 地图文件只取首段。
_GUID_PREFIX_RE = re.compile(r"^(%s)_" % GUID_PATTERN)

# source 词表（报告/日志用；未知来源一律 unresolved，不臆断）
SOURCE_GUID = "guid"
SOURCE_MOCK_PREFIX = "mock-prefix"
SOURCE_DB = "db"
SOURCE_VAULT_SINGLE_GUID = "vault-single-guid"
SOURCE_UNRESOLVED = "unresolved"
SOURCE_EMPTY = "empty"

CaseIdentity = namedtuple("CaseIdentity", "site_label case_prefix source candidates")


def is_case_guid(value):
    """是否 36 位标准 GUID 字符串（大小写不敏感，不做格式修复）。"""
    return bool(CASE_GUID_RE.match(str(value or "").strip()))


def _listdir(path):
    """目录列举（容错）：不存在/不可读 → 空元组，不抛。"""
    try:
        return tuple(os.listdir(path))
    except OSError:
        return ()


def _has_site_prefixed_files(site, vault_root, listdir):
    """Vault 下是否有 `{site}_...` 文件（mock/旧口径 vault 的唯一判据）。

    只看 `listdir` 的结果：注入的目录视图就是唯一事实源，不再用 `os.path.isdir`
    二次确认（那会让注入在虚拟根上静默失效，也让"真实 FS + 注入列表"混源）。
    """
    if not vault_root:
        return False
    needle = str(site).lower() + CASE_GUID_PREFIX
    for sub in (SYNTHESIS_DIR, RESULT_DIR):
        for name in listdir(os.path.join(vault_root, sub)):
            if name.lower().startswith(needle):
                return True
    return False


def _vault_case_guids(vault_root, listdir):
    """收集 Vault 里出现过的 Case GUID 前缀集合（双 GUID 只取首段）。"""
    found = set()
    if not vault_root:
        return found
    for sub in (SYNTHESIS_DIR, RESULT_DIR):
        for name in listdir(os.path.join(vault_root, sub)):
            match = _GUID_PREFIX_RE.match(name)
            if match:
                found.add(match.group(1))
    return found


def resolve_case_identity(site, vault_root="", query_case_id=None, listdir=None):
    """解析站点身份，返回 `CaseIdentity(site_label, case_prefix, source, candidates)`。

    参数：
      site            —— 人类可读站点名（如 `DEMO`），或已是 GUID；
      vault_root      —— Vault 根（空则该两步跳过，不失败）；
      query_case_id   —— 可选注入：`callable(site_label:str) -> GUID 或 ""`（LocalDB）；
      listdir         —— 可选注入：`callable(dir:str) -> Iterable[str]`，默认 `os.listdir`。

    **注入边界（DB 只在第 3 级被调用，且只调一次）**：
      · GUID 直通、`{site}_` mock、空站点名三路都**不会**触碰数据库；
      · 入参永远是人类标签（不会把 GUID 或文件名喂进去），返回必须是标准 GUID 字符串，
        其余（`""`/None/报错文本/多行）一律按"没查到"处理，由本模块降级到启发式；
      · 异常不外泄：本模块捕获后按"没查到"继续（发送路径不得因 DB 面中断）；
      · **超时与重试归注入方**（本模块不能 spawn、不能阻塞取消）；该调用位于
        发送前路径上，注入方应自行限制在秒级；
      · 诊断（DB 错误码、命中条数、多 Case 告警）由注入方在闭包里自行落账——
        本模块只回 `source/candidates`，不吞也不编造错误信息。
    """
    label = str(site or "").strip()
    if not label:
        return CaseIdentity("", "", SOURCE_EMPTY, ())
    listdir = listdir or _listdir

    if is_case_guid(label):
        return CaseIdentity(label, label, SOURCE_GUID, (label,))

    vault_root = str(vault_root or "")
    if _has_site_prefixed_files(label, vault_root, listdir):
        return CaseIdentity(label, label, SOURCE_MOCK_PREFIX, (label,))

    if callable(query_case_id):
        try:
            hit = str(query_case_id(label) or "").strip()
        except Exception:  # noqa: BLE001 —— DB 面失败只降级，不得中断发送
            hit = ""
        if is_case_guid(hit):
            return CaseIdentity(label, hit, SOURCE_DB, (hit,))

    guids = _vault_case_guids(vault_root, listdir)
    if len(guids) == 1:
        only = next(iter(guids))
        return CaseIdentity(label, only, SOURCE_VAULT_SINGLE_GUID, (only,))

    # unresolved：保留原串（调用方 fail-closed 或告警），候选集只作诊断。
    return CaseIdentity(label, label, SOURCE_UNRESOLVED, tuple(sorted(guids)))
