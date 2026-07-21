# encoding: utf-8
"""控件信息扫描规整（归一化）层 —— uia-peek / axe-windows 适配器共用的输出规整。

为什么要有这一层
================
WT_Automation 现有 pywinauto 采集（build_control_map_library.py）在写快照时，
会对字段做统一规整：controlType 去前缀/转小写、过滤 SVG 几何乱码 name、给出 10 档
locator 推荐、标注 authority。而 uia-peek / axe-windows 这两个第三方工具返回的字段
命名、大小写、完整度都不同：

  - UiaPeek  REST 返回 controlType 形如 "Button" / "Edit"（首字母大写）；
  - Axe.Windows 返回 ControlType 形如 "Button"（首字母大写）；
  - pywinauto  返回 control_type 形如 "button" / "edit"（全小写）。

若直接把三者写入 control_maps/，会导致：

  1. merge_standard_control_library 按 controlType.lower() 去重时，
     "Button" 与 "button" 实际已是同值，但控件实例缺少 authority/displayName、
     auxChecks 不一致，跨来源无法稳定合并；
  2. control_live_detector 评分依赖 authority / displayName / recommended* 字段，
     新来源缺失会导致匹配降级；
  3. locator 推荐档位/规则与主线不一致，影响后续定位稳定性。

本模块让三个来源输出【完全同构】的 control_definition，保证合并/匹配一致。

设计约束：本模块【不 import】 build_control_map_library，保持外部适配器独立、可裁剪。
其规整规则是主线规则的镜像（如有调整需同步）。
"""
import re


# ---------------------------------------------------------------------------
# controlType 归一
# ---------------------------------------------------------------------------
_UIA_PREFIX_RE = re.compile(r"UIA_")
_PAREN_ID_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\d+\)\s*$")


def normalize_control_type(control_type, localized_control_type=""):
    """对齐 build_control_map_library.normalize_control_type_name：
    去掉 UIA_/ControlTypeId/'(NNN)'，统一小写；无值回退 localizedControlType。"""
    ct = str(control_type or "").strip()
    if "UIA_" in ct or "ControlTypeId" in ct:
        ct = ct.replace("UIA_", "").replace("ControlTypeId", "").strip()
    m = _PAREN_ID_RE.match(ct)
    if m:
        ct = m.group(1)
    ct = ct.lower().strip()
    if not ct:
        ct = str(localized_control_type or "").strip().lower()
    return ct


# ---------------------------------------------------------------------------
# name 乱码过滤（对齐 build_control_map_library._is_garbage_name）
# ---------------------------------------------------------------------------
def is_garbage_name(name):
    """判断 name 是否是 SVG path / 几何坐标序列 / 超长乱码，不可作为可读名称。"""
    if not name:
        return False
    name = str(name).strip()
    if re.match(r"^[MLCAHVZmlcahvz][\d.,\s\-]+", name) and name.count(",") >= 3:
        return True
    if re.match(r"^[\d.,\s]+$", name) and len(name) > 10:
        return True
    return False


def normalize_name(name, max_len=80):
    name = str(name or "").strip()
    if is_garbage_name(name) or len(name) > max_len:
        return ""
    return name


# ---------------------------------------------------------------------------
# locator 推荐（10 档，与 build_control_map_library.build_locator_recommendation 对齐）
# ---------------------------------------------------------------------------
def recommend_locator(automation_id="", name="", class_name="", handle="", control_type=""):
    automation_id = str(automation_id or "").strip()
    name = normalize_name(name)
    class_name = str(class_name or "").strip()
    handle = str(handle or "").strip()
    control_type = normalize_control_type(control_type)
    candidates = [
        ("automation_id,control_type", [automation_id, control_type], 100, "automation_id + control_type"),
        ("automation_id,class_name", [automation_id, class_name], 96, "automation_id + class_name"),
        ("automation_id", [automation_id], 92, "automation_id"),
        ("name,control_type", [name, control_type], 88, "name + control_type"),
        ("name,class_name", [name, class_name], 84, "name + class_name"),
        ("name", [name], 78, "name"),
        ("class_name,control_type", [class_name, control_type], 68, "class_name + control_type"),
        ("class_name", [class_name], 58, "class_name"),
        ("control_type", [control_type], 42, "control_type"),
        ("handle", [handle], 24, "handle"),
    ]
    for method, values, score, reason in candidates:
        if all(str(item).strip() and str(item).strip() != "[null]" for item in values):
            return method, ",".join(values), score, reason
    return "", "", 0, "no_stable_locator"


