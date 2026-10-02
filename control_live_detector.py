# encoding: utf-8
"""
control_live_detector.py
实时控件检测器 - 鼠标悬停时捕获控件信息并与控件库实时匹配

采用与 build_control_map_library.py 完全一致的 Smart 探测模式：
1. 使用 pywinauto Desktop.from_point 获取鼠标位置的控件
2. 向上遍历父级链获取完整信息（支持设置深度）
3. 优先使用用户总控件库，找不到再遍历其他库
"""
import os
import json
import shutil
import time
import threading
import tkinter as tk
from types import SimpleNamespace
from tkinter import ttk, scrolledtext, messagebox, filedialog


# ============================================================================
# 统一浅色蓝灰主题
# ============================================================================

DETECTOR_THEME = {
    "bg": "#f4f7fb",
    "panel": "#ffffff",
    "panel_soft": "#fbfdff",
    "toolbar": "#eaf1fb",
    "border": "#d8e2f0",
    "primary": "#2563eb",
    "primary_soft": "#dbeafe",
    "success": "#059669",
    "success_soft": "#dcfce7",
    "danger": "#dc2626",
    "danger_soft": "#fee2e2",
    "warning": "#b45309",
    "warning_soft": "#fef3c7",
    "text": "#1f2937",
    "muted": "#64748b",
    "font": "Microsoft YaHei UI",
}


def _paint_button(button, bg, fg, active_bg, active_fg="#ffffff"):
    """按统一色板配置普通 tk.Button 样式。"""
    button.configure(
        bg=bg,
        fg=fg,
        activebackground=active_bg,
        activeforeground=active_fg,
        relief="flat",
        bd=0,
        cursor="hand2",
        padx=10,
        pady=3,
        font=(DETECTOR_THEME["font"], 10),
    )


# ============================================================================
# Windows API 获取鼠标位置
# ============================================================================

def _get_mouse_position():
    """使用 Windows API 获取当前鼠标位置"""
    try:
        import ctypes
        from ctypes import wintypes
        
        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]
        
        pt = POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x, pt.y
    except Exception:
        return 0, 0


# ============================================================================
# 公共工具函数（直接从 build_control_map_library 复制，避免导入冲突）
# ============================================================================

def _safe_get_value(callable_obj, default):
    """安全调用函数，避免异常导致程序崩溃"""
    try:
        return callable_obj()
    except Exception:
        return default


def _get_wrapper_handle(wrapper):
    """获取控件的句柄"""
    if wrapper is None:
        return None
    try:
        element_info = wrapper.element_info
        if element_info is not None:
            return getattr(element_info, "handle", None)
    except Exception:
        pass
    return None


def _build_wrapper_identity(wrapper):
    """构建控件的唯一标识"""
    if wrapper is None:
        return ""
    
    rect = _rect_to_dict(_safe_get_value(lambda: wrapper.rectangle(), None))
    rect_key = ""
    if rect:
        rect_key = f"{rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
    
    runtime_id = _get_wrapper_runtime_id(wrapper)
    handle = str(_get_wrapper_handle(wrapper) or "")
    
    def safe_get_text():
        return str(wrapper.window_text() or "").strip()
    
    def safe_get_class():
        return str(wrapper.class_name() or "").strip()
    
    def safe_get_ctrl_type():
        elem = wrapper.element_info
        if elem is not None:
            return getattr(elem, "control_type", "") or ""
        return ""
    
    return "|".join([
        runtime_id,
        handle,
        safe_get_text(),
        safe_get_class(),
        normalize_control_type_name(safe_get_ctrl_type(), ""),
        rect_key,
    ])


def _get_wrapper_runtime_id(wrapper):
    """获取控件的运行时 ID"""
    if wrapper is None:
        return ""
    try:
        elem = wrapper.element_info
        if elem is not None:
            rid = getattr(elem, "runtime_id", None)
            if rid:
                return str(rid)
    except Exception:
        pass
    return ""


def normalize_control_type_name(control_type, localized_control_type):
    """标准化控件类型名称"""
    if control_type:
        return str(control_type).strip()
    if localized_control_type:
        return str(localized_control_type).strip()
    return ""


def _rect_to_dict(rect):
    """将 rect 对象转换为字典"""
    if rect is None:
        return None
    try:
        return {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
        }
    except Exception:
        return None


def _build_path_segments_from_wrapper(wrapper, root_handle=""):
    """构建控件的路径段列表"""
    segments = []
    current = wrapper
    seen = set()
    
    for _ in range(32):
        if current is None:
            break
        
        identity = _build_wrapper_identity(current)
        if identity in seen:
            break
        seen.add(identity)
        
        def safe_get_text():
            return str(current.window_text() or "").strip()
        
        def safe_get_class():
            return str(current.class_name() or "").strip()
        
        def safe_get_aid():
            elem = current.element_info
            if elem is not None:
                return getattr(elem, "automation_id", "") or ""
            return ""
        
        def safe_get_ct():
            elem = current.element_info
            if elem is not None:
                return getattr(elem, "control_type", "") or ""
            return ""
        
        def safe_get_lct():
            elem = current.element_info
            if elem is not None:
                return getattr(elem, "localized_control_type", "") or ""
            return ""
        
        parsed = {
            "name": safe_get_text() or getattr(current.element_info, "name", "") or "",
            "automationId": safe_get_aid(),
            "className": safe_get_class(),
            "controlType": safe_get_ct(),
            "localizedControlType": safe_get_lct(),
        }
        segments.append(_build_display_name(parsed, "控件", len(segments) + 1))
        
        current_handle = _get_wrapper_handle(current)
        if root_handle and current_handle and str(current_handle) == str(root_handle):
            break
        
        parent = _safe_get_value(lambda: current.parent(), None)
        if not parent or parent is current:
            break
        current = parent
    
    return list(reversed(segments))


def _build_display_name(parsed, fallback, index):
    """构建控件的显示名称"""
    name = str(parsed.get("name", "")).strip()
    aid = str(parsed.get("automationId", "")).strip()
    ctrl_type = str(parsed.get("controlType", "")).strip()
    localized_type = str(parsed.get("localizedControlType", "")).strip()
    class_name = str(parsed.get("className", "")).strip()
    
    if name:
        return name
    if aid:
        return f"{ctrl_type or localized_type or fallback}[{aid}]"
    if class_name:
        return f"{ctrl_type or localized_type or fallback}[{class_name}]"
    return f"{ctrl_type or localized_type or fallback}#{index}"


# ============================================================================
# 控件库加载 - 总库优先，找不到再遍历其他库
# ============================================================================

def load_master_library():
    """加载用户总控件库（总控件信息.json）"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    # 控件库重整后 总控件信息.json 位于 control_maps/standard/ 下；兼容旧的根目录位置。
    candidate_paths = [
        os.path.join(base_dir, "control_maps", "standard", "总控件信息.json"),
        os.path.join(base_dir, "control_maps", "总控件信息.json"),
    ]
    for master_path in candidate_paths:
        if os.path.isfile(master_path):
            try:
                data = json.load(open(master_path, encoding="utf-8"))
                controls = data.get("flatControls", [])
                if controls:
                    return controls, master_path
            except Exception as e:
                print(f"[控件库] 加载总控件库失败: {e}")

    return [], ""


_EXCLUDED_LIBRARY_DIRS = {
    # recordings/ 为原始采集素材（2026-09 实测 95 个文件、约 1 GB，单文件最大 51 MB），
    # 不属于标准匹配池；排除后检测器启动加载量从 ~1.35 GB 降到 ~几 MB 量级。
    # 决策时间：2026-09-29（见 docs/ai_session_17_未审计模块UI审计_20260929.md P0-1）。
    "recordings",
}


def load_all_other_libraries(base_dir=None):
    """加载除总控件库外的所有控件库（递归遍历 control_maps 及其子目录）。

    recordings/ 等采集素材目录被排除（见 _EXCLUDED_LIBRARY_DIRS）。
    base_dir 仅供测试注入；默认取本文件所在目录。
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    control_map_dir = os.path.join(base_dir, "control_maps")

    all_controls = []
    sources = []

    if not os.path.isdir(control_map_dir):
        return all_controls, sources

    skip_names = {
        "standard_control_catalog.json",
        "standard_catalog_mismatch_report.json",
        "总控件信息.json",
    }
    # 控件库重整后文件分布在 library/ recordings/ _candidates/ 等子目录，需递归扫描。
    for root_dir, sub_dirs, file_names in os.walk(control_map_dir):
        # 剪枝：不进入排除目录（否则仍会遍历其大量文件）
        sub_dirs[:] = [d for d in sub_dirs if d not in _EXCLUDED_LIBRARY_DIRS]
        for fname in file_names:
            if not fname.lower().endswith(".json"):
                continue
            if fname in skip_names:
                continue

            fpath = os.path.join(root_dir, fname)
            before_count = len(all_controls)
            try:
                data = json.load(open(fpath, encoding="utf-8"))

                for ctrl in data.get("flatControls", []):
                    ctrl["_source_file"] = fname
                    all_controls.append(ctrl)

                for ctrl in data.get("controlDefinitions", []):
                    ctrl["_source_file"] = fname
                    all_controls.append(ctrl)

                # 按「本文件是否实际贡献控件」判定来源：
                # 此前用累计列表判定，一个文件有货后其余空文件也会被记为来源（审计 P2）
                if len(all_controls) > before_count:
                    sources.append(fname)

            except Exception:
                continue

    return all_controls, sources


# ============================================================================
# 鼠标位置控件捕获 - Smart 探测模式
# ============================================================================

# 可输入控件类型（用于下钻时优先级排序和静态文本回退查找）
_INPUT_CONTROL_TYPES = {"edit", "combobox", "spinner", "document"}


def _get_children_safe(wrapper, use_raw_view=False):
    """安全获取控件的直接子元素列表。

    use_raw_view=True 时通过 UIA COM API 的 FindAll 获取 Raw View 子元素，
    能看到 Content View（pywinauto 默认）过滤掉的装饰性/非控件元素
    （如 TextBlock、Border 等 IsControlElement=False 的节点）。
    失败时自动回退到 children()。
    """
    if use_raw_view:
        try:
            elem_info = wrapper.element_info
            # pywinauto UIA 后端将原始 IUIAutomationElement 存在 .element 属性
            uia_elem = getattr(elem_info, "element", None)
            if uia_elem is not None:
                from pywinauto.controls.uiawrapper import UIAWrapper
                from pywinauto.uia_element_info import UIAElementInfo
                import comtypes
                # TreeScope.Children = 2, 用 TrueCondition 匹配所有子元素（含 IsControlElement=False）
                tree_scope = 2  # TreeScope_Children
                true_cond = None
                try:
                    # 通过 CUIUIAutomation 获取 TrueCondition
                    uia_auto = comtypes.client.CreateObject(
                        "UIAutomationClient.CUIUIAutomation",
                        clsctx=comtypes.CLSCTX_INPROC_SERVER)
                    true_cond = uia_auto.CreateTrueCondition()
                except Exception:
                    pass
                if true_cond is not None:
                    raw_children = uia_elem.FindAll(tree_scope, true_cond)
                    if raw_children:
                        result = []
                        for i in range(raw_children.Length):
                            raw_el = raw_children.GetElement(i)
                            info = UIAElementInfo(raw_el)
                            result.append(UIAWrapper(info))
                        return result
        except Exception:
            pass
    try:
        return wrapper.children()
    except Exception:
        return []


def _collect_all_descendants(wrapper, use_raw_view=False, max_depth=50):
    """递归收集所有后代元素（不限深度）。

    用于全量遍历控件树场景（如 Inspect 式增量补采），确保深层嵌套的
    控件（如 TextBlock 在多层 Custom 容器内）不被遗漏。
    每个元素都用 try/except 保护，单个元素不可用不会中断遍历。
    """
    result = []
    stack = [(wrapper, 0)]
    seen_ids = set()
    while stack:
        current, depth = stack.pop()
        cid = id(current)
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        if depth >= max_depth:
            continue
        try:
            children = _get_children_safe(current, use_raw_view=use_raw_view)
        except Exception:
            continue
        for child in children:
            try:
                child_id = id(child)
                if child_id not in seen_ids:
                    result.append(child)
                    stack.append((child, depth + 1))
            except Exception:
                continue
    return result


def _get_wrapper_control_type(wrapper):
    """安全获取 wrapper 的 ControlType 字符串（小写）。"""
    try:
        elem = wrapper.element_info
        if elem is not None:
            return str(getattr(elem, "control_type", "") or "").strip().lower()
    except Exception:
        pass
    return ""


def _get_input_priority(control_type):
    """返回下钻优先级 — 越小越优先。可输入控件 0-3，其余 100。"""
    ct = str(control_type or "").strip().lower()
    if ct == "edit":
        return 0
    if ct == "combobox":
        return 1
    if ct == "spinner":
        return 2
    if ct == "document":
        return 3
    return 100


