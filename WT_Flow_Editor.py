# encoding: utf-8

import argparse
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from functools import lru_cache
import wt_dpi
import wt_theme
import wt_ui_state
import wt_wheel_router
from tkinter import filedialog, messagebox, scrolledtext, ttk

from flow_recorder_converter import convert_recorder_script_to_flow
from wt_action_schema import (
    ALLOWED_CONTINUE_WHEN_CONDITIONS,
    ALLOWED_ON_ERROR_MODES,
    ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS,
    ALLOWED_RELATIVE_REGION_ANCHORS,
    build_action_schema_hint,
    get_action_names,
    get_action_schema,
)
from wt_action_defaults import build_action_default_config
from wt_flow_validation import validate_flow_definition, validate_step_definition
import wt_flow_editor_utils
from wt_flow_editor_utils import build_source_info, mark_source_edited, mark_source_deleted
try:
    import image_template_index
except Exception:
    image_template_index = None

from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig
from WT_AUTOMATION_Agent.control_index import build_context_for_agent, build_index_from_json
from WT_AUTOMATION_Agent.skill_bridge import load_all_skills_text


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FLOW_DEFINITION_FILE = os.path.join(BASE_DIR, "workspace", "flow_definition.json")
FLOW_PACKAGE_STORE_DIR = os.path.join(BASE_DIR, "flow_packages")
FLOW_PACKAGE_REGISTRY_FILE = os.path.join(FLOW_PACKAGE_STORE_DIR, "flow_package_registry.json")
LAUNCHER_STATE_FILE = os.path.join(BASE_DIR, "launcher_state.json")
EDITOR_STATE_FILE = os.path.join(BASE_DIR, "workspace", "editor_state.json")
TEMPLATE_ROOT_DIR = os.path.join(BASE_DIR, "image_templates")
TEMPLATE_BUILDER_SCRIPT = os.path.join(BASE_DIR, "build_image_template_library.py")
CONTROL_MAP_DIR = os.path.join(BASE_DIR, "control_maps")
CONTROL_MAP_BUILDER_SCRIPT = os.path.join(BASE_DIR, "build_control_map_library.py")
MASTER_CONTROL_FILE = os.path.join(CONTROL_MAP_DIR, "standard", "总控件信息.json")
CATALOG_FILE = os.path.join(CONTROL_MAP_DIR, "standard", "standard_control_catalog.json")
RECORDER_CONVERTED_DIR = os.path.join(FLOW_PACKAGE_STORE_DIR, "converted_recorder_flows")
REFERENCE_PROJECT_DIR = r"D:\My_RF_Project\2026-06-25-风资源软件流程自动化\风资源软件流程自动化"


def _safe_destroy(window):
    """安全销毁 tkinter 窗口，避免因窗口已销毁而抛出 TclError。"""
    try:
        if window and window.winfo_exists():
            window.destroy()
    except Exception:
        pass


# ── 通用 Tkinter UI 构建辅助（供各 Dialog 类与主编辑器复用）──

EDITOR_THEME = wt_theme.get_palette()


def _make_dialog_window(parent, title, width=0, height=0, min_width=0, min_height=0, on_close=None, grab=True):
    """创建标准模态对话框窗口并返回 Toplevel。

    统一处理标题、transient、grab、DPI 缩放尺寸、最小尺寸与关闭协议。
    """
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent)
    window.configure(bg=EDITOR_THEME["bg"])
    if grab:
        window.grab_set()
    if width and height:
        wt_dpi.geometry(window, width, height)
    if min_width and min_height:
        window.minsize(wt_dpi.scale(min_width), wt_dpi.scale(min_height))
    if callable(on_close):
        window.protocol("WM_DELETE_WINDOW", on_close)
    return window


def _set_text(widget, value):
    """清空并写入 Text 控件。"""
    widget.delete("1.0", tk.END)
    widget.insert("1.0", value or "")


def _get_text(widget):
    """读取 Text 控件全文并去除首尾空白。"""
    return widget.get("1.0", tk.END).strip()


def _grid_label_entry(parent, label, variable, row, column, colspan=1):
    """在网格中构建一行 label + entry。"""
    tk.Label(parent, text=label, font=("Microsoft YaHei UI", 9)).grid(row=row, column=column, sticky="w", pady=4)
    entry = tk.Entry(
        parent,
        textvariable=variable,
        font=("Microsoft YaHei UI", 9),
        relief=tk.FLAT,
        bd=0,
        highlightthickness=1,
        highlightbackground=EDITOR_THEME["border"],
    )
    entry.grid(
        row=row, column=column + 1, columnspan=colspan, sticky="ew", padx=(8, 12), pady=4, ipady=2
    )


def _make_treeview(parent, columns, headings, widths=None, selectmode="browse", height=None, show="headings"):
    """构建带纵向滚动条的 Treeview，返回 (tree, scrollbar)。

    默认按 headings 显示列标题，滚动条已与树绑定。
    """
    tree = ttk.Treeview(parent, columns=columns, show=show, selectmode=selectmode, height=height)
    for col in columns:
        tree.heading(col, text=headings.get(col, col))
        if widths and col in widths:
            tree.column(col, width=widths[col])
    scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scrollbar.set)
    return tree, scrollbar


def _populate_treeview(tree, rows):
    """清空并填充 Treeview（每行一个 values 元组/列表）。"""
    tree.delete(*tree.get_children())
    for values in rows:
        tree.insert("", tk.END, values=values)


def _make_button_row(parent, buttons, padx=8, pady=8):
    """构建一行按钮并返回其 Frame。

    buttons: [(text, command, options)]；options 中可含 'side'（默认 left）
    与 'pack_padx'（按钮间距，默认 6），其余参数透传给 tk.Button。
    padx/pady 作为 pack 的外部间距传入（接受标量或二元组）。
    """
    frame = tk.Frame(parent, bg=parent.cget("bg") if hasattr(parent, "cget") else EDITOR_THEME["bg"])
    frame.pack(fill=tk.X, padx=padx, pady=pady)
    for text, command, options in buttons:
        opts = dict(options or {})
        side = opts.pop("side", tk.LEFT)
        pack_padx = opts.pop("pack_padx", 6)
        opts.setdefault("command", command)
        opts.setdefault("cursor", "hand2")
        opts.setdefault("relief", tk.FLAT)
        opts.setdefault("bd", 0)
        opts.setdefault("padx", 14)
        opts.setdefault("pady", 5)
        opts.setdefault("font", ("Microsoft YaHei UI", 9))
        if "bg" not in opts:
            opts["bg"] = "#ffffff"
        if "highlightthickness" not in opts:
            opts["highlightthickness"] = 1
            opts["highlightbackground"] = EDITOR_THEME["border"]
        btn = tk.Button(frame, text=text, **opts)
        btn_bg = opts.get("bg", "#ffffff")
        def _on_enter(e, b=btn, bg=btn_bg):
            try:
                if b["state"] != tk.DISABLED:
                    b.configure(bg="#f1f5f9" if bg == "#ffffff" else bg)
            except Exception:
                pass
        def _on_leave(e, b=btn, bg=btn_bg):
            try:
                if b["state"] != tk.DISABLED:
                    b.configure(bg=bg)
            except Exception:
                pass
        btn.bind("<Enter>", _on_enter)
        btn.bind("<Leave>", _on_leave)
        btn.pack(side=side, padx=pack_padx)
    return frame

DEFAULT_STEP_CONTROLS_BY_ID = {
    "configure_projection": [
        {"id": "config_button", "name": "配置按钮", "role": "进入配置窗口", "targetMethod": "template", "targetValue": "config_button", "templateKey": "config_button"},
        {"id": "general_tree_item", "name": "常规树节点", "role": "切到常规页", "targetMethod": "template", "targetValue": "general_tree_item", "templateKey": "general_tree_item"},
        {"id": "projection_tree_item", "name": "投影树节点", "role": "切到投影页", "targetMethod": "template", "targetValue": "projection_tree_item", "templateKey": "projection_tree_item"},
        {"id": "load_from_file_button", "name": "从文件加载按钮", "role": "加载投影文件", "targetMethod": "template", "targetValue": "load_from_file_button", "templateKey": "load_from_file_button"},
        {"id": "apply_button", "name": "应用按钮", "role": "应用投影配置", "targetMethod": "template", "targetValue": "apply_button", "templateKey": "apply_button"},
        {"id": "ok_button", "name": "确定按钮", "role": "确认投影配置", "targetMethod": "template", "targetValue": "ok_button", "templateKey": "ok_button"},
    ],
    "open_source_dwg": [
        {"id": "open_dialog_filename", "name": "文件名输入框", "role": "输入 DWG 文件路径", "targetMethod": "name", "targetValue": "文件名(N):"},
    ],
    "dwg_projection_confirm": [
        {"id": "dwg_load_from_file", "name": "从文件加载按钮", "role": "载入投影文件", "targetMethod": "template", "targetValue": "load_from_file_button", "templateKey": "load_from_file_button"},
        {"id": "dwg_filename", "name": "文件名输入框", "role": "输入投影文件路径", "targetMethod": "template", "targetValue": "file_name_input", "templateKey": "file_name_input"},
        {"id": "dwg_open_ok", "name": "打开(O)按钮", "role": "确认打开投影文件", "targetMethod": "name", "targetValue": "打开(O)"},
    ],
    "select_dgx_layer": [
        {"id": "layer_expand_icon", "name": "图层展开图标", "role": "展开 DGX 图层父节点", "targetMethod": "template", "targetValue": "展开图标", "templateKey": "展开图标.png"},
        {"id": "dgx_layer_item", "name": "DGX 图层项", "role": "右键 DGX 图层", "targetMethod": "regex", "targetValue": " - DGX \\[\\d+ Features\\]"},
        {"id": "dgx_select_all_menu", "name": "DGX 图层全选菜单", "role": "选择 DGX 图层中的所有要素", "targetMethod": "name,control_type", "targetValue": "选择 - 使用数字化工具选择所选图层中的所有要素,MenuItem", "windowTitle": "__all__", "inspectData": {"name": "选择 - 使用数字化工具选择所选图层中的所有要素", "controlType": "MenuItem"}, "auxChecks": ["IsEnabled=True", "IsOffscreen=False"]},
    ],
    "create_coverage": [
        {"id": "coverage_context_pane", "name": "覆盖区右键窗格", "role": "将鼠标移动到窗格后右键打开高级要素创建菜单", "targetMethod": "automation_id,control_type", "targetValue": "59648,Pane", "windowTitle": "__all__", "inspectData": {"automationId": "59648", "controlType": "Pane"}, "auxChecks": ["IsEnabled=True", "IsOffscreen=False"]},
        {"id": "advanced_feature_options_menu", "name": "高级要素创建选项菜单", "role": "打开高级要素创建菜单", "targetMethod": "name,control_type", "targetValue": "高级要素创建选项,MenuItem", "windowTitle": "__all__", "inspectData": {"name": "高级要素创建选项", "controlType": "MenuItem"}, "auxChecks": ["IsEnabled=True", "IsOffscreen=False"]},
        {"id": "create_coverage_menu", "name": "创建覆盖区菜单", "role": "从右键菜单进入凹形体覆盖区创建", "targetMethod": "name,control_type", "targetValue": "为 选定/加载 的要素创建覆盖区 (凹形体),MenuItem", "windowTitle": "__all__"},
        {"id": "coverage_smooth_input", "name": "平滑输入框", "role": "设置 Concave Hull Options 的平滑值", "targetMethod": "control_type", "targetValue": "Edit", "windowTitle": "Concave Hull Options"},
        {"id": "coverage_ok_button", "name": "覆盖区确定按钮", "role": "确认 Concave Hull Options", "targetMethod": "name,control_type", "targetValue": "确定,Button", "windowTitle": "Concave Hull Options"},
    ],
    "select_coverage_layer": [
        {"id": "coverage_layer_item", "name": "Coverage Areas 图层项", "role": "右键 Coverage Areas 图层", "targetMethod": "regex", "targetValue": " - DGX Coverage Areas \\[\\d+ Features\\]"},
        {"id": "coverage_select_all_menu", "name": "Coverage 图层全选菜单", "role": "选择 Coverage 图层中的所有要素", "targetMethod": "name,control_type", "targetValue": "选择 - 使用数字化工具选择所选图层中的所有要素,MenuItem", "windowTitle": "__all__"},
    ],
    "create_grid": [
        {"id": "analysis_menu", "name": "分析菜单", "role": "打开分析菜单", "targetMethod": "name,control_type", "targetValue": "分析(A),MenuItem"},
        {"id": "create_grid_menu", "name": "创建高程网格菜单", "role": "进入从 3D 矢量/Lidar 数据创建高程网格", "targetMethod": "name,control_type", "targetValue": "从 3D 矢量/Lidar 数据创建高程网格(D)...,MenuItem"},
        {"id": "select_layer_ok_button", "name": "选择图层确定按钮", "role": "确认参与创建网格的图层", "targetMethod": "name,control_type", "targetValue": "确定,Button", "windowTitle": "选择图层"},
        {"id": "manual_spacing_option", "name": "手动指定网格间距选项", "role": "切换为手动指定要使用的网格间距", "targetMethod": "name", "targetValue": "手动指定要使用的网格间距", "windowTitle": "网格创建选项"},
        {"id": "grid_x_input", "name": "X 轴输入框", "role": "输入 X 轴网格间距", "targetMethod": "control_type", "targetValue": "Edit", "windowTitle": "网格创建选项"},
        {"id": "grid_y_input", "name": "Y 轴输入框", "role": "输入 Y 轴网格间距", "targetMethod": "control_type", "targetValue": "Edit", "windowTitle": "网格创建选项"},
        {"id": "grid_boundary_tab", "name": "网格边界选项卡", "role": "切换到网格边界页", "targetMethod": "name,control_type", "targetValue": "网格边界,TabItem", "windowTitle": "网格创建选项"},
        {"id": "clip_selected_area_option", "name": "裁剪到选定区要素选项", "role": "裁剪到选定的区要素", "targetMethod": "name", "targetValue": "裁剪到选定的区要素", "windowTitle": "网格创建选项"},
        {"id": "grid_ok_button", "name": "网格创建确定按钮", "role": "确认网格创建选项", "targetMethod": "name,control_type", "targetValue": "确定,Button", "windowTitle": "网格创建选项"},
    ],
    "export_geotiff": [
        {"id": "generated_grid_item", "name": "Generated Grid 图层项", "role": "右键 Generated Grid 图层", "targetMethod": "regex", "targetValue": "Generated Grid 1"},
        {"id": "export_layer_menu", "name": "导出图层菜单", "role": "进入导出到新文件", "targetMethod": "name,control_type", "targetValue": "导出 - 将图层导出到新文件...,MenuItem"},
        {"id": "export_select_layer_ok", "name": "导出选择图层确定按钮", "role": "确认导出图层选择", "targetMethod": "name,control_type", "targetValue": "确定,Button", "windowTitle": "选择图层"},
        {"id": "export_format_combo", "name": "导出格式下拉框", "role": "选择导出格式", "targetMethod": "control_type", "targetValue": "ComboBox", "windowTitle": "选择导出格式"},
        {"id": "export_format_ok", "name": "导出格式确定按钮", "role": "确认导出格式", "targetMethod": "name,control_type", "targetValue": "确定,Button", "windowTitle": "选择导出格式"},
        {"id": "export_prompt_ok", "name": "提示确定按钮", "role": "关闭导出前提示窗口", "targetMethod": "name,control_type", "targetValue": "确定,Button", "windowTitle": "提示"},
        {"id": "geotiff_ok_button", "name": "GeoTIFF 导出确定按钮", "role": "确认 GeoTIFF 导出选项", "targetMethod": "name,control_type", "targetValue": "确定,Button", "windowTitle": "GeoTIFF 导出选项"},
        {"id": "save_dialog_path_input", "name": "保存路径输入框", "role": "在保存对话框中输入目录", "targetMethod": "name", "targetValue": "地址(D):", "windowTitle": "另存为"},
        {"id": "save_dialog_save_button", "name": "保存按钮", "role": "确认保存导出结果", "targetMethod": "name,control_type", "targetValue": "保存(S),Button", "windowTitle": "另存为"},
    ],
}


DEFAULT_FLOW_DEFINITION = {
    "version": "1.0",
    "project": "WT_Automation",
    "description": "WT 自动化流程链路定义。当前用于流程可视化、参数编辑、控件信息记录、步骤增删与运行参数维护。",
    "lastUpdated": "",
    "runtimeConfig": {
        "gmExe": "",
        "sourceFilePath": "",
        "outputDir": "",
        "projectionFilePath": "",
    },
    "flowPackages": [],
    "steps": [
        {
            "id": "launch_gm",
            "name": "启动目标软件",
            "stage": "startup",
            "strategy": "script",
            "enabled": True,
            "codeSymbol": "automation_process()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "启动目标软件并等待主窗口稳定。",
            "successLog": "启动目标软件",
            "windowTitle": "目标软件主窗口",
            "inspectHints": {
                "controlName": "",
                "className": "",
                "automationId": "",
                "controlType": "Window",
                "uiPath": "MAIN_WINDOW_UIPATH",
                "templateKey": "",
            },
            "auxChecks": [
                "GM_EXE 文件存在",
                "主窗口可激活并最大化",
            ],
            "fallbacks": [],
            "notes": "当前为主流程固定起点。",
        },
        {
            "id": "configure_projection",
            "name": "配置初始投影",
            "stage": "projection",
            "strategy": "script -> image -> ai",
            "enabled": True,
            "codeSymbol": "_configure_projection()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "进入 配置/常规/投影，加载 prj 文件并应用确定。",
            "successLog": "投影配置完成",
            "windowTitle": "配置 - 常规 / 配置 - 投影",
            "inspectHints": {
                "controlName": "配置 / 常规 / 投影 / 从文件加载 / 应用 / 确定",
                "className": "Button / TreeItem",
                "automationId": "",
                "controlType": "Button,TreeItem",
                "uiPath": "配置 - 常规||Window / 配置 - 投影||Window",
                "templateKey": "config_button,general_tree_item,projection_tree_item,load_from_file_button,apply_button,ok_button",
            },
            "auxChecks": [
                "失败时记录 fallback_stage",
                "图片匹配失败时传递 AI resume_stage",
                "确认当前已执行到哪一步，避免重复",
            ],
            "fallbacks": ["image_projection", "ai_projection"],
            "notes": "这是当前最复杂的链路之一，适合后续继续细分成子步骤。",
        },
        {
            "id": "open_source_dwg",
            "name": "打开源 DWG 文件",
            "stage": "import",
            "strategy": "script",
            "enabled": True,
            "codeSymbol": "_type_path_into_open_dialog()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "通过 Ctrl+O 打开文件对话框并输入源数据路径。",
            "successLog": "等待文件加载...",
            "windowTitle": "打开 文件对话框",
            "inspectHints": {
                "controlName": "文件名(N):",
                "className": "Edit / ComboBox",
                "automationId": "",
                "controlType": "Edit",
                "uiPath": "打开||Window",
                "templateKey": "",
            },
            "auxChecks": [
                "SOURCE_FILE_PATH 文件存在",
                "文件对话框已切到当前前台",
            ],
            "fallbacks": [],
            "notes": "",
        },
        {
            "id": "dwg_projection_confirm",
            "name": "DWG 导入后投影确认",
            "stage": "import_projection",
            "strategy": "image -> ai",
            "enabled": True,
            "codeSymbol": "_handle_dwg_projection_selection()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "脚本从文件加载 prj，AI 只负责确认三项投影参数正确。",
            "successLog": "处理 DWG 导入后的投影选择",
            "windowTitle": "DWG 投影选择窗口",
            "inspectHints": {
                "controlName": "从文件加载 / 文件名 / 打开(O) / 确定",
                "className": "Button / Edit",
                "automationId": "",
                "controlType": "Button,Edit",
                "uiPath": "投影选择窗口 / 打开||Window",
                "templateKey": "load_from_file_button,file_name_input",
            },
            "auxChecks": [
                "投影类型=Gauss Krueger (3 degree zones)",
                "带号=Zone 40",
                "基准面=WGS84",
            ],
            "fallbacks": ["ai_dwg_projection"],
            "notes": "AI 应从 confirm_values 阶段续跑，不要重复从文件加载。",
        },
        {
            "id": "split_layers",
            "name": "按属性拆分图层",
            "stage": "layers",
            "strategy": "script",
            "enabled": True,
            "codeSymbol": "automation_process()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "调用 图层 -> 基于属性值拆分为独立图层。",
            "successLog": "基于属性值拆分为独立图层",
            "windowTitle": "主窗口",
            "inspectHints": {
                "controlName": "图层(Y) / 基于属性值拆分为独立图层... / 确定",
                "className": "MenuItem / Button",
                "automationId": "",
                "controlType": "MenuItem,Button",
                "uiPath": "MAIN_WINDOW_UIPATH",
                "templateKey": "",
            },
            "auxChecks": [
                "拆分结果图层树可见",
            ],
            "fallbacks": [],
            "notes": "",
        },
        {
            "id": "select_dgx_layer",
            "name": "展开并右键 DGX 图层",
            "stage": "layers",
            "strategy": "template -> script",
            "enabled": True,
            "codeSymbol": "_try_click_layer_tree_expand_icon() + _right_click_tree_item_by_title_re()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "先尝试模板点击展开图标，再右键 DGX 图层。",
            "successLog": "选择DGX图层",
            "windowTitle": "主窗口图层树",
            "inspectHints": {
                "controlName": "DGX 图层 / 展开图标",
                "className": "TreeItem",
                "automationId": "",
                "controlType": "TreeItem",
                "uiPath": "图层树",
                "templateKey": "展开图标.png / 折叠图标.png",
            },
            "auxChecks": [
                "source_basename - DGX [n Features] 正则匹配",
                "展开图标模板可命中",
            ],
            "fallbacks": [],
            "notes": "这一段已经通过局部测试验证成功，并已并回主流程。",
        },
        {
            "id": "create_coverage",
            "name": "创建覆盖区",
            "stage": "coverage",
            "strategy": "script -> ai",
            "enabled": True,
            "codeSymbol": "automation_process()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "右键 DGX 图层后创建覆盖区，并由 AI 设置平滑值。",
            "successLog": "创建覆盖区",
            "windowTitle": "Concave Hull Options",
            "inspectHints": {
                "controlName": "为 选定/加载 的要素创建覆盖区 (凹形体)",
                "className": "MenuItem / Edit / Button",
                "automationId": "",
                "controlType": "MenuItem,Edit,Button",
                "uiPath": "Concave Hull Options||Window",
                "templateKey": "",
            },
            "auxChecks": [
                "平滑值=10",
            ],
            "fallbacks": ["ai_coverage_options"],
            "notes": "",
        },
        {
            "id": "select_coverage_layer",
            "name": "选择覆盖区图层",
            "stage": "coverage",
            "strategy": "script",
            "enabled": True,
            "codeSymbol": "_right_click_tree_item_by_title_re()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "右键 Coverage Areas 图层并选择所有要素。",
            "successLog": "选择覆盖区图层",
            "windowTitle": "主窗口图层树",
            "inspectHints": {
                "controlName": "DGX Coverage Areas",
                "className": "TreeItem",
                "automationId": "",
                "controlType": "TreeItem",
                "uiPath": "图层树",
                "templateKey": "",
            },
            "auxChecks": [],
            "fallbacks": [],
            "notes": "",
        },
        {
            "id": "create_grid",
            "name": "创建高程网格",
            "stage": "grid",
            "strategy": "script -> ai",
            "enabled": True,
            "codeSymbol": "automation_process()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "打开网格创建窗口，并由 AI 完成网格间距和边界设置。",
            "successLog": "创建高程网格",
            "windowTitle": "网格创建选项",
            "inspectHints": {
                "controlName": "分析(A) / 从 3D 矢量/Lidar 数据创建高程网格(D)... / 确定",
                "className": "MenuItem / Button / Edit",
                "automationId": "",
                "controlType": "MenuItem,Button,Edit",
                "uiPath": "网格创建选项||Window",
                "templateKey": "",
            },
            "auxChecks": [
                "X-轴=5",
                "Y轴=5",
                "裁剪到选定的区要素",
            ],
            "fallbacks": ["ai_grid_options"],
            "notes": "",
        },
        {
            "id": "export_geotiff",
            "name": "导出 GeoTIFF",
            "stage": "export",
            "strategy": "script",
            "enabled": True,
            "codeSymbol": "automation_process()",
            "codeReference": "WT_AUT_recorded.py",
            "description": "右键网格图层，选择导出格式 GeoTIFF 并保存输出。",
            "successLog": "导出网格",
            "windowTitle": "选择导出格式 / GeoTIFF 导出选项",
            "inspectHints": {
                "controlName": "导出 - 将图层导出到新文件... / 选择导出格式 / 确定",
                "className": "MenuItem / ComboBox / Button",
                "automationId": "",
                "controlType": "MenuItem,ComboBox,Button",
                "uiPath": "选择导出格式||Window / GeoTIFF 导出选项||Window",
                "templateKey": "",
            },
            "auxChecks": [
                "导出格式=GeoTIFF",
                "输出目录存在",
            ],
            "fallbacks": [],
            "notes": "",
        },
    ],
}


DEFAULT_FLOW_DEFINITION["description"] = "WT 自动化流程链路定义。当前用于新软件自动化项目的流程可视化、参数编辑、控件信息记录与步骤增删。"
DEFAULT_FLOW_DEFINITION["flowPackages"] = []
DEFAULT_FLOW_DEFINITION["steps"] = []


STEP_TEMPLATES = [
    {
        "id": "click_control",
        "name": "按钮点击",
        "description": "创建一个 action 点击步骤，默认按 按钮/Button 的稳定路径预填，适合普通按钮、导入按钮、确认按钮。",
        "step": {
            "id": "click_control",
            "name": "点击-按钮",
            "stage": "custom",
            "strategy": "action",
            "actionType": "action",
            "description": "通过流程链路匹配点击目标按钮。优先使用 automation_id + Button，名称仅作为补充。",
            "windowTitle": "请补充窗口标题",
            "inspectHints": {
                "controlName": "目标按钮",
                "className": "Button",
                "automationId": "",
                "controlType": "Button",
                "uiPath": "",
                "templateKey": "",
            },
            "actionConfig": build_action_default_config("click", controlId="target_control"),
            "controls": [
                {
                    "id": "target_control",
                    "name": "目标按钮",
                    "role": "点击目标按钮",
                    "windowTitle": "请补充窗口标题",
                    "targetMethod": "automation_id,control_type",
                    "targetValue": "请补充AutomationId,Button",
                }
            ],
        },
    },
    {
        "id": "right_click_control",
        "name": "右键控件",
        "description": "创建一个 action 右击步骤，适合图层、窗格、树节点等右键菜单入口。",
        "step": {
            "id": "right_click_control",
            "name": "右键控件",
            "stage": "custom",
            "strategy": "action",
            "actionType": "action",
            "description": "通过流程链路匹配右键目标控件。",
            "actionConfig": build_action_default_config("right_click", controlId="target_control"),
            "controls": [
                {
                    "id": "target_control",
                    "name": "目标控件",
                    "role": "右键目标控件",
                    "targetMethod": "name,control_type",
                    "targetValue": "请补充控件名称,Pane",
                }
            ],
        },
    },
    {
        "id": "type_text",
        "name": "输入框输入",
        "description": "创建一个 action 输入步骤，默认按 标签名 + Edit 的可编辑输入框路径预填，适合文件名、路径、账号等场景。",
        "step": {
            "id": "type_text",
            "name": "键入-输入框",
            "stage": "custom",
            "strategy": "action",
            "actionType": "action",
            "windowTitle": "请补充窗口标题",
            "description": "通过流程链路匹配输入文本。优先命中真正的 Edit 输入框，不要只按同名标签定位。",
            "inspectHints": {
                "controlName": "输入框",
                "className": "Edit",
                "automationId": "",
                "controlType": "Edit",
                "uiPath": "",
                "templateKey": "",
            },
            "actionConfig": build_action_default_config(
                "type_text",
                controlId="target_input",
                text="${runtime.sourceFilePath}",
            ),
            "controls": [
                {
                    "id": "target_input",
                    "name": "输入框",
                    "role": "输入文本",
                    "windowTitle": "请补充窗口标题",
                    "targetMethod": "name,class_name",
                    "targetValue": "请补充标签名称,Edit",
                }
            ],
        },
    },
    {
        "id": "select_dropdown_item",
        "name": "下拉项选择",
        "description": "创建一个运行时下拉选择步骤，默认在 Popup 中枚举可见 ListBoxItem/MenuItem，适合 WPF 下拉项。",
        "step": {
            "id": "select_dropdown_item",
            "name": "选择-下拉项",
            "stage": "custom",
            "strategy": "action",
            "actionType": "action",
            "windowTitle": "请补充窗口标题",
            "description": "优先在运行时枚举 Popup 中的可见 ListBoxItem/MenuItem，不要直接抓离屏 TextBlock 文字层。",
            "inspectHints": {
                "controlName": "下拉项",
                "className": "ListBoxItem",
                "automationId": "",
                "controlType": "ListItem",
                "uiPath": "",
                "templateKey": "",
            },
            "actionConfig": build_action_default_config("select_dropdown_item_runtime", controlId="dropdown_item"),
            "controls": [
                {
                    "id": "dropdown_item",
                    "name": "下拉项",
                    "role": "点击下拉项",
                    "windowTitle": "请补充窗口标题",
                    "targetMethod": "name,class_name",
                    "targetValue": "请补充选项文本,ListBoxItem",
                }
            ],
        },
    },
    {
        "id": "click_relative_region",
        "name": "相对区域操作",
        "description": "适合 WPF 弹窗、自绘控件、文字层难以稳定命中时，先锁定父窗口，再按相对区域操作。",
        "step": {
            "id": "click_relative_region",
            "name": "点击-相对区域",
            "stage": "custom",
            "strategy": "action",
            "actionType": "action",
            "windowTitle": "请补充父窗口标题",
            "description": "通过父窗口相对区域点击控件，适合 WPF 弹窗、自绘按钮等场景。",
            "actionConfig": {
                **build_action_default_config("click_relative_region"),
                "parentWindow": {
                    "title": "请补充父窗口标题",
                    "className": "Window",
                    "frameworkId": "WPF",
                },
                "relativeRegion": {
                    "x": 0.45,
                    "y": 0.45,
                    "width": 0.32,
                    "height": 0.08,
                    "anchor": "center",
                },
            },
        },
    },
    {
        "id": "type_text_relative",
        "name": "父窗口区域输入",
        "description": "适合 WPF 弹窗内部拿不到真实输入框时，先锁定父窗口，再按相对区域点击并输入文本。",
        "step": {
            "id": "type_text_relative",
            "name": "父窗口区域输入",
            "stage": "custom",
            "strategy": "action",
            "actionType": "action",
            "windowTitle": "请补充父窗口标题",
            "description": "通过父窗口相对区域点击输入文本，适合 WPF 弹窗、自绘输入框等场景。",
            "actionConfig": {
                **build_action_default_config("type_text_relative", text="${runtime.sourceFilePath}"),
                "parentWindow": {
                    "title": "请补充父窗口标题",
                    "className": "Window",
                    "frameworkId": "WPF",
                },
                "relativeRegion": {
                    "x": 0.45,
                    "y": 0.45,
                    "width": 0.32,
                    "height": 0.08,
                    "anchor": "center",
                },
            },
        },
    },
    {
        "id": "wait_for_control",
        "name": "等待控件出现",
        "description": "创建一个等待步骤，适合等待弹窗、菜单或主界面区域加载完成。",
        "step": {
            "id": "wait_for_control",
            "name": "等待控件出现",
            "stage": "custom",
            "strategy": "action",
            "actionType": "action",
            "description": "等待目标控件在超时时间内出现。",
            "actionConfig": build_action_default_config("wait_for_control", controlId="target_control"),
            "controls": [
                {
                    "id": "target_control",
                    "name": "等待目标控件",
                    "role": "等待目标控件出现",
                    "targetMethod": "name,control_type",
                    "targetValue": "请补充控件名称,Window",
                }
            ],
        },
    },
    {
        "id": "sleep",
        "name": "固定等待",
        "description": "创建一个固定时长等待步骤，适合短暂缓冲或调试。",
        "step": {
            "id": "sleep",
            "name": "固定等待",
            "stage": "custom",
            "strategy": "action",
            "actionType": "action",
            "description": "按指定秒数暂停。",
            "actionConfig": {
                "action": "sleep",
                "seconds": 1.0,
            },
        },
    },
    {
        "id": "flow_ref",
        "name": "调用流程包",
        "description": "创建一个 flow_ref 步骤，用于在当前流程中复用已有流程包。",
        "step": {
            "id": "flow_ref_step",
            "name": "调用流程包",
            "stage": "package",
            "strategy": "flow_ref",
            "actionType": "flow_ref",
            "packageRef": "",
            "description": "调用流程包中的一组步骤。",
        },
    },
]

QUICK_ADD_TEMPLATE_CHOICES = [
    {
        "template_id": "click_control",
        "label": "新增按钮",
        "description": "默认走 automation_id + Button，适合普通按钮。",
    },
    {
        "template_id": "type_text",
        "label": "新增输入框",
        "description": "默认走 标签名 + Edit，适合文件名/路径输入。",
    },
    {
        "template_id": "select_dropdown_item",
        "label": "新增下拉项",
        "description": "默认走 ListBoxItem 父容器，适合访问级别、列表项选择。",
    },
    {
        "template_id": "click_relative_region",
        "label": "新增相对区域",
        "description": "默认预填父窗口 + 区域参数，适合 WPF 自绘控件。",
    },
]

STEP_AUTHORING_GUIDE = """WT 步骤新增填写规范

一、优先顺序
1. 先新增细分控件，再新增动作步骤。
2. 先选对控件类型，再选动作。
3. automationId 有值时优先用 automation_id。
4. 同名控件很多时，必须补 class_name 或 control_type。
5. WPF 自绘控件抓不到稳定对象时，直接改用父窗口相对区域。

二、四种高频模板怎么用
1. 新增按钮
   推荐场景：导入按钮、确认按钮、普通按钮。
   默认目标：automation_id + Button。
   你通常只需要改：步骤名称、窗口标题、AutomationId、成功日志。

2. 新增输入框
   推荐场景：文件名、路径、账号、数字输入。
   默认目标：name + Edit。
   你通常只需要改：步骤名称、窗口标题、标签名称、输入文本。
   关键提醒：不要只填 name=文件名(N):，否则容易命中左侧 Static 标签。

3. 新增下拉项
   推荐场景：访问级别、枚举值、列表项选择。
   默认目标：name + ListBoxItem。
   你通常只需要改：步骤名称、窗口标题、选项文本。
   关键提醒：优先抓 ListBoxItem/MenuItem 父容器，不要直接抓 TextBlock。

4. 新增相对区域
   推荐场景：WPF 弹窗、自绘按钮、自绘输入框、控件树不稳定。
   默认目标：父窗口 + relativeRegion。
   你通常只需要改：父窗口标题、区域坐标、动作类型、输入文本。
   关键提醒：点击用 click_relative_region，输入用 type_text_relative。

三、字段填写口诀
1. 按钮：automation_id + Button 最稳。
2. 输入框：name 不够，要补 Edit。
3. 下拉项：先父容器，后文字层。
4. 相对区域：先父窗口，后矩形。
5. 输入文本直接填真实内容，不要手工再包一层双引号。

四、单步验证建议
1. 新增完先只跑当前步骤。
2. 日志成功但界面没反应，先检查是不是抓到了同名标签/文字层。
3. 如果 WPF 列表项或弹窗控件总是漂，优先切相对区域，不要硬顶纯控件定位。"""


def load_json_file(file_path):
    if not file_path or not os.path.exists(file_path):
        return None
    with open(file_path, "r", encoding="utf-8") as file_obj:
        return json.load(file_obj)


def save_json_file(file_path, payload):
    payload["lastUpdated"] = datetime.now().isoformat(timespec="seconds")
    parent_dir = os.path.dirname(os.path.abspath(file_path))
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2)


def load_editor_state():
    """读取编辑器自身的轻量状态（如新建链路的默认保存位置/名称）。"""
    state = load_json_file(EDITOR_STATE_FILE)
    return state if isinstance(state, dict) else {}


def save_editor_state(state):
    """写入编辑器自身的轻量状态。"""
    if not isinstance(state, dict):
        state = {}
    save_json_file(EDITOR_STATE_FILE, state)


def collect_flow_package_step_ids(flow_packages):
    step_ids = []
    seen_step_ids = set()
    for package in normalize_flow_packages(flow_packages or []):
        for item in package.get("stepIds", []):
            step_id = str(item).strip()
            if not step_id or step_id in seen_step_ids:
                continue
            step_ids.append(step_id)
            seen_step_ids.add(step_id)
    return step_ids


def resolve_initial_definition_path():
    launcher_state = load_json_file(LAUNCHER_STATE_FILE)
    if isinstance(launcher_state, dict):
        candidate = str(launcher_state.get("flowDefinitionPath", "")).strip()
        if candidate:
            return candidate

    registry_payload = load_json_file(FLOW_PACKAGE_REGISTRY_FILE)
    if isinstance(registry_payload, dict):
        candidate = str(registry_payload.get("sourceDefinitionPath", "")).strip()
        if candidate:
            return candidate

    return FLOW_DEFINITION_FILE


def sync_flow_package_registry(source_definition_path, runtime_config, flow_packages, steps):
    normalized_packages = normalize_flow_packages(flow_packages or [])
    normalized_steps = [normalize_step(step, index) for index, step in enumerate(steps or [])]
    existing_registry = load_json_file(FLOW_PACKAGE_REGISTRY_FILE)
    existing_steps = []
    if isinstance(existing_registry, dict):
        existing_steps = [normalize_step(step, index) for index, step in enumerate(existing_registry.get("steps", []))]
    referenced_step_ids = set(collect_flow_package_step_ids(normalized_packages))
    existing_step_map = {str(step.get("id", "")).strip(): step for step in existing_steps if str(step.get("id", "")).strip()}
    current_step_ids = {str(step.get("id", "")).strip() for step in normalized_steps if str(step.get("id", "")).strip()}
    for step_id in referenced_step_ids:
        if step_id in current_step_ids:
            continue
        fallback_step = existing_step_map.get(step_id)
        if not isinstance(fallback_step, dict):
            continue
        normalized_steps.append(normalize_step(json.loads(json.dumps(fallback_step, ensure_ascii=False)), len(normalized_steps)))
    payload = {
        "version": "1.0",
        "project": "WT_Automation",
        "description": "WT 自动化流程包注册表。由流程链路编辑器自动同步生成，供总控台读取流程包与步骤。",
        "sourceDefinitionPath": source_definition_path,
        "storageDir": FLOW_PACKAGE_STORE_DIR,
        "runtimeConfig": normalize_runtime_config(runtime_config or {}),
        "flowPackages": normalized_packages,
        "steps": normalized_steps,
    }
    save_json_file(FLOW_PACKAGE_REGISTRY_FILE, payload)


def sync_launcher_flow_definition_path(source_definition_path):
    launcher_state = load_json_file(LAUNCHER_STATE_FILE)
    if not isinstance(launcher_state, dict):
        launcher_state = {}
    launcher_state["flowDefinitionPath"] = source_definition_path
    launcher_state["updatedAt"] = datetime.now().isoformat(timespec="seconds")
    save_json_file(LAUNCHER_STATE_FILE, launcher_state)


def normalize_control_type_name(control_type, localized_control_type=""):
    return wt_flow_editor_utils.normalize_control_type_name(control_type, localized_control_type)


def build_locator_recommendation(parsed):
    from build_control_map_library import build_locator_recommendation as advanced_build_locator
    return advanced_build_locator(parsed)


def parse_inspect_text(raw_text):
    return wt_flow_editor_utils.parse_inspect_text(raw_text)


def normalize_control(control, index):
    return wt_flow_editor_utils.normalize_control(control, index)


def _safe_get_value(getter, default=""):
    try:
        value = getter()
    except Exception:
        return default
    return default if value is None else value


def _launch_python_script(script_path):
    """在后台启动一个独立的 Python 脚本进程（统一 cwd / 静默输出 / 无控制台窗口）。

    收口编辑器中多处完全一致的 subprocess.Popen 调用（采集器/模板库等）。
    返回 Popen 句柄；启动失败则抛出异常，由调用方自行处理提示。
    注：孤儿进程回收属 P2 改进（会改变行为），本函数暂保持与原有行为一致。
    """
    return subprocess.Popen(
        [sys.executable, script_path],
        cwd=BASE_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def build_synthetic_inspect_text(inspect_data):
    return wt_flow_editor_utils.build_synthetic_inspect_text(inspect_data)


def normalize_step(step, index):
    return wt_flow_editor_utils.normalize_step(step, index, DEFAULT_STEP_CONTROLS_BY_ID)


def _slugify_control_library_part(text, fallback="common"):
    return wt_flow_editor_utils.slugify_filename(text, fallback)


def _build_control_library_category(control, step=None):
    control = control if isinstance(control, dict) else {}
    step = step if isinstance(step, dict) else {}
    inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
    ui_path = str(control.get("uiPath", "")).strip() or str(step.get("inspectHints", {}).get("uiPath", "")).strip()
    main_window_root = wt_flow_editor_utils.uipath_is_main_window_root(ui_path)
    window_title = (
        str(control.get("windowTitle", "")).strip()
        or str(step.get("windowTitle", "")).strip()
        or str(inspect_data.get("name", "")).strip()
        or "未分类窗口"
    )
    if main_window_root:
        # 主窗口内控件统一归入“主窗口”分类，避免用控件库分类名冒充真实顶层窗口标题
        window_title = "主窗口"
    framework_id = (
        str(inspect_data.get("frameworkId", "")).strip()
        or str(step.get("actionConfig", {}).get("parentWindow", {}).get("frameworkId", "")).strip()
        or "unknown"
    )
    return {
        "windowTitle": window_title,
        "frameworkId": framework_id,
        "mainWindowRoot": bool(main_window_root),
        "fileName": "library_{window}_{framework}_controls.json".format(
            window=_slugify_control_library_part(window_title, "window"),
            framework=_slugify_control_library_part(framework_id, "unknown"),
        ),
    }


def _build_control_library_control_entry(control, step=None):
    control = normalize_control(control or {}, 0)
    step = step if isinstance(step, dict) else {}
    inspect_data = dict(control.get("inspectData", {}) or {})
    category = _build_control_library_category(control, step)
    entry = json.loads(json.dumps(control, ensure_ascii=False))
    entry["source"] = str(entry.get("source", "")).strip() or "flow_editor"
    entry["libraryCategory"] = {
        "windowTitle": category["windowTitle"],
        "frameworkId": category["frameworkId"],
        "stepId": str(step.get("id", "")).strip(),
        "stepName": str(step.get("name", "")).strip(),
    }
    if category.get("mainWindowRoot"):
        # 主窗口根路径内控件的真实窗口标题不在录制路径中，控件定义统一写 * 不约束
        entry["windowTitle"] = "*"
    elif not entry.get("windowTitle"):
        entry["windowTitle"] = category["windowTitle"]
    if inspect_data and not str(inspect_data.get("frameworkId", "")).strip() and category["frameworkId"] != "unknown":
        inspect_data["frameworkId"] = category["frameworkId"]
        entry["inspectData"] = inspect_data
    notes = str(entry.get("notes", "")).strip()
    auto_note = f"已自动收录到控件库分类：{category['windowTitle']} / {category['frameworkId']}"
    if auto_note not in notes:
        entry["notes"] = " | ".join(item for item in [notes, auto_note] if item)
    return entry


def _build_control_library_payload(category, existing_payload=None):
    existing_payload = existing_payload if isinstance(existing_payload, dict) else {}
    payload = existing_payload if isinstance(existing_payload, dict) else {}
    payload["schemaVersion"] = str(payload.get("schemaVersion", "")).strip() or "1.0"
    payload["description"] = "WT 自动化控件库。由链路编辑器自动沉淀常用控件，按窗口与框架分类保存。"
    payload["targetWindow"] = {
        "title": "*" if category.get("mainWindowRoot") else category["windowTitle"],
        "className": str((payload.get("targetWindow", {}) or {}).get("className", "")).strip(),
        "processId": str((payload.get("targetWindow", {}) or {}).get("processId", "")).strip(),
        "handle": str((payload.get("targetWindow", {}) or {}).get("handle", "")).strip(),
        "frameworkId": category["frameworkId"],
    }
    payload["scanMeta"] = payload.get("scanMeta", {}) if isinstance(payload.get("scanMeta"), dict) else {}
    payload["scanMeta"]["scanTime"] = datetime.now().isoformat(timespec="seconds")
    payload["scanMeta"]["backend"] = str(payload["scanMeta"].get("backend", "")).strip() or "editor"
    payload["scanMeta"]["mode"] = "editor_library"
    payload["scanMeta"]["categoryWindowTitle"] = "*" if category.get("mainWindowRoot") else category["windowTitle"]
    payload["scanMeta"]["categoryFrameworkId"] = category["frameworkId"]
    payload["controlDefinitions"] = payload.get("controlDefinitions", []) if isinstance(payload.get("controlDefinitions"), list) else []
    return payload


def _merge_controls_into_library_payload(payload, controls):
    payload = payload if isinstance(payload, dict) else {}
    existing_controls = payload.get("controlDefinitions", []) if isinstance(payload.get("controlDefinitions"), list) else []
    merged_controls = []
    control_index_map = {}
    locator_index_map = {}
    for existing in existing_controls:
        normalized = normalize_control(existing, len(merged_controls))
        merged_controls.append(normalized)
        control_id = str(normalized.get("id", "")).strip()
        locator_key = (
            str(normalized.get("windowTitle", "")).strip(),
            str(normalized.get("targetMethod", "")).strip(),
            str(normalized.get("targetValue", "")).strip(),
        )
        if control_id:
            control_index_map[control_id] = len(merged_controls) - 1
        if any(locator_key):
            locator_index_map[locator_key] = len(merged_controls) - 1
    updated_count = 0
    added_count = 0
    for control in controls:
        normalized = normalize_control(control, len(merged_controls))
        control_id = str(normalized.get("id", "")).strip()
        locator_key = (
            str(normalized.get("windowTitle", "")).strip(),
            str(normalized.get("targetMethod", "")).strip(),
            str(normalized.get("targetValue", "")).strip(),
        )
        target_index = None
        if control_id and control_id in control_index_map:
            target_index = control_index_map[control_id]
        elif any(locator_key) and locator_key in locator_index_map:
            target_index = locator_index_map[locator_key]
        if target_index is None:
            merged_controls.append(normalized)
            target_index = len(merged_controls) - 1
            added_count += 1
        else:
            merged_controls[target_index] = normalized
            updated_count += 1
        if control_id:
            control_index_map[control_id] = target_index
        if any(locator_key):
            locator_index_map[locator_key] = target_index
    payload["controlDefinitions"] = merged_controls
    payload["scanMeta"]["totalControls"] = len(merged_controls)
    payload["scanMeta"]["rawTotalControls"] = len(merged_controls)
    return added_count, updated_count


def normalize_runtime_config(runtime_config):
    return wt_flow_editor_utils.normalize_runtime_config(runtime_config)


def normalize_flow_packages(flow_packages):
    return wt_flow_editor_utils.normalize_flow_packages(flow_packages)


class NewDefaultChainDialog:
    """询问新建默认链路的保存目录与文件名称。

    可传入上一次使用的 directory / name 作为默认值，并在确认后将本次输入
    持久化（由调用方负责写入编辑器状态文件）。
    """

    def __init__(self, parent, last_directory="", last_name=""):
        self.parent = parent
        self.result = None  # {"directory": str, "name": str, "path": str}
        self.var_directory = tk.StringVar(value=(last_directory or BASE_DIR))
        self.var_name = tk.StringVar(value=(last_name or ""))
        self.warn_var = tk.StringVar()
        self.probe_status_var = tk.StringVar(value="正在探测路径合规性...")
        self._build_window()

    def _build_window(self):
        pal = EDITOR_THEME
        self.window = _make_dialog_window(
            self.parent,
            "✨ 新建流程链路定义",
            width=680,
            height=460,
            min_width=580,
            min_height=380,
            on_close=self.on_cancel,
        )

        root_frame = tk.Frame(self.window, bg=pal["bg"], padx=16, pady=14)
        root_frame.pack(fill=tk.BOTH, expand=True)

        # ── 顶栏标题与说明 ──
        header_frame = tk.Frame(root_frame, bg=pal["bg"])
        header_frame.pack(fill=tk.X, pady=(0, 10))

        tk.Label(
            header_frame,
            text="✨ 新建流程链路定义",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=pal["bg"],
            fg=pal["text"],
        ).pack(anchor="w")

        tk.Label(
            header_frame,
            text="指定链路保存目录与文件名称，支持常用工作区胶囊快捷填入及重名即时探测。",
            font=("Microsoft YaHei UI", 9),
            bg=pal["bg"],
            fg=pal["muted"],
        ).pack(anchor="w", pady=(2, 0))

        # ── 卡片 1：保存位置与快捷胶囊 ──
        dir_card = tk.LabelFrame(
            root_frame,
            text=" 📁 保存目录位置 ",
            bg=pal["card"],
            fg=pal["primary"],
            font=("Microsoft YaHei UI", 9, "bold"),
            bd=1,
            relief=tk.SOLID,
            padx=12,
            pady=10,
            highlightthickness=0,
        )
        dir_card.pack(fill=tk.X, pady=(0, 10))

        dir_input_row = tk.Frame(dir_card, bg=pal["card"])
        dir_input_row.pack(fill=tk.X)

        dir_entry = ttk.Entry(dir_input_row, textvariable=self.var_directory, font=("Microsoft YaHei UI", 9))
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        browse_btn = wt_theme.create_flat_button(
            dir_input_row,
            text="📂 浏览...",
            command=self._browse_directory,
            tone="secondary",
            padx=10,
            pady=3,
        )
        browse_btn.pack(side=tk.RIGHT)

        # 快捷直达胶囊栏
        chips_frame = tk.Frame(dir_card, bg=pal["card"])
        chips_frame.pack(fill=tk.X, pady=(8, 0))

        tk.Label(
            chips_frame,
            text="快捷直达:",
            font=("Microsoft YaHei UI", 8),
            bg=pal["card"],
            fg=pal["muted"],
        ).pack(side=tk.LEFT, padx=(0, 6))

        workspace_dir = os.path.join(BASE_DIR, "workspace")
        quick_locations = [
            ("📁 当前工作区", workspace_dir if os.path.isdir(workspace_dir) else BASE_DIR),
            ("⚡ 项目根目录", BASE_DIR),
            ("🖥️ 桌面", os.path.join(os.path.expanduser("~"), "Desktop")),
        ]
        # 扫描存在的盘符
        for drive in ("D:", "E:", "C:"):
            drive_path = drive + "\\"
            if os.path.exists(drive_path):
                quick_locations.append((f"{drive} 盘", drive_path))

        for chip_text, chip_path in quick_locations[:5]:
            btn = tk.Button(
                chips_frame,
                text=chip_text,
                command=lambda p=chip_path: self._set_directory(p),
                bg=pal.get("panel_soft", "#f8fafc"),
                fg=pal["text"],
                relief=tk.FLAT,
                bd=0,
                padx=8,
                pady=2,
                cursor="hand2",
                font=("Microsoft YaHei UI", 8),
                activebackground=pal.get("primary_soft", "#dbeafe"),
                activeforeground=pal["primary"],
                highlightthickness=1,
                highlightbackground=pal["border"],
            )
            btn.pack(side=tk.LEFT, padx=3)

        # ── 卡片 2：文件名称与命名预设 ──
        name_card = tk.LabelFrame(
            root_frame,
            text=" 📄 链路文件名称 ",
            bg=pal["card"],
            fg=pal["primary"],
            font=("Microsoft YaHei UI", 9, "bold"),
            bd=1,
            relief=tk.SOLID,
            padx=12,
            pady=10,
            highlightthickness=0,
        )
        name_card.pack(fill=tk.X, pady=(0, 10))

        name_input_row = tk.Frame(name_card, bg=pal["card"])
        name_input_row.pack(fill=tk.X)

        name_entry = ttk.Entry(name_input_row, textvariable=self.var_name, font=("Microsoft YaHei UI", 9))
        name_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        name_entry.focus_set()
        if self.var_name.get():
            name_entry.select_range(0, "end")

        tk.Label(
            name_input_row,
            text=".json",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=pal["card"],
            fg=pal["muted"],
        ).pack(side=tk.RIGHT)

        preset_row = tk.Frame(name_card, bg=pal["card"])
        preset_row.pack(fill=tk.X, pady=(8, 0))

        tk.Label(
            preset_row,
            text="命名预设:",
            font=("Microsoft YaHei UI", 8),
            bg=pal["card"],
            fg=pal["muted"],
        ).pack(side=tk.LEFT, padx=(0, 6))

        for preset_text, preset_val in (
            ("标准全流程", "flow_standard"),
            ("参数扫描子流程", "flow_param_scan"),
            ("快捷测试链路", "flow_quick_test"),
        ):
            pbtn = tk.Button(
                preset_row,
                text=preset_text,
                command=lambda v=preset_val: self._set_name(v),
                bg=pal.get("panel_soft", "#f8fafc"),
                fg=pal["text"],
                relief=tk.FLAT,
                bd=0,
                padx=8,
                pady=2,
                cursor="hand2",
                font=("Microsoft YaHei UI", 8),
                activebackground=pal.get("primary_soft", "#dbeafe"),
                activeforeground=pal["primary"],
                highlightthickness=1,
                highlightbackground=pal["border"],
            )
            pbtn.pack(side=tk.LEFT, padx=3)

        # ── 探针反馈指示条 ──
        probe_strip = tk.Frame(
            root_frame,
            bg=pal.get("panel_soft", "#f8fafc"),
            padx=10,
            pady=6,
            highlightthickness=1,
            highlightbackground=pal["border"],
        )
        probe_strip.pack(fill=tk.X, pady=(0, 12))

        self.probe_icon_label = tk.Label(
            probe_strip,
            text="✓",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=probe_strip["bg"],
            fg=pal["success"],
        )
        self.probe_icon_label.pack(side=tk.LEFT, padx=(0, 6))

        self.probe_msg_label = tk.Label(
            probe_strip,
            textvariable=self.probe_status_var,
            font=("Microsoft YaHei UI", 8),
            bg=probe_strip["bg"],
            fg=pal["text"],
            anchor="w",
        )
        self.probe_msg_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.var_directory.trace_add("write", self._refresh_warning)
        self.var_name.trace_add("write", self._refresh_warning)
        self._refresh_warning()

        # ── 底部操作按钮 ──
        btn_row = tk.Frame(root_frame, bg=pal["bg"])
        btn_row.pack(fill=tk.X, side=tk.BOTTOM)

        cancel_btn = wt_theme.create_flat_button(
            btn_row,
            text="取消 (Esc)",
            command=self.on_cancel,
            tone="secondary",
            padx=16,
            pady=5,
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(8, 0))

        ok_btn = wt_theme.create_flat_button(
            btn_row,
            text="确定新建 (Enter)",
            command=self.on_ok,
            tone="primary",
            padx=20,
            pady=5,
        )
        ok_btn.pack(side=tk.RIGHT)

        # 绑定键盘加速键
        self.window.bind("<Return>", lambda _e: self.on_ok())
        self.window.bind("<Escape>", lambda _e: self.on_cancel())

        self.window.update_idletasks()
        self.window.wait_window(self.window)

    def _set_directory(self, path):
        self.var_directory.set(path)
        self._refresh_warning()

    def _set_name(self, name):
        self.var_name.set(name)
        self._refresh_warning()

    def _browse_directory(self):
        initial = self.var_directory.get() or BASE_DIR
        chosen = filedialog.askdirectory(title="选择新建链路保存目录", initialdir=initial, parent=self.window)
        if chosen:
            self.var_directory.set(chosen)
        self._refresh_warning()

    def _build_path(self):
        directory = (self.var_directory.get() or "").strip()
        name = (self.var_name.get() or "").strip()
        if not directory or not name:
            return None
        if not name.lower().endswith(".json"):
            name += ".json"
        return os.path.join(directory, name)

    def _refresh_warning(self, *args):
        pal = EDITOR_THEME
        path = self._build_path()
        if not (self.var_directory.get() or "").strip() or not (self.var_name.get() or "").strip():
            self.warn_var.set("")
            self.probe_status_var.set("请填写保存目录与文件名称。")
            if hasattr(self, "probe_icon_label"):
                self.probe_icon_label.config(text="ℹ", fg=pal["muted"])
                self.probe_msg_label.config(fg=pal["muted"])
        elif path and os.path.exists(path):
            warn_msg = "⚠ 该路径已存在文件，新建后保存将覆盖原有内容。"
            self.warn_var.set(warn_msg)
            self.probe_status_var.set(f"同名文件已存在：{os.path.basename(path)}，确定后将提示覆盖。")
            if hasattr(self, "probe_icon_label"):
                self.probe_icon_label.config(text="⚠", fg=pal["warning"])
                self.probe_msg_label.config(fg=pal["warning_text"])
        else:
            self.warn_var.set("")
            self.probe_status_var.set(f"目标路径就绪：{path}，确认后可直接新建。")
            if hasattr(self, "probe_icon_label"):
                self.probe_icon_label.config(text="✓", fg=pal["success"])
                self.probe_msg_label.config(fg=pal["success_text"])

    def on_ok(self):
        directory = (self.var_directory.get() or "").strip()
        name = (self.var_name.get() or "").strip()
        if not directory:
            messagebox.showerror("缺少保存位置", "请选择或填写保存目录。", parent=self.window)
            return
        if not name:
            messagebox.showerror("缺少文件名称", "请填写新建链路的文件名称。", parent=self.window)
            return
        if not os.path.isdir(directory):
            try:
                os.makedirs(directory, exist_ok=True)
            except Exception as exc:
                messagebox.showerror("目录无效", f"无法创建保存目录：\n{exc}", parent=self.window)
                return
        path = self._build_path()
        if os.path.exists(path):
            if not messagebox.askyesno("文件已存在", f"目标文件已存在：\n{path}\n\n确定覆盖并新建吗？", parent=self.window):
                return
        self.result = {"directory": directory, "name": name, "path": path}
        self.window.destroy()

    def on_cancel(self):
        self.result = None
        self.window.destroy()


class ControlPickerDialog:
    """弹窗：选择锚点控件。来源为当前流程全部步骤的控件 + 控件库（control_maps）控件。"""

    def __init__(self, parent, controls=None, library_controls=None):
        self.parent = parent
        self.result = None
        self.selected_control = None
        self.selected_source = "flow"
        self._flow_controls = list(controls or [])
        self._library_controls = list(library_controls or [])
        self._displayed_controls = []
        self._displayed_sources = []
        self._source_filter = "all"  # "all" | "flow" | "library"

        self.window = _make_dialog_window(parent, "选择锚点控件", 980, 620, min_width=820, min_height=500)
        self.window.configure(bg=EDITOR_THEME.get("bg", "#f8fafc"))

        root_frame = tk.Frame(self.window, bg=EDITOR_THEME.get("bg", "#f8fafc"), padx=12, pady=10)
        root_frame.pack(fill=tk.BOTH, expand=True)

        # ── 顶栏筛选区（搜索框 + 来源分段器 + 匹配徽标） ──
        filter_bar = tk.Frame(
            root_frame,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            padx=12,
            pady=8,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        filter_bar.pack(fill=tk.X, pady=(0, 8))

        tk.Label(
            filter_bar,
            text="🔍 搜索：",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).pack(side=tk.LEFT)

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._on_search_changed)
        self._search_entry = tk.Entry(
            filter_bar,
            textvariable=self._search_var,
            font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            width=28,
        )
        self._search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6), ipady=3)

        def _clear_search():
            self._search_var.set("")
            self._search_entry.focus_set()

        wt_theme.create_flat_button(
            filter_bar,
            "✕ 清除",
            _clear_search,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=6,
            pady=2,
        ).pack(side=tk.LEFT, padx=(0, 14))

        # 来源分段器按钮组
        tk.Label(
            filter_bar,
            text="来源：",
            font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
        ).pack(side=tk.LEFT, padx=(0, 4))

        self._btn_filter_all = wt_theme.create_flat_button(
            filter_bar,
            f"全部 ({len(self._flow_controls) + len(self._library_controls)})",
            lambda: self._set_source_filter("all"),
            tone="primary",
            font=("Microsoft YaHei UI", 8),
            padx=8,
            pady=2,
        )
        self._btn_filter_all.pack(side=tk.LEFT, padx=(0, 4))

        self._btn_filter_flow = wt_theme.create_flat_button(
            filter_bar,
            f"流程步骤 ({len(self._flow_controls)})",
            lambda: self._set_source_filter("flow"),
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=8,
            pady=2,
        )
        self._btn_filter_flow.pack(side=tk.LEFT, padx=(0, 4))

        self._btn_filter_lib = wt_theme.create_flat_button(
            filter_bar,
            f"控件库 ({len(self._library_controls)})",
            lambda: self._set_source_filter("library"),
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=8,
            pady=2,
        )
        self._btn_filter_lib.pack(side=tk.LEFT, padx=(0, 8))

        # 匹配计数徽标
        self._match_count_var = tk.StringVar(value="")
        tk.Label(
            filter_bar,
            textvariable=self._match_count_var,
            font=("Microsoft YaHei UI", 8),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("primary", "#2563eb"),
            padx=8,
            pady=2,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        ).pack(side=tk.RIGHT)

        # ── 中部左右双栏（左列表 + 右属性即时透视） ──
        split_frame = tk.Frame(root_frame, bg=EDITOR_THEME.get("bg", "#f8fafc"))
        split_frame.pack(fill=tk.BOTH, expand=True)

        left_frame = tk.Frame(
            split_frame,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        columns = ("id", "name", "control_type", "source")
        self._tree, _scrollbar = _make_treeview(
            left_frame,
            columns,
            headings={"id": "ID", "name": "名称", "control_type": "控件类型", "source": "来源"},
            widths={"id": 140, "name": 150, "control_type": 100, "source": 75},
        )
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        _scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<Double-1>", self._on_double_click)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # 右侧：控件属性即时诊断卡片
        right_frame = tk.Frame(
            split_frame,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            width=360,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            padx=12,
            pady=10,
        )
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH)
        right_frame.pack_propagate(False)

        tk.Label(
            right_frame,
            text="🏷️ 控件即时诊断属性",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 8))

        # 属性展示容器
        self._detail_holder = tk.Frame(right_frame, bg=EDITOR_THEME.get("card", "#ffffff"))
        self._detail_holder.pack(fill=tk.BOTH, expand=True)

        self._preview_vars = {
            "id": tk.StringVar(value="-"),
            "name": tk.StringVar(value="-"),
            "windowTitle": tk.StringVar(value="-"),
            "targetMethod": tk.StringVar(value="-"),
            "targetValue": tk.StringVar(value="-"),
            "controlType": tk.StringVar(value="-"),
            "source": tk.StringVar(value="-"),
        }

        # ── 底部操作与提示栏 ──
        bottom_bar = tk.Frame(root_frame, bg=EDITOR_THEME.get("bg", "#f8fafc"))
        bottom_bar.pack(fill=tk.X, pady=(8, 0))

        tk.Label(
            bottom_bar,
            text="💡 双击行快速选定；选中「控件库」控件将自动导入为当前步骤锚点。",
            font=("Microsoft YaHei UI", 8),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
        ).pack(side=tk.LEFT)

        wt_theme.create_flat_button(
            bottom_bar,
            "确定选择",
            self._on_confirm,
            tone="primary",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=16,
            pady=5,
        ).pack(side=tk.RIGHT)

        wt_theme.create_flat_button(
            bottom_bar,
            "取消",
            self._on_cancel,
            tone="secondary",
            font=("Microsoft YaHei UI", 9),
            padx=14,
            pady=5,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self._show_empty_preview()
        self._refresh_display()
        self._search_entry.focus_set()

    # ── 内部方法 ──

    def _set_source_filter(self, filter_mode):
        self._source_filter = filter_mode
        tones = {
            "all": ("primary" if filter_mode == "all" else "secondary"),
            "flow": ("primary" if filter_mode == "flow" else "secondary"),
            "library": ("primary" if filter_mode == "library" else "secondary"),
        }
        # 更新按钮高亮态
        self._btn_filter_all.configure(
            bg=EDITOR_THEME["primary"] if tones["all"] == "primary" else EDITOR_THEME["card"],
            fg="#ffffff" if tones["all"] == "primary" else EDITOR_THEME["text"],
        )
        self._btn_filter_flow.configure(
            bg=EDITOR_THEME["primary"] if tones["flow"] == "primary" else EDITOR_THEME["card"],
            fg="#ffffff" if tones["flow"] == "primary" else EDITOR_THEME["text"],
        )
        self._btn_filter_lib.configure(
            bg=EDITOR_THEME["primary"] if tones["library"] == "primary" else EDITOR_THEME["card"],
            fg="#ffffff" if tones["library"] == "primary" else EDITOR_THEME["text"],
        )
        self._refresh_display()

    def _all_control_pairs(self):
        pairs = []
        if self._source_filter in ("all", "flow"):
            pairs.extend((ctrl, "流程") for ctrl in self._flow_controls)
        if self._source_filter in ("all", "library"):
            pairs.extend((ctrl, "控件库") for ctrl in self._library_controls)
        return pairs

    def _refresh_display(self, *_args):
        keyword = self._search_var.get().strip().lower()
        visible_controls = []
        visible_sources = []
        for ctrl, source in self._all_control_pairs():
            if not keyword:
                visible_controls.append(ctrl)
                visible_sources.append(source)
                continue
            searchable_text = (
                str(ctrl.get("id", "")).lower()
                + " "
                + str(ctrl.get("name", "")).lower()
                + " "
                + str(ctrl.get("windowTitle", "")).lower()
                + " "
                + str((ctrl.get("inspectData") or {}).get("name", "")).lower()
            )
            if keyword in searchable_text:
                visible_controls.append(ctrl)
                visible_sources.append(source)

        self._displayed_controls = visible_controls
        self._displayed_sources = visible_sources
        self._match_count_var.set(f"匹配 {len(visible_controls)} 项")

        self._tree.delete(*self._tree.get_children())
        for index, (ctrl, source) in enumerate(zip(visible_controls, visible_sources)):
            ctrl_type = (ctrl.get("inspectData") or {}).get("controlType", "") or ctrl.get("controlType", "")
            self._tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(
                    ctrl.get("id", ""),
                    ctrl.get("name", "") or ctrl.get("displayName", ""),
                    ctrl_type,
                    source,
                ),
            )
        self._show_empty_preview()

    def _on_search_changed(self, *_args):
        self._refresh_display()

    def _on_double_click(self, _event):
        self._on_confirm()

    def _show_empty_preview(self):
        for widget in self._detail_holder.winfo_children():
            widget.destroy()
        placeholder = tk.Label(
            self._detail_holder,
            text="👈 请在左侧列表中点击选择控件\n\n此处将实时透视回显定位方法、表达式、归属窗口与 Inspect 诊断属性。",
            font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
            justify=tk.CENTER,
            wraplength=300,
        )
        placeholder.pack(fill=tk.BOTH, expand=True, pady=40)

    def _on_tree_select(self, _event):
        sel = self._tree.selection()
        if not sel:
            self._show_empty_preview()
            return
        try:
            index = int(sel[0])
            if not (0 <= index < len(self._displayed_controls)):
                self._show_empty_preview()
                return
        except ValueError:
            self._show_empty_preview()
            return

        ctrl = self._displayed_controls[index]
        source = self._displayed_sources[index]

        for widget in self._detail_holder.winfo_children():
            widget.destroy()

        fields = [
            ("ID", str(ctrl.get("id", "")).strip() or "-"),
            ("名称", str(ctrl.get("name", "") or ctrl.get("displayName", "")).strip() or "-"),
            ("来源", "🌟 " + source),
            ("归属窗口", str(ctrl.get("windowTitle", "")).strip() or "(当前激活窗口)"),
            ("定位方法", str(ctrl.get("targetMethod", "") or ctrl.get("recommendedTargetMethod", "")).strip() or "-"),
            ("定位表达式", str(ctrl.get("targetValue", "") or ctrl.get("recommendedTargetValue", "")).strip() or "-"),
            ("控件类型", str((ctrl.get("inspectData") or {}).get("controlType", "") or ctrl.get("controlType", "")).strip() or "-"),
        ]

        for label_text, value in fields:
            row_frame = tk.Frame(self._detail_holder, bg=EDITOR_THEME.get("card", "#ffffff"))
            row_frame.pack(fill=tk.X, pady=3)
            tk.Label(
                row_frame,
                text=label_text + "：",
                font=("Microsoft YaHei UI", 8, "bold"),
                width=10,
                anchor="w",
                bg=EDITOR_THEME.get("card", "#ffffff"),
                fg=EDITOR_THEME.get("muted", "#64748b"),
            ).pack(side=tk.LEFT)
            val_entry = tk.Entry(
                row_frame,
                font=("Microsoft YaHei UI", 8),
                relief=tk.FLAT,
                bd=0,
                bg=EDITOR_THEME.get("bg", "#f8fafc"),
                highlightthickness=1,
                highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            )
            val_entry.insert(0, value)
            val_entry.configure(state="readonly")
            val_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)

    def _on_confirm(self):
        sel = self._tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先选择一个控件。", parent=self.window)
            return
        try:
            index = int(sel[0])
            if not (0 <= index < len(self._displayed_controls)):
                return
        except ValueError:
            return
        self.selected_control = self._displayed_controls[index]
        self.selected_source = self._displayed_sources[index]
        self.result = str(self.selected_control.get("id", "")).strip()
        self.window.destroy()

    def _on_cancel(self):
        self.result = None
        self.selected_control = None
        self.window.destroy()


_CONTROL_LIBRARY_CONTROLS_CACHE = None


def _load_control_library_controls(refresh=False):
    """枚举控件库目录下全部 JSON 的 controlDefinitions，合并返回控件列表（供锚点选择弹窗使用）。

    以 (id, windowTitle) 作为去重键，避免不同窗口下同名控件互相覆盖。
    """
    global _CONTROL_LIBRARY_CONTROLS_CACHE
    if _CONTROL_LIBRARY_CONTROLS_CACHE is not None and not refresh:
        return _CONTROL_LIBRARY_CONTROLS_CACHE
    os.makedirs(CONTROL_MAP_DIR, exist_ok=True)
    merged = {}
    for root_dir, _sub_dirs, dir_file_names in os.walk(CONTROL_MAP_DIR):
        for file_name in dir_file_names:
            if not file_name.lower().endswith(".json"):
                continue
            payload = load_json_file(os.path.join(root_dir, file_name))
            if not isinstance(payload, dict):
                continue
            control_definitions = payload.get("controlDefinitions")
            if not isinstance(control_definitions, list):
                continue
            for raw in control_definitions:
                if not isinstance(raw, dict):
                    continue
                control = normalize_control(raw, len(merged))
                dedup_key = "{}|{}".format(
                    str(control.get("id", "")).strip(),
                    str(control.get("windowTitle", "")).strip(),
                )
                if dedup_key and dedup_key not in merged:
                    merged[dedup_key] = control
    result = list(merged.values())
    _CONTROL_LIBRARY_CONTROLS_CACHE = result
    return result


class ControlEditorDialog:
    def __init__(self, parent, control=None, step_name="", default_window_title="", control_library=None, on_import_anchor_control=None):
        self.parent = parent
        self.result = None
        self.control = normalize_control(control or {}, 0)
        self.dirty = False
        self.applied = False
        self._loading = True
        self.on_import_anchor_control = on_import_anchor_control
        self.control_library = control_library or []

        self.window = _make_dialog_window(
            parent,
            f"细分控件编辑 - {step_name or '未命名步骤'}",
            1200, 880,
            min_width=980, min_height=720,
            on_close=self.on_cancel,
        )
        self.window.configure(bg=EDITOR_THEME.get("bg", "#f8fafc"))

        self.var_id = tk.StringVar(value=self.control.get("id", ""))
        self.var_name = tk.StringVar(value=self.control.get("name", ""))
        self.var_role = tk.StringVar(value=self.control.get("role", ""))
        self.var_enabled = tk.BooleanVar(value=bool(self.control.get("enabled", True)))
        self.var_window_title = tk.StringVar(value=self.control.get("windowTitle", "") or default_window_title)
        self.var_target_method = tk.StringVar(value=self.control.get("targetMethod", ""))
        self.var_target_value = tk.StringVar(value=self.control.get("targetValue", ""))
        self.var_template_key = tk.StringVar(value=self.control.get("templateKey", ""))
        self.var_ui_path = tk.StringVar(value=self.control.get("uiPath", ""))
        self.var_label_text = tk.StringVar(value=str(self.control.get("labelText", "")).strip())
        self.var_related_label_name = tk.StringVar(value=str(self.control.get("relatedLabelName", "")).strip())

        inspect_data = self.control.get("inspectData", {})
        self.var_how_found = tk.StringVar(value=inspect_data.get("howFound", ""))
        self.var_control_name = tk.StringVar(value=inspect_data.get("name", ""))
        self.var_class_name = tk.StringVar(value=inspect_data.get("className", ""))
        self.var_control_type = tk.StringVar(value=inspect_data.get("controlType", ""))
        self.var_localized_control_type = tk.StringVar(value=inspect_data.get("localizedControlType", ""))
        self.var_automation_id = tk.StringVar(value=inspect_data.get("automationId", ""))
        self.var_framework_id = tk.StringVar(value=inspect_data.get("frameworkId", ""))
        self.var_native_window_handle = tk.StringVar(value=inspect_data.get("nativeWindowHandle", ""))
        self.var_bounding_rectangle = tk.StringVar(value=inspect_data.get("boundingRectangle", ""))
        self.var_process_id = tk.StringVar(value=inspect_data.get("processId", ""))
        self.var_runtime_id = tk.StringVar(value=inspect_data.get("runtimeId", ""))
        self.var_is_enabled = tk.StringVar(value=inspect_data.get("isEnabled", ""))
        self.var_is_offscreen = tk.StringVar(value=inspect_data.get("isOffscreen", ""))
        self.var_is_keyboard_focusable = tk.StringVar(value=inspect_data.get("isKeyboardFocusable", ""))
        self.var_has_keyboard_focus = tk.StringVar(value=inspect_data.get("hasKeyboardFocus", ""))
        self.var_legacy_name = tk.StringVar(value=inspect_data.get("legacyName", ""))
        self.var_legacy_role = tk.StringVar(value=inspect_data.get("legacyRole", ""))
        self.var_legacy_state = tk.StringVar(value=inspect_data.get("legacyState", ""))
        self.var_provider_description = tk.StringVar(value=inspect_data.get("providerDescription", ""))
        self.var_first_child = tk.StringVar(value=inspect_data.get("firstChild", ""))
        self.var_last_child = tk.StringVar(value=inspect_data.get("lastChild", ""))
        self.var_next = tk.StringVar(value=inspect_data.get("next", ""))
        self.var_previous = tk.StringVar(value=inspect_data.get("previous", ""))

        tab_nav = self.control.get("tabNavigation", {}) or {}
        self.var_tab_anchor_id = tk.StringVar(value=tab_nav.get("anchorControlId", ""))
        self.var_tab_direction = tk.StringVar(value=tab_nav.get("direction", "forward"))
        self.var_tab_steps = tk.StringVar(value=str(tab_nav.get("steps", "")))
        self.var_prefer_tab_nav = tk.BooleanVar(value=bool(self.control.get("preferTabNavigation", False)))
        self.var_tab_click_twice = tk.BooleanVar(value=bool(tab_nav.get("clickTwiceToExpand", False)))

        self.status_var = tk.StringVar(value="可粘贴 Inspect 原始文本后自动解析。")
        self.validation_badge_var = tk.StringVar(value="正在分析定位...")
        self.search_filter_var = tk.StringVar(value="")
        self._parsed_field_rows = []

        self._build_ui()
        self._set_text(self.raw_text, self.control.get("rawInspectText", ""))
        self._set_text(self.aux_checks_text, "\n".join(self.control.get("auxChecks", [])))
        self._set_text(self.children_text, "\n".join(inspect_data.get("children", [])))
        self._set_text(self.ancestors_text, "\n".join(inspect_data.get("ancestors", [])))
        self._set_text(self.patterns_text, "\n".join(inspect_data.get("availablePatterns", [])))
        self._set_text(self.notes_text, self.control.get("notes", ""))
        if self.control.get("rawInspectText", "").strip():
            self.parse_current_text()
        self.dirty = False
        self._loading = False
        self._refresh_validation_state()

        # 监听字段变化
        for v in (self.var_id, self.var_name, self.var_target_method, self.var_target_value):
            v.trace_add("write", lambda *_: self._on_validation_field_change())

    def _build_ui(self):
        pal = EDITOR_THEME

        # ── 顶栏标题与实时校验 Badge ──
        header = tk.Frame(self.window, bg=pal.get("bg", "#f8fafc"), padx=14, pady=10)
        header.pack(fill=tk.X)

        title_col = tk.Frame(header, bg=pal.get("bg", "#f8fafc"))
        title_col.pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(
            title_col,
            text="🎯 细分控件精确配置",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=pal.get("bg", "#f8fafc"),
            fg=pal.get("text", "#1f2d3d"),
        ).pack(anchor="w")

        tk.Label(
            title_col,
            text="支持 Inspect 文本多源解析、属性筛选检索、推荐定位生成与即时验证。",
            font=("Microsoft YaHei UI", 9),
            bg=pal.get("bg", "#f8fafc"),
            fg=pal.get("muted", "#64748b"),
        ).pack(anchor="w", pady=(2, 0))

        self.validation_badge_label = tk.Label(
            header,
            textvariable=self.validation_badge_var,
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=pal.get("panel_soft", "#f1f5f9"),
            fg=pal.get("muted", "#64748b"),
            padx=10,
            pady=4,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        self.validation_badge_label.pack(side=tk.RIGHT, pady=4)

        # ── 操作工具栏 ──
        toolbar = tk.Frame(
            self.window,
            padx=14,
            pady=6,
            bg=pal.get("toolbar", "#ffffff"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        toolbar.pack(fill=tk.X)

        btn_specs = [
            ("📋 从剪贴板解析", self.import_from_clipboard, "secondary"),
            ("📂 从文本文件导入", self.import_from_file, "secondary"),
            ("🔄 重新解析", self.parse_current_text, "secondary"),
            ("💡 生成推荐定位", self.apply_recommended_locator, "secondary"),
            ("🧪 校验此定位", self.test_current_locator, "secondary"),
            ("📋 复制定位表达", self.copy_locator_expression, "secondary"),
            ("💾 保存当前控件", self.apply_changes, "secondary"),
            ("✔ 保存并关闭", self.on_confirm, "primary"),
        ]
        for text, cmd, tone in btn_specs:
            btn = wt_theme.create_flat_button(toolbar, text=text, command=cmd, tone=tone, padx=8, pady=3)
            btn.pack(side=tk.LEFT, padx=3)

        tk.Label(
            toolbar,
            textvariable=self.status_var,
            bg=pal.get("toolbar", "#ffffff"),
            fg=pal.get("muted", "#64748b"),
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT, padx=6)

        # ── 选项卡主体 ──
        body = tk.Frame(self.window, padx=12, pady=10, bg=pal.get("bg", "#f8fafc"))
        body.pack(fill=tk.BOTH, expand=True)

        notebook = ttk.Notebook(body)
        notebook.pack(fill=tk.BOTH, expand=True)

        basic_tab = tk.Frame(notebook, padx=10, pady=10, bg=pal.get("bg", "#f8fafc"))
        inspect_tab = tk.Frame(notebook, padx=10, pady=10, bg=pal.get("bg", "#f8fafc"))
        detail_tab = tk.Frame(notebook, padx=10, pady=10, bg=pal.get("bg", "#f8fafc"))

        notebook.add(basic_tab, text="  🎯 基本与定位  ")
        notebook.add(inspect_tab, text="  🔍 Inspect 解析  ")
        notebook.add(detail_tab, text="  🌲 结构与备注  ")

        # ────────── Tab 1: 基本与定位 ──────────
        # 1. 基本属性卡片
        basic_card = tk.LabelFrame(
            basic_tab,
            text="🏷️ 基本属性",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=8,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        basic_card.pack(fill=tk.X, pady=(0, 8))
        basic_card.columnconfigure(1, weight=1)
        basic_card.columnconfigure(3, weight=1)

        self._grid_label_entry(basic_card, "控件ID *", self.var_id, 0, 0)
        self._grid_label_entry(basic_card, "控件别名", self.var_name, 0, 2)
        self._grid_label_entry(basic_card, "控件用途", self.var_role, 1, 0)
        self._grid_label_entry(basic_card, "所属窗口", self.var_window_title, 1, 2)

        chk_wrap = tk.Frame(basic_card, bg=pal.get("card", "#ffffff"))
        chk_wrap.grid(row=2, column=0, columnspan=4, sticky="w", pady=(4, 2))
        tk.Checkbutton(
            chk_wrap,
            text="启用该控件定义",
            variable=self.var_enabled,
            bg=pal.get("card", "#ffffff"),
            activebackground=pal.get("card", "#ffffff"),
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)

        # 2. 定位策略卡片
        loc_card = tk.LabelFrame(
            basic_tab,
            text="🎯 定位策略 (带 * 至少保证 ID、target_method、target_value 完整)",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=8,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        loc_card.pack(fill=tk.X, pady=(0, 8))
        loc_card.columnconfigure(1, weight=1)
        loc_card.columnconfigure(3, weight=1)

        tk.Label(
            loc_card, text="target_method *", font=("Microsoft YaHei UI", 9),
            bg=pal.get("card", "#ffffff"), fg=pal.get("text", "#1f2d3d"),
        ).grid(row=0, column=0, sticky="w", pady=4)
        method_combo = ttk.Combobox(
            loc_card,
            textvariable=self.var_target_method,
            values=[
                "automation_id", "name", "class_name", "title",
                "control_type", "xpath", "coords", "template_key", "custom",
            ],
            font=("Microsoft YaHei UI", 9),
        )
        method_combo.grid(row=0, column=1, sticky="ew", padx=(8, 12), pady=4)

        self._grid_label_entry(loc_card, "target_value *", self.var_target_value, 0, 2)
        self._grid_label_entry(loc_card, "templateKey", self.var_template_key, 1, 0)
        self._grid_label_entry(loc_card, "UIPath", self.var_ui_path, 1, 2)
        self._grid_label_entry(loc_card, "labelText", self.var_label_text, 2, 0)
        self._grid_label_entry(loc_card, "relatedLabelName", self.var_related_label_name, 2, 2)

        loc_tools = tk.Frame(loc_card, bg=pal.get("card", "#ffffff"))
        loc_tools.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 2))
        wt_theme.create_flat_button(
            loc_tools, text="💡 生成推荐定位", command=self.apply_recommended_locator, tone="secondary", padx=8, pady=2
        ).pack(side=tk.LEFT, padx=(0, 6))
        wt_theme.create_flat_button(
            loc_tools, text="🧪 校验此定位", command=self.test_current_locator, tone="secondary", padx=8, pady=2
        ).pack(side=tk.LEFT, padx=(0, 6))
        wt_theme.create_flat_button(
            loc_tools, text="📋 复制定位表达", command=self.copy_locator_expression, tone="ghost", padx=8, pady=2
        ).pack(side=tk.LEFT)

        # 3. Tab 导航降级配置卡片
        tab_nav_frame = tk.LabelFrame(
            basic_tab,
            text="⌨️ Tab 导航降级（可选，用于 WPF 不可见输入框或键盘直达场景）",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=8,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        tab_nav_frame.pack(fill=tk.X, pady=(0, 4))
        tab_nav_frame.columnconfigure(1, weight=1)
        tab_nav_frame.columnconfigure(4, weight=1)

        self._grid_label_entry(tab_nav_frame, "锚点控件ID", self.var_tab_anchor_id, 0, 0)
        wt_theme.create_flat_button(
            tab_nav_frame, text="🔍 选择锚点...", command=self._pick_anchor_control, tone="secondary", padx=8, pady=2
        ).grid(row=0, column=2, sticky="w", padx=(4, 12), pady=4)
        self._grid_label_entry(tab_nav_frame, "Tab步数", self.var_tab_steps, 0, 3)

        tk.Label(
            tab_nav_frame, text="Tab方向", font=("Microsoft YaHei UI", 9),
            bg=pal.get("card", "#ffffff"), fg=pal.get("text", "#1f2d3d"),
        ).grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(
            tab_nav_frame,
            textvariable=self.var_tab_direction,
            values=["forward", "backward"],
            state="readonly",
            width=12,
            font=("Microsoft YaHei UI", 9),
        ).grid(row=1, column=1, sticky="w", padx=(8, 12), pady=4)

        opt_box = tk.Frame(tab_nav_frame, bg=pal.get("card", "#ffffff"))
        opt_box.grid(row=2, column=0, columnspan=5, sticky="w", pady=(4, 2))
        tk.Checkbutton(
            opt_box,
            text="优先使用 Tab 导航（跳过常规定位尝试）",
            variable=self.var_prefer_tab_nav,
            bg=pal.get("card", "#ffffff"),
            activebackground=pal.get("card", "#ffffff"),
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 16))
        tk.Checkbutton(
            opt_box,
            text="锚点点击会折叠，需再点一次展开",
            variable=self.var_tab_click_twice,
            bg=pal.get("card", "#ffffff"),
            activebackground=pal.get("card", "#ffffff"),
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)

        # ────────── Tab 2: Inspect 解析 ──────────
        inspect_tab.columnconfigure(0, weight=1)
        inspect_tab.columnconfigure(1, weight=1)
        inspect_tab.rowconfigure(0, weight=1)

        left = tk.LabelFrame(
            inspect_tab,
            text="📄 Inspect 原始文本",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=10,
            pady=8,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))

        self.raw_text = scrolledtext.ScrolledText(
            left, wrap=tk.WORD, font=("Consolas", 9), bd=0, highlightthickness=1, highlightbackground=pal.get("border", "#e2e8f0")
        )
        self.raw_text.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        left_tools = tk.Frame(left, bg=pal.get("card", "#ffffff"))
        left_tools.pack(fill=tk.X)
        wt_theme.create_flat_button(
            left_tools, text="📋 从剪贴板粘贴并解析", command=self.import_from_clipboard, tone="secondary", padx=8, pady=2
        ).pack(side=tk.LEFT, padx=(0, 6))
        wt_theme.create_flat_button(
            left_tools, text="🗑️ 清空文本", command=lambda: self._set_text(self.raw_text, ""), tone="ghost", padx=8, pady=2
        ).pack(side=tk.LEFT)

        right = tk.LabelFrame(
            inspect_tab,
            text="🔍 解析结果与关键字段",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=10,
            pady=8,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        search_bar = tk.Frame(right, bg=pal.get("card", "#ffffff"))
        search_bar.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            search_bar, text="🔍 筛选属性:", font=("Microsoft YaHei UI", 9),
            bg=pal.get("card", "#ffffff"), fg=pal.get("muted", "#64748b"),
        ).pack(side=tk.LEFT, padx=(0, 6))
        search_entry = tk.Entry(
            search_bar,
            textvariable=self.search_filter_var,
            font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
            bg=pal.get("bg", "#f8fafc"),
        )
        search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.search_filter_var.trace_add("write", self._filter_parsed_fields)

        parsed_canvas = tk.Canvas(right, highlightthickness=0, borderwidth=0, bg=pal.get("card", "#ffffff"))
        parsed_scrollbar = ttk.Scrollbar(right, orient="vertical", command=parsed_canvas.yview)
        parsed_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        parsed_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        parsed_canvas.configure(yscrollcommand=parsed_scrollbar.set)
        parsed_inner = tk.Frame(parsed_canvas, bg=pal.get("card", "#ffffff"))
        parsed_window = parsed_canvas.create_window((0, 0), window=parsed_inner, anchor="nw")

        def on_parsed_inner_configure(_event=None):
            parsed_canvas.configure(scrollregion=parsed_canvas.bbox("all"))

        def on_parsed_canvas_configure(event=None):
            if event is not None:
                parsed_canvas.itemconfigure(parsed_window, width=event.width)

        parsed_inner.bind("<Configure>", on_parsed_inner_configure)
        parsed_canvas.bind("<Configure>", on_parsed_canvas_configure)

        field_specs = [
            ("How found", self.var_how_found, None),
            ("Name", self.var_control_name, "name"),
            ("ClassName", self.var_class_name, "class_name"),
            ("ControlType", self.var_control_type, "control_type"),
            ("LocalizedType", self.var_localized_control_type, None),
            ("AutomationId", self.var_automation_id, "automation_id"),
            ("FrameworkId", self.var_framework_id, None),
            ("NativeHandle", self.var_native_window_handle, "handle"),
            ("BoundingRect", self.var_bounding_rectangle, None),
            ("ProcessId", self.var_process_id, None),
            ("RuntimeId", self.var_runtime_id, None),
            ("IsEnabled", self.var_is_enabled, None),
            ("IsOffscreen", self.var_is_offscreen, None),
            ("KeyboardFocusable", self.var_is_keyboard_focusable, None),
            ("HasKeyboardFocus", self.var_has_keyboard_focus, None),
            ("LegacyName", self.var_legacy_name, "name"),
            ("LegacyRole", self.var_legacy_role, None),
            ("LegacyState", self.var_legacy_state, None),
            ("FirstChild", self.var_first_child, None),
            ("LastChild", self.var_last_child, None),
            ("Next", self.var_next, None),
            ("Previous", self.var_previous, None),
            ("ProviderDescription", self.var_provider_description, None),
        ]

        self._parsed_field_rows = []
        for label, var, qm in field_specs:
            row_frame = tk.Frame(parsed_inner, bg=pal.get("card", "#ffffff"), pady=2)
            row_frame.pack(fill=tk.X)
            lbl = tk.Label(
                row_frame, text=label, font=("Microsoft YaHei UI", 8),
                width=16, anchor="w", bg=pal.get("card", "#ffffff"), fg=pal.get("muted", "#64748b"),
            )
            lbl.pack(side=tk.LEFT)
            ent = tk.Entry(
                row_frame, textvariable=var, font=("Consolas", 8),
                relief=tk.FLAT, bd=0, highlightthickness=1,
                highlightbackground=pal.get("border", "#e2e8f0"),
                bg=pal.get("bg", "#f8fafc"),
            )
            ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 4))
            if qm:
                qbtn = tk.Button(
                    row_frame,
                    text="填入定位",
                    font=("Microsoft YaHei UI", 7),
                    bg=pal.get("panel_soft", "#f8fafc"),
                    fg=pal.get("primary", "#2563eb"),
                    relief=tk.FLAT,
                    bd=0,
                    padx=4,
                    pady=0,
                    cursor="hand2",
                    highlightthickness=1,
                    highlightbackground=pal.get("border", "#e2e8f0"),
                    command=lambda m=qm, v=var: self._apply_field_to_locator(m, v.get()),
                )
                qbtn.pack(side=tk.RIGHT)
            self._parsed_field_rows.append((row_frame, label, var))

        # ────────── Tab 3: 结构与备注 ──────────
        detail = tk.LabelFrame(
            detail_tab,
            text="🌲 辅助判断 / 结构信息 / 备注",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=10,
            pady=10,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        detail.pack(fill=tk.BOTH, expand=True)
        detail.columnconfigure(0, weight=1)
        detail.columnconfigure(1, weight=1)
        detail.columnconfigure(2, weight=1)

        tk.Label(detail, text="辅助判断条件（每行一条）", font=("Microsoft YaHei UI", 9), bg=pal.get("card", "#ffffff"), fg=pal.get("text", "#1f2d3d")).grid(row=0, column=0, sticky="w")
        tk.Label(detail, text="Children (子控件)", font=("Microsoft YaHei UI", 9), bg=pal.get("card", "#ffffff"), fg=pal.get("text", "#1f2d3d")).grid(row=0, column=1, sticky="w")
        tk.Label(detail, text="Ancestors (父级链)", font=("Microsoft YaHei UI", 9), bg=pal.get("card", "#ffffff"), fg=pal.get("text", "#1f2d3d")).grid(row=0, column=2, sticky="w")

        self.aux_checks_text = scrolledtext.ScrolledText(detail, height=10, wrap=tk.WORD, font=("Consolas", 9), bd=0, highlightthickness=1, highlightbackground=pal.get("border", "#e2e8f0"))
        self.aux_checks_text.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(4, 8))
        self.children_text = scrolledtext.ScrolledText(detail, height=10, wrap=tk.WORD, font=("Consolas", 9), bd=0, highlightthickness=1, highlightbackground=pal.get("border", "#e2e8f0"))
        self.children_text.grid(row=1, column=1, sticky="nsew", padx=(0, 8), pady=(4, 8))
        self.ancestors_text = scrolledtext.ScrolledText(detail, height=10, wrap=tk.WORD, font=("Consolas", 9), bd=0, highlightthickness=1, highlightbackground=pal.get("border", "#e2e8f0"))
        self.ancestors_text.grid(row=1, column=2, sticky="nsew", pady=(4, 8))

        tk.Label(detail, text="Available Patterns", font=("Microsoft YaHei UI", 9), bg=pal.get("card", "#ffffff"), fg=pal.get("text", "#1f2d3d")).grid(row=2, column=0, sticky="w")
        tk.Label(detail, text="控件备注说明", font=("Microsoft YaHei UI", 9), bg=pal.get("card", "#ffffff"), fg=pal.get("text", "#1f2d3d")).grid(row=2, column=1, sticky="w")
        self.patterns_text = scrolledtext.ScrolledText(detail, height=8, wrap=tk.WORD, font=("Consolas", 9), bd=0, highlightthickness=1, highlightbackground=pal.get("border", "#e2e8f0"))
        self.patterns_text.grid(row=3, column=0, sticky="nsew", padx=(0, 8), pady=(4, 0))
        self.notes_text = scrolledtext.ScrolledText(detail, height=8, wrap=tk.WORD, font=("Microsoft YaHei UI", 9), bd=0, highlightthickness=1, highlightbackground=pal.get("border", "#e2e8f0"))
        self.notes_text.grid(row=3, column=1, columnspan=2, sticky="nsew", pady=(4, 0))
        detail.rowconfigure(1, weight=1)
        detail.rowconfigure(3, weight=1)

        # ── 底部操作按钮 ──
        footer = tk.Frame(self.window, padx=14, pady=10, bg=pal.get("bg", "#f8fafc"))
        footer.pack(fill=tk.X, side=tk.BOTTOM)

        wt_theme.create_flat_button(
            footer, text="取消 (Esc)", command=self.on_cancel, tone="ghost", padx=16, pady=5
        ).pack(side=tk.RIGHT, padx=(8, 0))
        wt_theme.create_flat_button(
            footer, text="仅保存修改 (Ctrl+S)", command=self.apply_changes, tone="secondary", padx=16, pady=5
        ).pack(side=tk.RIGHT, padx=(8, 0))
        wt_theme.create_flat_button(
            footer, text="保存并关闭 (Enter)", command=self.on_confirm, tone="primary", padx=20, pady=5
        ).pack(side=tk.RIGHT)

        self.window.bind("<Control-s>", lambda _e: self.apply_changes())
        self.window.bind("<Escape>", lambda _e: self.on_cancel())

    def _filter_parsed_fields(self, *args):
        query = (self.search_filter_var.get() or "").strip().lower()
        for row_frame, label, var in getattr(self, "_parsed_field_rows", []):
            val_text = str(var.get() or "").lower()
            if not query or query in label.lower() or query in val_text:
                row_frame.pack(fill=tk.X, pady=2)
            else:
                row_frame.pack_forget()

    def _apply_field_to_locator(self, method, value):
        val = str(value or "").strip()
        if not val:
            if hasattr(self, "status_var") and self.status_var:
                self.status_var.set("字段内容为空，无法填入定位。")
            return
        self.var_target_method.set(method)
        self.var_target_value.set(val)
        self._mark_dirty(f"已将 [{method}] 设为目标定位：{val}")
        self._refresh_validation_state()

    def test_current_locator(self):
        """打开定位检验透视台实时测试当前定位。"""
        try:
            ctl = self.build_control()
        except ValueError as exc:
            messagebox.showinfo("无法测试", f"当前定位信息不完整，无法执行检验：\n{exc}", parent=self.window)
            return
        try:
            dialog = ControlLocatorTesterDialog(self.window, initial_control=ctl)
            self.window.wait_window(dialog.window)
        except Exception as exc:
            messagebox.showerror("启动测试失败", f"无法启动定位检验透视台：\n{exc}", parent=self.window)

    def copy_locator_expression(self):
        """复制当前定位表达式到剪贴板。"""
        method = self.var_target_method.get().strip()
        val = self.var_target_value.get().strip()
        if not method and not val:
            messagebox.showinfo("提示", "当前 target_method 与 target_value 为空。", parent=self.window)
            return
        expr = f"{method}:{val}" if method else val
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(expr)
            if hasattr(self, "status_var") and self.status_var:
                self.status_var.set(f"已复制定位表达式到剪贴板：{expr}")
        except Exception as exc:
            messagebox.showerror("复制失败", f"无法写入剪贴板：{exc}", parent=self.window)

    def _on_validation_field_change(self):
        if not self._loading:
            self.dirty = True
        self._refresh_validation_state()

    def _refresh_validation_state(self):
        pal = EDITOR_THEME
        ctl_id = (self.var_id.get() or "").strip() if hasattr(self, "var_id") and self.var_id else ""
        method = (self.var_target_method.get() or "").strip() if hasattr(self, "var_target_method") and self.var_target_method else ""
        val = (self.var_target_value.get() or "").strip() if hasattr(self, "var_target_value") and self.var_target_value else ""

        if not ctl_id:
            msg = "❌ 缺少控件ID *"
            fg = pal.get("danger", "#ef4444")
            bg = pal.get("danger_soft", "#fee2e2")
        elif not method:
            msg = "⚠ 缺少 target_method *"
            fg = pal.get("warning_text", "#92400e")
            bg = pal.get("warning_soft", "#fef3c7")
        elif not val:
            msg = "⚠ 缺少 target_value *"
            fg = pal.get("warning_text", "#92400e")
            bg = pal.get("warning_soft", "#fef3c7")
        else:
            msg = f"✓ 定位三要素完整 [{method}]"
            fg = pal.get("success_text", "#065f46")
            bg = pal.get("success_soft", "#d1fae5")

        if hasattr(self, "validation_badge_var") and self.validation_badge_var:
            self.validation_badge_var.set(msg)
        if hasattr(self, "validation_badge_label") and self.validation_badge_label:
            try:
                self.validation_badge_label.config(fg=fg, bg=bg)
            except Exception:
                pass

    def _pick_anchor_control(self):
        """弹出锚点选择弹窗；选中控件库控件时先导入当前流程步骤再填入锚点ID。"""
        picker = ControlPickerDialog(
            self.window,
            controls=self.control_library,
            library_controls=_load_control_library_controls(),
        )
        self.window.wait_window(picker.window)
        if picker.selected_control is None:
            return
        if picker.selected_source == "控件库":
            if not self.on_import_anchor_control:
                messagebox.showwarning("提示", "当前上下文不支持将控件库控件导入为锚点。", parent=self.window)
                return
            imported = self.on_import_anchor_control(picker.selected_control)
            if not imported:
                return
            picker.selected_control = imported
            picker.result = str(imported.get("id", "")).strip()
        if picker.result:
            self.var_tab_anchor_id.set(picker.result)
            self._mark_dirty(f"已选择锚点控件：{picker.result}")

    def _grid_label_entry(self, parent, label, variable, row, column, colspan=1):
        _grid_label_entry(parent, label, variable, row, column, colspan=colspan)

    @staticmethod
    def _set_text(widget, value):
        _set_text(widget, value)

    @staticmethod
    def _get_text(widget):
        return _get_text(widget)

    def import_from_clipboard(self):
        try:
            raw_text = self.parent.clipboard_get()
        except Exception as exc:
            messagebox.showerror("读取失败", f"读取剪贴板失败：\n{exc}", parent=self.window)
            return
        self._set_text(self.raw_text, raw_text)
        self.parse_current_text()

    def import_from_file(self):
        file_path = filedialog.askopenfilename(
            title="选择 Inspect 文本文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as file_obj:
                raw_text = file_obj.read()
        except OSError as exc:
            messagebox.showerror("读取失败", f"读取文件失败：\n{exc}", parent=self.window)
            return
        self._set_text(self.raw_text, raw_text)
        self.parse_current_text()

    def apply_recommended_locator(self):
        locator_method, locator_value, _, _ = build_locator_recommendation(
            {
                "automationId": self.var_automation_id.get().strip(),
                "name": self.var_control_name.get().strip(),
                "className": self.var_class_name.get().strip(),
                "controlType": self.var_control_type.get().strip(),
                "localizedControlType": self.var_localized_control_type.get().strip(),
            }
        )
        self.var_target_method.set(locator_method)
        self.var_target_value.set(locator_value)
        if not self.var_ui_path.get().strip():
            self.var_ui_path.set(self.var_control_name.get().strip())
        self._mark_dirty("已根据当前字段生成推荐定位。")
        self._refresh_validation_state()

    def parse_current_text(self):
        raw_text = self._get_text(self.raw_text)
        if not raw_text:
            messagebox.showinfo("提示", "请先粘贴或导入 Inspect 原始文本。", parent=self.window)
            return
        parsed = parse_inspect_text(raw_text)
        self.var_how_found.set(parsed.get("howFound", ""))
        self.var_control_name.set(parsed.get("name", ""))
        self.var_class_name.set(parsed.get("className", ""))
        self.var_control_type.set(parsed.get("controlType", ""))
        self.var_localized_control_type.set(parsed.get("localizedControlType", ""))
        self.var_automation_id.set(parsed.get("automationId", ""))
        self.var_framework_id.set(parsed.get("frameworkId", ""))
        self.var_native_window_handle.set(parsed.get("nativeWindowHandle", ""))
        self.var_bounding_rectangle.set(parsed.get("boundingRectangle", ""))
        self.var_process_id.set(parsed.get("processId", ""))
        self.var_runtime_id.set(parsed.get("runtimeId", ""))
        self.var_is_enabled.set(parsed.get("isEnabled", ""))
        self.var_is_offscreen.set(parsed.get("isOffscreen", ""))
        self.var_is_keyboard_focusable.set(parsed.get("isKeyboardFocusable", ""))
        self.var_has_keyboard_focus.set(parsed.get("hasKeyboardFocus", ""))
        self.var_legacy_name.set(parsed.get("legacyName", ""))
        self.var_legacy_role.set(parsed.get("legacyRole", ""))
        self.var_legacy_state.set(parsed.get("legacyState", ""))
        self.var_provider_description.set(parsed.get("providerDescription", ""))
        self.var_first_child.set(parsed.get("firstChild", ""))
        self.var_last_child.set(parsed.get("lastChild", ""))
        self.var_next.set(parsed.get("next", ""))
        self.var_previous.set(parsed.get("previous", ""))
        self._set_text(self.children_text, "\n".join(parsed.get("children", [])))
        self._set_text(self.ancestors_text, "\n".join(parsed.get("ancestors", [])))
        self._set_text(self.patterns_text, "\n".join(parsed.get("availablePatterns", [])))
        if not self.var_name.get().strip():
            self.var_name.set(parsed.get("name", "") or "新控件")
        if not self.var_target_method.get().strip():
            self.var_target_method.set(parsed.get("recommendedTargetMethod", ""))
        if not self.var_target_value.get().strip():
            self.var_target_value.set(parsed.get("recommendedTargetValue", ""))
        if not self._get_text(self.aux_checks_text):
            self._set_text(self.aux_checks_text, "\n".join(parsed.get("suggestedAuxChecks", [])))
        if not self.var_ui_path.get().strip():
            self.var_ui_path.set(parsed.get("name", ""))
        self._mark_dirty("已解析 Inspect 文本并回填关键字段。")
        self._refresh_validation_state()

    def build_control(self):
        raw_inspect_text = self._get_text(self.raw_text)
        parsed = parse_inspect_text(raw_inspect_text) if raw_inspect_text else {}
        control_id = self.var_id.get().strip() or self.var_name.get().strip() or "control_new"
        target_method = self.var_target_method.get().strip()
        target_value = self.var_target_value.get().strip()
        if not control_id:
            raise ValueError("控件ID * 不能为空。")
        if not target_method:
            raise ValueError("target_method * 不能为空。")
        if not target_value:
            raise ValueError("target_value * 不能为空。")
        inspect_data = {
            "howFound": self.var_how_found.get().strip(),
            "name": self.var_control_name.get().strip(),
            "controlType": self.var_control_type.get().strip(),
            "localizedControlType": self.var_localized_control_type.get().strip(),
            "boundingRectangle": self.var_bounding_rectangle.get().strip(),
            "isEnabled": self.var_is_enabled.get().strip(),
            "isOffscreen": self.var_is_offscreen.get().strip(),
            "isKeyboardFocusable": self.var_is_keyboard_focusable.get().strip(),
            "hasKeyboardFocus": self.var_has_keyboard_focus.get().strip(),
            "processId": self.var_process_id.get().strip(),
            "runtimeId": self.var_runtime_id.get().strip(),
            "frameworkId": self.var_framework_id.get().strip(),
            "className": self.var_class_name.get().strip(),
            "automationId": self.var_automation_id.get().strip(),
            "nativeWindowHandle": self.var_native_window_handle.get().strip(),
            "providerDescription": self.var_provider_description.get().strip(),
            "legacyName": self.var_legacy_name.get().strip(),
            "legacyRole": self.var_legacy_role.get().strip(),
            "legacyState": self.var_legacy_state.get().strip(),
            "firstChild": self.var_first_child.get().strip(),
            "lastChild": self.var_last_child.get().strip(),
            "next": self.var_next.get().strip(),
            "previous": self.var_previous.get().strip(),
            "children": [line.strip() for line in self._get_text(self.children_text).splitlines() if line.strip()],
            "ancestors": [line.strip() for line in self._get_text(self.ancestors_text).splitlines() if line.strip()],
            "availablePatterns": [line.strip() for line in self._get_text(self.patterns_text).splitlines() if line.strip()],
            "recommendedTargetMethod": parsed.get("recommendedTargetMethod", ""),
            "recommendedTargetValue": parsed.get("recommendedTargetValue", ""),
        }
        return normalize_control(
            {
                "id": control_id,
                "name": self.var_name.get().strip() or inspect_data.get("name", "") or "新控件",
                "role": self.var_role.get().strip(),
                "enabled": bool(self.var_enabled.get()),
                "windowTitle": self.var_window_title.get().strip(),
                "targetMethod": target_method,
                "targetValue": target_value,
                "templateKey": self.var_template_key.get().strip(),
                "uiPath": self.var_ui_path.get().strip(),
                "labelText": self.var_label_text.get().strip(),
                "relatedLabelName": self.var_related_label_name.get().strip(),
                "notes": self._get_text(self.notes_text),
                "rawInspectText": raw_inspect_text,
                "auxChecks": [line.strip() for line in self._get_text(self.aux_checks_text).splitlines() if line.strip()],
                "inspectData": inspect_data,
                **(
                    {"tabNavigation": {
                        "anchorControlId": self.var_tab_anchor_id.get().strip(),
                        **({"direction": self.var_tab_direction.get().strip()} if self.var_tab_direction.get().strip() else {}),
                        **(
                            {"steps": int(self.var_tab_steps.get().strip())}
                            if self.var_tab_steps.get().strip().isdigit() else {}
                        ),
                        **({"clickTwiceToExpand": True} if self.var_tab_click_twice.get() else {}),
                    }}
                    if self.var_tab_anchor_id.get().strip() else {}
                ),
                **({"preferTabNavigation": True} if self.var_prefer_tab_nav.get() else {}),
            },
            0,
        )

    def on_confirm(self):
        # 只有保存成功才关窗：校验失败时 apply_changes 会提示并返回 False
        if self.apply_changes(close_after=False):
            self.window.destroy()

    def _mark_dirty(self, message):
        if self._loading:
            if hasattr(self, "status_var") and self.status_var:
                self.status_var.set(message)
            return
        self.dirty = True
        if hasattr(self, "status_var") and self.status_var:
            self.status_var.set(message)

    def apply_changes(self, close_after=False):
        """保存当前控件。校验失败时提示用户并返回 False（不再静默无反应）。"""
        try:
            self.result = self.build_control()
        except ValueError as exc:
            messagebox.showerror("保存失败", str(exc), parent=self.window)
            return False
        self.control = self.result
        self.dirty = False
        self.applied = True
        if hasattr(self, "status_var") and self.status_var:
            self.status_var.set("已保存当前控件修改。")
        if hasattr(self, "_refresh_validation_state"):
            self._refresh_validation_state()
        if close_after:
            self.window.destroy()
        return True

    def on_cancel(self):
        if self.dirty:
            answer = messagebox.askyesnocancel(
                "保存控件修改",
                "当前控件有未保存修改，是否先保存再关闭？",
                parent=self.window,
            )
            if answer is None:
                return
            if answer:
                self.apply_changes(close_after=True)
                return
        if not self.applied:
            self.result = None
        self.window.destroy()


class SemiAutoInspectCollectorDialog:
    def __init__(self, parent, existing_controls=None, step_name="", default_window_title=""):
        self.parent = parent
        self.result = None
        self.step_name = step_name
        self.default_window_title = default_window_title
        self.existing_controls = existing_controls or []
        self.captured_controls = []
        self._known_raw_texts = {
            str(control.get("rawInspectText", "")).strip()
            for control in self.existing_controls
            if str(control.get("rawInspectText", "")).strip()
        }
        self._monitoring = False
        self._last_clipboard_text = ""
        self._interactive_monitoring = False
        self._mouse_listener = None
        self._keyboard_listener = None
        self._win32_hook = None
        self._target_window = None
        self._target_backend = None
        self._last_click_ts = 0.0
        self._last_hotkey_ts = 0.0
        self.var_target_window = tk.StringVar(value=self.default_window_title or "目标软件")
        self.var_backend = tk.StringVar(value="uia")

        self._tips_expanded = False
        self.listener_badge_var = tk.StringVar(value="○ 监听待命")
        self.candidate_filter_var = tk.StringVar(value="")
        self.candidate_count_var = tk.StringVar(value="候选控件 (0 个)")
        self.current_candidate_title_var = tk.StringVar(value="暂无选中候选")

        self.window = _make_dialog_window(
            parent,
            f"半自动采集 - {step_name or '未命名步骤'}",
            1040, 780,
            min_width=880, min_height=640,
            on_close=self.on_cancel,
        )
        self.window.configure(bg=EDITOR_THEME.get("bg", "#f8fafc"))

        self.status_var = tk.StringVar(
            value="推荐操作：使用“交互采集”，直接点击目标软件控件即可抓取（不依赖复制）。"
        )

        self._build_ui()
        self._update_listener_badge()
        self.window.protocol("WM_DELETE_WINDOW", self.on_cancel)

    def _build_ui(self):
        pal = EDITOR_THEME

        # ── 顶栏标题与监听状态 Badge ──
        header = tk.Frame(self.window, bg=pal.get("bg", "#f8fafc"), padx=14, pady=10)
        header.pack(fill=tk.X)

        title_col = tk.Frame(header, bg=pal.get("bg", "#f8fafc"))
        title_col.pack(side=tk.LEFT, fill=tk.Y)

        tk.Label(
            title_col,
            text="⚡ 半自动控件采集器",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=pal.get("bg", "#f8fafc"),
            fg=pal.get("text", "#1f2d3d"),
        ).pack(anchor="w")

        tk.Label(
            title_col,
            text="支持交互点击抓取与剪贴板监听，自动解析 UIA 属性、生成推荐定位并去重。",
            font=("Microsoft YaHei UI", 9),
            bg=pal.get("bg", "#f8fafc"),
            fg=pal.get("muted", "#64748b"),
        ).pack(anchor="w", pady=(2, 0))

        self.listener_badge_label = tk.Label(
            header,
            textvariable=self.listener_badge_var,
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=pal.get("panel_soft", "#f1f5f9"),
            fg=pal.get("muted", "#64748b"),
            padx=10,
            pady=4,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        self.listener_badge_label.pack(side=tk.RIGHT, pady=4)

        # ── 折叠式采集指南 ──
        self.tips_frame = tk.LabelFrame(
            self.window,
            text=" 💡 采集指南与模式说明 ",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=6,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("primary", "#2563eb"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        self.tips_frame.pack(fill=tk.X, padx=14, pady=(0, 6))

        tips_header = tk.Frame(self.tips_frame, bg=pal.get("card", "#ffffff"))
        tips_header.pack(fill=tk.X)

        self.tips_summary_label = tk.Label(
            tips_header,
            text="提示：首选“交互采集”，直接点击目标控件或按 F8 抓取；也可在 Inspect 中复制文本自动捕获。",
            font=("Microsoft YaHei UI", 9),
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("muted", "#64748b"),
            anchor="w",
        )
        self.tips_summary_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.tips_toggle_btn = tk.Button(
            tips_header,
            text="▼ 展开指南",
            command=self._toggle_tips,
            bg=pal.get("panel_soft", "#f8fafc"),
            fg=pal.get("primary", "#2563eb"),
            relief=tk.FLAT,
            bd=0,
            padx=8,
            pady=1,
            cursor="hand2",
            font=("Microsoft YaHei UI", 8),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        self.tips_toggle_btn.pack(side=tk.RIGHT)

        self.tips_body_frame = tk.Frame(self.tips_frame, bg=pal.get("card", "#ffffff"), pady=6)
        tk.Label(
            self.tips_body_frame,
            text=(
                "【推荐模式：交互采集】（无需切换窗口复制文本）\n"
                "1. 在下方填写目标窗口关键字（如 目标软件主窗口）。\n"
                "2. 点击“开始交互点击采集”。\n"
                "3. 直接在目标软件里点击控件，候选列表会自动增加；需要不触发点击时可将鼠标悬停并按 F8。\n\n"
                "【备用模式：剪贴板监听】（适合 Inspect / Accessibility Insights 工具用户）\n"
                "1. 点击“开始监听剪贴板”。\n"
                "2. 在 Inspect 中选中控件并复制属性文本 (Ctrl+C)，采集器会自动拦截、解析并去重。"
            ),
            justify=tk.LEFT,
            anchor="w",
            font=("Microsoft YaHei UI", 8),
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("muted", "#64748b"),
        ).pack(fill=tk.X)

        # ── 交互目标窗口配置卡片 ──
        config_card = tk.LabelFrame(
            self.window,
            text=" 🎯 交互目标窗口配置 ",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=6,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        config_card.pack(fill=tk.X, padx=14, pady=(0, 6))

        tk.Label(
            config_card, text="目标窗口关键字:", font=("Microsoft YaHei UI", 9),
            bg=pal.get("card", "#ffffff"), fg=pal.get("text", "#1f2d3d"),
        ).pack(side=tk.LEFT, padx=(0, 6))

        target_ent = tk.Entry(
            config_card,
            textvariable=self.var_target_window,
            font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
            bg=pal.get("bg", "#f8fafc"),
        )
        target_ent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        if self.default_window_title:
            def _fill_default():
                self.var_target_window.set(self.default_window_title)
            tk.Button(
                config_card,
                text="填入当前窗口",
                command=_fill_default,
                bg=pal.get("panel_soft", "#f8fafc"),
                fg=pal.get("text", "#1f2d3d"),
                relief=tk.FLAT, bd=0, padx=6, pady=1,
                cursor="hand2", font=("Microsoft YaHei UI", 8),
                highlightthickness=1, highlightbackground=pal.get("border", "#e2e8f0"),
            ).pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(
            config_card, text="backend:", font=("Microsoft YaHei UI", 9),
            bg=pal.get("card", "#ffffff"), fg=pal.get("muted", "#64748b"),
        ).pack(side=tk.LEFT, padx=(4, 6))
        ttk.Combobox(
            config_card,
            textvariable=self.var_backend,
            values=["uia", "win32"],
            width=8,
            state="readonly",
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)

        # ── 工具栏 ──
        toolbar = tk.Frame(
            self.window,
            padx=14,
            pady=6,
            bg=pal.get("toolbar", "#ffffff"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        toolbar.pack(fill=tk.X)

        btn_specs = [
            ("▶ 交互点击采集", self.start_interactive_monitor, "primary"),
            ("⏹ 停止交互", self.stop_interactive_monitor, "ghost"),
            ("📋 监听剪贴板", self.start_monitor, "secondary"),
            ("⏹ 停止监听", self.stop_monitor, "ghost"),
            ("⚡ 抓取一次", self.capture_once, "secondary"),
            ("✏️ 编辑候选", self.edit_selected, "secondary"),
            ("🗑️ 删除候选", self.delete_selected, "danger"),
            ("🧪 检验定位", self.test_selected_locator, "secondary"),
        ]
        for text, cmd, tone in btn_specs:
            btn = wt_theme.create_flat_button(toolbar, text=text, command=cmd, tone=tone, padx=8, pady=3)
            btn.pack(side=tk.LEFT, padx=3)

        tk.Label(
            toolbar,
            textvariable=self.status_var,
            bg=pal.get("toolbar", "#ffffff"),
            fg=pal.get("muted", "#64748b"),
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT, padx=6)

        # ── 主体面板（左树右详情） ──
        body = tk.Frame(self.window, padx=14, pady=8, bg=pal.get("bg", "#f8fafc"))
        body.pack(fill=tk.BOTH, expand=True)

        left = tk.LabelFrame(
            body,
            text=" 📋 候选控件列表 ",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=10,
            pady=8,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 候选筛选框
        filter_bar = tk.Frame(left, bg=pal.get("card", "#ffffff"))
        filter_bar.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            filter_bar, textvariable=self.candidate_count_var, font=("Microsoft YaHei UI", 8, "bold"),
            bg=pal.get("card", "#ffffff"), fg=pal.get("primary", "#2563eb"),
        ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Label(
            filter_bar, text="🔍", font=("Microsoft YaHei UI", 8),
            bg=pal.get("card", "#ffffff"), fg=pal.get("muted", "#64748b"),
        ).pack(side=tk.LEFT)

        filter_entry = tk.Entry(
            filter_bar,
            textvariable=self.candidate_filter_var,
            font=("Microsoft YaHei UI", 8),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
            bg=pal.get("bg", "#f8fafc"),
        )
        filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        self.candidate_filter_var.trace_add("write", self._on_filter_changed)

        candidate_tree_wrap = tk.Frame(left, bg=pal.get("card", "#ffffff"))
        candidate_tree_wrap.pack(fill=tk.BOTH, expand=True)

        self.candidate_tree = ttk.Treeview(
            candidate_tree_wrap,
            columns=("seq", "name", "locator", "window"),
            show="headings",
        )
        self.candidate_tree.heading("seq", text="#")
        self.candidate_tree.heading("name", text="控件")
        self.candidate_tree.heading("locator", text="推荐定位")
        self.candidate_tree.heading("window", text="窗口")
        self.candidate_tree.column("seq", width=38, minwidth=38, stretch=False, anchor="center")
        self.candidate_tree.column("name", width=180, minwidth=140, stretch=False, anchor="w")
        self.candidate_tree.column("locator", width=220, minwidth=180, stretch=False, anchor="w")
        self.candidate_tree.column("window", width=180, minwidth=140, stretch=False, anchor="w")
        self.candidate_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.candidate_tree.bind("<<TreeviewSelect>>", self._on_candidate_select)
        self.candidate_tree.bind("<Double-1>", lambda _event: self.edit_selected())

        candidate_scrollbar = ttk.Scrollbar(candidate_tree_wrap, orient="vertical", command=self.candidate_tree.yview)
        candidate_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        candidate_h_scrollbar = ttk.Scrollbar(left, orient="horizontal", command=self.candidate_tree.xview)
        candidate_h_scrollbar.pack(fill=tk.X, pady=(4, 0))
        self.candidate_tree.configure(yscrollcommand=candidate_scrollbar.set, xscrollcommand=candidate_h_scrollbar.set)

        right = tk.LabelFrame(
            body,
            text=" 🔍 候选详情与预览 ",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=10,
            pady=8,
            bg=pal.get("card", "#ffffff"),
            fg=pal.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=pal.get("border", "#e2e8f0"),
        )
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(10, 0))

        # 预览顶部栏
        preview_top = tk.Frame(right, bg=pal.get("card", "#ffffff"))
        preview_top.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            preview_top, textvariable=self.current_candidate_title_var, font=("Microsoft YaHei UI", 9, "bold"),
            bg=pal.get("card", "#ffffff"), fg=pal.get("text", "#1f2d3d"), anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        wt_theme.create_flat_button(
            preview_top, text="📋 复制定位", command=self.copy_candidate_locator, tone="ghost", padx=6, pady=1
        ).pack(side=tk.RIGHT, padx=(4, 0))
        wt_theme.create_flat_button(
            preview_top, text="✏️ 立即编辑", command=self.edit_selected, tone="secondary", padx=6, pady=1
        ).pack(side=tk.RIGHT)

        self.preview_text = scrolledtext.ScrolledText(
            right, wrap=tk.WORD, font=("Consolas", 9), bd=0, highlightthickness=1, highlightbackground=pal.get("border", "#e2e8f0")
        )
        self.preview_text.pack(fill=tk.BOTH, expand=True)

        # ── 底部操作按钮 ──
        footer = tk.Frame(self.window, padx=14, pady=10, bg=pal.get("bg", "#f8fafc"))
        footer.pack(fill=tk.X, side=tk.BOTTOM)

        wt_theme.create_flat_button(
            footer, text="取消 (Esc)", command=self.on_cancel, tone="ghost", padx=16, pady=5
        ).pack(side=tk.RIGHT, padx=(8, 0))
        wt_theme.create_flat_button(
            footer, text="导入所选候选 (1)", command=self.import_selected, tone="secondary", padx=16, pady=5
        ).pack(side=tk.RIGHT, padx=(8, 0))
        wt_theme.create_flat_button(
            footer, text="导入全部候选", command=self.import_all, tone="primary", padx=20, pady=5
        ).pack(side=tk.RIGHT)

        self.window.bind("<Escape>", lambda _e: self.on_cancel())

    def _toggle_tips(self):
        self._tips_expanded = not self._tips_expanded
        if self._tips_expanded:
            self.tips_body_frame.pack(fill=tk.X, pady=(4, 0))
            self.tips_toggle_btn.config(text="▲ 收起指南")
        else:
            self.tips_body_frame.pack_forget()
            self.tips_toggle_btn.config(text="▼ 展开指南")

    def _update_listener_badge(self):
        if not hasattr(self, "listener_badge_label"):
            return
        pal = EDITOR_THEME
        if self._interactive_monitoring:
            self.listener_badge_var.set("● 交互点击监听中 (点击或按 F8 抓取)")
            try:
                self.listener_badge_label.config(
                    bg=pal.get("success_soft", "#dcfce7"),
                    fg=pal.get("success_text", "#166534"),
                )
            except Exception:
                pass
        elif self._monitoring:
            self.listener_badge_var.set("● 剪贴板监听中 (在 Inspect 中复制即可抓取)")
            try:
                self.listener_badge_label.config(
                    bg=pal.get("primary_soft", "#dbeafe"),
                    fg=pal.get("primary_text", "#1e40af"),
                )
            except Exception:
                pass
        else:
            self.listener_badge_var.set("○ 监听已停止 (待命)")
            try:
                self.listener_badge_label.config(
                    bg=pal.get("panel_soft", "#f1f5f9"),
                    fg=pal.get("muted", "#64748b"),
                )
            except Exception:
                pass

    def _on_filter_changed(self, *args):
        self._refresh_candidates()

    def copy_candidate_locator(self):
        index = self._get_selected_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择一个候选控件。", parent=self.window)
            return
        ctrl = self.captured_controls[index]
        expr = f"{ctrl.get('targetMethod', '')}:{ctrl.get('targetValue', '')}".strip(":")
        if not expr:
            messagebox.showinfo("提示", "当前控件无有效推荐定位。", parent=self.window)
            return
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(expr)
            self.status_var.set(f"已复制候选定位表达式：{expr}")
        except Exception as exc:
            messagebox.showerror("复制失败", f"无法写入剪贴板：{exc}", parent=self.window)

    def test_selected_locator(self):
        """使用当前采集候选的定位信息打开定位检验器。"""
        index = self._get_selected_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择一个候选控件。", parent=self.window)
            return
        control = self.captured_controls[index]
        dialog = ControlLocatorTesterDialog(self.window, initial_control=control)
        self.window.wait_window(dialog.window)

    def _get_clipboard_text(self):
        try:
            return self.parent.clipboard_get()
        except Exception:
            return ""

    def start_monitor(self):
        self._monitoring = True
        self.status_var.set("已开始监听剪贴板，请在 Inspect 中复制控件文本。")
        self._last_clipboard_text = self._get_clipboard_text().strip()
        self._update_listener_badge()
        self.window.after(600, self._poll_clipboard)

    def stop_monitor(self):
        self._monitoring = False
        self.status_var.set("已停止监听剪贴板。")
        self._update_listener_badge()

    def _find_target_window(self, keyword, backend):
        """按标题关键字找顶层窗口（win32-first：枚举不触碰 UIA，命中后按句柄连接）。"""
        import wt_flow_locator as flow_locator

        keyword = str(keyword or "").strip()
        windows = [
            info for info in flow_locator.iter_visible_top_level_windows()
            if (info.get("title") or "").strip()
            and keyword.lower() in (info.get("title") or "").lower()
        ]
        if not windows:
            raise RuntimeError(f"未找到匹配窗口关键字的顶层窗口：{keyword}")
        windows.sort(key=lambda item: len(item.get("title") or ""))
        from pywinauto.application import Application
        target_handle = windows[0]["hwnd"]
        try:
            return Application(backend=backend).connect(handle=target_handle).window(handle=target_handle)
        except Exception:
            return Application(backend="uia").connect(handle=target_handle).window(handle=target_handle)

    @staticmethod
    def _point_in_rect(rect, x, y):
        return rect.left <= x <= rect.right and rect.top <= y <= rect.bottom

    def _find_deepest_at(self, element, x, y):
        try:
            children = element.children()
        except Exception:
            children = []
        for child in children:
            try:
                rect = child.rectangle()
            except Exception:
                continue
            if self._point_in_rect(rect, x, y):
                deeper = self._find_deepest_at(child, x, y)
                return deeper if deeper is not None else child
        try:
            rect = element.rectangle()
            return element if self._point_in_rect(rect, x, y) else None
        except Exception:
            return None

    def _build_control_from_wrapper(self, ctrl):
        element_info = _safe_get_value(lambda: ctrl.element_info, None)
        name = str(_safe_get_value(lambda: ctrl.window_text(), "")).strip()
        if element_info is not None and not name:
            name = str(_safe_get_value(lambda: getattr(element_info, "name", ""), "")).strip()
        class_name = str(_safe_get_value(lambda: ctrl.class_name(), "")).strip()
        control_type = str(_safe_get_value(lambda: getattr(element_info, "control_type", ""), "")).strip()
        localized_control_type = str(_safe_get_value(lambda: getattr(element_info, "localized_control_type", ""), "")).strip()
        automation_id = str(_safe_get_value(lambda: getattr(element_info, "automation_id", ""), "")).strip()
        framework_id = str(_safe_get_value(lambda: getattr(element_info, "framework_id", ""), "")).strip()
        process_id = str(_safe_get_value(lambda: getattr(element_info, "process_id", ""), "")).strip()
        runtime_id_raw = _safe_get_value(lambda: getattr(element_info, "runtime_id", ""), "")
        runtime_id = ""
        if isinstance(runtime_id_raw, (list, tuple)) and runtime_id_raw:
            formatted = []
            for item in runtime_id_raw:
                try:
                    formatted.append(hex(int(item))[2:].upper())
                except Exception:
                    formatted.append(str(item))
            runtime_id = "[" + ",".join(formatted) + "]"
        elif runtime_id_raw:
            runtime_id = str(runtime_id_raw)
        handle = _safe_get_value(lambda: getattr(element_info, "handle", ""), "")
        rect = _safe_get_value(lambda: ctrl.rectangle(), None)
        rect_text = ""
        if rect is not None:
            rect_text = f"[l={rect.left},t={rect.top},r={rect.right},b={rect.bottom}]"
        is_enabled = _safe_get_value(lambda: getattr(element_info, "enabled", ""), "")
        if is_enabled == "":
            is_enabled = _safe_get_value(lambda: ctrl.is_enabled(), "")
        is_offscreen = _safe_get_value(lambda: getattr(element_info, "offscreen", ""), "")
        if is_offscreen == "":
            is_offscreen = not bool(_safe_get_value(lambda: ctrl.is_visible(), True))
        keyboard_focusable = _safe_get_value(lambda: getattr(element_info, "keyboard_focusable", ""), "")
        has_keyboard_focus = _safe_get_value(lambda: getattr(element_info, "has_keyboard_focus", ""), "")
        provider_description = str(_safe_get_value(lambda: getattr(element_info, "provider_description", ""), "")).strip()

        value_pattern_value = ""
        if hasattr(ctrl, "get_value"):
            value_pattern_value = str(_safe_get_value(lambda: ctrl.get_value(), "")).strip()

        toggle_state = ""
        if hasattr(ctrl, "get_toggle_state"):
            toggle_state = str(_safe_get_value(lambda: ctrl.get_toggle_state(), "")).strip()

        legacy_name = str(_safe_get_value(lambda: getattr(element_info, "legacy_name", ""), "")).strip()
        if not name and legacy_name:
            name = legacy_name

        ancestors = []
        current = ctrl
        for _ in range(4):
            current = _safe_get_value(lambda: current.parent(), None)
            if not current:
                break
            parent_name = str(_safe_get_value(lambda: current.window_text(), "")).strip()
            parent_class = str(_safe_get_value(lambda: current.class_name(), "")).strip()
            parent_type = str(_safe_get_value(lambda: getattr(current.element_info, "control_type", ""), "")).strip()
            signature = " | ".join(item for item in [parent_name, parent_class, parent_type] if item)
            if signature:
                ancestors.append(signature)

        children = []
        for child in _safe_get_value(lambda: ctrl.children(), []):
            child_name = str(_safe_get_value(lambda: child.window_text(), "")).strip()
            child_class = str(_safe_get_value(lambda: child.class_name(), "")).strip()
            child_type = str(_safe_get_value(lambda: getattr(child.element_info, "control_type", ""), "")).strip()
            signature = " | ".join(item for item in [child_name, child_class, child_type] if item)
            if signature:
                children.append(signature)
            if len(children) >= 8:
                break

        inspect_data = {
            "howFound": "Interactive click collector",
            "name": name,
            "value": value_pattern_value,
            "toggleState": toggle_state,
            "controlType": control_type,
            "localizedControlType": localized_control_type,
            "boundingRectangle": rect_text,
            "isEnabled": str(is_enabled),
            "isOffscreen": str(is_offscreen),
            "isKeyboardFocusable": str(keyboard_focusable),
            "hasKeyboardFocus": str(has_keyboard_focus),
            "processId": process_id,
            "runtimeId": runtime_id,
            "frameworkId": framework_id,
            "className": class_name,
            "automationId": automation_id,
            "nativeWindowHandle": hex(handle) if isinstance(handle, int) and handle else str(handle or ""),
            "providerDescription": provider_description,
            "legacyName": legacy_name,
            "legacyRole": "",
            "legacyState": "",
            "firstChild": children[0] if children else "",
            "lastChild": children[-1] if children else "",
            "next": "",
            "previous": "",
            "children": children,
            "ancestors": ancestors,
            "availablePatterns": [],
        }
        locator_method, locator_value, locator_score, locator_reason = build_locator_recommendation(inspect_data)
        inspect_data["recommendedTargetMethod"] = locator_method
        inspect_data["recommendedTargetValue"] = locator_value
        inspect_data["recommendedTargetScore"] = locator_score
        inspect_data["recommendedTargetReason"] = locator_reason
        raw_text = build_synthetic_inspect_text(inspect_data)
        control = normalize_control(
            {
                "id": f"captured_control_{len(self.captured_controls) + 1}",
                "name": name or legacy_name or f"控件{len(self.captured_controls) + 1}",
                "role": "",
                "enabled": True,
                "windowTitle": self.var_target_window.get().strip() or self.default_window_title,
                "targetMethod": locator_method,
                "targetValue": locator_value,
                "templateKey": "",
                "uiPath": name or legacy_name,
                "notes": "通过交互点击采集获得",
                "rawInspectText": raw_text,
                "auxChecks": [
                    f"FrameworkId={framework_id}" if framework_id else "",
                    f"ClassName={class_name}" if class_name else "",
                    f"ControlType={normalize_control_type_name(control_type, localized_control_type)}" if control_type or localized_control_type else "",
                    f"IsEnabled={is_enabled}",
                    f"IsOffscreen={is_offscreen}",
                    f"IsKeyboardFocusable={keyboard_focusable}" if keyboard_focusable != "" else "",
                    f"HasKeyboardFocus={has_keyboard_focus}" if has_keyboard_focus != "" else "",
                ],
                "inspectData": inspect_data,
            },
            len(self.captured_controls),
        )
        control["auxChecks"] = [item for item in control.get("auxChecks", []) if item]
        return control

    def _capture_interactive_at(self, x, y, how="click"):
        if not self._interactive_monitoring or self._target_window is None:
            return
        try:
            rect = self._target_window.rectangle()
        except Exception:
            return
        if not self._point_in_rect(rect, x, y):
            return
        try:
            ctrl = self._find_deepest_at(self._target_window, x, y)
        except Exception:
            ctrl = None
        if ctrl is None:
            return
        control = self._build_control_from_wrapper(ctrl)
        if how:
            inspect_data = control.get("inspectData", {})
            inspect_data["howFound"] = f"Interactive {how}"
            control["inspectData"] = inspect_data
        self.window.after(0, lambda: self._add_interactive_control(control))

    def start_interactive_monitor(self):
        keyword = self.var_target_window.get().strip()
        if not keyword:
            messagebox.showinfo("提示", "请先填写交互采集目标窗口关键字。", parent=self.window)
            return
        try:
            self._target_window = self._find_target_window(keyword, self.var_backend.get().strip() or "uia")
            self._target_backend = self.var_backend.get().strip() or "uia"
        except Exception as exc:
            messagebox.showerror("启动失败", f"未找到匹配窗口：\n{exc}", parent=self.window)
            return
        if not self._target_window:
            messagebox.showerror("启动失败", "未找到匹配窗口，请检查窗口关键字。", parent=self.window)
            return

        self.stop_interactive_monitor()
        self._interactive_monitoring = True
        self.status_var.set("交互采集中：点击目标控件抓取；按 F8 捕获鼠标指向控件（不触发点击）。")
        self._update_listener_badge()

        try:
            from pynput import keyboard, mouse

            def on_click(x, y, button, pressed):
                if not pressed or button != mouse.Button.left:
                    return True
                self._maybe_capture(x, y, "click", "_last_click_ts")
                return True

            def on_press(key):
                if key != keyboard.Key.f8:
                    return True
                try:
                    pos = mouse.Controller().position
                except Exception:
                    return True
                self._maybe_capture(pos[0], pos[1], "hotkey", "_last_hotkey_ts")
                return True

            self._mouse_listener = mouse.Listener(on_click=on_click)
            self._mouse_listener.start()
            self._keyboard_listener = keyboard.Listener(on_press=on_press)
            self._keyboard_listener.start()
            self.status_var.set(self.status_var.get() + "（使用 pynput）")
        except Exception:
            try:
                from win32_input_hook import Win32InputHook
                self._win32_hook = Win32InputHook(
                    on_click=self._on_win32_click,
                    on_hotkey=self._on_win32_hotkey,
                )
                self._win32_hook.start()
                self.status_var.set(self.status_var.get() + "（使用系统原生钩子：未安装 pynput）")
            except Exception as exc:
                self._interactive_monitoring = False
                self._update_listener_badge()
                messagebox.showerror(
                    "启动失败",
                    f"交互采集无法启动（pynput 与系统原生钩子均不可用）：\n{exc}",
                    parent=self.window,
                )

    def _maybe_capture(self, x, y, how, ts_attr):
        if not self._interactive_monitoring:
            return False
        now_ts = time.time()
        last = getattr(self, ts_attr, 0.0)
        if now_ts - last < 0.35:
            return False
        setattr(self, ts_attr, now_ts)
        self._capture_interactive_at(x, y, how=how)
        return True

    def _on_win32_click(self, x, y):
        self._maybe_capture(x, y, "click", "_last_click_ts")

    def _on_win32_hotkey(self, x, y):
        self._maybe_capture(x, y, "hotkey", "_last_hotkey_ts")

    def stop_interactive_monitor(self):
        self._interactive_monitoring = False
        if self._mouse_listener is not None:
            try:
                self._mouse_listener.stop()
            except Exception:
                pass
            self._mouse_listener = None
        if self._keyboard_listener is not None:
            try:
                self._keyboard_listener.stop()
            except Exception:
                pass
            self._keyboard_listener = None
        if self._win32_hook is not None:
            try:
                self._win32_hook.stop()
            except Exception:
                pass
            self._win32_hook = None
        self._update_listener_badge()

    def _add_interactive_control(self, control):
        raw_text = str(control.get("rawInspectText", "")).strip()
        if raw_text and raw_text in self._known_raw_texts:
            self.status_var.set("交互采集到了重复控件，已跳过。")
            return
        self.captured_controls.append(control)
        if raw_text:
            self._known_raw_texts.add(raw_text)
        self._refresh_candidates()
        self.candidate_tree.selection_set(str(len(self.captured_controls) - 1))
        self._show_candidate(len(self.captured_controls) - 1)
        self.status_var.set(f"已通过交互点击采集控件：{control.get('name', '')}")

    def _poll_clipboard(self):
        if not self._monitoring:
            return
        current_text = self._get_clipboard_text().strip()
        if current_text and current_text != self._last_clipboard_text:
            self._last_clipboard_text = current_text
            self._capture_raw_text(current_text)
        self.window.after(600, self._poll_clipboard)

    def capture_once(self):
        current_text = self._get_clipboard_text().strip()
        if not current_text:
            messagebox.showinfo("提示", "当前剪贴板没有可用文本。", parent=self.window)
            return
        self._last_clipboard_text = current_text
        self._capture_raw_text(current_text)

    def _capture_raw_text(self, raw_text):
        cleaned = raw_text.strip()
        if not cleaned:
            return
        if "ControlType" not in cleaned and "ClassName" not in cleaned and "Name:" not in cleaned:
            self.status_var.set("剪贴板内容不像 Inspect 文本，已忽略。")
            return
        if cleaned in self._known_raw_texts:
            self.status_var.set("检测到重复的 Inspect 文本，已跳过。")
            return

        parsed = parse_inspect_text(cleaned)
        base_name = parsed.get("name", "") or parsed.get("legacyName", "") or f"控件{len(self.captured_controls) + 1}"
        control = normalize_control(
            {
                "id": f"captured_control_{len(self.captured_controls) + 1}",
                "name": base_name,
                "role": "",
                "enabled": True,
                "windowTitle": self.default_window_title,
                "targetMethod": parsed.get("recommendedTargetMethod", ""),
                "targetValue": parsed.get("recommendedTargetValue", ""),
                "templateKey": "",
                "uiPath": base_name,
                "notes": "",
                "rawInspectText": cleaned,
                "auxChecks": parsed.get("suggestedAuxChecks", []),
                "inspectData": parsed,
            },
            len(self.captured_controls),
        )
        self.captured_controls.append(control)
        self._known_raw_texts.add(cleaned)
        self._refresh_candidates()
        self.candidate_tree.selection_set(str(len(self.captured_controls) - 1))
        self._show_candidate(len(self.captured_controls) - 1)
        self.status_var.set(f"已抓取候选控件：{control.get('name', '')}")

    def _refresh_candidates(self):
        query = (self.candidate_filter_var.get() or "").strip().lower() if hasattr(self, "candidate_filter_var") else ""
        self.candidate_tree.delete(*self.candidate_tree.get_children())
        for index, control in enumerate(self.captured_controls):
            name = str(control.get("name", "")).strip()
            locator = f"{control.get('targetMethod', '')}:{control.get('targetValue', '')}".strip(":")
            win = str(control.get("windowTitle", "")).strip()
            if query and query not in name.lower() and query not in locator.lower() and query not in win.lower():
                continue
            self.candidate_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(index + 1, name, locator, win),
            )
        if hasattr(self, "candidate_count_var"):
            total = len(self.captured_controls)
            self.candidate_count_var.set(f"候选控件 ({total} 个)")

    def _on_candidate_select(self, _event=None):
        selection = self.candidate_tree.selection()
        if not selection:
            return
        self._show_candidate(int(selection[0]))

    def _show_candidate(self, index):
        if not (0 <= index < len(self.captured_controls)):
            return
        control = self.captured_controls[index]
        inspect_data = control.get("inspectData", {})
        if hasattr(self, "current_candidate_title_var"):
            self.current_candidate_title_var.set(f"{control.get('name', '未命名')}  [{control.get('targetMethod', '')}:{control.get('targetValue', '')}]")
        lines = [
            f"控件名称: {control.get('name', '')}",
            f"用途角色: {control.get('role', '')}",
            f"所属窗口: {control.get('windowTitle', '')}",
            f"推荐定位: {control.get('targetMethod', '')}:{control.get('targetValue', '')}",
            f"ClassName: {inspect_data.get('className', '')}",
            f"ControlType: {normalize_control_type_name(inspect_data.get('controlType', ''), inspect_data.get('localizedControlType', ''))}",
            f"AutomationId: {inspect_data.get('automationId', '')}",
            "",
            "辅助判断条件:",
        ]
        lines.extend(f"- {item}" for item in control.get("auxChecks", []))
        lines.extend(["", "原始 Inspect 文本:", control.get("rawInspectText", "")])
        self.preview_text.delete("1.0", tk.END)
        self.preview_text.insert("1.0", "\n".join(lines))

    def _get_selected_index(self):
        selection = self.candidate_tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def edit_selected(self):
        index = self._get_selected_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择一个候选控件。", parent=self.window)
            return
        dialog = ControlEditorDialog(
            self.window,
            control=self.captured_controls[index],
            step_name=self.step_name,
            default_window_title=self.default_window_title,
        )
        self.window.wait_window(dialog.window)
        if not dialog.result:
            return
        self.captured_controls[index] = dialog.result
        self._refresh_candidates()
        self.candidate_tree.selection_set(str(index))
        self._show_candidate(index)
        self.status_var.set(f"已更新候选控件：{dialog.result.get('name', '')}")

    def delete_selected(self):
        index = self._get_selected_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择一个候选控件。", parent=self.window)
            return
        control_name = self.captured_controls[index].get("name", "")
        if not messagebox.askyesno("确认删除", f"确定删除候选控件：{control_name} ？", parent=self.window):
            return
        del self.captured_controls[index]
        self._refresh_candidates()
        self.preview_text.delete("1.0", tk.END)
        self.status_var.set(f"已删除候选控件：{control_name}")

    def import_selected(self):
        index = self._get_selected_index()
        if index is None:
            messagebox.showinfo("提示", "请先选择一个候选控件。", parent=self.window)
            return
        self.result = [self.captured_controls[index]]
        self.stop_monitor()
        self.stop_interactive_monitor()
        self.window.destroy()

    def import_all(self):
        if not self.captured_controls:
            messagebox.showinfo("提示", "当前没有候选控件可导入。", parent=self.window)
            return
        self.result = list(self.captured_controls)
        self.stop_monitor()
        self.stop_interactive_monitor()
        self.window.destroy()

    def on_cancel(self):
        self.stop_monitor()
        self.stop_interactive_monitor()
        self.result = None
        self.window.destroy()



class ControlEditDialog:
    """控件编辑对话框，用于修改控件库中的控件信息"""

    def __init__(self, parent, control):
        self.result = None
        self.control = dict(control)
        self.window = _make_dialog_window(parent, "编辑控件", 740, 660, min_width=640, min_height=580)
        self.window.configure(bg=EDITOR_THEME.get("bg", "#f8fafc"))

        container = tk.Frame(self.window, bg=EDITOR_THEME.get("bg", "#f8fafc"), padx=14, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        self.var_name = tk.StringVar()
        self.var_role = tk.StringVar()
        self.var_control_type = tk.StringVar()
        self.var_window_title = tk.StringVar()
        self.var_target_method = tk.StringVar()
        self.var_target_value = tk.StringVar()
        self.var_ui_path = tk.StringVar()
        self.var_quality = tk.StringVar()
        self.var_quality_reason = tk.StringVar()
        self.var_quality_badge = tk.StringVar(value="")

        # ── 1. 基本信息卡片 ──
        basic_card = tk.LabelFrame(
            container,
            text="🏷️ 基本信息",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=8,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        basic_card.pack(fill=tk.X, pady=(0, 8))
        basic_card.columnconfigure(1, weight=1)
        basic_card.columnconfigure(3, weight=1)

        # 控件名称 & 控制类型
        tk.Label(
            basic_card, text="控件名称 *", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=0, column=0, sticky="w", pady=3)
        name_entry = tk.Entry(
            basic_card, textvariable=self.var_name, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        name_entry.grid(row=0, column=1, sticky="ew", padx=(6, 14), pady=3, ipady=2)

        tk.Label(
            basic_card, text="控制类型", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("muted", "#64748b"),
        ).grid(row=0, column=2, sticky="w", pady=3)
        type_label = tk.Label(
            basic_card, textvariable=self.var_control_type, font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("bg", "#f8fafc"), fg=EDITOR_THEME.get("muted", "#64748b"),
            anchor="w", padx=6, pady=2, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        type_label.grid(row=0, column=3, sticky="ew", padx=(6, 0), pady=3)

        # 角色/说明 & 窗口标题
        tk.Label(
            basic_card, text="角色/说明", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=1, column=0, sticky="w", pady=3)
        role_entry = tk.Entry(
            basic_card, textvariable=self.var_role, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        role_entry.grid(row=1, column=1, sticky="ew", padx=(6, 14), pady=3, ipady=2)

        tk.Label(
            basic_card, text="窗口标题", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=1, column=2, sticky="w", pady=3)
        win_entry = tk.Entry(
            basic_card, textvariable=self.var_window_title, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        win_entry.grid(row=1, column=3, sticky="ew", padx=(6, 0), pady=3, ipady=2)

        # ── 2. 定位策略卡片 ──
        locator_card = tk.LabelFrame(
            container,
            text="🎯 定位策略",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=8,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        locator_card.pack(fill=tk.X, pady=(0, 8))
        locator_card.columnconfigure(1, weight=1)
        locator_card.columnconfigure(3, weight=1)

        tk.Label(
            locator_card, text="定位方法 *", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=0, column=0, sticky="w", pady=3)
        method_combo = ttk.Combobox(
            locator_card,
            textvariable=self.var_target_method,
            values=[
                "automation_id", "automation_id,control_type", "automation_id,class_name",
                "name", "name,control_type", "name,class_name",
                "class_name", "class_name,control_type", "control_type",
                "handle", "text", "text,control_type",
            ],
            state="readonly",
            font=("Microsoft YaHei UI", 9),
        )
        method_combo.grid(row=0, column=1, sticky="ew", padx=(6, 14), pady=3)

        tk.Label(
            locator_card, text="定位值 *", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=0, column=2, sticky="w", pady=3)
        val_entry = tk.Entry(
            locator_card, textvariable=self.var_target_value, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        val_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0), pady=3, ipady=2)

        tk.Label(
            locator_card, text="UI 路径", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=1, column=0, sticky="w", pady=3)
        ui_path_entry = tk.Entry(
            locator_card, textvariable=self.var_ui_path, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        ui_path_entry.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(6, 0), pady=3, ipady=2)

        # ── 3. 质量分级与工程备注卡片 ──
        quality_card = tk.LabelFrame(
            container,
            text="🛡️ 质量分级与工程备注",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=8,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        quality_card.pack(fill=tk.BOTH, expand=True)
        quality_card.columnconfigure(1, weight=1)
        quality_card.columnconfigure(3, weight=1)

        tk.Label(
            quality_card, text="质量分级", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=0, column=0, sticky="w", pady=3)

        q_frame = tk.Frame(quality_card, bg=EDITOR_THEME.get("card", "#ffffff"))
        q_frame.grid(row=0, column=1, sticky="ew", padx=(6, 14), pady=3)

        quality_combo = ttk.Combobox(
            q_frame,
            textvariable=self.var_quality,
            values=["推荐保留", "建议优化", "谨慎使用", "待验证", "未分类"],
            state="readonly",
            width=14,
            font=("Microsoft YaHei UI", 9),
        )
        quality_combo.pack(side=tk.LEFT)

        self._quality_badge_label = tk.Label(
            q_frame,
            textvariable=self.var_quality_badge,
            font=("Microsoft YaHei UI", 8, "bold"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg="#059669",
            padx=6,
            pady=1,
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        self._quality_badge_label.pack(side=tk.LEFT, padx=(6, 0))

        tk.Label(
            quality_card, text="质量说明", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=0, column=2, sticky="w", pady=3)
        reason_entry = tk.Entry(
            quality_card, textvariable=self.var_quality_reason, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        reason_entry.grid(row=0, column=3, sticky="ew", padx=(6, 0), pady=3, ipady=2)

        tk.Label(
            quality_card, text="工程备注", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("muted", "#64748b"),
        ).grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self.notes_text = tk.Text(
            quality_card, height=4, wrap=tk.WORD, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        self.notes_text.grid(row=1, column=1, columnspan=3, sticky="nsew", padx=(6, 0), pady=(6, 0))
        quality_card.rowconfigure(1, weight=1)

        # ── 底部操作栏 ──
        bottom_bar = tk.Frame(container, bg=EDITOR_THEME.get("bg", "#f8fafc"))
        bottom_bar.pack(fill=tk.X, pady=(10, 0))

        tk.Label(
            bottom_bar,
            text="💡 保存后将即时更新控件库内存对象并在下次落盘时固化。",
            font=("Microsoft YaHei UI", 8),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
        ).pack(side=tk.LEFT)

        wt_theme.create_flat_button(
            bottom_bar,
            "保存",
            self.on_save,
            tone="primary",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=18,
            pady=5,
        ).pack(side=tk.RIGHT)

        wt_theme.create_flat_button(
            bottom_bar,
            "取消",
            self.window.destroy,
            tone="secondary",
            font=("Microsoft YaHei UI", 9),
            padx=14,
            pady=5,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        def _on_quality_change(*_a):
            q = self.var_quality.get().strip()
            color_map = {
                "推荐保留": ("✓ 推荐保留", "#059669"),
                "建议优化": ("⚡ 建议优化", "#d97706"),
                "谨慎使用": ("⚠️ 谨慎使用", "#dc2626"),
                "待验证": ("🔍 待验证", "#2563eb"),
                "未分类": ("⚪ 未分类", "#64748b"),
            }
            text, color = color_map.get(q, (q or "未分类", "#64748b"))
            self.var_quality_badge.set(text)
            self._quality_badge_label.config(fg=color)

        self.var_quality.trace_add("write", _on_quality_change)
        self._load_control_data()
        _on_quality_change()

    def _load_control_data(self):
        # 兼容 flatControls 和 controlDefinitions 两种数据结构
        # flatControls 用 displayName/recommendedTargetMethod/recommendedTargetValue/qualityTier
        # controlDefinitions 用 name/targetMethod/targetValue/_qualityTier
        name = (
            str(self.control.get("displayName", "")).strip()
            or str(self.control.get("name", "")).strip()
            or str(self.control.get("savedControlName", "")).strip()
            or str(self.control.get("suggestedControlName", "")).strip()
        )
        self.var_name.set(name)

        # 控制类型（只读，与树形列表的类型列一致）
        inspect_data_for_type = self.control.get("inspectData") if isinstance(self.control.get("inspectData"), dict) else {}
        control_type = normalize_control_type_name(
            self.control.get("controlType", "") or inspect_data_for_type.get("controlType", ""),
            self.control.get("localizedControlType", "") or inspect_data_for_type.get("localizedControlType", ""),
        )
        self.var_control_type.set(control_type or "(未知)")

        role = (
            str(self.control.get("role", "")).strip()
            or str(self.control.get("locatorReason", "")).strip()
        )
        self.var_role.set(role)

        self.var_window_title.set(str(self.control.get("windowTitle", "")).strip())

        target_method = (
            str(self.control.get("recommendedTargetMethod", "")).strip()
            or str(self.control.get("targetMethod", "")).strip()
        )
        self.var_target_method.set(target_method)

        target_value = (
            str(self.control.get("recommendedTargetValue", "")).strip()
            or str(self.control.get("targetValue", "")).strip()
        )
        self.var_target_value.set(target_value)

        self.var_ui_path.set(str(self.control.get("uiPath", "")).strip())

        quality = (
            str(self.control.get("qualityTier", "")).strip()
            or str(self.control.get("_qualityTier", "")).strip()
            or "未分类"
        )
        self.var_quality.set(quality)

        quality_reason = (
            str(self.control.get("qualityReason", "")).strip()
            or str(self.control.get("_qualityReason", "")).strip()
        )
        self.var_quality_reason.set(quality_reason)

        # 备注：从 notes 或 auxChecks 拼接
        notes = str(self.control.get("notes", "")).strip()
        if not notes:
            aux_checks = self.control.get("auxChecks", [])
            if isinstance(aux_checks, list) and aux_checks:
                notes = " | ".join(str(item) for item in aux_checks)
        self.notes_text.delete("1.0", tk.END)
        self.notes_text.insert("1.0", notes)

    def on_save(self):
        name = self.var_name.get().strip()
        if not name:
            messagebox.showwarning("警告", "控件名称不能为空。", parent=self.window)
            return
        flat_item = {k: v for k, v in self.control.items() if not str(k).startswith("_")}
        flat_item["displayName"] = name
        flat_item["name"] = name
        flat_item["role"] = self.var_role.get().strip()
        flat_item["windowTitle"] = self.var_window_title.get().strip()
        flat_item["recommendedTargetMethod"] = self.var_target_method.get().strip()
        flat_item["recommendedTargetValue"] = self.var_target_value.get().strip()
        flat_item["uiPath"] = self.var_ui_path.get().strip()
        flat_item["qualityTier"] = self.var_quality.get().strip()
        flat_item["qualityReason"] = self.var_quality_reason.get().strip()
        if hasattr(self, "notes_text") and hasattr(self.notes_text, "get"):
            flat_item["notes"] = self.notes_text.get("1.0", tk.END).strip()
        # 兼容 inspectData 缺失与显式为 null 两种脏数据：
        # 只判 "not in" 时，JSON 里写成 "inspectData": null 会走到 None["name"] 抛 TypeError。
        if not isinstance(flat_item.get("inspectData"), dict):
            flat_item["inspectData"] = {}
        flat_item["inspectData"]["name"] = name
        self.result = flat_item
        self.window.destroy()


class ControlLocatorTesterDialog:
    """控件定位检验透视台 - 实时透视验证控件在目标窗口中的定位精度与屏幕坐标"""

    TOOL_KEY = "flow_editor_locator_tester"

    def __init__(self, parent, initial_control=None):
        self.result = None
        self.initial_control = initial_control if isinstance(initial_control, dict) else None
        self._all_windows = []
        self._selected_hwnd = None
        self._last_matched_rect = None
        self._last_matched_name = ""
        self._active_overlays = []

        self.window = _make_dialog_window(
            parent,
            "控件定位检验透视台",
            width=1120,
            height=720,
            min_width=920,
            min_height=580,
            on_close=self._on_close,
        )

        # 尝试导入 pywinauto
        self.pywinauto_available = False
        try:
            from pywinauto import Desktop
            self.Desktop = Desktop
            self.pywinauto_available = True
        except ImportError:
            pass

        self.var_status = tk.StringVar(value="就绪：请选择目标窗口，输入或导入定位规则后检验")
        self.var_target_window = tk.StringVar(value="")
        self.var_window_filter = tk.StringVar(value="")
        self.var_locator_method = tk.StringVar(value="automation_id")
        self.var_locator_value = tk.StringVar(value="")
        self.var_test_result = tk.StringVar(value="待检验")
        self.var_auto_highlight = tk.BooleanVar(value=True)

        self._build_ui()
        self._load_ui_state()
        self._refresh_window_list()

        if self.initial_control:
            control = self.initial_control
            inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
            self.var_locator_method.set(str(
                control.get("targetMethod", "")
                or control.get("recommendedTargetMethod", "")
                or inspect_data.get("recommendedTargetMethod", "")
                or "automation_id"
            ).strip())
            self.var_locator_value.set(str(
                control.get("targetValue", "")
                or control.get("recommendedTargetValue", "")
                or inspect_data.get("recommendedTargetValue", "")
            ).strip())
            ctrl_win = str(control.get("windowTitle", "") or inspect_data.get("windowTitle", "")).strip()
            if ctrl_win:
                self.var_target_window.set(ctrl_win)
            ctrl_name = control.get("name", "") or inspect_data.get("name", "")
            self.var_status.set(f"已载入采集候选控件：{ctrl_name}")

    def _build_ui(self):
        theme = EDITOR_THEME
        self.window.configure(bg=theme["bg"])

        # ─────────────────────────────────────────────────────────────────
        # 1. 顶部操作状态栏 (Header Bar)
        # ─────────────────────────────────────────────────────────────────
        header = tk.Frame(self.window, bg=theme["toolbar"], padx=14, pady=8, bd=1, relief=tk.SOLID)
        header.pack(fill=tk.X, side=tk.TOP)

        title_box = tk.Frame(header, bg=theme["toolbar"])
        title_box.pack(side=tk.LEFT)

        tk.Label(
            title_box,
            text="🔍 控件定位检验透视台",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg=theme["toolbar"],
            fg=theme["text"],
        ).pack(side=tk.LEFT)

        tk.Label(
            title_box,
            text=" |  Win32/UIA 定位快速试错、层级树探查与屏幕边界高亮透视",
            font=("Microsoft YaHei UI", 8),
            bg=theme["toolbar"],
            fg="#64748b",
        ).pack(side=tk.LEFT, padx=(4, 0))

        # 顶部右侧指标徽标
        right_box = tk.Frame(header, bg=theme["toolbar"])
        right_box.pack(side=tk.RIGHT)

        tk.Checkbutton(
            right_box,
            text="🎯 命中自动屏幕高亮",
            variable=self.var_auto_highlight,
            bg=theme["toolbar"],
            fg=theme["text"],
            font=("Microsoft YaHei UI", 8),
            activebackground=theme["toolbar"],
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_timing_badge = tk.Label(
            right_box,
            text="⏱️ -- ms",
            font=("Microsoft YaHei UI", 8, "bold"),
            bg="#f1f5f9",
            fg="#64748b",
            padx=8,
            pady=2,
            relief=tk.FLAT,
        )
        self.lbl_timing_badge.pack(side=tk.LEFT, padx=(0, 6))

        self.lbl_status_badge = tk.Label(
            right_box,
            text="● 待检验",
            font=("Microsoft YaHei UI", 8, "bold"),
            bg="#f1f5f9",
            fg="#64748b",
            padx=8,
            pady=2,
            relief=tk.FLAT,
        )
        self.lbl_status_badge.pack(side=tk.LEFT)

        # ─────────────────────────────────────────────────────────────────
        # 2. 主体左右分栏 (PanedWindow)
        # ─────────────────────────────────────────────────────────────────
        paned = ttk.PanedWindow(self.window, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=(8, 0))

        # ── 左栏：目标与定位规则配置面板 ──
        left_pane = tk.Frame(paned, bg=theme["bg"], width=430)
        paned.add(left_pane, weight=1)

        # Card 1: 目标窗口选择
        win_card = tk.LabelFrame(
            left_pane,
            text=" 1. 目标窗口探测与选择 ",
            bg=theme["card"],
            fg="#1e293b",
            font=("Microsoft YaHei UI", 9, "bold"),
            relief=tk.SOLID,
            bd=1,
            padx=10,
            pady=8,
        )
        win_card.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        filter_row = tk.Frame(win_card, bg=theme["card"])
        filter_row.pack(fill=tk.X, pady=(0, 5))

        tk.Label(
            filter_row,
            text="🔍",
            bg=theme["card"],
            fg="#64748b",
            font=("Segoe UI Emoji", 9),
        ).pack(side=tk.LEFT, padx=(0, 3))

        ent_filter = tk.Entry(
            filter_row,
            textvariable=self.var_window_filter,
            font=("Microsoft YaHei UI", 8),
            bg="#f8fafc",
            relief=tk.SOLID,
            bd=1,
        )
        ent_filter.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ent_filter.bind("<KeyRelease>", lambda e: self._apply_window_filter())

        btn_refresh = tk.Button(
            filter_row,
            text="🔄 刷新",
            command=self._refresh_window_list,
            bg="#e0e7ff",
            fg="#3730a3",
            font=("Microsoft YaHei UI", 8, "bold"),
            relief=tk.FLAT,
            padx=8,
            pady=1,
            cursor="hand2",
        )
        btn_refresh.pack(side=tk.RIGHT)

        # 可见窗口列表
        list_box_frame = tk.Frame(win_card, bg=theme["card"])
        list_box_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 5))

        self.window_listbox = tk.Listbox(
            list_box_frame,
            height=6,
            exportselection=False,
            font=("Microsoft YaHei UI", 8),
            selectbackground=theme["primary"],
            selectforeground="white",
            relief=tk.SOLID,
            bd=1,
        )
        self.window_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.window_listbox.bind("<<ListboxSelect>>", self._on_window_select)
        self.window_listbox.bind("<Double-Button-1>", self._on_window_double_click)

        win_scroll = ttk.Scrollbar(list_box_frame, orient="vertical", command=self.window_listbox.yview)
        win_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.window_listbox.configure(yscrollcommand=win_scroll.set)

        # 选中窗口显示
        sel_row = tk.Frame(win_card, bg=theme["card"])
        sel_row.pack(fill=tk.X)

        tk.Label(
            sel_row,
            text="已锁定窗口:",
            font=("Microsoft YaHei UI", 8),
            bg=theme["card"],
            fg="#475569",
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.target_window_entry = tk.Entry(
            sel_row,
            textvariable=self.var_target_window,
            font=("Microsoft YaHei UI", 8),
            bg="#ffffff",
            relief=tk.SOLID,
            bd=1,
        )
        self.target_window_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.lbl_win_count = tk.Label(
            win_card,
            text="枚举中...",
            font=("Microsoft YaHei UI", 7),
            bg=theme["card"],
            fg="#94a3b8",
            anchor="w",
        )
        self.lbl_win_count.pack(fill=tk.X, pady=(2, 0))

        # Card 2: 定位规则配置与操作
        loc_card = tk.LabelFrame(
            left_pane,
            text=" 2. 定位参数与检验动作 ",
            bg=theme["card"],
            fg="#1e293b",
            font=("Microsoft YaHei UI", 9, "bold"),
            relief=tk.SOLID,
            bd=1,
            padx=10,
            pady=8,
        )
        loc_card.pack(fill=tk.X, pady=(0, 6))

        # 定位方法 Combobox
        grid_loc = tk.Frame(loc_card, bg=theme["card"])
        grid_loc.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            grid_loc,
            text="定位方法:",
            font=("Microsoft YaHei UI", 8, "bold"),
            bg=theme["card"],
            fg=theme["text"],
            width=8,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=3)

        self.method_combo = ttk.Combobox(
            grid_loc,
            textvariable=self.var_locator_method,
            values=[
                "automation_id",
                "name",
                "class_name",
                "automation_id,control_type",
                "name,control_type",
                "automation_id,class_name",
                "name,class_name",
                "class_name,control_type",
                "control_type",
                "handle",
                "text",
                "text,control_type",
            ],
            state="readonly",
            font=("Consolas", 9),
        )
        self.method_combo.grid(row=0, column=1, sticky="ew", pady=3)

        # 定位值 Entry
        tk.Label(
            grid_loc,
            text="定位值:",
            font=("Microsoft YaHei UI", 8, "bold"),
            bg=theme["card"],
            fg=theme["text"],
            width=8,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", pady=3)

        val_box = tk.Frame(grid_loc, bg=theme["card"])
        val_box.grid(row=1, column=1, sticky="ew", pady=3)

        self.loc_value_entry = tk.Entry(
            val_box,
            textvariable=self.var_locator_value,
            font=("Consolas", 9),
            bg="#ffffff",
            relief=tk.SOLID,
            bd=1,
        )
        self.loc_value_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        btn_clear = tk.Button(
            val_box,
            text="✕",
            command=lambda: self.var_locator_value.set(""),
            bg="#f1f5f9",
            fg="#94a3b8",
            font=("Microsoft YaHei UI", 8),
            relief=tk.FLAT,
            padx=5,
            cursor="hand2",
        )
        btn_clear.pack(side=tk.LEFT, padx=(3, 0))

        grid_loc.columnconfigure(1, weight=1)

        # 操作动作按钮栅格
        action_row = tk.Frame(loc_card, bg=theme["card"])
        action_row.pack(fill=tk.X, pady=(6, 2))

        btn_test = tk.Button(
            action_row,
            text="▶ 开始检验",
            command=self._test_locator,
            bg=theme["primary"],
            fg="white",
            font=("Microsoft YaHei UI", 9, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=5,
            cursor="hand2",
        )
        btn_test.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        btn_highlight = tk.Button(
            action_row,
            text="🎯 屏幕高亮透视",
            command=lambda: self._flash_highlight(),
            bg="#fef3c7",
            fg="#92400e",
            font=("Microsoft YaHei UI", 8, "bold"),
            relief=tk.FLAT,
            padx=8,
            pady=5,
            cursor="hand2",
        )
        btn_highlight.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        action_row2 = tk.Frame(loc_card, bg=theme["card"])
        action_row2.pack(fill=tk.X, pady=(4, 0))

        btn_parent = tk.Button(
            action_row2,
            text="🌳 获取父级层级",
            command=self._get_parent_locator,
            bg="#f1f5f9",
            fg="#334155",
            font=("Microsoft YaHei UI", 8),
            relief=tk.FLAT,
            padx=8,
            pady=4,
            cursor="hand2",
        )
        btn_parent.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        btn_lib = tk.Button(
            action_row2,
            text="📖 控件库导入",
            command=self._select_from_library,
            bg="#d1fae5",
            fg="#065f46",
            font=("Microsoft YaHei UI", 8),
            relief=tk.FLAT,
            padx=8,
            pady=4,
            cursor="hand2",
        )
        btn_lib.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        # ── 右栏：诊断透视与详情控制台 ──
        right_pane = tk.Frame(paned, bg=theme["bg"])
        paned.add(right_pane, weight=2)

        diag_card = tk.LabelFrame(
            right_pane,
            text=" 3. 实时透视与诊断控制台 ",
            bg=theme["card"],
            fg="#1e293b",
            font=("Microsoft YaHei UI", 9, "bold"),
            relief=tk.SOLID,
            bd=1,
            padx=10,
            pady=8,
        )
        diag_card.pack(fill=tk.BOTH, expand=True)

        # 四项指标卡片栏
        metrics_bar = tk.Frame(diag_card, bg=theme["card"])
        metrics_bar.pack(fill=tk.X, pady=(0, 8))

        def _make_metric_box(parent, title, default_val):
            box = tk.Frame(parent, bg="#f8fafc", bd=1, relief=tk.SOLID, padx=8, pady=4)
            tk.Label(box, text=title, font=("Microsoft YaHei UI", 7), bg="#f8fafc", fg="#64748b").pack(anchor="w")
            val_lbl = tk.Label(box, text=default_val, font=("Microsoft YaHei UI", 8, "bold"), bg="#f8fafc", fg="#0f172a")
            val_lbl.pack(anchor="w")
            return box, val_lbl

        b1, self.lbl_m_status = _make_metric_box(metrics_bar, "命中状态", "待检验")
        b1.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        b2, self.lbl_m_timing = _make_metric_box(metrics_bar, "检验耗时", "-- ms")
        b2.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        b3, self.lbl_m_rect = _make_metric_box(metrics_bar, "屏幕包围盒 (W×H @ X,Y)", "--")
        b3.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

        b4, self.lbl_m_attr = _make_metric_box(metrics_bar, "可见性 / 激活态", "--")
        b4.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # 控制台文本区
        self.result_text = scrolledtext.ScrolledText(
            diag_card,
            wrap=tk.WORD,
            font=("Consolas", 9),
            bg="#f8fafc",
            fg="#0f172a",
            bd=1,
            relief=tk.SOLID,
            padx=8,
            pady=8,
        )
        self.result_text.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        # 配置文本样式标签
        self.result_text.tag_config("title", font=("Consolas", 10, "bold"), foreground="#1e40af")
        self.result_text.tag_config("success", font=("Consolas", 9, "bold"), foreground="#059669")
        self.result_text.tag_config("error", font=("Consolas", 9, "bold"), foreground="#dc2626")
        self.result_text.tag_config("warning", font=("Consolas", 9, "bold"), foreground="#d97706")
        self.result_text.tag_config("key", font=("Consolas", 9, "bold"), foreground="#334155")
        self.result_text.tag_config("val", font=("Consolas", 9), foreground="#0f172a")
        self.result_text.tag_config("dim", font=("Consolas", 9), foreground="#94a3b8")
        self.result_text.tag_config("highlight", background="#fef08a", foreground="#854d0e")

        self.result_text.insert(
            tk.END,
            "═══ 控件定位检验透视台就绪 ═══\n"
            "• 左侧选择目标窗口，输入定位方法与参数后点击「▶ 开始检验」\n"
            "• 支持在检验命中时自动在屏幕相应区域闪烁透视高亮线框\n"
            "• 点击「🌳 获取父级层级」可分析控件的祖先链与同级兄弟控件\n"
        )

        # 底部控制台操作栏
        diag_bottom = tk.Frame(diag_card, bg=theme["card"])
        diag_bottom.pack(fill=tk.X)

        btn_copy = tk.Button(
            diag_bottom,
            text="📋 复制诊断报告",
            command=self._copy_report,
            bg="#f1f5f9",
            fg="#334155",
            font=("Microsoft YaHei UI", 8),
            relief=tk.FLAT,
            padx=10,
            pady=3,
            cursor="hand2",
        )
        btn_copy.pack(side=tk.LEFT)

        btn_apply = tk.Button(
            diag_bottom,
            text="✓ 采纳此定位并返回",
            command=self._apply_and_close,
            bg="#059669",
            fg="white",
            font=("Microsoft YaHei UI", 8, "bold"),
            relief=tk.FLAT,
            padx=12,
            pady=3,
            cursor="hand2",
        )
        btn_apply.pack(side=tk.LEFT, padx=(8, 0))

        btn_close = tk.Button(
            diag_bottom,
            text="关闭",
            command=self._on_close,
            bg="#f1f5f9",
            fg="#64748b",
            font=("Microsoft YaHei UI", 8),
            relief=tk.FLAT,
            padx=12,
            pady=3,
            cursor="hand2",
        )
        btn_close.pack(side=tk.RIGHT)

        # ─────────────────────────────────────────────────────────────────
        # 3. 底部常驻提示条
        # ─────────────────────────────────────────────────────────────────
        status_bar = tk.Frame(self.window, bg="#f1f5f9", padx=12, pady=5)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM, pady=(6, 0))

        tk.Label(
            status_bar,
            textvariable=self.var_status,
            font=("Microsoft YaHei UI", 8),
            bg="#f1f5f9",
            fg="#475569",
            anchor="w",
        ).pack(fill=tk.X)

    def _refresh_window_list(self):
        """刷新可用窗口列表（win32-first，安全防崩溃）"""
        self.window_listbox.delete(0, tk.END)
        self._all_windows = []
        if not self.pywinauto_available:
            self.var_status.set("pywinauto 未安装，无法枚举窗口")
            self.lbl_win_count.config(text="pywinauto 未安装")
            return

        try:
            import wt_flow_locator as flow_locator
            windows = []
            for info in flow_locator.iter_visible_top_level_windows():
                title = (info.get("title") or "").strip()
                if title:
                    windows.append({
                        "title": title,
                        "className": info.get("className") or "",
                        "hwnd": info.get("hwnd") or 0,
                    })
            windows.sort(key=lambda x: x["title"].lower())
            self._all_windows = windows
            self._apply_window_filter()
            self.var_status.set(f"已发现 {len(windows)} 个顶层可见窗口")
        except Exception as exc:
            self.var_status.set(f"枚举窗口失败：{exc}")
            self.lbl_win_count.config(text=f"枚举失败：{exc}")

    def _apply_window_filter(self):
        """根据搜索框实时过滤列表"""
        q = self.var_window_filter.get().strip().lower()
        self.window_listbox.delete(0, tk.END)
        matched_count = 0
        for win in self._all_windows:
            title = win["title"]
            cls_name = win["className"]
            if not q or (q in title.lower()) or (q in cls_name.lower()):
                self.window_listbox.insert(tk.END, f"{title} [{cls_name}]")
                matched_count += 1
        self.lbl_win_count.config(
            text=f"共 {len(self._all_windows)} 个窗口 (匹配 {matched_count} 个)"
            if q else f"共 {len(self._all_windows)} 个可见窗口"
        )

    def _on_window_select(self, event=None):
        """窗口选中事件"""
        selection = self.window_listbox.curselection()
        if not selection:
            return
        content = self.window_listbox.get(selection[0])
        title = content.rsplit(" [", 1)[0] if " [" in content else content
        self.var_target_window.set(title)
        for w in self._all_windows:
            if w["title"] == title:
                self._selected_hwnd = w["hwnd"]
                break
        self.var_status.set(f"已锁定目标窗口：{title}")

    def _on_window_double_click(self, event=None):
        """窗口双击快捷事件：锁定窗口，若已填定位值则顺势触发检验"""
        self._on_window_select(event)
        if self.var_locator_value.get().strip():
            self._test_locator()

    def _resolve_target_window(self, flow_locator, target_title):
        """根据 hwnd 或标题精准解析并包装目标窗口（防标题手输与选中 hwnd 脱节）"""
        target_window = None
        if self._selected_hwnd:
            selected_win_title = ""
            for w in self._all_windows:
                if w.get("hwnd") == self._selected_hwnd:
                    selected_win_title = w.get("title", "")
                    break
            # 校验输入框中的标题是否与当前选中的 hwnd 一致
            if selected_win_title and (not target_title or target_title.lower() in selected_win_title.lower()):
                target_window = flow_locator.wrap_window_by_handle(self._selected_hwnd)
            else:
                self._selected_hwnd = None

        if target_window is None:
            all_windows = [
                w for w in flow_locator.iter_visible_top_level_windows()
                if (w.get("title") or "").strip()
            ]
            if target_title:
                matched = [
                    w for w in all_windows
                    if target_title.lower() in (w.get("title") or "").lower()
                ]
            else:
                matched = all_windows

            if not matched:
                msg = f"未找到标题包含 '{target_title}' 的顶层窗口" if target_title else "未找到任何可用顶层窗口"
                return None, msg

            target_window = flow_locator.wrap_window_by_handle(matched[0].get("hwnd"))
            if target_window:
                self._selected_hwnd = matched[0].get("hwnd")

        if target_window is None:
            return None, "目标窗口 UIA 包装失败，请重试或更换目标窗口"

        return target_window, None

    def _select_from_library(self):
        """从控件库选择控件"""
        try:
            dialog = ControlMapImportDialog(
                self.window,
                default_window_title=self.var_target_window.get().strip(),
                initial_filter="",
            )
            self.window.wait_window(dialog.window)
            if dialog.result and len(dialog.result) > 0:
                control = dialog.result[0]
                self.var_locator_method.set(str(control.get("targetMethod", "")).strip())
                self.var_locator_value.set(str(control.get("targetValue", "")).strip())
                window_title = str(control.get("windowTitle", "")).strip()
                if window_title:
                    self.var_target_window.set(window_title)
                self.var_status.set(f"已从控件库导入：{control.get('name', '')}")
        except Exception as exc:
            messagebox.showwarning("打开控件库失败", f"无法打开控件库：{exc}", parent=self.window)

    def _flash_highlight(self, rect=None):
        """在屏幕目标矩形区域弹出一个半透明高亮线框，并在指定毫秒后自动销毁"""
        target_rect = rect or self._last_matched_rect
        if not target_rect:
            messagebox.showinfo("提示", "当前尚无定位命中的控件坐标，请先执行「▶ 开始检验」。", parent=self.window)
            return None
        try:
            left = int(target_rect.left)
            top = int(target_rect.top)
            right = int(target_rect.right)
            bottom = int(target_rect.bottom)
            w = max(12, right - left)
            h = max(12, bottom - top)

            if w <= 0 or h <= 0 or w > 10000 or h > 10000 or left < -5000 or top < -5000 or right < -5000 or bottom < -5000:
                messagebox.showwarning(
                    "坐标异常",
                    f"控件包围盒坐标异常 ({left}, {top}, {w}×{h})，可能窗口已最小化或处于屏幕外部。",
                    parent=self.window,
                )
                return None

            overlay = tk.Toplevel(self.window)
            overlay.overrideredirect(True)
            overlay.attributes("-topmost", True)
            try:
                overlay.attributes("-alpha", 0.45)
            except Exception:
                pass
            overlay.geometry(f"{w}x{h}+{left}+{top}")

            canvas = tk.Canvas(
                overlay,
                width=w,
                height=h,
                bg="#fef08a",
                highlightthickness=3,
                highlightbackground="#ef4444",
                cursor="hand2",
            )
            canvas.pack(fill=tk.BOTH, expand=True)

            ctrl_name = self._last_matched_name or "Target Control"
            canvas.create_text(
                max(6, w // 2),
                max(10, min(14, h // 2)),
                text=f"🎯 {ctrl_name} ({w}×{h})",
                fill="#991b1b",
                font=("Microsoft YaHei UI", 9, "bold"),
            )

            self._active_overlays.append(overlay)

            def _cleanup():
                try:
                    if overlay in self._active_overlays:
                        self._active_overlays.remove(overlay)
                    overlay.destroy()
                except Exception:
                    pass

            overlay.after(1600, _cleanup)
            overlay.bind("<Button-1>", lambda e: _cleanup())
            return overlay
        except Exception:
            return None

    def _test_locator(self):
        """执行控件定位检验"""
        if not self.pywinauto_available:
            self._update_result_view(
                status="pywinauto 未安装",
                elapsed_ms=0,
                error="pywinauto 未安装，无法进行定位检验",
            )
            return

        target_title = self.var_target_window.get().strip()
        locator_method = self.var_locator_method.get().strip()
        locator_value = self.var_locator_value.get().strip()

        if not locator_value:
            self._update_result_view(
                status="参数缺失",
                elapsed_ms=0,
                error="请输入或选择定位值（targetValue）",
            )
            return

        t0 = time.perf_counter()
        try:
            import wt_flow_locator as flow_locator

            target_window, err_msg = self._resolve_target_window(flow_locator, target_title)
            if target_window is None:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self._update_result_view(
                    status="窗口无法定位",
                    elapsed_ms=elapsed_ms,
                    error=err_msg,
                )
                return

            search_props = {}
            if "," in locator_method:
                parts = [p.strip() for p in locator_method.split(",")]
                value_parts = [v.strip() for v in locator_value.split(",")]
                for i, part in enumerate(parts):
                    if i < len(value_parts) and value_parts[i]:
                        search_props[part] = value_parts[i]
            else:
                search_props[locator_method] = locator_value

            found = None
            try:
                found = target_window.child(**search_props)
            except Exception:
                found = None

            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if found:
                ctrl_name = ""
                try: ctrl_name = found.window_text()
                except Exception: pass
                ctrl_cls = ""
                try: ctrl_cls = found.class_name()
                except Exception: pass
                auto_id = ""
                try: auto_id = found.automation_id()
                except Exception: pass
                rect = None
                try: rect = found.rectangle()
                except Exception: pass
                is_vis = None
                try: is_vis = found.is_visible()
                except Exception: pass
                is_en = None
                try: is_en = found.is_enabled()
                except Exception: pass

                self._last_matched_rect = rect
                self._last_matched_name = ctrl_name or locator_value

                self._update_result_view(
                    status="定位成功",
                    elapsed_ms=elapsed_ms,
                    success=True,
                    target_title=target_title,
                    method=locator_method,
                    value=locator_value,
                    name=ctrl_name,
                    class_name=ctrl_cls,
                    automation_id=auto_id,
                    rect=rect,
                    is_visible=is_vis,
                    is_enabled=is_en,
                )

                if self.var_auto_highlight.get() and rect:
                    self._flash_highlight(rect)
            else:
                self._last_matched_rect = None
                candidates = []
                try:
                    count = 0
                    for ctrl in target_window.descendants():
                        try:
                            n = ctrl.window_text()
                            c = ctrl.class_name()
                            aid = getattr(ctrl, 'automation_id', '')
                            if n or aid:
                                candidates.append((n, c, aid))
                                count += 1
                                if count >= 35:
                                    break
                        except Exception:
                            pass
                except Exception:
                    pass

                self._update_result_view(
                    status="定位未命中",
                    elapsed_ms=elapsed_ms,
                    success=False,
                    target_title=target_title,
                    method=locator_method,
                    value=locator_value,
                    candidates=candidates,
                )

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._update_result_view(
                status="检验异常",
                elapsed_ms=elapsed_ms,
                error=f"检验过程发生异常：{exc}",
            )

    def _get_parent_locator(self):
        """探查控件的父子层级与兄弟节点，帮助提取更稳定可靠的定位特征"""
        if not self.pywinauto_available:
            self._update_result_view(
                status="pywinauto 未安装",
                elapsed_ms=0,
                error="pywinauto 未安装，无法获取父级层级信息",
            )
            return

        target_title = self.var_target_window.get().strip()
        locator_method = self.var_locator_method.get().strip()
        locator_value = self.var_locator_value.get().strip()

        if not locator_value:
            self._update_result_view(
                status="参数缺失",
                elapsed_ms=0,
                error="请先输入定位值，或从控件库选择一个控件",
            )
            return

        t0 = time.perf_counter()
        try:
            import wt_flow_locator as flow_locator

            target_window, err_msg = self._resolve_target_window(flow_locator, target_title)
            if target_window is None:
                elapsed_ms = (time.perf_counter() - t0) * 1000.0
                self._update_result_view(status="窗口无法定位", elapsed_ms=elapsed_ms, error=err_msg)
                return

            search_props = {}
            if "," in locator_method:
                parts = [p.strip() for p in locator_method.split(",")]
                if "automation_id" in parts:
                    search_props["automation_id"] = locator_value.split(",")[0].strip() if "," in locator_value else locator_value
                if "name" in parts:
                    search_props["name"] = locator_value.split(",")[-1].strip()
            else:
                search_props[locator_method] = locator_value

            found = None
            try:
                found = target_window.child(**search_props)
            except Exception:
                pass

            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            if not found:
                self._update_result_view(
                    status="定位未命中",
                    elapsed_ms=elapsed_ms,
                    error=f"未能定位到控件，无法获取父级关系。\n请先确认定位参数是否能命中控件。\n\n目标窗口: {target_title}\n定位方法: {locator_method}\n定位值: {locator_value}",
                )
                return

            ancestors = []
            current = found
            for _ in range(10):
                try:
                    parent = current.parent()
                    if parent and parent.window_text() != target_title:
                        try:
                            ancestors.append((parent.window_text() or "(Unnamed)", parent.class_name() or ""))
                        except Exception:
                            pass
                    else:
                        break
                    current = parent
                except Exception:
                    break

            siblings = []
            try:
                parent_ctrl = found.parent()
                if parent_ctrl:
                    for child in parent_ctrl.children():
                        try:
                            c_name = child.window_text() or ""
                            c_cls = child.class_name() or ""
                            c_aid = getattr(child, 'automation_id', '') or ""
                            siblings.append((c_name, c_cls, c_aid))
                        except Exception:
                            pass
            except Exception:
                pass

            lines = [
                ("title", "═══ 控件层级拓扑与兄弟节点诊断报告 ═══\n"),
                ("key", "目标控件: "), ("val", f"{found.window_text()} [{found.class_name()}]\n"),
                ("key", "检索耗时: "), ("val", f"{elapsed_ms:.1f} ms\n\n"),
                ("title", "── 祖先层级树 (从根到目标) ──\n"),
            ]
            if ancestors:
                for idx, (anc_name, anc_cls) in enumerate(reversed(ancestors)):
                    indent = "  " * idx
                    lines.append(("key", f"{indent}├── [L{idx}] "))
                    lines.append(("val", f"{anc_name} "))
                    lines.append(("dim", f"[{anc_cls}]\n"))
                indent = "  " * len(ancestors)
                lines.append(("success", f"{indent}└── 🎯 [Target] {found.window_text()} [{found.class_name()}]\n\n"))
            else:
                lines.append(("dim", "  (该控件直接位于顶层窗口下，无中间父级容器)\n\n"))

            lines.append(("title", f"── 同级兄弟控件 ({len(siblings)} 个) ──\n"))
            for s_name, s_cls, s_aid in siblings[:20]:
                is_target = (locator_value in s_name) or (locator_value in s_aid) or (s_name == found.window_text())
                if is_target:
                    lines.append(("highlight", " ▶ [MATCH] "))
                    lines.append(("success", f"{s_name or '(无名称)'} | {s_cls} | {s_aid}\n"))
                else:
                    lines.append(("dim", "   • "))
                    lines.append(("val", f"{s_name or '(无名称)'} | {s_cls} | {s_aid}\n"))

            if len(siblings) > 20:
                lines.append(("dim", f"   ... 其余 {len(siblings) - 20} 个兄弟控件已折叠\n"))

            self._render_console(lines)
            self._update_badges("层级解析完成", elapsed_ms, badge_bg="#d1fae5", badge_fg="#065f46")
            self.lbl_m_status.config(text="✓ 已提取层级", fg="#059669")
            self.lbl_m_timing.config(text=f"{elapsed_ms:.1f} ms")
            self.var_status.set(f"已成功获取 {len(ancestors)} 层父级与 {len(siblings)} 个同级兄弟节点")

        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            self._update_result_view(status="解析异常", elapsed_ms=elapsed_ms, error=f"获取层级出错：{exc}")

    def _render_console(self, tagged_chunks):
        """向 ScrolledText 渲染带格式的彩色输出"""
        self.result_text.delete("1.0", tk.END)
        for tag, text in tagged_chunks:
            self.result_text.insert(tk.END, text, tag)

    def _update_badges(self, status_text, elapsed_ms, badge_bg="#f1f5f9", badge_fg="#64748b"):
        self.lbl_status_badge.config(text=status_text, bg=badge_bg, fg=badge_fg)
        self.lbl_timing_badge.config(text=f"⏱️ {elapsed_ms:.1f} ms", bg="#e0f2fe", fg="#0369a1")

    def _update_result_view(self, status, elapsed_ms, success=None, error=None, **kwargs):
        self.var_test_result.set(status)
        self.var_status.set(f"检验完成：{status}")

        if error:
            self._update_badges(f"✗ {status}", elapsed_ms, badge_bg="#fee2e2", badge_fg="#991b1b")
            self.lbl_m_status.config(text=status, fg="#dc2626")
            self.lbl_m_timing.config(text=f"{elapsed_ms:.1f} ms")
            self.lbl_m_rect.config(text="--")
            self.lbl_m_attr.config(text="--")
            self._render_console([
                ("error", f"═══ 检验未通过: {status} ═══\n\n"),
                ("val", f"{error}\n"),
            ])
            return

        if success:
            self._update_badges("✓ 定位成功", elapsed_ms, badge_bg="#d1fae5", badge_fg="#065f46")
            self.lbl_m_status.config(text="✓ 成功命中", fg="#059669")
            self.lbl_m_timing.config(text=f"{elapsed_ms:.1f} ms")

            rect = kwargs.get("rect")
            if rect:
                w = max(0, rect.right - rect.left)
                h = max(0, rect.bottom - rect.top)
                cx = (rect.left + rect.right) // 2
                cy = (rect.top + rect.bottom) // 2
                self.lbl_m_rect.config(text=f"{w}×{h} @ ({rect.left}, {rect.top})")
            else:
                self.lbl_m_rect.config(text="无坐标信息")

            vis = kwargs.get("is_visible")
            en = kwargs.get("is_enabled")
            self.lbl_m_attr.config(text=f"可见: {'是' if vis else '否'} | 启用: {'是' if en else '否'}")

            lines = [
                ("title", "═══ 控件定位检验成功报告 ═══\n\n"),
                ("key", "【目标窗口】 "), ("val", f"{kwargs.get('target_title') or '(当前活跃)'}\n"),
                ("key", "【检索规则】 "), ("val", f"{kwargs.get('method')} = \"{kwargs.get('value')}\"\n"),
                ("key", "【响应耗时】 "), ("success", f"{elapsed_ms:.1f} ms\n\n"),
                ("title", "── 命中控件元数据属性 ──\n"),
                ("key", "• 控件文本 (Name):        "), ("val", f"{kwargs.get('name') or '(空)'}\n"),
                ("key", "• 控件类型 (ClassName):   "), ("val", f"{kwargs.get('class_name') or '(未知)'}\n"),
                ("key", "• 自动化标识 (AutomationId):"), ("val", f"{kwargs.get('automation_id') or '(未定义)'}\n"),
                ("key", "• 可见性 / 启用状态:       "), ("val", f"可见={'是' if vis else '否'}, 启用={'是' if en else '否'}\n"),
            ]
            if rect:
                lines.extend([
                    ("key", "• 屏幕包围盒 (Rect):       "),
                    ("val", f"左上: ({rect.left}, {rect.top})  右下: ({rect.right}, {rect.bottom})  尺寸: {w}×{h}\n"),
                    ("key", "• 物理中心点 (Center):     "),
                    ("highlight", f"X={cx}, Y={cy}  (可直接用于 pyautogui 物理点击)\n"),
                ])
            lines.append(("\nsuccess", "✓ 验证结论：该定位规则在当前目标窗口内唯一且有效，可直接采纳！\n"))
            self._render_console(lines)
        else:
            self._update_badges("✗ 定位未命中", elapsed_ms, badge_bg="#fee2e2", badge_fg="#991b1b")
            self.lbl_m_status.config(text="✗ 未命中", fg="#dc2626")
            self.lbl_m_timing.config(text=f"{elapsed_ms:.1f} ms")
            self.lbl_m_rect.config(text="--")
            self.lbl_m_attr.config(text="--")

            candidates = kwargs.get("candidates") or []
            lines = [
                ("error", "═══ 控件定位未命中 ═══\n\n"),
                ("key", "【目标窗口】 "), ("val", f"{kwargs.get('target_title')}\n"),
                ("key", "【检索规则】 "), ("val", f"{kwargs.get('method')} = \"{kwargs.get('value')}\"\n"),
                ("key", "【响应耗时】 "), ("val", f"{elapsed_ms:.1f} ms\n\n"),
                ("warning", "未能找到匹配该定位特征的子控件。可能原因：\n"),
                ("dim", " 1. 目标窗口未处于前台或控件未加载完成；\n"),
                ("dim", " 2. 定位方法或属性拼写错误（如 automation_id 与 name 混淆）；\n"),
                ("dim", " 3. 该控件处于更深层容器或内嵌子窗口中，建议使用「🌳 获取父级层级」或尝试不同定位方法。\n\n"),
            ]
            if candidates:
                lines.append(("title", f"── 窗口内部分候选控件 ({len(candidates)} 个) ──\n"))
                for n, c, aid in candidates:
                    lines.append(("dim", " • "))
                    lines.append(("val", f"{n or '(无名称)'} | {c} | {aid}\n"))
            self._render_console(lines)

    def _copy_report(self):
        """复制诊断报告至系统剪贴板"""
        content = self.result_text.get("1.0", tk.END).strip()
        if not content:
            return
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(content)
            self.var_status.set("✓ 诊断报告已成功复制到剪贴板")
        except Exception:
            pass

    def _apply_and_close(self):
        """采纳当前定位规则并返回"""
        method = self.var_locator_method.get().strip()
        value = self.var_locator_value.get().strip()
        win_title = self.var_target_window.get().strip()
        if not value:
            messagebox.showwarning("警告", "定位值为空，无法采纳。", parent=self.window)
            return

        self.result = {
            "windowTitle": win_title,
            "targetMethod": method,
            "targetValue": value,
        }
        if self.initial_control:
            res = dict(self.initial_control)
            res["windowTitle"] = win_title
            res["targetMethod"] = method
            res["targetValue"] = value
            self.result = res

        self._on_close()

    def _load_ui_state(self):
        """恢复窗口尺寸及上次会话偏好"""
        try:
            saved = wt_ui_state.load(self.TOOL_KEY)
            if isinstance(saved, dict):
                geom = saved.get("geometry")
                if geom:
                    wt_ui_state.apply_window_geometry(self.window, geom)
                if not self.initial_control:
                    if saved.get("target_window"):
                        self.var_target_window.set(saved.get("target_window"))
                    if saved.get("locator_method"):
                        self.var_locator_method.set(saved.get("locator_method"))
                    if saved.get("locator_value"):
                        self.var_locator_value.set(saved.get("locator_value"))
                if "auto_highlight" in saved:
                    self.var_auto_highlight.set(bool(saved.get("auto_highlight")))
        except Exception:
            pass

    def _save_ui_state(self):
        """保存窗口状态"""
        try:
            geom = wt_ui_state.capture_window_geometry(self.window)
            wt_ui_state.save(self.TOOL_KEY, {
                "geometry": geom,
                "target_window": self.var_target_window.get().strip(),
                "locator_method": self.var_locator_method.get().strip(),
                "locator_value": self.var_locator_value.get().strip(),
                "auto_highlight": self.var_auto_highlight.get(),
            })
        except Exception:
            pass

    def _on_close(self):
        """关闭窗口时的清理与状态保存"""
        self._save_ui_state()
        for ov in list(self._active_overlays):
            try:
                ov.destroy()
            except Exception:
                pass
        self._active_overlays.clear()
        self.window.destroy()


def control_map_timestamp(value):
    """控件时间字段 -> float 时间戳（0.0 表示无法解析）。

    同一文件内所有控件的 _addedAt 通常都是同一个 scanMeta.scanTime 字符串，
    lru_cache 让排序/时间过滤大量重复值零开销（7k 控件排序段 300ms -> ~15ms）。
    """
    return _control_map_timestamp_cached(str(value or "").strip())


@lru_cache(maxsize=2048)
def _control_map_timestamp_cached(text):
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


class ControlMapImportDialog:
    def __init__(self, parent, default_window_title="", initial_filter="", external_window=None):
        self.default_window_title = str(default_window_title or "").strip()
        self.result = None
        self.control_map_files = []
        self.current_payload = None
        self._tree_node_map = {}  # 树形视图索引映射
        self._tree_node_index = {}  # 树形视图 iid -> flatControls 原始下标
        self._tree_seq = 0  # 树形视图 controlsTree 节点自增序号
        self._parent = parent
        # 控件列表构建缓存：_build_controls_from_payload 对每个控件做 normalize+merge，
        # 大文件（总控件信息 16MB+）下每次调用都全量重建会卡顿。直接缓存 payload
        # 对象本身（is 身份比较）：对象被替换（新文件）时自动失效；原地修改
        # payload 的回写路径（编辑/删除）需手动置 None 失效，见两处回写点。
        self._controls_cache_key = None
        self._controls_cache_value = []
        # 过滤关键字输入防抖：每次按键都全量重建树（大文件下）会卡顿，
        # 用 after 延迟 300ms 合并连续输入，只在停顿后重建一次。
        self._filter_debounce_after = None
        # 已加载控件库文件缓存：避免重复选择同一文件时反复 json.load（大文件 16MB+）。
        # 键为文件路径，值 (mtime, payload)；文件 mtime 变化（外部保存）时自动失效。
        self._payload_file_cache = {}

        # 如果提供了外部窗口，则使用外部窗口；否则创建新的 Toplevel
        if external_window is not None:
            self.window = external_window
            self.window.title("控件库维护")
            self._owns_window = False
        else:
            self.window = _make_dialog_window(parent, "从控件库导入", 1480, 860, min_width=1320, min_height=760)
            self._owns_window = True

        self.var_filter = tk.StringVar(value=str(initial_filter or "").strip())
        self.var_sort = tk.StringVar(value="质量优先")
        self.var_time_filter = tk.StringVar(value="最近7天")
        self.var_file_summary = tk.StringVar(value="控件库文件：0")
        self.var_candidate_summary = tk.StringVar(value="候选控件：0")
        self.var_status = tk.StringVar(value="请选择左侧控件库文件，再导入所需控件。")
        self.var_file_scope = tk.StringVar(value="single")  # single=single file, master=master control

        self._build_ui()
        self._refresh_file_list()

    def _build_ui(self):
        self.window.configure(bg=EDITOR_THEME.get("bg", "#f8fafc"))

        # ── 顶栏紧凑使用指引（支持折叠展开，节省垂直空间） ──
        tips = tk.Frame(
            self.window,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            padx=12,
            pady=6,
        )
        tips.pack(fill=tk.X, padx=10, pady=(8, 0))

        tips_header = tk.Frame(tips, bg=EDITOR_THEME.get("card", "#ffffff"))
        tips_header.pack(fill=tk.X)

        tk.Label(
            tips_header,
            text="💡 提示：从已扫描的控件库中检索、复核并批量导入当前流程步骤锚点。",
            font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).pack(side=tk.LEFT)

        self._tips_expanded = False
        tips_body = tk.Frame(tips, bg=EDITOR_THEME.get("card", "#ffffff"))

        tk.Label(
            tips_body,
            text=(
                "• 先在总控台或控件库采集器里扫描目标软件窗口并保存。\n"
                "• 选择左侧控件库文件，中间列表会列出可直接导入当前步骤的控件候选（支持按质量优先排序）。\n"
                "• 质量分级帮助你优先选用更稳定的控件（推荐保留/建议优化/谨慎使用），支持按住 Ctrl/Shift 批量多选。\n"
                "• 导入后会进入当前步骤的“细分控件清单”，流程动作即可直接关联下拉选取。"
            ),
            justify=tk.LEFT,
            anchor="w",
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
            font=("Microsoft YaHei UI", 8),
        ).pack(fill=tk.X, pady=(4, 2))

        def _toggle_tips():
            self._tips_expanded = not self._tips_expanded
            if self._tips_expanded:
                tips_body.pack(fill=tk.X, pady=(4, 0))
                btn_tips_toggle.config(text="▲ 收起帮助")
            else:
                tips_body.pack_forget()
                btn_tips_toggle.config(text="▼ 展开帮助")

        btn_tips_toggle = wt_theme.create_flat_button(
            tips_header,
            "▼ 展开帮助",
            _toggle_tips,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=6,
            pady=1,
        )
        btn_tips_toggle.pack(side=tk.RIGHT)

        # ── 顶部操作工具栏 ──
        toolbar = tk.Frame(self.window, bg=EDITOR_THEME.get("bg", "#f8fafc"), padx=10, pady=6)
        toolbar.pack(fill=tk.X)

        # 第一排：过滤筛选与辅助工具区
        toolbar_row1 = tk.Frame(toolbar, bg=EDITOR_THEME.get("bg", "#f8fafc"))
        toolbar_row1.pack(fill=tk.X)

        tk.Label(
            toolbar_row1,
            text="🔍 过滤：",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).pack(side=tk.LEFT)

        filter_entry = tk.Entry(
            toolbar_row1,
            textvariable=self.var_filter,
            font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("card", "#ffffff"),
        )
        filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6), ipady=2)

        wt_theme.create_flat_button(
            toolbar_row1,
            "✕ 清除",
            lambda: self.var_filter.set(""),
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=6,
            pady=1,
        ).pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(
            toolbar_row1,
            text="排序：",
            font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
        ).pack(side=tk.LEFT)

        ttk.Combobox(
            toolbar_row1,
            textvariable=self.var_sort,
            values=("质量优先", "添加时间-新到旧", "添加时间-旧到新"),
            width=14,
            state="readonly",
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(2, 10))

        tk.Label(
            toolbar_row1,
            text="时间：",
            font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
        ).pack(side=tk.LEFT)

        ttk.Combobox(
            toolbar_row1,
            textvariable=self.var_time_filter,
            values=("全部时间", "最近7天", "最近30天"),
            width=10,
            state="readonly",
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(2, 10))

        wt_theme.create_flat_button(
            toolbar_row1,
            "🔄 刷新",
            self._refresh_file_list,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=8,
            pady=2,
        ).pack(side=tk.LEFT, padx=2)

        wt_theme.create_flat_button(
            toolbar_row1,
            "📁 库目录",
            self._open_control_map_dir,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=8,
            pady=2,
        ).pack(side=tk.LEFT, padx=2)

        wt_theme.create_flat_button(
            toolbar_row1,
            "🎯 打开采集器",
            self._open_control_map_builder,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=8,
            pady=2,
        ).pack(side=tk.LEFT, padx=2)

        # 第二排：模式切换与视图控制
        toolbar_row2 = tk.Frame(toolbar, bg=EDITOR_THEME.get("bg", "#f8fafc"))
        toolbar_row2.pack(fill=tk.X, pady=(6, 0))

        tk.Label(
            toolbar_row2,
            text="范围：",
            font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
        ).pack(side=tk.LEFT)

        for scope_val, scope_label in (("single", "单文件"), ("master", "总控件信息"), ("catalog", "标准目录")):
            tk.Radiobutton(
                toolbar_row2,
                text=scope_label,
                variable=self.var_file_scope,
                value=scope_val,
                command=self._on_file_scope_change,
                bg=EDITOR_THEME.get("bg", "#f8fafc"),
                fg=EDITOR_THEME.get("text", "#1f2d3d"),
                font=("Microsoft YaHei UI", 9),
                activebackground=EDITOR_THEME.get("bg", "#f8fafc"),
            ).pack(side=tk.LEFT, padx=3)

        wt_theme.create_flat_button(
            toolbar_row2,
            "⚡ 合并去重并保存",
            self._merge_and_save_master,
            tone="primary",
            font=("Microsoft YaHei UI", 8, "bold"),
            padx=8,
            pady=1,
        ).pack(side=tk.LEFT, padx=(10, 14))

        tk.Label(
            toolbar_row2,
            text="视图：",
            font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
        ).pack(side=tk.LEFT)

        for view_val, view_label in (("flat", "列表视图"), ("tree", "树形视图")):
            tk.Radiobutton(
                toolbar_row2,
                text=view_label,
                variable=self.var_view_mode,
                value=view_val,
                command=self._on_view_mode_change,
                bg=EDITOR_THEME.get("bg", "#f8fafc"),
                fg=EDITOR_THEME.get("text", "#1f2d3d"),
                font=("Microsoft YaHei UI", 9),
                activebackground=EDITOR_THEME.get("bg", "#f8fafc"),
            ).pack(side=tk.LEFT, padx=3)

        wt_theme.create_flat_button(
            toolbar_row2,
            "展开全部",
            self._cmd_expand_all,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=6,
            pady=1,
        ).pack(side=tk.LEFT, padx=(12, 2))

        wt_theme.create_flat_button(
            toolbar_row2,
            "折叠全部",
            self._cmd_collapse_all,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=6,
            pady=1,
        ).pack(side=tk.LEFT, padx=2)

        self.var_filter.trace_add("write", lambda *_args: self._schedule_filter_refresh())
        self.var_sort.trace_add("write", lambda *_args: self._refresh_file_list())
        self.var_time_filter.trace_add("write", lambda *_args: self._refresh_file_list())

        # ── 中部三栏主工作区 ──
        body = tk.PanedWindow(self.window, orient=tk.HORIZONTAL, sashrelief=tk.FLAT, bg=EDITOR_THEME.get("bg", "#f8fafc"))
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 8))

        # 左栏：控件库文件
        left = tk.LabelFrame(
            body,
            text="📁 控件库文件",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            padx=8,
            pady=8,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        body.add(left, minsize=wt_dpi.scale(260), width=wt_dpi.scale(280))

        tk.Label(
            left,
            textvariable=self.var_file_summary,
            font=("Microsoft YaHei UI", 8),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("primary", "#2563eb"),
            anchor="w",
            padx=6,
            pady=2,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        ).pack(fill=tk.X, pady=(0, 6))

        file_list_wrap = tk.Frame(left, bg=EDITOR_THEME.get("card", "#ffffff"))
        file_list_wrap.pack(fill=tk.BOTH, expand=True)

        self.file_listbox = tk.Listbox(
            file_list_wrap,
            exportselection=False,
            font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT,
            bd=0,
            highlightthickness=0,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            selectbackground=EDITOR_THEME.get("primary", "#2563eb"),
            selectforeground="#ffffff",
        )
        self.file_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.file_listbox.bind("<<ListboxSelect>>", self._on_file_select)
        file_scrollbar = ttk.Scrollbar(file_list_wrap, orient="vertical", command=self.file_listbox.yview)
        file_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.file_listbox.configure(yscrollcommand=file_scrollbar.set)

        # 中栏：控件候选
        middle = tk.LabelFrame(
            body,
            text="🎯 控件候选",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            padx=8,
            pady=8,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        body.add(middle, minsize=wt_dpi.scale(520), width=wt_dpi.scale(680))

        tk.Label(
            middle,
            textvariable=self.var_candidate_summary,
            font=("Microsoft YaHei UI", 8),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("primary", "#2563eb"),
            anchor="w",
            padx=6,
            pady=2,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        ).pack(fill=tk.X, pady=(0, 6))

        control_wrap = tk.Frame(middle, bg=EDITOR_THEME.get("card", "#ffffff"))
        control_wrap.pack(fill=tk.BOTH, expand=True)

        self.control_tree = ttk.Treeview(
            control_wrap,
            columns=("ctrl_type", "quality", "locator", "window"),
            show="tree headings",
            height=14,
            selectmode="extended",
        )
        self.control_tree.heading("#0", text="控件名称")
        self.control_tree.heading("ctrl_type", text="类型")
        self.control_tree.heading("quality", text="质量")
        self.control_tree.heading("locator", text="推荐定位")
        self.control_tree.heading("window", text="窗口")
        self.control_tree.column("#0", width=260, anchor="w")
        self.control_tree.column("ctrl_type", width=90, anchor="w")
        self.control_tree.column("quality", width=95, anchor="center")
        self.control_tree.column("locator", width=240, anchor="w")
        self.control_tree.column("window", width=140, anchor="w")
        self.control_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.control_tree.bind("<<TreeviewSelect>>", self._on_control_select)

        # 质量等级与容器状态样式标签
        self.control_tree.tag_configure("quality_high", foreground="#059669")
        self.control_tree.tag_configure("quality_medium", foreground="#d97706")
        self.control_tree.tag_configure("quality_low", foreground="#dc2626")
        self.control_tree.tag_configure("quality_unverified", foreground="#2563eb")
        self.control_tree.tag_configure("quality_unclassified", foreground="#64748b")
        self.control_tree.tag_configure("container", foreground="#1d4ed8", font=("Microsoft YaHei UI", 9, "bold"))
        self.control_tree.tag_configure("transparent", foreground="#64748b", font=("Microsoft YaHei UI", 9, "italic"))

        control_scrollbar = ttk.Scrollbar(control_wrap, orient="vertical", command=self.control_tree.yview)
        control_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        control_h_scrollbar = ttk.Scrollbar(middle, orient="horizontal", command=self.control_tree.xview)
        control_h_scrollbar.pack(fill=tk.X, pady=(4, 0))
        self.control_tree.configure(yscrollcommand=control_scrollbar.set, xscrollcommand=control_h_scrollbar.set)

        # 右栏：控件详情
        right = tk.LabelFrame(
            body,
            text="🏷️ 控件详细属性",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            padx=8,
            pady=8,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        body.add(right, minsize=wt_dpi.scale(320), width=wt_dpi.scale(440))

        right_header = tk.Frame(right, bg=EDITOR_THEME.get("card", "#ffffff"))
        right_header.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            right_header,
            text="可即时查看定位与诊断属性",
            font=("Microsoft YaHei UI", 8),
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
        ).pack(side=tk.LEFT)

        wt_theme.create_flat_button(
            right_header,
            "📋 复制表达式",
            self._copy_selected_locator,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=6,
            pady=1,
        ).pack(side=tk.RIGHT)

        wt_theme.create_flat_button(
            right_header,
            "📋 复制 JSON",
            self._copy_selected_json,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=6,
            pady=1,
        ).pack(side=tk.RIGHT, padx=(0, 4))

        self.preview_text = scrolledtext.ScrolledText(
            right,
            height=14,
            wrap=tk.WORD,
            font=("Consolas", 9),
            relief=tk.FLAT,
            bd=0,
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        self.preview_text.pack(fill=tk.BOTH, expand=True)

        # ── 底部操作工具栏 ──
        action_row = tk.Frame(self.window, bg=EDITOR_THEME.get("bg", "#f8fafc"), padx=10, pady=8)
        action_row.pack(fill=tk.X)

        # 第一排：导入 / 编辑 / 检验 / 删除 主操作组
        action_row1 = tk.Frame(action_row, bg=EDITOR_THEME.get("bg", "#f8fafc"))
        action_row1.pack(fill=tk.X)

        wt_theme.create_flat_button(
            action_row1,
            "📥 导入所选控件",
            self.import_selected,
            tone="primary",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=4,
        ).pack(side=tk.LEFT, padx=3)

        wt_theme.create_flat_button(
            action_row1,
            "⭐ 导入推荐控件",
            self.import_recommended,
            tone="primary",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=4,
        ).pack(side=tk.LEFT, padx=3)

        wt_theme.create_flat_button(
            action_row1,
            "📦 导入当前文件全部",
            self.import_all,
            tone="secondary",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=4,
        ).pack(side=tk.LEFT, padx=3)

        wt_theme.create_flat_button(
            action_row1,
            "✏️ 编辑所选控件",
            self.edit_selected_control,
            tone="secondary",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=4,
        ).pack(side=tk.LEFT, padx=3)

        wt_theme.create_flat_button(
            action_row1,
            "🗑️ 删除所选控件",
            self.delete_selected_controls,
            tone="danger",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=4,
        ).pack(side=tk.LEFT, padx=3)

        self._btn_test_locator = wt_theme.create_flat_button(
            action_row1,
            "🎯 检验定位",
            self._toggle_probe_button,
            tone="secondary",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=4,
        )
        self._btn_test_locator.pack(side=tk.LEFT, padx=3)

        wt_theme.create_flat_button(
            action_row1,
            "关闭",
            self.on_cancel,
            tone="secondary",
            font=("Microsoft YaHei UI", 9),
            padx=14,
            pady=4,
        ).pack(side=tk.RIGHT, padx=3)

        # 第二排：辅助视图控制与即时反馈
        action_row2 = tk.Frame(action_row, bg=EDITOR_THEME.get("bg", "#f8fafc"))
        action_row2.pack(fill=tk.X, pady=(6, 0))

        wt_theme.create_flat_button(
            action_row2,
            "展开全部",
            self._cmd_expand_all,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=6,
            pady=2,
        ).pack(side=tk.LEFT, padx=2)

        wt_theme.create_flat_button(
            action_row2,
            "折叠全部",
            self._cmd_collapse_all,
            tone="secondary",
            font=("Microsoft YaHei UI", 8),
            padx=6,
            pady=2,
        ).pack(side=tk.LEFT, padx=2)

        ttk.Separator(action_row2, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # 定位结果状态标签
        self.var_locator_result = tk.StringVar(value="")
        self.locator_result_label = tk.Label(
            action_row2,
            textvariable=self.var_locator_result,
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg="#2563eb",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.locator_result_label.pack(side=tk.LEFT, padx=(6, 0))

        tk.Label(
            action_row2,
            textvariable=self.var_status,
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
            fg=EDITOR_THEME.get("muted", "#64748b"),
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.RIGHT)

    def _copy_selected_locator(self):
        selected_ctrl = self._get_selected_single_control()
        if not selected_ctrl:
            self.var_status.set("请先在列表中选中一个控件。")
            return
        method = str(selected_ctrl.get("recommendedTargetMethod", "") or selected_ctrl.get("targetMethod", "")).strip()
        value = str(selected_ctrl.get("recommendedTargetValue", "") or selected_ctrl.get("targetValue", "")).strip()
        locator = f"{method}:{value}".strip(":")
        if not locator:
            self.var_status.set("未找到所选控件的定位数据。")
            return
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(locator)
            self.var_status.set(f"已复制定位表达式到剪贴板：{locator}")
        except Exception:
            self.var_status.set(f"定位表达式：{locator}")

    def _copy_selected_json(self):
        selected_ctrl = self._get_selected_single_control()
        if not selected_ctrl:
            self.var_status.set("请先在列表中选中一个控件。")
            return
        try:
            payload_str = json.dumps(selected_ctrl, ensure_ascii=False, indent=2)
            self.window.clipboard_clear()
            self.window.clipboard_append(payload_str)
            self.var_status.set(f"已复制控件 JSON 数据到剪贴板（{len(payload_str)} 字符）。")
        except Exception:
            self.var_status.set("复制控件 JSON 数据失败。")

    def _get_selected_single_control(self):
        selection = self.control_tree.selection()
        if not selection:
            return None
        iid = selection[0]
        view_mode = self.var_view_mode.get().strip()
        if view_mode == "tree":
            return self._tree_node_map.get(iid)
        try:
            idx = int(iid)
            controls = self._get_filtered_controls()
            if 0 <= idx < len(controls):
                return controls[idx]
        except Exception:
            pass
        return None

    def _refresh_file_list(self):
        if self.var_file_scope.get().strip() == "catalog":
            self._refresh_catalog_group_list()
            return
        os.makedirs(CONTROL_MAP_DIR, exist_ok=True)
        files = []
        # 控件库重整后文件分布在 library/ standard/ recordings/ _candidates/ 等子目录，
        # 需递归扫描（否则根目录已无 json，文件列表会为空）。
        collected_files = []
        for root_dir, _sub_dirs, dir_file_names in os.walk(CONTROL_MAP_DIR):
            for file_name in dir_file_names:
                if file_name.lower().endswith(".json") and not file_name.startswith("."):
                    collected_files.append((file_name, os.path.join(root_dir, file_name)))
        # 元数据索引缓存：控件库目录可达数百 MB，启动时全量 json.load 要数秒。
        # 把每个文件的 targetWindow/controlCount/scanTime 等列表所需元数据缓存到
        # control_maps/.library_index.json，文件 mtime 未变则直接复用，秒开。
        index_path = os.path.join(CONTROL_MAP_DIR, ".library_index.json")
        cache = {}
        try:
            cache_payload = load_json_file(index_path)
            if isinstance(cache_payload, dict) and isinstance(cache_payload.get("files"), dict):
                cache = cache_payload["files"]
        except Exception:
            cache = {}
        changed = False
        for file_name, file_path in sorted(collected_files, key=lambda pair: pair[0], reverse=True):
            mtime = os.path.getmtime(file_path)
            cached = cache.get(file_path)
            if isinstance(cached, dict) and abs(float(cached.get("mtime", 0) or 0) - mtime) < 1.0:
                target_window = str(cached.get("targetWindow", "")).strip()
                control_count = int(cached.get("controlCount", 0) or 0)
                scan_time = str(cached.get("scanTime", "")).strip()
                last_updated = str(cached.get("lastUpdated", "")).strip()
            else:
                payload = load_json_file(file_path)
                if not isinstance(payload, dict):
                    continue
                target_window = payload.get("targetWindow", {}) if isinstance(payload.get("targetWindow"), dict) else {}
                target_window = str(target_window.get("title", "")).strip()
                scan_meta = payload.get("scanMeta", {}) if isinstance(payload.get("scanMeta"), dict) else {}
                control_count = int(scan_meta.get("totalControls", 0) or 0)
                scan_time = str(scan_meta.get("scanTime", "")).strip()
                last_updated = str(payload.get("lastUpdated", "")).strip()
                cache[file_path] = {
                    "mtime": mtime,
                    "targetWindow": target_window,
                    "controlCount": control_count,
                    "scanTime": scan_time,
                    "lastUpdated": last_updated,
                }
                changed = True
            files.append(
                {
                    "path": file_path,
                    "name": file_name,
                    "targetWindow": target_window,
                    "controlCount": control_count,
                    "scanTime": scan_time,
                    "lastUpdated": last_updated,
                    "fileMtime": mtime,
                }
            )
        if changed:
            try:
                tmp_path = index_path + ".tmp"
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump({"files": cache}, f, ensure_ascii=False)
                os.replace(tmp_path, index_path)
            except Exception:
                pass
        files = [item for item in files if self._matches_time_filter(item)]
        files.sort(key=self._build_control_map_file_sort_key, reverse=self.var_sort.get().strip() != "添加时间-旧到新")
        self.control_map_files = files
        self.var_file_summary.set(f"控件库文件：{len(files)}")
        self.file_listbox.delete(0, tk.END)
        for item in files:
            scan_time_label = self._format_control_map_time(item.get("scanTime") or item.get("lastUpdated"))
            label = f"{item['name']} | {item['targetWindow']} | {item['controlCount']}个控件 | {scan_time_label}"
            self.file_listbox.insert(tk.END, label)
        if files:
            self.file_listbox.selection_clear(0, tk.END)
            self.file_listbox.selection_set(0)
            if self.var_file_scope.get().strip() == "master":
                self.current_payload = self._load_master_payload()
                self._refresh_controls_tree()
            else:
                self._on_file_select()
        else:
            self.current_payload = None
            self.control_tree.delete(*self.control_tree.get_children())
            self.preview_text.delete("1.0", tk.END)
            self.var_candidate_summary.set("候选控件：0")
            self.var_status.set("控件库目录里还没有 JSON 文件，请先运行控件库采集器。")

    def _load_selected_payload(self):
        selection = self.file_listbox.curselection()
        if not selection:
            return None
        index = selection[0]
        if not (0 <= index < len(self.control_map_files)):
            return None
        path = self.control_map_files[index]["path"]
        # 文件级缓存：同一文件 mtime 未变时复用已加载 payload，避免重复 json.load
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            mtime = 0.0
        cached = self._payload_file_cache.get(path)
        if isinstance(cached, tuple) and len(cached) == 2 and abs(float(cached[0] or 0) - mtime) < 1.0:
            return cached[1]
        payload = load_json_file(path)
        if isinstance(payload, dict):
            self._payload_file_cache[path] = (mtime, payload)
            # 有界缓存，防长期使用内存无限增长
            if len(self._payload_file_cache) > 32:
                try:
                    self._payload_file_cache.pop(next(iter(self._payload_file_cache)))
                except Exception:
                    pass
        return payload

    def _on_file_select(self, _event=None):
        scope = self.var_file_scope.get().strip()
        if scope == "master":
            return
        if scope == "catalog":
            self._on_catalog_group_select()
            return
        self.current_payload = self._load_selected_payload()
        self._refresh_controls_tree()

    def _on_file_scope_change(self):
        scope = self.var_file_scope.get().strip()
        if scope == "master":
            self.file_listbox.config(state=tk.DISABLED, fg="#999999")
            self.current_payload = self._load_master_payload()
            self._refresh_controls_tree()
        elif scope == "catalog":
            self.file_listbox.config(state=tk.NORMAL, fg="#000000")
            self._refresh_catalog_group_list()
        else:
            self.file_listbox.config(state=tk.NORMAL, fg="#000000")
            # catalog 模式清空了 control_map_files，切回单文件需重建文件列表
            self._refresh_file_list()

    def _load_master_payload(self):
        if not os.path.exists(MASTER_CONTROL_FILE):
            self.var_status.set("总控件信息文件不存在，请先点击“合并去重并保存”生成。")
            return None
        # 复用文件级缓存：总控件信息 16MB+，避免每次切换模式都重复 json.load
        try:
            mtime = os.path.getmtime(MASTER_CONTROL_FILE)
        except Exception:
            mtime = 0.0
        cached = self._payload_file_cache.get(MASTER_CONTROL_FILE)
        if isinstance(cached, tuple) and len(cached) == 2 and abs(float(cached[0] or 0) - mtime) < 1.0:
            return cached[1]
        payload = load_json_file(MASTER_CONTROL_FILE)
        if isinstance(payload, dict):
            self._payload_file_cache[MASTER_CONTROL_FILE] = (mtime, payload)
        return payload

    def _refresh_catalog_group_list(self):
        """标准目录模式：左侧列出 catalog 按窗口分组的 groups（权威库结构）。"""
        if not os.path.exists(CATALOG_FILE):
            self.control_map_files = []
            self._catalog_groups = []
            self.file_listbox.delete(0, tk.END)
            self.current_payload = None
            self.control_tree.delete(*self.control_tree.get_children())
            self.var_file_summary.set("窗口分组：0")
            self.var_status.set("标准目录不存在，请先点击“合并去重并保存”生成。")
            return
        payload = load_json_file(CATALOG_FILE)
        groups = payload.get("groups", []) if isinstance(payload, dict) else []
        self._catalog_groups = [g for g in groups if isinstance(g, dict)]
        self._catalog_generated_at = str(payload.get("generatedAt", "")).strip() if isinstance(payload, dict) else ""
        self.control_map_files = []  # catalog 模式不使用单文件列表
        self.var_file_summary.set("窗口分组：%d" % len(self._catalog_groups))
        self.file_listbox.delete(0, tk.END)
        for grp in self._catalog_groups:
            label = "%s | %s | %d个控件 | high %d" % (
                str(grp.get("windowTitle", "")).strip() or "（未命名窗口）",
                str(grp.get("frameworkId", "")).strip() or "unknown",
                int(grp.get("controlCount", 0) or 0),
                int(grp.get("highCount", 0) or 0),
            )
            self.file_listbox.insert(tk.END, label)
        if self._catalog_groups:
            self.file_listbox.selection_clear(0, tk.END)
            self.file_listbox.selection_set(0)
            self._on_catalog_group_select()
        else:
            self.current_payload = None
            self.control_tree.delete(*self.control_tree.get_children())
            self.var_status.set("标准目录为空，请先采集并合并。")

    def _on_catalog_group_select(self, _event=None):
        """选中窗口分组后，把该组控件包装为现有显示/导入管线兼容的 payload。"""
        selection = self.file_listbox.curselection()
        if not selection:
            return
        groups = getattr(self, "_catalog_groups", [])
        index = selection[0]
        if not (0 <= index < len(groups)):
            return
        grp = groups[index]
        window_title = str(grp.get("windowTitle", "")).strip()
        flat_controls = []
        for ctrl in grp.get("controls", []) or []:
            if not isinstance(ctrl, dict):
                continue
            item = dict(ctrl)
            # catalog 控件的窗口是组级字段，注入顶层供显示/筛选/导入管线使用
            item.setdefault("windowTitle", window_title)
            sources = item.get("sources") or []
            if sources:
                item["_sourceFile"] = ", ".join(str(s) for s in sources)
            flat_controls.append(item)
        self.current_payload = {
            "schemaVersion": "standard-catalog-group",
            "targetWindow": {"title": window_title, "frameworkId": str(grp.get("frameworkId", "")).strip()},
            "scanMeta": {
                "scanTime": getattr(self, "_catalog_generated_at", ""),
                "totalControls": len(flat_controls),
                "mode": "standard-catalog-group",
            },
            "flatControls": flat_controls,
        }
        self._refresh_controls_tree()
        self.var_status.set(
            "标准目录分组：%s，%d个控件（合并产物，只读）" % (window_title or "（未命名窗口）", len(flat_controls))
        )

    def _merge_and_save_master(self):
        """合并控件库：标准目录（权威） + 复核报告 + 总控件信息（派生平铺）。

        委托 tools.merge_standard_control_library.run_merge 一站式产出三份文件，
        合并逻辑只有一份（多实例不塌缩、增强字段全保留、controlsTree、lastSeen）；
        总控件信息.json 改为从标准目录派生，与本按钮产出的 catalog 数据同源。
        后台线程执行避免合并大库时卡 UI，完成后回主线程刷新视图。
        """
        if getattr(self, "_merge_running", False):
            self.var_status.set("控件库合并正在进行中，请稍候…")
            return
        self._merge_running = True
        self.var_status.set("正在合并控件库（标准目录 + 总控件信息派生）…")

        catalog_path = os.path.join(CONTROL_MAP_DIR, "standard", "standard_control_catalog.json")
        report_path = os.path.join(CONTROL_MAP_DIR, "standard", "standard_catalog_mismatch_report.json")

        def _worker():
            try:
                from tools import merge_standard_control_library as msl

                def _progress(pct, msg):
                    self.window.after(0, lambda p=pct, m=msg: self.var_status.set(
                        f"正在合并控件库 ({p}%): {m}"))

                stats = msl.run_merge(CONTROL_MAP_DIR, catalog_path, report_path,
                                      MASTER_CONTROL_FILE, progress_callback=_progress)
            except Exception as exc:  # noqa: BLE001 - 合并失败需完整反馈到状态栏
                self.window.after(0, lambda: self._on_merge_done(None, exc))
            else:
                self.window.after(0, lambda: self._on_merge_done(stats, None))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_merge_done(self, stats, error):
        self._merge_running = False
        if error is not None:
            self.var_status.set("控件库合并失败：%s" % error)
            return
        self.var_file_scope.set("master")
        self._on_file_scope_change()
        self.var_status.set(
            "合并完成：%d个窗口分组，%d个控件（high %d / medium %d / low %d），待复核%d项；标准目录+总控件信息已更新"
            % (
                stats.get("groups", 0),
                stats.get("totalControls", 0),
                stats.get("high", 0),
                stats.get("medium", 0),
                stats.get("lowOrUnknown", 0),
                stats.get("needsReview", 0),
            )
        )

    def _build_controls_from_payload(self):
        if not isinstance(self.current_payload, dict):
            return []
        # 缓存命中：payload 是同一对象时直接复用，避免每次过滤/刷新都全量重建。
        # 注意比较对象本身而非 id()：id() 返回新 int 对象，is 恒为 False（曾致缓存 100% 失效）
        if self._controls_cache_key is self.current_payload:
            return self._controls_cache_value
        controls = self.current_payload.get("controlDefinitions", [])
        flat_controls = self.current_payload.get("flatControls", [])
        if isinstance(controls, list) and controls:
            result = []
            for index, control in enumerate(controls):
                merged = self._merge_control_map_control_metadata(
                    normalize_control(control, index),
                    flat_controls[index] if index < len(flat_controls) else {},
                )
                # 记录在原始数组中的真实下标，供编辑/校验回写时定位，
                # 避免列表排序后按显示位置错取控件
                merged["_sourceIndex"] = index
                result.append(merged)
            self._controls_cache_key = self.current_payload
            self._controls_cache_value = result
            return result
        flat_controls = self.current_payload.get("flatControls", [])
        generated = []
        for index, item in enumerate(flat_controls):
            inspect_data = item.get("inspectData", {}) if isinstance(item.get("inspectData"), dict) else {}
            merged = self._merge_control_map_control_metadata(
                normalize_control(
                    {
                        "id": f"control_map_{index + 1}",
                        "name": item.get("savedControlName", "") or item.get("suggestedControlName", "") or item.get("displayName", "") or inspect_data.get("name", "") or f"控件 {index + 1}",
                        "role": f"来自控件库扫描：{item.get('windowTitle', '')}",
                        "windowTitle": item.get("windowTitle", "") or self.default_window_title,
                        # catalog 控件用 targetMethod/targetValue 键名，需回退兼容
                        "targetMethod": item.get("recommendedTargetMethod", "") or item.get("targetMethod", ""),
                        "targetValue": item.get("recommendedTargetValue", "") or item.get("targetValue", ""),
                        "uiPath": item.get("uiPath", ""),
                        # labelText 是多实例判别核心字段，需透传给 normalize_control 供过滤/显示
                        "labelText": item.get("labelText", ""),
                        "relatedLabelName": item.get("relatedLabelName", ""),
                        "tabNavigation": item.get("tabNavigation", {}),
                        "optionValues": item.get("optionValues", []),
                        "notes": f"由控件库扫描导入，定位评分={item.get('locatorScore', 0)}",
                        "rawInspectText": item.get("rawInspectText", ""),
                        "auxChecks": item.get("auxChecks", []),
                        "inspectData": inspect_data,
                    },
                    index,
                ),
                item,
            )
            merged["_sourceIndex"] = index
            generated.append(merged)
        self._controls_cache_key = self.current_payload
        self._controls_cache_value = generated
        return generated

    def _merge_control_map_control_metadata(self, control, flat_item):
        merged = dict(control or {})
        flat_item = flat_item if isinstance(flat_item, dict) else {}
        quality_tier = str(flat_item.get("qualityTier", "")).strip()
        quality_reason = str(flat_item.get("qualityReason", "")).strip()
        suggested_name = str(flat_item.get("suggestedControlName", "")).strip()
        locator_score = int(flat_item.get("locatorScore", 0) or 0)
        merged["_qualityTier"] = quality_tier or "未分类"
        merged["_qualityReason"] = quality_reason
        merged["_suggestedControlName"] = suggested_name
        merged["_locatorScore"] = locator_score
        scan_meta = self.current_payload.get("scanMeta", {}) if isinstance(self.current_payload, dict) else {}
        merged["_scanTime"] = str(scan_meta.get("scanTime", "") or self.current_payload.get("lastUpdated", "")).strip() if isinstance(self.current_payload, dict) else ""
        merged["_addedAt"] = merged.get("_scanTime", "")
        notes = str(merged.get("notes", "")).strip()
        extra_notes = []
        if quality_tier and f"质量={quality_tier}" not in notes:
            extra_notes.append(f"质量={quality_tier}")
        if quality_reason and f"说明={quality_reason}" not in notes:
            extra_notes.append(f"说明={quality_reason}")
        if extra_notes:
            merged["notes"] = " | ".join([item for item in [notes] + extra_notes if item]).strip(" |")
        return merged

    def _get_filtered_controls(self):
        controls = self._build_controls_from_payload()
        keyword = self.var_filter.get().strip().lower()
        filtered = []
        for control in controls:
            haystack = " ".join(
                [
                    str(control.get("id", "")),
                    str(control.get("name", "")),
                    str(control.get("windowTitle", "")),
                    str(control.get("targetMethod", "")),
                    str(control.get("targetValue", "")),
                    str(control.get("_qualityTier", "")),
                    str(control.get("_qualityReason", "")),
                    # labelText 是多实例判别核心字段（半径/X/载入），需可按标签搜索
                    str(control.get("labelText", "") or (control.get("inspectData", {}) or {}).get("labelText", "")),
                    str((control.get("inspectData", {}) or {}).get("className", "")),
                    str((control.get("inspectData", {}) or {}).get("controlType", "")),
                    str(control.get("_addedAt", "")),
                ]
            ).lower()
            if keyword and keyword not in haystack:
                continue
            if not self._matches_time_filter({"scanTime": control.get("_addedAt", "")}):
                continue
            filtered.append(control)
        if self.var_sort.get().strip() == "质量优先":
            filtered.sort(
                key=lambda control: (
                    0 if str(control.get("qualityTier", "") or control.get("_qualityTier", "")).strip() == "推荐保留" else 1,
                    -int(control.get("locatorScore", 0) or control.get("_locatorScore", 0) or 0),
                    str(control.get("name", "")).strip(),
                )
            )
        elif self.var_sort.get().strip() == "添加时间-旧到新":
            filtered.sort(key=lambda control: (self._control_map_timestamp(control.get("_addedAt", "")), str(control.get("name", "")).strip()))
        else:
            filtered.sort(
                key=lambda control: (self._control_map_timestamp(control.get("_addedAt", "")), str(control.get("name", "")).strip()),
                reverse=True,
            )
        return filtered

    def _control_map_timestamp(self, value):
        return control_map_timestamp(value)

    def _format_control_map_time(self, value):
        timestamp = self._control_map_timestamp(value)
        if timestamp <= 0:
            return "时间未知"
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")

    def _matches_time_filter(self, item):
        mode = self.var_time_filter.get().strip()
        if mode == "全部时间":
            return True
        timestamp = self._control_map_timestamp((item or {}).get("scanTime") or (item or {}).get("lastUpdated"))
        if timestamp <= 0:
            return False
        now = datetime.now().timestamp()
        days = 7 if mode == "最近7天" else 30
        return now - timestamp <= days * 24 * 60 * 60

    def _build_control_map_file_sort_key(self, item):
        if self.var_sort.get().strip() == "质量优先":
            return (
                int((item or {}).get("controlCount", 0) or 0),
                self._control_map_timestamp((item or {}).get("scanTime") or (item or {}).get("lastUpdated")),
                str((item or {}).get("name", "")).strip(),
            )
        return (
            self._control_map_timestamp((item or {}).get("scanTime") or (item or {}).get("lastUpdated")),
            float((item or {}).get("fileMtime", 0.0) or 0.0),
            str((item or {}).get("name", "")).strip(),
        )

    def _on_view_mode_change(self):
        """切换视图模式"""
        self._refresh_controls_tree()

    def _schedule_filter_refresh(self):
        """过滤关键字输入防抖：取消上一次定时器，300ms 后重建一次树。

        大文件（总控件信息 16MB+）下每次按键全量重建树会卡顿，合并连续输入
        只在停顿后重建一次，显著降低输入时的卡顿。
        """
        if self._filter_debounce_after is not None:
            try:
                self.window.after_cancel(self._filter_debounce_after)
            except Exception:
                pass
            self._filter_debounce_after = None
        try:
            self._filter_debounce_after = self.window.after(300, self._refresh_controls_tree)
        except Exception:
            self._refresh_controls_tree()

    def _refresh_controls_tree(self):
        self.control_tree.delete(*self.control_tree.get_children())
        view_mode = self.var_view_mode.get().strip()
        if view_mode == "flat":
            self._refresh_flat_view()
        else:
            self._refresh_tree_view()

    @staticmethod
    def _control_display_fields(control):
        """统一读取控件库显示字段，兼容旧 controlDefinitions 和新 flatControls。

        优先级与 ControlEditDialog._load_control_data 保持一致，确保
        列表、预览、编辑对话框三处看到的信息完全相同。
        """
        control = control if isinstance(control, dict) else {}
        inspect_data = control.get("inspectData") if isinstance(control.get("inspectData"), dict) else {}
        # 名称：优先 displayName（编辑对话框保存时写入），与编辑对话框顺序一致
        name = str(control.get("displayName", "") or control.get("name", "") or inspect_data.get("name", "")).strip()
        control_type = normalize_control_type_name(
            control.get("controlType", "") or inspect_data.get("controlType", ""),
            control.get("localizedControlType", "") or inspect_data.get("localizedControlType", ""),
        )
        quality = str(control.get("qualityTier", "") or control.get("_qualityTier", "") or "未分类").strip()
        # 推荐定位：不向下回退 inspectData（编辑对话框也不回退，保持一致）
        method = str(
            control.get("recommendedTargetMethod", "")
            or control.get("targetMethod", "")
        ).strip()
        value = str(
            control.get("recommendedTargetValue", "")
            or control.get("targetValue", "")
        ).strip()
        locator = f"{method}:{value}".strip(":")
        # 窗口：只使用 windowTitle（与编辑对话框一致），不混入 targetWindow
        window = str(control.get("windowTitle", "")).strip()
        return name, quality, control_type, locator, window

    @staticmethod
    def _format_quality_display(quality_raw):
        """格式化质量评级显示，并返回对应语义色彩 tag。"""
        q = str(quality_raw or "").strip()
        if not q or q in ("未分类", "none", "unknown"):
            return "⚪ 未分类", "quality_unverified"
        if any(w in q for w in ("推荐", "high", "高", "优质")):
            return "✓ 推荐保留", "quality_high"
        elif any(w in q for w in ("优化", "medium", "中", "良好")):
            return "⚡ 建议优化", "quality_medium"
        elif any(w in q for w in ("谨慎", "low", "低", "差")):
            return "⚠️ 谨慎使用", "quality_low"
        elif "待验证" in q or "verify" in q.lower():
            return "🔍 待验证", "quality_unverified"
        return f"• {q}", "quality_unverified"

    def _refresh_flat_view(self):
        """列表视图：显示序号、控件名称、类型、质量、推荐定位和窗口六列。"""
        self.control_tree.configure(
            columns=("seq", "name", "ctrl_type", "quality", "locator", "window"),
            show="headings",
        )
        self.control_tree.heading("seq", text="#")
        self.control_tree.heading("name", text="控件名称")
        self.control_tree.heading("ctrl_type", text="类型")
        self.control_tree.heading("quality", text="质量")
        self.control_tree.heading("locator", text="推荐定位")
        self.control_tree.heading("window", text="窗口")
        # 列宽与树形视图一致的自适应策略：初始总宽贴近中栏可用宽度，
        # name/locator 可拉伸，窗口拉宽时自动扩展。
        self.control_tree.column("seq", width=40, minwidth=36, anchor="center", stretch=False)
        self.control_tree.column("name", width=165, minwidth=120, stretch=True, anchor="w")
        self.control_tree.column("ctrl_type", width=75, minwidth=50, stretch=False, anchor="w")
        self.control_tree.column("quality", width=80, minwidth=60, stretch=False, anchor="center")
        self.control_tree.column("locator", width=165, minwidth=110, stretch=True, anchor="w")
        self.control_tree.column("window", width=115, minwidth=80, stretch=False, anchor="w")

        # 质量等级语义色彩 tags
        self.control_tree.tag_configure("quality_high", foreground="#15803d")
        self.control_tree.tag_configure("quality_medium", foreground="#b45309")
        self.control_tree.tag_configure("quality_low", foreground="#b91c1c")
        self.control_tree.tag_configure("quality_unverified", foreground="#64748b")

        controls = self._get_filtered_controls()
        payload_title = ""
        if isinstance(self.current_payload, dict):
            payload_title = str(((self.current_payload.get("targetWindow", {}) or {}).get("title", ""))).strip()
        for index, control in enumerate(controls, start=1):
            name, quality, control_type, locator, window_title = self._control_display_fields(control)
            quality_badge, quality_tag = self._format_quality_display(quality)
            self.control_tree.insert(
                "",
                tk.END,
                iid=str(index - 1),
                values=(index, name, control_type, quality_badge, locator, window_title),
                tags=(quality_tag,),
            )
        self.preview_text.delete("1.0", tk.END)
        summary_parts = [f"候选控件：{len(controls)}"]
        if payload_title:
            summary_parts.append(f"当前窗口：{payload_title}")
        self.var_candidate_summary.set(" | ".join(summary_parts))
        if controls:
            self.control_tree.selection_set("0")
            self._on_control_select()
            self.var_status.set(f"当前可导入控件 {len(controls)} 个。")
        else:
            self.var_status.set("当前筛选条件下没有可导入控件。")

    def _refresh_tree_view(self):
        """树形视图 - 从 uiPath 或 parentIndex 构建可折叠树形结构"""
        self.control_tree.configure(
            columns=("ctrl_type", "quality", "locator", "window"),
            show="tree headings",
        )
        self.control_tree.heading("#0", text="控件名称")
        self.control_tree.heading("ctrl_type", text="类型")
        self.control_tree.heading("quality", text="质量")
        self.control_tree.heading("locator", text="推荐定位")
        self.control_tree.heading("window", text="窗口")
        # 列自适应树宽：初始总宽 650 低于中栏默认可用宽度（约 676），
        # 避免树形视图一打开就出现横向滚动条；stretch=True 的列在窗口拉宽时
        # 自动扩展，多余空间优先分配给名称(#0)/推荐定位列。
        # 名称列带树形缩进/图标，给足宽度；总宽 = 190+75+80+170+115 = 630，
        # 低于中栏默认可用宽度（约 636），且 #0/locator 可拉伸，默认不出现横向滚动条。
        self.control_tree.column("#0", width=190, minwidth=130, stretch=True, anchor="w")
        self.control_tree.column("ctrl_type", width=75, minwidth=50, stretch=False, anchor="w")
        self.control_tree.column("quality", width=80, minwidth=60, stretch=False, anchor="center")
        self.control_tree.column("locator", width=170, minwidth=110, stretch=True, anchor="w")
        self.control_tree.column("window", width=115, minwidth=80, stretch=False, anchor="w")
        # 节点 tag：主（容器/面板）加粗主色，次（叶子控件）普通，透明容器浅灰斜体，
        # 同 uiPath 多实例叶子更浅、合成占位灰。
        self.control_tree.tag_configure("container", foreground="#1d4ed8", font=("Microsoft YaHei UI", 9, "bold"))
        self.control_tree.tag_configure("leaf", foreground="#1f2937")
        self.control_tree.tag_configure("synthetic", foreground="#94a3b8")
        self.control_tree.tag_configure("transparent", foreground="#94a3b8", font=("Microsoft YaHei UI", 9, "italic"))
        self.control_tree.tag_configure("instance", foreground="#64748b")
        self.control_tree.tag_configure("quality_high", foreground="#15803d")
        self.control_tree.tag_configure("quality_medium", foreground="#b45309")
        self.control_tree.tag_configure("quality_low", foreground="#b91c1c")
        self.control_tree.tag_configure("quality_unverified", foreground="#64748b")
        if self.var_file_scope.get().strip() in ("master", "catalog"):
            self.control_tree.heading("window", text="来源文件")
        else:
            self.control_tree.heading("window", text="窗口")

        payload = self.current_payload if isinstance(self.current_payload, dict) else {}
        flat_controls = payload.get("flatControls", [])
        control_defs = payload.get("controlDefinitions", [])

        # 如果 flatControls 为空，尝试使用 controlDefinitions
        if not flat_controls and control_defs:
            flat_controls = []
            for idx, ctrl in enumerate(control_defs):
                if not isinstance(ctrl, dict):
                    continue
                inspect_data = ctrl.get("inspectData", {}) if isinstance(ctrl.get("inspectData"), dict) else {}
                # 为 controlDefinitions 构建类似 flatControls 的结构
                flat_item = {
                    "displayName": ctrl.get("name", ""),
                    "name": ctrl.get("name", ""),
                    "targetMethod": ctrl.get("targetMethod", ""),
                    "targetValue": ctrl.get("targetValue", ""),
                    "uiPath": ctrl.get("uiPath", ""),
                    "windowTitle": ctrl.get("windowTitle", ""),
                    "qualityTier": ctrl.get("_qualityTier", ""),
                    "qualityReason": ctrl.get("_qualityReason", ""),
                    "controlType": inspect_data.get("controlType", ""),
                    "localizedControlType": inspect_data.get("localizedControlType", ""),
                    "automationId": inspect_data.get("automationId", ""),
                    "className": inspect_data.get("className", ""),
                    "inspectData": inspect_data,
                    "recommendedTargetMethod": ctrl.get("targetMethod", ""),
                    "recommendedTargetValue": ctrl.get("targetValue", ""),
                }
                flat_controls.append(flat_item)

        if not flat_controls:
            self._refresh_flat_view()
            return

        keyword = self.var_filter.get().strip().lower()

        # 优先使用采集器落盘的 controlsTree 渲染真实层级树（与采集器 GUI 完全一致）；
        # master/总控件信息等派生产物没有 controlsTree，才回退下面的 uiPath 重建。
        if isinstance(payload.get("controlsTree"), dict):
            self._refresh_tree_from_controls_tree(flat_controls, keyword)
            return

        # ---------- 1. 构建路径树 ----------
        # tree: uiPath -> {path, name, children:[path], synthetic:bool, controls:[dict], _order:int}
        # 将每个控件的 uiPath（如 "Window > A > B > Btn"）拆成所有前缀路径作为节点
        tree = {}
        real_paths = set()
        self._tree_node_map = {}    # iid(uiPath) -> flatControl dict（只有真实控件）
        self._tree_node_index = {}  # iid(uiPath) -> 在 flatControls 中的原始索引

        for idx, ctrl in enumerate(flat_controls):
            ui_path = str(ctrl.get("uiPath", "")).strip()
            if not ui_path:
                continue
            real_paths.add(ui_path)
            # 同一 uiPath 可能挂多个实例（模板复制控件，如各 interest-area 面板的
            # InterestAreas_Button_Add/Edit/Delete/Import），这里只保底挂第一条，
            # 多实例在 _insert_node 阶段以 path|#k 唯一 iid 补挂叶子行，
            # 避免后写覆盖导致树形视图只显示一条。
            self._tree_node_map.setdefault(ui_path, ctrl)
            self._tree_node_index.setdefault(ui_path, idx)

            segments = ui_path.split(" > ")
            # 为其所有前缀路径创建占位节点
            for i in range(1, len(segments)):
                prefix = " > ".join(segments[:i])
                if prefix not in tree:
                    tree[prefix] = {
                        "path": prefix,
                        "name": segments[i - 1],
                        "children": [],
                        "synthetic": True,
                        "controls": [],
                        "flat_indexes": [],
                        "_order": idx,
                    }
            # 完整路径节点（真实控件）；flat_indexes 与 controls 一一对应，
            # 供多实例叶子行映射回 flatControls 原始下标。
            if ui_path not in tree:
                tree[ui_path] = {
                    "path": ui_path,
                    "name": segments[-1],
                    "children": [],
                    "synthetic": False,
                    "controls": [],
                    "flat_indexes": [],
                    "_order": idx,
                }
            tree[ui_path]["controls"].append(ctrl)
            # uiPath 可能先以"前缀占位节点"（synthetic）存在、后作为真实控件路径出现，
            # 用 setdefault 兜底，避免缺键导致树形视图整棵空白。
            tree[ui_path].setdefault("flat_indexes", []).append(idx)
            tree[ui_path]["synthetic"] = False
            tree[ui_path]["_order"] = min(tree[ui_path]["_order"], idx)

        # ---------- 2. 建立父子关系 ----------
        roots = []
        for path, node in tree.items():
            segs = path.split(" > ")
            if len(segs) == 1:
                roots.append(path)
            else:
                parent_path = " > ".join(segs[:-1])
                if parent_path in tree:
                    tree[parent_path]["children"].append(path)
                else:
                    roots.append(path)

        # 按首次出现顺序排序
        for node in tree.values():
            node["children"].sort(key=lambda p: tree[p]["_order"])
        roots.sort(key=lambda p: tree[p]["_order"])

        # ---------- 3. 筛选：标记可见节点（自身或后代匹配关键词）----------
        def _ctrl_matches_keyword(ctrl_dict, kw):
            haystack = " ".join([
                str(ctrl_dict.get(k, ""))
                for k in ("displayName", "name", "automationId", "className",
                          "recommendedTargetMethod", "recommendedTargetValue",
                          "targetMethod", "targetValue", "labelText", "relatedLabelName")
            ]).lower()
            return kw in haystack

        visible = set()

        def _mark_visible(path):
            if path in visible:
                return False
            node = tree.get(path)
            if node is None:
                return False
            any_child = False
            for child_path in node["children"]:
                if _mark_visible(child_path):
                    any_child = True
            self_matches = any(_ctrl_matches_keyword(ctrl, keyword) for ctrl in node["controls"])
            if self_matches or any_child:
                visible.add(path)
                # 向上传递到祖先
                segs = path.split(" > ")
                for i in range(1, len(segs)):
                    ancestor = " > ".join(segs[:i])
                    if ancestor in tree:
                        visible.add(ancestor)
                return True
            return False

        if not keyword:
            visible = set(tree.keys())
        else:
            for root_path in roots:
                _mark_visible(root_path)

        # ---------- 4. 插入 Treeview ----------
        self.control_tree.delete(*self.control_tree.get_children())

        def _insert_node(parent_iid, path):
            if path not in visible:
                return
            node = tree[path]
            ctrl = node["controls"][0] if node["controls"] else None

            if ctrl:
                _name, quality, ctrl_type, locator, window_title = self._control_display_fields(ctrl)
                quality_badge, q_tag = self._format_quality_display(quality)
                if self.var_file_scope.get().strip() in ("master", "catalog"):
                    window_title = str(ctrl.get("_sourceFile", "") or window_title).strip()
            else:
                ctrl_type = quality_badge = locator = window_title = ""
                q_tag = "quality_unverified"

            display_name = f"[{node['name']}]" if node["synthetic"] else node["name"]

            iid = self.control_tree.insert(
                parent_iid, tk.END, iid=path,
                text=display_name,
                values=(ctrl_type, quality_badge, locator, window_title),
            )
            # 主次节点区分：合成前缀/占位 → synthetic；有子节点 → container；否则 → leaf
            if node["synthetic"]:
                self.control_tree.item(iid, tags=("synthetic",))
            elif node["children"]:
                self.control_tree.item(iid, tags=("container",))
            else:
                self.control_tree.item(iid, tags=("leaf", q_tag))

            # 同 uiPath 多实例（模板复制控件，如各 interest-area 面板的 添加/编辑/删除 等）：
            # 主行之外每个实例补一行叶子，iid=path|#k 保证唯一，并映射到各自 flat 条目，
            # 树形视图不再因 uiPath 相同而塌缩丢失。
            extra_controls = node.get("controls", [])[1:]
            extra_indexes = node.get("flat_indexes", [])[1:]
            for _k, (extra_ctrl, extra_idx) in enumerate(zip(extra_controls, extra_indexes), start=1):
                _e_name, _e_quality, _e_type, _e_locator, _e_window = self._control_display_fields(extra_ctrl)
                _e_badge, _e_qtag = self._format_quality_display(_e_quality)
                if self.var_file_scope.get().strip() in ("master", "catalog"):
                    _e_window = str(extra_ctrl.get("_sourceFile", "") or _e_window).strip()
                leaf_iid = f"{path}|#{_k}"
                leaf_name = (
                    str(extra_ctrl.get("displayName", "") or "").strip()
                    or str(extra_ctrl.get("name", "") or "").strip()
                    or str(extra_ctrl.get("automationId", "") or "").strip()
                    or node["name"]
                )
                self.control_tree.insert(
                    iid, tk.END, iid=leaf_iid,
                    text=f"实例 {_k}: {leaf_name}",
                    values=(_e_type, _e_badge, _e_locator, _e_window),
                )
                self.control_tree.item(leaf_iid, tags=("instance", _e_qtag))
                self._tree_node_map[leaf_iid] = extra_ctrl
                self._tree_node_index[leaf_iid] = extra_idx

            # 默认只展开前 2 层
            depth = len(path.split(" > "))
            if depth <= 2:
                self.control_tree.item(iid, open=True)

            for child_path in node["children"]:
                _insert_node(iid, child_path)

        for root_path in roots:
            _insert_node("", root_path)

        # ---------- 5. 状态更新 ----------
        self.preview_text.delete("1.0", tk.END)
        # 按真实控件数统计（同 uiPath 多实例占多行，不能按路径数算）
        visible_real = sum(
            len(tree[p].get("controls") or [])
            for p in visible
            if not tree[p].get("synthetic") and tree[p].get("controls")
        )
        payload_title = ""
        if isinstance(self.current_payload, dict):
            payload_title = str(((self.current_payload.get("targetWindow", {}) or {}).get("title", ""))).strip()
        scan_meta = self.current_payload.get("scanMeta", {}) if isinstance(self.current_payload, dict) else {}
        summary_parts = [f"候选控件：{visible_real}"]
        if payload_title:
            summary_parts.append(f"当前窗口：{payload_title}")
        if self.var_file_scope.get().strip() == "master":
            raw_total = int(scan_meta.get("rawTotalControls", 0) or 0)
            dupes = int(scan_meta.get("duplicatesRemoved", 0) or 0)
            source_count = len(scan_meta.get("sourceFiles", []))
            summary_parts.append(f"来源{source_count}文件")
            if dupes > 0:
                summary_parts.append(f"去重{dupes}个")
        summary_parts.append("树形视图")
        self.var_candidate_summary.set(" | ".join(summary_parts))
        if visible_real > 0:
            first_real = next((p for p in roots if p in visible and not tree[p].get("synthetic")), roots[0] if roots else "")
            if first_real:
                self.control_tree.selection_set(first_real)
                self._on_control_select()
            self.var_status.set(f"树形视图：共 {visible_real} 个控件，可折叠展开。")
        else:
            self.var_status.set("当前筛选条件下没有可导入控件。")

    def _refresh_tree_from_controls_tree(self, flat_controls, keyword):
        """按采集器落盘的 controlsTree 渲染真实层级树（与采集器 GUI 一致）。

        采集器保存的 controlsTree 保留真实父子关系（容器/叶子层次），比按 uiPath 字符串
        重建的路径树信息完整（同一 uiPath 的模板复制控件天然分列不同父节点下，不会塌缩）。
        仅在 payload 含 controlsTree（录制快照）时使用；master 等派生产物无此字段时
        _refresh_tree_view 仍走 uiPath 重建回退。
        """
        payload = self.current_payload if isinstance(self.current_payload, dict) else {}
        controls_tree = payload.get("controlsTree")
        # 与采集器一致的语义名提炼（helpText → 真实功能名，如"添加新配置"）
        from build_control_map_library import _extract_functional_name
        # 索引 flat 条目：controlsTree 节点 index/flatIndex 与 flatControls 的 index 对应
        flat_by_index = {}
        for f in flat_controls:
            if not isinstance(f, dict):
                continue
            try:
                fi = int(f.get("index", -1))
            except Exception:
                continue
            if fi >= 0:
                flat_by_index[fi] = f

        self.control_tree.delete(*self.control_tree.get_children())
        self._tree_node_map = {}    # iid -> ctrl（真实控件优先映射到 flat 条目）
        self._tree_node_index = {}  # iid -> flatControls 原始下标

        def _ctrl_matches_keyword(ctrl_dict, kw):
            haystack = " ".join([
                str(ctrl_dict.get(k, ""))
                for k in ("displayName", "name", "automationId", "className",
                          "recommendedTargetMethod", "recommendedTargetValue",
                          "targetMethod", "targetValue", "labelText", "relatedLabelName",
                          "functionText", "suggestedControlName", "helpText")
            ]).lower()
            return kw in haystack

        def _node_visible(node):
            # 自身或任一后代匹配即视为可见
            ctrl = _node_flat_or_node(node)
            if ctrl is not None and _ctrl_matches_keyword(ctrl, keyword):
                return True
            for ch in node.get("children", []) or []:
                if _node_visible(ch):
                    return True
            return False

        def _node_flat_or_node(node):
            try:
                fi = int(node.get("index", -1))
            except Exception:
                fi = -1
            if fi >= 0 and fi in flat_by_index:
                return flat_by_index[fi]
            return node

        def _insert_node(node, parent_iid, depth=0):
            if not isinstance(node, dict):
                return
            if keyword and not _node_visible(node):
                return
            ctrl = _node_flat_or_node(node)
            # 显示名：与采集器一致，优先语义名（helpText 提炼/已存名），再回退真实名
            semantic = (
                str(ctrl.get("functionText", "")).strip()
                or _extract_functional_name(ctrl)
                or str(ctrl.get("savedControlName", "")).strip()
                or str(ctrl.get("suggestedControlName", "")).strip()
                or str(ctrl.get("displayName", "")).strip()
                or str(ctrl.get("automationId", "")).strip()
                or f"[{node.get('controlType', 'Unknown')}]"
            )
            # 展示名与采集器层级树一致：语义名（helpText 提炼/已存名）优先，
            # 图标按钮（SVG path）回退原始名，保证可读性
            name = semantic
            control_type = str(ctrl.get("controlType", "") or node.get("controlType", "") or "Unknown").strip()
            quality = str(ctrl.get("qualityTier", "") or ctrl.get("_qualityTier", "") or "未分类").strip()
            quality_badge, q_tag = self._format_quality_display(quality)
            method = str(ctrl.get("recommendedTargetMethod", "") or ctrl.get("targetMethod", "")).strip()
            value = str(ctrl.get("recommendedTargetValue", "") or ctrl.get("targetValue", "")).strip()
            locator = f"{method}:{value}".strip(":")
            window_title = str(ctrl.get("windowTitle", "")).strip()
            if self.var_file_scope.get().strip() in ("master", "catalog"):
                window_title = str(ctrl.get("_sourceFile", "") or window_title).strip()
            is_transparent = bool(node.get("isTransparentContainer"))
            has_children = bool(node.get("children"))
            # 主次节点区分：透明容器 → transparent；面板/容器 → container（加粗主色）；叶子 → leaf
            if is_transparent:
                prefix, tags = "○", ("transparent",)
            elif has_children:
                prefix, tags = "▸", ("container",)
            else:
                prefix, tags = "●", ("leaf", q_tag)
            self._tree_seq += 1
            iid = f"hierarchy:{self._tree_seq}"
            self.control_tree.insert(
                parent_iid, tk.END, iid=iid,
                open=(depth < 2),
                text=f"{prefix} {name}",
                values=(control_type, quality_badge, locator, window_title),
                tags=tags,
            )
            try:
                flat_index = int(ctrl.get("index", -1))
            except Exception:
                flat_index = -1
            self._tree_node_map[iid] = ctrl
            # 无对应 flat 条目的纯容器/占位节点记 -1（不参与候选计数）
            self._tree_node_index[iid] = flat_index if flat_index >= 0 else -1
            for ch in node.get("children", []) or []:
                _insert_node(ch, iid, depth + 1)

        root = controls_tree
        if root is not None:
            _insert_node(root, "", 0)

        # ---------- 状态更新 ----------
        self.preview_text.delete("1.0", tk.END)
        # 候选控件数 = 树中挂到真实 flat 条目的节点数（与采集器"控件总数"对齐，
        # 排除纯容器/未入 flat 的占位节点）；多实例控件的每个实例都是独立 flat 条目，
        # 自然计入，不再像 uiPath 重建那样塌缩。
        mapped_indices = set()
        for idx in self._tree_node_index.values():
            try:
                idx = int(idx)
            except Exception:
                continue
            if idx >= 0:
                mapped_indices.add(idx)
        visible_real = len(mapped_indices)
        payload_title = str((payload.get("targetWindow", {}) or {}).get("title", "")).strip()
        scan_meta = payload.get("scanMeta", {}) if isinstance(payload.get("scanMeta"), dict) else {}
        summary_parts = [f"候选控件：{visible_real}"]
        if payload_title:
            summary_parts.append(f"当前窗口：{payload_title}")
        if self.var_file_scope.get().strip() == "master":
            source_count = len(scan_meta.get("sourceFiles", []))
            summary_parts.append(f"来源{source_count}文件")
        summary_parts.append("树形视图")
        self.var_candidate_summary.set(" | ".join(summary_parts))
        if visible_real > 0:
            first_child = self.control_tree.get_children()
            if first_child:
                self.control_tree.selection_set(first_child[0])
                self._on_control_select()
            self.var_status.set(f"树形视图：共 {visible_real} 个控件，可折叠展开。")
        else:
            self.var_status.set("当前筛选条件下没有可导入控件。")

    def _on_control_select(self, _event=None):
        view_mode = self.var_view_mode.get().strip()
        selection = self.control_tree.selection()
        if not selection:
            return
        self.preview_text.delete("1.0", tk.END)
        if view_mode == "tree":
            selected_items = []
            for iid in selection:
                ctrl = self._tree_node_map.get(iid)
                if ctrl is not None:
                    selected_items.append(ctrl)
            if not selected_items:
                self.preview_text.insert("1.0", "← 这是一个容器节点，请展开后选择其下的具体控件。")
                return
            if len(selected_items) == 1:
                # 使用人性化的格式显示单个控件详情
                self.preview_text.insert("1.0", self._format_control_detail_for_display(selected_items[0]))
                return
            preview = {
                "selectedCount": len(selected_items),
                "controls": [
                    {
                        "name": self._control_display_fields(item)[0],
                        "qualityTier": self._control_display_fields(item)[1],
                        "targetMethod": item.get("recommendedTargetMethod", "") or item.get("targetMethod", ""),
                        "targetValue": item.get("recommendedTargetValue", "") or item.get("targetValue", ""),
                        "windowTitle": self._control_display_fields(item)[4],
                    }
                    for item in selected_items
                ],
            }
            self.preview_text.insert("1.0", json.dumps(preview, ensure_ascii=False, indent=2))
        else:
            controls = self._get_filtered_controls()
            selected_indexes = []
            for item in selection:
                try:
                    index = int(item)
                except Exception:
                    continue
                if 0 <= index < len(controls):
                    selected_indexes.append(index)
            if not selected_indexes:
                return
            if len(selected_indexes) == 1:
                # 使用人性化的格式显示单个控件详情
                self.preview_text.insert("1.0", self._format_control_detail_for_display(controls[selected_indexes[0]]))
                return
            selected_controls = [controls[index] for index in selected_indexes]
            preview = {
                "selectedCount": len(selected_controls),
                "controls": [
                    {
                        "name": self._control_display_fields(control)[0],
                        "qualityTier": self._control_display_fields(control)[1],
                        "targetMethod": control.get("recommendedTargetMethod", "") or control.get("targetMethod", ""),
                        "targetValue": control.get("recommendedTargetValue", "") or control.get("targetValue", ""),
                        "windowTitle": self._control_display_fields(control)[4],
                    }
                    for control in selected_controls
                ],
            }
            self.preview_text.insert("1.0", json.dumps(preview, ensure_ascii=False, indent=2))

    def _format_control_detail_for_display(self, control):
        """将控件信息格式化为类似 Inspect 的人类可读格式，包含父级结构"""
        lines = []
        lines.append("=" * 60)
        lines.append("【基本信息】")
        
        # 基本识别信息
        inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
        name = str(control.get("displayName", "") or control.get("name", "") or inspect_data.get("name", "")).strip()
        ctrl_type = normalize_control_type_name(
            control.get("controlType", "") or inspect_data.get("controlType", ""),
            control.get("localizedControlType", "") or inspect_data.get("localizedControlType", ""),
        )
        class_name = str(control.get("className", "") or inspect_data.get("className", "")).strip()
        auto_id = str(control.get("automationId", "") or inspect_data.get("automationId", "")).strip()
        
        lines.append(f"Name:     {name or '(无名称)'}")
        lines.append(f"Type:     {ctrl_type}")
        lines.append(f"ClassName: {class_name}")
        lines.append(f"AutomationId: {auto_id or '(无)'}")
        
        # 定位信息
        lines.append("")
        lines.append("【推荐定位】")
        target_method = str(
            control.get("recommendedTargetMethod", "")
            or control.get("targetMethod", "")
            or inspect_data.get("recommendedTargetMethod", "")
        ).strip()
        target_value = str(
            control.get("recommendedTargetValue", "")
            or control.get("targetValue", "")
            or inspect_data.get("recommendedTargetValue", "")
        ).strip()
        lines.append(f"Method: {target_method}")
        lines.append(f"Value:  {target_value}")
        
        # 质量分级
        quality_tier = str(control.get("_qualityTier", "") or control.get("qualityTier", "")).strip()
        quality_reason = str(control.get("_qualityReason", "") or control.get("qualityReason", "")).strip()
        if quality_tier:
            lines.append("")
            lines.append("【质量评估】")
            lines.append(f"分级: {quality_tier}")
            if quality_reason:
                lines.append(f"说明: {quality_reason}")
        
        # 窗口信息
        window_title = str(control.get("windowTitle", "")).strip()
        if window_title:
            lines.append("")
            lines.append("【所属窗口】")
            lines.append(f"Title: {window_title}")
        
        # 位置信息
        rect = control.get("boundingRectangle", "") or control.get("boundingBox", {})
        if isinstance(rect, dict):
            l = rect.get("left", "")
            t = rect.get("top", "")
            r = rect.get("right", "")
            b = rect.get("bottom", "")
            if all(str(v) for v in [l, t, r, b]):
                lines.append("")
                lines.append("【位置信息】")
                lines.append(f"BoundingRectangle: ({l}, {t}) - ({r}, {b})")
        elif rect:
            lines.append("")
            lines.append("【位置信息】")
            lines.append(f"BoundingRectangle: {rect}")
        
        # 父级/祖级结构（关键信息）
        ancestors = inspect_data.get("ancestors", []) if isinstance(inspect_data, dict) else []
        if ancestors:
            lines.append("")
            lines.append("【父级/祖级结构】")
            lines.append("(从目标控件往上的层级关系，类似 Inspect 的树形视图)")
            for i, ancestor in enumerate(ancestors):
                indent = "  " * (len(ancestors) - i - 1)
                lines.append(f"{indent}└─ {ancestor}")
        
        # UI 路径
        ui_path = str(control.get("uiPath", "")).strip()
        if ui_path:
            lines.append("")
            lines.append("【UI 路径】")
            lines.append(ui_path)
        
        # 其他属性
        lines.append("")
        lines.append("【其他属性】")
        is_enabled = control.get("isEnabled", "")
        is_visible = control.get("isVisible", "")
        help_text = str(inspect_data.get("helpText", "") if isinstance(inspect_data, dict) else "").strip()
        
        if is_enabled != "":
            lines.append(f"isEnabled: {is_enabled}")
        if is_visible != "":
            lines.append(f"isVisible: {is_visible}")
        if help_text:
            lines.append(f"helpText: {help_text}")
        
        # Runtime ID
        runtime_id = str(inspect_data.get("runtimeId", "") if isinstance(inspect_data, dict) else "").strip()
        if runtime_id:
            lines.append(f"RuntimeId: {runtime_id}")
        
        lines.append("=" * 60)
        return "\n".join(lines)

    def _open_control_map_dir(self):
        os.makedirs(CONTROL_MAP_DIR, exist_ok=True)
        os.startfile(CONTROL_MAP_DIR)

    def _open_control_map_builder(self):
        if not os.path.exists(CONTROL_MAP_BUILDER_SCRIPT):
            messagebox.showerror("打开失败", f"未找到控件库采集器：\n{CONTROL_MAP_BUILDER_SCRIPT}", parent=self.window)
            return
        try:
            _launch_python_script(CONTROL_MAP_BUILDER_SCRIPT)
            self.var_status.set("已打开控件库采集器，采集保存后可回到这里刷新控件库。")
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动控件库采集器失败：\n{exc}", parent=self.window)

    def _convert_flat_to_control(self, flat_item, index):
        """将 flat_control 转换为可用于导入的控件格式"""
        inspect_data = flat_item.get("inspectData", {}) if isinstance(flat_item, dict) else {}
        origin = "control_library"
        if isinstance(flat_item, dict) and (flat_item.get("_sourceFile") or flat_item.get("sources")):
            origin = "standard_catalog"
        return {
            "id": f"control_map_{index + 1}",
            "name": flat_item.get("savedControlName", "") or flat_item.get("suggestedControlName", "") or flat_item.get("displayName", "") or inspect_data.get("name", "") or f"控件 {index + 1}",
            "role": f"来自控件库扫描：{flat_item.get('windowTitle', '')}",
            "windowTitle": flat_item.get("windowTitle", "") or self.default_window_title,
            # catalog 控件用 targetMethod/targetValue 键名，需回退兼容
            "targetMethod": flat_item.get("recommendedTargetMethod", "") or flat_item.get("targetMethod", ""),
            "targetValue": flat_item.get("recommendedTargetValue", "") or flat_item.get("targetValue", ""),
            "uiPath": flat_item.get("uiPath", ""),
            "labelText": flat_item.get("labelText", ""),
            "relatedLabelName": flat_item.get("relatedLabelName", ""),
            # 携带 helpText/functionText：运行时 label_text 消歧的 helpText 加分
            # 依赖快照内该字段（图标按钮 UIA Name 是 SVG path，helpText 是软件真实
            # 操作语义，如"添加新配置"），不携带则已导入步骤享受不到 helpText 消歧。
            "helpText": flat_item.get("helpText", "") or inspect_data.get("helpText", ""),
            "functionText": flat_item.get("functionText", "") or inspect_data.get("functionText", ""),
            "tabNavigation": flat_item.get("tabNavigation", {}),
            "optionValues": flat_item.get("optionValues", []),
            "notes": f"由控件库扫描导入，定位评分={flat_item.get('locatorScore', 0)}",
            "rawInspectText": flat_item.get("rawInspectText", ""),
            "auxChecks": flat_item.get("auxChecks", []),
            "inspectData": inspect_data,
            "sourceInfo": build_source_info(
                origin=origin,
                library_control_id=str(flat_item.get("id", "") or ""),
                library_file_name=str(flat_item.get("_sourceFile", "") or flat_item.get("windowTitle", "") or ""),
                imported_by="控件库导入",
            ),
            "_qualityTier": flat_item.get("qualityTier", ""),
            "_qualityReason": flat_item.get("qualityReason", ""),
        }

    def import_selected(self):
        view_mode = self.var_view_mode.get().strip()
        selection = self.control_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择一个控件。", parent=self.window)
            return
        if view_mode == "tree":
            selected_controls = []
            for iid in selection:
                ctrl = self._tree_node_map.get(iid)
                if ctrl is not None:
                    idx = self._tree_node_index.get(iid, 0)
                    selected_controls.append(self._convert_flat_to_control(ctrl, idx))
            if not selected_controls:
                messagebox.showinfo("提示", "当前选择的是容器节点，请选择具体控件后再导入。", parent=self.window)
                return
        else:
            controls = self._get_filtered_controls()
            selected_controls = []
            for item in selection:
                try:
                    index = int(item)
                except Exception:
                    continue
                if 0 <= index < len(controls):
                    selected_controls.append(controls[index])
            if not selected_controls:
                messagebox.showinfo("提示", "当前选择无有效控件。", parent=self.window)
                return
        self.result = selected_controls
        self.window.destroy()

    def import_recommended(self):
        view_mode = self.var_view_mode.get().strip()
        if view_mode == "tree":
            flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
            recommended = [self._convert_flat_to_control(item, idx) for idx, item in enumerate(flat_controls) if str(item.get("qualityTier", "")).strip() == "推荐保留"]
        else:
            controls = self._get_filtered_controls()
            recommended = [control for control in controls if str(control.get("_qualityTier", "")).strip() == "推荐保留"]
        if not recommended:
            messagebox.showinfo("提示", "当前文件中没有推荐保留的控件。", parent=self.window)
            return
        self.result = recommended
        self.window.destroy()

    def import_all(self):
        view_mode = self.var_view_mode.get().strip()
        if view_mode == "tree":
            flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
            if not flat_controls:
                messagebox.showinfo("提示", "当前没有可导入控件。", parent=self.window)
                return
            self.result = [self._convert_flat_to_control(item, idx) for idx, item in enumerate(flat_controls)]
        else:
            controls = self._get_filtered_controls()
            if not controls:
                messagebox.showinfo("提示", "当前没有可导入控件。", parent=self.window)
                return
            self.result = list(controls)
        self.window.destroy()

    def _get_control_by_index(self, index):
        """根据索引获取控件，支持两种视图"""
        view_mode = self.var_view_mode.get().strip()
        if view_mode == "tree":
            # 树形视图的 iid 是 uiPath 字符串
            flat_controls = self.current_payload.get("flatControls", []) if isinstance(self.current_payload, dict) else []
            # 先尝试用 uiPath 查找
            if index in self._tree_node_map:
                return self._tree_node_map[index]
            # 如果 index 是数字索引
            try:
                idx = int(index)
                if 0 <= idx < len(flat_controls):
                    return flat_controls[idx]
            except (ValueError, TypeError):
                pass
            return None
        else:
            controls = self._get_filtered_controls()
            if 0 <= index < len(controls):
                return controls[index]
            return None

    def _get_control_for_locator_test(self, index):
        """获取控件用于定位校验/编辑，与列表、预览面板看到的控件保持一致。

        注意：列表视图的 iid 是“排序后列表中的位置”，不能直接当作
        原始 flatControls/controlDefinitions 的下标（排序后会错位），
        必须回到同一份排序列表中取。
        """
        # 树形视图：iid 为 uiPath 字符串，直接命中真实控件
        if index in self._tree_node_map:
            control = self._tree_node_map[index]
            if control:
                return control

        # 列表视图：iid 为排序后列表的位置，从同一份排序列表取控件
        try:
            idx = int(index)
        except (ValueError, TypeError):
            idx = None
        if idx is not None:
            controls = self._get_filtered_controls()
            if 0 <= idx < len(controls):
                return controls[idx]

        return None

    @staticmethod
    def _resolve_control_source_indexes(selected_iids, control_lookup):
        """把 Treeview 选中项的 iid 解析为控件在原始数组中的真实下标。

        三种视图的 iid 语义不同，必须经 `control_lookup` 统一解析成控件字典：
          - 列表视图：iid 是「排序 + 筛选后的 0-based 显示位置」；
          - 树形视图：iid 是 uiPath 字符串；
          - 分组视图：iid 是 `hierarchy:N`。
        而 `_get_filtered_controls()` 三个分支都会排序、默认排序就是「质量优先」，
        所以列表视图的显示位置与原始下标通常**不相同**。真实下标由
        `_build_controls_from_payload` 写在每个控件的 `_sourceIndex` 上。

        删除/编辑等回写操作必须用真实下标，且 `flatControls` 与 `controlDefinitions`
        是按同一原始下标平行的数组（见 `_build_controls_from_payload` 的配对构造与
        `edit_selected_control` 的同一 source_index 回写），因此**两个数组必须共用
        同一组下标**，否则删除后两数组错位、后续控件元数据全部错配并写回文件。

        :param selected_iids: `control_tree.selection()` 的原始 iid 列表
        :param control_lookup: iid -> 控件字典 的解析函数（生产传
                               `_get_control_for_locator_test`，与编辑路径同一套解析）
        :return: 去重后的真实下标列表；无法解析的项被跳过（宁可不删，不可删错）
        """
        resolved = []
        for iid in selected_iids or []:
            try:
                control = control_lookup(iid)
            except Exception:
                control = None
            if not isinstance(control, dict):
                continue
            source_index = control.get("_sourceIndex")
            if not isinstance(source_index, int) or source_index < 0:
                # 缺少 _sourceIndex 标注时无法定位，跳过而不是猜一个下标
                continue
            if source_index not in resolved:
                resolved.append(source_index)
        return resolved

    def edit_selected_control(self):
        """编辑选中的控件"""
        if self.var_file_scope.get().strip() in ("catalog", "master"):
            messagebox.showinfo(
                "提示",
                "标准目录/总控件信息是合并派生物，编辑请在“单文件”模式下修改源采集文件。",
                parent=self.window,
            )
            return
        selection = self.control_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择要编辑的控件。", parent=self.window)
            return
        if len(selection) > 1:
            messagebox.showinfo("提示", "请只选择一个控件进行编辑。", parent=self.window)
            return
        index = selection[0]
        control = self._get_control_for_locator_test(index)
        if not control:
            messagebox.showinfo("提示", "无法获取控件信息。", parent=self.window)
            return
        dialog = ControlEditDialog(self.window, control)
        self.window.wait_window(dialog.window)
        if dialog.result:
            payload = self.current_payload
            if not isinstance(payload, dict):
                messagebox.showerror("错误", "无法获取控件库文件内容。", parent=self.window)
                return

            # 解析控件在原始数组中的真实下标（不能用显示位置 iid，排序后会错位）
            source_index = None
            if isinstance(control, dict) and isinstance(control.get("_sourceIndex"), int):
                source_index = control.get("_sourceIndex")
            elif index in self._tree_node_index:
                source_index = self._tree_node_index.get(index)
            else:
                try:
                    source_index = int(index)
                except (ValueError, TypeError):
                    source_index = None

            if source_index is None:
                messagebox.showerror("错误", "无法定位该控件在文件中的位置，未保存。", parent=self.window)
                return

            # 回写到真实下标对应的 flatControls / controlDefinitions
            flat_controls = payload.get("flatControls", [])
            if 0 <= source_index < len(flat_controls):
                flat_controls[source_index] = dialog.result
                payload["flatControls"] = flat_controls

            control_defs = payload.get("controlDefinitions", [])
            if 0 <= source_index < len(control_defs):
                control_defs[source_index] = dialog.result
                payload["controlDefinitions"] = control_defs
            # payload 是原地修改（对象身份不变），必须手动失效控件列表缓存
            self._controls_cache_key = None

            file_selection = self.file_listbox.curselection()
            if file_selection:
                file_index = file_selection[0]
                if 0 <= file_index < len(self.control_map_files):
                    file_path = self.control_map_files[file_index]["path"]
                    save_json_file(file_path, payload)
                    self._refresh_file_list()
                    self.var_status.set(f"已保存控件编辑：{dialog.result.get('displayName', '')}")

    def _show_locator_highlight(self, rect, duration_ms=3000):
        """用置顶覆盖层高亮实际命中的屏幕区域。"""
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
            overlay = tk.Toplevel(self.window)
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

    def test_selected_locator(self):
        """使用流程执行器同一套定位规则检验并指向实际命中控件（后台线程，不阻塞 UI）。

        只在窗口内状态栏显示结果，绝不弹窗夺取焦点。
        """
        selection = self.control_tree.selection()
        if len(selection) != 1:
            self.var_locator_result.set("⚠ 请只选择一个控件进行检验")
            return

        control = self._get_control_for_locator_test(selection[0])
        if not isinstance(control, dict):
            self.var_locator_result.set("⚠ 无法读取选中控件的信息")
            return

        control_definition = dict(control)
        # 与控件库采集器的检验一致：条目缺 windowTitle 时回退到文件级 targetWindow.title，
        # 避免老控件库文件因缺窗口标题而找不到目标窗口。
        if not str(control_definition.get("windowTitle", "")).strip():
            target_window = self.current_payload.get("targetWindow", {}) if isinstance(self.current_payload, dict) else {}
            if isinstance(target_window, dict) and str(target_window.get("title", "")).strip():
                control_definition["windowTitle"] = str(target_window.get("title", "")).strip()

        try:
            import wt_flow_locator as flow_locator
        except ImportError as exc:
            self.var_locator_result.set(f"⚠ 缺少定位依赖：{exc}")
            return

        try:
            # normalize 内部会回退 recommendedTargetMethod / recommendedTargetValue，
            # 因此“是否有可用于定位的属性”必须在 normalize 之后判断，
            # 否则只有推荐定位（无顶层 targetMethod、无 inspectData）的控件会被误拒。
            control_definition = flow_locator.normalize_control_definition(control_definition)
        except Exception as exc:
            self.var_locator_result.set(f"⚠ 定位参数解析失败：{exc}")
            return
        if not control_definition.get("targetMethod"):
            self.var_locator_result.set("⚠ 该控件没有可用于定位的属性")
            return

        self.var_locator_result.set("⏳ 正在检验定位（独立进程执行中）...")

        # 复用采集器同款 control_locator_probe.py 子进程隔离：
        # UIA 遍历在隔离子进程内执行，遇到失效 COM 指针触发原生堆损坏时
        # 只终止探针子进程，编辑器主窗口不受影响（之前在进程内线程跑会
        # 0xc0000374 崩溃、直接关掉整个编辑器）。
        self._set_probe_running(True)
        self._launch_locator_probe(control_definition)

    _PROBE_WAIT_SECONDS = 90.0

    def _set_probe_running(self, running):
        """检验期间把按钮切换为"⏹ 停止"，点击可中止探针并显示已等待秒数。

        同一时刻只允许一个探针在跑：按钮切换挡住常规重复启动，_probe_seq 序号
        兜底保证旧一轮的超时定时器/回调不会干扰新一轮（此前实例属性被第二次
        启动直接覆盖——第一轮的定时器会误杀第二轮探针、第一轮回调会取消第二轮
        的定时器，连续两次点击必然互相干扰）。
        """
        btn = getattr(self, "_btn_test_locator", None)
        if btn is not None:
            try:
                btn.configure(
                    text="⏹ 停止" if running else "检验定位",
                    state="normal",
                )
            except Exception:
                pass
        if getattr(self, "_probe_elapsed_timer", None) is not None:
            try:
                self.window.after_cancel(self._probe_elapsed_timer)
            except Exception:
                pass
            self._probe_elapsed_timer = None
        if running:
            self._probe_started_at = time.time()
            self._tick_probe_elapsed()

    def _toggle_probe_button(self):
        """按钮回调：探针空闲时启动检验，运行中则停止当前探针。"""
        proc = getattr(self, "_probe_proc", None)
        if proc is not None and proc.poll() is None:
            self._stop_probe()
        else:
            self.test_selected_locator()

    def _stop_probe(self):
        """手动停止当前探针：杀子进程、取消超时定时器，watcher 回调按过期处理。"""
        elapsed = int(time.time() - getattr(self, "_probe_started_at", time.time()))
        seq = getattr(self, "_probe_seq", 0) + 1
        self._probe_seq = seq  # watcher/超时回调携带旧序号 → 走过期分支只清理临时文件
        timer = getattr(self, "_probe_timer", None)
        if timer is not None:
            try:
                self.window.after_cancel(timer)
            except Exception:
                pass
            self._probe_timer = None
        proc = getattr(self, "_probe_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
        self._probe_proc = None
        self.var_locator_result.set("⚠ 检验定位：已手动停止（{}s）".format(elapsed))
        self._set_probe_running(False)

    def _tick_probe_elapsed(self):
        base = getattr(self, "_probe_started_at", None)
        if base is None:
            return
        self.var_locator_result.set(
            "⏳ 正在检验定位（独立进程执行中，已等待 {:.0f}s，可点停止中止）...".format(time.time() - base)
        )
        self._probe_elapsed_timer = self.window.after(1000, self._tick_probe_elapsed)

    def _launch_locator_probe(self, control):
        """在子进程中执行定位搜索（control_locator_probe.py）。

        探针把 UIA 遍历放到独立 subprocess：原生崩溃（STATUS_HEAP_CORRUPTION /
        0xc0000374，pywinauto 的 try/except 拦不住）只终止探针子进程，
        编辑器主窗口不受影响；主进程通过结果文件判断成功/失败/崩溃。
        新启动会先取消上一轮超时定时器、终止仍在运行的旧探针；watcher 回调
        携带 _probe_seq，过期回调只清理临时文件、不更新状态与按钮。
        """
        import subprocess as _subprocess
        base_dir = os.path.dirname(os.path.abspath(__file__))
        probe_script = os.path.join(base_dir, "control_locator_probe.py")
        if not os.path.isfile(probe_script):
            self._set_probe_running(False)
            self.var_locator_result.set("⚠ 检验定位：探针脚本缺失：" + probe_script)
            return

        self._probe_seq = getattr(self, "_probe_seq", 0) + 1
        prev_timer = getattr(self, "_probe_timer", None)
        if prev_timer is not None:
            try:
                self.window.after_cancel(prev_timer)
            except Exception:
                pass
            self._probe_timer = None
        prev_proc = getattr(self, "_probe_proc", None)
        if prev_proc is not None and prev_proc.poll() is None:
            try:
                prev_proc.kill()
            except Exception:
                pass

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
            self._set_probe_running(False)
            self.var_locator_result.set("⚠ 检验定位：写入控件定义失败：" + str(exc))
            return

        creationflags = getattr(_subprocess, "CREATE_NO_WINDOW", 0)
        try:
            proc = _subprocess.Popen(
                [sys.executable, probe_script, control_path, output_path],
                cwd=base_dir,
                stdout=_subprocess.PIPE,
                stderr=_subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                bufsize=1,
                creationflags=creationflags,
            )
        except Exception as exc:
            self._set_probe_running(False)
            self.var_locator_result.set("⚠ 检验定位：启动探针失败：" + str(exc))
            return

        self._probe_proc = proc
        self._probe_output_path = output_path
        self._probe_start = time.time()
        self._probe_timed_out = False

        watcher = threading.Thread(target=self._wait_probe_exit, args=(proc, output_path, self._probe_seq), daemon=True)
        watcher.start()
        # 超时兜底：探针挂死（如 UIA 死锁）时强杀，避免进程残留
        self._probe_timer = self.window.after(int(self._PROBE_WAIT_SECONDS * 1000), self._on_probe_timeout, self._probe_seq)

    def _wait_probe_exit(self, proc, output_path, seq):
        start = getattr(self, "_probe_start", time.time())
        try:
            returncode = proc.wait()
        except Exception as exc:
            self.window.after(0, lambda: self._handle_probe_result(output_path, -1, start, seq, str(exc)))
            return
        self.window.after(0, lambda: self._handle_probe_result(output_path, returncode, start, seq))

    def _on_probe_timeout(self, seq):
        if seq != getattr(self, "_probe_seq", None):
            return  # 过期定时器：新一轮检验已启动，不得误杀当前探针
        self._probe_timed_out = True
        proc = getattr(self, "_probe_proc", None)
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:
                pass
            self.var_locator_result.set(
                "⚠ 检验定位：探针超时（{}s），已终止。".format(int(self._PROBE_WAIT_SECONDS))
            )
        self._probe_proc = None
        self._set_probe_running(False)

    def _handle_probe_result(self, output_path, returncode, start, seq, extra_error=""):
        if seq != getattr(self, "_probe_seq", None):
            # 过期回调：新一轮检验已启动，只清理本轮临时文件，不触碰状态与按钮
            self._cleanup_probe_files(output_path)
            return
        if getattr(self, "_probe_timer", None) is not None:
            try:
                self.window.after_cancel(self._probe_timer)
            except Exception:
                pass
            self._probe_timer = None
        self._probe_proc = None
        self._set_probe_running(False)
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
                self.var_locator_result.set("⚠ 检验定位失败：" + str(extra_error))
            elif returncode == 0:
                self.var_locator_result.set("⚠ 检验定位：结果文件不可读（{:.1f}s）".format(elapsed))
            else:
                self.var_locator_result.set(
                    "⚠ 检验定位：定位进程异常退出（code {}, {:.1f}s）——可能因 UIA 原生崩溃，"
                    "已隔离在子进程，编辑器未受影响。".format(returncode, elapsed)
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
            self.var_locator_result.set(
                "⚠ 检验定位：{err}（{method}={value}，耗时 {elapsed:.1f}s）".format(
                    err=err, method=method, value=value, elapsed=elapsed
                )
            )
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
        """在主线程展示命中的高亮框与状态（视觉行为与原先一致，且避开 draw_outline）。"""
        try:
            import pyautogui
        except Exception:
            pyautogui = None
        center = result.get("center")
        rect = result.get("rect")
        if rect:
            # 探针输出是 dict，_show_locator_highlight 需要带 .left/.top/.right/.bottom 的对象
            rect_obj = type("Rect", (), {
                "left": rect.get("left"),
                "top": rect.get("top"),
                "right": rect.get("right"),
                "bottom": rect.get("bottom"),
            })()
            self._show_locator_highlight(rect_obj)
        if center and pyautogui is not None:
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
        self.var_locator_result.set(" ".join(part for part in parts if part))

    def delete_selected_controls(self):
        """从控件库文件中删除选中的控件"""
        if self.var_file_scope.get().strip() in ("catalog", "master"):
            messagebox.showinfo(
                "提示",
                "标准目录/总控件信息是合并派生物，删除请在“单文件”模式下修改源采集文件。",
                parent=self.window,
            )
            return
        selection = self.control_tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选择要删除的控件。", parent=self.window)
            return
        payload = self.current_payload
        if not isinstance(payload, dict):
            messagebox.showerror("错误", "无法获取控件库文件内容。", parent=self.window)
            return
        
        # 统一解析真实下标，后续四处（确认框控件名 / deleted_keys / flatControls 删除 /
        # controlDefinitions 删除）全部共用这一组下标。
        # 只改其中一处会让两个平行数组错位 —— 详见 _resolve_control_source_indexes 的说明。
        # 解析经 _get_control_for_locator_test，与编辑路径同一套逻辑，因此列表 / 树形 /
        # 分组三种视图的 iid 都能正确解析（旧实现用 int(iid) 会把后两种视图的选中项全丢掉）。
        flat_controls = payload.get("flatControls", [])
        control_defs = payload.get("controlDefinitions", [])
        resolved_indexes = self._resolve_control_source_indexes(
            selection, self._get_control_for_locator_test)
        if not resolved_indexes:
            messagebox.showerror(
                "错误", "无法定位所选控件在文件中的位置，未删除。", parent=self.window)
            return

        # 获取控件名称用于确认提示（用真实下标，保证提示与实际被删的控件一致）
        control_names = []
        for _src in resolved_indexes:
            name = ""
            if 0 <= _src < len(flat_controls) and isinstance(flat_controls[_src], dict):
                name = (flat_controls[_src].get("displayName", "")
                        or flat_controls[_src].get("name", ""))
            if not name and 0 <= _src < len(control_defs) and isinstance(control_defs[_src], dict):
                name = control_defs[_src].get("name", "")
            if name:
                control_names.append(name)
        
        names_str = ", ".join(control_names[:3])
        if len(control_names) > 3:
            names_str += f" 等{len(control_names)}个"
        
        if not control_names:
            names_str = f"{len(resolved_indexes)} 个控件"
        
        if not messagebox.askyesno(
            "确认删除",
            f"确定从控件库中删除以下控件？\n{names_str}\n\n"
            "注意：删除会立即写入当前控件库文件，并把流程定义中引用这些控件的步骤\n"
            "标记为「来源失效」（会改写 flow_packages/flow_definition_*.json）。",
            parent=self.window,
        ):
            return

        # 收集被删控件的标识（id / automationId / name），删除后用于流程来源联动标记
        deleted_keys = set()
        for _src in resolved_indexes:
            for _container in (flat_controls, control_defs):
                if 0 <= _src < len(_container) and isinstance(_container[_src], dict):
                    for _k in ("id", "automationId", "displayName", "name"):
                        _v = str(_container[_src].get(_k, "")).strip()
                        if _v:
                            deleted_keys.add(_v)

        # 从 flatControls / controlDefinitions 删除。
        # 两个数组按同一原始下标平行，必须共用同一组下标、且都从后往前删，
        # 否则删除后数组错位，后续控件元数据会全部错配并写回文件。
        try:
            if flat_controls:
                for i in sorted(resolved_indexes, reverse=True):
                    if 0 <= i < len(flat_controls):
                        del flat_controls[i]
                payload["flatControls"] = flat_controls
        except (ValueError, TypeError):
            pass
        
        try:
            if control_defs:
                for i in sorted(resolved_indexes, reverse=True):
                    if 0 <= i < len(control_defs):
                        del control_defs[i]
                payload["controlDefinitions"] = control_defs
        except (ValueError, TypeError):
            pass
        
        # payload 是原地修改（对象身份不变），必须手动失效控件列表缓存
        self._controls_cache_key = None
        file_path = None
        file_selection = self.file_listbox.curselection()
        if file_selection:
            file_index = file_selection[0]
            if 0 <= file_index < len(self.control_map_files):
                file_path = self.control_map_files[file_index]["path"]
        if not file_path:
            messagebox.showerror("错误", "无法确定控件库文件路径。", parent=self.window)
            return
        save_json_file(file_path, payload)
        self._refresh_file_list()
        # 源库删除联动：扫描流程定义，标记引用被删控件的控件 sourceDeleted=True
        marked_flows = self._mark_flow_controls_source_deleted(deleted_keys)
        if marked_flows:
            self.var_status.set(f"已删除 {len(resolved_indexes)} 个控件，并在 {marked_flows} 个流程中标记来源失效。")
        else:
            self.var_status.set(f"已删除 {len(resolved_indexes)} 个控件。")

    def on_cancel(self):
        self.result = None
        self.window.destroy()

    def _mark_flow_controls_source_deleted(self, deleted_keys):
        """源库删除控件后，扫描 flow_packages 下所有流程定义，
        对步骤中引用了被删控件来源的控件打上 sourceDeleted 标记。

        匹配依据（任一并集命中即可）：
          - sourceInfo.libraryControlId == 被删控件 id
          - 控件 id == 被删控件 id / automationId / name
          - inspectData.automationId == 被删控件 automationId
        返回标记了控件数目的流程文件数量。
        """
        if not deleted_keys:
            return 0
        flow_dir = os.path.join(BASE_DIR, "flow_packages")
        if not os.path.isdir(flow_dir):
            return 0
        affected_flows = 0
        for fname in sorted(os.listdir(flow_dir)):
            if not (fname.startswith("flow_definition_") and fname.endswith(".json")):
                continue
            fpath = os.path.join(flow_dir, fname)
            try:
                payload = load_json_file(fpath)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            changed = False
            for step in payload.get("steps", []) or []:
                if not isinstance(step, dict):
                    continue
                for ctrl in step.get("controls", []) or []:
                    if not isinstance(ctrl, dict):
                        continue
                    src = ctrl.get("sourceInfo")
                    src_id = str((src or {}).get("libraryControlId", "")).strip()
                    cid = str(ctrl.get("id", "")).strip()
                    aid = str(((ctrl.get("inspectData") or {}).get("automationId", ""))).strip()
                    name = str(ctrl.get("name", "")).strip()
                    hit = bool({cid, aid, name, src_id} & deleted_keys)
                    if not hit:
                        continue
                    src = ctrl.get("sourceInfo")
                    if not isinstance(src, dict) or not src.get("sourceDeleted"):
                        mark_source_deleted(ctrl)
                        changed = True
            if changed:
                try:
                    save_json_file(fpath, payload)
                    affected_flows += 1
                except Exception:
                    pass
        return affected_flows

    def _cmd_expand_all(self):
        """展开控件树全部节点。"""
        for item in self._all_tree_items(self.control_tree):
            self.control_tree.item(item, open=True)

    def _cmd_collapse_all(self):
        """折叠控件树全部节点。"""
        for item in self._all_tree_items(self.control_tree):
            self.control_tree.item(item, open=False)

    @staticmethod
    def _all_tree_items(tree=None, parent=""):
        """递归获取树视图的所有条目 iid。"""
        if tree is None:
            return []
        items = []
        for child in tree.get_children(parent):
            items.append(child)
            items.extend(ControlMapImportDialog._all_tree_items(tree, child))
        return items


_NATURAL_NUM = re.compile(r"(\d+)")


def _natural_sort_key(s):
    """生成自然排序键，使带数字的 ID 按数值而非字母序排列。

    例如：["S1", "S10", "S2"] → ["S1", "S2", "S10"]
    """
    return [int(p) if p.isdigit() else p.lower() for p in _NATURAL_NUM.split(str(s))]


class FlowPackageDialog:
    def __init__(self, parent, package=None, available_steps=None, on_focus_step=None):
        self.package = package or {}
        self.available_steps = available_steps or []
        self.on_focus_step = on_focus_step
        self.result = None
        self.available_step_map = {}
        for step in self.available_steps:
            step_id = str(step.get("id", "")).strip()
            if step_id and step_id not in self.available_step_map:
                self.available_step_map[step_id] = step
        self.available_step_ids = sorted(self.available_step_map.keys(), key=_natural_sort_key)
        self.selected_step_ids = []
        seen_selected_ids = set()
        for item in self.package.get("stepIds", []):
            step_id = str(item).strip()
            if step_id and step_id in self.available_step_map and step_id not in seen_selected_ids:
                self.selected_step_ids.append(step_id)
                seen_selected_ids.add(step_id)
        self.selected_step_ids.sort(key=_natural_sort_key)

        self.available_displayed_ids = []
        self.selected_displayed_ids = []

        self.window = _make_dialog_window(parent, "流程包编辑", 1000, 700, min_width=880, min_height=580)
        self.window.configure(bg=EDITOR_THEME.get("bg", "#f8fafc"))

        self.var_id = tk.StringVar(value=str(self.package.get("id", "")).strip())
        self.var_name = tk.StringVar(value=str(self.package.get("name", "")).strip())
        self.var_avail_filter = tk.StringVar()
        self.var_sel_filter = tk.StringVar()
        self.var_avail_count = tk.StringVar(value="")
        self.var_sel_count = tk.StringVar(value="")

        container = tk.Frame(self.window, bg=EDITOR_THEME.get("bg", "#f8fafc"), padx=14, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        # ── 流程包基本信息卡片 ──
        form = tk.LabelFrame(
            container,
            text="🏷️ 流程包基本信息",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=10,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        form.pack(fill=tk.X, pady=(0, 10))
        form.columnconfigure(1, weight=1)
        form.columnconfigure(3, weight=1)

        tk.Label(
            form, text="流程包 ID *", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=0, column=0, sticky="w", pady=3)
        id_entry = tk.Entry(
            form, textvariable=self.var_id, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        id_entry.grid(row=0, column=1, sticky="ew", padx=(8, 16), pady=3, ipady=3)

        tk.Label(
            form, text="流程包名称 *", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).grid(row=0, column=2, sticky="w", pady=3)
        name_entry = tk.Entry(
            form, textvariable=self.var_name, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        name_entry.grid(row=0, column=3, sticky="ew", padx=(8, 0), pady=3, ipady=3)

        tk.Label(
            form, text="说明描述", font=("Microsoft YaHei UI", 9),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("muted", "#64748b"),
        ).grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self.description_text = tk.Text(
            form, height=3, wrap=tk.WORD, font=("Microsoft YaHei UI", 9),
            relief=tk.FLAT, bd=0, highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        self.description_text.grid(row=1, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=(6, 0))
        self.description_text.insert("1.0", str(self.package.get("description", "")).strip())

        # ── 步骤穿梭框卡片 ──
        step_frame = tk.LabelFrame(
            container,
            text="🔄 包含步骤（双栏穿梭编排）",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=12,
            pady=10,
            bg=EDITOR_THEME.get("card", "#ffffff"),
            fg=EDITOR_THEME.get("text", "#1f2d3d"),
            highlightthickness=1,
            highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        step_frame.pack(fill=tk.BOTH, expand=True)

        list_frame = tk.Frame(step_frame, bg=EDITOR_THEME.get("card", "#ffffff"))
        list_frame.pack(fill=tk.BOTH, expand=True)
        list_frame.columnconfigure(0, weight=1)
        list_frame.columnconfigure(1, weight=0)
        list_frame.columnconfigure(2, weight=1)
        list_frame.rowconfigure(1, weight=1)

        # ── 左栏：可选步骤池 ──
        left_header = tk.Frame(list_frame, bg=EDITOR_THEME.get("card", "#ffffff"))
        left_header.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        tk.Label(
            left_header, text="可选步骤池", font=("Microsoft YaHei UI", 9, "bold"),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).pack(side=tk.LEFT)

        tk.Label(
            left_header, textvariable=self.var_avail_count, font=("Microsoft YaHei UI", 8),
            bg=EDITOR_THEME.get("bg", "#f8fafc"), fg=EDITOR_THEME.get("muted", "#64748b"),
            padx=6, pady=1, highlightthickness=1, highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        ).pack(side=tk.LEFT, padx=(8, 0))

        wt_theme.create_flat_button(
            left_header, "清空选择", lambda: self.available_step_listbox.selection_clear(0, tk.END),
            tone="secondary", font=("Microsoft YaHei UI", 8), padx=6, pady=1,
        ).pack(side=tk.RIGHT)
        wt_theme.create_flat_button(
            left_header, "全选", lambda: self.available_step_listbox.selection_set(0, tk.END),
            tone="secondary", font=("Microsoft YaHei UI", 8), padx=6, pady=1,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        # 左栏搜索条 + 列表容器
        available_outer = tk.Frame(list_frame, bg=EDITOR_THEME.get("card", "#ffffff"))
        available_outer.grid(row=1, column=0, sticky="nsew")

        avail_filter_row = tk.Frame(available_outer, bg=EDITOR_THEME.get("card", "#ffffff"))
        avail_filter_row.pack(fill=tk.X, pady=(0, 4))
        avail_filter_entry = tk.Entry(
            avail_filter_row, textvariable=self.var_avail_filter, font=("Microsoft YaHei UI", 8),
            relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        avail_filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)
        wt_theme.create_flat_button(
            avail_filter_row, "✕", lambda: self.var_avail_filter.set(""),
            tone="secondary", font=("Microsoft YaHei UI", 8), padx=6, pady=1,
        ).pack(side=tk.RIGHT, padx=(4, 0))

        available_frame = tk.Frame(
            available_outer, bg=EDITOR_THEME.get("card", "#ffffff"),
            highlightthickness=1, highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        available_frame.pack(fill=tk.BOTH, expand=True)

        self.available_step_listbox = tk.Listbox(
            available_frame, selectmode=tk.EXTENDED, activestyle="none", exportselection=False,
            font=("Microsoft YaHei UI", 9), relief=tk.FLAT, bd=0, highlightthickness=0,
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
            selectbackground=EDITOR_THEME.get("primary", "#2563eb"), selectforeground="#ffffff",
        )
        self.available_step_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        available_scrollbar = ttk.Scrollbar(available_frame, orient="vertical", command=self.available_step_listbox.yview)
        available_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.available_step_listbox.configure(yscrollcommand=available_scrollbar.set)

        # ── 中间操作列（现代方向穿梭按钮） ──
        middle_button_frame = tk.Frame(list_frame, bg=EDITOR_THEME.get("card", "#ffffff"))
        middle_button_frame.grid(row=1, column=1, sticky="ns", padx=12)

        tk.Frame(middle_button_frame, bg=EDITOR_THEME.get("card", "#ffffff")).pack(fill=tk.BOTH, expand=True)

        wt_theme.create_flat_button(
            middle_button_frame, " > ", self.add_selected_steps_to_package,
            tone="primary", font=("Microsoft YaHei UI", 9, "bold"), padx=10, pady=4,
        ).pack(fill=tk.X, pady=3)
        wt_theme.create_flat_button(
            middle_button_frame, " >> ", self.add_all_steps_to_package,
            tone="secondary", font=("Microsoft YaHei UI", 9), padx=10, pady=4,
        ).pack(fill=tk.X, pady=3)
        wt_theme.create_flat_button(
            middle_button_frame, " < ", self.remove_selected_steps_from_package,
            tone="secondary", font=("Microsoft YaHei UI", 9, "bold"), padx=10, pady=4,
        ).pack(fill=tk.X, pady=3)
        wt_theme.create_flat_button(
            middle_button_frame, " << ", self.remove_all_steps_from_package,
            tone="secondary", font=("Microsoft YaHei UI", 9), padx=10, pady=4,
        ).pack(fill=tk.X, pady=3)

        tk.Frame(middle_button_frame, bg=EDITOR_THEME.get("card", "#ffffff")).pack(fill=tk.BOTH, expand=True)

        # ── 右栏：流程包执行序列 ──
        right_header = tk.Frame(list_frame, bg=EDITOR_THEME.get("card", "#ffffff"))
        right_header.grid(row=0, column=2, sticky="ew", pady=(0, 6))

        tk.Label(
            right_header, text="流程包执行序列", font=("Microsoft YaHei UI", 9, "bold"),
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
        ).pack(side=tk.LEFT)

        tk.Label(
            right_header, textvariable=self.var_sel_count, font=("Microsoft YaHei UI", 8),
            bg=EDITOR_THEME.get("bg", "#f8fafc"), fg=EDITOR_THEME.get("primary", "#2563eb"),
            padx=6, pady=1, highlightthickness=1, highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        ).pack(side=tk.LEFT, padx=(8, 0))

        wt_theme.create_flat_button(
            right_header, "清空选择", lambda: self.selected_step_listbox.selection_clear(0, tk.END),
            tone="secondary", font=("Microsoft YaHei UI", 8), padx=6, pady=1,
        ).pack(side=tk.RIGHT)
        wt_theme.create_flat_button(
            right_header, "全选", lambda: self.selected_step_listbox.selection_set(0, tk.END),
            tone="secondary", font=("Microsoft YaHei UI", 8), padx=6, pady=1,
        ).pack(side=tk.RIGHT, padx=(0, 6))

        # 右栏搜索条 + 列表容器
        selected_outer = tk.Frame(list_frame, bg=EDITOR_THEME.get("card", "#ffffff"))
        selected_outer.grid(row=1, column=2, sticky="nsew")

        sel_filter_row = tk.Frame(selected_outer, bg=EDITOR_THEME.get("card", "#ffffff"))
        sel_filter_row.pack(fill=tk.X, pady=(0, 4))
        sel_filter_entry = tk.Entry(
            sel_filter_row, textvariable=self.var_sel_filter, font=("Microsoft YaHei UI", 8),
            relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
            bg=EDITOR_THEME.get("bg", "#f8fafc"),
        )
        sel_filter_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)
        wt_theme.create_flat_button(
            sel_filter_row, "✕", lambda: self.var_sel_filter.set(""),
            tone="secondary", font=("Microsoft YaHei UI", 8), padx=6, pady=1,
        ).pack(side=tk.RIGHT, padx=(4, 0))

        selected_frame = tk.Frame(
            selected_outer, bg=EDITOR_THEME.get("card", "#ffffff"),
            highlightthickness=1, highlightbackground=EDITOR_THEME.get("border", "#e2e8f0"),
        )
        selected_frame.pack(fill=tk.BOTH, expand=True)

        self.selected_step_listbox = tk.Listbox(
            selected_frame, selectmode=tk.EXTENDED, activestyle="none", exportselection=False,
            font=("Microsoft YaHei UI", 9), relief=tk.FLAT, bd=0, highlightthickness=0,
            bg=EDITOR_THEME.get("card", "#ffffff"), fg=EDITOR_THEME.get("text", "#1f2d3d"),
            selectbackground=EDITOR_THEME.get("primary", "#2563eb"), selectforeground="#ffffff",
        )
        self.selected_step_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)
        selected_scrollbar = ttk.Scrollbar(selected_frame, orient="vertical", command=self.selected_step_listbox.yview)
        selected_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.selected_step_listbox.configure(yscrollcommand=selected_scrollbar.set)

        # 右栏底部排序微调条
        order_bar = tk.Frame(list_frame, bg=EDITOR_THEME.get("card", "#ffffff"))
        order_bar.grid(row=2, column=2, sticky="e", pady=(8, 0))

        wt_theme.create_flat_button(
            order_bar, "⬆ 上移", lambda: self.move_selected_package_steps(-1),
            tone="secondary", font=("Microsoft YaHei UI", 8), padx=8, pady=2,
        ).pack(side=tk.LEFT)
        wt_theme.create_flat_button(
            order_bar, "⬇ 下移", lambda: self.move_selected_package_steps(1),
            tone="secondary", font=("Microsoft YaHei UI", 8), padx=8, pady=2,
        ).pack(side=tk.LEFT, padx=(6, 0))
        wt_theme.create_flat_button(
            order_bar, "🔀 按ID排序", self.sort_selected_package_steps_by_id,
            tone="secondary", font=("Microsoft YaHei UI", 8), padx=8, pady=2,
        ).pack(side=tk.LEFT, padx=(6, 0))

        # ── 底部操作栏 ──
        bottom_bar = tk.Frame(container, bg=EDITOR_THEME.get("bg", "#f8fafc"))
        bottom_bar.pack(fill=tk.X, pady=(10, 0))

        wt_theme.create_flat_button(
            bottom_bar, "🎯 定位到步骤树", self.focus_selected_step_in_editor,
            tone="secondary", font=("Microsoft YaHei UI", 9), padx=12, pady=5,
        ).pack(side=tk.LEFT)

        wt_theme.create_flat_button(
            bottom_bar, "保存流程包", self.on_save,
            tone="primary", font=("Microsoft YaHei UI", 9, "bold"), padx=18, pady=5,
        ).pack(side=tk.RIGHT)

        wt_theme.create_flat_button(
            bottom_bar, "取消", self.window.destroy,
            tone="secondary", font=("Microsoft YaHei UI", 9), padx=14, pady=5,
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self.available_step_listbox.bind("<Double-1>", lambda _e: self.add_selected_steps_to_package())
        self.selected_step_listbox.bind("<Double-1>", lambda _e: self.remove_selected_steps_from_package())
        self.var_avail_filter.trace_add("write", lambda *_a: self._refresh_available_step_listbox())
        self.var_sel_filter.trace_add("write", lambda *_a: self._refresh_selected_step_listbox())

        self._refresh_available_step_listbox()
        self._refresh_selected_step_listbox()

    def _format_step_display(self, step_id):
        step = self.available_step_map.get(str(step_id).strip(), {})
        step_name = str(step.get("name", "")).strip()
        return f"{step_id} | {step_name}" if step_name else str(step_id).strip()

    def _refresh_available_step_listbox(self):
        if not hasattr(self, "available_step_listbox"):
            return
        selected_set = set(self.selected_step_ids)
        filter_kw = self.var_avail_filter.get().strip().lower()
        all_avail = sorted(
            [
                step_id
                for step_id in self.available_step_map.keys()
                if step_id not in selected_set
            ],
            key=_natural_sort_key,
        )
        self.available_step_ids = all_avail
        preserved_ids = {
            self.available_displayed_ids[index]
            for index in self.available_step_listbox.curselection()
            if 0 <= index < len(self.available_displayed_ids)
        }
        self.available_step_listbox.delete(0, tk.END)
        del self.available_displayed_ids[:]
        for step_id in all_avail:
            display_str = self._format_step_display(step_id)
            if filter_kw and filter_kw not in display_str.lower():
                continue
            self.available_displayed_ids.append(step_id)
            self.available_step_listbox.insert(tk.END, "  " + display_str)
            if step_id in preserved_ids:
                self.available_step_listbox.selection_set(tk.END)
        self.var_avail_count.set(f"可选 {len(self.available_displayed_ids)} / {len(all_avail)} 项")

    def _refresh_selected_step_listbox(self):
        if not hasattr(self, "selected_step_listbox"):
            return
        filter_kw = self.var_sel_filter.get().strip().lower()
        preserved_ids = {
            self.selected_displayed_ids[index]
            for index in self.selected_step_listbox.curselection()
            if 0 <= index < len(self.selected_displayed_ids)
        }
        self.selected_step_listbox.delete(0, tk.END)
        del self.selected_displayed_ids[:]
        for step_id in self.selected_step_ids:
            display_str = self._format_step_display(step_id)
            if filter_kw and filter_kw not in display_str.lower():
                continue
            self.selected_displayed_ids.append(step_id)
            self.selected_step_listbox.insert(tk.END, "  " + display_str)
            if step_id in preserved_ids:
                self.selected_step_listbox.selection_set(tk.END)
        self.var_sel_count.set(f"已选 {len(self.selected_displayed_ids)} / {len(self.selected_step_ids)} 项")

    def _get_active_step_id(self):
        selected_indices = self.selected_step_listbox.curselection() if hasattr(self, "selected_step_listbox") else ()
        if selected_indices:
            index = selected_indices[0]
            if 0 <= index < len(self.selected_displayed_ids):
                return str(self.selected_displayed_ids[index]).strip()
        available_indices = self.available_step_listbox.curselection() if hasattr(self, "available_step_listbox") else ()
        if available_indices:
            index = available_indices[0]
            if 0 <= index < len(self.available_displayed_ids):
                return str(self.available_displayed_ids[index]).strip()
        return ""

    def add_selected_steps_to_package(self):
        selected_indices = list(self.available_step_listbox.curselection())
        if not selected_indices:
            messagebox.showinfo("提示", "请先在左侧可选步骤里选择一个或多个步骤。", parent=self.window)
            return
        selected_set = set(self.selected_step_ids)
        added_ids = []
        for index in selected_indices:
            if not (0 <= index < len(self.available_displayed_ids)):
                continue
            step_id = self.available_displayed_ids[index]
            if step_id not in selected_set:
                self.selected_step_ids.append(step_id)
                selected_set.add(step_id)
                added_ids.append(step_id)
        self.selected_step_ids.sort(key=_natural_sort_key)
        self._refresh_available_step_listbox()
        self._refresh_selected_step_listbox()
        for index, step_id in enumerate(self.selected_displayed_ids):
            if step_id in set(added_ids):
                self.selected_step_listbox.selection_set(index)

    def add_all_steps_to_package(self):
        if not self.available_displayed_ids:
            return
        selected_set = set(self.selected_step_ids)
        for step_id in self.available_displayed_ids:
            if step_id not in selected_set:
                self.selected_step_ids.append(step_id)
                selected_set.add(step_id)
        self.selected_step_ids.sort(key=_natural_sort_key)
        self._refresh_available_step_listbox()
        self._refresh_selected_step_listbox()

    def remove_selected_steps_from_package(self):
        selected_indices = list(self.selected_step_listbox.curselection())
        if not selected_indices:
            messagebox.showinfo("提示", "请先在右侧流程包步骤里选择一个或多个步骤。", parent=self.window)
            return
        selected_id_set = {
            self.selected_displayed_ids[index]
            for index in selected_indices
            if 0 <= index < len(self.selected_displayed_ids)
        }
        self.selected_step_ids = [
            step_id for step_id in self.selected_step_ids if step_id not in selected_id_set
        ]
        self._refresh_available_step_listbox()
        self._refresh_selected_step_listbox()

    def remove_all_steps_from_package(self):
        if not self.selected_displayed_ids:
            return
        remove_set = set(self.selected_displayed_ids)
        self.selected_step_ids = [
            step_id for step_id in self.selected_step_ids if step_id not in remove_set
        ]
        self._refresh_available_step_listbox()
        self._refresh_selected_step_listbox()

    def move_selected_package_steps(self, direction):
        selected_indices = list(self.selected_step_listbox.curselection())
        if not selected_indices:
            messagebox.showinfo("提示", "请先选择右侧流程包里的一个或多个步骤。", parent=self.window)
            return
        selected_step_id_set = {
            self.selected_displayed_ids[index]
            for index in selected_indices
            if 0 <= index < len(self.selected_displayed_ids)
        }
        normalized_direction = -1 if int(direction or 0) < 0 else 1
        moved = False
        if normalized_direction < 0:
            for index in range(1, len(self.selected_step_ids)):
                step_id = self.selected_step_ids[index]
                prev_id = self.selected_step_ids[index - 1]
                if step_id in selected_step_id_set and prev_id not in selected_step_id_set:
                    self.selected_step_ids[index - 1], self.selected_step_ids[index] = self.selected_step_ids[index], self.selected_step_ids[index - 1]
                    moved = True
        else:
            for index in range(len(self.selected_step_ids) - 2, -1, -1):
                step_id = self.selected_step_ids[index]
                next_id = self.selected_step_ids[index + 1]
                if step_id in selected_step_id_set and next_id not in selected_step_id_set:
                    self.selected_step_ids[index], self.selected_step_ids[index + 1] = self.selected_step_ids[index + 1], self.selected_step_ids[index]
                    moved = True
        if not moved:
            return
        self._refresh_selected_step_listbox()
        for index, step_id in enumerate(self.selected_displayed_ids):
            if step_id in selected_step_id_set:
                self.selected_step_listbox.selection_set(index)

    def sort_selected_package_steps_by_id(self):
        if not self.selected_step_ids:
            return
        selected_id_set = {
            self.selected_displayed_ids[index]
            for index in self.selected_step_listbox.curselection()
            if 0 <= index < len(self.selected_displayed_ids)
        }
        self.selected_step_ids = sorted(self.selected_step_ids, key=_natural_sort_key)
        self._refresh_selected_step_listbox()
        for index, step_id in enumerate(self.selected_displayed_ids):
            if step_id in selected_id_set:
                self.selected_step_listbox.selection_set(index)

    def focus_selected_step_in_editor(self):
        step_id = self._get_active_step_id()
        if not step_id:
            messagebox.showinfo("提示", "请先在流程包里选择一个步骤。", parent=self.window)
            return
        if callable(self.on_focus_step):
            self.on_focus_step(step_id)
            self.window.lift()
            self.window.focus_force()

    def on_save(self):
        package_id = self.var_id.get().strip()
        if not package_id:
            messagebox.showerror("保存失败", "流程包ID不能为空。", parent=self.window)
            return
        package_name = self.var_name.get().strip() or package_id
        self.result = {
            "id": package_id,
            "name": package_name,
            "description": self.description_text.get("1.0", tk.END).strip(),
            "stepIds": list(self.selected_step_ids),
        }
        self.window.destroy()


class FlowEditorApp:
    # 表单「未应用改动」检测的字段清单：新增表单字段时须同步补充，
    # 否则该字段的未应用改动不会被切步骤/关窗提示捕获（详见 _form_snapshot）。
    _FORM_SNAPSHOT_VARS = (
        "var_id", "var_name", "var_stage", "var_strategy", "var_action_type", "var_enabled",
        "var_code_symbol", "var_code_reference", "var_package_ref", "var_success_log", "var_window_title",
        "var_control_name", "var_class_name", "var_automation_id", "var_control_type", "var_ui_path",
        "var_template_key",
        "var_action", "var_target_control_id", "var_input_text", "var_post_input_keys",
        "var_require_blur_submit", "var_wait_before", "var_wait_after", "var_timeout",
        "var_continue_when_control_id", "var_continue_when_condition", "var_continue_when_timeout",
        "var_continue_when_window_title_hint", "var_retry_count", "var_retry_interval", "var_on_error",
        "var_fallback_template", "var_prefer_template",
        "var_precond_condition", "var_precond_expected", "var_precond_control",
        "var_anchor_align", "var_anchor_offset_x", "var_anchor_offset_y",
        "var_relative_parent_title", "var_relative_parent_class", "var_relative_parent_framework",
        "var_relative_region_x", "var_relative_region_y", "var_relative_region_width",
        "var_relative_region_height", "var_relative_region_anchor",
    )
    _FORM_SNAPSHOT_TEXTS = (
        "description_text", "aux_checks_text", "fallbacks_text", "notes_text",
        "step_params_text", "action_config_text",
    )

    def __init__(self, root, definition_path=None, focus_step_id=None):
        self.root = root
        self.focus_step_id = str(focus_step_id or "").strip()
        wt_theme.get_theme_manager().init_theme(self.root, "flatly")
        self.root.title("WT 自动化流程链路编辑器")
        wt_dpi.geometry(self.root, 1500, 900)
        self.root.minsize(wt_dpi.scale(1260), wt_dpi.scale(760))

        self.definition_path = str(definition_path or "").strip() or resolve_initial_definition_path()
        self.flow_definition = self._load_or_default_definition(self.definition_path)
        self.steps = self.flow_definition["steps"]
        self.flow_packages = normalize_flow_packages(self.flow_definition.get("flowPackages", []))
        self.selected_index = None
        self.current_package_step_filter_id = ""
        self.dirty = False
        self._suppress_tree_select_event = False
        # 表单「未应用改动」基线：加载/应用步骤时刷新（见 _form_snapshot）
        self._form_baseline = None
        self._step_clipboard = None
        self._dragging_step_iid = ""
        self._drag_hover_iid = ""
        self._drag_hover_after = False
        self._relative_region_preview_height = 6
        self._relative_region_preview_resize_start_y = None
        self._relative_region_preview_resize_start_height = 6

        self.status_var = tk.StringVar(value="就绪")
        self.ui_scale_var = tk.StringVar(value=wt_dpi.scale_to_label(wt_dpi.load_scale_config()))
        self.path_var = tk.StringVar(value=self.definition_path)
        self.step_scope_var = tk.StringVar(value="当前显示：全部步骤")
        self.step_search_query = tk.StringVar()
        self.step_filter_category = tk.StringVar(value="全部")
        self.step_count_badge_var = tk.StringVar(value="0 / 0 步")
        self.step_title_display_var = tk.StringVar(value="未选择步骤")
        self.step_dirty_badge_var = tk.StringVar(value="✓ 已同步")

        self.runtime_gm_exe_var = tk.StringVar()
        self.runtime_source_file_var = tk.StringVar()
        self.runtime_output_dir_var = tk.StringVar()
        self.runtime_projection_file_var = tk.StringVar()

        self.var_id = tk.StringVar()
        self.var_name = tk.StringVar()
        self.var_stage = tk.StringVar()
        self.var_strategy = tk.StringVar(value="script")
        self.var_action_type = tk.StringVar(value="script")
        self.var_enabled = tk.BooleanVar(value=True)
        self.var_code_symbol = tk.StringVar()
        self.var_code_reference = tk.StringVar()
        self.var_package_ref = tk.StringVar()
        self.var_success_log = tk.StringVar()
        self.var_window_title = tk.StringVar()
        self.var_control_name = tk.StringVar()
        self.var_class_name = tk.StringVar()
        self.var_automation_id = tk.StringVar()
        self.var_control_type = tk.StringVar()
        self.var_ui_path = tk.StringVar()
        self.var_template_key = tk.StringVar()
        self.controls_summary_var = tk.StringVar(value="当前步骤细分控件：0")
        self.template_summary_var = tk.StringVar(value="请选择一个步骤模板")
        
        # RPA风格新增变量
        self.var_action = tk.StringVar(value="click")
        self.var_target_control_id = tk.StringVar()
        # 动作切换时的「目标控件」暂存：切到不使用目标控件的动作（如固定等待）会清空该字段，
        # 暂存后切回原动作可自动还原，避免用户已选控件被静默丢弃。
        # 键为动作名；加载步骤时清空，防止跨步骤串味。
        self._action_target_stash = {}
        self._shown_action_name = None
        self.var_input_text = tk.StringVar()
        self.var_post_input_keys = tk.StringVar()
        self.var_require_blur_submit = tk.BooleanVar(value=False)
        self.var_wait_before = tk.StringVar(value="0.3")
        self.var_wait_after = tk.StringVar(value="0.3")
        self.var_timeout = tk.StringVar(value="3")
        self.var_continue_when_control_id = tk.StringVar()
        self.var_continue_when_condition = tk.StringVar(value="visible")
        self.var_continue_when_timeout = tk.StringVar(value="5")
        self.var_continue_when_window_title_hint = tk.StringVar()
        self.var_retry_count = tk.StringVar(value="0")
        self.var_retry_interval = tk.StringVar(value="1")
        self.var_on_error = tk.StringVar(value="continue")  # continue, stop, retry
        self.var_fallback_template = tk.StringVar()
        self.var_prefer_template = tk.BooleanVar(value=False)
        self.var_precond_condition = tk.StringVar(value="")
        self.var_precond_expected = tk.StringVar(value="off")
        self.var_precond_control = tk.StringVar()
        self._syncing_post_input_ui = False
        self.var_relative_parent_title = tk.StringVar()
        self.var_relative_parent_class = tk.StringVar()
        self.var_relative_parent_framework = tk.StringVar(value="WPF")
        self.var_relative_region_x = tk.StringVar(value="0.45")
        self.var_relative_region_y = tk.StringVar(value="0.45")
        self.var_relative_region_width = tk.StringVar(value="0.32")
        self.var_relative_region_height = tk.StringVar(value="0.08")
        self.var_relative_region_anchor = tk.StringVar(value="center")
        self.var_anchor_offset_x = tk.StringVar(value="0")
        self.var_anchor_offset_y = tk.StringVar(value="0")
        self.var_anchor_align = tk.StringVar(value="center")
        self.action_schema_hint_var = tk.StringVar(value=build_action_schema_hint("click"))
        self.font_ui_button = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=9)
        self.font_card_title = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=11, weight="bold")
        
        # 伴随拾取模式状态变量
        self.var_concurrent_mode = tk.BooleanVar(value=False)  # 开关状态
        self.var_concurrent_hotkey = tk.StringVar(value="Ctrl+Click")  # 当前快捷键提示
        self._concurrent_listener = None  # pynput 监听器
        self._concurrent_keyboard_listener = None  # pynput 键盘监听器
        self._concurrent_ctrl_pressed = False  # Ctrl 键状态
        self._concurrent_last_click_time = 0  # 防抖
        self._concurrent_enabled = False  # 是否真正开启
        self._concurrent_event_queue = None  # 线程安全事件队列
        self._concurrent_polling_job = None  # 主线程轮询任务 ID
        self.font_section_title = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=12, weight="bold")
        self.font_help_text = tkfont.Font(root=self.root, family="Microsoft YaHei UI", size=10)

        self._configure_visual_style()
        self._build_ui()
        if not self.root.bind("<Control-s>"):
            self.root.bind("<Control-s>", lambda _event: self.cmd_save())
        for trace_var in (
            self.var_window_title,
            self.var_action,
            self.var_relative_parent_title,
            self.var_relative_parent_class,
            self.var_relative_parent_framework,
            self.var_relative_region_x,
            self.var_relative_region_y,
            self.var_relative_region_width,
            self.var_relative_region_height,
            self.var_relative_region_anchor,
            self.var_anchor_offset_x,
            self.var_anchor_offset_y,
        ):
            trace_var.trace_add("write", lambda *_args: self._refresh_relative_region_preview())
        for v in (self.var_name, self.var_action, self.var_target_control_id, self.var_action_type):
            v.trace_add("write", lambda *_args: self._update_step_header_display())
        self._load_runtime_config_into_form(self.flow_definition.get("runtimeConfig", {}))
        self._load_flow_packages_into_form(self.flow_definition.get("flowPackages", []))
        self._refresh_template_library()
        self._refresh_steps_tree()
        self._refresh_overview()
        self._set_title()
        if self.steps:
            if self.focus_step_id:
                self.root.after_idle(lambda: self._focus_step_by_id(self.focus_step_id))
            else:
                self._select_step(0)

    def _load_or_default_definition(self, file_path):
        payload = load_json_file(file_path)
        if not isinstance(payload, dict):
            payload = json.loads(json.dumps(DEFAULT_FLOW_DEFINITION, ensure_ascii=False))
        payload.setdefault("version", "1.0")
        payload.setdefault("project", "WT_Automation")
        payload.setdefault("description", "")
        payload["runtimeConfig"] = normalize_runtime_config(payload.get("runtimeConfig", {}))
        payload["flowPackages"] = normalize_flow_packages(payload.get("flowPackages", []))
        payload.setdefault("steps", [])
        payload["steps"] = [normalize_step(step, index) for index, step in enumerate(payload["steps"])]
        return payload

    def _configure_visual_style(self):
        self.root.configure(bg=EDITOR_THEME["bg"])
        try:
            tkfont.nametofont("TkDefaultFont").configure(family="Microsoft YaHei UI", size=10)
            tkfont.nametofont("TkTextFont").configure(family="Microsoft YaHei UI", size=10)
            tkfont.nametofont("TkMenuFont").configure(family="Microsoft YaHei UI", size=10)
            tkfont.nametofont("TkHeadingFont").configure(family="Microsoft YaHei UI", size=10, weight="bold")
        except Exception:
            pass
        self.root.option_add("*Label.foreground", EDITOR_THEME["text"])
        self.root.option_add("*LabelFrame.background", EDITOR_THEME["panel"])
        self.root.option_add("*Frame.background", EDITOR_THEME["bg"])
        self.root.option_add("*Checkbutton.background", EDITOR_THEME["panel"])
        self.root.option_add("*Entry.background", "#ffffff")
        self.root.option_add("*Text.background", EDITOR_THEME["panel_soft"])
        try:
            style = ttk.Style()
            style.theme_use("clam")
            style.configure(
                "Treeview",
                rowheight=28,
                font=("Microsoft YaHei UI", 10),
                background=EDITOR_THEME["panel_soft"],
                fieldbackground=EDITOR_THEME["panel_soft"],
                foreground=EDITOR_THEME["text"],
                bordercolor=EDITOR_THEME["border"],
            )
            style.map("Treeview", background=[("selected", "#dbeafe")], foreground=[("selected", EDITOR_THEME["text"])])
            style.configure(
                "Treeview.Heading",
                font=("Microsoft YaHei UI", 10, "bold"),
                background="#f8fbff",
                foreground=EDITOR_THEME["text"],
                relief="flat",
                borderwidth=1,
            )
            style.configure("Treeview", rowheight=28, font=("Microsoft YaHei UI", 10))
            style.configure("TNotebook", background=EDITOR_THEME["bg"])
            style.configure("TNotebook.Tab", padding=(16, 8), font=("Microsoft YaHei UI", 10), background="#eef4ff")
            style.map("TNotebook.Tab", background=[("selected", "#ffffff")], foreground=[("selected", EDITOR_THEME["primary"])])
            style.configure("TCombobox", padding=5, fieldbackground="#ffffff", background="#ffffff")
        except Exception:
            pass

    def _create_action_button(self, parent, text, command, tone="default", **kwargs):
        colors = {
            "default": {"bg": "#ffffff", "active": "#e2e8f0", "hover": "#f8fafc", "border": EDITOR_THEME["border"]},
            "primary": {"bg": EDITOR_THEME["primary_soft"], "active": "#93c5fd", "hover": "#dbeafe", "border": "#bfdbfe"},
            "success": {"bg": EDITOR_THEME["success_soft"], "active": "#86efac", "hover": "#bbf7d0", "border": "#86efac"},
            "danger": {"bg": EDITOR_THEME["danger_soft"], "active": "#fca5a5", "hover": "#fecaca", "border": "#fca5a5"},
            "accent": {"bg": "#e0e7ff", "active": "#a5b4fc", "hover": "#c7d2fe", "border": "#c7d2fe"},
        }
        palette = colors.get(tone, colors["default"])
        padx = kwargs.pop("padx", 12)
        pady = kwargs.pop("pady", 5)
        font = kwargs.pop("font", self.font_ui_button)
        border_color = palette.get("border", EDITOR_THEME["border"])
        button = tk.Button(
            parent,
            text=text,
            command=command,
            bg=palette["bg"],
            activebackground=palette["active"],
            fg=EDITOR_THEME["text"],
            activeforeground=EDITOR_THEME["text"],
            relief=tk.FLAT,
            bd=0,
            padx=padx,
            pady=pady,
            cursor="hand2",
            font=font,
            highlightthickness=1,
            highlightbackground=border_color,
            **kwargs,
        )
        def on_enter(_e):
            if button["state"] != tk.DISABLED:
                button.configure(bg=palette["hover"])
        def on_leave(_e):
            if button["state"] != tk.DISABLED:
                button.configure(bg=palette["bg"])
        button.bind("<Enter>", on_enter)
        button.bind("<Leave>", on_leave)
        return button

    def _style_text_surface(self, widget, *, dark=False):
        if dark:
            widget.configure(
                bg="#111827",
                fg="#f9fafb",
                insertbackground="#f9fafb",
                relief=tk.FLAT,
                bd=1,
                highlightbackground=EDITOR_THEME["border"],
                highlightthickness=1,
            )
            return
        widget.configure(
            bg=EDITOR_THEME["panel_soft"],
            fg=EDITOR_THEME["text"],
            insertbackground=EDITOR_THEME["text"],
            relief=tk.FLAT,
            bd=1,
            highlightbackground=EDITOR_THEME["border"],
            highlightthickness=1,
        )

    def _create_form_card(self, parent, title, description="", tone="default"):
        palette_map = {
            "default": {
                "border": EDITOR_THEME["border"],
                "header_bg": "#f8fbff",
                "title_fg": EDITOR_THEME["text"],
                "desc_fg": EDITOR_THEME["muted"],
            },
            "primary": {
                "border": "#bfdbfe",
                "header_bg": "#eff6ff",
                "title_fg": EDITOR_THEME["primary"],
                "desc_fg": "#4b5563",
            },
        }
        palette = palette_map.get(tone, palette_map["default"])
        card = tk.Frame(
            parent,
            bg=EDITOR_THEME["panel"],
            highlightbackground=palette["border"],
            highlightthickness=1,
            bd=0,
        )
        header = tk.Frame(card, bg=palette["header_bg"], padx=16, pady=10)
        header.pack(fill=tk.X)
        tk.Label(
            header,
            text=title,
            font=self.font_card_title,
            fg=palette["title_fg"],
            bg=palette["header_bg"],
            anchor="w",
        ).pack(fill=tk.X, anchor="w")
        if description:
            tk.Label(
                header,
                text=description,
                fg=palette["desc_fg"],
                bg=palette["header_bg"],
                justify=tk.LEFT,
                anchor="w",
                wraplength=980,
            ).pack(fill=tk.X, anchor="w", pady=(4, 0))
        tk.Frame(card, height=1, bg=palette["border"]).pack(fill=tk.X)
        body = tk.Frame(card, bg=EDITOR_THEME["panel"], padx=16, pady=14)
        body.pack(fill=tk.BOTH, expand=True)
        return card, body

    def _on_ui_scale_changed(self):
        """界面缩放变更：写入共享配置并即时应用到主窗口。"""
        try:
            value = wt_dpi.label_to_scale(self.ui_scale_var.get())
            wt_dpi.apply_scale(self.root, value, 1500, 900)
        except Exception:
            pass

    def _build_ui(self):
        toolbar = tk.Frame(self.root, padx=14, pady=8, bg=EDITOR_THEME["toolbar"], highlightbackground=EDITOR_THEME["border"], highlightthickness=1)
        toolbar.pack(fill=tk.X)

        # 文件操作组
        self._create_action_button(toolbar, "新建默认链路", self.cmd_new_default).pack(side=tk.LEFT, padx=3)
        self._create_action_button(toolbar, "打开链路文件", self.cmd_open).pack(side=tk.LEFT, padx=3)
        self._create_action_button(toolbar, "保存", self.cmd_save, tone="success").pack(side=tk.LEFT, padx=3)
        self._create_action_button(toolbar, "另存为", self.cmd_save_as).pack(side=tk.LEFT, padx=3)

        tk.Frame(toolbar, width=1, bg=EDITOR_THEME["border"]).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # 脚本转换与智能组
        self._create_action_button(toolbar, "转换 Recorder 脚本", self.cmd_convert_recorder_script, tone="primary").pack(side=tk.LEFT, padx=3)
        self._create_action_button(toolbar, "AI 助手", self.cmd_open_ai_tab, tone="accent").pack(side=tk.LEFT, padx=3)

        tk.Frame(toolbar, width=1, bg=EDITOR_THEME["border"]).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # 工具与参考组
        self._create_action_button(toolbar, "记事本打开JSON", self.cmd_open_json_file).pack(side=tk.LEFT, padx=3)
        self._create_action_button(toolbar, "打开参考项目", self.cmd_open_reference_project).pack(side=tk.LEFT, padx=3)
        self._create_action_button(toolbar, "刷新总览", self._refresh_overview).pack(side=tk.LEFT, padx=3)

        # 伴随拾取模式分隔线
        tk.Frame(toolbar, width=1, bg=EDITOR_THEME["border"]).pack(side=tk.LEFT, fill=tk.Y, padx=8)

        # 伴随拾取模式开关
        tk.Checkbutton(
            toolbar,
            text="伴随拾取模式",
            variable=self.var_concurrent_mode,
            command=self._toggle_concurrent_mode,
            bg=EDITOR_THEME["toolbar"],
            fg=EDITOR_THEME["text"],
            activebackground=EDITOR_THEME["toolbar"],
            font=("Microsoft YaHei UI", 9),
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=4)
        
        # 快捷键提示标签
        self._concurrent_hotkey_label = tk.Label(
            toolbar,
            textvariable=self.var_concurrent_hotkey,
            bg=EDITOR_THEME["toolbar"],
            fg="#f59e0b",
            font=("Microsoft YaHei UI", 8),
            padx=4,
        )
        self._concurrent_hotkey_label.pack(side=tk.LEFT, padx=2)
        
        # 状态标签
        self.var_concurrent_status = tk.StringVar(value="关闭")
        self._concurrent_status_label = tk.Label(
            toolbar,
            textvariable=self.var_concurrent_status,
            bg=EDITOR_THEME["toolbar"],
            fg=EDITOR_THEME["muted"],
            font=("Microsoft YaHei UI", 8),
            padx=4,
        )
        self._concurrent_status_label.pack(side=tk.LEFT, padx=2)

        # 界面缩放（类似 Windows 显示缩放）
        tk.Frame(toolbar, width=1, bg=EDITOR_THEME["border"]).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        tk.Label(
            toolbar,
            text="界面缩放",
            bg=EDITOR_THEME["toolbar"],
            fg=EDITOR_THEME["text"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Combobox(
            toolbar,
            textvariable=self.ui_scale_var,
            values=[label for label, _ in wt_dpi.SCALE_PRESETS],
            state="readonly",
            width=7,
        ).pack(side=tk.LEFT, padx=(0, 8))
        self.ui_scale_var.trace_add("write", lambda *_args: self._on_ui_scale_changed())

        tk.Label(toolbar, textvariable=self.status_var, bg=EDITOR_THEME["toolbar"], fg=EDITOR_THEME["muted"], font=("Microsoft YaHei UI", 9)).pack(side=tk.RIGHT)

        path_bar = tk.Frame(self.root, padx=14, pady=6, bg=EDITOR_THEME["bg"])
        path_bar.pack(fill=tk.X)
        tk.Label(path_bar, text="当前链路文件", font=("Microsoft YaHei UI", 9, "bold"), fg=EDITOR_THEME["text"], bg=EDITOR_THEME["bg"]).pack(side=tk.LEFT)
        path_entry = tk.Entry(
            path_bar,
            textvariable=self.path_var,
            font=("Consolas", 9),
            bg="#ffffff",
            fg=EDITOR_THEME["text"],
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=EDITOR_THEME["border"],
        )
        path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0), ipady=3)

        body = tk.Frame(self.root, padx=10, pady=10, bg=EDITOR_THEME["bg"])
        body.pack(fill=tk.BOTH, expand=True)

        # 主区改为可拖动分隔的左右窗格：左侧“流程步骤”栏可向右拖拽增宽
        split = tk.PanedWindow(
            body,
            orient=tk.HORIZONTAL,
            sashrelief=tk.FLAT,
            sashwidth=4,
            sashcursor="sb_h_double_arrow",
            bg=EDITOR_THEME["border"],
            showhandle=False,
            bd=0,
            opaqueresize=False,
        )
        split.pack(fill=tk.BOTH, expand=True)

        left = tk.Frame(split, bg=EDITOR_THEME["panel"], highlightbackground=EDITOR_THEME["border"], highlightthickness=1)
        right_main = tk.Frame(split, bg=EDITOR_THEME["bg"])

        split.add(left, width=330, minsize=240)
        split.add(right_main, minsize=420)

        self._build_left_panel(left)
        self._build_right_main(right_main)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_scrollable_panel(self, parent):
        canvas = tk.Canvas(parent, highlightthickness=0, borderwidth=0)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=scrollbar.set)

        content = tk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")

        _sr_scheduled = [False]
        _last_width = [0]
        _canvas_cfg_scheduled = [False]

        def on_content_configure(_event=None):
            # after_idle 语义（Tk doc/after.n）：回调在事件队列排空、
            # 无事件可处理时执行一次。Configure 风暴期间不执行，风暴
            # 结束才跑一次 —— 效果即合帧：风暴 N 次触发只做 1 次
            # bbox("all") 全量重算（每帧调用会造成 resize 迟滞）
            if _sr_scheduled[0]:
                return
            _sr_scheduled[0] = True
            def _apply():
                _sr_scheduled[0] = False
                try:
                    canvas.configure(scrollregion=canvas.bbox("all"))
                except Exception:
                    pass
            canvas.after_idle(_apply)

        def on_canvas_configure(_event=None):
            new_w = canvas.winfo_width()
            if new_w <= 1 or new_w == _last_width[0]:
                return
            if _canvas_cfg_scheduled[0]:
                return
            _canvas_cfg_scheduled[0] = True
            def _apply_w():
                _canvas_cfg_scheduled[0] = False
                try:
                    cur_w = canvas.winfo_width()
                    if cur_w > 1 and cur_w != _last_width[0]:
                        _last_width[0] = cur_w
                        canvas.itemconfigure(window_id, width=cur_w)
                except Exception:
                    pass
            canvas.after_idle(_apply_w)

        # 统一滚轮路由（wt_wheel_router）：注册即可，root 上一次绑定全局生效。
        # 不再用 Enter/Leave 动态 bind_all/unbind_all —— unbind_all 会把
        # 主控台等其他窗口的常驻滚轮绑定一并拆掉（ttkbootstrap 2.0 源码
        # 注释独立证实了同一坑），细节见 wt_wheel_router.py 模块文档。
        wt_wheel_router.register(canvas)
        wt_wheel_router.bind_root(self.root)

        content.bind("<Configure>", on_content_configure)
        canvas.bind("<Configure>", on_canvas_configure)

        return content

    def _build_left_panel(self, parent):
        top_header = tk.Frame(parent, bg=EDITOR_THEME["panel"])
        top_header.pack(fill=tk.X)
        tk.Label(top_header, text="流程步骤", font=self.font_section_title, bg=EDITOR_THEME["panel"]).pack(side=tk.LEFT)
        tk.Label(top_header, textvariable=self.step_scope_var, fg=EDITOR_THEME["muted"], bg=EDITOR_THEME["panel"]).pack(side=tk.LEFT, padx=(10, 0))
        self._create_action_button(top_header, "查看全部步骤", self.cmd_clear_step_package_filter).pack(side=tk.RIGHT)

        button_row = tk.Frame(parent, bg=EDITOR_THEME["panel"])
        button_row.pack(fill=tk.X, pady=(8, 8))
        button_row_top = tk.Frame(button_row, bg=EDITOR_THEME["panel"])
        button_row_top.pack(fill=tk.X)
        button_row_bottom = tk.Frame(button_row, bg=EDITOR_THEME["panel"])
        button_row_bottom.pack(fill=tk.X, pady=(6, 0))
        self._create_action_button(button_row_top, "新增", self.cmd_add_step).pack(side=tk.LEFT, padx=2)
        self.quick_add_template_button = self._create_action_button(
            button_row_top,
            "模板新增",
            lambda: self._show_quick_add_template_menu(self.quick_add_template_button),
            tone="primary",
        )
        self.quick_add_template_button.pack(side=tk.LEFT, padx=2)
        self._create_action_button(button_row_top, "复制", self.cmd_duplicate_step).pack(side=tk.LEFT, padx=2)
        self._create_action_button(button_row_top, "删除所选", self.cmd_delete_step, tone="danger").pack(side=tk.LEFT, padx=2)
        self._create_action_button(button_row_bottom, "上移", self.cmd_move_up).pack(side=tk.LEFT, padx=2)
        self._create_action_button(button_row_bottom, "下移", self.cmd_move_down).pack(side=tk.LEFT, padx=2)
        self._create_action_button(button_row_bottom, "重排序ID", self.cmd_renumber_step_ids, tone="primary").pack(side=tk.LEFT, padx=2)
        tk.Label(
            button_row_bottom,
            text="也支持长按拖动步骤行调整顺序。",
            fg=EDITOR_THEME["muted"],
            bg=EDITOR_THEME["panel"],
            anchor="w",
            justify=tk.LEFT,
        ).pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(
            parent,
            text="模板新增已预置 4 类高频步骤：按钮、输入框、下拉项、相对区域。",
            fg=EDITOR_THEME["muted"],
            bg=EDITOR_THEME["panel"],
            anchor="w",
            justify=tk.LEFT,
        ).pack(fill=tk.X, pady=(0, 6))

        # 步骤搜索与分类筛选条
        search_filter_bar = tk.Frame(parent, bg=EDITOR_THEME["panel"])
        search_filter_bar.pack(fill=tk.X, pady=(0, 6))

        search_box = tk.Frame(search_filter_bar, bg="#ffffff", bd=1, relief=tk.SOLID)
        search_box.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(search_box, text="🔍", bg="#ffffff", fg="#64748b", font=("Segoe UI Emoji", 9)).pack(side=tk.LEFT, padx=(5, 2))
        self.step_search_entry = tk.Entry(
            search_box,
            textvariable=self.step_search_query,
            bd=0,
            highlightthickness=0,
            bg="#ffffff",
            font=("Microsoft YaHei UI", 9),
        )
        self.step_search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=2)
        self.step_search_query.trace_add("write", lambda *args: self._on_step_filter_changed())

        clear_btn = tk.Label(search_box, text="✕", bg="#ffffff", fg="#94a3b8", cursor="hand2", font=("Segoe UI", 9))
        clear_btn.pack(side=tk.RIGHT, padx=4)
        clear_btn.bind("<Button-1>", lambda _e: (self.step_search_query.set(""), self._on_step_filter_changed()))

        self.step_filter_combo = ttk.Combobox(
            search_filter_bar,
            textvariable=self.step_filter_category,
            values=("全部", "仅启用", "仅停用", "仅动作步", "仅子链路", "带模板"),
            state="readonly",
            width=8,
        )
        self.step_filter_combo.pack(side=tk.LEFT, padx=(6, 4))
        self.step_filter_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_step_filter_changed())

        self.step_count_badge_label = tk.Label(
            search_filter_bar,
            textvariable=self.step_count_badge_var,
            bg=EDITOR_THEME.get("primary_soft", "#eff6ff"),
            fg=EDITOR_THEME.get("primary", "#1d4ed8"),
            font=("Microsoft YaHei UI", 8, "bold"),
            padx=6,
            pady=2,
            relief=tk.FLAT,
        )
        self.step_count_badge_label.pack(side=tk.LEFT)

        tree_frame = tk.Frame(parent)
        tree_frame.pack(fill=tk.BOTH, expand=True)
        self.step_tree = ttk.Treeview(
            tree_frame,
            columns=("seq", "name", "action", "target"),
            show="headings",
            selectmode="extended",
        )
        self.step_tree.heading("seq", text="#")
        self.step_tree.heading("name", text="步骤")
        self.step_tree.heading("action", text="动作")
        self.step_tree.heading("target", text="目标")
        self.step_tree.column("seq", width=42, minwidth=42, stretch=False, anchor="center")
        self.step_tree.column("name", width=280, minwidth=160, stretch=True, anchor="w")
        self.step_tree.column("action", width=180, minwidth=120, stretch=False, anchor="w")
        self.step_tree.column("target", width=320, minwidth=160, stretch=True, anchor="w")
        self.step_tree.tag_configure("disabled", foreground=EDITOR_THEME["muted"])
        self.step_tree.tag_configure("action_step", background="#f8fbff")
        self.step_tree.tag_configure("flow_ref_step", background="#f8fafc")
        self.step_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.step_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.step_tree.bind("<ButtonPress-1>", self._start_step_drag, add="+")
        self.step_tree.bind("<B1-Motion>", self._track_step_drag, add="+")
        self.step_tree.bind("<ButtonRelease-1>", self._finish_step_drag, add="+")
        self.step_tree.bind("<Button-3>", self._show_step_context_menu)
        self.step_tree.bind("<Button-2>", self._show_step_context_menu)
        self.step_tree.bind("<Control-c>", lambda _e: (self.cmd_copy_selected_step(), "break")[1])
        self.step_tree.bind("<Control-C>", lambda _e: (self.cmd_copy_selected_step(), "break")[1])
        self.step_tree.bind("<Control-v>", lambda _e: (self.cmd_paste_step_below(), "break")[1])
        self.step_tree.bind("<Control-V>", lambda _e: (self.cmd_paste_step_below(), "break")[1])
        self.step_tree.bind("<Control-d>", lambda _e: (self.cmd_duplicate_step(), "break")[1])
        self.step_tree.bind("<Control-D>", lambda _e: (self.cmd_duplicate_step(), "break")[1])
        self.step_tree.bind("<space>", lambda _e: (self.cmd_toggle_selected_step_enabled(), "break")[1])
        self.step_tree.bind("<Delete>", lambda _e: (self.cmd_delete_step(), "break")[1])
        self.step_tree.bind("<Alt-Up>", lambda _e: (self.cmd_move_up(), "break")[1])
        self.step_tree.bind("<Alt-Down>", lambda _e: (self.cmd_move_down(), "break")[1])

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.step_tree.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scrollbar = ttk.Scrollbar(parent, orient="horizontal", command=self.step_tree.xview)
        h_scrollbar.pack(fill=tk.X, pady=(6, 0))
        self.step_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=h_scrollbar.set)

        # 下方添加流程包快捷预览
        package_quick_frame = tk.LabelFrame(parent, text="流程包概览", padx=8, pady=8)
        package_quick_frame.pack(fill=tk.X, pady=(10, 0))
        self.package_quick_text = tk.Text(package_quick_frame, height=5, wrap=tk.WORD, font=("Consolas", 9))
        self._style_text_surface(self.package_quick_text)
        self.package_quick_text.pack(fill=tk.X)

    def _build_editor_sticky_top_bar(self, parent):
        bar = tk.Frame(parent, bg=EDITOR_THEME["card"], bd=1, relief=tk.SOLID, padx=14, pady=8)
        bar.pack(fill=tk.X, padx=0, pady=(0, 4))

        left_side = tk.Frame(bar, bg=EDITOR_THEME["card"])
        left_side.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.step_title_display_label = tk.Label(
            left_side,
            textvariable=self.step_title_display_var,
            font=("Microsoft YaHei UI", 11, "bold"),
            fg=EDITOR_THEME["text"],
            bg=EDITOR_THEME["card"],
            anchor="w",
        )
        self.step_title_display_label.pack(side=tk.LEFT)

        self.step_form_dirty_badge = tk.Label(
            left_side,
            textvariable=self.step_dirty_badge_var,
            font=("Microsoft YaHei UI", 9, "bold"),
            bg="#f0fdf4",
            fg="#16a34a",
            padx=8,
            pady=2,
            relief=tk.FLAT,
        )
        self.step_form_dirty_badge.pack(side=tk.LEFT, padx=(12, 0))

        right_side = tk.Frame(bar, bg=EDITOR_THEME["card"])
        right_side.pack(side=tk.RIGHT)

        self.apply_button = self._create_action_button(
            right_side,
            "💾 应用到当前步骤",
            self.cmd_apply_step,
            tone="success",
        )
        self.apply_button.pack(side=tk.LEFT, padx=(0, 6))

        self.reload_button = self._create_action_button(
            right_side,
            "↺ 重置当前表单",
            self.cmd_reload_step,
        )
        self.reload_button.pack(side=tk.LEFT)

    def _build_right_main(self, parent):
        notebook = ttk.Notebook(parent)
        notebook.pack(fill=tk.BOTH, expand=True)

        # 标签1：步骤编辑（核心）
        tab_edit = tk.Frame(notebook)
        notebook.add(tab_edit, text="步骤编辑")
        self._build_editor_sticky_top_bar(tab_edit)
        center_scroll = self._build_scrollable_panel(tab_edit)
        self._build_center_panel(center_scroll)

        # 标签2：流程包管理
        tab_packages = tk.Frame(notebook)
        notebook.add(tab_packages, text="流程包管理")
        packages_scroll = self._build_scrollable_panel(tab_packages)
        self._build_packages_tab(packages_scroll)

        # 标签3：运行参数与总览
        tab_config = tk.Frame(notebook)
        notebook.add(tab_config, text="运行配置")
        config_scroll = self._build_scrollable_panel(tab_config)
        self._build_config_tab(config_scroll)

        # 标签4：模板库与帮助
        tab_help = tk.Frame(notebook)
        notebook.add(tab_help, text="模板与帮助")
        help_scroll = self._build_scrollable_panel(tab_help)
        self._build_help_tab(help_scroll)

        # 标签5：AI 助手
        tab_ai = tk.Frame(notebook)
        notebook.add(tab_ai, text="AI 助手")
        ai_scroll = self._build_scrollable_panel(tab_ai)
        self._build_ai_tab(ai_scroll)
        # 保存 notebook 引用以便切换
        self._notebook = notebook
        self._tab_ai_index = 4

    def _build_center_panel(self, parent):
        basic_card, basic = self._create_form_card(
            parent,
            "1. 这一步是什么",
            "先确定步骤名称、动作类型、策略和流程包引用，方便后续维护与复用。",
        )
        basic_card.pack(fill=tk.X, pady=(2, 0))
        basic.columnconfigure(1, weight=1)
        basic.columnconfigure(3, weight=1)

        row = 0
        self._grid_label_entry(basic, "步骤名称 *", self.var_name, row, 0)
        self._grid_label_entry(basic, "步骤ID", self.var_id, row, 2)
        row += 1
        self._grid_label_entry(basic, "阶段", self.var_stage, row, 0)
        tk.Label(basic, text="动作类型").grid(row=row, column=2, sticky="w", pady=4)
        self.action_type_combo = ttk.Combobox(
            basic,
            textvariable=self.var_action_type,
            values=("script", "action", "flow_ref", "placeholder"),
            state="readonly",
        )
        self.action_type_combo.grid(row=row, column=3, sticky="ew", padx=(8, 12), pady=4)
        row += 1
        tk.Label(basic, text="执行策略").grid(row=row, column=0, sticky="w", pady=4)
        self.strategy_combo = ttk.Combobox(
            basic,
            textvariable=self.var_strategy,
            values=("script", "action", "flow_ref", "script -> image -> ai", "template -> script", "image -> ai"),
        )
        self.strategy_combo.grid(row=row, column=1, sticky="ew", padx=(8, 12), pady=4)
        self._grid_label_entry(basic, "成功日志", self.var_success_log, row, 2)
        row += 1
        self._grid_label_entry(basic, "目标窗口", self.var_window_title, row, 0)
        tk.Label(basic, text="流程包引用").grid(row=row, column=2, sticky="w", pady=4)
        self.package_ref_combo = ttk.Combobox(basic, textvariable=self.var_package_ref, state="readonly")
        self.package_ref_combo.grid(row=row, column=3, sticky="ew", padx=(8, 12), pady=4)
        row += 1
        self._grid_label_entry(basic, "代码符号", self.var_code_symbol, row, 0)
        self._grid_label_entry(basic, "代码文件", self.var_code_reference, row, 2)
        row += 1
        tk.Checkbutton(basic, text="启用该步骤", variable=self.var_enabled).grid(row=row, column=0, sticky="w", pady=4)

        action_card, action_frame = self._create_form_card(
            parent,
            "2. 这一步要执行什么动作",
            "把常见动作、等待、重试和失败兜底集中在一张卡片里，减少来回切换。",
        )
        action_card.pack(fill=tk.X, pady=(10, 0))
        action_frame.columnconfigure(1, weight=1)
        action_frame.columnconfigure(3, weight=1)
        action_frame.columnconfigure(5, weight=1)

        tk.Label(
            action_frame,
            text="常见动作直接在这里配置，不再强迫你手写 JSON。带 * 为当前动作常用必填项。",
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        self._create_action_button(
            action_frame,
            "从剪贴板导入相对区域",
            self.cmd_import_relative_region_from_clipboard,
            tone="primary",
        ).grid(row=0, column=4, columnspan=2, sticky="e", pady=(0, 4))

        self.action_label = tk.Label(action_frame, text="动作 *")
        self.action_label.grid(row=1, column=0, sticky="w", pady=4)
        self.action_combo = ttk.Combobox(
            action_frame,
            textvariable=self.var_action,
            values=get_action_names(),
            state="readonly",
        )
        self.action_combo.grid(row=1, column=1, sticky="ew", padx=(8, 12), pady=4)

        self.target_control_label = tk.Label(action_frame, text="目标控件 *")
        self.target_control_label.grid(row=1, column=2, sticky="w", pady=4)
        self.target_control_container = tk.Frame(action_frame, bg=EDITOR_THEME["panel"])
        self.target_control_container.grid(row=1, column=3, sticky="ew", padx=(8, 12), pady=4)
        self.target_control_container.columnconfigure(0, weight=1)

        self.target_control_combo = ttk.Combobox(
            self.target_control_container,
            textvariable=self.var_target_control_id,
            state="readonly",
            postcommand=self._refresh_action_control_choices,
        )
        self.target_control_combo.grid(row=0, column=0, sticky="ew")

        self.target_control_picker_btn = tk.Button(
            self.target_control_container,
            text="🔍选控件",
            command=self._open_target_control_picker,
            bg=EDITOR_THEME.get("primary_soft", "#eff6ff"),
            fg=EDITOR_THEME.get("primary", "#1d4ed8"),
            activebackground="#bfdbfe",
            relief=tk.FLAT,
            bd=1,
            cursor="hand2",
            font=("Microsoft YaHei UI", 8),
            padx=4,
            pady=0,
        )
        self.target_control_picker_btn.grid(row=0, column=1, padx=(3, 1))

        self.target_control_view_btn = tk.Button(
            self.target_control_container,
            text="👁️",
            command=self._show_target_control_info,
            bg="#f1f5f9",
            fg=EDITOR_THEME["text"],
            activebackground="#e2e8f0",
            relief=tk.FLAT,
            bd=1,
            cursor="hand2",
            font=("Segoe UI Emoji", 8),
            padx=4,
            pady=0,
        )
        self.target_control_view_btn.grid(row=0, column=2, padx=(1, 0))

        self.input_param_label = tk.Label(action_frame, text="输入/参数")
        self.input_param_label.grid(row=1, column=4, sticky="w", pady=4)
        # 可编辑 Combobox：目标控件带 optionValues（如下拉框已采的"组/公共/私有"）时
        # 下拉候选直接可用，仍可手输任意文本
        self.input_param_entry = ttk.Combobox(
            action_frame,
            textvariable=self.var_input_text,
            state="normal",
            postcommand=self._refresh_input_param_options,
        )
        self.input_param_entry.grid(row=1, column=5, sticky="ew", padx=(8, 12), pady=4)

        self.wait_before_label = tk.Label(action_frame, text="前等待(秒)")
        self.wait_before_label.grid(row=2, column=0, sticky="w", pady=4)
        self.wait_before_entry = tk.Entry(action_frame, textvariable=self.var_wait_before)
        self.wait_before_entry.grid(row=2, column=1, sticky="ew", padx=(8, 12), pady=4)
        self.wait_after_label = tk.Label(action_frame, text="后等待(秒)")
        self.wait_after_label.grid(row=2, column=2, sticky="w", pady=4)
        self.wait_after_entry = tk.Entry(action_frame, textvariable=self.var_wait_after)
        self.wait_after_entry.grid(row=2, column=3, sticky="ew", padx=(8, 12), pady=4)
        self.timeout_label = tk.Label(action_frame, text="超时(秒)")
        self.timeout_label.grid(row=2, column=4, sticky="w", pady=4)
        self.timeout_entry = tk.Entry(action_frame, textvariable=self.var_timeout)
        self.timeout_entry.grid(row=2, column=5, sticky="ew", padx=(8, 12), pady=4)

        self.continue_when_control_label = tk.Label(action_frame, text="续跑控件")
        self.continue_when_control_label.grid(row=3, column=0, sticky="w", pady=4)
        self.continue_when_control_combo = ttk.Combobox(
            action_frame,
            textvariable=self.var_continue_when_control_id,
            state="readonly",
            postcommand=self._refresh_action_control_choices,
        )
        self.continue_when_control_combo.grid(row=3, column=1, sticky="ew", padx=(8, 12), pady=4)
        self.continue_when_condition_label = tk.Label(action_frame, text="续跑条件")
        self.continue_when_condition_label.grid(row=3, column=2, sticky="w", pady=4)
        self.continue_when_condition_combo = ttk.Combobox(
            action_frame,
            textvariable=self.var_continue_when_condition,
            values=ALLOWED_CONTINUE_WHEN_CONDITIONS,
            state="readonly",
        )
        self.continue_when_condition_combo.grid(row=3, column=3, sticky="ew", padx=(8, 12), pady=4)
        self.continue_when_timeout_label = tk.Label(action_frame, text="续跑超时(秒)")
        self.continue_when_timeout_label.grid(row=3, column=4, sticky="w", pady=4)
        self.continue_when_timeout_entry = tk.Entry(action_frame, textvariable=self.var_continue_when_timeout)
        self.continue_when_timeout_entry.grid(row=3, column=5, sticky="ew", padx=(8, 12), pady=4)

        self.continue_when_window_title_hint_label = tk.Label(action_frame, text="续跑窗口提示")
        self.continue_when_window_title_hint_label.grid(row=4, column=0, sticky="w", pady=4)
        self.continue_when_window_title_hint_entry = tk.Entry(action_frame, textvariable=self.var_continue_when_window_title_hint)
        self.continue_when_window_title_hint_entry.grid(
            row=4,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(8, 12),
            pady=4,
        )
        tk.Label(
            action_frame,
            text="动作完成后优先按续跑条件自动检测界面响应；waitAfter 仍保留，适合调试和微小缓冲。",
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=420,
        ).grid(row=4, column=4, columnspan=2, sticky="w", pady=4)

        self.retry_count_label = tk.Label(action_frame, text="失败重试次数")
        self.retry_count_label.grid(row=5, column=0, sticky="w", pady=4)
        self.retry_count_entry = tk.Entry(action_frame, textvariable=self.var_retry_count)
        self.retry_count_entry.grid(row=5, column=1, sticky="ew", padx=(8, 12), pady=4)
        self.retry_interval_label = tk.Label(action_frame, text="重试间隔(秒)")
        self.retry_interval_label.grid(row=5, column=2, sticky="w", pady=4)
        self.retry_interval_entry = tk.Entry(action_frame, textvariable=self.var_retry_interval)
        self.retry_interval_entry.grid(row=5, column=3, sticky="ew", padx=(8, 12), pady=4)
        self.on_error_label = tk.Label(action_frame, text="失败后")
        self.on_error_label.grid(row=5, column=4, sticky="w", pady=4)
        self.on_error_combo = ttk.Combobox(
            action_frame,
            textvariable=self.var_on_error,
            values=ALLOWED_ON_ERROR_MODES,
            state="readonly",
        )
        self.on_error_combo.grid(row=5, column=5, sticky="ew", padx=(8, 12), pady=4)

        self.fallback_template_label = tk.Label(action_frame, text="兜底模板/匹配")
        self.fallback_template_label.grid(row=6, column=0, sticky="w", pady=4)
        self.fallback_template_entry = tk.Entry(action_frame, textvariable=self.var_fallback_template)
        self.fallback_template_entry.grid(
            row=6,
            column=1,
            columnspan=3,
            sticky="ew",
            padx=(8, 12),
            pady=4,
        )
        self.fallback_template_choose_button = tk.Button(
            action_frame,
            text="选择模板图片",
            command=self.cmd_choose_fallback_template,
        )
        self.fallback_template_choose_button.configure(
            bg=EDITOR_THEME["primary_soft"],
            activebackground="#bfdbfe",
            fg=EDITOR_THEME["text"],
            activeforeground=EDITOR_THEME["text"],
            relief=tk.FLAT,
            bd=1,
            cursor="hand2",
            highlightbackground=EDITOR_THEME["border"],
        )
        self.fallback_template_choose_button.grid(row=6, column=4, sticky="ew", padx=(0, 8), pady=4)
        self.fallback_template_open_button = tk.Button(
            action_frame,
            text="打开模板库",
            command=self.cmd_open_template_library,
        )
        self.fallback_template_open_button.configure(
            bg="#ffffff",
            activebackground="#f8fafc",
            fg=EDITOR_THEME["text"],
            activeforeground=EDITOR_THEME["text"],
            relief=tk.FLAT,
            bd=1,
            cursor="hand2",
            highlightbackground=EDITOR_THEME["border"],
        )
        self.fallback_template_open_button.grid(row=6, column=5, sticky="ew", pady=4)
        self.prefer_template_check = tk.Checkbutton(
            action_frame,
            text="优先模板识别：该步骤先做图片模板匹配，命中即完成；未命中再回退控件定位",
            variable=self.var_prefer_template,
            bg=EDITOR_THEME["panel"],
            activebackground=EDITOR_THEME["panel"],
            cursor="hand2",
        )
        self.prefer_template_check.grid(row=7, column=0, columnspan=6, sticky="w", pady=(6, 0))
        tk.Label(
            action_frame,
            text="上方模板图片：未勾选时仅作步骤失败后的兜底识别；勾选优先模板后，该步骤先做模板匹配，命中更快，未命中自动回退控件定位。",
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=980,
        ).grid(row=8, column=0, columnspan=6, sticky="w")
        # 展开/折叠前置判断（条件执行）
        self.precond_frame = tk.Frame(
            action_frame,
            bg=EDITOR_THEME["panel"],
            highlightthickness=1,
            highlightbackground=EDITOR_THEME["border"],
            padx=10,
            pady=10,
        )
        self.precond_frame.grid(row=9, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        self.precond_frame.columnconfigure(1, weight=1)
        self.precond_frame.columnconfigure(3, weight=1)
        tk.Label(
            self.precond_frame,
            text="展开/折叠前置判断（条件执行）",
            bg=EDITOR_THEME["panel"],
            fg=EDITOR_THEME["text"],
            font=self.font_card_title,
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(
            self.precond_frame,
            text="仅当关联控件满足指定切换态时才执行动作；不满足则跳过。用于幂等展开/折叠分区、避免重复勾选复选框。留空判断类型即不使用。",
            bg=EDITOR_THEME["panel"],
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=980,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 8))
        self.precond_condition_label = tk.Label(self.precond_frame, text="判断类型", bg=EDITOR_THEME["panel"])
        self.precond_condition_label.grid(row=2, column=0, sticky="w", pady=4)
        self.precond_condition_combo = ttk.Combobox(
            self.precond_frame,
            textvariable=self.var_precond_condition,
            values=("", "toggle", "checked", "visible"),
            state="readonly",
        )
        self.precond_condition_combo.grid(row=2, column=1, sticky="ew", padx=(8, 12), pady=4)
        self.precond_expected_label = tk.Label(self.precond_frame, text="目标态(on/off)", bg=EDITOR_THEME["panel"])
        self.precond_expected_label.grid(row=2, column=2, sticky="w", pady=4)
        self.precond_expected_combo = ttk.Combobox(
            self.precond_frame,
            textvariable=self.var_precond_expected,
            values=("off", "on"),
            state="readonly",
        )
        self.precond_expected_combo.grid(row=2, column=3, sticky="ew", padx=(8, 0), pady=4)
        self.precond_control_label = tk.Label(self.precond_frame, text="关联控件(留空=目标控件)", bg=EDITOR_THEME["panel"])
        self.precond_control_label.grid(row=3, column=0, sticky="w", pady=4)
        self.precond_control_entry = tk.Entry(self.precond_frame, textvariable=self.var_precond_control)
        self.precond_control_entry.grid(row=3, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4)
        self.relative_region_frame = tk.Frame(
            action_frame,
            bg=EDITOR_THEME["panel"],
            highlightthickness=1,
            highlightbackground=EDITOR_THEME["border"],
            padx=10,
            pady=10,
        )
        self.relative_region_frame.grid(row=10, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        self.relative_region_frame.columnconfigure(1, weight=1)
        self.relative_region_frame.columnconfigure(3, weight=1)
        tk.Label(
            self.relative_region_frame,
            text="父窗口 + 相对区域动作",
            bg=EDITOR_THEME["panel"],
            fg=EDITOR_THEME["text"],
            font=self.font_card_title,
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(
            self.relative_region_frame,
            text="适合 WPF 弹窗里拿不到真实控件的情况。先填父窗口，再录入相对区域比例，可用于点击或输入。",
            bg=EDITOR_THEME["panel"],
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=760,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 8))
        tk.Label(self.relative_region_frame, text="父窗口标题 *", bg=EDITOR_THEME["panel"]).grid(row=2, column=0, sticky="w", pady=4)
        tk.Entry(self.relative_region_frame, textvariable=self.var_relative_parent_title).grid(
            row=2, column=1, columnspan=3, sticky="ew", padx=(8, 0), pady=4
        )
        tk.Label(self.relative_region_frame, text="父窗口类名", bg=EDITOR_THEME["panel"]).grid(row=3, column=0, sticky="w", pady=4)
        tk.Entry(self.relative_region_frame, textvariable=self.var_relative_parent_class).grid(
            row=3, column=1, sticky="ew", padx=(8, 12), pady=4
        )
        tk.Label(self.relative_region_frame, text="框架类型", bg=EDITOR_THEME["panel"]).grid(row=3, column=2, sticky="w", pady=4)
        ttk.Combobox(
            self.relative_region_frame,
            textvariable=self.var_relative_parent_framework,
            values=(*ALLOWED_PARENT_WINDOW_FRAMEWORK_IDS, ""),
        ).grid(row=3, column=3, sticky="ew", padx=(8, 0), pady=4)
        tk.Label(self.relative_region_frame, text="区域 X(0-1) *", bg=EDITOR_THEME["panel"]).grid(row=4, column=0, sticky="w", pady=4)
        tk.Entry(self.relative_region_frame, textvariable=self.var_relative_region_x).grid(row=4, column=1, sticky="ew", padx=(8, 12), pady=4)
        tk.Label(self.relative_region_frame, text="区域 Y(0-1) *", bg=EDITOR_THEME["panel"]).grid(row=4, column=2, sticky="w", pady=4)
        tk.Entry(self.relative_region_frame, textvariable=self.var_relative_region_y).grid(row=4, column=3, sticky="ew", padx=(8, 0), pady=4)
        tk.Label(self.relative_region_frame, text="区域宽度 *", bg=EDITOR_THEME["panel"]).grid(row=5, column=0, sticky="w", pady=4)
        tk.Entry(self.relative_region_frame, textvariable=self.var_relative_region_width).grid(row=5, column=1, sticky="ew", padx=(8, 12), pady=4)
        tk.Label(self.relative_region_frame, text="区域高度 *", bg=EDITOR_THEME["panel"]).grid(row=5, column=2, sticky="w", pady=4)
        tk.Entry(self.relative_region_frame, textvariable=self.var_relative_region_height).grid(row=5, column=3, sticky="ew", padx=(8, 0), pady=4)
        tk.Label(self.relative_region_frame, text="点击锚点", bg=EDITOR_THEME["panel"]).grid(row=6, column=0, sticky="w", pady=4)
        ttk.Combobox(
            self.relative_region_frame,
            textvariable=self.var_relative_region_anchor,
            values=ALLOWED_RELATIVE_REGION_ANCHORS,
            state="readonly",
        ).grid(row=6, column=1, sticky="ew", padx=(8, 12), pady=4)
        tk.Label(
            self.relative_region_frame,
            text="建议先在总控台的“相对区域取点助手”里取比例，再回来粘到这里。",
            bg=EDITOR_THEME["panel"],
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=420,
        ).grid(row=6, column=2, columnspan=2, sticky="w", pady=4)
        self.post_input_keys_label = tk.Label(self.relative_region_frame, text="输入后按键", bg=EDITOR_THEME["panel"])
        self.post_input_keys_label.grid(row=7, column=0, sticky="w", pady=4)
        self.post_input_keys_combo = ttk.Combobox(
            self.relative_region_frame,
            textvariable=self.var_post_input_keys,
            values=("空", "{TAB}", "{ENTER}"),
            state="readonly",
        )
        self.post_input_keys_combo.grid(row=7, column=1, sticky="ew", padx=(8, 12), pady=4)
        self.require_blur_submit_check = tk.Checkbutton(
            self.relative_region_frame,
            text="该字段需要失焦提交",
            variable=self.var_require_blur_submit,
            command=self._on_require_blur_submit_changed,
            bg=EDITOR_THEME["panel"],
        )
        self.require_blur_submit_check.grid(row=7, column=2, columnspan=2, sticky="w", pady=4)
        relative_button_row = tk.Frame(self.relative_region_frame, bg=EDITOR_THEME["panel"])
        relative_button_row.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self._create_action_button(relative_button_row, "粘贴相对区域配置", self.cmd_import_relative_region_from_clipboard).pack(side=tk.LEFT, padx=(0, 6))
        self._create_action_button(relative_button_row, "清空相对区域", self.cmd_clear_relative_region_fields).pack(side=tk.LEFT)
        tk.Label(
            self.relative_region_frame,
            text="父窗口与相对区域预览",
            bg=EDITOR_THEME["panel"],
            fg=EDITOR_THEME["text"],
            font=self.font_card_title,
        ).grid(row=9, column=0, columnspan=4, sticky="w", pady=(10, 4))
        self.relative_region_preview_text = tk.Text(
            self.relative_region_frame,
            height=self._relative_region_preview_height,
            wrap=tk.WORD,
        )
        self.relative_region_preview_text.grid(row=10, column=0, columnspan=4, sticky="ew", pady=(0, 2))
        self._style_text_surface(self.relative_region_preview_text)
        self.relative_region_preview_resize_bar = tk.Label(
            self.relative_region_frame,
            text="拖动这里可上下调整预览高度，双击恢复默认",
            bg=EDITOR_THEME["panel_soft"],
            fg=EDITOR_THEME["muted"],
            relief=tk.GROOVE,
            bd=1,
            padx=8,
            pady=4,
            cursor="sb_v_double_arrow",
            anchor="center",
        )
        self.relative_region_preview_resize_bar.grid(row=11, column=0, columnspan=4, sticky="ew", pady=(0, 2))
        self.relative_region_preview_resize_bar.bind("<ButtonPress-1>", self._start_relative_region_preview_resize)
        self.relative_region_preview_resize_bar.bind("<B1-Motion>", self._drag_relative_region_preview_resize)
        self.relative_region_preview_resize_bar.bind("<ButtonRelease-1>", self._finish_relative_region_preview_resize)
        self.relative_region_preview_resize_bar.bind("<Double-Button-1>", self._reset_relative_region_preview_height)
        # 锚点相对点击的偏移量编辑区（与相对区域编辑区同一位置，按 action 切换显示）
        self.anchor_offset_frame = tk.Frame(
            action_frame,
            bg=EDITOR_THEME["panel"],
            highlightthickness=1,
            highlightbackground=EDITOR_THEME["border"],
            padx=10,
            pady=10,
        )
        self.anchor_offset_frame.grid(row=10, column=0, columnspan=6, sticky="ew", pady=(8, 0))
        self.anchor_offset_frame.columnconfigure(1, weight=1)
        self.anchor_offset_frame.columnconfigure(3, weight=1)
        tk.Label(
            self.anchor_offset_frame,
            text="锚点相对点击 · 偏移量",
            bg=EDITOR_THEME["panel"],
            fg=EDITOR_THEME["text"],
            font=self.font_card_title,
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        tk.Label(
            self.anchor_offset_frame,
            text="先定位目标控件作为锚点，选择对齐基准后按像素偏移点击（适合点控件内部的配置图标/最右侧按钮）。",
            bg=EDITOR_THEME["panel"],
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=760,
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(4, 8))
        tk.Label(self.anchor_offset_frame, text="对齐基准 *", bg=EDITOR_THEME["panel"]).grid(row=2, column=0, sticky="w", pady=4)
        anchor_align_combo = tk.ttk.Combobox(
            self.anchor_offset_frame,
            textvariable=self.var_anchor_align,
            values=("center", "left", "right", "top", "bottom"),
            state="readonly",
            width=12,
        )
        anchor_align_combo.grid(row=2, column=1, sticky="w", padx=(8, 12), pady=4)
        tk.Label(self.anchor_offset_frame, text="水平偏移 X(像素) *", bg=EDITOR_THEME["panel"]).grid(row=2, column=2, sticky="w", pady=4)
        tk.Entry(self.anchor_offset_frame, textvariable=self.var_anchor_offset_x).grid(
            row=2, column=3, sticky="ew", padx=(8, 12), pady=4
        )
        tk.Label(self.anchor_offset_frame, text="垂直偏移 Y(像素) *", bg=EDITOR_THEME["panel"]).grid(row=3, column=0, sticky="w", pady=4)
        tk.Entry(self.anchor_offset_frame, textvariable=self.var_anchor_offset_y).grid(
            row=3, column=1, sticky="ew", padx=(8, 12), pady=4
        )
        tk.Label(
            self.anchor_offset_frame,
            text="基准点 = 控件矩形按对齐方式取的边/角（center=中心，right=右边缘中点）；偏移 X 向右为正、向左为负，Y 向下为正、向上为负。",
            bg=EDITOR_THEME["panel"],
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=760,
        ).grid(row=4, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.anchor_offset_preview_text = tk.Text(
            self.anchor_offset_frame,
            height=self._relative_region_preview_height,
            wrap=tk.WORD,
        )
        self.anchor_offset_preview_text.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self._style_text_surface(self.anchor_offset_preview_text)
        tk.Label(
            action_frame,
            textvariable=self.action_schema_hint_var,
            fg=EDITOR_THEME["primary"],
            justify=tk.LEFT,
            anchor="w",
            wraplength=980,
        ).grid(row=9, column=0, columnspan=6, sticky="w", pady=(6, 0))
        self.action_type_combo.bind("<<ComboboxSelected>>", self._on_action_type_changed)
        self.action_combo.bind("<<ComboboxSelected>>", self._on_action_changed)
        self.target_control_combo.bind("<<ComboboxSelected>>", self._on_target_control_changed)
        self.continue_when_control_combo.bind("<<ComboboxSelected>>", self._on_continue_when_control_changed)
        self.post_input_keys_combo.bind("<<ComboboxSelected>>", self._on_post_input_keys_changed)

        inspect_card, inspect = self._create_form_card(
            parent,
            "3. 这一步关联哪个控件",
            "维护当前步骤的主要控件信息，并支持直接从控件库搜索关联。",
        )
        inspect_card.pack(fill=tk.X, pady=(10, 0))
        inspect.columnconfigure(1, weight=1)
        inspect.columnconfigure(3, weight=1)

        row = 0
        self._grid_label_entry(inspect, "控件名称 *", self.var_control_name, row, 0)
        self._grid_label_entry(inspect, "控件类型", self.var_control_type, row, 2)
        row += 1
        self._grid_label_entry(inspect, "AutomationId", self.var_automation_id, row, 0)
        self._grid_label_entry(inspect, "类名", self.var_class_name, row, 2)
        row += 1
        self._grid_label_entry(inspect, "UIPath", self.var_ui_path, row, 0)
        self._grid_label_entry(inspect, "模板Key", self.var_template_key, row, 2)
        row += 1
        inspect_action_row = tk.Frame(inspect)
        inspect_action_row.grid(row=row, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self._create_action_button(
            inspect_action_row,
            "控件库搜索匹配",
            self.cmd_match_control_from_control_map,
            tone="primary",
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Label(
            inspect_action_row,
            text="会按当前步骤的控件名称、AutomationId、类名等关键字去控件库中搜索，选中后可直接关联到本步骤。点击类步骤至少要让目标控件和细分控件中的定位信息完整。",
            fg="#666666",
            justify=tk.LEFT,
            anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        controls_card, controls_frame = self._create_form_card(
            parent,
            "4. 步骤下的细分控件清单",
            "这里维护这一步会用到的按钮、树节点、输入框和模板点位，动作目标会自动从这里联动。",
        )
        controls_card.pack(fill=tk.BOTH, expand=False, pady=(10, 0))
        tk.Label(
            controls_frame,
            text="先维护好这一步用到的控件，动作里的“目标控件”就可以直接下拉选择，流程搭建更顺畅。",
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
            bg=EDITOR_THEME["panel"],
        ).pack(fill=tk.X)

        controls_button_row = tk.Frame(controls_frame)
        controls_button_row.pack(fill=tk.X, pady=(8, 6))
        self._create_action_button(controls_button_row, "新增控件", self.cmd_add_control).pack(side=tk.LEFT, padx=2)
        self._create_action_button(controls_button_row, "编辑控件", self.cmd_edit_control).pack(side=tk.LEFT, padx=2)
        self._create_action_button(controls_button_row, "删除控件", self.cmd_delete_control, tone="danger").pack(side=tk.LEFT, padx=2)
        self._create_action_button(controls_button_row, "从剪贴板导入 Inspect", self.cmd_import_control_from_clipboard).pack(side=tk.LEFT, padx=2)
        self._create_action_button(controls_button_row, "半自动采集", self.cmd_open_semi_auto_collector, tone="primary").pack(side=tk.LEFT, padx=2)
        self._create_action_button(controls_button_row, "打开控件库", self._open_control_library, tone="primary").pack(side=tk.LEFT, padx=2)
        self._create_action_button(controls_button_row, "控件库采集", self.cmd_open_control_map_builder, tone="primary").pack(side=tk.LEFT, padx=2)
        self._create_action_button(controls_button_row, "控件定位检验", self.cmd_open_control_locator_tester, tone="primary").pack(side=tk.LEFT, padx=2)
        self._create_action_button(controls_button_row, "同步到步骤定位", self.cmd_sync_control_to_step_hints, tone="success").pack(side=tk.LEFT, padx=2)
        tk.Label(controls_button_row, textvariable=self.controls_summary_var, fg=EDITOR_THEME["muted"]).pack(side=tk.RIGHT)

        controls_tree_frame = tk.Frame(controls_frame)
        controls_tree_frame.pack(fill=tk.BOTH, expand=True)
        self.control_tree = ttk.Treeview(
            controls_tree_frame,
            columns=("seq", "name", "role", "locator"),
            show="headings",
            height=6,
        )
        self.control_tree.heading("seq", text="#")
        self.control_tree.heading("name", text="控件")
        self.control_tree.heading("role", text="用途")
        self.control_tree.heading("locator", text="定位")
        self.control_tree.column("seq", width=42, minwidth=42, stretch=False, anchor="center")
        self.control_tree.column("name", width=220, minwidth=140, stretch=False, anchor="w")
        self.control_tree.column("role", width=220, minwidth=140, stretch=False, anchor="w")
        self.control_tree.column("locator", width=420, minwidth=220, stretch=False, anchor="w")
        self.control_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.control_tree.bind("<Double-1>", lambda _event: self.cmd_edit_control())

        control_scrollbar = ttk.Scrollbar(controls_tree_frame, orient="vertical", command=self.control_tree.yview)
        control_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        control_h_scrollbar = ttk.Scrollbar(controls_frame, orient="horizontal", command=self.control_tree.xview)
        control_h_scrollbar.pack(fill=tk.X, pady=(6, 0))
        self.control_tree.configure(yscrollcommand=control_scrollbar.set, xscrollcommand=control_h_scrollbar.set)

        desc_card, desc_frame = self._create_form_card(
            parent,
            "5. 等待、判断、兜底和备注",
            "用于补充步骤说明、辅助判断、fallback 链路和实施备注。",
        )
        desc_card.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        desc_frame.columnconfigure(0, weight=1)
        desc_frame.columnconfigure(1, weight=1)

        tk.Label(desc_frame, text="步骤说明").grid(row=0, column=0, sticky="w")
        tk.Label(desc_frame, text="辅助判断（每行一条）").grid(row=0, column=1, sticky="w")
        self.description_text = tk.Text(desc_frame, height=6, wrap=tk.WORD)
        self.description_text.grid(row=1, column=0, sticky="nsew", padx=(0, 8), pady=(4, 8))
        self.aux_checks_text = tk.Text(desc_frame, height=6, wrap=tk.WORD)
        self.aux_checks_text.grid(row=1, column=1, sticky="nsew", pady=(4, 8))

        tk.Label(desc_frame, text="兜底链路（每行一条）").grid(row=2, column=0, sticky="w")
        tk.Label(desc_frame, text="备注").grid(row=2, column=1, sticky="w")
        self.fallbacks_text = tk.Text(desc_frame, height=6, wrap=tk.WORD)
        self.fallbacks_text.grid(row=3, column=0, sticky="nsew", padx=(0, 8), pady=(4, 0))
        self.notes_text = tk.Text(desc_frame, height=6, wrap=tk.WORD)
        self.notes_text.grid(row=3, column=1, sticky="nsew", pady=(4, 0))
        for widget in (self.description_text, self.aux_checks_text, self.fallbacks_text, self.notes_text):
            self._style_text_surface(widget)

        desc_frame.rowconfigure(1, weight=1)
        desc_frame.rowconfigure(3, weight=1)

        advanced_card, advanced_frame = self._create_form_card(
            parent,
            "高级配置",
            "仅在可视化字段不够用时再编辑，避免把日常配置重新退回到纯 JSON。",
        )
        advanced_card.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        advanced_frame.columnconfigure(0, weight=1)
        advanced_frame.columnconfigure(1, weight=1)
        tk.Label(
            advanced_frame,
            text="左侧是步骤参数，右侧是完整 Action JSON。你平时只配上面的可视化字段，这里主要用于补充特殊参数。",
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(advanced_frame, text="步骤参数（JSON）").grid(row=1, column=0, sticky="w", pady=(8, 0))
        tk.Label(advanced_frame, text="Action 配置（JSON）").grid(row=1, column=1, sticky="w", pady=(8, 0))
        self.step_params_text = tk.Text(advanced_frame, height=7, wrap=tk.WORD)
        self.step_params_text.grid(row=2, column=0, sticky="nsew", padx=(0, 8), pady=(4, 0))
        self.action_config_text = tk.Text(advanced_frame, height=7, wrap=tk.WORD)
        self.action_config_text.grid(row=2, column=1, sticky="nsew", pady=(4, 0))
        self._style_text_surface(self.step_params_text)
        self._style_text_surface(self.action_config_text)
        advanced_frame.rowconfigure(2, weight=1)

    # ================================================================
    # AI 助手 Tab
    # ================================================================

    def _build_ai_tab(self, parent):
        """构建 AI 助手 Tab：自然语言 → 步骤转换 + 插入流程。"""
        ai_card, ai_body = self._create_form_card(
            parent,
            "AI 助手 — 自然语言指令转自动化步骤",
            "在下方输入 RPA 操作指令，由 AI Agent 将其转换为可执行的自动化步骤，并可直接插入当前流程。",
            tone="primary",
        )
        ai_card.pack(fill=tk.X)

        # — LLM 配置区 —
        cfg_frame = tk.LabelFrame(ai_body, text="LLM 连接配置", padx=10, pady=8)
        cfg_frame.pack(fill=tk.X, pady=(0, 10))

        row1 = tk.Frame(cfg_frame, bg=EDITOR_THEME["panel"])
        row1.pack(fill=tk.X, pady=2)
        tk.Label(row1, text="Base URL", width=12, anchor="w", bg=EDITOR_THEME["panel"]).pack(side=tk.LEFT)
        self.ai_var_base_url = tk.StringVar()
        tk.Entry(row1, textvariable=self.ai_var_base_url, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True)

        row2 = tk.Frame(cfg_frame, bg=EDITOR_THEME["panel"])
        row2.pack(fill=tk.X, pady=2)
        tk.Label(row2, text="API Key", width=12, anchor="w", bg=EDITOR_THEME["panel"]).pack(side=tk.LEFT)
        self.ai_var_api_key = tk.StringVar()
        self.ai_entry_api_key = tk.Entry(row2, textvariable=self.ai_var_api_key, width=60, show="*")
        self.ai_entry_api_key.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.ai_show_key_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            row2, text="显示", variable=self.ai_show_key_var,
            command=self._toggle_ai_key_visibility, bg=EDITOR_THEME["panel"],
        ).pack(side=tk.LEFT, padx=4)

        row3 = tk.Frame(cfg_frame, bg=EDITOR_THEME["panel"])
        row3.pack(fill=tk.X, pady=2)
        tk.Label(row3, text="模型", width=12, anchor="w", bg=EDITOR_THEME["panel"]).pack(side=tk.LEFT)
        self.ai_var_model = tk.StringVar(value="gpt-4o")
        tk.Entry(row3, textvariable=self.ai_var_model, width=60).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # 状态 + 按钮
        btn_cfg_row = tk.Frame(cfg_frame, bg=EDITOR_THEME["panel"])
        btn_cfg_row.pack(fill=tk.X, pady=(6, 0))
        self.ai_status_label = tk.Label(btn_cfg_row, text="● 未测试", fg=EDITOR_THEME["muted"], bg=EDITOR_THEME["panel"])
        self.ai_status_label.pack(side=tk.LEFT)
        self._create_action_button(btn_cfg_row, "测试连接", self.cmd_ai_test_connection).pack(side=tk.RIGHT, padx=4)
        self._create_action_button(btn_cfg_row, "保存配置", self._save_ai_config).pack(side=tk.RIGHT, padx=4)

        # — 指令输入区 —
        input_frame = tk.LabelFrame(ai_body, text="自然语言指令", padx=10, pady=8)
        input_frame.pack(fill=tk.X, pady=(0, 10))

        # 模式切换
        mode_row = tk.Frame(input_frame, bg=EDITOR_THEME["panel"])
        mode_row.pack(fill=tk.X, pady=(0, 6))
        self.ai_mode_var = tk.StringVar(value="sequence")
        for text, val in [("对话", "chat"), ("单步转换", "step"), ("序列转换", "sequence")]:
            tk.Radiobutton(
                mode_row, text=text, variable=self.ai_mode_var, value=val,
                bg=EDITOR_THEME["panel"], font=("Microsoft YaHei UI", 9),
            ).pack(side=tk.LEFT, padx=2)

        self.ai_input_text = tk.Text(input_frame, height=4, wrap=tk.WORD, font=("Microsoft YaHei UI", 10))
        self.ai_input_text.pack(fill=tk.X)
        self._style_text_surface(self.ai_input_text)

        btn_row = tk.Frame(input_frame, bg=EDITOR_THEME["panel"])
        btn_row.pack(fill=tk.X, pady=(6, 0))
        self._create_action_button(btn_row, "转换", self.cmd_ai_convert, tone="primary").pack(side=tk.LEFT, padx=4)
        self._create_action_button(btn_row, "清空", self._clear_ai_input).pack(side=tk.LEFT, padx=4)

        # — 结果展示区 —
        result_frame = tk.LabelFrame(ai_body, text="转换结果", padx=10, pady=8)
        result_frame.pack(fill=tk.BOTH, expand=True)

        self.ai_result_text = tk.Text(result_frame, height=12, wrap=tk.WORD, font=("Microsoft YaHei UI", 9))
        self.ai_result_text.pack(fill=tk.BOTH, expand=True)
        self._style_text_surface(self.ai_result_text)

        self.ai_result_steps = []  # 存储转换后的步骤

        btn_result_row = tk.Frame(result_frame, bg=EDITOR_THEME["panel"])
        btn_result_row.pack(fill=tk.X, pady=(6, 0))
        self.ai_insert_btn = tk.Button(
            btn_result_row, text="插入生成的步骤到流程",
            command=self.cmd_ai_insert_steps,
            bg=EDITOR_THEME["success_soft"], fg="#166534",
            relief=tk.FLAT, padx=10, pady=5, cursor="hand2",
            font=("Microsoft YaHei UI", 9), state=tk.DISABLED,
        )
        self.ai_insert_btn.pack(side=tk.LEFT, padx=4)
        self.ai_result_count_label = tk.Label(btn_result_row, text="", fg=EDITOR_THEME["muted"], bg=EDITOR_THEME["panel"])
        self.ai_result_count_label.pack(side=tk.LEFT, padx=4)

        # 加载已保存的 AI 配置
        self._load_ai_config()

    # — AI 助手回调 —

    def _toggle_ai_key_visibility(self):
        show = self.ai_show_key_var.get()
        self.ai_entry_api_key.config(show="" if show else "*")

    def _get_ai_config_dict(self):
        return {
            "base_url": self.ai_var_base_url.get().strip(),
            "api_key": self.ai_var_api_key.get().strip(),
            "model": self.ai_var_model.get().strip() or "gpt-4o",
        }

    def _save_ai_config(self):
        cfg = self._get_ai_config_dict()
        self.flow_definition["aiAgentConfig"] = cfg
        self._mark_dirty("AI 配置已保存到链路文件")
        messagebox.showinfo("AI 配置", "配置已保存到当前链路文件的 aiAgentConfig 字段。")

    def _load_ai_config(self):
        cfg = self.flow_definition.get("aiAgentConfig", {})
        if isinstance(cfg, dict):
            self.ai_var_base_url.set(cfg.get("base_url", ""))
            self.ai_var_api_key.set(cfg.get("api_key", ""))
            self.ai_var_model.set(cfg.get("model", "gpt-4o"))

    def cmd_open_ai_tab(self):
        self._notebook.select(self._tab_ai_index)

    def cmd_ai_test_connection(self):
        cfg = self._get_ai_config_dict()
        if not cfg["base_url"] or not cfg["api_key"]:
            messagebox.showerror("配置不完整", "请先填写 Base URL 和 API Key。")
            return
        self.ai_status_label.config(text="⏳ 测试中...", fg=EDITOR_THEME["muted"])
        self.root.update()
        try:
            agent = DslAgent(DslAgentConfig(**cfg))
            ok = agent.test_connection()
            if ok:
                self.ai_status_label.config(text="● 已连接", fg="#16a34a")
                messagebox.showinfo("连接成功", "LLM API 连接正常！")
            else:
                self.ai_status_label.config(text="● 连接失败", fg="#dc2626")
                messagebox.showerror("连接失败", "API 返回异常，请检查配置。")
        except Exception as e:
            self.ai_status_label.config(text="● 连接失败", fg="#dc2626")
            messagebox.showerror("连接失败", f"{e}")

    def cmd_ai_convert(self):
        nl_text = self.ai_input_text.get("1.0", "end-1c").strip()
        if not nl_text:
            messagebox.showwarning("输入为空", "请输入自然语言指令。")
            return

        cfg = self._get_ai_config_dict()
        if not cfg["base_url"] or not cfg["api_key"]:
            messagebox.showerror("配置不完整", "请先填写 Base URL 和 API Key。")
            return

        self.ai_result_text.delete("1.0", tk.END)
        self.ai_result_text.insert(tk.END, "⏳ 正在调用 AI Agent 转换...")
        self.ai_insert_btn.config(state=tk.DISABLED)
        self.root.update()

        try:
            agent = DslAgent(DslAgentConfig(**cfg))

            # 构建上下文
            skill_text = load_all_skills_text()
            control_text = ""
            source_path = self.definition_path if self.definition_path and os.path.exists(self.definition_path) else ""
            if source_path:
                control_text = build_index_from_json(source_path)
            context = build_context_for_agent(
                control_index_text=control_text,
                flow_path=source_path,
                project_description="Meteodyn WT 风资源仿真软件 RPA 自动化",
                skill_text=skill_text,
            )

            mode = self.ai_mode_var.get()
            if mode == "chat":
                reply = agent.chat(nl_text, context)
                self.ai_result_text.delete("1.0", tk.END)
                self.ai_result_text.insert(tk.END, reply)
                self.ai_result_steps = []
                self.ai_insert_btn.config(state=tk.DISABLED)
                self.ai_result_count_label.config(text="")
            else:
                if mode == "step":
                    steps = agent.nl_to_step(nl_text, context)
                else:
                    steps = agent.nl_to_sequence(nl_text, context)

                self.ai_result_text.delete("1.0", tk.END)
                self.ai_result_text.insert(tk.END, json.dumps(steps, ensure_ascii=False, indent=2))
                self.ai_result_steps = steps
                self.ai_insert_btn.config(state=tk.NORMAL)
                self.ai_result_count_label.config(text=f"共 {len(steps)} 个步骤")

        except Exception as e:
            self.ai_result_text.delete("1.0", tk.END)
            self.ai_result_text.insert(tk.END, f"转换失败: {e}")
            self.ai_result_steps = []
            self.ai_insert_btn.config(state=tk.DISABLED)
            self.ai_result_count_label.config(text="")

    def cmd_ai_insert_steps(self):
        if not self.ai_result_steps:
            messagebox.showwarning("无步骤", "没有可插入的步骤，请先执行转换。")
            return

        insert_at = self.selected_index + 1 if self.selected_index is not None else len(self.steps)
        inserted = 0
        for step_data in self.ai_result_steps:
            step_payload = json.loads(json.dumps(step_data, ensure_ascii=False))
            step_payload.setdefault("topLevel", True)
            step_payload = normalize_step(step_payload, insert_at + inserted)
            self.steps.insert(insert_at + inserted, step_payload)
            inserted += 1

        self._mark_dirty(f"AI 助手：已插入 {inserted} 个步骤")
        self._refresh_steps_tree()
        self._refresh_overview()
        self._select_step(insert_at)
        self._notebook.select(0)  # 切回步骤编辑 Tab
        messagebox.showinfo("插入成功", f"已将 {inserted} 个步骤插入到流程中。")

    def _clear_ai_input(self):
        self.ai_input_text.delete("1.0", tk.END)
        self.ai_result_text.delete("1.0", tk.END)
        self.ai_result_steps = []
        self.ai_insert_btn.config(state=tk.DISABLED)
        self.ai_result_count_label.config(text="")

    def _build_packages_tab(self, parent):
        package_frame = tk.LabelFrame(parent, text="流程包管理", padx=10, pady=10)
        package_frame.pack(fill=tk.X)
        tk.Label(
            package_frame,
            text=(
                "用于复用步骤包。可视化维护后，flow_ref 步骤可直接引用这里的流程包。\n"
                f"流程包会在保存时自动同步到固定目录：{FLOW_PACKAGE_REGISTRY_FILE}"
            ),
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X)

        package_button_row = tk.Frame(package_frame)
        package_button_row.pack(fill=tk.X, pady=(8, 6))
        self._create_action_button(package_button_row, "新增流程包", self.cmd_add_flow_package).pack(side=tk.LEFT, padx=2)
        self._create_action_button(package_button_row, "编辑流程包", self.cmd_edit_flow_package).pack(side=tk.LEFT, padx=2)
        self._create_action_button(package_button_row, "删除流程包", self.cmd_delete_flow_package, tone="danger").pack(side=tk.LEFT, padx=2)
        self._create_action_button(package_button_row, "刷新流程包", self.cmd_reload_flow_packages_from_registry).pack(side=tk.LEFT, padx=2)
        self._create_action_button(package_button_row, "定位包内步骤", self.cmd_focus_flow_package_steps, tone="primary").pack(side=tk.LEFT, padx=2)
        self._create_action_button(package_button_row, "↑", self.cmd_move_flow_package_up).pack(side=tk.LEFT, padx=2)
        self._create_action_button(package_button_row, "↓", self.cmd_move_flow_package_down).pack(side=tk.LEFT, padx=2)

        package_tree_frame = tk.Frame(package_frame)
        package_tree_frame.pack(fill=tk.X, pady=(8, 0))
        self.package_tree = ttk.Treeview(package_tree_frame, columns=("id", "name", "steps"), show="headings", height=6)
        self.package_tree.heading("id", text="ID")
        self.package_tree.heading("name", text="流程包")
        self.package_tree.heading("steps", text="步骤数")
        self.package_tree.column("id", width=180, minwidth=120, stretch=False, anchor="w")
        self.package_tree.column("name", width=320, minwidth=180, stretch=False, anchor="w")
        self.package_tree.column("steps", width=90, minwidth=70, stretch=False, anchor="center")
        self.package_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.package_tree.bind("<<TreeviewSelect>>", self._on_package_tree_select)
        self.package_tree.bind("<Double-1>", lambda _event: self.cmd_edit_flow_package())

        package_scrollbar = ttk.Scrollbar(package_tree_frame, orient="vertical", command=self.package_tree.yview)
        package_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        package_h_scrollbar = ttk.Scrollbar(package_frame, orient="horizontal", command=self.package_tree.xview)
        package_h_scrollbar.pack(fill=tk.X, pady=(6, 0))
        self.package_tree.configure(yscrollcommand=package_scrollbar.set, xscrollcommand=package_h_scrollbar.set)

        self.package_preview_text = tk.Text(package_frame, height=5, wrap=tk.WORD)
        self._style_text_surface(self.package_preview_text)
        self.package_preview_text.pack(fill=tk.X, pady=(8, 0))

    def _build_config_tab(self, parent):
        runtime_frame = tk.LabelFrame(parent, text="运行参数", padx=10, pady=10)
        runtime_frame.pack(fill=tk.X)
        runtime_frame.columnconfigure(1, weight=1)

        self._grid_label_entry(runtime_frame, "WT 程序", self.runtime_gm_exe_var, 0, 0)
        self._grid_label_entry(runtime_frame, "源文件", self.runtime_source_file_var, 1, 0)
        self._grid_label_entry(runtime_frame, "输出目录", self.runtime_output_dir_var, 2, 0)
        self._grid_label_entry(runtime_frame, "投影文件", self.runtime_projection_file_var, 3, 0)
        tk.Label(
            runtime_frame,
            text="保存 flow_definition.json 后，执行脚本会优先使用这里的路径配置覆盖硬编码与 resource 默认值。",
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

        summary_frame = tk.LabelFrame(parent, text="流程链路总览", padx=10, pady=10)
        summary_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))

        self.overview_text = tk.Text(
            summary_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 10),
        )
        self._style_text_surface(self.overview_text, dark=True)
        self.overview_text.pack(fill=tk.BOTH, expand=True)

    def _build_help_tab(self, parent):
        template_frame = tk.LabelFrame(parent, text="步骤模板库", padx=10, pady=10)
        template_frame.pack(fill=tk.X)
        tk.Label(
            template_frame,
            text="把常见动作封成模板。可直接插入新步骤，或套用到当前步骤。",
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X)
        template_list_frame = tk.Frame(template_frame)
        template_list_frame.pack(fill=tk.X, pady=(8, 0))
        self.template_listbox = tk.Listbox(template_list_frame, height=7, exportselection=False)
        self.template_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.template_listbox.bind("<<ListboxSelect>>", self._on_template_select)
        template_scrollbar = ttk.Scrollbar(template_list_frame, orient="vertical", command=self.template_listbox.yview)
        template_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.template_listbox.configure(yscrollcommand=template_scrollbar.set)
        self.template_preview_text = tk.Text(template_frame, height=6, wrap=tk.WORD)
        self._style_text_surface(self.template_preview_text)
        self.template_preview_text.pack(fill=tk.X, pady=(6, 0))
        tk.Label(template_frame, textvariable=self.template_summary_var, fg=EDITOR_THEME["muted"]).pack(anchor="w", pady=(6, 0))
        template_button_row = tk.Frame(template_frame)
        template_button_row.pack(fill=tk.X, pady=(8, 0))
        self._create_action_button(template_button_row, "插入为新步骤", self.cmd_insert_step_template, tone="primary").pack(side=tk.LEFT, padx=2)
        self._create_action_button(template_button_row, "套用到当前步骤", self.cmd_apply_step_template, tone="success").pack(side=tk.LEFT, padx=2)

        # 控件库入口
        control_lib_frame = tk.LabelFrame(parent, text="控件库管理", padx=10, pady=10)
        control_lib_frame.pack(fill=tk.X, pady=(10, 0))
        tk.Label(
            control_lib_frame,
            text="从已采集的控件库中选择控件，支持树形结构展示、编辑和删除。",
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X)
        control_lib_button_row = tk.Frame(control_lib_frame)
        control_lib_button_row.pack(fill=tk.X, pady=(8, 0))
        tk.Button(
            control_lib_button_row,
            text="打开控件库",
            command=self._open_control_library,
            bg="#dbeafe",
            width=20,
        ).pack(side=tk.LEFT, padx=2)
        tk.Button(
            control_lib_button_row,
            text="打开控件库采集器",
            command=self._open_control_map_builder,
            bg="#e0e7ff",
            width=20,
        ).pack(side=tk.LEFT, padx=2)

        authoring_frame = tk.LabelFrame(parent, text="步骤填写规范", padx=10, pady=10)
        authoring_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        tk.Label(
            authoring_frame,
            text="下面这份规范对应“模板新增”的默认思路。后续新增步骤时，优先按这里的字段组合填写。",
            fg=EDITOR_THEME["muted"],
            justify=tk.LEFT,
            anchor="w",
        ).pack(fill=tk.X)
        self.step_authoring_guide_text = tk.Text(authoring_frame, wrap=tk.WORD, height=18, font=self.font_help_text)
        self._style_text_surface(self.step_authoring_guide_text)
        self.step_authoring_guide_text.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.step_authoring_guide_text.insert("1.0", STEP_AUTHORING_GUIDE)
        self.step_authoring_guide_text.config(state=tk.DISABLED)

        help_frame = tk.LabelFrame(parent, text="使用提示", padx=10, pady=10)
        help_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        help_text = (
            "1. 左侧选择步骤，“步骤编辑”标签页编辑参数。\n"
            "2. 每个主步骤下都可以维护多个细分控件，用于按钮、树节点、模板点位等。\n"
            "3. 单个控件可用“从剪贴板导入 Inspect”，多个控件建议用“半自动采集”连续抓取。\n"
            "4. 半自动采集会监听剪贴板变化，适合在 Inspect 中连续复制多个控件信息。\n"
            "5. 步骤参数建议写 JSON；运行参数用于文件路径、输出目录、投影文件等可编辑输入。\n"
            "6. action 类型步骤使用 Action 配置；flow_ref 类型步骤通过 流程包引用 调用流程包。\n"
            "7. 流程包适合沉淀通用准备、导出、清理等固定流程块。\n"
            "8. 多重辅助判断建议一行一条，便于后续转为代码判断。\n"
            "9. “流程包管理”标签页用于统一维护可复用的流程包。"
        )
        help_text_widget = tk.Text(help_frame, wrap=tk.WORD, height=16, bg="#f8f9fa", fg="#212529", font=self.font_help_text)
        self._style_text_surface(help_text_widget)
        help_text_widget.pack(fill=tk.BOTH, expand=True)
        help_text_widget.insert("1.0", help_text)
        help_text_widget.config(state=tk.DISABLED)

    def _load_runtime_config_into_form(self, runtime_config):
        runtime_config = normalize_runtime_config(runtime_config)
        self.runtime_gm_exe_var.set(runtime_config.get("gmExe", ""))
        self.runtime_source_file_var.set(runtime_config.get("sourceFilePath", ""))
        self.runtime_output_dir_var.set(runtime_config.get("outputDir", ""))
        self.runtime_projection_file_var.set(runtime_config.get("projectionFilePath", ""))

    def _build_runtime_config_from_form(self):
        return normalize_runtime_config(
            {
                "gmExe": self.runtime_gm_exe_var.get().strip(),
                "sourceFilePath": self.runtime_source_file_var.get().strip(),
                "outputDir": self.runtime_output_dir_var.get().strip(),
                "projectionFilePath": self.runtime_projection_file_var.get().strip(),
            }
        )

    def _load_flow_packages_into_form(self, flow_packages):
        self.flow_packages = normalize_flow_packages(flow_packages)
        self._refresh_flow_packages_view()

    def _build_flow_packages_from_form(self):
        return normalize_flow_packages(self.flow_packages)

    def _load_flow_package_registry_payload(self):
        payload = load_json_file(FLOW_PACKAGE_REGISTRY_FILE)
        if not isinstance(payload, dict):
            return None
        payload["flowPackages"] = normalize_flow_packages(payload.get("flowPackages", []))
        payload["steps"] = [normalize_step(step, index) for index, step in enumerate(payload.get("steps", []))]
        return payload

    def _get_flow_package_dialog_available_steps(self):
        available_steps = []
        seen_step_ids = set()
        for step in self.steps:
            step_id = str(step.get("id", "")).strip()
            if not step_id or step_id in seen_step_ids:
                continue
            available_steps.append(step)
            seen_step_ids.add(step_id)
        registry_payload = self._load_flow_package_registry_payload()
        registry_steps = registry_payload.get("steps", []) if isinstance(registry_payload, dict) else []
        for step in registry_steps:
            step_id = str(step.get("id", "")).strip()
            if not step_id or step_id in seen_step_ids:
                continue
            available_steps.append(step)
            seen_step_ids.add(step_id)
        return available_steps

    def _import_missing_registry_steps_for_packages(self, registry_payload, target_packages):
        if not isinstance(registry_payload, dict):
            return 0
        registry_steps = registry_payload.get("steps", [])
        if not isinstance(registry_steps, list):
            return 0
        target_step_ids = set(collect_flow_package_step_ids(target_packages))
        existing_step_ids = {str(step.get("id", "")).strip() for step in self.steps if str(step.get("id", "")).strip()}
        imported_count = 0
        for step in registry_steps:
            step_id = str(step.get("id", "")).strip()
            if not step_id or step_id in existing_step_ids or step_id not in target_step_ids:
                continue
            step_payload = json.loads(json.dumps(step, ensure_ascii=False))
            step_payload["topLevel"] = False
            self.steps.append(normalize_step(step_payload, len(self.steps)))
            existing_step_ids.add(step_id)
            imported_count += 1
        return imported_count

    def _format_json_text(self, data):
        if not data:
            return "{}"
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _format_json_list_text(self, data):
        if not data:
            return "[]"
        return json.dumps(data, ensure_ascii=False, indent=2)

    def _get_package_ref_choices(self):
        return [""] + [package.get("id", "") for package in self.flow_packages if package.get("id", "")]

    def _refresh_package_ref_choices(self):
        if hasattr(self, "package_ref_combo"):
            self.package_ref_combo["values"] = self._get_package_ref_choices()
            current_value = self.var_package_ref.get().strip()
            if current_value and current_value not in self.package_ref_combo["values"]:
                self.var_package_ref.set("")

    def _build_step_package_names_map(self):
        package_names_map = {}
        for package in self.flow_packages:
            package_name = str(package.get("name", "")).strip() or str(package.get("id", "")).strip() or "未命名流程包"
            for item in package.get("stepIds", []):
                step_id = str(item).strip()
                if not step_id:
                    continue
                package_names_map.setdefault(step_id, [])
                if package_name not in package_names_map[step_id]:
                    package_names_map[step_id].append(package_name)
        return package_names_map

    def _refresh_flow_packages_view(self):
        self._refresh_package_ref_choices()
        if hasattr(self, "package_tree"):
            self.package_tree.delete(*self.package_tree.get_children())
            selected_package_iid = None
            for index, package in enumerate(self.flow_packages):
                if str(package.get("id", "")).strip() == self.current_package_step_filter_id:
                    selected_package_iid = str(index)
                self.package_tree.insert(
                    "",
                    tk.END,
                    iid=str(index),
                    values=(package.get("id", ""), package.get("name", ""), len(package.get("stepIds", []))),
                )
            if selected_package_iid is not None:
                self.package_tree.selection_set(selected_package_iid)
        if hasattr(self, "package_preview_text"):
            self.package_preview_text.delete("1.0", tk.END)
            if self.flow_packages:
                lines = []
                for package in self.flow_packages:
                    lines.append(
                        f"{package.get('id', '')} | {package.get('name', '')} | steps={', '.join(package.get('stepIds', [])) or '未配置'}"
                    )
                    if package.get("description"):
                        lines.append(f"  {package.get('description', '')}")
                self.package_preview_text.insert("1.0", "\n".join(lines))
            else:
                self.package_preview_text.insert("1.0", "当前还没有流程包。")
        # 同步更新左侧快捷流程包预览
        if hasattr(self, "package_quick_text"):
            self.package_quick_text.delete("1.0", tk.END)
            if self.flow_packages:
                lines = []
                for package in self.flow_packages:
                    lines.append(f"[{package.get('id', '')}] {package.get('name', '')}")
                    lines.append(f"  步骤: {', '.join(package.get('stepIds', [])) or '-'}")
                self.package_quick_text.insert("1.0", "\n".join(lines))
            else:
                self.package_quick_text.insert("1.0", "暂无流程包")
        self._update_step_scope_label()

    def cmd_reload_flow_packages_from_registry(self):
        registry_payload = self._load_flow_package_registry_payload()
        if not isinstance(registry_payload, dict):
            messagebox.showinfo("提示", f"未找到流程包仓库文件：\n{FLOW_PACKAGE_REGISTRY_FILE}")
            return
        registry_packages = normalize_flow_packages(registry_payload.get("flowPackages", []))
        if not registry_packages:
            messagebox.showinfo("提示", "流程包仓库里还没有可加载的历史流程包。")
            return
        if self.dirty and self.flow_packages:
            should_continue = messagebox.askyesno(
                "确认刷新流程包",
                "从流程包仓库刷新会覆盖当前内存中的流程包列表。\n"
                "如果这些修改尚未保存到链路文件，可能会丢失。\n"
                "确定继续吗？",
            )
            if not should_continue:
                return
        self.flow_packages = registry_packages
        self.flow_definition["flowPackages"] = normalize_flow_packages(registry_packages)
        imported_step_count = self._import_missing_registry_steps_for_packages(registry_payload, registry_packages)
        self.flow_definition["steps"] = [normalize_step(step, index) for index, step in enumerate(self.steps)]
        self._refresh_flow_packages_view()
        self._refresh_steps_tree()
        self._refresh_overview()
        if self.selected_index is not None:
            self._load_step_into_form(self.steps[self.selected_index])
        source_definition_path = str(registry_payload.get("sourceDefinitionPath", "")).strip()
        source_text = f" | 来源：{source_definition_path}" if source_definition_path else ""
        step_text = f" | 同步步骤 {imported_step_count} 个" if imported_step_count else ""
        self.status_var.set(f"已从流程包仓库刷新 {len(registry_packages)} 个流程包{step_text}{source_text}")

    def _get_selected_package_index(self):
        if not hasattr(self, "package_tree"):
            return None
        selection = self.package_tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _get_package_by_id(self, package_id):
        normalized_package_id = str(package_id or "").strip()
        if not normalized_package_id:
            return None
        for package in self.flow_packages:
            if str(package.get("id", "")).strip() == normalized_package_id:
                return package
        return None

    def _update_step_scope_label(self):
        package = self._get_package_by_id(self.current_package_step_filter_id)
        if package:
            package_name = str(package.get("name", "")).strip() or str(package.get("id", "")).strip()
            self.step_scope_var.set(f"当前显示：流程包 {package_name}")
        else:
            self.current_package_step_filter_id = ""
            self.step_scope_var.set("当前显示：全部步骤")

    def _get_visible_step_indexes(self):
        package = self._get_package_by_id(self.current_package_step_filter_id)
        if not package:
            candidate_indexes = list(range(len(self.steps)))
        else:
            step_index_map = {
                str(step.get("id", "")).strip(): index
                for index, step in enumerate(self.steps)
                if str(step.get("id", "")).strip()
            }
            candidate_indexes = []
            seen_indexes = set()
            for step_id in package.get("stepIds", []):
                normalized_step_id = str(step_id).strip()
                if normalized_step_id in step_index_map:
                    idx = step_index_map[normalized_step_id]
                    if idx not in seen_indexes:  # 去重，避免同一步骤被重复插入导致 iid 冲突
                        candidate_indexes.append(idx)
                        seen_indexes.add(idx)

        # 步骤搜索与分类筛选过滤
        search_query = self.step_search_query.get().strip().lower() if hasattr(self, "step_search_query") else ""
        category = self.step_filter_category.get().strip() if hasattr(self, "step_filter_category") else "全部"

        if not search_query and category in ("", "全部"):
            return candidate_indexes

        filtered = []
        for idx in candidate_indexes:
            if idx >= len(self.steps):
                continue
            step = self.steps[idx]
            # 类别过滤
            if category == "仅启用" and not step.get("enabled", True):
                continue
            elif category == "仅停用" and step.get("enabled", True):
                continue
            elif category == "仅动作步" and str(step.get("actionType", "")).strip().lower() not in ("action", "click", "input", "launch"):
                continue
            elif category == "仅子链路":
                act_type = str(step.get("actionType", "")).strip().lower()
                has_subflow = bool(step.get("subflow") or (isinstance(step.get("actionConfig"), dict) and step["actionConfig"].get("subflow")))
                if act_type not in ("flow_ref", "subflow") and not has_subflow:
                    continue
            elif category == "带模板":
                act_cfg = step.get("actionConfig", {}) if isinstance(step.get("actionConfig"), dict) else {}
                raw_act = step.get("action", {}) if isinstance(step.get("action"), dict) else {}
                has_tpl = bool(
                    act_cfg.get("fallbackTemplate")
                    or act_cfg.get("preferTemplate")
                    or act_cfg.get("image_template")
                    or raw_act.get("image_template")
                    or step.get("template")
                    or step.get("template_path")
                )
                if not has_tpl:
                    continue

            # 搜索词过滤
            if search_query:
                s_id = str(step.get("id", "")).lower()
                s_name = str(step.get("name", "")).lower()
                s_window = str(step.get("windowTitle", "")).lower()
                act_cfg = step.get("actionConfig", {}) if isinstance(step.get("actionConfig"), dict) else {}
                s_action = str(act_cfg.get("action", "")).lower()
                s_ctrl = str(act_cfg.get("controlId", "")).lower()
                s_stage = str(step.get("stage", "")).lower()
                matched = (
                    search_query in s_id
                    or search_query in s_name
                    or search_query in s_window
                    or search_query in s_action
                    or search_query in s_ctrl
                    or search_query in s_stage
                )
                if not matched:
                    continue

            filtered.append(idx)
        return filtered

    def _apply_package_step_filter(self, package_id, focus_first=True):
        self.current_package_step_filter_id = str(package_id or "").strip()
        self._update_step_scope_label()
        self._refresh_steps_tree()
        visible_indexes = self._get_visible_step_indexes()
        if not visible_indexes:
            self.status_var.set("当前流程包下没有可显示的步骤。")
            return
        if self.selected_index in visible_indexes:
            self._select_step(self.selected_index)
            return
        if focus_first:
            self._select_step(visible_indexes[0])

    def cmd_clear_step_package_filter(self):
        self.current_package_step_filter_id = ""
        self._update_step_scope_label()
        self._refresh_steps_tree()
        if self.selected_index is not None and 0 <= self.selected_index < len(self.steps):
            self._select_step(self.selected_index)
        elif self.steps:
            self._select_step(0)

    def _on_package_tree_select(self, _event=None):
        package_index = self._get_selected_package_index()
        if package_index is None or not (0 <= package_index < len(self.flow_packages)):
            return
        package = self.flow_packages[package_index]
        self._apply_package_step_filter(package.get("id", ""))

    def _focus_step_by_id(self, step_id):
        normalized_step_id = str(step_id).strip()
        if not normalized_step_id:
            return
        if hasattr(self, "step_search_query") and self.step_search_query.get().strip():
            self.step_search_query.set("")
        if hasattr(self, "step_filter_category") and self.step_filter_category.get() not in ("", "全部"):
            self.step_filter_category.set("全部")
        self._refresh_steps_tree()
        for index, step in enumerate(self.steps):
            if str(step.get("id", "")).strip() != normalized_step_id:
                continue
            self._select_step(index)
            if hasattr(self, "step_tree") and str(index) in self.step_tree.get_children():
                self.step_tree.selection_set(str(index))
                self.step_tree.see(str(index))
            self.root.lift()
            self.root.focus_force()
            self.status_var.set(f"已定位到步骤：{normalized_step_id}")
            return True
        messagebox.showinfo("提示", f"当前链路里未找到步骤：{normalized_step_id}")
        return False

    def cmd_focus_flow_package_steps(self):
        package_index = self._get_selected_package_index()
        if package_index is None:
            messagebox.showinfo("提示", "请先选择一个流程包。")
            return
        package = self.flow_packages[package_index]
        step_ids = [str(item).strip() for item in package.get("stepIds", []) if str(item).strip()]
        if not step_ids:
            messagebox.showinfo("提示", "当前流程包还没有配置步骤。")
            return
        self._focus_step_by_id(step_ids[0])

    def _open_flow_package_dialog(self, initial_package=None):
        dialog = FlowPackageDialog(
            self.root,
            package=initial_package,
            available_steps=self._get_flow_package_dialog_available_steps(),
            on_focus_step=self._focus_step_by_id,
        )
        self.root.wait_window(dialog.window)
        return dialog.result

    def _rename_step_id_in_packages(self, old_step_id, new_step_id):
        if not old_step_id or old_step_id == new_step_id:
            return False
        changed = False
        for package in self.flow_packages:
            updated = []
            for item in package.get("stepIds", []):
                current_id = str(item).strip()
                if current_id == old_step_id:
                    updated.append(new_step_id)
                    changed = True
                else:
                    updated.append(current_id)
            package["stepIds"] = updated
        return changed

    def _remove_step_id_from_packages(self, step_id):
        if not step_id:
            return False
        changed = False
        for package in self.flow_packages:
            original_ids = list(package.get("stepIds", []))
            package["stepIds"] = [item for item in original_ids if str(item).strip() != step_id]
            if package["stepIds"] != original_ids:
                changed = True
        return changed

    def _rename_package_ref_in_steps(self, old_package_id, new_package_id):
        if not old_package_id or old_package_id == new_package_id:
            return False
        changed = False
        for step in self.steps:
            if str(step.get("packageRef", "")).strip() == old_package_id:
                step["packageRef"] = new_package_id
                changed = True
        return changed

    def _clear_package_ref_in_steps(self, package_id):
        if not package_id:
            return False
        changed = False
        for step in self.steps:
            if str(step.get("packageRef", "")).strip() == package_id:
                step["packageRef"] = ""
                changed = True
        return changed

    def _generate_unique_step_id(self, base_id):
        normalized_base = re.sub(r"[^0-9a-zA-Z_]+", "_", str(base_id or "").strip()).strip("_") or "step"
        existing_ids = {str(step.get("id", "")).strip() for step in self.steps}
        if normalized_base not in existing_ids:
            return normalized_base
        suffix = 2
        while f"{normalized_base}_{suffix}" in existing_ids:
            suffix += 1
        return f"{normalized_base}_{suffix}"

    def _get_selected_template_definition(self):
        if not hasattr(self, "template_listbox"):
            return None
        selection = self.template_listbox.curselection()
        if not selection:
            return None
        index = selection[0]
        if not (0 <= index < len(STEP_TEMPLATES)):
            return None
        return STEP_TEMPLATES[index]

    def _get_template_definition_by_id(self, template_id):
        target_id = str(template_id or "").strip()
        if not target_id:
            return None
        for template in STEP_TEMPLATES:
            if str(template.get("id", "")).strip() == target_id:
                return template
        return None

    def _refresh_template_library(self):
        if hasattr(self, "template_listbox"):
            self.template_listbox.delete(0, tk.END)
            for template in STEP_TEMPLATES:
                self.template_listbox.insert(tk.END, template.get("name", "未命名模板"))
        if hasattr(self, "template_preview_text"):
            self.template_preview_text.delete("1.0", tk.END)
            self.template_preview_text.insert("1.0", "请选择左侧模板，查看说明和默认动作配置。")

    def _build_step_from_template(self, template_definition, insert_at):
        step_payload = json.loads(json.dumps(template_definition.get("step", {}), ensure_ascii=False))
        base_id = step_payload.get("id", template_definition.get("id", "step"))
        step_payload["id"] = self._generate_unique_step_id(base_id)
        if step_payload.get("name") == "调用流程包" and self.flow_packages and not step_payload.get("packageRef"):
            step_payload["packageRef"] = self.flow_packages[0].get("id", "")
        return normalize_step(step_payload, insert_at)

    def _insert_step_from_template_definition(self, template_definition):
        if not template_definition:
            messagebox.showinfo("提示", "未找到可用模板。")
            return
        insert_at = self.selected_index + 1 if self.selected_index is not None else len(self.steps)
        new_step = self._build_step_from_template(template_definition, insert_at)
        self.steps.insert(insert_at, new_step)
        self._mark_dirty(f"已插入模板步骤：{template_definition.get('name', '')}")
        self._refresh_steps_tree()
        self._select_step(insert_at)
        self._refresh_overview()

    def _show_quick_add_template_menu(self, anchor_widget=None):
        menu = tk.Menu(self.root, tearoff=0)
        for item in QUICK_ADD_TEMPLATE_CHOICES:
            label = f"{item.get('label', '')}：{item.get('description', '')}"
            template_id = item.get("template_id", "")
            menu.add_command(
                label=label,
                command=lambda template_id=template_id: self.cmd_insert_quick_template(template_id),
            )
        try:
            if anchor_widget is not None and anchor_widget.winfo_exists():
                menu.tk_popup(
                    anchor_widget.winfo_rootx(),
                    anchor_widget.winfo_rooty() + anchor_widget.winfo_height(),
                )
            else:
                menu.tk_popup(self.root.winfo_pointerx(), self.root.winfo_pointery())
        finally:
            menu.grab_release()

    def cmd_insert_quick_template(self, template_id):
        template_definition = self._get_template_definition_by_id(template_id)
        if not template_definition:
            messagebox.showerror("模板新增失败", f"未找到步骤模板：{template_id}")
            return
        self._insert_step_from_template_definition(template_definition)

    def _parse_json_dict_text(self, raw_text, field_name):
        text = str(raw_text or "").strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} 不是合法 JSON：{exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{field_name} 必须是 JSON 对象。")
        return data

    def _parse_json_list_text(self, raw_text, field_name):
        text = str(raw_text or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} 不是合法 JSON：{exc}") from exc
        if not isinstance(data, list):
            raise ValueError(f"{field_name} 必须是 JSON 数组。")
        return data

    def _grid_label_entry(self, parent, label, variable, row, column):
        _grid_label_entry(parent, label, variable, row, column)

    def _refresh_action_control_choices(self, step=None):
        if not hasattr(self, "target_control_combo"):
            return
        target_step = step
        if not isinstance(target_step, dict):
            if self.selected_index is None or not (0 <= self.selected_index < len(self.steps)):
                target_step = {}
            else:
                target_step = self.steps[self.selected_index]
        controls = target_step.get("controls", []) if isinstance(target_step, dict) else []
        values = []
        for control in controls:
            control_id = str(control.get("id", "")).strip()
            control_name = str(control.get("name", "")).strip()
            if not control_id:
                continue
            display_text = f"{control_id} | {control_name}" if control_name else control_id
            values.append(display_text)
        self.target_control_combo["values"] = values
        if hasattr(self, "continue_when_control_combo"):
            self.continue_when_control_combo["values"] = values
        current_value = self._get_target_control_id() if hasattr(self, "_get_target_control_id") else self.var_target_control_id.get().strip()
        valid_ids = {value.split(" | ", 1)[0] for value in values}
        if current_value and current_value not in valid_ids:
            self.var_target_control_id.set("")
        continue_value = self._get_continue_when_control_id() if hasattr(self, "_get_continue_when_control_id") else self.var_continue_when_control_id.get().strip()
        if continue_value and continue_value not in valid_ids:
            self.var_continue_when_control_id.set("")

    def _set_target_control_value(self, control_id):
        control_id = str(control_id or "").strip()
        if not control_id:
            self.var_target_control_id.set("")
            return
        values = [str(value).strip() for value in (self.target_control_combo.cget("values") or [])]
        for value in values:
            if value.split(" | ", 1)[0].strip() == control_id:
                self.var_target_control_id.set(value)
                return
        self.var_target_control_id.set(control_id)

    def _set_continue_when_control_value(self, control_id):
        control_id = str(control_id or "").strip()
        if not control_id:
            self.var_continue_when_control_id.set("")
            return
        values = [str(value).strip() for value in (self.continue_when_control_combo.cget("values") or [])]
        for value in values:
            if value.split(" | ", 1)[0].strip() == control_id:
                self.var_continue_when_control_id.set(value)
                return
        self.var_continue_when_control_id.set(control_id)

    def _get_target_control_id(self):
        target_control_id = self.var_target_control_id.get().strip()
        if " | " in target_control_id:
            target_control_id = target_control_id.split(" | ", 1)[0].strip()
        return target_control_id

    def _get_continue_when_control_id(self):
        control_id = self.var_continue_when_control_id.get().strip()
        if " | " in control_id:
            control_id = control_id.split(" | ", 1)[0].strip()
        return control_id

    def _maybe_autoselect_target_control(self, control_id):
        action_name = self.var_action.get().strip() or "click"
        schema = get_action_schema(action_name)
        if not schema.get("target_required"):
            return
        if self._get_target_control_id():
            return
        self._set_target_control_value(control_id)
        self._refresh_input_param_options()

    def _on_target_control_changed(self, _event=None):
        self._set_target_control_value(self._get_target_control_id())
        self._refresh_action_schema_hint()
        self._refresh_input_param_options()

    def _refresh_input_param_options(self):
        """按当前目标控件的 optionValues 填充输入/参数下拉候选（仍可手输任意文本）。"""
        if not hasattr(self, "input_param_entry"):
            return
        try:
            control_id = self._get_target_control_id()
        except Exception:
            control_id = ""
        option_values = []
        target_step = {}
        try:
            if self.selected_index is not None and 0 <= self.selected_index < len(self.steps):
                target_step = self.steps[self.selected_index]
        except Exception:
            target_step = {}
        if not isinstance(target_step, dict):
            target_step = {}
        for control in (target_step.get("controls", []) or []):
            if not isinstance(control, dict):
                continue
            if str(control.get("id", "")).strip() != control_id:
                continue
            option_values = [
                str(value).strip()
                for value in (control.get("optionValues") or (control.get("inspectData") or {}).get("optionValues") or [])
                if str(value).strip()
            ]
            break
        try:
            self.input_param_entry.configure(values=option_values)
        except Exception:
            pass

    def _on_continue_when_control_changed(self, _event=None):
        self._set_continue_when_control_value(self._get_continue_when_control_id())
        self._refresh_action_schema_hint()

    def _refresh_action_schema_hint(self):
        action_name = self.var_action.get().strip() or "click"
        hint_text = build_action_schema_hint(action_name)
        post_input_keys_value = self._normalize_post_input_keys_value(
            self.var_post_input_keys.get() if hasattr(self, "var_post_input_keys") else ""
        )
        continue_when_control_id = self._get_continue_when_control_id() if hasattr(self, "var_continue_when_control_id") else ""
        if action_name == "type_text_relative" and post_input_keys_value and not continue_when_control_id:
            hint_text += " 当前提示: 你已配置输入后按键，但还没有配置续跑条件；建议补一个能代表业务提交成功的控件状态。"
        self.action_schema_hint_var.set(hint_text)

    @staticmethod
    def _normalize_post_input_keys_value(raw_value):
        normalized = str(raw_value or "").strip()
        if normalized in {"", "空"}:
            return ""
        return normalized

    def _set_post_input_keys_value(self, raw_value):
        normalized = self._normalize_post_input_keys_value(raw_value)
        self.var_post_input_keys.set(normalized or "空")

    def _sync_post_input_controls(self, raw_value=None):
        normalized = self._normalize_post_input_keys_value(
            self.var_post_input_keys.get() if raw_value is None else raw_value
        )
        self._syncing_post_input_ui = True
        try:
            self.var_post_input_keys.set(normalized or "空")
            self.var_require_blur_submit.set(normalized == "{TAB}")
        finally:
            self._syncing_post_input_ui = False

    def _on_post_input_keys_changed(self, _event=None):
        if self._syncing_post_input_ui:
            return
        self._sync_post_input_controls(self.var_post_input_keys.get())
        self._refresh_action_schema_hint()
        self._sync_action_config_preview_from_form()

    def _on_require_blur_submit_changed(self):
        if self._syncing_post_input_ui:
            return
        current_value = self._normalize_post_input_keys_value(self.var_post_input_keys.get())
        next_value = "{TAB}" if self.var_require_blur_submit.get() else ("" if current_value == "{TAB}" else current_value)
        self._sync_post_input_controls(next_value)
        self._refresh_action_schema_hint()
        self._sync_action_config_preview_from_form()

    def _normalize_template_path_for_storage(self, template_path):
        normalized_path = os.path.normpath(str(template_path or "").strip())
        if not normalized_path:
            return ""
        try:
            relative_path = os.path.relpath(normalized_path, BASE_DIR)
        except ValueError:
            return normalized_path
        if relative_path.startswith(".."):
            return normalized_path
        return relative_path

    def cmd_choose_fallback_template(self):
        initial_dir = TEMPLATE_ROOT_DIR if os.path.exists(TEMPLATE_ROOT_DIR) else BASE_DIR
        file_path = filedialog.askopenfilename(
            title="选择兜底模板图片",
            initialdir=initial_dir,
            filetypes=[("图片文件", "*.png;*.jpg;*.jpeg;*.bmp;*.webp"), ("所有文件", "*.*")],
        )
        if not file_path:
            return
        self.var_fallback_template.set(self._normalize_template_path_for_storage(file_path))
        # 已勾选“优先模板识别”时不再自动转 fallback：优先模式已覆盖模板能力，
        # 避免主流程失败后再重复一次模板兜底。
        if not self.var_prefer_template.get() and self.var_on_error.get().strip() in {"", "continue"}:
            self.var_on_error.set("fallback")
        self._mark_dirty(f"已设置步骤失败兜底模板：{os.path.basename(file_path)}")

    def cmd_open_template_library(self):
        if os.path.exists(TEMPLATE_BUILDER_SCRIPT):
            try:
                _launch_python_script(TEMPLATE_BUILDER_SCRIPT)
                self.status_var.set("已打开模板库工具，可制作或管理模板图片。")
                return
            except Exception:
                pass
        if not os.path.exists(TEMPLATE_ROOT_DIR):
            messagebox.showerror("打开失败", f"未找到模板库目录：\n{TEMPLATE_ROOT_DIR}")
            return
        os.startfile(TEMPLATE_ROOT_DIR)
        self.status_var.set("已打开模板库目录。")

    def _open_control_library(self):
        """打开控件库对话框"""
        self._open_control_import_dialog()

    def _open_control_import_dialog(self):
        """直接打开“从控件库导入”对话框，不要求先选择流程步骤。"""
        if getattr(self, "_import_dialog_window", None) and self._import_dialog_window.winfo_exists():
            try:
                self._import_dialog_window.lift()
                self._import_dialog_window.focus_force()
            except Exception:
                pass
            return

        try:
            default_window_title = ""
            if self.selected_index is not None and 0 <= self.selected_index < len(self.steps):
                default_window_title = self.steps[self.selected_index].get("windowTitle", "")
            dialog = ControlMapImportDialog(
                self.root,
                default_window_title=default_window_title,
                initial_filter="",
            )
            self._import_dialog_window = dialog.window
            self.root.wait_window(dialog.window)
            if dialog.result:
                if self.selected_index is not None:
                    self._append_controls_to_selected_step(dialog.result, "控件库")
                else:
                    self.status_var.set(f"已从控件库选择 {len(dialog.result)} 个控件。请先选择步骤后再同步。")
        except Exception as e:
            messagebox.showerror("错误", f"打开从控件库导入失败：\n{e}")

    def _open_control_map_builder(self):
        """打开控件库采集器"""
        if not os.path.exists(CONTROL_MAP_BUILDER_SCRIPT):
            messagebox.showerror("打开失败", f"未找到控件库采集器：\n{CONTROL_MAP_BUILDER_SCRIPT}")
            return
        try:
            _launch_python_script(CONTROL_MAP_BUILDER_SCRIPT)
            self.status_var.set("已打开控件库采集器，采集保存后可回到这里刷新控件库。")
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动控件库采集器失败：\n{exc}")

    def _set_widget_enabled(self, widget, enabled):
        try:
            widget.configure(state="normal" if enabled else "disabled")
        except Exception:
            pass

    def _show_widget(self, widget, visible):
        if visible:
            try:
                widget.grid()
            except Exception:
                pass
        else:
            try:
                widget.grid_remove()
            except Exception:
                pass

    def _on_action_type_changed(self, _event=None):
        if self.var_action_type.get().strip() == "action" and self.var_strategy.get().strip() in {"", "script"}:
            self.var_strategy.set("action")
        self._update_action_editor_visibility()

    def _on_action_changed(self, _event=None):
        """用户切换动作时的处理（仅由动作下拉的 <<ComboboxSelected>> 触发）。

        `_update_action_editor_visibility` 在切到不使用「目标控件」的动作时会清空该字段，
        这是必要的（保存路径对任何非空目标都会写入 controlId，清空可避免产出伪造字段）。
        但用户只是「路过」另一个动作再切回来时，已选控件不该丢 —— 这里在清空前暂存，
        切回同一动作时自动还原。
        """
        if self.var_action_type.get().strip() != "action":
            self.var_action_type.set("action")
        if self.var_strategy.get().strip() in {"", "script"}:
            self.var_strategy.set("action")
        previous_action = self._shown_action_name
        new_action = self.var_action.get().strip() or "click"
        previous_target = self.var_target_control_id.get().strip()
        if previous_action and previous_action != new_action and previous_target:
            self._action_target_stash[previous_action] = previous_target
        self._update_action_editor_visibility()
        # 切回需要目标控件的动作且字段为空时，还原该动作上次用过的控件
        if get_action_schema(new_action).get("target_required") \
                and not self.var_target_control_id.get().strip():
            stashed = self._action_target_stash.get(new_action)
            if stashed:
                self._set_target_control_value(stashed)

    def _update_action_editor_visibility(self):
        if not hasattr(self, "action_combo"):
            return
        is_action = self.var_action_type.get().strip() == "action"
        action_name = self.var_action.get().strip() or "click"
        schema = get_action_schema(action_name)
        target_required = bool(schema.get("target_required"))
        input_required = bool(schema.get("input_required"))

        widgets_to_toggle = [
            self.action_combo,
            self.target_control_combo,
            self.input_param_entry,
            self.wait_before_entry,
            self.wait_after_entry,
            self.timeout_entry,
            self.continue_when_control_combo,
            self.continue_when_condition_combo,
            self.continue_when_timeout_entry,
            self.continue_when_window_title_hint_entry,
            self.retry_count_entry,
            self.retry_interval_entry,
            self.on_error_combo,
            self.fallback_template_entry,
            self.fallback_template_choose_button,
            self.fallback_template_open_button,
            self.prefer_template_check,
            self.post_input_keys_combo,
            self.require_blur_submit_check,
            self.precond_condition_combo,
            self.precond_expected_combo,
            self.precond_control_entry,
        ]
        for widget in widgets_to_toggle:
            self._set_widget_enabled(widget, is_action)

        for label in [
            self.action_label,
            self.target_control_label,
            self.input_param_label,
            self.wait_before_label,
            self.wait_after_label,
            self.timeout_label,
            self.continue_when_control_label,
            self.continue_when_condition_label,
            self.continue_when_timeout_label,
            self.continue_when_window_title_hint_label,
            self.retry_count_label,
            self.retry_interval_label,
            self.on_error_label,
            self.fallback_template_label,
        ]:
            self._show_widget(label, True)

        show_target = target_required
        show_input = input_required
        show_timeout = bool(schema.get("show_timeout", True))
        show_continue_when = is_action and action_name not in {"sleep", "log"}
        show_post_input = is_action and action_name == "type_text_relative"

        self.action_label.configure(text="动作 *")
        self.target_control_label.configure(text="目标控件 *" if target_required else "目标控件")
        self.input_param_label.configure(text=str(schema.get("input_label", "输入/参数")))
        self._refresh_action_schema_hint()

        self._show_widget(self.target_control_label, show_target)
        if hasattr(self, "target_control_container"):
            self._show_widget(self.target_control_container, show_target)
        self._show_widget(self.target_control_combo, show_target)
        self._show_widget(self.input_param_label, show_input)
        self._show_widget(self.input_param_entry, show_input)
        self._show_widget(self.timeout_label, show_timeout)
        self._show_widget(self.timeout_entry, show_timeout)
        self._show_widget(self.continue_when_control_label, show_continue_when)
        self._show_widget(self.continue_when_control_combo, show_continue_when)
        self._show_widget(self.continue_when_condition_label, show_continue_when)
        self._show_widget(self.continue_when_condition_combo, show_continue_when)
        self._show_widget(self.continue_when_timeout_label, show_continue_when)
        self._show_widget(self.continue_when_timeout_entry, show_continue_when)
        self._show_widget(self.continue_when_window_title_hint_label, show_continue_when)
        self._show_widget(self.continue_when_window_title_hint_entry, show_continue_when)
        self._show_widget(self.relative_region_frame, is_action and action_name in {"type_text_relative", "click_relative_region"})
        self._show_widget(self.anchor_offset_frame, is_action and action_name == "click_relative_anchor")
        self._show_widget(self.post_input_keys_label, show_post_input)
        self._show_widget(self.post_input_keys_combo, show_post_input)
        self._show_widget(self.require_blur_submit_check, show_post_input)

        if action_name == "sleep":
            self.var_target_control_id.set("")
            if not self.var_input_text.get().strip():
                self.var_input_text.set("1")
        elif action_name in {"type_text_relative", "click_relative_region"}:
            self.var_target_control_id.set("")
            if action_name == "type_text_relative" and not self.var_input_text.get().strip():
                self.var_input_text.set("${runtime.sourceFilePath}")
            if not self.var_relative_parent_title.get().strip():
                self.var_relative_parent_title.set(self.var_window_title.get().strip())
            if not self.var_relative_parent_class.get().strip():
                self.var_relative_parent_class.set("Window")
            if action_name == "type_text_relative":
                self._sync_post_input_controls(self.var_post_input_keys.get())
            else:
                self._sync_post_input_controls("")
        elif action_name in {"type_text", "send_keys"}:
            if not self.var_input_text.get().strip():
                self.var_input_text.set("${runtime.sourceFilePath}")
            self._sync_post_input_controls("")
        elif action_name == "mouse_wheel":
            if not self.var_input_text.get().strip():
                self.var_input_text.set("1")
            self._sync_post_input_controls("")
        else:
            self._sync_post_input_controls("")
        if hasattr(self, "target_control_combo"):
            self.target_control_combo.configure(state="readonly" if is_action and show_target else "disabled")
        if hasattr(self, "target_control_picker_btn"):
            self.target_control_picker_btn.configure(state="normal" if is_action and show_target else "disabled")
        if hasattr(self, "target_control_view_btn"):
            self.target_control_view_btn.configure(state="normal" if is_action and show_target else "disabled")
        if hasattr(self, "continue_when_control_combo"):
            self.continue_when_control_combo.configure(state="readonly" if show_continue_when else "disabled")
        if hasattr(self, "continue_when_condition_combo"):
            self.continue_when_condition_combo.configure(state="readonly" if show_continue_when else "disabled")
        if hasattr(self, "post_input_keys_combo"):
            self.post_input_keys_combo.configure(state="readonly" if show_post_input else "disabled")
        if hasattr(self, "require_blur_submit_check"):
            self.require_blur_submit_check.configure(state="normal" if show_post_input else "disabled")
        # 记录当前展示的动作名，供 _on_action_changed 判断「切换前」用的是哪个动作
        self._shown_action_name = action_name

    def _load_action_editor_from_config(self, step):
        action_config = step.get("actionConfig", {}) if isinstance(step, dict) else {}
        action_name = str(action_config.get("action", "")).strip()
        action_defaults = build_action_default_config(action_name or "click")
        schema = get_action_schema(action_name or "click")
        # 换步骤时清空动作切换暂存，避免上一步暂存的控件被还原到本步
        self._action_target_stash = {}
        self.var_action.set(action_name or "click")
        self._set_target_control_value(str(action_config.get("controlId", "")).strip())
        input_key = str(schema.get("input_key", "")).strip()
        input_value = str(action_config.get(input_key, "")).strip() if input_key else str(action_config.get("value", "")).strip()
        self.var_input_text.set(input_value)
        self._refresh_input_param_options()
        self.var_wait_before.set(str(action_config.get("waitBefore", action_defaults.get("waitBefore", 0.0))).strip() or str(action_defaults.get("waitBefore", 0.0)))
        self.var_wait_after.set(str(action_config.get("waitAfter", action_defaults.get("waitAfter", 0.12))).strip() or str(action_defaults.get("waitAfter", 0.12)))
        self.var_timeout.set(str(action_config.get("timeoutSeconds", action_defaults.get("timeoutSeconds", 3.0))).strip() or str(action_defaults.get("timeoutSeconds", 3.0)))
        continue_when = action_config.get("continueWhen", {}) if isinstance(action_config.get("continueWhen"), dict) else {}
        self._set_continue_when_control_value(str(continue_when.get("controlId", "")).strip())
        self.var_continue_when_condition.set(str(continue_when.get("condition", "visible")).strip() or "visible")
        self.var_continue_when_timeout.set(
            str(continue_when.get("timeoutSeconds", action_defaults.get("timeoutSeconds", 3.0))).strip()
            or str(action_defaults.get("timeoutSeconds", 3.0))
        )
        self.var_continue_when_window_title_hint.set(str(continue_when.get("windowTitleHint", "")).strip())
        self.var_retry_count.set(str(action_config.get("retryCount", "0")).strip() or "0")
        self.var_retry_interval.set(str(action_config.get("retryInterval", "1")).strip() or "1")
        self.var_on_error.set(str(action_config.get("onError", "continue")).strip() or "continue")
        self.var_fallback_template.set(str(action_config.get("fallbackTemplate", "")).strip())
        prefer_template_value = action_config.get("preferTemplate", False)
        if isinstance(prefer_template_value, bool):
            self.var_prefer_template.set(prefer_template_value)
        else:
            self.var_prefer_template.set(str(prefer_template_value or "").strip().lower() in {"1", "true", "yes", "on"})
        self._sync_post_input_controls(action_config.get("postInputKeys", ""))
        parent_window = action_config.get("parentWindow", {}) if isinstance(action_config.get("parentWindow"), dict) else {}
        relative_region = action_config.get("relativeRegion", {}) if isinstance(action_config.get("relativeRegion"), dict) else {}
        self.var_relative_parent_title.set(str(parent_window.get("title", step.get("windowTitle", "") if isinstance(step, dict) else "")).strip())
        self.var_relative_parent_class.set(str(parent_window.get("className", "")).strip())
        self.var_relative_parent_framework.set(str(parent_window.get("frameworkId", "WPF")).strip() or "WPF")
        self.var_relative_region_x.set(str(relative_region.get("x", "0.45")).strip() or "0.45")
        self.var_relative_region_y.set(str(relative_region.get("y", "0.45")).strip() or "0.45")
        self.var_relative_region_width.set(str(relative_region.get("width", "0.32")).strip() or "0.32")
        self.var_relative_region_height.set(str(relative_region.get("height", "0.08")).strip() or "0.08")
        self.var_relative_region_anchor.set(str(relative_region.get("anchor", "center")).strip() or "center")
        self.var_anchor_offset_x.set(str(action_config.get("offsetX", 0)).strip() or "0")
        self.var_anchor_offset_y.set(str(action_config.get("offsetY", 0)).strip() or "0")
        self.var_anchor_align.set(str(action_config.get("anchorAlign", "")).strip() or "center")
        precondition = action_config.get("precondition", {}) if isinstance(action_config.get("precondition"), dict) else {}
        self.var_precond_condition.set(str(precondition.get("condition", "")).strip())
        self.var_precond_expected.set(str(precondition.get("expected", "off")).strip() or "off")
        self.var_precond_control.set(str(precondition.get("controlId", "")).strip())
        self._update_action_editor_visibility()

    def _parse_float_or_default(self, raw_value, field_name, default_value):
        return wt_flow_editor_utils.parse_float_or_default(raw_value, field_name, default_value)

    def _parse_int_or_default(self, raw_value, field_name, default_value):
        return wt_flow_editor_utils.parse_int_or_default(raw_value, field_name, default_value)

    def _extract_relative_region_action_config(self, payload):
        if not isinstance(payload, dict):
            raise ValueError("剪贴板内容不是有效的 JSON 对象。")
        candidate = payload
        if isinstance(payload.get("actionConfig"), dict):
            candidate = payload.get("actionConfig")
        elif isinstance(payload.get("stepExample"), dict) and isinstance(payload.get("stepExample", {}).get("actionConfig"), dict):
            candidate = payload.get("stepExample", {}).get("actionConfig")
        action_name = str(candidate.get("action", "")).strip()
        if action_name not in {"type_text_relative", "click_relative_region"}:
            raise ValueError("剪贴板里的配置不是父窗口相对区域动作。")
        return candidate, payload.get("stepExample") if isinstance(payload.get("stepExample"), dict) else payload

    def _is_default_relative_region_step_name(self, name):
        return str(name or "").strip() in {
            "",
            "新步骤",
            "点击控件",
            "输入文本",
            "父窗口区域点击",
            "父窗口区域输入",
        }

    def _is_default_relative_region_description(self, description):
        return str(description or "").strip() in {
            "",
            "通过父窗口相对区域点击目标位置。",
            "通过父窗口相对区域点击输入文本。",
            "通过父窗口相对区域点击控件，适合 WPF 弹窗、自绘按钮等场景。",
            "通过父窗口相对区域点击输入文本，适合 WPF 弹窗、自绘输入框等场景。",
        }

    def _build_relative_region_step_name(self, action_name, parent_title=""):
        base_name = "父窗口区域输入" if action_name == "type_text_relative" else "父窗口区域点击"
        normalized_parent_title = str(parent_title or "").strip()
        if not normalized_parent_title:
            return base_name
        short_title = normalized_parent_title if len(normalized_parent_title) <= 18 else normalized_parent_title[:18] + "..."
        return f"{base_name} - {short_title}"

    def _build_relative_region_description(self, action_name, parent_title=""):
        normalized_parent_title = str(parent_title or "").strip()
        if action_name == "type_text_relative":
            base_text = "通过父窗口相对区域点击并输入文本，适合 WPF 弹窗、自绘输入框等场景。"
        else:
            base_text = "通过父窗口相对区域点击目标位置，适合 WPF 弹窗、自绘按钮等场景。"
        if normalized_parent_title:
            return f"{base_text} 当前父窗口：{normalized_parent_title}"
        return base_text

    def _sync_action_config_preview_from_form(self, current_action_config=None):
        seed_action_config = current_action_config if isinstance(current_action_config, dict) else {}
        if not seed_action_config:
            try:
                seed_action_config = self._parse_json_dict_text(self._get_text(self.action_config_text), "Action 配置")
            except Exception:
                seed_action_config = {}
        try:
            preview_action_config = self._build_action_config_from_editor(seed_action_config)
        except Exception:
            preview_action_config = dict(seed_action_config)
        self._set_text(self.action_config_text, self._format_json_text(preview_action_config))
        self._refresh_relative_region_preview(preview_action_config)

    def _refresh_relative_region_preview(self, action_config=None):
        if not hasattr(self, "relative_region_preview_text"):
            return
        action_name = self.var_action.get().strip()
        if action_name not in {"type_text_relative", "click_relative_region", "click_relative_anchor"}:
            self._set_text(
                self.relative_region_preview_text,
                "当前动作不是父窗口相对区域动作，切换到“父窗口区域点击”或“父窗口区域输入”后会在这里展示预览。",
            )
            return
        preview_action_config = action_config if isinstance(action_config, dict) else {}
        if not preview_action_config:
            try:
                preview_action_config = self._build_action_config_from_editor({})
            except Exception:
                preview_action_config = {}
        if action_name == "click_relative_anchor":
            offset_x = preview_action_config.get("offsetX", 0)
            offset_y = preview_action_config.get("offsetY", 0)
            anchor_align = str(preview_action_config.get("anchorAlign", "")).strip() or "center"
            align_names = {
                "center": "控件中心",
                "left": "控件左边缘中点",
                "right": "控件右边缘中点",
                "top": "控件上边缘中点",
                "bottom": "控件下边缘中点",
            }
            preview_lines = [
                f"锚点控件: {preview_action_config.get('controlId', '') or '(未选择)'}",
                f"对齐基准: {anchor_align} ({align_names.get(anchor_align, anchor_align)})",
                f"偏移量: offsetX={offset_x}, offsetY={offset_y}",
                "",
                "执行说明:",
                f"先定位锚点控件，取可见矩形{align_names.get(anchor_align, '按基准')}为基准点，",
                f"点击坐标 = 基准点 + ({offset_x}, {offset_y})",
            ]
            self._set_text(self.anchor_offset_preview_text, "\n".join(preview_lines))
            return
        parent_window = preview_action_config.get("parentWindow", {}) if isinstance(preview_action_config.get("parentWindow"), dict) else {}
        relative_region = preview_action_config.get("relativeRegion", {}) if isinstance(preview_action_config.get("relativeRegion"), dict) else {}
        preview_lines = [
            f"父窗口标题: {parent_window.get('title', '') or '(未填写)'}",
            f"父窗口类名: {parent_window.get('className', '') or '(未填写)'}",
            f"框架类型: {parent_window.get('frameworkId', '') or '(未填写)'}",
            "",
            "相对区域:",
            json.dumps(relative_region, ensure_ascii=False, indent=2) if relative_region else "{}",
        ]
        self._set_text(self.relative_region_preview_text, "\n".join(preview_lines))

    def _apply_relative_region_preview_height(self, height):
        new_height = max(6, min(int(height or 6), 24))
        self._relative_region_preview_height = new_height
        if hasattr(self, "relative_region_preview_text"):
            self.relative_region_preview_text.configure(height=new_height)
        if hasattr(self, "anchor_offset_preview_text"):
            self.anchor_offset_preview_text.configure(height=new_height)

    def _start_relative_region_preview_resize(self, event):
        self._relative_region_preview_resize_start_y = getattr(event, "y_root", None)
        self._relative_region_preview_resize_start_height = self._relative_region_preview_height

    def _drag_relative_region_preview_resize(self, event):
        if self._relative_region_preview_resize_start_y is None:
            return
        try:
            line_height = max(12, int(self.font_help_text.metrics("linespace")))
        except Exception:
            line_height = 18
        delta_y = int(getattr(event, "y_root", 0)) - int(self._relative_region_preview_resize_start_y or 0)
        delta_lines = int(round(float(delta_y) / float(line_height)))
        self._apply_relative_region_preview_height(self._relative_region_preview_resize_start_height + delta_lines)

    def _finish_relative_region_preview_resize(self, _event=None):
        self._relative_region_preview_resize_start_y = None
        self._relative_region_preview_resize_start_height = self._relative_region_preview_height

    def _reset_relative_region_preview_height(self, _event=None):
        self._apply_relative_region_preview_height(6)
        self._finish_relative_region_preview_resize()

    def cmd_import_relative_region_from_clipboard(self):
        try:
            raw_text = self.root.clipboard_get()
        except Exception as exc:
            messagebox.showerror("读取失败", f"读取剪贴板失败：\n{exc}")
            return
        try:
            payload = json.loads(raw_text)
            action_config, source_step = self._extract_relative_region_action_config(payload)
        except Exception as exc:
            messagebox.showerror("解析失败", f"剪贴板内容不是可用的相对区域配置：\n{exc}")
            return
        parent_window = action_config.get("parentWindow", {}) if isinstance(action_config.get("parentWindow"), dict) else {}
        relative_region = action_config.get("relativeRegion", {}) if isinstance(action_config.get("relativeRegion"), dict) else {}
        action_name = str(action_config.get("action", "type_text_relative")).strip() or "type_text_relative"
        parent_title = str(
            parent_window.get("title", source_step.get("windowTitle", "")) if isinstance(source_step, dict) else parent_window.get("title", "")
        ).strip()
        self.var_action_type.set("action")
        self.var_strategy.set("action")
        self.var_action.set(action_name)
        self.var_relative_parent_title.set(parent_title)
        self.var_relative_parent_class.set(str(parent_window.get("className", "")).strip())
        self.var_relative_parent_framework.set(str(parent_window.get("frameworkId", "WPF")).strip() or "WPF")
        self.var_relative_region_x.set(str(relative_region.get("x", "0.45")).strip() or "0.45")
        self.var_relative_region_y.set(str(relative_region.get("y", "0.45")).strip() or "0.45")
        self.var_relative_region_width.set(str(relative_region.get("width", "0.32")).strip() or "0.32")
        self.var_relative_region_height.set(str(relative_region.get("height", "0.08")).strip() or "0.08")
        self.var_relative_region_anchor.set(str(relative_region.get("anchor", "center")).strip() or "center")
        self.var_timeout.set(str(action_config.get("timeoutSeconds", build_action_default_config(action_name).get("timeoutSeconds", 3.0))).strip())
        self.var_wait_before.set(str(action_config.get("waitBefore", build_action_default_config(action_name).get("waitBefore", 0.0))).strip())
        self.var_wait_after.set(str(action_config.get("waitAfter", build_action_default_config(action_name).get("waitAfter", 0.12))).strip())
        self.var_input_text.set(str(action_config.get("text", "")).strip())
        self._sync_post_input_controls(action_config.get("postInputKeys", ""))
        if isinstance(source_step, dict) and str(source_step.get("windowTitle", "")).strip():
            self.var_window_title.set(str(source_step.get("windowTitle", "")).strip())
        elif parent_title:
            self.var_window_title.set(parent_title)
        if isinstance(source_step, dict) and str(source_step.get("name", "")).strip() and self._is_default_relative_region_step_name(self.var_name.get()):
            self.var_name.set(str(source_step.get("name", "")).strip())
        elif self._is_default_relative_region_step_name(self.var_name.get()):
            self.var_name.set(self._build_relative_region_step_name(action_name, parent_title))
        current_description = self._get_text(self.description_text)
        if isinstance(source_step, dict) and str(source_step.get("description", "")).strip() and self._is_default_relative_region_description(current_description):
            self._set_text(self.description_text, str(source_step.get("description", "")).strip())
        elif self._is_default_relative_region_description(current_description):
            self._set_text(self.description_text, self._build_relative_region_description(action_name, parent_title))
        self._update_action_editor_visibility()
        self._sync_action_config_preview_from_form(action_config)
        self.status_var.set(f"已导入相对区域配置并同步当前步骤：{action_name}")

    def cmd_clear_relative_region_fields(self):
        self.var_relative_parent_title.set("")
        self.var_relative_parent_class.set("Window")
        self.var_relative_parent_framework.set("WPF")
        self.var_relative_region_x.set("0.45")
        self.var_relative_region_y.set("0.45")
        self.var_relative_region_width.set("0.32")
        self.var_relative_region_height.set("0.08")
        self.var_relative_region_anchor.set("center")
        if self.var_action.get().strip() == "type_text_relative":
            self.var_input_text.set("${runtime.sourceFilePath}")
            self._sync_post_input_controls("")
        self.status_var.set("已清空相对区域配置，可重新粘贴或手工录入。")

    def _build_action_config_from_editor(self, current_action_config):
        action_config = dict(current_action_config or {})
        action_type = self.var_action_type.get().strip() or "script"
        if action_type != "action":
            return action_config

        action_name = self.var_action.get().strip() or "click"
        action_defaults = build_action_default_config(action_name)
        schema = get_action_schema(action_name)
        action_config["action"] = action_name
        action_config["timeoutSeconds"] = self._parse_float_or_default(self.var_timeout.get(), "超时(秒)", action_defaults.get("timeoutSeconds", 3.0))
        action_config["waitBefore"] = self._parse_float_or_default(self.var_wait_before.get(), "前等待(秒)", action_defaults.get("waitBefore", 0.0))
        action_config["waitAfter"] = self._parse_float_or_default(self.var_wait_after.get(), "后等待(秒)", action_defaults.get("waitAfter", 0.12))
        action_config["retryCount"] = self._parse_int_or_default(self.var_retry_count.get(), "失败重试次数", 0)
        action_config["retryInterval"] = self._parse_float_or_default(self.var_retry_interval.get(), "重试间隔(秒)", 1.0)
        action_config["onError"] = self.var_on_error.get().strip() or "continue"
        continue_when_control_id = self._get_continue_when_control_id()
        if continue_when_control_id:
            continue_when = {
                "controlId": continue_when_control_id,
                "condition": self.var_continue_when_condition.get().strip() or "visible",
                "timeoutSeconds": self._parse_float_or_default(
                    self.var_continue_when_timeout.get(),
                    "续跑超时(秒)",
                    action_defaults.get("timeoutSeconds", 3.0),
                ),
            }
            window_title_hint = self.var_continue_when_window_title_hint.get().strip()
            if window_title_hint:
                continue_when["windowTitleHint"] = window_title_hint
            action_config["continueWhen"] = continue_when
        else:
            action_config.pop("continueWhen", None)

        target_control_id = self._get_target_control_id()
        if schema.get("target_required") and not target_control_id:
            raise ValueError("目标控件 * 不能为空，请先在“步骤下的细分控件清单”里维护控件并选择。")
        if target_control_id:
            action_config["controlId"] = target_control_id
        else:
            action_config.pop("controlId", None)

        # 展开/折叠前置判断（条件执行）
        precond_condition = self.var_precond_condition.get().strip()
        if precond_condition:
            precond_control = self.var_precond_control.get().strip() or target_control_id
            action_config["precondition"] = {
                "condition": precond_condition,
                "expected": self.var_precond_expected.get().strip() or "off",
                "controlId": precond_control,
            }
        else:
            action_config.pop("precondition", None)

        if action_name == "click_relative_anchor":
            action_config["anchorAlign"] = self.var_anchor_align.get().strip() or "center"
            action_config["offsetX"] = self._parse_float_or_default(self.var_anchor_offset_x.get(), "水平偏移 X", 0)
            action_config["offsetY"] = self._parse_float_or_default(self.var_anchor_offset_y.get(), "垂直偏移 Y", 0)
            action_config.pop("parentWindow", None)
            action_config.pop("relativeRegion", None)
        elif action_name in {"type_text_relative", "click_relative_region"}:
            parent_title = self.var_relative_parent_title.get().strip() or self.var_window_title.get().strip()
            if not parent_title:
                raise ValueError("父窗口标题 * 不能为空，请先填写目标窗口或父窗口标题。")
            action_config["parentWindow"] = {
                "title": parent_title,
                "className": self.var_relative_parent_class.get().strip(),
                "frameworkId": self.var_relative_parent_framework.get().strip(),
            }
            action_config["relativeRegion"] = {
                "x": self._parse_float_or_default(self.var_relative_region_x.get(), "区域 X", 0.45),
                "y": self._parse_float_or_default(self.var_relative_region_y.get(), "区域 Y", 0.45),
                "width": self._parse_float_or_default(self.var_relative_region_width.get(), "区域宽度", 0.32),
                "height": self._parse_float_or_default(self.var_relative_region_height.get(), "区域高度", 0.08),
                "anchor": self.var_relative_region_anchor.get().strip() or "center",
            }
        else:
            action_config.pop("parentWindow", None)
            action_config.pop("relativeRegion", None)

        text_value = self.var_input_text.get().strip()
        post_input_keys_value = self._normalize_post_input_keys_value(self.var_post_input_keys.get())
        for key in ("text", "value", "seconds", "delta"):
            action_config.pop(key, None)
        if schema.get("input_required") and not text_value:
            raise ValueError(f"{str(schema.get('input_label', '输入参数')).replace('*', '').strip()} 不能为空。")
        input_key = str(schema.get("input_key", "")).strip()
        if input_key == "text" and text_value:
            action_config["text"] = text_value
        elif input_key == "seconds" and text_value:
            action_config["seconds"] = self._parse_float_or_default(text_value, "等待秒数", 1.0)
            action_config.pop("controlId", None)
        elif input_key == "delta" and text_value:
            action_config["delta"] = self._parse_float_or_default(text_value, "滚轮值", 1.0)
        elif text_value:
            action_config["value"] = text_value

        if action_name == "type_text_relative" and post_input_keys_value:
            action_config["postInputKeys"] = post_input_keys_value
        else:
            action_config.pop("postInputKeys", None)

        fallback_template = self.var_fallback_template.get().strip()
        prefer_template = self.var_prefer_template.get()
        if fallback_template:
            action_config["fallbackTemplate"] = fallback_template
            action_config["fallbackMode"] = "template_match"
            # 勾选“优先模板”时不再自动转 fallback：优先模式已覆盖模板能力，
            # 主流程失败应直接按 onError 策略处理，避免模板被尝试两次。
            if not prefer_template and action_config.get("onError") in {"", "continue"}:
                action_config["onError"] = "fallback"
            if prefer_template:
                action_config["preferTemplate"] = True
            else:
                action_config.pop("preferTemplate", None)
        else:
            action_config.pop("fallbackTemplate", None)
            action_config.pop("fallbackMode", None)
            action_config.pop("preferTemplate", None)

        return action_config

    def _build_step_tree_action_summary(self, step):
        action_type = str(step.get("actionType", "script")).strip() or "script"
        if action_type == "action":
            action_name = str(step.get("actionConfig", {}).get("action", "")).strip()
            return action_name or "action"
        if action_type == "flow_ref":
            return "调用流程包"
        return action_type

    def _build_step_tree_target_summary(self, step):
        action_type = str(step.get("actionType", "script")).strip() or "script"
        if action_type == "flow_ref":
            return str(step.get("packageRef", "")).strip() or "-"
        action_config = step.get("actionConfig", {}) if isinstance(step.get("actionConfig", {}), dict) else {}
        action_name = str(action_config.get("action", "")).strip()
        if action_name == "click_relative_anchor":
            control_id = str(action_config.get("controlId", "")).strip()
            offset_x = str(action_config.get("offsetX", 0))
            offset_y = str(action_config.get("offsetY", 0))
            anchor_align = str(action_config.get("anchorAlign", "")).strip() or "center"
            return f"锚点:{control_id or '(未选)'} | {anchor_align} 偏移:({offset_x},{offset_y})"
        if action_name in {"type_text_relative", "click_relative_region"}:
            parent_window = action_config.get("parentWindow", {}) if isinstance(action_config.get("parentWindow"), dict) else {}
            relative_region = action_config.get("relativeRegion", {}) if isinstance(action_config.get("relativeRegion"), dict) else {}
            parent_title = str(parent_window.get("title", "")).strip() or str(step.get("windowTitle", "")).strip()
            try:
                region_x = float(relative_region.get("x", 0.0) or 0.0)
            except Exception:
                region_x = 0.0
            try:
                region_y = float(relative_region.get("y", 0.0) or 0.0)
            except Exception:
                region_y = 0.0
            region_summary = f"x={region_x:.2f}, y={region_y:.2f}"
            return f"{parent_title or '父窗口'} | {region_summary}"
        control_id = str(action_config.get("controlId", "")).strip()
        if control_id:
            controls = step.get("controls", [])
            for control in controls:
                if str(control.get("id", "")).strip() == control_id:
                    return str(control.get("name", "")).strip() or control_id
            return control_id
        inspect_hints = step.get("inspectHints", {}) if isinstance(step.get("inspectHints", {}), dict) else {}
        return (
            str(inspect_hints.get("controlName", "")).strip()
            or str(step.get("windowTitle", "")).strip()
            or "-"
        )

    def _refresh_steps_tree(self):
        self.step_tree.delete(*self.step_tree.get_children())
        package_names_map = self._build_step_package_names_map()
        visible_indexes = self._get_visible_step_indexes()
        for order, index in enumerate(visible_indexes, start=1):
            step = self.steps[index]
            name = step.get("name", "")
            package_names = package_names_map.get(str(step.get("id", "")).strip(), [])
            if package_names:
                name = f"{name} [{', '.join(package_names)}]" if name else f"[{', '.join(package_names)}]"
            prefix = "" if step.get("enabled", True) else "[停用] "
            action_summary = self._build_step_tree_action_summary(step)
            target_summary = self._build_step_tree_target_summary(step)
            tags = []
            if not step.get("enabled", True):
                tags.append("disabled")
            action_type = str(step.get("actionType", "script")).strip()
            if action_type == "action":
                tags.append("action_step")
            elif action_type == "flow_ref":
                tags.append("flow_ref_step")
            self.step_tree.insert(
                "",
                tk.END,
                iid=str(index),
                values=(order, prefix + name, action_summary, target_summary),
                tags=tuple(tags),
            )
        if hasattr(self, "step_count_badge_var"):
            self.step_count_badge_var.set(f"{len(visible_indexes)} / {len(self.steps)} 步")
        if self.selected_index is not None and str(self.selected_index) in self.step_tree.get_children():
            self.step_tree.selection_set(str(self.selected_index))
            self.step_tree.see(str(self.selected_index))
        self._set_title()

    def _refresh_overview(self):
        runtime_config = self._build_runtime_config_from_form()
        flow_packages = self._build_flow_packages_from_form()
        lines = [
            f"项目: {self.flow_definition.get('project', '')}",
            f"版本: {self.flow_definition.get('version', '')}",
            "",
            self.flow_definition.get("description", ""),
            "",
            "当前运行参数:",
            f"  gmExe: {runtime_config.get('gmExe', '') or '未设置'}",
            f"  sourceFilePath: {runtime_config.get('sourceFilePath', '') or '未设置'}",
            f"  outputDir: {runtime_config.get('outputDir', '') or '未设置'}",
            f"  projectionFilePath: {runtime_config.get('projectionFilePath', '') or '未设置'}",
            "",
            f"当前流程包: {len(flow_packages)} 个",
            "",
            "当前流程链路:",
        ]
        for package in flow_packages:
            lines.append(
                f"包 {package.get('id', '')} | {package.get('name', '')} | steps={','.join(package.get('stepIds', []))}"
            )
        if flow_packages:
            lines.append("")
        for index, step in enumerate(self.steps, start=1):
            lines.append(
                f"{index:02d}. [{step.get('stage', '')}] {step.get('name', '')} | "
                f"strategy={step.get('strategy', '')} | actionType={step.get('actionType', 'script')} | enabled={step.get('enabled', True)}"
            )
            if step.get("successLog"):
                lines.append(f"    successLog: {step['successLog']}")
            if step.get("packageRef"):
                lines.append(f"    packageRef: {step.get('packageRef', '')}")
            if step.get("stepParams"):
                lines.append(f"    stepParams: {json.dumps(step.get('stepParams', {}), ensure_ascii=False)}")
            if step.get("actionConfig"):
                lines.append(f"    actionConfig: {json.dumps(step.get('actionConfig', {}), ensure_ascii=False)}")
            inspect_hints = step.get("inspectHints", {})
            locator_summary = " | ".join(
                item
                for item in [
                    inspect_hints.get("controlName", ""),
                    inspect_hints.get("automationId", ""),
                    inspect_hints.get("uiPath", ""),
                    inspect_hints.get("templateKey", ""),
                ]
                if item
            )
            if locator_summary:
                lines.append(f"    locator: {locator_summary}")
            controls = step.get("controls", [])
            if controls:
                lines.append(f"    controls: {len(controls)} 个细分控件")
                for control in controls[:4]:
                    lines.append(
                        f"      - {control.get('name', '')} | {control.get('targetMethod', '')}:{control.get('targetValue', '')}"
                    )
                if len(controls) > 4:
                    lines.append(f"      - ... 还有 {len(controls) - 4} 个控件")
            fallbacks = step.get("fallbacks", [])
            if fallbacks:
                lines.append(f"    fallbacks: {', '.join(fallbacks)}")
        self.overview_text.config(state=tk.NORMAL)
        self.overview_text.delete("1.0", tk.END)
        self.overview_text.insert("1.0", "\n".join(lines))
        self.overview_text.config(state=tk.DISABLED)

    def _on_template_select(self, _event=None):
        template_definition = self._get_selected_template_definition()
        if not template_definition:
            self.template_summary_var.set("请选择一个步骤模板")
            return
        step_payload = template_definition.get("step", {})
        lines = [
            f"模板: {template_definition.get('name', '')}",
            template_definition.get("description", ""),
            "",
            f"默认 actionType: {step_payload.get('actionType', '') or 'script'}",
            f"默认 strategy: {step_payload.get('strategy', '') or '未设置'}",
            f"默认步骤ID: {step_payload.get('id', '')}",
        ]
        if step_payload.get("packageRef"):
            lines.append(f"默认 packageRef: {step_payload.get('packageRef', '')}")
        if step_payload.get("actionConfig"):
            lines.extend(["", "默认 Action 配置:", json.dumps(step_payload.get("actionConfig", {}), ensure_ascii=False, indent=2)])
        self.template_preview_text.delete("1.0", tk.END)
        self.template_preview_text.insert("1.0", "\n".join(lines))
        self.template_summary_var.set(f"模板已选择：{template_definition.get('name', '')}")

    def _on_tree_select(self, _event=None):
        if self._suppress_tree_select_event:
            return
        # 按压/拖拽进行中（ButtonPress-1 绑定的 _start_step_drag 在按下任意行时就会
        # 置位 _dragging_step_iid，而选中事件恰在按压期间同步触发）：此刻既不能弹
        # 模态确认（会吞掉 release、打断拖拽手势），也不能直接重载表单（未应用改动
        # 会无声丢失）——统一推迟到 _finish_step_drag：未发生重排时手势已结束，
        # 再走本方法的完整确认流程；键盘方向键切步无按压，仍在此处直接确认。
        if getattr(self, "_dragging_step_iid", ""):
            return
        selection = self.step_tree.selection()
        if not selection:
            return
        if len(selection) > 1:
            try:
                focus_index = int(selection[0])
            except Exception:
                return
            if 0 <= focus_index < len(self.steps):
                # 多选加载第一个选中步骤：与单击切步同样先确认「未应用改动」，
                # 否则 Ctrl/Shift 多选时表单里的修改同样无声丢失。
                if not self._confirm_discard_form_changes():
                    self._restore_step_selection()
                    return
                self.selected_index = focus_index
                self._load_step_into_form(self.steps[focus_index])
                self.status_var.set(f"已选择 {len(selection)} 个步骤，当前编辑第一个选中步骤。")
            return
        target_index = int(selection[0])
        # 用户点击切换步骤：先处理当前表单里「未应用到步骤」的修改，避免无声丢弃。
        if not self._confirm_discard_form_changes():
            self._restore_step_selection()
            return
        self._select_step(target_index)

    def _restore_step_selection(self):
        """把步骤树的选中项恢复到当前编辑的步骤（不重新加载表单、不丢弃输入）。"""
        if self.selected_index is None:
            return
        self._suppress_tree_select_event = True
        try:
            self.step_tree.selection_set(str(self.selected_index))
        finally:
            self._suppress_tree_select_event = False

    def _form_snapshot(self):
        """收集当前表单全部可编辑字段的值，用于检测「未应用到步骤」的修改。

        字段清单在 ``_FORM_SNAPSHOT_VARS`` / ``_FORM_SNAPSHOT_TEXTS``，
        **新增表单字段时须同步补充**。防御式读取：字段尚未创建时跳过，不抛异常。
        """
        values = []
        for name in self._FORM_SNAPSHOT_VARS:
            var = getattr(self, name, None)
            if var is None:
                continue
            try:
                values.append((name, var.get()))
            except Exception:
                values.append((name, None))
        for name in self._FORM_SNAPSHOT_TEXTS:
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                values.append((name, _get_text(widget)))
            except Exception:
                values.append((name, None))
        return tuple(values)

    def _confirm_discard_form_changes(self):
        """切步骤/关窗前处理当前表单里「未应用到步骤」的修改。

        返回 True 表示可以继续（无改动 / 用户选择先应用且成功 / 用户放弃改动），
        返回 False 表示用户取消（应留在当前步骤）。
        """
        if self.selected_index is None:
            return True
        baseline = getattr(self, "_form_baseline", None)
        if baseline is None:
            return True
        if self._form_snapshot() == baseline:
            return True
        answer = messagebox.askyesnocancel(
            "未应用的修改",
            "当前表单有未应用到步骤的修改。\n\n"
            "「是」：先应用到当前步骤，再继续\n"
            "「否」：放弃这些修改\n"
            "「取消」：留在当前步骤",
            parent=self.root,
        )
        if answer is None:
            return False
        if not answer:
            return True
        # 「是」：先应用；失败时 cmd_apply_step 已给出提示，且基线未更新 → 阻止继续
        self.cmd_apply_step()
        return self._form_snapshot() == self._form_baseline

    def _get_selected_step_indexes(self):
        selection = self.step_tree.selection()
        indexes = []
        for item in selection:
            try:
                index = int(item)
            except Exception:
                continue
            if 0 <= index < len(self.steps):
                indexes.append(index)
        return sorted(set(indexes))

    def _select_step(self, index, preserve_selection=False):
        if not (0 <= index < len(self.steps)):
            return
        self.selected_index = index
        current_selection = list(self.step_tree.selection())
        if not preserve_selection and current_selection != [str(index)]:
            self._suppress_tree_select_event = True
            try:
                self.step_tree.selection_set(str(index))
            finally:
                self._suppress_tree_select_event = False
        self._load_step_into_form(self.steps[index])

    def _start_step_drag(self, event):
        row_id = self.step_tree.identify_row(event.y)
        if not row_id:
            self._dragging_step_iid = ""
            self._drag_hover_iid = ""
            self._drag_hover_after = False
            return
        self._dragging_step_iid = str(row_id)
        self._drag_hover_iid = str(row_id)
        self._drag_hover_after = False

    def _track_step_drag(self, event):
        if not self._dragging_step_iid:
            return
        row_id = self.step_tree.identify_row(event.y)
        if not row_id:
            return
        self._drag_hover_iid = str(row_id)
        bbox = self.step_tree.bbox(row_id)
        if bbox and len(bbox) >= 4:
            row_top = bbox[1]
            row_height = bbox[3]
            self._drag_hover_after = event.y > row_top + row_height / 2
        else:
            self._drag_hover_after = False
        try:
            drag_index = int(self._dragging_step_iid)
            hover_index = int(self._drag_hover_iid)
        except Exception:
            return
        drag_name = str(self.steps[drag_index].get("name", "")).strip() if 0 <= drag_index < len(self.steps) else self._dragging_step_iid
        hover_name = str(self.steps[hover_index].get("name", "")).strip() if 0 <= hover_index < len(self.steps) else self._drag_hover_iid
        position_text = "后方" if self._drag_hover_after else "前方"
        self.status_var.set(f"拖拽排序中：将 {drag_name or '步骤'} 移到 {hover_name or '目标步骤'} {position_text}")

    def _finish_step_drag(self, event):
        if not self._dragging_step_iid:
            return
        source_iid = self._dragging_step_iid
        target_iid = self.step_tree.identify_row(event.y) or self._drag_hover_iid
        place_after = self._drag_hover_after
        self._dragging_step_iid = ""
        self._drag_hover_iid = ""
        self._drag_hover_after = False
        if not source_iid or not target_iid:
            # 松开时不在任何行上（如拖出树外）：按压期间选中可能已移到其他行，
            # 而表单仍停在原步骤（见 _on_tree_select 的按压期推迟）——恢复选中保持一致。
            self._restore_step_selection()
            return
        try:
            source_index = int(source_iid)
            target_index = int(target_iid)
        except Exception:
            return
        if not self._move_step_to_position(source_index, target_index, place_after=place_after):
            # 未发生重排（普通点击/原地松开）：手势已结束，此刻弹模态确认是安全的。
            # 仅当选中项与当前编辑步骤不一致（存在被推迟的切步）时才走确认流程；
            # 点在当前已选行上时无事发生，避免误弹「未应用改动」确认。
            if list(self.step_tree.selection()) != [str(self.selected_index)]:
                self._on_tree_select()
            return
        self._mark_dirty("已拖拽调整步骤顺序")
        self._refresh_steps_tree()
        self._select_step(self.selected_index if self.selected_index is not None else target_index)
        self._refresh_overview()

    def _move_step_to_position(self, source_index, target_index, place_after=False):
        if not (0 <= source_index < len(self.steps) and 0 <= target_index < len(self.steps)):
            return False
        if source_index == target_index and not place_after:
            return False
        step = self.steps.pop(source_index)
        if source_index < target_index:
            target_index -= 1
        insert_at = target_index + (1 if place_after else 0)
        insert_at = max(0, min(insert_at, len(self.steps)))
        self.steps.insert(insert_at, step)
        self.selected_index = insert_at
        return True

    def _load_step_into_form(self, step):
        self.var_id.set(step.get("id", ""))
        self.var_name.set(step.get("name", ""))
        self.var_stage.set(step.get("stage", ""))
        self.var_strategy.set(step.get("strategy", ""))
        self.var_action_type.set(step.get("actionType", "script"))
        self.var_enabled.set(bool(step.get("enabled", True)))
        self.var_code_symbol.set(step.get("codeSymbol", ""))
        self.var_code_reference.set(step.get("codeReference", ""))
        self.var_package_ref.set(step.get("packageRef", ""))
        self.var_success_log.set(step.get("successLog", ""))
        self.var_window_title.set(step.get("windowTitle", ""))

        inspect_hints = step.get("inspectHints", {})
        self.var_control_name.set(inspect_hints.get("controlName", ""))
        self.var_class_name.set(inspect_hints.get("className", ""))
        self.var_automation_id.set(inspect_hints.get("automationId", ""))
        self.var_control_type.set(inspect_hints.get("controlType", ""))
        self.var_ui_path.set(inspect_hints.get("uiPath", ""))
        self.var_template_key.set(inspect_hints.get("templateKey", ""))
        self._refresh_controls_tree(step)
        self._refresh_action_control_choices(step)
        self._load_action_editor_from_config(step)

        self._set_text(self.description_text, step.get("description", ""))
        self._set_text(self.aux_checks_text, "\n".join(step.get("auxChecks", [])))
        self._set_text(self.fallbacks_text, "\n".join(step.get("fallbacks", [])))
        self._set_text(self.notes_text, step.get("notes", ""))
        self._set_text(self.step_params_text, self._format_json_text(step.get("stepParams", {})))
        self._set_text(self.action_config_text, self._format_json_text(step.get("actionConfig", {})))
        current_index = self.selected_index if self.selected_index is not None else 0
        self.status_var.set(f"已加载步骤 #{index_to_seq(current_index)}：{step.get('name', '')}")
        # 重新记录表单基线：此刻表单与步骤一致，之后任何未应用的改动都会被检测到
        self._form_baseline = self._form_snapshot()
        self._update_step_header_display()

    def _build_step_from_form(self):
        step_params = self._parse_json_dict_text(self._get_text(self.step_params_text), "步骤参数")
        action_config = self._parse_json_dict_text(self._get_text(self.action_config_text), "Action 配置")
        current_step = self.steps[self.selected_index] if self.selected_index is not None and 0 <= self.selected_index < len(self.steps) else {}
        action_config = self._build_action_config_from_editor(action_config)
        step_name = self.var_name.get().strip()
        if not step_name:
            raise ValueError("步骤名称 * 不能为空。")
        current_controls = current_step.get("controls", []) if isinstance(current_step, dict) else []
        action_type = self.var_action_type.get().strip() or "script"
        if action_type == "action":
            action_name = str(action_config.get("action", "")).strip()
            control_id = str(action_config.get("controlId", "")).strip()
            if action_name != "sleep" and control_id:
                known_control_ids = {
                    str(control.get("id", "")).strip()
                    for control in current_controls
                    if isinstance(control, dict) and str(control.get("id", "")).strip()
                }
                if control_id not in known_control_ids:
                    raise ValueError(f"目标控件 * 未在当前步骤的细分控件清单中找到：{control_id}")
        step_payload = normalize_step(
            {
                "id": self.var_id.get().strip(),
                "name": step_name,
                "stage": self.var_stage.get().strip(),
                "strategy": self.var_strategy.get().strip(),
                "actionType": self.var_action_type.get().strip() or "script",
                "topLevel": bool(current_step.get("topLevel", True)),
                "enabled": bool(self.var_enabled.get()),
                "codeSymbol": self.var_code_symbol.get().strip(),
                "codeReference": self.var_code_reference.get().strip(),
                "packageRef": self.var_package_ref.get().strip(),
                "description": self._get_text(self.description_text),
                "successLog": self.var_success_log.get().strip(),
                "windowTitle": self.var_window_title.get().strip(),
                "inspectHints": {
                    "controlName": self.var_control_name.get().strip(),
                    "className": self.var_class_name.get().strip(),
                    "automationId": self.var_automation_id.get().strip(),
                    "controlType": self.var_control_type.get().strip(),
                    "uiPath": self.var_ui_path.get().strip(),
                    "templateKey": self.var_template_key.get().strip(),
                },
                "controls": current_step.get("controls", []) if isinstance(current_step, dict) else [],
                "stepParams": step_params,
                "actionConfig": action_config,
                "auxChecks": self._split_lines(self._get_text(self.aux_checks_text)),
                "fallbacks": self._split_lines(self._get_text(self.fallbacks_text)),
                "notes": self._get_text(self.notes_text),
            },
            self.selected_index or 0,
        )
        validation_errors = validate_step_definition(
            step_payload,
            package_ids={
                str(package.get("id", "")).strip()
                for package in self.flow_packages
                if isinstance(package, dict)
            },
        )
        if validation_errors:
            raise ValueError("\n".join(validation_errors[:8]))
        return step_payload

    def cmd_apply_step(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先在左侧选择一个步骤。")
            return
        old_step_id = str(self.steps[self.selected_index].get("id", "")).strip()
        try:
            updated_step = self._build_step_from_form()
        except Exception as exc:
            messagebox.showerror("应用失败", str(exc))
            return
        new_step_id = str(updated_step.get("id", "")).strip()
        if old_step_id != new_step_id and new_step_id in {
            str(step.get("id", "")).strip() for index, step in enumerate(self.steps) if index != self.selected_index
        }:
            messagebox.showerror("应用失败", f"步骤ID 已存在：{new_step_id}")
            return
        self.steps[self.selected_index] = updated_step
        package_changed = self._rename_step_id_in_packages(old_step_id, new_step_id)
        self._mark_dirty(f"已应用步骤 #{self.selected_index + 1} 的修改")
        if package_changed:
            self.status_var.set(f"已应用步骤 #{self.selected_index + 1} 的修改，并同步更新流程包引用")
            self._refresh_flow_packages_view()
        self._refresh_steps_tree()
        self._select_step(self.selected_index)
        self._refresh_overview()

    def cmd_reload_step(self):
        if self.selected_index is None:
            return
        self._load_step_into_form(self.steps[self.selected_index])
        self.status_var.set("已重置当前步骤表单")

    def _refresh_controls_tree(self, step=None):
        if not hasattr(self, "control_tree"):
            return
        target_step = step if isinstance(step, dict) else (self.steps[self.selected_index] if self.selected_index is not None and 0 <= self.selected_index < len(self.steps) else None)
        self.control_tree.delete(*self.control_tree.get_children())
        controls = target_step.get("controls", []) if target_step else []
        for index, control in enumerate(controls):
            locator = f"{control.get('targetMethod', '')}:{control.get('targetValue', '')}".strip(":")
            self.control_tree.insert("", tk.END, iid=str(index), values=(index + 1, control.get("name", ""), control.get("role", ""), locator))
        self.controls_summary_var.set(f"当前步骤细分控件：{len(controls)}")
        self._refresh_action_control_choices(target_step)

    def _get_selected_control_index(self):
        selection = self.control_tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _import_anchor_control_from_library(self, control):
        """把控件库控件导入当前步骤的细分控件清单，返回导入后的控件（含唯一 ID）。

        锚点控件运行时通过 find_flow_control(step_id, control_id=anchor) 在当前步骤控件清单里定位，
        因此控件库控件必须先落进当前步骤，锚点 ID 才能在 Tab 导航降级时被解析。
        """
        if self.selected_index is None:
            return None
        step = self.steps[self.selected_index]
        step.setdefault("controls", [])
        candidate = normalize_control(control, len(step["controls"]))
        # 锚点控件来源标记（不覆盖已有的 sourceInfo，仅补默认值）
        if not isinstance(candidate.get("sourceInfo"), dict) or not candidate["sourceInfo"]:
            candidate["sourceInfo"] = build_source_info(
                origin="anchor_library",
                library_control_id=str(candidate.get("id", "") or ""),
                library_file_name="",
                imported_by="锚点控件导入",
            )
        original_id = str(candidate.get("id", "")).strip() or "anchor_control"
        existing_ids = {str(ctrl.get("id", "")).strip() for ctrl in step["controls"]}
        unique_id = original_id
        suffix = 2
        while unique_id in existing_ids:
            unique_id = f"{original_id}_{suffix}"
            suffix += 1
        candidate["id"] = unique_id
        if not candidate.get("windowTitle"):
            candidate["windowTitle"] = step.get("windowTitle", "")
        step["controls"].append(candidate)
        self._refresh_controls_tree(step)
        self._refresh_action_control_choices(step)
        self._mark_dirty(f"已从控件库导入锚点控件：{candidate.get('name', '') or unique_id}")
        return candidate

    def _open_control_dialog(self, initial_control=None):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return None
        step = self.steps[self.selected_index]
        # 收集当前流程所有步骤中的控件，供锚点选择弹窗使用
        all_controls = []
        seen_ids = set()
        for s in self.steps:
            for ctrl in s.get("controls", []):
                cid = ctrl.get("id", "")
                if cid and cid not in seen_ids:
                    seen_ids.add(cid)
                    all_controls.append(ctrl)
        dialog = ControlEditorDialog(
            self.root,
            control=initial_control,
            step_name=step.get("name", ""),
            default_window_title=step.get("windowTitle", ""),
            control_library=all_controls,
            on_import_anchor_control=self._import_anchor_control_from_library,
        )
        self.root.wait_window(dialog.window)
        return dialog.result

    def cmd_add_control(self):
        # 未选中步骤时必须先拦住：本函数后面有两处 self.steps[self.selected_index]
        # （控件序号、追加控件），selected_index 为 None 时会抛 TypeError 并被 Tk 回调
        # 吞掉，表现为「点了新增控件没反应」。守卫写法与 cmd_edit_control 保持一致。
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        step = self.steps[self.selected_index]
        existing_controls = step.get("controls", []) if isinstance(step, dict) else []
        new_control = self._open_control_dialog(
            {
                "id": f"{self.var_id.get().strip() or 'step'}_control_{len(existing_controls) + 1}",
                "windowTitle": self.var_window_title.get().strip(),
            }
        )
        if not new_control:
            return
        self.steps[self.selected_index].setdefault("controls", []).append(new_control)
        self._mark_dirty(f"已新增细分控件：{new_control.get('name', '')}")
        self._refresh_controls_tree()
        self._maybe_autoselect_target_control(new_control.get("id", ""))
        self._sync_controls_to_control_library([new_control], step=step, source_label="细分控件新增")
        self._refresh_overview()

    def cmd_edit_control(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        control_index = self._get_selected_control_index()
        if control_index is None:
            messagebox.showinfo("提示", "请先选择一个细分控件。")
            return
        current_control = self.steps[self.selected_index].get("controls", [])[control_index]
        updated_control = self._open_control_dialog(current_control)
        if not updated_control:
            return
        # 用户实际改动过定位/交互字段时，打上「已编辑」来源标记（供后续分析定位识别自定义修改）
        _ignore_fields = {"sourceInfo", "_qualityTier", "_qualityReason"}
        before = {k: v for k, v in current_control.items() if k not in _ignore_fields}
        after = {k: v for k, v in updated_control.items() if k not in _ignore_fields}
        if before != after:
            updated_control = mark_source_edited(updated_control)
        self.steps[self.selected_index]["controls"][control_index] = updated_control
        self._mark_dirty(f"已更新细分控件：{updated_control.get('name', '')}")
        self._refresh_controls_tree()
        if self._get_target_control_id() == str(current_control.get("id", "")).strip():
            self._set_target_control_value(updated_control.get("id", ""))
        self._sync_controls_to_control_library([updated_control], step=self.steps[self.selected_index], source_label="细分控件编辑")
        self.control_tree.selection_set(str(control_index))
        self._refresh_overview()

    def cmd_delete_control(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        control_index = self._get_selected_control_index()
        if control_index is None:
            messagebox.showinfo("提示", "请先选择一个细分控件。")
            return
        controls = self.steps[self.selected_index].get("controls", [])
        control_name = controls[control_index].get("name", "")
        if not messagebox.askyesno("确认删除", f"确定删除细分控件：{control_name} ？"):
            return
        del controls[control_index]
        self._mark_dirty(f"已删除细分控件：{control_name}")
        self._refresh_controls_tree()
        self._refresh_overview()

    def _append_controls_to_selected_step(self, controls, source_label):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return 0
        if not controls:
            return 0
        step = self.steps[self.selected_index]
        added_count = 0
        existing_ids = {control.get("id", "") for control in step.get("controls", [])}
        existing_names = {control.get("name", "") for control in step.get("controls", [])}
        step.setdefault("controls", [])
        for control in controls:
            candidate = normalize_control(control, len(step["controls"]))
            original_id = candidate.get("id", "") or f"{self.var_id.get().strip() or 'step'}_control"
            unique_id = original_id
            suffix = 2
            while unique_id in existing_ids:
                unique_id = f"{original_id}_{suffix}"
                suffix += 1
            candidate["id"] = unique_id
            existing_ids.add(unique_id)

            original_name = candidate.get("name", "") or "新控件"
            unique_name = original_name
            suffix = 2
            while unique_name in existing_names:
                unique_name = f"{original_name} {suffix}"
                suffix += 1
            candidate["name"] = unique_name
            if not candidate.get("windowTitle"):
                candidate["windowTitle"] = step.get("windowTitle", "")
            existing_names.add(unique_name)
            step["controls"].append(candidate)
            added_count += 1

        if added_count:
            self._mark_dirty(f"已通过{source_label}导入 {added_count} 个控件")
            self._refresh_controls_tree(step)
            self._refresh_action_control_choices(step)
            if controls:
                self._maybe_autoselect_target_control(controls[0].get("id", ""))
            self._sync_controls_to_control_library(controls, step=step, source_label=source_label)
            self._refresh_overview()
        return added_count

    def _sync_controls_to_control_library(self, controls, step=None, source_label="编辑器"):
        step = step if isinstance(step, dict) else (self.steps[self.selected_index] if self.selected_index is not None and 0 <= self.selected_index < len(self.steps) else {})
        normalized_controls = [
            _build_control_library_control_entry(control, step)
            for control in (controls or [])
            if isinstance(control, dict) and str(control.get("id", "")).strip()
        ]
        if not normalized_controls:
            return {"files": 0, "added": 0, "updated": 0}
        grouped_controls = {}
        for control in normalized_controls:
            category = _build_control_library_category(control, step)
            file_path = os.path.join(CONTROL_MAP_DIR, "library", category["fileName"])
            if not os.path.exists(os.path.dirname(file_path)):
                os.makedirs(os.path.dirname(file_path))
            grouped_controls.setdefault(file_path, {"category": category, "controls": []})
            grouped_controls[file_path]["controls"].append(control)
        total_added = 0
        total_updated = 0
        for file_path, item in grouped_controls.items():
            payload = load_json_file(file_path)
            payload = _build_control_library_payload(item["category"], payload)
            added_count, updated_count = _merge_controls_into_library_payload(payload, item["controls"])
            save_json_file(file_path, payload)
            total_added += added_count
            total_updated += updated_count
        self.status_var.set(
            f"已将 {len(normalized_controls)} 个控件自动收录到控件库：新增 {total_added}，更新 {total_updated}，来源={source_label}"
        )
        return {"files": len(grouped_controls), "added": total_added, "updated": total_updated}

    def cmd_import_control_from_clipboard(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        try:
            raw_text = self.root.clipboard_get()
        except Exception as exc:
            messagebox.showerror("读取失败", f"读取剪贴板失败：\n{exc}")
            return
        suggested_name = "Inspect导入控件"
        parsed = parse_inspect_text(raw_text)
        if parsed.get("name"):
            suggested_name = parsed.get("name")
        new_control = self._open_control_dialog(
            {
                "id": f"{self.var_id.get().strip() or 'step'}_control_{len(self.steps[self.selected_index].get('controls', [])) + 1}",
                "name": suggested_name,
                "windowTitle": self.var_window_title.get().strip(),
                "rawInspectText": raw_text,
            }
        )
        if not new_control:
            return
        self._append_controls_to_selected_step([new_control], "剪贴板")

    def cmd_open_semi_auto_collector(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        step = self.steps[self.selected_index]
        dialog = SemiAutoInspectCollectorDialog(
            self.root,
            existing_controls=step.get("controls", []),
            step_name=step.get("name", ""),
            default_window_title=step.get("windowTitle", ""),
        )
        self.root.wait_window(dialog.window)
        if not dialog.result:
            return
        self._append_controls_to_selected_step(dialog.result, "半自动采集")

    def cmd_open_control_map_builder(self):
        if not os.path.exists(CONTROL_MAP_BUILDER_SCRIPT):
            messagebox.showerror("打开失败", f"未找到控件库采集器：\n{CONTROL_MAP_BUILDER_SCRIPT}")
            return
        try:
            _launch_python_script(CONTROL_MAP_BUILDER_SCRIPT)
            self.status_var.set("已打开控件库采集器，可先扫描窗口并保存到 control_maps。")
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动控件库采集器失败：\n{exc}")

    def cmd_open_control_locator_tester(self):
        """打开控件定位检验器，验证控件是否能正确定位到目标"""
        dialog = ControlLocatorTesterDialog(self.root)
        self.root.wait_window(dialog.window)

    def cmd_import_control_from_control_map(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        dialog = ControlMapImportDialog(
            self.root,
            default_window_title=self.steps[self.selected_index].get("windowTitle", ""),
        )
        self.root.wait_window(dialog.window)
        if not dialog.result:
            return
        self._append_controls_to_selected_step(dialog.result, "控件库")

    def _sync_control_to_step_hints_by_control(self, control):
        control = control or {}
        inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
        self.var_control_name.set(control.get("name", "") or inspect_data.get("name", ""))
        self.var_class_name.set(inspect_data.get("className", ""))
        self.var_automation_id.set(inspect_data.get("automationId", ""))
        self.var_control_type.set(
            normalize_control_type_name(
                inspect_data.get("controlType", ""),
                inspect_data.get("localizedControlType", ""),
            )
        )
        self.var_ui_path.set(control.get("uiPath", ""))
        self.var_template_key.set(control.get("templateKey", ""))

    def cmd_match_control_from_control_map(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        step = self.steps[self.selected_index]
        dialog = ControlMapImportDialog(
            self.root,
            default_window_title=step.get("windowTitle", ""),
        )
        self.root.wait_window(dialog.window)
        if not dialog.result:
            return
        before_count = len(step.get("controls", []))
        added_count = self._append_controls_to_selected_step(dialog.result, "控件库搜索匹配")
        if not added_count:
            return
        added_controls = step.get("controls", [])[before_count:before_count + added_count]
        primary_control = added_controls[0] if added_controls else step.get("controls", [])[-1]
        primary_control_id = str(primary_control.get("id", "")).strip()
        if primary_control_id:
            self.var_target_control_id.set(primary_control_id)
        self._sync_control_to_step_hints_by_control(primary_control)
        if hasattr(self, "control_tree") and before_count < len(step.get("controls", [])):
            self.control_tree.selection_set(str(before_count))
            self.control_tree.see(str(before_count))
        self.status_var.set(
            f"已从控件库搜索匹配导入 {added_count} 个控件，并将动作目标切换到：{primary_control.get('name', '') or primary_control_id}"
        )

    def cmd_sync_control_to_step_hints(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        control_index = self._get_selected_control_index()
        if control_index is None:
            messagebox.showinfo("提示", "请先选择一个细分控件。")
            return
        control = self.steps[self.selected_index].get("controls", [])[control_index]
        self._sync_control_to_step_hints_by_control(control)
        self._mark_dirty(f"已同步细分控件到步骤定位：{control.get('name', '')}")

    def _extract_step_control_aid(self, control):
        """委托共享修复层提取 automationId（与 flow_repair 同源）。"""
        from WT_AUTOMATION_Agent import flow_repair

        return flow_repair.extract_step_control_aid(control)

    def _pick_library_match(self, candidates, control, flat_controls):
        """委托共享修复层挑选多实例控件库匹配（与 flow_repair 同源）。"""
        from WT_AUTOMATION_Agent import flow_repair

        return flow_repair.pick_library_match(candidates, control, flat_controls)

    def _apply_library_definition(self, control, rec):
        """委托共享修复层回填控件库定义（与 flow_repair 同源）。"""
        from WT_AUTOMATION_Agent import flow_repair

        return flow_repair.apply_library_definition(control, rec)

    def cmd_refresh_step_controls_from_library(self):
        """批量刷新步骤控件定位：按 automationId 从合并后的总控件库匹配最新定义，
        覆盖 targetMethod/targetValue/helpText/functionText，一键同步全部已绑定步骤。"""
        if not self.steps:
            messagebox.showinfo("提示", "当前流程没有步骤，无需刷新。", parent=self.root)
            return
        if not os.path.exists(MASTER_CONTROL_FILE):
            messagebox.showinfo(
                "提示",
                f"未找到总控件库：\n{MASTER_CONTROL_FILE}\n\n请先在控件库界面执行『合并去重并保存』生成总控件信息。",
                parent=self.root,
            )
            return
        master_data = load_json_file(MASTER_CONTROL_FILE)
        flat_controls = master_data.get("flatControls", []) if isinstance(master_data, dict) else []
        if not flat_controls:
            messagebox.showinfo("提示", "总控件库为空，请先采集并合并控件。", parent=self.root)
            return

        by_aid = {}
        for idx, rec in enumerate(flat_controls):
            if not isinstance(rec, dict):
                continue
            rec_ins = rec.get("inspectData", {}) if isinstance(rec.get("inspectData"), dict) else {}
            rec_aid = str(rec.get("automationId", "") or rec_ins.get("automationId", "")).strip().lower()
            if rec_aid:
                by_aid.setdefault(rec_aid, []).append(idx)

        updated, unmatched, skipped = 0, 0, 0
        details = []
        for step in self.steps:
            if not isinstance(step, dict):
                continue
            controls = step.get("controls", [])
            if not isinstance(controls, list):
                continue
            for control in controls:
                if not isinstance(control, dict):
                    continue
                aid = self._extract_step_control_aid(control)
                if not aid:
                    skipped += 1
                    continue
                candidates = by_aid.get(aid.lower())
                if not candidates:
                    unmatched += 1
                    continue
                match_idx = self._pick_library_match(candidates, control, flat_controls)
                if match_idx is None:
                    unmatched += 1
                    continue
                if self._apply_library_definition(control, flat_controls[match_idx]):
                    updated += 1
                    details.append(control.get("name", "") or aid)

        if updated == 0:
            messagebox.showinfo(
                "刷新完成",
                f"扫描 {len(self.steps)} 个步骤，未发现可更新的控件（匹配 0，未匹配 {unmatched}，无 automationId 跳过 {skipped}）。",
                parent=self.root,
            )
            return
        self._mark_dirty(f"批量刷新步骤定位：更新 {updated} 个控件，未匹配 {unmatched} 个")
        if self.selected_index is not None:
            self._refresh_controls_tree()
        preview = "\n".join(details[:10]) + ("…" if len(details) > 10 else "")
        messagebox.showinfo(
            "刷新完成",
            f"已更新 {updated} 个步骤控件的定位，未匹配 {unmatched} 个（无 automationId 跳过 {skipped} 个）：\n\n{preview}",
            parent=self.root,
        )

    def cmd_repair_flow_audit_issues(self):
        """一键修复审核问题：确定性修复当前步骤 + 展示待确认项。"""
        if not self.steps:
            messagebox.showinfo("提示", "当前流程没有步骤，无需修复。", parent=self.root)
            return
        try:
            from WT_AUTOMATION_Agent import flow_repair

            repaired_flow, report = flow_repair.repair_flow_definition({"steps": self.steps}, MASTER_CONTROL_FILE)
        except Exception as exc:
            messagebox.showerror("修复失败", str(exc), parent=self.root)
            return
        repaired_steps = repaired_flow.get("steps", [])
        if not isinstance(repaired_steps, list) or len(repaired_steps) != len(self.steps):
            messagebox.showerror("修复失败", "修复模块返回的步骤数量不一致，已取消应用。", parent=self.root)
            return
        self.steps = repaired_steps
        auto_count = int(report.get("auto_fixed_count", 0) or 0)
        pending_count = int(report.get("pending_confirm_count", 0) or 0)
        if auto_count == 0 and pending_count == 0:
            messagebox.showinfo("修复完成", "未发现可自动修复或需要确认的问题。", parent=self.root)
            return
        self._mark_dirty(f"一键修复审核问题：自动修复 {auto_count} 项，待确认 {pending_count} 项")
        if self.selected_index is not None:
            self._refresh_controls_tree()
        pending_preview = "\n".join(
            f"- 步骤{it.get('step_index', '')} {it.get('step_name', '')}: {it.get('message', '')}"
            for it in (report.get("pending_confirm", []) or [])[:10]
        )
        messagebox.showinfo(
            "修复完成",
            f"已自动修复 {auto_count} 项；仍有 {pending_count} 项待确认：\n\n{pending_preview or '（无）'}\n\n请保存流程文件后再次运行检查确认。",
            parent=self.root,
        )

    def cmd_add_flow_package(self):
        result = self._open_flow_package_dialog()
        if not result:
            return
        package_id = result.get("id", "")
        if package_id in {package.get("id", "") for package in self.flow_packages}:
            messagebox.showerror("新增失败", f"流程包ID 已存在：{package_id}")
            return
        self.flow_packages.append(normalize_flow_packages([result])[0])
        self._mark_dirty(f"已新增流程包：{result.get('name', '')}")
        self._refresh_flow_packages_view()
        self._refresh_overview()

    def cmd_edit_flow_package(self):
        package_index = self._get_selected_package_index()
        if package_index is None:
            messagebox.showinfo("提示", "请先选择一个流程包。")
            return
        current_package = self.flow_packages[package_index]
        result = self._open_flow_package_dialog(current_package)
        if not result:
            return
        new_package_id = result.get("id", "")
        if new_package_id in {
            package.get("id", "") for index, package in enumerate(self.flow_packages) if index != package_index
        }:
            messagebox.showerror("更新失败", f"流程包ID 已存在：{new_package_id}")
            return
        old_package_id = current_package.get("id", "")
        self.flow_packages[package_index] = normalize_flow_packages([result])[0]
        if self._rename_package_ref_in_steps(old_package_id, new_package_id):
            self.dirty = True
            self.status_var.set(f"已更新流程包，并同步修正 flow_ref 引用：{new_package_id}")
        else:
            self._mark_dirty(f"已更新流程包：{result.get('name', '')}")
        self._refresh_flow_packages_view()
        self._refresh_overview()
        if self.selected_index is not None:
            self._load_step_into_form(self.steps[self.selected_index])

    def cmd_delete_flow_package(self):
        package_index = self._get_selected_package_index()
        if package_index is None:
            messagebox.showinfo("提示", "请先选择一个流程包。")
            return
        current_package = self.flow_packages[package_index]
        package_name = current_package.get("name", "")
        package_id = current_package.get("id", "")
        if not messagebox.askyesno("确认删除", f"确定删除流程包：{package_name} ？"):
            return
        del self.flow_packages[package_index]
        cleared_refs = self._clear_package_ref_in_steps(package_id)
        if cleared_refs:
            self.dirty = True
            self.status_var.set(f"已删除流程包，并清空相关步骤的 packageRef：{package_id}")
        else:
            self._mark_dirty(f"已删除流程包：{package_name}")
        self._refresh_flow_packages_view()
        self._refresh_overview()
        if self.selected_index is not None:
            self._load_step_into_form(self.steps[self.selected_index])

    def cmd_move_flow_package_up(self):
        package_index = self._get_selected_package_index()
        if package_index is None or package_index <= 0:
            return
        self.flow_packages[package_index - 1], self.flow_packages[package_index] = (
            self.flow_packages[package_index],
            self.flow_packages[package_index - 1],
        )
        self._mark_dirty("已上移流程包")
        self._refresh_flow_packages_view()
        self._select_package_by_index(package_index - 1)

    def cmd_move_flow_package_down(self):
        package_index = self._get_selected_package_index()
        if package_index is None or package_index >= len(self.flow_packages) - 1:
            return
        self.flow_packages[package_index + 1], self.flow_packages[package_index] = (
            self.flow_packages[package_index],
            self.flow_packages[package_index + 1],
        )
        self._mark_dirty("已下移流程包")
        self._refresh_flow_packages_view()
        self._select_package_by_index(package_index + 1)

    def _select_package_by_index(self, index):
        if not hasattr(self, "package_tree") or not (0 <= index < len(self.flow_packages)):
            return
        self.package_tree.selection_set(str(index))
        self.package_tree.see(str(index))

    def cmd_insert_step_template(self):
        template_definition = self._get_selected_template_definition()
        if not template_definition:
            messagebox.showinfo("提示", "请先在右侧选择一个步骤模板。")
            return
        self._insert_step_from_template_definition(template_definition)

    def cmd_apply_step_template(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        template_definition = self._get_selected_template_definition()
        if not template_definition:
            messagebox.showinfo("提示", "请先在右侧选择一个步骤模板。")
            return
        should_continue = messagebox.askyesno(
            "确认套用",
            "套用模板会覆盖当前步骤的 actionType、strategy、Action 配置等模板字段。\n确定继续吗？",
        )
        if not should_continue:
            return
        current_step = json.loads(json.dumps(self.steps[self.selected_index], ensure_ascii=False))
        template_step = self._build_step_from_template(template_definition, self.selected_index)
        template_step["id"] = current_step.get("id", "")
        template_step["name"] = current_step.get("name", "") or template_step.get("name", "")
        if current_step.get("stage"):
            template_step["stage"] = current_step.get("stage", "")
        if current_step.get("windowTitle") and not template_step.get("windowTitle"):
            template_step["windowTitle"] = current_step.get("windowTitle", "")
        if current_step.get("controls") and not template_step.get("controls"):
            template_step["controls"] = current_step.get("controls", [])
        if current_step.get("inspectHints") and not template_step.get("inspectHints", {}).get("controlName"):
            template_step["inspectHints"] = current_step.get("inspectHints", {})
        self.steps[self.selected_index] = normalize_step(template_step, self.selected_index)
        self._mark_dirty(f"已套用模板到当前步骤：{template_definition.get('name', '')}")
        self._select_step(self.selected_index)
        self._refresh_steps_tree()
        self._refresh_overview()

    def cmd_add_step(self):
        insert_at = self.selected_index + 1 if self.selected_index is not None else len(self.steps)
        new_step = normalize_step(
            {"id": f"step_{len(self.steps) + 1}", "name": "新步骤", "strategy": "script", "actionType": "placeholder"},
            insert_at,
        )
        self.steps.insert(insert_at, new_step)
        self._mark_dirty("已新增步骤")
        self._refresh_steps_tree()
        self._select_step(insert_at)
        self._refresh_overview()

    def cmd_duplicate_step(self):
        if self.selected_index is None:
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        source = json.loads(json.dumps(self.steps[self.selected_index], ensure_ascii=False))
        source["id"] = self._generate_unique_step_id(source.get("id", "") + "_copy")
        source["name"] = source.get("name", "") + " 副本"
        insert_at = self.selected_index + 1
        self.steps.insert(insert_at, normalize_step(source, insert_at))
        self._mark_dirty("已复制步骤")
        self._refresh_steps_tree()
        self._select_step(insert_at)
        self._refresh_overview()

    def cmd_delete_step(self):
        selected_indexes = self._get_selected_step_indexes()
        if not selected_indexes:
            if self.selected_index is not None and 0 <= self.selected_index < len(self.steps):
                selected_indexes = [self.selected_index]
            else:
                messagebox.showinfo("提示", "请先选择一个步骤。")
                return
        step_names = [str(self.steps[index].get("name", "")).strip() or f"步骤 {index + 1}" for index in selected_indexes]
        step_ids = [str(self.steps[index].get("id", "")).strip() for index in selected_indexes]
        if len(selected_indexes) == 1:
            confirm_message = f"确定删除步骤：{step_names[0]} ？"
        else:
            preview_names = "、".join(step_names[:5])
            if len(step_names) > 5:
                preview_names += " 等"
            confirm_message = f"确定批量删除 {len(selected_indexes)} 个步骤：{preview_names} ？"
        if not messagebox.askyesno("确认删除", confirm_message):
            return
        for index in sorted(selected_indexes, reverse=True):
            del self.steps[index]
        package_changed = False
        for step_id in step_ids:
            if self._remove_step_id_from_packages(step_id):
                package_changed = True
        next_index = min(selected_indexes[0], len(self.steps) - 1) if self.steps else None
        self.selected_index = None
        deleted_count = len(selected_indexes)
        self._mark_dirty(f"已删除 {deleted_count} 个步骤" if deleted_count > 1 else "已删除步骤")
        if package_changed:
            self.status_var.set(f"已删除 {deleted_count} 个步骤，并同步移除流程包内引用")
            self._refresh_flow_packages_view()
        self._refresh_steps_tree()
        self._refresh_overview()
        if next_index is not None:
            self._select_step(next_index)
        return

    def cmd_move_up(self):
        if self.selected_index is None or self.selected_index == 0:
            return
        index = self.selected_index
        self.steps[index - 1], self.steps[index] = self.steps[index], self.steps[index - 1]
        self._mark_dirty("已上移步骤")
        self._refresh_steps_tree()
        self._select_step(index - 1)
        self._refresh_overview()

    def cmd_move_down(self):
        if self.selected_index is None or self.selected_index >= len(self.steps) - 1:
            return
        index = self.selected_index
        self.steps[index + 1], self.steps[index] = self.steps[index], self.steps[index + 1]
        self._mark_dirty("已下移步骤")
        self._refresh_steps_tree()
        self._select_step(index + 1)
        self._refresh_overview()

    def _on_step_filter_changed(self):
        self._refresh_steps_tree()
        visible = self._get_visible_step_indexes()
        if visible and (self.selected_index is None or self.selected_index not in visible):
            self._select_step(visible[0])

    def _show_step_context_menu(self, event):
        row_id = self.step_tree.identify_row(event.y)
        if row_id:
            sel = self.step_tree.selection()
            if row_id not in sel:
                self.step_tree.selection_set(row_id)
                try:
                    self._select_step(int(row_id))
                except Exception:
                    pass
        elif self.selected_index is None:
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="📋 复制步骤 (Ctrl+C)", command=self.cmd_copy_selected_step)
        menu.add_command(label="📥 粘贴到下方 (Ctrl+V)", command=self.cmd_paste_step_below)
        menu.add_command(label="📑 快速克隆 (Ctrl+D)", command=self.cmd_duplicate_step)
        menu.add_separator()
        menu.add_command(label="🔘 切换启用/停用 (Space)", command=self.cmd_toggle_selected_step_enabled)
        menu.add_command(label="➕ 在下方插入新步骤", command=self.cmd_insert_new_step_below)
        menu.add_separator()
        menu.add_command(label="⬆ 上移步骤 (Alt+Up)", command=self.cmd_move_up)
        menu.add_command(label="⬇ 下移步骤 (Alt+Down)", command=self.cmd_move_down)
        menu.add_separator()
        menu.add_command(label="🆔 复制步骤ID", command=self.cmd_copy_step_id)
        menu.add_command(label="{ } 复制步骤完整JSON", command=self.cmd_copy_step_json)
        menu.add_separator()
        menu.add_command(label="🗑️ 删除所选步骤 (Del)", command=self.cmd_delete_step)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def cmd_copy_selected_step(self):
        if self.selected_index is None or not (0 <= self.selected_index < len(self.steps)):
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        step = self.steps[self.selected_index]
        self._step_clipboard = json.loads(json.dumps(step, ensure_ascii=False))
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(json.dumps(self._step_clipboard, ensure_ascii=False, indent=2))
        except Exception:
            pass
        self.status_var.set(f"已复制步骤：{step.get('name', '')} 到剪贴板")

    def cmd_paste_step_below(self):
        source = getattr(self, "_step_clipboard", None)
        if not source:
            try:
                clip_text = self.root.clipboard_get()
                parsed = json.loads(clip_text)
                if isinstance(parsed, dict) and "name" in parsed:
                    source = parsed
            except Exception:
                pass
        if not source:
            messagebox.showinfo("提示", "剪贴板中没有可粘贴的步骤数据。")
            return
        clone = json.loads(json.dumps(source, ensure_ascii=False))
        clone["id"] = self._generate_unique_step_id(clone.get("id", "step") + "_copy")
        clone["name"] = clone.get("name", "") + " (粘贴)"
        insert_at = (self.selected_index + 1) if (self.selected_index is not None and 0 <= self.selected_index < len(self.steps)) else len(self.steps)
        self.steps.insert(insert_at, normalize_step(clone, insert_at))
        self._mark_dirty(f"已粘贴步骤：{clone.get('name', '')}")
        self._refresh_steps_tree()
        self._select_step(insert_at)
        self._refresh_overview()

    def cmd_toggle_selected_step_enabled(self):
        selected_indexes = self._get_selected_step_indexes()
        if not selected_indexes and self.selected_index is not None:
            selected_indexes = [self.selected_index]
        if not selected_indexes:
            return
        for idx in selected_indexes:
            cur = self.steps[idx].get("enabled", True)
            self.steps[idx]["enabled"] = not cur
            if idx == self.selected_index:
                self.var_enabled.set(self.steps[idx]["enabled"])
        self._mark_dirty(f"已切换 {len(selected_indexes)} 个步骤的启用状态")
        self._refresh_steps_tree()
        if self.selected_index is not None and str(self.selected_index) in self.step_tree.get_children():
            self.step_tree.selection_set(str(self.selected_index))
            self.step_tree.see(str(self.selected_index))

    def cmd_copy_step_id(self):
        if self.selected_index is None or not (0 <= self.selected_index < len(self.steps)):
            return
        step_id = str(self.steps[self.selected_index].get("id", "")).strip()
        if step_id:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(step_id)
                self.status_var.set(f"已复制步骤ID：{step_id}")
            except Exception:
                pass

    def cmd_copy_step_json(self):
        if self.selected_index is None or not (0 <= self.selected_index < len(self.steps)):
            return
        step = self.steps[self.selected_index]
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(json.dumps(step, ensure_ascii=False, indent=2))
            self.status_var.set(f"已复制步骤 [{step.get('name', '')}] 的完整 JSON")
        except Exception:
            pass

    def cmd_insert_new_step_below(self):
        insert_at = (self.selected_index + 1) if (self.selected_index is not None and 0 <= self.selected_index < len(self.steps)) else len(self.steps)
        new_step = normalize_step(
            {"id": self._generate_unique_step_id("step"), "name": "新步骤", "strategy": "script", "actionType": "action"},
            insert_at,
        )
        self.steps.insert(insert_at, new_step)
        self._mark_dirty("已在下方插入新步骤")
        self._refresh_steps_tree()
        self._select_step(insert_at)
        self._refresh_overview()

    def _open_target_control_picker(self):
        if self.selected_index is None or not (0 <= self.selected_index < len(self.steps)):
            messagebox.showinfo("提示", "请先选择一个步骤。")
            return
        step = self.steps[self.selected_index]
        all_flow_controls = []
        for s in self.steps:
            all_flow_controls.extend(s.get("controls", []))
        picker = ControlPickerDialog(
            self.root,
            controls=all_flow_controls,
            library_controls=_load_control_library_controls(),
        )
        self.root.wait_window(picker.window)
        if picker.selected_control is None:
            return
        picked_ctrl = picker.selected_control
        picked_id = str(picked_ctrl.get("id", "")).strip()
        if not picked_id:
            return

        # 确保当前步骤包含该控件
        step_ctrl_ids = {str(c.get("id", "")).strip() for c in step.get("controls", [])}
        if picked_id not in step_ctrl_ids:
            self._append_controls_to_selected_step([picked_ctrl], "控件选择器")

        self.var_target_control_id.set(picked_id)
        self._sync_control_to_step_hints_by_control(picked_ctrl)
        self.status_var.set(f"已选择目标控件：{picked_ctrl.get('name', '') or picked_id}")
        self._update_step_header_display()

    def _show_target_control_info(self):
        ctrl_id = self.var_target_control_id.get().strip()
        if not ctrl_id:
            messagebox.showinfo("提示", "当前步骤尚未指定目标控件。")
            return
        found_ctrl = None
        if self.selected_index is not None and 0 <= self.selected_index < len(self.steps):
            for c in self.steps[self.selected_index].get("controls", []):
                if str(c.get("id", "")).strip() == ctrl_id:
                    found_ctrl = c
                    break
        if not found_ctrl:
            for s in self.steps:
                for c in s.get("controls", []):
                    if str(c.get("id", "")).strip() == ctrl_id:
                        found_ctrl = c
                        break
                if found_ctrl:
                    break
        if not found_ctrl:
            lib = _load_control_library_controls()
            for c in lib:
                if str(c.get("id", "")).strip() == ctrl_id:
                    found_ctrl = c
                    break
        if not found_ctrl:
            messagebox.showinfo("控件信息", f"未找到控件 [{ctrl_id}] 的详细信息。")
            return

        inspect = found_ctrl.get("inspectData", {}) if isinstance(found_ctrl.get("inspectData"), dict) else {}
        info_lines = [
            f"控件 ID: {found_ctrl.get('id', '')}",
            f"控件名称: {found_ctrl.get('name', '')}",
            f"所属窗口: {found_ctrl.get('windowTitle', '') or inspect.get('windowTitle', '')}",
            f"automationId: {inspect.get('automationId', '') or found_ctrl.get('automationId', '')}",
            f"className: {inspect.get('className', '') or found_ctrl.get('className', '')}",
            f"controlType: {inspect.get('controlType', '') or found_ctrl.get('controlType', '')}",
            f"uiPath: {found_ctrl.get('uiPath', '') or inspect.get('uiPath', '')}",
            f"boundingRect: {inspect.get('boundingRect', '')}",
        ]
        messagebox.showinfo("目标控件详情", "\n".join(info_lines))

    def _update_step_header_display(self):
        if self.selected_index is not None and 0 <= self.selected_index < len(self.steps):
            step = self.steps[self.selected_index]
            seq = self.selected_index + 1
            name = self.var_name.get().strip() or step.get("name", "")
            action = self.var_action.get().strip() if self.var_action_type.get() == "action" else self.var_action_type.get()
            self.step_title_display_var.set(f"步骤 #{seq:02d}: {name} [{action}]")
        else:
            self.step_title_display_var.set("未选择步骤")

        is_dirty = self._is_form_dirty()
        if is_dirty:
            self.step_dirty_badge_var.set("● 表单已修改 (未保存)")
            if hasattr(self, "step_form_dirty_badge"):
                self.step_form_dirty_badge.configure(bg="#fef3c7", fg="#b45309")
        else:
            self.step_dirty_badge_var.set("✓ 已同步")
            if hasattr(self, "step_form_dirty_badge"):
                self.step_form_dirty_badge.configure(bg="#f0fdf4", fg="#16a34a")

    def _is_form_dirty(self):
        if self.selected_index is None:
            return False
        baseline = getattr(self, "_form_baseline", None)
        if baseline is None:
            return False
        return self._form_snapshot() != baseline


    def cmd_renumber_step_ids(self):
        """一键按当前步骤顺序重新编号所有步骤ID，并同步更新流程包引用。"""
        if not self.steps:
            messagebox.showinfo("提示", "当前没有步骤可排序。")
            return
        if not messagebox.askyesno(
            "确认重排序ID",
            "将按当前顺序重新编号所有步骤ID（如 step_1, step_2, ...）。\n"
            "流程包内的步骤引用也会同步更新。\n\n确定继续吗？",
        ):
            return

        old_to_new = {}
        used_new_ids = set()
        for index, step in enumerate(self.steps, start=1):
            old_id = str(step.get("id", "")).strip()
            new_id = f"step_{index}"
            # 确保不与已有非当前步骤冲突（理论上不会，但安全起见）
            while new_id in used_new_ids:
                index += 1
                new_id = f"step_{index}"
            used_new_ids.add(new_id)
            if old_id and old_id != new_id:
                old_to_new[old_id] = new_id
            step["id"] = new_id

        # 同步更新流程包引用
        package_changed = False
        for old_id, new_id in old_to_new.items():
            if self._rename_step_id_in_packages(old_id, new_id):
                package_changed = True

        changed_count = len(old_to_new)
        if changed_count > 0:
            self._mark_dirty(f"已重排序 {changed_count} 个步骤ID")
            if package_changed:
                self._refresh_flow_packages_view()
            self._refresh_steps_tree()
            if self.selected_index is not None:
                self._select_step(self.selected_index)
            self._refresh_overview()
            messagebox.showinfo("完成", f"已重新编号 {changed_count} 个步骤ID。", parent=self.root)
        else:
            messagebox.showinfo("完成", "所有步骤ID已是顺序排列，无需调整。", parent=self.root)

    def cmd_new_default(self):
        if self.dirty and not messagebox.askyesno("确认", "当前修改未保存，确定新建默认链路吗？"):
            return
        last_new = load_editor_state().get("newDefaultChain", {})
        if not isinstance(last_new, dict):
            last_new = {}
        last_directory = str(last_new.get("directory", "") or "")
        last_name = str(last_new.get("name", "") or "")

        dialog = NewDefaultChainDialog(self.root, last_directory=last_directory, last_name=last_name)
        if not dialog.result:
            return
        target_path = dialog.result["path"]
        directory = dialog.result["directory"]
        name = dialog.result["name"]

        # 持久化本次输入，供后续新建作为默认值
        editor_state = load_editor_state()
        if not isinstance(editor_state, dict):
            editor_state = {}
        editor_state["newDefaultChain"] = {"directory": directory, "name": name}
        save_editor_state(editor_state)

        self.flow_definition = self._load_or_default_definition("")
        self.steps = self.flow_definition["steps"]
        self._load_runtime_config_into_form(self.flow_definition.get("runtimeConfig", {}))
        self._load_flow_packages_into_form(self.flow_definition.get("flowPackages", []))
        self.definition_path = target_path
        self.path_var.set(self.definition_path)
        self.selected_index = None
        self.dirty = True
        self._refresh_steps_tree()
        self._refresh_overview()
        if self.steps:
            self._select_step(0)
        self._set_title()
        self.status_var.set(f"已新建默认链路，保存位置：{target_path}（编辑后请点击保存）")

    def _load_definition_into_editor(self, flow_definition, file_path, status_message=""):
        self.flow_definition = flow_definition
        self.steps = self.flow_definition["steps"]
        self._load_runtime_config_into_form(self.flow_definition.get("runtimeConfig", {}))
        self._load_flow_packages_into_form(self.flow_definition.get("flowPackages", []))
        self.definition_path = file_path
        self.path_var.set(file_path)
        self.selected_index = None
        self.dirty = False
        self._refresh_steps_tree()
        self._refresh_overview()
        self._set_title()
        if self.steps:
            self._select_step(0)
        else:
            self._clear_step_form()
        if status_message:
            self.status_var.set(status_message)

    def _clear_step_form(self):
        self.selected_index = None
        self.var_id.set("")
        self.var_name.set("")
        self.var_stage.set("")
        self.var_strategy.set("script")
        self.var_action_type.set("script")
        self.var_enabled.set(True)
        self.var_code_symbol.set("")
        self.var_code_reference.set("")
        self.var_package_ref.set("")
        self.var_success_log.set("")
        self.var_window_title.set("")
        self.var_control_name.set("")
        self.var_class_name.set("")
        self.var_automation_id.set("")
        self.var_control_type.set("")
        self.var_ui_path.set("")
        self.var_template_key.set("")
        self._refresh_controls_tree({})
        self._refresh_action_control_choices({})
        self._load_action_editor_from_config({})
        self._set_text(self.description_text, "")
        self._set_text(self.aux_checks_text, "")
        self._set_text(self.fallbacks_text, "")
        self._set_text(self.notes_text, "")
        self._set_text(self.step_params_text, "{}")
        self._set_text(self.action_config_text, "{}")
        if hasattr(self, "step_tree"):
            self._suppress_tree_select_event = True
            try:
                self.step_tree.selection_remove(*self.step_tree.selection())
            finally:
                self._suppress_tree_select_event = False

    def _build_recorder_output_path(self, script_path):
        script_base_name = os.path.splitext(os.path.basename(script_path or ""))[0].strip() or "converted_recorder"
        os.makedirs(RECORDER_CONVERTED_DIR, exist_ok=True)
        return os.path.join(RECORDER_CONVERTED_DIR, f"{script_base_name}_flow.json")

    def cmd_convert_recorder_script(self):
        if self.dirty and not messagebox.askyesno("确认", "当前链路有未保存修改，确定先执行 Recorder 转换并切换到新结果吗？"):
            return
        script_path = filedialog.askopenfilename(
            title="选择 Recorder Python 脚本",
            initialdir=BASE_DIR,
            filetypes=[("Python 脚本", "*.py"), ("所有文件", "*.*")],
        )
        if not script_path:
            return
        output_path = self._build_recorder_output_path(script_path)
        # 推断截图目录：优先脚本同级 screenshots 子目录，否则回退到脚本所在目录
        script_dir = os.path.dirname(script_path)
        screenshot_candidate = os.path.join(script_dir, "screenshots")
        screenshot_dir = screenshot_candidate if os.path.isdir(screenshot_candidate) else script_dir
        # 若输出文件已存在，启用增量转换，保留上次的人工修正与反馈历史
        previous_flow_path = output_path if os.path.isfile(output_path) else None
        try:
            converted_payload = convert_recorder_script_to_flow(
                script_path,
                output_json_path=output_path,
                control_map_dir=CONTROL_MAP_DIR,
                screenshot_dir=screenshot_dir,
                previous_flow_path=previous_flow_path,
            )
            sync_flow_package_registry(
                output_path,
                converted_payload.get("runtimeConfig", {}),
                converted_payload.get("flowPackages", []),
                converted_payload.get("steps", []),
            )
            sync_launcher_flow_definition_path(output_path)
            flow_definition = self._load_or_default_definition(output_path)
        except Exception as exc:
            messagebox.showerror("转换失败", str(exc))
            return
        conversion_meta = converted_payload.get("conversionMeta", {}) if isinstance(converted_payload, dict) else {}
        total_steps = int(conversion_meta.get("totalSteps", 0) or 0)
        action_steps = int(conversion_meta.get("actionSteps", 0) or 0)
        matched_count = int(conversion_meta.get("controlMapMatchedCount", 0) or 0)
        suspicious_count = int(conversion_meta.get("suspiciousStepCount", 0) or 0)
        runtime_binding_count = int(conversion_meta.get("runtimeParamBindings", 0) or 0)
        screenshot_linked = int(conversion_meta.get("screenshotLinkedCount", 0) or 0)
        incr_merged = int(conversion_meta.get("incrementalMerged", 0) or 0)
        incr_new = int(conversion_meta.get("incrementalNew", 0) or 0)
        incr_removed = int(conversion_meta.get("incrementalRemoved", 0) or 0)
        auto_fixed_count = int(conversion_meta.get("autoFixedCount", 0) or 0)
        pending_confirm_count = int(conversion_meta.get("pendingConfirmCount", 0) or 0)
        incr_suffix = (
            f" | 增量合并={incr_merged}/新增={incr_new}/移除={incr_removed}"
            if (incr_merged or incr_new or incr_removed) else ""
        )
        self._load_definition_into_editor(
            flow_definition,
            output_path,
            status_message=(
                f"已完成 Recorder 转换并自动打开：{os.path.basename(output_path)}"
                f" | 步骤={total_steps} | action={action_steps} | 控件库命中={matched_count}"
                f" | 参数抽取={runtime_binding_count} | 截图关联={screenshot_linked} | 待复核={suspicious_count}"
                f" | 自动修复={auto_fixed_count} | 待确认={pending_confirm_count}"
                f"{incr_suffix}"
            ),
        )
        # P2: 若生成了视觉质量报告，提供一键打开
        quality_report_path = str(conversion_meta.get("qualityReportPath", "") or "").strip()
        if quality_report_path and os.path.isfile(quality_report_path):
            if messagebox.askyesno("质量报告", f"已生成转换质量报告：\n{os.path.basename(quality_report_path)}\n\n是否立即在浏览器中打开？"):
                try:
                    os.startfile(quality_report_path)
                except Exception as report_exc:
                    messagebox.showwarning("打开失败", f"无法打开质量报告：{report_exc}")

    def cmd_open(self):
        if self.dirty and not messagebox.askyesno("确认", "当前链路有未保存修改，确定继续打开其他链路文件吗？"):
            return
        file_path = filedialog.askopenfilename(
            title="打开流程链路文件",
            initialdir=BASE_DIR,
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not file_path:
            return
        try:
            flow_definition = self._load_or_default_definition(file_path)
        except Exception as exc:
            messagebox.showerror("打开失败", str(exc))
            return
        self._load_definition_into_editor(
            flow_definition,
            file_path,
            status_message=f"已打开流程链路文件：{os.path.basename(file_path)}",
        )

    def cmd_save(self):
        if not self.definition_path:
            self.cmd_save_as()
            return
        self._save_to(self.definition_path)

    def cmd_save_as(self):
        file_path = filedialog.asksaveasfilename(
            title="另存为流程链路文件",
            initialdir=BASE_DIR,
            initialfile=os.path.basename(self.definition_path or FLOW_DEFINITION_FILE),
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
        )
        if not file_path:
            return
        self._save_to(file_path)

    def _save_to(self, file_path):
        try:
            if self.selected_index is not None:
                self.steps[self.selected_index] = self._build_step_from_form()
            self.flow_definition["runtimeConfig"] = self._build_runtime_config_from_form()
            self.flow_definition["flowPackages"] = self._build_flow_packages_from_form()
            self.flow_definition["steps"] = [normalize_step(step, index) for index, step in enumerate(self.steps)]
            # 自动关联模板兜底：步骤未显式配置 fallbackTemplate 且控件存在可解析的
            # templateKey（采集器/伴随拾取已回填）时，自动写入相对项目根的模板路径。
            if image_template_index is not None:
                try:
                    image_template_index.auto_associate_fallback_templates(self.flow_definition.get("steps"))
                except Exception:
                    pass
            validation_errors = validate_flow_definition(self.flow_definition)
            if validation_errors:
                raise ValueError("保存前校验未通过：\n- " + "\n- ".join(validation_errors[:12]))
            save_json_file(file_path, self.flow_definition)
            sync_flow_package_registry(
                file_path,
                self.flow_definition.get("runtimeConfig", {}),
                self.flow_definition.get("flowPackages", []),
                self.flow_definition.get("steps", []),
            )
            sync_launcher_flow_definition_path(file_path)
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))
            return
        self.definition_path = file_path
        self.path_var.set(file_path)
        self.dirty = False
        self._refresh_steps_tree()
        self._refresh_overview()
        self.status_var.set(
            "已保存流程链路，并同步流程包仓库："
            f"{os.path.basename(file_path)} -> {os.path.basename(FLOW_PACKAGE_REGISTRY_FILE)}"
        )

    def _toggle_concurrent_mode(self):
        """切换伴随拾取模式的开/关"""
        if self.var_concurrent_mode.get():
            self._start_concurrent_mode()
        else:
            self._stop_concurrent_mode()

    def _start_concurrent_mode(self):
        """启动伴随拾取模式"""
        if self._concurrent_enabled:
            return
        
        # queue / threading 已在模块顶部导入。
        # 原先此处是局部 `import queue as queue_module`，而 _poll_concurrent_events
        # 里用 `except queue_module.Empty` —— 局部名在该函数不可见，
        # 每轮取空队列时都会抛 NameError，被外层 except 吞掉（循环靠异常退出而非 break）。
        try:
            from pynput import keyboard, mouse
        except Exception as exc:
            messagebox.showerror("启动失败", f"无法启动伴随拾取模式：\n{exc}", parent=self.root)
            self.var_concurrent_mode.set(False)
            return
        
        # 使用线程安全的队列传递 Ctrl 键状态和点击事件
        self._concurrent_event_queue = queue.Queue()
        
        self._concurrent_enabled = True
        self._concurrent_ctrl_pressed = False
        self.var_concurrent_status.set("监听中")
        self._concurrent_status_label.config(fg="#22c55e")
        self.var_concurrent_hotkey.set("按住 Ctrl + 左键点击目标控件")
        self.status_var.set("伴随拾取模式已开启：按住 Ctrl + 左键点击目标控件，自动追加步骤")
        
        def on_key_press(key):
            if not self._concurrent_enabled:
                return True
            try:
                if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                    self._concurrent_ctrl_pressed = True
                    self._concurrent_event_queue.put(("ctrl", True))
            except Exception:
                pass
            return True
        
        def on_key_release(key):
            if not self._concurrent_enabled:
                return True
            try:
                if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                    self._concurrent_ctrl_pressed = False
                    self._concurrent_event_queue.put(("ctrl", False))
            except Exception:
                pass
            return True
        
        def on_click(x, y, button, pressed):
            if not self._concurrent_enabled or not pressed:
                return True
            if button != mouse.Button.left:
                return True

            # 实时查询物理 Ctrl 状态（避免依赖键盘 release 事件导致的"粘滞"）
            if not self._is_ctrl_down():
                return True

            import time
            now = time.time()
            if now - self._concurrent_last_click_time < 0.5:
                return True
            self._concurrent_last_click_time = now

            self._concurrent_event_queue.put(("click", x, y))
            return True
        
        try:
            self._concurrent_listener = mouse.Listener(on_click=on_click, daemon=True)
            self._concurrent_listener.start()
            self._concurrent_keyboard_listener = keyboard.Listener(
                on_press=on_key_press, on_release=on_key_release, daemon=True
            )
            self._concurrent_keyboard_listener.start()
            
            # 启动主线程轮询队列
            self._concurrent_polling_job = self.root.after(100, self._poll_concurrent_events)
        except Exception as exc:
            self._stop_concurrent_mode()
            messagebox.showerror("启动失败", f"监听器创建失败：\n{exc}", parent=self.root)

    def _is_ctrl_down(self):
        """实时查询物理 Ctrl 键状态（优先用 Win32 API，避免粘滞）。"""
        try:
            import win32api
            import win32con
            return bool(win32api.GetAsyncKeyState(win32con.VK_CONTROL) & 0x8000)
        except Exception:
            # 回退：使用键盘监听器维护的布尔状态
            return self._concurrent_ctrl_pressed

    def _point_in_editor(self, x, y):
        """判断坐标是否落在编辑器自身窗口内（主线程调用，需在主线程执行）。"""
        try:
            rx = self.root.winfo_rootx()
            ry = self.root.winfo_rooty()
            rw = self.root.winfo_width()
            rh = self.root.winfo_height()
            return (rx <= x <= rx + rw) and (ry <= y <= ry + rh)
        except Exception:
            return False

    def _poll_concurrent_events(self):
        """轮询处理来自 pynput 线程的事件（线程安全）"""
        if not self._concurrent_enabled:
            return
        
        try:
            while True:
                try:
                    event = self._concurrent_event_queue.get_nowait()
                    if event[0] == "click":
                        x, y = event[1], event[2]
                        # 排除编辑器自身窗口内的点击，避免误拾取 UI
                        if self._point_in_editor(x, y):
                            continue
                        self._concurrent_capture_and_append(x, y)
                except queue.Empty:
                    break
        except Exception:
            pass
        
        self._concurrent_polling_job = self.root.after(100, self._poll_concurrent_events)

    def _stop_concurrent_mode(self):
        """停止伴随拾取模式"""
        self._concurrent_enabled = False
        self.var_concurrent_mode.set(False)
        self.var_concurrent_status.set("关闭")
        self._concurrent_status_label.config(fg=EDITOR_THEME["muted"])
        self.var_concurrent_hotkey.set("Ctrl+Click")
        self.status_var.set("伴随拾取模式已关闭")
        
        if hasattr(self, "_concurrent_polling_job") and self._concurrent_polling_job:
            try:
                self.root.after_cancel(self._concurrent_polling_job)
            except Exception:
                pass
            self._concurrent_polling_job = None
        
        if self._concurrent_listener is not None:
            try:
                self._concurrent_listener.stop()
            except Exception:
                pass
            self._concurrent_listener = None
        
        if getattr(self, '_concurrent_keyboard_listener', None) is not None:
            try:
                self._concurrent_keyboard_listener.stop()
            except Exception:
                pass
            self._concurrent_keyboard_listener = None

    def _build_control_from_wrapper(self, ctrl):
        """从 pywinauto wrapper 对象构建控件信息字典"""
        element_info = _safe_get_value(lambda: ctrl.element_info, None)
        name = str(_safe_get_value(lambda: ctrl.window_text(), "")).strip()
        if element_info is not None and not name:
            name = str(_safe_get_value(lambda: getattr(element_info, "name", ""), "")).strip()
        class_name = str(_safe_get_value(lambda: ctrl.class_name(), "")).strip()
        control_type = str(_safe_get_value(lambda: getattr(element_info, "control_type", ""), "")).strip()
        localized_control_type = str(_safe_get_value(lambda: getattr(element_info, "localized_control_type", ""), "")).strip()
        automation_id = str(_safe_get_value(lambda: getattr(element_info, "automation_id", ""), "")).strip()
        framework_id = str(_safe_get_value(lambda: getattr(element_info, "framework_id", ""), "")).strip()
        process_id = str(_safe_get_value(lambda: getattr(element_info, "process_id", ""), "")).strip()
        runtime_id_raw = _safe_get_value(lambda: getattr(element_info, "runtime_id", ""), "")
        runtime_id = ""
        if isinstance(runtime_id_raw, (list, tuple)) and runtime_id_raw:
            formatted = []
            for item in runtime_id_raw:
                try:
                    formatted.append(hex(int(item))[2:].upper())
                except Exception:
                    formatted.append(str(item))
            runtime_id = "[" + ",".join(formatted) + "]"
        elif runtime_id_raw:
            runtime_id = str(runtime_id_raw)
        handle = _safe_get_value(lambda: getattr(element_info, "handle", ""), "")
        rect = _safe_get_value(lambda: ctrl.rectangle(), None)
        rect_text = ""
        if rect is not None:
            rect_text = f"[l={rect.left},t={rect.top},r={rect.right},b={rect.bottom}]"
        is_enabled = _safe_get_value(lambda: getattr(element_info, "enabled", ""), "")
        if is_enabled == "":
            is_enabled = _safe_get_value(lambda: ctrl.is_enabled(), "")
        is_offscreen = _safe_get_value(lambda: getattr(element_info, "offscreen", ""), "")
        if is_offscreen == "":
            is_offscreen = not bool(_safe_get_value(lambda: ctrl.is_visible(), True))
        keyboard_focusable = _safe_get_value(lambda: getattr(element_info, "keyboard_focusable", ""), "")
        has_keyboard_focus = _safe_get_value(lambda: getattr(element_info, "has_keyboard_focus", ""), "")
        provider_description = str(_safe_get_value(lambda: getattr(element_info, "provider_description", ""), "")).strip()
        
        # 提取深层属性 (ValuePattern & TogglePattern)
        value_pattern_value = ""
        if hasattr(ctrl, "get_value"):
            value_pattern_value = str(_safe_get_value(lambda: ctrl.get_value(), "")).strip()
            
        toggle_state = ""
        if hasattr(ctrl, "get_toggle_state"):
            toggle_state = str(_safe_get_value(lambda: ctrl.get_toggle_state(), "")).strip()
            
        legacy_name = str(_safe_get_value(lambda: getattr(element_info, "legacy_name", ""), "")).strip()
        if not name and legacy_name:
            name = legacy_name

        ancestors = []
        current = ctrl
        for _ in range(4):
            current = _safe_get_value(lambda: current.parent(), None)
            if not current:
                break
            parent_name = str(_safe_get_value(lambda: current.window_text(), "")).strip()
            parent_class = str(_safe_get_value(lambda: current.class_name(), "")).strip()
            parent_type = str(_safe_get_value(lambda: getattr(current.element_info, "control_type", ""), "")).strip()
            signature = " | ".join(item for item in [parent_name, parent_class, parent_type] if item)
            if signature:
                ancestors.append(signature)

        children = []
        for child in _safe_get_value(lambda: ctrl.children(), []):
            child_name = str(_safe_get_value(lambda: child.window_text(), "")).strip()
            child_class = str(_safe_get_value(lambda: child.class_name(), "")).strip()
            child_type = str(_safe_get_value(lambda: getattr(child.element_info, "control_type", ""), "")).strip()
            signature = " | ".join(item for item in [child_name, child_class, child_type] if item)
            if signature:
                children.append(signature)
            if len(children) >= 8:
                break

        inspect_data = {
            "howFound": "Interactive click collector",
            "name": name,
            "value": value_pattern_value,
            "toggleState": toggle_state,
            "controlType": control_type,
            "localizedControlType": localized_control_type,
            "boundingRectangle": rect_text,
            "isEnabled": str(is_enabled),
            "isOffscreen": str(is_offscreen),
            "isKeyboardFocusable": str(keyboard_focusable),
            "hasKeyboardFocus": str(has_keyboard_focus),
            "processId": process_id,
            "runtimeId": runtime_id,
            "frameworkId": framework_id,
            "className": class_name,
            "automationId": automation_id,
            "nativeWindowHandle": hex(handle) if isinstance(handle, int) and handle else str(handle or ""),
            "providerDescription": provider_description,
            "legacyName": legacy_name,
            "legacyRole": "",
            "legacyState": "",
            "firstChild": children[0] if children else "",
            "lastChild": children[-1] if children else "",
            "next": "",
            "previous": "",
            "children": children,
            "ancestors": ancestors,
            "availablePatterns": [],
        }
        locator_method, locator_value, locator_score, locator_reason = build_locator_recommendation(inspect_data)
        inspect_data["recommendedTargetMethod"] = locator_method
        inspect_data["recommendedTargetValue"] = locator_value
        inspect_data["recommendedTargetScore"] = locator_score
        inspect_data["recommendedTargetReason"] = locator_reason
        raw_text = build_synthetic_inspect_text(inspect_data)
        control_id_idx = len(self.steps) + 1
        control = normalize_control(
            {
                "id": f"captured_control_{control_id_idx}",
                "name": name or legacy_name or f"控件{control_id_idx}",
                "role": "",
                "enabled": True,
                "windowTitle": "",
                "targetMethod": locator_method,
                "targetValue": locator_value,
                "templateKey": "",
                "uiPath": name or legacy_name,
                "notes": "通过伴随拾取采集获得",
                "rawInspectText": raw_text,
                "auxChecks": [
                    f"FrameworkId={framework_id}" if framework_id else "",
                    f"ClassName={class_name}" if class_name else "",
                    f"ControlType={normalize_control_type_name(control_type, localized_control_type)}" if control_type or localized_control_type else "",
                    f"IsEnabled={is_enabled}",
                    f"IsOffscreen={is_offscreen}",
                    f"IsKeyboardFocusable={keyboard_focusable}" if keyboard_focusable != "" else "",
                    f"HasKeyboardFocus={has_keyboard_focus}" if has_keyboard_focus != "" else "",
                ],
                "inspectData": inspect_data,
            },
            control_id_idx - 1,
        )
        control["auxChecks"] = [item for item in control.get("auxChecks", []) if item]
        return control

    def _concurrent_capture_and_append(self, x, y):
        """在 Ctrl+Click 时捕获控件并自动追加步骤"""
        try:
            # 1. 使用 pywinauto 捕获鼠标位置下的控件
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            wrapper = desktop.from_point(x, y)
            
            if wrapper is None:
                self.status_var.set("伴随拾取失败：未找到控件")
                return
            
            # 2. 提取控件信息（复用现有逻辑）
            control = self._build_control_from_wrapper(wrapper)
            if control is None:
                self.status_var.set("伴随拾取失败：控件信息无效")
                return

            # 2.1 用真实顶层窗口标题覆盖（避免把控件名误当作窗口标题）
            try:
                real_window_title = str(wrapper.top_level_parent().window_text()).strip()
            except Exception:
                real_window_title = ""
            if real_window_title:
                control["windowTitle"] = real_window_title
            
            # 尝试在总控件信息.json中匹配
            matched = self._match_control_in_master_library(control)
            
            # 如果匹配成功，合并一些关键信息到当前 control 中
            if matched:
                control["id"] = matched.get("id", control["id"])
                if matched.get("name"):
                    control["name"] = matched["name"]
                if matched.get("displayName"):
                    control["displayName"] = matched["displayName"]
                if matched.get("uiPath"):
                    control["uiPath"] = matched["uiPath"]
                    
            # 自动截图作为图像模板兜底
            rect_dict = None
            rect_str = control.get("inspectData", {}).get("boundingRectangle", "")
            if rect_str.startswith("[l="):
                try:
                    parts = rect_str.strip("[]").split(",")
                    rect_dict = {
                        "left": int(parts[0].split("=")[1]),
                        "top": int(parts[1].split("=")[1]),
                        "right": int(parts[2].split("=")[1]),
                        "bottom": int(parts[3].split("=")[1])
                    }
                    w = rect_dict["right"] - rect_dict["left"]
                    h = rect_dict["bottom"] - rect_dict["top"]
                    
                    # 确保宽度和高度有效，避免截图无效区域
                    if w > 0 and h > 0:
                        from PIL import ImageGrab
                        import time
                        import re
                        bbox = (rect_dict["left"], rect_dict["top"], rect_dict["right"], rect_dict["bottom"])
                        img = ImageGrab.grab(bbox)
                        capture_dir = os.path.join(TEMPLATE_ROOT_DIR, "recorder_captures")
                        if not os.path.exists(capture_dir):
                            os.makedirs(capture_dir)
                        
                        safe_name = re.sub(r'[^\w\u4e00-\u9fa5]', '_', str(control.get("name", "未命名")))[:15]
                        step_number = len(self.steps) + 1
                        template_filename = f"step_{step_number}_{safe_name}.png"
                        full_path = os.path.join(capture_dir, template_filename)
                        img.save(full_path)
                        
                        # 回填给 control，以便构建步骤时带上 templateKey（含子目录路径）
                        control["templateKey"] = f"recorder_captures/{template_filename}"
                except Exception as exc:
                    self.status_var.set(f"伴随拾取自动截图失败: {exc}")
            
            # 4. 智能推断动作类型
            action_type = self._infer_action_type(control)
            
            # 5. 构建步骤
            new_step = self._build_step_from_control(control, matched, action_type)
            
            # 6. 追加到当前流程
            self._append_step_to_flow(new_step)
            
            # 7. 更新状态
            ctrl_name = control.get("name", "") or control.get("displayName", "")
            msg = f"✓ 已追加步骤：{action_type} - {ctrl_name}"
            self.status_var.set(msg)
            
            self._show_toast_notification(f"伴随拾取成功\n{action_type}: {ctrl_name}", rect=rect_dict)
            
        except Exception as exc:
            self.status_var.set(f"伴随拾取失败：{exc}")
            self._show_toast_notification(f"伴随拾取失败\n{exc}", is_error=True)

    def _show_toast_notification(self, message, is_error=False, rect=None):
        """在屏幕顶部中央显示一个不抢焦点的悬浮提示，并可选绘制屏幕红框高亮"""
        try:
            import winsound
            if is_error:
                winsound.MessageBeep(winsound.MB_ICONHAND)
            else:
                winsound.MessageBeep(winsound.MB_OK)
        except:
            pass

        # 1. 绘制红框高亮 (RPA 常用体验)
        if rect and isinstance(rect, dict):
            try:
                left = int(rect.get("left", 0))
                top = int(rect.get("top", 0))
                right = int(rect.get("right", 0))
                bottom = int(rect.get("bottom", 0))
                w = right - left
                h = bottom - top
                # 过滤掉极小/异常的高亮框，避免在屏幕左上角出现小方框遮挡
                if w > 12 and h > 12 and w < 4000 and h < 4000:
                    hl = tk.Toplevel(self.root)
                    hl.overrideredirect(True)
                    hl.attributes("-topmost", True)
                    try:
                        hl.attributes("-transparentcolor", "white")
                        hl.configure(bg="white")
                        wt_dpi.raw_geometry(hl, f"{w}x{h}+{left}+{top}")
                        canvas = tk.Canvas(hl, width=w, height=h, bg="white", highlightthickness=0)
                        canvas.pack(fill="both", expand=True)
                        canvas.create_rectangle(0, 0, w-1, h-1, outline="red", width=4)
                    except Exception:
                        hl.attributes("-alpha", 0.28)
                        hl.configure(bg="#dc2626")
                        wt_dpi.raw_geometry(hl, f"{w}x{h}+{left}+{top}")
                    hl.update_idletasks()
                    # 800ms 后自动销毁，避免永久遮挡
                    hl.after(800, lambda w=hl: _safe_destroy(w))
            except Exception:
                pass

        # 2. 绘制屏幕顶部中央的 Toast
        try:
            toast = tk.Toplevel(self.root)
            toast.overrideredirect(True)
            toast.attributes("-topmost", True)
            try:
                toast.attributes("-disabled", True)
            except:
                pass
            
            bg_color = "#fee2e2" if is_error else "#dcfce7"
            fg_color = "#991b1b" if is_error else "#166534"
            
            toast.configure(bg=bg_color, highlightbackground=fg_color, highlightthickness=2)
            # 字体族用本文件统一的 "Microsoft YaHei UI"（原为 self.default_font，
            # 该类从未定义过该属性 → AttributeError 被外层 except 吞掉，Toast 永远不显示）
            label = tk.Label(toast, text=message, bg=bg_color, fg=fg_color, font=("Microsoft YaHei UI", 12, "bold"), padx=30, pady=15)
            label.pack()
            
            toast.update_idletasks()
            w = toast.winfo_reqwidth()
            h = toast.winfo_reqheight()
            
            # 放置在屏幕顶部中央，更显眼
            x = (self.root.winfo_screenwidth() - w) // 2
            y = 50  # 距离顶部 50 像素
            # w/h 来自 winfo_reqwidth/height（已含 Tk scaling），绕过 DPI 缩放避免偏大
            wt_dpi.raw_geometry(toast, f"{w}x{h}+{x}+{y}")
            
            # 强制提升层级 (Win32)
            try:
                import ctypes
                HWND_TOPMOST = -1
                SWP_NOMOVE = 0x0002
                SWP_NOSIZE = 0x0001
                SWP_NOACTIVATE = 0x0010
                ctypes.windll.user32.SetWindowPos(toast.winfo_id(), HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            except:
                pass
            
            # 淡出效果
            alpha = 1.0
            def fade_out():
                nonlocal alpha
                try:
                    alpha -= 0.05
                    if alpha <= 0:
                        _safe_destroy(toast)
                    else:
                        toast.attributes("-alpha", alpha)
                        toast.after(50, fade_out)
                except Exception:
                    pass
            
            toast.after(2000, fade_out)
        except Exception:
            pass

    def _match_control_in_master_library(self, control):
        """尝试在总控件信息.json中匹配控件"""
        try:
            master_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "control_maps", "standard", "总控件信息.json")
            if not os.path.exists(master_path):
                return None
            
            import json
            with open(master_path, encoding="utf-8") as f:
                master_data = json.load(f)
            
            inspect_data = control.get("inspectData", {})
            ctrl_automation_id = str(inspect_data.get("automationId", "")).strip()
            ctrl_name = str(inspect_data.get("name", "")).strip() or str(control.get("name", "")).strip()
            ctrl_class = str(inspect_data.get("className", "")).strip()
            ctrl_type = str(inspect_data.get("controlType", "")).strip()
            
            # 在 flatControls 中查找匹配
            flat_controls = master_data.get("flatControls", []) if isinstance(master_data, dict) else []
            for master_ctrl in flat_controls:
                master_inspect = master_ctrl.get("inspectData", {}) if isinstance(master_ctrl.get("inspectData"), dict) else {}
                master_aid = str(master_inspect.get("automationId", "")).strip()
                
                # 优先匹配 automationId
                if master_aid and master_aid == ctrl_automation_id:
                    return master_ctrl
                
                # 备选：匹配 name + controlType
                master_name = str(master_inspect.get("name", "")).strip()
                master_type = str(master_ctrl.get("controlType", "")).strip() or str(master_inspect.get("controlType", "")).strip()
                if master_name == ctrl_name and master_type == ctrl_type and master_name:
                    return master_ctrl
            
            return None
        except Exception:
            return None

    def _infer_action_type(self, control):
        """根据控件类型智能推断动作类型，与 wt_action_schema.py 中定义的合法动作保持一致"""
        inspect_data = control.get("inspectData", {})
        ctrl_type = str(inspect_data.get("controlType", "")).strip().lower()

        if ctrl_type in {"button", "hyperlink", "link", "text", "menuitem"}:
            return "click"
        elif ctrl_type in {"edit", "textbox"}:
            return "type_text"
        elif ctrl_type in {"combobox", "dropdown"}:
            return "select_dropdown_item_runtime"
        elif ctrl_type in {"list", "listitem"}:
            return "select_dropdown_item_runtime"
        elif ctrl_type in {"checkbox", "radiobutton", "tab", "tabitem"}:
            return "click"
        else:
            return "click"  # 默认动作为点击

    def _build_step_from_control(self, control, matched, action_type):
        """根据控件信息构建步骤"""
        inspect_data = control.get("inspectData", {}) if isinstance(control.get("inspectData"), dict) else {}
        
        # 优先使用匹配到的控件信息
        if matched:
            matched_inspect = matched.get("inspectData", {}) if isinstance(matched.get("inspectData"), dict) else {}
            target_method = matched.get("targetMethod", "") or matched.get("recommendedTargetMethod", "") or matched_inspect.get("recommendedTargetMethod", "")
            target_value = matched.get("targetValue", "") or matched.get("recommendedTargetValue", "") or matched_inspect.get("recommendedTargetValue", "")
            window_title = matched.get("windowTitle", "")
            ctrl_id = matched.get("id", "")
            ctrl_name = matched.get("name", "")
            
            # 将匹配到的更丰富的信息回填到 control
            control["targetMethod"] = target_method
            control["targetValue"] = target_value
            control["id"] = ctrl_id
            control["name"] = ctrl_name
            if matched.get("uiPath"):
                control["uiPath"] = matched["uiPath"]
            if matched.get("auxChecks"):
                control["auxChecks"] = matched["auxChecks"]
            # 如果匹配到的库里已经有 templateKey，且目前还没截图（或优先用总库的）
            if matched.get("templateKey") and not control.get("templateKey"):
                control["templateKey"] = matched.get("templateKey")
        else:
            target_method = inspect_data.get("recommendedTargetMethod", "")
            target_value = inspect_data.get("recommendedTargetValue", "")
            # 优先使用真实窗口标题，缺失时回退到控件名
            window_title = str(control.get("windowTitle", "")).strip() or str(inspect_data.get("name", "")).strip()
            ctrl_id = ""
            ctrl_name = control.get("name", "") or control.get("displayName", "")
        
        # 构建步骤名称
        action_labels = {
            "click": "点击",
            "type_text": "输入文本",
            "set_combobox": "设置下拉框",
            "select_dropdown_item_runtime": "下拉选择",
        }
        step_name = f"{action_labels.get(action_type, '操作')}-{ctrl_name or '新控件'}"
        
        # 构建步骤对象
        # 这里直接使用 control 字典中可能已被匹配库覆盖的字段
        ctrl_id = control.get("id", "") or f"ctrl_captured_{len(self.steps) + 1}"
        target_method = control.get("targetMethod", "") or inspect_data.get("recommendedTargetMethod", "")
        target_value = control.get("targetValue", "") or inspect_data.get("recommendedTargetValue", "")
        window_title = control.get("windowTitle", "") or str(inspect_data.get("name", "")).strip()
        template_key = control.get("templateKey", "")

        step = {
            "id": f"step_{len(self.steps) + 1}",
            "name": step_name,
            "stage": "captured",
            "strategy": "action",
            "actionType": "action",
            "topLevel": True,
            "enabled": True,
            "codeSymbol": "",
            "codeReference": "",
            "packageRef": "",
            "description": f"伴随拾取生成：{ctrl_name}",
            "successLog": "",
            "windowTitle": window_title,
            "inspectHints": {
                "controlName": ctrl_name,
                "className": str(inspect_data.get("className", "")).strip(),
                "automationId": str(inspect_data.get("automationId", "")).strip(),
                "controlType": str(inspect_data.get("controlType", "")).strip(),
                "uiPath": control.get("uiPath", ""),
                "templateKey": template_key,
            },
            "controls": [{
                "id": ctrl_id,
                "name": ctrl_name,
                "role": "伴随拾取",
                "enabled": True,
                "windowTitle": window_title,
                "targetMethod": target_method,
                "targetValue": target_value,
                "templateKey": template_key,
                "uiPath": control.get("uiPath", ""),
                "notes": "由伴随拾取模式自动生成" + ("（已匹配总控件库）" if matched else "（未匹配，依赖实时定位）"),
                "rawInspectText": control.get("rawInspectText", ""),
                "auxChecks": control.get("auxChecks", []),
                "inspectData": inspect_data,
            }],
            "stepParams": {},
            "actionConfig": {
                "action": action_type,
                "controlId": ctrl_id,
                "timeoutSeconds": 3.0,
                "waitBefore": 0.3,
                "waitAfter": 0.3,
                "retryCount": 0,
                "retryInterval": 1.0,
                "onError": "continue",
            },
            "auxChecks": [],
            "fallbacks": [],
            "notes": "",
        }
        
        return step

    def _append_step_to_flow(self, new_step):
        """将新步骤追加到当前流程"""
        self.steps.append(new_step)
        self.dirty = True
        
        # 刷新步骤树
        self._refresh_steps_tree()
        
        # 选中新追加的步骤
        new_index = len(self.steps) - 1
        self._select_step(new_index)
        
        self._set_title()

    def cmd_open_json_file(self):
        target = self.definition_path or FLOW_DEFINITION_FILE
        if not os.path.exists(target):
            messagebox.showinfo("提示", "当前链路文件还不存在，请先保存。")
            return
        try:
            subprocess.Popen(["notepad.exe", target])
            self.status_var.set(f"已在记事本中打开：{os.path.basename(target)}")
        except Exception:
            os.startfile(os.path.dirname(target))
            self.status_var.set(f"已打开链路文件所在目录：{os.path.dirname(target)}")

    def cmd_open_reference_project(self):
        if not os.path.exists(REFERENCE_PROJECT_DIR):
            messagebox.showerror("打开失败", f"未找到参考项目目录：\n{REFERENCE_PROJECT_DIR}")
            return
        os.startfile(REFERENCE_PROJECT_DIR)

    def _mark_dirty(self, status_text):
        self.dirty = True
        self.status_var.set(status_text)
        self._set_title()

    def _set_title(self):
        file_name = os.path.basename(self.definition_path or FLOW_DEFINITION_FILE)
        dirty_mark = " *" if self.dirty else ""
        self.root.title(f"WT 自动化流程链路编辑器 - {file_name}{dirty_mark}")

    def _on_close(self):
        # 退出前停止伴随拾取模式
        self._stop_concurrent_mode()

        # 表单里未应用到步骤的修改也先确认，避免关窗无声丢弃
        if not self._confirm_discard_form_changes():
            return
        
        if self.dirty:
            should_close = messagebox.askyesno("确认退出", "当前流程链路有未保存修改，确定直接退出吗？")
            if not should_close:
                return
        # 关窗前停掉定位探针：其周期 after 与子进程在窗口销毁后仍会驻留/触发
        if getattr(self, "_probe_proc", None) is not None or getattr(self, "_probe_elapsed_timer", None) is not None:
            try:
                self._stop_probe()
            except Exception:
                pass
        self.root.destroy()

    @staticmethod
    def _split_lines(raw_text):
        return [line.strip() for line in raw_text.splitlines() if line.strip()]

    @staticmethod
    def _get_text(widget):
        return widget.get("1.0", tk.END).strip()

    @staticmethod
    def _set_text(widget, value):
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value or "")


def index_to_seq(index):
    return index + 1


def build_cli_parser():
    parser = argparse.ArgumentParser(description="WT 自动化流程链路编辑器")
    parser.add_argument("flow_file", nargs="?", default="", help="流程定义 JSON 文件路径")
    parser.add_argument("--step-id", default="", help="启动后自动定位并聚焦指定的步骤 ID")
    parser.add_argument("--startup-ping", default="", help="窗口初始化完成后写入该文件，供启动方确认 GUI 已真正创建")
    parser.add_argument("--open-control-library", action="store_true", help="启动后自动打开从控件库导入对话框")
    parser.add_argument("--open-control-import", action="store_true", help="启动后自动打开从控件库导入对话框")
    parser.add_argument("--open-locator-tester", action="store_true", help="启动后自动打开控件定位检验器")
    parser.add_argument("--control-library-standalone", action="store_true", help="独立启动控件库维护窗口（不加载流程编辑器主界面）")
    parser.add_argument("--ui-scale", type=float, default=None, help="界面缩放系数（如 1.0/1.25/1.5），覆盖共享配置文件 ui_scale.json")
    return parser


def main():
    parser = build_cli_parser()
    args = parser.parse_args()

    if args.ui_scale is not None:
        wt_dpi.set_user_scale(args.ui_scale)

    wt_dpi.enable_process_dpi_awareness()
    root = tk.Tk()
    wt_dpi.compute_scale(root)
    root.withdraw()  # 先隐藏，非独立模式下后面会 deiconify 回来
    try:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    # 独立控件库维护模式：不启动流程编辑器，直接弹控件库窗口
    if args.control_library_standalone:
        try:
            style = ttk.Style()
            if "vista" in style.theme_names():
                style.theme_use("vista")
            elif "clam" in style.theme_names():
                style.theme_use("clam")
        except Exception:
            pass
        
        # 创建独立顶级窗口
        standalone_window = tk.Toplevel()
        standalone_window.title("控件库维护")
        wt_dpi.geometry(standalone_window, 1500, 900)
        standalone_window.minsize(wt_dpi.scale(1320), wt_dpi.scale(760))
        
        try:
            # 传递外部窗口给对话框
            dialog = ControlMapImportDialog(root, external_window=standalone_window)
            
            standalone_window.protocol("WM_DELETE_WINDOW", root.destroy)
            
            standalone_window.update_idletasks()
            standalone_window.lift()
            standalone_window.focus_force()
            standalone_window.attributes("-topmost", True)
            standalone_window.after(700, lambda: standalone_window.attributes("-topmost", False))
            
            if args.startup_ping:
                try:
                    with open(args.startup_ping, "w", encoding="utf-8") as file_obj:
                        file_obj.write(datetime.now().isoformat(timespec="seconds"))
                except OSError:
                    pass
            root.wait_window(standalone_window)
        except Exception as exc:
            messagebox.showerror("打开失败", f"启动控件库维护失败：\n{exc}")
        return

    app = FlowEditorApp(root, definition_path=args.flow_file, focus_step_id=args.step_id)
    try:
        root.update_idletasks()
        root.deiconify()
        root.lift()
        root.focus_force()
        root.attributes("-topmost", True)
        root.after(400, lambda: root.attributes("-topmost", False))
    except Exception:
        pass

    if args.startup_ping:
        try:
            with open(args.startup_ping, "w", encoding="utf-8") as file_obj:
                file_obj.write(datetime.now().isoformat(timespec="seconds"))
        except OSError:
            pass

    # 启动后自动打开指定对话框
    def _auto_open():
        if args.open_control_library or args.open_control_import:
            try:
                app._open_control_import_dialog()
            except Exception as exc:
                messagebox.showerror("打开失败", f"自动打开控件库失败：\n{exc}")
        if args.open_locator_tester:
            try:
                app.cmd_open_control_locator_tester()
            except Exception as exc:
                messagebox.showerror("打开失败", f"自动打开控件定位检验器失败：\n{exc}")

    if args.open_control_library or args.open_control_import or args.open_locator_tester:
        root.after(600, _auto_open)

    root.mainloop()


if __name__ == "__main__":
    main()

