#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
txt2wtg_gui.py  —— 图形界面版 TXT -> WTG 转换工具（tkinter，零额外依赖）

功能：
  - 选择/拖拽 TXT 文件
  - 填写机组参数（叶轮直径、额定功率等）
  - 一键生成 .wtg，并显示解析结果与数据校验提示
  - 保存默认参数，下次启动自动填充
"""

import os
import sys
import json
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox

import txt2wtg_core as core

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "txt2wtg_gui_config.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TXT → WTG 转换工具")
        self.resizable(True, True)
        self.minsize(560, 520)
        try:
            self.iconbitmap()
        except Exception:
            pass

        self.cfg = load_config()
        self.input_path = tk.StringVar(value=self.cfg.get("input_path", ""))
        self.out_path = tk.StringVar(value=self.cfg.get("out_path", ""))

        self._build_widgets()
        self._enable_dragdrop()
        self._refresh_out_default()

    # ---- UI 布局 -------------------------------------------------------
    def _build_widgets(self):
        pad = {"padx": 10, "pady": 5}

        # 输入文件
        f_in = ttk.LabelFrame(self, text="1. 输入 TXT 文件（支持表头 / 拖拽到此处）")
        f_in.pack(fill="x", **pad)
        ttk.Entry(f_in, textvariable=self.input_path, width=60).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        ttk.Button(f_in, text="浏览...", command=self._browse_input).pack(
            side="left", padx=(0, 8), pady=8)

        # 输出文件
        f_out = ttk.LabelFrame(self, text="2. 输出 WTG 文件（留空则自动命名）")
        f_out.pack(fill="x", **pad)
        ttk.Entry(f_out, textvariable=self.out_path, width=60).pack(
            side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        ttk.Button(f_out, text="浏览...", command=self._browse_output).pack(
            side="left", padx=(0, 8), pady=8)

        # 参数
        f_param = ttk.LabelFrame(self, text="3. 机组参数")
        f_param.pack(fill="x", **pad)

        fields = [
            ("diameter", "叶轮直径 D (米) *", "220"),
            ("rated", "额定功率 (千瓦) *", "6250"),
            ("cutin", "切入风速 (m/s)", "3.0"),
            ("cutout", "切出风速 (m/s)", "25.0"),
            ("airdensity", "空气密度 (kg/m³)", "1.225"),
            ("hubheight", "轮毂高度 (米)", "120.0"),
            ("manufacturer", "制造商名称", "User"),
        ]
        self.vars = {}
        for i, (key, label, default) in enumerate(fields):
            col = i % 2
            row = i // 2
            ttk.Label(f_param, text=label).grid(
                row=row, column=col * 2, sticky="e", padx=8, pady=4)
            v = tk.StringVar(value=str(self.cfg.get(key, default)))
            self.vars[key] = v
            ttk.Entry(f_param, textvariable=v, width=18).grid(
                row=row, column=col * 2 + 1, sticky="w", padx=8, pady=4)
        f_param.columnconfigure(1, weight=1)
        f_param.columnconfigure(3, weight=1)

        # 曲线预览
        f_prev = ttk.LabelFrame(self, text="4. 曲线预览（功率 / 推力系数 Ct）")
        f_prev.pack(fill="both", expand=True, **pad)
        self.preview = tk.Canvas(f_prev, height=200, bg="white")
        self.preview.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self.preview.bind("<Configure>", lambda e: self._draw_preview())

        # 生成按钮
        f_btn = ttk.Frame(self)
        f_btn.pack(fill="x", **pad)
        ttk.Button(f_btn, text="▶ 生成 WTG 文件", command=self._run).pack(
            side="left", padx=(8, 8), pady=6)
        ttk.Button(f_btn, text="⟳ 刷新预览", command=self._draw_preview).pack(
            side="left", pady=6)
        ttk.Button(f_btn, text="清空日志", command=self._clear_log).pack(
            side="left", pady=6)
        self.status = ttk.Label(f_btn, text="就绪", foreground="gray")
        self.status.pack(side="right", padx=8, pady=6)

        # 日志
        f_log = ttk.LabelFrame(self, text="运行日志")
        f_log.pack(fill="both", expand=True, **pad)
        self.log = scrolledtext.ScrolledText(f_log, height=12, wrap="word")
        self.log.pack(fill="both", expand=True, padx=8, pady=8)

    # ---- 文件选择 ------------------------------------------------------
    def _browse_input(self):
        p = filedialog.askopenfilename(
            title="选择 TXT 数据文件",
            filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if p:
            self.input_path.set(p)
            self._refresh_out_default()
            self._draw_preview()

    def _browse_output(self):
        p = filedialog.asksaveasfilename(
            title="保存 WTG 文件",
            defaultextension=".wtg",
            filetypes=[("WTG 文件", "*.wtg"), ("所有文件", "*.*")])
        if p:
            self.out_path.set(p)

    def _refresh_out_default(self):
        if not self.out_path.get() and self.input_path.get():
            base = os.path.splitext(self.input_path.get())[0]
            self.out_path.set(base + ".wtg")

    # ---- 拖拽支持（Windows WM_DROPFILES）-------------------------------
    def _enable_dragdrop(self):
        try:
            import ctypes
            from ctypes import wintypes
            ole = ctypes.windll.shell32
            WM_DROPFILES = 0x0233

            def wnd_proc(hwnd, msg, wp, lp):
                if msg == WM_DROPFILES:
                    count = ole.DragQueryFile(wp, 0xFFFFFFFF, None, 0)
                    buf = ctypes.create_unicode_buffer(260 * count)
                    names = []
                    for i in range(count):
                        ole.DragQueryFile(wp, i, buf, 260)
                        names.append(buf.value)
                    ole.DragFinish(wp)
                    if names:
                        self.input_path.set(names[0])
                        self._refresh_out_default()
                        self._log(f"已拖入文件：{names[0]}")
                        self._draw_preview()
                    return 1
                # 其它消息交给原窗口过程
                return ctypes.windll.user32.CallWindowProcW(
                    self._old_proc, hwnd, msg, wp, lp)

            self._hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            WNDPROC = ctypes.WINFUNCTYPE(
                wintypes.LPARAM, wintypes.HWND, wintypes.UINT,
                wintypes.WPARAM, wintypes.LPARAM)
            self._new_proc = WNDPROC(wnd_proc)
            self._old_proc = ctypes.windll.user32.SetWindowLongPtrW(
                self._hwnd, -4, self._new_proc)
            ole.DragAcceptFiles(self._hwnd, True)
        except Exception as e:
            # 拖拽失败不影响其余功能
            self._log(f"(提示) 拖拽功能不可用：{e}")

    # ---- 运行 ----------------------------------------------------------
    def _draw_preview(self):
        """在 Canvas 上手绘功率曲线与 Ct 曲线（无第三方绘图库）。"""
        cv = self.preview
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        if w < 10 or h < 10:
            return

        in_p = self.input_path.get().strip()
        if not in_p or not os.path.isfile(in_p):
            cv.create_text(w / 2, h / 2, text="（未选择文件或无数据）",
                           fill="gray", font=("TkDefaultFont", 10))
            return

        try:
            data = core.parse_txt(in_p, verbose=False)
        except Exception as e:
            cv.create_text(w / 2, h / 2, text=f"解析失败：{e}",
                           fill="red", font=("TkDefaultFont", 10))
            return

        if not data:
            cv.create_text(w / 2, h / 2, text="未解析到有效数据点",
                           fill="gray", font=("TkDefaultFont", 10))
            return

        # 边距
        ml, mr, mt, mb = 48, 48, 16, 28
        px0, px1 = ml, w - mr
        py0, py1 = h - mb, mt

        ws = [d[0] for d in data]
        pw = [d[1] for d in data]
        ct = [d[2] for d in data]

        x_min, x_max = 0.0, max(ws) * 1.02 or 1.0
        y_p_max = (max(pw) * 1.05) or 1.0
        y_c_max = max(0.2, min(1.0, max(ct) * 1.1))

        def sx(v):
            return px0 + (v - x_min) / (x_max - x_min) * (px1 - px0)

        def sy_p(v):
            return py0 - (v / y_p_max) * (py0 - py1)

        def sy_c(v):
            return py0 - (v / y_c_max) * (py0 - py1)

        # 网格 + 坐标
        for i in range(5):
            gy = py0 - i * (py0 - py1) / 4
            cv.create_line(px0, gy, px1, gy, fill="#e3e3e3")
            cv.create_text(px0 - 6, gy, text=f"{y_p_max * i / 4:.0f}",
                           fill="#1f77b4", font=("TkDefaultFont", 8),
                           anchor="e")
            cv.create_text(px1 + 6, gy, text=f"{y_c_max * i / 4:.2f}",
                           fill="#ff7f0e", font=("TkDefaultFont", 8),
                           anchor="w")
        for i in range(5):
            gx = px0 + i * (px1 - px0) / 4
            cv.create_line(gx, py0, gx, py1, fill="#e3e3e3")
            cv.create_text(gx, py0 + 12, text=f"{x_min + (x_max - x_min) * i / 4:.1f}",
                           fill="#333", font=("TkDefaultFont", 8), anchor="n")

        # 额定功率线（若有）
        try:
            r = float(self.vars["rated"].get())
            if 0 < r <= y_p_max:
                y = sy_p(r)
                cv.create_line(px0, y, px1, y, fill="#1f77b4", dash=(4, 3))
                cv.create_text(px1 - 2, y - 8, text=f"额定 {r:.0f}",
                               fill="#1f77b4", font=("TkDefaultFont", 8),
                               anchor="e")
        except ValueError:
            pass

        # 曲线
        if len(data) > 1:
            cv.create_line([(sx(v), sy_p(p)) for v, p in zip(ws, pw)],
                           fill="#1f77b4", width=2, smooth=True)
            cv.create_line([(sx(v), sy_c(c)) for v, c in zip(ws, ct)],
                           fill="#ff7f0e", width=2, smooth=True)
        else:
            cv.create_oval(sx(ws[0]) - 3, sy_p(pw[0]) - 3,
                           sx(ws[0]) + 3, sy_p(pw[0]) + 3, fill="#1f77b4")

        # 轴标题
        cv.create_text((px0 + px1) / 2, h - 4, text="风速 (m/s)",
                       fill="#333", font=("TkDefaultFont", 9))
        cv.create_text(12, (py0 + py1) / 2, text="功率 (kW)",
                       fill="#1f77b4", font=("TkDefaultFont", 9), angle=90)
        cv.create_text(w - 8, (py0 + py1) / 2, text="Ct",
                       fill="#ff7f0e", font=("TkDefaultFont", 9), angle=90)

        self.status.configure(
            text=f"预览 {len(data)} 点", foreground="gray")

    def _log(self, msg, level="info"):
        tag = {"info": "black", "warn": "orange", "err": "red",
               "ok": "green"}.get(level, "black")
        self.log.insert("end", msg + "\n", tag)
        self.log.see("end")

    def _clear_log(self):
        self.log.delete("1.0", "end")

    def _run(self):
        in_p = self.input_path.get().strip()
        if not in_p:
            messagebox.showwarning("提示", "请先选择输入 TXT 文件。")
            return
        if not os.path.isfile(in_p):
            messagebox.showerror("错误", f"文件不存在：{in_p}")
            return

        out_p = self.out_path.get().strip() or (
            os.path.splitext(in_p)[0] + ".wtg")

        try:
            d = float(self.vars["diameter"].get())
            r = float(self.vars["rated"].get())
            if d <= 0 or r <= 0:
                raise ValueError("叶轮直径和额定功率必须为正数")
            ci = float(self.vars["cutin"].get())
            co = float(self.vars["cutout"].get())
            rho = float(self.vars["airdensity"].get())
            hh = float(self.vars["hubheight"].get())
            mfr = self.vars["manufacturer"].get().strip() or "User"
        except ValueError as e:
            messagebox.showerror("参数错误", f"参数填写不正确：{e}")
            return

        self.status.configure(text="处理中...", foreground="blue")
        self._log(f"正在读取：{in_p}")
        try:
            out_p, data, warnings = core.convert_file(
                in_p, out_p, d, r, ci, co, rho, hh, mfr, verbose=False)
        except ValueError as e:
            self._log(f"错误：{e}", "err")
            self.status.configure(text="失败", foreground="red")
            return

        self._log(f"✅ 成功读取 {len(data)} 个数据点", "ok")
        for w in warnings:
            self._log(f"⚠️  {w}", "warn")
        self._log(f"✅ 已生成：{out_p}", "ok")
        self._log(
            f"   额定功率 {r / 1000:.2f} MW | 叶轮直径 {d:.1f} m | "
            f"数据点 {len(data)}", "ok")
        self.status.configure(text="完成", foreground="green")

        # 保存配置
        self.cfg.update({
            "input_path": in_p, "out_path": out_p,
            "diameter": d, "rated": r, "cutin": ci, "cutout": co,
            "airdensity": rho, "hubheight": hh, "manufacturer": mfr,
        })
        save_config(self.cfg)


def main():
    # 日志颜色 tag
    app = App()
    app.log.tag_config("red", foreground="red")
    app.log.tag_config("green", foreground="green")
    app.log.tag_config("orange", foreground="#d2691e")
    app.mainloop()


if __name__ == "__main__":
    main()
