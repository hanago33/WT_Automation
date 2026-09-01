# encoding: utf-8

"""任务队列 + 服务器监控统一客户端窗口，供 WT_Launcher 使用。"""

import hashlib
import http.client
import json
import os
import threading
import tkinter as tk
import urllib.error
import urllib.parse
import urllib.request
from tkinter import filedialog, messagebox, ttk


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

MONITOR_STATUS_LABELS = {
    "idle": "空闲",
    "running": "运行中",
    "success": "成功",
    "failed": "失败",
    "unknown": "未知",
}


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
    ):
        self.on_settings_change = on_settings_change
        self.on_monitor_url_change = on_monitor_url_change
        self.on_start_service = on_start_service
        self.on_stop_service = on_stop_service
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

        self.window = tk.Toplevel(master)
        self.window.title("任务与服务器监控")
        self.window.configure(bg="#f4f7fb")
        self.window.geometry("1080x700")
        self.window.minsize(860, 560)
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)

        top = tk.Frame(self.window, bg="#eaf1fb", padx=10, pady=8)
        top.pack(fill=tk.X)
        tk.Label(top, text="任务服务地址", bg="#eaf1fb", fg="#1f2937").pack(side=tk.LEFT)
        self.url_var = tk.StringVar(value=self.base_url)
        tk.Entry(top, textvariable=self.url_var, width=22).pack(side=tk.LEFT, padx=6)
        tk.Label(top, text="用户名", bg="#eaf1fb", fg="#1f2937").pack(side=tk.LEFT)
        self.user_var = tk.StringVar(value=initial_user or "")
        tk.Entry(top, textvariable=self.user_var, width=12).pack(side=tk.LEFT, padx=6)
        tk.Label(top, text="服务令牌(--auth-token)", bg="#eaf1fb", fg="#1f2937").pack(side=tk.LEFT)
        self.token_var = tk.StringVar(value=initial_token or "")
        tk.Entry(top, textvariable=self.token_var, width=16, show="*").pack(
            side=tk.LEFT, padx=6
        )
        tk.Label(top, text="监控地址", bg="#eaf1fb", fg="#1f2937").pack(
            side=tk.LEFT, padx=(8, 0)
        )
        self.monitor_url_var = tk.StringVar(value=self.monitor_base_url)
        tk.Entry(top, textvariable=self.monitor_url_var, width=22).pack(
            side=tk.LEFT, padx=6
        )
        tk.Button(
            top,
            text="刷新",
            command=self._apply_settings_and_refresh,
            bg="#dbeafe",
            fg="#1f2937",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        self.url_var.trace_add("write", self._on_settings_text_changed)
        self.user_var.trace_add("write", self._on_user_changed)
        self.token_var.trace_add("write", self._on_settings_text_changed)
        self.monitor_url_var.trace_add("write", self._on_monitor_url_changed)

        hint = tk.Label(
            top,
            text="先填用户名/服务令牌，再启动服务或刷新；连接失败时请先在本窗口或总控台启动服务。",
            bg="#eaf1fb",
            fg="#64748b",
            anchor="e",
        )
        hint.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(10, 0))

        self.notebook = ttk.Notebook(self.window)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.queue_tab = tk.Frame(self.notebook, bg="#f4f7fb")
        self.monitor_tab = tk.Frame(self.notebook, bg="#f4f7fb")
        self.notebook.add(self.queue_tab, text="任务队列")
        self.notebook.add(self.monitor_tab, text="服务器监控")
        self._build_queue_tab(self.queue_tab)
        self._build_monitor_tab(self.monitor_tab)

        self._after_id = self.window.after(POLL_MS, self._poll_loop)
        self._monitor_after_id = self.window.after(POLL_MS, self._monitor_poll_loop)

    def _build_queue_tab(self, parent):
        action_frame = tk.Frame(parent, bg="#ffffff", padx=10, pady=6)
        action_frame.pack(fill=tk.X)
        for text, command in (
            ("提交任务", self.submit_task),
            ("提交本地 JSON 链路", self.submit_local_flow_file),
            ("暂停", lambda: self.control_action("pause")),
            ("继续", lambda: self.control_action("resume")),
            ("终止", lambda: self.control_action("terminate")),
            ("取消", lambda: self.control_action("cancel")),
            ("查看报告", self.view_report),
        ):
            tk.Button(
                action_frame,
                text=text,
                command=command,
                bg="#dbeafe",
                fg="#1f2937",
                relief=tk.FLAT,
                padx=12,
                pady=4,
                cursor="hand2",
            ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            action_frame,
            text="启动任务队列服务",
            command=lambda: self._service_action("start", "task"),
            bg="#dcfce7",
            fg="#14532d",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(14, 4))
        tk.Button(
            action_frame,
            text="停止任务队列服务",
            command=lambda: self._service_action("stop", "task"),
            bg="#fee2e2",
            fg="#7f1d1d",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT)
        self.conn_var = tk.StringVar(value="未连接")
        tk.Label(action_frame, textvariable=self.conn_var, bg="#ffffff", fg="#64748b").pack(
            side=tk.RIGHT
        )

        self.auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            action_frame,
            text="自动刷新 (2s)",
            variable=self.auto_var,
            bg="#ffffff",
            fg="#1f2937",
            activebackground="#ffffff",
            selectcolor="#ffffff",
        ).pack(side=tk.RIGHT, padx=(0, 10))
        self.mine_only_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            action_frame,
            text="只看我的任务",
            variable=self.mine_only_var,
            command=self._on_mine_only_toggle,
            bg="#ffffff",
            fg="#1f2937",
            activebackground="#ffffff",
            selectcolor="#ffffff",
        ).pack(side=tk.RIGHT, padx=(0, 10))

        filter_frame = tk.Frame(parent, bg="#ffffff", padx=10, pady=(0, 4))
        filter_frame.pack(fill=tk.X)
        tk.Label(filter_frame, text="状态筛选", bg="#ffffff", fg="#334155").pack(
            side=tk.LEFT
        )
        self.status_filter_var = tk.StringVar(value="全部")
        status_box = ttk.Combobox(
            filter_frame,
            textvariable=self.status_filter_var,
            values=("全部", "排队中", "运行中", "已暂停", "成功", "失败", "已取消", "已终止"),
            width=10,
            state="readonly",
        )
        status_box.pack(side=tk.LEFT, padx=(6, 12))
        status_box.bind("<<ComboboxSelected>>", self._on_filter_changed)
        tk.Label(filter_frame, text="关键字（任务ID/用户/流程/步骤）", bg="#ffffff", fg="#334155").pack(
            side=tk.LEFT
        )
        self.keyword_var = tk.StringVar()
        keyword_entry = tk.Entry(filter_frame, textvariable=self.keyword_var, width=34)
        keyword_entry.pack(side=tk.LEFT, padx=(6, 8))
        keyword_entry.bind("<Return>", self._on_filter_changed)
        tk.Button(
            filter_frame,
            text="应用筛选",
            command=self._on_filter_changed,
            bg="#dbeafe",
            fg="#1f2937",
            relief=tk.FLAT,
            padx=10,
            pady=2,
            cursor="hand2",
        ).pack(side=tk.LEFT)

        tree_frame = tk.LabelFrame(
            parent,
            text="当前队列与任务",
            padx=6,
            pady=6,
            bg="#ffffff",
            fg="#1f2937",
            bd=1,
            relief=tk.GROOVE,
        )
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)
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
        self.task_tree = ttk.Treeview(tree_frame, columns=columns, show="headings")
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
            "taskId": 190,
            "user": 70,
            "status": 70,
            "priority": 60,
            "scheduled": 150,
            "attempts": 60,
            "progress": 110,
            "currentStep": 180,
            "created": 150,
            "updated": 150,
        }
        for column in columns:
            self.task_tree.heading(column, text=headings[column])
            self.task_tree.column(column, width=widths[column], minwidth=50, anchor="w")
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=scrollbar.set)
        self.task_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        h_scroll = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.task_tree.xview)
        self.task_tree.configure(xscrollcommand=h_scroll.set)
        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_select)

        stats_frame = tk.LabelFrame(
            parent,
            text="队列统计",
            padx=8,
            pady=4,
            bg="#ffffff",
            fg="#1f2937",
            bd=1,
            relief=tk.GROOVE,
        )
        stats_frame.pack(fill=tk.X, padx=10, pady=(0, 8))
        self.stats_var = tk.StringVar(value="尚未获取统计")
        tk.Label(
            stats_frame,
            textvariable=self.stats_var,
            bg="#ffffff",
            fg="#334155",
            anchor="w",
        ).pack(fill=tk.X)

        log_frame = tk.LabelFrame(
            parent,
            text="任务日志",
            padx=6,
            pady=6,
            bg="#ffffff",
            fg="#1f2937",
            bd=1,
            relief=tk.GROOVE,
        )
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        text_frame = tk.Frame(log_frame, bg="#111418")
        text_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.log_text = tk.Text(
            text_frame,
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
        log_scrollbar = tk.Scrollbar(text_frame, command=self.log_text.yview, relief=tk.FLAT)
        self.log_text.config(yscrollcommand=log_scrollbar.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        for tag, color in (
            ("info", "#e6edf3"),
            ("error", "#ff7b72"),
            ("warning", "#e3b341"),
            ("success", "#7ee787"),
            ("system", "#79c0ff"),
        ):
            self.log_text.tag_configure(tag, foreground=color)

    def _build_monitor_tab(self, parent):
        top = tk.Frame(parent, bg="#eaf1fb", padx=10, pady=8)
        top.pack(fill=tk.X)
        self.monitor_auto_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            top,
            text="自动刷新 (2s)",
            variable=self.monitor_auto_var,
            bg="#eaf1fb",
            fg="#1f2937",
            activebackground="#eaf1fb",
            selectcolor="#ffffff",
        ).pack(side=tk.LEFT)
        tk.Button(
            top,
            text="刷新监控",
            command=self.refresh_monitor,
            bg="#dbeafe",
            fg="#1f2937",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(10, 0))
        tk.Button(
            top,
            text="启动监控服务",
            command=lambda: self._service_action("start", "monitor"),
            bg="#dcfce7",
            fg="#14532d",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(10, 0))
        tk.Button(
            top,
            text="停止监控服务",
            command=lambda: self._service_action("stop", "monitor"),
            bg="#fee2e2",
            fg="#7f1d1d",
            relief=tk.FLAT,
            padx=10,
            pady=4,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(10, 0))

        status_frame = tk.LabelFrame(
            parent,
            text="运行状态（只读，来自 8767 监控服务）",
            padx=10,
            pady=8,
            bg="#ffffff",
            fg="#1f2937",
            bd=1,
            relief=tk.GROOVE,
        )
        status_frame.pack(fill=tk.X, padx=10, pady=8)
        self.monitor_conn_var = tk.StringVar(value="未连接")
        self.run_status_var = tk.StringVar(value="未知")
        self.activity_var = tk.StringVar(value="-")
        self.error_var = tk.StringVar(value="-")
        self.updated_var = tk.StringVar(value="-")
        rows = [
            ("连接状态", self.monitor_conn_var),
            ("运行状态", self.run_status_var),
            ("当前活动", self.activity_var),
            ("最后错误", self.error_var),
            ("更新时间", self.updated_var),
        ]
        for row_index, (label, var) in enumerate(rows):
            tk.Label(status_frame, text=label, bg="#ffffff", fg="#64748b").grid(
                row=row_index, column=0, sticky="w", padx=(0, 10), pady=2
            )
            tk.Label(status_frame, textvariable=var, bg="#ffffff", fg="#1f2937").grid(
                row=row_index, column=1, sticky="w"
            )

        log_frame = tk.LabelFrame(
            parent,
            text="运行日志（只读，最后 300 行）",
            padx=6,
            pady=6,
            bg="#ffffff",
            fg="#1f2937",
            bd=1,
            relief=tk.GROOVE,
        )
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        text_frame = tk.Frame(log_frame, bg="#111418")
        text_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.monitor_log_text = tk.Text(
            text_frame,
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
        log_scrollbar = tk.Scrollbar(text_frame, command=self.monitor_log_text.yview, relief=tk.FLAT)
        self.monitor_log_text.config(yscrollcommand=log_scrollbar.set)
        self.monitor_log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        for tag, color in (
            ("info", "#e6edf3"),
            ("error", "#ff7b72"),
            ("warning", "#e3b341"),
            ("success", "#7ee787"),
            ("system", "#79c0ff"),
        ):
            self.monitor_log_text.tag_configure(tag, foreground=color)

    def _on_settings_text_changed(self, *_args):
        self._save_settings()

    def _on_user_changed(self, *_args):
        if not self.user_var.get().strip() and self.mine_only_var.get():
            self.mine_only_var.set(False)
        self._save_settings()

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
            self.base_url = url
            self._http_conn = None
        monitor_url = self.monitor_url_var.get().strip().rstrip("/")
        if monitor_url:
            self.monitor_base_url = monitor_url
        self._save_settings()
        self.refresh()
        self.refresh_monitor()

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
        self._save_settings()
        self.refresh()

    def _task_list_path(self):
        if not self.mine_only_var.get():
            return "/api/tasks?scope=all"
        user = self.user_var.get().strip()
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
        try:
            status_payload = self._get_json_monitor("/api/status")
            logs_payload = self._get_json_monitor("/api/logs?tail=300")
        except Exception as exc:
            self._post_ui(lambda: self._mark_monitor_offline(str(exc)))
            self._monitor_fetching = False
            return
        self._post_ui(
            lambda: self._apply_monitor_payload(status_payload, logs_payload)
        )
        self._monitor_fetching = False

    def _apply_tasks(self, tasks, stats=None):
        self.conn_var.set("已连接")
        self._queue_fail_streak = 0
        self._apply_stats(stats)
        self._cached_tasks = list(tasks)
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

    def _render_task_list(self, tasks):
        selected = self._selected_task_id()
        self.task_tree.delete(*self.task_tree.get_children())
        for task in tasks:
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
            self.task_tree.insert(
                "",
                tk.END,
                iid=task.get("taskId", ""),
                values=(
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
                ),
            )
        if selected and self.task_tree.exists(selected):
            self.task_tree.selection_set(selected)
            self.task_tree.see(selected)

    def _apply_monitor_payload(self, status_payload, logs_payload):
        self.monitor_conn_var.set("已连接")
        self._monitor_fail_streak = 0
        status = str(status_payload.get("status", "unknown"))
        self.run_status_var.set(MONITOR_STATUS_LABELS.get(status, status))
        self.activity_var.set(str(status_payload.get("activity") or "-"))
        self.error_var.set(str(status_payload.get("error") or "-"))
        self.updated_var.set(str(status_payload.get("updatedAt") or "-"))
        self._render_monitor_logs(logs_payload.get("lines", []))

    def _apply_stats(self, stats):
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

    def _selected_task_id(self):
        selection = self.task_tree.selection()
        return selection[0] if selection else ""

    def _on_task_select(self, _event=None):
        task_id = self._selected_task_id()
        if task_id:
            self._log_fetching = True
            threading.Thread(
                target=self._fetch_logs_worker, args=(task_id,), daemon=True
            ).start()

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

    def _render_logs(self, lines):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        for line in lines:
            self.log_text.insert(
                tk.END, str(line) + "\n", self._classify_line(str(line))
            )
        self.log_text.config(state=tk.DISABLED)
        self.log_text.see(tk.END)

    def _render_monitor_logs(self, lines):
        self.monitor_log_text.config(state=tk.NORMAL)
        self.monitor_log_text.delete("1.0", tk.END)
        for line in lines:
            self.monitor_log_text.insert(
                tk.END, str(line) + "\n", self._classify_line(str(line))
            )
        self.monitor_log_text.config(state=tk.DISABLED)
        self.monitor_log_text.see(tk.END)

    @staticmethod
    def _classify_line(line):
        if any(key in line for key in ("错误", "失败", "ERROR", "Traceback", "Exception")):
            return "error"
        if any(key in line for key in ("警告", "WARN", "Warning")):
            return "warning"
        if any(key in line for key in ("完成", "成功", "SUCCESS")):
            return "success"
        if any(key in line for key in ("queue", "启动", "开始")):
            return "system"
        return "info"

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
        self.conn_var.set("未连接：{}".format(self._friendly_error(message)))

    def _mark_monitor_offline(self, message):
        self._monitor_fail_streak += 1
        self.monitor_conn_var.set("未连接：{}".format(self._friendly_error(message)))
        self.run_status_var.set("未知")
        self.activity_var.set("-")
        self.error_var.set("-")
        self.updated_var.set("-")

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
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self.token_var.get().strip(),
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
                        raise urllib.error.HTTPError(
                            full, status, response.reason or "", {}, None
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
        user = self.user_var.get().strip()
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
        try:
            payload = self._post_json(
                "/api/flows/upload",
                {
                    "name": os.path.basename(file_path),
                    "content": content,
                    "user": self.user_var.get().strip(),
                },
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            messagebox.showerror(
                "上传流程失败", "{} {}".format(exc.code, detail), parent=dialog
            )
            return
        except Exception as exc:
            messagebox.showerror(
                "上传流程失败", self._friendly_error(exc), parent=dialog
            )
            return
        flow = {
            "name": payload.get("name") or os.path.basename(file_path),
            "path": payload.get("flowPath", ""),
        }
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
            self._post_ui(
                lambda t=title, i=index, n=len(sections): self._append_log_text(
                    "[queue] 提交 {}/{}：{}".format(i, n, t)
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
                known = known_flows.get(os.path.basename(path).lower())
                if known and known.get("path") and known.get("sha256") == digest:
                    flow_path = known["path"]
                    skipped_upload = True
                else:
                    upload = self._post_json(
                        "/api/flows/upload",
                        {
                            "name": os.path.basename(path),
                            "content": content,
                            "user": user,
                        },
                    )
                    flow_path = upload.get("flowPath", "")
                    skipped_upload = False
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
                results.append(
                    (title, "已提交（内容未变化，跳过上传）" if skipped_upload else "已提交")
                )
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
        success_reasons = ("已提交", "已提交（内容未变化，跳过上传）")
        success = [item for item in results if item[1] in success_reasons]
        failed = [item for item in results if item[1] not in success_reasons]
        skipped = sum(1 for item in results if item[1] == "已提交（内容未变化，跳过上传）")
        skip_text = "，跳过上传 {} 个".format(skipped) if skipped else ""
        self._append_log_text(
            "[queue] 提交完成：成功 {} 个，失败 {} 个{}".format(
                len(success),
                len(failed),
                skip_text,
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
        except Exception:
            return False

    def _append_log_text(self, line):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(
            tk.END, str(line) + "\n", self._classify_line(str(line))
        )
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

    def control_action(self, action):
        task_id = self._selected_task_id()
        if not task_id:
            messagebox.showinfo("任务队列", "请先在列表中选择一个任务。")
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
            detail = exc.read().decode("utf-8", errors="replace")
            self._post_ui(
                lambda: messagebox.showerror(
                    "操作失败", "{} {}".format(exc.code, detail)
                )
            )
            return
        except Exception as exc:
            self._post_ui(
                lambda: messagebox.showerror("操作失败", self._friendly_error(exc))
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
            detail = exc.read().decode("utf-8", errors="replace")
            self._post_ui(
                lambda: messagebox.showerror(
                    "查看报告失败", "{} {}".format(exc.code, detail)
                )
            )
            return
        except Exception as exc:
            self._post_ui(
                lambda: messagebox.showerror(
                    "查看报告失败", self._friendly_error(exc)
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
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
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