def _drill_deepest_interactive(wrapper, x, y, depth=0, max_depth=50, use_raw_view=False):
    """递归下钻：找到包含 (x,y) 的最深控件，同层多个候选时优先可输入类型。

    与旧版仅下钻一层的逻辑不同，这里会持续向下直到没有更深的子控件
    或到达 max_depth，确保不会因为浅层遍历漏掉嵌套的 Edit/ComboBox。

    use_raw_view=True 时使用 Raw View 遍历，能看到 Content View 过滤掉的
    装饰性控件（如 TextBlock、Border 等），与 Inspect 工具的 Raw View 对齐。
    max_depth 默认 50，足以覆盖任意深层嵌套的 WPF 控件树。
    """
    if depth >= max_depth:
        return wrapper
    try:
        children = _get_children_safe(wrapper, use_raw_view=use_raw_view)
    except Exception:
        return wrapper
    # 收集所有包含 (x,y) 的直接子控件
    candidates = []
    try:
        parent_rect = wrapper.rectangle()
    except Exception:
        parent_rect = None
    for child in children:
        try:
            child_rect = child.rectangle()
            if (child_rect.left <= x <= child_rect.right and
                    child_rect.top <= y <= child_rect.bottom):
                if parent_rect is not None and _rect_eq(child_rect, parent_rect):
                    continue
                candidates.append(child)
        except Exception:
            continue
    if not candidates:
        return wrapper
    # 按输入优先级选出最佳候选
    best = candidates[0]
    best_prio = 999
    for child in candidates:
        ct = _get_wrapper_control_type(child)
        prio = _get_input_priority(ct)
        if prio < best_prio:
            best = child
            best_prio = prio
    if best is None:
        return wrapper
    return _drill_deepest_interactive(best, x, y, depth + 1, max_depth, use_raw_view=use_raw_view)


def _rect_eq(a, b):
    """比较两个 rect 是否完全相等。"""
    try:
        return (a.left == b.left and a.top == b.top and
                a.right == b.right and a.bottom == b.bottom)
    except Exception:
        return False


def _is_static_text(wrapper):
    """判断 wrapper 是否是静态 Text/Label（非可输入控件）。"""
    return _get_wrapper_control_type(wrapper) == "text"


def _find_nearest_editable_sibling(wrapper, x, y):
    """当 wrapper 是静态 Text 时，在其父容器兄弟中查找同行的输入框。

    匹配条件：ControlType 属于可输入类型、竖直方向与标签重叠（同行）、
    水平方向在标签右侧或与标签重叠。返回距离最近的候选；无候选返回 None。
    """
    try:
        wrapper_rect = wrapper.rectangle()
        parent = wrapper.parent()
        if parent is None:
            return None
        siblings = parent.children()
    except Exception:
        return None
    candidates = []
    for sib in siblings:
        if sib is wrapper:
            continue
        try:
            ct = _get_wrapper_control_type(sib)
            if ct not in _INPUT_CONTROL_TYPES:
                continue
            sib_rect = sib.rectangle()
            # 竖直方向有重叠 = 同一行
            y_overlap = not (sib_rect.bottom <= wrapper_rect.top or
                             sib_rect.top >= wrapper_rect.bottom)
            if not y_overlap:
                continue
            # 水平方向在标签右侧或与标签重叠
            if sib_rect.left < wrapper_rect.left - 30:
                continue
            # 距离 = 输入框左边 到 标签右边（可能为负，即重叠）
            distance = sib_rect.left - wrapper_rect.right
            if distance >= -30:  # 允许少量重叠
                candidates.append((abs(distance), distance, sib))
        except Exception:
            continue
    if not candidates:
        return None
    # 水平距离最小的优先
    candidates.sort(key=lambda pair: pair[0])
    return candidates[0][2]


def get_control_at_mouse_position(backend="uia", max_depth=8, smart_mode=True, use_raw_view=False):
    """获取鼠标当前位置下的控件信息
    
    smart_mode=True 时同时用 uia 和 win32 两个后端探测，返回结果列表；
    smart_mode=False 时仅用指定后端，返回单个结果（兼容旧调用）。
    
    use_raw_view=False 时默认关闭。当 True 时，UIA 后端使用 Raw View 遍历
    控件树，能看到 Content View 过滤掉的装饰性/非控件元素（如 TextBlock、
    Border 等 IsControlElement=False 的节点），与 Inspect 工具的 Raw View
    模式对齐。Win32 后端不受此参数影响。
    """
    x, y = _get_mouse_position()

    if smart_mode:
        results = []
        for be in ("uia", "win32"):
            # Raw View 仅对 UIA 后端生效
            rv = use_raw_view and be == "uia"
            r = _get_control_single(x, y, be, max_depth, use_raw_view=rv)
            if r is not None:
                results.append(r)
        return results if results else None
    else:
        rv = use_raw_view and backend == "uia"
        r = _get_control_single(x, y, backend, max_depth, use_raw_view=rv)
        return [r] if r is not None else None


def _get_control_single(x, y, backend, max_depth, use_raw_view=False):
    """使用指定后端获取控件信息
    
    use_raw_view=True 时下钻遍历使用 Raw View，能看到更多控件。
    """
    from pywinauto import Desktop
    
    try:
        desktop = Desktop(backend=backend)
        wrapper = _safe_get_value(lambda: desktop.from_point(x, y), None)
        
        if wrapper is None:
            return None
        
        # ① 递归下钻：找到包含 (x,y) 的最深控件，同层多个候选时优先可输入类型
        #    use_raw_view=True 时通过 Raw View 遍历，不遗漏 IsControlElement=False 的控件
        target_wrapper = _drill_deepest_interactive(wrapper, x, y, use_raw_view=use_raw_view)
        if target_wrapper is not None:
            wrapper = target_wrapper
        
        # ② 静态文本回退：如果结果仍是静态 Text/Label，尝试找同行的相邻输入框
        if _is_static_text(wrapper):
            sibling = _find_nearest_editable_sibling(wrapper, x, y)
            if sibling is not None:
                wrapper = sibling
        
        # ③ 富字段抽取：复用 WT控件库采集器 的同源管线，产出与控件库完全一致的
        #    字段（归一化 controlType、recommendedTargetValue、字符串 ancestors 等），
        #    使实时采集能力与库采集器对齐；失败时回退到轻量抽取，保证不中断。
        return _extract_control_info_rich(wrapper, backend, x, y, max_depth)
        
    except Exception as e:
        print(f"获取控件失败: {e}")
        return None


def _extract_control_info_rich(wrapper, backend, x, y, max_depth):
    """复用 build_control_map_library 的富字段抽取管线，产出与控件库同构的记录。

    这样实时监测器捕获的控件与“WT控件库采集器”写入库的控件字段一致
    （归一化的 controlType、recommendedTargetMethod/Value、frameworkId、
    ValuePattern value、字符串格式 ancestors 等），匹配时可直接同源比对。
    附加 backend/x/y/pathSegments 及若干兼容别名供实时界面与匹配使用。
    """
    try:
        import build_control_map_library as clb
    except Exception:
        return _extract_control_info_thin(wrapper, backend, x, y, max_depth)

    try:
        # 定位顶层窗口，用于构建 target_window 与截断 path_segments
        top_window = _safe_get_value(lambda: wrapper.top_level_parent(), None)
        root_handle = ""
        if top_window is not None:
            root_handle = str(clb._get_wrapper_handle(top_window) or "")
            target_window = clb._build_target_window_info(top_window)
        else:
            target_window = {"title": "", "className": "", "processId": "", "handle": "", "frameworkId": ""}

        path_segments = clb._build_path_segments_from_wrapper(wrapper, root_handle=root_handle)
        depth = max(0, len(path_segments) - 1)
        info = clb._extract_wrapper_info(wrapper, depth, 1, path_segments, target_window)

        # 实时监测器专用字段
        info["backend"] = backend
        info["x"] = x
        info["y"] = y
        info["pathSegments"] = path_segments
        # 兼容旧界面/匹配读取的键名
        info["windowClass"] = info.get("windowClassName", "")
        info["rectangle"] = info.get("boundingBox")
        inspect = info.get("inspectData", {}) or {}
        info["ancestors"] = list(inspect.get("ancestors", []))
        return info
    except Exception:
        return _extract_control_info_thin(wrapper, backend, x, y, max_depth)


def _extract_control_info_thin(wrapper, backend, x, y, max_depth):
    """轻量抽取（原始逻辑）——作为富字段抽取失败时的回退，保证采集不中断。"""
    # 获取顶层窗口
    root_handle = ""
    try:
        top_window = _safe_get_value(lambda: wrapper.top_level_parent(), None)
        if top_window:
            root_handle = str(_get_wrapper_handle(top_window) or "")
    except Exception:
        pass

    # 构建路径段
    path_segments = _build_path_segments_from_wrapper(wrapper, root_handle=root_handle)

    # 获取父级链信息
    window_title = ""
    window_class = ""
    ancestors = []

    current = wrapper
    for _ in range(max_depth):
        parent = _safe_get_value(lambda: current.parent(), None)
        if parent is None:
            break

        parent_name = str(_safe_get_value(lambda: parent.window_text(), "")).strip()
        parent_class = str(_safe_get_value(lambda: parent.class_name(), "")).strip()

        def safe_get_parent_ct():
            elem = parent.element_info
            if elem is not None:
                return getattr(elem, "control_type", "") or ""
            return ""

        parent_type = safe_get_parent_ct()

        if not window_title and (parent_type.lower() == "window" or "window" in parent_class.lower()):
            window_title = parent_name
            window_class = parent_class
            break

        if parent_name or parent_class:
            ancestors.append({
                "name": parent_name,
                "className": parent_class,
                "controlType": parent_type,
            })

        current = parent

    # 获取当前控件信息
    name = str(_safe_get_value(lambda: wrapper.window_text(), "")).strip()
    class_name = str(_safe_get_value(lambda: wrapper.class_name(), "")).strip()

    def safe_get_ctrl_type():
        elem = wrapper.element_info
        if elem is not None:
            return getattr(elem, "control_type", "") or ""
        return ""

    def safe_get_auto_id():
        elem = wrapper.element_info
        if elem is not None:
            return getattr(elem, "automation_id", "") or ""
        return ""

    ctrl_type = safe_get_ctrl_type()
    auto_id = safe_get_auto_id()
    handle = str(_get_wrapper_handle(wrapper) or "")

    # 获取位置
    rect = None
    try:
        rect_obj = wrapper.rectangle()
        rect = {
            "left": rect_obj.left,
            "top": rect_obj.top,
            "right": rect_obj.right,
            "bottom": rect_obj.bottom,
        }
    except Exception:
        pass

    return {
        "backend": backend,
        "x": x,
        "y": y,
        "name": name,
        "automationId": auto_id,
        "controlType": ctrl_type,
        "className": class_name,
        "handle": handle,
        "windowTitle": window_title,
        "windowClass": window_class,
        "rectangle": rect,
        "ancestors": ancestors,
        "pathSegments": path_segments,
    }


# ============================================================================
# 控件库匹配
# ============================================================================

def _normalize_ancestor_tokens(ancestors):
    """将祖先链规整为小写 token 集合，兼容字符串列表与字典列表两种格式。

    新版增强采集把 inspectData.ancestors 存成字符串列表（uiPath 段，如
    'Window'、'MUPGeographicalDataCreatorView'）；旧版与实时捕获则是带
    name/className/controlType 的字典列表。统一抽取为可比较的 token 集合，
    比较时与顺序无关，避免格式差异导致 .get() 异常使整个匹配静默失败。
    """
    tokens = set()
    if not isinstance(ancestors, (list, tuple)):
        return tokens
    for anc in ancestors:
        if isinstance(anc, str):
            token = anc.strip().lower()
            if token:
                tokens.add(token)
        elif isinstance(anc, dict):
            for field in ("name", "className", "controlType"):
                token = str(anc.get(field, "") or "").strip().lower()
                if token:
                    tokens.add(token)
    return tokens


def match_control_to_library(ctrl_info, master_controls, other_controls):
    """将捕获的控件与控件库进行匹配"""
    if not ctrl_info:
        return []
    
    captured_aid = str(ctrl_info.get("automationId", "")).strip().lower()
    captured_name = str(ctrl_info.get("name", "")).strip().lower()
    captured_ct = str(ctrl_info.get("controlType", "")).strip().lower()
    captured_cn = str(ctrl_info.get("className", "")).strip().lower()
    captured_win = str(ctrl_info.get("windowTitle", "")).strip().lower()
    
    all_matches = []
    matched_ids = set()
    
    # 1. 先匹配总控件库
    for lib_ctrl in master_controls:
        match = _score_single_control(ctrl_info, lib_ctrl, captured_aid, captured_name, 
                                    captured_ct, captured_cn, captured_win, source="总控件库")
        if match:
            lib_def = match["library_definition"]
            match_id = f"{lib_def.get('automationId', '')}|{lib_def.get('windowTitle', '')}"
            if match_id not in matched_ids:
                matched_ids.add(match_id)
                all_matches.append(match)
    
    # 2. 再匹配其他控件库
    for lib_ctrl in other_controls:
        match = _score_single_control(ctrl_info, lib_ctrl, captured_aid, captured_name,
                                    captured_ct, captured_cn, captured_win, source="控件库")
        if match:
            lib_def = match["library_definition"]
            match_id = f"{lib_def.get('automationId', '')}|{lib_def.get('windowTitle', '')}"
            if match_id not in matched_ids:
                matched_ids.add(match_id)
                all_matches.append(match)
    
    all_matches.sort(key=lambda x: x["score"], reverse=True)
    return all_matches[:20]


