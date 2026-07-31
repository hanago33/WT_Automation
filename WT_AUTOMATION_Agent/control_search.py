# encoding: utf-8
"""control_search —— 在 control_maps 中语义检索真实存在的控件。

为 Agent 提供"资产辅助构建"能力：当用户要用自然语言描述一个控件时，
先用本模块在控件库里找到最匹配的控件，返回其 targetValue（即可填进
add_step.control_id 的真实标识），从而显著减少"点击了不存在的控件"这类失败。

v3 设计要点（回应"不只看主库" + "树结构更易查询"）：
1. **源库优先，master 仅做补全层**：以 control_maps/library（手工确认的定义）
   与 standard 标准目录为"权威基座"，绝不被 master 合并覆盖/丢弃；master
   （如 总控件信息.json）只作为字段补全层，回填 uiPath / labelText /
   optionValues / qualityTier / locatorScore 等。→ 即使 master 合并时丢控件，
   源库中的原始定义也永远保留（实测 master 曾丢 17 个 library 控件）。
2. **uiPath 树索引**：基于 "Window > View > Container > Control" 分层构建树，
   支持在指定祖先（窗口/视图）范围内查询 find_within()，以及输出应用结构
   大纲 tree_summary()，让"在某视图里找某控件"更精准、更易用。
3. 评分利用采集器新字段：labelText / optionValues / automationId / uiPath /
   qualityTier / locatorScore。
4. "动作↔控件类型"对标：find_controls(query, action=...) 按动作语义加权。
5. 精确反查 API：resolve_control / best_control_for_step，回填步骤控件信息。

零外部依赖：仅用标准库做关键词/子串/中文二元组匹配。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

# 项目根目录（WT_AUTOMATION_Agent 的上一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STD_DIR = os.path.join(_PROJECT_ROOT, "control_maps", "standard")
_STD_CATALOG = os.path.join(_STD_DIR, "standard_control_catalog.json")
_LIB_DIR = os.path.join(_PROJECT_ROOT, "control_maps", "library")

# 中文控件类型词 → 标准化 controlType 关键词
_TYPE_HINTS = {
    "按钮": "Button",
    "下拉": "ComboBox",
    "组合框": "ComboBox",
    "列表": "ListBox",
    "复选": "CheckBox",
    "勾选": "CheckBox",
    "单选": "RadioButton",
    "选项": "RadioButton",
    "选项卡": "TabItem",
    "标签": "TabItem",
    "输入框": "Edit",
    "文本框": "Edit",
    "文本": "Text",
    "菜单": "MenuItem",
    "树": "Tree",
    "表格": "Grid",
    "网格": "Grid",
    "滚动": "ScrollBar",
    "滑块": "Slider",
    "进度": "ProgressBar",
    "窗口": "Window",
}

# 动作 → 优先匹配的控件类型（小写）。与 wt_action_schema.ACTION_SCHEMAS 对齐。
_ACTION_TYPE_PREFS: dict[str, set[str]] = {
    "click": {"button", "tabitem", "tab", "menuitem", "listitem", "checkbox",
              "radiobutton", "splitbutton", "hyperlink", "image", "treeitem", "pane"},
    "double_click": {"button", "listitem", "treeitem", "dataitem", "image", "pane"},
    "right_click": {"listitem", "treeitem", "dataitem", "pane", "tree", "list"},
    "type_text": {"edit", "document", "combobox"},
    "type_text_relative": {"edit", "document", "combobox", "text", "textblock"},
    "set_combobox": {"combobox"},
    "select_dropdown_item_runtime": {"combobox", "listbox", "list", "listitem"},
    "menu_select": {"menuitem", "menu", "menubar"},
    "wait_for_control": set(),   # 任意类型
    "click_relative_region": {"text", "textblock", "edit", "combobox", "button"},
    "click_relative_anchor": {"text", "textblock", "edit", "combobox", "button"},
}

# 与"输入/选择"动作明显不匹配的纯展示类型（用于降权）
_DISPLAY_ONLY_TYPES = {"text", "textblock", "static", "label", "image", "separator"}
_INPUT_ACTIONS = {"type_text", "set_combobox", "select_dropdown_item_runtime"}

_CACHE: list[dict[str, Any]] | None = None
_TREE_CACHE: "_TreeNode | None" = None


def _norm_record(
    *,
    name: str = "",
    control_type: str = "",
    class_name: str = "",
    target_method: str = "",
    target_value: str = "",
    automation_id: str = "",
    ui_path: str = "",
    label_text: str = "",
    option_values: list[str] | None = None,
    quality_tier: str = "",
    locator_score: float = 0.0,
    authority: str = "",
    occurrences: int = 0,
    source: str = "",
    notes: str = "",
    window_title: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "controlType": control_type,
        "className": class_name,
        "targetMethod": target_method,
        "targetValue": target_value,
        "automationId": automation_id,
        "uiPath": ui_path,
        "labelText": label_text,
        "optionValues": [str(v) for v in (option_values or []) if str(v).strip()],
        "qualityTier": quality_tier,
        "locatorScore": float(locator_score or 0),
        "authority": authority,
        "occurrences": occurrences,
        "source": source,
        "notes": notes,
        "windowTitle": window_title,
    }


def _load_master_overlay(controls: list[dict[str, Any]]) -> None:
    """加载 standard/ 下含 flatControls 的合并主库，作为"字段补全层"（非权威基座）。

    仅用于给源库控件回填 uiPath / labelText / optionValues / qualityTier /
    locatorScore 等更丰富的字段；任何源库中已存在的控件都不会被它覆盖或丢弃。
    """
    if not os.path.isdir(_STD_DIR):
        return
    for fn in sorted(os.listdir(_STD_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(_STD_DIR, fn)
        if os.path.abspath(path) == os.path.abspath(_STD_CATALOG):
            continue  # 标准目录单独走 _load_standard_catalog
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(raw, dict):
            continue
        flat = raw.get("flatControls")
        if not isinstance(flat, list):
            continue
        for c in flat:
            if not isinstance(c, dict):
                continue
            name = str(c.get("name", "")).strip() \
                or str(c.get("labelText", "")).strip() \
                or str(c.get("suggestedControlName", "")).strip() \
                or str(c.get("displayName", "")).strip()
            target_value = str(c.get("recommendedTargetValue", "")).strip()
            if not target_value and not name:
                continue
            controls.append(_norm_record(
                name=name,
                control_type=str(c.get("controlType", "")),
                class_name=str(c.get("className", "")),
                target_method=str(c.get("recommendedTargetMethod", "")),
                target_value=target_value,
                automation_id=str(c.get("automationId", "")),
                ui_path=str(c.get("uiPath", "")),
                label_text=str(c.get("labelText", "")),
                option_values=c.get("optionValues") if isinstance(c.get("optionValues"), list) else None,
                quality_tier=str(c.get("qualityTier", "")),
                locator_score=c.get("locatorScore", 0) or 0,
                authority="master",
                occurrences=1,
                source="master:" + fn,
                notes=str(c.get("qualityReason", "")),
                window_title=str(c.get("windowTitle", "")),
            ))


def _load_standard_catalog(controls: list[dict[str, Any]]) -> None:
    """加载标准控件目录（按功能窗口分组、含出现次数统计）。"""
    if not os.path.exists(_STD_CATALOG):
        return
    try:
        raw = json.load(open(_STD_CATALOG, "r", encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    groups = raw if isinstance(raw, list) else raw.get("groups", [])
    for grp in groups:
        if not isinstance(grp, dict):
            continue
        window = str(grp.get("windowTitle", "") or grp.get("frameworkId", ""))
        group_controls = grp.get("controls") if isinstance(grp.get("controls"), list) else [grp]
        for c in group_controls:
            if not isinstance(c, dict):
                continue
            controls.append(_norm_record(
                name=str(c.get("name", "")),
                control_type=str(c.get("controlType", "")),
                class_name=str(c.get("className", "")),
                target_method=str(c.get("targetMethod", "")),
                target_value=str(c.get("targetValue", "")),
                authority=str(c.get("authority", "")),
                occurrences=int(c.get("occurrences", 0) or 0),
                source="standard_catalog",
                notes=("窗口: " + window) if window else "",
                window_title=window,
            ))


def _load_library_dir(controls: list[dict[str, Any]]) -> None:
    """加载 library/*.json 控件库（手工确认过的定义，作为权威基座之一）。"""
    if not os.path.isdir(_LIB_DIR):
        return
    for fn in sorted(os.listdir(_LIB_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(_LIB_DIR, fn), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        for c in data.get("controlDefinitions", []):
            if not isinstance(c, dict):
                continue
            controls.append(_norm_record(
                name=str(c.get("name", "")),
                control_type=str(c.get("controlType", "")),
                class_name=str(c.get("className", "")),
                target_method=str(c.get("targetMethod", "")),
                target_value=str(c.get("targetValue", "")),
                automation_id=str(c.get("automationId", "")),
                ui_path=str(c.get("uiPath", "")),
                label_text=str(c.get("labelText", "")),
                option_values=c.get("optionValues") if isinstance(c.get("optionValues"), list) else None,
                quality_tier=str(c.get("_qualityTier", "") or c.get("qualityTier", "")),
                authority=str(c.get("authority", "")),
                occurrences=1,
                source="library:" + fn,
                notes=str(c.get("notes", "")),
                window_title=str(c.get("windowTitle", "")),
            ))


def _control_key(c: dict[str, Any]) -> tuple:
    """稳定的去重/对齐键：优先 targetValue，其次 automationId，再次 name+窗口。"""
    tv = str(c.get("targetValue", "") or "").strip().lower()
    if tv:
        return ("tv", tv)
    aid = str(c.get("automationId", "") or "").strip().lower()
    if aid:
        return ("aid", aid)
    nm = str(c.get("name", "") or "").strip().lower()
    if nm:
        return ("nm", nm, str(c.get("windowTitle", "") or "").strip().lower())
    return ("id", id(c))


def _fill_gaps(dest: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    """把 src 中 dest 缺失的字段补全（dest 已有值不被覆盖）。"""
    for fld in ("uiPath", "labelText", "optionValues", "qualityTier", "locatorScore",
                "automationId", "name", "targetMethod", "targetValue", "className",
                "windowTitle", "authority", "notes"):
        if not dest.get(fld) and src.get(fld):
            dest[fld] = src[fld]
    return dest


def _load() -> list[dict[str, Any]]:
    """加载控件库：源库（library + 标准目录）为权威基座，master 仅做字段补全。

    合并规则（保证"不丢控件"）：
    1. 源库控件先入基座 dict（library / 标准目录间按 key 合并，首入者优先、补缺失字段）；
    2. master 作为补全层：若 control_key 命中基座控件，则只回填缺失字段；
    3. master 独有的控件（源库没有）也保留进最终列表。
    → 源库中任何控件都不会被 master 的合并结果丢弃或覆盖。
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    source: list[dict[str, Any]] = []
    _load_library_dir(source)           # 权威基座：手工确认定义
    _load_standard_catalog(source)      # 权威基座：标准目录（含出现次数）

    master: list[dict[str, Any]] = []
    _load_master_overlay(master)        # 补全层：字段最全

    # 1) 基座：源库控件合并（首入优先、补缺失）
    base: dict[tuple, dict[str, Any]] = {}
    for c in source:
        k = _control_key(c)
        if k in base:
            _fill_gaps(base[k], c)
        else:
            base[k] = c

    # 2) master 补全基座缺失字段；3) master 独有则保留
    final: list[dict[str, Any]] = []
    leftover: dict[tuple, dict[str, Any]] = {}
    for c in master:
        leftover[_control_key(c)] = c
    for k, rec in base.items():
        m = leftover.pop(k, None)
        if m is not None:
            _fill_gaps(rec, m)          # 只补缺失，不覆盖源库定义
        final.append(rec)
    final.extend(leftover.values())     # master 独有控件

    _CACHE = final
    return _CACHE


def reload() -> None:
    """清空缓存，下次检索时重新加载控件库（采集完新控件后调用）。"""
    global _CACHE, _TREE_CACHE
    _CACHE = None
    _TREE_CACHE = None


# ——————————————————————————————————————————————————————————————————————
# uiPath 树索引：基于 "Window > View > Container > Control" 分层构建
# ——————————————————————————————————————————————————————————————————————
class _TreeNode:
    __slots__ = ("name", "children", "controls")

    def __init__(self, name: str):
        self.name = name
        self.children: dict[str, "_TreeNode"] = {}
        self.controls: list[dict[str, Any]] = []


def _split_uipath(up: str) -> list[str]:
    """把 uiPath 按分隔符拆成有序层级片段。"""
    if not up:
        return []
    return [s.strip() for s in str(up).replace(">", "|").split("|") if s.strip()]


def _build_tree() -> _TreeNode:
    """根据所有控件的 uiPath 构建层级树（缓存）。"""
    global _TREE_CACHE
    if _TREE_CACHE is not None:
        return _TREE_CACHE
    root = _TreeNode("")
    for c in _load():
        segs = _split_uipath(c.get("uiPath", ""))
        node = root
        for seg in segs:
            node = node.children.setdefault(seg, _TreeNode(seg))
        if c not in node.controls:
            node.controls.append(c)
    _TREE_CACHE = root
    return root


def tree_summary(max_depth: int = 3, max_children: int = 30) -> str:
    """返回应用的层级结构大纲（窗口/视图/容器及各级控件数），便于 LLM 先定位
    视图再精确查询。可用作 control_tree 工具的输出。"""
    def _count(node: _TreeNode) -> int:
        n = len(node.controls)
        for ch in node.children.values():
            n += _count(ch)
        return n

    def _walk(node: _TreeNode, depth: int, lines: list[str]) -> None:
        if depth > max_depth:
            return
        kids = sorted(node.children.values(), key=lambda x: -_count(x))
        for ch in kids[:max_children]:
            cnt = _count(ch)
            leaf = len(ch.controls)
            mark = f"  [{leaf} 叶控件 / 共 {cnt}]" if leaf else f"  [共 {cnt}]"
            lines.append("  " * depth + "• " + ch.name + mark)
            _walk(ch, depth + 1, lines)

    lines = ["应用控件树结构（按 uiPath 层级）："]
    _walk(_build_tree(), 1, lines)
    return "\n".join(lines)


def ancestors_of(control_id: str) -> list[str] | None:
    """返回某控件在 uiPath 树中的祖先路径（含自身叶节点名）。"""
    rec = resolve_control(control_id)
    if rec is None:
        return None
    return _split_uipath(rec.get("uiPath", ""))


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _tokens(s: str) -> list[str]:
    """按驼峰/分隔符切出 >=3 长度的英文 token。"""
    parts = re.split(r"[_,.\-/> ]+", s)
    out: list[str] = []
    for p in parts:
        out.extend(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", p))
    return [t.lower() for t in out if len(t) >= 3]


def _score(c: dict[str, Any], query: str, action: str = "") -> float:
    """给单个控件打分：子串、token 重叠、中文二元组、标签文本、下拉选项、
    automationId/uiPath、类型提示、动作对标、质量分级、出现次数。"""
    ql = query.lower()
    q = query  # 保留原始（含中文）
    score = 0.0

    tv = str(c.get("targetValue", "")).lower()
    ct = str(c.get("controlType", "")).lower()
    cn = str(c.get("name", ""))
    lt = str(c.get("labelText", ""))
    aid = str(c.get("automationId", "")).lower()
    up = str(c.get("uiPath", "")).lower()
    q_bi = _bigrams(q)

    # 1) targetValue 子串 / token 重叠（英文标识片段，如 GeographicalData）
    if tv:
        if tv in ql or ql in tv:
            score += 4.0
        for tok in _tokens(tv):
            if tok in ql:
                score += 2.0

    # 2) automationId token 重叠（采集器主推定位字段）
    if aid and aid != tv:
        if aid in ql or ql in aid:
            score += 4.0
        else:
            for tok in _tokens(aid):
                if tok in ql:
                    score += 1.5

    # 3) controlType 字面命中
    if ct and ct in ql:
        score += 3.0

    # 4) 中文类型提示映射
    for kw, ctype in _TYPE_HINTS.items():
        if kw in q:
            if ctype.lower() in ct:
                score += 3.0
            if kw in cn:
                score += 1.0

    # 5) 控件中文名子串 / 二元组重叠
    if cn:
        cnl = cn.lower()
        if cnl and cnl in ql:
            score += 5.0
        if ql and ql in cnl:
            score += 4.0
        score += len(q_bi & _bigrams(cn)) * 1.5

    # 6) 标签关联文本（labelText，采集器回填的最近标签）
    if lt and lt != cn:
        ltl = lt.lower()
        if ltl in ql or (ql and ql in ltl):
            score += 4.0
        score += len(q_bi & _bigrams(lt)) * 1.2

    # 7) 下拉选项值命中（如查询"选择均匀风"命中含"均匀"选项的下拉框）
    for opt in c.get("optionValues", [])[:50]:
        ol = str(opt).strip()
        if len(ol) >= 2 and (ol in q or ol.lower() in ql):
            score += 4.0
            break

    # 8) uiPath 片段命中（弱信号）
    if up:
        hit = sum(1 for tok in _tokens(up) if tok in ql)
        score += min(hit, 3) * 0.8

    # —— 相关性闸门：以上文本信号（1-8）为 0 时，直接判不相关，
    #    防止仅靠动作对标/质量加分把无关控件顶过阈值 ——
    if score <= 0:
        return 0.0

    # 9) 动作 ↔ 控件类型对标
    if action:
        prefs = _ACTION_TYPE_PREFS.get(action)
        if prefs:
            if ct in prefs:
                score += 3.0
            elif action in _INPUT_ACTIONS and ct in _DISPLAY_ONLY_TYPES:
                score -= 3.0

    # 10) 质量分级 / 定位评分加权
    tier = str(c.get("qualityTier", ""))
    if "推荐" in tier or "high" in tier.lower():
        score += 1.5
    elif "忽略" in tier or "low" in tier.lower():
        score -= 2.0
    score += min(float(c.get("locatorScore", 0) or 0), 100.0) / 100.0

    # 11) 出现次数作为轻微优先级（同分时靠前）
    score += min(c.get("occurrences", 0) or 0, 10) * 0.1

    return score


def find_controls(
    query: str,
    top_k: int = 5,
    action: str = "",
    within: "str | list[str] | None" = None,
) -> list[dict[str, Any]]:
    """按自然语言查询检索控件，返回按相关度排序的候选列表。

    action 可选：传入步骤动作名（如 set_combobox / type_text / click），
    会按"动作↔控件类型"对标加/降权，优先返回能执行该动作的控件。
    within 可选：限定在指定祖先（窗口/视图名，可传列表做 OR）的 uiPath 子树内
    检索，如 within="MUPWindTurbineTypeMainView" 只在风机类型视图里找。
    """
    if not query or not query.strip():
        return []
    pool = _scope_pool(within)
    return _rank(pool, query.strip(), action=action.strip(), top_k=top_k)


def find_within(
    ancestor: str,
    query: str = "",
    action: str = "",
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """在指定祖先（窗口/视图）的 uiPath 子树内检索控件。

    - 传 query：在该子树内按相关度检索；
    - 不传 query：直接浏览该子树内的控件（按出现次数排序），用于"列出某视图下控件"。
    """
    return _rank(_scope_pool(ancestor), query.strip() if query else "", action=action, top_k=top_k)


def _scope_pool(within: "str | list[str] | None") -> list[dict[str, Any]]:
    """返回落在 within 指定祖先子树内的控件子集（within=None 返回全部）。"""
    if not within:
        return _load()
    ancs = [a.strip().lower() for a in (within if isinstance(within, list) else [within]) if str(a).strip()]
    if not ancs:
        return _load()
    return [
        c for c in _load()
        if any(a in str(c.get("uiPath", "")).lower() for a in ancs)
    ]


def _rank(
    pool: list[dict[str, Any]],
    query: str,
    action: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """对给定控件池按 query 打分排序，返回去重后的候选列表。"""
    if not query:
        # 浏览模式：按出现次数降序直接列出
        scored = [(c.get("occurrences", 0) or 0, c) for c in pool]
        scored.sort(key=lambda x: -x[0])
    else:
        scored = [
            (s, c) for c in pool
            if (s := _score(c, query, action=action)) > 0
        ]
        scored.sort(key=lambda x: (-x[0], -(x[1].get("occurrences", 0) or 0)))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s, c in scored:
        key = c.get("targetValue") or c.get("name") or str(id(c))
        if key in seen:
            continue
        seen.add(key)
        item = dict(c)
        item["score"] = round(s, 2) if query else None
        out.append(item)
        if len(out) >= max(1, top_k):
            break
    return out


def resolve_control(control_id: str) -> dict[str, Any] | None:
    """按 control_id 精确反查控件库记录（用于步骤生成时回填真实控件信息）。

    依次尝试：targetValue 全等 → targetValue 首段（automation_id 部分）全等
    → automationId 全等。命中多条时优先 qualityTier 更好、locatorScore 更高者。
    """
    cid = (control_id or "").strip()
    if not cid:
        return None
    cidl = cid.lower()
    controls = _load()

    def _rank(c: dict[str, Any]) -> tuple:
        tier = str(c.get("qualityTier", ""))
        tier_rank = 0 if ("推荐" in tier or "high" in tier.lower()) else (2 if "忽略" in tier else 1)
        return (tier_rank, -float(c.get("locatorScore", 0) or 0), -(c.get("occurrences", 0) or 0))

    exact = [c for c in controls if str(c.get("targetValue", "")).strip().lower() == cidl]
    if not exact:
        # targetValue 常为 "AutomationId,ControlType" 复合格式，容忍只传首段
        exact = [
            c for c in controls
            if str(c.get("targetValue", "")).split(",")[0].strip().lower() == cidl
        ]
    if not exact:
        exact = [c for c in controls if str(c.get("automationId", "")).strip().lower() == cidl]
    if not exact:
        return None
    exact.sort(key=_rank)
    return dict(exact[0])


def best_control_for_step(
    action: str, control_id: str, hint: str = ""
) -> tuple[dict[str, Any] | None, bool]:
    """为一个生成的步骤找到最匹配的真实控件。

    返回 (record, exact)：
    - exact=True：control_id 在库中精确命中，record 为该记录；
    - exact=False：未精确命中，record 为模糊检索的最优候选（分数不足则为 None）。
    """
    rec = resolve_control(control_id)
    if rec is not None:
        return rec, True
    query = " ".join(x for x in (control_id, hint) if x and x.strip())
    if not query:
        return None, False
    cands = find_controls(query, top_k=1, action=action)
    if cands and cands[0].get("score", 0) >= 6.0:
        return cands[0], False
    return None, False


def format_controls(candidates: list[dict[str, Any]]) -> str:
    """把候选控件格式化为给 LLM 阅读的工具结果文本。"""
    if not candidates:
        return (
            "控件库中未找到匹配的控件。"
            "建议：①改用更通用/更标准的中文或英文描述；"
            "②按 WT 最佳实践改用相对区域定位（click_relative_region）；"
            "③若该控件确实未被收录，可在 control_maps 中补充后再次检索。"
        )
    lines = ["以下是从控件库检索到的候选控件（已按相关度排序，请选最匹配的一项）："]
    for i, c in enumerate(candidates, 1):
        lines.append(
            f'{i}. control_id="{c.get("targetValue", "")}"  名称="{c.get("name", "")}"  '
            f'类型={c.get("controlType") or "?"} 质量={c.get("qualityTier") or c.get("authority") or "N/A"} '
            f'来源={c.get("source", "")}'
        )
        extra: list[str] = []
        if c.get("labelText") and c.get("labelText") != c.get("name"):
            extra.append(f"标签: {c['labelText']}")
        if c.get("optionValues"):
            opts = c["optionValues"][:8]
            suffix = "…" if len(c["optionValues"]) > 8 else ""
            extra.append("下拉选项: " + " / ".join(opts) + suffix)
        if c.get("uiPath"):
            extra.append(f"路径: {c['uiPath'][-80:]}")
        if c.get("notes"):
            extra.append(f"备注: {c['notes']}")
        for e in extra:
            lines.append(f"   {e}")
    lines.append(
        "请把最匹配候选的 control_id（即上面双引号中的 targetValue）填入 "
        "add_step / add_sequence 的 control_id 字段；若都不匹配，"
        "按 WPF 最佳实践改用相对区域定位。"
    )
    return "\n".join(lines)


def search_text(query: str, top_k: int = 5, action: str = "", within=None) -> str:
    """供工具直接调用的便捷封装：返回格式化文本。"""
    return format_controls(find_controls(query, top_k=top_k, action=action, within=within))


def stats() -> dict[str, Any]:
    """返回控件库规模统计（用于状态展示）。"""
    controls = _load()
    from collections import Counter
    src = Counter()
    for c in controls:
        s = str(c.get("source", ""))
        if s.startswith("library:"):
            src["library"] += 1
        elif s.startswith("master:"):
            src["master_only"] += 1
        elif s == "standard_catalog":
            src["standard_catalog"] += 1
        else:
            src["other"] += 1
    return {
        "total": len(controls),
        "by_source": dict(src),
        "with_target": sum(1 for c in controls if c.get("targetValue")),
        "with_label": sum(1 for c in controls if c.get("labelText")),
        "with_options": sum(1 for c in controls if c.get("optionValues")),
        "with_uipath": sum(1 for c in controls if c.get("uiPath")),
        "with_tree_node": sum(1 for c in controls if _split_uipath(c.get("uiPath", ""))),
    }
