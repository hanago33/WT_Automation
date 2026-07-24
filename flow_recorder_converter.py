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
import wt_flow_editor_utils


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT_JSON = os.path.join(BASE_DIR, "converted_recorder_flow.json")
CONTROL_MAP_DIR = os.path.join(BASE_DIR, "control_maps")
CONTROL_CANDIDATES_DIR = os.path.join(CONTROL_MAP_DIR, "_candidates")

SUPPORTED_ACTIONS = {
    "click": "click",
    "right_click": "right_click",
    "double_click": "double_click",
    "double_right_click": "double_right_click",
    "drag_and_drop": "drag_and_drop",
    "mouse_wheel": "mouse_wheel",
    "send_keys": "send_keys",
    # 阶段一新增：补齐 pywinauto_recorder 支持但原转换器未覆盖的 action
    "find": "wait_for_control",
    "menu_click": "menu_select",
    "set_text": "type_text",
    "set_combobox": "select_dropdown_item_runtime",
}

UNSUPPORTED_ACTIONS = {
    "move",
}

# Recorder 中需要特殊 AST 处理的 action（参数格式与通用模式不同）
SPECIAL_HANDLING_ACTIONS = {
    "find",         # 返回 wrapper，参数只有路径
    "menu_click",   # 参数是 "File->Open" 格式的菜单路径
    "set_text",     # 两个参数：(路径, 文本值)
    "set_combobox", # 两个参数：(路径, 选中值)
}

# ClawBridge 风格的三级置信度阈值（用于控件匹配评分）
CONFIDENCE_HIGH = 0.95    # automation_id 精确命中 → 纯机械回放
CONFIDENCE_MEDIUM = 0.70  # name+type+parent 匹配 → 机械+视觉验证
CONFIDENCE_LOW = 0.60     # type+proximity 匹配 → AI/LLM 辅助回放

def _build_action_timing_config(action_name):
    normalized_action = SUPPORTED_ACTIONS.get(action_name, action_name)
    return build_action_default_config(normalized_action)


def _strip_wrapping_quotes(text):
    return wt_flow_editor_utils.strip_wrapping_quotes(text)


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


def _extract_segment_found_index(segment_text):
    """提取 pywinauto_recorder 段尾 #[<范围>,<索引>] 中的同级序号（父容器内第 N 个），无则 -1。

    录制器会把同级消歧索引写成 "Name||ControlType#[<匹配路径>,<index>]"，
    例如 "打开||Button#[带号:||ComboBox,0]"、"||Image#[...,4]"、"Custom#[1,3]"。
    ] 前的最后一个整数即 found_index。
    """
    base, _coords = _split_coordinate_suffix(segment_text)
    matched = re.search(r'#\[[^\]]*?(-?\d+)\]$', str(base or "").strip())
    if not matched:
        return -1
    try:
        return int(matched.group(1))
    except (TypeError, ValueError):
        return -1


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
    
    json_files = []
    for root, _, files in os.walk(control_map_dir):
        for file_name in files:
            if file_name.lower().endswith(".json"):
                json_files.append(os.path.join(root, file_name))
                
    for file_path in sorted(json_files, reverse=True):
        file_name = os.path.basename(file_path)
        try:
            with open(file_path, "r", encoding="utf-8") as file_obj:
                payload = json.load(file_obj)
        except Exception as e:
            print(f"Warning: Failed to load control map file {file_path}: {e}")
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
    """对候选控件库条目评分，返回 (score, reasons, confidence)。
    
    借鉴 ClawBridge 三级置信度策略：
    - confidence >= 0.95: automation_id 精确命中 → 纯机械回放
    - confidence >= 0.70: name+type+parent 匹配 → 机械+视觉验证
    - confidence >= 0.60: type+proximity 匹配 → AI/LLM 辅助回放
    """
    leaf = _get_leaf_segment(full_path)
    leaf_name = _normalize_match_text(leaf.get("name", ""))
    leaf_type = _normalize_match_text(leaf.get("controlType", ""))
    window_title = _normalize_match_text(_find_window_title(full_path))
    ui_path = _normalize_match_text(full_path)
    recorder_tokens = set(_split_identifier_tokens(ui_path))
    score = 0
    reasons = []
    confidence = 0.0

    candidate_type = _normalize_match_text(candidate.get("controlType", ""))
    if leaf_type and candidate_type and leaf_type.lower() == candidate_type.lower():
        score += 18
        reasons.append("控件类型一致")
        confidence = max(confidence, 0.3)

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
            confidence = max(confidence, 0.6)
        elif window_title in candidate_window or candidate_window in window_title:
            score += 10
            reasons.append("窗口标题相近")
            confidence = max(confidence, 0.5)

    if leaf_name:
        if automation_id and leaf_name == automation_id:
            score += 120
            reasons.append("AutomationId 精确命中")
            confidence = max(confidence, CONFIDENCE_HIGH)  # 1.0 → 纯机械回放
        elif leaf_ui_name and leaf_name == leaf_ui_name:
            score += 95
            reasons.append("UIPath 叶子名精确命中")
            confidence = max(confidence, CONFIDENCE_MEDIUM + 0.15)
        elif friendly_name and leaf_name == friendly_name:
            score += 80
            reasons.append("控件名称精确命中")
            confidence = max(confidence, CONFIDENCE_MEDIUM + 0.05)
        elif automation_id and leaf_name.lower() in automation_id.lower():
            score += 38
            reasons.append("AutomationId 包含叶子名")
            confidence = max(confidence, CONFIDENCE_MEDIUM - 0.05)
        elif candidate_path and leaf_name.lower() in candidate_path.lower():
            score += 24
            reasons.append("UIPath 包含叶子名")
            confidence = max(confidence, CONFIDENCE_MEDIUM - 0.10)
        elif class_name and leaf_name.lower() == class_name.lower():
            score += 18
            reasons.append("类名命中")
            confidence = max(confidence, 0.5)

    # 新增：祖先链匹配（借鉴 ClawBridge name+type+parent 策略）
    ancestor_signatures = _build_ancestor_signatures(full_path)
    candidate_ancestors = candidate.get("definition", {}).get("inspectData", {}).get("ancestors", [])
    if ancestor_signatures and candidate_ancestors:
        matched_ancestors = 0
        for rec_sig in ancestor_signatures:
            for cand_sig in candidate_ancestors:
                cand_sig_norm = _normalize_match_text(cand_sig if isinstance(cand_sig, str) else "")
                if rec_sig and cand_sig_norm and rec_sig.lower() in cand_sig_norm.lower():
                    matched_ancestors += 1
                    break
        if matched_ancestors >= 2:
            score += 22
            reasons.append(f"祖先链匹配 {matched_ancestors} 层")
            confidence = max(confidence, CONFIDENCE_MEDIUM)  # 0.85 → name+type+parent
        elif matched_ancestors >= 1:
            score += 12
            reasons.append(f"祖先链匹配 {matched_ancestors} 层")
            confidence = max(confidence, 0.65)

    overlap_tokens = recorder_tokens & set(candidate.get("searchTokens", set()))
    if overlap_tokens:
        token_score = min(28, len(overlap_tokens) * 4)
        score += token_score
        reasons.append(f"路径语义重合 {len(overlap_tokens)} 项")
        confidence = max(confidence, 0.55)

    action_type_bonus = {
        "click": {"Button", "MenuItem", "TabItem", "TreeItem", "ListItem"},
        "double_click": {"Button", "ListItem", "TreeItem", "TabItem"},
        "right_click": {"Pane", "Button", "TreeItem", "ListItem"},
        "type_text": {"Edit", "ComboBox", "Document"},
        "send_keys": {"Edit", "ComboBox", "Document"},
        "wait_for_control": {"Window", "Button", "Edit", "Custom", "Pane"},
        "mouse_wheel": {"Pane", "Custom", "List", "Tree"},
        "menu_select": {"MenuItem", "Button"},
        "select_dropdown_item_runtime": {"ComboBox"},
    }
    preferred_types = action_type_bonus.get(step_action, set())
    if candidate_type in preferred_types:
        score += 6
        reasons.append("动作类型偏好匹配")

    return score, reasons, confidence


