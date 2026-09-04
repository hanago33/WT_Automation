# encoding: utf-8

import ctypes
import faulthandler
import gc
import io
import json
import os
import re
import sys
import time
import tkinter as tk
import argparse
from datetime import datetime
from ctypes import wintypes
from functools import lru_cache
from threading import Thread
import threading
import warnings

# pywinauto 0.6.9 的 set_focus() 对部分 UIA 控件（元素已失效/控件不支持 SetFocus）
# 会抛 COMError 并发出 "The window has not been focused due to COMError..." 的
# RuntimeWarning。这类聚焦失败对自动化流程通常非致命（后续定位/点击动作仍会执行），
# 这里按"精确匹配消息"仅抑制这一条，避免它在高频定位/点击时刷屏、掩盖真正错误。
warnings.filterwarnings(
    "ignore",
    message=r"The window has not been focused due to .*",
    category=RuntimeWarning,
)

# 启用 faulthandler：当子进程因原生崩溃（如 UIA/COM 段错误）而硬退出时，
# 会在 stderr（已合并进 stdout）dump 出 Python 调用栈，避免"开始执行步骤后
# 直接失败、无任何错误详情"的盲区。文件句柄 2 即 stderr。
try:
    faulthandler.enable(file=sys.stderr, all_threads=True)
except Exception:
    pass

import pyautogui
from pywinauto import Desktop
from pywinauto_recorder.player import *
import wt_business_steps
import wt_window_helpers
import wt_dpi
import wt_flow_executor
import wt_flow_locator
import wt_projection_helpers
import wt_run_reporting
import wt_run_status
import wt_task_queue
from wt_flow_validation import validate_flow_definition
from wt_flow_editor_utils import normalize_control_window_title
from WT_AUTOMATION_Agent.parameter_scan import ParameterScanner, StepModeFilterUnavailable

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_GM_EXE = r"C:\Program Files\Meteodyn\MeteodynUniverse\MUPSmartClient.exe"
DEFAULT_SOURCE_FILE_PATH = ""
DEFAULT_OUTPUT_DIR = ""
DEFAULT_PROJECTION_FILE_PATH = ""

GM_EXE = DEFAULT_GM_EXE
SOURCE_FILE_PATH = DEFAULT_SOURCE_FILE_PATH
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
PROJECTION_FILE_PATH = DEFAULT_PROJECTION_FILE_PATH

# 目标软件：WT Meteodyn Universe（MUPSmartClient.exe）
# 主窗口标题实测为 "Meteodyn Universe / v1.10.1.0"（窗口类 CASCADIA_HOSTING_WINDOW_CLASS）
MAIN_WINDOW_TITLE_RE = re.compile(r"Meteodyn Universe")
MAIN_WINDOW_UIPATH = u"Meteodyn Universe / v1.10.1.0||Window"

PROJECT_CONFIG_RESOURCE = os.path.join(os.path.dirname(__file__), "resources", "project_config.resource")
UI_TARS_RUNNER = os.path.join(os.path.dirname(__file__), "ui_tars_runner.js")
LOG_FILE = os.path.join(os.path.dirname(__file__), "wt_automation.log")
FLOW_DEFINITION_FILE = os.environ.get(
	"WT_FLOW_DEFINITION_FILE",
	os.path.join(os.path.dirname(__file__), "workspace", "flow_definition.json"),
)
FLOW_PACKAGE_REGISTRY_FILE = os.path.join(os.path.dirname(__file__), "flow_packages", "flow_package_registry.json")
IMAGE_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "image_templates", "projection")
LAYER_TREE_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "image_templates", "layer_tree")
DEBUG_SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "debug_screenshots")
RUNTIME_CONFIG_ENV_KEY = "GM_RUNTIME_CONFIG_JSON"
ENABLE_AI_INTERVENTION_ENV_KEY = "WT_ENABLE_AI_INTERVENTION"
AI_INTERVENTION_LOG_LINES_ENV_KEY = "WT_AI_INTERVENTION_LOG_LINES"

IMAGE_TEMPLATES = {
	"config_button": "配置_按钮.png",
	"general_tree_item": "常规_按钮.png",
	"projection_tree_item": "投影_按钮.png",
	"load_from_file_button": "从文件加载_按钮.png",
	"file_name_input": "文件名(N)_按钮.png",
	"apply_button": "应用_按钮.png",
	"ok_button": "确定_按钮.png",
}

# 全局变量
monitor_window = None
running = False
# 关闭监控窗口时的优雅停止请求：由 main() 的 WM_DELETE_WINDOW 触发，
# 运行循环在步骤边界检测并落盘报告后退出（避免关窗即硬杀 daemon 线程导致报告丢失）
_STOP_REQUESTED = threading.Event()
_FLOW_LOCATOR_CONFIGURED = False
_FLOW_EXECUTOR_CONFIGURED = False
_WT_BUSINESS_STEPS_CONFIGURED = False
_WT_PROJECTION_HELPERS_CONFIGURED = False
_WT_WINDOW_HELPERS_CONFIGURED = False
_WT_RUN_REPORTING_CONFIGURED = False


class _TaskbarProgress:
    """Windows 任务栏进度条（ITaskbarList3，ctypes 直连 vtable，无第三方类型库依赖）。

    绑定到监视器窗口（GUI 模式，有任务栏按钮且用户可见）或控制台窗口（CLI 模式），
    在任务栏图标上显示进度与状态色。不创建新窗口、不置顶、不抢焦点，
    完全不影响自动化步骤执行——解决"进度监测窗口放主页面后看不到、
    置顶又可能干扰执行"的矛盾。非 Windows / COM 初始化失败时静默降级为 no-op。

    注：comtypes.GetActiveObject 只返回 POINTER(IUnknown)（无接口方法），
    必须用 ctypes 按 vtable 偏移调用 SetProgressValue/SetProgressState。
    """
    TBPF_NOPROGRESS = 0x0
    TBPF_NORMAL = 0x1
    TBPF_ERROR = 0x4
    TBPF_PAUSED = 0x8

    # ITaskbarList3 vtable 偏移（基于 IUnknown）：
    # 0-2 IUnknown, 3 HrInit, 4-7 ITaskbarList, 8 MarkFullscreenWindow,
    # 9 SetProgressValue, 10 SetProgressState
    _VTBL_HRINIT = 3
    _VTBL_SET_PROGRESS_VALUE = 9
    _VTBL_SET_PROGRESS_STATE = 10

    def __init__(self, host_hwnd=None):
        self._hwnd = 0
        self._obj = None
        self._set_value = None
        self._set_state = None
        hwnd = host_hwnd or _resolve_taskbar_host_hwnd()
        if not hwnd:
            return
        self._hwnd = int(hwnd)
        try:
            ole32 = ctypes.windll.ole32

            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            CLSID_TaskbarList = GUID(0x56FDF344, 0xFD6D, 0x11D0, (0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90))
            IID_ITaskbarList3 = GUID(0xEA1AFB91, 0x9E28, 0x4B86, (0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF))
            ole32.CoCreateInstance.argtypes = [
                ctypes.POINTER(GUID),
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.POINTER(GUID),
                ctypes.POINTER(ctypes.c_void_p),
            ]
            ole32.CoCreateInstance.restype = ctypes.c_long
            obj = ctypes.c_void_p()
            # CLSCTX_INPROC_SERVER = 0x1
            hr = ole32.CoCreateInstance(
                ctypes.byref(CLSID_TaskbarList), None, 0x1,
                ctypes.byref(IID_ITaskbarList3), ctypes.byref(obj),
            )
            if hr != 0 or not obj.value:
                return
            self._obj = obj
            vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            hrinit = ctypes.cast(
                vtbl[self._VTBL_HRINIT],
                ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p),
            )
            self._set_value = ctypes.cast(
                vtbl[self._VTBL_SET_PROGRESS_VALUE],
                ctypes.WINFUNCTYPE(
                    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_ulonglong, ctypes.c_ulonglong,
                ),
            )
            self._set_state = ctypes.cast(
                vtbl[self._VTBL_SET_PROGRESS_STATE],
                ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint),
            )
            # ITaskbarList 要求先 HrInit，否则 SetProgress* 返回错误
            if hrinit:
                hrinit(obj)
        except Exception:
            self._obj = None
            self._set_value = None
            self._set_state = None

    def set_state(self, state):
        if self._set_state and self._obj and self._hwnd:
            try:
                self._set_state(self._obj, ctypes.c_void_p(self._hwnd), int(state))
            except Exception:
                pass

    def set_value(self, current, total):
        if self._set_value and self._obj and self._hwnd and total > 0:
            try:
                self._set_value(self._obj, ctypes.c_void_p(self._hwnd), int(current), int(total))
            except Exception:
                pass

    def clear(self):
        self.set_state(self.TBPF_NOPROGRESS)


def _resolve_taskbar_host_hwnd():
    """选择任务栏进度宿主窗口。

    优先级：监视器窗口（GUI 模式，任务栏有按钮、用户可见）→ 控制台窗口（CLI 模式）。
    注意：经 WT_Launcher 以 CREATE_NO_WINDOW 启动的自动化子进程没有控制台，
    只能依赖监视器窗口作为宿主。
    """
    try:
        if monitor_window is not None and getattr(monitor_window, "root", None) is not None:
            hwnd = monitor_window.root.winfo_id()
            if hwnd:
                return int(hwnd)
    except Exception:
        pass
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            return int(hwnd)
    except Exception:
        pass
    return 0


# 任务栏进度条（在主线程创建，daemon 执行线程仅做方法调用）
_taskbar_progress = None
# 任务栏/控制台进度状态（供步骤结果回调更新）
_progress_current = 0
_progress_total = 0


def _init_taskbar_progress():
    global _taskbar_progress
    if _taskbar_progress is None:
        _taskbar_progress = _TaskbarProgress()
    return _taskbar_progress


def _update_taskbar_progress(current, total, status="running"):
    """更新任务栏进度条：进度值 + 状态色（运行绿 / 失败红 / 暂停黄）。"""
    tb = _init_taskbar_progress()
    if tb is None:
        return
    if status == "success":
        tb.set_value(total, total)
        tb.set_state(tb.TBPF_NORMAL)
    elif status == "failed":
        tb.set_state(tb.TBPF_ERROR)
    elif status == "paused":
        tb.set_state(tb.TBPF_PAUSED)
    else:
        tb.set_value(current, total)
        tb.set_state(tb.TBPF_NORMAL)


def _update_progress_title(current, total, step_name="", status="success"):
    """进度文字：写入监视器窗口标题 + 控制台标题。

    监视器窗口标题在任务栏悬停即可看到（'WT自动化 [第 X/Y 步] 步骤名 [成功]'），
    无需置顶窗口、不干扰自动化执行；有控制台时同步设置控制台标题。
    """
    try:
        if total <= 0:
            return
        status_text = {"success": "成功", "failed": "失败", "paused": "暂停", "skipped": "跳过"}.get(status, "运行中")
        title = f"WT自动化 [第 {current}/{total} 步] {step_name} [{status_text}]"
        if monitor_window is not None and getattr(monitor_window, "root", None) is not None:
            # 本函数可能在后台自动化线程中被调用；Tk 调用一律由调用方经
            # _ui_safe_call 调度到主线程，此处禁止直接调 root.title()。
            try:
                monitor_window.root.title(title)
            except Exception:
                pass
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            pass
    except Exception:
        pass

pyautogui.PAUSE = 0.15
pyautogui.FAILSAFE = True



StageExecutionError = wt_projection_helpers.StageExecutionError


MONITOR_THEME = {
    "bg": "#f4f7fb",
    "panel": "#ffffff",
    "panel_soft": "#fbfdff",
    "toolbar": "#eaf1fb",
    "border": "#d8e2f0",
    "primary": "#2563eb",
    "primary_soft": "#dbeafe",
    "success": "#059669",
    "danger": "#dc2626",
    "warning": "#b45309",
    "text": "#1f2937",
    "muted": "#64748b",
}


