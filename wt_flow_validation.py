# encoding: utf-8

from wt_action_schema import (
    ACTION_SCHEMAS,
    ALLOWED_CONTINUE_WHEN_CONDITIONS,
    ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS,
    ALLOWED_RELATIVE_REGION_ANCHORS,
    get_action_schema,
)


def _split_locator_parts(text):
    parts = []
    buffer = []
    index = 0
    text = str(text or "")
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] == ",":
            buffer.append(",")
            index += 2
            continue
        if char == ",":
            current = "".join(buffer).strip()
            if current:
                parts.append(current)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    current = "".join(buffer).strip()
    if current:
        parts.append(current)
    return parts


def _contains_placeholder_text(value):
    text = str(value or "").strip()
    if not text:
        return False
    return "请补充" in text or text.startswith("<") or text.endswith(">")


def validate_step_definition(step, package_ids=None):
    errors = []
    step = step if isinstance(step, dict) else {}
    package_ids = set(package_ids or [])

    step_id = str(step.get("id", "")).strip()
    step_name = str(step.get("name", "")).strip()
    action_type = str(step.get("actionType", "script")).strip() or "script"
    label = step_name or step_id or "<未命名步骤>"

    if not step_id:
        errors.append(f"步骤 {label} 缺少步骤ID。")
    if not step_name:
        errors.append(f"步骤 {step_id or '<未命名步骤>'} 缺少步骤名称。")

    if action_type == "action":
        action_config = step.get("actionConfig", {}) if isinstance(step.get("actionConfig"), dict) else {}
        action_name = str(action_config.get("action", "")).strip() or "click"
        if action_name not in ACTION_SCHEMAS:
            errors.append(f"步骤 {label} 的动作 `{action_name}` 非法。")
        schema = get_action_schema(action_name)
        control_id = str(action_config.get("controlId", "")).strip()
        known_control_ids = {
            str(control.get("id", "")).strip()
            for control in step.get("controls", [])
            if isinstance(control, dict) and str(control.get("id", "")).strip()
        }

        # send_keys 支持"无目标控件"：向当前焦点发送按键（如"名称输入后 Tab 切到描述框"场景）。
        # 其余动作仍要求必须配置目标控件。
        if schema.get("target_required") and not control_id and action_name != "send_keys":
            errors.append(f"步骤 {label} 的动作 `{action_name}` 缺少目标控件。")
        if control_id and control_id not in known_control_ids:
            errors.append(f"步骤 {label} 的目标控件 `{control_id}` 不存在于当前步骤细分控件清单中。")

        step_window_title = str(step.get("windowTitle", "")).strip()
        if _contains_placeholder_text(step_window_title):
            errors.append(f"步骤 {label} 的目标窗口仍是占位值，请改成真实窗口标题。")

        input_key = str(schema.get("input_key", "")).strip()
        if schema.get("input_required") and not str(action_config.get(input_key, "")).strip():
            errors.append(f"步骤 {label} 的动作 `{action_name}` 缺少 `{input_key}`。")

        if action_name in {"type_text_relative", "click_relative_region"}:
            parent_window = action_config.get("parentWindow", {}) if isinstance(action_config.get("parentWindow"), dict) else {}
            relative_region = action_config.get("relativeRegion", {}) if isinstance(action_config.get("relativeRegion"), dict) else {}
            parent_title = str(parent_window.get("title", "")).strip() or str(step.get("windowTitle", "")).strip()
            parent_class_name = str(parent_window.get("className", "")).strip()
            parent_framework_id = str(parent_window.get("frameworkId", "")).strip()
            # Allow untitled host windows only when class/framework are both explicit,
            # so the relative-region action still has a constrained parent window spec.
            if not parent_title and not (parent_class_name and parent_framework_id):
                errors.append(f"步骤 {label} 的动作 `{action_name}` 缺少父窗口标题。")
            if parent_framework_id and parent_framework_id not in ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS:
                errors.append(
                    f"步骤 {label} 的动作 `{action_name}` 的父窗口 `frameworkId` 非法，"
                    f"仅支持: {', '.join(ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS)}。"
                )
            for key in ("x", "y", "width", "height"):
                raw_value = relative_region.get(key, "")
                try:
                    numeric_value = float(raw_value)
                except Exception:
                    errors.append(f"步骤 {label} 的动作 `{action_name}` 缺少相对区域 `{key}`。")
                    continue
                if key in {"x", "y"} and not (0.0 <= numeric_value <= 1.0):
                    errors.append(f"步骤 {label} 的动作 `{action_name}` 的 `{key}` 需在 0 到 1 之间。")
                if key in {"width", "height"} and not (0.0 < numeric_value <= 1.0):
                    errors.append(f"步骤 {label} 的动作 `{action_name}` 的 `{key}` 需大于 0 且不超过 1。")
            anchor = str(relative_region.get("anchor", "")).strip()
            if anchor and anchor not in ALLOWED_RELATIVE_REGION_ANCHORS:
                errors.append(
                    f"步骤 {label} 的动作 `{action_name}` 的 `anchor` 非法，"
                    f"仅支持: {', '.join(ALLOWED_RELATIVE_REGION_ANCHORS)}。"
                )

        continue_when = action_config.get("continueWhen", {}) if isinstance(action_config.get("continueWhen"), dict) else {}
        continue_control_id = str(continue_when.get("controlId", "")).strip()
        continue_condition = str(continue_when.get("condition", "")).strip().lower()
        if continue_control_id and continue_control_id not in known_control_ids:
            errors.append(f"步骤 {label} 的续跑控件 `{continue_control_id}` 不存在于当前步骤细分控件清单中。")
        if continue_condition and continue_condition not in ALLOWED_CONTINUE_WHEN_CONDITIONS:
            errors.append(
                f"步骤 {label} 的续跑条件 `{continue_condition}` 非法，"
                f"仅支持: {', '.join(ALLOWED_CONTINUE_WHEN_CONDITIONS)}。"
            )

    controls = step.get("controls", []) if isinstance(step.get("controls"), list) else []
    for control in controls:
        if not isinstance(control, dict):
            continue
        control_label = str(control.get("name", "")).strip() or str(control.get("id", "")).strip() or "未命名控件"
        target_method = str(control.get("targetMethod", "")).strip()
        target_value = str(control.get("targetValue", "")).strip()
        control_window_title = str(control.get("windowTitle", "")).strip()
        method_parts = _split_locator_parts(target_method)
        value_parts = _split_locator_parts(target_value)

        if _contains_placeholder_text(control_window_title):
            errors.append(f"步骤 {label} 的控件 `{control_label}` 仍是占位窗口标题，请改成真实窗口标题。")
        if _contains_placeholder_text(target_value):
            errors.append(f"步骤 {label} 的控件 `{control_label}` 的 targetValue 仍是占位值，请补成真实定位值。")
        if method_parts and len(method_parts) != len(value_parts):
            errors.append(
                f"步骤 {label} 的控件 `{control_label}` 的 targetMethod 与 targetValue 数量不一致："
                f"{len(method_parts)} 个方法，但只有 {len(value_parts)} 个值。"
            )

    if action_type == "flow_ref":
        package_ref = str(step.get("packageRef", "")).strip()
        if not package_ref:
            errors.append(f"步骤 {label} 是 flow_ref，但未填写流程包引用。")
        elif package_ids and package_ref not in package_ids:
            errors.append(f"步骤 {label} 引用了不存在的流程包 `{package_ref}`。")

    return errors


