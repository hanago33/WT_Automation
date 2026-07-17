# encoding: utf-8

import argparse
import ctypes
import json
import os
import re
import sys
import tkinter as tk
import time
from ctypes import wintypes
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

try:
    from pywinauto import Desktop
except Exception:
    Desktop = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTROL_MAP_DIR = os.path.join(BASE_DIR, "control_maps")
DEFAULT_BACKEND = "smart"
DEFAULT_MAX_DEPTH = 6
DEFAULT_PICK_DELAY_SECONDS = 3
BACKEND_OPTIONS = ["smart", "uia", "win32"]

user32 = ctypes.windll.user32


def _safe_get_value(getter, default=""):
    try:
        value = getter()
    except Exception:
        return default
    return default if value is None else value


def ensure_directory(path):
    os.makedirs(path, exist_ok=True)


def slugify_filename(text, fallback="window"):
    text = str(text or "").strip()
    if not text:
        return fallback
    text = re.sub(r"[\\/:*?\"<>|]+", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:80] or fallback


def normalize_control_type_name(control_type, localized_control_type=""):
    control_type = str(control_type or "").strip()
    if control_type.startswith("UIA_") and "ControlTypeId" in control_type:
        control_type = control_type.replace("UIA_", "").replace("ControlTypeId", "").strip()
    matched = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\d+\)\s*$", control_type)
    if matched:
        control_type = matched.group(1)
    return control_type or str(localized_control_type or "").strip()


def build_locator_recommendation(parsed):
    automation_id = str(parsed.get("automationId", "")).strip()
    name = str(parsed.get("name", "")).strip()
    class_name = str(parsed.get("className", "")).strip()
    handle = str(parsed.get("nativeWindowHandle", "")).strip()
    control_type = normalize_control_type_name(parsed.get("controlType", ""), parsed.get("localizedControlType", ""))

    # 过滤掉 SVG path 几何数据、超长乱码等无效 name
    if name and (_is_garbage_name(name) or len(name) > 80):
        name = ""

    candidates = [
        ("automation_id,control_type", [automation_id, control_type], 100, "automation_id + control_type"),
        ("automation_id,class_name", [automation_id, class_name], 96, "automation_id + class_name"),
        ("automation_id", [automation_id], 92, "automation_id"),
        ("name,control_type", [name, control_type], 88, "name + control_type"),
        ("name,class_name", [name, class_name], 84, "name + class_name"),
        ("name", [name], 78, "name"),
        ("class_name,control_type", [class_name, control_type], 68, "class_name + control_type"),
        ("class_name", [class_name], 58, "class_name"),
        ("control_type", [control_type], 42, "control_type"),
        ("handle", [handle], 24, "handle"),
    ]
    for method, values, score, reason in candidates:
        if all(str(item).strip() and str(item).strip() != "[null]" for item in values):
            return method, ",".join(values), score, reason
    return "", "", 0, "no_stable_locator"


def _is_garbage_name(name):
    """判断 name 是否是 SVG path / 几何数据 / 乱码，不可作为可读名称"""
    if not name:
        return False
    name = str(name).strip()
    # SVG path 数据特征：以 M/L/C/A/H/V/Z 开头并包含大量数字和逗号
    if re.match(r"^[MLCAHVZmlcahvz][\d.,\s\-]+", name) and name.count(",") >= 3:
        return True
    # 纯坐标序列
    if re.match(r"^[\d.,\s]+$", name) and len(name) > 10:
        return True
    return False


def build_aux_checks(parsed):
    checks = []
    for key, label in [
        ("isEnabled", "IsEnabled"),
        ("isOffscreen", "IsOffscreen"),
        ("isKeyboardFocusable", "IsKeyboardFocusable"),
        ("hasKeyboardFocus", "HasKeyboardFocus"),
        ("frameworkId", "FrameworkId"),
        ("className", "ClassName"),
    ]:
        value = str(parsed.get(key, "")).strip()
        if value:
            checks.append(f"{label}={value}")
    return checks


def build_synthetic_inspect_text(inspect_data):
    lines = [
        f'Name: \t "{inspect_data.get("name", "")}"',
        f'ControlType: \t {inspect_data.get("controlType", "")}',
        f'LocalizedControlType: \t "{inspect_data.get("localizedControlType", "")}"',
        f'BoundingRectangle: \t {inspect_data.get("boundingRectangle", "")}',
        f'IsEnabled: \t {inspect_data.get("isEnabled", "")}',
        f'IsOffscreen: \t {inspect_data.get("isOffscreen", "")}',
        f'IsKeyboardFocusable: \t {inspect_data.get("isKeyboardFocusable", "")}',
        f'HasKeyboardFocus: \t {inspect_data.get("hasKeyboardFocus", "")}',
        f'ProcessId: \t {inspect_data.get("processId", "")}',
        f'FrameworkId: \t "{inspect_data.get("frameworkId", "")}"',
        f'ClassName: \t "{inspect_data.get("className", "")}"',
        f'AutomationId: \t "{inspect_data.get("automationId", "")}"',
        f'NativeWindowHandle: \t {inspect_data.get("nativeWindowHandle", "")}',
        f'ProviderDescription: \t "{inspect_data.get("providerDescription", "")}"',
        f'LegacyIAccessible.Name: \t "{inspect_data.get("legacyName", "")}"',
        f'LegacyIAccessible.Role: \t {inspect_data.get("legacyRole", "")}',
        f'LegacyIAccessible.State: \t {inspect_data.get("legacyState", "")}',
    ]
    children = inspect_data.get("children", [])
    if children:
        lines.append("Children: \t " + children[0])
        lines.extend(f"\t {item}" for item in children[1:])
    ancestors = inspect_data.get("ancestors", [])
    if ancestors:
        lines.append("Ancestors: \t " + ancestors[0])
        lines.extend(f"\t {item}" for item in ancestors[1:])
    return "\n".join(lines)


def _format_runtime_id(runtime_id_raw):
    if isinstance(runtime_id_raw, (list, tuple)) and runtime_id_raw:
        values = []
        for item in runtime_id_raw:
            try:
                values.append(hex(int(item))[2:].upper())
            except Exception:
                values.append(str(item))
        return "[" + ",".join(values) + "]"
    return str(runtime_id_raw or "").strip()


def _format_rectangle(rect):
    if not rect:
        return ""
    left = _safe_get_value(lambda: rect.left, "")
    top = _safe_get_value(lambda: rect.top, "")
    right = _safe_get_value(lambda: rect.right, "")
    bottom = _safe_get_value(lambda: rect.bottom, "")
    return f"[l={left},t={top},r={right},b={bottom}]"


def _rect_to_dict(rect):
    if not rect:
        return None
    left = _safe_get_value(lambda: int(rect.left), None)
    top = _safe_get_value(lambda: int(rect.top), None)
    right = _safe_get_value(lambda: int(rect.right), None)
    bottom = _safe_get_value(lambda: int(rect.bottom), None)
    if None in {left, top, right, bottom}:
        return None
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _normalize_rect_dict(rect):
    if not isinstance(rect, dict):
        return None
    try:
        left = int(rect.get("left"))
        top = int(rect.get("top"))
        right = int(rect.get("right"))
        bottom = int(rect.get("bottom"))
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _rect_contains(inner, outer):
    inner = _normalize_rect_dict(inner)
    outer = _normalize_rect_dict(outer)
    if not inner or not outer:
        return False
    return (
        inner["left"] >= outer["left"]
        and inner["top"] >= outer["top"]
        and inner["right"] <= outer["right"]
        and inner["bottom"] <= outer["bottom"]
    )


def _rect_intersects(a, b):
    a = _normalize_rect_dict(a)
    b = _normalize_rect_dict(b)
    if not a or not b:
        return False
    return not (
        a["right"] <= b["left"]
        or a["left"] >= b["right"]
        or a["bottom"] <= b["top"]
        or a["top"] >= b["bottom"]
    )


def _rect_area(rect):
    rect = _normalize_rect_dict(rect)
    if not rect:
        return 0
    return max(0, rect["right"] - rect["left"]) * max(0, rect["bottom"] - rect["top"])


def _intersection_area(a, b):
    a = _normalize_rect_dict(a)
    b = _normalize_rect_dict(b)
    if not a or not b:
        return 0
    left = max(a["left"], b["left"])
    top = max(a["top"], b["top"])
    right = min(a["right"], b["right"])
    bottom = min(a["bottom"], b["bottom"])
    if right <= left or bottom <= top:
        return 0
    return (right - left) * (bottom - top)


def _rect_center(rect):
    rect = _normalize_rect_dict(rect)
    if not rect:
        return None
    return ((rect["left"] + rect["right"]) / 2.0, (rect["top"] + rect["bottom"]) / 2.0)


def _distance(point_a, point_b):
    if not point_a or not point_b:
        return 10**9
    return ((point_a[0] - point_b[0]) ** 2 + (point_a[1] - point_b[1]) ** 2) ** 0.5


