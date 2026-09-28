# -*- coding: utf-8 -*-
"""通用化：按进程名查找目标软件主窗口（替代原 WT 的 find_main_windows 写死 MUP）。"""
import os
import psutil

try:
    from uiautomation import WindowControl, Process, TreeWalker
except Exception:  # pragma: no cover
    WindowControl = Process = TreeWalker = None


def _pid_of(process_name):
    pname = process_name.lower()
    pids = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            if (p.info.get("name") or "").lower() == pname:
                pids.append(p.info.get("pid"))
        except Exception:
            continue
    return pids


def find_main_windows_by_process(process_name):
    """返回目标进程主窗口的 hwnd 字符串列表（供 locator 包装成 UIA wrapper）。"""
    pids = _pid_of(process_name)
    wins = []
    if WindowControl is None:
        return wins
    for pid in pids:
        try:
            pc = Process(pid)
            main = pc.GetMainWindow()
            if main:
                wins.append(str(main.NativeWindowHandle))
        except Exception:
            continue
    return wins
