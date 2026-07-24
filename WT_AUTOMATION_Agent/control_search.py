# encoding: utf-8
"""control_search —— 在 control_maps 中语义检索真实存在的控件。

为 Agent 提供"资产辅助构建"能力：当用户要用自然语言描述一个控件时，
先用本模块在控件库（standard_control_catalog / library/*_controls.json）里
找到最匹配的控件，返回其 targetValue（即可填进 add_step.control_id 的真实标识），
从而显著减少"点击了不存在的控件"这类失败。

零外部依赖：仅用标准库做关键词/子串/中文二元组匹配。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

# 项目根目录（WT_AUTOMATION_Agent 的上一级）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STD_CATALOG = os.path.join(_PROJECT_ROOT, "control_maps", "standard", "standard_control_catalog.json")
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

_CACHE: list[dict[str, Any]] | None = None


def _load() -> list[dict[str, Any]]:
    """加载标准控件目录 + library 控件库，返回统一的控件列表。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    controls: list[dict[str, Any]] = []

    # 1) 标准控件目录（权威度最高，按功能窗口分组）
    if os.path.exists(_STD_CATALOG):
        try:
            raw = json.load(open(_STD_CATALOG, "r", encoding="utf-8"))
            groups = raw if isinstance(raw, list) else raw.get("groups", [])
            for grp in groups:
                if not isinstance(grp, dict):
                    continue
                window = str(grp.get("windowTitle", "") or grp.get("frameworkId", ""))
                group_controls = grp.get("controls") if isinstance(grp.get("controls"), list) else [grp]
                for c in group_controls:
                    if not isinstance(c, dict):
                        continue
                    controls.append({
                        "name": str(c.get("name", "")),
                        "controlType": str(c.get("controlType", "")),
                        "className": str(c.get("className", "")),
                        "targetValue": str(c.get("targetValue", "")),
                        "authority": str(c.get("authority", "")),
                        "occurrences": int(c.get("occurrences", 0) or 0),
                        "source": "standard_catalog",
                        "notes": ("窗口: " + window) if window else "",
                    })
        except (json.JSONDecodeError, OSError):
            pass

    # 2) library 控件库
    if os.path.isdir(_LIB_DIR):
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
                controls.append({
                    "name": str(c.get("name", "")),
                    "controlType": str(c.get("controlType", "")),
                    "className": str(c.get("className", "")),
                    "targetValue": str(c.get("targetValue", "")),
                    "authority": str(c.get("authority", "")),
                    "occurrences": 1,
                    "source": "library:" + fn,
                    "notes": str(c.get("notes", "")),
                })

    _CACHE = controls
    return _CACHE


def _bigrams(s: str) -> set[str]:
    s = re.sub(r"\s+", "", s)
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _score(c: dict[str, Any], query: str) -> float:
    """给单个控件打分：子串、token 重叠、中文二元组、类型提示、出现次数。"""
    ql = query.lower()
    q = query  # 保留原始（含中文）
    score = 0.0

    tv = c.get("targetValue", "").lower()
    nm = c.get("controlType", "").lower()
    ct = c.get("controlType", "").lower()
    cn = c.get("name", "")

    # 1) targetValue 子串 / token 重叠（英文标识片段，如 GeographicalData）
    if tv:
        if tv in ql:
            score += 4.0
        for tok in re.split(r"[_,.\- ]+", tv):
            if len(tok) >= 3 and tok in ql:
                score += 2.0

    # 2) controlType 字面命中
    if ct and ct in ql:
        score += 3.0

    # 3) 中文类型提示映射
    for kw, ctype in _TYPE_HINTS.items():
        if kw in q:
            if ctype.lower() in ct:
                score += 3.0
            # name 里若含类型词也加分
            if kw in cn:
                score += 1.0

    # 4) 控件中文名子串 / 被包含
    if cn:
        cnl = cn.lower()
        if cnl and cnl in ql:
            score += 5.0
        if ql and ql in cnl:
            score += 4.0
        # 5) 中文二元组重叠
        overlap = _bigrams(q) & _bigrams(cn)
        score += len(overlap) * 1.5

    # 6) 出现次数作为轻微优先级（同分时靠前）
    score += min(c.get("occurrences", 0) or 0, 10) * 0.1

    return score


def find_controls(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    """按自然语言查询检索控件，返回按相关度排序的候选列表。

    返回的每个元素含：name, controlType, targetValue, authority, source,
    notes, score。
    """
    if not query or not query.strip():
        return []
    controls = _load()
    scored: list[tuple[float, dict[str, Any]]] = []
    for c in controls:
        s = _score(c, query.strip())
        if s > 0:
            scored.append((s, c))
    scored.sort(key=lambda x: (-x[0], -(x[1].get("occurrences", 0) or 0)))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s, c in scored:
        # 同一控件可能被多个窗口分组重复收录，按 targetValue 去重，保留最高分
        key = c.get("targetValue") or c.get("name") or str(id(c))
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "name": c.get("name", ""),
            "controlType": c.get("controlType", ""),
            "targetValue": c.get("targetValue", ""),
            "authority": c.get("authority", ""),
            "source": c.get("source", ""),
            "notes": c.get("notes", ""),
            "score": round(s, 2),
        })
        if len(out) >= max(1, top_k):
            break
    return out


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
            f'{i}. control_id="{c["targetValue"]}"  名称="{c["name"]}"  '
            f'类型={c["controlType"] or "?"} 权威度={c["authority"] or "N/A"} 来源={c["source"]}'
        )
        if c.get("notes"):
            lines.append(f"   备注: {c['notes']}")
    lines.append(
        "请把最匹配候选的 control_id（即上面双引号中的 targetValue）填入 "
        "add_step / add_sequence 的 control_id 字段；若都不匹配，"
        "按 WPF 最佳实践改用相对区域定位。"
    )
    return "\n".join(lines)


def search_text(query: str, top_k: int = 5) -> str:
    """供工具直接调用的便捷封装：返回格式化文本。"""
    return format_controls(find_controls(query, top_k=top_k))


def stats() -> dict[str, Any]:
    """返回控件库规模统计（用于状态展示）。"""
    controls = _load()
    return {
        "total": len(controls),
        "with_target": sum(1 for c in controls if c.get("targetValue")),
    }
