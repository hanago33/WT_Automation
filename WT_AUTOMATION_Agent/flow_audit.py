# encoding: utf-8
"""flow_audit —— 流程链路确定性检查（动作 / 控件 / 类型匹配 / 参数完整性）。

规则层不依赖 LLM，基于 schemas（与执行器 wt_action_schema 对齐）+ control_search
做可复现的规则校验，输出结构化问题清单；语义级审核（动作选型是否合理、控件是否
明显张冠李戴等）由 agent.audit_flow 的 LLM 环节补充。

问题条目结构：
    {"step_index": int, "step_name": str, "level": "error"|"warning",
     "category": str, "message": str, "suggestion": str}
"""
from __future__ import annotations

import re
from typing import Any

from WT_AUTOMATION_Agent.schemas import get_action_names, get_action_schema
from WT_AUTOMATION_Agent import control_search


def _split_locator_parts(text: str) -> list[str]:
    """把 targetMethod/targetValue 按逗号拆分成段（与执行器一致）。"""
    parts = []
    buffer = []
    for char in str(text or ""):
        if char == ",":
            parts.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
    if buffer:
        parts.append("".join(buffer).strip())
    return [p for p in parts if p]


def _find_embedded_control(step: dict[str, Any], control_id: str) -> dict[str, Any] | None:
    """在步骤 controls 内嵌清单里反查控件（id / targetValue 首段 / automationId / 名称）。"""
    cid = (control_id or "").strip().lower()
    if not cid:
        return None
    controls = step.get("controls", [])
    if not isinstance(controls, list):
        return None
    for control in controls:
        if not isinstance(control, dict):
            continue
        inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
        target_value = str(control.get("targetValue", "") or "").strip()
        candidates = [
            control.get("id", ""),
            control.get("automationId", ""),
            control.get("name", ""),
            target_value,
            target_value.split(",", 1)[0] if "," in target_value else "",
            inspect_data.get("automationId", ""),
        ]
        if any(str(item).strip().lower() == cid for item in candidates if str(item).strip()):
            return control
    return None


