# encoding: utf-8

import argparse
import collections
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import tkinter as tk
import time

# 强制 COM 初始化为 MTA 模式 (Multi-Threaded Apartment)
# 这是 pywinauto 社区推荐的 UIA 后端核心性能优化，能大幅提升跨进程 COM 调用的速度。
# 必须在导入 pywinauto 或 comtypes 之前设置。
sys.coinit_flags = 0

from ctypes import wintypes
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

import wt_dpi
import wt_flow_editor_utils

try:
    from pywinauto import Desktop
    import pywinauto
except Exception:
    Desktop = None
    pywinauto = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTROL_MAP_DIR = os.path.join(BASE_DIR, "control_maps")
DEFAULT_BACKEND = "smart"
DEFAULT_MAX_DEPTH = 10
# 整树采集（无画框区域）时的最小递归深度。WPF 控件树嵌套很深，
# 默认 10 层往往覆盖不到滚动区/折叠面板/未激活 Tab 中的深层控件。
FULLTREE_MIN_DEPTH = 18
DEFAULT_PICK_DELAY_SECONDS = 3
BACKEND_OPTIONS = ["smart", "uia", "win32"]

# 独立 .NET 工具：走 UIA RawViewWalker 全量遍历控件树（对齐 Accessibility Insights 的采全能力）。
# 由 Python 通过 subprocess 调用，输出 JSON 到 stdout，用于在整树采集时补采纯 pywinauto 遍历漏掉的深层控件。
UIA_TREE_DUMPER_EXE = os.path.join(
    BASE_DIR,
    "uia_tree_dumper",
    "uia_tree_dumper",
    "bin",
    "Release",
    "net10.0-windows",
    "uia_tree_dumper.exe",
)

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
    return wt_flow_editor_utils.slugify_filename(text, fallback)


def normalize_control_type_name(control_type, localized_control_type=""):
    control_type = str(control_type or "").strip()
    if control_type.startswith("UIA_") and "ControlTypeId" in control_type:
        control_type = control_type.replace("UIA_", "").replace("ControlTypeId", "").strip()
    matched = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\d+\)\s*$", control_type)
    if matched:
        control_type = matched.group(1)
    return control_type or str(localized_control_type or "").strip()


def build_locator_recommendation(parsed, index=-1, ui_path=None):
    automation_id = str(parsed.get("automationId", "")).strip()
    name = str(parsed.get("name", "")).strip()
    class_name = str(parsed.get("className", "")).strip()
    handle = str(parsed.get("nativeWindowHandle", "")).strip()
    control_type = normalize_control_type_name(parsed.get("controlType", ""), parsed.get("localizedControlType", ""))

    # 尝试将 Value/ToggleState 作为补充定位特征（如果存在的话）
    value = str(parsed.get("value", "")).strip()

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
            
    # 结构化兜底：优先用层级 uiPath（运行时已支持，比 found_index 更稳），
    # 其次才退回 found_index；都没有则放弃（标记为 no_stable_locator）。
    if ui_path:
        return "ui_path", ui_path, 12, "结构化兜底: ui_path"
    if control_type and index >= 0:
        return "control_type,found_index", f"{control_type},{index}", 10, "结构化兜底: control_type + found_index"
    if class_name and index >= 0:
        return "class_name,found_index", f"{class_name},{index}", 8, "结构化兜底: class_name + found_index"

    return "", "", 0, "no_stable_locator"


def _is_garbage_name(name):
    """判断 name 是否是 SVG path / 几何数据 / 乱码，不可作为可读名称"""
    if not name:
        return False
    name = str(name).strip()
    # 纯特殊字符
    if re.match(r"^[^a-zA-Z0-9\u4e00-\u9fa5]+$", name):
        return True
    # SVG path 数据特征：以 M/L/C/A/H/V/Z 开头并包含大量数字和逗号
    if re.match(r"^[MLCAHVZmlcahvz][\d.,\s\-]+", name) and name.count(",") >= 3:
        return True
    # 纯坐标序列
    if re.match(r"^[\d.,\s\-]+$", name) and len(name) > 10:
        return True
    # Base64 编码特征
    if len(name) > 40 and re.match(r"^[A-Za-z0-9+/=]+$", name):
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


def assess_control_automatability(flat_control):
    """对单个扁平控件做"可自动化体检"，返回 (风险等级, 原因列表)。

    风险等级: 高 / 中 / 低。
    复用的 UIA 属性（IsEnabled / IsKeyboardFocusable / IsOffscreen / FrameworkId）
    与 Accessibility Insights FastPass 关注的同源，是 pywinauto 运行时能否驱动
    控件的关键信号。

    与 _classify_control_quality 互补：质量分级看"能否稳定定位"，本函数看
    "运行时能否被驱动"（禁用 / 不可聚焦 / 离屏 / 非标准框架）。
    """
    if not isinstance(flat_control, dict):
        return "未知", []
    inspect = flat_control.get("inspectData")
    if not isinstance(inspect, dict):
        inspect = {}

    def _flag(key):
        value = str(inspect.get(key, "")).strip().lower()
        if not value:
            value = str(flat_control.get(key, "")).strip().lower()
        return value

    is_enabled = _flag("isEnabled")
    is_keyboard_focusable = _flag("isKeyboardFocusable")
    is_offscreen = _flag("isOffscreen")
    framework_id = _flag("frameworkId")
    control_type = str(flat_control.get("controlType", "")).strip().lower()
    has_automation_id = bool(str(flat_control.get("automationId", "")).strip())
    has_name = bool(str(flat_control.get("name", "")).strip())

    high = []
    medium = []

    # 高：运行时完全无法被驱动
    if is_enabled == "false":
        high.append("控件已禁用(IsEnabled=false)，不会响应操作")
    if is_keyboard_focusable == "false":
        high.append("控件不可键盘聚焦(IsKeyboardFocusable=false)，pywinauto 难以驱动")
    if (not has_automation_id) and (not has_name) and control_type in {
        "custom", "pane", "group", "image", "document", "text",
    }:
        high.append("缺少 automationId 与 name 且为非交互类型，难以稳定定位")

    # 中：存在不稳定因素
    if is_offscreen == "true":
        medium.append("控件当前离屏(IsOffscreen=true)，运行时可能定位失败")
    if framework_id in {"directuihwnd", "win32"}:
        medium.append(f"框架为 {framework_id}，非标准 UI 自动化，定位可能不稳定")
    if control_type in {"custom", "pane", "group"} and not has_automation_id:
        medium.append("容器/自定义类型且无 automationId，定位依赖层级或文本")

    reasons = high + medium
    level = "高" if high else ("中" if medium else "低")
    return level, reasons


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
    # 与同行标签关联的下拉框常无有意义 name（如 PART_DropDownButton），用标签命名。
    related_label = str(flat_control.get("relatedLabelName", "")).strip()
    if related_label and _control_is_dropdown(flat_control):
        return f"{related_label} 下拉框"
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
    # 下拉框显示值文本：已并入对应下拉框，保留但不单独入库。
    if flat_control.get("foldedIntoDropdown"):
        return "建议忽略", "下拉框当前显示值文本，已并入对应下拉框"
    # 下拉框可选项：属于某下拉框的候选值，未展开时通常无法完整采集。
    if flat_control.get("dropdownOption"):
        return "建议忽略", "下拉框可选项，属于对应下拉框的候选值"
    # 已与同行标签关联的下拉框/输入控件：是该标签对应的实际操作控件。
    if flat_control.get("regionRelated") and _control_is_assoc_actionable(flat_control):
        option_count = int(flat_control.get("optionCount", 0) or 0)
        if option_count:
            return "推荐保留", f"同行标签对应的实际操作控件（下拉框/输入框），已展开采集 {option_count} 个可选项"
        return "推荐保留", "同行标签对应的实际操作控件（下拉框/输入框）"
    # 已自动展开采集到可选项的下拉框：即使未关联标签也具备实际编排价值。
    if flat_control.get("optionValues") and _control_is_dropdown(flat_control):
        return "推荐保留", f"下拉框，已展开采集 {len(flat_control.get('optionValues'))} 个可选项"
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
        risk_level, risk_reasons = assess_control_automatability(item)
        item["automatabilityRisk"] = risk_level
        item["automatabilityReasons"] = risk_reasons
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


# ── Pattern 检测：对齐 Inspect "Action" 菜单 ──────────────────────
# 通过 UIA 的 IsXxxPatternAvailable 布尔属性检测元素支持的 Control Patterns。
# 相比实例化 pywinauto 的 iface_* 接口，读取可用性属性的优势：
#   1. 只需一次 GetCurrentPropertyValue 跨进程调用，不实例化 Pattern 对象（零副作用，
#      绝不会误触发 Invoke/Toggle/Expand 等真实界面动作）；
#   2. 复用 pywinauto 的 pattern_ids，自动适配当前 OS 支持的全部 Pattern（含 32 种）；
#   3. 能检出 pywinauto 无 iface_ 封装的 Pattern（如 LegacyIAccessible）。
# 实测约 1.6ms/控件，整树 1100+ 控件累计约 1.8s，开销可接受。
_PATTERN_AVAILABILITY_MAP = None
_PATTERN_AVAILABILITY_READY = False


def _get_pattern_availability_map():
    """惰性构建 {pattern_name: availabilityPropertyId} 映射。

    依赖 COM（IUIA 单例），故延迟到首次调用时初始化，避免影响模块导入；
    构建失败（如非 Windows / 缺 pywinauto）时返回空 dict，检测降级为空列表。
    """
    global _PATTERN_AVAILABILITY_MAP, _PATTERN_AVAILABILITY_READY
    if _PATTERN_AVAILABILITY_READY:
        return _PATTERN_AVAILABILITY_MAP
    _PATTERN_AVAILABILITY_READY = True
    mapping = {}
    try:
        from pywinauto.uia_defines import IUIA, pattern_ids
        uia_dll = IUIA().UIA_dll
        for ptrn_name in pattern_ids.keys():
            # V2 结尾的 Pattern（如 TransformV2）对应属性名 UIA_IsTransformPattern2AvailablePropertyId
            if ptrn_name.endswith("V2"):
                prop_name = "UIA_Is" + ptrn_name[:-2] + "Pattern2AvailablePropertyId"
            else:
                prop_name = "UIA_Is" + ptrn_name + "PatternAvailablePropertyId"
            prop_id = getattr(uia_dll, prop_name, None)
            if prop_id is not None:
                mapping[ptrn_name] = prop_id
    except Exception:
        mapping = {}
    _PATTERN_AVAILABILITY_MAP = mapping
    return mapping


def _detect_supported_patterns(wrapper):
    """检测元素支持的 UIA Control Patterns（对齐 Inspect Action 菜单）。

    逐个读取 Pattern 的 IsXxxPatternAvailable 布尔属性，返回受支持的 Pattern
    名称列表，如 ["Invoke", "Value", "Scroll"]。仅对 uia backend 有效；
    win32 元素的 element_info 无 .element，捕获异常后返回空列表。
    """
    if wrapper is None:
        return []
    mapping = _get_pattern_availability_map()
    if not mapping:
        return []
    try:
        element = wrapper.element_info.element
    except Exception:
        return []
    if element is None:
        return []
    supported = []
    for pattern_name, prop_id in mapping.items():
        try:
            if element.GetCurrentPropertyValue(prop_id):
                supported.append(pattern_name)
        except Exception:
            pass
    return supported


def _detect_expand_collapse_state(wrapper, supported_patterns=None):
    """读取 ExpandCollapse 展开状态（ComboBox / Tree / 折叠面板）。

    若已知 supported_patterns 且其中不含 ExpandCollapse，则直接跳过，
    避免对不支持该 Pattern 的元素触发一次必然失败的接口实例化。
    """
    if wrapper is None:
        return ""
    if supported_patterns is not None and "ExpandCollapse" not in supported_patterns:
        return ""
    try:
        state = wrapper.get_expand_state()
        # 0=Collapsed, 1=Expanded, 2=PartiallyExpanded, 3=LeafNode
        state_names = {0: "Collapsed", 1: "Expanded", 2: "PartiallyExpanded", 3: "LeafNode"}
        return state_names.get(state, str(state))
    except Exception:
        return ""


