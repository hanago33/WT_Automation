# encoding: utf-8
"""
界面模板采集器

功能：
1. 载入整张程序界面截图
2. 自动检测候选按钮/控件区域
3. 在界面中预览候选区域
4. 为候选区域命名并保存到模板库
"""
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import wt_dpi
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk, ImageGrab

try:
    import pytesseract
except Exception:
    pytesseract = None


DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "image_templates", "WT_software_Images")
INDEX_FILE_NAME = "templates_index.json"
CANVAS_MAX_WIDTH = 1100
CANVAS_MAX_HEIGHT = 760
DEFAULT_TEMPLATE_PREFIX = "template"
MIN_REGION_SIZE = 12
HANDLE_SIZE = 6
CONTACT_SHEET_CELL_WIDTH = 240
CONTACT_SHEET_CELL_HEIGHT = 180
CONTACT_SHEET_MARGIN = 16


# ============================================================================
# 统一浅色蓝灰主题
# ============================================================================

TEMPLATE_THEME = {
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
        font=(TEMPLATE_THEME["font"], 10),
    )


def find_tesseract_executable():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_dir = os.path.dirname(script_dir)
    candidate_paths = [
        os.environ.get("TESSERACT_EXE", ""),
        shutil.which("tesseract") or "",
        os.path.join(script_dir, "tools", "ORC", "tesseract.exe"),
        os.path.join(script_dir, "tools", "OCR", "tesseract.exe"),
        os.path.join(script_dir, "ORC", "tesseract.exe"),
        os.path.join(script_dir, "OCR", "tesseract.exe"),
        os.path.join(workspace_dir, "WT_Automation", "tools", "ORC", "tesseract.exe"),
        os.path.join(workspace_dir, "ORC", "tesseract.exe"),
        os.path.join(workspace_dir, "OCR", "tesseract.exe"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for path in candidate_paths:
        if path and os.path.exists(path):
            return path
    return None


TESSERACT_EXE = find_tesseract_executable()


@dataclass
class CandidateRegion:
    x: int
    y: int
    w: int
    h: int

    @property
    def area(self):
        return self.w * self.h

    def as_dict(self):
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


def clamp(value, min_value, max_value):
    return max(min_value, min(value, max_value))


def intersection_over_union(a, b):
    x1 = max(a.x, b.x)
    y1 = max(a.y, b.y)
    x2 = min(a.x + a.w, b.x + b.w)
    y2 = min(a.y + a.h, b.y + b.h)
    if x2 <= x1 or y2 <= y1:
        return 0.0

    intersection = (x2 - x1) * (y2 - y1)
    union = a.area + b.area - intersection
    if union <= 0:
        return 0.0
    return intersection / union


def sort_regions(regions):
    return sorted(regions, key=lambda item: (item.y // 20, item.x, item.y))


def deduplicate_regions(regions, iou_threshold=0.45):
    kept = []
    for region in sorted(regions, key=lambda item: item.area, reverse=True):
        if any(intersection_over_union(region, existing) >= iou_threshold for existing in kept):
            continue
        kept.append(region)
    return sort_regions(kept)


def detect_candidate_regions(image_bgr):
    image_height, image_width = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    edge_map = cv2.Canny(blurred, 50, 150)
    edge_map = cv2.dilate(edge_map, None, iterations=1)

    adaptive = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        21,
        6,
    )
    adaptive = cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3)))

    combined = cv2.bitwise_or(edge_map, adaptive)
    contours, _ = cv2.findContours(combined, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    min_width = max(28, image_width // 120)
    min_height = max(14, image_height // 160)
    max_width = int(image_width * 0.75)
    max_height = int(image_height * 0.25)

    regions = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if w < min_width or h < min_height:
            continue
        if w > max_width or h > max_height:
            continue
        if area < 450:
            continue

        aspect_ratio = w / float(h)
        if aspect_ratio < 0.6 or aspect_ratio > 18:
            continue

        # 给模板留一点边距，后续匹配更稳。
        padding = 4
        x = clamp(x - padding, 0, image_width - 1)
        y = clamp(y - padding, 0, image_height - 1)
        w = clamp(w + padding * 2, 1, image_width - x)
        h = clamp(h + padding * 2, 1, image_height - y)
        regions.append(CandidateRegion(x=x, y=y, w=w, h=h))

    return deduplicate_regions(regions)


def sanitize_template_name(name, fallback_name):
    cleaned = (name or "").strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r'[<>:"/\\|?*]+', "", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or fallback_name


def normalize_template_category(category_name):
    return sanitize_template_name(category_name or "", "default") or "default"


def load_template_index(output_root):
    index_path = os.path.join(output_root, INDEX_FILE_NAME)
    if not os.path.exists(index_path):
        return {}
    try:
        with open(index_path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def safe_relpath(target_path, base_path):
    try:
        return os.path.relpath(target_path, base_path)
    except Exception:
        return os.path.basename(target_path)


def rebuild_template_index(output_root, index_data):
    raw_templates = index_data.get("templates", []) if isinstance(index_data, dict) else []
    templates = []
    category_counts = {}
    for item in raw_templates:
        if not isinstance(item, dict):
            continue
        category = normalize_template_category(item.get("category", "default"))
        image_path = str(item.get("image_path", "")).strip()
        record = {
            "category": category,
            "file_name": str(item.get("file_name", "")).strip(),
            "image_path": image_path,
            "relative_image_path": safe_relpath(image_path, output_root) if image_path else "",
            "source_screenshot": str(item.get("source_screenshot", "")).strip(),
            "region": item.get("region", {}),
            "updatedAt": str(item.get("updatedAt", "")).strip() or datetime.now().isoformat(timespec="seconds"),
        }
        if not record["file_name"]:
            continue
        templates.append(record)
        category_counts[category] = category_counts.get(category, 0) + 1

    templates.sort(key=lambda item: (item.get("category", ""), item.get("file_name", "")))
    categories = [
        {"id": category_id, "count": category_counts[category_id]}
        for category_id in sorted(category_counts.keys())
    ]
    return {
        "meta": {
            "templateCount": len(templates),
            "categoryCount": len(categories),
            "lastUpdated": datetime.now().isoformat(timespec="seconds"),
        },
        "categories": categories,
        "templates": templates,
    }


def save_template_index(output_root, index_data):
    os.makedirs(output_root, exist_ok=True)
    index_path = os.path.join(output_root, INDEX_FILE_NAME)
    normalized = rebuild_template_index(output_root, index_data)
    with open(index_path, "w", encoding="utf-8") as file_obj:
        json.dump(normalized, file_obj, ensure_ascii=False, indent=2)
    return normalized


def preprocess_for_ocr(crop_image):
    if isinstance(crop_image, Image.Image):
        image_rgb = np.array(crop_image.convert("RGB"))
    else:
        image_rgb = np.array(crop_image)
        if image_rgb.ndim == 2:
            image_rgb = cv2.cvtColor(image_rgb, cv2.COLOR_GRAY2RGB)
        elif image_rgb.ndim == 3 and image_rgb.shape[2] == 4:
            image_rgb = cv2.cvtColor(image_rgb, cv2.COLOR_RGBA2RGB)

    image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(binary)


def run_tesseract_cli(processed_image):
    if not TESSERACT_EXE:
        return ""

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temp_file:
        temp_path = temp_file.name
    try:
        processed_image.save(temp_path)
        result = subprocess.run(
            [
                TESSERACT_EXE,
                temp_path,
                "stdout",
                "-l",
                "chi_sim+eng",
                "--psm",
                "7",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except Exception:
        return ""
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def extract_text_with_ocr(crop_image):
    processed_image = preprocess_for_ocr(crop_image)

    if pytesseract is not None:
        try:
            text = pytesseract.image_to_string(processed_image, lang="chi_sim+eng", config="--psm 7")
            if text.strip():
                return text.strip()
        except Exception:
            pass

    return run_tesseract_cli(processed_image)


def get_ocr_status_message():
    if pytesseract is not None:
        return "OCR 可用: pytesseract"
    if TESSERACT_EXE:
        return f"OCR 可用: {TESSERACT_EXE}"
    return "OCR 未就绪: 未找到 pytesseract 或 tesseract.exe"


class TemplateBuilderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("WT 模板采集器")
        self.root.geometry("1600x950")

        self.screenshot_path_var = tk.StringVar()
        self.output_dir_var = tk.StringVar(value=DEFAULT_OUTPUT_DIR)
        self.category_var = tk.StringVar(value="projection")
        self.category_summary_var = tk.StringVar(value="模板库概览：尚未扫描")
        self.file_name_var = tk.StringVar()
        self.status_var = tk.StringVar(value="请选择一张界面截图")

        self.source_image_bgr = None
        self.source_image_rgb = None
        self.candidates = []
        self.selected_index = None
        self.selected_indices = set()
        self.template_names = []
        self.scale = 1.0
        self.display_photo = None
        self.preview_photo = None
        self.batch_prefix_var = tk.StringVar()
        self.draw_mode_var = tk.BooleanVar(value=False)
        self.drag_action = None
        self.drag_start = None
        self.drag_start_region = None
        self.drag_current_index = None
        self.active_handle = None
        self.temp_region = None
        self.selection_region = None
        self.selection_additive = False
        self.drag_moved = False
        self.pre_drag_snapshot = None
        self.undo_stack = []
        self.max_undo_steps = 50
        self.category_options = []

        self._apply_theme()
        self._build_ui()
        self.status_var.trace_add("write", self._on_status_changed)
        self.refresh_category_options()

    def _apply_theme(self):
        """应用统一浅色蓝灰主题。"""
        t = TEMPLATE_THEME
        self.root.configure(bg=t["bg"])
        style = ttk.Style(self.root)
        style.configure(
            "Template.TCombobox",
            fieldbackground=t["panel_soft"],
            background=t["panel_soft"],
            foreground=t["text"],
            arrowcolor=t["primary"],
            padding=3,
        )
        style.map(
            "Template.TCombobox",
            fieldbackground=[("readonly", t["panel_soft"])],
            foreground=[("readonly", t["text"])],
            selectbackground=[("readonly", t["primary_soft"])],
            selectforeground=[("readonly", t["text"])],
        )

    def _on_status_changed(self, *_args):
        """根据状态文本自动着色：失败/成功/运行/就绪。"""
        text = self.status_var.get()
        t = TEMPLATE_THEME
        if any(k in text for k in ("失败", "错误", "无法", "未找到")):
            color = t["danger"]
        elif any(k in text for k in ("成功", "完成", "已保存", "已加载")):
            color = t["success"]
        elif any(k in text for k in ("检测", "扫描", "截屏", "保存", "OCR", "加载", "绘制", "撤回", "3秒")):
            color = t["primary"]
        elif any(k in text for k in ("没有", "暂停", "跳过", "暂无", "未开启")):
            color = t["warning"]
        else:
            color = t["text"]
        if hasattr(self, "status_label"):
            self.status_label.config(fg=color)

    def _build_ui(self):
        top_frame = tk.Frame(self.root, bg=TEMPLATE_THEME["toolbar"])
        top_frame.pack(fill=tk.X, padx=8, pady=8)

        tk.Label(top_frame, text="截图", bg=TEMPLATE_THEME["toolbar"], fg=TEMPLATE_THEME["text"], font=(TEMPLATE_THEME["font"], 10)).grid(row=0, column=0, sticky="w")
        tk.Entry(top_frame, textvariable=self.screenshot_path_var, width=90, bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["text"], insertbackground=TEMPLATE_THEME["text"], relief="solid", bd=1, font=(TEMPLATE_THEME["font"], 10)).grid(row=0, column=1, padx=4, sticky="ew")
        choose_btn = tk.Button(top_frame, text="选择截图", command=self.choose_screenshot)
        _paint_button(choose_btn, TEMPLATE_THEME["panel"], TEMPLATE_THEME["text"], TEMPLATE_THEME["primary_soft"], active_fg=TEMPLATE_THEME["primary"])
        choose_btn.grid(row=0, column=2, padx=4)
        capture_btn = tk.Button(top_frame, text="截屏(3秒后)", command=self.take_screenshot)
        _paint_button(capture_btn, TEMPLATE_THEME["primary_soft"], TEMPLATE_THEME["primary"], TEMPLATE_THEME["primary"])
        capture_btn.grid(row=0, column=3, padx=4)
        detect_btn = tk.Button(top_frame, text="自动检测", command=self.detect_regions)
        _paint_button(detect_btn, TEMPLATE_THEME["primary_soft"], TEMPLATE_THEME["primary"], TEMPLATE_THEME["primary"])
        detect_btn.grid(row=0, column=4, padx=4)

        tk.Label(top_frame, text="输出目录", bg=TEMPLATE_THEME["toolbar"], fg=TEMPLATE_THEME["text"], font=(TEMPLATE_THEME["font"], 10)).grid(row=1, column=0, sticky="w")
        tk.Entry(top_frame, textvariable=self.output_dir_var, width=90, bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["text"], insertbackground=TEMPLATE_THEME["text"], relief="solid", bd=1, font=(TEMPLATE_THEME["font"], 10)).grid(row=1, column=1, padx=4, sticky="ew")
        dir_btn = tk.Button(top_frame, text="选择目录", command=self.choose_output_dir)
        _paint_button(dir_btn, TEMPLATE_THEME["panel"], TEMPLATE_THEME["text"], TEMPLATE_THEME["primary_soft"], active_fg=TEMPLATE_THEME["primary"])
        dir_btn.grid(row=1, column=2, padx=4)

        tk.Label(top_frame, text="分类", bg=TEMPLATE_THEME["toolbar"], fg=TEMPLATE_THEME["text"], font=(TEMPLATE_THEME["font"], 10)).grid(row=1, column=3, sticky="e")
        self.category_combo = ttk.Combobox(top_frame, textvariable=self.category_var, width=18, style="Template.TCombobox")
        self.category_combo.grid(row=1, column=4, padx=4)
        refresh_cat_btn = tk.Button(top_frame, text="刷新分类", command=self.refresh_category_options)
        _paint_button(refresh_cat_btn, TEMPLATE_THEME["panel"], TEMPLATE_THEME["text"], TEMPLATE_THEME["primary_soft"], active_fg=TEMPLATE_THEME["primary"])
        refresh_cat_btn.grid(row=1, column=5, padx=4)
        open_cat_btn = tk.Button(top_frame, text="打开当前分类目录", command=self.open_current_category_dir)
        _paint_button(open_cat_btn, TEMPLATE_THEME["panel"], TEMPLATE_THEME["text"], TEMPLATE_THEME["primary_soft"], active_fg=TEMPLATE_THEME["primary"])
        open_cat_btn.grid(row=1, column=6, padx=4)

        top_frame.columnconfigure(1, weight=1)

        main_frame = tk.Frame(self.root, bg=TEMPLATE_THEME["bg"])
        main_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        left_frame = tk.Frame(main_frame, bg=TEMPLATE_THEME["bg"])
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(left_frame, bg="#1f1f1f", width=CANVAS_MAX_WIDTH, height=CANVAS_MAX_HEIGHT, highlightbackground=TEMPLATE_THEME["border"], highlightcolor=TEMPLATE_THEME["border"], highlightthickness=1, relief="flat")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)
        self.root.bind("<Delete>", self.on_delete_key)
        self.root.bind("<Control-z>", self.on_undo_shortcut)
        self.root.bind("<Control-Z>", self.on_undo_shortcut)

        right_container = tk.Frame(main_frame, width=380, bg=TEMPLATE_THEME["panel"])
        right_container.pack(side=tk.RIGHT, fill=tk.Y, padx=(8, 0))
        right_container.pack_propagate(False)

        right_scrollbar = tk.Scrollbar(right_container, bg=TEMPLATE_THEME["toolbar"], troughcolor=TEMPLATE_THEME["panel"], activebackground=TEMPLATE_THEME["primary"], relief="flat", bd=0)
        right_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.right_canvas = tk.Canvas(right_container, bg=TEMPLATE_THEME["panel_soft"], highlightthickness=0)
        self.right_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right_scrollbar.config(command=self.right_canvas.yview)
        self.right_canvas.configure(yscrollcommand=right_scrollbar.set)

        right_frame = tk.Frame(self.right_canvas, bg=TEMPLATE_THEME["panel_soft"])
        self.right_canvas_window = self.right_canvas.create_window((0, 0), window=right_frame, anchor=tk.NW)
        right_frame.bind("<Configure>", self.on_right_frame_configure)
        self.right_canvas.bind("<Configure>", self.on_right_canvas_configure)
        self.right_canvas.bind_all("<MouseWheel>", self.on_mousewheel)

        tk.Label(right_frame, text="候选区域", bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["text"], font=(TEMPLATE_THEME["font"], 10, "bold")).pack(anchor="w")
        self.listbox = tk.Listbox(right_frame, width=45, height=12, selectmode=tk.EXTENDED, exportselection=False, bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["text"], selectbackground=TEMPLATE_THEME["primary"], selectforeground="#ffffff", highlightbackground=TEMPLATE_THEME["border"], highlightcolor=TEMPLATE_THEME["primary"], highlightthickness=1, relief="flat", bd=0, font=(TEMPLATE_THEME["font"], 10))
        self.listbox.pack(fill=tk.BOTH, expand=False)
        self.listbox.bind("<<ListboxSelect>>", self.on_listbox_select)
        listbox_h_scrollbar = tk.Scrollbar(right_frame, orient=tk.HORIZONTAL, command=self.listbox.xview, bg=TEMPLATE_THEME["toolbar"], troughcolor=TEMPLATE_THEME["panel_soft"], activebackground=TEMPLATE_THEME["primary"], relief="flat", bd=0)
        listbox_h_scrollbar.pack(fill=tk.X, pady=(4, 0))
        self.listbox.configure(xscrollcommand=listbox_h_scrollbar.set)

        list_button_frame = tk.Frame(right_frame, bg=TEMPLATE_THEME["panel_soft"])
        list_button_frame.pack(fill=tk.X, pady=6)
        prev_btn = tk.Button(list_button_frame, text="上一项", command=self.select_previous)
        _paint_button(prev_btn, TEMPLATE_THEME["panel"], TEMPLATE_THEME["text"], TEMPLATE_THEME["primary_soft"], active_fg=TEMPLATE_THEME["primary"])
        prev_btn.pack(side=tk.LEFT, padx=2)
        next_btn = tk.Button(list_button_frame, text="下一项", command=self.select_next)
        _paint_button(next_btn, TEMPLATE_THEME["panel"], TEMPLATE_THEME["text"], TEMPLATE_THEME["primary_soft"], active_fg=TEMPLATE_THEME["primary"])
        next_btn.pack(side=tk.LEFT, padx=2)
        redetect_btn = tk.Button(list_button_frame, text="重新检测", command=self.detect_regions)
        _paint_button(redetect_btn, TEMPLATE_THEME["primary_soft"], TEMPLATE_THEME["primary"], TEMPLATE_THEME["primary"])
        redetect_btn.pack(side=tk.LEFT, padx=2)
        tk.Checkbutton(
            list_button_frame,
            text="手动画框",
            variable=self.draw_mode_var,
            command=self.on_draw_mode_changed,
            bg=TEMPLATE_THEME["panel_soft"],
            fg=TEMPLATE_THEME["text"],
            selectcolor=TEMPLATE_THEME["panel"],
            activebackground=TEMPLATE_THEME["panel_soft"],
            activeforeground=TEMPLATE_THEME["primary"],
            font=(TEMPLATE_THEME["font"], 10),
        ).pack(side=tk.LEFT, padx=2)

        tk.Label(right_frame, text="当前模板预览", bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["text"], font=(TEMPLATE_THEME["font"], 10, "bold")).pack(anchor="w", pady=(8, 2))
        self.preview_label = tk.Label(right_frame, relief=tk.SUNKEN, bd=1, width=300, height=160, bg="white", highlightbackground=TEMPLATE_THEME["border"], highlightcolor=TEMPLATE_THEME["border"], highlightthickness=1)
        self.preview_label.pack(fill=tk.X)

        form_frame = tk.Frame(right_frame, bg=TEMPLATE_THEME["panel_soft"])
        form_frame.pack(fill=tk.X, pady=8)
        tk.Label(form_frame, text="文件名", bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["text"], font=(TEMPLATE_THEME["font"], 10)).grid(row=0, column=0, sticky="w")
        self.file_name_entry = tk.Entry(form_frame, textvariable=self.file_name_var, width=28, bg=TEMPLATE_THEME["panel"], fg=TEMPLATE_THEME["text"], insertbackground=TEMPLATE_THEME["text"], relief="solid", bd=1, font=(TEMPLATE_THEME["font"], 10))
        self.file_name_entry.grid(row=0, column=1, sticky="ew", padx=4)
        tk.Label(form_frame, text=".png", bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["muted"], font=(TEMPLATE_THEME["font"], 10)).grid(row=0, column=2, sticky="w")
        tk.Label(form_frame, text="批量前缀", bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["text"], font=(TEMPLATE_THEME["font"], 10)).grid(row=1, column=0, sticky="w", pady=(6, 0))
        tk.Entry(form_frame, textvariable=self.batch_prefix_var, width=28, bg=TEMPLATE_THEME["panel"], fg=TEMPLATE_THEME["text"], insertbackground=TEMPLATE_THEME["text"], relief="solid", bd=1, font=(TEMPLATE_THEME["font"], 10)).grid(row=1, column=1, sticky="ew", padx=4, pady=(6, 0))
        form_frame.columnconfigure(1, weight=1)

        button_frame = tk.Frame(right_frame, bg=TEMPLATE_THEME["panel_soft"])
        button_frame.pack(fill=tk.X)

        def _tone(text, command, tone):
            btn = tk.Button(button_frame, text=text, command=command)
            if tone == "primary":
                _paint_button(btn, TEMPLATE_THEME["primary_soft"], TEMPLATE_THEME["primary"], TEMPLATE_THEME["primary"])
            elif tone == "danger":
                _paint_button(btn, TEMPLATE_THEME["danger_soft"], TEMPLATE_THEME["danger"], TEMPLATE_THEME["danger"])
            else:
                _paint_button(btn, TEMPLATE_THEME["panel"], TEMPLATE_THEME["text"], TEMPLATE_THEME["primary_soft"], active_fg=TEMPLATE_THEME["primary"])
            btn.pack(fill=tk.X, pady=2)
            return btn

        _tone("OCR命名当前", self.ocr_name_current, "default")
        _tone("OCR命名选中", self.ocr_name_selected, "default")
        _tone("保存当前模板", self.save_current_template, "primary")
        _tone("保存并下一项", self.save_and_next, "primary")
        _tone("保存选中模板", self.save_selected_templates, "primary")
        _tone("导出预览拼图", self.export_contact_sheet, "default")
        _tone("导出带框原图", self.export_annotated_screenshot, "default")
        _tone("导出布局JSON", self.export_layout_json, "default")
        _tone("导入布局JSON", self.import_layout_json, "default")
        _tone("撤回(Ctrl+Z)", self.undo_last_action, "default")
        _tone("删除当前框", self.delete_current_region, "danger")
        _tone("删除选中框", self.delete_selected_regions, "danger")
        _tone("打开输出目录", self.open_output_dir, "default")

        help_text = (
            "使用建议:\n"
            "1. 先选整张界面截图，再点“自动检测”\n"
            "2. 点击候选框单选，Ctrl+点击可多选，Shift+拖拽可框选多个框\n"
            "3. 勾选“手动画框”后，可在左侧拖出新框\n"
            "4. 选中当前框后，可直接拖动移动，或拖拽边角改大小\n"
            "5. 可先做 OCR 命名，再保存到 image_templates 库\n"
            "6. 可导出当前所有框的预览拼图，快速检查切框质量\n"
            "7. 可导出带框原图，便于留档和回看标注结果\n"
            "8. 可导入/导出布局 JSON，方便下次恢复修好的框\n"
            "9. 选中多个框后可按 Delete 批量删除\n"
            "10. 支持 Ctrl+Z 撤回上一步框编辑或命名操作"
        )
        tk.Label(right_frame, text=help_text, justify=tk.LEFT, bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["muted"], font=(TEMPLATE_THEME["font"], 9)).pack(anchor="w", pady=(10, 0))
        tk.Label(right_frame, text=get_ocr_status_message(), justify=tk.LEFT, bg=TEMPLATE_THEME["panel_soft"], fg=TEMPLATE_THEME["muted"], font=(TEMPLATE_THEME["font"], 9)).pack(anchor="w", pady=(8, 0))
        tk.Label(
            right_frame,
            textvariable=self.category_summary_var,
            justify=tk.LEFT,
            bg=TEMPLATE_THEME["panel_soft"],
            fg=TEMPLATE_THEME["muted"],
            wraplength=320,
            font=(TEMPLATE_THEME["font"], 9),
        ).pack(anchor="w", pady=(8, 0))

        bottom_frame = tk.Frame(self.root, bg=TEMPLATE_THEME["toolbar"])
        bottom_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.status_label = tk.Label(bottom_frame, textvariable=self.status_var, anchor="w", bg=TEMPLATE_THEME["toolbar"], fg=TEMPLATE_THEME["text"], font=(TEMPLATE_THEME["font"], 10, "bold"))
        self.status_label.pack(fill=tk.X)

    def on_right_frame_configure(self, _event):
        self.right_canvas.configure(scrollregion=self.right_canvas.bbox("all"))

    def on_right_canvas_configure(self, event):
        self.right_canvas.itemconfigure(self.right_canvas_window, width=event.width)

    def on_mousewheel(self, event):
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        parent = widget
        while parent is not None:
            if parent == self.right_canvas:
                self.right_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return
            parent = parent.master

    def capture_state(self):
        return {
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "template_names": list(self.template_names),
            "selected_index": self.selected_index,
            "selected_indices": list(self.selected_indices),
            "file_name": self.file_name_var.get(),
            "batch_prefix": self.batch_prefix_var.get(),
        }

    def push_undo_state(self, snapshot=None):
        if snapshot is None:
            snapshot = self.capture_state()
        self.undo_stack.append(snapshot)
        if len(self.undo_stack) > self.max_undo_steps:
            self.undo_stack = self.undo_stack[-self.max_undo_steps:]

    def restore_state(self, snapshot):
        self.candidates = [
            CandidateRegion(
                x=int(item["x"]),
                y=int(item["y"]),
                w=int(item["w"]),
                h=int(item["h"]),
            )
            for item in snapshot.get("candidates", [])
        ]
        self.template_names = list(snapshot.get("template_names", []))
        self.selected_index = snapshot.get("selected_index")
        self.selected_indices = set(snapshot.get("selected_indices", []))
        self.file_name_var.set(snapshot.get("file_name", ""))
        self.batch_prefix_var.set(snapshot.get("batch_prefix", ""))

        if not self.candidates:
            self.clear_selection()
            self.refresh_listbox()
            self.preview_label.config(image="", text="")
            self.status_var.set("已撤回到空布局")
            return

        if self.selected_index is None or self.selected_index >= len(self.candidates):
            self.selected_index = 0
        self.selected_indices = {idx for idx in self.selected_indices if 0 <= idx < len(self.candidates)}
        if not self.selected_indices:
            self.selected_indices = {self.selected_index}
        self.file_name_var.set(self.template_names[self.selected_index] if self.selected_index < len(self.template_names) else "")
        self.refresh_listbox()
        self.update_preview()
        self.refresh_canvas()
        self.status_var.set("已撤回上一步操作")

    def undo_last_action(self):
        if not self.undo_stack:
            self.status_var.set("没有可撤回的操作")
            return
        snapshot = self.undo_stack.pop()
        self.restore_state(snapshot)

    def on_undo_shortcut(self, _event):
        self.undo_last_action()
        return "break"

    def take_screenshot(self):
        self.status_var.set("3秒后开始截屏，请切换到目标窗口...")
        self.root.update()
        for i in range(3, 0, -1):
            self.status_var.set(f"{i}秒后开始截屏...")
            self.root.update()
            time.sleep(1)
        
        try:
            screenshot = ImageGrab.grab()
            if screenshot is None:
                messagebox.showerror("错误", "截屏失败")
                return
            
            output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
            os.makedirs(output_root, exist_ok=True)
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = os.path.join(output_root, f"screenshot_{timestamp}.png")
            screenshot.save(screenshot_path)
            
            self.screenshot_path_var.set(screenshot_path)
            self.load_screenshot(screenshot_path)
            self.status_var.set(f"截屏已保存并加载: {screenshot_path}")
        except Exception as exc:
            messagebox.showerror("错误", f"截屏失败: {exc}")
    
    def choose_screenshot(self):
        file_path = filedialog.askopenfilename(
            title="选择界面截图",
            filetypes=[
                ("图片文件", "*.png;*.jpg;*.jpeg;*.bmp;*.webp"),
                ("所有文件", "*.*"),
            ],
        )
        if not file_path:
            return
        self.screenshot_path_var.set(file_path)
        self.load_screenshot(file_path)

    def choose_output_dir(self):
        output_dir = filedialog.askdirectory(title="选择模板输出目录", initialdir=self.output_dir_var.get())
        if output_dir:
            self.output_dir_var.set(output_dir)
            self.refresh_category_options()

    def refresh_category_options(self):
        output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
        categories = set()
        if os.path.isdir(output_root):
            for entry in os.listdir(output_root):
                entry_path = os.path.join(output_root, entry)
                if os.path.isdir(entry_path):
                    categories.add(entry)
        index_data = load_template_index(output_root)
        for item in index_data.get("categories", []):
            if isinstance(item, dict):
                category_id = normalize_template_category(item.get("id", "default"))
                if category_id:
                    categories.add(category_id)
        current_category = normalize_template_category(self.category_var.get().strip() or "projection")
        categories.add(current_category)
        self.category_options = sorted(categories)
        self.category_combo["values"] = self.category_options
        self.category_var.set(current_category)
        self.refresh_category_summary()

    def refresh_category_summary(self):
        output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
        index_data = load_template_index(output_root)
        meta = index_data.get("meta", {}) if isinstance(index_data, dict) else {}
        categories = index_data.get("categories", []) if isinstance(index_data, dict) else []
        if categories:
            summary_items = []
            for item in categories[:5]:
                if not isinstance(item, dict):
                    continue
                summary_items.append(f"{item.get('id', '')}:{item.get('count', 0)}")
            extra_suffix = ""
            if len(categories) > 5:
                extra_suffix = f" 等{len(categories)}类"
            self.category_summary_var.set(
                f"模板库概览：{meta.get('templateCount', 0)} 个模板 / {meta.get('categoryCount', len(categories))} 个分类"
                f"\n当前分类：{normalize_template_category(self.category_var.get().strip() or 'default')}"
                f"\n分类分布：{', '.join(summary_items) or '暂无'}{extra_suffix}"
            )
            return
        existing_dirs = []
        if os.path.isdir(output_root):
            existing_dirs = sorted(
                entry for entry in os.listdir(output_root) if os.path.isdir(os.path.join(output_root, entry))
            )
        self.category_summary_var.set(
            f"模板库概览：索引未建立"
            f"\n当前分类：{normalize_template_category(self.category_var.get().strip() or 'default')}"
            f"\n已存在目录：{', '.join(existing_dirs[:6]) if existing_dirs else '暂无'}"
        )

    def open_current_category_dir(self):
        output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
        category = normalize_template_category(self.category_var.get().strip() or "default")
        target_dir = os.path.join(output_root, category)
        os.makedirs(target_dir, exist_ok=True)
        os.startfile(target_dir)

    def load_screenshot(self, file_path):
        self.source_image_bgr = cv2.imread(file_path)
        if self.source_image_bgr is None:
            messagebox.showerror("读取失败", f"无法读取截图: {file_path}")
            return

        self.source_image_rgb = cv2.cvtColor(self.source_image_bgr, cv2.COLOR_BGR2RGB)
        self.candidates = []
        self.selected_index = None
        self.selected_indices = set()
        self.template_names = []
        self.undo_stack = []
        self.listbox.delete(0, tk.END)
        self.file_name_var.set("")
        self.preview_label.config(image="", text="")
        self.status_var.set("截图已加载，点击“自动检测”开始切分")
        self.refresh_canvas()

    def detect_regions(self):
        if self.source_image_bgr is None:
            screenshot_path = self.screenshot_path_var.get().strip()
            if not screenshot_path:
                messagebox.showwarning("提示", "请先选择一张界面截图")
                return
            self.load_screenshot(screenshot_path)
            if self.source_image_bgr is None:
                return

        self.push_undo_state()
        self.candidates = detect_candidate_regions(self.source_image_bgr)
        self.selected_index = 0 if self.candidates else None
        self.selected_indices = {0} if self.selected_index is not None else set()
        self.template_names = [
            f"{DEFAULT_TEMPLATE_PREFIX}_{index + 1:03d}" for index in range(len(self.candidates))
        ]
        self.refresh_listbox()

        if self.selected_index is not None:
            self.listbox.selection_set(self.selected_index)
            self.listbox.see(self.selected_index)
            self.update_preview()

        self.refresh_canvas()
        self.status_var.set(f"检测完成，共找到 {len(self.candidates)} 个候选区域")

    def image_point_from_event(self, event):
        if self.scale <= 0 or self.source_image_rgb is None:
            return None, None
        image_height, image_width = self.source_image_rgb.shape[:2]
        x = clamp(int(round(event.x / self.scale)), 0, image_width - 1)
        y = clamp(int(round(event.y / self.scale)), 0, image_height - 1)
        return x, y

    def point_in_region(self, region, x, y):
        return region.x <= x <= region.x + region.w and region.y <= y <= region.y + region.h

    def region_intersects(self, region_a, region_b):
        return not (
            region_a.x + region_a.w < region_b.x
            or region_b.x + region_b.w < region_a.x
            or region_a.y + region_a.h < region_b.y
            or region_b.y + region_b.h < region_a.y
        )

    def event_has_shift(self, event):
        return bool(event.state & 0x0001)

    def event_has_ctrl(self, event):
        return bool(event.state & 0x0004)

    def get_handle_radius_in_image(self):
        return max(4, int(round(HANDLE_SIZE / max(self.scale, 0.01))))

    def get_resize_handle(self, region, x, y):
        radius = self.get_handle_radius_in_image()
        left = region.x
        top = region.y
        right = region.x + region.w
        bottom = region.y + region.h
        center_x = left + region.w // 2
        center_y = top + region.h // 2

        handle_positions = {
            "nw": (left, top),
            "n": (center_x, top),
            "ne": (right, top),
            "e": (right, center_y),
            "se": (right, bottom),
            "s": (center_x, bottom),
            "sw": (left, bottom),
            "w": (left, center_y),
        }

        for handle_name, (hx, hy) in handle_positions.items():
            if abs(x - hx) <= radius and abs(y - hy) <= radius:
                return handle_name
        return None

    def find_candidate_index_at_point(self, x, y):
        for index in reversed(range(len(self.candidates))):
            if self.point_in_region(self.candidates[index], x, y):
                return index
        return None

    def add_candidate_region(self, region, select_new=True):
        self.candidates.append(region)
        self.template_names.append(f"{DEFAULT_TEMPLATE_PREFIX}_{len(self.candidates):03d}")
        new_index = len(self.candidates) - 1
        if select_new:
            self.selected_index = new_index
            self.selected_indices = {new_index}
            self.file_name_var.set(self.template_names[new_index])
        self.refresh_listbox()
        self.refresh_canvas()
        self.update_preview()
        return new_index

    def clear_selection(self):
        self.selected_index = None
        self.selected_indices = set()
        self.file_name_var.set("")
        self.listbox.selection_clear(0, tk.END)
        self.preview_label.config(image="", text="")
        self.refresh_canvas()

    def on_draw_mode_changed(self):
        if self.draw_mode_var.get():
            self.status_var.set("手动画框模式已开启：在左侧拖拽创建新框")
        else:
            self.status_var.set("手动画框模式已关闭：可选框、移动框、调整框")

    def refresh_canvas(self):
        self.canvas.delete("all")
        if self.source_image_rgb is None:
            return

        image_height, image_width = self.source_image_rgb.shape[:2]
        self.scale = min(CANVAS_MAX_WIDTH / image_width, CANVAS_MAX_HEIGHT / image_height, 1.0)
        display_width = int(image_width * self.scale)
        display_height = int(image_height * self.scale)

        display_image = Image.fromarray(self.source_image_rgb).resize((display_width, display_height), Image.LANCZOS)
        self.display_photo = ImageTk.PhotoImage(display_image)
        self.canvas.config(width=display_width, height=display_height, scrollregion=(0, 0, display_width, display_height))
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.display_photo)

        for index, region in enumerate(self.candidates):
            x1 = int(region.x * self.scale)
            y1 = int(region.y * self.scale)
            x2 = int((region.x + region.w) * self.scale)
            y2 = int((region.y + region.h) * self.scale)
            if index == self.selected_index:
                color = "#00ff7f"
            elif index in self.selected_indices:
                color = "#ffd166"
            else:
                color = "#ff6b6b"
            self.canvas.create_rectangle(x1, y1, x2, y2, outline=color, width=2)
            self.canvas.create_text(x1 + 4, y1 + 4, text=str(index + 1), anchor=tk.NW, fill=color)

            if index == self.selected_index:
                handle_radius = HANDLE_SIZE
                handle_points = [
                    (x1, y1),
                    ((x1 + x2) // 2, y1),
                    (x2, y1),
                    (x2, (y1 + y2) // 2),
                    (x2, y2),
                    ((x1 + x2) // 2, y2),
                    (x1, y2),
                    (x1, (y1 + y2) // 2),
                ]
                for hx, hy in handle_points:
                    self.canvas.create_rectangle(
                        hx - handle_radius,
                        hy - handle_radius,
                        hx + handle_radius,
                        hy + handle_radius,
                        fill="#00ff7f",
                        outline="#003b24",
                    )

        if self.temp_region is not None:
            x1 = int(self.temp_region.x * self.scale)
            y1 = int(self.temp_region.y * self.scale)
            x2 = int((self.temp_region.x + self.temp_region.w) * self.scale)
            y2 = int((self.temp_region.y + self.temp_region.h) * self.scale)
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#4dabf7", width=2, dash=(5, 3))
            self.canvas.create_text(x1 + 4, y1 + 4, text="new", anchor=tk.NW, fill="#4dabf7")

        if self.selection_region is not None:
            x1 = int(self.selection_region.x * self.scale)
            y1 = int(self.selection_region.y * self.scale)
            x2 = int((self.selection_region.x + self.selection_region.w) * self.scale)
            y2 = int((self.selection_region.y + self.selection_region.h) * self.scale)
            self.canvas.create_rectangle(x1, y1, x2, y2, outline="#74c0fc", width=2, dash=(6, 4))
            self.canvas.create_text(x1 + 4, y1 + 4, text="select", anchor=tk.NW, fill="#74c0fc")

    def on_canvas_press(self, event):
        if self.source_image_rgb is None:
            return

        x, y = self.image_point_from_event(event)
        if x is None:
            return

        self.drag_action = None
        self.drag_start = (x, y)
        self.drag_start_region = None
        self.drag_current_index = None
        self.active_handle = None
        self.drag_moved = False
        self.selection_region = None
        self.selection_additive = False

        ctrl_pressed = self.event_has_ctrl(event)
        shift_pressed = self.event_has_shift(event)

        if self.draw_mode_var.get():
            self.pre_drag_snapshot = self.capture_state()
            self.drag_action = "drawing"
            self.temp_region = CandidateRegion(x=x, y=y, w=1, h=1)
            self.refresh_canvas()
            return

        if shift_pressed:
            self.drag_action = "selecting"
            self.selection_additive = ctrl_pressed
            self.selection_region = CandidateRegion(x=x, y=y, w=1, h=1)
            self.status_var.set("正在框选候选框...")
            self.refresh_canvas()
            return

        clicked_index = self.find_candidate_index_at_point(x, y)
        if clicked_index is None:
            if ctrl_pressed:
                self.status_var.set("未命中候选框，当前多选保持不变")
                return
            self.clear_selection()
            self.status_var.set("未命中候选框，可切到手动画框模式新增区域")
            return

        if ctrl_pressed:
            self.toggle_candidate_selection(clicked_index)
            self.status_var.set(f"已切换框 #{clicked_index + 1} 的多选状态")
            return

        self.select_candidate(clicked_index)
        self.drag_current_index = clicked_index
        self.pre_drag_snapshot = self.capture_state()
        self.drag_start_region = CandidateRegion(
            x=self.candidates[clicked_index].x,
            y=self.candidates[clicked_index].y,
            w=self.candidates[clicked_index].w,
            h=self.candidates[clicked_index].h,
        )
        self.active_handle = self.get_resize_handle(self.drag_start_region, x, y)
        if self.active_handle:
            self.drag_action = "resizing"
            self.status_var.set(f"正在调整框 #{clicked_index + 1}")
        else:
            self.drag_action = "moving"
            self.status_var.set(f"正在移动框 #{clicked_index + 1}")

    def build_region_from_points(self, x1, y1, x2, y2):
        left = min(x1, x2)
        top = min(y1, y2)
        right = max(x1, x2)
        bottom = max(y1, y2)
        width = max(1, right - left)
        height = max(1, bottom - top)
        return CandidateRegion(x=left, y=top, w=width, h=height)

    def move_region(self, region, dx, dy):
        image_height, image_width = self.source_image_rgb.shape[:2]
        new_x = clamp(region.x + dx, 0, image_width - region.w)
        new_y = clamp(region.y + dy, 0, image_height - region.h)
        return CandidateRegion(x=new_x, y=new_y, w=region.w, h=region.h)

    def resize_region(self, region, handle_name, current_x, current_y):
        image_height, image_width = self.source_image_rgb.shape[:2]
        left = region.x
        top = region.y
        right = region.x + region.w
        bottom = region.y + region.h

        if "w" in handle_name:
            left = clamp(current_x, 0, right - MIN_REGION_SIZE)
        if "e" in handle_name:
            right = clamp(current_x, left + MIN_REGION_SIZE, image_width)
        if "n" in handle_name:
            top = clamp(current_y, 0, bottom - MIN_REGION_SIZE)
        if "s" in handle_name:
            bottom = clamp(current_y, top + MIN_REGION_SIZE, image_height)

        return CandidateRegion(x=left, y=top, w=right - left, h=bottom - top)

    def on_canvas_drag(self, event):
        if self.drag_action is None or self.source_image_rgb is None:
            return

        x, y = self.image_point_from_event(event)
        if x is None:
            return

        start_x, start_y = self.drag_start
        if abs(x - start_x) > 1 or abs(y - start_y) > 1:
            self.drag_moved = True

        if self.drag_action == "drawing":
            self.temp_region = self.build_region_from_points(start_x, start_y, x, y)
            self.refresh_canvas()
            return

        if self.drag_action == "selecting":
            self.selection_region = self.build_region_from_points(start_x, start_y, x, y)
            self.refresh_canvas()
            return

        if self.drag_current_index is None or self.drag_start_region is None:
            return

        if self.drag_action == "moving":
            dx = x - start_x
            dy = y - start_y
            self.candidates[self.drag_current_index] = self.move_region(self.drag_start_region, dx, dy)
        elif self.drag_action == "resizing" and self.active_handle:
            self.candidates[self.drag_current_index] = self.resize_region(
                self.drag_start_region,
                self.active_handle,
                x,
                y,
            )

        self.refresh_canvas()
        self.refresh_listbox()
        self.update_preview()

    def on_canvas_release(self, event):
        if self.source_image_rgb is None:
            return

        if self.drag_action == "drawing":
            region = self.temp_region
            self.temp_region = None
            if region and region.w >= MIN_REGION_SIZE and region.h >= MIN_REGION_SIZE:
                if self.pre_drag_snapshot is not None:
                    self.push_undo_state(self.pre_drag_snapshot)
                new_index = self.add_candidate_region(region, select_new=True)
                self.status_var.set(f"已新增手动画框 #{new_index + 1}")
            else:
                self.refresh_canvas()
                self.status_var.set("新框过小，未保存")
        elif self.drag_action == "selecting":
            selection_region = self.selection_region
            self.selection_region = None
            if selection_region and selection_region.w >= MIN_REGION_SIZE and selection_region.h >= MIN_REGION_SIZE:
                hit_indices = {
                    index
                    for index, candidate in enumerate(self.candidates)
                    if self.region_intersects(candidate, selection_region)
                }
                if self.selection_additive:
                    hit_indices = set(self.selected_indices) | hit_indices
                if hit_indices:
                    self.set_selected_indices(hit_indices, active_index=max(hit_indices))
                    self.status_var.set(f"框选完成，共选中 {len(hit_indices)} 个候选框")
                else:
                    if not self.selection_additive:
                        self.clear_selection()
                    self.refresh_canvas()
                    self.status_var.set("框选范围内未命中候选框")
            else:
                self.refresh_canvas()
                self.status_var.set("框选范围过小，未更新选择")
        elif self.drag_action in {"moving", "resizing"} and self.drag_current_index is not None:
            if self.drag_moved and self.pre_drag_snapshot is not None:
                self.push_undo_state(self.pre_drag_snapshot)
            self.refresh_listbox()
            self.update_preview()
            action_text = "移动" if self.drag_action == "moving" else "调整"
            self.status_var.set(f"已{action_text}框 #{self.drag_current_index + 1}")

        self.drag_action = None
        self.drag_start = None
        self.drag_start_region = None
        self.drag_current_index = None
        self.active_handle = None
        self.drag_moved = False
        self.selection_region = None
        self.selection_additive = False
        self.pre_drag_snapshot = None

    def on_listbox_select(self, _event):
        selected = self.listbox.curselection()
        if selected:
            self.selected_indices = set(selected)
            self.selected_index = selected[-1]
            self.file_name_var.set(self.template_names[self.selected_index])
            self.update_preview()
            self.refresh_canvas()

    def sync_selection_from_listbox(self):
        if not hasattr(self, "listbox"):
            return
        selected = self.listbox.curselection()
        if selected:
            self.selected_indices = set(selected)
            self.selected_index = selected[-1]
            if 0 <= self.selected_index < len(self.template_names):
                self.file_name_var.set(self.template_names[self.selected_index])
            return
        if self.selected_index is not None and 0 <= self.selected_index < len(self.candidates):
            self.selected_indices = {self.selected_index}
            return
        self.selected_indices = set()
        self.selected_index = None

    def format_candidate_label(self, index):
        region = self.candidates[index]
        template_name = self.template_names[index] if index < len(self.template_names) else ""
        return (
            f"{index + 1:03d}  [{template_name}]  "
            f"x={region.x} y={region.y} w={region.w} h={region.h}"
        )

    def refresh_listbox(self):
        selected = list(self.selected_indices)
        self.listbox.delete(0, tk.END)
        for index in range(len(self.candidates)):
            self.listbox.insert(tk.END, self.format_candidate_label(index))
        for index in selected:
            if 0 <= index < self.listbox.size():
                self.listbox.selection_set(index)

    def select_candidate(self, index, update_listbox=True):
        if index < 0 or index >= len(self.candidates):
            return
        self.selected_index = index
        self.selected_indices = {index}
        if update_listbox:
            self.listbox.selection_clear(0, tk.END)
            self.listbox.selection_set(index)
            self.listbox.see(index)
        self.file_name_var.set(self.template_names[index])
        self.update_preview()
        self.refresh_canvas()

    def set_selected_indices(self, indices, active_index=None, update_listbox=True):
        valid_indices = sorted({index for index in indices if 0 <= index < len(self.candidates)})
        if not valid_indices:
            self.clear_selection()
            return

        if active_index not in valid_indices:
            active_index = valid_indices[-1]

        self.selected_indices = set(valid_indices)
        self.selected_index = active_index
        self.file_name_var.set(self.template_names[active_index])
        if update_listbox:
            self.listbox.selection_clear(0, tk.END)
            for index in valid_indices:
                self.listbox.selection_set(index)
            self.listbox.see(active_index)
        self.update_preview()
        self.refresh_canvas()

    def toggle_candidate_selection(self, index):
        if index < 0 or index >= len(self.candidates):
            return

        updated_indices = set(self.selected_indices)
        if index in updated_indices:
            updated_indices.remove(index)
            if not updated_indices:
                self.clear_selection()
                return
            next_active_index = self.selected_index
            if next_active_index not in updated_indices:
                next_active_index = max(updated_indices)
            self.set_selected_indices(updated_indices, active_index=next_active_index)
            return

        updated_indices.add(index)
        self.set_selected_indices(updated_indices, active_index=index)

    def select_previous(self):
        if self.selected_index is None:
            return
        self.select_candidate(max(0, self.selected_index - 1))

    def select_next(self):
        if self.selected_index is None:
            return
        self.select_candidate(min(len(self.candidates) - 1, self.selected_index + 1))

    def get_selected_crop(self):
        if self.source_image_rgb is None or self.selected_index is None:
            return None, None
        region = self.candidates[self.selected_index]
        crop = self.source_image_rgb[region.y:region.y + region.h, region.x:region.x + region.w]
        return region, Image.fromarray(crop)

    def update_preview(self):
        region, crop_image = self.get_selected_crop()
        if region is None or crop_image is None:
            self.preview_label.config(image="", text="")
            return

        preview_width = 300
        preview_height = 160
        crop_image.thumbnail((preview_width, preview_height), Image.LANCZOS)
        self.preview_photo = ImageTk.PhotoImage(crop_image)
        self.preview_label.config(image=self.preview_photo)
        self.status_var.set(
            f"当前候选区域: #{self.selected_index + 1}  "
            f"x={region.x}, y={region.y}, w={region.w}, h={region.h}"
        )

    def ensure_ocr_available(self):
        if pytesseract is not None or TESSERACT_EXE:
            return True
        messagebox.showwarning(
            "OCR 未就绪",
            "当前未检测到 pytesseract 或 tesseract.exe。\n"
            "可安装 Tesseract OCR 后再使用 OCR 命名功能。",
        )
        self.status_var.set("OCR 不可用，请先安装 Tesseract OCR")
        return False

    def suggest_name_for_index(self, index):
        if index < 0 or index >= len(self.candidates):
            return ""

        region = self.candidates[index]
        crop = self.source_image_rgb[region.y:region.y + region.h, region.x:region.x + region.w]
        crop_image = Image.fromarray(crop)
        raw_text = extract_text_with_ocr(crop_image)
        fallback_name = f"{DEFAULT_TEMPLATE_PREFIX}_{index + 1:03d}"
        if raw_text:
            return sanitize_template_name(raw_text, fallback_name)
        return fallback_name

    def ocr_name_current(self):
        self.sync_selection_from_listbox()
        if self.selected_index is None:
            messagebox.showwarning("提示", "请先选择一个候选区域")
            return
        if not self.ensure_ocr_available():
            return

        try:
            self.push_undo_state()
            suggested_name = self.suggest_name_for_index(self.selected_index)
            self.template_names[self.selected_index] = suggested_name
            self.file_name_var.set(suggested_name)
            self.refresh_listbox()
            self.select_candidate(self.selected_index)
            self.status_var.set(f"当前模板 OCR 命名完成: {suggested_name}")
        except Exception as exc:
            messagebox.showerror("OCR 命名失败", f"OCR 命名当前失败：\n{exc}")
            self.status_var.set(f"OCR 命名当前失败: {exc}")

    def ocr_name_selected(self):
        self.sync_selection_from_listbox()
        if not self.selected_indices:
            messagebox.showwarning("提示", "请先在右侧列表中选择一个或多个候选区域")
            return
        if not self.ensure_ocr_available():
            return

        try:
            self.push_undo_state()
            renamed_count = 0
            for index in sorted(self.selected_indices):
                suggested_name = self.suggest_name_for_index(index)
                self.template_names[index] = suggested_name
                renamed_count += 1

            if self.selected_index is not None:
                self.file_name_var.set(self.template_names[self.selected_index])
            self.refresh_listbox()
            self.set_selected_indices(self.selected_indices, active_index=self.selected_index)
            self.status_var.set(f"OCR 批量命名完成，共处理 {renamed_count} 个候选区域")
        except Exception as exc:
            messagebox.showerror("OCR 批量命名失败", f"OCR 命名选中失败：\n{exc}")
            self.status_var.set(f"OCR 批量命名失败: {exc}")

    def save_region_by_index(self, index, file_name):
        if index < 0 or index >= len(self.candidates):
            return None

        region = self.candidates[index]
        crop = self.source_image_rgb[region.y:region.y + region.h, region.x:region.x + region.w]
        crop_image = Image.fromarray(crop)
        category = normalize_template_category(self.category_var.get().strip() or "default")
        self.category_var.set(category)
        output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
        output_dir = os.path.join(output_root, category)
        os.makedirs(output_dir, exist_ok=True)

        output_path = os.path.join(output_dir, f"{file_name}.png")
        crop_image.save(output_path)
        self._update_index_file(output_root, category, file_name, region, output_path)
        return output_path

    def save_current_template(self):
        region, crop_image = self.get_selected_crop()
        if region is None or crop_image is None:
            messagebox.showwarning("提示", "请先选择一个候选区域")
            return False

        file_name = sanitize_template_name(
            self.file_name_var.get().strip(),
            f"{DEFAULT_TEMPLATE_PREFIX}_{self.selected_index + 1:03d}",
        )
        if not file_name:
            messagebox.showwarning("提示", "请输入模板文件名")
            self.file_name_entry.focus_set()
            return False

        self.push_undo_state()
        self.template_names[self.selected_index] = file_name
        output_path = self.save_region_by_index(self.selected_index, file_name)
        self.file_name_var.set(file_name)
        self.refresh_listbox()
        self.status_var.set(f"已保存模板: {output_path}")
        return True

    def save_and_next(self):
        if self.save_current_template():
            self.select_next()

    def save_selected_templates(self):
        if not self.selected_indices:
            messagebox.showwarning("提示", "请先在右侧列表中选择一个或多个候选区域")
            return

        self.push_undo_state()
        saved_count = 0
        batch_prefix = sanitize_template_name(self.batch_prefix_var.get().strip(), "").strip("_")
        for index in sorted(self.selected_indices):
            fallback_name = f"{DEFAULT_TEMPLATE_PREFIX}_{index + 1:03d}"
            base_name = self.template_names[index] if index < len(self.template_names) else fallback_name
            base_name = sanitize_template_name(base_name, fallback_name)
            if batch_prefix:
                file_name = f"{batch_prefix}_{index + 1:03d}"
            else:
                file_name = base_name

            self.template_names[index] = file_name
            output_path = self.save_region_by_index(index, file_name)
            if output_path:
                saved_count += 1

        if self.selected_index is not None:
            self.file_name_var.set(self.template_names[self.selected_index])
        self.refresh_listbox()
        self.status_var.set(f"批量保存完成，共保存 {saved_count} 个模板")

    def export_contact_sheet(self):
        if self.source_image_rgb is None or not self.candidates:
            messagebox.showwarning("提示", "请先加载截图并生成至少一个候选框")
            return

        output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
        os.makedirs(output_root, exist_ok=True)

        screenshot_path = self.screenshot_path_var.get().strip()
        screenshot_base = os.path.splitext(os.path.basename(screenshot_path))[0] if screenshot_path else "screenshot"
        category = sanitize_template_name(self.category_var.get().strip() or "default", "default")
        output_path = os.path.join(output_root, f"{screenshot_base}_{category}_contact_sheet.png")

        font = ImageFont.load_default()
        cell_width = CONTACT_SHEET_CELL_WIDTH
        cell_height = CONTACT_SHEET_CELL_HEIGHT
        margin = CONTACT_SHEET_MARGIN
        columns = max(1, min(4, math.ceil(math.sqrt(len(self.candidates)))))
        rows = math.ceil(len(self.candidates) / columns)

        sheet_width = columns * cell_width + (columns + 1) * margin
        sheet_height = rows * cell_height + (rows + 1) * margin
        sheet = Image.new("RGB", (sheet_width, sheet_height), color=(245, 246, 248))
        draw = ImageDraw.Draw(sheet)

        for index, region in enumerate(self.candidates):
            row = index // columns
            col = index % columns
            cell_left = margin + col * (cell_width + margin)
            cell_top = margin + row * (cell_height + margin)
            cell_right = cell_left + cell_width
            cell_bottom = cell_top + cell_height

            draw.rounded_rectangle(
                (cell_left, cell_top, cell_right, cell_bottom),
                radius=10,
                fill=(255, 255, 255),
                outline=(210, 214, 220),
                width=2,
            )

            crop = self.source_image_rgb[region.y:region.y + region.h, region.x:region.x + region.w]
            crop_image = Image.fromarray(crop)
            preview_max_width = cell_width - 20
            preview_max_height = cell_height - 52
            crop_image.thumbnail((preview_max_width, preview_max_height), Image.LANCZOS)

            preview_x = cell_left + (cell_width - crop_image.width) // 2
            preview_y = cell_top + 10
            sheet.paste(crop_image, (preview_x, preview_y))

            template_name = self.template_names[index] if index < len(self.template_names) else f"{DEFAULT_TEMPLATE_PREFIX}_{index + 1:03d}"
            label = f"{index + 1:03d} | {template_name}"
            draw.text((cell_left + 8, cell_bottom - 34), label, fill=(35, 38, 43), font=font)
            draw.text(
                (cell_left + 8, cell_bottom - 18),
                f"{region.w}x{region.h} @ ({region.x},{region.y})",
                fill=(98, 104, 112),
                font=font,
            )

        sheet.save(output_path)
        self.status_var.set(f"预览拼图已导出: {output_path}")
        messagebox.showinfo("导出完成", f"预览拼图已保存到:\n{output_path}")

    def export_annotated_screenshot(self):
        if self.source_image_rgb is None or not self.candidates:
            messagebox.showwarning("提示", "请先加载截图并生成至少一个候选框")
            return

        output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
        os.makedirs(output_root, exist_ok=True)

        screenshot_path = self.screenshot_path_var.get().strip()
        screenshot_base = os.path.splitext(os.path.basename(screenshot_path))[0] if screenshot_path else "screenshot"
        category = sanitize_template_name(self.category_var.get().strip() or "default", "default")
        output_path = os.path.join(output_root, f"{screenshot_base}_{category}_annotated.png")

        annotated = Image.fromarray(self.source_image_rgb.copy())
        draw = ImageDraw.Draw(annotated)
        font = ImageFont.load_default()

        for index, region in enumerate(self.candidates):
            template_name = self.template_names[index] if index < len(self.template_names) else f"{DEFAULT_TEMPLATE_PREFIX}_{index + 1:03d}"
            if index == self.selected_index:
                color = (0, 255, 127)
            elif index in self.selected_indices:
                color = (255, 209, 102)
            else:
                color = (255, 107, 107)

            left = region.x
            top = region.y
            right = region.x + region.w
            bottom = region.y + region.h

            draw.rectangle((left, top, right, bottom), outline=color, width=3)
            label = f"{index + 1:03d} | {template_name}"

            text_bbox = draw.textbbox((0, 0), label, font=font)
            text_width = text_bbox[2] - text_bbox[0]
            text_height = text_bbox[3] - text_bbox[1]
            label_left = left
            label_top = max(0, top - text_height - 8)
            label_right = min(annotated.width, label_left + text_width + 10)
            label_bottom = label_top + text_height + 6
            draw.rounded_rectangle(
                (label_left, label_top, label_right, label_bottom),
                radius=4,
                fill=(255, 255, 255),
                outline=color,
                width=2,
            )
            draw.text((label_left + 5, label_top + 3), label, fill=(35, 38, 43), font=font)

        annotated.save(output_path)
        self.status_var.set(f"带框原图已导出: {output_path}")
        messagebox.showinfo("导出完成", f"带框原图已保存到:\n{output_path}")

    def export_layout_json(self):
        if self.source_image_rgb is None or not self.candidates:
            messagebox.showwarning("提示", "请先加载截图并生成至少一个候选框")
            return

        output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
        os.makedirs(output_root, exist_ok=True)
        screenshot_path = self.screenshot_path_var.get().strip()
        screenshot_base = os.path.splitext(os.path.basename(screenshot_path))[0] if screenshot_path else "screenshot"
        category = sanitize_template_name(self.category_var.get().strip() or "default", "default")
        default_path = os.path.join(output_root, f"{screenshot_base}_{category}_layout.json")

        output_path = filedialog.asksaveasfilename(
            title="导出布局 JSON",
            initialdir=output_root,
            initialfile=os.path.basename(default_path),
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
        )
        if not output_path:
            return

        image_height, image_width = self.source_image_rgb.shape[:2]
        layout_data = {
            "source_screenshot": screenshot_path,
            "category": self.category_var.get().strip() or "default",
            "image_width": image_width,
            "image_height": image_height,
            "candidates": [
                {
                    "file_name": self.template_names[index] if index < len(self.template_names) else f"{DEFAULT_TEMPLATE_PREFIX}_{index + 1:03d}",
                    "region": self.candidates[index].as_dict(),
                }
                for index in range(len(self.candidates))
            ],
        }

        with open(output_path, "w", encoding="utf-8") as file_obj:
            json.dump(layout_data, file_obj, ensure_ascii=False, indent=2)

        self.status_var.set(f"布局 JSON 已导出: {output_path}")
        messagebox.showinfo("导出完成", f"布局 JSON 已保存到:\n{output_path}")

    def import_layout_json(self):
        if self.source_image_rgb is None:
            messagebox.showwarning("提示", "请先加载一张截图，再导入布局 JSON")
            return

        output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
        input_path = filedialog.askopenfilename(
            title="导入布局 JSON",
            initialdir=output_root if os.path.isdir(output_root) else os.path.dirname(__file__),
            filetypes=[("JSON 文件", "*.json"), ("所有文件", "*.*")],
        )
        if not input_path:
            return

        try:
            with open(input_path, "r", encoding="utf-8") as file_obj:
                layout_data = json.load(file_obj)
        except Exception as exc:
            messagebox.showerror("读取失败", f"无法读取布局 JSON:\n{exc}")
            return

        raw_candidates = layout_data.get("candidates", [])
        if not raw_candidates:
            messagebox.showwarning("提示", "该布局 JSON 中没有可导入的候选框")
            return

        image_height, image_width = self.source_image_rgb.shape[:2]
        imported_regions = []
        imported_names = []
        for index, item in enumerate(raw_candidates):
            region_data = item.get("region", {})
            try:
                x = clamp(int(region_data.get("x", 0)), 0, image_width - 1)
                y = clamp(int(region_data.get("y", 0)), 0, image_height - 1)
                w = max(1, int(region_data.get("w", 1)))
                h = max(1, int(region_data.get("h", 1)))
            except Exception:
                continue

            w = min(w, image_width - x)
            h = min(h, image_height - y)
            if w < MIN_REGION_SIZE or h < MIN_REGION_SIZE:
                continue

            imported_regions.append(CandidateRegion(x=x, y=y, w=w, h=h))
            imported_names.append(
                sanitize_template_name(
                    item.get("file_name", ""),
                    f"{DEFAULT_TEMPLATE_PREFIX}_{index + 1:03d}",
                )
            )

        if not imported_regions:
            messagebox.showwarning("提示", "布局 JSON 中没有有效候选框可导入")
            return

        self.push_undo_state()
        self.candidates = imported_regions
        self.template_names = imported_names
        self.selected_index = 0
        self.selected_indices = {0}
        self.file_name_var.set(self.template_names[0])
        self.refresh_listbox()
        self.update_preview()
        self.refresh_canvas()
        self.status_var.set(f"已导入布局 JSON，共恢复 {len(self.candidates)} 个候选框")
        messagebox.showinfo("导入完成", f"已从布局 JSON 恢复 {len(self.candidates)} 个候选框")

    def delete_indices(self, indices):
        if not indices:
            return

        self.push_undo_state()
        for index in sorted(indices, reverse=True):
            if 0 <= index < len(self.candidates):
                del self.candidates[index]
                del self.template_names[index]

        if not self.candidates:
            self.clear_selection()
            self.refresh_listbox()
            self.status_var.set("已删除所有选中框")
            return

        new_index = min(min(indices), len(self.candidates) - 1)
        self.selected_index = new_index
        self.selected_indices = {new_index}
        self.file_name_var.set(self.template_names[new_index])
        self.refresh_listbox()
        self.update_preview()
        self.refresh_canvas()

    def delete_current_region(self):
        if self.selected_index is None:
            messagebox.showwarning("提示", "请先选择当前框")
            return
        self.delete_indices({self.selected_index})
        self.status_var.set("已删除当前框")

    def delete_selected_regions(self):
        if not self.selected_indices:
            messagebox.showwarning("提示", "请先在右侧列表中选择一个或多个框")
            return
        self.delete_indices(set(self.selected_indices))
        self.status_var.set("已删除选中框")

    def on_delete_key(self, _event):
        if self.selected_indices:
            self.delete_selected_regions()

    def _update_index_file(self, output_root, category, file_name, region, output_path):
        category = normalize_template_category(category)
        index_data = load_template_index(output_root)
        if "templates" not in index_data or not isinstance(index_data.get("templates"), list):
            index_data["templates"] = []

        record = {
            "category": category,
            "file_name": file_name,
            "image_path": output_path,
            "relative_image_path": safe_relpath(output_path, output_root),
            "source_screenshot": self.screenshot_path_var.get().strip(),
            "region": region.as_dict(),
            "updatedAt": datetime.now().isoformat(timespec="seconds"),
        }

        existing_index = next(
            (
                idx for idx, item in enumerate(index_data["templates"])
                if item.get("category") == category and item.get("file_name") == file_name
            ),
            None,
        )
        if existing_index is None:
            index_data["templates"].append(record)
        else:
            index_data["templates"][existing_index] = record

        save_template_index(output_root, index_data)
        self.refresh_category_options()
        self.refresh_category_summary()

    def open_output_dir(self):
        output_root = self.output_dir_var.get().strip() or DEFAULT_OUTPUT_DIR
        os.makedirs(output_root, exist_ok=True)
        os.startfile(output_root)


def main():
    wt_dpi.enable_process_dpi_awareness()
    root = tk.Tk()
    wt_dpi.compute_scale(root)
    app = TemplateBuilderApp(root)
    if os.path.isdir(DEFAULT_OUTPUT_DIR):
        app.output_dir_var.set(DEFAULT_OUTPUT_DIR)
    root.mainloop()


if __name__ == "__main__":
    main()