def audit_flow(flow: dict[str, Any] | None) -> dict[str, Any]:
    """对 flow_definition 做确定性规则检查。

    Args:
        flow: flow_definition dict（含 steps 列表）；None 返回空报告。

    Returns:
        {"total_steps": n, "issues": [...], "summary": str}
    """
    issues: list[dict[str, Any]] = []
    flow = flow if isinstance(flow, dict) else {}
    steps = flow.get("steps", [])
    if not isinstance(steps, list):
        return {"total_steps": 0, "issues": [], "summary": "流程没有 steps 列表"}

    action_names = set(get_action_names())
    # 动作 → 优先控件类型（复用 control_search 的对标表，小写）
    action_type_prefs = getattr(control_search, "_ACTION_TYPE_PREFS", {})
    display_only = getattr(control_search, "_DISPLAY_ONLY_TYPES", set())
    input_actions = getattr(control_search, "_INPUT_ACTIONS", set())

    seen_names: dict[str, int] = {}
    for idx, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            issues.append(_issue(idx, "", "error", "structure", "步骤不是对象结构", "检查流程文件是否损坏"))
            continue
        name = str(step.get("name", "")).strip()
        label = name or f"步骤{idx}"

        if not str(step.get("id", "")).strip():
            issues.append(_issue(idx, name, "error", "structure", "缺少步骤 id（执行器会拒绝加载）",
                                 "补一个唯一 id，如 step_1"))
        if not name:
            issues.append(_issue(idx, name, "error", "structure", "缺少步骤名称", "给步骤起一个可读名称"))
        if name:
            seen_names[name] = seen_names.get(name, 0) + 1

        ac = step.get("actionConfig", {})
        if not isinstance(ac, dict):
            issues.append(_issue(idx, name, "error", "action", "缺少 actionConfig", "补充动作配置"))
            continue

        action = str(ac.get("action", "")).strip()
        if not action:
            issues.append(_issue(idx, name, "error", "action", "action 为空", "选择合法动作"))
            continue
        if action not in action_names:
            issues.append(_issue(idx, name, "error", "action", f"动作 `{action}` 不被执行器支持",
                                 f"改用支持的动作：{', '.join(sorted(action_names))}"))
            continue

        schema = get_action_schema(action)
        control_id = str(ac.get("controlId", "")).strip()

        # 1) 目标控件
        if schema.get("target_required") and not control_id:
            issues.append(_issue(idx, name, "error", "control",
                                 f"动作 `{action}` 需要目标控件（controlId 为空）",
                                 "用 find_control 检索真实控件并填入 control_id"))
        elif control_id:
            # 先查步骤内嵌控件，避免 Custom_1_2、含图形路径的按钮等被误报
            # “控件库中不存在”；内嵌缺失再回退全局控件库精确反查。
            rec = _find_embedded_control(step, control_id)
            if rec is None:
                try:
                    rec = control_search.resolve_control(control_id)
                except Exception:
                    rec = None
            if rec is None:
                issues.append(_issue(idx, name, "error", "control",
                                     f"控件 `{control_id}` 在控件库中不存在",
                                     "用 find_control 检索真实控件替换；或改用相对区域定位"))
            else:
                ctype = str(rec.get("controlType", "")).lower()
                prefs = action_type_prefs.get(action)
                if prefs and ctype and ctype not in prefs:
                    if action in input_actions and ctype in display_only:
                        issues.append(_issue(idx, name, "warning", "match",
                                             f"动作 `{action}` 用于纯展示控件（类型 {ctype}），无法输入/选择",
                                             f"换成可输入的控件（如 {rec.get('targetValue', '')} 的输入框/下拉框）"))
                    elif ctype not in prefs:
                        issues.append(_issue(idx, name, "warning", "match",
                                             f"控件类型 {ctype} 与动作 `{action}` 不太匹配",
                                             f"确认控件是否正确；若控件无误且动作可执行可忽略"))
                if ctype and ctype in prefs and action == "set_combobox" and ctype != "combobox":
                    issues.append(_issue(idx, name, "warning", "match",
                                         f"`set_combobox` 通常作用于 ComboBox，但控件类型是 {ctype}",
                                         "确认是否应为设置下拉框动作"))
                # 控件定位字段可解析性
                tm = str(rec.get("targetMethod", "")).strip()
                tv = str(rec.get("targetValue", "")).strip()
                if tm and tv and len(_split_locator_parts(tm)) != len(_split_locator_parts(tv)):
                    issues.append(_issue(idx, name, "warning", "locator",
                                         "控件 targetMethod 与 targetValue 段数不一致，运行时可能定位失败",
                                         "检查控件库中该控件的定位字段"))
                # 禁用控件
                raw_enabled = str(rec.get("isEnabled", "")).strip().lower()
                if raw_enabled == "false" or rec.get("enabled") is False:
                    issues.append(_issue(idx, name, "warning", "control",
                                         f"控件 `{control_id}` 处于禁用状态（IsEnabled=False），点击/输入可能失败",
                                         "确认目标控件是否在操作前被启用，或改用其他可操作控件"))
                # 弱定位：Custom#[...] / 坐标后缀
                weak_text = " ".join([
                    str(control_id or ""),
                    str(rec.get("targetValue", "") or ""),
                    str(rec.get("targetMethod", "") or ""),
                ])
                if "#[" in weak_text or "%(" in weak_text or re.search(r"Custom#\[", weak_text):
                    issues.append(_issue(idx, name, "warning", "locator",
                                         f"控件 `{control_id}` 使用弱定位（Custom#[...] 或坐标后缀），运行时可能漂移",
                                         "补充 automationId 或 labelText 等稳定定位字段"))

        # 下拉/选择动作缺少目标值
        if action in {"select_dropdown_item_runtime", "set_combobox"}:
            target_option = str(
                ac.get("recommendedTargetValue", "")
                or ac.get("text", "")
                or ac.get("value", "")
            ).strip()
            if not target_option:
                issues.append(_issue(idx, name, "warning", "input",
                                     f"动作 `{action}` 未指定下拉目标值（recommendedTargetValue / text 为空）",
                                     "补充 recommendedTargetValue 与 text，执行器会从 text 读取目标选项"))

        # 2) 输入参数
        input_key = str(schema.get("input_key", "")).strip()
        if schema.get("input_required") and input_key:
            val = ac.get(input_key)
            if val is None or not str(val).strip():
                issues.append(_issue(idx, name, "error", "input",
                                     f"动作 `{action}` 缺少输入字段 `{input_key}`",
                                     f"在步骤编辑中填写 {input_key}（或 text 兜底）"))

        # 3) 相对区域动作参数完整
        if action in ("type_text_relative", "click_relative_region"):
            pw = ac.get("parentWindow", {}) if isinstance(ac.get("parentWindow"), dict) else {}
            rr = ac.get("relativeRegion", {}) if isinstance(ac.get("relativeRegion"), dict) else {}
            if not (str(pw.get("title", "")).strip() or (str(pw.get("className", "")).strip() and str(pw.get("frameworkId", "")).strip())):
                issues.append(_issue(idx, name, "warning", "region",
                                     f"动作 `{action}` 未指定父窗口标题/类名，运行时会扩大候选范围",
                                     "补充 parentWindow.title 或 className+frameworkId"))
            missing_rr = [k for k in ("x", "y", "width", "height") if str(rr.get(k, "")).strip() == ""]
            if missing_rr:
                issues.append(_issue(idx, name, "error", "region",
                                     f"动作 `{action}` 相对区域缺少 {', '.join(missing_rr)}",
                                     f"在步骤编辑中补全 relativeRegion.{'/'.join(missing_rr)}（0~1 归一化）"))

        # 4) 步骤级 windowTitle 占位
        wt = str(step.get("windowTitle", "")).strip()
        if wt and ("window" in wt.lower() or "title" in wt.lower() or "占位" in wt):
            issues.append(_issue(idx, name, "warning", "window",
                                 f"步骤窗口标题疑似占位值：{wt!r}",
                                 "改成真实窗口标题，或留空让控件自身定位"))

    # 重复步骤名
    for n, cnt in seen_names.items():
        if cnt > 1:
            issues.append(_issue(0, n, "warning", "structure",
                                 f"步骤名 `{n}` 出现 {cnt} 次，不利于维护与追溯",
                                 "重命名为有区分度的名称"))

    errors = sum(1 for i in issues if i["level"] == "error")
    warnings = len(issues) - errors
    if not issues:
        summary = "未发现确定性问题（建议再让模型做一次语义级审核）"
    else:
        summary = f"发现 {len(issues)} 个问题（{errors} 个错误、{warnings} 个警告）"
    return {"total_steps": len(steps), "issues": issues, "summary": summary}


def _issue(step_index: int, step_name: str, level: str, category: str,
           message: str, suggestion: str) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "step_name": step_name,
        "level": level,
        "category": category,
        "message": message,
        "suggestion": suggestion,
    }
