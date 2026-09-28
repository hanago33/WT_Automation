# encoding: utf-8
"""WT Automation 统一现代化主题引擎与设计系统。

功能与设计目标：
1. 统一设计令牌（Design Tokens）：提供现代深/浅色彩、边框、间距、字体规范。
2. 双轨驱动架构（Progressive Modernization）：
   - 优先加载 ttkbootstrap 现代主题（如 flatly/cosmo），为 TTK 控件带来平滑现代视觉；
   - 若环境未安装 ttkbootstrap，自动降级为标准库 ttk.Style('clam') 现代定制，
     消除 Windows 旧式灰色凸起，保持 100% 跨平台与离线稳定性。
3. 高 DPI 协同：结合 wt_dpi.py 自动缩放行高、字号与边距。
4. 现代 UI 组件构建器：现代扁平按钮（带 Hover 动效）、药丸状态徽标、卡片容器、分段选择器等。
"""

import os
import sys
import tkinter as tk
from tkinter import ttk

import wt_dpi

# 是否支持 ttkbootstrap
_HAS_TTKBOOTSTRAP = False
tb = None
try:
    import ttkbootstrap as tb
    _HAS_TTKBOOTSTRAP = True
except Exception:
    tb = None
    _HAS_TTKBOOTSTRAP = False


# ── 全局设计令牌 (Design Tokens) ─────────────────────────────────────────────
PALETTE = {
    # 背景与面板
    "bg": "#f8fafc",            # 软件主底色（Slate-50 极浅灰蓝，护眼）
    "surface": "#ffffff",       # 卡片/主工作区纯白底
    "card": "#ffffff",          # 卡片背景
    "card_hover": "#f1f5f9",    # 卡片悬停微亮
    "toolbar": "#f1f5f9",       # 顶部操作栏底色（Slate-100）
    "panel": "#ffffff",         # 侧边面板底色
    "panel_soft": "#f8fafc",    # 柔和次级底色
    "border": "#e2e8f0",        # 柔和分割线（Slate-200）
    "border_dark": "#cbd5e1",   # 边框深色/悬停（Slate-300）
    "border_focus": "#3b82f6",  # 聚焦边框（Blue-500）

    # 语义色：主色（科技蓝）
    "primary": "#2563eb",       # Blue-600
    "primary_hover": "#1d4ed8", # Blue-700
    "primary_active": "#1e40af",# Blue-800
    "primary_soft": "#dbeafe",  # Blue-100
    "primary_text": "#1e40af",  # 浅底上的文字色

    # 语义色：成功（翠绿）
    "success": "#059669",       # Emerald-600
    "success_hover": "#047857", # Emerald-700
    "success_active": "#065f46",# Emerald-800
    "success_soft": "#d1fae5",  # Emerald-100
    "success_text": "#065f46",

    # 语义色：警告（暖琥珀）
    "warning": "#d97706",       # Amber-600
    "warning_hover": "#b45309", # Amber-700
    "warning_active": "#92400e",# Amber-800
    "warning_soft": "#fef3c7",  # Amber-100
    "warning_text": "#92400e",

    # 语义色：危险/错误（赤红）
    "danger": "#dc2626",        # Red-600
    "danger_hover": "#b91c1c",  # Red-700
    "danger_active": "#991b1b", # Red-800
    "danger_soft": "#fee2e2",   # Red-100
    "danger_text": "#991b1b",

    # 语义色：信息（青蓝）
    "info": "#0284c7",          # Sky-600
    "info_hover": "#0369a1",    # Sky-700
    "info_soft": "#e0f2fe",     # Sky-100
    "info_text": "#075985",

    # 纯白次级按钮
    "secondary": "#ffffff",
    "secondary_hover": "#f1f5f9",
    "secondary_active": "#e2e8f0",

    # 文本色阶
    "text": "#0f172a",          # 标题/主要正文（Slate-900）
    "text_secondary": "#334155",# 次要正文（Slate-700）
    "muted": "#64748b",         # 辅助与弱化文字（Slate-500）
    "text_disabled": "#94a3b8", # 禁用文字

    # 深色控制台 / 日志背景
    "terminal_bg": "#0f172a",   # Slate-900
    "terminal_fg": "#f8fafc",   # 白色代码文字
}


