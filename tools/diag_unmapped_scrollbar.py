#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断 ControlMapImportDialog 中未映射的滚动条（F9）。

输出全部未映射 widget 的：类名、路径、pack 信息（manager/in/expand/fill）、
几何（宽x高）、pack_propagate 状态、其父容器兄弟清单，判断是“等待首次
刷新才映射”的正常惰性行为，还是漏 pack 的缺陷。
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


def walk(widget, out):
    for child in widget.winfo_children():
        out.append(child)
        walk(child, out)
    return out


def describe(w):
    try:
        info = w.pack_info()
        mgr = "pack: in=%s side=%s fill=%s expand=%s" % (
            info.get("in", "?"), info.get("side", "?"), info.get("fill", "?"), info.get("expand", "?"))
    except Exception as exc:
        mgr = f"pack_info 失败: {type(exc).__name__}: {exc}"
    return (f"{w.winfo_class():<14} {str(w):<58} {w.winfo_width()}x{w.winfo_height():<6} "
            f"req={w.winfo_reqwidth()}x{w.winfo_reqheight():<6} {mgr}")


def main():
    root = tk.Tk()
    wt_dpi.compute_scale(root)
    root.title("F9 滚动条诊断")
    root.geometry("180x60+40+40")
    dialog = E.ControlMapImportDialog(root, default_window_title="F9 诊断")
    deadline = time.time() + 15
    while time.time() < deadline:
        root.update()
        if dialog.window.winfo_ismapped() and dialog.window.winfo_width() > 1:
            for _ in range(8):
                root.update_idletasks()
                root.update()
                time.sleep(0.02)
            break
        time.sleep(0.05)

    all_w = walk(dialog.window, [])
    unmapped = [w for w in all_w if not w.winfo_ismapped()]
    print(f"总 widget: {len(all_w)}，未映射: {len(unmapped)}")
    for w in unmapped:
        print("  " + describe(w))
        try:
            parent = w.nametowidget(w.winfo_parent())
            print(f"    父容器 {parent.winfo_class()} {str(parent)} {parent.winfo_width()}x{parent.winfo_height()} "
                  f"propagate={parent.pack_propagate()}")
            for sib in parent.winfo_children():
                mark = " <== 未映射" if not sib.winfo_ismapped() else ""
                print(f"      兄弟 {sib.winfo_class():<12} {str(sib):<56} "
                      f"{sib.winfo_width()}x{sib.winfo_height()}{mark}")
        except Exception as exc:
            print(f"    父容器解析失败: {exc}")

    # 额外核对：中面板容器树（control_tree 的 wrap 与滚动条归属）
    print("\n-- 中面板结构 --")
    wrap = dialog.control_tree.nametowidget(dialog.control_tree.winfo_parent())
    for w in [wrap] + wrap.winfo_children():
        print("  " + describe(w))

    try:
        dialog.window.destroy()
    except Exception:
        pass
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