def _score_single_control(ctrl_info, lib_ctrl, captured_aid, captured_name,
                          captured_ct, captured_cn, captured_win, source):
    """对单个控件库条目评分"""
    lib_def = _extract_lib_control_def(lib_ctrl, source)
    
    lib_aid = str(lib_def.get("automationId", "")).strip().lower()
    lib_name = str(lib_def.get("name", "")).strip().lower()
    lib_ct = str(lib_def.get("controlType", "")).strip().lower()
    lib_cn = str(lib_def.get("className", "")).strip().lower()
    lib_win = str(lib_def.get("windowTitle", "")).strip().lower()
    
    score = 0
    reasons = []

    # 主匹配：与运行时 find_flow_control 同源的 targetMethod/targetValue 定位。
    # 命中即视为"捕获控件就是该库条目运行时能定位到的控件"，与执行定位保持一致。
    if _match_lib_locator(ctrl_info, lib_ctrl):
        score += 120
        reasons.append("定位器匹配")

    # AutomationId 完全匹配
    if captured_aid and lib_aid and captured_aid == lib_aid:
        score += 100
        reasons.append("AID完全匹配")
    elif captured_aid and lib_aid and (captured_aid in lib_aid or lib_aid in captured_aid):
        score += 40
        reasons.append("AID部分匹配")
    
    # Name 匹配
    if captured_name and lib_name:
        if captured_name == lib_name:
            score += 30
            reasons.append("Name匹配")
        elif captured_name in lib_name or lib_name in captured_name:
            score += 15
            reasons.append("Name包含")
    
    # ControlType 匹配
    if captured_ct and lib_ct and captured_ct == lib_ct:
        score += 20
        reasons.append("Type匹配")
    
    # ClassName 匹配
    if captured_cn and lib_cn and captured_cn == lib_cn:
        score += 15
        reasons.append("Class匹配")
    
    # 窗口标题匹配
    if captured_win and lib_win:
        if captured_win == lib_win:
            score += 10
        elif captured_win in lib_win or lib_win in captured_win:
            score += 5
            
    # 层级结构 (Ancestors) 匹配 —— 兼容字符串/字典两种祖先格式，按 token 交集给分，
    # 与顺序无关。旧逻辑对新版库的字符串祖先调 .get() 会抛 AttributeError，
    # 在监控线程被静默吞掉，导致库中控件全部匹配不上。
    captured_tokens = _normalize_ancestor_tokens(ctrl_info.get("ancestors", []))
    inspect_data = lib_ctrl.get("inspectData", {}) if isinstance(lib_ctrl.get("inspectData"), dict) else {}
    lib_ancestor_tokens = _normalize_ancestor_tokens(inspect_data.get("ancestors", []))
    # 库条目若无 inspectData.ancestors，退而从 parentPath 拆出层级线索
    if not lib_ancestor_tokens:
        lib_ancestor_tokens = _normalize_ancestor_tokens(
            [seg.strip() for seg in str(lib_ctrl.get("parentPath", "")).split(">") if seg.strip()]
        )

    if captured_tokens and lib_ancestor_tokens:
        overlap = len(captured_tokens & lib_ancestor_tokens)
        if overlap > 0:
            bonus = min(overlap * 5, 30)  # 最多奖励 30 分
            score += bonus
            reasons.append(f"层级匹配({overlap})")
    
    if score >= 20:
        return {
            "score": score,
            "reasons": reasons,
            "library_control": lib_ctrl,
            "library_definition": lib_def,
        }
    
    return None


def _extract_lib_control_def(lib_ctrl, source=""):
    """从控件库条目提取控件定义"""
    inspect_data = lib_ctrl.get("inspectData", {}) if isinstance(lib_ctrl.get("inspectData"), dict) else {}
    
    target_value = (
        str(lib_ctrl.get("targetValue", "")).strip()
        or str(lib_ctrl.get("recommendedTargetValue", "")).strip()
        or str(inspect_data.get("recommendedTargetValue", "")).strip()
    )
    target_method = (
        str(lib_ctrl.get("targetMethod", "")).strip()
        or str(lib_ctrl.get("recommendedTargetMethod", "")).strip()
        or str(inspect_data.get("recommendedTargetMethod", "")).strip()
    )
    
    automation_id = (
        str(inspect_data.get("automationId", "")).strip()
        or str(lib_ctrl.get("automationId", "")).strip()
    )
    
    return {
        "name": (
            str(lib_ctrl.get("name", "")).strip()
            or str(lib_ctrl.get("displayName", "")).strip()
            or str(inspect_data.get("name", "")).strip()
        ),
        "targetMethod": target_method,
        "targetValue": target_value,
        "windowTitle": str(lib_ctrl.get("windowTitle", "") or "").strip(),
        "controlType": str(inspect_data.get("controlType", "") or lib_ctrl.get("controlType", "")).strip(),
        "automationId": automation_id,
        "className": str(inspect_data.get("className", "") or lib_ctrl.get("className", "")).strip(),
        "authority": str(lib_ctrl.get("authority", "low")).strip(),
        "sourceFile": str(lib_ctrl.get("_source_file", source)).strip(),
    }


def _make_pseudo_wrapper(ctrl_info):
    """把实时捕获的控件 dict 构造成 wt_flow_locator 可读的伪 wrapper。

    wt_flow_locator 的匹配（wrapper_matches_locator / build_common_locator_candidates）
    通过 wrapper.window_text() / class_name() 与 element_info 字段读取控件属性，
    这里用捕获到的字段构造等价对象，使两套逻辑共用同一匹配实现。
    """
    ei = SimpleNamespace(
        control_type=ctrl_info.get("controlType", ""),
        localized_control_type=ctrl_info.get("localizedControlType", ""),
        automation_id=ctrl_info.get("automationId", ""),
        class_name=ctrl_info.get("className", ""),
        framework_id=ctrl_info.get("frameworkId", ""),
        help_text=ctrl_info.get("helpText", ""),
        process_id=ctrl_info.get("processId", ""),
        name=ctrl_info.get("name", ""),
    )
    wrapper = SimpleNamespace(element_info=ei)
    wrapper.window_text = lambda: str(ctrl_info.get("name", "") or "")
    wrapper.class_name = lambda: str(ctrl_info.get("className", "") or "")
    return wrapper


def _match_lib_locator(ctrl_info, lib_ctrl):
    """用 wt_flow_locator 的候选匹配语义，判断捕获控件是否命中库条目的定位器。

    与运行时 find_flow_control 同源：对库条目生成定位候选，再用捕获控件字段匹配，
    避免 live_detector 与执行定位两套匹配逻辑不一致。
    """
    try:
        import wt_flow_locator as flow_locator
        wrapper = _make_pseudo_wrapper(ctrl_info)
        for method, value in flow_locator.build_common_locator_candidates(lib_ctrl):
            if method and value and flow_locator.wrapper_matches_locator(wrapper, method, value):
                return True
    except Exception:
        pass
    return False


# ============================================================================
# 实时检测窗口
# ============================================================================

