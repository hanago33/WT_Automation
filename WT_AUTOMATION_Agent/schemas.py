# encoding: utf-8
"""WT_AUTOMATION_Agent 自包含的 Action 元数据定义。

不依赖外部项目文件，内置完整的 action schema 和默认配置。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

# ---------------------------------------------------------------------------
# Action Schema（与 WT 项目 wt_action_schema.py 一致，但完全自包含）
# ---------------------------------------------------------------------------

ALLOWED_CONTINUE_WHEN_CONDITIONS = (
    "exists", "present", "visible", "enabled", "gone",
    "nonempty", "non_empty", "value_equals", "toggle", "checked", "toggle_state",
)
ALLOWED_RELATIVE_REGION_ANCHORS = ("center", "left_center", "right_center")
ALLOWED_ON_ERROR_MODES = ("continue", "retry", "stop", "fallback", "ask")
ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS = ("WPF", "Win32", "uia", "WinForm")

ACTION_SCHEMAS: dict[str, dict[str, Any]] = {
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
        "description": "先定位父窗口，再按相对区域点击并输入文本，适合 WPF 弹窗内拿不到真实输入框的场景。",
        "target_required": False,
        "input_required": True,
        "input_key": "text",
        "input_label": "输入文本 *",
        "show_timeout": True,
    },
    "click_relative_region": {
        "label": "父窗口区域点击",
        "description": "先定位父窗口，再按相对区域点击，适合 WPF 弹窗内拿不到真实按钮的场景。",
        "target_required": False,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
    },
    "click_relative_anchor": {
        "label": "锚点相对点击",
        "description": "先定位锚点控件，再以其可见矩形中心为基准按像素偏移点击。",
        "target_required": True,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
    },
    "select_dropdown_item_runtime": {
        "label": "运行时下拉选择",
        "description": "先展开下拉框，再在运行时枚举 Popup 中的可见 ListBoxItem/MenuItem 并点击。",
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
    "drag_and_drop": {
        "label": "拖放",
        "description": "从一个控件拖拽到另一个控件。",
        "target_required": True,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
    },
    "double_right_click": {
        "label": "右键双击",
        "description": "对目标控件连续执行两次右键点击。",
        "target_required": True,
        "input_required": False,
        "input_key": "",
        "input_label": "输入/参数",
        "show_timeout": True,
    },
    "menu_select": {
        "label": "菜单选择",
        "description": "按路径依次点击菜单项，如 File->Open。",
        "target_required": False,
        "input_required": True,
        "input_key": "menuPath",
        "input_label": "菜单路径 *",
        "show_timeout": True,
    },
    "set_combobox": {
        "label": "设置下拉框",
        "description": "设置下拉框(ComboBox)的选中值。",
        "target_required": True,
        "input_required": True,
        "input_key": "value",
        "input_label": "选中值 *",
        "show_timeout": True,
    },
    "log": {
        "label": "日志",
        "description": "在日志中输出一条消息。",
        "target_required": False,
        "input_required": True,
        "input_key": "message",
        "input_label": "日志消息 *",
        "show_timeout": False,
    },
    "foreach_param": {
        "label": "参数扫描（循环）",
        "description": (
            "多参数扫描模式：从一个 Excel 参数表读取多行参数，"
            "每行参数驱动一组步骤模板执行一次。"
            "搭配 parameter_scan 模块使用，参数值通过 ${stepParams.xxx} 动态注入。"
        ),
        "target_required": False,
        "input_required": True,
        "input_key": "excelPath",
        "input_label": "参数 Excel 路径 *",
        "show_timeout": False,
    },
    "run_flow_package": {
        "label": "运行流程包",
        "description": (
            "引用并运行一个预定义的流程包（flow package），"
            "通过 flowRefParamStack 传递父步骤的 stepParams 给子流程。"
        ),
        "target_required": False,
        "input_required": True,
        "input_key": "flowPackageName",
        "input_label": "流程包名称 *",
        "show_timeout": False,
    },
}


# ---------------------------------------------------------------------------
# 默认配置
# ---------------------------------------------------------------------------

ACTION_DEFAULT_CONFIGS: dict[str, dict[str, float]] = {
    "click": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.12},
    "double_click": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.18},
    "right_click": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.18},
    "type_text": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.15},
    "type_text_relative": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.15},
    "click_relative_region": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.12},
    "click_relative_anchor": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.12},
    "select_dropdown_item_runtime": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.15},
    "send_keys": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.12},
    "drag_and_drop": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.2},
    "double_right_click": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.18},
    "menu_select": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.15},
    "set_combobox": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.15},
    "mouse_wheel": {"timeoutSeconds": 2.0, "waitBefore": 0.0, "waitAfter": 0.12},
    "wait_for_control": {"timeoutSeconds": 8.0, "waitBefore": 0.0, "waitAfter": 0.0},
    "sleep": {"timeoutSeconds": 0.0, "waitBefore": 0.0, "waitAfter": 0.0},
    "log": {"timeoutSeconds": 0.0, "waitBefore": 0.0, "waitAfter": 0.0},
    "foreach_param": {"timeoutSeconds": 0.0, "waitBefore": 0.2, "waitAfter": 0.1},
    "run_flow_package": {"timeoutSeconds": 0.0, "waitBefore": 0.3, "waitAfter": 0.3},
}


# ---------------------------------------------------------------------------
# 公共函数
# ---------------------------------------------------------------------------

def get_action_names() -> tuple[str, ...]:
    """返回所有支持的 action 名称。"""
    return tuple(ACTION_SCHEMAS.keys())


def get_action_schema(action_name: str) -> dict[str, Any]:
    """获取指定 action 的 schema 副本。"""
    normalized = str(action_name or "").strip() or "click"
    base = deepcopy(ACTION_SCHEMAS.get(normalized, ACTION_SCHEMAS["click"]))
    base["name"] = normalized
    return base


def build_action_default_config(action_name: str, **overrides: Any) -> dict[str, Any]:
    """用默认值构建 actionConfig，支持覆盖。"""
    normalized = str(action_name or "click").strip() or "click"
    config: dict[str, Any] = dict(ACTION_DEFAULT_CONFIGS.get(normalized, ACTION_DEFAULT_CONFIGS["click"]))
    config["action"] = normalized
    for key, val in overrides.items():
        if val is not None:
            config[key] = val
    return config


def build_action_schema_hint(action_name: str) -> str:
    """生成人可读的 schema 提示文本。"""
    schema = get_action_schema(action_name)
    requirements = []
    if schema.get("target_required"):
        requirements.append("目标控件")
    if schema.get("input_required"):
        requirements.append(schema.get("input_label", "输入参数").replace(" *", ""))
    req_text = "、".join(requirements) if requirements else "无额外必填"
    return f"{schema.get('label', action_name)}: {schema.get('description', '')} 必填: {req_text}."
