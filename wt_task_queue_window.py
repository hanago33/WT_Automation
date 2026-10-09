# encoding: utf-8

"""任务队列 + 服务器监控统一客户端窗口，供 WT_Launcher 使用。"""

import hashlib
import http.client
import io
import json
import os
import threading
import time
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
import uuid
from tkinter import filedialog, messagebox, ttk

import wt_theme
import wt_logging
import wt_queue_selfcheck
try:
    import wt_project_workdir_parser
except ImportError:
    wt_project_workdir_parser = None


DEFAULT_URL = "http://127.0.0.1:8768"
DEFAULT_MONITOR_URL = "http://127.0.0.1:8767"
POLL_MS = 2000

STATUS_LABELS = {
    "pending": "排队中",
    "running": "运行中",
    "paused": "已暂停",
    "success": "成功",
    "failed": "失败",
    "canceled": "已取消",
    "terminated": "已终止",
}


def build_status_filter_options():
    """状态筛选下拉的选项：由 STATUS_LABELS 派生，避免与状态模型不同步。

    原先是在 _build_ui 里手工列举 6 项，漏了「已取消 / 已终止」——
    这两种状态的任务只能靠「全部」查看，无法筛选。
    """
    return [("全部", "全部")] + [(label, label) for label in STATUS_LABELS.values()]

MONITOR_STATUS_LABELS = {
    "idle": "空闲",
    "running": "运行中",
    "success": "成功",
    "failed": "失败",
    "unknown": "未知",
}


def _derive_monitor_url(task_url):
    """根据任务服务地址自动联动推导监控服务地址（将 8768 端口转换为 8767）。"""
    url = str(task_url or "").strip().rstrip("/")
    if not url:
        return DEFAULT_MONITOR_URL
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    try:
        parsed = urllib.parse.urlsplit(url)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port
        if port == 8768 or port is None:
            new_netloc = "{}:8767".format(host)
        else:
            new_netloc = "{}:8767".format(host)
        return "{}://{}".format(scheme, new_netloc)
    except Exception:
        return DEFAULT_MONITOR_URL


def _local_ipv4s():
    """本机全部 IPv4（含 127.0.0.1），用于判定任务服务地址指向哪台机器。"""
    ips = set(wt_queue_selfcheck.get_lan_ip_list())
    ips.add("127.0.0.1")
    ips.add("localhost")
    return ips



def filter_tasks(tasks, status="", keyword=""):
    status = (status or "").strip()
    keyword = (keyword or "").strip().lower()
    filtered = []
    for task in tasks:
        raw_status = task.get("status", "")
        label = STATUS_LABELS.get(raw_status, raw_status)
        if status and status != "全部" and label != status and raw_status != status:
            continue
        if keyword:
            haystack = " ".join(
                str(task.get(key, "")) for key in (
                    "taskId", "user", "flowPath", "currentStepName", "lastLog"
                )
            ).lower()
            if keyword not in haystack:
                continue
        filtered.append(task)
    return filtered


def open_network_diag_dialog(
    parent,
    default_target=None,
    default_token="wt2026",
    history_urls=None,
    on_apply=None,
):
    """打开内网服务与网络连通性一键交互式诊断窗口。"""
    pal = wt_theme.get_palette()
    diag_win = tk.Toplevel(parent)
    diag_win.title("内网服务与网络连通性一键自检与诊断")
    diag_win.geometry("720x540")
    diag_win.minsize(600, 420)
    diag_win.configure(bg=pal["bg"])

    top_bar = tk.Frame(
        diag_win,
        bg=pal["surface"],
        padx=12,
        pady=10,
        highlightthickness=1,
        highlightbackground=pal["border"],
    )
    top_bar.pack(fill=tk.X, padx=10, pady=(10, 6))

    tk.Label(
        top_bar,
        text="目标服务器 IP / 地址:",
        bg=pal["surface"],
        fg=pal["text"],
        font=("Microsoft YaHei UI", 9, "bold"),
    ).pack(side=tk.LEFT)
    target_var = tk.StringVar(value=default_target or "http://127.0.0.1:8768")
    target_combo = ttk.Combobox(
        top_bar,
        textvariable=target_var,
        values=history_urls or ["http://127.0.0.1:8768"],
        width=24,
        font=("Microsoft YaHei UI", 9),
    )
    target_combo.pack(side=tk.LEFT, padx=(6, 12))

    tk.Label(
        top_bar,
        text="服务令牌:",
        bg=pal["surface"],
        fg=pal["text"],
        font=("Microsoft YaHei UI", 9),
    ).pack(side=tk.LEFT)
    diag_token_var = tk.StringVar(value=default_token or "wt2026")
    tk.Entry(
        top_bar,
        textvariable=diag_token_var,
        width=12,
        bg=pal["card_hover"],
        relief=tk.FLAT,
        highlightthickness=1,
        highlightbackground=pal["border"],
        font=("Microsoft YaHei UI", 9),
    ).pack(side=tk.LEFT, padx=(4, 12))

    term_frame = tk.Frame(
        diag_win,
        bg="#0f172a",
        padx=8,
        pady=8,
        highlightthickness=1,
        highlightbackground=pal["border"],
    )
    term_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=6)

    diag_text = tk.Text(
        term_frame,
        bg="#0f172a",
        fg="#f8fafc",
        insertbackground="#f8fafc",
        font=("Consolas", 10),
        relief=tk.FLAT,
        wrap=tk.WORD,
    )
    scroll = tk.Scrollbar(term_frame, command=diag_text.yview)
    diag_text.config(yscrollcommand=scroll.set)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)
    diag_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    diag_text.tag_configure("info", foreground="#94a3b8")
    diag_text.tag_configure("ok", foreground="#4ade80", font=("Consolas", 10, "bold"))
    diag_text.tag_configure("warn", foreground="#facc15")
    diag_text.tag_configure("err", foreground="#f87171", font=("Consolas", 10, "bold"))
    diag_text.tag_configure("highlight", foreground="#38bdf8", font=("Consolas", 10, "bold"))

    bot_bar = tk.Frame(diag_win, bg=pal["bg"], padx=10, pady=8)
    bot_bar.pack(fill=tk.X)

    def _log(msg, tag="info"):
        def _do_log():
            try:
                if diag_win.winfo_exists() and diag_text.winfo_exists():
                    diag_text.insert(tk.END, msg + "\n", tag)
                    diag_text.see(tk.END)
            except Exception:
                pass

        try:
            diag_win.after(0, _do_log)
        except Exception:
            pass

    def _run_diag():
        run_btn.config(state=tk.DISABLED, text="正在诊断...")
        diag_text.delete("1.0", tk.END)
        host = target_var.get().strip()
        token = diag_token_var.get().strip()

        def _worker():
            _log("=" * 60, "info")
            _log("  开始网络与内网服务连通性诊断", "highlight")
            _log("  目标主机: {} | 验证令牌: {}".format(host, token), "info")
            _log("=" * 60, "info")

            local_ips = wt_queue_selfcheck.get_lan_ip_list()
            primary = wt_queue_selfcheck.get_primary_lan_ip()
            _log("\n[步骤 1/4] 本机物理网络环境检查 ...", "info")
            _log("  ✓ 本机首选局域网 IP: {}".format(primary), "ok")
            _log("  ✓ 本机全部检测到的 IPv4: {}".format(" / ".join(local_ips)), "info")

            _log("\n[步骤 2/4] 目标主机与端口可达性检测 ...", "info")
            res = wt_queue_selfcheck.check_lan_connection(host, token=token)

            if res.get("port_open"):
                _log("  ✓ TCP 8768 端口可达，网络往返延迟: {:.1f} ms".format(res.get("ping_ms", 0)), "ok")
            else:
                _log("  ✗ TCP 8768 端口不可达！", "err")
                _log("    报错信息: {}".format(res.get("error")), "err")

            _log("\n[步骤 3/4] 任务队列服务健康与令牌校验 ...", "info")
            if res.get("ok"):
                _log("  ✓ 队列服务健康正常，服务名: {}".format(res.get("service")), "ok")
                _log("  ✓ Bearer 令牌鉴权验证通过！", "ok")
            elif res.get("auth_ok") is False:
                _log("  ✗ 令牌鉴权失败 (HTTP 401)：输入的令牌不正确！", "err")
            elif res.get("port_open"):
                _log("  ✗ 队列服务响应异常或未处于健康状态", "err")

            _log("\n[步骤 4/4] 监控服务 (端口 8767) 检测 ...", "info")
            if res.get("monitor_ok"):
                _log("  ✓ 监控服务健康正常", "ok")
            else:
                _log("  ⚠️ 监控服务未就绪或未开启（不影响任务正常派发）", "warn")

            _log("\n" + "=" * 60, "info")
            _log("  诊断结论与处置指引：", "highlight")
            if res.get("ok"):
                _log("  🎉 双机连通性完全正常！可直接在总控台和监控窗口中操作。", "ok")
            else:
                _log("  {}".format(res.get("diagnosis")), "warn")
            _log("=" * 60, "info")

            diag_win.after(0, lambda: run_btn.config(state=tk.NORMAL, text="重新诊断"))

        threading.Thread(target=_worker, daemon=True).start()

    run_btn = wt_theme.create_flat_button(
        top_bar, text="开始诊断", command=_run_diag, tone="primary", padx=12, pady=3
    )
    run_btn.pack(side=tk.LEFT)

    def _apply_and_close():
        chosen = target_var.get().strip()
        tok = diag_token_var.get().strip()
        if callable(on_apply) and chosen:
            on_apply(chosen, tok)
        diag_win.destroy()

    def _copy_clipboard(txt):
        try:
            diag_win.clipboard_clear()
            diag_win.clipboard_append(txt)
            diag_win.update()
        except Exception:
            pass

    wt_theme.create_flat_button(
        bot_bar, text="使用此地址并立即连接", command=_apply_and_close, tone="success", padx=12, pady=4
    ).pack(side=tk.LEFT)
    wt_theme.create_flat_button(
        bot_bar, text="复制诊断日志", command=lambda: _copy_clipboard(diag_text.get("1.0", tk.END)), tone="secondary", padx=10, pady=4
    ).pack(side=tk.LEFT, padx=8)
    wt_theme.create_flat_button(
        bot_bar, text="关闭", command=diag_win.destroy, tone="subtle", padx=10, pady=4
    ).pack(side=tk.RIGHT)

    diag_win.after(100, _run_diag)
    return diag_win


