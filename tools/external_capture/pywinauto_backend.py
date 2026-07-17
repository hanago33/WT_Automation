# encoding: utf-8
"""纯 Python pywinauto 后端 —— 无需外部 .NET 工具，立即可用。

用 Desktop(backend="uia").from_point(x,y) 与 UIA GetFocusedElement 实现
uia-peek 的"按坐标/焦点 peek 控件祖先链"，输出与 uiapeek_client 完全相同的
control_map 兼容格式（复用 uiapeek_client.chain_to_payload / save_payload）。

适用场景：UiaPeek 服务未运行、或不想下载/安装 .NET 运行时时的开箱即用后端。
与 uia-peek 的区别：
  - 优势：纯 Python，零外部依赖，复用现有 pywinauto；
  - 劣势：不支持 SignalR 实时录制流；Patterns 提取需 axe-windows bridge。

对外接口与 uiapeek_client.capture_at / capture_focused 同签名（base_url/timeout
参数仅为兼容，本后端忽略），便于在对话框里无缝替换/兜底。
"""
import ctypes

from . import uiapeek_client as up


def _safe(getter, default=None):
    try:
        value = getter()
    except Exception:
        return default
    return default if value is None else value


def _ei_from_wrapper(wrapper):
    return _safe(lambda: wrapper.element_info, None)


def _node_from_ei(ei, is_trigger=False):
    """UIAElementInfo -> uia-peek chain node 格式。"""
    aid = str(_safe(lambda: ei.automation_id, "") or "").strip()
    name = str(_safe(lambda: ei.name, "") or "").strip()
    ctype = str(_safe(lambda: ei.control_type, "") or "").strip()
    cls = str(_safe(lambda: ei.class_name, "") or "").strip()
    pid = str(_safe(lambda: ei.process_id, "") or "").strip()
    handle = str(_safe(lambda: ei.handle, 0) or 0)

    bounds = {}
    rect = _safe(lambda: ei.rectangle, None)
    if rect is not None:
        try:
            left = int(getattr(rect, "left", 0))
            top = int(getattr(rect, "top", 0))
            right = int(getattr(rect, "right", left))
            bottom = int(getattr(rect, "bottom", top))
            bounds = {"X": left, "Y": top, "width": right - left, "height": bottom - top}
        except Exception:
            bounds = {}

    return {
        "automationId": aid,
        "name": name,
        "controlType": ctype,
        "className": cls,
        "processId": pid,
        "bounds": bounds,
        "isTopWindow": (ctype.lower() == "window"),
        "isTriggerElement": is_trigger,
        "nativeWindowHandle": handle,
    }


def _ancestor_eis(ei, max_depth=24):
    """从 ei 往上收集祖先（含自己），返回 top-down 列表（末位 = ei）。"""
    chain = []
    current = ei
    seen = set()
    for _ in range(max_depth):
        if current is None:
            break
        handle = str(_safe(lambda: current.handle, 0) or 0)
        rid = _safe(lambda: id(current), 0)
        key = handle if handle and handle != "0" else rid
        if key in seen:
            break
        seen.add(key)
        chain.append(current)
        parent = _safe(lambda: current.parent, None)
        if not parent or parent is current:
            break
        current = parent
    chain.reverse()
    return chain


def _eis_to_path(chain):
    nodes = [_node_from_ei(ei, is_trigger=(i == len(chain) - 1)) for i, ei in enumerate(chain)]
    top_marked = False
    for node in nodes:
        if str(node.get("controlType", "")).lower() == "window":
            node["isTopWindow"] = True
            top_marked = True
            break
    if not top_marked and nodes:
        nodes[0]["isTopWindow"] = True
    return nodes


def _build_payload(chain, trigger, source_label):
    path = _eis_to_path(chain)
    if not path:
        return None
    chain_obj = {"locator": "pywinauto/{}".format(trigger.lower()), "path": path, "trigger": trigger}
    return up.chain_to_payload(chain_obj, source_label=source_label)


# ---------------------------------------------------------------------------
# 对外接口（与 uiapeek_client.capture_at / capture_focused 同签名）
# ---------------------------------------------------------------------------
def capture_at(x, y, save=True, control_map_dir=None, base_url=None, timeout=None):
    """按屏幕坐标 peek 控件祖先链。base_url/timeout 仅为接口兼容，忽略。"""
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    wrapper = _safe(lambda: desktop.from_point(int(x), int(y)), None)
    ei = _ei_from_wrapper(wrapper)
    if ei is None:
        raise RuntimeError("pywinauto from_point({},{}) 未取到控件".format(x, y))
    chain = _ancestor_eis(ei)
    payload = _build_payload(chain, "Point", "pywinauto")
    if payload is None:
        raise RuntimeError("pywinauto peek({},{}) 祖先链为空".format(x, y))
    fp = up.save_payload(payload, control_map_dir or up.CONTROL_MAP_DIR) if save else None
    return payload, fp


def capture_focused(save=True, control_map_dir=None, base_url=None, timeout=None):
    """peek 焦点控件祖先链。优先 UIA GetFocusedElement，失败回退前台窗口。"""
    ei = _get_focused_ei()
    if ei is None:
        ei = _get_foreground_window_ei()
    if ei is None:
        raise RuntimeError("未取到焦点控件，也未取到前台窗口（请先激活目标窗口）")
    chain = _ancestor_eis(ei)
    payload = _build_payload(chain, "Focus", "pywinauto")
    if payload is None:
        raise RuntimeError("pywinauto focused 祖先链为空")
    fp = up.save_payload(payload, control_map_dir or up.CONTROL_MAP_DIR) if save else None
    return payload, fp


def _get_foreground_window_ei():
    from pywinauto import Desktop

    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return None
    wrapper = _safe(lambda: Desktop(backend="uia").window(handle=int(hwnd)), None)
    return _ei_from_wrapper(wrapper)


def _get_focused_ei():
    """尝试 UIA GetFocusedElement 拿焦点控件。失败返回 None。"""
    try:
        from pywinauto.uia_defines import IUIA
        from pywinauto.uia_element_info import UIAElementInfo

        iuia = IUIA().iuia
        focused = iuia.GetFocusedElement()
        return UIAElementInfo(focused)
    except Exception:
        return None
