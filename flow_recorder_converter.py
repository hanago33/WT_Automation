# encoding: utf-8
import argparse
import ast
import copy
import json
import os
import re
from datetime import datetime
from collections import deque

from wt_action_defaults import build_action_default_config


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_JSON = os.path.join(BASE_DIR, "converted_recorder_flow.json")
CONTROL_MAP_DIR = os.path.join(BASE_DIR, "control_maps")

SUPPORTED_ACTIONS = {
    "click": "click",
    "right_click": "right_click",
    "double_click": "double_click",
    "double_right_click": "double_right_click",
    "drag_and_drop": "drag_and_drop",
    "mouse_wheel": "mouse_wheel",
    "send_keys": "send_keys",
}

UNSUPPORTED_ACTIONS = {
    "move",
}

def _build_action_timing_config(action_name):
    normalized_action = SUPPORTED_ACTIONS.get(action_name, action_name)
    return build_action_default_config(normalized_action)


def _strip_wrapping_quotes(text):
    value = str(text or "").strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return value


def _normalize_match_text(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower() in {"property does not exist", "[null]", "null", "none"}:
        return ""
    return _strip_wrapping_quotes(text)


def _split_identifier_tokens(text):
    text = _normalize_match_text(text)
    if not text:
        return []
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    normalized = re.sub(r"[_\-\\/|>]+", " ", normalized)
    normalized = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", " ", normalized)
    return [item.lower() for item in normalized.split() if item.strip()]


def _sanitize_control_id(text, fallback_prefix="target_control"):
    base_id = re.sub(r"[^0-9a-zA-Z_]+", "_", str(text or "").strip()).strip("_")
    return base_id or fallback_prefix


def _read_text(file_path):
    with open(file_path, "r", encoding="utf-8") as file_obj:
        return file_obj.read()


def _write_json(file_path, payload):
    with open(file_path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)


def _clean_uipath(path_text):
    return str(path_text or "").strip()


def _combine_uipath(parent_path, child_path):
    parent_path = _clean_uipath(parent_path)
    child_path = _clean_uipath(child_path)
    if not parent_path:
        return child_path
    if not child_path:
        return parent_path
    if child_path.startswith(parent_path):
        return child_path
    return f"{parent_path}->{child_path}"


def _split_uipath(path_text):
    return [segment.strip() for segment in str(path_text or "").split("->") if segment.strip()]


def _strip_segment_index(segment_text):
    return re.sub(r'#\[[^\]]+\]$', '', str(segment_text or "").strip())


def _split_coordinate_suffix(text):
    matched = re.search(r'%\(\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\)$', str(text or "").strip())
    if not matched:
        return str(text or "").strip(), None
    base_text = str(text or "").strip()[:matched.start()].strip()
    return base_text, {"x": float(matched.group(1)), "y": float(matched.group(2))}


def _parse_segment(segment_text):
    segment_text = _strip_segment_index(segment_text)
    base_segment, coords = _split_coordinate_suffix(segment_text)
    if "||" not in base_segment:
        return {"raw": segment_text, "name": base_segment, "controlType": "", "coords": coords}
    name, control_type = base_segment.rsplit("||", 1)
    return {"raw": segment_text, "name": name.strip(), "controlType": control_type.strip(), "coords": coords}


def _find_window_title(path_text):
    for segment in reversed(_split_uipath(path_text)):
        parsed = _parse_segment(segment)
        if parsed.get("controlType") == "Window" and parsed.get("name", ""):
            return parsed.get("name", "")
    return ""


def _get_leaf_segment(path_text):
    segments = _split_uipath(path_text)
    if not segments:
        return {"raw": "", "name": "", "controlType": ""}
    return _parse_segment(segments[-1])


def _build_target_method_and_value(path_text):
    leaf = _get_leaf_segment(path_text)
    name = str(leaf.get("name", "")).strip()
    control_type = str(leaf.get("controlType", "")).strip()
    if name and control_type:
        return "name,control_type", f"{name},{control_type}"
    if name:
        return "name", name
    if control_type:
        return "control_type", control_type
    return "", ""


def _split_target_value(target_value):
    parts = []
    buffer = []
    text = str(target_value or "")
    index = 0
    while index < len(text):
        current = text[index]
        if current == "\\" and index + 1 < len(text) and text[index + 1] == ",":
            buffer.append(",")
            index += 2
            continue
        if current == ",":
            parts.append("".join(buffer).strip())
            buffer = []
            index += 1
            continue
        buffer.append(current)
        index += 1
    parts.append("".join(buffer).strip())
    return [_normalize_match_text(item) for item in parts if _normalize_match_text(item)]


def _clean_control_definition(control_definition, fallback_window_title="", source_file=""):
    control_definition = copy.deepcopy(control_definition if isinstance(control_definition, dict) else {})
    inspect_data = control_definition.get("inspectData") if isinstance(control_definition.get("inspectData"), dict) else {}
    cleaned_inspect = {}
    for key, value in inspect_data.items():
        if isinstance(value, list):
            cleaned_inspect[key] = [_normalize_match_text(item) for item in value if _normalize_match_text(item)]
        else:
            cleaned_inspect[key] = _normalize_match_text(value)
    control_definition["inspectData"] = cleaned_inspect
    control_definition["id"] = _sanitize_control_id(control_definition.get("id", ""), "target_control")
    control_definition["name"] = _normalize_match_text(control_definition.get("name", "")) or cleaned_inspect.get("name", "") or control_definition["id"]
    control_definition["windowTitle"] = _normalize_match_text(control_definition.get("windowTitle", "")) or _normalize_match_text(fallback_window_title)
    control_definition["targetMethod"] = _normalize_match_text(control_definition.get("targetMethod", ""))
    control_definition["targetValue"] = _normalize_match_text(control_definition.get("targetValue", ""))
    control_definition["uiPath"] = _normalize_match_text(control_definition.get("uiPath", ""))
    control_definition["templateKey"] = _normalize_match_text(control_definition.get("templateKey", ""))
    control_definition["notes"] = _normalize_match_text(control_definition.get("notes", ""))
    control_definition["auxChecks"] = [_normalize_match_text(item) for item in control_definition.get("auxChecks", []) if _normalize_match_text(item)]
    control_definition["source"] = _normalize_match_text(control_definition.get("source", "")) or "control_map"
    control_definition["sourceFile"] = source_file
    return control_definition


def _load_control_map_definitions(control_map_dir=CONTROL_MAP_DIR):
    definitions = []
    if not os.path.isdir(control_map_dir):
        return definitions
    seen = set()
    for file_name in sorted(os.listdir(control_map_dir), reverse=True):
        if not file_name.lower().endswith(".json"):
            continue
        file_path = os.path.join(control_map_dir, file_name)
        try:
            with open(file_path, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        target_window = payload.get("targetWindow") if isinstance(payload.get("targetWindow"), dict) else {}
        fallback_window_title = _normalize_match_text(target_window.get("title", ""))
        for item in payload.get("controlDefinitions", []):
            if not isinstance(item, dict):
                continue
            cleaned = _clean_control_definition(item, fallback_window_title=fallback_window_title, source_file=file_name)
            inspect_data = cleaned.get("inspectData", {})
            target_parts = _split_target_value(cleaned.get("targetValue", ""))
            control_type = _normalize_match_text(inspect_data.get("controlType", "")) or (target_parts[-1] if target_parts else "")
            search_tokens = set()
            for value in [
                cleaned.get("name", ""),
                cleaned.get("uiPath", ""),
                cleaned.get("windowTitle", ""),
                cleaned.get("targetValue", ""),
                inspect_data.get("automationId", ""),
                inspect_data.get("name", ""),
                inspect_data.get("className", ""),
                inspect_data.get("frameworkId", ""),
            ]:
                search_tokens.update(_split_identifier_tokens(value))
            dedupe_key = (
                cleaned.get("id", ""),
                inspect_data.get("automationId", ""),
                cleaned.get("targetMethod", ""),
                cleaned.get("targetValue", ""),
                cleaned.get("uiPath", ""),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            definitions.append(
                {
                    "definition": cleaned,
                    "searchTokens": search_tokens,
                    "windowTitle": cleaned.get("windowTitle", ""),
                    "controlType": control_type,
                    "automationId": inspect_data.get("automationId", ""),
                    "className": inspect_data.get("className", ""),
                    "friendlyName": cleaned.get("name", ""),
                    "uiPath": cleaned.get("uiPath", ""),
                    "leafName": _normalize_match_text(_get_leaf_segment(cleaned.get("uiPath", "")).get("name", "")),
                }
            )
    return definitions


def _score_control_map_match(full_path, step_action, candidate):
    leaf = _get_leaf_segment(full_path)
    leaf_name = _normalize_match_text(leaf.get("name", ""))
    leaf_type = _normalize_match_text(leaf.get("controlType", ""))
    window_title = _normalize_match_text(_find_window_title(full_path))
    ui_path = _normalize_match_text(full_path)
    recorder_tokens = set(_split_identifier_tokens(ui_path))
    score = 0
    reasons = []

    candidate_type = _normalize_match_text(candidate.get("controlType", ""))
    if leaf_type and candidate_type and leaf_type.lower() == candidate_type.lower():
        score += 18
        reasons.append("控件类型一致")

    automation_id = _normalize_match_text(candidate.get("automationId", ""))
    friendly_name = _normalize_match_text(candidate.get("friendlyName", ""))
    leaf_ui_name = _normalize_match_text(candidate.get("leafName", ""))
    class_name = _normalize_match_text(candidate.get("className", ""))
    candidate_path = _normalize_match_text(candidate.get("uiPath", ""))
    candidate_window = _normalize_match_text(candidate.get("windowTitle", ""))

    if window_title and candidate_window:
        if window_title == candidate_window:
            score += 16
            reasons.append("窗口标题一致")
        elif window_title in candidate_window or candidate_window in window_title:
            score += 10
            reasons.append("窗口标题相近")

    if leaf_name:
        if automation_id and leaf_name == automation_id:
            score += 120
            reasons.append("AutomationId 精确命中")
        elif leaf_ui_name and leaf_name == leaf_ui_name:
            score += 95
            reasons.append("UIPath 叶子名精确命中")
        elif friendly_name and leaf_name == friendly_name:
            score += 80
            reasons.append("控件名称精确命中")
        elif automation_id and leaf_name.lower() in automation_id.lower():
            score += 38
            reasons.append("AutomationId 包含叶子名")
        elif candidate_path and leaf_name.lower() in candidate_path.lower():
            score += 24
            reasons.append("UIPath 包含叶子名")
        elif class_name and leaf_name.lower() == class_name.lower():
            score += 18
            reasons.append("类名命中")

    overlap_tokens = recorder_tokens & set(candidate.get("searchTokens", set()))
    if overlap_tokens:
        token_score = min(28, len(overlap_tokens) * 4)
        score += token_score
        reasons.append(f"路径语义重合 {len(overlap_tokens)} 项")

    action_type_bonus = {
        "click": {"Button", "MenuItem", "TabItem", "TreeItem", "ListItem"},
        "double_click": {"Button", "ListItem", "TreeItem", "TabItem"},
        "right_click": {"Pane", "Button", "TreeItem", "ListItem"},
        "type_text": {"Edit", "ComboBox", "Document"},
        "send_keys": {"Edit", "ComboBox", "Document"},
        "wait_for_control": {"Window", "Button", "Edit", "Custom"},
        "mouse_wheel": {"Pane", "Custom", "List", "Tree"},
    }
    preferred_types = action_type_bonus.get(step_action, set())
    if candidate_type in preferred_types:
        score += 6
        reasons.append("动作类型偏好匹配")

    return score, reasons


def _match_control_from_library(full_path, step_action, control_library):
    if not full_path or not control_library:
        return None
    best_match = None
    best_score = -1
    best_reasons = []
    for candidate in control_library:
        score, reasons = _score_control_map_match(full_path, step_action, candidate)
        if score > best_score:
            best_score = score
            best_match = candidate
            best_reasons = reasons
    if best_match is None or best_score < 32:
        return None
    matched = copy.deepcopy(best_match["definition"])
    matched["notes"] = (
        (matched.get("notes", "") + " | " if matched.get("notes", "") else "")
        + f"recorder 自动匹配命中，评分={best_score}，依据={'; '.join(best_reasons) or '综合相似度'}。"
    )
    return {
        "definition": matched,
        "score": best_score,
        "reasons": best_reasons,
    }


def _format_coords(coords):
    if not isinstance(coords, dict):
        return ""
    try:
        x_value = float(coords.get("x", 0))
        y_value = float(coords.get("y", 0))
    except Exception:
        return ""
    return f"@({x_value:.2f},{y_value:.2f})"


def _is_generic_click_target(leaf, window_title=""):
    control_type = str((leaf or {}).get("controlType", "")).strip()
    control_name = str((leaf or {}).get("name", "")).strip()
    generic_control_types = {"Window", "Pane", "Custom", "List", "ComboBox"}
    return bool(
        control_type in generic_control_types
        and (leaf or {}).get("coords")
        and (not control_name or control_name == str(window_title or "").strip())
    )


def _derive_step_name(action_name, path_text, text_value=""):
    leaf = _get_leaf_segment(path_text)
    window_title = _find_window_title(path_text)
    control_name = leaf.get("name", "") or leaf.get("controlType", "") or "目标控件"
    if action_name in {"click", "right_click", "double_click", "double_right_click"} and _is_generic_click_target(leaf, window_title):
        coords_text = _format_coords(leaf.get("coords"))
        control_name = f"{window_title or control_name or '窗口点击'}{coords_text}"
    elif action_name in {"click", "right_click", "double_click", "double_right_click"} and leaf.get("coords"):
        raw_name = str(leaf.get("name", "")).strip()
        raw_type = str(leaf.get("controlType", "")).strip()
        if (not raw_type and raw_name.startswith("Custom#[")) or raw_name in {"", "Window", "Pane", "Custom"}:
            coords_text = _format_coords(leaf.get("coords"))
            control_name = f"{raw_name or window_title or '目标控件'}{coords_text}"
    name_map = {
        "click": f"点击 {control_name}",
        "right_click": f"右键 {control_name}",
        "double_click": f"双击 {control_name}",
        "double_right_click": f"双右键 {control_name}",
        "send_keys": f"输入 {text_value or control_name}",
        "drag_and_drop": f"拖拽 {control_name}",
        "mouse_wheel": f"滚动 {text_value or control_name}",
    }
    return name_map.get(action_name, f"{action_name} {control_name}".strip())


def _sanitize_step_id(text, fallback_prefix="step"):
    sanitized = re.sub(r'[^0-9a-zA-Z_]+', '_', str(text or "").strip().lower()).strip('_')
    return sanitized or fallback_prefix


def _unique_step_id(base_id, seen_ids):
    if base_id not in seen_ids:
        seen_ids.add(base_id)
        return base_id
    suffix = 2
    while f"{base_id}_{suffix}" in seen_ids:
        suffix += 1
    unique_id = f"{base_id}_{suffix}"
    seen_ids.add(unique_id)
    return unique_id


def _literal_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return ""
        return "".join(parts)
    return ""


def _literal_number(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        operand = _literal_number(node.operand)
        if operand is None:
            return None
        return -operand if isinstance(node.op, ast.USub) else operand
    return None


def _stringify_arg(node):
    text_value = _literal_string(node)
    if text_value:
        return text_value
    number_value = _literal_number(node)
    if number_value is not None:
        return str(number_value)
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _call_name(call_node):
    func = getattr(call_node, "func", None)
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _extract_uipath_from_expr(expr_node):
    if not isinstance(expr_node, ast.Call):
        return ""
    if _call_name(expr_node) != "UIPath" or not expr_node.args:
        return ""
    return _literal_string(expr_node.args[0])


def _normalize_send_keys_text(text):
    normalized = str(text or "")
    replacements = {
        '{VK_CONTROL down}" "{v down}" "{VK_CONTROL up}" "{v up}': "^v",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    normalized = normalized.replace('""', '')
    return normalized.strip()


def _append_step_review_hint(step, hint_text):
    if not isinstance(step, dict):
        return
    hint_text = str(hint_text or "").strip()
    if not hint_text:
        return
    hints = step.setdefault("_reviewHints", [])
    if hint_text not in hints:
        hints.append(hint_text)
    notes = str(step.get("notes", "")).strip()
    marker = f"[待确认: {hint_text}]"
    if marker not in notes:
        step["notes"] = (notes + " " + marker).strip()


def _infer_runtime_binding(text_value):
    normalized = str(text_value or "").strip().strip('"').strip("'")
    lowered = normalized.lower()
    if not normalized:
        return None
    if any(token in normalized for token in ("${runtime.", "{ENTER}", "^", "%", "{TAB}")):
        return None
    if re.search(r"\.(prj|qpj)$", lowered):
        return {
            "runtimeKey": "projectionFilePath",
            "placeholder": "${runtime.projectionFilePath}",
            "reason": "检测到投影文件路径常量",
        }
    if re.search(r"\.(dwg|shp|tif|tiff|tab|geojson|json|csv|txt)$", lowered):
        return {
            "runtimeKey": "sourceFilePath",
            "placeholder": "${runtime.sourceFilePath}",
            "reason": "检测到源文件路径常量",
        }
    if ("/" in normalized or "\\" in normalized) and not os.path.splitext(lowered)[1]:
        return {
            "runtimeKey": "outputDir",
            "placeholder": "${runtime.outputDir}",
            "reason": "检测到目录路径常量",
        }
    return None


def _apply_runtime_binding_to_step(step, binding, original_text, line_no, stats=None):
    if not isinstance(step, dict) or not isinstance(binding, dict):
        return
    step.setdefault("stepParams", {})
    step["stepParams"].setdefault("runtimeBindings", [])
    step["stepParams"]["runtimeBindings"].append(
        {
            "key": binding.get("runtimeKey", ""),
            "placeholder": binding.get("placeholder", ""),
            "originalValue": str(original_text or ""),
            "sourceRecorderLine": line_no,
        }
    )
    step["notes"] = (
        step.get("notes", "")
        + f" [第 {line_no} 行已把文本常量提升为运行参数 {binding.get('runtimeKey', '')}]"
    ).strip()
    if stats is not None:
        stats["runtimeParamBindings"] += 1


def _build_control_definition(control_id, full_path, window_title):
    target_method, target_value = _build_target_method_and_value(full_path)
    leaf = _get_leaf_segment(full_path)
    control_definition = {
        "id": _sanitize_control_id(control_id, "target_control"),
        "name": leaf.get("name", "") or leaf.get("controlType", "") or control_id,
        "role": "来自 recorder 自动转换的目标控件",
        "targetMethod": target_method,
        "targetValue": target_value,
        "windowTitle": window_title,
        "uiPath": full_path,
        "notes": "由 pywinauto_recorder 录制脚本自动转换生成；若控件库未命中，可继续补充 Inspect/Accessibility 信息。",
        "inspectData": {
            "name": _normalize_match_text(leaf.get("name", "")),
            "controlType": _normalize_match_text(leaf.get("controlType", "")),
            "localizedControlType": "",
            "automationId": _normalize_match_text(leaf.get("name", "")),
            "className": "",
            "frameworkId": "",
            "recommendedTargetMethod": target_method,
            "recommendedTargetValue": target_value,
        },
        "auxChecks": [],
        "source": "recorder",
    }
    if leaf.get("coords"):
        coords_text = _format_coords(leaf.get("coords"))
        if coords_text:
            control_definition["notes"] += f" 点击偏移={coords_text}。"
    return control_definition


def _apply_control_definition_to_step(step, control_definition, line_no, match_meta=None):
    control_definition = copy.deepcopy(control_definition if isinstance(control_definition, dict) else {})
    control_definition["id"] = _sanitize_control_id(control_definition.get("id", ""), "target_control")
    step["controls"] = [control_definition]
    step["actionConfig"]["controlId"] = control_definition["id"]
    inspect_data = control_definition.get("inspectData", {}) if isinstance(control_definition.get("inspectData"), dict) else {}
    step["inspectHints"] = {
        "controlName": _normalize_match_text(control_definition.get("name", "")) or _normalize_match_text(inspect_data.get("name", "")),
        "className": _normalize_match_text(inspect_data.get("className", "")),
        "automationId": _normalize_match_text(inspect_data.get("automationId", "")),
        "controlType": _normalize_match_text(inspect_data.get("controlType", "")),
        "uiPath": _normalize_match_text(control_definition.get("uiPath", "")),
        "templateKey": _normalize_match_text(control_definition.get("templateKey", "")),
    }
    if _normalize_match_text(control_definition.get("windowTitle", "")):
        step["windowTitle"] = _normalize_match_text(control_definition.get("windowTitle", ""))
    if match_meta:
        step["strategy"] = "recorder -> action -> control_map"
        step["notes"] = (
            step.get("notes", "")
            + f" [第 {line_no} 行已自动匹配控件库，评分={match_meta.get('score', 0)}]"
        ).strip()
    return step


def _build_action_step_base(action_name, line_no, step_name, window_title, inspect_path):
    leaf_segment = _get_leaf_segment(inspect_path)
    return {
        "id": "",
        "name": step_name,
        "stage": "converted",
        "strategy": "recorder -> action",
        "actionType": "action",
        "enabled": True,
        "codeSymbol": "",
        "codeReference": "",
        "description": f"由 recorder 第 {line_no} 行自动转换。",
        "successLog": step_name,
        "windowTitle": window_title,
        "inspectHints": {
            "controlName": leaf_segment.get("name", ""),
            "className": "",
            "automationId": "",
            "controlType": leaf_segment.get("controlType", ""),
            "uiPath": inspect_path,
            "templateKey": "",
        },
        "controls": [],
        "stepParams": {},
        "actionConfig": {
            **_build_action_timing_config(action_name),
            "sourceRecorderLine": line_no,
        },
        "auxChecks": [],
        "fallbacks": [],
        "notes": "自动转换步骤，建议补充 controls.inspectData 和更稳的 targetMethod/targetValue。",
    }


def _build_action_step(action_name, full_path, line_no, text_value="", control_library=None, stats=None):
    window_title = _find_window_title(full_path)
    step_name = _derive_step_name(action_name, full_path, text_value=text_value)
    control_id = _sanitize_control_id(_get_leaf_segment(full_path).get("name", "") or _get_leaf_segment(full_path).get("controlType", ""), "target_control")
    step = _build_action_step_base(action_name, line_no, step_name, window_title, full_path)
    leaf = _get_leaf_segment(full_path)
    if leaf.get("coords"):
        step["actionConfig"]["positionOffset"] = leaf.get("coords")

    matched_control = _match_control_from_library(full_path, SUPPORTED_ACTIONS.get(action_name, action_name), control_library or [])
    if matched_control:
        _apply_control_definition_to_step(step, matched_control["definition"], line_no, match_meta=matched_control)
        if stats is not None:
            stats["controlMapMatchedCount"] += 1

    if action_name == "send_keys":
        normalized_text = _normalize_send_keys_text(text_value)
        runtime_binding = _infer_runtime_binding(normalized_text)
        step["actionConfig"]["text"] = runtime_binding.get("placeholder", normalized_text) if runtime_binding else normalized_text
        if runtime_binding:
            _apply_runtime_binding_to_step(step, runtime_binding, normalized_text, line_no, stats=stats)
        step["actionConfig"]["action"] = "type_text" if step.get("controls") else "send_keys"
        if not step.get("controls") and full_path and leaf.get("controlType", ""):
            _apply_control_definition_to_step(step, _build_control_definition(control_id, full_path, window_title), line_no)
    else:
        if not step.get("controls"):
            _apply_control_definition_to_step(step, _build_control_definition(control_id, full_path, window_title), line_no)

    if not matched_control:
        _append_step_review_hint(step, "未命中控件库，建议补充控件库或检查目标控件定位")
    if action_name in {"click", "right_click", "double_click", "double_right_click"} and _is_generic_click_target(leaf, window_title):
        _append_step_review_hint(step, "当前步骤更像坐标点击，后续建议改成稳定控件定位")

    return step


def _build_drag_action_step(current_scope_path, source_path, target_path, line_no, control_library=None, stats=None):
    full_source_path = _combine_uipath(current_scope_path, source_path)
    full_target_path = _combine_uipath(current_scope_path, target_path)
    window_title = _find_window_title(full_source_path) or _find_window_title(full_target_path)
    source_leaf = _get_leaf_segment(full_source_path)
    target_leaf = _get_leaf_segment(full_target_path)
    source_name = source_leaf.get("name", "") or source_leaf.get("controlType", "") or "源控件"
    target_name = target_leaf.get("name", "") or target_leaf.get("controlType", "") or "目标控件"
    step_name = f"拖拽 {source_name} 到 {target_name}"
    step = _build_action_step_base("drag_and_drop", line_no, step_name, window_title, full_source_path or full_target_path)
    source_control = _build_control_definition("source_control", full_source_path, window_title)
    target_control = _build_control_definition("target_control", full_target_path, window_title)
    source_match = _match_control_from_library(full_source_path, "drag_and_drop", control_library or [])
    target_match = _match_control_from_library(full_target_path, "drag_and_drop", control_library or [])
    if source_match:
        source_control = source_match["definition"]
        if stats is not None:
            stats["controlMapMatchedCount"] += 1
    if target_match:
        target_control = target_match["definition"]
        if stats is not None:
            stats["controlMapMatchedCount"] += 1
    source_control["id"] = "source_control"
    target_control["id"] = "target_control"
    step["actionConfig"]["controlId"] = "source_control"
    step["actionConfig"]["sourceControlId"] = "source_control"
    step["actionConfig"]["targetControlId"] = "target_control"
    step["controls"] = [source_control, target_control]
    return step


def _build_mouse_wheel_step(current_scope_path, delta_value, line_no, control_library=None, stats=None):
    full_path = current_scope_path
    step_name = _derive_step_name("mouse_wheel", full_path, text_value=str(delta_value))
    window_title = _find_window_title(full_path)
    step = _build_action_step_base("mouse_wheel", line_no, step_name, window_title, full_path)
    step["actionConfig"]["delta"] = delta_value
    matched_control = _match_control_from_library(full_path, "mouse_wheel", control_library or [])
    if matched_control:
        _apply_control_definition_to_step(step, matched_control["definition"], line_no, match_meta=matched_control)
        if stats is not None:
            stats["controlMapMatchedCount"] += 1
    elif full_path and _get_leaf_segment(full_path).get("controlType", ""):
        _apply_control_definition_to_step(step, _build_control_definition("target_control", full_path, window_title), line_no)
    return step


def _build_placeholder_step(action_name, scope_path, call_args_text, line_no):
    step_name = _derive_step_name(action_name, scope_path, text_value=call_args_text)
    step = {
        "id": "",
        "name": step_name,
        "stage": "converted",
        "strategy": "recorder -> placeholder",
        "actionType": "placeholder",
        "enabled": True,
        "codeSymbol": "",
        "codeReference": "",
        "description": f"由 recorder 第 {line_no} 行自动生成，占位等待人工处理。",
        "successLog": "",
        "windowTitle": _find_window_title(scope_path),
        "inspectHints": {
            "controlName": _get_leaf_segment(scope_path).get("name", ""),
            "className": "",
            "automationId": "",
            "controlType": _get_leaf_segment(scope_path).get("controlType", ""),
            "uiPath": scope_path,
            "templateKey": "",
        },
        "controls": [],
        "stepParams": {},
        "actionConfig": {},
        "auxChecks": [],
        "fallbacks": [],
        "notes": f"当前 recorder 动作 `{action_name}` 暂未自动转换为可执行 action。原始参数：{call_args_text}",
    }
    _append_step_review_hint(step, "该步骤为占位步骤，需人工确认后再执行")
    return step


def _convert_call_to_step(call_node, current_scope_path, stats, control_library=None):
    action_name = _call_name(call_node)
    if not action_name:
        return None

    line_no = getattr(call_node, "lineno", 0)
    arg_texts = [_stringify_arg(arg) for arg in call_node.args]
    arg_texts = [item for item in arg_texts if item]

    if action_name in {"click", "right_click", "double_click", "double_right_click"}:
        full_path = _combine_uipath(current_scope_path, arg_texts[0] if arg_texts else "")
        return _build_action_step(action_name, full_path, line_no, control_library=control_library, stats=stats)

    if action_name == "send_keys":
        text_value = arg_texts[0] if arg_texts else ""
        return _build_action_step(action_name, current_scope_path, line_no, text_value=text_value, control_library=control_library, stats=stats)

    if action_name == "drag_and_drop":
        if len(arg_texts) >= 2:
            full_source_path = _combine_uipath(current_scope_path, arg_texts[0])
            full_target_path = _combine_uipath(current_scope_path, arg_texts[1])
            if full_source_path and full_source_path == full_target_path:
                stats["droppedNoOpDragCount"] += 1
                stats["droppedRecorderLines"].append(line_no)
                return None
            return _build_drag_action_step(current_scope_path, arg_texts[0], arg_texts[1], line_no, control_library=control_library, stats=stats)
        return _build_placeholder_step(action_name, current_scope_path, " | ".join(arg_texts), line_no)

    if action_name == "mouse_wheel":
        delta_value = _literal_number(call_node.args[0]) if call_node.args else None
        if delta_value is None:
            arg_text = arg_texts[0] if arg_texts else ""
            try:
                delta_value = float(arg_text)
            except Exception:
                delta_value = arg_text or 0
        return _build_mouse_wheel_step(current_scope_path, delta_value, line_no, control_library=control_library, stats=stats)

    if action_name in UNSUPPORTED_ACTIONS:
        return _build_placeholder_step(action_name, current_scope_path, " | ".join(arg_texts), line_no)

    return None


def _walk_statements(statements, scope_stack, raw_steps, stats, control_library=None):
    current_scope = scope_stack[-1] if scope_stack else ""
    for statement in statements:
        if isinstance(statement, ast.With):
            new_scope = current_scope
            for item in statement.items:
                path_text = _extract_uipath_from_expr(item.context_expr)
                if path_text:
                    new_scope = _combine_uipath(new_scope, path_text)
            _walk_statements(statement.body, scope_stack + [new_scope], raw_steps, stats, control_library=control_library)
            continue

        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            step = _convert_call_to_step(statement.value, current_scope, stats, control_library=control_library)
            if step:
                raw_steps.append(step)
            continue

        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            step = _convert_call_to_step(statement.value, current_scope, stats, control_library=control_library)
            if step:
                raw_steps.append(step)
            continue

        nested_bodies = []
        if isinstance(statement, ast.If):
            nested_bodies.extend([statement.body, statement.orelse])
        elif isinstance(statement, (ast.For, ast.While, ast.Try)):
            nested_bodies.append(statement.body)
            nested_bodies.append(getattr(statement, "orelse", []))
            nested_bodies.append(getattr(statement, "finalbody", []))
            for handler in getattr(statement, "handlers", []):
                nested_bodies.append(handler.body)

        for body in nested_bodies:
            if body:
                _walk_statements(body, scope_stack, raw_steps, stats, control_library=control_library)


def _build_step_signature(step):
    """构建步骤的唯一签名，用于去重和模式识别"""
    action_type = step.get("actionType", "")
    
    if action_type == "action":
        action_config = step.get("actionConfig", {})
        action_name = action_config.get("action", "")
        inspect_hints = step.get("inspectHints", {})
        ui_path = inspect_hints.get("uiPath", "")
        
        if action_name in {"click", "right_click", "double_click", "double_right_click"}:
            return f"{action_name}:{ui_path}"
        
        if action_name in {"send_keys", "type_text"}:
            text = action_config.get("text", "")
            return f"{action_name}:{ui_path}:{text}"
        
        return f"{action_type}:{action_name}:{ui_path}"
    else:
        notes = step.get("notes", "")
        match = re.search(r'`([a-z_]+)`', notes)
        if match:
            action_name = match.group(1)
            arg_match = re.search(r'原始参数[：:]\s*(.+)', notes)
            args = arg_match.group(1) if arg_match else ""
            return f"{action_type}:{action_name}:{args}"
        return f"{action_type}:{step.get('description', '')}"


class SemanticAnalyzer:
    """语义分析器 - 理解录制步骤的实际意义"""
    
    def __init__(self):
        self.stats = {
            "duplicatesRemoved": 0,
            "waitInferred": 0,
            "sequencesMerged": 0,
            "noiseFiltered": 0,
        }
    
    def analyze(self, raw_steps):
        """执行完整的语义分析流程"""
        if not raw_steps:
            return raw_steps
        
        # 1. 过滤纯噪声步骤
        steps = self._filter_noise(raw_steps)
        
        # 2. 识别有意义的操作序列
        steps = self._identify_meaningful_sequences(steps)
        
        # 3. 推断等待操作
        steps = self._infer_waits(steps)
        
        # 4. 智能去重（保留有意义的重复）
        steps = self._smart_deduplicate(steps)
        
        # 5. 合并相关操作
        steps = self._merge_related_operations(steps)
        
        return steps
    
    def _filter_noise(self, steps):
        """过滤纯噪声步骤（如连续的鼠标移动）"""
        filtered = []
        for step in steps:
            sig = _build_step_signature(step)
            # 过滤掉连续的 move 操作
            if "placeholder:move:" in sig and filtered and "placeholder:move:" in _build_step_signature(filtered[-1]):
                self.stats["noiseFiltered"] += 1
                continue
            filtered.append(step)
        return filtered
    
    def _identify_meaningful_sequences(self, steps):
        """识别有意义的操作序列"""
        # 标记每个步骤的重要性
        for i, step in enumerate(steps):
            sig = _build_step_signature(step)
            # action 类型的步骤通常更重要
            if step.get("actionType") == "action":
                step["_importance"] = "high"
            else:
                step["_importance"] = "low"
        return steps
    
    def _infer_waits(self, steps):
        """智能推断需要等待的位置"""
        result = []
        window_changes = []
        
        for i, step in enumerate(steps):
            result.append(step)
            
            # 检查窗口变化
            current_window = step.get("windowTitle", "")
            if i > 0:
                prev_window = steps[i-1].get("windowTitle", "")
                if current_window and prev_window and current_window != prev_window:
                    # 窗口切换后可能需要等待
                    window_changes.append(i)
        
        # 在关键操作后添加等待提示
        for i in window_changes:
            if i < len(result):
                step = result[i]
                step["notes"] = step.get("notes", "") + " [可能需要等待窗口加载]"
                action_config = step.get("actionConfig", {}) if isinstance(step.get("actionConfig"), dict) else {}
                wait_after = action_config.get("waitAfter", 0.3)
                try:
                    wait_after = float(wait_after)
                except Exception:
                    wait_after = 0.3
                action_config["waitAfter"] = max(wait_after, 1.0)
                step["actionConfig"] = action_config
                self.stats["waitInferred"] += 1
        
        return steps
    
    def _smart_deduplicate(self, steps):
        """智能去重 - 保留有意义的重复（如双击）"""
        result = []
        window = deque(maxlen=3)  # 滑动窗口
        
        for step in steps:
            current_sig = _build_step_signature(step)
            
            # 检查是否是有意义的重复操作
            is_meaningful_duplicate = False
            
            # 检查是否是双击模式（两个相同的点击紧挨着）
            if len(window) >= 1:
                prev_sig = _build_step_signature(window[-1])
                if (current_sig and prev_sig and 
                    "click:" in current_sig and 
                    "click:" in prev_sig and
                    current_sig == prev_sig):
                    current_action = ((step.get("actionConfig", {}) or {}).get("action", "")).strip()
                    prev_action = ((window[-1].get("actionConfig", {}) or {}).get("action", "")).strip()
                    if current_action == "click" and prev_action == "click":
                        window[-1]["actionConfig"]["action"] = "double_click"
                        window[-1]["name"] = str(window[-1].get("name", "")).replace("点击 ", "双击 ", 1)
                        window[-1]["notes"] = (window[-1].get("notes", "") + " [由连续两次点击自动合并为双击]").strip()
                        self.stats["sequencesMerged"] += 1
                        self.stats["duplicatesRemoved"] += 1
                        continue
                    # 这可能是双击，保留两个
                    is_meaningful_duplicate = True
            
            # 检查是否是重试模式（相同操作重复多次）
            if len(window) >= 2:
                sig1 = _build_step_signature(window[0])
                sig2 = _build_step_signature(window[1])
                if sig1 and sig2 and current_sig and sig1 == sig2 == current_sig:
                    # 三次或更多相同操作，可能是重试，只保留一次或两次
                    if not is_meaningful_duplicate:
                        self.stats["duplicatesRemoved"] += 1
                        window.append(step)
                        continue
            
            if not is_meaningful_duplicate and result:
                prev_result_sig = _build_step_signature(result[-1])
                if prev_result_sig and current_sig and prev_result_sig == current_sig:
                    # 普通重复，去重
                    self.stats["duplicatesRemoved"] += 1
                    continue
            
            result.append(step)
            window.append(step)
        
        return result
    
    def _merge_related_operations(self, steps):
        """合并相关的操作序列"""
        result = []
        i = 0
        
        while i < len(steps):
            current = steps[i]
            
            # 检查是否可以和后续步骤合并
            if i + 1 < len(steps):
                next_step = steps[i + 1]
                
                # 检查是否是"点击 + 输入"的模式
                current_sig = _build_step_signature(current)
                next_sig = _build_step_signature(next_step)
                
                if ("click:" in current_sig and ("send_keys:" in next_sig or "type_text:" in next_sig)):
                    current_control = str(((current.get("actionConfig", {}) or {}).get("controlId", ""))).strip()
                    next_control = str(((next_step.get("actionConfig", {}) or {}).get("controlId", ""))).strip()
                    current_path = str(((current.get("inspectHints", {}) or {}).get("uiPath", ""))).strip()
                    next_path = str(((next_step.get("inspectHints", {}) or {}).get("uiPath", ""))).strip()
                    if (current_control and next_control and current_control == next_control) or (current_path and current_path == next_path):
                        next_step["actionConfig"]["action"] = "type_text"
                        next_step["actionConfig"]["controlId"] = next_control or current_control
                        if current.get("controls") and not next_step.get("controls"):
                            next_step["controls"] = copy.deepcopy(current.get("controls", []))
                        if current.get("inspectHints") and not next_step.get("inspectHints", {}).get("controlName"):
                            next_step["inspectHints"] = copy.deepcopy(current.get("inspectHints", {}))
                        if current.get("windowTitle") and not next_step.get("windowTitle"):
                            next_step["windowTitle"] = current.get("windowTitle", "")
                        next_step["name"] = str(next_step.get("name", "")).replace("输入 ", "在 ", 1)
                        next_step["notes"] = (next_step.get("notes", "") + " [已吸收前一步点击，自动转为可直接输入]").strip()
                        self.stats["sequencesMerged"] += 1
                        i += 1
                        result.append(next_step)
                        i += 1
                        continue
                    # 这是一个常见的模式：点击输入框然后输入
                    # 保留两个步骤，但在备注中说明它们的关系
                    current["notes"] = current.get("notes", "") + " [激活输入框]"
                    next_step["notes"] = next_step.get("notes", "") + " [输入内容]"
                    self.stats["sequencesMerged"] += 1
            
            result.append(current)
            i += 1
        
        return result


def _refine_step_names(steps):
    """优化步骤名称，使其更符合实际业务含义"""
    for step in steps:
        inspect_hints = step.get("inspectHints", {})
        ui_path = inspect_hints.get("uiPath", "")
        action_config = step.get("actionConfig", {})
        action_name = action_config.get("action", "")
        
        # 为控件生成更友好的名称
        if ui_path:
            leaf = _get_leaf_segment(ui_path)
            friendly_name = leaf.get("name", "") or leaf.get("controlType", "")
            if friendly_name:
                # 更新步骤名称
                if action_name == "click":
                    step["name"] = f"点击 {friendly_name}"
                elif action_name == "double_click":
                    step["name"] = f"双击 {friendly_name}"
                elif action_name == "send_keys":
                    text = action_config.get("text", "")
                    if text:
                        step["name"] = f"在 {friendly_name} 输入: {text}"
                    else:
                        step["name"] = f"在 {friendly_name} 输入"
                elif action_name == "type_text":
                    text = action_config.get("text", "")
                    if text:
                        step["name"] = f"在 {friendly_name} 输入: {text}"
                    else:
                        step["name"] = f"在 {friendly_name} 输入"
    
    return steps


def _cleanup_generated_steps(steps):
    cleaned_steps = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        cleaned = {
            key: value
            for key, value in step.items()
            if not str(key).startswith("_")
        }
        cleaned_steps.append(cleaned)
    return cleaned_steps


def _collect_conversion_review_items(steps):
    review_items = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        hints = [str(item).strip() for item in step.get("_reviewHints", []) if str(item).strip()]
        if not hints:
            continue
        review_items.append(
            {
                "stepId": str(step.get("id", "")).strip(),
                "stepName": str(step.get("name", "")).strip(),
                "hints": hints,
            }
        )
    return review_items


def convert_recorder_script_to_flow(script_path, output_json_path=None, control_map_dir=CONTROL_MAP_DIR):
    script_text = _read_text(script_path)
    module_node = ast.parse(script_text, filename=script_path)
    raw_steps = []
    control_library = _load_control_map_definitions(control_map_dir)
    stats = {
        "droppedNoOpDragCount": 0,
        "droppedRecorderLines": [],
        "droppedDuplicateCount": 0,
        "waitInferred": 0,
        "sequencesMerged": 0,
        "noiseFiltered": 0,
        "controlMapMatchedCount": 0,
        "controlMapLoadedCount": len(control_library),
        "runtimeParamBindings": 0,
    }
    
    # 1. 初始解析
    _walk_statements(module_node.body, [], raw_steps, stats, control_library=control_library)
    
    # 2. 语义分析和智能处理
    analyzer = SemanticAnalyzer()
    processed_steps = analyzer.analyze(raw_steps)
    
    # 更新统计信息
    stats["droppedDuplicateCount"] = analyzer.stats["duplicatesRemoved"]
    stats["waitInferred"] = analyzer.stats["waitInferred"]
    stats["sequencesMerged"] = analyzer.stats["sequencesMerged"]
    stats["noiseFiltered"] = analyzer.stats["noiseFiltered"]
    
    # 3. 优化步骤名称
    processed_steps = _refine_step_names(processed_steps)
    review_items = _collect_conversion_review_items(processed_steps)
    processed_steps = _cleanup_generated_steps(processed_steps)
    
    # 4. 重新生成不重复的 step id
    seen_ids = set()
    for i, step in enumerate(processed_steps):
        base_id = _sanitize_step_id(step["name"], fallback_prefix=f"step_{i+1}")
        step["id"] = _unique_step_id(base_id, seen_ids)
    
    payload = {
        "version": "1.0",
        "project": os.path.splitext(os.path.basename(script_path))[0],
        "description": "由 pywinauto_recorder 录制脚本智能转换生成的 action 流程骨架（已语义分析优化）。",
        "lastUpdated": datetime.now().isoformat(timespec="seconds"),
        "runtimeConfig": {
            "gmExe": "",
            "sourceFilePath": "",
            "outputDir": "",
            "projectionFilePath": "",
        },
        "flowPackages": [],
        "steps": processed_steps,
        "conversionMeta": {
            "sourceScript": script_path,
            "rawSteps": len(raw_steps),
            "totalSteps": len(processed_steps),
            "actionSteps": sum(1 for step in processed_steps if step.get("actionType") == "action"),
            "placeholderSteps": sum(1 for step in processed_steps if step.get("actionType") == "placeholder"),
            "droppedNoOpDragCount": stats["droppedNoOpDragCount"],
            "droppedDuplicateCount": stats["droppedDuplicateCount"],
            "waitInferred": stats["waitInferred"],
            "sequencesMerged": stats["sequencesMerged"],
            "noiseFiltered": stats["noiseFiltered"],
            "controlMapMatchedCount": stats["controlMapMatchedCount"],
            "controlMapLoadedCount": stats["controlMapLoadedCount"],
            "runtimeParamBindings": stats["runtimeParamBindings"],
            "suspiciousStepCount": len(review_items),
            "controlMapDir": control_map_dir,
            "droppedRecorderLines": stats["droppedRecorderLines"],
            "reviewItems": review_items,
        },
    }
    
    if output_json_path:
        _write_json(output_json_path, payload)
    return payload


def build_arg_parser():
    parser = argparse.ArgumentParser(description="把 pywinauto_recorder Python 脚本智能转换为 action 流程骨架")
    parser.add_argument("script", help="输入 recorder Python 文件路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_JSON, help="输出 JSON 路径")
    parser.add_argument("--control-map-dir", default=CONTROL_MAP_DIR, help="控件库目录，默认读取项目 control_maps")
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    payload = convert_recorder_script_to_flow(args.script, args.output, control_map_dir=args.control_map_dir)
    meta = payload["conversionMeta"]
    info_parts = [f"原始步骤: {meta['rawSteps']}", f"最终步骤: {meta['totalSteps']}"]
    if meta.get('droppedDuplicateCount', 0) > 0:
        info_parts.append(f"去重: -{meta['droppedDuplicateCount']}")
    if meta.get('droppedNoOpDragCount', 0) > 0:
        info_parts.append(f"无效拖拽: -{meta['droppedNoOpDragCount']}")
    if meta.get('noiseFiltered', 0) > 0:
        info_parts.append(f"过滤噪声: -{meta['noiseFiltered']}")
    if meta.get('sequencesMerged', 0) > 0:
        info_parts.append(f"序列合并: {meta['sequencesMerged']}")
    if meta.get('controlMapMatchedCount', 0) > 0:
        info_parts.append(f"控件库命中: {meta['controlMapMatchedCount']}/{meta.get('actionSteps', 0)}")
    info_parts.extend([
        f"action: {meta['actionSteps']}",
        f"placeholder: {meta['placeholderSteps']}",
        f"输出: {args.output}"
    ])
    print("已完成 recorder 智能转换: " + ", ".join(info_parts))


if __name__ == "__main__":
    main()
