# encoding: utf-8

import ctypes
from ctypes import wintypes
import json
import os
import re
import time

import pyautogui
from pywinauto import Desktop
from pywinauto_recorder.player import send_keys


FLOW_WINDOW_CACHE_TTL_SECONDS = 2.0
FLOW_CONTROL_CACHE_TTL_SECONDS = 12.0
FLOW_PARENT_CACHE_TTL_SECONDS = 20.0

FLOW_WINDOW_CACHE = {}
FLOW_CONTROL_CACHE = {}
FLOW_PARENT_CACHE = {}

_GET_STEP_DEFINITION = lambda step_id: {}
_LOG_STEP = lambda message: None


def _emit_debug_event(hypothesis_id, location, msg, data=None):
    try:
        debug_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".dbg",
            "trae-debug-log-private-group-click.ndjson",
        )
        os.makedirs(os.path.dirname(debug_file), exist_ok=True)
        with open(debug_file, "a", encoding="utf-8") as file_obj:
            file_obj.write(
                json.dumps(
                    {
                        "sessionId": "private-group-click",
                        "runId": "post-fix-v3",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": msg,
                        "data": data or {},
                        "ts": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def _emit_time_series_debug_event(hypothesis_id, location, msg, data=None):
    try:
        debug_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".dbg",
            "trae-debug-log-time-series-path-input.ndjson",
        )
        os.makedirs(os.path.dirname(debug_file), exist_ok=True)
        with open(debug_file, "a", encoding="utf-8") as file_obj:
            file_obj.write(
                json.dumps(
                    {
                        "sessionId": "time-series-path-input",
                        "runId": "pre-fix",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": msg,
                        "data": data or {},
                        "ts": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def _emit_post_type_click_debug_event(hypothesis_id, location, msg, data=None):
    try:
        debug_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".dbg",
            "trae-debug-log-post-type-click-failure.ndjson",
        )
        os.makedirs(os.path.dirname(debug_file), exist_ok=True)
        with open(debug_file, "a", encoding="utf-8") as file_obj:
            file_obj.write(
                json.dumps(
                    {
                        "sessionId": "post-type-click-failure",
                        "runId": "pre-fix",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": msg,
                        "data": data or {},
                        "ts": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def _emit_default_height_debug_event(hypothesis_id, location, msg, data=None):
    try:
        debug_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".dbg",
            "trae-debug-log-default-height-relative-input.ndjson",
        )
        os.makedirs(os.path.dirname(debug_file), exist_ok=True)
        with open(debug_file, "a", encoding="utf-8") as file_obj:
            file_obj.write(
                json.dumps(
                    {
                        "sessionId": "default-height-relative-input",
                        "runId": "pre-fix",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": msg,
                        "data": data or {},
                        "ts": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def _emit_add_data_false_hit_debug_event(hypothesis_id, location, msg, data=None):
    try:
        debug_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".dbg",
            "trae-debug-log-add-data-false-hit.ndjson",
        )
        os.makedirs(os.path.dirname(debug_file), exist_ok=True)
        with open(debug_file, "a", encoding="utf-8") as file_obj:
            file_obj.write(
                json.dumps(
                    {
                        "sessionId": "add-data-false-hit",
                        "runId": "pre-fix",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": msg,
                        "data": data or {},
                        "ts": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def _emit_start_validation_regression_debug_event(hypothesis_id, location, msg, data=None):
    try:
        debug_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".dbg",
            "trae-debug-log-start-validation-regression.ndjson",
        )
        os.makedirs(os.path.dirname(debug_file), exist_ok=True)
        with open(debug_file, "a", encoding="utf-8") as file_obj:
            file_obj.write(
                json.dumps(
                    {
                        "sessionId": "start-validation-regression",
                        "runId": "pre-fix",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": msg,
                        "data": data or {},
                        "ts": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def _emit_relative_region_rect_trace(step_id, location, msg, data=None):
    step_id = str(step_id or "").strip()
    if step_id == "step_16":
        _emit_default_height_debug_event("RECT", location, msg, data)
    elif step_id == "step_26":
        _emit_start_validation_regression_debug_event("RECT", location, msg, data)


def _emit_step37_add_data_miss_debug_event(hypothesis_id, location, msg, data=None):
    try:
        debug_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            ".dbg",
            "trae-debug-log-step37-add-data-miss.ndjson",
        )
        os.makedirs(os.path.dirname(debug_file), exist_ok=True)
        with open(debug_file, "a", encoding="utf-8") as file_obj:
            file_obj.write(
                json.dumps(
                    {
                        "sessionId": "step37-add-data-miss",
                        "runId": "pre-fix",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "msg": msg,
                        "data": data or {},
                        "ts": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except Exception:
        pass


def configure_flow_locator(get_step_definition=None, log_step=None):
    global _GET_STEP_DEFINITION, _LOG_STEP
    if callable(get_step_definition):
        _GET_STEP_DEFINITION = get_step_definition
    if callable(log_step):
        _LOG_STEP = log_step


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
        or class_name in {"ListBoxItem", "MenuItem", "RadListBoxItem"}
    )


def score_dropdown_runtime_candidate(
    wrapper,
    target_texts,
    expected_window_titles=None,
    expected_process_id="",
):
    if wrapper is None or not is_dropdown_like_wrapper(wrapper):
        return -1
    if not _safe_get_value(lambda: wrapper.is_visible(), False):
        return -1
    if not _safe_get_value(lambda: wrapper.is_enabled(), False):
        return -1

    runtime_tokens = [item.lower() for item in get_wrapper_runtime_text_candidates(wrapper)]
    if not runtime_tokens:
        return -1

    score = 0
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
        matched_window_title = False
        for title in expected_window_titles:
            normalized_title = normalize_match_text(title).lower()
            if not normalized_title:
                continue
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


def iter_dropdown_runtime_candidates():
    windows = []
    try:
        windows = Desktop(backend="uia").windows()
    except Exception:
        windows = []
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
    return False, {}


def select_dropdown_item_runtime(step_id, control_id, timeout_seconds=3, window_title_hint=""):
    step_definition = _GET_STEP_DEFINITION(step_id)
    control_definition = get_flow_control_definition(step_id, control_id)
    if not control_definition:
        return False, {}

    foreground_before = _try_get_window_by_handle(get_foreground_window_handle())
    expected_process_id = get_wrapper_process_id(foreground_before)
    target_texts = get_dropdown_runtime_target_texts(control_definition)
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

    last_ranked_candidates = []
    while time.time() < deadline:
        ranked_candidates = []
        for candidate in iter_dropdown_runtime_candidates():
            score = score_dropdown_runtime_candidate(
                candidate,
                target_texts,
                expected_window_titles=expected_window_titles,
                expected_process_id=expected_process_id,
            )
            if score < 0:
                continue
            ranked_candidates.append((score, candidate))
        ranked_candidates.sort(key=lambda item: item[0], reverse=True)
        last_ranked_candidates = ranked_candidates[:5]
        if ranked_candidates and ranked_candidates[0][0] >= 70:
            best_score, best_candidate = ranked_candidates[0]
            best_candidate_snapshot = get_wrapper_debug_snapshot(best_candidate)
            clicked, click_meta = click_dropdown_runtime_candidate(best_candidate)
            if clicked:
                time.sleep(0.12)
                _LOG_STEP(
                    "已通过运行时下拉候选点击控件: step={step_id}, control={control_id}, score={score}, texts={texts}".format(
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
                }
        time.sleep(0.15)
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
            "运行时下拉项未命中且未枚举到候选项: step={step_id}, control={control_id}, targets={targets}, expectedTitles={titles}, foreground={foreground}".format(
                step_id=step_id,
                control_id=control_id,
                targets=" / ".join(target_texts) or "(empty)",
                titles=" / ".join(expected_window_titles) or "(empty)",
                foreground=get_wrapper_text(foreground_before) or "(empty)",
            )
        )
    return False, {"targetTexts": target_texts}


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
    for method, expected in zip(methods, values):
        method = method.strip()
        if method == "automation_id":
            if not value_matches(get_wrapper_automation_id(wrapper), expected):
                return False
        elif method == "name":
            if not value_matches(get_wrapper_text(wrapper), expected):
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
        elif method == "process_id":
            if not value_matches(get_wrapper_process_id(wrapper), expected):
                return False
        elif method == "regex":
            if not value_matches(get_wrapper_text(wrapper), expected, regex=True):
                return False
        elif method == "template":
            return False
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

    if not automation_id:
        add_candidate("name,control_type", [name, control_type])
        add_candidate("name", [name])

    if not automation_id and not name and allow_class_name_fallback:
        add_candidate("class_name,control_type", [class_name, control_type])
        add_candidate("class_name", [class_name])
        add_candidate("framework_id,class_name,control_type", [framework_id, class_name, control_type])
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
            base_score = max(base_score, 120 - priority * 10)
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
        and actual_class_name in {"Window", "HwndWrapper[MUPSmartClient.exe;;916f6a43-19df-48d6-85bf-f0e5771b59b6]"}
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
    if expected_window_title:
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
    for value in [inspect_data.get("processId", ""), inspect_data.get("process_id", "")]:
        candidate = normalize_match_text(value)
        if candidate and candidate not in process_candidates:
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
        return Desktop(backend="uia").window(handle=int(handle))
    except Exception:
        return None


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
    return is_text_like_wrapper(control) or get_wrapper_is_keyboard_focusable(control) == "False"


def make_flow_window_cache_key(title_candidates, process_candidates):
    normalized_titles = tuple(sorted(item for item in (title_candidates or []) if item))
    normalized_processes = tuple(sorted(item for item in (process_candidates or []) if item))
    return normalized_titles, normalized_processes


def is_wrapper_alive(wrapper):
    if wrapper is None:
        return False
    try:
        handle = _safe_get_value(lambda: getattr(wrapper.element_info, "handle", 0), 0)
        if handle:
            return True
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
    FLOW_WINDOW_CACHE[cache_key] = {"timestamp": time.time(), "windows": valid_windows}


def make_flow_control_cache_key(step_id, control_definition, window_title_hint=""):
    control_definition = control_definition if isinstance(control_definition, dict) else {}
    return (
        str(step_id or "").strip(),
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
    if locator_map.get("automation_id"):
        query_fields.extend(
            [
                ("automation_id", "control_type"),
                ("automation_id",),
            ]
        )
    elif locator_map.get("name"):
        query_fields.extend(
            [
                ("name", "control_type"),
                ("name",),
            ]
        )
    elif locator_map.get("class_name"):
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
    for query in build_fast_locator_queries(control_definition):
        kwargs = {}
        if query.get("automation_id"):
            kwargs["auto_id"] = query["automation_id"]
        if query.get("name"):
            kwargs["title"] = query["name"]
        if query.get("class_name"):
            kwargs["class_name"] = query["class_name"]
        if query.get("control_type"):
            kwargs["control_type"] = query["control_type"]
        if query.get("framework_id"):
            kwargs["framework_id"] = query["framework_id"]
        if not kwargs:
            continue
        for root in unique_roots:
            candidates = []
            try:
                candidates.extend(root.children(**kwargs))
            except Exception:
                pass
            if root is window or not candidates:
                try:
                    candidates.extend(root.descendants(**kwargs))
                except Exception:
                    pass
            for candidate in candidates:
                handle = _safe_get_value(lambda: getattr(candidate.element_info, "handle", None), None)
                handle_key = handle if handle not in (None, 0, "") else id(candidate)
                if handle_key in seen_handles:
                    continue
                seen_handles.add(handle_key)
                result.append(candidate)
    return result


def iter_flow_search_windows(step_definition, window_title_hint="", control_definition=None):
    title_candidates = []
    control_window_title = ""
    if isinstance(control_definition, dict):
        control_window_title = str(control_definition.get("windowTitle", "")).strip()
    if control_window_title in {"*", "__all__", "__ALL__"}:
        control_window_title = ""
        step_window_title = ""
    else:
        step_window_title = step_definition.get("windowTitle", "") if isinstance(step_definition, dict) else ""
    for text in [window_title_hint, control_window_title, step_window_title]:
        for item in parse_window_title_candidates(text):
            if item not in title_candidates:
                title_candidates.append(item)

    process_candidates = get_control_process_candidates(control_definition)
    cache_key = make_flow_window_cache_key(title_candidates, process_candidates)
    use_window_cache = not title_candidates
    if use_window_cache:
        cached_windows = get_cached_flow_windows(cache_key)
        if cached_windows:
            return cached_windows
    ranked_windows = []
    desktop = Desktop(backend="uia")
    all_windows = desktop.windows()
    foreground_handle = get_foreground_window_handle()
    for window in all_windows:
        if is_automation_window(window):
            continue
        title = get_wrapper_text(window)
        process_id = get_wrapper_process_id(window)
        handle = _safe_get_value(lambda: getattr(window.element_info, "handle", 0), 0)
        matched_title = any(candidate in title for candidate in title_candidates) if title_candidates else False
        matched_process = process_id in process_candidates if process_candidates else False
        score = 0
        if foreground_handle and handle == foreground_handle:
            score += 20
        if matched_process:
            score += 12
        if matched_title:
            # When a step/control explicitly provides a target title, title match must outrank same-process fallbacks.
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
        if title_candidates:
            return []
        for window in all_windows:
            if is_automation_window(window):
                continue
            score = 0
            handle = _safe_get_value(lambda: getattr(window.element_info, "handle", 0), 0)
            process_id = get_wrapper_process_id(window)
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


def resolve_relative_region_absolute_rect(window, relative_region, window_rect=None):
    window_rect = window_rect or get_wrapper_rectangle(window)
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


def click_relative_region(step_definition, parent_window, relative_region, timeout_seconds=3, window_title_hint="", click_kind="single"):
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
    try:
        time.sleep(0.15)
        send_keys("^a")
        time.sleep(0.05)
        send_keys("{BACKSPACE}")
        time.sleep(0.05)
        send_keys(str(text or ""))
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


def find_flow_control(step_id, control_id=None, timeout_seconds=3, window_title_hint=""):
    step_definition = _GET_STEP_DEFINITION(step_id)
    controls = step_definition.get("controls", []) if isinstance(step_definition, dict) else []
    if control_id:
        controls = [control for control in controls if str(control.get("id", "")).strip() == control_id]
    if not controls:
        return None

    deadline = time.time() + timeout_seconds
    last_error = None
    search_started = time.time()
    while time.time() < deadline:
        try:
            best_match = None
            best_score = -1
            windows = []
            for control_definition in controls:
                cached_wrapper = get_cached_flow_control(step_id, control_definition, window_title_hint=window_title_hint)
                if cached_wrapper is not None:
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
                )
                if not windows and foreground_wrapper is not None:
                    foreground_title = normalize_match_text(get_wrapper_text(foreground_wrapper))
                    if expected_window_title and value_matches(foreground_title, expected_window_title):
                        windows = [foreground_wrapper]
                for window in windows:
                    for candidate in iter_fast_locator_candidates(window, control_definition):
                        if not wrapper_matches_control_definition(candidate, control_definition):
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
                                f"seconds={elapsed:.2f}, score={best_score}, phase=fast"
                            )
                        return best_match
                for window in windows:
                    candidates = [window]
                    try:
                        candidates.extend(window.descendants())
                    except Exception:
                        pass
                    for candidate in candidates:
                        if not wrapper_matches_control_definition(candidate, control_definition):
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
                        return best_match
            if best_match is not None:
                for control_definition in controls:
                    cache_flow_control(step_id, control_definition, best_match, window_title_hint=window_title_hint)
                elapsed = time.time() - search_started
                if elapsed >= 0.8:
                    _LOG_STEP(
                        f"流程控件定位耗时较长: step={step_id}, control={control_id or '(first)'}, "
                        f"seconds={elapsed:.2f}, score={best_score}"
                    )
                return best_match
            last_error = RuntimeError(
                f"step={step_id}, control={control_id or '(first)'}, windows={len(windows)} 未找到匹配控件"
            )
        except Exception as exc:
            last_error = exc
        time.sleep(0.15)
    elapsed = time.time() - search_started
    if last_error is None:
        _LOG_STEP(
            f"流程控件定位失败: step={step_id}, control={control_id or '(first)'}, "
            f"seconds={elapsed:.2f}, reason=timeout_no_match"
        )
    else:
        _LOG_STEP(
            f"流程控件定位失败: step={step_id}, control={control_id or '(first)'}, "
            f"seconds={elapsed:.2f}, last_error={last_error}"
        )
    return None


def wait_for_flow_control_condition(
    step_id,
    control_id,
    condition="exists",
    timeout_seconds=3,
    window_title_hint="",
    poll_interval_seconds=0.4,
):
    target_condition = str(condition or "exists").strip().lower() or "exists"
    deadline = time.time() + max(0.1, float(timeout_seconds))
    while time.time() < deadline:
        control = find_flow_control(
            step_id,
            control_id=control_id,
            timeout_seconds=min(max(0.1, float(poll_interval_seconds)), max(0.1, float(timeout_seconds))),
            window_title_hint=window_title_hint,
        )
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
        else:
            raise ValueError(f"不支持的 wait_for_control condition: {condition}")
        time.sleep(max(0.1, float(poll_interval_seconds)))
    return False


def click_flow_control(step_id, control_id, timeout_seconds=3, window_title_hint="", click_kind="left"):
    control = find_flow_control(
        step_id,
        control_id=control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
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
    try:
        control.set_focus()
    except Exception:
        pass
    if click_kind == "right":
        control.right_click_input()
    elif click_kind == "double":
        try:
            control.double_click_input()
        except Exception:
            control.click_input(double=True)
    else:
        control.click_input()
    time.sleep(0.12)
    foreground_after = _try_get_window_by_handle(get_foreground_window_handle())
    refined_click = {}
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
    return True


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


def focus_flow_control(step_id, control_id, timeout_seconds=3, window_title_hint=""):
    control = find_flow_control(
        step_id,
        control_id=control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
    )
    if control is None:
        return False
    try:
        control.click_input()
        time.sleep(0.3)
        return True
    except Exception:
        try:
            control.set_focus()
            time.sleep(0.2)
            return True
        except Exception:
            return False


def type_text_into_wrapper(control, text):
    text = str(text or "")
    input_method = ""
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


def type_text_into_flow_control(step_id, control_id, text, timeout_seconds=3, window_title_hint=""):
    control = find_flow_control(
        step_id,
        control_id=control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
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
    if not type_text_into_wrapper(control, text):
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
):
    source = find_flow_control(
        step_id,
        control_id=source_control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
    )
    target = find_flow_control(
        step_id,
        control_id=target_control_id,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
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
):
    if control_id:
        control = find_flow_control(
            step_id,
            control_id=control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
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
