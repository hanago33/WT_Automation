# encoding: utf-8
"""flow_repair —— 流程链路确定性修复与备份写回。

与 flow_audit / flow_recorder_converter / FlowEditorApp 共享同一套修复规则：
- 按总控件库 flatControls 的 automationId 回填 targetMethod / targetValue /
  helpText / functionText / uiPath / labelText（与编辑器“批量刷新步骤定位”同源）；
- 步骤名 / 控件名按 functionText → helpText → 原名清洗，去除 SVG path 与
  #[...] 噪声；
- 修正 actionConfig.controlId 与 controls 内嵌 id 的一致性；
- 按 action schema 补齐可推导输入字段（如下拉 recommendedTargetValue → text）；
- 多实例无法确定或语义不明的项进入“待确认”，不自动猜测。

确定性修复完成后自动跑 flow_audit.audit_flow + wt_flow_validation，剩余 error
并入待确认清单。LLM 语义级改动由上层逐条确认后通过 save_with_backup 写回。
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
from datetime import datetime
from typing import Any

from WT_AUTOMATION_Agent.schemas import get_action_schema

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MASTER_CONTROL_FILE = os.path.join(
    _PROJECT_ROOT, "control_maps", "standard", "总控件信息.json"
)

_INDEX_SUFFIX_RE = re.compile(r"#\[[^\]]+\]$")
_COORD_SUFFIX_RE = re.compile(r"%\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)$")
_SVG_PATH_RE = re.compile(r"(?:[MLC]\s*-?\d+(?:\.\d+)?(?:[, ]|$))", re.IGNORECASE)


def load_flat_controls(master_control_file: str = DEFAULT_MASTER_CONTROL_FILE) -> list[dict[str, Any]]:
    """加载总控件库 flatControls；文件缺失或损坏时返回空列表，不抛异常。"""
    if not master_control_file or not os.path.isfile(master_control_file):
        return []
    try:
        with open(master_control_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    flat = data.get("flatControls", [])
    if not isinstance(flat, list):
        return []
    return [c for c in flat if isinstance(c, dict)]


def extract_step_control_aid(control: dict[str, Any]) -> str:
    """从步骤控件快照提取 automationId，缺失时回退到 targetValue 首段。"""
    inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
    aid = str(inspect_data.get("automationId", "") or control.get("automationId", "")).strip()
    if aid:
        return aid
    target_value = str(control.get("targetValue", "") or "").strip()
    if target_value and "," in target_value:
        first = target_value.split(",", 1)[0].strip()
        if first:
            return first
    return ""


def pick_library_match(
    candidates: list[int],
    control: dict[str, Any],
    flat_controls: list[dict[str, Any]],
) -> int | None:
    """同 automationId 多实例时，用 labelText/relatedLabelName → uiPath 挑选最贴近条目。

    与 merge_standard_control_library._discriminator 的判别顺序一致，避免多实例
    （如“半径/X/载入”同 aid）被随机挑中造成错配。
    """
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
    control_label = str(
        control.get("labelText", "")
        or control.get("relatedLabelName", "")
        or inspect_data.get("labelText", "")
        or inspect_data.get("relatedLabelName", "")
    ).strip().lower()
    if control_label:
        for idx in candidates:
            rec = flat_controls[idx]
            rec_ins = rec.get("inspectData", {}) if isinstance(rec.get("inspectData"), dict) else {}
            rec_label = str(
                rec.get("labelText", "")
                or rec.get("relatedLabelName", "")
                or rec_ins.get("labelText", "")
                or rec_ins.get("relatedLabelName", "")
            ).strip().lower()
            if rec_label and rec_label == control_label:
                return idx
    control_ui = str(control.get("uiPath", "") or inspect_data.get("uiPath", "") or "").strip().lower()
    if control_ui:
        for idx in candidates:
            rec = flat_controls[idx]
            if str(rec.get("uiPath", "") or "").strip().lower() == control_ui:
                return idx
    return None


def apply_library_definition(control: dict[str, Any], rec: dict[str, Any]) -> bool:
    """用控件库最新定义覆盖步骤控件定位字段，返回是否发生变更。"""
    rec_ins = rec.get("inspectData", {}) if isinstance(rec.get("inspectData"), dict) else {}
    method = str(rec.get("recommendedTargetMethod", "") or rec.get("targetMethod", "") or "").strip()
    value = str(rec.get("recommendedTargetValue", "") or rec.get("targetValue", "") or "").strip()
    help_text = str(rec.get("helpText", "") or rec_ins.get("helpText", "") or "").strip()
    function_text = str(rec.get("functionText", "") or rec_ins.get("functionText", "") or "").strip()
    ui_path = str(rec.get("uiPath", "") or "").strip()
    label_text = str(rec.get("labelText", "") or rec_ins.get("labelText", "") or "").strip()

    changed = False
    if method and str(control.get("targetMethod", "") or "").strip() != method:
        control["targetMethod"] = method
        changed = True
    if value and str(control.get("targetValue", "") or "").strip() != value:
        control["targetValue"] = value
        changed = True
    if help_text and str(control.get("helpText", "") or "").strip() != help_text:
        control["helpText"] = help_text
        changed = True
    if function_text and str(control.get("functionText", "") or "").strip() != function_text:
        control["functionText"] = function_text
        changed = True
    if ui_path and str(control.get("uiPath", "") or "").strip() != ui_path:
        control["uiPath"] = ui_path
        changed = True
    if label_text and str(control.get("labelText", "") or "").strip() != label_text:
        control["labelText"] = label_text
        changed = True
    if isinstance(control.get("inspectData"), dict):
        if function_text and str(control["inspectData"].get("functionText", "") or "").strip() != function_text:
            control["inspectData"]["functionText"] = function_text
            changed = True
        if help_text and str(control["inspectData"].get("helpText", "") or "").strip() != help_text:
            control["inspectData"]["helpText"] = help_text
            changed = True
    return changed


def _is_svg_path_like(text: str) -> bool:
    if len(text) < 10:
        return False
    if re.search(r"[\u4e00-\u9fff]", text):
        return False
    return bool(_SVG_PATH_RE.search(text))


def clean_display_name(text: str) -> str:
    """去除录制路径中的 #[...] 索引与 %(x,y) 坐标后缀，并丢弃 SVG path 噪声。"""
    text = str(text or "").strip()
    if not text:
        return ""
    text = _COORD_SUFFIX_RE.sub("", text).strip()
    text = _INDEX_SUFFIX_RE.sub("", text).strip()
    if _is_svg_path_like(text):
        return ""
    return text


