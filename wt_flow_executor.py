# encoding: utf-8

import json
import os
import threading
import time
from datetime import datetime

import pyautogui
from pywinauto_recorder.player import send_keys

from wt_action_schema import step_policy_on_fail_to_legacy

try:
    import image_template_index
except Exception:
    image_template_index = None


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
_LOCATE_FLOW_CONTROL = lambda *args, **kwargs: None
_CALL_GET_WRAPPER_VALUE_SNAPSHOT = lambda wrapper: {}
_MENU_SELECT_FLOW = lambda *args, **kwargs: False
_LOCATE_TEMPLATE_CENTER_BY_PATH = lambda *args, **kwargs: None
_REPORT_STEP_RESULT = lambda *args, **kwargs: None
_RUN_AI_INTERVENTION_AFTER_FAILURE = lambda *args, **kwargs: None

_FLOW_FEEDBACK_LOCK = threading.Lock()
# 执行中自动采集控件模板（P1）：由调用方注入（如 WT_AUT_recorded 用 flow_locator 定位控件
# 后截图保存到 image_templates/auto_captured/）。默认空操作，执行器保持通用。
_AUTO_CAPTURE_TEMPLATE = lambda *args, **kwargs: None

# ── 控件库路径（用于激活 JSON 模糊匹配和 bbox 兜底） ──
_CONTROL_MAP_PATH = None


def set_control_map_path(path: str):
    """设置控件库 JSON 路径，激活 find_flow_control 的 Stage A/B。"""
    global _CONTROL_MAP_PATH
    _CONTROL_MAP_PATH = path


def get_control_map_path():
    """获取当前控件库 JSON 路径。"""
    return _CONTROL_MAP_PATH


def _resolve_control_map_path(step_definition=None):
    """按优先级解析控件库路径：step 定义 > 全局配置 > None"""
    if step_definition and isinstance(step_definition, dict):
        step_path = step_definition.get("controlMapPath") or step_definition.get("control_map_path")
        if step_path:
            return step_path
    return _CONTROL_MAP_PATH


def _call_with_control_map_path(func, step_definition, *args, **kwargs):
    """调用回调函数，尝试传入 control_map_path；若回调签名不接受则安全降级。"""
    cmp = _resolve_control_map_path(step_definition)
    if cmp is None:
        return func(*args, **kwargs)
    try:
        return func(*args, **kwargs, control_map_path=cmp)
    except TypeError as exc:
        if "control_map_path" in str(exc):
            try:
                _LOG_STEP(
                    "Warning: callback does not accept control_map_path, degraded call: func={}, path={}".format(
                        getattr(func, "__name__", repr(func)), cmp
                    )
                )
            except Exception:
                pass
            return func(*args, **kwargs)
        raise


def _write_feedback_to_flow(context, step_id, feedback_data):
    """运行时反馈闭环：将步骤执行反馈回写到 flow_definition.json。

    在 context["flowDefinitionPath"] 存在时，向 flow_definition 的
    feedbackHistory 数组追加一条反馈记录。用于积累多次运行的稳定性数据。
    读-改-写整体加锁，避免多任务并发回写时丢反馈或写坏 flow JSON。
    """
    flow_path = context.get("flowDefinitionPath", "")
    if not flow_path or not os.path.isfile(flow_path):
        return
    with _FLOW_FEEDBACK_LOCK:
        try:
            with open(flow_path, "r", encoding="utf-8") as f:
                flow_def = json.load(f)
        except Exception as e:
            _LOG_STEP(f"Warning: Failed to load flow definition for feedback: {e} (step={step_id})")
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

        tmp_path = flow_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(flow_def, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, flow_path)
        except Exception as e:
            _LOG_STEP(f"Warning: Failed to save flow definition for feedback: {e} (step={step_id})")
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
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
    find_flow_control=None,
    get_wrapper_value_snapshot=None,
    menu_select_flow=None,
    locate_template_center_by_path=None,
    report_step_result=None,
    run_ai_intervention_after_failure=None,
    auto_capture_template=None,
    control_map_path=None,
):
    global _GET_STEP_DEFINITION, _GET_FLOW_PACKAGE, _GET_STEP_PARAMS
    global _RESOLVE_DYNAMIC_VALUE, _LOG_STEP, _CLICK_FLOW_CONTROL, _CLICK_RELATIVE_REGION
    global _CLICK_RELATIVE_ANCHOR
    global _FOCUS_FLOW_CONTROL, _TYPE_TEXT_INTO_FLOW_CONTROL, _TYPE_TEXT_INTO_RELATIVE_REGION
    global _SELECT_DROPDOWN_ITEM_RUNTIME
    global _DRAG_BETWEEN_FLOW_CONTROLS, _MOUSE_WHEEL_ON_FLOW_CONTROL
    global _WAIT_FOR_FLOW_CONTROL_CONDITION, _MENU_SELECT_FLOW
    global _LOCATE_FLOW_CONTROL, _CALL_GET_WRAPPER_VALUE_SNAPSHOT
    global _LOCATE_TEMPLATE_CENTER_BY_PATH, _REPORT_STEP_RESULT
    global _RUN_AI_INTERVENTION_AFTER_FAILURE, _AUTO_CAPTURE_TEMPLATE

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
    if callable(find_flow_control):
        _LOCATE_FLOW_CONTROL = find_flow_control
    if callable(get_wrapper_value_snapshot):
        _CALL_GET_WRAPPER_VALUE_SNAPSHOT = get_wrapper_value_snapshot
    if callable(menu_select_flow):
        _MENU_SELECT_FLOW = menu_select_flow
    if callable(locate_template_center_by_path):
        _LOCATE_TEMPLATE_CENTER_BY_PATH = locate_template_center_by_path
    if callable(report_step_result):
        _REPORT_STEP_RESULT = report_step_result
    if callable(run_ai_intervention_after_failure):
        _RUN_AI_INTERVENTION_AFTER_FAILURE = run_ai_intervention_after_failure
    if callable(auto_capture_template):
        _AUTO_CAPTURE_TEMPLATE = auto_capture_template
    if control_map_path is not None:
        set_control_map_path(control_map_path)


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


