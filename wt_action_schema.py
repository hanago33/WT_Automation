# encoding: utf-8

ALLOWED_CONTINUE_WHEN_CONDITIONS = ("exists", "present", "visible", "enabled", "gone")
ALLOWED_RELATIVE_REGION_ANCHORS = ("center", "left_center", "right_center")
ALLOWED_ON_ERROR_MODES = ("continue", "retry", "stop", "fallback", "ask")
ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS = ("WPF", "Win32", "uia", "WinForm")

# 步骤级执行策略（stepPolicy）—— 统一 onError/retry/continueWhen 的收敛方式。
# 旧字段 onError / retryCount / retryInterval / continueWhen 仍完全支持，
# stepPolicy 作为可选新字段存在，优先级更高，运行时由 _resolve_step_policy 归一化。
STEP_POLICY_ON_FAIL_MODES = ("skip", "retry", "fallback", "abort", "ask")
_POLICY_ON_FAIL_TO_LEGACY = {
    "skip": "continue",
    "retry": "retry",
    "fallback": "fallback",
    "abort": "stop",
    "ask": "ask",
}
_LEGACY_TO_POLICY_ON_FAIL = {
    "continue": "skip",
    "retry": "retry",
    "stop": "abort",
    "fallback": "fallback",
    "ask": "ask",
}


def step_policy_on_fail_to_legacy(on_fail_value):
    """将 stepPolicy.onFail 枚举转为旧 onError 字段值。"""
    return _POLICY_ON_FAIL_TO_LEGACY.get(
        str(on_fail_value or "").strip().lower(), "stop"
    )


def step_policy_from_legacy_on_error(on_error_value):
    """从旧 onError 值反推 stepPolicy.onFail 枚举。"""
    return _LEGACY_TO_POLICY_ON_FAIL.get(
        str(on_error_value or "").strip().lower(), "abort"
    )

ACTION_SCHEMAS = {
    "click": {
        "label": "单击",
        "description": "对目标控件执行一次左键点击。",
        "target_required": True,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
    },
    "right_click": {
        "label": "右键",
        "description": "对目标控件执行右键点击。",
        "target_required": True,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
    },
    "double_click": {
        "label": "双击",
        "description": "对目标控件执行双击。",
        "target_required": True,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
    },
    "type_text": {
        "label": "输入文本",
        "description": "先聚焦目标控件，再向其中输入文本。",
        "target_required": True,
        "input_required": True,
        "input_key": "text",
        "input_label": "输入文本 *",
        "show_timeout": True,
    },
    "send_keys": {
        "label": "发送按键",
        "description": "向目标控件发送键盘内容或快捷键。",
        "target_required": True,
        "input_required": True,
        "input_key": "text",
        "input_label": "输入文本 *",
        "show_timeout": True,
    },
    "type_text_relative": {
        "label": "父窗口区域输入",
        "description": "先定位父窗口，再按相对区域点击并输入文本，适合 WPF 弹窗内拿不到真实输入框的场景；若界面需要失焦提交，可配 postInputKeys 为 {TAB} 或 {ENTER}。",
        "target_required": False,
        "input_required": True,
        "input_key": "text",
        "input_label": "输入文本 *",
        "show_timeout": True,
        "suggested_columns": ("inputText", "postInputKeys", "parentWindowTitle/className/frameworkId", "regionX/Y/Width/Height"),
    },
    "click_relative_region": {
        "label": "父窗口区域点击",
        "description": "先定位父窗口，再按相对区域点击，适合 WPF 弹窗内拿不到真实按钮或输入框的场景。",
        "target_required": False,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
        "suggested_columns": ("parentWindowTitle/className/frameworkId", "regionX/Y/Width/Height"),
    },
    "click_relative_anchor": {
        "label": "锚点相对点击",
        "description": "先定位锚点控件(controlId)，再以其可见矩形中心为基准、按像素偏移 (offsetX, offsetY) 点击。比固定区域更抗布局/缩放漂移，适合附近有稳定锚点控件、但目标点本身拿不到的情形。",
        "target_required": True,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
        "suggested_columns": ("controlId(锚点)", "offsetX", "offsetY", "clickKind"),
    },
    "select_dropdown_item_runtime": {
        "label": "运行时下拉选择",
        "description": "先展开下拉框，再在运行时枚举 Popup 中的可见 ListBoxItem/MenuItem 并点击，适合 WPF 下拉项。",
        "target_required": True,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
    },
    "wait_for_control": {
        "label": "等待控件",
        "description": "等待目标控件出现、可见或满足条件。",
        "target_required": True,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
    },
    "mouse_wheel": {
        "label": "滚轮",
        "description": "在目标控件上执行鼠标滚轮操作，正数向上，负数向下。",
        "target_required": True,
        "input_required": True,
        "input_key": "delta",
        "input_label": "滚轮值 *",
        "show_timeout": True,
    },
    "sleep": {
        "label": "等待",
        "description": "单纯等待指定秒数，不依赖控件。",
        "target_required": False,
        "input_required": True,
        "input_key": "seconds",
        "input_label": "等待秒数 *",
        "show_timeout": False,
    },
    "menu_select": {
        "label": "菜单选择",
        "description": "按路径依次点击菜单项，如 File->Open。解析自 recorder 的 menu_click。",
        "target_required": False,
        "input_required": True,
        "input_key": "menuPath",
        "input_label": "菜单路径 *",
        "show_timeout": True,
    },
    "set_combobox": {
        "label": "设置下拉框",
        "description": "设置下拉框(ComboBox)的选中值。解析自 recorder 的 set_combobox。",
        "target_required": True,
        "input_required": True,
        "input_key": "value",
        "input_label": "选中值 *",
        "show_timeout": True,
    },
}


def get_action_names():
    return tuple(ACTION_SCHEMAS.keys())


def get_action_schema(action_name):
    normalized_name = str(action_name or "").strip() or "click"
    base_schema = ACTION_SCHEMAS.get(normalized_name, ACTION_SCHEMAS["click"]).copy()
    base_schema["name"] = normalized_name
    return base_schema


def build_action_schema_hint(action_name):
    schema = get_action_schema(action_name)
    requirements = []
    if schema.get("target_required"):
        requirements.append("目标控件")
    if schema.get("input_required"):
        requirements.append(schema.get("input_label", "输入参数").replace(" *", ""))
    requirements_text = "、".join(requirements) if requirements else "无额外必填"
    suggested_columns = tuple(schema.get("suggested_columns", ()) or ())
    suggestion_text = f" 建议列: {'、'.join(suggested_columns)}。" if suggested_columns else ""
    return f"{schema.get('label', action_name)}: {schema.get('description', '')} 当前必填: {requirements_text}.{suggestion_text}"