def _pick_display_name(control: dict[str, Any]) -> str:
    """按 functionText → helpText → 清洗后原名 → automationId → controlType 取可读名。"""
    inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
    raw_name = str(control.get("name", "") or inspect_data.get("name", "") or "").strip()
    candidates = [
        str(control.get("functionText", "") or inspect_data.get("functionText", "")).strip(),
        str(control.get("helpText", "") or inspect_data.get("helpText", "")).strip(),
        clean_display_name(raw_name),
        str(control.get("automationId", "") or inspect_data.get("automationId", "")).strip(),
        str(control.get("controlType", "") or inspect_data.get("controlType", "")).strip(),
    ]
    for cand in candidates:
        if cand and "#[" not in cand and not _is_svg_path_like(cand):
            return cand
    return raw_name


def _auto_item(step_index: int, step_name: str, category: str,
               before: Any, after: Any, message: str) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "step_name": step_name,
        "category": category,
        "before": before,
        "after": after,
        "message": message,
    }


def _pending_item(step_index: int, step_name: str, category: str,
                  message: str, suggestion: str) -> dict[str, Any]:
    return {
        "step_index": step_index,
        "step_name": step_name,
        "category": category,
        "message": message,
        "suggestion": suggestion,
    }


def _index_flat_controls(flat_controls: list[dict[str, Any]]) -> dict[str, list[int]]:
    by_aid: dict[str, list[int]] = {}
    for idx, rec in enumerate(flat_controls):
        rec_ins = rec.get("inspectData", {}) if isinstance(rec.get("inspectData"), dict) else {}
        rec_aid = str(rec.get("automationId", "") or rec_ins.get("automationId", "")).strip().lower()
        if rec_aid:
            by_aid.setdefault(rec_aid, []).append(idx)
    return by_aid


def _repair_control_name(control: dict[str, Any], step_index: int,
                         step_name: str, auto_fixed: list[dict[str, Any]]) -> None:
    old_name = str(control.get("name", "") or "").strip()
    new_name = _pick_display_name(control)
    if new_name and new_name != old_name:
        control["name"] = new_name
        auto_fixed.append(_auto_item(
            step_index, step_name, "name",
            old_name or "(空)", new_name,
            f"控件名称由 {old_name or '(空)'} 清洗为 {new_name}",
        ))