class MonitorWindow:
    def __init__(self):
        wt_dpi.enable_process_dpi_awareness()
        self.root = tk.Tk()
        wt_dpi.compute_scale(self.root)
        self.root.title("WT自动化流程监视器")
        # 小窗放在屏幕右下角，尽量不挡住 WT 目标窗口
        window_width = 340
        window_height = 210
        margin = 24
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        sw = wt_dpi.scale(window_width)
        sh = wt_dpi.scale(window_height)
        pos_x = max(0, screen_width - sw - margin)
        pos_y = max(0, screen_height - sh - 80)
        wt_dpi.raw_geometry(self.root, f"{sw}x{sh}+{pos_x}+{pos_y}")
        self.root.configure(bg=MONITOR_THEME["toolbar"])

        # 默认不置顶，避免遮挡目标父窗口；仅在流程结束需要提示结果时再抬到最前。
        self._set_topmost(False)
        self.root.attributes("-alpha", 0.92)
        try:
            self.root.after(200, self._send_to_back)
        except Exception:
            pass

        self.text_widget = tk.Text(
            self.root, wrap=tk.WORD, state=tk.DISABLED,
            font=("Microsoft YaHei UI", 9),
            bg=MONITOR_THEME["panel_soft"], fg=MONITOR_THEME["text"],
            relief=tk.FLAT, bd=0,
            highlightthickness=1, highlightbackground=MONITOR_THEME["border"],
            padx=8, pady=6,
        )
        self.text_widget.pack(expand=True, fill=tk.BOTH, padx=6, pady=(6, 0))
        self.text_widget.tag_configure("time", foreground=MONITOR_THEME["muted"])
        self.text_widget.tag_configure("info", foreground=MONITOR_THEME["text"])
        self.text_widget.tag_configure("success", foreground=MONITOR_THEME["success"])
        self.text_widget.tag_configure("error", foreground=MONITOR_THEME["danger"])
        self.text_widget.tag_configure("warning", foreground=MONITOR_THEME["warning"])

        self.scrollbar = tk.Scrollbar(
            self.text_widget, relief=tk.FLAT, bd=0,
            bg=MONITOR_THEME["panel"], troughcolor=MONITOR_THEME["toolbar"],
            activebackground=MONITOR_THEME["primary_soft"],
        )
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.text_widget.config(yscrollcommand=self.scrollbar.set)
        self.scrollbar.config(command=self.text_widget.yview)

        self.status_label = tk.Label(
            self.root, text="状态：准备就绪",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=MONITOR_THEME["toolbar"], fg=MONITOR_THEME["muted"],
            pady=4,
        )
        self.status_label.pack(pady=(2, 4))

        # 主线程创建任务栏进度对象（绑定本窗口，任务栏图标显示进度/状态色）。
        # daemon 执行线程只做方法调用，避免跨线程创建 COM 对象不稳定。
        global monitor_window, _taskbar_progress
        monitor_window = self
        try:
            _taskbar_progress = _TaskbarProgress(host_hwnd=self.root.winfo_id())
        except Exception:
            _taskbar_progress = None

    def _set_topmost(self, enabled):
        try:
            self.root.wm_attributes("-topmost", bool(enabled))
        except Exception:
            pass

    def _send_to_back(self):
        self._set_topmost(False)
        try:
            self.root.lower()
        except Exception:
            pass

    def _bring_to_front_for_notice(self):
        try:
            self.root.deiconify()
        except Exception:
            pass
        self._set_topmost(True)
        try:
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass

    def log(self, message, kind="info"):
        if kind not in ("info", "success", "error", "warning"):
            kind = "info"
        self.text_widget.config(state=tk.NORMAL)
        if message.startswith("[") and "] " in message:
            head, _, tail = message.partition("] ")
            self.text_widget.insert(tk.END, head + "] ", "time")
            self.text_widget.insert(tk.END, tail, kind)
        else:
            self.text_widget.insert(tk.END, message, kind)
        self.text_widget.insert(tk.END, "\n")
        self.text_widget.see(tk.END)
        self.text_widget.config(state=tk.DISABLED)
        # 注意：不再调用 self.root.update()。本方法可能被后台自动化线程调用，
        # 跨线程进入 Tcl 事件循环会导致解释器重入崩溃（access violation）。
        # Tk 会在下一个 idle 周期自动刷新，无需手动 pump。

    def update_status(self, status):
        text = "状态：{}".format(status)
        fg = MONITOR_THEME["muted"]
        if "失败" in status or "错误" in status:
            fg = MONITOR_THEME["danger"]
        elif "完成" in status or "成功" in status:
            fg = MONITOR_THEME["success"]
        elif "开始" in status or "执行" in status or "启动" in status:
            fg = MONITOR_THEME["primary"]
        elif "警告" in status or "跳过" in status:
            fg = MONITOR_THEME["warning"]
        self.status_label.config(text=text, fg=fg)
        # 同上：不调用 self.root.update()，避免后台线程重入 Tcl 事件循环。

    def set_success(self):
        self._bring_to_front_for_notice()
        self.status_label.config(text="状态：流程完成！", fg=MONITOR_THEME["success"])
        try:
            self.root.bell()
        except Exception:
            pass
        # 同上：不调用 self.root.update()。

    def set_error(self):
        self._bring_to_front_for_notice()
        self.status_label.config(text="状态：流程失败！", fg=MONITOR_THEME["danger"])
        try:
            self.root.bell()
        except Exception:
            pass
        # 同上：不调用 self.root.update()。


def _ui_safe_call(callback):
    """线程安全地把 Tk 回调调度到主线程执行。

    monitor 模式下主线程运行 mainloop，用 after 调度；若主线程不在 mainloop
    （如 --no-monitor / 主线程同步执行），则直接调用兜底。
    背景：run_automation 在后台自动化线程中运行，直接调用 Tk widget 方法
    （尤其 root.update()/root.title()）会跨线程重入 Tcl 事件循环，导致原生崩溃。
    """
    mw = monitor_window
    if mw is not None and getattr(mw, "root", None) is not None:
        try:
            if mw.root.winfo_exists():
                mw.root.after(0, callback)
                return True
        except Exception:
            pass
    try:
        callback()
    except Exception:
        pass
    return False


def log_step(step_name):
    global monitor_window
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {step_name}"
    print(log_line, end="\n")

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_line + "\n")
        try:
            wt_run_status.publish(activity=step_name, last_log=log_line, source="WT_AUT_recorded")
        except Exception:
            pass

    if monitor_window:
        if any(k in step_name for k in ("失败", "错误")):
            kind = "error"
        elif any(k in step_name for k in ("完成", "成功")):
            kind = "success"
        elif any(k in step_name for k in ("警告", "跳过")):
            kind = "warning"
        else:
            kind = "info"
        # log/update_status 可能在后台自动化线程被调用，须调度到主线程执行，
        # 避免跨线程 Tcl 重入崩溃。
        _ui_safe_call(lambda: monitor_window.log(log_line, kind=kind))
        _ui_safe_call(lambda: monitor_window.update_status(step_name))




# ctypes 窗口检测（来自 combine_test_packaged\wait_global_mapper_ready.py）
user32 = ctypes.windll.user32
ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_size_t)
EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002
user32.SendMessageTimeoutW.argtypes = [
	wintypes.HWND,
	wintypes.UINT,
	wintypes.WPARAM,
	wintypes.LPARAM,
	wintypes.UINT,
	wintypes.UINT,
	ctypes.POINTER(ULONG_PTR),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
SW_RESTORE = 9
SW_MAXIMIZE = 3


def _force_utf8_stdio():
	if sys.platform == "win32":
		# line_buffering=True：日志按行实时刷出到管道，主控台/监控窗口才能实时显示，
		# 否则 python 对管道 stdout 默认块缓冲（攒满 8KB 或进程退出才 flush）。
		sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
		sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)


def _safe_get_value(getter, default=""):
	try:
		value = getter()
	except Exception:
		return default
	return default if value is None else value


@lru_cache(maxsize=1)
def _load_project_settings():
	settings = {}
	if not os.path.exists(PROJECT_CONFIG_RESOURCE):
		return settings
	try:
		with open(PROJECT_CONFIG_RESOURCE, "r", encoding="utf-8", errors="ignore") as file_obj:
			for raw_line in file_obj:
				line = raw_line.strip()
				if not line.startswith("${") or "}" not in line:
					continue
				key_end = line.find("}")
				key = line[2:key_end]
				value = line[key_end + 1 :].strip()
				if value:
					settings[key] = value.replace("\\\\", "\\")
	except OSError:
		return settings
	return settings


@lru_cache(maxsize=1)
def _load_flow_payload():
	def _read_payload(file_path, error_message):
		if not os.path.exists(file_path):
			return {}
		try:
			with open(file_path, "r", encoding="utf-8") as file_obj:
				payload = json.load(file_obj)
		except Exception as exc:
			log_step(f"{error_message}: {exc}")
			return {}
		return payload if isinstance(payload, dict) else {}

	def _normalize_payload(payload):
		payload = payload if isinstance(payload, dict) else {}
		runtime_config = payload.get("runtimeConfig", {})
		flow_packages = payload.get("flowPackages", [])
		steps = payload.get("steps", [])
		if not isinstance(runtime_config, dict):
			runtime_config = {}
		if not isinstance(flow_packages, list):
			flow_packages = []
		if not isinstance(steps, list):
			steps = []
		steps = [item for item in steps if isinstance(item, dict)]
		for step in steps:
			controls = step.get("controls")
			if isinstance(controls, list):
				for control in controls:
					normalize_control_window_title(control)
		return {
			"runtimeConfig": runtime_config,
			"flowPackages": [item for item in flow_packages if isinstance(item, dict)],
			"steps": steps,
		}

	def _has_payload_content(payload):
		payload = _normalize_payload(payload)
		return bool(payload["flowPackages"] or payload["steps"])

	def _build_package_map(flow_packages):
		package_map = {}
		for package in flow_packages:
			package_id = str(package.get("id", "")).strip()
			if package_id:
				package_map[package_id] = package
		return package_map

	def _build_step_map(steps):
		step_map = {}
		for step in steps:
			step_id = str(step.get("id", "")).strip()
			if step_id:
				step_map[step_id] = step
		return step_map

	def _collect_package_refs(steps):
		package_ids = []
		seen_package_ids = set()
		for step in steps:
			package_id = str(step.get("packageRef", "")).strip()
			if not package_id or package_id in seen_package_ids:
				continue
			package_ids.append(package_id)
			seen_package_ids.add(package_id)
		return package_ids

	def _collect_package_step_ids(flow_packages):
		step_ids = []
		seen_step_ids = set()
		for package in flow_packages:
			for item in package.get("stepIds", []):
				step_id = str(item).strip()
				if not step_id or step_id in seen_step_ids:
					continue
				step_ids.append(step_id)
				seen_step_ids.add(step_id)
		return step_ids

	raw_target_payload = _read_payload(FLOW_DEFINITION_FILE, "读取流程链路定义失败，跳过运行时控件匹配")
	target_payload = _normalize_payload(raw_target_payload)
	registry_payload = _normalize_payload(_read_payload(FLOW_PACKAGE_REGISTRY_FILE, "读取流程包仓库失败，跳过流程包补全"))
	if not _has_payload_content(target_payload):
		merged_payload = registry_payload
	elif not _has_payload_content(registry_payload):
		merged_payload = target_payload
	else:
		merged_runtime = dict(registry_payload.get("runtimeConfig", {}))
		merged_runtime.update(target_payload.get("runtimeConfig", {}))

		target_packages = target_payload.get("flowPackages", [])
		registry_package_map = _build_package_map(registry_payload.get("flowPackages", []))
		merged_packages = list(target_packages)
		existing_package_ids = {str(package.get("id", "")).strip() for package in target_packages if str(package.get("id", "")).strip()}
		for package_id in _collect_package_refs(target_payload.get("steps", [])):
			if package_id in existing_package_ids:
				continue
			registry_package = registry_package_map.get(package_id)
			if isinstance(registry_package, dict):
				merged_packages.append(registry_package)
				existing_package_ids.add(package_id)

		target_steps = target_payload.get("steps", [])
		registry_step_map = _build_step_map(registry_payload.get("steps", []))
		merged_steps = list(target_steps)
		existing_step_ids = {str(step.get("id", "")).strip() for step in target_steps if str(step.get("id", "")).strip()}
		for step_id in _collect_package_step_ids(merged_packages):
			if step_id in existing_step_ids:
				continue
			registry_step = registry_step_map.get(step_id)
			if isinstance(registry_step, dict):
				merged_steps.append(registry_step)
				existing_step_ids.add(step_id)

		merged_payload = {
			"runtimeConfig": merged_runtime,
			"flowPackages": merged_packages,
			"steps": merged_steps,
		}

	# 参数表驱动展开（路线B · 非 Agent）：若流程顶层声明 paramTable，
	# 则按参数表把含 ${stepParams.xxx} 占位的模板展开为完整步骤序列。
	# 展开失败安全降级为原始 steps，不影响无 paramTable 的既有路径。
	# paramTable 取自原始未 normalize 的 target（_normalize_payload 会丢弃未知字段）。
	merged_payload = _apply_param_table_expansion(merged_payload, raw_target_payload)

	validation_errors = validate_flow_definition(merged_payload)
	if validation_errors:
		raise RuntimeError(
			"流程链路配置校验失败: "
			+ " | ".join(str(item) for item in validation_errors[:8])
		)
	return merged_payload


def _load_json_env_runtime():
	"""读取 GM_RUNTIME_CONFIG_JSON 环境变量（Simple 界面注入的项目解析结果），返回 dict。"""
	raw_env_payload = str(os.environ.get(RUNTIME_CONFIG_ENV_KEY, "")).strip()
	if not raw_env_payload:
		return {}
	try:
		env_runtime = json.loads(raw_env_payload)
	except Exception:
		return {}
	return env_runtime if isinstance(env_runtime, dict) else {}


