# encoding: utf-8
"""WT 自动化控件库索引 —— 为 DSL Agent 提供 LLM 可读的控件信息。【向后兼容包装器】

新代码请使用 WT_AUTOMATION_Agent.control_index 中的函数。

从 flow_definition.json（步骤内嵌 controls）和 control_maps/ 中的
标准控件库提取控件信息，生成人类可读的索引文本，供 DSL Agent 的
System Prompt 使用，帮助 LLM 理解有哪些可用控件及其用途。
"""

from __future__ import annotations

import json
import os
from typing import Any


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FLOW_PATH = os.path.join(BASE_DIR, "workspace", "flow_definition.json")
DEFAULT_CONTROL_MAP_PATH = os.path.join(BASE_DIR, "control_maps")
MASTER_CONTROL_FILE = os.path.join(DEFAULT_CONTROL_MAP_PATH, "standard", "总控件信息.json")
STANDARD_CATALOG_FILE = os.path.join(DEFAULT_CONTROL_MAP_PATH, "standard", "standard_control_catalog.json")


# ---------------------------------------------------------------------------
# 从 flow_definition.json 提取控件
# ---------------------------------------------------------------------------

def _load_json(file_path: str) -> dict | None:
    if not file_path or not os.path.exists(file_path):
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _collect_controls_from_flow(flow_path: str) -> dict[str, dict]:
    """从 flow_definition.json 的 steps[i].controls 收集控件。

    返回 {control_id: control_info, ...}
    """
    payload = _load_json(flow_path)
    if not isinstance(payload, dict):
        return {}

    controls: dict[str, dict] = {}
    for step in payload.get("steps", []):
        if not isinstance(step, dict):
            continue
        # 从 controls 数组收集
        for ctrl in step.get("controls", []):
            if not isinstance(ctrl, dict):
                continue
            cid = str(ctrl.get("id", "")).strip()
            if cid and cid not in controls:
                controls[cid] = {
                    "id": cid,
                    "name": str(ctrl.get("name", "")).strip(),
                    "role": str(ctrl.get("role", "")).strip(),
                    "targetMethod": str(ctrl.get("targetMethod", "")).strip(),
                    "targetValue": str(ctrl.get("targetValue", "")).strip(),
                    "windowTitle": str(ctrl.get("windowTitle", "")).strip(),
                    "source": f"step[{step.get('id', '?')}]",
                }
        # 从 inspectHints 收集
        hints = step.get("inspectHints", {})
        if isinstance(hints, dict) and hints.get("automationId"):
            cid = hints["automationId"]
            if cid not in controls:
                controls[cid] = {
                    "id": cid,
                    "name": str(hints.get("controlName", "")).strip(),
                    "role": "",
                    "targetMethod": "automation_id",
                    "targetValue": cid,
                    "className": str(hints.get("className", "")).strip(),
                    "controlType": str(hints.get("controlType", "")).strip(),
                    "source": f"inspectHints[step {step.get('id', '?')}]",
                }

    return controls


def _collect_controls_from_master(master_path: str) -> dict[str, dict]:
    """从 总控件信息.json 的 flatControls 收集控件。

    优先选取有 automationId 且 isEnabled=True 的控件。
    """
    payload = _load_json(master_path)
    if not isinstance(payload, dict):
        return {}

    controls: dict[str, dict] = {}
    flat = payload.get("flatControls", [])
    if not isinstance(flat, list):
        return {}

    for ctrl in flat:
        if not isinstance(ctrl, dict):
            continue
        aid = str(ctrl.get("automationId", "")).strip()
        if not aid:
            continue
        name = str(ctrl.get("name", "")).strip()
        # 跳过无意义的名称（纯图标、路径等）
        if not name or name.startswith("M") and len(name) > 20:
            name = ""
        controls[aid] = {
            "id": aid,
            "name": name,
            "className": str(ctrl.get("className", "")).strip(),
            "controlType": str(ctrl.get("controlType", "")).strip(),
            "frameworkId": str(ctrl.get("frameworkId", "")).strip(),
            "isEnabled": ctrl.get("isEnabled", False),
            "source": "master_control_map",
        }
    return controls


def _collect_controls_from_catalog(catalog_path: str) -> dict[str, dict]:
    """从 standard_control_catalog.json 的 groups schema 收集标准化控件信息。

    兼容旧版 {control_id: control_info} 扁平结构；新版按
    group.windowTitle/frameworkId 下钻 controls，并保留 targetMethod/targetValue。
    """
    payload = _load_json(catalog_path)
    if not isinstance(payload, dict):
        return {}

    groups = payload.get("groups")
    if not isinstance(groups, list):
        groups = [
            {
                "windowTitle": "",
                "frameworkId": "",
                "controls": [
                    dict(value, _catalogKey=key)
                    for key, value in payload.items()
                    if isinstance(value, dict)
                ],
            }
        ]

    controls: dict[str, dict] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_window = str(group.get("windowTitle", "")).strip()
        group_framework = str(group.get("frameworkId", "")).strip()
        group_controls = group.get("controls", [])
        if not isinstance(group_controls, list):
            group_controls = [group]
        for ctrl in group_controls:
            if not isinstance(ctrl, dict):
                continue
            target_value = str(ctrl.get("targetValue", "")).strip()
            automation_id = str(ctrl.get("automationId", "")).strip()
            legacy_key = str(ctrl.get("_catalogKey", "")).strip()
            cid = (
                target_value
                or automation_id
                or legacy_key
                or str(ctrl.get("id", "")).strip()
                or str(ctrl.get("name", "")).strip()
            )
            if not cid:
                continue
            if cid not in controls:
                controls[cid] = {
                    "id": cid,
                    "name": str(ctrl.get("name", "")).strip(),
                    "className": str(ctrl.get("className", "")).strip(),
                    "controlType": str(ctrl.get("controlType", "")).strip(),
                    "targetMethod": str(ctrl.get("targetMethod", "")).strip(),
                    "targetValue": target_value,
                    "automationId": automation_id,
                    "windowTitle": str(ctrl.get("windowTitle", "")).strip() or group_window,
                    "frameworkId": str(ctrl.get("frameworkId", "")).strip() or group_framework,
                    "authority": str(ctrl.get("authority", "")).strip(),
                    "source": "standard_catalog",
                }
    return controls


