# encoding: utf-8
"""轻量界面状态持久化（待修改清单 #6）。

窗口几何与关键选项（轻量标量）集中落盘 %APPDATA%/wt_automation/ui_state.json，
按 tool_key 分节存取；启动时恢复，含「屏幕外坐标纠偏」防御——多显示器拔插后
窗口不得落在不可见区域。只持久化 str/int/float/bool，不存业务数据。

为什么集中一个文件：项目已有 _gui_config.json / txt2wtg_gui_config.json 两处
先例，若每个窗口再各存一份会继续碎片化；本模块是唯一的第三处（也是最后一处），
新窗口一律走 load/save。
写盘采用临时文件 + os.replace 原子替换（沿用项目写盘安全惯例）。
"""
import json
import os
import re

# 环境变量 WT_UI_STATE_FILE 仅用于测试注入临时文件路径
_GEOM_RE = re.compile(r"^(\d+)x(\d+)([+-]\d+)([+-]\d+)$")


def state_path():
    override = os.environ.get("WT_UI_STATE_FILE")
    if override:
        return override
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "wt_automation", "ui_state.json")


def load(tool_key):
    """读取单个工具的状态；文件缺失/损坏/类型不对时返回 {}（永不抛出）。"""
    try:
        with open(state_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    section = data.get(tool_key) if isinstance(data, dict) else None
    return dict(section) if isinstance(section, dict) else {}


def save(tool_key, state):
    """合并写入单个工具的状态；只接受轻量标量，失败静默（不得影响关窗流程）。"""
    clean = {
        key: value
        for key, value in (state or {}).items()
        if isinstance(value, (str, int, float, bool)) and value is not None
    }
    try:
        path = state_path()
        data = {}
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
        data[tool_key] = clean
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
        return True
    except OSError:
        return False


def virtual_screen_rect():
    """全虚拟桌面 (x, y, w, h)（Windows）；不可用时返回 None。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        left = user32.GetSystemMetrics(76)    # SM_XVIRTUALSCREEN
        top = user32.GetSystemMetrics(77)     # SM_YVIRTUALSCREEN
        width = user32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
        height = user32.GetSystemMetrics(79)  # SM_CYVIRTUALSCREEN
        if width > 0 and height > 0:
            return int(left), int(top), int(width), int(height)
    except Exception:
        pass
    return None


def sanitize_geometry(geom, virtual_rect=None):
    """把 "WxH+X+Y" 夹回可见区域；完全不可见时复位到安全位置。

    多显示器拔插后，保存的坐标可能指向已消失的屏幕。virtual_rect 缺省时
    按主屏 (0, 0, 1920, 1080) 近似。无法解析时返回 None（调用方跳过恢复）。
    """
    match = _GEOM_RE.match(str(geom or ""))
    if not match:
        return None
    width, height, x, y = (int(part) for part in match.groups())
    if virtual_rect:
        vx, vy, vw, vh = virtual_rect
    else:
        vx, vy, vw, vh = 0, 0, 1920, 1080
    width = min(max(width, 320), vw)
    height = min(max(height, 240), vh)
    visible = (
        x + width > vx + 120 and x < vx + vw - 120
        and y + height > vy + 80 and y < vy + vh - 80
    )
    if not visible:
        x, y = vx + 40, vy + 40  # 完全落在可见区域外 → 复位
    else:
        x = min(max(x, vx), vx + vw - 200)
        y = min(max(y, vy), vy + vh - 160)
    # {:+d}：位置自带符号（负坐标 = 主屏左侧的显示器），不能拼死 "+"
    return "{}x{}{:+d}{:+d}".format(width, height, x, y)


def capture_window_geometry(window):
    """读取窗口当前 "WxH+X+Y"；失败返回 None。"""
    try:
        return window.winfo_geometry()
    except Exception:
        return None


def apply_window_geometry(window, geom, virtual_rect=None):
    """按 sanitize 后的几何串设置窗口；geom 缺失/无效时不动。"""
    try:
        sanitized = sanitize_geometry(geom, virtual_rect=virtual_rect)
        if sanitized:
            window.geometry(sanitized)
            return True
    except Exception:
        pass
    return False