def _apply_param_table_expansion(merged_payload, target_payload):
	"""若流程顶层含 paramTable 字段，按参数表展开模板步骤。

	展开结果覆盖 merged_payload["steps"]（每行参数 × 模板步，每步带 stepParams）。
	flowPackages 保留（packageRef 引用的包定义仍需存在）。
	无 paramTable / 展开异常时原样返回，零回归。
	"""
	if not isinstance(target_payload, dict):
		return merged_payload
	param_table = str(target_payload.get("paramTable", "")).strip()
	if not param_table:
		return merged_payload
	if not os.path.isabs(param_table):
		param_table = os.path.join(os.path.dirname(FLOW_DEFINITION_FILE), param_table)
	if not os.path.exists(param_table):
		# 防御：Simple 模式把流程写到 workspace/ 临时文件后，FLOW_DEFINITION_FILE 所在
		# 目录不再是流程原目录；参数表通常与流程文件同在 flow_packages/，按文件名回退查找。
		fallback_param = os.path.join(
			os.path.dirname(__file__), "flow_packages", os.path.basename(param_table)
		)
		if os.path.exists(fallback_param):
			param_table = fallback_param
		else:
			log_step(f"[paramTable] 参数表不存在，跳过展开：{param_table}")
			return merged_payload
	template_steps_for_scan = merged_payload.get("steps", [])
	# stepTags 缺失的硬失败在 ParameterScanner.scan 内判定（过滤启用且存在请求模式的行
	# 但模板零 stepTags 时才抛 StepModeFilterUnavailable），此处不再冗余宽泛告警。
	# ── 单塔/多塔自动决定：按项目解析出的测风塔数量覆盖参数表 towerMode 列 ──
	# Simple 界面选择项目文件夹后，把项目解析结果（含 mastIds）经 GM_RUNTIME_CONFIG_JSON
	# 环境变量传给子进程；这里读环境覆盖值（优先级最高），其次流程 runtimeConfig。
	tower_mode_override = None
	_mast_ids: list[str] = []
	for _src in (
		{"payload": merged_payload.get("runtimeConfig", {})},
		{"payload": _load_json_env_runtime()},
	):
		_rc = _src["payload"]
		if not isinstance(_rc, dict):
			continue
		_mast_ids_value = _rc.get("mastIds")
		if isinstance(_mast_ids_value, list) and _mast_ids_value:
			_mast_ids = [str(item) for item in _mast_ids_value if str(item).strip()]
			break
	# 显式 towerMode（如 Simple 界面已决定）优先，其次按测风塔数量推断。
	for _src in ({"payload": merged_payload.get("runtimeConfig", {})}, {"payload": _load_json_env_runtime()}):
		_rc = _src["payload"]
		if not isinstance(_rc, dict):
			continue
		_tm = str(_rc.get("towerMode", "")).strip().lower()
		if _tm in ("single", "multi"):
			tower_mode_override = _tm
			break
	if tower_mode_override is None and len(_mast_ids) >= 2:
		tower_mode_override = "multi"
	if tower_mode_override is not None:
		log_step(f"[paramTable] 按项目条件决定塔模式：towerMode={tower_mode_override}（测风塔数={len(_mast_ids)}）")
	try:
		# 真实综合流程的模板步骤位于 flow.steps（已规整进 merged_payload["steps"]），
		# 直接用 ParameterScanner.scan 以合并后的步骤为模板展开，避免 scan_from_flow
		# 只读顶层 steps 导致空模板。flowPackages 保留供 packageRef 解析。
		scanned = ParameterScanner.scan(
			param_table,
			template_steps=template_steps_for_scan,
			tower_mode_override=tower_mode_override,
		)
	except StepModeFilterUnavailable as exc:
		# 硬失败：stepMode 过滤无法生效时若静默全跑，会把所有模式的全套步骤排给每行，
		# 运行期才暴露（如新建行混入复制链、复制链定位综合1失败）。直接中止启动。
		raise RuntimeError(f"[paramTable] {exc}") from exc
	except Exception as exc:
		log_step(f"[paramTable] 展开失败，降级为原始步骤：{exc}")
		return merged_payload
	scanned_steps = scanned.get("steps", []) if isinstance(scanned, dict) else []
	if not isinstance(scanned_steps, list) or not scanned_steps:
		log_step("[paramTable] 参数表无有效数据行，跳过展开。")
		return merged_payload
	# ── 项目解析值覆盖参数表默认值（机型 / Cp 版本）──
	# Simple 界面注入的项目解析/人工填写值（GM_RUNTIME_CONFIG_JSON 优先，其次流程 runtimeConfig）
	# 优先于参数表模板默认值（如 defaultTurbine=WT6250D220 / cpVersion=Cp0.429 被实际项目
	# 机型 WT6250D220_A.4 / 华润Ⅰ类-0.429 覆盖）。仅当 runtime 有非空值时才覆盖，
	# 避免污染未指定项目文件夹的既有行为。
	_rt_merged = merged_payload.get("runtimeConfig", {})
	if not isinstance(_rt_merged, dict):
		_rt_merged = {}
	_rt_env = _load_json_env_runtime()
	if not isinstance(_rt_env, dict):
		_rt_env = {}
	_rt_turbine = str(_rt_env.get("turbineType", "") or "").strip() or str(_rt_merged.get("turbineType", "") or "").strip()
	_rt_cp = str(_rt_env.get("cpVersion", "") or "").strip() or str(_rt_merged.get("cpVersion", "") or "").strip()
	# 测风塔/气象引用：项目解析的测风塔编号覆盖参数表占位值（如 M1/Mast1/testMESO）。
	# 参考点/极风速=测风塔名（mastName 优先，其次 mastEntries/mastIds 首座）；气象参考=气象对象名
	# （塔名_轮毂高度_Auto，与测风塔元素名区分，见 wt_project_workdir_parser.meteoName）。
	# 多塔第二塔=第二座塔。⚠ 不能取 sorted(mastIds)[1]：mastIds 在解析器里是 sorted(set(...))，
	# 排序会打乱 CFT 行序（如 CFT=[W2512960, C2536] → mastIds=["C2536","W2512960"]），曾致
	# 第二塔被覆盖成第一塔的气象。须按 mastEntries（CFT 行序）取"第一个不同于主塔的塔"；
	# 无 mastEntries 时才回退 mastIds。单塔时第二塔引用保持参数表原样（其步骤被 towerMode 过滤）。
	_mast_ids_rt = _rt_env.get("mastIds") if _rt_env.get("mastIds") else _rt_merged.get("mastIds")
	_mast_names = [str(x).strip() for x in _mast_ids_rt if str(x).strip()] if isinstance(_mast_ids_rt, list) else []
	_mast_entries_rt = _rt_env.get("mastEntries") if _rt_env.get("mastEntries") else _rt_merged.get("mastEntries")
	_ordered_mast_names = []
	if isinstance(_mast_entries_rt, list):
		for _e in _mast_entries_rt:
			if not isinstance(_e, dict):
				continue
			_n = str(_e.get("mastName", "") or "").strip()
			if _n and _n not in _ordered_mast_names:
				_ordered_mast_names.append(_n)
	if not _ordered_mast_names:
		_ordered_mast_names = list(_mast_names)
	_rt_ref_primary = str(_rt_env.get("mastName", "") or "").strip() or str(_rt_merged.get("mastName", "") or "").strip() or (_ordered_mast_names[0] if _ordered_mast_names else "")
	# 第二座塔 = 行序（或回退序）中第一个非主塔者；推导为空/与主塔同名时保持空，
	# 宁可保留参数表原样，也不把第二塔引用覆盖成与第一塔相同的气象（假成功）。
	_rt_ref_secondary = next((m for m in _ordered_mast_names if m and m != _rt_ref_primary), "")

	def _meteo_for(_mast_name, _hub_default):
		"""气象对象名：优先 mastEntries 里的 meteoName，否则按 mastName_hubHeight_Auto 拼接。"""
		if not _mast_name:
			return ""
		for _e in (_rt_env.get("mastEntries") or _rt_merged.get("mastEntries") or []):
			if isinstance(_e, dict) and str(_e.get("mastName", "")).strip() == _mast_name:
				_meteo = str(_e.get("meteoName", "") or "").strip()
				if _meteo:
					return _meteo
				_hub = str(_e.get("hubHeight", "") or "").strip()
				return f"{_mast_name}_{_hub}_Auto" if _hub else f"{_mast_name}_Auto"
		_hub = str(_hub_default or "").strip()
		return f"{_mast_name}_{_hub}_Auto" if _hub else f"{_mast_name}_Auto"

	_rt_hub = str(_rt_env.get("hubHeight", "") or "").strip() or str(_rt_merged.get("hubHeight", "") or "").strip()
	_rt_meteo = str(_rt_env.get("meteoName", "") or "").strip() or str(_rt_merged.get("meteoName", "") or "").strip()
	if not _rt_meteo:
		_rt_meteo = _meteo_for(_rt_ref_primary, _rt_hub)
	_rt_meteo2 = _meteo_for(_rt_ref_secondary, _rt_hub) if _rt_ref_secondary else ""
	_mast_overrides = {}
	if _mast_names:
		_mast_overrides.update({
			"refmast": _rt_ref_primary,
			"weathername": _rt_meteo,
			"mettowername": _rt_ref_primary,
		})
	if _rt_ref_secondary:
		_mast_overrides.update({
			"refmast2": _rt_ref_secondary,
			"refclimatology2": _rt_meteo2 or _rt_ref_secondary,
		})
	if _rt_turbine or _rt_cp or _mast_overrides:
		_ovr = []
		for _s in scanned_steps:
			if not isinstance(_s, dict):
				continue
			_sp = _s.get("stepParams")
			if not isinstance(_sp, dict):
				continue
			if _rt_turbine:
				for _k in ("defaultturbine", "drawturbinetype"):
					if _k in _sp and str(_sp[_k]).strip() != _rt_turbine:
						_ovr.append(f"{_k}:{_sp[_k]}->{_rt_turbine}")
						_sp[_k] = _rt_turbine
			if _rt_cp and "cpversion" in _sp and str(_sp["cpversion"]).strip() != _rt_cp:
				_ovr.append(f"cpversion:{_sp['cpversion']}->{_rt_cp}")
				_sp["cpversion"] = _rt_cp
			for _k, _v in _mast_overrides.items():
				if _k in _sp and _v and str(_sp[_k]).strip() != _v:
					_ovr.append(f"{_k}:{_sp[_k]}->{_v}")
					_sp[_k] = _v
		if _ovr:
			log_step(f"[paramTable] 项目解析值覆盖参数表默认（{len(_ovr)} 处）：{'；'.join(dict.fromkeys(_ovr))}")
	# 包 stepIds 同步映射：参数表展开会把模板步骤 id 改为 `{原id}_scan{行}_{序号}`，
	# 若不改写，flowPackages 中登记整流程的包（stepIds 引用原模板 id）在校验时会报
	# "引用了不存在的步骤"。把每个原 id 替换为展开后对应的一批 id，保持包与 steps 一致。
	base_to_expanded = {}
	scanned_ids = set()
	for s in scanned_steps:
		if not isinstance(s, dict):
			continue
		sid = str(s.get("id", "")).strip()
		if sid:
			scanned_ids.add(sid)
		base = sid.rsplit("_scan", 1)[0] if "_scan" in sid else sid
		if base:
			base_to_expanded.setdefault(base, []).append(sid)
	mapped_packages = []
	for pkg in merged_payload.get("flowPackages", []):
		if not isinstance(pkg, dict):
			mapped_packages.append(pkg)
			continue
		new_pkg = dict(pkg)
		new_step_ids = []
		for pid in pkg.get("stepIds", []) or []:
			pid = str(pid).strip()
			if pid in base_to_expanded:
				new_step_ids.extend(base_to_expanded[pid])
			elif pid in scanned_ids:
				# 非模板步骤（展开后仍以原 id 存在）保留
				new_step_ids.append(pid)
			# 其余：模板 id 被行级过滤（如多塔模式下单塔专属步骤）排除，直接丢弃，
			# 否则校验会报"流程包引用了不存在的步骤"。
		new_pkg["stepIds"] = new_step_ids
		mapped_packages.append(new_pkg)
	expanded = {
		"runtimeConfig": merged_payload.get("runtimeConfig", {}),
		"flowPackages": mapped_packages,
		"steps": [s for s in scanned_steps if isinstance(s, dict)],
	}
	row_count = sum(1 for s in scanned_steps if str(s.get("stage", "")) == "separator") + (1 if scanned_steps and str(scanned_steps[0].get("stage", "")) != "separator" else 0)
	log_step(f"[paramTable] 已按参数表展开：{len(expanded['steps'])} 个步骤（参数表：{os.path.basename(param_table)}）。")
	return expanded


@lru_cache(maxsize=1)
def _load_runtime_config():
	project_settings = _load_project_settings()
	flow_payload = _load_flow_payload()
	flow_runtime = flow_payload.get("runtimeConfig", {}) if isinstance(flow_payload, dict) else {}
	if not isinstance(flow_runtime, dict):
		flow_runtime = {}

	env_runtime = {}
	raw_env_payload = str(os.environ.get(RUNTIME_CONFIG_ENV_KEY, "")).strip()
	if raw_env_payload:
		try:
			env_runtime = json.loads(raw_env_payload)
		except Exception as exc:
			log_step(f"读取运行参数环境变量失败，忽略环境覆盖: {exc}")
			env_runtime = {}
	if not isinstance(env_runtime, dict):
		env_runtime = {}

	def _pick_value(key, legacy_key, default_value):
		for source in (env_runtime, flow_runtime):
			value = source.get(key)
			if value not in (None, ""):
				return str(value).strip()
		value = project_settings.get(legacy_key, "")
		if value not in (None, ""):
			return str(value).strip()
		return str(default_value).strip()

	def _pick_bool(key, legacy_key, default_value):
		for source in (env_runtime, flow_runtime):
			value = source.get(key)
			if value not in (None, ""):
				if isinstance(value, bool):
					return value
				return str(value).strip().lower() in ("1", "true", "yes", "on")
		value = project_settings.get(legacy_key, "")
		if value not in (None, ""):
			return str(value).strip().lower() in ("1", "true", "yes", "on")
		return bool(default_value)

	# 保留环境/流程注入的扩展键（mastIds/turbineType/synthesisDesc/towerMode 等），
	# 供 ${runtime.xxx} 占位替换与运行期逻辑（如单塔/多塔自动决定）使用。
	extra_runtime = {}
	for source in (env_runtime, flow_runtime):
		if not isinstance(source, dict):
			continue
		for key, value in source.items():
			if key in extra_runtime:
				continue
			if value is None or value == "":
				continue
			extra_runtime[key] = value

	return {
		"gmExe": _pick_value("gmExe", "GM_EXE", DEFAULT_GM_EXE),
		"sourceFilePath": _pick_value("sourceFilePath", "SOURCE_FILE_PATH", DEFAULT_SOURCE_FILE_PATH),
		"outputDir": _pick_value("outputDir", "OUTPUT_DIR", DEFAULT_OUTPUT_DIR),
		"projectionFilePath": _pick_value("projectionFilePath", "PROJECTION_FILE_PATH", DEFAULT_PROJECTION_FILE_PATH),
		"controlMapPath": _pick_value("controlMapPath", "CONTROL_MAP_PATH", ""),
		# 执行中自动更新控件模板开关（runtimeConfig.templateAutoUpdate 或环境变量
		# WT_TEMPLATE_AUTO_UPDATE）：开启=每次运行截图并与上次模板对比、不一致才替换，
		# 且自动把模板关联为步骤兜底；关闭=完全不采集/不更新/不关联兜底（默认关闭，
		# 避免“假成功”时的错误截图污染模板库）。
		"templateAutoUpdate": _pick_bool("templateAutoUpdate", "TEMPLATE_AUTO_UPDATE", False),
		**extra_runtime,
	}


def _apply_runtime_config():
	global GM_EXE, SOURCE_FILE_PATH, OUTPUT_DIR, PROJECTION_FILE_PATH
	runtime_config = _load_runtime_config()
	GM_EXE = runtime_config.get("gmExe", "").strip()
	SOURCE_FILE_PATH = runtime_config.get("sourceFilePath", "").strip()
	OUTPUT_DIR = runtime_config.get("outputDir", "").strip()
	PROJECTION_FILE_PATH = runtime_config.get("projectionFilePath", "").strip()


def _refresh_flow_caches():
	for cached_func in [
		_load_project_settings,
		_load_flow_payload,
		_load_runtime_config,
		_load_flow_definition,
		_load_flow_packages,
	]:
		try:
			cached_func.cache_clear()
		except Exception:
			pass


@lru_cache(maxsize=1)
def _load_flow_definition():
	payload = _load_flow_payload()
	steps = payload.get("steps", []) if isinstance(payload, dict) else []
	step_map = {}
	for step in steps:
		if not isinstance(step, dict):
			continue
		step_id = str(step.get("id", "")).strip()
		if step_id:
			step_map[step_id] = step
	return step_map


def _get_flow_step(step_id):
	return _load_flow_definition().get(step_id, {})


# 运行循环在单步执行前放置 {step_id, context}；定位器在控件定位时通过流程定义取器读取它，
# 把控件名里的 ${...} 占位按实际运行上下文解析（跟随导入文件/设置命名变化的动态控件）。
_ACTIVE_FLOW_CTX = threading.local()