def _get_foreground_wrapper(backend, excluded_process_ids=None, excluded_titles=None):
    if Desktop is None:
        raise RuntimeError("缺少 pywinauto 依赖，请先安装 pywinauto。")
    excluded_process_ids = {str(item) for item in (excluded_process_ids or []) if str(item).strip()}
    excluded_titles = {str(item).strip() for item in (excluded_titles or []) if str(item).strip()}
    desktop = Desktop(backend=backend)

    hwnd = user32.GetForegroundWindow()
    if hwnd:
        candidate = desktop.window(handle=hwnd)
        title = str(_safe_get_value(lambda: candidate.window_text(), "")).strip()
        process_id = str(_safe_get_value(lambda: candidate.process_id(), "")).strip()
        if process_id not in excluded_process_ids and title not in excluded_titles:
            return candidate

    visible_windows = []
    for window in desktop.windows():
        title = str(_safe_get_value(lambda: window.window_text(), "")).strip()
        if not title:
            continue
        process_id = str(_safe_get_value(lambda: window.process_id(), "")).strip()
        if process_id in excluded_process_ids or title in excluded_titles:
            continue
        visible_windows.append(window)
    if not visible_windows:
        raise RuntimeError("当前没有可用的前台窗口。")
    return visible_windows[0]


def _find_window_by_keyword(keyword, backend, excluded_process_ids=None, excluded_titles=None):
    if Desktop is None:
        raise RuntimeError("缺少 pywinauto 依赖，请先安装 pywinauto。")
    keyword = str(keyword or "").strip().lower()
    if not keyword:
        raise RuntimeError("请先输入目标窗口关键字，或改用“当前前台窗口”模式。")
    excluded_process_ids = {str(item) for item in (excluded_process_ids or []) if str(item).strip()}
    excluded_titles = {str(item).strip() for item in (excluded_titles or []) if str(item).strip()}

    candidates = []
    for window in Desktop(backend=backend).windows():
        title = str(_safe_get_value(lambda: window.window_text(), "")).strip()
        if not title:
            continue
        process_id = str(_safe_get_value(lambda: window.process_id(), "")).strip()
        if process_id in excluded_process_ids or title in excluded_titles:
            continue
        lower_title = title.lower()
        if keyword not in lower_title:
            continue
        score = 0
        if lower_title == keyword:
            score += 100
        elif lower_title.startswith(keyword):
            score += 60
        score -= len(title)
        candidates.append((score, window))
    if not candidates:
        raise RuntimeError(f"未找到标题包含关键字的窗口：{keyword}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _build_display_name(parsed, fallback_prefix, index):
    for candidate in [
        parsed.get("name", ""),
        parsed.get("automationId", ""),
        parsed.get("className", ""),
        normalize_control_type_name(parsed.get("controlType", ""), parsed.get("localizedControlType", "")),
    ]:
        candidate = str(candidate or "").strip()
        if candidate and candidate != "[null]":
            return candidate
    return f"{fallback_prefix}_{index}"


def _split_identifier_tokens(text):
    text = str(text or "").strip()
    if not text:
        return []
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", " ", text)
    return [token for token in text.split() if token]


def _translate_control_type_label(control_type):
    mapping = {
        "button": "按钮",
        "edit": "输入框",
        "combobox": "下拉框",
        "menuitem": "菜单项",
        "tabitem": "页签",
        "treeitem": "树节点",
        "checkbox": "复选框",
        "radiobutton": "单选框",
        "text": "文本",
        "custom": "区域",
        "pane": "面板",
        "group": "分组",
        "window": "窗口",
        "image": "图像",
        "listitem": "列表项",
    }
    return mapping.get(str(control_type or "").strip().lower(), "")


def _build_readable_control_name(flat_control):
    if not isinstance(flat_control, dict):
        return "控件"
    preferred_name = str(flat_control.get("name", "")).strip()
    if preferred_name and not re.fullmatch(r"[A-Za-z0-9_]+", preferred_name):
        return preferred_name
    raw_name = (
        preferred_name
        or str(flat_control.get("automationId", "")).strip()
        or str(flat_control.get("displayName", "")).strip()
        or str(flat_control.get("className", "")).strip()
        or ""
    )
    tokens = _split_identifier_tokens(raw_name)
    control_type = str(flat_control.get("controlType", "")).strip()
    ignore_tokens = {
        "mup",
        "view",
        "viewmodel",
        "model",
        "main",
        "control",
        "item",
        "panel",
        "pane",
        "window",
        "textblock",
    }
    type_tokens = {token.lower() for token in _split_identifier_tokens(control_type)}
    readable_tokens = []
    for token in tokens:
        lower_token = token.lower()
        if lower_token in ignore_tokens or lower_token in type_tokens:
            continue
        if len(token) <= 2 and token.isupper():
            continue
        readable_tokens.append(token)
    base_name = " ".join(readable_tokens).strip() or raw_name or "控件"
    type_label = _translate_control_type_label(control_type)
    if type_label and type_label not in base_name:
        return f"{base_name} {type_label}".strip()
    return base_name


def _classify_control_quality(flat_control):
    if not isinstance(flat_control, dict):
        return "建议忽略", "无有效控件信息"
    control_type = str(flat_control.get("controlType", "")).strip().lower()
    score = int(flat_control.get("locatorScore", 0) or 0)
    has_automation_id = bool(str(flat_control.get("automationId", "")).strip())
    has_name = bool(str(flat_control.get("name", "")).strip())
    depth = int(flat_control.get("depth", 0) or 0)
    child_count = len(((flat_control.get("inspectData", {}) or {}).get("children", []) or []))

    # 非动作控件黑名单：Thumb(列宽手柄)、ScrollBar、Separator、Header 等
    non_actionable_types = {"thumb", "scrollbar", "separator", "header", "titlebar", "statusbar", "menubar", "tooltip", "tooltip"}
    if control_type in non_actionable_types:
        return "建议忽略", f"{control_type} 不是动作控件，无需入库"

    # 顶层窗口
    if control_type in {"window"}:
        return "建议忽略", "顶层窗口通常不作为直接动作控件"
    if control_type in {"custom", "pane", "group"} and (child_count >= 3 or score < 80):
        return "容器控件", "更适合作为区域或父级上下文"
    if control_type in {"button", "edit", "combobox", "menuitem", "tabitem", "treeitem", "checkbox", "radiobutton", "listitem"} and (score >= 80 or has_automation_id or has_name):
        return "推荐保留", "定位稳定且控件类型适合直接编排动作"
    if score >= 90 and (has_automation_id or has_name):
        return "推荐保留", "定位策略稳定"
    if depth <= 0 or score <= 45:
        return "建议忽略", "定位弱或层级过粗"
    if child_count >= 5:
        return "容器控件", "子控件较多，更像容器区域"
    return "建议忽略", "可作为备选，但不建议优先入库"


def _should_default_select_group(group):
    if not isinstance(group, dict):
        return False
    quality_tier = str(group.get("qualityTier", "")).strip()
    return quality_tier in {"推荐保留", "容器控件"}


def _enrich_flat_controls(flat_controls, target_window):
    target_window = target_window or {}
    for item in flat_controls or []:
        if not isinstance(item, dict):
            continue
        if not str(item.get("windowTitle", "")).strip():
            item["windowTitle"] = str(target_window.get("title", "")).strip()
        if not str(item.get("windowClassName", "")).strip():
            item["windowClassName"] = str(target_window.get("className", "")).strip()
        suggested_name = _build_readable_control_name(item)
        item["suggestedControlName"] = suggested_name
        quality_tier, quality_reason = _classify_control_quality(item)
        item["qualityTier"] = quality_tier
        item["qualityReason"] = quality_reason
    return flat_controls


def _get_wrapper_handle(wrapper):
    return str(
        _safe_get_value(lambda: getattr(wrapper, "handle", ""), "")
        or _safe_get_value(lambda: getattr(wrapper.element_info, "handle", ""), "")
    ).strip()


def _get_wrapper_runtime_id(wrapper):
    return _format_runtime_id(_safe_get_value(lambda: getattr(wrapper.element_info, "runtime_id", ""), ""))


def _build_flat_control_identity(item):
    rect = _normalize_rect_dict(item.get("boundingBox"))
    rect_key = ""
    if rect:
        rect_key = f"{rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
    return "|".join(
        [
            str(item.get("runtimeId", "")).strip(),
            str(item.get("handle", "")).strip(),
            str(item.get("name", "")).strip(),
            str(item.get("className", "")).strip(),
            str(item.get("controlType", "")).strip(),
            rect_key,
        ]
    )


def _build_wrapper_identity(wrapper):
    rect = _rect_to_dict(_safe_get_value(lambda: wrapper.rectangle(), None))
    rect_key = ""
    if rect:
        rect_key = f"{rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
    return "|".join(
        [
            _get_wrapper_runtime_id(wrapper),
            _get_wrapper_handle(wrapper),
            str(_safe_get_value(lambda: wrapper.window_text(), "")).strip(),
            str(_safe_get_value(lambda: wrapper.class_name(), "")).strip(),
            normalize_control_type_name(
                _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
                _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
            ),
            rect_key,
        ]
    )


def _build_path_segments_from_wrapper(wrapper, root_handle=""):
    segments = []
    current = wrapper
    seen = set()
    for _ in range(32):
        if current is None:
            break
        identity = _build_wrapper_identity(current)
        if identity in seen:
            break
        seen.add(identity)
        parsed = {
            "name": str(_safe_get_value(lambda: current.window_text(), "")).strip()
            or str(_safe_get_value(lambda: getattr(current.element_info, "name", ""), "")).strip(),
            "automationId": str(_safe_get_value(lambda: getattr(current.element_info, "automation_id", ""), "")).strip(),
            "className": str(_safe_get_value(lambda: current.class_name(), "")).strip(),
            "controlType": str(_safe_get_value(lambda: getattr(current.element_info, "control_type", ""), "")).strip(),
            "localizedControlType": str(
                _safe_get_value(lambda: getattr(current.element_info, "localized_control_type", ""), "")
            ).strip(),
        }
        segments.append(_build_display_name(parsed, "控件", len(segments) + 1))
        current_handle = _get_wrapper_handle(current)
        if root_handle and current_handle and str(current_handle) == str(root_handle):
            break
        parent = _safe_get_value(lambda: current.parent(), None)
        if not parent or parent is current:
            break
        current = parent
    return list(reversed(segments))


def _extract_wrapper_info(wrapper, depth, index, path_segments, target_window):
    element_info = _safe_get_value(lambda: wrapper.element_info, None)
    name = str(_safe_get_value(lambda: wrapper.window_text(), "")).strip()
    if element_info is not None and not name:
        name = str(_safe_get_value(lambda: getattr(element_info, "name", ""), "")).strip()
    class_name = str(_safe_get_value(lambda: wrapper.class_name(), "")).strip()
    control_type = normalize_control_type_name(
        _safe_get_value(lambda: getattr(element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(element_info, "localized_control_type", ""), ""),
    )
    localized_control_type = str(_safe_get_value(lambda: getattr(element_info, "localized_control_type", ""), "")).strip()
    automation_id = str(_safe_get_value(lambda: getattr(element_info, "automation_id", ""), "")).strip()
    framework_id = str(_safe_get_value(lambda: getattr(element_info, "framework_id", ""), "")).strip()
    process_id = str(_safe_get_value(lambda: getattr(element_info, "process_id", ""), "")).strip()
    handle = str(_safe_get_value(lambda: getattr(element_info, "handle", ""), "")).strip()
    help_text = str(_safe_get_value(lambda: getattr(element_info, "rich_text", ""), "")).strip()
    provider_description = str(_safe_get_value(lambda: getattr(element_info, "provider_description", ""), "")).strip()
    runtime_id = _format_runtime_id(_safe_get_value(lambda: getattr(element_info, "runtime_id", ""), ""))
    rect = _safe_get_value(lambda: wrapper.rectangle(), None)
    bounding_rectangle = _format_rectangle(rect)
    is_enabled = _safe_get_value(lambda: wrapper.is_enabled(), "")
    is_visible = _safe_get_value(lambda: wrapper.is_visible(), "")
    is_offscreen = _safe_get_value(lambda: getattr(element_info, "offscreen", ""), "")
    if is_offscreen == "" and is_visible != "":
        is_offscreen = str(not bool(is_visible))
    keyboard_focusable = _safe_get_value(lambda: getattr(element_info, "keyboard_focusable", ""), "")
    has_keyboard_focus = _safe_get_value(lambda: getattr(element_info, "has_keyboard_focus", ""), "")
    legacy_name = str(_safe_get_value(lambda: getattr(element_info, "legacy_name", ""), "")).strip()
    legacy_role = str(_safe_get_value(lambda: getattr(element_info, "legacy_role", ""), "")).strip()
    legacy_state = str(_safe_get_value(lambda: getattr(element_info, "legacy_state", ""), "")).strip()

    inspect_data = {
        "name": name,
        "controlType": control_type,
        "localizedControlType": localized_control_type,
        "boundingRectangle": bounding_rectangle,
        "isEnabled": str(is_enabled),
        "isVisible": str(is_visible),
        "isOffscreen": str(is_offscreen),
        "isKeyboardFocusable": str(keyboard_focusable),
        "hasKeyboardFocus": str(has_keyboard_focus),
        "processId": process_id,
        "runtimeId": runtime_id,
        "frameworkId": framework_id,
        "className": class_name,
        "automationId": automation_id,
        "nativeWindowHandle": handle,
        "providerDescription": provider_description,
        "legacyName": legacy_name,
        "legacyRole": legacy_role,
        "legacyState": legacy_state,
        "helpText": help_text,
        "ancestors": list(path_segments[:-1]),
        "children": [],
    }
    locator_method, locator_value, locator_score, locator_reason = build_locator_recommendation(inspect_data)
    inspect_data["recommendedTargetMethod"] = locator_method
    inspect_data["recommendedTargetValue"] = locator_value

    display_name = _build_display_name(inspect_data, "控件", index)
    ui_path = " > ".join(item for item in path_segments if item)
    raw_inspect_text = build_synthetic_inspect_text(inspect_data)

    return {
        "depth": depth,
        "index": index,
        "displayName": display_name,
        "windowTitle": str(target_window.get("title", "")).strip(),
        "windowClassName": str(target_window.get("className", "")).strip(),
        "processId": process_id,
        "handle": handle,
        "name": name,
        "className": class_name,
        "controlType": control_type,
        "localizedControlType": localized_control_type,
        "automationId": automation_id,
        "frameworkId": framework_id,
        "runtimeId": runtime_id,
        "boundingRectangle": bounding_rectangle,
        "boundingBox": _rect_to_dict(rect),
        "isEnabled": bool(is_enabled) if str(is_enabled) != "" else None,
        "isVisible": bool(is_visible) if str(is_visible) != "" else None,
        "isOffscreen": str(is_offscreen),
        "helpText": help_text,
        "providerDescription": provider_description,
        "locatorScore": locator_score,
        "locatorReason": locator_reason,
        "recommendedTargetMethod": locator_method,
        "recommendedTargetValue": locator_value,
        "uiPath": ui_path,
        "parentPath": " > ".join(item for item in path_segments[:-1] if item),
        "auxChecks": build_aux_checks(inspect_data),
        "inspectData": inspect_data,
        "rawInspectText": raw_inspect_text,
    }


def _append_wrapper_info(flat_controls, seen_identities, wrapper, target_window, root_handle="", max_depth=DEFAULT_MAX_DEPTH):
    if wrapper is None:
        return False
    identity = _build_wrapper_identity(wrapper)
    if not identity or identity in seen_identities:
        return False
    path_segments = _build_path_segments_from_wrapper(wrapper, root_handle=root_handle)
    if not path_segments:
        return False
    depth = max(0, len(path_segments) - 1)
    if depth > max_depth:
        return False
    info = _extract_wrapper_info(wrapper, depth, len(flat_controls) + 1, path_segments, target_window)
    flat_controls.append(info)
    seen_identities.add(_build_flat_control_identity(info))
    seen_identities.add(identity)
    return True


def _walk_wrapper(wrapper, depth, max_depth, target_window, flat_controls, siblings_index=1, path_segments=None, parent_index=-1):
    path_segments = list(path_segments or [])
    info = _extract_wrapper_info(wrapper, depth, len(flat_controls) + 1, path_segments, target_window)
    info["treeLevel"] = depth
    info["parentIndex"] = parent_index
    info["siblingsIndex"] = siblings_index
    current_index = len(flat_controls)
    node = dict(info)
    node["children"] = []
    flat_controls.append(info)

    if depth >= max_depth:
        return node

    children = _safe_get_value(lambda: wrapper.children(), [])
    for child_index, child in enumerate(children, start=1):
        child_display_name = _build_display_name(
            {
                "name": str(_safe_get_value(lambda: child.window_text(), "")).strip(),
                "automationId": str(_safe_get_value(lambda: getattr(child.element_info, "automation_id", ""), "")).strip(),
                "className": str(_safe_get_value(lambda: child.class_name(), "")).strip(),
                "controlType": str(_safe_get_value(lambda: getattr(child.element_info, "control_type", ""), "")).strip(),
                "localizedControlType": str(_safe_get_value(lambda: getattr(child.element_info, "localized_control_type", ""), "")).strip(),
            },
            "控件",
            child_index,
        )
        child_path = path_segments + [child_display_name]
        node["children"].append(
            _walk_wrapper(
                child,
                depth + 1,
                max_depth,
                target_window,
                flat_controls,
                siblings_index=child_index,
                path_segments=child_path,
                parent_index=current_index,
            )
        )

    node["inspectData"]["children"] = [
        f"{child.get('displayName', '')} | {child.get('className', '')} | {child.get('controlType', '')}".strip(" |")
        for child in node["children"][:12]
    ]
    return node


def _collect_descendant_wrappers(wrapper, target_window, flat_controls, seen_identities, root_handle="", max_depth=DEFAULT_MAX_DEPTH):
    descendants = _safe_get_value(lambda: wrapper.descendants(), [])
    for candidate in descendants:
        _append_wrapper_info(
            flat_controls,
            seen_identities,
            candidate,
            target_window,
            root_handle=root_handle,
            max_depth=max_depth,
        )


def _generate_probe_points(region_rect):
    region_rect = _normalize_rect_dict(region_rect)
    if not region_rect:
        return []
    width = max(1, region_rect["right"] - region_rect["left"])
    height = max(1, region_rect["bottom"] - region_rect["top"])
    cols = max(3, min(12, int(width / 90) + 1))
    rows = max(3, min(10, int(height / 90) + 1))
    points = []
    for row in range(rows):
        for col in range(cols):
            x = region_rect["left"] + int((col + 0.5) * width / cols)
            y = region_rect["top"] + int((row + 0.5) * height / rows)
            points.append((x, y))
    return points


def _collect_region_probe_wrappers(
    backend,
    region_rect,
    target_window,
    flat_controls,
    seen_identities,
    root_handle="",
    max_depth=DEFAULT_MAX_DEPTH,
):
    region_rect = _normalize_rect_dict(region_rect)
    if not region_rect or Desktop is None:
        return
    desktop = Desktop(backend=backend)
    for x, y in _generate_probe_points(region_rect):
        wrapper = _safe_get_value(lambda: desktop.from_point(x, y), None)
        if wrapper is None:
            continue
        current = wrapper
        for _ in range(max_depth + 4):
            if current is None:
                break
            _append_wrapper_info(
                flat_controls,
                seen_identities,
                current,
                target_window,
                root_handle=root_handle,
                max_depth=max_depth,
            )
            current_handle = _get_wrapper_handle(current)
            if root_handle and current_handle and str(current_handle) == str(root_handle):
                break
            parent = _safe_get_value(lambda: current.parent(), None)
            if not parent or parent is current:
                break
            current = parent


def _build_target_window_info(window_wrapper):
    element_info = _safe_get_value(lambda: window_wrapper.element_info, None)
    return {
        "title": str(_safe_get_value(lambda: window_wrapper.window_text(), "")).strip(),
        "className": str(_safe_get_value(lambda: window_wrapper.class_name(), "")).strip(),
        "processId": str(_safe_get_value(lambda: getattr(element_info, "process_id", ""), "")).strip(),
        "handle": str(_safe_get_value(lambda: getattr(element_info, "handle", ""), "")).strip(),
        "frameworkId": str(_safe_get_value(lambda: getattr(element_info, "framework_id", ""), "")).strip(),
    }


def _ensure_unique_control_id(control_definition, existing_ids):
    base_id = str(control_definition.get("id", "")).strip() or "control"
    unique_id = base_id
    suffix = 2
    while unique_id in existing_ids:
        unique_id = f"{base_id}_{suffix}"
        suffix += 1
    control_definition["id"] = unique_id
    existing_ids.add(unique_id)
    return control_definition


def _build_control_definition_from_flat(flat_item, existing_ids):
    inspect_data = dict(flat_item.get("inspectData", {}) or {})
    display_name = (
        str(flat_item.get("savedControlName", "")).strip()
        or str(flat_item.get("suggestedControlName", "")).strip()
        or str(flat_item.get("displayName", "")).strip()
        or "新控件"
    )
    control_type = str(flat_item.get("controlType", "")).strip()
    base_id = slugify_filename("_".join(item for item in [inspect_data.get("automationId", ""), display_name, control_type] if item), "control")
    control_definition = {
        "id": base_id,
        "name": display_name,
        "role": f"来自控件库扫描：{flat_item.get('windowTitle', '')}",
        "enabled": True,
        "windowTitle": str(flat_item.get("windowTitle", "")).strip(),
        "targetMethod": str(flat_item.get("recommendedTargetMethod", "")).strip(),
        "targetValue": str(flat_item.get("recommendedTargetValue", "")).strip(),
        "templateKey": "",
        "uiPath": str(flat_item.get("uiPath", "")).strip(),
        "notes": (
            f"由控件库扫描生成，定位评分={flat_item.get('locatorScore', 0)}，"
            f"策略={flat_item.get('locatorReason', '')}，"
            f"质量={flat_item.get('qualityTier', '')}，"
            f"说明={flat_item.get('qualityReason', '')}"
        ),
        "rawInspectText": str(flat_item.get("rawInspectText", "")).strip(),
        "auxChecks": [str(item).strip() for item in flat_item.get("auxChecks", []) if str(item).strip()],
        "inspectData": inspect_data,
        "source": "control_map",
    }
    return _ensure_unique_control_id(control_definition, existing_ids)


def _expand_backend_candidates(backend):
    backend = str(backend or DEFAULT_BACKEND).strip().lower() or DEFAULT_BACKEND
    if backend == "smart":
        return ["uia", "win32"]
    if backend in {"uia", "win32"}:
        return [backend]
    return ["uia"]


def _score_region_candidate(item, region_rect):
    item_rect = _normalize_rect_dict(item.get("boundingBox"))
    region_rect = _normalize_rect_dict(region_rect)
    if not item_rect or not region_rect:
        return None
    intersection = _intersection_area(item_rect, region_rect)
    if intersection <= 0:
        return None
    item_area = _rect_area(item_rect) or 1
    overlap_ratio = intersection / item_area
    center_distance = _distance(_rect_center(item_rect), _rect_center(region_rect))
    score = (
        int(item.get("locatorScore", 0) or 0) * 1000
        + int(overlap_ratio * 1000)
        - int(center_distance)
        - int(item.get("depth", 0) or 0) * 25
    )
    if _rect_contains(item_rect, region_rect):
        score += 300
    return score


def _filter_flat_controls_by_region(flat_controls, region_rect):
    region_rect = _normalize_rect_dict(region_rect)
    if not region_rect:
        return list(flat_controls)
    ranked = []
    for item in flat_controls:
        item_rect = _normalize_rect_dict(item.get("boundingBox"))
        if not item_rect or not _rect_intersects(item_rect, region_rect):
            continue
        score = _score_region_candidate(item, region_rect)
        if score is None:
            continue
        ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in ranked]


def _is_low_value_root_candidate(item, target_window_title):
    if not isinstance(item, dict):
        return False
    if int(item.get("depth", 0) or 0) != 0:
        return False
    control_type = str(item.get("controlType", "")).strip().lower()
    if control_type not in {"", "window"}:
        return False
    name = str(item.get("name", "")).strip()
    return name == str(target_window_title or "").strip()


def _prune_low_value_region_controls(region_controls, target_window_title):
    region_controls = list(region_controls or [])
    if len(region_controls) <= 1:
        return region_controls
    deeper_exists = any(int(item.get("depth", 0) or 0) > 0 for item in region_controls)
    if not deeper_exists:
        return region_controls
    pruned = [item for item in region_controls if not _is_low_value_root_candidate(item, target_window_title)]
    return pruned or region_controls


def _filter_noise_controls(controls, exclude_offscreen=True, exclude_unidentified_containers=True):
    """A+C: 过滤离屏控件和无标识容器，减少无关信息"""
    if not controls:
        return controls
    filtered = []
    for item in controls:
        if not isinstance(item, dict):
            continue
        # A: 过滤离屏控件（IsOffscreen=True）
        if exclude_offscreen and str(item.get("isOffscreen", "")).strip().lower() == "true":
            continue
        # C: 过滤无 name 且无 automationId 的容器（Custom/Pane/Group）
        if exclude_unidentified_containers:
            control_type = str(item.get("controlType", "")).strip().lower()
            has_name = bool(str(item.get("name", "")).strip())
            has_automation_id = bool(str(item.get("automationId", "")).strip())
            if control_type in {"custom", "pane", "group"} and not has_name and not has_automation_id:
                continue
        filtered.append(item)
    return filtered


def _merge_flat_controls(*groups):
    merged = []
    seen = set()
    for group in groups:
        for item in group or []:
            identity = _build_flat_control_identity(item)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            merged.append(item)
    return merged


def _build_saved_control_id_from_name(name, fallback="control"):
    return slugify_filename(name, fallback)


def _build_control_group_key(flat_control):
    if not isinstance(flat_control, dict):
        return "unknown"
    target_method = str(flat_control.get("recommendedTargetMethod", "")).strip()
    target_value = str(flat_control.get("recommendedTargetValue", "")).strip()
    if target_method and target_value:
        return f"locator|{target_method}|{target_value}"
    automation_id = str(flat_control.get("automationId", "")).strip()
    control_type = str(flat_control.get("controlType", "")).strip()
    class_name = str(flat_control.get("className", "")).strip()
    ui_path = str(flat_control.get("uiPath", "")).strip()
    return "|".join(["fallback", automation_id, class_name, control_type, ui_path])


def _build_group_display_name(flat_control, fallback="控件"):
    if not isinstance(flat_control, dict):
        return fallback
    return (
        str(flat_control.get("savedControlName", "")).strip()
        or str(flat_control.get("suggestedControlName", "")).strip()
        or str(flat_control.get("displayName", "")).strip()
        or str(flat_control.get("automationId", "")).strip()
        or str(flat_control.get("name", "")).strip()
        or str(flat_control.get("className", "")).strip()
        or fallback
    )


def _rank_group_candidate(flat_control, preferred_index=0):
    if not isinstance(flat_control, dict):
        return (-1, -1, -1, preferred_index * -1)
    score = int(flat_control.get("locatorScore", 0) or 0)
    has_automation_id = 1 if str(flat_control.get("automationId", "")).strip() else 0
    control_type = str(flat_control.get("controlType", "")).strip().lower()
    preferred_type = 1 if control_type in {"button", "edit", "combobox", "menuitem", "tabitem", "treeitem", "text"} else 0
    return (score, has_automation_id, preferred_type, preferred_index * -1)


def _scan_single_backend_payload(
    window_keyword="",
    backend="uia",
    use_foreground=False,
    max_depth=DEFAULT_MAX_DEPTH,
    region_rect=None,
    excluded_process_ids=None,
    excluded_titles=None,
    exclude_offscreen=True,
    exclude_unidentified_containers=True,
):
    backend = str(backend or "uia").strip().lower() or "uia"
    max_depth = max(0, int(max_depth))
    if use_foreground:
        target_window_wrapper = _get_foreground_wrapper(
            backend,
            excluded_process_ids=excluded_process_ids,
            excluded_titles=excluded_titles,
        )
    else:
        target_window_wrapper = _find_window_by_keyword(
            window_keyword,
            backend,
            excluded_process_ids=excluded_process_ids,
            excluded_titles=excluded_titles,
        )

    target_window = _build_target_window_info(target_window_wrapper)
    root_handle = str(target_window.get("handle", "")).strip()
    root_display_name = _build_display_name(
        {
            "name": target_window.get("title", ""),
            "className": target_window.get("className", ""),
            "controlType": "Window",
        },
        "窗口",
        1,
    )
    flat_controls = []
    control_tree = _walk_wrapper(
        target_window_wrapper,
        depth=0,
        max_depth=max_depth,
        target_window=target_window,
        flat_controls=flat_controls,
        path_segments=[root_display_name],
    )
    seen_identities = {_build_flat_control_identity(item) for item in flat_controls}
    _collect_descendant_wrappers(
        target_window_wrapper,
        target_window,
        flat_controls,
        seen_identities,
        root_handle=root_handle,
        max_depth=max_depth,
    )
    _collect_region_probe_wrappers(
        backend,
        region_rect,
        target_window,
        flat_controls,
        seen_identities,
        root_handle=root_handle,
        max_depth=max_depth,
    )
    for item in flat_controls:
        item["scanBackend"] = backend
    existing_ids = set()
    region_controls = _filter_flat_controls_by_region(flat_controls, region_rect)
    region_controls = _prune_low_value_region_controls(region_controls, target_window.get("title", ""))
    _enrich_flat_controls(region_controls, target_window)
    # A+C: 过滤离屏控件和无标识容器
    region_controls = _filter_noise_controls(
        region_controls,
        exclude_offscreen=exclude_offscreen,
        exclude_unidentified_containers=exclude_unidentified_containers,
    )
    control_definitions = [_build_control_definition_from_flat(item, existing_ids) for item in region_controls]
    by_type = {}
    for item in region_controls:
        control_type = str(item.get("controlType", "")).strip() or "Unknown"
        by_type[control_type] = by_type.get(control_type, 0) + 1

    return {
        "schemaVersion": "1.0",
        "scanMeta": {
            "scanTime": datetime.now().isoformat(timespec="seconds"),
            "backend": backend,
            "mode": "foreground" if use_foreground else "keyword",
            "windowKeyword": str(window_keyword or "").strip(),
            "maxDepth": max_depth,
            "totalControls": len(region_controls),
            "rawTotalControls": len(flat_controls),
            "regionRect": _normalize_rect_dict(region_rect),
            "controlTypeSummary": dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
        },
        "targetWindow": target_window,
        "controlsTree": control_tree,
        "flatControls": region_controls,
        "controlDefinitions": control_definitions,
    }


def build_control_map_payload(
    window_keyword="",
    backend=DEFAULT_BACKEND,
    use_foreground=False,
    max_depth=DEFAULT_MAX_DEPTH,
    region_rect=None,
    excluded_process_ids=None,
    excluded_titles=None,
    exclude_offscreen=True,
    exclude_unidentified_containers=True,
):
    requested_backend = str(backend or DEFAULT_BACKEND).strip().lower() or DEFAULT_BACKEND
    backend_candidates = _expand_backend_candidates(requested_backend)
    payloads = []
    for backend_name in backend_candidates:
        payloads.append(
            _scan_single_backend_payload(
                window_keyword=window_keyword,
                backend=backend_name,
                use_foreground=use_foreground,
                max_depth=max_depth,
                region_rect=region_rect,
                excluded_process_ids=excluded_process_ids,
                excluded_titles=excluded_titles,
                exclude_offscreen=exclude_offscreen,
                exclude_unidentified_containers=exclude_unidentified_containers,
            )
        )

    if len(payloads) == 1 and region_rect:
        only_payload = payloads[0]
        only_controls = only_payload.get("flatControls", []) or []
        only_root = len(only_controls) <= 1 or all(
            _is_low_value_root_candidate(item, only_payload.get("targetWindow", {}).get("title", ""))
            for item in only_controls
        )
        if only_root:
            alternate_backend = "win32" if backend_candidates[0] == "uia" else "uia"
            try:
                payloads.append(
                    _scan_single_backend_payload(
                        window_keyword=window_keyword,
                        backend=alternate_backend,
                        use_foreground=use_foreground,
                        max_depth=max_depth,
                        region_rect=region_rect,
                        excluded_process_ids=excluded_process_ids,
                        excluded_titles=excluded_titles,
                    )
                )
            except Exception:
                pass

    base_payload = payloads[0]
    if len(payloads) == 1:
        base_payload["scanMeta"]["requestedBackend"] = requested_backend
        base_payload["scanMeta"]["mergedBackends"] = [payloads[0].get("scanMeta", {}).get("backend", requested_backend)]
        return base_payload

    merged_controls = _merge_flat_controls(*(payload.get("flatControls", []) for payload in payloads))
    merged_controls = _filter_flat_controls_by_region(merged_controls, region_rect)
    merged_controls = _prune_low_value_region_controls(merged_controls, base_payload.get("targetWindow", {}).get("title", ""))
    _enrich_flat_controls(merged_controls, base_payload.get("targetWindow", {}))
    existing_ids = set()
    merged_definitions = [_build_control_definition_from_flat(item, existing_ids) for item in merged_controls]
    by_type = {}
    for item in merged_controls:
        control_type = str(item.get("controlType", "")).strip() or "Unknown"
        by_type[control_type] = by_type.get(control_type, 0) + 1

    raw_total_controls = 0
    merged_backends = []
    for payload in payloads:
        scan_meta = payload.get("scanMeta", {}) or {}
        raw_total_controls += int(scan_meta.get("rawTotalControls", 0) or 0)
        backend_name = str(scan_meta.get("backend", "")).strip()
        if backend_name and backend_name not in merged_backends:
            merged_backends.append(backend_name)

    return {
        "schemaVersion": "1.0",
        "scanMeta": {
            "scanTime": datetime.now().isoformat(timespec="seconds"),
            "backend": requested_backend,
            "requestedBackend": requested_backend,
            "mergedBackends": merged_backends,
            "mode": "foreground" if use_foreground else "keyword",
            "windowKeyword": str(window_keyword or "").strip(),
            "maxDepth": max_depth,
            "totalControls": len(merged_controls),
            "rawTotalControls": raw_total_controls,
            "regionRect": _normalize_rect_dict(region_rect),
            "controlTypeSummary": dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
        },
        "targetWindow": base_payload.get("targetWindow", {}),
        "controlsTree": base_payload.get("controlsTree", {}),
        "flatControls": merged_controls,
        "controlDefinitions": merged_definitions,
    }


def save_control_map_payload(payload, output_path=""):
    ensure_directory(CONTROL_MAP_DIR)
    if not output_path:
        title = ((payload.get("targetWindow", {}) or {}).get("title", "") or "window").strip()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(CONTROL_MAP_DIR, f"{timestamp}_{slugify_filename(title)}_control_map.json")
    with open(output_path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
    return output_path


class RegionPickerOverlay:
    def __init__(self, parent, on_complete=None):
        self.parent = parent
        self.on_complete = on_complete
        self.result = None
        self.start_x = 0
        self.start_y = 0
        self.rect_id = None

        self.window = tk.Toplevel(parent)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.25)
        self.window.configure(bg="#111111")
        self.window.geometry(
            f"{self.window.winfo_screenwidth()}x{self.window.winfo_screenheight()}+0+0"
        )

        self.canvas = tk.Canvas(self.window, bg="#111111", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.tip_id = self.canvas.create_text(
            30,
            30,
            anchor="nw",
            fill="#ffffff",
            text="按住鼠标左键拖动画框，松开完成；按 Esc 取消。",
            font=("Microsoft YaHei UI", 14, "bold"),
        )
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.window.bind("<Escape>", lambda _event: self.finish(None))
        self.window.focus_force()

    def _on_press(self, event):
        self.start_x = int(event.x_root)
        self.start_y = int(event.y_root)
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="#22c55e",
            width=3,
            fill="#60a5fa",
            stipple="gray25",
        )

    def _on_drag(self, event):
        if self.rect_id is None:
            return
        self.canvas.coords(self.rect_id, self.start_x, self.start_y, int(event.x_root), int(event.y_root))

    def _on_release(self, event):
        end_x = int(event.x_root)
        end_y = int(event.y_root)
        left = min(self.start_x, end_x)
        top = min(self.start_y, end_y)
        right = max(self.start_x, end_x)
        bottom = max(self.start_y, end_y)
        if right - left < 6 or bottom - top < 6:
            self.finish(None)
            return
        self.finish({"left": left, "top": top, "right": right, "bottom": bottom})

    def finish(self, rect):
        self.result = _normalize_rect_dict(rect)
        try:
            self.window.destroy()
        finally:
            if self.on_complete:
                self.on_complete(self.result)


class ControlMapBuilderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("WT 控件库采集器")
        self.root.geometry("1400x860")

        self.var_scan_mode = tk.StringVar(value="foreground")
        self.var_window_keyword = tk.StringVar(value="")
        self.var_backend = tk.StringVar(value=DEFAULT_BACKEND)
        self.var_max_depth = tk.IntVar(value=DEFAULT_MAX_DEPTH)
        self.var_pick_delay = tk.IntVar(value=DEFAULT_PICK_DELAY_SECONDS)
        self.var_exclude_offscreen = tk.BooleanVar(value=True)
        self.var_exclude_unidentified = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="准备就绪：建议先切到目标软件窗口，再点击“扫描并保存”。")
        self.var_summary = tk.StringVar(value="尚未扫描控件树。")
        self.current_payload = None
        self.current_output_path = ""
        self.current_region_rect = None
        self.checked_control_indices = set()
        self.control_groups = []
        self.var_saved_control_name = tk.StringVar(value="")
        self.var_saved_control_id = tk.StringVar(value="")

        self._build_ui()

    def _build_ui(self):
        toolbar = tk.LabelFrame(self.root, text="扫描配置", padx=10, pady=10)
        toolbar.pack(fill=tk.X, padx=10, pady=10)

        tk.Radiobutton(toolbar, text="当前前台窗口", variable=self.var_scan_mode, value="foreground").grid(row=0, column=0, sticky="w")
        tk.Radiobutton(toolbar, text="按标题关键字", variable=self.var_scan_mode, value="keyword").grid(row=0, column=1, sticky="w")
        tk.Label(toolbar, text="窗口关键字").grid(row=0, column=2, sticky="e", padx=(10, 4))
        tk.Entry(toolbar, textvariable=self.var_window_keyword, width=28).grid(row=0, column=3, sticky="ew")
        tk.Label(toolbar, text="backend").grid(row=0, column=4, sticky="e", padx=(10, 4))
        ttk.Combobox(toolbar, textvariable=self.var_backend, values=BACKEND_OPTIONS, width=8, state="readonly").grid(row=0, column=5, sticky="w")
        tk.Label(toolbar, text="最大深度").grid(row=0, column=6, sticky="e", padx=(10, 4))
        tk.Spinbox(toolbar, from_=0, to=12, textvariable=self.var_max_depth, width=6).grid(row=0, column=7, sticky="w")
        tk.Label(toolbar, text="画框延迟").grid(row=0, column=8, sticky="e", padx=(10, 4))
        tk.Spinbox(toolbar, from_=1, to=10, textvariable=self.var_pick_delay, width=6).grid(row=0, column=9, sticky="w")
        toolbar.columnconfigure(3, weight=1)

        filter_row = tk.Frame(toolbar)
        filter_row.grid(row=2, column=0, columnspan=10, sticky="w", pady=(6, 0))
        tk.Checkbutton(filter_row, text="过滤离屏控件", variable=self.var_exclude_offscreen).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(filter_row, text="过滤无标识容器", variable=self.var_exclude_unidentified).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(filter_row, text="(勾选后采集时自动丢弃离屏控件和无 name/automationId 的容器，减少无关信息)", fg="#6b7280").pack(side=tk.LEFT)

        button_row = tk.Frame(toolbar)
        button_row.grid(row=1, column=0, columnspan=10, sticky="ew", pady=(10, 0))
        tk.Button(button_row, text="扫描并保存", command=self.cmd_scan_and_save, bg="#d1fae5").pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="仅扫描预览", command=self.cmd_scan_preview).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="画框区域采集并保存", command=self.cmd_region_scan_and_save, bg="#bfdbfe").pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="画框区域预览", command=self.cmd_region_scan_preview, bg="#bfdbfe").pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="智能勾选", command=self.cmd_smart_check_results).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="全选结果", command=self.cmd_check_all_results).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="清空勾选", command=self.cmd_clear_checked_results).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="保存当前结果", command=self.cmd_save_current_payload).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="打开控件库目录", command=self.cmd_open_control_map_dir).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="复制所选定位", command=self.cmd_copy_selected_locator).pack(side=tk.LEFT, padx=3)
        tk.Label(button_row, textvariable=self.var_status, fg="#555555").pack(side=tk.RIGHT)

        summary = tk.LabelFrame(self.root, text="扫描概览", padx=10, pady=10)
        summary.pack(fill=tk.X, padx=10)
        tk.Label(summary, textvariable=self.var_summary, justify=tk.LEFT, anchor="w", wraplength=1320).pack(fill=tk.X)

        body = tk.PanedWindow(self.root, orient=tk.HORIZONTAL, sashrelief=tk.RAISED)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = tk.LabelFrame(body, text="控件候选", padx=10, pady=10)
        body.add(left, stretch="always")
        right = tk.LabelFrame(body, text="控件详情", padx=10, pady=10)
        body.add(right, stretch="always")

        tree_style = ttk.Style()
        tree_style.configure("ControlMap.Treeview", rowheight=28)
        tree_style.configure("ControlMap.Treeview.Heading", padding=(8, 6))

        self.control_tree = ttk.Treeview(
            left,
            columns=("pick", "seq", "name", "type", "locator", "score", "path"),
            style="ControlMap.Treeview",
            show="tree headings",
        )
        self.control_tree.heading("#0", text="归类")
        self.control_tree.column("#0", width=240, anchor="w", stretch=True, minwidth=180)
        self.control_tree.heading("pick", text="勾选")
        self.control_tree.heading("seq", text="#")
        self.control_tree.heading("name", text="控件")
        self.control_tree.heading("type", text="类型")
        self.control_tree.heading("locator", text="推荐定位")
        self.control_tree.heading("score", text="评分")
        self.control_tree.heading("path", text="路径")
        self.control_tree.column("pick", width=48, anchor="center")
        self.control_tree.column("seq", width=50, anchor="center")
        self.control_tree.column("name", width=260, anchor="w", stretch=True, minwidth=180)
        self.control_tree.column("type", width=150, anchor="w", stretch=False, minwidth=120)
        self.control_tree.column("locator", width=300, anchor="w", stretch=True, minwidth=220)
        self.control_tree.column("score", width=60, anchor="center")
        self.control_tree.column("path", width=460, anchor="w", stretch=True, minwidth=260)
        self.control_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        self.control_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.control_tree.bind("<Button-1>", self._on_tree_click, add="+")

        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.control_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scrollbar = ttk.Scrollbar(left, orient="horizontal", command=self.control_tree.xview)
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        self.control_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=h_scrollbar.set)

        rename_frame = tk.LabelFrame(right, text="保存前命名", padx=10, pady=10)
        rename_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Label(rename_frame, text="保存控件名").grid(row=0, column=0, sticky="w")
        tk.Entry(rename_frame, textvariable=self.var_saved_control_name).grid(row=0, column=1, sticky="ew", padx=(8, 12))
        tk.Label(rename_frame, text="控件ID").grid(row=0, column=2, sticky="w")
        tk.Entry(rename_frame, textvariable=self.var_saved_control_id).grid(row=0, column=3, sticky="ew", padx=(8, 12))
        tk.Button(rename_frame, text="应用到当前控件", command=self.cmd_apply_current_control_alias, bg="#d1fae5").grid(row=0, column=4)
        tk.Label(
            rename_frame,
            text="扫描名通常只是系统原始控件名。保存前可改成业务语义名称，最终只保存已勾选控件。",
            fg="#555555",
            justify=tk.LEFT,
            anchor="w",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        rename_frame.columnconfigure(1, weight=1)
        rename_frame.columnconfigure(3, weight=1)

        self.preview_text = scrolledtext.ScrolledText(right, wrap=tk.WORD, font=("Consolas", 10))
        self.preview_text.pack(fill=tk.BOTH, expand=True)

    def _resolve_scan_args(self):
        return {
            "window_keyword": self.var_window_keyword.get().strip(),
            "backend": self.var_backend.get().strip() or DEFAULT_BACKEND,
            "use_foreground": self.var_scan_mode.get().strip() == "foreground",
            "max_depth": self.var_max_depth.get(),
            "exclude_offscreen": bool(self.var_exclude_offscreen.get()),
            "exclude_unidentified_containers": bool(self.var_exclude_unidentified.get()),
        }

    def _rebuild_control_groups(self):
        self.control_groups = []
        flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        grouped = {}
        ordered_keys = []
        for index, item in enumerate(flat_controls):
            group_key = _build_control_group_key(item)
            if group_key not in grouped:
                grouped[group_key] = {"key": group_key, "memberIndexes": []}
                ordered_keys.append(group_key)
            grouped[group_key]["memberIndexes"].append(index)
        for group_number, group_key in enumerate(ordered_keys, start=1):
            group = grouped[group_key]
            member_indexes = group["memberIndexes"]
            best_index = max(
                member_indexes,
                key=lambda idx: _rank_group_candidate(flat_controls[idx], preferred_index=idx),
            )
            best_item = flat_controls[best_index]
            group_name = _build_group_display_name(best_item, fallback=f"控件组 {group_number}")
            quality_tier = str(best_item.get("qualityTier", "")).strip() or "建议忽略"
            quality_reason = str(best_item.get("qualityReason", "")).strip()
            self.control_groups.append(
                {
                    "key": group_key,
                    "groupNumber": group_number,
                    "groupName": group_name,
                    "memberIndexes": member_indexes,
                    "bestIndex": best_index,
                    "isGrouped": len(member_indexes) >= 2,
                    "qualityTier": quality_tier,
                    "qualityReason": quality_reason,
                }
            )

    def _find_group_by_index(self, index):
        for group in self.control_groups:
            if index in group.get("memberIndexes", []):
                return group
        return None

    def _build_default_checked_indices(self):
        smart_checked = {
            group.get("bestIndex")
            for group in self.control_groups
            if group.get("bestIndex") is not None and _should_default_select_group(group)
        }
        if smart_checked:
            return smart_checked
        return {group.get("bestIndex") for group in self.control_groups if group.get("bestIndex") is not None}

    def _find_group_by_tree_iid(self, tree_iid):
        tree_iid = str(tree_iid or "")
        if tree_iid.startswith("group:"):
            target_key = tree_iid.split(":", 1)[1]
            for group in self.control_groups:
                if group.get("key") == target_key:
                    return group
            return None
        if tree_iid.startswith("item:"):
            try:
                index = int(tree_iid.split(":", 1)[1])
            except Exception:
                return None
            return self._find_group_by_index(index)
        return None

    def _get_excluded_scan_context(self):
        process_id = str(os.getpid())
        titles = {str(self.root.title()).strip(), "WT 控件库采集器"}
        return {
            "excluded_process_ids": [process_id],
            "excluded_titles": [item for item in titles if item],
        }

    def _run_scan(self, auto_save, region_rect=None):
        args = self._resolve_scan_args()
        args.update(self._get_excluded_scan_context())
        args["region_rect"] = region_rect
        try:
            payload = build_control_map_payload(**args)
        except Exception as exc:
            messagebox.showerror("扫描失败", f"控件树扫描失败：\n{exc}")
            self.var_status.set(f"扫描失败：{exc}")
            return

        self.current_payload = payload
        self.current_output_path = ""
        self.current_region_rect = _normalize_rect_dict(region_rect)
        self._rebuild_control_groups()
        self.checked_control_indices = self._build_default_checked_indices()
        self.var_saved_control_name.set("")
        self.var_saved_control_id.set("")
        if auto_save:
            try:
                filtered_payload = self._build_filtered_payload_for_save()
                self.current_output_path = save_control_map_payload(filtered_payload)
            except Exception as exc:
                messagebox.showerror("保存失败", f"扫描完成，但保存控件库失败：\n{exc}")
                self.var_status.set(f"扫描完成，但保存失败：{exc}")
        self._refresh_tree()
        self._refresh_summary()
        if auto_save and self.current_output_path:
            self.var_status.set(f"已完成控件库扫描并保存：{self.current_output_path}")
        elif self.current_region_rect:
            self.var_status.set("已完成画框区域控件扫描，当前结果尚未保存。")
        else:
            self.var_status.set("已完成控件树扫描，当前结果尚未保存。")

    def _refresh_summary(self):
        if not isinstance(self.current_payload, dict):
            self.var_summary.set("尚未扫描控件树。")
            return
        target_window = self.current_payload.get("targetWindow", {}) or {}
        scan_meta = self.current_payload.get("scanMeta", {}) or {}
        summary = [
            f"目标窗口：{target_window.get('title', '')}",
            f"类名：{target_window.get('className', '')}",
            f"backend：{scan_meta.get('backend', '')}",
            f"最大深度：{scan_meta.get('maxDepth', '')}",
            f"控件总数：{scan_meta.get('totalControls', 0)}",
            f"当前勾选保存：{len(self.checked_control_indices)}",
        ]
        grouped_count = sum(1 for group in self.control_groups if group.get("isGrouped"))
        single_count = sum(1 for group in self.control_groups if not group.get("isGrouped"))
        summary.append(f"归类控件组：{grouped_count}")
        summary.append(f"未归类单项：{single_count}")
        if self.control_groups:
            tier_summary = {}
            for group in self.control_groups:
                tier = str(group.get("qualityTier", "")).strip() or "未分类"
                tier_summary[tier] = tier_summary.get(tier, 0) + 1
            summary.append("质量分级：" + ", ".join(f"{key}={value}" for key, value in tier_summary.items()))
        merged_backends = scan_meta.get("mergedBackends", []) or []
        if merged_backends:
            summary.append(f"实际采集后端：{', '.join(merged_backends)}")
        if scan_meta.get("rawTotalControls", 0) and scan_meta.get("rawTotalControls", 0) != scan_meta.get("totalControls", 0):
            summary.append(f"整窗原始控件数：{scan_meta.get('rawTotalControls', 0)}")
        region_rect = scan_meta.get("regionRect")
        if region_rect:
            summary.append(
                "采集区域："
                f"({region_rect.get('left')},{region_rect.get('top')})"
                f"-({region_rect.get('right')},{region_rect.get('bottom')})"
            )
        if self.current_output_path:
            summary.append(f"保存路径：{self.current_output_path}")
        type_summary = scan_meta.get("controlTypeSummary", {}) or {}
        if type_summary:
            top_types = ", ".join(f"{key}={value}" for key, value in list(type_summary.items())[:8])
            summary.append(f"类型分布：{top_types}")
        self.var_summary.set("\n".join(summary))

    def _refresh_tree(self):
        self.control_tree.delete(*self.control_tree.get_children())
        flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        for group in self.control_groups:
            member_indexes = group.get("memberIndexes", [])
            best_index = group.get("bestIndex")
            checked_count = sum(1 for index in member_indexes if index in self.checked_control_indices)
            best_item = flat_controls[best_index] if best_index is not None and best_index < len(flat_controls) else {}
            if not group.get("isGrouped") and member_indexes:
                index = member_indexes[0]
                item = flat_controls[index]
                locator = f"{item.get('recommendedTargetMethod', '')}:{item.get('recommendedTargetValue', '')}".strip(":")
                display_name = item.get("savedControlName", "") or item.get("suggestedControlName", "") or item.get("displayName", "")
                tier_label = str(item.get("qualityTier", "")).strip() or "未分类"
                self.control_tree.insert(
                    "",
                    tk.END,
                    iid=f"item:{index}",
                    text=tier_label,
                    values=(
                        "[x]" if index in self.checked_control_indices else "[ ]",
                        index + 1,
                        display_name,
                        f"{item.get('controlType', '') or 'Unknown'}@{item.get('scanBackend', '') or '-'}",
                        locator,
                        item.get("locatorScore", 0),
                        item.get("uiPath", ""),
                    ),
                )
                continue
            group_iid = f"group:{group.get('key')}"
            group_type = str(best_item.get("controlType", "")).strip() or "Unknown"
            tier_label = str(group.get("qualityTier", "")).strip() or "建议忽略"
            self.control_tree.insert(
                "",
                tk.END,
                iid=group_iid,
                open=True,
                text=f"{tier_label} | 第 {group.get('groupNumber')} 组",
                values=(
                    "[x]" if checked_count == len(member_indexes) and member_indexes else "[ ]",
                    len(member_indexes),
                    group.get("groupName", ""),
                    f"{group_type} | 最优 {int(best_item.get('locatorScore', 0) or 0)}",
                    str(group.get("qualityReason", "")).strip() or "同组候选",
                    checked_count,
                    str(best_item.get("uiPath", "")).strip(),
                ),
            )
            for child_order, index in enumerate(member_indexes, start=1):
                item = flat_controls[index]
                locator = f"{item.get('recommendedTargetMethod', '')}:{item.get('recommendedTargetValue', '')}".strip(":")
                display_name = item.get("savedControlName", "") or item.get("suggestedControlName", "") or item.get("displayName", "")
                child_label = ("推荐保留" if index == best_index else f"候选 {child_order}") + f" | {item.get('qualityTier', '') or '未分类'}"
                self.control_tree.insert(
                    group_iid,
                    tk.END,
                    iid=f"item:{index}",
                    text=child_label,
                    values=(
                        "[x]" if index in self.checked_control_indices else "[ ]",
                        index + 1,
                        display_name,
                        f"{item.get('controlType', '') or 'Unknown'}@{item.get('scanBackend', '') or '-'}",
                        locator,
                        item.get("locatorScore", 0),
                        item.get("uiPath", ""),
                    ),
                )

    def _on_tree_click(self, event):
        row_id = self.control_tree.identify_row(event.y)
        column_id = self.control_tree.identify_column(event.x)
        if not row_id:
            return
        if column_id == "#1":
            if str(row_id).startswith("group:"):
                group = self._find_group_by_tree_iid(row_id)
                if group:
                    self._toggle_group_checked(group)
            elif str(row_id).startswith("item:"):
                self._toggle_checked_index(int(str(row_id).split(":", 1)[1]))
            return "break"

    def _toggle_checked_index(self, index):
        if index in self.checked_control_indices:
            self.checked_control_indices.remove(index)
        else:
            self.checked_control_indices.add(index)
        current_selection = self.control_tree.selection()
        self._refresh_tree()
        if current_selection:
            self.control_tree.selection_set(current_selection)
        self._refresh_summary()

    def _toggle_group_checked(self, group):
        member_indexes = list(group.get("memberIndexes", []))
        if not member_indexes:
            return
        if all(index in self.checked_control_indices for index in member_indexes):
            for index in member_indexes:
                self.checked_control_indices.discard(index)
        else:
            for index in member_indexes:
                self.checked_control_indices.add(index)
        current_selection = self.control_tree.selection()
        self._refresh_tree()
        if current_selection:
            self.control_tree.selection_set(current_selection)
        self._refresh_summary()

    def _on_tree_select(self, _event=None):
        selection = self.control_tree.selection()
        if not selection or not isinstance(self.current_payload, dict):
            return
        selected_iid = selection[0]
        if str(selected_iid).startswith("group:"):
            group = self._find_group_by_tree_iid(selected_iid)
            if not group:
                return
            flat_controls = self.current_payload.get("flatControls", [])
            best_index = group.get("bestIndex")
            if best_index is None or best_index >= len(flat_controls):
                return
            index = best_index
        elif str(selected_iid).startswith("item:"):
            index = int(str(selected_iid).split(":", 1)[1])
        else:
            return
        flat_controls = self.current_payload.get("flatControls", [])
        if index >= len(flat_controls):
            return
        item = flat_controls[index]
        control_definition = {}
        if index < len(self.current_payload.get("controlDefinitions", [])):
            control_definition = self.current_payload.get("controlDefinitions", [])[index]
        self.var_saved_control_name.set(
            str(control_definition.get("name", "")).strip()
            or str(item.get("savedControlName", "")).strip()
            or str(item.get("suggestedControlName", "")).strip()
            or str(item.get("displayName", "")).strip()
        )
        self.var_saved_control_id.set(
            str(control_definition.get("id", "")).strip()
            or str(item.get("savedControlId", "")).strip()
            or _build_saved_control_id_from_name(
                str(control_definition.get("name", "")).strip()
                or str(item.get("savedControlName", "")).strip()
                or str(item.get("suggestedControlName", "")).strip()
                or str(item.get("displayName", "")).strip(),
                fallback=f"control_{index + 1}",
            )
        )
        preview = {
            "qualityTier": item.get("qualityTier", ""),
            "qualityReason": item.get("qualityReason", ""),
            "suggestedControlName": item.get("suggestedControlName", ""),
            "flatControl": item,
            "controlDefinition": control_definition,
        }
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", json.dumps(preview, ensure_ascii=False, indent=2))

    def _get_selected_tree_index(self):
        selection = self.control_tree.selection()
        if not selection:
            return None
        selected_iid = str(selection[0])
        if selected_iid.startswith("group:"):
            group = self._find_group_by_tree_iid(selected_iid)
            return None if not group else group.get("bestIndex")
        if selected_iid.startswith("item:"):
            try:
                return int(selected_iid.split(":", 1)[1])
            except Exception:
                return None
        return None

    def cmd_scan_and_save(self):
        self._run_scan(auto_save=True)

    def cmd_scan_preview(self):
        self._run_scan(auto_save=False)

    def _start_region_pick(self, auto_save):
        delay_seconds = max(1, int(self.var_pick_delay.get() or DEFAULT_PICK_DELAY_SECONDS))
        self.var_status.set(f"请在 {delay_seconds} 秒内切到目标软件窗口，随后开始画框选区。")
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.after(delay_seconds * 1000, lambda: self._show_region_overlay(auto_save))

    def _show_region_overlay(self, auto_save):
        self.root.update()
        overlay = RegionPickerOverlay(self.root, on_complete=lambda rect: self._finish_region_pick(rect, auto_save))
        overlay.window.focus_force()

    def _finish_region_pick(self, rect, auto_save):
        if not rect:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.var_status.set("已取消区域采集。")
            return
        time.sleep(0.2)
        self._run_scan(auto_save=auto_save, region_rect=rect)
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def cmd_region_scan_and_save(self):
        self._start_region_pick(auto_save=True)

    def cmd_region_scan_preview(self):
        self._start_region_pick(auto_save=False)

    def cmd_check_all_results(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        self.checked_control_indices = set(range(len(self.current_payload.get("flatControls", []) or [])))
        self._refresh_tree()
        self._refresh_summary()
        self.var_status.set("已全选当前扫描结果。")

    def cmd_smart_check_results(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        self.checked_control_indices = self._build_default_checked_indices()
        self._refresh_tree()
        self._refresh_summary()
        self.var_status.set(
            "已按质量分级智能勾选：默认保留推荐控件和容器控件；如无推荐项，则回退到每组最优候选。"
        )

    def cmd_clear_checked_results(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        self.checked_control_indices = set()
        self._refresh_tree()
        self._refresh_summary()
        self.var_status.set("已清空当前勾选结果。")

    def cmd_apply_current_control_alias(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        index = self._get_selected_tree_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择一个扫描结果。")
            return
        flat_controls = self.current_payload.get("flatControls", []) or []
        control_definitions = self.current_payload.get("controlDefinitions", []) or []
        if index >= len(flat_controls) or index >= len(control_definitions):
            return
        saved_name = (
            self.var_saved_control_name.get().strip()
            or flat_controls[index].get("suggestedControlName", "")
            or flat_controls[index].get("displayName", "")
            or f"控件 {index + 1}"
        )
        saved_id = self.var_saved_control_id.get().strip() or _build_saved_control_id_from_name(saved_name, fallback=f"control_{index + 1}")
        group = self._find_group_by_index(index)
        target_indexes = group.get("memberIndexes", []) if group else [index]
        for target_index in target_indexes:
            if target_index >= len(flat_controls) or target_index >= len(control_definitions):
                continue
            flat_controls[target_index]["savedControlName"] = saved_name
            flat_controls[target_index]["savedControlId"] = saved_id
            control_definitions[target_index]["name"] = saved_name
            control_definitions[target_index]["id"] = saved_id
            control_definitions[target_index]["notes"] = (
                str(control_definitions[target_index].get("notes", "")).strip() + f" | 保存名={saved_name}"
            ).strip(" |")
        self._rebuild_control_groups()
        self._refresh_tree()
        self.control_tree.selection_set(f"item:{index}")
        self._on_tree_select()
        self.var_status.set(f"已更新保存命名：{saved_name}")

    def _build_filtered_payload_for_save(self):
        payload = json.loads(json.dumps(self.current_payload, ensure_ascii=False))
        flat_controls = payload.get("flatControls", []) or []
        control_definitions = payload.get("controlDefinitions", []) or []
        selected_indexes = sorted(index for index in self.checked_control_indices if 0 <= index < len(flat_controls))
        if not selected_indexes:
            raise RuntimeError("当前没有勾选任何扫描结果，无法保存控件库。")
        deduplicated_indexes = []
        seen_group_keys = set()
        for index in selected_indexes:
            group_key = _build_control_group_key(flat_controls[index])
            if group_key in seen_group_keys:
                continue
            grouped_selected = [item_index for item_index in selected_indexes if _build_control_group_key(flat_controls[item_index]) == group_key]
            keep_index = max(grouped_selected, key=lambda item_index: _rank_group_candidate(flat_controls[item_index], preferred_index=item_index))
            if keep_index not in deduplicated_indexes:
                deduplicated_indexes.append(keep_index)
            seen_group_keys.add(group_key)
        filtered_flat_controls = [flat_controls[index] for index in deduplicated_indexes]
        filtered_control_definitions = [control_definitions[index] for index in deduplicated_indexes if index < len(control_definitions)]
        existing_ids = set()
        normalized_definitions = []
        for index, item in enumerate(filtered_control_definitions):
            current_name = (
                str(item.get("name", "")).strip()
                or str(filtered_flat_controls[index].get("savedControlName", "")).strip()
                or str(filtered_flat_controls[index].get("suggestedControlName", "")).strip()
                or str(filtered_flat_controls[index].get("displayName", "")).strip()
                or f"控件 {index + 1}"
            )
            item["name"] = current_name
            item["id"] = _ensure_unique_control_id(
                {"id": str(item.get("id", "")).strip() or _build_saved_control_id_from_name(current_name, fallback=f"control_{index + 1}")},
                existing_ids,
            )["id"]
            normalized_definitions.append(item)
        payload["flatControls"] = filtered_flat_controls
        payload["controlDefinitions"] = normalized_definitions
        payload.setdefault("scanMeta", {})
        payload["scanMeta"]["selectedControls"] = len(normalized_definitions)
        payload["scanMeta"]["selectedRawControls"] = len(selected_indexes)
        payload["scanMeta"]["totalControls"] = len(filtered_flat_controls)
        return payload

    def cmd_save_current_payload(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        initial_dir = CONTROL_MAP_DIR if os.path.exists(CONTROL_MAP_DIR) else BASE_DIR
        target_window = self.current_payload.get("targetWindow", {}) or {}
        default_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slugify_filename(target_window.get('title', 'window'))}_control_map.json"
        output_path = filedialog.asksaveasfilename(
            title="保存控件库",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
        )
        if not output_path:
            return
        try:
            filtered_payload = self._build_filtered_payload_for_save()
            self.current_output_path = save_control_map_payload(filtered_payload, output_path)
        except Exception as exc:
            messagebox.showerror("保存失败", f"保存控件库失败：\n{exc}")
            return
        self._refresh_summary()
        self.var_status.set(f"已保存已勾选控件：{self.current_output_path}")

    def cmd_open_control_map_dir(self):
        ensure_directory(CONTROL_MAP_DIR)
        os.startfile(CONTROL_MAP_DIR)

    def cmd_copy_selected_locator(self):
        selection = self.control_tree.selection()
        if not selection or not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先选择一个控件。")
            return
        index = self._get_selected_tree_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择一个具体控件。")
            return
        flat_controls = self.current_payload.get("flatControls", [])
        if index >= len(flat_controls):
            return
        item = flat_controls[index]
        locator_text = json.dumps(
            {
                "targetMethod": item.get("recommendedTargetMethod", ""),
                "targetValue": item.get("recommendedTargetValue", ""),
                "windowTitle": item.get("windowTitle", ""),
                "uiPath": item.get("uiPath", ""),
            },
            ensure_ascii=False,
            indent=2,
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(locator_text)
        self.var_status.set(f"已复制推荐定位：{item.get('displayName', '')}")


def main():
    parser = argparse.ArgumentParser(description="扫描目标窗口控件树并生成控件信息库")
    parser.add_argument("window_title", nargs="?", default="", help="目标窗口标题关键字")
    parser.add_argument("--foreground", action="store_true", help="扫描当前前台窗口")
    parser.add_argument("--backend", default=DEFAULT_BACKEND, help="pywinauto backend，默认 uia")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help="递归扫描最大深度")
    parser.add_argument("--output", default="", help="输出 JSON 路径")
    args = parser.parse_args()

    if args.foreground or args.window_title or args.output:
        payload = build_control_map_payload(
            window_keyword=args.window_title,
            backend=args.backend,
            use_foreground=args.foreground,
            max_depth=args.max_depth,
        )
        output_path = save_control_map_payload(payload, args.output)
        print(f"控件信息库已保存：{output_path}")
        return

    root = tk.Tk()
    ControlMapBuilderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
