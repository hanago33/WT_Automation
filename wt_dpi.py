# encoding: utf-8

"""tkinter 高 DPI 支持与界面缩放（Windows）。

问题背景：
    WT_Automation 自带的桌面窗口（主控台 WT_Launcher、链路编辑器 WT_Flow_Editor）
    都是 tkinter 程序。在 2K / 高分屏上，如果进程不声明 DPI 感知，Windows 会对部分
    窗口做“DPI 虚拟化”位图拉伸（看起来是 1080p 正常大小但发虚），而另一些窗口按原生
    2K 渲染（内容显小/“缩小”）。这导致不同窗口之间分辨率/缩放不一致。

本模块统一解决：
    1. 在创建 tk.Tk() 之前声明进程为 Per-Monitor DPI Aware (V2)；
    2. 按屏幕“物理分辨率 / 逻辑分辨率”自动计算缩放系数（即系统“显示缩放”），并应用到
       Tk 的 scaling，使字体、控件在 2K 屏上一致缩放且清晰；
    3. 支持用户手动选择“界面缩放”档位（自动 / 100% / 125% / 150% / 175% / 200%），
       行为类似 Windows 的“显示缩放”，配置保存在 workspace/ui_scale.json，所有窗口
       （主控台、链路编辑器、构建工具、监视器等）共用同一份设置。
    4. 对 tk.Widget.geometry 做一次性 monkeypatch：本进程内任何 .geometry("WxH...") 调用
       的尺寸都会按系数自动缩放（屏幕坐标 +X+Y 保持不动）。因此只要某个独立入口在创建
       Tk() 时调用一次 compute_scale()，它里面【所有窗口（含 dialog / Toplevel）】都会
       自动套用缩放，无需逐个改写几何字符串。

用法：
    import wt_dpi
    wt_dpi.enable_process_dpi_awareness()   # 必须在 tk.Tk() 之前
    root = tk.Tk()
    wt_dpi.compute_scale(root)              # 必须在 tk.Tk() 之后、布局之前（自动缩放所有窗口）
    wt_dpi.geometry(self.window, 1500, 900) # 代替 self.window.geometry("1500x900")
    self.window.minsize(wt_dpi.scale(1260), wt_dpi.scale(760))

用户在 UI 中改变缩放后：
    wt_dpi.apply_scale(self.root, value, 1500, 900)  # value=None 表示自动
"""

import ctypes
import json
import os
import platform
import re
import tkinter as tk
from datetime import datetime

# 模块级缓存的 DPI 缩放系数（每个进程独立，随 compute_scale() 更新）。
_DPI_SCALE = 1.0
# 显式覆盖值（例如来自 --ui-scale 命令行参数）。None 表示使用共享配置/自动检测。
_USER_SCALE = None

# 共享的界面缩放配置文件（两个窗口进程共用）。
UI_SCALE_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "workspace", "ui_scale.json"
)

# 默认界面缩放档位（系数）。首次启动、且尚无配置文件时使用，用户建议默认 150%。
# 用户若显式选择“自动”，则写入配置并跟随系统显示缩放（见 load_scale_config）。
DEFAULT_SCALE = 1.5

# 界面缩放档位：标签 -> 系数（None 表示“自动”，即跟随系统显示缩放）。
SCALE_PRESETS = [
    ("自动", None),
    ("100%", 1.0),
    ("125%", 1.25),
    ("150%", 1.5),
    ("175%", 1.75),
    ("200%", 2.0),
]


def scale_to_label(value):
    """把系数转换为档位标签（None -> 自动）。"""
    if value is None:
        return "自动"
    for label, val in SCALE_PRESETS:
        if val is not None and abs(val - value) < 1e-6:
            return label
    return "自动"


def label_to_scale(label):
    """把档位标签转换为系数（未知标签 -> 自动/None）。"""
    for lbl, val in SCALE_PRESETS:
        if lbl == label:
            return val
    return None


