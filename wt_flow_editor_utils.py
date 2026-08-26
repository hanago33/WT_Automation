# encoding: utf-8

import re
import time


def _now_iso():
    """生成 ISO 8601 时间戳（本地时间，无时区后缀）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def build_source_info(origin="control_library", library_control_id="", library_file_name="", imported_by=""):
    """构建控件来源标记 sourceInfo。

    origin 取值：
      - control_library : 从控件库(单文件/library)导入
      - standard_catalog: 从标准控件库目录导入
      - anchor_library  : 锚点控件从控件库导入
      - recorder        : 录制器自动转换
      - manual          : 用户手写/新建
    后续「用户编辑」「源库删除」由 mark_source_edited / mark_source_deleted 联动标记。
    """
    return {
        "origin": str(origin or "control_library"),
        "libraryControlId": str(library_control_id or ""),
        "libraryFileName": str(library_file_name or ""),
        "importedBy": str(imported_by or ""),
        "importedAt": _now_iso(),
        "edited": False,
        "editedAt": "",
        "sourceDeleted": False,
        "sourceDeletedAt": "",
    }


def mark_source_edited(control, edited=True):
    """用户编辑控件后打上 edited 标记（保留原来源信息）。"""
    if not isinstance(control, dict):
        return control
    src = control.get("sourceInfo")
    if not isinstance(src, dict):
        src = build_source_info(origin=control.get("role", ""), library_control_id=control.get("id", ""))
        control["sourceInfo"] = src
    src["edited"] = bool(edited)
    if edited:
        src["editedAt"] = _now_iso()
    return control


def mark_source_deleted(control, deleted=True):
    """源库删除控件后，把流程步骤里引用该来源的控件打上 sourceDeleted 标记。"""
    if not isinstance(control, dict):
        return control
    src = control.get("sourceInfo")
    if not isinstance(src, dict):
        src = build_source_info(origin="control_library", library_control_id=control.get("id", ""))
        control["sourceInfo"] = src
    src["sourceDeleted"] = bool(deleted)
    if deleted:
        src["sourceDeletedAt"] = _now_iso()
    return control


def normalize_source_info(source_info):
    """规范化 sourceInfo 字段（缺失字段补默认值）。"""
    if not isinstance(source_info, dict):
        return {}
    return {
        "origin": str(source_info.get("origin", "")).strip(),
        "libraryControlId": str(source_info.get("libraryControlId", "")).strip(),
        "libraryFileName": str(source_info.get("libraryFileName", "")).strip(),
        "importedBy": str(source_info.get("importedBy", "")).strip(),
        "importedAt": str(source_info.get("importedAt", "")).strip(),
        "edited": bool(source_info.get("edited", False)),
        "editedAt": str(source_info.get("editedAt", "")).strip(),
        "sourceDeleted": bool(source_info.get("sourceDeleted", False)),
        "sourceDeletedAt": str(source_info.get("sourceDeletedAt", "")).strip(),
    }


def normalize_control_type_name(control_type, localized_control_type=""):
    control_type = str(control_type or "").strip()
    if control_type.startswith("UIA_") and "ControlTypeId" in control_type:
        control_type = control_type.replace("UIA_", "").replace("ControlTypeId", "").strip()
    matched = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\d+\)\s*$", control_type)
    if matched:
        control_type = matched.group(1)
    if control_type:
        return control_type
    if localized_control_type:
        return localized_control_type
    return ""


def strip_wrapping_quotes(text):
    value = str(text or "").strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1].strip()
    return value


def slugify_filename(text, fallback="window"):
    """将任意文本规范化为安全的文件名片段。

    共享实现：原先分散在 build_control_map_library.slugify_filename 与
    WT_Flow_Editor._slugify_control_library_part 两处（正则逻辑完全一致），
    现统一到此处。行为保持不变：非法文件名字符→下划线，压缩重复
    下划线/空白，首尾去除 . 与 _，截断为 80 字符；为空时返回 fallback。
    """
    text = re.sub(r"[\\/:*?\"<>|]+", "_", str(text or "").strip())
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:80] or fallback


GENERIC_MAIN_WINDOW_ROOT_NAMES = {"window", "window_main", "mainwindow", "main_window"}


def parse_uipath_root_segment(ui_path):
    """解析录制路径首段，返回 (name, control_type)。

    兼容 pywinauto_recorder 的 `>`/`->`/`/` 分隔与段尾坐标后缀，
    例如 `Window > MicroScaleMainView_View_Main` -> ("Window", "")。
    """
    text = str(ui_path or "").strip()
    if not text:
        return "", ""
    for sep in ("->", ">", "/"):
        if sep in text:
            root_text = text.split(sep, 1)[0]
            break
    else:
        root_text = text
    root_text = re.sub(r"%\(\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?\s*\)$", "", root_text).strip()
    if "||" in root_text:
        name, control_type = root_text.rsplit("||", 1)
    else:
        name, control_type = root_text, ""
    return str(name).strip(), str(control_type).strip()


def uipath_is_main_window_root(ui_path):
    """判断录制路径是否以通用主窗口根节点开头。

    录制器在主应用窗口内采集时，路径首段常写成 `Window` / `Window_Main`，
    此时控件位于真实顶层窗口内部，录制路径本身不携带真实窗口标题。
    这类控件的 windowTitle 若写成控件库分类名（如“创建新的粗糙度数据”），
    运行时按标题过滤必然 -160 判负，应视为伪标题并改为不约束。
    """
    name, control_type = parse_uipath_root_segment(ui_path)
    name_lower = str(name).lower()
    if name_lower in GENERIC_MAIN_WINDOW_ROOT_NAMES:
        return True
    return str(control_type).lower() == "window" and name_lower in {"window", "window_main"}


def normalize_window_title_for_uipath(window_title, ui_path=""):
    """按 uiPath 根节点规范化 windowTitle。

    主窗口根路径下返回 "*"（不约束标题），其余情况原样返回规范化文本。
    转换器与编辑器共用此规则，避免同类伪标题问题反复出现。
    """
    if uipath_is_main_window_root(ui_path):
        return "*"
    return str(window_title or "").strip()


def normalize_control_window_title(control):
    """Fix per-control windowTitle for generic main-window roots."""
    if isinstance(control, dict) and str(control.get("windowTitle", "")).strip():
        control["windowTitle"] = normalize_window_title_for_uipath(
            control["windowTitle"], control.get("uiPath", "")
        )
    return control


def normalize_inspect_scalar(value):
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower() in {"property does not exist", "[null]", "none", "null"}:
        return ""
    return strip_wrapping_quotes(text)


def has_meaningful_inspect_value(value):
    return bool(normalize_inspect_scalar(value))


def build_locator_recommendation(parsed):
    automation_id = normalize_inspect_scalar(parsed.get("automationId", ""))
    name = normalize_inspect_scalar(parsed.get("name", ""))
    class_name = normalize_inspect_scalar(parsed.get("className", ""))
    control_type = normalize_control_type_name(parsed.get("controlType", ""), parsed.get("localizedControlType", ""))
    if automation_id:
        return "automation_id", automation_id
    if name and name != "[null]":
        return "name", name
    if class_name:
        return "class_name", class_name
    if control_type:
        return "control_type", control_type
    return "", ""


def parse_inspect_text(raw_text):
    data = {
        "howFound": "",
        "name": "",
        "controlType": "",
        "localizedControlType": "",
        "boundingRectangle": "",
        "isEnabled": "",
        "isOffscreen": "",
        "isKeyboardFocusable": "",
        "hasKeyboardFocus": "",
        "processId": "",
        "runtimeId": "",
        "frameworkId": "",
        "className": "",
        "automationId": "",
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
        "rawText": raw_text.strip(),
    }
    lines = raw_text.splitlines()
    index = 0
    while index < len(lines):
        raw_line = lines[index].rstrip()
        stripped = raw_line.strip()
        if not stripped:
            index += 1
            continue
        matched = re.match(r"^([^:]+):\s*(.*)$", stripped)
        if matched:
            key = matched.group(1).strip()
            value = matched.group(2).strip()
        else:
            matched = re.match(r"^([^\t]+)\t+(.*)$", stripped)
            if not matched:
                index += 1
                continue
            key = matched.group(1).strip()
            value = matched.group(2).strip()
        if value.lower() == "property does not exist":
            value = ""
        if key in ("Children", "Ancestors"):
            items = []
            if value:
                items.append(value)
            next_index = index + 1
            while next_index < len(lines):
                next_line = lines[next_index].rstrip()
                next_stripped = next_line.strip()
                if not next_stripped:
                    next_index += 1
                    continue
                if re.match(r"^[^:]+:\s*", next_stripped) or re.match(r"^[^\t]+\t+", next_stripped):
                    break
                items.append(next_stripped)
                next_index += 1
            data[key.lower()] = items
            index = next_index
            continue

        key_map = {
            "How found": "howFound",
            "Name": "name",
            "ControlType": "controlType",
            "Control Type": "controlType",
            "LocalizedControlType": "localizedControlType",
            "Localized Control Type": "localizedControlType",
            "BoundingRectangle": "boundingRectangle",
            "Bounding Rectangle": "boundingRectangle",
            "IsEnabled": "isEnabled",
            "IsOffscreen": "isOffscreen",
            "IsKeyboardFocusable": "isKeyboardFocusable",
            "HasKeyboardFocus": "hasKeyboardFocus",
            "ProcessId": "processId",
            "Process Id": "processId",
            "RuntimeId": "runtimeId",
            "Runtime Id": "runtimeId",
            "FrameworkId": "frameworkId",
            "Framework Id": "frameworkId",
            "ClassName": "className",
            "Class Name": "className",
            "AutomationId": "automationId",
            "Automation Id": "automationId",
            "NativeWindowHandle": "nativeWindowHandle",
            "Native Window Handle": "nativeWindowHandle",
            "ProviderDescription": "providerDescription",
            "Provider Description": "providerDescription",
            "LegacyIAccessible.Name": "legacyName",
            "LegacyIAccessible.Role": "legacyRole",
            "LegacyIAccessible.State": "legacyState",
            "LegacyIAccessiblePattern.Name": "legacyName",
            "LegacyIAccessiblePattern.Role": "legacyRole",
            "LegacyIAccessiblePattern.State": "legacyState",
            "FirstChild": "firstChild",
            "LastChild": "lastChild",
            "Next": "next",
            "Previous": "previous",
        }
        normalized_key = key_map.get(key)
        if normalized_key:
            data[normalized_key] = normalize_inspect_scalar(value)
        if key.startswith("Is") and key.endswith("PatternAvailable") and value.lower() == "true":
            data["availablePatterns"].append(key)
        index += 1

    from build_control_map_library import build_locator_recommendation as advanced_build_locator
    locator_method, locator_value, locator_score, locator_reason = advanced_build_locator(data)
    aux_checks = []
    for check_key, label in [
        ("isEnabled", "IsEnabled"),
        ("isOffscreen", "IsOffscreen"),
        ("isKeyboardFocusable", "IsKeyboardFocusable"),
        ("hasKeyboardFocus", "HasKeyboardFocus"),
        ("frameworkId", "FrameworkId"),
        ("className", "ClassName"),
    ]:
        current_value = str(data.get(check_key, "")).strip()
        if current_value:
            aux_checks.append(f"{label}={current_value}")
    data["recommendedTargetMethod"] = locator_method
    data["recommendedTargetValue"] = locator_value
    data["recommendedTargetScore"] = locator_score
    data["recommendedTargetReason"] = locator_reason
    data["suggestedAuxChecks"] = aux_checks
    return data


def normalize_control(control, index):
    inspect_data = control.get("inspectData") if isinstance(control.get("inspectData"), dict) else {}
    raw_inspect_text = str(control.get("rawInspectText", "")).strip()
    ui_path = str(control.get("uiPath", "")).strip()
    normalized = {
        "id": str(control.get("id", f"control_{index + 1}")).strip() or f"control_{index + 1}",
        "name": str(control.get("name", f"控件 {index + 1}")).strip() or f"控件 {index + 1}",
        "role": str(control.get("role", "")).strip(),
        "enabled": bool(control.get("enabled", True)),
        "windowTitle": normalize_window_title_for_uipath(control.get("windowTitle", ""), ui_path),
        "targetMethod": str(control.get("targetMethod", "")).strip(),
        "targetValue": str(control.get("targetValue", "")).strip(),
        "templateKey": str(control.get("templateKey", "")).strip(),
        "uiPath": ui_path,
        "notes": str(control.get("notes", "")).strip(),
        # labelText/relatedLabelName 是多实例判别核心字段（如“半径/X/载入”旁的 Edit），
        # 白名单构建必须显式透传，否则编辑器过滤/显示管线拿不到标签
        "labelText": str(control.get("labelText") or "").strip(),
        "relatedLabelName": str(control.get("relatedLabelName") or "").strip(),
        # helpText/functionText：运行时 helpText 消歧加分依赖快照内字段
        # （图标按钮 UIA Name 是 SVG path，helpText 是软件真实操作语义），
        # 顶层透传与 inspectData 双保险，避免已导入步骤享受不到 helpText 消歧。
        "helpText": str(control.get("helpText") or "").strip(),
        "functionText": str(control.get("functionText") or "").strip(),
        "rawInspectText": raw_inspect_text,
        "optionValues": [normalize_inspect_scalar(item) for item in control.get("optionValues", []) if normalize_inspect_scalar(item)],
        "auxChecks": [str(item).strip() for item in control.get("auxChecks", []) if str(item).strip()],
        "inspectData": {
            "howFound": normalize_inspect_scalar(inspect_data.get("howFound", "")),
            "name": normalize_inspect_scalar(inspect_data.get("name", "")),
            "controlType": normalize_inspect_scalar(inspect_data.get("controlType", "")),
            "localizedControlType": normalize_inspect_scalar(inspect_data.get("localizedControlType", "")),
            "boundingRectangle": normalize_inspect_scalar(inspect_data.get("boundingRectangle", "")),
            "isEnabled": normalize_inspect_scalar(inspect_data.get("isEnabled", "")),
            "isOffscreen": normalize_inspect_scalar(inspect_data.get("isOffscreen", "")),
            "isKeyboardFocusable": normalize_inspect_scalar(inspect_data.get("isKeyboardFocusable", "")),
            "hasKeyboardFocus": normalize_inspect_scalar(inspect_data.get("hasKeyboardFocus", "")),
            "processId": normalize_inspect_scalar(inspect_data.get("processId", "")),
            "runtimeId": normalize_inspect_scalar(inspect_data.get("runtimeId", "")),
            "frameworkId": normalize_inspect_scalar(inspect_data.get("frameworkId", "")),
            "className": normalize_inspect_scalar(inspect_data.get("className", "")),
            "automationId": normalize_inspect_scalar(inspect_data.get("automationId", "")),
            "nativeWindowHandle": normalize_inspect_scalar(inspect_data.get("nativeWindowHandle", "")),
            "providerDescription": normalize_inspect_scalar(inspect_data.get("providerDescription", "")),
            "legacyName": normalize_inspect_scalar(inspect_data.get("legacyName", "")),
            "legacyRole": normalize_inspect_scalar(inspect_data.get("legacyRole", "")),
            "legacyState": normalize_inspect_scalar(inspect_data.get("legacyState", "")),
            "firstChild": normalize_inspect_scalar(inspect_data.get("firstChild", "")),
            "lastChild": normalize_inspect_scalar(inspect_data.get("lastChild", "")),
            "next": normalize_inspect_scalar(inspect_data.get("next", "")),
            "previous": normalize_inspect_scalar(inspect_data.get("previous", "")),
            "children": [normalize_inspect_scalar(item) for item in inspect_data.get("children", []) if normalize_inspect_scalar(item)],
            "ancestors": [normalize_inspect_scalar(item) for item in inspect_data.get("ancestors", []) if normalize_inspect_scalar(item)],
            "availablePatterns": [normalize_inspect_scalar(item) for item in inspect_data.get("availablePatterns", []) if normalize_inspect_scalar(item)],
            # ── 定位增强字段：消歧 / Fallback / 状态匹配 ──
            "foundIndex": normalize_inspect_scalar(inspect_data.get("foundIndex", "")),
            "isControlElement": normalize_inspect_scalar(inspect_data.get("isControlElement", "")),
            "isContentElement": normalize_inspect_scalar(inspect_data.get("isContentElement", "")),
            "value": normalize_inspect_scalar(inspect_data.get("value", "")),
            "toggleState": normalize_inspect_scalar(inspect_data.get("toggleState", "")),
            "isVisible": normalize_inspect_scalar(inspect_data.get("isVisible", "")),
            "supportedPatterns": [normalize_inspect_scalar(item) for item in inspect_data.get("supportedPatterns", []) if normalize_inspect_scalar(item)],
            "textContent": normalize_inspect_scalar(inspect_data.get("textContent", "")),
            "recommendedTargetMethod": str(inspect_data.get("recommendedTargetMethod", "")).strip(),
            "recommendedTargetValue": str(inspect_data.get("recommendedTargetValue", "")).strip(),
            "labelText": normalize_inspect_scalar(inspect_data.get("labelText", "")),
            "relatedLabelName": normalize_inspect_scalar(inspect_data.get("relatedLabelName", "")),
            "helpText": normalize_inspect_scalar(inspect_data.get("helpText", "")),
            "optionValues": [normalize_inspect_scalar(item) for item in inspect_data.get("optionValues", []) if normalize_inspect_scalar(item)],
        },
    }
    if raw_inspect_text and not has_meaningful_inspect_value(normalized["inspectData"]["name"]):
        parsed = parse_inspect_text(raw_inspect_text)
        normalized["inspectData"].update({key: value for key, value in parsed.items() if key in normalized["inspectData"]})
        if not normalized["targetMethod"]:
            normalized["targetMethod"] = parsed.get("recommendedTargetMethod", "")
        if not normalized["targetValue"]:
            normalized["targetValue"] = parsed.get("recommendedTargetValue", "")
        if not normalized["auxChecks"]:
            normalized["auxChecks"] = parsed.get("suggestedAuxChecks", [])
        if not normalized["uiPath"]:
            normalized["uiPath"] = normalized["inspectData"].get("name", "")
    if normalized["uiPath"] and normalized["windowTitle"]:
        normalized["windowTitle"] = normalize_window_title_for_uipath(
            normalized["windowTitle"], normalized["uiPath"]
        )
    # 保留 tabNavigation 配置（Tab 导航降级定位）
    tab_nav = control.get("tabNavigation")
    if isinstance(tab_nav, dict) and tab_nav:
        normalized["tabNavigation"] = {
            "anchorControlId": str(tab_nav.get("anchorControlId", "")).strip(),
            "direction": str(tab_nav.get("direction", "forward")).strip() or "forward",
            "steps": int(tab_nav.get("steps", 0)) if str(tab_nav.get("steps", "")).strip() else 0,
            "verify": tab_nav.get("verify", {}) if isinstance(tab_nav.get("verify"), dict) else {},
            **({"clickTwiceToExpand": True} if tab_nav.get("clickTwiceToExpand") else {}),
        }
    # 保留 preferTabNavigation 配置（优先 Tab 导航，跳过常规降级链）
    if control.get("preferTabNavigation"):
        normalized["preferTabNavigation"] = True
    # 保留控件来源标记 sourceInfo（来源库/导入时间/编辑与删除联动标记）
    source_info = control.get("sourceInfo")
    if isinstance(source_info, dict) and source_info:
        normalized["sourceInfo"] = normalize_source_info(source_info)
    return normalized


def build_synthetic_inspect_text(inspect_data):
    lines = [
        f'Name: \t "{inspect_data.get("name", "")}"',
        f'ControlType: \t {inspect_data.get("controlType", "")}',
        f'LocalizedControlType: \t "{inspect_data.get("localizedControlType", "")}"',
        f'BoundingRectangle: \t {inspect_data.get("boundingRectangle", "")}',
        f'IsEnabled: \t {inspect_data.get("isEnabled", "")}',
        f'IsOffscreen: \t {inspect_data.get("isOffscreen", "")}',
        f'FrameworkId: \t "{inspect_data.get("frameworkId", "")}"',
        f'ClassName: \t "{inspect_data.get("className", "")}"',
        f'AutomationId: \t "{inspect_data.get("automationId", "")}"',
        f'NativeWindowHandle: \t {inspect_data.get("nativeWindowHandle", "")}',
        f'LegacyIAccessible.Name: \t "{inspect_data.get("legacyName", "")}"',
        f'LegacyIAccessible.Role: \t {inspect_data.get("legacyRole", "")}',
        f'LegacyIAccessible.State: \t {inspect_data.get("legacyState", "")}',
    ]
    children = inspect_data.get("children", [])
    if children:
        lines.append("Children: \t " + children[0])
        lines.extend(f"\t {item}" for item in children[1:])
    ancestors = inspect_data.get("ancestors", [])
    if ancestors:
        lines.append("Ancestors: \t " + ancestors[0])
        lines.extend(f"\t {item}" for item in ancestors[1:])
    return "\n".join(lines)


def normalize_step(step, index, default_step_controls_by_id):
    inspect_hints = step.get("inspectHints") if isinstance(step.get("inspectHints"), dict) else {}
    raw_controls = step.get("controls") if isinstance(step.get("controls"), list) else []
    step_params = step.get("stepParams") if isinstance(step.get("stepParams"), dict) else {}
    action_config = step.get("actionConfig") if isinstance(step.get("actionConfig"), dict) else {}
    if not raw_controls:
        raw_controls = default_step_controls_by_id.get(str(step.get("id", "")).strip(), [])
    normalized = {
        "id": str(step.get("id", f"step_{index + 1}")).strip() or f"step_{index + 1}",
        "name": str(step.get("name", f"步骤 {index + 1}")).strip() or f"步骤 {index + 1}",
        "stage": str(step.get("stage", "")).strip(),
        "strategy": str(step.get("strategy", "script")).strip() or "script",
        "actionType": str(step.get("actionType", "script")).strip() or "script",
        "topLevel": bool(step.get("topLevel", True)),
        "enabled": bool(step.get("enabled", True)),
        "codeSymbol": str(step.get("codeSymbol", "")).strip(),
        "codeReference": str(step.get("codeReference", "")).strip(),
        "packageRef": str(step.get("packageRef", "")).strip(),
        "description": str(step.get("description", "")).strip(),
        "successLog": str(step.get("successLog", "")).strip(),
        "windowTitle": str(step.get("windowTitle", "")).strip(),
        "inspectHints": {
            "controlName": str(inspect_hints.get("controlName", "")).strip(),
            "className": str(inspect_hints.get("className", "")).strip(),
            "automationId": str(inspect_hints.get("automationId", "")).strip(),
            "controlType": str(inspect_hints.get("controlType", "")).strip(),
            "uiPath": str(inspect_hints.get("uiPath", "")).strip(),
            "templateKey": str(inspect_hints.get("templateKey", "")).strip(),
        },
        "controls": [normalize_control(control, control_index) for control_index, control in enumerate(raw_controls)],
        "stepParams": {str(key).strip(): value for key, value in step_params.items() if str(key).strip()},
        "actionConfig": {str(key).strip(): value for key, value in action_config.items() if str(key).strip()},
        "auxChecks": [str(item).strip() for item in step.get("auxChecks", []) if str(item).strip()],
        "fallbacks": [str(item).strip() for item in step.get("fallbacks", []) if str(item).strip()],
        "notes": str(step.get("notes", "")).strip(),
    }
    # 保留转换器生成的自适应降级链（字典列表，编辑器不编辑但需原样保留）
    if isinstance(step.get("fallbackChain"), list):
        normalized["fallbackChain"] = step["fallbackChain"]
    # 保留 stepTags（参数表 stepMode 行级过滤依赖；编辑器不编辑但必须原样保留，
    # 否则编辑器保存会剥离 stepTags → 过滤失效 → 每行全跑模板步骤）。
    if "stepTags" in step:
        normalized["stepTags"] = step["stepTags"]
    # 保留下划线前缀的元数据字段（如 _status / _sourceRecorderLine 等），避免保存时丢失
    for key, value in step.items():
        if str(key).startswith("_") and key not in normalized:
            normalized[key] = value
    return normalized


def normalize_runtime_config(runtime_config):
    runtime_config = runtime_config if isinstance(runtime_config, dict) else {}
    return {
        "gmExe": str(runtime_config.get("gmExe", "")).strip(),
        "sourceFilePath": str(runtime_config.get("sourceFilePath", "")).strip(),
        "outputDir": str(runtime_config.get("outputDir", "")).strip(),
        "projectionFilePath": str(runtime_config.get("projectionFilePath", "")).strip(),
    }


def normalize_flow_packages(flow_packages):
    if not isinstance(flow_packages, list):
        return []
    normalized = []
    for index, package in enumerate(flow_packages, start=1):
        if not isinstance(package, dict):
            continue
        package_id = str(package.get("id", f"package_{index}")).strip() or f"package_{index}"
        normalized.append(
            {
                "id": package_id,
                "name": str(package.get("name", package_id)).strip() or package_id,
                "description": str(package.get("description", "")).strip(),
                "stepIds": [str(item).strip() for item in package.get("stepIds", []) if str(item).strip()],
            }
        )
    return normalized


def parse_float_or_default(raw_value, field_name, default_value):
    text = str(raw_value).strip()
    if not text:
        return default_value
    try:
        return float(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是数字。") from exc


def parse_int_or_default(raw_value, field_name, default_value):
    text = str(raw_value).strip()
    if not text:
        return default_value
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是整数。") from exc
