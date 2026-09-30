#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
txt2wtg_gui.py  —— 图形界面版 TXT -> WTG 转换工具（现代化 UI 版本）

功能：
  - 选择/拖拽 TXT 功率曲线数据文件
  - 填写并校验机组参数（叶轮直径、额定功率、切入/切出风速、空气密度、轮毂高度等）
  - 实时绘制功率曲线与 Ct 推力系数曲线预览
  - 一键生成标准 .wtg 格式，并输出带彩色标记的详细校验与诊断日志
  - 记住最近参数，支持保存为个人默认设置
  - 现代化设计：Slate 调色板、圆角卡片、高 DPI 自适应、状态胶囊徽标、平滑 Hover 动效
"""

import os
import sys
import json
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(HERE)
if PARENT_DIR not in sys.path:
    sys.path.insert(0, PARENT_DIR)

CONFIG_PATH = os.path.join(HERE, "txt2wtg_gui_config.json")

# 引入核心业务解析模块
import txt2wtg_core as core

# 尝试引入工程基础设施模块（高 DPI 与统一设计系统）
try:
    import wt_dpi
    _HAS_WT_DPI = True
except Exception:
    wt_dpi = None
    _HAS_WT_DPI = False

try:
    import wt_theme
    _HAS_WT_THEME = True
except Exception:
    wt_theme = None
    _HAS_WT_THEME = False


# ── 本地降级调色板与设计令牌（确保脱离主工程时独立可用） ─────────────────────────
DEFAULT_PALETTE = {
    "bg": "#f8fafc",            # 窗口底色 Slate-50
    "surface": "#ffffff",       # 卡片底色
    "card": "#ffffff",
    "toolbar": "#f1f5f9",       # 浅灰条 Slate-100
    "border": "#e2e8f0",        # 分割线 Slate-200
    "border_dark": "#cbd5e1",   # 边框深色 Slate-300
    "primary": "#2563eb",       # 科技蓝 Blue-600
    "primary_hover": "#1d4ed8",
    "primary_active": "#1e40af",
    "primary_soft": "#dbeafe",  # Blue-100
    "primary_text": "#1e40af",
    "success": "#059669",       # 翡翠绿 Emerald-600
    "success_hover": "#047857",
    "success_active": "#065f46",
    "success_soft": "#d1fae5",
    "success_text": "#065f46",
    "danger": "#dc2626",        # 赤红 Red-600
    "danger_hover": "#b91c1c",
    "warning": "#d97706",       # 琥珀黄
    "warning_soft": "#fef3c7",
    "warning_text": "#92400e",
    "secondary": "#ffffff",
    "secondary_hover": "#f1f5f9",
    "secondary_active": "#e2e8f0",
    "text": "#0f172a",          # 正文 Slate-900
    "text_secondary": "#334155",
    "muted": "#64748b",         # 辅助文字 Slate-500
}


def _get_palette():
    if _HAS_WT_THEME and hasattr(wt_theme, "get_palette"):
        try:
            return wt_theme.get_palette()
        except Exception:
            pass
    return DEFAULT_PALETTE


def _scale_val(val):
    if _HAS_WT_DPI and hasattr(wt_dpi, "scale"):
        try:
            return wt_dpi.scale(val)
        except Exception:
            pass
    return int(val)


def _create_flat_button(parent, text, command=None, tone="secondary", font=None, padx=10, pady=5, width=None, state=tk.NORMAL):
    if _HAS_WT_THEME and hasattr(wt_theme, "create_flat_button"):
        try:
            return wt_theme.create_flat_button(
                parent, text=text, command=command, tone=tone, font=font,
                padx=padx, pady=pady, width=width, state=state,
            )
        except Exception:
            pass

    pal = _get_palette()
    fnt = font or ("Microsoft YaHei UI", 9)
    tones = {
        "primary": {"bg": pal["primary"], "fg": "#ffffff", "hover": pal["primary_hover"], "active": pal["primary_active"], "bd": 0},
        "success": {"bg": pal["success"], "fg": "#ffffff", "hover": pal["success_hover"], "active": pal["success_active"], "bd": 0},
        "danger": {"bg": pal["danger"], "fg": "#ffffff", "hover": pal["danger_hover"], "active": pal["danger_active"], "bd": 0},
        "secondary": {"bg": pal["secondary"], "fg": pal["text"], "hover": pal["secondary_hover"], "active": pal["secondary_active"], "bd": 1},
        "subtle": {"bg": pal["bg"], "fg": pal["muted"], "hover": pal["toolbar"], "active": pal["border"], "bd": 0},
    }
    t = tones.get(tone, tones["secondary"])
    btn = tk.Button(
        parent, text=text, command=command, bg=t["bg"], fg=t["fg"],
        activebackground=t["active"], activeforeground=t["fg"],
        relief=tk.FLAT, bd=0, padx=padx, pady=pady, font=fnt, state=state,
        cursor="hand2" if state != tk.DISABLED else "arrow",
    )
    if width:
        btn.configure(width=width)
    if t["bd"] > 0:
        btn.configure(highlightthickness=1, highlightbackground=pal["border"])

    def on_enter(_e):
        if btn["state"] != tk.DISABLED:
            btn.configure(bg=t["hover"])

    def on_leave(_e):
        if btn["state"] != tk.DISABLED:
            btn.configure(bg=t["bg"])

    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)
    return btn


def _create_badge(parent, text, tone="info", font=None):
    if _HAS_WT_THEME and hasattr(wt_theme, "create_badge"):
        try:
            return wt_theme.create_badge(parent, text=text, tone=tone, font=font)
        except Exception:
            pass

    pal = _get_palette()
    fnt = font or ("Microsoft YaHei UI", 8, "bold")
    tones = {
        "primary": {"bg": pal["primary_soft"], "fg": pal["primary_text"]},
        "success": {"bg": pal["success_soft"], "fg": pal["success_text"]},
        "warning": {"bg": pal["warning_soft"], "fg": pal["warning_text"]},
        "info": {"bg": pal["primary_soft"], "fg": pal["primary_text"]},
        "muted": {"bg": pal["toolbar"], "fg": pal["muted"]},
    }
    t = tones.get(tone, tones["muted"])
    badge = tk.Label(parent, text=text, bg=t["bg"], fg=t["fg"], font=fnt, padx=8, pady=2)

    def set_badge(new_text, new_tone="muted"):
        badge.configure(text=new_text)
        nt = tones.get(new_tone, tones["muted"])
        badge.configure(bg=nt["bg"], fg=nt["fg"])

    badge.set_badge = set_badge
    return badge


def _create_card_frame(parent, padx=12, pady=12, border=True, **kwargs):
    if _HAS_WT_THEME and hasattr(wt_theme, "create_card_frame"):
        try:
            return wt_theme.create_card_frame(parent, padx=padx, pady=pady, border=border, **kwargs)
        except Exception:
            pass

    pal = _get_palette()
    frame = tk.Frame(
        parent, bg=pal["card"], padx=padx, pady=pady,
        highlightthickness=1 if border else 0,
        highlightbackground=pal["border"] if border else pal["card"],
        **kwargs
    )
    return frame


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        # 进程级 DPI 声明
        if _HAS_WT_DPI and hasattr(wt_dpi, "enable_process_dpi_awareness"):
            try:
                wt_dpi.enable_process_dpi_awareness()
            except Exception:
                pass

        super().__init__()

        # 计算并注入缩放
        if _HAS_WT_DPI and hasattr(wt_dpi, "compute_scale"):
            try:
                wt_dpi.compute_scale(self)
            except Exception:
                pass

        self.pal = _get_palette()
        self.title("TXT → WTG 机组数据转换工具")
        self.configure(bg=self.pal["bg"])
        self.resizable(True, True)

        if _HAS_WT_DPI and hasattr(wt_dpi, "geometry"):
            wt_dpi.geometry(self, 860, 740)
            self.minsize(_scale_val(760), _scale_val(620))
        else:
            self.geometry("860x740")
            self.minsize(760, 620)

        try:
            self.iconbitmap()
        except Exception:
            pass

        self.cfg = load_config()
        self.input_path = tk.StringVar(value=self.cfg.get("input_path", ""))
        self.out_path = tk.StringVar(value=self.cfg.get("out_path", ""))

        self._build_widgets()
        self._enable_dragdrop()
        self._refresh_out_default()

    # ---- UI 布局 -------------------------------------------------------
    def _build_widgets(self):
        pal = self.pal
        pad_x = _scale_val(14)
        pad_y = _scale_val(8)

        # ── 1. 顶部 Header 引导卡片 ──
        top_card = _create_card_frame(self, padx=_scale_val(14), pady=_scale_val(10), border=False)
        top_card.pack(fill="x", padx=pad_x, pady=(pad_y, _scale_val(4)))

        top_left = tk.Frame(top_card, bg=pal["card"])
        top_left.pack(side="left", fill="both", expand=True)

        title_lbl = tk.Label(
            top_left,
            text="TXT → WTG 机组数据转换工具",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=pal["card"],
            fg=pal["text"],
            anchor="w",
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            top_left,
            text="将功率曲线数据转换为标准 WTG 仿真格式，支持曲线实时预览、参数校验与默认配置保存。",
            font=("Microsoft YaHei UI", 9),
            bg=pal["card"],
            fg=pal["muted"],
            anchor="w",
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        self.badge_status = _create_badge(top_card, "就绪 · 待选择文件", tone="muted")
        self.badge_status.pack(side="right", anchor="center")

        # ── 2. 文件输入/输出卡片 ──
        file_card = _create_card_frame(self, padx=_scale_val(14), pady=_scale_val(10))
        file_card.pack(fill="x", padx=pad_x, pady=(0, _scale_val(6)))

        # 输入文件行
        r1 = tk.Frame(file_card, bg=pal["card"])
        r1.pack(fill="x", pady=(0, _scale_val(6)))
        tk.Label(r1, text="输入 TXT 数据：", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"], width=13, anchor="w").pack(side="left")
        e_in = tk.Entry(r1, textvariable=self.input_path, font=("Microsoft YaHei UI", 9), highlightthickness=1, highlightbackground=pal["border"], bd=0)
        e_in.pack(side="left", fill="x", expand=True, padx=(0, _scale_val(8)), ipady=3)
        _create_flat_button(r1, "浏览...", command=self._browse_input, tone="secondary", width=8).pack(side="left")

        # 输出文件行
        r2 = tk.Frame(file_card, bg=pal["card"])
        r2.pack(fill="x")
        tk.Label(r2, text="输出 WTG 文件：", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"], width=13, anchor="w").pack(side="left")
        e_out = tk.Entry(r2, textvariable=self.out_path, font=("Microsoft YaHei UI", 9), highlightthickness=1, highlightbackground=pal["border"], bd=0)
        e_out.pack(side="left", fill="x", expand=True, padx=(0, _scale_val(8)), ipady=3)
        _create_flat_button(r2, "更改...", command=self._browse_output, tone="secondary", width=8).pack(side="left")

        # ── 3. 机组核心参数卡片 ──
        param_card = _create_card_frame(self, padx=_scale_val(14), pady=_scale_val(10))
        param_card.pack(fill="x", padx=pad_x, pady=(0, _scale_val(6)))

        param_head = tk.Frame(param_card, bg=pal["card"])
        param_head.pack(fill="x", pady=(0, _scale_val(8)))
        tk.Label(param_head, text="机组核心参数与仿真边界", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"]).pack(side="left")
        _create_flat_button(param_head, "💾 保存为默认配置", command=self._save_default_params, tone="subtle", font=("Microsoft YaHei UI", 8)).pack(side="right")

        # 参数网格
        p_grid = tk.Frame(param_card, bg=pal["card"])
        p_grid.pack(fill="x")

        fields = [
            ("diameter", "叶轮直径 D *", "220", "米"),
            ("rated", "额定功率 P *", "6250", "kW"),
            ("cutin", "切入风速", "3.0", "m/s"),
            ("cutout", "切出风速", "25.0", "m/s"),
            ("airdensity", "空气密度", "1.225", "kg/m³"),
            ("hubheight", "轮毂高度", "120.0", "米"),
            ("manufacturer", "机组制造商", "User", ""),
        ]
        self.vars = {}
        for i, (key, label, default, unit) in enumerate(fields):
            row = i // 2
            col = (i % 2) * 3

            tk.Label(
                p_grid, text=label + "：", font=("Microsoft YaHei UI", 9),
                bg=pal["card"], fg=pal["text_secondary"], anchor="e",
            ).grid(row=row, column=col, sticky="e", padx=(8, 4), pady=3)

            v = tk.StringVar(value=str(self.cfg.get(key, default)))
            self.vars[key] = v

            entry_frame = tk.Frame(p_grid, bg=pal["card"])
            entry_frame.grid(row=row, column=col + 1, sticky="w", padx=(0, 2), pady=3)

            e = tk.Entry(entry_frame, textvariable=v, width=14, font=("Microsoft YaHei UI", 9), highlightthickness=1, highlightbackground=pal["border"], bd=0)
            e.pack(side="left", ipady=2)

            if unit:
                tk.Label(entry_frame, text=unit, font=("Microsoft YaHei UI", 8), bg=pal["card"], fg=pal["muted"]).pack(side="left", padx=(3, 0))

        p_grid.columnconfigure(1, weight=1)
        p_grid.columnconfigure(4, weight=1)

        # ── 4. 预览与日志多标签卡片 ──
        tab_card = _create_card_frame(self, padx=_scale_val(12), pady=_scale_val(8))
        tab_card.pack(fill="both", expand=True, padx=pad_x, pady=(0, _scale_val(6)))

        self.notebook = ttk.Notebook(tab_card)
        self.notebook.pack(fill="both", expand=True)

        # Tab 1: 曲线预览
        tab_prev = tk.Frame(self.notebook, bg=pal["card"], padx=6, pady=6)
        self.notebook.add(tab_prev, text="  📈 功率与推力曲线预览  ")

        prev_ctrl = tk.Frame(tab_prev, bg=pal["card"])
        prev_ctrl.pack(fill="x", pady=(0, 4))
        tk.Label(prev_ctrl, text="曲线说明：蓝色为输出功率曲线 (kW)，橙色为推力系数 Ct 曲线", font=("Microsoft YaHei UI", 8), bg=pal["card"], fg=pal["muted"]).pack(side="left")
        _create_flat_button(prev_ctrl, "⟳ 刷新预览", command=self._draw_preview, tone="subtle", font=("Microsoft YaHei UI", 8)).pack(side="right")

        self.preview = tk.Canvas(tab_prev, height=_scale_val(180), bg="#ffffff", highlightthickness=1, highlightbackground=pal["border"])
        self.preview.pack(fill="both", expand=True)
        self.preview.bind("<Configure>", lambda e: self._draw_preview())

        # Tab 2: 运行日志
        tab_log = tk.Frame(self.notebook, bg=pal["card"], padx=6, pady=6)
        self.notebook.add(tab_log, text="  📋 诊断与转换日志  ")

        log_ctrl = tk.Frame(tab_log, bg=pal["card"])
        log_ctrl.pack(fill="x", pady=(0, 4))
        tk.Label(log_ctrl, text="日志输出包含单调性检查、极限风速判定与 WTG 写入报告", font=("Microsoft YaHei UI", 8), bg=pal["card"], fg=pal["muted"]).pack(side="left")
        _create_flat_button(log_ctrl, "清空日志", command=self._clear_log, tone="subtle", font=("Microsoft YaHei UI", 8)).pack(side="right")

        self.log = scrolledtext.ScrolledText(tab_log, height=10, wrap="word", font=("Consolas", 9), relief=tk.FLAT, bd=0, highlightthickness=1, highlightbackground=pal["border"])
        self.log.pack(fill="both", expand=True)
        self.log.tag_config("err", foreground=pal["danger"])
        self.log.tag_config("ok", foreground=pal["success"])
        self.log.tag_config("warn", foreground=pal["warning"])
        self.log.tag_config("info", foreground=pal["text"])

        # ── 5. 底部执行与状态条 ──
        footer = tk.Frame(self, bg=pal["bg"])
        footer.pack(fill="x", padx=pad_x, pady=(0, pad_y))

        self.status = tk.Label(footer, text="就绪，请选择 TXT 文件", anchor="w", bg=pal["bg"], fg=pal["muted"], font=("Microsoft YaHei UI", 9))
        self.status.pack(side="left", fill="x", expand=True)

        self.btn_run = _create_flat_button(
            footer,
            text="▶ 一键生成 .WTG 机组文件",
            command=self._run,
            tone="success",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=_scale_val(20),
            pady=_scale_val(6),
        )
        self.btn_run.pack(side="right")

    # ---- 文件选择 ------------------------------------------------------
    def _browse_input(self):
        p = filedialog.askopenfilename(
            title="选择 TXT 功率曲线数据文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if p:
            self.input_path.set(p)
            self._refresh_out_default()
            self._draw_preview()
            self.badge_status.set_badge("文件已载入", "info")

    def _browse_output(self):
        p = filedialog.asksaveasfilename(
            title="保存 WTG 文件",
            defaultextension=".wtg",
            filetypes=[("WTG 文件", "*.wtg"), ("所有文件", "*.*")])
        if p:
            self.out_path.set(p)

    def _refresh_out_default(self):
        if not self.out_path.get() and self.input_path.get():
            base = os.path.splitext(self.input_path.get())[0]
            self.out_path.set(base + ".wtg")

    def _save_default_params(self):
        """保存当前机组参数为下次默认。"""
        try:
            d = float(self.vars["diameter"].get())
            r = float(self.vars["rated"].get())
            ci = float(self.vars["cutin"].get())
            co = float(self.vars["cutout"].get())
            rho = float(self.vars["airdensity"].get())
            hh = float(self.vars["hubheight"].get())
            mfr = self.vars["manufacturer"].get().strip() or "User"
            self.cfg.update({
                "diameter": d, "rated": r, "cutin": ci, "cutout": co,
                "airdensity": rho, "hubheight": hh, "manufacturer": mfr,
            })
            save_config(self.cfg)
            self.status.config(text="已成功将当前参数保存为默认配置")
            messagebox.showinfo("保存成功", "机组常用参数已保存！下次打开工具将自动填入。")
        except ValueError as e:
            messagebox.showerror("参数错误", f"参数格式不正确，无法保存：\n{e}")

    # ---- 拖拽支持（Windows WM_DROPFILES）-------------------------------
    def _enable_dragdrop(self):
        try:
            import ctypes
            from ctypes import wintypes
            ole = ctypes.windll.shell32
            WM_DROPFILES = 0x0233

            def wnd_proc(hwnd, msg, wp, lp):
                if msg == WM_DROPFILES:
                    count = ole.DragQueryFile(wp, 0xFFFFFFFF, None, 0)
                    buf = ctypes.create_unicode_buffer(260 * count)
                    names = []
                    for i in range(count):
                        ole.DragQueryFile(wp, i, buf, 260)
                        names.append(buf.value)
                    ole.DragFinish(wp)
                    if names:
                        self.input_path.set(names[0])
                        self._refresh_out_default()
                        self._log(f"已拖入数据文件：{names[0]}")
                        self._draw_preview()
                        self.badge_status.set_badge("文件已载入", "info")
                    return 1
                return ctypes.windll.user32.CallWindowProcW(
                    self._old_proc, hwnd, msg, wp, lp)

            self._hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            WNDPROC = ctypes.WINFUNCTYPE(
                wintypes.LPARAM, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM)
            self._new_proc = WNDPROC(wnd_proc)
            self._old_proc = ctypes.windll.user32.SetWindowLongPtrW(
                self._hwnd, -4, self._new_proc)
            ole.DragAcceptFiles(self._hwnd, True)
        except Exception as e:
            self._log(f"(提示) 拖拽功能未就绪：{e}")

    # ---- 曲线预览绘制 --------------------------------------------------
    def _draw_preview(self):
        cv = self.preview
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 10 or h < 10:
            return

        in_p = self.input_path.get().strip()
        if not in_p or not os.path.isfile(in_p):
            cv.create_text(w / 2, h / 2, text="（未选择数据文件，请先选择或拖拽 TXT 文件）",
                           fill=self.pal["muted"], font=("Microsoft YaHei UI", 10))
            return

        try:
            data = core.parse_txt(in_p, verbose=False)
        except Exception as e:
            cv.create_text(w / 2, h / 2, text=f"解析失败：{e}",
                           fill=self.pal["danger"], font=("Microsoft YaHei UI", 10))
            return

        if not data:
            cv.create_text(w / 2, h / 2, text="未在文件中解析到有效数据点",
                           fill=self.pal["muted"], font=("Microsoft YaHei UI", 10))
            return

        # 边距
        ml, mr, mt, mb = 56, 56, 20, 32
        px0, px1 = ml, w - mr
        py0, py1 = h - mb, mt

        ws = [d[0] for d in data]
        pw = [d[1] for d in data]
        ct = [d[2] for d in data]

        x_min, x_max = 0.0, max(ws) * 1.02 or 1.0
        y_p_max = (max(pw) * 1.05) or 1.0
        y_c_max = max(0.2, min(1.0, max(ct) * 1.1))

        def sx(v):
            return px0 + (v - x_min) / (x_max - x_min) * (px1 - px0)

        def sy_p(v):
            return py0 - (v / y_p_max) * (py0 - py1)

        def sy_c(v):
            return py0 - (v / y_c_max) * (py0 - py1)

        # 绘制浅灰网格与双轴坐标标注
        for i in range(5):
            gy = py0 - i * (py0 - py1) / 4
            cv.create_line(px0, gy, px1, gy, fill=self.pal["border"])
            cv.create_text(px0 - 8, gy, text=f"{y_p_max * i / 4:.0f}",
                           fill="#2563eb", font=("Microsoft YaHei UI", 8),
                           anchor="e")
            cv.create_text(px1 + 8, gy, text=f"{y_c_max * i / 4:.2f}",
                           fill="#ea580c", font=("Microsoft YaHei UI", 8),
                           anchor="w")

        for i in range(5):
            gx = px0 + i * (px1 - px0) / 4
            cv.create_line(gx, py0, gx, py1, fill=self.pal["border"])
            cv.create_text(gx, py0 + 12, text=f"{x_min + (x_max - x_min) * i / 4:.1f}",
                           fill=self.pal["text_secondary"], font=("Microsoft YaHei UI", 8), anchor="n")

        # 额定功率虚线
        try:
            r = float(self.vars["rated"].get())
            if 0 < r <= y_p_max:
                y = sy_p(r)
                cv.create_line(px0, y, px1, y, fill="#2563eb", dash=(4, 3))
                cv.create_text(px1 - 4, y - 8, text=f"额定 {r:.0f} kW",
                               fill="#2563eb", font=("Microsoft YaHei UI", 8, "bold"),
                               anchor="e")
        except ValueError:
            pass

        # 绘制曲线
        if len(data) > 1:
            cv.create_line([(sx(v), sy_p(p)) for v, p in zip(ws, pw)],
                           fill="#2563eb", width=2, smooth=True)
            cv.create_line([(sx(v), sy_c(c)) for v, c in zip(ws, ct)],
                           fill="#ea580c", width=2, smooth=True)
        else:
            cv.create_oval(sx(ws[0]) - 3, sy_p(pw[0]) - 3,
                           sx(ws[0]) + 3, sy_p(pw[0]) + 3, fill="#2563eb")

        # 轴标题
        cv.create_text((px0 + px1) / 2, h - 8, text="风速 (m/s)",
                       fill=self.pal["text"], font=("Microsoft YaHei UI", 9))
        cv.create_text(14, (py0 + py1) / 2, text="输出功率 (kW)",
                       fill="#2563eb", font=("Microsoft YaHei UI", 9, "bold"), angle=90)
        cv.create_text(w - 10, (py0 + py1) / 2, text="推力系数 Ct",
                       fill="#ea580c", font=("Microsoft YaHei UI", 9, "bold"), angle=90)

        self.status.configure(text=f"曲线预览已生成：共 {len(data)} 个风速-功率数据点")
        self.badge_status.set_badge(f"已解析 {len(data)} 点", "success")

    def _log(self, msg, level="info"):
        self.log.insert("end", msg + "\n", level)
        self.log.see("end")

    def _clear_log(self):
        self.log.delete("1.0", "end")

    # ---- 核心运行 ------------------------------------------------------
    def _run(self):
        in_p = self.input_path.get().strip()
        if not in_p:
            messagebox.showwarning("提示", "请先选择输入 TXT 数据文件。")
            return
        if not os.path.isfile(in_p):
            messagebox.showerror("错误", f"输入文件不存在：{in_p}")
            return

        out_p = self.out_path.get().strip() or (os.path.splitext(in_p)[0] + ".wtg")

        try:
            d = float(self.vars["diameter"].get())
            r = float(self.vars["rated"].get())
            if d <= 0 or r <= 0:
                raise ValueError("叶轮直径和额定功率必须为大于 0 的有效数值")
            ci = float(self.vars["cutin"].get())
            co = float(self.vars["cutout"].get())
            if ci >= co:
                raise ValueError(f"切入风速 ({ci} m/s) 不能大于或等于切出风速 ({co} m/s)")
            rho = float(self.vars["airdensity"].get())
            hh = float(self.vars["hubheight"].get())
            mfr = self.vars["manufacturer"].get().strip() or "User"
        except ValueError as e:
            messagebox.showerror("参数格式错误", f"机组参数填写不合法：\n{e}")
            return

        self.btn_run.configure(state=tk.DISABLED)
        self.status.configure(text="正在转换并校验机组数据...")
        self.update_idletasks()

        self._log(f"[{os.path.basename(in_p)}] 开始生成 WTG 格式机组文件...", "info")
        try:
            out_p, data, warnings = core.convert_file(
                in_p, out_p, d, r, ci, co, rho, hh, mfr, verbose=False)
            self._log(f"✅ 数据解析成功：提取 {len(data)} 组功率与 Ct 映射点", "ok")
            for w in warnings:
                self._log(f"⚠️  {w}", "warn")
            self._log(f"✅ 生成成功：{out_p}", "ok")
            self._log(
                f"   [机组参数] 额定功率: {r / 1000:.2f} MW | 叶轮直径: {d:.1f} m | 轮毂高度: {hh:.1f} m",
                "ok"
            )
            self.status.configure(text=f"生成成功：{os.path.basename(out_p)}")
            self.badge_status.set_badge("生成完毕", "success")

            # 同步配置记忆
            self.cfg.update({
                "input_path": in_p, "out_path": out_p,
                "diameter": d, "rated": r, "cutin": ci, "cutout": co,
                "airdensity": rho, "hubheight": hh, "manufacturer": mfr,
            })
            save_config(self.cfg)

            messagebox.showinfo(
                "转换完成",
                f"WTG 文件生成完毕！\n\n"
                f"• 目标文件: {out_p}\n"
                f"• 数据点数: {len(data)} 点\n"
                f"• 机型规格: {d:.1f}m / {r:.0f}kW",
            )
        except Exception as e:
            self._log(f"❌ 发生错误：{e}", "err")
            self.status.configure(text="转换失败，请查阅日志")
            self.badge_status.set_badge("处理失败", "warning")
            messagebox.showerror("生成失败", f"转换过程中发生错误：\n{e}")
        finally:
            self.btn_run.configure(state=tk.NORMAL)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
