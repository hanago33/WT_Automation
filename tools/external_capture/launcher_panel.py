# encoding: utf-8
"""外部控件采集对话框 —— 集成进 WT_Launcher 总控台的统一入口。

把 uia-peek / axe-windows 两个适配器的调用收进一个 Toplevel 对话框：
  - UiaPeek 服务：自动后台拉起 UiaPeek.exe、检测状态、停止；
  - peek：焦点元素 / 屏幕坐标；录制 N 秒键鼠事件流；
  - axe-windows：按进程 ID 调 CLI 或 bridge 扫描；
  - 路径配置：记忆 UiaPeek.exe / AxeWindowsCLI.exe 路径。

所有耗时操作在后台线程执行，结果通过 after 回调更新 UI，不阻塞总控台。
WT_Launcher.py 只需：
    from tools.external_capture.launcher_panel import ExternalCaptureDialog
    ExternalCaptureDialog(self.root, self.theme, log_callback=self._append_log)
"""
import json
import os
import subprocess
import ctypes
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

from . import uiapeek_client as up
from . import axewindows_client as aw

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CONFIG_FILE = os.path.join(REPO_ROOT, "external_capture_config.json")
DEFAULT_UIAPEEK_BASE_URL = up.DEFAULT_BASE_URL


def _is_admin():
    """当前进程是否以管理员（高完整性）运行。"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _start_elevated(exe, cwd):
    """用 ShellExecute runas 以管理员提权启动 exe（弹 UAC）。返回是否成功 (>32)。"""
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, None, cwd, 1)
        return rc > 32
    except Exception:
        return False


def _kill_uiapeek():
    """按进程名结束 UiaPeek（用于停止 runas 提权启动的实例）。"""
    try:
        subprocess.run(["taskkill", "/F", "/IM", "UiaPeek.exe"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        pass


def _load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def _save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


class ExternalCaptureDialog:
    """总控台内的外部控件采集统一对话框。"""

    def __init__(self, parent, theme, log_callback=None):
        self.parent = parent
        self.theme = dict(theme or {})
        self.log_callback = log_callback
        self.cfg = _load_config()
        self._uiapeek_proc = None
        self._uiapeek_elevated = False

        self.var_uiapeek_exe = tk.StringVar(value=self.cfg.get("uiapeek_exe", ""))
        self.var_axe_cli_exe = tk.StringVar(value=self.cfg.get("axe_cli_exe", ""))
        self.var_base_url = tk.StringVar(value=self.cfg.get("uiapeek_base_url", DEFAULT_UIAPEEK_BASE_URL))
        self.var_service_status = tk.StringVar(value="未检测")
        self.var_peek_x = tk.StringVar(value="0")
        self.var_peek_y = tk.StringVar(value="0")
        self.var_record_seconds = tk.StringVar(value="10")
        self.var_pid = tk.StringVar(value="")
        self.var_result = tk.StringVar(value="尚无结果")

        self.window = tk.Toplevel(parent)
        self.window.title("外部控件采集（uia-peek / axe-windows）")
        self.window.geometry("780x720")
        self.window.minsize(700, 640)
        self.window.configure(bg=self.theme.get("bg", "#eef3f9"))
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build_ui()
        self.window.after(300, self._refresh_service_status)

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        bg = self.theme.get("bg", "#eef3f9")
        card = self.theme.get("card", "#ffffff")
        text_c = self.theme.get("text", "#1f2d3d")
        muted = self.theme.get("muted", "#5f6f82")

        container = tk.Frame(self.window, bg=bg, padx=12, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            container,
            text="在总控台内调用控件采集。peek 功能无需任何外部下载——UiaPeek 服务未运行时"
            "自动用内置 pywinauto 后端（按坐标/焦点取控件祖先链）。\n"
            "UiaPeek 服务（需下载 release + 管理员）与 axe-windows（需 .NET）为可选增强："
            "UiaPeek 提供实时录制流，axe-windows 提供控件 Patterns。"
            "结果均保存到 control_maps/，可被标准控件库合并。",
            justify=tk.LEFT, anchor="w", wraplength=740, bg=card, fg=muted,
        ).pack(fill=tk.X)

        # ---- UiaPeek 服务区 ----
        svc = tk.LabelFrame(container, text="UiaPeek 服务", padx=10, pady=10, bg=card, fg=text_c)
        svc.pack(fill=tk.X, pady=(10, 0))
        row = tk.Frame(svc, bg=card)
        row.pack(fill=tk.X)
        tk.Button(row, text="启动服务", command=self.start_uiapeek_service).pack(side=tk.LEFT)
        tk.Button(row, text="停止服务", command=self.stop_uiapeek_service).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(row, text="检测状态", command=self._refresh_service_status).pack(side=tk.LEFT, padx=(6, 0))
        tk.Label(row, text="状态：", bg=card).pack(side=tk.LEFT, padx=(12, 0))
        tk.Label(row, textvariable=self.var_service_status, bg=card,
                 fg=self.theme.get("accent", "#2d7ff9")).pack(side=tk.LEFT)

        path_row = tk.Frame(svc, bg=card)
        path_row.pack(fill=tk.X, pady=(8, 0))
        tk.Label(path_row, text="UiaPeek.exe 路径：", bg=card).pack(side=tk.LEFT)
        tk.Entry(path_row, textvariable=self.var_uiapeek_exe).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        tk.Button(path_row, text="浏览…", command=self._browse_uiapeek_exe).pack(side=tk.LEFT)

        url_row = tk.Frame(svc, bg=card)
        url_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(url_row, text="服务地址：", bg=card).pack(side=tk.LEFT)
        tk.Entry(url_row, textvariable=self.var_base_url, width=30).pack(side=tk.LEFT, padx=(6, 0))

        # ---- peek 操作区 ----
        peek = tk.LabelFrame(container, text="UiaPeek 采集", padx=10, pady=10, bg=card, fg=text_c)
        peek.pack(fill=tk.X, pady=(10, 0))
        prow = tk.Frame(peek, bg=card)
        prow.pack(fill=tk.X)
        tk.Button(prow, text="peek 焦点元素", command=self.peek_focused).pack(side=tk.LEFT)
        tk.Label(prow, text="  坐标 X：", bg=card).pack(side=tk.LEFT, padx=(10, 0))
        tk.Entry(prow, textvariable=self.var_peek_x, width=6).pack(side=tk.LEFT)
        tk.Label(prow, text="Y：", bg=card).pack(side=tk.LEFT, padx=(4, 0))
        tk.Entry(prow, textvariable=self.var_peek_y, width=6).pack(side=tk.LEFT)
        tk.Button(prow, text="peek 坐标", command=self.peek_at).pack(side=tk.LEFT, padx=(6, 0))

        rrow = tk.Frame(peek, bg=card)
        rrow.pack(fill=tk.X, pady=(6, 0))
        tk.Button(rrow, text="录制键鼠事件流", command=self.record_events).pack(side=tk.LEFT)
        tk.Label(rrow, text="  秒数：", bg=card).pack(side=tk.LEFT, padx=(10, 0))
        tk.Entry(rrow, textvariable=self.var_record_seconds, width=6).pack(side=tk.LEFT)
        tk.Label(rrow, text="（需 pip install signalrcore）", bg=card, fg=muted).pack(side=tk.LEFT, padx=(6, 0))

        # ---- axe-windows 区 ----
        axe = tk.LabelFrame(container, text="Axe.Windows 扫描（补充 Patterns）", padx=10, pady=10, bg=card, fg=text_c)
        axe.pack(fill=tk.X, pady=(10, 0))
        arow = tk.Frame(axe, bg=card)
        arow.pack(fill=tk.X)
        tk.Label(arow, text="目标进程 PID：", bg=card).pack(side=tk.LEFT)
        tk.Entry(arow, textvariable=self.var_pid, width=10).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(arow, text="CLI 扫描", command=self.axe_scan_cli).pack(side=tk.LEFT, padx=(10, 0))
        tk.Button(arow, text="Bridge 扫描(含 Patterns)", command=self.axe_scan_bridge).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(arow, text="查找 CLI", command=self.axe_find_cli).pack(side=tk.LEFT, padx=(6, 0))

        crow = tk.Frame(axe, bg=card)
        crow.pack(fill=tk.X, pady=(6, 0))
        tk.Label(crow, text="AxeWindowsCLI.exe 路径（可选）：", bg=card).pack(side=tk.LEFT)
        tk.Entry(crow, textvariable=self.var_axe_cli_exe).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 6))
        tk.Button(crow, text="浏览…", command=self._browse_axe_cli_exe).pack(side=tk.LEFT)

        # ---- 结果区 ----
        res = tk.LabelFrame(container, text="采集结果", padx=10, pady=10, bg=card, fg=text_c)
        res.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.result_text = scrolledtext.ScrolledText(res, height=12, wrap=tk.WORD, font=("Consolas", 9))
        self.result_text.pack(fill=tk.BOTH, expand=True)
        self.result_text.configure(state=tk.DISABLED)

        tk.Label(container, textvariable=self.var_result, anchor="w", bg=bg, fg=muted).pack(fill=tk.X, pady=(6, 0))

    # ----------------------------------------------------------- 路径配置
    def _browse_uiapeek_exe(self):
        fp = filedialog.askopenfilename(
            title="选择 UiaPeek.exe", filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if fp:
            self.var_uiapeek_exe.set(fp)
            self._persist_paths()

    def _browse_axe_cli_exe(self):
        fp = filedialog.askopenfilename(
            title="选择 AxeWindowsCLI.exe", filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if fp:
            self.var_axe_cli_exe.set(fp)
            self._persist_paths()

    def _persist_paths(self):
        self.cfg["uiapeek_exe"] = self.var_uiapeek_exe.get().strip()
        self.cfg["axe_cli_exe"] = self.var_axe_cli_exe.get().strip()
        self.cfg["uiapeek_base_url"] = self.var_base_url.get().strip() or DEFAULT_UIAPEEK_BASE_URL
        _save_config(self.cfg)

    def _base_url(self):
        return self.var_base_url.get().strip() or DEFAULT_UIAPEEK_BASE_URL

    # ----------------------------------------------------------- UiaPeek 服务
    def _refresh_service_status(self):
        def work():
            return up.ping(self._base_url())

        def done(ok):
            self.var_service_status.set("运行中" if ok else "未运行")

        self._run_async(work, done)

    def start_uiapeek_service(self):
        exe = self.var_uiapeek_exe.get().strip()
        if not exe:
            messagebox.showinfo(
                "提示",
                "请先通过“浏览…”选择 UiaPeek.exe 路径。\n"
                "从 https://github.com/g4-api/uia-peek/releases 下载解压后选择 UiaPeek.exe。",
                parent=self.window)
            return
        if not os.path.isfile(exe):
            messagebox.showerror("错误", "文件不存在：{}".format(exe), parent=self.window)
            return
        if up.ping(self._base_url()):
            self.var_service_status.set("运行中")
            messagebox.showinfo("提示", "UiaPeek 服务已在运行。", parent=self.window)
            return

        self._persist_paths()
        self.var_service_status.set("启动中…")
        self._uiapeek_elevated = False
        try:
            if _is_admin():
                self._uiapeek_proc = subprocess.Popen(
                    [exe], cwd=os.path.dirname(exe),
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                # 非管理员：UiaPeek 的全局键鼠钩子需要管理员，改用 runas 提权启动（弹 UAC）
                if _start_elevated(exe, os.path.dirname(exe)):
                    self._uiapeek_elevated = True
                else:
                    self.var_service_status.set("启动失败")
                    messagebox.showerror(
                        "启动失败",
                        "无法以管理员权限启动 UiaPeek（UAC 被拒绝或不可用）。\n"
                        "请改为：右键“启动WT自动化总控台.bat”选择“以管理员身份运行”，\n"
                        "或手动以管理员运行 UiaPeek.exe 后点“检测状态”。",
                        parent=self.window)
                    return
        except Exception as exc:
            self.var_service_status.set("启动失败")
            messagebox.showerror("启动失败", str(exc), parent=self.window)
            return

        def wait_ready():
            for _ in range(40):  # 最多等 ~20s
                if up.ping(self._base_url()):
                    return True
                if self._uiapeek_proc and self._uiapeek_proc.poll() is not None:
                    return False
                time.sleep(0.5)
            return False

        def done(ok):
            if ok:
                self.var_service_status.set("运行中")
                self._log("UiaPeek 服务已启动。", "success")
            else:
                self.var_service_status.set("启动失败")
                messagebox.showwarning(
                    "启动失败",
                    "UiaPeek 进程未就绪。可能需要管理员权限（全局键鼠钩子）。\n"
                    "请以管理员身份运行总控台，或手动以管理员运行 UiaPeek.exe 后点“检测状态”。",
                    parent=self.window)

        self._run_async(wait_ready, done)

    def stop_uiapeek_service(self):
        if self._uiapeek_elevated:
            _kill_uiapeek()
            self._uiapeek_elevated = False
            self._uiapeek_proc = None
            self.var_service_status.set("已停止")
            self._log("已停止（管理员提权启动的）UiaPeek 服务。", "system")
            messagebox.showinfo(
                "提示",
                "已结束 UiaPeek.exe 进程。\n若服务是手动启动的，请直接关闭 UiaPeek.exe 窗口。",
                parent=self.window)
            return
        if self._uiapeek_proc and self._uiapeek_proc.poll() is None:
            try:
                self._uiapeek_proc.terminate()
                self._uiapeek_proc.wait(timeout=5)
            except Exception:
                try:
                    self._uiapeek_proc.kill()
                except Exception:
                    pass
            self._uiapeek_proc = None
            self.var_service_status.set("已停止")
            self._log("已停止本对话框启动的 UiaPeek 服务。", "system")
        else:
            self.var_service_status.set("未由本对话框启动")
        messagebox.showinfo(
            "提示",
            "仅可停止由本对话框启动的 UiaPeek 进程。\n"
            "若服务是手动启动的，请直接关闭 UiaPeek.exe 窗口。",
            parent=self.window)

    # ----------------------------------------------------------- peek 操作
    def _peek_focused_with_fallback(self):
        """先试 UiaPeek 服务；未运行/失败则自动 fallback 到 pywinauto 后端。"""
        if up.ping(self._base_url()):
            try:
                return up.capture_focused(base_url=self._base_url(), save=True)
            except Exception:
                pass  # 服务在但调用失败，走 fallback
        from . import pywinauto_backend as pwb
        return pwb.capture_focused(save=True)

    def _peek_at_with_fallback(self, x, y):
        """先试 UiaPeek 服务；未运行/失败则自动 fallback 到 pywinauto 后端。"""
        if up.ping(self._base_url()):
            try:
                return up.capture_at(x, y, base_url=self._base_url(), save=True)
            except Exception:
                pass
        from . import pywinauto_backend as pwb
        return pwb.capture_at(x, y, save=True)

    def _set_result_text(self, text):
        self.result_text.configure(state=tk.NORMAL)
        self.result_text.delete("1.0", tk.END)
        self.result_text.insert(tk.END, text)
        self.result_text.configure(state=tk.DISABLED)

    def _format_payload(self, payload, fp=None):
        if not payload:
            return "（无结果）"
        meta = payload.get("scanMeta", {})
        tw = payload.get("targetWindow", {})
        lines = ["窗口: {} | 来源: {} | 控件数: {} | 链深: {}".format(
            tw.get("title", ""), meta.get("source", ""),
            meta.get("totalControls", 0), meta.get("pathDepth", 0))]
        for c in payload.get("controlDefinitions", []):
            flag = "  <- 触发元素" if c.get("isTriggerElement") else ""
            pats = c.get("inspectData", {}).get("patterns", [])
            pats_s = ("  patterns={}".format(pats)) if pats else ""
            lines.append("  [{:>2}] {:<14} aid={!r:<28} name={!r}{}{}".format(
                c.get("index", ""), c.get("controlType", ""),
                c.get("inspectData", {}).get("automationId"), c.get("name"), flag, pats_s))
        if fp:
            lines.append("\n已保存: {}".format(fp))
        return "\n".join(lines)

    def peek_focused(self):
        def work():
            return self._peek_focused_with_fallback()

        def done(result):
            exc, payload, fp = self._unpack(result)
            if exc:
                self._on_error("peek 焦点", exc)
                return
            self._set_result_text(self._format_payload(payload, fp))
            self.var_result.set("已保存: {}".format(fp) if fp else "未落盘")
            self._log("{} peek 焦点完成：{} 个控件。".format(
                payload.get("scanMeta", {}).get("source", "UiaPeek"),
                len(payload.get("controlDefinitions", []))), "success")

        self._run_async(work, done)

    def peek_at(self):
        try:
            x = int(self.var_peek_x.get())
            y = int(self.var_peek_y.get())
        except ValueError:
            messagebox.showerror("错误", "坐标需为整数", parent=self.window)
            return

        def work():
            return self._peek_at_with_fallback(x, y)

        def done(result):
            exc, payload, fp = self._unpack(result)
            if exc:
                self._on_error("peek 坐标", exc)
                return
            self._set_result_text(self._format_payload(payload, fp))
            self.var_result.set("已保存: {}".format(fp) if fp else "未落盘")
            self._log("{} peek ({},{}) 完成。".format(
                payload.get("scanMeta", {}).get("source", "UiaPeek"), x, y), "success")

        self._run_async(work, done)

    def record_events(self):
        try:
            seconds = int(self.var_record_seconds.get())
        except ValueError:
            messagebox.showerror("错误", "秒数需为整数", parent=self.window)
            return

        def work():
            evs = up.record_events(seconds, base_url=self._base_url())
            fp = None
            if evs:
                ts = time.strftime("%Y%m%d_%H%M%S")
                fp = os.path.join(up.CONTROL_MAP_DIR, "{}_uiapeek_recording.json".format(ts))
                os.makedirs(up.CONTROL_MAP_DIR, exist_ok=True)
                with open(fp, "w", encoding="utf-8") as fh:
                    json.dump(evs, fh, ensure_ascii=False, indent=2)
            return evs, fp

        def done(result):
            if isinstance(result, Exception):
                self._on_error("录制", result)
                return
            evs, fp = result
            self._set_result_text("收到 {} 个事件。\n{}".format(
                len(evs), "已保存: {}".format(fp) if fp else "未保存"))
            self.var_result.set("录制 {} 个事件".format(len(evs)))
            self._log("UiaPeek 录制完成：{} 个事件。".format(len(evs)), "success")

        self.var_result.set("录制中…")
        self._set_result_text("录制中（{} 秒）：请在 WT 软件中操作鼠标/键盘，结束后自动汇总。".format(seconds))
        self._run_async(work, done)

    # ----------------------------------------------------------- axe-windows
    def _resolve_pid(self):
        try:
            return int(self.var_pid.get().strip())
        except ValueError:
            messagebox.showerror("错误", "请输入有效的进程 PID（整数）", parent=self.window)
            return None

    def axe_find_cli(self):
        cli = aw.find_cli_exe()
        bridge = aw.find_bridge_exe()
        msg = "AxeWindowsCLI.exe: {}\nAxeBridge.exe: {}".format(
            cli or "（未找到，请装 MSI）", bridge or "（未编译）")
        self._set_result_text(msg)
        if cli:
            self.var_axe_cli_exe.set(cli)
            self._persist_paths()

    def axe_scan_cli(self):
        pid = self._resolve_pid()
        if pid is None:
            return
        cli_exe = self.var_axe_cli_exe.get().strip() or None

        def work():
            payload = aw.scan_process(pid, cli_exe=cli_exe)
            fp = aw.save_payload(payload)
            return payload, fp

        def done(result):
            exc, payload, fp = self._unpack(result)
            if exc:
                self._on_error("axe CLI 扫描", exc)
                return
            self._set_result_text(self._format_payload(payload, fp))
            a11y = (payload.get("scanMeta") or {}).get("a11ytestFiles") or []
            self.var_result.set("元素 {} | a11ytest {} 个".format(
                len(payload.get("controlDefinitions", [])), len(a11y)))
            self._log("axe-windows CLI 扫描完成。", "success")

        self._run_async(work, done)

    def axe_scan_bridge(self):
        pid = self._resolve_pid()
        if pid is None:
            return

        def work():
            payload = aw.scan_via_bridge(pid)
            fp = aw.save_payload(payload)
            return payload, fp

        def done(result):
            exc, payload, fp = self._unpack(result)
            if exc:
                self._on_error("axe bridge 扫描", exc)
                return
            self._set_result_text(self._format_payload(payload, fp))
            self.var_result.set("元素 {}（含 Patterns）".format(len(payload.get("controlDefinitions", []))))
            self._log("axe-windows bridge 扫描完成。", "success")

        self._run_async(work, done)

    # ----------------------------------------------------------- 工具
    def _run_async(self, work, done):
        def runner():
            try:
                result = work()
            except Exception as exc:  # noqa
                result = exc
            try:
                self.window.after(0, lambda: done(result))
            except Exception:
                pass

        threading.Thread(target=runner, daemon=True).start()

    @staticmethod
    def _unpack(result):
        """统一解包：work 抛异常时 result 是 Exception，否则是 (payload, fp)。"""
        if isinstance(result, Exception):
            return result, None, None
        if isinstance(result, tuple) and len(result) == 2:
            payload, fp = result
            return None, payload, fp
        return None, result, None

    def _on_error(self, action, exc):
        msg = str(exc)
        self.var_result.set("{} 失败".format(action))
        self._set_result_text("{} 失败：\n{}".format(action, msg))
        self._log("{} 失败：{}".format(action, msg), "error")
        if "不可达" in msg or "未运行" in msg or "UiaPeek" in msg:
            messagebox.showwarning(
                "{} 失败".format(action),
                msg + "\n\n请确认 UiaPeek 服务已启动（点“启动服务”或“检测状态”）。",
                parent=self.window)

    def _log(self, message, tag="system"):
        if self.log_callback:
            try:
                self.log_callback(message, tag=tag)
                return
            except TypeError:
                try:
                    self.log_callback(message)
                    return
                except Exception:
                    pass
        # 无回调则打到 stderr
        try:
            import sys as _sys
            _sys.stderr.write(message + "\n")
        except Exception:
            pass

    def _on_close(self):
        # 关闭对话框时不停 UiaPeek 服务（用户可能想继续用），仅释放窗口
        self.window.destroy()
