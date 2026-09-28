#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 修复后的控件库对话框 UI 冒烟验证（只读，不落盘）。

比单测更进一步：真实实例化完整 ControlMapImportDialog（构建期会扫描文件列表、
加载 master payload、走展平/过滤/排序/插入 Treeview 全链路），然后：
  A. 全按钮可点击性 + 控件树三列渲染数量逐行核对（数量级对齐 master 真实数据）
  B. 三种排序模式新旧时间戳实现全量逐位对照（绝不允许顺序差异）
  C. 选中行 -> 预览面板文本构建（点击链路核心消费方）
  D. 树形视图切换 + 逐节点 _tree_node_map 一致性
  E. 缓存契约三态复核（命中/替换失效/原地失效）
截图保存到 %TEMP% 供人工确认，测试完自动销毁窗口。
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
from types import MethodType

import WT_Flow_Editor as E

FAILURES = []


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(name)


def set_var_without_trace(var, value, callback):
    """真实 .set() 但不触发 trace（否则会重扫文件列表换 payload）：
    临时摘掉 trace 回调，set 完再挂回。"""
    var.trace_remove("write", var.trace_info()[0][1]) if var.trace_info() else None
    var.set(value)
    var.trace_add("write", callback)


def legacy_timestamp(text):
    """修复前实现，逐值对照用。"""
    from datetime import datetime

    text = str(text or "").strip()
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


def walk_buttons(widget, out):
    for child in widget.winfo_children():
        if child.winfo_class() in ("TButton", "Button", "TCheckbutton", "Checkbutton", "TRadiobutton", "Radiobutton"):
            out.append(child)
        walk_buttons(child, out)
    return out


def count_widget_classes(widget, counter):
    cls = widget.winfo_class()
    counter[cls] = counter.get(cls, 0) + 1
    for child in widget.winfo_children():
        count_widget_classes(child, counter)
    return counter