def _repair_control_id_consistency(step: dict[str, Any], controls: list[dict[str, Any]],
                                   ac: dict[str, Any], step_index: int,
                                   step_name: str, auto_fixed: list[dict[str, Any]]) -> None:
    control_ids = [str(c.get("id", "") or "").strip() for c in controls if isinstance(c, dict)]
    ac_cid = str(ac.get("controlId", "") or "").strip()
    if len(control_ids) == 1:
        cid = control_ids[0]
        if cid and ac_cid and ac_cid != cid:
            ac["controlId"] = cid
            auto_fixed.append(_auto_item(
                step_index, step_name, "control_id",
                ac_cid, cid,
                f"actionConfig.controlId 由 {ac_cid} 对齐为内嵌控件 {cid}",
            ))
        elif cid and not ac_cid:
            ac["controlId"] = cid
            auto_fixed.append(_auto_item(
                step_index, step_name, "control_id",
                "(空)", cid,
                f"补齐 actionConfig.controlId = {cid}",
            ))
        elif not cid and ac_cid:
            controls[0]["id"] = ac_cid
            auto_fixed.append(_auto_item(
                step_index, step_name, "control_id",
                "(空)", ac_cid,
                f"内嵌控件 id 由 actionConfig.controlId 补齐为 {ac_cid}",
            ))


def _repair_action_input_fields(step: dict[str, Any], controls: list[dict[str, Any]],
                                ac: dict[str, Any], step_index: int,
                                step_name: str, auto_fixed: list[dict[str, Any]]) -> None:
    action = str(ac.get("action", "") or "").strip()
    if not action:
        return
    schema = get_action_schema(action)
    input_key = str(schema.get("input_key", "") or "").strip()
    value = str(ac.get("recommendedTargetValue", "") or "").strip()
    if not value:
        for control in controls:
            if not isinstance(control, dict):
                continue
            ins = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
            value = str(control.get("recommendedTargetValue", "") or ins.get("recommendedTargetValue", "") or "").strip()
            if value:
                break
    if not value:
        return
    if not str(ac.get("recommendedTargetValue", "") or "").strip():
        ac["recommendedTargetValue"] = value
        auto_fixed.append(_auto_item(
            step_index, step_name, "input",
            "(空)", value,
            f"从控件快照补齐 recommendedTargetValue = {value}",
        ))
    if input_key and not str(ac.get(input_key, "") or "").strip():
        ac[input_key] = value
        auto_fixed.append(_auto_item(
            step_index, step_name, "input",
            "(空)", value,
            f"按 schema 补齐输入字段 {input_key} = {value}",
        ))
    if action in {"select_dropdown_item_runtime", "set_combobox"} and input_key != "text":
        if not str(ac.get("text", "") or "").strip():
            ac["text"] = value
            auto_fixed.append(_auto_item(
                step_index, step_name, "input",
                "(空)", value,
                f"同步下拉目标值 text = {value}（执行器从 text 读取）",
            ))


def _extract_svg_fragment(text: str) -> str:
    """从一段文本中提取疑似 SVG path 的连续片段，用于步骤名替换。"""
    matched = re.search(
        r"(?:[MLC]\s*-?\d+(?:\.\d+)?[, ]?){2,}[A-Za-z0-9.,\- ]*",
        text,
        re.IGNORECASE,
    )
    if not matched:
        return ""
    fragment = matched.group(0).strip()
    return fragment if _is_svg_path_like(fragment) else ""


def _repair_step_name(step: dict[str, Any], controls: list[dict[str, Any]],
                      old_new_pairs: list[tuple[str, str]], step_index: int,
                      auto_fixed: list[dict[str, Any]]) -> None:
    step_name = str(step.get("name", "") or "").strip()
    if not step_name:
        return
    new_step_name = step_name
    for old, new in old_new_pairs:
        if old and new and old != new and old in new_step_name:
            new_step_name = new_step_name.replace(old, new)
    new_step_name = _COORD_SUFFIX_RE.sub("", new_step_name).strip()
    new_step_name = _INDEX_SUFFIX_RE.sub("", new_step_name).strip()
    friendly = next((new for _, new in old_new_pairs if new), "")
    if friendly and re.search(r"Custom#\[[^\]]+\]", new_step_name):
        new_step_name = re.sub(r"Custom#\[[^\]]+\]", friendly, new_step_name)
    if friendly:
        svg_fragment = _extract_svg_fragment(new_step_name)
        if svg_fragment:
            new_step_name = new_step_name.replace(svg_fragment, friendly)
        elif _is_svg_path_like(new_step_name):
            new_step_name = friendly
    if new_step_name != step_name:
        step["name"] = new_step_name
        auto_fixed.append(_auto_item(
            step_index, step_name, "name",
            step_name, new_step_name,
            f"步骤名称由 {step_name} 清洗为 {new_step_name}",
        ))
    if isinstance(step.get("inspectHints"), dict) and controls and isinstance(controls[0], dict):
        control_name = str(controls[0].get("name", "") or "").strip()
        if control_name:
            step["inspectHints"]["controlName"] = control_name


