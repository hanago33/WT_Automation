# encoding: utf-8

ALLOWED_CONTINUE_WHEN_CONDITIONS = ("exists", "present", "visible", "enabled", "gone")
ALLOWED_RELATIVE_REGION_ANCHORS = ("center", "left_center", "right_center")
ALLOWED_ON_ERROR_MODES = ("continue", "retry", "stop", "fallback")
ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS = ("WPF", "Win32", "uia", "WinForm")

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
