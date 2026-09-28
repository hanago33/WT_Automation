# -*- coding: utf-8 -*-
"""通用化极简启动器（去 WT/MUP 绑定，方案 A）。

提供最小 GUI 总控台：配置目标软件 -> 运行 generic_automation.run_automation。
- 目标软件识别完全由用户配置（exe 路径 + 可选窗口标题正则），不写死 WT/MUP。
- 提权检测改为"按配置的目标进程名判断"，不再硬编码 smartclient/meteodyn。
- 不复制 WT_Launcher.py 的 7347 行 GUI，仅保留运行所需的最小控件。

运行（在已部署完整运行版环境的机器上）：
    python tools/generic_flow/generic_launcher.py
"""
import os
import sys
import re
import ctypes
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))  # 项目根（含 wt_* 通用基础设施）
for p in (HERE, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

import generic_automation  # 通用化主程序副本

_TARGET_PROCESS_KEYWORD = ""  # 通用化：目标进程名关键词，由配置注入（提权检测用）


# ── 通用化自动提权（UIPI 内容树隔离处置，参照原 WT_Launcher，去 WT 硬编码）─────────
_ELEVATED_RELAUNCH_FLAG = "--generic-launched-elevated"
_ELEVATION_HINT_SHOWN = False


def _process_integrity_tier(pid):
    """进程完整性级别：'high'/'medium'/'low'/''（未知）。"""
    if not pid:
        return ""
    try:
        import re as _re
        import win32api as _wa
        import win32security as _ws
        h = _wa.OpenProcess(0x1000, False, int(pid))
        if not h:
            return ""
        try:
            th = _ws.OpenProcessToken(h, _ws.TOKEN_QUERY)
            info = _ws.GetTokenInformation(th, _ws.TokenIntegrityLevel)
            sid = info[0]
            s = _ws.ConvertSidToStringSid(sid)
            m = _re.search(r"S-1-16-(\d+)", s or "")
            rid = int(m.group(1)) if m else 0
        finally:
            try:
                _wa.CloseHandle(h)
            except Exception:
                pass
        if rid >= 0x3000:
            return "high"
        if rid >= 0x2000:
            return "medium"
        if rid > 0:
            return "low"
        return ""
    except Exception:
        return ""


def _runs_target_elevated():
    """是否存在以高完整性运行的目标软件进程（按 _TARGET_PROCESS_KEYWORD 识别）。"""
    if not _TARGET_PROCESS_KEYWORD:
        return False
    try:
        import psutil
    except Exception:
        return False
    kw = _TARGET_PROCESS_KEYWORD.lower()
    try:
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = (p.info.get("name") or "").lower()
                exe = (p.info.get("exe") or "").lower()
            except Exception:
                continue
            if kw not in name and kw not in exe:
                continue
            if _process_integrity_tier(p.info.get("pid")) == "high":
                return True
    except Exception:
        return False
    return False


def _should_relaunch_elevated():
    if _ELEVATED_RELAUNCH_FLAG in sys.argv or "--no-elevate" in sys.argv:
        return False
    if _process_integrity_tier(os.getpid()) == "high":
        return False
    if not _runs_target_elevated():
        return False
    return True


def _relaunch_elevated():
    try:
        if getattr(sys, "frozen", False):
            argv = subprocess.list2cmdline(sys.argv) + " " + _ELEVATED_RELAUNCH_FLAG
            target = sys.executable
        else:
            args = sys.argv if sys.argv else [os.path.basename(sys.argv[0])]
            argv = subprocess.list2cmdline(args) + " " + _ELEVATED_RELAUNCH_FLAG
            target = sys.executable
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", target, argv, os.getcwd(), 1
        )
        return result > 32
    except Exception:
        return False


def _show_elevation_hint():
    ctypes.windll.user32.MessageBoxW(
        None,
        "检测到目标软件可能以管理员权限运行，而本工具当前是普通权限。\n\n"
        "Windows 的 UIPI 隔离会阻止普通权限进程读取目标软件的控件内容树，"
        "导致所有步骤报“未命中控件”。\n\n"
        "建议：以管理员身份启动本工具，或将目标软件改为普通权限启动。\n\n"
        "（可先继续运行；若步骤失败，请按上述方式调整权限）",
        "通用自动化 - 权限提示",
        0x40 | 0x1000,
    )