class ControlLiveDetectorWindow:
    """实时控件检测器窗口"""
    
    def __init__(self, parent, flow_editor=None, on_inject=None, start_in_hud=False):
        self.parent = parent
        self.flow_editor = flow_editor
        self.on_inject = on_inject
        self.hud_mode = False
        self.saved_geometry = "1100x780"
        self.monitoring = False
        self.paused = False
        self.monitor_thread = None
        
        self.master_controls = []
        self.other_controls = []
        
        self.current_matches = []
        self.current_ctrl_info = None
        self.saved_controls = []
        
        self.window = tk.Toplevel(parent)
        self.window.title("实时控件检测器")
        self.window.geometry("1100x780")
        self.window.minsize(1000, 700)
        self.window.protocol("WM_DELETE_WINDOW", self._on_window_close)
        
        self._apply_theme()
        self._build_ui()
        self._load_library()
        if start_in_hud:
            self.window.after(50, self.toggle_hud_mode)

    def _on_window_close(self):
        """窗口关闭时安全终止后台监控线程并销毁。"""
        try:
            self.stop_monitoring()
        except Exception:
            pass
        try:
            getattr(self.window, "destroy")()
        except Exception:
            pass

    def winfo_exists(self):
        try:
            return bool(self.window and self.window.winfo_exists())
        except Exception:
            return False

    def lift(self):
        try:
            if self.window and self.window.winfo_exists():
                self.window.lift()
        except Exception:
            pass

    def focus_force(self):
        try:
            if self.window and self.window.winfo_exists():
                self.window.focus_force()
        except Exception:
            pass

    def deiconify(self):
        try:
            if self.window and self.window.winfo_exists():
                self.window.deiconify()
        except Exception:
            pass
    
    def _apply_theme(self):
        """应用统一浅色蓝灰主题。"""
        t = DETECTOR_THEME
        self.window.configure(bg=t["bg"])
        style = ttk.Style(self.window)
        style.configure(
            "Detector.TCombobox",
            fieldbackground=t["panel_soft"],
            background=t["panel_soft"],
            foreground=t["text"],
            arrowcolor=t["primary"],
            padding=3,
        )
        style.map(
            "Detector.TCombobox",
            fieldbackground=[("readonly", t["panel_soft"])],
            foreground=[("readonly", t["text"])],
            selectbackground=[("readonly", t["primary_soft"])],
            selectforeground=[("readonly", t["text"])],
        )
        style.configure(
            "Detector.Treeview",
            background=t["panel_soft"],
            fieldbackground=t["panel_soft"],
            foreground=t["text"],
            rowheight=26,
            bordercolor=t["border"],
        )
        style.map(
            "Detector.Treeview",
            background=[("selected", t["primary"])],
            foreground=[("selected", "#ffffff")],
        )
        style.configure(
            "Detector.Treeview.Heading",
            background=t["toolbar"],
            foreground=t["text"],
            relief="flat",
            font=(t["font"], 9, "bold"),
        )
        style.map(
            "Detector.Treeview.Heading",
            background=[("active", t["primary_soft"])],
        )
    
    def _build_ui(self):
        self.standard_container = tk.Frame(self.window, bg=DETECTOR_THEME["bg"])
        self.standard_container.pack(fill=tk.BOTH, expand=True)

        toolbar = tk.Frame(self.standard_container, bg=DETECTOR_THEME["toolbar"], padx=10, pady=6)
        toolbar.pack(fill=tk.X)
        
        self.smart_var = tk.BooleanVar(value=True)
        tk.Checkbutton(toolbar, text="双后端模式(同时UIA+Win32)", variable=self.smart_var, bg=DETECTOR_THEME["toolbar"], fg=DETECTOR_THEME["text"], selectcolor=DETECTOR_THEME["panel_soft"], activebackground=DETECTOR_THEME["toolbar"], activeforeground=DETECTOR_THEME["primary"], font=(DETECTOR_THEME["font"], 10)).pack(side=tk.LEFT)
        
        # Raw View 开关：启用后使用 UIA Raw View 遍历，能看到 Content View 过滤掉的控件
        # （注意：tk.Checkbutton 没有 help/tooltip 选项，说明放在提示行文本里）
        self.raw_view_var = tk.BooleanVar(value=False)
        tk.Checkbutton(toolbar, text="Raw View", variable=self.raw_view_var, bg=DETECTOR_THEME["toolbar"], fg=DETECTOR_THEME["text"], selectcolor=DETECTOR_THEME["panel_soft"], activebackground=DETECTOR_THEME["toolbar"], activeforeground=DETECTOR_THEME["primary"], font=(DETECTOR_THEME["font"], 10)).pack(side=tk.LEFT, padx=(10, 0))
        
        # 窗口置顶开关：监测时保持窗口在最前，避免被目标应用覆盖
        self.topmost_var = tk.BooleanVar(value=True)
        tk.Checkbutton(toolbar, text="窗口置顶", variable=self.topmost_var,
                        command=self._toggle_topmost, bg=DETECTOR_THEME["toolbar"], fg=DETECTOR_THEME["text"],
                        selectcolor=DETECTOR_THEME["panel_soft"], activebackground=DETECTOR_THEME["toolbar"],
                        activeforeground=DETECTOR_THEME["primary"], font=(DETECTOR_THEME["font"], 10)).pack(side=tk.LEFT, padx=(10, 0))
        
        tk.Label(toolbar, text="后端:", bg=DETECTOR_THEME["toolbar"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10)).pack(side=tk.LEFT, padx=(10, 0))
        self.backend_var = tk.StringVar(value="uia")
        backend_combo = ttk.Combobox(
            toolbar, textvariable=self.backend_var,
            values=["uia", "win32"], width=8, state="readonly", style="Detector.TCombobox"
        )
        backend_combo.pack(side=tk.LEFT, padx=(0, 10))
        
        tk.Label(toolbar, text="深度:", bg=DETECTOR_THEME["toolbar"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10)).pack(side=tk.LEFT)
        self.depth_var = tk.IntVar(value=8)
        depth_spin = tk.Spinbox(toolbar, from_=3, to=20, textvariable=self.depth_var, width=5, bg=DETECTOR_THEME["panel_soft"], fg=DETECTOR_THEME["text"], buttonbackground=DETECTOR_THEME["toolbar"], insertbackground=DETECTOR_THEME["text"], relief="solid", bd=1, font=(DETECTOR_THEME["font"], 10))
        depth_spin.pack(side=tk.LEFT, padx=(0, 10))
        
        self.status_label = tk.Label(toolbar, text="状态: 未开始", fg=DETECTOR_THEME["muted"], bg=DETECTOR_THEME["toolbar"], width=25, font=(DETECTOR_THEME["font"], 10, "bold"))
        self.status_label.pack(side=tk.LEFT)
        
        self.start_btn = tk.Button(toolbar, text="开始检测", command=self.start_monitoring, width=9)
        _paint_button(self.start_btn, DETECTOR_THEME["success_soft"], DETECTOR_THEME["success"], DETECTOR_THEME["success"])
        self.start_btn.pack(side=tk.LEFT, padx=2)
        
        self.pause_btn = tk.Button(toolbar, text="暂停[Space]", command=self.toggle_pause, width=9, state="disabled")
        _paint_button(self.pause_btn, DETECTOR_THEME["warning_soft"], DETECTOR_THEME["warning"], DETECTOR_THEME["warning"])
        self.pause_btn.pack(side=tk.LEFT, padx=2)
        
        self.stop_btn = tk.Button(toolbar, text="停止", command=self.stop_monitoring, width=6, state="disabled")
        _paint_button(self.stop_btn, DETECTOR_THEME["danger_soft"], DETECTOR_THEME["danger"], DETECTOR_THEME["danger"])
        self.stop_btn.pack(side=tk.LEFT, padx=2)
        
        self.save_btn = tk.Button(toolbar, text="保存[Enter]", command=self.save_current_match, width=9, state="disabled")
        _paint_button(self.save_btn, DETECTOR_THEME["primary_soft"], DETECTOR_THEME["primary"], DETECTOR_THEME["primary"])
        self.save_btn.pack(side=tk.LEFT, padx=2)
        
        self.refresh_btn = tk.Button(toolbar, text="刷新库", command=self._load_library, width=7)
        _paint_button(self.refresh_btn, DETECTOR_THEME["panel"], DETECTOR_THEME["text"], DETECTOR_THEME["primary_soft"], active_fg=DETECTOR_THEME["primary"])
        self.refresh_btn.pack(side=tk.LEFT, padx=(10, 0))
        
        # ── 搜索控件按钮 ──
        self.search_btn = tk.Button(toolbar, text="🔍 搜索控件", command=self._open_search_dialog, width=10)
        _paint_button(self.search_btn, DETECTOR_THEME["primary_soft"], DETECTOR_THEME["primary"], DETECTOR_THEME["primary"])
        self.search_btn.pack(side=tk.LEFT, padx=(6, 0))

        # ── 合并入库按钮：将源采集文件的控件合并到目标控件库 ──
        self.merge_btn = tk.Button(toolbar, text="合并入库", command=self._open_merge_dialog, width=9)
        _paint_button(self.merge_btn, DETECTOR_THEME["primary_soft"], DETECTOR_THEME["primary"], DETECTOR_THEME["primary"])
        self.merge_btn.pack(side=tk.LEFT, padx=(6, 0))

        # ── 迷你 HUD 模式切换按钮 ──
        self.hud_btn = tk.Button(toolbar, text="📌 迷你 HUD", command=self.toggle_hud_mode, width=10)
        _paint_button(self.hud_btn, DETECTOR_THEME["primary_soft"], DETECTOR_THEME["primary"], DETECTOR_THEME["primary"])
        self.hud_btn.pack(side=tk.LEFT, padx=(6, 0))

        # ── 一键注入流程按钮 ──
        self.inject_btn = tk.Button(toolbar, text="💉 直注流程", command=self.inject_to_flow_action, width=10)
        _paint_button(self.inject_btn, DETECTOR_THEME["success_soft"], DETECTOR_THEME["success"], DETECTOR_THEME["success"])
        self.inject_btn.pack(side=tk.LEFT, padx=(6, 0))

        self.lib_count_label = tk.Label(toolbar, text="总库:0 其他:0", fg=DETECTOR_THEME["muted"], bg=DETECTOR_THEME["toolbar"])
        self.lib_count_label.pack(side=tk.RIGHT)
        
        tips = tk.Label(
            self.standard_container,
            text="提示: 移动鼠标查看控件 | Space暂停/继续 | Enter保存最佳匹配 | Esc停止 | F12切迷你HUD | Raw View=遍历原始视图",
            bg=DETECTOR_THEME["panel_soft"],
            fg=DETECTOR_THEME["muted"],
            anchor="w",
            padx=10,
            pady=4,
            font=(DETECTOR_THEME["font"], 9),
        )
        tips.pack(fill=tk.X)
        
        content = tk.PanedWindow(self.standard_container, orient=tk.HORIZONTAL, sashrelief=tk.RAISED, bg=DETECTOR_THEME["panel"])
        content.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        
        left_frame = tk.LabelFrame(content, text="捕获的控件信息", padx=8, pady=5, bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10, "bold"))
        content.add(left_frame, minsize=420)
        
        self.capture_text = scrolledtext.ScrolledText(
            left_frame, wrap=tk.WORD, font=("Consolas", 10), height=28,
            bg=DETECTOR_THEME["panel_soft"], fg=DETECTOR_THEME["text"],
            insertbackground=DETECTOR_THEME["text"],
            highlightbackground=DETECTOR_THEME["border"], highlightcolor=DETECTOR_THEME["primary"],
            highlightthickness=1, relief="flat",
        )
        self.capture_text.pack(fill=tk.BOTH, expand=True)
        
        right_frame = tk.LabelFrame(content, text="控件库匹配结果", padx=8, pady=5, bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10, "bold"))
        content.add(right_frame, minsize=500)
        
        list_frame = tk.Frame(right_frame, bg=DETECTOR_THEME["panel"])
        list_frame.pack(fill=tk.BOTH, expand=True)
        
        header_frame = tk.Frame(list_frame, bg=DETECTOR_THEME["panel"])
        header_frame.pack(fill=tk.X)
        tk.Label(header_frame, text="分数", width=6, anchor="center", bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["muted"], font=(DETECTOR_THEME["font"], 9, "bold")).pack(side=tk.LEFT)
        tk.Label(header_frame, text="控件名称", width=18, anchor="w", bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["muted"], font=(DETECTOR_THEME["font"], 9, "bold")).pack(side=tk.LEFT)
        tk.Label(header_frame, text="AutomationId", width=28, anchor="w", bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["muted"], font=(DETECTOR_THEME["font"], 9, "bold")).pack(side=tk.LEFT)
        tk.Label(header_frame, text="来源", width=12, anchor="w", bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["muted"], font=(DETECTOR_THEME["font"], 9, "bold")).pack(side=tk.LEFT)
        
        listbox_frame = tk.Frame(list_frame, bg=DETECTOR_THEME["panel"])
        listbox_frame.pack(fill=tk.BOTH, expand=True)
        
        scrollbar = tk.Scrollbar(listbox_frame, bg=DETECTOR_THEME["toolbar"], troughcolor=DETECTOR_THEME["panel"], activebackground=DETECTOR_THEME["primary"], relief="flat", bd=0)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.match_listbox = tk.Listbox(
            listbox_frame,
            font=(DETECTOR_THEME["font"], 10),
            yscrollcommand=scrollbar.set,
            height=25,
            bg=DETECTOR_THEME["panel_soft"],
            fg=DETECTOR_THEME["text"],
            selectbackground=DETECTOR_THEME["primary"],
            selectforeground="#ffffff",
            highlightbackground=DETECTOR_THEME["border"],
            highlightcolor=DETECTOR_THEME["primary"],
            highlightthickness=1,
            relief="flat",
            bd=0,
        )
        self.match_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.match_listbox.yview)
        
        self.match_listbox.bind("<<ListboxSelect>>", self._on_match_select)
        self.match_listbox.bind("<Double-1>", lambda e: self.save_current_match())
        
        self.match_detail = scrolledtext.ScrolledText(
            right_frame, wrap=tk.WORD, font=("Consolas", 9), height=6,
            bg=DETECTOR_THEME["panel_soft"], fg=DETECTOR_THEME["text"],
            insertbackground=DETECTOR_THEME["text"],
            highlightbackground=DETECTOR_THEME["border"], highlightcolor=DETECTOR_THEME["primary"],
            highlightthickness=1, relief="flat",
        )
        self.match_detail.pack(fill=tk.X, pady=(5, 0))
        
        bottom_bar = tk.Frame(self.standard_container, bg=DETECTOR_THEME["toolbar"], relief=tk.SUNKEN, bd=1)
        bottom_bar.pack(fill=tk.X)
        
        self.coord_label = tk.Label(bottom_bar, text="位置: (-, -)", bg=DETECTOR_THEME["toolbar"], fg=DETECTOR_THEME["muted"], anchor="w", padx=10, font=(DETECTOR_THEME["font"], 10))
        self.coord_label.pack(side=tk.LEFT)
        
        self.saved_count_label = tk.Label(bottom_bar, text="已保存: 0", bg=DETECTOR_THEME["toolbar"], fg=DETECTOR_THEME["text"], anchor="e", padx=10, font=(DETECTOR_THEME["font"], 10))
        self.saved_count_label.pack(side=tk.RIGHT)

        self._build_hud_ui()
        
        self.window.bind("<space>", lambda e: self.toggle_pause())
        self.window.bind("<Return>", lambda e: self.save_current_match())
        self.window.bind("<Escape>", lambda e: self.stop_monitoring())
        self.window.bind("<F12>", lambda e: self.toggle_hud_mode())
        self.window.bind("<Control-h>", lambda e: self.toggle_hud_mode())
        self.window.bind("<Control-H>", lambda e: self.toggle_hud_mode())
        self.window.bind("<Control-i>", lambda e: self.inject_to_flow_action())
        self.window.bind("<Control-I>", lambda e: self.inject_to_flow_action())

    def _build_hud_ui(self):
        """构建迷你 HUD 悬浮模式的界面（初态不 pack）。"""
        self.hud_container = tk.Frame(
            self.window,
            bg=DETECTOR_THEME["panel"],
            padx=8,
            pady=6,
            highlightthickness=1,
            highlightbackground=DETECTOR_THEME["border"],
        )

        # 1. 拖拽顶栏
        hud_bar = tk.Frame(self.hud_container, bg=DETECTOR_THEME["toolbar"], padx=6, pady=3)
        hud_bar.pack(fill=tk.X, pady=(0, 4))

        hud_title = tk.Label(
            hud_bar,
            text="🎯 控件探测 HUD",
            font=(DETECTOR_THEME["font"], 9, "bold"),
            bg=DETECTOR_THEME["toolbar"],
            fg=DETECTOR_THEME["text"],
            cursor="fleur",
        )
        hud_title.pack(side=tk.LEFT)

        self.hud_status_badge = tk.Label(
            hud_bar,
            text="🟢 探测中",
            font=(DETECTOR_THEME["font"], 8, "bold"),
            bg=DETECTOR_THEME["success_soft"],
            fg=DETECTOR_THEME["success"],
            padx=5,
            pady=1,
        )
        self.hud_status_badge.pack(side=tk.LEFT, padx=(6, 0))

        hud_title.bind("<Button-1>", self._start_hud_drag)
        hud_title.bind("<B1-Motion>", self._on_hud_drag)
        hud_bar.bind("<Button-1>", self._start_hud_drag)
        hud_bar.bind("<B1-Motion>", self._on_hud_drag)

        btn_hud_restore = tk.Button(
            hud_bar,
            text="🖥️ 还原大窗",
            command=self.toggle_hud_mode,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=6,
            pady=1,
            font=(DETECTOR_THEME["font"], 8),
            bg=DETECTOR_THEME["panel_soft"],
            fg=DETECTOR_THEME["text"],
        )
        btn_hud_restore.pack(side=tk.RIGHT, padx=(4, 0))

        btn_hud_inject = tk.Button(
            hud_bar,
            text="💉 直注流程",
            command=self.inject_to_flow_action,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=6,
            pady=1,
            font=(DETECTOR_THEME["font"], 8, "bold"),
            bg=DETECTOR_THEME["success_soft"],
            fg=DETECTOR_THEME["success"],
        )
        btn_hud_inject.pack(side=tk.RIGHT, padx=(4, 0))

        btn_hud_copy = tk.Button(
            hud_bar,
            text="📋 复制",
            command=self.copy_current_locator,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=6,
            pady=1,
            font=(DETECTOR_THEME["font"], 8),
            bg=DETECTOR_THEME["primary_soft"],
            fg=DETECTOR_THEME["primary"],
        )
        btn_hud_copy.pack(side=tk.RIGHT)

        # 2. 紧凑内容卡片
        hud_body = tk.Frame(self.hud_container, bg=DETECTOR_THEME["panel"], padx=2)
        hud_body.pack(fill=tk.BOTH, expand=True)

        self.hud_name_lbl = tk.Label(
            hud_body,
            text="等待捕获控件... (移动鼠标至目标软件)",
            font=(DETECTOR_THEME["font"], 10, "bold"),
            bg=DETECTOR_THEME["panel"],
            fg=DETECTOR_THEME["primary"],
            anchor="w",
        )
        self.hud_name_lbl.pack(fill=tk.X, pady=(2, 1))

        self.hud_aid_lbl = tk.Label(
            hud_body,
            text="AID: - | Class: -",
            font=("Consolas", 8),
            bg=DETECTOR_THEME["panel"],
            fg=DETECTOR_THEME["muted"],
            anchor="w",
        )
        self.hud_aid_lbl.pack(fill=tk.X, pady=(0, 1))

        self.hud_win_lbl = tk.Label(
            hud_body,
            text="🪟 窗口: - (x=-, y=-)",
            font=(DETECTOR_THEME["font"], 8),
            bg=DETECTOR_THEME["panel"],
            fg=DETECTOR_THEME["text"],
            anchor="w",
        )
        self.hud_win_lbl.pack(fill=tk.X, pady=(0, 1))

        self.hud_match_lbl = tk.Label(
            hud_body,
            text="⭐ 匹配: - | [Space]暂停 [F12]切大窗",
            font=(DETECTOR_THEME["font"], 8),
            bg=DETECTOR_THEME["panel"],
            fg=DETECTOR_THEME["muted"],
            anchor="w",
        )
        self.hud_match_lbl.pack(fill=tk.X)

    def _start_hud_drag(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_hud_drag(self, event):
        deltax = event.x - getattr(self, "_drag_start_x", 0)
        deltay = event.y - getattr(self, "_drag_start_y", 0)
        x = self.window.winfo_x() + deltax
        y = self.window.winfo_y() + deltay
        self.window.geometry(f"+{x}+{y}")

    def toggle_hud_mode(self):
        """在标准全景窗口与迷你 HUD 悬浮模式之间切换。"""
        if not self.hud_mode:
            try:
                self.saved_geometry = self.window.geometry()
            except Exception:
                self.saved_geometry = "1100x780"
            self.hud_mode = True
            self.standard_container.pack_forget()
            self.hud_container.pack(fill=tk.BOTH, expand=True)
            self.window.minsize(380, 150)
            self.window.geometry("450x170")
            try:
                self.window.attributes("-topmost", True)
                self.window.attributes("-alpha", 0.94)
            except Exception:
                pass
            self.window.title("⚡ 控件探测 HUD [F12还原]")
        else:
            self.hud_mode = False
            self.hud_container.pack_forget()
            self.standard_container.pack(fill=tk.BOTH, expand=True)
            self.window.minsize(1000, 700)
            self.window.geometry(self.saved_geometry or "1100x780")
            try:
                self.window.attributes("-alpha", 1.0)
                self.window.attributes("-topmost", bool(self.topmost_var.get()))
            except Exception:
                pass
            self.window.title("实时控件检测器")

    @staticmethod
    def _build_locator_payload(ctrl_info, best_match=None):
        """构造供流程编辑器直接消费的统一定位信息字典"""
        ctrl_info = ctrl_info or {}
        if best_match and best_match.get("library_definition"):
            lib_def = best_match["library_definition"]
            return {
                "id": lib_def.get("id", ""),
                "name": lib_def.get("name") or ctrl_info.get("name", ""),
                "targetMethod": lib_def.get("targetMethod") or lib_def.get("recommendedTargetMethod", "automation_id"),
                "targetValue": lib_def.get("targetValue") or lib_def.get("recommendedTargetValue") or ctrl_info.get("automationId", ""),
                "windowTitle": lib_def.get("windowTitle") or ctrl_info.get("windowTitle", ""),
                "controlType": lib_def.get("controlType") or ctrl_info.get("controlType", ""),
                "className": lib_def.get("className") or ctrl_info.get("className", ""),
                "automationId": lib_def.get("automationId") or ctrl_info.get("automationId", ""),
                "source": "library_match",
                "score": best_match.get("score", 0),
            }

        aid = str(ctrl_info.get("automationId") or "").strip()
        name = str(ctrl_info.get("name") or "").strip()
        cls = str(ctrl_info.get("className") or "").strip()
        ctrl_type = str(ctrl_info.get("controlType") or "").strip()
        win_title = str(ctrl_info.get("windowTitle") or "").strip()

        if aid:
            method = "automation_id"
            val = aid
        elif name:
            method = "name"
            val = name
        elif cls:
            method = "class_name"
            val = cls
        else:
            method = "control_type"
            val = ctrl_type

        return {
            "name": name or ctrl_type or "未命名控件",
            "targetMethod": method,
            "targetValue": val,
            "windowTitle": win_title,
            "controlType": ctrl_type,
            "className": cls,
            "automationId": aid,
            "source": "live_detector",
        }

    def copy_current_locator(self):
        """复制当前控件定位表达式或结构化字典到剪贴板。"""
        ctrl_info = self.current_ctrl_info
        if not ctrl_info:
            messagebox.showinfo("提示", "当前尚未捕获到控件。", parent=self.window)
            return
        best_match = self.current_matches[0] if self.current_matches else None
        payload = self._build_locator_payload(ctrl_info, best_match)
        method = payload.get("targetMethod", "")
        val = payload.get("targetValue", "")
        expr = f"{method}:{val}" if method else val
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(expr)
            self._show_inject_toast(f"已复制定位表达式：{expr}")
        except Exception as exc:
            messagebox.showerror("复制失败", str(exc), parent=self.window)

    def inject_to_flow_action(self):
        """将当前捕获或匹配的控件一键注入到流程编辑器。"""
        ctrl_info = self.current_ctrl_info
        if not ctrl_info:
            messagebox.showinfo("无法注入", "当前尚未捕获到有效控件，请移动鼠标到目标控件上。", parent=self.window)
            return

        best_match = self.current_matches[0] if self.current_matches else None
        lib_match = (
            best_match.get("library_definition", best_match)
            if isinstance(best_match, dict) and "library_definition" in best_match
            else best_match
        )

        # 优先使用显式注册的 on_inject 回调
        if callable(getattr(self, "on_inject", None)):
            try:
                self.on_inject(ctrl_info, lib_match)
                self._show_inject_toast("✓ 已将控件注入到流程编辑器！")
                return
            except Exception as exc:
                print(f"[实时检测器] on_inject 回调失败: {exc}")

        # 次优：直接通知关联的 flow_editor 实例
        flow_editor = getattr(self, "flow_editor", None)
        if flow_editor is not None and hasattr(flow_editor, "inject_control_as_step"):
            try:
                flow_editor.inject_control_as_step(ctrl_info, lib_match)
                self._show_inject_toast("✓ 已将控件注入到流程编辑器！")
                return
            except Exception as exc:
                print(f"[实时检测器] flow_editor 注入失败: {exc}")

        # 兜底：复制标准定位定义到剪贴板并提示
        payload = self._build_locator_payload(ctrl_info, best_match)
        payload_str = json.dumps(payload, ensure_ascii=False, indent=2)
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(payload_str)
            messagebox.showinfo(
                "已复制控件定位",
                "未检测到直接关联的流程编辑器，已将标准控件定位定义复制到剪贴板：\n\n"
                f"{payload_str}\n\n"
                "可在流程编辑器中直接粘贴应用。",
                parent=self.window,
            )
        except Exception as exc:
            messagebox.showerror("写入剪贴板失败", str(exc), parent=self.window)

    def _show_inject_toast(self, message):
        """轻量悬浮气泡反馈"""
        try:
            toast = tk.Toplevel(self.window)
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)
            toast.configure(bg="#0f172a")
            x = self.window.winfo_x() + 30
            y = self.window.winfo_y() + self.window.winfo_height() + 5
            toast.geometry(f"+{x}+{y}")
            lbl = tk.Label(
                toast,
                text=message,
                bg="#0f172a",
                fg="#38bdf8",
                padx=12,
                pady=6,
                font=(DETECTOR_THEME["font"], 9, "bold"),
            )
            lbl.pack()
            toast.after(1600, lambda: toast.destroy())
        except Exception:
            pass
    
    def _load_library(self):
        """加载控件库（后台线程）：避免 UI 线程同步解析大目录 JSON 导致窗口假死。

        历史问题：control_maps 含 recordings/ 时全量加载约 1.35 GB，
        打开检测器即"未响应"数十秒至数分钟（P0-1，2026-09-29 审计）。
        现在：① recordings/ 等素材目录排除（见 load_all_other_libraries）；
        ② 加载在后台线程执行，完成后经 window.after 回主线程刷新界面。
        """
        if getattr(self, "_library_loading", False):
            return
        self._library_loading = True
        self.status_label.config(text="状态: 加载控件库...(后台)", fg=DETECTOR_THEME["primary"])
        try:
            self.refresh_btn.config(state="disabled")
        except Exception:
            pass

        def work():
            master, _ = load_master_library()
            others, _ = load_all_other_libraries()
            return master, others

        def done(result):
            self._library_loading = False
            try:
                self.refresh_btn.config(state="normal")
            except Exception:
                pass
            if result is None:
                self.status_label.config(text="状态: 控件库加载失败", fg=DETECTOR_THEME["danger"])
                return
            self.master_controls, self.other_controls = result
            self.lib_count_label.config(
                text=f"总库:{len(self.master_controls)} 其他:{len(self.other_controls)}"
            )
            self.status_label.config(text="状态: 已加载", fg=DETECTOR_THEME["muted"])

        def runner():
            try:
                result = work()
            except Exception as exc:
                print(f"[实时检测器] 加载控件库失败: {exc}")
                result = None
            try:
                self.window.after(0, lambda: done(result))
            except Exception:
                pass

        threading.Thread(target=runner, daemon=True).start()
    
    def _toggle_topmost(self):
        """响应置顶复选框切换。"""
        try:
            self.window.attributes("-topmost", self.topmost_var.get())
        except Exception:
            pass

    def start_monitoring(self):
        if self.monitoring:
            return
        if getattr(self, "_library_loading", False):
            messagebox.showinfo("提示", "控件库正在后台加载，请稍候再开始检测。", parent=self.window)
            return
        
        total = len(self.master_controls) + len(self.other_controls)
        if total == 0:
            messagebox.showwarning("提示", "控件库为空，请先采集控件。", parent=self.window)
            return
        
        self.monitoring = True
        self.paused = False
        self.start_btn.config(state="disabled")
        self.pause_btn.config(state="normal")
        self.stop_btn.config(state="normal")
        self.save_btn.config(state="disabled")
        
        # 监测时窗口置顶，避免被目标应用覆盖
        if self.topmost_var.get():
            self.window.attributes("-topmost", True)
        
        mode = "双后端(UIA+Win32)" if self.smart_var.get() else self.backend_var.get().upper()
        depth = self.depth_var.get()
        raw_tag = "|RawView" if self.raw_view_var.get() else ""
        self.status_label.config(text=f"状态: 检测中({mode}|深度{depth}{raw_tag})", fg=DETECTOR_THEME["primary"])

        # 选项在主线程快照为普通值传入线程（worker 不得读 Tk 变量：跨线程读
        # Tcl 对象会偶发假死/异常被吞）。代次号用于让上一轮残留线程在下一次
        # 循环检查时自行退出（避免"停止后立即开始"双线程并行探测）。
        monitor_opts = {
            "depth": depth,
            "smart": bool(self.smart_var.get()),
            "use_raw": bool(self.raw_view_var.get()),
            "backend": self.backend_var.get(),
        }
        self._monitor_gen = getattr(self, "_monitor_gen", 0) + 1
        
        self.monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(monitor_opts, self._monitor_gen),
            daemon=True,
        )
        self.monitor_thread.start()
    
    def toggle_pause(self):
        if not self.monitoring:
            return
        
        self.paused = not self.paused
        
        if self.paused:
            self.pause_btn.config(text="继续[Space]", bg=DETECTOR_THEME["success_soft"], fg=DETECTOR_THEME["success"], activebackground=DETECTOR_THEME["success"], activeforeground="#ffffff")
            self.save_btn.config(state="normal")
            self.status_label.config(text="状态: 已暂停", fg=DETECTOR_THEME["warning"])
        else:
            self.pause_btn.config(text="暂停[Space]", bg=DETECTOR_THEME["warning_soft"], fg=DETECTOR_THEME["warning"], activebackground=DETECTOR_THEME["warning"], activeforeground="#ffffff")
            self.save_btn.config(state="disabled")
            mode = "双后端" if self.smart_var.get() else self.backend_var.get().upper()
            self.status_label.config(text=f"状态: 检测中({mode})", fg=DETECTOR_THEME["primary"])
    
    def stop_monitoring(self):
        self.monitoring = False
        self.paused = False
        self.start_btn.config(state="normal")
        self.pause_btn.config(state="disabled", text="暂停[Space]", bg=DETECTOR_THEME["warning_soft"])
        self.stop_btn.config(state="disabled")
        self.save_btn.config(state="disabled")
        self.status_label.config(text="状态: 已停止", fg=DETECTOR_THEME["muted"])
        # 停止监测时恢复窗口置顶
        self.window.attributes("-topmost", False)
    
    def _monitor_loop(self, opts, gen):
        """监控线程主循环。

        opts 为主线程快照的选项（depth/smart/use_raw/backend）——worker 线程
        不得读 Tk 变量；gen 为代次号，停止后立即重新开始时旧线程在下一次
        循环检查即自行退出（避免双线程同时探测/刷新）。
        """
        last_x, last_y = -1, -1
        consecutive_errors = 0
        
        while self.monitoring and getattr(self, "_monitor_gen", 0) == gen:
            if self.paused:
                time.sleep(0.1)
                continue
            
            try:
                consecutive_errors = 0
                x, y = _get_mouse_position()
                
                if x != last_x or y != last_y:
                    last_x, last_y = x, y
                    
                    depth = opts["depth"]
                    smart = opts["smart"]
                    use_raw = opts["use_raw"]
                    
                    if smart:
                        results = get_control_at_mouse_position("uia", depth, smart_mode=True, use_raw_view=use_raw)
                    else:
                        results = get_control_at_mouse_position(opts["backend"], depth, smart_mode=False, use_raw_view=use_raw)
                    
                    if results:
                        all_matches = []
                        all_ctrl_infos = results  # list of ctrl_info dicts
                        for ci in all_ctrl_infos:
                            matches = match_control_to_library(
                                ci, self.master_controls, self.other_controls
                            )
                            all_matches.extend(matches)
                        
                        # 去重：按 library_definition 的 automationId+windowTitle 去重，保留高分
                        seen = {}
                        for m in all_matches:
                            lib_def = m["library_definition"]
                            key = f"{lib_def.get('automationId', '')}|{lib_def.get('windowTitle', '')}"
                            if key not in seen or m["score"] > seen[key]["score"]:
                                seen[key] = m
                        all_matches = sorted(seen.values(), key=lambda x: x["score"], reverse=True)[:20]
                        
                        self.current_ctrl_info = all_ctrl_infos[0]  # 保持兼容：最佳匹配的第一个
                        self.current_matches = all_matches
                        
                        self.window.after(0, lambda cis=all_ctrl_infos, m=all_matches: self._update_display(cis, m))
                    else:
                        self.window.after(0, lambda: self.status_label.config(text="状态: 未检测到控件", fg=DETECTOR_THEME["warning"]))
                
                time.sleep(0.05)
                
            except Exception as exc:
                # 此前异常被完全吞掉：表现为"检测还活着但界面永不更新"且无提示。
                consecutive_errors += 1
                if consecutive_errors == 1:
                    print(f"[实时检测器] 监控循环异常: {exc}")
                    try:
                        self.window.after(0, lambda: self.status_label.config(
                            text="状态: 监控异常（详见控制台），仍在重试",
                            fg=DETECTOR_THEME["warning"]))
                    except Exception:
                        pass
                time.sleep(0.2)
    
    def _update_display(self, ctrl_infos, matches):
        """同时显示多个后端的控件信息
        
        Args:
            ctrl_infos: list of ctrl_info dict（uia / win32 各一个）
            matches: 合并去重后的匹配列表
        """
        if not ctrl_infos:
            return
        first = ctrl_infos[0]
        self.coord_label.config(text=f"位置: ({first.get('x')}, {first.get('y')})")
        
        self.capture_text.delete("1.0", tk.END)
        
        lines = [
            f"【鼠标位置】 ({first.get('x')}, {first.get('y')})",
            f"【探测后端】 {', '.join(ci.get('backend', '?').upper() for ci in ctrl_infos)}",
            "=" * 50,
        ]
        
        for ci in ctrl_infos:
            backend_label = ci.get("backend", "?").upper()
            lines.append(f"──── {backend_label} 后端结果 ────")
            lines.append("【控件基本信息】")
            lines.append(f"  名称:      {ci.get('name') or '(无)'}")
            lines.append(f"  类型:      {ci.get('controlType') or '(未知)'}")
            lines.append(f"  ClassName: {ci.get('className') or '(无)'}")
            lines.append(f"  AID:       {ci.get('automationId') or '(无)'}")
            lines.append(f"  句柄:      {ci.get('handle') or '(无)'}")
            lines.append("【所属窗口】")
            lines.append(f"  {ci.get('windowTitle') or '(无)'}")
            
            path = ci.get("pathSegments", [])
            if path:
                lines.append(f"【路径】 {' > '.join(path)}")
            
            ancestors = ci.get("ancestors", [])
            if ancestors:
                lines.append("【父级控件链】")
                for anc in ancestors[:8]:
                    # 兼容两种格式：富字段采集为字符串（uiPath 段），轻量回退为字典
                    if isinstance(anc, dict):
                        lines.append(f"  └─ [{anc.get('controlType', '')}] {anc.get('name') or anc.get('className', '')}")
                    else:
                        lines.append(f"  └─ {anc}")
            
            rect = ci.get("rectangle")
            if rect:
                lines.append(f"【矩形】 L={rect.get('left')} T={rect.get('top')} R={rect.get('right')} B={rect.get('bottom')}")
            
            lines.append("=" * 50)
        
        self.capture_text.insert("1.0", "\n".join(lines))
        
        self.match_listbox.delete(0, tk.END)
        
        for i, match in enumerate(matches[:15]):
            lib_def = match["library_definition"]
            score = match["score"]
            source = lib_def.get("sourceFile", "")[:10]
            
            name = (lib_def.get("name", "") or "")[:17]
            aid = (lib_def.get("automationId", "") or "")[:27]
            
            star = " ★" if score >= 100 else ""
            line = f"{score:>3}{star}  {name:<17}  {aid:<27}  {source}"
            
            self.match_listbox.insert(tk.END, line)
            
            if score >= 100:
                self.match_listbox.itemconfig(i, bg=DETECTOR_THEME["success_soft"])
            elif score >= 60:
                self.match_listbox.itemconfig(i, bg=DETECTOR_THEME["warning_soft"])
        
        perfect = sum(1 for m in matches if m["score"] >= 100)
        if perfect > 0:
            self.status_label.config(text=f"★ 发现{perfect}个完全匹配!", fg=DETECTOR_THEME["success"])
        else:
            self.status_label.config(text=f"匹配{len(matches)}个候选", fg=DETECTOR_THEME["primary"])
        
        # 搜索定位的固定高亮在有效期内重设：本轮刷新重填列表时行色会被清掉（审计 P2）
        pin = getattr(self, "_highlight_pin", None)
        if pin is not None:
            pin_idx, pin_until = pin
            if time.time() >= pin_until:
                self._highlight_pin = None
            elif 0 <= pin_idx < len(matches):
                self.match_listbox.itemconfig(pin_idx, bg=DETECTOR_THEME["primary_soft"])
        
        self.match_detail.delete("1.0", tk.END)

        # 同步更新 HUD 悬浮模式内容
        if hasattr(self, "hud_name_lbl"):
            ctrl_name = first.get("name") or "(无名称)"
            ctrl_type = first.get("controlType") or "未知类型"
            self.hud_name_lbl.config(text=f"🔲 [{ctrl_type}] {ctrl_name}")

            aid = first.get("automationId") or "(无AID)"
            cls = first.get("className") or "(无Class)"
            self.hud_aid_lbl.config(text=f"AID: {aid} | Class: {cls}")

            win = first.get("windowTitle") or "(无顶层窗口)"
            self.hud_win_lbl.config(text=f"🪟 {win}  ({first.get('x')}, {first.get('y')})")

            if matches:
                top_m = matches[0]
                lib_name = top_m.get("name") or top_m.get("library_definition", {}).get("name", "")
                score = top_m.get("score", 0)
                src = top_m.get("source", "")
                self.hud_match_lbl.config(
                    text=f"⭐ 最佳匹配: {score}分 [{src}] {lib_name} | [Space]暂停",
                    fg=DETECTOR_THEME["success"] if score >= 80 else DETECTOR_THEME["warning"],
                )
            else:
                self.hud_match_lbl.config(
                    text="ℹ️ 库内无匹配项（可一键直注为新步骤）| [Space]暂停",
                    fg=DETECTOR_THEME["muted"],
                )
    
    def _on_match_select(self, event):
        selection = self.match_listbox.curselection()
        if not selection:
            return
        
        try:
            idx = selection[0]
            if 0 <= idx < len(self.current_matches):
                match = self.current_matches[idx]
                lib_def = match["library_definition"]
                
                lines = [
                    f"分数: {match['score']} ({', '.join(match['reasons'])})",
                    "-" * 50,
                    f"名称:     {lib_def.get('name') or '(无)'}",
                    f"类型:     {lib_def.get('controlType') or '(无)'}",
                    f"ClassName: {lib_def.get('className') or '(无)'}",
                    f"AID:      {lib_def.get('automationId') or '(无)'}",
                    f"窗口:     {lib_def.get('windowTitle') or '(无)'}",
                    "-" * 50,
                    f"Method: {lib_def.get('targetMethod') or '(无)'}",
                    f"Value:  {lib_def.get('targetValue') or '(无)'}",
                    "-" * 50,
                    f"来源: {lib_def.get('sourceFile') or '(无)'}",
                ]
                
                self.match_detail.delete("1.0", tk.END)
                self.match_detail.insert("1.0", "\n".join(lines))
                
        except (ValueError, IndexError):
            pass
    
    def save_current_match(self):
        if not self.current_matches:
            messagebox.showinfo("提示", "当前没有匹配的控件。", parent=self.window)
            return

        # 有选中行时保存选中行（双击/回车均按该行查看过详情），无选中时回退
        # 最高分（第 1 行）—— 此前恒保存第 1 行，与用户双击的行无关（审计 P2）
        index = 0
        try:
            selection = self.match_listbox.curselection()
            if selection and 0 <= int(selection[0]) < len(self.current_matches):
                index = int(selection[0])
        except Exception:
            index = 0
        best = self.current_matches[index]
        lib_def = best["library_definition"]
        
        self.saved_controls.append({
            "savedTime": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "capturedInfo": self.current_ctrl_info,
            "matchedControl": best["library_control"],
            "matchScore": best["score"],
            "matchReasons": best["reasons"],
        })
        
        self.saved_count_label.config(text=f"已保存: {len(self.saved_controls)}")
        self.status_label.config(
            text=f"✓ 已保存: {lib_def.get('name') or lib_def.get('automationId', '?')} (分数:{best['score']})",
            fg=DETECTOR_THEME["success"]
        )
    
    def export_saved_controls(self):
        """导出已保存控件；返回是否真正写出文件（无内容/用户取消/失败均视为未导出）。"""
        if not self.saved_controls:
            messagebox.showinfo("提示", "没有已保存的控件。", parent=self.window)
            return False
        
        fpath = filedialog.asksaveasfilename(
            title="保存控件信息",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            initialdir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "control_maps"),
        )
        
        if fpath:
            try:
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump({
                        "exportTime": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "count": len(self.saved_controls),
                        "controls": self.saved_controls,
                    }, f, ensure_ascii=False, indent=2)
                
                messagebox.showinfo("保存成功", f"已保存 {len(self.saved_controls)} 个控件", parent=self.window)
                return True
            except Exception as e:
                messagebox.showerror("保存失败", str(e), parent=self.window)
                return False
    
    # ====================================================================
    # 搜索控件功能
    # ====================================================================

    def _open_search_dialog(self):
        """打开搜索控件对话框（Toplevel），支持按名称模糊搜索已加载的控件库。"""
        # 如果已有搜索窗口则前置
        if hasattr(self, "_search_win") and self._search_win.winfo_exists():
            self._search_win.lift()
            self._search_win.focus_force()
            return

        dlg = tk.Toplevel(self.window)
        dlg.title("搜索控件")
        dlg.geometry("720x480")
        dlg.minsize(560, 320)
        dlg.transient(self.window)
        self._search_win = dlg
        dlg.configure(bg=DETECTOR_THEME["bg"])

        # ── 搜索输入区 ──
        input_frame = tk.Frame(dlg, bg=DETECTOR_THEME["bg"], padx=10, pady=8)
        input_frame.pack(fill=tk.X)

        tk.Label(input_frame, text="搜索名称:", bg=DETECTOR_THEME["bg"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10)).pack(side=tk.LEFT)
        search_var = tk.StringVar()
        search_entry = tk.Entry(input_frame, textvariable=search_var, font=(DETECTOR_THEME["font"], 11), width=36, bg=DETECTOR_THEME["panel_soft"], fg=DETECTOR_THEME["text"], insertbackground=DETECTOR_THEME["text"], relief="solid", bd=1, highlightbackground=DETECTOR_THEME["border"], highlightcolor=DETECTOR_THEME["primary"], highlightthickness=1)
        search_entry.pack(side=tk.LEFT, padx=(4, 6), fill=tk.X, expand=True)
        search_entry.focus_set()

        search_btn = tk.Button(input_frame, text="搜索", width=8,
                               command=lambda: self._do_search(search_var.get(), tree, count_label))
        _paint_button(search_btn, DETECTOR_THEME["primary_soft"], DETECTOR_THEME["primary"], DETECTOR_THEME["primary"])
        search_btn.pack(side=tk.LEFT, padx=(0, 6))

        close_btn = tk.Button(input_frame, text="关闭", width=6, command=dlg.destroy)
        _paint_button(close_btn, DETECTOR_THEME["panel"], DETECTOR_THEME["text"], DETECTOR_THEME["primary_soft"], active_fg=DETECTOR_THEME["primary"])
        close_btn.pack(side=tk.LEFT)

        # 回车触发搜索
        search_entry.bind("<Return>", lambda e: self._do_search(search_var.get(), tree, count_label))

        # ── 结果列表（Treeview） ──
        tree_frame = tk.Frame(dlg, bg=DETECTOR_THEME["bg"], padx=10, pady=(0, 4))
        tree_frame.pack(fill=tk.BOTH, expand=True)

        columns = ("name", "control_type", "automation_id", "source")
        tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=16, style="Detector.Treeview")
        tree.heading("name", text="名称")
        tree.heading("control_type", text="类型")
        tree.heading("automation_id", text="AutomationId")
        tree.heading("source", text="来源")
        tree.column("name", width=200)
        tree.column("control_type", width=100)
        tree.column("automation_id", width=180)
        tree.column("source", width=140)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        # 双击结果 → 在主界面匹配列表中定位并高亮
        tree.bind("<Double-1>", lambda e: self._on_search_result_double_click(tree))

        # ── 底部计数 ──
        count_label = tk.Label(dlg, text="输入关键词后点击搜索或按 Enter", fg=DETECTOR_THEME["muted"], bg=DETECTOR_THEME["bg"], anchor="w", padx=12)
        count_label.pack(fill=tk.X, pady=(2, 6))

    def _do_search(self, keyword, tree, count_label):
        """遍历已加载的控件库数据，按 name 字段做 case-insensitive 包含匹配。

        结果按相关性排序：精确匹配优先，前缀匹配次之，其余按名称长度升序。
        """
        # 清空旧结果
        for item in tree.get_children():
            tree.delete(item)

        keyword = (keyword or "").strip()
        if not keyword:
            count_label.config(text="请输入搜索关键词")
            return

        kw_lower = keyword.lower()

        # 合并总库 + 其他库，标记来源
        all_items = []
        for ctrl in self.master_controls:
            all_items.append((ctrl, "总控件库"))
        for ctrl in self.other_controls:
            src = str(ctrl.get("_source_file", "控件库")).strip()
            all_items.append((ctrl, src))

        results = []
        for ctrl, source in all_items:
            # 从控件条目中提取 name / controlType / automationId
            inspect_data = ctrl.get("inspectData", {}) if isinstance(ctrl.get("inspectData"), dict) else {}
            name = (str(ctrl.get("name", "") or ctrl.get("displayName", "") or
                        inspect_data.get("name", "")).strip())
            ctrl_type = (str(inspect_data.get("controlType", "") or ctrl.get("controlType", "")).strip())
            aid = (str(inspect_data.get("automationId", "") or ctrl.get("automationId", "")).strip())

            if not name:
                continue

            name_lower = name.lower()
            if kw_lower not in name_lower:
                continue

            # 计算排序权重：精确匹配 0，前缀匹配 1，包含匹配 2
            if name_lower == kw_lower:
                rank = 0
            elif name_lower.startswith(kw_lower):
                rank = 1
            else:
                rank = 2
            results.append((rank, len(name), name, ctrl_type, aid, source, ctrl))

        # 排序：rank 升序 → 名称长度升序
        results.sort(key=lambda t: (t[0], t[1]))

        # 限制显示 200 条
        for rank, _len, name, ctrl_type, aid, source, ctrl in results[:200]:
            tree.insert("", tk.END, values=(name, ctrl_type, aid, source), tags=("ctrl_ref",))
            # 将原始 ctrl 引用存到 item 的 tags 中无法直接存对象，改用 iid 映射
            # 这里用 tree 的 children iid 做索引
        # 建立 iid → ctrl 映射，供双击使用
        self._search_iid_map = {}
        for i, (rank, _len, name, ctrl_type, aid, source, ctrl) in enumerate(results[:200]):
            children = tree.get_children()
            if i < len(children):
                self._search_iid_map[children[i]] = ctrl

        total = len(results)
        shown = min(total, 200)
        count_label.config(text=f"找到 {total} 个匹配控件" + (f"（显示前 {shown} 条）" if total > 200 else ""))

    def _on_search_result_double_click(self, tree):
        """双击搜索结果时，在主界面匹配列表中定位并高亮对应项。

        逻辑：用双击的控件条目在 self.master_controls / self.other_controls 中
        查找 current_matches 里是否有匹配项，若有则选中并滚动到对应行。
        若无匹配项，则将控件详情显示在 match_detail 区域。
        """
        sel = tree.selection()
        if not sel:
            return
        iid = sel[0]
        ctrl = getattr(self, "_search_iid_map", {}).get(iid)
        if ctrl is None:
            return

        # 提取该控件的关键标识用于在 current_matches 中查找
        inspect_data = ctrl.get("inspectData", {}) if isinstance(ctrl.get("inspectData"), dict) else {}
        target_aid = str(inspect_data.get("automationId", "") or ctrl.get("automationId", "")).strip().lower()
        target_name = str(ctrl.get("name", "") or ctrl.get("displayName", "") or
                          inspect_data.get("name", "")).strip().lower()
        target_win = str(ctrl.get("windowTitle", "")).strip().lower()

        # 尝试在 current_matches 中找到对应项
        found_idx = None
        for i, match in enumerate(self.current_matches):
            lib_def = match["library_definition"]
            m_aid = str(lib_def.get("automationId", "")).strip().lower()
            m_name = str(lib_def.get("name", "")).strip().lower()
            m_win = str(lib_def.get("windowTitle", "")).strip().lower()
            if target_aid and m_aid and target_aid == m_aid:
                found_idx = i
                break
            if target_name and m_name and target_name == m_name and target_win == m_win:
                found_idx = i
                break

        if found_idx is not None:
            # 在主界面 Listbox 中选中并滚动
            self.match_listbox.selection_clear(0, tk.END)
            self.match_listbox.selection_set(found_idx)
            self.match_listbox.see(found_idx)
            # 触发详情显示
            self._on_match_select(None)
            # 高亮背景
            self.match_listbox.itemconfig(found_idx, bg=DETECTOR_THEME["primary_soft"])
            # 记录固定高亮：检测刷新（_update_display 重填列表）会冲掉行色，
            # 刷新末尾按该记录重设（审计 P2）
            self._highlight_pin = (found_idx, time.time() + 1.5)
            # 1.5 秒后恢复原色；记录定时器 id，连续搜索/关窗时可取消，
            # 旧回调不再打到已刷新/销毁的列表项（审计 P2）
            prev_highlight = getattr(self, "_match_highlight_after_id", None)
            if prev_highlight is not None:
                try:
                    self.window.after_cancel(prev_highlight)
                except Exception:
                    pass
            self._match_highlight_after_id = self.window.after(
                1500, lambda idx=found_idx: self._restore_match_color(idx)
            )
        else:
            # 不在当前匹配列表中 → 在 match_detail 显示该控件详情
            lines = [
                f"── 搜索结果详情 ──",
                f"名称:     {ctrl.get('name') or ctrl.get('displayName') or '(无)'}",
                f"类型:     {inspect_data.get('controlType') or ctrl.get('controlType') or '(无)'}",
                f"AID:      {inspect_data.get('automationId') or ctrl.get('automationId') or '(无)'}",
                f"窗口:     {ctrl.get('windowTitle') or '(无)'}",
                f"来源:     {ctrl.get('_source_file', '总控件库')}",
            ]
            self.match_detail.delete("1.0", tk.END)
            self.match_detail.insert("1.0", "\n".join(lines))

    def _restore_match_color(self, idx):
        """恢复匹配列表项的原始背景色。"""
        pin = getattr(self, "_highlight_pin", None)
        if pin is not None and pin[0] == idx:
            self._highlight_pin = None
        try:
            if 0 <= idx < len(self.current_matches):
                score = self.current_matches[idx]["score"]
                if score >= 100:
                    self.match_listbox.itemconfig(idx, bg=DETECTOR_THEME["success_soft"])
                elif score >= 60:
                    self.match_listbox.itemconfig(idx, bg=DETECTOR_THEME["warning_soft"])
                else:
                    self.match_listbox.itemconfig(idx, bg="")
        except Exception:
            pass

    # ========================================================================
    # 合并入库功能
    # ========================================================================

    def _open_merge_dialog(self):
        """打开合并入库对话框，让用户选择源文件和目标文件并执行合并。"""
        MergeDialog(self.window, on_merge_done=self._load_library)

    def on_close(self):
        self.stop_monitoring()
        
        if self.saved_controls:
            answer = messagebox.askyesnocancel(
                "退出前导出",
                f"是否导出已保存的 {len(self.saved_controls)} 个控件？\n\n"
                "「是」：先导出再关闭\n「否」：不导出直接关闭\n「取消」：返回窗口",
                parent=self.window,
            )
            if answer is None:
                return
            if answer and not self.export_saved_controls():
                # 导出未完成（用户在保存对话框取消/写入失败）：保留窗口，避免静默丢失
                return
        
        self.window.destroy()