def load_scale_config():
    """读取共享界面缩放配置。

    返回系数（float），或 None（表示“自动”，即跟随系统显示缩放）。
    注意：当配置文件不存在（首次启动）或读取失败时，返回 DEFAULT_SCALE（默认 150%），
    使界面一开始就用放大的档位，避免在高分屏上看不出效果。
    """
    try:
        if os.path.exists(UI_SCALE_CONFIG_FILE):
            with open(UI_SCALE_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            raw = data.get("scale", "auto")
            if raw in (None, "auto", ""):
                return None
            return float(raw)
    except Exception:
        pass
    return DEFAULT_SCALE


def save_scale_config(value):
    """保存界面缩放配置（value 为系数 float 或 None 表示自动）。"""
    try:
        os.makedirs(os.path.dirname(UI_SCALE_CONFIG_FILE), exist_ok=True)
        payload = {
            "scale": ("auto" if value is None else float(value)),
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
        }
        with open(UI_SCALE_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_user_scale(value):
    """显式设置本进程的缩放系数覆盖（例如来自 --ui-scale）。None 表示不覆盖。"""
    global _USER_SCALE
    _USER_SCALE = value


def enable_process_dpi_awareness():
    """在创建 tk.Tk() 之前调用一次，声明进程为 Per-Monitor DPI Aware (V2)。

    若系统不支持或调用失败，会静默回退，不影响程序运行。
    """
    if platform.system() != "Windows":
        return
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2（V2 提供最锐利且一致的缩放）
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _detect_scale():
    """按屏幕物理/逻辑分辨率自动检测缩放系数（即系统显示缩放）。"""
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        logical = ctypes.windll.gdi32.GetDeviceCaps(hdc, 8)      # HORZRES：逻辑像素宽
        physical = ctypes.windll.gdi32.GetDeviceCaps(hdc, 118)   # DESKTOPHORZRES：物理像素宽
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if logical and physical and physical != logical:
            return physical / float(logical)
    except Exception:
        pass
    return 1.0


# ---------------------------------------------------------------------------
# geometry 自动缩放（monkeypatch）
# ---------------------------------------------------------------------------
# 匹配 "WxH" 或 "WxH+X+Y" 或 "WxH-X-Y"；仅缩放 W、H，屏幕坐标 +X+Y / -X-Y 保持不动。
_GEOMETRY_RE = re.compile(r"^(\d+)x(\d+)([+-]\d+)?([+-]\d+)?$")
_ORIG_GEOMETRY = None


def _scale_geometry_string(value):
    """若 geometry 字符串含尺寸，按当前系数缩放 W、H；否则原样返回。"""
    if not isinstance(value, str):
        return value
    m = _GEOMETRY_RE.match(value.strip())
    if not m:
        return value
    w = int(round(int(m.group(1)) * _DPI_SCALE))
    h = int(round(int(m.group(2)) * _DPI_SCALE))
    x = m.group(3) or ""
    y = m.group(4) or ""
    return "%dx%d%s%s" % (w, h, x, y)


def _install_geometry_patch():
    """一次性替换 tk.Wm.geometry，使所有窗口尺寸按 DPI 系数自动缩放。

    geometry() 方法定义在窗口管理器 mixin tk.Wm 上（Tk / Toplevel 均继承自它）。
    幂等：重复调用只生效一次。本进程内此后任何 .geometry("WxH...") 调用都会自动缩放，
    无需逐个改写。必须在 compute_scale() 中（Tk 创建后）调用。
    """
    global _ORIG_GEOMETRY
    if _ORIG_GEOMETRY is not None:
        return
    _ORIG_GEOMETRY = tk.Wm.geometry

    def _geometry(self, newGeometry=None):
        if isinstance(newGeometry, str):
            newGeometry = _scale_geometry_string(newGeometry)
        return _ORIG_GEOMETRY(self, newGeometry)

    tk.Wm.geometry = _geometry


def compute_scale(root, override=None):
    """根据（覆盖值 > 共享配置 > 自动检测）计算缩放系数，并应用到 Tk 的 scaling。

    必须在 tk.Tk() 创建之后、任何布局之前调用。返回缩放系数。
    """
    global _DPI_SCALE
    if override is not None:
        candidate = float(override)
    elif _USER_SCALE is not None:
        candidate = float(_USER_SCALE)
    else:
        cfg = load_scale_config()
        candidate = float(cfg) if cfg is not None else _detect_scale()

    # 钳制，避免异常显示环境把界面撑爆或缩没
    _DPI_SCALE = max(1.0, min(candidate, 3.0))

    try:
        # Tk 在 96 DPI 下默认 scaling=1.333；按实际系数放大，字体/控件一致缩放。
        # 该值与 Tk 在 DPI 感知下的自动取值一致（dpi/72），此处显式设定以保证确定性。
        root.tk.call("tk", "scaling", 1.333 * _DPI_SCALE)
    except Exception:
        pass

    # 让本进程内所有 .geometry() 调用自动按系数缩放（含 dialog / Toplevel）
    _install_geometry_patch()

    return _DPI_SCALE


def apply_scale(root, value, base_width, base_height):
    """用户改变缩放后的便捷入口：写共享配置 + 设置覆盖 + 重新计算 + 重设根窗口几何。

    value 为系数（float）或 None（自动）；base_width/base_height 为窗口设计的基准像素。
    重设主窗口几何后会触发 Tk 重新分配布局，使字体/控件即时跟随新缩放重排。
    """
    set_user_scale(value)
    save_scale_config(value)
    compute_scale(root)
    geometry(root, base_width, base_height)
    try:
        root.update_idletasks()
    except Exception:
        pass


def scale(value):
    """按当前 DPI 系数缩放一个像素长度（用于 minsize / 内边距 / 固定宽高）。"""
    return int(round(value * _DPI_SCALE))


def geometry(window, width, height):
    """按 DPI 系数设置窗口几何尺寸，等价于 window.geometry('WxH')。

    实际尺寸缩放由本模块对 tk.Widget.geometry 的 monkeypatch 统一完成，
    此处仅拼接 "WxH" 字符串，避免双重缩放。
    """
    window.geometry("%dx%d" % (int(width), int(height)))


def raw_geometry(window, geometry_string):
    """设置窗口几何但【绕过 DPI 自动缩放】，直接使用给定的真实像素值。

    适用于本身就以真实屏幕像素表达的窗口，这些值不应再被系数放大，否则会错位：
      - 全屏框选遮罩（尺寸取自 winfo_screenwidth/height）；
      - 控件高亮框（尺寸/坐标取自目标控件的实际屏幕 rect）；
      - Toast（尺寸取自 winfo_reqwidth/height，已含 Tk scaling）。

    若 geometry patch 尚未安装（未调用过 compute_scale），退化为普通 geometry。
    """
    if _ORIG_GEOMETRY is not None:
        _ORIG_GEOMETRY(window, geometry_string)
    else:
        window.geometry(geometry_string)


def get_scale():
    """返回当前缩放系数。"""
    return _DPI_SCALE
