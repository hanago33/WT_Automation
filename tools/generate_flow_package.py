#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
generate_flow_package.py
从 operation_blueprints/*.json (合规操作流程蓝图) + control_maps/standard_control_catalog.json
(标准控件库) 自动生成可运行的流程包 flow_definition_<id>.json。

设计目标（对齐 WT_Automation 论文与现有 flow_definition 格式）：
- 步骤顺序 100% 来自手册蓝图，省去人工排序。
- 控件标识优先用标准库里的 automation_id（高权威），否则输出相对区域/模板占位的
  PENDING_CAPTURE 步骤，由用户后续在软件实时抓取后回填。
- 输出格式与现有 flow_definition_新建风机类型.json 完全兼容（flowPackages + steps）。

用法:
  python tools/generate_flow_package.py [blueprint_id ...] [--all] [--catalog <path>] [--out <dir>]
若不指定 blueprint_id 也不加 --all，则生成全部在 operation_blueprints/ 下的蓝图。
"""
import os
import sys
import json
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BLUEPRINT_DIR = os.path.join(ROOT, "operation_blueprints")
CATALOG_PATH = os.path.join(ROOT, "control_maps", "standard", "standard_control_catalog.json")
OUT_DIR = os.path.join(ROOT, "flow_packages")

# ---------------------------------------------------------------------------
# 蓝图中文业务名 -> 标准库 automation_id 的匹配词表
# 来源: control_maps/standard_control_catalog.json 中已验证的高权威控件。
# 仅收录有把握的匹配；其余一律输出 PENDING_CAPTURE 占位步骤。
# ---------------------------------------------------------------------------
SYNONYMS = {
    "turbine_type_create_and_curves": {
        "进入风机类型管理页按钮": "MUPMicroscaleInformationViewModel_Button_WindTurbineType",
        "创建新的风机类型按钮": "WTTypeExplorer_Button_New",
        "添加功率曲线": "WTTypePerformanceCurveVersionManagerEditView_Button_AddNew",
        # 推力系数曲线与噪声曲线可能共用版本管理器，待实时抓取确认
        "推力系数曲线文件": "WTTypeNoisesCurveVersionManagerEditView_Button_AddNew",
    },
    "microscale_create_model": {
        "进入微尺度建模按钮": "MainPage_Button_Microscale",
    },
    "geo_import_data_file": {
        # 地理信息数据按钮用于进入地理数据管理工具窗口
        "进入管理工具图标": "MUPMicroscaleInformationViewModel_Button_GeographicalData",
    },
}

# 对匹配存在不确定性的控件，附加说明
SYNONYM_NOTES = {
    "推力系数曲线文件": "需核实：推力系数曲线与噪声曲线可能共用版本管理器(NoisesCurve)，请实时抓取确认。",
    "进入管理工具图标": "候选匹配为地理信息数据入口按钮，需确认是否为管理工具窗口入口。",
}


def load_catalog(path):
    if not os.path.isfile(path):
        return {}
    data = json.load(open(path, encoding="utf-8"))
    by_id = {}
    for g in data.get("groups", []):
        for c in g.get("controls", []):
            tv = c.get("targetValue", "")
            aid = tv.split(",")[0] if tv else ""
            if aid:
                by_id.setdefault(aid, c)
    return by_id


def sanitize(name):
    keep = []
    for ch in name:
        if ch.isalnum() or ch in ("_", "-"):
            keep.append(ch)
        else:
            keep.append("_")
    return "".join(keep).strip("_")


def build_control_from_catalog(aid, cat_ctrl, window_title):
    """用标准库条目构造 controls[] 中的控件对象（含定位信息）。"""
    tv = cat_ctrl.get("targetValue", "")
    method = cat_ctrl.get("targetMethod", "automation_id")
    name = cat_ctrl.get("name", aid)
    ctrl_type = cat_ctrl.get("controlType", "")
    cls = cat_ctrl.get("className", "")
    fw = cat_ctrl.get("frameworkId", "WPF")
    safe = sanitize(name) or "ctrl"
    cid = f"{aid}_{safe}_{ctrl_type}" if ctrl_type else f"{aid}_{safe}"
    aux = [f"FrameworkId={fw}"]
    if cls:
        aux.append(f"ClassName={cls}")
    if ctrl_type:
        aux.append(f"ControlType={ctrl_type}")
    return {
        "id": cid,
        "name": name,
        "role": "来自标准控件库：",
        "enabled": True,
        "windowTitle": window_title,
        "targetMethod": method,
        "targetValue": tv,
        "templateKey": "",
        "uiPath": "",
        "notes": "由标准控件库合并生成，定位权威度高，可直接用于结构化定位；运行前请验证。",
        "rawInspectText": "",
        "auxChecks": aux,
        "inspectData": {
            "howFound": "standard_catalog",
            "name": "",
            "controlType": ctrl_type,
            "localizedControlType": "",
            "boundingRectangle": "",
            "isEnabled": "True",
            "isOffscreen": "False",
            "isKeyboardFocusable": "",
            "hasKeyboardFocus": "",
            "processId": "",
            "runtimeId": "",
            "frameworkId": fw,
            "className": cls,
            "automationId": aid,
            "nativeWindowHandle": "",
            "providerDescription": "",
            "legacyName": "",
            "legacyRole": "",
            "legacyState": "",
            "firstChild": "",
            "lastChild": "",
            "next": "",
            "previous": "",
            "children": [],
            "ancestors": [],
            "availablePatterns": [],
            "recommendedTargetMethod": method.split(",")[0],
            "recommendedTargetValue": aid,
        },
    }


def pending_control(step, idx):
    """为未匹配控件生成占位控件（需实时抓取/人工回填）。"""
    ctrl_type = {"click": "Button", "select": "ComboBox", "type_text": "TextBox",
                 "import": "Button", "check": "CheckBox", "navigate_tab": "Tab",
                 "drag": "Custom"}.get(step["action"], "Custom")
    cid = f"step_{idx}_control_pending"
    return {
        "id": cid,
        "name": step.get("control", "未知控件"),
        "role": "占位(待实时抓取)",
        "enabled": True,
        "windowTitle": step.get("window", ""),
        "targetMethod": "",
        "targetValue": "PENDING_CAPTURE",
        "templateKey": "",
        "uiPath": "",
        "notes": "PENDING_CAPTURE: 标准库无匹配标识，需在软件中实时抓取 automation_id/相对区域/模板。",
        "rawInspectText": "",
        "auxChecks": [],
        "inspectData": {
            "howFound": "pending", "name": "", "controlType": ctrl_type,
            "automationId": "", "frameworkId": "WPF",
            "recommendedTargetMethod": "", "recommendedTargetValue": "",
        },
    }


def build_step(blueprint_id, step, idx, cat_map):
    seq = step.get("seq", idx)
    action = step.get("action", "click")
    window = step.get("window", "")
    control = step.get("control", "")
    value = step.get("value", "")
    intent = step.get("intent", "")
    manual = step.get("manualRef", "")

    syn = SYNONYMS.get(blueprint_id, {})
    aid = syn.get(control)
    matched = aid and aid in cat_map
    note_extra = SYNONYM_NOTES.get(control, "")

    step_id = f"step_{idx}"
    step_name_map = {
        "click": "点击", "type_text": "键入", "select": "选择",
        "import": "导入", "check": "勾选", "navigate_tab": "切换标签",
        "drag": "拖拽",
    }
    prefix = step_name_map.get(action, "操作")
    name = f"{prefix}-{control or intent or seq}"

    inspect_hints = {"controlName": "", "className": "", "automationId": "",
                     "controlType": "", "uiPath": "", "templateKey": ""}
    controls = []
    action_config = {}
    notes = []
    stage = "converted" if matched else ""

    if matched:
        cat_ctrl = cat_map[aid]
        ctrl_obj = build_control_from_catalog(aid, cat_ctrl, window)
        controls.append(ctrl_obj)
        inspect_hints.update({
            "controlName": cat_ctrl.get("name", ""),
            "className": cat_ctrl.get("className", ""),
            "automationId": aid,
            "controlType": cat_ctrl.get("controlType", ""),
        })
        if action in ("click", "navigate_tab", "check"):
            action_config = {
                "action": "click",
                "controlId": ctrl_obj["id"],
                "timeoutSeconds": 3.0, "waitBefore": 0.3, "waitAfter": 0.3,
                "retryCount": 0, "retryInterval": 1.0,
                "onError": "continue",
            }
        elif action == "import":
            action_config = {
                "action": "click",
                "controlId": ctrl_obj["id"],
                "timeoutSeconds": 3.0, "waitBefore": 0.3, "waitAfter": 0.5,
                "retryCount": 1, "retryInterval": 1.0,
                "onError": "fallback", "fallbackMode": "file_dialog",
                "fileDialog": {"title": "打开", "fileName": value or "${runtime.curveFile}"},
            }
        notes.append(f"已用标准库 automation_id 匹配: {aid}。{note_extra}")
    else:
        # 未匹配 -> 占位步骤
        ctrl_obj = pending_control(step, idx)
        controls.append(ctrl_obj)
        pw = {"title": window, "className": "Window", "frameworkId": "WPF"}
        if action == "type_text":
            action_config = {
                "action": "type_text_relative",
                "timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.15,
                "parentWindow": pw,
                "relativeRegion": {"x": 0.5, "y": 0.5, "width": 0.2, "height": 0.08, "anchor": "center"},
                "retryCount": 0, "retryInterval": 1.0, "onError": "continue",
                "text": value or "${runtime.inputValue}",
            }
        elif action == "select":
            action_config = {
                "action": "select_dropdown_item_runtime",
                "controlId": ctrl_obj["id"],
                "value": value or "${runtime.selectValue}",
                "timeoutSeconds": 3.0, "waitBefore": 1.0, "waitAfter": 1.0,
                "retryCount": 1, "retryInterval": 1.0,
                "onError": "fallback", "fallbackMode": "template_match",
                "fallbackTemplate": "PENDING_CAPTURE",
            }
        elif action == "import":
            action_config = {
                "action": "click_relative_region",
                "timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.5,
                "parentWindow": pw,
                "relativeRegion": {"x": 0.5, "y": 0.5, "width": 0.1, "height": 0.1, "anchor": "center"},
                "retryCount": 0, "retryInterval": 1.0, "onError": "continue",
            }
            notes.append("PENDING_CAPTURE: 导入文件需先点击导入图标，再处理系统文件选择对话框，"
                         "建议补充 file_dialog 步骤。")
        elif action == "drag":
            action_config = {
                "action": "click_relative_region",
                "timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.2,
                "parentWindow": pw,
                "relativeRegion": {"x": 0.4, "y": 0.4, "width": 0.2, "height": 0.2, "anchor": "center"},
                "retryCount": 0, "retryInterval": 1.0, "onError": "continue",
            }
            notes.append("PENDING_CAPTURE: 拖拽计算域矩形，需实现 drag 交互或两次点击定位对角点。")
        else:  # click / navigate_tab / check 未匹配
            action_config = {
                "action": "click_relative_region",
                "timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.3,
                "parentWindow": pw,
                "relativeRegion": {"x": 0.5, "y": 0.5, "width": 0.1, "height": 0.1, "anchor": "center"},
                "retryCount": 0, "retryInterval": 1.0, "onError": "continue",
            }
        notes.append(f"PENDING_CAPTURE: 控件『{control}』在标准库无匹配，需软件实时抓取后回填"
                     f" automation_id / 相对区域 / 模板。{note_extra}")

    return {
        "id": step_id,
        "name": name,
        "stage": stage,
        "strategy": "action",
        "actionType": "action",
        "topLevel": True,
        "enabled": True,
        "codeSymbol": "",
        "codeReference": "",
        "packageRef": "",
        "description": intent,
        "successLog": f"{prefix} {control}" if not matched else "",
        "windowTitle": window,
        "inspectHints": inspect_hints,
        "controls": controls,
        "stepParams": {},
        "actionConfig": action_config,
        "auxChecks": [],
        "fallbacks": [],
        "notes": "\n".join(notes),
    }


def generate(blueprint_path, cat_map):
    bp = json.load(open(blueprint_path, encoding="utf-8"))
    bid = bp["id"]
    steps_in = sorted(bp.get("steps", []), key=lambda s: s.get("seq", 0))
    steps_out = [build_step(bid, s, i + 1, cat_map) for i, s in enumerate(steps_in)]
    step_ids = [s["id"] for s in steps_out]
    pkg = {
        "version": "1.0",
        "project": "WT_Automation",
        "description": f"自动生成流程包（来自蓝图 {bid}）。{bp.get('summary','')}",
        "lastUpdated": datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "runtimeConfig": {
            "gmExe": "", "sourceFilePath": "", "outputDir": "", "projectionFilePath": "",
        },
        "flowPackages": [
            {
                "id": bid,
                "name": bp.get("name", bid),
                "description": bp.get("summary", ""),
                "sourceManual": bp.get("sourceManual", ""),
                "sourceSections": bp.get("sourceSections", ""),
                "reconstructedNote": bp.get("reconstructedNote", ""),
                "preconditions": bp.get("preconditions", []),
                "stepIds": step_ids,
            }
        ],
        "steps": steps_out,
    }
    return pkg


def main():
    args = sys.argv[1:]
    use_all = "--all" in args
    rest = [a for a in args if not a.startswith("--")]
    catalog_path = CATALOG_PATH
    out_dir = OUT_DIR
    if "--catalog" in rest:
        i = rest.index("--catalog"); catalog_path = rest[i + 1]; rest = rest[:i] + rest[i + 2:]
    if "--out" in rest:
        i = rest.index("--out"); out_dir = rest[i + 1]; rest = rest[:i] + rest[i + 2:]

    cat_map = load_catalog(catalog_path)
    print(f"[catalog] 加载标准控件 {len(cat_map)} 个")

    if use_all or not rest:
        targets = [f for f in os.listdir(BLUEPRINT_DIR) if f.endswith(".json")]
    else:
        targets = []
        for t in rest:
            if os.path.isfile(t):
                targets.append(t)
            else:
                targets.append(f"{t}.json" if not t.endswith(".json") else t)

    os.makedirs(out_dir, exist_ok=True)
    for fname in targets:
        bp_path = fname if os.path.isfile(fname) else os.path.join(BLUEPRINT_DIR, fname)
        if not os.path.isfile(bp_path):
            print(f"[skip] 蓝图不存在: {fname}")
            continue
        pkg = generate(bp_path, cat_map)
        bid = pkg["flowPackages"][0]["id"]
        out_name = f"flow_definition_{bid}.json"
        out_path = os.path.join(out_dir, out_name)
        json.dump(pkg, open(out_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        matched = sum(1 for s in pkg["steps"] if s["stage"] == "converted")
        print(f"[gen] {out_name}: {len(pkg['steps'])} 步, 已匹配 {matched}, 待抓取 {len(pkg['steps'])-matched}")


if __name__ == "__main__":
    main()