def _match_control_from_library(full_path, step_action, control_library):
    if not full_path or not control_library:
        return None
    best_match = None
    best_score = -1
    best_reasons = []
    best_confidence = 0.0
    for candidate in control_library:
        score, reasons, confidence = _score_control_map_match(full_path, step_action, candidate)
        if score > best_score:
            best_score = score
            best_match = candidate
            best_reasons = reasons
            best_confidence = confidence
    if best_match is None or best_score < 32:
        return None
    matched = copy.deepcopy(best_match["definition"])
    matched["notes"] = (
        (matched.get("notes", "") + " | " if matched.get("notes", "") else "")
        + f"recorder 自动匹配命中，评分={best_score}，置信度={best_confidence:.2f}，依据={'; '.join(best_reasons) or '综合相似度'}。"
    )
    return {
        "definition": matched,
        "score": best_score,
        "confidence": best_confidence,
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
        # 新增 action 的步骤命名
        "find": f"等待 {control_name} 出现",
        "wait_for_control": f"等待 {control_name} 出现",
        "menu_click": f"菜单 {text_value or control_name}",
        "menu_select": f"菜单 {text_value or control_name}",
        "set_text": f"设置 {control_name} 为 {text_value}",
        "type_text": f"在 {control_name} 输入 {text_value}",
        "set_combobox": f"设置 {control_name} 选中 {text_value}",
        "select_dropdown_item_runtime": f"设置 {control_name} 选中 {text_value}",
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


# 录制器路径段自带控件类型（Name||ControlType），转换时据此把 click+send_keys
# 提升为语义化动作：Edit/Document -> type_text，ComboBox -> select_dropdown_item_runtime。
_EDIT_CONTROL_TOKENS = ("edit", "document")
_COMBOBOX_CONTROL_TOKENS = ("combo",)


def _classify_control_kind(control_type):
    """按录制路径里的 ControlType 归类控件语义。

    返回 'combobox' / 'edit' / ''(未知或无需语义化)。
    仅用于把“点击输入框/下拉框 + 键入”这类拆分步骤合并成可直接执行的语义动作，
    因此故意不把静态 Text/Window 归入可输入类，避免误合并坐标点击或快捷键。
    """
    lowered = str(control_type or "").strip().lower()
    if not lowered:
        return ""
    if any(token in lowered for token in _COMBOBOX_CONTROL_TOKENS):
        return "combobox"
    if any(token in lowered for token in _EDIT_CONTROL_TOKENS):
        return "edit"
    return ""


def _extract_combobox_value(send_keys_text):
    """从 send_keys 文本中提取下拉框目标选项的字面值。

    去除 {ENTER}/{TAB}/{DOWN} 等按键标记与 ^ % + 修饰符；
    若只剩纯键盘导航（无任何字面文本），返回 "" —— 此时无法还原选中项。
    """
    text = _normalize_send_keys_text(send_keys_text)
    without_braces = re.sub(r"\{[^}]*\}", "", text)      # 去除 {ENTER}、{DOWN 3} 等按键标记
    without_mods = re.sub(r"[\^%+]", "", without_braces)  # 去除 Ctrl(^)/Alt(%)/Shift(+) 修饰符
    return without_mods.strip().strip('"').strip("'").strip()


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


def _build_ancestor_signatures(full_path):
    """从录制路径的祖先链（除叶子外）生成定位器可用的祖先签名列表，提升同名控件的唯一性。"""
    segments = _split_uipath(full_path)
    signatures = []
    for seg in segments[:-1]:
        parsed = _parse_segment(seg)
        name = _normalize_match_text(parsed.get("name", ""))
        control_type = _normalize_match_text(parsed.get("controlType", ""))
        if not name and not control_type:
            continue
        sig = " | ".join(item for item in [name, control_type] if item)
        if sig and sig not in signatures:
            signatures.append(sig)
    return signatures


def _build_control_definition(control_id, full_path, window_title, stats=None, screenshot_key=""):
    """从录制路径构建控件定义，注入 a11y 富化元数据。
    
    借鉴 ClawBridge 的 a11y enrichment 思路：
    - 从 UIPath 各段解析 name/controlType/className/frameworkId
    - 推断 frameworkId（WPF vs Win32 vs uia）
    - 若有截图，关联 templateKey 供模板匹配层使用
    """
    target_method, target_value = _build_target_method_and_value(full_path)
    leaf = _get_leaf_segment(full_path)
    path_depth = len(_split_uipath(full_path))
    # 路径足够深时，推荐用完整 UIA 路径作为唯一选择器（#7）
    use_ui_path = path_depth >= 2 and bool(_normalize_match_text(full_path))
    recommended_method, recommended_value = (
        ("ui_path", full_path) if use_ui_path else (target_method, target_value)
    )
    if use_ui_path and stats is not None:
        stats["uniquePathSelectors"] = stats.get("uniquePathSelectors", 0) + 1

    # a11y 富化：从 UIPath 各段解析更多 accessibility 信息
    segments = _split_uipath(full_path)
    # 保留录制器捕获的叶子同级序号（父链引导定位：列表第 N 项等同类同名控件的消歧依据）
    leaf_found_index = _extract_segment_found_index(segments[-1]) if segments else -1
    a11y_name = _normalize_match_text(leaf.get("name", ""))
    a11y_control_type = _normalize_match_text(leaf.get("controlType", ""))
    a11y_class_name = ""
    a11y_automation_id = ""
    a11y_framework_id = ""

    # 从 UIPath 叶子段推断 className 和 automationId
    # pywinauto_recorder 的 UIPath 格式: "Name||ControlType" 或 "Name||ControlType#Index"
    raw_leaf = leaf.get("raw", "")
    leaf_parsed = _parse_segment(raw_leaf) if raw_leaf else leaf
    leaf_raw_name = _normalize_match_text(leaf_parsed.get("name", ""))
    leaf_raw_type = _normalize_match_text(leaf_parsed.get("controlType", ""))

    # 如果 name 看起来像 automationId（驼峰命名、包含下划线、含 ViewModel），
    # 则将其作为 automationId
    if leaf_raw_name and re.search(r'[A-Z][a-z]+.*(ViewModel|Button|Control|View|Window|Tab|Item|Box|List|[A-Z]{2,})', leaf_raw_name):
        a11y_automation_id = leaf_raw_name
    # 如果 name 是常见类名格式，作为 className
    if leaf_raw_name and re.match(r'^[A-Z][a-zA-Z0-9_]*$', leaf_raw_name) and len(leaf_raw_name) >= 3:
        a11y_class_name = leaf_raw_name

    # 从祖先段推断 frameworkId
    for seg in segments:
        parsed = _parse_segment(seg)
        ct = _normalize_match_text(parsed.get("controlType", ""))
        name = _normalize_match_text(parsed.get("name", ""))
        combined = (name + ct).lower()
        if ct.lower() == "window" and not a11y_framework_id:
            # WPF 窗口的特征：控件名包含 ViewModel，或路径中出现 WPF 特有模式
            if "viewmodel" in combined or "wpf" in combined:
                a11y_framework_id = "WPF"
            elif "mfc" in combined or "afx" in combined:
                a11y_framework_id = "MFC"
            elif "winforms" in combined or "form1" in combined or "system.windows.forms" in combined:
                a11y_framework_id = "WinForm"
            else:
                # 默认根据控件类型推断
                if a11y_control_type in {"Button", "Edit", "ComboBox", "TabItem", "MenuItem", "ListBox", "ListView"}:
                    # 常见于 WPF/WinForm/通用 Win32 控件
                    pass  # 保持空，运行时自动探测

    # 如果祖先段中有 WPF 标记
    if not a11y_framework_id:
        for seg in segments:
            if "wpf" in _normalize_match_text(str(_parse_segment(seg).get("name", ""))).lower():
                a11y_framework_id = "WPF"
                break

    # 统计 a11y 富化：只要推断出至少一项元数据就算富化成功
    if stats is not None and (a11y_name or a11y_control_type or a11y_automation_id or a11y_class_name or a11y_framework_id):
        stats["a11yEnrichedCount"] = stats.get("a11yEnrichedCount", 0) + 1

    control_definition = {
        "id": _sanitize_control_id(control_id, "target_control"),
        "name": leaf.get("name", "") or leaf.get("controlType", "") or control_id,
        "role": "来自 recorder 自动转换的目标控件",
        "targetMethod": target_method,
        "targetValue": target_value,
        "windowTitle": window_title,
        "uiPath": full_path,
        "templateKey": _normalize_match_text(screenshot_key),
        "notes": "由 pywinauto_recorder 录制脚本自动转换生成（含 a11y 富化）；若控件库未命中，可继续补充 Inspect/Accessibility 信息。",
        "inspectData": {
            "name": a11y_name or leaf_raw_name,
            "controlType": a11y_control_type or leaf_raw_type,
            "localizedControlType": "",
            "automationId": a11y_automation_id,
            "className": a11y_class_name,
            "frameworkId": a11y_framework_id,
            "ancestors": _build_ancestor_signatures(full_path),
            "recommendedTargetMethod": recommended_method,
            "recommendedTargetValue": recommended_value,
        },
        "auxChecks": [],
        "source": "recorder",
    }
    if leaf.get("coords"):
        coords_text = _format_coords(leaf.get("coords"))
        if coords_text:
            control_definition["notes"] += f" 点击偏移={coords_text}。"
    if leaf_found_index >= 0:
        control_definition["inspectData"]["foundIndex"] = leaf_found_index
        if stats is not None:
            stats["foundIndexPreserved"] = stats.get("foundIndexPreserved", 0) + 1
    if screenshot_key:
        control_definition["notes"] += f" 已关联录制截图 {screenshot_key} 供模板匹配。"
        if stats is not None:
            stats["screenshotLinkedCount"] = stats.get("screenshotLinkedCount", 0) + 1
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

    # 构建自适应定位 fallback 链
    full_path = control_definition.get("uiPath", "") or step.get("inspectHints", {}).get("uiPath", "")
    template_key = control_definition.get("templateKey", "") or step.get("inspectHints", {}).get("templateKey", "")
    leaf = _get_leaf_segment(full_path) if full_path else {}
    step["fallbackChain"] = _build_fallback_chain(control_definition, full_path, template_key, leaf)

    return step


def _build_fallback_chain(control_definition, full_path, template_key="", leaf=None):
    """为步骤构建自适应定位 fallback 链。

    生成 4 级降级策略，执行器在主定位方法失败后按序尝试：
      L1: automation_id + control_type 精确命中（已在主 targetMethod 中，不重复）
      L2: ui_path 完整路径定位
      L3: 模板匹配（录制截图）
      L4: 坐标降级（录制时屏幕绝对坐标）
    """
    fallbacks = []
    if leaf is None and full_path:
        leaf = _get_leaf_segment(full_path)

    # L2: uiPath 完整路径定位
    if full_path:
        fallbacks.append({
            "method": "ui_path",
            "value": full_path,
            "confidence": 0.65,
            "type": "ui_path_search",
            "description": "使用录制时的 UIPath 完整祖先路径在窗口树中搜索定位",
        })

    # L3: 模板匹配（录制截图）
    if template_key:
        fallbacks.append({
            "method": "template",
            "value": template_key,
            "confidence": 0.50,
            "type": "template",
            "description": "使用录制截图进行视觉模板匹配定位",
        })

    # L4: 坐标降级（录制时的屏幕绝对坐标）
    if leaf and leaf.get("coords"):
        coords = leaf.get("coords")
        if isinstance(coords, dict) and coords.get("x") is not None:
            fallbacks.append({
                "method": "coordinate",
                "value": {"x": float(coords.get("x", 0)), "y": float(coords.get("y", 0))},
                "confidence": 0.35,
                "type": "coordinate",
                "description": "使用录制时的屏幕绝对坐标（可能因窗口位置/DPI变化而偏移）",
            })

    return fallbacks


def _save_control_candidate(control_definition, full_path, window_title, script_path="", line_no=0):
    """将未命中控件库的控件信息保存为候选条目，供后续人工确认合并。

    候选条目存入 control_maps/_candidates/，每条记录包含：
      - 控件定义（含自动推断的 targetMethod/targetValue）
      - 来源脚本路径和行号，可回溯验证
      - 去重：同一 (full_path, window_title) 只保存一次
    """
    if not full_path:
        return None

    os.makedirs(CONTROL_CANDIDATES_DIR, exist_ok=True)

    # 生成唯一文件名（基于 UIPath + 窗口标题的 hash）
    dedupe_key = f"{full_path}|{window_title}"
    safe_hash = ""
    for ch in dedupe_key:
        if ch.isalnum() or ch in "_-.":
            safe_hash += ch
        else:
            safe_hash += "_"
    safe_hash = safe_hash[:80].strip("_")
    if not safe_hash:
        safe_hash = f"candidate_{line_no}"

    candidate_file = os.path.join(CONTROL_CANDIDATES_DIR, f"{safe_hash}.json")

    # 如果已存在同名候选，跳过
    if os.path.exists(candidate_file):
        return None

    leaf = _get_leaf_segment(full_path)
    candidate_entry = {
        "_candidateMeta": {
            "sourceScript": script_path,
            "sourceLine": line_no,
            "discoveredAt": datetime.now().isoformat(timespec="seconds"),
            "status": "pending_review",
            "uipath": full_path,
            "windowTitle": window_title,
            "leafName": leaf.get("name", ""),
            "leafControlType": leaf.get("controlType", ""),
        },
        "controlDefinition": control_definition,
    }

    try:
        with open(candidate_file, "w", encoding="utf-8") as fh:
            json.dump(candidate_entry, fh, ensure_ascii=False, indent=2)
        return candidate_file
    except Exception:
        return None


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
        # 内部字段（_ 前缀，最终输出前会被 _cleanup_generated_steps 剔除）：
        # 保留录制器原始的叶子 ControlType，不会被控件库匹配覆盖，
        # 供 _merge_related_operations 判断点击目标是 Edit/ComboBox。
        "_recorderControlType": leaf_segment.get("controlType", ""),
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
            _apply_control_definition_to_step(step, _build_control_definition(control_id, full_path, window_title, stats=stats), line_no)
    else:
        if not step.get("controls"):
            _apply_control_definition_to_step(step, _build_control_definition(control_id, full_path, window_title, stats=stats), line_no)

    if not matched_control:
        _append_step_review_hint(step, "未命中控件库，建议补充控件库或检查目标控件定位")
        # 双向同步：未命中的控件自动保存为候选条目，供后续确认合并入库
        cdef = step.get("controls", [{}])[0] if step.get("controls") else _build_control_definition(control_id, full_path, window_title, stats=stats)
        script_src = stats.get("scriptPath", "") if stats else ""
        saved = _save_control_candidate(cdef, full_path, window_title, script_path=script_src, line_no=line_no)
        if saved and stats is not None:
            stats["candidateCount"] = stats.get("candidateCount", 0) + 1
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
    source_control = _build_control_definition("source_control", full_source_path, window_title, stats=stats)
    target_control = _build_control_definition("target_control", full_target_path, window_title, stats=stats)
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
        _apply_control_definition_to_step(step, _build_control_definition("target_control", full_path, window_title, stats=stats), line_no)
    return step


# ===== 阶段一新增：find / menu_click / set_text / set_combobox 步骤构建器 =====

def _build_find_step(full_path, line_no, control_library=None, stats=None):
    """将 recorder 的 find(u"path") 转为 wait_for_control action。
    
    recorder 脚本示例: wrapper = find(u"ListBox||List")
    find 函数返回 pywinauto wrapper，本质是等待控件出现并获取其引用。
    """
    window_title = _find_window_title(full_path)
    leaf = _get_leaf_segment(full_path)
    step_name = _derive_step_name("find", full_path)
    control_id = _sanitize_control_id(leaf.get("name", "") or leaf.get("controlType", "") or "target_control", "target_control")
    step = _build_action_step_base("wait_for_control", line_no, step_name, window_title, full_path)
    matched_control = _match_control_from_library(full_path, "wait_for_control", control_library or [])
    if matched_control:
        _apply_control_definition_to_step(step, matched_control["definition"], line_no, match_meta=matched_control)
        if stats is not None:
            stats["findActionCount"] = stats.get("findActionCount", 0) + 1
            stats["controlMapMatchedCount"] += 1
    else:
        _apply_control_definition_to_step(step, _build_control_definition(control_id, full_path, window_title, stats=stats), line_no)
        if stats is not None:
            stats["findActionCount"] = stats.get("findActionCount", 0) + 1
        _append_step_review_hint(step, "find 转 wait_for_control：建议确认等待条件和超时时间")
    step["actionConfig"]["action"] = "wait_for_control"
    return step


def _build_menu_click_step(menu_path, line_no, control_library=None, stats=None):
    """将 recorder 的 menu_click(u"Edit->Font") 转为 menu_select action。
    
    解析 "->" 分隔的菜单路径，生成带 menuPath 参数的步骤。
    每个菜单层级作为独立的子步骤信息保存在 stepParams 中，
    方便后续执行器逐个点击菜单项。
    """
    menu_segments = [seg.strip() for seg in str(menu_path or "").split("->") if seg.strip()]
    if not menu_segments:
        return _build_placeholder_step("menu_click", "", menu_path, line_no)
    
    step_name = _derive_step_name("menu_click", "", text_value="->".join(menu_segments))
    step = {
        "id": "",
        "name": step_name,
        "stage": "converted",
        "strategy": "recorder -> action",
        "actionType": "action",
        "enabled": True,
        "codeSymbol": "",
        "codeReference": "",
        "description": f"由 recorder 第 {line_no} 行 menu_click 自动转换。",
        "successLog": step_name,
        "windowTitle": "",
        "inspectHints": {
            "controlName": menu_segments[0],
            "className": "MenuItem",
            "automationId": "",
            "controlType": "MenuItem",
            "uiPath": "->".join(menu_segments),
            "templateKey": "",
        },
        "controls": [],
        "stepParams": {
            "menuSegments": menu_segments,
        },
        "actionConfig": {
            **_build_action_timing_config("menu_select"),
            "action": "menu_select",
            "menuPath": "->".join(menu_segments),
            "sourceRecorderLine": line_no,
        },
        "auxChecks": [],
        "fallbacks": [],
        "notes": f"菜单路径: {' -> '.join(menu_segments)}。由 recorder menu_click 转换，执行时依次点击各级菜单项。",
    }
    if stats is not None:
        stats["menuClickActionCount"] = stats.get("menuClickActionCount", 0) + 1
    return step


def _build_set_text_step(full_path, text_value, line_no, control_library=None, stats=None):
    """将 recorder 的 set_text(u"path", "value") 转为 type_text action。
    
    set_text 是 pywinauto_recorder 提供的直接设置文本函数，比 click+send_keys 更可靠。
    """
    window_title = _find_window_title(full_path)
    leaf = _get_leaf_segment(full_path)
    step_name = _derive_step_name("set_text", full_path, text_value=text_value)
    control_id = _sanitize_control_id(leaf.get("name", "") or leaf.get("controlType", "") or "target_control", "target_control")
    step = _build_action_step_base("type_text", line_no, step_name, window_title, full_path)
    
    matched_control = _match_control_from_library(full_path, "type_text", control_library or [])
    if matched_control:
        _apply_control_definition_to_step(step, matched_control["definition"], line_no, match_meta=matched_control)
        if stats is not None:
            stats["controlMapMatchedCount"] += 1
    else:
        _apply_control_definition_to_step(step, _build_control_definition(control_id, full_path, window_title, stats=stats), line_no)
    
    step["actionConfig"]["text"] = _normalize_send_keys_text(text_value)
    step["actionConfig"]["action"] = "type_text"
    if stats is not None:
        stats["setTextActionCount"] = stats.get("setTextActionCount", 0) + 1
    return step


def _build_set_combobox_step(full_path, selected_value, line_no, control_library=None, stats=None):
    """将 recorder 的 set_combobox(u"path", "value") 转为 select_dropdown_item_runtime action。
    
    执行器已支持 select_dropdown_item_runtime（运行时展开下拉并枚举候选点击），
    将 selected_value 写入 control 的 inspectData.recommendedTargetValue，
    供 get_dropdown_runtime_target_texts() 在回放时匹配正确项。
    """
    window_title = _find_window_title(full_path)
    leaf = _get_leaf_segment(full_path)
    step_name = _derive_step_name("set_combobox", full_path, text_value=selected_value)
    control_id = _sanitize_control_id(leaf.get("name", "") or leaf.get("controlType", "") or "target_control", "target_control")
    step = _build_action_step_base("select_dropdown_item_runtime", line_no, step_name, window_title, full_path)
    
    matched_control = _match_control_from_library(full_path, "select_dropdown_item_runtime", control_library or [])
    if matched_control:
        _apply_control_definition_to_step(step, matched_control["definition"], line_no, match_meta=matched_control)
        if stats is not None:
            stats["controlMapMatchedCount"] += 1
    else:
        ctrl_def = _build_control_definition(control_id, full_path, window_title, stats=stats)
        _apply_control_definition_to_step(step, ctrl_def, line_no)
    
    # 将选中的值设置到 control 的 inspectData 中，供运行时下拉匹配使用
    if step.get("controls"):
        ctrl = step["controls"][0]
        ctrl.setdefault("inspectData", {})
        ctrl["inspectData"]["recommendedTargetValue"] = _normalize_send_keys_text(selected_value)
    
    step["actionConfig"]["action"] = "select_dropdown_item_runtime"
    step["actionConfig"]["recommendedTargetValue"] = _normalize_send_keys_text(selected_value)
    if stats is not None:
        stats["setComboboxActionCount"] = stats.get("setComboboxActionCount", 0) + 1
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

    # ===== 阶段一新增：find / menu_click / set_text / set_combobox =====
    if action_name == "find":
        full_path = _combine_uipath(current_scope_path, arg_texts[0] if arg_texts else "")
        return _build_find_step(full_path, line_no, control_library=control_library, stats=stats)

    if action_name == "menu_click":
        menu_path = arg_texts[0] if arg_texts else ""
        # 从 UIPath 上下文推断窗口标题（menu_click 自身不带 UIPath）
        return _build_menu_click_step(menu_path, line_no, control_library=control_library, stats=stats)

    if action_name == "set_text":
        full_path = _combine_uipath(current_scope_path, arg_texts[0] if len(arg_texts) >= 1 else "")
        text_value = arg_texts[1] if len(arg_texts) >= 2 else ""
        if full_path:
            return _build_set_text_step(full_path, text_value, line_no, control_library=control_library, stats=stats)
        return _build_placeholder_step(action_name, current_scope_path, " | ".join(arg_texts), line_no)

    if action_name == "set_combobox":
        full_path = _combine_uipath(current_scope_path, arg_texts[0] if len(arg_texts) >= 1 else "")
        selected_value = arg_texts[1] if len(arg_texts) >= 2 else ""
        if full_path:
            return _build_set_combobox_step(full_path, selected_value, line_no, control_library=control_library, stats=stats)
        return _build_placeholder_step(action_name, current_scope_path, " | ".join(arg_texts), line_no)

    if action_name in UNSUPPORTED_ACTIONS:
        return _build_placeholder_step(action_name, current_scope_path, " | ".join(arg_texts), line_no)

    return None


def _walk_statements(statements, scope_stack, raw_steps, stats, control_library=None):
    """遍历 AST 语句块，解析 recorder 脚本中的操作。
    
    增强了对嵌套 with UIPath 的处理，包括：
    - 多层嵌套的作用域正确拼接
    - 通配符 `*` 路径的处理（表示"在当前窗口下任意路径查找"）
    - 记录嵌套深度以便后续分析
    """
    current_scope = scope_stack[-1] if scope_stack else ""
    for statement in statements:
        if isinstance(statement, ast.With):
            new_scope = current_scope
            for item in statement.items:
                path_text = _extract_uipath_from_expr(item.context_expr)
                if path_text:
                    # 处理通配符路径：* 表示"当前窗口内任意路径"
                    # 如 with UIPath("*->Button||Button"): 表示在窗口下查找 Button
                    if path_text.startswith("*->") or path_text.startswith("*||"):
                        # 去掉通配符前缀，在当前作用域下拼接
                        cleaned_path = path_text[2:] if path_text.startswith("*->") else path_text[2:]
                        new_scope = _combine_uipath(new_scope, cleaned_path)
                    else:
                        new_scope = _combine_uipath(new_scope, path_text)
            # 记录嵌套深度
            if stats is not None:
                max_depth = stats.get("maxNestingDepth", 0)
                stats["maxNestingDepth"] = max(max_depth, len(scope_stack) + 1)
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
        
        if action_name in {"send_keys", "type_text", "set_text"}:
            text = action_config.get("text", "")
            return f"{action_name}:{ui_path}:{text}"
        
        if action_name == "wait_for_control":
            return f"{action_name}:{ui_path}"
        
        if action_name == "menu_select":
            menu_path = action_config.get("menuPath", "")
            return f"{action_name}:{menu_path}"
        
        if action_name == "select_dropdown_item_runtime":
            value = action_config.get("recommendedTargetValue", "")
            return f"select_dropdown_item_runtime:{ui_path}:{value}"
        
        if action_name == "mouse_wheel":
            delta = action_config.get("delta", 0)
            return f"{action_name}:{ui_path}:{delta}"
        
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
            "comboboxRecognized": 0,
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
        """合并相关的操作序列。

        录制器把"文本输入"录成 click(输入框)+send_keys(...)、把"下拉选择"录成
        click(下拉框)+send_keys(...)，且 send_keys 常退化到窗口作用域(||Window)导致
        丢失真实目标控件。这里依据前一步点击控件的 ControlType 做语义化合并：
          - Edit/Document → type_text（用点击控件定位覆盖键入步骤，修复丢失的目标）
          - ComboBox      → select_dropdown_item_runtime（提取并写入 recommendedTargetValue）
        """
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
                
                is_input_next = (
                    "send_keys:" in next_sig
                    or "type_text:" in next_sig
                    or "select_dropdown_item_runtime:" in next_sig
                )
                if "click:" in current_sig and is_input_next:
                    current_hints = current.get("inspectHints", {}) or {}
                    # 命中控件库后 inspectHints.controlType/uiPath 可能被库定义覆盖，
                    # 优先用基础构建时保留的录制器原始 ControlType，再依次回退到
                    # inspectHints.controlType 与 uiPath 叶子段（仍可能携带 ||Edit / ||ComboBox）。
                    click_control_type = str(current.get("_recorderControlType", "")).strip()
                    if not _classify_control_kind(click_control_type):
                        click_control_type = current_hints.get("controlType", "")
                    if not _classify_control_kind(click_control_type):
                        leaf_type = _get_leaf_segment(current_hints.get("uiPath", "")).get("controlType", "")
                        click_control_type = leaf_type or click_control_type
                    click_kind = _classify_control_kind(click_control_type)
                    next_config = next_step.get("actionConfig", {}) or {}
                    current_control = str(((current.get("actionConfig", {}) or {}).get("controlId", ""))).strip()
                    next_control = str((next_config.get("controlId", ""))).strip()
                    current_path = str((current_hints.get("uiPath", ""))).strip()
                    next_path = str(((next_step.get("inspectHints", {}) or {}).get("uiPath", ""))).strip()
                    same_target = (current_control and next_control and current_control == next_control) or (current_path and current_path == next_path)
                    # 当点击的是可输入控件(Edit/ComboBox)时，即使键入步骤已退化到窗口作用域
                    # (目标不同)，也用点击控件的定位覆盖键入步骤，修复丢失的真实目标。
                    if click_kind in ("edit", "combobox") or same_target:
                        keyed_text = str(next_config.get("text", ""))
                        # 用点击步骤的控件定位覆盖键入步骤（键入步骤常丢失真实目标）
                        if current.get("controls"):
                            next_step["controls"] = copy.deepcopy(current.get("controls", []))
                        if current.get("inspectHints"):
                            next_step["inspectHints"] = copy.deepcopy(current.get("inspectHints", {}))
                        if current.get("windowTitle"):
                            next_step["windowTitle"] = current.get("windowTitle", "")
                        next_step["actionConfig"]["controlId"] = current_control or next_control
                        if click_kind == "combobox":
                            combo_value = _extract_combobox_value(keyed_text)
                            next_step["actionConfig"]["action"] = "select_dropdown_item_runtime"
                            next_step["actionConfig"]["recommendedTargetValue"] = combo_value
                            next_step["actionConfig"].pop("text", None)
                            if next_step.get("controls"):
                                ctrl = next_step["controls"][0]
                                ctrl.setdefault("inspectData", {})
                                ctrl["inspectData"]["recommendedTargetValue"] = combo_value
                            leaf_name = current_hints.get("controlName", "") or "下拉框"
                            if combo_value:
                                next_step["name"] = f"在 {leaf_name} 选择 {combo_value}"
                            else:
                                next_step["name"] = f"在 {leaf_name} 选择下拉项"
                                _append_step_review_hint(next_step, "下拉选项为纯键盘导航，无法还原选中值，请手动补充 recommendedTargetValue")
                            next_step["notes"] = (next_step.get("notes", "") + " [已吸收前一步点击，识别为下拉选择]").strip()
                            self.stats["comboboxRecognized"] = self.stats.get("comboboxRecognized", 0) + 1
                        else:
                            next_step["actionConfig"]["action"] = "type_text"
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
                elif action_name == "wait_for_control":
                    step["name"] = f"等待 {friendly_name} 出现"
                elif action_name == "select_dropdown_item_runtime":
                    value = inspect_hints.get("recommendedTargetValue") or action_config.get("recommendedTargetValue", "")
                    if value:
                        step["name"] = f"设置 {friendly_name} 为 {value}"
                    else:
                        step["name"] = f"设置 {friendly_name}"
        elif action_name == "menu_select":
            menu_path = action_config.get("menuPath", "")
            if menu_path:
                step["name"] = f"菜单选择: {menu_path}"
    
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


def _load_screenshot_map(screenshot_dir, script_path):
    """加载录制截图目录，建立 (窗口名, UIPath) → screenshot_filename 的映射。
    
    借鉴 ClawBridge 思路：录制时每步操作都可以伴随截图，
    转换时将这些截图关联到对应步骤的 templateKey，供模板匹配定位层使用。
    
    截图命名约定（与 pywinauto_recorder 兼容）：
    - 按行号命名: screenshot_line_{N}.png
    - 或按时间戳: screenshot_{HHMMSS}.png
    - 同时尝试匹配现有的 image_templates 库
    """
    screenshot_map = {}
    if not os.path.isdir(screenshot_dir):
        return screenshot_map
    
    # 获取脚本基准名称（不含扩展名），用于匹配同批次的截图
    base_name = os.path.splitext(os.path.basename(script_path))[0]
    
    for file_name in sorted(os.listdir(screenshot_dir)):
        file_path = os.path.join(screenshot_dir, file_name)
        if not os.path.isfile(file_path):
            continue
        ext = os.path.splitext(file_name)[1].lower()
        if ext not in {".png", ".jpg", ".jpeg", ".bmp"}:
            continue
        
        # 尝试从文件名解析行号
        line_match = re.search(r'line[_\-]?(\d+)', file_name, re.IGNORECASE)
        if line_match:
            line_no = int(line_match.group(1))
            screenshot_map[line_no] = file_name
            continue
        
        # 尝试从文件名解析步骤序号
        step_match = re.search(r'step[_\-]?(\d+)', file_name, re.IGNORECASE)
        if step_match:
            step_no = int(step_match.group(1))
            # 步骤序号可能对应脚本中的某个位置，暂用负数表示
            screenshot_map[-step_no] = file_name
            continue
        
        # 尝试时间戳匹配（与脚本基准名相同的批次）
        ts_match = re.search(r'(\d{6})', file_name)
        if ts_match and base_name.lower() in file_name.lower():
            ts = ts_match.group(1)
            screenshot_map[f"ts_{ts}"] = file_name
    
    return screenshot_map


def _associate_screenshots_to_steps(steps, screenshot_map):
    """后处理：将截图映射关联到已生成的步骤。
    
    根据每个步骤的 sourceRecorderLine 查找对应截图，
    设置 controls[].templateKey 字段，供模板匹配定位层使用。
    """
    if not screenshot_map:
        return
    
    linked_count = 0
    for step_index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        line_no = step.get("actionConfig", {}).get("sourceRecorderLine", 0)
        screenshot_file = None

        # 精确行号匹配
        if line_no:
            screenshot_file = screenshot_map.get(line_no)
            # 模糊匹配：行号附近
            if not screenshot_file:
                for offset in range(-2, 3):
                    candidate = screenshot_map.get(line_no + offset)
                    if candidate:
                        screenshot_file = candidate
                        break

        # step_N 命名回退：按步骤序号匹配（screenshot_map 中以负键存储 -N）
        if not screenshot_file:
            screenshot_file = screenshot_map.get(-step_index)

        if screenshot_file:
            controls = step.get("controls", []) if isinstance(step.get("controls"), list) else []
            for ctrl in controls:
                if not isinstance(ctrl, dict):
                    continue
                ctrl["templateKey"] = screenshot_file
                existing_notes = str(ctrl.get("notes", "") or "").strip()
                ctrl["notes"] = (existing_notes + f" 录制截图: {screenshot_file}").strip()
            linked_count += 1
    
    return linked_count


# 增量转换时保留的用户自定义字段
_PRESERVED_ACTION_FIELDS = {
    "waitAfter", "waitBefore", "onError", "retryCount", "retryInterval",
    "fallbackTemplate", "fallbackMode", "continueWhen", "stepPolicy",
}
_PRESERVED_CONTROL_FIELDS = {
    "targetMethod", "targetValue", "recommendedTargetMethod", "recommendedTargetValue",
}


def _merge_with_previous(new_steps, previous_steps):
    """增量合并：保留用户在上次转换中手动修正的字段。

    通过 sourceRecorderLine 匹配新旧步骤：
      - 匹配成功 → 保留用户修改的字段（targetMethod、waitAfter、fallbacks 等）
      - 新步骤 → 标记 _status: "new"
      - 旧步骤未匹配 → 标记 _status: "removed"，保留在结果中供参考
    """
    if not previous_steps:
        return new_steps

    # 建立旧步骤的行号索引
    old_by_line = {}
    for step in previous_steps:
        if not isinstance(step, dict):
            continue
        line_no = step.get("actionConfig", {}).get("sourceRecorderLine", 0) if isinstance(step.get("actionConfig"), dict) else 0
        if line_no:
            old_by_line[line_no] = step

    merged = []
    used_old_lines = set()

    for step in new_steps:
        if not isinstance(step, dict):
            merged.append(step)
            continue
        line_no = step.get("actionConfig", {}).get("sourceRecorderLine", 0) if isinstance(step.get("actionConfig"), dict) else 0

        old_step = old_by_line.get(line_no) if line_no else None
        if old_step:
            used_old_lines.add(line_no)
            # 保留用户修改的 actionConfig 字段
            old_ac = old_step.get("actionConfig", {}) if isinstance(old_step.get("actionConfig"), dict) else {}
            new_ac = step.get("actionConfig", {}) if isinstance(step.get("actionConfig"), dict) else {}
            for field in _PRESERVED_ACTION_FIELDS:
                if field in old_ac and old_ac[field] != new_ac.get(field):
                    new_ac[field] = old_ac[field]
            step["actionConfig"] = new_ac

            # 保留用户修改的 control 定位字段
            old_ctrls = old_step.get("controls", []) if isinstance(old_step.get("controls"), list) else []
            new_ctrls = step.get("controls", []) if isinstance(step.get("controls"), list) else []
            for i in range(min(len(old_ctrls), len(new_ctrls))):
                for field in _PRESERVED_CONTROL_FIELDS:
                    if field in old_ctrls[i] and old_ctrls[i][field] != new_ctrls[i].get(field):
                        new_ctrls[i][field] = old_ctrls[i][field]

            # 保留用户自定义的 fallbackChain（如果旧的有更多/不同的降级策略）
            old_fbs = old_step.get("fallbackChain", []) if isinstance(old_step.get("fallbackChain"), list) else []
            new_fbs = step.get("fallbackChain", []) if isinstance(step.get("fallbackChain"), list) else []
            if old_fbs and len(old_fbs) != len(new_fbs):
                # 保留旧版 fallbackChain（用户可能手动调整过）
                step["fallbackChain"] = old_fbs

            # 保留 enabled 状态
            if "enabled" in old_step:
                step["enabled"] = old_step["enabled"]

            # 保留用户添加的 notes（拼接而非覆盖）
            old_notes = str(old_step.get("notes", "")).strip()
            new_notes = str(step.get("notes", "")).strip()
            if old_notes and old_notes not in new_notes:
                step["notes"] = new_notes + " " + old_notes if new_notes else old_notes

            step["_status"] = "merged"
        else:
            step["_status"] = "new"

        merged.append(step)

    # 追加旧步骤中已被删除的（标记 removed，供人工审核）
    for line_no, old_step in sorted(old_by_line.items()):
        if line_no not in used_old_lines:
            old_step_copy = copy.deepcopy(old_step)
            old_step_copy["_status"] = "removed"
            old_step_copy["enabled"] = False
            merged.append(old_step_copy)

    return merged


def _generate_quality_report(payload, output_json_path):
    """生成自包含 HTML 质量的报告，可视化转换统计、置信度分布和步骤详情。"""
    if not output_json_path:
        return None
    report_path = os.path.splitext(output_json_path)[0] + "_report.html"
    meta = payload.get("conversionMeta", {})
    steps = payload.get("steps", [])

    total_steps = len(steps)
    action_steps = [s for s in steps if s.get("actionType") == "action"]
    placeholder_steps = [s for s in steps if s.get("actionType") == "placeholder"]

    high = meta.get("highConfidenceMatchedCount", 0)
    medium = meta.get("mediumConfidenceMatchedCount", 0)
    control_matched = meta.get("controlMapMatchedCount", 0)
    total_action = meta.get("actionSteps", 1) or 1
    low = max(0, total_action - control_matched - high - medium)

    match_rate = round(control_matched * 100 / total_action, 1) if total_action else 0
    high_rate = round(high * 100 / total_action, 1) if total_action else 0
    medium_rate = round(medium * 100 / total_action, 1) if total_action else 0
    low_rate = round(low * 100 / total_action, 1) if total_action else 0

    def _esc(text):
        if text is None:
            return ""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

    def _step_conf_class(step):
        for ctrl in (step.get("controls") or []):
            notes = str(ctrl.get("notes", "") or "")
            if "置信度=" in notes:
                m = re.search(r'置信度=([\d.]+)', notes)
                if m:
                    v = float(m.group(1))
                    if v >= CONFIDENCE_HIGH:
                        return "conf-high"
                    if v >= CONFIDENCE_MEDIUM:
                        return "conf-medium"
                    return "conf-low"
        return "conf-none"

    # 构建反馈稳定性索引
    feedback_history = payload.get("feedbackHistory", [])
    if not isinstance(feedback_history, list):
        feedback_history = []
    step_feedback = {}  # step_id → {"failures": N, "fallbacks": N, "lastError": "...", "lastTime": "..."}
    for fb in feedback_history:
        sid = fb.get("stepId", "")
        if not sid:
            continue
        if sid not in step_feedback:
            step_feedback[sid] = {"failures": 0, "fallbacks": 0, "lastError": "", "lastTime": ""}
        fb_type = fb.get("type", "")
        if fb_type == "step_failure":
            step_feedback[sid]["failures"] += 1
            step_feedback[sid]["lastError"] = fb.get("error", "")
        elif fb_type in ("fallback_recovery", "fallback_template_recovery"):
            step_feedback[sid]["fallbacks"] += 1
        step_feedback[sid]["lastTime"] = fb.get("timestamp", "") or step_feedback[sid]["lastTime"]

    # 计算总反馈统计
    total_failures = sum(s["failures"] for s in step_feedback.values())
    total_fallbacks = sum(s["fallbacks"] for s in step_feedback.values())
    hot_steps = sorted(
        [(sid, s) for sid, s in step_feedback.items() if s["failures"] + s["fallbacks"] > 0],
        key=lambda x: x[1]["failures"] + x[1]["fallbacks"],
        reverse=True,
    )[:10]

    def _stability_cell(step_id):
        fb = step_feedback.get(step_id, {})
        fails = fb.get("failures", 0)
        fbs = fb.get("fallbacks", 0)
        if fails + fbs == 0:
            return '<span class="stable-ok">—</span>'
        parts = []
        if fails > 0:
            parts.append(f'<span class="stable-bad">{fails}次失败</span>')
        if fbs > 0:
            parts.append(f'<span class="stable-warn">{fbs}次降级</span>')
        return " ".join(parts)

    # 构建步骤行
    step_rows = []
    for i, step in enumerate(steps):
        sid = _esc(step.get("id", f"step_{i+1}"))
        name = _esc(step.get("name", ""))
        atype = _esc(step.get("actionType", ""))
        action = _esc((step.get("actionConfig") or {}).get("action", ""))
        line_no = (step.get("actionConfig") or {}).get("sourceRecorderLine", "")
        status = step.get("_status", "")
        enabled = step.get("enabled", True)
        css_class = _step_conf_class(step)
        ctrls = step.get("controls", [])
        ctrl_info = ""
        if ctrls:
            c = ctrls[0]
            ctrl_info = _esc(c.get("targetMethod", "") or c.get("recommendedTargetMethod", "") or "—")
        review = ""
        if step.get("_reviewHint"):
            review = f' <span class="badge warn">⚠ 待审</span>'
        if status:
            badge_cls = "badge-new" if status == "new" else ("badge-removed" if status == "removed" else "badge-merged")
            review += f' <span class="badge {badge_cls}">{status}</span>'
        tr_class = "disabled" if not enabled else ""
        step_rows.append(
            f'<tr class="{css_class} {tr_class}">'
            f'<td>{i+1}</td><td>{line_no}</td><td>{sid}</td>'
            f'<td>{name}</td><td>{atype}</td><td>{action}</td>'
            f'<td>{ctrl_info}{review}</td>'
            f'<td>{_stability_cell(step.get("id", ""))}</td></tr>'
        )

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>转换质量报告 — {_esc(payload.get("project", ""))}</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background:#f5f7fa; color:#333; line-height:1.6; }}
.header {{ background: linear-gradient(135deg,#1a237e,#283593); color:#fff; padding:24px 32px; }}
.header h1 {{ font-size:22px; font-weight:600; }}
.header .sub {{ font-size:13px; opacity:.75; margin-top:4px; }}
.dashboard {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap:16px; padding:24px 32px; }}
.card {{ background:#fff; border-radius:8px; padding:18px 20px; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
.card .label {{ font-size:12px; color:#888; text-transform:uppercase; letter-spacing:.5px; }}
.card .value {{ font-size:28px; font-weight:700; margin-top:4px; }}
.card .sub-value {{ font-size:12px; color:#666; margin-top:2px; }}
.bar-section {{ padding:0 32px 24px; }}
.bar-section h2 {{ font-size:15px; margin-bottom:12px; color:#555; }}
.bar-row {{ display:flex; height:28px; border-radius:6px; overflow:hidden; margin-bottom:8px; }}
.bar-row .seg {{ display:flex; align-items:center; justify-content:center; font-size:12px; font-weight:600; color:#fff; }}
.bar-row .seg.high {{ background:#4CAF50; }}
.bar-row .seg.medium {{ background:#FF9800; }}
.bar-row .seg.low {{ background:#F44336; }}
.bar-row .seg.none {{ background:#BDBDBD; }}
.legend {{ display:flex; gap:20px; font-size:12px; color:#666; margin-top:4px; }}
.legend span::before {{ content:""; display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px; vertical-align:middle; }}
.legend .high::before {{ background:#4CAF50; }}
.legend .medium::before {{ background:#FF9800; }}
.legend .low::before {{ background:#F44336; }}
.legend .none::before {{ background:#BDBDBD; }}
.table-wrap {{ padding:0 32px 24px; overflow-x:auto; }}
.table-wrap h2 {{ font-size:15px; margin-bottom:12px; color:#555; }}
table {{ width:100%; border-collapse:collapse; background:#fff; border-radius:8px; overflow:hidden; box-shadow:0 1px 3px rgba(0,0,0,.08); }}
th {{ background:#f0f2f5; text-align:left; padding:10px 14px; font-size:12px; font-weight:600; color:#666; border-bottom:2px solid #e0e0e0; }}
td {{ padding:9px 14px; font-size:13px; border-bottom:1px solid #f0f0f0; }}
tr:hover {{ background:#f8f9ff; }}
tr.disabled {{ opacity:.45; }}
tr.conf-high {{ border-left:4px solid #4CAF50; }}
tr.conf-medium {{ border-left:4px solid #FF9800; }}
tr.conf-low {{ border-left:4px solid #F44336; }}
tr.conf-none {{ border-left:4px solid #BDBDBD; }}
.badge {{ display:inline-block; padding:1px 8px; border-radius:10px; font-size:11px; font-weight:600; margin-left:6px; }}
.badge.warn {{ background:#FFF3E0; color:#E65100; }}
.badge-merged {{ background:#E8F5E9; color:#2E7D32; }}
.badge-new {{ background:#E3F2FD; color:#1565C0; }}
.badge-removed {{ background:#FFEBEE; color:#C62828; text-decoration:line-through; }}
.review-section {{ padding:0 32px 24px; }}
.review-section h2 {{ font-size:15px; margin-bottom:12px; color:#555; }}
.review-item {{ background:#fff; border-radius:6px; padding:10px 16px; margin-bottom:6px; font-size:13px; box-shadow:0 1px 2px rgba(0,0,0,.05); border-left:3px solid #FF9800; }}
.stable-ok {{ color:#9E9E9E; font-size:11px; }}
.stable-bad {{ color:#C62828; font-weight:600; font-size:11px; }}
.stable-warn {{ color:#E65100; font-size:11px; }}
.feedback-card {{ background:#FFF8E1; border:1px solid #FFE082; }}
.hot-step {{ background:#fff; border-radius:6px; padding:8px 14px; margin-bottom:4px; font-size:13px; display:flex; justify-content:space-between; align-items:center; }}
.hot-step .step-name {{ font-weight:600; }}
.hot-step .stats {{ font-size:11px; color:#888; }}
.footer {{ text-align:center; padding:16px; font-size:11px; color:#aaa; border-top:1px solid #eee; }}
</style>
</head>
<body>
<div class="header">
  <h1>📊 转换质量报告</h1>
  <div class="sub">{_esc(meta.get("sourceScript", ""))} · {_esc(payload.get("lastUpdated", ""))}</div>
</div>

<div class="dashboard">
  <div class="card">
    <div class="label">原始步骤</div>
    <div class="value">{meta.get("rawSteps", 0)}</div>
    <div class="sub-value">recorder 录制行</div>
  </div>
  <div class="card">
    <div class="label">最终步骤</div>
    <div class="value">{total_steps}</div>
    <div class="sub-value">action: {len(action_steps)} · placeholder: {len(placeholder_steps)}</div>
  </div>
  <div class="card">
    <div class="label">控件命中率</div>
    <div class="value">{match_rate}%</div>
    <div class="sub-value">{control_matched}/{total_action} 匹配</div>
  </div>
  <div class="card">
    <div class="label">去重/合并</div>
    <div class="value">{meta.get("droppedDuplicateCount",0)+meta.get("noiseFiltered",0)}</div>
    <div class="sub-value">去重:{meta.get("droppedDuplicateCount",0)} · 噪声:{meta.get("noiseFiltered",0)} · 合并:{meta.get("sequencesMerged",0)}</div>
  </div>
  <div class="card">
    <div class="label">截图关联</div>
    <div class="value">{meta.get("screenshotLinkedCount", 0)}</div>
    <div class="sub-value">候选控件: {meta.get("candidateCount", 0)}</div>
  </div>
  <div class="card">
    <div class="label">增量转换</div>
    <div class="value">{meta.get("incrementalMerged", 0)}</div>
    <div class="sub-value">保留修正 · 新增:{meta.get("incrementalNew",0)} · 移除:{meta.get("incrementalRemoved",0)}</div>
  </div>
</div>

<div class="bar-section">
  <h2>置信度分布</h2>
  <div class="bar-row">
    <div class="seg high" style="width:{high_rate}%">{high_rate}% 高</div>
    <div class="seg medium" style="width:{medium_rate}%">{medium_rate}% 中</div>
    <div class="seg low" style="width:{low_rate}%">{low_rate}% 低</div>
    <div class="seg none" style="width:{max(0,100-high_rate-medium_rate-low_rate)}%">{max(0,round(100-high_rate-medium_rate-low_rate,1))}% 无</div>
  </div>
  <div class="legend">
    <span class="high">高置信度 (≥{CONFIDENCE_HIGH}) — automation_id 精确命中</span>
    <span class="medium">中置信度 (≥{CONFIDENCE_MEDIUM}) — name+type+parent 匹配</span>
    <span class="low">低置信度 (≥{CONFIDENCE_LOW}) — type+proximity 匹配</span>
    <span class="none">未匹配 — 需人工校验</span>
  </div>
</div>

<div class="table-wrap">
  <h2>步骤详情 ({total_steps})</h2>
  <table>
    <thead><tr><th>#</th><th>录制行</th><th>ID</th><th>名称</th><th>类型</th><th>动作</th><th>控件/备注</th><th>稳定性</th></tr></thead>
    <tbody>{"".join(step_rows)}</tbody>
  </table>
</div>
'''

    # 审查项
    review_items = meta.get("reviewItems", [])
    if review_items:
        html += '<div class="review-section"><h2>⚠ 待审查项 (' + str(len(review_items)) + ')</h2>'
        for item in review_items:
            step_id = _esc(item.get("stepId", ""))
            hint = _esc(item.get("hint", ""))
            html += f'<div class="review-item"><strong>{step_id}</strong>: {hint}</div>'
        html += '</div>'

    # P3: 运行时反馈闭环 — 稳定性分析
    if feedback_history:
        html += '<div class="review-section"><h2>🔄 运行时反馈 (' + str(len(feedback_history)) + ' 条记录)</h2>'
        html += '<div class="dashboard" style="padding:0 0 16px;">'
        html += f'<div class="card feedback-card"><div class="label">累计失败</div><div class="value">{total_failures}</div></div>'
        html += f'<div class="card feedback-card"><div class="label">降级恢复</div><div class="value">{total_fallbacks}</div></div>'
        html += f'<div class="card feedback-card"><div class="label">涉及步骤</div><div class="value">{len(hot_steps)}</div></div>'
        html += '</div>'
        if hot_steps:
            html += '<h3 style="font-size:14px; color:#555; margin-bottom:8px;">🔥 热点步骤（最不稳定）</h3>'
            for sid, s in hot_steps:
                step_name = _esc(sid)
                html += '<div class="hot-step">'
                html += f'<span class="step-name">{step_name}</span>'
                html += f'<span class="stats">失败:{s["failures"]} · 降级:{s["fallbacks"]} · 最后:{s.get("lastTime","?")[:16]}</span>'
                html += '</div>'
        html += '</div>'

    html += f'<div class="footer">WT_Automation flow_recorder_converter · 自动生成于 {_esc(payload.get("lastUpdated", ""))}</div>\n</body>\n</html>'

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
    return report_path


def convert_recorder_script_to_flow(script_path, output_json_path=None, control_map_dir=CONTROL_MAP_DIR, screenshot_dir=None, previous_flow_path=None):
    """将 pywinauto_recorder 脚本转换为 flow_definition JSON。
    
    Args:
        script_path: recorder .py 脚本路径
        output_json_path: 输出 JSON 路径
        control_map_dir: 控件库目录
        screenshot_dir: 可选，录制截图目录。若提供，转换时会尝试匹配截图到对应步骤，
                       关联 templateKey 供模板匹配层使用。
        previous_flow_path: 可选，上次转换的 flow_definition.json。若提供，
                       启用增量转换模式，保留用户在上次转换中的手动修正。
    """
    script_text = _read_text(script_path)
    module_node = ast.parse(script_text, filename=script_path)
    raw_steps = []
    
    # 加载截图映射（若提供截图目录）
    screenshot_map = {}
    if screenshot_dir and os.path.isdir(screenshot_dir):
        screenshot_map = _load_screenshot_map(screenshot_dir, script_path)
    
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
        "uniquePathSelectors": 0,
        # 阶段一新增统计
        "findActionCount": 0,
        "menuClickActionCount": 0,
        "setTextActionCount": 0,
        "setComboboxActionCount": 0,
        # 阶段二新增统计
        "screenshotLinkedCount": 0,
        "a11yEnrichedCount": 0,
        "maxNestingDepth": 0,
        "ancestorMatchedCount": 0,
        "highConfidenceMatchedCount": 0,
        "mediumConfidenceMatchedCount": 0,
        # 阶段三：双向控件库同步
        "candidateCount": 0,
    }
    # 注入脚本路径，供候选控件存储时回溯来源
    stats["scriptPath"] = script_path
    
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
    
    # 3.5 关联截图到步骤（借鉴 ClawBridge a11y enrichment）
    if screenshot_map:
        linked = _associate_screenshots_to_steps(processed_steps, screenshot_map)
        stats["screenshotLinkedCount"] = linked

    # 3.6 增量转换：若提供上次转换结果，保留用户手动修正 + 反馈历史
    if previous_flow_path and os.path.isfile(previous_flow_path):
        try:
            with open(previous_flow_path, "r", encoding="utf-8") as fh:
                prev = json.load(fh)
            prev_steps = prev.get("steps", []) if isinstance(prev, dict) else []
            if prev_steps:
                processed_steps = _merge_with_previous(processed_steps, prev_steps)
                stats["incrementalMerged"] = sum(1 for s in processed_steps if s.get("_status") == "merged")
                stats["incrementalNew"] = sum(1 for s in processed_steps if s.get("_status") == "new")
                stats["incrementalRemoved"] = sum(1 for s in processed_steps if s.get("_status") == "removed")
            # P3: 保留历史运行时反馈
            prev_feedback = prev.get("feedbackHistory", []) if isinstance(prev, dict) else []
            if isinstance(prev_feedback, list) and prev_feedback:
                stats["previousFeedbackHistory"] = prev_feedback
        except Exception:
            pass  # 增量合并失败不影响主流程

    # 4. 重新生成不重复的 step id
    seen_ids = set()
    for i, step in enumerate(processed_steps):
        base_id = _sanitize_step_id(step["name"], fallback_prefix=f"step_{i+1}")
        step["id"] = _unique_step_id(base_id, seen_ids)
    
    # 统计置信度分布（借鉴 ClawBridge）
    for step in processed_steps:
        controls = step.get("controls", []) if isinstance(step.get("controls"), list) else []
        for ctrl in controls:
            notes = str(ctrl.get("notes", "") or "").strip()
            conf_match = re.search(r'置信度=([\d.]+)', notes)
            if conf_match:
                conf = float(conf_match.group(1))
                if conf >= CONFIDENCE_HIGH:
                    stats["highConfidenceMatchedCount"] += 1
                elif conf >= CONFIDENCE_MEDIUM:
                    stats["mediumConfidenceMatchedCount"] += 1
    
    payload = {
        "version": "1.1",
        "project": os.path.splitext(os.path.basename(script_path))[0],
        "description": "由 pywinauto_recorder 录制脚本智能转换生成的 action 流程骨架（已语义分析 + a11y 富化优化）。",
        "lastUpdated": datetime.now().isoformat(timespec="seconds"),
        "feedbackHistory": stats.get("previousFeedbackHistory", []),
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
            "uniquePathSelectors": stats["uniquePathSelectors"],
            "suspiciousStepCount": len(review_items),
            # 阶段一新增
            "findActionCount": stats.get("findActionCount", 0),
            "menuClickActionCount": stats.get("menuClickActionCount", 0),
            "setTextActionCount": stats.get("setTextActionCount", 0),
            "setComboboxActionCount": stats.get("setComboboxActionCount", 0),
            # 阶段二新增
            "screenshotLinkedCount": stats.get("screenshotLinkedCount", 0),
            "a11yEnrichedCount": stats.get("a11yEnrichedCount", 0),
            "maxNestingDepth": stats.get("maxNestingDepth", 0),
            "highConfidenceMatchedCount": stats.get("highConfidenceMatchedCount", 0),
            "mediumConfidenceMatchedCount": stats.get("mediumConfidenceMatchedCount", 0),
            # 阶段三新增
            "candidateCount": stats.get("candidateCount", 0),
            "candidateDir": CONTROL_CANDIDATES_DIR,
            # 增量转换统计
            "incrementalMerged": stats.get("incrementalMerged", 0),
            "incrementalNew": stats.get("incrementalNew", 0),
            "incrementalRemoved": stats.get("incrementalRemoved", 0),
            # P2: 质量报告路径
            "qualityReportPath": stats.get("qualityReportPath", ""),
            "confidenceThresholds": {
                "high": CONFIDENCE_HIGH,
                "medium": CONFIDENCE_MEDIUM,
                "low": CONFIDENCE_LOW,
            },
            "controlMapDir": control_map_dir,
            "screenshotDir": screenshot_dir or "",
            "droppedRecorderLines": stats["droppedRecorderLines"],
            "reviewItems": review_items,
        },
    }
    
    # P2: 生成视觉质量 HTML 报告（在写入 JSON 之前，确保路径回写到 payload）
    if output_json_path:
        report_path = _generate_quality_report(payload, output_json_path)
        if report_path:
            payload["conversionMeta"]["qualityReportPath"] = report_path
    
    if output_json_path:
        _write_json(output_json_path, payload)
    return payload


def build_arg_parser():
    parser = argparse.ArgumentParser(description="把 pywinauto_recorder Python 脚本智能转换为 action 流程骨架")
    parser.add_argument("script", help="输入 recorder Python 文件路径")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_JSON, help="输出 JSON 路径")
    parser.add_argument("--control-map-dir", default=CONTROL_MAP_DIR, help="控件库目录，默认读取项目 control_maps")
    parser.add_argument("--screenshot-dir", default=None, help="可选，录制截图目录。若提供，转换时关联截图 templateKey 供模板匹配层使用")
    parser.add_argument("--previous", default=None, help="可选，上次转换的 flow_definition.json。若提供，启用增量转换保留用户手动修正")
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    payload = convert_recorder_script_to_flow(
        args.script,
        args.output,
        control_map_dir=args.control_map_dir,
        screenshot_dir=args.screenshot_dir,
        previous_flow_path=args.previous,
    )
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
    # 阶段一新增统计
    if meta.get('findActionCount', 0) > 0:
        info_parts.append(f"find→wait: {meta['findActionCount']}")
    if meta.get('menuClickActionCount', 0) > 0:
        info_parts.append(f"menu_click→menu_select: {meta['menuClickActionCount']}")
    if meta.get('setTextActionCount', 0) > 0:
        info_parts.append(f"set_text→type_text: {meta['setTextActionCount']}")
    if meta.get('setComboboxActionCount', 0) > 0:
        info_parts.append(f"set_combobox: {meta['setComboboxActionCount']}")
    # 阶段二新增统计
    if meta.get('screenshotLinkedCount', 0) > 0:
        info_parts.append(f"截图关联: {meta['screenshotLinkedCount']}")
    if meta.get('highConfidenceMatchedCount', 0) > 0:
        info_parts.append(f"高置信度匹配: {meta['highConfidenceMatchedCount']}")
    # 阶段三新增统计
    if meta.get('candidateCount', 0) > 0:
        info_parts.append(f"候选控件: {meta['candidateCount']} (→{CONTROL_CANDIDATES_DIR})")
    # 增量转换统计
    if meta.get('incrementalMerged', 0) > 0 or meta.get('incrementalNew', 0) > 0 or meta.get('incrementalRemoved', 0) > 0:
        incr_parts = []
        if meta.get('incrementalMerged', 0) > 0:
            incr_parts.append(f"保留修正: {meta['incrementalMerged']}")
        if meta.get('incrementalNew', 0) > 0:
            incr_parts.append(f"新增: {meta['incrementalNew']}")
        if meta.get('incrementalRemoved', 0) > 0:
            incr_parts.append(f"已移除: {meta['incrementalRemoved']}")
        info_parts.append(" | ".join(incr_parts))
    info_parts.extend([
        f"action: {meta['actionSteps']}",
        f"placeholder: {meta['placeholderSteps']}",
        f"输出: {args.output}"
    ])
    print("已完成 recorder 智能转换: " + ", ".join(info_parts))
    # P2: 质量报告提示
    report_path = meta.get("qualityReportPath", "")
    if report_path:
        print(f"📊 质量报告: {report_path}")


if __name__ == "__main__":
    main()
