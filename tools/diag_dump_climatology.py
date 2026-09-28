# -*- coding: utf-8 -*-
"""
诊断脚本：转储气象数据选择弹窗(MTDClimatologySelectorControl)的 UIA 子树。

用途：step_15/step_mt_refclim_select 双击 M1/Mast1 列表项失败（未命中/未选中）时，
确认运行时 UIA 树里列表项的真实结构（name/rect/offscreen/父链），
对比视觉可见但自动化定位不到/点不中的差异。

用法（先手动把软件气象弹窗开到目标状态，如检索 M1 后）：
    python tools/diag_dump_climatology.py
    可选: --hwnd 330362  指定主窗口句柄（默认自动找 MUPSmartClient 主窗口）
    可选: --raw          同时输出 Raw View 子树（含 IsControlElement=False 的节点）
"""
import os
import sys
import json
import time
import argparse
import ctypes
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pywinauto import Desktop
from pywinauto.application import Application

_ELEVATED_RELAUNCH_FLAG = "--diag-dump-elevated"


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


def _mup_pids_win32():
    """用 Win32 EnumWindows + 进程名，找出 MUPSmartClient 主窗口所属 PID（不依赖 psutil）。"""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pids = set()
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name"]):
            if "MUPSmartClient" in (p.info.get("name") or ""):
                pids.add(p.info["pid"])
        return pids
    except Exception:
        pass
    # 兜底：按窗口枚举进程名
    try:
        from pywinauto import Desktop as _D
        for w in _D(backend="win32").windows():
            try:
                pid = w.process_id()
                if pid:
                    pids.add(pid)
            except Exception:
                continue
        return pids
    except Exception:
        return set()


def _mup_runs_elevated():
    """是否存在以高完整性运行的 MUPSmartClient 目标进程。"""
    for pid in _mup_pids_win32():
        if _process_integrity_tier(pid) == "high":
            return True
    return False


def _maybe_relaunch_elevated():
    """自身非高完整性且 MUP 高完整性时，以管理员身份重启自身。返回 True 表示已重启。"""
    if _ELEVATED_RELAUNCH_FLAG in sys.argv:
        return False
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return False
    except Exception:
        return False
    if not _mup_runs_elevated():
        return False
    print("[elevate] 检测到 MUP 以管理员/高完整性运行，本脚本为非管理员，"
          "正在以管理员身份重启自身（请在弹出的 UAC 提示中点'是'）...")
    try:
        args = sys.argv + [_ELEVATED_RELAUNCH_FLAG]
        argv = subprocess.list2cmdline(args)
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, argv, os.getcwd(), 1
        )
        return int(result) > 32
    except Exception as exc:
        print("[elevate] 提权重启失败: %r" % exc)
        return False


def find_main_window(hwnd_hint=None, pid_hint=None):
    """定位 MUP 主窗口：优先指定 hwnd，其次 pid，最后按进程名自动找。"""
    if hwnd_hint:
        try:
            app = Application(backend="uia").connect(handle=int(hwnd_hint))
            win = app.window()
            print("[main] 使用指定 hwnd=%s title=%r class=%r" % (
                hwnd_hint, win.window_text(), win.class_name()))
            return win
        except Exception as exc:
            print("[main] 指定 hwnd 连接失败: %r" % exc)
    if pid_hint:
        try:
            app = Application(backend="uia").connect(process=int(pid_hint))
            win = app.window()
            print("[main] 使用指定 pid=%s title=%r class=%r" % (
                pid_hint, win.window_text(), win.class_name()))
            return win
        except Exception as exc:
            print("[main] 指定 pid 连接失败: %r" % exc)
    # 自动找 MUPSmartClient 进程主窗口：按进程名匹配（UIA 窗口 class 多为 Window）
    target_pids = _mup_pids_win32()
    print("[main] MUPSmartClient pids=%s" % (sorted(target_pids) if target_pids else "未找到"))
    # 用 Win32 EnumWindows 按 pid 找主窗口
    if target_pids:
        for pid in target_pids:
            try:
                app = Application(backend="uia").connect(process=int(pid))
                win = app.window()
                print("[main] 自动命中 pid=%s: hwnd=%s title=%r class=%r" % (
                    pid, win.handle, win.window_text(), win.class_name()))
                return win
            except Exception as exc:
                print("[main] pid=%s 连接失败: %r" % (pid, exc))
    # 最终兜底：Desktop 枚举，按 class 或 title 含 Meteodyn 判断
    try:
        for proc in Desktop(backend="uia").windows():
            try:
                title = proc.window_text()
                cls = proc.class_name()
                if "Meteodyn" in (title or "") or "MUP" in (cls or "").upper():
                    print("[main] Desktop 命中: hwnd=%s title=%r class=%r" % (
                        proc.handle, title, cls))
                    return proc
            except Exception:
                continue
    except Exception as exc:
        print("[main] Desktop 枚举失败: %r" % exc)
    return None