def validate_flow_definition(flow_definition):
    errors = []
    flow_definition = flow_definition if isinstance(flow_definition, dict) else {}
    steps = flow_definition.get("steps", []) if isinstance(flow_definition.get("steps", []), list) else []
    flow_packages = flow_definition.get("flowPackages", []) if isinstance(flow_definition.get("flowPackages", []), list) else []

    package_ids = {
        str(package.get("id", "")).strip()
        for package in flow_packages
        if isinstance(package, dict) and str(package.get("id", "")).strip()
    }
    seen_package_ids = {}

    seen_step_ids = {}
    for index, step in enumerate(steps, start=1):
        step = step if isinstance(step, dict) else {}
        step_id = str(step.get("id", "")).strip()
        if step_id:
            seen_step_ids.setdefault(step_id, []).append(index)
        errors.extend(validate_step_definition(step, package_ids=package_ids))

    for step_id, indexes in seen_step_ids.items():
        if len(indexes) > 1:
            errors.append(f"步骤ID `{step_id}` 重复出现 {len(indexes)} 次。")

    for index, package in enumerate(flow_packages, start=1):
        if not isinstance(package, dict):
            continue
        package_id = str(package.get("id", "")).strip() or "<未命名流程包>"
        if package_id != "<未命名流程包>":
            seen_package_ids.setdefault(package_id, []).append(index)
        for step_id in package.get("stepIds", []):
            normalized_step_id = str(step_id or "").strip()
            if normalized_step_id and normalized_step_id not in seen_step_ids:
                errors.append(f"流程包 `{package_id}` 引用了不存在的步骤 `{normalized_step_id}`。")

    for package_id, indexes in seen_package_ids.items():
        if len(indexes) > 1:
            errors.append(f"流程包ID `{package_id}` 重复出现 {len(indexes)} 次。")

    return errors
