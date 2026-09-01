# encoding: utf-8
"""流程链路可视化（只读）—— 内嵌总控台 GUI，纯 tkinter Canvas 自绘。

读取 flow_packages/flow_definition_<板块>.json，按 stepIds 顺序把每个 step
渲染为纵向流程卡片；点击节点弹出该步详情（动作类型、目标控件、键入值 /
notes、等待条件、标签、控件明细等）。

本模块只做「展示」，不修改任何流程引擎或链路文件。
"""
import json
import os
import tkinter as tk
from tkinter import messagebox, ttk

# SIMPLE 板块 key -> 流程文件中文名（用于 flow_definition_<名>.json）
SECTION_FLOW_MAP = {
    "terrain": "导入地形图",
    "weather": "新建气象数据",
    "element": "导入并配置元素",
    "turbine": "新建风机类型",
    "project": "创建一个新建模",
    "cfd": "发送CFD计算",
    "comprehensive": "发送综合计算",
    "export_result": "导出综合计算结果",
}

# 动作类型 -> 主题色（用于节点描边 / 标签）
_ACTION_COLORS = {
    "click": "#2563eb",
    "input": "#059669",
    "type": "#059669",
    "wait": "#64748b",
    "select": "#7c3aed",
    "check": "#0891b2",
    "uncheck": "#0891b2",
    "assert": "#dc2626",
    "default": "#475569",
}

# 图例顺序
_LEGEND = [
    ("click", "#2563eb"),
    ("input", "#059669"),
    ("wait", "#64748b"),
    ("select", "#7c3aed"),
    ("check", "#0891b2"),
    ("assert", "#dc2626"),
]

_DEFAULT_THEME = {
    "bg": "#f4f7fb",
    "card": "#ffffff",
    "border": "#dce4f0",
    "text": "#1f2937",
    "muted": "#64748b",
    "toolbar": "#eaf1fb",
    "shadow": "#e4ebf5",
}


def round_rectangle(c, x1, y1, x2, y2, r=12, **kw):
    """用平滑多边形绘制圆角矩形（tkinter 原生无圆角矩形）。"""
    r = min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0)
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1,
        x2, y1 + r, x2, y2 - r, x2, y2,
        x2 - r, y2, x1 + r, y2, x1, y2,
        x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return c.create_polygon(pts, smooth=True, **kw)


