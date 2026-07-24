# encoding: utf-8

import json
import os
import time
from datetime import datetime

import pyautogui
from pywinauto_recorder.player import send_keys

from wt_action_schema import step_policy_on_fail_to_legacy


_GET_STEP_DEFINITION = lambda step_id: {}
_GET_FLOW_PACKAGE = lambda package_id: {}
_GET_STEP_PARAMS = lambda step_id: {}
_RESOLVE_DYNAMIC_VALUE = lambda value, step_id, context: value
_LOG_STEP = lambda message: None
_CLICK_FLOW_CONTROL = lambda *args, **kwargs: False
_CLICK_RELATIVE_REGION = lambda *args, **kwargs: (False, {})
_CLICK_RELATIVE_ANCHOR = lambda *args, **kwargs: (False, {})
_FOCUS_FLOW_CONTROL = lambda *args, **kwargs: False
_TYPE_TEXT_INTO_FLOW_CONTROL = lambda *args, **kwargs: False
_TYPE_TEXT_INTO_RELATIVE_REGION = lambda *args, **kwargs: (False, {})
_SELECT_DROPDOWN_ITEM_RUNTIME = lambda *args, **kwargs: (False, {})
_DRAG_BETWEEN_FLOW_CONTROLS = lambda *args, **kwargs: False
_MOUSE_WHEEL_ON_FLOW_CONTROL = lambda *args, **kwargs: False
_WAIT_FOR_FLOW_CONTROL_CONDITION = lambda *args, **kwargs: False
_MENU_SELECT_FLOW = lambda *args, **kwargs: False
_LOCATE_TEMPLATE_CENTER_BY_PATH = lambda *args, **kwargs: None
_REPORT_STEP_RESULT = lambda *args, **kwargs: None
_RUN_AI_INTERVENTION_AFTER_FAILURE = lambda *args, **kwargs: None