def _extract_wrapper_info(wrapper, depth, index, path_segments, target_window):
    element_info = _safe_get_value(lambda: wrapper.element_info, None)
    if element_info is not None:
        _safe_get_value(lambda: element_info.set_cache_strategy("basic"), None)
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
    
    # 尝试获取 ValuePattern (对于没有 name 但有内容的文本框极其重要)
    value_pattern_value = ""
    if hasattr(wrapper, "get_value"):
        value_pattern_value = str(_safe_get_value(lambda: wrapper.get_value(), "")).strip()
    
    # 尝试获取 TogglePattern (对于复选框/单选框的状态采集)
    toggle_state = ""
    if hasattr(wrapper, "get_toggle_state"):
        toggle_state = str(_safe_get_value(lambda: wrapper.get_toggle_state(), "")).strip()

    # 检测支持的 Control Patterns（对齐 Inspect Action 菜单）
    supported_patterns = _detect_supported_patterns(wrapper)
    # 检测 ExpandCollapse 状态（ComboBox / Tree / 下拉框），复用已测 Pattern 跳过不支持项
    expand_collapse_state = _detect_expand_collapse_state(wrapper, supported_patterns)

    legacy_name = str(_safe_get_value(lambda: getattr(element_info, "legacy_name", ""), "")).strip()
    legacy_role = str(_safe_get_value(lambda: getattr(element_info, "legacy_role", ""), "")).strip()
    legacy_state = str(_safe_get_value(lambda: getattr(element_info, "legacy_state", ""), "")).strip()

    inspect_data = {
        "name": name,
        "value": value_pattern_value,
        "toggleState": toggle_state,
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
    full_ui_path = " > ".join(item for item in path_segments if item)
    locator_method, locator_value, locator_score, locator_reason = build_locator_recommendation(
        inspect_data, index=index, ui_path=full_ui_path)
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
        "value": value_pattern_value,
        "toggleState": toggle_state,
        "supportedPatterns": supported_patterns,
        "expandCollapseState": expand_collapse_state,
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
    info["_wrapperIdentity"] = identity
    info["_wrapperRef"] = wrapper
    flat_controls.append(info)
    seen_identities.add(_build_flat_control_identity(info))
    seen_identities.add(identity)
    return True


def _walk_wrapper(
    wrapper,
    depth,
    max_depth,
    target_window,
    flat_controls,
    siblings_index=1,
    path_segments=None,
    parent_index=-1,
    start_time=None,
    scan_timeout_seconds=30,
    status_callback=None,
):
    if start_time is None:
        start_time = time.time()

    if time.time() - start_time > scan_timeout_seconds:
        if status_callback:
            status_callback("扫描超时，已停止遍历。", len(flat_controls))
        return None

    path_segments = list(path_segments or [])
    info = _extract_wrapper_info(wrapper, depth, len(flat_controls) + 1, path_segments, target_window)
    info["treeLevel"] = depth
    info["parentIndex"] = parent_index
    info["siblingsIndex"] = siblings_index
    current_index = len(flat_controls)
    info["_wrapperIdentity"] = _build_wrapper_identity(wrapper)
    node = dict(info)
    node["children"] = []
    # _wrapperRef 仅挂在扁平控件上（node 已拷贝完成），供后续自动展开下拉框采选项使用；
    # 序列化前会被剥离，不会进入 controlsTree/输出。
    info["_wrapperRef"] = wrapper
    flat_controls.append(info)

    if status_callback and len(flat_controls) % 20 == 0:
        status_callback(f"已收集 {len(flat_controls)} 个控件...", len(flat_controls))

    if depth >= max_depth:
        return node

    children = _safe_get_value(lambda: wrapper.children(), [])
    for child_index, child in enumerate(children, start=1):
        if time.time() - start_time > scan_timeout_seconds:
            if status_callback:
                status_callback("扫描超时，已停止遍历。", len(flat_controls))
            break

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
        child_node = _walk_wrapper(
            child,
            depth + 1,
            max_depth,
            target_window,
            flat_controls,
            siblings_index=child_index,
            path_segments=child_path,
            parent_index=current_index,
            start_time=start_time,
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
        )
        if child_node is not None:
            node["children"].append(child_node)
    node["inspectData"]["children"] = [
        f"{child.get('displayName', '')} | {child.get('className', '')} | {child.get('controlType', '')}".strip(" |")
        for child in node["children"][:12]
    ]
    return node


# ---------------------------------------------------------------------------
# RawViewWalker BFS — Inspect 风格渐进式完整树遍历
# ---------------------------------------------------------------------------

_MAX_ELEMENTS_PER_WALK = 8000


def _rebuild_bfs_paths(flat_controls):
    """用 parentIndex 链为 BFS 采集结果重建 uiPath / parentPath / ancestors。"""
    for item in (flat_controls or []):
        display_name = item.get("displayName", "")
        path_parts = [display_name]
        parent_idx = item.get("parentIndex", -1)
        visited = set()
        while parent_idx >= 0 and parent_idx < len(flat_controls) and parent_idx not in visited:
            visited.add(parent_idx)
            parent = flat_controls[parent_idx]
            parent_name = parent.get("displayName", "")
            if parent_name:
                path_parts.insert(0, parent_name)
            parent_idx = parent.get("parentIndex", -1)
        ui_path = " > ".join(p for p in path_parts if p)
        parent_path = " > ".join(path_parts[:-1]) if len(path_parts) > 1 else ""
        item["uiPath"] = ui_path
        item["parentPath"] = parent_path
        inspect = item.get("inspectData")
        if isinstance(inspect, dict):
            inspect["ancestors"] = path_parts[:-1]


def _enrich_tree_metadata(flat_controls):
    """为 BFS 采集结果添加树结构辅助元数据（不删除任何控件）。

    添加字段：
     - pathHash: 基于 uiPath 的短哈希，用于快速定位和去重
     - isTransparentContainer: 标记“透明容器”（无名、单子节点），GUI 可折叠显示
     - childCount: 直接子节点数量
    """
    import hashlib
    if not flat_controls:
        return
    # 先统计每个节点的子节点数量
    child_counts = [0] * len(flat_controls)
    for idx, item in enumerate(flat_controls):
        parent_idx = item.get("parentIndex", -1)
        if 0 <= parent_idx < len(flat_controls):
            child_counts[parent_idx] += 1
    # 为每个节点计算 pathHash 和透明容器标记
    for idx, item in enumerate(flat_controls):
        ui_path = item.get("uiPath", "")
        # pathHash: 取 uiPath 的 MD5 前 8 位
        path_hash = hashlib.md5(ui_path.encode("utf-8", errors="replace")).hexdigest()[:8]
        item["pathHash"] = path_hash
        item["childCount"] = child_counts[idx]
        # 透明容器判断：无名 + 单子节点 + 容器类型
        name = str(item.get("name", "")).strip()
        control_type = str(item.get("controlType", "")).strip().lower()
        is_container = control_type in ("pane", "group", "custom", "window")
        is_single_child = child_counts[idx] == 1
        is_transparent = is_container and not name and is_single_child
        item["isTransparentContainer"] = is_transparent


def _build_control_summary(filtered_controls, all_controls):
    """生成控件摘要（快速概览），不修改原始数据。

    返回包含以下信息的 dict：
     - 定位能力统计：有 automationId / 有 name / 两者都有
     - 深度分布
     - 质量分级分布
     - 透明容器数量
    """
    from collections import Counter
    if not filtered_controls:
        return {"totalFiltered": 0, "totalRaw": len(all_controls or [])}

    has_aid = sum(1 for c in filtered_controls if str(c.get("automationId", "")).strip())
    has_name = sum(1 for c in filtered_controls if str(c.get("name", "")).strip())
    has_both = sum(1 for c in filtered_controls if str(c.get("automationId", "")).strip() and str(c.get("name", "")).strip())

    depth_dist = Counter(c.get("treeLevel", 0) for c in filtered_controls)
    quality_dist = Counter(str(c.get("qualityTier", "")).strip() or "未分类" for c in filtered_controls)
    transparent_count = sum(1 for c in filtered_controls if c.get("isTransparentContainer"))

    return {
        "totalFiltered": len(filtered_controls),
        "totalRaw": len(all_controls or []),
        "locatability": {
            "hasAutomationId": has_aid,
            "hasName": has_name,
            "hasBoth": has_both,
            "hasNeither": len(filtered_controls) - has_aid - has_name + has_both,
        },
        "depthDistribution": dict(sorted(depth_dist.items())),
        "qualityDistribution": dict(sorted(quality_dist.items(), key=lambda x: -x[1])),
        "transparentContainers": transparent_count,
    }


def _build_tree_from_flat(flat_controls):
    """从 flat_controls（含 parentIndex）重建嵌套控件树。

    每个节点额外设置：
     - flatIndex: 原始 flat_controls 中的下标，用于 GUI 反查控件详情
    """
    if not flat_controls:
        return {}
    nodes = []
    for idx, item in enumerate(flat_controls):
        node = dict(item)
        # 剥离临时 live 包装器引用，避免序列化失败（与主采集流程保持一致）
        node.pop("_wrapperRef", None)
        node.pop("_wrapperIdentity", None)
        node.setdefault("children", [])
        node["flatIndex"] = idx
        nodes.append(node)
    roots = []
    for idx, node in enumerate(nodes):
        parent_idx = node.get("parentIndex", -1)
        if parent_idx >= 0 and parent_idx < len(nodes) and parent_idx != idx:
            nodes[parent_idx].setdefault("children", []).append(node)
        else:
            roots.append(node)
    for node in nodes:
        inspect = node.get("inspectData")
        if isinstance(inspect, dict):
            inspect["children"] = [
                f"{c.get('displayName', '')} | {c.get('className', '')} | {c.get('controlType', '')}".strip(" |")
                for c in node.get("children", [])[:12]
            ]
    return roots[0] if roots else {}


def _walk_raw_view_bfs(
    target_window_wrapper,
    max_depth,
    target_window,
    flat_controls,
    path_segments=None,
    start_time=None,
    scan_timeout_seconds=30,
    status_callback=None,
):
    """使用 RawViewWalker 进行 BFS 树遍历，不丢失 IsContentElement=False 的控件。

    核心改进（对比 _walk_wrapper）：
     1. IUIAutomation::RawViewWalker → 不过滤 IsContentElement / IsControlElement
     2. 迭代 BFS → 可控内存、可暂停
     3. 输出 flat_controls 格式与 _walk_wrapper 完全兼容
    """
    from pywinauto.uia_defines import IUIA
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    if start_time is None:
        start_time = time.time()

    path_segments = list(path_segments or [])
    root_display = path_segments[0] if path_segments else _build_display_name(target_window, "窗口", 1)

    iuia = IUIA().iuia
    raw_walker = iuia.RawViewWalker

    # ---- 根节点 ----
    root_info = _extract_wrapper_info(target_window_wrapper, 0, 1, path_segments, target_window)
    root_info["treeLevel"] = 0
    root_info["parentIndex"] = -1
    root_info["siblingsIndex"] = 1
    root_info["_wrapperIdentity"] = _build_wrapper_identity(target_window_wrapper)
    root_info["_wrapperRef"] = target_window_wrapper
    flat_controls.append(root_info)

    if status_callback:
        status_callback(f"已发现 {len(flat_controls)} 个控件 (depth 0)...", len(flat_controls))

    if max_depth <= 0:
        _rebuild_bfs_paths(flat_controls)
        return None

    # ---- BFS 队列 ----
    # 每个队列项: (element, depth, parent_index, siblings_index)
    queue = collections.deque()

    root_element = target_window_wrapper.element_info.element
    child_sib = 0
    try:
        child = raw_walker.GetFirstChildElement(root_element)
        while child:
            child_sib += 1
            queue.append((child, 1, 0, child_sib))
            child = raw_walker.GetNextSiblingElement(child)
    except Exception:
        pass

    if status_callback:
        status_callback(f"BFS 根节点子元素入队: {len(queue)} 个", len(flat_controls))

    last_report = 0

    while queue:
        if time.time() - start_time > scan_timeout_seconds:
            if status_callback:
                status_callback("扫描超时，已停止遍历。", len(flat_controls))
            break

        if len(flat_controls) >= _MAX_ELEMENTS_PER_WALK:
            if status_callback:
                status_callback(f"已达上限 {_MAX_ELEMENTS_PER_WALK} 个控件，暂停。", len(flat_controls))
            break

        element, depth, parent_idx, sib_idx = queue.popleft()

        if depth > max_depth:
            continue

        # 将原始 UI Automation 元素包装为 pywinauto wrapper
        try:
            elem_info = UIAElementInfo(element)
            wrapper = UIAWrapper(elem_info)
        except Exception:
            continue

        # 提取控件信息（与 _walk_wrapper 相同格式）
        identity = _build_wrapper_identity(wrapper)
        # path_segments 在 BFS 中先填占位值，后续由 _rebuild_bfs_paths 修正
        info = _extract_wrapper_info(
            wrapper, depth, len(flat_controls) + 1, [root_display], target_window
        )
        info["treeLevel"] = depth
        info["parentIndex"] = parent_idx
        info["siblingsIndex"] = sib_idx
        info["_wrapperIdentity"] = identity
        info["_wrapperRef"] = wrapper
        current_index = len(flat_controls)
        flat_controls.append(info)

        if status_callback and current_index - last_report >= 50:
            last_report = current_index
            status_callback(
                f"已发现 {len(flat_controls)} 个控件 (depth {depth}, 队列 {len(queue)})...",
                len(flat_controls),
            )

        if depth >= max_depth:
            continue

        # 通过 RawViewWalker 枚举子元素
        child_sib = 0
        try:
            child = raw_walker.GetFirstChildElement(element)
            while child:
                if time.time() - start_time > scan_timeout_seconds:
                    break
                child_sib += 1
                queue.append((child, depth + 1, current_index, child_sib))
                child = raw_walker.GetNextSiblingElement(child)
        except Exception:
            pass

    # ---- 后处理 ----
    _rebuild_bfs_paths(flat_controls)

    if status_callback:
        status_callback(
            f"BFS 遍历完成: {len(flat_controls)} 个控件, 最大深度 {max_depth}",
            len(flat_controls),
        )

    return None


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


_REGION_RELATED_CONTROL_TYPES = {
    "edit", "combobox", "spinner", "document", "list", "listitem", "button"
}
_REGION_LABEL_TYPES = {"text", "label", "static", "textblock", "control", "custom", "pane", "group"}


def _wrapper_rect_contains_point(wrapper, x, y):
    rect = _safe_get_value(lambda: wrapper.rectangle(), None)
    if rect is None:
        return False
    try:
        return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom
    except Exception:
        return False


def _wrapper_is_region_label(wrapper):
    control_type = normalize_control_type_name(
        _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
    ).strip().lower()
    return control_type in _REGION_LABEL_TYPES


def _wrapper_is_related_editable(wrapper):
    control_type = normalize_control_type_name(
        _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
    ).strip().lower()
    return control_type in _REGION_RELATED_CONTROL_TYPES


def _wrapper_horizontal_relation_score(label, candidate):
    """计算标签与候选控件的几何关系评分，返回距离评分（越小越近）或 None 表示不相关。

    支持的关系：
    - 同行右侧（最优）：标签右边的输入/下拉控件
    - 同行左侧：标签左边的控件
    - 矩形重叠：标签矩形覆盖控件矩形（常见于 WPF 中 TextBlock 覆盖 ComboBox）
    - 相邻行：标签正上/正下方的控件
    - 同祖级容器内的邻近控件
    """
    label_rect = _safe_get_value(lambda: label.rectangle(), None)
    candidate_rect = _safe_get_value(lambda: candidate.rectangle(), None)
    if label_rect is None or candidate_rect is None:
        return None
    try:
        label_h = max(1, label_rect.bottom - label_rect.top)
        label_w = max(1, label_rect.right - label_rect.left)
        cand_h = max(1, candidate_rect.bottom - candidate_rect.top)
        cand_w = max(1, candidate_rect.right - candidate_rect.left)

        vertical_gap = max(0, max(label_rect.top, candidate_rect.top) - min(label_rect.bottom, candidate_rect.bottom))
        horizontal_gap = max(0, max(label_rect.left, candidate_rect.left) - min(label_rect.right, candidate_rect.right))

        # 矩形重叠检测：标签和控件矩形有交集
        has_overlap = not (label_rect.right < candidate_rect.left or label_rect.left > candidate_rect.right
                           or label_rect.bottom < candidate_rect.top or label_rect.top > candidate_rect.bottom)

        if has_overlap:
            # 计算重叠面积占标签面积的比例
            overlap_w = min(label_rect.right, candidate_rect.right) - max(label_rect.left, candidate_rect.left)
            overlap_h = min(label_rect.bottom, candidate_rect.bottom) - max(label_rect.top, candidate_rect.top)
            overlap_area = max(0, overlap_w) * max(0, overlap_h)
            label_area = label_h * label_w
            overlap_ratio = overlap_area / max(1, label_area)
            if overlap_ratio >= 0.3:
                # 标签矩形大面积覆盖控件，优先级高
                return -100 + int((1.0 - overlap_ratio) * 50)

        # 同行检测：垂直间距不超过控件高度的 2 倍
        max_vertical_tolerance = max(40, min(120, label_h * 2, cand_h * 2))
        if vertical_gap > max_vertical_tolerance:
            # 相邻行检测：水平有交集且垂直距离合理
            if horizontal_gap == 0 and vertical_gap <= max_vertical_tolerance * 3:
                return vertical_gap + 50
            return None

        # 同行：计算方向惩罚和距离
        direction_penalty = 0
        if candidate_rect.left >= label_rect.right - 20:
            # 右侧（标准布局：标签在左，控件在右）
            direction_penalty = 0
        elif candidate_rect.right <= label_rect.left + 20:
            # 左侧（反向布局）
            direction_penalty = 15
        else:
            # 重叠或不明确方向
            direction_penalty = 5

        distance = horizontal_gap + vertical_gap * 2 + direction_penalty
        return distance
    except Exception:
        return None


def _collect_ancestor_related_wrappers(wrapper, max_ancestor_depth=6):
    """从区域命中的标签/容器向祖级横向查找可操作控件。

    增强逻辑：
    - 向上遍历祖级容器，检查各层兄弟控件
    - 对找到的可操作控件（Edit/ComboBox 等），进一步下钻其子级（如 ComboBox 的下拉选项）
    - 支持矩形重叠、同行、相邻行等多种几何关系
    """
    related = []
    current = wrapper
    seen = set()
    for _ in range(max_ancestor_depth):
        if current is None:
            break
        current_identity = _build_wrapper_identity(current)
        if current_identity in seen:
            break
        seen.add(current_identity)
        siblings = _safe_get_value(lambda: current.parent().children(), [])
        candidates = []
        for sibling in siblings:
            if _build_wrapper_identity(sibling) == current_identity:
                continue
            if not _wrapper_is_related_editable(sibling):
                continue
            distance = _wrapper_horizontal_relation_score(wrapper, sibling)
            if distance is not None:
                candidates.append((distance, sibling))
        candidates.sort(key=lambda item: item[0])
        for _dist, sibling in candidates[:4]:
            sibling_identity = _build_wrapper_identity(sibling)
            if sibling_identity not in [ _build_wrapper_identity(r) for r in related ]:
                related.append(sibling)
            # 对 ComboBox 下钻收集子级（可能包含下拉选项）
            sibling_type = normalize_control_type_name(
                _safe_get_value(lambda: getattr(sibling.element_info, "control_type", ""), ""),
                _safe_get_value(lambda: getattr(sibling.element_info, "localized_control_type", ""), ""),
            ).strip().lower()
            if sibling_type == "combobox":
                children = _safe_get_value(lambda: sibling.children(), [])
                for child in children:
                    child_identity = _build_wrapper_identity(child)
                    if child_identity and child_identity not in [ _build_wrapper_identity(r) for r in related ]:
                        related.append(child)
        parent = _safe_get_value(lambda: current.parent(), None)
        if not parent or parent is current:
            break
        current = parent
    return related


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
    related_sources = set()
    for x, y in _generate_probe_points(region_rect):
        wrapper = _safe_get_value(lambda: desktop.from_point(x, y), None)
        if wrapper is None:
            continue
        current = wrapper
        for _ in range(max_depth + 4):
            if current is None:
                break
            current_identity = _build_wrapper_identity(current)
            if _append_wrapper_info(
                flat_controls,
                seen_identities,
                current,
                target_window,
                root_handle=root_handle,
                max_depth=max_depth,
            ):
                related_sources.add(current_identity)
            # 标签/容器不一定与输入控件同层，沿祖级查找同行的 Edit/ComboBox。
            if _wrapper_is_region_label(current):
                for related in _collect_ancestor_related_wrappers(current):
                    related_identity = _build_wrapper_identity(related)
                    if not related_identity:
                        continue
                    added = _append_wrapper_info(
                        flat_controls,
                        seen_identities,
                        related,
                        target_window,
                        root_handle=root_handle,
                        max_depth=max_depth,
                    )
                    for item in flat_controls:
                        if item.get("_wrapperIdentity") == related_identity:
                            item["regionRelated"] = True
                            item["regionRelation"] = "label-ancestor-sibling"
                    if added:
                        related_sources.add(related_identity)
            current_handle = _get_wrapper_handle(current)
            if root_handle and current_handle and str(current_handle) == str(root_handle):
                break
            parent = _safe_get_value(lambda: current.parent(), None)
            if not parent or parent is current:
                break
            current = parent


def _collect_fulltree_probe_wrappers(
    backend,
    target_window,
    target_window_wrapper,
    flat_controls,
    seen_identities,
    root_handle="",
    max_depth=DEFAULT_MAX_DEPTH,
    start_time=None,
    scan_timeout_seconds=30,
    status_callback=None,
):
    """整树采集时借鉴画框采集的网格探针：对整个窗口矩形做 from_point 采样，
    捕获纯树遍历（children 递归）漏掉的控件——例如虚拟化列表、滚动区外、
    折叠面板/未激活 Tab 中 isOffscreen=True 的控件。

    去重依赖 seen_identities，所以重复命中的控件不会被重复加入。
    """
    if Desktop is None:
        return
    window_rect = _rect_to_dict(_safe_get_value(lambda: target_window_wrapper.rectangle(), None))
    if not window_rect:
        return
    if start_time is None:
        start_time = time.time()
    try:
        desktop = Desktop(backend=backend)
    except Exception:
        return
    added_count = 0
    for x, y in _generate_probe_points(window_rect):
        if time.time() - start_time > scan_timeout_seconds:
            if status_callback:
                status_callback("整树探针超时，已停止采样。", len(flat_controls))
            break
        wrapper = _safe_get_value(lambda: desktop.from_point(x, y), None)
        if wrapper is None:
            continue
        current = wrapper
        for _ in range(max_depth + 4):
            if current is None:
                break
            if _append_wrapper_info(
                flat_controls,
                seen_identities,
                current,
                target_window,
                root_handle=root_handle,
                max_depth=max_depth,
            ):
                added_count += 1
                if status_callback and added_count % 20 == 0:
                    status_callback(f"整树探针补采 {added_count} 个控件...", len(flat_controls))
            current_handle = _get_wrapper_handle(current)
            if root_handle and current_handle and str(current_handle) == str(root_handle):
                break
            parent = _safe_get_value(lambda: current.parent(), None)
            if not parent or parent is current:
                break
            current = parent


def _prune_empty_unidentified_containers(flat_controls):
    """整树采集放宽过滤后，只剔除“真正的空壳容器”：
    无 name / automationId / labelText 的 Custom/Pane/Group，且在扁平树中没有任何子级。

    这样既保留所有有标识的控件与承载真实控件的结构容器，又不会让无意义的
    空布局节点淹没结果。相比旧的 _filter_noise_controls（无脑丢弃全部无标识容器），
    这里是保守剪枝，最大化保留信息。
    """
    if not flat_controls:
        return flat_controls
    # 统计每个控件在扁平树里的直接子级数量（依赖 parentIndex，探针补采的控件没有该字段视为叶子）。
    child_count = {}
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        parent_index = item.get("parentIndex", -1)
        try:
            parent_index = int(parent_index)
        except Exception:
            parent_index = -1
        if parent_index >= 0:
            child_count[parent_index] = child_count.get(parent_index, 0) + 1

    kept = []
    for idx, item in enumerate(flat_controls):
        if not isinstance(item, dict):
            continue
        control_type = str(item.get("controlType", "")).strip().lower()
        has_name = bool(str(item.get("name", "")).strip())
        has_automation_id = bool(str(item.get("automationId", "")).strip())
        has_label_text = bool(str(item.get("labelText", "")).strip())
        is_empty_container = (
            control_type in {"custom", "pane", "group"}
            and not has_name
            and not has_automation_id
            and not has_label_text
            and child_count.get(idx, 0) == 0
        )
        if is_empty_container:
            continue
        kept.append(item)
    return kept


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
        "_qualityTier": str(flat_item.get("qualityTier", "")).strip(),
        "templateKey": "",
        "uiPath": str(flat_item.get("uiPath", "")).strip(),
        "notes": (
            f"由控件库扫描生成，定位评分={flat_item.get('locatorScore', 0)}，"
            f"策略={flat_item.get('locatorReason', '')}，"
            f"质量={flat_item.get('qualityTier', '')}，"
            f"说明={flat_item.get('qualityReason', '')}"
            + (
                f" | 可自动化风险={flat_item.get('automatabilityRisk', '')}"
                + (
                    f"；原因={'；'.join(flat_item.get('automatabilityReasons', []) or [])}"
                    if flat_item.get('automatabilityReasons')
                    else ""
                )
            )
            if str(flat_item.get('automatabilityRisk', '')).strip()
            else ""
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


# 同行标签→控件关联：下拉框/输入框常紧贴标签右缘，允许的最大水平间隙。
_SAME_ROW_MAX_GAP = 60
# 同行关联允许控件左缘略早于标签右缘的容差（超过则视为重叠，交由重叠关联处理）。
_SAME_ROW_LEFT_TOLERANCE = 20
# 判定“同一行”所需的最小垂直重叠比例。
_SAME_ROW_MIN_VERTICAL_OVERLAP = 0.5
# 纵向相邻关联：标签在控件正上/下方时，允许的最大垂直间隙与最小水平重叠比例。
_VERTICAL_MAX_GAP = 40
_VERTICAL_MIN_HORIZONTAL_OVERLAP = 0.5
# 矩形重叠关联：标签矩形与控件矩形大面积重叠（如标签覆盖下拉框）时的最小重叠比例。
_MIN_RECT_OVERLAP_RATIO = 0.5
_ASSOC_LABEL_TYPES = {"text", "label", "static", "textblock"}
_ASSOC_ACTIONABLE_TYPES = {"button", "combobox", "edit", "spinner", "splitbutton"}
_DROPDOWN_AUTOMATION_ID_HINTS = ("dropdownbutton", "combobox", "combo")
_DROPDOWN_CLASS_HINTS = ("radcombobox", "combobox")
_DROPDOWN_OPTION_CLASS_HINTS = ("comboboxitem",)
# 自动展开下拉框采选项：expand() 后等待弹出层渲染的时间（秒）与遍历选项的最大深度。
_DROPDOWN_EXPAND_WAIT_SECONDS = 0.4
_DROPDOWN_OPTION_WALK_MAX_DEPTH = 5


def _control_is_dropdown(item):
    """识别下拉框：ComboBox 类型，或 automationId/className 命中下拉按钮/组合框特征。"""
    if not isinstance(item, dict):
        return False
    control_type = str(item.get("controlType", "")).strip().lower()
    if control_type == "combobox":
        return True
    automation_id = str(item.get("automationId", "")).strip().lower()
    class_name = str(item.get("className", "")).strip().lower()
    if any(hint in automation_id for hint in _DROPDOWN_AUTOMATION_ID_HINTS):
        return True
    if any(hint in class_name for hint in _DROPDOWN_CLASS_HINTS):
        return True
    return False


def _control_is_dropdown_option(item):
    """识别下拉框可选项：ListItem 或 RadComboBoxItem 一类的选项控件。"""
    if not isinstance(item, dict):
        return False
    control_type = str(item.get("controlType", "")).strip().lower()
    class_name = str(item.get("className", "")).strip().lower()
    if control_type == "listitem":
        return True
    return any(hint in class_name for hint in _DROPDOWN_OPTION_CLASS_HINTS)


def _wrapper_is_dropdown_option(wrapper):
    """live 包装器层面判定是否为下拉选项（ListItem/RadComboBoxItem）。"""
    control_type = normalize_control_type_name(
        _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
    ).strip().lower()
    if control_type == "listitem":
        return True
    class_name = str(_safe_get_value(lambda: wrapper.class_name(), "")).strip().lower()
    return any(hint in class_name for hint in _DROPDOWN_OPTION_CLASS_HINTS)


def _wrapper_option_text(wrapper):
    text = str(_safe_get_value(lambda: wrapper.window_text(), "")).strip()
    if not text:
        text = str(_safe_get_value(lambda: getattr(wrapper.element_info, "name", ""), "")).strip()
    return text


def _looks_like_type_name(text):
    """判断文本是否形如 WPF 绑定对象的类型全名（如 MTD.Xxx.Yyy），此类值非用户可读选项。"""
    text = str(text or "").strip()
    if not text:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", text))


def _extract_option_text(item_wrapper, max_depth=3):
    """提取单个选项的可读文本：选项自身 name 若是类型全名（如 MTDLocalizedEnumValue
    的 ToString），则下钻到其子级 TextBlock 取真正显示文本（如“组”）。"""
    text = _wrapper_option_text(item_wrapper)
    if text and not _looks_like_type_name(text):
        return text
    found = [""]

    def _walk(wrapper, depth):
        if found[0] or wrapper is None or depth > max_depth:
            return
        for child in _safe_get_value(lambda: wrapper.children(), []) or []:
            candidate = _wrapper_option_text(child)
            if candidate and not _looks_like_type_name(candidate):
                found[0] = candidate
                return
            _walk(child, depth + 1)

    _walk(item_wrapper, 0)
    return found[0]


def _collect_option_texts_from_wrapper(root_wrapper, max_depth=_DROPDOWN_OPTION_WALK_MAX_DEPTH):
    """从给定包装器向下遍历，收集 ListItem/RadComboBoxItem 选项的可读文本（保序去重）。"""
    texts = []
    seen = set()

    def _walk(wrapper, depth):
        if wrapper is None or depth > max_depth:
            return
        for child in _safe_get_value(lambda: wrapper.children(), []) or []:
            if _wrapper_is_dropdown_option(child):
                text = _extract_option_text(child)
                if text and text not in seen and not _looks_like_type_name(text):
                    seen.add(text)
                    texts.append(text)
                # 选项内部不再继续下钻，避免重复采集其子文本。
            else:
                _walk(child, depth + 1)

    _walk(root_wrapper, 0)
    return texts


def _resolve_expandable_wrapper(wrapper, max_up=4):
    """从命中的下拉相关控件（如 PART_DropDownButton）向上寻找支持
    ExpandCollapse 的元素（如 RadComboBox）；找不到则兑底返回原控件。"""
    current = wrapper
    for _ in range(max_up + 1):
        if current is None:
            break
        if hasattr(current, "expand") and hasattr(current, "collapse"):
            return current
        current = _safe_get_value(lambda: current.parent(), None)
    return wrapper


def _open_dropdown(wrapper, expandable):
    """尝试程序化打开下拉框，返回 (opened, closer, strategy)。

    优先 UIA ExpandCollapse（RadComboBox）；其次 Toggle（PART_DropDownButton 常为
    toggle 按钮）；最后 Invoke 兑底。closer 用于采完后恢复关闭，strategy 记录成功策略。
    """
    if expandable is not None and hasattr(expandable, "expand") and hasattr(expandable, "collapse"):
        try:
            expandable.expand()
            return True, (lambda: _safe_get_value(lambda: expandable.collapse(), None)), "expand"
        except Exception:
            pass
    for target in (wrapper, expandable):
        if target is not None and hasattr(target, "toggle"):
            try:
                target.toggle()
                return True, (lambda t=target: _safe_get_value(lambda: t.toggle(), None)), "toggle"
            except Exception:
                pass
    for target in (wrapper, expandable):
        if target is not None and hasattr(target, "invoke"):
            try:
                target.invoke()
                return True, (lambda t=target: _safe_get_value(lambda: t.invoke(), None)), "invoke"
            except Exception:
                pass
    return False, None, ""


def _wrap_top_level_element(elem_info, backend):
    """把 desktop 的原始子 element_info 包装成可遍历的控件包装器。

    Desktop.windows() 会因可见性/标题等默认过滤漏掉 WPF 弹出窗口，故需从
    element_info.children() 拿到未过滤的顶层窗口再手动包装。
    """
    if elem_info is None:
        return None
    try:
        if backend == "uia":
            from pywinauto.controls.uiawrapper import UIAWrapper
            return UIAWrapper(elem_info)
        from pywinauto.controls.hwndwrapper import HwndWrapper
        return HwndWrapper(elem_info)
    except Exception:
        return None


def _enumerate_top_level_windows(backend):
    """枚举顶层窗口：合并 Desktop.windows()（已过滤）与 desktop 原始子节点
    （未过滤，含 WPF 弹出窗口），按句柄/运行时 ID 去重后返回。"""
    results = []
    seen_keys = set()

    def _add(win):
        if win is None:
            return
        key = _get_wrapper_handle(win) or _get_wrapper_runtime_id(win)
        if key:
            if key in seen_keys:
                return
            seen_keys.add(key)
        results.append(win)

    try:
        for win in Desktop(backend=backend).windows():
            _add(win)
    except Exception:
        pass
    try:
        desktop = Desktop(backend=backend)
        raw_children = _safe_get_value(lambda: desktop.element_info.children(), []) or []
        for child_info in raw_children:
            _add(_wrap_top_level_element(child_info, backend))
    except Exception:
        pass
    return results


def _collect_options_near_anchor(root_wrapper, anchor_rect, max_depth=14):
    """在主窗口视觉树内按锚点矩形圈定“弹出区域”，采集其中的下拉选项文本。

    用于 WPF Popup 内嵌于主窗口视觉树（未独立成顶层窗口）的情形：此时选项项
    仍是主窗口的后代，但位于锚点下拉按钮正下方的弹出层。仅按空间位置圈定，
    避免把主窗口其它无关 ListItem 也当成本下拉框的选项。
    """
    anchor = _normalize_rect_dict(anchor_rect)
    if root_wrapper is None or not anchor:
        return []
    zone = {
        "left": anchor["left"] - 40,
        "top": anchor["top"] - 4,
        "right": anchor["right"] + 40,
        "bottom": anchor["bottom"] + 600,
    }
    texts = []
    seen = set()

    def _walk(wrapper, depth):
        if wrapper is None or depth > max_depth:
            return
        for child in _safe_get_value(lambda: wrapper.children(), []) or []:
            if _wrapper_is_dropdown_option(child):
                rect = _rect_to_dict(_safe_get_value(lambda: child.rectangle(), None))
                if rect and _rect_intersects(rect, zone):
                    text = _extract_option_text(child)
                    if text and text not in seen and not _looks_like_type_name(text):
                        seen.add(text)
                        texts.append(text)
            else:
                _walk(child, depth + 1)

    _walk(root_wrapper, 0)
    return texts


def _nearest_option_ancestor(wrapper, max_up=5):
    """从命中点的元素向上寻找最近的下拉选项容器（ListItem/RadComboBoxItem）。

    Desktop.from_point 命中的往往是选项内部的 TextBlock 或选项本身，需向上回溯
    到选项容器再统一提取文本，同时借此过滤掉滚动条/边框等非选项命中。
    """
    current = wrapper
    for _ in range(max_up + 1):
        if current is None:
            return None
        if _wrapper_is_dropdown_option(current):
            return current
        current = _safe_get_value(lambda c=current: c.parent(), None)
    return None


def _realize_options_by_point_sweep(anchor_rect, backend="uia", desktop=None,
                                    step=22, max_span=640, existing=None, diag=None):
    """在锚点下拉按钮正下方按屏幕坐标做“命中点扫掠”，强制实体化并采集选项文本。

    MTD 等 WPF 下拉框对选项列表启用 UI 虚拟化：选项节点只有在被 UIA 命中测试
    （ElementFromPoint，即鼠标悬停到该处）时才实体化，普通子树遍历/顶层窗口枚举
    都取不到。故用 Desktop.from_point 从按钮正下方逐行命中弹出区域，触发实体化后
    读取可读文本。from_point 仅做命中测试，不移动物理鼠标、不提交选择，无副作用。
    desktop 可注入以便测试。
    """
    anchor = _normalize_rect_dict(anchor_rect)
    if not anchor:
        return []
    if desktop is None:
        if Desktop is None:
            return []
        desktop = _safe_get_value(lambda: Desktop(backend=backend), None)
    if desktop is None or not hasattr(desktop, "from_point"):
        return []
    x = (anchor["left"] + anchor["right"]) // 2
    y = anchor["bottom"] + 2
    limit_y = anchor["bottom"] + max_span
    seen = set(existing or [])
    ordered = []
    hits = 0
    misses = 0
    while y <= limit_y and misses < 6:
        wrapper = _safe_get_value(lambda yy=y: desktop.from_point(x, yy), None)
        option = _nearest_option_ancestor(wrapper)
        text = _extract_option_text(option) if option is not None else ""
        if text and not _looks_like_type_name(text):
            if text not in seen:
                seen.add(text)
                ordered.append(text)
            hits += 1
            misses = 0
        elif ordered:
            # 已进入选项列表后连续多次命中空白，视为已越过列表末尾，提前结束。
            misses += 1
        y += step
    if isinstance(diag, dict):
        diag["pointSweepHits"] = hits
        diag["pointSweepOptionCount"] = len(ordered)
    return ordered


def _collect_options_from_popups(reference_wrapper, backend, diag=None, anchor_rect=None):
    """从同进程的顶层 Popup 窗口中采集选项，兑底再从主窗口内嵌弹出层采集。

    WPF 下拉框（如 MTD 自定义 DropDownButton）展开后，选项常位于一个独立的
    顶层弹出窗口（ListItem 的祖先直接是 Window 而非该下拉框）。此类窗口常被
    Desktop.windows() 的默认过滤漏掉，故改用 _enumerate_top_level_windows 合并
    未过滤的 desktop 原始子节点一并扫描；若仍无所获，则回退到主窗口视觉树内按
    锚点空间位置圈定弹出区域采集。
    """
    if Desktop is None or reference_wrapper is None:
        return []
    process_id = str(_safe_get_value(lambda: getattr(reference_wrapper.element_info, "process_id", ""), "")).strip()
    main_top = _safe_get_value(lambda: reference_wrapper.top_level_parent(), None)
    main_handle = _get_wrapper_handle(main_top) if main_top is not None else ""
    windows = _enumerate_top_level_windows(backend)
    texts = []
    seen = set()
    scanned = 0
    windows_with_options = []
    for win in windows:
        win_handle = _get_wrapper_handle(win)
        if main_handle and str(win_handle) == str(main_handle):
            continue  # 跳过主窗口（体量庞大，稍后按需做锚点区域兑底）
        win_pid = str(_safe_get_value(lambda: getattr(win.element_info, "process_id", ""), "")).strip()
        if process_id and win_pid and win_pid != process_id:
            continue
        scanned += 1
        win_texts = _collect_option_texts_from_wrapper(win, max_depth=_DROPDOWN_OPTION_WALK_MAX_DEPTH + 3)
        if win_texts and isinstance(diag, dict):
            windows_with_options.append(
                {
                    "className": str(_safe_get_value(lambda: win.class_name(), "")).strip(),
                    "optionCount": len(win_texts),
                }
            )
        for text in win_texts:
            if text not in seen:
                seen.add(text)
                texts.append(text)
    # 兑底：弹出层可能内嵌在主窗口视觉树中（未独立成顶层窗口），按锚点圈定区域采集。
    if not texts and main_top is not None and anchor_rect:
        near_texts = _collect_options_near_anchor(main_top, anchor_rect)
        if near_texts:
            texts = near_texts
            if isinstance(diag, dict):
                diag["popupFromMainWindow"] = True
    if isinstance(diag, dict):
        diag["popupWindowsScanned"] = scanned
        diag["popupWindowsTotal"] = len(windows)
        if windows_with_options:
            diag["popupWindowsWithOptions"] = windows_with_options
    return texts


def _expand_dropdown_and_collect_options(wrapper, backend="uia", diag=None, anchor_rect=None):
    """程序化展开下拉框、采集其可选项文本、再收回。仅在 live UIA 下有效。

    打开策略：ExpandCollapse -> Toggle -> Invoke（兼容 RadComboBox 与 PART_DropDownButton）。
    读取策略：ComboBox.item_texts -> 控件子树 -> 父级子树 -> 顶层 Popup 窗口 -> 命中点扫掠。
    任何异常都被吞掉并返回已采集到的部分，避免影响整体扫描。diag 可传入 dict 记录诊断过程。
    """
    if wrapper is None:
        return []
    expandable = _resolve_expandable_wrapper(wrapper)
    options = []
    closer = None
    opened = False
    stage = ""
    try:
        opened, closer, strategy = _open_dropdown(wrapper, expandable)
        if isinstance(diag, dict):
            diag["opened"] = bool(opened)
            diag["openStrategy"] = strategy
        if opened:
            time.sleep(_DROPDOWN_EXPAND_WAIT_SECONDS)
        # 1) pywinauto ComboBox 的 item_texts（若可用）。
        item_texts = _safe_get_value(lambda: expandable.item_texts(), None)
        if item_texts:
            options = [
                str(text).strip()
                for text in item_texts
                if str(text).strip() and not _looks_like_type_name(str(text).strip())
            ]
            if options:
                stage = "item_texts"
        # 2) 控件自身子树（项在子树内，如 RadComboBox）。
        if not options:
            options = _collect_option_texts_from_wrapper(expandable)
            if options:
                stage = "control_subtree"
        # 3) 父级子树兑底。
        if not options:
            parent = _safe_get_value(lambda: expandable.parent(), None)
            if parent is not None:
                options = _collect_option_texts_from_wrapper(parent)
                if options:
                    stage = "parent_subtree"
        # 4) 顶层 Popup 窗口（WPF 下拉项常在独立弹出窗口）。
        if not options and opened:
            options = _collect_options_from_popups(wrapper, backend, diag=diag, anchor_rect=anchor_rect)
            if options:
                stage = "popup_window"
        # 5) 命中点扫掠兑底（WPF 虚拟化：选项仅在被命中/悬停时实体化，前述遍历均取不到）。
        # 注意：即使第 4 步 near-anchor 已返回结果，点扫掠也必须运行——near-anchor 走的是
        # 主窗口静态树，可能返回假阳性或过时结果；点扫掠通过 from_point 强制实体化，结果
        # 更可靠，优先采用。
        if opened and anchor_rect:
            sweep_options = _realize_options_by_point_sweep(anchor_rect, backend, diag=diag)
            if sweep_options:
                options = sweep_options
                stage = "point_sweep"
    except Exception as exc:
        if isinstance(diag, dict):
            diag["error"] = str(exc)
    finally:
        if closer is not None:
            _safe_get_value(closer, None)
    result = []
    seen = set()
    for text in options:
        text = str(text).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    if isinstance(diag, dict):
        diag["optionStage"] = stage
        diag["optionCount"] = len(result)
    return result


def _expand_region_dropdowns(flat_controls, region_rect, backend="uia"):
    """对画框区域内的下拉框逐个展开采集可选项，写入 optionValues，并返回诊断列表。

    该操作会真实展开/收回界面下拉框，仅在显式开启 expand_dropdowns 开关时调用。
    依赖 flat_controls 中临时保留的 _wrapperRef（live 包装器）。为避免展开远离画框
    区域的无关下拉框（如标签关联可能跨窗口命中），只对自身矩形与画框区域
    实际相交的下拉框执行展开。
    """
    region_rect = _normalize_rect_dict(region_rect)
    diagnostics = []
    for item in flat_controls:
        if not isinstance(item, dict) or not _control_is_dropdown(item):
            continue
        wrapper = item.get("_wrapperRef")
        item_rect = _normalize_rect_dict(item.get("boundingBox"))
        if not region_rect:
            in_scope = True
        else:
            in_scope = bool(item_rect and _rect_intersects(item_rect, region_rect))
        entry = {
            "identity": str(item.get("automationId", "")).strip() or str(item.get("name", "")).strip(),
            "className": item.get("className", ""),
            "controlType": item.get("controlType", ""),
            "rect": item_rect,
            "inScope": bool(in_scope),
            "hasWrapper": wrapper is not None,
        }
        if not in_scope:
            diagnostics.append(entry)
            continue
        if wrapper is None:
            entry["note"] = "no_wrapper_ref"
            diagnostics.append(entry)
            continue
        options = _expand_dropdown_and_collect_options(wrapper, backend, diag=entry, anchor_rect=item_rect)
        if options:
            item["optionValues"] = options
            item["optionCount"] = len(options)
            inspect_data = item.get("inspectData")
            if isinstance(inspect_data, dict):
                inspect_data["optionValues"] = options
        diagnostics.append(entry)
    return diagnostics



def _control_is_assoc_label(item):
    if not isinstance(item, dict):
        return False
    control_type = str(item.get("controlType", "")).strip().lower()
    if control_type not in _ASSOC_LABEL_TYPES:
        return False
    return bool(str(item.get("name", "")).strip())


def _control_is_assoc_actionable(item):
    if _control_is_dropdown(item):
        return True
    control_type = str(item.get("controlType", "")).strip().lower()
    return control_type in _ASSOC_ACTIONABLE_TYPES


def _vertical_overlap_ratio(rect_a, rect_b):
    rect_a = _normalize_rect_dict(rect_a)
    rect_b = _normalize_rect_dict(rect_b)
    if not rect_a or not rect_b:
        return 0.0
    top = max(rect_a["top"], rect_b["top"])
    bottom = min(rect_a["bottom"], rect_b["bottom"])
    if bottom <= top:
        return 0.0
    min_height = max(1, min(rect_a["bottom"] - rect_a["top"], rect_b["bottom"] - rect_b["top"]))
    return (bottom - top) / float(min_height)


def _horizontal_overlap_ratio(rect_a, rect_b):
    rect_a = _normalize_rect_dict(rect_a)
    rect_b = _normalize_rect_dict(rect_b)
    if not rect_a or not rect_b:
        return 0.0
    left = max(rect_a["left"], rect_b["left"])
    right = min(rect_a["right"], rect_b["right"])
    if right <= left:
        return 0.0
    min_width = max(1, min(rect_a["right"] - rect_a["left"], rect_b["right"] - rect_b["left"]))
    return (right - left) / float(min_width)


def _rect_overlap_ratio(rect_a, rect_b):
    """交集面积 / 较小矩形面积，衡量两矩形重叠程度。"""
    intersection = _intersection_area(rect_a, rect_b)
    if intersection <= 0:
        return 0.0
    min_area = max(1, min(_rect_area(rect_a), _rect_area(rect_b)))
    return intersection / float(min_area)


def _fold_dropdown_value_texts(controls, dropdowns):
    """下拉框自身常无 name，真实显示值在其矩形内的独立 TextBlock 中。

    将该值文本并入对应下拉框（记录 dropdownValueText/value），并标记该文本为
    foldedIntoDropdown，避免它作为孤立“建议忽略”控件干扰入库。
    """
    if not dropdowns:
        return
    for text_item in controls:
        if not isinstance(text_item, dict):
            continue
        control_type = str(text_item.get("controlType", "")).strip().lower()
        if control_type not in _ASSOC_LABEL_TYPES:
            continue
        # 具备独立 automationId 的文本更可能是真实标签，不折叠。
        if str(text_item.get("automationId", "")).strip():
            continue
        text_rect = _normalize_rect_dict(text_item.get("boundingBox"))
        if not text_rect:
            continue
        for dropdown in dropdowns:
            dropdown_rect = _normalize_rect_dict(dropdown.get("boundingBox"))
            if not dropdown_rect or not _rect_contains(text_rect, dropdown_rect):
                continue
            text_item["foldedIntoDropdown"] = True
            text_item["foldedDropdownAutomationId"] = str(dropdown.get("automationId", "")).strip()
            value_text = str(text_item.get("name", "")).strip()
            if value_text:
                dropdown["dropdownValueText"] = value_text
                if not str(dropdown.get("value", "")).strip():
                    dropdown["value"] = value_text
                # 将动态文本复制到下拉框的 inspectData.name 和 inspectData.value，
                # 确保运行时无需定位值 TextBlock 即可读取下拉框当前值。
                inspect_data = dropdown.get("inspectData")
                if isinstance(inspect_data, dict):
                    if not str(inspect_data.get("name", "")).strip():
                        inspect_data["name"] = value_text
                    if not str(inspect_data.get("value", "")).strip():
                        inspect_data["value"] = value_text
            # 将折叠值 TextBlock 的定位器改为基于 className+control_type（而非 name），
            # 因为其 name 是动态显示值（"公共"/"私有"等），会随选中项变化，不能作为定位特征。
            # className+control_type 稳定不变，后续 _disambiguate_duplicate_locators 会
            # 在同父级多个同类 TextBlock 间追加 found_index 消歧。
            class_name = str(text_item.get("className", "")).strip()
            control_type = str(text_item.get("controlType", "")).strip()
            if class_name and control_type:
                text_item["recommendedTargetMethod"] = "class_name,control_type"
                text_item["recommendedTargetValue"] = f"{class_name},{control_type}"
                text_item["locatorScore"] = 68
                text_item["locatorReason"] = "class_name + control_type（折叠值文本，name 为动态值不可靠）"
                # 同步更新 inspectData 中的定位器
                inspect_data = text_item.get("inspectData")
                if isinstance(inspect_data, dict):
                    inspect_data["recommendedTargetMethod"] = "class_name,control_type"
                    inspect_data["recommendedTargetValue"] = f"{class_name},{control_type}"
            break


def _find_same_row_control(label_rect, controls, label, claimed):
    """同行关联：控件位于标签右侧或与其重叠、垂直高度充分重叠、水平间隙小。"""
    label_center_y = (label_rect["top"] + label_rect["bottom"]) / 2.0
    best = None
    best_key = None
    for candidate in controls:
        if candidate is label or id(candidate) in claimed or not _control_is_assoc_actionable(candidate):
            continue
        candidate_rect = _normalize_rect_dict(candidate.get("boundingBox"))
        if not candidate_rect:
            continue
        if _vertical_overlap_ratio(label_rect, candidate_rect) < _SAME_ROW_MIN_VERTICAL_OVERLAP:
            continue
        # 控件应位于标签右侧（下拉框常紧贴标签右缘）；若控件左缘明显落入标签内部，
        # 则属重叠布局，不在同行关联处理。
        horizontal_gap = candidate_rect["left"] - label_rect["right"]
        if horizontal_gap < -_SAME_ROW_LEFT_TOLERANCE or horizontal_gap > _SAME_ROW_MAX_GAP:
            continue
        horizontal_gap = max(0, horizontal_gap)
        candidate_center_y = (candidate_rect["top"] + candidate_rect["bottom"]) / 2.0
        # 排序键：间隙优先，其次下拉框优先，再次垂直中心线接近。
        dropdown_rank = 0 if _control_is_dropdown(candidate) else 1
        key = (horizontal_gap, dropdown_rank, abs(candidate_center_y - label_center_y))
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    return best, "same-row-label"


def _find_vertical_or_overlap_control(label_rect, controls, label, claimed):
    """纵向/重叠关联：处理标签在控件正上方、或标签矩形与控件大面积重叠的布局。

    优先级：矩形大面积重叠 > 正下方相邻 > 正上方相邻；同类中下拉框优先、间隙更小优先。
    返回 (best_control, relation)。
    """
    best = None
    best_key = None
    best_relation = ""
    for candidate in controls:
        if candidate is label or id(candidate) in claimed or not _control_is_assoc_actionable(candidate):
            continue
        candidate_rect = _normalize_rect_dict(candidate.get("boundingBox"))
        if not candidate_rect:
            continue
        dropdown_rank = 0 if _control_is_dropdown(candidate) else 1
        overlap_ratio = _rect_overlap_ratio(label_rect, candidate_rect)
        if overlap_ratio >= _MIN_RECT_OVERLAP_RATIO:
            key = (0, dropdown_rank, -overlap_ratio)
            relation = "overlap-label"
        elif _horizontal_overlap_ratio(label_rect, candidate_rect) >= _VERTICAL_MIN_HORIZONTAL_OVERLAP:
            gap_below = candidate_rect["top"] - label_rect["bottom"]
            gap_above = label_rect["top"] - candidate_rect["bottom"]
            if 0 <= gap_below <= _VERTICAL_MAX_GAP:
                key = (1, dropdown_rank, gap_below)
                relation = "vertical-label"
            elif 0 <= gap_above <= _VERTICAL_MAX_GAP:
                key = (2, dropdown_rank, gap_above)
                relation = "vertical-label"
            else:
                continue
        else:
            continue
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
            best_relation = relation
    return best, best_relation


def _associate_region_labels_with_controls(flat_controls):
    """在 dict 级对采集结果做“标签→实际控件”关联，独立于实时探测路径。

    分两轮：
    1. 同行关联（最强）：标签右侧、同行、间隙小的可操作控件（下拉框优先）。
    2. 纵向/重叠关联：对未命中同行的标签，再尝试正上/下方相邻或矩形大面积重叠的控件。

    已被关联的控件不会被其他标签重复抢占；关联后标记 regionRelated/relatedLabelName，
    使其即便部分落在画框外也会保留。另外折叠下拉框内部显示值文本、标记下拉框可选项。
    """
    if not flat_controls:
        return flat_controls
    controls = [item for item in flat_controls if isinstance(item, dict)]
    dropdowns = [item for item in controls if _control_is_dropdown(item)]
    labels = []
    for label in controls:
        if not _control_is_assoc_label(label):
            continue
        label_rect = _normalize_rect_dict(label.get("boundingBox"))
        if label_rect:
            labels.append((label, label_rect))

    claimed = set()

    def _bind(label, control, relation):
        control["regionRelated"] = True
        if not str(control.get("regionRelation", "")).strip():
            control["regionRelation"] = relation
        control["relatedLabelName"] = str(label.get("name", "")).strip()
        claimed.add(id(control))

    # 第一轮：同行关联（最强、最可靠）。
    pending = []
    for label, label_rect in labels:
        best, relation = _find_same_row_control(label_rect, controls, label, claimed)
        if best is not None:
            _bind(label, best, relation)
        else:
            pending.append((label, label_rect))

    # 第二轮：纵向/重叠关联（仅处理未命中同行的标签，避开已被占用的控件）。
    for label, label_rect in pending:
        best, relation = _find_vertical_or_overlap_control(label_rect, controls, label, claimed)
        if best is not None:
            _bind(label, best, relation)

    _fold_dropdown_value_texts(controls, dropdowns)
    for item in controls:
        if _control_is_dropdown_option(item):
            item["dropdownOption"] = True
    return flat_controls


def _filter_flat_controls_by_region(flat_controls, region_rect):
    region_rect = _normalize_rect_dict(region_rect)
    if not region_rect:
        return list(flat_controls)
    ranked = []
    for item in flat_controls:
        item_rect = _normalize_rect_dict(item.get("boundingBox"))
        related = bool(item.get("regionRelated"))
        if not item_rect:
            continue
        if related:
            # 祖级横向关联控件可能在画框外，但它是画框标签对应的实际输入/下拉控件。
            score = int(item.get("locatorScore", 0) or 0) * 1000
            score += 500 - int(item.get("depth", 0) or 0) * 10
            ranked.append((score, item))
            continue
        if not _rect_intersects(item_rect, region_rect):
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
    """A+C: 过滤离屏控件和无标识容器，减少无关信息。

    特殊处理：ComboBox 的子级（下拉选项）即使 isOffscreen=True 也保留，
    因为它们是下拉框未展开时不可见的选项。
    """
    if not controls:
        return controls
    # 找出所有 ComboBox 的 runtimeId，用于判断子级是否属于下拉框
    combobox_runtime_ids = set()
    for item in controls:
        if str(item.get("controlType", "")).strip().lower() == "combobox":
            rid = str(item.get("runtimeId", "")).strip()
            if rid:
                combobox_runtime_ids.add(rid)

    filtered = []
    for item in controls:
        if not isinstance(item, dict):
            continue
        control_type = str(item.get("controlType", "")).strip().lower()
        # A: 过滤离屏控件（IsOffscreen=True），但保留 ComboBox 的子级
        if exclude_offscreen and str(item.get("isOffscreen", "")).strip().lower() == "true":
            parent_path = str(item.get("parentPath", "")).strip()
            parent_runtime = str(item.get("inspectData", {}).get("ancestors", [])[-1] if item.get("inspectData", {}).get("ancestors") else "").strip() if isinstance(item.get("inspectData"), dict) else ""
            # 如果控件类型是 ListItem/ListItem 且属于 ComboBox，则保留
            if control_type in {"listitem", "list"} and parent_path:
                continue  # ListItem 可能是 ComboBox 的下拉选项，即使离屏也保留
            continue
        # C: 过滤无 name 且无 automationId 的容器（Custom/Pane/Group）
        if exclude_unidentified_containers:
            has_name = bool(str(item.get("name", "")).strip())
            has_automation_id = bool(str(item.get("automationId", "")).strip())
            has_label_text = bool(str(item.get("labelText", "")).strip())
            if control_type in {"custom", "pane", "group"} and not has_name and not has_automation_id and not has_label_text:
                continue
        filtered.append(item)
    return filtered


def _normalize_textbox_wrappers(flat_controls):
    """识别 WPF TextBox/PasswordBox 控件并修正其 controlType 为 Edit。

    WPF 的 TextBox UIA 控件可能报告 controlType="Custom"（而非 "Edit"），
    导致 _filter_noise_controls（过滤无标识容器）将其丢弃，且 _backfill_label_text_to_controls
    （标签关联回填）因只处理 edit/combobox/spinner/document 而跳过它。

    此外 WPF TextBox 内部包含 PART_ContentHost（ScrollViewer）作为编辑区域，
    该子控件设了 IsContentElement=False / IsControlElement=False，
    pywinauto 默认树 walker（ContentViewWalker）不会返回它。
    在 from_point 探针命中 PART_ContentHost 时，沿祖先链向上找到父级 TextBox 并标记。

    策略：
    1. className 含 "TextBox"/"PasswordBox" 且 controlType 为 Custom/Pane → 改为 Edit
    2. automationId="PART_ContentHost" → 向上找到父级 TextBox 并标记父级，将自身折叠
    """
    if not flat_controls:
        return

    # 建立索引以支持 parentIndex 向上查找
    by_index = {}
    for idx, item in enumerate(flat_controls):
        if isinstance(item, dict):
            by_index[idx] = item

    for idx, item in enumerate(flat_controls):
        if not isinstance(item, dict):
            continue
        class_name = str(item.get("className", "")).strip().lower()
        control_type = str(item.get("controlType", "")).strip().lower()
        automation_id = str(item.get("automationId", "")).strip()

        # 情况 1：TextBox/PasswordBox 包装器被标记为 Custom/Pane
        if class_name in ("textbox", "passwordbox", "richtextbox"):
            if control_type in ("custom", "pane", "group"):
                item["controlType"] = "Edit"
                item["controlTypeSource"] = "normalized-from-classname"
                # 同步更新 inspectData
                inspect = item.get("inspectData")
                if isinstance(inspect, dict):
                    inspect["controlType"] = "Edit"

        # 情况 2：PART_ContentHost — WPF TextBox 内部 ScrollViewer，
        # IsContentElement=False，UIA tree walker 可跳过；from_point 可命中。
        # 要向上找到父级 TextBox 并交换身份。
        if automation_id == "PART_ContentHost" and control_type in ("pane", "custom"):
            # 查找父级 TextBox
            parent_idx = item.get("parentIndex")
            textbox_parent = None
            if parent_idx is not None and parent_idx in by_index:
                parent = by_index[parent_idx]
                parent_class = str(parent.get("className", "")).strip().lower()
                if parent_class in ("textbox", "passwordbox", "richtextbox"):
                    textbox_parent = parent
                    # 父级是真正的 TextBox，规范化其 controlType
                    if str(parent.get("controlType", "")).strip().lower() in ("custom", "pane", "group"):
                        parent["controlType"] = "Edit"
                        parent["controlTypeSource"] = "normalized-from-contenthost"
                        inspect_p = parent.get("inspectData")
                        if isinstance(inspect_p, dict):
                            inspect_p["controlType"] = "Edit"
            # 折叠 PART_ContentHost 自身 — 它不应作为独立控件被定位/操作
            item["foldedIntoParent"] = True
            item["qualityTier"] = "建议忽略"
            item["qualityReason"] = ("PART_ContentHost: WPF TextBox 内部编辑区域，"
                                      "非独立可定位控件，已由父级 TextBox 替代")
            if textbox_parent is not None:
                item["foldedTargetIndex"] = parent_idx


def _backfill_label_text_to_controls(flat_controls):
    """标签关联回填：对弱定位的 Edit/ComboBox 用邻近标签文本增强。

    策略：
    1. 收集所有 Text 标签控件及其矩形
    2. 对没有 name 和 automationId 的 Edit/ComboBox，找最近标签
    3. 把标签文本写入 labelText 字段
    4. 如果控件本身没有 name，用标签文本作为 name 回填
    5. 重新计算定位评分
    """
    if not flat_controls:
        return

    # 收集标签控件
    labels = []
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        control_type = str(item.get("controlType", "")).strip().lower()
        if control_type in {"text", "label", "static", "textblock"}:
            name = str(item.get("name", "")).strip()
            rect = _normalize_rect_dict(item.get("boundingBox"))
            if name and rect:
                labels.append({"name": name, "rect": rect, "path": str(item.get("uiPath", "")).strip()})

    if not labels:
        return

    # 对弱定位控件回填
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        control_type = str(item.get("controlType", "")).strip().lower()
        if control_type not in {"edit", "combobox", "spinner", "document"}:
            continue

        has_name = bool(str(item.get("name", "")).strip())
        has_automation_id = bool(str(item.get("automationId", "")).strip())
        if has_name and has_automation_id:
            continue  # 已有强定位，不需要回填

        item_rect = _normalize_rect_dict(item.get("boundingBox"))
        if not item_rect:
            continue

        # 找最近的标签
        best_label = None
        best_score = float("inf")
        for label in labels:
            label_rect = label["rect"]
            # 计算几何距离
            vertical_gap = max(0, max(item_rect["top"], label_rect["top"]) - min(item_rect["bottom"], label_rect["bottom"]))
            horizontal_gap = max(0, max(item_rect["left"], label_rect["left"]) - min(item_rect["right"], label_rect["right"]))

            # 同行优先
            if vertical_gap > 60:
                continue
            # 标签在左侧优先
            direction_penalty = 0 if label_rect["right"] <= item_rect["left"] + 20 else 30
            score = horizontal_gap + vertical_gap * 2 + direction_penalty
            if score < best_score:
                best_score = score
                best_label = label

        if best_label and best_score < 200:
            label_text = best_label["name"]
            item["labelText"] = label_text
            item["labelRelation"] = "nearest-text-label"
            # 如果控件没有 name，用标签文本回填
            if not has_name:
                item["name"] = label_text
                item["nameSource"] = "label-backfill"
            # 如果没有 automationId 但有 name 了，重新计算定位
            if not has_automation_id:
                method, value, score, reason = build_locator_recommendation(item, int(item.get("index", 0) or 0), item.get("uiPath", ""))
                if score > int(item.get("locatorScore", 0) or 0):
                    item["recommendedTargetMethod"] = method
                    item["recommendedTargetValue"] = value
                    item["locatorScore"] = score
                    item["locatorReason"] = reason
            # 重新分级质量
            quality_tier, quality_reason = _classify_control_quality(item)
            item["qualityTier"] = quality_tier
            item["qualityReason"] = quality_reason


def _locator_scope_method(target_method):
    """从复合 targetMethod 中取可作为 found_index 范围锚的方法（control_type/class_name/name）。"""
    for part in str(target_method or "").split(","):
        part = part.strip()
        if part in ("control_type", "class_name", "name"):
            return part
    return ""


def _sibling_scope_value(item, scope_method):
    if scope_method == "class_name":
        return str(item.get("className", "")).strip()
    if scope_method == "name":
        return str(item.get("name", "")).strip()
    return str(item.get("controlType", "")).strip()


def _compute_sibling_found_index(item, flat_controls, scope_method):
    """预测该控件在运行时 get_wrapper_found_index 的结果：同父容器直接子节点中、与
    scope_method 同类的兄弟里的 0 基序号。基于扫描期的 parentIndex/siblingsIndex 计算，
    与运行时按父链引导的兄弟计数保持一致。找不到返回 -1。"""
    parent_index = item.get("parentIndex", None)
    if parent_index is None:
        return -1
    target_scope = _sibling_scope_value(item, scope_method)
    if not target_scope:
        return -1
    siblings = [
        other
        for other in flat_controls
        if isinstance(other, dict)
        and other.get("parentIndex", None) == parent_index
        and _sibling_scope_value(other, scope_method) == target_scope
    ]
    siblings.sort(key=lambda x: int(x.get("siblingsIndex", 0) or 0))
    for position, sibling in enumerate(siblings):
        if sibling is item:
            return position
    return -1


def _locator_identity(item):
    """用于区分“真正不同的控件”与“同一控件的多次出现”：优先 runtimeId，其次 rect。"""
    runtime_id = str(item.get("runtimeId", "")).strip()
    if runtime_id:
        return runtime_id
    rect = _normalize_rect_dict(item.get("boundingBox"))
    if rect:
        return f"{rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
    return str(id(item))


def _disambiguate_duplicate_locators(flat_controls):
    """全局唯一性后处理：多个不同控件若共用同一 recommendedTargetValue（如
    “访问级别下拉框”和“性质下拉框”两个 PART_DropDownButton 都得到
    "PART_DropDownButton,Button"），运行时会双双定位到同一个控件。此处对这类冲突
    控件追加 found_index 消歧（同父容器内按 control_type/class_name/name 计数），
    使各自定位唯一，并避免它们在分组去重中被误并为一个。
    """
    groups = {}
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        method = str(item.get("recommendedTargetMethod", "")).strip()
        value = str(item.get("recommendedTargetValue", "")).strip()
        if not method or not value or "found_index" in method:
            continue
        groups.setdefault((method, value), []).append(item)
    for (method, value), members in groups.items():
        # 仅对“真正不同的控件”消歧（不同 runtimeId/rect），跨后端同一控件不处理。
        distinct = []
        seen_identity = set()
        for member in members:
            identity = _locator_identity(member)
            if identity in seen_identity:
                continue
            seen_identity.add(identity)
            distinct.append(member)
        if len(distinct) < 2:
            continue
        scope_method = _locator_scope_method(method)
        if not scope_method:
            continue
        assigned = {}
        for member in distinct:
            found_index = _compute_sibling_found_index(member, flat_controls, scope_method)
            if found_index < 0 or found_index in assigned:
                continue
            assigned[found_index] = member
        # 只有当消歧确实给出彼此不同的序号时才落地，否则保持原样避免误伤。
        if len(assigned) < 2:
            continue
        for found_index, member in assigned.items():
            member["recommendedTargetMethod"] = method + ",found_index"
            member["recommendedTargetValue"] = value + "," + str(found_index)
            reason = str(member.get("locatorReason", "")).strip()
            member["locatorReason"] = (reason + " + found_index消歧").strip(" +")


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


def _collect_uia_dumper_flat_controls(
    target_window,
    flat_controls,
    seen_identities,
    max_depth=FULLTREE_MIN_DEPTH,
    scan_timeout_seconds=30,
    status_callback=None,
):
    """借助独立 .NET 工具 uia_tree_dumper.exe（走 UIA RawViewWalker）全量遍历控件树，
    补采纯 pywinauto 遍历漏掉的深层 / 虚拟化 / 离屏控件，对齐 Accessibility Insights 的采全能力。
    工具缺失或调用失败时静默降级，不影响已采结果。返回新增控件数量。"""
    if not os.path.isfile(UIA_TREE_DUMPER_EXE):
        return 0
    pid = str(target_window.get("processId", "")).strip()
    handle = str(target_window.get("handle", "")).strip()
    if not pid and not handle:
        return 0
    args = [UIA_TREE_DUMPER_EXE]
    if handle:
        args += ["--hwnd", handle]
    else:
        args += ["--pid", pid]
    args += ["--maxdepth", str(max(int(max_depth), FULLTREE_MIN_DEPTH))]
    timeout_ms = max(5000, int(scan_timeout_seconds) * 1000)
    args += ["--timeout", str(timeout_ms)]
    if status_callback:
        status_callback("调用 uia_tree_dumper 全量遍历 UIA 树...", len(flat_controls))
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=max(15, int(scan_timeout_seconds) + 15),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return 0
    if completed.returncode != 0 or not completed.stdout:
        return 0
    try:
        records = json.loads(completed.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return 0
    if not isinstance(records, list) or not records:
        return 0

    return _merge_uia_dumper_records(
        records, target_window, flat_controls, seen_identities, status_callback
    )
# __UIA_DUMPER_MERGE_PLACEHOLDER__
def _merge_uia_dumper_records(records, target_window, flat_controls, seen_identities, status_callback=None):
    """将 uia_tree_dumper 输出的记录数组转换为标准 flat_control 结构并去重合并。"""
    by_index = {}
    for rec in records:
        try:
            by_index[int(rec.get("index"))] = rec
        except Exception:
            continue

    root_title = str(target_window.get("title", "")).strip()
    root_class = str(target_window.get("className", "")).strip()

    def _seg_name(rec):
        parsed = {
            "name": str(rec.get("name") or "").strip(),
            "automationId": str(rec.get("automationId") or "").strip(),
            "className": str(rec.get("className") or "").strip(),
            "controlType": str(rec.get("controlType") or "").strip(),
            "localizedControlType": "",
        }
        return _build_display_name(parsed, "控件", int(rec.get("index", 0) or 0))

    def _path_segments_for(rec):
        chain = []
        seen_idx = set()
        cur = rec
        guard = 0
        while cur is not None and guard < 64:
            guard += 1
            idx = cur.get("index")
            if idx in seen_idx:
                break
            seen_idx.add(idx)
            chain.append(_seg_name(cur))
            try:
                parent_idx = int(cur.get("parentIndex", -1))
            except Exception:
                parent_idx = -1
            if parent_idx < 0 or parent_idx not in by_index:
                break
            cur = by_index[parent_idx]
        return list(reversed(chain))
    added = 0
    win_pid = str(target_window.get("processId", "")).strip()
    win_handle = str(target_window.get("handle", "")).strip()

    for rec in records:
        if rec.get("error"):
            continue

        try:
            depth = int(rec.get("depth", 0) or 0)
        except Exception:
            depth = 0
        try:
            index = int(rec.get("index", 0) or 0)
        except Exception:
            index = 0

        name = str(rec.get("name") or "").strip()
        class_name = str(rec.get("className") or "").strip()
        control_type = normalize_control_type_name(str(rec.get("controlType") or "").strip())
        automation_id = str(rec.get("automationId") or "").strip()
        help_text = str(rec.get("helpText") or "").strip()
        value_pattern_value = str(rec.get("value") or "").strip()
        toggle_state = ""
        expand_state = str(rec.get("expandState") or "").strip()
        runtime_id = _format_runtime_id(rec.get("runtimeId"))
        process_id = str(rec.get("processId") or "").strip() or win_pid
        handle = str(rec.get("handle") or "").strip() or (win_handle if depth == 0 else "")

        # 矩形转换: dumper 输出 {X,Y,W,H} -> {left,top,right,bottom}
        bounding_box = None
        bounding_rectangle = ""
        rect_raw = rec.get("rect")
        if isinstance(rect_raw, dict):
            try:
                rx = int(rect_raw.get("X"))
                ry = int(rect_raw.get("Y"))
                rw = int(rect_raw.get("W"))
                rh = int(rect_raw.get("H"))
                if rw > 0 and rh > 0:
                    bounding_box = {"left": rx, "top": ry, "right": rx + rw, "bottom": ry + rh}
                    bounding_rectangle = f"[l={rx},t={ry},r={rx + rw},b={ry + rh}]"
            except Exception:
                bounding_box = None

        is_offscreen_raw = rec.get("isOffscreen")
        is_offscreen = str(bool(is_offscreen_raw)) if is_offscreen_raw is not None else ""
        is_enabled_raw = rec.get("isEnabled")
        is_visible_val = None
        if is_offscreen_raw is not None:
            is_visible_val = not bool(is_offscreen_raw)
        keyboard_focusable = rec.get("isKeyboardFocusable")

        path_segments = _path_segments_for(rec)

        inspect_data = {
            "name": name,
            "value": value_pattern_value,
            "toggleState": toggle_state,
            "controlType": control_type,
            "localizedControlType": "",
            "boundingRectangle": bounding_rectangle,
            "isEnabled": str(is_enabled_raw) if is_enabled_raw is not None else "",
            "isVisible": str(is_visible_val) if is_visible_val is not None else "",
            "isOffscreen": is_offscreen,
            "isKeyboardFocusable": str(keyboard_focusable) if keyboard_focusable is not None else "",
            "hasKeyboardFocus": "",
            "processId": process_id,
            "runtimeId": runtime_id,
            "frameworkId": "",
            "className": class_name,
            "automationId": automation_id,
            "nativeWindowHandle": handle,
            "providerDescription": "",
            "legacyName": "",
            "legacyRole": "",
            "legacyState": "",
            "helpText": help_text,
            "expandState": expand_state,
            "ancestors": list(path_segments[:-1]),
            "children": [],
        }
        full_ui_path = " > ".join(seg for seg in path_segments if seg)
        locator_method, locator_value, locator_score, locator_reason = build_locator_recommendation(
            inspect_data, index=index, ui_path=full_ui_path)
        inspect_data["recommendedTargetMethod"] = locator_method
        inspect_data["recommendedTargetValue"] = locator_value

        display_name = _build_display_name(inspect_data, "控件", index)
        raw_inspect_text = build_synthetic_inspect_text(inspect_data)

        flat_item = {
            "depth": depth,
            "index": index,
            "displayName": display_name,
            "windowTitle": root_title,
            "windowClassName": root_class,
            "processId": process_id,
            "handle": handle,
            "name": name,
            "className": class_name,
            "controlType": control_type,
            "localizedControlType": "",
            "automationId": automation_id,
            "frameworkId": "",
            "runtimeId": runtime_id,
            "value": value_pattern_value,
            "toggleState": toggle_state,
            # uia_tree_dumper 回填路径无 wrapper，无法探测 Pattern，置空默认保持输出 schema 一致
            "supportedPatterns": [],
            "expandCollapseState": "",
            "boundingRectangle": bounding_rectangle,
            "boundingBox": bounding_box,
            "isEnabled": bool(is_enabled_raw) if is_enabled_raw is not None else None,
            "isVisible": is_visible_val,
            "isOffscreen": is_offscreen,
            "helpText": help_text,
            "providerDescription": "",
            "locatorScore": locator_score,
            "locatorReason": locator_reason,
            "recommendedTargetMethod": locator_method,
            "recommendedTargetValue": locator_value,
            "uiPath": full_ui_path,
            "parentPath": " > ".join(seg for seg in path_segments[:-1] if seg),
            "auxChecks": build_aux_checks(inspect_data),
            "inspectData": inspect_data,
            "rawInspectText": raw_inspect_text,
        }

        identity = _build_flat_control_identity(flat_item)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        flat_controls.append(flat_item)
        added += 1

    if status_callback and added:
        status_callback(f"uia_tree_dumper 新增 {added} 个控件", len(flat_controls))
    return added


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
    scan_timeout_seconds=30,
    expand_dropdowns=False,
    status_callback=None,
):
    start_time = time.time()
    if status_callback:
        status_callback("开始扫描...", 0)
    
    backend = str(backend or "uia").strip().lower() or "uia"

    # UIA 性能优化：启用 RawViewWalker。
    # 默认的 ControlViewWalker 会在底层过滤掉很多非控件元素，这需要额外的 COM 计算时间。
    # 启用 RawViewWalker 会获取所有元素（速度极快），然后我们依赖后期的 _filter_noise_controls 在 Python 内存中过滤。
    if backend == "uia" and pywinauto:
        try:
            import pywinauto.windows.uia_element_info as uia_info
            uia_info.UIAElementInfo.use_raw_view_walker = True
        except Exception:
            pass

    max_depth = max(0, int(max_depth))

    # 整树采集（未指定画框区域）时自动放宽过滤条件：
    #   1. exclude_offscreen -> False：保留滚动区/折叠面板/未激活 Tab 中 isOffscreen 的控件；
    #   2. exclude_unidentified_containers -> False：保留无 name/automationId 的 WPF 布局容器；
    #   3. max_depth 提升到至少 FULLTREE_MIN_DEPTH：覆盖深层嵌套控件树。
    # 放宽后靠 _prune_empty_unidentified_containers 做保守剪枝，避免空壳容器淹没结果。
    fulltree_relaxation = None
    is_fulltree = _normalize_rect_dict(region_rect) is None
    if is_fulltree:
        fulltree_relaxation = {
            "excludeOffscreen": [bool(exclude_offscreen), False],
            "excludeUnidentifiedContainers": [bool(exclude_unidentified_containers), False],
            "maxDepth": [max_depth, max(max_depth, FULLTREE_MIN_DEPTH)],
        }
        exclude_offscreen = False
        exclude_unidentified_containers = False
        max_depth = max(max_depth, FULLTREE_MIN_DEPTH)

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
    use_raw_view_bfs = backend != "win32"
    if use_raw_view_bfs:
        try:
            # RawView BFS 遍历每个元素需多次 COM 调用，超时比常规 DFS 更宽松
            bfs_timeout = max(scan_timeout_seconds, 90)
            control_tree = _walk_raw_view_bfs(
                target_window_wrapper,
                max_depth=max_depth,
                target_window=target_window,
                flat_controls=flat_controls,
                path_segments=[root_display_name],
                start_time=start_time,
                scan_timeout_seconds=bfs_timeout,
                status_callback=status_callback,
            )
        except Exception:
            if status_callback:
                status_callback("RawView BFS 失败，降级为 _walk_wrapper...", 0)
            flat_controls.clear()
            use_raw_view_bfs = False
    if not use_raw_view_bfs:
        control_tree = _walk_wrapper(
            target_window_wrapper,
            depth=0,
            max_depth=max_depth,
            target_window=target_window,
            flat_controls=flat_controls,
            path_segments=[root_display_name],
            start_time=start_time,
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
        )
    # 树结构元数据增强 + 重建嵌套控件树（在全部采集与探针补采完成后统一执行，
    # 确保所有 flat_controls 条目都获得 pathHash / childCount / isTransparentContainer，
    # 且 controlsTree 包含探针补采的新条目）。
    # 注意：_enrich_tree_metadata 和 _build_tree_from_flat 延后到全部补采完成后执行。
    seen_identities = {_build_flat_control_identity(item) for item in flat_controls}
    _collect_region_probe_wrappers(
        backend,
        region_rect,
        target_window,
        flat_controls,
        seen_identities,
        root_handle=root_handle,
        max_depth=max_depth,
    )
    # 整树采集时借鉴画框采集的网格探针：对整个窗口矩形做 from_point 采样，
    # 补采纯树遍历漏掉的虚拟化/离屏控件。
    if is_fulltree:
        _collect_fulltree_probe_wrappers(
            backend,
            target_window,
            target_window_wrapper,
            flat_controls,
            seen_identities,
            root_handle=root_handle,
            max_depth=max_depth,
            start_time=start_time,
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
        )
        # 调用独立 .NET 工具 uia_tree_dumper，用 RawViewWalker 全量遍历 UIA 树，
        # 补采 pywinauto 因过滤/深度限制漏掉的控件（对齐 Inspect/Accessibility Insights）。
        _collect_uia_dumper_flat_controls(
            target_window,
            flat_controls,
            seen_identities,
            max_depth=max_depth,
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
        )
    # ---- 全部采集与补采完成，统一增强元数据并重建控件树 ----
    _enrich_tree_metadata(flat_controls)
    control_tree = _build_tree_from_flat(flat_controls)
    for item in flat_controls:
        item["scanBackend"] = backend
    # 在区域筛选前做 dict 级“标签→实际控件”横向关联（同行下拉框/输入框优先）。
    _associate_region_labels_with_controls(flat_controls)
    # 全局唯一性消歧：对共用同一定位器的不同控件追加 found_index，避免定位到同一处。
    _disambiguate_duplicate_locators(flat_controls)
    # 可选：自动展开区域内下拉框，采集其可选项（会真实操作界面，默认关闭）。
    dropdown_diagnostics = []
    if expand_dropdowns:
        dropdown_diagnostics = _expand_region_dropdowns(flat_controls, region_rect, backend)
    # 采集阶段结束，剥离临时 live 包装器引用，避免序列化失败与 COM 引用滞留。
    for item in flat_controls:
        item.pop("_wrapperRef", None)
    existing_ids = set()
    region_controls = _filter_flat_controls_by_region(flat_controls, region_rect)
    region_controls = _prune_low_value_region_controls(region_controls, target_window.get("title", ""))
    _enrich_flat_controls(region_controls, target_window)
    # 规范化 WPF TextBox 包装器：将 Custom/Pane+TextBox className 修正为 Edit，
    # 避免被后续 _filter_noise_controls 误删，并让 _backfill_label_text_to_controls 能关联标签。
    _normalize_textbox_wrappers(region_controls)
    # A+C: 过滤离屏控件和无标识容器
    region_controls = _filter_noise_controls(
        region_controls,
        exclude_offscreen=exclude_offscreen,
        exclude_unidentified_containers=exclude_unidentified_containers,
    )
    # 整树采集放宽过滤后，只保守剔除真正的空壳无标识容器，避免噪声淹没结果。
    if is_fulltree:
        region_controls = _prune_empty_unidentified_containers(region_controls)
    # 标签关联回填：对弱定位的 Edit/ComboBox 用邻近标签文本增强
    _backfill_label_text_to_controls(region_controls)
    for item in region_controls:
        item.pop("_wrapperIdentity", None)
    control_definitions = [_build_control_definition_from_flat(item, existing_ids) for item in region_controls]
    by_type = {}
    for item in region_controls:
        control_type = str(item.get("controlType", "")).strip() or "Unknown"
        by_type[control_type] = by_type.get(control_type, 0) + 1

    if status_callback:
        status_callback("扫描完成。", len(flat_controls))

    # 生成控件摘要（快速概览，不影响原始数据）
    control_summary = _build_control_summary(region_controls, flat_controls)

    return {
        "schemaVersion": "1.0",
        "scanMeta": {
            "scanTime": datetime.now().isoformat(timespec="seconds"),
            "backend": backend,
            "mode": "foreground" if use_foreground else "keyword",
            "windowKeyword": str(window_keyword or "").strip(),
            "maxDepth": max_depth,
            "scanTimeoutSeconds": scan_timeout_seconds,
            "totalControls": len(region_controls),
            "rawTotalControls": len(flat_controls),
            "regionRect": _normalize_rect_dict(region_rect),
            "controlTypeSummary": dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
            "dropdownExpandDiagnostics": dropdown_diagnostics,
            "fullTreeRelaxation": fulltree_relaxation,
        },
        "controlSummary": control_summary,
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
    scan_timeout_seconds=30,
    expand_dropdowns=False,
    status_callback=None,
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
                scan_timeout_seconds=scan_timeout_seconds,
                expand_dropdowns=expand_dropdowns,
                status_callback=status_callback,
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
                        scan_timeout_seconds=scan_timeout_seconds,
                        expand_dropdowns=expand_dropdowns,
                        status_callback=status_callback,
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
    _normalize_textbox_wrappers(merged_controls)
    _backfill_label_text_to_controls(merged_controls)
    existing_ids = set()
    merged_definitions = [_build_control_definition_from_flat(item, existing_ids) for item in merged_controls]
    by_type = {}
    for item in merged_controls:
        control_type = str(item.get("controlType", "")).strip() or "Unknown"
        by_type[control_type] = by_type.get(control_type, 0) + 1

    raw_total_controls = 0
    merged_backends = []
    merged_dropdown_diagnostics = []
    for payload in payloads:
        scan_meta = payload.get("scanMeta", {}) or {}
        raw_total_controls += int(scan_meta.get("rawTotalControls", 0) or 0)
        backend_name = str(scan_meta.get("backend", "")).strip()
        if backend_name and backend_name not in merged_backends:
            merged_backends.append(backend_name)
        for entry in scan_meta.get("dropdownExpandDiagnostics", []) or []:
            merged_dropdown_diagnostics.append(entry)

    # 单后端 payload 已在整树模式下放宽过滤并提升深度，合并元数据应反映实际生效值，
    # 而非未放宽的入参 max_depth。
    base_scan_meta = base_payload.get("scanMeta", {}) or {}
    effective_max_depth = base_scan_meta.get("maxDepth", max_depth)
    fulltree_relaxation = base_scan_meta.get("fullTreeRelaxation")

    return {
        "schemaVersion": "1.0",
        "scanMeta": {
            "scanTime": datetime.now().isoformat(timespec="seconds"),
            "backend": requested_backend,
            "requestedBackend": requested_backend,
            "mergedBackends": merged_backends,
            "mode": "foreground" if use_foreground else "keyword",
            "windowKeyword": str(window_keyword or "").strip(),
            "maxDepth": effective_max_depth,
            "totalControls": len(merged_controls),
            "rawTotalControls": raw_total_controls,
            "regionRect": _normalize_rect_dict(region_rect),
            "controlTypeSummary": dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
            "dropdownExpandDiagnostics": merged_dropdown_diagnostics,
            "fullTreeRelaxation": fulltree_relaxation,
        },
        "targetWindow": base_payload.get("targetWindow", {}),
        "controlsTree": base_payload.get("controlsTree", {}),
        "flatControls": merged_controls,
        "controlDefinitions": merged_definitions,
    }


def save_control_map_payload(payload, output_path=""):
    recordings_dir = os.path.join(CONTROL_MAP_DIR, "recordings")
    ensure_directory(recordings_dir)
    if not output_path:
        title = ((payload.get("targetWindow", {}) or {}).get("title", "") or "window").strip()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(recordings_dir, f"{timestamp}_{slugify_filename(title)}_control_map.json")
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
        # 全屏框选遮罩用真实屏幕像素，绕过 DPI 自动缩放，否则会超出屏幕、坐标错位
        wt_dpi.raw_geometry(
            self.window,
            f"{self.window.winfo_screenwidth()}x{self.window.winfo_screenheight()}+0+0",
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
        self.var_expand_dropdowns = tk.BooleanVar(value=False)
        self.var_status = tk.StringVar(value="准备就绪：建议先切到目标软件窗口，再点击“一键整树采集并保存”。")
        self.var_summary = tk.StringVar(value="尚未扫描控件树。")
        self.current_payload = None
        self.current_output_path = ""
        self.current_region_rect = None
        self.checked_control_indices = set()
        self._all_checked_mode = False
        self.control_groups = []
        self.var_saved_control_name = tk.StringVar(value="")
        self.var_saved_control_id = tk.StringVar(value="")
        self.var_scan_progress = tk.StringVar(value="")

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
        tk.Spinbox(toolbar, from_=0, to=40, textvariable=self.var_max_depth, width=6).grid(row=0, column=7, sticky="w")
        tk.Label(toolbar, text="画框延迟").grid(row=0, column=8, sticky="e", padx=(10, 4))
        tk.Spinbox(toolbar, from_=1, to=10, textvariable=self.var_pick_delay, width=6).grid(row=0, column=9, sticky="w")
        toolbar.columnconfigure(3, weight=1)

        filter_row = tk.Frame(toolbar)
        filter_row.grid(row=2, column=0, columnspan=10, sticky="w", pady=(6, 0))
        tk.Checkbutton(filter_row, text="过滤离屏控件", variable=self.var_exclude_offscreen).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(filter_row, text="过滤无标识容器", variable=self.var_exclude_unidentified).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(filter_row, text="自动展开下拉框采选项", variable=self.var_expand_dropdowns).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(filter_row, text="(勾选后采集时自动展开区域内下拉框读取可选项再收回，会真实操作界面，需目标软件处于可交互状态)", fg="#6b7280").pack(side=tk.LEFT)

        hint_row = tk.Frame(toolbar)
        hint_row.grid(row=3, column=0, columnspan=10, sticky="w", pady=(4, 0))
        tk.Label(
            hint_row,
            text=(
                "整树采集会自动放宽过滤：保留离屏控件与无标识容器、深度至少提升到 "
                f"{FULLTREE_MIN_DEPTH} 层，并对整窗做网格探针补采（上方过滤开关仅对画框采集生效）。"
            ),
            fg="#2563eb",
        ).pack(side=tk.LEFT)

        button_row = tk.Frame(toolbar)
        button_row.grid(row=1, column=0, columnspan=10, sticky="ew", pady=(10, 0))
        tk.Button(button_row, text="一键整树采集并保存", command=self.cmd_scan_and_save, bg="#d1fae5").pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="整树采集预览", command=self.cmd_scan_preview).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="画框区域采集并保存", command=self.cmd_region_scan_and_save, bg="#bfdbfe").pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="画框区域预览", command=self.cmd_region_scan_preview, bg="#bfdbfe").pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="智能勾选", command=self.cmd_smart_check_results).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="全选结果", command=self.cmd_check_all_results).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="清空勾选", command=self.cmd_clear_checked_results).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="保存当前结果", command=self.cmd_save_current_payload).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="打开控件库目录", command=self.cmd_open_control_map_dir).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="复制所选定位", command=self.cmd_copy_selected_locator).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row, text="检验定位", command=self.cmd_test_selected_locator, bg="#e0e7ff").pack(side=tk.LEFT, padx=3)
        self.var_tree_view_mode = tk.StringVar(value="flat")
        tk.Checkbutton(button_row, text="层级树视图", variable=self.var_tree_view_mode, onvalue="hierarchy", offvalue="flat", command=self._refresh_tree).pack(side=tk.LEFT, padx=8)
        tk.Label(button_row, textvariable=self.var_status, fg="#555555").pack(side=tk.RIGHT)
        tk.Label(button_row, textvariable=self.var_scan_progress, fg="#22c55e").pack(side=tk.RIGHT, padx=(10, 0))

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
            "expand_dropdowns": bool(self.var_expand_dropdowns.get()),
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
        args["status_callback"] = self._update_scan_progress
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
        self._all_checked_mode = False
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
        else:
            relaxation = scan_meta.get("fullTreeRelaxation")
            if isinstance(relaxation, dict):
                depth_pair = relaxation.get("maxDepth") or []
                depth_note = ""
                if isinstance(depth_pair, list) and len(depth_pair) == 2 and depth_pair[0] != depth_pair[1]:
                    depth_note = f"，深度 {depth_pair[0]}→{depth_pair[1]}"
                summary.append(f"整树模式：已自动放宽过滤（保留离屏控件/无标识容器{depth_note}）")
        if self.current_output_path:
            summary.append(f"保存路径：{self.current_output_path}")
        type_summary = scan_meta.get("controlTypeSummary", {}) or {}
        if type_summary:
            top_types = ", ".join(f"{key}={value}" for key, value in list(type_summary.items())[:8])
            summary.append(f"类型分布：{top_types}")
        self.var_summary.set("\n".join(summary))

    def _refresh_tree(self):
        """根据视图模式刷新控件树：扁平分组视图或层级树视图。"""
        self.control_tree.delete(*self.control_tree.get_children())
        if not isinstance(self.current_payload, dict):
            return
        mode = getattr(self, "var_tree_view_mode", tk.StringVar(value="flat")).get()
        if mode == "hierarchy":
            self._refresh_hierarchical_tree()
        else:
            self._refresh_flat_tree()

    def _refresh_flat_tree(self):
        """扁平分组视图：按控件组展示。"""
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

    def _refresh_hierarchical_tree(self):
        """层级树视图：展示真实父子关系。"""
        control_tree = self.current_payload.get("controlsTree", {}) if isinstance(self.current_payload, dict) else {}
        flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        if not control_tree:
            return
        # 递归插入树节点
        def insert_node(node, parent_iid="", depth=0):
            if not isinstance(node, dict):
                return
            # 构建显示名称
            name = str(node.get("name", "")).strip()
            control_type = str(node.get("controlType", "")).strip() or "Unknown"
            auto_id = str(node.get("automationId", "")).strip()
            display_name = name or auto_id or f"[{control_type}]"
            # 透明容器标记
            is_transparent = node.get("isTransparentContainer", False)
            prefix = "○" if is_transparent else "●"
            # 构建 iid
            node_index = node.get("flatIndex", -1)
            iid = f"hierarchy:{node_index}" if node_index >= 0 else f"hierarchy:{id(node)}"
            # 插入节点
            self.control_tree.insert(
                parent_iid,
                tk.END,
                iid=iid,
                open=(depth < 2),  # 前 2 层默认展开
                text=f"{prefix} {display_name}",
                values=(
                    f"{control_type}",
                    auto_id or "-",
                    name or "-",
                    node.get("pathHash", ""),
                    node.get("treeLevel", depth),
                ),
            )
            # 递归插入子节点
            children = node.get("children", [])
            for child in children:
                insert_node(child, iid, depth + 1)
        insert_node(control_tree)

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
            "automatabilityRisk": item.get("automatabilityRisk", ""),
            "automatabilityReasons": item.get("automatabilityReasons", []),
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

    def cmd_test_selected_locator(self):
        """将画框采集结果中的当前控件交给统一定位检验器。"""
        index = self._get_selected_tree_index()
        flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        if index is None or not (0 <= index < len(flat_controls)):
            messagebox.showinfo("提示", "请先选择一个具体控件或控件组。", parent=self.root)
            return

        item = flat_controls[index]
        control_definition = {}
        definitions = self.current_payload.get("controlDefinitions", []) if isinstance(self.current_payload, dict) else []
        if index < len(definitions) and isinstance(definitions[index], dict):
            control_definition = dict(definitions[index])

        control = dict(item)
        control.update({key: value for key, value in control_definition.items() if value not in (None, "")})
        inspect_data = control.get("inspectData") if isinstance(control.get("inspectData"), dict) else {}
        control["name"] = (
            str(control.get("name", "")).strip()
            or str(control.get("displayName", "")).strip()
            or str(inspect_data.get("name", "")).strip()
        )
        control["targetMethod"] = (
            str(control.get("targetMethod", "")).strip()
            or str(control.get("recommendedTargetMethod", "")).strip()
            or str(inspect_data.get("recommendedTargetMethod", "")).strip()
        )
        control["targetValue"] = (
            str(control.get("targetValue", "")).strip()
            or str(control.get("recommendedTargetValue", "")).strip()
            or str(inspect_data.get("recommendedTargetValue", "")).strip()
        )
        target_window = self.current_payload.get("targetWindow", {}) if isinstance(self.current_payload, dict) else {}
        if not control.get("windowTitle") and isinstance(target_window, dict):
            control["windowTitle"] = str(target_window.get("title", "")).strip()

        try:
            import WT_Flow_Editor
            dialog = WT_Flow_Editor.ControlLocatorTesterDialog(self.root, initial_control=control)
            self.root.wait_window(dialog.window)
        except Exception as exc:
            messagebox.showerror("打开失败", f"无法打开定位检验器：\n{exc}", parent=self.root)

    def cmd_scan_and_save(self):
        self._start_fulltree_scan(auto_save=True)

    def cmd_scan_preview(self):
        self._start_fulltree_scan(auto_save=False)

    def _start_fulltree_scan(self, auto_save):
        """整树采集入口：前台窗口模式下先捕获当前前台窗口标题，
        再最小化自身，避免 GetForegroundWindow 锁到采集工具自己或水印窗口。"""
        if self.var_scan_mode.get().strip() == "foreground":
            # 最小化前先捕获当前前台窗口标题
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            captured_title = buf.value.strip()
            # 排除自身和水印窗口
            if captured_title and captured_title not in ("WT 控件库采集器", "window"):
                self._captured_foreground_title = captured_title
                self.var_status.set(f"已捕获目标窗口：{captured_title}，采集工具最小化中…")
            else:
                self._captured_foreground_title = ""
                self.var_status.set("采集工具已最小化，目标窗口即将成为前台，0.6 秒后开始采集…")
            self.root.iconify()
            self.root.update()
            self.root.after(600, lambda: self._run_scan_foreground(auto_save))
        else:
            self._captured_foreground_title = ""
            self._run_scan(auto_save=auto_save)

    def _run_scan_foreground(self, auto_save):
        """延迟后执行扫描，完成后恢复窗口与原始采集模式。"""
        original_mode = self.var_scan_mode.get().strip()
        captured = getattr(self, "_captured_foreground_title", "")
        if captured:
            self.var_scan_mode.set("keyword")
            self.var_window_keyword.set(captured)
        try:
            self._run_scan(auto_save=auto_save)
        finally:
            # 即使扫描异常也要恢复原始模式
            if captured:
                self.var_scan_mode.set(original_mode)
                self.var_window_keyword.set("")
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

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
        self._update_scan_progress("开始区域扫描", 0)
        time.sleep(0.2)
        self._run_scan(auto_save=auto_save, region_rect=rect)
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _update_scan_progress(self, message, count):
        self.var_scan_progress.set(f"{message} (已采集 {count} 个)")
        self.root.update_idletasks()

    def cmd_region_scan_and_save(self):
        self._start_region_pick(auto_save=True)

    def cmd_region_scan_preview(self):
        self._start_region_pick(auto_save=False)

    def cmd_check_all_results(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        self.checked_control_indices = set(range(len(self.current_payload.get("flatControls", []) or [])))
        self._all_checked_mode = True
        self._refresh_tree()
        self._refresh_summary()
        self.var_status.set("已全选当前扫描结果（保存时不去重）。")

    def cmd_smart_check_results(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        self._all_checked_mode = False
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
        self._all_checked_mode = False
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

        # 全选模式：保留所有勾选项，不做组内去重
        if getattr(self, "_all_checked_mode", False):
            deduplicated_indexes = selected_indexes
        else:
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
        existing_ids = set()
        normalized_definitions = []
        # 逐个勾选控件保证 1:1 生成定义，任何缺失对应定义的控件都就地补建，绝不丢弃。
        for position, index in enumerate(deduplicated_indexes):
            flat_item = flat_controls[index]
            if index < len(control_definitions) and isinstance(control_definitions[index], dict):
                item = control_definitions[index]
            else:
                item = _build_control_definition_from_flat(flat_item, set())
            current_name = (
                str(item.get("name", "")).strip()
                or str(flat_item.get("savedControlName", "")).strip()
                or str(flat_item.get("suggestedControlName", "")).strip()
                or str(flat_item.get("displayName", "")).strip()
                or f"控件 {position + 1}"
            )
            item["name"] = current_name
            item["id"] = _ensure_unique_control_id(
                {"id": str(item.get("id", "")).strip() or _build_saved_control_id_from_name(current_name, fallback=f"control_{position + 1}")},
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
        initial_dir = os.path.join(CONTROL_MAP_DIR, "recordings") if os.path.exists(os.path.join(CONTROL_MAP_DIR, "recordings")) else BASE_DIR
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

    wt_dpi.enable_process_dpi_awareness()
    root = tk.Tk()
    wt_dpi.compute_scale(root)
    ControlMapBuilderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
