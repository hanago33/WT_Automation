# encoding: utf-8
"""
txt_merge_tool.py —— 多个 txt 文档合并工具（现代化 UI 版本）

功能：
  - 选择多个 txt 文件 / 文件夹，按列表顺序合并成一个 txt 文档
  - 合并方式：新起一行再接 / 直接接在末尾
  - 可选：文件名作为章节标题、自定义分隔符、去重空行、按文件名排序
  - 输出编码可选：UTF-8 / GBK
  - 支持拖拽添加文件（优先 TkinterDnD，备用 pywin32 OLE 拖拽，均不可用时优雅降级）
  - 保持每个文件的原始内容与格式不变（含编码、换行符、空行等）
  - 现代化视觉：高 DPI 自动感知、Slate 调色板、圆角卡片、平滑 Hover 动效、空态占位引导
"""

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# 尝试引入工程基础设施模块（高 DPI 与统一设计系统），若缺失则自动降级内联实现
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

# 常见文本编码，按优先级尝试解码
_ENCODINGS = ["utf-8", "gbk", "gb18030", "utf-16", "big5", "latin-1"]

# 拖拽支持：优先用 tkinterdnd2，否则用 pywin32 的 OLE 拖拽
_HAS_DND = False
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    _HAS_DND = True
except Exception:  # noqa: BLE001
    pass

_HAS_OLE_DND = False
try:
    import pythoncom
    from win32com.shell import shell, shellcon
    _HAS_OLE_DND = True
except Exception:  # noqa: BLE001
    pass


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


# ── 业务核心逻辑（纯函数，保持 100% 格式与编码原样） ────────────────────────────

def read_text_file(path):
    """读取文本文件内容，自动探测编码，返回 (内容, 编码)。"""
    with open(path, "rb") as f:
        raw = f.read()
    # 优先尝试 utf-8-sig（兼容带 BOM 的 utf-8）
    for enc in ["utf-8-sig"] + _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    # 兜底：latin-1 永不失败，保证不丢字节
    return raw.decode("latin-1"), "latin-1"


def merge_txt_files(
    file_paths,
    output_path,
    newline_between=True,
    add_headers=False,
    separator="",
    remove_empty_lines=False,
    output_encoding="utf-8-sig",
):
    """按顺序合并多个 txt 文件到输出文件，保持内容原样。

    参数：
      newline_between    : True 时在每个文件之间插入一个换行（新起一行再接）
      add_headers        : True 时在每个文件内容前插入一行文件名作为章节标题
      separator          : 非空时在每个文件之间插入该自定义分隔行
      remove_empty_lines : True 时删除所有空行
      output_encoding    : 输出编码（utf-8-sig / gbk 等）
    """
    parts = []
    for i, path in enumerate(file_paths):
        content, _ = read_text_file(path)

        # 文件之间的衔接（换行 / 分隔符）
        if i > 0:
            if separator:
                parts.append(separator + "\n")
            elif newline_between:
                parts.append("\n")

        # 章节标题
        if add_headers:
            parts.append(os.path.basename(path) + "\n")

        parts.append(content)

    merged = "".join(parts)

    # 去重空行
    if remove_empty_lines:
        merged = "\n".join(line for line in merged.split("\n") if line.strip() != "")

    with open(output_path, "w", encoding=output_encoding, newline="") as f:
        f.write(merged)
    return len(file_paths), len(merged)


class OleDropTarget:
    """基于 pywin32 的 OLE 文件拖拽目标（IDropTarget 实现）。"""

    _com_interfaces_ = [pythoncom.IID_IDropTarget]
    _public_methods_ = ["DragEnter", "DragOver", "DragLeave", "Drop"]

    def __init__(self, hwnd, on_files):
        self._hwnd = hwnd
        self._on_files = on_files
        self._registered = False

    def register(self):
        try:
            pythoncom.RegisterDragDrop(self._hwnd, self)
            self._registered = True
            return True
        except Exception:  # noqa: BLE001
            return False

    def unregister(self):
        try:
            if self._registered:
                pythoncom.RevokeDragDrop(self._hwnd)
                self._registered = False
        except Exception:  # noqa: BLE001
            pass

    def DragEnter(self, pDataObj, grfKeyState, pt, pdwEffect):
        pdwEffect[0] = shellcon.DROPEFFECT_COPY
        return 0

    def DragOver(self, grfKeyState, pt, pdwEffect):
        pdwEffect[0] = shellcon.DROPEFFECT_COPY
        return 0

    def DragLeave(self):
        return 0

    def Drop(self, pDataObj, grfKeyState, pt, pdwEffect):
        pdwEffect[0] = shellcon.DROPEFFECT_COPY
        try:
            files = []
            try:
                data = pDataObj.GetData(shellcon.CF_HDROP)
                files = shell.DragQueryFile(data)
            except Exception:  # noqa: BLE001
                pass
            if files:
                self._on_files(files)
        except Exception:  # noqa: BLE001
            pass
        return 0


