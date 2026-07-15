ACTION_DEFAULT_CONFIGS = {
    "click": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.12},
    "double_click": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.18},
    "right_click": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.18},
    "double_right_click": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.25},
    "type_text": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.15},
    "type_text_relative": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.15},
    "click_relative_region": {"timeoutSeconds": 2.5, "waitBefore": 0.0, "waitAfter": 0.12},
    "select_dropdown_item_runtime": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.15},
    "send_keys": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.12},
    "drag_and_drop": {"timeoutSeconds": 3.0, "waitBefore": 0.0, "waitAfter": 0.2},
    "mouse_wheel": {"timeoutSeconds": 2.0, "waitBefore": 0.0, "waitAfter": 0.12},
    "wait_for_control": {"timeoutSeconds": 8.0, "waitBefore": 0.0, "waitAfter": 0.0},
    "sleep": {"timeoutSeconds": 0.0, "waitBefore": 0.0, "waitAfter": 0.0},
}


def build_action_default_config(action_name, **overrides):
    normalized_action = str(action_name or "click").strip() or "click"
    config = dict(ACTION_DEFAULT_CONFIGS.get(normalized_action, ACTION_DEFAULT_CONFIGS["click"]))
    config["action"] = normalized_action
    for key, value in overrides.items():
        if value is not None:
            config[key] = value
    return config
