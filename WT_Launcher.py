# encoding: utf-8

import json
import importlib.util
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import zipfile
import ctypes
import contextlib
import io
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import wt_dpi
from flow_excel_io import (
    DEFAULT_FLOW_XLSX,
    audit_flow_excel_roundtrip,
    export_flow_to_excel,
    load_flow_payload_from_excel,
)
from flow_recorder_converter import convert_recorder_script_to_flow
from pywinauto import Desktop
from WT_Flow_Editor import sync_flow_package_registry
from wt_action_schema import ALLOWED_RELATIVE_REGION_ANCHORS
from wt_flow_validation import validate_flow_definition


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATION_SCRIPT = os.path.join(BASE_DIR, "WT_AUT_recorded.py")
TEMPLATE_BUILDER_SCRIPT = os.path.join(BASE_DIR, "build_image_template_library.py")
CONTROL_MAP_BUILDER_SCRIPT = os.path.join(BASE_DIR, "build_control_map_library.py")
FLOW_EDITOR_SCRIPT = os.path.join(BASE_DIR, "WT_Flow_Editor.py")
FLOW_DEFINITION_FILE = os.path.join(BASE_DIR, "workspace", "flow_definition.json")
FLOW_PACKAGE_STORE_DIR = os.path.join(BASE_DIR, "flow_packages")
FLOW_PACKAGE_REGISTRY_FILE = os.path.join(FLOW_PACKAGE_STORE_DIR, "flow_package_registry.json")
FLOW_EDITOR_STARTUP_SIGNAL = os.path.join(BASE_DIR, "flow_editor_startup.signal")
PROJECT_CONFIG_RESOURCE = os.path.join(BASE_DIR, "resources", "project_config.resource")
LOG_FILE = os.path.join(BASE_DIR, "wt_automation.log")
TEMPLATE_ROOT_DIR = os.path.join(BASE_DIR, "image_templates")
CONTROL_MAP_DIR = os.path.join(BASE_DIR, "control_maps")
TEMPLATE_INDEX_FILE = os.path.join(TEMPLATE_ROOT_DIR, "templates_index.json")
TEMPLATE_DIR = os.path.join(TEMPLATE_ROOT_DIR, "projection")
LOG_ARCHIVE_DIR = os.path.join(BASE_DIR, "debug_archives")
RUN_REPORT_DIR = os.path.join(BASE_DIR, "logs", "run_reports")
LAST_RUN_REPORT_FILE = os.path.join(BASE_DIR, "logs", "last_run_report.json")
LAUNCHER_STATE_FILE = os.path.join(BASE_DIR, "launcher_state.json")
DEFAULT_UI_TARS_REPO_ROOT = r"C:\Users\14830\UI-TARS-desktop"
DEFAULT_UI_TARS_CONFIG = os.path.join(os.path.expanduser("~"), ".ui-tars-cli.json")
MAX_RECENT_MODELS = 8
DEFAULT_RECORDER_DIR = r"D:\Pywinauto Recorder\pywinauto_recorder"
DEFAULT_RECORDER_SCRIPT = "pywinauto_recorder.py"
FLOW_DEFINITION_ENV_KEY = "WT_FLOW_DEFINITION_FILE"
RECORDER_LAUNCH_CMD_FILE = os.path.join(BASE_DIR, "_launch_pywinauto_recorder.cmd")
_PYAUTOGUI_MODULE = None
_PYAUTOGUI_IMPORT_ERROR = None


def load_project_settings():
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


def get_pyautogui():
    global _PYAUTOGUI_MODULE, _PYAUTOGUI_IMPORT_ERROR
    if _PYAUTOGUI_MODULE is not None:
        return _PYAUTOGUI_MODULE, None
    if _PYAUTOGUI_IMPORT_ERROR is not None:
        return None, _PYAUTOGUI_IMPORT_ERROR
    try:
        import pyautogui as imported_pyautogui

        _PYAUTOGUI_MODULE = imported_pyautogui
        return _PYAUTOGUI_MODULE, None
    except Exception as exc:
        _PYAUTOGUI_IMPORT_ERROR = exc
        return None, exc


def load_json_file(file_path):
    if not file_path or not os.path.exists(file_path):
        return None, f"未找到配置文件：{file_path}"
    try:
        with open(file_path, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj), None
    except Exception as exc:
        return None, f"读取配置失败：{exc}"


def save_json_file(file_path, payload):
    with open(file_path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)


def load_flow_runtime_config(flow_definition_path=None):
    payload, _error = load_json_file(flow_definition_path or FLOW_DEFINITION_FILE)
    payload = payload or {}
    runtime_config = payload.get("runtimeConfig", {}) if isinstance(payload, dict) else {}
    if not isinstance(runtime_config, dict):
        runtime_config = {}
    return {
        "gmExe": str(runtime_config.get("gmExe", "")).strip() or DEFAULT_GM_EXE,
        "sourceFilePath": str(runtime_config.get("sourceFilePath", "")).strip() or DEFAULT_SOURCE_FILE_PATH,
        "outputDir": str(runtime_config.get("outputDir", "")).strip() or DEFAULT_OUTPUT_DIR,
        "projectionFilePath": str(runtime_config.get("projectionFilePath", "")).strip() or DEFAULT_PROJECTION_FILE_PATH,
    }


def _normalize_flow_payload(payload):
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
    return {
        "runtimeConfig": runtime_config,
        "flowPackages": [item for item in flow_packages if isinstance(item, dict)],
        "steps": [item for item in steps if isinstance(item, dict)],
        "sourceDefinitionPath": str(payload.get("sourceDefinitionPath", "")).strip(),
    }


def _has_flow_content(payload):
    normalized = _normalize_flow_payload(payload)
    return bool(normalized["flowPackages"] or normalized["steps"])


def _build_flow_package_map(flow_packages):
    package_map = {}
    for package in flow_packages:
        if not isinstance(package, dict):
            continue
        package_id = str(package.get("id", "")).strip()
        if package_id:
            package_map[package_id] = package
    return package_map


def _build_step_map(steps):
    step_map = {}
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id", "")).strip()
        if step_id:
            step_map[step_id] = step
    return step_map


def _collect_package_refs(steps):
    package_ids = []
    seen_package_ids = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
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
        if not isinstance(package, dict):
            continue
        for item in package.get("stepIds", []):
            step_id = str(item).strip()
            if not step_id or step_id in seen_step_ids:
                continue
            step_ids.append(step_id)
            seen_step_ids.add(step_id)
    return step_ids


def _merge_target_with_registry(target_payload, registry_payload):
    normalized_target = _normalize_flow_payload(target_payload)
    normalized_registry = _normalize_flow_payload(registry_payload)
    if not _has_flow_content(normalized_target):
        return normalized_registry

    merged_runtime = dict(normalized_registry.get("runtimeConfig", {}))
    merged_runtime.update(normalized_target.get("runtimeConfig", {}))

    target_packages = normalized_target.get("flowPackages", [])
    registry_package_map = _build_flow_package_map(normalized_registry.get("flowPackages", []))
    merged_packages = list(target_packages)
    existing_package_ids = {str(package.get("id", "")).strip() for package in target_packages if str(package.get("id", "")).strip()}
    for package_id in _collect_package_refs(normalized_target.get("steps", [])):
        if package_id in existing_package_ids:
            continue
        registry_package = registry_package_map.get(package_id)
        if isinstance(registry_package, dict):
            merged_packages.append(registry_package)
            existing_package_ids.add(package_id)

    target_steps = normalized_target.get("steps", [])
    registry_step_map = _build_step_map(normalized_registry.get("steps", []))
    merged_steps = list(target_steps)
    existing_step_ids = {str(step.get("id", "")).strip() for step in target_steps if str(step.get("id", "")).strip()}
    for step_id in _collect_package_step_ids(merged_packages):
        if step_id in existing_step_ids:
            continue
        registry_step = registry_step_map.get(step_id)
        if isinstance(registry_step, dict):
            merged_steps.append(registry_step)
            existing_step_ids.add(step_id)

    return {
        "runtimeConfig": merged_runtime,
        "flowPackages": merged_packages,
        "steps": merged_steps,
        "sourceDefinitionPath": normalized_target.get("sourceDefinitionPath", "") or normalized_registry.get("sourceDefinitionPath", ""),
    }


def load_effective_flow_payload(flow_definition_path=None):
    target_path = flow_definition_path or FLOW_DEFINITION_FILE
    target_payload, _target_error = load_json_file(target_path)
    normalized_target = _normalize_flow_payload(target_payload)
    registry_payload, _registry_error = load_json_file(FLOW_PACKAGE_REGISTRY_FILE)
    normalized_registry = _normalize_flow_payload(registry_payload)
    if _has_flow_content(normalized_target):
        merged_payload = _merge_target_with_registry(normalized_target, normalized_registry)
        source_definition_path = normalized_registry.get("sourceDefinitionPath", "") if _has_flow_content(normalized_registry) else ""
        return merged_payload, target_path, source_definition_path
    if _has_flow_content(normalized_registry):
        source_definition_path = normalized_registry.get("sourceDefinitionPath", "")
        return normalized_registry, FLOW_PACKAGE_REGISTRY_FILE, source_definition_path

    return normalized_target, target_path, ""


def validate_effective_flow_payload(flow_definition_path=None):
    payload, effective_path, source_definition_path = load_effective_flow_payload(flow_definition_path)
    validation_errors = validate_flow_definition(payload)
    return payload, effective_path, source_definition_path, validation_errors


def validate_imported_flow_payload_or_raise(payload, source_label="导入流程"):
    normalized_payload = _normalize_flow_payload(payload)
    validation_errors = validate_flow_definition(normalized_payload)
    if validation_errors:
        raise ValueError(f"{source_label}校验未通过：\n- " + "\n- ".join(validation_errors[:12]))
    return normalized_payload


PROJECT_SETTINGS = load_project_settings()
DEFAULT_GM_EXE = PROJECT_SETTINGS.get("GM_EXE", "")
DEFAULT_SOURCE_FILE_PATH = PROJECT_SETTINGS.get("SOURCE_FILE_PATH", "")
DEFAULT_OUTPUT_DIR = PROJECT_SETTINGS.get("OUTPUT_DIR", "")
DEFAULT_PROJECTION_FILE_PATH = PROJECT_SETTINGS.get("PROJECTION_FILE_PATH", "")
DEFAULT_MODEL_NAME = PROJECT_SETTINGS.get("MODEL_NAME", "")
DEFAULT_BASE_URL = PROJECT_SETTINGS.get("UI_TARS_VLM_BASE_URL", "")