def _resolve_continue_when(action_config, step_definition=None):
    continue_when = action_config.get("continueWhen", {})
    value_assert_action = _is_value_assert_action(action_config)
    # 未显式配置 continueWhen（缺失/空/非 dict）→ 输入/选择类动作触发自动值断言
    if not isinstance(continue_when, dict) or not continue_when:
        if not value_assert_action:
            return None
        control_id = "".join([
            str(action_config.get("controlId", "")).strip(),
            str(action_config.get("controlRef", "")).strip(),
        ]).strip()
        if not control_id:
            return None
        expected = _resolve_expected_value(action_config)
        default_timeout = sleep_seconds(action_config.get("timeoutSeconds", 3), 3)
        return {
            "controlId": control_id,
            "condition": "nonempty" if expected == "" else "value_equals",
            "timeoutSeconds": default_timeout,
            "windowTitleHint": "",
            "expectedValue": expected,
            "autoAssert": True,
        }
    control_id = str(continue_when.get("controlId", "")).strip()
    if not control_id:
        return None
    default_timeout = sleep_seconds(action_config.get("timeoutSeconds", 3), 3)
    condition = str(continue_when.get("condition", "exists")).strip().lower() or "exists"
    expected = str(continue_when.get("expectedValue", "")).strip()
    if not expected and condition in {"value_equals", "nonempty", "non_empty"}:
        # 显式值条件未填期望值时，从动作值自动取
        expected = _resolve_expected_value(action_config)
        if condition == "non_empty":
            condition = "nonempty"
    # value_equals 未取到期望值 → 降级为非空断言，仍可拦空值假成功
    resolved_condition = condition
    if condition == "value_equals" and expected == "":
        resolved_condition = "nonempty"
    return {
        "controlId": control_id,
        "condition": resolved_condition,
        "timeoutSeconds": sleep_seconds(continue_when.get("timeoutSeconds", default_timeout), default_timeout),
        "windowTitleHint": str(
            continue_when.get("windowTitleHint", action_config.get("windowTitleHint", ""))
        ).strip(),
        "expectedValue": expected,
    }


def _is_value_assert_action(action_config):
    """动作是否为输入/选择类（其效果可用控件值断言）。"""
    action = str(action_config.get("action", "")).strip().lower()
    return action in {
        "type_text", "type_text_relative", "send_keys", "set_text",
        "set_combobox", "select_dropdown_item", "select_dropdown_item_runtime",
        "set_value", "input", "type",
    }


def _resolve_expected_value(action_config):
    """从 actionConfig 提取待断言的预期值（inputText > value > text 优先级）。"""
    for k in ("inputText", "value", "text"):
        v = str(action_config.get(k, "")).strip()
        if v:
            return v
    text = str(action_config.get("text", "")).strip()
    return text


def _wait_for_continue_when(step_id, action_config, phase="action", step_definition=None):
    continue_when = _resolve_continue_when(action_config if isinstance(action_config, dict) else {}, step_definition=step_definition)
    if not continue_when:
        return {}
    _LOG_STEP(
        "等待步骤续跑条件: "
        f"step={step_id}, phase={phase}, control={continue_when['controlId']}, "
        f"condition={continue_when['condition']}, timeout={continue_when['timeoutSeconds']}"
    )
    satisfied = _call_with_control_map_path(
        _WAIT_FOR_FLOW_CONTROL_CONDITION, step_definition or {},
        step_id,
        control_id=continue_when["controlId"],
        condition=continue_when["condition"],
        timeout_seconds=continue_when["timeoutSeconds"],
        window_title_hint=continue_when["windowTitleHint"],
        expected_value=continue_when.get("expectedValue", ""),
    )
    if not satisfied:
        _write_failed_continue_when_evidence(
            step_id, continue_when, step_definition=step_definition or {}
        )
        raise RuntimeError(
            "步骤续跑条件未满足: "
            f"step={step_id}, phase={phase}, control={continue_when['controlId']}, "
            f"condition={continue_when['condition']}, "
            f"expectedValue={continue_when.get('expectedValue', '')}, "
            f"timeout={continue_when['timeoutSeconds']}"
        )
    return {
        "continueWhen": continue_when,
        "continueWhenSatisfied": True,
        "continueWhenPhase": phase,
    }


