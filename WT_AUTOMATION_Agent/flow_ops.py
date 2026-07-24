# encoding: utf-8
"""flow_ops —— 流程定义（flow_definition.json）的读取与结构化处理。

为 Agent 提供"读懂并维护已有流程"的底层能力：
- load_flow: 安全加载流程定义
- flow_to_text: 把流程压成 LLM 友好的紧凑文本
- diff_flows_structural: 两份流程的结构化差异（不依赖 LLM，稳定可用）

解释/编辑/比对的自然语言理解由 agent.py 调用 LLM 完成。
"""
from __future__ import annotations

import json
import os
from typing import Any


def load_flow(path: str) -> dict[str, Any] | None:
    """加载 flow_definition.json，失败返回 None。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("steps", [])
    data.setdefault("flowPackages", [])
    return data


def _step_summary(step: dict[str, Any], idx: int) -> str:
    ac = step.get("actionConfig", {}) or {}
    action = ac.get("action", "?")
    control_id = ac.get("controlId", "")
    text = ac.get("text", "")
    name = step.get("name", "") or "(未命名)"
    parts = [f"  {idx}. [{name}] action={action}"]
    if control_id:
        parts.append(f"controlId={control_id}")
    if text:
        parts.append(f"text={text!r}")
    desc = step.get("description", "")
    if desc:
        parts.append(f"说明={desc}")
    return "  ".join(parts)


def flow_to_text(flow: dict[str, Any], max_steps: int = 300) -> str:
    """把流程定义压成紧凑文本，便于注入 LLM 上下文。"""
    lines: list[str] = []
    desc = flow.get("description", "")
    if desc:
        lines.append(f"# 流程描述: {desc}")
    pkgs = flow.get("flowPackages", [])
    if pkgs:
        names = [p.get("name", "") for p in pkgs if isinstance(p, dict)]
        lines.append("# 流程包: " + ", ".join(filter(None, names)))
    steps = flow.get("steps", [])
    lines.append(f"# 步骤总数: {len(steps)}")
    for i, s in enumerate(steps[:max_steps], 1):
        if not isinstance(s, dict):
            continue
        lines.append(_step_summary(s, i))
    if len(steps) > max_steps:
        lines.append(f"  ...（其余 {len(steps) - max_steps} 步已省略）")
    return "\n".join(lines)


def _norm_step(step: dict[str, Any]) -> tuple[str, str, str, str]:
    ac = step.get("actionConfig", {}) or {}
    return (
        step.get("name", "") or "",
        ac.get("action", "") or "",
        ac.get("controlId", "") or "",
        ac.get("text", "") or "",
    )


def diff_flows_structural(flow_a: dict[str, Any], flow_b: dict[str, Any]) -> str:
    """计算两份流程的结构化差异（基于索引对齐）。"""
    a = flow_a.get("steps", [])
    b = flow_b.get("steps", [])
    n = max(len(a), len(b))
    lines: list[str] = [f"流程A步骤数={len(a)}，流程B步骤数={len(b)}", ""]
    any_diff = False
    for i in range(n):
        sa = a[i] if i < len(a) else None
        sb = b[i] if i < len(b) else None
        if sa is None:
            lines.append(f"步骤{i+1}: [新增于B] {_norm_step(sb)}")  # type: ignore[arg-type]
            any_diff = True
        elif sb is None:
            lines.append(f"步骤{i+1}: [仅A存在] {_norm_step(sa)}")
            any_diff = True
        else:
            na, aa, ca, ta = _norm_step(sa)
            nb, ab, cb, tb = _norm_step(sb)
            changes: list[str] = []
            if na != nb:
                changes.append(f"名称: {na!r}→{nb!r}")
            if aa != ab:
                changes.append(f"动作: {aa}→{ab}")
            if ca != cb:
                changes.append(f"控件: {ca}→{cb}")
            if ta != tb:
                changes.append(f"文本: {ta!r}→{tb!r}")
            if changes:
                lines.append(f"步骤{i+1}: [修改] " + "; ".join(changes))
                any_diff = True
    if not any_diff:
        lines.append("（两份流程在名称/动作/控件/文本上完全一致）")
    return "\n".join(lines)