class MergeDialog:
    """合并入库对话框：选择源采集文件和目标控件库文件，预览并执行合并。

    合并逻辑：
    - 读取两个 JSON 文件的 flatControls / controlDefinitions 列表
    - 按 (automationId, controlType, name) 去重
    - 合并 controlsTree（如果有）
    - 更新 metadata 记录合并来源
    - 保存合并后的 JSON 到目标文件
    """

    def __init__(self, parent, on_merge_done=None):
        self.parent = parent
        self.on_merge_done = on_merge_done
        self.source_path = tk.StringVar()
        self.target_path = tk.StringVar()
        self.source_info = {}  # 源文件预览信息

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("合并入库")
        self.dialog.geometry("620x480")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.configure(bg=DETECTOR_THEME["bg"])

        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 12, "pady": 4}

        # ── 源文件选择 ──
        src_frame = tk.LabelFrame(self.dialog, text="源文件（要并入的采集文件）", padx=8, pady=6, bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10, "bold"))
        src_frame.pack(fill=tk.X, **pad)

        src_entry = tk.Entry(src_frame, textvariable=self.source_path, width=55, state="readonly", bg=DETECTOR_THEME["panel_soft"], fg=DETECTOR_THEME["text"], readonlybackground=DETECTOR_THEME["panel_soft"], relief="solid", bd=1)
        src_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        src_browse_btn = tk.Button(src_frame, text="浏览…", command=self._browse_source, width=8)
        _paint_button(src_browse_btn, DETECTOR_THEME["panel"], DETECTOR_THEME["text"], DETECTOR_THEME["primary_soft"], active_fg=DETECTOR_THEME["primary"])
        src_browse_btn.pack(side=tk.LEFT, padx=(6, 0))

        # ── 源文件预览 ──
        preview_frame = tk.LabelFrame(self.dialog, text="源文件预览", padx=8, pady=6, bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10, "bold"))
        preview_frame.pack(fill=tk.X, **pad)

        self.preview_text = scrolledtext.ScrolledText(
            preview_frame, height=6, font=("Consolas", 9), state="disabled",
            bg=DETECTOR_THEME["panel_soft"], fg=DETECTOR_THEME["text"],
            insertbackground=DETECTOR_THEME["text"],
            highlightbackground=DETECTOR_THEME["border"], highlightcolor=DETECTOR_THEME["primary"],
            highlightthickness=1, relief="flat",
        )
        self.preview_text.pack(fill=tk.X)

        # ── 目标文件选择 ──
        tgt_frame = tk.LabelFrame(self.dialog, text="目标文件（合并到的控件库）", padx=8, pady=6, bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10, "bold"))
        tgt_frame.pack(fill=tk.X, **pad)

        tgt_entry = tk.Entry(tgt_frame, textvariable=self.target_path, width=55, state="readonly", bg=DETECTOR_THEME["panel_soft"], fg=DETECTOR_THEME["text"], readonlybackground=DETECTOR_THEME["panel_soft"], relief="solid", bd=1)
        tgt_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tgt_browse_btn = tk.Button(tgt_frame, text="浏览…", command=self._browse_target, width=8)
        _paint_button(tgt_browse_btn, DETECTOR_THEME["panel"], DETECTOR_THEME["text"], DETECTOR_THEME["primary_soft"], active_fg=DETECTOR_THEME["primary"])
        tgt_browse_btn.pack(side=tk.LEFT, padx=(6, 0))

        # ── 合并选项 ──
        opt_frame = tk.LabelFrame(self.dialog, text="合并选项", padx=8, pady=6, bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10, "bold"))
        opt_frame.pack(fill=tk.X, **pad)

        self.dedup_var = tk.StringVar(value="automationId+controlType+name")
        tk.Label(opt_frame, text="去重键:", bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["text"], font=(DETECTOR_THEME["font"], 10)).pack(side=tk.LEFT)
        ttk.Combobox(
            opt_frame, textvariable=self.dedup_var, width=30, state="readonly", style="Detector.TCombobox",
            values=["automationId+controlType+name", "uiPath", "name+controlType"]
        ).pack(side=tk.LEFT, padx=(4, 0))

        self.overwrite_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opt_frame, text="高权威覆盖低权威字段", variable=self.overwrite_var, bg=DETECTOR_THEME["panel"], fg=DETECTOR_THEME["text"], selectcolor=DETECTOR_THEME["panel_soft"], activebackground=DETECTOR_THEME["panel"], activeforeground=DETECTOR_THEME["primary"], font=(DETECTOR_THEME["font"], 10)).pack(side=tk.LEFT, padx=(16, 0))

        # ── 操作按钮 ──
        btn_frame = tk.Frame(self.dialog, bg=DETECTOR_THEME["bg"])
        btn_frame.pack(fill=tk.X, **pad)

        merge_btn = tk.Button(btn_frame, text="执行合并", command=self._execute_merge, width=12)
        _paint_button(merge_btn, DETECTOR_THEME["success_soft"], DETECTOR_THEME["success"], DETECTOR_THEME["success"])
        merge_btn.configure(font=(DETECTOR_THEME["font"], 10, "bold"))
        merge_btn.pack(side=tk.LEFT)
        cancel_btn = tk.Button(btn_frame, text="取消", command=self.dialog.destroy, width=8)
        _paint_button(cancel_btn, DETECTOR_THEME["panel"], DETECTOR_THEME["text"], DETECTOR_THEME["primary_soft"], active_fg=DETECTOR_THEME["primary"])
        cancel_btn.pack(side=tk.RIGHT)

        # ── 状态栏 ──
        self.status_label = tk.Label(self.dialog, text="请选择源文件和目标文件", fg=DETECTOR_THEME["muted"], bg=DETECTOR_THEME["bg"], anchor="w", font=(DETECTOR_THEME["font"], 10))
        self.status_label.pack(fill=tk.X, padx=12, pady=(0, 8))

    def _browse_source(self):
        """选择源采集文件（JSON）。"""
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "control_maps")
        fpath = filedialog.askopenfilename(
            title="选择源采集文件",
            initialdir=base_dir,
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            parent=self.dialog,
        )
        if fpath:
            self.source_path.set(fpath)
            self._preview_source(fpath)

    def _preview_source(self, fpath):
        """预览源文件的基本信息。"""
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)

            flat = data.get("flatControls", [])
            defs = data.get("controlDefinitions", [])
            tree = data.get("controlsTree", [])
            tw = data.get("targetWindow", {})
            meta = data.get("scanMeta", {})

            self.source_info = {
                "flatControls": flat,
                "controlDefinitions": defs,
                "controlsTree": tree,
                "targetWindow": tw,
                "scanMeta": meta,
                "data": data,
            }

            lines = [
                f"文件: {os.path.basename(fpath)}",
                f"flatControls: {len(flat)} 个控件",
                f"controlDefinitions: {len(defs)} 个定义",
                f"controlsTree: {len(tree)} 个顶层节点" if tree else "controlsTree: 无",
                f"窗口: {tw.get('title', '(未知)')}",
                f"框架: {tw.get('frameworkId', '(未知)')}",
                f"扫描时间: {meta.get('scanTime', '(未知)')}",
            ]
            self.preview_text.config(state="normal")
            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert("1.0", "\n".join(lines))
            self.preview_text.config(state="disabled")

            self.status_label.config(text=f"已加载源文件: {len(flat)} 个控件", fg=DETECTOR_THEME["primary"])
        except Exception as e:
            self.source_info = {}
            self.preview_text.config(state="normal")
            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert("1.0", f"加载失败: {e}")
            self.preview_text.config(state="disabled")
            self.status_label.config(text="源文件加载失败", fg=DETECTOR_THEME["danger"])

    def _browse_target(self):
        """选择目标控件库文件（JSON）。"""
        base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "control_maps")
        fpath = filedialog.askopenfilename(
            title="选择目标控件库文件",
            initialdir=base_dir,
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            parent=self.dialog,
        )
        if fpath:
            self.target_path.set(fpath)

    def _interestarea_node_label(self, ctrl, panel_title_map=None):
        """提取 interest-area 模板复制控件的面板节点标签，用于合并去重时按节点消歧。

        优先级（与 canonical normalize_control 的 disc 逻辑一致）：
          1) TileHeader 兄弟面板节点名（panel_title_map，最权威，兼容旧采集格式）
          2) labelText / relatedLabelName
          3) 兜底解析 recommendedTargetValue 第 3 段节点名
        非模板复制控件返回空串（不影响其它控件去重）。
        """
        if not isinstance(ctrl, dict):
            return ""
        ins = ctrl.get("inspectData", {}) or {}
        aid = str(ctrl.get("automationId", "") or ins.get("automationId", "")).strip()
        if not aid.startswith("InterestAreas_Button_"):
            return ""
        # 1) TileHeader 兄弟面板节点名（最权威，兼容旧采集格式）
        if isinstance(panel_title_map, dict):
            pid = ctrl.get("parentIndex")
            if pid is not None and pid in panel_title_map:
                return panel_title_map[pid]
        # 2) labelText / relatedLabelName
        for f in ("labelText", "relatedLabelName"):
            v = str(ctrl.get(f, "") or "").strip()
            if v and "," not in v:
                return v
        # 3) recommendedTargetValue 第 3 段节点名
        rtv = str(ctrl.get("recommendedTargetValue", "") or "").strip()
        if rtv:
            parts = [p.strip() for p in rtv.split(",")]
            _ia_nodes = {"测风点", "风机", "结果点", "绘图", "配置", "风廓线", "Lidar", "中尺度单元"}
            if len(parts) >= 3 and parts[2] in _ia_nodes:
                return parts[2]
        return ""

    @staticmethod
    def _build_ia_panel_title_map(flat_controls):
        """从 flat controls 的 InterestAreasView_Tile_Header 兄弟构建 {parent_index: 面板节点名}。

        与 build_control_map_library._build_ia_panel_title_map 一致：模板复制按钮与同面板
        TileHeader 共享同一 parentIndex，故可用按钮自身的 parentIndex 反查节点名。
        """
        result = {}
        if not isinstance(flat_controls, list):
            return result
        for c in flat_controls:
            if not isinstance(c, dict):
                continue
            if str(c.get("automationId", "")).strip() != "InterestAreasView_Tile_Header":
                continue
            pid = c.get("parentIndex")
            if pid is None:
                continue
            text = str(c.get("name", "") or "").strip()
            if text and "," not in text[:30] and len(text) <= 30:
                result[pid] = text
        return result

    def _build_dedup_key(self, ctrl, mode, panel_title_map=None):
        """根据去重模式构建控件的唯一键（与 build_control_map_library._merge_dedup_key 一致）。

        注意：interest-area 模板复制控件（各面板同名 automationId 的按钮）仅靠
        aid+ct+name 无法区分（name 为空、uiPath 相同），必须追加面板节点标签消歧，
        否则合并入库时 8 个节点的按钮会被误并为 1 条（见知识库模式 L）。
        """
        ins = ctrl.get("inspectData", {}) or {}
        if mode == "automationId+controlType+name":
            aid = str(ctrl.get("automationId", "") or ins.get("automationId", "")).strip().lower()
            ct = str(ctrl.get("controlType", "") or ins.get("controlType", "")).strip().lower()
            name = str(ctrl.get("name", "") or ins.get("name", "")).strip().lower()
            if aid:
                key = ["aid", aid, ct, name]
                node = self._interestarea_node_label(ctrl, panel_title_map)
                if node:
                    key.append("ia:" + node)
                return tuple(key)
            return ("name", name, ct)
        elif mode == "uiPath":
            key = ["ui", str(ctrl.get("uiPath", "") or ins.get("uiPath", "")).strip().lower()]
            node = self._interestarea_node_label(ctrl, panel_title_map)
            if node:
                key.append("ia:" + node)
            return tuple(key)
        elif mode == "name+controlType":
            name = str(ctrl.get("name", "") or ins.get("name", "")).strip().lower()
            ct = str(ctrl.get("controlType", "") or ins.get("controlType", "")).strip().lower()
            key = ["nc", name, ct]
            node = self._interestarea_node_label(ctrl, panel_title_map)
            if node:
                key.append("ia:" + node)
            return tuple(key)
        return ("raw", id(ctrl))

    def _execute_merge(self):
        """执行合并操作。"""
        src_path = self.source_path.get().strip()
        tgt_path = self.target_path.get().strip()

        if not src_path:
            messagebox.showwarning("提示", "请选择源文件。", parent=self.dialog)
            return
        if not tgt_path:
            messagebox.showwarning("提示", "请选择目标文件。", parent=self.dialog)
            return
        if os.path.abspath(src_path) == os.path.abspath(tgt_path):
            messagebox.showwarning("提示", "源文件和目标文件不能相同。", parent=self.dialog)
            return
        if not self.source_info:
            messagebox.showwarning("提示", "源文件未加载，请重新选择。", parent=self.dialog)
            return

        # 破坏性操作：合并会整库重写目标文件 —— 先确认（此前无任何确认）。
        if not messagebox.askyesno(
            "确认合并入库",
            "将把源文件的控件合并写入目标控件库（整文件重写）：\n\n"
            "{}\n\n"
            "写入前会先备份目标文件为 .bak；写入采用临时文件 + 原子替换。\n"
            "是否继续？".format(tgt_path),
            parent=self.dialog,
        ):
            return

        try:
            # 读取目标文件
            with open(tgt_path, encoding="utf-8") as f:
                target_data = json.load(f)

            src_data = self.source_info["data"]
            dedup_mode = self.dedup_var.get()

            # 提取控件列表（优先 flatControls，回退 controlDefinitions）
            src_controls = src_data.get("flatControls", []) or src_data.get("controlDefinitions", [])
            tgt_controls = target_data.get("flatControls", []) or target_data.get("controlDefinitions", [])

            src_count = len(src_controls)
            tgt_count_before = len(tgt_controls)

            # 构建去重索引：以 dedup key 为键，保留高权威条目
            tgt_panel_map = self._build_ia_panel_title_map(tgt_controls)
            src_panel_map = self._build_ia_panel_title_map(src_controls)
            merged_index = {}
            for ctrl in tgt_controls:
                key = self._build_dedup_key(ctrl, dedup_mode, tgt_panel_map)
                merged_index[key] = ctrl

            added = 0
            updated = 0
            for ctrl in src_controls:
                key = self._build_dedup_key(ctrl, dedup_mode, src_panel_map)
                if key in merged_index:
                    # 已存在：如果开启了高权威覆盖，则合并字段
                    if self.overwrite_var.get():
                        existing = merged_index[key]
                        for field, value in ctrl.items():
                            if field.startswith("_"):
                                continue
                            if value not in (None, "", [], {}) and not existing.get(field):
                                existing[field] = value
                        updated += 1
                else:
                    merged_index[key] = ctrl
                    added += 1

            merged_list = list(merged_index.values())

            # 合并 controlsTree（简单追加）
            src_tree = src_data.get("controlsTree", [])
            tgt_tree = target_data.get("controlsTree", [])
            if src_tree and not tgt_tree:
                target_data["controlsTree"] = src_tree
            elif src_tree and tgt_tree:
                # 简单合并：追加源树的顶层节点
                target_data["controlsTree"] = tgt_tree + src_tree

            # 更新控件列表
            if target_data.get("flatControls") is not None:
                target_data["flatControls"] = merged_list
            elif target_data.get("controlDefinitions") is not None:
                target_data["controlDefinitions"] = merged_list
            else:
                # 默认写入 flatControls
                target_data["flatControls"] = merged_list

            # 更新 metadata（mergeAdded/mergeUpdated 为累计值，与 canonical 实现
            # 一致；此前为覆盖赋值，反复合并后台账只剩最后一次的量）
            meta = target_data.get("scanMeta", {}) or {}
            meta["rawTotalControls"] = len(merged_list)
            meta["totalControls"] = len(merged_list)
            meta["mergedFrom"] = os.path.basename(src_path)
            meta["mergedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            meta["mergeAdded"] = int(meta.get("mergeAdded", 0) or 0) + added
            meta["mergeUpdated"] = int(meta.get("mergeUpdated", 0) or 0) + updated
            target_data["scanMeta"] = meta

            # 保存：先备份 + 临时文件原子替换（此前直接 open(w) 截断，写盘
            # 中途失败会把目标库损坏成半截 JSON 且无备份）
            backup_path = ""
            try:
                backup_path = tgt_path + ".bak"
                shutil.copy2(tgt_path, backup_path)
            except Exception:
                backup_path = ""
            tmp_path = tgt_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(target_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, tgt_path)

            msg = (
                f"合并完成！\n"
                f"目标文件: {os.path.basename(tgt_path)}\n"
                f"原有控件: {tgt_count_before}\n"
                f"源文件控件: {src_count}\n"
                f"新增: {added}  更新字段: {updated}\n"
                f"合并后总数: {len(merged_list)}"
                + (f"\n已备份: {os.path.basename(backup_path)}" if backup_path else "")
            )
            self.status_label.config(text=f"合并成功: +{added} 更新{updated}", fg=DETECTOR_THEME["success"])
            messagebox.showinfo("合并成功", msg, parent=self.dialog)

            # 通知父窗口刷新
            if self.on_merge_done:
                self.on_merge_done()

        except Exception as e:
            self.status_label.config(text="合并失败", fg=DETECTOR_THEME["danger"])
            messagebox.showerror("合并失败", f"合并过程出错:\n{e}", parent=self.dialog)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="实时控件检测器")
    parser.add_argument("--hud", action="store_true", help="以迷你 HUD 悬浮模式启动")
    cli_args = parser.parse_args()

    main_root = tk.Tk()
    main_root.withdraw()
    detector_win = ControlLiveDetectorWindow(main_root, start_in_hud=cli_args.hud)
    main_root.mainloop()
