# encoding: utf-8

import ctypes
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
from wt_flow_validation import validate_flow_definition

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_GM_EXE = ""
DEFAULT_SOURCE_FILE_PATH = ""
DEFAULT_OUTPUT_DIR = ""
DEFAULT_PROJECTION_FILE_PATH = ""

GM_EXE = DEFAULT_GM_EXE
SOURCE_FILE_PATH = DEFAULT_SOURCE_FILE_PATH
OUTPUT_DIR = DEFAULT_OUTPUT_DIR
PROJECTION_FILE_PATH = DEFAULT_PROJECTION_FILE_PATH

MAIN_WINDOW_TITLE_RE = re.compile(r"Global Mapper v22\.1 .*中文注册版")
MAIN_WINDOW_UIPATH = u"Global Mapper v22.1 (b082421) [64-bit] [+OTF] [+LIDAR] - 中文注册版||Window"

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
_FLOW_LOCATOR_CONFIGURED = False
_FLOW_EXECUTOR_CONFIGURED = False
_WT_BUSINESS_STEPS_CONFIGURED = False
_WT_PROJECTION_HELPERS_CONFIGURED = False
_WT_WINDOW_HELPERS_CONFIGURED = False
_WT_RUN_REPORTING_CONFIGURED = False

pyautogui.PAUSE = 0.15
pyautogui.FAILSAFE = True



StageExecutionError = wt_projection_helpers.StageExecutionError


class MonitorWindow:
	def __init__(self):
		wt_dpi.enable_process_dpi_awareness()
		self.root = tk.Tk()
		wt_dpi.compute_scale(self.root)
		self.root.title("WT自动化流程监视器")
		# 把窗口设为较小尺寸，并动态放在屏幕右下角，尽量不挡住 WT 目标窗口
		window_width = 320
		window_height = 190
		margin = 24
		screen_width = self.root.winfo_screenwidth()
		screen_height = self.root.winfo_screenheight()
		# 尺寸按 DPI 缩放，并用缩放后的尺寸计算右下角位置，避免窗口跑出屏幕右侧
		sw = wt_dpi.scale(window_width)
		sh = wt_dpi.scale(window_height)
		pos_x = max(0, screen_width - sw - margin)
		pos_y = max(0, screen_height - sh - 80)
		wt_dpi.raw_geometry(self.root, f"{sw}x{sh}+{pos_x}+{pos_y}")
		
		# 默认不置顶，避免遮挡目标父窗口；仅在流程结束需要提示结果时再抬到最前。
		self._set_topmost(False)
		self.root.attributes("-alpha", 0.92)
		try:
			self.root.after(200, self._send_to_back)
		except Exception:
			pass
		
		# 创建文本框
		self.text_widget = tk.Text(self.root, wrap=tk.WORD, state=tk.DISABLED, font=("Arial", 9))
		self.text_widget.pack(expand=True, fill=tk.BOTH, padx=5, pady=5)
		
		# 创建滚动条
		self.scrollbar = tk.Scrollbar(self.text_widget)
		self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
		self.text_widget.config(yscrollcommand=self.scrollbar.set)
		self.scrollbar.config(command=self.text_widget.yview)
		
		# 标签显示当前状态
		self.status_label = tk.Label(self.root, text="状态：准备就绪", font=("Arial", 10))
		self.status_label.pack(pady=3)

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

	def log(self, message):
		self.text_widget.config(state=tk.NORMAL)
		self.text_widget.insert(tk.END, message + "\n")
		self.text_widget.see(tk.END)  # 自动滚动到末尾
		self.text_widget.config(state=tk.DISABLED)
		self.root.update()

	def update_status(self, status):
		self.status_label.config(text=f"状态：{status}")
		self.root.update()

	def set_success(self):
		self._bring_to_front_for_notice()
		self.status_label.config(text="状态：流程完成！", fg="green")
		self.root.bell()
		self.root.update()

	def set_error(self):
		self._bring_to_front_for_notice()
		self.status_label.config(text="状态：流程失败！", fg="red")
		self.root.bell()
		self.root.update()