def _get_flow_step_resolved(step_id):
	"""返回 step；若当前运行循环正执行该 step，则对 controls 做 ${...} 动态解析。

	使流程库里写死的控件名（如 'testMESO'/'WT6250D2'/'综合3'）可改为
	${stepParams.x}/${steps.<stepId>.<output>}/${runtime.x} 占位，运行时按实际
	文件名/设置名定位，避免换项目/换文件后失配。不在运行循环内或与当前 step 不符时
	原样返回（保持旧行为）。
	"""
	step = _get_flow_step(step_id)
	if not isinstance(step, dict):
		return step
	active = getattr(_ACTIVE_FLOW_CTX, "snapshot", None)
	if not active or active.get("step_id") != step_id:
		return step
	controls = step.get("controls")
	if isinstance(controls, list) and controls:
		try:
			resolved = _resolve_dynamic_value(
				[dict(c) for c in controls if isinstance(c, dict)],
				step_id,
				active.get("context") or {},
			)
		except Exception:
			resolved = None
		if isinstance(resolved, list):
			step = dict(step)
			step["controls"] = resolved
	return step


def _get_main_window_candidates():
	"""提供目标软件(MUP)主窗口候选（hwnd 字典列表），供定位器 fallback 权威取窗。

	复用预检同款 find_main_windows：按进程名 MUPSmartClient 识别主窗，UIPI 免疫，
	比定位器自身的进程名枚举更可靠（实测运行中 MUP 枚举偶发为空导致误回退到自己 Tk 窗）。
	"""
	try:
		main_windows = _get_wt_window_helpers().find_main_windows(
			MAIN_WINDOW_TITLE_RE,
			class_name_keywords=("MUPSmartClient",),
		)
		return list(main_windows or [])
	except Exception:
		return []


def _get_flow_locator():
	global _FLOW_LOCATOR_CONFIGURED
	if not _FLOW_LOCATOR_CONFIGURED:
		wt_flow_locator.configure_flow_locator(
			get_step_definition=_get_flow_step_resolved,
			log_step=log_step,
			get_main_window_candidates=_get_main_window_candidates,
		)
		_FLOW_LOCATOR_CONFIGURED = True
	return wt_flow_locator


def _resolve_runtime_control_map_path():
	"""??????????????????flow ?????controlMapPath > runtime_config > ???WT_CONTROL_MAP_PATH??"""
	flow_payload = _load_flow_payload()
	if isinstance(flow_payload, dict):
		root_path = str(flow_payload.get("controlMapPath", "")).strip()
		if root_path:
			return root_path
		packages = flow_payload.get("flowPackages", [])
		if isinstance(packages, list):
			for package in packages:
				if isinstance(package, dict):
					package_path = str(package.get("controlMapPath", "")).strip()
					if package_path:
						return package_path
	runtime_path = str(_load_runtime_config().get("controlMapPath", "")).strip()
	if runtime_path:
		return runtime_path
	return str(os.environ.get("WT_CONTROL_MAP_PATH", "")).strip() or None


# 模板兜底所需的图像匹配实现：复用 wt_projection_helpers 的多尺度匹配
# （此前 _get_flow_executor 直接引用 _locate_template_center_by_path 但未定义 → NameError，
#  导致执行器模板兜底从未真正接线）。
_locate_template_center_by_path = wt_projection_helpers.locate_template_center_by_path


def _auto_capture_template(step_id, step_definition):
    """执行中自动采集/更新控件模板（受 runtimeConfig.templateAutoUpdate 开关控制）。

    开关开启时（每次运行）：
      - 用 flow_locator 定位控件 → 截图控件真实区域；
      - 与上次模板做感知哈希对比：一致则保留（控件外观未变，避免假成功/界面波动
        的错误截图覆盖掉可用模板）；不一致或无旧模板才替换保存；
      - 保存后把模板 key 写入步骤控件的 templateKey（内存），使执行器模板兜底
        自动接线（P0）立即生效，供后续运行复用。
    开关关闭时（默认）：完全不采集截图模板，也不编辑/关联兜底功能。

    best-effort：任何失败静默返回 None。
    """
    try:
        import image_template_index
        import pyautogui
        import wt_flow_locator as flow_locator
    except Exception:
        return None
    # 开关：未开启自动更新模板 → 不保存截图、不编辑兜底
    try:
        if not _load_runtime_config().get("templateAutoUpdate"):
            return None
    except Exception:
        return None

    step_definition = step_definition if isinstance(step_definition, dict) else {}
    controls = step_definition.get("controls", [])
    if not isinstance(controls, list) or not controls or not isinstance(controls[0], dict):
        return None
    control = controls[0]
    action_config = step_definition.get("actionConfig", {}) if isinstance(step_definition.get("actionConfig"), dict) else {}
    control_id = str(action_config.get("controlId", "")).strip()
    window_title = str(step_definition.get("windowTitle", "") or control.get("windowTitle", "")).strip()
    control_name = str(control.get("name", "") or control.get("displayName", "") or control_id or "control").strip()

    _cap_dir, cap_path = image_template_index.auto_capture_path(window_title, control_name)

    wrapper = flow_locator.find_flow_control(
        step_id, control_id=control_id,
        timeout_seconds=1.5, window_title_hint=window_title,
    )
    if wrapper is None:
        return None

    rect = None
    try:
        rect = flow_locator.get_wrapper_rectangle(wrapper)
    except Exception:
        rect = None
    if not rect:
        try:
            rect = wrapper.rectangle()
        except Exception:
            rect = None
    if rect is None:
        return None
    try:
        if isinstance(rect, dict):
            left, top = int(rect.get("left", 0)), int(rect.get("top", 0))
            right, bottom = int(rect.get("right", 0)), int(rect.get("bottom", 0))
        else:
            left, top = int(rect.left), int(rect.top)
            right, bottom = int(rect.right), int(rect.bottom)
    except Exception:
        return None
    padding = 3
    left = max(0, left - padding)
    top = max(0, top - padding)
    width = right - left + padding * 2
    height = bottom - top + padding * 2
    if width < 8 or height < 8 or width > 4000 or height > 4000:
        return None

    try:
        shot = pyautogui.screenshot(region=(left, top, width, height))
        # 与上次模板对比：外观一致则保留旧模板，不替换（避免假成功污染模板库）
        if os.path.exists(cap_path) and image_template_index.images_are_similar(cap_path, shot):
            return cap_path
        os.makedirs(_cap_dir, exist_ok=True)
        shot.save(cap_path)
        image_template_index.reload()
        # 编辑对应兜底：把本次模板关联为步骤控件的 templateKey（内存，执行器
        # 自动接线后该步骤下次失败即可用此模板兜底匹配）
        rel_key = os.path.relpath(cap_path, image_template_index.IMAGE_TEMPLATE_ROOT).replace("\\", "/")
        control["templateKey"] = "auto_captured/" + rel_key.split("auto_captured/", 1)[-1]
        log_step(f"已自动更新控件模板: {cap_path}")
        return cap_path
    except Exception as exc:
        log_step(f"自动采集控件模板失败: step={step_id}, err={exc}")
        return None


def _get_flow_executor():
	global _FLOW_EXECUTOR_CONFIGURED
	if not _FLOW_EXECUTOR_CONFIGURED:
		wt_flow_executor.configure_flow_executor(
			get_step_definition=_get_flow_step_resolved,
			get_flow_package=_get_flow_package,
			get_step_params=_get_step_params,
			resolve_dynamic_value=_resolve_dynamic_value,
			log_step=log_step,
			click_flow_control=_click_flow_control,
			click_relative_region=_click_relative_region,
		click_relative_anchor=_click_relative_anchor,
			check_all_toggles=_check_all_unchecked_toggle_controls,
			select_list_items=_select_list_items_runtime,
			focus_flow_control=_focus_flow_control,
			type_text_into_flow_control=_type_text_into_flow_control,
			type_text_into_relative_region=_type_text_into_relative_region,
			select_dropdown_item_runtime=_select_dropdown_item_runtime,
			drag_between_flow_controls=_drag_between_flow_controls,
			mouse_wheel_on_flow_control=_mouse_wheel_on_flow_control,
			wait_for_flow_control_condition=_wait_for_flow_control_condition,
			find_flow_control=getattr(wt_flow_locator, "find_flow_control", None),
			get_wrapper_value_snapshot=getattr(wt_flow_locator, "get_wrapper_value_snapshot", None),
			menu_select_flow=_menu_select_flow,
			locate_template_center_by_path=_locate_template_center_by_path,
			report_step_result=_record_step_result,
			run_ai_intervention_after_failure=_run_ai_intervention_after_failure,
			auto_capture_template=_auto_capture_template,
			control_map_path=_resolve_runtime_control_map_path(),
		)
		_FLOW_EXECUTOR_CONFIGURED = True
	return wt_flow_executor


def _get_wt_business_steps():
	global _WT_BUSINESS_STEPS_CONFIGURED
	if not _WT_BUSINESS_STEPS_CONFIGURED:
		wt_business_steps.configure_wt_business_steps(
			log_step=log_step,
			activate_and_maximize_main_window=activate_and_maximize_main_window,
			configure_projection=_configure_projection,
			click_unknown_projection_if_present=_click_unknown_projection_if_present,
			type_path_into_open_dialog=_type_path_into_open_dialog,
			try_click_layer_tree_expand_icon=_try_click_layer_tree_expand_icon,
			right_click_flow_tree_item=_right_click_flow_tree_item,
			right_click_tree_item_by_title_re=_right_click_tree_item_by_title_re,
			click_context_menu_with_fallback=_click_context_menu_with_fallback,
			click_flow_control=_click_flow_control,
			get_step_param=_get_step_param,
			get_flow_ref_param=_get_flow_ref_param,
			run_ui_tars=_run_ui_tars,
			handle_dwg_projection_selection=_handle_dwg_projection_selection,
			get_gm_exe=lambda: GM_EXE,
			get_source_file_path=lambda: SOURCE_FILE_PATH,
			get_output_dir=lambda: OUTPUT_DIR,
			get_projection_file_path=lambda: PROJECTION_FILE_PATH,
			get_main_window_uipath=lambda: MAIN_WINDOW_UIPATH,
		)
		_WT_BUSINESS_STEPS_CONFIGURED = True
	return wt_business_steps


def _get_wt_projection_helpers():
	global _WT_PROJECTION_HELPERS_CONFIGURED
	if not _WT_PROJECTION_HELPERS_CONFIGURED:
		wt_projection_helpers.configure_wt_projection_helpers(
			log_step=log_step,
			click_flow_control=_click_flow_control,
			activate_and_maximize_main_window=activate_and_maximize_main_window,
			confirm_open_file_dialog=_confirm_open_file_dialog,
			get_projection_file_path=lambda: PROJECTION_FILE_PATH,
			get_ui_tars_runner=lambda: UI_TARS_RUNNER,
			get_image_template_dir=lambda: IMAGE_TEMPLATE_DIR,
			get_layer_tree_template_dir=lambda: LAYER_TREE_TEMPLATE_DIR,
			get_debug_screenshot_dir=lambda: DEBUG_SCREENSHOT_DIR,
			get_image_templates=lambda: IMAGE_TEMPLATES,
		)
		_WT_PROJECTION_HELPERS_CONFIGURED = True
	return wt_projection_helpers


def _get_wt_window_helpers():
	global _WT_WINDOW_HELPERS_CONFIGURED
	if not _WT_WINDOW_HELPERS_CONFIGURED:
		wt_window_helpers.configure_wt_window_helpers(
			user32=user32,
			enum_windows_proc=EnumWindowsProc,
			wm_null=WM_NULL,
			smto_abort_if_hung=SMTO_ABORTIFHUNG,
			sw_restore=SW_RESTORE,
			sw_maximize=SW_MAXIMIZE,
			get_window_text=_get_window_text,
			get_window_rect=_get_window_rect,
			log_step=log_step,
			click_flow_control=_click_flow_control,
			focus_flow_control=_focus_flow_control,
		)
		_WT_WINDOW_HELPERS_CONFIGURED = True
	return wt_window_helpers


def _get_wt_run_reporting():
	global _WT_RUN_REPORTING_CONFIGURED
	if not _WT_RUN_REPORTING_CONFIGURED:
		wt_run_reporting.configure_run_reporting(base_dir=BASE_DIR, log_step=log_step)
		_WT_RUN_REPORTING_CONFIGURED = True
	return wt_run_reporting


# ── 模块级透传函数（数据驱动生成，替代原先逐条手写的转发包装）──
# 每个转发名在首次被调用时通过对应的惰性 getter 完成模块配置，
# 行为与原手写包装完全一致；这里只负责批量生成 `_<name>` 代理。
def _make_module_proxy(module_getter, name):
	def _proxy(*args, **kwargs):
		return getattr(module_getter(), name)(*args, **kwargs)
	_proxy.__name__ = name
	return _proxy


def _install_module_proxies(module_getter, names):
	for name in names:
		globals()["_" + name] = _make_module_proxy(module_getter, name)


_FLOW_LOCATOR_FORWARDED = (
	"normalize_control_type_name",
	"strip_wrapping_quotes",
	"normalize_match_text",
	"build_locator_text",
	"build_common_locator_candidates",
	"get_control_definition_match_score",
	"split_locator_parts",
	"parse_aux_check_line",
	"parse_window_title_candidates",
	"get_wrapper_text",
	"get_wrapper_class_name",
	"get_wrapper_control_type",
	"get_wrapper_localized_control_type",
	"get_wrapper_automation_id",
	"get_wrapper_framework_id",
	"get_wrapper_help_text",
	"get_wrapper_process_id",
	"get_wrapper_handle_text",
	"get_wrapper_is_enabled",
	"get_wrapper_is_offscreen",
	"get_wrapper_is_keyboard_focusable",
	"get_wrapper_has_keyboard_focus",
	"get_wrapper_parent_signatures",
	"get_wrapper_child_signatures",
	"value_matches",
	"wrapper_matches_locator",
	"wrapper_matches_control_definition",
	"score_control_match",
	"get_control_process_candidates",
	"get_foreground_window_handle",
	"iter_flow_search_windows",
	"find_flow_control",
	"wait_for_flow_control_condition",
	"get_flow_control_definition",
	"click_flow_control",
	"click_relative_anchor",
	"click_relative_region",
	"check_all_unchecked_toggle_controls",
	"select_list_items_runtime",
	"click_menu_candidate_by_text",
	"focus_flow_control",
	"type_text_into_wrapper",
	"type_text_into_flow_control",
	"type_text_into_relative_region",
	"select_dropdown_item_runtime",
	"menu_select_flow",
	"get_wrapper_center",
	"drag_between_flow_controls",
	"mouse_wheel_on_flow_control",
)
_WT_WINDOW_HELPERS_FORWARDED = (
	"click_unknown_projection_if_present",
	"find_open_dialog",
	"confirm_open_file_dialog",
	"type_path_into_open_dialog",
)
_WT_PROJECTION_HELPERS_FORWARDED = (
	"run_ui_tars",
	"build_projection_ai_prompt",
	"build_dwg_projection_confirmation_prompt",
	"build_dwg_projection_ai_prompt",
	"get_template_path",
	"locate_template_center",
	"locate_template_center_by_path",
	"capture_debug_screenshot",
	"log_projection_debug_context",
	"click_template",
	"try_click_layer_tree_expand_icon",
	"find_config_window",
	"config_window_is_open",
	"click_button_in_config_window",
	"configure_projection_by_image",
)
_WT_FLOW_EXECUTOR_FORWARDED = (
	"sleep_seconds",
	"run_action_step",
	"resolve_fallback_template_path",
	"apply_position_offset",
	"run_action_step_with_template_fallback",
	"run_flow_ref_step",
	"is_setup_step",
	"execute_step_by_id",
)
_WT_BUSINESS_STEPS_FORWARDED = (
	"get_step_registry",
	"get_step_registry_map",
)

