# encoding: utf-8

import argparse
import collections
import ctypes
import json
import os
import queue
import copy
import shutil
import re
import subprocess
import sys
import threading
import tkinter as tk
import time

_HOVER_MONITOR_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_hover_monitor.log")

def _hover_log(msg):
    """同时输出到控制台和日志文件。"""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(_HOVER_MONITOR_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _play_notify_sound():
    """采集结束提示音；winsound 不可用（非 Windows）时静默。"""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass

# 强制 COM 初始化为 MTA 模式 (Multi-Threaded Apartment)
# 这是 pywinauto 社区推荐的 UIA 后端核心性能优化，能大幅提升跨进程 COM 调用的速度。
# 必须在导入 pywinauto 或 comtypes 之前设置。
sys.coinit_flags = 0

from ctypes import wintypes
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

import wt_dpi
import wt_flow_editor_utils

# pynput 全局热键：用于悬停跟踪模式下的冻结/采集/树导航快捷键。
# 仅在探测模式开启时生效，避免与 Tk 自身快捷键冲突。
try:
    from pynput import keyboard as _pynput_keyboard
    _PYNPUT_AVAILABLE = True
except Exception:
    _pynput_keyboard = None
    _PYNPUT_AVAILABLE = False

try:
    from pywinauto import Desktop
    import pywinauto
    import comtypes
except Exception:
    Desktop = None
    pywinauto = None
    comtypes = None


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONTROL_MAP_DIR = os.path.join(BASE_DIR, "control_maps")
DEFAULT_BACKEND = "smart"
DEFAULT_MAX_DEPTH = 10

# 自动合并入库时，总控件信息.json 历史备份最多保留的份数（超出按时间删旧留新）
MAX_MASTER_BACKUPS = 5


CONTROL_MAP_THEME = {
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
    "font": ("Microsoft YaHei UI", 10),
}


def _paint_button(btn, tone="default"):
    """按统一色板给 tk.Button 上色；tone 取 default/primary/success/danger/warning。"""
    soft = {
        "default": CONTROL_MAP_THEME["panel"],
        "primary": CONTROL_MAP_THEME["primary_soft"],
        "success": CONTROL_MAP_THEME["success_soft"],
        "danger": CONTROL_MAP_THEME["danger_soft"],
        "warning": CONTROL_MAP_THEME["warning_soft"],
    }
    fg = {
        "default": CONTROL_MAP_THEME["text"],
        "primary": CONTROL_MAP_THEME["primary"],
        "success": CONTROL_MAP_THEME["success"],
        "danger": CONTROL_MAP_THEME["danger"],
        "warning": CONTROL_MAP_THEME["warning"],
    }
    btn.configure(
        bg=soft.get(tone, soft["default"]),
        fg=fg.get(tone, fg["default"]),
        activebackground=CONTROL_MAP_THEME["panel_soft"],
        activeforeground=fg.get(tone, fg["default"]),
        relief="flat",
        bd=1,
        highlightthickness=1,
        highlightbackground=CONTROL_MAP_THEME["border"],
        cursor="hand2",
        padx=10,
        pady=3,
        font=CONTROL_MAP_THEME["font"],
    )
    return btn




class _TaskbarProgress:
    """Windows 任务栏进度条（通过 ITaskbarList3 COM 接口）。

    在采集期间显示进度，给用户直观的反馈。comtypes 不可用或
    非 Windows 平台时静默降级为 no-op。
    """
    TBPF_NOPROGRESS = 0x0
    TBPF_NORMAL = 0x1
    TBPF_PAUSED = 0x8
    TBPF_ERROR = 0x4

    def __init__(self, root):
        self._hwnd = None
        self._taskbar = None
        try:
            self._hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        except Exception:
            return
        if comtypes is None:
            return
        try:
            from comtypes.client import GetActiveObject, CreateObject
            try:
                self._taskbar = GetActiveObject(
                    "{56FDF344-FD6D-11d0-958A-006097C9A090}"
                )
            except Exception:
                self._taskbar = CreateObject(
                    "{56FDF344-FD6D-11d0-958A-006097C9A090}"
                )
        except Exception:
            self._taskbar = None

    def set_state(self, state):
        if self._taskbar and self._hwnd:
            try:
                self._taskbar.SetProgressState(self._hwnd, state)
            except Exception:
                pass

    def set_value(self, current, total):
        if self._taskbar and self._hwnd and total > 0:
            try:
                self._taskbar.SetProgressValue(self._hwnd, current, total)
            except Exception:
                pass

    def clear(self):
        self.set_state(self.TBPF_NOPROGRESS)


class _ScanProgressOverlay:
    """采集进度置顶浮窗：实时显示控件数，结束/中止显示结果几秒后自动消失。

    设计原因：整树/画框采集常需把采集器窗口最小化让目标软件置前，此时主界面
    状态栏不可见，用户无法感知进度与结束。本浮窗独立于主窗口置顶在右上角，
    鼠标穿透（WS_EX_TRANSPARENT）不拦截目标软件操作、不抢焦点（WS_EX_NOACTIVATE），
    采集结束变绿显示结果，数秒后自动消失，配合提示音强调完成。
    """
    _GWL_EXSTYLE = -20
    _WS_EX_TRANSPARENT = 0x00000020
    _WS_EX_NOACTIVATE = 0x08000000
    _AUTO_CLOSE_MS = 4000

    def __init__(self, parent):
        self._parent = parent
        self._window = None
        self._label = None
        self._auto_close_id = None

    def _ensure_window(self):
        if self._window is not None:
            try:
                if self._window.winfo_exists():
                    return self._window
            except Exception:
                pass
            self._window = None
        window = tk.Toplevel(self._parent)
        window.overrideredirect(True)
        try:
            window.attributes("-topmost", True)
        except Exception:
            pass
        window.configure(bg=CONTROL_MAP_THEME["toolbar"], highlightthickness=1,
                         highlightbackground=CONTROL_MAP_THEME["border"])
        label = tk.Label(window, text="", justify="left", anchor="w",
                         bg=CONTROL_MAP_THEME["toolbar"], fg=CONTROL_MAP_THEME["text"],
                         padx=14, pady=8, wraplength=340,
                         font=(CONTROL_MAP_THEME["font"][0], 10))
        label.pack(fill=tk.BOTH, expand=True)
        # 鼠标穿透 + 不抢焦点。注意：不能加 WS_EX_LAYERED——Tk 窗口没有配合
        # transparentcolor/SetLayeredWindowAttributes 设置颜色键时，LAYERED 窗口
        # 会被系统直接跳过绘制，导致浮窗建了却看不见（2026-08-12 实测像素级验证）。
        try:
            hwnd_str = window.frame() if window.frame() else str(window.winfo_id())
            hwnd = int(hwnd_str, 16)
            style = ctypes.windll.user32.GetWindowLongW(hwnd, self._GWL_EXSTYLE)
            new_style = style | self._WS_EX_TRANSPARENT | self._WS_EX_NOACTIVATE
            ctypes.windll.user32.SetWindowLongW(hwnd, self._GWL_EXSTYLE, new_style)
        except Exception:
            pass
        self._window = window
        self._label = label
        return window

    def _position_top_right(self):
        try:
            sw = self._parent.winfo_screenwidth()
            self._window.update_idletasks()
            width = max(self._window.winfo_reqwidth(), 160)
            height = self._window.winfo_reqheight()
            x = sw - width - 14
            self._window.geometry(f"{width}x{height}+{x}+10")
        except Exception:
            pass

    def _set_text(self, text, fg=None, bg=None):
        window = self._ensure_window()
        if self._label is not None:
            self._label.configure(text=text, fg=fg or CONTROL_MAP_THEME["text"])
        if bg:
            window.configure(bg=bg)
            if self._label is not None:
                self._label.configure(bg=bg)
        self._position_top_right()
        try:
            window.deiconify()
            window.lift()
        except Exception:
            pass

    def _cancel_auto_close(self):
        if self._auto_close_id is not None:
            try:
                self._parent.after_cancel(self._auto_close_id)
            except Exception:
                pass
            self._auto_close_id = None

    def _schedule_auto_close(self, ms=_AUTO_CLOSE_MS):
        self._cancel_auto_close()
        try:
            self._auto_close_id = self._parent.after(ms, self.close)
        except Exception:
            pass

    def show_progress(self, message, count):
        self._cancel_auto_close()
        self._set_text(f"扫描中… 已采集 {count} 个控件\n{message}",
                       fg=CONTROL_MAP_THEME["text"], bg=CONTROL_MAP_THEME["toolbar"])

    def show_done(self, message):
        self._set_text(message, fg=CONTROL_MAP_THEME["success"], bg=CONTROL_MAP_THEME["success_soft"])
        self._schedule_auto_close()

    def show_cancelled(self, message):
        self._set_text(message, fg=CONTROL_MAP_THEME["warning"], bg=CONTROL_MAP_THEME["warning_soft"])
        self._schedule_auto_close()

    def show_error(self, message):
        self._set_text(message, fg=CONTROL_MAP_THEME["danger"], bg=CONTROL_MAP_THEME["danger_soft"])
        self._schedule_auto_close(ms=6000)

    def close(self):
        self._cancel_auto_close()
        if self._window is not None:
            try:
                self._window.withdraw()
            except Exception:
                pass
# 整树采集（无画框区域）时的最小递归深度。WPF 控件树嵌套很深，
# 默认 10 层往往覆盖不到滚动区/折叠面板/未激活 Tab 中的深层控件。
FULLTREE_MIN_DEPTH = 18
DEFAULT_PICK_DELAY_SECONDS = 3
BACKEND_OPTIONS = ["smart", "uia", "win32"]

# 独立 .NET 工具：走 UIA RawViewWalker 全量遍历控件树（对齐 Accessibility Insights 的采全能力）。
# 由 Python 通过 subprocess 调用，输出 JSON 到 stdout，用于在整树采集时补采纯 pywinauto 遍历漏掉的深层控件。
UIA_TREE_DUMPER_EXE = os.path.join(
    BASE_DIR,
    "uia_tree_dumper",
    "uia_tree_dumper",
    "bin",
    "Release",
    "net10.0-windows",
    "uia_tree_dumper.exe",
)

user32 = ctypes.windll.user32


def _safe_get_value(getter, default=""):
    try:
        value = getter()
    except Exception:
        return default
    return default if value is None else value


def ensure_directory(path):
    os.makedirs(path, exist_ok=True)


def slugify_filename(text, fallback="window"):
    return wt_flow_editor_utils.slugify_filename(text, fallback)


def normalize_control_type_name(control_type, localized_control_type=""):
    control_type = str(control_type or "").strip()
    if control_type.startswith("UIA_") and "ControlTypeId" in control_type:
        control_type = control_type.replace("UIA_", "").replace("ControlTypeId", "").strip()
    matched = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*\(\d+\)\s*$", control_type)
    if matched:
        control_type = matched.group(1)
    return control_type or str(localized_control_type or "").strip()


def build_locator_recommendation(parsed, index=-1, ui_path=None):
    automation_id = str(parsed.get("automationId", "")).strip()
    name = str(parsed.get("name", "")).strip()
    class_name = str(parsed.get("className", "")).strip()
    handle = str(parsed.get("nativeWindowHandle", "")).strip()
    control_type = normalize_control_type_name(parsed.get("controlType", ""), parsed.get("localizedControlType", ""))

    # 尝试将 Value/ToggleState 作为补充定位特征（如果存在的话）
    value = str(parsed.get("value", "")).strip()

    # 过滤掉 SVG path 几何数据、超长乱码等无效 name
    if name and (_is_garbage_name(name) or len(name) > 80):
        name = ""
    # 关联标签文本（labelText）：标签伴随定位法的定位值，同样过滤乱码/碎片。
    # 该值来自真实的标签元素（运行时按标签找邻近控件），比回填的 name 更可靠。
    label_text = _clean_label_text(parsed.get("labelText", ""))

    candidates = [
        ("automation_id,control_type", [automation_id, control_type], 100, "automation_id + control_type"),
        ("automation_id,class_name", [automation_id, class_name], 96, "automation_id + class_name"),
        ("automation_id", [automation_id], 92, "automation_id"),
        ("name,control_type", [name, control_type], 88, "name + control_type"),
        ("name,class_name", [name, class_name], 84, "name + class_name"),
        ("name", [name], 78, "name"),
        ("label_text,control_type", [label_text, control_type], 76, "label_text + control_type（标签伴随定位）"),
        ("label_text", [label_text], 72, "label_text（标签伴随定位）"),
        ("class_name,control_type", [class_name, control_type], 68, "class_name + control_type"),
        ("class_name", [class_name], 58, "class_name"),
        ("control_type", [control_type], 42, "control_type"),
        ("handle", [handle], 24, "handle"),
    ]
    
    for method, values, score, reason in candidates:
        if all(str(item).strip() and str(item).strip() != "[null]" for item in values):
            return method, ",".join(values), score, reason
            
    # 结构化兜底：优先用层级 uiPath（运行时已支持，比 found_index 更稳），
    # 其次才退回 found_index；都没有则放弃（标记为 no_stable_locator）。
    if ui_path:
        return "ui_path", ui_path, 12, "结构化兜底: ui_path"
    if control_type and index >= 0:
        return "control_type,found_index", f"{control_type},{index}", 10, "结构化兜底: control_type + found_index"
    if class_name and index >= 0:
        return "class_name,found_index", f"{class_name},{index}", 8, "结构化兜底: class_name + found_index"

    return "", "", 0, "no_stable_locator"


def _is_garbage_name(name):
    """判断 name 是否是 SVG path / 几何数据 / 乱码，不可作为可读名称"""
    if not name:
        return False
    name = str(name).strip()
    # 纯特殊字符
    if re.match(r"^[^a-zA-Z0-9\u4e00-\u9fa5]+$", name):
        return True
    # SVG path 数据特征：以 M/L/C/A/H/V/Z 开头并包含大量数字和逗号
    if re.match(r"^[MLCAHVZmlcahvz][\d.,\s\-]+", name) and name.count(",") >= 3:
        return True
    # 纯坐标序列
    if re.match(r"^[\d.,\s\-]+$", name) and len(name) > 10:
        return True
    # Base64 编码特征
    if len(name) > 40 and re.match(r"^[A-Za-z0-9+/=]+$", name):
        return True
    return False


def _clean_label_text(text):
    """丢弃"碎片标签"：纯标点/乱码 → 空串。保留长度>=1 的真实标签。

    注意：**不按"长度<2"丢弃单字符标签**——MUP 综合编辑器里海拔输入框的紧邻
    标签就是单字符 '在'（"空气密度在[海拔]"语境），按长度判碎片会误删合法标签。
    仅丢弃 _is_garbage_name 判定的乱码/纯标点/SVG 与超长串。
    """
    cleaned = str(text or "").strip()
    if not cleaned:
        return ""
    if _is_garbage_name(cleaned) or len(cleaned) > 80:
        return ""
    return cleaned


def build_aux_checks(parsed):
    checks = []
    for key, label in [
        ("isEnabled", "IsEnabled"),
        ("isOffscreen", "IsOffscreen"),
        ("isKeyboardFocusable", "IsKeyboardFocusable"),
        ("hasKeyboardFocus", "HasKeyboardFocus"),
        ("frameworkId", "FrameworkId"),
        ("className", "ClassName"),
    ]:
        value = str(parsed.get(key, "")).strip()
        if value:
            checks.append(f"{label}={value}")
    return checks


def assess_control_automatability(flat_control):
    """对单个扁平控件做"可自动化体检"，返回 (风险等级, 原因列表)。

    风险等级: 高 / 中 / 低。
    复用的 UIA 属性（IsEnabled / IsKeyboardFocusable / IsOffscreen / FrameworkId）
    与 Accessibility Insights FastPass 关注的同源，是 pywinauto 运行时能否驱动
    控件的关键信号。

    与 _classify_control_quality 互补：质量分级看"能否稳定定位"，本函数看
    "运行时能否被驱动"（禁用 / 不可聚焦 / 离屏 / 非标准框架）。
    """
    if not isinstance(flat_control, dict):
        return "未知", []
    inspect = flat_control.get("inspectData")
    if not isinstance(inspect, dict):
        inspect = {}

    def _flag(key):
        value = str(inspect.get(key, "")).strip().lower()
        if not value:
            value = str(flat_control.get(key, "")).strip().lower()
        return value

    is_enabled = _flag("isEnabled")
    is_keyboard_focusable = _flag("isKeyboardFocusable")
    is_offscreen = _flag("isOffscreen")
    framework_id = _flag("frameworkId")
    control_type = str(flat_control.get("controlType", "")).strip().lower()
    has_automation_id = bool(str(flat_control.get("automationId", "")).strip())
    has_name = bool(str(flat_control.get("name", "")).strip())

    high = []
    medium = []

    # 高：运行时完全无法被驱动
    if is_enabled == "false":
        high.append("控件已禁用(IsEnabled=false)，不会响应操作")
    if is_keyboard_focusable == "false":
        high.append("控件不可键盘聚焦(IsKeyboardFocusable=false)，pywinauto 难以驱动")
    if (not has_automation_id) and (not has_name) and control_type in {
        "custom", "pane", "group", "image", "document", "text",
    }:
        high.append("缺少 automationId 与 name 且为非交互类型，难以稳定定位")

    # 中：存在不稳定因素
    if is_offscreen == "true":
        medium.append("控件当前离屏(IsOffscreen=true)，运行时可能定位失败")
    if framework_id in {"directuihwnd", "win32"}:
        medium.append(f"框架为 {framework_id}，非标准 UI 自动化，定位可能不稳定")
    if control_type in {"custom", "pane", "group"} and not has_automation_id:
        medium.append("容器/自定义类型且无 automationId，定位依赖层级或文本")

    reasons = high + medium
    level = "高" if high else ("中" if medium else "低")
    return level, reasons


def build_synthetic_inspect_text(inspect_data):
    lines = [
        f'Name: \t "{inspect_data.get("name", "")}"',
        f'ControlType: \t {inspect_data.get("controlType", "")}',
        f'LocalizedControlType: \t "{inspect_data.get("localizedControlType", "")}"',
        f'BoundingRectangle: \t {inspect_data.get("boundingRectangle", "")}',
        f'IsEnabled: \t {inspect_data.get("isEnabled", "")}',
        f'IsOffscreen: \t {inspect_data.get("isOffscreen", "")}',
        f'IsKeyboardFocusable: \t {inspect_data.get("isKeyboardFocusable", "")}',
        f'HasKeyboardFocus: \t {inspect_data.get("hasKeyboardFocus", "")}',
        f'ProcessId: \t {inspect_data.get("processId", "")}',
        f'FrameworkId: \t "{inspect_data.get("frameworkId", "")}"',
        f'ClassName: \t "{inspect_data.get("className", "")}"',
        f'AutomationId: \t "{inspect_data.get("automationId", "")}"',
        f'NativeWindowHandle: \t {inspect_data.get("nativeWindowHandle", "")}',
        f'ProviderDescription: \t "{inspect_data.get("providerDescription", "")}"',
        f'LegacyIAccessible.Name: \t "{inspect_data.get("legacyName", "")}"',
        f'LegacyIAccessible.Role: \t {inspect_data.get("legacyRole", "")}',
        f'LegacyIAccessible.State: \t {inspect_data.get("legacyState", "")}',
        f'AccessKey: \t "{inspect_data.get("accessKey", "")}"',
        f'AcceleratorKey: \t "{inspect_data.get("acceleratorKey", "")}"',
        f'ItemType: \t "{inspect_data.get("itemType", "")}"',
        f'ItemStatus: \t "{inspect_data.get("itemStatus", "")}"',
        f'IsContentElement: \t {inspect_data.get("isContentElement", "")}',
        f'IsControlElement: \t {inspect_data.get("isControlElement", "")}',
        f'IsPassword: \t {inspect_data.get("isPassword", "")}',
        f'HelpText: \t "{inspect_data.get("helpText", "")}"',
        f'LabeledBy: \t "{inspect_data.get("labeledByName", "")}"',
        f'SupportedPatterns: \t "{", ".join(str(p) for p in (inspect_data.get("supportedPatterns") or []))}"',
        f'LegacyIAccessible.ChildId: \t {inspect_data.get("legacyChildId", "")}',
        f'LegacyIAccessible.Value: \t "{inspect_data.get("legacyValue", "")}"',
        f'LegacyIAccessible.Description: \t "{inspect_data.get("legacyDescription", "")}"',
        f'LegacyIAccessible.Role: \t {inspect_data.get("legacyRoleText", "") or inspect_data.get("legacyRole", "")}',
        f'LegacyIAccessible.State: \t {inspect_data.get("legacyStateText", "") or inspect_data.get("legacyState", "")}',
        f'LegacyIAccessible.Help: \t "{inspect_data.get("legacyHelp", "")}"',
        f'LegacyIAccessible.Kbshortcut: \t "{inspect_data.get("legacyKeyboardShortcut", "")}"',
        f'LegacyIAccessible.DefAction: \t "{inspect_data.get("legacyDefaultAction", "")}"',
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


def _format_runtime_id(runtime_id_raw):
    if isinstance(runtime_id_raw, (list, tuple)) and runtime_id_raw:
        values = []
        for item in runtime_id_raw:
            try:
                values.append(hex(int(item))[2:].upper())
            except Exception:
                values.append(str(item))
        return "[" + ",".join(values) + "]"
    return str(runtime_id_raw or "").strip()


def _format_rectangle(rect):
    if not rect:
        return ""
    left = _safe_get_value(lambda: rect.left, "")
    top = _safe_get_value(lambda: rect.top, "")
    right = _safe_get_value(lambda: rect.right, "")
    bottom = _safe_get_value(lambda: rect.bottom, "")
    return f"[l={left},t={top},r={right},b={bottom}]"


def _rect_to_dict(rect):
    if not rect:
        return None
    left = _safe_get_value(lambda: int(rect.left), None)
    top = _safe_get_value(lambda: int(rect.top), None)
    right = _safe_get_value(lambda: int(rect.right), None)
    bottom = _safe_get_value(lambda: int(rect.bottom), None)
    if None in {left, top, right, bottom}:
        return None
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _normalize_rect_dict(rect):
    if not isinstance(rect, dict):
        return None
    try:
        left = int(rect.get("left"))
        top = int(rect.get("top"))
        right = int(rect.get("right"))
        bottom = int(rect.get("bottom"))
    except Exception:
        return None
    if right <= left or bottom <= top:
        return None
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _rect_contains(inner, outer):
    inner = _normalize_rect_dict(inner)
    outer = _normalize_rect_dict(outer)
    if not inner or not outer:
        return False
    return (
        inner["left"] >= outer["left"]
        and inner["top"] >= outer["top"]
        and inner["right"] <= outer["right"]
        and inner["bottom"] <= outer["bottom"]
    )


def _rect_intersects(a, b):
    a = _normalize_rect_dict(a)
    b = _normalize_rect_dict(b)
    if not a or not b:
        return False
    return not (
        a["right"] <= b["left"]
        or a["left"] >= b["right"]
        or a["bottom"] <= b["top"]
        or a["top"] >= b["bottom"]
    )


def _rect_area(rect):
    rect = _normalize_rect_dict(rect)
    if not rect:
        return 0
    return max(0, rect["right"] - rect["left"]) * max(0, rect["bottom"] - rect["top"])


def _intersection_area(a, b):
    a = _normalize_rect_dict(a)
    b = _normalize_rect_dict(b)
    if not a or not b:
        return 0
    left = max(a["left"], b["left"])
    top = max(a["top"], b["top"])
    right = min(a["right"], b["right"])
    bottom = min(a["bottom"], b["bottom"])
    if right <= left or bottom <= top:
        return 0
    return (right - left) * (bottom - top)


def _rect_center(rect):
    rect = _normalize_rect_dict(rect)
    if not rect:
        return None
    return ((rect["left"] + rect["right"]) / 2.0, (rect["top"] + rect["bottom"]) / 2.0)


def _distance(point_a, point_b):
    if not point_a or not point_b:
        return 10**9
    return ((point_a[0] - point_b[0]) ** 2 + (point_a[1] - point_b[1]) ** 2) ** 0.5


def _get_foreground_wrapper(backend, excluded_process_ids=None, excluded_titles=None):
    if Desktop is None:
        raise RuntimeError("缺少 pywinauto 依赖，请先安装 pywinauto。")
    excluded_process_ids = {str(item) for item in (excluded_process_ids or []) if str(item).strip()}
    excluded_titles = {str(item).strip() for item in (excluded_titles or []) if str(item).strip()}
    desktop = Desktop(backend=backend)

    hwnd = user32.GetForegroundWindow()
    if hwnd:
        candidate = desktop.window(handle=hwnd)
        title = str(_safe_get_value(lambda: candidate.window_text(), "")).strip()
        process_id = str(_safe_get_value(lambda: candidate.process_id(), "")).strip()
        if process_id not in excluded_process_ids and title not in excluded_titles:
            return candidate

    visible_windows = []
    for window in desktop.windows():
        title = str(_safe_get_value(lambda: window.window_text(), "")).strip()
        if not title:
            continue
        process_id = str(_safe_get_value(lambda: window.process_id(), "")).strip()
        if process_id in excluded_process_ids or title in excluded_titles:
            continue
        visible_windows.append(window)
    if not visible_windows:
        raise RuntimeError("当前没有可用的前台窗口。")
    return visible_windows[0]


def _find_window_by_keyword(keyword, backend, excluded_process_ids=None, excluded_titles=None):
    if Desktop is None:
        raise RuntimeError("缺少 pywinauto 依赖，请先安装 pywinauto。")
    keyword = str(keyword or "").strip().lower()
    if not keyword:
        raise RuntimeError('请先输入目标窗口关键字，或改用"当前前台窗口"模式。')
    excluded_process_ids = {str(item) for item in (excluded_process_ids or []) if str(item).strip()}
    excluded_titles = {str(item).strip() for item in (excluded_titles or []) if str(item).strip()}

    candidates = []
    for window in Desktop(backend=backend).windows():
        title = str(_safe_get_value(lambda: window.window_text(), "")).strip()
        if not title:
            continue
        process_id = str(_safe_get_value(lambda: window.process_id(), "")).strip()
        if process_id in excluded_process_ids or title in excluded_titles:
            continue
        lower_title = title.lower()
        if keyword not in lower_title:
            continue
        score = 0
        if lower_title == keyword:
            score += 100
        elif lower_title.startswith(keyword):
            score += 60
        score -= len(title)
        candidates.append((score, window))
    if not candidates:
        raise RuntimeError(f"未找到标题包含关键字的窗口：{keyword}")
    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def _build_display_name(parsed, fallback_prefix, index):
    for candidate in [
        parsed.get("name", ""),
        parsed.get("automationId", ""),
        parsed.get("className", ""),
        normalize_control_type_name(parsed.get("controlType", ""), parsed.get("localizedControlType", "")),
    ]:
        candidate = str(candidate or "").strip()
        if candidate and candidate != "[null]":
            return candidate
    return f"{fallback_prefix}_{index}"


def _split_identifier_tokens(text):
    text = str(text or "").strip()
    if not text:
        return []
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", " ", text)
    return [token for token in text.split() if token]


def _translate_control_type_label(control_type):
    mapping = {
        "button": "按钮",
        "edit": "输入框",
        "combobox": "下拉框",
        "menuitem": "菜单项",
        "tabitem": "页签",
        "treeitem": "树节点",
        "checkbox": "复选框",
        "radiobutton": "单选框",
        "text": "文本",
        "custom": "区域",
        "pane": "面板",
        "group": "分组",
        "window": "窗口",
        "image": "图像",
        "listitem": "列表项",
    }
    return mapping.get(str(control_type or "").strip().lower(), "")


def _build_readable_control_name(flat_control):
    if not isinstance(flat_control, dict):
        return "控件"
    # 与同行标签关联的下拉框常无有意义 name（如 PART_DropDownButton），用标签命名。
    related_label = str(flat_control.get("relatedLabelName", "")).strip()
    if related_label and _control_is_dropdown(flat_control):
        return f"{related_label} 下拉框"
    preferred_name = str(flat_control.get("name", "")).strip()
    if preferred_name and not re.fullmatch(r"[A-Za-z0-9_]+", preferred_name):
        return preferred_name
    raw_name = (
        preferred_name
        or str(flat_control.get("automationId", "")).strip()
        or str(flat_control.get("displayName", "")).strip()
        or str(flat_control.get("className", "")).strip()
        or ""
    )
    tokens = _split_identifier_tokens(raw_name)
    control_type = str(flat_control.get("controlType", "")).strip()
    ignore_tokens = {
        "mup",
        "view",
        "viewmodel",
        "model",
        "main",
        "control",
        "item",
        "panel",
        "pane",
        "window",
        "textblock",
    }
    type_tokens = {token.lower() for token in _split_identifier_tokens(control_type)}
    readable_tokens = []
    for token in tokens:
        lower_token = token.lower()
        if lower_token in ignore_tokens or lower_token in type_tokens:
            continue
        if len(token) <= 2 and token.isupper():
            continue
        readable_tokens.append(token)
    base_name = " ".join(readable_tokens).strip() or raw_name or "控件"
    type_label = _translate_control_type_label(control_type)
    if type_label and type_label not in base_name:
        return f"{base_name} {type_label}".strip()
    return base_name


_HELPTEXT_NOISE_EXACT = {"radcombobox", "radtabcontrol", "radtabitem"}


def _extract_functional_name(flat_control):
    """从软件暴露的 helpText 提炼控件真实操作语义名（如"添加新配置"）。
    图标按钮的 UIA Name 常是 SVG path（M19,13L13,13... 加号/铅笔/垃圾桶）无法阅读，
    而 helpText 是软件本地化资源里的真实功能说明，用作展示/识别层名字最可靠。
    只影响展示层，不改任何定位字段（automationId/name/targetValue）。"""
    if not isinstance(flat_control, dict):
        return ""
    help_text = str(flat_control.get("helpText", "") or "").strip()
    if not help_text:
        return ""
    lowered = help_text.lower()
    if lowered in _HELPTEXT_NOISE_EXACT or lowered.startswith("rad"):
        return ""
    # 排除 pid / 十六进制 / 路径等内部技术串，以及 SVG path 片段（M3,17.25...）
    if any(marker in help_text for marker in ("pid:", ":0x", ".dll", "\\")) or re.search(r"\bM\d{1,3},", help_text):
        return ""
    if len(help_text) > 30:
        help_text = help_text[:30]
    return help_text


def _display_control_name(item):
    """UI 展示名：优先功能名（helpText 提炼），回退原始控件名。
    采集 JSON 加载不经过 enrich 时 functionText 缺失，实时从 helpText 提炼兜底。"""
    if not isinstance(item, dict):
        return "控件"
    return (
        str(item.get("functionText", "")).strip()
        or _extract_functional_name(item)
        or str(item.get("savedControlName", "")).strip()
        or str(item.get("suggestedControlName", "")).strip()
        or str(item.get("displayName", "")).strip()
        or "控件"
    )


def _option_values_hint(item):
    """下拉框已采选项的树展示后缀，如「组·公共·私有」；无选项返回空串。"""
    if not isinstance(item, dict):
        return ""
    options = item.get("optionValues") or (item.get("inspectData") or {}).get("optionValues")
    if not options:
        return ""
    texts = [str(o).strip() for o in options if str(o).strip()]
    if not texts:
        return ""
    return f"「{'·'.join(texts)}」"


def _classify_control_quality(flat_control):
    if not isinstance(flat_control, dict):
        return "建议忽略", "无有效控件信息"
    # 已折叠进父级 TextBox 的 PART_ContentHost：不应作为独立控件推荐，
    # 否则它带着不唯一的 automation_id 定位入库，运行时误命中其它输入框宿主。
    if flat_control.get("foldedIntoParent"):
        return "建议忽略", "PART_ContentHost: WPF TextBox 内部编辑区域，已折叠进父级 TextBox，非独立可定位控件"
    # 下拉框显示值文本：已并入对应下拉框，保留但不单独入库。
    if flat_control.get("foldedIntoDropdown"):
        return "建议忽略", "下拉框当前显示值文本，已并入对应下拉框"
    # 下拉框可选项：属于某下拉框的候选值，未展开时通常无法完整采集。
    if flat_control.get("dropdownOption"):
        return "建议忽略", "下拉框可选项，属于对应下拉框的候选值"
    # 已与同行标签关联的下拉框/输入控件：是该标签对应的实际操作控件。
    if flat_control.get("regionRelated") and _control_is_assoc_actionable(flat_control):
        option_count = int(flat_control.get("optionCount", 0) or 0)
        if option_count:
            return "推荐保留", f"同行标签对应的实际操作控件（下拉框/输入框），已展开采集 {option_count} 个可选项"
        return "推荐保留", "同行标签对应的实际操作控件（下拉框/输入框）"
    # 已自动展开采集到可选项的下拉框：即使未关联标签也具备实际编排价值。
    if flat_control.get("optionValues") and _control_is_dropdown(flat_control):
        return "推荐保留", f"下拉框，已展开采集 {len(flat_control.get('optionValues'))} 个可选项"
    control_type = str(flat_control.get("controlType", "")).strip().lower()
    score = int(flat_control.get("locatorScore", 0) or 0)
    has_automation_id = bool(str(flat_control.get("automationId", "")).strip())
    has_name = bool(str(flat_control.get("name", "")).strip())
    has_label_text = bool(str(flat_control.get("labelText", "")).strip())
    depth = int(flat_control.get("depth", 0) or 0)
    child_count = len(((flat_control.get("inspectData", {}) or {}).get("children", []) or []))

    # 非动作控件黑名单：ScrollBar、Separator、Header 等
    # 注：thumb 已从黑名单移除——WPF Thumb 常用于滑块/拖拽控件，有采集价值
    non_actionable_types = {"scrollbar", "separator", "header", "titlebar", "statusbar", "menubar", "tooltip"}
    if control_type in non_actionable_types:
        return "建议忽略", f"{control_type} 不是动作控件，无需入库"

    # 顶层窗口
    if control_type in {"window"}:
        return "建议忽略", "顶层窗口通常不作为直接动作控件"
    if control_type in {"custom", "pane", "group"} and (child_count >= 3 or score < 80):
        return "容器控件", "更适合作为区域或父级上下文"
    # 推荐保留白名单：含 WPF TextBlock(text/textblock)、Hyperlink、Thumb 滑块等
    # text/textblock 须有标识（name/automationId/labelText），否则纯装饰文本会膨胀控件数量
    if control_type in {"textblock", "text"}:
        if has_name or has_automation_id or has_label_text:
            return "推荐保留", "有标识的文本控件"
        else:
            return "建议忽略", "纯装饰文本无标识，无自动化价值"
    if control_type in {"button", "edit", "combobox", "menuitem", "tabitem", "treeitem", "checkbox", "radiobutton", "listitem",
                        "thumb", "hyperlink"} and (score >= 80 or has_automation_id or has_name):
        return "推荐保留", "定位稳定且控件类型适合直接编排动作"
    if score >= 90 and (has_automation_id or has_name):
        return "推荐保留", "定位策略稳定"
    if depth <= 0 or score <= 45:
        return "建议忽略", "定位弱或层级过粗"
    if child_count >= 5:
        return "容器控件", "子控件较多，更像容器区域"
    return "建议忽略", "可作为备选，但不建议优先入库"


def _should_default_select_group(group):
    if not isinstance(group, dict):
        return False
    quality_tier = str(group.get("qualityTier", "")).strip()
    return quality_tier in {"推荐保留", "容器控件"}


def _annotate_input_drive_hint(item):
    """对不可聚焦的输入类宿主（WPF TextBox 内部宿主 PART_ContentHost 等）标注驱动方式。

    这类控件 IsKeyboardFocusable=false、通常无 Value/Text Pattern，UIA 输入
    （type_text 的 set_edit_text / type_keys）必然失败；正确驱动是
    "坐标点击聚焦 + 全局键盘(send_keys)"。提示追加到 qualityReason，
    入库后进入 notes 的"说明="，供流程设计时参考。
    """
    if not isinstance(item, dict):
        return
    keyboard_focusable = str(item.get("isKeyboardFocusable", "")).strip().lower()
    if keyboard_focusable != "false":
        return
    automation_id = str(item.get("automationId", "")).strip()
    control_type = str(item.get("controlType", "")).strip().lower()
    class_name = str(item.get("className", "")).strip().lower()
    input_class_names = {"textbox", "passwordbox", "scrollviewer"}
    is_input_like = (
        automation_id == "PART_ContentHost"
        or control_type in {"edit", "combobox", "spinner", "document", "textbox"}
        or (control_type in {"pane", "custom"} and class_name in input_class_names)
    )
    if not is_input_like:
        return
    raw_patterns = item.get("supportedPatterns") or []
    if isinstance(raw_patterns, str):
        raw_patterns = [raw_patterns]
    patterns = {str(p).strip().lower() for p in raw_patterns if str(p).strip()}
    has_value_or_text = any("value" in p or "text" in p for p in patterns)
    hint = "不可聚焦输入宿主(IsKeyboardFocusable=false)"
    if not has_value_or_text:
        hint += "，无 Value/Text Pattern，需坐标点击+键盘驱动(send_keys)，不可直接 UIA 输入"
    else:
        hint += "，需坐标点击聚焦后键盘驱动"
    quality_reason = str(item.get("qualityReason", "")).strip()
    if hint not in quality_reason:
        item["qualityReason"] = (quality_reason + " | " + hint).strip(" |")


def _enrich_flat_controls(flat_controls, target_window):
    target_window = target_window or {}
    for item in flat_controls or []:
        if not isinstance(item, dict):
            continue
        if not str(item.get("windowTitle", "")).strip():
            item["windowTitle"] = str(target_window.get("title", "")).strip()
        if not str(item.get("windowClassName", "")).strip():
            item["windowClassName"] = str(target_window.get("className", "")).strip()
        suggested_name = _build_readable_control_name(item)
        item["suggestedControlName"] = suggested_name
        item["functionText"] = _extract_functional_name(item)
        quality_tier, quality_reason = _classify_control_quality(item)
        item["qualityTier"] = quality_tier
        item["qualityReason"] = quality_reason
        risk_level, risk_reasons = assess_control_automatability(item)
        item["automatabilityRisk"] = risk_level
        item["automatabilityReasons"] = risk_reasons
        _annotate_input_drive_hint(item)
    return flat_controls


def _get_wrapper_handle(wrapper):
    return str(
        _safe_get_value(lambda: getattr(wrapper, "handle", ""), "")
        or _safe_get_value(lambda: getattr(wrapper.element_info, "handle", ""), "")
    ).strip()


def _get_wrapper_runtime_id(wrapper):
    return _format_runtime_id(_safe_get_value(lambda: getattr(wrapper.element_info, "runtime_id", ""), ""))


def _build_flat_control_identity(item):
    rect = _normalize_rect_dict(item.get("boundingBox"))
    rect_key = ""
    if rect:
        rect_key = f"{rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
    return "|".join(
        [
            str(item.get("runtimeId", "")).strip(),
            str(item.get("handle", "")).strip(),
            str(item.get("name", "")).strip(),
            str(item.get("className", "")).strip(),
            str(item.get("controlType", "")).strip(),
            rect_key,
        ]
    )


def _build_wrapper_identity(wrapper):
    rect = _rect_to_dict(_safe_get_value(lambda: wrapper.rectangle(), None))
    rect_key = ""
    if rect:
        rect_key = f"{rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
    return "|".join(
        [
            _get_wrapper_runtime_id(wrapper),
            _get_wrapper_handle(wrapper),
            str(_safe_get_value(lambda: wrapper.window_text(), "")).strip(),
            str(_safe_get_value(lambda: wrapper.class_name(), "")).strip(),
            normalize_control_type_name(
                _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
                _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
            ),
            rect_key,
        ]
    )


def _build_path_segments_from_wrapper(wrapper, root_handle=""):
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
        parsed = {
            "name": str(_safe_get_value(lambda: current.window_text(), "")).strip()
            or str(_safe_get_value(lambda: getattr(current.element_info, "name", ""), "")).strip(),
            "automationId": str(_safe_get_value(lambda: getattr(current.element_info, "automation_id", ""), "")).strip(),
            "className": str(_safe_get_value(lambda: current.class_name(), "")).strip(),
            "controlType": str(_safe_get_value(lambda: getattr(current.element_info, "control_type", ""), "")).strip(),
            "localizedControlType": str(
                _safe_get_value(lambda: getattr(current.element_info, "localized_control_type", ""), "")
            ).strip(),
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


# ── Pattern 检测：对齐 Inspect "Action" 菜单 ──────────────────────
# 通过 UIA 的 IsXxxPatternAvailable 布尔属性检测元素支持的 Control Patterns。
# 相比实例化 pywinauto 的 iface_* 接口，读取可用性属性的优势：
#   1. 只需一次 GetCurrentPropertyValue 跨进程调用，不实例化 Pattern 对象（零副作用，
#      绝不会误触发 Invoke/Toggle/Expand 等真实界面动作）；
#   2. 复用 pywinauto 的 pattern_ids，自动适配当前 OS 支持的全部 Pattern（含 32 种）；
#   3. 能检出 pywinauto 无 iface_ 封装的 Pattern（如 LegacyIAccessible）。
# 实测约 1.6ms/控件，整树 1100+ 控件累计约 1.8s，开销可接受。
_PATTERN_AVAILABILITY_MAP = None
_PATTERN_AVAILABILITY_READY = False


def _get_pattern_availability_map():
    """惰性构建 {pattern_name: availabilityPropertyId} 映射。

    依赖 COM（IUIA 单例），故延迟到首次调用时初始化，避免影响模块导入；
    构建失败（如非 Windows / 缺 pywinauto）时返回空 dict，检测降级为空列表。
    """
    global _PATTERN_AVAILABILITY_MAP, _PATTERN_AVAILABILITY_READY
    if _PATTERN_AVAILABILITY_READY:
        return _PATTERN_AVAILABILITY_MAP
    _PATTERN_AVAILABILITY_READY = True
    mapping = {}
    try:
        from pywinauto.uia_defines import IUIA, pattern_ids
        uia_dll = IUIA().UIA_dll
        for ptrn_name in pattern_ids.keys():
            # V2 结尾的 Pattern（如 TransformV2）对应属性名 UIA_IsTransformPattern2AvailablePropertyId
            if ptrn_name.endswith("V2"):
                prop_name = "UIA_Is" + ptrn_name[:-2] + "Pattern2AvailablePropertyId"
            else:
                prop_name = "UIA_Is" + ptrn_name + "PatternAvailablePropertyId"
            prop_id = getattr(uia_dll, prop_name, None)
            if prop_id is not None:
                mapping[ptrn_name] = prop_id
    except Exception:
        mapping = {}
    _PATTERN_AVAILABILITY_MAP = mapping
    return mapping


def _detect_supported_patterns(wrapper):
    """检测元素支持的 UIA Control Patterns（对齐 Inspect Action 菜单）。

    逐个读取 Pattern 的 IsXxxPatternAvailable 布尔属性，返回受支持的 Pattern
    名称列表，如 ["Invoke", "Value", "Scroll"]。仅对 uia backend 有效；
    win32 元素的 element_info 无 .element，捕获异常后返回空列表。
    """
    if wrapper is None:
        return []
    mapping = _get_pattern_availability_map()
    if not mapping:
        return []
    try:
        element = wrapper.element_info.element
    except Exception:
        return []
    if element is None:
        return []
    supported = []
    for pattern_name, prop_id in mapping.items():
        try:
            if element.GetCurrentPropertyValue(prop_id):
                supported.append(pattern_name)
        except Exception:
            pass
    return supported


def _detect_expand_collapse_state(wrapper, supported_patterns=None):
    """读取 ExpandCollapse 展开状态（ComboBox / Tree / 折叠面板）。

    若已知 supported_patterns 且其中不含 ExpandCollapse，则直接跳过，
    避免对不支持该 Pattern 的元素触发一次必然失败的接口实例化。
    """
    if wrapper is None:
        return ""
    if supported_patterns is not None and "ExpandCollapse" not in supported_patterns:
        return ""
    try:
        state = wrapper.get_expand_state()
        # 0=Collapsed, 1=Expanded, 2=PartiallyExpanded, 3=LeafNode
        state_names = {0: "Collapsed", 1: "Expanded", 2: "PartiallyExpanded", 3: "LeafNode"}
        return state_names.get(state, str(state))
    except Exception:
        return ""


# 需要探测 UIA LabeledBy 属性的控件类型（弱定位时读取，提升标签-输入对关联精度）
_LABELED_BY_PROBE_TYPES = {
    "edit", "combobox", "spinner", "document", "custom", "pane", "group",
    "button", "splitbutton", "image", "text", "listitem", "treeitem", "menuitem",
    "checkbox", "radiobutton", "slider",
}


def _read_labeled_by_info(wrapper):
    """读取 UIA LabeledBy 属性指向的标签元素（WPF Label.Target 权威关联）。

    返回 (标签 name, 标签 automationId)；不支持/读取失败时返回 ("", "")。
    pywinauto 未封装该属性，直接走 COM；每次约 1-2ms，仅对弱定位控件使用。
    """
    try:
        from pywinauto.uia_defines import IUIA
        element = _safe_get_value(lambda: wrapper.element_info.element, None)
        if element is None:
            return "", ""
        prop_id = getattr(IUIA().UIA_dll, "UIA_LabeledByPropertyId", 30018)
        labeled = element.GetCurrentPropertyValue(prop_id)
        if not labeled:
            return "", ""
        from pywinauto.uia_element_info import UIAElementInfo
        info = UIAElementInfo(labeled)
        name = str(_safe_get_value(lambda: getattr(info, "name", ""), "")).strip()
        automation_id = str(_safe_get_value(lambda: getattr(info, "automation_id", ""), "")).strip()
        return name, automation_id
    except Exception:
        return "", ""


# ── Inspect 字段补全：pywinauto UIAElementInfo 未暴露的属性，COM 直读 ─────
# pywinauto 的 UIAElementInfo 只暴露 name/class_name/control_type/handle 等少数字段，
# HelpText/AccessKey/ItemStatus/焦点状态/LegacyIAccessible 等 Inspect 可见信息必须
# 通过 IUIAutomationElement.GetCurrentPropertyValue / LegacyIAccessiblePattern 直读，
# 否则采集结果中这些字段恒为空串。
_UIA_PROP_IDS = {
    "localizedControlType": 30004,
    "acceleratorKey": 30006,
    "accessKey": 30007,
    "hasKeyboardFocus": 30008,
    "isKeyboardFocusable": 30009,
    "helpText": 30013,
    "isControlElement": 30016,
    "isContentElement": 30017,
    "isPassword": 30019,
    "itemType": 30021,
    "isOffscreen": 30022,
    "itemStatus": 30026,
}

# MSAA 角色码 → 中文可读名（对齐 Inspect 的本地化角色文本）
_MSAA_ROLE_TEXT = {
    0x01: "标题栏", 0x02: "菜单栏", 0x03: "滚动条", 0x04: "手柄", 0x05: "声音",
    0x06: "光标", 0x07: "插入符号", 0x08: "警告", 0x09: "窗口", 0x0A: "客户端",
    0x0B: "弹出菜单", 0x0C: "菜单项", 0x0D: "工具提示", 0x0E: "应用程序",
    0x0F: "文档", 0x10: "窗格", 0x11: "图表", 0x12: "对话框", 0x13: "边框",
    0x14: "分组", 0x15: "分隔符", 0x16: "工具栏", 0x17: "状态栏", 0x18: "表格",
    0x19: "列标题", 0x1A: "行标题", 0x1B: "列", 0x1C: "行", 0x1D: "单元格",
    0x1E: "链接", 0x1F: "帮助气球", 0x20: "字符", 0x21: "列表", 0x22: "列表项",
    0x23: "大纲", 0x24: "大纲项", 0x25: "选项卡页", 0x26: "属性页", 0x27: "指示器",
    0x28: "图形", 0x29: "静态文本", 0x2A: "可编辑文本", 0x2B: "按下按钮",
    0x2C: "复选按钮", 0x2D: "单选按钮", 0x2E: "组合框", 0x2F: "下拉列表",
    0x30: "进度条", 0x31: "刻度盘", 0x32: "热键字段", 0x33: "滑块",
    0x34: "数值调节钮", 0x35: "示意图", 0x36: "动画", 0x37: "公式",
    0x38: "下拉按钮", 0x39: "菜单按钮", 0x3A: "网格下拉按钮", 0x3B: "空白",
    0x3C: "选项卡列表", 0x3D: "时钟", 0x3E: "拆分按钮", 0x3F: "IP 地址",
    0x40: "大纲按钮",
}

# MSAA 状态位 → 中文可读名（按位或组合，对齐 Inspect 的状态文本）
_MSAA_STATE_BITS = (
    (0x00000001, "不可用"), (0x00000002, "已选择"), (0x00000004, "焦点"),
    (0x00000008, "按下"), (0x00000010, "已选中"), (0x00000020, "半选"),
    (0x00000040, "只读"), (0x00000080, "热跟踪"), (0x00000100, "默认"),
    (0x00000200, "已扩展"), (0x00000400, "已折叠"), (0x00000800, "忙"),
    (0x00001000, "浮动"), (0x00002000, "滚动字幕"), (0x00004000, "动画"),
    (0x00008000, "不可见"), (0x00010000, "离屏"), (0x00020000, "可调大小"),
    (0x00040000, "可移动"), (0x00080000, "自朗读"), (0x00100000, "可设定焦点"),
    (0x00200000, "可选择"), (0x00400000, "已链接"), (0x00800000, "已遍历"),
    (0x01000000, "可多选"), (0x02000000, "可扩展选择"), (0x04000000, "低警报"),
    (0x08000000, "中警报"), (0x10000000, "高警报"), (0x20000000, "受保护"),
)


def decode_msaa_role(role_value):
    """MSAA 角色码 → "按下按钮 (0x2B)" 风格文本（对齐 Inspect Role 行）。"""
    try:
        role_int = int(role_value)
    except (TypeError, ValueError):
        return ""
    if role_int <= 0:
        return ""
    label = _MSAA_ROLE_TEXT.get(role_int, "")
    return f"{label} (0x{role_int:X})" if label else f"(0x{role_int:X})"


def decode_msaa_state(state_value):
    """MSAA 状态位掩码 → "不可用 (0x1)" 风格文本（对齐 Inspect State 行）。"""
    try:
        state_int = int(state_value)
    except (TypeError, ValueError):
        return ""
    if state_int < 0:
        return ""
    labels = [label for bit, label in _MSAA_STATE_BITS if state_int & bit]
    text = ",".join(labels) if labels else "常规"
    return f"{text} (0x{state_int:X})"


def _read_uia_property(element, prop_id, default=""):
    """COM 直读 UIA 属性；不支持/失败/空值时返回 default。

    GetCurrentPropertyValue 对不受支持的属性返回 ReservedNotSupportedValue
    （IUnknown 指针），需按类型过滤，避免把指针对象写入 JSON。
    """
    if element is None or not prop_id:
        return default
    try:
        value = element.GetCurrentPropertyValue(int(prop_id))
    except Exception:
        return default
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value if value else default
    return default


def _read_legacy_accessible_info(wrapper, supported_patterns=None):
    """读取 LegacyIAccessible（MSAA）信息，对齐 Inspect 的 MSAA 面板。

    返回 dict：legacyChildId/legacyName/legacyValue/legacyDescription/legacyRole(+Text)/
    legacyState(+Text)/legacyHelp/legacyKeyboardShortcut/legacyDefaultAction。
    仅在元素支持 LegacyIAccessible Pattern 时读取（WPF/Win32 大多支持）；
    任何字段读取失败都降级为空，不影响主流程。
    """
    info = {
        "legacyChildId": "",
        "legacyName": "",
        "legacyValue": "",
        "legacyDescription": "",
        "legacyRole": "",
        "legacyRoleText": "",
        "legacyState": "",
        "legacyStateText": "",
        "legacyHelp": "",
        "legacyKeyboardShortcut": "",
        "legacyDefaultAction": "",
    }
    if wrapper is None:
        return info
    if supported_patterns is not None and "LegacyIAccessible" not in supported_patterns:
        return info
    try:
        from pywinauto.uia_defines import IUIA
        element = _safe_get_value(lambda: wrapper.element_info.element, None)
        if element is None:
            return info
        pattern = element.GetCurrentPattern(10018)  # UIA_LegacyIAccessiblePatternId
        if not pattern:
            return info
        legacy = pattern.QueryInterface(IUIA().ui_automation_client.IUIAutomationLegacyIAccessiblePattern)
    except Exception:
        return info

    def _text(attr):
        value = _safe_get_value(lambda: getattr(legacy, attr, ""), "")
        return str(value).strip() if isinstance(value, str) else ""

    def _int(attr):
        value = _safe_get_value(lambda: getattr(legacy, attr, None), None)
        if isinstance(value, bool):
            return ""
        try:
            return int(value)
        except (TypeError, ValueError):
            return ""

    child_id = _int("CurrentChildId")
    if child_id != "":
        info["legacyChildId"] = str(child_id)
    info["legacyName"] = _text("CurrentName")
    info["legacyValue"] = _text("CurrentValue")
    info["legacyDescription"] = _text("CurrentDescription")
    role = _int("CurrentRole")
    if role != "":
        info["legacyRole"] = str(role)
        info["legacyRoleText"] = decode_msaa_role(role)
    state = _int("CurrentState")
    if state != "":
        info["legacyState"] = str(state)
        info["legacyStateText"] = decode_msaa_state(state)
    info["legacyHelp"] = _text("CurrentHelp")
    info["legacyKeyboardShortcut"] = _text("CurrentKeyboardShortcut")
    info["legacyDefaultAction"] = _text("CurrentDefaultAction")
    return info


def _extract_wrapper_info(wrapper, depth, index, path_segments, target_window, lightweight=False):
    element_info = _safe_get_value(lambda: wrapper.element_info, None)
    if element_info is not None:
        _safe_get_value(lambda: element_info.set_cache_strategy("basic"), None)
    raw_element = _safe_get_value(lambda: getattr(element_info, "element", None), None)
    name = str(_safe_get_value(lambda: wrapper.window_text(), "")).strip()
    if element_info is not None and not name:
        name = str(_safe_get_value(lambda: getattr(element_info, "name", ""), "")).strip()
    class_name = str(_safe_get_value(lambda: wrapper.class_name(), "")).strip()
    control_type = normalize_control_type_name(
        _safe_get_value(lambda: getattr(element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(element_info, "localized_control_type", ""), ""),
    )
    # pywinauto 未暴露 localized_control_type 等属性（getattr 恒为空），全部 COM 直读补齐
    localized_control_type = str(_safe_get_value(lambda: getattr(element_info, "localized_control_type", ""), "")).strip()
    if not localized_control_type:
        localized_control_type = str(_read_uia_property(raw_element, _UIA_PROP_IDS["localizedControlType"], "")).strip()
    automation_id = str(_safe_get_value(lambda: getattr(element_info, "automation_id", ""), "")).strip()
    framework_id = str(_safe_get_value(lambda: getattr(element_info, "framework_id", ""), "")).strip()
    process_id = str(_safe_get_value(lambda: getattr(element_info, "process_id", ""), "")).strip()
    handle = str(_safe_get_value(lambda: getattr(element_info, "handle", ""), "")).strip()
    # HelpText 必须读 UIA_HelpTextPropertyId；旧实现误用 rich_text（TextPattern 文档文本）
    text_content = str(_safe_get_value(lambda: getattr(element_info, "rich_text", ""), "")).strip()
    if lightweight:
        # 悬停补采轻量级模式：跳过非核心字段，大幅减少 COM 调用
        help_text = ""
        provider_description = ""
        access_key = ""
        accelerator_key = ""
        item_type = ""
        item_status = ""
        is_content_element = ""
        is_control_element = ""
        is_password = ""
    else:
        help_text = str(_read_uia_property(raw_element, _UIA_PROP_IDS["helpText"], "")).strip()
        provider_description = str(_safe_get_value(lambda: getattr(element_info, "provider_description", ""), "")).strip()
        if not provider_description:
            provider_description = str(_read_uia_property(raw_element, 30107, "")).strip()
        # 对齐 Inspect 的补充属性：快捷键/项状态/视图归属/密码标记（流程编排与安全提示用）
        access_key = str(_read_uia_property(raw_element, _UIA_PROP_IDS["accessKey"], "")).strip()
        accelerator_key = str(_read_uia_property(raw_element, _UIA_PROP_IDS["acceleratorKey"], "")).strip()
        item_type = str(_read_uia_property(raw_element, _UIA_PROP_IDS["itemType"], "")).strip()
        item_status = str(_read_uia_property(raw_element, _UIA_PROP_IDS["itemStatus"], "")).strip()
        is_content_element = _read_uia_property(raw_element, _UIA_PROP_IDS["isContentElement"], "")
        is_control_element = _read_uia_property(raw_element, _UIA_PROP_IDS["isControlElement"], "")
        is_password = _read_uia_property(raw_element, _UIA_PROP_IDS["isPassword"], "")

    runtime_id = _format_runtime_id(_safe_get_value(lambda: getattr(element_info, "runtime_id", ""), ""))
    rect = _safe_get_value(lambda: wrapper.rectangle(), None)
    bounding_rectangle = _format_rectangle(rect)
    is_enabled = _safe_get_value(lambda: wrapper.is_enabled(), "")
    is_visible = _safe_get_value(lambda: wrapper.is_visible(), "")
    is_offscreen = _read_uia_property(raw_element, _UIA_PROP_IDS["isOffscreen"], "")
    if is_offscreen == "":
        is_offscreen = _safe_get_value(lambda: getattr(element_info, "offscreen", ""), "")
    if is_offscreen == "" and is_visible != "":
        is_offscreen = str(not bool(is_visible))
    keyboard_focusable = _read_uia_property(raw_element, _UIA_PROP_IDS["isKeyboardFocusable"], "")
    if keyboard_focusable == "":
        keyboard_focusable = _safe_get_value(lambda: getattr(element_info, "keyboard_focusable", ""), "")
    has_keyboard_focus = _read_uia_property(raw_element, _UIA_PROP_IDS["hasKeyboardFocus"], "")
    if has_keyboard_focus == "":
        has_keyboard_focus = _safe_get_value(lambda: getattr(element_info, "has_keyboard_focus", ""), "")
    # UIA LabeledBy（WPF Label.Target 权威标签关联）：仅弱定位控件读取，控制 COM 开销
    labeled_by_name = ""
    labeled_by_automation_id = ""
    if not lightweight and (not name or not automation_id) and control_type.strip().lower() in _LABELED_BY_PROBE_TYPES:
        labeled_by_name, labeled_by_automation_id = _read_labeled_by_info(wrapper)
    
    # 尝试获取 ValuePattern (对于没有 name 但有内容的文本框及其重要)
    value_pattern_value = ""
    if hasattr(wrapper, "get_value"):
        value_pattern_value = str(_safe_get_value(lambda: wrapper.get_value(), "")).strip()
        
    # 尝试获取 TogglePattern (对于复选框/单选框的状态采集)
    toggle_state = ""
    if hasattr(wrapper, "get_toggle_state"):
        toggle_state = str(_safe_get_value(lambda: wrapper.get_toggle_state(), "")).strip()
    
    if lightweight:
        # 悬停补采轻量级模式：跳过 Pattern 探测、LegacyIAccessible，大幅减少 COM 调用
        supported_patterns = []
        expand_collapse_state = ""
        legacy_info = {
            "legacyName": "", "legacyRole": "", "legacyState": "",
            "legacyRoleText": "", "legacyStateText": "", "legacyChildId": "",
            "legacyValue": "", "legacyDescription": "", "legacyHelp": "",
            "legacyKeyboardShortcut": "", "legacyDefaultAction": "",
        }
    else:
        # 检测支持的 Control Patterns（对齐 Inspect Action 菜单）
        supported_patterns = _detect_supported_patterns(wrapper)
        # 检测 ExpandCollapse 状态（ComboBox / Tree / 下拉框），复用已测 Pattern 跳过不支持项
        expand_collapse_state = _detect_expand_collapse_state(wrapper, supported_patterns)
    
        # LegacyIAccessible（MSAA）信息：Role/State/Help/DefAction 等，对齐 Inspect MSAA 面板；
        # 仅在支持 LegacyIAccessible Pattern 时读取（复用已测 Pattern 结果，零额外门槛调用）
        legacy_info = _read_legacy_accessible_info(wrapper, supported_patterns)
    legacy_name = legacy_info["legacyName"]
    legacy_role = legacy_info["legacyRole"]
    legacy_state = legacy_info["legacyState"]
    # HelpText 兜底：WPF 桥接的 MSAA Help 常比 UIA HelpText 更完整（如"删除"按钮）
    if not help_text:
        help_text = legacy_info["legacyHelp"]

    inspect_data = {
        "name": name,
        "value": value_pattern_value,
        "toggleState": toggle_state,
        "controlType": control_type,
        "localizedControlType": localized_control_type,
        "boundingRectangle": bounding_rectangle,
        "isEnabled": str(is_enabled),
        "isVisible": str(is_visible),
        "isOffscreen": str(is_offscreen),
        "isKeyboardFocusable": str(keyboard_focusable),
        "hasKeyboardFocus": str(has_keyboard_focus),
        "processId": process_id,
        "runtimeId": runtime_id,
        "frameworkId": framework_id,
        "className": class_name,
        "automationId": automation_id,
        "nativeWindowHandle": handle,
        "providerDescription": provider_description,
        "legacyName": legacy_name,
        "legacyRole": legacy_role,
        "legacyState": legacy_state,
        "legacyRoleText": legacy_info["legacyRoleText"],
        "legacyStateText": legacy_info["legacyStateText"],
        "legacyChildId": legacy_info["legacyChildId"],
        "legacyValue": legacy_info["legacyValue"],
        "legacyDescription": legacy_info["legacyDescription"],
        "legacyHelp": legacy_info["legacyHelp"],
        "legacyKeyboardShortcut": legacy_info["legacyKeyboardShortcut"],
        "legacyDefaultAction": legacy_info["legacyDefaultAction"],
        "helpText": help_text,
        "textContent": text_content,
        "accessKey": access_key,
        "acceleratorKey": accelerator_key,
        "itemType": item_type,
        "itemStatus": item_status,
        "isContentElement": str(is_content_element),
        "isControlElement": str(is_control_element),
        "isPassword": str(is_password),
        "labeledByName": labeled_by_name,
        "labeledByAutomationId": labeled_by_automation_id,
        "supportedPatterns": list(supported_patterns or []),
        "expandCollapseState": expand_collapse_state,
        "ancestors": list(path_segments[:-1]),
        "children": [],
    }
    full_ui_path = " > ".join(item for item in path_segments if item)
    locator_method, locator_value, locator_score, locator_reason = build_locator_recommendation(
        inspect_data, index=index, ui_path=full_ui_path)
    inspect_data["recommendedTargetMethod"] = locator_method
    inspect_data["recommendedTargetValue"] = locator_value

    display_name = _build_display_name(inspect_data, "控件", index)
    ui_path = " > ".join(item for item in path_segments if item)
    raw_inspect_text = build_synthetic_inspect_text(inspect_data)

    return {
        "depth": depth,
        "index": index,
        "displayName": display_name,
        "windowTitle": str(target_window.get("title", "")).strip(),
        "windowClassName": str(target_window.get("className", "")).strip(),
        "processId": process_id,
        "handle": handle,
        "name": name,
        "className": class_name,
        "controlType": control_type,
        "localizedControlType": localized_control_type,
        "automationId": automation_id,
        "frameworkId": framework_id,
        "runtimeId": runtime_id,
        "value": value_pattern_value,
        "toggleState": toggle_state,
        "supportedPatterns": supported_patterns,
        "expandCollapseState": expand_collapse_state,
        "boundingRectangle": bounding_rectangle,
        "boundingBox": _rect_to_dict(rect),
        "isEnabled": bool(is_enabled) if str(is_enabled) != "" else None,
        "isVisible": bool(is_visible) if str(is_visible) != "" else None,
        "isOffscreen": str(is_offscreen),
        "isKeyboardFocusable": str(keyboard_focusable),
        "hasKeyboardFocus": str(has_keyboard_focus),
        "helpText": help_text,
        "textContent": text_content,
        "accessKey": access_key,
        "acceleratorKey": accelerator_key,
        "itemType": item_type,
        "itemStatus": item_status,
        "isContentElement": str(is_content_element),
        "isControlElement": str(is_control_element),
        "isPassword": str(is_password),
        "legacyName": legacy_name,
        "legacyRole": legacy_role,
        "legacyState": legacy_state,
        "legacyRoleText": legacy_info["legacyRoleText"],
        "legacyStateText": legacy_info["legacyStateText"],
        "legacyHelp": legacy_info["legacyHelp"],
        "legacyKeyboardShortcut": legacy_info["legacyKeyboardShortcut"],
        "legacyDefaultAction": legacy_info["legacyDefaultAction"],
        "labeledByName": labeled_by_name,
        "labeledByAutomationId": labeled_by_automation_id,
        "providerDescription": provider_description,
        "locatorScore": locator_score,
        "locatorReason": locator_reason,
        "recommendedTargetMethod": locator_method,
        "recommendedTargetValue": locator_value,
        "uiPath": ui_path,
        "parentPath": " > ".join(item for item in path_segments[:-1] if item),
        "auxChecks": build_aux_checks(inspect_data),
        "inspectData": inspect_data,
        "rawInspectText": raw_inspect_text,
    }


def _append_wrapper_info(flat_controls, seen_identities, wrapper, target_window, root_handle="", max_depth=DEFAULT_MAX_DEPTH):
    if wrapper is None:
        return False
    identity = _build_wrapper_identity(wrapper)
    if not identity or identity in seen_identities:
        return False
    path_segments = _build_path_segments_from_wrapper(wrapper, root_handle=root_handle)
    if not path_segments:
        return False
    depth = max(0, len(path_segments) - 1)
    if depth > max_depth:
        return False
    info = _extract_wrapper_info(wrapper, depth, len(flat_controls) + 1, path_segments, target_window)
    info["_wrapperIdentity"] = identity
    info["_wrapperRef"] = wrapper
    flat_controls.append(info)
    seen_identities.add(_build_flat_control_identity(info))
    seen_identities.add(identity)
    return True


def _walk_wrapper(
    wrapper,
    depth,
    max_depth,
    target_window,
    flat_controls,
    siblings_index=1,
    path_segments=None,
    parent_index=-1,
    start_time=None,
    scan_timeout_seconds=30,
    status_callback=None,
    cancel_event=None,
):
    if start_time is None:
        start_time = time.time()

    if (cancel_event and cancel_event.is_set()) or time.time() - start_time > scan_timeout_seconds:
        if status_callback:
            status_callback("扫描已中止或超时，已停止遍历。", len(flat_controls))
        return None

    path_segments = list(path_segments or [])
    info = _extract_wrapper_info(wrapper, depth, len(flat_controls) + 1, path_segments, target_window)
    info["treeLevel"] = depth
    info["parentIndex"] = parent_index
    info["siblingsIndex"] = siblings_index
    current_index = len(flat_controls)
    info["_wrapperIdentity"] = _build_wrapper_identity(wrapper)
    node = dict(info)
    node["children"] = []
    # _wrapperRef 仅挂在扁平控件上（node 已拷贝完成），供后续自动展开下拉框采选项使用；
    # 序列化前会被剥离，不会进入 controlsTree/输出。
    info["_wrapperRef"] = wrapper
    flat_controls.append(info)

    if status_callback and len(flat_controls) % 20 == 0:
        status_callback(f"已收集 {len(flat_controls)} 个控件...", len(flat_controls))

    if depth >= max_depth:
        return node

    children = _safe_get_value(lambda: wrapper.children(), [])
    for child_index, child in enumerate(children, start=1):
        if (cancel_event and cancel_event.is_set()) or time.time() - start_time > scan_timeout_seconds:
            if status_callback:
                status_callback("扫描已中止或超时，已停止遍历。", len(flat_controls))
            break

        child_display_name = _build_display_name(
            {
                "name": str(_safe_get_value(lambda: child.window_text(), "")).strip(),
                "automationId": str(_safe_get_value(lambda: getattr(child.element_info, "automation_id", ""), "")).strip(),
                "className": str(_safe_get_value(lambda: child.class_name(), "")).strip(),
                "controlType": str(_safe_get_value(lambda: getattr(child.element_info, "control_type", ""), "")).strip(),
                "localizedControlType": str(_safe_get_value(lambda: getattr(child.element_info, "localized_control_type", ""), "")).strip(),
            },
            "控件",
            child_index,
        )
        child_path = path_segments + [child_display_name]
        child_node = _walk_wrapper(
            child,
            depth + 1,
            max_depth,
            target_window,
            flat_controls,
            siblings_index=child_index,
            path_segments=child_path,
            parent_index=current_index,
            start_time=start_time,
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
            cancel_event=cancel_event,
        )
        if child_node is not None:
            node["children"].append(child_node)
    node["inspectData"]["children"] = [
        f"{child.get('displayName', '')} | {child.get('className', '')} | {child.get('controlType', '')}".strip(" |")
        for child in node["children"][:12]
    ]
    return node


# ---------------------------------------------------------------------------
# RawViewWalker BFS — Inspect 风格渐进式完整树遍历
# ---------------------------------------------------------------------------

_MAX_ELEMENTS_PER_WALK = 8000


def _rebuild_bfs_paths(flat_controls):
    """用 parentIndex 链为 BFS 采集结果重建 uiPath / parentPath / ancestors。"""
    for item in (flat_controls or []):
        display_name = item.get("displayName", "")
        path_parts = [display_name]
        parent_idx = item.get("parentIndex", -1)
        visited = set()
        while parent_idx >= 0 and parent_idx < len(flat_controls) and parent_idx not in visited:
            visited.add(parent_idx)
            parent = flat_controls[parent_idx]
            parent_name = parent.get("displayName", "")
            if parent_name:
                path_parts.insert(0, parent_name)
            parent_idx = parent.get("parentIndex", -1)
        ui_path = " > ".join(p for p in path_parts if p)
        parent_path = " > ".join(path_parts[:-1]) if len(path_parts) > 1 else ""
        item["uiPath"] = ui_path
        item["parentPath"] = parent_path
        inspect = item.get("inspectData")
        if isinstance(inspect, dict):
            inspect["ancestors"] = path_parts[:-1]


def _enrich_tree_metadata(flat_controls):
    """为 BFS 采集结果添加树结构辅助元数据（不删除任何控件）。

    添加字段：
     - pathHash: 基于 uiPath 的短哈希，用于快速定位和去重
     - isTransparentContainer: 标记"透明容器"（无名、单子节点），GUI 可折叠显示
     - childCount: 直接子节点数量
    """
    import hashlib
    if not flat_controls:
        return
    # 先统计每个节点的子节点数量
    child_counts = [0] * len(flat_controls)
    for idx, item in enumerate(flat_controls):
        parent_idx = item.get("parentIndex", -1)
        if 0 <= parent_idx < len(flat_controls):
            child_counts[parent_idx] += 1
    # 为每个节点计算 pathHash 和透明容器标记
    for idx, item in enumerate(flat_controls):
        ui_path = item.get("uiPath", "")
        # pathHash: 取 uiPath 的 MD5 前 8 位
        path_hash = hashlib.md5(ui_path.encode("utf-8", errors="replace")).hexdigest()[:8]
        item["pathHash"] = path_hash
        item["childCount"] = child_counts[idx]
        # 透明容器判断：无名 + 单子节点 + 容器类型
        name = str(item.get("name", "")).strip()
        control_type = str(item.get("controlType", "")).strip().lower()
        is_container = control_type in ("pane", "group", "custom", "window")
        is_single_child = child_counts[idx] == 1
        is_transparent = is_container and not name and is_single_child
        item["isTransparentContainer"] = is_transparent


def _build_control_summary(filtered_controls, all_controls):
    """生成控件摘要（快速概览），不修改原始数据。

    返回包含以下信息的 dict：
     - 定位能力统计：有 automationId / 有 name / 两者都有
     - 深度分布
     - 质量分级分布
     - 透明容器数量
    """
    from collections import Counter
    if not filtered_controls:
        return {"totalFiltered": 0, "totalRaw": len(all_controls or [])}

    has_aid = sum(1 for c in filtered_controls if str(c.get("automationId", "")).strip())
    has_name = sum(1 for c in filtered_controls if str(c.get("name", "")).strip())
    has_both = sum(1 for c in filtered_controls if str(c.get("automationId", "")).strip() and str(c.get("name", "")).strip())

    depth_dist = Counter(c.get("treeLevel", 0) for c in filtered_controls)
    quality_dist = Counter(str(c.get("qualityTier", "")).strip() or "未分类" for c in filtered_controls)
    transparent_count = sum(1 for c in filtered_controls if c.get("isTransparentContainer"))

    return {
        "totalFiltered": len(filtered_controls),
        "totalRaw": len(all_controls or []),
        "locatability": {
            "hasAutomationId": has_aid,
            "hasName": has_name,
            "hasBoth": has_both,
            "hasNeither": len(filtered_controls) - has_aid - has_name + has_both,
        },
        "depthDistribution": dict(sorted(depth_dist.items())),
        "qualityDistribution": dict(sorted(quality_dist.items(), key=lambda x: -x[1])),
        "transparentContainers": transparent_count,
    }


def _build_tree_from_flat(flat_controls):
    """从 flat_controls（含 parentIndex）重建嵌套控件树。

    每个节点额外设置：
     - flatIndex: 原始 flat_controls 中的下标，用于 GUI 反查控件详情
    """
    if not flat_controls:
        return {}
    nodes = []
    for idx, item in enumerate(flat_controls):
        node = dict(item)
        # 剥离临时 live 包装器引用，避免序列化失败（与主采集流程保持一致）
        node.pop("_wrapperRef", None)
        node.pop("_wrapperIdentity", None)
        node.setdefault("children", [])
        node["flatIndex"] = idx
        nodes.append(node)
    roots = []
    for idx, node in enumerate(nodes):
        parent_idx = node.get("parentIndex", -1)
        if parent_idx >= 0 and parent_idx < len(nodes) and parent_idx != idx:
            nodes[parent_idx].setdefault("children", []).append(node)
        else:
            roots.append(node)
    for node in nodes:
        inspect = node.get("inspectData")
        if isinstance(inspect, dict):
            inspect["children"] = [
                f"{c.get('displayName', '')} | {c.get('className', '')} | {c.get('controlType', '')}".strip(" |")
                for c in node.get("children", [])[:12]
            ]
    return roots[0] if roots else {}


def _walk_raw_view_bfs(
    target_window_wrapper,
    max_depth,
    target_window,
    flat_controls,
    path_segments=None,
    start_time=None,
    scan_timeout_seconds=30,
    status_callback=None,
    lightweight=False,
    cancel_event=None,
):
    """使用 RawViewWalker 进行 BFS 树遍历，不丢失 IsContentElement=False 的控件。

    核心改进（对比 _walk_wrapper）：
     1. IUIAutomation::RawViewWalker → 不过滤 IsContentElement / IsControlElement
     2. 迭代 BFS → 可控内存、可暂停
     3. 输出 flat_controls 格式与 _walk_wrapper 完全兼容

    返回 stats dict：{"timedOut": bool, "hitLimit": bool}，标记采集是否被截断（调用方应上报，
    静默截断 = 静默漏采）。
    """
    from pywinauto.uia_defines import IUIA
    from pywinauto.controls.uiawrapper import UIAWrapper
    from pywinauto.uia_element_info import UIAElementInfo

    if start_time is None:
        start_time = time.time()

    path_segments = list(path_segments or [])
    root_display = path_segments[0] if path_segments else _build_display_name(target_window, "窗口", 1)

    # 采集完整性统计：超时/元素熔断导致的截断需显式上报（静默截断 = 静默漏采）
    stats = {"timedOut": False, "hitLimit": False}

    iuia = IUIA().iuia
    raw_walker = iuia.RawViewWalker

    # ---- 根节点 ----
    root_info = _extract_wrapper_info(target_window_wrapper, 0, 1, path_segments, target_window, lightweight=lightweight)
    root_info["treeLevel"] = 0
    root_info["parentIndex"] = -1
    root_info["siblingsIndex"] = 1
    root_info["_wrapperIdentity"] = _build_wrapper_identity(target_window_wrapper)
    root_info["_wrapperRef"] = target_window_wrapper
    flat_controls.append(root_info)

    if status_callback:
        status_callback(f"已发现 {len(flat_controls)} 个控件 (depth 0)...", len(flat_controls))

    if max_depth <= 0:
        _rebuild_bfs_paths(flat_controls)
        return stats

    # ---- BFS 队列 ----
    # 每个队列项: (element, depth, parent_index, siblings_index)
    queue = collections.deque()

    root_element = target_window_wrapper.element_info.element
    child_sib = 0
    try:
        child = raw_walker.GetFirstChildElement(root_element)
        while child:
            child_sib += 1
            queue.append((child, 1, 0, child_sib))
            child = raw_walker.GetNextSiblingElement(child)
    except Exception:
        pass

    if status_callback:
        status_callback(f"BFS 根节点子元素入队: {len(queue)} 个", len(flat_controls))

    last_report = 0

    while queue:
        if (cancel_event and cancel_event.is_set()) or time.time() - start_time > scan_timeout_seconds:
            if status_callback:
                status_callback("扫描已中止或超时，已停止遍历。", len(flat_controls))
            if cancel_event and cancel_event.is_set():
                stats["cancelled"] = True
            stats["timedOut"] = True
            break

        if len(flat_controls) >= _MAX_ELEMENTS_PER_WALK:
            if status_callback:
                status_callback(f"已达上限 {_MAX_ELEMENTS_PER_WALK} 个控件，暂停。", len(flat_controls))
            stats["hitLimit"] = True
            break

        element, depth, parent_idx, sib_idx = queue.popleft()

        if depth > max_depth:
            continue

        # 将原始 UI Automation 元素包装为 pywinauto wrapper
        try:
            elem_info = UIAElementInfo(element)
            wrapper = UIAWrapper(elem_info)
        except Exception:
            continue

        # 提取控件信息（与 _walk_wrapper 相同格式）
        identity = _build_wrapper_identity(wrapper)
        # path_segments 在 BFS 中先填占位值，后续由 _rebuild_bfs_paths 修正
        info = _extract_wrapper_info(
            wrapper, depth, len(flat_controls) + 1, [root_display], target_window, lightweight=lightweight
        )
        info["treeLevel"] = depth
        info["parentIndex"] = parent_idx
        info["siblingsIndex"] = sib_idx
        info["_wrapperIdentity"] = identity
        info["_wrapperRef"] = wrapper
        current_index = len(flat_controls)
        flat_controls.append(info)

        # P1: COM 节流——每处理 20 个元素短暂 sleep，让目标进程有机会处理自身消息
        if lightweight and len(flat_controls) % 20 == 0:
            time.sleep(0.002)

        if status_callback and current_index - last_report >= 50:
            last_report = current_index
            status_callback(
                f"已发现 {len(flat_controls)} 个控件 (depth {depth}, 队列 {len(queue)})...",
                len(flat_controls),
            )

        if len(flat_controls) % 100 == 0:
            _bfs_elapsed = (time.time() - start_time) * 1000
            _hover_log(f"bfs progress, count={len(flat_controls)}, elapsed={_bfs_elapsed:.1f}ms")

        if depth >= max_depth:
            continue

        # 通过 RawViewWalker 枚举子元素
        child_sib = 0
        try:
            child = raw_walker.GetFirstChildElement(element)
            while child:
                if (cancel_event and cancel_event.is_set()) or time.time() - start_time > scan_timeout_seconds:
                    if cancel_event and cancel_event.is_set():
                        stats["cancelled"] = True
                    stats["timedOut"] = True
                    break
                child_sib += 1
                queue.append((child, depth + 1, current_index, child_sib))
                child = raw_walker.GetNextSiblingElement(child)
        except Exception:
            pass

    # ---- 后处理 ----
    _rebuild_bfs_paths(flat_controls)

    if status_callback:
        status_callback(
            f"BFS 遍历完成: {len(flat_controls)} 个控件, 最大深度 {max_depth}",
            len(flat_controls),
        )

    return stats


# ---------------------------------------------------------------------------
# Inspect 式定点补采 — 人工定位 → 实时读子树 → 合并回主控件树
# ---------------------------------------------------------------------------
# 整树 BFS 只能采到扫描瞬间已实体化的元素；WPF 虚拟化/懒加载子控件要等鼠标悬停、
# 面板展开后才出现。补采流程对齐 Inspect：先采完整树骨架，再由人工把鼠标指到
# 目标控件（或在层级树中选中已采控件），实时 BFS 该控件子树并合并回现有 payload。

# 定点补采的子树相对深度上限（相对被指控件，而非窗口根）
SUPPLEMENT_SUBTREE_MAX_DEPTH = FULLTREE_MIN_DEPTH
SUPPLEMENT_SCAN_TIMEOUT_SECONDS = 45

# 连续悬停补采（Inspect 式跟踪）：深度降到 6 层控制单次补采耗时（悬停补采是
# 高频增量操作，漏采的深层可再悬停子节点继续补；定点补采仍用大深度）
HOVER_SUPPLEMENT_MAX_DEPTH = 6
HOVER_SUPPLEMENT_TIMEOUT_SECONDS = 8
# 悬停轮询周期（毫秒）：查看层每 tick 都跟随，150ms 让红框/树聚焦近乎跟手；
# 采集层另由停顿门控约束（4 tick ≈ 0.6s），重操作触发频率与改前相当
HOVER_TICK_MS = 150
HOVER_STABLE_TICKS = 4
# 位置级防重复：探测线程被 MUP 阻塞、无新鲜 key 时，距上次补采点过近则跳过，
# 避免同一位置反复入队；worker 后台采集自会按 identity 去重合并（冗余无副作用）
HOVER_REPEAT_DRIFT_PX = 8


def _prefix_subtree_paths(sub_flats, ancestor_segments, base_depth=0):
    """为子树 BFS 结果补全绝对路径与深度。

    _walk_raw_view_bfs 以子树根为起点，uiPath/depth 都是相对值；合并回主树前
    需要用子树根在窗口内的祖先链前缀补全为绝对 uiPath，并偏移 depth/treeLevel。
    """
    prefix_parts = [seg for seg in (ancestor_segments or []) if seg]
    prefix = " > ".join(prefix_parts)
    for item in sub_flats or []:
        if prefix:
            ui_path = str(item.get("uiPath", "")).strip()
            item["uiPath"] = f"{prefix} > {ui_path}" if ui_path else prefix
            parent_path = str(item.get("parentPath", "")).strip()
            item["parentPath"] = f"{prefix} > {parent_path}" if parent_path else prefix
            inspect = item.get("inspectData")
            if isinstance(inspect, dict):
                inspect["ancestors"] = prefix_parts + list(inspect.get("ancestors") or [])
        if base_depth:
            item["depth"] = int(item.get("depth", 0) or 0) + base_depth
            item["treeLevel"] = int(item.get("treeLevel", 0) or 0) + base_depth


def _climb_to_expected_wrapper(wrapper, expected, max_up=12):
    """从命中元素沿祖先链上溯，找到与期望控件最匹配的元素。

    from_point 命中的往往是期望控件内部的深层叶子（TextBlock 等），需按
    runtimeId（最强）→ 矩形完全一致 → name+className+controlType 签名逐级回溯。
    找不到时返回原命中元素兜底。
    """
    if not isinstance(expected, dict):
        return wrapper
    exp_runtime = str(expected.get("runtimeId", "")).strip()
    exp_rect = _normalize_rect_dict(expected.get("boundingBox"))
    exp_sig = (
        str(expected.get("name", "")).strip(),
        str(expected.get("className", "")).strip(),
        str(expected.get("controlType", "")).strip(),
    )
    has_sig = any(exp_sig)
    current = wrapper
    for _ in range(max_up):
        if current is None:
            break
        if exp_runtime and _get_wrapper_runtime_id(current) == exp_runtime:
            return current
        rect = _rect_to_dict(_safe_get_value(lambda: current.rectangle(), None))
        if exp_rect and rect and _normalize_rect_dict(rect) == exp_rect:
            return current
        if has_sig:
            sig = (
                str(_safe_get_value(lambda: current.window_text(), "")).strip(),
                str(_safe_get_value(lambda: current.class_name(), "")).strip(),
                normalize_control_type_name(
                    _safe_get_value(lambda: getattr(current.element_info, "control_type", ""), ""),
                    _safe_get_value(lambda: getattr(current.element_info, "localized_control_type", ""), ""),
                ),
            )
            if sig == exp_sig:
                return current
        parent = _safe_get_value(lambda: current.parent(), None)
        if not parent or parent is current:
            break
        current = parent
    return wrapper


def collect_subtree_at_point(
    x,
    y,
    climb_levels=0,
    expected=None,
    max_depth=SUPPLEMENT_SUBTREE_MAX_DEPTH,
    scan_timeout_seconds=SUPPLEMENT_SCAN_TIMEOUT_SECONDS,
    excluded_process_ids=None,
    allowed_process_ids=None,
    status_callback=None,
):
    """Inspect 式定点补采：从屏幕坐标命中元素，实时 BFS 采集其子树。

    参数：
     - climb_levels: 命中后沿祖先链上溯的层数（人工指到叶子时可扩大补采范围），
       不越过顶层窗口；
     - expected: 期望控件的 identity 字段（补采已选中控件时用于锚定，见
       _climb_to_expected_wrapper），提供时忽略 climb_levels。

    返回 (sub_flats, target_window, error_message)：
     - sub_flats: 与主采集完全兼容的 flat_control 列表（已补全绝对路径、
       已做树元数据增强、已剥离 wrapper 引用）；
     - target_window: 子树所在顶层窗口信息；
     - error_message: 失败原因，成功时为空串。
    """
    _hover_log(f"collect_subtree start, lightweight=True")
    if Desktop is None:
        return [], {}, "pywinauto 不可用，无法定点补采。"
    desktop = Desktop(backend="uia")
    wrapper = _safe_get_value(lambda: desktop.from_point(int(x), int(y)), None)
    if wrapper is None:
        return [], {}, f"坐标 ({x},{y}) 未命中任何 UIA 元素。"
    hit_pid = str(_safe_get_value(lambda: wrapper.element_info.process_id, "")).strip()
    excluded = {str(pid).strip() for pid in (excluded_process_ids or []) if str(pid).strip()}
    if hit_pid and hit_pid in excluded:
        return [], {}, "命中了采集工具自身窗口，请把鼠标悬停在目标软件的控件上。"
    allowed = {str(pid).strip() for pid in (allowed_process_ids or []) if str(pid).strip()}
    if allowed and hit_pid and hit_pid not in allowed:
        return [], {}, "命中元素不属于目标软件进程，已跳过。"

    top_wrapper = _safe_get_value(lambda: wrapper.top_level_parent(), None) or wrapper
    top_handle = _get_wrapper_handle(top_wrapper)

    root_wrapper = wrapper
    if isinstance(expected, dict) and expected:
        root_wrapper = _climb_to_expected_wrapper(wrapper, expected)
    else:
        for _ in range(max(0, int(climb_levels or 0))):
            current_handle = _get_wrapper_handle(root_wrapper)
            if top_handle and current_handle and str(current_handle) == str(top_handle):
                break
            parent = _safe_get_value(lambda: root_wrapper.parent(), None)
            if not parent or parent is root_wrapper:
                break
            root_wrapper = parent

    target_window = _build_target_window_info(top_wrapper)
    ancestor_segments = _build_path_segments_from_wrapper(
        root_wrapper, root_handle=str(target_window.get("handle", "")).strip()
    )
    if not ancestor_segments:
        ancestor_segments = [_build_display_name({"name": "", "controlType": ""}, "控件", 1)]

    sub_flats = []
    if status_callback:
        status_callback("定点补采：开始实时遍历子树...", 0)
    try:
        _walk_raw_view_bfs(
            root_wrapper,
            max_depth=max_depth,
            target_window=target_window,
            flat_controls=sub_flats,
            path_segments=[ancestor_segments[-1]],
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
            lightweight=True,
        )
    except Exception as exc:
        return [], target_window, f"子树遍历失败：{exc}"

    _prefix_subtree_paths(sub_flats, ancestor_segments[:-1], base_depth=max(0, len(ancestor_segments) - 1))
    # 子树自身的 pathHash / childCount / isTransparentContainer（parentIndex 仍为局部索引）
    _enrich_tree_metadata(sub_flats)
    for item in sub_flats:
        item["scanBackend"] = "uia"
        item.pop("_wrapperRef", None)
        item.pop("_wrapperIdentity", None)
    return sub_flats, target_window, ""


def _nodes_identity_match(a, b):
    """判断两个节点是否身份匹配（签名一致或 runtimeId 一致）。"""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return a is b
    a_runtime, a_sig, a_ok = _extract_identity_match_keys(a)
    b_runtime, b_sig, b_ok = _extract_identity_match_keys(b)
    if a_runtime and b_runtime and a_runtime == b_runtime:
        return True
    if a_ok and b_ok and a_sig == b_sig:
        return True
    return False


def _extract_identity_match_keys(item):
    """提取 identity 匹配键：(runtimeId, 多字段签名, 签名是否可用)。

    签名基于稳定标识字段（name / className / controlType），
    不包含 boundingRectangle（屏幕绝对坐标，窗口移动后不可靠）。
    """
    sig = (
        str(item.get("name", "")).strip(),
        str(item.get("className", "")).strip(),
        str(item.get("controlType", "")).strip(),
    )
    sig_usable = bool(sig[0] or sig[1] or sig[2])
    return str(item.get("runtimeId", "")).strip(), sig, sig_usable


def _find_tree_node_by_identity(tree_root, target_item):
    """在 controlsTree 中按 runtimeId（优先）或多字段签名递归查找锚点节点。"""
    if not isinstance(tree_root, dict) or not isinstance(target_item, dict):
        return None
    target_runtime, target_sig, sig_usable = _extract_identity_match_keys(target_item)
    stack = [tree_root]
    sig_match = None
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        node_runtime = str(node.get("runtimeId", "")).strip()
        if target_runtime and node_runtime and node_runtime == target_runtime:
            return node
        if sig_usable and sig_match is None:
            _, node_sig, _ = _extract_identity_match_keys(node)
            if node_sig == target_sig:
                sig_match = node
        stack.extend(node.get("children", []) or [])
    return sig_match


# 树合并时旧节点向新节点补空继承的字段白名单（含用户在 GUI 侧命名/质量分级成果）
_TREE_MERGE_INHERIT_KEYS = (
    "savedControlName",
    "savedControlId",
    "suggestedControlName",
    "displayName",
    "recommendedTargetMethod",
    "recommendedTargetValue",
    "locatorScore",
    "qualityTier",
    "qualityReason",
    "supportedPatterns",
    "expandCollapseState",
    "value",
    "toggleState",
    "pathHash",
    "scanBackend",
)


def _merge_tree_children_preserving(old_children, new_children):
    """递归合并锚点子树：以实时采集的新子树为基准，保留旧子树中未匹配节点。

    补采（尤其悬停模式）深度受限，若整体替换 children 会把旧扫描中更深的分支
    从树上砍掉（flatControls 条目还在但层级丢失），违反零丢弃原则；此处按
    identity（runtimeId 优先、多字段签名兜底）匹配并递归合并：
     - 匹配到的节点：新节点保留实时值，旧节点的白名单字段补空继承，children 递归合并；
     - 未匹配的旧节点：原样保留（可能是更深层分支或暂时离屏的虚拟化项）。
    """
    old_children = [child for child in (old_children or []) if isinstance(child, dict)]
    new_children = [child for child in (new_children or []) if isinstance(child, dict)]
    if not old_children:
        return new_children
    if not new_children:
        return old_children
    remaining_old = list(old_children)
    merged = []
    for new_child in new_children:
        runtime, sig, sig_usable = _extract_identity_match_keys(new_child)
        match_index = -1
        if runtime:
            for i, old_child in enumerate(remaining_old):
                if str(old_child.get("runtimeId", "")).strip() == runtime:
                    match_index = i
                    break
        if match_index < 0 and sig_usable:
            for i, old_child in enumerate(remaining_old):
                _, old_sig, _ = _extract_identity_match_keys(old_child)
                if old_sig == sig:
                    match_index = i
                    break
        if match_index >= 0:
            old_child = remaining_old.pop(match_index)
            for key in _TREE_MERGE_INHERIT_KEYS:
                if not new_child.get(key) and old_child.get(key) not in (None, "", [], {}):
                    new_child[key] = old_child.get(key)
            new_child["children"] = _merge_tree_children_preserving(
                old_child.get("children", []), new_child.get("children", [])
            )
            new_child["childCount"] = len(new_child["children"])
        merged.append(new_child)
    merged.extend(remaining_old)
    return merged


def merge_supplement_into_payload(payload, sub_flats, target_window=None, status_callback=None):
    """将定点补采的子树 flat 列表就地合并进现有 payload。

    合并策略（遵循"只增强、零丢弃"原则）：
     1. flatControls 按 identity 去重后追加新控件（追加式合并，既有条目的下标
        不变，GUI 勾选状态保持有效）；已存在条目只补空缺字段，不覆盖；
     2. controlsTree 按子树根 identity 定位锚点，其 children 与实时子树递归合并
        （新子树为基准、旧的更深层分支保留，防止浅层补采砍树；找不到锚点时挂到树根下）；
     3. controlDefinitions 为新控件追加定义，保持与 flatControls 1:1 对应；
     4. scanMeta 记录本次补采统计（supplementScans）。

    注意：新增条目的 parentIndex 是合并后 flatControls 的下标空间，与初扫条目
    残留的原始（未过滤）下标空间不同，层级展示以 controlsTree 为准。

    返回 (added_count, anchor_found)。
    """
    if not isinstance(payload, dict) or not sub_flats:
        return 0, False

    # 先用局部 parentIndex 重建嵌套子树（后续会将条目 parentIndex 改写为合并索引）
    sub_tree_root = _build_tree_from_flat(sub_flats)

    flat = payload.setdefault("flatControls", [])
    definitions = payload.setdefault("controlDefinitions", [])
    definitions_aligned = len(definitions) == len(flat)
    identity_to_index = {}
    for idx, item in enumerate(flat):
        identity_to_index.setdefault(_build_flat_control_identity(item), idx)

    resolved_target_window = target_window or payload.get("targetWindow", {}) or {}
    local_to_merged = {}
    new_items = []
    for local_idx, item in enumerate(sub_flats):
        identity = _build_flat_control_identity(item)
        if identity in identity_to_index:
            merged_idx = identity_to_index[identity]
            local_to_merged[local_idx] = merged_idx
            # 用实时采集结果补全既有条目的空缺字段（只补空，不覆盖既有值）
            existing = flat[merged_idx]
            for key in ("supportedPatterns", "expandCollapseState", "value", "toggleState", "pathHash"):
                if not existing.get(key) and item.get(key):
                    existing[key] = item.get(key)
            continue
        merged_idx = len(flat)
        try:
            local_parent = int(item.get("parentIndex", -1))
        except Exception:
            local_parent = -1
        item["parentIndex"] = local_to_merged.get(local_parent, -1)
        item["index"] = merged_idx + 1
        item["supplementSource"] = "point_supplement"
        identity_to_index[identity] = merged_idx
        local_to_merged[local_idx] = merged_idx
        flat.append(item)
        new_items.append(item)

    if new_items:
        _enrich_flat_controls(new_items, resolved_target_window)
        # 仅在既有定义与 flatControls 对齐时追加，保持 1:1；错位时交由保存路径就地补建
        if definitions_aligned:
            existing_ids = {
                str(definition.get("id", "")).strip()
                for definition in definitions
                if isinstance(definition, dict) and str(definition.get("id", "")).strip()
            }
            for item in new_items:
                if not _should_include_definition(item):
                    continue
                definitions.append(_build_control_definition_from_flat(item, existing_ids))

    # ---- controlsTree 锚点替换：子树节点 flatIndex 重映射到合并后下标 ----
    def _remap_flat_index(node):
        if not isinstance(node, dict):
            return
        node["flatIndex"] = local_to_merged.get(node.get("flatIndex", -1), -1)
        for child in node.get("children", []) or []:
            _remap_flat_index(child)

    anchor_found = False
    if isinstance(sub_tree_root, dict) and sub_tree_root:
        _remap_flat_index(sub_tree_root)
        tree = payload.get("controlsTree")
        if isinstance(tree, dict) and tree:
            anchor = _find_tree_node_by_identity(tree, sub_flats[0])
            if anchor is not None:
                anchor["children"] = _merge_tree_children_preserving(
                    anchor.get("children", []), sub_tree_root.get("children", [])
                )
                anchor["childCount"] = len(anchor["children"])
                anchor["isTransparentContainer"] = bool(
                    sub_tree_root.get("isTransparentContainer", anchor.get("isTransparentContainer", False))
                )
                # 锚点自身也用实时结果补空缺字段
                for key in ("supportedPatterns", "expandCollapseState", "value", "pathHash"):
                    if not anchor.get(key) and sub_tree_root.get(key):
                        anchor[key] = sub_tree_root.get(key)
                anchor_found = True
            else:
                tree.setdefault("children", []).append(sub_tree_root)
        else:
            payload["controlsTree"] = sub_tree_root
            anchor_found = True

    # ---- scanMeta 统计 ----
    scan_meta = payload.setdefault("scanMeta", {})
    scan_meta["totalControls"] = len(flat)
    by_type = dict(scan_meta.get("controlTypeSummary") or {})
    for item in new_items:
        control_type = str(item.get("controlType", "")).strip() or "Unknown"
        by_type[control_type] = by_type.get(control_type, 0) + 1
    scan_meta["controlTypeSummary"] = dict(sorted(by_type.items(), key=lambda pair: (-pair[1], pair[0])))
    scan_meta.setdefault("supplementScans", []).append(
        {
            "time": datetime.now().isoformat(timespec="seconds"),
            "rootPath": str(sub_flats[0].get("uiPath", "")),
            "collected": len(sub_flats),
            "added": len(new_items),
            "anchorFound": anchor_found,
        }
    )

    if status_callback:
        status_callback(f"定点补采合并完成：新增 {len(new_items)} 个控件", len(flat))
    return len(new_items), anchor_found


def _generate_probe_points(region_rect):
    region_rect = _normalize_rect_dict(region_rect)
    if not region_rect:
        return []
    width = max(1, region_rect["right"] - region_rect["left"])
    height = max(1, region_rect["bottom"] - region_rect["top"])
    cols = max(3, min(12, int(width / 90) + 1))
    rows = max(3, min(10, int(height / 90) + 1))
    points = []
    for row in range(rows):
        for col in range(cols):
            x = region_rect["left"] + int((col + 0.5) * width / cols)
            y = region_rect["top"] + int((row + 0.5) * height / rows)
            points.append((x, y))
    return points


_REGION_RELATED_CONTROL_TYPES = {
    "edit", "combobox", "spinner", "document", "list", "listitem", "button"
}
_REGION_LABEL_TYPES = {"text", "label", "static", "textblock", "control", "custom", "pane", "group"}


def _wrapper_rect_contains_point(wrapper, x, y):
    rect = _safe_get_value(lambda: wrapper.rectangle(), None)
    if rect is None:
        return False
    try:
        return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom
    except Exception:
        return False


def _wrapper_is_region_label(wrapper):
    control_type = normalize_control_type_name(
        _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
    ).strip().lower()
    return control_type in _REGION_LABEL_TYPES


def _wrapper_is_related_editable(wrapper):
    control_type = normalize_control_type_name(
        _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
    ).strip().lower()
    return control_type in _REGION_RELATED_CONTROL_TYPES


def _wrapper_horizontal_relation_score(label, candidate):
    """计算标签与候选控件的几何关系评分，返回距离评分（越小越近）或 None 表示不相关。

    支持的关系：
    - 同行右侧（最优）：标签右边的输入/下拉控件
    - 同行左侧：标签左边的控件
    - 矩形重叠：标签矩形覆盖控件矩形（常见于 WPF 中 TextBlock 覆盖 ComboBox）
    - 相邻行：标签正上/正下方的控件
    - 同祖级容器内的邻近控件
    """
    label_rect = _safe_get_value(lambda: label.rectangle(), None)
    candidate_rect = _safe_get_value(lambda: candidate.rectangle(), None)
    if label_rect is None or candidate_rect is None:
        return None
    try:
        label_h = max(1, label_rect.bottom - label_rect.top)
        label_w = max(1, label_rect.right - label_rect.left)
        cand_h = max(1, candidate_rect.bottom - candidate_rect.top)
        cand_w = max(1, candidate_rect.right - candidate_rect.left)

        vertical_gap = max(0, max(label_rect.top, candidate_rect.top) - min(label_rect.bottom, candidate_rect.bottom))
        horizontal_gap = max(0, max(label_rect.left, candidate_rect.left) - min(label_rect.right, candidate_rect.right))

        # 矩形重叠检测：标签和控件矩形有交集
        has_overlap = not (label_rect.right < candidate_rect.left or label_rect.left > candidate_rect.right
                           or label_rect.bottom < candidate_rect.top or label_rect.top > candidate_rect.bottom)

        if has_overlap:
            # 计算重叠面积占标签面积的比例
            overlap_w = min(label_rect.right, candidate_rect.right) - max(label_rect.left, candidate_rect.left)
            overlap_h = min(label_rect.bottom, candidate_rect.bottom) - max(label_rect.top, candidate_rect.top)
            overlap_area = max(0, overlap_w) * max(0, overlap_h)
            label_area = label_h * label_w
            overlap_ratio = overlap_area / max(1, label_area)
            if overlap_ratio >= 0.3:
                # 标签矩形大面积覆盖控件，优先级高
                return -100 + int((1.0 - overlap_ratio) * 50)

        # 同行检测：垂直间距不超过控件高度的 2 倍
        max_vertical_tolerance = max(40, min(120, label_h * 2, cand_h * 2))
        if vertical_gap > max_vertical_tolerance:
            # 相邻行检测：水平有交集且垂直距离合理
            if horizontal_gap == 0 and vertical_gap <= max_vertical_tolerance * 3:
                return vertical_gap + 50
            return None

        # 同行：计算方向惩罚和距离
        direction_penalty = 0
        if candidate_rect.left >= label_rect.right - 20:
            # 右侧（标准布局：标签在左，控件在右）
            direction_penalty = 0
        elif candidate_rect.right <= label_rect.left + 20:
            # 左侧（反向布局）
            direction_penalty = 15
        else:
            # 重叠或不明确方向
            direction_penalty = 5

        distance = horizontal_gap + vertical_gap * 2 + direction_penalty
        return distance
    except Exception:
        return None


def _collect_ancestor_related_wrappers(wrapper, max_ancestor_depth=6):
    """从区域命中的标签/容器向祖级横向查找可操作控件。

    增强逻辑：
    - 向上遍历祖级容器，检查各层兄弟控件
    - 对找到的可操作控件（Edit/ComboBox 等），进一步下钻其子级（如 ComboBox 的下拉选项）
    - 支持矩形重叠、同行、相邻行等多种几何关系
    """
    related = []
    current = wrapper
    seen = set()
    for _ in range(max_ancestor_depth):
        if current is None:
            break
        current_identity = _build_wrapper_identity(current)
        if current_identity in seen:
            break
        seen.add(current_identity)
        siblings = _safe_get_value(lambda: current.parent().children(), [])
        candidates = []
        for sibling in siblings:
            if _build_wrapper_identity(sibling) == current_identity:
                continue
            if not _wrapper_is_related_editable(sibling):
                continue
            distance = _wrapper_horizontal_relation_score(wrapper, sibling)
            if distance is not None:
                candidates.append((distance, sibling))
        candidates.sort(key=lambda item: item[0])
        for _dist, sibling in candidates[:4]:
            sibling_identity = _build_wrapper_identity(sibling)
            if sibling_identity not in [ _build_wrapper_identity(r) for r in related ]:
                related.append(sibling)
            # 对 ComboBox 下钻收集子级（可能包含下拉选项）
            sibling_type = normalize_control_type_name(
                _safe_get_value(lambda: getattr(sibling.element_info, "control_type", ""), ""),
                _safe_get_value(lambda: getattr(sibling.element_info, "localized_control_type", ""), ""),
            ).strip().lower()
            if sibling_type == "combobox":
                children = _safe_get_value(lambda: sibling.children(), [])
                for child in children:
                    child_identity = _build_wrapper_identity(child)
                    if child_identity and child_identity not in [ _build_wrapper_identity(r) for r in related ]:
                        related.append(child)
        parent = _safe_get_value(lambda: current.parent(), None)
        if not parent or parent is current:
            break
        current = parent
    return related


def _collect_region_probe_wrappers(
    backend,
    region_rect,
    target_window,
    flat_controls,
    seen_identities,
    root_handle="",
    max_depth=DEFAULT_MAX_DEPTH,
):
    region_rect = _normalize_rect_dict(region_rect)
    if not region_rect or Desktop is None:
        return
    desktop = Desktop(backend=backend)
    related_sources = set()
    for x, y in _generate_probe_points(region_rect):
        wrapper = _safe_get_value(lambda: desktop.from_point(x, y), None)
        if wrapper is None:
            continue
        current = wrapper
        for _ in range(max_depth + 4):
            if current is None:
                break
            current_identity = _build_wrapper_identity(current)
            if _append_wrapper_info(
                flat_controls,
                seen_identities,
                current,
                target_window,
                root_handle=root_handle,
                max_depth=max_depth,
            ):
                related_sources.add(current_identity)
            # 标签/容器不一定与输入控件同层，沿祖级查找同行的 Edit/ComboBox。
            if _wrapper_is_region_label(current):
                for related in _collect_ancestor_related_wrappers(current):
                    related_identity = _build_wrapper_identity(related)
                    if not related_identity:
                        continue
                    added = _append_wrapper_info(
                        flat_controls,
                        seen_identities,
                        related,
                        target_window,
                        root_handle=root_handle,
                        max_depth=max_depth,
                    )
                    for item in flat_controls:
                        if item.get("_wrapperIdentity") == related_identity:
                            item["regionRelated"] = True
                            item["regionRelation"] = "label-ancestor-sibling"
                    if added:
                        related_sources.add(related_identity)
            current_handle = _get_wrapper_handle(current)
            if root_handle and current_handle and str(current_handle) == str(root_handle):
                break
            parent = _safe_get_value(lambda: current.parent(), None)
            if not parent or parent is current:
                break
            current = parent


def _collect_fulltree_probe_wrappers(
    backend,
    target_window,
    target_window_wrapper,
    flat_controls,
    seen_identities,
    root_handle="",
    max_depth=DEFAULT_MAX_DEPTH,
    start_time=None,
    scan_timeout_seconds=30,
    status_callback=None,
):
    """整树采集时借鉴画框采集的网格探针：对整个窗口矩形做 from_point 采样，
    捕获纯树遍历（children 递归）漏掉的控件——例如虚拟化列表、滚动区外、
    折叠面板/未激活 Tab 中 isOffscreen=True 的控件。

    去重依赖 seen_identities，所以重复命中的控件不会被重复加入。
    """
    if Desktop is None:
        return
    window_rect = _rect_to_dict(_safe_get_value(lambda: target_window_wrapper.rectangle(), None))
    if not window_rect:
        return
    if start_time is None:
        start_time = time.time()
    try:
        desktop = Desktop(backend=backend)
    except Exception:
        return
    added_count = 0
    for x, y in _generate_probe_points(window_rect):
        if time.time() - start_time > scan_timeout_seconds:
            if status_callback:
                status_callback("整树探针超时，已停止采样。", len(flat_controls))
            break
        wrapper = _safe_get_value(lambda: desktop.from_point(x, y), None)
        if wrapper is None:
            continue
        current = wrapper
        for _ in range(max_depth + 4):
            if current is None:
                break
            if _append_wrapper_info(
                flat_controls,
                seen_identities,
                current,
                target_window,
                root_handle=root_handle,
                max_depth=max_depth,
            ):
                added_count += 1
                if status_callback and added_count % 20 == 0:
                    status_callback(f"整树探针补采 {added_count} 个控件...", len(flat_controls))
            current_handle = _get_wrapper_handle(current)
            if root_handle and current_handle and str(current_handle) == str(root_handle):
                break
            parent = _safe_get_value(lambda: current.parent(), None)
            if not parent or parent is current:
                break
            current = parent


# ── 标签伴随采集：保证"标签 → 输入/操作控件"成对采全 ─────────────────────
# 场景：BFS/画框只采到"名称"标签，其右侧文本框（WPF 自定义输入控件常报 Custom
# 且无 name/automationId/Pattern）未被采到，自动化无法定位输入。这里对每个已采集
# 标签做 from_point 定向探测（标签右侧/下方），把实际输入控件强制补采入库并标记
# regionRelated/relatedLabelName，使其通过区域过滤与噪声过滤。
_COMPANION_STRONG_INPUT_TYPES = {
    "edit", "combobox", "spinner", "splitbutton", "document", "slider",
    "checkbox", "radiobutton", "button", "list", "listitem",
}
_COMPANION_CONTAINER_TYPES = {"custom", "pane", "group", "image"}
# 伴随探测的标签数量上限（防止大表单探测耗时失控）
_COMPANION_MAX_LABELS = 60
# 每个标签最多补采的伴随控件数（如 输入框 + 浏览按钮）
_COMPANION_MAX_PER_LABEL = 2


def _wrapper_control_type_name(wrapper):
    return normalize_control_type_name(
        _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
    ).strip().lower()


def _item_is_companion_input(item):
    """dict 级判定：是否是"标签伴随"的输入/操作控件（强类型直通，容器需可交互证据）。"""
    if not isinstance(item, dict):
        return False
    control_type = str(item.get("controlType", "")).strip().lower()
    if control_type in _COMPANION_STRONG_INPUT_TYPES:
        return True
    if control_type not in _COMPANION_CONTAINER_TYPES:
        return False
    class_name = str(item.get("className", "")).strip().lower()
    if any(hint in class_name for hint in _INPUT_CLASS_NAME_HINTS):
        return True
    return _item_has_actionable_pattern(item)


def _wrapper_is_companion_input(wrapper):
    """wrapper 级判定：强类型直通；容器类型需 className/Pattern/可聚焦证据。"""
    control_type = _wrapper_control_type_name(wrapper)
    if control_type in _COMPANION_STRONG_INPUT_TYPES:
        return True
    if control_type not in _COMPANION_CONTAINER_TYPES:
        return False
    class_name = str(_safe_get_value(lambda: wrapper.class_name(), "")).strip().lower()
    if any(hint in class_name for hint in _INPUT_CLASS_NAME_HINTS):
        return True
    patterns = {str(p).strip().lower() for p in _detect_supported_patterns(wrapper)}
    if patterns & _ACTIONABLE_PATTERNS:
        return True
    element = _safe_get_value(lambda: getattr(wrapper.element_info, "element", None), None)
    focusable = _read_uia_property(element, _UIA_PROP_IDS["isKeyboardFocusable"], "")
    return focusable is True or str(focusable).strip().lower() == "true"


def _label_companion_geometry_match(label_rect, candidate_rect):
    """标签-伴随控件几何关系：同行右侧（间隙≤420px）或正下方（水平重叠、间隙≤120px）。"""
    label_rect = _normalize_rect_dict(label_rect)
    candidate_rect = _normalize_rect_dict(candidate_rect)
    if not label_rect or not candidate_rect:
        return False
    # 同行右侧（标准表单布局）
    if _vertical_overlap_ratio(label_rect, candidate_rect) >= _SAME_ROW_MIN_VERTICAL_OVERLAP:
        gap = candidate_rect["left"] - label_rect["right"]
        if -_SAME_ROW_LEFT_TOLERANCE <= gap <= 420:
            return True
    # 正下方（标签在上、控件在下的纵向布局）
    if _horizontal_overlap_ratio(label_rect, candidate_rect) >= 0.3:
        gap_below = candidate_rect["top"] - label_rect["bottom"]
        if 0 <= gap_below <= 120:
            return True
    return False


def _companion_path_blocked_by_label(label_item, label_rect, candidate_rect, label_pairs):
    """标签与候选之间隔着另一个标签（双栏/多栏表单）时视为阻挡，防止跨栏误绑。"""
    if not label_pairs:
        return False
    gap_left = label_rect["right"] - 4
    gap_right = candidate_rect["left"] + 4
    if gap_right <= gap_left:
        return False
    for other_item, other_rect in label_pairs:
        if other_item is label_item:
            continue
        if _vertical_overlap_ratio(label_rect, other_rect) < _SAME_ROW_MIN_VERTICAL_OVERLAP:
            continue
        if other_rect["left"] >= gap_left and other_rect["right"] <= gap_right:
            return True
    return False


def _find_existing_companion_for_label(label_item, label_rect, flat_controls, label_pairs):
    """dict 级查重：标签附近是否已有伴随输入控件（有则补标记并跳过实时探测）。"""
    label_name = str(label_item.get("name", "")).strip()
    for item in flat_controls:
        if not isinstance(item, dict) or item is label_item:
            continue
        if not _item_is_companion_input(item):
            continue
        existing_related = str(item.get("relatedLabelName", "")).strip()
        if existing_related and existing_related != label_name:
            continue  # 已被其他标签占用，不抢占
        candidate_rect = _normalize_rect_dict(item.get("boundingBox"))
        if not _label_companion_geometry_match(label_rect, candidate_rect):
            continue
        if _companion_path_blocked_by_label(label_item, label_rect, candidate_rect, label_pairs):
            continue
        item["regionRelated"] = True
        if not str(item.get("regionRelation", "")).strip():
            item["regionRelation"] = "label-companion-existing"
        if not existing_related:
            item["relatedLabelName"] = label_name
        return True
    return False


def _generate_label_companion_probe_points(label_rect):
    """围绕标签生成探测点：右侧一行（+6~+420px，步长28）+ 下方一列（+6~+120px，步长24）。"""
    points = []
    center_y = int((label_rect["top"] + label_rect["bottom"]) / 2)
    for offset in range(6, 421, 28):
        points.append((label_rect["right"] + offset, center_y))
    center_x = int((label_rect["left"] + label_rect["right"]) / 2)
    for offset in range(6, 121, 24):
        points.append((center_x, label_rect["bottom"] + offset))
    return points


def _climb_to_companion_input(wrapper, label_rect, root_handle="", max_climb=8):
    """从 from_point 命中点沿祖先链爬升到 input-like 控件。

    包含守卫：候选矩形完全包住标签且为容器类型时拒绝（那是共享布局容器，
    不是输入控件）；强输入类型允许覆盖标签（覆盖式输入框）。
    """
    current = wrapper
    for _ in range(max_climb):
        if current is None:
            return None
        if _wrapper_is_companion_input(current):
            control_type = _wrapper_control_type_name(current)
            rect = _rect_to_dict(_safe_get_value(lambda: current.rectangle(), None))
            if rect and label_rect and _rect_contains(label_rect, rect):
                if control_type not in _COMPANION_STRONG_INPUT_TYPES:
                    return None
            return current
        current_handle = _get_wrapper_handle(current)
        if root_handle and current_handle and str(current_handle) == str(root_handle):
            return None
        parent = _safe_get_value(lambda: current.parent(), None)
        if not parent or parent is current:
            return None
        current = parent
    return None


def _mark_label_companion_item(flat_controls, identity, label_name, relation):
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        if str(item.get("_wrapperIdentity", "")) != identity:
            continue
        item["regionRelated"] = True
        item["labelCompanion"] = True
        if not str(item.get("regionRelation", "")).strip():
            item["regionRelation"] = relation
        if not str(item.get("relatedLabelName", "")).strip():
            item["relatedLabelName"] = label_name
        return item
    return None


def _collect_label_companion_wrappers(
    backend,
    target_window,
    flat_controls,
    seen_identities,
    root_handle="",
    max_depth=DEFAULT_MAX_DEPTH,
    start_time=None,
    scan_timeout_seconds=30,
    status_callback=None,
):
    """标签伴随采集（树采集+画框采集通用）。

    对每个已采集标签（有 name + 矩形），先 dict 级检查其附近是否已有输入/操作
    控件；没有则在标签右侧/下方做 from_point 定向探测，沿祖先链提升到 input-like
    层级后强制入库，并标记 regionRelated/relatedLabelName——保证"名称"这类标签
    对应的输入框一定能被采到、能参与后续关联与定位。
    """
    if Desktop is None or not flat_controls:
        return
    if start_time is None:
        start_time = time.time()
    labels = []
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        if not _control_is_assoc_label(item):
            continue
        rect = _normalize_rect_dict(item.get("boundingBox"))
        if not rect:
            continue
        labels.append((item, rect))
    if not labels:
        return
    labels.sort(key=lambda pair: (pair[1]["top"], pair[1]["left"]))
    labels = labels[:_COMPANION_MAX_LABELS]
    try:
        desktop = Desktop(backend=backend)
    except Exception:
        return
    added_count = 0
    for label_item, label_rect in labels:
        if time.time() - start_time > scan_timeout_seconds:
            if status_callback:
                status_callback("标签伴随采集超时，已停止探测。", len(flat_controls))
            break
        label_name = str(label_item.get("name", "")).strip()
        # 快速通道：已存在几何相邻的输入控件，仅补标记，零 COM 开销
        if _find_existing_companion_for_label(label_item, label_rect, flat_controls, labels):
            continue
        collected = 0
        for x, y in _generate_label_companion_probe_points(label_rect):
            if time.time() - start_time > scan_timeout_seconds:
                break
            wrapper = _safe_get_value(lambda: desktop.from_point(x, y), None)
            if wrapper is None:
                continue
            candidate = _climb_to_companion_input(wrapper, label_rect, root_handle=root_handle)
            if candidate is None:
                continue
            candidate_rect = _rect_to_dict(_safe_get_value(lambda: candidate.rectangle(), None))
            if not _label_companion_geometry_match(label_rect, candidate_rect):
                continue
            if _companion_path_blocked_by_label(label_item, label_rect, candidate_rect, labels):
                continue
            identity = _build_wrapper_identity(candidate)
            if not identity:
                continue
            existing = _mark_label_companion_item(flat_controls, identity, label_name, "label-companion-probe")
            if existing is not None:
                collected += 1
            else:
                added = _append_wrapper_info(
                    flat_controls,
                    seen_identities,
                    candidate,
                    target_window,
                    root_handle=root_handle,
                    max_depth=max_depth,
                )
                if added:
                    _mark_label_companion_item(flat_controls, identity, label_name, "label-companion-probe")
                    added_count += 1
                    collected += 1
            if collected >= _COMPANION_MAX_PER_LABEL:
                break
    if status_callback and added_count:
        status_callback(f"标签伴随补采 {added_count} 个输入控件", len(flat_controls))


def _find_loose_companion_control(label_rect, controls, label, claimed, label_pairs):
    """第三轮宽松伴随关联：接受伴随输入型候选（含容器证据型），同行右侧间隙≤420px。"""
    label_center_y = (label_rect["top"] + label_rect["bottom"]) / 2.0
    best = None
    best_key = None
    for candidate in controls:
        if candidate is label or id(candidate) in claimed:
            continue
        if not (_control_is_assoc_actionable(candidate) or _item_is_companion_input(candidate)):
            continue
        candidate_rect = _normalize_rect_dict(candidate.get("boundingBox"))
        if not candidate_rect:
            continue
        if _vertical_overlap_ratio(label_rect, candidate_rect) < _SAME_ROW_MIN_VERTICAL_OVERLAP:
            continue
        horizontal_gap = candidate_rect["left"] - label_rect["right"]
        if horizontal_gap < -_SAME_ROW_LEFT_TOLERANCE or horizontal_gap > 420:
            continue
        if _companion_path_blocked_by_label(label, label_rect, candidate_rect, label_pairs):
            continue
        horizontal_gap = max(0, horizontal_gap)
        candidate_center_y = (candidate_rect["top"] + candidate_rect["bottom"]) / 2.0
        key = (horizontal_gap, abs(candidate_center_y - label_center_y))
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    return best, "loose-companion"


def _prune_empty_unidentified_containers(flat_controls):
    """整树采集放宽过滤后，只剔除"真正的空壳容器"：
    无 name / automationId / labelText 的 Custom/Pane/Group，且在扁平树中没有任何子级。

    这样既保留所有有标识的控件与承载真实控件的结构容器，又不会让无意义的
    空布局节点淹没结果。相比旧的 _filter_noise_controls（无脑丢弃全部无标识容器），
    这里是保守剪枝，最大化保留信息。
    """
    if not flat_controls:
        return flat_controls
    # 统计每个控件在扁平树里的直接子级数量（依赖 parentIndex，探针补采的控件没有该字段视为叶子）。
    child_count = {}
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        parent_index = item.get("parentIndex", -1)
        try:
            parent_index = int(parent_index)
        except Exception:
            parent_index = -1
        if parent_index >= 0:
            child_count[parent_index] = child_count.get(parent_index, 0) + 1

    kept = []
    for idx, item in enumerate(flat_controls):
        if not isinstance(item, dict):
            continue
        control_type = str(item.get("controlType", "")).strip().lower()
        has_name = bool(str(item.get("name", "")).strip())
        has_automation_id = bool(str(item.get("automationId", "")).strip())
        has_label_text = bool(str(item.get("labelText", "")).strip())
        is_empty_container = (
            control_type in {"custom", "pane", "group"}
            and not has_name
            and not has_automation_id
            and not has_label_text
            and child_count.get(idx, 0) == 0
        )
        # 标签伴随/关联控件豁免：已确认是某标签对应的实际输入控件，不得按空壳剪枝
        if is_empty_container and (item.get("regionRelated") or str(item.get("relatedLabelName", "")).strip()):
            is_empty_container = False
        # 可交互的空壳容器（自定义输入框/图形按钮）不视为空壳，保留
        if is_empty_container and not _item_has_actionable_pattern(item):
            continue
        kept.append(item)
    return kept


def _build_target_window_info(window_wrapper):
    element_info = _safe_get_value(lambda: window_wrapper.element_info, None)
    return {
        "title": str(_safe_get_value(lambda: window_wrapper.window_text(), "")).strip(),
        "className": str(_safe_get_value(lambda: window_wrapper.class_name(), "")).strip(),
        "processId": str(_safe_get_value(lambda: getattr(element_info, "process_id", ""), "")).strip(),
        "handle": str(_safe_get_value(lambda: getattr(element_info, "handle", ""), "")).strip(),
        "frameworkId": str(_safe_get_value(lambda: getattr(element_info, "framework_id", ""), "")).strip(),
    }


def _ensure_unique_control_id(control_definition, existing_ids):
    base_id = str(control_definition.get("id", "")).strip() or "control"
    unique_id = base_id
    suffix = 2
    while unique_id in existing_ids:
        unique_id = f"{base_id}_{suffix}"
        suffix += 1
    control_definition["id"] = unique_id
    existing_ids.add(unique_id)
    return control_definition


def _should_include_definition(item):
    """入库过滤：折叠进父级 TextBox 的 PART_ContentHost 不再单独入库。

    它在 _normalize_textbox_wrappers 中已折叠（foldedIntoParent=True），
    由父级 TextBox 替代，单独入库只会带来不唯一 automation_id 的冗余控件。
    """
    return isinstance(item, dict) and not item.get("foldedIntoParent")


def _build_control_definition_from_flat(flat_item, existing_ids):
    inspect_data = dict(flat_item.get("inspectData", {}) or {})
    # 将关联/回填产物同步进 inspectData：运行时 label_text 候选与界面展示直接可用
    for sync_key in ("labelText", "labelRelation", "relatedLabelName", "nameSource"):
        sync_value = str(flat_item.get(sync_key, "")).strip()
        if sync_value and not str(inspect_data.get(sync_key, "")).strip():
            inspect_data[sync_key] = sync_value
    display_name = (
        # 优先 helpText 提炼的功能名（与采集端树显示 _display_control_name 一致），
        # 避免"采集端显示'创建一个综合'、保存后却变成 automationId 分词'综合2'"的不一致。
        str(flat_item.get("functionText", "")).strip()
        or _extract_functional_name(flat_item)
        or str(flat_item.get("savedControlName", "")).strip()
        or str(flat_item.get("suggestedControlName", "")).strip()
        or str(flat_item.get("displayName", "")).strip()
        or "新控件"
    )
    control_type = str(flat_item.get("controlType", "")).strip()
    base_id = slugify_filename("_".join(item for item in [inspect_data.get("automationId", ""), display_name, control_type] if item), "control")
    control_definition = {
        "id": base_id,
        "name": display_name,
        "role": f"来自控件库扫描：{flat_item.get('windowTitle', '')}",
        "enabled": True,
        "windowTitle": str(flat_item.get("windowTitle", "")).strip(),
        "targetMethod": str(flat_item.get("recommendedTargetMethod", "")).strip(),
        "targetValue": str(flat_item.get("recommendedTargetValue", "")).strip(),
        "_qualityTier": str(flat_item.get("qualityTier", "")).strip(),
        "templateKey": "",
        "uiPath": str(flat_item.get("uiPath", "")).strip(),
        "notes": (
            f"由控件库扫描生成，定位评分={flat_item.get('locatorScore', 0)}，"
            f"策略={flat_item.get('locatorReason', '')}，"
            f"质量={flat_item.get('qualityTier', '')}，"
            f"说明={flat_item.get('qualityReason', '')}"
            + (
                f" | 可自动化风险={flat_item.get('automatabilityRisk', '')}"
                + (
                    f"；原因={'；'.join(flat_item.get('automatabilityReasons', []) or [])}"
                    if flat_item.get('automatabilityReasons')
                    else ""
                )
            )
            if str(flat_item.get('automatabilityRisk', '')).strip()
            else ""
        ),
        "rawInspectText": str(flat_item.get("rawInspectText", "")).strip(),
        "auxChecks": [str(item).strip() for item in flat_item.get("auxChecks", []) if str(item).strip()],
        # 标签关联与交互元数据（流程编排直接可用：标签定位、快捷键、可驱动性预判）
        "labelText": _clean_label_text(flat_item.get("labelText", "")),
        "labelRelation": str(flat_item.get("labelRelation", "")).strip(),
        "labeledByAutomationId": str(flat_item.get("labeledByAutomationId", "")).strip(),
        "accessKey": str(flat_item.get("accessKey", "")).strip(),
        "isEnabled": flat_item.get("isEnabled"),
        "supportedPatterns": [str(p) for p in (flat_item.get("supportedPatterns") or []) if str(p).strip()],
        "helpText": str(flat_item.get("helpText", "")).strip(),
        "functionText": str(flat_item.get("functionText", "")).strip(),
        # 标签伴随/关联元数据（流程编排可直接按关联标签定位输入控件）
        "relatedLabelName": _clean_label_text(flat_item.get("relatedLabelName", "")),
        "labelCompanion": bool(flat_item.get("labelCompanion")),
        "regionRelated": bool(flat_item.get("regionRelated")),
        "nameSource": str(flat_item.get("nameSource", "")).strip(),
        # 交互与框架元数据（可驱动性预判/富文本内容/项状态）
        "isKeyboardFocusable": str(flat_item.get("isKeyboardFocusable", "")).strip(),
        "expandCollapseState": str(flat_item.get("expandCollapseState", "")).strip(),
        "textContent": str(flat_item.get("textContent", "")).strip(),
        "itemStatus": str(flat_item.get("itemStatus", "")).strip(),
        # MSAA 桥接信息（对齐 Inspect MSAA 面板，老控件兜底定位/说明用）
        "legacyHelp": str(flat_item.get("legacyHelp", "")).strip(),
        "legacyDefaultAction": str(flat_item.get("legacyDefaultAction", "")).strip(),
        "legacyRoleText": str(flat_item.get("legacyRoleText", "")).strip(),
        "legacyStateText": str(flat_item.get("legacyStateText", "")).strip(),
        "inspectData": inspect_data,
        "source": "control_map",
    }
    tab_nav = flat_item.get("tabNavigation")
    if tab_nav:
        control_definition["tabNavigation"] = tab_nav
    # 下拉框已采可选项：随控件定义一起入库，流程编辑可枚举、执行时键盘导航兜底选中
    option_values = [
        str(value).strip()
        for value in (flat_item.get("optionValues") or inspect_data.get("optionValues") or [])
        if str(value).strip()
    ]
    if option_values:
        control_definition["optionValues"] = option_values
        control_definition["optionCount"] = len(option_values)
    return _ensure_unique_control_id(control_definition, existing_ids)


def _expand_backend_candidates(backend):
    backend = str(backend or DEFAULT_BACKEND).strip().lower() or DEFAULT_BACKEND
    if backend == "smart":
        return ["uia", "win32"]
    if backend in {"uia", "win32"}:
        return [backend]
    return ["uia"]


def _score_region_candidate(item, region_rect):
    item_rect = _normalize_rect_dict(item.get("boundingBox"))
    region_rect = _normalize_rect_dict(region_rect)
    if not item_rect or not region_rect:
        return None
    intersection = _intersection_area(item_rect, region_rect)
    if intersection <= 0:
        return None
    item_area = _rect_area(item_rect) or 1
    overlap_ratio = intersection / item_area
    center_distance = _distance(_rect_center(item_rect), _rect_center(region_rect))
    score = (
        int(item.get("locatorScore", 0) or 0) * 1000
        + int(overlap_ratio * 1000)
        - int(center_distance)
        - int(item.get("depth", 0) or 0) * 25
    )
    if _rect_contains(item_rect, region_rect):
        score += 300
    return score


# 同行标签→控件关联：下拉框/输入框常紧贴标签右缘，允许的最大水平间隙。
_SAME_ROW_MAX_GAP = 60
# 同行关联允许控件左缘略早于标签右缘的容差（超过则视为重叠，交由重叠关联处理）。
_SAME_ROW_LEFT_TOLERANCE = 20
# 判定"同一行"所需的最小垂直重叠比例。
_SAME_ROW_MIN_VERTICAL_OVERLAP = 0.5
# 纵向相邻关联：标签在控件正上/下方时，允许的最大垂直间隙与最小水平重叠比例。
_VERTICAL_MAX_GAP = 40
_VERTICAL_MIN_HORIZONTAL_OVERLAP = 0.5
# 矩形重叠关联：标签矩形与控件矩形大面积重叠（如标签覆盖下拉框）时的最小重叠比例。
_MIN_RECT_OVERLAP_RATIO = 0.5
_ASSOC_LABEL_TYPES = {"text", "label", "static", "textblock"}
_ASSOC_ACTIONABLE_TYPES = {"button", "combobox", "edit", "spinner", "splitbutton"}
_DROPDOWN_AUTOMATION_ID_HINTS = ("dropdownbutton", "combobox", "combo")
_DROPDOWN_CLASS_HINTS = ("radcombobox", "combobox")
_DROPDOWN_OPTION_CLASS_HINTS = ("comboboxitem",)
# 自动展开下拉框采选项：expand() 后等待弹出层渲染的时间（秒）与遍历选项的最大深度。
_DROPDOWN_EXPAND_WAIT_SECONDS = 0.4
_DROPDOWN_OPTION_WALK_MAX_DEPTH = 5


def _control_is_dropdown(item):
    """识别下拉框：ComboBox 类型，或 automationId/className 命中下拉按钮/组合框特征。"""
    if not isinstance(item, dict):
        return False
    control_type = str(item.get("controlType", "")).strip().lower()
    if control_type == "combobox":
        return True
    automation_id = str(item.get("automationId", "")).strip().lower()
    class_name = str(item.get("className", "")).strip().lower()
    if any(hint in automation_id for hint in _DROPDOWN_AUTOMATION_ID_HINTS):
        return True
    if any(hint in class_name for hint in _DROPDOWN_CLASS_HINTS):
        return True
    return False


def _control_is_dropdown_option(item):
    """识别下拉框可选项：ListItem 或 RadComboBoxItem 一类的选项控件。"""
    if not isinstance(item, dict):
        return False
    control_type = str(item.get("controlType", "")).strip().lower()
    class_name = str(item.get("className", "")).strip().lower()
    if control_type == "listitem":
        return True
    return any(hint in class_name for hint in _DROPDOWN_OPTION_CLASS_HINTS)


def _wrapper_is_dropdown_option(wrapper):
    """live 包装器层面判定是否为下拉选项（ListItem/RadComboBoxItem）。"""
    control_type = normalize_control_type_name(
        _safe_get_value(lambda: getattr(wrapper.element_info, "control_type", ""), ""),
        _safe_get_value(lambda: getattr(wrapper.element_info, "localized_control_type", ""), ""),
    ).strip().lower()
    if control_type == "listitem":
        return True
    class_name = str(_safe_get_value(lambda: wrapper.class_name(), "")).strip().lower()
    return any(hint in class_name for hint in _DROPDOWN_OPTION_CLASS_HINTS)


def _wrapper_option_text(wrapper):
    text = str(_safe_get_value(lambda: wrapper.window_text(), "")).strip()
    if not text:
        text = str(_safe_get_value(lambda: getattr(wrapper.element_info, "name", ""), "")).strip()
    return text


def _looks_like_type_name(text):
    """判断文本是否形如 WPF 绑定对象的类型全名（如 MTD.Xxx.Yyy），此类值非用户可读选项。"""
    text = str(text or "").strip()
    if not text:
        return False
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", text))


def _extract_option_text(item_wrapper, max_depth=3):
    """提取单个选项的可读文本：选项自身 name 若是类型全名（如 MTDLocalizedEnumValue
    的 ToString），则下钻到其子级 TextBlock 取真正显示文本（如"组"）。"""
    text = _wrapper_option_text(item_wrapper)
    if text and not _looks_like_type_name(text):
        return text
    found = [""]

    def _walk(wrapper, depth):
        if found[0] or wrapper is None or depth > max_depth:
            return
        for child in _safe_get_value(lambda: wrapper.children(), []) or []:
            candidate = _wrapper_option_text(child)
            if candidate and not _looks_like_type_name(candidate):
                found[0] = candidate
                return
            _walk(child, depth + 1)

    _walk(item_wrapper, 0)
    return found[0]


def _collect_option_texts_from_wrapper(root_wrapper, max_depth=_DROPDOWN_OPTION_WALK_MAX_DEPTH):
    """从给定包装器向下遍历，收集 ListItem/RadComboBoxItem 选项的可读文本（保序去重）。"""
    texts = []
    seen = set()

    def _walk(wrapper, depth):
        if wrapper is None or depth > max_depth:
            return
        for child in _safe_get_value(lambda: wrapper.children(), []) or []:
            if _wrapper_is_dropdown_option(child):
                text = _extract_option_text(child)
                if text and text not in seen and not _looks_like_type_name(text):
                    seen.add(text)
                    texts.append(text)
                # 选项内部不再继续下钻，避免重复采集其子文本。
            else:
                _walk(child, depth + 1)

    _walk(root_wrapper, 0)
    return texts


def _resolve_expandable_wrapper(wrapper, max_up=4):
    """从命中的下拉相关控件（如 PART_DropDownButton）向上寻找支持
    ExpandCollapse 的元素（如 RadComboBox）；找不到则兑底返回原控件。"""
    current = wrapper
    for _ in range(max_up + 1):
        if current is None:
            break
        if hasattr(current, "expand") and hasattr(current, "collapse"):
            return current
        current = _safe_get_value(lambda: current.parent(), None)
    return wrapper


def _open_dropdown(wrapper, expandable):
    """尝试程序化打开下拉框，返回 (opened, closer, strategy)。

    优先 UIA ExpandCollapse（RadComboBox）；其次 Toggle（PART_DropDownButton 常为
    toggle 按钮）；最后 Invoke 兑底。closer 用于采完后恢复关闭，strategy 记录成功策略。
    """
    if expandable is not None and hasattr(expandable, "expand") and hasattr(expandable, "collapse"):
        try:
            expandable.expand()
            return True, (lambda: _safe_get_value(lambda: expandable.collapse(), None)), "expand"
        except Exception:
            pass
    for target in (wrapper, expandable):
        if target is not None and hasattr(target, "toggle"):
            try:
                target.toggle()
                return True, (lambda t=target: _safe_get_value(lambda: t.toggle(), None)), "toggle"
            except Exception:
                pass
    for target in (wrapper, expandable):
        if target is not None and hasattr(target, "invoke"):
            try:
                target.invoke()
                return True, (lambda t=target: _safe_get_value(lambda: t.invoke(), None)), "invoke"
            except Exception:
                pass
    return False, None, ""


def _wrap_top_level_element(elem_info, backend):
    """把 desktop 的原始子 element_info 包装成可遍历的控件包装器。

    Desktop.windows() 会因可见性/标题等默认过滤漏掉 WPF 弹出窗口，故需从
    element_info.children() 拿到未过滤的顶层窗口再手动包装。
    """
    if elem_info is None:
        return None
    try:
        if backend == "uia":
            from pywinauto.controls.uiawrapper import UIAWrapper
            return UIAWrapper(elem_info)
        from pywinauto.controls.hwndwrapper import HwndWrapper
        return HwndWrapper(elem_info)
    except Exception:
        return None


def _enumerate_top_level_windows(backend):
    """枚举顶层窗口：合并 Desktop.windows()（已过滤）与 desktop 原始子节点
    （未过滤，含 WPF 弹出窗口），按句柄/运行时 ID 去重后返回。"""
    results = []
    seen_keys = set()

    def _add(win):
        if win is None:
            return
        key = _get_wrapper_handle(win) or _get_wrapper_runtime_id(win)
        if key:
            if key in seen_keys:
                return
            seen_keys.add(key)
        results.append(win)

    try:
        for win in Desktop(backend=backend).windows():
            _add(win)
    except Exception:
        pass
    try:
        desktop = Desktop(backend=backend)
        raw_children = _safe_get_value(lambda: desktop.element_info.children(), []) or []
        for child_info in raw_children:
            _add(_wrap_top_level_element(child_info, backend))
    except Exception:
        pass
    return results


def _collect_options_near_anchor(root_wrapper, anchor_rect, max_depth=14):
    """在主窗口视觉树内按锚点矩形圈定"弹出区域"，采集其中的下拉选项文本。

    用于 WPF Popup 内嵌于主窗口视觉树（未独立成顶层窗口）的情形：此时选项项
    仍是主窗口的后代，但位于锚点下拉按钮正下方的弹出层。仅按空间位置圈定，
    避免把主窗口其它无关 ListItem 也当成本下拉框的选项。
    """
    anchor = _normalize_rect_dict(anchor_rect)
    if root_wrapper is None or not anchor:
        return []
    zone = {
        "left": anchor["left"] - 40,
        "top": anchor["top"] - 4,
        "right": anchor["right"] + 40,
        "bottom": anchor["bottom"] + 600,
    }
    texts = []
    seen = set()

    def _walk(wrapper, depth):
        if wrapper is None or depth > max_depth:
            return
        for child in _safe_get_value(lambda: wrapper.children(), []) or []:
            if _wrapper_is_dropdown_option(child):
                rect = _rect_to_dict(_safe_get_value(lambda: child.rectangle(), None))
                if rect and _rect_intersects(rect, zone):
                    text = _extract_option_text(child)
                    if text and text not in seen and not _looks_like_type_name(text):
                        seen.add(text)
                        texts.append(text)
            else:
                _walk(child, depth + 1)

    _walk(root_wrapper, 0)
    return texts


def _nearest_option_ancestor(wrapper, max_up=5):
    """从命中点的元素向上寻找最近的下拉选项容器（ListItem/RadComboBoxItem）。

    Desktop.from_point 命中的往往是选项内部的 TextBlock 或选项本身，需向上回溯
    到选项容器再统一提取文本，同时借此过滤掉滚动条/边框等非选项命中。
    """
    current = wrapper
    for _ in range(max_up + 1):
        if current is None:
            return None
        if _wrapper_is_dropdown_option(current):
            return current
        current = _safe_get_value(lambda c=current: c.parent(), None)
    return None


def _realize_options_by_point_sweep(anchor_rect, backend="uia", desktop=None,
                                    step=22, max_span=640, existing=None, diag=None,
                                    move_cursor=True, dwell_seconds=0.06, max_probes=10):
    """在锚点下拉按钮正下方按屏幕坐标做"命中点扫掠"，强制实体化并采集选项文本。

    MTD 等 WPF 下拉框对选项列表启用 UI 虚拟化：未实体化的选项节点在 UIA 树中不存在，
    普通子树遍历/顶层窗口枚举都取不到，而 UIA ElementFromPoint 命中测试本身不会触发
    WPF 实体化。故默认把物理鼠标移到每个候选位置并短暂停留，让 WPF 因真实鼠标悬停
    而实体化该项，再经 Desktop.from_point 读取文本；扫完把鼠标复位。down 方向无果时
    向上扫掠兜底（部分下拉框弹出层向上展开）。max_probes 限制每方向探测次数，避免
    空弹出层下拉框拖长采集。desktop 可注入以便测试。
    """
    anchor = _normalize_rect_dict(anchor_rect)
    if not anchor:
        return []
    if desktop is None:
        if Desktop is None:
            return []
        desktop = _safe_get_value(lambda: Desktop(backend=backend), None)
    if desktop is None or not hasattr(desktop, "from_point"):
        return []
    x = (anchor["left"] + anchor["right"]) // 2
    original_pos = None
    if move_cursor:
        try:
            pt = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(pt))
            original_pos = (pt.x, pt.y)
        except Exception:
            original_pos = None

    seen = set(existing or [])
    ordered = []
    hits = 0
    misses = 0

    def _probe(y):
        nonlocal hits, misses
        if move_cursor:
            try:
                user32.SetCursorPos(x, y)
            except Exception:
                pass
            if dwell_seconds:
                time.sleep(dwell_seconds)
        wrapper = _safe_get_value(lambda yy=y: desktop.from_point(x, yy), None)
        option = _nearest_option_ancestor(wrapper)
        text = _extract_option_text(option) if option is not None else ""
        if text and not _looks_like_type_name(text):
            if text not in seen:
                seen.add(text)
                ordered.append(text)
            hits += 1
            misses = 0
        elif ordered:
            # 已进入选项列表后连续多次命中空白，视为已越过列表末尾，提前结束。
            misses += 1

    def _sweep(y_start, y_end):
        y = y_start
        step_sign = step if y_end >= y_start else -step
        probes = 0
        while (y <= y_end if y_end >= y_start else y >= y_end) and misses < 6 and probes < max_probes:
            _probe(y)
            y += step_sign
            probes += 1

    try:
        # 先向下扫（绝大多数下拉框弹出层在按钮正下方）。
        _sweep(anchor["bottom"] + 2, anchor["bottom"] + max_span)
        if not ordered:
            _sweep(anchor["top"] - 2, anchor["top"] - max_span)
    finally:
        if move_cursor and original_pos is not None:
            try:
                user32.SetCursorPos(original_pos[0], original_pos[1])
            except Exception:
                pass
    if isinstance(diag, dict):
        if move_cursor:
            diag["pointSweepUsedCursor"] = True
        diag["pointSweepHits"] = hits
        diag["pointSweepOptionCount"] = len(ordered)
    return ordered


def _collect_options_from_popups(reference_wrapper, backend, diag=None, anchor_rect=None):
    """从同进程的顶层 Popup 窗口中采集选项，兑底再从主窗口内嵌弹出层采集。

    WPF 下拉框（如 MTD 自定义 DropDownButton）展开后，选项常位于一个独立的
    顶层弹出窗口（ListItem 的祖先直接是 Window 而非该下拉框）。此类窗口常被
    Desktop.windows() 的默认过滤漏掉，故改用 _enumerate_top_level_windows 合并
    未过滤的 desktop 原始子节点一并扫描；若仍无所获，则回退到主窗口视觉树内按
    锚点空间位置圈定弹出区域采集。
    """
    if Desktop is None or reference_wrapper is None:
        return []
    process_id = str(_safe_get_value(lambda: getattr(reference_wrapper.element_info, "process_id", ""), "")).strip()
    main_top = _safe_get_value(lambda: reference_wrapper.top_level_parent(), None)
    main_handle = _get_wrapper_handle(main_top) if main_top is not None else ""
    windows = _enumerate_top_level_windows(backend)
    texts = []
    seen = set()
    scanned = 0
    windows_with_options = []
    for win in windows:
        win_handle = _get_wrapper_handle(win)
        if main_handle and str(win_handle) == str(main_handle):
            continue  # 跳过主窗口（体量庞大，稍后按需做锚点区域兑底）
        win_pid = str(_safe_get_value(lambda: getattr(win.element_info, "process_id", ""), "")).strip()
        if process_id and win_pid and win_pid != process_id:
            continue
        scanned += 1
        win_texts = _collect_option_texts_from_wrapper(win, max_depth=_DROPDOWN_OPTION_WALK_MAX_DEPTH + 3)
        if win_texts and isinstance(diag, dict):
            windows_with_options.append(
                {
                    "className": str(_safe_get_value(lambda: win.class_name(), "")).strip(),
                    "optionCount": len(win_texts),
                }
            )
        for text in win_texts:
            if text not in seen:
                seen.add(text)
                texts.append(text)
    # 兑底：弹出层可能内嵌在主窗口视觉树中（未独立成顶层窗口），按锚点圈定区域采集。
    if not texts and main_top is not None and anchor_rect:
        near_texts = _collect_options_near_anchor(main_top, anchor_rect)
        if near_texts:
            texts = near_texts
            if isinstance(diag, dict):
                diag["popupFromMainWindow"] = True
    if isinstance(diag, dict):
        diag["popupWindowsScanned"] = scanned
        diag["popupWindowsTotal"] = len(windows)
        if windows_with_options:
            diag["popupWindowsWithOptions"] = windows_with_options
    return texts


def _expand_dropdown_and_collect_options(wrapper, backend="uia", diag=None, anchor_rect=None,
                                         move_cursor=True):
    """程序化展开下拉框、采集其可选项文本、再收回。仅在 live UIA 下有效。

    打开策略：ExpandCollapse -> Toggle -> Invoke（兼容 RadComboBox 与 PART_DropDownButton）。
    读取策略：ComboBox.item_texts -> 控件子树 -> 父级子树 -> 顶层 Popup 窗口 -> 命中点扫掠。
    任何异常都被吞掉并返回已采集到的部分，避免影响整体扫描。diag 可传入 dict 记录诊断过程。
    move_cursor=False 时点扫掠只做 ElementFromPoint 命中测试、不物理移动鼠标（目标软件
    对悬停布局敏感/会卡死时由界面开关关闭）。
    """
    if wrapper is None:
        return []
    expandable = _resolve_expandable_wrapper(wrapper)
    # anchor_rect 缺失时（如 PART_DropDownButton 的 boundingBox 为空）从 live wrapper 现取，
    # 否则点扫掠拿不到锚点会整段跳过，虚拟化下拉框的选项永远采不到。
    if not anchor_rect:
        anchor_rect = (
            _rect_to_dict(_safe_get_value(lambda: wrapper.rectangle(), None))
            or _rect_to_dict(_safe_get_value(lambda: expandable.rectangle(), None))
        )
    options = []
    closer = None
    opened = False
    stage = ""
    try:
        opened, closer, strategy = _open_dropdown(wrapper, expandable)
        if isinstance(diag, dict):
            diag["opened"] = bool(opened)
            diag["openStrategy"] = strategy
        if opened:
            time.sleep(_DROPDOWN_EXPAND_WAIT_SECONDS)
        # 1) pywinauto ComboBox 的 item_texts（若可用）。
        item_texts = _safe_get_value(lambda: expandable.item_texts(), None)
        if item_texts:
            options = [
                str(text).strip()
                for text in item_texts
                if str(text).strip() and not _looks_like_type_name(str(text).strip())
            ]
            if options:
                stage = "item_texts"
        # 2) 控件自身子树（项在子树内，如 RadComboBox）。
        if not options:
            options = _collect_option_texts_from_wrapper(expandable)
            if options:
                stage = "control_subtree"
        # 3) 父级子树兑底。
        if not options:
            parent = _safe_get_value(lambda: expandable.parent(), None)
            if parent is not None:
                options = _collect_option_texts_from_wrapper(parent)
                if options:
                    stage = "parent_subtree"
        # 4) 顶层 Popup 窗口（WPF 下拉项常在独立弹出窗口）。
        if not options and opened:
            options = _collect_options_from_popups(wrapper, backend, diag=diag, anchor_rect=anchor_rect)
            if options:
                stage = "popup_window"
        # 5) 命中点扫掠兑底（WPF 虚拟化：选项仅在被命中/悬停时实体化，前述遍历均取不到）。
        # 仅在前述步骤（1-4）均未取到结果时才运行，避免覆盖已成功获取的 options。
        if opened and anchor_rect and not options:
            sweep_options = _realize_options_by_point_sweep(
                anchor_rect, backend, diag=diag, move_cursor=move_cursor
            )
            if sweep_options:
                options = sweep_options
                stage = "point_sweep"
    except Exception as exc:
        if isinstance(diag, dict):
            diag["error"] = str(exc)
    finally:
        if closer is not None:
            _safe_get_value(closer, None)
    result = []
    seen = set()
    for text in options:
        text = str(text).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    if isinstance(diag, dict):
        diag["optionStage"] = stage
        diag["optionCount"] = len(result)
    return result


def _expand_region_dropdowns(flat_controls, region_rect, backend="uia",
                             status_callback=None, move_cursor=True):
    """对画框区域内的下拉框逐个展开采集可选项，写入 optionValues，并返回诊断列表。

    该操作会真实展开/收回界面下拉框，仅在显式开启 expand_dropdowns 开关时调用。
    依赖 flat_controls 中临时保留的 _wrapperRef（live 包装器）。为避免展开远离画框
    区域的无关下拉框（如标签关联可能跨窗口命中），只对自身矩形与画框区域
    实际相交的下拉框执行展开。逐项经 status_callback 上报进度，避免该阶段在
    UI 上长时间无反馈（此前整段静默，最小化时用户看不到任何进展）。
    """
    region_rect = _normalize_rect_dict(region_rect)
    diagnostics = []
    dropdown_items = [
        item for item in flat_controls
        if isinstance(item, dict) and _control_is_dropdown(item)
    ]
    total = len(dropdown_items)
    processed = 0
    with_options = 0
    for item in dropdown_items:
        wrapper = item.get("_wrapperRef")
        item_rect = _normalize_rect_dict(item.get("boundingBox"))
        if not region_rect:
            in_scope = True
        else:
            in_scope = bool(item_rect and _rect_intersects(item_rect, region_rect))
        entry = {
            "identity": str(item.get("automationId", "")).strip() or str(item.get("name", "")).strip(),
            "className": item.get("className", ""),
            "controlType": item.get("controlType", ""),
            "rect": item_rect,
            "inScope": bool(in_scope),
            "hasWrapper": wrapper is not None,
        }
        if not in_scope:
            diagnostics.append(entry)
            continue
        if wrapper is None:
            entry["note"] = "no_wrapper_ref"
            diagnostics.append(entry)
            continue
        processed += 1
        options = _expand_dropdown_and_collect_options(
            wrapper, backend, diag=entry, anchor_rect=item_rect, move_cursor=move_cursor
        )
        if options:
            item["optionValues"] = options
            item["optionCount"] = len(options)
            with_options += 1
            inspect_data = item.get("inspectData")
            if isinstance(inspect_data, dict):
                inspect_data["optionValues"] = options
        # 在采完该项后上报，让浮窗能实时看到"已采到选项的下拉框数"在累积
        # （选项存于 optionValues、不增加控件总数，仅报 (n/total) 会让人误以为没采到）。
        if status_callback and total:
            status_callback(
                f"自动展开下拉框采选项 ({processed}/{total})，已采 {with_options} 组",
                len(flat_controls))
        diagnostics.append(entry)
    return diagnostics



def _control_is_assoc_label(item):
    if not isinstance(item, dict):
        return False
    control_type = str(item.get("controlType", "")).strip().lower()
    if control_type not in _ASSOC_LABEL_TYPES:
        return False
    return bool(str(item.get("name", "")).strip())


def _control_is_assoc_actionable(item):
    if _control_is_dropdown(item):
        return True
    control_type = str(item.get("controlType", "")).strip().lower()
    return control_type in _ASSOC_ACTIONABLE_TYPES


def _vertical_overlap_ratio(rect_a, rect_b):
    rect_a = _normalize_rect_dict(rect_a)
    rect_b = _normalize_rect_dict(rect_b)
    if not rect_a or not rect_b:
        return 0.0
    top = max(rect_a["top"], rect_b["top"])
    bottom = min(rect_a["bottom"], rect_b["bottom"])
    if bottom <= top:
        return 0.0
    min_height = max(1, min(rect_a["bottom"] - rect_a["top"], rect_b["bottom"] - rect_b["top"]))
    return (bottom - top) / float(min_height)


def _horizontal_overlap_ratio(rect_a, rect_b):
    rect_a = _normalize_rect_dict(rect_a)
    rect_b = _normalize_rect_dict(rect_b)
    if not rect_a or not rect_b:
        return 0.0
    left = max(rect_a["left"], rect_b["left"])
    right = min(rect_a["right"], rect_b["right"])
    if right <= left:
        return 0.0
    min_width = max(1, min(rect_a["right"] - rect_a["left"], rect_b["right"] - rect_b["left"]))
    return (right - left) / float(min_width)


def _rect_overlap_ratio(rect_a, rect_b):
    """交集面积 / 较小矩形面积，衡量两矩形重叠程度。"""
    intersection = _intersection_area(rect_a, rect_b)
    if intersection <= 0:
        return 0.0
    min_area = max(1, min(_rect_area(rect_a), _rect_area(rect_b)))
    return intersection / float(min_area)


def _fold_dropdown_value_texts(controls, dropdowns):
    """下拉框自身常无 name，真实显示值在其矩形内的独立 TextBlock 中。

    将该值文本并入对应下拉框（记录 dropdownValueText/value），并标记该文本为
    foldedIntoDropdown，避免它作为孤立"建议忽略"控件干扰入库。
    """
    if not dropdowns:
        return
    for text_item in controls:
        if not isinstance(text_item, dict):
            continue
        control_type = str(text_item.get("controlType", "")).strip().lower()
        if control_type not in _ASSOC_LABEL_TYPES:
            continue
        # 具备独立 automationId 的文本更可能是真实标签，不折叠。
        if str(text_item.get("automationId", "")).strip():
            continue
        text_rect = _normalize_rect_dict(text_item.get("boundingBox"))
        if not text_rect:
            continue
        for dropdown in dropdowns:
            dropdown_rect = _normalize_rect_dict(dropdown.get("boundingBox"))
            if not dropdown_rect or not _rect_contains(text_rect, dropdown_rect):
                continue
            text_item["foldedIntoDropdown"] = True
            text_item["foldedDropdownAutomationId"] = str(dropdown.get("automationId", "")).strip()
            value_text = str(text_item.get("name", "")).strip()
            if value_text:
                dropdown["dropdownValueText"] = value_text
                if not str(dropdown.get("value", "")).strip():
                    dropdown["value"] = value_text
                # 将动态文本复制到下拉框的 inspectData.name 和 inspectData.value，
                # 确保运行时无需定位值 TextBlock 即可读取下拉框当前值。
                inspect_data = dropdown.get("inspectData")
                if isinstance(inspect_data, dict):
                    if not str(inspect_data.get("name", "")).strip():
                        inspect_data["name"] = value_text
                    if not str(inspect_data.get("value", "")).strip():
                        inspect_data["value"] = value_text
            # 将折叠值 TextBlock 的定位器改为基于 className+control_type（而非 name），
            # 因为其 name 是动态显示值（"公共"/"私有"等），会随选中项变化，不能作为定位特征。
            # className+control_type 稳定不变，后续 _disambiguate_duplicate_locators 会
            # 在同父级多个同类 TextBlock 间追加 found_index 消歧。
            class_name = str(text_item.get("className", "")).strip()
            control_type = str(text_item.get("controlType", "")).strip()
            if class_name and control_type:
                text_item["recommendedTargetMethod"] = "class_name,control_type"
                text_item["recommendedTargetValue"] = f"{class_name},{control_type}"
                text_item["locatorScore"] = 68
                text_item["locatorReason"] = "class_name + control_type（折叠值文本，name 为动态值不可靠）"
                # 同步更新 inspectData 中的定位器
                inspect_data = text_item.get("inspectData")
                if isinstance(inspect_data, dict):
                    inspect_data["recommendedTargetMethod"] = "class_name,control_type"
                    inspect_data["recommendedTargetValue"] = f"{class_name},{control_type}"
            break


def _find_same_row_control(label_rect, controls, label, claimed):
    """同行关联：控件位于标签右侧或与其重叠、垂直高度充分重叠、水平间隙小。"""
    label_center_y = (label_rect["top"] + label_rect["bottom"]) / 2.0
    best = None
    best_key = None
    for candidate in controls:
        if candidate is label or id(candidate) in claimed or not _control_is_assoc_actionable(candidate):
            continue
        candidate_rect = _normalize_rect_dict(candidate.get("boundingBox"))
        if not candidate_rect:
            continue
        if _vertical_overlap_ratio(label_rect, candidate_rect) < _SAME_ROW_MIN_VERTICAL_OVERLAP:
            continue
        # 控件应位于标签右侧（下拉框常紧贴标签右缘）；若控件左缘明显落入标签内部，
        # 则属重叠布局，不在同行关联处理。
        horizontal_gap = candidate_rect["left"] - label_rect["right"]
        if horizontal_gap < -_SAME_ROW_LEFT_TOLERANCE or horizontal_gap > _SAME_ROW_MAX_GAP:
            continue
        horizontal_gap = max(0, horizontal_gap)
        candidate_center_y = (candidate_rect["top"] + candidate_rect["bottom"]) / 2.0
        # 排序键：间隙优先，其次下拉框优先，再次垂直中心线接近。
        dropdown_rank = 0 if _control_is_dropdown(candidate) else 1
        key = (horizontal_gap, dropdown_rank, abs(candidate_center_y - label_center_y))
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
    return best, "same-row-label"


def _find_vertical_or_overlap_control(label_rect, controls, label, claimed):
    """纵向/重叠关联：处理标签在控件正上方、或标签矩形与控件大面积重叠的布局。

    优先级：矩形大面积重叠 > 正下方相邻 > 正上方相邻；同类中下拉框优先、间隙更小优先。
    返回 (best_control, relation)。
    """
    best = None
    best_key = None
    best_relation = ""
    for candidate in controls:
        if candidate is label or id(candidate) in claimed or not _control_is_assoc_actionable(candidate):
            continue
        candidate_rect = _normalize_rect_dict(candidate.get("boundingBox"))
        if not candidate_rect:
            continue
        dropdown_rank = 0 if _control_is_dropdown(candidate) else 1
        overlap_ratio = _rect_overlap_ratio(label_rect, candidate_rect)
        if overlap_ratio >= _MIN_RECT_OVERLAP_RATIO:
            key = (0, dropdown_rank, -overlap_ratio)
            relation = "overlap-label"
        elif _horizontal_overlap_ratio(label_rect, candidate_rect) >= _VERTICAL_MIN_HORIZONTAL_OVERLAP:
            gap_below = candidate_rect["top"] - label_rect["bottom"]
            gap_above = label_rect["top"] - candidate_rect["bottom"]
            if 0 <= gap_below <= _VERTICAL_MAX_GAP:
                key = (1, dropdown_rank, gap_below)
                relation = "vertical-label"
            elif 0 <= gap_above <= _VERTICAL_MAX_GAP:
                key = (2, dropdown_rank, gap_above)
                relation = "vertical-label"
            else:
                continue
        else:
            continue
        if best_key is None or key < best_key:
            best_key = key
            best = candidate
            best_relation = relation
    return best, best_relation


def _associate_region_labels_with_controls(flat_controls):
    """在 dict 级对采集结果做"标签→实际控件"关联，独立于实时探测路径。

    分两轮：
    1. 同行关联（最强）：标签右侧、同行、间隙小的可操作控件（下拉框优先）。
    2. 纵向/重叠关联：对未命中同行的标签，再尝试正上/下方相邻或矩形大面积重叠的控件。

    已被关联的控件不会被其他标签重复抢占；关联后标记 regionRelated/relatedLabelName，
    使其即便部分落在画框外也会保留。另外折叠下拉框内部显示值文本、标记下拉框可选项。
    """
    if not flat_controls:
        return flat_controls
    controls = [item for item in flat_controls if isinstance(item, dict)]
    dropdowns = [item for item in controls if _control_is_dropdown(item)]
    labels = []
    for label in controls:
        if not _control_is_assoc_label(label):
            continue
        label_rect = _normalize_rect_dict(label.get("boundingBox"))
        if label_rect:
            labels.append((label, label_rect))

    claimed = set()

    def _bind(label, control, relation):
        control["regionRelated"] = True
        if not str(control.get("regionRelation", "")).strip():
            control["regionRelation"] = relation
        control["relatedLabelName"] = str(label.get("name", "")).strip()
        claimed.add(id(control))

    # 第一轮：同行关联（最强、最可靠）。
    pending = []
    for label, label_rect in labels:
        best, relation = _find_same_row_control(label_rect, controls, label, claimed)
        if best is not None:
            _bind(label, best, relation)
        else:
            pending.append((label, label_rect))

    # 第二轮：纵向/重叠关联（仅处理未命中同行的标签，避开已被占用的控件）。
    still_pending = []
    for label, label_rect in pending:
        best, relation = _find_vertical_or_overlap_control(label_rect, controls, label, claimed)
        if best is not None:
            _bind(label, best, relation)
        else:
            still_pending.append((label, label_rect))

    # 第三轮：宽松伴随关联。候选放宽到伴随输入型（含容器型自定义输入控件），
    # 同行右侧间隙放宽到 420px；中间隔着其他标签时不绑（双栏表单防误绑）。
    for label, label_rect in still_pending:
        best, relation = _find_loose_companion_control(label_rect, controls, label, claimed, labels)
        if best is not None:
            _bind(label, best, relation)

    _fold_dropdown_value_texts(controls, dropdowns)
    for item in controls:
        if _control_is_dropdown_option(item):
            item["dropdownOption"] = True
    return flat_controls


def _filter_flat_controls_by_region(flat_controls, region_rect):
    region_rect = _normalize_rect_dict(region_rect)
    if not region_rect:
        return list(flat_controls)
    ranked = []
    for item in flat_controls:
        item_rect = _normalize_rect_dict(item.get("boundingBox"))
        related = bool(item.get("regionRelated"))
        if not item_rect:
            continue
        if related:
            # 祖级横向关联控件可能在画框外，但它是画框标签对应的实际输入/下拉控件。
            score = int(item.get("locatorScore", 0) or 0) * 1000
            score += 500 - int(item.get("depth", 0) or 0) * 10
            ranked.append((score, item))
            continue
        if not _rect_intersects(item_rect, region_rect):
            continue
        score = _score_region_candidate(item, region_rect)
        if score is None:
            continue
        ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _score, item in ranked]


def _is_low_value_root_candidate(item, target_window_title):
    if not isinstance(item, dict):
        return False
    if int(item.get("depth", 0) or 0) != 0:
        return False
    control_type = str(item.get("controlType", "")).strip().lower()
    if control_type not in {"", "window"}:
        return False
    name = str(item.get("name", "")).strip()
    return name == str(target_window_title or "").strip()


def _prune_low_value_region_controls(region_controls, target_window_title):
    region_controls = list(region_controls or [])
    if len(region_controls) <= 1:
        return region_controls
    deeper_exists = any(int(item.get("depth", 0) or 0) > 0 for item in region_controls)
    if not deeper_exists:
        return region_controls
    pruned = [item for item in region_controls if not _is_low_value_root_candidate(item, target_window_title)]
    return pruned or region_controls


# 可交互 Control Pattern 集合：命中其一即认为控件具备操作价值，
# 即使无 name/automationId 也不得被无标识容器规则误丢（自定义输入框/图形按钮常如此）。
_ACTIONABLE_PATTERNS = {
    "value", "text", "invoke", "toggle", "selectionitem", "expandcollapse",
    "rangevalue", "scrollitem", "selection", "grid", "griditem", "table", "tableitem",
}


def _item_has_actionable_pattern(item):
    """控件是否具备可交互能力（支持操作 Pattern 或可键盘聚焦）。"""
    if not isinstance(item, dict):
        return False
    patterns = {str(p).strip().lower() for p in (item.get("supportedPatterns") or []) if str(p).strip()}
    if patterns & _ACTIONABLE_PATTERNS:
        return True
    inspect = item.get("inspectData") if isinstance(item.get("inspectData"), dict) else {}
    focusable = str(item.get("isKeyboardFocusable", inspect.get("isKeyboardFocusable", ""))).strip().lower()
    return focusable == "true"


def _filter_noise_controls(controls, exclude_offscreen=True, exclude_unidentified_containers=True):
    """A+C: 过滤离屏控件和无标识容器，减少无关信息。

    特殊处理：ComboBox 的子级（下拉选项）即使 isOffscreen=True 也保留，
    因为它们是下拉框未展开时不可见的选项。
    """
    if not controls:
        return controls
    # 找出所有 ComboBox 的 runtimeId，用于判断子级是否属于下拉框
    combobox_runtime_ids = set()
    for item in controls:
        if str(item.get("controlType", "")).strip().lower() == "combobox":
            rid = str(item.get("runtimeId", "")).strip()
            if rid:
                combobox_runtime_ids.add(rid)

    filtered = []
    for item in controls:
        if not isinstance(item, dict):
            continue
        control_type = str(item.get("controlType", "")).strip().lower()
        # A: 过滤离屏控件（IsOffscreen=True），但保留 ComboBox 的子级
        if exclude_offscreen and str(item.get("isOffscreen", "")).strip().lower() == "true":
            parent_path = str(item.get("parentPath", "")).strip()
            parent_runtime = str(item.get("inspectData", {}).get("ancestors", [])[-1] if item.get("inspectData", {}).get("ancestors") else "").strip() if isinstance(item.get("inspectData"), dict) else ""
            # 如果控件类型是 ListItem/ListItem 且属于 ComboBox，则保留
            if control_type in {"listitem", "list"} and parent_path:
                continue  # ListItem 可能是 ComboBox 的下拉选项，即使离屏也保留
            continue
        # C: 过滤无 name 且无 automationId 的容器（Custom/Pane/Group）；
        # 但具备可交互 Pattern 或可聚焦的容器一律放行——它们通常是自定义输入框/
        # 图形按钮（如 WT 的 MUP 输入控件），误丢会导致自动化无法定位输入。
        if exclude_unidentified_containers:
            has_name = bool(str(item.get("name", "")).strip())
            has_automation_id = bool(str(item.get("automationId", "")).strip())
            has_label_text = bool(str(item.get("labelText", "")).strip())
            if control_type in {"custom", "pane", "group"} and not has_name and not has_automation_id and not has_label_text:
                # 标签伴随/关联控件豁免：已确认是某标签对应的实际输入控件
                if item.get("regionRelated") or str(item.get("relatedLabelName", "")).strip():
                    filtered.append(item)
                    continue
                if _item_has_actionable_pattern(item):
                    filtered.append(item)
                continue
        filtered.append(item)
    return filtered


# 输入框类名特征（子串匹配，覆盖 TextBoxEx/WatermarkTextBox 等 WPF 自定义变体）
_INPUT_CLASS_NAME_HINTS = (
    "textbox", "passwordbox", "richtextbox", "texteditor", "spinedit",
    "watermarktextbox", "maskedtextbox", "numericupdown", "comboboxedit",
)


def _find_textbox_ancestor(item, by_index, max_depth=12):
    """沿 parentIndex 祖先链向上找 className 含 textbox 的父级 TextBox（多层）。

    PART_ContentHost 常隔着 ScrollViewer 等中间层才到 TextBox，不能只看直接父级。
    返回 (textbox_item, parent_index) 或 (None, None)。
    """
    parent_idx = item.get("parentIndex")
    if parent_idx is None:
        return None, None
    current_idx = parent_idx
    for _ in range(max_depth):
        parent = by_index.get(current_idx)
        if parent is None:
            return None, None
        parent_class = str(parent.get("className", "")).strip().lower()
        if any(hint in parent_class for hint in ("textbox", "passwordbox", "richtextbox")):
            return parent, current_idx
        next_idx = parent.get("parentIndex")
        if next_idx is None or next_idx == current_idx:
            return None, None
        current_idx = next_idx
    return None, None


def _find_overlapping_textbox(item, flat_controls):
    """按位置重叠匹配同位置的 TextBox 控件（祖先链找不到父级 TextBox 时的兜底）。

    同一输入框的 PART_ContentHost 与父级 TextBox 位置几乎完全重合，
    据此将二者关联为一个输入框。返回 (textbox_item, textbox_index) 或 (None, None)。
    """
    item_rect = _normalize_rect_dict(item.get("boundingBox"))
    if not item_rect:
        return None, None
    for index, other in enumerate(flat_controls):
        if other is item or not isinstance(other, dict):
            continue
        if str(other.get("automationId", "")).strip() == "PART_ContentHost":
            continue
        other_class = str(other.get("className", "")).strip().lower()
        if not any(hint in other_class for hint in ("textbox", "passwordbox", "richtextbox")):
            continue
        other_rect = _normalize_rect_dict(other.get("boundingBox"))
        if not other_rect or not _rect_intersects(item_rect, other_rect):
            continue
        return other, index
    return None, None


def _normalize_textbox_wrappers(flat_controls):
    """识别 WPF TextBox/PasswordBox 控件并修正其 controlType 为 Edit。

    WPF 的 TextBox UIA 控件可能报告 controlType="Custom"（而非 "Edit"），
    导致 _filter_noise_controls（过滤无标识容器）将其丢弃，且 _backfill_label_text_to_controls
    （标签关联回填）因只处理 edit/combobox/spinner/document 而跳过它。

    此外 WPF TextBox 内部包含 PART_ContentHost（ScrollViewer）作为编辑区域，
    该子控件设了 IsContentElement=False / IsControlElement=False，
    pywinauto 默认树 walker（ContentViewWalker）不会返回它。
    在 from_point 探针命中 PART_ContentHost 时，沿祖先链向上找到父级 TextBox 并标记。

    策略：
    1. className 含 "TextBox"/"PasswordBox" 且 controlType 为 Custom/Pane → 改为 Edit
    2. automationId="PART_ContentHost" → 向上找到父级 TextBox 并标记父级，将自身折叠
    """
    if not flat_controls:
        return

    # 建立索引以支持 parentIndex 向上查找
    by_index = {}
    for idx, item in enumerate(flat_controls):
        if isinstance(item, dict):
            by_index[idx] = item

    for idx, item in enumerate(flat_controls):
        if not isinstance(item, dict):
            continue
        class_name = str(item.get("className", "")).strip().lower()
        control_type = str(item.get("controlType", "")).strip().lower()
        automation_id = str(item.get("automationId", "")).strip()

        # 情况 1：输入框类名命中（子串匹配，覆盖 TextBoxEx/WatermarkTextBox 等自定义变体）
        if any(hint in class_name for hint in _INPUT_CLASS_NAME_HINTS):
            if control_type in ("custom", "pane", "group", "document"):
                item["controlType"] = "Edit"
                item["controlTypeSource"] = "normalized-from-classname"
                # 同步更新 inspectData
                inspect = item.get("inspectData")
                if isinstance(inspect, dict):
                    inspect["controlType"] = "Edit"

        # 情况 2：PART_ContentHost — WPF TextBox 内部 ScrollViewer，
        # IsContentElement=False，UIA tree walker 可跳过；from_point 可命中。
        # 要向上找到父级 TextBox 并交换身份。
        if automation_id == "PART_ContentHost" and not item.get("foldedIntoParent"):
            # 沿祖先链向上找父级 TextBox（可能隔着 ScrollViewer 等中间层）；
            # 祖先链找不到时，按位置重叠匹配同位置的 TextBox 控件。
            # 合并后 PART_ContentHost 的 controlType 可能已是 Edit（被前置规范化/提升），
            # 不再限定 pane/custom，只要 aid 是 PART_ContentHost 且未折叠就尝试折叠。
            textbox_parent, parent_idx = _find_textbox_ancestor(item, by_index)
            if textbox_parent is None:
                textbox_parent, parent_idx = _find_overlapping_textbox(item, flat_controls)
            if textbox_parent is not None:
                # 父级是真正的 TextBox，规范化其 controlType
                if str(textbox_parent.get("controlType", "")).strip().lower() in ("custom", "pane", "group"):
                    textbox_parent["controlType"] = "Edit"
                    textbox_parent["controlTypeSource"] = "normalized-from-contenthost"
                    inspect_p = textbox_parent.get("inspectData")
                    if isinstance(inspect_p, dict):
                        inspect_p["controlType"] = "Edit"
                # 折叠 PART_ContentHost 自身 — 它不应作为独立控件被定位/操作
                item["foldedIntoParent"] = True
                item["qualityTier"] = "建议忽略"
                item["qualityReason"] = ("PART_ContentHost: WPF TextBox 内部编辑区域，"
                                          "非独立可定位控件，已由父级 TextBox 替代")
                item["foldedTargetIndex"] = parent_idx
            else:
                # 孤儿 PART_ContentHost：UIA 树中不存在父级 TextBox（MTD 的"名称/描述"
                # 等自定义输入控件即此形态——ScrollViewer 直接挂在视图容器下，
                # IsControlElement=False）。该 ScrollViewer 就是实际可编辑面，
                # 若按上支折叠，此类输入框将在库中彻底缺失；提升为 Edit 使其进入
                # 标签关联/回填/定位管道，可由邻近标签获得稳定定位。
                item["controlType"] = "Edit"
                item["controlTypeSource"] = "normalized-from-contenthost-orphan"
                item["qualityTier"] = "推断输入框"
                item["qualityReason"] = ("PART_ContentHost: 未找到父级 TextBox，"
                                          "按实际编辑区域提升为输入框")
                inspect = item.get("inspectData")
                if isinstance(inspect, dict):
                    inspect["controlType"] = "Edit"

        # 情况 3：支持 Value/Text Pattern 的无标识容器——几乎必是可读写文本的
        # 自定义输入控件（WT 的 MUP 输入框即此类），规范化为 Edit 以进入标签关联与定位管道
        if str(item.get("controlType", "")).strip().lower() in ("custom", "pane", "group"):
            patterns = {str(p).strip().lower() for p in (item.get("supportedPatterns") or []) if str(p).strip()}
            if "value" in patterns or "text" in patterns:
                item["controlType"] = "Edit"
                item["controlTypeSource"] = "normalized-from-valuepattern"
                inspect = item.get("inspectData")
                if isinstance(inspect, dict):
                    inspect["controlType"] = "Edit"


def _backfill_sibling_label_for_inputs(flat_controls):
    """为 PART_ContentHost 及普通 Edit/TextBox 回填同父容器内前邻 TextBlock 的 name。

    设计原因：
    _backfill_label_text_to_controls 基于全局几何最近邻匹配标签，但在某些 WPF 布局中
    （如 MTD 的自定义输入控件），标签与输入框虽在同一父容器下，几何距离可能因布局偏移
    而超出阈值。本函数利用 UIA 树的父子/兄弟关系做精确的同容器内标签查找，作为
    _backfill_label_text_to_controls 的前置补充。

    策略：
    1. 对 labelText 为空的 PART_ContentHost（含已折叠/已提升的）和普通 Edit 控件
    2. 通过 parentIndex 找到同父容器下的所有子控件（按 index 顺序即 UI 顺序）
    3. 先向前查找最近的 TextBlock（controlType=text/textblock, className=TextBlock）
    4. 若向前未找到，也向后查找（部分布局标签在输入框右侧/下方）
    5. 对候选标签做几何验证（同行左侧 或 同列上方），通过后才回填
    """
    if not flat_controls:
        return

    # 按 parentIndex 分组子控件，保持原始 index 顺序（即 UI 顺序）
    children_by_parent = {}  # parentIndex -> [(child_index, child_item), ...]
    for idx, item in enumerate(flat_controls):
        if not isinstance(item, dict):
            continue
        pidx = item.get("parentIndex")
        if pidx is None or pidx < 0:
            continue
        children_by_parent.setdefault(pidx, []).append((idx, item))

    for idx, item in enumerate(flat_controls):
        if not isinstance(item, dict):
            continue

        # 只处理 labelText 为空的控件
        if str(item.get("labelText", "")).strip():
            continue

        automation_id = str(item.get("automationId", "")).strip()
        control_type = str(item.get("controlType", "")).strip().lower()
        class_name = str(item.get("className", "")).strip().lower()

        # 目标控件：PART_ContentHost（含折叠/提升的）或普通 Edit/TextBox
        is_target = False
        if automation_id == "PART_ContentHost":
            is_target = True
        elif control_type == "edit" or "textbox" in class_name:
            is_target = True
        if not is_target:
            continue

        parent_idx = item.get("parentIndex")
        if parent_idx is None or parent_idx < 0:
            continue
        siblings = children_by_parent.get(parent_idx)
        if not siblings:
            continue

        item_rect = _normalize_rect_dict(item.get("boundingBox"))

        # 在兄弟列表中查找 TextBlock 标签
        # 先向前（index 更小的兄弟），再向后（index 更大的兄弟）
        found_label_name = ""
        found_label_relation = ""

        my_pos_in_siblings = None
        for si, (s_idx, _) in enumerate(siblings):
            if s_idx == idx:
                my_pos_in_siblings = si
                break
        if my_pos_in_siblings is None:
            continue

        # 向前查找（UI 顺序中在目标之前的兄弟）
        for si in range(my_pos_in_siblings - 1, -1, -1):
            s_idx, s_item = siblings[si]
            s_ct = str(s_item.get("controlType", "")).strip().lower()
            s_cn = str(s_item.get("className", "")).strip().lower()
            if s_ct in ("text", "textblock") or s_cn == "textblock":
                s_name = str(s_item.get("name", "")).strip()
                if s_name and not _is_garbage_name(s_name):
                    # 几何验证（如果有 boundingBox）
                    if item_rect:
                        s_rect = _normalize_rect_dict(s_item.get("boundingBox"))
                        if s_rect and _verify_label_geometry(s_rect, item_rect):
                            found_label_name = s_name
                            found_label_relation = "sibling-textblock"
                            break
                        # 几何验证不通过，继续找
                        continue
                    # 无 boundingBox 时不做几何验证，直接采用
                    found_label_name = s_name
                    found_label_relation = "sibling-textblock"
                    break

        # 向前没找到，向后查找
        if not found_label_name:
            for si in range(my_pos_in_siblings + 1, len(siblings)):
                s_idx, s_item = siblings[si]
                s_ct = str(s_item.get("controlType", "")).strip().lower()
                s_cn = str(s_item.get("className", "")).strip().lower()
                if s_ct in ("text", "textblock") or s_cn == "textblock":
                    s_name = str(s_item.get("name", "")).strip()
                    if s_name and not _is_garbage_name(s_name):
                        if item_rect:
                            s_rect = _normalize_rect_dict(s_item.get("boundingBox"))
                            if s_rect and _verify_label_geometry(s_rect, item_rect):
                                found_label_name = s_name
                                found_label_relation = "sibling-textblock-following"
                                break
                            continue
                        found_label_name = s_name
                        found_label_relation = "sibling-textblock-following"
                        break

        if found_label_name:
            item["labelText"] = found_label_name
            if not str(item.get("labelRelation", "")).strip():
                item["labelRelation"] = found_label_relation
            # 如果控件没有 name，也用标签文本回填（与 _backfill_label_text_to_controls 一致）
            if not str(item.get("name", "")).strip():
                item["name"] = found_label_name
                item["nameSource"] = "sibling-label-backfill"
            # 对折叠的 PART_ContentHost，将 labelText 也传递给折叠目标（父级 TextBox）
            folded_target_idx = item.get("foldedTargetIndex")
            if folded_target_idx is not None and 0 <= folded_target_idx < len(flat_controls):
                target = flat_controls[folded_target_idx]
                if isinstance(target, dict) and not str(target.get("labelText", "")).strip():
                    target["labelText"] = found_label_name
                    if not str(target.get("labelRelation", "")).strip():
                        target["labelRelation"] = found_label_relation


def _verify_label_geometry(label_rect, content_rect):
    """验证标签与输入控件的几何关系是否合理（同行左侧 或 同列上方）。

    同行：标签在输入框左侧，垂直中心接近（差距 < 30px）
    同列：标签在输入框上方，水平起始接近（差距 < 30px）
    """
    if not label_rect or not content_rect:
        return False
    # 同行：标签右边界在输入框左边界之前（或重叠不多），垂直接近
    same_row = (
        abs(label_rect.get("top", 0) - content_rect.get("top", 0)) < 30
        and label_rect.get("right", 0) <= content_rect.get("left", 0) + 20
    )
    # 同列：标签在输入框上方，水平起始接近
    same_col = (
        abs(label_rect.get("left", 0) - content_rect.get("left", 0)) < 30
        and label_rect.get("bottom", 0) <= content_rect.get("top", 0) + 5
    )
    return same_row or same_col


def _backfill_label_text_to_controls(flat_controls):
    """标签关联回填：对弱定位的 Edit/ComboBox 用邻近标签文本增强。

    策略：
    1. 收集所有 Text 标签控件及其矩形
    2. 对没有 name 和 automationId 的 Edit/ComboBox，找最近标签
    3. 把标签文本写入 labelText 字段
    4. 如果控件本身没有 name，用标签文本作为 name 回填
    5. 重新计算定位评分
    """
    if not flat_controls:
        return

    # 收集标签控件
    labels = []
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        control_type = str(item.get("controlType", "")).strip().lower()
        if control_type in {"text", "label", "static", "textblock"}:
            name = str(item.get("name", "")).strip()
            rect = _normalize_rect_dict(item.get("boundingBox"))
            if name and rect:
                labels.append({"name": name, "rect": rect, "path": str(item.get("uiPath", "")).strip()})

    # 对弱定位控件回填
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        control_type = str(item.get("controlType", "")).strip().lower()

        has_name = bool(str(item.get("name", "")).strip())
        has_automation_id = bool(str(item.get("automationId", "")).strip())

        # 最优先：标签伴随/关联阶段已确认的关联标签（同区域几何验证过，可信度最高）。
        # 该路径不限控件类型（容器型自定义输入控件也可回填），故放在类型过滤之前；
        # labelText 无条件补写——即使控件已有强定位，labelText 也供消歧与界面展示使用，
        # 但强定位控件的 name 与推荐定位器保持不变。
        related_label_name = str(item.get("relatedLabelName", "")).strip()
        if related_label_name:
            item["labelText"] = related_label_name
            if not str(item.get("labelRelation", "")).strip():
                item["labelRelation"] = "region-association"
            if has_name and has_automation_id:
                continue  # 强定位：仅补 labelText，不改动 name/推荐定位器
            if not has_name:
                item["name"] = related_label_name
                item["nameSource"] = "relatedlabel-backfill"
            _rescore_backfilled_control(item)
            continue

        # 强定位控件（同时有 name 和 automationId）：仍执行 labelText 回填（供运行时消歧），
        # 但不改动 name 与推荐定位器。用 is_strong_locator 标记控制下游行为。
        is_strong_locator = has_name and has_automation_id

        if control_type not in {"edit", "combobox", "spinner", "document", "splitbutton", "checkbox", "radiobutton", "slider"}:
            continue

        # 优先使用 UIA LabeledBy（WPF Label.Target 权威关联，精度高于几何最近邻）；
        # 该路径不依赖窗口内存在 Text 标签，故放在 labels 判空之外
        labeled_by_name = str(item.get("labeledByName", "")).strip()
        if labeled_by_name:
            item["labelText"] = labeled_by_name
            item["labelRelation"] = "uia-labeledby"
            if not is_strong_locator and not has_name:
                item["name"] = labeled_by_name
                item["nameSource"] = "labeledby-backfill"
            if not is_strong_locator:
                _rescore_backfilled_control(item)
            continue

        if not labels:
            continue  # 无几何候选标签可关联

        item_rect = _normalize_rect_dict(item.get("boundingBox"))
        if not item_rect:
            continue

        # 找最近的标签
        best_label = None
        best_score = float("inf")
        for label in labels:
            label_rect = label["rect"]
            # 计算几何距离
            vertical_gap = max(0, max(item_rect["top"], label_rect["top"]) - min(item_rect["bottom"], label_rect["bottom"]))
            horizontal_gap = max(0, max(item_rect["left"], label_rect["left"]) - min(item_rect["right"], label_rect["right"]))

            # 同行优先
            if vertical_gap > 60:
                continue
            # 标签在左侧优先
            direction_penalty = 0 if label_rect["right"] <= item_rect["left"] + 20 else 30
            score = horizontal_gap + vertical_gap * 2 + direction_penalty
            if score < best_score:
                best_score = score
                best_label = label

        if best_label and best_score < 200:
            label_text = best_label["name"]
            item["labelText"] = label_text
            item["labelRelation"] = "nearest-text-label"
            # 如果控件没有 name，用标签文本回填
            if not is_strong_locator and not has_name:
                item["name"] = label_text
                item["nameSource"] = "label-backfill"
            if not is_strong_locator:
                _rescore_backfilled_control(item)


def _reconcile_label_with_name(flat_controls):
    """标签一致化：弱定位输入框若 labelText 与自身 name 矛盾（多为向后兜底把
    "下一个字段的标签"误连进来，例如描述编辑框被误连为"服务"），以自身 name 为准
    回填 labelText，避免 name/label 自相矛盾导致运行时按 label 消歧时错配。"""
    if not flat_controls:
        return
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        label = str(item.get("labelText", "")).strip()
        if not name or label == name:
            continue
        relation = str(item.get("labelRelation", "")).strip()
        if "sibling" in relation or "following" in relation:
            item["labelText"] = name
            item["labelRelation"] = "name-consistent"
            related = str(item.get("relatedLabelName", "")).strip()
            if related and related != name:
                item["relatedLabelName"] = name


def _rescore_backfilled_control(item):
    """标签回填后的定位器重算与质量重分级。

    回填的 name（nameSource 以 -backfill 结尾）不是真实 UIA Name，运行时 name
    定位必然失配；重算定位时将其剔除，让 label_text 等真实可匹配的方法胜出。
    """
    has_automation_id = bool(str(item.get("automationId", "")).strip())
    if not has_automation_id:
        locator_source = item
        if str(item.get("nameSource", "")).strip().endswith("-backfill"):
            locator_source = dict(item)
            locator_source["name"] = ""
        method, value, score, reason = build_locator_recommendation(
            locator_source, int(item.get("index", 0) or 0), item.get("uiPath", ""))
        if score > int(item.get("locatorScore", 0) or 0):
            item["recommendedTargetMethod"] = method
            item["recommendedTargetValue"] = value
            item["locatorScore"] = score
            item["locatorReason"] = reason
    quality_tier, quality_reason = _classify_control_quality(item)
    item["qualityTier"] = quality_tier
    item["qualityReason"] = quality_reason


def _locator_scope_method(target_method):
    """从复合 targetMethod 中取可作为 found_index 范围锚的方法（control_type/class_name/name）。"""
    for part in str(target_method or "").split(","):
        part = part.strip()
        if part in ("control_type", "class_name", "name"):
            return part
    return ""


def _sibling_scope_value(item, scope_method):
    if scope_method == "class_name":
        return str(item.get("className", "")).strip()
    if scope_method == "name":
        return str(item.get("name", "")).strip()
    return str(item.get("controlType", "")).strip()


def _compute_sibling_found_index(item, flat_controls, scope_method):
    """预测该控件在运行时 get_wrapper_found_index 的结果：同父容器直接子节点中、与
    scope_method 同类的兄弟里的 0 基序号。基于扫描期的 parentIndex/siblingsIndex 计算，
    与运行时按父链引导的兄弟计数保持一致。找不到返回 -1。"""
    parent_index = item.get("parentIndex", None)
    if parent_index is None:
        return -1
    target_scope = _sibling_scope_value(item, scope_method)
    if not target_scope:
        return -1
    siblings = [
        other
        for other in flat_controls
        if isinstance(other, dict)
        and other.get("parentIndex", None) == parent_index
        and _sibling_scope_value(other, scope_method) == target_scope
    ]
    siblings.sort(key=lambda x: int(x.get("siblingsIndex", 0) or 0))
    for position, sibling in enumerate(siblings):
        if sibling is item:
            return position
    return -1


def _locator_identity(item):
    """用于区分"真正不同的控件"与"同一控件的多次出现"：优先 runtimeId，其次 rect。"""
    runtime_id = str(item.get("runtimeId", "")).strip()
    if runtime_id:
        return runtime_id
    rect = _normalize_rect_dict(item.get("boundingBox"))
    if rect:
        return f"{rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
    return str(id(item))


def _extract_panel_title(item, flat_controls):
    """提取控件所在面板容器的标题 Text（如 interest-area 面板的"测风点"/"结果点"）。

    模板复制控件（同 automationId/name/uiPath，只有位置不同）之间唯一稳定的
    语义差异是所在面板标题，用作 label_text 消歧与运行时定位键。
    提取失败返回空串。
    """
    parent_index = item.get("parentIndex", None)
    if parent_index is None or not isinstance(flat_controls, list):
        return ""
    if not (0 <= parent_index < len(flat_controls)):
        return ""
    # 权威来源：interest-area 面板标题以 automationId=InterestAreasView_Tile_Header
    # 的 TileHeader Text 兄弟为准（其 name 即"测风点/风机/结果点/绘图/配置/风廓线/
    # Lidar/中尺度单元"）。Edit/Delete/Import 同父容器还共享其它字段标签兄弟
    # （"载入"/"计算尾流效应"/"类型"/"高度 (m)"等），这些按 flat_controls 顺序出现在
    # 前面，若按"首个短文本"取值会取到错误标签。故优先返回该兄弟的 name。
    for sibling in flat_controls:
        if sibling is item or sibling.get("parentIndex", None) != parent_index:
            continue
        if str(sibling.get("automationId", "")).strip() == "InterestAreasView_Tile_Header":
            text = str(sibling.get("name", "") or "").strip()
            if text and "," not in text[:30] and len(text) <= 30:
                return text
            break
    # 兜底：无 TileHeader 兄弟时，仍取首个非 SVG 短文本兄弟（兼容非 interest-area 面板）。
    for sibling in flat_controls:
        if sibling is item or sibling.get("parentIndex", None) != parent_index:
            continue
        if sibling.get("controlType", "") not in ("Text", "TextBlock", "Static", "Label"):
            continue
        text = str(sibling.get("name", "") or "").strip()
        # 跳过 SVG path（"M3,17.25L3,21..."）等非标题长文本
        if not text or "," in text[:30] or len(text) > 30:
            continue
        return text
    return ""


def _disambiguate_duplicate_locators(flat_controls):
    """全局唯一性后处理：多个不同控件若共用同一 recommendedTargetValue（如
    "访问级别下拉框"和"性质下拉框"两个 PART_DropDownButton 都得到
    "PART_DropDownButton,Button"），运行时会双双定位到同一个控件。此处对这类冲突
    控件追加 found_index 消歧（同父容器内按 control_type/class_name/name 计数），
    使各自定位唯一，并避免它们在分组去重中被误并为一个。
    """
    groups = {}
    for item in flat_controls:
        if not isinstance(item, dict):
            continue
        method = str(item.get("recommendedTargetMethod", "")).strip()
        value = str(item.get("recommendedTargetValue", "")).strip()
        if not method or not value or "found_index" in method:
            continue
        groups.setdefault((method, value), []).append(item)
    for (method, value), members in groups.items():
        # 仅对"真正不同的控件"消歧（不同 runtimeId/rect），跨后端同一控件不处理。
        distinct = []
        seen_identity = set()
        for member in members:
            identity = _locator_identity(member)
            if identity in seen_identity:
                continue
            seen_identity.add(identity)
            distinct.append(member)
        if len(distinct) < 2:
            continue
        # 优先 name 消歧：组内成员 name（按钮文本/控件名）各不相同时，用 name 消歧。
        # name 匹配（get_wrapper_text）比 label_text 快——label_text 匹配要遍历窗口找标签。
        name_members = {}
        for member in distinct:
            nm = str(member.get("name", "") or member.get("suggestedControlName", "") or member.get("displayName", "")).strip()
            if not nm or "," in nm or nm in name_members:
                name_members = {}
                break
            name_members[nm] = member
        if len(name_members) >= 2:
            for nm, member in name_members.items():
                member["recommendedTargetMethod"] = method + ",name"
                member["recommendedTargetValue"] = value + "," + nm
                reason = str(member.get("locatorReason", "")).strip()
                member["locatorReason"] = (reason + " + name消歧").strip(" +")
            continue
        # 其次 label_text 消歧：组内成员各有互不相同的关联标签文本（标签伴随/
        # 区域关联产物）时，用标签文本消歧比 found_index 更抗布局与顺序变动。
        label_text_members = {}
        for member in distinct:
            lt = str(member.get("labelText", "") or member.get("relatedLabelName", "")).strip()
            if not lt or "," in lt or lt in label_text_members:
                label_text_members = {}
                break
            label_text_members[lt] = member
        if len(label_text_members) >= 2:
            for lt, member in label_text_members.items():
                member["recommendedTargetMethod"] = method + ",label_text"
                member["recommendedTargetValue"] = value + "," + lt
                reason = str(member.get("locatorReason", "")).strip()
                member["locatorReason"] = (reason + " + label_text消歧").strip(" +")
            continue
        # 面板标题消歧：模板复制控件（同 automationId/name/uiPath，如各 interest-area
        # 面板的图标按钮）无法用 name/label_text/found_index 区分时，其所在面板标题
        # Text（"测风点"/"结果点"等）互不相同，用面板标题作 label_text 消歧。
        # 运行时 wrapper_matches_label_text 按父容器内兄弟 Text 匹配。
        panel_title_members = {}
        for member in distinct:
            pt = _extract_panel_title(member, flat_controls)
            if not pt or "," in pt or pt in panel_title_members:
                panel_title_members = {}
                break
            panel_title_members[pt] = member
        if len(panel_title_members) >= 2:
            for pt, member in panel_title_members.items():
                member["labelText"] = pt
                member["recommendedTargetMethod"] = method + ",label_text"
                member["recommendedTargetValue"] = value + "," + pt
                reason = str(member.get("locatorReason", "")).strip()
                member["locatorReason"] = (reason + " + 面板标题消歧").strip(" +")
            continue
        scope_method = _locator_scope_method(method)
        if not scope_method:
            continue
        assigned = {}
        for member in distinct:
            found_index = _compute_sibling_found_index(member, flat_controls, scope_method)
            if found_index < 0 or found_index in assigned:
                continue
            assigned[found_index] = member
        # 只有当消歧确实给出彼此不同的序号时才落地，否则保持原样避免误伤。
        if len(assigned) < 2:
            continue
        for found_index, member in assigned.items():
            member["recommendedTargetMethod"] = method + ",found_index"
            member["recommendedTargetValue"] = value + "," + str(found_index)
            reason = str(member.get("locatorReason", "")).strip()
            member["locatorReason"] = (reason + " + found_index消歧").strip(" +")
    # 功能名消歧增强：对已消歧（name/label_text/found_index）成员追加 help_text 分量。
    # helpText 是控件自身 UIA 属性，运行时直接从属性读取比对，比父容器兄弟 Text 查找
    # 更抗树结构变化，与面板标题（label_text）组合成双保险精确定位器。
    # functionText 为空或含逗号（破坏 locator 逗号分隔）时跳过；已有 help_text 不重复追加。
    disambiguated_parts = {"name", "label_text", "found_index"}
    for member in flat_controls:
        if not isinstance(member, dict):
            continue
        member_method = str(member.get("recommendedTargetMethod", "")).strip()
        method_parts = {part.strip() for part in member_method.split(",")}
        if "help_text" in method_parts or not (method_parts & disambiguated_parts):
            continue
        function_text = str(member.get("functionText", "")).strip()
        if not function_text or "," in function_text:
            continue
        member["recommendedTargetMethod"] = member_method + ",help_text"
        member["recommendedTargetValue"] = str(member.get("recommendedTargetValue", "")).strip() + "," + function_text
        reason = str(member.get("locatorReason", "")).strip()
        member["locatorReason"] = (reason + " + 功能名消歧").strip(" +")


def _merge_flat_controls(*groups):
    merged = []
    seen = set()
    for group in groups:
        for item in group or []:
            identity = _build_flat_control_identity(item)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            merged.append(item)
    return merged


def _build_saved_control_id_from_name(name, fallback="control"):
    return slugify_filename(name, fallback)


def _build_control_group_key(flat_control):
    if not isinstance(flat_control, dict):
        return "unknown"
    target_method = str(flat_control.get("recommendedTargetMethod", "")).strip()
    target_value = str(flat_control.get("recommendedTargetValue", "")).strip()
    if target_method and target_value:
        key = f"locator|{target_method}|{target_value}"
        # 模板复制控件（同一 WPF 模板多次实例化，如各 interest-area 面板的
        # InterestAreas_Button_Edit/Delete 等）的 automationId/name/uiPath 完全相同，
        # 只有位置不同；追加归一化 rect 让不同实例在分组/保存去重中不被误并为一个，
        # 否则非全选保存时每组只保留一个，其它面板/位置的按钮会静默丢失。
        if str(flat_control.get("automationId", "")).strip():
            rect = _normalize_rect_dict(flat_control.get("boundingBox"))
            if rect:
                key += f"|rect:{rect['left']},{rect['top']},{rect['right']},{rect['bottom']}"
        return key
    automation_id = str(flat_control.get("automationId", "")).strip()
    control_type = str(flat_control.get("controlType", "")).strip()
    class_name = str(flat_control.get("className", "")).strip()
    ui_path = str(flat_control.get("uiPath", "")).strip()
    return "|".join(["fallback", automation_id, class_name, control_type, ui_path])


def _build_group_display_name(flat_control, fallback="控件"):
    if not isinstance(flat_control, dict):
        return fallback
    return (
        str(flat_control.get("savedControlName", "")).strip()
        or str(flat_control.get("suggestedControlName", "")).strip()
        or str(flat_control.get("displayName", "")).strip()
        or str(flat_control.get("automationId", "")).strip()
        or str(flat_control.get("name", "")).strip()
        or str(flat_control.get("className", "")).strip()
        or fallback
    )


def _rank_group_candidate(flat_control, preferred_index=0):
    if not isinstance(flat_control, dict):
        return (-1, -1, -1, preferred_index * -1)
    score = int(flat_control.get("locatorScore", 0) or 0)
    has_automation_id = 1 if str(flat_control.get("automationId", "")).strip() else 0
    control_type = str(flat_control.get("controlType", "")).strip().lower()
    preferred_type = 1 if control_type in {"button", "edit", "combobox", "menuitem", "tabitem", "treeitem", "text"} else 0
    return (score, has_automation_id, preferred_type, preferred_index * -1)


def _collect_uia_dumper_flat_controls(
    target_window,
    flat_controls,
    seen_identities,
    max_depth=FULLTREE_MIN_DEPTH,
    scan_timeout_seconds=30,
    status_callback=None,
):
    """借助独立 .NET 工具 uia_tree_dumper.exe（走 UIA RawViewWalker）全量遍历控件树，
    补采纯 pywinauto 遍历漏掉的深层 / 虚拟化 / 离屏控件，对齐 Accessibility Insights 的采全能力。
    工具缺失或调用失败时静默降级，不影响已采结果。返回新增控件数量。"""
    if not os.path.isfile(UIA_TREE_DUMPER_EXE):
        return 0
    pid = str(target_window.get("processId", "")).strip()
    handle = str(target_window.get("handle", "")).strip()
    if not pid and not handle:
        return 0
    args = [UIA_TREE_DUMPER_EXE]
    if handle:
        args += ["--hwnd", handle]
    else:
        args += ["--pid", pid]
    args += ["--maxdepth", str(max(int(max_depth), FULLTREE_MIN_DEPTH))]
    timeout_ms = max(5000, int(scan_timeout_seconds) * 1000)
    args += ["--timeout", str(timeout_ms)]
    if status_callback:
        status_callback("调用 uia_tree_dumper 全量遍历 UIA 树...", len(flat_controls))
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            timeout=max(15, int(scan_timeout_seconds) + 15),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:
        return 0
    if completed.returncode != 0 or not completed.stdout:
        return 0
    try:
        records = json.loads(completed.stdout.decode("utf-8", errors="replace"))
    except Exception:
        return 0
    if not isinstance(records, list) or not records:
        return 0

    return _merge_uia_dumper_records(
        records, target_window, flat_controls, seen_identities, status_callback
    )
def _merge_uia_dumper_records(records, target_window, flat_controls, seen_identities, status_callback=None):
    """将 uia_tree_dumper 输出的记录数组转换为标准 flat_control 结构并去重合并。"""
    by_index = {}
    for rec in records:
        try:
            by_index[int(rec.get("index"))] = rec
        except Exception:
            continue

    root_title = str(target_window.get("title", "")).strip()
    root_class = str(target_window.get("className", "")).strip()

    def _seg_name(rec):
        parsed = {
            "name": str(rec.get("name") or "").strip(),
            "automationId": str(rec.get("automationId") or "").strip(),
            "className": str(rec.get("className") or "").strip(),
            "controlType": str(rec.get("controlType") or "").strip(),
            "localizedControlType": "",
        }
        return _build_display_name(parsed, "控件", int(rec.get("index", 0) or 0))

    def _path_segments_for(rec):
        chain = []
        seen_idx = set()
        cur = rec
        guard = 0
        while cur is not None and guard < 64:
            guard += 1
            idx = cur.get("index")
            if idx in seen_idx:
                break
            seen_idx.add(idx)
            chain.append(_seg_name(cur))
            try:
                parent_idx = int(cur.get("parentIndex", -1))
            except Exception:
                parent_idx = -1
            if parent_idx < 0 or parent_idx not in by_index:
                break
            cur = by_index[parent_idx]
        return list(reversed(chain))
    added = 0
    win_pid = str(target_window.get("processId", "")).strip()
    win_handle = str(target_window.get("handle", "")).strip()

    for rec in records:
        if rec.get("error"):
            continue

        try:
            depth = int(rec.get("depth", 0) or 0)
        except Exception:
            depth = 0
        try:
            index = int(rec.get("index", 0) or 0)
        except Exception:
            index = 0

        name = str(rec.get("name") or "").strip()
        class_name = str(rec.get("className") or "").strip()
        control_type = normalize_control_type_name(
            str(rec.get("controlType") or "").strip(),
            str(rec.get("localizedControlType") or "").strip(),
        )
        automation_id = str(rec.get("automationId") or "").strip()
        help_text = str(rec.get("helpText") or "").strip()
        value_pattern_value = str(rec.get("value") or "").strip()
        toggle_state = ""
        expand_state = str(rec.get("expandState") or "").strip()
        runtime_id = _format_runtime_id(rec.get("runtimeId"))
        process_id = str(rec.get("processId") or "").strip() or win_pid
        handle = (
            str(rec.get("handle") or "").strip()
            or str(rec.get("nativeWindowHandle") or "").strip()
            or (win_handle if depth == 0 else "")
        )
        # dumper v2 补充字段（对齐 Inspect 面板与 pywinauto 采集端 schema）
        localized_control_type = str(rec.get("localizedControlType") or "").strip()
        framework_id = str(rec.get("frameworkId") or "").strip()
        access_key = str(rec.get("accessKey") or "").strip()
        accelerator_key = str(rec.get("acceleratorKey") or "").strip()
        item_type = str(rec.get("itemType") or "").strip()
        item_status = str(rec.get("itemStatus") or "").strip()
        labeled_by_name = str(rec.get("labeledByName") or "").strip()
        supported_patterns = [
            p.strip() for p in str(rec.get("patterns") or "").split(",") if p.strip()
        ]

        # 矩形转换: dumper 输出 {X,Y,W,H} -> {left,top,right,bottom}
        bounding_box = None
        bounding_rectangle = ""
        rect_raw = rec.get("rect")
        if isinstance(rect_raw, dict):
            try:
                rx = int(rect_raw.get("X"))
                ry = int(rect_raw.get("Y"))
                rw = int(rect_raw.get("W"))
                rh = int(rect_raw.get("H"))
                if rw > 0 and rh > 0:
                    bounding_box = {"left": rx, "top": ry, "right": rx + rw, "bottom": ry + rh}
                    bounding_rectangle = f"[l={rx},t={ry},r={rx + rw},b={ry + rh}]"
            except Exception:
                bounding_box = None

        is_offscreen_raw = rec.get("isOffscreen")
        is_offscreen = str(bool(is_offscreen_raw)) if is_offscreen_raw is not None else ""
        is_enabled_raw = rec.get("isEnabled")
        is_visible_val = None
        if is_offscreen_raw is not None:
            is_visible_val = not bool(is_offscreen_raw)
        keyboard_focusable = rec.get("isKeyboardFocusable")
        has_keyboard_focus = rec.get("hasKeyboardFocus")
        is_content_element = rec.get("isContentElement")
        is_control_element = rec.get("isControlElement")
        is_password = rec.get("isPassword")

        path_segments = _path_segments_for(rec)

        inspect_data = {
            "name": name,
            "value": value_pattern_value,
            "toggleState": toggle_state,
            "controlType": control_type,
            "localizedControlType": localized_control_type,
            "boundingRectangle": bounding_rectangle,
            "isEnabled": str(is_enabled_raw) if is_enabled_raw is not None else "",
            "isVisible": str(is_visible_val) if is_visible_val is not None else "",
            "isOffscreen": is_offscreen,
            "isKeyboardFocusable": str(keyboard_focusable) if keyboard_focusable is not None else "",
            "hasKeyboardFocus": str(has_keyboard_focus) if has_keyboard_focus is not None else "",
            "processId": process_id,
            "runtimeId": runtime_id,
            "frameworkId": framework_id,
            "className": class_name,
            "automationId": automation_id,
            "nativeWindowHandle": handle,
            "providerDescription": "",
            "legacyName": "",
            "legacyRole": "",
            "legacyState": "",
            "helpText": help_text,
            "accessKey": access_key,
            "acceleratorKey": accelerator_key,
            "itemType": item_type,
            "itemStatus": item_status,
            "isContentElement": str(is_content_element) if is_content_element is not None else "",
            "isControlElement": str(is_control_element) if is_control_element is not None else "",
            "isPassword": str(is_password) if is_password is not None else "",
            "labeledByName": labeled_by_name,
            "supportedPatterns": list(supported_patterns),
            "expandCollapseState": expand_state,
            "expandState": expand_state,
            "ancestors": list(path_segments[:-1]),
            "children": [],
        }
        full_ui_path = " > ".join(seg for seg in path_segments if seg)
        locator_method, locator_value, locator_score, locator_reason = build_locator_recommendation(
            inspect_data, index=index, ui_path=full_ui_path)
        inspect_data["recommendedTargetMethod"] = locator_method
        inspect_data["recommendedTargetValue"] = locator_value

        display_name = _build_display_name(inspect_data, "控件", index)
        raw_inspect_text = build_synthetic_inspect_text(inspect_data)

        flat_item = {
            "depth": depth,
            "index": index,
            "displayName": display_name,
            "windowTitle": root_title,
            "windowClassName": root_class,
            "processId": process_id,
            "handle": handle,
            "name": name,
            "className": class_name,
            "controlType": control_type,
            "localizedControlType": localized_control_type,
            "automationId": automation_id,
            "frameworkId": framework_id,
            "runtimeId": runtime_id,
            "value": value_pattern_value,
            "toggleState": toggle_state,
            "supportedPatterns": list(supported_patterns),
            "expandCollapseState": expand_state,
            "boundingRectangle": bounding_rectangle,
            "boundingBox": bounding_box,
            "isEnabled": bool(is_enabled_raw) if is_enabled_raw is not None else None,
            "isVisible": is_visible_val,
            "isOffscreen": is_offscreen,
            "isKeyboardFocusable": str(keyboard_focusable) if keyboard_focusable is not None else "",
            "hasKeyboardFocus": str(has_keyboard_focus) if has_keyboard_focus is not None else "",
            "helpText": help_text,
            "accessKey": access_key,
            "acceleratorKey": accelerator_key,
            "itemType": item_type,
            "itemStatus": item_status,
            "labeledByName": labeled_by_name,
            "providerDescription": "",
            "locatorScore": locator_score,
            "locatorReason": locator_reason,
            "recommendedTargetMethod": locator_method,
            "recommendedTargetValue": locator_value,
            "uiPath": full_ui_path,
            "parentPath": " > ".join(seg for seg in path_segments[:-1] if seg),
            "auxChecks": build_aux_checks(inspect_data),
            "inspectData": inspect_data,
            "rawInspectText": raw_inspect_text,
        }

        identity = _build_flat_control_identity(flat_item)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        flat_controls.append(flat_item)
        added += 1

    if status_callback and added:
        status_callback(f"uia_tree_dumper 新增 {added} 个控件", len(flat_controls))
    return added


def _scan_single_backend_payload(
    window_keyword="",
    backend="uia",
    use_foreground=False,
    max_depth=DEFAULT_MAX_DEPTH,
    region_rect=None,
    excluded_process_ids=None,
    excluded_titles=None,
    exclude_offscreen=True,
    exclude_unidentified_containers=True,
    scan_timeout_seconds=30,
    expand_dropdowns=False,
    status_callback=None,
    cancel_event=None,
    move_cursor=True,
):
    start_time = time.time()
    if status_callback:
        status_callback("开始扫描...", 0)
    
    backend = str(backend or "uia").strip().lower() or "uia"

    # UIA 性能优化：启用 RawViewWalker。
    # 默认的 ControlViewWalker 会在底层过滤掉很多非控件元素，这需要额外的 COM 计算时间。
    # 启用 RawViewWalker 会获取所有元素（速度极快），然后我们依赖后期的 _filter_noise_controls 在 Python 内存中过滤。
    if backend == "uia" and pywinauto:
        try:
            import pywinauto.windows.uia_element_info as uia_info
            uia_info.UIAElementInfo.use_raw_view_walker = True
        except Exception:
            pass

    max_depth = max(0, int(max_depth))

    # 整树采集（未指定画框区域）时自动放宽过滤条件：
    #   1. exclude_offscreen -> False：保留滚动区/折叠面板/未激活 Tab 中 isOffscreen 的控件；
    #   2. exclude_unidentified_containers -> False：保留无 name/automationId 的 WPF 布局容器；
    #   3. max_depth 提升到至少 FULLTREE_MIN_DEPTH：覆盖深层嵌套控件树。
    # 放宽后靠 _prune_empty_unidentified_containers 做保守剪枝，避免空壳容器淹没结果。
    fulltree_relaxation = None
    is_fulltree = _normalize_rect_dict(region_rect) is None
    if is_fulltree:
        fulltree_relaxation = {
            "excludeOffscreen": [bool(exclude_offscreen), False],
            "excludeUnidentifiedContainers": [bool(exclude_unidentified_containers), False],
            "maxDepth": [max_depth, max(max_depth, FULLTREE_MIN_DEPTH)],
        }
        exclude_offscreen = False
        exclude_unidentified_containers = False
        max_depth = max(max_depth, FULLTREE_MIN_DEPTH)

    if use_foreground:
        target_window_wrapper = _get_foreground_wrapper(
            backend,
            excluded_process_ids=excluded_process_ids,
            excluded_titles=excluded_titles,
        )
    else:
        target_window_wrapper = _find_window_by_keyword(
            window_keyword,
            backend,
            excluded_process_ids=excluded_process_ids,
            excluded_titles=excluded_titles,
        )

    target_window = _build_target_window_info(target_window_wrapper)
    root_handle = str(target_window.get("handle", "")).strip()
    root_display_name = _build_display_name(
        {
            "name": target_window.get("title", ""),
            "className": target_window.get("className", ""),
            "controlType": "Window",
        },
        "窗口",
        1,
    )
    flat_controls = []
    bfs_stats = {}
    use_raw_view_bfs = backend != "win32"
    if use_raw_view_bfs:
        try:
            # RawView BFS 遍历每个元素需多次 COM 调用，超时比常规 DFS 更宽松
            bfs_timeout = max(scan_timeout_seconds, 90)
            bfs_stats = _walk_raw_view_bfs(
                target_window_wrapper,
                max_depth=max_depth,
                target_window=target_window,
                flat_controls=flat_controls,
                path_segments=[root_display_name],
                start_time=start_time,
                scan_timeout_seconds=bfs_timeout,
                status_callback=status_callback,
                cancel_event=cancel_event,
            )
        except Exception:
            if status_callback:
                status_callback("RawView BFS 失败，降级为 _walk_wrapper...", 0)
            flat_controls.clear()
            use_raw_view_bfs = False
    if not use_raw_view_bfs:
        control_tree = _walk_wrapper(
            target_window_wrapper,
            depth=0,
            max_depth=max_depth,
            target_window=target_window,
            flat_controls=flat_controls,
            path_segments=[root_display_name],
            start_time=start_time,
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
            cancel_event=cancel_event,
        )
    # 树结构元数据增强 + 重建嵌套控件树（在全部采集与探针补采完成后统一执行，
    # 确保所有 flat_controls 条目都获得 pathHash / childCount / isTransparentContainer，
    # 且 controlsTree 包含探针补采的新条目）。
    # 注意：_enrich_tree_metadata 和 _build_tree_from_flat 延后到全部补采完成后执行。
    seen_identities = {_build_flat_control_identity(item) for item in flat_controls}
    # 用户中止后不再执行任何补采（尤其 uia_tree_dumper 会启动子进程），
    # 仅返回已采集到的部分结果。
    scan_cancelled = bool(cancel_event and cancel_event.is_set())
    if not scan_cancelled:
        _collect_region_probe_wrappers(
            backend,
            region_rect,
            target_window,
            flat_controls,
            seen_identities,
            root_handle=root_handle,
            max_depth=max_depth,
        )
    # 整树采集时借鉴画框采集的网格探针：对整个窗口矩形做 from_point 采样，
    # 补采纯树遍历漏掉的虚拟化/离屏控件。
    if is_fulltree and not scan_cancelled:
        _collect_fulltree_probe_wrappers(
            backend,
            target_window,
            target_window_wrapper,
            flat_controls,
            seen_identities,
            root_handle=root_handle,
            max_depth=max_depth,
            start_time=start_time,
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
        )
        # 调用独立 .NET 工具 uia_tree_dumper，用 RawViewWalker 全量遍历 UIA 树，
        # 补采 pywinauto 因过滤/深度限制漏掉的控件（对齐 Inspect/Accessibility Insights）。
        _collect_uia_dumper_flat_controls(
            target_window,
            flat_controls,
            seen_identities,
            max_depth=max_depth,
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
        )
    # 标签伴随采集：保证每个已采集标签对应的输入/操作控件也被采到（树采集+画框采集通用）。
    # 放在 _enrich_tree_metadata 之前，使补采条目同样获得 pathHash 等树元数据。
    if not scan_cancelled:
        _collect_label_companion_wrappers(
            backend,
            target_window,
            flat_controls,
            seen_identities,
            root_handle=root_handle,
            max_depth=max_depth,
            start_time=start_time,
            scan_timeout_seconds=scan_timeout_seconds,
            status_callback=status_callback,
        )
    # ---- 全部采集与补采完成，统一增强元数据并重建控件树 ----
    _enrich_tree_metadata(flat_controls)
    control_tree = _build_tree_from_flat(flat_controls)
    for item in flat_controls:
        item["scanBackend"] = backend
    # 先规范化 WPF 输入框（Custom/Pane→Edit），再做同行标签关联：
    # 关联只认可操作类型集合，自定义输入控件必须先恢复为 Edit 才不会错过关联（数据流时序）。
    _normalize_textbox_wrappers(flat_controls)
    # 同父容器内兄弟 TextBlock 标签回填（PART_ContentHost / Edit / TextBox）：
    # 必须在 _normalize_textbox_wrappers 之后（controlType 已规范化为 Edit），
    # 在 _associate_region_labels_with_controls 之前（区域关联会设置 relatedLabelName，
    # 本函数只处理 labelText 仍为空的控件，避免覆盖区域关联结果）。
    _backfill_sibling_label_for_inputs(flat_controls)
    # 在区域筛选前做 dict 级"标签→实际控件"横向关联（同行下拉框/输入框优先）。
    _associate_region_labels_with_controls(flat_controls)
    # 全局唯一性消歧：对共用同一定位器的不同控件追加 found_index，避免定位到同一处。
    _disambiguate_duplicate_locators(flat_controls)
    # 可选：自动展开区域内下拉框，采集其可选项（会真实操作界面，默认关闭）。
    dropdown_diagnostics = []
    if expand_dropdowns:
        dropdown_diagnostics = _expand_region_dropdowns(
            flat_controls, region_rect, backend,
            status_callback=status_callback, move_cursor=move_cursor,
        )
    # 采集阶段结束，剥离临时 live 包装器引用，避免序列化失败与 COM 引用滞留。
    for item in flat_controls:
        item.pop("_wrapperRef", None)
    existing_ids = set()
    region_controls = _filter_flat_controls_by_region(flat_controls, region_rect)
    region_controls = _prune_low_value_region_controls(region_controls, target_window.get("title", ""))
    _enrich_flat_controls(region_controls, target_window)
    # TextBox 规范化已前移至标签关联之前（对全量 flat_controls 生效），此处无需重复。
    # A+C: 过滤离屏控件和无标识容器
    region_controls = _filter_noise_controls(
        region_controls,
        exclude_offscreen=exclude_offscreen,
        exclude_unidentified_containers=exclude_unidentified_containers,
    )
    # 整树采集放宽过滤后，只保守剔除真正的空壳无标识容器，避免噪声淹没结果。
    if is_fulltree:
        region_controls = _prune_empty_unidentified_containers(region_controls)
    # 标签关联回填：对弱定位的 Edit/ComboBox 用邻近标签文本增强
    _backfill_label_text_to_controls(region_controls)
    # 回填可能改变推荐定位器（label_text），对回填后的结果再消歧一次
    _disambiguate_duplicate_locators(region_controls)
    for item in region_controls:
        item.pop("_wrapperIdentity", None)
    control_definitions = [
        _build_control_definition_from_flat(item, existing_ids)
        for item in region_controls
        if _should_include_definition(item)
    ]
    by_type = {}
    for item in region_controls:
        control_type = str(item.get("controlType", "")).strip() or "Unknown"
        by_type[control_type] = by_type.get(control_type, 0) + 1

    if status_callback:
        status_callback("扫描完成。", len(flat_controls))

    # 生成控件摘要（快速概览，不影响原始数据）
    control_summary = _build_control_summary(region_controls, flat_controls)

    scan_meta = {
        "scanTime": datetime.now().isoformat(timespec="seconds"),
        "backend": backend,
        "mode": "foreground" if use_foreground else "keyword",
        "windowKeyword": str(window_keyword or "").strip(),
        "maxDepth": max_depth,
        "scanTimeoutSeconds": scan_timeout_seconds,
        "totalControls": len(region_controls),
        "rawTotalControls": len(flat_controls),
        "regionRect": _normalize_rect_dict(region_rect),
        "controlTypeSummary": dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
        "dropdownExpandDiagnostics": dropdown_diagnostics,
        "fullTreeRelaxation": fulltree_relaxation,
    }
    # 采集截断可见性：超时/元素熔断/用户中止会静默漏采后续元素，必须显式记录，
    # 提醒用户对缺失区域使用悬停跟踪/定点补采。
    if bfs_stats.get("timedOut") or bfs_stats.get("hitLimit") or scan_cancelled:
        truncation = {
            "bfsTimedOut": bool(bfs_stats.get("timedOut")),
            "bfsHitElementLimit": bool(bfs_stats.get("hitLimit")),
            "elementLimit": _MAX_ELEMENTS_PER_WALK,
            "hint": "整树采集被截断，结果可能不全；请对缺失区域使用悬停跟踪/定点补采。",
        }
        if scan_cancelled:
            truncation["cancelled"] = True
            truncation["hint"] = "扫描已被用户中止，结果不完整；可重新采集。"
        scan_meta["truncation"] = truncation

    return {
        "schemaVersion": "1.0",
        "scanMeta": scan_meta,
        "controlSummary": control_summary,
        "targetWindow": target_window,
        "controlsTree": control_tree,
        "flatControls": region_controls,
        "controlDefinitions": control_definitions,
    }


def build_control_map_payload(
    window_keyword="",
    backend=DEFAULT_BACKEND,
    use_foreground=False,
    max_depth=DEFAULT_MAX_DEPTH,
    region_rect=None,
    excluded_process_ids=None,
    excluded_titles=None,
    exclude_offscreen=True,
    exclude_unidentified_containers=True,
    scan_timeout_seconds=30,
    expand_dropdowns=False,
    status_callback=None,
    cancel_event=None,
    move_cursor=True,
):
    requested_backend = str(backend or DEFAULT_BACKEND).strip().lower() or DEFAULT_BACKEND
    backend_candidates = _expand_backend_candidates(requested_backend)
    payloads = []
    for backend_name in backend_candidates:
        payloads.append(
            _scan_single_backend_payload(
                window_keyword=window_keyword,
                backend=backend_name,
                use_foreground=use_foreground,
                max_depth=max_depth,
                region_rect=region_rect,
                excluded_process_ids=excluded_process_ids,
                excluded_titles=excluded_titles,
                exclude_offscreen=exclude_offscreen,
                exclude_unidentified_containers=exclude_unidentified_containers,
                scan_timeout_seconds=scan_timeout_seconds,
                expand_dropdowns=expand_dropdowns,
                status_callback=status_callback,
                cancel_event=cancel_event,
                move_cursor=move_cursor,
            )
        )

    if len(payloads) == 1 and region_rect:
        only_payload = payloads[0]
        only_controls = only_payload.get("flatControls", []) or []
        only_root = len(only_controls) <= 1 or all(
            _is_low_value_root_candidate(item, only_payload.get("targetWindow", {}).get("title", ""))
            for item in only_controls
        )
        if only_root:
            alternate_backend = "win32" if backend_candidates[0] == "uia" else "uia"
            try:
                payloads.append(
                    _scan_single_backend_payload(
                        window_keyword=window_keyword,
                        backend=alternate_backend,
                        use_foreground=use_foreground,
                        max_depth=max_depth,
                        region_rect=region_rect,
                        excluded_process_ids=excluded_process_ids,
                        excluded_titles=excluded_titles,
                        scan_timeout_seconds=scan_timeout_seconds,
                        expand_dropdowns=expand_dropdowns,
                        status_callback=status_callback,
                        cancel_event=cancel_event,
                    )
                )
            except Exception:
                pass

    base_payload = payloads[0]
    if len(payloads) == 1:
        base_payload["scanMeta"]["requestedBackend"] = requested_backend
        base_payload["scanMeta"]["mergedBackends"] = [payloads[0].get("scanMeta", {}).get("backend", requested_backend)]
        return base_payload

    merged_controls = _merge_flat_controls(*(payload.get("flatControls", []) for payload in payloads))
    merged_controls = _filter_flat_controls_by_region(merged_controls, region_rect)
    merged_controls = _prune_low_value_region_controls(merged_controls, base_payload.get("targetWindow", {}).get("title", ""))
    _enrich_flat_controls(merged_controls, base_payload.get("targetWindow", {}))
    _normalize_textbox_wrappers(merged_controls)
    _backfill_sibling_label_for_inputs(merged_controls)
    _backfill_label_text_to_controls(merged_controls)
    _reconcile_label_with_name(merged_controls)
    existing_ids = set()
    merged_definitions = [
        _build_control_definition_from_flat(item, existing_ids)
        for item in merged_controls
        if _should_include_definition(item)
    ]
    by_type = {}
    for item in merged_controls:
        control_type = str(item.get("controlType", "")).strip() or "Unknown"
        by_type[control_type] = by_type.get(control_type, 0) + 1

    raw_total_controls = 0
    merged_backends = []
    merged_dropdown_diagnostics = []
    for payload in payloads:
        scan_meta = payload.get("scanMeta", {}) or {}
        raw_total_controls += int(scan_meta.get("rawTotalControls", 0) or 0)
        backend_name = str(scan_meta.get("backend", "")).strip()
        if backend_name and backend_name not in merged_backends:
            merged_backends.append(backend_name)
        for entry in scan_meta.get("dropdownExpandDiagnostics", []) or []:
            merged_dropdown_diagnostics.append(entry)

    # 单后端 payload 已在整树模式下放宽过滤并提升深度，合并元数据应反映实际生效值，
    # 而非未放宽的入参 max_depth。
    base_scan_meta = base_payload.get("scanMeta", {}) or {}
    effective_max_depth = base_scan_meta.get("maxDepth", max_depth)
    fulltree_relaxation = base_scan_meta.get("fullTreeRelaxation")

    return {
        "schemaVersion": "1.0",
        "scanMeta": {
            "scanTime": datetime.now().isoformat(timespec="seconds"),
            "backend": requested_backend,
            "requestedBackend": requested_backend,
            "mergedBackends": merged_backends,
            "mode": "foreground" if use_foreground else "keyword",
            "windowKeyword": str(window_keyword or "").strip(),
            "maxDepth": effective_max_depth,
            "totalControls": len(merged_controls),
            "rawTotalControls": raw_total_controls,
            "regionRect": _normalize_rect_dict(region_rect),
            "controlTypeSummary": dict(sorted(by_type.items(), key=lambda item: (-item[1], item[0]))),
            "dropdownExpandDiagnostics": merged_dropdown_diagnostics,
            "fullTreeRelaxation": fulltree_relaxation,
        },
        "targetWindow": base_payload.get("targetWindow", {}),
        "controlsTree": base_payload.get("controlsTree", {}),
        "flatControls": merged_controls,
        "controlDefinitions": merged_definitions,
    }


def save_control_map_payload(payload, output_path=""):
    recordings_dir = os.path.join(CONTROL_MAP_DIR, "recordings")
    ensure_directory(recordings_dir)
    if not output_path:
        title = ((payload.get("targetWindow", {}) or {}).get("title", "") or "window").strip()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(recordings_dir, f"{timestamp}_{slugify_filename(title)}_control_map.json")
    with open(output_path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)
    return output_path


class _PersistentHighlight:
    """持久跟随鼠标目标的高亮框，鼠标完全穿透（WS_EX_TRANSPARENT）。

    设计原因：悬停跟踪模式下需要实时高亮当前命中的控件矩形，但不能让 overlay
    本身被 pywinauto 的 from_point() 命中（否则会"自伤"——采到自己的控件信息）。
    WS_EX_TRANSPARENT 使 overlay 对鼠标和 hit-test 完全透明，WS_EX_LAYERED 是
    transparentcolor 生效的前提，WS_EX_NOACTIVATE 防止抢焦点。
    """

    # Windows 扩展样式常量
    _GWL_EXSTYLE = -20
    _WS_EX_TRANSPARENT = 0x00000020
    _WS_EX_LAYERED = 0x00080000
    _WS_EX_NOACTIVATE = 0x08000000

    def __init__(self, parent):
        self._parent = parent
        self._overlay = None  # Tk Toplevel，延迟创建
        self._canvas = None   # Canvas 控件，延迟创建
        self.last_rect = None  # 当前显示的高亮矩形 (left, top, right, bottom)，None=未显示

    def _ensure_overlay(self):
        """懒创建 overlay 窗口（首次调用时）。"""
        if self._overlay is not None:
            try:
                if self._overlay.winfo_exists():
                    return
            except Exception:
                pass
            self._overlay = None
        try:
            overlay = tk.Toplevel(self._parent)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            # 背景透明：magenta 作为 transparentcolor，只留红色边框可见
            overlay.attributes("-transparentcolor", "magenta")
            # 初始隐藏，等 show() 时再定位显示
            overlay.withdraw()
            # 设置鼠标穿透样式：overlay 不可被 hit-test 命中
            try:
                hwnd_str = overlay.frame() if overlay.frame() else str(overlay.winfo_id())
                hwnd = int(hwnd_str, 16)
                style = ctypes.windll.user32.GetWindowLongW(hwnd, self._GWL_EXSTYLE)
                new_style = style | self._WS_EX_TRANSPARENT | self._WS_EX_LAYERED | self._WS_EX_NOACTIVATE
                ctypes.windll.user32.SetWindowLongW(hwnd, self._GWL_EXSTYLE, new_style)
            except Exception:
                pass  # 穿透设置失败不阻塞功能，仍有 pid 过滤兜底
            self._overlay = overlay
        except Exception:
            self._overlay = None

    def show(self, rect):
        """移动/调整高亮框到指定矩形区域。已显示则只移动不重建。

        rect: 具有 left/top/right/bottom 属性的矩形对象或 (left, top, right, bottom) 元组。
        """
        try:
            if hasattr(rect, 'left'):
                left, top, right, bottom = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
            else:
                left, top, right, bottom = int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])
            width = max(4, right - left)
            height = max(4, bottom - top)
        except Exception:
            return

        self.last_rect = (left, top, right, bottom)
        self._ensure_overlay()
        if self._overlay is None:
            return
        try:
            # 绕过 DPI 缩放设置 geometry，与 _show_locator_highlight 保持一致
            wt_dpi.raw_geometry(self._overlay, f"{width}x{height}+{left}+{top}")
            # 如果 canvas 已存在则只更新矩形位置
            canvas = getattr(self, '_canvas', None)
            if canvas is not None:
                try:
                    canvas.delete('rect')
                    canvas.create_rectangle(2, 2, width - 2, height - 2, outline="#ff2020", width=4, tags='rect')
                except Exception:
                    pass
            else:
                canvas = tk.Canvas(self._overlay, bg="magenta", highlightthickness=0, bd=0)
                canvas.pack(fill=tk.BOTH, expand=True)
                canvas.create_rectangle(2, 2, width - 2, height - 2, outline="#ff2020", width=4, tags='rect')
                self._canvas = canvas
            self._overlay.deiconify()
            self._overlay.lift()
        except Exception:
            pass

    def hide(self):
        """隐藏高亮框（不销毁，可再次 show）。"""
        self.last_rect = None
        if self._overlay is not None:
            try:
                self._overlay.withdraw()
            except Exception:
                pass

    def destroy(self):
        """彻底销毁 overlay。"""
        self._canvas = None
        if self._overlay is not None:
            try:
                self._overlay.destroy()
            except Exception:
                pass
            self._overlay = None


class RegionPickerOverlay:
    def __init__(self, parent, on_complete=None):
        self.parent = parent
        self.on_complete = on_complete
        self.result = None
        self.start_x = 0
        self.start_y = 0
        self.rect_id = None

        self.window = tk.Toplevel(parent)
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.attributes("-alpha", 0.25)
        self.window.configure(bg="#111111")
        # 全屏框选遮罩用真实屏幕像素，绕过 DPI 自动缩放，否则会超出屏幕、坐标错位
        wt_dpi.raw_geometry(
            self.window,
            f"{self.window.winfo_screenwidth()}x{self.window.winfo_screenheight()}+0+0",
        )

        self.canvas = tk.Canvas(self.window, bg="#111111", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.tip_id = self.canvas.create_text(
            30,
            30,
            anchor="nw",
            fill="#ffffff",
            text="按住鼠标左键拖动画框，松开完成；按 Esc 取消。",
            font=("Microsoft YaHei UI", 14, "bold"),
        )
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.window.bind("<Escape>", lambda _event: self.finish(None))
        self.window.focus_force()

    def _on_press(self, event):
        self.start_x = int(event.x_root)
        self.start_y = int(event.y_root)
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            self.start_x,
            self.start_y,
            self.start_x,
            self.start_y,
            outline="#22c55e",
            width=3,
            fill="#60a5fa",
            stipple="gray25",
        )

    def _on_drag(self, event):
        if self.rect_id is None:
            return
        self.canvas.coords(self.rect_id, self.start_x, self.start_y, int(event.x_root), int(event.y_root))

    def _on_release(self, event):
        end_x = int(event.x_root)
        end_y = int(event.y_root)
        left = min(self.start_x, end_x)
        top = min(self.start_y, end_y)
        right = max(self.start_x, end_x)
        bottom = max(self.start_y, end_y)
        if right - left < 6 or bottom - top < 6:
            self.finish(None)
            return
        self.finish({"left": left, "top": top, "right": right, "bottom": bottom})

    def finish(self, rect):
        self.result = _normalize_rect_dict(rect)
        try:
            self.window.destroy()
        finally:
            if self.on_complete:
                self.on_complete(self.result)


class ControlMapBuilderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("WT 控件库采集器")
        self.root.geometry("1400x860")

        self.var_scan_mode = tk.StringVar(value="foreground")
        self.var_window_keyword = tk.StringVar(value="")
        self.var_backend = tk.StringVar(value=DEFAULT_BACKEND)
        self.var_max_depth = tk.IntVar(value=DEFAULT_MAX_DEPTH)
        self.var_pick_delay = tk.IntVar(value=DEFAULT_PICK_DELAY_SECONDS)
        self.var_exclude_offscreen = tk.BooleanVar(value=True)
        self.var_exclude_unidentified = tk.BooleanVar(value=True)
        self.var_expand_dropdowns = tk.BooleanVar(value=False)
        # 点扫掠实体化选项时是否物理移动鼠标悬停（MUP 对悬停布局敏感会卡死时取消勾选）
        self.var_move_cursor = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value='准备就绪：建议先切到目标软件窗口，再点击"一键整树采集并保存"。')
        self.var_summary = tk.StringVar(value="尚未扫描控件树。")
        self.current_payload = None
        self.current_output_path = ""
        self.current_region_rect = None
        self.checked_control_indices = set()
        self._all_checked_mode = False
        self.control_groups = []
        self.var_saved_control_name = tk.StringVar(value="")
        self.var_saved_control_id = tk.StringVar(value="")
        self.var_scan_progress = tk.StringVar(value="")
        # 保存后自动合并入库：勾选时保存成功即触发全量 recordings → 总控件库（含备份）
        self.var_auto_merge = tk.BooleanVar(value=True)
        # 扫描后台线程状态：防重入 + 中止标志 + 中止按钮引用 + 扫描期间禁用的按钮
        self._scan_running = False
        self._scan_cancel_event = None
        self._scan_cancel_btn = None
        self._scan_disabled_widgets = []
        # 定点补采：命中元素后沿祖先链上溯的层数（人工指到叶子时扩大补采范围）
        self.var_supplement_climb = tk.IntVar(value=1)
        # 层级树视图 iid → 树节点映射（选中预览与补采锚定用）；iid 用单调递增序号，
        # 增量插入/删除后也不会撞号
        self._hierarchy_nodes_by_iid = {}
        self._hierarchy_iid_seq = 0
        # 树节点 index → flat 条目映射（层级树展示名回退，刷新时重建）
        self._hierarchy_flat_by_index = {}
        # 补采选中控件时暂存的期望 identity
        self._pending_supplement_expected = None
        # 连续悬停跟踪补采（Inspect 式）运行状态
        self._hover_mode_active = False
        self._hover_consecutive_errors = 0  # 连续错误计数，超过阈值自动禁用悬停模式
        self._hover_after_id = None
        self._hover_last_pos = None
        self._hover_stable_count = 0
        self._hover_last_hit_key = ""
        # 最近一次"新鲜命中"的光标位置：overlay 跳过重探测的参考点（见 _hover_probe_once）
        self._hover_last_fresh_xy = None
        # 采集层去重：本轮已入队补采过的元素 key（与查看层 last_hit_key 分离）
        self._hover_last_collect_key = ""
        # 位置级防重复：探测被阻塞无新鲜 key 时，距上次补采点过近则跳过入队
        self._hover_last_collect_pos = None
        # 当前悬停元素在 flatControls 中的下标缓存（None=不在库中）
        self._hover_existing_index = None
        self._hover_session_added = 0
        # "只看不采"模式：开启时悬停仅高亮跟随，不触发补采写库
        self._hover_look_only = False
        # 冻结状态：锁定当前悬停元素，停止探测但保持高亮
        self._hover_frozen = False
        self._hover_frozen_wrapper = None  # 冻结时锁定的 pywinauto wrapper
        self._hover_frozen_rect = None  # 冻结时锁定的控件矩形 (left,top,right,bottom)
        # 持久高亮跟随 overlay（Inspect 式红框），悬停模式开启时创建，停止时销毁
        self._persistent_highlight = None
        # pynput 全局热键监听器（悬停模式开启时创建，停止时销毁）
        # 设计原因：悬停时焦点在目标软件，Tk 快捷键不生效，需全局监听
        self._hotkey_listener = None
        # ---- Worker 线程（秒级子树采集卸载到后台，避免阻塞 UI）----
        # 设计原因：collect_subtree_at_point 对 WPF 深树是秒级操作，
        # 而 _hover_tick 每 350ms 调度一次，若同步执行会严重阻塞 UI。
        # 通过 worker 线程 + 队列 + 防重入标志，保证 UI 流畅且不会并发采集。
        self._worker_queue = queue.Queue()
        self._worker_busy = False  # 防重入标志：上一个任务未完成时为 True
        self._worker_thread = None  # 当前运行的 worker 线程
        # ---- 悬停命中探测线程（把 from_point 从主线程挪走，杜绝 UI 卡死）----
        # 设计原因：_probe_hover_hit_key 的 UIA COM 调用无超时，MUP 忙/卡时单次
        # 可阻塞主线程数秒，整窗"未响应"。探测挪到独立线程后，主线程每个 tick
        # 只读最新探测结果，永不阻塞；命中跟随延迟约 1 tick。
        self._probe_thread = None
        self._probe_stop = threading.Event()  # 停止信号，_stop_probe_thread 时置位
        self._probe_wake = threading.Event()
        self._probe_req_lock = threading.Lock()
        self._probe_request = None  # 主线程请求探测的 (x, y, submit_ts)，单槽取最新
        self._probe_result = (None, None, None, None, "")  # 探测线程最新完成 (x, y, key, rect, pid)
        self._probe_result_latency_ms = 0.0  # 最近一次探测自身的耗时（线程内度量），供慢 tick 日志
        self._hover_last_pid = ""  # 最近命中元素的进程 pid（放行补采用）

        self._apply_theme()
        self._build_ui()

        # 任务栏进度条（采集期间显示进度）
        self._taskbar_progress = _TaskbarProgress(self.root)
        # 置顶迷你进度浮窗（采集时主窗口常最小化，实时反馈进度与结果）
        self._scan_progress_overlay = _ScanProgressOverlay(self.root)

    def _apply_theme(self):
        root = self.root
        root.configure(bg=CONTROL_MAP_THEME["bg"])
        root.option_add("*Font", CONTROL_MAP_THEME["font"])
        root.option_add("*TCombobox*Listbox*Font", CONTROL_MAP_THEME["font"])
        root.option_add("*Entry*Font", CONTROL_MAP_THEME["font"])
        root.option_add("*Spinbox*Font", CONTROL_MAP_THEME["font"])
        root.option_add("*Checkbutton*Font", CONTROL_MAP_THEME["font"])
        root.option_add("*Radiobutton*Font", CONTROL_MAP_THEME["font"])

        style = ttk.Style(root)
        style.configure("ControlMap.TCombobox", fieldbackground=CONTROL_MAP_THEME["panel"],
                        background=CONTROL_MAP_THEME["panel"], foreground=CONTROL_MAP_THEME["text"],
                        arrowcolor=CONTROL_MAP_THEME["primary"], bordercolor=CONTROL_MAP_THEME["border"])
        style.map("ControlMap.TCombobox", fieldbackground=[("readonly", CONTROL_MAP_THEME["panel_soft"])])
        style.configure("ControlMap.Treeview", background=CONTROL_MAP_THEME["panel"],
                        fieldbackground=CONTROL_MAP_THEME["panel"], foreground=CONTROL_MAP_THEME["text"],
                        borderwidth=1, relief="solid", rowheight=28)
        style.map("ControlMap.Treeview",
                  background=[("selected", CONTROL_MAP_THEME["primary_soft"])],
                  foreground=[("selected", CONTROL_MAP_THEME["primary"])])
        style.configure("ControlMap.Treeview.Heading", background=CONTROL_MAP_THEME["toolbar"],
                        foreground=CONTROL_MAP_THEME["text"], relief="flat", padding=(8, 6),
                        font=(CONTROL_MAP_THEME["font"][0], 10, "bold"))
        style.map("ControlMap.Treeview.Heading", background=[("active", CONTROL_MAP_THEME["border"])])
        style.configure("ControlMap.Vertical.TScrollbar", background=CONTROL_MAP_THEME["panel_soft"],
                        troughcolor=CONTROL_MAP_THEME["bg"], bordercolor=CONTROL_MAP_THEME["border"],
                        arrowcolor=CONTROL_MAP_THEME["muted"], relief="flat")
        style.configure("ControlMap.Horizontal.TScrollbar", background=CONTROL_MAP_THEME["panel_soft"],
                        troughcolor=CONTROL_MAP_THEME["bg"], bordercolor=CONTROL_MAP_THEME["border"],
                        arrowcolor=CONTROL_MAP_THEME["muted"], relief="flat")

    def _build_ui(self):
        toolbar = tk.LabelFrame(self.root, text="扫描配置", padx=10, pady=10,
                               bg=CONTROL_MAP_THEME["toolbar"], fg=CONTROL_MAP_THEME["text"],
                               relief="flat", bd=1, highlightthickness=1,
                               highlightbackground=CONTROL_MAP_THEME["border"],
                               font=(CONTROL_MAP_THEME["font"][0], 11, "bold"))
        toolbar.pack(fill=tk.X, padx=10, pady=10)

        tk.Radiobutton(toolbar, text="当前前台窗口", variable=self.var_scan_mode, value="foreground").grid(row=0, column=0, sticky="w")
        tk.Radiobutton(toolbar, text="按标题关键字", variable=self.var_scan_mode, value="keyword").grid(row=0, column=1, sticky="w")
        tk.Label(toolbar, text="窗口关键字", bg=CONTROL_MAP_THEME["toolbar"], fg=CONTROL_MAP_THEME["text"]).grid(row=0, column=2, sticky="e", padx=(10, 4))
        tk.Entry(toolbar, textvariable=self.var_window_keyword, width=28).grid(row=0, column=3, sticky="ew")
        tk.Label(toolbar, text="backend", bg=CONTROL_MAP_THEME["toolbar"], fg=CONTROL_MAP_THEME["text"]).grid(row=0, column=4, sticky="e", padx=(10, 4))
        ttk.Combobox(toolbar, textvariable=self.var_backend, values=BACKEND_OPTIONS, width=8, state="readonly", style="ControlMap.TCombobox").grid(row=0, column=5, sticky="w")
        tk.Label(toolbar, text="最大深度", bg=CONTROL_MAP_THEME["toolbar"], fg=CONTROL_MAP_THEME["text"]).grid(row=0, column=6, sticky="e", padx=(10, 4))
        tk.Spinbox(toolbar, from_=0, to=40, textvariable=self.var_max_depth, width=6).grid(row=0, column=7, sticky="w")
        tk.Label(toolbar, text="画框延迟", bg=CONTROL_MAP_THEME["toolbar"], fg=CONTROL_MAP_THEME["text"]).grid(row=0, column=8, sticky="e", padx=(10, 4))
        tk.Spinbox(toolbar, from_=1, to=10, textvariable=self.var_pick_delay, width=6).grid(row=0, column=9, sticky="w")
        toolbar.columnconfigure(3, weight=1)

        filter_row = tk.Frame(toolbar, bg=CONTROL_MAP_THEME["toolbar"])
        filter_row.grid(row=3, column=0, columnspan=10, sticky="w", pady=(6, 0))
        tk.Checkbutton(filter_row, text="过滤离屏控件", variable=self.var_exclude_offscreen).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(filter_row, text="过滤无标识容器", variable=self.var_exclude_unidentified).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(filter_row, text="自动展开下拉框采选项", variable=self.var_expand_dropdowns).pack(side=tk.LEFT, padx=(0, 12))
        tk.Checkbutton(filter_row, text="移动鼠标实体化选项", variable=self.var_move_cursor).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(filter_row, text="(勾选后采集时自动展开区域内下拉框读取可选项再收回，会真实操作界面，需目标软件处于可交互状态；MUP 扫描时卡死请取消\"移动鼠标实体化选项\")", fg=CONTROL_MAP_THEME["muted"]).pack(side=tk.LEFT)

        hint_row = tk.Frame(toolbar, bg=CONTROL_MAP_THEME["toolbar"])
        hint_row.grid(row=4, column=0, columnspan=10, sticky="w", pady=(4, 0))
        tk.Label(
            hint_row,
            text=(
                "整树采集会自动放宽过滤：保留离屏控件与无标识容器、深度至少提升到 "
                f"{FULLTREE_MIN_DEPTH} 层，并对整窗做网格探针补采（上方过滤开关仅对画框采集生效）。"
            ),
            fg=CONTROL_MAP_THEME["primary"],
        ).pack(side=tk.LEFT)

        supplement_row = tk.Frame(toolbar, bg=CONTROL_MAP_THEME["toolbar"])
        supplement_row.grid(row=5, column=0, columnspan=10, sticky="w", pady=(6, 0))
        self.btn_hover_supplement = tk.Button(
            supplement_row, text="🔴 悬停跟踪补采", command=self.cmd_toggle_hover_supplement, bg=CONTROL_MAP_THEME["danger_soft"]
        )
        self.btn_hover_supplement.pack(side=tk.LEFT, padx=(0, 3))
        # "只看不采"模式开关：开启后悬停仅高亮跟随，不触发补采写库（默认关闭，保持原有行为）
        self.var_look_only = tk.BooleanVar(value=False)
        tk.Checkbutton(
            supplement_row,
            text="👁 只看不采",
            variable=self.var_look_only,
            command=self._on_look_only_toggle,
        ).pack(side=tk.LEFT, padx=(0, 3))
        tk.Button(supplement_row, text="🎯 定点补采子树", command=self.cmd_point_supplement, bg=CONTROL_MAP_THEME["warning_soft"]).pack(side=tk.LEFT, padx=3)
        tk.Button(supplement_row, text="🌱 补采选中控件", command=self.cmd_selected_supplement, bg=CONTROL_MAP_THEME["warning_soft"]).pack(side=tk.LEFT, padx=3)
        tk.Label(supplement_row, text="上溯层级").pack(side=tk.LEFT, padx=(10, 2))
        tk.Spinbox(supplement_row, from_=0, to=8, textvariable=self.var_supplement_climb, width=4).pack(side=tk.LEFT)
        tk.Label(
            supplement_row,
            text="(Inspect 式跟踪：开启后鼠标悬停到哪里就实时补采哪里的子树并同步定位层级树；Esc 或再次点击停止；虚拟化控件需先在目标软件里展开；F6 冻结/解冻，F7 采集入库，Ctrl+Shift+方向键导航)",
            fg=CONTROL_MAP_THEME["warning"],
        ).pack(side=tk.LEFT, padx=(10, 0))

        # 操作按钮分两排排布，避免窗口缩小时单排按钮被遮挡：
        # 第一排：采集 + 勾选操作；第二排：结果处理 + 视图操作（右侧为状态栏）
        button_row1 = tk.Frame(toolbar, bg=CONTROL_MAP_THEME["toolbar"])
        button_row1.grid(row=1, column=0, columnspan=10, sticky="ew", pady=(10, 0))
        self._scan_button_frame1 = button_row1
        tk.Button(button_row1, text="一键整树采集并保存", command=self.cmd_scan_and_save, bg=CONTROL_MAP_THEME["success_soft"]).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row1, text="整树采集预览", command=self.cmd_scan_preview).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row1, text="画框区域采集并保存", command=self.cmd_region_scan_and_save, bg=CONTROL_MAP_THEME["primary_soft"]).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row1, text="画框区域预览", command=self.cmd_region_scan_preview, bg=CONTROL_MAP_THEME["primary_soft"]).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row1, text="智能勾选", command=self.cmd_smart_check_results).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row1, text="全选结果", command=self.cmd_check_all_results).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row1, text="清空勾选", command=self.cmd_clear_checked_results).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row1, text="🗑 清空当前结果", command=self.cmd_clear_current_payload, bg=CONTROL_MAP_THEME["danger_soft"]).pack(side=tk.LEFT, padx=3)
        # 中止采集：仅在扫描进行中可用（_set_scan_ui_busy 控制），点击后置位
        # 取消事件，后台扫描线程在下一遍历检查点响应。
        self._scan_cancel_btn = tk.Button(
            button_row1, text="⏹ 中止采集", command=self.cmd_cancel_scan,
            bg=CONTROL_MAP_THEME["danger_soft"], state=tk.DISABLED,
        )
        self._scan_cancel_btn.pack(side=tk.LEFT, padx=3)

        button_row2 = tk.Frame(toolbar, bg=CONTROL_MAP_THEME["toolbar"])
        button_row2.grid(row=2, column=0, columnspan=10, sticky="ew", pady=(6, 0))
        self._scan_button_frame2 = button_row2
        tk.Button(button_row2, text="📂 加载控件库文件", command=self.cmd_load_control_map_file, bg=CONTROL_MAP_THEME["primary_soft"]).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row2, text="保存当前结果", command=self.cmd_save_current_payload).pack(side=tk.LEFT, padx=3)
        tk.Checkbutton(button_row2, text="保存后自动合并入库", variable=self.var_auto_merge, bg=CONTROL_MAP_THEME["toolbar"]).pack(side=tk.LEFT, padx=(0, 4))
        tk.Button(button_row2, text="打开控件库目录", command=self.cmd_open_control_map_dir).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row2, text="🔍 搜索控件", command=self.cmd_search_controls, bg=CONTROL_MAP_THEME["warning_soft"]).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row2, text="📥 合并入库", command=self.cmd_merge_into_library, bg=CONTROL_MAP_THEME["primary_soft"]).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row2, text="复制所选定位", command=self.cmd_copy_selected_locator).pack(side=tk.LEFT, padx=3)
        tk.Button(button_row2, text="检验定位", command=self.cmd_test_selected_locator, bg=CONTROL_MAP_THEME["primary_soft"]).pack(side=tk.LEFT, padx=3)
        self.var_tree_view_mode = tk.StringVar(value="flat")
        tk.Checkbutton(button_row2, text="层级树视图", variable=self.var_tree_view_mode, onvalue="hierarchy", offvalue="flat", command=self._refresh_tree).pack(side=tk.LEFT, padx=8)
        tk.Button(button_row2, text="展开全部", command=self._cmd_expand_all_tree,
                  font=("Microsoft YaHei UI", 9), padx=6, pady=0).pack(side=tk.LEFT, padx=2)
        tk.Button(button_row2, text="折叠全部", command=self._cmd_collapse_all_tree,
                  font=("Microsoft YaHei UI", 9), padx=6, pady=0).pack(side=tk.LEFT, padx=2)
        tk.Label(button_row2, textvariable=self.var_status, fg=CONTROL_MAP_THEME["muted"]).pack(side=tk.RIGHT)
        tk.Label(button_row2, textvariable=self.var_scan_progress, fg=CONTROL_MAP_THEME["success"]).pack(side=tk.RIGHT, padx=(10, 0))

        # 主内容区：左右两栏，左2/3（控件候选），右1/3（扫描概览 + 控件详情）
        body = tk.Frame(self.root, bg=CONTROL_MAP_THEME["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        body.columnconfigure(0, weight=2)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        left = tk.LabelFrame(body, text="控件候选", padx=10, pady=10,
                             bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"],
                             relief="flat", bd=1, highlightthickness=1,
                             highlightbackground=CONTROL_MAP_THEME["border"],
                             font=(CONTROL_MAP_THEME["font"][0], 11, "bold"))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 5))

        right_container = tk.Frame(body, bg=CONTROL_MAP_THEME["bg"])
        right_container.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        right_container.columnconfigure(0, weight=1)
        right_container.rowconfigure(1, weight=1)

        summary = tk.LabelFrame(right_container, text="扫描概览", padx=10, pady=10,
                                bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"],
                                relief="flat", bd=1, highlightthickness=1,
                                highlightbackground=CONTROL_MAP_THEME["border"],
                                font=(CONTROL_MAP_THEME["font"][0], 11, "bold"))
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        tk.Label(summary, textvariable=self.var_summary, justify=tk.LEFT, anchor="w", wraplength=380).pack(fill=tk.X)

        right = tk.LabelFrame(right_container, text="控件详情", padx=10, pady=10,
                              bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"],
                              relief="flat", bd=1, highlightthickness=1,
                              highlightbackground=CONTROL_MAP_THEME["border"],
                              font=(CONTROL_MAP_THEME["font"][0], 11, "bold"))
        right.grid(row=1, column=0, sticky="nsew")

        # Treeview/滚动条主题样式在 _apply_theme 中统一配置

        self.control_tree = ttk.Treeview(
            left,
            columns=("pick", "seq", "name", "type", "locator", "score", "path"),
            style="ControlMap.Treeview",
            show="tree headings",
        )
        self.control_tree.heading("#0", text="归类")
        self.control_tree.column("#0", width=240, anchor="w", stretch=True, minwidth=180)
        self.control_tree.heading("pick", text="勾选")
        self.control_tree.heading("seq", text="#")
        self.control_tree.heading("name", text="控件")
        self.control_tree.heading("type", text="类型")
        self.control_tree.heading("locator", text="推荐定位")
        self.control_tree.heading("score", text="评分")
        self.control_tree.heading("path", text="路径")
        self.control_tree.column("pick", width=48, anchor="center", stretch=True, minwidth=36)
        self.control_tree.column("seq", width=50, anchor="center", stretch=True, minwidth=36)
        self.control_tree.column("name", width=260, anchor="w", stretch=True, minwidth=180)
        self.control_tree.column("type", width=150, anchor="w", stretch=True, minwidth=100)
        self.control_tree.column("locator", width=300, anchor="w", stretch=True, minwidth=200)
        self.control_tree.column("score", width=60, anchor="center", stretch=True, minwidth=40)
        self.control_tree.column("path", width=460, anchor="w", stretch=True, minwidth=260)
        self.control_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 4))
        self.control_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.control_tree.bind("<Button-1>", self._on_tree_click, add="+")

        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self.control_tree.yview, style="ControlMap.Vertical.TScrollbar")
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scrollbar = ttk.Scrollbar(left, orient="horizontal", command=self.control_tree.xview, style="ControlMap.Horizontal.TScrollbar")
        h_scrollbar.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        self.control_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=h_scrollbar.set)

        rename_frame = tk.LabelFrame(right, text="保存前命名", padx=10, pady=10,
                                     bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"],
                                     relief="flat", bd=1, highlightthickness=1,
                                     highlightbackground=CONTROL_MAP_THEME["border"],
                                     font=(CONTROL_MAP_THEME["font"][0], 11, "bold"))
        rename_frame.pack(fill=tk.X, pady=(0, 8))
        tk.Label(rename_frame, text="保存控件名", bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"]).grid(row=0, column=0, sticky="w")
        tk.Entry(rename_frame, textvariable=self.var_saved_control_name).grid(row=0, column=1, sticky="ew", padx=(8, 12))
        tk.Label(rename_frame, text="控件ID", bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"]).grid(row=0, column=2, sticky="w")
        tk.Entry(rename_frame, textvariable=self.var_saved_control_id).grid(row=0, column=3, sticky="ew", padx=(8, 12))
        tk.Button(rename_frame, text="应用到当前控件", command=self.cmd_apply_current_control_alias, bg=CONTROL_MAP_THEME["success_soft"]).grid(row=0, column=4)
        tk.Label(
            rename_frame,
            text="扫描名通常只是系统原始控件名。保存前可改成业务语义名称，最终只保存已勾选控件。",
            fg=CONTROL_MAP_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
        ).grid(row=1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        rename_frame.columnconfigure(1, weight=1)
        rename_frame.columnconfigure(3, weight=1)

        self.preview_text = scrolledtext.ScrolledText(right, wrap=tk.WORD, font=("Consolas", 10),
                                                      bg=CONTROL_MAP_THEME["panel_soft"], fg=CONTROL_MAP_THEME["text"],
                                                      insertbackground=CONTROL_MAP_THEME["text"],
                                                      relief="flat", bd=1, highlightthickness=1,
                                                      highlightbackground=CONTROL_MAP_THEME["border"])
        self.preview_text.pack(fill=tk.BOTH, expand=True)

    def _resolve_scan_args(self):
        return {
            "window_keyword": self.var_window_keyword.get().strip(),
            "backend": self.var_backend.get().strip() or DEFAULT_BACKEND,
            "use_foreground": self.var_scan_mode.get().strip() == "foreground",
            "max_depth": self.var_max_depth.get(),
            "exclude_offscreen": bool(self.var_exclude_offscreen.get()),
            "exclude_unidentified_containers": bool(self.var_exclude_unidentified.get()),
            "expand_dropdowns": bool(self.var_expand_dropdowns.get()),
            "move_cursor": bool(self.var_move_cursor.get()),
        }

    def _rebuild_control_groups(self):
        self.control_groups = []
        flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        grouped = {}
        ordered_keys = []
        for index, item in enumerate(flat_controls):
            group_key = _build_control_group_key(item)
            if group_key not in grouped:
                grouped[group_key] = {"key": group_key, "memberIndexes": []}
                ordered_keys.append(group_key)
            grouped[group_key]["memberIndexes"].append(index)
        for group_number, group_key in enumerate(ordered_keys, start=1):
            group = grouped[group_key]
            member_indexes = group["memberIndexes"]
            best_index = max(
                member_indexes,
                key=lambda idx: _rank_group_candidate(flat_controls[idx], preferred_index=idx),
            )
            best_item = flat_controls[best_index]
            group_name = _build_group_display_name(best_item, fallback=f"控件组 {group_number}")
            quality_tier = str(best_item.get("qualityTier", "")).strip() or "建议忽略"
            quality_reason = str(best_item.get("qualityReason", "")).strip()
            self.control_groups.append(
                {
                    "key": group_key,
                    "groupNumber": group_number,
                    "groupName": group_name,
                    "memberIndexes": member_indexes,
                    "bestIndex": best_index,
                    "isGrouped": len(member_indexes) >= 2,
                    "qualityTier": quality_tier,
                    "qualityReason": quality_reason,
                }
            )

    def _find_group_by_index(self, index):
        for group in self.control_groups:
            if index in group.get("memberIndexes", []):
                return group
        return None

    def _build_default_checked_indices(self):
        smart_checked = {
            group.get("bestIndex")
            for group in self.control_groups
            if group.get("bestIndex") is not None and _should_default_select_group(group)
        }
        if smart_checked:
            return smart_checked
        return {group.get("bestIndex") for group in self.control_groups if group.get("bestIndex") is not None}

    def _find_group_by_tree_iid(self, tree_iid):
        tree_iid = str(tree_iid or "")
        if tree_iid.startswith("group:"):
            target_key = tree_iid.split(":", 1)[1]
            for group in self.control_groups:
                if group.get("key") == target_key:
                    return group
            return None
        if tree_iid.startswith("item:"):
            try:
                index = int(tree_iid.split(":", 1)[1])
            except Exception:
                return None
            return self._find_group_by_index(index)
        return None

    def _get_excluded_scan_context(self):
        process_id = str(os.getpid())
        titles = {str(self.root.title()).strip(), "WT 控件库采集器"}
        return {
            "excluded_process_ids": [process_id],
            "excluded_titles": [item for item in titles if item],
        }

    def _set_scan_ui_busy(self, busy):
        """扫描进行中禁用两排操作按钮，仅保留「中止采集」可用；结束/中止后恢复。"""
        if busy:
            if self._scan_disabled_widgets:
                return
            for frame in (
                getattr(self, "_scan_button_frame1", None),
                getattr(self, "_scan_button_frame2", None),
            ):
                if frame is None:
                    continue
                for child in frame.winfo_children():
                    if not isinstance(child, tk.Button):
                        continue
                    if child is self._scan_cancel_btn:
                        continue
                    try:
                        if str(child.cget("state")) != tk.DISABLED:
                            child.configure(state=tk.DISABLED)
                            self._scan_disabled_widgets.append(child)
                    except Exception:
                        pass
            if self._scan_cancel_btn is not None:
                try:
                    self._scan_cancel_btn.configure(text="⏹ 中止采集", state=tk.NORMAL)
                except Exception:
                    pass
        else:
            for widget in self._scan_disabled_widgets:
                try:
                    widget.configure(state=tk.NORMAL)
                except Exception:
                    pass
            self._scan_disabled_widgets = []
            if self._scan_cancel_btn is not None:
                try:
                    self._scan_cancel_btn.configure(text="⏹ 中止采集", state=tk.DISABLED)
                except Exception:
                    pass

    def cmd_cancel_scan(self):
        """中止当前扫描：置位取消事件，后台扫描线程在下一遍历检查点停止。"""
        if not getattr(self, "_scan_running", False):
            return
        event = getattr(self, "_scan_cancel_event", None)
        if event is not None:
            event.set()
        self.var_status.set("正在中止采集（等待当前遍历返回）...")
        if self._scan_cancel_btn is not None:
            try:
                self._scan_cancel_btn.configure(text="⏹ 正在中止…", state=tk.DISABLED)
            except Exception:
                pass
        # 兜底：若后台线程被永久阻塞的 UIA 调用拖住（目标进程无响应），
        # 15 秒后恢复界面按钮，避免整个采集器保持"半冻结"；此时可关闭窗口重开。
        try:
            self.root.after(15000, self._recover_ui_after_cancel_stall)
        except Exception:
            pass

    def _recover_ui_after_cancel_stall(self):
        """中止兜底：扫描线程未按时返回时恢复界面，让用户可以关闭/重开采集器。"""
        if not getattr(self, "_scan_running", False):
            return
        self._set_scan_ui_busy(False)
        self.var_status.set(
            "扫描线程仍在等待目标进程响应，已恢复界面；可关闭本窗口后重新打开采集器。"
        )

    def _run_scan(self, auto_save, region_rect=None):
        """扫描入口：主线程启动后台扫描线程后立即返回，UI 保持响应。

        后台线程执行 build_control_map_payload（UIA 遍历可能因目标进程忙碌而
        长时间阻塞，甚至永久挂起，此前主线程同步执行会把整个采集器窗口拖成
        "未响应"）。完成后经 root.after 回主线程更新界面，扫描期间可随时点
        「中止采集」。
        """
        if getattr(self, "_scan_running", False):
            self.var_status.set("已有扫描正在进行，请先中止或等待完成。")
            return
        self._scan_running = True
        self._scan_cancel_event = threading.Event()
        try:
            args = self._resolve_scan_args()
            args.update(self._get_excluded_scan_context())
            args["region_rect"] = region_rect
            args["status_callback"] = self._update_scan_progress
            args["cancel_event"] = self._scan_cancel_event
            self._set_scan_ui_busy(True)
            self.var_status.set("正在启动扫描...")
            # 立即显示置顶浮窗，让用户最小化窗口后也能立刻看到采集已开始
            try:
                self._scan_progress_overlay.show_progress("正在启动扫描...", 0)
            except Exception:
                pass
            threading.Thread(
                target=self._run_scan_worker,
                args=(auto_save, region_rect, args),
                daemon=True,
                name="control-map-scan",
            ).start()
        except Exception as exc:
            # 启动失败（如参数解析异常）：复位状态并还原窗口，避免一直最小化/隐藏
            self._scan_running = False
            self._scan_cancel_event = None
            try:
                self._set_scan_ui_busy(False)
            except Exception:
                pass
            self.var_status.set(f"扫描启动失败：{exc}")
            try:
                self.root.deiconify()
                self.root.lift()
            except Exception:
                pass

    def _run_scan_worker(self, auto_save, region_rect, args):
        """后台线程：执行扫描，结果/异常经 root.after 交回主线程。"""
        com_init = False
        try:
            import pythoncom
            pythoncom.CoInitialize()
            com_init = True
        except Exception:
            pass
        try:
            payload = build_control_map_payload(**args)
            error = None
            cancelled = bool(
                getattr(self, "_scan_cancel_event", None) and self._scan_cancel_event.is_set()
            )
        except Exception as exc:
            payload = None
            error = exc
            cancelled = bool(
                getattr(self, "_scan_cancel_event", None) and self._scan_cancel_event.is_set()
            )
        if com_init:
            try:
                import pythoncom
                pythoncom.CoUninitialize()
            except Exception:
                pass
        try:
            self.root.after(0, lambda: self._on_scan_finished(auto_save, region_rect, payload, error, cancelled))
        except Exception:
            pass  # 窗口已关闭

    def _on_scan_finished(self, auto_save, region_rect, payload, error, cancelled):
        """主线程完成回调：恢复 UI、更新树、按需保存。"""
        self._scan_running = False
        self._set_scan_ui_busy(False)
        # 扫描期间采集器保持最小化不抢目标前台，完成后还原窗口展示结果
        try:
            self.root.deiconify()
            self.root.lift()
        except Exception:
            pass
        if error is not None:
            messagebox.showerror("扫描失败", f"控件树扫描失败：\n{error}")
            self.var_status.set(f"扫描失败：{error}")
            try:
                self._scan_progress_overlay.show_error(f"扫描失败：{str(error)[:80]}")
                _play_notify_sound()
            except Exception:
                pass
            try:
                self._taskbar_progress.clear()
            except Exception:
                pass
            return

        self.current_payload = payload
        self.current_output_path = ""
        self.current_region_rect = _normalize_rect_dict(region_rect)
        self._rebuild_control_groups()
        self._all_checked_mode = False
        self.checked_control_indices = self._build_default_checked_indices()
        self.var_saved_control_name.set("")
        self.var_saved_control_id.set("")
        if not cancelled and auto_save:
            try:
                filtered_payload = self._build_filtered_payload_for_save()
                self.current_output_path = save_control_map_payload(filtered_payload)
            except Exception as exc:
                messagebox.showerror("保存失败", f"扫描完成，但保存控件库失败：\n{exc}")
                self.var_status.set(f"扫描完成，但保存失败：{exc}")
        self._refresh_tree()
        self._refresh_summary()
        # 采集完成，恢复窗口标题，清除任务栏进度
        self.root.title("WT 控件库采集器")
        try:
            self._taskbar_progress.clear()
        except Exception:
            pass
        if cancelled:
            self.var_status.set("扫描已中止。已保留部分结果，未自动保存；可再次采集。")
            try:
                self._scan_progress_overlay.show_cancelled("采集已中止：已保留部分结果，未自动保存")
                _play_notify_sound()
            except Exception:
                pass
        elif auto_save and self.current_output_path:
            self.var_status.set(f"已完成控件库扫描并保存：{self.current_output_path}")
            try:
                total = (payload.get("scanMeta", {}) or {}).get("totalControls", 0)
                self._scan_progress_overlay.show_done(
                    f"采集完成：共 {total} 个控件\n已保存：{self.current_output_path}"
                )
                _play_notify_sound()
            except Exception:
                pass
            self._maybe_auto_merge_after_save()
        elif self.current_region_rect:
            self.var_status.set("已完成画框区域控件扫描，当前结果尚未保存。")
            try:
                self._scan_progress_overlay.show_done("采集完成：画框区域控件扫描完成")
                _play_notify_sound()
            except Exception:
                pass
        else:
            self.var_status.set("已完成控件树扫描，当前结果尚未保存。")
            try:
                self._scan_progress_overlay.show_done("采集完成：控件树扫描完成")
                _play_notify_sound()
            except Exception:
                pass

    def _refresh_summary(self):
        if not isinstance(self.current_payload, dict):
            self.var_summary.set("尚未扫描控件树。")
            return
        target_window = self.current_payload.get("targetWindow", {}) or {}
        scan_meta = self.current_payload.get("scanMeta", {}) or {}
        summary = [
            f"目标窗口：{target_window.get('title', '')}",
            f"类名：{target_window.get('className', '')}",
            f"backend：{scan_meta.get('backend', '')}",
            f"最大深度：{scan_meta.get('maxDepth', '')}",
            f"控件总数：{scan_meta.get('totalControls', 0)}",
            f"当前勾选保存：{len(self.checked_control_indices)}",
        ]
        grouped_count = sum(1 for group in self.control_groups if group.get("isGrouped"))
        single_count = sum(1 for group in self.control_groups if not group.get("isGrouped"))
        summary.append(f"归类控件组：{grouped_count}")
        summary.append(f"未归类单项：{single_count}")
        if self.control_groups:
            tier_summary = {}
            for group in self.control_groups:
                tier = str(group.get("qualityTier", "")).strip() or "未分类"
                tier_summary[tier] = tier_summary.get(tier, 0) + 1
            summary.append("质量分级：" + ", ".join(f"{key}={value}" for key, value in tier_summary.items()))
        merged_backends = scan_meta.get("mergedBackends", []) or []
        if merged_backends:
            summary.append(f"实际采集后端：{', '.join(merged_backends)}")
        supplements = scan_meta.get("supplementScans", []) or []
        if supplements:
            total_added = sum(int(entry.get("added", 0) or 0) for entry in supplements)
            summary.append(f"定点补采：{len(supplements)} 次，累计新增 {total_added} 个控件")
        if scan_meta.get("rawTotalControls", 0) and scan_meta.get("rawTotalControls", 0) != scan_meta.get("totalControls", 0):
            summary.append(f"整窗原始控件数：{scan_meta.get('rawTotalControls', 0)}")
        truncation = scan_meta.get("truncation")
        if isinstance(truncation, dict):
            reasons = []
            if truncation.get("bfsTimedOut"):
                reasons.append("遍历超时")
            if truncation.get("bfsHitElementLimit"):
                reasons.append(f"达到元素上限 {truncation.get('elementLimit', '')}")
            if reasons:
                summary.append(f"⚠ 采集被截断（{'、'.join(reasons)}），结果可能不全，缺失区域请用悬停/定点补采")
        region_rect = scan_meta.get("regionRect")
        if region_rect:
            summary.append(
                "采集区域："
                f"({region_rect.get('left')},{region_rect.get('top')})"
                f"-({region_rect.get('right')},{region_rect.get('bottom')})"
            )
        else:
            relaxation = scan_meta.get("fullTreeRelaxation")
            if isinstance(relaxation, dict):
                depth_pair = relaxation.get("maxDepth") or []
                depth_note = ""
                if isinstance(depth_pair, list) and len(depth_pair) == 2 and depth_pair[0] != depth_pair[1]:
                    depth_note = f"，深度 {depth_pair[0]}→{depth_pair[1]}"
                summary.append(f"整树模式：已自动放宽过滤（保留离屏控件/无标识容器{depth_note}）")
        if self.current_output_path:
            summary.append(f"保存路径：{self.current_output_path}")
        type_summary = scan_meta.get("controlTypeSummary", {}) or {}
        if type_summary:
            top_types = ", ".join(f"{key}={value}" for key, value in list(type_summary.items())[:8])
            summary.append(f"类型分布：{top_types}")
        self.var_summary.set("\n".join(summary))

    def _cmd_expand_all_tree(self):
        """展开控件树全部节点。"""
        for item in self._all_tree_items(self.control_tree):
            self.control_tree.item(item, open=True)

    def _cmd_collapse_all_tree(self):
        """折叠控件树全部节点。"""
        for item in self._all_tree_items(self.control_tree):
            self.control_tree.item(item, open=False)

    @staticmethod
    def _all_tree_items(tree, parent=""):
        """递归获取树视图的所有条目 iid（深度优先）。"""
        items = []
        for child in tree.get_children(parent):
            items.append(child)
            items.extend(ControlMapBuilderApp._all_tree_items(tree, child))
        return items

    def _refresh_tree(self):
        """根据视图模式刷新控件树：扁平分组视图或层级树视图。"""
        self.control_tree.delete(*self.control_tree.get_children())
        mode = getattr(self, "var_tree_view_mode", tk.StringVar(value="flat")).get()
        # #0 树列标题随视图语义切换，避免层级树下仍显示"归类"
        self.control_tree.heading("#0", text="层级结构" if mode == "hierarchy" else "归类")
        if not isinstance(self.current_payload, dict):
            return
        if mode == "hierarchy":
            self._refresh_hierarchical_tree()
        else:
            self._refresh_flat_tree()

    def _refresh_flat_tree(self):
        """扁平分组视图：按控件组展示。"""
        flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        for group in self.control_groups:
            member_indexes = group.get("memberIndexes", [])
            best_index = group.get("bestIndex")
            checked_count = sum(1 for index in member_indexes if index in self.checked_control_indices)
            best_item = flat_controls[best_index] if best_index is not None and best_index < len(flat_controls) else {}
            if not group.get("isGrouped") and member_indexes:
                index = member_indexes[0]
                item = flat_controls[index]
                locator = f"{item.get('recommendedTargetMethod', '')}:{item.get('recommendedTargetValue', '')}".strip(":")
                display_name = _display_control_name(item) + _option_values_hint(item)
                tier_label = str(item.get("qualityTier", "")).strip() or "未分类"
                self.control_tree.insert(
                    "",
                    tk.END,
                    iid=f"item:{index}",
                    text=tier_label,
                    values=(
                        "[x]" if index in self.checked_control_indices else "[ ]",
                        index + 1,
                        display_name,
                        f"{item.get('controlType', '') or 'Unknown'}@{item.get('scanBackend', '') or '-'}",
                        locator,
                        item.get("locatorScore", 0),
                        item.get("uiPath", ""),
                    ),
                )
                continue
            group_iid = f"group:{group.get('key')}"
            group_type = str(best_item.get("controlType", "")).strip() or "Unknown"
            tier_label = str(group.get("qualityTier", "")).strip() or "建议忽略"
            self.control_tree.insert(
                "",
                tk.END,
                iid=group_iid,
                open=True,
                text=f"{tier_label} | 第 {group.get('groupNumber')} 组",
                values=(
                    "[x]" if checked_count == len(member_indexes) and member_indexes else "[ ]",
                    len(member_indexes),
                    group.get("groupName", ""),
                    f"{group_type} | 最优 {int(best_item.get('locatorScore', 0) or 0)}",
                    str(group.get("qualityReason", "")).strip() or "同组候选",
                    checked_count,
                    str(best_item.get("uiPath", "")).strip(),
                ),
            )
            for child_order, index in enumerate(member_indexes, start=1):
                item = flat_controls[index]
                locator = f"{item.get('recommendedTargetMethod', '')}:{item.get('recommendedTargetValue', '')}".strip(":")
                display_name = _display_control_name(item) + _option_values_hint(item)
                child_label = ("推荐保留" if index == best_index else f"候选 {child_order}") + f" | {item.get('qualityTier', '') or '未分类'}"
                self.control_tree.insert(
                    group_iid,
                    tk.END,
                    iid=f"item:{index}",
                    text=child_label,
                    values=(
                        "[x]" if index in self.checked_control_indices else "[ ]",
                        index + 1,
                        display_name,
                        f"{item.get('controlType', '') or 'Unknown'}@{item.get('scanBackend', '') or '-'}",
                        locator,
                        item.get("locatorScore", 0),
                        item.get("uiPath", ""),
                    ),
                )

    def _refresh_hierarchical_tree(self):
        """层级树视图：展示真实父子关系。"""
        control_tree = self.current_payload.get("controlsTree", {}) if isinstance(self.current_payload, dict) else {}
        self._hierarchy_nodes_by_iid = {}
        # 树节点常缺 suggestedControlName 等语义字段，建 index→flat 条目映射供展示名回退
        self._hierarchy_flat_by_index = {}
        if isinstance(self.current_payload, dict):
            for f in self.current_payload.get("flatControls") or []:
                if isinstance(f, dict) and f.get("index") is not None:
                    try:
                        self._hierarchy_flat_by_index.setdefault(int(f["index"]), f)
                    except Exception:
                        pass
        if not control_tree:
            return
        self._insert_hierarchy_node(control_tree)

    def _node_option_hint(self, node):
        """层级树节点的选项后缀：节点自带 optionValues 直接用，否则按 flatIndex
        反查 flatControls（controlsTree 在展开下拉框之前构建，节点不含 optionValues）。"""
        hint = _option_values_hint(node)
        if hint:
            return hint
        try:
            flat_index = int(node.get("flatIndex", -1))
        except Exception:
            return ""
        if flat_index >= 0:
            flats = (self.current_payload or {}).get("flatControls") or []
            if flat_index < len(flats):
                return _option_values_hint(flats[flat_index])
        return ""

    def _insert_hierarchy_node(self, node, parent_iid="", depth=0, open_depth=2):
        """递归插入一个 controlsTree 节点及其子树，返回根 iid。

        全量刷新与补采后的增量子树替换共用此方法；iid 用单调递增序号保证全局唯一，
        节点实体（controlsTree 节点引用）通过 _hierarchy_nodes_by_iid 反查。
        """
        if not isinstance(node, dict):
            return None
        # 构建显示名称
        name = str(node.get("name", "")).strip()
        control_type = str(node.get("controlType", "")).strip() or "Unknown"
        auto_id = str(node.get("automationId", "")).strip()
        display_name = name or auto_id or f"[{control_type}]"
        # 透明容器标记；定点补采新增节点用 ✦ 区分
        is_transparent = node.get("isTransparentContainer", False)
        if node.get("supplementSource"):
            prefix = "✦"
        else:
            prefix = "○" if is_transparent else "●"
        self._hierarchy_iid_seq += 1
        iid = f"hierarchy:{self._hierarchy_iid_seq}"
        self._hierarchy_nodes_by_iid[iid] = node
        # values 与扁平视图共用同一套 7 列语义：勾选/#/控件/类型/推荐定位/评分/路径，
        # 避免切到层级树后列标题与内容错位
        try:
            flat_index = int(node.get("flatIndex", -1))
        except Exception:
            flat_index = -1
        has_flat = flat_index >= 0
        # 语义展示名：节点自身 functionText/功能名/已存语义名优先；
        # 树节点常缺这些字段（如 suggestedControlName），回退到对应 flat 条目的语义名，
        # 避免显示成 PART_DropDownButton / 原始数值等无法辨认的名字
        node_semantic = (
            str(node.get("functionText", "")).strip()
            or _extract_functional_name(node)
            or str(node.get("savedControlName", "")).strip()
            or str(node.get("suggestedControlName", "")).strip()
        )
        flat_display = ""
        if not node_semantic:
            try:
                flat_item = self._hierarchy_flat_by_index.get(int(node.get("index", -1)))
            except Exception:
                flat_item = None
            if flat_item:
                flat_display = _display_control_name(flat_item)
        full_display = (
            node_semantic
            or flat_display
            or str(node.get("displayName", "")).strip()
            or display_name
        ) + self._node_option_hint(node)
        scan_backend = str(node.get("scanBackend", "")).strip() or "-"
        locator = f"{node.get('recommendedTargetMethod', '')}:{node.get('recommendedTargetValue', '')}".strip(":")
        # 插入节点
        self.control_tree.insert(
            parent_iid,
            tk.END,
            iid=iid,
            open=(depth < open_depth),
            text=f"{prefix} {display_name}",
            values=(
                ("[x]" if flat_index in self.checked_control_indices else "[ ]") if has_flat else "",
                flat_index + 1 if has_flat else "",
                full_display,
                f"{control_type}@{scan_backend}",
                locator or "-",
                node.get("locatorScore", 0) if has_flat else "",
                str(node.get("uiPath", "")).strip(),
            ),
        )
        # 递归插入子节点
        for child in node.get("children", []) or []:
            self._insert_hierarchy_node(child, iid, depth + 1, open_depth=open_depth)
        return iid

    def _get_hierarchy_identity_index(self):
        """构建并缓存层级树节点的 identity 反查索引（runtimeId/签名 → iid，O(1) 查找）。

        悬停查看层每次元素切换都要反查层级节点，旧实现逐节点扫描数千个节点，
        是悬停聚焦卡顿的主因之一。此处与 _get_flat_identity_maps 同款缓存键
        (id(dict), len(dict))：重建树换新 dict→id 变；补采增量插入→len 变，
        两种变更都能自动失效（别名编辑不改 identity 字段，无需失效）。
        """
        nodes = self._hierarchy_nodes_by_iid
        cache = getattr(self, "_hierarchy_index_cache", None)
        cache_key = (id(nodes), len(nodes))
        if cache is not None and cache[0] == cache_key:
            return cache[1], cache[2]
        runtime_index = {}
        sig_index = {}
        for iid, node in nodes.items():
            if not isinstance(node, dict):
                continue
            runtime = str(node.get("runtimeId", "")).strip()
            if runtime and runtime not in runtime_index:
                runtime_index[runtime] = iid
            _, sig, sig_usable = _extract_identity_match_keys(node)
            if sig_usable and sig not in sig_index:
                sig_index[sig] = iid
        self._hierarchy_index_cache = (cache_key, runtime_index, sig_index)
        return runtime_index, sig_index

    def _locate_hierarchy_iid_by_identity(self, item):
        """在当前层级树视图中反查节点 iid。

        匹配策略（逐步回退）：
        1. runtimeId 精确匹配（最可靠，但重启后失效）
        2. 多字段签名匹配（name+className+controlType，稳定）
        3. uiPath 模糊匹配（父子路径安全，签名退化时的兜底）

        runtimeId/签名走 _get_hierarchy_identity_index 的 O(1) 缓存索引；
        uiPath 仅在签名退化（name/className/controlType 全空）时逐节点扫描。
        """
        if not isinstance(item, dict):
            return None
        target_runtime, target_sig, sig_usable = _extract_identity_match_keys(item)
        target_ui_path = str(item.get("uiPath", "") or item.get("inspectData", {}).get("uiPath", "")).strip()

        runtime_index, sig_index = self._get_hierarchy_identity_index()
        runtime_iid = runtime_index.get(target_runtime) if target_runtime else None
        sig_iid = sig_index.get(target_sig) if sig_usable else None

        ui_path_iid = None
        if not sig_usable and target_ui_path:
            for iid, node in self._hierarchy_nodes_by_iid.items():
                if not isinstance(node, dict):
                    continue
                node_ui_path = str(node.get("uiPath", "") or node.get("inspectData", {}).get("uiPath", "")).strip()
                if node_ui_path and node_ui_path == target_ui_path:
                    ui_path_iid = iid
                    break
        # exists() 是跨 Tcl 调用，只对最终候选验证
        for candidate in (runtime_iid, sig_iid, ui_path_iid):
            if candidate and self.control_tree.exists(candidate):
                return candidate
        return None

    def _sync_hierarchy_after_supplement(self, anchor_item):
        """补采合并后增量同步层级树：只重建锚点子树（结构更新，定位高亮由调用方统一处理）。

        相比全量 _refresh_tree，保留其余节点的展开状态且性能恒定，是 Inspect 式
        悬停跟踪的核心体验。返回是否同步成功（失败时调用方应回退全量刷新）。
        """
        if self.var_tree_view_mode.get() != "hierarchy":
            return False
        iid = self._locate_hierarchy_iid_by_identity(anchor_item)
        if not iid:
            return False
        node = self._hierarchy_nodes_by_iid.get(iid)
        tree_root = self.current_payload.get("controlsTree", {}) if isinstance(self.current_payload, dict) else {}
        payload_anchor = _find_tree_node_by_identity(tree_root, anchor_item)
        # GUI 映射的节点必须与 payload 树中锚点匹配（身份签名一致），
        # 否则说明树结构已重建，增量更新会刷到旧对象上，退回全量刷新
        if payload_anchor is None or not _nodes_identity_match(node, payload_anchor):
            return False
        # 删除锚点旧子项并清理失效 iid 映射，再插入最新子树
        for child_iid in self.control_tree.get_children(iid):
            self.control_tree.delete(child_iid)
        self._hierarchy_nodes_by_iid = {
            key: value for key, value in self._hierarchy_nodes_by_iid.items() if self.control_tree.exists(key)
        }
        for child in node.get("children", []) or []:
            self._insert_hierarchy_node(child, iid, depth=1, open_depth=2)
        return True

    def _on_tree_click(self, event):
        row_id = self.control_tree.identify_row(event.y)
        column_id = self.control_tree.identify_column(event.x)
        if not row_id:
            return
        if column_id == "#1":
            if str(row_id).startswith("group:"):
                group = self._find_group_by_tree_iid(row_id)
                if group:
                    self._toggle_group_checked(group)
            elif str(row_id).startswith("item:"):
                self._toggle_checked_index(int(str(row_id).split(":", 1)[1]))
            elif str(row_id).startswith("hierarchy:"):
                node = self._hierarchy_nodes_by_iid.get(str(row_id))
                index = self._resolve_flat_index_for_node(node)
                if index is not None:
                    self._toggle_checked_index(index)
            return "break"

    def _resolve_flat_index_for_node(self, node):
        """把层级树节点解析为 flatControls 下标：flatIndex 优先，identity 校验，失败时全表兜底。"""
        if not isinstance(node, dict) or not isinstance(self.current_payload, dict):
            return None
        flat = self.current_payload.get("flatControls", []) or []
        if not flat:
            return None
        runtime, sig, sig_usable = _extract_identity_match_keys(node)

        def _same(item):
            if not isinstance(item, dict):
                return False
            item_runtime, item_sig, _ = _extract_identity_match_keys(item)
            if runtime and item_runtime and runtime == item_runtime:
                return True
            return bool(sig_usable and item_sig == sig)

        try:
            index = int(node.get("flatIndex", -1))
        except Exception:
            index = -1
        if 0 <= index < len(flat) and _same(flat[index]):
            return index
        for i, item in enumerate(flat):
            if _same(item):
                return i
        return None

    def _toggle_checked_index(self, index):
        if index in self.checked_control_indices:
            self.checked_control_indices.remove(index)
        else:
            self.checked_control_indices.add(index)
        flat = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        flat_item = flat[index] if 0 <= index < len(flat) else None
        self._refresh_tree()
        # 恢复选中：扁平视图 iid 确定性重建；层级视图 iid 刷新后重排，按 identity 重新定位
        if self.var_tree_view_mode.get() == "hierarchy":
            iid = self._locate_hierarchy_iid_by_identity(flat_item) if isinstance(flat_item, dict) else None
            if iid:
                self.control_tree.selection_set(iid)
        else:
            iid = f"item:{index}"
            if self.control_tree.exists(iid):
                self.control_tree.selection_set(iid)
        self._refresh_summary()

    def _toggle_group_checked(self, group):
        member_indexes = list(group.get("memberIndexes", []))
        if not member_indexes:
            return
        if all(index in self.checked_control_indices for index in member_indexes):
            for index in member_indexes:
                self.checked_control_indices.discard(index)
        else:
            for index in member_indexes:
                self.checked_control_indices.add(index)
        current_selection = self.control_tree.selection()
        self._refresh_tree()
        if current_selection:
            self.control_tree.selection_set(current_selection)
        self._refresh_summary()

    def _on_tree_select(self, _event=None):
        selection = self.control_tree.selection()
        if not selection or not isinstance(self.current_payload, dict):
            return
        selected_iid = selection[0]
        if str(selected_iid).startswith("hierarchy:"):
            # 层级树节点：直接用节点实体预览（flatIndex 在补采合并后可能跨下标空间，不可直接反查）
            node = self._hierarchy_nodes_by_iid.get(str(selected_iid))
            if not isinstance(node, dict):
                return
            self.var_saved_control_name.set(
                str(node.get("savedControlName", "")).strip()
                or str(node.get("suggestedControlName", "")).strip()
                or str(node.get("displayName", "")).strip()
            )
            self.var_saved_control_id.set(
                str(node.get("savedControlId", "")).strip()
                or _build_saved_control_id_from_name(
                    str(node.get("suggestedControlName", "")).strip() or str(node.get("displayName", "")).strip(),
                    fallback="control",
                )
            )
            preview = {
                "qualityTier": node.get("qualityTier", ""),
                "qualityReason": node.get("qualityReason", ""),
                "supplementSource": node.get("supplementSource", ""),
                "suggestedControlName": node.get("suggestedControlName", ""),
                "flatControl": {key: value for key, value in node.items() if key != "children"},
            }
            self.preview_text.delete("1.0", tk.END)
            self.preview_text.insert("1.0", json.dumps(preview, ensure_ascii=False, indent=2))
            return
        if str(selected_iid).startswith("group:"):
            group = self._find_group_by_tree_iid(selected_iid)
            if not group:
                return
            flat_controls = self.current_payload.get("flatControls", [])
            best_index = group.get("bestIndex")
            if best_index is None or best_index >= len(flat_controls):
                return
            index = best_index
        elif str(selected_iid).startswith("item:"):
            index = int(str(selected_iid).split(":", 1)[1])
        else:
            return
        flat_controls = self.current_payload.get("flatControls", [])
        if index >= len(flat_controls):
            return
        item = flat_controls[index]
        control_definition = {}
        if index < len(self.current_payload.get("controlDefinitions", [])):
            control_definition = self.current_payload.get("controlDefinitions", [])[index]
        self.var_saved_control_name.set(
            str(control_definition.get("name", "")).strip()
            or str(item.get("savedControlName", "")).strip()
            or str(item.get("suggestedControlName", "")).strip()
            or str(item.get("displayName", "")).strip()
        )
        self.var_saved_control_id.set(
            str(control_definition.get("id", "")).strip()
            or str(item.get("savedControlId", "")).strip()
            or _build_saved_control_id_from_name(
                str(control_definition.get("name", "")).strip()
                or str(item.get("savedControlName", "")).strip()
                or str(item.get("suggestedControlName", "")).strip()
                or str(item.get("displayName", "")).strip(),
                fallback=f"control_{index + 1}",
            )
        )
        preview = {
            "qualityTier": item.get("qualityTier", ""),
            "qualityReason": item.get("qualityReason", ""),
            "automatabilityRisk": item.get("automatabilityRisk", ""),
            "automatabilityReasons": item.get("automatabilityReasons", []),
            "suggestedControlName": item.get("suggestedControlName", ""),
            "flatControl": item,
            "controlDefinition": control_definition,
        }
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", json.dumps(preview, ensure_ascii=False, indent=2))

    def _get_selected_tree_index(self):
        selection = self.control_tree.selection()
        if not selection:
            return None
        selected_iid = str(selection[0])
        if selected_iid.startswith("group:"):
            group = self._find_group_by_tree_iid(selected_iid)
            return None if not group else group.get("bestIndex")
        if selected_iid.startswith("item:"):
            try:
                return int(selected_iid.split(":", 1)[1])
            except Exception:
                return None
        if selected_iid.startswith("hierarchy:"):
            # 层级树节点：按 identity 反查 flatControls 下标（支持检验定位/复制定位/补采）
            node = self._hierarchy_nodes_by_iid.get(selected_iid)
            if not isinstance(node, dict):
                return None
            return self._find_flat_index_by_identity(node)
        return None

    def _find_flat_index_by_identity(self, item):
        """按 identity 在当前 flatControls 中反查下标，未命中返回 None。"""
        if not isinstance(self.current_payload, dict) or not isinstance(item, dict):
            return None
        target_identity = _build_flat_control_identity(item)
        for index, flat_item in enumerate(self.current_payload.get("flatControls", []) or []):
            if _build_flat_control_identity(flat_item) == target_identity:
                return index
        return None

    def _show_locator_highlight(self, rect, duration_ms=3000):
        """用置顶覆盖层高亮实际命中的屏幕区域（与控件库维护同款）。"""
        try:
            left = int(rect.left)
            top = int(rect.top)
            width = max(4, int(rect.right - rect.left))
            height = max(4, int(rect.bottom - rect.top))
        except Exception:
            return False

        try:
            old_overlay = getattr(self, "_locator_highlight_window", None)
            if old_overlay is not None and old_overlay.winfo_exists():
                old_overlay.destroy()
        except Exception:
            pass

        try:
            overlay = tk.Toplevel(self.root)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            # 高亮框套住目标控件的真实屏幕 rect，绕过 DPI 缩放，否则框会偏移放大
            wt_dpi.raw_geometry(overlay, f"{width}x{height}+{left}+{top}")
            try:
                overlay.attributes("-transparentcolor", "magenta")
                canvas = tk.Canvas(overlay, bg="magenta", highlightthickness=0, bd=0)
                canvas.pack(fill=tk.BOTH, expand=True)
                canvas.create_rectangle(2, 2, width - 2, height - 2, outline="#ff2020", width=4)
            except Exception:
                overlay.attributes("-alpha", 0.35)
                canvas = tk.Canvas(overlay, bg="#ff2020", highlightthickness=0, bd=0)
                canvas.pack(fill=tk.BOTH, expand=True)
            overlay.protocol("WM_DELETE_WINDOW", overlay.destroy)
            self._locator_highlight_window = overlay
            overlay.after(duration_ms, overlay.destroy)
            return True
        except Exception:
            return False

    def cmd_test_selected_locator(self):
        """使用流程执行器同一套定位规则检验并指向实际命中控件。

        定位搜索在独立子进程（control_locator_probe.py）中执行：
        UIA 遍历遇到失效元素指针触发原生堆损坏时，只终止探针子进程，
        采集器主窗口不受影响（此前在线程内直接跑会发生 0xc0000374 崩溃关窗）。
        """
        index = self._get_selected_tree_index()
        flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        if index is None or not (0 <= index < len(flat_controls)):
            messagebox.showinfo("提示", "请先选择一个具体控件或控件组。", parent=self.root)
            return

        item = flat_controls[index]
        control_definition = {}
        definitions = self.current_payload.get("controlDefinitions", []) if isinstance(self.current_payload, dict) else []
        if index < len(definitions) and isinstance(definitions[index], dict):
            control_definition = dict(definitions[index])

        control = dict(item)
        control.update({key: value for key, value in control_definition.items() if value not in (None, "")})
        inspect_data = control.get("inspectData") if isinstance(control.get("inspectData"), dict) else {}
        control["name"] = (
            str(control.get("name", "")).strip()
            or str(control.get("displayName", "")).strip()
            or str(inspect_data.get("name", "")).strip()
        )
        control["targetMethod"] = (
            str(control.get("targetMethod", "")).strip()
            or str(control.get("recommendedTargetMethod", "")).strip()
            or str(inspect_data.get("recommendedTargetMethod", "")).strip()
        )
        control["targetValue"] = (
            str(control.get("targetValue", "")).strip()
            or str(control.get("recommendedTargetValue", "")).strip()
            or str(inspect_data.get("recommendedTargetValue", "")).strip()
        )
        target_window = self.current_payload.get("targetWindow", {}) if isinstance(self.current_payload, dict) else {}
        if not control.get("windowTitle") and isinstance(target_window, dict):
            control["windowTitle"] = str(target_window.get("title", "")).strip()

        control["inspectData"] = inspect_data
        if not control["targetMethod"] and not inspect_data:
            self.var_status.set("⚠ 检验定位：该控件没有可用于定位的属性")
            return

        self.var_status.set("⏳ 正在检验定位（独立进程执行中）...")
        self.root.update_idletasks()
        self._launch_locator_probe(control)

    _PROBE_WAIT_SECONDS = 90.0

    def _launch_locator_probe(self, control):
        """在子进程中执行定位搜索；探针崩溃只影响自身，主界面不受影响。"""
        base_dir = os.path.dirname(os.path.abspath(__file__))
        probe_script = os.path.join(base_dir, "control_locator_probe.py")
        if not os.path.isfile(probe_script):
            self.var_status.set("⚠ 检验定位：探针脚本缺失：" + probe_script)
            return
        tmp_dir = os.path.join(base_dir, "logs", "probe")
        try:
            os.makedirs(tmp_dir, exist_ok=True)
        except Exception:
            tmp_dir = base_dir
        stamp = time.strftime("%Y%m%d_%H%M%S") + "_" + str(getattr(self, "_probe_counter", 0))
        self._probe_counter = getattr(self, "_probe_counter", 0) + 1
        control_path = os.path.join(tmp_dir, "probe_control_{}.json".format(stamp))
        output_path = os.path.join(tmp_dir, "probe_result_{}.json".format(stamp))

        try:
            with open(control_path, "w", encoding="utf-8") as f:
                json.dump(control, f, ensure_ascii=False)
        except Exception as exc:
            self.var_status.set("⚠ 检验定位：写入控件定义失败：" + str(exc))
            return

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = subprocess.Popen(
                [sys.executable, probe_script, control_path, output_path],
                cwd=base_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.var_status.set("⚠ 检验定位：启动探针失败：" + str(exc))
            return

        self._probe_proc = proc
        self._probe_output_path = output_path
        self._probe_start = time.time()
        self._probe_timed_out = False

        watcher = threading.Thread(target=self._wait_probe_exit, args=(proc, output_path), daemon=True)
        watcher.start()
        # 超时兜底：探针挂死（如 UIA 死锁）时强杀，避免进程残留
        self._probe_timer = self.root.after(int(self._PROBE_WAIT_SECONDS * 1000), self._on_probe_timeout)

    def _wait_probe_exit(self, proc, output_path):
        start = getattr(self, "_probe_start", time.time())
        try:
            returncode = proc.wait()
        except Exception as exc:
            self.root.after(0, lambda rc=-1, e=exc: self._handle_probe_result(output_path, rc, start, str(e)))
            return
        self.root.after(0, lambda rc=returncode: self._handle_probe_result(output_path, rc, start))

    def _on_probe_timeout(self):
        self._probe_timed_out = True
        proc = getattr(self, "_probe_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
            self.var_status.set("⚠ 检验定位：探针超时（{}s），已终止。".format(int(self._PROBE_WAIT_SECONDS)))
        self._probe_proc = None

    def _handle_probe_result(self, output_path, returncode, start, extra_error=""):
        if hasattr(self, "_probe_timer") and self._probe_timer is not None:
            try:
                self.root.after_cancel(self._probe_timer)
            except Exception:
                pass
            self._probe_timer = None
        self._probe_proc = None
        if getattr(self, "_probe_timed_out", False):
            # 超时分支已报告状态，这里仅清理临时文件
            self._cleanup_probe_files(output_path)
            return
        elapsed = time.time() - start

        result = None
        try:
            if os.path.isfile(output_path):
                with open(output_path, "r", encoding="utf-8") as f:
                    result = json.load(f)
        except Exception:
            result = None

        if not isinstance(result, dict):
            if extra_error:
                self.var_status.set("⚠ 检验定位失败：" + str(extra_error))
            elif returncode == 0:
                self.var_status.set("⚠ 检验定位：结果文件不可读（{:.1f}s）".format(elapsed))
            else:
                self.var_status.set(
                    "⚠ 检验定位：定位进程异常退出（code {}, {:.1f}s）——可能因 UIA 原生崩溃，"
                    "已隔离在子进程，主窗口未受影响。".format(returncode, elapsed)
                )
            self._cleanup_probe_files(output_path)
            return

        status = result.get("status")
        if status == "found":
            self._show_probe_found(result, elapsed)
        else:
            err = result.get("error") or "未知错误"
            method = result.get("targetMethod") or "?"
            value = result.get("targetValue") or "?"
            self.var_status.set("⚠ 检验定位：{err}（{method}={value}，耗时 {elapsed:.1f}s）".format(
                err=err, method=method, value=value, elapsed=elapsed
            ))
        self._cleanup_probe_files(output_path)

    def _cleanup_probe_files(self, output_path):
        """清理探针临时文件。"""
        try:
            if output_path and os.path.isfile(output_path):
                os.remove(output_path)
            control_path = output_path.replace("probe_result_", "probe_control_")
            if control_path != output_path and os.path.isfile(control_path):
                os.remove(control_path)
        except Exception:
            pass

    def _show_probe_found(self, result, elapsed):
        """在主线程展示命中的高亮框与状态（视觉行为与原先一致）。"""
        import pyautogui
        center = result.get("center")
        rect = result.get("rect")
        if rect:
            rect_obj = type("Rect", (), {
                "left": rect.get("left"),
                "top": rect.get("top"),
                "right": rect.get("right"),
                "bottom": rect.get("bottom"),
            })()
            self._show_locator_highlight(rect_obj)
        if center:
            try:
                pyautogui.moveTo(center["x"], center["y"], duration=0.2)
            except Exception:
                pass
        snap = result.get("snapshot") or {}
        parts = [
            f"✓ 定位成功 (评分:{result.get('score', 0)}, {elapsed:.1f}s)",
            f"名称:{snap.get('name', '') or '(无)'}",
            f"类型:{snap.get('controlType', '') or '(未知)'}",
            f"AID:{snap.get('automationId', '') or '(无)'}",
            "位置:({}, {})".format(center["x"], center["y"]) if center else "位置:(?)",
            "(多匹配)" if result.get("match_count", 0) > 1 else "(唯一)",
        ]
        self.var_status.set(" ".join(part for part in parts if part))

    def cmd_scan_and_save(self):
        self._start_fulltree_scan(auto_save=True)

    def cmd_scan_preview(self):
        self._start_fulltree_scan(auto_save=False)

    def _start_fulltree_scan(self, auto_save):
        """整树采集入口：前台窗口模式下先捕获当前前台窗口标题，
        再最小化自身，避免 GetForegroundWindow 锁到采集工具自己或水印窗口。"""
        if self.var_scan_mode.get().strip() == "foreground":
            # 最小化前先捕获当前前台窗口标题
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            captured_title = buf.value.strip()
            # 排除自身和水印窗口
            if captured_title and captured_title not in ("WT 控件库采集器", "window"):
                self._captured_foreground_title = captured_title
                self.var_status.set(f"已捕获目标窗口：{captured_title}，采集工具最小化中…")
            else:
                self._captured_foreground_title = ""
                self.var_status.set("采集工具已最小化，目标窗口即将成为前台，0.6 秒后开始采集…")
            self.root.iconify()
            self.root.update()
            self.root.after(600, lambda: self._run_scan_foreground(auto_save))
        else:
            self._captured_foreground_title = ""
            self._run_scan(auto_save=auto_save)

    def _run_scan_foreground(self, auto_save):
        """延迟后执行扫描，完成后恢复窗口与原始采集模式。"""
        original_mode = self.var_scan_mode.get().strip()
        captured = getattr(self, "_captured_foreground_title", "")
        if captured:
            self.var_scan_mode.set("keyword")
            self.var_window_keyword.set(captured)
        try:
            self._run_scan(auto_save=auto_save)
        finally:
            # 即使扫描异常也要恢复原始模式
            if captured:
                self.var_scan_mode.set(original_mode)
                self.var_window_keyword.set("")
            # 扫描进行中（_run_scan 起后台线程后立即返回）保持最小化，
            # 避免采集器抢前台导致目标窗口失焦；真正完成由 _on_scan_finished 还原。
            if not getattr(self, "_scan_running", False):
                self.root.deiconify()
                self.root.lift()
                self.root.focus_force()

    def _start_region_pick(self, auto_save):
        delay_seconds = max(1, int(self.var_pick_delay.get() or DEFAULT_PICK_DELAY_SECONDS))
        self.var_status.set(f"请在 {delay_seconds} 秒内切到目标软件窗口，随后开始画框选区。")
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.after(delay_seconds * 1000, lambda: self._show_region_overlay(auto_save))

    def _show_region_overlay(self, auto_save):
        self.root.update()
        # 画框前捕获当前前台窗口标题：延迟期间用户已切到目标软件，此刻前台即目标。
        # 画框扫描据此走 keyword 模式，避免 overlay 销毁后前台窗口漂移导致扫错/扫空。
        self._region_capture_title = ""
        try:
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            captured_title = buf.value.strip()
            if captured_title and captured_title not in ("WT 控件库采集器", "window"):
                self._region_capture_title = captured_title
        except Exception:
            self._region_capture_title = ""
        overlay = RegionPickerOverlay(self.root, on_complete=lambda rect: self._finish_region_pick(rect, auto_save))
        overlay.window.focus_force()

    def _finish_region_pick(self, rect, auto_save):
        original_mode = self.var_scan_mode.get().strip()
        original_keyword = self.var_window_keyword.get().strip()
        captured = str(getattr(self, "_region_capture_title", "") or "").strip()
        # 画框扫描优先使用画框前捕获的目标窗口标题（keyword 模式），
        # 避免 overlay 销毁后前台窗口漂移导致扫描到错误窗口/空结果；
        # 用户已显式设置 keyword 模式与关键字时尊重原设置，不覆盖。
        used_captured = bool(captured and (original_mode == "foreground" or not original_keyword))
        try:
            if not rect:
                self.var_status.set("已取消区域采集。")
                return
            if used_captured:
                self.var_scan_mode.set("keyword")
                self.var_window_keyword.set(captured)
            self._update_scan_progress("开始区域扫描", 0)
            time.sleep(0.2)
            self._run_scan(auto_save=auto_save, region_rect=rect)
        except Exception as exc:
            # 画框采集链路任何异常都不能让主窗口保持隐藏（否则表现为"界面消失"）：
            # 记录完整 traceback 供定位，并弹出错误提示。
            try:
                import traceback as _tb
                _hover_log("画框区域扫描异常: {}\n{}".format(exc, _tb.format_exc()))
            except Exception:
                pass
            try:
                messagebox.showerror(
                    "画框扫描异常",
                    "画框区域扫描过程中出现异常：\n{}\n\n详情已写入 _hover_monitor.log".format(exc),
                )
            except Exception:
                pass
        finally:
            # 恢复用户原有的扫描模式与关键字设置
            try:
                if used_captured:
                    self.var_scan_mode.set(original_mode)
                    self.var_window_keyword.set(original_keyword)
            except Exception:
                pass
            # 取消/启动失败时主窗口必须恢复，避免"界面消失"误判；扫描进行中
            # 保持隐藏/最小化不抢目标窗口前台，完成由 _on_scan_finished 统一还原。
            if not getattr(self, "_scan_running", False):
                try:
                    self.root.deiconify()
                    self.root.lift()
                    self.root.focus_force()
                except Exception:
                    pass

    def _update_scan_progress(self, message, count):
        """进度回调 — 可能在 worker 线程调用，必须线程安全。"""
        try:
            self.root.after(0, self._do_update_scan_progress, message, count)
        except Exception:
            pass  # 窗口已关闭

    def _do_update_scan_progress(self, message, count):
        """主线程执行的实际 UI 更新（由 root.after 调度）。"""
        _hover_log(f"ui_update, message={message}, count={count}")
        self.var_scan_progress.set(f"{message} (已采集 {count} 个)")
        # 同步更新主状态栏，即使窗口最小化，恢复后也能看到最后进度
        self.var_status.set(f"扫描中：{message} (已采集 {count} 个)")
        # 窗口标题实时显示进度（最小化时任务栏按钮可见）
        self.root.title(f"WT 控件库采集器 — {count} 个控件")
        # 更新任务栏进度条（使用估算上限，每 50 个控件为一档）
        try:
            estimated_total = max(count, ((count // 50) + 1) * 50)
            self._taskbar_progress.set_state(_TaskbarProgress.TBPF_NORMAL)
            self._taskbar_progress.set_value(count, estimated_total)
        except Exception:
            pass
        # 置顶迷你浮窗实时反馈（主窗口最小化时依然可见）
        try:
            self._scan_progress_overlay.show_progress(message, count)
        except Exception:
            pass
        try:
            self.root.update_idletasks()
        except Exception:
            pass

    # ---- Inspect 式定点补采 ----

    def cmd_toggle_hover_supplement(self):
        """开/关连续悬停跟踪补采（Inspect 式：悬停到哪里就实时补采哪里）。"""
        if self._hover_mode_active:
            self._stop_hover_supplement("已停止悬停跟踪补采。")
            return
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次整树/画框扫描，再开启悬停跟踪补采。")
            return
        self._hover_mode_active = True
        # 悬停模式启动时清空日志
        try:
            with open(_HOVER_MONITOR_LOG, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass
        self._hover_last_pos = None
        self._hover_stable_count = 0
        self._hover_last_hit_key = ""
        self._hover_last_fresh_xy = None
        # 采集层去重：本轮已入队补采过的元素 key（与查看层 last_hit_key 分离）
        self._hover_last_collect_key = ""
        # 位置级防重复：探测被阻塞无新鲜 key 时，距上次补采点过近则跳过入队
        self._hover_last_collect_pos = None
        # 当前悬停元素在 flatControls 中的下标缓存（None=不在库中）
        self._hover_existing_index = None
        self._hover_session_added = 0
        self._hover_frozen = False
        self._hover_frozen_wrapper = None
        self._hover_frozen_rect = None
        # 初始化持久高亮 overlay（跟随鼠标目标实时显示红框）
        self._persistent_highlight = _PersistentHighlight(self.root)
        # 跟踪时需实时看到树同步：自动切到层级树视图并保持窗口置顶
        if self.var_tree_view_mode.get() != "hierarchy":
            self.var_tree_view_mode.set("hierarchy")
            self._refresh_tree()
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self.root.bind("<Escape>", lambda _event: self._stop_hover_supplement("已停止悬停跟踪补采（Esc）。"))
        self.btn_hover_supplement.config(text="⏹ 停止悬停跟踪", bg=CONTROL_MAP_THEME["danger_soft"], fg=CONTROL_MAP_THEME["danger"])
        self.var_status.set(
            "悬停跟踪已开启：把鼠标悬停到目标软件控件上稍作停顿即自动补采其子树；Esc 或再次点击按钮停止；F6 冻结/解冻，F7 采集入库。"
        )
        # 启动 worker 线程（秒级子树采集卸载到后台）
        self._worker_busy = False
        self._start_worker_thread()
        # 启动悬停命中探测线程（from_point 卸载到后台，主线程永不阻塞）
        self._probe_result = (None, None, None, None, "")  # 复位旧结果，首个 tick 重新探测
        self._hover_last_pid = ""
        self._start_probe_thread()
        # 启动全局热键监听器
        self._start_hotkey_listener()
        self._hover_after_id = self.root.after(HOVER_TICK_MS, self._hover_tick)

    def _stop_hover_supplement(self, message=""):
        self._hover_mode_active = False
        # 停止全局热键监听器
        self._stop_hotkey_listener()
        # 清除冻结状态
        self._hover_frozen = False
        self._hover_frozen_wrapper = None
        self._hover_frozen_rect = None
        if self._hover_after_id:
            try:
                self.root.after_cancel(self._hover_after_id)
            except Exception:
                pass
            self._hover_after_id = None
        # 停止 worker 线程（发送退出信号并等待结束）
        self._stop_worker_thread()
        # 停止悬停命中探测线程
        self._stop_probe_thread()
        # 销毁持久高亮 overlay
        try:
            if self._persistent_highlight is not None:
                self._persistent_highlight.destroy()
                self._persistent_highlight = None
        except Exception:
            pass
        try:
            self.root.attributes("-topmost", False)
            self.root.unbind("<Escape>")
        except Exception:
            pass
        # 停止悬停模式时清除任务栏进度
        try:
            self._taskbar_progress.clear()
        except Exception:
            pass
        self.btn_hover_supplement.config(text="🔴 悬停跟踪补采", bg=CONTROL_MAP_THEME["danger_soft"], fg=CONTROL_MAP_THEME["danger"])
        if message:
            self.var_status.set(message)

    def _hover_tick(self):
        """悬停轮询：先探测再重新调度，采集异常不中断跟踪循环。

        冻结状态下跳过探测（保持当前高亮不动），但保持调度循环以便解冻后恢复。
        """
        if not self._hover_mode_active:
            return
        # 冻结时不探测，保持当前高亮和锁定元素
        if self._hover_frozen:
            if self._hover_mode_active:
                self._hover_after_id = self.root.after(HOVER_TICK_MS, self._hover_tick)
            return
        try:
            self._hover_probe_once()
            self._hover_consecutive_errors = 0  # 成功时重置
        except Exception as exc:
            import traceback
            self._hover_consecutive_errors += 1
            _hover_log(f"hover exception ({self._hover_consecutive_errors}): {exc}\n{traceback.format_exc()}")
            self.var_status.set(f"悬停补采异常：{exc}")
            if self._hover_consecutive_errors >= 5:
                _hover_log("连续错误过多，自动禁用悬停模式")
                self._hover_mode_active = False
                return
        finally:
            if self._hover_mode_active:
                self._hover_after_id = self.root.after(HOVER_TICK_MS, self._hover_tick)

    def _hover_probe_once(self):
        """悬停探测（Inspect 式两层解耦）。

        查看层：每 tick 读探测线程最新结果，仅当结果对应当前光标位置才算新鲜；
            新鲜命中立即更新红框/状态栏/树聚焦（"指哪看哪"），静止时同样每 tick
            重读，探测一返回当前位置的结果立即生效（不再等鼠标再动一次）。
            探测被 MUP 阻塞时用 Win32 窗口级命中兜底高亮（永不阻塞），界面始终跟手。
        采集层：停顿满 HOVER_STABLE_TICKS、非只看不采、worker 空闲才触发；元素级
            去重仅在新鲜结果可用时生效，探测被阻塞时退化为位置级防重复，避免旧 key
            误判"已存在"而吞掉新元素的补采。
        """
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        # 鼠标在采集器自身窗口上时不探（正常查看树/操作按钮）
        try:
            root_x, root_y = self.root.winfo_rootx(), self.root.winfo_rooty()
            if (root_x <= point.x <= root_x + self.root.winfo_width()
                    and root_y <= point.y <= root_y + self.root.winfo_height()):
                self._hover_last_pos = None
                self._hover_stable_count = 0
                return
        except Exception:
            pass
        last = self._hover_last_pos
        self._hover_last_pos = (point.x, point.y)
        if last is None or abs(point.x - last[0]) > 6 or abs(point.y - last[1]) > 6:
            self._hover_stable_count = 0
        else:
            self._hover_stable_count += 1

        # ---- overlay 处理：需要探测时先隐藏红框，读完新鲜结果再恢复 ----
        # 高亮 overlay 是置顶窗口，实测其覆盖范围内的 UIA ElementFromPoint 会被拖慢
        # 到 0.4~3 秒且命中 overlay 自身（返回自身进程→被过滤为空）。因此凡是需要
        # 新探测，就先隐藏红框，探测返回毫秒级真实元素后立刻恢复；光标停住不动时
        # 直接沿用缓存命中，红框/树聚焦稳定不闪。这样悬停任何控件（含大容器里的
        # 子控件）都能精确定位聚焦，而不是卡在已高亮的大元素上。
        hl = getattr(self, "_persistent_highlight", None)
        hl_rect = getattr(hl, "last_rect", None) if hl is not None else None
        inside_hl = (hl_rect is not None and self._hover_last_fresh_xy is not None
                     and hl_rect[0] <= point.x <= hl_rect[2]
                     and hl_rect[1] <= point.y <= hl_rect[3])

        # ---- 查看层：探测走独立线程，主线程每 tick 读最新结果，绝不阻塞 ----
        # 此前 from_point+6 属性读取在主线程执行，UIA COM 无超时，MUP 忙/卡时
        # 单次可阻塞数秒→整窗"未响应"（移动门控只降频、不消除阻塞，故仍卡死）。
        # 现在探测交给 _probe_thread，主线程每 tick 读结果并校验"位置新鲜"：
        # 仅当结果对应当前光标位置才更新视图；静止时同样每 tick 重读，探测一返回
        # 当前位置的新鲜命中立即生效（修复"停住后必须再动一下才更新"的卡死）。
        # 注意：探测期间绝不能再显示任何覆盖光标点的窗口——overlay/兜底高亮都会让
        # MTA 探测线程的 from_point 阻塞到超时（实测 300ms~1.3s+）。所以只允许
        # "先隐藏 overlay→探测→返回后恢复真实矩形高亮"这一条路径。
        moved = last is None or point.x != last[0] or point.y != last[1]
        if (not moved) and inside_hl:
            # 停住不动且光标仍在已高亮框内：元素未变，用缓存命中替代探测，
            # 红框/树聚焦稳定；采集层照常走停顿门控补采新元素。
            rx, ry = self._hover_last_fresh_xy[0], self._hover_last_fresh_xy[1]
            key = self._hover_last_hit_key
            rect = hl_rect
            pid = self._hover_last_pid
            probe_ms = 0
            matched = True
        else:
            rx, ry, key, rect, pid = self._read_probe_result()
            matched = (rx == point.x and ry == point.y)
            if not matched and (moved or not self._hover_last_hit_key):
                # 探测前先隐藏红框：overlay 会遮蔽其覆盖范围内的 UIA from_point。
                # 隐藏后探测命中真实元素、毫秒级返回；读到新鲜结果时立即恢复红框。
                if hl is not None:
                    try:
                        hl.hide()
                    except Exception:
                        pass
                self._submit_probe(point.x, point.y)
            probe_ms = self._probe_result_latency_ms if matched else 0
        if matched and key:
            # 新鲜命中：红框/状态栏/树聚焦立即跟随（O3：高亮不受停顿门控约束）
            if key != self._hover_last_hit_key:
                self._hover_last_hit_key = key
                self._hover_last_fresh_xy = (point.x, point.y)
                self._hover_last_pid = pid
                # 红框立即跟随
                if rect is not None and self._persistent_highlight is not None:
                    try:
                        self._persistent_highlight.show(rect)
                    except Exception:
                        pass
                # 已存在控件：立即在层级树中聚焦（结果缓存到 tick 级，避免重复扫 flat）
                _t_sync = time.time()
                self._hover_existing_index = self._find_flat_in_payload_by_hit_key(key)
                if self._hover_existing_index is not None:
                    existing_item = self.current_payload["flatControls"][self._hover_existing_index]
                    anchor_iid = self._locate_hierarchy_iid_by_identity(existing_item)
                    # 已选中同一节点时跳过重复聚焦
                    if anchor_iid and self.control_tree.selection() != (anchor_iid,):
                        self._expand_ancestors(anchor_iid)
                        self.control_tree.see(anchor_iid)
                        self.control_tree.selection_set(anchor_iid)
                        self.control_tree.focus(anchor_iid)
                        # selection_set() 不会触发 <<TreeviewSelect>> 事件，需显式调用面板更新
                        try:
                            self._on_tree_select()
                        except Exception:
                            pass
                    self.var_status.set("[已存在] 控件已在库中，树已聚焦（不会重复补采）")
                elif self._hover_look_only:
                    self.var_status.set("[只看不采] 悬停命中新元素，仅高亮跟随（F7 可手动采集入库）")
                else:
                    self.var_status.set("悬停命中新元素：停顿约 0.6 秒自动补采其子树…")
                sync_ms = (time.time() - _t_sync) * 1000
                # 仅慢 tick 才落盘（定位容器卡顿用）：高频日志本身会拖慢轮询
                if probe_ms + sync_ms > 100:
                    _hover_log(
                        f"slow view tick: from_point={probe_ms:.0f}ms, tree_sync={sync_ms:.0f}ms, "
                        f"index={self._hover_existing_index}, key={key[:80]!r}"
                    )
            else:
                # 同一元素命中（光标在同元素内移动后回到它 / 探测确认同元素）：
                # 移动 tick 的清空分支可能把 existing_index 置 None，这里重算恢复，
                # 避免把已入库元素误判成"新元素"而触发多余补采。
                self._hover_existing_index = self._find_flat_in_payload_by_hit_key(key)
                self._hover_last_fresh_xy = (point.x, point.y)
                self._hover_last_pid = pid
        elif matched:
            # 探测新鲜返回但为空（命中采集器自身窗口/无元素）：复位，不采。
            # 探测前已隐藏红框，正常情况下不会命中 overlay；此处兜底复位，
            # 下个 tick 重新探测真实元素（红框已隐藏→探测快→恢复高亮）。
            self._hover_last_hit_key = ""
            self._hover_last_fresh_xy = None
            self._hover_existing_index = None
            return
        else:
            # 探测尚未返回当前位置结果（MUP 忙/卡或线程被阻塞）：
            # 不清空上次 key（静止时保持上次高亮不闪）；不把旧结果当本次命中，
            # 避免"已存在"误判吞掉新元素补采。此处不再做任何高亮兜底：
            # 任何覆盖光标点的窗口（含兜底高亮）都会让 MTA 探测线程的 from_point
            # 阻塞到超时（实测 300ms~1.3s+），等于把卡顿又引回来。探测返回后
            # 下一个 tick 自会用真实控件矩形恢复高亮（探测前已隐藏 overlay）。
            self._hover_existing_index = None

        # ---- 采集层：停顿门控 + 只看不采拦截 + 已存在跳过 + 去重 + 防重入 ----
        if self._hover_stable_count < HOVER_STABLE_TICKS:
            return
        if self._hover_look_only:
            return  # O4：只看不采时绝不入队秒级采集（F7 手动采）
        if self._worker_busy:
            return  # 上一个任务未完成，下个 tick 重试（门控条件仍满足）
        if matched and key:
            # 新鲜结果可用：元素级去重（已在库 / 本轮已采过 → 跳过）
            if self._hover_existing_index is not None:
                return  # 已在库中，无需补采
            if key == self._hover_last_collect_key:
                return  # 同一元素本轮已采过，不重复入队
            self._hover_last_collect_key = key
        else:
            # 无新鲜 key（探测被阻塞）：元素级去重不可用，退化为位置级防重复，
            # 避免同一位置反复入队；worker 后台采集自会按 identity 去重合并。
            last_cp = self._hover_last_collect_pos or (None, None)
            if (last_cp[0] is not None
                    and abs(point.x - last_cp[0]) <= HOVER_REPEAT_DRIFT_PX
                    and abs(point.y - last_cp[1]) <= HOVER_REPEAT_DRIFT_PX):
                return
            self._hover_last_collect_key = self._hover_last_hit_key or ""
        self._hover_last_collect_pos = (point.x, point.y)
        self._worker_busy = True
        # 记录 source 供回调时使用（避免闭包捕获问题）
        self._worker_last_source = f"悬停({point.x},{point.y})"
        # 确保 worker 线程在运行
        self._start_worker_thread()
        # 将秒级操作放入队列，由 worker 线程执行
        self._collect_t0 = time.time()  # 记录补采开始时间，供回调计算耗时
        _hover_log(f"worker enqueue, queue_size={self._worker_queue.qsize()}")
        self.var_status.set("正在后台补采子树…（界面可继续悬停查看，采完自动入树）")
        allowed_process_ids = self._get_target_process_ids()
        # MUP 重启后 payload 的 pid 失效：并入当前悬停元素自身 pid，放行补采
        if self._hover_last_pid:
            allowed_process_ids = sorted(set(allowed_process_ids) | {self._hover_last_pid})
        # 补采前隐藏高亮 overlay：worker 线程的 from_point 若命中 overlay（自身进程、
        # 置顶在光标下）会被判为"命中自身窗口"而拒绝，补采必然失败。隐藏后 worker
        # 命中真实元素；采集回调会复位缓存，下个 tick 重新探测并恢复高亮。
        if self._persistent_highlight is not None:
            try:
                self._persistent_highlight.hide()
            except Exception:
                pass
        self._worker_queue.put((
            collect_subtree_at_point,
            (
                point.x,
                point.y,
            ),
            {
                "climb_levels": int(self.var_supplement_climb.get() or 0),
                "max_depth": HOVER_SUPPLEMENT_MAX_DEPTH,
                "scan_timeout_seconds": HOVER_SUPPLEMENT_TIMEOUT_SECONDS,
                "excluded_process_ids": [str(os.getpid())],
                "allowed_process_ids": allowed_process_ids,
                "status_callback": self._update_scan_progress,
            },
            self._on_subtree_collected,
        ))

    def _probe_hover_hit_key(self, x, y):
        """轻量命中探测：只取元素 identity 作去重键，不做子树遍历。

        返回 (hit_key, rect, pid) 三元组：hit_key 为 identity 字符串，rect 为控件
        屏幕矩形，pid 为命中元素所属进程号。命中自身进程（overlay 等）时返回
        ("", None, "")，防止探测"自伤"。运行在独立探测线程，主线程调用方只读结果。
        """
        if Desktop is None:
            return "", None, ""
        wrapper = _safe_get_value(lambda: Desktop(backend="uia").from_point(int(x), int(y)), None)
        if wrapper is None:
            return "", None, ""
        pid = ""
        # 排除自身进程的窗口，防止 overlay 被 from_point 命中后"自伤"
        try:
            pid = str(wrapper.process_id()).strip()
            if pid == str(os.getpid()):
                return "", None, ""
        except Exception:
            pass
        try:
            rect = wrapper.rectangle()
        except Exception:
            rect = None
        try:
            identity = _build_wrapper_identity(wrapper)
        except Exception as exc:
            _hover_log(f"_build_wrapper_identity failed: {exc}")
            return "", None, ""
        return identity, rect, pid

    def _get_target_process_ids(self):
        """从当前 payload 收集目标软件的进程 id，悬停跟踪时限定只采同进程元素。

        MUP 重启后 payload 里记录的 pid 会失效（进程号变了），此处兜底按可执行路径
        实时枚举 Meteodyn\\MeteodynUniverse 下的进程，两者取并集，保证重启后仍可补采。
        """
        flat = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        pids = {str(item.get("processId", "")).strip() for item in flat if str(item.get("processId", "")).strip()}
        try:
            import psutil
            for proc in psutil.process_iter(["pid", "exe"]):
                try:
                    exe = proc.info.get("exe") or ""
                except Exception:
                    continue
                if "Meteodyn\\MeteodynUniverse\\" in exe.replace("/", "\\"):
                    pids.add(str(proc.info.get("pid", "")))
        except Exception:
            pass
        return sorted(p for p in pids if p)

    # ---- "只看不采"模式开关 ----

    def _on_look_only_toggle(self):
        """"只看不采"模式切换回调：更新内部状态并提示用户。"""
        self._hover_look_only = self.var_look_only.get()
        if self._hover_mode_active:
            if self._hover_look_only:
                self.var_status.set("[只看不采] 已开启：悬停仅高亮跟随，不触发补采写库（F7 可手动采集）")
            else:
                self.var_status.set("[只看不采] 已关闭：恢复原有悬停补采行为")

    # ---- pynput 全局热键监听器 ----

    def _start_hotkey_listener(self):
        """启动 pynput 全局热键监听器（仅在悬停模式开启时生效）。

        热键定义：
        - F6：冻结/解冻当前悬停元素（停止探测，锁定高亮）
        - F7：采集当前冻结/悬停的控件入库
        - Ctrl+Shift+方向键：在冻结状态下导航 UIA 树（父/子/兄弟）

        设计原因：pynput 的 Listener 接收裸键，修饰键需通过 on_press 中检查状态。
        热键仅在探测模式开启时生效，避免与日常操作冲突。
        """
        if not _PYNPUT_AVAILABLE:
            return
        if self._hotkey_listener is not None:
            return  # 已在运行

        # 修饰键状态跟踪
        self._hotkey_ctrl_pressed = False
        self._hotkey_shift_pressed = False

        def _on_press(key):
            """全局按键按下回调：检查修饰键并触发对应操作。"""
            # 更新修饰键状态
            if key in (_pynput_keyboard.Key.ctrl_l, _pynput_keyboard.Key.ctrl_r):
                self._hotkey_ctrl_pressed = True
            elif key in (_pynput_keyboard.Key.shift, _pynput_keyboard.Key.shift_l, _pynput_keyboard.Key.shift_r):
                self._hotkey_shift_pressed = True

            # F6：冻结/解冻
            if key == _pynput_keyboard.Key.f6:
                self.root.after(0, self._hotkey_freeze_toggle)
            # F7：采集当前元素入库
            elif key == _pynput_keyboard.Key.f7:
                self.root.after(0, self._hotkey_capture_current)
            # Ctrl+Shift+方向键：UIA 树导航（仅冻结状态下生效）
            elif self._hotkey_ctrl_pressed and self._hotkey_shift_pressed and self._hover_frozen:
                if key == _pynput_keyboard.Key.up:
                    self.root.after(0, self._hotkey_navigate, "parent")
                elif key == _pynput_keyboard.Key.down:
                    self.root.after(0, self._hotkey_navigate, "first_child")
                elif key == _pynput_keyboard.Key.left:
                    self.root.after(0, self._hotkey_navigate, "prev_sibling")
                elif key == _pynput_keyboard.Key.right:
                    self.root.after(0, self._hotkey_navigate, "next_sibling")

        def _on_release(key):
            """全局按键释放回调：更新修饰键状态。"""
            if key in (_pynput_keyboard.Key.ctrl_l, _pynput_keyboard.Key.ctrl_r):
                self._hotkey_ctrl_pressed = False
            elif key in (_pynput_keyboard.Key.shift, _pynput_keyboard.Key.shift_l, _pynput_keyboard.Key.shift_r):
                self._hotkey_shift_pressed = False

        try:
            listener = _pynput_keyboard.Listener(on_press=_on_press, on_release=_on_release)
            listener.start()
            self._hotkey_listener = listener
        except Exception as exc:
            self.var_status.set(f"全局热键启动失败：{exc}")

    def _stop_hotkey_listener(self):
        """停止 pynput 全局热键监听器。"""
        if self._hotkey_listener is not None:
            try:
                self._hotkey_listener.stop()
            except Exception:
                pass
            self._hotkey_listener = None
        self._hotkey_ctrl_pressed = False
        self._hotkey_shift_pressed = False

    def _hotkey_freeze_toggle(self):
        """F6 热键：冻结/解冻当前悬停元素。

        冻结时：停止探测，锁定当前高亮位置和控件信息。
        解冻时：恢复探测，继续跟踪鼠标。
        """
        if not self._hover_mode_active:
            return
        if self._hover_frozen:
            # 解冻：恢复探测
            self._hover_frozen = False
            self._hover_frozen_wrapper = None
            self._hover_frozen_rect = None
            self._hover_last_pos = None  # 重置位置跟踪，避免解冻后误判
            self._hover_stable_count = 0
            self.var_status.set("已解冻，恢复悬停跟踪。")
        else:
            # 冻结：锁定当前鼠标位置的元素
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            if Desktop is None:
                self.var_status.set("冻结失败：pywinauto 不可用。")
                return
            wrapper = _safe_get_value(lambda: Desktop(backend="uia").from_point(int(point.x), int(point.y)), None)
            if wrapper is None:
                self.var_status.set("冻结失败：未命中任何控件。")
                return
            try:
                rect = wrapper.rectangle()
                rect_tuple = (rect.left, rect.top, rect.right, rect.bottom)
            except Exception:
                rect_tuple = None
            self._hover_frozen = True
            self._hover_frozen_wrapper = wrapper
            self._hover_frozen_rect = rect_tuple
            # 保持高亮显示
            if rect_tuple and self._persistent_highlight is not None:
                try:
                    self._persistent_highlight.show(rect_tuple)
                except Exception:
                    pass
            name = _safe_get_value(lambda: wrapper.window_text(), "")
            self.var_status.set(f"已冻结：{name[:40]}（F6 解冻，方向键导航，F7 采集）")

    def _hotkey_capture_current(self):
        """F7 热键：采集当前冻结/悬停的控件入库。

        优先采集冻结元素，否则采集当前鼠标位置元素。
        将采集任务提交到 worker 线程执行，避免阻塞 UI。
        """
        if not self._hover_mode_active:
            return
        if self._hover_frozen and self._hover_frozen_wrapper is not None:
            # 采集冻结的元素
            wrapper = self._hover_frozen_wrapper
            try:
                rect = wrapper.rectangle()
                cx, cy = (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2
            except Exception:
                cx, cy = 0, 0
        else:
            # 采集当前鼠标位置
            point = wintypes.POINT()
            user32.GetCursorPos(ctypes.byref(point))
            cx, cy = int(point.x), int(point.y)
        # 防重入：上一个 worker 任务未完成则跳过
        if self._worker_busy:
            self.var_status.set("[采集排队] 上一次采集尚未完成，请稍候再按 F7。")
            return
        self._worker_busy = True
        self._worker_last_source = f"热键({cx},{cy})"
        self._start_worker_thread()
        # 将秒级操作放入队列，由 worker 线程执行
        self._worker_queue.put((
            collect_subtree_at_point,
            (cx, cy),
            {
                "climb_levels": int(self.var_supplement_climb.get() or 0),
                "max_depth": HOVER_SUPPLEMENT_MAX_DEPTH,
                "scan_timeout_seconds": HOVER_SUPPLEMENT_TIMEOUT_SECONDS,
                "excluded_process_ids": [str(os.getpid())],
                "allowed_process_ids": self._get_target_process_ids(),
                "status_callback": self._update_scan_progress,
            },
            self._on_subtree_collected,
        ))
        self.var_status.set(f"[F7 采集] 正在采集 ({cx},{cy}) 处控件子树…")

    def _hotkey_navigate(self, direction):
        """Ctrl+Shift+方向键：在冻结状态下导航 UIA 树。

        direction: "parent" | "first_child" | "next_sibling" | "prev_sibling"
        导航后更新冻结元素和高亮显示。
        """
        if not self._hover_frozen or self._hover_frozen_wrapper is None:
            self.var_status.set("导航需先冻结元素（F6）。")
            return
        wrapper = self._hover_frozen_wrapper
        target = None
        try:
            if direction == "parent":
                target = wrapper.parent()
            elif direction == "first_child":
                children = wrapper.children()
                if children:
                    target = children[0]
            elif direction == "next_sibling":
                parent = wrapper.parent()
                if parent:
                    siblings = parent.children()
                    idx = next((i for i, c in enumerate(siblings) if c == wrapper), -1)
                    if 0 <= idx < len(siblings) - 1:
                        target = siblings[idx + 1]
            elif direction == "prev_sibling":
                parent = wrapper.parent()
                if parent:
                    siblings = parent.children()
                    idx = next((i for i, c in enumerate(siblings) if c == wrapper), -1)
                    if idx > 0:
                        target = siblings[idx - 1]
        except Exception:
            target = None
        if target is None:
            self.var_status.set(f"导航失败：{direction} 方向无元素。")
            return
        # 更新冻结元素
        self._hover_frozen_wrapper = target
        try:
            rect = target.rectangle()
            rect_tuple = (rect.left, rect.top, rect.right, rect.bottom)
            self._hover_frozen_rect = rect_tuple
            if self._persistent_highlight is not None:
                self._persistent_highlight.show(rect_tuple)
        except Exception:
            pass
        name = _safe_get_value(lambda: target.window_text(), "")
        self.var_status.set(f"导航到：{name[:40]}（{direction}）")

    # ---- Worker 线程管理（秒级子树采集卸载到后台）----

    def _start_worker_thread(self):
        """启动 worker 线程（如果尚未启动）。

        Worker 线程负责执行秒级的 collect_subtree_at_point 操作，
        避免阻塞 UI 主线程。线程入口做 COM 初始化以支持 UIA 调用。
        """
        if self._worker_thread is not None and self._worker_thread.is_alive():
            return  # 已在运行
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            name="HoverProbeWorker",
            daemon=True,  # 主进程退出时自动终止
        )
        self._worker_thread.start()

    # ---- 悬停命中探测线程（from_point 卸载，主线程永不阻塞）----

    def _start_probe_thread(self):
        """启动悬停命中探测线程（如果尚未启动）。

        探测线程负责执行 _probe_hover_hit_key 的 UIA COM 调用（from_point + 属性读取，
        无超时）。此前这些调用在主线程执行，MUP 忙/卡时单次可阻塞数秒，导致整个
        采集器窗口"未响应"；挪到独立线程后主线程只读结果，UI 永远可响应。
        """
        if self._probe_thread is not None and self._probe_thread.is_alive():
            return  # 已在运行
        self._probe_stop.clear()
        self._probe_thread = threading.Thread(
            target=self._probe_loop,
            name="HoverProbeThread",
            daemon=True,
        )
        self._probe_thread.start()

    def _stop_probe_thread(self, timeout=2.0):
        """停止探测线程：置停止信号并等待线程结束（当前探测阻塞时仅超时返回）。"""
        if self._probe_thread is None:
            return
        self._probe_stop.set()
        self._probe_wake.set()
        try:
            self._probe_thread.join(timeout=timeout)
        except Exception:
            pass
        self._probe_thread = None

    def _probe_loop(self):
        """探测线程主循环：处理主线程的命中探测请求，结果写入 _probe_result。

        线程内独立做 COM 初始化（MTA），与 worker 线程一致。请求单槽取最新：
        鼠标快速移动时只保留最后位置，避免积压；正在执行的探测不可中断，完成后
        立即接下一个请求。收到停止信号（_probe_stop）时退出。
        """
        if comtypes is not None:
            try:
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            except Exception:
                pass
        try:
            while not self._probe_stop.is_set():
                self._probe_wake.wait(0.25)
                self._probe_wake.clear()
                with self._probe_req_lock:
                    req = self._probe_request
                if self._probe_stop.is_set():
                    break
                if req is None:
                    continue
                x, y, req_ts = req
                key, rect, pid = self._probe_hover_hit_key(x, y)
                with self._probe_req_lock:
                    self._probe_result = (x, y, key, rect, pid)
                    self._probe_result_latency_ms = (time.time() - req_ts) * 1000
                    if self._probe_request == req:
                        self._probe_request = None  # 处理完当前请求，等待新请求
        finally:
            if comtypes is not None:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass

    def _submit_probe(self, x, y):
        """主线程：提交一次命中探测请求（不阻塞；同位置重复请求会被合并）。"""
        with self._probe_req_lock:
            if self._probe_request is None or self._probe_request[:2] != (x, y):
                self._probe_request = (x, y, time.time())
                self._probe_wake.set()

    def _read_probe_result(self):
        """主线程：读取探测线程最新完成的结果 (x, y, hit_key, rect, pid)。"""
        with self._probe_req_lock:
            return self._probe_result

    def _stop_worker_thread(self, timeout=2.0):
        """停止 worker 线程：发送退出信号并等待线程结束。

        探测停止或窗口关闭时调用，确保 worker 线程正确清理。
        """
        if self._worker_thread is None:
            return
        # 发送退出信号（None 作为毒丸）
        try:
            self._worker_queue.put_nowait(None)
        except Exception:
            pass
        # 等待线程结束
        try:
            self._worker_thread.join(timeout=timeout)
        except Exception:
            pass
        self._worker_thread = None
        self._worker_busy = False

    def _worker_loop(self):
        """Worker 线程主循环：从队列取任务执行，结果通过 root.after 回调到 UI 线程。

        设计要点：
        1. 入口做 COM 初始化（MTA 模式），否则 UIA 调用会失败；
        2. 任务格式为 (func, args, kwargs, callback) 四元组；
        3. 执行完毕后通过 root.after(0, callback, result) 将结果传回 UI 线程；
        4. 收到 None 毒丸时退出循环。
        """
        # Worker 线程必须独立做 COM 初始化，主线程的 MTA 设置不会传播到子线程
        if comtypes is not None:
            try:
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            except Exception:
                pass
        try:
            while True:
                task = self._worker_queue.get()
                if task is None:
                    break  # 退出信号
                try:
                    func, args, kwargs, callback = task
                    _task_name = getattr(func, '__name__', str(func))
                    _hover_log(f"worker dequeue, task={_task_name}")
                    _task_t0 = time.time()
                    result = func(*args, **kwargs)
                    _task_elapsed = (time.time() - _task_t0) * 1000
                    _hover_log(f"worker task done, task={_task_name}, elapsed={_task_elapsed:.1f}ms")
                    # 通过 root.after 将结果回调到 UI 线程
                    try:
                        self.root.after(0, callback, result)
                    except Exception:
                        pass  # 窗口已关闭
                except Exception as e:
                    # 异常时回调 None
                    try:
                        self.root.after(0, callback, None)
                    except Exception:
                        pass
                finally:
                    self._worker_busy = False
        finally:
            if comtypes is not None:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass

    def _on_subtree_collected(self, result):
        """Worker 线程完成子树采集后的 UI 回调。

        参数 result 为 collect_subtree_at_point 的返回值：
        (sub_flats, target_window, error) 三元组，或 None（异常时）。
        """
        if result is None:
            _hover_log(f"supplement done, total_elements=0 (result=None)")
            return  # 异常已处理
        sub_flats, target_window, error = result
        _supp_elapsed = (time.time() - getattr(self, '_collect_t0', time.time())) * 1000
        _hover_log(f"supplement done, total_elements={len(sub_flats)}, elapsed={_supp_elapsed:.1f}ms, error={error!r}")
        # 调用 _finish_supplement 合并结果到 payload 并刷新视图
        # 悬停模式下 interactive=False，不弹窗打断跟踪
        self._finish_supplement(
            sub_flats, target_window, error,
            source=self._worker_last_source,
            interactive=False,
        )
        # 采完后重置查看层 key：下个 tick 重新评估当前悬停元素，层级树自动聚焦
        # 到刚入库的控件（采集层去重靠 _hover_last_collect_key，不会重复入队）
        self._hover_last_hit_key = ""
        self._hover_last_fresh_xy = None
        self._hover_existing_index = None

    def _bring_to_front_temporarily(self, duration_ms=2500):
        """把主窗口临时置顶带回最前。

        目标软件持有前台时，单纯 deiconify+lift 会被 Windows 前台锁挡住，
        窗口回来了但压在目标软件后面，用户看不到补采结果；短暂置顶可绕过。
        拿到焦点后自动取消置顶（悬停跟踪模式需保持置顶，不取消）。
        """
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.focus_force()
        except Exception:
            return

        def _release_topmost():
            if self._hover_mode_active:
                return
            try:
                self.root.attributes("-topmost", False)
            except Exception:
                pass

        self.root.after(max(500, int(duration_ms)), _release_topmost)

    def cmd_point_supplement(self):
        """定点补采：倒计时后按鼠标位置命中控件，实时采其子树并合并回当前结果。"""
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次整树/画框扫描，再做定点补采。")
            return
        delay_seconds = max(1, int(self.var_pick_delay.get() or DEFAULT_PICK_DELAY_SECONDS))
        self.var_status.set(f"请在 {delay_seconds} 秒内把鼠标悬停到目标控件上，随后自动补采其子树…")
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.after(delay_seconds * 1000, self._do_point_supplement)

    def _do_point_supplement(self):
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        try:
            sub_flats, target_window, error = collect_subtree_at_point(
                point.x,
                point.y,
                climb_levels=int(self.var_supplement_climb.get() or 0),
                excluded_process_ids=[str(os.getpid())],
                status_callback=self._update_scan_progress,
            )
        except Exception as exc:
            sub_flats, target_window, error = [], {}, str(exc)
        finally:
            self._bring_to_front_temporarily()
        self._finish_supplement(sub_flats, target_window, error, source=f"定点({point.x},{point.y})")

    def cmd_selected_supplement(self):
        """补采选中控件：按已采控件的屏幕位置与 identity 重新锚定活元素，实时采其子树。"""
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次整树/画框扫描，再做补采。")
            return
        item = self._get_selected_flat_item_or_node()
        if not isinstance(item, dict):
            messagebox.showinfo("提示", "请先在左侧列表或层级树中选中一个控件。")
            return
        rect = _normalize_rect_dict(item.get("boundingBox"))
        if not rect:
            messagebox.showinfo("提示", '选中控件没有屏幕坐标，无法实时定位，请改用"定点补采子树"。')
            return
        self._pending_supplement_expected = {
            "runtimeId": item.get("runtimeId", ""),
            "name": item.get("name", ""),
            "className": item.get("className", ""),
            "controlType": item.get("controlType", ""),
            "boundingBox": rect,
        }
        self.var_status.set("正在按选中控件位置实时补采子树…（需目标控件当前在屏幕上可见）")
        self.root.withdraw()
        self.root.update_idletasks()
        self.root.after(400, self._do_selected_supplement)

    def _do_selected_supplement(self):
        expected = self._pending_supplement_expected or {}
        self._pending_supplement_expected = None
        center = _rect_center(expected.get("boundingBox"))
        try:
            if not center:
                raise RuntimeError("选中控件矩形无效。")
            sub_flats, target_window, error = collect_subtree_at_point(
                int(center[0]),
                int(center[1]),
                expected=expected,
                excluded_process_ids=[str(os.getpid())],
                status_callback=self._update_scan_progress,
            )
        except Exception as exc:
            sub_flats, target_window, error = [], {}, str(exc)
        finally:
            self._bring_to_front_temporarily()
        self._finish_supplement(sub_flats, target_window, error, source="选中控件")

    def _get_selected_flat_item_or_node(self):
        """取当前选中的控件实体：层级树节点优先，否则按扁平视图下标取 flatControls。"""
        selection = self.control_tree.selection()
        if selection and str(selection[0]).startswith("hierarchy:"):
            return self._hierarchy_nodes_by_iid.get(str(selection[0]))
        index = self._get_selected_tree_index()
        flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
        if index is None or not (0 <= index < len(flat_controls)):
            return None
        return flat_controls[index]

    def _finish_supplement(self, sub_flats, target_window, error, source="", interactive=True):
        """补采收尾：合并 payload、自动勾选、同步视图。

        interactive=False 为悬停跟踪模式：不弹窗打断跟踪，错误/空结果只写状态栏。
        """
        if error:
            if interactive:
                messagebox.showwarning("定点补采", f"补采失败：{error}")
            self.var_status.set(f"补采跳过{f'[{source}]' if source else ''}：{error}")
            return
        if not sub_flats:
            if interactive:
                messagebox.showinfo("定点补采", "未采集到任何控件。")
            self.var_status.set("补采：未采集到任何控件。")
            return
        old_total = len(self.current_payload.get("flatControls", []) or [])
        added, anchor_found = merge_supplement_into_payload(
            self.current_payload,
            sub_flats,
            target_window=target_window,
            status_callback=self._update_scan_progress,
        )
        # 追加式合并不改变既有下标，勾选状态保持有效；只需重建分组与视图
        # 新增控件自动勾选（追加式合并 ⇒ 新增项必为尾部连续区间），
        # 避免补采后直接保存时因未勾选而被静默丢弃（零丢弃原则）。
        if added:
            total = len(self.current_payload.get("flatControls", []) or [])
            self.checked_control_indices.update(range(total - added, total))
            # 更新任务栏进度（悬停补采累计）
            try:
                self._taskbar_progress.set_state(_TaskbarProgress.TBPF_NORMAL)
                self._taskbar_progress.set_value(total, max(total, ((total // 50) + 1) * 50))
            except Exception:
                pass
        self._rebuild_control_groups()
        # 补采结果必须在层级树里可见：非层级视图时自动切换（否则同步定位无处展示）
        if self.var_tree_view_mode.get() != "hierarchy":
            self.var_tree_view_mode.set("hierarchy")
        # 优先增量同步锚点子树（保留展开状态）；刚切视图/锚点缺失时退回全量刷新
        synced = anchor_found and self._sync_hierarchy_after_supplement(sub_flats[0])
        if not synced:
            self._refresh_tree()
        # 统一定位高亮：优先聚焦本次新增的控件（Inspect 式"采到哪看到哪"），无新增则聚焦锚点
        flat_now = self.current_payload.get("flatControls", []) or []
        focus_item = flat_now[old_total] if added and old_total < len(flat_now) else sub_flats[0]
        anchor_iid = self._locate_hierarchy_iid_by_identity(focus_item)
        if anchor_iid:
            self.control_tree.see(anchor_iid)  # see 会自动展开祖先链
            self.control_tree.item(anchor_iid, open=True)
            self.control_tree.selection_set(anchor_iid)
            self.control_tree.focus(anchor_iid)
        else:
            # 身份查找失败时，展开最近的分组让用户至少看到新增数据
            groups = getattr(self, "control_groups", []) or []
            if groups:
                last_group = groups[-1]
                last_iid = f"group:{last_group.get('id', '')}"
                if self.control_tree.exists(last_iid):
                    self.control_tree.item(last_iid, open=True)
                    self.control_tree.see(last_iid)
        self._refresh_summary()
        source_note = f"[{source}]" if source else ""
        if self._hover_mode_active:
            self._hover_session_added += added
            self.var_status.set(
                f"悬停跟踪中{source_note}：本次扫描 {len(sub_flats)} 个、新增 {added} 个，累计新增 {self._hover_session_added} 个（Esc 停止）。"
            )
        else:
            anchor_note = "" if anchor_found else "（未在原树中找到锚点，已挂到窗口根下）"
            self.var_status.set(
                f"补采完成{source_note}：实时采集 {len(sub_flats)} 个，新增 {added} 个控件{anchor_note}，已自动勾选，当前结果尚未保存。"
            )

    def cmd_region_scan_and_save(self):
        self._start_region_pick(auto_save=True)

    def cmd_region_scan_preview(self):
        self._start_region_pick(auto_save=False)

    def cmd_check_all_results(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        self.checked_control_indices = set(range(len(self.current_payload.get("flatControls", []) or [])))
        self._all_checked_mode = True
        self._refresh_tree()
        self._refresh_summary()
        self.var_status.set("已全选当前扫描结果（保存时不去重）。")

    def cmd_smart_check_results(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        self._all_checked_mode = False
        self.checked_control_indices = self._build_default_checked_indices()
        self._refresh_tree()
        self._refresh_summary()
        self.var_status.set(
            "已按质量分级智能勾选：默认保留推荐控件和容器控件；如无推荐项，则回退到每组最优候选。"
        )

    def cmd_clear_checked_results(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        self._all_checked_mode = False
        self.checked_control_indices = set()
        self._refresh_tree()
        self._refresh_summary()
        self.var_status.set("已清空当前勾选结果。")

    def cmd_clear_current_payload(self):
        """🗑 清空当前采集结果，重置所有状态。"""
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "当前没有可清空的采集结果。")
            return
        if not messagebox.askyesno("确认清空", "确定要清空当前采集结果吗？\n未保存的修改将丢失。"):
            return
        self.current_payload = None
        self.current_output_path = ""
        self.current_region_rect = None
        self.checked_control_indices = set()
        self._all_checked_mode = False
        self.control_groups = []
        self.var_saved_control_name.set("")
        self.var_saved_control_id.set("")
        self._hierarchy_nodes_by_iid = {}
        self._hierarchy_iid_seq = 0
        self._hierarchy_flat_by_index = {}
        self._pending_supplement_expected = None
        self._flat_identity_cache = None  # 悬停查库的 identity 反查缓存一并释放
        # 清理悬停相关后台线程与状态（避免清空后旧探测结果误用）
        self._stop_probe_thread()
        with self._probe_req_lock:
            self._probe_result = (None, None, None, None, "")
            self._probe_request = None
        self._hover_last_hit_key = ""
        self._hover_last_fresh_xy = None
        self._hover_last_pid = ""
        self._hover_last_collect_key = ""
        self._hover_last_collect_pos = None
        self.control_tree.delete(*self.control_tree.get_children())
        self.var_status.set("已清空当前采集结果。")
        self._refresh_summary()
        self.root.title("WT 控件库采集器")

    def cmd_apply_current_control_alias(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        index = self._get_selected_tree_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择一个扫描结果。")
            return
        flat_controls = self.current_payload.get("flatControls", []) or []
        control_definitions = self.current_payload.get("controlDefinitions", []) or []
        if index >= len(flat_controls) or index >= len(control_definitions):
            return
        saved_name = (
            self.var_saved_control_name.get().strip()
            or flat_controls[index].get("suggestedControlName", "")
            or flat_controls[index].get("displayName", "")
            or f"控件 {index + 1}"
        )
        saved_id = self.var_saved_control_id.get().strip() or _build_saved_control_id_from_name(saved_name, fallback=f"control_{index + 1}")
        group = self._find_group_by_index(index)
        target_indexes = group.get("memberIndexes", []) if group else [index]
        for target_index in target_indexes:
            if target_index >= len(flat_controls) or target_index >= len(control_definitions):
                continue
            flat_controls[target_index]["savedControlName"] = saved_name
            flat_controls[target_index]["savedControlId"] = saved_id
            control_definitions[target_index]["name"] = saved_name
            control_definitions[target_index]["id"] = saved_id
            control_definitions[target_index]["notes"] = (
                str(control_definitions[target_index].get("notes", "")).strip() + f" | 保存名={saved_name}"
            ).strip(" |")
        self._rebuild_control_groups()
        self._refresh_tree()
        self.control_tree.selection_set(f"item:{index}")
        self._on_tree_select()
        self.var_status.set(f"已更新保存命名：{saved_name}")

    def _build_filtered_payload_for_save(self):
        payload = json.loads(json.dumps(self.current_payload, ensure_ascii=False))
        flat_controls = payload.get("flatControls", []) or []
        control_definitions = payload.get("controlDefinitions", []) or []
        selected_indexes = sorted(index for index in self.checked_control_indices if 0 <= index < len(flat_controls))
        if not selected_indexes:
            raise RuntimeError("当前没有勾选任何扫描结果，无法保存控件库。")

        # 全选模式：保留所有勾选项，不做组内去重
        if getattr(self, "_all_checked_mode", False):
            deduplicated_indexes = selected_indexes
        else:
            deduplicated_indexes = []
            seen_group_keys = set()
            for index in selected_indexes:
                group_key = _build_control_group_key(flat_controls[index])
                if group_key in seen_group_keys:
                    continue
                grouped_selected = [item_index for item_index in selected_indexes if _build_control_group_key(flat_controls[item_index]) == group_key]
                keep_index = max(grouped_selected, key=lambda item_index: _rank_group_candidate(flat_controls[item_index], preferred_index=item_index))
                if keep_index not in deduplicated_indexes:
                    deduplicated_indexes.append(keep_index)
                seen_group_keys.add(group_key)
        filtered_flat_controls = [flat_controls[index] for index in deduplicated_indexes]
        existing_ids = set()
        normalized_definitions = []
        # 逐个勾选控件保证 1:1 生成定义，任何缺失对应定义的控件都就地补建，绝不丢弃。
        for position, index in enumerate(deduplicated_indexes):
            flat_item = flat_controls[index]
            if index < len(control_definitions) and isinstance(control_definitions[index], dict):
                item = control_definitions[index]
            elif _should_include_definition(flat_item):
                item = _build_control_definition_from_flat(flat_item, set())
            else:
                continue
            current_name = (
                str(item.get("name", "")).strip()
                or str(flat_item.get("savedControlName", "")).strip()
                or str(flat_item.get("suggestedControlName", "")).strip()
                or str(flat_item.get("displayName", "")).strip()
                or f"控件 {position + 1}"
            )
            item["name"] = current_name
            item["id"] = _ensure_unique_control_id(
                {"id": str(item.get("id", "")).strip() or _build_saved_control_id_from_name(current_name, fallback=f"control_{position + 1}")},
                existing_ids,
            )["id"]
            # 补填下拉框可选项：老进程/旧 JSON 生成的控件定义可能缺失 optionValues，
            # 但 flat 条目（本次展开采集/权威注入）有，保存时补齐以支撑流程可选项与执行兜底
            existing_options = [
                str(value).strip()
                for value in (item.get("optionValues") or (item.get("inspectData") or {}).get("optionValues") or [])
                if str(value).strip()
            ]
            if not existing_options:
                flat_options = [
                    str(value).strip()
                    for value in (flat_item.get("optionValues") or (flat_item.get("inspectData") or {}).get("optionValues") or [])
                    if str(value).strip()
                ]
                if flat_options:
                    item["optionValues"] = flat_options
                    item["optionCount"] = len(flat_options)
            normalized_definitions.append(item)
        payload["flatControls"] = filtered_flat_controls
        payload["controlDefinitions"] = normalized_definitions
        payload.setdefault("scanMeta", {})
        payload["scanMeta"]["selectedControls"] = len(normalized_definitions)
        payload["scanMeta"]["selectedRawControls"] = len(selected_indexes)
        payload["scanMeta"]["totalControls"] = len(filtered_flat_controls)
        return payload

    def cmd_save_current_payload(self):
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        initial_dir = os.path.join(CONTROL_MAP_DIR, "recordings") if os.path.exists(os.path.join(CONTROL_MAP_DIR, "recordings")) else BASE_DIR
        target_window = self.current_payload.get("targetWindow", {}) or {}
        default_name = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{slugify_filename(target_window.get('title', 'window'))}_control_map.json"
        output_path = filedialog.asksaveasfilename(
            title="保存控件库",
            initialdir=initial_dir,
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
        )
        if not output_path:
            return
        try:
            filtered_payload = self._build_filtered_payload_for_save()
            self.current_output_path = save_control_map_payload(filtered_payload, output_path)
        except Exception as exc:
            messagebox.showerror("保存失败", f"保存控件库失败：\n{exc}")
            return
        self._refresh_summary()
        self.var_status.set(f"已保存已勾选控件：{self.current_output_path}")
        self._maybe_auto_merge_after_save()

    def cmd_load_control_map_file(self):
        """加载已采集的控件库文件，直接在当前采集器上继续补采。

        免去每次打开都要重新整树扫描的重复工作：加载既有库后，
        悬停跟踪 / 定点补采会把新增控件合并进当前结果，保存即为增量库。
        """
        initial_dir = os.path.join(CONTROL_MAP_DIR, "recordings") if os.path.exists(os.path.join(CONTROL_MAP_DIR, "recordings")) else BASE_DIR
        path = filedialog.askopenfilename(
            title="加载已采集控件库文件",
            initialdir=initial_dir,
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except Exception as exc:
            messagebox.showerror("加载失败", f"读取控件库文件失败：\n{exc}")
            return
        if not isinstance(payload, dict):
            messagebox.showerror("加载失败", "文件内容不是有效的控件库（应为 JSON 对象）。")
            return
        flat_controls = payload.get("flatControls")
        definitions = payload.get("controlDefinitions")
        if not isinstance(flat_controls, list) or not isinstance(definitions, list):
            messagebox.showerror("加载失败", "文件缺少 flatControls / controlDefinitions 数组，不是有效的控件库文件。")
            return
        # 悬停跟踪若在运行，先停止并清空探测/去重状态，避免旧 key 干扰新库
        if self._hover_mode_active:
            self._stop_hover_supplement("已加载控件库，停止上一轮悬停跟踪。")
        # 复用扫描完成的状态装配：分组、树、摘要
        self.current_payload = payload
        self.current_output_path = path
        self.current_region_rect = None
        self._rebuild_control_groups()
        # 加载的库整体保留：全量勾选（零丢弃保存），补采新控件继续并入
        self._all_checked_mode = True
        self.checked_control_indices = set(range(len(flat_controls)))
        self.var_saved_control_name.set("")
        self.var_saved_control_id.set("")
        # 复位悬停层去重状态（防止旧 key 误判"已存在"而吞掉补采）
        self._hover_last_hit_key = ""
        self._hover_last_fresh_xy = None
        self._hover_last_pid = ""
        self._hover_last_collect_key = ""
        self._hover_last_collect_pos = None
        self._hover_existing_index = None
        with self._probe_req_lock:
            self._probe_result = (None, None, None, None, "")
            self._probe_request = None
        self._refresh_tree()
        self._refresh_summary()
        self.root.title("WT 控件库采集器")
        target_title = ((payload.get("targetWindow", {}) or {}).get("title", "") or "未知窗口").strip()
        self.var_status.set(
            f"已加载控件库：{len(flat_controls)} 个控件（窗口：{target_title}）。"
            f"可直接悬停跟踪 / 定点补采新增控件，保存即为增量库。"
        )

    def _maybe_auto_merge_after_save(self):
        """保存成功后按勾选状态触发自动合并入库（后台线程执行）。"""
        if self.var_auto_merge.get():
            self._auto_merge_to_master()

    def _auto_merge_to_master(self):
        """自动合并入库：备份总库后，将 recordings 全量合并进总控件库。

        复用 tools.merge_standard_control_library.run_merge（与流程编辑器"合并去重并保存"
        同一套合并逻辑），保证两处产物数据同源；以后优化合并代码，自动合并自动同步。
        """
        if getattr(self, "_auto_merge_running", False):
            return
        self._auto_merge_running = True
        self.var_status.set("保存成功，正在自动合并入库…")

        catalog_path = os.path.join(CONTROL_MAP_DIR, "standard", "standard_control_catalog.json")
        report_path = os.path.join(CONTROL_MAP_DIR, "standard", "standard_catalog_mismatch_report.json")
        master_path = os.path.join(CONTROL_MAP_DIR, "standard", "总控件信息.json")

        backup_path = ""
        try:
            if os.path.exists(master_path):
                backup_dir = os.path.join(CONTROL_MAP_DIR, "standard", "backups")
                os.makedirs(backup_dir, exist_ok=True)
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_path = os.path.join(backup_dir, f"总控件信息_{stamp}.json")
                shutil.copy2(master_path, backup_path)
                # 仅保留最近若干份历史备份，避免每次合并无限累积
                self._prune_master_backups(backup_dir, keep=MAX_MASTER_BACKUPS)
        except Exception:
            backup_path = ""

        def _worker():
            try:
                from tools import merge_standard_control_library as msl

                def _progress(pct, msg):
                    self.root.after(0, lambda p=pct, m=msg: self.var_status.set(
                        f"自动合并入库 ({p}%): {m}"))

                stats = msl.run_merge(CONTROL_MAP_DIR, catalog_path, report_path,
                                      master_path, progress_callback=_progress)
            except Exception as exc:  # noqa: BLE001 - 失败需完整反馈到状态栏
                self.root.after(0, lambda: self._on_auto_merge_done(None, exc, backup_path))
            else:
                self.root.after(0, lambda: self._on_auto_merge_done(stats, None, backup_path))

        threading.Thread(target=_worker, daemon=True).start()

    def _prune_master_backups(self, backup_dir, keep=MAX_MASTER_BACKUPS, prefix="总控件信息_"):
        """保留 backup_dir 中前缀为 prefix 的最近 keep 份自动备份，删除其余历史文件。

        仅匹配 '<prefix><时间戳>.json' 形态（自动合并产生），不触碰：
          - standard_catalog_*/report_* 等其它备份；
          - 形如 '<prefix><时间戳>_<标签>.json' 的人工里程碑备份（如 pre-IA-label-fix），
            这些带标签的副本永远保留。
        删除按修改时间从旧到新进行，保留最新 keep 份自动备份。
        """
        import re as _re
        auto_pat = _re.compile(r"^" + _re.escape(prefix) + r"(\d{8}_\d{6})\.json$")
        milestone_pat = _re.compile(r"^" + _re.escape(prefix) + r"\d{8}_\d{6}_.+\.json$")
        try:
            names = os.listdir(backup_dir)
        except OSError:
            return
        auto_files = []
        for f in names:
            full = os.path.join(backup_dir, f)
            if f.endswith(".json") and auto_pat.match(f) and os.path.isfile(full):
                auto_files.append(full)
            # 带标签的里程碑（milestone_pat）与 catalog/report 等一律不动
        auto_files.sort(key=os.path.getmtime, reverse=True)
        for old in auto_files[keep:]:
            try:
                os.remove(old)
            except OSError:
                pass

    def _on_auto_merge_done(self, stats, error, backup_path):
        self._auto_merge_running = False
        if error is not None:
            self.var_status.set("自动合并入库失败：%s" % error)
            return
        self.var_status.set(
            "自动合并入库完成：%d个控件（high %d / medium %d / low %d），待复核%d项%s"
            % (
                stats.get("totalControls", 0),
                stats.get("high", 0),
                stats.get("medium", 0),
                stats.get("lowOrUnknown", 0),
                stats.get("needsReview", 0),
                ("；已备份：" + backup_path) if backup_path else "",
            )
        )

    def cmd_open_control_map_dir(self):
        ensure_directory(CONTROL_MAP_DIR)
        os.startfile(CONTROL_MAP_DIR)

    def cmd_copy_selected_locator(self):
        selection = self.control_tree.selection()
        if not selection or not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先选择一个控件。")
            return
        index = self._get_selected_tree_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择一个具体控件。")
            return
        flat_controls = self.current_payload.get("flatControls", [])
        if index >= len(flat_controls):
            return
        item = flat_controls[index]
        locator_text = json.dumps(
            {
                "targetMethod": item.get("recommendedTargetMethod", ""),
                "targetValue": item.get("recommendedTargetValue", ""),
                "windowTitle": item.get("windowTitle", ""),
                "uiPath": item.get("uiPath", ""),
            },
            ensure_ascii=False,
            indent=2,
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(locator_text)
        self.var_status.set(f"已复制推荐定位：{item.get('displayName', '')}")


    # ── 搜索控件 ──────────────────────────────────────────────────────────────

    def _select_tree_item_by_index(self, index):
        """在控件树中选中指定 flat index 对应的行（兼容 flat / hierarchy 两种视图）。

        返回 True 表示成功选中；False 表示未找到。
        """
        # flat 模式：iid = item:{index}
        iid = f"item:{index}"
        children = self.control_tree.get_children()
        if iid in children:
            self.control_tree.selection_set(iid)
            self.control_tree.see(iid)
            self._on_tree_select()
            return True
        # hierarchy 模式：递归遍历所有层级，按 seq 列（第2列 = index+1）匹配
        target_iid = self._search_hierarchy_child_by_seq(index + 1)
        if target_iid:
            # 展开祖先链使控件可见
            self._expand_ancestors(target_iid)
            self.control_tree.see(target_iid)
            self.control_tree.selection_set(target_iid)
            self.control_tree.focus(target_iid)
            self._on_tree_select()
            return True
        return False

    def _search_hierarchy_child_by_seq(self, target_seq, parent=""):
        """递归搜索 hierarchy 树中 seq（第2列）等于 target_seq 的节点，返回其 iid。"""
        for child in self.control_tree.get_children(parent):
            vals = self.control_tree.item(child, "values") or []
            if len(vals) >= 2:
                try:
                    if int(str(vals[1]).strip()) == target_seq:
                        return child
                except (ValueError, IndexError):
                    pass
            # 递归深入子节点
            found = self._search_hierarchy_child_by_seq(target_seq, child)
            if found:
                return found
        return None

    def _expand_ancestors(self, iid):
        """展开节点 iid 的所有祖先，使其完整可见。"""
        parent = self.control_tree.parent(iid)
        ancestors = []
        while parent:
            ancestors.append(parent)
            parent = self.control_tree.parent(parent)
        for anc in reversed(ancestors):
            self.control_tree.item(anc, open=True)

    def _get_flat_identity_maps(self):
        """构建并缓存 flatControls 的 identity 反查字典（精确/5字段/runtimeId 三级）。

        悬停查看层元素一变就要查一次库，O(n) 扫描在鼠标扫过容器/子控件之间
        来回时会反复全表重算 identity；缓存后降为 O(1) 字典查找。
        缓存键为 (id(flat), len(flat))：重新扫描换新列表→id 变；补采追加合并→len 变，
        两种变更都能自动失效（别名编辑不改 identity 字段，无需失效）。
        """
        flat = self.current_payload.get("flatControls", []) or []
        cache = getattr(self, "_flat_identity_cache", None)
        cache_key = (id(flat), len(flat))
        if cache is not None and cache[0] == cache_key:
            return cache[1], cache[2], cache[3]
        exact_map = {}
        five_map = {}
        runtime_map = {}
        for idx, item in enumerate(flat):
            identity = _build_flat_control_identity(item)
            exact_map.setdefault(identity, idx)  # setdefault 保留首个匹配，与逐个扫描语义一致
            parts = identity.split("|")
            if len(parts) >= 5:
                five_map.setdefault("|".join(parts[:5]), idx)
            runtime = str(item.get("runtimeId", "")).strip()
            if runtime:
                runtime_map.setdefault(runtime, idx)
        self._flat_identity_cache = (cache_key, exact_map, five_map, runtime_map)
        return exact_map, five_map, runtime_map

    def _find_flat_in_payload_by_hit_key(self, hit_key):
        """在 flatControls 中按 hit_key 查找已存在控件的下标。

        多级匹配策略（基于缓存字典，O(1)）：
        1. 完整 identity 精确匹配（6 字段，含坐标）
        2. 5 字段匹配（去掉坐标，窗口移动后仍可匹配）
        3. runtimeId 单独匹配（最可靠但重启后失效）
        """
        if not hit_key or not isinstance(self.current_payload, dict):
            return None
        exact_map, five_map, runtime_map = self._get_flat_identity_maps()
        if not exact_map and not runtime_map:
            return None
        idx = exact_map.get(hit_key)
        if idx is not None:
            return idx
        hit_parts = hit_key.split("|")
        if len(hit_parts) >= 5:
            idx = five_map.get("|".join(hit_parts[:5]))
            if idx is not None:
                return idx
        hit_runtime = hit_parts[0].strip() if hit_parts else ""
        if hit_runtime:
            return runtime_map.get(hit_runtime)
        return None

    def cmd_search_controls(self):
        """打开搜索控件对话框：按关键字搜索当前扫描结果并跳转定位。"""
        if not isinstance(self.current_payload, dict):
            messagebox.showinfo("提示", "请先完成一次扫描。")
            return
        if not self.current_payload.get("flatControls"):
            messagebox.showinfo("提示", "当前扫描结果中没有控件。")
            return

        dlg = tk.Toplevel(self.root, bg=CONTROL_MAP_THEME["bg"])
        dlg.title("搜索控件")
        dlg.geometry("620x520")
        dlg.transient(self.root)
        dlg.grab_set()

        # -- 搜索行 --
        top_frame = tk.Frame(dlg, bg=CONTROL_MAP_THEME["bg"])
        top_frame.pack(fill=tk.X, padx=10, pady=(10, 4))
        tk.Label(top_frame, text="关键字:", bg=CONTROL_MAP_THEME["bg"], fg=CONTROL_MAP_THEME["text"]).pack(side=tk.LEFT)
        kw_var = tk.StringVar()
        kw_entry = tk.Entry(top_frame, textvariable=kw_var, width=30,
                           bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"],
                           insertbackground=CONTROL_MAP_THEME["text"], relief="flat",
                           highlightthickness=1, highlightbackground=CONTROL_MAP_THEME["border"])
        kw_entry.pack(side=tk.LEFT, padx=(6, 0))
        kw_entry.focus_set()
        _paint_button(tk.Button(top_frame, text="搜索", command=lambda: _do_search()), tone="success").pack(side=tk.LEFT, padx=(6, 0))

        # -- 搜索范围 --
        scope_var = tk.StringVar(value="all")
        scope_frame = tk.Frame(dlg, bg=CONTROL_MAP_THEME["bg"])
        scope_frame.pack(fill=tk.X, padx=10, pady=4)
        for text, val in [("全部字段", "all"), ("控件名(name)", "name"),
                          ("ID(automationId)", "aid"), ("类型(controlType)", "ctype"),
                          ("类名(className)", "cname")]:
            tk.Radiobutton(scope_frame, text=text, variable=scope_var, value=val).pack(side=tk.LEFT, padx=(0, 8))

        # -- 结果列表 --
        list_frame = tk.Frame(dlg, bg=CONTROL_MAP_THEME["bg"])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        sb = tk.Scrollbar(list_frame)
        result_listbox = tk.Listbox(list_frame, font=("Consolas", 10), yscrollcommand=sb.set,
                                    bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"],
                                    selectbackground=CONTROL_MAP_THEME["primary_soft"],
                                    selectforeground=CONTROL_MAP_THEME["primary"],
                                    relief="flat", bd=1, highlightthickness=1,
                                    highlightbackground=CONTROL_MAP_THEME["border"])
        sb.config(command=result_listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        result_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        status_var = tk.StringVar(value="输入关键字后回车或点搜索")
        tk.Label(dlg, textvariable=status_var, fg=CONTROL_MAP_THEME["muted"], bg=CONTROL_MAP_THEME["bg"]).pack(anchor="w", padx=10, pady=(0, 4))

        # -- 底部按钮 --
        btn_frame = tk.Frame(dlg, bg=CONTROL_MAP_THEME["bg"])
        btn_frame.pack(fill=tk.X, padx=10, pady=(0, 10))

        result_indices = []  # listbox position -> flat index

        def _search_flat(kw, scope, ctrl):
            texts = {
                "all": lambda c: " ".join(str(v) for v in c.values()),
                "name": lambda c: f"{_display_control_name(c)} {c.get('name','')} {c.get('helpText','')}",
                "aid": lambda c: c.get("automationId", ""),
                "ctype": lambda c: c.get("controlType", ""),
                "cname": lambda c: c.get("className", ""),
            }
            return kw in texts.get(scope, texts["all"])(ctrl).lower()

        def _do_search(_event=None):
            nonlocal result_indices
            result_listbox.delete(0, tk.END)
            result_indices.clear()
            kw = kw_var.get().strip().lower()
            if not kw:
                status_var.set("请输入关键字")
                return
            scope = scope_var.get()
            # 每次搜索都从 current_payload 重新读取，支持异步补采追加后的搜索
            current_flat = self.current_payload.get("flatControls", []) or []
            for idx, ctrl in enumerate(current_flat):
                if _search_flat(kw, scope, ctrl):
                    result_indices.append(idx)
                    name = _display_control_name(ctrl) or ctrl.get("name") or ""
                    aid = (ctrl.get("automationId") or "")
                    ctype = (ctrl.get("controlType") or "")
                    display = f"#{idx:<4} | {ctype:<12} | id={aid:<20} | {name}"
                    result_listbox.insert(tk.END, display)
            status_var.set(f"共找到 {len(result_indices)} 个匹配控件" if result_indices else "未找到匹配控件")

        def _locate():
            sel = result_listbox.curselection()
            if not sel:
                return
            flat_idx = result_indices[sel[0]]
            if self._select_tree_item_by_index(flat_idx):
                self.var_status.set(f"已定位到搜索结果 #{flat_idx}")
            else:
                self.var_status.set(f"⚠ 未在控件树中找到 # {flat_idx}，请重新扫描")

        kw_entry.bind("<Return>", _do_search)
        result_listbox.bind("<Double-Button-1>", lambda e: _locate())
        _paint_button(tk.Button(btn_frame, text="定位到选中项", command=_locate), tone="primary").pack(side=tk.LEFT, padx=3)
        _paint_button(tk.Button(btn_frame, text="关闭", command=dlg.destroy)).pack(side=tk.RIGHT, padx=3)

    # ── 合并入库 ──────────────────────────────────────────────────────────────

    def cmd_merge_into_library(self):
        """合并入库对话框：源文件(新采集) → 合并进 → 目标文件(现有库)。

        支持：
          - 源+目标双文件合并（主流，新采集汇入现有库）
          - 多源文件一并汇入（批量）
          - 另存为新文件
          - 三种去重策略 + 高权威覆盖
        """
        dlg = tk.Toplevel(self.root, bg=CONTROL_MAP_THEME["bg"])
        dlg.title("合并入库")
        dlg.geometry("780x620")
        dlg.transient(self.root)
        dlg.grab_set()

        # ── 源文件区（新采集，可多选） ──
        src_frame = tk.LabelFrame(dlg, text="源文件（新采集的要汇入的文件）", padx=8, pady=6,
                                  bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"],
                                  relief="flat", bd=1, highlightthickness=1,
                                  highlightbackground=CONTROL_MAP_THEME["border"],
                                  font=(CONTROL_MAP_THEME["font"][0], 10, "bold"))
        src_frame.pack(fill=tk.X, padx=10, pady=(10, 2))

        src_btn_row = tk.Frame(src_frame, bg=CONTROL_MAP_THEME["panel"])
        src_btn_row.pack(fill=tk.X)
        _paint_button(tk.Button(src_btn_row, text="添加源文件...", command=lambda: _add_source()), tone="primary").pack(side=tk.LEFT, padx=(0, 6))
        _paint_button(tk.Button(src_btn_row, text="移除选中", command=lambda: _remove_source())).pack(side=tk.LEFT)

        src_listbox = tk.Listbox(src_frame, height=4, font=("Consolas", 10),
                                bg=CONTROL_MAP_THEME["panel_soft"], fg=CONTROL_MAP_THEME["text"],
                                selectbackground=CONTROL_MAP_THEME["primary_soft"],
                                selectforeground=CONTROL_MAP_THEME["primary"],
                                relief="flat", bd=1, highlightthickness=1,
                                highlightbackground=CONTROL_MAP_THEME["border"])
        src_listbox.pack(fill=tk.X, pady=(4, 0))
        src_scroll = tk.Scrollbar(src_frame, orient="vertical", command=src_listbox.yview)
        src_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        src_listbox.config(yscrollcommand=src_scroll.set)

        src_entries = []  # [(display_name, full_path)]

        # ── 目标文件区 ──
        tgt_frame = tk.LabelFrame(dlg, text="目标文件（要合并到哪个库）", padx=8, pady=6,
                                  bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"],
                                  relief="flat", bd=1, highlightthickness=1,
                                  highlightbackground=CONTROL_MAP_THEME["border"],
                                  font=(CONTROL_MAP_THEME["font"][0], 10, "bold"))
        tgt_frame.pack(fill=tk.X, padx=10, pady=2)

        tgt_path_var = tk.StringVar()
        tgt_entry = tk.Entry(tgt_frame, textvariable=tgt_path_var, width=60, state="readonly",
                             bg=CONTROL_MAP_THEME["panel_soft"], fg=CONTROL_MAP_THEME["text"],
                             relief="flat", highlightthickness=1, highlightbackground=CONTROL_MAP_THEME["border"])
        tgt_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        _paint_button(tk.Button(tgt_frame, text="选择目标...", command=lambda: _browse_target()), tone="primary").pack(side=tk.LEFT, padx=(6, 0))

        # ── 合并选项 ──
        opt_frame = tk.LabelFrame(dlg, text="合并选项", padx=8, pady=6,
                                  bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"],
                                  relief="flat", bd=1, highlightthickness=1,
                                  highlightbackground=CONTROL_MAP_THEME["border"],
                                  font=(CONTROL_MAP_THEME["font"][0], 10, "bold"))
        opt_frame.pack(fill=tk.X, padx=10, pady=2)

        dedup_var = tk.StringVar(value="automationId+controlType+name")
        tk.Label(opt_frame, text="去重键:", bg=CONTROL_MAP_THEME["panel"], fg=CONTROL_MAP_THEME["text"]).pack(side=tk.LEFT)
        ttk.Combobox(
            opt_frame, textvariable=dedup_var, width=32, state="readonly",
            values=["automationId+controlType+name", "uiPath", "name+controlType"],
            style="ControlMap.TCombobox"
        ).pack(side=tk.LEFT, padx=(4, 16))

        overwrite_var = tk.BooleanVar(value=False)
        tk.Checkbutton(opt_frame, text="高权威覆盖（源非空字段填充目标空字段）",
                       variable=overwrite_var).pack(side=tk.LEFT)

        # ── 预览区 ──
        preview_box = scrolledtext.ScrolledText(dlg, wrap=tk.WORD, font=("Consolas", 10), height=10,
                                                bg=CONTROL_MAP_THEME["panel_soft"], fg=CONTROL_MAP_THEME["text"],
                                                insertbackground=CONTROL_MAP_THEME["text"], relief="flat",
                                                bd=1, highlightthickness=1, highlightbackground=CONTROL_MAP_THEME["border"])
        preview_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)

        lbl_status = tk.Label(dlg, text="请选择源文件（至少一个）和目标文件", fg=CONTROL_MAP_THEME["muted"], bg=CONTROL_MAP_THEME["bg"])
        lbl_status.pack(anchor="w", padx=10)

        # ── 底部按钮 ──
        bottom_bar = tk.Frame(dlg, bg=CONTROL_MAP_THEME["bg"])
        bottom_bar.pack(fill=tk.X, padx=10, pady=(0, 10))

        # 缓存的源数据（避免重复加载）
        _src_payloads_cache = []

        # ── 内部函数 ──

        def _add_source():
            paths = filedialog.askopenfilenames(
                title="选择源采集文件(JSON)",
                filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            )
            for p in paths:
                disp = os.path.basename(p)
                if disp not in (e[0] for e in src_entries):
                    src_entries.append((disp, p))
                    src_listbox.insert(tk.END, disp)
                    try:
                        with open(p, encoding="utf-8") as fh:
                            _src_payloads_cache.append(json.load(fh))
                    except Exception:
                        _src_payloads_cache.append(None)
            _sync_status()

        def _remove_source():
            sel = src_listbox.curselection()
            for i in reversed(sel):
                del src_entries[i]
                src_listbox.delete(i)
                if i < len(_src_payloads_cache):
                    del _src_payloads_cache[i]
            _sync_status()

        def _browse_target():
            fpath = filedialog.askopenfilename(
                title="选择目标控件库文件",
                initialdir=CONTROL_MAP_DIR,
                filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            )
            if fpath:
                tgt_path_var.set(fpath)
            _sync_status()

        def _sync_status():
            info = []
            if src_entries:
                info.append(f"源文件: {len(src_entries)} 个")
            if tgt_path_var.get().strip():
                info.append(f"目标: {os.path.basename(tgt_path_var.get().strip())}")
            lbl_status.config(text=" | ".join(info) if info else "请选择源文件和目标文件")

        def _load_target():
            tgt_path = tgt_path_var.get().strip()
            if not tgt_path:
                messagebox.showwarning("提示", "请选择目标文件。")
                return None
            try:
                with open(tgt_path, encoding="utf-8") as fh:
                    return json.load(fh)
            except Exception as exc:
                messagebox.showerror("读取目标文件失败", str(exc))
                return None

        def _do_preview():
            preview_box.delete("1.0", tk.END)
            if not src_entries:
                messagebox.showwarning("提示", "请至少添加一个源文件。")
                return
            if not tgt_path_var.get().strip():
                messagebox.showwarning("提示", "请选择目标文件。")
                return
            tgt_data = _load_target()
            if tgt_data is None:
                return
            mode = dedup_var.get()
            override = overwrite_var.get()

            src_flat_count = sum(len(p.get("flatControls", []) or []) for p in _src_payloads_cache if p)
            tgt_flat = tgt_data.get("flatControls", []) or tgt_data.get("controlDefinitions", [])
            tgt_count = len(tgt_flat)

            # 模拟合并（只预估增量，不动数据）
            tgt_panel_map = _build_ia_panel_title_map(tgt_flat)
            tgt_index = {}
            for ctrl in tgt_flat:
                key = _merge_dedup_key(ctrl, mode, tgt_panel_map)
                tgt_index[key] = ctrl

            added = 0
            updated = 0
            for p in _src_payloads_cache:
                if p is None:
                    continue
                src_flat = p.get("flatControls", []) or p.get("controlDefinitions", [])
                src_panel_map = _build_ia_panel_title_map(src_flat)
                for ctrl in src_flat:
                    key = _merge_dedup_key(ctrl, mode, src_panel_map)
                    if key in tgt_index:
                        if override:
                            updated += 1
                    else:
                        tgt_index[key] = ctrl
                        added += 1

            tgt_title = tgt_data.get("targetWindow", {}).get("title", "(未知)") or "(未知)"

            # 目标文件控件类型分布
            tgt_type_count = {}
            for ctrl in tgt_flat:
                ct = str(ctrl.get("controlType", "") or ctrl.get("inspectData", {}).get("controlType", "") or "未知")
                tgt_type_count[ct] = tgt_type_count.get(ct, 0) + 1
            types_summary = ", ".join(f"{k}×{v}" for k, v in sorted(tgt_type_count.items(), key=lambda x: -x[1])[:6])
            if len(tgt_type_count) > 6:
                types_summary += f" …等共 {len(tgt_type_count)} 种"

            # 源文件统计
            src_details = []
            for idx, (_, path) in enumerate(src_entries):
                p_data = _src_payloads_cache[idx]
                if p_data is None:
                    src_details.append(f"  - {os.path.basename(path)}  (读取失败)")
                else:
                    fc = len(p_data.get("flatControls", []) or p_data.get("controlDefinitions", []))
                    win = p_data.get("targetWindow", {}).get("title", "(未知)")
                    src_details.append(f"  - {os.path.basename(path)} [{fc} 个控件, 窗口={win}]")

            info_lines = [
                f"== 合并预览 ==",
                f"目标库: {os.path.basename(tgt_path_var.get().strip())}",
                f"  窗口: {tgt_title}",
                f"  已有控件: {tgt_count}",
                f"  控件类型: {types_summary}",
                f"",
                f"源文件 ({len(src_entries)} 个):",
            ]
            info_lines += src_details

            info_lines += [
                f"",
                f"预测合并结果:",
                f"  新增控件: {added}",
                f"  更新字段: {updated}",
                f"  合并后总数: {tgt_count + added}",
                f"  去重策略: {mode}",
                f"  高权威覆盖: {'是' if override else '否'}",
            ]
            preview_box.insert("1.0", "\n".join(info_lines))

        def _do_merge(target_override=None):
            """执行合并。覆盖前备份 + 二次确认 + 合并后自动刷新主界面。"""
            if not src_entries:
                messagebox.showwarning("提示", "请至少添加一个源文件。")
                return
            tgt_path = target_override or tgt_path_var.get().strip()
            if not tgt_path:
                messagebox.showwarning("提示", "请选择目标文件。")
                return
            if os.path.abspath(tgt_path) in (os.path.abspath(e[1]) for e in src_entries):
                messagebox.showwarning("提示", "目标和源文件不能相同时选择另存为新文件。")
                return

            # 备份目标文件（带时间戳）
            backup_path = tgt_path + f".bak.{time.strftime('%Y%m%d_%H%M%S')}"
            try:
                shutil.copy2(tgt_path, backup_path)
            except Exception as exc:
                messagebox.showerror("备份失败", f"无法创建备份文件:\n{exc}")
                return

            # 二次确认
            if not messagebox.askyesno(
                "确认覆盖",
                f"即将覆盖目标文件:\n{os.path.basename(tgt_path)}\n\n"
                f"备份至:\n{os.path.basename(backup_path)}\n\n"
                f"是否继续？",
            ):
                # 用户取消，删除刚创建的备份
                try:
                    os.remove(backup_path)
                except Exception:
                    pass
                return

            try:
                tgt_data = _load_target()
                if tgt_data is None:
                    return
                result, stats = _merge_payloads_into_target(
                    tgt_data, _src_payloads_cache, dedup_var.get(), overwrite_var.get())
            except Exception as exc:
                messagebox.showerror("合并失败", str(exc))
                return

            try:
                with open(tgt_path, "w", encoding="utf-8") as fh:
                    json.dump(result, fh, ensure_ascii=False, indent=2)
                msg = (
                    f"合并完成！\n"
                    f"目标文件: {os.path.basename(tgt_path)}\n"
                    f"已有控件: {stats['tgt_before']}\n"
                    f"新增: {stats['added']}  更新字段: {stats['updated']}\n"
                    f"合并后总数: {stats['tgt_after']}"
                )
                messagebox.showinfo("合并成功", msg)

                # 合并成功后，刷新主界面控件树
                self.current_payload = result
                self._refresh_tree()
                self.var_status.set(f"已合并入库：新增 {stats['added']}，更新 {stats['updated']}")

                dlg.destroy()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))

        def _save_as_new():
            """另存为全新文件（不覆盖目标）。"""
            tgt_data = _load_target()
            if tgt_data is None:
                return
            try:
                result, stats = _merge_payloads_into_target(
                    tgt_data, _src_payloads_cache, dedup_var.get(), overwrite_var.get())
            except Exception as exc:
                messagebox.showerror("合并失败", str(exc))
                return

            base_name = "merged"
            if src_entries:
                base_name = os.path.splitext(src_entries[0][0])[0]
            default_name = f"{base_name}_merged.json"
            save_path = filedialog.asksaveasfilename(
                title="另存为新文件",
                initialdir=CONTROL_MAP_DIR,
                initialfile=default_name,
                defaultextension=".json",
                filetypes=[("JSON 文件", "*.json")],
            )
            if not save_path:
                return
            try:
                with open(save_path, "w", encoding="utf-8") as fh:
                    json.dump(result, fh, ensure_ascii=False, indent=2)
                messagebox.showinfo("保存成功", f"合并结果已另存为:\n{save_path}")
                dlg.destroy()
            except Exception as exc:
                messagebox.showerror("保存失败", str(exc))

        _paint_button(tk.Button(bottom_bar, text="预览合并", command=_do_preview), tone="warning").pack(side=tk.LEFT, padx=3)
        _paint_button(tk.Button(bottom_bar, text="执行合并（覆盖目标）", command=_do_merge), tone="success").pack(side=tk.LEFT, padx=3)
        _paint_button(tk.Button(bottom_bar, text="另存为新文件", command=_save_as_new), tone="primary").pack(side=tk.LEFT, padx=3)
        _paint_button(tk.Button(bottom_bar, text="关闭", command=dlg.destroy)).pack(side=tk.RIGHT, padx=3)


# interest-area 面板节点标签集合（与 merge_standard_control_library._IA_NODES 保持一致），
# 用于合并去重时按面板标题区分模板复制控件（各节点同名 automationId 的 Add/Edit/Delete… 按钮）。
_IA_AUTOMATION_PREFIX = "InterestAreas_Button_"
_IA_NODES = {"测风点", "风机", "结果点", "绘图", "配置", "风廓线", "Lidar", "中尺度单元"}


def _build_ia_panel_title_map(flat_controls):
    """从 flat controls 的 InterestAreasView_Tile_Header 兄弟构建 {parent_index: 面板节点名}。

    与 canonical merge（merge_standard_control_library.load_all 的 panel_title_by_parent）一致：
    模板复制按钮与同面板 TileHeader 共享同一 parentIndex，故可用按钮自身的 parentIndex 反查节点名。
    用于修复旧采集格式（labelText 为空、recommendedTargetValue 第 3 段为数字）下的节点消歧。
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


def _interestarea_node_label(item, panel_title_map=None):
    """提取 interest-area 模板复制控件的面板节点标签，用于合并去重时按节点消歧。

    优先级（与 canonical normalize_control 的 disc 逻辑一致）：
      1) TileHeader 兄弟面板节点名（panel_title_map，最权威）
      2) labelText / relatedLabelName
      3) 兜底解析 recommendedTargetValue 第 3 段节点名
    非 interest-area 或非模板复制控件返回空串（不影响其它控件去重）。
    """
    if not isinstance(item, dict):
        return ""
    aid = str(item.get("automationId", "") or (item.get("inspectData") or {}).get("automationId", "")).strip()
    if not aid.startswith(_IA_AUTOMATION_PREFIX):
        return ""
    # 1) TileHeader 兄弟面板节点名（最权威，兼容旧采集格式）
    if isinstance(panel_title_map, dict):
        pid = item.get("parentIndex")
        if pid is not None and pid in panel_title_map:
            return panel_title_map[pid]
    # 2) labelText / relatedLabelName
    for f in ("labelText", "relatedLabelName"):
        v = str(item.get(f, "") or "").strip()
        if v and "," not in v:
            return v
    # 3) recommendedTargetValue 第 3 段节点名
    rtv = str(item.get("recommendedTargetValue", "") or "").strip()
    if rtv:
        parts = [p.strip() for p in rtv.split(",")]
        if len(parts) >= 3 and parts[2] in _IA_NODES:
            return parts[2]
    return ""


def _merge_dedup_key(item, mode, panel_title_map=None):
    """根据去重模式从 flat control 提取 dedup key（与 control_live_detector 一致）。

    mode 取值:
      "automationId+controlType+name" — 优先 AID，回退 (name, ct)
      "uiPath"                         — uiPath 字符串
      "name+controlType"               — (name, ct)

    注意：interest-area 模板复制控件（各面板同名 automationId 的按钮）仅靠
    aid+ct+name 无法区分（name 为空、uiPath 相同），必须追加面板节点标签消歧，
    否则合并入库时 8 个节点的按钮会被误并为 1 条（见知识库模式 L）。
    """
    ins = item.get("inspectData", {}) or {}
    if mode == "automationId+controlType+name":
        aid = str(item.get("automationId", "") or ins.get("automationId", "")).strip().lower()
        ct = str(item.get("controlType", "") or ins.get("controlType", "")).strip().lower()
        name = str(item.get("name", "") or ins.get("name", "") or item.get("displayName", "")).strip().lower()
        if aid:
            key = ["aid", aid, ct, name]
            node = _interestarea_node_label(item, panel_title_map)
            if node:
                key.append("ia:" + node)
            return tuple(key)
        return ("name", name, ct)
    elif mode == "uiPath":
        key = ["ui", str(item.get("uiPath", "") or ins.get("uiPath", "")).strip().lower()]
        node = _interestarea_node_label(item, panel_title_map)
        if node:
            key.append("ia:" + node)
        return tuple(key)
    elif mode == "name+controlType":
        name = str(item.get("name", "") or ins.get("name", "") or item.get("displayName", "")).strip().lower()
        ct = str(item.get("controlType", "") or ins.get("controlType", "")).strip().lower()
        key = ["nc", name, ct]
        node = _interestarea_node_label(item, panel_title_map)
        if node:
            key.append("ia:" + node)
        return tuple(key)
    return ("raw", id(item))


def _merge_payloads_into_target(target_payload, source_payloads, mode, authority_override):
    """将 source_payloads 的控件合并进 target_payload，返回 (merged_payload, stats)。

    合并规则（与 control_live_detector.MergeDialog._execute_merge 一致）：
      - 以 target 控件构建去重索引
      - source 控件新增的追加，已存在的按 authority_override 合并字段
      - 合并 controlsTree
      - 更新 scanMeta
    """
    # 从 target 提取控件列表
    tgt_controls = target_payload.get("flatControls", []) or target_payload.get("controlDefinitions", [])
    tgt_before = len(tgt_controls)

    # 构建去重索引 {(key_tuple): ctrl}
    tgt_panel_map = _build_ia_panel_title_map(tgt_controls)
    index = {}
    for ctrl in tgt_controls:
        key = _merge_dedup_key(ctrl, mode, tgt_panel_map)
        index[key] = ctrl

    added = 0
    updated = 0
    for pay in source_payloads:
        if pay is None:
            continue
        src_controls = pay.get("flatControls", []) or pay.get("controlDefinitions", [])
        src_panel_map = _build_ia_panel_title_map(src_controls)
        for ctrl in src_controls:
            key = _merge_dedup_key(ctrl, mode, src_panel_map)
            if key in index:
                # 重复 key：始终做「字段级补空合并」，保证 source 独有字段不丢失。
                # 只填补 target 中为空/缺失的字段，不覆盖 target 已有的权威值。
                existing = index[key]
                for field, value in ctrl.items():
                    if field.startswith("_"):
                        continue
                    if value not in (None, "", [], {}) and existing.get(field) in (None, "", [], {}):
                        existing[field] = value
                # 嵌套结构（inspectData 等）也做一层补空，避免子字段丢失
                if isinstance(ctrl.get("inspectData"), dict) and isinstance(existing.get("inspectData"), dict):
                    for sub_field, sub_value in ctrl["inspectData"].items():
                        if sub_value not in (None, "", [], {}) and existing["inspectData"].get(sub_field) in (None, "", [], {}):
                            existing["inspectData"][sub_field] = sub_value
                updated += 1
            else:
                index[key] = ctrl
                added += 1

    merged_list = list(index.values())

    # 合并 controlsTree（兼容采集端单根节点 dict 结构与目录 list 结构：
    # dict 形式 {根节点} 取其 children 子树；list 形式直接用）
    def _tree_to_list(value):
        if isinstance(value, list):
            return list(value)
        if isinstance(value, dict):
            children = value.get("children", []) or []
            if isinstance(children, list) and children:
                return children
            return [value]
        return []

    tgt_tree = _tree_to_list(target_payload.get("controlsTree", []))
    src_tree_merged = []
    for pay in source_payloads:
        if pay is None:
            continue
        src_tree_merged.extend(_tree_to_list(pay.get("controlsTree", [])))
    if src_tree_merged:
        target_payload["controlsTree"] = (tgt_tree + src_tree_merged) if tgt_tree else src_tree_merged

    # 回写控件列表（优先 flatControls）
    if target_payload.get("flatControls") is not None:
        target_payload["flatControls"] = merged_list
    elif target_payload.get("controlDefinitions") is not None:
        target_payload["controlDefinitions"] = merged_list
    else:
        target_payload["flatControls"] = merged_list

    # 更新 scanMeta
    meta = target_payload.get("scanMeta", {}) or {}
    meta["totalControls"] = len(merged_list)
    meta["rawTotalControls"] = len(merged_list)
    meta["mergeAdded"] = meta.get("mergeAdded", 0) + added
    meta["mergeUpdated"] = meta.get("mergeUpdated", 0) + updated
    meta["mergedFrom"] = ", ".join(
        f"file_{i}" for i in range(len(source_payloads))
    ) if len(source_payloads) <= 5 else f"{len(source_payloads)} files"
    meta["mergedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    target_payload["scanMeta"] = meta

    return target_payload, {
        "added": added,
        "updated": updated,
        "tgt_before": tgt_before,
        "tgt_after": len(merged_list),
    }





def main():
    parser = argparse.ArgumentParser(description="扫描目标窗口控件树并生成控件信息库")
    parser.add_argument("window_title", nargs="?", default="", help="目标窗口标题关键字")
    parser.add_argument("--foreground", action="store_true", help="扫描当前前台窗口")
    parser.add_argument("--backend", default=DEFAULT_BACKEND, help="pywinauto backend，默认 uia")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH, help="递归扫描最大深度")
    parser.add_argument("--output", default="", help="输出 JSON 路径")
    args = parser.parse_args()

    if args.foreground or args.window_title or args.output:
        payload = build_control_map_payload(
            window_keyword=args.window_title,
            backend=args.backend,
            use_foreground=args.foreground,
            max_depth=args.max_depth,
        )
        output_path = save_control_map_payload(payload, args.output)
        print(f"控件信息库已保存：{output_path}")
        return

    wt_dpi.enable_process_dpi_awareness()
    root = tk.Tk()
    wt_dpi.compute_scale(root)
    ControlMapBuilderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