def log_step(step_name):
	global monitor_window
	timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
	log_line = f"[{timestamp}] {step_name}"
	print(log_line, end="\n")
	
	with open(LOG_FILE, "a", encoding="utf-8") as f:
		f.write(log_line + "\n")
	
	if monitor_window:
		monitor_window.log(log_line)
		monitor_window.update_status(step_name)


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
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
SW_RESTORE = 9
SW_MAXIMIZE = 3


def _force_utf8_stdio():
	if sys.platform == "win32":
		sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
		sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


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
		return {
			"runtimeConfig": runtime_config,
			"flowPackages": [item for item in flow_packages if isinstance(item, dict)],
			"steps": [item for item in steps if isinstance(item, dict)],
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

	target_payload = _normalize_payload(_read_payload(FLOW_DEFINITION_FILE, "读取流程链路定义失败，跳过运行时控件匹配"))
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

	validation_errors = validate_flow_definition(merged_payload)
	if validation_errors:
		raise RuntimeError(
			"流程链路配置校验失败: "
			+ " | ".join(str(item) for item in validation_errors[:8])
		)
	return merged_payload


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

	return {
		"gmExe": _pick_value("gmExe", "GM_EXE", DEFAULT_GM_EXE),
		"sourceFilePath": _pick_value("sourceFilePath", "SOURCE_FILE_PATH", DEFAULT_SOURCE_FILE_PATH),
		"outputDir": _pick_value("outputDir", "OUTPUT_DIR", DEFAULT_OUTPUT_DIR),
		"projectionFilePath": _pick_value("projectionFilePath", "PROJECTION_FILE_PATH", DEFAULT_PROJECTION_FILE_PATH),
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


def _get_flow_locator():
	global _FLOW_LOCATOR_CONFIGURED
	if not _FLOW_LOCATOR_CONFIGURED:
		wt_flow_locator.configure_flow_locator(get_step_definition=_get_flow_step, log_step=log_step)
		_FLOW_LOCATOR_CONFIGURED = True
	return wt_flow_locator


def _get_flow_executor():
	global _FLOW_EXECUTOR_CONFIGURED
	if not _FLOW_EXECUTOR_CONFIGURED:
		wt_flow_executor.configure_flow_executor(
			get_step_definition=_get_flow_step,
			get_flow_package=_get_flow_package,
			get_step_params=_get_step_params,
			resolve_dynamic_value=_resolve_dynamic_value,
			log_step=log_step,
			click_flow_control=_click_flow_control,
			click_relative_region=_click_relative_region,
		click_relative_anchor=_click_relative_anchor,
			focus_flow_control=_focus_flow_control,
			type_text_into_flow_control=_type_text_into_flow_control,
			type_text_into_relative_region=_type_text_into_relative_region,
			select_dropdown_item_runtime=_select_dropdown_item_runtime,
			drag_between_flow_controls=_drag_between_flow_controls,
			mouse_wheel_on_flow_control=_mouse_wheel_on_flow_control,
			wait_for_flow_control_condition=_wait_for_flow_control_condition,
			menu_select_flow=_menu_select_flow,
			locate_template_center_by_path=_locate_template_center_by_path,
			report_step_result=_record_step_result,
			run_ai_intervention_after_failure=_run_ai_intervention_after_failure,
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


def _record_step_result(run_report, step_id, step_name, status, action_type="", strategy="", elapsed=0.0, error="", extra=None):
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
			return str(runtime_config.get(expression.split(".", 1)[1], match_obj.group(0)))
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
		return match_obj.group(0)

	return re.sub(r"\$\{([^{}]+)\}", _replace, value)


def _normalize_control_type_name(control_type, localized_control_type=""):
	return _get_flow_locator().normalize_control_type_name(control_type, localized_control_type)


def _strip_wrapping_quotes(text):
	return _get_flow_locator().strip_wrapping_quotes(text)


def _normalize_match_text(value):
	return _get_flow_locator().normalize_match_text(value)


def _build_locator_text(method, values):
	return _get_flow_locator().build_locator_text(method, values)


def _build_common_locator_candidates(control_definition):
	return _get_flow_locator().build_common_locator_candidates(control_definition)


def _get_control_definition_match_score(wrapper, control_definition):
	return _get_flow_locator().get_control_definition_match_score(wrapper, control_definition)


def _split_locator_parts(text):
	return _get_flow_locator().split_locator_parts(text)


def _parse_aux_check_line(line):
	return _get_flow_locator().parse_aux_check_line(line)


def _parse_window_title_candidates(window_title):
	return _get_flow_locator().parse_window_title_candidates(window_title)


def _get_wrapper_text(wrapper):
	return _get_flow_locator().get_wrapper_text(wrapper)


def _get_wrapper_class_name(wrapper):
	return _get_flow_locator().get_wrapper_class_name(wrapper)


def _get_wrapper_control_type(wrapper):
	return _get_flow_locator().get_wrapper_control_type(wrapper)


def _get_wrapper_localized_control_type(wrapper):
	return _get_flow_locator().get_wrapper_localized_control_type(wrapper)


def _get_wrapper_automation_id(wrapper):
	return _get_flow_locator().get_wrapper_automation_id(wrapper)


def _get_wrapper_framework_id(wrapper):
	return _get_flow_locator().get_wrapper_framework_id(wrapper)


def _get_wrapper_help_text(wrapper):
	return _get_flow_locator().get_wrapper_help_text(wrapper)


def _get_wrapper_process_id(wrapper):
	return _get_flow_locator().get_wrapper_process_id(wrapper)


def _get_wrapper_handle_text(wrapper):
	return _get_flow_locator().get_wrapper_handle_text(wrapper)


def _get_wrapper_is_enabled(wrapper):
	return _get_flow_locator().get_wrapper_is_enabled(wrapper)


def _get_wrapper_is_offscreen(wrapper):
	return _get_flow_locator().get_wrapper_is_offscreen(wrapper)


def _get_wrapper_is_keyboard_focusable(wrapper):
	return _get_flow_locator().get_wrapper_is_keyboard_focusable(wrapper)


def _get_wrapper_has_keyboard_focus(wrapper):
	return _get_flow_locator().get_wrapper_has_keyboard_focus(wrapper)


def _get_wrapper_parent_signatures(wrapper, depth=6):
	return _get_flow_locator().get_wrapper_parent_signatures(wrapper, depth=depth)


def _get_wrapper_child_signatures(wrapper, limit=12):
	return _get_flow_locator().get_wrapper_child_signatures(wrapper, limit=limit)


def _value_matches(actual, expected, regex=False):
	return _get_flow_locator().value_matches(actual, expected, regex=regex)


def _wrapper_matches_locator(wrapper, target_method, target_value):
	return _get_flow_locator().wrapper_matches_locator(wrapper, target_method, target_value)


def _wrapper_matches_control_definition(wrapper, control_definition):
	return _get_flow_locator().wrapper_matches_control_definition(wrapper, control_definition)


def _score_control_match(wrapper, control_definition):
	return _get_flow_locator().score_control_match(wrapper, control_definition)


def _get_control_process_candidates(control_definition):
	return _get_flow_locator().get_control_process_candidates(control_definition)


def _get_foreground_window_handle():
	return _get_flow_locator().get_foreground_window_handle()


def _iter_flow_search_windows(step_definition, window_title_hint="", control_definition=None):
	return _get_flow_locator().iter_flow_search_windows(
		step_definition,
		window_title_hint=window_title_hint,
		control_definition=control_definition,
	)


def _find_flow_control(step_id, control_id=None, timeout_seconds=3, window_title_hint=""):
	return _get_flow_locator().find_flow_control(
		step_id,
		control_id=control_id,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
	)


def _wait_for_flow_control_condition(
	step_id,
	control_id,
	condition="exists",
	timeout_seconds=3,
	window_title_hint="",
	poll_interval_seconds=0.4,
):
	return _get_flow_locator().wait_for_flow_control_condition(
		step_id,
		control_id,
		condition=condition,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
		poll_interval_seconds=poll_interval_seconds,
	)


def _get_flow_control_definition(step_id, control_id):
	return _get_flow_locator().get_flow_control_definition(step_id, control_id)


def _click_flow_control(step_id, control_id, timeout_seconds=3, window_title_hint="", click_kind="left"):
	return _get_flow_locator().click_flow_control(
		step_id,
		control_id,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
		click_kind=click_kind,
	)


def _click_relative_anchor(
    step_id,
    anchor_control_id,
    offset,
    timeout_seconds=3,
    window_title_hint="",
    click_kind="single",
):
    return _get_flow_locator().click_relative_anchor(
        step_id,
        anchor_control_id,
        offset,
        timeout_seconds=timeout_seconds,
        window_title_hint=window_title_hint,
        click_kind=click_kind,
    )


def _click_relative_region(
	step_definition,
	parent_window,
	relative_region,
	timeout_seconds=3,
	window_title_hint="",
	click_kind="single",
):
	return _get_flow_locator().click_relative_region(
		step_definition,
		parent_window,
		relative_region,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
		click_kind=click_kind,
	)


def _click_menu_candidate_by_text(step_id, control_id):
	return _get_flow_locator().click_menu_candidate_by_text(step_id, control_id)


def _focus_flow_control(step_id, control_id, timeout_seconds=3, window_title_hint=""):
	return _get_flow_locator().focus_flow_control(
		step_id,
		control_id,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
	)


def _type_text_into_wrapper(control, text):
	return _get_flow_locator().type_text_into_wrapper(control, text)


def _type_text_into_flow_control(step_id, control_id, text, timeout_seconds=3, window_title_hint=""):
	return _get_flow_locator().type_text_into_flow_control(
		step_id,
		control_id,
		text,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
	)


def _type_text_into_relative_region(
	step_definition,
	parent_window,
	relative_region,
	text,
	timeout_seconds=3,
	window_title_hint="",
	post_input_keys="",
):
	return _get_flow_locator().type_text_into_relative_region(
		step_definition,
		parent_window,
		relative_region,
		text,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
		post_input_keys=post_input_keys,
	)


def _select_dropdown_item_runtime(step_id, control_id, timeout_seconds=3, window_title_hint="", target_option=""):
	return _get_flow_locator().select_dropdown_item_runtime(
		step_id,
		control_id,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
		target_option=target_option,
	)


def _menu_select_flow(step_id, menu_path, timeout_seconds=3, window_title_hint=""):
	return _get_flow_locator().menu_select_flow(
		step_id,
		menu_path,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
	)


def _get_wrapper_center(control):
	return _get_flow_locator().get_wrapper_center(control)


def _drag_between_flow_controls(
	step_id,
	source_control_id,
	target_control_id,
	timeout_seconds=3,
	window_title_hint="",
	duration_seconds=0.4,
):
	return _get_flow_locator().drag_between_flow_controls(
		step_id,
		source_control_id,
		target_control_id,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
		duration_seconds=duration_seconds,
	)


def _mouse_wheel_on_flow_control(
	step_id,
	control_id="",
	delta=0,
	timeout_seconds=3,
	window_title_hint="",
):
	return _get_flow_locator().mouse_wheel_on_flow_control(
		step_id,
		control_id=control_id,
		delta=delta,
		timeout_seconds=timeout_seconds,
		window_title_hint=window_title_hint,
	)


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


def _find_main_windows():
	return _get_wt_window_helpers().find_main_windows(MAIN_WINDOW_TITLE_RE)


def _choose_best_window(windows):
	return _get_wt_window_helpers().choose_best_window(windows)


def _wait_until_main_window_ready(timeout_seconds=30):
	return _get_wt_window_helpers().wait_until_main_window_ready(
		MAIN_WINDOW_TITLE_RE,
		timeout_seconds=timeout_seconds,
	)


def activate_and_maximize_main_window(timeout_seconds=60):
	return _get_wt_window_helpers().activate_and_maximize_main_window(
		MAIN_WINDOW_TITLE_RE,
		timeout_seconds=timeout_seconds,
	)


def _run_ui_tars(prompt, step_name="AI介入操作"):
	return _get_wt_projection_helpers().run_ui_tars(prompt, step_name=step_name)


def _build_projection_ai_prompt(start_stage):
	return _get_wt_projection_helpers().build_projection_ai_prompt(start_stage)


def _build_dwg_projection_confirmation_prompt():
	return _get_wt_projection_helpers().build_dwg_projection_confirmation_prompt()


def _build_dwg_projection_ai_prompt(start_stage):
	return _get_wt_projection_helpers().build_dwg_projection_ai_prompt(start_stage)


def _get_template_path(template_key):
	return _get_wt_projection_helpers().get_template_path(template_key)


def _locate_template_center(template_key, timeout_seconds=8, confidence=0.8):
	return _get_wt_projection_helpers().locate_template_center(
		template_key,
		timeout_seconds=timeout_seconds,
		confidence=confidence,
	)


def _locate_template_center_by_path(template_path, timeout_seconds=8, confidence=0.8):
	return _get_wt_projection_helpers().locate_template_center_by_path(
		template_path,
		timeout_seconds=timeout_seconds,
		confidence=confidence,
	)


def _capture_debug_screenshot(tag):
	return _get_wt_projection_helpers().capture_debug_screenshot(tag)


def _log_projection_debug_context(stage):
	return _get_wt_projection_helpers().log_projection_debug_context(stage)


def _click_template(template_key, timeout_seconds=8, confidence=0.8):
	return _get_wt_projection_helpers().click_template(
		template_key,
		timeout_seconds=timeout_seconds,
		confidence=confidence,
	)


def _try_click_layer_tree_expand_icon(timeout_seconds=4, confidence=0.8):
	return _get_wt_projection_helpers().try_click_layer_tree_expand_icon(
		timeout_seconds=timeout_seconds,
		confidence=confidence,
	)


def _find_config_window(timeout_seconds=2):
	return _get_wt_projection_helpers().find_config_window(timeout_seconds=timeout_seconds)


def _config_window_is_open():
	return _get_wt_projection_helpers().config_window_is_open()


def _click_button_in_config_window(button_title, timeout_seconds=6, flow_control_id=None):
	return _get_wt_projection_helpers().click_button_in_config_window(
		button_title,
		timeout_seconds=timeout_seconds,
		flow_control_id=flow_control_id,
	)


def _configure_projection_by_image(start_stage="config_button"):
	return _get_wt_projection_helpers().configure_projection_by_image(start_stage=start_stage)


def _click_unknown_projection_if_present(timeout_seconds=8):
	return _get_wt_window_helpers().click_unknown_projection_if_present(timeout_seconds=timeout_seconds)


def _find_open_dialog(timeout_seconds=5):
	return _get_wt_window_helpers().find_open_dialog(timeout_seconds=timeout_seconds)


def _confirm_open_file_dialog(timeout_seconds=5):
	return _get_wt_window_helpers().confirm_open_file_dialog(timeout_seconds=timeout_seconds)


def _type_path_into_open_dialog(file_path, step_id="open_source_dwg", control_id="open_dialog_filename"):
	return _get_wt_window_helpers().type_path_into_open_dialog(
		file_path,
		step_id=step_id,
		control_id=control_id,
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


def _ensure_output_dir_exists():
	if not os.path.isdir(OUTPUT_DIR):
		raise NotADirectoryError(f"Output dir not found: {OUTPUT_DIR}")


def _sleep_seconds(value, default_seconds=0.0):
	return _get_flow_executor().sleep_seconds(value, default_seconds=default_seconds)


def _run_action_step(step_id, context):
	return _get_flow_executor().run_action_step(step_id, context)


def _resolve_fallback_template_path(template_path):
	return _get_flow_executor().resolve_fallback_template_path(template_path)


def _apply_position_offset(center_point, action_config):
	return _get_flow_executor().apply_position_offset(center_point, action_config)


def _run_action_step_with_template_fallback(step_id, context, original_error):
	return _get_flow_executor().run_action_step_with_template_fallback(step_id, context, original_error)


def _run_flow_ref_step(step_id, execution_plan_map, context, skip_setup=False):
	return _get_flow_executor().run_flow_ref_step(
		step_id,
		execution_plan_map,
		context,
		skip_setup=skip_setup,
	)


def _is_setup_step(step_id):
	return _get_flow_executor().is_setup_step(step_id)


def _execute_step_by_id(step_id, execution_plan_map, context, skip_setup=False):
	return _get_flow_executor().execute_step_by_id(
		step_id,
		execution_plan_map,
		context,
		skip_setup=skip_setup,
	)


def _get_step_registry():
	return _get_wt_business_steps().get_step_registry()


def _get_step_registry_map():
	return _get_wt_business_steps().get_step_registry_map()


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
	if steps_arg:
		raw_items = [item.strip() for item in str(steps_arg).split(",") if item.strip()]
		normalized = []
		for item in raw_items:
			step_id = _normalize_step_id(item)
			if step_id and step_id in step_ids and step_id not in normalized:
				normalized.append(step_id)
		return normalized
	if from_step:
		from_id = _normalize_step_id(from_step)
		if from_id in step_ids:
			start_index = step_ids.index(from_id)
		else:
			start_index = 0
		end_index = len(step_ids)
		if to_step:
			to_id = _normalize_step_id(to_step)
			if to_id in step_ids:
				end_index = step_ids.index(to_id) + 1
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


def run_automation(steps_arg=None, from_step=None, to_step=None, skip_setup=False):
	global running
	try:
		_force_utf8_stdio()
		_refresh_flow_caches()
		_apply_runtime_config()
		running = True
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
		log_step(f"执行步骤列表: {steps_to_run}")
		log_step(
			f"当前运行参数: gmExe={GM_EXE}, sourceFilePath={SOURCE_FILE_PATH}, outputDir={OUTPUT_DIR}, projectionFilePath={PROJECTION_FILE_PATH}"
		)

		if not skip_setup:
			_validate_runtime_items(steps_to_run)

		context = _build_execution_context()
		context["run_report"] = _get_wt_run_reporting().start_run_report(
			steps_to_run,
			context.get("runtime_config", {}),
		)
		context["runId"] = context["run_report"].get("runId", "") if isinstance(context.get("run_report"), dict) else ""
		execution_plan_map = {item["id"]: item for item in execution_plan}
		for item in execution_plan:
			step_id = item["id"]
			if step_id not in steps_to_run:
				continue
			_execute_step_by_id(step_id, execution_plan_map, context, skip_setup=skip_setup)

		log_step("WT自动化流程完成")
		_get_wt_run_reporting().finalize_run_report(context.get("run_report"), "success")
		if monitor_window:
			monitor_window.set_success()
	except Exception as e:
		error_msg = f"错误：{str(e)}"
		log_step(error_msg)
		try:
			_get_wt_run_reporting().finalize_run_report(
				context.get("run_report") if "context" in locals() else None,
				"failed",
				error=str(e),
			)
		except Exception:
			pass
		if monitor_window:
			monitor_window.log(error_msg)
			monitor_window.set_error()
		raise
	finally:
		running = False


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
	args = parser.parse_args()

	steps_arg = args.steps.strip() or None
	from_step = args.from_step.strip() or None
	to_step = args.to_step.strip() or None

	if args.no_monitor:
		run_automation(steps_arg=steps_arg, from_step=from_step, to_step=to_step, skip_setup=bool(args.skip_setup))
		return

	monitor_window = MonitorWindow()
	automation_thread = Thread(
		target=lambda: run_automation(
			steps_arg=steps_arg,
			from_step=from_step,
			to_step=to_step,
			skip_setup=bool(args.skip_setup),
		),
		daemon=True,
	)
	automation_thread.start()
	monitor_window.root.mainloop()


if __name__ == "__main__":
	main()
