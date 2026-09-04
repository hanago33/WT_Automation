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
_CHECK_ALL_TOGGLES = lambda *args, **kwargs: False
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
    check_all_toggles=None,
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
    global _CLICK_RELATIVE_ANCHOR, _CHECK_ALL_TOGGLES
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
    if callable(check_all_toggles):
        _CHECK_ALL_TOGGLES = check_all_toggles
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


def _is_internal_content_host_control(control_id, step_definition=None):
    """判断控件是否为 WPF 内部内容宿主（PART_ContentHost）。

    PART_ContentHost 是 TextBox 的内部编辑宿主（Pane/ScrollViewer），自身无
    ValuePattern，父链也常读不到 TextBox 值，无法用值断言校验。通过控件 id、
    targetValue 或 inspectData.automationId 中的 "PART_ContentHost" 识别。
    """
    if "PART_ContentHost" in str(control_id or ""):
        return True
    if not isinstance(step_definition, dict):
        return False
    action_config = step_definition.get("actionConfig", {})
    if isinstance(action_config, dict):
        if "PART_ContentHost" in str(action_config.get("controlId", "")):
            return True
    for control in step_definition.get("controls", []) or []:
        if not isinstance(control, dict):
            continue
        if str(control.get("id", "")).strip() == str(control_id or "").strip():
            if "PART_ContentHost" in str(control.get("targetValue", "")):
                return True
            inspect_data = control.get("inspectData", {})
            if isinstance(inspect_data, dict) and "PART_ContentHost" in str(inspect_data.get("automationId", "")):
                return True
    return False


def _normalize_locator_text(text):
    """定位文本归一化：小写 + 折叠空白，用于标记匹配（与 locator 的 normalize 对齐）。"""
    try:
        return " ".join(str(text or "").strip().lower().split())
    except Exception:
        return ""


def _is_unreadable_value_control(control_id, step_definition=None):
    """判断控件是否无法用 ValuePattern 可靠读取值，从而跳过自动值断言。

    自动 value_equals 断言的目的是"输入/选择动作生效后校验值"，但以下控件
    即使动作成功也读不到输入结果，断言必然假失败（拖慢流程、误报 failed）：
      - PART_DropDownButton：下拉框的展开按钮，无 ValuePattern
      - Text/TextBlock/全文检索：标签文本，读到的不是输入框的值
      - 无 label_text 消歧的 textbox：目标可能是多个同名输入框之一，读到的
        可能是错误控件或离屏控件
    """
    if not control_id:
        return False
    target_value = ""
    try:
        controls = step_definition.get("controls", []) if isinstance(step_definition, dict) else []
        for control in controls:
            if str(control.get("id", "")).strip() == control_id:
                target_value = " ".join([
                    str(control.get("targetValue", "") or ""),
                    str(control.get("targetMethod", "") or ""),
                ])
                inspect_data = control.get("inspectData", {})
                if isinstance(inspect_data, dict):
                    target_value += " " + " ".join([
                        str(inspect_data.get("automationId", "") or ""),
                        str(inspect_data.get("controlType", "") or ""),
                        str(inspect_data.get("className", "") or ""),
                    ])
                break
    except Exception:
        return False
    normalized = _normalize_locator_text(target_value)
    if not normalized:
        return False
    # 下拉展开按钮 / 文本标签 / 内部宿主：均读不到输入值
    # 注意：normalized 已全部小写化（见 _normalize_locator_text），markers 必须用小写
    # 匹配，否则豁免永远不生效 → 对 Text/下拉按钮等误加 value_equals 断言而假失败。
    unreadable_markers = [
        "part_dropdownbutton",
        "part_contenthost",
        "textblock",
        ",text,",
        "control_type,text",
        "controltype.text",
        "text,",
    ]
    for marker in unreadable_markers:
        if marker in normalized:
            return True
    # textbox 无 label_text 消歧：目标可能是多个同名输入框之一
    if "textbox" in normalized and "label" not in normalized and "label_text" not in normalized:
        return True
    return False


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
        # 自动值断言（autoAssert）仅对"能可靠读取值"的控件启用：
        #  - PART_ContentHost（WPF TextBox 内部宿主，无 ValuePattern）
        #  - PART_DropDownButton（下拉展开按钮，无 ValuePattern）
        #  - Text/TextBlock 标签（全文检索等 label，读到的不是输入值）
        #  - 无 label_text 消歧的裸 textbox（可能命中多个/离屏）
        # 这些控件读不到输入结果，value_equals 断言必然"假失败"，即使键入已成功。
        # 键入动作成功（type_text_into_wrapper 返回 True）即视为通过。
        if _is_internal_content_host_control(control_id, step_definition):
            return None
        if _is_unreadable_value_control(control_id, step_definition):
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