# ---------------------------------------------------------------------------
# 权威度（对齐 merge_standard_control_library.authority）
# ---------------------------------------------------------------------------
def authority_of(automation_id="", name="", class_name=""):
    automation_id = str(automation_id or "").strip()
    name = str(name or "").strip()
    class_name = str(class_name or "").strip()
    if automation_id:
        return "high"
    if name and class_name:
        return "medium"
    if name:
        return "low"
    return "unknown"


# ---------------------------------------------------------------------------
# 矩形
# ---------------------------------------------------------------------------
def rect_to_string(box):
    if not isinstance(box, dict):
        return ""
    try:
        l, t, r, b = int(box["left"]), int(box["top"]), int(box["right"]), int(box["bottom"])
    except Exception:
        return ""
    if r <= l or b <= t:
        return ""
    return "[l={},t={},r={},b={}]".format(l, t, r, b)


# ---------------------------------------------------------------------------
# auxChecks（对齐 build_control_map_library.build_aux_checks）
# ---------------------------------------------------------------------------
_AUX_FIELDS = [
    ("isEnabled", "IsEnabled"),
    ("isOffscreen", "IsOffscreen"),
    ("isKeyboardFocusable", "IsKeyboardFocusable"),
    ("hasKeyboardFocus", "HasKeyboardFocus"),
    ("frameworkId", "FrameworkId"),
    ("className", "ClassName"),
    ("processId", "ProcessId"),
    ("controlType", "ControlType"),
]


def build_aux_checks(inspect):
    checks = []
    for key, label in _AUX_FIELDS:
        value = str(inspect.get(key, "")).strip()
        if value:
            checks.append("{}={}".format(label, value))
    return checks


# ---------------------------------------------------------------------------
# 统一 control_definition 构造
# ---------------------------------------------------------------------------
def generate_control_id(automation_id="", class_name="", control_type="", index=0):
    """生成控件唯一 id：优先 automationId，fallback 到 className+index。

    规则与 _sanitize_control_id 对齐，确保控件库消费方去重可用。
    """
    if automation_id:
        return automation_id
    base = str(class_name or "").strip()
    if not base:
        base = str(control_type or "").strip()
    if not base:
        base = "Control"
    if index:
        return "{}_{}".format(base, index)
    return base