class RelativeRegionHelperDialog:
    def __init__(self, parent, theme):
        self.parent = parent
        self.theme = dict(theme or {})
        self.window_rect = None
        self.overlay_window = None
        self.overlay_canvas = None
        self.overlay_rect_id = None
        self.overlay_hint_id = None
        self.overlay_start = None
        self.capture_after_id = None
        self.preview_refresh_after_id = None
        self.var_capture_delay_seconds = tk.StringVar(value="3")
        self.status_var = tk.StringVar(value="建议先延时抓父窗口，再手动画框；鼠标中心建议用延时记录，避免点按钮后位置偏移。")
        self.preview_metrics_var = tk.StringVar(value="预览摘要：尚未抓取父窗口")
        self.var_parent_title = tk.StringVar()
        self.var_parent_class = tk.StringVar()
        self.var_parent_framework = tk.StringVar(value="WPF")
        self.var_window_rect = tk.StringVar(value="未捕获")
        self.var_center_x = tk.StringVar(value="0")
        self.var_center_y = tk.StringVar(value="0")
        self.var_region_x = tk.StringVar(value="0.45")
        self.var_region_y = tk.StringVar(value="0.45")
        self.var_region_width = tk.StringVar(value="0.32")
        self.var_region_height = tk.StringVar(value="0.08")
        self.var_anchor = tk.StringVar(value="center")
        self.var_action_name = tk.StringVar(value="type_text_relative")
        self.var_text = tk.StringVar(value="${runtime.sourceFilePath}")

        self.window = tk.Toplevel(parent)
        self.window.title("父窗口相对区域取点助手")
        wt_dpi.geometry(self.window, 980, 760)
        self.window.minsize(wt_dpi.scale(860), wt_dpi.scale(680))
        self.window.configure(bg=self.theme.get("bg", "#eef3f9"))
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self._bind_preview_traces()
        self._refresh_preview()

    def _build_ui(self):
        container = tk.Frame(self.window, bg=self.theme.get("bg", "#eef3f9"), padx=14, pady=14)
        container.pack(fill=tk.BOTH, expand=True)

        header = tk.LabelFrame(
            container,
            text="使用说明",
            padx=10,
            pady=10,
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("text", "#1f2d3d"),
        )
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text="1. 可直接抓前台父窗口，也可用延时抓取让总控台先最小化。2. 抓到父窗口后，优先用“手动画框选择区域”框出目标输入框或按钮。3. 鼠标中心建议用延时记录，避免点击按钮时鼠标已偏移。4. 右侧会显示父窗口和相对区域可视化预览。",
            justify=tk.LEFT,
            anchor="w",
            wraplength=900,
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("muted", "#5f6f82"),
        ).pack(fill=tk.X)

        body = tk.Frame(container, bg=self.theme.get("bg", "#eef3f9"))
        body.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

        left = tk.LabelFrame(
            body,
            text="参数配置",
            padx=10,
            pady=10,
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("text", "#1f2d3d"),
        )
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = tk.LabelFrame(
            body,
            text="预览结果",
            padx=10,
            pady=10,
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("text", "#1f2d3d"),
        )
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(12, 0))

        for column in (1, 3, 5):
            left.columnconfigure(column, weight=1)

        row = 0
        tk.Button(left, text="抓取当前前台窗口", command=self.capture_foreground_window).grid(row=row, column=0, sticky="ew", pady=4)
        tk.Button(left, text="延时抓取父窗口", command=self.capture_foreground_window_with_delay).grid(row=row, column=1, sticky="ew", padx=(8, 12), pady=4)
        tk.Button(left, text="手动画框选择区域", command=self.start_region_overlay_capture).grid(row=row, column=2, columnspan=2, sticky="ew", padx=(0, 12), pady=4)
        tk.Button(left, text="最小化总控台", command=self.minimize_launcher).grid(row=row, column=4, sticky="ew", pady=4)
        tk.Button(left, text="刷新预览", command=self._refresh_preview).grid(row=row, column=5, sticky="ew", padx=(8, 0), pady=4)
        row += 1
        self._grid_label_entry(left, "延时秒数", self.var_capture_delay_seconds, row, 0)
        self._grid_label_entry(left, "鼠标中心 X", self.var_center_x, row, 2)
        self._grid_label_entry(left, "鼠标中心 Y", self.var_center_y, row, 4)
        row += 1
        tk.Button(left, text="延时记录鼠标为区域中心", command=self.capture_mouse_center_with_delay).grid(row=row, column=0, columnspan=2, sticky="ew", pady=4)
        tk.Button(left, text="立即读取当前鼠标", command=self.capture_mouse_center).grid(row=row, column=2, columnspan=1, sticky="ew", padx=(8, 12), pady=4)
        tk.Label(
            left,
            text="建议优先使用“延时抓父窗口 + 手动画框选择区域”；若用鼠标中心，优先点延时记录，避免你点按钮时鼠标位置已经变了。",
            justify=tk.LEFT,
            anchor="w",
            wraplength=430,
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("muted", "#5f6f82"),
        ).grid(row=row, column=3, columnspan=3, sticky="ew", padx=(0, 0), pady=4)
        row += 1

        self._grid_label_entry(left, "父窗口标题 *", self.var_parent_title, row, 0)
        self._grid_label_entry(left, "父窗口类名", self.var_parent_class, row, 2)
        row += 1
        self._grid_label_entry(left, "框架类型", self.var_parent_framework, row, 0)
        self._grid_label_entry(left, "窗口矩形", self.var_window_rect, row, 2)
        row += 1
        self._grid_label_entry(left, "区域 X(0-1) *", self.var_region_x, row, 0)
        self._grid_label_entry(left, "区域 Y(0-1) *", self.var_region_y, row, 2)
        row += 1
        self._grid_label_entry(left, "区域宽度 *", self.var_region_width, row, 0)
        self._grid_label_entry(left, "区域高度 *", self.var_region_height, row, 2)
        row += 1
        self.action_name_label = tk.Label(
            left,
            text="动作类型",
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("text", "#1f2d3d"),
        )
        self.action_name_label.grid(row=row, column=0, sticky="w", pady=4)
        self.action_combo = ttk.Combobox(
            left,
            textvariable=self.var_action_name,
            values=("type_text_relative", "click_relative_region"),
            state="readonly",
        )
        self.action_combo.grid(row=row, column=1, sticky="ew", padx=(8, 12), pady=4)
        self.action_combo.bind("<<ComboboxSelected>>", self._on_action_name_change)
        self.anchor_label = tk.Label(
            left,
            text="点击锚点",
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("text", "#1f2d3d"),
        )
        self.anchor_label.grid(row=row, column=2, sticky="w", pady=4)
        self.anchor_combo = ttk.Combobox(
            left,
            textvariable=self.var_anchor,
            values=ALLOWED_RELATIVE_REGION_ANCHORS,
            state="readonly",
        )
        self.anchor_combo.grid(row=row, column=3, sticky="ew", padx=(8, 12), pady=4)
        self.default_text_label = tk.Label(
            left,
            text="默认输入文本",
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("text", "#1f2d3d"),
        )
        self.default_text_label.grid(row=row, column=4, sticky="w", pady=4)
        self.default_text_entry = tk.Entry(left, textvariable=self.var_text)
        self.default_text_entry.grid(row=row, column=5, sticky="ew", padx=(8, 12), pady=4)
        row += 1

        button_row = tk.Frame(left, bg=self.theme.get("card", "#ffffff"))
        button_row.grid(row=row, column=0, columnspan=6, sticky="ew", pady=(10, 0))
        tk.Button(button_row, text="复制 actionConfig", command=self.copy_action_config).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(button_row, text="复制完整步骤样例", command=self.copy_step_template).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))
        row += 1

        tk.Label(
            left,
            textvariable=self.status_var,
            justify=tk.LEFT,
            anchor="w",
            wraplength=640,
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("muted", "#5f6f82"),
        ).grid(row=row, column=0, columnspan=6, sticky="ew", pady=(12, 0))

        preview_hint = tk.Label(
            right,
            text="父窗口与相对区域预览",
            anchor="w",
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("text", "#1f2d3d"),
        )
        preview_hint.pack(fill=tk.X, pady=(0, 6))
        self.region_preview_canvas = tk.Canvas(
            right,
            height=260,
            bg="#f8fbff",
            highlightthickness=1,
            highlightbackground=self.theme.get("border", "#d7e0ee"),
        )
        self.region_preview_canvas.pack(fill=tk.X, pady=(0, 10))
        tk.Label(
            right,
            textvariable=self.preview_metrics_var,
            justify=tk.LEFT,
            anchor="w",
            wraplength=430,
            bg=self.theme.get("card", "#ffffff"),
            fg=self.theme.get("muted", "#5f6f82"),
        ).pack(fill=tk.X, pady=(0, 10))
        self.preview_text = tk.Text(
            right,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="#fbfdff",
            fg=self.theme.get("text", "#1f2d3d"),
            relief=tk.FLAT,
            bd=1,
            insertbackground=self.theme.get("text", "#1f2d3d"),
        )
        self.preview_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        preview_scrollbar = tk.Scrollbar(right, command=self.preview_text.yview, relief=tk.FLAT)
        preview_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_text.config(yscrollcommand=preview_scrollbar.set)

    def _grid_label_entry(self, parent, label, variable, row, column):
        tk.Label(parent, text=label, bg=self.theme.get("card", "#ffffff"), fg=self.theme.get("text", "#1f2d3d")).grid(
            row=row, column=column, sticky="w", pady=4
        )
        tk.Entry(parent, textvariable=variable).grid(row=row, column=column + 1, sticky="ew", padx=(8, 12), pady=4)

    def _bind_preview_traces(self):
        for variable in (
            self.var_parent_title,
            self.var_parent_class,
            self.var_parent_framework,
            self.var_window_rect,
            self.var_center_x,
            self.var_center_y,
            self.var_region_x,
            self.var_region_y,
            self.var_region_width,
            self.var_region_height,
            self.var_anchor,
            self.var_action_name,
            self.var_text,
        ):
            variable.trace_add("write", self._schedule_preview_refresh)

    def _schedule_preview_refresh(self, *_args):
        if self.preview_refresh_after_id is not None:
            return
        try:
            self.preview_refresh_after_id = self.window.after_idle(self._run_scheduled_preview_refresh)
        except Exception:
            self.preview_refresh_after_id = None

    def _run_scheduled_preview_refresh(self):
        self.preview_refresh_after_id = None
        self._refresh_preview()

    def _on_close(self):
        self._cancel_overlay_capture()
        if self.capture_after_id:
            try:
                self.window.after_cancel(self.capture_after_id)
            except Exception:
                pass
            self.capture_after_id = None
        if self.preview_refresh_after_id:
            try:
                self.window.after_cancel(self.preview_refresh_after_id)
            except Exception:
                pass
            self.preview_refresh_after_id = None
        try:
            self.window.destroy()
        except Exception:
            pass

    def minimize_launcher(self):
        try:
            self.window.iconify()
        except Exception:
            pass
        try:
            self.parent.iconify()
        except Exception:
            pass

    def _get_foreground_handle(self):
        try:
            return int(ctypes.windll.user32.GetForegroundWindow() or 0)
        except Exception:
            return 0

    def _get_foreground_window_wrapper(self):
        handle = self._get_foreground_handle()
        if not handle:
            return None
        try:
            return Desktop(backend="uia").window(handle=handle)
        except Exception:
            return None

    def _set_window_capture_from_wrapper(self, wrapper, status_text):
        rect = wrapper.rectangle()
        self.window_rect = {
            "left": int(rect.left),
            "top": int(rect.top),
            "right": int(rect.right),
            "bottom": int(rect.bottom),
            "width": max(0, int(rect.right) - int(rect.left)),
            "height": max(0, int(rect.bottom) - int(rect.top)),
        }
        self.var_parent_title.set(str(wrapper.window_text() or "").strip())
        self.var_parent_class.set(str(wrapper.class_name() or "").strip())
        self.var_parent_framework.set(str(getattr(wrapper.element_info, "framework_id", "") or "").strip() or "WPF")
        self.var_window_rect.set(
            "{left},{top},{right},{bottom}".format(
                left=self.window_rect["left"],
                top=self.window_rect["top"],
                right=self.window_rect["right"],
                bottom=self.window_rect["bottom"],
            )
        )
        self.status_var.set(status_text)
        self._refresh_preview()

    def capture_foreground_window(self):
        wrapper = self._get_foreground_window_wrapper()
        if wrapper is None:
            self.status_var.set("未能读取当前前台窗口，请先把目标窗口切到最前面。")
            return
        try:
            self._set_window_capture_from_wrapper(wrapper, "已抓取当前前台窗口，可继续画框选择相对区域。")
        except Exception as exc:
            self.status_var.set(f"抓取前台窗口失败：{exc}")

    def capture_foreground_window_with_delay(self):
        delay_seconds = max(1.0, self._parse_float(self.var_capture_delay_seconds.get(), 3.0))
        if self.capture_after_id:
            try:
                self.window.after_cancel(self.capture_after_id)
            except Exception:
                pass
            self.capture_after_id = None
        self.status_var.set(f"将在 {delay_seconds:.1f} 秒后抓取当前前台父窗口，助手和总控台将暂时隐藏。")
        try:
            self.window.withdraw()
        except Exception:
            pass
        try:
            self.parent.iconify()
        except Exception:
            pass
        self.capture_after_id = self.parent.after(int(delay_seconds * 1000), self._finish_delayed_foreground_capture)

    def _finish_delayed_foreground_capture(self):
        self.capture_after_id = None
        wrapper = self._get_foreground_window_wrapper()
        try:
            self.parent.deiconify()
        except Exception:
            pass
        try:
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
        except Exception:
            pass
        if wrapper is None:
            self.status_var.set("延时抓取失败：未读取到当前前台窗口，请重试。")
            return
        try:
            self._set_window_capture_from_wrapper(wrapper, "已按延时方式抓取父窗口，可继续手动画框选择区域。")
        except Exception as exc:
            self.status_var.set(f"延时抓取前台窗口失败：{exc}")

    def capture_mouse_center_with_delay(self):
        if not self.window_rect or not self.window_rect.get("width") or not self.window_rect.get("height"):
            self.status_var.set("请先抓取父窗口，再延时记录鼠标中心点。")
            messagebox.showinfo("请先抓父窗口", "手动画框和鼠标中心记录都依赖父窗口矩形。\n请先点“抓取当前前台窗口”或“延时抓取父窗口”。")
            return
        delay_seconds = max(1.0, self._parse_float(self.var_capture_delay_seconds.get(), 3.0))
        if self.capture_after_id:
            try:
                self.window.after_cancel(self.capture_after_id)
            except Exception:
                pass
            self.capture_after_id = None
        self.status_var.set(f"将在 {delay_seconds:.1f} 秒后记录当前鼠标中心，助手和总控台将暂时隐藏。")
        try:
            self.window.withdraw()
        except Exception:
            pass
        try:
            self.parent.iconify()
        except Exception:
            pass
        self.capture_after_id = self.parent.after(int(delay_seconds * 1000), self._finish_delayed_mouse_capture)

    def _finish_delayed_mouse_capture(self):
        self.capture_after_id = None
        self._capture_mouse_center_impl(restore_window=True, status_text="已按延时方式记录鼠标中心，并同步更新相对区域。")

    def capture_mouse_center(self):
        self._capture_mouse_center_impl(restore_window=False, status_text="已按当前鼠标位置计算相对区域左上角，可微调后复制。")

    def _capture_mouse_center_impl(self, restore_window, status_text):
        if not self.window_rect or not self.window_rect.get("width") or not self.window_rect.get("height"):
            self.status_var.set("请先抓取父窗口，再记录鼠标中心点。")
            return
        pyautogui_module, import_error = get_pyautogui()
        if restore_window:
            try:
                self.parent.deiconify()
            except Exception:
                pass
            try:
                self.window.deiconify()
                self.window.lift()
                self.window.focus_force()
            except Exception:
                pass
        if pyautogui_module is None:
            self.status_var.set(f"鼠标定位依赖加载失败：{import_error}")
            messagebox.showerror("依赖加载失败", f"无法加载 pyautogui，暂时不能记录鼠标中心点：\n{import_error}")
            return
        x, y = pyautogui_module.position()
        self.var_center_x.set(str(int(x)))
        self.var_center_y.set(str(int(y)))
        rel_x = (float(x) - float(self.window_rect["left"])) / float(self.window_rect["width"])
        rel_y = (float(y) - float(self.window_rect["top"])) / float(self.window_rect["height"])
        rel_width = self._parse_float(self.var_region_width.get(), 0.32)
        rel_height = self._parse_float(self.var_region_height.get(), 0.08)
        self.var_region_x.set(f"{max(0.0, min(1.0, rel_x - rel_width / 2.0)):.4f}")
        self.var_region_y.set(f"{max(0.0, min(1.0, rel_y - rel_height / 2.0)):.4f}")
        self.status_var.set(status_text)
        self._refresh_preview()

    def start_region_overlay_capture(self):
        if not self.window_rect or not self.window_rect.get("width") or not self.window_rect.get("height"):
            self.status_var.set("手动画框前必须先抓到父窗口，因为需要用父窗口矩形换算相对比例。")
            messagebox.showinfo(
                "请先抓父窗口",
                "手动画框选择区域前，需要先拿到父窗口矩形。\n\n建议顺序：\n1. 点“延时抓取父窗口”\n2. 切到目标弹窗\n3. 抓到后再点“手动画框选择区域”",
            )
            return
        if self.overlay_window is not None:
            self.status_var.set("画框选择窗口已打开，请直接在屏幕上拖动选择。")
            return
        self.overlay_start = None
        self.overlay_rect_id = None
        try:
            self.window.withdraw()
        except Exception:
            pass
        overlay = tk.Toplevel(self.parent)
        self.overlay_window = overlay
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        try:
            overlay.attributes("-alpha", 0.22)
        except Exception:
            pass
        screen_width = overlay.winfo_screenwidth()
        screen_height = overlay.winfo_screenheight()
        # 全屏遮罩用真实屏幕像素，绕过 DPI 自动缩放，否则会超出屏幕、框选坐标错位
        wt_dpi.raw_geometry(overlay, f"{screen_width}x{screen_height}+0+0")
        overlay.configure(bg="#0f172a")
        canvas = tk.Canvas(overlay, bg="#0f172a", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill=tk.BOTH, expand=True)
        self.overlay_canvas = canvas
        parent_rect = self.window_rect or {}
        canvas.create_rectangle(
            parent_rect.get("left", 0),
            parent_rect.get("top", 0),
            parent_rect.get("right", 0),
            parent_rect.get("bottom", 0),
            outline="#60a5fa",
            width=3,
        )
        self.overlay_hint_id = canvas.create_text(
            max(220, parent_rect.get("left", 0) + 180),
            max(40, parent_rect.get("top", 0) - 20),
            text="请在父窗口内拖动画框选择区域，Esc 取消，松开鼠标后自动回到助手。",
            fill="#ffffff",
            font=("TkDefaultFont", 11, "bold"),
        )
        canvas.bind("<ButtonPress-1>", self._on_overlay_mouse_down)
        canvas.bind("<B1-Motion>", self._on_overlay_mouse_drag)
        canvas.bind("<ButtonRelease-1>", self._on_overlay_mouse_up)
        overlay.bind("<Escape>", lambda _event=None: self._cancel_overlay_capture(restore_status=True))
        overlay.focus_force()
        self.status_var.set("请在父窗口内拖动画框选择相对区域，按 Esc 可取消。")

    def _cancel_overlay_capture(self, restore_status=False):
        overlay = self.overlay_window
        self.overlay_window = None
        self.overlay_canvas = None
        self.overlay_rect_id = None
        self.overlay_hint_id = None
        self.overlay_start = None
        if overlay is not None:
            try:
                overlay.destroy()
            except Exception:
                pass
        try:
            if self.window.winfo_exists():
                self.window.deiconify()
                self.window.lift()
        except Exception:
            pass
        if restore_status:
            self.status_var.set("已取消画框选择区域。")

    def _clamp_overlay_point(self, x_value, y_value):
        rect = self.window_rect or {}
        left = int(rect.get("left", 0))
        top = int(rect.get("top", 0))
        right = int(rect.get("right", left))
        bottom = int(rect.get("bottom", top))
        clamped_x = min(max(int(x_value), left), right)
        clamped_y = min(max(int(y_value), top), bottom)
        return clamped_x, clamped_y

    def _on_overlay_mouse_down(self, event):
        if self.overlay_canvas is None:
            return
        start_x, start_y = self._clamp_overlay_point(event.x_root, event.y_root)
        self.overlay_start = (start_x, start_y)
        if self.overlay_rect_id is not None:
            self.overlay_canvas.delete(self.overlay_rect_id)
        self.overlay_rect_id = self.overlay_canvas.create_rectangle(
            start_x,
            start_y,
            start_x,
            start_y,
            outline="#facc15",
            width=3,
            dash=(6, 4),
        )

    def _on_overlay_mouse_drag(self, event):
        if self.overlay_canvas is None or self.overlay_rect_id is None or self.overlay_start is None:
            return
        current_x, current_y = self._clamp_overlay_point(event.x_root, event.y_root)
        self.overlay_canvas.coords(self.overlay_rect_id, self.overlay_start[0], self.overlay_start[1], current_x, current_y)

    def _on_overlay_mouse_up(self, event):
        if self.overlay_start is None:
            return
        end_x, end_y = self._clamp_overlay_point(event.x_root, event.y_root)
        start_x, start_y = self.overlay_start
        left = min(start_x, end_x)
        top = min(start_y, end_y)
        right = max(start_x, end_x)
        bottom = max(start_y, end_y)
        self._cancel_overlay_capture()
        if right - left < 4 or bottom - top < 4:
            self.status_var.set("画框区域过小，请重新框选。")
            return
        self._apply_absolute_region(left, top, right, bottom, "已按画框结果生成相对区域，可继续微调后复制。")

    def _apply_absolute_region(self, left, top, right, bottom, status_text):
        if not self.window_rect or not self.window_rect.get("width") or not self.window_rect.get("height"):
            self.status_var.set("请先抓取父窗口，再应用画框区域。")
            return
        parent_left = float(self.window_rect["left"])
        parent_top = float(self.window_rect["top"])
        parent_width = float(self.window_rect["width"])
        parent_height = float(self.window_rect["height"])
        rel_x = max(0.0, min(1.0, (float(left) - parent_left) / parent_width))
        rel_y = max(0.0, min(1.0, (float(top) - parent_top) / parent_height))
        rel_width = max(0.0, min(1.0, (float(right) - float(left)) / parent_width))
        rel_height = max(0.0, min(1.0, (float(bottom) - float(top)) / parent_height))
        center_x = int(round((float(left) + float(right)) / 2.0))
        center_y = int(round((float(top) + float(bottom)) / 2.0))
        self.var_center_x.set(str(center_x))
        self.var_center_y.set(str(center_y))
        self.var_region_x.set(f"{rel_x:.4f}")
        self.var_region_y.set(f"{rel_y:.4f}")
        self.var_region_width.set(f"{rel_width:.4f}")
        self.var_region_height.set(f"{rel_height:.4f}")
        self.status_var.set(status_text)
        self._refresh_preview()

    def _parse_float(self, value, default_value):
        try:
            return float(value)
        except Exception:
            return float(default_value)

    def _set_widget_visible(self, widget, visible):
        if widget is None:
            return
        if visible:
            widget.grid()
        else:
            widget.grid_remove()

    def _is_input_relative_action(self):
        return (self.var_action_name.get().strip() or "type_text_relative") == "type_text_relative"

    def _refresh_action_specific_fields(self):
        show_text_input = self._is_input_relative_action()
        self._set_widget_visible(getattr(self, "default_text_label", None), show_text_input)
        self._set_widget_visible(getattr(self, "default_text_entry", None), show_text_input)

    def _on_action_name_change(self, _event=None):
        if self._is_input_relative_action() and not self.var_text.get().strip():
            self.var_text.set("${runtime.sourceFilePath}")
        self._refresh_action_specific_fields()
        self._refresh_preview()

    def build_action_config(self):
        action_name = self.var_action_name.get().strip() or "type_text_relative"
        payload = {
            "action": action_name,
            "timeoutSeconds": 3.0 if action_name == "type_text_relative" else 2.5,
            "waitBefore": 0.0,
            "waitAfter": 0.15 if action_name == "type_text_relative" else 0.12,
            "parentWindow": {
                "title": self.var_parent_title.get().strip(),
                "className": self.var_parent_class.get().strip(),
                "frameworkId": self.var_parent_framework.get().strip(),
            },
            "relativeRegion": {
                "x": round(self._parse_float(self.var_region_x.get(), 0.45), 4),
                "y": round(self._parse_float(self.var_region_y.get(), 0.45), 4),
                "width": round(self._parse_float(self.var_region_width.get(), 0.32), 4),
                "height": round(self._parse_float(self.var_region_height.get(), 0.08), 4),
                "anchor": self.var_anchor.get().strip() or "center",
            },
        }
        if action_name == "type_text_relative":
            payload["text"] = self.var_text.get().strip() or "${runtime.sourceFilePath}"
        return payload

    def build_step_template(self):
        parent_title = self.var_parent_title.get().strip() or "请补充父窗口标题"
        action_name = self.var_action_name.get().strip() or "type_text_relative"
        return {
            "id": f"{action_name}_step",
            "name": "父窗口区域输入" if action_name == "type_text_relative" else "父窗口区域点击",
            "stage": "custom",
            "strategy": "action",
            "actionType": "action",
            "windowTitle": parent_title,
            "description": "通过父窗口相对区域点击输入文本。" if action_name == "type_text_relative" else "通过父窗口相对区域点击目标位置。",
            "actionConfig": self.build_action_config(),
        }

    def _refresh_preview(self):
        self._refresh_action_specific_fields()
        self._refresh_region_preview()
        preview = {
            "actionConfig": self.build_action_config(),
            "stepExample": self.build_step_template(),
        }
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", json.dumps(preview, ensure_ascii=False, indent=2))

    def _refresh_region_preview(self):
        canvas = getattr(self, "region_preview_canvas", None)
        if canvas is None:
            return
        canvas.delete("all")
        canvas_width = max(int(canvas.winfo_width() or 0), 520)
        canvas_height = max(int(canvas.winfo_height() or 0), 260)
        canvas.create_rectangle(10, 10, canvas_width - 10, canvas_height - 10, outline=self.theme.get("border", "#d7e0ee"), width=1)
        if not self.window_rect or not self.window_rect.get("width") or not self.window_rect.get("height"):
            self.preview_metrics_var.set("预览摘要：尚未抓取父窗口，暂无像素尺寸与中心点信息")
            canvas.create_text(
                canvas_width / 2,
                canvas_height / 2,
                text="尚未抓取父窗口\n请先抓取父窗口后再画框或记录鼠标中心",
                fill=self.theme.get("muted", "#5f6f82"),
                font=("TkDefaultFont", 11),
                justify=tk.CENTER,
            )
            return
        padding = 28
        preview_left = padding
        preview_top = padding
        preview_width = canvas_width - padding * 2
        preview_height = canvas_height - padding * 2
        parent_width_px = max(int(self.window_rect.get("width", 0) or 0), 1)
        parent_height_px = max(int(self.window_rect.get("height", 0) or 0), 1)
        canvas.create_rectangle(
            preview_left,
            preview_top,
            preview_left + preview_width,
            preview_top + preview_height,
            fill="#eef6ff",
            outline="#60a5fa",
            width=2,
        )
        region_x = max(0.0, min(1.0, self._parse_float(self.var_region_x.get(), 0.45)))
        region_y = max(0.0, min(1.0, self._parse_float(self.var_region_y.get(), 0.45)))
        region_width = max(0.0, min(1.0, self._parse_float(self.var_region_width.get(), 0.32)))
        region_height = max(0.0, min(1.0, self._parse_float(self.var_region_height.get(), 0.08)))
        region_width_px = max(1, int(round(parent_width_px * region_width)))
        region_height_px = max(1, int(round(parent_height_px * region_height)))
        region_left_px = int(round(parent_width_px * region_x))
        region_top_px = int(round(parent_height_px * region_y))
        region_center_x_px = int(round(region_left_px + region_width_px / 2.0))
        region_center_y_px = int(round(region_top_px + region_height_px / 2.0))
        region_left = preview_left + preview_width * region_x
        region_top = preview_top + preview_height * region_y
        region_right = min(preview_left + preview_width, region_left + preview_width * region_width)
        region_bottom = min(preview_top + preview_height, region_top + preview_height * region_height)
        canvas.create_rectangle(
            region_left,
            region_top,
            region_right,
            region_bottom,
            fill="#93c5fd",
            outline="#1d4ed8",
            width=2,
        )
        center_x = (region_left + region_right) / 2.0
        center_y = (region_top + region_bottom) / 2.0
        canvas.create_line(center_x - 10, center_y, center_x + 10, center_y, fill="#1e3a8a", width=2)
        canvas.create_line(center_x, center_y - 10, center_x, center_y + 10, fill="#1e3a8a", width=2)
        anchor_text = self.var_anchor.get().strip() or "center"
        canvas.create_text(
            preview_left + 6,
            preview_top - 10,
            text="父窗口",
            anchor="w",
            fill=self.theme.get("text", "#1f2d3d"),
            font=("TkDefaultFont", 10, "bold"),
        )
        canvas.create_text(
            region_left + 6,
            max(preview_top + 14, region_top - 10),
            text=f"区域 {region_width:.3f} x {region_height:.3f} / {region_width_px}px x {region_height_px}px / anchor={anchor_text}",
            anchor="w",
            fill="#1e3a8a",
            font=("TkDefaultFont", 9, "bold"),
        )
        canvas.create_text(
            preview_left,
            preview_top + preview_height + 12,
            text=f"窗口矩形: {self.var_window_rect.get() or '未捕获'}",
            anchor="w",
            fill=self.theme.get("muted", "#5f6f82"),
            font=("TkDefaultFont", 9),
        )
        canvas.create_text(
            preview_left,
            preview_top + preview_height + 28,
            text=f"中心点: ({region_center_x_px}, {region_center_y_px})   左上角: ({region_left_px}, {region_top_px})   区域占比: x={region_x:.4f}, y={region_y:.4f}",
            anchor="w",
            fill=self.theme.get("muted", "#5f6f82"),
            font=("TkDefaultFont", 9),
        )
        self.preview_metrics_var.set(
            "预览摘要："
            f"父窗口 {parent_width_px} x {parent_height_px}px；"
            f"区域 {region_width_px} x {region_height_px}px；"
            f"中心点 ({region_center_x_px}, {region_center_y_px})；"
            f"左上角比例 ({region_x:.4f}, {region_y:.4f})"
        )

    def _copy_payload(self, payload, label):
        self.parent.clipboard_clear()
        self.parent.clipboard_append(json.dumps(payload, ensure_ascii=False, indent=2))
        self.parent.update_idletasks()
        self.status_var.set(f"已复制 {label} 到剪贴板。")

    def copy_action_config(self):
        self._refresh_preview()
        self._copy_payload(self.build_action_config(), "actionConfig")

    def copy_step_template(self):
        self._refresh_preview()
        self._copy_payload(self.build_step_template(), "完整步骤样例")


class LauncherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("WT 自动化项目总控台")
        wt_dpi.geometry(self.root, 1320, 860)
        self.root.minsize(wt_dpi.scale(1140), wt_dpi.scale(760))
        self.theme = {
            "bg": "#eef3f9",
            "card": "#ffffff",
            "toolbar": "#eaf1fb",
            "border": "#d7e0ee",
            "text": "#1f2d3d",
            "muted": "#5f6f82",
            "primary": "#2563eb",
            "primary_soft": "#dbeafe",
            "primary_active": "#1d4ed8",
            "danger": "#dc2626",
            "danger_active": "#b91c1c",
            "secondary": "#f8fbff",
            "secondary_active": "#edf4ff",
        }
        self.root.configure(bg=self.theme["bg"])

        self.process = None
        self.output_queue = queue.Queue()
        self.status_var = tk.StringVar(value="状态：准备就绪")
        self.current_step_var = tk.StringVar(value="当前步骤：等待启动")
        self.process_var = tk.StringVar(value="流程进程：未运行")
        self.config_summary_var = tk.StringVar(value="配置概览：尚未检查")
        self.template_summary_var = tk.StringVar(value="模板库概览：尚未检查")
        self.template_category_var = tk.StringVar(value="")
        self.run_report_summary_var = tk.StringVar(value="运行报告：尚未生成")
        self.run_report_meta_var = tk.StringVar(value="最近一次流程执行后，会在这里展示结构化运行结果。")
        self.skip_setup_var = tk.BooleanVar(value=False)
        self.show_monitor_var = tk.BooleanVar(value=True)
        self.flow_steps = []
        self.flow_step_display_map = {}
        self.step_check_vars = {}
        self.flow_packages = []
        self.selected_flow_package_var = tk.StringVar(value="")
        self.step_scroll_hint_var = tk.StringVar(value="步骤列表位置：顶部")
        self.flow_definition_path_var = tk.StringVar(value=FLOW_DEFINITION_FILE)
        self.template_categories = []

        launcher_state, _state_error = load_json_file(LAUNCHER_STATE_FILE)
        launcher_state = launcher_state or {}
        self.simple_mode_flows = {}
        simple_flows = launcher_state.get("simpleModeFlows", {})
        if isinstance(simple_flows, dict):
            self.simple_mode_flows.update(simple_flows)
        self.enable_ai_intervention_var = tk.BooleanVar(value=bool(launcher_state.get("enableAiIntervention", False)))
        self.ui_scale_var = tk.StringVar(value=wt_dpi.scale_to_label(wt_dpi.load_scale_config()))
        self.flow_definition_path_var.set(launcher_state.get("flowDefinitionPath") or FLOW_DEFINITION_FILE)
        state_step_order = launcher_state.get("stepOrderByFlowPath", {})
        self.step_order_by_flow_path = state_step_order if isinstance(state_step_order, dict) else {}

        initial_repo_root = launcher_state.get("repoRoot") or os.environ.get("UI_TARS_REPO_ROOT") or PROJECT_SETTINGS.get(
            "UI_TARS_REPO_ROOT", DEFAULT_UI_TARS_REPO_ROOT
        )
        initial_config_path = launcher_state.get("configPath") or os.environ.get("UI_TARS_CLI_CONFIG") or PROJECT_SETTINGS.get(
            "UI_TARS_CLI_CONFIG", DEFAULT_UI_TARS_CONFIG
        )
        config_data, _error = load_json_file(initial_config_path)
        config_data = config_data or {}

        self.ui_tars_repo_var = tk.StringVar(value=initial_repo_root)
        self.ui_tars_config_path_var = tk.StringVar(value=initial_config_path)
        self.api_key_var = tk.StringVar(
            value=launcher_state.get("apiKey")
            or os.environ.get("VOLC_API_KEY")
            or os.environ.get("UI_TARS_API_KEY")
            or config_data.get("apiKey", "")
        )
        self.model_name_var = tk.StringVar(
            value=launcher_state.get("model")
            or os.environ.get("MODEL_NAME")
            or os.environ.get("UI_TARS_MODEL")
            or config_data.get("model", "")
            or DEFAULT_MODEL_NAME
        )
        self.base_url_var = tk.StringVar(
            value=launcher_state.get("baseURL")
            or os.environ.get("UI_TARS_VLM_BASE_URL")
            or config_data.get("baseURL", "")
            or DEFAULT_BASE_URL
        )
        self.use_responses_api_var = tk.BooleanVar(
            value=bool(launcher_state.get("useResponsesApi", config_data.get("useResponsesApi", False)))
        )
        self.show_api_key_var = tk.BooleanVar(value=False)
        self.recent_models = self._normalize_recent_models(launcher_state.get("recentModels", []))
        self.model_history_var = tk.StringVar(value="")
        self.model_history_values = []

        self._configure_styles()
        self._build_ui()
        self._refresh_api_entry_mode()
        self._refresh_model_history_options(select_current=True)
        self._refresh_config_summary()
        self._refresh_template_library_summary()
        self._load_recent_log()
        self.current_run_report = None
        self.run_report_step_items = []
        self._refresh_run_report_view()
        self._refresh_flow_steps()
        self.root.after(120, self._poll_output_queue)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        container = tk.Frame(self.root, padx=16, pady=16, bg=self.theme["bg"])
        container.pack(fill=tk.BOTH, expand=True)

        # ── 头部：标题 + 模式切换 ──
        header = tk.Frame(container, bg=self.theme["primary"], padx=20, pady=14)
        header.pack(fill=tk.X)
        header.columnconfigure(0, weight=1)

        title_frame = tk.Frame(header, bg=self.theme["primary"])
        title_frame.grid(row=0, column=0, sticky="w")
        tk.Label(
            title_frame,
            text="WT 自动化项目总控台",
            font=("Microsoft YaHei UI", 16, "bold"),
            bg=self.theme["primary"],
            fg="white",
        ).pack(anchor="w")
        tk.Label(
            title_frame,
            text="集成流程运行监测、模板制作、运行检测、模型配置编辑与日志打包",
            fg="#dbeafe",
            bg=self.theme["primary"],
        ).pack(anchor="w", pady=(2, 0))

        # 模式切换按钮（顶部右侧）
        mode_frame = tk.Frame(header, bg=self.theme["primary"])
        mode_frame.grid(row=0, column=1, sticky="e", padx=(20, 0))
        self.ui_mode_var = tk.StringVar(value="advanced")

        self.btn_simple_mode = tk.Button(
            mode_frame, text="▸ Simple", font=("Microsoft YaHei UI", 10, "bold"),
            command=lambda: self._switch_ui_mode("simple"),
            relief=tk.FLAT, bd=0, padx=16, pady=6,
            cursor="hand2",
        )
        self.btn_simple_mode.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_advanced_mode = tk.Button(
            mode_frame, text="Advanced ◂", font=("Microsoft YaHei UI", 10, "bold"),
            command=lambda: self._switch_ui_mode("advanced"),
            relief=tk.FLAT, bd=0, padx=16, pady=6,
            cursor="hand2",
        )
        self.btn_advanced_mode.pack(side=tk.LEFT)
        self._update_mode_button_styles()

        # ── Simple 模式内容区 ──
        self.simple_frame = tk.Frame(container, bg=self.theme["bg"])
        self._build_simple_panel(self.simple_frame)

        # ── Advanced 模式内容区（原界面） ──
        self.advanced_frame = tk.Frame(container, bg=self.theme["bg"])

        self.main_paned = tk.PanedWindow(
            self.advanced_frame,
            orient=tk.HORIZONTAL,
            sashwidth=12,
            sashrelief=tk.RAISED,
            showhandle=True,
            handlesize=10,
            handlepad=6,
            bd=0,
            bg=self.theme["bg"],
            sashcursor="sb_h_double_arrow",
            opaqueresize=True,
        )
        self.main_paned.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(
            self.main_paned,
            width=430,
            padx=14,
            pady=14,
            bg=self.theme["card"],
            highlightthickness=1,
            highlightbackground=self.theme["border"],
        )
        left_frame.pack_propagate(False)

        right_frame = tk.Frame(
            self.main_paned,
            padx=14,
            pady=14,
            bg=self.theme["card"],
            highlightthickness=1,
            highlightbackground=self.theme["border"],
        )

        self.main_paned.add(left_frame, minsize=wt_dpi.scale(360))
        self.main_paned.add(right_frame, minsize=wt_dpi.scale(760))
        self.root.after(120, lambda: self._set_left_panel_width(430))

        self._build_left_panel(left_frame)
        self._build_right_panel(right_frame)

        # 默认显示 Advanced 模式
        self.simple_frame.pack_forget()
        self.advanced_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))

    # ── 模式切换 ──────────────────────────────────────────────────────────────

    def _update_mode_button_styles(self):
        active_bg = "#3b82f6"
        inactive_bg = "#1e40af"
        active_fg = "white"
        inactive_fg = "#93c5fd"
        mode = self.ui_mode_var.get()
        self.btn_simple_mode.config(
            bg=active_bg if mode == "simple" else inactive_bg,
            fg=active_fg if mode == "simple" else inactive_fg,
        )
        self.btn_advanced_mode.config(
            bg=active_bg if mode == "advanced" else inactive_bg,
            fg=active_fg if mode == "advanced" else inactive_fg,
        )

    def _switch_ui_mode(self, mode):
        self.ui_mode_var.set(mode)
        self._update_mode_button_styles()
        if mode == "simple":
            self.advanced_frame.pack_forget()
            self.simple_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        else:
            self.simple_frame.pack_forget()
            self.advanced_frame.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.root.update_idletasks()

    # ── Simple 模式界面 ──────────────────────────────────────────────────────

    SIMPLE_SECTIONS = [
        {"key": "terrain", "title": "新建地形信息数据", "icon": "🗺️"},
        {"key": "weather", "title": "新建气象数据", "icon": "🌤️"},
        {"key": "turbine", "title": "新建风机类型", "icon": "🌬️"},
        {"key": "project", "title": "新建工程项目", "icon": "📋"},
        {"key": "cfd", "title": "发送 CFD 计算", "icon": "⚙️"},
        {"key": "comprehensive", "title": "发送综合计算", "icon": "📊"},
    ]

    def _build_simple_panel(self, parent):
        """构建 Simple 模式界面：6 个功能板块卡片，2 列 3 行布局。"""
        theme = self.theme

        # ── 顶部操作栏 ──
        toolbar = tk.Frame(parent, bg=theme["bg"])
        toolbar.pack(fill=tk.X, pady=(0, 12))

        tk.Label(toolbar, text="运行流程", font=("Microsoft YaHei UI", 14, "bold"),
                 bg=theme["bg"], fg=theme["text"]).pack(side=tk.LEFT)

        tk.Button(toolbar, text="全选", command=lambda: self._simple_toggle_all(True),
                  bg=theme["secondary"], fg=theme["text"], relief=tk.FLAT,
                  padx=12, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=(20, 4))
        tk.Button(toolbar, text="取消全选", command=lambda: self._simple_toggle_all(False),
                  bg=theme["secondary"], fg=theme["text"], relief=tk.FLAT,
                  padx=12, pady=4, cursor="hand2").pack(side=tk.LEFT, padx=4)

        sep = tk.Frame(toolbar, width=1, bg=theme["border"])
        sep.pack(side=tk.LEFT, fill=tk.Y, padx=12)

        self.btn_simple_run = tk.Button(
            toolbar, text="▶ 运行所选板块", command=self._run_simple_mode,
            bg="#059669", fg="white", font=("Microsoft YaHei UI", 10, "bold"),
            relief=tk.FLAT, padx=20, pady=6, cursor="hand2",
        )
        self.btn_simple_run.pack(side=tk.LEFT)

        self.simple_status_var = tk.StringVar(value="就绪")
        tk.Label(toolbar, textvariable=self.simple_status_var, bg=theme["bg"],
                 fg=theme["muted"], font=("Microsoft YaHei UI", 9)).pack(side=tk.RIGHT)

        # ── 卡片网格容器（可滚动） ──
        canvas_frame = tk.Frame(parent, bg=theme["bg"])
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(canvas_frame, bg=theme["bg"], highlightthickness=0)
        h_scroll = tk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        scrollable = tk.Frame(canvas, bg=theme["bg"])

        scrollable.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable, anchor="nw", tags="inner")
        canvas.configure(yscrollcommand=h_scroll.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        h_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮支持
        def _on_mousewheel(event):
            canvas.yview_scroll(-1 * (event.delta // 120), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel, add="+")
        # 控件进入/离开 canvas 时绑定/解绑滚轮，避免干扰其他可滚动区域
        canvas.bind("<Enter>", lambda e: setattr(self, "_simple_scroll_canvas", canvas))
        canvas.bind("<Leave>", lambda e: setattr(self, "_simple_scroll_canvas", None))

        # ── 6 个板块卡片 ──
        self.simple_section_vars = {}  # key -> {"enabled": BooleanVar, "path": str, ...}
        section_widgets = {}  # key -> {"path_label": Label, "frame": Frame}

        for i, sec in enumerate(self.SIMPLE_SECTIONS):
            key = sec["key"]
            enabled_var = tk.BooleanVar(value=True)
            path_key = f"simple_{key}_path"
            flow_path = getattr(self, path_key, "") or self.simple_mode_flows.get(key, "")

            self.simple_section_vars[key] = {
                "enabled": enabled_var,
                "path": flow_path,
            }

            # 卡片外框
            card = tk.Frame(
                scrollable, bg=theme["card"],
                highlightthickness=1, highlightbackground=theme["border"],
                padx=14, pady=12,
            )
            row = i // 2
            col = i % 2
            card.grid(row=row, column=col, sticky="nsew", padx=6, pady=6)
            scrollable.columnconfigure(0, weight=1)
            scrollable.columnconfigure(1, weight=1)
            scrollable.rowconfigure(row, weight=0)

            # ── 标题行：勾选 + 图标 + 名称 ──
            title_row = tk.Frame(card, bg=theme["card"])
            title_row.pack(fill=tk.X, anchor="w")

            tk.Checkbutton(title_row, variable=enabled_var, bg=theme["card"],
                           font=("Microsoft YaHei UI", 12, "bold"), cursor="hand2").pack(side=tk.LEFT)
            tk.Label(title_row, text=f"{sec['icon']} {sec['title']}",
                     font=("Microsoft YaHei UI", 12, "bold"), bg=theme["card"],
                     fg=theme["text"]).pack(side=tk.LEFT, padx=(4, 0))

            # ── 默认流程路径 ──
            path_row = tk.Frame(card, bg=theme["card"])
            path_row.pack(fill=tk.X, anchor="w", pady=(8, 0))

            tk.Label(path_row, text="当前默认:", font=("Microsoft YaHei UI", 9),
                     bg=theme["card"], fg=theme["muted"]).pack(side=tk.LEFT)
            path_label = tk.Label(
                path_row, text=flow_path or "（未设置）",
                font=("Consolas", 9), bg=theme["card"],
                fg=theme["primary"] if flow_path else "#9ca3af",
                anchor="w",
            )
            path_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

            # ── 按钮行 ──
            btn_row = tk.Frame(card, bg=theme["card"])
            btn_row.pack(fill=tk.X, anchor="w", pady=(8, 0))

            def _make_import_flow(k=key):
                return lambda: self._simple_import_flow(k)

            def _make_import_excel(k=key):
                return lambda: self._simple_import_excel(k)

            def _make_export(k=key):
                return lambda: self._simple_export_flow(k)

            ip_btn = tk.Button(btn_row, text="导入流程", command=_make_import_flow(),
                               bg=theme["primary_soft"], fg=theme["primary"],
                               relief=tk.FLAT, padx=10, pady=2, cursor="hand2",
                               font=("Microsoft YaHei UI", 9))
            ip_btn.pack(side=tk.LEFT, padx=(0, 4))

            ie_btn = tk.Button(btn_row, text="导入Excel", command=_make_import_excel(),
                               bg=theme["primary_soft"], fg=theme["primary"],
                               relief=tk.FLAT, padx=10, pady=2, cursor="hand2",
                               font=("Microsoft YaHei UI", 9))
            ie_btn.pack(side=tk.LEFT, padx=4)

            ex_btn = tk.Button(btn_row, text="导出", command=_make_export(),
                               bg=theme["secondary"], fg=theme["muted"],
                               relief=tk.FLAT, padx=10, pady=2, cursor="hand2",
                               font=("Microsoft YaHei UI", 9))
            ex_btn.pack(side=tk.LEFT, padx=4)

            section_widgets[key] = {"path_label": path_label, "frame": card}

        # 保存对 widget 的引用供导入后更新
        self._simple_section_widgets = section_widgets

    def _simple_import_flow(self, section_key):
        """为某个板块导入流程链路文件（JSON）。"""
        path = filedialog.askopenfilename(
            title=f"导入流程链路文件 - {section_key}",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.simple_section_vars[section_key]["path"] = path
        self._simple_update_path_label(section_key)
        self._simple_save_state()

    def _simple_import_excel(self, section_key):
        """为某个板块导入 Excel 流程定义，自动转换为 flow 并设为默认。"""
        path = filedialog.askopenfilename(
            title=f"导入流程 Excel - {section_key}",
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")],
        )
        if not path:
            return
        try:
            flow_payload = load_flow_payload_from_excel(path)
            if not flow_payload:
                messagebox.showwarning("导入失败", "Excel 文件未能解析为有效的流程定义。")
                return
            # 保存为 JSON 并设为该板块的默认
            target_dir = os.path.join(BASE_DIR, "flow_packages")
            os.makedirs(target_dir, exist_ok=True)
            base_name = f"simple_{section_key}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            target_path = os.path.join(target_dir, base_name)
            with open(target_path, "w", encoding="utf-8") as f:
                json.dump(flow_payload, f, ensure_ascii=False, indent=2)
            self.simple_section_vars[section_key]["path"] = target_path
            self._simple_update_path_label(section_key)
            self._simple_save_state()
            messagebox.showinfo("导入完成", f"已将 Excel 转换为流程文件并设为默认:\n{target_path}")
        except Exception as exc:
            messagebox.showerror("导入失败", f"导入 Excel 时出错:\n{exc}")

    def _simple_export_flow(self, section_key):
        """导出某个板块的默认流程文件。"""
        flow_path = self.simple_section_vars[section_key]["path"]
        if not flow_path:
            messagebox.showinfo("提示", f"该板块尚未设置默认流程，无法导出。")
            return
        out_path = filedialog.asksaveasfilename(
            title=f"导出流程 - {section_key}",
            initialfile=os.path.basename(flow_path),
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not out_path:
            return
        try:
            shutil.copy2(flow_path, out_path)
            messagebox.showinfo("导出完成", f"已导出到:\n{out_path}")
        except Exception as exc:
            messagebox.showerror("导出失败", str(exc))

    def _simple_update_path_label(self, section_key):
        w = self._simple_section_widgets.get(section_key)
        if not w:
            return
        path = self.simple_section_vars[section_key]["path"]
        w["path_label"].config(
            text=path or "（未设置）",
            fg=self.theme["primary"] if path else "#9ca3af",
        )

    def _simple_toggle_all(self, checked):
        for sec in self.SIMPLE_SECTIONS:
            self.simple_section_vars[sec["key"]]["enabled"].set(checked)

    def _simple_save_state(self):
        """将 Simple 模式各板块的流程配置持久化到 launcher_state.json。"""
        try:
            state, _ = load_json_file(LAUNCHER_STATE_FILE)
            state = state or {}
            simple_flows = {}
            for sec in self.SIMPLE_SECTIONS:
                key = sec["key"]
                path = self.simple_section_vars.get(key, {}).get("path", "")
                if path:
                    simple_flows[key] = path
            state["simpleModeFlows"] = simple_flows
            save_json_file(LAUNCHER_STATE_FILE, state)
        except Exception:
            pass

    def _run_simple_mode(self):
        """运行 Simple 模式中勾选的板块（顺序执行，线程安全）。"""
        selected = []
        for sec in self.SIMPLE_SECTIONS:
            key = sec["key"]
            info = self.simple_section_vars.get(key, {})
            if info.get("enabled", tk.BooleanVar(value=False)).get() and info.get("path"):
                selected.append(key)

        if not selected:
            messagebox.showinfo("提示", "请先勾选要运行的板块，并确保每个板块已设置默认流程文件。")
            return

        self.btn_simple_run.config(state=tk.DISABLED)
        self._simple_run_queue = list(selected)
        self._simple_run_index = 0
        self._simple_run_next()

    def _simple_run_next(self):
        """启动下一个待运行板块（由 after 在主线程中调度，线程安全）。"""
        if self._simple_run_index >= len(self._simple_run_queue):
            self.simple_status_var.set("全部运行完成 ✓")
            self.btn_simple_run.config(state=tk.NORMAL)
            self._simple_run_queue = []
            return

        key = self._simple_run_queue[self._simple_run_index]
        sec = next(s for s in self.SIMPLE_SECTIONS if s["key"] == key)
        flow_path = self.simple_section_vars.get(key, {}).get("path", "")
        idx = self._simple_run_index + 1
        total = len(self._simple_run_queue)

        if not flow_path or not os.path.exists(flow_path):
            self.simple_status_var.set(f"⚠ {idx}/{total} {sec['title']}: 流程文件不存在")
            self._simple_run_index += 1
            self.root.after(500, self._simple_run_next)
            return

        self.simple_status_var.set(f"▶ {idx}/{total}: {sec['title']}")
        original_path = self._get_flow_definition_path()
        self.flow_definition_path_var.set(flow_path)
        self._launch_automation([], banner=f"========== Simple: {sec['title']} ==========")
        self.flow_definition_path_var.set(original_path)

        self._simple_run_index += 1
        # 启动后台监控线程，等待进程退出后调度下一个
        import threading as _th
        _th.Thread(target=self._simple_wait_for_next, args=(idx, total, sec["title"]), daemon=True).start()

    def _simple_wait_for_next(self, idx, total, title):
        """等待当前进程退出，然后调度下一个板块（在后台线程中）。"""
        try:
            if self.process:
                self.process.wait()
        except Exception:
            pass
        time.sleep(0.5)
        self.root.after(0, self._simple_run_next)

    def _configure_styles(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Modern.TCombobox", padding=6)
        style.configure("RunReport.Treeview", rowheight=30)
        style.configure("RunReport.Treeview.Heading", padding=(6, 8))

    def _create_secondary_button(self, parent, text, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            fg=self.theme["text"],
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            pady=6,
        )

    def _build_tool_section(self, parent, title, buttons):
        frame = tk.LabelFrame(
            parent,
            text=title,
            padx=10,
            pady=10,
            bg=self.theme["card"],
            fg=self.theme["text"],
            bd=1,
            relief=tk.GROOVE,
        )
        frame.pack(fill=tk.X, pady=(8, 0))
        for text, handler in buttons:
            self._create_secondary_button(frame, text, handler).pack(fill=tk.X, pady=4)
        return frame

    def _build_left_panel(self, parent):
        outer = tk.Frame(parent, bg=self.theme["card"])
        outer.pack(fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0, borderwidth=0, bg=self.theme["card"])
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(
            outer,
            orient=tk.VERTICAL,
            command=canvas.yview,
            relief=tk.FLAT,
            width=14,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            troughcolor=self.theme.get("toolbar", self.theme["bg"]),
            highlightthickness=0,
            bd=0,
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=scrollbar.set)

        content = tk.Frame(canvas, bg=self.theme["card"])
        canvas_window = canvas.create_window((0, 0), window=content, anchor="nw")

        def on_content_configure(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(canvas_window, width=canvas.winfo_width())

        def on_canvas_configure(_event=None):
            canvas.itemconfig(canvas_window, width=canvas.winfo_width())

        content.bind("<Configure>", on_content_configure)
        canvas.bind("<Configure>", on_canvas_configure)

        def on_left_mousewheel(event):
            delta = 0
            if getattr(event, "delta", 0):
                delta = -1 * int(event.delta / 120)
            elif getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            if delta:
                canvas.yview_scroll(delta, "units")
            return "break"

        def bind_left_mousewheel(_event=None):
            canvas.bind_all("<MouseWheel>", on_left_mousewheel)
            canvas.bind_all("<Button-4>", on_left_mousewheel)
            canvas.bind_all("<Button-5>", on_left_mousewheel)

        def unbind_left_mousewheel(_event=None):
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        for widget in (outer, canvas, content):
            widget.bind("<Enter>", bind_left_mousewheel)
            widget.bind("<Leave>", unbind_left_mousewheel)

        tk.Label(
            content,
            text="快捷入口",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=self.theme["card"],
            fg=self.theme["text"],
        ).pack(anchor="w")
        tk.Label(
            content,
            text="提示：鼠标滚轮可上下滚动；拖动中间分隔条可调整左侧宽度。",
            bg=self.theme["card"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=360,
        ).pack(fill=tk.X, anchor="w", pady=(4, 0))

        action_frame = tk.Frame(content, bg=self.theme["card"])
        action_frame.pack(fill=tk.X, pady=(10, 0))

        self.start_button = tk.Button(
            action_frame,
            text="启动自动化流程",
            height=2,
            command=self.start_automation,
            bg=self.theme["primary"],
            fg="white",
            activebackground=self.theme["primary_active"],
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2",
        )
        self.start_button.pack(fill=tk.X, pady=(0, 8))

        self.stop_button = tk.Button(
            action_frame,
            text="停止当前流程",
            command=self.stop_automation,
            state=tk.DISABLED,
            bg=self.theme["danger"],
            fg="white",
            activebackground=self.theme["danger_active"],
            activeforeground="white",
            relief=tk.FLAT,
            bd=0,
            font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2",
        )
        self.stop_button.pack(fill=tk.X, pady=4)

        test_frame = tk.LabelFrame(
            content,
            text="步骤 / 流程包测试",
            padx=10,
            pady=10,
            bg=self.theme["card"],
            fg=self.theme["text"],
            bd=1,
            relief=tk.GROOVE,
        )
        test_frame.pack(fill=tk.X, pady=(14, 0))
        tk.Checkbutton(
            test_frame,
            text="跳过启动/导入（假定 WT 已处于测试界面）",
            variable=self.skip_setup_var,
            bg=self.theme["card"],
            fg=self.theme["text"],
            activebackground=self.theme["card"],
        ).pack(anchor="w")
        tk.Checkbutton(
            test_frame,
            text="显示流程监视器",
            variable=self.show_monitor_var,
            bg=self.theme["card"],
            fg=self.theme["text"],
            activebackground=self.theme["card"],
        ).pack(anchor="w", pady=(4, 0))
        tk.Checkbutton(
            test_frame,
            text="启用 AI 失效介入（仅在普通执行和模板兜底都失败后调用 UI-TARS）",
            variable=self.enable_ai_intervention_var,
            command=self._refresh_config_summary,
            bg=self.theme["card"],
            fg=self.theme["text"],
            activebackground=self.theme["card"],
            wraplength=340,
            justify=tk.LEFT,
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))

        # 界面缩放（类似 Windows 显示缩放）
        scale_row = tk.Frame(test_frame, bg=self.theme["card"])
        scale_row.pack(fill=tk.X, pady=(8, 0))
        tk.Label(
            scale_row,
            text="界面缩放",
            bg=self.theme["card"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.LEFT)
        ttk.Combobox(
            scale_row,
            textvariable=self.ui_scale_var,
            values=[label for label, _ in wt_dpi.SCALE_PRESETS],
            state="readonly",
            width=8,
        ).pack(side=tk.LEFT, padx=(8, 4))
        tk.Label(
            scale_row,
            text="（修改后本窗口即时生效，其余窗口重启后生效）",
            bg=self.theme["card"],
            fg=self.theme.get("muted", self.theme["text"]),
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT)
        self.ui_scale_var.trace_add("write", lambda *_args: self._on_ui_scale_changed())

        flow_file_frame = tk.Frame(test_frame, bg=self.theme["card"])
        flow_file_frame.pack(fill=tk.X, pady=(8, 0))
        tk.Label(
            flow_file_frame,
            text="当前链路文件",
            bg=self.theme["card"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w")
        tk.Label(
            flow_file_frame,
            textvariable=self.flow_definition_path_var,
            justify=tk.LEFT,
            anchor="w",
            wraplength=340,
            bg=self.theme["card"],
            fg=self.theme["muted"],
        ).pack(fill=tk.X, anchor="w", pady=(4, 0))

        step_toolbar = tk.Frame(test_frame, bg=self.theme["card"])
        step_toolbar.pack(fill=tk.X, pady=(8, 0))
        tk.Button(
            step_toolbar,
            text="加载链路文件",
            command=self.select_flow_definition_file,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Button(
            step_toolbar,
            text="全选",
            command=lambda: self._set_all_step_checks(True),
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(
            step_toolbar,
            text="全不选",
            command=lambda: self._set_all_step_checks(False),
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(
            step_toolbar,
            text="上移已选",
            command=lambda: self._move_selected_steps(-1),
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(
            step_toolbar,
            text="下移已选",
            command=lambda: self._move_selected_steps(1),
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(8, 0))
        tk.Button(
            step_toolbar,
            text="刷新步骤",
            command=self._refresh_flow_steps,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        steps_box = tk.Frame(test_frame, bg=self.theme["card"])
        steps_box.pack(fill=tk.X, pady=(8, 0))
        self.steps_canvas = tk.Canvas(
            steps_box,
            height=260,
            highlightthickness=1,
            highlightbackground=self.theme["border"],
            borderwidth=0,
            bg="#fbfdff",
        )
        self.steps_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.steps_scrollbar = tk.Scrollbar(
            steps_box,
            orient=tk.VERTICAL,
            command=self.steps_canvas.yview,
            relief=tk.FLAT,
            width=16,
            bg=self.theme["secondary"],
            activebackground=self.theme["primary_soft"],
            troughcolor=self.theme.get("toolbar", self.theme["bg"]),
            highlightthickness=0,
            bd=0,
        )
        self.steps_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.steps_canvas.configure(yscrollcommand=self._on_steps_canvas_scroll)

        self.steps_inner = tk.Frame(self.steps_canvas, bg="#fbfdff")
        self.steps_canvas_window = self.steps_canvas.create_window((0, 0), window=self.steps_inner, anchor="nw")

        def on_steps_inner_configure(_event=None):
            self.steps_canvas.configure(scrollregion=self.steps_canvas.bbox("all"))

        def on_steps_canvas_configure(_event=None):
            self.steps_canvas.itemconfig(self.steps_canvas_window, width=self.steps_canvas.winfo_width())

        def on_steps_mousewheel(event):
            delta = 0
            if getattr(event, "delta", 0):
                delta = -1 * int(event.delta / 120)
            elif getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            if delta:
                self.steps_canvas.yview_scroll(delta, "units")
            return "break"

        def bind_steps_mousewheel(_event=None):
            self.steps_canvas.bind_all("<MouseWheel>", on_steps_mousewheel)
            self.steps_canvas.bind_all("<Button-4>", on_steps_mousewheel)
            self.steps_canvas.bind_all("<Button-5>", on_steps_mousewheel)

        def unbind_steps_mousewheel(_event=None):
            self.steps_canvas.unbind_all("<MouseWheel>")
            self.steps_canvas.unbind_all("<Button-4>")
            self.steps_canvas.unbind_all("<Button-5>")

        self.steps_inner.bind("<Configure>", on_steps_inner_configure)
        self.steps_canvas.bind("<Configure>", on_steps_canvas_configure)
        self.steps_canvas.bind("<Enter>", bind_steps_mousewheel)
        self.steps_canvas.bind("<Leave>", unbind_steps_mousewheel)
        tk.Label(
            test_frame,
            textvariable=self.step_scroll_hint_var,
            bg=self.theme["card"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X, anchor="w", pady=(6, 0))

        action_row = tk.Frame(test_frame, bg=self.theme["card"])
        action_row.pack(fill=tk.X, pady=(8, 0))
        tk.Button(
            action_row,
            text="运行所选步骤",
            command=self.start_selected_steps,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        ).pack(
            side=tk.LEFT, fill=tk.X, expand=True
        )
        tk.Button(
            action_row,
            text="从所选开始",
            command=self.start_from_selected_step,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        ).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0)
        )

        package_row = tk.Frame(test_frame, bg=self.theme["card"])
        package_row.pack(fill=tk.X, pady=(8, 0))
        tk.Label(
            package_row,
            text="流程包",
            bg=self.theme["card"],
            fg=self.theme["text"],
        ).pack(side=tk.LEFT)
        self.flow_package_combo = ttk.Combobox(
            package_row,
            textvariable=self.selected_flow_package_var,
            state="readonly",
            style="Modern.TCombobox",
        )
        self.flow_package_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 8))
        tk.Button(
            package_row,
            text="测试流程包",
            command=self.start_selected_flow_package,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            font=("Microsoft YaHei UI", 9, "bold"),
            cursor="hand2",
        ).pack(side=tk.RIGHT)

        tk.Label(
            content,
            text="模型配置",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=self.theme["card"],
            fg=self.theme["text"],
        ).pack(anchor="w", pady=(18, 0))

        model_frame = tk.LabelFrame(
            content,
            text="UI-TARS / VLM 参数",
            padx=10,
            pady=10,
            bg=self.theme["card"],
            fg=self.theme["text"],
            bd=1,
            relief=tk.GROOVE,
        )
        model_frame.pack(fill=tk.X, pady=(8, 0))
        model_frame.columnconfigure(1, weight=1)

        tk.Label(model_frame, text="最近模型", bg=self.theme["card"], fg=self.theme["text"]).grid(row=0, column=0, sticky="w", pady=4)
        self.model_history_combo = ttk.Combobox(
            model_frame,
            textvariable=self.model_history_var,
            state="readonly",
            style="Modern.TCombobox",
        )
        self.model_history_combo.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)
        self.model_history_combo.bind("<<ComboboxSelected>>", self._on_recent_model_selected)

        tk.Label(model_frame, text="API Key", bg=self.theme["card"], fg=self.theme["text"]).grid(row=1, column=0, sticky="w", pady=4)
        self.api_key_entry = tk.Entry(model_frame, textvariable=self.api_key_var, show="*", width=30)
        self.api_key_entry.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=4)
        tk.Checkbutton(
            model_frame,
            text="显示",
            variable=self.show_api_key_var,
            command=self._refresh_api_entry_mode,
            bg=self.theme["card"],
            fg=self.theme["text"],
            activebackground=self.theme["card"],
        ).grid(row=1, column=2, sticky="w", padx=(8, 0))

        tk.Label(model_frame, text="模型名称", bg=self.theme["card"], fg=self.theme["text"]).grid(row=2, column=0, sticky="w", pady=4)
        tk.Entry(model_frame, textvariable=self.model_name_var).grid(row=2, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        tk.Label(model_frame, text="BaseURL", bg=self.theme["card"], fg=self.theme["text"]).grid(row=3, column=0, sticky="w", pady=4)
        tk.Entry(model_frame, textvariable=self.base_url_var).grid(row=3, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        tk.Label(model_frame, text="仓库路径", bg=self.theme["card"], fg=self.theme["text"]).grid(row=4, column=0, sticky="w", pady=4)
        tk.Entry(model_frame, textvariable=self.ui_tars_repo_var).grid(row=4, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        tk.Label(model_frame, text="配置文件", bg=self.theme["card"], fg=self.theme["text"]).grid(row=5, column=0, sticky="w", pady=4)
        tk.Entry(model_frame, textvariable=self.ui_tars_config_path_var).grid(row=5, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)

        tk.Checkbutton(
            model_frame,
            text="useResponsesApi",
            variable=self.use_responses_api_var,
            bg=self.theme["card"],
            fg=self.theme["text"],
            activebackground=self.theme["card"],
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 4))

        model_action_frame = tk.Frame(model_frame, bg=self.theme["card"])
        model_action_frame.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        tk.Button(
            model_action_frame,
            text="保存模型配置",
            command=self.save_model_config,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(
            model_action_frame,
            text="重新加载配置",
            command=self.reload_model_config,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        tk.Label(
            content,
            text="工具入口",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=self.theme["card"],
            fg=self.theme["text"],
        ).pack(anchor="w", pady=(18, 0))
        self._build_tool_section(
            content,
            "流程设计与转换",
            [
                ("启动 pywinauto recorder", self.open_pywinauto_recorder),
                ("同步录制脚本(增量·最新)", self.sync_recorded_scripts),
                ("打开流程链路编辑", self.open_flow_editor),
                ("相对区域取点", self.open_relative_region_helper),
                ("转换 Recorder 脚本", self.convert_recorder_script),
                ("导出流程 Excel", self.export_flow_excel),
                ("导入流程 Excel", self.import_flow_excel),
            ],
        )
        self._build_tool_section(
            content,
            "模板与资源库",
            [
                ("进入模板制作", self.open_template_builder),
                ("打开控件库", self.open_control_import_standalone),
                ("进入控件库采集", self.open_control_map_builder),
                ("实时控件检测", self.open_live_detector),
                ("外部控件采集(uia-peek/axe)", self.open_external_capture),
                ("打开模板库目录", self.open_template_root_dir),
                ("刷新模板库概览", self.refresh_template_library_summary_action),
            ],
        )
        self._build_tool_section(
            content,
            "检查与日志",
            [
                ("运行环境检测", self.run_environment_check),
                ("模型配置检查", self.run_model_check),
                ("打开 UI-TARS 配置", self.open_ui_tars_config),
                ("打开运行日志", self.open_log_file),
                ("分析运行日志·最近一次", self.analyze_run_logs_last),
                ("分析运行日志·汇总趋势", self.analyze_run_logs_aggregate),
                ("一键日志打包", self.package_debug_logs),
            ],
        )

        info_frame = tk.LabelFrame(
            content,
            text="当前状态",
            padx=10,
            pady=10,
            bg=self.theme["card"],
            fg=self.theme["text"],
            bd=1,
            relief=tk.GROOVE,
        )
        info_frame.pack(fill=tk.X, pady=(18, 0))
        tk.Label(info_frame, textvariable=self.status_var, justify=tk.LEFT, anchor="w", wraplength=370).pack(fill=tk.X, anchor="w")
        tk.Label(info_frame, textvariable=self.current_step_var, justify=tk.LEFT, anchor="w", wraplength=370, pady=6).pack(fill=tk.X, anchor="w")
        tk.Label(info_frame, textvariable=self.process_var, justify=tk.LEFT, anchor="w", wraplength=370).pack(fill=tk.X, anchor="w")

        for child in info_frame.winfo_children():
            child.configure(bg=self.theme["card"], fg=self.theme["text"])

        config_frame = tk.LabelFrame(
            content,
            text="配置概览",
            padx=10,
            pady=10,
            bg=self.theme["card"],
            fg=self.theme["text"],
            bd=1,
            relief=tk.GROOVE,
        )
        config_frame.pack(fill=tk.X, pady=(18, 0))
        tk.Label(
            config_frame,
            textvariable=self.config_summary_var,
            justify=tk.LEFT,
            anchor="w",
            wraplength=370,
            fg=self.theme["muted"],
            bg=self.theme["card"],
        ).pack(fill=tk.X, anchor="w")

        template_frame = tk.LabelFrame(
            content,
            text="模板库概览",
            padx=10,
            pady=10,
            bg=self.theme["card"],
            fg=self.theme["text"],
            bd=1,
            relief=tk.GROOVE,
        )
        template_frame.pack(fill=tk.X, pady=(18, 0))
        tk.Label(
            template_frame,
            textvariable=self.template_summary_var,
            justify=tk.LEFT,
            anchor="w",
            wraplength=370,
            fg=self.theme["muted"],
            bg=self.theme["card"],
        ).pack(fill=tk.X, anchor="w")
        category_box = tk.Frame(template_frame, bg=self.theme["card"])
        category_box.pack(fill=tk.X, pady=(10, 0))
        tk.Label(
            category_box,
            text="分类列表",
            bg=self.theme["card"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w")
        category_list_wrap = tk.Frame(category_box, bg=self.theme["card"])
        category_list_wrap.pack(fill=tk.X, pady=(6, 0))
        self.template_category_listbox = tk.Listbox(
            category_list_wrap,
            height=6,
            exportselection=False,
            activestyle="none",
            relief=tk.FLAT,
            bg="#fbfdff",
            fg=self.theme["text"],
            highlightthickness=1,
            highlightbackground=self.theme["border"],
            selectbackground=self.theme["primary"],
            selectforeground="white",
        )
        self.template_category_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.template_category_listbox.bind("<<ListboxSelect>>", self._on_template_category_selected)
        category_scrollbar = tk.Scrollbar(category_list_wrap, command=self.template_category_listbox.yview, relief=tk.FLAT)
        category_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.template_category_listbox.config(yscrollcommand=category_scrollbar.set)
        tk.Label(
            category_box,
            textvariable=self.template_category_var,
            justify=tk.LEFT,
            anchor="w",
            wraplength=340,
            fg=self.theme["muted"],
            bg=self.theme["card"],
        ).pack(fill=tk.X, anchor="w", pady=(8, 0))
        category_button_row = tk.Frame(category_box, bg=self.theme["card"])
        category_button_row.pack(fill=tk.X, pady=(8, 0))
        self._create_secondary_button(
            category_button_row,
            "打开所选分类",
            self.open_selected_template_category,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._create_secondary_button(
            category_button_row,
            "打开模板根目录",
            self.open_template_root_dir,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

    def _set_left_panel_width(self, target_width):
        if not hasattr(self, "main_paned"):
            return
        try:
            total_width = int(self.main_paned.winfo_width() or 0)
            safe_width = int(target_width)
            if total_width > 0:
                safe_width = max(360, min(safe_width, total_width - 760))
            self.main_paned.sash_place(0, safe_width, 1)
        except Exception:
            pass

    def _on_steps_canvas_scroll(self, first, last):
        if hasattr(self, "steps_scrollbar"):
            self.steps_scrollbar.set(first, last)
        try:
            first_value = float(first)
            last_value = float(last)
        except Exception:
            self.step_scroll_hint_var.set("步骤列表位置：滚动中")
            return
        if first_value <= 0.001 and last_value >= 0.999:
            self.step_scroll_hint_var.set("步骤列表位置：全部可见")
        elif first_value <= 0.001:
            self.step_scroll_hint_var.set("步骤列表位置：顶部")
        elif last_value >= 0.999:
            self.step_scroll_hint_var.set("步骤列表位置：底部")
        else:
            self.step_scroll_hint_var.set(
                f"步骤列表位置：{int(round(first_value * 100))}% - {int(round(last_value * 100))}%"
            )

    def _build_right_panel(self, parent):
        top_info = tk.Frame(parent, bg=self.theme["card"])
        top_info.pack(fill=tk.X)
        tk.Label(
            top_info,
            text="流程运行监测",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=self.theme["card"],
            fg=self.theme["text"],
        ).pack(side=tk.LEFT)
        tk.Label(
            top_info,
            text="实时输出 / 运行报告 / 检测回显 / 打包结果",
            fg=self.theme["muted"],
            bg=self.theme["card"],
        ).pack(side=tk.LEFT, padx=(8, 0))

        action_row = tk.Frame(parent, bg=self.theme["card"])
        action_row.pack(fill=tk.X, pady=(10, 0))
        self._create_secondary_button(action_row, "刷新运行报告", self._refresh_run_report_view).pack(side=tk.LEFT)
        self._create_secondary_button(action_row, "打开报告 JSON", self.open_last_run_report).pack(side=tk.LEFT, padx=(8, 0))
        self._create_secondary_button(action_row, "打开报告目录", self.open_run_report_dir).pack(side=tk.LEFT, padx=(8, 0))
        self._create_secondary_button(action_row, "导出步骤结果 Excel", self.export_run_report_steps_excel).pack(side=tk.LEFT, padx=(8, 0))

        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        log_tab = tk.Frame(notebook, bg=self.theme["card"])
        report_tab = tk.Frame(notebook, bg=self.theme["card"])
        notebook.add(log_tab, text="运行日志")
        notebook.add(report_tab, text="运行报告")

        text_frame = tk.Frame(log_tab, bg=self.theme["card"])
        text_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 10),
            bg="#111111",
            fg="#f5f5f5",
            insertbackground="#f5f5f5",
            relief=tk.FLAT,
            padx=10,
            pady=10,
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(text_frame, command=self.log_text.yview, relief=tk.FLAT)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

        self.log_text.tag_configure("info", foreground="#f5f5f5")
        self.log_text.tag_configure("error", foreground="#ff7b72")
        self.log_text.tag_configure("success", foreground="#7ee787")
        self.log_text.tag_configure("system", foreground="#79c0ff")
        self.log_text.tag_configure("warning", foreground="#e3b341")

        summary_frame = tk.LabelFrame(
            report_tab,
            text="最近一次运行摘要",
            padx=10,
            pady=10,
            bg=self.theme["card"],
            fg=self.theme["text"],
            bd=1,
            relief=tk.GROOVE,
        )
        summary_frame.pack(fill=tk.X, pady=(0, 10))
        tk.Label(
            summary_frame,
            textvariable=self.run_report_summary_var,
            justify=tk.LEFT,
            anchor="w",
            wraplength=760,
            bg=self.theme["card"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(fill=tk.X, anchor="w")
        tk.Label(
            summary_frame,
            textvariable=self.run_report_meta_var,
            justify=tk.LEFT,
            anchor="w",
            wraplength=760,
            bg=self.theme["card"],
            fg=self.theme["muted"],
        ).pack(fill=tk.X, anchor="w", pady=(6, 0))

        report_split = tk.PanedWindow(report_tab, orient=tk.VERTICAL, sashrelief=tk.FLAT, bg=self.theme["card"], bd=0)
        report_split.pack(fill=tk.BOTH, expand=True)

        report_list_frame = tk.LabelFrame(
            report_split,
            text="步骤结果",
            padx=8,
            pady=8,
            bg=self.theme["card"],
            fg=self.theme["text"],
            bd=1,
            relief=tk.GROOVE,
        )
        report_detail_frame = tk.LabelFrame(
            report_split,
            text="步骤详情",
            padx=8,
            pady=8,
            bg=self.theme["card"],
            fg=self.theme["text"],
            bd=1,
            relief=tk.GROOVE,
        )
        report_split.add(report_list_frame, stretch="always")
        report_split.add(report_detail_frame, stretch="always")

        report_tree_wrap = tk.Frame(report_list_frame, bg=self.theme["card"])
        report_tree_wrap.pack(fill=tk.BOTH, expand=True)
        self.run_report_tree = ttk.Treeview(
            report_tree_wrap,
            columns=("seq", "stepName", "status", "elapsed", "strategy"),
            show="headings",
            height=10,
            style="RunReport.Treeview",
        )
        self.run_report_tree.heading("seq", text="#")
        self.run_report_tree.heading("stepName", text="步骤")
        self.run_report_tree.heading("status", text="结果")
        self.run_report_tree.heading("elapsed", text="耗时(秒)")
        self.run_report_tree.heading("strategy", text="策略")
        self.run_report_tree.column("seq", width=40, minwidth=40, stretch=False, anchor="center")
        self.run_report_tree.column("stepName", width=320, minwidth=180, stretch=False, anchor="w")
        self.run_report_tree.column("status", width=90, minwidth=80, stretch=False, anchor="center")
        self.run_report_tree.column("elapsed", width=90, minwidth=80, stretch=False, anchor="center")
        self.run_report_tree.column("strategy", width=260, minwidth=160, stretch=False, anchor="w")
        self.run_report_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.run_report_tree.bind("<<TreeviewSelect>>", self._on_run_report_step_select)
        self.run_report_tree.tag_configure("success", foreground="#15803d")
        self.run_report_tree.tag_configure("failed", foreground="#dc2626")
        self.run_report_tree.tag_configure("skipped", foreground="#b45309")

        report_tree_scrollbar = tk.Scrollbar(report_tree_wrap, command=self.run_report_tree.yview, relief=tk.FLAT)
        report_tree_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        report_tree_h_scrollbar = tk.Scrollbar(report_list_frame, orient=tk.HORIZONTAL, command=self.run_report_tree.xview, relief=tk.FLAT)
        report_tree_h_scrollbar.pack(fill=tk.X, pady=(6, 0))
        self.run_report_tree.config(yscrollcommand=report_tree_scrollbar.set, xscrollcommand=report_tree_h_scrollbar.set)

        self.run_report_detail_text = tk.Text(
            report_detail_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 10),
            bg="#fbfdff",
            fg=self.theme["text"],
            relief=tk.FLAT,
            padx=10,
            pady=10,
            insertbackground=self.theme["text"],
        )
        self.run_report_detail_text.pack(fill=tk.BOTH, expand=True)

    def _refresh_api_entry_mode(self):
        self.api_key_entry.config(show="" if self.show_api_key_var.get() else "*")

    def _append_log(self, message, tag="info"):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message.rstrip() + "\n", tag)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _log_block(self, title, lines):
        self._append_log(f"========== {title} ==========", tag="system")
        for line, tag in lines:
            self._append_log(line, tag=tag)

    def _tag_for_line(self, line):
        text = line.lower()
        if "错误" in line or "failed" in text or "traceback" in text:
            return "error"
        if "警告" in line or "warning" in text:
            return "warning"
        if "完成" in line or "成功" in line:
            return "success"
        if "启动" in line or "开始" in line or "状态" in line:
            return "system"
        return "info"

    def _load_recent_log(self, max_lines=15):
        if not os.path.exists(LOG_FILE):
            self._append_log("尚未检测到历史运行日志。", tag="system")
            return

        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as file_obj:
                lines = file_obj.readlines()
        except OSError as exc:
            self._append_log(f"读取历史日志失败：{exc}", tag="error")
            return

        self._append_log("已载入最近一次流程日志片段：", tag="system")
        for line in lines[-max_lines:]:
            self._append_log(line.rstrip(), tag=self._tag_for_line(line))

    def _set_run_report_detail_text(self, text):
        if not hasattr(self, "run_report_detail_text"):
            return
        self.run_report_detail_text.config(state=tk.NORMAL)
        self.run_report_detail_text.delete("1.0", tk.END)
        self.run_report_detail_text.insert("1.0", text)
        self.run_report_detail_text.config(state=tk.DISABLED)

    def _load_last_run_report(self):
        payload, error = load_json_file(LAST_RUN_REPORT_FILE)
        if error or not isinstance(payload, dict):
            return None, error or "运行报告格式不正确"
        return payload, None

    def _refresh_run_report_view(self):
        report, error = self._load_last_run_report()
        self.current_run_report = report if isinstance(report, dict) else None
        self.run_report_step_items = []
        if hasattr(self, "run_report_tree"):
            self.run_report_tree.delete(*self.run_report_tree.get_children())
        if not report:
            self.run_report_summary_var.set("运行报告：尚未生成")
            self.run_report_meta_var.set(f"未找到最近一次运行报告：{LAST_RUN_REPORT_FILE}" if error else "最近一次流程执行后，会在这里展示结构化运行结果。")
            self._set_run_report_detail_text("当前还没有可展示的结构化运行报告。")
            return

        summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
        self.run_report_summary_var.set(
            "运行状态：{status} | 请求 {requested} 步 | 实际记录 {executed} 步 | 成功 {success} | 失败 {failed} | 跳过 {skipped} | fallback {fallback}".format(
                status=report.get("status", "unknown"),
                requested=summary.get("requestedCount", len(report.get("stepsRequested", []) or [])),
                executed=summary.get("executedCount", len(report.get("stepResults", []) or [])),
                success=summary.get("successCount", 0),
                failed=summary.get("failedCount", 0),
                skipped=summary.get("skippedCount", 0),
                fallback=summary.get("fallbackCount", 0),
            )
        )
        self.run_report_meta_var.set(
            "开始：{started} | 结束：{ended} | 总耗时：{elapsed:.3f} 秒 | 报告：{path}".format(
                started=report.get("startedAt", "") or "-",
                ended=report.get("endedAt", "") or "-",
                elapsed=float(summary.get("totalElapsedSeconds", 0.0) or 0.0),
                path=report.get("reportPath", "") or LAST_RUN_REPORT_FILE,
            )
        )

        step_results = report.get("stepResults", []) if isinstance(report.get("stepResults"), list) else []
        for index, item in enumerate(step_results, start=1):
            if not isinstance(item, dict):
                continue
            self.run_report_step_items.append(item)
            self.run_report_tree.insert(
                "",
                tk.END,
                iid=str(index - 1),
                values=(
                    index,
                    str(item.get("stepName", "")).strip() or str(item.get("stepId", "")).strip(),
                    str(item.get("status", "")).strip() or "-",
                    f"{float(item.get('elapsedSeconds', 0.0) or 0.0):.3f}",
                    str(item.get("strategy", "")).strip() or "-",
                ),
                tags=(str(item.get("status", "")).strip(),),
            )
        if self.run_report_step_items:
            self.run_report_tree.selection_set("0")
            self._on_run_report_step_select()
        else:
            self._set_run_report_detail_text("当前报告没有记录任何步骤结果。")

    def _on_run_report_step_select(self, _event=None):
        if not hasattr(self, "run_report_tree"):
            return
        selected = self.run_report_tree.selection()
        if not selected:
            self._set_run_report_detail_text("请选择一条步骤结果查看详情。")
            return
        try:
            item = self.run_report_step_items[int(selected[-1])]
        except Exception:
            self._set_run_report_detail_text("当前步骤详情读取失败。")
            return
        detail_payload = {
            "stepId": item.get("stepId", ""),
            "stepName": item.get("stepName", ""),
            "status": item.get("status", ""),
            "actionType": item.get("actionType", ""),
            "strategy": item.get("strategy", ""),
            "elapsedSeconds": item.get("elapsedSeconds", 0.0),
            "error": item.get("error", ""),
            "extra": item.get("extra", {}),
        }
        self._set_run_report_detail_text(json.dumps(detail_payload, ensure_ascii=False, indent=2))

    def open_last_run_report(self):
        if not os.path.exists(LAST_RUN_REPORT_FILE):
            messagebox.showinfo("提示", "当前还没有生成运行报告。")
            return
        os.startfile(LAST_RUN_REPORT_FILE)

    def open_run_report_dir(self):
        os.makedirs(RUN_REPORT_DIR, exist_ok=True)
        os.startfile(RUN_REPORT_DIR)

    def _load_run_log_analyzer(self):
        """惰性加载 tools/analyze_run_logs.py（只读分析工具），并缓存模块。"""
        module = getattr(self, "_run_log_analyzer_module", None)
        if module is not None:
            return module
        tool_path = os.path.join(BASE_DIR, "tools", "analyze_run_logs.py")
        spec = importlib.util.spec_from_file_location("wt_analyze_run_logs", tool_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self._run_log_analyzer_module = module
        return module

    def _render_run_log_analysis(self, print_callable, analysis):
        """把分析工具的文本输出捕获后填入运行报告详情面板。"""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            print_callable(analysis)
        self._set_run_report_detail_text(buffer.getvalue())

    def analyze_run_logs_last(self):
        """诊断最近一次运行：首因定位 + 软成功 + 耗时热点，结果显示在报告面板。"""
        try:
            analyzer = self._load_run_log_analyzer()
            report = analyzer.resolve_single_report("last", RUN_REPORT_DIR)
            if report is None:
                self._append_log("未找到可分析的运行报告，先跑一次自动化流程后再试。", tag="warning")
                self._set_run_report_detail_text("未找到可分析的运行报告。先跑一次自动化流程后再试。")
                return
            analysis = analyzer.build_single_analysis(report)
            self._render_run_log_analysis(analyzer.print_single_analysis, analysis)
            root_cause = analysis.get("rootCause")
            if root_cause:
                self._append_log(
                    f"运行日志分析(最近一次)：首因 {root_cause.get('stepId', '')} "
                    f"{root_cause.get('stepName', '')}",
                    tag="error",
                )
            else:
                self._append_log(
                    f"运行日志分析(最近一次)：{analysis.get('runId', '')} 无失败步 ✅",
                    tag="success",
                )
        except Exception as exc:
            self._append_log(f"分析运行日志失败：{exc}", tag="error")

    def analyze_run_logs_aggregate(self):
        """聚合最近若干次运行：失败频次 / 错误签名聚类 / fallback 高频步 / 慢步。"""
        try:
            analyzer = self._load_run_log_analyzer()
            paths = analyzer.iter_report_paths(RUN_REPORT_DIR)
            if not paths:
                self._append_log("报告目录为空，暂无可聚合的运行日志。", tag="warning")
                self._set_run_report_detail_text("报告目录为空。先跑几次自动化流程后再试。")
                return
            recent_paths = paths[-40:]
            reports = [r for r in (analyzer.load_report(p) for p in recent_paths) if r is not None]
            analysis = analyzer.build_aggregate_analysis(reports, top_n=10)
            self._render_run_log_analysis(analyzer.print_aggregate_analysis, analysis)
            self._append_log(
                f"运行日志分析(汇总)：纳入最近 {analysis.get('runCount', 0)} 次运行",
                tag="system",
            )
        except Exception as exc:
            self._append_log(f"分析运行日志失败：{exc}", tag="error")

    def _get_run_report_for_export(self):
        report = self.current_run_report if isinstance(getattr(self, "current_run_report", None), dict) else None
        if report:
            return report
        report, _error = self._load_last_run_report()
        return report if isinstance(report, dict) else None

    def export_run_report_steps_excel(self):
        report = self._get_run_report_for_export()
        if not report:
            messagebox.showinfo("提示", "当前没有可导出的运行报告。")
            return
        step_results = report.get("stepResults", []) if isinstance(report.get("stepResults"), list) else []
        if not step_results:
            messagebox.showinfo("提示", "当前运行报告没有步骤结果可导出。")
            return
        default_name = f"{str(report.get('runId', 'wt_run')).strip() or 'wt_run'}_步骤结果.xlsx"
        target_path = filedialog.asksaveasfilename(
            title="导出步骤结果 Excel",
            initialdir=RUN_REPORT_DIR,
            initialfile=default_name,
            defaultextension=".xlsx",
            filetypes=[("Excel 文件", "*.xlsx")],
        )
        if not target_path:
            return
        try:
            from openpyxl import Workbook
        except Exception:
            messagebox.showerror("导出失败", "当前环境缺少 openpyxl，无法导出 Excel。请先执行: py -3.11 -m pip install openpyxl")
            return
        try:
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = "步骤结果"
            worksheet.append(["编号", "步骤ID", "步骤", "结果", "耗时(秒)", "策略"])
            for index, item in enumerate(step_results, start=1):
                if not isinstance(item, dict):
                    continue
                worksheet.append([
                    index,
                    str(item.get("stepId", "")).strip(),
                    str(item.get("stepName", "")).strip() or str(item.get("stepId", "")).strip(),
                    str(item.get("status", "")).strip() or "-",
                    float(item.get("elapsedSeconds", 0.0) or 0.0),
                    str(item.get("strategy", "")).strip() or "-",
                ])
            workbook.save(target_path)
        except Exception as exc:
            messagebox.showerror("导出失败", f"导出步骤结果 Excel 失败：\n{exc}")
            self._append_log(f"导出步骤结果 Excel 失败：{exc}", tag="error")
            return
        self._append_log(f"已导出步骤结果 Excel：{target_path}", tag="success")
        self.status_var.set("状态：步骤结果 Excel 导出完成")
        messagebox.showinfo("导出完成", f"步骤结果已导出到：\n{target_path}")

    def _get_config_path(self):
        return self.ui_tars_config_path_var.get().strip()

    def _get_flow_definition_path(self):
        return self.flow_definition_path_var.get().strip() or FLOW_DEFINITION_FILE

    def _get_effective_flow_definition_path(self):
        _payload, effective_path, _source_definition_path = load_effective_flow_payload(self._get_flow_definition_path())
        return effective_path

    def _get_repo_root(self):
        return self.ui_tars_repo_var.get().strip()

    def _get_model_config_values(self):
        return {
            "apiKey": self.api_key_var.get().strip(),
            "model": self.model_name_var.get().strip(),
            "baseURL": self.base_url_var.get().strip(),
            "useResponsesApi": bool(self.use_responses_api_var.get()),
            "repoRoot": self._get_repo_root(),
            "configPath": self._get_config_path(),
        }

    def _normalize_recent_models(self, raw_items):
        normalized = []
        if not isinstance(raw_items, list):
            return normalized

        for item in raw_items:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "apiKey": str(item.get("apiKey", "")).strip(),
                    "model": str(item.get("model", "")).strip(),
                    "baseURL": str(item.get("baseURL", "")).strip(),
                    "useResponsesApi": bool(item.get("useResponsesApi", False)),
                    "repoRoot": str(item.get("repoRoot", "")).strip(),
                    "configPath": str(item.get("configPath", "")).strip(),
                    "lastUsedAt": str(item.get("lastUsedAt", "")).strip(),
                }
            )
        return normalized

    def _build_recent_model_label(self, item):
        model = item.get("model") or "未命名模型"
        base_url = item.get("baseURL") or "未配置BaseURL"
        return f"{model} | {base_url}"

    def _find_recent_model_match_index(self, values):
        for index, item in enumerate(self.recent_models):
            if (
                item.get("model", "") == values.get("model", "")
                and item.get("baseURL", "") == values.get("baseURL", "")
                and item.get("repoRoot", "") == values.get("repoRoot", "")
                and item.get("configPath", "") == values.get("configPath", "")
            ):
                return index
        return None

    def _refresh_model_history_options(self, select_current=False):
        self.model_history_values = [self._build_recent_model_label(item) for item in self.recent_models]
        self.model_history_combo["values"] = self.model_history_values

        if not self.model_history_values:
            self.model_history_var.set("")
            return

        if select_current:
            current_values = self._get_model_config_values()
            match_index = self._find_recent_model_match_index(current_values)
            if match_index is not None:
                self.model_history_var.set(self.model_history_values[match_index])
                return

        current_label = self.model_history_var.get().strip()
        if current_label in self.model_history_values:
            self.model_history_var.set(current_label)
        else:
            self.model_history_var.set(self.model_history_values[0])

    def _add_current_config_to_recent_models(self):
        values = self._get_model_config_values()
        if not values["model"]:
            return

        record = {
            "apiKey": values["apiKey"],
            "model": values["model"],
            "baseURL": values["baseURL"],
            "useResponsesApi": values["useResponsesApi"],
            "repoRoot": values["repoRoot"],
            "configPath": values["configPath"],
            "lastUsedAt": datetime.now().isoformat(timespec="seconds"),
        }

        deduped = [record]
        for item in self.recent_models:
            if (
                item.get("model", "") == record["model"]
                and item.get("baseURL", "") == record["baseURL"]
                and item.get("repoRoot", "") == record["repoRoot"]
                and item.get("configPath", "") == record["configPath"]
            ):
                continue
            deduped.append(item)

        self.recent_models = deduped[:MAX_RECENT_MODELS]
        self._refresh_model_history_options(select_current=True)

    def _apply_recent_model_record(self, item):
        self.api_key_var.set(item.get("apiKey", ""))
        self.model_name_var.set(item.get("model", ""))
        self.base_url_var.set(item.get("baseURL", ""))
        self.use_responses_api_var.set(bool(item.get("useResponsesApi", False)))
        self.ui_tars_repo_var.set(item.get("repoRoot", "") or self.ui_tars_repo_var.get())
        self.ui_tars_config_path_var.set(item.get("configPath", "") or self.ui_tars_config_path_var.get())
        self._apply_model_env()
        self._refresh_config_summary()

    def _on_recent_model_selected(self, _event=None):
        selected_label = self.model_history_var.get().strip()
        if not selected_label or selected_label not in self.model_history_values:
            return

        selected_index = self.model_history_values.index(selected_label)
        selected_item = self.recent_models[selected_index]
        self._apply_recent_model_record(selected_item)
        self._append_log(f"已切换到最近使用模型：{selected_item.get('model', '未命名模型')}", tag="system")
        self.status_var.set("状态：已切换最近使用模型")
        self.current_step_var.set("当前步骤：模型参数已从历史记录回填")

    def _on_ui_scale_changed(self):
        """界面缩放变更：写入共享配置并即时应用到主窗口。"""
        try:
            value = wt_dpi.label_to_scale(self.ui_scale_var.get())
            wt_dpi.apply_scale(self.root, value, 1320, 860)
        except Exception as exc:
            self._append_log(f"应用界面缩放失败：{exc}", tag="warning")

    def _save_launcher_state(self):
        values = self._get_model_config_values()
        self._add_current_config_to_recent_models()
        simple_flows = {}
        if hasattr(self, "simple_section_vars"):
            for sec in self.SIMPLE_SECTIONS:
                key = sec["key"]
                path = self.simple_section_vars.get(key, {}).get("path", "")
                if path:
                    simple_flows[key] = path
        save_json_file(
            LAUNCHER_STATE_FILE,
            {
                "apiKey": values["apiKey"],
                "model": values["model"],
                "baseURL": values["baseURL"],
                "useResponsesApi": values["useResponsesApi"],
                "repoRoot": values["repoRoot"],
                "configPath": values["configPath"],
                "enableAiIntervention": bool(self.enable_ai_intervention_var.get()),
                "flowDefinitionPath": self._get_flow_definition_path(),
                "stepOrderByFlowPath": self.step_order_by_flow_path,
                "recentModels": self.recent_models,
                "simpleModeFlows": simple_flows,
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            },
        )

    def _apply_model_env(self, env=None):
        target_env = env if env is not None else os.environ
        values = self._get_model_config_values()
        target_env["UI_TARS_REPO_ROOT"] = values["repoRoot"]
        target_env["UI_TARS_CLI_CONFIG"] = values["configPath"]
        target_env["MODEL_NAME"] = values["model"]
        target_env["UI_TARS_MODEL"] = values["model"]
        target_env["UI_TARS_VLM_BASE_URL"] = values["baseURL"]
        target_env["UI_TARS_USE_RESPONSES_API"] = "true" if values["useResponsesApi"] else "false"
        target_env["WT_ENABLE_AI_INTERVENTION"] = "true" if self.enable_ai_intervention_var.get() else "false"
        target_env["WT_AI_INTERVENTION_LOG_LINES"] = "10"
        if values["apiKey"]:
            target_env["VOLC_API_KEY"] = values["apiKey"]
            target_env["UI_TARS_API_KEY"] = values["apiKey"]
        return target_env

    def _set_running_state(self, running):
        self.start_button.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_button.config(state=tk.NORMAL if running else tk.DISABLED)

    def _mask_secret(self, value):
        if not value:
            return "未配置"
        if len(value) <= 8:
            return "*" * len(value)
        return value[:4] + "*" * max(4, len(value) - 8) + value[-4:]

    def _refresh_config_summary(self):
        values = self._get_model_config_values()
        target_flow_definition_path = self._get_flow_definition_path()
        effective_flow_definition_path = self._get_effective_flow_definition_path()
        flow_definition_lines = [f"链路文件：{target_flow_definition_path}"]
        if effective_flow_definition_path != target_flow_definition_path:
            flow_definition_lines.append(f"运行读取：{effective_flow_definition_path}")
        flow_definition_lines.append(f"流程包仓库：{FLOW_PACKAGE_REGISTRY_FILE}")
        summary = (
            "\n".join(flow_definition_lines)
            + "\n"
            f"程序路径：{'已配置' if DEFAULT_GM_EXE else '未配置'}\n"
            f"UI-TARS仓库：{values['repoRoot'] or '未配置'}\n"
            f"配置文件：{values['configPath'] or '未配置'}\n"
            f"模型：{values['model'] or '未配置'}\n"
            f"BaseURL：{values['baseURL'] or '未配置'}\n"
            f"API Key：{self._mask_secret(values['apiKey'])}\n"
            f"失败后 AI 介入：{'已开启' if self.enable_ai_intervention_var.get() else '未开启'}"
        )
        self.config_summary_var.set(summary)

    def _collect_template_category_rows(self):
        rows = []
        category_counts = {}
        if os.path.exists(TEMPLATE_INDEX_FILE):
            payload, error = load_json_file(TEMPLATE_INDEX_FILE)
            if not error and isinstance(payload, dict):
                categories = payload.get("categories", [])
                for item in categories:
                    if not isinstance(item, dict):
                        continue
                    category_id = str(item.get("id", "")).strip()
                    if not category_id:
                        continue
                    try:
                        count = int(item.get("count", 0))
                    except Exception:
                        count = 0
                    category_counts[category_id] = max(count, 0)
        if os.path.isdir(TEMPLATE_ROOT_DIR):
            for entry in os.listdir(TEMPLATE_ROOT_DIR):
                entry_path = os.path.join(TEMPLATE_ROOT_DIR, entry)
                if not os.path.isdir(entry_path):
                    continue
                category_counts.setdefault(entry, 0)
        for category_id in sorted(category_counts.keys()):
            rows.append(
                {
                    "id": category_id,
                    "count": category_counts.get(category_id, 0),
                    "path": os.path.join(TEMPLATE_ROOT_DIR, category_id),
                }
            )
        return rows

    def _refresh_template_category_list(self):
        self.template_categories = self._collect_template_category_rows()
        if not hasattr(self, "template_category_listbox"):
            return
        listbox = self.template_category_listbox
        listbox.delete(0, tk.END)
        for item in self.template_categories:
            listbox.insert(tk.END, f"{item['id']} ({item['count']})")
        if self.template_categories:
            listbox.selection_set(0)
            listbox.see(0)
            self._update_template_category_detail(0)
        else:
            self.template_category_var.set("当前没有可展示的模板分类。")

    def _update_template_category_detail(self, index):
        if index < 0 or index >= len(self.template_categories):
            self.template_category_var.set("当前没有可展示的模板分类。")
            return
        item = self.template_categories[index]
        path_exists = os.path.isdir(item["path"])
        self.template_category_var.set(
            f"当前分类：{item['id']}\n"
            f"模板数量：{item['count']}\n"
            f"目录状态：{'已存在' if path_exists else '索引存在但目录缺失'}\n"
            f"目录路径：{item['path']}"
        )

    def _on_template_category_selected(self, _event=None):
        if not hasattr(self, "template_category_listbox"):
            return
        selected = self.template_category_listbox.curselection()
        if not selected:
            self._update_template_category_detail(-1)
            return
        self._update_template_category_detail(selected[-1])

    def _refresh_template_library_summary(self):
        if not os.path.isdir(TEMPLATE_ROOT_DIR):
            self.template_summary_var.set("模板库概览：模板根目录尚未创建")
            self.template_categories = []
            if hasattr(self, "template_category_listbox"):
                self.template_category_listbox.delete(0, tk.END)
            self.template_category_var.set("当前没有可展示的模板分类。")
            return

        categories = sorted(
            entry for entry in os.listdir(TEMPLATE_ROOT_DIR) if os.path.isdir(os.path.join(TEMPLATE_ROOT_DIR, entry))
        )
        if os.path.exists(TEMPLATE_INDEX_FILE):
            payload, error = load_json_file(TEMPLATE_INDEX_FILE)
            if error or not isinstance(payload, dict):
                self.template_summary_var.set(
                    f"模板库概览：已发现模板目录 {len(categories)} 个，但索引读取失败\n"
                    f"根目录：{TEMPLATE_ROOT_DIR}"
                )
                return
            meta = payload.get("meta", {}) if isinstance(payload.get("meta", {}), dict) else {}
            category_rows = payload.get("categories", []) if isinstance(payload.get("categories", []), list) else []
            category_text = ", ".join(
                f"{str(item.get('id', '')).strip()}:{int(item.get('count', 0))}"
                for item in category_rows[:6]
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            ) or "暂无"
            suffix = f" 等{len(category_rows)}类" if len(category_rows) > 6 else ""
            self.template_summary_var.set(
                f"模板总数：{meta.get('templateCount', 0)}\n"
                f"分类数量：{meta.get('categoryCount', len(category_rows))}\n"
                f"分类分布：{category_text}{suffix}\n"
                f"根目录：{TEMPLATE_ROOT_DIR}"
            )
            self._refresh_template_category_list()
            return

        self.template_summary_var.set(
            f"模板库概览：索引尚未建立\n"
            f"已有分类目录：{', '.join(categories[:6]) if categories else '暂无'}\n"
            f"根目录：{TEMPLATE_ROOT_DIR}"
        )
        self._refresh_template_category_list()

    def refresh_template_library_summary_action(self):
        self._refresh_template_library_summary()
        self._append_log("已刷新模板库概览。", tag="system")
        self.status_var.set("状态：模板库概览已刷新")
        self.current_step_var.set("当前步骤：模板分类与索引统计已更新")

    def open_selected_template_category(self):
        if not self.template_categories:
            messagebox.showinfo("提示", "当前没有可打开的模板分类。")
            return
        selected_index = 0
        if hasattr(self, "template_category_listbox"):
            selected = self.template_category_listbox.curselection()
            if selected:
                selected_index = selected[-1]
        selected_index = min(max(selected_index, 0), len(self.template_categories) - 1)
        target = self.template_categories[selected_index]
        os.makedirs(target["path"], exist_ok=True)
        os.startfile(target["path"])
        self._append_log(f"已打开模板分类目录：{target['id']}", tag="system")

    def reload_model_config(self):
        config_path = self._get_config_path()
        config_data, error = load_json_file(config_path)
        if error:
            messagebox.showerror("读取失败", error)
            self._append_log(error, tag="error")
            return

        self.api_key_var.set(config_data.get("apiKey", ""))
        self.model_name_var.set(config_data.get("model", "") or DEFAULT_MODEL_NAME)
        self.base_url_var.set(config_data.get("baseURL", "") or DEFAULT_BASE_URL)
        self.use_responses_api_var.set(bool(config_data.get("useResponsesApi", False)))
        self._apply_model_env()
        self._save_launcher_state()
        self._refresh_config_summary()
        self._append_log(f"已从配置文件重新加载模型参数：{config_path}", tag="success")

    def save_model_config(self):
        values = self._get_model_config_values()
        config_path = values["configPath"]
        if not config_path:
            messagebox.showerror("保存失败", "请先填写 UI-TARS 配置文件路径。")
            return

        try:
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            save_json_file(
                config_path,
                {
                    "baseURL": values["baseURL"],
                    "apiKey": values["apiKey"],
                    "model": values["model"],
                    "useResponsesApi": values["useResponsesApi"],
                },
            )
        except Exception as exc:
            messagebox.showerror("保存失败", f"写入配置文件失败：\n{exc}")
            return

        self._apply_model_env()
        self._save_launcher_state()
        self._refresh_config_summary()
        self._append_log(f"已保存模型配置到：{config_path}", tag="success")
        self.status_var.set("状态：模型配置已保存")
        self.current_step_var.set("当前步骤：模型配置已更新")

    def start_automation(self):
        self._launch_automation([], banner="========== 启动新的自动化流程 ==========")

    def _launch_automation(self, extra_args, banner="========== 启动新的自动化流程 =========="):
        if self.process and self.process.poll() is None:
            messagebox.showinfo("提示", "自动化流程已经在运行中。")
            return

        if not os.path.exists(AUTOMATION_SCRIPT):
            messagebox.showerror("启动失败", f"未找到自动化脚本：\n{AUTOMATION_SCRIPT}")
            return

        _payload, flow_definition_path, _source_definition_path, validation_errors = validate_effective_flow_payload(
            self._get_flow_definition_path()
        )
        if not os.path.exists(flow_definition_path):
            messagebox.showerror("启动失败", f"未找到流程链路文件：\n{flow_definition_path}")
            return
        if validation_errors:
            preview_errors = "\n".join(f"- {item}" for item in validation_errors[:8])
            remaining_count = max(0, len(validation_errors) - 8)
            if remaining_count:
                preview_errors += f"\n- 其余 {remaining_count} 项请先在编辑器中修正"
            messagebox.showerror(
                "启动失败",
                "当前流程链路配置存在错误，已阻止启动。\n"
                f"读取文件：\n{flow_definition_path}\n\n"
                f"{preview_errors}",
            )
            self.status_var.set("状态：流程链路配置有误，未启动")
            return

        values = self._get_model_config_values()
        if not values["apiKey"] or not values["model"] or not values["baseURL"]:
            should_continue = messagebox.askyesno(
                "配置不完整",
                "当前 API Key、模型名称或 BaseURL 未填写完整。\n是否仍然继续启动自动化流程？",
            )
            if not should_continue:
                return

        try:
            self._save_launcher_state()
        except Exception as exc:
            self._append_log(f"保存上次使用配置失败：{exc}", tag="warning")

        try:
            if os.path.exists(LOG_FILE):
                os.remove(LOG_FILE)
        except OSError:
            pass

        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)
        self._append_log(banner, tag="system")

        self.status_var.set("状态：正在启动自动化流程")
        self.current_step_var.set("当前步骤：初始化中")
        self.process_var.set("流程进程：启动中")
        self.run_report_summary_var.set("运行报告：等待本次流程执行完成后刷新")
        self.run_report_meta_var.set("当前展示的仍可能是上一次结果；流程退出后会自动重新加载 `last_run_report.json`。")
        self._set_running_state(True)
        self._refresh_config_summary()

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        process_env = self._apply_model_env(os.environ.copy())
        process_env["GM_RUNTIME_CONFIG_JSON"] = json.dumps(load_flow_runtime_config(flow_definition_path), ensure_ascii=False)
        process_env[FLOW_DEFINITION_ENV_KEY] = flow_definition_path
        command = [sys.executable, AUTOMATION_SCRIPT]
        if not self.show_monitor_var.get():
            command.append("--no-monitor")
        command.extend(extra_args or [])
        self.process = subprocess.Popen(
            command,
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
            creationflags=creationflags,
            env=process_env,
        )

        threading.Thread(target=self._read_process_output, daemon=True).start()
        threading.Thread(target=self._wait_for_process_exit, daemon=True).start()

    def _read_process_output(self):
        if not self.process or not self.process.stdout:
            return
        for line in self.process.stdout:
            self.output_queue.put(("line", line.rstrip()))

    def _wait_for_process_exit(self):
        if not self.process:
            return
        return_code = self.process.wait()
        self.output_queue.put(("exit", return_code))

    def _poll_output_queue(self):
        while True:
            try:
                item_type, payload = self.output_queue.get_nowait()
            except queue.Empty:
                break
            if item_type == "line":
                self._handle_output_line(payload)
            elif item_type == "exit":
                self._handle_process_exit(payload)
        self.root.after(120, self._poll_output_queue)

    def _handle_output_line(self, line):
        line = line.strip()
        if not line:
            return
        self._append_log(line, tag=self._tag_for_line(line))
        self.process_var.set("流程进程：运行中")
        if line.startswith("[") and "] " in line:
            self.current_step_var.set(f"当前步骤：{line.split('] ', 1)[1]}")
        if "错误" in line:
            self.status_var.set("状态：流程运行异常")
        elif "完成" in line:
            self.status_var.set("状态：流程执行中，已完成部分步骤")
        else:
            self.status_var.set("状态：流程运行中")

    def _handle_process_exit(self, return_code):
        self._set_running_state(False)
        self.process_var.set(f"流程进程：已结束（退出码 {return_code}）")
        if return_code == 0:
            self.status_var.set("状态：流程完成")
            self.current_step_var.set("当前步骤：自动化流程已完成")
            self._append_log("========== 自动化流程执行完成 ==========", tag="success")
            self.root.bell()
        else:
            self.status_var.set("状态：流程失败")
            self._append_log("========== 自动化流程执行失败 ==========", tag="error")
            self.root.bell()
        self._refresh_run_report_view()
        self.process = None

    def stop_automation(self):
        if not self.process or self.process.poll() is not None:
            messagebox.showinfo("提示", "当前没有正在运行的流程。")
            return
        should_stop = messagebox.askyesno(
            "停止流程",
            "确定要停止当前自动化流程吗？\n停止后可能需要你手动检查目标软件当前界面状态。",
        )
        if not should_stop:
            return
        self._append_log("正在停止自动化流程...", tag="warning")
        self.status_var.set("状态：正在停止流程")
        self.current_step_var.set("当前步骤：等待进程退出")
        threading.Thread(target=self._stop_process_worker, daemon=True).start()

    def _stop_process_worker(self):
        process = self.process
        if not process:
            return
        try:
            process.terminate()
        except Exception:
            pass
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.1)
        try:
            process.kill()
            self.output_queue.put(("line", "[launcher] 已强制结束自动化流程进程。"))
        except Exception as exc:
            self.output_queue.put(("line", f"[launcher] 强制结束进程失败：{exc}"))

    def _refresh_flow_steps(self):
        self.flow_steps = []
        self.flow_step_display_map = {}
        self.flow_packages = []
        target_path = self._get_flow_definition_path()
        effective_payload, effective_path, source_definition_path = load_effective_flow_payload(target_path)
        if not os.path.exists(effective_path):
            self._render_flow_step_list()
            self._render_flow_package_list()
            return
        payload = effective_payload
        if effective_path != target_path:
            source_hint = f"（原链路：{source_definition_path}）" if source_definition_path else ""
            self._append_log(
                f"当前链路文件没有可读取的步骤/流程包，已回退读取流程包仓库：{effective_path}{source_hint}",
                tag="warning",
            )
        raw_steps = payload.get("steps", []) if isinstance(payload, dict) else []
        step_map = {}
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("id", "")).strip()
            if step_id:
                step_map[step_id] = step
        seen_step_ids = set()
        for step in raw_steps:
            if not isinstance(step, dict):
                continue
            if not bool(step.get("topLevel", True)):
                continue
            step_id = str(step.get("id", "")).strip()
            if not step_id:
                continue
            self.flow_steps.append({"id": step_id, "name": str(step.get("name", "")).strip(), "source": "top_level"})
            seen_step_ids.add(step_id)
        raw_packages = payload.get("flowPackages", []) if isinstance(payload, dict) else []
        for pkg in raw_packages:
            if not isinstance(pkg, dict):
                continue
            pkg_id = str(pkg.get("id", "")).strip()
            if not pkg_id:
                continue
            self.flow_packages.append({
                "id": pkg_id,
                "name": str(pkg.get("name", pkg_id)).strip(),
                "description": str(pkg.get("description", "")).strip(),
                "stepIds": pkg.get("stepIds", [])
            })
            for child_step_id in pkg.get("stepIds", []) or []:
                child_step_id = str(child_step_id).strip()
                if not child_step_id or child_step_id in seen_step_ids:
                    continue
                child_step = step_map.get(child_step_id, {})
                self.flow_steps.append(
                    {
                        "id": child_step_id,
                        "name": str(child_step.get("name", "")).strip(),
                        "source": "package",
                        "packageId": pkg_id,
                        "packageName": str(pkg.get("name", pkg_id)).strip(),
                    }
                )
                seen_step_ids.add(child_step_id)
        self._apply_saved_step_order()
        self._render_flow_step_list()
        self._render_flow_package_list()

    def _normalize_flow_path_key(self, file_path=None):
        target_path = file_path or self._get_flow_definition_path()
        return os.path.normcase(os.path.normpath(str(target_path or "").strip()))

    def _apply_saved_step_order(self):
        if not self.flow_steps:
            return
        flow_path_key = self._normalize_flow_path_key()
        saved_order = self.step_order_by_flow_path.get(flow_path_key, [])
        if not isinstance(saved_order, list) or not saved_order:
            return
        item_map = {}
        for item in self.flow_steps:
            step_id = str(item.get("id", "")).strip()
            if step_id and step_id not in item_map:
                item_map[step_id] = item
        reordered = []
        seen_step_ids = set()
        for step_id in saved_order:
            normalized_step_id = str(step_id or "").strip()
            if normalized_step_id and normalized_step_id in item_map and normalized_step_id not in seen_step_ids:
                reordered.append(item_map[normalized_step_id])
                seen_step_ids.add(normalized_step_id)
        for item in self.flow_steps:
            step_id = str(item.get("id", "")).strip()
            if step_id and step_id not in seen_step_ids:
                reordered.append(item)
                seen_step_ids.add(step_id)
        self.flow_steps = reordered

    def _persist_current_step_order(self):
        flow_path_key = self._normalize_flow_path_key()
        if not flow_path_key:
            return
        self.step_order_by_flow_path[flow_path_key] = [
            str(item.get("id", "")).strip()
            for item in self.flow_steps
            if str(item.get("id", "")).strip()
        ]

    def _move_selected_steps(self, direction):
        selected_step_ids = self._get_selected_step_ids()
        if not selected_step_ids:
            messagebox.showinfo("提示", "请先勾选一个或多个步骤，再执行上移或下移。")
            return
        normalized_direction = -1 if int(direction or 0) < 0 else 1
        selected_set = set(selected_step_ids)
        moved = False
        if normalized_direction < 0:
            for index in range(1, len(self.flow_steps)):
                current_id = str(self.flow_steps[index].get("id", "")).strip()
                previous_id = str(self.flow_steps[index - 1].get("id", "")).strip()
                if current_id in selected_set and previous_id not in selected_set:
                    self.flow_steps[index - 1], self.flow_steps[index] = self.flow_steps[index], self.flow_steps[index - 1]
                    moved = True
        else:
            for index in range(len(self.flow_steps) - 2, -1, -1):
                current_id = str(self.flow_steps[index].get("id", "")).strip()
                next_id = str(self.flow_steps[index + 1].get("id", "")).strip()
                if current_id in selected_set and next_id not in selected_set:
                    self.flow_steps[index], self.flow_steps[index + 1] = self.flow_steps[index + 1], self.flow_steps[index]
                    moved = True
        if not moved:
            messagebox.showinfo("提示", "当前所选步骤已经在边界位置，无法继续移动。")
            return
        self._persist_current_step_order()
        try:
            self._save_launcher_state()
        except Exception as exc:
            self._append_log(f"保存步骤测试顺序失败：{exc}", tag="warning")
        self._render_flow_step_list()
        direction_text = "上移" if normalized_direction < 0 else "下移"
        moved_text = "，".join(selected_step_ids[:5])
        if len(selected_step_ids) > 5:
            moved_text += " 等"
        self._append_log(f"已{direction_text}步骤测试顺序：{moved_text}", tag="system")
        self.status_var.set(f"状态：已{direction_text}步骤测试顺序")
        self.current_step_var.set(f"当前步骤：可按新顺序执行所选步骤")

    def _render_flow_step_list(self):
        if not hasattr(self, "steps_inner"):
            return
        for child in self.steps_inner.winfo_children():
            child.destroy()
        preserved = dict(self.step_check_vars)
        self.step_check_vars = {}
        self.flow_step_display_map = {}

        for item in self.flow_steps:
            step_id = item["id"]
            name = item.get("name", "")
            if item.get("source") == "package":
                package_name = item.get("packageName") or item.get("packageId") or "未命名流程包"
                display = f"[流程包] {package_name} / {step_id}" + (f" | {name}" if name else "")
            else:
                display = f"{step_id} | {name}" if name else step_id
            self.flow_step_display_map[display] = step_id
            var = preserved.get(step_id) if isinstance(preserved.get(step_id), tk.BooleanVar) else tk.BooleanVar(value=False)
            self.step_check_vars[step_id] = var
            tk.Checkbutton(
                self.steps_inner,
                text=display,
                variable=var,
                anchor="w",
                justify=tk.LEFT,
                bg="#fbfdff",
                fg=self.theme["text"],
                activebackground="#fbfdff",
                wraplength=340,
            ).pack(fill=tk.X, anchor="w")

    def _render_flow_package_list(self):
        if not hasattr(self, "flow_package_combo"):
            return
        values = []
        current_label = self.selected_flow_package_var.get().strip()
        for package in self.flow_packages:
            package_id = package["id"]
            package_name = package.get("name", "")
            step_count = len(package.get("stepIds", []) or [])
            values.append(f"{package_id} | {package_name} | {step_count} steps")
        self.flow_package_combo["values"] = values
        if current_label in values:
            self.selected_flow_package_var.set(current_label)
        elif values:
            self.selected_flow_package_var.set(values[0])
        else:
            self.selected_flow_package_var.set("")

    def _get_selected_flow_package(self):
        selected_label = self.selected_flow_package_var.get().strip()
        if not selected_label:
            return None
        package_id = selected_label.split("|", 1)[0].strip()
        for package in self.flow_packages:
            if package.get("id") == package_id:
                return package
        return None

    def _format_flow_step_display(self, item):
        step_id = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        if item.get("source") == "package":
            package_name = item.get("packageName") or item.get("packageId") or "未命名流程包"
            return f"[流程包] {package_name} / {step_id}" + (f" | {name}" if name else "")
        return f"{step_id} | {name}" if name else step_id

    def _format_flow_package_display(self, package):
        package_id = str(package.get("id", "")).strip()
        package_name = str(package.get("name", package_id)).strip()
        step_count = len(package.get("stepIds", []) or [])
        return f"{package_id} | {package_name} | {step_count} steps"

    def _prompt_export_flow_scope(self):
        if not self.flow_steps:
            self._refresh_flow_steps()
        step_items = [item for item in self.flow_steps if str(item.get("id", "")).strip()]
        if not step_items:
            messagebox.showinfo("提示", "当前链路文件里没有可导出的步骤。")
            return None

        result = {}
        dialog = tk.Toplevel(self.root)
        dialog.title("选择导出范围")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg=self.theme["bg"])
        wt_dpi.geometry(dialog, 760, 560)
        dialog.minsize(wt_dpi.scale(680), wt_dpi.scale(500))

        mode_var = tk.StringVar(value="all")
        selected_step_ids = self._get_selected_step_ids()
        selected_package = self._get_selected_flow_package()

        root_frame = tk.Frame(dialog, bg=self.theme["bg"], padx=16, pady=16)
        root_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            root_frame,
            text="导出流程 Excel",
            bg=self.theme["bg"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            root_frame,
            text="选择这次要导出的范围。当前模式支持全部步骤、按流程包多选导出，以及手工勾选步骤导出。",
            bg=self.theme["bg"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            wraplength=700,
        ).pack(anchor="w", pady=(4, 10))

        mode_frame = tk.Frame(root_frame, bg=self.theme["card"], padx=12, pady=10, highlightthickness=1, highlightbackground=self.theme["border"])
        mode_frame.pack(fill=tk.X)
        for value, label in (
            ("all", "全部步骤"),
            ("package", "按流程包"),
            ("manual", "手工勾选步骤"),
        ):
            tk.Radiobutton(
                mode_frame,
                text=label,
                value=value,
                variable=mode_var,
                bg=self.theme["card"],
                fg=self.theme["text"],
                activebackground=self.theme["card"],
                anchor="w",
            ).pack(side=tk.LEFT, padx=(0, 18))

        content_holder = tk.Frame(root_frame, bg=self.theme["bg"])
        content_holder.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        bottom_frame = tk.Frame(root_frame, bg=self.theme["bg"])
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(12, 0))

        summary_var = tk.StringVar(value="将导出当前链路文件中的全部步骤。")
        tk.Label(
            bottom_frame,
            textvariable=summary_var,
            bg=self.theme["bg"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            wraplength=700,
        ).pack(anchor="w")

        button_row = tk.Frame(bottom_frame, bg=self.theme["bg"])
        button_row.pack(fill=tk.X, pady=(10, 0))

        package_frame = tk.Frame(content_holder, bg=self.theme["card"], padx=12, pady=12, highlightthickness=1, highlightbackground=self.theme["border"])
        tk.Label(
            package_frame,
            text="按流程包导出",
            bg=self.theme["card"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            package_frame,
            text="可多选流程包，导出时会按所选流程包内的 stepIds 汇总步骤。",
            bg=self.theme["card"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            wraplength=660,
        ).pack(anchor="w", pady=(4, 8))
        package_listbox = tk.Listbox(package_frame, selectmode=tk.EXTENDED, exportselection=False, height=8)
        package_listbox.pack(fill=tk.BOTH, expand=False)
        package_display_to_ids = []
        for package in self.flow_packages:
            package_listbox.insert(tk.END, self._format_flow_package_display(package))
            package_display_to_ids.append(str(package.get("id", "")).strip())
        if selected_package:
            selected_package_id = str(selected_package.get("id", "")).strip()
            for index, package_id in enumerate(package_display_to_ids):
                if package_id == selected_package_id:
                    package_listbox.selection_set(index)
                    break
        package_button_row = tk.Frame(package_frame, bg=self.theme["card"])
        package_button_row.pack(fill=tk.X, pady=(8, 0))
        tk.Button(package_button_row, text="全选流程包", command=lambda: package_listbox.selection_set(0, tk.END)).pack(side=tk.LEFT)
        tk.Button(package_button_row, text="清空流程包", command=lambda: package_listbox.selection_clear(0, tk.END)).pack(side=tk.LEFT, padx=(8, 0))

        manual_frame = tk.Frame(content_holder, bg=self.theme["card"], padx=12, pady=12, highlightthickness=1, highlightbackground=self.theme["border"])
        tk.Label(
            manual_frame,
            text="手工勾选步骤导出",
            bg=self.theme["card"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            manual_frame,
            text="可多选步骤。若你已经在主界面的步骤区勾选了步骤，这里会自动带入当前勾选结果。",
            bg=self.theme["card"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            wraplength=660,
        ).pack(anchor="w", pady=(4, 8))
        manual_listbox = tk.Listbox(manual_frame, selectmode=tk.EXTENDED, exportselection=False, height=14)
        manual_listbox.pack(fill=tk.BOTH, expand=True)
        manual_step_ids = []
        selected_step_id_set = set(selected_step_ids)
        for index, item in enumerate(step_items):
            manual_listbox.insert(tk.END, self._format_flow_step_display(item))
            manual_step_id = str(item.get("id", "")).strip()
            manual_step_ids.append(manual_step_id)
            if manual_step_id in selected_step_id_set:
                manual_listbox.selection_set(index)
        manual_button_row = tk.Frame(manual_frame, bg=self.theme["card"])
        manual_button_row.pack(fill=tk.X, pady=(8, 0))
        tk.Button(manual_button_row, text="全选步骤", command=lambda: manual_listbox.selection_set(0, tk.END)).pack(side=tk.LEFT)
        tk.Button(manual_button_row, text="清空步骤", command=lambda: manual_listbox.selection_clear(0, tk.END)).pack(side=tk.LEFT, padx=(8, 0))

        def _update_scope_summary():
            mode = mode_var.get().strip()
            if mode == "package":
                count = len(package_listbox.curselection())
                summary_var.set(f"当前模式：按流程包导出，已选 {count} 个流程包。")
            elif mode == "manual":
                count = len(manual_listbox.curselection())
                summary_var.set(f"当前模式：手工勾选步骤导出，已选 {count} 个步骤。")
            else:
                summary_var.set(f"当前模式：全部步骤导出，共 {len(step_items)} 个步骤。")

        def _refresh_scope_panels(*_args):
            for child in content_holder.winfo_children():
                child.pack_forget()
            current_mode = mode_var.get().strip()
            if current_mode == "package":
                package_frame.pack(fill=tk.BOTH, expand=True)
            elif current_mode == "manual":
                manual_frame.pack(fill=tk.BOTH, expand=True)
            _update_scope_summary()

        mode_var.trace_add("write", _refresh_scope_panels)
        package_listbox.bind("<<ListboxSelect>>", lambda _event: _update_scope_summary())
        manual_listbox.bind("<<ListboxSelect>>", lambda _event: _update_scope_summary())
        _refresh_scope_panels()

        def _confirm():
            mode = mode_var.get().strip()
            if mode == "all":
                result.update({
                    "mode": "all",
                    "step_ids": None,
                    "scope_label": f"全部步骤（{len(step_items)} 个）",
                })
            elif mode == "package":
                selected_package_indices = list(package_listbox.curselection())
                if not selected_package_indices:
                    messagebox.showinfo("提示", "请先选择一个或多个流程包。", parent=dialog)
                    return
                ordered_step_ids = []
                seen_step_ids = set()
                selected_package_names = []
                for index in selected_package_indices:
                    package = self.flow_packages[index]
                    package_id = str(package.get("id", "")).strip()
                    package_name = str(package.get("name", package_id)).strip() or package_id
                    selected_package_names.append(package_name)
                    for step_id in package.get("stepIds", []) or []:
                        normalized_step_id = str(step_id).strip()
                        if normalized_step_id and normalized_step_id not in seen_step_ids:
                            ordered_step_ids.append(normalized_step_id)
                            seen_step_ids.add(normalized_step_id)
                if not ordered_step_ids:
                    messagebox.showinfo("提示", "所选流程包里没有可导出的步骤。", parent=dialog)
                    return
                result.update({
                    "mode": "package",
                    "step_ids": ordered_step_ids,
                    "scope_label": f"流程包导出（{', '.join(selected_package_names)}）",
                })
            else:
                selected_indices = list(manual_listbox.curselection())
                if not selected_indices:
                    messagebox.showinfo("提示", "请先勾选一个或多个步骤。", parent=dialog)
                    return
                ordered_step_ids = [manual_step_ids[index] for index in selected_indices if 0 <= index < len(manual_step_ids)]
                result.update({
                    "mode": "manual",
                    "step_ids": ordered_step_ids,
                    "scope_label": f"手工勾选步骤（{len(ordered_step_ids)} 个）",
                })
            dialog.destroy()

        def _cancel():
            result.clear()
            dialog.destroy()

        tk.Button(
            button_row,
            text="取消",
            command=_cancel,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
        ).pack(side=tk.RIGHT)
        tk.Button(
            button_row,
            text="确认导出",
            command=_confirm,
            bg=self.theme["primary"],
            fg="white",
            activebackground=self.theme["primary_active"],
            activeforeground="white",
        ).pack(side=tk.RIGHT, padx=(0, 8))

        dialog.protocol("WM_DELETE_WINDOW", _cancel)
        dialog.wait_window()
        return result or None

    def _prompt_import_flow_target(self, source_path):
        current_path = self._get_flow_definition_path()
        suggested_dir = os.path.dirname(current_path) if current_path else BASE_DIR
        suggested_name = os.path.splitext(os.path.basename(source_path or DEFAULT_FLOW_XLSX))[0] + ".json"
        default_package_name = self._build_default_import_package_name(source_path)
        default_package_id = self._build_default_import_package_id(source_path)

        result = {}
        dialog = tk.Toplevel(self.root)
        dialog.title("选择导入方式")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.configure(bg=self.theme["bg"])
        wt_dpi.geometry(dialog, 760, 580)
        dialog.minsize(wt_dpi.scale(700), wt_dpi.scale(520))

        save_as_path_var = tk.StringVar(value=os.path.join(suggested_dir, suggested_name))
        package_id_var = tk.StringVar(value=default_package_id)
        package_name_var = tk.StringVar(value=default_package_name)
        package_description_var = tk.StringVar(value=f"由 Excel 导入生成，来源：{os.path.basename(source_path)}")
        summary_var = tk.StringVar(
            value=(
                "覆盖模式：Excel 将直接写入当前链路文件\n"
                f"{current_path}"
            )
        )

        root_frame = tk.Frame(dialog, bg=self.theme["bg"], padx=16, pady=16)
        root_frame.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            root_frame,
            text="导入流程 Excel",
            bg=self.theme["bg"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            root_frame,
            text=(
                "当前导入支持三种方式：\n"
                "1. 覆盖当前链路：直接写到当前选中的链路 JSON。\n"
                "2. 另存为新链路：生成一个新的 JSON，并自动切换为当前链路，方便立刻测试。\n"
                "3. 追加为流程包：把 Excel 内容合并进当前链路，并生成可直接测试的流程包。"
            ),
            bg=self.theme["bg"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            wraplength=700,
        ).pack(anchor="w", pady=(4, 12))

        info_frame = tk.Frame(
            root_frame,
            bg=self.theme["card"],
            padx=12,
            pady=12,
            highlightthickness=1,
            highlightbackground=self.theme["border"],
        )
        info_frame.pack(fill=tk.X)
        tk.Label(
            info_frame,
            text=f"Excel 文件：{source_path}",
            bg=self.theme["card"],
            fg=self.theme["text"],
            justify=tk.LEFT,
            wraplength=680,
        ).pack(anchor="w")
        tk.Label(
            info_frame,
            text=f"当前链路：{current_path}",
            bg=self.theme["card"],
            fg=self.theme["text"],
            justify=tk.LEFT,
            wraplength=680,
        ).pack(anchor="w", pady=(6, 0))

        save_as_frame = tk.Frame(
            root_frame,
            bg=self.theme["card"],
            padx=12,
            pady=12,
            highlightthickness=1,
            highlightbackground=self.theme["border"],
        )
        save_as_frame.pack(fill=tk.X, pady=(12, 0))
        tk.Label(
            save_as_frame,
            text="另存为新链路文件",
            bg=self.theme["card"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            save_as_frame,
            text="建议用于导入新流程或试运行，避免直接覆盖现有稳定链路。",
            bg=self.theme["card"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            wraplength=680,
        ).pack(anchor="w", pady=(4, 8))

        path_row = tk.Frame(save_as_frame, bg=self.theme["card"])
        path_row.pack(fill=tk.X)
        tk.Entry(path_row, textvariable=save_as_path_var).pack(side=tk.LEFT, fill=tk.X, expand=True)

        def _browse_save_as():
            selected_path = filedialog.asksaveasfilename(
                parent=dialog,
                title="保存为新的流程链路文件",
                initialdir=os.path.dirname(save_as_path_var.get().strip()) or suggested_dir or BASE_DIR,
                initialfile=os.path.basename(save_as_path_var.get().strip()) or suggested_name,
                defaultextension=".json",
                filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
            )
            if selected_path:
                save_as_path_var.set(selected_path)
                summary_var.set(f"另存为模式：Excel 将写入新链路文件\n{selected_path}")

        tk.Button(path_row, text="浏览...", command=_browse_save_as).pack(side=tk.LEFT, padx=(8, 0))

        package_frame = tk.Frame(
            root_frame,
            bg=self.theme["card"],
            padx=12,
            pady=12,
            highlightthickness=1,
            highlightbackground=self.theme["border"],
        )
        package_frame.pack(fill=tk.X, pady=(12, 0))
        tk.Label(
            package_frame,
            text="追加为流程包",
            bg=self.theme["card"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w")
        tk.Label(
            package_frame,
            text=(
                "会把 Excel 追加到当前链路文件中，并在步骤 / 流程包测试区域里立即可选。"
                "如果 Excel 已自带 flow_packages，会保留导入包；若 ID 冲突会自动改名。"
            ),
            bg=self.theme["card"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            wraplength=680,
        ).pack(anchor="w", pady=(4, 8))

        tk.Label(package_frame, text="流程包 ID", bg=self.theme["card"], fg=self.theme["text"]).pack(anchor="w")
        tk.Entry(package_frame, textvariable=package_id_var).pack(fill=tk.X, pady=(4, 8))
        tk.Label(package_frame, text="流程包名称", bg=self.theme["card"], fg=self.theme["text"]).pack(anchor="w")
        tk.Entry(package_frame, textvariable=package_name_var).pack(fill=tk.X, pady=(4, 8))
        tk.Label(package_frame, text="流程包说明", bg=self.theme["card"], fg=self.theme["text"]).pack(anchor="w")
        tk.Entry(package_frame, textvariable=package_description_var).pack(fill=tk.X, pady=(4, 0))

        tk.Label(
            root_frame,
            textvariable=summary_var,
            bg=self.theme["bg"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            wraplength=700,
        ).pack(anchor="w", pady=(12, 0))

        button_row = tk.Frame(root_frame, bg=self.theme["bg"])
        button_row.pack(side=tk.BOTTOM, fill=tk.X, pady=(16, 0))

        def _cancel():
            result.clear()
            dialog.destroy()

        def _confirm_overwrite():
            result.update({"mode": "overwrite", "target_path": current_path})
            dialog.destroy()

        def _confirm_save_as():
            target_path = save_as_path_var.get().strip()
            if not target_path:
                messagebox.showinfo("提示", "请先选择新的链路文件保存位置。", parent=dialog)
                return
            result.update({"mode": "save_as", "target_path": target_path})
            dialog.destroy()

        def _confirm_append_package():
            package_id = package_id_var.get().strip()
            package_name = package_name_var.get().strip()
            if not package_id:
                messagebox.showinfo("提示", "请先填写流程包 ID。", parent=dialog)
                return
            if not package_name:
                messagebox.showinfo("提示", "请先填写流程包名称。", parent=dialog)
                return
            result.update(
                {
                    "mode": "append_package",
                    "target_path": current_path,
                    "package_id": package_id,
                    "package_name": package_name,
                    "package_description": package_description_var.get().strip(),
                }
            )
            dialog.destroy()

        tk.Button(
            button_row,
            text="取消",
            command=_cancel,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
        ).pack(side=tk.RIGHT)
        tk.Button(
            button_row,
            text="另存为新链路",
            command=_confirm_save_as,
            bg=self.theme["primary"],
            fg="white",
            activebackground=self.theme["primary_active"],
            activeforeground="white",
        ).pack(side=tk.RIGHT, padx=(0, 8))
        tk.Button(
            button_row,
            text="追加为流程包",
            command=_confirm_append_package,
        ).pack(side=tk.RIGHT, padx=(0, 8))
        tk.Button(
            button_row,
            text="覆盖当前链路",
            command=_confirm_overwrite,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        dialog.protocol("WM_DELETE_WINDOW", _cancel)
        dialog.wait_window()
        return result or None

    def _build_default_import_package_name(self, source_path):
        source_name = os.path.splitext(os.path.basename(source_path or DEFAULT_FLOW_XLSX))[0].strip()
        return source_name or "导入流程包"

    def _build_default_import_package_id(self, source_path):
        raw_name = self._build_default_import_package_name(source_path)
        chars = []
        last_was_separator = False
        for char in raw_name:
            if char.isalnum():
                chars.append(char.lower())
                last_was_separator = False
                continue
            if last_was_separator:
                continue
            chars.append("_")
            last_was_separator = True
        normalized = "".join(chars).strip("_")
        return f"pkg_{normalized or 'imported'}"

    def _ensure_unique_identifier(self, preferred_id, used_ids, fallback_prefix):
        base_id = str(preferred_id).strip() or str(fallback_prefix).strip() or "item"
        if base_id not in used_ids:
            used_ids.add(base_id)
            return base_id
        suffix = 2
        while True:
            candidate = f"{base_id}_{suffix}"
            if candidate not in used_ids:
                used_ids.add(candidate)
                return candidate
            suffix += 1

    def _merge_runtime_config_for_import(self, current_runtime, imported_runtime):
        merged = dict(current_runtime or {})
        imported_runtime = imported_runtime if isinstance(imported_runtime, dict) else {}
        for key, value in imported_runtime.items():
            current_value = str(merged.get(key, "")).strip()
            imported_value = str(value).strip()
            if not current_value and imported_value:
                merged[key] = imported_value
        return merged

    def _prepare_imported_payload_for_package(self, imported_payload, source_path, target_payload, package_options):
        normalized_import = _normalize_flow_payload(imported_payload)
        imported_steps = json.loads(json.dumps(normalized_import.get("steps", []), ensure_ascii=False))
        imported_packages = json.loads(json.dumps(normalized_import.get("flowPackages", []), ensure_ascii=False))
        if not imported_steps:
            raise ValueError("导入的 Excel 中没有可追加的步骤。")

        package_options = package_options if isinstance(package_options, dict) else {}
        default_package_id = str(package_options.get("package_id", "")).strip() or self._build_default_import_package_id(source_path)
        default_package_name = str(package_options.get("package_name", "")).strip() or self._build_default_import_package_name(source_path)
        default_package_description = (
            str(package_options.get("package_description", "")).strip()
            or f"由 Excel 导入生成，来源：{os.path.basename(source_path)}"
        )

        used_step_ids = {
            str(step.get("id", "")).strip()
            for step in target_payload.get("steps", [])
            if isinstance(step, dict) and str(step.get("id", "")).strip()
        }
        used_package_ids = {
            str(package.get("id", "")).strip()
            for package in target_payload.get("flowPackages", [])
            if isinstance(package, dict) and str(package.get("id", "")).strip()
        }

        step_id_map = {}
        for index, step in enumerate(imported_steps, start=1):
            original_step_id = str(step.get("id", "")).strip()
            fallback_step_id = f"{default_package_id}_step_{index}"
            unique_step_id = self._ensure_unique_identifier(original_step_id or fallback_step_id, used_step_ids, fallback_step_id)
            step["id"] = unique_step_id
            if original_step_id:
                step_id_map[original_step_id] = unique_step_id

        package_id_map = {}
        prepared_packages = []
        if imported_packages:
            multiple_packages = len(imported_packages) > 1
            for index, package in enumerate(imported_packages, start=1):
                original_package_id = str(package.get("id", "")).strip()
                fallback_package_id = f"{default_package_id}_{index}" if multiple_packages else default_package_id
                preferred_package_id = original_package_id or fallback_package_id
                if not multiple_packages:
                    preferred_package_id = str(package_options.get("package_id", "")).strip() or preferred_package_id
                unique_package_id = self._ensure_unique_identifier(preferred_package_id, used_package_ids, fallback_package_id)
                package_id_map[original_package_id] = unique_package_id

                package_name = str(package.get("name", "")).strip() or unique_package_id
                package_description = str(package.get("description", "")).strip()
                if not multiple_packages:
                    package_name = str(package_options.get("package_name", "")).strip() or package_name
                    package_description = str(package_options.get("package_description", "")).strip() or package_description

                prepared_packages.append(
                    {
                        "id": unique_package_id,
                        "name": package_name or unique_package_id,
                        "description": package_description or default_package_description,
                        "stepIds": [
                            step_id_map.get(str(step_id).strip(), str(step_id).strip())
                            for step_id in (package.get("stepIds") or [])
                            if str(step_id).strip()
                        ],
                    }
                )
        else:
            synthesized_package_id = self._ensure_unique_identifier(default_package_id, used_package_ids, default_package_id)
            prepared_packages.append(
                {
                    "id": synthesized_package_id,
                    "name": default_package_name or synthesized_package_id,
                    "description": default_package_description,
                    "stepIds": [str(step.get("id", "")).strip() for step in imported_steps if str(step.get("id", "")).strip()],
                }
            )

        for step in imported_steps:
            package_ref = str(step.get("packageRef", "")).strip()
            if package_ref and package_ref in package_id_map:
                step["packageRef"] = package_id_map[package_ref]

        step_ids_by_package_ref = {}
        for step in imported_steps:
            package_ref = str(step.get("packageRef", "")).strip()
            step_id = str(step.get("id", "")).strip()
            if package_ref and step_id:
                step_ids_by_package_ref.setdefault(package_ref, []).append(step_id)

        for package in prepared_packages:
            package_id = str(package.get("id", "")).strip()
            step_ids = [str(step_id).strip() for step_id in (package.get("stepIds") or []) if str(step_id).strip()]
            if not step_ids:
                step_ids = step_ids_by_package_ref.get(package_id, [])
            if not step_ids and len(prepared_packages) == 1:
                step_ids = [str(step.get("id", "")).strip() for step in imported_steps if str(step.get("id", "")).strip()]
            package["stepIds"] = step_ids

        return {
            "steps": imported_steps,
            "flowPackages": prepared_packages,
            "packageLabels": [self._format_flow_package_display(package) for package in prepared_packages],
        }

    def _append_imported_excel_as_package(self, source_path, target_path, package_options, imported_payload=None):
        imported_payload = imported_payload if isinstance(imported_payload, dict) else load_flow_payload_from_excel(source_path)
        target_payload, _target_error = load_json_file(target_path)
        target_payload = target_payload if isinstance(target_payload, dict) else {}
        normalized_target = _normalize_flow_payload(target_payload)
        prepared_payload = self._prepare_imported_payload_for_package(
            imported_payload,
            source_path,
            normalized_target,
            package_options,
        )

        merged_payload = json.loads(json.dumps(target_payload, ensure_ascii=False)) if isinstance(target_payload, dict) else {}
        merged_payload["version"] = str(merged_payload.get("version", "")).strip() or str(imported_payload.get("version", "")).strip() or "1.0"
        merged_payload["project"] = str(merged_payload.get("project", "")).strip() or str(imported_payload.get("project", "")).strip() or "WT_Automation"
        merged_payload["description"] = (
            str(merged_payload.get("description", "")).strip()
            or str(imported_payload.get("description", "")).strip()
            or "WT 自动化流程定义"
        )
        merged_payload["lastUpdated"] = datetime.now().isoformat(timespec="seconds")
        merged_payload["runtimeConfig"] = self._merge_runtime_config_for_import(
            normalized_target.get("runtimeConfig", {}),
            imported_payload.get("runtimeConfig", {}),
        )
        merged_payload["flowPackages"] = list(normalized_target.get("flowPackages", [])) + prepared_payload["flowPackages"]
        merged_payload["steps"] = list(normalized_target.get("steps", [])) + prepared_payload["steps"]

        validation_errors = validate_flow_definition(merged_payload)
        if validation_errors:
            raise ValueError("导入后的链路校验未通过：\n- " + "\n- ".join(validation_errors[:12]))

        save_json_file(target_path, merged_payload)
        return prepared_payload

    def _show_text_report_dialog(self, title, summary_text, detail_text):
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        wt_dpi.geometry(dialog, 860, 640)
        dialog.minsize(wt_dpi.scale(720), wt_dpi.scale(520))
        dialog.configure(bg=self.theme["bg"])
        dialog.transient(self.root)
        try:
            dialog.grab_set()
        except Exception:
            pass

        container = tk.Frame(dialog, bg=self.theme["bg"], padx=16, pady=16)
        container.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            container,
            text=title,
            bg=self.theme["bg"],
            fg=self.theme["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
            anchor="w",
            justify=tk.LEFT,
        ).pack(fill=tk.X)
        tk.Label(
            container,
            text=summary_text,
            bg=self.theme["bg"],
            fg=self.theme["muted"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=800,
        ).pack(fill=tk.X, pady=(6, 12))

        text_frame = tk.Frame(
            container,
            bg=self.theme["card"],
            highlightthickness=1,
            highlightbackground=self.theme["border"],
        )
        text_frame.pack(fill=tk.BOTH, expand=True)
        report_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="#fbfdff",
            fg=self.theme["text"],
            relief=tk.FLAT,
            bd=0,
            padx=10,
            pady=10,
            insertbackground=self.theme["text"],
        )
        report_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        report_scrollbar = tk.Scrollbar(text_frame, command=report_text.yview, relief=tk.FLAT)
        report_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        report_text.config(yscrollcommand=report_scrollbar.set)
        report_text.insert("1.0", detail_text or "")
        report_text.config(state=tk.DISABLED)

        button_row = tk.Frame(container, bg=self.theme["bg"])
        button_row.pack(fill=tk.X, pady=(12, 0))

        def _copy_report():
            report_body = detail_text or ""
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(report_body)
                self._append_log("已复制回灌审计报告到剪贴板", tag="system")
            except Exception as exc:
                messagebox.showerror("复制失败", f"复制审计报告失败：\n{exc}", parent=dialog)

        tk.Button(
            button_row,
            text="复制报告",
            command=_copy_report,
            bg=self.theme["secondary"],
            activebackground=self.theme["secondary_active"],
        ).pack(side=tk.RIGHT, padx=(8, 0))
        tk.Button(
            button_row,
            text="关闭",
            command=dialog.destroy,
            bg=self.theme["primary"],
            fg="white",
            activebackground=self.theme["primary_active"],
            activeforeground="white",
        ).pack(side=tk.RIGHT)

        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.wait_window()

    def _show_roundtrip_audit_result(self, audit_result, source_path, target_path, import_mode, mode_label):
        if not isinstance(audit_result, dict):
            return
        baseline_path = str(audit_result.get("baselinePath", "")).strip()
        candidate_label = str(audit_result.get("candidateLabel", "")).strip() or os.path.basename(source_path)
        baseline_hint = f"\n审计基线：{baseline_path}" if baseline_path else ""
        if not audit_result.get("available"):
            reason = str(audit_result.get("reason", "")).strip() or "缺少基线信息，未执行关键回灌字段审计。"
            summary_text = (
                f"导入方式：{mode_label}\n"
                f"Excel：{source_path}\n"
                f"目标链路：{target_path}\n"
                f"结果：导入已完成，但自动回灌审计未执行。{baseline_hint}"
            )
            report_text = str(audit_result.get("report", "")).strip() or reason
            self._append_log(f"导入完成，但回灌审计未执行：{reason}", tag="warning")
            self._show_text_report_dialog("导入完成：回灌审计未执行", summary_text, report_text)
            return
        if audit_result.get("hasIssues"):
            issue_count = len(audit_result.get("issues", []) or [])
            summary_text = (
                f"导入方式：{mode_label}\n"
                f"Excel：{source_path}\n"
                f"目标链路：{target_path}\n"
                f"结果：检测到 {issue_count} 项关键回灌字段差异。{baseline_hint}\n"
                f"对比结果：{candidate_label}"
            )
            self._append_log(
                f"导入完成，但回灌审计发现 {issue_count} 项差异：{source_path} -> {target_path}",
                tag="warning",
            )
            self._show_text_report_dialog(
                "导入完成：发现回灌差异",
                summary_text,
                str(audit_result.get("report", "")).strip(),
            )
            return
        self._append_log(
            f"导入完成，关键回灌字段检查通过：{source_path} -> {target_path}",
            tag="success",
        )
        messagebox.showinfo(
            "导入完成",
            "导入完成，关键回灌字段检查通过。\n"
            f"导入方式：{mode_label}\n"
            f"Excel：{source_path}\n"
            f"目标链路：{target_path}{baseline_hint}",
        )

    def _sync_flow_package_registry_for_definition(self, file_path):
        payload, error = load_json_file(file_path)
        if error:
            raise ValueError(error)
        if not isinstance(payload, dict):
            raise ValueError(f"链路文件结构无效：{file_path}")
        sync_flow_package_registry(
            file_path,
            payload.get("runtimeConfig", {}),
            payload.get("flowPackages", []),
            payload.get("steps", []),
        )

    def select_flow_definition_file(self):
        current_path = self._get_flow_definition_path()
        file_path = filedialog.askopenfilename(
            title="加载流程链路文件",
            initialdir=os.path.dirname(current_path) if current_path else BASE_DIR,
            initialfile=os.path.basename(current_path) if current_path else os.path.basename(FLOW_DEFINITION_FILE),
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not file_path:
            return
        self.flow_definition_path_var.set(file_path)
        try:
            self._save_launcher_state()
        except Exception as exc:
            self._append_log(f"保存当前链路文件状态失败：{exc}", tag="warning")
        self._refresh_flow_steps()
        self._refresh_config_summary()
        self._append_log(f"已加载流程链路文件：{file_path}", tag="system")
        self.status_var.set("状态：流程链路文件已加载")
        self.current_step_var.set("当前步骤：可直接测试所选链路文件中的步骤和流程包")

    def _set_all_step_checks(self, value):
        for var in self.step_check_vars.values():
            try:
                var.set(bool(value))
            except Exception:
                pass

    def _get_selected_step_ids(self):
        selected = []
        for item in self.flow_steps:
            step_id = item["id"]
            var = self.step_check_vars.get(step_id)
            if var is not None and bool(var.get()):
                selected.append(step_id)
        return selected

    def start_selected_steps(self):
        step_ids = self._get_selected_step_ids()
        if not step_ids:
            messagebox.showinfo("提示", "请先在步骤列表中选择一个或多个步骤。")
            return
        extra_args = ["--steps", ",".join(step_ids)]
        if self.skip_setup_var.get():
            extra_args.append("--skip-setup")
        self._launch_automation(extra_args, banner=f"========== 启动步骤测试：{','.join(step_ids)} ==========")

    def start_from_selected_step(self):
        step_ids = self._get_selected_step_ids()
        if not step_ids:
            messagebox.showinfo("提示", "请先在步骤列表中选择一个步骤。")
            return
        start_step = step_ids[0]
        extra_args = ["--from-step", start_step]
        if self.skip_setup_var.get():
            extra_args.append("--skip-setup")
        self._launch_automation(extra_args, banner=f"========== 从步骤开始执行：{start_step} ==========")

    def start_selected_flow_package(self):
        package = self._get_selected_flow_package()
        if not package:
            messagebox.showinfo("提示", "请先选择一个流程包。")
            return
        step_ids = [str(item).strip() for item in (package.get("stepIds") or []) if str(item).strip()]
        if not step_ids:
            messagebox.showinfo("提示", f"流程包 {package.get('id', '')} 里还没有可测试的步骤。")
            return
        extra_args = ["--steps", ",".join(step_ids)]
        if self.skip_setup_var.get():
            extra_args.append("--skip-setup")
        package_name = package.get("name") or package.get("id", "")
        self._launch_automation(
            extra_args,
            banner=f"========== 启动流程包测试：{package_name} ({package.get('id', '')}) ==========",
        )

    def open_template_builder(self):
        if not os.path.exists(TEMPLATE_BUILDER_SCRIPT):
            messagebox.showerror("打开失败", f"未找到模板制作脚本：\n{TEMPLATE_BUILDER_SCRIPT}")
            return
        missing_modules = self._get_missing_template_builder_modules()
        if missing_modules:
            missing_text = "、".join(missing_modules)
            install_hint = f'py -3.11 -m pip install {" ".join(self._map_module_to_package_name(name) for name in missing_modules)}'
            message = (
                "模板制作器启动失败，当前环境缺少以下依赖：\n"
                f"{missing_text}\n\n"
                f"建议安装命令：\n{install_hint}"
            )
            messagebox.showerror("依赖缺失", message)
            self._append_log(message, tag="error")
            self.status_var.set("状态：模板制作器启动失败")
            self.current_step_var.set("当前步骤：缺少模板制作器依赖")
            return

        try:
            builder_process = subprocess.Popen(
                [sys.executable, TEMPLATE_BUILDER_SCRIPT],
                cwd=BASE_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动模板制作器失败：\n{exc}")
            self._append_log(f"启动模板制作器失败：{exc}", tag="error")
            return

        self.root.after(1200, lambda: self._check_template_builder_startup(builder_process))

    def _get_missing_template_builder_modules(self):
        required_modules = ["cv2", "numpy", "PIL"]
        return [module_name for module_name in required_modules if importlib.util.find_spec(module_name) is None]

    def _map_module_to_package_name(self, module_name):
        package_map = {
            "cv2": "opencv-python",
            "PIL": "Pillow",
        }
        return package_map.get(module_name, module_name)

    def _check_template_builder_startup(self, process):
        return_code = process.poll()
        if return_code is None:
            self._append_log("已打开模板制作器。", tag="system")
            self.status_var.set("状态：模板制作器已启动")
            self.current_step_var.set("当前步骤：模板制作器运行中")
            return

        stdout, stderr = process.communicate()
        error_output = (stderr or stdout or "").strip()
        if not error_output:
            error_output = f"模板制作器已退出，退出码：{return_code}"

        messagebox.showerror("打开失败", f"模板制作器未能正常启动：\n{error_output}")
        self._append_log(f"模板制作器启动失败：{error_output}", tag="error")
        self.status_var.set("状态：模板制作器启动失败")
        self.current_step_var.set("当前步骤：请检查模板制作器依赖或启动日志")

    def open_control_import_standalone(self):
        """直接打开导入控件界面（独立窗口，不加载流程编辑器）"""
        if not os.path.exists(FLOW_EDITOR_SCRIPT):
            messagebox.showerror("打开失败", f"未找到流程链路编辑器：\n{FLOW_EDITOR_SCRIPT}")
            return

        try:
            if os.path.exists(FLOW_EDITOR_STARTUP_SIGNAL):
                os.remove(FLOW_EDITOR_STARTUP_SIGNAL)
        except OSError:
            pass

        try:
            editor_process = subprocess.Popen(
                [sys.executable, FLOW_EDITOR_SCRIPT, "--startup-ping", FLOW_EDITOR_STARTUP_SIGNAL, "--control-library-standalone"],
                cwd=BASE_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动导入控件失败：\n{exc}")
            self._append_log(f"启动导入控件失败：{exc}", tag="error")
            return

        self.root.after(1600, lambda: self._check_control_import_standalone_startup(editor_process))

    def _check_control_import_standalone_startup(self, process):
        if os.path.exists(FLOW_EDITOR_STARTUP_SIGNAL):
            self._append_log("已打开导入控件。", tag="system")
            self.status_var.set("状态：导入控件已启动")
            self.current_step_var.set("当前步骤：可查看/导入控件库中的控件")
            return

        return_code = process.poll()
        if return_code is None:
            self._append_log("导入控件进程已启动，正在等待窗口就绪。", tag="system")
            self.status_var.set("状态：导入控件启动中")
            self.current_step_var.set("当前步骤：等待导入控件窗口就绪")
            self.root.after(500, lambda: self._check_control_import_standalone_startup(process))
            return

        error_output = ""
        if not error_output:
            error_output = f"导入控件已退出，退出码：{return_code}"

        messagebox.showerror("打开失败", f"导入控件未能正常启动：\n{error_output}")
        self._append_log(f"导入控件启动失败：{error_output}", tag="error")
        self.status_var.set("状态：导入控件启动失败")
        self.current_step_var.set("当前步骤：请检查流程编辑器启动日志")

    def open_control_map_builder(self):
        """打开控件库采集器（用于采集新窗口的控件信息）"""
        if not os.path.exists(CONTROL_MAP_BUILDER_SCRIPT):
            messagebox.showerror("打开失败", f"未找到控件库采集器：\n{CONTROL_MAP_BUILDER_SCRIPT}")
            return

        try:
            builder_process = subprocess.Popen(
                [sys.executable, CONTROL_MAP_BUILDER_SCRIPT],
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动控件库采集器失败：\n{exc}")
            self._append_log(f"启动控件库采集器失败：{exc}", tag="error")
            return

        self.root.after(1200, lambda: self._check_control_map_builder_startup(builder_process))

    def _check_control_map_builder_startup(self, process):
        return_code = process.poll()
        if return_code is None:
            self._append_log("已打开控件库采集器。", tag="system")
            self.status_var.set("状态：控件库采集器已启动")
            self.current_step_var.set("当前步骤：可扫描窗口控件树并保存控件库")
            return

        stdout, stderr = process.communicate()
        error_output = (stderr or stdout or "").strip()
        if not error_output:
            error_output = f"控件库采集器已退出，退出码：{return_code}"

        messagebox.showerror("打开失败", f"控件库采集器未能正常启动：\n{error_output}")
        self._append_log(f"控件库采集器启动失败：{error_output}", tag="error")
        self.status_var.set("状态：控件库采集器启动失败")
        self.current_step_var.set("当前步骤：请检查控件库采集器依赖或启动日志")

    def open_live_detector(self):
        """打开实时控件检测器 - 鼠标悬停时自动捕获控件并匹配控件库"""
        try:
            from control_live_detector import ControlLiveDetectorWindow
            
            # 创建检测器窗口
            detector = ControlLiveDetectorWindow(self.root)
            detector.window.protocol("WM_DELETE_WINDOW", detector.on_close)
            self._append_log("已打开实时控件检测器。", tag="system")
            self.status_var.set("状态：实时控件检测器已启动")
            self.current_step_var.set("当前步骤：将鼠标移到目标软件上，自动捕获并匹配控件")
        except ImportError as exc:
            messagebox.showerror("打开失败", f"缺少实时检测器依赖：\n{exc}", parent=self.root)
            self._append_log(f"打开实时控件检测器失败：{exc}", tag="error")
        except Exception as exc:
            # UI 构建失败（如 TclError）也要明确提示，避免只剩一个空窗口
            messagebox.showerror("打开失败", f"实时检测器初始化失败：\n{exc}", parent=self.root)
            self._append_log(f"实时控件检测器初始化失败：{exc}", tag="error")

    def open_flow_editor(self):
        if getattr(self, "_editor_process", None) is not None and self._editor_process.poll() is None:
            self._append_log("流程链路编辑器已经在运行中，请勿重复打开。", tag="warning")
            return

        if not os.path.exists(FLOW_EDITOR_SCRIPT):
            messagebox.showerror("打开失败", f"未找到流程链路编辑器：\n{FLOW_EDITOR_SCRIPT}")
            return

        try:
            self._save_launcher_state()
        except Exception as exc:
            self._append_log(f"同步当前链路文件到编辑器失败：{exc}", tag="warning")

        try:
            if os.path.exists(FLOW_EDITOR_STARTUP_SIGNAL):
                os.remove(FLOW_EDITOR_STARTUP_SIGNAL)
        except OSError:
            pass

        try:
            self._editor_process = subprocess.Popen(
                [sys.executable, FLOW_EDITOR_SCRIPT, "--startup-ping", FLOW_EDITOR_STARTUP_SIGNAL],
                cwd=BASE_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动流程链路编辑器失败：\n{exc}")
            self._append_log(f"启动流程链路编辑器失败：{exc}", tag="error")
            return

        self.root.after(1600, lambda: self._check_flow_editor_startup(self._editor_process))

    def _check_flow_editor_startup(self, process):
        if os.path.exists(FLOW_EDITOR_STARTUP_SIGNAL):
            self._append_log("已打开流程链路编辑器。", tag="system")
            self.status_var.set("状态：流程链路编辑器已启动")
            self.current_step_var.set("当前步骤：可查看/编辑项目流程链路")
            return

        return_code = process.poll()
        if return_code is None:
            self._append_log("流程链路编辑器进程已启动，但尚未收到窗口就绪信号，请检查是否被其他窗口遮挡。", tag="warning")
            self.status_var.set("状态：流程链路编辑器启动中")
            self.current_step_var.set("当前步骤：等待链路编辑器窗口就绪")
            return

        stdout, stderr = process.communicate()
        error_output = (stderr or stdout or "").strip()
        if not error_output:
            error_output = f"流程链路编辑器已退出，退出码：{return_code}"

        messagebox.showerror("打开失败", f"流程链路编辑器未能正常启动：\n{error_output}")
        self._append_log(f"流程链路编辑器启动失败：{error_output}", tag="error")
        self.status_var.set("状态：流程链路编辑器启动失败")
        self.current_step_var.set("当前步骤：请检查链路编辑器启动日志")

    def open_control_library(self):
        """打开流程链路编辑器并自动进入控件库维护对话框"""
        if getattr(self, "_editor_process", None) is not None and self._editor_process.poll() is None:
            self._append_log("流程链路编辑器/控件库已经在运行中，请勿重复打开。", tag="warning")
            return

        if not os.path.exists(FLOW_EDITOR_SCRIPT):
            messagebox.showerror("打开失败", f"未找到流程链路编辑器：\n{FLOW_EDITOR_SCRIPT}")
            return

        try:
            self._save_launcher_state()
        except Exception as exc:
            self._append_log(f"同步当前链路文件到编辑器失败：{exc}", tag="warning")

        try:
            if os.path.exists(FLOW_EDITOR_STARTUP_SIGNAL):
                os.remove(FLOW_EDITOR_STARTUP_SIGNAL)
        except OSError:
            pass

        try:
            self._editor_process = subprocess.Popen(
                [sys.executable, FLOW_EDITOR_SCRIPT, "--startup-ping", FLOW_EDITOR_STARTUP_SIGNAL, "--open-control-import"],
                cwd=BASE_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动控件库维护失败：\n{exc}")
            self._append_log(f"启动控件库维护失败：{exc}", tag="error")
            return

        self.root.after(1600, lambda: self._check_control_library_startup(self._editor_process))

    def _check_control_library_startup(self, process):
        if os.path.exists(FLOW_EDITOR_STARTUP_SIGNAL):
            self._append_log("已打开控件库维护。", tag="system")
            self.status_var.set("状态：控件库维护已启动")
            self.current_step_var.set("当前步骤：可查看/导入/维护控件库中的控件")
            return

        return_code = process.poll()
        if return_code is None:
            self._append_log("控件库维护进程已启动，正在等待窗口就绪。", tag="system")
            self.status_var.set("状态：控件库维护启动中")
            self.current_step_var.set("当前步骤：等待控件库维护窗口就绪")
            self.root.after(500, lambda: self._check_control_library_startup(process))
            return

        error_output = ""
        if not error_output:
            error_output = f"控件库维护已退出，退出码：{return_code}"

        messagebox.showerror("打开失败", f"控件库维护未能正常启动：\n{error_output}")
        self._append_log(f"控件库维护启动失败：{error_output}", tag="error")
        self.status_var.set("状态：控件库维护启动失败")
        self.current_step_var.set("当前步骤：请检查链路编辑器启动日志")

    def open_relative_region_helper(self):
        try:
            RelativeRegionHelperDialog(self.root, self.theme)
            self._append_log("已打开父窗口相对区域取点助手。", tag="system")
            self.status_var.set("状态：相对区域取点助手已打开")
            self.current_step_var.set("当前步骤：可抓取父窗口和输入框相对区域")
        except Exception as exc:
            messagebox.showerror("打开失败", f"打开相对区域取点助手失败：\n{exc}")
            self._append_log(f"打开相对区域取点助手失败：{exc}", tag="error")
            self.status_var.set("状态：相对区域取点助手启动失败")

    def open_external_capture(self):
        """打开外部控件采集对话框（实验性功能）。"""
        try:
            from tools.external_capture.launcher_panel import ExternalCaptureDialog

            ExternalCaptureDialog(self.root, self.theme, log_callback=self._append_log)
            self._append_log("已打开外部控件采集对话框（实验性功能：uia-peek / axe-windows）。", tag="system")
            self.status_var.set("状态：外部控件采集已打开 (实验性)")
            self.current_step_var.set("当前步骤：实验性功能，建议优先使用原生控件采集和实时监测")
        except ImportError as exc:
            messagebox.showerror("打开失败", f"缺少外部采集模块：\n{exc}", parent=self.root)
            self._append_log(f"打开外部控件采集失败：{exc}", tag="error")
        except Exception as exc:
            messagebox.showerror("打开失败", f"打开外部控件采集失败：\n{exc}", parent=self.root)
            self._append_log(f"打开外部控件采集失败：{exc}", tag="error")

    def run_environment_check(self):
        values = self._get_model_config_values()
        runtime_config = load_flow_runtime_config(self._get_flow_definition_path())
        lines = []
        checks = [
            ("自动化脚本", AUTOMATION_SCRIPT, os.path.exists(AUTOMATION_SCRIPT)),
            ("流程链路文件", self._get_flow_definition_path(), os.path.exists(self._get_flow_definition_path())),
            ("模板制作器", TEMPLATE_BUILDER_SCRIPT, os.path.exists(TEMPLATE_BUILDER_SCRIPT)),
            ("模板根目录", TEMPLATE_ROOT_DIR, os.path.isdir(TEMPLATE_ROOT_DIR)),
            ("投影模板目录", TEMPLATE_DIR, os.path.isdir(TEMPLATE_DIR)),
            ("WT 程序", runtime_config.get("gmExe", ""), bool(runtime_config.get("gmExe", "") and os.path.exists(runtime_config.get("gmExe", "")))),
            ("源数据文件", runtime_config.get("sourceFilePath", ""), bool(runtime_config.get("sourceFilePath", "") and os.path.exists(runtime_config.get("sourceFilePath", "")))),
            ("输出目录", runtime_config.get("outputDir", ""), bool(runtime_config.get("outputDir", "") and os.path.isdir(runtime_config.get("outputDir", "")))),
            ("投影文件", runtime_config.get("projectionFilePath", ""), bool(runtime_config.get("projectionFilePath", "") and os.path.exists(runtime_config.get("projectionFilePath", "")))),
            ("UI-TARS 仓库", values["repoRoot"], bool(values["repoRoot"] and os.path.isdir(values["repoRoot"]))),
            ("UI-TARS 配置", values["configPath"], bool(values["configPath"] and os.path.exists(values["configPath"]))),
        ]
        ok_count = 0
        for label, path, passed in checks:
            lines.append((f"[{'OK' if passed else '缺失'}] {label}: {path or '未配置'}", "success" if passed else "error"))
            if passed:
                ok_count += 1
        python_ok = bool(sys.executable and os.path.exists(sys.executable))
        node_path = shutil.which("node")
        template_modules = ["cv2", "numpy", "PIL"]
        lines.append((f"[{'OK' if python_ok else '缺失'}] Python: {sys.executable}", "success" if python_ok else "error"))
        lines.append((f"[{'OK' if node_path else '缺失'}] Node.js: {node_path or '未检测到 node 命令'}", "success" if node_path else "error"))
        for module_name in template_modules:
            installed = importlib.util.find_spec(module_name) is not None
            package_name = self._map_module_to_package_name(module_name)
            lines.append(
                (
                    f"[{'OK' if installed else '缺失'}] 模板制作依赖 {package_name}: {'已安装' if installed else '未安装'}",
                    "success" if installed else "error",
                )
            )
        self._log_block("运行环境检测", lines)
        dependency_ok_count = sum(1 for module_name in template_modules if importlib.util.find_spec(module_name) is not None)
        total_checks = len(checks) + 2 + len(template_modules)
        self.status_var.set(
            f"状态：环境检测完成（{ok_count + int(python_ok) + int(bool(node_path)) + dependency_ok_count}/{total_checks} 通过）"
        )
        self.current_step_var.set("当前步骤：已完成运行环境检测")

    def run_model_check(self):
        values = self._get_model_config_values()
        config_data, error = load_json_file(values["configPath"])
        lines = [
            (f"仓库路径: {values['repoRoot'] or '未配置'}", "success" if values["repoRoot"] else "error"),
            (f"配置文件: {values['configPath'] or '未配置'}", "success" if values["configPath"] else "error"),
            (f"表单 API Key: {'已配置' if values['apiKey'] else '未配置'}", "success" if values["apiKey"] else "error"),
            (f"表单模型名称: {values['model'] or '未配置'}", "success" if values["model"] else "error"),
            (f"表单 BaseURL: {values['baseURL'] or '未配置'}", "success" if values["baseURL"] else "error"),
            (f"表单 useResponsesApi: {values['useResponsesApi']}", "info"),
        ]
        if error:
            lines.append((error, "warning"))
        else:
            lines.append((f"配置文件模型: {config_data.get('model', '') or '未配置'}", "info"))
            lines.append((f"配置文件 BaseURL: {config_data.get('baseURL', '') or '未配置'}", "info"))
            lines.append((f"配置文件 API Key: {'已配置' if config_data.get('apiKey') else '未配置'}", "info"))
            lines.append((f"配置文件 useResponsesApi: {config_data.get('useResponsesApi')}", "info"))

        repo_ok = bool(values["repoRoot"] and os.path.isdir(values["repoRoot"]))
        config_ok = bool(values["configPath"])
        model_ok = bool(values["model"])
        base_url_ok = bool(values["baseURL"])
        api_key_ok = bool(values["apiKey"])
        self._apply_model_env()
        self._refresh_config_summary()
        self._log_block("模型配置检查", lines)
        if all([repo_ok, config_ok, model_ok, base_url_ok, api_key_ok]):
            self.status_var.set("状态：模型配置检查完成")
            self.current_step_var.set("当前步骤：模型配置可用")
        else:
            self.status_var.set("状态：模型配置不完整")
            self.current_step_var.set("当前步骤：请补齐模型配置")

    def open_ui_tars_config(self):
        config_path = self._get_config_path()
        if not config_path or not os.path.exists(config_path):
            messagebox.showerror("打开失败", f"未找到 UI-TARS 配置文件：\n{config_path}")
            return
        os.startfile(config_path)
        self._append_log(f"已打开 UI-TARS 配置：{config_path}", tag="system")

    def export_flow_excel(self):
        scope_selection = self._prompt_export_flow_scope()
        if not scope_selection:
            return
        default_path = os.path.join(BASE_DIR, os.path.basename(DEFAULT_FLOW_XLSX))
        target_path = filedialog.asksaveasfilename(
            title="导出流程 Excel",
            defaultextension=".xlsx",
            initialfile=os.path.basename(default_path),
            initialdir=os.path.dirname(default_path),
            filetypes=[("Excel 文件", "*.xlsx")],
        )
        if not target_path:
            return
        try:
            export_flow_to_excel(
                self._get_flow_definition_path(),
                target_path,
                selected_step_ids=scope_selection.get("step_ids"),
            )
        except Exception as exc:
            messagebox.showerror("导出失败", f"导出流程 Excel 失败：\n{exc}")
            self._append_log(f"导出流程 Excel 失败：{exc}", tag="error")
            return
        scope_label = scope_selection.get("scope_label", "全部步骤")
        self._append_log(f"已导出流程 Excel：{target_path} | 范围：{scope_label}", tag="success")
        self.status_var.set("状态：流程 Excel 导出完成")
        self.current_step_var.set(f"当前步骤：已导出 {scope_label}")
        if os.path.exists(target_path):
            os.startfile(os.path.dirname(target_path))

    def import_flow_excel(self):
        source_path = filedialog.askopenfilename(
            title="导入流程 Excel",
            initialdir=BASE_DIR,
            filetypes=[("Excel 文件", "*.xlsx")],
        )
        if not source_path:
            return
        import_target = self._prompt_import_flow_target(source_path)
        if not import_target:
            return
        target_path = import_target.get("target_path", "").strip() or self._get_flow_definition_path()
        import_mode = import_target.get("mode", "overwrite")
        try:
            imported_payload = load_flow_payload_from_excel(source_path)
            audit_result = audit_flow_excel_roundtrip(
                source_path,
                candidate_payload=imported_payload,
                candidate_label=os.path.basename(source_path),
            )
            package_result = None
            if import_mode == "append_package":
                package_result = self._append_imported_excel_as_package(
                    source_path,
                    target_path,
                    import_target,
                    imported_payload=imported_payload,
                )
            else:
                imported_payload = validate_imported_flow_payload_or_raise(
                    imported_payload,
                    source_label=f"Excel 导入 {os.path.basename(source_path)}",
                )
                save_json_file(target_path, imported_payload)
            try:
                self._sync_flow_package_registry_for_definition(target_path)
            except Exception as exc:
                self._append_log(f"流程包仓库同步失败：{exc}", tag="warning")
            self.flow_definition_path_var.set(target_path)
            try:
                self._save_launcher_state()
            except Exception as exc:
                self._append_log(f"保存当前链路文件状态失败：{exc}", tag="warning")
            self._refresh_flow_steps()
            self._refresh_config_summary()
        except Exception as exc:
            messagebox.showerror("导入失败", f"导入流程 Excel 失败：\n{exc}")
            self._append_log(f"导入流程 Excel 失败：{exc}", tag="error")
            return
        if import_mode == "overwrite":
            mode_label = "覆盖当前链路"
        elif import_mode == "save_as":
            mode_label = "另存为新链路"
        else:
            mode_label = "追加为流程包"
        detail_suffix = ""
        if import_mode == "append_package" and package_result:
            detail_suffix = f" | 新流程包：{'；'.join(package_result.get('packageLabels', []))}"
        self._append_log(f"已导入流程 Excel：{source_path} -> {target_path} | 方式：{mode_label}{detail_suffix}", tag="success")
        self.status_var.set("状态：流程 Excel 导入完成")
        if import_mode == "append_package":
            self.current_step_var.set("当前步骤：已追加为流程包，可在流程包测试区直接选择并测试")
        else:
            self.current_step_var.set("当前步骤：导入结果已切换到当前链路，可直接测试步骤和流程包")
        self._show_roundtrip_audit_result(audit_result, source_path, target_path, import_mode, mode_label)

    def convert_recorder_script(self):
        script_path = filedialog.askopenfilename(
            title="选择 Recorder Python 脚本",
            initialdir=BASE_DIR,
            filetypes=[("Python 文件", "*.py")],
        )
        if not script_path:
            return
        default_name = os.path.splitext(os.path.basename(script_path))[0] + "_converted_flow.json"
        output_path = filedialog.asksaveasfilename(
            title="保存转换结果",
            initialdir=BASE_DIR,
            initialfile=default_name,
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
        )
        if not output_path:
            return
        try:
            # 自动检测截图目录：脚本同目录下的 screenshots 子目录
            screenshot_dir = None
            script_dir = os.path.dirname(script_path)
            candidate_screenshot_dirs = [
                os.path.join(script_dir, "screenshots"),
                os.path.join(script_dir, "screens"),
                os.path.join(BASE_DIR, "debug_screenshots"),
            ]
            for cand in candidate_screenshot_dirs:
                if os.path.isdir(cand):
                    screenshot_dir = cand
                    break
            payload = convert_recorder_script_to_flow(script_path, output_path, screenshot_dir=screenshot_dir)
        except Exception as exc:
            messagebox.showerror("转换失败", f"Recorder 脚本转换失败：\n{exc}")
            self._append_log(f"Recorder 脚本转换失败：{exc}", tag="error")
            return
        meta = payload.get("conversionMeta", {})
        log_msg = (
            "已完成 Recorder 转换："
            f"steps={meta.get('totalSteps', 0)}, "
            f"action={meta.get('actionSteps', 0)}, "
            f"placeholder={meta.get('placeholderSteps', 0)}"
        )
        screenshot_count = meta.get("screenshotLinkedCount", 0)
        if screenshot_count:
            log_msg += f", screenshots={screenshot_count}"
        elif screenshot_dir:
            log_msg += ", screenshots=0"
        log_msg += f", output={output_path}"
        self._append_log(log_msg, tag="success")
        self.status_var.set("状态：Recorder 转换完成")
        self.current_step_var.set("当前步骤：已生成 action 流程骨架")
        if os.path.exists(output_path):
            os.startfile(os.path.dirname(output_path))

    def open_log_file(self):
        if not os.path.exists(LOG_FILE):
            messagebox.showinfo("提示", "当前还没有生成运行日志。")
            return
        os.startfile(LOG_FILE)

    def open_pywinauto_recorder(self):
        recorder_dir = DEFAULT_RECORDER_DIR
        recorder_script = os.path.join(recorder_dir, DEFAULT_RECORDER_SCRIPT)

        if not os.path.exists(recorder_dir):
            messagebox.showerror("打开失败", f"未找到 pywinauto recorder 目录：\n{recorder_dir}")
            return
        if not os.path.exists(recorder_script):
            messagebox.showerror("打开失败", f"未找到 pywinauto recorder 脚本：\n{recorder_script}")
            return

        try:
            launch_script = (
                "@echo off\n"
                f'cd /d "{recorder_dir}"\n'
                f'python "{DEFAULT_RECORDER_SCRIPT}"\n'
            )
            with open(RECORDER_LAUNCH_CMD_FILE, "w", encoding="utf-8", newline="\r\n") as file_obj:
                file_obj.write(launch_script)
            subprocess.Popen(
                [
                    "cmd.exe",
                    "/k",
                    RECORDER_LAUNCH_CMD_FILE,
                ],
                cwd=recorder_dir,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
            self._append_log(
                "已按两条命令启动 pywinauto recorder：\n"
                f'1. cd /d "{recorder_dir}"\n'
                f'2. python "{DEFAULT_RECORDER_SCRIPT}"',
                tag="system",
            )
            self.status_var.set("状态：pywinauto recorder 已启动")
            self.current_step_var.set("当前步骤：录制器窗口已打开")
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动 pywinauto recorder 失败：\n{exc}")
            self._append_log(f"启动 pywinauto recorder 失败：{exc}", tag="error")

    def sync_recorded_scripts(self):
        """把 pywinauto recorder 输出目录里“这次新录的那一个”增量同步到项目。

        采用清单增量：已同步过的源文件即使在项目里被改名/删除，也不会被搬回。
        """
        try:
            from tools import sync_recorded as _sync_mod
            importlib.reload(_sync_mod)
            result = _sync_mod.sync(mode="latest")
        except Exception as exc:
            messagebox.showerror("同步失败", f"同步录制脚本时出错：\n{exc}")
            self._append_log(f"同步录制脚本失败：{exc}", tag="error")
            return

        log_text = result.get("log_text") or result.get("message", "")
        if not result.get("ok", True):
            self._append_log(f"同步录制脚本：{result.get('message', '')}", tag="error")
            messagebox.showwarning("同步录制脚本", result.get("message", "同步未完成"))
            return

        copied = result.get("copied", [])
        self._append_log("同步录制脚本结果：\n" + log_text, tag="success" if copied else "system")
        self.status_var.set("状态：录制脚本已同步" if copied else "状态：无新录制脚本")

        if copied:
            newest = copied[-1]
            self.current_step_var.set(f"当前步骤：已同步 {newest}")
            if messagebox.askyesno(
                "同步完成",
                f"已同步最新录制脚本：\n{newest}\n\n是否打开收录目录查看？",
            ):
                dest_dir = result.get("dest", "")
                if dest_dir and os.path.isdir(dest_dir):
                    os.startfile(dest_dir)
        else:
            self.current_step_var.set("当前步骤：无新录制脚本可同步")
            messagebox.showinfo("同步录制脚本", result.get("message", "没有新文件需要同步。"))

    def open_template_root_dir(self):
        os.makedirs(TEMPLATE_ROOT_DIR, exist_ok=True)
        os.startfile(TEMPLATE_ROOT_DIR)

    def open_control_map_dir(self):
        os.makedirs(CONTROL_MAP_DIR, exist_ok=True)
        os.startfile(CONTROL_MAP_DIR)

    def open_template_dir(self):
        os.makedirs(TEMPLATE_DIR, exist_ok=True)
        os.startfile(TEMPLATE_DIR)

    def package_debug_logs(self):
        os.makedirs(LOG_ARCHIVE_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = os.path.join(LOG_ARCHIVE_DIR, f"gm_debug_bundle_{timestamp}.zip")
        candidates = []
        for file_name in os.listdir(BASE_DIR):
            lower_name = file_name.lower()
            if lower_name.endswith((".log", ".xml", ".html", ".json")) and (
                lower_name.startswith("ui_tars_")
                or lower_name in {"wt_automation.log", "gm_automation.log", "output.xml", "log.html", "report.html", "templates_index.json"}
            ):
                candidates.append(os.path.join(BASE_DIR, file_name))
        root_index = os.path.join(TEMPLATE_ROOT_DIR, "templates_index.json")
        if os.path.exists(root_index):
            candidates.append(root_index)
        if os.path.exists(LAST_RUN_REPORT_FILE):
            candidates.append(LAST_RUN_REPORT_FILE)
        if os.path.isdir(RUN_REPORT_DIR):
            for file_name in os.listdir(RUN_REPORT_DIR):
                if file_name.lower().endswith(".json"):
                    candidates.append(os.path.join(RUN_REPORT_DIR, file_name))
        config_path = self._get_config_path()
        if config_path and os.path.exists(config_path):
            candidates.append(config_path)

        existing_files = [path for path in candidates if os.path.isfile(path)]
        if not existing_files:
            messagebox.showinfo("提示", "当前没有可打包的调试日志文件。")
            return

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zip_obj:
            for file_path in existing_files:
                try:
                    arcname = os.path.relpath(file_path, BASE_DIR)
                except ValueError:
                    arcname = os.path.basename(file_path)
                zip_obj.write(file_path, arcname)

        self._append_log(f"已完成日志打包：{archive_path}", tag="success")
        self.status_var.set("状态：日志打包完成")
        self.current_step_var.set("当前步骤：调试归档已生成")
        os.startfile(LOG_ARCHIVE_DIR)

    def _on_close(self):
        try:
            self._save_launcher_state()
        except Exception:
            pass
        if self.process and self.process.poll() is None:
            should_close = messagebox.askyesno(
                "退出总控台",
                "当前自动化流程仍在运行中，确定退出并停止流程吗？",
            )
            if not should_close:
                return
            try:
                self.process.terminate()
            except OSError:
                pass
        self.root.destroy()


def main():
    wt_dpi.enable_process_dpi_awareness()
    root = tk.Tk()
    wt_dpi.compute_scale(root)
    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