def _eval_precondition_skip(step_id, action_config, step_definition):
    """按 actionConfig.precondition 判断是否应跳过动作（控件已处于目标切换态）。

    仅当步骤显式配置 precondition 且 condition 为 toggle/checked 时生效；未配置的
    旧步骤完全不受影响。仅对实现 TogglePattern 的控件有效——非切换控件读不到
    toggleState，视为不满足条件、照常执行动作（旧行为不变）。

    语义：precondition.expected 描述"执行动作前控件应处的切换态"。当前态 == expected
    才需要执行动作（例如 uncheck 步骤 expected="on"：仅在已勾选时点击取消；check 步骤
    expected="off"：仅在未勾选时点击勾选）。当前态 != expected（已处于相反/无关态）则
    跳过，等价于"check only if unchecked / uncheck only if checked"。
    """
    pre = action_config.get("precondition") if isinstance(action_config, dict) else None
    if not isinstance(pre, dict):
        return None
    cond = str(pre.get("condition", "")).strip().lower()
    # 前置等待：轮询引用控件直到可见/存在（最长 timeoutSeconds），用于吸收界面
    # 刷新延迟（如切换分区后列表项尚未渲染）。超时一律不跳过、照常执行动作让
    # 其自然失败——只做"等"，不做"跳"，不影响旧语义。
    if cond in {"wait_visible", "wait_exists", "wait_present"}:
        control_id = str(
            pre.get("controlId", "") or action_config.get("controlId", "") or action_config.get("controlRef", "")
        ).strip()
        if not control_id:
            return None
        window_title_hint = str(pre.get("windowTitleHint", "") or action_config.get("windowTitleHint", "")).strip()
        timeout = float(str(pre.get("timeoutSeconds", "10")).strip() or "10")
        step_def = step_definition if isinstance(step_definition, dict) else {}
        step_arg = str(step_def.get("id", "")).strip() or str(step_id)
        deadline = time.time() + max(0.2, timeout)
        found = False
        while time.time() < deadline:
            try:
                candidate = _call_with_control_map_path(
                    _LOCATE_FLOW_CONTROL, step_def, step_arg,
                    control_id=control_id,
                    # 6.0s：fast 阶段原生 FindAll + 标签预过滤在候选多时实测需 ~4s
                    #（如风机配置分组下拉 30+ 候选 × 多窗口），预算过短会每轮
                    # 死在打分前，永远凑不齐候选项
                    timeout_seconds=min(6.0, max(0.2, deadline - time.time())),
                    window_title_hint=window_title_hint,
                )
            except Exception:
                candidate = None
            if candidate is not None:
                if cond == "wait_visible":
                    try:
                        from wt_flow_locator import get_wrapper_is_offscreen
                        off = str(get_wrapper_is_offscreen(candidate)).strip().lower()
                        if off in {"false", "0", ""}:
                            found = True
                            break
                    except Exception:
                        found = True
                        break
                else:
                    found = True
                    break
            time.sleep(0.4)
        if callable(_LOG_STEP):
            _LOG_STEP(
                "前置[wait_visible] step=%s: 引用控件 %s %s (timeout=%.1fs)"
                % (step_arg, control_id, "已就绪" if found else "等待超时，继续执行", timeout)
            )
        if not found:
            # 诊断：wait_visible/wait_exists 超时通常是"目标项根本没出现在 UIA 树里"。
            # 可通过 precondition.diagnosticAutomationId 指定要倾倒的容器
            # （默认 WRA 结果列表），用原生 FindAll 毫秒级取出其直接子项结构与名称，
            # 区分"容器为空 / 项未暴露 / 名称结构不同 / 选项集合未加载完"等情况。
            # 注意：任何真实 UIA 遍历都必须停在 GC 禁用区间内，否则 comtypes 对象
            # 在 __del__/Release 时可能触发 0xc0000374 堆损坏（见 find_flow_control 说明）。
            _diag_aid = str(pre.get("diagnosticAutomationId", "")).strip() or "WRAResults_ListBox_WRAResults"
            _gc_was_enabled = False
            try:
                import gc as _gc
                _gc_was_enabled = _gc.isenabled()
                if _gc_was_enabled:
                    _gc.disable()
                from wt_flow_locator import (
                    _try_get_window_by_handle,
                    get_foreground_window_handle,
                    _iter_uia_findall_by_automation_id,
                    get_wrapper_text,
                    get_wrapper_control_type,
                    get_wrapper_class_name,
                    get_wrapper_automation_id,
                )
                fg_window = _try_get_window_by_handle(get_foreground_window_handle())
                if fg_window is not None:
                    list_hits = list(
                        _iter_uia_findall_by_automation_id(fg_window, _diag_aid)
                    )
                    if list_hits:
                        list_wrapper = list_hits[0]
                        tile_lines = []
                        try:
                            children = list(list_wrapper.children())
                        except Exception:
                            children = []
                        for tile in children[:8]:
                            tile_lines.append(
                                "name={}, type={}, class={}, aid={}".format(
                                    repr(get_wrapper_text(tile) or ""),
                                    get_wrapper_control_type(tile),
                                    get_wrapper_class_name(tile),
                                    get_wrapper_automation_id(tile),
                                )
                            )
                            try:
                                for sub in list(tile.children())[:4]:
                                    tile_lines.append(
                                        "  子: name={}, type={}, aid={}".format(
                                            repr(get_wrapper_text(sub) or ""),
                                            get_wrapper_control_type(sub),
                                            get_wrapper_automation_id(sub),
                                        )
                                    )
                            except Exception:
                                pass
                        _LOG_STEP(
                            "前置[wait_visible] 诊断: 容器 {} 子项=%d -> %s".format(_diag_aid)
                            % (len(children), " | ".join(tile_lines[:28])[:1400])
                        )
                        # 显式释放 UIA wrapper 引用，避免跨调用延迟回收；
                        # 防御：异常路径下个别变量可能未定义（如首项 children 抛错）
                        try:
                            del list_hits, list_wrapper, children, tile_lines, tile, sub
                        except Exception:
                            pass
                    else:
                        _LOG_STEP(
                            "前置[wait_visible] 诊断: 前台窗口未找到容器 automationId=%s" % _diag_aid
                        )
                    # 倾倒目标进程的全部顶层窗口（标题/类名/句柄）：
                    # 定位失败常因"多出一个窗口"（复制后弹窗/独立编辑器窗口等），
                    # 列出候选窗口即可识别应用当前所处状态。
                    try:
                        from wt_flow_locator import _collect_dropdown_windows
                        _win_lines = []
                        for _w in _collect_dropdown_windows()[:8]:
                            _win_lines.append(
                                "title={!r}, class={}, hwnd={}".format(
                                    get_wrapper_text(_w) or "",
                                    get_wrapper_class_name(_w),
                                    hex(int(get_wrapper_handle(_w) or 0)),
                                )
                            )
                        if _win_lines:
                            _LOG_STEP("前置[wait_visible] 诊断: 候选窗口 -> " + " | ".join(_win_lines))
                    except Exception:
                        pass
                        del list_hits
            except Exception as _diag_exc:
                if callable(_LOG_STEP):
                    _LOG_STEP("前置[wait_visible] 诊断失败(忽略): %s" % (_diag_exc,))
            finally:
                if _gc_was_enabled:
                    _gc.enable()
        return None
    # 可见性前置条件：用于"仅支持 Invoke、不暴露 ToggleState"的分区瓦片
    # (如 MTDTileView_Button_ToggleState)。通过引用控件的屏幕可见性(IsOffscreen)
    # 判断分区是否已展开/激活：
    #   expected="off" → 仅当引用控件不可见(折叠/未激活)时执行动作(点击以展开/激活)，可见则跳过；
    #   expected="on"  → 仅当引用控件可见时执行动作，不可见则跳过。
    # 控件定位失败一律视为不可见 → 执行动作(点击以激活)，保证不漏操作。
    if cond in {"visible", "invisible", "offscreen", "is_visible"}:
        expected = str(pre.get("expected", "")).strip().lower()
        if expected not in {"on", "off", "1", "0"}:
            expected = "off"  # 默认：仅在不可见时执行(点击激活/展开)
        control_id = str(
            pre.get("controlId", "") or action_config.get("controlId", "") or action_config.get("controlRef", "")
        ).strip()
        if not control_id:
            controls = step_definition.get("controls", []) if isinstance(step_definition, dict) else []
            for c in controls:
                if isinstance(c, dict) and str(c.get("id", "")).strip():
                    control_id = str(c.get("id", "")).strip()
                    break
        if not control_id:
            return None
        window_title_hint = str(pre.get("windowTitleHint", "") or action_config.get("windowTitleHint", "")).strip()
        timeout = float(str(pre.get("timeoutSeconds", "1.5")).strip() or "1.5")
        step_def = step_definition if isinstance(step_definition, dict) else {}
        step_arg = str(step_def.get("id", "")).strip() or str(step_id)
        try:
            control = _call_with_control_map_path(
                _LOCATE_FLOW_CONTROL, step_def, step_arg,
                control_id=control_id,
                timeout_seconds=min(1.0, timeout),
                window_title_hint=window_title_hint,
            )
        except Exception:
            control = None
        if control is None:
            visible_now = False  # 定位不到 → 视为不可见 → 执行动作
            if callable(_LOG_STEP):
                _LOG_STEP("前置[visible] step=%s: 引用控件 %s 未定位(分区折叠/未激活) → 执行动作(点击展开)" % (step_arg, control_id))
        else:
            try:
                from wt_flow_locator import get_wrapper_is_offscreen
                off = str(get_wrapper_is_offscreen(control)).strip().lower()
                visible_now = off in {"false", "0", ""}
            except Exception:
                visible_now = True  # 读取异常→保守按可见(跳过)，避免误折叠
        if expected in {"off", "0"}:
            if visible_now:
                return "precondition 未满足(目标可见态=off)：引用控件已可见(分区已激活)，跳过执行"
            if control is not None and callable(_LOG_STEP):
                _LOG_STEP("前置[visible] step=%s: 引用控件 %s 不可见(分区折叠) → 执行点击以展开/激活" % (step_arg, control_id))
            return None  # 不可见 → 执行点击以激活/展开
        else:
            if not visible_now:
                return "precondition 未满足(目标可见态=on)：引用控件不可见，跳过执行"
            return None
    if cond not in {"toggle", "checked", "toggle_state"}:
        return None
    expected = str(pre.get("expected", "")).strip().lower()
    if expected not in {"on", "off", "1", "0", "indeterminate", "2"}:
        return None
    # 解析目标控件：优先 precondition.controlId，其次动作控件，再次步骤首个控件。
    control_id = str(
        pre.get("controlId", "") or action_config.get("controlId", "") or action_config.get("controlRef", "")
    ).strip()
    if not control_id:
        controls = step_definition.get("controls", []) if isinstance(step_definition, dict) else []
        for c in controls:
            if isinstance(c, dict) and str(c.get("id", "")).strip():
                control_id = str(c.get("id", "")).strip()
                break
    if not control_id:
        return None
    window_title_hint = str(pre.get("windowTitleHint", "") or action_config.get("windowTitleHint", "")).strip()
    timeout = float(str(pre.get("timeoutSeconds", "1.5")).strip() or "1.5")
    step_def = step_definition if isinstance(step_definition, dict) else {}
    step_arg = str(step_def.get("id", "")).strip() or str(step_id)
    # 1) 控件必须存在；不存在则不跳过（保留旧行为，让动作自然失败/报错）。
    try:
        control = _call_with_control_map_path(
            _LOCATE_FLOW_CONTROL, step_def, step_arg,
            control_id=control_id,
            timeout_seconds=min(1.0, timeout),
            window_title_hint=window_title_hint,
        )
    except Exception:
        control = None
    if control is None:
        return None
    # [fix] 失焦时 WPF 分区瓦片的 CurrentToggleState 可能读到陈旧值(折叠却读成展开)，
    # 读取前先激活所属主窗口，强制 UIA 缓存刷新后再读，规避误 skip。
    try:
        _w = getattr(control, "window", None)
        if callable(_w):
            _win = _w()
            if _win is not None:
                _win.set_focus()
    except Exception:
        pass
    # 2) 直接读取切换态原始值，区分"确定态"与"不可读"。
    try:
        from wt_flow_locator import get_wrapper_toggle_state
        raw_state = str(get_wrapper_toggle_state(control) or "")
    except Exception:
        raw_state = ""
    if callable(_LOG_STEP):
        _LOG_STEP("前置[toggle] step=%s: 控件 %s ToggleState=%s (expected=%s)" % (step_id, control_id, raw_state, expected))
    on_set = {"1", "on"}
    off_set = {"0", "off"}
    ind_set = {"2", "indeterminate"}
    exp_norm = expected  # expected 已归一为 on/off/indeterminate
    if raw_state not in on_set | off_set | ind_set:
        # 状态不可读：按确定性默认态执行动作（click），避免漏勾选/漏取消。
        # 默认态下 check 步骤(期望 off)点击=勾选、uncheck 步骤(期望 on)点击=取消，
        # 均得到正确结果；若后续能确保控件可读（如先展开所在面板），则走下方精确判定。
        _LOG_STEP(
            f"前置条件状态不可读，按默认态执行动作: step={step_id}, control={control_id}, expected={expected}"
        )
        return None
    # 3) 状态可读：仅在当前态 == expected（动作执行前应有的态）时执行动作；否则跳过。
    if (raw_state in on_set and exp_norm in on_set) \
            or (raw_state in off_set and exp_norm in off_set) \
            or (raw_state in ind_set and exp_norm in ind_set):
        return None  # 当前已是 expected 态，需执行动作
    return "precondition 未满足(目标切换态={})，控件已处于相反/无关状态，跳过执行".format(expected)


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
    if action_name == "click":
        pyautogui.click(center[0], center[1])
    elif action_name == "wait_for_control":
        # 等待语义不应产生点击：坐标兜底中"到达该坐标即视为满足"，避免误触按钮
        # 进入无关状态（BUG-5）。
        _LOG_STEP("coordinate fallback: wait_for_control 仅做存在性确认，不点击")
        return
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
        anchor_align = str(action_config.get("anchorAlign", "")).strip() or "center"
        ok, anchor_meta = _call_with_control_map_path(
            _CLICK_RELATIVE_ANCHOR, step_definition,
            step_id,
            control_id,
            (offset_x, offset_y),
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
            click_kind=click_kind,
            anchor_align=anchor_align,
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
        focus_first = str(action_config.get("focusFirst", "")).strip().lower()
        if focus_first not in {"false", "0", "no"} and control_id:
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
        # 守护线程 + 看门狗：wait_for_flow_control_condition 内部单次 UIA COM 调用可能在
        # "应用忙"时阻塞不返回（窗口响应探测有盲区），看门狗超时强制失败让流程继续。
        # 与 wt_flow_executor 同款修复。
        _wait_box = {"value": False, "error": ""}
        _wait_done = threading.Event()

        def _do_wait_call():
            try:
                _ok = _call_with_control_map_path(
                    _WAIT_FOR_FLOW_CONTROL_CONDITION, step_definition,
                    step_id,
                    control_id=control_id,
                    condition=condition,
                    timeout_seconds=timeout_seconds,
                    window_title_hint=window_title_hint,
                )
                _wait_box["value"] = bool(_ok)
            except Exception as _wexc:
                _wait_box["error"] = repr(_wexc)
            finally:
                _wait_done.set()

        _wait_worker = threading.Thread(target=_do_wait_call, daemon=True)
        _wait_worker.start()
        _wait_budget = float(timeout_seconds or 0)
        _watchdog_secs = max(30.0, _wait_budget + 60.0)
        if not _wait_done.wait(_watchdog_secs):
            _LOG_STEP(
                f"wait_for_control 看门狗超时({_watchdog_secs:.0f}s)，强制失败: "
                f"step={step_id}, control={control_id}, condition={condition}"
            )
            raise RuntimeError(
                f"action wait_for_control 看门狗超时: step={step_id}, control={control_id}, condition={condition}"
            )
        if _wait_box.get("error"):
            raise RuntimeError(
                f"action wait_for_control 执行异常: step={step_id}, control={control_id}, "
                f"condition={condition}, error={_wait_box['error']}"
            )
        if not _wait_box["value"]:
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
    elif action_name == "check_all_toggles":
        if not control_id:
            raise ValueError(f"action check_all_toggles 缺少 controlId: {step_id}")
        summary = _call_with_control_map_path(
            _CHECK_ALL_TOGGLES,
            step_definition,
            step_id,
            control_id,
            timeout_seconds=timeout_seconds,
            window_title_hint=window_title_hint,
        )
        if not summary:
            raise RuntimeError(
                f"action check_all_toggles 未命中: step={step_id}, control={control_id}"
            )
        _LOG_STEP(
            "已执行 check_all_toggles: step={step}, control={control}, result={result}".format(
                step=step_id, control=control_id, result=summary
            )
        )
        result = summary
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
            # 前置条件跳过：仅对显式配置 precondition 的步骤生效（如按需 check/uncheck
            # 勾选框），未配置的步骤完全不受影响；仅对实现 TogglePattern 的控件有效。
            _pre_skip = _eval_precondition_skip(step_id, action_config, step_definition)
            if _pre_skip:
                _LOG_STEP(f"前置条件满足，跳过动作执行: step={step_id}, reason={_pre_skip}")
                step_status = "skipped"
                step_error = _pre_skip
                return
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
