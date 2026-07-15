# encoding: utf-8

import re


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

    locator_method, locator_value = build_locator_recommendation(data)
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
    data["suggestedAuxChecks"] = aux_checks
    return data


def normalize_control(control, index):
    inspect_data = control.get("inspectData") if isinstance(control.get("inspectData"), dict) else {}
    raw_inspect_text = str(control.get("rawInspectText", "")).strip()
    normalized = {
        "id": str(control.get("id", f"control_{index + 1}")).strip() or f"control_{index + 1}",
        "name": str(control.get("name", f"控件 {index + 1}")).strip() or f"控件 {index + 1}",
        "role": str(control.get("role", "")).strip(),
        "enabled": bool(control.get("enabled", True)),
        "windowTitle": str(control.get("windowTitle", "")).strip(),
        "targetMethod": str(control.get("targetMethod", "")).strip(),
        "targetValue": str(control.get("targetValue", "")).strip(),
        "templateKey": str(control.get("templateKey", "")).strip(),
        "uiPath": str(control.get("uiPath", "")).strip(),
        "notes": str(control.get("notes", "")).strip(),
        "rawInspectText": raw_inspect_text,
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
            "recommendedTargetMethod": str(inspect_data.get("recommendedTargetMethod", "")).strip(),
            "recommendedTargetValue": str(inspect_data.get("recommendedTargetValue", "")).strip(),
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
    return {
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