def _write_feedback_to_flow(context, step_id, feedback_data):
    """运行时反馈闭环：将步骤执行反馈回写到 flow_definition.json。

    在 context["flowDefinitionPath"] 存在时，向 flow_definition 的
    feedbackHistory 数组追加一条反馈记录。用于积累多次运行的稳定性数据。
    """
    flow_path = context.get("flowDefinitionPath", "")
    if not flow_path or not os.path.isfile(flow_path):
        return
    try:
        with open(flow_path, "r", encoding="utf-8") as f:
            flow_def = json.load(f)
    except Exception as e:
        _LOG_STEP(context, f"Warning: Failed to load flow definition for feedback: {e}", step_id)
        return

    if not isinstance(flow_def, dict):
        return

    entry = {
        "stepId": step_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "runId": context.get("runId", ""),
    }
    entry.update(feedback_data)

    history = flow_def.get("feedbackHistory")
    if not isinstance(history, list):
        flow_def["feedbackHistory"] = [entry]
    else:
        history.append(entry)
        # 限制历史条目数，防止文件过大
        if len(history) > 500:
            flow_def["feedbackHistory"] = history[-500:]

    try:
        with open(flow_path, "w", encoding="utf-8") as f:
            json.dump(flow_def, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _LOG_STEP(context, f"Warning: Failed to save flow definition for feedback: {e}", step_id)


def configure_flow_executor(
    get_step_definition=None,
    get_flow_package=None,
    get_step_params=None,
    resolve_dynamic_value=None,
    log_step=None,
    click_flow_control=None,
    click_relative_region=None,
    click_relative_anchor=None,
    focus_flow_control=None,
    type_text_into_flow_control=None,
    type_text_into_relative_region=None,
    select_dropdown_item_runtime=None,
    drag_between_flow_controls=None,
    mouse_wheel_on_flow_control=None,
    wait_for_flow_control_condition=None,
    menu_select_flow=None,
    locate_template_center_by_path=None,
    report_step_result=None,
    run_ai_intervention_after_failure=None,
):
    global _GET_STEP_DEFINITION, _GET_FLOW_PACKAGE, _GET_STEP_PARAMS
    global _RESOLVE_DYNAMIC_VALUE, _LOG_STEP, _CLICK_FLOW_CONTROL, _CLICK_RELATIVE_REGION
    global _CLICK_RELATIVE_ANCHOR
    global _FOCUS_FLOW_CONTROL, _TYPE_TEXT_INTO_FLOW_CONTROL, _TYPE_TEXT_INTO_RELATIVE_REGION
    global _SELECT_DROPDOWN_ITEM_RUNTIME
    global _DRAG_BETWEEN_FLOW_CONTROLS, _MOUSE_WHEEL_ON_FLOW_CONTROL
    global _WAIT_FOR_FLOW_CONTROL_CONDITION, _MENU_SELECT_FLOW
    global _LOCATE_TEMPLATE_CENTER_BY_PATH, _REPORT_STEP_RESULT
    global _RUN_AI_INTERVENTION_AFTER_FAILURE

    if callable(get_step_definition):
        _GET_STEP_DEFINITION = get_step_definition
    if callable(get_flow_package):
        _GET_FLOW_PACKAGE = get_flow_package
    if callable(get_step_params):
        _GET_STEP_PARAMS = get_step_params
    if callable(resolve_dynamic_value):
        _RESOLVE_DYNAMIC_VALUE = resolve_dynamic_value
    if callable(log_step):
        _LOG_STEP = log_step
    if callable(click_flow_control):
        _CLICK_FLOW_CONTROL = click_flow_control
    if callable(click_relative_region):
        _CLICK_RELATIVE_REGION = click_relative_region
    if callable(click_relative_anchor):
        _CLICK_RELATIVE_ANCHOR = click_relative_anchor
    if callable(focus_flow_control):
        _FOCUS_FLOW_CONTROL = focus_flow_control
    if callable(type_text_into_flow_control):
        _TYPE_TEXT_INTO_FLOW_CONTROL = type_text_into_flow_control
    if callable(type_text_into_relative_region):
        _TYPE_TEXT_INTO_RELATIVE_REGION = type_text_into_relative_region
    if callable(select_dropdown_item_runtime):
        _SELECT_DROPDOWN_ITEM_RUNTIME = select_dropdown_item_runtime
    if callable(drag_between_flow_controls):
        _DRAG_BETWEEN_FLOW_CONTROLS = drag_between_flow_controls
    if callable(mouse_wheel_on_flow_control):
        _MOUSE_WHEEL_ON_FLOW_CONTROL = mouse_wheel_on_flow_control
    if callable(wait_for_flow_control_condition):
        _WAIT_FOR_FLOW_CONTROL_CONDITION = wait_for_flow_control_condition
    if callable(menu_select_flow):
        _MENU_SELECT_FLOW = menu_select_flow
    if callable(locate_template_center_by_path):
        _LOCATE_TEMPLATE_CENTER_BY_PATH = locate_template_center_by_path
    if callable(report_step_result):
        _REPORT_STEP_RESULT = report_step_result
    if callable(run_ai_intervention_after_failure):
        _RUN_AI_INTERVENTION_AFTER_FAILURE = run_ai_intervention_after_failure


def _maybe_run_ai_intervention_after_failure(step_id, context, original_error, fallback_error=None):
    try:
        result = _RUN_AI_INTERVENTION_AFTER_FAILURE(
            step_id,
            context,
            original_error=original_error,
            fallback_error=fallback_error,
        )
    except TypeError:
        result = _RUN_AI_INTERVENTION_AFTER_FAILURE(step_id, context)
    if isinstance(result, dict):
        return result
    if result:
        return {"aiInterventionUsed": True}
    return {}


def _resolve_continue_when(action_config):
    continue_when = action_config.get("continueWhen", {})
    if not isinstance(continue_when, dict):
        return None
    control_id = str(continue_when.get("controlId", "")).strip()
    if not control_id:
        return None
    default_timeout = sleep_seconds(action_config.get("timeoutSeconds", 3), 3)
    return {
        "controlId": control_id,
        "condition": str(continue_when.get("condition", "exists")).strip().lower() or "exists",
        "timeoutSeconds": sleep_seconds(continue_when.get("timeoutSeconds", default_timeout), default_timeout),
        "windowTitleHint": str(
            continue_when.get("windowTitleHint", action_config.get("windowTitleHint", ""))
        ).strip(),
    }


def _wait_for_continue_when(step_id, action_config, phase="action"):
    continue_when = _resolve_continue_when(action_config if isinstance(action_config, dict) else {})
    if not continue_when:
        return {}
    _LOG_STEP(
        "等待步骤续跑条件: "
        f"step={step_id}, phase={phase}, control={continue_when['controlId']}, "
        f"condition={continue_when['condition']}, timeout={continue_when['timeoutSeconds']}"
    )
    if not _WAIT_FOR_FLOW_CONTROL_CONDITION(
        step_id,
        control_id=continue_when["controlId"],
        condition=continue_when["condition"],
        timeout_seconds=continue_when["timeoutSeconds"],
        window_title_hint=continue_when["windowTitleHint"],
    ):
        raise RuntimeError(
            "步骤续跑条件未满足: "
            f"step={step_id}, phase={phase}, control={continue_when['controlId']}, "
            f"condition={continue_when['condition']}, timeout={continue_when['timeoutSeconds']}"
        )
    return {
        "continueWhen": continue_when,
        "continueWhenSatisfied": True,
        "continueWhenPhase": phase,
    }


def sleep_seconds(value, default_seconds=0.0):
    try:
        return max(0.0, float(value))
    except Exception:
        return float(default_seconds)


def resolve_fallback_template_path(template_path):
    normalized_path = os.path.normpath(str(template_path or "").strip())
    if not normalized_path:
        return ""
    if os.path.isabs(normalized_path):
        return normalized_path
    return os.path.normpath(os.path.join(os.path.dirname(__file__), normalized_path))


def _coerce_int(value, default=0):
    """Safely coerce a value to an integer."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

def _resolve_step_policy(action_config):
    """归一化 stepPolicy 到旧字段，保证后续所有代码无感知。

    优先读取 actionConfig.stepPolicy（新格式），
    不存在时保持旧字段 onError/retryCount/retryInterval/continueWhen 原样——零副作用。
    """
    action_config = action_config if isinstance(action_config, dict) else {}
    sp = action_config.get("stepPolicy")
    if not isinstance(sp, dict):
        return  # 无 stepPolicy，旧字段保持不变，现有逻辑完全不动
    # 写入旧字段
    action_config["onError"] = step_policy_on_fail_to_legacy(sp.get("onFail"))
    action_config["retryCount"] = max(0, _coerce_int(sp.get("maxRetries", 0), 0))
    action_config["retryInterval"] = sleep_seconds(sp.get("retryInterval", 1.0), 1.0)
    sp_continue_when = sp.get("continueWhen")
    if isinstance(sp_continue_when, dict):
        action_config["continueWhen"] = sp_continue_when
    elif "continueWhen" in action_config:
        # stepPolicy 不含 continueWhen，则抹掉旧字段里的 continueWhen 以免误用
        action_config.pop("continueWhen", None)


def _resolve_retry_policy(action_config):
    action_config = action_config if isinstance(action_config, dict) else {}
    on_error = str(action_config.get("onError", "")).strip().lower()
    retry_count = max(0, int(sleep_seconds(action_config.get("retryCount", 0), 0)))
    if on_error == "retry" and retry_count <= 0:
        retry_count = 1
    return {
        "retryCount": retry_count,
        "retryInterval": sleep_seconds(action_config.get("retryInterval", 1.0), 1.0),
    }


def _run_action_step_with_retry(step_id, context, action_config, step_extra):
    retry_policy = _resolve_retry_policy(action_config)
    total_attempts = max(1, int(retry_policy.get("retryCount", 0)) + 1)
    retry_interval = float(retry_policy.get("retryInterval", 1.0))
    last_error = None
    for attempt_index in range(1, total_attempts + 1):
        try:
            result = run_action_step(step_id, context) or {}
            if total_attempts > 1:
                step_extra["attemptCount"] = attempt_index
                step_extra["retryCountConfigured"] = total_attempts - 1
            return result
        except Exception as exc:
            last_error = exc
            step_extra["attemptCount"] = attempt_index
            step_extra["retryCountConfigured"] = total_attempts - 1
            step_extra["lastActionError"] = str(exc)
            if attempt_index >= total_attempts:
                break
            _LOG_STEP(
                "步骤执行失败，准备重试: "
                f"step={step_id}, attempt={attempt_index}/{total_attempts}, "
                f"next_after={retry_interval}, error={exc}"
            )
            if retry_interval > 0:
                time.sleep(retry_interval)

    # 所有重试耗尽后，尝试 fallback 链
    fallback_result = _try_fallback_chain(step_id, context, last_error)
    if fallback_result is not None:
        _LOG_STEP(f"Fallback 链执行成功: step={step_id}")
        step_extra["fallbackUsed"] = True
        step_extra["fallbackLevel"] = fallback_result.get("_fallback_level", 0)
        # P3: 反馈闭环 — 记录降级恢复
        _write_feedback_to_flow(context, step_id, {
            "type": "fallback_recovery",
            "fallbackLevel": fallback_result.get("_fallback_level", 0),
            "originalError": str(last_error),
        })
        return fallback_result

    raise last_error


def _try_fallback_chain(step_id, context, original_error):
    """遍历步骤的 fallbackChain 降级链，逐个尝试备用定位策略。

    返回执行结果 dict（含 _fallback_level 标记）或 None（全部失败）。
    """
    step_definition = _GET_STEP_DEFINITION(step_id)
    fallback_chain = step_definition.get("fallbackChain", [])
    if not isinstance(fallback_chain, list) or not fallback_chain:
        return None

    action_config = step_definition.get("actionConfig", {})
    if not isinstance(action_config, dict):
        action_config = {}
    action_config = _RESOLVE_DYNAMIC_VALUE(action_config, step_id, context)

    action_name = str(action_config.get("action", "")).strip().lower()
    text = str(action_config.get("text", action_config.get("value", ""))).strip()
    wait_after = sleep_seconds(action_config.get("waitAfter", 0.3), 0.3)
    timeout_seconds = sleep_seconds(action_config.get("timeoutSeconds", 3), 3)
    save_as = str(action_config.get("saveAs", "output")).strip() or "output"

    for level, fb in enumerate(fallback_chain, start=1):
        if not isinstance(fb, dict):
            continue
        fb_method = str(fb.get("method", "")).strip().lower()
        fb_type = str(fb.get("type", "")).strip().lower()

        _LOG_STEP(
            f"Fallback 链 L{level} 尝试: step={step_id}, method={fb_method}, "
            f"type={fb_type}, confidence={fb.get('confidence', 0)}"
        )

        try:
            center = None
            if fb_type == "template":
                template_path = resolve_fallback_template_path(fb.get("value", ""))
                if template_path and os.path.isfile(template_path):
                    center = _LOCATE_TEMPLATE_CENTER_BY_PATH(template_path, timeout_seconds=timeout_seconds)
            elif fb_type == "coordinate":
                coords = fb.get("value", {})
                if isinstance(coords, dict):
                    center = (int(float(coords.get("x", 0))), int(float(coords.get("y", 0))))
            elif fb_type == "ui_path_search":
                center = _locate_by_uipath_fallback(fb.get("value", ""), timeout_seconds)

            if center:
                center = apply_position_offset(center, action_config)
                result = _execute_fallback_action(action_name, center, text)
                context.setdefault("step_outputs", {}).setdefault(step_id, {})[save_as] = result
                context["step_outputs"][step_id]["output"] = result
                result["_fallback_level"] = level
                if wait_after > 0:
                    time.sleep(wait_after)
                return result
        except Exception as exc:
            _LOG_STEP(f"Fallback 链 L{level} 失败: step={step_id}, error={exc}")
            continue

    return None


def _locate_by_uipath_fallback(uipath, timeout_seconds=3):
    """通过 UIPath 完整路径在窗口树中搜索控件，返回中心坐标（fallback 用）。

    使用 UIA backend 的 pywinauto Desktop 按祖先链逐级导航，
    匹配叶子控件后返回其 bounding rectangle 中心。
    """
    if not uipath:
        return None
    try:
        from pywinauto import Desktop
    except ImportError:
        return None

    segments = [s.strip() for s in str(uipath).split("->") if s.strip()]
    if not segments:
        return None

    deadline = time.time() + max(0.5, float(timeout_seconds or 2))
    while time.time() < deadline:
        try:
            desktop = Desktop(backend="uia")
            # 从根窗口开始搜索
            current = desktop
            for seg in segments:
                name_type = seg.split("||") if "||" in seg else [seg, ""]
                name = name_type[0].strip()
                ctype = name_type[1].strip() if len(name_type) > 1 else ""
                # 在当前层级下查找匹配的子控件
                found = None
                for child in current.descendants():
                    try:
                        child_name = str(child.window_text()).strip()
                        child_ct = str(child.element_info.control_type).strip() if hasattr(child, 'element_info') else ""
                    except Exception:
                        continue
                    if name and name.lower() in child_name.lower():
                        found = child
                        break
                    if ctype and ctype.lower() == child_ct.lower():
                        found = child
                        break
                if found:
                    current = found
                else:
                    current = None
                    break

            if current:
                try:
                    rect = current.rectangle()
                    cx = (rect.left + rect.right) // 2
                    cy = (rect.top + rect.bottom) // 2
                    if cx > 0 and cy > 0:
                        return (cx, cy)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(0.3)

    return None


def _execute_fallback_action(action_name, center, text=""):
    """在指定屏幕坐标执行 fallback 动作，返回 result 值。"""
    import pyautogui
    if action_name in {"click", "wait_for_control"}:
        pyautogui.click(center[0], center[1])
        return f"fallback_coord_{center[0]}_{center[1]}"
    elif action_name == "double_click":
        pyautogui.doubleClick(center[0], center[1])
        return f"fallback_coord_{center[0]}_{center[1]}"
    elif action_name == "right_click":
        pyautogui.click(center[0], center[1], button="right")
        return f"fallback_coord_{center[0]}_{center[1]}"
    elif action_name == "double_right_click":
        pyautogui.click(center[0], center[1], button="right")
        time.sleep(0.2)
        pyautogui.click(center[0], center[1], button="right")
        return f"fallback_coord_{center[0]}_{center[1]}"
    elif action_name in {"type_text", "send_keys"}:
        pyautogui.click(center[0], center[1])
        time.sleep(0.2)
        if text:
            send_keys(text)
        return text
    elif action_name == "mouse_wheel":
        pyautogui.moveTo(center[0], center[1])
        return f"fallback_coord_{center[0]}_{center[1]}"
    elif action_name == "select_dropdown_item_runtime":
        pyautogui.click(center[0], center[1])
        return f"fallback_coord_{center[0]}_{center[1]}"
    else:
        # 默认：尝试点击
        pyautogui.click(center[0], center[1])
        return f"fallback_coord_{center[0]}_{center[1]}"


def apply_position_offset(center_point, action_config):
    if not center_point:
        return center_point
    offset = action_config.get("positionOffset", {})
    if not isinstance(offset, dict):
        return center_point
    try:
        offset_x = int(float(offset.get("x", 0)))
        offset_y = int(float(offset.get("y", 0)))
    except Exception:
        return center_point
    return (int(center_point[0]) + offset_x, int(center_point[1]) + offset_y)


def run_action_step(step_id, context):
    step_definition = _GET_STEP_DEFINITION(step_id)
    action_config = step_definition.get("actionConfig", {})
    if not isinstance(action_config, dict):
        action_config = {}
    action_config = _RESOLVE_DYNAMIC_VALUE(action_config, step_id, context)

    action_name = str(action_config.get("action", "")).strip().lower()
    control_id = str(action_config.get("controlId", "")).strip()
    text = str(action_config.get("text", action_config.get("value", ""))).strip()
    window_title_hint = str(action_config.get("windowTitleHint", "")).strip()
    wait_before = sleep_seconds(action_config.get("waitBefore", 0))
    wait_after = sleep_seconds(action_config.get("waitAfter", 0.3), 0.3)
    timeout_seconds = sleep_seconds(action_config.get("timeoutSeconds", 3), 3)
    save_as = str(action_config.get("saveAs", "output")).strip() or "output"
    action_extra = {}

    if wait_before > 0:
        time.sleep(wait_before)

    result = None
    if action_name == "click":
        if not control_id:
            raise ValueError(f"action 步骤缺少 controlId: {step_id}")
        if not _CLICK_FLOW_CONTROL(step_id, control_id, timeout_seconds=timeout_seconds, window_title_hint=window_title_hint):
            raise RuntimeError(f"action click 未命中控件: step={step_id}, control={control_id}")
        result = control_id
    elif action_name == "click_relative_region":
        parent_window = action_config.get("parentWindow", {}) if isinstance(action_config.get("parentWindow"), dict) else {}
        relative_region = action_config.get("relativeRegion", {}) if isinstance(action_config.get("relativeRegion"), dict) else {}
        has_parent_window_spec = any(
            str(parent_window.get(key, "")).strip()
            for key in ("title", "className", "frameworkId")
        )
        if not has_parent_window_spec and not window_title_hint:
            raise ValueError(f"action click_relative_region 缺少 parentWindow 定位信息或步骤目标窗口: {step_id}")
        ok, region_meta = _CLICK_RELATIVE_REGION(
            step_definition,
            parent_window,
            relative_region,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
        )
        if not ok:
            raise RuntimeError(f"action click_relative_region 未命中父窗口相对区域: {step_id}")
        action_extra["relativeRegion"] = region_meta
        result = region_meta.get("clickPoint", {})
    elif action_name == "click_relative_anchor":
        if not control_id:
            raise ValueError(f"action click_relative_anchor 缺少 controlId(锚点控件): {step_id}")
        try:
            offset_x = int(float(action_config.get("offsetX", 0) or 0))
            offset_y = int(float(action_config.get("offsetY", 0) or 0))
        except Exception:
            raise ValueError(f"action click_relative_anchor 的 offsetX/offsetY 必须为数字: {step_id}")
        click_kind = "double" if str(action_config.get("clickKind", "")).strip().lower() == "double" else "single"
        ok, anchor_meta = _CLICK_RELATIVE_ANCHOR(
            step_id,
            control_id,
            (offset_x, offset_y),
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            click_kind=click_kind,
        )
        if not ok:
            raise RuntimeError(f"action click_relative_anchor 未命中锚点控件: {step_id}")
        action_extra["anchorMeta"] = anchor_meta
        result = anchor_meta.get("clickPoint", {})
    elif action_name == "double_click":
        if not control_id:
            raise ValueError(f"action 步骤缺少 controlId: {step_id}")
        if not _CLICK_FLOW_CONTROL(
            step_id,
            control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            click_kind="double",
        ):
            raise RuntimeError(f"action double_click 未命中控件: step={step_id}, control={control_id}")
        result = control_id
    elif action_name == "double_right_click":
        if not control_id:
            raise ValueError(f"action 步骤缺少 controlId: {step_id}")
        if not _CLICK_FLOW_CONTROL(
            step_id,
            control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            click_kind="right",
        ):
            raise RuntimeError(f"action double_right_click 未命中控件: step={step_id}, control={control_id}")
        time.sleep(0.2)
        if not _CLICK_FLOW_CONTROL(
            step_id,
            control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            click_kind="right",
        ):
            raise RuntimeError(f"action double_right_click 第二次右击失败: step={step_id}, control={control_id}")
        result = control_id
    elif action_name == "right_click":
        if not control_id:
            raise ValueError(f"action 步骤缺少 controlId: {step_id}")
        if not _CLICK_FLOW_CONTROL(
            step_id,
            control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            click_kind="right",
        ):
            raise RuntimeError(f"action right_click 未命中控件: step={step_id}, control={control_id}")
        result = control_id
    elif action_name == "type_text":
        if not control_id:
            raise ValueError(f"action 步骤缺少 controlId: {step_id}")
        if not _TYPE_TEXT_INTO_FLOW_CONTROL(
            step_id,
            control_id,
            text,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
        ):
            raise RuntimeError(f"action type_text 未命中控件: step={step_id}, control={control_id}")
        result = text
    elif action_name == "send_keys":
        if control_id:
            if not _FOCUS_FLOW_CONTROL(
                step_id,
                control_id,
                timeout_seconds=timeout_seconds,
                window_title_hint=window_title_hint,
            ):
                raise RuntimeError(f"action send_keys 未命中控件: step={step_id}, control={control_id}")
        send_keys(text)
        _LOG_STEP(f"已执行 action send_keys: step={step_id}, text={text}")
        result = text
    elif action_name == "type_text_relative":
        parent_window = action_config.get("parentWindow", {}) if isinstance(action_config.get("parentWindow"), dict) else {}
        relative_region = action_config.get("relativeRegion", {}) if isinstance(action_config.get("relativeRegion"), dict) else {}
        post_input_keys = str(action_config.get("postInputKeys", "")).strip()
        has_parent_window_spec = any(
            str(parent_window.get(key, "")).strip()
            for key in ("title", "className", "frameworkId")
        )
        if not has_parent_window_spec and not window_title_hint:
            raise ValueError(f"action type_text_relative 缺少 parentWindow 定位信息或步骤目标窗口: {step_id}")
        try:
            ok, region_meta = _TYPE_TEXT_INTO_RELATIVE_REGION(
                step_definition,
                parent_window,
                relative_region,
                text,
                timeout_seconds=timeout_seconds,
                window_title_hint=window_title_hint,
                post_input_keys=post_input_keys,
            )
        except TypeError as exc:
            if "post_input_keys" not in str(exc):
                raise
            ok, region_meta = _TYPE_TEXT_INTO_RELATIVE_REGION(
                step_definition,
                parent_window,
                relative_region,
                text,
                timeout_seconds=timeout_seconds,
                window_title_hint=window_title_hint,
            )
        if not ok:
            raise RuntimeError(f"action type_text_relative 未命中父窗口相对区域: step={step_id}")
        action_extra["relativeRegion"] = region_meta
        if post_input_keys:
            action_extra["postInputKeys"] = post_input_keys
        result = text
    elif action_name == "select_dropdown_item_runtime":
        if not control_id:
            raise ValueError(f"action 步骤缺少 controlId: {step_id}")
        ok, select_meta = _SELECT_DROPDOWN_ITEM_RUNTIME(
            step_id,
            control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            target_option=text,
        )
        if not ok:
            raise RuntimeError(
                f"action select_dropdown_item_runtime 未命中可见下拉项: step={step_id}, control={control_id}"
            )
        action_extra["dropdownRuntime"] = select_meta
        result = control_id
    elif action_name == "drag_and_drop":
        source_control_id = str(action_config.get("sourceControlId", control_id)).strip()
        target_control_id = str(action_config.get("targetControlId", "")).strip()
        drag_duration = sleep_seconds(action_config.get("durationSeconds", 0.4), 0.4)
        if not source_control_id or not target_control_id:
            raise ValueError(f"action drag_and_drop 缺少 sourceControlId/targetControlId: {step_id}")
        if not _DRAG_BETWEEN_FLOW_CONTROLS(
            step_id,
            source_control_id,
            target_control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            duration_seconds=drag_duration,
        ):
            raise RuntimeError(
                f"action drag_and_drop 未命中控件: step={step_id}, source={source_control_id}, target={target_control_id}"
            )
        result = f"{source_control_id}->{target_control_id}"
    elif action_name == "mouse_wheel":
        wheel_delta = action_config.get("delta", text or action_config.get("amount", 0))
        if not _MOUSE_WHEEL_ON_FLOW_CONTROL(
            step_id,
            control_id=control_id,
            delta=wheel_delta,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
        ):
            raise RuntimeError(f"action mouse_wheel 执行失败: step={step_id}, control={control_id}, delta={wheel_delta}")
        result = wheel_delta
    elif action_name == "wait_for_control":
        if not control_id:
            raise ValueError(f"action 步骤缺少 controlId: {step_id}")
        condition = str(action_config.get("condition", "exists")).strip().lower() or "exists"
        if not _WAIT_FOR_FLOW_CONTROL_CONDITION(
            step_id,
            control_id=control_id,
            condition=condition,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
        ):
            raise RuntimeError(f"action wait_for_control 超时: step={step_id}, control={control_id}, condition={condition}")
        _LOG_STEP(f"已执行 action wait_for_control: step={step_id}, control={control_id}, condition={condition}")
        result = f"{control_id}:{condition}"
    elif action_name == "menu_select":
        menu_path = str(action_config.get("menuPath", text)).strip()
        if not menu_path:
            raise ValueError(f"action menu_select 缺少 menuPath: {step_id}")
        if not _MENU_SELECT_FLOW(
            step_id,
            menu_path,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
        ):
            raise RuntimeError(f"action menu_select 失败: step={step_id}, menuPath={menu_path}")
        _LOG_STEP(f"已执行 action menu_select: step={step_id}, menuPath={menu_path}")
        result = menu_path
    elif action_name == "sleep":
        seconds = sleep_seconds(action_config.get("seconds", text or 1), 1)
        _LOG_STEP(f"已执行 action sleep: step={step_id}, seconds={seconds}")
        time.sleep(seconds)
        result = seconds
    elif action_name == "log":
        message = str(action_config.get("message", text)).strip()
        _LOG_STEP(message or f"action log: {step_id}")
        result = message
    else:
        raise ValueError(f"不支持的 action 类型: {action_name or '(empty)'}")

    action_extra.update(_wait_for_continue_when(step_id, action_config, phase="action") or {})

    context.setdefault("step_outputs", {}).setdefault(step_id, {})[save_as] = result
    context["step_outputs"][step_id]["output"] = result

    if wait_after > 0:
        time.sleep(wait_after)
    return action_extra


def run_action_step_with_template_fallback(step_id, context, original_error):
    step_definition = _GET_STEP_DEFINITION(step_id)
    action_config = step_definition.get("actionConfig", {})
    if not isinstance(action_config, dict):
        action_config = {}
    action_config = _RESOLVE_DYNAMIC_VALUE(action_config, step_id, context)

    fallback_template = resolve_fallback_template_path(action_config.get("fallbackTemplate", ""))
    fallback_mode = str(action_config.get("fallbackMode", "")).strip().lower() or "template_match"
    action_name = str(action_config.get("action", "")).strip().lower()
    text = str(action_config.get("text", action_config.get("value", ""))).strip()
    wait_after = sleep_seconds(action_config.get("waitAfter", 0.3), 0.3)
    timeout_seconds = sleep_seconds(action_config.get("timeoutSeconds", 3), 3)
    save_as = str(action_config.get("saveAs", "output")).strip() or "output"

    if fallback_mode != "template_match":
        raise original_error
    if not fallback_template:
        raise original_error

    _LOG_STEP(
        f"步骤执行失败，尝试模板兜底: step={step_id}, action={action_name}, "
        f"template={fallback_template}, error={original_error}"
    )

    center = _LOCATE_TEMPLATE_CENTER_BY_PATH(fallback_template, timeout_seconds=timeout_seconds)
    if not center:
        raise RuntimeError(
            f"模板兜底未命中: step={step_id}, action={action_name}, "
            f"template={fallback_template}, timeout={timeout_seconds}"
        ) from original_error
    center = apply_position_offset(center, action_config)
    result = None

    if action_name == "click":
        pyautogui.click(center[0], center[1])
        result = fallback_template
    elif action_name == "double_click":
        pyautogui.doubleClick(center[0], center[1])
        result = fallback_template
    elif action_name == "right_click":
        pyautogui.click(center[0], center[1], button="right")
        result = fallback_template
    elif action_name == "double_right_click":
        pyautogui.click(center[0], center[1], button="right")
        time.sleep(0.2)
        pyautogui.click(center[0], center[1], button="right")
        result = fallback_template
    elif action_name in {"type_text", "send_keys"}:
        pyautogui.click(center[0], center[1])
        time.sleep(0.2)
        send_keys(text)
        result = text
    elif action_name == "mouse_wheel":
        pyautogui.moveTo(center[0], center[1])
        wheel_delta = action_config.get("delta", text or action_config.get("amount", 0))
        try:
            pyautogui.scroll(int(float(wheel_delta)))
        except Exception:
            pyautogui.scroll(0)
        result = wheel_delta
    elif action_name == "wait_for_control":
        result = fallback_template
    elif action_name == "select_dropdown_item_runtime":
        pyautogui.click(center[0], center[1])
        result = fallback_template
    else:
        raise RuntimeError(
            f"当前 action 暂不支持模板兜底执行: step={step_id}, action={action_name}, "
            f"template={fallback_template}"
        ) from original_error

    _LOG_STEP(f"模板兜底执行成功: step={step_id}, action={action_name}, template={fallback_template}")
    context.setdefault("step_outputs", {}).setdefault(step_id, {})[save_as] = result
    context["step_outputs"][step_id]["output"] = result

    if wait_after > 0:
        time.sleep(wait_after)


def run_flow_ref_step(step_id, execution_plan_map, context, skip_setup=False):
    step_definition = _GET_STEP_DEFINITION(step_id)
    package_ref = str(step_definition.get("packageRef", "")).strip()
    if not package_ref:
        raise ValueError(f"flow_ref 步骤缺少 packageRef: {step_id}")
    package_definition = _GET_FLOW_PACKAGE(package_ref)
    if not isinstance(package_definition, dict) or not package_definition:
        raise ValueError(f"未找到流程包定义: {package_ref}")
    package_step_ids = package_definition.get("stepIds", [])
    if not isinstance(package_step_ids, list) or not package_step_ids:
        raise ValueError(f"流程包未定义 stepIds: {package_ref}")

    stack = context.setdefault("flow_ref_stack", [])
    if package_ref in stack:
        raise RuntimeError(f"检测到流程包循环引用: {' -> '.join(stack + [package_ref])}")

    _LOG_STEP(f"进入流程包: {package_ref}")
    stack.append(package_ref)
    flow_ref_param_stack = context.setdefault("flow_ref_param_stack", [])
    parent_step_params = _GET_STEP_PARAMS(step_id)
    flow_ref_param_stack.append(parent_step_params if isinstance(parent_step_params, dict) else {})
    try:
        for child_step_id in package_step_ids:
            execute_step_by_id(str(child_step_id or "").strip(), execution_plan_map, context, skip_setup=skip_setup)
    finally:
        flow_ref_param_stack.pop()
        stack.pop()
    _LOG_STEP(f"完成流程包: {package_ref}")


def is_setup_step(step_id):
    return step_id in {"launch_gm", "configure_projection", "open_source_dwg", "close_unknown_projection", "dwg_projection_confirm"}


def execute_step_by_id(step_id, execution_plan_map, context, skip_setup=False):
    item = execution_plan_map.get(step_id)
    step_definition = _GET_STEP_DEFINITION(step_id)
    if not isinstance(step_definition, dict) or not step_definition:
        _LOG_STEP(f"未找到步骤定义，当前跳过: {step_id}")
        _REPORT_STEP_RESULT(context.get("run_report"), step_id, step_id, "skipped", error="未找到步骤定义")
        return
    if not bool(step_definition.get("enabled", True)):
        _LOG_STEP(f"步骤已停用，跳过执行: {step_id}")
        _REPORT_STEP_RESULT(
            context.get("run_report"),
            step_id,
            str(step_definition.get("name", "")).strip() or step_id,
            "skipped",
            action_type=str(step_definition.get("actionType", "script")).strip(),
            strategy=str(step_definition.get("strategy", "")).strip(),
            error="步骤已停用",
        )
        return
    if skip_setup and is_setup_step(step_id):
        _LOG_STEP(f"skip_setup 跳过步骤: {step_id}")
        _REPORT_STEP_RESULT(
            context.get("run_report"),
            step_id,
            str(step_definition.get("name", "")).strip() or step_id,
            "skipped",
            action_type=str(step_definition.get("actionType", "script")).strip(),
            strategy=str(step_definition.get("strategy", "")).strip(),
            error="skip_setup 跳过初始化步骤",
        )
        return

    step_name = str(step_definition.get("name", "")).strip() or step_id
    step_started = time.time()
    _LOG_STEP(f"开始执行步骤: step={step_id}, name={step_name}")

    action_type = str(step_definition.get("actionType", "script")).strip().lower() or "script"
    strategy = str(step_definition.get("strategy", "")).strip()
    step_status = "success"
    step_error = ""
    step_extra = {}
    try:
        if action_type == "script":
            func = item.get("func") if isinstance(item, dict) else None
            if func is None:
                _LOG_STEP(f"步骤未绑定执行函数，当前跳过: {step_id}")
                step_status = "skipped"
                step_error = "步骤未绑定执行函数"
                return
            func(context)
            return
        if action_type == "action":
            action_config = step_definition.get("actionConfig", {})
            if not isinstance(action_config, dict):
                action_config = {}
            _resolve_step_policy(action_config)  # stepPolicy → 旧字段归一化（无 stepPolicy 时零副作用）
            on_error = str(action_config.get("onError", "")).strip().lower() or "stop"
            fallback_template = str(action_config.get("fallbackTemplate", "")).strip()
            try:
                step_extra.update(_run_action_step_with_retry(step_id, context, action_config, step_extra) or {})
            except Exception as exc:
                if on_error == "fallback" and fallback_template:
                    fallback_exc = None
                    try:
                        run_action_step_with_template_fallback(step_id, context, exc)
                        step_extra["fallbackTemplateUsed"] = fallback_template
                        step_extra["fallbackReason"] = str(exc)
                        step_extra.update(_wait_for_continue_when(step_id, action_config, phase="template_fallback") or {})
                        # P3: 反馈闭环 — 记录模板降级恢复
                        _write_feedback_to_flow(context, step_id, {
                            "type": "fallback_template_recovery",
                            "fallbackTemplate": fallback_template,
                            "originalError": str(exc),
                        })
                        return
                    except Exception as current_fallback_exc:
                        fallback_exc = current_fallback_exc
                        step_extra["fallbackTemplateAttempted"] = fallback_template
                        step_extra["fallbackReason"] = str(exc)
                        step_extra["fallbackError"] = str(current_fallback_exc)
                    ai_extra = _maybe_run_ai_intervention_after_failure(
                        step_id,
                        context,
                        original_error=exc,
                        fallback_error=fallback_exc,
                    )
                    if ai_extra:
                        step_extra.update(ai_extra)
                        step_extra.update(_wait_for_continue_when(step_id, action_config, phase="ai_intervention") or {})
                        return
                    if fallback_exc is not None:
                        raise RuntimeError(
                            f"{fallback_exc} | 原始错误: {exc}"
                        ) from fallback_exc
                if on_error == "continue":
                    step_status = "failed"
                    step_error = str(exc)
                    step_extra["onErrorHandled"] = "continue"
                    _LOG_STEP(f"步骤执行失败但按 continue 继续后续流程: step={step_id}, error={exc}")
                    return
                if on_error == "ask":
                    # ask 模式：失败时直接触发 AI/人工干预
                    ai_extra = _maybe_run_ai_intervention_after_failure(
                        step_id, context, original_error=exc
                    )
                    if ai_extra:
                        step_extra.update(ai_extra)
                        step_extra["onErrorHandled"] = "ask"
                        step_extra.update(_wait_for_continue_when(step_id, action_config, phase="ai_intervention") or {})
                        return
                    raise
                raise
            return
        if action_type == "flow_ref":
            run_flow_ref_step(step_id, execution_plan_map, context, skip_setup=skip_setup)
            return
        if action_type == "placeholder":
            _LOG_STEP(f"占位步骤，当前跳过: {step_id}")
            step_status = "skipped"
            step_error = "占位步骤"
            return
        _LOG_STEP(f"未知 actionType，当前跳过: step={step_id}, actionType={action_type}")
        step_status = "skipped"
        step_error = f"未知 actionType: {action_type}"
    except Exception as exc:
        step_status = "failed"
        step_error = str(exc)
        # P3: 反馈闭环 — 记录步骤失败
        _write_feedback_to_flow(context, step_id, {
            "type": "step_failure",
            "error": str(exc),
            "actionType": action_type,
        })
        raise
    finally:
        elapsed = time.time() - step_started
        summary_parts = [
            f"step={step_id}",
            f"name={step_name}",
            f"status={step_status}",
            f"seconds={elapsed:.2f}",
        ]
        if step_error:
            summary_parts.append(f"error={step_error}")
        if step_extra.get("fallbackTemplateUsed"):
            summary_parts.append(f"fallback={step_extra.get('fallbackTemplateUsed')}")
        if step_extra.get("attemptCount"):
            configured_retry_count = int(step_extra.get("retryCountConfigured", 0) or 0)
            total_attempts = configured_retry_count + 1
            summary_parts.append(f"attempt={step_extra.get('attemptCount')}/{total_attempts}")
        _LOG_STEP("步骤结束: " + ", ".join(summary_parts))
        _REPORT_STEP_RESULT(
            context.get("run_report"),
            step_id,
            step_name,
            step_status,
            action_type=action_type,
            strategy=strategy,
            elapsed=elapsed,
            error=step_error,
            extra=step_extra,
        )