# ── 日志级别配色（唯一权威定义，供 wt_logging 与各日志控件引用） ──────────────
# 约定：遵循通用日志级别语义 —— DEBUG 弱化灰 / INFO 正文 / WARN 琥珀 / ERROR 赤红 /
# CRIT 深红。浅底与深底保持同一色相，只调整明度以适配背景。
# 说明：浅底 WARN 取 Amber-700 而非 Amber-600，是为满足 9~10px 小字在浅底上的对比度。
LOG_LEVEL_COLORS = {
    "light": {
        "DEBUG": "#94a3b8",     # Slate-400 弱化
        "INFO": "#0f172a",      # Slate-900 正文
        "WARN": "#b45309",      # Amber-700
        "ERROR": "#dc2626",     # Red-600
        "CRIT": "#991b1b",      # Red-800
        "debug": "#94a3b8",
        "info": "#0f172a",
        "warning": "#b45309",
        "error": "#dc2626",
        "success": "#047857",   # Emerald-700
        "system": "#0369a1",    # Sky-700
        "muted": "#64748b",
    },
    "dark": {
        "DEBUG": "#64748b",     # Slate-500
        "INFO": "#e2e8f0",      # Slate-200
        "WARN": "#fbbf24",      # Amber-400
        "ERROR": "#f87171",     # Red-400
        "CRIT": "#fca5a5",      # Red-300
        "debug": "#64748b",
        "info": "#e2e8f0",
        "warning": "#fbbf24",
        "error": "#f87171",
        "success": "#34d399",   # Emerald-400
        "system": "#60a5fa",    # Blue-400
        "muted": "#94a3b8",
    },
}


def has_ttkbootstrap():
    """返回当前环境是否启用了 ttkbootstrap。"""
    return _HAS_TTKBOOTSTRAP


