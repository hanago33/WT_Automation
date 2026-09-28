#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断视图切换（树形->平铺）后 控件树 与 垂直滚动条 的 17px 重叠（J210）。

序列：
  1. 构建后（平铺）：dump control_wrap 子控件矩形
  2. 切树形（真实 _on_view_mode_change 链路）：dump
  3. 切回平铺：dump（立即 / 额外泵 20 轮后再 dump，看是否自愈）
  4. 再模拟一次窗口尺寸变化（触发 Configure 重排）：dump
"""
from __future__ import annotations

import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import tkinter as tk

import WT_Flow_Editor as E
import wt_dpi


def rect(w):
    return (w.winfo_rootx(), w.winfo_rooty(),
            w.winfo_rootx() + w.winfo_width(), w.winfo_rooty() + w.winfo_height())


def overlap(a, b):
    ox = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    oy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ox * oy


def pump(root, cycles=6):
    for _ in range(cycles):
        root.update_idletasks()
        root.update()
        time.sleep(0.02)


def dump(root, dialog, label):
    pump(root)
    wrap = dialog.control_tree.nametowidget(dialog.control_tree.winfo_parent())
    tree_r = rect(dialog.control_tree)
    print(f"-- {label} --")
    print(f"   control_wrap {wrap.winfo_width()}x{wrap.winfo_height()}")
    for w in wrap.winfo_children():
        r = rect(w)
        ov = overlap(tree_r, r) if w is not dialog.control_tree else 0
        print(f"   {w.winfo_class():<12} {str(w):<52} x={r[0]}..{r[2]} y={r[1]}..{r[3]} "
              f"{w.winfo_width()}x{w.winfo_height()} mapped={w.winfo_ismapped()} 与树重叠={ov}px²")
    middle = wrap.nametowidget(wrap.winfo_parent())
    print(f"   [middle 兄弟] ", end="")
    for w in middle.winfo_children():
        print(f"{w.winfo_class()}({w.winfo_width()}x{w.winfo_height()},mapped={w.winfo_ismapped()}) ", end="")
    print()


def main():
    root = tk.Tk()
    wt_dpi.compute_scale(root)
    root.title("J210 重叠诊断")
    root.geometry("180x60+40+40")
    dialog = E.ControlMapImportDialog(root, default_window_title="J210 诊断")
    deadline = time.time() + 15
    while time.time() < deadline:
        root.update()
        if dialog.window.winfo_ismapped() and dialog.window.winfo_width() > 1:
            pump(root)
            break
        time.sleep(0.05)

    dump(root, dialog, "1. 构建后（平铺）")
    dialog.var_view_mode.set("tree")
    dialog._on_view_mode_change()
    dump(root, dialog, "2. 切树形视图")
    dialog.var_view_mode.set("flat")
    dialog._on_view_mode_change()
    dump(root, dialog, "3. 切回平铺（立即）")
    for _ in range(20):
        root.update_idletasks()
        root.update()
        time.sleep(0.05)
    dump(root, dialog, "4. 额外泵 20 轮后")
    wt_dpi.geometry(dialog.window, 1320, 760)
    dump(root, dialog, "5. 触发窗口尺寸变化后")
    wt_dpi.geometry(dialog.window, 1480, 860)
    dump(root, dialog, "6. 恢复尺寸后")

    try:
        dialog.window.destroy()
    except Exception:
        pass
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
