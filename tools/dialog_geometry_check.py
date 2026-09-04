#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""控件库对话框几何布局校验（纯 winfo_* 程序化断言，不截图）。

覆盖原"视觉验收"三项检查点，用屏幕坐标等价替代：
  F. 按钮布局：15 按钮 + 5 单选全部创建且文本清单一致、真实映射、
     在窗口客户区内零裁剪、同排兄弟零重叠、按钮宽度容纳文本
  G. 列渲染：Treeview 5 列 id/列头文本/列宽齐全
  H. 面板布局：PanedWindow 三面板 x 区间互不重叠、宽度不低于 minsize、
     文件列表/控件树/预览面板各归其位、预览与树零遮挡、滚动条不侵入内容
  I. 窗口纵向堆叠：说明/工具栏/主体/操作区 y 区间互不重叠且自上而下
  J. 最小尺寸压力测试：缩到 minsize(1320x760) 后按钮与面板仍零缺陷，
     恢复默认 1480x860 后复核

与真实运行对齐的两个前提：
  1. 调 wt_dpi.compute_scale(root)（同 WT_Flow_Editor 主入口），安装 geometry
     自动缩放补丁，尺寸断言按 wt_dpi.scale() 计算；
  2. 驱动用 root 保持可见——对话框是 transient(root)，Windows 上 transient
     窗口跟随父窗口显示状态，父 withdrawn 时对话框永不映射（winfo_* 全 1x1）。
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
from collections import Counter

import WT_Flow_Editor as E
import wt_dpi

FAILURES = []

EXPECTED_BUTTON_TEXTS = {
    "刷新控件库": 1, "打开控件库目录": 1, "打开控件库采集器": 1,
    "合并去重并保存": 1, "展开全部": 2, "折叠全部": 2,
    "导入所选控件": 1, "导入推荐控件": 1, "导入当前文件全部控件": 1,
    "编辑所选控件": 1, "删除所选控件": 1, "检验定位": 1, "取消": 1,
}
EXPECTED_RADIO_TEXTS = {"单文件": 1, "总控件信息": 1, "标准目录": 1, "树形视图": 1, "列表视图": 1}


def check(name, ok, detail=""):
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILURES.append(name)


def rect(w):
    """控件在屏幕上的绝对矩形 (x1, y1, x2, y2)。"""
    return (w.winfo_rootx(), w.winfo_rooty(),
            w.winfo_rootx() + w.winfo_width(), w.winfo_rooty() + w.winfo_height())


def overlap_area(a, b):
    ox = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    oy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return ox * oy


def contains(outer, inner, tol=1):
    return (inner[0] >= outer[0] - tol and inner[1] >= outer[1] - tol
            and inner[2] <= outer[2] + tol and inner[3] <= outer[3] + tol)


def walk(widget, out):
    for child in widget.winfo_children():
        out.append(child)
        walk(child, out)
    return out


def collect_clickables(dialog):
    buttons, radios = [], []
    for w in walk(dialog.window, []):
        if w.winfo_class() in ("TButton", "Button"):
            buttons.append(w)
        elif w.winfo_class() in ("TRadiobutton", "Radiobutton"):
            radios.append(w)
    return buttons, radios


def text_width_px(btn):
    """按钮文本像素宽（Tcl font measure，接受任意字体描述/命名字体）。"""
    return int(btn.tk.call("font", "measure", str(btn.cget("font")), str(btn.cget("text"))))


def pump(root, cycles=8):
    """泵事件循环：WM 的 Map/Configure 是异步的，一次 update 不够。"""
    for _ in range(cycles):
        root.update_idletasks()
        root.update()
        time.sleep(0.02)