def build_control_index_text(
    flow_path: str | None = None,
    control_map_dir: str | None = None,
    max_controls: int = 200,
) -> str:
    """生成 LLM 可读的控件库索引文本。

    合并来自 flow_definition.json 的步骤内嵌控件、总控件信息、
    标准控件目录的控件，按相关性排序，去重。

    参数：
        flow_path: flow_definition.json 路径，默认取项目根目录
        control_map_dir: control_maps 目录路径
        max_controls: 返回的最大控件数量，避免 prompt 过长

    返回：
        纯文本控件索引
    """
    flow_path = flow_path or DEFAULT_FLOW_PATH
    control_map_dir = control_map_dir or DEFAULT_CONTROL_MAP_PATH

    # 收集三个来源的控件
    flow_controls = _collect_controls_from_flow(flow_path)
    master_file = os.path.join(control_map_dir, "standard", "总控件信息.json")
    master_controls = _collect_controls_from_master(master_file)
    catalog_file = os.path.join(control_map_dir, "standard", "standard_control_catalog.json")
    catalog_controls = _collect_controls_from_catalog(catalog_file)

    # 合并：flow 中的优先（精确关联步骤），master 次之，catalog 最后
    merged: dict[str, dict] = {}
    for src in (flow_controls, master_controls, catalog_controls):
        for cid, info in src.items():
            if cid not in merged:
                merged[cid] = info
            elif info.get("name") and not merged[cid].get("name"):
                merged[cid]["name"] = info["name"]

    if not merged:
        return "（当前工程未定义控件库）"

    # 排序：有名称的排前面，自动化流程中用到的排前面
    flow_ids = set(flow_controls.keys())
    sorted_items = sorted(
        merged.items(),
        key=lambda item: (
            0 if item[0] in flow_ids else 1,
            0 if item[1].get("name") else 1,
            item[0],
        ),
    )

    # 截取最大数量
    if len(sorted_items) > max_controls:
        sorted_items = sorted_items[:max_controls]

    # 构建文本
    lines = []
    for cid, info in sorted_items:
        parts = [f"  - control_id=\"{cid}\""]
        if info.get("name"):
            parts.append(f"  名称=\"{info['name']}\"")
        if info.get("className"):
            parts.append(f"  类名={info['className']}")
        if info.get("controlType"):
            parts.append(f"  类型={info['controlType']}")
        if info.get("targetMethod"):
            parts.append(f"  定位方式={info['targetMethod']}")
        if info.get("targetValue"):
            parts.append(f"  定位值={info['targetValue']}")
        if info.get("role"):
            parts.append(f"  角色={info['role']}")
        if info.get("windowTitle"):
            parts.append(f"  窗口={info['windowTitle']}")
        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def build_compact_control_index(
    flow_path: str | None = None,
) -> str:
    """生成紧凑格式的控件索引（适合 Prompt 中作为参考）。

    只列出 control_id 和名称，适合快速预览。
    """
    flow_path = flow_path or DEFAULT_FLOW_PATH

    flow_controls = _collect_controls_from_flow(flow_path)
    if not flow_controls:
        return "（当前工程未在 flow_definition 中定义控件）"

    lines = ["control_id → 名称"]
    lines.append("-" * 60)
    for cid in sorted(flow_controls.keys()):
        info = flow_controls[cid]
        name = info.get("name", "") or cid
        lines.append(f"  {cid} → {name}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 快捷构建 DslContext（供编辑器直接调用）
# ---------------------------------------------------------------------------

def build_dsl_context_for_agent(
    flow_path: str | None = None,
    control_map_dir: str | None = None,
):
    """构建 DslAgent 需要的 DslContext。"""
    from WT_AUTOMATION_Agent.agent import DslContext

    flow_path = flow_path or DEFAULT_FLOW_PATH

    control_text = build_control_index_text(flow_path, control_map_dir)

    # 收集已有步骤名称
    step_names: list[str] = []
    payload = _load_json(flow_path)
    if isinstance(payload, dict):
        for step in payload.get("steps", []):
            name = str(step.get("name", "")).strip() if isinstance(step, dict) else ""
            if name:
                step_names.append(name)

    return DslContext(
        control_index_text=control_text,
        current_step_names=step_names,
    )