def _maybe_relaunch_elevated():
    global _ELEVATION_HINT_SHOWN
    if _ELEVATION_HINT_SHOWN:
        return False
    if _should_relaunch_elevated():
        if _relaunch_elevated():
            return True
        _ELEVATION_HINT_SHOWN = True
        _show_elevation_hint()
    return False


# ── 最小 GUI ────────────────────────────────────────────────────────────────
class GenericLauncherUI:
    def __init__(self, root):
        self.root = root
        root.title("通用自动化总控台（去 WT 绑定）")
        root.geometry("720x520")

        f = ttk.Frame(root, padding=10)
        f.pack(fill="x")

        # 目标软件 exe
        ttk.Label(f, text="目标软件 exe 路径:").grid(row=0, column=0, sticky="w")
        self.exe_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.exe_var, width=60).grid(row=0, column=1)
        ttk.Button(f, text="浏览", command=self._browse_exe).grid(row=0, column=2)

        # 窗口标题正则（可选）
        ttk.Label(f, text="窗口标题正则(可选):").grid(row=1, column=0, sticky="w")
        self.title_var = tk.StringVar()
        ttk.Entry(f, textvariable=self.title_var, width=60).grid(row=1, column=1)
        ttk.Label(f, text="留空则纯靠进程名识别").grid(row=1, column=2, sticky="w")

        # 流程定义文件
        ttk.Label(f, text="流程定义文件:").grid(row=2, column=0, sticky="w")
        self.flow_var = tk.StringVar(
            value=os.environ.get(
                "WT_FLOW_DEFINITION_FILE",
                os.path.join(ROOT, "workspace", "flow_definition.json"),
            )
        )
        ttk.Entry(f, textvariable=self.flow_var, width=60).grid(row=2, column=1)
        ttk.Button(f, text="浏览", command=self._browse_flow).grid(row=2, column=2)

        # 运行 / 停止
        bf = ttk.Frame(root, padding=(10, 4))
        bf.pack(fill="x")
        ttk.Button(bf, text="运行自动化", command=self._run).pack(side="left")
        ttk.Button(bf, text="清空日志", command=self._clear_log).pack(side="left", padx=6)

        # 日志
        self.log = scrolledtext.ScrolledText(root, state="disabled", height=24)
        self.log.pack(fill="both", expand=True, padx=10, pady=10)

        self._running = False
        self._append("通用自动化总控台已就绪。请先配置目标软件 exe 路径，再点“运行自动化”。\n")

    def _browse_exe(self):
        p = filedialog.askopenfilename(filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        if p:
            self.exe_var.set(p)

    def _browse_flow(self):
        p = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if p:
            self.flow_var.set(p)

    def _append(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.configure(state="disabled")
        self.log.see("end")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _run(self):
        if self._running:
            self._append("[warn] 已有运行在进行中，忽略本次点击。")
            return
        exe = self.exe_var.get().strip()
        if not exe:
            messagebox.showwarning("配置缺失", "请先填写目标软件 exe 路径。")
            return
        flow = self.flow_var.get().strip()
        if not flow or not os.path.isfile(flow):
            messagebox.showwarning("配置缺失", "流程定义文件不存在：%s" % flow)
            return

        # 由 exe 派生的进程名关键词（去掉 .exe）
        base = os.path.basename(exe).lower()
        kw = base[:-4] if base.endswith(".exe") else base
        title_re = self.title_var.get().strip() or None

        # 注入通用目标配置
        generic_automation.config_generic_target_app(
            exe=exe,
            title_re=title_re,
            class_keywords=[kw] if kw else [],
        )
        generic_automation.FLOW_DEFINITION_FILE = flow

        # 重定向日志到文本框
        generic_automation.log_step = lambda msg: self._append(str(msg))

        self._running = True
        self._append("[preflight] 目标=%s 关键词=%s 流程=%s" % (exe, kw, flow))
        t = threading.Thread(target=self._run_worker, daemon=True)
        t.start()

    def _run_worker(self):
        try:
            generic_automation.run_automation(pre_raise=True)
            self._append("[done] 运行结束。")
        except Exception as exc:
            self._append("[error] 运行异常: %r" % exc)
        finally:
            self._running = False


def main():
    # 启动期：若目标软件以管理员运行，先尝试提权（通用化，按配置关键词；此处尚未配置则用默认不提权）
    if _TARGET_PROCESS_KEYWORD and _maybe_relaunch_elevated():
        return  # 已触发 UAC 提权重启，原进程退出
    app = tk.Tk()
    GenericLauncherUI(app)
    app.mainloop()


if __name__ == "__main__":
    main()
