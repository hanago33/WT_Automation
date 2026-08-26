# encoding: utf-8

import ctypes
from ctypes import wintypes
import gc
import json
import os
import re
import threading
import time
import traceback

import pyautogui
from pywinauto import Desktop
from pywinauto_recorder.player import send_keys

try:
    from fuzzywuzzy import fuzz
except ImportError:
    fuzz = None

from wt_flow_editor_utils import uipath_is_main_window_root


FLOW_WINDOW_CACHE_TTL_SECONDS = 60.0
FLOW_CONTROL_CACHE_TTL_SECONDS = 12.0
FLOW_PARENT_CACHE_TTL_SECONDS = 20.0
FLOW_UIPI_BLOCK_CACHE_TTL_SECONDS = 3.0

FLOW_WINDOW_CACHE = {}
FLOW_CONTROL_CACHE = {}
FLOW_PARENT_CACHE = {}
_UIPI_BLOCK_CACHE = {}
_UIPI_BLOCK_DETECTED = {"timestamp": 0.0, "diagnostic": None}
# UIPI 锁存有效时长：一次误检（UIA 瞬时枚举失败/辅助进程窗口）不应影响后续所有迭代
_UIPI_BLOCK_TTL_SECONDS = 30.0


def _uipi_block_active(marker_before):
    """UIPI 锁存是否仍有效（TTL 内且晚于本轮 marker），有效返回 diagnostic dict，否则 None。"""
    entry = _UIPI_BLOCK_DETECTED
    ts = float(entry.get("timestamp", 0.0) or 0.0)
    if ts <= float(marker_before or 0.0):
        return None
    if time.time() - ts > _UIPI_BLOCK_TTL_SECONDS:
        return None
    return entry.get("diagnostic") or {}
_control_map_cache = {}

_GET_STEP_DEFINITION = lambda step_id: {}
_LOG_STEP = lambda message: None
# 目标软件主窗口候选提供者：运行时注入（如 WT_AUT_recorded 用 find_main_windows 按进程名
# 找 MUPSmartClient 主窗）。fallback 用它的 hwnd 包装成 UIA wrapper，比枚举解析进程名可靠。
_GET_MAIN_WINDOW_CANDIDATES = lambda: []


# #region debug-point fan-type-create-error:report

_DEBUG_EVENTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dbg")


def _debug_events_enabled():
    """调试事件默认关闭，设置 WT_DEBUG_EVENTS=1 时恢复 .dbg/*.ndjson 写入。"""
    return os.environ.get("WT_DEBUG_EVENTS") == "1"


def _write_ndjson_debug_event(debug_file, session_id, hypothesis_id, location, msg, data=None, run_id="post-fix-v3", prefix_debug=True):
    if not _debug_events_enabled():
        return
    try:
        os.makedirs(os.path.dirname(debug_file), exist_ok=True)
        text = str(msg)
        if prefix_debug:
            text = "[DEBUG] " + text
        with open(debug_file, "a", encoding="utf-8") as file_obj:
            file_obj.write(
                json.dumps(
                    {
                        "sessionId": session_id,
                        "runId": run_id,
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": text,
                        "data": data or {},
                        "ts": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def _emit_fan_type_create_debug_event(hypothesis_id, location, msg, data=None):
    _write_ndjson_debug_event(
        os.path.join(_DEBUG_EVENTS_DIR, "trae-debug-log-fan-type-create-error.ndjson"),
        "fan-type-create-error",
        hypothesis_id,
        location,
        msg,
        data=data,
        run_id="pre-fix",
    )


def _emit_debug_event(hypothesis_id, location, msg, data=None):
    _write_ndjson_debug_event(
        os.path.join(_DEBUG_EVENTS_DIR, "trae-debug-log-private-group-click.ndjson"),
        "private-group-click",
        hypothesis_id,
        location,
        msg,
        data=data,
        prefix_debug=False,
    )


def _emit_time_series_debug_event(hypothesis_id, location, msg, data=None):
    _write_ndjson_debug_event(
        os.path.join(_DEBUG_EVENTS_DIR, "trae-debug-log-time-series-path-input.ndjson"),
        "time-series-path-input",
        hypothesis_id,
        location,
        msg,
        data=data,
        run_id="pre-fix",
    )


def _emit_post_type_click_debug_event(hypothesis_id, location, msg, data=None):
    _write_ndjson_debug_event(
        os.path.join(_DEBUG_EVENTS_DIR, "trae-debug-log-post-type-click-failure.ndjson"),
        "post-type-click-failure",
        hypothesis_id,
        location,
        msg,
        data=data,
        run_id="pre-fix",
    )


def _emit_default_height_debug_event(hypothesis_id, location, msg, data=None):
    _write_ndjson_debug_event(
        os.path.join(_DEBUG_EVENTS_DIR, "trae-debug-log-default-height-relative-input.ndjson"),
        "default-height-relative-input",
        hypothesis_id,
        location,
        msg,
        data=data,
        run_id="pre-fix",
    )


def _emit_add_data_false_hit_debug_event(hypothesis_id, location, msg, data=None):
    _write_ndjson_debug_event(
        os.path.join(_DEBUG_EVENTS_DIR, "trae-debug-log-add-data-false-hit.ndjson"),
        "add-data-false-hit",
        hypothesis_id,
        location,
        msg,
        data=data,
        run_id="pre-fix",
    )


def _emit_start_validation_regression_debug_event(hypothesis_id, location, msg, data=None):
    _write_ndjson_debug_event(
        os.path.join(_DEBUG_EVENTS_DIR, "trae-debug-log-start-validation-regression.ndjson"),
        "start-validation-regression",
        hypothesis_id,
        location,
        msg,
        data=data,
        run_id="pre-fix",
    )


def _emit_relative_region_rect_trace(step_id, location, msg, data=None):
    step_id = str(step_id or "").strip()
    if step_id == "step_16":
        _emit_default_height_debug_event("RECT", location, msg, data)
    elif step_id == "step_26":
        _emit_start_validation_regression_debug_event("RECT", location, msg, data)


def _emit_step37_add_data_miss_debug_event(hypothesis_id, location, msg, data=None):
    _write_ndjson_debug_event(
        os.path.join(_DEBUG_EVENTS_DIR, "trae-debug-log-step37-add-data-miss.ndjson"),
        "step37-add-data-miss",
        hypothesis_id,
        location,
        msg,
        data=data,
        run_id="pre-fix",
    )


# #endregion

_SILENT_EXCEPTION_COUNTS = {}
_SILENT_EXCEPTION_LOCK = threading.Lock()


def _record_silent_exception(phase, exc=None):
    with _SILENT_EXCEPTION_LOCK:
        _SILENT_EXCEPTION_COUNTS[phase] = _SILENT_EXCEPTION_COUNTS.get(phase, 0) + 1


def _reset_silent_exception_counts():
    with _SILENT_EXCEPTION_LOCK:
        _SILENT_EXCEPTION_COUNTS.clear()


def _snapshot_silent_exception_counts():
    with _SILENT_EXCEPTION_LOCK:
        return dict(_SILENT_EXCEPTION_COUNTS)


def configure_flow_locator(get_step_definition=None, log_step=None, get_main_window_candidates=None):
    global _GET_STEP_DEFINITION, _LOG_STEP, _GET_MAIN_WINDOW_CANDIDATES
    if callable(get_step_definition):
        _GET_STEP_DEFINITION = get_step_definition
    if callable(log_step):
        _LOG_STEP = log_step
    if callable(get_main_window_candidates):
        _GET_MAIN_WINDOW_CANDIDATES = get_main_window_candidates


# #region self-healing selector (#4)
# 自愈式选择器：当控件主定位器（如 automationId）在当前界面失效时，自动降级到
# 次级候选（name / class_name 等）命中，并把"实际生效的定位器"持久化学习下来，
# 后续运行直接将其作为首选，避免反复走弱匹配。同时记录自愈事件便于回查修正控件库。

SELF_HEAL_ENABLED = True
SELF_HEAL_STORE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "control_maps", "self_heal_store.json"
)
_self_heal_overrides = {}  # key "step_id|control_id" -> {"method","value","score"}
_self_heal_session_hits = []  # 本次运行的全部自愈事件


def configure_self_heal(enabled=None, store_path=None):
    """配置自愈：enabled 开关，store_path 改存储路径（同时重载已学习覆盖）。"""
    global SELF_HEAL_ENABLED, SELF_HEAL_STORE_PATH, _self_heal_overrides
    if enabled is not None:
        SELF_HEAL_ENABLED = bool(enabled)
    if store_path:
        SELF_HEAL_STORE_PATH = store_path
        _self_heal_overrides = _load_self_heal_store()


def _self_heal_key(step_id, control_id):
    return f"{step_id}|{control_id or ''}"


def _load_self_heal_store():
    try:
        with open(SELF_HEAL_STORE_PATH, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        if isinstance(data, dict) and isinstance(data.get("overrides"), dict):
            return data["overrides"]
    except Exception:
        pass
    return {}


# 自愈存储写盘锁：守护线程（相对点击/悬停补采）与主线程并发都会读写 _self_heal_overrides，
# 不加锁时 json 序列化迭代中 dict 被改会抛 RuntimeError（被吞后静默丢数据）
_SELF_HEAL_LOCK = threading.Lock()


def save_self_heal_store():
    try:
        with _SELF_HEAL_LOCK:
            parent_dir = os.path.dirname(SELF_HEAL_STORE_PATH) or "."
            os.makedirs(parent_dir, exist_ok=True)
            snapshot = dict(_self_heal_overrides)
        # 持锁期间只做快照复制，串行化开销最小化，写盘在锁外进行
        with open(SELF_HEAL_STORE_PATH, "w", encoding="utf-8") as file_obj:
            json.dump(
                {"overrides": snapshot, "updatedAt": int(time.time())},
                file_obj,
                ensure_ascii=False,
                indent=2,
            )
    except Exception:
        pass


def get_self_heal_override(step_id, control_id):
    return _self_heal_overrides.get(_self_heal_key(step_id, control_id))


def record_self_heal(step_id, control_id, method, value, score=None):
    """把实际生效的定位器记录下来（覆盖式学习）。"""
    if not SELF_HEAL_ENABLED:
        return
    with _SELF_HEAL_LOCK:
        _self_heal_overrides[_self_heal_key(step_id, control_id)] = {
            "method": method,
            "value": value,
            "score": score,
        }
    save_self_heal_store()


def get_self_heal_report():
    """返回本次运行捕获到的全部自愈事件（供编辑器/总控台回显）。"""
    return list(_self_heal_session_hits)


def self_heal_summary():
    return {
        "enabled": SELF_HEAL_ENABLED,
        "storePath": SELF_HEAL_STORE_PATH,
        "learnedCount": len(_self_heal_overrides),
        "sessionHits": list(_self_heal_session_hits),
    }


def detect_healed_locator(wrapper, control_definition):
    """找出 wrapper 实际命中的、优先级最高的候选定位器。

    返回 {"method","value","priority"} 或 None（完全不命中）。
    priority==0 表示命中主定位器（非自愈）；priority>0 表示降级自愈命中。
    """
    if wrapper is None:
        return None
    candidates = build_common_locator_candidates(control_definition)
    for priority, (method, value) in enumerate(candidates):
        if wrapper_matches_locator(wrapper, method, value):
            return {"method": method, "value": value, "priority": priority}
    return None


def _apply_self_heal_override(step_id, control_id, control_definition):
    """若存在已学习的覆盖，把其定位器写入 control_definition 的 targetMethod/targetValue，
    使 build_common_locator_candidates 将其排在 priority 0，作为首选定位器。"""
    if not SELF_HEAL_ENABLED:
        return control_definition
    override = get_self_heal_override(step_id, control_id)
    if not override:
        return control_definition
    method = override.get("method", "")
    value = override.get("value", "")
    if not method or not value:
        return control_definition
    cloned = dict(control_definition) if isinstance(control_definition, dict) else {}
    cloned["targetMethod"] = method
    cloned["targetValue"] = value
    return cloned


def _maybe_report_self_heal(step_id, control_id, wrapper, controls):
    """当命中属于降级自愈（priority>0）时，记录学习覆盖 + 写日志 + 记入本次会话。"""
    if not SELF_HEAL_ENABLED or wrapper is None or not controls:
        return
    healed = detect_healed_locator(wrapper, controls[0])
    if healed is None or healed.get("priority", 0) <= 0:
        return
    record_self_heal(step_id, control_id, healed["method"], healed["value"])
    event = {
        "stepId": step_id,
        "controlId": control_id or "(first)",
        "method": healed["method"],
        "value": healed["value"],
        "priority": healed["priority"],
    }
    _self_heal_session_hits.append(event)
    _LOG_STEP(
        f"[自愈] 控件定位已降级自愈: step={step_id}, control={control_id or '(first)'}, "
        f"通过候选={healed['method']}={healed['value']}"
    )


# #endregion self-healing selector (#4)


def _safe_get_value(getter, default=""):
    try:
        value = getter()
    except Exception:
        return default
    return default if value is None else value


def normalize_control_type_name(control_type, localized_control_type=""):
    control_type = str(control_type or "").strip()
    if control_type.startswith("UIA_") and "ControlTypeId" in control_type:
        control_type = control_type.replace("UIA_", "").replace("ControlTypeId", "").strip()
    if control_type:
        return control_type
    return str(localized_control_type or "").strip()


def strip_wrapping_quotes(text):
    value = str(text or "").strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return value


def normalize_match_text(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower() in {"property does not exist", "[null]", "none", "null"}:
        return ""
    return strip_wrapping_quotes(text)


def normalize_control_definition(control_definition):
    source = control_definition if isinstance(control_definition, dict) else {}
    normalized = dict(source)
    inspect_data = source.get("inspectData", {})
    inspect_data = dict(inspect_data) if isinstance(inspect_data, dict) else {}
    field_map = {
        "name": ("name", "displayName"),
        "controlType": ("controlType",),
        "localizedControlType": ("localizedControlType",),
        "className": ("className",),
        "automationId": ("automationId",),
        "frameworkId": ("frameworkId",),
        "processId": ("processId",),
        "runtimeId": ("runtimeId",),
        "isControlElement": ("isControlElement",),
        "isContentElement": ("isContentElement",),
        "isOffscreen": ("isOffscreen",),
        "isEnabled": ("isEnabled",),
        "boundingRectangle": ("boundingRectangle",),
        "foundIndex": ("foundIndex", "siblingsIndex"),
        "ancestors": ("ancestors",),
        "children": ("children",),
    }
    for inspect_key, source_keys in field_map.items():
        if inspect_data.get(inspect_key) not in (None, "", []):
            continue
        for source_key in source_keys:
            value = source.get(source_key)
            if value not in (None, "", []):
                inspect_data[inspect_key] = value
                break
    normalized["inspectData"] = inspect_data
    normalized["name"] = normalize_match_text(
        source.get("name", "") or source.get("displayName", "") or inspect_data.get("name", "")
    )
    normalized["targetMethod"] = normalize_match_text(
        source.get("targetMethod", "")
        or source.get("recommendedTargetMethod", "")
        or inspect_data.get("recommendedTargetMethod", "")
    )
    normalized["targetValue"] = normalize_match_text(
        source.get("targetValue", "")
        or source.get("recommendedTargetValue", "")
        or inspect_data.get("recommendedTargetValue", "")
    )
    normalized["uiPath"] = normalize_match_text(source.get("uiPath", "") or inspect_data.get("uiPath", ""))
    window_title = normalize_match_text(source.get("windowTitle", ""))
    if uipath_is_main_window_root(normalized["uiPath"]):
        # 主窗口根路径内录制的 windowTitle 常是控件库分类名伪标题，运行时不再约束
        window_title = "*"
    normalized["windowTitle"] = window_title
    return normalized


def build_locator_text(method, values):
    if isinstance(values, str):
        values = [values]
    return str(method or "").strip(), ",".join(
        normalize_match_text(item) for item in values if normalize_match_text(item)
    )


def split_locator_parts(text):
    parts = []
    buffer = []
    index = 0
    text = str(text or "")
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == ",":
            buffer.append(",")
            index += 2
            continue
        if char == ",":
            parts.append("".join(buffer).strip())
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    parts.append("".join(buffer).strip())
    return [normalize_match_text(item) for item in parts if normalize_match_text(item)]


def is_generic_locator_class_name(value):
    return normalize_match_text(value) in {
        "button",
        "edit",
        "pane",
        "window",
        "text",
        "image",
        "custom",
        "list",
        "listitem",
        "menuitem",
        "tabitem",
        "group",
        "checkbox",
        "radiobutton",
        "combobox",
        "hyperlink",
        "treeitem",
        "datagridcell",
    }


def parse_aux_check_line(line):
    text = str(line or "").strip()
    if not text or "=" not in text:
        return "", ""
    key, value = text.split("=", 1)
    return key.strip(), normalize_match_text(value)


def parse_window_title_candidates(window_title):
    text = str(window_title or "").strip()
    if not text:
        return []
    candidates = []
    for item in text.split("/"):
        current = normalize_match_text(item)
        if current:
            candidates.append(current)
    return candidates


def get_wrapper_text(wrapper):
    return normalize_match_text(_safe_get_value(lambda: wrapper.window_text(), ""))


def get_wrapper_class_name(wrapper):
    return normalize_match_text(_safe_get_value(lambda: wrapper.class_name(), ""))


def get_wrapper_control_type(wrapper):
    return normalize_control_type_name(
        _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
    )


def get_wrapper_localized_control_type(wrapper):
    return normalize_match_text(
        _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), "")
    )


def get_wrapper_automation_id(wrapper):
    return normalize_match_text(_safe_get_value(lambda: getattr(wrapper.element_info, "automation_id", ""), ""))


def get_wrapper_framework_id(wrapper):
    return normalize_match_text(_safe_get_value(lambda: getattr(wrapper.element_info, "framework_id", ""), ""))


def get_wrapper_help_text(wrapper):
    return normalize_match_text(_safe_get_value(lambda: getattr(wrapper.element_info, "help_text", ""), ""))


def get_wrapper_process_id(wrapper):
    return normalize_match_text(_safe_get_value(lambda: getattr(wrapper.element_info, "process_id", ""), ""))


def is_automation_window(wrapper):
    process_id = get_wrapper_process_id(wrapper)
    current_pid = str(os.getpid())
    if process_id and process_id == current_pid:
        return True
    title = normalize_match_text(get_wrapper_text(wrapper)).lower()
    class_name = normalize_match_text(get_wrapper_class_name(wrapper)).lower()
    if title in {
        "gm自动化流程监视器",
        "wt自动化流程监视器",
        "wt自动化总控台",
        "wt 自动化项目总控台",
        "wt流程编辑器",
    }:
        return True
    if class_name in {"tk", "tktoplevel"} and title:
        return True
    return False


def get_wrapper_handle_text(wrapper):
    handle = _safe_get_value(lambda: getattr(wrapper.element_info, "handle", ""), "")
    if isinstance(handle, int) and handle:
        return hex(handle)
    return normalize_match_text(handle)


def get_wrapper_is_enabled(wrapper):
    return str(bool(_safe_get_value(lambda: wrapper.is_enabled(), False)))


def get_wrapper_is_offscreen(wrapper):
    return str(not bool(_safe_get_value(lambda: wrapper.is_visible(), True)))


def get_wrapper_is_keyboard_focusable(wrapper):
    return str(bool(_safe_get_value(lambda: getattr(wrapper.element_info, "is_keyboard_focusable", False), False)))


def get_wrapper_has_keyboard_focus(wrapper):
    return str(bool(_safe_get_value(lambda: getattr(wrapper.element_info, "has_keyboard_focus", False), False)))


def get_wrapper_parent_signatures(wrapper, depth=6):
    signatures = []
    current = wrapper
    for _ in range(depth):
        current = _safe_get_value(lambda: current.parent(), None)
        if current is None:
            break
        name = get_wrapper_text(current)
        class_name = get_wrapper_class_name(current)
        control_type = get_wrapper_control_type(current)
        signature = " | ".join(item for item in [name, class_name, control_type] if item)
        if signature:
            signatures.append(signature)
    return signatures


def _is_same_wrapper(left, right):
    """判断两个 wrapper 是否指向同一控件：优先对象同一，其次 runtime_id / 矩形。"""
    if left is right:
        return True
    if left is None or right is None:
        return False
    left_rt = _safe_get_value(lambda: getattr(left.element_info, "runtime_id", ""), "")
    right_rt = _safe_get_value(lambda: getattr(right.element_info, "runtime_id", ""), "")
    if left_rt and right_rt and left_rt == right_rt:
        return True
    left_rect = get_wrapper_rectangle(left)
    right_rect = get_wrapper_rectangle(right)
    if left_rect and right_rect and left_rect == right_rect:
        return True
    return False


_LABEL_COMPANION_CONTROL_TYPES = {"Edit", "ComboBox", "Pane", "Custom"}
_TAB_NAV_IN_PROGRESS = set()
# Tab 起点被替换为锚点附近可聚焦控件时，与录制配置(steps)的步数偏移未知，
# 将上限放宽为该缓冲值 + 配置步数，由评分命中(>=70)提前返回与焦点环检测兜底。
_TAB_NAV_START_OFFSET_BUFFER = 6


def _read_wrapper_labeled_by_name(wrapper):
    value = _safe_get_value(lambda: getattr(wrapper.element_info, "labeled_by", ""), "")
    if not value:
        value = _safe_get_value(lambda: getattr(wrapper.element_info, "label_name", ""), "")
    return normalize_match_text(value)


def _label_rect_matches_control(label_rect, control_rect):
    if not label_rect or not control_rect:
        return False

    def _num(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    label_left = _num(label_rect.get("left"))
    label_top = _num(label_rect.get("top"))
    label_right = _num(label_rect.get("right"))
    label_bottom = _num(label_rect.get("bottom"))
    control_left = _num(control_rect.get("left"))
    control_top = _num(control_rect.get("top"))
    control_right = _num(control_rect.get("right"))
    control_bottom = _num(control_rect.get("bottom"))
    if label_right <= label_left or label_bottom <= label_top:
        return False
    if control_right <= control_left or control_bottom <= control_top:
        return False

    label_height = label_bottom - label_top
    control_height = control_bottom - control_top
    vertical_overlap = min(label_bottom, control_bottom) - max(label_top, control_top)
    if vertical_overlap > 0:
        min_height = max(1.0, min(label_height, control_height))
        if vertical_overlap / min_height >= 0.5:
            gap = control_left - label_right
            if -20.0 <= gap <= 420.0:
                return True

    label_width = label_right - label_left
    control_width = control_right - control_left
    horizontal_overlap = min(label_right, control_right) - max(label_left, control_left)
    if horizontal_overlap > 0:
        min_width = max(1.0, min(label_width, control_width))
        if horizontal_overlap / min_width >= 0.3:
            gap_below = control_top - label_bottom
            if 0.0 <= gap_below <= 120.0:
                return True
    return False


_LABEL_RECT_CACHE = threading.local()

_LABEL_RECT_CACHE_TTL = 30.0  # label 矩形缓存软失效时长（秒）

def _label_rect_cache_reset():
    """硬清空 label 矩形缓存（测试隔离 / 明确需要失效时调用）。

    生产路径 find_flow_control 不再每次调用都硬清空（改由 TTL 自动过期），
    使同一目标窗口在几十秒内的连续步骤共享一次全树 label 扫描结果，
    避免每个步骤都重复支付 20-36 秒的全树扫描。
    """
    _LABEL_RECT_CACHE.rects = {}


def _label_rect_cache_get(top_window, label_text):
    cache = getattr(_LABEL_RECT_CACHE, "rects", None)
    if cache is None:
        return None
    handle = _safe_get_value(lambda: get_wrapper_handle(top_window), 0) or id(top_window)
    entry = cache.get((handle, label_text))
    if entry is None:
        return None
    ts, rects = entry
    if time.time() - ts > _LABEL_RECT_CACHE_TTL:
        cache.pop((handle, label_text), None)
        return None
    return list(rects)


def _label_rect_cache_put(top_window, label_text, rects):
    cache = getattr(_LABEL_RECT_CACHE, "rects", None)
    if cache is None:
        cache = {}
        _LABEL_RECT_CACHE.rects = cache
    handle = _safe_get_value(lambda: get_wrapper_handle(top_window), 0) or id(top_window)
    if len(cache) < 64:  # 有界，防异常路径缓存无限膨胀
        cache[(handle, label_text)] = (time.time(), list(rects or []))


def _find_label_rects_for_wrapper(wrapper, label_text):
    expected = normalize_match_text(label_text)
    if not expected:
        return []
    rects = []
    try:
        scopes = []
        parent = _safe_get_value(lambda: wrapper.parent(), None)
        if parent is not None:
            scopes.append(parent)
        top_window = get_wrapper_top_level_window(wrapper)
        if top_window is not None and not _is_same_wrapper(top_window, parent):
            scopes.append(top_window)
        # 缓存命中：同窗口下此前已扫描过该标签的全部矩形，直接复用。
        # （parent 子树是 top_window 子树的子集，按 top_window 缓存不遗漏、不重复。）
        cached = _label_rect_cache_get(top_window, expected)
        if cached is not None:
            return list(cached)
        seen_rects = set()
        # 临时关闭 GC：pywinauto/comtypes 的 UIA 元素数组在垃圾回收时释放 COM
        # 对象（__del__ → Release），若目标软件 UIA provider 已失效会触发
        # "Windows fatal exception: access violation"（段错误）导致整个子进程崩溃。
        # 遍历期间禁用 GC，避免 COM 对象在遍历中途被回收，遍历结束后恢复。
        gc_was_enabled = gc.isenabled()
        try:
            if gc_was_enabled:
                gc.disable()
            for scope in scopes:
                for candidate in scope.descendants():
                    if get_wrapper_control_type(candidate) not in {"Text", "Static", "Label", "Document"}:
                        continue
                    if normalize_match_text(get_wrapper_text(candidate)) != expected:
                        continue
                    rect = get_wrapper_rectangle(candidate)
                    if not rect:
                        continue
                    rect_key = (
                        int(rect.get("left", 0)),
                        int(rect.get("top", 0)),
                        int(rect.get("right", 0)),
                        int(rect.get("bottom", 0)),
                    )
                    if rect_key in seen_rects:
                        continue
                    seen_rects.add(rect_key)
                    rects.append(rect)
        finally:
            if gc_was_enabled:
                gc.enable()
        _label_rect_cache_put(top_window, expected, rects)
    except Exception as exc:
        _record_silent_exception("find_label_rects", exc)
    return rects


def wrapper_matches_label_text(wrapper, label_text, allow_full_scan=True):
    expected = normalize_match_text(label_text)
    if not expected:
        return False
    # 控件自身文本/名称即标签（如按钮文本"地形"）：采集端 label_text 消歧
    # 常把控件自身 name 作为标签值（按钮 name 即按钮文字）。
    if normalize_match_text(get_wrapper_text(wrapper)) == expected:
        return True
    labeled_by = _read_wrapper_labeled_by_name(wrapper)
    if labeled_by:
        return value_matches(labeled_by, expected)
    # 廉价兄弟 TextBlock 匹配优先：WPF 中"标签/面板标题 + 控件"常为同层兄弟
    # （interest-area 面板"测风点"标题与各图标按钮、输入框旁的单位/名称标签）。
    # 大多数场景在此命中，可避免 _find_label_rects_for_wrapper 的全树扫描
    # （巨大 WPF 窗口首次扫描实测 20-36 秒，是每步定位耗时主因）。
    if _match_sibling_text_block_label(wrapper, expected):
        return True
    # Telerik/WPF 多选下拉 CheckBox 的等级文本在其子 TextBlock 上，而非自身 Name
    # （如热稳定度『2 - 中性』的 MTDGroupComboBoxMultiSelection_CheckBox：自身 name
    # 为空，父 ListItem 名才是选项文本，子节点 TextBlock 保存展示文本）。
    # 仅靠 automation_id 会命中全部 10 个等级 checkbox，靠 label_text 匹配子文本
    # 才能精确锁定目标等级，避免勾选到同下拉框下其它选项。
    if _match_child_text_block_label(wrapper, expected):
        return True
    # 全树 label 矩形扫描非常昂贵（巨大 WPF 窗口首次扫描 20-36s）。仅当
    # targetMethod 明确含 label_text（硬性消歧，必须靠 label 区分同 automationId
    # 控件）时才允许全树扫描；软加分路径（如 step_2 创建综合按钮 labelText=综合2
    # 但 targetMethod=automation_id,control_type）不应触发——label 只是 +12 加分，
    # 缺它 score 依然 >=100，全树扫描纯属浪费，是 step_2 定位卡 25s 的根因。
    if not allow_full_scan:
        return False
    control_rect = get_wrapper_rectangle(wrapper)
    for label_rect in _find_label_rects_for_wrapper(wrapper, expected):
        if _label_rect_matches_control(label_rect, control_rect):
            return True
    return False


def _match_sibling_text_block_label(wrapper, expected):
    """在 wrapper 的父级直接子节点中查找与控件紧邻的 TextBlock 标签兄弟。

    采用「DOM 顺序紧邻」消歧（不依赖布局/滚动位置）：从 wrapper 前面最近的
    兄弟开始往前扫描，遇到文本匹配的 TextBlock 即命中；若先遇到其它交互控件
    （Edit/Button/CheckBox/ComboBox 等）则终止——避免同一父容器下两个近似标签
    （如 50年/100年回归风速，automationId 都是 textbox）因共享父容器而误命中
    到不相邻的那个。空间邻近匹配（_label_rect_matches_control）作为兜底，
    覆盖 wrapper 不在直接子节点中的场景。
    """
    try:
        parent = wrapper.parent()
        if parent is None:
            return False
        children = parent.children()
        wrapper_index = None
        for i, sibling in enumerate(children):
            if _is_same_wrapper(sibling, wrapper):
                wrapper_index = i
                break
        _INTERACTIVE_TYPES = {
            "Edit", "Button", "CheckBox", "ComboBox", "RadioButton",
            "List", "ListItem", "ListItemView", "Spinner", "Thumb",
            "DataGrid", "Tree", "Table", "Calendar",
        }
        if wrapper_index is not None:
            # DOM 顺序紧邻判定：向前找最近的文本兄弟，中间不能隔其它交互控件
            for i in range(wrapper_index - 1, -1, -1):
                sibling = children[i]
                ctype = get_wrapper_control_type(sibling)
                if ctype in {"Text", "TextBlock", "Static", "Label"}:
                    if normalize_match_text(get_wrapper_text(sibling)) == expected:
                        return True
                    continue  # 非目标文本的文本兄弟（如单位"m/s"）继续往前找
                if ctype in _INTERACTIVE_TYPES:
                    break
                # 装饰节点（Image/Path/Group 等）不打断，继续往前
            # DOM 顺序未命中：再用空间邻近兜底（覆盖 label 与控件间有装饰节点、
            # 或 label 在控件上方而非左侧的布局），仍能正确排除不相邻的近似标签
        # 空间邻近匹配兜底：label 矩形与控件矩形左右/上下相邻才命中，
        # 同一父容器下多个近似标签（如 50年/100年回归风速）只会命中紧邻自己的那个
        control_rect = get_wrapper_rectangle(wrapper)
        if control_rect:
            for label_rect in _find_label_rects_for_wrapper(wrapper, expected):
                if _label_rect_matches_control(label_rect, control_rect):
                    return True
        return False
    except Exception as exc:
        _record_silent_exception("match_sibling_text_block", exc)
    return False


def _match_child_text_block_label(wrapper, expected):
    """在 wrapper 的子节点中查找文本等于 expected 的 TextBlock/Text。

    专为"控件自身 UIA Name 为空、文本在其子 Text 上"的场景设计：
    Telerik 多选下拉的 CheckBox（如热稳定度各等级 MTDGroupComboBoxMultiSelection_CheckBox）
    结构为 CheckBox > [Image, TextBlock(等级文本)]，等级文本在子 TextBlock 上。
    仅匹配 Text/TextBlock/Static 类子节点，避免误命中图标等无文本节点。
    """
    try:
        for child in wrapper.children():
            if get_wrapper_control_type(child) not in {"Text", "TextBlock", "Static", "Label"}:
                continue
            if normalize_match_text(get_wrapper_text(child)) == expected:
                return True
    except Exception as exc:
        _record_silent_exception("match_child_text_block", exc)
    return False


_FUNCTION_TEXT_NOISE = {"radcombobox", "radtabcontrol", "radtabitem"}


def _control_function_text(control_definition):
    """从控件定义提炼运行时消歧用的 helpText 语义（功能名，如"编辑所选中的配置"）。
    图标按钮的 UIA Name 常是 SVG path 无法阅读，helpText 是软件本地化资源暴露的真实
    操作语义，且是控件自身 UIA 属性——比父容器兄弟 Text 查找更可靠地消歧模板复制控件
    （同 automationId/name/uiPath，如各 interest-area 面板的 Edit/Delete 按钮）。
    与采集端 functionText 同源同规则：过滤 RadXxx 控件类型、pid/路径、SVG path 片段。"""
    if not isinstance(control_definition, dict):
        return ""
    inspect_data = (
        control_definition.get("inspectData", {})
        if isinstance(control_definition.get("inspectData"), dict)
        else {}
    )
    source = (
        control_definition.get("functionText", "")
        or control_definition.get("helpText", "")
        or inspect_data.get("functionText", "")
        or inspect_data.get("helpText", "")
    )
    value = normalize_match_text(source)
    if not value:
        return ""
    lowered = value.lower()
    if lowered in _FUNCTION_TEXT_NOISE or lowered.startswith("rad"):
        return ""
    if any(marker in value for marker in ("pid:", ":0x", ".dll", "\\")) or re.search(r"\bM\d{1,3},", value):
        return ""
    return value


def _get_focused_element():
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo
        from pywinauto.controls.uiawrapper import UIAWrapper

        focused = IUIA().iuia.GetFocusedElement()
        if focused is not None:
            return UIAWrapper(UIAElementInfo(focused))
    except Exception as exc:
        _record_silent_exception("get_focused_uia", exc)
    try:
        foreground = _try_get_window_by_handle(get_foreground_window_handle())
        if foreground is None:
            return None
        return foreground.focused()
    except Exception as exc:
        _record_silent_exception("get_focused_foreground", exc)
        return None


def _try_label_to_input_fallback(windows, control_definition, step_id=""):
    if not isinstance(control_definition, dict):
        return None
    if not windows:
        return None
    inspect_data = control_definition.get("inspectData", {})
    inspect_data = inspect_data if isinstance(inspect_data, dict) else {}
    expected_type = normalize_control_type_name(
        str(control_definition.get("controlType", "") or ""),
        str(inspect_data.get("controlType", "") or ""),
    )
    if expected_type not in _LABEL_COMPANION_CONTROL_TYPES:
        return None
    if expected_type in {"Pane", "Custom"} and normalize_match_text(
        inspect_data.get("automationId", "")
    ) != "PART_ContentHost":
        return None
    label_text = normalize_match_text(
        control_definition.get("labelText", "")
        or inspect_data.get("labelText", "")
        or control_definition.get("relatedLabelName", "")
        or inspect_data.get("relatedLabelName", "")
    )
    if not label_text:
        return None

    expects_content_host = expected_type in {"Pane", "Custom"} and normalize_match_text(
        inspect_data.get("automationId", "")
    ) == "PART_ContentHost"

    best_match = None
    best_score = -1
    for window in windows:
        try:
            candidates = window.descendants()
        except Exception as exc:
            _record_silent_exception("label_input_descendants", exc)
            continue
        for candidate in candidates:
            control_type = get_wrapper_control_type(candidate)
            resolved_candidate = candidate
            label_matches = wrapper_matches_label_text(candidate, label_text)
            if expects_content_host:
                if get_wrapper_automation_id(candidate) == "PART_ContentHost":
                    resolved_candidate = _resolve_editable_target(candidate)
                    if resolved_candidate is None:
                        continue
                    label_matches = label_matches or wrapper_matches_label_text(resolved_candidate, label_text)
                elif _is_editable_wrapper(candidate):
                    resolved_candidate = candidate
                else:
                    continue
            else:
                if control_type not in _LABEL_COMPANION_CONTROL_TYPES or control_type != expected_type:
                    continue
            if not label_matches:
                continue
            raw_score = score_control_match(candidate, control_definition)
            try:
                raw_score = int(raw_score or 0)
            except (TypeError, ValueError):
                raw_score = 80
            # 用原始分挑选最优候选（同窗口多个 label 候选时按分数排序）；
            # 不能先钳制到 ≥80 再比较——所有候选钳成同分后，第一个枚举到的必胜出（B7）。
            # 对应的 label 语义（候选已通过 label_matches）本就视作可接受，无需再按阈值否决。
            if raw_score > best_score:
                best_match = resolved_candidate
                best_score = raw_score
    if best_match is not None:
        _LOG_STEP(
            "[FlowLocator] label fallback hit: step={step_id}, control_type={control_type}, score={score}".format(
                step_id=step_id, control_type=expected_type, score=best_score
            )
        )
    return best_match


_NON_FOCUSABLE_CONTROL_TYPES = {"Text", "TextBlock", "Label", "Image", "Group", "Document"}


def _wrapper_is_keyboard_focusable(wrapper):
    """判定 wrapper 是否键盘可聚焦：静态控件类型（Text/Image 等）直接判定不可聚焦；
    其余类型优先读 UIA is_keyboard_focusable 属性增强，属性缺失或值不可判定时
    （如测试桩动态属性）按类型默认可聚焦。"""
    if wrapper is None:
        return False
    if get_wrapper_control_type(wrapper) in _NON_FOCUSABLE_CONTROL_TYPES:
        return False
    raw = _safe_get_value(
        lambda: getattr(wrapper.element_info, "is_keyboard_focusable", None), None
    )
    if raw is not None and (raw is True or raw is False):
        return bool(raw)
    return True


def _rect_center_distance(rect_a, rect_b):
    """两个矩形（left/top/right/bottom）中心点的欧氏距离；矩形缺失返回极大值。"""
    if not rect_a or not rect_b:
        return float("inf")
    ax = (rect_a["left"] + rect_a["right"]) / 2.0
    ay = (rect_a["top"] + rect_a["bottom"]) / 2.0
    bx = (rect_b["left"] + rect_b["right"]) / 2.0
    by = (rect_b["top"] + rect_b["bottom"]) / 2.0
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _find_focusable_sibling(wrapper):
    """在 wrapper 同父直接子节点中寻找最近的键盘可聚焦兄弟（同行优先，其次中心距离）。"""
    parent = _safe_get_value(lambda: wrapper.parent(), None)
    if parent is None:
        return None
    base_rect = get_wrapper_rectangle(wrapper)
    if not base_rect:
        return None
    candidates = []
    for sib in _safe_get_value(lambda: parent.children(), []) or []:
        if _is_same_wrapper(sib, wrapper):
            continue
        if not _wrapper_is_keyboard_focusable(sib):
            continue
        sib_rect = get_wrapper_rectangle(sib)
        if not sib_rect:
            continue
        same_row = not (sib_rect["bottom"] <= base_rect["top"] or sib_rect["top"] >= base_rect["bottom"])
        candidates.append((0 if same_row else 1, _rect_center_distance(sib_rect, base_rect), sib))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _find_focusable_neighbor(anchor, max_ancestor_depth=2):
    """寻找锚点附近可作为 Tab 起点的可聚焦控件（锚点自身不可聚焦时用）。

    优先级：同父可聚焦兄弟 → 后代中首个可聚焦控件 → 祖先（最多 max_ancestor_depth 层）
    的可聚焦兄弟。全部找不到返回 None。
    """
    if anchor is None:
        return None
    sibling = _find_focusable_sibling(anchor)
    if sibling is not None:
        return sibling
    frontier = _safe_get_value(lambda: anchor.children(), []) or []
    depth = 0
    while frontier and depth < 5:
        next_frontier = []
        for node in frontier:
            if _wrapper_is_keyboard_focusable(node):
                return node
            next_frontier.extend(_safe_get_value(lambda: node.children(), []) or [])
        frontier = next_frontier
        depth += 1
    current = anchor
    for _ in range(max_ancestor_depth):
        current = _safe_get_value(lambda: current.parent(), None)
        if current is None:
            break
        ancestor_sibling = _find_focusable_sibling(current)
        if ancestor_sibling is not None:
            return ancestor_sibling
    return None


def _activate_wrapper_window(wrapper):
    """激活 wrapper 所在顶层窗口（含还原最小化），成功返回 True。

    与 click_relative_anchor 的窗口激活逻辑一致：ALT 键击绕过 Windows 前台锁定。
    """
    hwnd = _get_top_level_hwnd_safe(wrapper)
    if not hwnd:
        return False
    try:
        if ctypes.windll.user32.IsIconic(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.4)
        for _ in range(3):
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            time.sleep(0.35)
            if ctypes.windll.user32.GetForegroundWindow() == hwnd:
                break
        time.sleep(0.5)
        return True
    except Exception:
        return False


def _try_set_focus(wrapper):
    try:
        wrapper.set_focus()
        time.sleep(0.1)
        return True
    except Exception:
        return False


def _focus_lands_on_anchor(anchor):
    """set_focus 后校验焦点是否可用作 Tab 起点。

    返回 True 需满足：焦点存在，且落在锚点自身，或至少落在窗口内子控件
    （非顶层 Window）。窗口未激活时 UIA set_focus 可能静默无效（不抛异常），
    焦点仍停留在窗口级——这种场景必须拦截，否则 Tab 起点无效、在窗口级空转。
    """
    focused = _get_focused_element()
    if focused is None:
        return False
    if _is_same_wrapper(focused, anchor):
        return True
    return get_wrapper_control_type(focused) != "Window"


def _try_focus_anchor(anchor, tab_navigation, step_id="", anchor_id=""):
    """锚点聚焦策略链：为 Tab 导航确定一个可用的焦点起点，返回该起点 wrapper。

    可聚焦锚点（Button/Edit 等）优先 set_focus（失败则激活窗口后重试）；
    静态不可聚焦锚点（Text/TextBlock/Label/Image 等）无法承载焦点，改为点击
    其最近的可聚焦相邻控件（同行兄弟/后代/祖级兄弟），让焦点落到确定的起点上，
    而不是点击锚点自身碰运气。所有点击策略执行后都会校验焦点落点
    （焦点未移动 / 落在顶层窗口视为该策略无效，继续尝试下一个），全部失败返回 None。
    配置 clickTwiceToExpand 时锚点是折叠头/展开钮，双击展开为功能操作，
    起点直接取锚点自身，由 Tab 循环评分兜底。
    """
    click_twice_to_expand = bool(tab_navigation.get("clickTwiceToExpand"))

    # 1) clickTwiceToExpand：连续点击两次确保展开后再 Tab。
    if click_twice_to_expand:
        anchor_center = get_wrapper_center(anchor)
        if not anchor_center:
            return None
        try:
            pyautogui.click(anchor_center[0], anchor_center[1])
            time.sleep(0.2)
            pyautogui.click(anchor_center[0], anchor_center[1])
            time.sleep(0.2)
            return anchor
        except Exception:
            return None

    # 2) 可聚焦锚点：set_focus 优先，成功后校验焦点落点（未落到窗口内子控件则
    #    视为未生效——窗口未激活时 UIA set_focus 会静默无效，激活窗口后重试一次）；
    #    均无效则降级到点击策略。
    if _wrapper_is_keyboard_focusable(anchor):
        if _try_set_focus(anchor) and _focus_lands_on_anchor(anchor):
            return anchor
        if _activate_wrapper_window(anchor) and _try_set_focus(anchor) and _focus_lands_on_anchor(anchor):
            return anchor
        # set_focus 均未生效：继续走点击策略（可聚焦控件点击同样可获焦）。

    # 3) 不可聚焦静态锚点（或可聚焦但 set_focus 失效）：
    #    先点击最近的可聚焦相邻控件，最后兜底点击锚点自身。
    #    仅点击路径需要校验"焦点是否移动"，故此处才记录点击前焦点。
    focus_before = _get_focused_element()
    neighbor = _find_focusable_neighbor(anchor)
    candidates = []
    if neighbor is not None:
        candidates.append(("focusable_neighbor", neighbor))
    candidates.append(("anchor_center", anchor))
    for label, wrapper in candidates:
        center = get_wrapper_center(wrapper)
        if not center:
            continue
        try:
            pyautogui.click(center[0], center[1])
            time.sleep(0.2)
        except Exception:
            continue
        focused = _get_focused_element()
        if focused is None:
            continue
        if _is_same_wrapper(focus_before, focused):
            _LOG_STEP(
                f"Tab 导航锚点点击聚焦无效({label}), 焦点未移动: "
                f"step={step_id}, anchor={anchor_id}, type={get_wrapper_control_type(anchor)}"
            )
            continue
        if get_wrapper_control_type(focused) == "Window":
            _LOG_STEP(
                f"Tab 导航锚点点击聚焦无效({label}), 焦点在顶层窗口: "
                f"step={step_id}, anchor={anchor_id}, type={get_wrapper_control_type(anchor)}"
            )
            continue
        _LOG_STEP(
            f"Tab 导航锚点聚焦成功(手段={label}): step={step_id}, anchor={anchor_id}, "
            f"type={get_wrapper_control_type(anchor)}, 起点={get_wrapper_control_type(focused)}"
        )
        return focused
    return None


def _try_find_anchor_in_windows(windows, step_id, anchor_id):
    """在已枚举的窗口中轻量查找 Tab 导航锚点（复用主流程窗口，避免递归完整定位）。

    仅走 fast 原生查询（automationId/name/control_type，毫秒级），匹配标准与
    find_flow_control 一致（self-heal 覆盖 + wrapper_matches_control_definition + 评分）。
    返回命中 wrapper（含低置信候选，供评分兜底）；未命中返回 None，由调用方
    回退 find_flow_control 完整递归定位兜底（复杂场景行为与旧版一致）。
    """
    if not windows:
        return None
    step_definition = _GET_STEP_DEFINITION(step_id)
    controls = step_definition.get("controls", []) if isinstance(step_definition, dict) else []
    raw_cds = [c for c in controls if str(c.get("id", "")).strip() == anchor_id]
    if not raw_cds:
        return None
    control_definition = normalize_control_definition(
        _apply_self_heal_override(step_id, anchor_id, raw_cds[0])
    )
    best_match = None
    best_score = -1
    for _attempt in range(2):  # 小重试应对 UI 未就绪（如弹窗渲染中）
        for window in windows:
            for candidate in iter_fast_locator_candidates(window, control_definition):
                if not wrapper_matches_control_definition(candidate, control_definition):
                    continue
                score = score_control_match(candidate, control_definition)
                if score > best_score:
                    best_score = score
                    best_match = candidate
                if best_score >= 100:
                    return best_match
        if _attempt == 0:
            time.sleep(0.3)
    return best_match


def _try_tab_navigation_fallback(windows, control_definition, step_id="", max_tab_steps=None):
    if not isinstance(control_definition, dict):
        return None
    tab_navigation = control_definition.get("tabNavigation")
    if not isinstance(tab_navigation, dict) or not tab_navigation:
        return None
    if not windows:
        return None
    inspect_data = control_definition.get("inspectData", {})
    inspect_data = inspect_data if isinstance(inspect_data, dict) else {}
    control_type = normalize_control_type_name(
        str(control_definition.get("controlType", "") or ""),
        str(inspect_data.get("controlType", "") or ""),
    )
    if control_type not in _LABEL_COMPANION_CONTROL_TYPES:
        return None
    control_key = str(control_definition.get("id", "") or "").strip() or str(step_id or "").strip()
    if not control_key:
        return None
    if control_key in _TAB_NAV_IN_PROGRESS:
        return None

    _TAB_NAV_IN_PROGRESS.add(control_key)
    try:
        anchor_id = str(tab_navigation.get("anchorControlId", "")).strip()
        if not anchor_id:
            return None
        direction = str(tab_navigation.get("direction", "")).strip().lower()
        send_key = "+{TAB}" if direction in {"backward", "back", "shift_tab"} else "{TAB}"
        # steps 解析容错：非法值（如 "3.5"/None）默认 0，避免 int() 抛错炸穿整个定位
        try:
            configured_steps = int(tab_navigation.get("steps", 0) or 0)
        except (TypeError, ValueError):
            configured_steps = 0
        if max_tab_steps is None:
            max_tab_steps = configured_steps or 8
        else:
            try:
                max_tab_steps = int(max_tab_steps or 0) or configured_steps or 8
            except (TypeError, ValueError):
                max_tab_steps = configured_steps or 8
        max_tab_steps = max(1, max_tab_steps)
        # 两级锚点查找：先在主流程已枚举的 windows 里轻量 fast 查询（毫秒级，
        # 避免递归完整定位的窗枚举+整树耗时），未命中再回退完整递归定位兜底。
        anchor = _try_find_anchor_in_windows(windows, step_id, anchor_id)
        if anchor is None:
            try:
                anchor = find_flow_control(step_id, control_id=anchor_id, timeout_seconds=1.5)
            except Exception:
                return None
        if anchor is None:
            return None
        # 聚焦锚点或确定 Tab 起点：不可聚焦锚点（Text/Image 等）采用策略链——
        # set_focus → 激活窗口后重试 → 点击最近可聚焦相邻控件 → 点击锚点中心，
        # 由实际获得焦点的控件作为 Tab 起点（评分兜底）。
        anchor_type = get_wrapper_control_type(anchor)
        start_wrapper = _try_focus_anchor(anchor, tab_navigation, step_id=step_id, anchor_id=anchor_id)
        if start_wrapper is None:
            _LOG_STEP(
                f"Tab 导航锚点聚焦失败, 放弃: step={step_id}, anchor={anchor_id}, type={anchor_type}"
            )
            return None
        if not _is_same_wrapper(start_wrapper, anchor):
            # 起点被替换为锚点附近的可聚焦控件（点击邻居/锚点中心的落点），
            # 从该起点到目标的 Tab 步数与录制配置(steps)不同：放宽步数上限，
            # 由评分命中(>=70)提前返回与焦点环检测兜底，避免步数不足走不到目标。
            _tab_upper = max(max_tab_steps, configured_steps + _TAB_NAV_START_OFFSET_BUFFER)
            if _tab_upper != max_tab_steps:
                _LOG_STEP(
                    f"Tab 导航起点偏移(非锚点自身), 步数上限 {max_tab_steps} → {_tab_upper}: "
                    f"step={step_id}, anchor={anchor_id}, type={anchor_type}"
                )
                max_tab_steps = _tab_upper
        anchor = start_wrapper

        best_match = None
        best_score = -1
        seen = []
        for _step_index in range(max_tab_steps):
            try:
                send_keys(send_key)
                time.sleep(0.1)
            except Exception:
                break
            focused = _get_focused_element()
            if focused is None:
                continue
            if get_wrapper_control_type(focused) == "Window":
                # Tab 起点无效：焦点仍停留在顶层窗口（set_focus/点击都未落到子控件），
                # 继续 Tab 只会窗口级空转，立即放弃。
                _LOG_STEP(
                    f"Tab 导航步进焦点在顶层窗口, 放弃: step={step_id}, tab={_step_index + 1}/{max_tab_steps}"
                )
                break
            if any(_is_same_wrapper(focused, seen_item) for seen_item in seen):
                break
            seen.append(focused)
            score = score_control_match(focused, control_definition)
            try:
                score = int(score or 0)
            except (TypeError, ValueError):
                score = 0
            if score > best_score:
                best_match = focused
                best_score = score
            _LOG_STEP(
                f"Tab 导航步进: step={step_id}, tab={_step_index + 1}/{max_tab_steps}, "
                f"type={get_wrapper_control_type(focused)}, "
                f"aid={_safe_get_value(lambda: getattr(focused.element_info, 'automation_id', ''), '')}, "
                f"name={_safe_get_value(lambda: getattr(focused.element_info, 'name', ''), '')}, "
                f"score={score}"
            )
            if best_score >= 70:
                return best_match, best_score
        # 循环正常结束后兜底：必须是达标分数才判命中，否则返回 None——
        # 任意正分（10-69 的弱匹配/仅 control_type 命中的无关控件）不得当作成功，
        # 避免误定位并污染控件缓存。
        if best_match is not None and best_score >= 70:
            return best_match, best_score
        return None
    finally:
        _TAB_NAV_IN_PROGRESS.discard(control_key)


def get_wrapper_found_index(wrapper, scope_method="", scope_value=""):
    """返回 wrapper 在其父容器直接子节点中、与指定属性同类的兄弟里的 0 基序号；未知返回 -1。

    这是"父链引导"定位的核心：先由父容器界定范围，再按同级第 N 个消歧。
    scope_method 取 control_type/class_name/name（默认 control_type）；
    scope_value 为空时按 wrapper 自身对应属性归类。
    """
    if wrapper is None:
        return -1
    parent = _safe_get_value(lambda: wrapper.parent(), None)
    if parent is None:
        return -1
    siblings = _safe_get_value(lambda: parent.children(), []) or []
    if not siblings:
        return -1

    def scope_of(node):
        if scope_method == "class_name":
            return get_wrapper_class_name(node)
        if scope_method == "name":
            return get_wrapper_text(node)
        return get_wrapper_control_type(node)

    target_scope = normalize_match_text(scope_value) or scope_of(wrapper)
    position = 0
    for node in siblings:
        node_scope = scope_of(node)
        if target_scope and not value_matches(node_scope, target_scope):
            continue
        if _is_same_wrapper(node, wrapper):
            return position
        position += 1
    return -1


def get_wrapper_debug_snapshot(wrapper):
    if wrapper is None:
        return {}
    return {
        "name": get_wrapper_text(wrapper),
        "className": get_wrapper_class_name(wrapper),
        "controlType": get_wrapper_control_type(wrapper),
        "automationId": get_wrapper_automation_id(wrapper),
        "frameworkId": get_wrapper_framework_id(wrapper),
        "processId": get_wrapper_process_id(wrapper),
        "handle": get_wrapper_handle_text(wrapper),
        "rect": get_wrapper_rectangle(wrapper) or {},
        "isEnabled": get_wrapper_is_enabled(wrapper),
        "isOffscreen": get_wrapper_is_offscreen(wrapper),
        "isKeyboardFocusable": get_wrapper_is_keyboard_focusable(wrapper),
        "hasKeyboardFocus": get_wrapper_has_keyboard_focus(wrapper),
        "parents": get_wrapper_parent_signatures(wrapper, depth=4),
        "children": get_wrapper_child_signatures(wrapper, limit=8),
    }


def get_wrapper_child_signatures(wrapper, limit=12):
    signatures = []
    for child in _safe_get_value(lambda: wrapper.children(), []):
        name = get_wrapper_text(child)
        class_name = get_wrapper_class_name(child)
        control_type = get_wrapper_control_type(child)
        signature = " | ".join(item for item in [name, class_name, control_type] if item)
        if signature:
            signatures.append(signature)
        if len(signatures) >= limit:
            break
    return signatures


def get_wrapper_value_snapshot(wrapper):
    return {
        "windowText": normalize_match_text(_safe_get_value(lambda: wrapper.window_text(), "")),
        "texts": _safe_get_value(lambda: wrapper.texts(), []),
        "getValue": normalize_match_text(_safe_get_value(lambda: wrapper.get_value(), "")),
        "legacyValue": normalize_match_text(
            _safe_get_value(lambda: (wrapper.legacy_properties() or {}).get("Value", ""), "")
        ),
        "ifaceValue": normalize_match_text(
            _safe_get_value(lambda: getattr(wrapper.iface_value, "CurrentValue", ""), "")
        ),
    }


def get_window_descendant_debug_summary(window, limit=16):
    summary = []
    if window is None:
        return summary
    try:
        descendants = window.descendants()
    except Exception:
        descendants = []
    for candidate in descendants:
        control_type = get_wrapper_control_type(candidate)
        class_name = get_wrapper_class_name(candidate)
        if control_type not in {"Edit", "Button", "SplitButton", "ComboBox", "Text"} and class_name not in {"Edit", "Button"}:
            continue
        summary.append(
            {
                "name": get_wrapper_text(candidate),
                "className": class_name,
                "controlType": control_type,
                "automationId": get_wrapper_automation_id(candidate),
                "frameworkId": get_wrapper_framework_id(candidate),
                "isEnabled": get_wrapper_is_enabled(candidate),
                "hasKeyboardFocus": get_wrapper_has_keyboard_focus(candidate),
                "rect": get_wrapper_rectangle(candidate) or {},
            }
        )
        if len(summary) >= limit:
            break
    return summary


def extract_signature_tokens(signature):
    tokens = []
    for part in str(signature or "").split("|"):
        token = normalize_match_text(part)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def get_wrapper_runtime_text_candidates(wrapper):
    tokens = []
    for value in [get_wrapper_text(wrapper)]:
        normalized = normalize_match_text(value)
        if normalized and normalized not in tokens:
            tokens.append(normalized)
    for value in _safe_get_value(lambda: wrapper.texts(), []):
        normalized = normalize_match_text(value)
        if normalized and normalized not in tokens:
            tokens.append(normalized)
    for signature in get_wrapper_child_signatures(wrapper, limit=8):
        for token in extract_signature_tokens(signature):
            if token and token not in tokens:
                tokens.append(token)
    return tokens


def is_placeholder_text(value):
    text = normalize_match_text(value)
    if not text:
        return False
    if re.fullmatch(r"[\?？]+", text):
        return True
    return "请补充" in text


def get_dropdown_runtime_target_texts(control_definition):
    control_definition = control_definition if isinstance(control_definition, dict) else {}
    inspect_data = control_definition.get("inspectData", {}) or {}
    target_texts = []

    def add_target(value):
        for part in split_locator_parts(value) or [value]:
            normalized = normalize_match_text(part)
            if not normalized:
                continue
            if is_placeholder_text(normalized):
                continue
            if normalized in {
                "ListBoxItem",
                "MenuItem",
                "TextBlock",
                "Text",
                "ListItem",
                "list item",
                "menu item",
                "text",
            }:
                continue
            if normalized not in target_texts:
                target_texts.append(normalized)

    add_target(control_definition.get("name", ""))
    add_target(inspect_data.get("name", ""))
    add_target(inspect_data.get("legacyName", ""))
    add_target(inspect_data.get("recommendedTargetValue", ""))
    for segment in parse_uipath_segments(control_definition.get("uiPath", "")):
        add_target(segment)
    methods = split_locator_parts(control_definition.get("targetMethod", ""))
    values = split_locator_parts(control_definition.get("targetValue", ""))
    for method, value in zip(methods, values):
        if method.strip() == "name":
            add_target(value)
    for child_signature in inspect_data.get("children", []) if isinstance(inspect_data.get("children", []), list) else []:
        for token in extract_signature_tokens(child_signature):
            add_target(token)
    return target_texts


def get_dropdown_runtime_expected_window_titles(step_definition, control_definition, window_title_hint=""):
    titles = []
    for value in [
        window_title_hint,
        control_definition.get("windowTitle", "") if isinstance(control_definition, dict) else "",
        step_definition.get("windowTitle", "") if isinstance(step_definition, dict) else "",
    ]:
        for item in parse_window_title_candidates(value):
            if not is_placeholder_text(item) and item not in titles:
                titles.append(item)
    return titles


def is_dropdown_like_wrapper(wrapper):
    control_type = get_wrapper_control_type(wrapper)
    localized_control_type = get_wrapper_localized_control_type(wrapper).lower()
    class_name = get_wrapper_class_name(wrapper)
    return (
        control_type in {"ListItem", "MenuItem"}
        or localized_control_type in {"list item", "menu item"}
        or (
            class_name
            and any(
                key in class_name
                for key in ("ListBoxItem", "MenuItem", "RadListBoxItem", "RadComboBoxItem", "ComboBoxItem")
            )
        )
    )


def score_dropdown_runtime_candidate(
    wrapper,
    target_texts,
    expected_window_titles=None,
    expected_process_id="",
):
    if wrapper is None or not is_dropdown_like_wrapper(wrapper):
        return -1
    if not _safe_get_value(lambda: wrapper.is_enabled(), False):
        return -1
    # 可见性不再硬性拒绝：UIA-to-MSAA bridge 暴露的 WPF 虚拟化选项
    # （如 Telerik ListBoxItem）常被标记 IsOffscreen=true，但它是真实可选项，
    # 且坐标点击依赖矩形、对 bridge 元素无效，只能用 LegacyIAccessible 双击。
    # 改为"可见 +10 分"让可见候选优先，同时保留不可见候选取胜的可能。
    wrapper_visible = bool(_safe_get_value(lambda: wrapper.is_visible(), False))

    runtime_tokens = [item.lower() for item in get_wrapper_runtime_text_candidates(wrapper)]
    if not runtime_tokens:
        return -1

    score = 0
    if wrapper_visible:
        score += 10
    target_matched = False
    for target in target_texts or []:
        normalized_target = normalize_match_text(target).lower()
        if not normalized_target:
            continue
        if any(token == normalized_target for token in runtime_tokens):
            score += 60
            target_matched = True
            continue
        if any(normalized_target in token or token in normalized_target for token in runtime_tokens):
            score += 36
            target_matched = True
    if target_texts and not target_matched:
        return -1

    parents = get_wrapper_parent_signatures(wrapper, depth=6)
    parent_blob = " || ".join(parents).lower()
    if "popup | window" in parent_blob:
        score += 18
    if any("window" in signature.lower() for signature in parents):
        score += 4
    if expected_window_titles:
        # "*" / "__all__" 表示不约束窗口标题（主窗口根路径的伪标题），
        # 与 score_control_match 的 windowTitle 通配符处理保持一致：
        # 仅存在真实标题时才做 匹配+24 / 不匹配-40，避免通配符把候选分数压垮。
        real_titles = [
            normalized
            for title in expected_window_titles
            if (normalized := normalize_match_text(title).lower())
            and normalized not in {"*", "__all__"}
        ]
        if real_titles:
            matched_window_title = False
            for normalized_title in real_titles:
                if normalized_title in parent_blob:
                    score += 24
                    matched_window_title = True
                    break
            if not matched_window_title:
                score -= 40
    wrapper_process_id = get_wrapper_process_id(wrapper)
    if expected_process_id and wrapper_process_id and wrapper_process_id == expected_process_id:
        score += 10
    if get_wrapper_class_name(wrapper) == "ListBoxItem":
        score += 8
    if get_wrapper_control_type(wrapper) == "ListItem":
        score += 8
    return score


def _collect_dropdown_windows():
    """收集用于下拉候选枚举的顶层窗口（含 WPF Popup 等未过滤顶层窗口）。"""
    windows = []
    try:
        windows = Desktop(backend="uia").windows()
    except Exception:
        windows = []
    # 补上 Desktop.windows() 默认过滤漏掉的 WPF Popup 等未过滤顶层窗口。
    try:
        desktop = Desktop(backend="uia")
        raw_children = desktop.element_info.children() if hasattr(desktop.element_info, "children") else []
        for child_info in raw_children or []:
            try:
                from pywinauto.controls.uiawrapper import UIAWrapper
                win = UIAWrapper(child_info)
                handle = get_wrapper_handle(win)
                if handle and any(get_wrapper_handle(w) == handle for w in windows):
                    continue
                windows.append(win)
            except Exception:
                pass
    except Exception:
        pass
    return windows


def iter_dropdown_runtime_candidates(windows=None):
    if windows is None:
        windows = _collect_dropdown_windows()
    seen = set()
    for window in windows:
        if is_automation_window(window):
            continue
        wrappers = [window]
        try:
            wrappers.extend(window.descendants())
        except Exception:
            pass
        for wrapper in wrappers:
            handle_key = get_wrapper_handle(wrapper) or id(wrapper)
            if handle_key in seen:
                continue
            seen.add(handle_key)
            yield wrapper


_DROPDOWN_RAW_FILTER_PROPS = None


def _dropdown_raw_filter_props():
    """惰性构建 Raw View 下拉选项预过滤使用的 UIA 属性 id（依赖 COM 单例）。"""
    global _DROPDOWN_RAW_FILTER_PROPS
    if _DROPDOWN_RAW_FILTER_PROPS is None:
        try:
            from pywinauto.uia_defines import IUIA
            uia_dll = IUIA().UIA_dll
            _DROPDOWN_RAW_FILTER_PROPS = {
                "control_type": getattr(uia_dll, "UIA_ControlTypePropertyId", 30003),
                "class_name": getattr(uia_dll, "UIA_ClassNamePropertyId", 30012),
            }
        except Exception:
            _DROPDOWN_RAW_FILTER_PROPS = {}
    return _DROPDOWN_RAW_FILTER_PROPS


def _iter_dropdown_raw_view_candidates(windows=None, max_elements=40000):
    """Raw View 兜底：Telerik 等虚拟化下拉选项在 Control View 不可见时，
    用 RawViewWalker 枚举窗口树中的 ListBoxItem/MenuItem（生成器）。

    优化：
      1. 支持传入预收集的窗口列表（按目标进程过滤），避免每轮枚举所有桌面窗口；
      2. 窗口按"前台窗口优先"排序——展开的下拉 Popup 通常在前台，先扫它；
      3. 用原始 UIA 属性（ControlType/ClassName）预过滤，非 ListBoxItem 类
         元素一次读取即跳过，不再构造 UIAWrapper，大幅提速；
      4. 元素上限 40000（配合预过滤，覆盖 Meteodyn 主窗口深层树）。
    """
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
    except Exception:
        return
    if windows is None:
        windows = _collect_dropdown_windows()
    else:
        windows = list(windows)
    props = _dropdown_raw_filter_props()
    foreground_handle = get_foreground_window_handle()
    try:
        windows.sort(key=lambda w: 0 if str(get_wrapper_handle(w)) == str(foreground_handle) else 1)
    except Exception:
        pass
    for window in windows:
        if is_automation_window(window):
            continue
        try:
            walker = IUIA().iuia.RawViewWalker
            root_element = window.element_info.element
        except Exception:
            continue
        queue = []
        try:
            child = walker.GetFirstChildElement(root_element)
            while child:
                queue.append(child)
                child = walker.GetNextSiblingElement(child)
        except Exception:
            pass
        seen = set()
        index = 0
        while index < len(queue) and index < max_elements:
            element = queue[index]
            index += 1
            # 原始属性预过滤：非 ListBoxItem/MenuItem 类元素直接跳过
            if props:
                is_candidate = False
                try:
                    actual_type = int(element.GetCurrentPropertyValue(props["control_type"]) or 0)
                    if actual_type in (50007, 50011):  # ListItem / MenuItem
                        is_candidate = True
                except Exception:
                    is_candidate = True  # 属性读取失败不拦截
                if not is_candidate:
                    try:
                        class_name = str(element.GetCurrentPropertyValue(props["class_name"]) or "")
                        is_candidate = any(
                            key in class_name
                            for key in ("ListBoxItem", "MenuItem", "RadListBoxItem", "RadComboBoxItem", "ComboBoxItem")
                        )
                    except Exception:
                        is_candidate = True
                if not is_candidate:
                    continue
            try:
                wrapper = UIAWrapper(UIAElementInfo(element))
            except Exception:
                continue
            handle_key = get_wrapper_handle(wrapper) or id(wrapper)
            if handle_key in seen:
                continue
            seen.add(handle_key)
            if is_dropdown_like_wrapper(wrapper):
                yield wrapper
            try:
                child = walker.GetFirstChildElement(element)
                while child:
                    queue.append(child)
                    child = walker.GetNextSiblingElement(child)
            except Exception:
                pass


def get_wrapper_toggle_state(wrapper):
    """读取控件的 ToggleState：'0'=Off / '1'=On / '2'=Indeterminate，读取失败返回 ''。"""
    if wrapper is None:
        return ""
    try:
        return str(wrapper.element_info.element.CurrentToggleState)
    except Exception:
        pass
    try:
        return str(wrapper.get_toggle_state())
    except Exception:
        return ""


def _toggle_fixup_desired_state(step_definition, control_id):
    """步骤 toggle 前置条件指向与点击同一控件时，返回点击后续期望的切换态
    （expected 的相反态）；不适用（无 precondition / 指向其它控件 / expected
    不可推导）时返回 ''。仅用于 click 动作结束后校验并兜底收敛。"""
    try:
        action_config = (step_definition or {}).get("actionConfig", {}) or {}
        precondition = action_config.get("precondition", {}) or {}
        condition = str(precondition.get("condition", "")).strip().lower()
        if condition not in {"toggle", "checked", "toggle_state"}:
            return ""
        pre_control_id = str(
            precondition.get("controlId", "")
            or action_config.get("controlId", "")
            or action_config.get("controlRef", "")
        ).strip()
        if pre_control_id and pre_control_id != str(control_id):
            return ""
        expected = str(precondition.get("expected", "")).strip().lower()
    except Exception:
        return ""
    return {"off": "on", "0": "on", "on": "off", "1": "off"}.get(expected, "")


def toggle_wrapper_via_pattern(wrapper):
    """用 TogglePattern.Toggle() 程序化切换；不支持时回退 LegacyIAccessible 默认动作。
    返回是否成功派发了切换动作（不代表最终状态已达目标）。"""
    if wrapper is None:
        return False
    try:
        from pywinauto.uia_defines import get_elem_interface
    except Exception:
        return False
    try:
        get_elem_interface(wrapper.element_info.element, "Toggle").Toggle()
        return True
    except Exception:
        pass
    try:
        get_elem_interface(wrapper.element_info.element, "LegacyIAccessible").DoDefaultAction()
        return True
    except Exception:
        return False


def reach_wrapper_toggle_state(wrapper, desired, max_attempts=3):
    """把控件 ToggleState 收敛到 desired('on'/'off')：读状态，未达标则程序化
    Toggle 一次后重读，最多 max_attempts 次。状态不可读或无法程序化切换时返回 False。"""
    if wrapper is None or desired not in {"on", "off"}:
        return False
    on_set = {"1", "on"}
    off_set = {"0", "off"}
    for _ in range(max_attempts):
        state = str(get_wrapper_toggle_state(wrapper) or "").lower()
        if state in on_set and desired == "on":
            return True
        if state in off_set and desired == "off":
            return True
        if not toggle_wrapper_via_pattern(wrapper):
            return False
        time.sleep(0.2)
    return False


def _read_wrapper_value_raw(wrapper):
    """读取单个 wrapper 的值（ValuePattern / LegacyIAccessible.Value），不支持/无值时返回 None。"""
    if wrapper is None:
        return None
    try:
        raw = wrapper.element_info.element.CurrentValue
        if raw not in (None, ""):
            return raw
    except Exception:
        pass
    try:
        raw = wrapper.get_value()
        if raw not in (None, ""):
            return raw
    except Exception:
        pass
    # LegacyIAccessible.Value：Telerik RadComboBox 的选中项文本经 MSAA 桥暴露在此
    # （Inspect 中 RadComboBox 的 LegacyIAccessible.Value 即当前选中项文本）。
    try:
        from pywinauto.uia_defines import get_elem_interface
        raw = get_elem_interface(wrapper.element_info.element, "LegacyIAccessible").CurrentValue
        if raw not in (None, ""):
            return raw
    except Exception:
        pass
    return None


def get_wrapper_value(wrapper, climb_parents=True):
    """读取控件的 ValuePattern 值（如下拉框当前选中文本），读取失败返回 ''。

    Telerik RadComboBox 的 ValuePattern 挂在 ComboBox 本体上，PART_DropDownButton
    （ToggleButton）自身无 ValuePattern；本函数读取失败时沿父级链向上查找带
    ValuePattern 的祖先（最多 6 层），并兜底查找子级 Edit 文本框
    （可编辑 ComboBox 的 PART_EditableTextBox）。int/float 值按原样字符串化。
    """
    if wrapper is None:
        return ""
    candidates = []
    current = wrapper
    for _depth in range(7 if climb_parents else 1):
        if current is None:
            break
        candidates.append(current)
        try:
            current = current.parent()
        except Exception:
            break
    # 子级 Edit 兜底：可编辑 RadComboBox 的当前文本显示在 PART_EditableTextBox 上
    try:
        for child in wrapper.children():
            if str(get_wrapper_control_type(child) or "").lower() in {"edit", "text"}:
                candidates.append(child)
                break
    except Exception:
        pass
    for candidate in candidates:
        raw = _read_wrapper_value_raw(candidate)
        if raw not in (None, ""):
            return raw if isinstance(raw, str) else str(raw)
    return ""


def click_dropdown_runtime_candidate(wrapper):
    click_point = get_wrapper_center(wrapper)
    try:
        wrapper.set_focus()
    except Exception:
        pass
    try:
        wrapper.click_input()
        return True, {"method": "click_input", "point": click_point}
    except Exception:
        pass
    ok, click_point = click_wrapper_center(wrapper, click_kind="left")
    if ok:
        return True, {"method": "center_click", "point": click_point}
    # UIA-to-MSAA bridge 暴露的选项（如 Telerik ListBoxItem）无有效矩形，
    # 坐标点击必然失败，改用 LegacyIAccessible 默认动作（DefAction=双击=选择）。
    try:
        from pywinauto.uia_defines import get_elem_interface
        get_elem_interface(wrapper.element_info.element, "LegacyIAccessible").DoDefaultAction()
        return True, {"method": "legacy_default_action"}
    except Exception:
        pass
    return False, {}


def _read_dropdown_display_text(dropdown_wrapper):
    """读下拉框当前显示文本：优先 ValuePattern，其次 window_text，最后子 TextBlock。

    Telerik PART_DropDownButton 常无 ValuePattern 且 window_text 为空，
    显示文本在内部 TextBlock 子控件中。
    """
    if dropdown_wrapper is None:
        return ""
    value = get_wrapper_value(dropdown_wrapper)
    if value:
        return str(value).strip()
    text = get_wrapper_text(dropdown_wrapper)
    if text:
        return text
    try:
        for child in dropdown_wrapper.children():
            if get_wrapper_control_type(child) in {"Text", "TextBlock", "Static"}:
                child_text = get_wrapper_text(child)
                if child_text:
                    return child_text
    except Exception:
        pass
    return ""


def _escape_send_keys_text(text):
    """转义 send_keys 特殊字符，防止输入文本被解释成按键指令。

    pywinauto_recorder.player.send_keys 把 { } + ^ % ~ 当键码/修饰键：
    选项/输入文本含 "C++"、"100%"、"路径{xxx}" 时会被改写或触发组合键。
    """
    escape_map = {"{": "{{}", "}": "{}}", "+": "{+}", "^": "{^}", "%": "{%}", "~": "{~}"}
    return "".join(escape_map.get(ch, ch) for ch in str(text or ""))


def _dropdown_nav_delta(option_values, current_display, target_index):
    """计算下拉框键盘导航的目标位移。

    返回 (downs, needs_home)：
    - current_display 能在 option_values 中解析到下标时，downs = 目标与当前下标之差
      （>0 向下、<0 向上），needs_home=False —— 从当前选中项相对导航，防重跑错位；
    - 解析不到时，downs = target_index，needs_home=True —— 调用方先 HOME 归零再绝对导航。
    """
    current_index = -1
    if current_display:
        current_norm = normalize_match_text(current_display).lower()
        for idx, opt in enumerate(option_values):
            if normalize_match_text(opt).lower() == current_norm:
                current_index = idx
                break
    if current_index >= 0:
        return target_index - current_index, False
    return target_index, True


def _wrapper_identity_key(wrapper):
    """返回候选控件的稳定身份键（用于剔除已确认点错的候选，避免重复点击同一错误项）。"""
    if wrapper is None:
        return ""
    try:
        aid = get_wrapper_automation_id(wrapper)
        name = get_wrapper_text(wrapper)
        ctype = get_wrapper_control_type(wrapper)
        runtime_id = _safe_get_value(lambda: getattr(wrapper.element_info, "runtime_id", ""), "")
        return "{}\u0001{}\u0001{}\u0001{}".format(aid, ctype, name, runtime_id)
    except Exception:
        return ""


def _dropdown_currently_expanded(dropdown_wrapper):
    """判定下拉框是否处于展开态；两种状态都不可读时返回 None（未知）。

    Telerik RadComboBox 的 TogglePattern 挂在 PART_DropDownButton 子元素上，
    ComboBox 根读取 ToggleState 常返回 ""；此时改读 ExpandCollapsePattern 的
    展开态，避免"枚举+点击"路径被整体静默禁用。
    """
    if dropdown_wrapper is None:
        return None
    toggle = get_wrapper_toggle_state(dropdown_wrapper)
    if toggle in {"1", "On", "on", "1.0"}:
        return True
    if toggle in {"0", "Off", "off", "0.0"}:
        return False
    state = None
    for read_fn in (
        lambda: str(dropdown_wrapper.element_info.element.CurrentExpandCollapseState),
        lambda: str(dropdown_wrapper.get_expand_state()),
    ):
        try:
            value = read_fn()
        except Exception:
            continue
        if value:
            state = str(value).strip().lower()
            break
    if not state:
        return None
    name = state.rsplit(".", 1)[-1]
    if name in {"expanded", "partiallyexpanded", "1", "1.0", "2", "2.0"}:
        return True
    if name in {"collapsed", "leafnode", "leaf", "0", "0.0"}:
        return False
    return None


def _candidate_has_visible_rect(wrapper):
    """候选元素是否具有可见屏幕矩形（宽高均 > 0）。

    收起状态下 UIA-to-MSAA bridge 暴露的离屏/未渲染选项矩形为空，
    无有效矩形即不可安全点击，用该信号兜底判定。
    """
    if wrapper is None:
        return False
    try:
        rect = get_wrapper_rectangle(wrapper)
        if isinstance(rect, dict):
            width = int(rect.get("width", 0) or 0)
            height = int(rect.get("height", 0) or 0)
        else:
            width = int(getattr(rect, "width", 0) or 0)
            height = int(getattr(rect, "height", 0) or 0)
        return width > 0 and height > 0
    except Exception:
        return False


def select_dropdown_item_runtime(step_id, control_id, timeout_seconds=3, window_title_hint="", target_option="", control_map_path=None):
    step_definition = _GET_STEP_DEFINITION(step_id)
    control_definition = get_flow_control_definition(step_id, control_id)
    if not control_definition:
        return False, {}

    foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
    expected_process_id = get_wrapper_process_id(foreground_before)
    target_texts = get_dropdown_runtime_target_texts(control_definition)
    # 如果调用方指定了目标选项文本（如 "组"），优先使用，并补充到候选文本中。
    explicit_target = str(target_option or "").strip()
    if explicit_target:
        normalized = normalize_match_text(explicit_target)
        if normalized and normalized not in target_texts:
            target_texts = [normalized] + target_texts
    # 显式目标文本（actionConfig.value），供值检查/键入搜索兜底使用。
    search_text = str(explicit_target or "").strip()
    expected_window_titles = get_dropdown_runtime_expected_window_titles(
        step_definition,
        control_definition,
        window_title_hint=window_title_hint,
    )
    if not expected_window_titles:
        foreground_title = normalize_match_text(get_wrapper_text(foreground_before))
        if foreground_title and not is_placeholder_text(foreground_title):
            expected_window_titles = [foreground_title]
    deadline = time.time() + max(0.2, float(timeout_seconds or 0))

    # 预收集目标进程窗口：仅枚举一次并复用，避免每轮循环重复枚举所有桌面窗口，
    # 且大幅减少无关窗口的遍历开销（Meteodyn 主窗口 Raw View 树很大）。
    dropdown_windows = _collect_dropdown_windows()
    if expected_process_id:
        dropdown_windows = [
            w for w in dropdown_windows if get_wrapper_process_id(w) == expected_process_id
        ] or dropdown_windows

    # 提前检测 optionValues：若控件定义已包含可选项列表（如 MTD PART_DropDownButton），
    # 说明选项依赖 WPF 虚拟化渲染，UIA 弹窗枚举几乎不可能命中。此时将弹窗搜索
    # 预算缩短到 1.5 秒（做一次快速确认），尽快进入键盘导航兜底。
    pre_option_values = []
    inspect_data = control_definition.get("inspectData", {}) or {}
    if isinstance(inspect_data, dict):
        pre_option_values = [str(v).strip() for v in (inspect_data.get("optionValues", []) or []) if str(v).strip()]
    if not pre_option_values and isinstance(control_definition, dict):
        pre_option_values = [str(v).strip() for v in (control_definition.get("optionValues", []) or []) if str(v).strip()]
    # 资产注入：粗糙度索引文件类控件（如 step20 选 ESA2020.txt）的权威选项在
    # MUP 安装目录 Assets/CorrespondanceFiles/ 下，采集器常采不到 optionValues，
    # 此处从安装目录补全，使虚拟化下拉框尽早进入键盘导航/键入路径。
    if not pre_option_values:
        try:
            from mup_assets import inject_dropdown_option_values
            injected, _injected_flag = inject_dropdown_option_values(control_definition, [])
            pre_option_values = injected
        except Exception:
            pass
    if pre_option_values:
        deadline = min(deadline, time.time() + 1.5)

    last_ranked_candidates = []
    expanded_attempted = False
    dropdown_wrapper = None
    # 已确认点错的候选身份键：点击后显示值校验失败即剔除，避免每轮重复点击同一个错误项
    failed_option_keys = set()
    # 诊断探针：记录 Raw View 枚举到的下拉类候选数量与文本样例，失败时可定位
    # "没枚举到"还是"枚举到但文本/分数不匹配"。
    raw_probe = {"count": 0, "samples": []}
    _loop_started = time.time()
    _progress_log_at = 0.0
    while time.time() < deadline:
        # 进度日志：枚举过程（FindAll/树遍历/展开重试）可能耗时数十秒且无任何输出，
        # 每 ≥4 秒打一条进度，便于定位"卡在枚举"还是"命中但值校验失败"。
        _loop_now = time.time()
        if _loop_now - _progress_log_at >= 4.0:
            _progress_log_at = _loop_now
            _LOG_STEP(
                "下拉选项枚举进度: step={step_id}, control={control_id}, "
                "elapsed={elapsed:.1f}s, 候选窗口={windows}, raw探针={raw}, 已剔除错误项={failed}".format(
                    step_id=step_id,
                    control_id=control_id,
                    elapsed=_loop_now - _loop_started,
                    windows=len(dropdown_windows),
                    raw=raw_probe["count"],
                    failed=len(failed_option_keys),
                )
            )
        # 第一次迭代先确保下拉框展开：展开后的选项才可见/可操作，且避免误点
        # 收起状态下枚举到的离屏选项（UIA-to-MSAA bridge 选项无矩形、点击不可验证）。
        if not expanded_attempted:
            dropdown_wrapper = find_flow_control(
                step_id, control_id, timeout_seconds=2.0, window_title_hint=window_title_hint, control_map_path=control_map_path
            )
            if dropdown_wrapper is not None:
                # 值检查捷径：若下拉框当前值已等于目标选项（此前可能已选中），
                # 无需展开/枚举，直接判定成功。读取父级 ComboBox 的 ValuePattern。
                if search_text and not is_placeholder_text(search_text):
                    current_value = get_wrapper_value(dropdown_wrapper)
                    if current_value and normalize_match_text(current_value) == normalize_match_text(search_text):
                        _LOG_STEP(
                            "下拉框当前值已匹配目标: step={step_id}, control={control_id}, value={value}".format(
                                step_id=step_id, control_id=control_id, value=current_value
                            )
                        )
                        return True, {
                            "method": "value_already_matched",
                            "value": current_value,
                            "targetTexts": target_texts,
                        }
                toggle_state = get_wrapper_toggle_state(dropdown_wrapper)
                should_click = toggle_state in {"", "0", "Off", "off", "0.0", "Indeterminate"}
                # 无论本次是否点击展开，都重新收集下拉窗口列表：step_16 之类的前置
                # 步骤可能已把下拉展开（toggle=On），此时 Popup 窗口已存在但不在旧的
                # dropdown_windows 里，不重新收集会导致后续枚举漏掉 RadComboBoxItem。
                try:
                    popup_windows = _collect_dropdown_windows()
                    if expected_process_id:
                        popup_windows = [
                            w for w in popup_windows
                            if get_wrapper_process_id(w) == expected_process_id
                        ]
                    for w in popup_windows:
                        if not any(
                            get_wrapper_handle(x) == get_wrapper_handle(w)
                            for x in dropdown_windows
                        ):
                            dropdown_windows.append(w)
                except Exception:
                    pass
                if should_click:
                    clicked, _ = click_wrapper_center(dropdown_wrapper, click_kind="left")
                    if clicked:
                        _LOG_STEP(
                            "下拉框未展开，已自愈点击展开: step={step_id}, control={control_id}, toggle={toggle}".format(
                                step_id=step_id, control_id=control_id, toggle=toggle_state or "(unknown)"
                            )
                        )
                        time.sleep(0.4)
                        # 等待展开动画完成：轮询 ToggleState 直到 On（最多约 1.2 秒），
                        # 避免展开尚未渲染完就枚举导致选项不可见。
                        for _wait in range(4):
                            if get_wrapper_toggle_state(dropdown_wrapper) in {"1", "On", "on", "1.0"}:
                                break
                            time.sleep(0.2)
                        _LOG_STEP(
                            "下拉框展开状态: step={step_id}, control={control_id}, toggleAfter={toggle}".format(
                                step_id=step_id,
                                control_id=control_id,
                                toggle=get_wrapper_toggle_state(dropdown_wrapper) or "(unknown)",
                            )
                        )
                        # 展开后下拉选项所在的 Popup 窗口才创建（上面的窗口收集已覆盖），
                        # 此处只需为枚举选项续时：定位可能已耗掉大部分预算。
                        remaining = deadline - time.time()
                        if remaining < 2.0:
                            deadline = time.time() + 2.0
            expanded_attempted = True
            continue
        # 已展开：Control View 枚举可见选项
        ranked_candidates = []
        for candidate in iter_dropdown_runtime_candidates(dropdown_windows):
            # 内层超时退出：枚举目标窗口 UIA 子树可能较慢，超时提前中断
            if time.time() > deadline:
                break
            if failed_option_keys and _wrapper_identity_key(candidate) in failed_option_keys:
                continue
            score = score_dropdown_runtime_candidate(
                candidate,
                target_texts,
                expected_window_titles=expected_window_titles,
                expected_process_id=expected_process_id,
            )
            if score < 0:
                continue
            ranked_candidates.append((score, candidate))
        # Control View 无候选时补一轮 Raw View 枚举（Telerik 虚拟化选项）
        if not ranked_candidates:
            for candidate in _iter_dropdown_raw_view_candidates(dropdown_windows):
                if time.time() > deadline:
                    break
                if failed_option_keys and _wrapper_identity_key(candidate) in failed_option_keys:
                    continue
                raw_probe["count"] += 1
                if len(raw_probe["samples"]) < 5:
                    raw_probe["samples"].append(
                        "{}|{}|{}".format(
                            get_wrapper_text(candidate) or "(empty)",
                            get_wrapper_class_name(candidate) or "",
                            get_wrapper_control_type(candidate) or "",
                        )
                    )
                score = score_dropdown_runtime_candidate(
                    candidate,
                    target_texts,
                    expected_window_titles=expected_window_titles,
                    expected_process_id=expected_process_id,
                )
                if score < 0:
                    continue
                ranked_candidates.append((score, candidate))
            if not ranked_candidates:
                continue
        ranked_candidates.sort(key=lambda item: item[0], reverse=True)
        last_ranked_candidates = ranked_candidates[:5]
        if ranked_candidates and ranked_candidates[0][0] >= 70:
            best_candidate = ranked_candidates[0][1]
            # 仅当下拉框确已展开时才点击选项：收起状态下点击 bridge 离屏选项无效。
            # TogglePattern 在 Telerik 上常挂在 PART_DropDownButton 子元素，ComboBox 根
            # 读取失败返回 ""——此时不再简单禁用整条"枚举+点击"路径：
            #   1) 用 Toggle / ExpandCollapse 状态判定（_dropdown_currently_expanded）；
            #   2) 状态未知且候选已有可见矩形时，视为已展开可点（离屏/未渲染选项矩形为空）；
            #   3) 状态未知且候选无可见矩形时，补一次幂等展开点击后重判。
            dropdown_expanded = _dropdown_currently_expanded(dropdown_wrapper)
            if dropdown_expanded is None and dropdown_wrapper is not None:
                if _candidate_has_visible_rect(best_candidate):
                    dropdown_expanded = True
                else:
                    _exp_clicked, _ = click_wrapper_center(dropdown_wrapper, click_kind="left")
                    if _exp_clicked:
                        time.sleep(0.3)
                        dropdown_expanded = _dropdown_currently_expanded(dropdown_wrapper)
                        if dropdown_expanded is not True and _candidate_has_visible_rect(best_candidate):
                            dropdown_expanded = True
            if dropdown_expanded is not True and dropdown_wrapper is None and _candidate_has_visible_rect(best_candidate):
                # 下拉框本体不可得但选项已渲染在屏（如焦点已在下拉内）：同样允许点击
                dropdown_expanded = True
            if dropdown_expanded is not True:
                time.sleep(0.15)
                continue
            best_score, best_candidate = ranked_candidates[0]
            best_candidate_snapshot = get_wrapper_debug_snapshot(best_candidate)
            clicked, click_meta = click_dropdown_runtime_candidate(best_candidate)
            if clicked:
                time.sleep(0.12)
                verify_value = ""
                if dropdown_wrapper is not None:
                    try:
                        verify_value = _read_dropdown_display_text(dropdown_wrapper)
                    except Exception:
                        verify_value = ""
                if verify_value:
                    verify_norm = normalize_match_text(verify_value).lower()
                    matched_target = False
                    if explicit_target and not is_placeholder_text(explicit_target):
                        matched_target = normalize_match_text(explicit_target).lower() == verify_norm
                    if not matched_target:
                        for target in target_texts:
                            if target and normalize_match_text(target).lower() == verify_norm:
                                matched_target = True
                                break
                    if not matched_target:
                        _LOG_STEP(
                            "候选点击后显示值未确认: step={step_id}, control={control_id}, expected={expected}, actual={actual}".format(
                                step_id=step_id,
                                control_id=control_id,
                                expected=" / ".join(target_texts) or "(empty)",
                                actual=verify_value,
                            )
                        )
                        # 该候选已确认点错：剔除后重新展开，避免下一轮重复点击同一个错误项
                        failed_key = _wrapper_identity_key(best_candidate)
                        if failed_key:
                            failed_option_keys.add(failed_key)
                        expanded_attempted = False
                        continue
                    _LOG_STEP(
                        "已通过运行时下拉候选点击控件并校验显示值: step={step_id}, control={control_id}, score={score}, value={value}".format(
                            step_id=step_id,
                            control_id=control_id,
                            score=best_score,
                            value=verify_value,
                        )
                    )
                    return True, {
                        "score": best_score,
                        "targetTexts": target_texts,
                        "clickMeta": click_meta,
                        "bestCandidate": best_candidate_snapshot,
                        "valueVerified": verify_value,
                    }
                _LOG_STEP(
                    "已通过运行时下拉候选点击控件（显示值不可读，保留点击证据）: step={step_id}, control={control_id}, score={score}, texts={texts}".format(
                        step_id=step_id,
                        control_id=control_id,
                        score=best_score,
                        texts=" / ".join(target_texts) or "(empty)",
                    )
                )
                return True, {
                    "score": best_score,
                    "targetTexts": target_texts,
                    "clickMeta": click_meta,
                    "bestCandidate": best_candidate_snapshot,
                    "valueVerification": "unreadable",
                }
        time.sleep(0.15)

    # ---- 键盘导航兜底 ----
    _LOG_STEP(
        "运行时下拉枚举未命中，进入键盘导航兜底: step={step_id}, control={control_id}, "
        "elapsed={elapsed:.1f}s, raw探针={raw}, 已剔除错误项={failed}".format(
            step_id=step_id,
            control_id=control_id,
            elapsed=time.time() - _loop_started,
            raw=raw_probe["count"],
            failed=len(failed_option_keys),
        )
    )
    # 虚拟化下拉列表（如 MTD PART_DropDownButton）的选项在弹出窗口枚举和
    # 子树遍历中均不可见，仅当鼠标悬停时才实体化。若枚举阶段未命中，尝试用
    # 键盘方向键导航定位目标选项。
    option_values = []
    inspect_data = control_definition.get("inspectData", {}) or {}
    if isinstance(inspect_data, dict):
        option_values = [str(v).strip() for v in (inspect_data.get("optionValues", []) or []) if str(v).strip()]
    if not option_values and isinstance(control_definition, dict):
        option_values = [str(v).strip() for v in (control_definition.get("optionValues", []) or []) if str(v).strip()]
    option_values_injected = False
    if not option_values:
        try:
            from mup_assets import inject_dropdown_option_values
            option_values, option_values_injected = inject_dropdown_option_values(
                control_definition, option_values
            )
        except Exception:
            pass
    if option_values:
        # 在 optionValues 中查找目标文本的索引（精确或包含匹配）。
        target_index = -1
        search_texts = [explicit_target] if explicit_target else target_texts
        for search in search_texts:
            if not search:
                continue
            normalized_search = normalize_match_text(search).lower()
            for idx, opt in enumerate(option_values):
                if normalize_match_text(opt).lower() == normalized_search:
                    target_index = idx
                    break
            if target_index < 0:
                for idx, opt in enumerate(option_values):
                    if normalized_search in normalize_match_text(opt).lower():
                        target_index = idx
                        break
            if target_index >= 0:
                break
        if target_index >= 0:
            # 防错位前置：发方向键/回车前确保下拉框确已展开且获得键盘焦点。
            # 未展开/未定位到下拉框时会盲发按键、落到当前焦点控件，可能误触发无关按钮。
            nav_ready = dropdown_wrapper is not None
            downs = target_index
            if nav_ready:
                if _dropdown_currently_expanded(dropdown_wrapper) is not True:
                    _exp_clicked, _ = click_wrapper_center(dropdown_wrapper, click_kind="left")
                    if _exp_clicked:
                        time.sleep(0.3)
                        for _wait_t in range(4):
                            if _dropdown_currently_expanded(dropdown_wrapper) is True:
                                break
                            time.sleep(0.2)
                try:
                    dropdown_wrapper.set_focus()
                except Exception:
                    pass
                if _dropdown_currently_expanded(dropdown_wrapper) is not True:
                    _LOG_STEP(
                        "键盘导航前置：下拉框未确认展开，跳过盲发按键转键入搜索兜底: step={step_id}, control={control_id}, option={option}".format(
                            step_id=step_id,
                            control_id=control_id,
                            option=option_values[target_index],
                        )
                    )
                    nav_ready = False
                else:
                    # 从当前选中项出发做相对导航：重跑流程时下拉框当前值可能已落在目标
                    # 之前的某项，若每次都从固定起点 DOWN N 次会选到第 2N 项导致错位。
                    try:
                        current_display = _read_dropdown_display_text(dropdown_wrapper)
                    except Exception:
                        current_display = ""
                    downs, needs_home = _dropdown_nav_delta(option_values, current_display, target_index)
                    if needs_home:
                        # 当前值无法在候选列表中解析：先 HOME 归零再绝对导航
                        send_keys("{HOME}")
                        time.sleep(0.08)
            if nav_ready:
                if downs > 0:
                    for _ in range(downs):
                        send_keys("{DOWN}")
                        time.sleep(0.06)
                elif downs < 0:
                    for _ in range(-downs):
                        send_keys("{UP}")
                        time.sleep(0.06)
                send_keys("{ENTER}")
                time.sleep(0.15)
                # 键盘导航后统一读显示值验证（对全部选项来源生效，不限于注入的安装目录
                # 名单）：读到且不匹配，或读不到（不可验证），都不判定成功，转键入搜索，
                # 避免"防错位"被绕过导致点错。
                navigate_ok = False
                try:
                    display = _read_dropdown_display_text(dropdown_wrapper) if dropdown_wrapper is not None else ""
                except Exception:
                    display = ""
                if display:
                    display_norm = normalize_match_text(display).lower()
                    navigate_ok = any(
                        normalize_match_text(s).lower() and normalize_match_text(s).lower() in display_norm
                        for s in search_texts if s
                    )
                if not navigate_ok:
                    _LOG_STEP(
                        "键盘导航后显示值未确认，转键入搜索兜底: step={step_id}, control={control_id}, option={option}".format(
                            step_id=step_id,
                            control_id=control_id,
                            option=option_values[target_index],
                        )
                    )
                else:
                    _LOG_STEP(
                        "键盘导航选中下拉项并通过显示值校验: step={step_id}, control={control_id}, option={option}, index={idx}, totalOptions={total}".format(
                            step_id=step_id,
                            control_id=control_id,
                            option=option_values[target_index],
                            idx=target_index,
                            total=len(option_values),
                        )
                    )
                    return True, {
                        "method": "keyboard_navigate",
                        "targetIndex": target_index,
                        "targetOption": option_values[target_index],
                        "optionValues": option_values,
                        "targetTexts": target_texts,
                        "valueVerified": display,
                    }
    # ---- 键盘导航兜底结束 ----

    # ---- 值检查 + 键入搜索兜底（不依赖 optionValues / 下拉项 UIA 可见性）----
    # Telerik RadComboBox 的虚拟化选项在 UIA 中不可枚举，但支持键入过滤：
    # 展开后键入目标文本会自动跳转/过滤，ENTER 即选中。仅当调用方显式指定了
    # 目标选项文本（actionConfig.value）时才启用，避免误把控件名当键入内容。
    if search_text and not is_placeholder_text(search_text):
        # 复用循环前已定位的下拉框，避免在键入兜底里重复整树 FindAll
        # （实测 step_24a 测风对象 9.5s 二次定位）；仅当未定位/已失效时才重找。
        if dropdown_wrapper is None or not is_wrapper_alive(dropdown_wrapper):
            dropdown_wrapper = find_flow_control(
                step_id, control_id, timeout_seconds=2.0, window_title_hint=window_title_hint, control_map_path=control_map_path
            )
        if dropdown_wrapper is not None:
            current_value = get_wrapper_value(dropdown_wrapper)
            # 1) 当前值已等于目标：无需展开选择，直接成功
            if current_value and normalize_match_text(current_value) == normalize_match_text(search_text):
                _LOG_STEP(
                    "下拉框当前值已匹配目标: step={step_id}, control={control_id}, value={value}".format(
                        step_id=step_id, control_id=control_id, value=current_value
                    )
                )
                return True, {
                    "method": "value_already_matched",
                    "value": current_value,
                    "targetTexts": target_texts,
                }
            # 2) 确保下拉框处于展开状态后键入目标文本 + ENTER
            #    Telerik RadComboBox 展开后键盘焦点自动落到列表，键入目标文本会
            #    自动过滤/跳转，ENTER 即选中（选项经 UIA-to-MSAA bridge 暴露，
            #    无法通过 UIA 枚举/坐标点击操作，键入是唯一可靠路径）。
            expanded_confirmed = False
            toggle_state = get_wrapper_toggle_state(dropdown_wrapper)
            if toggle_state in {"1", "On", "on", "1.0"}:
                expanded_confirmed = True
            else:
                ok_click, _ = click_wrapper_center(dropdown_wrapper, click_kind="left")
                if ok_click:
                    time.sleep(0.4)
                    for _wait in range(4):
                        if get_wrapper_toggle_state(dropdown_wrapper) in {"1", "On", "on", "1.0"}:
                            expanded_confirmed = True
                            break
                        time.sleep(0.2)
            if expanded_confirmed:
                try:
                    dropdown_wrapper.set_focus()
                except Exception:
                    pass
                try:
                    send_keys(_escape_send_keys_text(search_text))
                    time.sleep(0.35)

                    # Telerik 键入过滤只是"高亮/过滤"，直接 ENTER 常只收起不选中
                    # （实测：选项可见但收起后框内无选中内容）。键入后目标项已
                    # 实体化可见，首选重试"枚举+点击"真实选中；均未命中再补
                    # DOWN/UP 提交 selection + ENTER 确认。
                    typed_clicked = False
                    retry_deadline = time.time() + 3.0
                    for candidate in iter_dropdown_runtime_candidates(dropdown_windows):
                        if time.time() > retry_deadline:
                            break
                        score = score_dropdown_runtime_candidate(
                            candidate,
                            target_texts,
                            expected_window_titles=expected_window_titles,
                            expected_process_id=expected_process_id,
                        )
                        if score < 70:
                            continue
                        if dropdown_wrapper is None or get_wrapper_toggle_state(
                            dropdown_wrapper
                        ) not in {"1", "On", "on", "1.0"}:
                            break  # 下拉框已收起，停止补点
                        clicked_cand, _click_meta = click_dropdown_runtime_candidate(candidate)
                        if clicked_cand:
                            typed_clicked = True
                            break
                    if not typed_clicked:
                        retry_deadline = time.time() + 3.0
                        for candidate in _iter_dropdown_raw_view_candidates(dropdown_windows):
                            if time.time() > retry_deadline:
                                break
                            score = score_dropdown_runtime_candidate(
                                candidate,
                                target_texts,
                                expected_window_titles=expected_window_titles,
                                expected_process_id=expected_process_id,
                            )
                            if score < 70:
                                continue
                            if dropdown_wrapper is None or get_wrapper_toggle_state(
                                dropdown_wrapper
                            ) not in {"1", "On", "on", "1.0"}:
                                break
                            clicked_cand, _click_meta = click_dropdown_runtime_candidate(candidate)
                            if clicked_cand:
                                typed_clicked = True
                                break

                    if typed_clicked:
                        time.sleep(0.25)
                        _LOG_STEP(
                            "键入过滤后点击选中下拉项(候选命中): step={step_id}, control={control_id}, option={option}".format(
                                step_id=step_id, control_id=control_id, option=search_text
                            )
                        )
                    else:
                        # 键入已高亮第一个匹配项；DOWN/UP 提交 selection 后再 ENTER 确认
                        send_keys("{DOWN}")
                        time.sleep(0.12)
                        send_keys("{UP}")
                        time.sleep(0.12)
                        send_keys("{ENTER}")
                        time.sleep(0.3)
                except Exception as exc:
                    _LOG_STEP(
                        "键入搜索失败: step={step_id}, control={control_id}, error={error}".format(
                            step_id=step_id, control_id=control_id, error=exc
                        )
                    )
                else:
                    verify_value = _read_dropdown_display_text(dropdown_wrapper)
                    if verify_value and normalize_match_text(verify_value) == normalize_match_text(search_text):
                        _LOG_STEP(
                            "键盘键入搜索选中下拉项: step={step_id}, control={control_id}, option={option}".format(
                                step_id=step_id, control_id=control_id, option=verify_value
                            )
                        )
                        return True, {
                            "method": "keyboard_type_search",
                            "targetOption": verify_value,
                            "targetTexts": target_texts,
                        }
                    if verify_value:
                        # 读到了值但不同于目标 → 确认未选中（如键入文本不匹配任何选项），
                        # 不靠"收起"兜底，避免误判成功。
                        _LOG_STEP(
                            "键盘键入搜索后值不匹配: step={step_id}, control={control_id}, expected={expected}, actual={actual}".format(
                                step_id=step_id,
                                control_id=control_id,
                                expected=search_text,
                                actual=verify_value,
                            )
                        )
                    else:
                        # 读不到值（无 ValuePattern 的 bridge 控件）时，收起状态作为间接证据：
                        # 键入+ENTER 后下拉框自动收起 = 已选中目标（Telerik 选中后收起）。
                        if get_wrapper_toggle_state(dropdown_wrapper) in {"0", "Off", "off", "0.0"}:
                            _LOG_STEP(
                                "键盘键入搜索后下拉框已收起（视为选中）: step={step_id}, control={control_id}, option={option}".format(
                                    step_id=step_id, control_id=control_id, option=search_text
                                )
                            )
                            return True, {
                                "method": "keyboard_type_search_collapsed",
                                "targetOption": search_text,
                                "targetTexts": target_texts,
                            }
    # ---- 值检查 + 键入搜索兜底结束 ----

    if last_ranked_candidates:
        candidate_text = "; ".join(
            "#{index} score={score} title={title} class={class_name} control={control_type} rect={rect}".format(
                index=index + 1,
                score=item[0],
                title=get_wrapper_text(item[1]) or "(empty)",
                class_name=get_wrapper_class_name(item[1]) or "(empty)",
                control_type=get_wrapper_control_type(item[1]) or "(empty)",
                rect=get_wrapper_rectangle(item[1]) or {},
            )
            for index, item in enumerate(last_ranked_candidates)
        )
        _LOG_STEP(
            "运行时下拉项未命中: step={step_id}, control={control_id}, targets={targets}, expectedTitles={titles}, foreground={foreground}, candidates={candidates}".format(
                step_id=step_id,
                control_id=control_id,
                targets=" / ".join(target_texts) or "(empty)",
                titles=" / ".join(expected_window_titles) or "(empty)",
                foreground=get_wrapper_text(foreground_before) or "(empty)",
                candidates=candidate_text,
            )
        )
    else:
        _LOG_STEP(
            "运行时下拉项未命中且未枚举到候选项: step={step_id}, control={control_id}, targets={targets}, expectedTitles={titles}, foreground={foreground}, rawProbe={probe}".format(
                step_id=step_id,
                control_id=control_id,
                targets=" / ".join(target_texts) or "(empty)",
                titles=" / ".join(expected_window_titles) or "(empty)",
                foreground=get_wrapper_text(foreground_before) or "(empty)",
                probe=json.dumps(raw_probe, ensure_ascii=False),
            )
        )
    return False, {"targetTexts": target_texts}


def menu_select_flow(step_id, menu_path, timeout_seconds=3, window_title_hint="", control_map_path=None):
    """通过 pywinauto 选择菜单路径（如 'File->Open'）。
    
    优先按 window_title_hint 定位目标窗口；回退到前台窗口。
    """
    menu_path = str(menu_path or "").strip()
    if not menu_path:
        _LOG_STEP(f"菜单选择失败: menuPath 为空 step={step_id}")
        return False
    try:
        deadline = time.time() + max(0.2, float(timeout_seconds or 0))
        while time.time() < deadline:
            try:
                if window_title_hint:
                    try:
                        escaped = re.escape(str(window_title_hint).strip())
                        dlg = Desktop(backend="uia").window(title_re=".*" + escaped + ".*")
                        if dlg.exists(timeout=0.3):
                            dlg.menu_select(menu_path)
                            _LOG_STEP(
                                "菜单选择成功: path={menu_path}, window={title}".format(
                                    menu_path=menu_path,
                                    title=get_wrapper_text(dlg),
                                )
                            )
                            return True
                    except Exception:
                        pass
                # 回退：前台窗口
                fore_wrapper = _try_get_window_by_handle(get_foreground_window_handle())
                if fore_wrapper is not None:
                    fore_wrapper.menu_select(menu_path)
                    _LOG_STEP(
                        "菜单选择成功 (前台窗口): path={menu_path}, title={title}".format(
                            menu_path=menu_path,
                            title=get_wrapper_text(fore_wrapper),
                        )
                    )
                    return True
            except Exception:
                pass
            time.sleep(0.2)
    except Exception as exc:
        _LOG_STEP("菜单选择异常: path={menu_path}, error={error}".format(
            menu_path=menu_path, error=exc
        ))
    return False


def value_matches(actual, expected, regex=False):
    actual_text = normalize_match_text(actual)
    expected_text = normalize_match_text(expected)
    if not expected_text:
        return True
    if regex:
        try:
            return re.search(expected_text, actual_text) is not None
        except re.error:
            return False
    return actual_text == expected_text or expected_text in actual_text


def wrapper_matches_locator(wrapper, target_method, target_value):
    methods = split_locator_parts(target_method)
    values = split_locator_parts(target_value)
    if not methods:
        return True
    if len(methods) != len(values):
        return False
    # found_index 依赖同一 locator 中的类型/类名/名称限定其兄弟范围（父链引导）
    scope_method, scope_value = "", ""
    for scope_candidate_method, scope_candidate_value in zip(methods, values):
        if scope_candidate_method.strip() in {"control_type", "class_name", "name"}:
            scope_method, scope_value = scope_candidate_method.strip(), scope_candidate_value
            break
    for method, expected in zip(methods, values):
        method = method.strip()
        if method == "automation_id":
            if not value_matches(get_wrapper_automation_id(wrapper), expected):
                return False
        elif method == "name":
            if value_matches(get_wrapper_text(wrapper), expected):
                continue
            # 控件自身 UIA Name 为空时（如 Telerik 多选下拉 CheckBox，等级文本在子
            # TextBlock 上），回退到运行时文本候选（含子节点文本）匹配，避免 name
            # 定位失配把同一 automationId 的多实例全部选中导致点错等级。
            if any(value_matches(t, expected) for t in get_wrapper_runtime_text_candidates(wrapper)):
                continue
            return False
        elif method == "class_name":
            if not value_matches(get_wrapper_class_name(wrapper), expected):
                return False
        elif method == "control_type":
            if not value_matches(get_wrapper_control_type(wrapper), expected):
                return False
        elif method == "localized_control_type":
            if not value_matches(get_wrapper_localized_control_type(wrapper), expected):
                return False
        elif method == "handle":
            if not value_matches(get_wrapper_handle_text(wrapper), expected):
                return False
        elif method == "framework_id":
            if not value_matches(get_wrapper_framework_id(wrapper), expected):
                return False
        elif method == "help_text":
            if not value_matches(get_wrapper_help_text(wrapper), expected):
                return False
        elif method == "ui_path":
            recorded = _parse_recorded_uipath(expected)
            if not recorded:
                return False
            actual = _build_wrapper_path_signature(wrapper, depth=max(len(recorded) + 1, 8))
            if len(recorded) > len(actual):
                return False
            # recorded 为根->叶顺序，actual 为叶->根顺序；对两者取"叶对齐"的尾部比对
            for rec_seg, act_seg in zip(reversed(recorded), actual):
                rec_name, rec_type = rec_seg
                act_name, act_type = act_seg
                if rec_name and act_name and rec_name != act_name:
                    return False
                if rec_type and act_type and rec_type != act_type:
                    return False
        elif method == "process_id":
            if not value_matches(get_wrapper_process_id(wrapper), expected):
                return False
        elif method == "regex":
            if not value_matches(get_wrapper_text(wrapper), expected, regex=True):
                return False
        elif method == "found_index":
            try:
                expected_index = int(str(expected).strip())
            except (TypeError, ValueError):
                return False
            if get_wrapper_found_index(wrapper, scope_method, scope_value) != expected_index:
                return False
        elif method == "label_text":
            if not wrapper_matches_label_text(wrapper, expected):
                return False
        elif method == "template_key":
            actual_template_text = get_wrapper_text(wrapper) or get_wrapper_automation_id(wrapper)
            if not value_matches(actual_template_text, expected):
                return False
        elif method == "template":
            # template ??????????UIA ?????????????????
            pass
        else:
            return False
    return True


def build_common_locator_candidates(control_definition):
    control_definition = control_definition if isinstance(control_definition, dict) else {}
    inspect_data = (
        control_definition.get("inspectData", {})
        if isinstance(control_definition.get("inspectData"), dict)
        else {}
    )
    candidates = []

    def add_candidate(method, values):
        locator_method, locator_value = build_locator_text(method, values)
        if locator_method and locator_value and (locator_method, locator_value) not in candidates:
            candidates.append((locator_method, locator_value))

    add_candidate(control_definition.get("targetMethod", ""), split_locator_parts(control_definition.get("targetValue", "")))

    automation_id = normalize_match_text(inspect_data.get("automationId", ""))
    name = normalize_match_text(inspect_data.get("name", ""))
    class_name = normalize_match_text(inspect_data.get("className", ""))
    framework_id = normalize_match_text(inspect_data.get("frameworkId", ""))
    control_type = normalize_control_type_name(
        normalize_match_text(inspect_data.get("controlType", "")),
        normalize_match_text(inspect_data.get("localizedControlType", "")),
    )
    allow_class_name_fallback = bool(class_name) and not is_generic_locator_class_name(class_name)

    add_candidate("automation_id,control_type", [automation_id, control_type])
    add_candidate("automation_id", [automation_id])

    # 完整 UIA 路径作为唯一选择器（#7）：路径深度足够时按父链硬匹配，
    # 比仅靠 name 更稳定，可唯一确定界面上的控件（优先级高于 name 回退）。
    ui_path = normalize_match_text(control_definition.get("uiPath", ""))
    if ui_path and len(_parse_recorded_uipath(ui_path)) >= 2:
        add_candidate("ui_path", [ui_path])

    # name 候选始终作为低优先级自愈回退：即便控件已记录 automationId，
    # 当其失效（如版本升级改名/改 id）时仍可借稳定的 name 找回控件。
    if name:
        add_candidate("name,control_type", [name, control_type])
        add_candidate("name", [name])

    # labelText / relatedLabelName ?????????????? automation_id/name?
    label_text = normalize_match_text(
        control_definition.get("labelText", "")
        or inspect_data.get("labelText", "")
        or control_definition.get("relatedLabelName", "")
        or inspect_data.get("relatedLabelName", "")
    )
    if label_text:
        if control_type:
            add_candidate("label_text,control_type", [label_text, control_type])
        add_candidate("label_text", [label_text])

    # template ???????? UIA ???????? name/template_key ?????????
    template_key = normalize_match_text(
        control_definition.get("templateKey", "")
        or inspect_data.get("templateKey", "")
    )
    if str(control_definition.get("targetMethod", "")).strip().lower() == "template" or template_key:
        top_name = normalize_match_text(
            control_definition.get("name", "")
            or control_definition.get("displayName", "")
        )
        if top_name:
            add_candidate("name", [top_name])
            if control_type:
                add_candidate("name,control_type", [top_name, control_type])
        if template_key:
            add_candidate("template_key", [template_key])

    if not automation_id and not name and allow_class_name_fallback:
        add_candidate("class_name,control_type", [class_name, control_type])
        add_candidate("class_name", [class_name])
        add_candidate("framework_id,class_name,control_type", [framework_id, class_name, control_type])

    # 父容器内同级序号：最低优先级回退，仅当 id/name 全部失效时才用来消歧
    # "一排同类控件"（如列表项版本升级后 automationId 从 _5 变 _6、name 通用）。
    try:
        found_index_int = int(str(inspect_data.get("foundIndex", inspect_data.get("found_index", ""))).strip())
    except (TypeError, ValueError):
        found_index_int = -1
    if found_index_int >= 0:
        if control_type:
            add_candidate("control_type,found_index", [control_type, str(found_index_int)])
        elif allow_class_name_fallback:
            add_candidate("class_name,found_index", [class_name, str(found_index_int)])
    return candidates


def get_control_definition_match_score(wrapper, control_definition):
    control_definition = control_definition if isinstance(control_definition, dict) else {}
    inspect_data = (
        control_definition.get("inspectData", {})
        if isinstance(control_definition.get("inspectData"), dict)
        else {}
    )
    locator_candidates = build_common_locator_candidates(control_definition)
    base_score = -1
    for priority, (target_method, target_value) in enumerate(locator_candidates):
        if wrapper_matches_locator(wrapper, target_method, target_value):
            candidate_score = 120 - priority * 10
            if target_method == "template":
                # ??????? UIA ??????????????????????
                candidate_score = 0
            base_score = max(base_score, candidate_score)
    # PART_ContentHost 的 Raw View 类型不稳定：WPF 内部宿主采集时为 Pane，
    # 运行时可能报告为 Edit 等其它类型（控件库 inspectData 即记录为 Edit），
    # 按 control_type 严格候选必然失配。当 automation_id 精确命中
    # PART_ContentHost 时放宽 control_type 候选，交由 labelText/uiPath 消歧。
    if base_score < 0 and normalize_match_text(inspect_data.get("automationId", "")) == "PART_ContentHost":
        if get_wrapper_automation_id(wrapper) == "PART_ContentHost":
            for _priority, (_method, _value) in enumerate(locator_candidates):
                if _method == "automation_id" and normalize_match_text(_value) == "PART_ContentHost":
                    base_score = 120 - _priority * 10
                    break
    if locator_candidates and base_score < 0:
        return -1
    if not locator_candidates:
        base_score = 0

    score = base_score
    field_bonus_rules = [
        (get_wrapper_automation_id(wrapper), inspect_data.get("automationId", ""), 14),
        (get_wrapper_text(wrapper), inspect_data.get("name", ""), 10),
        (
            get_wrapper_control_type(wrapper),
            normalize_control_type_name(inspect_data.get("controlType", ""), inspect_data.get("localizedControlType", "")),
            8,
        ),
        (get_wrapper_class_name(wrapper), inspect_data.get("className", ""), 6),
        (get_wrapper_framework_id(wrapper), inspect_data.get("frameworkId", ""), 4),
        (get_wrapper_localized_control_type(wrapper), inspect_data.get("localizedControlType", ""), 3),
        (get_wrapper_process_id(wrapper), inspect_data.get("processId", ""), 2),
    ]
    for actual, expected, bonus in field_bonus_rules:
        if normalize_match_text(expected) and value_matches(actual, expected):
            score += bonus

    label_text_expected = normalize_match_text(
        control_definition.get("labelText", "")
        or inspect_data.get("labelText", "")
        or control_definition.get("relatedLabelName", "")
        or inspect_data.get("relatedLabelName", "")
    )
    # label_text 全树扫描仅允许在 targetMethod 硬性含 label_text 时发生；
    # 软加分路径（targetMethod 不含 label_text）用 allow_full_scan=False，
    # 只做自身/兄弟/子文本廉价匹配，避免巨大 WPF 窗口全树扫描 20-36s
    # （step_2 创建综合按钮 labelText=综合2 而 targetMethod=automation_id,
    # control_type，此前每次定位都触发全树扫描导致卡 25s）。
    _label_full_scan = "label_text" in {m.strip() for m in split_locator_parts(str(control_definition.get("targetMethod", "")))}
    if label_text_expected and wrapper_matches_label_text(wrapper, label_text_expected, allow_full_scan=_label_full_scan):
        score += 12
    # label_text 硬性消歧：当 targetMethod 明确含 label_text 时（如
    # "textbox,Edit,50年回归风速（m/s)"），label 不匹配的候选必须否决，
    # 不能只靠 +12 软加分区分。否则同父容器多个同名/相近 textbox（如
    # 50年/100年回归风速都 automationId=textbox）中，label 不匹配的控件
    # 因 base_score 已达 120+ 而被 fast 阶段提前返回误命中。
    elif "label_text" in {m.strip() for m in split_locator_parts(str(control_definition.get("targetMethod", "")))}:
        return -1

    # helpText 消歧加分：helpText 是控件自身 UIA 属性（本地化资源真实功能名），
    # 当 label_text 面板标题匹配因树结构变化落空时，功能名匹配仍能打破同 automationId
    # 模板复制控件（各面板 Edit/Delete/Add 按钮）的平局。
    function_text_expected = _control_function_text(control_definition)
    if function_text_expected and value_matches(get_wrapper_help_text(wrapper), function_text_expected):
        score += 12

    # uiPath 父级消歧：AutomationId 相同的同名控件（如各面板的"添加"按钮）
    # 依赖父链名称段区分；父链匹配 +30、不匹配 -15，打破 automation_id 平局。
    ui_path = normalize_match_text(control_definition.get("uiPath", ""))
    if ui_path and len(_parse_recorded_uipath(ui_path)) >= 2:
        recorded = _parse_recorded_uipath(ui_path)
        actual = _build_wrapper_path_signature(wrapper, depth=3)
        name_mismatch = False
        checked = 0
        # 叶对齐：reversed(recorded) 与 actual 均为叶->根顺序
        for rec_seg, act_seg in zip(reversed(recorded), actual):
            rec_name, rec_type = rec_seg
            act_name, act_type = act_seg
            if not rec_name and not rec_type:
                continue
            checked += 1
            if rec_name and act_name and not value_matches(act_name, rec_name):
                name_mismatch = True
                break
            if rec_type and act_type and not value_matches(act_type, rec_type):
                name_mismatch = True
                break
        if checked >= 1:
            if name_mismatch:
                score -= 15
            else:
                score += 30

    for line in control_definition.get("auxChecks", []):
        key, expected = parse_aux_check_line(line)
        if not key or not expected:
            continue
        aux_lookup = {
            "IsEnabled": get_wrapper_is_enabled(wrapper),
            "IsOffscreen": get_wrapper_is_offscreen(wrapper),
            "IsKeyboardFocusable": get_wrapper_is_keyboard_focusable(wrapper),
            "HasKeyboardFocus": get_wrapper_has_keyboard_focus(wrapper),
            "FrameworkId": get_wrapper_framework_id(wrapper),
            "ClassName": get_wrapper_class_name(wrapper),
            "AutomationId": get_wrapper_automation_id(wrapper),
            "ControlType": get_wrapper_control_type(wrapper),
            "LocalizedControlType": get_wrapper_localized_control_type(wrapper),
            "HelpText": get_wrapper_help_text(wrapper),
            "ProcessId": get_wrapper_process_id(wrapper),
        }
        if key in aux_lookup and value_matches(aux_lookup[key], expected):
            score += 1

    expected_ancestors = inspect_data.get("ancestors", []) if isinstance(inspect_data.get("ancestors", []), list) else []
    if expected_ancestors:
        actual_ancestors = get_wrapper_parent_signatures(wrapper)
        ancestor_matched = any(
            any(value_matches(actual_item, expected_item) for actual_item in actual_ancestors)
            for expected_item in expected_ancestors
            if normalize_match_text(expected_item)
        )
        if ancestor_matched:
            score += 6

    # 父容器内同级序号消歧：在 name/type 完全相同的兄弟中，只有序号命中者获加分，
    # 使"第 N 个"从并列中胜出（弥补 ui_path/name 无法区分同类同名控件的缺口）。
    try:
        found_index_expected = int(str(inspect_data.get("foundIndex", inspect_data.get("found_index", ""))).strip())
    except (TypeError, ValueError):
        found_index_expected = -1
    if found_index_expected >= 0:
        control_type_scope = normalize_control_type_name(
            inspect_data.get("controlType", ""), inspect_data.get("localizedControlType", "")
        )
        if control_type_scope:
            found_scope_method, found_scope_value = "control_type", control_type_scope
        else:
            found_scope_method, found_scope_value = "class_name", normalize_match_text(inspect_data.get("className", ""))
        actual_found_index = get_wrapper_found_index(wrapper, found_scope_method, found_scope_value)
        if actual_found_index >= 0 and actual_found_index == found_index_expected:
            score += 12

    expected_children = inspect_data.get("children", []) if isinstance(inspect_data.get("children", []), list) else []
    if expected_children:
        actual_children = get_wrapper_child_signatures(wrapper)
        child_matched = any(
            any(value_matches(actual_item, expected_item) for actual_item in actual_children)
            for expected_item in expected_children
            if normalize_match_text(expected_item)
        )
        if child_matched:
            score += 24
        else:
            score -= 18
    return score


def get_wrapper_top_level_window(wrapper, depth=8):
    if wrapper is None:
        return None
    current = wrapper
    last = wrapper
    for _ in range(max(1, int(depth))):
        parent = _safe_get_value(lambda: current.parent(), None)
        if parent is None:
            break
        last = parent
        current = parent
        if get_wrapper_control_type(parent) == "Window":
            return parent
    return last


def wrapper_matches_expected_window_title(wrapper, expected_window_title):
    title_candidates = parse_window_title_candidates(expected_window_title)
    if not title_candidates:
        return True
    top_level_window = get_wrapper_top_level_window(wrapper)
    actual_title = get_wrapper_text(top_level_window)
    if any(candidate in actual_title for candidate in title_candidates):
        return True
    actual_framework = get_wrapper_framework_id(top_level_window or wrapper)
    actual_class_name = get_wrapper_class_name(top_level_window or wrapper)
    if (
        not actual_title
        and actual_framework == "WPF"
        and (
            actual_class_name == "Window"
            # HwndWrapper[MUPSmartClient.exe;;<GUID>] 的 GUID 随安装/机器变化，
            # 只按进程名子串匹配，避免换机后窗口匹配静默失效
            or "MUPSmartClient" in actual_class_name
        )
    ):
        return True
    return False


def wrapper_matches_control_definition(wrapper, control_definition):
    return get_control_definition_match_score(wrapper, control_definition) > 0


def score_control_match(wrapper, control_definition):
    score = get_control_definition_match_score(wrapper, control_definition)
    if score < 0:
        return score
    expected_window_title = normalize_match_text(control_definition.get("windowTitle", ""))
    # "*" / "__all__" 表示不约束窗口标题（控件位于主窗口内、标题为控件库分类名时使用）
    if uipath_is_main_window_root(control_definition.get("uiPath", "")):
        expected_window_title = "*"
    if expected_window_title and expected_window_title not in {"*", "__all__", "__ALL__"}:
        if wrapper_matches_expected_window_title(wrapper, expected_window_title):
            score += 24
        else:
            score -= 160
    if _safe_get_value(lambda: wrapper.is_visible(), False):
        score += 1
    if _safe_get_value(lambda: wrapper.is_enabled(), False):
        score += 1
    return score


def get_control_process_candidates(control_definition):
    process_candidates = []
    if not isinstance(control_definition, dict):
        return process_candidates
    inspect_data = control_definition.get("inspectData", {}) or {}
    # 数字 PID：每次运行会变化（MUP 重启后进程号不同），不作为硬过滤候选，
    # 否则按旧 PID 过滤会把新进程的窗口全过滤掉（窗口枚举 count=0）。
    # 仅保留非数字进程标识（如名称/十六进制）作为候选。
    for value in [inspect_data.get("processId", ""), inspect_data.get("process_id", "")]:
        candidate = normalize_match_text(value)
        if not candidate or candidate.isdigit():
            continue
        if candidate not in process_candidates:
            process_candidates.append(candidate)
    methods = split_locator_parts(control_definition.get("targetMethod", ""))
    values = split_locator_parts(control_definition.get("targetValue", ""))
    for method, value in zip(methods, values):
        if method.strip() != "process_id":
            continue
        candidate = normalize_match_text(value)
        if candidate and candidate not in process_candidates:
            process_candidates.append(candidate)
    return process_candidates


def get_foreground_window_handle():
    try:
        return int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _try_get_window_by_handle(handle):
    try:
        if not handle:
            return None
        # 句柄可能来自两种来源：int（GetForegroundWindow / element_info.handle）或
        # "0x…" 十六进制字符串（_enum_visible_mup_win32_windows 枚举侧 hex() 格式化）。
        # int(x, 0) 同时兼容十进制与 0x 前缀，修复"严格标题无命中 → 回退 MUP 可见窗口
        # 候选"分支因 int("0x…") 抛 ValueError 被吞而恒为空（回退永远只剩前台窗口单候选）。
        handle_num = int(handle, 0) if isinstance(handle, str) else int(handle)
        return Desktop(backend="uia").window(handle=handle_num)
    except Exception:
        return None


def wrap_window_by_handle(handle):
    """按窗口句柄把目标顶层窗口包装为 UIA wrapper（公开入口，供编辑器等外部模块使用）。

    只包目标窗口自身子树，不做全桌面 UIA 枚举，规避整树拉取时的原生崩溃面；
    包装失败（句柄无效 / UIA 不可达）返回 None。
    """
    return _try_get_window_by_handle(handle)


def _is_more_specific_window(candidate_rect, base_rect):
    candidate_rect = candidate_rect if isinstance(candidate_rect, dict) else {}
    base_rect = base_rect if isinstance(base_rect, dict) else {}
    candidate_area = int(candidate_rect.get("width", 0) or 0) * int(candidate_rect.get("height", 0) or 0)
    base_area = int(base_rect.get("width", 0) or 0) * int(base_rect.get("height", 0) or 0)
    return candidate_area > 0 and (base_area <= 0 or candidate_area < base_area)


def _rect_contains(outer_rect, inner_rect):
    outer_rect = outer_rect if isinstance(outer_rect, dict) else {}
    inner_rect = inner_rect if isinstance(inner_rect, dict) else {}
    if not outer_rect or not inner_rect:
        return False
    return (
        int(outer_rect.get("left", 0)) <= int(inner_rect.get("left", 0))
        and int(outer_rect.get("top", 0)) <= int(inner_rect.get("top", 0))
        and int(outer_rect.get("right", 0)) >= int(inner_rect.get("right", 0))
        and int(outer_rect.get("bottom", 0)) >= int(inner_rect.get("bottom", 0))
    )


def _rect_area(rect):
    rect = rect if isinstance(rect, dict) else {}
    return max(0, int(rect.get("width", 0) or 0)) * max(0, int(rect.get("height", 0) or 0))


def should_replace_flow_window_candidate(candidate, candidate_score, best_match, best_score, window_spec):
    if candidate is None:
        return False
    if best_match is None:
        return True
    if candidate_score > best_score:
        return True
    if candidate_score < best_score:
        return False

    window_spec = window_spec if isinstance(window_spec, dict) else {}
    expected_title = normalize_match_text(window_spec.get("title", ""))
    expected_class = normalize_match_text(window_spec.get("className", ""))
    expected_framework = normalize_match_text(window_spec.get("frameworkId", ""))

    candidate_title = normalize_match_text(get_wrapper_text(candidate))
    best_title = normalize_match_text(get_wrapper_text(best_match))
    candidate_explicit = bool(not expected_title or value_matches(candidate_title, expected_title))
    best_explicit = bool(not expected_title or value_matches(best_title, expected_title))
    if candidate_explicit != best_explicit:
        return candidate_explicit

    # When multiple titled WPF Window candidates tie on score, prefer the outer dialog
    # frame if it meaningfully contains a smaller same-spec window. This avoids
    # foreground content hosts (same title/class/framework) shrinking relative regions.
    prefers_larger_wpf_window = expected_title and expected_class == "Window" and expected_framework == "WPF"
    if prefers_larger_wpf_window:
        candidate_rect = get_wrapper_rectangle(candidate) or {}
        best_rect = get_wrapper_rectangle(best_match) or {}
        candidate_area = _rect_area(candidate_rect)
        best_area = _rect_area(best_rect)
        if candidate_area > 0 and best_area > 0:
            if _rect_contains(candidate_rect, best_rect):
                minimum_gain = max(40000, int(best_area * 0.03))
                if candidate_area - best_area >= minimum_gain:
                    return True
            if _rect_contains(best_rect, candidate_rect):
                minimum_gain = max(40000, int(candidate_area * 0.03))
                if best_area - candidate_area >= minimum_gain:
                    return False

    candidate_handle = normalize_match_text(get_wrapper_handle_text(candidate))
    best_handle = normalize_match_text(get_wrapper_handle_text(best_match))
    if candidate_handle and best_handle:
        return candidate_handle < best_handle
    return False


def resolve_effective_relative_region_window(window, parent_window):
    if window is None:
        return None
    original_title = normalize_match_text(get_wrapper_text(window))
    original_class = normalize_match_text(get_wrapper_class_name(window))
    original_framework = normalize_match_text(get_wrapper_framework_id(window))
    original_process = normalize_match_text(get_wrapper_process_id(window))
    original_rect = get_wrapper_rectangle(window) or {}
    expected_title = normalize_match_text((parent_window or {}).get("title", ""))
    expected_class = normalize_match_text((parent_window or {}).get("className", ""))
    expected_framework = normalize_match_text((parent_window or {}).get("frameworkId", ""))
    focused_wrapper = _try_get_window_by_handle(get_foreground_window_handle())
    if focused_wrapper is None:
        return window
    focused_title = normalize_match_text(get_wrapper_text(focused_wrapper))
    focused_class = normalize_match_text(get_wrapper_class_name(focused_wrapper))
    focused_framework = normalize_match_text(get_wrapper_framework_id(focused_wrapper))
    focused_process = normalize_match_text(get_wrapper_process_id(focused_wrapper))
    focused_rect = get_wrapper_rectangle(focused_wrapper) or {}
    class_matches = not expected_class or focused_class == expected_class
    framework_matches = not expected_framework or focused_framework == expected_framework
    title_matches = bool(expected_title and focused_title == expected_title)
    original_class_matches = not expected_class or original_class == expected_class
    original_framework_matches = not expected_framework or original_framework == expected_framework
    original_explicit_match = bool(
        expected_title
        and original_title == expected_title
        and original_class_matches
        and original_framework_matches
    )
    same_process = bool(original_process and focused_process and original_process == focused_process)
    more_specific = _is_more_specific_window(focused_rect, original_rect)
    if expected_title:
        # Keep the originally resolved titled dialog as the anchor window once it already
        # matches the explicit parent spec; otherwise a smaller focused content window can
        # shrink the reference rect and shift all relative-region coordinates.
        original_is_generic = not original_explicit_match
    else:
        original_is_generic = (not original_title) or not _is_more_specific_window(original_rect, focused_rect)
    # If the parent window title is explicit, do not let an unrelated popup in the same
    # process hijack the relative-region click target after the first click.
    allow_process_fallback = not expected_title
    if class_matches and framework_matches and more_specific and original_is_generic and (
        title_matches or (allow_process_fallback and same_process)
    ):
        return focused_wrapper
    return window


def _perform_relative_region_click(center, click_kind):
    if click_kind == "double":
        pyautogui.doubleClick(center[0], center[1])
    elif click_kind == "right":
        pyautogui.click(center[0], center[1], button="right")
    else:
        pyautogui.click(center[0], center[1])


def is_text_like_wrapper(wrapper):
    if wrapper is None:
        return False
    control_type = normalize_match_text(get_wrapper_control_type(wrapper)).lower()
    localized_control_type = normalize_match_text(get_wrapper_localized_control_type(wrapper)).lower()
    class_name = normalize_match_text(get_wrapper_class_name(wrapper)).lower()
    return control_type == "text" or localized_control_type == "text" or class_name == "textblock"


def click_wrapper_center(wrapper, click_kind="left"):
    center = get_wrapper_center(wrapper)
    if not center:
        return False, None
    try:
        _perform_relative_region_click(center, click_kind)
    except Exception:
        return False, None
    return True, {"x": center[0], "y": center[1]}


def should_retry_click_after_focus_switch(control, foreground_before, foreground_after):
    if control is None or foreground_after is None:
        return False
    if is_automation_window(foreground_after):
        return False
    before_handle = get_wrapper_handle(foreground_before)
    after_handle = get_wrapper_handle(foreground_after)
    if before_handle and after_handle and before_handle == after_handle:
        return False
    control_process = get_wrapper_process_id(control)
    after_process = get_wrapper_process_id(foreground_after)
    if control_process and after_process and control_process != after_process:
        return False
    # ToggleButton / Expander 头等可切换控件：点击一次即切换状态，重试会再切换回去
    # （如地形区域"展开又折叠"），因此不可重试。
    if get_wrapper_toggle_state(control):
        return False
    return is_text_like_wrapper(control) or get_wrapper_is_keyboard_focusable(control) == "False"


def make_flow_window_cache_key(title_candidates, process_candidates, framework_candidates=()):
    normalized_titles = tuple(sorted(item for item in (title_candidates or []) if item))
    normalized_processes = tuple(sorted(item for item in (process_candidates or []) if item))
    normalized_frameworks = tuple(sorted(item for item in (framework_candidates or []) if item))
    return normalized_titles, normalized_processes, normalized_frameworks


def is_wrapper_alive(wrapper):
    if wrapper is None:
        return False
    try:
        handle = _safe_get_value(lambda: getattr(wrapper.element_info, "handle", 0), 0)
        if handle:
            # 句柄非空不代表窗口仍存活：窗口关闭后 element_info.handle 会保留旧句柄，
            # 若只看句柄会谎报存活——导致 "gone" 条件永远不满足、死控件继续被点击/键入。
            # 用 IsWindow 做一次廉价的真实性校验（毫秒级 Win32 调用）。
            try:
                return bool(ctypes.windll.user32.IsWindow(int(handle)))
            except Exception:
                return True  # IsWindow 调用异常时保守按原逻辑（句柄存在即视为存活）
        rectangle = wrapper.rectangle()
        return bool(rectangle)
    except Exception:
        return False


def get_cached_flow_windows(cache_key):
    entry = FLOW_WINDOW_CACHE.get(cache_key)
    if not entry:
        return []
    if (time.time() - entry.get("timestamp", 0)) > FLOW_WINDOW_CACHE_TTL_SECONDS:
        FLOW_WINDOW_CACHE.pop(cache_key, None)
        return []
    cached_windows = []
    for window in entry.get("windows", []):
        if is_wrapper_alive(window):
            cached_windows.append(window)
    if cached_windows:
        return cached_windows
    FLOW_WINDOW_CACHE.pop(cache_key, None)
    return []


def cache_flow_windows(cache_key, windows):
    valid_windows = []
    for window in windows or []:
        if window is None or not is_wrapper_alive(window):
            continue
        valid_windows.append(window)
        if len(valid_windows) >= 8:
            break
    if not valid_windows:
        return
    # 防污染：仅缓存 >=2 个窗口的结果。目标软件启动期可能只有 1 个窗口
    # （如 Meteodyn 未就绪时只剩运行器/桌面），缓存该状态会导致后续 2 秒内
    # 所有定位都拿到错误的单窗口列表（实测 step_9 因 windows=1 找不到控件）。
    if len(valid_windows) < 2:
        return
    FLOW_WINDOW_CACHE[cache_key] = {"timestamp": time.time(), "windows": valid_windows}


def make_flow_control_cache_key(step_id, control_definition, window_title_hint=""):
    control_definition = control_definition if isinstance(control_definition, dict) else {}
    # 跨步骤共享：同一控件定义（id/targetMethod/targetValue/windowTitle）的定位结果
    # 在流程顺序执行中窗口状态一致，可复用，避免每个步骤重复 FindAll 巨大窗口（实测 60s+）。
    # 去掉了 step_id，靠 TTL 控制过期，避免界面变化后长时间误命中。
    return (
        str(control_definition.get("id", "")).strip(),
        str(control_definition.get("targetMethod", "")).strip(),
        str(control_definition.get("targetValue", "")).strip(),
        str(control_definition.get("windowTitle", "")).strip(),
        str(window_title_hint or "").strip(),
    )


def get_cached_flow_control(step_id, control_definition, window_title_hint=""):
    cache_key = make_flow_control_cache_key(step_id, control_definition, window_title_hint)
    entry = FLOW_CONTROL_CACHE.get(cache_key)
    if not entry:
        return None
    if (time.time() - entry.get("timestamp", 0)) > FLOW_CONTROL_CACHE_TTL_SECONDS:
        FLOW_CONTROL_CACHE.pop(cache_key, None)
        return None
    wrapper = entry.get("wrapper")
    if not is_wrapper_alive(wrapper):
        FLOW_CONTROL_CACHE.pop(cache_key, None)
        return None
    if not wrapper_matches_control_definition(wrapper, control_definition):
        FLOW_CONTROL_CACHE.pop(cache_key, None)
        return None
    expected_window_title = (
        normalize_match_text(control_definition.get("windowTitle", ""))
        or normalize_match_text(((_GET_STEP_DEFINITION(step_id) or {}).get("windowTitle", "")))
        or normalize_match_text(window_title_hint)
    )
    if expected_window_title and not wrapper_matches_expected_window_title(wrapper, expected_window_title):
        FLOW_CONTROL_CACHE.pop(cache_key, None)
        return None
    return wrapper


def cache_flow_control(step_id, control_definition, wrapper, window_title_hint=""):
    if wrapper is None or not is_wrapper_alive(wrapper):
        return
    cache_key = make_flow_control_cache_key(step_id, control_definition, window_title_hint)
    FLOW_CONTROL_CACHE[cache_key] = {"timestamp": time.time(), "wrapper": wrapper}


def get_wrapper_handle(wrapper):
    return _safe_get_value(lambda: getattr(wrapper.element_info, "handle", 0), 0)


_MUP_WINDOW_KEYWORDS = ("mupsmartclient", "smartclient", "meteodyn", "univwrse")


def _format_window_handle_text(handle):
    try:
        handle = int(handle or 0)
        return hex(handle) if handle else ""
    except Exception:
        return normalize_match_text(handle)


def _get_process_id_from_handle(handle):
    try:
        process_id = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(int(handle), ctypes.byref(process_id))
        return int(process_id.value or 0)
    except Exception:
        return 0


def _get_window_title_from_handle(handle):
    try:
        buffer = ctypes.create_unicode_buffer(512)
        ctypes.windll.user32.GetWindowTextW(int(handle), buffer, 512)
        return buffer.value
    except Exception:
        return ""


def _get_process_image_name_from_pid(pid):
    try:
        process_id = int(pid or 0)
        if not process_id:
            return ""
        kernel32 = ctypes.windll.kernel32
        open_process = kernel32.OpenProcess
        open_process.restype = wintypes.HANDLE
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        process_handle = open_process(0x1000, False, process_id)
        if not process_handle:
            return ""
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(ctypes.sizeof(buffer))
            query_full_process_image_name = getattr(kernel32, "QueryFullProcessImageNameW", None)
            if query_full_process_image_name:
                query_full_process_image_name.restype = wintypes.BOOL
                query_full_process_image_name.argtypes = [
                    wintypes.HANDLE,
                    wintypes.DWORD,
                    wintypes.LPWSTR,
                    ctypes.POINTER(wintypes.DWORD),
                ]
                if query_full_process_image_name(process_handle, 0, buffer, ctypes.byref(size)):
                    return buffer.value
        finally:
            close_handle = kernel32.CloseHandle
            close_handle.restype = wintypes.BOOL
            close_handle.argtypes = [wintypes.HANDLE]
            close_handle(process_handle)
    except Exception:
        return ""
    return ""


def iter_visible_top_level_windows():
    """纯 Win32 枚举所有可见顶层窗口（不触碰 UIA），供 win32-first 定位与编辑器窗口列表使用。

    返回 [{hwnd:int, title, className, processId:str, processName}]，顺序为 EnumWindows
    的 z-order 近似；单窗口读取失败静默跳过。避开 Desktop(backend="uia").windows()
    全桌面 UIA 枚举这一原生崩溃高发面（跨完整性/异常 Provider 会直接炸进程，
    Python try/except 兜不住）。
    """
    windows = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    @enum_proc
    def _callback(hwnd, _lparam):
        try:
            if not ctypes.windll.user32.IsWindowVisible(int(hwnd)):
                return True
            class_buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
            process_id = _get_process_id_from_handle(hwnd)
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "title": _get_window_title_from_handle(hwnd),
                    "className": class_buf.value or "",
                    "processId": str(process_id) if process_id else "",
                    "processName": _get_process_image_name_from_pid(process_id),
                }
            )
        except Exception:
            return True
        return True

    try:
        # 注意：必须传入回调实例 _callback，而不是类型工厂 enum_proc。
        # 传 enum_proc 会抛 ctypes.ArgumentError 被 except 吞掉，导致函数永远返回 []，
        # MUP 窗口进不了候选（实测"窗口过滤严格无命中，回退前置窗口单候选"反复出现）。
        ctypes.windll.user32.EnumWindows(_callback, 0)
    except Exception:
        return []
    return windows


def _enum_visible_mup_win32_windows():
    """MUP 关键词过滤的可见顶层窗口（复用 iter_visible_top_level_windows，纯 Win32）。"""
    windows = []
    for info in iter_visible_top_level_windows():
        class_name = (info.get("className") or "").lower()
        process_name = (info.get("processName") or "").lower()
        matched = any(keyword in class_name for keyword in _MUP_WINDOW_KEYWORDS) or any(
            keyword in process_name for keyword in _MUP_WINDOW_KEYWORDS
        )
        if not matched:
            continue
        windows.append(
            {
                "hwnd": _format_window_handle_text(int(info.get("hwnd") or 0)),
                "title": info.get("title") or "",
                "className": info.get("className") or "",
                "processId": info.get("processId") or "",
                "processName": info.get("processName") or "",
            }
        )
    return windows


def _wrap_win32_window_candidates(title_candidates, process_candidates, foreground_handle, include_all=False):
    """win32-first 窗口候选：按标题/进程/前台过滤可见顶层窗，再逐个按句柄包 UIA。

    仅当存在标题或进程候选时使用（避开全桌面 UIA 枚举）。过滤语义与
    iter_flow_search_windows 评分循环的剔除条件一致：标题子串命中 / 进程 PID 命中 /
    前台窗口 三者满足其一才进入候选；无标题候选时（仅有进程候选）也按此收窄，
    避免枚举无关窗口。包装失败（UIA 不可达）的窗口静默跳过。

    include_all=True 时不按标题/进程/前台收窄，返回全部可见顶层窗的 UIA wrapper，
    供"无标题候选"的兜底路径使用（由下游 framework 过滤与评分剔除无关窗口）。
    """
    wrapped = []
    for info in iter_visible_top_level_windows():
        title = (info.get("title") or "").strip()
        process_id = info.get("processId") or ""
        handle = int(info.get("hwnd") or 0)
        matched_title = any(candidate in title for candidate in title_candidates) if title_candidates else False
        matched_process = process_id in process_candidates if process_candidates else False
        if not include_all and not matched_title and not matched_process and handle != foreground_handle:
            continue
        window = _try_get_window_by_handle(handle)
        if window is None:
            continue
        wrapped.append(window)
    return wrapped


def detect_uia_content_blocked(uia_windows):
    """检测 UIPI/完整性级别隔离：UIA 枚举不到 MUP 窗口但 Win32 能看见。"""
    now = time.time()
    cached = _UIPI_BLOCK_CACHE.get("last")
    if cached and (now - cached.get("timestamp", 0)) <= FLOW_UIPI_BLOCK_CACHE_TTL_SECONDS:
        return bool(cached.get("blocked")), cached.get("diagnostic")
    win32_windows = _enum_visible_mup_win32_windows()
    blocked = False
    diagnostic = None
    if win32_windows:
        uia_process_ids = set()
        for window in uia_windows or []:
            pid = normalize_match_text(get_wrapper_process_id(window))
            if pid:
                uia_process_ids.add(pid)
        mup_windows = [w for w in win32_windows if w.get("processId")]
        missing_pids = sorted(
            {w["processId"] for w in mup_windows if w["processId"] not in uia_process_ids}
        )
        if missing_pids:
            # 误检面收窄：仅当"缺失 PID 的 MUP 窗口"占多数才判隔离，避免 UIA 瞬时
            # 枚举失败或存在辅助进程窗口（托盘/崩溃框）时把正常状态误判为 UIPI 隔离
            missing_window_count = sum(
                1 for w in mup_windows if w["processId"] not in uia_process_ids
            )
            if missing_window_count > len(mup_windows) / 2:
                blocked = True
                diagnostic = {
                    "reason": "uipi_uia_content_blocked",
                    "uiaWindowCount": len(uia_windows or []),
                    "uiaProcessIds": sorted(uia_process_ids),
                    "missingProcessIds": missing_pids,
                    "win32MupWindows": win32_windows[:8],
                }
    _UIPI_BLOCK_CACHE["last"] = {"timestamp": now, "blocked": blocked, "diagnostic": diagnostic}
    return blocked, diagnostic


def _process_integrity_tier(pid):
    """返回进程完整性级别（'high'/'medium'/'low'/''）；读取失败返回空串（未知）。

    用于诊断 UIPI 内容隔离：MUP 常以管理员（High 完整性）运行，工具若以普通权限
    （Medium）运行，UIA 跨完整性读取内容树会被系统拦截 —— 窗口能找到但子树为空。
    """
    try:
        import re as _re
        import win32api as _win32api
        import win32security as _win32security
        if not pid:
            return ""
        h = _win32api.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            th = _win32security.OpenProcessToken(h, _win32security.TOKEN_QUERY)
            info = _win32security.GetTokenInformation(th, _win32security.TokenIntegrityLevel)
            sid = info[0]
            s = _win32security.ConvertSidToStringSid(sid)
            m = _re.search(r"S-1-16-(\d+)", s or "")
            rid = int(m.group(1)) if m else 0
        finally:
            try:
                _win32api.CloseHandle(h)
            except Exception:
                pass
        if rid >= 0x3000:
            return "high"
        if rid >= 0x2000:
            return "medium"
        if rid > 0:
            return "low"
        return ""
    except Exception:
        return ""


def _detect_higher_integrity_windows(windows):
    """定位全部失败后，识别"目标窗口进程完整性高于本进程"的 UIPI 隔离窗口。

    症状：窗口枚举能找到（Win32 可见 + 按句柄包 UIA 成功），但子树 UIA 内容被隔离
    （descendants/children 均为空），控件匹配 0 候选，表现为静默"未命中控件"。
    根因是 UIPI 完整性隔离，不是定位器回归。返回 [(pid, process_name), ...]；
    本进程已是高完整性或无法读取完整级别时返回 []（不做误判）。
    """
    out = []
    self_tier = _process_integrity_tier(os.getpid())
    if self_tier not in ("medium", "low"):
        return out
    seen_pids = set()
    for w in windows or []:
        pid = normalize_match_text(get_wrapper_process_id(w))
        if not pid or pid in seen_pids:
            continue
        seen_pids.add(pid)
        if _process_integrity_tier(pid) == "high":
            out.append((pid, _get_process_image_name_from_pid(pid) or ""))
    return out


def parse_uipath_segments(path_text):
    return [str(item).strip() for item in str(path_text or "").split(">") if str(item).strip()]


def get_parent_hint_candidates(control_definition):
    control_definition = control_definition if isinstance(control_definition, dict) else {}
    inspect_data = (
        control_definition.get("inspectData", {})
        if isinstance(control_definition.get("inspectData"), dict)
        else {}
    )
    candidates = []
    for item in inspect_data.get("ancestors", []):
        normalized = normalize_match_text(item)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    ui_path_segments = parse_uipath_segments(control_definition.get("uiPath", ""))
    for segment in ui_path_segments[:-1]:
        normalized = normalize_match_text(segment)
        if normalized and normalized not in candidates:
            candidates.append(normalized)
    return candidates


# #region ui_path unique selector (#7)
# pywinauto_recorder 录制出的完整 UIA 路径（从根到叶）本身即是一个"不依赖坐标的唯一选择器"。
# 这里把它做成定位器里的一种硬匹配方法：沿控件父链重建实际路径，与录制路径做尾部（叶子->根）比对，
# 命中即唯一确定控件，避免仅靠 name 在界面上命中错控件。

def _strip_uipath_coords(segment_text):
    """去掉录制路径段末尾的坐标后缀，如 '%(-12,-34)'。"""
    text = str(segment_text or "").strip()
    match = re.search(r'%\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)$', text)
    if not match:
        return text
    return text[: match.start()].strip()


def _split_uipath_segments_any(path_text):
    """兼容 pywinauto_recorder 的 '->'、定位器内部的 '>' 以及 '/' 三种路径分隔符。"""
    raw = str(path_text or "")
    for sep in ("->", ">", "/"):
        if sep in raw:
            return [item.strip() for item in raw.split(sep) if item.strip()]
    return [raw.strip()] if raw.strip() else []


def _parse_recorded_uipath(uipath):
    """把录制路径解析为 [(name, control_type), ...] 列表（叶子在前）。"""
    segments = []
    for part in _split_uipath_segments_any(uipath):
        part = _strip_uipath_coords(part)
        if not part:
            continue
        if "||" in part:
            name, control_type = part.rsplit("||", 1)
        else:
            name, control_type = part, ""
        name = normalize_match_text(name)
        control_type = normalize_match_text(control_type)
        segments.append((name, control_type))
    return segments


def _build_wrapper_path_signature(wrapper, depth=8):
    """从叶子控件向上重建实际 UIA 路径签名 [(name, control_type), ...]（叶子在前）。"""
    segments = []
    current = wrapper
    for _ in range(depth):
        if current is None:
            break
        name = get_wrapper_text(current)
        control_type = get_wrapper_control_type(current)
        segments.append((normalize_match_text(name), normalize_match_text(control_type)))
        current = _safe_get_value(lambda: current.parent(), None)
    return segments


# #endregion ui_path unique selector (#7)


def cache_parent_wrapper(window, parent_wrapper):
    if window is None or parent_wrapper is None or not is_wrapper_alive(parent_wrapper):
        return
    window_handle = get_wrapper_handle(window)
    if not window_handle:
        return
    cache_tokens = []
    for value in [
        get_wrapper_text(parent_wrapper),
        get_wrapper_automation_id(parent_wrapper),
        get_wrapper_class_name(parent_wrapper),
    ]:
        normalized = normalize_match_text(value)
        if normalized and normalized not in cache_tokens:
            cache_tokens.append(normalized)
    for token in cache_tokens:
        FLOW_PARENT_CACHE[(window_handle, token)] = {"timestamp": time.time(), "wrapper": parent_wrapper}


def cache_wrapper_parent_chain(window, wrapper, depth=4):
    current = wrapper
    for _ in range(max(0, int(depth))):
        current = _safe_get_value(lambda: current.parent(), None)
        if current is None:
            break
        cache_parent_wrapper(window, current)


def get_cached_parent_wrappers(window, control_definition):
    window_handle = get_wrapper_handle(window)
    if not window_handle:
        return []
    result = []
    seen_handles = set()
    for token in get_parent_hint_candidates(control_definition):
        cache_key = (window_handle, token)
        entry = FLOW_PARENT_CACHE.get(cache_key)
        if not entry:
            continue
        if (time.time() - entry.get("timestamp", 0)) > FLOW_PARENT_CACHE_TTL_SECONDS:
            FLOW_PARENT_CACHE.pop(cache_key, None)
            continue
        wrapper = entry.get("wrapper")
        if not is_wrapper_alive(wrapper):
            FLOW_PARENT_CACHE.pop(cache_key, None)
            continue
        handle_key = get_wrapper_handle(wrapper) or id(wrapper)
        if handle_key in seen_handles:
            continue
        seen_handles.add(handle_key)
        result.append(wrapper)
    return result


def build_fast_locator_queries(control_definition):
    if not isinstance(control_definition, dict):
        return []
    inspect_data = control_definition.get("inspectData", {}) or {}
    locator_map = {}
    methods = split_locator_parts(control_definition.get("targetMethod", ""))
    values = split_locator_parts(control_definition.get("targetValue", ""))
    for method, value in zip(methods, values):
        method = method.strip()
        if method in {"automation_id", "name", "class_name", "control_type", "framework_id"}:
            locator_map[method] = normalize_match_text(value)
    for method, inspect_key in [
        ("automation_id", "automationId"),
        ("name", "name"),
        ("class_name", "className"),
        ("control_type", "controlType"),
        ("framework_id", "frameworkId"),
    ]:
        if locator_map.get(method):
            continue
        value = normalize_match_text(inspect_data.get(inspect_key, ""))
        if value:
            locator_map[method] = value
    if is_generic_locator_class_name(locator_map.get("class_name", "")):
        locator_map.pop("class_name", None)
    query_candidates = []
    seen = set()
    query_fields = []
    # automation_id 优先：UIA 原生 FindAll(AutomationId) 精确、毫秒级，且不依赖 name。
    # 部分控件（图标按钮/图形按钮）的 name 是超长 SVG/Geometry 路径（如 M21032.418,...），
    # 用 name 走 pywinauto descendants(title=超长串) 每次属性比较都做字符串匹配，既慢又不稳，
    # 而稳定唯一的 automation_id 分支反被 elif 跳过 → fast 阶段空转超时掉进整树扫描。
    # （step_18 点击-返回按钮 WRAAnalysisReferenceIEC_Button_GoBack 4.7s 定位失败的根因）
    if locator_map.get("automation_id"):
        query_fields.extend(
            [
                ("automation_id", "control_type"),
                ("automation_id",),
            ]
        )
    if locator_map.get("name"):
        # name 作为补充查询（尤其无 automation_id 时）：按钮文本/下拉框标题通常唯一。
        # 但当 name 是超长 SVG path（几百到几千字符）时跳过，避免 pywinauto 慢匹配，
        # automation_id 已覆盖该场景。
        if not _is_svg_path_name(locator_map.get("name", "")):
            query_fields.extend(
                [
                    ("name", "control_type"),
                    ("name",),
                ]
            )
    elif locator_map.get("class_name") and not query_fields:
        query_fields.extend(
            [
                ("class_name", "control_type"),
                ("class_name",),
            ]
        )
    for fields in query_fields:
        query = {}
        for field in fields:
            value = locator_map.get(field, "")
            if not value:
                query = {}
                break
            query[field] = value
        if not query:
            continue
        query_key = tuple(sorted(query.items()))
        if query_key in seen:
            continue
        seen.add(query_key)
        query_candidates.append(query)
    return query_candidates


def _release_com_pointer(ptr):
    """释放 UIA COM 接口指针（IUnknown.Release，vtable 第 3 槽），失败静默。

    原始 ctypes 层拿到的 COM 指针（CreatePropertyCondition / FindAll 的结果）不会
    被 Python 自动释放，长流程数百步×重试会累积泄漏。此处按标准 IUnknown vtable
    布局调用 Release；任何异常静默，不影响主流程。
    """
    if not ptr:
        return
    try:
        import ctypes as _ct
        # ptr 是接口指针；ptr[0] 指向 vtable（函数指针数组）；vtable[2] 即 Release
        _iface = _ct.cast(ptr, _ct.POINTER(_ct.POINTER(_ct.c_void_p)))
        _release_addr = _iface[0][2]
        if not _release_addr:
            return
        _Release = _ct.cast(_release_addr, _ct.CFUNCTYPE(_ct.c_ulong, _ct.c_void_p))
        _Release(ptr)
    except Exception:
        pass


def _iter_uia_findall_by_automation_id(window, automation_id, max_results=256, label_text=""):
    """UIA 原生 FindAll(Subtree, AutomationId) 快速定位。

    pywinauto 0.6.9 的 descendants 不支持 automation_id 参数（build_condition 只认
    process/class_name/title/control_type），传它会抛 TypeError 被吞，导致 fast 定位空。
    UIA 原生 FindAll 用 AutomationId 条件直接枚举，兼容所有控件。

    label_text 可选：对泛化 automationId（如 WPF 每个 TextBox 都叫 "textbox"）做
    Raw View 兄弟标签预过滤（毫秒级）。控件定义 targetMethod 带 label_text 时传入，
    可把"全树 FindAll 返回几十个同名 Edit 再逐个完整评分"压缩为直接命中目标，
    避免 fast 阶段超时后掉进整树扫描（整树在巨大 WPF 窗口下达 9 秒+，见 step_10
    Wohler 指数 16.7s 的定位耗时）。
    返回 list（非生成器），以便 in-flight 立即释放 FindAll 的 COM 指针。
    """
    results = []
    if window is None or not automation_id:
        return results
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
    except Exception as exc:
        _record_silent_exception("uia_findall_import", exc)
        return results
    condition = None
    found = None
    walker = None
    props = {}
    label_expected = normalize_match_text(label_text)
    # label 预过滤失败时的 plain 兜底候选（WPF Raw View 兄弟标签匹配失效时，
    # 保留少量候选交给 Control View 兄弟 TextBlock 匹配兜底，避免 fast 阶段空转
    # 掉进整树扫描 9.5s；见下方循环注释）。
    plain_fallback = []
    PLAIN_FALLBACK_LIMIT = 16
    try:
        root_element = window.element_info.element
        iuia = IUIA().iuia
        uia_dll = IUIA().UIA_dll
        automation_id_prop = getattr(uia_dll, "UIA_AutomationIdPropertyId", 30011)
        condition = iuia.CreatePropertyCondition(automation_id_prop, str(automation_id))
        found = root_element.FindAll(5, condition)
        try:
            count = int(found.Length)
        except Exception as exc:
            _record_silent_exception("uia_findall_count", exc)
            count = 0
        if label_expected:
            # 需要 Raw View 兄弟标签过滤时才获取 walker/props（多数场景无 label，避免额外开销）。
            # 与 _iter_raw_view_findall_candidates 保持一致，用 RawViewWalker 属性。
            try:
                walker = IUIA().iuia.RawViewWalker
                props = _raw_view_filter_props()
            except Exception as exc:
                _record_silent_exception("uia_findall_label_init", exc)
        for i in range(min(count, max_results)):
            try:
                element = found.GetElement(i)
                if label_expected and walker is not None:
                    try:
                        label_hit = _raw_sibling_label_matches(element, label_text, walker, props)
                        # Telerik 多选下拉 CheckBox：等级文本在子节点而非兄弟，
                        # 兄弟匹配不到时回退子节点文本匹配，避免真实候选被误过滤。
                        if not label_hit:
                            label_hit = _raw_element_child_text_matches(
                                element, label_text, walker, props
                            )
                        if not label_hit:
                            # label 预过滤失败：保留前 PLAIN_FALLBACK_LIMIT 个作为 plain
                            # 兜底（与整树 _iter_raw_view_findall_candidates 的
                            # label_hits + plain_hits 策略一致），由上层
                            # wrapper_matches_control_definition 走 Control View 兄弟
                            # TextBlock 匹配（_match_sibling_text_block_label）兜底命中，
                            # 避免 WPF TextBox/TextBlock 在 Raw View 中非直接兄弟时
                            # Raw 兄弟标签误匹配失败、fast 阶段空转 4s+ 掉进整树 9.5s
                            # （实测 step_21 空气密度 Edit 14.5s 定位耗时主因）。
                            if len(plain_fallback) < PLAIN_FALLBACK_LIMIT:
                                plain_fallback.append(UIAWrapper(UIAElementInfo(element)))
                            continue
                    except Exception as exc:
                        _record_silent_exception("uia_findall_label_filter", exc)
                results.append(UIAWrapper(UIAElementInfo(element)))
            except Exception as exc:
                _record_silent_exception("uia_findall_element", exc)
        # label 命中（results 前部）优先于 plain 兜底（追加到 results 末尾）；
        # 外层 iter_fast_locator_candidates 有 seen_handles 去重，handle 重复无副作用。
        results.extend(plain_fallback)
    except Exception as exc:
        _record_silent_exception("uia_findall_root", exc)
    finally:
        _release_com_pointer(found)
        _release_com_pointer(condition)
    return results


def _is_svg_path_name(text):
    """判断控件 name 是否为超长 SVG/Geometry 路径（图标按钮的常见脏数据）。

    此类 name 是 WPF Path 的 Data 属性（M/L/C/A 命令 + 大量坐标），几百到几千字符，
    用于 pywinauto descendants(title=...) 匹配既慢又不稳；有稳定 automation_id 时应跳过。
    判定标准：长度超阈值 且 含 SVG 命令特征。
    """
    try:
        if not text:
            return False
        if len(text) < 64:
            return False
        head = text[:32]
        # SVG path 通常以 M/L/C/Q/A 命令开头，且含大量逗号/空格分隔的坐标数字
        if not (head[0] in "MmLlCcQqAa" and ("," in head or " " in head)):
            return False
        # 特征：M/C/A 命令与数字密集
        cmd_count = sum(1 for ch in text if ch in "MmCcAaZzLlQq")
        digit_ratio = sum(1 for ch in text if ch.isdigit()) / max(1, len(text))
        return cmd_count >= 4 and digit_ratio >= 0.3
    except Exception:
        return False


def _fast_locator_label_hint(control_definition):
    """从控件定义提取 labelText 快查提示。

    仅当 targetMethod 明确含 label_text 时返回（此时 fast 阶段才需要按标签消歧）；
    否则返回空串，避免对无标签需求的控件引入 Raw View 兄弟扫描开销。
    """
    try:
        if not isinstance(control_definition, dict):
            return ""
        methods = split_locator_parts(control_definition.get("targetMethod", ""))
        if "label_text" not in {m.strip() for m in methods}:
            return ""
        return (
            control_definition.get("labelText", "")
            or (control_definition.get("inspectData", {}) or {}).get("labelText", "")
            or control_definition.get("relatedLabelName", "")
            or (control_definition.get("inspectData", {}) or {}).get("relatedLabelName", "")
        )
    except Exception:
        return ""


def iter_fast_locator_candidates(window, control_definition):
    if window is None:
        return []
    result = []
    seen_handles = set()
    search_roots = [window]
    search_roots.extend(get_cached_parent_wrappers(window, control_definition))
    unique_roots = []
    seen_root_handles = set()
    for root in search_roots:
        root_handle = get_wrapper_handle(root) or id(root)
        if root_handle in seen_root_handles:
            continue
        seen_root_handles.add(root_handle)
        unique_roots.append(root)
    # 先收集 automation_id 查询候选：UIA 原生 FindAll 毫秒级，且 (automation_id,control_type)
    # 与 (automation_id) 两个 query 会重复全树 FindAll——合并为一次。若 automation_id 已
    # 精确命中少量候选（<=4），说明是唯一标识，无需再跑慢的 name/descendants 全树遍历
    # （pywinauto descendants 在巨大 WPF 窗口下达 20s+，是 25 秒定位耗时的主因）。
    automation_id_hits = []
    for query in build_fast_locator_queries(control_definition):
        if query.get("automation_id"):
            label_hint = _fast_locator_label_hint(control_definition)
            for candidate in _iter_uia_findall_by_automation_id(
                window, query.get("automation_id", ""), label_text=label_hint
            ):
                handle = _safe_get_value(lambda: getattr(candidate.element_info, "handle", None), None)
                handle_key = handle if handle not in (None, 0, "") else id(candidate)
                if handle_key in seen_handles:
                    continue
                seen_handles.add(handle_key)
                result.append(candidate)
                automation_id_hits.append(candidate)
            break  # 只跑一次 automation_id FindAll（两个 query 等价，合并）
    if automation_id_hits and len(automation_id_hits) <= 4:
        return result
    for query in build_fast_locator_queries(control_definition):
        if query.get("automation_id"):
            continue  # 已在上方处理
        kwargs = {}
        if query.get("name"):
            kwargs["title"] = query["name"]
        if query.get("class_name"):
            kwargs["class_name"] = query["class_name"]
        if query.get("control_type"):
            kwargs["control_type"] = query["control_type"]
        # 注意：build_fast_locator_queries 的 query 只含 name/control_type/automation_id/class_name，
        # framework_id 从不进入 fast 查询（pywinauto descendants 也不支持），故此处不传。
        if not kwargs:
            continue
        for root in unique_roots:
            candidates = []
            try:
                candidates.extend(root.children(**kwargs))
            except Exception as exc:
                _record_silent_exception("fast_locator_children", exc)
            if root is window or not candidates:
                try:
                    candidates.extend(root.descendants(**kwargs))
                except Exception as exc:
                    _record_silent_exception("fast_locator_descendants", exc)
            for candidate in candidates:
                handle = _safe_get_value(lambda: getattr(candidate.element_info, "handle", None), None)
                handle_key = handle if handle not in (None, 0, "") else id(candidate)
                if handle_key in seen_handles:
                    continue
                seen_handles.add(handle_key)
                result.append(candidate)
    return result


def control_definition_expects_raw_view(control_definition):
    normalized = normalize_control_definition(control_definition)
    inspect_data = normalized.get("inspectData", {})
    is_control = str(inspect_data.get("isControlElement", "")).strip().lower()
    is_content = str(inspect_data.get("isContentElement", "")).strip().lower()
    if is_control == "false" or is_content == "false":
        return True
    # 兼容：inspectData 字段缺失但 rawInspectText 已记录 IsControlElement/IsContentElement=False
    # 的控件（如主界面不可交互文本），Control View 必然不可见，必须走 Raw View。
    raw_inspect = str(normalized.get("rawInspectText", "") or inspect_data.get("rawInspectText", ""))
    if raw_inspect:
        if re.search(r"IsControlElement:\s*False", raw_inspect, re.IGNORECASE):
            return True
        if re.search(r"IsContentElement:\s*False", raw_inspect, re.IGNORECASE):
            return True
    return False


_RAW_VIEW_FILTER_PROPS = None


def _raw_view_filter_props():
    """惰性构建 Raw View 预过滤使用的 UIA 属性 id（依赖 COM 单例，避免影响模块导入）。"""
    global _RAW_VIEW_FILTER_PROPS
    if _RAW_VIEW_FILTER_PROPS is None:
        try:
            from pywinauto.uia_defines import IUIA
            uia_dll = IUIA().UIA_dll
            _RAW_VIEW_FILTER_PROPS = {
                "name": getattr(uia_dll, "UIA_NamePropertyId", 30005),
                "automation_id": getattr(uia_dll, "UIA_AutomationIdPropertyId", 30011),
                "control_type": getattr(uia_dll, "UIA_ControlTypePropertyId", 30003),
            }
        except Exception:
            _RAW_VIEW_FILTER_PROPS = {}
    return _RAW_VIEW_FILTER_PROPS


def _raw_view_control_type_id(control_type_name):
    """控件类型名 → UIA ControlType id（如 'Text' -> 50020），未知返回 None。"""
    if not control_type_name:
        return None
    try:
        from pywinauto.uia_defines import IUIA
        return getattr(IUIA().UIA_dll, "UIA_{}ControlTypeId".format(control_type_name.replace(" ", "")), None)
    except Exception:
        return None


def _raw_element_passes_prefilter(element, target_name, target_automation_id, target_type_id, props):
    """Raw View 元素廉价预过滤：仅读取 1-3 个原始 UIA 属性即判定是否可能命中。

    相比为每个元素构造 UIAWrapper 并调用完整匹配评分（实测约 86s/5000 元素），
    预过滤把每次候选判定降为 1-3 次 GetCurrentPropertyValue，数量级提速。
    无过滤条件时返回 True（交由完整匹配兜底）。
    """
    if not props or not (target_name or target_automation_id or target_type_id):
        return True
    if target_name:
        # name 最独特：目标有 name 时按 name 严格过滤（空 name 元素直接跳过）。
        # 采集时 inspectData.name 即来自 UIA Name 属性，真目标必然非空，可严格判定。
        try:
            actual_name = str(element.GetCurrentPropertyValue(props["name"]) or "").strip()
        except Exception:
            return True  # 属性读取失败不拦截，交由完整匹配判定
        n_actual = normalize_match_text(actual_name)
        if n_actual == target_name or (n_actual and target_name in n_actual):
            return True
        return False
    # type 匹配降为辅助条件：Raw View 中内部宿主（如 PART_ContentHost）的
    # ControlType 可能与采集时不一致（实测 Pane 在 Raw View 报告为其它类型），
    # 硬按 type 拦截会误杀真目标（step_9 找不到 PART_ContentHost 的根因）。
    type_matched = True
    if target_type_id is not None:
        try:
            type_matched = int(element.GetCurrentPropertyValue(props["control_type"]) or 0) == int(target_type_id)
        except Exception:
            type_matched = True
    if target_automation_id:
        try:
            actual_aid = str(element.GetCurrentPropertyValue(props["automation_id"]) or "").strip()
        except Exception:
            actual_aid = ""
        if actual_aid == target_automation_id:
            # automation_id 精确匹配即放行（忽略 type 不一致）
            return True
        if actual_aid:
            # aid 非空但不匹配：仅当 type 匹配时才留待完整匹配判定
            return type_matched
        # aid 属性在 Raw View 元素上常读取为空（内部宿主如 PART_ContentHost），
        # 不拦截，交由完整匹配判定（wrapper_matches 对无 aid 元素快速失败）
        return True
    # 无 aid：按 type 匹配
    return type_matched


def _iter_raw_view_findall_candidates(window, control_definition, max_results=128):
    """UIA 原生 FindAll(Subtree, AutomationId 条件) 快速返回深层 Raw View 宿主控件。

    手动 RawViewWalker BFS 受元素预算与深度限制：PART_ContentHost 等内部宿主
    处于很深的装饰器子树中，前级父节点多被预过滤剪枝，且 30000 元素全量遍历
    实测达数百秒。FindAll 由 UIA 原生遍历子树并直接返回 AutomationId 命中的
    元素集合，数量级提速且不受深度限制。
    """
    if window is None:
        return []
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
    except Exception as exc:
        _record_silent_exception("raw_findall_import", exc)
        # 必须返回空列表而不是 None：调用方 `for candidate in _iter_raw_view_findall_candidates(...)`
        # 直接迭代返回值，返回 None 会抛 TypeError('NoneType' object is not iterable)，
        # 使整个定位链提前失败（日志中 step_31/38/45/52/59/66 反复出现该错误）。
        return []
    normalized = normalize_control_definition(control_definition)
    inspect_data = normalized.get("inspectData", {}) or {}
    target_automation_id = normalize_match_text(inspect_data.get("automationId", ""))
    if not target_automation_id:
        # 无 automationId 时无法走 FindAll 优化：返回空列表，让调用方回退到
        # descendants 全量扫描（按 name/label/class 匹配），避免返回 None 导致
        # `for candidate in ...` 触发 TypeError（如保存对话框文件名框等占位控件）。
        return []
    condition = None
    found = None
    try:
        root_element = window.element_info.element
        iuia = IUIA().iuia
        uia_dll = IUIA().UIA_dll
        automation_id_prop = getattr(uia_dll, "UIA_AutomationIdPropertyId", 30011)
        condition = iuia.CreatePropertyCondition(automation_id_prop, target_automation_id)
        # TreeScope_Subtree = 5：自 root 起包含全部后代
        found = root_element.FindAll(5, condition)
        try:
            count = int(found.Length)
        except Exception as exc:
            _record_silent_exception("raw_findall_count", exc)
            count = 0
        seen = set()
        label_hits = []
        plain_hits = []
        try:
            walker = IUIA().iuia.RawViewWalker
        except Exception as exc:
            _record_silent_exception("raw_findall_walker", exc)
            walker = None
        props = _raw_view_filter_props()
        label_expected = normalize_match_text(
            normalized.get("labelText", "")
            or inspect_data.get("labelText", "")
            or normalized.get("relatedLabelName", "")
            or inspect_data.get("relatedLabelName", "")
        )
        budget_deadline = time.time() + 8.0  # 评分预算：巨大窗口海量候选时避免评分阶段拖垮
        for i in range(min(count, max_results)):
            if time.time() > budget_deadline:
                break
            try:
                element = found.GetElement(i)
                wrapper = UIAWrapper(UIAElementInfo(element))
            except Exception as exc:
                _record_silent_exception("raw_findall_element", exc)
                continue
            # 廉价 Raw View 兄弟标签预过滤优先：控件定义带 label_text 时，先检查
            # 候选的 Raw 兄弟是否含同名标签（毫秒级），不匹配直接跳过，避免对
            # 每个候选做完整评分（完整评分内的 _find_label_rects_for_wrapper 全树
            # 扫描在巨大 WPF 窗口下可达 20-36 秒）。label_expected 为空时跳过。
            # Telerik 多选下拉 CheckBox 的等级文本在子节点而非兄弟，兄弟匹配不到
            # 时回退子节点文本匹配，避免真实候选被误过滤。
            if label_expected and walker is not None:
                label_ok = _raw_sibling_label_matches(element, label_expected, walker, props)
                if not label_ok:
                    label_ok = _raw_element_child_text_matches(element, label_expected, walker, props)
                if not label_ok:
                    continue
            if not wrapper_matches_control_definition(wrapper, normalized):
                continue
            key = get_wrapper_handle(wrapper) or normalize_match_text(
                _safe_get_value(lambda: str(wrapper.element_info.runtime_id), "")
            ) or id(wrapper)
            if key in seen:
                continue
            seen.add(key)
            if label_expected and walker is not None:
                label_ok = _raw_sibling_label_matches(element, label_expected, walker, props)
                if not label_ok:
                    label_ok = _raw_element_child_text_matches(element, label_expected, walker, props)
                if label_ok:
                    label_hits.append(wrapper)
                else:
                    plain_hits.append(wrapper)
            else:
                plain_hits.append(wrapper)
    except Exception as exc:
        _record_silent_exception("raw_findall_root", exc)
    finally:
        # 及时释放 FindAll/Condition 的 COM 指针，避免长流程泄漏
        _release_com_pointer(found)
        _release_com_pointer(condition)
    return label_hits + plain_hits


def _raw_sibling_label_matches(element, label_text, walker, props):
    """Raw View 兄弟节点标签关联：宿主元素的兄弟中是否有 Name 等于标签文本的节点。

    搜索框 PART_ContentHost 的"查找"标签即其 Raw 兄弟；据此可在多个同名
    PART_ContentHost（每个 TextBox 一个）中精确区分目标。
    """
    expected = normalize_match_text(label_text)
    if not expected:
        return False
    try:
        parent = walker.GetParentElement(element)
    except Exception as exc:
        _record_silent_exception("raw_sibling_parent", exc)
        return False
    try:
        sibling = walker.GetFirstChildElement(parent)
        while sibling:
            try:
                name = str(
                    sibling.GetCurrentPropertyValue(props.get("name", 30005)) or ""
                ).strip()
            except Exception:
                name = ""
            if normalize_match_text(name) == expected:
                return True
            sibling = walker.GetNextSiblingElement(sibling)
    except Exception as exc:
        _record_silent_exception("raw_sibling_walk", exc)
    return False


def _raw_element_child_text_matches(element, label_text, walker, props, max_depth=2):
    """Raw View 子节点文本匹配：宿主元素的直接子 Text/TextBlock 中是否有 Name 等于标签。

    Telerik 多选下拉 CheckBox（如热稳定度各等级 MTDGroupComboBoxMultiSelection_CheckBox）
    的结构为 CheckBox > [Image, Text(等级文本)]：等级文本在 CheckBox 的**子节点**上，
    而非兄弟节点。只按兄弟匹配（_raw_sibling_label_matches）会把这些真实候选全部
    过滤掉，导致 fast 定位枚举不到任何 checkbox（日志表现为『未找到匹配控件』）。
    """
    expected = normalize_match_text(label_text)
    if not expected:
        return False
    try:
        child = walker.GetFirstChildElement(element)
        depth = 0
        stack = [(child, depth)]
        while stack:
            cur, d = stack.pop()
            if cur is None or d > max_depth:
                continue
            try:
                name = str(cur.GetCurrentPropertyValue(props.get("name", 30005)) or "").strip()
                ctype = str(cur.GetCurrentPropertyValue(props.get("control_type", 30003)) or "").strip()
            except Exception:
                name, ctype = "", ""
            if normalize_match_text(name) == expected:
                return True
            try:
                sub = walker.GetFirstChildElement(cur)
            except Exception:
                sub = None
            if sub is not None and d < max_depth:
                stack.append((sub, d + 1))
            try:
                sibling = walker.GetNextSiblingElement(cur)
            except Exception:
                sibling = None
            if sibling is not None:
                stack.append((sibling, d))
    except Exception as exc:
        _record_silent_exception("raw_child_text_walk", exc)
    return False


def _iter_raw_view_guided_candidates(window, control_definition, max_depth=6, max_elements=800):
    """uiPath 祖先类名引导的 Raw View 受限下降。

    PART_ContentHost 等 IsControlElement=False 的内部宿主不在 Control View 中，
    FindAll(Subtree) 也按 Control View 语义遍历而不可见；手动 BFS 全量遍历又达
    数百秒。改为两段式：
      1. 录制路径（uiPath）各祖先段在录制时以 className 命名，且 Control View
         子树闭包保证：只要宿主所在区域存在任一 Control View 后代（如投影列表项
         的 Text），其祖先容器必然也在 Control View 中 → 用 className 在
         window.descendants() 中定位容器；
      2. 从容器做受限深度/元素数的 Raw View 下降，直接匹配目标 AutomationId；
         命中多个同 AutomationId 宿主时，优先产出"Raw 兄弟含标签同名节点"
         （标签关联）的候选，精确区分目标 TextBox。
    """
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
    except Exception as exc:
        _record_silent_exception("raw_guided_import", exc)
        return
    if window is None:
        return
    normalized = normalize_control_definition(control_definition)
    inspect_data = normalized.get("inspectData", {}) or {}
    target_automation_id = normalize_match_text(inspect_data.get("automationId", ""))
    if not target_automation_id:
        return
    label_expected = normalize_match_text(
        normalized.get("labelText", "")
        or inspect_data.get("labelText", "")
        or normalized.get("relatedLabelName", "")
        or inspect_data.get("relatedLabelName", "")
    )
    recorded = _parse_recorded_uipath(normalized.get("uiPath", ""))
    ancestor_names = []
    for seg_name, seg_type in reversed(recorded):
        name = normalize_match_text(seg_name)
        if not name or name == target_automation_id:
            continue
        if name not in ancestor_names:
            ancestor_names.append(name)
        if len(ancestor_names) >= 4:
            break
    if not ancestor_names:
        return
    containers = []
    try:
        # 按 uiPath 祖先 className 过滤 descendants：全量枚举 + 逐个比对 className
        # 在巨大 WPF 窗口下达 20s+（step_3 PART_ContentHost 整树 24.8s 主因）。
        # pywinauto 0.6.9 的 build_condition 只支持单个 class_name（不支持 list），
        # 故对每个祖先 className 单独查询并取并集；UIA 原生条件过滤比全量枚举
        # 数量级提速（仅命中 className 的元素才构造 wrapper）。
        seen_containers = set()
        for _ancestor_name in ancestor_names:
            if not _ancestor_name:
                continue
            try:
                for cand in window.descendants(class_name=_ancestor_name):
                    key = get_wrapper_handle(cand) or normalize_match_text(
                        _safe_get_value(lambda: str(cand.element_info.runtime_id), "")
                    ) or id(cand)
                    if key in seen_containers:
                        continue
                    seen_containers.add(key)
                    containers.append(cand)
            except Exception as _exc:
                _record_silent_exception("raw_guided_containers_one", _exc)
    except Exception as exc:
        _record_silent_exception("raw_guided_containers", exc)
        try:
            for cand in window.descendants():
                if normalize_match_text(get_wrapper_class_name(cand)) in ancestor_names:
                    containers.append(cand)
        except Exception as exc2:
            _record_silent_exception("raw_guided_containers_retry", exc2)
            containers = []
    if not containers:
        return
    try:
        walker = IUIA().iuia.RawViewWalker
    except Exception as exc:
        _record_silent_exception("raw_guided_walker", exc)
        return
    props = _raw_view_filter_props()
    seen = set()
    label_hits = []
    plain_hits = []

    def _collect():
        for wrapper in label_hits:
            yield wrapper
        for wrapper in plain_hits:
            yield wrapper

    for container in containers:
        try:
            root_element = container.element_info.element
        except Exception as exc:
            _record_silent_exception("raw_guided_container_element", exc)
            continue
        queue = []
        try:
            child = walker.GetFirstChildElement(root_element)
            while child:
                queue.append((child, 1))
                child = walker.GetNextSiblingElement(child)
        except Exception as exc:
            _record_silent_exception("raw_guided_queue_init", exc)
            continue
        visited = 0
        index = 0
        while index < len(queue) and visited < max_elements:
            element, depth = queue[index]
            index += 1
            visited += 1
            if depth >= max_depth:
                continue
            try:
                child = walker.GetFirstChildElement(element)
                while child:
                    queue.append((child, depth + 1))
                    child = walker.GetNextSiblingElement(child)
            except Exception as exc:
                _record_silent_exception("raw_guided_children", exc)
            try:
                actual_aid = str(
                    element.GetCurrentPropertyValue(props.get("automation_id", 30011)) or ""
                ).strip()
            except Exception as exc:
                _record_silent_exception("raw_guided_aid", exc)
                continue
            if normalize_match_text(actual_aid) != target_automation_id:
                continue
            try:
                wrapper = UIAWrapper(UIAElementInfo(element))
            except Exception as exc:
                _record_silent_exception("raw_guided_wrapper", exc)
                wrapper = None
            if wrapper is None or not wrapper_matches_control_definition(wrapper, normalized):
                continue
            key = get_wrapper_handle(wrapper) or normalize_match_text(
                _safe_get_value(lambda: str(wrapper.element_info.runtime_id), "")
            ) or id(wrapper)
            if key in seen:
                continue
            seen.add(key)
            if label_expected and _raw_sibling_label_matches(element, label_expected, walker, props):
                label_hits.append(wrapper)
            else:
                plain_hits.append(wrapper)
    yield from _collect()


def iter_raw_view_fallback_candidates(window, control_definition, max_elements=30000, budget_seconds=15.0):
    """Raw View 兜底：枚举窗口 Raw View 树中匹配控件定义的候选（生成器）。

    原实现为一次性返回 list，须等整棵 BFS 扫完（WPF 大树下数千元素 × 完整评分
    可达数十秒）；现改为生成器 + 廉价属性预过滤：
      1. 每个元素先做 1-3 次 GetCurrentPropertyValue 预过滤，明显不匹配的跳过，
         不再构造 UIAWrapper、不调用完整匹配评分；
      2. 匹配结果逐个 yield，调用方达到阈值后提前 break 可立即终止 BFS。

    深度/元素上限说明：Raw View 深度远超 Control View（每层之间含装饰器、
    ContentPresenter 等中间元素），uiPath 14 层深的目标（如 PART_ContentHost）
    在 Raw View 中可达 30+ 层，故 max_depth 取 64、元素上限 30000，配合预过滤
    与提前退出保证性能。

    带 AutomationId 的目标（如 PART_ContentHost）走 UIA 原生 FindAll，不依赖
    手动 BFS 的深度/元素预算；仅无 AutomationId 的 Raw View 目标才走 BFS，并
    以预算时限兜底，避免全量遍历把整个步骤拖到数百秒。
    """
    if window is None:
        return
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.controls.uiawrapper import UIAWrapper
        from pywinauto.uia_element_info import UIAElementInfo
    except Exception:
        return
    normalized = normalize_control_definition(control_definition)
    inspect_data = normalized.get("inspectData", {}) or {}
    target_name = normalize_match_text(normalized.get("name", ""))
    target_automation_id = normalize_match_text(inspect_data.get("automationId", ""))
    if target_automation_id:
        # 优先 UIA 原生 FindAll：多数场景毫秒级返回。FindAll 按 Control View
        # 语义遍历，PART_ContentHost 等 IsControlElement=False 的内部宿主不可见，
        # 此时退回 uiPath 祖先引导的受限 Raw View 下降。
        found_any = False
        for _wrapper in _iter_raw_view_findall_candidates(window, normalized):
            found_any = True
            yield _wrapper
        if not found_any:
            yield from _iter_raw_view_guided_candidates(window, normalized)
        return
    target_type = normalize_control_type_name(
        inspect_data.get("controlType", ""), inspect_data.get("localizedControlType", "")
    )
    target_type_id = _raw_view_control_type_id(target_type) if target_type else None
    props = _raw_view_filter_props()
    # Raw View 深度远超 Control View：固定放宽到 64，不再受 uiPath 深度限制
    max_depth = 64
    try:
        walker = IUIA().iuia.RawViewWalker
        root_element = window.element_info.element
    except Exception:
        return
    queue = []
    try:
        child = walker.GetFirstChildElement(root_element)
        while child:
            queue.append((child, 1))
            child = walker.GetNextSiblingElement(child)
    except Exception:
        return
    seen = set()
    deadline = time.time() + max(1.0, float(budget_seconds or 0))
    index = 0
    while index < len(queue) and index < max_elements:
        if time.time() > deadline:
            break
        element, depth = queue[index]
        index += 1
        # 廉价预过滤：不匹配仅跳过对该元素自身的 wrapper 构造与完整评分，仍继续下沉到子节点。
        # 注意：不能因祖先预过滤失败就剪掉整个子树——无 automationId 的 Raw View 目标
        #（如孤立 PART_ContentHost）的祖先几乎必然不通过 name/type 预过滤，剪枝后这类
        # 目标永远不可达（B8）。子树遍历由 max_depth/max_elements/budget_seconds 兜底。
        if _raw_element_passes_prefilter(element, target_name, target_automation_id, target_type_id, props):
            try:
                wrapper = UIAWrapper(UIAElementInfo(element))
            except Exception:
                wrapper = None
            if wrapper is not None and wrapper_matches_control_definition(wrapper, normalized):
                key = get_wrapper_handle(wrapper) or normalize_match_text(
                    _safe_get_value(lambda: str(wrapper.element_info.runtime_id), "")
                ) or id(wrapper)
                if key not in seen:
                    seen.add(key)
                    yield wrapper
        if depth >= max_depth:
            continue
        try:
            child = walker.GetFirstChildElement(element)
            while child:
                queue.append((child, depth + 1))
                child = walker.GetNextSiblingElement(child)
        except Exception:
            pass


def iter_flow_search_windows(step_definition, window_title_hint="", control_definition=None, allow_soften=True):
    title_candidates = []
    control_window_title = ""
    if isinstance(control_definition, dict):
        control_window_title = str(control_definition.get("windowTitle", "")).strip()
        if uipath_is_main_window_root(control_definition.get("uiPath", "")):
            control_window_title = "*"
    if control_window_title in {"*", "__all__", "__ALL__"}:
        control_window_title = ""
        step_window_title = ""
        # "*" 通配（主窗口根控件）语义 = 不约束窗口标题：window_title_hint 也要同步清空，
        # 否则 "*" 会作为字面标题进入 title_candidates，win32-first 过滤恒为空，
        # 报"未找到目标窗口：*"（主窗口根控件 windowTitle 为空会被 normalize 置为 "*"）。
        window_title_hint = ""
    else:
        step_window_title = step_definition.get("windowTitle", "") if isinstance(step_definition, dict) else ""
    for text in [window_title_hint, control_window_title, step_window_title]:
        for item in parse_window_title_candidates(text):
            if item not in title_candidates:
                title_candidates.append(item)

    process_candidates = get_control_process_candidates(control_definition)
    framework_candidates = []
    if isinstance(control_definition, dict):
        inspect_data = control_definition.get("inspectData", {})
        if isinstance(inspect_data, dict):
            framework_id = normalize_match_text(inspect_data.get("frameworkId", ""))
            if framework_id:
                framework_candidates.append(framework_id)
    cache_key = make_flow_window_cache_key(title_candidates, process_candidates, framework_candidates)
    use_window_cache = not title_candidates
    if use_window_cache:
        cached_windows = get_cached_flow_windows(cache_key)
        if cached_windows:
            return cached_windows
    ranked_windows = []
    foreground_handle = get_foreground_window_handle()
    if title_candidates or process_candidates:
        # win32-first：有窗口标题/进程候选时，先用纯 Win32 枚举按"标题子串 / 进程 PID /
        # 前台窗口"过滤出目标顶层窗口，再逐个按句柄包 UIA——避免全桌面
        # Desktop(backend="uia").windows() 枚举（UIA 原生崩溃高发面，try/except 兜不住）。
        all_windows = _wrap_win32_window_candidates(title_candidates, process_candidates, foreground_handle)
    else:
        # 无标题/进程候选：不再全桌面 UIA 枚举（Desktop(backend="uia").windows() 会触碰
        # 无关应用 UIA provider，是 0xc0000374 原生堆损坏的高发面，try/except 兜不住；
        # 此前进程内线程跑"检验定位"正是在此崩掉整个编辑器）。改走 win32-first：
        # 纯 Win32 枚举可见顶层窗、按句柄包 UIA（包装失败静默跳过），由下游 framework
        # 过滤 + 评分剔除无关窗口。
        all_windows = _wrap_win32_window_candidates(
            title_candidates, process_candidates, foreground_handle, include_all=True
        )
    for window in all_windows:
        if is_automation_window(window):
            continue
        title = get_wrapper_text(window)
        process_id = get_wrapper_process_id(window)
        framework_id = get_wrapper_framework_id(window)
        handle = _safe_get_value(lambda: getattr(window.element_info, "handle", 0), 0)
        # frameworkId 硬过滤：只扫描与控件采集一致的框架窗口（如 WPF），
        # 避免在无关窗口（Win32 等）上做整树扫描造成性能灾难（实测 141s）。
        if framework_candidates and framework_id not in framework_candidates:
            continue
        matched_title = any(candidate in title for candidate in title_candidates) if title_candidates else False
        matched_process = process_id in process_candidates if process_candidates else False
        score = 0
        if foreground_handle and handle == foreground_handle:
            score += 20
        if matched_process:
            score += 12
        if matched_title:
            score += 40
        elif title_candidates and matched_process:
            score -= 16
        if _safe_get_value(lambda: window.is_visible(), False):
            score += 2
        if _safe_get_value(lambda: window.is_enabled(), False):
            score += 1
        if title_candidates and not matched_title and not matched_process and handle != foreground_handle:
            continue
        ranked_windows.append((score, window))
    if not ranked_windows:
        if (title_candidates and not allow_soften) or (not title_candidates):
            # 严格标题过滤无命中，或无标题候选（主窗口根 "*" 控件，framework 硬过滤后
            # 无命中）时：不直接放弃，回退到"前置窗口 + MUP 可见窗口"候选。
            # 场景：WPF 单窗口应用的模态弹窗不改变窗口标题（实际标题可能与配置的
            # 场景：WPF 单窗口应用的模态弹窗不改变窗口标题（实际标题可能与配置的
            # windowTitle 不一致），或空标题 MUP 主窗口被严格标题过滤排除，或句柄
            # 体系差异导致 UIA 顶层枚举漏掉 MUP 窗口。候选合并去重，后续控件匹配
            # 阶段仍有类型/评分把关，避免误命中。
            def _wrap_hwnd_candidates(candidates):
                # candidates: [{hwnd,...}]；包装成 UIA wrapper，排除自动化自身窗口
                wrapped_result = []
                seen = set()
                for cand in candidates or []:
                    hwnd = cand.get("hwnd") if isinstance(cand, dict) else cand
                    if not hwnd:
                        continue
                    wrapped = _try_get_window_by_handle(hwnd)
                    if wrapped is None or is_automation_window(wrapped):
                        continue
                    handle = _safe_get_value(lambda: getattr(wrapped.element_info, "handle", 0), 0)
                    if handle in seen:
                        continue
                    seen.add(handle)
                    wrapped_result.append(wrapped)
                return wrapped_result

            result = _wrap_hwnd_candidates(_GET_MAIN_WINDOW_CANDIDATES())
            if result:
                _LOG_STEP("[FlowLocator] 窗口过滤严格无命中，采用运行时主窗口候选 {} 个".format(len(result)))
            if not result:
                result = _wrap_hwnd_candidates(_enum_visible_mup_win32_windows())
            if not result:
                # 前台仅当是真实应用窗（非自动化自身 Tk 进度/监视窗）才回退；绝不把
                # 自动化自己"WT自动化 …"进度窗当作目标定位（必然"未找到匹配控件"）。
                fg_wrapper = _try_get_window_by_handle(foreground_handle)
                if fg_wrapper is not None and not is_automation_window(fg_wrapper):
                    _LOG_STEP("[FlowLocator] 窗口过滤严格无命中，回退前置窗口单候选")
                    result = [fg_wrapper]
                else:
                    _LOG_STEP(
                        "[FlowLocator] 窗口过滤严格无命中且前置为自动化自身窗口，"
                        "放弃本次窗口回退（不把自身进度窗当目标）"
                    )
            if result:
                _LOG_STEP(
                    "[FlowLocator] 窗口过滤严格无命中，回退候选窗口 {} 个".format(len(result))
                )
                if use_window_cache:
                    cache_flow_windows(cache_key, result)
                return result
            _LOG_STEP("[FlowLocator] 窗口过滤严格: 标题无命中且控件非低丰富度，跳过全窗口软化")
            return []
        if title_candidates:
            _LOG_STEP("[FlowLocator] 窗口过滤软化: 严格标题过滤无命中，回退到全窗口枚举")
        for window in all_windows:
            if is_automation_window(window):
                continue
            score = 0
            handle = _safe_get_value(lambda: getattr(window.element_info, "handle", 0), 0)
            process_id = get_wrapper_process_id(window)
            framework_id = get_wrapper_framework_id(window)
            if framework_candidates and framework_id not in framework_candidates:
                continue
            if foreground_handle and handle == foreground_handle:
                score += 20
            if process_candidates and process_id in process_candidates:
                score += 12
            if _safe_get_value(lambda: window.is_visible(), False):
                score += 2
            if _safe_get_value(lambda: window.is_enabled(), False):
                score += 1
            ranked_windows.append((score, window))
    ranked_windows.sort(key=lambda item: item[0], reverse=True)
    result = [window for _, window in ranked_windows]
    if not ranked_windows:
        blocked, diagnostic = detect_uia_content_blocked(all_windows)
        if blocked:
            _UIPI_BLOCK_DETECTED["timestamp"] = time.time()
            _UIPI_BLOCK_DETECTED["diagnostic"] = diagnostic
            _LOG_STEP(
                "[FlowLocator] UIPI/UIA 内容树隔离检测: UIA 窗口数={}, Win32 可见 MUP 窗口={}, 诊断={}".format(
                    len(all_windows),
                    len((diagnostic or {}).get("win32MupWindows", [])),
                    json.dumps(diagnostic, ensure_ascii=False),
                )
            )
    if use_window_cache:
        cache_flow_windows(cache_key, result)
    return result


def build_relative_region_window_spec(step_definition=None, parent_window=None, window_title_hint=""):
    step_definition = step_definition if isinstance(step_definition, dict) else {}
    parent_window = parent_window if isinstance(parent_window, dict) else {}
    title = normalize_match_text(parent_window.get("title", "")) or normalize_match_text(window_title_hint)
    if not title:
        title = normalize_match_text(step_definition.get("windowTitle", ""))
    return {
        "title": title,
        "className": normalize_match_text(parent_window.get("className", "")),
        "frameworkId": normalize_match_text(parent_window.get("frameworkId", "")),
    }


def score_window_against_spec(window, window_spec, allow_empty_title_fallback=False):
    window_spec = window_spec if isinstance(window_spec, dict) else {}
    score = 0
    title = normalize_match_text(window_spec.get("title", ""))
    class_name = normalize_match_text(window_spec.get("className", ""))
    framework_id = normalize_match_text(window_spec.get("frameworkId", ""))
    actual_title = get_wrapper_text(window)
    actual_class_name = get_wrapper_class_name(window)
    actual_framework_id = get_wrapper_framework_id(window)
    class_matched = value_matches(actual_class_name, class_name) if class_name else False
    framework_matched = value_matches(actual_framework_id, framework_id) if framework_id else False
    if title:
        if value_matches(actual_title, title):
            score += 20
        elif allow_empty_title_fallback and not actual_title and class_matched and framework_matched:
            # Only the active window may use the empty-title WPF fallback; otherwise
            # generic main windows can impersonate titled dialogs during relative clicks.
            score += 8
        else:
            return -1
    if class_name:
        if class_matched:
            score += 10
        else:
            score -= 2
    if framework_id:
        if framework_matched:
            score += 6
        else:
            score -= 1
    if _safe_get_value(lambda: window.is_visible(), False):
        score += 2
    if _safe_get_value(lambda: window.is_enabled(), False):
        score += 1
    return score


def find_nested_flow_window_candidate(ranked_candidates, window_spec):
    ranked_candidates = ranked_candidates if isinstance(ranked_candidates, list) else []
    window_spec = window_spec if isinstance(window_spec, dict) else {}
    if not ranked_candidates:
        return None, -1
    title = normalize_match_text(window_spec.get("title", ""))
    class_name = normalize_match_text(window_spec.get("className", ""))
    framework_id = normalize_match_text(window_spec.get("frameworkId", ""))
    if not title:
        return None, -1
    seen_handles = set()
    best_match = None
    best_score = -1
    for _, root_window in ranked_candidates[:5]:
        if root_window is None:
            continue
        descendants = []
        try:
            descendants = root_window.descendants()
        except Exception:
            descendants = []
        for candidate in descendants:
            candidate_handle = get_wrapper_handle(candidate) or id(candidate)
            if candidate_handle in seen_handles:
                continue
            seen_handles.add(candidate_handle)
            if class_name and not value_matches(get_wrapper_class_name(candidate), class_name):
                continue
            if framework_id and not value_matches(get_wrapper_framework_id(candidate), framework_id):
                continue
            if not value_matches(get_wrapper_text(candidate), title):
                continue
            score = score_window_against_spec(candidate, window_spec, allow_empty_title_fallback=False)
            if should_replace_flow_window_candidate(candidate, score, best_match, best_score, window_spec):
                best_score = score
                best_match = candidate
    return best_match, best_score


def find_flow_window_for_relative_region(step_definition=None, parent_window=None, timeout_seconds=3, window_title_hint=""):
    deadline = time.time() + max(0.2, float(timeout_seconds or 0))
    window_spec = build_relative_region_window_spec(
        step_definition=step_definition,
        parent_window=parent_window,
        window_title_hint=window_title_hint,
    )
    step_id = str((step_definition or {}).get("id", "")).strip()
    debug_default_height = step_id in {"step_16", "step_16_2"}
    debug_start_validation_regression = step_id in {"step_26", "step_26_2"}
    last_ranked_candidates = []
    if debug_default_height:
        foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point A:default-height-relative-input-before
        _emit_default_height_debug_event(
            "A",
            "wt_flow_locator.py:find_flow_window_for_relative_region:before",
            "[DEBUG] before find_flow_window_for_relative_region",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "windowTitleHint": window_title_hint,
                "windowSpec": window_spec,
                "parentWindow": parent_window or {},
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
            },
        )
        # #endregion
    if debug_start_validation_regression:
        foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point A:start-validation-regression-before
        _emit_start_validation_regression_debug_event(
            "A",
            "wt_flow_locator.py:find_flow_window_for_relative_region:before",
            "[DEBUG] before find_flow_window_for_relative_region",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "windowTitleHint": window_title_hint,
                "windowSpec": window_spec,
                "parentWindow": parent_window or {},
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
            },
        )
        # #endregion
    while time.time() < deadline:
        best_match = None
        best_score = -1
        ranked_candidates = []
        foreground_handle = get_foreground_window_handle()
        foreground_wrapper = _try_get_window_by_handle(foreground_handle)
        seen_handles = set()
        if foreground_wrapper is not None:
            foreground_score = score_window_against_spec(
                foreground_wrapper,
                window_spec,
                allow_empty_title_fallback=True,
            )
            foreground_handle_key = get_wrapper_handle(foreground_wrapper) or id(foreground_wrapper)
            seen_handles.add(foreground_handle_key)
            # 前台窗口只有在“匹配或可能匹配目标窗口”时才参与候选（score>=0）：
            # 标题不匹配且非空标题的前台窗口（如用户当前操作的其他应用）不得作为
            # 相对区域父窗口，否则会在目标窗口缺失时对错误窗口“假成功”点击。
            if foreground_score < 0:
                continue
            ranked_candidates.append((foreground_score, foreground_wrapper))
            if should_replace_flow_window_candidate(
                foreground_wrapper,
                foreground_score,
                best_match,
                best_score,
                window_spec,
            ):
                best_score = foreground_score
                best_match = foreground_wrapper
        for window in iter_flow_search_windows(step_definition or {}, window_title_hint=window_spec.get("title", "")):
            window_handle_key = get_wrapper_handle(window) or id(window)
            if window_handle_key in seen_handles:
                continue
            seen_handles.add(window_handle_key)
            score = score_window_against_spec(window, window_spec, allow_empty_title_fallback=False)
            ranked_candidates.append((score, window))
            if should_replace_flow_window_candidate(window, score, best_match, best_score, window_spec):
                best_score = score
                best_match = window
        nested_match, nested_score = find_nested_flow_window_candidate(ranked_candidates, window_spec)
        if nested_match is not None:
            ranked_candidates.append((nested_score, nested_match))
            if should_replace_flow_window_candidate(nested_match, nested_score, best_match, best_score, window_spec):
                best_score = nested_score
                best_match = nested_match
        if ranked_candidates:
            ranked_candidates.sort(key=lambda item: item[0], reverse=True)
            last_ranked_candidates = ranked_candidates[:5]
        if best_match is not None:
            if debug_default_height:
                # #region debug-point B:default-height-relative-input-window-found
                _emit_default_height_debug_event(
                    "B",
                    "wt_flow_locator.py:find_flow_window_for_relative_region:found",
                    "[DEBUG] relative region parent window found",
                    {
                        "stepId": step_id,
                        "windowSpec": window_spec,
                        "bestScore": best_score,
                        "bestMatch": get_wrapper_debug_snapshot(best_match),
                        "topCandidates": [
                            {
                                "score": score,
                                "candidate": get_wrapper_debug_snapshot(candidate),
                            }
                            for score, candidate in last_ranked_candidates
                        ],
                    },
                )
                # #endregion
            if debug_start_validation_regression:
                # #region debug-point B:start-validation-regression-window-found
                _emit_start_validation_regression_debug_event(
                    "B",
                    "wt_flow_locator.py:find_flow_window_for_relative_region:found",
                    "[DEBUG] relative region parent window found",
                    {
                        "stepId": step_id,
                        "windowSpec": window_spec,
                        "bestScore": best_score,
                        "bestMatch": get_wrapper_debug_snapshot(best_match),
                        "topCandidates": [
                            {
                                "score": score,
                                "candidate": get_wrapper_debug_snapshot(candidate),
                            }
                            for score, candidate in last_ranked_candidates
                        ],
                    },
                )
                # #endregion
            return best_match
        time.sleep(0.15)
    if last_ranked_candidates:
        candidate_text = " || ".join(
            "#{index} score={score} title={title} class={class_name} framework={framework}".format(
                index=index + 1,
                score=item[0],
                title=get_wrapper_text(item[1]) or "(empty)",
                class_name=get_wrapper_class_name(item[1]) or "(empty)",
                framework=get_wrapper_framework_id(item[1]) or "(empty)",
            )
            for index, item in enumerate(last_ranked_candidates)
        )
        _LOG_STEP(
            "父窗口相对区域未命中，目标标题={title}, class={class_name}, framework={framework_id}，候选窗口: {candidates}".format(
                title=window_spec.get("title", "") or "(empty)",
                class_name=window_spec.get("className", "") or "(empty)",
                framework_id=window_spec.get("frameworkId", "") or "(empty)",
                candidates=candidate_text,
            )
        )
        if debug_default_height:
            foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point C:default-height-relative-input-not-found
            _emit_default_height_debug_event(
                "C",
                "wt_flow_locator.py:find_flow_window_for_relative_region:not_found",
                "[DEBUG] relative region parent window not found",
                {
                    "stepId": step_id,
                    "windowSpec": window_spec,
                    "parentWindow": parent_window or {},
                    "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
                    "topCandidates": [
                        {
                            "score": score,
                            "candidate": get_wrapper_debug_snapshot(candidate),
                        }
                        for score, candidate in last_ranked_candidates
                    ],
                },
            )
            # #endregion
        if debug_start_validation_regression:
            foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point C:start-validation-regression-not-found
            _emit_start_validation_regression_debug_event(
                "C",
                "wt_flow_locator.py:find_flow_window_for_relative_region:not_found",
                "[DEBUG] relative region parent window not found",
                {
                    "stepId": step_id,
                    "windowSpec": window_spec,
                    "parentWindow": parent_window or {},
                    "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
                    "topCandidates": [
                        {
                            "score": score,
                            "candidate": get_wrapper_debug_snapshot(candidate),
                        }
                        for score, candidate in last_ranked_candidates
                    ],
                },
            )
            # #endregion
    else:
        _LOG_STEP(
            "父窗口相对区域未命中，目标标题={title}, class={class_name}, framework={framework_id}，且未枚举到任何候选窗口。".format(
                title=window_spec.get("title", "") or "(empty)",
                class_name=window_spec.get("className", "") or "(empty)",
                framework_id=window_spec.get("frameworkId", "") or "(empty)",
            )
        )
        if debug_default_height:
            foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point D:default-height-relative-input-empty
            _emit_default_height_debug_event(
                "D",
                "wt_flow_locator.py:find_flow_window_for_relative_region:no_candidates",
                "[DEBUG] relative region parent window search returned no candidates",
                {
                    "stepId": step_id,
                    "windowSpec": window_spec,
                    "parentWindow": parent_window or {},
                    "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
                },
            )
            # #endregion
        if debug_start_validation_regression:
            foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point D:start-validation-regression-empty
            _emit_start_validation_regression_debug_event(
                "D",
                "wt_flow_locator.py:find_flow_window_for_relative_region:empty",
                "[DEBUG] relative region parent window not found and no candidates enumerated",
                {
                    "stepId": step_id,
                    "windowSpec": window_spec,
                    "parentWindow": parent_window or {},
                    "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
                },
            )
            # #endregion
    return None


def get_wrapper_rectangle(control):
    rect, _ = _resolve_wrapper_rectangle(control, capture_trace=False)
    return rect


def _resolve_wrapper_rectangle(control, capture_trace=False):
    DWMWA_EXTENDED_FRAME_BOUNDS = 9
    GW_OWNER = 4

    def _rect_to_dict(rect):
        return {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
            "width": max(0, int(rect.right) - int(rect.left)),
            "height": max(0, int(rect.bottom) - int(rect.top)),
        }

    def _get_window_rect_from_handle(handle):
        if not handle:
            return None
        try:
            rect = wintypes.RECT()
            if ctypes.windll.user32.GetWindowRect(int(handle), ctypes.byref(rect)):
                return _rect_to_dict(rect)
        except Exception:
            pass
        return None

    def _get_window_text_from_handle(handle):
        if not handle:
            return ""
        try:
            length = int(ctypes.windll.user32.GetWindowTextLengthW(int(handle)) or 0)
            buffer = ctypes.create_unicode_buffer(max(1, length + 1))
            ctypes.windll.user32.GetWindowTextW(int(handle), buffer, len(buffer))
            return normalize_match_text(buffer.value)
        except Exception:
            return ""

    def _get_process_id_from_handle(handle):
        if not handle:
            return ""
        try:
            process_id = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(int(handle), ctypes.byref(process_id))
            return str(int(process_id.value or 0)) if int(process_id.value or 0) else ""
        except Exception:
            return ""

    def _enum_titled_top_level_candidates(expected_title, expected_process_id):
        candidates = []
        if not expected_title:
            return candidates
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        @enum_proc
        def _callback(hwnd, _lparam):
            try:
                if not ctypes.windll.user32.IsWindowVisible(int(hwnd)):
                    return True
                title = _get_window_text_from_handle(hwnd)
                if not value_matches(title, expected_title):
                    return True
                process_id = _get_process_id_from_handle(hwnd)
                if expected_process_id and process_id != expected_process_id:
                    return True
                rect = _get_window_rect_from_handle(hwnd)
                if rect and rect.get("width") and rect.get("height"):
                    candidates.append(
                        {
                            "handle": int(hwnd),
                            "title": title,
                            "processId": process_id,
                            "rect": rect,
                        }
                    )
            except Exception:
                return True
            return True

        try:
            ctypes.windll.user32.EnumWindows(_callback, 0)
        except Exception:
            return []
        return candidates

    def _enum_process_top_level_candidates(expected_process_id):
        candidates = []
        if not expected_process_id:
            return candidates
        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

        @enum_proc
        def _callback(hwnd, _lparam):
            try:
                if not ctypes.windll.user32.IsWindowVisible(int(hwnd)):
                    return True
                process_id = _get_process_id_from_handle(hwnd)
                if process_id != expected_process_id:
                    return True
                rect = _get_window_rect_from_handle(hwnd)
                if rect and rect.get("width") and rect.get("height"):
                    candidates.append(
                        {
                            "handle": int(hwnd),
                            "title": _get_window_text_from_handle(hwnd),
                            "processId": process_id,
                            "rect": rect,
                        }
                    )
            except Exception:
                return True
            return True

        try:
            ctypes.windll.user32.EnumWindows(_callback, 0)
        except Exception:
            return []
        return candidates

    def _get_dwm_extended_frame_rect(handle):
        if not handle:
            return None
        try:
            rect = wintypes.RECT()
            result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                int(handle),
                int(DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if int(result) == 0:
                candidate = _rect_to_dict(rect)
                if candidate.get("width") and candidate.get("height"):
                    return candidate
        except Exception:
            pass
        return None

    def _get_related_window_handle(handle, relationship):
        if not handle:
            return 0
        try:
            if relationship == "owner":
                return int(ctypes.windll.user32.GetWindow(int(handle), int(GW_OWNER)) or 0)
            if relationship == "parent":
                return int(ctypes.windll.user32.GetParent(int(handle)) or 0)
        except Exception:
            return 0
        return 0

    def _get_raw_rectangle(wrapper):
        handle = get_wrapper_handle(wrapper)
        if handle:
            rect = _get_window_rect_from_handle(handle)
            if rect:
                return rect
        try:
            return _rect_to_dict(wrapper.rectangle())
        except Exception:
            return None

    def _rect_contains(outer_rect, inner_rect):
        if not isinstance(outer_rect, dict) or not isinstance(inner_rect, dict):
            return False
        return (
            int(outer_rect.get("left", 0)) <= int(inner_rect.get("left", 0))
            and int(outer_rect.get("top", 0)) <= int(inner_rect.get("top", 0))
            and int(outer_rect.get("right", 0)) >= int(inner_rect.get("right", 0))
            and int(outer_rect.get("bottom", 0)) >= int(inner_rect.get("bottom", 0))
        )

    def _looks_like_wpf_window(wrapper):
        return (
            wrapper is not None
            and normalize_match_text(get_wrapper_framework_id(wrapper)) == "WPF"
            and normalize_match_text(get_wrapper_class_name(wrapper)) == "Window"
            and normalize_match_text(get_wrapper_control_type(wrapper)) == "Window"
        )

    def _format_handle(handle):
        if isinstance(handle, int) and handle:
            return hex(handle)
        return normalize_match_text(handle)

    def _get_wrapper_identity(wrapper):
        if wrapper is None:
            return {}
        return {
            "name": get_wrapper_text(wrapper),
            "className": get_wrapper_class_name(wrapper),
            "controlType": get_wrapper_control_type(wrapper),
            "frameworkId": get_wrapper_framework_id(wrapper),
            "handle": _format_handle(get_wrapper_handle(wrapper)),
        }

    def _describe_frame_upgrade(wrapper, current_rect, candidate_rect):
        details = {
            "isWpfWindow": _looks_like_wpf_window(wrapper),
            "containsCurrentRect": False,
            "leftInset": None,
            "topInset": None,
            "rightInset": None,
            "bottomInset": None,
            "horizontalPadding": None,
            "verticalPadding": None,
            "accepted": False,
        }
        if not current_rect or not candidate_rect or not details["isWpfWindow"]:
            return details
        details["containsCurrentRect"] = _rect_contains(candidate_rect, current_rect)
        if not details["containsCurrentRect"]:
            return details
        left_inset = int(current_rect["left"]) - int(candidate_rect["left"])
        top_inset = int(current_rect["top"]) - int(candidate_rect["top"])
        right_inset = int(candidate_rect["right"]) - int(current_rect["right"])
        bottom_inset = int(candidate_rect["bottom"]) - int(current_rect["bottom"])
        horizontal_padding = int(candidate_rect["width"]) - int(current_rect["width"])
        vertical_padding = int(candidate_rect["height"]) - int(current_rect["height"])
        details.update(
            {
                "leftInset": left_inset,
                "topInset": top_inset,
                "rightInset": right_inset,
                "bottomInset": bottom_inset,
                "horizontalPadding": horizontal_padding,
                "verticalPadding": vertical_padding,
            }
        )
        details["accepted"] = (
            8 <= horizontal_padding <= 80
            and 40 <= vertical_padding <= 160
            and 0 <= left_inset <= 40
            and 20 <= top_inset <= 90
            and 0 <= right_inset <= 40
            and 10 <= bottom_inset <= 90
        )
        return details

    def _pick_title_matched_frame(wrapper, current_rect):
        expected_title = normalize_match_text(get_wrapper_text(wrapper))
        expected_process_id = get_wrapper_process_id(wrapper)
        best_candidate = None
        best_evaluation = None
        best_area = None
        evaluated_candidates = []
        for candidate in _enum_titled_top_level_candidates(expected_title, expected_process_id):
            evaluation = _describe_frame_upgrade(wrapper, current_rect, candidate.get("rect"))
            candidate["evaluation"] = evaluation
            evaluated_candidates.append(candidate)
            if not evaluation.get("accepted"):
                continue
            area = int(candidate["rect"].get("width", 0)) * int(candidate["rect"].get("height", 0))
            if best_area is None or area < best_area:
                best_candidate = candidate
                best_evaluation = evaluation
                best_area = area
        return best_candidate, best_evaluation, evaluated_candidates

    trace = {
        "wrapper": _get_wrapper_identity(control),
        "baseRect": None,
        "rootCandidates": [],
        "parentCandidates": [],
        "selectedSource": "",
        "selectedRect": None,
    } if capture_trace else None

    rect = _get_raw_rectangle(control)
    if not rect:
        if trace is not None:
            trace["selectedSource"] = "missing"
        return None, trace
    if trace is not None:
        trace["baseRect"] = rect
    handle = get_wrapper_handle(control)
    if handle and _looks_like_wpf_window(control):
        title_matched_candidate, title_matched_evaluation, title_matched_candidates = _pick_title_matched_frame(control, rect)
        if trace is not None:
            for candidate in title_matched_candidates[:5]:
                trace["rootCandidates"].append(
                    {
                        "source": "EnumWindows(title/process)",
                        "handle": _format_handle(candidate.get("handle")),
                        "rect": candidate.get("rect") or {},
                        "sameAsBaseHandle": int(candidate.get("handle") or 0) == int(handle),
                        "evaluation": candidate.get("evaluation") or {},
                    }
                )
        if title_matched_candidate is not None and (title_matched_evaluation or {}).get("accepted"):
            if trace is not None:
                trace["selectedSource"] = "EnumWindows(title/process)"
                trace["selectedRect"] = title_matched_candidate.get("rect")
            return title_matched_candidate.get("rect"), trace
        process_matched_candidates = []
        expected_process_id = get_wrapper_process_id(control)
        for candidate in _enum_process_top_level_candidates(expected_process_id):
            if int(candidate.get("handle") or 0) == int(handle):
                continue
            evaluation = _describe_frame_upgrade(control, rect, candidate.get("rect"))
            candidate["evaluation"] = evaluation
            process_matched_candidates.append(candidate)
        process_matched_candidates.sort(
            key=lambda item: int((item.get("rect") or {}).get("width", 0)) * int((item.get("rect") or {}).get("height", 0))
        )
        if trace is not None:
            for candidate in process_matched_candidates[:5]:
                trace["rootCandidates"].append(
                    {
                        "source": "EnumWindows(process)",
                        "handle": _format_handle(candidate.get("handle")),
                        "rect": candidate.get("rect") or {},
                        "sameAsBaseHandle": False,
                        "evaluation": candidate.get("evaluation") or {},
                        "title": candidate.get("title", ""),
                    }
                )
        for candidate in process_matched_candidates:
            if not (candidate.get("evaluation") or {}).get("accepted"):
                continue
            if trace is not None:
                trace["selectedSource"] = "EnumWindows(process)"
                trace["selectedRect"] = candidate.get("rect")
            return candidate.get("rect"), trace
        for relationship in ("owner", "parent"):
            related_handle = _get_related_window_handle(handle, relationship)
            if not related_handle:
                continue
            related_rect = _get_window_rect_from_handle(related_handle)
            evaluation = _describe_frame_upgrade(control, rect, related_rect)
            if trace is not None:
                trace["rootCandidates"].append(
                    {
                        "source": "Get{relationship}".format(relationship=relationship.title()),
                        "handle": _format_handle(related_handle),
                        "rect": related_rect or {},
                        "sameAsBaseHandle": int(related_handle) == int(handle),
                        "evaluation": evaluation,
                    }
                )
            if evaluation["accepted"]:
                if trace is not None:
                    trace["selectedSource"] = "Get{relationship}".format(relationship=relationship.title())
                    trace["selectedRect"] = related_rect
                return related_rect, trace
        dwm_rect = _get_dwm_extended_frame_rect(handle)
        dwm_evaluation = _describe_frame_upgrade(control, rect, dwm_rect)
        if trace is not None:
            trace["rootCandidates"].append(
                {
                    "source": "DwmExtendedFrameBounds",
                    "handle": _format_handle(handle),
                    "rect": dwm_rect or {},
                    "sameAsBaseHandle": True,
                    "evaluation": dwm_evaluation,
                }
            )
        if dwm_evaluation["accepted"]:
            if trace is not None:
                trace["selectedSource"] = "DwmExtendedFrameBounds"
                trace["selectedRect"] = dwm_rect
            return dwm_rect, trace
        root_candidates = []
        for flag in (2, 3):
            try:
                candidate_handle = int(ctypes.windll.user32.GetAncestor(int(handle), int(flag)) or 0)
            except Exception:
                candidate_handle = 0
            if candidate_handle and all(item["handle"] != candidate_handle for item in root_candidates):
                root_candidates.append({"flag": flag, "handle": candidate_handle})
        for root_candidate in root_candidates:
            root_handle = root_candidate["handle"]
            if root_handle == int(handle):
                if trace is not None:
                    trace["rootCandidates"].append(
                        {
                            "source": "GetAncestor({flag})".format(flag=root_candidate["flag"]),
                            "handle": _format_handle(root_handle),
                            "rect": _get_window_rect_from_handle(root_handle) or {},
                            "sameAsBaseHandle": True,
                            "evaluation": {
                                "isWpfWindow": True,
                                "containsCurrentRect": True,
                                "accepted": False,
                            },
                        }
                    )
                continue
            root_rect = _get_window_rect_from_handle(root_handle)
            evaluation = _describe_frame_upgrade(control, rect, root_rect)
            if trace is not None:
                trace["rootCandidates"].append(
                    {
                        "source": "GetAncestor({flag})".format(flag=root_candidate["flag"]),
                        "handle": _format_handle(root_handle),
                        "rect": root_rect or {},
                        "sameAsBaseHandle": False,
                        "evaluation": evaluation,
                    }
                )
            if evaluation["accepted"]:
                if trace is not None:
                    trace["selectedSource"] = "GetAncestor({flag})".format(flag=root_candidate["flag"])
                    trace["selectedRect"] = root_rect
                return root_rect, trace
    # Some WPF child windows expose content-area bounds; prefer the nearest enclosing
    # WPF Window ancestor when it only adds a small frame/title-bar margin.
    current = control
    for depth in range(1, 4):
        current = _safe_get_value(lambda: current.parent(), None)
        if current is None:
            break
        candidate_rect = _get_raw_rectangle(current)
        evaluation = _describe_frame_upgrade(current, rect, candidate_rect)
        if trace is not None:
            trace["parentCandidates"].append(
                {
                    "source": "parent({depth})".format(depth=depth),
                    "wrapper": _get_wrapper_identity(current),
                    "rect": candidate_rect or {},
                    "evaluation": evaluation,
                }
            )
        if evaluation["accepted"]:
            if trace is not None:
                trace["selectedSource"] = "parent({depth})".format(depth=depth)
                trace["selectedRect"] = candidate_rect
            return candidate_rect, trace
    if trace is not None:
        trace["selectedSource"] = "base"
        trace["selectedRect"] = rect
    return rect, trace


def normalize_relative_region(relative_region):
    relative_region = relative_region if isinstance(relative_region, dict) else {}

    def _read_float(key, default_value):
        try:
            return float(relative_region.get(key, default_value))
        except Exception:
            return float(default_value)

    normalized = {
        "x": _read_float("x", 0.0),
        "y": _read_float("y", 0.0),
        "width": _read_float("width", 0.2),
        "height": _read_float("height", 0.08),
        "anchor": str(relative_region.get("anchor", "center")).strip().lower() or "center",
    }
    normalized["x"] = min(max(normalized["x"], 0.0), 1.0)
    normalized["y"] = min(max(normalized["y"], 0.0), 1.0)
    normalized["width"] = min(max(normalized["width"], 0.01), 1.0)
    normalized["height"] = min(max(normalized["height"], 0.01), 1.0)
    return normalized


def get_relative_region_reference_window_rect(relative_region):
    relative_region = relative_region if isinstance(relative_region, dict) else {}
    for key in ("referenceWindowRect", "recordedWindowRect"):
        candidate = relative_region.get(key)
        if not isinstance(candidate, dict):
            continue
        try:
            left = int(candidate.get("left", 0))
            top = int(candidate.get("top", 0))
            width = int(candidate.get("width", 0))
            height = int(candidate.get("height", 0))
            right = int(candidate.get("right", left + width))
            bottom = int(candidate.get("bottom", top + height))
        except Exception:
            continue
        if width <= 0 and right > left:
            width = right - left
        if height <= 0 and bottom > top:
            height = bottom - top
        if width <= 0 or height <= 0:
            continue
        return {
            "left": left,
            "top": top,
            "right": left + width,
            "bottom": top + height,
            "width": width,
            "height": height,
        }
    return None


def resolve_relative_region_absolute_rect(window, relative_region, window_rect=None):
    reference_window_rect = get_relative_region_reference_window_rect(relative_region)
    window_rect_source = "referenceWindowRect" if reference_window_rect else "runtime"
    window_rect = reference_window_rect or window_rect or get_wrapper_rectangle(window)
    if not window_rect or not window_rect.get("width") or not window_rect.get("height"):
        return None
    region = normalize_relative_region(relative_region)
    left = int(window_rect["left"] + window_rect["width"] * region["x"])
    top = int(window_rect["top"] + window_rect["height"] * region["y"])
    width = max(1, int(window_rect["width"] * region["width"]))
    height = max(1, int(window_rect["height"] * region["height"]))
    return {
        "left": left,
        "top": top,
        "right": left + width,
        "bottom": top + height,
        "width": width,
        "height": height,
        "anchor": region["anchor"],
        "windowRect": window_rect,
        "windowRectSource": window_rect_source,
    }


def resolve_relative_region_anchor_point(absolute_rect):
    if not isinstance(absolute_rect, dict):
        return None
    left = int(absolute_rect.get("left", 0))
    top = int(absolute_rect.get("top", 0))
    width = max(1, int(absolute_rect.get("width", 1)))
    height = max(1, int(absolute_rect.get("height", 1)))
    anchor = str(absolute_rect.get("anchor", "center")).strip().lower() or "center"
    if anchor == "left_center":
        return left + max(1, int(width * 0.2)), top + int(height / 2)
    if anchor == "right_center":
        return left + max(1, int(width * 0.8)), top + int(height / 2)
    return left + int(width / 2), top + int(height / 2)


def click_relative_region(step_definition, parent_window, relative_region, timeout_seconds=3, window_title_hint="", click_kind="single", control_map_path=None):
    step_id = str((step_definition or {}).get("id", "")).strip()
    debug_default_height = step_id in {"step_16", "step_16_2"}
    debug_add_data_false_hit = step_id in {"step_27", "step_29"}
    debug_start_validation_regression = step_id in {"step_26", "step_26_2"}
    debug_longitude_confirm = step_id in {"step_11", "step_12"}
    debug_step37_family = step_id in {"step_37", "step_44"}
    if debug_add_data_false_hit:
        foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point A:add-data-false-hit-before
        _emit_add_data_false_hit_debug_event(
            "A",
            "wt_flow_locator.py:click_relative_region:before",
            "[DEBUG] before click_relative_region",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "windowTitleHint": window_title_hint,
                "parentWindow": parent_window or {},
                "relativeRegion": relative_region or {},
                "clickKind": click_kind,
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
            },
        )
        # #endregion
    if debug_start_validation_regression:
        foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point E:start-validation-regression-before-click-relative
        _emit_start_validation_regression_debug_event(
            "E",
            "wt_flow_locator.py:click_relative_region:before",
            "[DEBUG] before click_relative_region",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "windowTitleHint": window_title_hint,
                "parentWindow": parent_window or {},
                "relativeRegion": relative_region or {},
                "clickKind": click_kind,
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
            },
        )
        # #endregion
    if debug_longitude_confirm:
        foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point E:post-type-click-step11-12-before-relative
        _emit_post_type_click_debug_event(
            "E",
            "wt_flow_locator.py:click_relative_region:step_11_12_before",
            "[DEBUG] before relative region action for step_11/step_12",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "windowTitleHint": window_title_hint,
                "parentWindow": parent_window or {},
                "relativeRegion": relative_region or {},
                "clickKind": click_kind,
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
            },
        )
        # #endregion
    if debug_step37_family:
        foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point A:step37-add-data-before-click-relative
        _emit_step37_add_data_miss_debug_event(
            "A",
            "wt_flow_locator.py:click_relative_region:before",
            "[DEBUG] before click_relative_region for step_37/44 family",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "windowTitleHint": window_title_hint,
                "parentWindow": parent_window or {},
                "relativeRegion": relative_region or {},
                "clickKind": click_kind,
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
            },
        )
        # #endregion
    window = find_flow_window_for_relative_region(
        step_definition=step_definition,
        parent_window=parent_window,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
    )
    if window is None:
        if debug_add_data_false_hit:
            foreground_not_found = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point B:add-data-false-hit-window-miss
            _emit_add_data_false_hit_debug_event(
                "B",
                "wt_flow_locator.py:click_relative_region:window_not_found",
                "[DEBUG] click_relative_region failed to find parent window",
                {
                    "stepId": step_id,
                    "parentWindow": parent_window or {},
                    "relativeRegion": relative_region or {},
                    "foregroundAfter": get_wrapper_debug_snapshot(foreground_not_found),
                },
            )
            # #endregion
        if debug_start_validation_regression:
            foreground_not_found = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point F:start-validation-regression-window-not-found
            _emit_start_validation_regression_debug_event(
                "F",
                "wt_flow_locator.py:click_relative_region:window_not_found",
                "[DEBUG] click_relative_region failed to find parent window",
                {
                    "stepId": step_id,
                    "parentWindow": parent_window or {},
                    "relativeRegion": relative_region or {},
                    "foregroundAfter": get_wrapper_debug_snapshot(foreground_not_found),
                },
            )
            # #endregion
        if debug_longitude_confirm:
            foreground_not_found = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point F:post-type-click-step11-12-window-miss
            _emit_post_type_click_debug_event(
                "F",
                "wt_flow_locator.py:click_relative_region:step_11_12_window_not_found",
                "[DEBUG] relative region action failed to find parent window for step_11/step_12",
                {
                    "stepId": step_id,
                    "parentWindow": parent_window or {},
                    "relativeRegion": relative_region or {},
                    "foregroundAfter": get_wrapper_debug_snapshot(foreground_not_found),
                },
            )
            # #endregion
        if debug_step37_family:
            foreground_not_found = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point B:step37-add-data-window-miss
            _emit_step37_add_data_miss_debug_event(
                "B",
                "wt_flow_locator.py:click_relative_region:window_not_found",
                "[DEBUG] click_relative_region failed to find parent window for step_37/44 family",
                {
                    "stepId": step_id,
                    "parentWindow": parent_window or {},
                    "relativeRegion": relative_region or {},
                    "foregroundAfter": get_wrapper_debug_snapshot(foreground_not_found),
                },
            )
            # #endregion
        return False, {}
    try:
        window.set_focus()
    except Exception:
        pass
    time.sleep(0.12)
    effective_window = resolve_effective_relative_region_window(window, parent_window)
    traced_window_rect = None
    if debug_default_height or debug_start_validation_regression:
        traced_window_rect, rect_trace = _resolve_wrapper_rectangle(effective_window, capture_trace=True)
        _emit_relative_region_rect_trace(
            step_id,
            "wt_flow_locator.py:click_relative_region:rectangle_trace",
            "[DEBUG] relative region rectangle resolution trace",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "parentWindow": parent_window or {},
                "windowTitleHint": window_title_hint,
                "relativeRegion": relative_region or {},
                "effectiveWindow": {
                    "name": get_wrapper_text(effective_window),
                    "className": get_wrapper_class_name(effective_window),
                    "controlType": get_wrapper_control_type(effective_window),
                    "frameworkId": get_wrapper_framework_id(effective_window),
                    "handle": get_wrapper_handle_text(effective_window),
                },
                "trace": rect_trace or {},
            },
        )
    absolute_rect = resolve_relative_region_absolute_rect(effective_window, relative_region, window_rect=traced_window_rect)
    center = resolve_relative_region_anchor_point(absolute_rect)
    if not center:
        if debug_add_data_false_hit:
            # #region debug-point C:add-data-false-hit-center-miss
            _emit_add_data_false_hit_debug_event(
                "C",
                "wt_flow_locator.py:click_relative_region:center_not_found",
                "[DEBUG] click_relative_region failed to resolve anchor center",
                {
                    "stepId": step_id,
                    "parentWindow": parent_window or {},
                    "relativeRegion": relative_region or {},
                    "window": get_wrapper_debug_snapshot(effective_window),
                    "absoluteRect": absolute_rect or {},
                },
            )
            # #endregion
        if debug_start_validation_regression:
            # #region debug-point G:start-validation-regression-center-not-found
            _emit_start_validation_regression_debug_event(
                "G",
                "wt_flow_locator.py:click_relative_region:center_not_found",
                "[DEBUG] click_relative_region failed to resolve anchor center",
                {
                    "stepId": step_id,
                    "parentWindow": parent_window or {},
                    "relativeRegion": relative_region or {},
                    "window": get_wrapper_debug_snapshot(effective_window),
                    "absoluteRect": absolute_rect or {},
                },
            )
            # #endregion
        return False, {}
    if debug_add_data_false_hit:
        # #region debug-point D:add-data-false-hit-before-click
        _emit_add_data_false_hit_debug_event(
            "D",
            "wt_flow_locator.py:click_relative_region:before_click",
            "[DEBUG] click_relative_region resolved click point",
            {
                "stepId": step_id,
                "window": get_wrapper_debug_snapshot(effective_window),
                "absoluteRect": absolute_rect or {},
                "clickPoint": {"x": center[0], "y": center[1]},
                "clickKind": click_kind,
            },
        )
        # #endregion
    if debug_start_validation_regression:
        # #region debug-point H:start-validation-regression-before-click
        _emit_start_validation_regression_debug_event(
            "H",
            "wt_flow_locator.py:click_relative_region:before_click",
            "[DEBUG] click_relative_region resolved click point",
            {
                "stepId": step_id,
                "window": get_wrapper_debug_snapshot(effective_window),
                "absoluteRect": absolute_rect or {},
                "clickPoint": {"x": center[0], "y": center[1]},
                "clickKind": click_kind,
            },
        )
        # #endregion
    if debug_longitude_confirm:
        # #region debug-point G:post-type-click-step11-12-before-click
        _emit_post_type_click_debug_event(
            "G",
            "wt_flow_locator.py:click_relative_region:step_11_12_before_click",
            "[DEBUG] relative region resolved point for step_11/step_12",
            {
                "stepId": step_id,
                "window": get_wrapper_debug_snapshot(effective_window),
                "windowChildren": get_window_descendant_debug_summary(effective_window, limit=32),
                "absoluteRect": absolute_rect or {},
                "clickPoint": {"x": center[0], "y": center[1]},
                "clickKind": click_kind,
            },
        )
        # #endregion
    if debug_step37_family:
        # #region debug-point C:step37-add-data-before-click
        _emit_step37_add_data_miss_debug_event(
            "C",
            "wt_flow_locator.py:click_relative_region:before_click",
            "[DEBUG] click_relative_region resolved point for step_37/44 family",
            {
                "stepId": step_id,
                "window": get_wrapper_debug_snapshot(effective_window),
                "windowChildren": get_window_descendant_debug_summary(effective_window, limit=32),
                "absoluteRect": absolute_rect or {},
                "clickPoint": {"x": center[0], "y": center[1]},
                "clickKind": click_kind,
            },
        )
        # #endregion
    try:
        _perform_relative_region_click(center, click_kind)
    except Exception:
        if debug_add_data_false_hit:
            # #region debug-point E:add-data-false-hit-click-error
            _emit_add_data_false_hit_debug_event(
                "E",
                "wt_flow_locator.py:click_relative_region:click_error",
                "[DEBUG] click_relative_region raised while clicking",
                {
                    "stepId": step_id,
                    "window": get_wrapper_debug_snapshot(effective_window),
                    "absoluteRect": absolute_rect or {},
                    "clickPoint": {"x": center[0], "y": center[1]},
                    "clickKind": click_kind,
                },
            )
            # #endregion
        if debug_start_validation_regression:
            # #region debug-point I:start-validation-regression-click-error
            _emit_start_validation_regression_debug_event(
                "I",
                "wt_flow_locator.py:click_relative_region:click_error",
                "[DEBUG] click_relative_region raised while clicking",
                {
                    "stepId": step_id,
                    "window": get_wrapper_debug_snapshot(effective_window),
                    "absoluteRect": absolute_rect or {},
                    "clickPoint": {"x": center[0], "y": center[1]},
                    "clickKind": click_kind,
                },
            )
            # #endregion
        if debug_step37_family:
            # #region debug-point D:step37-add-data-click-error
            _emit_step37_add_data_miss_debug_event(
                "D",
                "wt_flow_locator.py:click_relative_region:click_error",
                "[DEBUG] click_relative_region raised while clicking for step_37/44 family",
                {
                    "stepId": step_id,
                    "window": get_wrapper_debug_snapshot(effective_window),
                    "absoluteRect": absolute_rect or {},
                    "clickPoint": {"x": center[0], "y": center[1]},
                    "clickKind": click_kind,
                },
            )
            # #endregion
        return False, {}
    time.sleep(0.12)
    foreground_after_first_click = _try_get_window_by_handle(get_foreground_window_handle()) if debug_add_data_false_hit else None
    refined_window = resolve_effective_relative_region_window(effective_window, parent_window)
    refined_window_rect = traced_window_rect if get_wrapper_handle(refined_window) == get_wrapper_handle(effective_window) else None
    refined_absolute_rect = resolve_relative_region_absolute_rect(refined_window, relative_region, window_rect=refined_window_rect)
    refined_center = resolve_relative_region_anchor_point(refined_absolute_rect)
    refined_click_performed = False
    if (
        click_kind == "single"
        and refined_window is not None
        and refined_absolute_rect
        and refined_center
        and get_wrapper_handle(refined_window) != get_wrapper_handle(effective_window)
    ):
        try:
            _perform_relative_region_click(refined_center, click_kind)
            refined_click_performed = True
            effective_window = refined_window
            absolute_rect = refined_absolute_rect
            center = refined_center
        except Exception:
            pass
    if debug_add_data_false_hit:
        foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point F:add-data-false-hit-after-click
        _emit_add_data_false_hit_debug_event(
            "F",
            "wt_flow_locator.py:click_relative_region:after_click",
            "[DEBUG] click_relative_region finished click sequence",
            {
                "stepId": step_id,
                "windowBeforeRefine": get_wrapper_debug_snapshot(window),
                "effectiveWindow": get_wrapper_debug_snapshot(effective_window),
                "foregroundAfterFirstClick": get_wrapper_debug_snapshot(foreground_after_first_click),
                "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
                "absoluteRect": absolute_rect or {},
                "clickPoint": {"x": center[0], "y": center[1]},
                "refinedClickPerformed": refined_click_performed,
                "refinedAbsoluteRect": refined_absolute_rect or {},
                "refinedCenter": {"x": refined_center[0], "y": refined_center[1]} if refined_center else {},
            },
        )
        # #endregion
    if debug_start_validation_regression:
        foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point J:start-validation-regression-after-click
        _emit_start_validation_regression_debug_event(
            "J",
            "wt_flow_locator.py:click_relative_region:after_click",
            "[DEBUG] click_relative_region finished click sequence",
            {
                "stepId": step_id,
                "windowBeforeRefine": get_wrapper_debug_snapshot(window),
                "effectiveWindow": get_wrapper_debug_snapshot(effective_window),
                "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
                "absoluteRect": absolute_rect or {},
                "clickPoint": {"x": center[0], "y": center[1]},
                "refinedClickPerformed": refined_click_performed,
                "refinedAbsoluteRect": refined_absolute_rect or {},
                "refinedCenter": {"x": refined_center[0], "y": refined_center[1]} if refined_center else {},
            },
        )
        # #endregion
    if debug_longitude_confirm:
        foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point H:post-type-click-step11-12-after-click
        _emit_post_type_click_debug_event(
            "H",
            "wt_flow_locator.py:click_relative_region:step_11_12_after_click",
            "[DEBUG] relative region action finished for step_11/step_12",
            {
                "stepId": step_id,
                "windowBeforeRefine": get_wrapper_debug_snapshot(window),
                "effectiveWindow": get_wrapper_debug_snapshot(effective_window),
                "windowChildren": get_window_descendant_debug_summary(effective_window, limit=32),
                "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
                "absoluteRect": absolute_rect or {},
                "clickPoint": {"x": center[0], "y": center[1]},
                "refinedClickPerformed": refined_click_performed,
            },
        )
        # #endregion
    if debug_step37_family:
        foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point E:step37-add-data-after-click
        _emit_step37_add_data_miss_debug_event(
            "E",
            "wt_flow_locator.py:click_relative_region:after_click",
            "[DEBUG] click_relative_region finished for step_37/44 family",
            {
                "stepId": step_id,
                "windowBeforeRefine": get_wrapper_debug_snapshot(window),
                "effectiveWindow": get_wrapper_debug_snapshot(effective_window),
                "windowChildren": get_window_descendant_debug_summary(effective_window, limit=32),
                "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
                "absoluteRect": absolute_rect or {},
                "clickPoint": {"x": center[0], "y": center[1]},
                "refinedClickPerformed": refined_click_performed,
            },
        )
        # #endregion
    _LOG_STEP(
        "已通过父窗口相对区域点击: window={title}, center=({x},{y}), kind={kind}".format(
            title=get_wrapper_text(effective_window) or normalize_match_text((parent_window or {}).get("title", "")) or "(window)",
            x=center[0],
            y=center[1],
            kind=click_kind,
        )
    )
    return True, {
        "windowTitle": get_wrapper_text(effective_window),
        "windowClassName": get_wrapper_class_name(effective_window),
        "windowFrameworkId": get_wrapper_framework_id(effective_window),
        "windowRect": absolute_rect.get("windowRect", {}),
        "relativeRegionRect": {
            "left": absolute_rect.get("left", 0),
            "top": absolute_rect.get("top", 0),
            "right": absolute_rect.get("right", 0),
            "bottom": absolute_rect.get("bottom", 0),
            "width": absolute_rect.get("width", 0),
            "height": absolute_rect.get("height", 0),
        },
        "clickPoint": {"x": center[0], "y": center[1]},
        "clickKind": click_kind,
    }


def type_text_into_relative_region(
    step_definition,
    parent_window,
    relative_region,
    text,
    timeout_seconds=3,
    window_title_hint="",
    post_input_keys="",
    control_map_path=None,
):
    step_id = str((step_definition or {}).get("id", "")).strip()
    debug_default_height = step_id in {"step_16", "step_16_2"}
    debug_longitude_confirm = step_id == "step_11"
    debug_step37_family_input = step_id in {"step_36", "step_43"}
    if debug_default_height:
        # #region debug-point E:default-height-relative-input-before-type
        _emit_default_height_debug_event(
            "E",
            "wt_flow_locator.py:type_text_into_relative_region:before",
            "[DEBUG] before type_text_into_relative_region",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "parentWindow": parent_window or {},
                "relativeRegion": relative_region or {},
                "text": str(text or ""),
                "windowTitleHint": window_title_hint,
            },
        )
        # #endregion
    if debug_longitude_confirm:
        foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point I:post-type-click-step11-before-type
        _emit_post_type_click_debug_event(
            "I",
            "wt_flow_locator.py:type_text_into_relative_region:step_11_before",
            "[DEBUG] before type_text_into_relative_region for step_11",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "parentWindow": parent_window or {},
                "relativeRegion": relative_region or {},
                "text": str(text or ""),
                "windowTitleHint": window_title_hint,
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
            },
        )
        # #endregion
    if debug_step37_family_input:
        foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point F:step37-add-data-before-type
        _emit_step37_add_data_miss_debug_event(
            "F",
            "wt_flow_locator.py:type_text_into_relative_region:before",
            "[DEBUG] before type_text_into_relative_region for step_36/43 family",
            {
                "stepId": step_id,
                "stepName": str((step_definition or {}).get("name", "")).strip(),
                "parentWindow": parent_window or {},
                "relativeRegion": relative_region or {},
                "text": str(text or ""),
                "postInputKeys": str(post_input_keys or ""),
                "windowTitleHint": window_title_hint,
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
            },
        )
        # #endregion
    ok, region_meta = click_relative_region(
        step_definition,
        parent_window,
        relative_region,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
        click_kind="single",
    )
    if not ok:
        if debug_default_height:
            # #region debug-point F:default-height-relative-input-click-failed
            _emit_default_height_debug_event(
                "F",
                "wt_flow_locator.py:type_text_into_relative_region:click_failed",
                "[DEBUG] type_text_into_relative_region failed before text input",
                {
                    "stepId": step_id,
                    "parentWindow": parent_window or {},
                    "relativeRegion": relative_region or {},
                    "text": str(text or ""),
                    "windowTitleHint": window_title_hint,
                },
            )
            # #endregion
        return False, {}
    center = (region_meta.get("clickPoint", {}) or {}).get("x"), (region_meta.get("clickPoint", {}) or {}).get("y")
    # 防误清空：点击"成功"只代表坐标点击已发出，不代表输入框获得了焦点。若窗口未激活
    # 或点击偏移，Ctrl+A+Backspace 会清空当前真实焦点控件的内容（可能是上一个编辑框）。
    # 键入前做一次焦点落点校验：前台窗口不是目标窗口时，重新点击区域一次再键入。
    try:
        _fg_wrapper = _try_get_window_by_handle(get_foreground_window_handle())
        _fg_title = normalize_match_text(get_wrapper_text(_fg_wrapper)) if _fg_wrapper is not None else ""
        _region_title = normalize_match_text(str(region_meta.get("windowTitle") or ""))
        if _fg_wrapper is not None and _region_title and not value_matches(_fg_title, _region_title):
            _LOG_STEP(
                "相对区域输入前焦点校验未通过，重新点击区域: step={step_id}, fg={fg}, region={region}".format(
                    step_id=step_id,
                    fg=_fg_title or "(unknown)",
                    region=_region_title,
                )
            )
            _reclick_ok, _ = click_relative_region(
                step_definition,
                parent_window,
                relative_region,
                timeout_seconds=timeout_seconds,
                window_title_hint=window_title_hint,
                click_kind="single",
            )
            if not _reclick_ok:
                return False, {}
            time.sleep(0.15)
    except Exception:
        pass
    try:
        time.sleep(0.15)
        send_keys("^a")
        time.sleep(0.05)
        send_keys("{BACKSPACE}")
        time.sleep(0.05)
        # 转义特殊字符，防止文本被 send_keys 解释成按键指令（如 "C++"、"100%"）
        send_keys(_escape_send_keys_text(text))
        if str(post_input_keys or "").strip():
            time.sleep(0.05)
            send_keys(str(post_input_keys))
    except Exception:
        return False, {}
    _LOG_STEP(
        "已通过父窗口相对区域输入文本: window={title}, center=({x},{y}), text={text}, postKeys={post_keys}".format(
            title=region_meta.get("windowTitle") or normalize_match_text((parent_window or {}).get("title", "")) or "(window)",
            x=center[0],
            y=center[1],
            text=str(text or ""),
            post_keys=str(post_input_keys or ""),
        )
    )
    if debug_default_height:
        # #region debug-point G:default-height-relative-input-success
        _emit_default_height_debug_event(
            "G",
            "wt_flow_locator.py:type_text_into_relative_region:success",
            "[DEBUG] type_text_into_relative_region succeeded",
            {
                "stepId": step_id,
                "regionMeta": region_meta,
                "text": str(text or ""),
                "postInputKeys": str(post_input_keys or ""),
            },
        )
        # #endregion
    if debug_longitude_confirm:
        foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point J:post-type-click-step11-after-type
        _emit_post_type_click_debug_event(
            "J",
            "wt_flow_locator.py:type_text_into_relative_region:step_11_after",
            "[DEBUG] after type_text_into_relative_region for step_11",
            {
                "stepId": step_id,
                "regionMeta": region_meta,
                "text": str(text or ""),
                "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
            },
        )
        # #endregion
    if debug_step37_family_input:
        foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point G:step37-add-data-after-type
        _emit_step37_add_data_miss_debug_event(
            "G",
            "wt_flow_locator.py:type_text_into_relative_region:after",
            "[DEBUG] after type_text_into_relative_region for step_36/43 family",
            {
                "stepId": step_id,
                "regionMeta": region_meta,
                "text": str(text or ""),
                "postInputKeys": str(post_input_keys or ""),
                "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
            },
        )
        # #endregion
    region_meta["inputText"] = str(text or "")
    return True, region_meta


def get_flow_control_definition(step_id, control_id):
    step_definition = _GET_STEP_DEFINITION(step_id)
    controls = step_definition.get("controls", []) if isinstance(step_definition, dict) else []
    for control in controls:
        if str(control.get("id", "")).strip() == str(control_id or "").strip():
            return control
    return {}


# ---------------------------------------------------------------------------
# 控件库 JSON 模糊匹配辅助函数
# ---------------------------------------------------------------------------

def _control_map_cache_key(control_map_path):
    """Return a cache key that also tracks file mtime/size."""
    try:
        stat = os.stat(control_map_path)
        return (control_map_path, stat.st_mtime_ns, stat.st_size)
    except Exception:
        return control_map_path


def _load_control_map_json(control_map_path):
    """Load control library JSON with mtime/size-aware 30s TTL caching."""
    if not control_map_path or not os.path.isfile(control_map_path):
        return None
    cache_key = _control_map_cache_key(control_map_path)
    now = time.time()
    cached = _control_map_cache.get(cache_key)
    if cached is not None and (now - cached[0]) < 30:
        return cached[1]
    try:
        with open(control_map_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        _control_map_cache[cache_key] = (now, data)
        return data
    except Exception:
        _control_map_cache.pop(cache_key, None)
        return None


_control_map_index_cache = {}  # path -> (load_time, flattened, name_index, aid_index, ct_index)


def _load_control_map_index(control_map_path):
    """Load and flatten the control library JSON with cached indexes.

    Returns (flattened, name_index, automation_id_index, control_type_index)
    with mtime/size-aware 30s TTL caching.
    """
    if not control_map_path or not os.path.isfile(control_map_path):
        return None, {}, {}, {}
    cache_key = _control_map_cache_key(control_map_path)
    now = time.time()
    cached = _control_map_index_cache.get(cache_key)
    if cached is not None and (now - cached[0]) < 30:
        return cached[1], cached[2], cached[3], cached[4]
    data = _load_control_map_json(control_map_path)
    if data is None:
        return None, {}, {}, {}
    root = data.get("controlsTree", data)
    flattened = _flatten_controls_tree(root)
    name_index = {}
    automation_id_index = {}
    control_type_index = {}
    for entry in flattened:
        label = (
            entry.get("label")
            or entry.get("name")
            or entry.get("labelText")
            or entry.get("displayName")
            or ""
        )
        if label:
            name_index.setdefault(str(label).strip(), []).append(entry)
        automation_id = entry.get("automationId") or entry.get("automation_id")
        if automation_id:
            automation_id_index.setdefault(str(automation_id).strip(), []).append(entry)
        control_type = entry.get("controlType") or entry.get("control_type")
        if control_type:
            control_type_index.setdefault(str(control_type).strip().lower(), []).append(entry)
    _control_map_index_cache[cache_key] = (now, flattened, name_index, automation_id_index, control_type_index)
    return flattened, name_index, automation_id_index, control_type_index


def _flatten_controls_tree(node):
    """递归展平嵌套控件树为平面列表，同时处理 controls / children / items / controlsTree 键。"""
    result = []
    if not isinstance(node, dict):
        return result
    # 收集当前节点（如果有标识字段）
    if node.get("label") or node.get("name") or node.get("labelText") or node.get("displayName"):
        result.append(node)
    for child_key in ("controls", "children", "items", "controlsTree"):
        child = node.get(child_key)
        if isinstance(child, list):
            for item in child:
                result.extend(_flatten_controls_tree(item))
        elif isinstance(child, dict):
            result.extend(_flatten_controls_tree(child))
    return result


def _fuzzy_match_control_map(step_label, step_info, flattened_controls, name_index=None, automation_id_index=None, control_type_index=None):
    """Fuzzy-match a control against the flattened control library.

    flattened_controls is the cached flattened entry list; name_index,
    automation_id_index and control_type_index provide O(1) fast paths.
    """
    if fuzz is None or not flattened_controls or not step_label:
        return None
    step_label_str = str(step_label).strip()
    exact_pool = name_index.get(step_label_str) if name_index else None
    candidate_pool = exact_pool if exact_pool else flattened_controls
    is_exact = bool(exact_pool)
    if not is_exact and step_info.get("automationId") and automation_id_index:
        aid_pool = automation_id_index.get(str(step_info["automationId"]).strip())
        if aid_pool:
            candidate_pool = aid_pool
    if candidate_pool is flattened_controls and step_info.get("controlType") and control_type_index:
        ct_pool = control_type_index.get(str(step_info["controlType"]).strip().lower())
        if ct_pool:
            candidate_pool = ct_pool

    def _entry_label(entry):
        return (
            entry.get("label")
            or entry.get("name")
            or entry.get("labelText")
            or entry.get("displayName")
            or ""
        )

    def _entry_score(entry):
        entry_label = _entry_label(entry)
        if not entry_label:
            return None
        if is_exact:
            score = 95
        else:
            score = fuzz.ratio(step_label_str, str(entry_label))
            if score < 65:
                return None
        if step_info.get("automationId") and entry.get("automationId"):
            if str(step_info["automationId"]).strip() == str(entry["automationId"]).strip():
                score += 15
        if step_info.get("className") and entry.get("className"):
            if str(step_info["className"]).strip() == str(entry["className"]).strip():
                score += 10
        if step_info.get("controlType") and entry.get("controlType"):
            if str(step_info["controlType"]).strip().lower() == str(entry["controlType"]).strip().lower():
                score += 5
        return score

    best_entry = None
    best_score = 0
    for entry in candidate_pool:
        score = _entry_score(entry)
        if score is None:
            continue
        if score > best_score:
            best_score = score
            best_entry = dict(entry)
            best_entry["_fuzzy_score"] = score
    narrowed_pool = candidate_pool is not flattened_controls
    if best_entry is not None and ((is_exact and best_score < 100) or (not is_exact and narrowed_pool)):
        # Re-scan the full pool when the exact/narrowed pool is weak or empty.
        narrowed_ids = {id(entry) for entry in candidate_pool} if narrowed_pool else None
        for entry in flattened_controls:
            if narrowed_ids is not None and id(entry) in narrowed_ids:
                continue
            score = _entry_score(entry)
            if score is None:
                continue
            if score > best_score:
                best_score = score
                best_entry = dict(entry)
                best_entry["_fuzzy_score"] = score
    return best_entry


def _search_uia_by_json_entry(json_entry, window):
    """根据 JSON 条目属性在运行时 UIA 树中搜索，返回 UIAWrapper 或 None。"""
    if not json_entry or window is None:
        return None
    # 构造临时 control_definition
    ctrl_def = {
        "automationId": json_entry.get("automationId", ""),
        "className": json_entry.get("className", ""),
        "controlType": json_entry.get("controlType", ""),
        "name": json_entry.get("name", "") or json_entry.get("displayName", ""),
        "targetMethod": "",
        "targetValue": "",
    }
    # 优先用 automationId
    if ctrl_def["automationId"]:
        ctrl_def["targetMethod"] = "automation_id,control_type"
        ctrl_def["targetValue"] = "{aid},{ct}".format(
            aid=ctrl_def["automationId"], ct=ctrl_def["controlType"]
        )
    elif ctrl_def["name"]:
        ctrl_def["targetMethod"] = "name,control_type"
        ctrl_def["targetValue"] = "{name},{ct}".format(
            name=ctrl_def["name"], ct=ctrl_def["controlType"]
        )
    elif ctrl_def["className"]:
        ctrl_def["targetMethod"] = "class_name,control_type"
        ctrl_def["targetValue"] = "{cls},{ct}".format(
            cls=ctrl_def["className"], ct=ctrl_def["controlType"]
        )
    else:
        return None
    # 使用现有快速定位器
    for candidate in iter_fast_locator_candidates(window, ctrl_def):
        if wrapper_matches_control_definition(candidate, ctrl_def):
            # 确保返回 UIAWrapper 而非原始 dict
            if hasattr(candidate, 'element_info'):
                return candidate
            continue
    # 降级到全量 descendants
    try:
        for candidate in window.descendants():
            if wrapper_matches_control_definition(candidate, ctrl_def):
                if hasattr(candidate, 'element_info'):
                    return candidate
                continue
    except Exception:
        pass
    return None


def _find_by_bbox_fallback(candidates, expected_pos, window_title_hint=""):
    """Return the nearest bbox candidate to expected_pos, optionally filtered by window title."""
    if not candidates or not expected_pos:
        return None
    exp_x, exp_y = expected_pos
    title_hint = str(window_title_hint or "").strip().lower()
    best = None
    best_dist = float("inf")
    for entry in candidates:
        if title_hint:
            entry_title = entry.get("windowTitle") or entry.get("window") or entry.get("title") or ""
            if entry_title:
                if title_hint not in str(entry_title).strip().lower():
                    continue
        bbox = entry.get("boundingBox") or entry.get("bbox") or {}
        left = bbox.get("left", 0)
        top = bbox.get("top", 0)
        right = bbox.get("right", 0)
        bottom = bbox.get("bottom", 0)
        if not (left or top or right or bottom):
            continue
        cx = (left + right) / 2.0
        cy = (top + bottom) / 2.0
        dist = ((cx - exp_x) ** 2 + (cy - exp_y) ** 2) ** 0.5
        if dist < best_dist:
            best_dist = dist
            best = dict(entry)
            best["_bbox_distance"] = best_dist
    return best


# ── 定位分阶段耗时统计 ────────────────────────────────────────────────────
# key: "step_id|control_id" → {
#     "t_windows_ms": float, "t_fast_ms": float, "t_descendants_ms": float,
#     "t_json_ms": float, "t_action_ms": float, "t_total_ms": float,
# }
_step_timing = {}


def get_step_timing(step_id, control_id=""):
    """获取并清除指定步骤的分阶段耗时记录，供 executor 汇总日志。"""
    key = f"{step_id}|{control_id}"
    return _step_timing.pop(key, None)


def _record_locator_timing(step_id, control_id, t0, t1, t2, t3, t4):
    """记录 find_flow_control 四个阶段的耗时（毫秒），供下游汇总。"""
    key = f"{step_id}|{control_id}"
    _step_timing[key] = {
        "t_windows_ms": round((t1 - t0) * 1000, 2),
        "t_fast_ms": round((t2 - t1) * 1000, 2),
        "t_descendants_ms": round((t3 - t2) * 1000, 2),
        "t_json_ms": round((t4 - t3) * 1000, 2),
        "t_action_ms": 0.0,
        "t_total_ms": 0.0,
    }


# 滚入视口的"贴底余量"曾尝试让贴底控件（并行核数）再多滚一档，实机验证发现
# 该判定会误触发 ScrollViewer 祖先整页滚动，把本可正常键入的控件滚出屏幕上方
# （step_20 海拔 rect (515,1396) → 滚动后 (515,-362)，键入落空）。贴底控件
# 在窗口内时保持原行为：不滚动、直接键入。此常量仅保留作失败教训记录。
_SCROLL_VIEW_BOTTOM_MARGIN = 100


def _scroll_flow_control_into_view(control, step_id="", control_id="", force_top=False):
    """将 WPF ScrollViewer 内的离屏控件滚动到视口可见。

    部分输入框（如 WRA 编辑器的并行核数 textbox）位于滚动容器深处
    （boundingRect y 可能超出窗口高度），click_input/send_keys 直接点击
    屏幕外坐标会落空或点到其它位置。

    判定离屏的方式：取控件 boundingRect 与所在顶层窗口可视区域比较，
    控件完全落在窗口可视区域外（或大部分超出）即视为离屏。pywinauto 0.6.9
    的 UIAWrapper 没有 is_offscreen()（调用会抛 AttributeError），因此不能
    依赖该方法，必须用坐标判断。

    force_top=True 时跳过离屏判定，无条件尝试把滚动容器滚到顶部（用于
    流程步骤配置 preScrollToTop，如"键入描述前先滚动到最上面"，名称/描述
    输入框位于 WRA 编辑器顶部，录制时 boundingRect 为负 y 离屏，必须显式
    滚动到顶部才能看到）。

    滚动方式依次尝试：
      1) ScrollItemPattern.ScrollIntoView()（WPF ScrollViewer 子项通常支持）；
      2) 查找可滚动的 ScrollViewer 祖先，用 ScrollPattern 向下滚动一页；
      3) 鼠标滚轮兜底（将光标移到控件 boundingRect 处滚轮）。

    滚动后再校验一次坐标，若仍离屏则继续下一级兜底。
    返回 True 表示已尝试滚动（或控件本就可见）；失败不阻断后续动作。
    """
    if control is None:
        return False
    _log = lambda msg: _LOG_STEP(msg)
    _label = f"step={step_id}, control={control_id}" if step_id else ""

    def _rect_xy(rect):
        try:
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            return None

    def _get_window_rect():
        # 取控件所在顶层窗口的可视区域（与控件 rect 同坐标系）。
        # 注意：不能用控件 native handle + GetAncestor —— WPF 深层 UIA 元素
        # handle 常为 0，GetAncestor(0) 失败导致拿不到窗口 rect、离屏判定恒 False。
        # 改用 UIA 树向上找 top_level_parent，其 rectangle() 即窗口边界。
        try:
            top = control.top_level_parent()
            if top is None:
                return None
            rect = top.rectangle()
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        except Exception:
            pass
        return None

    def _is_offscreen(rect):
        """判定控件是否在窗口可视区域外。
        视口留 8px 容差：控件 bottom 超出窗口 bottom、或 top 超出窗口 top、
        或水平方向完全超出窗口左右边界，均视为离屏。
        """
        if rect is None:
            return False
        win_rect = _get_window_rect()
        if win_rect is None:
            return False
        win_left, win_top, win_right, win_bottom = win_rect
        c_left, c_top, c_right, c_bottom = rect
        margin = 8
        below = c_bottom > win_bottom - margin
        above = c_top < win_top + margin
        beyond_left = c_right < win_left + margin
        beyond_right = c_left > win_right - margin
        return bool(below or above or beyond_left or beyond_right)

    # 读取控件当前 rect
    try:
        raw_rect = control.rectangle()
        cur_rect = _rect_xy(raw_rect)
    except Exception:
        cur_rect = None

    if not _is_offscreen(cur_rect):
        # 控件在窗口可视区域内 → 无需滚动（force_top 同理：控件已可见时
        # 不再执行无谓滚动，避免 SetScrollPercent 干扰 TextBox 输入状态/焦点，
        # 曾导致 step_3 键入名称时 click_input 后 set_edit_text 失败）
        return True
    if force_top:
        _log(
            f"[滚动] preScrollToTop: 强制滚动到顶部: {_label}, "
            f"控件rect={cur_rect}, 窗口rect={_get_window_rect()}"
        )

    _log(
        f"[滚动] 控件在窗口可视区域外，尝试滚入视口: {_label}, "
        f"控件rect={cur_rect}, 窗口rect={_get_window_rect()}"
    )

    # 1) ScrollItemPattern.ScrollIntoView
    try:
        scroll_item = control.iface_scroll_item
        scroll_item.ScrollIntoView()
        time.sleep(0.35)
        try:
            after_rect = _rect_xy(control.rectangle())
        except Exception:
            after_rect = None
        if not _is_offscreen(after_rect):
            _log(f"[滚动] ScrollItemPattern 滚动成功: {_label}, rect={after_rect}")
            return True
        _log(f"[滚动] ScrollItemPattern 滚动后仍离屏: {_label}, rect={after_rect}")
    except Exception:
        pass

    # 2) 向上查找可滚动的 ScrollViewer 祖先：若控件在下方则直接滚到底，
    #    若在上方则滚到顶（用 SetScrollPercent 一步到位，失败再逐页滚动）。
    try:
        ancestor = control.parent()
        depth = 0
        while ancestor is not None and depth < 12:
            try:
                scroll_if = ancestor.iface_scroll
                if scroll_if and scroll_if.CurrentVerticallyScrollable:
                    win_rect = _get_window_rect()
                    # 控件在窗口下方 / 底部 240px 内 → 直接滚到底，确保完全进入视口；
                    # force_top 时无条件滚到顶（名称/描述输入框位于编辑区顶部）
                    below = bool(not force_top and cur_rect and win_rect and cur_rect[3] > win_rect[3] - 240)
                    above = bool(force_top or (cur_rect and win_rect and cur_rect[1] < win_rect[1] + 240))
                    try:
                        if below:
                            scroll_if.SetScrollPercent(-1, 100.0)  # 直接滚到底
                        elif above:
                            scroll_if.SetScrollPercent(-1, 0.0)    # 滚到顶
                        else:
                            scroll_if.SetScrollPercent(-1, 50.0)
                        time.sleep(0.35)
                    except Exception:
                        # SetScrollPercent 不可用 → 逐页向下滚直到可见
                        for _step in range(8):
                            scroll_if.Scroll(0, 1)  # NoAmount->SmallIncrement? 见下
                            time.sleep(0.15)
                            try:
                                after_rect = _rect_xy(control.rectangle())
                            except Exception:
                                after_rect = None
                            if not _is_offscreen(after_rect):
                                break
                    try:
                        after_rect = _rect_xy(control.rectangle())
                    except Exception:
                        after_rect = None
                    if not _is_offscreen(after_rect):
                        _log(f"[滚动] ScrollViewer 祖先滚动到底/顶成功: {_label}, rect={after_rect}")
                        return True
                    _log(f"[滚动] ScrollViewer 祖先滚动后仍离屏: {_label}, rect={after_rect}")
            except Exception:
                pass
            ancestor = ancestor.parent()
            depth += 1
    except Exception:
        pass

    # 3) 鼠标滚轮兜底：把光标移到控件附近多次滚动（WPF 滚动容器对滚轮敏感）
    try:
        if cur_rect is not None:
            import pyautogui
            win_rect = _get_window_rect()
            for _wheel_round in range(3):
                try:
                    after_rect = _rect_xy(control.rectangle())
                except Exception:
                    after_rect = cur_rect
                cx = int((after_rect[0] + after_rect[2]) / 2)
                cy = int((after_rect[1] + after_rect[3]) / 2)
                if win_rect:
                    # 光标落点在窗口内部（避免滚到其它窗口）
                    cx = max(win_rect[0] + 20, min(cx, win_rect[2] - 20))
                    cy = max(win_rect[1] + 40, min(cy, win_rect[3] - 20))
                pyautogui.moveTo(cx, cy, duration=0.05)
                pyautogui.scroll(-8, x=cx, y=cy)
                time.sleep(0.3)
                try:
                    after_rect = _rect_xy(control.rectangle())
                except Exception:
                    after_rect = None
                if not _is_offscreen(after_rect):
                    _log(f"[滚动] 鼠标滚轮滚动成功: {_label}, rect={after_rect}")
                    return True
            _log(f"[滚动] 鼠标滚轮滚动后仍离屏: {_label}, rect={after_rect}")
    except Exception:
        pass

    return False


def _finalize_step_timing(step_id, control_id, t_act_start):
    """在动作执行完成后，合并定位四阶段耗时 + 动作耗时，输出一条汇总日志。"""
    key = f"{step_id}|{control_id}"
    timing = _step_timing.get(key)
    if timing is None:
        return
    t_act_ms = round((time.perf_counter() - t_act_start) * 1000, 2)
    timing["t_action_ms"] = t_act_ms
    timing["t_total_ms"] = round(
        timing["t_windows_ms"] + timing["t_fast_ms"] + timing["t_descendants_ms"]
        + timing["t_json_ms"] + t_act_ms, 2
    )
    _LOG_STEP(
        "[定位耗时] step={}, ctrl={}, 窗枚举={:.1f}ms, 快查={:.1f}ms, "
        "整树={:.1f}ms, JSON={:.1f}ms, 动作={:.1f}ms, 总计={:.1f}ms".format(
            step_id, control_id,
            timing["t_windows_ms"], timing["t_fast_ms"], timing["t_descendants_ms"],
            timing["t_json_ms"], t_act_ms, timing["t_total_ms"],
        )
    )
    _step_timing.pop(key, None)


def _classify_control_richness(control_definition: dict) -> str:
    """根据控件定义的字段丰富度分类，返回 'high' / 'mid' / 'low'。
    
    high: 有 automationId 或 uiPath → 可精确定位，应维持高阈值
    mid: 有 name + controlType，无 automationId → 需要评分匹配，可适度放宽
    low: 仅有 name 或极少字段 → 需要模糊匹配，可大幅放宽
    """
    if not isinstance(control_definition, dict):
        return "mid"
    
    # 检查 inspectData 中的字段
    inspect = control_definition.get("inspectData", {})
    if not inspect:
        inspect = control_definition  # fallback 到顶层
    
    automation_id = inspect.get("automationId") or control_definition.get("automationId") or control_definition.get("targetValue", "")
    name = inspect.get("name") or control_definition.get("name", "")
    control_type = inspect.get("controlType") or control_definition.get("controlType", "")
    ui_path = control_definition.get("uiPath", "")
    
    # 检查 targetMethod 是否使用了 automation_id
    target_method = control_definition.get("targetMethod", "")
    uses_automation_id = "automation_id" in target_method if target_method else bool(automation_id)
    
    if uses_automation_id and automation_id:
        return "high"
    if ui_path:
        return "high"
    if name and control_type:
        return "mid"
    return "low"


def _get_adaptive_threshold(richness: str) -> int:
    """根据数据丰富度返回匹配阈值。
    
    high: 100 (维持精确匹配，不放宽)
    mid:  50  (适度放宽，允许部分匹配)
    low:  1   (极度放宽，只要有正分即可)
    """
    return {"high": 100, "mid": 50, "low": 1}.get(richness, 100)


def _window_is_responsive(hwnd, timeout_ms=500):
    """目标窗口消息泵响应探测（SendMessageTimeout WM_NULL）。

    应用忙（复制/计算等）时 UIA 属性查询会阻塞数分钟且无法被 Python 侧超时中断
    （实测复制综合后整树枚举挂起 487-742s）。探测在枚举前识别无响应窗口并跳过，
    让"应用忙"期间的定位快速失败，由上层轮询等待应用恢复。
    返回 False 表示窗口无响应；句柄无效/探测异常时保守放行（返回 True）。
    """
    try:
        hwnd = int(hwnd or 0)
        if not hwnd:
            return True
        result = ctypes.c_ulong()
        ok = ctypes.windll.user32.SendMessageTimeoutW(
            hwnd, 0, 0, 0, 2,  # WM_NULL, SMTO_ABORTIFHUNG
            int(timeout_ms), ctypes.byref(result)
        )
        return bool(ok)
    except Exception:
        return True


def _find_flow_control_impl(step_id, control_id=None, timeout_seconds=3, window_title_hint="", control_map_path=None):
    # ── 阶段计时初始化 ──────────────────────────────────────────────────────
    _t0 = time.perf_counter()
    _reset_silent_exception_counts()
    # label 矩形缓存带 TTL 跨调用保留：同一目标窗口在几十秒内的连续步骤共享
    # 一次全树 label 扫描结果，避免每个步骤重复支付 20-36 秒的全树扫描。
    # （缓存 get/put 内部按 _LABEL_RECT_CACHE_TTL 自动过期，无需硬清空。）
    _t1 = _t2 = _t3 = _t4 = _t0
    step_definition = _GET_STEP_DEFINITION(step_id)
    controls = step_definition.get("controls", []) if isinstance(step_definition, dict) else []
    if control_id:
        controls = [control for control in controls if str(control.get("id", "")).strip() == control_id]
    if not controls:
        return None
    
    # ⚡ 性能优化：在 while 循环外预先计算 richness 和阈值（只计算一次）
    # 这些值在所有 retry 迭代中保持不变，避免重复计算开销
    _richness = _classify_control_richness(controls[0] if controls else {})
    _adaptive_threshold = _get_adaptive_threshold(_richness)
    
    # 优先 Tab 导航降级：控件配置 preferTabNavigation 时，先尝试 Tab 定位再回退常规。
    # （编辑器里“优先使用 Tab 导航（跳过常规定位尝试）”的运行时语义）
    if any(bool(ctrl.get("preferTabNavigation")) for ctrl in controls):
        # 整个 preferTab 块包异常兜底：配置异常（steps 非法等）或底层 UIA 挂起时
        # 记录日志并回退常规定位链，而不是把异常直接炸穿 find_flow_control。
        try:
            for _prefer_raw_cd in controls:
                _prefer_tab_cd = normalize_control_definition(
                    _apply_self_heal_override(step_id, control_id, _prefer_raw_cd)
                )
                _prefer_tab_windows = list(iter_flow_search_windows(
                    step_definition,
                    window_title_hint=window_title_hint,
                    control_definition=_prefer_tab_cd,
                ))
                _prefer_tab_result = _try_tab_navigation_fallback(
                    _prefer_tab_windows, _prefer_tab_cd, step_id=step_id
                )
                if _prefer_tab_result is not None:
                    _prefer_best, _prefer_score = _prefer_tab_result
                    for _cd in controls:
                        cache_flow_control(step_id, _cd, _prefer_best, window_title_hint=window_title_hint)
                    _t4 = time.perf_counter()
                    _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                    _LOG_STEP(
                        f"优先 Tab 导航定位命中: step={step_id}, control={control_id or '(first)'}, score={_prefer_score}"
                    )
                    return _prefer_best
        except Exception as _prefer_tab_exc:
            _LOG_STEP(
                f"优先 Tab 导航异常，回退常规定位: step={step_id}, control={control_id or '(first)'}, error={_prefer_tab_exc}"
            )
        # Tab 导航未命中：继续常规定位

    deadline = time.time() + timeout_seconds
    last_error = None
    search_started = time.time()
    _uipi_marker_before = _UIPI_BLOCK_DETECTED.get("timestamp", 0.0)
    _uipi_short_circuit = False
    while time.time() < deadline:
        try:
            best_match = None
            best_score = -1
            windows = []
            _json_fallback_entry = None
            _low_confidence_match = None
            _low_confidence_score = -1
            for raw_control_definition in controls:
                control_definition = normalize_control_definition(
                    _apply_self_heal_override(step_id, control_id, raw_control_definition)
                )
                cached_wrapper = get_cached_flow_control(step_id, control_definition, window_title_hint=window_title_hint)
                if cached_wrapper is not None:
                    if str(step_id).strip() == "step_2" and get_wrapper_is_offscreen(cached_wrapper) == "True":
                        cached_wrapper = None
                    else:
                        return cached_wrapper
                foreground_wrapper = _try_get_window_by_handle(get_foreground_window_handle())
                expected_window_title = normalize_match_text(
                    control_definition.get("windowTitle", "")
                    or (step_definition.get("windowTitle", "") if isinstance(step_definition, dict) else "")
                    or window_title_hint
                )
                windows = iter_flow_search_windows(
                    step_definition,
                    window_title_hint=window_title_hint,
                    control_definition=control_definition,
                    allow_soften=(_richness == "low"),
                )
                if not windows and foreground_wrapper is not None:
                    foreground_title = normalize_match_text(get_wrapper_text(foreground_wrapper))
                    if expected_window_title and value_matches(foreground_title, expected_window_title):
                        windows = [foreground_wrapper]
                # 窗口响应探测：应用忙（复制/计算等）时 UIA 查询会阻塞数分钟且无法被
                # 超时中断（实测复制综合后整树枚举挂起 487-742s）。枚举前对候选窗口
                # 做 SendMessageTimeout(WM_NULL) 探测，无响应窗口直接跳过——宁可快速
                # 失败等应用恢复后再定位，也不在无响应窗口上发起必然挂起的查询。
                if windows:
                    _responsive_windows = []
                    for _probe_window in windows:
                        _probe_hwnd = get_wrapper_handle(_probe_window) or 0
                        if _probe_hwnd and not _window_is_responsive(_probe_hwnd):
                            _LOG_STEP(
                                "[FlowLocator] 窗口无响应，跳过定位: step={}, control={}, hwnd={}".format(
                                    step_id, control_id, hex(int(_probe_hwnd))
                                )
                            )
                            continue
                        _responsive_windows.append(_probe_window)
                    if not _responsive_windows:
                        last_error = RuntimeError(
                            "候选窗口均无响应(应用忙): step={}, control={}, windows={}".format(
                                step_id, control_id, len(windows)
                            )
                        )
                        _LOG_STEP(
                            "[FlowLocator] 候选窗口均无响应，快速失败等重试: step={}, control={}".format(
                                step_id, control_id
                            )
                        )
                        break
                    windows = _responsive_windows
                if not windows and _uipi_block_active(_uipi_marker_before):
                    _uipi_diag = _uipi_block_active(_uipi_marker_before) or {}
                    _LOG_STEP(
                        "[FlowLocator] UIPI 快速短路: step={}, control={}, UIA 内容树被隔离，"
                        "跳过整树/JSON/重试; diagnostic={}".format(
                            step_id,
                            control_id,
                            json.dumps(_uipi_diag, ensure_ascii=False),
                        )
                    )
                    last_error = RuntimeError(
                        "UIPI/UIA 内容树隔离快速短路: step={}, control={}, diagnostic={}".format(
                            step_id,
                            control_id,
                            json.dumps(_uipi_diag, ensure_ascii=False),
                        )
                    )
                    _uipi_short_circuit = True
                    break
                _t1 = time.perf_counter()  # Phase 1: 窗口枚举完成
                # IsControlElement=False 的控件在 Control View 中必然不可见，
                # fast 与整树扫描必定空转（WPF 大树下可达数秒），直接走 Raw View。
                expects_raw = control_definition_expects_raw_view(control_definition)
                for window in windows:
                    if expects_raw:
                        break
                    for candidate in iter_fast_locator_candidates(window, control_definition):
                        # fast 阶段可能对巨大窗口做全量枚举（如 label_text 匹配），
                        # 单轮就可能远超 deadline；逐候选检查，超时就放弃继续搜索。
                        if time.time() > deadline:
                            break
                        if not wrapper_matches_control_definition(candidate, control_definition):
                            continue
                        if str(step_id).strip() == "step_2" and get_wrapper_is_offscreen(candidate) == "True":
                            continue
                        score = score_control_match(candidate, control_definition)
                        if score > best_score:
                            best_score = score
                            best_match = candidate
                            # 高分候选立即返回：候选列表可能很大（如泛化 automationId 的
                            # textbox/PART_ContentHost 有几十上百个），遍历全部既慢又无意义。
                            if best_score >= 100:
                                break
                    if best_match is not None and best_score >= 100:
                        cache_wrapper_parent_chain(window, best_match)
                        cache_flow_control(step_id, control_definition, best_match, window_title_hint=window_title_hint)
                        elapsed = time.time() - search_started
                        if elapsed >= 0.8:
                            _LOG_STEP(
                                f"流程控件定位耗时较长: step={step_id}, control={control_id or '(first)'}, "
                                f"seconds={elapsed:.2f}, score={best_score}, phase=fast"
                            )
                        if str(step_id).strip() == "step_2":
                            _emit_fan_type_create_debug_event("B", "wt_flow_locator.py:find_flow_control:fast-hit", "step_2 control matched", {"window": get_wrapper_debug_snapshot(window), "control": get_wrapper_debug_snapshot(best_match), "score": best_score})
                        _t2 = _t3 = _t4 = time.perf_counter()
                        _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                        return best_match
                    # 记录低置信匹配（正分但不足100）
                    if best_match is not None and 0 < best_score < _adaptive_threshold:
                        if best_score > _low_confidence_score:
                            _low_confidence_match = best_match
                            _low_confidence_score = best_score
                _t2 = time.perf_counter()  # Phase 2: 快速查询结束
                for window in windows:
                    if expects_raw:
                        break
                    # 巨大 WPF 窗口全量 descendants 可能阻塞数十秒（实测 141s），
                    # 优先用 UIA 原生 FindAll(automationId) 快速找候选（数量级提速）。
                    for candidate in _iter_raw_view_findall_candidates(window, control_definition):
                        if not wrapper_matches_control_definition(candidate, control_definition):
                            continue
                        if str(step_id).strip() == "step_2" and get_wrapper_is_offscreen(candidate) == "True":
                            continue
                        score = score_control_match(candidate, control_definition)
                        if score > best_score:
                            best_score = score
                            best_match = candidate
                    if best_match is not None and best_score >= 100:
                        cache_wrapper_parent_chain(window, best_match)
                        cache_flow_control(step_id, control_definition, best_match, window_title_hint=window_title_hint)
                        elapsed = time.time() - search_started
                        if elapsed >= 0.8:
                            _LOG_STEP(
                                f"流程控件定位命中(FindAll): step={step_id}, control={control_id or '(first)'}, "
                                f"seconds={elapsed:.2f}, score={best_score}"
                            )
                        _t3 = _t4 = time.perf_counter()
                        _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                        return best_match
                    candidates = [window]
                    expected_type = normalize_control_type_name(
                        control_definition.get("controlType", ""),
                        control_definition.get("inspectData", {}).get("controlType", ""),
                    )
                    try:
                        candidates.extend(window.descendants(control_type=expected_type) if expected_type else window.descendants())
                    except Exception:
                        pass
                    for candidate in candidates:
                        if time.time() > deadline:
                            break
                        if not wrapper_matches_control_definition(candidate, control_definition):
                            continue
                        if str(step_id).strip() == "step_2" and get_wrapper_is_offscreen(candidate) == "True":
                            continue
                        score = score_control_match(candidate, control_definition)
                        if score > best_score:
                            best_score = score
                            best_match = candidate
                    if best_match is not None and best_score >= 100:
                        cache_wrapper_parent_chain(window, best_match)
                        cache_flow_control(step_id, control_definition, best_match, window_title_hint=window_title_hint)
                        elapsed = time.time() - search_started
                        if elapsed >= 0.8:
                            _LOG_STEP(
                                f"流程控件定位耗时较长: step={step_id}, control={control_id or '(first)'}, "
                                f"seconds={elapsed:.2f}, score={best_score}, phase=fallback"
                            )
                        if str(step_id).strip() == "step_2":
                            _emit_fan_type_create_debug_event("B", "wt_flow_locator.py:find_flow_control:descendant-hit", "step_2 control matched by descendants", {"window": get_wrapper_debug_snapshot(window), "control": get_wrapper_debug_snapshot(best_match), "score": best_score})
                        _t3 = _t4 = time.perf_counter()
                        _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                        return best_match
                    # 记录低置信匹配（正分但不足100）
                    if best_match is not None and 0 < best_score < _adaptive_threshold:
                        if best_score > _low_confidence_score:
                            _low_confidence_match = best_match
                            _low_confidence_score = best_score
                # --- 阈值放宽：fast+descendants 不足自适应阈值时，取最高正分 ---
                # 仅 mid/low 丰富度允许放宽；high（阈值 100，有 automationId/uiPath 可精确定位）
                # 必须维持精确匹配，任何正分低置信命中都不得当作成功。
                if (_low_confidence_match is not None
                        and _adaptive_threshold < 100
                        and (best_match is None or best_score < _adaptive_threshold)):
                    best_match = _low_confidence_match
                    best_score = _low_confidence_score
                    _LOG_STEP(
                        "[FlowLocator] 阈值放宽命中：step=%s, ctrl=%s, score=%s(threshold=%s, richness=%s)"
                        % (step_id, control_id, best_score, _adaptive_threshold, _richness)
                    )
                    _t3 = _t4 = time.perf_counter()
                    _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                    return best_match
                if best_match is None and expects_raw:
                    for window in windows:
                        for candidate in iter_raw_view_fallback_candidates(window, control_definition):
                            score = score_control_match(candidate, control_definition)
                            resolved_candidate = _resolve_control_definition_target(candidate, control_definition)
                            if resolved_candidate is None:
                                # 无法提升到可编辑控件（孤儿 PART_ContentHost）：
                                # 保留原始宿主候选，交由执行侧点击+键入兜底
                                resolved_candidate = candidate
                            if score > best_score:
                                best_score = score
                                best_match = resolved_candidate
                            # 达到自适应阈值即命中：终止 BFS 生成器，不再扫描剩余 Raw View 元素
                            if best_score >= _adaptive_threshold:
                                break
                        if best_score >= _adaptive_threshold:
                            break
                    if best_match is not None:
                        cache_wrapper_parent_chain(windows[0], best_match)
                        cache_flow_control(step_id, control_definition, best_match, window_title_hint=window_title_hint)
                        _hit_snapshot = _safe_get_value(
                            lambda: "aid={}, type={}, name={}, class={}".format(
                                get_wrapper_automation_id(best_match),
                                get_wrapper_control_type(best_match),
                                get_wrapper_text(best_match),
                                get_wrapper_class_name(best_match),
                            ),
                            "",
                        )
                        _LOG_STEP(
                            f"流程控件定位命中 Raw View: step={step_id}, control={control_id or '(first)'}, score={best_score}, "
                            f"hit=({_hit_snapshot})"
                        )
                        _t3 = _t4 = time.perf_counter()
                        _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                        return best_match
            if _uipi_short_circuit:
                break
            # --- 新阶段 A：控件库 JSON 模糊匹配 ---
            # --- ??? A1?labelText ?????????????->????? ---
            if best_match is None:
                for _raw_cd in controls:
                    _label_cd = normalize_control_definition(
                        _apply_self_heal_override(step_id, control_id, _raw_cd)
                    )
                    _label_wrapper = _try_label_to_input_fallback(windows, _label_cd, step_id=step_id)
                    if _label_wrapper is not None:
                        best_match = _label_wrapper
                        best_score = max(best_score, 80)
                        break
                if best_match is not None:
                    _last_window = windows[-1] if windows else None
                    if _last_window is not None:
                        cache_wrapper_parent_chain(_last_window, best_match)
                    for _cd in controls:
                        cache_flow_control(step_id, _cd, best_match, window_title_hint=window_title_hint)
                    _t4 = time.perf_counter()
                    _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                    return best_match
            # --- ??? A2?Tab ???? ---
            if best_match is None:
                for _raw_cd in controls:
                    _tab_cd = normalize_control_definition(
                        _apply_self_heal_override(step_id, control_id, _raw_cd)
                    )
                    _tab_result = _try_tab_navigation_fallback(windows, _tab_cd, step_id=step_id)
                    if _tab_result is not None:
                        best_match, best_score = _tab_result
                        break
                if best_match is not None:
                    _last_window = windows[-1] if windows else None
                    if _last_window is not None:
                        cache_wrapper_parent_chain(_last_window, best_match)
                    for _cd in controls:
                        cache_flow_control(step_id, _cd, best_match, window_title_hint=window_title_hint)
                    _t4 = time.perf_counter()
                    _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                    return best_match
            _t3 = time.perf_counter()  # Phase 3: 整树 fallback 结束
            if control_map_path and fuzz is not None and best_match is None:
                _cm_flattened, _cm_name_index, _cm_aid_index, _cm_ct_index = _load_control_map_index(control_map_path)
                if _cm_flattened:
                    for _cd in controls:
                        _step_label = _cd.get("label") or _cd.get("name") or _cd.get("labelText") or ""
                        if not _step_label:
                            continue
                        _json_fallback_entry = _fuzzy_match_control_map(
                            _step_label,
                            _cd,
                            _cm_flattened,
                            _cm_name_index,
                            _cm_aid_index,
                            _cm_ct_index,
                        )
                        if _json_fallback_entry is not None:
                            break
                    if _json_fallback_entry is not None:
                        _last_window = windows[-1] if windows else None
                        if _last_window is not None:
                            _json_wrapper = _search_uia_by_json_entry(_json_fallback_entry, _last_window)
                            if _json_wrapper is not None and hasattr(_json_wrapper, 'element_info'):
                                _LOG_STEP(
                                    "[FlowLocator] 控件库JSON模糊匹配命中: label={}, score={}".format(
                                        _json_fallback_entry.get("displayName") or _json_fallback_entry.get("name", ""),
                                        _json_fallback_entry.get("_fuzzy_score", ""),
                                    )
                                )
                                cache_wrapper_parent_chain(_last_window, _json_wrapper)
                                for _cd in controls:
                                    cache_flow_control(step_id, _cd, _json_wrapper, window_title_hint=window_title_hint)
                                _t4 = time.perf_counter()
                                _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                                return _json_wrapper
            # --- 新阶段 B：bbox 空间距离兜底 ---
            # 说明：此阶段只在 fuzzy 命中后的二级降级，且必须依赖 JSON 条目自身的 bbox/expectedX/Y；
            # 当前流程定义没有独立的坐标来源，因此不改变现有语义，仅作定位证据记录。
            if _json_fallback_entry is not None and best_match is None:
                _bbox = _json_fallback_entry.get("boundingBox") or _json_fallback_entry.get("bbox") or {}
                _exp_x = _json_fallback_entry.get("expectedX") or _bbox.get("left", 0)
                _exp_y = _json_fallback_entry.get("expectedY") or _bbox.get("top", 0)
                # 如果 bbox 有完整信息则用中心点
                if _bbox.get("right") and _bbox.get("bottom"):
                    _exp_x = (_bbox.get("left", 0) + _bbox.get("right", 0)) / 2.0
                    _exp_y = (_bbox.get("top", 0) + _bbox.get("bottom", 0)) / 2.0
                if _exp_x is not None or _exp_y is not None:
                    _cm_flattened_b, _, _, _ = _load_control_map_index(control_map_path)
                    if _cm_flattened_b:
                        _bbox_candidates = [c for c in _cm_flattened_b if c.get("boundingBox") or c.get("bbox")]
                        _bbox_hit = _find_by_bbox_fallback(
                            _bbox_candidates,
                            (_exp_x, _exp_y),
                            window_title_hint=window_title_hint,
                        )
                        if _bbox_hit is not None:
                            _last_window_b = windows[-1] if windows else None
                            if _last_window_b is not None:
                                _bbox_wrapper = _search_uia_by_json_entry(_bbox_hit, _last_window_b)
                                if _bbox_wrapper is not None and hasattr(_bbox_wrapper, 'element_info'):
                                    _LOG_STEP(
                                        "[FlowLocator] bbox空间距离兜底命中: label={}, distance={}".format(
                                            _bbox_hit.get("displayName") or _bbox_hit.get("name", ""),
                                            _bbox_hit.get("_bbox_distance", ""),
                                        )
                                    )
                                    cache_wrapper_parent_chain(_last_window_b, _bbox_wrapper)
                                    for _cd in controls:
                                        cache_flow_control(step_id, _cd, _bbox_wrapper, window_title_hint=window_title_hint)
                                    _t4 = time.perf_counter()
                                    _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                                    return _bbox_wrapper
            # --- JSON fallback 结束，记录 checkpoint ---
            _t4 = time.perf_counter()  # Phase 4: JSON fallback 结束
            if best_match is not None and best_score >= _adaptive_threshold:
                for control_definition in controls:
                    cache_flow_control(step_id, control_definition, best_match, window_title_hint=window_title_hint)
                _maybe_report_self_heal(step_id, control_id, best_match, controls)
                elapsed = time.time() - search_started
                if elapsed >= 0.8:
                    _LOG_STEP(
                        f"流程控件定位耗时较长：step={step_id}, control={control_id or '(first)'}, "
                        f"seconds={elapsed:.2f}, score={best_score}"
                    )
                _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
                return best_match
            if len(windows) <= 2:
                _LOG_STEP(
                    "[FlowLocator] 窗口枚举概要: step={step_id}, count={count}, windows={windows}".format(
                        step_id=step_id,
                        count=len(windows),
                        windows=json.dumps(
                            [
                                {
                                    "title": get_wrapper_text(w),
                                    "class": get_wrapper_class_name(w),
                                    "framework": get_wrapper_framework_id(w),
                                    "process": get_wrapper_process_id(w),
                                    "hwnd": get_wrapper_handle_text(w),
                                    "offscreen": get_wrapper_is_offscreen(w),
                                }
                                for w in windows
                            ],
                            ensure_ascii=False,
                        ),
                    )
                )
            # [诊断] 定位失败时打印 Raw View（FindAll automationId=PART_ContentHost）候现实况，
            # 排查"名称/描述"同名同结构输入框的 found_index 消歧为何失效。
            try:
                _diag_win = windows[-1] if windows else None
                if _diag_win is not None:
                    _diag_candidates = _iter_raw_view_findall_candidates(
                        _diag_win,
                        {"automationId": "PART_ContentHost", "inspectData": {"automationId": "PART_ContentHost"}},
                        max_results=60,
                    )
                    _diag_snap = []
                    for _c in (_diag_candidates or []):
                        try:
                            _diag_snap.append({
                                "type": get_wrapper_control_type(_c),
                                "class": get_wrapper_class_name(_c),
                                "name": get_wrapper_text(_c),
                                "offscreen": get_wrapper_is_offscreen(_c),
                                "rect": str(get_wrapper_rectangle(_c) or {}),
                                "idx_ctrl_Pane": get_wrapper_found_index(_c, "control_type", "Pane"),
                                "idx_cls_ScrollViewer": get_wrapper_found_index(_c, "class_name", "ScrollViewer"),
                            })
                        except Exception:
                            continue
                    _LOG_STEP("[FlowLocator][候选][raw] PART_ContentHost count={} snap={}".format(
                        len(_diag_snap), json.dumps(_diag_snap, ensure_ascii=False)))
            except Exception as _diag_outer_exc:
                _LOG_STEP("[FlowLocator][候选] 诊断失败: " + repr(_diag_outer_exc)[:200])
            last_error = RuntimeError(
                f"step={step_id}, control={control_id or '(first)'}, windows={len(windows)} 未找到匹配控件"
            )
        except Exception as exc:
            last_error = exc
        time.sleep(0.15)
    elapsed = time.time() - search_started
    _t4 = time.perf_counter()
    _record_locator_timing(step_id, control_id, _t0, _t1, _t2, _t3, _t4)
    timing = get_step_timing(step_id, control_id)
    if timing:
        _LOG_STEP(
            "[定位耗时-失败] step={}, ctrl={}, 窗枚举={:.1f}ms, 快查={:.1f}ms, "
            "整树={:.1f}ms, JSON={:.1f}ms, 总计={:.1f}ms".format(
                step_id,
                control_id,
                timing["t_windows_ms"],
                timing["t_fast_ms"],
                timing["t_descendants_ms"],
                timing["t_json_ms"],
                round(elapsed * 1000, 2),
            )
        )
    silent_counts = _snapshot_silent_exception_counts()
    silent_suffix = ""
    if silent_counts:
        silent_suffix = ", silent_exception_counts={}".format(json.dumps(silent_counts, ensure_ascii=False, sort_keys=True))
    if last_error is None:
        _LOG_STEP(
            f"流程控件定位失败: step={step_id}, control={control_id or '(first)'}, "
            f"seconds={elapsed:.2f}, reason=timeout_no_match{silent_suffix}"
        )
    else:
        tb_snippet = ""
        if isinstance(last_error, Exception) and last_error.__traceback__ is not None:
            try:
                frames = traceback.format_exception(
                    type(last_error), last_error, last_error.__traceback__
                )
                # 只取真正抛异常的帧，去掉前几行包装，保留文件:行号
                tb_snippet = " | traceback=" + " | ".join(
                    line.strip() for line in frames if line.strip()
                )[-1500:]
            except Exception:
                tb_snippet = ""
        _LOG_STEP(
            f"流程控件定位失败: step={step_id}, control={control_id or '(first)'}, "
            f"seconds={elapsed:.2f}, last_error={last_error}{tb_snippet}{silent_suffix}"
        )
    # [修复] UIPI 完整性隔离识别：窗口能找到但内容树被隔离，此前静默返回 None 被上层
    # 误报为"未命中控件"。目标进程完整性高于本进程（MUP 提权运行）时给出可执行提示。
    try:
        _higher_pids = _detect_higher_integrity_windows(windows)
    except Exception:
        _higher_pids = []
    if _higher_pids:
        _UIPI_BLOCK_DETECTED["timestamp"] = time.time()
        _UIPI_BLOCK_DETECTED["diagnostic"] = {
            "reason": "uipi_higher_integrity_target",
            "step": step_id,
            "control": control_id or "",
            "higherPids": sorted({pid for pid, _ in _higher_pids}),
        }
        _LOG_STEP(
            "[FlowLocator] UIPI 完整性隔离: step={}, control={}, 目标进程={} 以管理员/高完整性运行，"
            "本进程完整性 {}(pid={})，UIA 内容树被隔离。请以管理员身份启动本工具，"
            "或将目标软件改为普通权限启动。".format(
                step_id,
                control_id or "",
                json.dumps(_higher_pids, ensure_ascii=False),
                _process_integrity_tier(os.getpid()),
                os.getpid(),
            )
        )
        raise RuntimeError(
            "目标软件以管理员/高完整性运行，本工具未以管理员身份运行，UIA 内容树被系统隔离"
            "(UIPI)，故无法找到任何控件。请选择其一：① 以管理员身份重启本工具；"
            "② 将目标软件(MUPSmartClient)改为普通权限启动。相关窗口进程 pid={}".format(
                sorted({pid for pid, _ in _higher_pids})
            )
        )
    return None


def find_flow_control(step_id, control_id=None, timeout_seconds=3, window_title_hint="", control_map_path=None):
    """控件定位入口：在 UIA 遍历期间临时禁用 GC，防止 comtypes 释放失效 COM 指针崩溃。

    pywinauto/comtypes 的 UIA 元素数组在垃圾回收时释放 COM 对象（__del__ → Release），
    若目标软件 UIA provider 已失效会触发 "Windows fatal exception: access violation"
    或 "code 0xc0000374"（堆损坏），导致整个子进程崩溃、无任何错误详情。整个定位过程
    （descendants/children/FindAll/Raw View 遍历）都可能触发，故在此统一禁用 GC，
    遍历结束后恢复。定位过程通常秒级，禁用 GC 的内存影响可忽略。
    """
    gc_was_enabled = gc.isenabled()
    try:
        if gc_was_enabled:
            gc.disable()
        return _find_flow_control_impl(
            step_id,
            control_id=control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            control_map_path=control_map_path,
        )
    finally:
        if gc_was_enabled:
            gc.enable()


def _normalize_compare_value(value):
    """值断言比较前归一化：去空白、统一大小写，容忍输入框尾随换行/空格。"""
    try:
        return str(value or "").strip().casefold()
    except Exception:
        return str(value or "")


def _values_match_loose(actual, expected):
    """值断言宽松匹配：在归一化相等基础上，容忍数值输入框的单位后缀与精度差异。

    MUP 的数值输入框（空气密度 kg/m3、海拔 m 等）Value 通常带单位后缀
    （如 "1.220 kg/m3"、"99.00 米 (海拔）"），而 actionConfig.text 只有
    裸数值（如 "1.220"、"99"），严格相等必然假失败。

    匹配策略（按顺序）：
      1) 归一化精确相等；
      2) 期望值本身是数字时，从实际值开头提取数值前缀做浮点比较
         （"1.220 kg/m3" == "1.220"；"99.00 米 (海拔）" == "99"；
         "1.2200" == "1.220"）；
      3) 实际值以期望值开头且尾随是单位类文本（空格/单位括号/k/g 等）。

    注意保留数值语义：期望 "1.220" 不会误配实际 "1.225 kg/m3"
    （浮点 1.220 != 1.225），期望 "1.22" 也不匹配实际 "1.220" 之外
    的歧义文本。
    """
    try:
        act = str(actual or "").strip().casefold()
        exp = str(expected or "").strip().casefold()
        if act == exp:
            return True
        if not act or not exp:
            return False
        # 期望值是裸数字 → 从实际值开头提取数值前缀做浮点比较
        import re as _re
        if re_search_number := _re.match(r"^[+-]?\d*\.?\d+", exp):
            num_expected = float(re_search_number.group())
            num_actual_match = _re.match(r"^[+-]?\d*\.?\d+", act)
            if num_actual_match:
                num_actual = float(num_actual_match.group())
                if abs(num_actual - num_expected) < 1e-9:
                    return True
        # 实际值以期望值开头，尾随部分必须是单位类文本
        if act.startswith(exp):
            tail = act[len(exp):]
            if not tail:
                return True
            first = tail[0]
            if first in " \t\n\r（(kg.+-":
                return True
        return False
    except Exception:
        return False


def wait_for_flow_control_condition(
    step_id,
    control_id,
    condition="exists",
    timeout_seconds=3,
    window_title_hint="",
    poll_interval_seconds=0.4,
    control_map_path=None,
    expected_value="",
):
    target_condition = str(condition or "exists").strip().lower() or "exists"
    deadline = time.time() + max(0.1, float(timeout_seconds))
    # 值/toggle/可见类校验轮询优先复用已定位的 wrapper：续跑校验紧跟在动作之后、
    # 控件通常不变，每轮重复整树 FindAll（实测 12s+/轮）是"键入后等待"秒级耗时的
    # 主要来源。exists/present 走 find_flow_control 本身即有缓存短路，gone 必须
    # 每轮全新枚举（检测控件消失），两者保持原逻辑。
    control_definition = get_flow_control_definition(step_id, control_id) or {}
    held_control = None
    if target_condition not in {"gone", "exists", "present"} and isinstance(control_definition, dict):
        try:
            held_control = get_cached_flow_control(step_id, control_definition, window_title_hint=window_title_hint)
            if held_control is None and window_title_hint:
                # 动作阶段定位与续跑校验传入的 window_title_hint 可能不一致
                # （缓存键含 hint），补一次空 hint 查找，让校验轮询命中动作
                # 阶段刚写入的缓存，避免第一轮就重复整树 FindAll（实测 12s+）。
                held_control = get_cached_flow_control(step_id, control_definition, window_title_hint="")
        except Exception:
            held_control = None
    while time.time() < deadline:
        control = held_control
        if control is None:
            control = find_flow_control(
                step_id,
                control_id=control_id,
                timeout_seconds=min(max(0.1, float(poll_interval_seconds)), max(0.1, float(timeout_seconds))),
                window_title_hint=window_title_hint,
                control_map_path=control_map_path,
            )
            if control is not None and target_condition not in {"gone", "exists", "present"}:
                held_control = control
        if target_condition in {"exists", "present"}:
            if control is not None:
                return True
        elif target_condition == "visible":
            if control is not None and _safe_get_value(lambda: control.is_visible(), False):
                return True
        elif target_condition == "enabled":
            if control is not None and _safe_get_value(lambda: control.is_enabled(), False):
                return True
        elif target_condition == "gone":
            if control is None:
                return True
        elif target_condition in {"nonempty", "value_equals"}:
            actual_value = _safe_get_value(lambda: get_wrapper_value(control), "")
            if target_condition == "nonempty":
                if actual_value not in (None, ""):
                    return True
            else:  # value_equals
                expected = str(expected_value or "").strip()
                if expected == "":
                    if actual_value not in (None, ""):
                        return True
                elif _values_match_loose(actual_value, expected):
                    return True
        elif target_condition in {"toggle", "checked", "toggle_state"}:
            # 仅对实现 TogglePattern 的控件有意义（CheckBox / ToggleButton /
            # RadioButton / Expander 头 / 分扇区开关等）。非切换控件
            # get_wrapper_toggle_state 返回 ""，永不满足 → 不影响其它流程。
            state = get_wrapper_toggle_state(control)
            exp = str(expected_value or "").strip().lower() or "on"
            on_states = {"1", "on", "1.0"}
            off_states = {"0", "off", "0.0"}
            ind_states = {"2", "indeterminate"}
            if exp in on_states and state in on_states:
                return True
            if exp in off_states and state in off_states:
                return True
            if exp in ind_states and state in ind_states:
                return True
        else:
            raise ValueError(f"不支持的 wait_for_control condition: {condition}")
        # 持有的 wrapper 已失效（控件销毁/重绘）时丢弃，下一轮重新定位；
        # 否则保持持有，避免每轮重复整树 FindAll。
        if held_control is not None:
            try:
                if not is_wrapper_alive(held_control):
                    held_control = None
            except Exception:
                held_control = None
        time.sleep(max(0.1, float(poll_interval_seconds)))
    return False


def _activate_process_main_window(process_name="MUPSmartClient"):
    """按窗口类名关键词找到目标进程主窗口并激活到前台。

    用 win32 EnumWindows + GetClassNameW 毫秒级查找（不依赖 UIA COM），
    找到后恢复最小化并通过 ALT 键击绕过 Windows 前台锁定。
    返回激活的 hwnd，失败返回 0。
    """
    try:
        found = wintypes.HWND(0)
        keyword = str(process_name or "MUPSmartClient").lower()
        _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        @_WNDENUMPROC
        def _enum_callback(hwnd, lparam):
            class_buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetClassNameW(hwnd, class_buf, 256)
            if keyword in class_buf.value.lower():
                ctypes.cast(lparam, ctypes.POINTER(wintypes.HWND))[0] = hwnd
                return False
            return True
        ctypes.windll.user32.EnumWindows(_enum_callback, ctypes.byref(found))
        hwnd = int(found) if found and int(found) else 0
        if not hwnd:
            return 0
        if ctypes.windll.user32.IsIconic(hwnd):
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.4)
        for _ in range(3):
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            time.sleep(0.35)
            if ctypes.windll.user32.GetForegroundWindow() == hwnd:
                break
        time.sleep(0.3)
        return hwnd
    except Exception:
        return 0


def _get_top_level_hwnd_safe(wrapper):
    """安全获取 wrapper 所属顶层窗口句柄。

    优先用 pywinauto 自带的 top_level_parent()（单次调用）；兜底用带深度上限、
    每层异常即退出的 parent() 遍历，避免 UIA COM 调用在后台/最小化窗口上挂起。
    """
    try:
        tl_method = getattr(wrapper, "top_level_parent", None)
        if callable(tl_method):
            try:
                tl = tl_method()
                hwnd = int(getattr(getattr(tl, "element_info", None), "handle", 0) or 0)
                if hwnd:
                    return hwnd
            except Exception:
                pass
    except Exception:
        pass
    try:
        cur = wrapper
        last_hwnd = 0
        for _ in range(12):
            hwnd = int(getattr(getattr(cur, "element_info", None), "handle", 0) or 0)
            if hwnd:
                last_hwnd = hwnd
            try:
                parent = cur.parent()
            except Exception:
                break
            if parent is None:
                break
            cur = parent
        return last_hwnd
    except Exception:
        return 0


def _wrapper_rect_offscreen(wrapper):
    """控件矩形是否位于所在顶层窗口可视区域之外（含 8px 贴边容差）。

    复制链等场景中编辑器滚动位置不同，目标控件可能离屏（实测锚点矩形 y=-901），
    锚点相对点击按离屏坐标点击会"看似成功实则无效果"。
    """
    if wrapper is None:
        return False
    try:
        rect = wrapper.rectangle()
        top = wrapper.top_level_parent()
        if top is None:
            return False
        win = top.rectangle()
        margin = 8
        return bool(
            rect.bottom > win.bottom - margin
            or rect.top < win.top + margin
            or rect.right < win.left + margin
            or rect.left > win.right - margin
        )
    except Exception:
        return False


def click_relative_anchor(
    step_id,
    anchor_control_id,
    offset=(0, 0),
    timeout_seconds=3,
    window_title_hint="",
    click_kind="single",
    control_map_path=None,
    anchor_align="center",
):
    """锚点相对点击：先定位锚点控件(anchor_control_id)，再以其可见矩形某个基准点为原点，
    按像素偏移 offset=(offset_x, offset_y) 点击。

    anchor_align 决定基准点（offset 从该点开始偏移）：
      - "center": 矩形中心（默认，兼容旧行为）
      - "left":   左边缘中点（x = left,  y = 垂直中心）
      - "right":  右边缘中点（x = right, y = 垂直中心）——常用于点控件最右侧的内部图标
      - "top":    上边缘中点（y = top,   x = 水平中心）
      - "bottom": 下边缘中点（y = bottom, x = 水平中心）

    整段逻辑运行在守护线程中并配看门狗：UIA COM 调用在后台/最小化窗口上可能挂起，
    看门狗超时后强制返回失败，避免流程"一直运行无反应"。
    """
    offset_x = int(round(float(offset[0]) if len(offset) > 0 else 0))
    offset_y = int(round(float(offset[1]) if len(offset) > 1 else 0))

    result_box = {}
    done_evt = threading.Event()
    # 看门狗超时共享标志：线程解除挂起后必须检查，超时则跳过点击（防幽灵点击）
    timed_out = {"value": False}

    def _do_click():
        try:
            anchor = find_flow_control(
                step_id,
                control_id=anchor_control_id,
                timeout_seconds=timeout_seconds,
                window_title_hint=window_title_hint,
                control_map_path=control_map_path,
            )
            if anchor is None:
                result_box["reason"] = "anchor_not_found"
                return
            # 双保险窗口激活：先按类名找 MUP 窗口激活（win32，毫秒级），
            # 再从锚点控件获取顶层窗口激活（UIA），互补提高前台命中率。
            _activate_process_main_window("MUPSmartClient")
            hwnd = _get_top_level_hwnd_safe(anchor)
            if hwnd:
                try:
                    if ctypes.windll.user32.IsIconic(hwnd):
                        ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        time.sleep(0.4)
                    for _ in range(3):
                        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
                        ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        time.sleep(0.35)
                        if ctypes.windll.user32.GetForegroundWindow() == hwnd:
                            break
                    time.sleep(0.5)
                except Exception:
                    pass

            # 锚点在可视区域外（如复制链编辑器停在滚动底部而目标控件在顶部，
            # 实测锚点矩形 y=-901 离屏，点击落在屏幕外坐标"看似成功实则无效果"）：
            # 先按 offscreen 判定滚入视口，再取矩形计算点击点。
            if _wrapper_rect_offscreen(anchor):
                _scroll_flow_control_into_view(anchor, step_id=step_id, control_id=anchor_control_id)
            try:
                rect = anchor.rectangle()
            except Exception:
                result_box["reason"] = "anchor_rect_failed"
                return
            if _wrapper_rect_offscreen(anchor):
                # 滚动后仍离屏：显式失败（交由上层重试），不再盲点屏幕外坐标
                result_box["reason"] = "anchor_offscreen_after_scroll"
                _LOG_STEP(
                    "锚点滚动后仍离屏，放弃锚点点击: step={step_id}, anchor={anchor_control_id}, "
                    "rect=({left},{top},{right},{bottom})".format(
                        step_id=step_id,
                        anchor_control_id=anchor_control_id,
                        left=rect.left, top=rect.top, right=rect.right, bottom=rect.bottom,
                    )
                )
                return
            cx = int((rect.left + rect.right) // 2)
            cy = int((rect.top + rect.bottom) // 2)
            # 根据对齐基准计算基准点坐标；anchor_align 非法时回退 center
            align = str(anchor_align or "center").strip().lower()
            if align == "left":
                base_x, base_y = rect.left, cy
            elif align == "right":
                base_x, base_y = rect.right, cy
            elif align == "top":
                base_x, base_y = cx, rect.top
            elif align == "bottom":
                base_x, base_y = cx, rect.bottom
            else:
                base_x, base_y = cx, cy
            px = base_x + offset_x
            py = base_y + offset_y

            kind = str(click_kind or "single").strip().lower()
            if timed_out["value"]:
                # 看门狗已判定超时、线程此刻才解除挂起：必须跳过点击，防"幽灵点击"
                # 落在数秒后用户正在操作的任意位置
                result_box["ok"] = False
                result_box["reason"] = "watchdog_timeout_skip_click"
                _LOG_STEP(
                    f"锚点相对点击看门狗已超时，跳过延迟点击: step={step_id}, anchor={anchor_control_id}"
                )
                return
            if kind == "double":
                pyautogui.doubleClick(px, py)
            else:
                pyautogui.click(px, py)

            _LOG_STEP(
                f"已通过锚点相对点击: anchor={anchor_control_id}, align={align}, "
                f"base=({base_x},{base_y}), center=({cx},{cy}), "
                f"offset=({offset_x},{offset_y}), click=({px},{py}), kind={kind}"
            )
            result_box["ok"] = True
            result_box["meta"] = {
                "anchorControlId": anchor_control_id,
                "anchorAlign": align,
                "anchorCenter": {"x": cx, "y": cy},
                "anchorBase": {"x": base_x, "y": base_y},
                "offset": {"x": offset_x, "y": offset_y},
                "clickPoint": {"x": px, "y": py},
                "clickKind": kind,
            }
        except Exception as exc:
            result_box["error"] = str(exc)
        finally:
            done_evt.set()

    worker = threading.Thread(target=_do_click, daemon=True)
    worker.start()

    watchdog = max(30.0, float(timeout_seconds) + 24.0)
    if not done_evt.wait(watchdog):
        timed_out["value"] = True
        _LOG_STEP(
            f"锚点相对点击超时(看门狗 {watchdog:.0f}s): step={step_id}, anchor={anchor_control_id}"
        )
        return False, {"reason": "timeout", "watchdogSeconds": watchdog}

    if result_box.get("reason") == "anchor_not_found":
        _LOG_STEP(f"锚点相对点击未命中锚点控件: step={step_id}, anchor={anchor_control_id}")
        return False, {"reason": "anchor_not_found", "anchorControlId": anchor_control_id}
    if result_box.get("reason") == "anchor_rect_failed":
        _LOG_STEP(f"锚点相对点击获取矩形失败: step={step_id}, anchor={anchor_control_id}")
        return False, {"reason": "anchor_rect_failed", "anchorControlId": anchor_control_id}
    if result_box.get("reason") == "anchor_offscreen_after_scroll":
        _LOG_STEP(f"锚点相对点击滚动后仍离屏: step={step_id}, anchor={anchor_control_id}")
        return False, {"reason": "anchor_offscreen_after_scroll", "anchorControlId": anchor_control_id}
    if result_box.get("error"):
        _LOG_STEP(f"锚点相对点击异常: step={step_id}, anchor={anchor_control_id}, error={result_box['error']}")
        return False, {"reason": "error", "error": result_box["error"]}

    return bool(result_box.get("ok")), result_box.get("meta", {})


def _is_list_item_wrapper(wrapper):
    """判断控件是否为 ListBoxItem/ListItem（卡片列表项）。"""
    if wrapper is None:
        return False
    try:
        ct = str(get_wrapper_control_type(wrapper) or "").lower()
        cls = str(get_wrapper_class_name(wrapper) or "").lower()
    except Exception:
        return False
    return ct in {"listitem", "list item"} or cls in {"listboxitem"}


def _get_control_expected_name(step_id, control_id):
    """取控件定义里的期望名称（inspectData.name / 顶层 name / targetValue 的 name 段）。

    用于 SelectionItemPattern 选中后的自检：确认所选卡片内部确实包含目标文本，
    防止"定位偏差选中相邻卡片"被静默吞掉。
    """
    step_definition = _GET_STEP_DEFINITION(step_id) or {}
    for control in step_definition.get("controls", []):
        if str(control.get("id", "")).strip() != str(control_id).strip():
            continue
        inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
        name = inspect_data.get("name") or control.get("name")
        if name:
            return str(name)
        methods = [m.strip() for m in split_locator_parts(str(control.get("targetMethod", "")))]
        values = [v.strip() for v in split_locator_parts(str(control.get("targetValue", "")))]
        for method, value in zip(methods, values):
            if method == "name":
                return value
    return ""


def _list_item_inner_texts(wrapper, limit=6):
    """收集 ListItem 内部的主要文本（标题等），用于选中后的证据日志。"""
    texts = []
    if wrapper is None:
        return texts
    try:
        for child in wrapper.descendants(control_type="Text"):
            try:
                t = get_wrapper_text(child)
            except Exception:
                continue
            if t and str(t).strip() and str(t).strip() not in texts:
                texts.append(str(t).strip())
                if len(texts) >= max(int(limit), 1):
                    break
    except Exception:
        pass
    return texts


def _collapse_parent_combo_popup(list_item, max_depth=10, step_id="", control_id=""):
    """选中下拉项后收起父级 ComboBox 的弹出层。

    Telerik 多选分组下拉（MTDGroupComboBoxMultiSelection）勾选后不自动收起
    （单选下拉通常在选中时自关，再 Collapse 是无害空操作）。弹出层不收起会
    继续盖住其下方的控件，后续步骤的物理点击会落在弹出层上被吞掉——现象是
    鼠标已移动到目标位置但没有点击效果（实测 step_7 锚点点击 857px 处正好
    落在风机配置下拉的第 4 行上）。
    """
    if list_item is None:
        return
    try:
        current = list_item
        combo = None
        for _ in range(int(max_depth)):
            parent = current.parent()
            if parent is None:
                break
            current = parent
            if get_wrapper_control_type(parent) == "ComboBox":
                combo = parent
                break
        if combo is None:
            return
        if hasattr(combo, "collapse"):
            combo.collapse()
        else:
            combo.patterns.ExpandCollapse.Collapse()
        _LOG_STEP(f"已收起下拉弹出层: step={step_id}, control={control_id}")
    except Exception as exc:
        _LOG_STEP(
            f"收起下拉弹出层失败(忽略): step={step_id}, control={control_id}, error={exc}"
        )


def _find_list_item_with_text(anchor_item, expected_text, max_items=100):
    """在 anchor ListItem 的同一容器内，寻找卡片文本包含期望文本的 ListItem。

    用于 SelectionItemPattern 选中错项后的文本消歧重选：共享 automationId 的
    下拉选项（Telerik MTDGroupComboBoxMultiSelection）全部同 id 同类型，首次
    命中可能落在第一项（如"默认配置"），需按期望文本在兄弟项中重选。
    """
    if anchor_item is None or not expected_text:
        return None
    try:
        parent = anchor_item.parent()
    except Exception:
        return None
    if parent is None:
        return None
    try:
        candidates = list(parent.descendants(control_type="ListItem"))
    except Exception:
        return None
    anchor_handle = _safe_get_value(lambda: getattr(anchor_item.element_info, "handle", None), None)
    checked = 0
    for candidate in candidates:
        checked += 1
        if checked > int(max_items):
            break
        cand_handle = _safe_get_value(lambda: getattr(candidate.element_info, "handle", None), None)
        if cand_handle and anchor_handle and cand_handle == anchor_handle:
            continue
        try:
            own_text = get_wrapper_text(candidate)
        except Exception:
            own_text = ""
        if own_text and expected_text in own_text:
            return candidate
        if any(expected_text in text for text in _list_item_inner_texts(candidate, limit=6)):
            return candidate
    return None


def _find_list_item_ancestor(wrapper, max_depth=6):
    """从控件向上找最近的 ListBoxItem 祖先（用于卡片内标题文本→卡片 ListItem 选中）。"""
    if wrapper is None:
        return None
    current = wrapper
    for _ in range(max(int(max_depth), 1)):
        if _is_list_item_wrapper(current):
            return current
        try:
            current = current.parent()
        except Exception:
            return None
        if current is None:
            return None
    return None


def click_flow_control(step_id, control_id, timeout_seconds=3, window_title_hint="", click_kind="left", control_map_path=None):
    control = find_flow_control(
        step_id,
        control_id=control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
        control_map_path=control_map_path,
    )
    if control is None:
        if step_id == "step_15":
            search_windows = list(
                iter_flow_search_windows(
                    _GET_STEP_DEFINITION(step_id),
                    window_title_hint=window_title_hint,
                    control_definition=get_flow_control_definition(step_id, control_id),
                )
            )
            open_window = None
            for candidate_window in search_windows:
                if value_matches(get_wrapper_text(candidate_window), "打开"):
                    open_window = candidate_window
                    break
            foreground_missing = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point B:post-type-click-step15-not-found
            _emit_post_type_click_debug_event(
                "B",
                "wt_flow_locator.py:click_flow_control:step_15_not_found",
                "[DEBUG] step_15 open button not found during sequential flow",
                {
                    "stepId": step_id,
                    "controlId": control_id,
                    "windowTitleHint": window_title_hint,
                    "clickKind": click_kind,
                    "foreground": get_wrapper_debug_snapshot(foreground_missing),
                    "searchWindows": [get_wrapper_debug_snapshot(window) for window in search_windows[:4]],
                    "openWindowChildren": get_window_descendant_debug_summary(open_window, limit=24),
                    "controlDefinition": get_flow_control_definition(step_id, control_id),
                },
            )
            # #endregion
        # #region debug-point E:private-group-click-not-found
        _emit_debug_event(
            "E",
            "wt_flow_locator.py:click_flow_control:not-found",
            "[DEBUG] click_flow_control failed to locate control",
            {
                "stepId": step_id,
                "controlId": control_id,
                "windowTitleHint": window_title_hint,
                "clickKind": click_kind,
                "stepWindowTitle": str((_GET_STEP_DEFINITION(step_id) or {}).get("windowTitle", "")).strip(),
                "controlDefinition": get_flow_control_definition(step_id, control_id),
            },
        )
        # #endregion
        return False
    _t_act = time.perf_counter()  # 动作执行阶段计时开始
    foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
    if step_id == "step_15":
        open_window = None
        search_windows = list(
            iter_flow_search_windows(
                _GET_STEP_DEFINITION(step_id),
                window_title_hint=window_title_hint,
                control_definition=get_flow_control_definition(step_id, control_id),
            )
        )
        for candidate_window in search_windows:
            if value_matches(get_wrapper_text(candidate_window), "打开"):
                open_window = candidate_window
                break
        # #region debug-point C:post-type-click-step15-before
        _emit_post_type_click_debug_event(
            "C",
            "wt_flow_locator.py:click_flow_control:step_15_before",
            "[DEBUG] before clicking step_15 open button",
            {
                "stepId": step_id,
                "controlId": control_id,
                "windowTitleHint": window_title_hint,
                "clickKind": click_kind,
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
                "control": get_wrapper_debug_snapshot(control),
                "openWindow": get_wrapper_debug_snapshot(open_window),
                "openWindowChildren": get_window_descendant_debug_summary(open_window, limit=24),
                "controlDefinition": get_flow_control_definition(step_id, control_id),
            },
        )
        # #endregion
    # #region debug-point E:private-group-click-before
    _emit_debug_event(
        "E",
        "wt_flow_locator.py:click_flow_control:before",
        "[DEBUG] before click_flow_control",
        {
            "stepId": step_id,
            "controlId": control_id,
            "windowTitleHint": window_title_hint,
            "clickKind": click_kind,
            "control": get_wrapper_debug_snapshot(control),
            "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
            "controlDefinition": get_flow_control_definition(step_id, control_id),
        },
    )
    # #endregion
    # 滚动容器内离屏控件先滚到可见，避免点击屏幕外坐标落空/点到其它位置
    _scroll_flow_control_into_view(control, step_id=step_id, control_id=control_id)
    try:
        control.set_focus()
    except Exception:
        pass
    _toggle_desired = _toggle_fixup_desired_state(_GET_STEP_DEFINITION(step_id), control_id)
    _toggle_before = str(get_wrapper_toggle_state(control) or "") if _toggle_desired else ""
    click_ok = True
    _selected_via_pattern = False
    _is_list_item = _is_list_item_wrapper(control)
    # 若目标控件位于 ListBoxItem 内（如卡片标题文本），向上找父级 ListItem，
    # 用 SelectionItemPattern 程序化选中（物理点击常只触发悬停不触发选中）。
    _list_item_target = control if _is_list_item else _find_list_item_ancestor(control)
    _LOG_STEP(
        f"[DEBUG] click_flow_control 判定: step={step_id}, control={control_id}, "
        f"is_list_item={_is_list_item}, list_item_target={_list_item_target is not None}, "
        f"control_type={get_wrapper_control_type(control)}, class={get_wrapper_class_name(control)}, "
        f"name={get_wrapper_text(control)!r}, rect={get_wrapper_rectangle(control)}"
    )
    if click_kind in ("left", "single", "") and _list_item_target is not None:
        # ListBoxItem 物理点击常只触发悬停不选中（Telerik/WPF 卡片列表，如综合卡片），
        # 改用 SelectionItemPattern.Select() 程序化选中，可靠且不依赖屏幕坐标/分辨率。
        try:
            if hasattr(_list_item_target, "select"):
                _list_item_target.select()
            else:
                _list_item_target.patterns.SelectionItem.Select()
            _selected_via_pattern = True
            _item_texts = _list_item_inner_texts(_list_item_target, limit=6)
            _LOG_STEP(
                f"ListBoxItem 已通过 SelectionItemPattern 选中: step={step_id}, control={control_id}, "
                f"item_rect={get_wrapper_rectangle(_list_item_target)}, item_texts={_item_texts}"
            )
            _expected_name = normalize_match_text(_get_control_expected_name(step_id, control_id))
            if _expected_name and not any(_expected_name in text for text in _item_texts):
                _LOG_STEP(
                    f"[预警] SelectionItemPattern 选中卡片内未见期望文本 {_expected_name!r}: "
                    f"item_texts={_item_texts}"
                )
                # 控件定义显式要求按文本消歧（targetMethod 含 name/label_text/text）时，
                # 首次命中落错项不可静默吞掉：先在兄弟项中按期望文本重选；重选无果则
                # 中止步骤（错误项已被勾选，继续执行等于把错误选择带进后续步骤）。
                _control_def_here = get_flow_control_definition(step_id, control_id) or {}
                _text_methods = {
                    m.strip() for m in split_locator_parts(str(_control_def_here.get("targetMethod", "")))
                }
                if _item_texts and _text_methods & {"name", "label_text", "text"}:
                    _reselect_ok = False
                    _replacement = _find_list_item_with_text(_list_item_target, _expected_name)
                    if _replacement is not None:
                        try:
                            if hasattr(_replacement, "select"):
                                _replacement.select()
                            else:
                                _replacement.patterns.SelectionItem.Select()
                            _reselect_ok = True
                            _selected_via_pattern = True
                            _LOG_STEP(
                                f"已按期望文本重选命中: step={step_id}, control={control_id}, "
                                f"expected={_expected_name!r}, "
                                f"item_texts={_list_item_inner_texts(_replacement, limit=6)}"
                            )
                        except Exception as _re_exc:
                            _LOG_STEP(
                                f"按期望文本重选失败: step={step_id}, control={control_id}, "
                                f"expected={_expected_name!r}, error={_re_exc}"
                            )
                    if not _reselect_ok:
                        _LOG_STEP(
                            f"[失败] 选中卡片内未见期望文本且重选无果，中止步骤防误选: step={step_id}, "
                            f"control={control_id}, expected={_expected_name!r}, item_texts={_item_texts}"
                        )
                        _finalize_step_timing(step_id, control_id, _t_act)
                        return False
            # Telerik 多选分组下拉程序化选中后不自动收起：弹出层会继续盖住下方
            # 控件并吞掉后续步骤的物理点击（step_7 锚点点击"鼠标到位却无效果"
            # 即落在风机配置弹出的下拉层上），选中完成后收起父级下拉弹出层。
            _collapse_parent_combo_popup(_list_item_target, step_id=step_id, control_id=control_id)
        except Exception as sel_exc:
            _LOG_STEP(
                f"SelectionItemPattern 选中失败，回退物理点击: step={step_id}, control={control_id}, error={sel_exc}"
            )
            _selected_via_pattern = False
    if not _selected_via_pattern:
        try:
            if click_kind == "right":
                control.right_click_input()
            elif click_kind == "double":
                try:
                    control.double_click_input()
                except Exception:
                    control.click_input(double=True)
            else:
                control.click_input()
        except Exception as click_exc:
            # 点击瞬间控件销毁/窗口无响应：改用坐标点击兜底，避免步骤以"崩溃"收场
            click_ok = False
            _LOG_STEP(
                f"控件点击异常，尝试坐标兜底: step={step_id}, control={control_id}, error={click_exc}"
            )
    if not click_ok:
        fallback_ok, fallback_point = click_wrapper_center(control, click_kind=click_kind)
        if not fallback_ok:
            _LOG_STEP(
                f"控件点击失败且坐标兜底不可用: step={step_id}, control={control_id}"
            )
            _finalize_step_timing(step_id, control_id, _t_act)
            return False
        _LOG_STEP(
            f"控件点击异常，坐标兜底成功: step={step_id}, control={control_id}"
        )
    time.sleep(0.12)
    foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
    refined_click = {}
    if not click_ok:
        refined_click = {
            "performed": True,
            "reason": "click-exception-center-fallback",
            "clickPoint": fallback_point or {},
        }
    if should_retry_click_after_focus_switch(control, foreground_before, foreground_after):
        ok, click_point = click_wrapper_center(control, click_kind=click_kind)
        if ok:
            refined_click = {
                "performed": True,
                "reason": "focus-switch-text-like-control",
                "clickPoint": click_point or {},
            }
            time.sleep(0.12)
            foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point E:private-group-click-refined
            _emit_debug_event(
                "E",
                "wt_flow_locator.py:click_flow_control:refined",
                "[DEBUG] performed refined center click after foreground switched",
                {
                    "stepId": step_id,
                    "controlId": control_id,
                    "clickKind": click_kind,
                    "clickPoint": click_point or {},
                    "control": get_wrapper_debug_snapshot(control),
                    "foregroundAfterRefinedClick": get_wrapper_debug_snapshot(foreground_after),
                },
            )
            # #endregion
    if _toggle_desired:
        # click 动作成功仅代表"点击未抛异常"，可能落空（如表头 CheckBox 物理点击
        # 未触发 toggle）。此处点后校验：未达期望且点前状态可读时，程序化 Toggle 收敛。
        _toggle_after = str(get_wrapper_toggle_state(control) or "").lower()
        _on_set = {"1", "on"}
        _off_set = {"0", "off"}
        _desired_met = (_toggle_after in _on_set and _toggle_desired == "on") or (
            _toggle_after in _off_set and _toggle_desired == "off"
        )
        if _desired_met:
            _LOG_STEP(
                "点击后切换态已达标: step={step}, control={control}, state={state}".format(
                    step=step_id, control=control_id, state=_toggle_after
                )
            )
        elif _toggle_before in _on_set | _off_set:
            _reached = reach_wrapper_toggle_state(control, _toggle_desired)
            _final = str(get_wrapper_toggle_state(control) or "")
            _LOG_STEP(
                "点击后切换态未翻转，程序化Toggle兜底: step={step}, control={control}, before={before}, after={after}, desired={desired}, ok={reached}, final={final}".format(
                    step=step_id,
                    control=control_id,
                    before=_toggle_before,
                    after=_toggle_after or "(unreadable)",
                    desired=_toggle_desired,
                    reached=_reached,
                    final=_final or "(unreadable)",
                )
            )
    # #region debug-point E:private-group-click-after
    _emit_debug_event(
        "E",
        "wt_flow_locator.py:click_flow_control:after",
        "[DEBUG] after click_flow_control",
        {
            "stepId": step_id,
            "controlId": control_id,
            "clickKind": click_kind,
            "control": get_wrapper_debug_snapshot(control),
            "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
            "refinedClick": refined_click,
        },
    )
    # #endregion
    if step_id == "step_15":
        # #region debug-point D:post-type-click-step15-after
        _emit_post_type_click_debug_event(
            "D",
            "wt_flow_locator.py:click_flow_control:step_15_after",
            "[DEBUG] after clicking step_15 open button",
            {
                "stepId": step_id,
                "controlId": control_id,
                "windowTitleHint": window_title_hint,
                "clickKind": click_kind,
                "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
                "controlAfter": get_wrapper_debug_snapshot(control),
                "refinedClick": refined_click,
            },
        )
        # #endregion
    _LOG_STEP(f"已通过流程链路匹配点击控件: step={step_id}, control={control_id}")
    _finalize_step_timing(step_id, control_id, _t_act)
    return True


def check_all_unchecked_toggle_controls(
    step_id,
    control_id,
    timeout_seconds=3,
    window_title_hint="",
    control_map_path=None,
    desired="on",
):
    """定位所有匹配的 toggle 控件（如 automationId=CbValid 的风向行 CheckBox），
    逐行读 ToggleState：已勾选跳过；未勾选先物理点击（点后读态校验），未翻转再经
    TogglePattern/LegacyIAccessible 程序化收敛；禁用项跳过不计失败。

    未找到任何匹配时抛 ValueError（与单目标 click 未命中语义一致）；存在勾选失败
    时抛 RuntimeError（携带 counts），避免"点到即成功、实为落空"的假成功。

    返回 {"found", "checked", "already", "failed", "skipped_disabled"}。
    """
    control_definition = get_flow_control_definition(step_id, control_id)
    if not isinstance(control_definition, dict) or not control_definition:
        raise ValueError(
            "check_all_toggles 缺省控件定义: step={step}, control={control}".format(
                step=step_id, control=control_id
            )
        )
    step_definition = _GET_STEP_DEFINITION(step_id) or {}
    windows = list(
        iter_flow_search_windows(
            step_definition,
            window_title_hint=window_title_hint,
            control_definition=control_definition,
        )
    )
    if not windows:
        raise RuntimeError(
            "check_all_toggles 未找到目标窗口: step={step}, control={control}".format(
                step=step_id, control=control_id
            )
        )
    seen = set()
    matches = []
    for window in windows:
        for candidate in iter_fast_locator_candidates(window, control_definition):
            try:
                if not wrapper_matches_control_definition(candidate, control_definition):
                    continue
            except Exception:
                continue
            key = get_wrapper_handle(candidate) or normalize_match_text(
                _safe_get_value(lambda: str(candidate.element_info.runtime_id), "")
            ) or id(candidate)
            if key in seen:
                continue
            seen.add(key)
            matches.append(candidate)
        if not matches:
            # fast 阶段可能因控件被非 Control View 元素包裹而漏检，补一轮整树 Raw View。
            for candidate in iter_raw_view_fallback_candidates(window, control_definition):
                try:
                    if not wrapper_matches_control_definition(candidate, control_definition):
                        continue
                except Exception:
                    continue
                key = get_wrapper_handle(candidate) or normalize_match_text(
                    _safe_get_value(lambda: str(candidate.element_info.runtime_id), "")
                ) or id(candidate)
                if key in seen:
                    continue
                seen.add(key)
                matches.append(candidate)
    if not matches:
        raise ValueError(
            "check_all_toggles 未找到匹配控件: step={step}, control={control}, windows={win}".format(
                step=step_id, control=control_id, win=len(windows)
            )
        )
    on_set = {"1", "on"}
    off_set = {"0", "off"}
    checked = 0
    already = 0
    failed = 0
    skipped_disabled = 0
    total = len(matches)
    for index, wrapper in enumerate(matches):
        try:
            enabled = str(get_wrapper_is_enabled(wrapper) or "").strip().lower() in {"true", "1", ""}
        except Exception:
            enabled = True
        if not enabled:
            skipped_disabled += 1
            continue
        before = str(get_wrapper_toggle_state(wrapper) or "").lower()
        if before in on_set:
            already += 1
            continue
        ok = False
        if before in off_set:
            try:
                wrapper.set_focus()
            except Exception:
                pass
            try:
                wrapper.click_input()
            except Exception:
                try:
                    click_wrapper_center(wrapper, click_kind="left")
                except Exception:
                    pass
            time.sleep(0.12)
            after = str(get_wrapper_toggle_state(wrapper) or "").lower()
            if after in on_set:
                ok = True
            else:
                ok = reach_wrapper_toggle_state(wrapper, desired)
        else:
            ok = reach_wrapper_toggle_state(wrapper, desired)
        if ok:
            checked += 1
            _LOG_STEP(
                "check_all 行勾选成功: step={step}, control={control}, index={idx}/{total}, before={before}".format(
                    step=step_id, control=control_id, idx=index, total=total, before=before or "(unreadable)"
                )
            )
        else:
            failed += 1
            _LOG_STEP(
                "check_all 行勾选失败: step={step}, control={control}, index={idx}/{total}, before={before}".format(
                    step=step_id, control=control_id, idx=index, total=total, before=before or "(unreadable)"
                )
            )
    summary = {
        "found": total,
        "checked": checked,
        "already": already,
        "failed": failed,
        "skipped_disabled": skipped_disabled,
    }
    if failed > 0:
        raise RuntimeError(
            "check_all_toggles 存在未勾选成功的行: step={step}, control={control}, result={result}".format(
                step=step_id, control=control_id, result=summary
            )
        )
    return summary


def click_menu_candidate_by_text(step_id, control_id):
    control_definition = get_flow_control_definition(step_id, control_id)
    if not isinstance(control_definition, dict):
        return False
    target_name = str((control_definition.get("inspectData", {}) or {}).get("name", "")).strip()
    if not target_name:
        methods = split_locator_parts(control_definition.get("targetMethod", ""))
        values = split_locator_parts(control_definition.get("targetValue", ""))
        for method, value in zip(methods, values):
            if method.strip() == "name":
                target_name = str(value).strip()
                break
    if not target_name:
        return False

    for window in iter_flow_search_windows(_GET_STEP_DEFINITION(step_id), window_title_hint="__all__"):
        candidates = [window]
        try:
            candidates.extend(window.descendants())
        except Exception:
            pass
        for candidate in candidates:
            if get_wrapper_control_type(candidate) != "MenuItem":
                continue
            name = get_wrapper_text(candidate)
            if not value_matches(name, target_name):
                continue
            try:
                candidate.click_input()
                time.sleep(0.12)
                _LOG_STEP(f"已通过菜单候选直点控件: step={step_id}, control={control_id}, name={target_name}")
                return True
            except Exception:
                continue
    return False


def _step_config_bool(step_id, key, default=False):
    """读取步骤定义 actionConfig 中的布尔配置（如 preScrollToTop）。"""
    try:
        step_def = _GET_STEP_DEFINITION(step_id) or {}
        action_config = step_def.get("actionConfig", {}) or {}
        value = action_config.get(key)
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}
    except Exception:
        return default


def focus_flow_control(step_id, control_id, timeout_seconds=3, window_title_hint="", control_map_path=None):
    control = find_flow_control(
        step_id,
        control_id=control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
        control_map_path=control_map_path,
    )
    if control is None:
        return False
    _t_act = time.perf_counter()  # 动作执行阶段计时开始
    # 滚动容器内离屏控件先滚到可见，避免 click_input 点击屏幕外坐标落空；
    # preScrollToTop 时强制滚动到容器顶部
    _scroll_flow_control_into_view(
        control,
        step_id=step_id,
        control_id=control_id,
        force_top=_step_config_bool(step_id, "preScrollToTop"),
    )
    try:
        control.click_input()
        time.sleep(0.3)
        _finalize_step_timing(step_id, control_id, _t_act)
        return True
    except Exception:
        try:
            control.set_focus()
            time.sleep(0.2)
            _finalize_step_timing(step_id, control_id, _t_act)
            return True
        except Exception:
            return False


_EDITABLE_CONTROL_TYPES = {"edit", "combobox", "spinner", "document"}


def _is_editable_control_type(control_type):
    return str(control_type or "").strip().lower() in _EDITABLE_CONTROL_TYPES


def _is_editable_wrapper(wrapper):
    if wrapper is None:
        return False
    if _is_editable_control_type(get_wrapper_control_type(wrapper)):
        return True
    return get_wrapper_class_name(wrapper).strip().lower() in {"textbox", "passwordbox"}


def _find_editable_ancestor(wrapper, max_depth=8):
    current = wrapper
    for _ in range(max_depth):
        current = _safe_get_value(lambda: current.parent(), None)
        if current is None:
            return None
        if _is_editable_wrapper(current):
            return current
    return None


def _find_editable_descendant(wrapper, max_depth=4):
    """在 wrapper 后代中广度优先查找第一个可输入控件（Edit/ComboBox 等）。"""
    frontier = _safe_get_value(lambda: wrapper.children(), []) or []
    depth = 0
    while frontier and depth < max_depth:
        next_frontier = []
        for node in frontier:
            if _is_editable_wrapper(node):
                return node
            next_frontier.extend(_safe_get_value(lambda: node.children(), []) or [])
        frontier = next_frontier
        depth += 1
    return None


def _find_editable_sibling(wrapper):
    """当 wrapper 是标签时，在其父容器的兄弟里查找同行、位于右侧且最近的可输入控件。"""
    parent = _safe_get_value(lambda: wrapper.parent(), None)
    if parent is None:
        return None
    base_rect = get_wrapper_rectangle(wrapper)
    if not base_rect:
        return None
    siblings = _safe_get_value(lambda: parent.children(), []) or []
    candidates = []
    for sib in siblings:
        if _is_same_wrapper(sib, wrapper):
            continue
        if not _is_editable_wrapper(sib):
            continue
        sib_rect = get_wrapper_rectangle(sib)
        if not sib_rect:
            continue
        # 垂直方向需有重叠（视为同一行）
        if sib_rect["bottom"] <= base_rect["top"] or sib_rect["top"] >= base_rect["bottom"]:
            continue
        # 允许输入框在标签右侧，或与标签基本对齐（容忍 30px 抖动）
        if sib_rect["left"] < base_rect["left"] - 30:
            continue
        distance = sib_rect["left"] - base_rect["right"]
        candidates.append((abs(distance), sib))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][1]


def _resolve_editable_target(control):
    """将命中的非输入控件（常见为标签 Text）解析到真正可输入的控件。

    先向下钻取后代中的可输入控件，再回退到同行相邻的可输入兄弟；
    找不到时返回 None，由调用方保持原控件不变（不影响既有正常路径）。
    """
    if control is None:
        return None
    if _is_editable_wrapper(control):
        return control
    if get_wrapper_automation_id(control) == "PART_ContentHost":
        ancestor = _find_editable_ancestor(control)
        if ancestor is not None:
            return ancestor
    descendant = _find_editable_descendant(control)
    if descendant is not None:
        return descendant
    return _find_editable_sibling(control)


def _resolve_control_definition_target(control, control_definition):
    normalized = normalize_control_definition(control_definition)
    inspect_data = normalized.get("inspectData", {}) or {}
    automation_id = normalize_match_text(inspect_data.get("automationId", ""))
    expected_type = normalize_control_type_name(
        normalized.get("controlType", ""), inspect_data.get("controlType", "")
    )
    if automation_id == "PART_ContentHost" and expected_type in {"Pane", "Custom", "Edit"}:
        return _resolve_editable_target(control)
    return control


def _type_via_screen_keyboard(control, text):
    """PART_ContentHost 等 Raw View 内部宿主输入兜底：点击聚焦 + 全局键盘键入。

    set_edit_text/type_keys 依赖 wrapper 的 UIA 输入方法，对 Raw View 内部宿主
    （无独立逻辑控件/句柄）可能失效；PART_ContentHost 点击宿主即聚焦所属 TextBox，
    随后用全局 send_keys 键入，不依赖宿主自身的输入方法。

    聚焦用 pywinauto 的 click_input（实测对内部宿主有效）；pyautogui.click
    存在假阳性（日志成功但焦点未落到输入框，文字发到别处）。
    """
    if get_wrapper_center(control) is None:
        return False
    try:
        control.click_input()
        time.sleep(0.25)
        send_keys(text)
        time.sleep(0.1)
        return True
    except Exception:
        return False


def type_text_into_wrapper(control, text, force_top=False):
    text = str(text or "")
    # 命中的若是标签等非输入控件，先尝试解析到真正可输入的邻近控件（执行侧兜底）；
    # 若控件本身已是 Edit/ComboBox 等可输入类型，则保持原行为，不做任何额外遍历。
    if not _is_editable_wrapper(control):
        editable = _resolve_editable_target(control)
        if editable is not None:
            control = editable
        elif get_wrapper_control_type(control) in {"Pane", "Custom"} and get_wrapper_automation_id(control) == "PART_ContentHost":
            # 孤儿 PART_ContentHost（无父级 TextBox 可提升）：点击宿主自身即可
            # 聚焦所属 TextBox，随后键入文本；不在此直接判失败，避免"能输入却
            # 误报失败"。若点击后仍无法键入，由 type_keys 失败路径返回 False。
            pass
    input_method = ""
    # 滚动容器内离屏控件先滚到可见，避免 click_input 点击屏幕外坐标落空；
    # force_top 时无条件滚动到容器顶部（步骤配置 preScrollToTop）
    _scroll_flow_control_into_view(control, force_top=force_top)
    try:
        control.click_input()
        time.sleep(0.2)
    except Exception:
        try:
            control.set_focus()
            time.sleep(0.2)
        except Exception:
            pass
    try:
        control.set_edit_text(text)
        input_method = "set_edit_text"
        # #region debug-point A:time-series-path-input-wrapper-set-edit-text
        _emit_time_series_debug_event(
            "A",
            "wt_flow_locator.py:type_text_into_wrapper:set_edit_text",
            "[DEBUG] type_text_into_wrapper set_edit_text succeeded",
            {
                "inputMethod": input_method,
                "text": text,
                "quotedText": text.startswith('"') and text.endswith('"'),
                "control": get_wrapper_debug_snapshot(control),
                "valueAfterSetEditText": get_wrapper_value_snapshot(control),
            },
        )
        # #endregion
        return True
    except Exception:
        pass
    try:
        control.type_keys("^a", pause=0.02)
        time.sleep(0.1)
    except Exception:
        # PART_ContentHost 等内部宿主：type_keys 可能失效，回退坐标点击 + 键盘键入
        if get_wrapper_automation_id(control) == "PART_ContentHost" and _type_via_screen_keyboard(control, text):
            return True
        # #region debug-point B:time-series-path-input-wrapper-type-keys-select-failed
        _emit_time_series_debug_event(
            "B",
            "wt_flow_locator.py:type_text_into_wrapper:type_keys_select_failed",
            "[DEBUG] type_text_into_wrapper failed before typing",
            {
                "text": text,
                "quotedText": text.startswith('"') and text.endswith('"'),
                "control": get_wrapper_debug_snapshot(control),
                "valueBeforeFailure": get_wrapper_value_snapshot(control),
            },
        )
        # #endregion
        return False
    try:
        control.type_keys(text, with_spaces=True, pause=0.02)
        input_method = "type_keys"
        # #region debug-point C:time-series-path-input-wrapper-type-keys
        _emit_time_series_debug_event(
            "C",
            "wt_flow_locator.py:type_text_into_wrapper:type_keys",
            "[DEBUG] type_text_into_wrapper type_keys succeeded",
            {
                "inputMethod": input_method,
                "text": text,
                "quotedText": text.startswith('"') and text.endswith('"'),
                "control": get_wrapper_debug_snapshot(control),
                "valueAfterTypeKeys": get_wrapper_value_snapshot(control),
            },
        )
        # #endregion
        return True
    except Exception:
        if get_wrapper_automation_id(control) == "PART_ContentHost" and _type_via_screen_keyboard(control, text):
            return True
        # #region debug-point D:time-series-path-input-wrapper-type-keys-failed
        _emit_time_series_debug_event(
            "D",
            "wt_flow_locator.py:type_text_into_wrapper:type_keys_failed",
            "[DEBUG] type_text_into_wrapper type_keys failed",
            {
                "text": text,
                "quotedText": text.startswith('"') and text.endswith('"'),
                "control": get_wrapper_debug_snapshot(control),
                "valueAfterTypeKeysFailure": get_wrapper_value_snapshot(control),
            },
        )
        # #endregion
        return False


def type_text_into_flow_control(step_id, control_id, text, timeout_seconds=3, window_title_hint="", control_map_path=None):
    control = find_flow_control(
        step_id,
        control_id=control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
        control_map_path=control_map_path,
    )
    if control is None:
        if step_id in {"step_39", "step_46"}:
            foreground_missing = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point H:step37-add-data-step39-not-found
            _emit_step37_add_data_miss_debug_event(
                "H",
                "wt_flow_locator.py:type_text_into_flow_control:not_found",
                "[DEBUG] type_text_into_flow_control target not found for step_39/46 family",
                {
                    "stepId": step_id,
                    "controlId": control_id,
                    "windowTitleHint": window_title_hint,
                    "text": str(text or ""),
                    "foreground": get_wrapper_debug_snapshot(foreground_missing),
                    "controlDefinition": get_flow_control_definition(step_id, control_id),
                },
            )
            # #endregion
        if step_id == "step_14" or control_id == "step_14_control_1":
            search_windows = list(
                iter_flow_search_windows(
                    _GET_STEP_DEFINITION(step_id),
                    window_title_hint=window_title_hint,
                    control_definition=get_flow_control_definition(step_id, control_id),
                )
            )
            open_window = None
            for candidate_window in search_windows:
                if value_matches(get_wrapper_text(candidate_window), "打开"):
                    open_window = candidate_window
                    break
            foreground_missing = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point A:post-type-click-step14-not-found
            _emit_post_type_click_debug_event(
                "A",
                "wt_flow_locator.py:type_text_into_flow_control:step_14_not_found",
                "[DEBUG] step_14 path edit not found during sequential flow",
                {
                    "stepId": step_id,
                    "controlId": control_id,
                    "windowTitleHint": window_title_hint,
                    "text": str(text or ""),
                    "foreground": get_wrapper_debug_snapshot(foreground_missing),
                    "searchWindows": [get_wrapper_debug_snapshot(window) for window in search_windows[:4]],
                    "openWindowChildren": get_window_descendant_debug_summary(open_window, limit=24),
                    "controlDefinition": get_flow_control_definition(step_id, control_id),
                },
            )
            # #endregion
        if step_id == "step_14" or control_id == "step_14_control_1":
            # #region debug-point E:time-series-path-input-not-found
            _emit_time_series_debug_event(
                "E",
                "wt_flow_locator.py:type_text_into_flow_control:not_found",
                "[DEBUG] target control not found for type_text_into_flow_control",
                {
                    "stepId": step_id,
                    "controlId": control_id,
                    "windowTitleHint": window_title_hint,
                    "text": str(text or ""),
                },
            )
            # #endregion
        return False
    _t_act = time.perf_counter()  # 动作执行阶段计时开始
    _LOG_STEP(
        f"[DEBUG] type_text_into_flow_control 判定: step={step_id}, control={control_id}, "
        f"control_type={get_wrapper_control_type(control)}, class={get_wrapper_class_name(control)}, "
        f"name={get_wrapper_text(control)!r}, rect={get_wrapper_rectangle(control)}"
    )
    if step_id == "step_14" or control_id == "step_14_control_1":
        foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point F:time-series-path-input-before
        _emit_time_series_debug_event(
            "F",
            "wt_flow_locator.py:type_text_into_flow_control:before",
            "[DEBUG] before type_text_into_flow_control",
            {
                "stepId": step_id,
                "controlId": control_id,
                "windowTitleHint": window_title_hint,
                "text": str(text or ""),
                "quotedText": str(text or "").startswith('"') and str(text or "").endswith('"'),
                "control": get_wrapper_debug_snapshot(control),
                "controlValueBefore": get_wrapper_value_snapshot(control),
                "foregroundBefore": get_wrapper_debug_snapshot(foreground_before),
                "controlDefinition": get_flow_control_definition(step_id, control_id),
            },
        )
        # #endregion
    _pre_scroll_force_top = _step_config_bool(step_id, "preScrollToTop")
    if not type_text_into_wrapper(control, text, force_top=_pre_scroll_force_top):
        if step_id == "step_14" or control_id == "step_14_control_1":
            foreground_after_failure = _try_get_window_by_handle(get_foreground_window_handle())
            # #region debug-point G:time-series-path-input-write-failed
            _emit_time_series_debug_event(
                "G",
                "wt_flow_locator.py:type_text_into_flow_control:write_failed",
                "[DEBUG] type_text_into_flow_control write failed",
                {
                    "stepId": step_id,
                    "controlId": control_id,
                    "text": str(text or ""),
                    "control": get_wrapper_debug_snapshot(control),
                    "controlValueAfterFailure": get_wrapper_value_snapshot(control),
                    "foregroundAfterFailure": get_wrapper_debug_snapshot(foreground_after_failure),
                },
            )
            # #endregion
        return False
    time.sleep(0.3)
    if step_id == "step_14" or control_id == "step_14_control_1":
        foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
        # #region debug-point H:time-series-path-input-after
        _emit_time_series_debug_event(
            "H",
            "wt_flow_locator.py:type_text_into_flow_control:after",
            "[DEBUG] after type_text_into_flow_control",
            {
                "stepId": step_id,
                "controlId": control_id,
                "text": str(text or ""),
                "quotedText": str(text or "").startswith('"') and str(text or "").endswith('"'),
                "control": get_wrapper_debug_snapshot(control),
                "controlValueAfter": get_wrapper_value_snapshot(control),
                "foregroundAfter": get_wrapper_debug_snapshot(foreground_after),
            },
        )
        # #endregion
    _LOG_STEP(f"已通过流程链路匹配输入文本: step={step_id}, control={control_id}, text={text}")
    _finalize_step_timing(step_id, control_id, _t_act)
    return True


def get_wrapper_center(control):
    try:
        rect = control.rectangle()
        return int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2)
    except Exception:
        return None


def drag_between_flow_controls(
    step_id,
    source_control_id,
    target_control_id,
    timeout_seconds=3,
    window_title_hint="",
    duration_seconds=0.4,
    control_map_path=None,
):
    source = find_flow_control(
        step_id,
        control_id=source_control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
        control_map_path=control_map_path,
    )
    target = find_flow_control(
        step_id,
        control_id=target_control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
        control_map_path=control_map_path,
    )
    if source is None or target is None:
        return False
    source_center = get_wrapper_center(source)
    target_center = get_wrapper_center(target)
    if not source_center or not target_center:
        return False
    try:
        pyautogui.moveTo(source_center[0], source_center[1], duration=0.15)
        pyautogui.dragTo(
            target_center[0],
            target_center[1],
            duration=max(0.05, float(duration_seconds)),
            button="left",
        )
    except Exception:
        return False
    time.sleep(0.4)
    _LOG_STEP(
        f"已通过流程链路匹配拖拽控件: step={step_id}, source={source_control_id}, target={target_control_id}"
    )
    return True


def mouse_wheel_on_flow_control(
    step_id,
    control_id="",
    delta=0,
    timeout_seconds=3,
    window_title_hint="",
    control_map_path=None,
):
    if control_id:
        control = find_flow_control(
            step_id,
            control_id=control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            control_map_path=control_map_path,
        )
        if control is None:
            return False
        center = get_wrapper_center(control)
        if center:
            try:
                pyautogui.moveTo(center[0], center[1], duration=0.1)
            except Exception:
                pass
        try:
            control.set_focus()
        except Exception:
            pass
    try:
        pyautogui.scroll(int(float(delta)))
    except Exception:
        return False
    _LOG_STEP(f"已执行滚轮动作: step={step_id}, control={control_id}, delta={delta}")
    return True