_install_module_proxies(_get_flow_locator, _FLOW_LOCATOR_FORWARDED)
_install_module_proxies(_get_wt_window_helpers, _WT_WINDOW_HELPERS_FORWARDED)
_install_module_proxies(_get_wt_projection_helpers, _WT_PROJECTION_HELPERS_FORWARDED)
_install_module_proxies(_get_flow_executor, _WT_FLOW_EXECUTOR_FORWARDED)
_install_module_proxies(_get_wt_business_steps, _WT_BUSINESS_STEPS_FORWARDED)


def _record_step_result(run_report, step_id, step_name, status, action_type="", strategy="", elapsed=0.0, error="", extra=None):
	# 任务栏进度 + 控制台标题：每步结束更新（不创建窗口、不置顶、不抢焦点）
	global _progress_current, _progress_total
	try:
		if status in ("success", "failed", "skipped"):
			_progress_current += 1
		# 本函数在后台自动化线程中调用；Tk/COM 收尾一律调度到主线程执行。
		_ui_safe_call(lambda: _update_taskbar_progress(_progress_current, _progress_total, status="running"))
		_ui_safe_call(lambda: _update_progress_title(_progress_current, _progress_total, step_name=step_name, status=status))
	except Exception:
		pass
	return _get_wt_run_reporting().report_step_result(
		run_report,
		step_id,
		step_name,
		status,
		action_type=action_type,
		strategy=strategy,
		elapsed=elapsed,
		error=error,
		extra=extra,
	)


def _env_flag(name, default=False):
	value = str(os.environ.get(name, "")).strip().lower()
	if not value:
		return bool(default)
	return value in {"1", "true", "yes", "on"}


def _env_int(name, default_value):
	try:
		return max(1, int(str(os.environ.get(name, "")).strip() or default_value))
	except Exception:
		return int(default_value)


def _read_recent_log_lines(limit=10):
	if not os.path.exists(LOG_FILE):
		return []
	try:
		with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as file_obj:
			lines = [line.rstrip("\r\n") for line in file_obj.readlines()]
		return [line for line in lines[-max(1, int(limit)): ] if line.strip()]
	except Exception:
		return []


def _summarize_recent_step_results(run_report, limit=3):
	if not isinstance(run_report, dict):
		return []
	step_results = run_report.get("stepResults", [])
	if not isinstance(step_results, list):
		return []
	items = []
	for item in step_results[-max(1, int(limit)):]:
		if not isinstance(item, dict):
			continue
		step_id = str(item.get("stepId", "")).strip()
		step_name = str(item.get("stepName", "")).strip()
		status = str(item.get("status", "")).strip() or "unknown"
		elapsed = item.get("elapsedSeconds", 0)
		items.append(f"{step_id} | {step_name} | {status} | {elapsed}s")
	return items


def _summarize_action_for_ai(step_definition):
	if not isinstance(step_definition, dict):
		return ""
	action_config = step_definition.get("actionConfig", {})
	if not isinstance(action_config, dict):
		action_config = {}
	action_name = str(action_config.get("action", "")).strip()
	control_id = str(action_config.get("controlId", "")).strip()
	text_value = str(action_config.get("text", action_config.get("value", ""))).strip()
	parent_window = action_config.get("parentWindow", {}) if isinstance(action_config.get("parentWindow"), dict) else {}
	parent_title = str(parent_window.get("title", "")).strip()
	parent_class = str(parent_window.get("className", "")).strip()
	parent_framework = str(parent_window.get("frameworkId", "")).strip()
	parts = []
	if action_name:
		parts.append(f"action={action_name}")
	if control_id:
		parts.append(f"controlId={control_id}")
	if text_value:
		parts.append(f"text={text_value}")
	if parent_title or parent_class or parent_framework:
		parts.append(
			f"parentWindow(title={parent_title or '(空)'}, class={parent_class or '(空)'}, framework={parent_framework or '(空)'})"
		)
	return "；".join(parts)


def _summarize_control_target_for_ai(step_definition):
	if not isinstance(step_definition, dict):
		return ""
	action_config = step_definition.get("actionConfig", {})
	if not isinstance(action_config, dict):
		action_config = {}
	control_id = str(action_config.get("controlId", "")).strip()
	step_window_title = str(step_definition.get("windowTitle", "")).strip()
	matched_control = {}
	for item in step_definition.get("controls", []):
		if not isinstance(item, dict):
			continue
		if str(item.get("id", "")).strip() == control_id:
			matched_control = item
			break
	parts = []
	if step_window_title:
		parts.append(f"stepWindowTitle={step_window_title}")
	if control_id:
		parts.append(f"controlId={control_id}")
	target_method = str(matched_control.get("targetMethod", "")).strip()
	target_value = str(matched_control.get("targetValue", "")).strip()
	control_window_title = str(matched_control.get("windowTitle", "")).strip()
	if target_method:
		parts.append(f"targetMethod={target_method}")
	if target_value:
		parts.append(f"targetValue={target_value}")
	if control_window_title:
		parts.append(f"controlWindowTitle={control_window_title}")
	return "；".join(parts)


def _normalize_requested_step_ids(steps_requested):
	if not isinstance(steps_requested, list):
		return []
	items = []
	for raw_item in steps_requested:
		if isinstance(raw_item, dict):
			step_id = str(raw_item.get("id", raw_item.get("stepId", ""))).strip()
		else:
			step_id = str(raw_item or "").strip()
		if step_id:
			items.append(step_id)
	return items


def _format_step_hint_for_ai(step_id):
	step_id = str(step_id or "").strip()
	if not step_id:
		return ""
	step_definition = _get_flow_step(step_id)
	step_name = str(step_definition.get("name", "")).strip() or step_id
	action_summary = _summarize_action_for_ai(step_definition)
	if action_summary:
		return f"{step_id} | {step_name} | {action_summary}"
	return f"{step_id} | {step_name}"


def _describe_requested_step_neighbors(step_id, run_report):
	run_report = run_report if isinstance(run_report, dict) else {}
	requested_step_ids = _normalize_requested_step_ids(run_report.get("stepsRequested", []))
	if requested_step_ids and step_id in requested_step_ids:
		current_index = requested_step_ids.index(step_id)
	else:
		current_index = min(len(run_report.get("stepResults", [])), max(0, len(requested_step_ids) - 1))
	total_count = len(requested_step_ids) if requested_step_ids else max(1, current_index + 1)
	previous_step = requested_step_ids[current_index - 1] if current_index - 1 >= 0 and current_index - 1 < len(requested_step_ids) else ""
	current_step = requested_step_ids[current_index] if current_index < len(requested_step_ids) else str(step_id or "").strip()
	next_step = requested_step_ids[current_index + 1] if current_index + 1 < len(requested_step_ids) else ""
	return {
		"currentIndex": current_index + 1,
		"totalCount": total_count,
		"previousStep": _format_step_hint_for_ai(previous_step),
		"currentStep": _format_step_hint_for_ai(current_step),
		"nextStep": _format_step_hint_for_ai(next_step),
	}


def _build_ai_next_action_guidance(step_id, step_definition):
	step_name = str(step_definition.get("name", "")).strip() or str(step_id or "").strip()
	action_config = step_definition.get("actionConfig", {})
	if not isinstance(action_config, dict):
		action_config = {}
	action_name = str(action_config.get("action", "")).strip().lower()
	text_value = str(action_config.get("text", action_config.get("value", ""))).strip()
	action_instruction_map = {
		"click": "点击目标控件，让当前步骤完成。",
		"double_click": "双击目标控件，让当前步骤完成。",
		"type_text": "向目标控件输入要求的文本，让当前步骤完成。",
		"type_text_relative": "在指定相对区域输入要求的文本，让当前步骤完成。",
		"click_relative_region": "在指定相对区域执行点击，让当前步骤完成。",
		"select_dropdown_item_runtime": "展开下拉框并选择目标项，让当前步骤完成。",
		"drag_between_controls": "完成当前步骤要求的拖拽动作。",
		"mouse_wheel": "在目标区域执行滚轮操作，让当前步骤完成。",
	}
	action_instruction = action_instruction_map.get(action_name, "按当前步骤定义完成这一步的业务动作。")
	control_target_summary = _summarize_control_target_for_ai(step_definition)
	lines = [
		"你现在应该这样做：",
		f"1. 只聚焦完成当前卡住步骤 `{step_id} | {step_name}`，不要扩散到别的步骤。",
		"2. 先根据当前屏幕判断这一步是否实际上已经完成；如果已完成，只做最小确认。",
		f"3. 如果尚未完成，立即执行这一步需要的最小操作：{action_instruction}",
	]
	if text_value:
		lines.append(f"4. 当前步骤要求输入/选择的关键值：{text_value}")
	if control_target_summary:
		lines.append(f"5. 优先按以下定位线索操作：{control_target_summary}")
	else:
		lines.append("5. 优先使用当前窗口中与本步骤名称、动作最匹配的控件，不要盲目重做前序动作。")
	lines.append("6. 当前步骤完成后立刻停止，不要继续执行后续业务步骤。")
	return lines


def _build_ai_intervention_prompt(step_id, context, original_error, fallback_error=None):
	step_definition = _get_flow_step(step_id)
	step_name = str(step_definition.get("name", "")).strip() or step_id
	run_report = context.get("run_report", {}) if isinstance(context, dict) else {}
	recent_steps = _summarize_recent_step_results(run_report, limit=3)
	recent_logs = _read_recent_log_lines(limit=_env_int(AI_INTERVENTION_LOG_LINES_ENV_KEY, 10))
	neighbor_summary = _describe_requested_step_neighbors(step_id, run_report)
	action_summary = _summarize_action_for_ai(step_definition)
	control_target_summary = _summarize_control_target_for_ai(step_definition)
	lines = [
		"当前 WT 自动化流程在执行步骤时失败，常规执行与模板兜底都未成功，请你只辅助处理当前卡住步骤。",
		f"当前运行到第 {neighbor_summary['currentIndex']}/{neighbor_summary['totalCount']} 个请求步骤。",
		f"当前卡住步骤: {step_id} | {step_name}",
	]
	if action_summary:
		lines.append(f"当前步骤动作摘要: {action_summary}")
	if control_target_summary:
		lines.append(f"当前步骤定位线索: {control_target_summary}")
	lines.append(f"原始失败原因: {original_error}")
	if fallback_error:
		lines.append(f"模板兜底失败原因: {fallback_error}")
	if neighbor_summary.get("previousStep") or neighbor_summary.get("currentStep") or neighbor_summary.get("nextStep"):
		lines.append("紧邻步骤上下文:")
		if neighbor_summary.get("previousStep"):
			lines.append(f"- 上一步: {neighbor_summary['previousStep']}")
		if neighbor_summary.get("currentStep"):
			lines.append(f"- 当前步骤: {neighbor_summary['currentStep']}")
		if neighbor_summary.get("nextStep"):
			lines.append(f"- 下一步(仅供识别上下文，不要执行): {neighbor_summary['nextStep']}")
	if recent_steps:
		lines.append("最近已记录步骤:")
		lines.extend(f"- {item}" for item in recent_steps)
	if recent_logs:
		lines.append("最近日志:")
		lines.extend(f"- {item}" for item in recent_logs)
	lines.extend(_build_ai_next_action_guidance(step_id, step_definition))
	lines.extend(
		[
			"请遵守以下规则：",
			"1. 只处理当前卡住步骤，不要从头重跑整个流程。",
			"2. 如果发现当前步骤实际已完成，只需做最少必要确认，不要重复已成功步骤。",
			"3. 优先基于当前屏幕和当前弹窗状态判断下一次最小操作，不要凭空假设界面状态。",
			"4. 不要把“下一步”当成执行目标；它只用于帮助你识别当前所处界面。",
			"5. 完成当前步骤后停止，不要继续执行后续业务步骤。",
		]
	)
	return "\n".join(lines)


def _run_ai_intervention_after_failure(step_id, context, original_error=None, fallback_error=None):
	if not _env_flag(ENABLE_AI_INTERVENTION_ENV_KEY, default=False):
		return {}
	step_definition = _get_flow_step(step_id)
	step_name = str(step_definition.get("name", "")).strip() or step_id
	prompt = _build_ai_intervention_prompt(step_id, context, original_error, fallback_error=fallback_error)
	log_step(f"模板兜底失败，开始调用 UI-TARS 介入当前步骤: step={step_id}, name={step_name}")
	run_result = _run_ui_tars(prompt, step_name=f"失败后AI介入_{step_id}")
	return {
		"aiInterventionUsed": True,
		"aiInterventionMode": "ui_tars_desktop",
		"aiInterventionStepId": step_id,
		"aiInterventionStepName": step_name,
		"aiInterventionOriginalError": str(original_error or ""),
		"aiInterventionFallbackError": str(fallback_error or ""),
		"aiInterventionLogs": run_result if isinstance(run_result, dict) else {},
	}


def _get_step_params(step_id):
	step_definition = _get_flow_step(step_id)
	step_params = step_definition.get("stepParams", {})
	if not isinstance(step_params, dict):
		return {}
	return step_params


def _get_step_param(context, step_id, key, default=""):
	step_params = context.get("step_params", {}).get(step_id, {})
	if not isinstance(step_params, dict):
		step_params = {}
	value = step_params.get(key, default)
	return default if value in (None, "") else value