def wrapper_desc(w):
    """紧凑描述一个 wrapper。"""
    try:
        name = w.window_text()
    except Exception:
        name = ""
    try:
        aid = w.automation_id()
    except Exception:
        aid = ""
    try:
        ct = w.element_info.control_type
    except Exception:
        ct = ""
    try:
        r = w.rectangle()
        rect = "[l=%d,t=%d,r=%d,b=%d]" % (r.left, r.top, r.right, r.bottom)
    except Exception:
        rect = "[no-rect]"
    try:
        off = w.is_offscreen()
    except Exception:
        off = "?"
    return {"name": name, "aid": aid, "ct": ct, "rect": rect, "offscreen": off}


def dump_subtree(root, label, max_depth=20, raw=False):
    """从 root 向下遍历，收集所有控件（深度优先）。"""
    rows = []
    seen = set()

    def _walk(w, depth):
        if depth > max_depth:
            return
        try:
            key = id(w.element_info)
        except Exception:
            key = None
        if key in seen:
            return
        seen.add(key)
        desc = wrapper_desc(w)
        rows.append((depth, desc))
        try:
            children = w.children()
        except Exception:
            children = []
        for c in children:
            _walk(c, depth + 1)

    _walk(root, 0)
    print("\n===== %s (共 %d 节点) =====" % (label, len(rows)))
    for depth, d in rows:
        print("  " * depth + "- name=%r aid=%r ct=%s rect=%s off=%s" % (
            d["name"], d["aid"], d["ct"], d["rect"], d["offscreen"]))
    return rows


def find_climatology(win, raw=False):
    """在主窗口内找 MTDClimatologySelectorControl 容器（支持多实例）。"""
    found = []
    try:
        for c in win.descendants():
            try:
                cls = c.class_name()
                aid = c.automation_id()
            except Exception:
                continue
            if cls == "MTDClimatologySelectorControl" or aid == "MTDClimatologySelectorControl":
                found.append(c)
    except Exception as exc:
        print("[find] descendants 遍历异常: %r" % exc)
    return found


def main():
    # 自动提权：MUP 高完整性 + 本脚本非管理员时，以管理员身份重启
    if _maybe_relaunch_elevated():
        return

    ap = argparse.ArgumentParser()
    ap.add_argument("--hwnd", type=str, default="", help="MUP 主窗口句柄（推荐，如 330362）")
    ap.add_argument("--pid", type=str, default="", help="MUP 进程 PID（如 28832）")
    ap.add_argument("--raw", action="store_true", help="输出 Raw View 子树")
    ap.add_argument("--save", type=str, default="", help="同时保存 JSON 到指定路径")
    args = ap.parse_args()

    win = find_main_window(args.hwnd or None, args.pid or None)
    if win is None:
        print("[fatal] 未找到 MUP 主窗口。请确认软件已打开，且气象弹窗处于目标状态。")
        sys.exit(1)

    # 先整体扫一遍，列出所有 MTDClimatologySelectorControl
    clims = find_climatology(win)
    print("\n[find] MTDClimatologySelectorControl 实例数: %d" % len(clims))
    for i, c in enumerate(clims):
        print("  #%d: %s" % (i, wrapper_desc(c)))

    if not clims:
        print("\n[提示] 未找到气象弹窗。请确认：")
        print("  1. 软件已打开且当前在'气象参考'下拉弹出的选择界面；")
        print("  2. 弹窗未关闭；")
        print("  3. 若已关闭，先手动打开到'M1 已检索'状态再重跑本脚本。")
        sys.exit(2)

    all_rows = []
    for i, c in enumerate(clims):
        label = "MTDClimatologySelectorControl #%d 子树" % i
        rows = dump_subtree(c, label, max_depth=25, raw=args.raw)
        all_rows.extend([(i, d, r) for (d, r) in rows])

    # 汇总：列出所有 Text 节点（定位 M1/Mast1 的关键）
    print("\n===== 所有 Text 节点汇总（name 排序）=====")
    text_rows = []
    for idx, depth, d in all_rows:
        if (d["ct"] or "").lower() in ("text",):
            text_rows.append((depth, d))
    for depth, d in sorted(text_rows, key=lambda x: (x[1]["name"] or "", x[0])):
        print("  depth=%d name=%r rect=%s off=%s" % (depth, d["name"], d["rect"], d["offscreen"]))

    if args.save:
        payload = {
            "dumpTime": time.strftime("%Y-%m-%d %H:%M:%S"),
            "hwnd": getattr(win, "handle", ""),
            "climatologyInstances": [wrapper_desc(c) for c in clims],
            "nodes": [
                {"instance": idx, "depth": depth, **d}
                for idx, depth, d in all_rows
            ],
        }
        with open(args.save, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print("\n[save] 已保存: %s" % args.save)


if __name__ == "__main__":
    main()