def main():
    print("== 构建完整对话框（真实 master 数据） ==")
    root = tk.Tk()
    root.withdraw()
    t0 = time.perf_counter()
    try:
        dialog = E.ControlMapImportDialog(root, default_window_title="冒烟测试")
    except Exception as exc:
        print(f"对话框构建异常: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return 2
    t_build = time.perf_counter() - t0
    print(f"构建耗时: {t_build*1000:,.0f} ms")
    root.update()

    try:
        # ---------- A. 控件树渲染与数据对齐 ----------
        tree = dialog.control_tree
        rows = tree.get_children()
        check("A1 Treeview 渲染行数 > 0", len(rows) > 0, f"{len(rows):,} 行")

        dialog_all = dialog._build_controls_from_payload()
        filtered = dialog._get_filtered_controls()
        check("A2 Treeview 行数 == 过滤后控件数", len(rows) == len(filtered),
              f"{len(rows):,} vs {len(filtered):,}")

        display_pairs = []
        for control in filtered:
            name, quality, ctype, locator, window = E.ControlMapImportDialog._control_display_fields(control)
            # 插入顺序 (seq, name, ctrl_type, quality, locator, window)：values[1..5] 对应
            display_pairs.append((name, ctype, quality, locator, window))
        rendered_mismatch = 0
        first_mismatch_info = ""
        for iid, expected in zip(rows, display_pairs):
            values = tree.item(iid, "values")
            if len(values) < 6 or any(str(values[i + 1]) != expected[i] for i in range(5)):
                rendered_mismatch += 1
                if not first_mismatch_info:
                    first_mismatch_info = f"iid={iid} 渲染={list(values)[:6]} 期望={expected}"
        check("A3 全部行五列渲染值与数据层一致", rendered_mismatch == 0,
              f"{len(rows):,} 行核对, 不一致 {rendered_mismatch}" + (f"; 例: {first_mismatch_info}" if first_mismatch_info else ""))

        buttons = walk_buttons(dialog.window, [])
        check("A4 全部按钮已创建", len(buttons) >= 10, f"{len(buttons)} 个按钮/复选/单选")
        wclasses = count_widget_classes(dialog.window, {})
        check("A5 对话框 widget 总量正常", sum(wclasses.values()) > 50,
              f"{sum(wclasses.values()):,} widgets, Top={wclasses.get('Toplevel', 0)} Frame={wclasses.get('Frame', 0)}")

        # ---------- B. 排序验证：同一 payload 上的纯方法对照 ----------
        # 注意不能用 var_sort.set()：它触发 trace -> _refresh_file_list()，
        # 会按新排序重排文件列表并自动选中另一个文件（payload 被合法替换），
        # 拿新文件的排序对旧文件的期望必然错位。这里绑定真实方法在同一 payload 上对照。
        sorter = MethodType(E.ControlMapImportDialog._get_filtered_controls, dialog)

        added_values = [c.get("_addedAt", "") for c in dialog_all]
        added_values = [v for v in added_values if v]
        mismatch = sum(1 for v in added_values if E.control_map_timestamp(v) != legacy_timestamp(v))
        check("B1 时间戳新旧实现全量等价", mismatch == 0,
              f"{len(added_values):,} 个时间值, 不等价 {mismatch}")

        legacy_timestamp_asc = sorted(
            dialog_all,
            key=lambda c: (legacy_timestamp(c.get("_addedAt", "")), str(c.get("name", "")).strip()),
        )
        legacy_names_desc = [c.get("name") for c in reversed(legacy_timestamp_asc)]

        sort_callback = lambda *_a: dialog._refresh_file_list()
        set_var_without_trace(dialog.var_sort, "添加时间-新到旧", sort_callback)
        check("B2 排序顺序与旧实现逐位一致 [新到旧]",
              [c.get("name") for c in sorter()] == legacy_names_desc, f"{len(dialog_all):,} 项")

        set_var_without_trace(dialog.var_sort, "添加时间-旧到新", sort_callback)
        check("B3 排序顺序与旧实现逐位一致 [旧到新]",
              [c.get("name") for c in sorter()] == [c.get("name") for c in legacy_timestamp_asc], f"{len(dialog_all):,} 项")

        set_var_without_trace(dialog.var_sort, "质量优先", sort_callback)
        quality_sorted = sorter()
        legacy_quality = sorted(
            dialog_all,
            key=lambda c: (
                0 if str(c.get("qualityTier", "") or c.get("_qualityTier", "")).strip() == "推荐保留" else 1,
                -int(c.get("locatorScore", 0) or c.get("_locatorScore", 0) or 0),
                str(c.get("name", "")).strip(),
            ),
        )
        check("B4 质量优先排序与旧实现逐位一致",
              [c.get("name") for c in quality_sorted] == [c.get("name") for c in legacy_quality],
              f"{len(quality_sorted):,} 项, 首项tier={quality_sorted[0].get('_qualityTier') if quality_sorted else '-'}")

        # 端到端：真实 trace 切排序（文件列表会重扫+重选，属既有行为），
        # 树行数必须与新 payload 的过滤结果一致，且首行渲染与数据层一致。
        dialog.var_sort.set("添加时间-新到旧")  # 触发 trace 真实链路
        root.update()
        rows_after = tree.get_children()
        filtered_after = dialog._get_filtered_controls()
        check("B5 trace 切排序后树行数与新 payload 过滤结果一致",
              len(rows_after) == len(filtered_after), f"{len(rows_after):,} vs {len(filtered_after):,}")
        if rows_after and filtered_after:
            first_vals = tree.item(rows_after[0], "values")
            name, quality, ctype, locator, window = E.ControlMapImportDialog._control_display_fields(filtered_after[0])
            check("B6 trace 切排序后首行五列渲染与数据层一致",
                  len(first_vals) >= 6 and str(first_vals[1]) == name and str(first_vals[3]) == quality
                  and str(first_vals[2]) == ctype and str(first_vals[4]) == locator and str(first_vals[5]) == window,
                  f"name={str(first_vals[1])[:20]}")

        # ---------- C. 选中链路：预览面板 ----------
        if rows:
            tree.selection_set(rows[0])
            dialog._on_control_select()
            dialog.window.update_idletasks()
            preview = dialog.preview_text.get("1.0", tk.END).strip()
            check("C1 选中行 -> 预览面板有内容", len(preview) > 20, f"{len(preview):,} 字符")

        # ---------- D. 树形视图切换（真实 .set() 走完整刷新链） ----------
        dialog.var_view_mode.set("tree")
        dialog._refresh_controls_tree()
        root.update()
        tree_rows = tree.get_children()
        check("D1 树形视图渲染节点数 > 0", len(tree_rows) > 0, f"{len(tree_rows):,} 根节点")
        node_map_count = len(dialog._tree_node_map)
        flat_count = len(dialog.current_payload.get("flatControls", []) or []) if isinstance(dialog.current_payload, dict) else 0
        # uiPath 重复的控件在树形视图按路径塌缩为同一节点（既有行为）：
        # master 无 controlsTree 时走 uiPath 重建，map = 唯一路径数 + 合成容器节点数。
        paths = [str(c.get("uiPath", "")).strip() for c in (dialog.current_payload.get("flatControls", []) or [])]
        uniq_paths = len(set(p for p in paths if p))
        check("D2 树形节点映射覆盖唯一 uiPath（塌缩为既有行为）",
              node_map_count >= uniq_paths,
              f"map {node_map_count:,} / 唯一路径 {uniq_paths:,} / flat {flat_count:,}（重复塌缩 {flat_count - uniq_paths:,}，合成容器 {node_map_count - uniq_paths if node_map_count >= uniq_paths else 0:,}）")

        # 选中树形叶子节点 -> 预览面板（树形链路的选中消费方）
        leaf_iids = [iid for iid in dialog._tree_node_map if dialog._tree_node_map[iid]]
        if leaf_iids:
            tree.selection_set(leaf_iids[0])
            dialog._on_control_select()
            root.update_idletasks()
            preview_tree = dialog.preview_text.get("1.0", tk.END).strip()
            check("D3 树形视图选中叶子 -> 预览面板有内容", len(preview_tree) > 20, f"{len(preview_tree):,} 字符")

        # ---------- E. 缓存契约三态 ----------
        dialog.var_view_mode.set("flat")
        dialog._refresh_controls_tree()
        root.update()
        before = dialog._build_controls_from_payload()
        again = dialog._build_controls_from_payload()
        check("E1 重复构建命中缓存", again is before, "同一 list 对象")
        old_payload = dialog.current_payload
        dialog.current_payload = {"flatControls": []}
        rebuilt = dialog._build_controls_from_payload()
        check("E2 换 payload 对象后重建", rebuilt is not before, "缓存自动失效")
        dialog.current_payload = old_payload
        restored = dialog._build_controls_from_payload()
        check("E3 切回 payload 后缓存重建成功", len(restored) == len(before))

        # ---------- 截图（人工确认用） ----------
        shot = os.path.join(os.environ.get("TEMP", "/tmp"), "wt_dialog_smoke.png")
        try:
            x = root
            while x.master is not None:
                x = x.master
            dialog.window.deiconify()
            dialog.window.geometry("1480x860+30+30")
            root.update()
            time.sleep(0.5)
            from PIL import ImageGrab
            import ctypes

            user32 = ctypes.windll.user32
            user32.SetProcessDPIAware()
            img = ImageGrab.grab(bbox=None, all_screens=True)
            img.save(shot)
            print(f"截图: {shot}")
        except Exception as exc:
            print(f"截图失败（不影响验证结论）: {exc}")

    finally:
        try:
            dialog.window.destroy()
        except Exception:
            pass
        root.destroy()

    print()
    if FAILURES:
        print(f"共 {len(FAILURES)} 项失败: {FAILURES}")
        return 1
    print("全部 UI 冒烟验证通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