def _get_flow_ref_param(context, key, default=""):
	flow_ref_param_stack = context.get("flow_ref_param_stack", [])
	if not flow_ref_param_stack:
		return default
	current = flow_ref_param_stack[-1]
	if not isinstance(current, dict):
		return default
	value = current.get(key, default)
	return default if value in (None, "") else value


@lru_cache(maxsize=1)
def _load_flow_packages():
	payload = _load_flow_payload()
	packages = payload.get("flowPackages", []) if isinstance(payload, dict) else []
	package_map = {}
	for package in packages:
		if not isinstance(package, dict):
			continue
		package_id = str(package.get("id", "")).strip()
		if package_id:
			package_map[package_id] = package
	return package_map


def _get_flow_package(package_id):
	return _load_flow_packages().get(str(package_id or "").strip(), {})


def _resolve_dynamic_value(value, step_id, context):
	if isinstance(value, dict):
		return {key: _resolve_dynamic_value(item, step_id, context) for key, item in value.items()}
	if isinstance(value, list):
		return [_resolve_dynamic_value(item, step_id, context) for item in value]
	if not isinstance(value, str):
		return value

	step_outputs = context.get("step_outputs", {})
	step_params = context.get("step_params", {}).get(step_id, {})
	runtime_config = context.get("runtime_config", {})
	flow_ref_param_stack = context.get("flow_ref_param_stack", [])
	flow_ref_params = flow_ref_param_stack[-1] if flow_ref_param_stack else {}

	def _replace(match_obj):
		expression = match_obj.group(1).strip()
		if expression.startswith("runtime."):
			key = expression.split(".", 1)[1]
			value = runtime_config.get(key)
			if value not in (None, ""):
				return str(value)
			# 综合描述兜底：runtime.synthesisDesc 未注入时回退到综合名称，
			# 避免 send_keys 把未解析的 ${runtime.synthesisDesc} 花括号当按键码
			# （pywinauto 报 "Unknown code"）。
			if key == "synthesisDesc":
				fallback = step_params.get("synthesisname") or step_params.get("synthesisName")
				if fallback not in (None, ""):
					return str(fallback)
			return match_obj.group(0)
		if expression.startswith("stepParams."):
			return str(step_params.get(expression.split(".", 1)[1], match_obj.group(0)))
		if expression.startswith("flowRefParams."):
			return str(flow_ref_params.get(expression.split(".", 1)[1], match_obj.group(0)))
		if expression.startswith("context."):
			return str(context.get(expression.split(".", 1)[1], match_obj.group(0)))
		if expression.startswith("steps."):
			parts = expression.split(".")
			if len(parts) >= 3:
				referenced_step_id = parts[1]
				output_key = ".".join(parts[2:])
				referenced_outputs = step_outputs.get(referenced_step_id, {})
				return str(referenced_outputs.get(output_key, match_obj.group(0)))
		# 裸键兼容：历史流程用 ${KEY} 直接引用 runtime_config，导出流程已用 ${runtime.KEY}
		# 前缀，故仅在此作向后兼容回退，不改变现有前缀语义。
		if expression in runtime_config:
			return str(runtime_config[expression])
		if expression in step_params:
			return str(step_params[expression])
		if expression in flow_ref_params:
			return str(flow_ref_params[expression])
		return match_obj.group(0)

	return re.sub(r"\$\{([^{}]+)\}", _replace, value)




def _get_window_text(hwnd):
	length = user32.GetWindowTextLengthW(hwnd)
	if length <= 0:
		return ""
	buffer = ctypes.create_unicode_buffer(length + 1)
	user32.GetWindowTextW(hwnd, buffer, len(buffer))
	return buffer.value


def _get_window_rect(hwnd):
	rect = wintypes.RECT()
	if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
		return None
	return rect


def _is_window_responsive(hwnd, timeout_ms=1000):
	result = ULONG_PTR()
	response = user32.SendMessageTimeoutW(
		hwnd,
		WM_NULL,
		0,
		0,
		SMTO_ABORTIFHUNG,
		timeout_ms,
		ctypes.byref(result),
	)
	return bool(response)


def activate_and_maximize_main_window(timeout_seconds=60):
	return _get_wt_window_helpers().activate_and_maximize_main_window(
		MAIN_WINDOW_TITLE_RE,
		timeout_seconds=timeout_seconds,
		class_name_keywords=("MUPSmartClient",),
	)


def ensure_main_window_foreground(timeout_seconds=15):
	"""运行前窗口健康检查 + 置顶（仅恢复+置顶，不改变窗口布局）。"""
	return _get_wt_window_helpers().ensure_main_window_foreground(
		MAIN_WINDOW_TITLE_RE,
		timeout_seconds=timeout_seconds,
		class_name_keywords=("MUPSmartClient",),
	)



def _select_tree_item_by_title_re(title_re, timeout_seconds=20):
	deadline = time.time() + timeout_seconds
	last_error = None
	while time.time() < deadline:
		try:
			hwnd = activate_and_maximize_main_window(timeout_seconds=5)
			window = Desktop(backend="uia").window(handle=hwnd)
			item = window.child_window(title_re=title_re, control_type="TreeItem")
			item.click_input()
			return item
		except Exception as exc:
			last_error = exc
			time.sleep(1)
	raise RuntimeError(f"无法定位图层树节点: {title_re}; last_error={last_error}")


def _right_click_tree_item_by_title_re(title_re):
	item = _select_tree_item_by_title_re(title_re)
	time.sleep(0.3)
	item.right_click_input()
	return item


def _right_click_flow_tree_item(step_id, control_id, fallback_title_re):
	if _click_flow_control(step_id, control_id, timeout_seconds=4, click_kind="right"):
		return True
	item = _right_click_tree_item_by_title_re(fallback_title_re)
	return item is not None


def _click_context_menu_with_fallback(step_id, control_id, fallback_uipath, timeout_seconds=4):
	if _click_flow_control(
		step_id,
		control_id,
		timeout_seconds=timeout_seconds,
		window_title_hint="__all__",
	):
		return True
	if _click_menu_candidate_by_text(step_id, control_id):
		return True
	try:
		menu_candidates = []
		for window in _iter_flow_search_windows(_get_flow_step(step_id), window_title_hint="__all__"):
			candidates = [window]
			try:
				candidates.extend(window.descendants())
			except Exception:
				pass
			for candidate in candidates:
				control_type = _get_wrapper_control_type(candidate)
				if control_type not in {"Menu", "MenuItem"}:
					continue
				name = _get_wrapper_text(candidate)
				help_text = _get_wrapper_help_text(candidate)
				class_name = _get_wrapper_class_name(candidate)
				signature = f"{control_type} | {name} | {help_text} | {class_name}".strip()
				if signature not in menu_candidates:
					menu_candidates.append(signature)
				if len(menu_candidates) >= 12:
					break
			if len(menu_candidates) >= 12:
				break
		if menu_candidates:
			log_step("当前菜单候选: " + " || ".join(menu_candidates))
		else:
			log_step("当前未枚举到任何 Menu/MenuItem 候选")
	except Exception as exc:
		log_step(f"枚举菜单候选失败: {exc}")
	log_step(f"流程链路匹配未命中菜单控件，回退 UIPath: step={step_id}, control={control_id}")
	click(fallback_uipath)
	time.sleep(0.5)
	return True


def _configure_projection():
	log_step("配置投影坐标系 - 完全录制流程")
	fallback_stage = "config_button"
	try:
		# 步骤1：点击文件菜单 -> 配置
		log_step("1. 点击文件菜单 -> 配置")
		with UIPath(MAIN_WINDOW_UIPATH):
			# 尝试多次点击，确保成功
			for i in range(3):
				try:
					click(u"||Pane->文件||Pane->配置||Button")
					time.sleep(0.5)
					fallback_stage = "general_tree_item"
					break
				except Exception as e:
					if i == 2:
						raise
					log_step(f"点击配置失败，重试 {i+1}")
					time.sleep(0.5)
		
		# 步骤2：在配置常规窗口选择常规，再选投影
		log_step("2. 在配置窗口选择常规")
		with UIPath(u"配置 - 常规||Window->||Tree"):
			for i in range(3):
				try:
					click(u"常规||TreeItem")
					time.sleep(0.3)
					fallback_stage = "projection_tree_item"
					break
				except Exception as e:
					if i == 2:
						raise
					log_step(f"点击常规失败，重试 {i+1}")
					time.sleep(0.5)
		
		log_step("3. 选择投影")
		for i in range(3):
			try:
				click(u"配置 - 常规||Window->||Tree->投影||TreeItem")
				time.sleep(0.3)
				fallback_stage = "load_from_file_button"
				break
			except Exception as e:
				if i == 2:
					raise
				log_step(f"点击投影失败，重试 {i+1}")
				time.sleep(0.5)
		
		# 步骤3：在配置投影窗口中操作
		log_step("4. 配置投影窗口操作")
		with UIPath(u"配置 - 投影||Window"):
			for i in range(3):
				try:
					click(u"||Tree->投影||TreeItem->投影||TreeItem")
					time.sleep(0.3)
					break
				except Exception as e:
					if i == 2:
						raise
					log_step(f"点击投影项失败，重试 {i+1}")
					time.sleep(0.5)
			
			for i in range(3):
				try:
					click(u"从文件加载...||Button")
					time.sleep(0.5)
					fallback_stage = "file_name_input"
					break
				except Exception as e:
					if i == 2:
						raise
					log_step(f"点击从文件加载失败，重试 {i+1}")
					time.sleep(0.5)
		
		# 步骤4：处理打开文件对话框
		log_step("5. 输入投影文件路径")
		try:
			with UIPath(u"配置 - 投影||Window->打开||Window"):
				click(u"文件名(N):||Text")
				time.sleep(0.3)
		except Exception:
			log_step("跳过文件名 Text 点击")
		
		with UIPath(u"配置 - 投影||Window->打开||Window->文件名(N):||ComboBox"):
			for i in range(3):
				try:
					click(u"文件名(N):||Edit")
					time.sleep(0.3)
					break
				except Exception as e:
					if i == 2:
						raise
					log_step(f"点击文件名输入框失败，重试 {i+1}")
					time.sleep(0.5)
			
			# 直接键入路径
			log_step(f"键入投影文件路径: {PROJECTION_FILE_PATH}")
			send_keys(PROJECTION_FILE_PATH)
			time.sleep(0.5)
			_confirm_open_file_dialog(timeout_seconds=3)
			fallback_stage = "apply_button"
		
		# 步骤5：回到配置窗口点击应用和确定
		log_step("6. 应用并确定")
		time.sleep(0.5)
		with UIPath(u"配置 - 投影||Window"):
			for i in range(3):
				try:
					click(u"应用||Button")
					time.sleep(0.3)
					fallback_stage = "ok_button"
					break
				except Exception as e:
					if i == 2:
						raise
					log_step(f"点击应用失败，重试 {i+1}")
					time.sleep(0.5)
			
			for i in range(3):
				try:
					click(u"确定||Button")
					time.sleep(0.3)
					break
				except Exception as e:
					if i == 2:
						raise
					log_step(f"点击确定失败，重试 {i+1}")
					time.sleep(0.5)
		
		log_step("投影配置完成")
		time.sleep(1)
	except Exception as e:
		error_msg = f"配置投影出错: {e}"
		log_step(error_msg)
		log_step(f"录制流程失败后切换图片识别，续跑阶段: {fallback_stage}")
		try:
			activate_and_maximize_main_window(timeout_seconds=10)
			_configure_projection_by_image(start_stage=fallback_stage)
			log_step("投影配置完成（图片识别）")
			time.sleep(1)
		except Exception as image_error:
			_log_projection_debug_context("image_projection_failed")
			_capture_debug_screenshot("projection_image_failed")
			log_step(f"图片识别加载投影失败: {image_error}")
			activate_and_maximize_main_window(timeout_seconds=10)
			ai_resume_stage = getattr(image_error, "resume_stage", fallback_stage)
			log_step(f"图片识别失败后切换 AI，续跑阶段: {ai_resume_stage}")
			log_step("图片识别失败，尝试用 AI 来配置投影")
			_run_ui_tars(
				_build_projection_ai_prompt(ai_resume_stage),
				step_name="AI配置投影"
			)


def _handle_dwg_projection_selection():
	log_step("处理 DWG 导入后的投影选择")
	ai_resume_stage = "load_from_file_button"
	try:
		if _click_flow_control("dwg_projection_confirm", "dwg_load_from_file", timeout_seconds=2.5):
			log_step("DWG 投影选择：流程链路匹配点击从文件加载成功")
		else:
			_click_template("load_from_file_button", timeout_seconds=8)
			log_step("DWG 投影选择：图片识别点击从文件加载成功")
		time.sleep(1)
		ai_resume_stage = "file_name_input"

		try:
			if _focus_flow_control("dwg_projection_confirm", "dwg_filename", timeout_seconds=1.5, window_title_hint="打开"):
				log_step("DWG 投影选择：流程链路匹配聚焦文件名输入框成功")
			else:
				_click_template("file_name_input", timeout_seconds=5)
				log_step("DWG 投影选择：图片识别点击文件名输入框成功")
		except Exception as exc:
			log_step(f"DWG 投影选择：文件名输入框未匹配，改用 Alt+N: {exc}")
			send_keys("%n")
			time.sleep(0.5)

		send_keys("^a")
		time.sleep(0.2)
		send_keys(PROJECTION_FILE_PATH)
		log_step(f"DWG 投影选择：已输入投影文件路径: {PROJECTION_FILE_PATH}")
		time.sleep(0.3)
		_confirm_open_file_dialog(timeout_seconds=3)
		log_step("DWG 投影选择：已点击打开(O)")
		time.sleep(1)
		ai_resume_stage = "confirm_values"

		_run_ui_tars(_build_dwg_projection_confirmation_prompt(), step_name="投影选择确认")
		activate_and_maximize_main_window(timeout_seconds=10)
		time.sleep(1)
	except Exception as exc:
		_capture_debug_screenshot("dwg_projection_selection_failed")
		log_step(f"DWG 投影选择失败: {exc}")
		activate_and_maximize_main_window(timeout_seconds=10)
		log_step(f"DWG 投影选择切换 AI，续跑阶段: {ai_resume_stage}")
		_run_ui_tars(_build_dwg_projection_ai_prompt(ai_resume_stage), step_name="投影选择")
		activate_and_maximize_main_window(timeout_seconds=10)
		time.sleep(1)