def load_flow_definition(base_dir, section_key):
    """解析流程文件，返回 {'title':..., 'nodes':[...]} 或 None。"""
    name = SECTION_FLOW_MAP.get(section_key)
    if not name:
        return None
    path = os.path.join(base_dir, "flow_packages", "flow_definition_%s.json" % name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    packages = data.get("flowPackages") or []
    if not packages:
        return None
    pkg = packages[0]
    steps_by_id = {s["id"]: s for s in data.get("steps", []) if "id" in s}
    # 主流程 stepIds 为空（未编排草稿）时，回退到 steps 数组自身顺序
    step_ids = pkg.get("stepIds") or [s["id"] for s in data.get("steps", []) if "id" in s]
    nodes = []
    for idx, sid in enumerate(step_ids, 1):
        s = steps_by_id.get(sid)
        if not s:
            continue
        ac = s.get("actionConfig") or {}
        controls = s.get("controls") or []
        target = ""
        if controls:
            c0 = controls[0]
            cname = c0.get("name") or c0.get("labelText") or ""
            tval = c0.get("targetValue")
            target = cname
            if tval:
                target = "%s → %s" % (cname, tval)
        nodes.append({
            "index": idx,
            "id": sid,
            "name": s.get("name", sid),
            "action": ac.get("action", ""),
            "control": target,
            "notes": s.get("notes", ""),
            "continueWhen": s.get("continueWhen"),
            "stepTags": s.get("stepTags", []),
            "description": s.get("description", ""),
            "stepParams": s.get("stepParams", {}),
            "controls": controls,
            "actionConfig": ac,
        })
    return {"title": pkg.get("name", name), "nodes": nodes}


class FlowGraphWindow(tk.Toplevel):
    def __init__(self, parent, base_dir, default_section="comprehensive", theme=None):
        super().__init__(parent)
        self.base_dir = base_dir
        # 合并默认主题，保证 shadow 等键始终存在（调用方 theme 可能不含）
        self.theme = dict(_DEFAULT_THEME)
        self.theme.update(theme or {})
        self.reverse_map = {v: k for k, v in SECTION_FLOW_MAP.items()}
        self.title("流程链路可视化（只读）")
        self.geometry("700x780")
        self.configure(bg=self.theme["bg"])

        # ── 顶部：板块选择 ──
        top = tk.Frame(self, bg=self.theme["toolbar"])
        top.pack(fill=tk.X)
        tk.Label(top, text="板块：", bg=self.theme["toolbar"], fg=self.theme["muted"],
                 font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT, padx=(12, 4))
        self.section_var = tk.StringVar(value=SECTION_FLOW_MAP.get(default_section, ""))
        self.opt = tk.OptionMenu(
            top, self.section_var,
            *[v for _, v in SECTION_FLOW_MAP.items()],
            command=lambda e: self.reload(),
        )
        self.opt.pack(side=tk.LEFT)
        self.title_label = tk.Label(top, text="", bg=self.theme["toolbar"],
                                    fg=self.theme["text"],
                                    font=("Microsoft YaHei UI", 10, "bold"))
        self.title_label.pack(side=tk.LEFT, padx=(10, 0))
        tk.Label(top, text="点击节点看详情 · 滚轮滚动", bg=self.theme["toolbar"],
                 fg=self.theme["muted"], font=("Microsoft YaHei UI", 9)
                 ).pack(side=tk.RIGHT, padx=(0, 12))

        # ── 动作图例 ──
        legend = tk.Frame(self, bg=self.theme["bg"])
        legend.pack(fill=tk.X, padx=16, pady=(8, 2))
        tk.Label(legend, text="动作图例", bg=self.theme["bg"], fg=self.theme["muted"],
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(0, 6))
        for act, col in _LEGEND:
            sw = tk.Label(legend, bg=col, width=2, height=1, relief=tk.FLAT)
            sw.pack(side=tk.LEFT, padx=(0, 2))
            tk.Label(legend, text=act, bg=self.theme["bg"], fg=self.theme["muted"],
                     font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(0, 9))

        # ── 画布 + 滚动条 ──
        self.canvas = tk.Canvas(self, bg=self.theme["bg"], highlightthickness=0)
        self.vscroll = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vscroll.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.vscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Configure>", lambda e: self._draw())

        self.nodes = []
        self.reload()
        # 延迟重绘，确保窗口完成布局后 canvas 拥有正确宽度
        self.after(120, self._draw)

    # ---------- 数据 ----------
    def reload(self):
        cn_name = self.section_var.get()
        section_key = self.reverse_map.get(cn_name, "")
        info = load_flow_definition(self.base_dir, section_key) if section_key else None
        if not info:
            messagebox.showerror("错误", "无法加载板块流程：%s" % cn_name)
            self.nodes = []
            self.title_label.configure(text="")
        else:
            self.nodes = info["nodes"]
            self.title_label.configure(text=info["title"])
            self.title("流程链路可视化 - %s" % info["title"])
        self._draw()

    # ---------- 交互 ----------
    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _make_hover(self, box, color):
        def enter(e):
            self.canvas.itemconfig(box, outline=color, width=3)
            self.canvas.configure(cursor="hand2")
        def leave(e):
            self.canvas.itemconfig(box, outline=self.theme["border"], width=2)
            self.canvas.configure(cursor="")
        return enter, leave

    # ---------- 绘制 ----------
    def _pill(self, c, x, y, text, color):
        w = max(34, len(text) * 7 + 16)
        h = 18
        round_rectangle(c, x, y, x + w, y + h, 9, fill=color, outline=color)
        c.create_text(x + w / 2.0, y + h / 2.0, text=text, fill="#ffffff",
                      font=("Microsoft YaHei UI", 9, "bold"))
        return w

    def _draw(self):
        self.canvas.delete("all")
        if not self.nodes:
            self.canvas.create_text(20, 20, anchor="nw", text="（无流程数据）",
                                    fill=self.theme["muted"],
                                    font=("Microsoft YaHei UI", 11))
            self.canvas.configure(scrollregion=(0, 0, 1, 1))
            return
        w = self.canvas.winfo_width()
        width = w if w > 1 else 660
        node_w, node_h, gap = 460, 94, 32
        x = max(24, (width - node_w) // 2)
        y = 24
        prev_bottom = None
        for n in self.nodes:
            if prev_bottom is not None:
                self.canvas.create_line(
                    x + node_w // 2, prev_bottom,
                    x + node_w // 2, y,
                    fill="#b9c6dc", width=2, arrow=tk.LAST, arrowshape=(7, 9, 4),
                )
            color = _ACTION_COLORS.get(n["action"], _ACTION_COLORS["default"])
            # 阴影
            round_rectangle(self.canvas, x + 2, y + 5, x + node_w + 2, y + node_h + 5, 12,
                            fill=self.theme["shadow"], outline=self.theme["shadow"])
            # 卡片
            box = round_rectangle(self.canvas, x, y, x + node_w, y + node_h, 12,
                                  fill=self.theme["card"], outline=self.theme["border"], width=2)
            # 序号 badge（垂直居中）
            cx, cy = x + 36, y + node_h / 2.0
            self.canvas.create_oval(cx - 16, cy - 16, cx + 16, cy + 16,
                                    fill=color, outline=color)
            self.canvas.create_text(cx, cy, text=str(n["index"]), fill="white",
                                    font=("Microsoft YaHei UI", 13, "bold"))
            # 标题
            disp = n["name"]
            if len(disp) > 30:
                disp = disp[:30] + "…"
            self.canvas.create_text(x + 62, y + 14, anchor="nw", text=disp,
                                    fill=self.theme["text"],
                                    font=("Microsoft YaHei UI", 11, "bold"),
                                    width=node_w - 74)
            # 动作胶囊（单独一行）
            self._pill(self.canvas, x + 62, y + 48, n["action"] or "—", color)
            # 控件名（独占一行，可换行，放宽截断）
            ctrl = n["control"] or ""
            if len(ctrl) > 40:
                ctrl = ctrl[:40] + "…"
            if ctrl:
                self.canvas.create_text(
                    x + 62, y + 70, anchor="nw", text=ctrl,
                    fill=self.theme["muted"], font=("Microsoft YaHei UI", 9),
                    width=node_w - 74,
                )
            # 交互
            tag = "node_%d" % n["index"]
            self.canvas.addtag_withtag(tag, box)
            self.canvas.tag_bind(tag, "<Button-1>",
                                 lambda e, nn=n: self._open_detail(nn))
            enter, leave = self._make_hover(box, color)
            self.canvas.tag_bind(tag, "<Enter>", enter)
            self.canvas.tag_bind(tag, "<Leave>", leave)
            prev_bottom = y + node_h
            y += node_h + gap
        self.canvas.configure(scrollregion=(0, 0, width, max(y, 1)))

    # ---------- 详情 ----------
    def _open_detail(self, node):
        win = tk.Toplevel(self)
        win.title("步骤 #%d 详情" % node["index"])
        win.geometry("560x620")
        win.configure(bg=self.theme["bg"])
        color = _ACTION_COLORS.get(node["action"], _ACTION_COLORS["default"])

        # 头部色条
        header = tk.Frame(win, bg=color)
        header.pack(fill=tk.X)
        tk.Label(header, text="%d. %s" % (node["index"], node["name"]),
                 bg=color, fg="white", font=("Microsoft YaHei UI", 13, "bold"),
                 padx=16, pady=12, anchor="w").pack(side=tk.LEFT)

        # 主体（可滚动）
        body = tk.Frame(win, bg=self.theme["bg"])
        body.pack(fill=tk.BOTH, expand=True, padx=16, pady=14)
        cv = tk.Canvas(body, bg=self.theme["bg"], highlightthickness=0)
        sb = tk.Scrollbar(body, orient="vertical", command=cv.yview)
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        inner = tk.Frame(cv, bg=self.theme["bg"])
        cv.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>",
                   lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.bind("<MouseWheel>",
                lambda e: cv.yview_scroll(int(-1 * (e.delta / 120)), "units"))

        def field(label, value, color=None, bold=False, indent=0):
            fr = tk.Frame(inner, bg=self.theme["bg"])
            fr.pack(fill=tk.X, pady=7, padx=(indent * 10, 0))
            tk.Label(fr, text=label, width=14, anchor="ne", bg=self.theme["bg"],
                     fg=self.theme["muted"],
                     font=("Microsoft YaHei UI", 10)).pack(side=tk.LEFT)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if not isinstance(value, str):
                value = str(value)
            if len(value) > 800:
                value = value[:800] + " …"
            tk.Label(fr, text=value, anchor="nw", bg=self.theme["bg"],
                     fg=color or self.theme["text"],
                     font=("Microsoft YaHei UI", 10, "bold" if bold else "normal"),
                     wraplength=420, justify="left"
                     ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        field("ID", node["id"])
        field("动作", node["action"] or "—", color=color, bold=True)
        field("目标控件", node["control"] or "—")
        if node["notes"]:
            field("备注", node["notes"])
        if node["description"]:
            field("描述", node["description"])
        if node["continueWhen"]:
            field("等待条件", json.dumps(node["continueWhen"], ensure_ascii=False))
        if node["stepTags"]:
            field("标签", ", ".join(node["stepTags"]))
        if node["stepParams"]:
            field("参数", node["stepParams"])
        if node["controls"]:
            ttk.Separator(inner, orient="horizontal").pack(fill=tk.X, pady=(12, 6))
            tk.Label(inner, text="控件明细", bg=self.theme["bg"], fg=self.theme["text"],
                     font=("Microsoft YaHei UI", 11, "bold")
                     ).pack(anchor="w", pady=(0, 4))
            for c in node["controls"]:
                cname = c.get("name") or c.get("labelText") or "(未命名)"
                cvt = c.get("targetValue", "")
                line = cname
                if cvt:
                    line += "  →  " + str(cvt)
                field("·", line)
                if c.get("notes"):
                    field("备注", c.get("notes"), color=self.theme["muted"], indent=1)
                if c.get("optionValues"):
                    field("候选项",
                          ", ".join([str(o) for o in c["optionValues"]]),
                          color=self.theme["muted"], indent=1)