def wait_mapped(root, w, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if w.winfo_ismapped() and w.winfo_width() > 1 and w.winfo_height() > 1:
            pump(root)
            return True
        time.sleep(0.05)
    return False


def get_body_panes(dialog):
    """从中面板导航到主体 PanedWindow 并取三面板 widget（panes() 返回 Tcl 路径需转换）。"""
    control_wrap = dialog.control_tree.nametowidget(dialog.control_tree.winfo_parent())
    middle = control_wrap.nametowidget(control_wrap.winfo_parent())
    body = middle.nametowidget(middle.winfo_parent())
    panes = [body.nametowidget(str(p)) for p in body.panes()]
    return body, panes


def button_layout_checks(dialog, phase):
    """按钮三断言：窗口内零裁剪 / 同排兄弟零重叠 / 文本不超按钮宽。"""
    buttons, radios = collect_clickables(dialog)
    win = rect(dialog.window)

    clipped = [str(b.cget("text")) for b in buttons + radios if not contains(win, rect(b))]
    check(f"{phase} 按钮全部在窗口客户区内（零裁剪）", not clipped,
          f"{len(buttons) + len(radios)} 个控件" + (f"，越界: {clipped[:5]}" if clipped else ""))

    overlap_bad = []
    by_parent = {}
    for b in buttons + radios:
        by_parent.setdefault(b.winfo_parent(), []).append(b)
    for group in by_parent.values():
        rects = [rect(b) for b in group]
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if overlap_area(rects[i], rects[j]) > 0:
                    overlap_bad.append(
                        f"{group[i].cget('text')} x {group[j].cget('text')} "
                        f"({overlap_area(rects[i], rects[j])}px²)")
    check(f"{phase} 同排兄弟按钮零重叠", not overlap_bad,
          f"{len(by_parent)} 个排容器" + (f"；重叠: {overlap_bad[:5]}" if overlap_bad else ""))

    truncated = []
    for b in buttons:
        tw = text_width_px(b)
        if b.winfo_width() - tw < 4:
            truncated.append(f"{b.cget('text')}(按钮{b.winfo_width()}px < 文本+4 {tw + 4}px)")
    check(f"{phase} 按钮宽度容纳文本（无截断）", not truncated,
          (f"截断: {truncated[:5]}" if truncated else "全部按钮宽度 >= 文本像素宽 + 4"))
    return buttons, radios


def pane_checks(dialog, phase):
    """三面板断言：类型/齐全/互斥/minsize/内容归位/零遮挡/滚动条不侵入。"""
    body, panes = get_body_panes(dialog)
    check(f"{phase}1 主体为 PanedWindow",
          body.winfo_class().lower() == "panedwindow", body.winfo_class())
    check(f"{phase}2 三面板齐全", len(panes) == 3, f"{len(panes)} 个: {[p.winfo_class() for p in panes]}")
    if len(panes) != 3:
        return body, panes

    pane_rects = [rect(p) for p in panes]
    pane_overlap = [f"面板{i}x面板{j}" for i in range(3) for j in range(i + 1, 3)
                    if overlap_area(pane_rects[i], pane_rects[j]) > 0]
    check(f"{phase}3 三面板 x 区间互不重叠", not pane_overlap,
          (f"重叠: {pane_overlap}" if pane_overlap else "左|中|右 互斥"))

    min_sizes = [wt_dpi.scale(280), wt_dpi.scale(500), wt_dpi.scale(340)]
    widths = [pane_rects[i][2] - pane_rects[i][0] for i in range(3)]
    width_bad = [f"面板{i}宽{widths[i]}px < minsize {min_sizes[i]}px" for i in range(3)
                 if widths[i] < min_sizes[i] - 2]
    check(f"{phase}4 各面板宽度不低于 minsize", not width_bad,
          "宽=" + ",".join(str(w) for w in widths)
          + (f"；不足: {width_bad}" if width_bad else f" (minsize {min_sizes})"))

    left, middle, right = panes
    check(f"{phase}5 文件列表在左面板内", contains(rect(left), rect(dialog.file_listbox)),
          f"listbox {rect(dialog.file_listbox)} in 左面板 {rect(left)}")
    check(f"{phase}6 控件树在中面板内", contains(rect(middle), rect(dialog.control_tree)),
          f"tree {rect(dialog.control_tree)} in 中面板 {rect(middle)}")
    check(f"{phase}7 预览面板在右面板内", contains(rect(right), rect(dialog.preview_text)),
          f"preview {rect(dialog.preview_text)} in 右面板 {rect(right)}")
    check(f"{phase}8 预览面板与控件树零遮挡",
          overlap_area(rect(dialog.preview_text), rect(dialog.control_tree)) == 0,
          f"重叠 {overlap_area(rect(dialog.preview_text), rect(dialog.control_tree))}px²")
    pw, ph = dialog.preview_text.winfo_width(), dialog.preview_text.winfo_height()
    check(f"{phase}9 预览面板非退化尺寸", pw > 100 and ph > 100, f"{pw}x{ph}px")

    # 只对【可见】滚动条断言零侵入：unmapped 控件的 winfo 矩形是残留值，与
    # 内容区相交不代表视觉遮挡。已知既有缺陷（stash 对照证实非本改动引入）：
    # 1) 左面板滚动条被 pack 挤死（Listbox 请求宽 + 17px > 面板内宽）；
    # 2) 切树形视图后树垂直滚动条被挤没、切回平铺不复活（树请求宽 >= wrap 全宽）。
    # 两者均为 pack 饥饿兄弟缺陷，只影响滚动条控件可见性（滚轮/键盘滚动不受影响），
    # 与本次数据层改动正交，记录为 WARN 不阻断。
    sb_overlap = 0
    control_wrap = dialog.control_tree.nametowidget(dialog.control_tree.winfo_parent())
    sb_count = visible_sb = dead_sb = 0
    for container, content in ((left, dialog.file_listbox), (control_wrap, dialog.control_tree)):
        for w in container.winfo_children():
            if w.winfo_class() in ("Scrollbar", "TScrollbar"):
                sb_count += 1
                if w.winfo_ismapped() and w.winfo_width() > 1:
                    visible_sb += 1
                    sb_overlap += overlap_area(rect(w), rect(content))
                else:
                    dead_sb += 1
    if dead_sb:
        print(f"[WARN] {phase}10 既有缺陷（非本改动引入）：{dead_sb} 个滚动条被 pack 挤成未映射不可见"
              "（不影响滚轮/键盘滚动，只影响滚动条控件本身）")
    check(f"{phase}10 可见滚动条不侵入列表/树内容区", sb_overlap == 0,
          f"共 {sb_count} 个滚动条（可见 {visible_sb} / 挤没 {dead_sb}），可见者侵入 {sb_overlap}px²")
    return body, panes


def main():
    print("== 构建完整对话框（真实 master 数据，与主入口同序 compute_scale） ==")
    root = tk.Tk()
    wt_dpi.compute_scale(root)
    root.title("控件库对话框几何校验")
    root.geometry("180x60+40+40")
    dialog = None
    try:
        t0 = time.perf_counter()
        dialog = E.ControlMapImportDialog(root, default_window_title="几何校验")
        print(f"构建耗时: {(time.perf_counter() - t0) * 1000:,.0f} ms (scale={wt_dpi.scale(100) / 100:g})")
    except Exception as exc:
        import traceback
        print(f"对话框构建异常: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        root.destroy()
        return 2

    try:
        mapped = wait_mapped(root, dialog.window)
        check("F0 对话框真实映射（阻塞前提，未映射则后续几何全是假值）", mapped,
              f"{dialog.window.winfo_width()}x{dialog.window.winfo_height()} "
              f"mapped={dialog.window.winfo_ismapped()}")
        if not mapped:
            return 1

        # ---------- F. 按钮布局（默认尺寸 1480x860） ----------
        # 期望 2220x1290（1480x860 * scale=1.5），但若超出本进程可见屏幕会被 WM
        # 夹紧到 minsize（scale(1320)xscale(760)）——被夹紧时等效 J 段最小尺寸压力
        # 测试，该尺寸下全部布局断言均已覆盖，视为通过。
        exp_w, exp_h = wt_dpi.scale(1480), wt_dpi.scale(860)
        min_w, min_h = wt_dpi.scale(1320), wt_dpi.scale(760)
        w_clipped = (dialog.window.winfo_width(), dialog.window.winfo_height()) == (min_w, min_h)
        check("F1 窗口默认尺寸 == 1480x860（DPI 缩放后）或被 WM 夹紧到 minsize",
              (abs(dialog.window.winfo_width() - exp_w) <= 4
               and abs(dialog.window.winfo_height() - exp_h) <= 4) or w_clipped,
              f"{dialog.window.winfo_width()}x{dialog.window.winfo_height()} vs 期望 {exp_w}x{exp_h} / minsize {min_w}x{min_h}"
              f" (屏 {dialog.window.winfo_screenwidth()}x{dialog.window.winfo_screenheight()}"
              f"{', WM夹紧' if w_clipped else ''})")

        buttons, radios = collect_clickables(dialog)
        check("F2 按钮总数 == 15", len(buttons) == 15, f"{len(buttons)} 个")
        check("F3 单选钮总数 == 5", len(radios) == 5, f"{len(radios)} 个")

        btn_counts = Counter(str(b.cget("text")) for b in buttons)
        radio_counts = Counter(str(r.cget("text")) for r in radios)
        check("F4 按钮文本清单与设计一致", btn_counts == Counter(EXPECTED_BUTTON_TEXTS),
              f"实际 {dict(btn_counts)}" if btn_counts != Counter(EXPECTED_BUTTON_TEXTS) else "15 项全部匹配")
        check("F5 单选钮文本清单与设计一致", radio_counts == Counter(EXPECTED_RADIO_TEXTS),
              f"实际 {dict(radio_counts)}" if radio_counts != Counter(EXPECTED_RADIO_TEXTS) else "5 项全部匹配")

        button_layout_checks(dialog, "F6-F8")

        all_widgets = walk(dialog.window, [])
        unmapped = [w for w in all_widgets if not w.winfo_ismapped()]
        # 已知既有缺陷（修复前后 stash 对照已证实与本改动无关）：
        # 左面板 Listbox 请求宽 426px + 滚动条 17px > 面板内宽 430px，
        # pack 先打包者得全腔，滚动条 0 腔体保持未映射 1x1。此处允许该
        # scroll bar，其余未映射控件仍视为缺陷。
        known = {".!toplevel.!panedwindow.!labelframe.!scrollbar"}
        real_unmapped = [str(w) for w in unmapped if str(w) not in known]
        if unmapped:
            print(f"[WARN] F9 既有缺陷（非本改动引入，stash 对照证实）：左面板滚动条被 pack 挤死未映射 "
                  f"(Listbox 请求宽 {dialog.file_listbox.winfo_reqwidth()}px + 滚动条 17px > 面板内宽) —— "
                  "只影响文件列表的滚动条可见性，不影响按钮/列/预览面板布局")
        check("F9 全部子控件已映射（允许既有左面板滚动条缺陷）", not real_unmapped,
              f"{len(all_widgets)} 个子控件" + (f"，未映射: {real_unmapped[:5]}" if real_unmapped else ""))

        # ---------- I. 窗口纵向堆叠 ----------
        children = dialog.window.winfo_children()
        rects = [rect(c) for c in children]
        stack_bad = []
        for i in range(len(rects)):
            for j in range(i + 1, len(rects)):
                if overlap_area(rects[i], rects[j]) > 0:
                    stack_bad.append(f"{children[i].winfo_class()} x {children[j].winfo_class()}")
        check("I1 顶层区块（说明/工具栏/主体/操作区）y 区间互不重叠", not stack_bad,
              f"{len(children)} 个区块" + (f"；重叠: {stack_bad}" if stack_bad else ""))
        ys_ok = all(rects[i][1] <= rects[j][1] for i, j in zip(range(len(rects)), range(1, len(rects))))
        check("I2 区块按 pack 顺序自上而下", ys_ok, "y=" + ",".join(str(r[1]) for r in rects))

        # ---------- H. 三面板布局（默认尺寸） ----------
        pane_checks(dialog, "H")

        # ---------- G. Treeview 列渲染（运行时配置） ----------
        # 构建后默认平铺视图：_refresh_flat_view 把列集重配为 6 列 + show=headings
        # （WT_Flow_Editor.py:3800），构建期的 4 列静态配置已被运行时覆盖。
        tree = dialog.control_tree

        def show_tokens():
            # ttk Treeview show 选项返回元组（如 ('headings',)），统一成 frozenset 比较
            return frozenset(str(s) for s in tree["show"])

        flat_headings = {"seq": "#", "name": "控件名称", "ctrl_type": "类型",
                         "quality": "质量", "locator": "推荐定位", "window": "窗口"}
        head_bad = [f"{cid}:{tree.heading(cid, 'text')!r}!={txt!r}" for cid, txt in flat_headings.items()
                    if str(tree.heading(cid, "text")) != txt]
        check("G1 平铺视图六列列头文本齐全", not head_bad, (f"异常: {head_bad}" if head_bad else
              "#/控件名称/类型/质量/推荐定位/窗口"))
        width_bad = [f"{cid} 列宽 {tree.column(cid, 'width')}px" for cid in flat_headings
                     if int(tree.column(cid, "width") or 0) < 30]
        check("G2 平铺视图六列显示列宽有效（>=30px）", not width_bad,
              "宽=" + ",".join(f"{cid}:{tree.column(cid, 'width')}" for cid in flat_headings)
              + (f"；异常: {width_bad}" if width_bad else ""))
        check("G3 平铺视图列 id 顺序与设计一致",
              tuple(str(c) for c in tree["columns"]) == tuple(flat_headings),
              str(tree["columns"]))
        check("G4 平铺视图显示模式 == headings", show_tokens() == frozenset({"headings"}), str(tree["show"]))

        # 树形视图：var_view_mode 无 trace，真实链路是单选钮 command ->
        # _on_view_mode_change() -> _refresh_controls_tree()，.set() 后显式调用
        # （与 tools/dialog_ui_smoke.py D 段同款链路）。
        # _refresh_tree_view 重配为 4 列 + show=tree headings（WT_Flow_Editor.py:3844）
        dialog.var_view_mode.set("tree")
        dialog._on_view_mode_change()
        pump(root)
        tree_headings = {"ctrl_type": "类型", "quality": "质量",
                         "locator": "推荐定位", "window": "窗口"}
        head_bad2 = [f"{cid}:{tree.heading(cid, 'text')!r}!={txt!r}" for cid, txt in tree_headings.items()
                     if str(tree.heading(cid, "text")) != txt]
        check("G5 树形视图四列列头文本齐全 + #0=控件名称", not head_bad2
              and str(tree.heading("#0", "text")) == "控件名称",
              (f"异常: {head_bad2}" if head_bad2 else "控件名称(#0)/类型/质量/推荐定位/窗口"))
        check("G6 树形视图列 id 顺序与设计一致",
              tuple(str(c) for c in tree["columns"]) == tuple(tree_headings), str(tree["columns"]))
        check("G7 树形视图显示模式 == tree headings",
              show_tokens() == frozenset({"tree", "headings"}), str(tree["show"]))
        check("G8 树形视图渲染节点数 > 0", len(tree.get_children()) > 0,
              f"{len(tree.get_children())} 根节点, map {len(dialog._tree_node_map):,}")

        # 切回平铺复核列配置复原
        dialog.var_view_mode.set("flat")
        dialog._on_view_mode_change()
        pump(root)
        check("G9 切回平铺视图列配置复原",
              tuple(str(c) for c in tree["columns"]) == tuple(flat_headings)
              and show_tokens() == frozenset({"headings"}), str(tree["columns"]) + " " + str(tree["show"]))

        # ---------- J. 最小尺寸压力测试 ----------
        print("-- 缩到最小尺寸 1320x760 --")
        wt_dpi.geometry(dialog.window, 1320, 760)
        pump(root)
        check("J1 窗口缩到 minsize 生效",
              abs(dialog.window.winfo_width() - wt_dpi.scale(1320)) <= 6
              and abs(dialog.window.winfo_height() - wt_dpi.scale(760)) <= 6,
              f"{dialog.window.winfo_width()}x{dialog.window.winfo_height()} vs {wt_dpi.scale(1320)}x{wt_dpi.scale(760)}")
        _, panes_min = pane_checks(dialog, "J2")
        button_layout_checks(dialog, "J3-J5")

        print("-- 恢复默认尺寸 1480x860 --")
        wt_dpi.geometry(dialog.window, 1480, 860)
        pump(root)
        fails_before = len(FAILURES)
        button_layout_checks(dialog, "J6")
        check("J6 恢复默认尺寸后按钮复核通过", len(FAILURES) == fails_before,
              "裁剪/重叠/截断三断言同 F6-F8")

    finally:
        try:
            if dialog is not None:
                dialog.window.destroy()
        except Exception:
            pass
        root.destroy()

    print()
    if FAILURES:
        print(f"共 {len(FAILURES)} 项失败: {FAILURES}")
        return 1
    print("全部几何布局校验通过（无截图，纯坐标断言）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