def repair_flow_definition(
    flow: dict[str, Any] | None,
    master_control_file: str = DEFAULT_MASTER_CONTROL_FILE,
    deterministic_only: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """对 flow_definition 做确定性修复，返回 (修复后的 flow, 修复报告)。

    确定性错误自动修；多实例无法确定或语义不明的项写入报告 pending_confirm，
    不自动猜测。deterministic_only 参数保留给上层 LLM 确认流程使用（本模块只做
    确定性修复，剩余项始终进入待确认清单）。
    """
    flow = copy.deepcopy(flow) if isinstance(flow, dict) else {}
    steps = flow.setdefault("steps", [])
    if not isinstance(steps, list):
        return flow, {
            "total_steps": 0,
            "auto_fixed": [],
            "auto_fixed_count": 0,
            "pending_confirm": [],
            "pending_confirm_count": 0,
            "summary": "流程没有 steps 列表",
        }

    flat_controls = load_flat_controls(master_control_file)
    by_aid = _index_flat_controls(flat_controls)
    auto_fixed: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []

    for idx, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            pending.append(_pending_item(idx, "", "structure", "步骤不是对象结构", "检查流程文件是否损坏"))
            continue
        step_name = str(step.get("name", "") or "").strip() or f"步骤{idx}"
        ac = step.get("actionConfig", {})
        if not isinstance(ac, dict):
            step["actionConfig"] = ac = {}
        controls = step.get("controls", [])
        if not isinstance(controls, list):
            controls = []
            step["controls"] = controls

        old_new_pairs: list[tuple[str, str]] = []
        for control in controls:
            if not isinstance(control, dict):
                continue
            old_name = str(control.get("name", "") or "").strip()
            aid = extract_step_control_aid(control)
            if aid:
                candidates = by_aid.get(aid.lower())
                if candidates:
                    match_idx = pick_library_match(candidates, control, flat_controls)
                    if match_idx is None:
                        pending.append(_pending_item(
                            idx, step_name, "control",
                            f"控件 {aid} 在控件库中是多实例，无法确定唯一匹配",
                            "用 labelText/uiPath 判别后手工指定，或补充步骤控件的关联标签",
                        ))
                    elif apply_library_definition(control, flat_controls[match_idx]):
                        auto_fixed.append(_auto_item(
                            idx, step_name, "locator",
                            "(控件库旧定位)", "(控件库推荐定位)",
                            f"按 automationId {aid} 回填控件库推荐定位与语义字段",
                        ))
            _repair_control_name(control, idx, step_name, auto_fixed)
            old_new_pairs.append((old_name, str(control.get("name", "") or "").strip()))

        _repair_control_id_consistency(step, controls, ac, idx, step_name, auto_fixed)
        _repair_action_input_fields(step, controls, ac, idx, step_name, auto_fixed)
        _repair_step_name(step, controls, old_new_pairs, idx, auto_fixed)

    # 修复后联动确定性审核与执行器校验，剩余 error 并入待确认
    try:
        from WT_AUTOMATION_Agent import flow_audit

        audit = flow_audit.audit_flow(flow)
        for issue in audit.get("issues", []):
            if issue.get("level") == "error":
                pending.append(_pending_item(
                    int(issue.get("step_index", 0) or 0),
                    str(issue.get("step_name", "") or ""),
                    str(issue.get("category", "") or ""),
                    str(issue.get("message", "") or ""),
                    str(issue.get("suggestion", "") or ""),
                ))
    except Exception:
        pass
    try:
        from wt_flow_validation import validate_flow_definition

        for error in validate_flow_definition(flow):
            pending.append(_pending_item(
                0, "", "validation",
                str(error), "按校验提示修正后重新运行一键修复",
            ))
    except Exception:
        pass

    # 去重（同一步骤同类问题只保留一条）
    dedup: dict[tuple, dict[str, Any]] = {}
    for item in pending:
        key = (item["step_index"], item["category"], item["message"])
        dedup.setdefault(key, item)
    pending = list(dedup.values())

    report = {
        "total_steps": len(steps),
        "auto_fixed": auto_fixed,
        "auto_fixed_count": len(auto_fixed),
        "pending_confirm": pending,
        "pending_confirm_count": len(pending),
        "summary": f"自动修复 {len(auto_fixed)} 项，待确认 {len(pending)} 项",
    }
    return flow, report


def save_with_backup(path: str, flow: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    """先备份为 <file>.bak.<YYYYmmdd_HHMMSS>，再原地写回流程文件。"""
    path = str(path or "").strip()
    if not path:
        raise ValueError("缺少保存路径")
    backup_path = ""
    if os.path.isfile(path):
        backup_path = f"{path}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(path, backup_path)
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(flow, f, ensure_ascii=False, indent=2)
    return backup_path