class WTThemeManager:
    """WT 统一主题管理器（单例模式）。"""
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(WTThemeManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.palette = dict(PALETTE)
        self.style = None
        self.root = None
        self.using_tb = False
        self._initialized = True

    def init_theme(self, root, preferred_theme="flatly"):
        """为指定的 Tk 根窗口初始化现代主题。"""
        self.root = root
        try:
            root.configure(bg=self.palette["bg"])
        except Exception:
            pass

        scale = wt_dpi.get_scale()
        row_height = max(26, int(round(28 * scale)))
        header_height = max(28, int(round(30 * scale)))

        if _HAS_TTKBOOTSTRAP and tb is not None:
            try:
                # 尝试加载 ttkbootstrap 现代主题
                self.style = tb.Style(theme=preferred_theme)
                self.using_tb = True
            except Exception:
                self.style = ttk.Style(root)
                self.using_tb = False
        else:
            self.style = ttk.Style(root)
            self.using_tb = False

        if not self.using_tb:
            try:
                self.style.theme_use("clam")
            except Exception:
                pass

        self._configure_custom_styles(row_height, header_height)
        return self.style

    def _configure_custom_styles(self, row_height, header_height):
        """配置 TTK 核心控件样式，保证双轨视觉一致。"""
        style = self.style
        pal = self.palette

        # ── Treeview 表格美化 ──
        style.configure(
            "Treeview",
            background=pal["surface"],
            fieldbackground=pal["surface"],
            foreground=pal["text"],
            bordercolor=pal["border"],
            rowheight=row_height,
            font=("Microsoft YaHei UI", 9),
        )
        style.map(
            "Treeview",
            background=[("selected", pal["primary_soft"])],
            foreground=[("selected", pal["primary_text"])],
        )

        style.configure(
            "Treeview.Heading",
            background=pal["toolbar"],
            foreground=pal["text_secondary"],
            relief=tk.FLAT,
            borderwidth=0,
            padding=(6, max(4, int(round(4 * wt_dpi.get_scale())))),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        style.map(
            "Treeview.Heading",
            background=[("active", pal["border"])],
            foreground=[("active", pal["text"])],
        )

        # 运行报告专用 Treeview 样式
        style.configure("RunReport.Treeview", rowheight=row_height)
        style.configure("RunReport.Treeview.Heading", padding=(6, 8))

        # ── 下拉框 Combobox ──
        style.configure(
            "TCombobox",
            padding=max(4, int(round(4 * wt_dpi.get_scale()))),
            background=pal["surface"],
            fieldbackground=pal["surface"],
            foreground=pal["text"],
            bordercolor=pal["border"],
            arrowcolor=pal["muted"],
        )
        style.configure("Modern.TCombobox", padding=6)

        # ── 选项卡 Notebook ──
        style.configure(
            "TNotebook",
            background=pal["bg"],
            borderwidth=0,
            tabmargins=[2, 4, 2, 0],
        )
        style.configure(
            "TNotebook.Tab",
            background=pal["toolbar"],
            foreground=pal["muted"],
            padding=[14, 6],
            font=("Microsoft YaHei UI", 9),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", pal["surface"]), ("active", pal["border"])],
            foreground=[("selected", pal["primary"]), ("active", pal["text"])],
        )

        # ── 进度条 ──
        style.configure(
            "Horizontal.TProgressbar",
            background=pal["primary"],
            troughcolor=pal["border"],
            borderwidth=0,
            thickness=max(6, int(round(8 * wt_dpi.get_scale()))),
        )


_theme_manager = WTThemeManager()


def get_theme_manager():
    return _theme_manager


def get_palette():
    return _theme_manager.palette


def get_log_level_colors(dark=False):
    """返回日志级别配色映射（``dark=False`` 为浅底，``True`` 为深底）。

    供 wt_logging.level_colors() 与各日志 Text 控件的 tag_configure 使用，
    避免各处再硬编码色值导致同一系统出现多套配色。
    """
    return dict(LOG_LEVEL_COLORS["dark" if dark else "light"])


# ── 现代扁平 UI 控件构造辅助器 ────────────────────────────────────────────────

def create_flat_button(
    parent,
    text,
    command=None,
    tone="secondary",
    font=None,
    padx=10,
    pady=5,
    width=None,
    state=tk.NORMAL,
):
    """创建一个具备现代感、带有平滑 Hover 动效与阴影质感的扁平按钮。

    tone 可选:
      - 'primary': 主按钮（科技蓝底白字）
      - 'secondary': 次级按钮（白底黑字、浅灰细边框）
      - 'success': 成功/运行按钮（翡翠绿底白字）
      - 'danger': 危险/停止按钮（赤红底白字）
      - 'warning': 警告按钮（琥珀黄底白字）
      - 'subtle': 极简透明底（仅文字与弱悬停底）
    """
    pal = get_palette()
    fnt = font or ("Microsoft YaHei UI", 9)

    tones = {
        "primary": {
            "bg": pal["primary"],
            "fg": "#ffffff",
            "hover": pal["primary_hover"],
            "active": pal["primary_active"],
            "border": pal["primary"],
            "bd": 0,
        },
        "success": {
            "bg": pal["success"],
            "fg": "#ffffff",
            "hover": pal["success_hover"],
            "active": pal["success_active"],
            "border": pal["success"],
            "bd": 0,
        },
        "danger": {
            "bg": pal["danger"],
            "fg": "#ffffff",
            "hover": pal["danger_hover"],
            "active": pal["danger_active"],
            "border": pal["danger"],
            "bd": 0,
        },
        "warning": {
            "bg": pal["warning"],
            "fg": "#ffffff",
            "hover": pal["warning_hover"],
            "active": pal["warning_active"],
            "border": pal["warning"],
            "bd": 0,
        },
        "secondary": {
            "bg": pal["secondary"],
            "fg": pal["text"],
            "hover": pal["secondary_hover"],
            "active": pal["secondary_active"],
            "border": pal["border"],
            "bd": 1,
        },
        "subtle": {
            "bg": pal["bg"],
            "fg": pal["muted"],
            "hover": pal["toolbar"],
            "active": pal["border"],
            "border": pal["bg"],
            "bd": 0,
        },
    }

    t = tones.get(tone, tones["secondary"])
    kwargs = {
        "text": text,
        "command": command,
        "bg": t["bg"],
        "fg": t["fg"],
        "activebackground": t["active"],
        "activeforeground": t["fg"],
        "relief": tk.FLAT,
        "bd": 0,
        "cursor": "hand2" if state != tk.DISABLED else "arrow",
        "padx": padx,
        "pady": pady,
        "font": fnt,
        "state": state,
    }
    if width:
        kwargs["width"] = width

    btn = tk.Button(parent, **kwargs)

    if t["bd"] > 0:
        btn.configure(highlightthickness=1, highlightbackground=t["border"])

    def on_enter(_event):
        if btn["state"] != tk.DISABLED:
            btn.configure(bg=t["hover"])

    def on_leave(_event):
        if btn["state"] != tk.DISABLED:
            btn.configure(bg=t["bg"])

    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)

    def set_tone(new_tone):
        nt = tones.get(new_tone, tones["secondary"])
        t["bg"] = nt["bg"]
        t["fg"] = nt["fg"]
        t["hover"] = nt["hover"]
        t["active"] = nt["active"]
        t["border"] = nt["border"]
        btn.configure(
            bg=nt["bg"],
            fg=nt["fg"],
            activebackground=nt["active"],
            activeforeground=nt["fg"],
        )
        if nt["bd"] > 0:
            btn.configure(highlightthickness=1, highlightbackground=nt["border"])
        else:
            btn.configure(highlightthickness=0)

    btn.set_tone = set_tone
    return btn


def create_badge(parent, text, tone="info", font=None):
    """创建现代胶囊式状态徽标 (Pill Badge)。"""
    pal = get_palette()
    fnt = font or ("Microsoft YaHei UI", 8, "bold")

    tones = {
        "primary": {"bg": pal["primary_soft"], "fg": pal["primary_text"]},
        "success": {"bg": pal["success_soft"], "fg": pal["success_text"]},
        "warning": {"bg": pal["warning_soft"], "fg": pal["warning_text"]},
        "danger": {"bg": pal["danger_soft"], "fg": pal["danger_text"]},
        "info": {"bg": pal["info_soft"], "fg": pal["info_text"]},
        "muted": {"bg": pal["toolbar"], "fg": pal["muted"]},
    }
    t = tones.get(tone, tones["muted"])

    badge = tk.Label(
        parent,
        text=text,
        bg=t["bg"],
        fg=t["fg"],
        font=fnt,
        padx=8,
        pady=2,
    )

    def set_badge(new_text, new_tone):
        badge.configure(text=new_text)
        nt = tones.get(new_tone, tones["muted"])
        badge.configure(bg=nt["bg"], fg=nt["fg"])

    badge.set_badge = set_badge
    return badge


def create_card_frame(parent, padx=12, pady=12, border=True, **kwargs):
    """创建纯白圆角感现代卡片容器。"""
    pal = get_palette()
    frame = tk.Frame(
        parent,
        bg=pal["card"],
        padx=padx,
        pady=pady,
        highlightthickness=1 if border else 0,
        highlightbackground=pal["border"] if border else pal["card"],
        **kwargs
    )
    return frame


def create_modern_scrollbar(parent, orient=tk.VERTICAL, command=None):
    """创建现代扁平浅色滚动条。"""
    pal = get_palette()
    sb = tk.Scrollbar(
        parent,
        orient=orient,
        command=command,
        relief=tk.FLAT,
        width=12,
        bg=pal["toolbar"],
        activebackground=pal["border_dark"],
        troughcolor=pal["bg"],
        highlightthickness=0,
        bd=0,
    )
    return sb


def create_pill_selector(parent, options, variable, on_change=None):
    """创建现代分段选择器（Pill Segmented Control）。

    options 格式: [("显示标签", "对应值"), ...]
    """
    pal = get_palette()
    container = tk.Frame(
        parent,
        bg=pal["toolbar"],
        padx=2,
        pady=2,
        highlightthickness=1,
        highlightbackground=pal["border"],
    )

    buttons = {}

    def update_styles():
        curr = variable.get()
        for val, btn in buttons.items():
            if val == curr:
                btn.configure(
                    bg=pal["primary"],
                    fg="#ffffff",
                    font=("Microsoft YaHei UI", 9, "bold"),
                )
            else:
                btn.configure(
                    bg=pal["toolbar"],
                    fg=pal["muted"],
                    font=("Microsoft YaHei UI", 9),
                )

    def select(val):
        variable.set(val)
        update_styles()
        if on_change:
            on_change(val)

    for label, val in options:
        btn = tk.Button(
            container,
            text=label,
            relief=tk.FLAT,
            bd=0,
            padx=12,
            pady=4,
            cursor="hand2",
            command=lambda v=val: select(v),
        )
        btn.pack(side=tk.LEFT, padx=1)
        buttons[val] = btn

    update_styles()
    container.update_styles = update_styles
    return container