def _build_execution_context():
	return {
		"runtime_config": _load_runtime_config(),
		"source_basename": os.path.basename(SOURCE_FILE_PATH) if SOURCE_FILE_PATH else "",
		"step_params": {
			step_id: _get_step_params(step_id)
			for step_id in _load_flow_definition().keys()
		},
		"step_outputs": {},
		"flow_ref_stack": [],
		"flow_ref_param_stack": [],
		"run_report": None,
		"flowDefinitionPath": FLOW_DEFINITION_FILE,
		"runId": "",
	}


def _normalize_step_id(text):
	return str(text or "").strip()


def _build_execution_plan():
	payload = _load_flow_payload()
	registry_map = _get_step_registry_map()
	flow_steps = payload.get("steps", []) if isinstance(payload, dict) else []
	plan = []
	seen = set()
	if isinstance(payload, dict) and isinstance(flow_steps, list):
		for step in flow_steps:
			if not isinstance(step, dict):
				continue
			step_id = _normalize_step_id(step.get("id", ""))
			if not step_id or step_id in seen:
				continue
			seen.add(step_id)
			plan.append(
				{
					"id": step_id,
					"enabled": bool(step.get("enabled", True)),
					"topLevel": bool(step.get("topLevel", True)),
					"actionType": str(step.get("actionType", "script")).strip() or "script",
					"func": registry_map.get(step_id),
				}
			)
		return plan
	return [{"id": step_id, "enabled": True, "topLevel": True, "actionType": "script", "func": func} for step_id, func in _get_step_registry()]


def _resolve_steps_to_run(step_ids, steps_arg=None, from_step=None, to_step=None, default_step_ids=None):
	# 参数表展开后步骤 id 变为 `{原id}_scan{行}_{序号}`，外部（--steps/--from-step/
	# --to-step）传入的模板步骤 id（如 step_1）不再精确命中。构建 模板base id -> 展开id
	# 映射，未命中时按 base 展开，保证参数表模式下列表/区间参数仍能正确解析到执行计划。
	base_map = {}
	for sid in step_ids:
		sid_str = str(sid).strip()
		base = sid_str.rsplit("_scan", 1)[0] if "_scan" in sid_str else sid_str
		if base:
			base_map.setdefault(base, []).append(sid_str)

	def _resolve_one(raw):
		step_id = _normalize_step_id(raw)
		if not step_id:
			return None
		if step_id in step_ids:
			return [step_id]
		expanded = base_map.get(step_id)
		if expanded:
			return list(expanded)
		return None

	if steps_arg:
		raw_items = [item.strip() for item in str(steps_arg).split(",") if item.strip()]
		exact_ids = set()
		requested_bases = set()
		dropped = []
		for item in raw_items:
			step_id = _normalize_step_id(item)
			if step_id and step_id in step_ids:
				# 精确命中：记录精确 id，并把其 base 一并记入（若该 base 另有展开实例）
				exact_ids.add(step_id)
				requested_bases.add(step_id.rsplit("_scan", 1)[0] if "_scan" in step_id else step_id)
			elif step_id and step_id in base_map:
				# 模板 base id：映射为展开后全部实例
				requested_bases.add(step_id)
			else:
				dropped.append(item)
		# 按展开顺序收集：参数矩阵每行是一整套流程，必须保持 step_ids 的线性执行顺序，
		# 不能按模板 base 分组（否则会把所有行的 step_1 先跑完再跑 step_2，完全乱序）。
		normalized = []
		for sid in step_ids:
			if sid in exact_ids:
				normalized.append(sid)
				continue
			base = sid.rsplit("_scan", 1)[0] if "_scan" in sid else sid
			if base in requested_bases:
				normalized.append(sid)
		if dropped:
			# 防假成功：无效步骤名不再静默丢弃——明确告警，便于调用方及时发现
			log_step(
				"以下步骤名未匹配到可用步骤，已忽略: {}".format(", ".join(dropped))
			)
		return normalized
	if from_step:
		from_id = _normalize_step_id(from_step)
		if from_id in step_ids:
			start_index = step_ids.index(from_id)
		elif from_id in base_map and base_map[from_id]:
			start_index = step_ids.index(base_map[from_id][0])
		else:
			start_index = 0
		end_index = len(step_ids)
		if to_step:
			to_id = _normalize_step_id(to_step)
			if to_id in step_ids:
				end_index = step_ids.index(to_id) + 1
			elif to_id in base_map and base_map[to_id]:
				end_index = step_ids.index(base_map[to_id][-1]) + 1
		return step_ids[start_index:end_index]
	if default_step_ids is not None:
		return list(default_step_ids)
	return step_ids

def _collect_required_runtime_items(steps_to_run):
	selected_step_ids = {str(step_id).strip() for step_id in (steps_to_run or []) if str(step_id).strip()}
	requirements = set()
	if not selected_step_ids:
		return requirements

	if "launch_gm" in selected_step_ids:
		requirements.add("gmExe")
	if "configure_projection" in selected_step_ids:
		requirements.add("projectionFilePath")
	if "open_source_dwg" in selected_step_ids:
		requirements.add("sourceFilePath")
	if "export_geotiff" in selected_step_ids:
		requirements.add("outputDir")

	# These steps build layer names from the source file basename, so they also depend on sourceFilePath.
	source_dependent_steps = {"select_dgx_layer", "create_coverage", "select_coverage_layer"}
	if selected_step_ids.intersection(source_dependent_steps):
		requirements.add("sourceFilePath")
	return requirements


def _validate_runtime_items(steps_to_run):
	requirements = _collect_required_runtime_items(steps_to_run)
	if "gmExe" in requirements:
		if not GM_EXE:
			raise FileNotFoundError("目标软件可执行文件未配置")
		if not os.path.exists(GM_EXE):
			raise FileNotFoundError(f"目标软件可执行文件不存在: {GM_EXE}")
	if "sourceFilePath" in requirements:
		if not SOURCE_FILE_PATH:
			raise FileNotFoundError("源数据文件未配置")
		if not os.path.exists(SOURCE_FILE_PATH):
			raise FileNotFoundError(f"源数据文件不存在: {SOURCE_FILE_PATH}")
	if "projectionFilePath" in requirements:
		if not PROJECTION_FILE_PATH:
			raise FileNotFoundError("投影文件未配置")
		if not os.path.exists(PROJECTION_FILE_PATH):
			raise FileNotFoundError(f"Projection file not found: {PROJECTION_FILE_PATH}")
	if "outputDir" in requirements:
		if not OUTPUT_DIR:
			raise NotADirectoryError("输出目录未配置")
		if not os.path.isdir(OUTPUT_DIR):
			raise NotADirectoryError(f"Output dir not found: {OUTPUT_DIR}")

	# MUP 资产软诊断（不阻塞）：确认安装目录/粗糙度索引文件可读，
	# 换机后安装路径变化时由 GM_EXE 自动探测，缺失仅记录 warning。
	_log_mup_assets_status()

	# 运行前窗口健康检查 + 置顶：
	# 流程若包含 launch_gm（会自动启动/等待目标软件），则允许窗口未开，跳过检查；
	# 否则要求主窗口已存在并置顶，避免窗口被遮挡/最小化导致控件定位偏移。
	if "launch_gm" not in (steps_to_run or []):
		return _preflight_check_main_window()
	return None


def _log_mup_assets_status():
	"""运行前 MUP 资产状态日志（缺失不影响流程，仅提示增强不可用）。"""
	try:
		from mup_assets import configure_mup_install_dir, status
		configure_mup_install_dir(GM_EXE)
		st = status()
		if st.get("found"):
			log_step(
				"[mup-assets] 资产目录可用: {dir}（来源 {src}，{count} 个粗糙度索引文件）".format(
					dir=st["install_dir"], src=st["detect_source"], count=st["option_count"]
				)
			)
		else:
			log_step(
				"[mup-assets] 未找到 MUP 安装目录，粗糙度下拉框增强不可用（不影响主流程）"
			)
	except Exception:
		pass


def _attach_mup_data_diff(run_report, context):
	"""把 MUP 数据目录"运行前后"文件差异写入 run_report["mupDataFiles"]。

	惰性快照由 run_automation 在首个业务步骤执行前拍摄（launch_gm 完成后），
	此处对比当前快照，得到导入/计算/合成步骤是否真正落盘的文件级证据
	（软校验：数据目录不可用时跳过，不阻塞流程）。
	"""
	if not isinstance(run_report, dict) or not context:
		return
	before = context.get("mupDataSnapshotBefore")
	try:
		from mup_data_files import snapshot, diff
		after = snapshot()
		if not before:
			context["mupDataSnapshotBefore"] = after
			return
		result = diff(before, after)
		# 判定包含纯删除场景：只删除（deletedCount>0 且无新增/修改）同样记录
		if result["newCount"] or result["changedCount"] or result["deletedCount"]:
			run_report["mupDataFiles"] = result
			log_step(
				"[mup-data] 运行后检测到数据文件变化: 新增{new} 修改{changed} 删除{deleted}".format(
					new=result["newCount"], changed=result["changedCount"], deleted=result["deletedCount"]
				)
			)
	except Exception as exc:
		# 铁证缺失时不再静默吞掉：写日志并在报告中留错误字段，避免"证据缺席无任何痕迹"
		try:
			log_step(f"[mup-data] 运行后数据差异检测失败（跳过）: {exc}")
		except Exception:
			pass
		if isinstance(run_report, dict):
			run_report["mupDataDiffError"] = str(exc)


def _preflight_check_main_window():
	"""运行前窗口健康检查：主窗口存在则恢复+置顶，返回窗口信息；不存在则中止启动。"""
	try:
		window_info = ensure_main_window_foreground(timeout_seconds=15)
		log_step(
			"[mup-preflight] 运行前窗口健康检查通过: "
			"hwnd={hwnd} title={title} size={width}x{height}".format(**window_info)
		)
		return window_info
	except Exception as exc:
		raise RuntimeError(
			"运行前窗口健康检查失败：未找到目标软件主窗口（标题匹配: %s）。"
			"请先手动打开 %s 主窗口后重新运行。原因: %s"
			% (MAIN_WINDOW_TITLE_RE.pattern, GM_EXE or "Meteodyn Universe", exc)
		) from exc


def _pre_raise_main_window(timeout_seconds=15):
	"""运行前置顶：尽力把目标软件主窗口恢复并置顶，失败不中止流程。

	与 _preflight_check_main_window 的区别：这里只做"置顶/恢复"，不做"未找到即中止"。
	用于 skip_setup 场景或用户仅希望置顶不中断的流程包/步骤测试。
	"""
	try:
		window_info = ensure_main_window_foreground(timeout_seconds=timeout_seconds)
		log_step(
			"[mup-preflight] 运行前置顶完成: hwnd={hwnd} title={title} size={width}x{height}".format(
				**window_info
			)
		)
		return window_info
	except Exception as exc:
		log_step(f"[mup-preflight] 运行前置顶未生效（继续执行）: {exc}")
		return None