def build_control_definition(
    name="", automation_id="", class_name="", control_type="",
    localized_control_type="", framework_id="", window_title="",
    process_id="", handle="", rect_box=None,
    is_enabled="", is_visible="", is_offscreen="",
    is_keyboard_focusable="", has_keyboard_focus="",
    runtime_id="", native_window_handle="", help_text="", provider_description="",
    patterns=None, source="external", extra=None,
    is_trigger=False, depth=0, index=0, ui_path="", parent_path="",
):
    """构造与 build_control_map_library 同构的 control_definition。

    规整动作：name 乱码过滤、controlType 小写化、10 档 locator、authority、displayName、
    auxChecks、boundingRectangle/boundingBox、inspectData 一致化。
    """
    name = normalize_name(name)
    control_type = normalize_control_type(control_type, localized_control_type)
    automation_id = str(automation_id or "").strip()
    class_name = str(class_name or "").strip()
    framework_id = str(framework_id or "").strip()
    loc_method, loc_value, loc_score, loc_reason = recommend_locator(
        automation_id=automation_id, name=name, class_name=class_name,
        handle=str(handle or "").strip(), control_type=control_type)
    authority = authority_of(automation_id, name, class_name)
    display_name = name or automation_id or class_name or control_type or "控件{}".format(index)
    rect_box = rect_box if isinstance(rect_box, dict) else {}
    rect_str = rect_to_string(rect_box)

    inspect = {
        "name": name,
        "controlType": control_type,
        "localizedControlType": str(localized_control_type or "").strip(),
        "boundingRectangle": rect_str,
        "isEnabled": str(is_enabled),
        "isVisible": str(is_visible),
        "isOffscreen": str(is_offscreen),
        "isKeyboardFocusable": str(is_keyboard_focusable),
        "hasKeyboardFocus": str(has_keyboard_focus),
        "processId": str(process_id or "").strip(),
        "runtimeId": str(runtime_id or "").strip(),
        "frameworkId": framework_id,
        "className": class_name,
        "automationId": automation_id,
        "nativeWindowHandle": str(native_window_handle or handle or "").strip(),
        "helpText": str(help_text or "").strip(),
        "providerDescription": str(provider_description or "").strip(),
        "source": source,
    }
    # 补充 UIA Patterns 衍生的动作能力标识
    if patterns:
        inspect["patterns"] = patterns
        # 深度规整: 将 UIA Patterns 转换为动作标识，辅助 executor 决定执行策略
        is_clickable = False
        is_editable = False
        for p in patterns:
            p_lower = p.lower()
            if p_lower in ("invoke", "toggle", "selectionitem", "expandcollapse"):
                is_clickable = True
            if p_lower in ("value", "text", "rangevalue"):
                is_editable = True
        
        extra_flags = {}
        if is_clickable:
            extra_flags["isClickable"] = True
        if is_editable:
            extra_flags["isEditable"] = True
            
        if extra_flags:
            inspect.update(extra_flags)

    if extra:
        inspect.update(extra)

    return {
        "id": generate_control_id(automation_id, class_name, control_type, index),
        "name": name,
        "displayName": display_name,
        "windowTitle": str(window_title or "").strip(),
        "frameworkId": framework_id,
        "controlType": control_type,
        "className": class_name,
        "automationId": automation_id,
        "targetMethod": loc_method,
        "targetValue": loc_value,
        "recommendedTargetMethod": loc_method,
        "recommendedTargetValue": loc_value,
        "locatorScore": loc_score,
        "locatorReason": loc_reason,
        "authority": authority,
        "auxChecks": build_aux_checks(inspect),
        "boundingBox": rect_box,
        "uiPath": str(ui_path or "").strip(),
        "parentPath": str(parent_path or "").strip(),
        "isTriggerElement": bool(is_trigger),
        "depth": int(depth or 0),
        "index": int(index or 0),
        "inspectData": inspect,
    }


# ---------------------------------------------------------------------------
# controlDefinitions -> flatControls 镜像（供读取 flatControls 的下游工具使用）
# ---------------------------------------------------------------------------
def definitions_to_flat_controls(control_defs, window_title=""):
    flat = []
    for cdef in control_defs or []:
        if not isinstance(cdef, dict):
            continue
        ins = cdef.get("inspectData", {}) or {}
        flat.append({
            "name": cdef.get("name", ""),
            "displayName": cdef.get("displayName", ""),
            "windowTitle": cdef.get("windowTitle", window_title),
            "frameworkId": cdef.get("frameworkId", ""),
            "controlType": cdef.get("controlType", ""),
            "className": cdef.get("className", ""),
            "automationId": cdef.get("automationId", ""),
            "boundingRectangle": ins.get("boundingRectangle", ""),
            "boundingBox": cdef.get("boundingBox", {}) if isinstance(cdef.get("boundingBox"), dict) else {},
            "locatorScore": cdef.get("locatorScore", 0),
            "locatorReason": cdef.get("locatorReason", ""),
            "recommendedTargetMethod": cdef.get("recommendedTargetMethod", ""),
            "recommendedTargetValue": cdef.get("recommendedTargetValue", ""),
            "uiPath": cdef.get("uiPath", ""),
            "parentPath": cdef.get("parentPath", ""),
            "isTriggerElement": cdef.get("isTriggerElement", False),
            "depth": cdef.get("depth", 0),
            "index": cdef.get("index", 0),
            "authority": cdef.get("authority", "low"),
            "auxChecks": cdef.get("auxChecks", []),
            "inspectData": ins,
        })
    return flat