# ── 主界面应用（现代化交互与视觉重塑） ──────────────────────────────────────────

class TxtMergeApp:
    def __init__(self, root):
        self.root = root
        self.pal = _get_palette()
        self.files = []  # 待合并文件列表路径

        root.title("TXT 文本文件合并工具")
        root.configure(bg=self.pal["bg"])

        # 初始化 DPI 缩放与初始窗口几何
        if _HAS_WT_DPI and hasattr(wt_dpi, "geometry"):
            wt_dpi.geometry(root, 760, 680)
            root.minsize(_scale_val(660), _scale_val(560))
        else:
            root.geometry("760x680")
            root.minsize(660, 560)

        self._build_ui()
        self._init_drag_drop()
        self.refresh_list()

    def _build_ui(self):
        pal = self.pal
        pad_x = _scale_val(14)
        pad_y = _scale_val(10)

        # ── 1. 顶部 Header 卡片 ──
        top_card = _create_card_frame(self.root, padx=_scale_val(14), pady=_scale_val(10), border=False)
        top_card.pack(fill="x", padx=pad_x, pady=(pad_y, _scale_val(6)))

        top_left = tk.Frame(top_card, bg=pal["card"])
        top_left.pack(side="left", fill="both", expand=True)

        title_lbl = tk.Label(
            top_left,
            text="TXT 文本文件合并工具",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=pal["card"],
            fg=pal["text"],
            anchor="w",
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            top_left,
            text="按列表顺序合并多个文本文件，保留编码与排版。支持拖拽文件/文件夹。",
            font=("Microsoft YaHei UI", 9),
            bg=pal["card"],
            fg=pal["muted"],
            anchor="w",
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        # 顶部右侧：状态胶囊徽标
        self.badge_status = _create_badge(top_card, "就绪 · 0 个文件", tone="muted")
        self.badge_status.pack(side="right", anchor="center")

        # ── 2. 主体工作区（左侧列表卡片 + 右侧操作工具列） ──
        body_frame = tk.Frame(self.root, bg=pal["bg"])
        body_frame.pack(fill="both", expand=True, padx=pad_x, pady=0)

        # 2.1 左侧列表卡片容器
        list_card = _create_card_frame(body_frame, padx=_scale_val(12), pady=_scale_val(10))
        list_card.pack(side="left", fill="both", expand=True)

        list_header = tk.Frame(list_card, bg=pal["card"])
        list_header.pack(fill="x", pady=(0, _scale_val(6)))
        tk.Label(
            list_header,
            text="待合并文件列表",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=pal["card"],
            fg=pal["text"],
        ).pack(side="left")

        self.list_tip_var = tk.StringVar(value="提示：可上下拖动或排序微调合并先后")
        tk.Label(
            list_header,
            textvariable=self.list_tip_var,
            font=("Microsoft YaHei UI", 8),
            bg=pal["card"],
            fg=pal["muted"],
        ).pack(side="right")

        # 列表框与滚动条容器
        box_wrap = tk.Frame(list_card, bg=pal["card"])
        box_wrap.pack(fill="both", expand=True)

        self.scrollbar = tk.Scrollbar(
            box_wrap,
            orient="vertical",
            relief=tk.FLAT,
            width=_scale_val(12),
            bg=pal["toolbar"],
            activebackground=pal["border_dark"],
            troughcolor=pal["bg"],
            highlightthickness=0,
            bd=0,
        )
        self.scrollbar.pack(side="right", fill="y")

        self.listbox = tk.Listbox(
            box_wrap,
            selectmode="extended",
            activestyle="none",
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=pal["border"],
            highlightcolor=pal["primary"],
            bg="#ffffff",
            fg=pal["text"],
            selectbackground=pal["primary_soft"],
            selectforeground=pal["primary_text"],
            font=("Microsoft YaHei UI", 9),
            yscrollcommand=self.scrollbar.set,
        )
        self.listbox.pack(side="left", fill="both", expand=True)
        self.scrollbar.config(command=self.listbox.yview)

        # 空状态占位提示（无文件时浮于 Listbox 之上）
        self.empty_label = tk.Label(
            self.listbox,
            text="📄 拖拽 TXT 文件/文件夹到此处\n或点击右侧「添加文件」按钮",
            bg="#ffffff",
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 9),
            justify="center",
        )

        # 2.2 右侧操作工具列
        tool_col = tk.Frame(body_frame, bg=pal["bg"], width=_scale_val(130))
        tool_col.pack(side="right", fill="y", padx=(pad_x, 0))

        # 添加操作组
        _create_flat_button(tool_col, "＋ 添加文件", command=self.add_files, tone="primary", width=12).pack(fill="x", pady=(0, _scale_val(4)))
        _create_flat_button(tool_col, "📁 添加文件夹", command=self.add_folder, tone="secondary", width=12).pack(fill="x", pady=0)

        # 排序微调组
        sep1 = tk.Frame(tool_col, height=1, bg=pal["border"])
        sep1.pack(fill="x", pady=_scale_val(10))

        _create_flat_button(tool_col, "↑ 上移", command=lambda: self.move(-1), tone="secondary", width=12).pack(fill="x", pady=(0, _scale_val(4)))
        _create_flat_button(tool_col, "↓ 下移", command=lambda: self.move(1), tone="secondary", width=12).pack(fill="x", pady=(0, _scale_val(4)))
        _create_flat_button(tool_col, "↕ 智能排序", command=self.sort_by_name, tone="secondary", width=12).pack(fill="x", pady=0)

        # 清理操作组
        sep2 = tk.Frame(tool_col, height=1, bg=pal["border"])
        sep2.pack(fill="x", pady=_scale_val(10))

        _create_flat_button(tool_col, "✕ 移除选中", command=self.remove_selected, tone="subtle", width=12).pack(fill="x", pady=(0, _scale_val(4)))
        _create_flat_button(tool_col, "⌫ 清空列表", command=self.clear_list, tone="subtle", width=12).pack(fill="x", pady=0)

        # ── 3. 合并规则与输出选项卡片 ──
        opt_card = _create_card_frame(self.root, padx=_scale_val(14), pady=_scale_val(10))
        opt_card.pack(fill="x", padx=pad_x, pady=(_scale_val(8), _scale_val(6)))

        # 3.1 衔接方式
        row1 = tk.Frame(opt_card, bg=pal["card"])
        row1.pack(fill="x", pady=(0, _scale_val(6)))
        tk.Label(row1, text="衔接方式：", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"]).pack(side="left")
        self.join_var = tk.StringVar(value="newline")
        tk.Radiobutton(row1, text="新起一行再接 (推荐)", variable=self.join_var, value="newline", bg=pal["card"], fg=pal["text"], activebackground=pal["card"]).pack(side="left", padx=_scale_val(6))
        tk.Radiobutton(row1, text="直接接在末尾", variable=self.join_var, value="direct", bg=pal["card"], fg=pal["text"], activebackground=pal["card"]).pack(side="left", padx=_scale_val(6))

        # 自定义分隔符
        sep_box = tk.Frame(row1, bg=pal["card"])
        sep_box.pack(side="right")
        tk.Label(sep_box, text="分隔符行：", font=("Microsoft YaHei UI", 9), bg=pal["card"], fg=pal["text_secondary"]).pack(side="left")
        self.sep_var = tk.StringVar(value="")
        entry_sep = tk.Entry(sep_box, textvariable=self.sep_var, width=18, font=("Microsoft YaHei UI", 9), highlightthickness=1, highlightbackground=pal["border"], bd=0)
        entry_sep.pack(side="left", ipady=2)

        # 3.2 高级与编码选项
        row2 = tk.Frame(opt_card, bg=pal["card"])
        row2.pack(fill="x")
        tk.Label(row2, text="高级选项：", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"]).pack(side="left")

        self.header_var = tk.BooleanVar(value=False)
        tk.Checkbutton(row2, text="文件名作为章节标题", variable=self.header_var, bg=pal["card"], fg=pal["text"], activebackground=pal["card"]).pack(side="left", padx=_scale_val(6))

        self.empty_var = tk.BooleanVar(value=False)
        tk.Checkbutton(row2, text="去重空行", variable=self.empty_var, bg=pal["card"], fg=pal["text"], activebackground=pal["card"]).pack(side="left", padx=_scale_val(6))

        # 编码选择（靠右）
        enc_box = tk.Frame(row2, bg=pal["card"])
        enc_box.pack(side="right")
        tk.Label(enc_box, text="输出编码：", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"]).pack(side="left")
        self.enc_var = tk.StringVar(value="utf-8-sig")
        for enc, label in [("utf-8-sig", "UTF-8(带BOM)"), ("utf-8", "UTF-8"), ("gbk", "GBK")]:
            tk.Radiobutton(enc_box, text=label, variable=self.enc_var, value=enc, bg=pal["card"], fg=pal["text"], activebackground=pal["card"]).pack(side="left", padx=_scale_val(4))

        # ── 4. 底部执行与状态条 ──
        footer = tk.Frame(self.root, bg=pal["bg"])
        footer.pack(fill="x", padx=pad_x, pady=(0, pad_y))

        self.status = tk.Label(footer, text="就绪，等待添加文件...", anchor="w", bg=pal["bg"], fg=pal["muted"], font=("Microsoft YaHei UI", 9))
        self.status.pack(side="left", fill="x", expand=True)

        self.btn_merge = _create_flat_button(
            footer,
            text="★ 开始合并文件",
            command=self.merge,
            tone="success",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=_scale_val(18),
            pady=_scale_val(6),
        )
        self.btn_merge.pack(side="right")

    def _init_drag_drop(self):
        """初始化拖拽支持。"""
        self._ole_drop = None
        if _HAS_DND:
            try:
                self.listbox.drop_target_register(DND_FILES)
                self.listbox.dnd_bind("<<Drop>>", self.on_drop)
            except Exception:
                pass
        elif _HAS_OLE_DND:
            try:
                self._ole_drop = OleDropTarget(self.root.winfo_id(), self._append_paths)
                if not self._ole_drop.register():
                    self._ole_drop = None
            except Exception:
                pass

    # ---------- 文件操作 ----------
    def add_files(self):
        paths = filedialog.askopenfilenames(
            title="选择要合并的文本文件（支持 txt / wnd / log 等格式）",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
        )
        self._append_paths(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="选择包含文本文件的文件夹")
        if not folder:
            return
        # 扫描文件夹内的文本文件（.txt/.wnd/.log，不递归子目录）
        try:
            paths = [
                os.path.join(folder, name)
                for name in sorted(os.listdir(folder))
                if name.lower().endswith((".txt", ".wnd", ".log"))
            ]
        except OSError as exc:
            messagebox.showerror("读取文件夹失败", "{}\n\n{}".format(folder, exc))
            return
        if not paths:
            messagebox.showinfo("提示", "该文件夹内没有找到文本文件。")
            return
        self._append_paths(paths)

    def on_drop(self, event):
        raw = event.data
        try:
            paths = self.root.tk.splitlist(raw)
        except Exception:
            paths = [raw]
        self._append_paths(paths)

    def _append_paths(self, paths):
        added = 0

        def _clean(p):
            """清洗拖拽路径：仅剥去成对的 Tk 花括号包裹。

            tk.splitlist 已正确处理带空格路径；这里只需兜底「splitlist 失败时
            整串带 {…}」的情况。此前 str.strip("{}'\"") 会把名称首尾本身含
            {}'/\" 字符的合法目录/文件截断（如 Data{2024} → Data{2024）。
            """
            text = str(p or "").strip()
            if len(text) >= 2 and text.startswith("{") and text.endswith("}"):
                text = text[1:-1].strip()
            return text

        for p in paths:
            clean_p = _clean(p)
            if os.path.isfile(clean_p) and clean_p not in self.files:
                self.files.append(clean_p)
                added += 1
            elif os.path.isdir(clean_p):
                for name in sorted(os.listdir(clean_p)):
                    sub = os.path.join(clean_p, name)
                    if os.path.isfile(sub) and sub.lower().endswith((".txt", ".wnd", ".log")) and sub not in self.files:
                        self.files.append(sub)
                        added += 1
        self.refresh_list()
        if added > 0:
            self.status.config(text=f"已成功追加 {added} 个文本文件")

    def remove_selected(self):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        for idx in reversed(sel):
            del self.files[idx]
        self.refresh_list()
        self.status.config(text=f"已移除所选文件，剩余 {len(self.files)} 个")

    def clear_list(self):
        if not self.files:
            return
        if not messagebox.askyesno(
            "确认清空", "确定清空列表中的 {} 个文件吗？".format(len(self.files))
        ):
            return
        self.files = []
        self.refresh_list()
        self.status.config(text="列表已清空")

    def move(self, direction):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        idx = sel[0]
        new_idx = idx + direction
        if new_idx < 0 or new_idx >= len(self.files):
            return
        self.files[idx], self.files[new_idx] = self.files[new_idx], self.files[idx]
        self.refresh_list()
        self.listbox.selection_set(new_idx)
        self.listbox.see(new_idx)

    def sort_by_name(self):
        if not self.files:
            return
        self.files.sort(key=lambda p: os.path.basename(p).lower())
        self.refresh_list()
        self.status.config(text="已按文件名 A-Z 重新排序")

    def refresh_list(self):
        self.listbox.delete(0, tk.END)
        total_size = 0
        for p in self.files:
            try:
                sz = os.path.getsize(p)
                total_size += sz
                sz_str = f"{sz / 1024:.1f} KB" if sz < 1024 * 1024 else f"{sz / (1024 * 1024):.2f} MB"
            except Exception:
                sz_str = ""
            name = os.path.basename(p)
            self.listbox.insert(tk.END, f"{name}    ({sz_str})" if sz_str else name)

        count = len(self.files)
        # 空状态占位提示显隐
        if count == 0:
            self.empty_label.place(relx=0.5, rely=0.5, anchor="center")
            self.badge_status.set_badge("就绪 · 0 个文件", "muted")
            self.status.config(text="就绪，等待添加文件...")
        else:
            self.empty_label.place_forget()
            size_mb = total_size / (1024 * 1024)
            size_summary = f"{size_mb:.2f} MB" if size_mb >= 0.1 else f"{total_size / 1024:.1f} KB"
            self.badge_status.set_badge(f"已就绪 · {count} 个文件 ({size_summary})", "success")
            self.status.config(text=f"已载入 {count} 个文本文件，合计 {size_summary}")

    # ---------- 合并 ----------
    def merge(self):
        if not self.files:
            messagebox.showwarning("提示", "请先添加要合并的文本文件。")
            return
        output = filedialog.asksaveasfilename(
            title="保存合并后的文件",
            defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")],
            initialfile="merged.txt",
        )
        if not output:
            return

        self.btn_merge.configure(state=tk.DISABLED)
        self.status.config(text="正在读取并合并文件，请稍候...")
        self.root.update_idletasks()

        try:
            count, total_chars = merge_txt_files(
                self.files,
                output,
                newline_between=self.join_var.get() == "newline",
                add_headers=self.header_var.get(),
                separator=self.sep_var.get().strip(),
                remove_empty_lines=self.empty_var.get(),
                output_encoding=self.enc_var.get(),
            )
            out_sz = os.path.getsize(output) if os.path.exists(output) else 0
            sz_str = f"{out_sz / 1024:.1f} KB" if out_sz < 1024 * 1024 else f"{out_sz / (1024 * 1024):.2f} MB"
            self.status.config(text=f"合并完成！已生成 {os.path.basename(output)} ({sz_str})")
            messagebox.showinfo(
                "合并成功",
                f"成功合并 {count} 个文本文件！\n\n"
                f"• 输出路径: {output}\n"
                f"• 总字符数: {total_chars:,} 字符\n"
                f"• 文件大小: {sz_str}",
            )
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("合并失败", f"合并过程中发生错误：\n{e}")
            self.status.config(text=f"合并失败: {e}")
        finally:
            self.btn_merge.configure(state=tk.NORMAL)


def main():
    if _HAS_WT_DPI and hasattr(wt_dpi, "enable_process_dpi_awareness"):
        try:
            wt_dpi.enable_process_dpi_awareness()
        except Exception:
            pass

    if _HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    if _HAS_WT_DPI and hasattr(wt_dpi, "compute_scale"):
        try:
            wt_dpi.compute_scale(root)
        except Exception:
            pass

    # OLE 拖拽的 RegisterDragDrop 依赖 COM 已在 UI 线程初始化（STA）——
    # 必须在构建 App 之前 CoInitialize，否则注册静默失败、拖拽不工作（审计 P2）
    ole_ready = False
    if _HAS_OLE_DND:
        try:
            pythoncom.CoInitialize()
            ole_ready = True
        except Exception:  # noqa: BLE001
            ole_ready = False
    app = TxtMergeApp(root)
    try:
        root.mainloop()
    finally:
        if ole_ready:
            drop = getattr(app, "_ole_drop", None)
            if drop is not None:
                try:
                    drop.unregister()
                except Exception:  # noqa: BLE001
                    pass
            pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