def run_automation(steps_arg=None, from_step=None, to_step=None, skip_setup=False, task_id=None, task_user=None, task_db=None, pre_raise=True):
	global running
	# 每次运行从干净状态开始：上一轮若因关闭窗口触发过停止，不得延续到本次
	# （同一进程内可能多次调用 run_automation，如任务队列 worker 串行执行）。
	_STOP_REQUESTED.clear()
	queue_db = task_db or wt_task_queue.DEFAULT_DB_PATH
	# 整个流程执行期间禁用 GC：pywinauto/comtypes 的 UIA 元素数组在垃圾回收时释放
	# COM 对象（__del__ → Release），若目标软件 UIA provider 已失效会触发
	# "Windows fatal exception: access violation" / "code 0xc0000374"（堆损坏），
	# 导致子进程崩溃且无任何错误详情。定位、点击、键入、下拉等所有 UIA 操作都可能
	# 触发，故在流程级统一禁用 GC，并【保持禁用直到进程退出】——finally 不再恢复，
	# 否则结束/异常 unwind 阶段收集 comtypes 对象会触发原生崩溃（模式 H 结束变体）。
	_gc_was_enabled = gc.isenabled()
	if _gc_was_enabled:
		gc.disable()
	try:
		_force_utf8_stdio()
		_refresh_flow_caches()
		_apply_runtime_config()
		running = True
		wt_run_status.publish(
			status="running",
			activity="WT自动化流程开始",
			last_log="WT自动化流程开始",
			source="WT_AUT_recorded",
		)
		log_step("WT自动化流程开始")

		execution_plan = _build_execution_plan()
		available_step_ids = [item["id"] for item in execution_plan]
		default_step_ids = [item["id"] for item in execution_plan if bool(item.get("topLevel", True))]
		steps_to_run = _resolve_steps_to_run(
			available_step_ids,
			steps_arg=steps_arg,
			from_step=from_step,
			to_step=to_step,
			default_step_ids=default_step_ids,
		)
		if not steps_to_run:
			# 防假成功：无任何可执行步骤时必须中止，禁止"0 步执行"却报 success 的空跑
			raise RuntimeError(
				"待执行步骤列表为空：流程文件 {} 无可执行 topLevel 步骤"
				"（或 --steps/--from-step/--to-step 参数未匹配到步骤）；"
				"若该路径不存在，说明流程文件未上传/未部署到本机".format(FLOW_DEFINITION_FILE)
			)
		log_step(f"执行步骤列表: {steps_to_run}")
		log_step(
			f"当前运行参数: gmExe={GM_EXE}, sourceFilePath={SOURCE_FILE_PATH}, outputDir={OUTPUT_DIR}, projectionFilePath={PROJECTION_FILE_PATH}"
		)

		preflight_window = None
		if not skip_setup:
			preflight_window = _validate_runtime_items(steps_to_run)
		if pre_raise and preflight_window is None:
			# 运行前置顶（可开关，默认开启）：无论是否 skip_setup，
			# 只要 _validate_runtime_items 未完成置顶（含 launch_gm 步骤、跳过检查等），
			# 都尽力把 WT 主窗口恢复并置顶，失败不中止流程。
			_pre_raise_main_window(timeout_seconds=15)

		context = _build_execution_context()
		if preflight_window:
			context["preflightWindow"] = preflight_window
			# MUP 数据目录快照不在流程开始前拍：MUP 应用启动（launch_gm）时初始化的
			# 数据文件会被误算入"步骤落盘铁证"（误报）。改为惰性快照——
			# 首个业务步骤执行前拍，此时 launch_gm 已完成，启动期文件被排除在证据外。
			context["mupDataSnapshotBefore"] = None
		context["run_report"] = _get_wt_run_reporting().start_run_report(
			steps_to_run,
			context.get("runtime_config", {}),
		)
		context["runId"] = context["run_report"].get("runId", "") if isinstance(context.get("run_report"), dict) else ""
		if task_id:
			wt_task_queue.mark_started(task_id, run_id=context.get("runId", ""), db_path=queue_db)
		wt_run_status.publish(
			status="running",
			activity="运行报告已初始化",
			last_log="运行报告已初始化",
			run_id=context.get("runId", ""),
			source="WT_AUT_recorded",
		)
		execution_plan_map = {item["id"]: item for item in execution_plan}
		completed_count = 0
		total_count = len(steps_to_run)
		# 任务栏/控制台进度初始化
		global _progress_current, _progress_total
		_progress_current = 0
		_progress_total = total_count
		_ui_safe_call(lambda: _update_taskbar_progress(0, total_count, status="running"))
		_ui_safe_call(lambda: _update_progress_title(0, total_count, step_name="流程开始", status="running"))
		try:
			tb = _init_taskbar_progress()
			if tb is not None and tb._obj and tb._set_value:
				log_step(f"[任务栏进度] 已启用: hwnd={tb._hwnd}")
			else:
				log_step("[任务栏进度] 不可用（无宿主窗口或 COM 初始化失败），仅显示标题进度")
		except Exception:
			pass
		for item in execution_plan:
			step_id = item["id"]
			if step_id not in steps_to_run:
				continue
			# 优雅停止：用户关闭监控窗口时，当前步骤完成后落盘报告再退出
			if _STOP_REQUESTED.is_set():
				log_step("收到关闭窗口请求，停止流程并落盘报告")
				_attach_mup_data_diff(context.get("run_report"), context)
				_get_wt_run_reporting().finalize_run_report(
					context.get("run_report"), "terminated", error="用户关闭窗口请求"
				)
				wt_run_status.publish(
					status="terminated",
					activity="用户关闭窗口，流程终止",
					last_log="用户关闭窗口，流程终止",
					run_id=context.get("runId", ""),
					source="WT_AUT_recorded",
				)
				if task_id:
					wt_task_queue.mark_terminated(
						task_id, error="用户关闭窗口请求", db_path=queue_db
					)
				return
			if task_id:
				task_snapshot = wt_task_queue.get_task(task_id, db_path=queue_db)
				if task_snapshot:
					if task_snapshot.get("terminateRequested"):
						_attach_mup_data_diff(context.get("run_report"), context)
						_get_wt_run_reporting().finalize_run_report(context.get("run_report"), "terminated", error="任务终止请求已收到")
						wt_task_queue.mark_terminated(task_id, error="任务终止请求已收到", db_path=queue_db)
						log_step("任务终止请求已收到，流程退出")
						return
					if task_snapshot.get("pauseRequested"):
						_attach_mup_data_diff(context.get("run_report"), context)
						_get_wt_run_reporting().finalize_run_report(context.get("run_report"), "paused")
						wt_task_queue.mark_paused(task_id, resume_from_step=step_id, db_path=queue_db)
						log_step("任务暂停请求已收到，流程将在当前步骤后暂停")
						return
				step_definition_for_progress = _get_flow_step(step_id)
				step_name = (
					str(step_definition_for_progress.get("name", "")).strip()
					or str(item.get("title", "")).strip()
					or str(item.get("name", "")).strip()
					or step_id
				)
				wt_task_queue.update_progress(
					task_id,
					current_step_id=step_id,
					current_step_name=step_name,
					progress_current=completed_count,
					progress_total=total_count,
					progress_percent=(completed_count / total_count * 100.0) if total_count else 0.0,
					resume_from_step=step_id,
					last_log="正在执行步骤: {}".format(step_name),
					db_path=queue_db,
				)
			# 失败联动短路：步骤配置 requiresPreviousSuccess 且上一步非 success 时跳过，
			# 避免"前置步骤失败后仍继续空操作却报成功"（如复制链在未选中综合1时
			# 继续点击行尾菜单/复制按钮）。只对显式配置该标志的步骤生效。
			_link_base_id = str(step_id).rsplit("_scan", 1)[0] if "_scan" in str(step_id) else str(step_id)
			_link_step_def = _get_flow_step(_link_base_id) or {}
			if completed_count > 0 and bool(_link_step_def.get("requiresPreviousSuccess")):
				_link_results = (context.get("run_report") or {}).get("stepResults", []) or []
				_link_prev_status = str((_link_results[-1] or {}).get("status", "")).strip() if _link_results else ""
				if _link_prev_status != "success":
					_link_name = str(_link_step_def.get("name", "")).strip() or step_id
					log_step(
						f"前置步骤失败联动跳过: step={step_id}, name={_link_name}, prev_status={_link_prev_status or '(none)'}"
					)
					try:
						_get_wt_run_reporting().report_step_result(
							context.get("run_report"),
							step_id,
							_link_name,
							"skipped",
							action_type=str(_link_step_def.get("actionType", "")).strip(),
							strategy=str(_link_step_def.get("strategy", "")).strip(),
							error="前置步骤失败联动跳过",
						)
					except Exception:
						pass
					completed_count += 1
					continue
			# 惰性快照：首个待执行步骤（launch_gm 之后）执行前拍"运行前基线"
			if context.get("mupDataSnapshotBefore") is None:
				try:
					from mup_data_files import snapshot
					context["mupDataSnapshotBefore"] = snapshot()
				except Exception:
					context["mupDataSnapshotBefore"] = {}
			_ACTIVE_FLOW_CTX.snapshot = {"step_id": step_id, "context": context}
			try:
				_execute_step_by_id(step_id, execution_plan_map, context, skip_setup=skip_setup)
			finally:
				_ACTIVE_FLOW_CTX.snapshot = None
			completed_count += 1
			if task_id:
				next_step = steps_to_run[completed_count] if completed_count < total_count else ""
				wt_task_queue.update_progress(
					task_id,
					progress_current=completed_count,
					progress_percent=(completed_count / total_count * 100.0) if total_count else 100.0,
					resume_from_step=next_step,
					db_path=queue_db,
				)

		log_step("WT自动化流程完成")
		_attach_mup_data_diff(context.get("run_report"), context)
		_get_wt_run_reporting().finalize_run_report(context.get("run_report"), "success")
		wt_run_status.publish(
			status="success",
			activity="WT自动化流程完成",
			last_log="WT自动化流程完成",
			run_id=context.get("runId", ""),
			source="WT_AUT_recorded",
		)
		if task_id:
			wt_task_queue.mark_success(task_id, run_id=context.get("runId", ""), last_log="WT自动化流程完成", db_path=queue_db)
		if monitor_window:
			_ui_safe_call(monitor_window.set_success)
		_ui_safe_call(lambda: _update_taskbar_progress(_progress_total, _progress_total, status="success"))
		_ui_safe_call(lambda: _update_progress_title(_progress_total, _progress_total, step_name="流程完成", status="success"))
	except Exception as e:
		error_msg = f"错误：{str(e)}"
		log_step(error_msg)
		try:
			_attach_mup_data_diff(
				context.get("run_report") if "context" in locals() else None,
				context if "context" in locals() else None,
			)
			_get_wt_run_reporting().finalize_run_report(
				context.get("run_report") if "context" in locals() else None,
				"failed",
				error=str(e),
			)
		except Exception:
			pass
		wt_run_status.publish(
			status="failed",
			activity="WT自动化流程失败",
			last_log=error_msg,
			error=error_msg,
			run_id=context.get("runId", "") if "context" in locals() else "",
			source="WT_AUT_recorded",
		)
		if task_id:
			wt_task_queue.mark_failed(task_id, error=str(e), run_id=context.get("runId", "") if "context" in locals() else "", db_path=queue_db)
		if monitor_window:
			_ui_safe_call(lambda: monitor_window.log(error_msg))
			_ui_safe_call(monitor_window.set_error)
		_ui_safe_call(lambda: _update_taskbar_progress(_progress_current, _progress_total, status="failed"))
		_ui_safe_call(lambda: _update_progress_title(_progress_current, _progress_total, step_name="流程失败", status="failed"))
		raise
	finally:
		running = False
		# 保持 GC 禁用，不再恢复：run_automation 一旦结束（含异常 unwind），
		# 局部变量里的 comtypes/UIA 对象（__del__→Release）若被 GC 收集会释放
		# 失效 COM 指针，触发原生崩溃（access violation，模式 H 的"结束阶段变体"）。
		# 崩溃栈特征：Garbage-collecting → comtypes unknwn.py Release → __del__，
		# 发生在 daemon 线程、报告已落盘之后，main() 的 os._exit 来不及执行。
		# 本进程是专用自动化子进程（含任务队列 worker），GC 由 main() 两分支在
		# os._exit 前保持禁用即可；恢复会导致结束/异常路径出现收集窗口。
		# （若未来确需在同一进程内多次调用 run_automation，应在调用方显式恢复。）
		if _gc_was_enabled:
			try:
				gc.disable()
			except Exception:
				pass


def automation_process():
	return run_automation()


def main():
	global monitor_window
	parser = argparse.ArgumentParser(add_help=True)
	parser.add_argument("--no-monitor", action="store_true")
	parser.add_argument("--steps", default="")
	parser.add_argument("--from-step", dest="from_step", default="")
	parser.add_argument("--to-step", dest="to_step", default="")
	parser.add_argument("--skip-setup", action="store_true")
	parser.add_argument("--task-id", dest="task_id", default="")
	parser.add_argument("--task-user", dest="task_user", default="")
	parser.add_argument("--task-db", dest="task_db", default="")
	parser.add_argument("--no-pre-raise", dest="pre_raise", action="store_false")
	parser.set_defaults(pre_raise=True)
	args = parser.parse_args()

	steps_arg = args.steps.strip() or None
	from_step = args.from_step.strip() or None
	to_step = args.to_step.strip() or None

	if args.no_monitor:
		try:
			run_automation(
				steps_arg=steps_arg,
				from_step=from_step,
				to_step=to_step,
				skip_setup=bool(args.skip_setup),
				task_id=args.task_id or None,
				task_user=args.task_user or None,
				task_db=args.task_db.strip() or None,
				pre_raise=bool(args.pre_raise),
			)
			exit_code = 0
		except BaseException:
			# 失败信息已由 run_automation 打印/落盘，这里只标记退出码
			exit_code = 1
		# 禁用 GC：run 结束后不再回收 UIA comtypes 对象（__del__→Release 失效指针原生崩溃），
		# 由下方 os._exit 硬退出直接跳过解释器清理。
		try:
			gc.disable()
		except Exception:
			pass
		# 硬退出，跳过 Python 解释器清理阶段（Py_Finalize）：
		# 流程中累积的 pywinauto/comtypes UIA 对象在进程退出时释放失效 COM 指针
		# 会触发原生崩溃（access violation / 0xc0000374），退出码非 0，
		# 被 WT_Launcher 误判为"流程执行失败"（此时运行报告实际已是 success）。
		print(f"[exit-code] automation exit_code={exit_code}")
		os._exit(exit_code)
		return

	monitor_window = MonitorWindow()
	# 任务栏进度 COM 对象（ITaskbarList3，ctypes 直连 vtable）必须在【主线程】创建：
	# monitor 模式下 run_automation 跑在后台 daemon 线程，若 COM 对象在那里创建
	# （CoCreateInstance 自动 CoInitialize(MTA)），而成功/失败收尾经 _ui_safe_call 又
	# 调度回主线程调用其 vtable，会形成"后台线程创建、主线程调用"的跨公寓 ctypes COM
	# 直连，触发原生崩溃（access violation，无 traceback），进程以非 0 退出码死亡、
	# 被 WT_Launcher 误判为"流程执行失败"（模式 H-2 残留根因）。
	# 这里提前在主线程创建一次，run_automation 内 _init_taskbar_progress() 发现已存在会跳过。
	_init_taskbar_progress()

	def _request_stop():
		# 拦截关闭按钮：先请求自动化优雅停止并落盘运行报告，而不是销毁窗口硬杀 daemon 线程
		_STOP_REQUESTED.set()
		monitor_window.log("关闭请求已收到：当前步骤完成后停止并落盘报告", kind="warning")

	run_finished = threading.Event()
	# 自动化线程的最终退出码：0=正常完成，1=流程异常（错误已由 run_automation 打印/落盘）
	_automation_exit_code = {"code": 0}

	def _run_automation_and_mark():
		try:
			run_automation(
				steps_arg=steps_arg,
				from_step=from_step,
				to_step=to_step,
				skip_setup=bool(args.skip_setup),
				task_id=args.task_id or None,
				task_user=args.task_user or None,
				task_db=args.task_db.strip() or None,
				pre_raise=bool(args.pre_raise),
			)
		except BaseException:
			_automation_exit_code["code"] = 1
		finally:
			# 禁用 GC：run 线程返回时若触发 GC，会回收 UIA comtypes 对象并 __del__→Release
			# 失效 COM 指针导致原生崩溃（main 的 os._exit 在 mainloop 退出后才执行，救不了这一下）。
			try:
				gc.disable()
			except Exception:
				pass
			run_finished.set()

	automation_thread = Thread(target=_run_automation_and_mark, daemon=True)

	def _poll_exit():
		# 关闭请求已发出且自动化线程收尾完毕后，销毁窗口结束 mainloop
		if _STOP_REQUESTED.is_set() and run_finished.is_set():
			try:
				monitor_window.root.destroy()
			except Exception:
				pass
			return
		monitor_window.root.after(200, _poll_exit)

	monitor_window.root.protocol("WM_DELETE_WINDOW", _request_stop)
	automation_thread.start()
	monitor_window.root.after(200, _poll_exit)
	monitor_window.root.mainloop()
	# mainloop 退出（正常完成或用户关闭）：等自动化线程彻底收尾，确保运行报告已落盘
	automation_thread.join(timeout=30)
	# 硬退出，跳过 Python 解释器清理阶段（Py_Finalize）：与 --no-monitor 分支同理，
	# 避免 comtypes UIA 对象在进程退出时释放失效 COM 指针导致原生崩溃、
	# 退出码非 0 被 WT_Launcher 误判为"流程执行失败"。
	print(f"[exit-code] automation exit_code={_automation_exit_code['code']}")
	os._exit(_automation_exit_code["code"])


if __name__ == "__main__":
	main()