class TaskQueueWindow:
    """任务队列与服务器监控的统一只读/操作窗口。"""

    def __init__(
        self,
        master,
        initial_url="",
        initial_user="",
        initial_token="",
        on_settings_change=None,
        initial_monitor_url="",
        on_monitor_url_change=None,
        on_start_service=None,
        on_stop_service=None,
        history_urls=None,
        on_history_urls_change=None,
        initial_project_dir="",
    ):
        self.on_settings_change = on_settings_change
        self.on_monitor_url_change = on_monitor_url_change
        self.on_start_service = on_start_service
        self.on_stop_service = on_stop_service
        self.on_history_urls_change = on_history_urls_change
        self.project_dir = str(initial_project_dir or "").strip()
        self.primary_lan_ip = wt_queue_selfcheck.get_primary_lan_ip()

        # 初始化历史地址列表（支持记忆与快速切换）
        default_presets = ["http://127.0.0.1:8768"]
        if self.primary_lan_ip != "127.0.0.1":
            default_presets.append("http://{}:8768".format(self.primary_lan_ip))
        seed_list = []
        if initial_url:
            seed_list.append(initial_url)
        seed_list.extend(list(history_urls or []))
        seed_list.extend(default_presets)
        combined = []
        for u in seed_list:
            cleaned = str(u).strip().rstrip("/")
            if cleaned and cleaned not in combined:
                combined.append(cleaned)
        self.history_urls = combined[:12]

        self.base_url = (initial_url or DEFAULT_URL).strip().rstrip("/") or DEFAULT_URL
        self.monitor_base_url = (
            (initial_monitor_url or DEFAULT_MONITOR_URL).strip().rstrip("/")
            or DEFAULT_MONITOR_URL
        )
        # 任务队列服务可复用的 HTTP 连接（配合服务端 HTTP/1.1 keep-alive），
        # 由 _http_lock 串行化，避免多线程并发复用同一条连接出错。
        self._http_lock = threading.Lock()
        self._http_conn = None
        self._fetching = False
        self._monitor_fetching = False
        # 运行中任务日志自动刷新的防并发守卫（同一时刻只允许一个日志拉取线程）
        self._log_fetching = False
        self._after_id = None
        self._monitor_after_id = None
        self._closing = False
        self._queue_fail_streak = 0
        self._monitor_fail_streak = 0
        self._cached_tasks = []
        # 运行角色横幅与当前运行角色标识
        self._role_banner_var = tk.StringVar()
        self._role_banner_frame = None
        self._local_ips = _local_ipv4s()
        # 线程安全快照：后台线程（轮询/提交/上传/控制）不得直接读取 Tk 变量
        self._prefs_snapshot = {
            "user": str(initial_user or "").strip(),
            "token": str(initial_token or "").strip(),
            "mine_only": False,
        }

        pal = wt_theme.get_palette()
        self.window = tk.Toplevel(master)
        self.window.title("任务与服务器监控")
        self.window.configure(bg=pal["bg"])
        self.window.geometry("1180x760")
        self.window.minsize(920, 600)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        # ── 1. 运行角色横幅：鲜明色彩 + 运行角色 + 状态标识 ──
        self._role_banner_frame = tk.Frame(self.window, padx=14, pady=8)
        self._role_banner_frame.pack(fill=tk.X, before=None)

        self._role_title_lbl = tk.Label(
            self._role_banner_frame,
            text="",
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        )
        self._role_title_lbl.pack(side=tk.LEFT, padx=(0, 10))

        self._role_desc_lbl = tk.Label(
            self._role_banner_frame,
            textvariable=self._role_banner_var,
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        )
        self._role_desc_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self._role_action_btn = tk.Button(
            self._role_banner_frame,
            text="",
            relief=tk.FLAT,
            font=("Microsoft YaHei UI", 8, "bold"),
            cursor="hand2",
            padx=8,
            pady=2,
        )
        self._role_action_btn.pack(side=tk.RIGHT, padx=(8, 0))

        # ── 2. 连接目标与内网服务控制面板（双行清晰规划） ──
        self.top_frame = tk.Frame(
            self.window,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border"],
            padx=10,
            pady=6,
        )
        self.top_frame.pack(fill=tk.X, padx=8, pady=(4, 2))

        # 第一行：连接目标、凭证与刷新
        row1 = tk.Frame(self.top_frame, bg=pal["surface"])
        row1.pack(fill=tk.X, pady=(0, 4))

        tk.Label(
            row1,
            text="任务服务",
            bg=pal["surface"],
            fg=pal["text_secondary"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.LEFT)
        self.url_var = tk.StringVar(value=self.base_url)
        self.url_combo = ttk.Combobox(
            row1,
            textvariable=self.url_var,
            values=self.history_urls,
            width=24,
            font=("Microsoft YaHei UI", 9),
        )
        self.url_combo.pack(side=tk.LEFT, padx=(4, 8))
        self.url_combo.bind("<<ComboboxSelected>>", self._on_url_combo_selected)
        self.url_combo.bind("<Return>", lambda _e: self._apply_settings_and_refresh())

        tk.Label(
            row1,
            text="用户名",
            bg=pal["surface"],
            fg=pal["text_secondary"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)
        self.user_var = tk.StringVar(value=initial_user or "")
        tk.Entry(
            row1,
            textvariable=self.user_var,
            width=10,
            bg=pal["card_hover"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=pal["border"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(4, 8))

        tk.Label(
            row1,
            text="服务令牌",
            bg=pal["surface"],
            fg=pal["text_secondary"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)
        self.token_var = tk.StringVar(value=initial_token or "")
        tk.Entry(
            row1,
            textvariable=self.token_var,
            width=12,
            show="*",
            bg=pal["card_hover"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=pal["border"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(4, 8))

        tk.Label(
            row1,
            text="监控地址",
            bg=pal["surface"],
            fg=pal["text_secondary"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)
        self.monitor_url_var = tk.StringVar(value=self.monitor_base_url)
        tk.Entry(
            row1,
            textvariable=self.monitor_url_var,
            width=20,
            bg=pal["card_hover"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=pal["border"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(4, 8))

        wt_theme.create_flat_button(
            row1,
            text="🔄 刷新连接",
            command=self._apply_settings_and_refresh,
            tone="primary",
            padx=10,
            pady=3,
        ).pack(side=tk.LEFT, padx=(0, 6))

        wt_theme.create_flat_button(
            row1,
            text="🩺 网络诊断",
            command=lambda: self.open_network_diag_dialog(),
            tone="secondary",
            padx=8,
            pady=3,
        ).pack(side=tk.LEFT)

        # 第二行：内网服务引擎控制 + 本机局域网 IP 显示与复制
        row2 = tk.Frame(self.top_frame, bg=pal["surface"])
        row2.pack(fill=tk.X, pady=(2, 0))

        tk.Label(
            row2,
            text="内网服务控制:",
            bg=pal["surface"],
            fg=pal["text_secondary"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 6))

        wt_theme.create_flat_button(
            row2,
            text="🚀 一键启动内网服务",
            command=self.start_lan_services_action,
            tone="success",
            padx=10,
            pady=2,
        ).pack(side=tk.LEFT, padx=(0, 6))

        wt_theme.create_flat_button(
            row2,
            text="⏹️ 停止服务",
            command=self.stop_lan_services_action,
            tone="danger",
            padx=8,
            pady=2,
        ).pack(side=tk.LEFT, padx=(0, 16))

        local_service_url = "http://{}:8768".format(self.primary_lan_ip)
        tk.Label(
            row2,
            text="📌 本机局域网地址:",
            bg=pal["surface"],
            fg=pal["text_secondary"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)

        ip_lbl = tk.Label(
            row2,
            text=local_service_url,
            bg=pal["surface"],
            fg=pal["primary_text"],
            font=("Consolas", 9, "bold"),
        )
        ip_lbl.pack(side=tk.LEFT, padx=(3, 4))

        copy_btn = wt_theme.create_flat_button(
            row2,
            text="📋 复制",
            command=lambda: self._copy_text(local_service_url, copy_btn),
            tone="subtle",
            padx=6,
            pady=1,
            font=("Microsoft YaHei UI", 8),
        )
        copy_btn.pack(side=tk.LEFT, padx=(0, 6))

        tk.Label(
            row2,
            text="（供其他电脑在任务服务栏填入）",
            bg=pal["surface"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT)

        # ── 3. 连接异常显式诊断警示条（默认收起，异常展开） ──
        self._diag_banner_visible = False
        self._diag_banner_text_var = tk.StringVar(value="")
        self._diag_banner_frame = tk.Frame(
            self.window,
            bg="#fef2f2",
            highlightthickness=1,
            highlightbackground="#f87171",
            padx=12,
            pady=6,
        )

        tk.Label(
            self._diag_banner_frame,
            textvariable=self._diag_banner_text_var,
            bg="#fef2f2",
            fg="#991b1b",
            font=("Microsoft YaHei UI", 9, "bold"),
            anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        wt_theme.create_flat_button(
            self._diag_banner_frame,
            text="🔍 点击一键排查原因",
            command=lambda: self.open_network_diag_dialog(),
            tone="danger",
            padx=8,
            pady=2,
            font=("Microsoft YaHei UI", 8, "bold"),
        ).pack(side=tk.RIGHT, padx=(6, 0))

        wt_theme.create_flat_button(
            self._diag_banner_frame,
            text="🚀 本机启动服务",
            command=self.start_lan_services_action,
            tone="success",
            padx=8,
            pady=2,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT, padx=(6, 0))

        wt_theme.create_flat_button(
            self._diag_banner_frame,
            text="✕ 忽略",
            command=self._hide_diag_banner,
            tone="subtle",
            padx=6,
            pady=2,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT)

        self.url_var.trace_add("write", self._on_settings_text_changed)
        self.user_var.trace_add("write", self._on_user_changed)
        self.token_var.trace_add("write", self._on_settings_text_changed)
        self.monitor_url_var.trace_add("write", self._on_monitor_url_changed)
        # 变量初值同步到线程安全快照（此后由上述回调持续维护）
        self._sync_prefs_snapshot()

        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 8))

        self.queue_tab = tk.Frame(self.notebook, bg=pal["bg"])
        self.monitor_tab = tk.Frame(self.notebook, bg=pal["bg"])
        self.flows_tab = tk.Frame(self.notebook, bg=pal["bg"])
        self.notebook.add(self.queue_tab, text="任务队列")
        self.notebook.add(self.monitor_tab, text="服务器监控")
        self.notebook.add(self.flows_tab, text="流程仓库")
        self._build_queue_tab(self.queue_tab)
        self._build_monitor_tab(self.monitor_tab)
        self._build_flows_tab(self.flows_tab)

        self._after_id = self.window.after(POLL_MS, self._poll_loop)
        self._monitor_after_id = self.window.after(POLL_MS, self._monitor_poll_loop)
        # 流程仓库页签初始加载一次（后续由 _poll_loop 按开关续刷）
        self.window.after(200, self.refresh_flows)
        self._refresh_role_banner()

    def _copy_text(self, text, button_widget=None):
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
            self.window.update()
            if button_widget is not None:
                orig_text = button_widget.cget("text")
                button_widget.config(text="✓ 已复制!")
                self.window.after(1800, lambda: button_widget.config(text=orig_text))
            return True
        except Exception:
            return False

    def _on_url_combo_selected(self, _event=None):
        new_url = self.url_var.get().strip()
        if new_url:
            derived_mon = _derive_monitor_url(new_url)
            self.monitor_url_var.set(derived_mon)
            self._record_history_url(new_url)
            self._apply_settings_and_refresh()

    def _record_history_url(self, url):
        cleaned = str(url or "").strip().rstrip("/")
        if not cleaned:
            return
        if cleaned in self.history_urls:
            self.history_urls.remove(cleaned)
        self.history_urls.insert(0, cleaned)
        self.history_urls = self.history_urls[:12]
        if hasattr(self, "url_combo"):
            self.url_combo["values"] = self.history_urls
        if self.on_history_urls_change:
            try:
                self.on_history_urls_change(self.history_urls)
            except Exception:
                pass

    def _show_diag_banner(self, error_message, is_queue=True):
        if not hasattr(self, "_diag_banner_frame"):
            return
        service_name = "任务服务 (8768)" if is_queue else "监控服务 (8767)"
        target_url = self.base_url if is_queue else self.monitor_base_url
        friendly = self._friendly_error(error_message)
        self._diag_banner_text_var.set(
            "⚠️ 无法连接至 {} [{}]：{}".format(service_name, target_url, friendly)
        )
        if not getattr(self, "_diag_banner_visible", False):
            self._diag_banner_frame.pack(fill=tk.X, padx=8, pady=(2, 4), after=self.top_frame)
            self._diag_banner_visible = True

    def _hide_diag_banner(self):
        if hasattr(self, "_diag_banner_frame") and getattr(self, "_diag_banner_visible", False):
            self._diag_banner_frame.pack_forget()
            self._diag_banner_visible = False

    def open_network_diag_dialog(self, default_target=None):
        """打开内网服务与网络连通性一键交互式诊断窗口。"""
        def _on_apply(url, tok):
            self.url_var.set(url)
            self.monitor_url_var.set(_derive_monitor_url(url))
            self.token_var.set(tok)
            self._record_history_url(url)
            self._apply_settings_and_refresh()

        open_network_diag_dialog(
            self.window,
            default_target=default_target or self.base_url,
            default_token=self.token_var.get() or "wt2026",
            history_urls=self.history_urls,
            on_apply=_on_apply,
        )

    def start_lan_services_action(self):
        """一键启动本机内网服务（队列 8768 + 监控 8767），带 --force 级别孤儿端口清理。"""
        token = str(self.token_var.get() or "wt2026").strip()
        if not messagebox.askyesno(
            "启动内网服务",
            "确定要在本机启动内网队列服务(8768)与监控服务(8767)吗？\n"
            "系统将自动清理端口残留，并在本机后台启动服务供局域网协作。",
            parent=self.window,
        ):
            return

        def _worker():
            res = wt_queue_selfcheck.start_lan_services(token=token, force=True, timeout=15)

            def _ui():
                if res["ok"]:
                    primary_ip = res["primary_ip"]
                    task_url = "http://127.0.0.1:8768"
                    mon_url = "http://127.0.0.1:8767"
                    self.url_var.set(task_url)
                    self.monitor_url_var.set(mon_url)
                    self._record_history_url(task_url)
                    self._record_history_url("http://{}:8768".format(primary_ip))
                    self._apply_settings_and_refresh()

                    share_url = "http://{}:8768".format(primary_ip)
                    self._copy_text(share_url)

                    messagebox.showinfo(
                        "内网服务启动成功",
                        "✅ 本机内网服务已成功启动！\n\n"
                        "• 任务队列服务：http://0.0.0.0:8768 (运行中)\n"
                        "• 监控管理服务：http://0.0.0.0:8767 (运行中)\n"
                        "• 服务访问令牌：{}\n\n"
                        "📌 本机内网服务地址：\n{}\n\n"
                        "（该地址已自动复制到系统剪贴板，供其他电脑填写连接）".format(token, share_url),
                        parent=self.window,
                    )
                else:
                    err = res.get("error") or "启动超时或异常"
                    messagebox.showerror(
                        "启动内网服务失败",
                        "启动失败：\n{}\n\n请检查 logs/task_server.log 或使用「网络诊断」排查。".format(err),
                        parent=self.window,
                    )

            self.window.after(0, _ui)

        threading.Thread(target=_worker, daemon=True).start()

    def stop_lan_services_action(self):
        """一键停止本机运行的内网服务。"""
        if not messagebox.askyesno(
            "停止内网服务",
            "确定要停止本机运行的队列与监控服务吗？\n停止后正在运行的任务可能中断，其他客户端将无法提交任务。",
            parent=self.window,
        ):
            return
        killed = wt_queue_selfcheck.stop_lan_services()
        self._refresh_role_banner()
        self._apply_settings_and_refresh()
        messagebox.showinfo(
            "服务已停止",
            "已成功停止本机内网服务（清理了 {} 个相关进程）。".format(killed),
            parent=self.window,
        )

    def _resolve_role(self, url=None):
        """按任务服务地址判定本窗口的运行角色。

        返回 (role, desc) 2 元组：
        - "server"：地址指向本机（127.0.0.1/localhost/本机局域网 IP）
        - "client"：地址指向远程服务器
        - "unknown"：未配置有效地址
        """
        if url is None:
            url = (self.base_url or "").strip()
        else:
            url = str(url or "").strip()
        try:
            parsed = urllib.parse.urlsplit(url)
            host = parsed.hostname or ""
        except ValueError:
            host = ""
        if not host:
            return "unknown", "未配置任务服务地址——请填写服务器地址后刷新"

        primary_ip = getattr(self, "primary_lan_ip", "127.0.0.1")
        if host in self._local_ips:
            status = wt_queue_selfcheck.get_local_service_status()
            if status.get("task_running"):
                token = str(getattr(self, "token_var", None) and self.token_var.get() or "wt2026").strip()
                desc = "服务器本机控制台 —— 服务监听 0.0.0.0:8768 / 8767 | 本机内网地址: http://{}:8768 | 令牌: {}".format(primary_ip, token)
            else:
                desc = "服务器本机控制台 —— 本地单机模式 (127.0.0.1:8768) | 点击右侧可一键对外启动为内网服务器"
            return "server", desc

        desc = "远程客户端 —— 任务将发送到服务器 {} 执行（本机 IP：{}）".format(host, primary_ip)
        return "client", desc

    def _refresh_role_banner(self):
        """按当前任务服务地址与本机服务监听状态刷新角色横幅与窗口标题。"""
        role, desc = self._resolve_role()
        url = (self.base_url or "").strip()
        try:
            parsed = urllib.parse.urlsplit(url)
            host = parsed.hostname or "127.0.0.1"
        except ValueError:
            host = "127.0.0.1"
        primary_ip = getattr(self, "primary_lan_ip", "127.0.0.1")
        token = str(getattr(self, "token_var", None) and self.token_var.get() or "wt2026").strip()

        if role == "server":
            status = wt_queue_selfcheck.get_local_service_status()
            if status.get("task_running"):
                bg = "#064e3b"  # 翡翠墨绿
                fg = "#ffffff"
                desc_fg = "#a7f3d0"
                btn_bg = "#047857"
                btn_fg = "#ffffff"
                title_text = "🖥️【本机服务器模式】对外服务中"
                self._role_banner_var.set(
                    "  服务器本机控制台 —— 服务监听 0.0.0.0:8768 / 8767 | 本机内网地址: http://{}:8768 | 令牌: {}".format(primary_ip, token)
                )
                if hasattr(self, "_role_action_btn") and self._role_action_btn:
                    self._role_action_btn.config(
                        text="📋 复制本机服务地址",
                        command=lambda: self._copy_text("http://{}:8768".format(primary_ip), self._role_action_btn),
                        bg=btn_bg,
                        fg=btn_fg,
                        activebackground="#059669",
                    )
                    self._role_action_btn.pack(side=tk.RIGHT, padx=(8, 0))
                win_title = "任务与服务器监控 【服务器本机】[🖥️ 本机服务器模式 - IP: {}]".format(primary_ip)
            else:
                bg = "#1e293b"  # 板岩灰
                fg = "#f8fafc"
                desc_fg = "#94a3b8"
                btn_bg = "#334155"
                btn_fg = "#f8fafc"
                title_text = "💻【本地单机模式】"
                self._role_banner_var.set(
                    "  服务器本机控制台 —— 当前连接至本机 (127.0.0.1:8768) | 点击右侧可一键对外启动为内网服务器"
                )
                if hasattr(self, "_role_action_btn") and self._role_action_btn:
                    self._role_action_btn.config(
                        text="🚀 一键启动内网服务",
                        command=self.start_lan_services_action,
                        bg=btn_bg,
                        fg=btn_fg,
                        activebackground="#475569",
                    )
                    self._role_action_btn.pack(side=tk.RIGHT, padx=(8, 0))
                win_title = "任务与服务器监控 【服务器本机】[💻 本地单机模式]"
        elif role == "client":
            bg = "#1e3a8a"  # 科技深蓝
            fg = "#ffffff"
            desc_fg = "#bfdbfe"
            btn_bg = "#2563eb"
            btn_fg = "#ffffff"
            title_text = "🌐【远程协作客户端模式】"
            self._role_banner_var.set(
                "  远程客户端 —— 任务将发送到服务器 {} 执行（本机 IP：{}）".format(host, primary_ip)
            )
            if hasattr(self, "_role_action_btn") and self._role_action_btn:
                self._role_action_btn.config(
                    text="🩺 网络诊断与排查",
                    command=lambda: self.open_network_diag_dialog(host),
                    bg=btn_bg,
                    fg=btn_fg,
                    activebackground="#3b82f6",
                )
                self._role_action_btn.pack(side=tk.RIGHT, padx=(8, 0))
            win_title = "任务与服务器监控 【远程客户端】[🌐 远程协作 -> {}:8768]".format(host)
        else:
            bg = "#1e293b"
            fg = "#94a3b8"
            desc_fg = "#94a3b8"
            title_text = "❓【未配置】"
            self._role_banner_var.set("  未配置任务服务地址——请填写服务器地址后刷新")
            if hasattr(self, "_role_action_btn") and self._role_action_btn:
                self._role_action_btn.pack_forget()
            win_title = "任务与服务器监控"

        if hasattr(self, "_role_banner_frame") and self._role_banner_frame is not None:
            self._role_banner_frame.config(bg=bg)
            if hasattr(self, "_role_title_lbl") and self._role_title_lbl is not None:
                self._role_title_lbl.config(text=title_text, bg=bg, fg=fg)
            if hasattr(self, "_role_desc_lbl") and self._role_desc_lbl is not None:
                self._role_desc_lbl.config(bg=bg, fg=desc_fg)
        self.window.title(win_title)

    def _build_queue_tab(self, parent):
        pal = wt_theme.get_palette()

        # ── 顶部快捷筛选与控制栏 ──
        filter_frame = tk.Frame(
            parent,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border"],
            padx=10,
            pady=6,
        )
        filter_frame.pack(fill=tk.X, padx=8, pady=(6, 4))

        tk.Label(
            filter_frame,
            text="状态筛选:",
            bg=pal["surface"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT, padx=(0, 4))

        self.status_filter_var = tk.StringVar(value="全部")
        status_options = build_status_filter_options()
        pill_selector = wt_theme.create_pill_selector(
            filter_frame,
            options=status_options,
            variable=self.status_filter_var,
            on_change=lambda _v: self._on_filter_changed(),
        )
        pill_selector.pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(filter_frame, text="🔍", bg=pal["surface"], fg=pal["muted"]).pack(side=tk.LEFT)
        self.keyword_var = tk.StringVar()
        keyword_entry = tk.Entry(
            filter_frame,
            textvariable=self.keyword_var,
            width=22,
            bg=pal["card_hover"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=pal["border"],
            font=("Microsoft YaHei UI", 9),
        )
        keyword_entry.pack(side=tk.LEFT, padx=(4, 6))
        keyword_entry.bind("<KeyRelease>", lambda _e: self._on_filter_changed())
        keyword_entry.bind("<Return>", self._on_filter_changed)

        self.conn_var = tk.StringVar(value="未连接")
        tk.Label(
            filter_frame,
            textvariable=self.conn_var,
            bg=pal["surface"],
            fg=pal["primary_text"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.RIGHT, padx=(8, 0))

        self.auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            filter_frame,
            text="自动刷新 (2s)",
            variable=self.auto_var,
            bg=pal["surface"],
            fg=pal["text"],
            activebackground=pal["surface"],
            selectcolor=pal["surface"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.RIGHT, padx=(0, 8))

        self.mine_only_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            filter_frame,
            text="只看我的任务",
            variable=self.mine_only_var,
            command=self._on_mine_only_toggle,
            bg=pal["surface"],
            fg=pal["text"],
            activebackground=pal["surface"],
            selectcolor=pal["surface"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.RIGHT, padx=(0, 8))

        # ── 全局队列统计与快捷操作看板 (Epic 3: Global Queue Dashboard) ──
        summary_frame = tk.Frame(
            parent,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border"],
            padx=10,
            pady=6,
        )
        summary_frame.pack(fill=tk.X, padx=8, pady=(0, 4))

        tk.Label(
            summary_frame,
            text="队列看板:",
            bg=pal["surface"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.LEFT, padx=(0, 6))

        self.stat_badge_total = wt_theme.create_badge(summary_frame, "总计 0", "muted")
        self.stat_badge_total.pack(side=tk.LEFT, padx=(0, 4))

        self.stat_badge_pending = wt_theme.create_badge(summary_frame, "排队中 0", "info")
        self.stat_badge_pending.pack(side=tk.LEFT, padx=(0, 4))

        self.stat_badge_running = wt_theme.create_badge(summary_frame, "运行中 0", "primary")
        self.stat_badge_running.pack(side=tk.LEFT, padx=(0, 4))

        self.stat_badge_success = wt_theme.create_badge(summary_frame, "成功 0", "success")
        self.stat_badge_success.pack(side=tk.LEFT, padx=(0, 4))

        self.stat_badge_failed = wt_theme.create_badge(summary_frame, "失败 0", "danger")
        self.stat_badge_failed.pack(side=tk.LEFT, padx=(0, 8))

        # 快捷批处理操作按钮（右侧）
        wt_theme.create_flat_button(
            summary_frame,
            text="🧹 清理已结束",
            command=self.clear_finished_tasks_action,
            tone="subtle",
            padx=8,
            pady=2,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT, padx=(4, 0))

        wt_theme.create_flat_button(
            summary_frame,
            text="🔄 重试全部失败",
            command=self.retry_all_failed_tasks_action,
            tone="warning",
            padx=8,
            pady=2,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT, padx=(4, 0))

        wt_theme.create_flat_button(
            summary_frame,
            text="📐 测风塔按需排队 (高级)",
            command=self.open_batch_mast_queue_dialog,
            tone="secondary",
            padx=8,
            pady=2,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT, padx=(0, 4))

        # ── 核心工作区：Master-Detail 左右分栏 ──
        main_paned = tk.PanedWindow(parent, orient=tk.HORIZONTAL, bg=pal["bg"], sashwidth=4, bd=0)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        # 左栏：任务列表与排队管理
        left_frame = tk.Frame(
            main_paned,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border"],
            padx=4,
            pady=4,
        )
        left_top = tk.Frame(left_frame, bg=pal["surface"], pady=2)
        left_top.pack(fill=tk.X, padx=4, pady=(2, 4))
        tk.Label(
            left_top,
            text="任务列表",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=pal["surface"],
            fg=pal["text"],
        ).pack(side=tk.LEFT)

        wt_theme.create_flat_button(
            left_top,
            text="+ 提交任务",
            command=self.submit_task,
            tone="primary",
            padx=8,
            pady=2,
        ).pack(side=tk.RIGHT, padx=(4, 0))

        wt_theme.create_flat_button(
            left_top,
            text="+ 本地JSON",
            command=self.submit_local_flow_file,
            tone="secondary",
            padx=8,
            pady=2,
        ).pack(side=tk.RIGHT)

        tree_container = tk.Frame(left_frame, bg=pal["surface"])
        tree_container.pack(fill=tk.BOTH, expand=True)

        columns = (
            "taskId",
            "user",
            "status",
            "priority",
            "scheduled",
            "attempts",
            "progress",
            "currentStep",
            "created",
            "updated",
        )
        self.task_tree = ttk.Treeview(tree_container, columns=columns, show="headings", selectmode="browse")
        headings = {
            "taskId": "任务ID",
            "user": "用户",
            "status": "状态",
            "priority": "优先级",
            "scheduled": "预约",
            "attempts": "尝试",
            "progress": "进度",
            "currentStep": "当前步骤",
            "created": "创建时间",
            "updated": "更新时间",
        }
        widths = {
            "taskId": 180,
            "user": 65,
            "status": 65,
            "priority": 55,
            "scheduled": 120,
            "attempts": 55,
            "progress": 95,
            "currentStep": 150,
            "created": 130,
            "updated": 130,
        }
        for column in columns:
            self.task_tree.heading(column, text=headings[column])
            self.task_tree.column(column, width=widths[column], minwidth=45, anchor="w")

        # 滚动条先 pack、树最后 pack。pack 官方算法（Tk doc/pack.n「THE
        # PACKER ALGORITHM」）按 packing list 顺序扫描分配，先 pack 的先占
        # cavity；若 cavity 收缩到放不下后续控件，"all remaining content
        # on the packing list will be unmapped from the screen"（直接从屏
        # 幕消失）。树列宽总和固定（本表约 1190px），若树先 pack，窗口/分栏
        # 变窄时后 pack 的滚动条会被挤成 1x1 直至 unmap —— 滚动条消失。
        # 滚动条先占位，树吃剩余空间并靠列 stretch 自适应，任何宽度都可见。
        v_scroll = ttk.Scrollbar(tree_container, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=v_scroll.set)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        h_scroll = ttk.Scrollbar(tree_container, orient="horizontal", command=self.task_tree.xview)
        self.task_tree.configure(xscrollcommand=h_scroll.set)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)

        self.task_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_select)

        main_paned.add(left_frame, minsize=380, width=520)

        # 右栏：任务看板与实时终端控制台
        right_frame = tk.Frame(main_paned, bg=pal["bg"])

        # 上半部分：任务详情卡片与操作栏
        inspect_card = wt_theme.create_card_frame(right_frame, padx=12, pady=8)
        inspect_card.pack(fill=tk.X, pady=(0, 6))

        row1 = tk.Frame(inspect_card, bg=pal["card"])
        row1.pack(fill=tk.X)
        self.detail_task_id_var = tk.StringVar(value="未选中任务")
        tk.Label(
            row1,
            textvariable=self.detail_task_id_var,
            font=("Microsoft YaHei UI", 11, "bold"),
            bg=pal["card"],
            fg=pal["text"],
        ).pack(side=tk.LEFT)
        self.detail_status_badge = wt_theme.create_badge(row1, text="未选择", tone="muted")
        self.detail_status_badge.pack(side=tk.RIGHT)

        self.detail_meta_var = tk.StringVar(value="请在左侧列表中点击选择任务查看详情与操作")
        tk.Label(
            inspect_card,
            textvariable=self.detail_meta_var,
            font=("Microsoft YaHei UI", 9),
            bg=pal["card"],
            fg=pal["muted"],
            anchor="w",
        ).pack(fill=tk.X, pady=(4, 4))

        row_prog = tk.Frame(inspect_card, bg=pal["card"])
        row_prog.pack(fill=tk.X, pady=(2, 2))
        self.detail_step_var = tk.StringVar(value="当前步骤：-")
        tk.Label(
            row_prog,
            textvariable=self.detail_step_var,
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=pal["card"],
            fg=pal["primary_text"],
        ).pack(side=tk.LEFT)
        self.detail_progress_var = tk.StringVar(value="步骤进度：-")
        tk.Label(
            row_prog,
            textvariable=self.detail_progress_var,
            font=("Microsoft YaHei UI", 9),
            bg=pal["card"],
            fg=pal["text_secondary"],
        ).pack(side=tk.RIGHT)

        self.detail_progressbar = ttk.Progressbar(
            inspect_card,
            orient="horizontal",
            mode="determinate",
            style="Horizontal.TProgressbar",
        )
        self.detail_progressbar.pack(fill=tk.X, pady=(4, 4))

        self.detail_eta_var = tk.StringVar(value="")
        tk.Label(
            inspect_card,
            textvariable=self.detail_eta_var,
            font=("Microsoft YaHei UI", 9),
            bg=pal["card"],
            fg=pal.get("primary_text", "#1e40af"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 6))

        # 控制按钮组
        control_bar = tk.Frame(inspect_card, bg=pal["card"])
        control_bar.pack(fill=tk.X)

        self._control_buttons = {}
        btn_pause = wt_theme.create_flat_button(
            control_bar, "暂停", lambda: self.control_action("pause"), tone="warning", padx=10, pady=3
        )
        btn_pause.pack(side=tk.LEFT, padx=(0, 6))
        self._control_buttons["pause"] = btn_pause

        btn_resume = wt_theme.create_flat_button(
            control_bar, "继续", lambda: self.control_action("resume"), tone="primary", padx=10, pady=3
        )
        btn_resume.pack(side=tk.LEFT, padx=(0, 6))
        self._control_buttons["resume"] = btn_resume

        btn_term = wt_theme.create_flat_button(
            control_bar, "终止", lambda: self.control_action("terminate"), tone="danger", padx=10, pady=3
        )
        btn_term.pack(side=tk.LEFT, padx=(0, 6))
        self._control_buttons["terminate"] = btn_term

        btn_cancel = wt_theme.create_flat_button(
            control_bar, "取消", lambda: self.control_action("cancel"), tone="secondary", padx=10, pady=3
        )
        btn_cancel.pack(side=tk.LEFT, padx=(0, 6))
        self._control_buttons["cancel"] = btn_cancel

        btn_del = wt_theme.create_flat_button(
            control_bar, "删除", lambda: self.control_action("delete"), tone="secondary", padx=10, pady=3
        )
        btn_del.pack(side=tk.LEFT, padx=(0, 6))
        self._control_buttons["delete"] = btn_del

        self.btn_view_report = wt_theme.create_flat_button(
            control_bar, "查看报告", self.view_report, tone="secondary", padx=10, pady=3
        )
        self.btn_view_report.pack(side=tk.RIGHT)

        # 下半部分：实时终端控制台
        console_card = wt_theme.create_card_frame(right_frame, padx=8, pady=6)
        console_card.pack(fill=tk.BOTH, expand=True)

        console_header = tk.Frame(console_card, bg=pal["card"])
        console_header.pack(fill=tk.X, padx=2, pady=(0, 4))
        tk.Label(
            console_header,
            text="任务运行日志 (实时终端)",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=pal["card"],
            fg=pal["text"],
        ).pack(side=tk.LEFT)

        wt_theme.create_flat_button(
            console_header,
            text="复制日志",
            command=self._copy_log_text,
            tone="subtle",
            padx=8,
            pady=1,
        ).pack(side=tk.RIGHT, padx=(4, 0))

        wt_theme.create_flat_button(
            console_header,
            text="清屏",
            command=self._clear_log_text,
            tone="subtle",
            padx=8,
            pady=1,
        ).pack(side=tk.RIGHT)

        text_container = tk.Frame(console_card, bg=pal["terminal_bg"])
        text_container.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            text_container,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=pal["terminal_bg"],
            fg=pal["terminal_fg"],
            insertbackground=pal["terminal_fg"],
            font=("Consolas", 9),
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        log_scrollbar = tk.Scrollbar(text_container, command=self.log_text.yview, relief=tk.FLAT)
        self.log_text.config(yscrollcommand=log_scrollbar.set)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 日志配色统一从 wt_theme 取（深底变体），不再就地硬编码。
        wt_logging.configure_log_tags(
            self.log_text, dark=True, font_family="Consolas", font_size=9
        )

        main_paned.add(right_frame, minsize=420, width=580)

        # ── 底部：统计状态条 ──
        stats_frame = tk.Frame(
            parent,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border"],
            padx=10,
            pady=4,
        )
        stats_frame.pack(fill=tk.X, padx=8, pady=(0, 6))
        self.stats_var = tk.StringVar(value="尚未获取统计")
        tk.Label(
            stats_frame,
            textvariable=self.stats_var,
            bg=pal["surface"],
            fg=pal["text_secondary"],
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(fill=tk.X)

        self._update_control_buttons()


    def _build_monitor_tab(self, parent):
        pal = wt_theme.get_palette()
        top = tk.Frame(
            parent,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border"],
            padx=10,
            pady=6,
        )
        top.pack(fill=tk.X, padx=8, pady=(6, 4))
        self.monitor_auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            top,
            text="自动刷新 (2s)",
            variable=self.monitor_auto_var,
            bg=pal["surface"],
            fg=pal["text"],
            activebackground=pal["surface"],
            selectcolor=pal["surface"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)

        wt_theme.create_flat_button(
            top,
            text="刷新监控",
            command=self.refresh_monitor,
            tone="secondary",
            padx=10,
            pady=3,
        ).pack(side=tk.LEFT, padx=(10, 0))

        wt_theme.create_flat_button(
            top,
            text="启动监控服务",
            command=lambda: self._service_action("start", "monitor"),
            tone="success",
            padx=10,
            pady=3,
        ).pack(side=tk.LEFT, padx=(8, 0))

        wt_theme.create_flat_button(
            top,
            text="停止监控服务",
            command=lambda: self._service_action("stop", "monitor"),
            tone="danger",
            padx=10,
            pady=3,
        ).pack(side=tk.LEFT, padx=(8, 0))

        # 顶部右侧网络延迟徽标
        self.monitor_ping_badge = wt_theme.create_badge(top, "⏱️ 等待探测", tone="muted")
        self.monitor_ping_badge.pack(side=tk.RIGHT, padx=(8, 0))

        hint = tk.Label(
            top,
            text="提示：本页显示服务器进程状态（8767 监控服务）",
            bg=pal["surface"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="e",
        )
        hint.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 8))

        # ── 4 块现代化指标卡片网格 ──
        cards_frame = tk.Frame(parent, bg=pal["bg"])
        cards_frame.pack(fill=tk.X, padx=8, pady=(2, 6))
        for col_idx in range(4):
            cards_frame.columnconfigure(col_idx, weight=1, uniform="mcard")

        self.monitor_conn_var = tk.StringVar(value="未连接")
        self.monitor_ping_var = tk.StringVar(value="等待探测")
        self.run_status_var = tk.StringVar(value="未知")
        self.activity_var = tk.StringVar(value="-")
        self.updated_var = tk.StringVar(value="-")
        self.monitor_heartbeat_var = tk.StringVar(value="轮询周期 2s")
        self.monitor_health_var = tk.StringVar(value="待检测")
        self.error_var = tk.StringVar(value="-")

        # 卡片 1：连接状态
        card0 = wt_theme.create_card_frame(cards_frame, padx=10, pady=8)
        card0.grid(row=0, column=0, sticky="nsew", padx=(0, 4))
        tk.Label(
            card0,
            text="🔌 监控服务连接",
            bg=pal["card"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")
        self.monitor_conn_lbl = tk.Label(
            card0,
            textvariable=self.monitor_conn_var,
            bg=pal["card"],
            fg=pal["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
            anchor="w",
        )
        self.monitor_conn_lbl.pack(anchor="w", pady=(3, 1))
        tk.Label(
            card0,
            textvariable=self.monitor_ping_var,
            bg=pal["card"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(anchor="w")

        # 卡片 2：运行状态
        card1 = wt_theme.create_card_frame(cards_frame, padx=10, pady=8)
        card1.grid(row=0, column=1, sticky="nsew", padx=4)
        tk.Label(
            card1,
            text="⚡ 任务运行状态",
            bg=pal["card"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")
        self.monitor_status_lbl = tk.Label(
            card1,
            textvariable=self.run_status_var,
            bg=pal["card"],
            fg=pal["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
            anchor="w",
        )
        self.monitor_status_lbl.pack(anchor="w", pady=(3, 1))
        tk.Label(
            card1,
            textvariable=self.activity_var,
            bg=pal["card"],
            fg=pal["text_secondary"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(anchor="w")

        # 卡片 3：状态心跳
        card2 = wt_theme.create_card_frame(cards_frame, padx=10, pady=8)
        card2.grid(row=0, column=2, sticky="nsew", padx=4)
        tk.Label(
            card2,
            text="⏱️ 状态心跳监测",
            bg=pal["card"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")
        tk.Label(
            card2,
            textvariable=self.updated_var,
            bg=pal["card"],
            fg=pal["text"],
            font=("Consolas", 10, "bold"),
            anchor="w",
        ).pack(anchor="w", pady=(3, 1))
        tk.Label(
            card2,
            textvariable=self.monitor_heartbeat_var,
            bg=pal["card"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(anchor="w")

        # 卡片 4：健康诊断
        card3 = wt_theme.create_card_frame(cards_frame, padx=10, pady=8)
        card3.grid(row=0, column=3, sticky="nsew", padx=(4, 0))
        tk.Label(
            card3,
            text="🛡️ 系统健康诊断",
            bg=pal["card"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(anchor="w")
        self.monitor_health_lbl = tk.Label(
            card3,
            textvariable=self.monitor_health_var,
            bg=pal["card"],
            fg=pal["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
            anchor="w",
        )
        self.monitor_health_lbl.pack(anchor="w", pady=(3, 1))
        err_row = tk.Frame(card3, bg=pal["card"])
        err_row.pack(fill=tk.X)
        tk.Label(
            err_row,
            textvariable=self.error_var,
            bg=pal["card"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
            anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        wt_theme.create_flat_button(
            err_row,
            text="📋 复制",
            command=self._copy_monitor_error_detail,
            tone="subtle",
            padx=4,
            pady=1,
            font=("Microsoft YaHei UI", 7),
        ).pack(side=tk.RIGHT)

        # ── 运行日志卡片与智能工具栏 ──
        log_card = wt_theme.create_card_frame(parent, padx=8, pady=6)
        log_card.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        log_header = tk.Frame(log_card, bg=pal["card"])
        log_header.pack(fill=tk.X, padx=2, pady=(0, 4))
        tk.Label(
            log_header,
            text="📋 运行日志（只读，最后 300 行）",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=pal["card"],
            fg=pal["text"],
        ).pack(side=tk.LEFT, padx=(0, 10))

        # 搜索过滤
        tk.Label(
            log_header,
            text="🔍",
            bg=pal["card"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT)
        self.monitor_search_var = tk.StringVar(value="")
        search_entry = tk.Entry(
            log_header,
            textvariable=self.monitor_search_var,
            width=14,
            font=("Microsoft YaHei UI", 8),
            relief=tk.SOLID,
            bd=1,
        )
        search_entry.pack(side=tk.LEFT, padx=(2, 2))
        self.monitor_search_var.trace_add("write", lambda *_: self._on_monitor_log_filter_changed())
        wt_theme.create_flat_button(
            log_header,
            text="✕",
            command=lambda: self.monitor_search_var.set(""),
            tone="subtle",
            padx=4,
            pady=1,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT, padx=(0, 8))

        # 级别筛选
        self.monitor_level_var = tk.StringVar(value="全部")
        level_cb = ttk.Combobox(
            log_header,
            textvariable=self.monitor_level_var,
            values=["全部", "仅错误", "仅警告", "仅信息"],
            width=8,
            state="readonly",
            font=("Microsoft YaHei UI", 8),
        )
        level_cb.pack(side=tk.LEFT, padx=(0, 8))
        level_cb.bind("<<ComboboxSelected>>", lambda _: self._on_monitor_log_filter_changed())

        # 右侧操作
        self.monitor_pause_scroll_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            log_header,
            text="⏸ 暂停滚屏",
            variable=self.monitor_pause_scroll_var,
            bg=pal["card"],
            fg=pal["text"],
            activebackground=pal["card"],
            selectcolor=pal["card"],
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT, padx=(0, 8))

        wt_theme.create_flat_button(
            log_header,
            text="📋 复制可见日志",
            command=self._copy_monitor_visible_logs,
            tone="secondary",
            padx=8,
            pady=2,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT, padx=(4, 0))

        wt_theme.create_flat_button(
            log_header,
            text="🧹 清屏",
            command=self._clear_monitor_log_view,
            tone="subtle",
            padx=6,
            pady=2,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.RIGHT)

        text_frame = tk.Frame(log_card, bg=pal["terminal_bg"])
        text_frame.pack(fill=tk.BOTH, expand=True)

        self.monitor_log_text = tk.Text(
            text_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg=pal["terminal_bg"],
            fg=pal["terminal_fg"],
            insertbackground=pal["terminal_fg"],
            font=("Consolas", 9),
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        log_scrollbar = tk.Scrollbar(text_frame, command=self.monitor_log_text.yview, relief=tk.FLAT)
        self.monitor_log_text.config(yscrollcommand=log_scrollbar.set)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.monitor_log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 同 log_text：配色统一由 wt_logging 从 wt_theme 取。
        wt_logging.configure_log_tags(
            self.monitor_log_text, dark=True, font_family="Consolas", font_size=9
        )

    def _build_flows_tab(self, parent):
        """流程仓库页签：服务器 flow_packages 的版本台账 + 提交人/时间 + 关联任务与项目参数。

        数据来自 GET /api/flows（版本台账）与 GET /api/tasks（按 flowPath 匹配
        最近使用该流程的任务，含 runtimeConfig 项目参数）。
        """
        pal = wt_theme.get_palette()
        top = tk.Frame(
            parent,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border"],
            padx=10,
            pady=6,
        )
        top.pack(fill=tk.X, padx=8, pady=(6, 4))
        self.flows_auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            top,
            text="随队列自动刷新",
            variable=self.flows_auto_var,
            bg=pal["surface"],
            fg=pal["text"],
            activebackground=pal["surface"],
            selectcolor=pal["surface"],
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.LEFT)

        wt_theme.create_flat_button(
            top,
            text="刷新仓库",
            command=self.refresh_flows,
            tone="secondary",
            padx=10,
            pady=3,
        ).pack(side=tk.LEFT, padx=(10, 0))

        self.flows_conn_var = tk.StringVar(value="未加载")
        tk.Label(
            top,
            textvariable=self.flows_conn_var,
            bg=pal["surface"],
            fg=pal["text_secondary"],
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side=tk.RIGHT)

        main_paned = tk.PanedWindow(parent, orient=tk.HORIZONTAL, bg=pal["bg"], sashwidth=4, bd=0)
        main_paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        # 左：流程列表（名称 / 版本数 / 最新上传人 / 最新时间）
        left_frame = tk.Frame(
            main_paned,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border"],
            padx=4,
            pady=4,
        )
        left_top = tk.Frame(left_frame, bg=pal["surface"], pady=2)
        left_top.pack(fill=tk.X, padx=4, pady=(2, 4))
        tk.Label(
            left_top,
            text="流程文件 (flow_packages)",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=pal["surface"],
            fg=pal["text"],
        ).pack(side=tk.LEFT)

        columns = ("name", "versions", "lastUser", "lastUploaded")
        self.flows_tree = ttk.Treeview(left_frame, columns=columns, show="headings")
        for col, text, width in (
            ("name", "流程文件", 260),
            ("versions", "版本数", 60),
            ("lastUser", "最新上传人", 90),
            ("lastUploaded", "最新上传时间", 150),
        ):
            self.flows_tree.heading(col, text=text)
            self.flows_tree.column(col, width=width, anchor=tk.W)
        flows_scroll = ttk.Scrollbar(left_frame, command=self.flows_tree.yview)
        self.flows_tree.config(yscrollcommand=flows_scroll.set)
        flows_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.flows_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.flows_tree.bind("<<TreeviewSelect>>", self._on_flow_selected)
        main_paned.add(left_frame, minsize=360, width=480)

        # 右：版本明细 + 关联任务（含项目参数）
        right_frame = tk.Frame(main_paned, bg=pal["bg"])
        versions_frame = tk.Frame(
            right_frame,
            bg=pal["surface"],
            highlightthickness=1,
            highlightbackground=pal["border"],
            padx=6,
            pady=6,
        )
        versions_frame.pack(fill=tk.BOTH, expand=True)

        v_top = tk.Frame(versions_frame, bg=pal["surface"], pady=2)
        v_top.pack(fill=tk.X, padx=2, pady=(2, 4))
        tk.Label(
            v_top,
            text="版本历史（点击查看任务明细）",
            font=("Microsoft YaHei UI", 10, "bold"),
            bg=pal["surface"],
            fg=pal["text"],
        ).pack(side=tk.LEFT)

        v_columns = ("version", "user", "uploadedAt", "sha256", "isCurrent")
        self.flow_versions_tree = ttk.Treeview(
            versions_frame, columns=v_columns, show="headings"
        )
        for col, text, width in (
            ("version", "版本", 50),
            ("user", "上传人", 90),
            ("uploadedAt", "上传时间", 150),
            ("sha256", "内容指纹(sha256 前 12 位)", 130),
            ("isCurrent", "状态", 70),
        ):
            self.flow_versions_tree.heading(col, text=text)
            self.flow_versions_tree.column(col, width=width, anchor=tk.W)
        v_scroll = ttk.Scrollbar(versions_frame, command=self.flow_versions_tree.yview)
        self.flow_versions_tree.config(yscrollcommand=v_scroll.set)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.flow_versions_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.flow_versions_tree.bind("<<TreeviewSelect>>", self._on_flow_version_selected)

        # 回滚操作行：选中历史版本后，一键把该版内容恢复为当前版（当前内容自动归档）
        rollback_bar = tk.Frame(versions_frame, bg=pal["surface"])
        rollback_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 2))
        self.rollback_hint_var = tk.StringVar(
            value="提示：选中上方任一历史版本后点「回滚到此版」；当前版回滚无操作（幂等提示）。"
        )
        tk.Label(
            rollback_bar,
            textvariable=self.rollback_hint_var,
            bg=pal["surface"],
            fg=pal["muted"],
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        wt_theme.create_flat_button(
            rollback_bar,
            text="回滚到此版",
            command=self._rollback_selected_flow_version,
            tone="warning",
            padx=12,
            pady=3,
        ).pack(side=tk.RIGHT)
        main_paned.add(right_frame, minsize=420, width=560)

        # 底部：选中任务的项目参数详情
        detail_frame = wt_theme.create_card_frame(right_frame, padx=8, pady=6)
        detail_frame.pack(fill=tk.X, pady=(6, 0))

        tk.Label(
            detail_frame,
            text="关联任务与项目参数（选中上方任一行后自动展示）",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=pal["card"],
            fg=pal["text"],
        ).pack(anchor="w", pady=(0, 4))

        text_container = tk.Frame(detail_frame, bg=pal["terminal_bg"])
        text_container.pack(fill=tk.X)

        self.flow_detail_text = tk.Text(
            text_container,
            wrap=tk.WORD,
            state=tk.DISABLED,
            height=8,
            bg=pal["terminal_bg"],
            fg=pal["terminal_fg"],
            insertbackground=pal["terminal_fg"],
            font=("Consolas", 9),
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        detail_scroll = tk.Scrollbar(text_container, command=self.flow_detail_text.yview, relief=tk.FLAT)
        self.flow_detail_text.config(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.flow_detail_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def refresh_flows(self):
        """后台拉取服务器流程台账 + 任务列表，供仓库页签展示。"""
        if getattr(self, "_flows_fetching", False):
            return
        self._flows_fetching = True
        threading.Thread(target=self._flows_fetch_worker, daemon=True).start()

    def _flows_fetch_worker(self):
        flows_payload = None
        tasks_payload = None
        error = None
        try:
            flows_payload = self._get_json("/api/flows")
        except Exception as exc:
            error = str(exc)
        try:
            tasks_payload = self._get_json("/api/tasks?limit=200")
        except Exception:
            tasks_payload = None
        self._flows_fetching = False
        if flows_payload is None:
            self._post_ui(
                lambda e=error: self.flows_conn_var.set("加载失败：{}".format(e)[:60])
            )
            return
        self._post_ui(lambda: self._apply_flows_payload(flows_payload, tasks_payload))

    def _apply_flows_payload(self, flows_payload, tasks_payload=None):
        self.flows_conn_var.set("已加载 {} 个流程".format(len(flows_payload.get("flows", []))))
        self._cached_flows = list(flows_payload.get("flows", []))
        self._cached_flow_tasks = list((tasks_payload or {}).get("tasks", []))
        selected = self.flows_tree.selection()
        selected_name = selected[0] if selected else ""
        self.flows_tree.delete(*self.flows_tree.get_children())
        for flow in self._cached_flows:
            versions = flow.get("versions") or []
            latest = versions[-1] if versions else {}
            self.flows_tree.insert(
                "",
                tk.END,
                iid=flow.get("name", ""),
                values=(
                    flow.get("name", ""),
                    len(versions),
                    latest.get("user", ""),
                    latest.get("uploadedAt", ""),
                ),
            )
        if selected_name and self.flows_tree.exists(selected_name):
            self.flows_tree.selection_set(selected_name)
        elif self.flows_tree.get_children():
            # 无既有选择时默认选中第一个流程，右侧即刻显示版本明细
            first = self.flows_tree.get_children()[0]
            self.flows_tree.selection_set(first)
            self._render_flow_versions(first)

    def _on_flow_selected(self, _event=None):
        selection = self.flows_tree.selection()
        if not selection:
            return
        flow_name = selection[0]
        self._render_flow_versions(flow_name)

    def _render_flow_versions(self, flow_name):
        self.flow_versions_tree.delete(*self.flow_versions_tree.get_children())
        self._set_flow_detail_text(
            "流程：{}\n（选中下方任一版本行查看使用该流程的任务与项目参数）".format(flow_name)
        )
        flow = None
        for item in getattr(self, "_cached_flows", []) or []:
            if item.get("name") == flow_name:
                flow = item
                break
        if flow is None:
            return
        current_path = str(flow.get("path") or "")
        versions = flow.get("versions") or []
        if not versions:
            self._set_flow_detail_text(
                "流程：{}\n（未上传过版本：随发布包部署，无上传人/时间记录；"
                "选中下行查看使用该流程的任务）".format(flow_name)
            )
        for idx, version in enumerate(reversed(versions), 1):
            file_path = str(version.get("file") or "")
            is_current = "当前版" if file_path == current_path else ""
            self.flow_versions_tree.insert(
                "",
                tk.END,
                iid="v{}".format(version.get("version", idx)),
                values=(
                    version.get("version", ""),
                    version.get("user", ""),
                    version.get("uploadedAt", ""),
                    str(version.get("sha256", ""))[:12],
                    is_current,
                ),
            )
        if not versions:
            self.flow_versions_tree.insert(
                "", tk.END, values=("(未上传过版本：随发布包部署)", "", "", "", "当前版")
            )

    def _rollback_selected_flow_version(self):
        """把选中的历史版本恢复为当前版（服务端自动归档当前内容，历史不丢）。"""
        flow_selection = self.flows_tree.selection()
        if not flow_selection:
            messagebox.showinfo("流程仓库", "请先在左侧选择流程文件。", parent=self.window)
            return
        flow_name = flow_selection[0]
        version_selection = self.flow_versions_tree.selection()
        if not version_selection:
            messagebox.showinfo(
                "流程仓库", "请先在版本历史中选中要回滚到的版本。", parent=self.window
            )
            return
        version_id = str(version_selection[0]).lstrip("v")
        if not version_id.isdigit():
            messagebox.showinfo(
                "流程仓库", "该行不是可回滚的版本记录。", parent=self.window
            )
            return
        user = self.user_var.get().strip()
        if not user:
            messagebox.showwarning(
                "流程仓库", "回滚需要先在顶部填写用户名（记入版本台账）。", parent=self.window
            )
            return
        # 当前版回滚无意义，但仍允许（服务端会幂等提示"与当前内容一致"）
        confirm = messagebox.askyesno(
            "回滚流程版本",
            "确定把流程\n  {}\n的版本 {} 恢复为当前版吗？\n\n"
            "当前内容会自动归档为新版本，历史不丢失；\n"
            "随后提交的远程任务将使用回滚后的内容。".format(flow_name, version_id),
            parent=self.window,
        )
        if not confirm:
            return
        self.rollback_hint_var.set("正在回滚 {} 到版本 {} ...".format(flow_name, version_id))

        def _worker():
            try:
                resp = self._post_json(
                    "/api/flows/rollback",
                    {"name": flow_name, "version": int(version_id), "user": user},
                )
                if resp.get("rolledBack"):
                    message = "已回滚：{} 的版本 {} 已恢复为当前版（新版本号 {}）。\n后续提交的远程任务将使用回滚后的内容。".format(
                        flow_name, version_id, resp.get("version", "?")
                    )
                else:
                    message = "无需回滚：{} 的版本 {} 与当前内容一致。".format(
                        flow_name, version_id
                    )
                self._post_ui(
                    lambda m=message: (
                        self.rollback_hint_var.set(m.split("\n")[0]),
                        messagebox.showinfo("回滚完成", m, parent=self.window),
                        self.refresh_flows(),
                    )
                )
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                text = "回滚失败：{} {}".format(exc.code, detail or exc.reason)
                self._post_ui(
                    lambda t=text: (
                        self.rollback_hint_var.set(t),
                        messagebox.showerror("回滚失败", t, parent=self.window),
                    )
                )
            except Exception as exc:
                text = "回滚失败：{}".format(exc)
                self._post_ui(
                    lambda t=text: (
                        self.rollback_hint_var.set(t),
                        messagebox.showerror("回滚失败", t, parent=self.window),
                    )
                )

        threading.Thread(target=_worker, daemon=True).start()

    def _on_flow_version_selected(self, _event=None):
        """选中版本行 → 展示使用该流程的最近任务与项目参数。"""
        flow_selection = self.flows_tree.selection()
        if not flow_selection:
            return
        flow_name = flow_selection[0]
        flow = None
        for item in getattr(self, "_cached_flows", []) or []:
            if item.get("name") == flow_name:
                flow = item
                break
        if flow is None:
            return
        # 版本树未建行 iid（无版本流程），用流程名兜底
        flow_path = str(flow.get("path") or "")
        related = []
        for task in getattr(self, "_cached_flow_tasks", []) or []:
            if str(task.get("flowPath") or "") == flow_path:
                related.append(task)
        related = list(reversed(related[-20:]))  # 最近 20 条，新在前
        lines = ["流程：{}".format(flow_name), "服务器路径：{}".format(flow_path), ""]
        if not related:
            lines.append("（近期没有使用该流程的任务记录）")
        else:
            lines.append("最近使用该流程的任务（含提交时携带的项目参数）：")
            lines.append("-" * 60)
            for task in related:
                runtime = task.get("runtimeConfig") or {}
                lines.append(
                    "· {}  [{}]  提交人={}".format(
                        task.get("taskId", ""),
                        STATUS_LABELS.get(task.get("status", ""), task.get("status", "")),
                        task.get("user", ""),
                    )
                )
                lines.append(
                    "  提交时间={}  步骤进度={}/{}".format(
                        task.get("createdAt", ""),
                        task.get("progressCurrent", 0),
                        task.get("progressTotal", 0),
                    )
                )
                if runtime:
                    lines.append("  项目参数（runtimeConfig）：")
                    for key in sorted(runtime):
                        lines.append("    {} = {}".format(key, runtime[key]))
                else:
                    lines.append("  项目参数：无（流程文件自包含或旧版提交）")
                lines.append("")
        self._set_flow_detail_text("\n".join(lines))

    def _set_flow_detail_text(self, text):
        self.flow_detail_text.config(state=tk.NORMAL)
        self.flow_detail_text.delete("1.0", tk.END)
        self.flow_detail_text.insert(tk.END, str(text))
        self.flow_detail_text.config(state=tk.DISABLED)

    def _on_settings_text_changed(self, *_args):
        self._sync_prefs_snapshot()
        self._save_settings()
        # 地址栏实时更新角色横幅（不重连，仅视觉反馈；连接在点"刷新"时生效）
        new_url = self.url_var.get().strip().rstrip("/")
        if new_url and new_url != self.base_url:
            try:
                self._role_preview_url = new_url
                old_base = self.base_url
                self.base_url = new_url
                self._refresh_role_banner()
                self.base_url = old_base
            except Exception:
                pass

    def _on_user_changed(self, *_args):
        if not self.user_var.get().strip() and self.mine_only_var.get():
            self.mine_only_var.set(False)
        self._sync_prefs_snapshot()
        self._save_settings()

    def _sync_prefs_snapshot(self):
        """从 Tk 变量刷新「线程安全快照」（仅主线程调用；worker 线程只读快照）。

        后台线程（轮询/提交/上传/控制）不得直接读取 Tk 变量 —— 跨线程读
        Tcl 对象在部分环境会偶发 "main thread is not in main loop" 或原生崩溃。
        快照随用户名/令牌/「只看我的」的变化由 trace 回调自动刷新。
        """
        user_var = getattr(self, "user_var", None)
        if user_var is not None:
            self._prefs_snapshot["user"] = user_var.get().strip()
        token_var = getattr(self, "token_var", None)
        if token_var is not None:
            self._prefs_snapshot["token"] = token_var.get().strip()
        mine_only_var = getattr(self, "mine_only_var", None)
        if mine_only_var is not None:
            self._prefs_snapshot["mine_only"] = bool(mine_only_var.get())

    def _on_monitor_url_changed(self, *_args):
        url = self.monitor_url_var.get().strip().rstrip("/")
        if not url:
            return
        self.monitor_base_url = url
        if self.on_monitor_url_change:
            try:
                self.on_monitor_url_change(url)
            except Exception:
                pass

    def _apply_settings_and_refresh(self):
        url = self.url_var.get().strip().rstrip("/")
        if url:
            if not url.startswith("http://") and not url.startswith("https://"):
                url = "http://" + url
                self.url_var.set(url)
            self.base_url = url
            self._http_conn = None
            self._record_history_url(url)
        monitor_url = self.monitor_url_var.get().strip().rstrip("/")
        if not monitor_url and url:
            monitor_url = _derive_monitor_url(url)
            self.monitor_url_var.set(monitor_url)
        elif monitor_url and not monitor_url.startswith("http://") and not monitor_url.startswith("https://"):
            monitor_url = "http://" + monitor_url
            self.monitor_url_var.set(monitor_url)
        if monitor_url:
            self.monitor_base_url = monitor_url
        self._sync_prefs_snapshot()
        self._save_settings()
        self._refresh_role_banner()
        self.refresh()
        self.refresh_monitor()
        self.refresh_flows()

    def _save_settings(self):
        if self.on_settings_change:
            try:
                self.on_settings_change(
                    self.base_url,
                    self.user_var.get().strip(),
                    self.token_var.get().strip(),
                )
            except Exception:
                pass

    def refresh(self):
        if self._fetching:
            return
        self._fetching = True
        threading.Thread(target=self._fetch_worker, daemon=True).start()

    def refresh_monitor(self):
        if self._monitor_fetching:
            return
        self._monitor_fetching = True
        threading.Thread(target=self._monitor_fetch_worker, daemon=True).start()

    def _on_mine_only_toggle(self):
        if self.mine_only_var.get() and not self.user_var.get().strip():
            self.mine_only_var.set(False)
            messagebox.showwarning(
                "任务队列",
                "“只看我的任务”需要先填写用户名；已自动取消勾选。",
                parent=self.window,
            )
            return
        self._sync_prefs_snapshot()
        self._save_settings()
        self.refresh()

    def _task_list_path(self):
        # 由轮询 worker 线程调用，只能读线程安全快照，不得触碰 Tk 变量。
        if not self._prefs_snapshot["mine_only"]:
            return "/api/tasks?scope=all"
        user = self._prefs_snapshot["user"]
        if not user:
            return None
        return "/api/tasks?scope=mine&user={}".format(urllib.parse.quote(user))

    def _fetch_worker(self):
        path = self._task_list_path()
        if path is None:
            self._fetching = False
            return
        try:
            tasks_payload = self._get_json(path)
        except Exception as exc:
            self._post_ui(lambda: self._mark_offline(str(exc)))
            self._fetching = False
            return
        stats_payload = None
        try:
            stats_payload = self._get_json("/api/queue/stats")
        except Exception:
            stats_payload = None
        self._post_ui(
            lambda: self._apply_tasks(
                tasks_payload.get("tasks", []),
                stats_payload,
            )
        )
        self._fetching = False

    def _monitor_fetch_worker(self):
        t0 = time.perf_counter()
        try:
            status_payload = self._get_json_monitor("/api/status")
            ping_ms = int(max(0, (time.perf_counter() - t0) * 1000))
            logs_payload = self._get_json_monitor("/api/logs?tail=300")
        except Exception as exc:
            self._post_ui(lambda e=exc: self._mark_monitor_offline(str(e)))
            self._monitor_fetching = False
            return
        self._post_ui(
            lambda: self._apply_monitor_payload(status_payload, logs_payload, ping_ms=ping_ms)
        )
        self._monitor_fetching = False

    def _apply_tasks(self, tasks, stats=None):
        self.conn_var.set("已连接")
        self._queue_fail_streak = 0
        self._hide_diag_banner()
        self._cached_tasks = list(tasks)
        self._apply_stats(stats)
        self._update_queue_dashboard(self._cached_tasks, stats)
        self._render_task_list(
            filter_tasks(
                self._cached_tasks,
                self.status_filter_var.get(),
                self.keyword_var.get(),
            )
        )
        self._maybe_refresh_selected_logs(tasks)

    def _maybe_refresh_selected_logs(self, tasks):
        """运行中任务的日志随轮询自动刷新（每轮最多一次，且防并发重复拉取）。

        手动选中已由 _on_task_select 拉取；这里仅当当前选中的任务处于 running
        且没有其他日志拉取线程在途时，追加一次拉取，让"远程看日志"跟随进度更新。
        """
        task_id = self._selected_task_id()
        if not task_id or getattr(self, "_log_fetching", False):
            return
        task = next(
            (t for t in (tasks or []) if str(t.get("taskId", "")) == task_id),
            None,
        )
        if task is None or str(task.get("status", "")) != "running":
            return
        self._log_fetching = True
        threading.Thread(
            target=self._fetch_logs_worker, args=(task_id,), daemon=True
        ).start()

    def _on_filter_changed(self, _event=None):
        self._render_task_list(
            filter_tasks(
                getattr(self, "_cached_tasks", []),
                self.status_filter_var.get(),
                self.keyword_var.get(),
            )
        )

    @staticmethod
    def _task_row_values(task):
        """任务 → 树行显示值（纯函数，供增量 diff 复用）。"""
        progress_total = int(task.get("progressTotal") or 0)
        progress_current = int(task.get("progressCurrent") or 0)
        progress_percent = float(task.get("progressPercent") or 0.0)
        if progress_total > 0:
            progress_text = "{}/{} ({:.0f}%)".format(
                progress_current,
                progress_total,
                progress_percent,
            )
        else:
            progress_text = "-"
        return (
            task.get("taskId", ""),
            task.get("user", ""),
            STATUS_LABELS.get(task.get("status", ""), task.get("status", "")),
            task.get("priority", 0),
            task.get("scheduledAt") or "-",
            "{}/{}".format(
                task.get("attempts", 1),
                task.get("maxAttempts", 1),
            ),
            progress_text,
            task.get("currentStepName") or task.get("currentStepId") or "-",
            task.get("createdAt", ""),
            task.get("updatedAt", ""),
        )

    def _render_task_list(self, tasks):
        # 增量更新：每轮轮询只对"值变化"的行 itemconfigure、新增行 insert、
        # 消失行 delete，避免 2s 一次全量 delete+insert 造成滚动跳动和整帧重绘
        # （全量重绘正是拖动滚动条/下拉框时出现尾影的主要放大器）。
        selected = self._selected_task_id()
        existing = self.task_tree.get_children()
        existing_set = set(existing)
        incoming_ids = []
        incoming_map = {}
        for task in tasks:
            task_id = str(task.get("taskId", ""))
            if task_id:
                incoming_ids.append(task_id)
                incoming_map[task_id] = self._task_row_values(task)
        # 1) 删掉不再出现的行
        for iid in existing:
            if iid not in incoming_map:
                self.task_tree.delete(iid)
        # 2) 更新值有变化的行 / 插入新行（保持服务器返回顺序）
        for iid in incoming_ids:
            values = incoming_map[iid]
            if iid in existing_set:
                try:
                    # Tk 返回字符串列表，与 _task_row_values 生成的值统一成
                    # 字符串比较（priority 等数字经 Tk 往返后变成字符串）
                    shown = [str(v) for v in (self.task_tree.set(iid) or {}).values()]
                    if shown != [str(v) for v in values]:
                        self.task_tree.item(iid, values=values)
                except tk.TclError:
                    # 行在对比与写入之间被并发删除（理论少见）：补插到末尾
                    self.task_tree.insert("", tk.END, iid=iid, values=values)
            else:
                try:
                    self.task_tree.insert("", tk.END, iid=iid, values=values)
                except tk.TclError:
                    # iid 已存在（过滤条件切换的中间态）：原地更新
                    self.task_tree.item(iid, values=values)
        if selected and self.task_tree.exists(selected):
            # 只有当选择真的丢失时才恢复，避免每轮 selection_set 打断用户操作
            if selected not in self.task_tree.selection():
                self.task_tree.selection_set(selected)
                self.task_tree.see(selected)
        # 列表刷新后所选任务状态可能已变化（如 pending→running），同步按钮可用性
        self._update_control_buttons()

    def _apply_monitor_payload(self, status_payload, logs_payload, ping_ms=None):
        pal = wt_theme.get_palette()
        self.monitor_conn_var.set("已连接")
        self._monitor_fail_streak = 0
        if ping_ms is not None:
            if hasattr(self, "monitor_ping_var"):
                self.monitor_ping_var.set(f"延迟: {ping_ms} ms")
            tone = "success" if ping_ms < 60 else ("warning" if ping_ms < 300 else "danger")
            if hasattr(self, "monitor_ping_badge") and hasattr(self.monitor_ping_badge, "set_badge"):
                self.monitor_ping_badge.set_badge(f"⏱️ {ping_ms} ms", tone)

        status = str(status_payload.get("status", "unknown"))
        status_text = MONITOR_STATUS_LABELS.get(status, status)
        self.run_status_var.set(status_text)
        if hasattr(self, "monitor_status_lbl"):
            st_color = pal["success_text"] if status in ("idle", "success") else (
                pal["primary_text"] if status == "running" else (
                    pal["danger_text"] if status == "failed" else pal["text"]
                )
            )
            self.monitor_status_lbl.configure(fg=st_color)

        self.activity_var.set(str(status_payload.get("activity") or "-"))
        raw_err = str(status_payload.get("error") or "").strip()
        self._raw_monitor_error = raw_err
        if raw_err and raw_err != "-":
            clean_err = self._friendly_error(raw_err)
            self.error_var.set(clean_err)
            if hasattr(self, "monitor_health_var"):
                self.monitor_health_var.set("⚠️ 存在异常")
            if hasattr(self, "monitor_health_lbl"):
                self.monitor_health_lbl.configure(fg=pal["danger_text"])
        else:
            self.error_var.set("无异常")
            if hasattr(self, "monitor_health_var"):
                self.monitor_health_var.set("✓ 运行正常")
            if hasattr(self, "monitor_health_lbl"):
                self.monitor_health_lbl.configure(fg=pal["success_text"])

        self.updated_var.set(str(status_payload.get("updatedAt") or "-"))
        if hasattr(self, "monitor_heartbeat_var"):
            self.monitor_heartbeat_var.set("实时同步正常")

        lines = logs_payload.get("lines", [])
        self._cached_monitor_logs = list(lines)
        self._render_monitor_logs(lines)

    def _apply_stats(self, stats):
        self._update_queue_dashboard(getattr(self, "_cached_tasks", []), stats)
        if not stats:
            self.stats_var.set("统计不可用")
            return
        by_status = stats.get("byStatus") or {}
        rate = stats.get("successRateLast24h")
        if isinstance(rate, (int, float)):
            rate_text = "{:.1f}%".format(float(rate) * 100)
        else:
            rate_text = "-"
        wait = stats.get("averageWaitSeconds")
        run = stats.get("averageRunSeconds")
        wait_text = "{:.1f}s".format(float(wait)) if isinstance(wait, (int, float)) else "-"
        run_text = "{:.1f}s".format(float(run)) if isinstance(run, (int, float)) else "-"
        self.stats_var.set(
            "排队 {pending} | 运行 {running} | 暂停 {paused} | "
            "成功 {success} | 失败 {failed} | 今日提交 {submitted} | "
            "今日完成 {completed} | 24h成功率 {rate} | "
            "平均等待 {wait} | 平均运行 {run}".format(
                pending=by_status.get("pending", 0),
                running=by_status.get("running", 0),
                paused=by_status.get("paused", 0),
                success=by_status.get("success", 0),
                failed=by_status.get("failed", 0),
                submitted=stats.get("todaySubmitted", 0),
                completed=stats.get("todayCompleted", 0),
                rate=rate_text,
                wait=wait_text,
                run=run_text,
            )
        )

    def _update_queue_dashboard(self, tasks=None, stats=None):
        """更新全局队列看板的实时指标徽章"""
        tasks = tasks if tasks is not None else getattr(self, "_cached_tasks", [])
        if not hasattr(self, "stat_badge_total") or self.stat_badge_total is None:
            return

        if stats and isinstance(stats, dict) and stats.get("byStatus"):
            by_status = stats["byStatus"]
            total = sum(by_status.values())
            pending = by_status.get("pending", 0)
            running = by_status.get("running", 0)
            success = by_status.get("success", 0)
            failed = by_status.get("failed", 0) + by_status.get("terminated", 0)
        else:
            total = len(tasks)
            pending = sum(1 for t in tasks if str(t.get("status", "")).lower() in ("pending", "queued", "waiting"))
            running = sum(1 for t in tasks if str(t.get("status", "")).lower() == "running")
            success = sum(1 for t in tasks if str(t.get("status", "")).lower() == "success")
            failed = sum(1 for t in tasks if str(t.get("status", "")).lower() in ("failed", "terminated", "error"))

        try:
            self.stat_badge_total.set_badge("总计 {}".format(total), "muted")
            self.stat_badge_pending.set_badge("排队中 {}".format(pending), "info" if pending > 0 else "muted")
            self.stat_badge_running.set_badge("运行中 {}".format(running), "primary" if running > 0 else "muted")
            self.stat_badge_success.set_badge("成功 {}".format(success), "success" if success > 0 else "muted")
            self.stat_badge_failed.set_badge("失败 {}".format(failed), "danger" if failed > 0 else "muted")
        except Exception:
            pass

    def _selected_task_id(self):
        # 窗口构建早期（task_tree 尚未创建）或已销毁时返回空串，
        # 避免 _build_queue_tab 内的初始置灰调用触发 AttributeError 中断构建
        task_tree = getattr(self, "task_tree", None)
        if task_tree is None:
            return ""
        try:
            selection = task_tree.selection()
        except tk.TclError:
            return ""
        return selection[0] if selection else ""

    def _selected_task(self):
        """返回当前选中任务的完整 dict（从最近一轮任务列表缓存中取）。"""
        task_id = self._selected_task_id()
        if not task_id:
            return None
        for task in getattr(self, "_cached_tasks", []) or []:
            if task.get("taskId") == task_id:
                return task
        return None

    def _update_task_detail_card(self, task=None):
        """更新右侧看板任务卡片信息与进度条（防无头测试属性缺失）。"""
        if not hasattr(self, "detail_task_id_var"):
            return
        if not task:
            self.detail_task_id_var.set("未选中任务")
            if hasattr(self, "detail_status_badge") and hasattr(self.detail_status_badge, "set_badge"):
                self.detail_status_badge.set_badge("未选择", "muted")
            if hasattr(self, "detail_meta_var"):
                self.detail_meta_var.set("请在左侧列表中点击选择任务查看详情与操作")
            if hasattr(self, "detail_step_var"):
                self.detail_step_var.set("当前步骤：-")
            if hasattr(self, "detail_progress_var"):
                self.detail_progress_var.set("步骤进度：-")
            if hasattr(self, "detail_progressbar"):
                try:
                    self.detail_progressbar["value"] = 0
                    self.detail_progressbar["maximum"] = 100
                except Exception:
                    pass
            return

        task_id = str(task.get("taskId") or "-")
        self.detail_task_id_var.set("任务: {}".format(task_id))
        raw_status = str(task.get("status") or "unknown")
        status_label = STATUS_LABELS.get(raw_status, raw_status)
        tone_map = {
            "pending": "warning",
            "running": "primary",
            "paused": "warning",
            "success": "success",
            "failed": "danger",
            "canceled": "muted",
            "terminated": "danger",
        }
        status_tone = tone_map.get(raw_status, "muted")
        if hasattr(self, "detail_status_badge") and hasattr(self.detail_status_badge, "set_badge"):
            self.detail_status_badge.set_badge(status_label, status_tone)

        user = str(task.get("user") or "-")
        prio = str(task.get("priority", 0))
        sched = str(task.get("scheduledAt") or "-")
        created = str(task.get("createdAt") or "-")
        if hasattr(self, "detail_meta_var"):
            self.detail_meta_var.set(
                "提交人: {}  |  优先级: {}  |  预约执行: {}  |  创建: {}".format(
                    user, prio, sched, created
                )
            )

        step_name = str(task.get("currentStepName") or "-")
        step_idx = task.get("currentStepIndex")
        total_steps = task.get("totalSteps")

        if step_idx is not None and total_steps:
            try:
                curr_i = int(step_idx)
                tot_s = int(total_steps)
                pct = (curr_i / tot_s) * 100.0 if tot_s > 0 else 0
                if hasattr(self, "detail_step_var"):
                    self.detail_step_var.set("当前步骤 ({}/{}): {}".format(curr_i, tot_s, step_name))
                if hasattr(self, "detail_progress_var"):
                    self.detail_progress_var.set("{}/{} ({:.0f}%)".format(curr_i, tot_s, pct))
                if hasattr(self, "detail_progressbar"):
                    self.detail_progressbar["maximum"] = tot_s
                    self.detail_progressbar["value"] = curr_i
            except (ValueError, TypeError):
                if hasattr(self, "detail_step_var"):
                    self.detail_step_var.set("当前步骤: {}".format(step_name))
        elif raw_status == "success":
            if hasattr(self, "detail_step_var"):
                self.detail_step_var.set("执行完成")
            if hasattr(self, "detail_progress_var"):
                self.detail_progress_var.set("100%")
            if hasattr(self, "detail_progressbar"):
                try:
                    self.detail_progressbar["maximum"] = 100
                    self.detail_progressbar["value"] = 100
                except Exception:
                    pass
        else:
            if hasattr(self, "detail_step_var"):
                self.detail_step_var.set("当前步骤: {}".format(step_name))
            if hasattr(self, "detail_progress_var"):
                self.detail_progress_var.set("步骤进度: -")
            if hasattr(self, "detail_progressbar"):
                try:
                    self.detail_progressbar["maximum"] = 100
                    self.detail_progressbar["value"] = 0
                except Exception:
                    pass

        if hasattr(self, "detail_eta_var"):
            eta = task.get("eta") or {}
            eta_fmt = eta.get("etaFormatted") or ""
            elapsed = eta.get("elapsedSeconds")
            elapsed_fmt = "{}秒".format(elapsed) if elapsed is not None else ""
            if elapsed is not None and elapsed >= 60:
                elapsed_fmt = "{:02d}分{:02d}秒".format(elapsed // 60, elapsed % 60)

            if raw_status == "running":
                if eta_fmt and elapsed_fmt:
                    self.detail_eta_var.set("⏱ 耗时与预估: 已耗时 {}  |  预计剩余 {}".format(elapsed_fmt, eta_fmt))
                elif elapsed_fmt:
                    self.detail_eta_var.set("⏱ 耗时: 已运行 {}".format(elapsed_fmt))
                else:
                    self.detail_eta_var.set("⏱ 耗时与预估: 运行中...")
            elif raw_status in ("success", "completed"):
                if elapsed_fmt:
                    self.detail_eta_var.set("⏱ 总耗时: {}".format(elapsed_fmt))
                else:
                    self.detail_eta_var.set("⏱ 状态: 执行完成")
            elif raw_status in ("failed", "terminated", "canceled", "cancelled"):
                if elapsed_fmt:
                    self.detail_eta_var.set("⏱ 运行耗时: {} (已终止)".format(elapsed_fmt))
                else:
                    self.detail_eta_var.set("⏱ 状态: 已终止")
            elif raw_status == "pending":
                self.detail_eta_var.set("⏱ 状态: 排队等待调度中")
            elif raw_status == "paused":
                self.detail_eta_var.set("⏱ 状态: 已暂停")
            else:
                self.detail_eta_var.set("")

    def _copy_log_text(self):
        """复制当前终端控制台日志到系统剪贴板。"""
        log_text = getattr(self, "log_text", None)
        if not log_text:
            return
        try:
            content = log_text.get("1.0", tk.END).strip()
            if not content:
                return
            self.window.clipboard_clear()
            self.window.clipboard_append(content)
        except Exception:
            pass

    def _clear_log_text(self):
        """清空终端控制台已显示日志，并重置增量行数计数器。"""
        log_text = getattr(self, "log_text", None)
        if not log_text:
            return
        try:
            prev_state = log_text.cget("state")
            log_text.config(state=tk.NORMAL)
            log_text.delete("1.0", tk.END)
            log_text.config(state=prev_state)
            self._log_rendered_lines = 0
        except Exception:
            pass

    def _update_control_buttons(self):
        """按所选任务状态启用/禁用控制按钮，规则与服务端 API 状态机一致：

        - 暂停：pending / running
        - 继续：paused / failed / terminated
        - 终止：running
        - 取消：pending
        - 删除：除 running 外（running 须先终止）
        未选中任务时全部禁用。UI 只是提前拦截，服务端仍做权威校验。
        """
        task = self._selected_task()
        self._update_task_detail_card(task)
        buttons = getattr(self, "_control_buttons", None)
        if not buttons:
            return
        task = self._selected_task()
        status = (task or {}).get("status", "")
        allowed = {
            "pause": status in ("pending", "running"),
            "resume": status in ("paused", "failed", "terminated"),
            "terminate": status == "running",
            "cancel": status == "pending",
            "delete": bool(status) and status != "running",
        }
        for action, enabled in allowed.items():
            btn = buttons.get(action)
            if btn is not None:
                try:
                    btn.config(state=tk.NORMAL if enabled else tk.DISABLED)
                except tk.TclError:
                    pass

    def _on_task_select(self, _event=None):
        task_id = self._selected_task_id()
        self._update_control_buttons()
        if task_id:
            if task_id != getattr(self, "_last_selected_task_id", None):
                self._last_selected_task_id = task_id
                self._clear_log_text()
            self._start_task_sse_stream(task_id)

    def _start_task_sse_stream(self, task_id):
        """启动选中任务的 SSE 实时事件流监听；若不可用则自动无感降级至轮询。"""
        if getattr(self, "_sse_stop_event", None) is not None:
            self._sse_stop_event.set()
        stop_event = threading.Event()
        self._sse_stop_event = stop_event
        self._active_stream_task_id = task_id
        threading.Thread(
            target=self._sse_stream_worker,
            args=(task_id, stop_event),
            daemon=True,
        ).start()

    def _sse_stream_worker(self, task_id, stop_event):
        """后台长连接读取 SSE 事件流，网络断开或不支持时自动回退到 _fetch_logs_worker。"""
        base_url = self._server_url()
        if not base_url:
            self._fetch_logs_worker(task_id)
            return

        url = "{}/api/tasks/{}/events".format(base_url.rstrip("/"), urllib.parse.quote(task_id))
        token = self._auth_token()
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", "Bearer {}".format(token))

        resp = None
        try:
            resp = urllib.request.urlopen(req, timeout=25)
            content_type = str(resp.headers.get("Content-Type", ""))
            if resp.status != 200 or "text/event-stream" not in content_type:
                if not stop_event.is_set():
                    self._fetch_logs_worker(task_id)
                return

            event_name = "message"
            for raw_line in resp:
                if stop_event.is_set() or self._closing:
                    break
                line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line or line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[len("event:"):].strip()
                    continue
                if line.startswith("data:"):
                    data_str = line[len("data:"):].strip()
                    try:
                        payload = json.loads(data_str)
                    except Exception:
                        continue
                    if event_name == "log":
                        lines = payload.get("lines", [])
                        is_initial = payload.get("isInitial", False)
                        if lines:
                            if is_initial:
                                self._post_ui(lambda l=lines: self._append_lines_incremental(
                                    self.log_text, l, "_log_rendered_lines"
                                ))
                            else:
                                self._post_ui(lambda l=lines: self._append_stream_lines(
                                    self.log_text, l, "_log_rendered_lines"
                                ))
                    elif event_name == "status":
                        task = payload.get("task")
                        if task and getattr(self, "_active_stream_task_id", None) == task_id:
                            self._post_ui(lambda t=task: self._update_task_detail_card(t))
                    elif event_name == "end":
                        break
        except Exception:
            if not stop_event.is_set() and not self._closing:
                self._fetch_logs_worker(task_id)
        finally:
            if resp:
                try:
                    resp.close()
                except Exception:
                    pass

    def _fetch_logs_worker(self, task_id):
        try:
            try:
                payload = self._get_json(
                    "/api/tasks/{}/logs?tail=300".format(urllib.parse.quote(task_id))
                )
            except Exception as exc:
                self._post_ui(
                    lambda: self._render_logs(["[日志获取失败] {}".format(exc)])
                )
                return
            self._post_ui(lambda: self._render_logs(payload.get("lines", [])))
        finally:
            # 无论成功/失败都释放防并发守卫，供下一轮自动刷新复用
            self._log_fetching = False

    # 日志面板增量渲染参数：行数上限（超过从头 trim），与服务器 /api/logs?tail 的
    # 300 行对齐再留余量；_render_*_logs 在轮询间只 append 新行，不再每 2s 全量
    # delete+insert（全量重绘会让日志区在用户拖动/选择时闪一整帧）。
    _LOG_MAX_LINES = 400

    def _render_logs(self, lines):
        self._append_lines_incremental(
            self.log_text, lines, "_log_rendered_lines"
        )

    def _render_monitor_logs(self, lines):
        query = (getattr(self, "monitor_search_var", None) and self.monitor_search_var.get() or "").strip().lower()
        level_filter = (getattr(self, "monitor_level_var", None) and self.monitor_level_var.get() or "全部").strip()
        is_filtered = bool(query or level_filter != "全部")

        if not is_filtered:
            self._append_lines_incremental(
                self.monitor_log_text, lines, "_monitor_log_rendered_lines"
            )
            return

        filtered = []
        for line in lines:
            s_line = str(line)
            if query and query not in s_line.lower():
                continue
            if level_filter == "仅错误":
                if self._classify_line(s_line) != "error":
                    continue
            elif level_filter == "仅警告":
                if self._classify_line(s_line) not in ("warning", "error"):
                    continue
            elif level_filter == "仅信息":
                if self._classify_line(s_line) not in ("info", "success", "system"):
                    continue
            filtered.append(s_line)

        self._monitor_log_rendered_lines = None
        self.monitor_log_text.config(state=tk.NORMAL)
        self.monitor_log_text.delete("1.0", tk.END)
        for line in filtered:
            self.monitor_log_text.insert(tk.END, line + "\n", self._classify_line(line))
        self.monitor_log_text.config(state=tk.DISABLED)

        if not (hasattr(self, "monitor_pause_scroll_var") and self.monitor_pause_scroll_var.get()):
            self.monitor_log_text.see(tk.END)

    def _on_monitor_log_filter_changed(self):
        lines = getattr(self, "_cached_monitor_logs", [])
        self._render_monitor_logs(lines)

    def _copy_monitor_visible_logs(self):
        text = self.monitor_log_text.get("1.0", "end-1c")
        if not text:
            return
        self.window.clipboard_clear()
        self.window.clipboard_append(text)

    def _copy_monitor_error_detail(self):
        err = getattr(self, "_raw_monitor_error", "") or self.error_var.get()
        if err and err != "-":
            self.window.clipboard_clear()
            self.window.clipboard_append(err)

    def _clear_monitor_log_view(self):
        self._cached_monitor_logs = []
        self._monitor_log_rendered_lines = 0
        self.monitor_log_text.config(state=tk.NORMAL)
        self.monitor_log_text.delete("1.0", tk.END)
        self.monitor_log_text.config(state=tk.DISABLED)

    @staticmethod
    def _detect_log_shift(rendered_lines, incoming_lines):
        """判断等长滚动窗口相对已渲染内容「整体前移」了多少行。

        服务器返回固定长度快照（`/api/logs?tail=300`）时，日志持续推进但行数始终
        等于 tail 上限：内容整体前移若干行、行数却不变。只比行数会永久零绘制，
        只比末行则在末行内容重复时漏检（重复的进度/步骤提示很常见）。

        :return: 0 = 内容完全一致（无需绘制）；k>0 = 应丢弃首部 k 行并追加尾部 k 行
                 （k 等于行数时等价于整体替换）；None = 行数不同，无法用前移解释
        """
        size = len(rendered_lines)
        if size != len(incoming_lines):
            return None
        for shift in range(0, size + 1):
            if rendered_lines[shift:] == incoming_lines[:size - shift]:
                return shift
        return None

    def _append_lines_incremental(self, widget, lines, state_attr):
        """把服务器 tail 返回的日志行增量应用到 Text 控件。

        服务器每轮返回"最后 N 行"快照。与上轮已渲染内容对比：
          - 完全一致（常见稳态）：完全跳过，本轮零绘制；
          - 行数增加（追加型）：只 insert 末尾新增行；
          - 行数相同但内容整体前移（tail 饱和后的滚动窗口）：只丢弃首部若干行、
            追加尾部同样多行 —— 保持增量、不再全量重绘（全量重绘会让日志区在
            用户拖动/选择时闪一整帧）；
          - 行数变少（轮转/换任务）：全量重绘兜底。
        行数超过 _LOG_MAX_LINES 时从头 trim，保证 Text 不无限增长。
        """
        text_lines = [str(l) for l in lines]

        def rendered_count():
            # Tk 语义：内容 "a\nb\n" 的 end-1c 在第 3 行行首，真实行数需减 1；
            # 空控件（无内容）时 end-1c 为 "1.0"，减 1 会得 0，用 max 保护
            row = int(widget.index("end-1c").split(".")[0] or 0)
            return max(0, row - 1)

        def normalized(values):
            # 逐行比较时忽略行尾 CR/LF 差异（服务器返回可能带 \r）
            return [str(v).rstrip("\r\n") for v in values]

        need_full = getattr(self, state_attr, None) is None
        shift = 0
        if not need_full and rendered_count() > len(text_lines):
            # 快照比已渲染行数还短：服务器日志轮转/换任务，全量重绘兜底
            need_full = True
        if not need_full and text_lines:
            current = rendered_count()
            if current > 0 and len(text_lines) == current:
                rendered_lines = widget.get("1.0", "end-1c").split("\n")
                detected = self._detect_log_shift(
                    normalized(rendered_lines), normalized(text_lines))
                if detected is None:
                    need_full = True
                else:
                    shift = detected
        widget.config(state=tk.NORMAL)
        if need_full:
            widget.delete("1.0", tk.END)
            for line in text_lines:
                widget.insert(tk.END, line + "\n", self._classify_line(line))
        else:
            current = rendered_count()
            if len(text_lines) > current:
                for line in text_lines[current:]:
                    widget.insert(tk.END, line + "\n", self._classify_line(line))
            elif shift > 0:
                # 等长滚动：丢首部 shift 行 + 追加尾部 shift 行（O(shift)，避免全量重绘）
                widget.delete("1.0", "%d.0" % (shift + 1))
                for line in text_lines[len(text_lines) - shift:]:
                    widget.insert(tk.END, line + "\n", self._classify_line(line))
        # 头部 trim：超过上限时删除最早的 (total-上限) 行；
        # Tk 行号从 1 计，delete("1.0", "N.0") 删的是第 1..N-1 行，
        # 所以要删到第 (多余行数+1).0 才能恰好剩 _LOG_MAX_LINES 行
        total = rendered_count()
        excess = total - self._LOG_MAX_LINES
        if excess > 0:
            widget.delete("1.0", "%d.0" % (excess + 1))
        widget.config(state=tk.DISABLED)
        if not (widget == getattr(self, "monitor_log_text", None) and getattr(self, "monitor_pause_scroll_var", None) and self.monitor_pause_scroll_var.get()):
            widget.see(tk.END)
        setattr(self, state_attr, len(text_lines))

    def _append_stream_lines(self, widget, delta_lines, state_attr):
        """将 SSE 流式推送的增量日志行直接追加到 Text 控件末尾并自动滚动。"""
        if not delta_lines:
            return
        widget.config(state=tk.NORMAL)
        for line in delta_lines:
            widget.insert(tk.END, str(line) + "\n", self._classify_line(str(line)))

        # 头部 trim：超过上限时删除最早的多余行
        row = int(widget.index("end-1c").split(".")[0] or 0)
        total = max(0, row - 1)
        excess = total - self._LOG_MAX_LINES
        if excess > 0:
            widget.delete("1.0", "%d.0" % (excess + 1))
            total = self._LOG_MAX_LINES

        widget.config(state=tk.DISABLED)
        widget.see(tk.END)
        setattr(self, state_attr, total)

    @staticmethod
    def _classify_line(line):
        # 统一委托 wt_logging：优先读写入方显式级别，缺失时关键字兜底。
        return wt_logging.tag_for_line(line)

    @staticmethod
    def _friendly_error(raw):
        text = str(raw)
        lowered = text.lower()
        if "10061" in text or "connectionrefused" in lowered or "connection refused" in lowered:
            return (
                "无法连接：任务队列/监控服务未启动或地址错误。"
                "请先点击“启动任务队列服务”/“启动监控服务”，或检查任务服务地址与监控地址。"
            )
        if "10060" in text or "timed out" in lowered or "timeout" in lowered:
            return "连接超时：服务未响应，请确认服务已启动且网络可达。"
        if "401" in text or "unauthorized" in lowered:
            return "认证失败：请填写正确的用户名与服务令牌，然后点击刷新。"
        if "403" in text or "forbidden" in lowered:
            return "无权限：令牌有效但当前用户无权执行该操作。"
        if "404" in text or "not found" in lowered:
            return "接口不存在：请检查服务版本与地址是否匹配。"
        if "name or service not known" in lowered or "getaddrinfo" in lowered:
            return "无法解析服务器地址，请检查任务服务地址和监控地址。"
        return text

    def _mark_offline(self, message):
        self._queue_fail_streak += 1
        friendly = self._friendly_error(message)
        self.conn_var.set("未连接：{}".format(friendly))
        self._show_diag_banner(message, is_queue=True)

    def _mark_monitor_offline(self, message):
        pal = wt_theme.get_palette()
        self._monitor_fail_streak += 1
        friendly = self._friendly_error(message)
        self._raw_monitor_error = message
        self.monitor_conn_var.set("未连接：{}".format(friendly))
        if hasattr(self, "monitor_ping_var"):
            self.monitor_ping_var.set("服务离线")
        if hasattr(self, "monitor_ping_badge") and hasattr(self.monitor_ping_badge, "set_badge"):
            self.monitor_ping_badge.set_badge("⏱️ 离线", "danger")
        self.run_status_var.set("未知")
        if hasattr(self, "monitor_status_lbl"):
            self.monitor_status_lbl.configure(fg=pal["muted"])
        self.activity_var.set("-")
        self.error_var.set(friendly)
        if hasattr(self, "monitor_health_var"):
            self.monitor_health_var.set("⚠️ 服务离线")
        if hasattr(self, "monitor_health_lbl"):
            self.monitor_health_lbl.configure(fg=pal["danger_text"])
        self.updated_var.set("-")
        if hasattr(self, "monitor_heartbeat_var"):
            self.monitor_heartbeat_var.set("等待服务启动")
        if getattr(self, "_queue_fail_streak", 0) > 0 or self._monitor_fail_streak >= 2:
            self._show_diag_banner(message, is_queue=False)

    def _post_ui(self, callback):
        try:
            self.window.after(0, callback)
        except Exception:
            pass

    def _make_conn(self, host, port, use_https):
        if use_https:
            return http.client.HTTPSConnection(host, port, timeout=6)
        return http.client.HTTPConnection(host, port, timeout=6)

    def _queue_request(self, method, path, body=None):
        """通过复用的 HTTP 连接访问任务队列服务（配合服务端 HTTP/1.1 keep-alive）。

        - 复用单条 TCP 连接，把轮询从“每次新建连接”降为“连接建立一次、多次复用”；
        - 界面线程与轮询线程并发访问时由 _http_lock 串行化，同一时刻只有一条请求
          在连接上飞行；
        - 连接失效（服务端关闭 / 超时）时丢弃重建，但**仅对幂等方法
          （GET/HEAD/OPTIONS）重试一次**：POST 等非幂等操作（提交任务、上传流程、
          暂停/终止/取消）若在响应返回前断连，服务端可能已执行，重试会造成重复提交
          或重复控制，因此直接抛错，交由上层提示用户。
        """
        base = urllib.parse.urlparse(self.base_url)
        host = base.hostname or "127.0.0.1"
        port = base.port or (443 if base.scheme == "https" else 80)
        use_https = base.scheme == "https"
        full = self.base_url + path
        parsed_full = urllib.parse.urlparse(full)
        target = parsed_full.path + ("?" + parsed_full.query if parsed_full.query else "")
        # 本方法在 worker 线程执行：令牌只读线程安全快照，不得读 Tk 变量
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self._prefs_snapshot["token"],
        }
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        idempotent = method in ("GET", "HEAD", "OPTIONS")
        with self._http_lock:
            for attempt in range(2 if idempotent else 1):
                conn = self._http_conn
                if conn is None:
                    conn = self._make_conn(host, port, use_https)
                    self._http_conn = conn
                try:
                    conn.request(method, target, body=data, headers=headers)
                    response = conn.getresponse()
                    raw = response.read()
                    status = response.status
                    response.close()
                    if status >= 400:
                        # 携带响应体（BytesIO），使调用方 exc.read() 可用
                        raise urllib.error.HTTPError(
                            full, status, response.reason or "", {}, io.BytesIO(raw)
                        )
                    if not raw:
                        return None
                    return json.loads(raw.decode("utf-8"))
                except urllib.error.HTTPError:
                    raise
                except (http.client.HTTPException, OSError) as exc:
                    # 连接可能已失效，丢弃重建；仅幂等方法可安全重试一次
                    self._http_conn = None
                    try:
                        conn.close()
                    except Exception:
                        pass
                    if attempt == 0 and idempotent:
                        continue
                    raise urllib.error.URLError(str(exc) or "connection error")
        raise urllib.error.URLError("connection error")

    def _get_json(self, path):
        return self._queue_request("GET", path)

    def _get_json_monitor(self, path):
        url = self.monitor_base_url + path
        request = urllib.request.Request(
            url, headers={"Accept": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, path, payload):
        return self._queue_request("POST", path, payload)

    def _service_action(self, action, service):
        labels = {
            "task": ("任务队列服务", "启动任务队列服务", "停止任务队列服务"),
            "monitor": ("监控服务", "启动监控服务", "停止监控服务"),
        }
        label, start_text, stop_text = labels[service]
        if action == "start":
            callback = self.on_start_service
            if not callback:
                messagebox.showinfo(label, "请从总控台的“检查与日志”中启动{}。".format(label))
                return
            try:
                callback(service)
            except Exception as exc:
                messagebox.showerror(start_text, "启动{}失败：\n{}".format(label, exc))
            return
        callback = self.on_stop_service
        if not callback:
            messagebox.showinfo(label, "请从总控台的“检查与日志”中停止{}。".format(label))
            return
        # 停止服务会中断其上正在运行的任务，属不可逆操作，原先无任何确认、误点即生效。
        if not messagebox.askyesno(
            stop_text,
            "确定停止{}吗？\n其上正在运行的任务可能被中断。".format(label),
            parent=self.window,
        ):
            return
        try:
            callback(service)
        except Exception as exc:
            messagebox.showerror(stop_text, "停止{}失败：\n{}".format(label, exc))

    def submit_local_flow_file(self):
        file_path = filedialog.askopenfilename(
            parent=self.window,
            title="选择自动化 JSON 链路文件",
            filetypes=[("JSON 流程文件", "*.json"), ("所有文件", "*.*")],
        )
        if not file_path:
            return
        threading.Thread(
            target=self._submit_local_flow_worker,
            args=(file_path,),
            daemon=True,
        ).start()

    def _submit_local_flow_worker(self, file_path):
        # worker 线程：只读线程安全快照，不得读 Tk 变量
        user = self._prefs_snapshot["user"]
        if not user:
            self._post_ui(
                lambda: messagebox.showwarning("提交 JSON 链路", "请先填写用户名。")
            )
            return
        try:
            with open(file_path, "r", encoding="utf-8") as file_obj:
                content = json.load(file_obj)
            if not isinstance(content, dict):
                self._post_ui(
                    lambda: messagebox.showerror("提交 JSON 链路", "所选文件不是 JSON 对象。")
                )
                return
            upload = self._post_json(
                "/api/flows/upload",
                {"name": os.path.basename(file_path), "content": content, "user": user},
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._post_ui(
                lambda: messagebox.showerror("上传流程失败", "{} {}".format(exc.code, detail))
            )
            return
        except Exception as exc:
            self._post_ui(
                lambda: messagebox.showerror("上传流程失败", self._friendly_error(exc))
            )
            return
        flow = {
            "name": upload.get("name") or os.path.basename(file_path),
            "path": upload.get("flowPath", ""),
        }
        self._post_ui(lambda: self._open_submit_dialog([flow]))

    def submit_task(self):
        threading.Thread(target=self._fetch_flows_worker, daemon=True).start()

    def _fetch_flows_worker(self):
        flows = []
        try:
            payload = self._get_json("/api/flows")
            flows = payload.get("flows", [])
        except Exception as exc:
            self._post_ui(
                lambda: self._append_log_text(
                    "[queue] 获取服务器流程列表失败：{}".format(exc)
                )
            )
        self._post_ui(lambda: self._open_submit_dialog(flows))

    def _open_submit_dialog(self, flows):
        flows = list(flows or [])
        dialog = tk.Toplevel(self.window)
        dialog.title("提交计算任务")
        dialog.configure(bg="#f4f7fb")
        dialog.geometry("520x600")
        dialog.transient(self.window)
        dialog.grab_set()

        flow_header = tk.Frame(dialog, bg="#f4f7fb")
        flow_header.pack(fill=tk.X, padx=12, pady=(10, 2))
        tk.Label(
            flow_header,
            text="选择流程（服务器端或本地 JSON）",
            bg="#f4f7fb",
            fg="#1f2937",
        ).pack(side=tk.LEFT)
        tk.Button(
            flow_header,
            text="选择本地 JSON 文件",
            command=lambda: self._pick_local_flow_into_dialog(dialog, listbox, flows),
            bg="#dbeafe",
            fg="#1f2937",
            relief=tk.FLAT,
            padx=10,
            pady=2,
            cursor="hand2",
        ).pack(side=tk.RIGHT)
        listbox = tk.Listbox(dialog, width=62, height=8)
        listbox.pack(fill=tk.X, padx=12)
        for flow in flows:
            listbox.insert(tk.END, flow.get("name", ""))
        if flows:
            listbox.selection_set(0)

        form = tk.Frame(dialog, bg="#f4f7fb")
        form.pack(fill=tk.X, padx=12, pady=8)
        steps_var = tk.StringVar(value="")
        from_var = tk.StringVar(value="")
        to_var = tk.StringVar(value="")
        priority_var = tk.StringVar(value="0")
        scheduled_var = tk.StringVar(value="")
        max_attempts_var = tk.StringVar(value="1")
        retry_delay_var = tk.StringVar(value="0")
        timeout_var = tk.StringVar(value="0")
        notify_url_var = tk.StringVar(value="")
        rows = (
            ("步骤（可选，逗号分隔）", steps_var),
            ("起始步骤（可选）", from_var),
            ("结束步骤（可选）", to_var),
            ("优先级（越大越先执行）", priority_var),
            ("预约时间（ISO，可选）", scheduled_var),
            ("最大尝试次数", max_attempts_var),
            ("失败重试延迟（秒）", retry_delay_var),
            ("运行超时（秒，0不限）", timeout_var),
            ("通知 URL（可选，http/https）", notify_url_var),
        )
        for row_index, (label, var) in enumerate(rows):
            tk.Label(form, text=label, bg="#f4f7fb", fg="#1f2937").grid(
                row=row_index, column=0, sticky="w", pady=3
            )
            tk.Entry(form, textvariable=var, width=44).grid(
                row=row_index, column=1, sticky="w", pady=3, padx=(8, 0)
            )

        def on_ok():
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning("提交任务", "请选择一个流程。")
                return
            flow = flows[selection[0]]
            user = self.user_var.get().strip()
            if not user:
                messagebox.showwarning("提交任务", "请先填写用户名。")
                return
            payload = {
                "user": user,
                "flowPath": flow.get("path", ""),
                "steps": steps_var.get().strip(),
                "fromStep": from_var.get().strip(),
                "toStep": to_var.get().strip(),
                "priority": priority_var.get().strip() or 0,
                "scheduledAt": scheduled_var.get().strip(),
                "maxAttempts": max_attempts_var.get().strip() or 1,
                "retryDelaySeconds": retry_delay_var.get().strip() or 0,
                "timeoutSeconds": timeout_var.get().strip() or 0,
                "notifyUrl": notify_url_var.get().strip(),
            }
            dialog.destroy()
            threading.Thread(
                target=self._send_submit_worker,
                args=(payload,),
                daemon=True,
            ).start()

        buttons = tk.Frame(dialog, bg="#f4f7fb")
        buttons.pack(fill=tk.X, padx=12, pady=(8, 12))
        tk.Button(
            buttons,
            text="提交",
            command=on_ok,
            bg="#059669",
            fg="white",
            relief=tk.FLAT,
            padx=18,
            pady=5,
            cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Button(
            buttons,
            text="取消",
            command=dialog.destroy,
            bg="#e2e8f0",
            fg="#1f2937",
            relief=tk.FLAT,
            padx=14,
            pady=5,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(8, 0))

    def _pick_local_flow_into_dialog(self, dialog, listbox, flows):
        file_path = filedialog.askopenfilename(
            parent=dialog,
            title="选择自动化 JSON 链路文件",
            filetypes=[("JSON 流程文件", "*.json"), ("所有文件", "*.*")],
        )
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as file_obj:
                content = json.load(file_obj)
            if not isinstance(content, dict):
                messagebox.showerror(
                    "选择流程失败", "所选文件不是 JSON 对象。", parent=dialog
                )
                return
        except Exception as exc:
            messagebox.showerror(
                "选择流程失败", "读取 JSON 失败：\n{}".format(exc), parent=dialog
            )
            return
        # 上传走后台线程：网络慢/文件大时不再阻塞 UI（原先在 UI 线程同步上传，窗口假死数秒）
        user = self._prefs_snapshot["user"]
        threading.Thread(
            target=self._upload_local_flow_worker,
            args=(dialog, listbox, flows, file_path, content, user),
            daemon=True,
        ).start()

    def _upload_local_flow_worker(self, dialog, listbox, flows, file_path, content, user):
        """后台线程：上传所选本地流程 JSON 到服务器（不触碰 Tk 变量）。"""
        try:
            payload = self._post_json(
                "/api/flows/upload",
                {
                    "name": os.path.basename(file_path),
                    "content": content,
                    "user": user,
                },
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._post_ui(
                lambda c=exc.code, d=detail: self._show_upload_error(
                    dialog, "{} {}".format(c, d)
                )
            )
            return
        except Exception as exc:
            self._post_ui(
                lambda e=exc: self._show_upload_error(dialog, self._friendly_error(e))
            )
            return
        flow = {
            "name": payload.get("name") or os.path.basename(file_path),
            "path": payload.get("flowPath", ""),
        }
        self._post_ui(lambda: self._apply_uploaded_flow(dialog, listbox, flows, flow))

    @staticmethod
    def _dialog_alive(dialog):
        """对话框可能在上传期间被用户关闭，更新其 UI 前须先确认。"""
        try:
            return bool(dialog.winfo_exists())
        except Exception:
            return False

    def _show_upload_error(self, dialog, detail):
        if not self._dialog_alive(dialog):
            return
        messagebox.showerror("上传流程失败", detail, parent=dialog)

    def _apply_uploaded_flow(self, dialog, listbox, flows, flow):
        if not self._dialog_alive(dialog):
            return
        for index, item in enumerate(flows):
            if item.get("name") == flow["name"]:
                flows[index] = flow
                break
        else:
            flows.append(flow)
        listbox.delete(0, tk.END)
        for item in flows:
            listbox.insert(tk.END, item.get("name", ""))
        listbox.selection_clear(0, tk.END)
        listbox.selection_set(tk.END)
        messagebox.showinfo(
            "上传流程成功", "流程已上传到服务器，可直接填写参数后提交。", parent=dialog
        )

    @staticmethod
    def _call_submit_callback(completed_callback, task_ids=None, results=None):
        """安全回调提交完成/取消（调用方可能已销毁或回调本身抛错）。"""
        if not completed_callback:
            return
        try:
            completed_callback(list(task_ids or []), list(results or []))
        except Exception:
            pass

    def submit_simple_sections_dialog(
        self, sections, completed_callback=None, on_task_submitted=None
    ):
        sections = [s for s in sections if s and s.get("path")]
        if not sections:
            messagebox.showinfo("提交所选板块", "没有可提交的板块（请先勾选并配置流程文件）。")
            self._call_submit_callback(completed_callback)
            return
        dialog = tk.Toplevel(self.window)
        dialog.title("提交所选板块到远程队列")
        dialog.configure(bg="#f4f7fb")
        dialog.geometry("580x540")
        dialog.transient(self.window)
        dialog.grab_set()

        header = tk.Frame(dialog, bg="#f4f7fb")
        header.pack(fill=tk.X, padx=12, pady=(10, 6))
        tk.Label(header, text="已勾选板块", bg="#f4f7fb", fg="#1f2937",
                 font=("Microsoft YaHei UI", 11, "bold")).pack(side=tk.LEFT)

        list_frame = tk.Frame(dialog, bg="#ffffff",
                              highlightthickness=1, highlightbackground="#cbd5e1")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 8))
        section_vars = []
        for sec in sections:
            var = tk.BooleanVar(value=True)
            section_vars.append((sec, var))
            row = tk.Frame(list_frame, bg="#ffffff")
            row.pack(fill=tk.X, padx=8, pady=4)
            tk.Checkbutton(row, variable=var, bg="#ffffff", activebackground="#ffffff",
                           selectcolor="#ffffff").pack(side=tk.LEFT)
            title = sec.get("title") or sec.get("key") or ""
            path = sec.get("path", "")
            tk.Label(row, text=title, bg="#ffffff", fg="#1f2937", width=20, anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=os.path.basename(path) if path else "（未设置）",
                     bg="#ffffff", fg="#64748b", anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)

        extra_files = []
        extra_label_var = tk.StringVar(value="未添加")
        chain_frame = tk.Frame(dialog, bg="#f4f7fb")
        chain_frame.pack(fill=tk.X, padx=12, pady=4)
        tk.Button(
            chain_frame,
            text="添加自动化 JSON 链路文件",
            command=lambda: self._pick_extra_chain_files(dialog, extra_files, extra_label_var),
            bg="#dbeafe",
            fg="#1f2937",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT)
        tk.Label(chain_frame, textvariable=extra_label_var, bg="#f4f7fb", fg="#64748b").pack(
            side=tk.LEFT, padx=(10, 0)
        )

        options_frame = tk.Frame(dialog, bg="#f4f7fb")
        options_frame.pack(fill=tk.X, padx=12, pady=6)
        skip_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            options_frame,
            text="跳过服务器上内容未变化的流程（避免重复生成版本）",
            variable=skip_var,
            bg="#f4f7fb",
            fg="#1f2937",
            activebackground="#f4f7fb",
            selectcolor="#ffffff",
        ).pack(anchor="w")

        buttons = tk.Frame(dialog, bg="#f4f7fb")
        buttons.pack(fill=tk.X, padx=12, pady=(8, 12))

        def on_submit():
            selected = []
            for sec, var in section_vars:
                if var.get():
                    selected.append(sec)
            for path in extra_files:
                selected.append({"key": "chain", "title": os.path.basename(path), "path": path})
            dialog.destroy()
            self.submit_simple_sections(
                selected,
                completed_callback,
                skip_identical=skip_var.get(),
                on_task_submitted=on_task_submitted,
            )

        def on_cancel():
            dialog.destroy()
            # 用户取消/关闭对话框：必须通知调用方，避免"提交中"标志与按钮永久卡死
            self._call_submit_callback(completed_callback)

        tk.Button(buttons, text="提交", command=on_submit, bg="#059669", fg="white",
                  relief=tk.FLAT, padx=18, pady=5, cursor="hand2").pack(side=tk.LEFT)
        tk.Button(buttons, text="取消", command=on_cancel, bg="#e2e8f0", fg="#1f2937",
                  relief=tk.FLAT, padx=14, pady=5, cursor="hand2").pack(side=tk.LEFT, padx=(8, 0))
        dialog.protocol("WM_DELETE_WINDOW", on_cancel)

    @staticmethod
    def _pick_extra_chain_files(parent, extra_files, label_var):
        paths = filedialog.askopenfilenames(
            parent=parent,
            title="选择自动化 JSON 链路文件（可多选）",
            filetypes=[("JSON 流程文件", "*.json"), ("所有文件", "*.*")],
        )
        if not paths:
            return
        extra_files[:] = []
        for path in paths:
            if path not in extra_files:
                extra_files.append(path)
        label_var.set("已添加 {} 个文件：{}".format(
            len(extra_files),
            "、".join(os.path.basename(p) for p in extra_files),
        ))

    def submit_simple_sections(
        self, sections, completed_callback=None, skip_identical=False, on_task_submitted=None
    ):
        sections = [s for s in sections if s and s.get("path")]
        if not sections:
            messagebox.showinfo(
                "提交所选板块", "没有可提交的板块（请先勾选并配置流程文件）。"
            )
            self._call_submit_callback(completed_callback)
            return
        user = self.user_var.get().strip()
        token = self.token_var.get().strip()
        if not user:
            messagebox.showwarning("提交所选板块", "请先在顶部填写用户名。")
            self.window.lift()
            self._call_submit_callback(completed_callback)
            return
        if not token:
            messagebox.showwarning("提交所选板块", "请先在顶部填写服务令牌。")
            self.window.lift()
            self._call_submit_callback(completed_callback)
            return
        self.window.lift()
        self._render_logs(["开始提交 {} 个板块到远程队列...".format(len(sections))])
        threading.Thread(
            target=self._submit_simple_sections_worker,
            args=(list(sections), user, completed_callback),
            kwargs={
                "skip_identical": bool(skip_identical),
                "on_task_submitted": on_task_submitted,
            },
            daemon=True,
        ).start()

    def _submit_simple_sections_worker(
        self,
        sections,
        user,
        completed_callback=None,
        skip_identical=False,
        on_task_submitted=None,
    ):
        results = []
        task_ids = []
        known_flows = {}
        if skip_identical:
            try:
                payload = self._get_json("/api/flows")
                for flow in payload.get("flows", []):
                    versions = flow.get("versions") or []
                    latest = versions[-1] if versions else {}
                    known_flows[str(flow.get("name", "")).lower()] = {
                        "path": flow.get("path", ""),
                        "sha256": str(latest.get("sha256") or ""),
                    }
            except Exception:
                known_flows = {}
        for index, sec in enumerate(sections, start=1):
            title = sec.get("title") or sec.get("key") or ""
            path = sec.get("path", "")
            # 板块可携带 name（覆盖后的临时流程仍按原始文件名上传/比对）与
            # runtimeConfig（项目参数，随任务提交、worker 启动时注入）
            flow_name = str(sec.get("name") or "").strip() or os.path.basename(path)
            runtime_config = sec.get("runtimeConfig")
            has_runtime = isinstance(runtime_config, dict) and bool(runtime_config)
            param_tag = "（含项目参数）" if has_runtime else ""
            self._post_ui(
                lambda t=title, tag=param_tag, i=index, n=len(sections): self._append_log_text(
                    "[queue] 提交 {}/{}：{}{}".format(i, n, t, tag)
                )
            )
            if not os.path.isfile(path):
                results.append((title, "本地流程文件不存在"))
                continue
            try:
                with open(path, "r", encoding="utf-8") as file_obj:
                    content = json.load(file_obj)
                if not isinstance(content, dict):
                    results.append((title, "流程文件不是 JSON 对象"))
                    continue
                digest = self._flow_sha256(content)
                known = known_flows.get(flow_name.lower())
                if known and known.get("path") and known.get("sha256") == digest:
                    flow_path = known["path"]
                    skipped_upload = True
                else:
                    upload = self._post_json(
                        "/api/flows/upload",
                        {
                            "name": flow_name,
                            "content": content,
                            "user": user,
                        },
                    )
                    flow_path = upload.get("flowPath", "")
                    skipped_upload = False
                client_token = str(
                    sec.get("clientToken") or sec.get("idempotencyKey") or ""
                ).strip()
                if not client_token:
                    import time

                    sec_id = str(sec.get("key") or sec.get("title") or index)
                    token_seed = "{}_{}_{}_{}_{}".format(
                        user,
                        flow_name,
                        json.dumps(runtime_config, sort_keys=True) if has_runtime else "",
                        sec_id,
                        int(time.time() // 10),
                    )
                    client_token = "cli_" + hashlib.md5(token_seed.encode("utf-8")).hexdigest()[:16]

                submit_resp = self._post_json(
                    "/api/tasks/submit",
                    {
                        "user": user,
                        "flowPath": flow_path,
                        "steps": "",
                        "fromStep": "",
                        "toStep": "",
                        "priority": 0,
                        "maxAttempts": 1,
                        "retryDelaySeconds": 0,
                        "timeoutSeconds": 0,
                        "runtimeConfig": runtime_config if has_runtime else {},
                        "idempotencyKey": client_token,
                    },
                )
                task = submit_resp.get("task") or {}
                task_id = str(task.get("taskId") or "")
                if task_id:
                    task_ids.append(task_id)
                    # 每成功入队一个任务立即通知调用方，使"提交中停止"能终止已提交任务
                    if on_task_submitted:
                        try:
                            on_task_submitted(task_id)
                        except Exception:
                            pass
                parts = []
                if skipped_upload:
                    parts.append("内容未变化，跳过上传")
                if submit_resp.get("idempotentReplay"):
                    parts.append("幂等重放，复用已入队任务")
                results.append((title, "已提交" + (param_tag or "") + ("（{}）".format("，".join(parts)) if parts else "")))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                results.append((title, "失败：{} {}".format(exc.code, detail)))
            except Exception as exc:
                results.append((title, "失败：{}".format(exc)))
        self._post_ui(
            lambda: self._show_simple_submit_result(results, task_ids, completed_callback)
        )

    @staticmethod
    def _flow_sha256(content):
        serialized = json.dumps(content, ensure_ascii=False, indent=2)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _show_simple_submit_result(self, results, task_ids=None, completed_callback=None):
        # 成功消息以"已提交"开头（含"含项目参数/跳过上传"等修饰），其余按失败处理
        success = [item for item in results if str(item[1]).startswith("已提交")]
        failed = [item for item in results if not str(item[1]).startswith("已提交")]
        skipped = sum(1 for item in results if "跳过上传" in str(item[1]))
        params_cnt = sum(1 for item in results if "含项目参数" in str(item[1]))
        skip_text = "，跳过上传 {} 个".format(skipped) if skipped else ""
        param_text = "，含项目参数 {} 个".format(params_cnt) if params_cnt else ""
        self._append_log_text(
            "[queue] 提交完成：成功 {} 个，失败 {} 个{}{}".format(
                len(success),
                len(failed),
                skip_text,
                param_text,
            )
        )
        if failed:
            detail = "\n".join(
                "- {}：{}".format(title, reason) for title, reason in failed
            )
            messagebox.showwarning(
                "提交所选板块",
                "成功 {} 个，失败 {} 个：\n\n{}".format(
                    len(success), len(failed), detail
                ),
            )
        else:
            messagebox.showinfo(
                "提交所选板块", "已成功提交 {} 个板块到远程队列。".format(len(success))
            )
        self.refresh()
        if completed_callback:
            try:
                completed_callback(list(task_ids or []), list(results or []))
            except Exception:
                pass

    def get_task_detail(self, task_id):
        task_id = str(task_id or "").strip()
        if not task_id:
            return None
        return self._get_json(
            "/api/tasks/{}".format(urllib.parse.quote(task_id))
        )

    def get_tasks_batch(self, task_ids):
        """批量查询任务状态：用 1 次请求替代 N 次 get_task_detail。

        依赖服务端 /api/tasks?ids=...（新增参数）。旧服务端不识别该参数时
        会返回 400/404，此时返回 None，调用方应回退到逐个查询。
        """
        ids = [str(item).strip() for item in (task_ids or []) if str(item).strip()]
        if not ids:
            return {}
        limit = min(500, max(len(ids), 1))
        try:
            payload = self._get_json(
                "/api/tasks?ids={}&limit={}".format(
                    urllib.parse.quote(",".join(ids), safe=""), limit
                )
            )
        except urllib.error.HTTPError as exc:
            # 服务端不支持批量查询时，交由调用方降级为逐个查询
            if exc.code in (400, 404, 501):
                return None
            raise
        tasks = (payload or {}).get("tasks") or []
        return {
            str(item.get("taskId") or ""): item
            for item in tasks
            if isinstance(item, dict)
        }

    def control_task(self, task_id, action):
        task_id = str(task_id or "").strip()
        if not task_id:
            return False
        try:
            self._post_json(
                "/api/tasks/{}/{}".format(
                    urllib.parse.quote(task_id), str(action or "").strip()
                ),
                {},
            )
            self._post_ui(self.refresh)
            return True
        except Exception as exc:
            # 保持 bool 返回（调用方按真值判断），但把失败原因写进日志面板：
            # 原先静默 return False，用户与调用方都只看到「点了没反应」。
            reason = self._friendly_error(exc)
            self._post_ui(lambda: self._append_log_text("任务操作失败：{}".format(reason)))
            return False

    def _append_log_text(self, line):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(
            tk.END, str(line) + "\n", self._classify_line(str(line))
        )
        # 与增量渲染共用行数上限，防止本地提交提示把 Text 撑到无限大。
        # Tk 的 end-1c 行号比真实行数大 1（见 _append_lines_incremental 注释）
        total = max(0, int(self.log_text.index("end-1c").split(".")[0] or 0) - 1)
        excess = total - self._LOG_MAX_LINES
        if excess > 0:
            self.log_text.delete("1.0", "%d.0" % (excess + 1))
        self.log_text.config(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def _send_submit_worker(self, payload):
        try:
            self._post_json("/api/tasks/submit", payload)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            self._post_ui(
                lambda: messagebox.showerror(
                    "提交失败", "{} {}".format(exc.code, detail)
                )
            )
            return
        except Exception as exc:
            self._post_ui(
                lambda: messagebox.showerror("提交失败", self._friendly_error(exc))
            )
            return
        self._post_ui(self.refresh)

    def retry_all_failed_tasks_action(self):
        """批量重试所有失败/异常终止的任务"""
        tasks = getattr(self, "_cached_tasks", [])
        failed_tasks = [
            t for t in tasks
            if str(t.get("status", "")).lower() in ("failed", "terminated")
        ]
        if not failed_tasks:
            messagebox.showinfo("批量重试", "当前列表中没有处于失败或终止状态的任务。", parent=self.window)
            return

        if not messagebox.askyesno(
            "批量重试任务",
            "检测到 {} 个失败/终止的任务，确定要将它们全部重新加入排队吗？".format(len(failed_tasks)),
            parent=self.window,
        ):
            return

        threading.Thread(
            target=self._retry_all_failed_worker,
            args=(failed_tasks,),
            daemon=True,
        ).start()

    def _retry_all_failed_worker(self, failed_tasks):
        success_count = 0
        err_count = 0
        for t in failed_tasks:
            tid = t.get("taskId")
            if not tid:
                continue
            try:
                self._post_json("/api/tasks/{}/resume".format(urllib.parse.quote(str(tid))), {})
                success_count += 1
            except Exception:
                err_count += 1
        msg = "批量重试完成：成功恢复 {} 个任务{}".format(
            success_count,
            "，{} 个失败".format(err_count) if err_count else ""
        )
        self._post_ui(lambda: [
            self.refresh(),
            messagebox.showinfo("批量重试完成", msg, parent=self.window)
        ])

    def clear_finished_tasks_action(self):
        """批量清理删除所有已结束的任务记录（成功/失败/终止/已取消）"""
        tasks = getattr(self, "_cached_tasks", [])
        finished_tasks = [
            t for t in tasks
            if str(t.get("status", "")).lower() in ("success", "failed", "terminated", "canceled", "cancelled")
        ]
        if not finished_tasks:
            messagebox.showinfo("清理已结束任务", "当前列表中没有已结束的任务记录。", parent=self.window)
            return

        if not messagebox.askyesno(
            "清理已结束任务",
            "确定要删除 {} 个已结束（成功/失败/取消）的任务记录吗？\n此操作不可恢复，请确认。".format(len(finished_tasks)),
            parent=self.window,
        ):
            return

        threading.Thread(
            target=self._clear_finished_worker,
            args=(finished_tasks,),
            daemon=True,
        ).start()

    def _clear_finished_worker(self, finished_tasks):
        del_count = 0
        err_count = 0
        for t in finished_tasks:
            tid = t.get("taskId")
            if not tid:
                continue
            try:
                self._post_json("/api/tasks/{}/delete".format(urllib.parse.quote(str(tid))), {})
                del_count += 1
            except Exception:
                err_count += 1
        msg = "已从队列中清理删除 {} 条记录{}".format(
            del_count,
            "，{} 条删除失败".format(err_count) if err_count else ""
        )
        self._post_ui(lambda: [
            self.refresh(),
            messagebox.showinfo("清理完成", msg, parent=self.window)
        ])

    def open_batch_mast_queue_dialog(self, initial_work_dir=""):
        """打开测风塔按需排队向导对话框（高级/局部补算）"""
        work_dir = initial_work_dir or getattr(self, "project_dir", "")
        return BatchMastQueueDialog(self.window, queue_window=self, initial_work_dir=work_dir)

    def control_action(self, action):
        task_id = self._selected_task_id()
        if not task_id:
            messagebox.showinfo("任务队列", "请先在列表中选择一个任务。")
            return
        if action == "delete":
            if not messagebox.askyesno(
                "删除任务",
                "确定删除该任务记录吗？此操作不可恢复，且不会终止正在运行的 worker。\n"
                "如需停止运行中的任务，请先用「终止」。",
                parent=self.window,
            ):
                return
        elif action in ("terminate", "cancel"):
            # 「终止」会强制结束运行中的 worker，「取消」会让排队任务不再执行，
            # 都属不可逆操作，原先却只有「删除」有确认，误点即生效。
            if action == "terminate":
                title, detail = "终止任务", "正在运行的 worker 会被强制结束，当前进度将丢失。"
            else:
                title, detail = "取消任务", "排队中的任务将不再执行。"
            if not messagebox.askyesno(
                title,
                "确定{}所选任务吗？\n{}".format("终止" if action == "terminate" else "取消", detail),
                parent=self.window,
            ):
                return
        threading.Thread(
            target=self._send_control_worker,
            args=(task_id, action),
            daemon=True,
        ).start()

    def _send_control_worker(self, task_id, action):
        try:
            self._post_json(
                "/api/tasks/{}/{}".format(urllib.parse.quote(task_id), action),
                {},
            )
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            # 用默认参数捕获 exc/detail，避免 except-as 变量在闭包延迟执行时被删除
            self._post_ui(
                lambda c=exc.code, d=detail: messagebox.showerror(
                    "操作失败", "{} {}".format(c, d)
                )
            )
            return
        except Exception as exc:
            self._post_ui(
                lambda e=exc: messagebox.showerror("操作失败", self._friendly_error(e))
            )
            return
        self._post_ui(self.refresh)

    def view_report(self):
        task_id = self._selected_task_id()
        if not task_id:
            messagebox.showinfo("任务队列", "请先在列表中选择一个任务。")
            return
        threading.Thread(
            target=self._fetch_report_worker,
            args=(task_id,),
            daemon=True,
        ).start()

    def _fetch_report_worker(self, task_id):
        try:
            payload = self._get_json(
                "/api/tasks/{}/report".format(urllib.parse.quote(task_id))
            )
        except urllib.error.HTTPError as exc:
            try:
                detail = exc.read().decode("utf-8", errors="replace")
            except Exception:
                detail = str(exc)
            self._post_ui(
                lambda c=exc.code, d=detail: messagebox.showerror(
                    "查看报告失败", "{} {}".format(c, d)
                )
            )
            return
        except Exception as exc:
            self._post_ui(
                lambda e=exc: messagebox.showerror(
                    "查看报告失败", self._friendly_error(e)
                )
            )
            return
        self._post_ui(lambda: self._open_report_dialog(payload))

    def _open_report_dialog(self, payload):
        dialog = tk.Toplevel(self.window)
        dialog.title("任务运行报告")
        dialog.configure(bg="#f4f7fb")
        dialog.geometry("680x520")
        dialog.transient(self.window)
        header = tk.Frame(dialog, bg="#f4f7fb")
        header.pack(fill=tk.X, padx=10, pady=(10, 0))
        tk.Button(
            header,
            text="另存为文件",
            command=lambda: self._save_report_to_file(payload),
            bg="#dbeafe",
            fg="#1f2937",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.RIGHT)
        frame = tk.Frame(dialog, bg="#111418")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        text = tk.Text(
            frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            bg="#111418",
            fg="#e6edf3",
            insertbackground="#e6edf3",
            font=("Consolas", 9),
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
        scrollbar = tk.Scrollbar(frame, command=text.yview, relief=tk.FLAT)
        text.config(yscrollcommand=scrollbar.set)
        # 滚动条先 pack（防止 JSON 长行挤压滚动条）
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        text.config(state=tk.NORMAL)
        text.insert(tk.END, json.dumps(payload, ensure_ascii=False, indent=2))
        text.config(state=tk.DISABLED)

    def _save_report_to_file(self, payload):
        file_path = filedialog.asksaveasfilename(
            parent=self.window,
            title="另存任务报告",
            defaultextension=".json",
            filetypes=[("JSON 文件", "*.json")],
            initialfile="task_report.json",
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as file_obj:
                file_obj.write(
                    json.dumps(payload, ensure_ascii=False, indent=2)
                )
        except Exception as exc:
            messagebox.showerror("另存报告失败", str(exc), parent=self.window)
            return
        messagebox.showinfo(
            "另存报告", "报告已保存到：\n{}".format(file_path), parent=self.window
        )

    @staticmethod
    def _poll_delay_ms(streak):
        # 稳态 2s（与界面文案/文档一致），失败时按 2→4→8→16→30s 退避
        seconds = min(30, max(POLL_MS / 1000.0, 2 ** min(max(0, streak), 5)))
        return int(seconds * 1000)

    def _poll_loop(self):
        if self._closing:
            return
        if self.auto_var.get():
            self.refresh()
        # 流程仓库页签：跟随队列自动刷新（独立开关，默认随队列刷新）
        if getattr(self, "flows_auto_var", None) is not None and self.flows_auto_var.get():
            self.refresh_flows()
        self._after_id = self.window.after(
            self._poll_delay_ms(self._queue_fail_streak), self._poll_loop
        )

    def _monitor_poll_loop(self):
        if self._closing:
            return
        if self.monitor_auto_var.get():
            self.refresh_monitor()
        self._monitor_after_id = self.window.after(
            self._poll_delay_ms(self._monitor_fail_streak), self._monitor_poll_loop
        )

    def _on_close(self):
        self._closing = True
        if getattr(self, "_sse_stop_event", None) is not None:
            self._sse_stop_event.set()
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except Exception:
                pass
        if self._monitor_after_id is not None:
            try:
                self.window.after_cancel(self._monitor_after_id)
            except Exception:
                pass
        self._save_settings()
        try:
            self.window.destroy()
        except Exception:
            pass


class BatchMastQueueDialog:
    """测风塔按需排队向导对话框（高级/局部补算）。

    支持选择项目工作文件夹，自动解析 CFT信息.txt 提取所有测风塔，
    提供复选表格供勾选目标测风塔，并配置执行流程与参数，批量排队提交任务。
    提示：标准业务流程已内置全量测风塔自动遍历新建，本向导仅供现场调试、指定部分测风塔补跑或分派自定义流程时使用。
    """

    def __init__(self, parent, queue_window, initial_work_dir=""):
        self.parent = parent
        self.queue_window = queue_window
        self.pal = wt_theme.get_palette()
        self.masts = []  # list of mast dicts
        self.selected_masts = set()  # set of mast indices (0, 1, ...)
        self.server_flows = []
        self.local_flow_path = ""
        self.local_flow_content = None
        self._is_submitting = False

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("测风塔按需排队向导 (高级)")
        self.dialog.geometry("820x680")
        self.dialog.minsize(720, 520)
        self.dialog.configure(bg=self.pal["bg"])
        self.dialog.transient(parent)
        try:
            self.dialog.grab_set()
        except Exception:
            pass

        self._build_ui(initial_work_dir)
        self._fetch_flows()
        if initial_work_dir:
            self._scan_masts(initial_work_dir)

    def _build_ui(self, initial_work_dir):
        # 1. 顶部 Header
        header = tk.Frame(
            self.dialog,
            bg=self.pal["surface"],
            padx=16,
            pady=10,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        )
        header.pack(fill=tk.X, padx=12, pady=(10, 6))

        tk.Label(
            header,
            text="📐 测风塔按需排队向导 (高级)",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg=self.pal["surface"],
            fg=self.pal["primary_text"],
        ).pack(anchor="w")

        tk.Label(
            header,
            text="基于项目工作文件夹中的测风塔数据，按需勾选特定测风塔并分派任务到远程调度队列。",
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["surface"],
            fg=self.pal["muted"],
        ).pack(anchor="w", pady=(2, 0))

        # 防呆与定位提示卡片
        tip_frame = tk.Frame(
            header,
            bg=self.pal["bg"],
            padx=10,
            pady=5,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        )
        tip_frame.pack(fill=tk.X, pady=(6, 0))

        tip_lbl = tk.Label(
            tip_frame,
            text="💡 提示：项目标准流程已内置全量测风塔自动遍历新建；本向导仅供现场调试、指定部分测风塔补跑或分派自定义流程时使用。",
            font=("Microsoft YaHei UI", 8),
            bg=self.pal["bg"],
            fg=self.pal["muted"],
            wraplength=640,
            justify=tk.LEFT,
        )
        tip_lbl.pack(anchor="w", fill=tk.X)
        tip_frame.bind(
            "<Configure>",
            lambda e: tip_lbl.config(wraplength=max(200, e.width - 20)),
        )

        # 2. 项目工作文件夹选择区
        proj_frame = tk.Frame(
            self.dialog,
            bg=self.pal["surface"],
            padx=14,
            pady=8,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        )
        proj_frame.pack(fill=tk.X, padx=12, pady=(0, 6))

        tk.Label(
            proj_frame,
            text="项目工作目录:",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.pal["surface"],
            fg=self.pal["text"],
        ).pack(side=tk.LEFT, padx=(0, 6))

        self.work_dir_var = tk.StringVar(value=initial_work_dir or "")
        dir_entry = tk.Entry(
            proj_frame,
            textvariable=self.work_dir_var,
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["card_hover"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        )
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        dir_entry.bind("<Return>", lambda _e: self._scan_masts(self.work_dir_var.get()))

        wt_theme.create_flat_button(
            proj_frame,
            text="📂 浏览...",
            command=self._browse_dir,
            tone="secondary",
            padx=10,
            pady=2,
        ).pack(side=tk.LEFT, padx=(0, 6))

        wt_theme.create_flat_button(
            proj_frame,
            text="🔍 扫描测风塔",
            command=lambda: self._scan_masts(self.work_dir_var.get()),
            tone="primary",
            padx=10,
            pady=2,
        ).pack(side=tk.LEFT)

        # 3. 测风塔清单表格区
        tree_card = tk.Frame(
            self.dialog,
            bg=self.pal["surface"],
            padx=14,
            pady=8,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        )
        tree_card.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))

        tree_top = tk.Frame(tree_card, bg=self.pal["surface"])
        tree_top.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            tree_top,
            text="测风塔清单 (勾选参与排队的测风塔):",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.pal["surface"],
            fg=self.pal["text"],
        ).pack(side=tk.LEFT)

        wt_theme.create_flat_button(
            tree_top,
            text="✔ 全选",
            command=self._select_all,
            tone="subtle",
            padx=6,
            pady=1,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT, padx=(10, 4))

        wt_theme.create_flat_button(
            tree_top,
            text="✖ 全不选",
            command=self._select_none,
            tone="subtle",
            padx=6,
            pady=1,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT, padx=(0, 4))

        wt_theme.create_flat_button(
            tree_top,
            text="🔄 反选",
            command=self._select_invert,
            tone="subtle",
            padx=6,
            pady=1,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT, padx=(0, 10))

        self.mast_count_badge = wt_theme.create_badge(
            tree_top, "已选 0 / 0 座塔", "info"
        )
        self.mast_count_badge.pack(side=tk.RIGHT)

        # 表格容器
        table_container = tk.Frame(tree_card, bg=self.pal["surface"])
        table_container.pack(fill=tk.BOTH, expand=True)

        cols = ("sel", "mast_name", "height", "lon", "lat", "utm")
        self.tree = ttk.Treeview(
            table_container,
            columns=cols,
            show="headings",
            selectmode="browse",
            height=8,
        )
        self.tree.heading("sel", text="选择")
        self.tree.heading("mast_name", text="测风塔编号")
        self.tree.heading("height", text="测风高度/轮毂 (m)")
        self.tree.heading("lon", text="经度")
        self.tree.heading("lat", text="纬度")
        self.tree.heading("utm", text="UTM 坐标")

        self.tree.column("sel", width=55, anchor="center")
        self.tree.column("mast_name", width=120, anchor="w")
        self.tree.column("height", width=130, anchor="center")
        self.tree.column("lon", width=100, anchor="center")
        self.tree.column("lat", width=100, anchor="center")
        self.tree.column("utm", width=160, anchor="center")

        tree_sb = wt_theme.create_modern_scrollbar(table_container, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_tree_space)

        # 4. 流程与执行选项配置卡片
        options_card = tk.Frame(
            self.dialog,
            bg=self.pal["surface"],
            padx=14,
            pady=8,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        )
        options_card.pack(fill=tk.X, padx=12, pady=(0, 6))

        # 第一行：流程选择
        flow_row = tk.Frame(options_card, bg=self.pal["surface"])
        flow_row.pack(fill=tk.X, pady=(0, 6))

        tk.Label(
            flow_row,
            text="计算流程:",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg=self.pal["surface"],
            fg=self.pal["text"],
        ).pack(side=tk.LEFT, padx=(0, 6))

        self.flow_var = tk.StringVar()
        self.flow_combo = ttk.Combobox(
            flow_row,
            textvariable=self.flow_var,
            width=36,
            font=("Microsoft YaHei UI", 9),
            state="readonly",
        )
        self.flow_combo.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        wt_theme.create_flat_button(
            flow_row,
            text="📂 本地流程 JSON...",
            command=self._pick_local_flow,
            tone="secondary",
            padx=8,
            pady=2,
            font=("Microsoft YaHei UI", 8),
        ).pack(side=tk.LEFT)

        # 第二行：执行参数
        params_row = tk.Frame(options_card, bg=self.pal["surface"])
        params_row.pack(fill=tk.X)

        tk.Label(
            params_row,
            text="用户名:",
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["surface"],
            fg=self.pal["muted"],
        ).pack(side=tk.LEFT, padx=(0, 4))
        user_init = getattr(self.queue_window, "user_var", None)
        user_val = user_init.get() if user_init else ""
        self.user_var = tk.StringVar(value=user_val or "operator")
        tk.Entry(
            params_row,
            textvariable=self.user_var,
            width=10,
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["card_hover"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        ).pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(
            params_row,
            text="优先级:",
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["surface"],
            fg=self.pal["muted"],
        ).pack(side=tk.LEFT, padx=(0, 4))
        self.priority_var = tk.StringVar(value="0")
        tk.Entry(
            params_row,
            textvariable=self.priority_var,
            width=6,
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["card_hover"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        ).pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(
            params_row,
            text="最大重试:",
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["surface"],
            fg=self.pal["muted"],
        ).pack(side=tk.LEFT, padx=(0, 4))
        self.max_attempts_var = tk.StringVar(value="1")
        tk.Entry(
            params_row,
            textvariable=self.max_attempts_var,
            width=5,
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["card_hover"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        ).pack(side=tk.LEFT, padx=(0, 10))

        tk.Label(
            params_row,
            text="超时 (秒):",
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["surface"],
            fg=self.pal["muted"],
        ).pack(side=tk.LEFT, padx=(0, 4))
        self.timeout_var = tk.StringVar(value="0")
        tk.Entry(
            params_row,
            textvariable=self.timeout_var,
            width=6,
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["card_hover"],
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground=self.pal["border"],
        ).pack(side=tk.LEFT)

        # 5. 底部状态与操作栏
        footer = tk.Frame(self.dialog, bg=self.pal["bg"])
        footer.pack(fill=tk.X, padx=12, pady=(4, 10))

        self.status_lbl = tk.Label(
            footer,
            text="准备就绪。请选择工作目录并勾选测风塔。",
            font=("Microsoft YaHei UI", 9),
            bg=self.pal["bg"],
            fg=self.pal["muted"],
            anchor="w",
        )
        self.status_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.submit_btn = wt_theme.create_flat_button(
            footer,
            text="🚀 批量提交排队 (已选 0 座塔)",
            command=self._start_batch_submit,
            tone="primary",
            padx=16,
            pady=4,
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.submit_btn.pack(side=tk.RIGHT, padx=(8, 0))

        wt_theme.create_flat_button(
            footer,
            text="关闭",
            command=self.dialog.destroy,
            tone="subtle",
            padx=12,
            pady=4,
            font=("Microsoft YaHei UI", 9),
        ).pack(side=tk.RIGHT)

    def _browse_dir(self):
        chosen = filedialog.askdirectory(
            parent=self.dialog,
            title="选择包含测风塔数据的项目工作文件夹",
            initialdir=self.work_dir_var.get().strip() or None,
        )
        if chosen:
            self.work_dir_var.set(chosen)
            self._scan_masts(chosen)

    def _scan_masts(self, work_dir):
        work_dir = (work_dir or "").strip()
        if not work_dir or not os.path.isdir(work_dir):
            self.status_lbl.config(text="⚠️ 项目工作文件夹不存在，请重新选择。")
            return

        entries = []
        if wt_project_workdir_parser is not None:
            try:
                entries = wt_project_workdir_parser.list_mast_entries(work_dir)
            except Exception as exc:
                wt_logging.get_logger("wt_queue").warning("list_mast_entries failed: %s", exc)

        # 兜底：如果直接调用 parser 为空，尝试找常见路径的 CFT信息.txt
        if not entries and wt_project_workdir_parser is not None:
            candidates = [
                os.path.join(work_dir, "03-WT输入", "01-测风塔及机位点坐标", "CFT信息.txt"),
                os.path.join(work_dir, "03-WT输入", "CFT信息.txt"),
                os.path.join(work_dir, "CFT信息.txt"),
            ]
            for c in candidates:
                if os.path.isfile(c):
                    entries = wt_project_workdir_parser._parse_cft_info_file(c)
                    if entries:
                        break

        # 备选回退：扫描 03-WT输入/03-测风塔数据 下的子目录名称
        if not entries:
            mast_dir = os.path.join(work_dir, "03-WT输入", "03-测风塔数据")
            if os.path.isdir(mast_dir):
                for sub in sorted(os.listdir(mast_dir)):
                    sub_path = os.path.join(mast_dir, sub)
                    if os.path.isdir(sub_path):
                        entries.append({
                            "mastName": sub,
                            "hubHeight": "",
                            "longitude": "",
                            "latitude": "",
                            "elevation": "",
                            "lon": "",
                            "lat": "",
                            "elev": "",
                            "utmX": "",
                            "utmY": "",
                        })

        self.masts = entries
        self.tree.delete(*self.tree.get_children())
        self.selected_masts = set(range(len(entries)))

        for idx, entry in enumerate(entries):
            name = entry.get("mastName") or entry.get("mastId") or "未知塔"
            height = str(entry.get("hubHeight") or "-")
            lon = str(entry.get("longitude") or entry.get("lon") or "-")
            lat = str(entry.get("latitude") or entry.get("lat") or "-")
            utm_x = entry.get("utmX")
            utm_y = entry.get("utmY")
            utm = "{}, {}".format(utm_x, utm_y) if utm_x and utm_y else "-"
            self.tree.insert("", tk.END, iid=str(idx), values=("✔", name, height, lon, lat, utm))

        self._update_selection_stats()
        if entries:
            self.status_lbl.config(text="✅ 成功解析到 {} 座测风塔，已默认全选。".format(len(entries)))
        else:
            self.status_lbl.config(text="ℹ️ 在所选目录未解析到测风塔，请确认是否存在 CFT信息.txt 或 03-测风塔数据。")

    def _update_selection_stats(self):
        sel_count = len(self.selected_masts)
        total_count = len(self.masts)
        badge_tone = "primary" if sel_count > 0 else "muted"
        self.mast_count_badge.set_badge("已选 {} / {} 座塔".format(sel_count, total_count), badge_tone)
        self.submit_btn.config(text="🚀 批量提交排队 (已选 {} 座塔)".format(sel_count))

    def _select_all(self):
        self.selected_masts = set(range(len(self.masts)))
        for idx in range(len(self.masts)):
            row_id = str(idx)
            if self.tree.exists(row_id):
                vals = list(self.tree.item(row_id, "values"))
                vals[0] = "✔"
                self.tree.item(row_id, values=vals)
        self._update_selection_stats()

    def _select_none(self):
        self.selected_masts.clear()
        for idx in range(len(self.masts)):
            row_id = str(idx)
            if self.tree.exists(row_id):
                vals = list(self.tree.item(row_id, "values"))
                vals[0] = ""
                self.tree.item(row_id, values=vals)
        self._update_selection_stats()

    def _select_invert(self):
        all_set = set(range(len(self.masts)))
        self.selected_masts = all_set - self.selected_masts
        for idx in range(len(self.masts)):
            row_id = str(idx)
            if self.tree.exists(row_id):
                vals = list(self.tree.item(row_id, "values"))
                vals[0] = "✔" if idx in self.selected_masts else ""
                self.tree.item(row_id, values=vals)
        self._update_selection_stats()

    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region in ("tree", "cell"):
            item_id = self.tree.identify_row(event.y)
            if item_id:
                self._toggle_item(item_id)

    def _on_tree_space(self, _event):
        focus_id = self.tree.focus()
        if focus_id:
            self._toggle_item(focus_id)

    def _toggle_item(self, item_id):
        try:
            idx = int(item_id)
        except ValueError:
            return
        if idx in self.selected_masts:
            self.selected_masts.remove(idx)
            new_sym = ""
        else:
            self.selected_masts.add(idx)
            new_sym = "✔"
        vals = list(self.tree.item(item_id, "values"))
        vals[0] = new_sym
        self.tree.item(item_id, values=vals)
        self._update_selection_stats()

    def _fetch_flows(self):
        def _worker():
            flows = []
            try:
                payload = self.queue_window._get_json("/api/flows")
                flows = payload.get("flows", [])
            except Exception:
                pass

            def _apply():
                self.server_flows = flows
                combo_values = [f.get("name", "") for f in flows if f.get("name")]
                if combo_values:
                    self.flow_combo["values"] = combo_values
                    self.flow_combo.current(0)
                else:
                    self.flow_combo["values"] = ["(请选择本地流程文件)"]
                    self.flow_combo.current(0)

            self.queue_window._post_ui(_apply)

        threading.Thread(target=_worker, daemon=True).start()

    def _pick_local_flow(self):
        chosen = filedialog.askopenfilename(
            parent=self.dialog,
            title="选择自动化链路流程文件",
            filetypes=[("JSON 流程文件", "*.json"), ("所有文件", "*.*")],
        )
        if not chosen:
            return
        try:
            with open(chosen, "r", encoding="utf-8") as f:
                content = json.load(f)
            if not isinstance(content, dict):
                messagebox.showerror("选择流程失败", "所选文件不是有效的 JSON 对象。", parent=self.dialog)
                return
            self.local_flow_path = chosen
            self.local_flow_content = content
            flow_name = os.path.basename(chosen)
            combo_vals = list(self.flow_combo["values"])
            display_label = "[本地] {}".format(flow_name)
            if display_label not in combo_vals:
                combo_vals.insert(0, display_label)
                self.flow_combo["values"] = combo_vals
            self.flow_combo.set(display_label)
            self.status_lbl.config(text="已加载本地流程：{}".format(flow_name))
        except Exception as exc:
            messagebox.showerror("加载流程失败", str(exc), parent=self.dialog)

    def _start_batch_submit(self):
        if self._is_submitting:
            return
        user = self.user_var.get().strip()
        if not user:
            messagebox.showwarning("缺少参数", "请填写用户名。", parent=self.dialog)
            return

        if not self.selected_masts:
            messagebox.showwarning("缺少参数", "请至少勾选一座测风塔！", parent=self.dialog)
            return

        chosen_flow_text = self.flow_var.get().strip()
        if not chosen_flow_text or chosen_flow_text == "(请选择本地流程文件)":
            messagebox.showwarning("缺少参数", "请选择要执行的计算流程或指定本地流程文件。", parent=self.dialog)
            return

        work_dir = self.work_dir_var.get().strip()
        if not work_dir:
            messagebox.showwarning("缺少参数", "请指定项目工作文件夹。", parent=self.dialog)
            return

        try:
            priority = int(self.priority_var.get().strip() or 0)
        except ValueError:
            priority = 0

        try:
            max_attempts = max(1, int(self.max_attempts_var.get().strip() or 1))
        except ValueError:
            max_attempts = 1

        try:
            timeout_seconds = max(0, int(self.timeout_var.get().strip() or 0))
        except ValueError:
            timeout_seconds = 0

        selected_items = [self.masts[i] for i in sorted(self.selected_masts)]
        if not selected_items:
            messagebox.showinfo("提示", "未选中任何测风塔。", parent=self.dialog)
            return

        should_submit = messagebox.askyesno(
            "确认批量排队",
            f"确定要为选中的 {len(selected_items)} 座测风塔批量生成排队任务吗？\n\n"
            f"目标流程：{chosen_flow_text}\n"
            f"项目目录：{work_dir}",
            parent=self.dialog,
        )
        if not should_submit:
            return

        self._is_submitting = True
        self.submit_btn.config(state=tk.DISABLED, text="⏳ 正在批量提交中...")
        self.status_lbl.config(text="正在准备批量提交...")

        threading.Thread(
            target=self._submit_batch_masts_worker,
            args=(selected_items, chosen_flow_text, work_dir, user, priority, max_attempts, timeout_seconds),
            daemon=True,
        ).start()

    def _submit_batch_masts_worker(
        self,
        selected_masts,
        chosen_flow_text,
        work_dir,
        user,
        priority=0,
        max_attempts=1,
        timeout_seconds=0,
        completed_callback=None,
    ):
        """后台线程提交批量任务（纯逻辑，解耦 GUI）"""
        flow_path = ""
        # 1. 确定 flowPath（若是本地流程则先上传）
        if self.local_flow_content is not None and chosen_flow_text.startswith("[本地]"):
            try:
                self.queue_window._post_ui(
                    lambda: self.status_lbl.config(text="正在上传本地流程文件到服务器...")
                )
                upload_resp = self.queue_window._post_json(
                    "/api/flows/upload",
                    {
                        "name": os.path.basename(self.local_flow_path),
                        "content": self.local_flow_content,
                        "user": user,
                    },
                )
                flow_path = upload_resp.get("flowPath", "")
            except Exception as exc:
                self.queue_window._post_ui(
                    lambda: [
                        messagebox.showerror("上传流程失败", "上传流程失败：\n{}".format(exc), parent=self.dialog),
                        self.submit_btn.config(state=tk.NORMAL, text="🚀 批量提交排队"),
                        self.status_lbl.config(text="⚠️ 上传流程失败。"),
                    ]
                )
                self._is_submitting = False
                return
        else:
            # 服务端流程
            for f in self.server_flows:
                if f.get("name") == chosen_flow_text:
                    flow_path = f.get("path", "")
                    break

        if not flow_path:
            self.queue_window._post_ui(
                lambda: [
                    messagebox.showerror("提交失败", "未找到对应的流程文件路径。", parent=self.dialog),
                    self.submit_btn.config(state=tk.NORMAL, text="🚀 批量提交排队"),
                ]
            )
            self._is_submitting = False
            return

        success_count = 0
        fail_count = 0
        submitted_task_ids = []

        total = len(selected_masts)
        batch_prefix = "bat_{}".format(uuid.uuid4().hex[:8])
        for idx, mast in enumerate(selected_masts, start=1):
            mast_name = mast.get("mastName") or mast.get("mastId") or "mast_{}".format(idx)
            self.queue_window._post_ui(
                lambda i=idx, n=mast_name: self.status_lbl.config(
                    text="正在提交 ({}/{}): 测风塔 {}...".format(i, total, n)
                )
            )

            # 聚合单塔信息与工程目录全量运行时参数
            proj_params = {"mastId": mast_name, "mastName": mast_name}
            for k in ("latitude", "longitude", "elevation", "hubHeight", "utmX", "utmY"):
                v = mast.get(k)
                if not v:
                    abbrev_map = {"latitude": "lat", "longitude": "lon", "elevation": "elev"}
                    if k in abbrev_map:
                        v = mast.get(abbrev_map[k])
                if v:
                    proj_params[k] = str(v)

            runtime_cfg = {}
            if work_dir and os.path.isdir(work_dir) and wt_project_workdir_parser is not None:
                try:
                    parsed = wt_project_workdir_parser.parse_project_work_dir(
                        work_dir,
                        project_params=proj_params,
                        flow_path=flow_path if (flow_path and os.path.isfile(flow_path)) else None,
                    )
                    if parsed and isinstance(parsed.get("runtime_config"), dict):
                        runtime_cfg.update(parsed["runtime_config"])
                except Exception as parse_err:
                    wt_logging.get_logger("wt_queue").warning(
                        "parse project workdir for mast %s failed: %s", mast_name, parse_err
                    )

            runtime_cfg.setdefault("projectWorkDir", work_dir)
            runtime_cfg["mastId"] = mast_name
            runtime_cfg["mastName"] = mast_name
            for k in ("hubHeight", "elevation", "longitude", "latitude", "utmX", "utmY"):
                if proj_params.get(k):
                    runtime_cfg[k] = str(proj_params[k])

            idempotency_token = "{}_{}_{}".format(batch_prefix, mast_name, idx)
            payload = {
                "user": user,
                "flowPath": flow_path,
                "steps": "",
                "fromStep": "",
                "toStep": "",
                "priority": priority,
                "maxAttempts": max_attempts,
                "timeoutSeconds": timeout_seconds,
                "runtimeConfig": runtime_cfg,
                "idempotencyKey": idempotency_token,
                "clientToken": idempotency_token,
            }
            try:
                res = self.queue_window._post_json("/api/tasks/submit", payload)
                tid = (res.get("task") or {}).get("taskId") or ""
                if tid:
                    submitted_task_ids.append(tid)
                success_count += 1
            except Exception as exc:
                wt_logging.get_logger("wt_queue").warning("submit mast task %s failed: %s", mast_name, exc)
                fail_count += 1

        if completed_callback:
            completed_callback(submitted_task_ids, success_count, fail_count)

        def _finish_ui():
            self._is_submitting = False
            self.queue_window.refresh()
            msg = "批量提交完成：成功提交 {} 座测风塔计算任务{}".format(
                success_count,
                "，{} 座提交失败".format(fail_count) if fail_count else "",
            )
            messagebox.showinfo("批量排队完成", msg, parent=self.dialog)
            try:
                self.dialog.destroy()
            except Exception:
                pass

        self.queue_window._post_ui(_finish_ui)