def _snapshot_control_value(step_definition, control_id, window_title_hint=""):
    """尝试对目标控件做 UIA 值快照，返回 dict；无法定位或读取时返回空 dict。"""
    try:
        if _WAIT_FOR_FLOW_CONTROL_CONDITION is None:
            return {}
        control = _call_with_control_map_path(
            _LOCATE_FLOW_CONTROL, step_definition or {},
            step_definition.get("id", "") or "",
            control_id=control_id,
            timeout_seconds=1.0,
            window_title_hint=window_title_hint,
        )
        if control is None:
            return {"controlLocated": False}
        snap = _CALL_GET_WRAPPER_VALUE_SNAPSHOT(control) if _CALL_GET_WRAPPER_VALUE_SNAPSHOT else {}
        return dict(snap or {})
    except Exception as exc:
        return {"snapshotError": str(exc)}


def _write_failed_continue_when_evidence(step_id, continue_when, step_definition=None):
    """断言失败时把目标控件的 UIA 值快照写入 logs/evidence/<runId>/step_<id>.json。"""
    try:
        evidence_dir = os.path.join(os.path.dirname(__file__), "logs", "evidence")
        os.makedirs(evidence_dir, exist_ok=True)
        snap = _snapshot_control_value(
            step_definition or {},
            continue_when.get("controlId", ""),
            window_title_hint=continue_when.get("windowTitleHint", ""),
        )
        payload = {
            "stepId": step_id,
            "failedAt": datetime.now().isoformat(timespec="seconds"),
            "condition": continue_when.get("condition", ""),
            "expectedValue": continue_when.get("expectedValue", ""),
            "controlSnapshot": snap,
        }
        with open(os.path.join(evidence_dir, f"step_{step_id}.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


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


def _resolve_template_key_from_controls(step_definition):
    """自动接线：步骤未显式配置 fallbackTemplate 时，从细分控件的 templateKey 解析模板。

    兼容 recorder 伴随拾取（templateKey=recorder_captures/...）与采集器索引的模板引用，
    让“录制/采集时自动截图”的模板在执行时真正可用。返回模板绝对路径，找不到返回空串。
    """
    if image_template_index is None:
        return ""
    controls = step_definition.get("controls", []) if isinstance(step_definition, dict) else []
    if not isinstance(controls, list):
        return ""
    for control in controls:
        if not isinstance(control, dict):
            continue
        template_key = str(control.get("templateKey", "")).strip()
        if template_key:
            path = image_template_index.get_template_path(template_key)
            if path:
                return path
    return ""


def _coerce_int(value, default=0):
    """Safely coerce a value to an integer."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default

def _resolve_step_policy(action_config):
    """归一化 stepPolicy 到旧字段，返回归一化后的 action_config dict。

    优先读取 actionConfig.stepPolicy（新格式），
    不存在时旧字段 onError/retryCount/retryInterval/continueWhen 原样返回——零副作用。
    注意：action_config 可能来自 lru 缓存的流程定义，归一化前先深拷贝，
    避免写穿进程内缓存（同一流程被重复执行/flow_ref 二次引用时旧字段语义被静默覆盖）。
    """
    if not isinstance(action_config, dict):
        return action_config
    sp = action_config.get("stepPolicy")
    if not isinstance(sp, dict):
        return action_config  # 无 stepPolicy，旧字段保持不变，现有逻辑完全不动
    import copy
    result = copy.deepcopy(action_config)
    # 写入旧字段（stepPolicy 本身保留，不影响读取方）
    result["onError"] = step_policy_on_fail_to_legacy(sp.get("onFail"))
    result["retryCount"] = max(0, _coerce_int(sp.get("maxRetries", 0), 0))
    result["retryInterval"] = sleep_seconds(sp.get("retryInterval", 1.0), 1.0)
    sp_continue_when = sp.get("continueWhen")
    if isinstance(sp_continue_when, dict):
        result["continueWhen"] = sp_continue_when
    elif "continueWhen" in result:
        # stepPolicy 不含 continueWhen，则抹掉旧字段里的 continueWhen 以免误用
        result.pop("continueWhen", None)
    return result


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
    # 归一化 stepPolicy（深拷贝、幂等、不写穿缓存）：本函数从流程定义重新读取
    # action_config，不归一化会看不到 stepPolicy 提供的 continueWhen 等旧字段。
    action_config = _resolve_step_policy(action_config)
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
                result = _execute_fallback_action(
                    action_name,
                    center,
                    text,
                    step_id=step_id,
                    action_config=action_config,
                    step_definition=step_definition,
                    timeout_seconds=timeout_seconds,
                )
                if isinstance(result, dict) and result.get("valueConfirmed") is False:
                    raise RuntimeError("fallback result unconfirmed: step={}, level={}".format(step_id, level))
                context.setdefault("step_outputs", {}).setdefault(step_id, {})[save_as] = result
                context["step_outputs"][step_id]["output"] = result
                result["_fallback_level"] = level
                if wait_after > 0:
                    time.sleep(wait_after)
                # 与模板兜底/AI 介入路径对齐：fallback 链成功后若配置了续跑条件
                # （continueWhen），必须满足才算成功，否则尝试下一级 fallback。
                # 未配置 continueWhen 时 _wait_for_continue_when 为无副作用空操作。
                try:
                    _wait_for_continue_when(step_id, action_config, phase="fallback_chain", step_definition=step_definition)
                except RuntimeError as cw_exc:
                    _LOG_STEP(
                        f"Fallback 链 L{level} 续跑条件未满足，尝试下一级: step={step_id}, error={cw_exc}"
                    )
                    continue
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


def _scroll_at_point(center, delta):
    """移动鼠标到目标坐标并执行滚轮 delta，坐标兜底真正滚动而不是仅移动。"""
    try:
        pyautogui.moveTo(int(center[0]), int(center[1]))
    except Exception:
        pass
    try:
        pyautogui.scroll(int(float(delta)))
    except Exception:
        pyautogui.scroll(0)


def _perform_coordinate_action(action_name, center, text="", delta=0):
    """在屏幕坐标 center 上执行与 action_name 对应的鼠标/键盘动作。

    供 fallback 链与模板兜底共用，避免两份近似的点击分发逻辑。
    """
    if action_name == "select_dropdown_item_runtime":
        _LOG_STEP("Warning: refusing raw coordinate click for select_dropdown_item_runtime")
        return
    if action_name in {"click", "wait_for_control"}:
        pyautogui.click(center[0], center[1])
    elif action_name == "double_click":
        pyautogui.doubleClick(center[0], center[1])
    elif action_name == "right_click":
        pyautogui.click(center[0], center[1], button="right")
    elif action_name == "double_right_click":
        pyautogui.click(center[0], center[1], button="right")
        time.sleep(0.2)
        pyautogui.click(center[0], center[1], button="right")
    elif action_name in {"type_text", "send_keys"}:
        pyautogui.click(center[0], center[1])
        time.sleep(0.2)
        if text:
            send_keys(text)
    elif action_name == "mouse_wheel":
        _scroll_at_point(center, delta)
    else:
        # 默认：尝试点击
        pyautogui.click(center[0], center[1])


def _execute_fallback_action(action_name, center, text="", step_id=None, action_config=None, step_definition=None, timeout_seconds=3):
    """Execute a fallback action at screen coordinates and return a result dict.

    Dropdown actions prefer the latest runtime locator and never fall back to
    an unconfirmed coordinate-only click; failures raise so the fallback chain
    can continue to the next level.
    """
    if action_name == "select_dropdown_item_runtime":
        dropdown_meta = _try_dropdown_runtime_fallback(
            step_id=step_id,
            action_config=action_config,
            step_definition=step_definition,
            timeout_seconds=timeout_seconds,
        )
        if dropdown_meta is not None:
            return dropdown_meta
        control_id = action_config.get("controlId", "") if isinstance(action_config, dict) else ""
        raise RuntimeError(
            "dropdown fallback 未命中可见下拉项，拒绝把坐标点击当作成功: "
            "step={}, control={}".format(step_id, control_id)
        )

    if action_name == "mouse_wheel":
        wheel_delta = (
            action_config.get("delta", text or action_config.get("amount", 0))
            if isinstance(action_config, dict)
            else 0
        )
        _perform_coordinate_action(action_name, center, text, delta=wheel_delta)
        return {
            "method": action_name,
            "point": [int(center[0]), int(center[1])],
            "fallback": True,
            "delta": wheel_delta,
            "detail": str(wheel_delta),
        }

    _perform_coordinate_action(action_name, center, text)
    detail = text if action_name in {"type_text", "send_keys"} else "fallback_coord_{}_{}".format(int(center[0]), int(center[1]))
    return {
        "method": action_name,
        "point": [int(center[0]), int(center[1])],
        "fallback": True,
        "detail": detail,
    }


def _try_dropdown_runtime_fallback(step_id=None, action_config=None, step_definition=None, timeout_seconds=3):
    """Retry the latest dropdown runtime locator during fallback execution."""
    if not step_id or not isinstance(action_config, dict):
        return None
    control_id = str(action_config.get("controlId", "")).strip()
    target_option = str(action_config.get("text", action_config.get("value", ""))).strip()
    if not control_id:
        return None
    try:
        ok, select_meta = _call_with_control_map_path(
            _SELECT_DROPDOWN_ITEM_RUNTIME,
            step_definition,
            step_id,
            control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=str(action_config.get("windowTitleHint", "")).strip(),
            target_option=target_option,
        )
    except Exception as exc:
        _LOG_STEP("dropdown fallback retry failed: step={}, error={}".format(step_id, exc))
        return None
    if not ok:
        return None
    return {
        "method": "select_dropdown_item_runtime",
        "controlId": control_id,
        "targetOption": target_option,
        "fallback": True,
        "valueConfirmed": True,
        "detail": select_meta or target_option,
    }



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
    # 归一化 stepPolicy（深拷贝、幂等、不写穿缓存）：本函数从流程定义重新读取
    # action_config，不归一化会看不到 stepPolicy 提供的 continueWhen（动作后置校验）。
    action_config = _resolve_step_policy(action_config)
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
    click_kind_by_action = {
        "click": "left",
        "double_click": "double",
        "right_click": "right",
        "double_right_click": "right",
    }
    if action_name in click_kind_by_action:
        if not control_id:
            raise ValueError(f"action 步骤缺少 controlId: {step_id}")
        click_kind = click_kind_by_action[action_name]
        click_count = 2 if action_name == "double_right_click" else 1
        for click_index in range(click_count):
            if click_index > 0:
                time.sleep(0.2)
            if not _call_with_control_map_path(
                _CLICK_FLOW_CONTROL,
                step_definition,
                step_id,
                control_id,
                timeout_seconds=timeout_seconds,
                window_title_hint=window_title_hint,
                click_kind=click_kind,
            ):
                failure_note = "第二次右击失败" if click_index > 0 else "未命中控件"
                raise RuntimeError(f"action {action_name} {failure_note}: step={step_id}, control={control_id}")
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
        ok, region_meta = _call_with_control_map_path(
            _CLICK_RELATIVE_REGION, step_definition,
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
        ok, anchor_meta = _call_with_control_map_path(
            _CLICK_RELATIVE_ANCHOR, step_definition,
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
    elif action_name == "type_text":
        if not control_id:
            raise ValueError(f"action 步骤缺少 controlId: {step_id}")
        if not _call_with_control_map_path(
            _TYPE_TEXT_INTO_FLOW_CONTROL, step_definition,
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
            if not _call_with_control_map_path(
                _FOCUS_FLOW_CONTROL, step_definition,
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
            ok, region_meta = _call_with_control_map_path(
                _TYPE_TEXT_INTO_RELATIVE_REGION, step_definition,
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
            ok, region_meta = _call_with_control_map_path(
                _TYPE_TEXT_INTO_RELATIVE_REGION, step_definition,
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
        ok, select_meta = _call_with_control_map_path(
            _SELECT_DROPDOWN_ITEM_RUNTIME, step_definition,
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
        if not _call_with_control_map_path(
            _DRAG_BETWEEN_FLOW_CONTROLS, step_definition,
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
        if not _call_with_control_map_path(
            _MOUSE_WHEEL_ON_FLOW_CONTROL, step_definition,
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
        if not _call_with_control_map_path(
            _WAIT_FOR_FLOW_CONTROL_CONDITION, step_definition,
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
        if not _call_with_control_map_path(
            _MENU_SELECT_FLOW, step_definition,
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

    action_extra.update(_wait_for_continue_when(step_id, action_config, phase="action", step_definition=step_definition) or {})

    context.setdefault("step_outputs", {}).setdefault(step_id, {})[save_as] = result
    context["step_outputs"][step_id]["output"] = result

    if wait_after > 0:
        time.sleep(wait_after)
    return action_extra


def run_action_step_with_template_fallback(step_id, context, original_error=None, on_miss="raise"):
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
        if original_error is not None:
            raise original_error
        raise RuntimeError(
            f"fallbackMode 非 template_match: step={step_id}, mode={fallback_mode}"
        )
    if not fallback_template:
        if original_error is not None:
            raise original_error
        raise RuntimeError(f"缺少 fallbackTemplate: step={step_id}")

    if original_error is None:
        _LOG_STEP(
            f"步骤优先模板识别: step={step_id}, action={action_name}, "
            f"template={fallback_template}"
        )
    else:
        _LOG_STEP(
            f"步骤执行失败，尝试模板兜底: step={step_id}, action={action_name}, "
            f"template={fallback_template}, error={original_error}"
        )

    center = _LOCATE_TEMPLATE_CENTER_BY_PATH(fallback_template, timeout_seconds=timeout_seconds)
    if not center:
        if on_miss == "return_none":
            _LOG_STEP(
                f"模板优先未命中，回退主流程: step={step_id}, template={fallback_template}"
            )
            return None
        raise RuntimeError(
            f"模板兜底未命中: step={step_id}, action={action_name}, "
            f"template={fallback_template}, timeout={timeout_seconds}"
        ) from original_error
    center = apply_position_offset(center, action_config)
    result = None

    if action_name == "wait_for_control":
        result = fallback_template
    elif action_name == "mouse_wheel":
        wheel_delta = action_config.get("delta", text or action_config.get("amount", 0))
        _perform_coordinate_action("mouse_wheel", center, delta=wheel_delta)
        result = wheel_delta
    elif action_name in {
        "click",
        "double_click",
        "right_click",
        "double_right_click",
        "type_text",
        "send_keys",
    }:
        _perform_coordinate_action(action_name, center, text)
        result = text if action_name in {"type_text", "send_keys"} else fallback_template
    elif action_name == "click_relative_anchor":
        # 模板模式下：模板命中点即目标点击点，不再叠加 click_relative_anchor 的
        # offsetX/offsetY（截图本身已包含目标位置，叠加会导致点偏）。
        # 若确需额外偏移，可用通用 positionOffset 字段。
        target_point = (int(center[0]), int(center[1]))
        if str(action_config.get("clickKind", "")).strip().lower() == "double":
            pyautogui.doubleClick(target_point[0], target_point[1])
        else:
            pyautogui.click(target_point[0], target_point[1])
        result = {
            "method": action_name,
            "clickPoint": {"x": target_point[0], "y": target_point[1]},
            "fallback": True,
            "template": fallback_template,
        }
    elif action_name == "select_dropdown_item_runtime":
        dropdown_meta = _try_dropdown_runtime_fallback(
            step_id=step_id,
            action_config=action_config,
            step_definition=step_definition,
            timeout_seconds=timeout_seconds,
        )
        if dropdown_meta is not None:
            result = dropdown_meta
        else:
            control_id = action_config.get("controlId", "") if isinstance(action_config, dict) else ""
            raise RuntimeError(
                "template fallback dropdown 未命中可见下拉项，拒绝把坐标点击当作成功: "
                "step={}, control={}".format(step_id, control_id)
            )
    else:
        raise RuntimeError(
            f"当前 action 暂不支持模板兜底执行: step={step_id}, action={action_name}, "
            f"template={fallback_template}"
        ) from original_error

    if original_error is None:
        _LOG_STEP(f"模板优先执行成功: step={step_id}, action={action_name}, template={fallback_template}")
    else:
        _LOG_STEP(f"模板兜底执行成功: step={step_id}, action={action_name}, template={fallback_template}")
    context.setdefault("step_outputs", {}).setdefault(step_id, {})[save_as] = result
    context["step_outputs"][step_id]["output"] = result

    if wait_after > 0:
        time.sleep(wait_after)
    return True


def _as_bool_flag(value):
    """宽松布尔解析：兼容布尔 / 数字 / 常见字符串表示。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def try_run_action_step_template_first(step_id, context):
    """模板优先识别（preferTemplate）：命中并执行成功返回 True；未命中/不支持返回 False。

    供 execute_step_by_id 在进入主流程 UIA 定位前调用；返回 False 时由主流程接管，
    保证勾选“优先模板”不会因模板未命中而让原本可用的步骤失败。
    """
    step_definition = _GET_STEP_DEFINITION(step_id)
    action_config = step_definition.get("actionConfig", {})
    if not isinstance(action_config, dict):
        action_config = {}
    action_config = _RESOLVE_DYNAMIC_VALUE(action_config, step_id, context)
    fallback_template = resolve_fallback_template_path(action_config.get("fallbackTemplate", ""))
    if not fallback_template:
        return False
    try:
        hit = run_action_step_with_template_fallback(
            step_id, context, original_error=None, on_miss="return_none"
        )
        return bool(hit)
    except Exception as exc:
        _LOG_STEP(
            f"模板优先未命中，回退主流程: step={step_id}, reason={exc}"
        )
        return False


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
    # 保留历史硬编码集合以兼容存量流程；流程定义显式标 setup=true 的步骤同样视为初始化步骤
    if step_id in {"launch_gm", "configure_projection", "open_source_dwg", "close_unknown_projection", "dwg_projection_confirm"}:
        return True
    try:
        return bool((_GET_STEP_DEFINITION(str(step_id or "")) or {}).get("setup"))
    except Exception:
        return False


# ── 投影选择后置校验 ──
# 用 MUP user.config 的投影历史/预置（mup_user_config.resolve_projection）
# 确认"选择投影"步骤所选投影是否在最近使用/预置中，结果写入 step_extra（软校验，
# 未命中不阻塞——首次使用新投影属正常，仅记 warning 供人工核对）。
_PROJECTION_SELECT_KEYWORDS = ("选择投影", "select projection", "choose projection")
_PROJECTION_NAME_KEYWORDS = (
    "Gauss-Kruger", "Gauss Kruger", "Gauss-Krüger", "CGCS2000", "CGCS 2000",
    "Web Mercator", "WGS 84", "WGS84", "UTM", "GEOGCS", "PROJCS", "Lambert",
    "Albers", "Mercator", "Beijing 1954", "Xian 1980", "China 2000", "投影",
)


def _get_projection_verify_target(step_definition):
    """从步骤定义提取投影后置校验的目标文本；非投影选择步骤返回 None。

    只对"选择投影"类步骤校验（如 step_10 点击-选择投影坐标系），
    "键入-查找投影坐标系"等搜索类步骤不校验，避免把搜索词误当投影名。
    目标文本优先级：
      1. actionConfig.value/text —— 动作实际选中的投影值（最准确，避免拿控件名校验）；
      2. inspectHints.controlName（完整投影名）；
      3. controls[0].name（兜底）。
    仅当来源不是动作值时，才用投影命名特征关键词把关，防止控件名不是投影本身。
    """
    if not isinstance(step_definition, dict):
        return None
    name = str(step_definition.get("name") or "").strip()
    low_name = name.lower()
    is_select = any(k in low_name for k in _PROJECTION_SELECT_KEYWORDS)
    if not is_select:
        return None
    action_config = step_definition.get("actionConfig") or {}
    if not isinstance(action_config, dict):
        action_config = {}
    target = str(action_config.get("value", "") or action_config.get("text", "") or "").strip()
    source = "value" if target else ""
    if not target:
        hints = step_definition.get("inspectHints") or {}
        target = str(hints.get("controlName") or "").strip()
        source = "controlName" if target else ""
    if not target:
        controls = step_definition.get("controls") or []
        if controls and isinstance(controls[0], dict):
            target = str(controls[0].get("name") or "").strip()
            source = "controlName" if target else ""
    if not target:
        return None
    # 动作值即所选投影值，无需关键词把关；控件名类来源才需确认含投影命名特征
    if source != "value":
        low_target = target.lower()
        if not any(k.lower() in low_target for k in _PROJECTION_NAME_KEYWORDS):
            return None
    return target


def _verify_projection_choice(target_text):
    """用 MUP user.config 的投影历史/预置校验投影是否已存在于历史。

    返回写入 step_extra 的 dict（命中记录名称/EPSG，未命中记 warning）。
    """
    try:
        from mup_user_config import resolve_projection, clear_cache as clear_projection_cache
        # 校验前清掉投影缓存：本次运行新选用的投影若已由 MUP 异步写入 user.config，
        # 不清缓存会命中旧历史，误报"首次使用"。
        try:
            clear_projection_cache()
        except Exception:
            pass
        matched = resolve_projection(target_text)
    except Exception as exc:
        # 解析异常不再静默：写日志并在 step_extra 留错误痕迹
        _LOG_STEP(f"投影后置校验解析失败: target={target_text}, error={exc}")
        return {"projectionVerifyError": str(exc)}
    if matched:
        return {
            "projectionVerified": True,
            "projectionName": matched.get("name", ""),
            "projectionEpsg": matched.get("epsg", ""),
        }
    return {
        "projectionWarning": "所选投影未在 MUP 历史/预置中出现，可能为首次使用或名称不一致",
        "projectionTarget": target_text,
    }


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

    # 防重复执行：同一步骤在同一轮运行中只执行一次。
    # 同一 child 被多个流程包引用、或同时是顶层步骤又在包内时，UI 动作会被重复触发
    # （二次点击/二次弹窗/报告重复记录）。重复引用记 skipped 并跳过。
    run_executed_ids = context.setdefault("_run_executed_step_ids", set())
    if step_id in run_executed_ids:
        _LOG_STEP(f"步骤已在本轮执行过，跳过重复执行: step={step_id}, name={step_name}")
        _REPORT_STEP_RESULT(
            context.get("run_report"),
            step_id,
            step_name,
            "skipped",
            action_type=str(step_definition.get("actionType", "script")).strip(),
            error="重复引用已跳过",
        )
        return
    run_executed_ids.add(step_id)

    action_type = str(step_definition.get("actionType", "script")).strip().lower() or "script"
    strategy = str(step_definition.get("strategy", "")).strip()
    step_status = "success"
    step_error = ""
    step_extra = {}
    try:
        if action_type == "script":
            func = item.get("func") if isinstance(item, dict) else None
            if func is None:
                # 未绑定执行函数说明流程配置有问题：按失败记录（显式 optional 的除外），
                # 避免"关键业务步骤缺失执行体却静默跳过、流程继续报成功"。
                if bool(step_definition.get("optional")):
                    _LOG_STEP(f"可选步骤未绑定执行函数，按跳过处理: {step_id}")
                    step_status = "skipped"
                    step_error = "步骤未绑定执行函数(optional)"
                    return
                _LOG_STEP(f"步骤未绑定执行函数，按失败处理: {step_id}")
                step_status = "failed"
                step_error = "步骤未绑定执行函数"
                return
            func(context)
            return
        if action_type == "action":
            action_config = step_definition.get("actionConfig", {})
            if not isinstance(action_config, dict):
                action_config = {}
            action_config = _resolve_step_policy(action_config)  # stepPolicy → 旧字段归一化（返回新 dict，不写穿缓存）
            on_error = str(action_config.get("onError", "")).strip().lower() or "stop"
            fallback_template = str(action_config.get("fallbackTemplate", "")).strip()
            if not fallback_template:
                # 自动接线：actionConfig 未显式配置时，从控件 templateKey 解析
                # （recorder 伴随拾取 / 采集器自动截图生成的模板）
                fallback_template = _resolve_template_key_from_controls(step_definition)
            prefer_template = _as_bool_flag(action_config.get("preferTemplate", False))
            template_preferred_tried = bool(prefer_template and fallback_template)
            try:
                if template_preferred_tried:
                    step_extra["templatePreferredAttempted"] = fallback_template
                    if try_run_action_step_template_first(step_id, context):
                        step_extra["templatePreferred"] = fallback_template
                        step_extra.update(_wait_for_continue_when(step_id, action_config, phase="template_preferred", step_definition=step_definition) or {})
                        return
                step_extra.update(_run_action_step_with_retry(step_id, context, action_config, step_extra) or {})
                # P1: 步骤执行成功后自动采集控件模板（best-effort，静默失败）：
                # 定位当前控件区域截图存入 auto_captured，后续该控件即可用模板兜底匹配。
                try:
                    _AUTO_CAPTURE_TEMPLATE(step_id, step_definition)
                except Exception:
                    pass
            except Exception as exc:
                if on_error == "fallback" and fallback_template:
                    fallback_exc = None
                    try:
                        run_action_step_with_template_fallback(step_id, context, exc)
                        step_extra["fallbackTemplateUsed"] = fallback_template
                        step_extra["fallbackReason"] = str(exc)
                        step_extra.update(_wait_for_continue_when(step_id, action_config, phase="template_fallback", step_definition=step_definition) or {})
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
                        # P1: 常规执行与模板兜底均失败时，也尝试自动采集控件模板存档，
                        # 供后续运行复用（best-effort，静默失败）。
                        try:
                            _AUTO_CAPTURE_TEMPLATE(step_id, step_definition)
                        except Exception:
                            pass
                    ai_extra = _maybe_run_ai_intervention_after_failure(
                        step_id,
                        context,
                        original_error=exc,
                        fallback_error=fallback_exc,
                    )
                    if ai_extra:
                        step_extra.update(ai_extra)
                        cont_extra = _wait_for_continue_when(step_id, action_config, phase="ai_intervention", step_definition=step_definition) or {}
                        step_extra.update(cont_extra)
                        # 防"试过即成功"：AI 介入后若无 continueWhen 校验结果，禁止记成功。
                        # （configured 但未满足的 continueWhen 已在 _wait_for_continue_when 内抛错）
                        if not cont_extra.get("continueWhenSatisfied"):
                            step_extra["aiInterventionUnverified"] = True
                            raise RuntimeError(
                                f"AI 介入结果未验证（步骤未配置 continueWhen）: step={step_id}"
                            )
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
                        cont_extra = _wait_for_continue_when(step_id, action_config, phase="ai_intervention", step_definition=step_definition) or {}
                        step_extra.update(cont_extra)
                        # 防"试过即成功"：AI 介入后无 continueWhen 校验结果时禁止记成功
                        if not cont_extra.get("continueWhenSatisfied"):
                            step_extra["aiInterventionUnverified"] = True
                            raise RuntimeError(
                                f"AI 介入结果未验证（步骤未配置 continueWhen）: step={step_id}"
                            )
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
        # 投影选择后置校验：仅对成功的选择投影步骤，用 MUP 投影历史/预置确认
        if step_status == "success":
            proj_target = _get_projection_verify_target(step_definition)
            if proj_target:
                proj_result = _verify_projection_choice(proj_target)
                if proj_result:
                    step_extra.update(proj_result)
                    if proj_result.get("projectionVerified"):
                        _LOG_STEP(
                            "投影后置校验通过: step={step_id}, projection={name} (EPSG {epsg})".format(
                                step_id=step_id,
                                name=proj_result.get("projectionName", ""),
                                epsg=proj_result.get("projectionEpsg", "?"),
                            )
                        )
                    else:
                        _LOG_STEP(
                            "投影后置校验警告: step={step_id}, target={target}（{msg}）".format(
                                step_id=step_id,
                                target=proj_target,
                                msg=proj_result.get("projectionWarning", ""),
                            )
                        )
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
        if step_extra.get("templatePreferred"):
            summary_parts.append(f"templatePreferred={step_extra.get('templatePreferred')}")
        elif step_extra.get("templatePreferredAttempted"):
            summary_parts.append("templatePreferredMissed")
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
