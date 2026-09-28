# encoding: utf-8
"""WT_AUTOMATION_Agent 控件库索引 —— 为 Agent 提供 LLM 可读的控件信息。

完全自包含，不依赖外部项目结构。
可手动注入控件信息，或从 JSON 文件加载。
"""
from __future__ import annotations

import json
import os
from typing import Any


# ---------------------------------------------------------------------------
# 从字典或 JSON 文件构建索引
# ---------------------------------------------------------------------------

def build_index_from_controls(controls: dict[str, dict[str, Any]]) -> str:
    """从控件字典生成 LLM 可读的索引文本。

    参数：
        controls: {control_id: {name, className, controlType, ...}}

    返回：
        纯文本控件索引
    """
    if not controls:
        return "（当前未定义控件库）"

    sorted_items = sorted(
        controls.items(),
        key=lambda item: (
            0 if item[1].get("name") else 1,
            item[0],
        ),
    )

    lines: list[str] = []
    for cid, info in sorted_items:
        parts = [f'  - control_id="{cid}"']
        if info.get("name"):
            parts.append(f'  名称="{info["name"]}"')
        if info.get("className"):
            parts.append(f"  类名={info['className']}")
        if info.get("controlType"):
            parts.append(f"  类型={info['controlType']}")
        if info.get("role"):
            parts.append(f"  角色={info['role']}")
        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def build_index_from_json(json_path: str) -> str:
    """从 JSON 文件加载控件并生成索引。

    支持的 JSON 格式（兼容 WT_Automation 的 flow_definition.json steps）：
        {"steps": [{"controls": [{"id": "...", "name": "...", ...}], ...}]}
    或直接：
        [{"id": "...", "name": "...", ...}]
    """
    if not json_path or not os.path.exists(json_path):
        return "（控件文件不存在）"

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return "（控件文件解析失败）"

    controls: dict[str, dict] = {}

    # 从 steps 数组提取
    if isinstance(data, dict):
        for step in data.get("steps", []):
            if not isinstance(step, dict):
                continue
            for ctrl in step.get("controls", []):
                if not isinstance(ctrl, dict):
                    continue
                cid = str(ctrl.get("id", "")).strip()
                if cid and cid not in controls:
                    controls[cid] = {
                        "id": cid,
                        "name": str(ctrl.get("name", "")).strip(),
                        "className": str(ctrl.get("className", "")).strip(),
                        "controlType": str(ctrl.get("controlType", "")).strip(),
                        "role": str(ctrl.get("role", "")).strip(),
                    }
            # 从 inspectHints 补
            hints = step.get("inspectHints", {})
            if isinstance(hints, dict) and hints.get("automationId"):
                aid = hints["automationId"]
                if aid not in controls:
                    controls[aid] = {
                        "id": aid,
                        "name": str(hints.get("controlName", "")).strip(),
                        "className": str(hints.get("className", "")).strip(),
                        "controlType": str(hints.get("controlType", "")).strip(),
                    }

    # 直接是列表
    elif isinstance(data, list):
        for ctrl in data:
            if not isinstance(ctrl, dict):
                continue
            cid = str(ctrl.get("id", "")).strip()
            if cid:
                controls[cid] = {
                    "id": cid,
                    "name": str(ctrl.get("name", "")).strip(),
                    "className": str(ctrl.get("className", "")).strip(),
                    "controlType": str(ctrl.get("controlType", "")).strip(),
                }

    return build_index_from_controls(controls)


# ---------------------------------------------------------------------------
# 手动构建常用控件（内置常见 UI 控件模式）
# ---------------------------------------------------------------------------

BUILTIN_CONTROL_TEMPLATES: dict[str, dict[str, str]] = {
    # 可以预置一些常见的控件模式
    "button_confirm": {
        "name": "确认按钮",
        "className": "Button",
        "controlType": "Button",
        "role": "确认当前操作",
    },
    "button_cancel": {
        "name": "取消按钮",
        "className": "Button",
        "controlType": "Button",
        "role": "取消当前操作",
    },
    "input_text_field": {
        "name": "文本输入框",
        "className": "Edit",
        "controlType": "Edit",
        "role": "文本输入区域",
    },
    "dropdown_list": {
        "name": "下拉列表",
        "className": "ComboBox",
        "controlType": "ComboBox",
        "role": "选择选项",
    },
    "checkbox_option": {
        "name": "复选框",
        "className": "CheckBox",
        "controlType": "CheckBox",
        "role": "开关选项",
    },
    "radio_option": {
        "name": "单选按钮",
        "className": "RadioButton",
        "controlType": "RadioButton",
        "role": "单选选项",
    },
    "tab_page": {
        "name": "选项卡",
        "className": "TabItem",
        "controlType": "TabItem",
        "role": "切换标签页",
    },
    "list_item": {
        "name": "列表项",
        "className": "ListBoxItem",
        "controlType": "ListBoxItem",
        "role": "选择列表中的一项",
    },
}


def build_builtin_index() -> str:
    """生成内置控件模板的索引。"""
    return build_index_from_controls(BUILTIN_CONTROL_TEMPLATES)


# ---------------------------------------------------------------------------
# 快捷构建 DslContext
# ---------------------------------------------------------------------------

def build_context_for_agent(
    control_index_text: str = "",
    flow_path: str | None = None,
    step_names: list[str] | None = None,
    project_description: str = "",
    skill_text: str = "",
):
    """快捷构建 DslContext 并返回。"""
    from WT_AUTOMATION_Agent.agent import DslContext

    if not control_index_text and flow_path:
        control_index_text = build_index_from_json(flow_path)

    return DslContext(
        control_index_text=control_index_text,
        current_step_names=step_names or [],
        project_description=project_description,
        skill_context_text=skill_text,
    )
