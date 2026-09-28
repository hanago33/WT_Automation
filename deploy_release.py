#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
内网机一键部署（解压 + 应用）增强版。融合原 deploy_release 与发布包批量更新能力。

用法：
  1) 双击运行：图形界面（有 tkinter 时）；否则命令行交互输入。
  2) 命令行（保持原兼容）：
       deploy_release.exe <发布包.zip> [内网仓库根目录]
       deploy_release.exe --dry-run            # 只预览不执行
       deploy_release.exe --latest [根目录]    # 只覆盖最新一个包
       deploy_release.exe --all [根目录]       # 从旧到新依次覆盖全部包（默认）
    找不到 zip 时，自动在脚本目录/当前目录/发布包文件夹寻找 发布包_*.zip。
记忆：目标根目录、发布包文件夹、覆盖模式 自动保存（~/.wt_deploy_release.json），下次打开默认填入。
备份：覆盖前把将被覆盖的旧文件备份到 <目标>\.update_backup\<时间戳>\，可 --no-backup 关闭。
"""
import argparse
import datetime
import glob
import json
import os
import shutil
import sys
import tempfile
import zipfile

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    _HAS_TK = True
except Exception:
    _HAS_TK = False

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


CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".wt_deploy_release.json")
BACKUP_DIR_NAME = ".update_backup"

# 与旧版保持一致的跳过列表（工具自身/清单文件不覆盖）。
SKIP = {"清单.txt", "deleted.txt", "apply_release.py", "apply_release.exe",
        "make_release.py", "make_release.exe", "deploy_release.py"}


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


# --------------------------------------------------------------------------
# 核心逻辑（可脱离 GUI / exe 复用与测试）
# --------------------------------------------------------------------------
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


def list_release_zips(release_dir):
    """返回按修改时间倒序（新->旧）的 zip 列表。"""
    items = []
    if not release_dir or not os.path.isdir(release_dir):
        return items
    for name in os.listdir(release_dir):
        if name.lower().endswith(".zip"):
            path = os.path.join(release_dir, name)
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                mtime = 0
            items.append({"path": path, "name": name, "mtime": mtime})
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def find_zip(hint=None, release_dir=None):
    """兼容旧版：优先 hint 指定的 zip；否则在脚本目录/当前目录/发布包文件夹找最新的 发布包_*.zip。"""
    if hint and os.path.isfile(hint):
        return hint
    cands = []
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, os.getcwd(), release_dir or ""):
        if base and os.path.isdir(base):
            cands.extend(glob.glob(os.path.join(base, "发布包_*.zip")))
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def _safe_join(root, rel):
    root = os.path.abspath(root)
    target = os.path.abspath(os.path.join(root, rel))
    if not target.startswith(root + os.sep) and target != root:
        raise RuntimeError("发布包路径越界: %s" % rel)
    return target


def _zip_file_list(zip_path):
    with zipfile.ZipFile(zip_path, "r") as z:
        return [n for n in z.namelist() if not n.endswith("/")]


def apply_zip(zip_path, target, backup_dir=None, log=print):
    """解压覆盖单个 zip 到目标目录（跳过 SKIP 文件）；覆盖前可选备份。返回覆盖的文件数。

    先整体解压到临时目录，再逐文件复制（跳过 SKIP），确保工具自身/清单文件不被覆盖。
    """
    tmp = tempfile.mkdtemp(prefix="wt_deploy_")
    effective = []
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmp)
        for root, _dirs, files in os.walk(tmp):
            for fn in files:
                if fn in SKIP:
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, tmp)
                dst = _safe_join(target, rel)
                if os.path.exists(dst) and backup_dir:
                    bak = os.path.join(backup_dir, rel)
                    os.makedirs(os.path.dirname(bak), exist_ok=True)
                    shutil.copy2(dst, bak)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(full, dst)
                effective.append(rel)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    skipped = len(_zip_file_list(zip_path)) - len(effective)
    log("  [OK] %s -> %d 个文件已覆盖（跳过 %d 个工具自身文件）"
        % (os.path.basename(zip_path), len(effective), skipped))
    # 处理删除清单
    _apply_deleted_list(zip_path, target)
    return len(effective)


def _apply_deleted_list(zip_path, target):
    try:
        with zipfile.ZipFile(zip_path, "r") as z:
            data = z.read("deleted.txt").decode("utf-8", errors="replace")
    except Exception:
        return 0
    removed = 0
    for line in data.splitlines():
        rel = line.strip()
        if not rel:
            continue
        p = _safe_join(target, rel)
        if os.path.isfile(p):
            try:
                os.remove(p)
                removed += 1
            except Exception:
                pass
    if removed:
        print("  [del] 按删除清单删除 %d 个文件" % removed)


def plan_apply(zips, target, mode="all", dry_run=False, backup=True, log=print):
    """按模式执行或预览覆盖。

    mode: "latest" 只覆盖最新一个；"all" 从旧到新依次覆盖（默认）。
    返回 (成功包数, 覆盖文件总数, backup_dir)。
    """
    if not zips:
        log("没有找到发布包 zip。")
        return (0, 0, None)
    ordered = sorted(zips, key=lambda x: x["mtime"]) if mode == "all" else [zips[0]]
    backup_dir = None
    if backup and not dry_run:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(target, BACKUP_DIR_NAME, ts)
        os.makedirs(backup_dir, exist_ok=True)
        log("备份目录: %s" % backup_dir)
    total = 0
    for item in ordered:
        dt = datetime.datetime.fromtimestamp(item["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
        log("== %s (%s)" % (item["name"], dt))
        if dry_run:
            names = _zip_file_list(item["path"])
            log("  [预览] 将覆盖 %d 个文件（跳过 %d 个工具自身文件）"
                % (len([n for n in names if os.path.basename(n) not in SKIP]),
                   len([n for n in names if os.path.basename(n) in SKIP])))
            total += len([n for n in names if os.path.basename(n) not in SKIP])
        else:
            total += apply_zip(item["path"], target, backup_dir, log=log)
    return (len(ordered), total, backup_dir)


# --------------------------------------------------------------------------
# 图形界面（现代化交互与视觉重塑）
# --------------------------------------------------------------------------
class DeployApp(object):
    def __init__(self, root):
        self.root = root
        self.pal = _get_palette()
        self.cfg = load_config()

        root.title("WT Automation - 发布包一键部署控制台")
        root.configure(bg=self.pal["bg"])

        # 初始化 DPI 缩放与初始窗口几何
        if _HAS_WT_DPI and hasattr(wt_dpi, "enable_process_dpi_awareness"):
            try:
                wt_dpi.enable_process_dpi_awareness()
                wt_dpi.compute_scale(root)
            except Exception:
                pass

        if _HAS_WT_DPI and hasattr(wt_dpi, "geometry"):
            wt_dpi.geometry(root, 920, 720)
            root.minsize(_scale_val(800), _scale_val(620))
        else:
            root.geometry("920x720")
            root.minsize(800, 620)

        self._build_ui()
        self._load_cfg_into_ui()

    def _build_ui(self):
        pal = self.pal
        pad_x = _scale_val(14)
        pad_y = _scale_val(8)

        # ── 1. 顶部 Header 引导卡片 ──
        top_card = _create_card_frame(self.root, padx=_scale_val(14), pady=_scale_val(10), border=False)
        top_card.pack(fill="x", padx=pad_x, pady=(pad_y, _scale_val(4)))

        top_left = tk.Frame(top_card, bg=pal["card"])
        top_left.pack(side="left", fill="both", expand=True)

        title_lbl = tk.Label(
            top_left,
            text="WT Automation - 发布包一键部署控制台",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=pal["card"],
            fg=pal["text"],
            anchor="w",
        )
        title_lbl.pack(anchor="w")

        sub_lbl = tk.Label(
            top_left,
            text="解压覆盖与发布包批量部署，支持安全自动备份、差异预览与按序全量覆盖。",
            font=("Microsoft YaHei UI", 9),
            bg=pal["card"],
            fg=pal["muted"],
            anchor="w",
        )
        sub_lbl.pack(anchor="w", pady=(2, 0))

        self.badge_status = _create_badge(top_card, "部署环境就绪", tone="info")
        self.badge_status.pack(side="right", anchor="center")

        # ── 2. 仓库与发布源配置卡片 ──
        cfg_card = _create_card_frame(self.root, padx=_scale_val(14), pady=_scale_val(10))
        cfg_card.pack(fill="x", padx=pad_x, pady=(0, _scale_val(6)))

        # 仓库根目录行
        r1 = tk.Frame(cfg_card, bg=pal["card"])
        r1.pack(fill="x", pady=(0, _scale_val(6)))
        tk.Label(r1, text="内网仓库根目录：", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"], width=15, anchor="w").pack(side="left")
        self.var_root = tk.StringVar()
        e_root = tk.Entry(r1, textvariable=self.var_root, font=("Microsoft YaHei UI", 9), highlightthickness=1, highlightbackground=pal["border"], bd=0)
        e_root.pack(side="left", fill="x", expand=True, padx=(0, _scale_val(8)), ipady=3)
        _create_flat_button(r1, "浏览…", command=self._browse_root, tone="secondary", width=8).pack(side="left")

        # 发布包存档路径行
        r2 = tk.Frame(cfg_card, bg=pal["card"])
        r2.pack(fill="x")
        tk.Label(r2, text="发布包文件夹：", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"], width=15, anchor="w").pack(side="left")
        self.var_release = tk.StringVar()
        e_rel = tk.Entry(r2, textvariable=self.var_release, font=("Microsoft YaHei UI", 9), highlightthickness=1, highlightbackground=pal["border"], bd=0)
        e_rel.pack(side="left", fill="x", expand=True, padx=(0, _scale_val(8)), ipady=3)
        _create_flat_button(r2, "浏览…", command=self._browse_release, tone="secondary", width=8).pack(side="left")

        # ── 3. 部署模式与安全配置卡片 ──
        mode_card = _create_card_frame(self.root, padx=_scale_val(14), pady=_scale_val(8))
        mode_card.pack(fill="x", padx=pad_x, pady=(0, _scale_val(6)))

        r_mode = tk.Frame(mode_card, bg=pal["card"])
        r_mode.pack(fill="x")
        tk.Label(r_mode, text="覆盖策略：", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"]).pack(side="left")
        self.var_mode = tk.StringVar(value="all")
        tk.Radiobutton(r_mode, text="从旧到新依次覆盖全部增量包 (推荐)", value="all", variable=self.var_mode, bg=pal["card"], fg=pal["text"], activebackground=pal["card"]).pack(side="left", padx=_scale_val(4))
        tk.Radiobutton(r_mode, text="仅覆盖选中的最新增量包", value="latest", variable=self.var_mode, bg=pal["card"], fg=pal["text"], activebackground=pal["card"]).pack(side="left", padx=_scale_val(8))

        # 安全备份
        self.var_backup = tk.BooleanVar(value=True)
        tk.Checkbutton(r_mode, text="覆盖前自动备份受影响文件至 .update_backup 目录", variable=self.var_backup, bg=pal["card"], fg=pal["text"], activebackground=pal["card"]).pack(side="right")

        # ── 4. 待处理发布包列表卡片 ──
        list_card = _create_card_frame(self.root, padx=_scale_val(14), pady=_scale_val(8))
        list_card.pack(fill="x", padx=pad_x, pady=(0, _scale_val(6)))

        lh = tk.Frame(list_card, bg=pal["card"])
        lh.pack(fill="x", pady=(0, _scale_val(4)))
        tk.Label(lh, text="待处理发布包列表（按时间倒序排列）", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"]).pack(side="left")
        _create_flat_button(lh, "↻ 刷新列表", command=self._refresh_list, tone="subtle", font=("Microsoft YaHei UI", 8)).pack(side="right")

        lb_wrap = tk.Frame(list_card, bg=pal["card"])
        lb_wrap.pack(fill="x")

        sb_list = tk.Scrollbar(lb_wrap, orient="vertical", relief=tk.FLAT, bd=0, bg=pal["toolbar"], troughcolor=pal["bg"])
        sb_list.pack(side="right", fill="y")

        self.listbox = tk.Listbox(
            lb_wrap,
            selectmode="extended",
            height=5,
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
            font=("Consolas", 9),
            yscrollcommand=sb_list.set,
        )
        self.listbox.pack(side="left", fill="x", expand=True)
        sb_list.config(command=self.listbox.yview)

        # ── 5. 部署终端日志卡片 ──
        log_card = _create_card_frame(self.root, padx=_scale_val(12), pady=_scale_val(8))
        log_card.pack(fill="both", expand=True, padx=pad_x, pady=(0, _scale_val(6)))

        log_head = tk.Frame(log_card, bg=pal["card"])
        log_head.pack(fill="x", pady=(0, 4))
        tk.Label(log_head, text="部署终端日志", font=("Microsoft YaHei UI", 9, "bold"), bg=pal["card"], fg=pal["text"]).pack(side="left")
        _create_flat_button(log_head, "清空日志", command=self._clear_log, tone="subtle", font=("Microsoft YaHei UI", 8)).pack(side="right")

        log_wrap = tk.Frame(log_card, bg=pal["card"])
        log_wrap.pack(fill="both", expand=True)

        sb_log = tk.Scrollbar(log_wrap, orient="vertical", relief=tk.FLAT, bd=0, bg=pal["toolbar"], troughcolor=pal["bg"])
        sb_log.pack(side="right", fill="y")

        self.log = tk.Text(
            log_wrap,
            height=10,
            state="disabled",
            wrap="word",
            font=("Consolas", 9),
            relief=tk.FLAT,
            bd=0,
            highlightthickness=1,
            highlightbackground=pal["border"],
            bg="#ffffff",
            fg=pal["text"],
            yscrollcommand=sb_log.set,
        )
        self.log.pack(side="left", fill="both", expand=True)
        sb_log.config(command=self.log.yview)

        self.log.tag_configure("err", foreground=pal["danger"])
        self.log.tag_configure("ok", foreground=pal["success"])
        self.log.tag_configure("warn", foreground=pal["warning"])
        self.log.tag_configure("info", foreground=pal["text"])

        # ── 6. 底部操作条 ──
        footer = tk.Frame(self.root, bg=pal["bg"])
        footer.pack(fill="x", padx=pad_x, pady=(0, pad_y))

        self.status = tk.Label(footer, text="路径与模式已自动保存至 ~/.wt_deploy_release.json", anchor="w", bg=pal["bg"], fg=pal["muted"], font=("Microsoft YaHei UI", 8))
        self.status.pack(side="left", fill="x", expand=True)

        self.btn_preview = _create_flat_button(
            footer,
            text="🔍 预览覆盖差异",
            command=self._preview,
            tone="secondary",
            padx=_scale_val(14),
            pady=_scale_val(6),
        )
        self.btn_preview.pack(side="left", padx=(0, _scale_val(8)))

        self.btn_apply = _create_flat_button(
            footer,
            text="★ 执行升级部署",
            command=self._apply,
            tone="primary",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=_scale_val(20),
            pady=_scale_val(6),
        )
        self.btn_apply.pack(side="right")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _log(self, msg, tag=None):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        if tag:
            self.log.tag_add(tag, "end-%dc" % (len(msg) + 1), "end-1c")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _load_cfg_into_ui(self):
        if self.cfg.get("project_root"):
            self.var_root.set(self.cfg["project_root"])
        if self.cfg.get("release_dir"):
            self.var_release.set(self.cfg["release_dir"])
        else:
            root_path = self.var_root.get()
            if root_path:
                self.var_release.set(os.path.join(root_path, "release_out"))
        if self.cfg.get("mode"):
            self.var_mode.set(self.cfg["mode"])
        self._refresh_list()

    def _save_cfg(self):
        self.cfg["project_root"] = self.var_root.get().strip()
        self.cfg["release_dir"] = self.var_release.get().strip()
        self.cfg["mode"] = self.var_mode.get()
        save_config(self.cfg)

    def _browse_root(self):
        path = filedialog.askdirectory(title="选择内网仓库根目录", initialdir=self.var_root.get() or os.getcwd())
        if path:
            self.var_root.set(path)
            if not self.var_release.get():
                self.var_release.set(os.path.join(path, "release_out"))
            self._save_cfg()
            self._refresh_list()

    def _browse_release(self):
        path = filedialog.askdirectory(title="选择发布包文件夹（存放 zip）", initialdir=self.var_release.get() or os.getcwd())
        if path:
            self.var_release.set(path)
            self._save_cfg()
            self._refresh_list()

    def _refresh_list(self):
        self.listbox.delete(0, "end")
        self._zips = list_release_zips(self.var_release.get().strip())
        if not self._zips:
            here = os.path.dirname(os.path.abspath(__file__))
            self._zips = list_release_zips(here)
        for i, item in enumerate(self._zips):
            dt = datetime.datetime.fromtimestamp(item["mtime"]).strftime("%m-%d %H:%M")
            tag_latest = "★ [最新] " if i == 0 else "   "
            self.listbox.insert("end", "%s[%s]  %s" % (tag_latest, dt, item["name"]))
        if self._zips:
            self.listbox.selection_set(0)
            self.listbox.activate(0)
            self.badge_status.set_badge("发现 %d 个发布包" % len(self._zips), "info")
        else:
            self.badge_status.set_badge("未找到发布包", "muted")

    def _selected_zips(self):
        sel = self.listbox.curselection()
        if not sel and self._zips:
            sel = (0,)
        return [self._zips[i] for i in sel]

    def _validate(self):
        root_path = self.var_root.get().strip()
        release_dir = self.var_release.get().strip()
        if not root_path or not os.path.isdir(root_path):
            messagebox.showerror("错误", "内网仓库根目录不存在或未选择。")
            return None, None
        if not release_dir or not os.path.isdir(release_dir):
            messagebox.showerror("错误", "发布包文件夹不存在或未选择。")
            return None, None
        return root_path, release_dir

    def _preview(self):
        root_path, _ = self._validate()
        if not root_path:
            return
        self._save_cfg()
        zips = self._selected_zips() if self.var_mode.get() == "latest" else self._zips
        self._clear_log()
        self.btn_preview.configure(state=tk.DISABLED)
        self.btn_apply.configure(state=tk.DISABLED)
        try:
            plan_apply(zips, root_path, mode=self.var_mode.get(), dry_run=True, log=lambda m: self._log(m))
            self._log("--- 预览结束 ---", "ok")
        finally:
            self.btn_preview.configure(state=tk.NORMAL)
            self.btn_apply.configure(state=tk.NORMAL)

    def _apply(self):
        root_path, _ = self._validate()
        if not root_path:
            return
        self._save_cfg()
        zips = self._selected_zips() if self.var_mode.get() == "latest" else self._zips
        self._clear_log()

        if not messagebox.askyesno(
            "确认部署覆盖",
            f"将覆盖 {len(zips)} 个发布包到仓库根目录：\n{root_path}\n\n"
            f"安全备份：{'已启用（自动留存至 .update_backup）' if self.var_backup.get() else '已禁用'}\n\n"
            f"是否继续执行？",
        ):
            return

        self.btn_preview.configure(state=tk.DISABLED)
        self.btn_apply.configure(state=tk.DISABLED)
        self.badge_status.set_badge("正在部署覆盖...", "warning")
        self.root.update_idletasks()

        try:
            n_ok, n_files, backup_dir = plan_apply(
                zips, root_path, mode=self.var_mode.get(),
                dry_run=False, backup=self.var_backup.get(), log=lambda m: self._log(m)
            )
            self._log("--- 覆盖完成 ---", "ok")
            self.badge_status.set_badge("部署完成", "success")
            messagebox.showinfo(
                "部署成功",
                f"发布包覆盖完成！\n\n"
                f"• 成功应用: {n_ok} 个包\n"
                f"• 覆盖文件: {n_files} 个\n"
                f"• 备份位置: {backup_dir or '未备份'}\n\n"
                f"若修改了服务端核心脚本，请重启后台队列服务使其生效。"
            )
        except Exception as exc:
            self._log("执行失败: %s" % exc, "err")
            self.badge_status.set_badge("部署失败", "danger")
            messagebox.showerror("失败", f"覆盖失败:\n{exc}")
        finally:
            self.btn_preview.configure(state=tk.NORMAL)
            self.btn_apply.configure(state=tk.NORMAL)


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------
def main_cli(argv):
    parser = argparse.ArgumentParser(description="内网机一键部署（解压 + 应用）")
    parser.add_argument("zip_hint", nargs="?", help="发布包 zip 路径（省略则自动找最新）")
    parser.add_argument("target", nargs="?", help="内网仓库根目录（省略则用记忆值）")
    parser.add_argument("--latest", action="store_true", help="只覆盖最新一个包（默认从旧到新全部）")
    parser.add_argument("--all", action="store_true", help="从旧到新依次覆盖全部包（默认）")
    parser.add_argument("--dry-run", action="store_true", help="只预览不执行")
    parser.add_argument("--no-backup", action="store_true", help="覆盖前不备份")
    args = parser.parse_args(argv)

    cfg = load_config()
    target = args.target or cfg.get("project_root")
    if not target or not os.path.isdir(target):
        print("错误：未指定有效的内网仓库根目录。")
        print("用法: deploy_release.exe <发布包.zip> <内网仓库根目录>   （或直接双击运行图形界面）")
        return 2

    release_dir = cfg.get("release_dir") or os.path.join(target, "release_out")
    zip_path = find_zip(args.zip_hint, release_dir=release_dir)
    if not zip_path or not os.path.isfile(zip_path):
        print("错误：找不到发布包 zip 文件。")
        print("用法: deploy_release.exe <发布包.zip> <内网仓库根目录>   （或直接双击运行图形界面）")
        return 2

    cfg["project_root"] = target
    cfg["release_dir"] = release_dir
    cfg["mode"] = "latest" if args.latest else "all"
    save_config(cfg)

    if os.path.dirname(zip_path) != release_dir and zip_path.endswith(".zip"):
        release_dir = os.path.dirname(zip_path)
    zips = [{"path": zip_path, "name": os.path.basename(zip_path),
             "mtime": os.path.getmtime(zip_path)}] if args.zip_hint else list_release_zips(release_dir)
    mode = "latest" if args.latest else "all"
    n_ok, n_files, backup_dir = plan_apply(zips, target, mode=mode,
                                           dry_run=args.dry_run, backup=not args.no_backup, log=print)
    if args.dry_run:
        print("\n(预览模式。去掉 --dry-run 执行覆盖)")
    else:
        print("\n部署完成：成功 %d 个包，共 %d 个文件" % (n_ok, n_files))
        print("  来源：%s" % os.path.basename(zip_path))
        print("  目标：%s" % target)
        if backup_dir:
            print("  备份目录：%s" % backup_dir)
    return 0


def main():
    if _HAS_TK and len(sys.argv) == 1:
        try:
            root = tk.Tk()
            DeployApp(root)
            root.mainloop()
            return 0
        except Exception as exc:
            print("图形界面启动失败（%s），切换命令行模式。" % exc)
    if len(sys.argv) == 1:
        print("[提示] 未检测到图形界面（Tkinter 不可用），进入命令行模式。")
    return main_cli(sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())