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

try:
    import wt_theme
    _DEFAULT_THEME = dict(wt_theme.get_palette())
    _DEFAULT_THEME["shadow"] = "#e2e8f0"
except Exception:
    _DEFAULT_THEME = {
        "bg": "#f8fafc",
        "card": "#ffffff",
        "border": "#e2e8f0",
        "text": "#0f172a",
        "muted": "#64748b",
        "toolbar": "#f1f5f9",
        "shadow": "#e2e8f0",
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
        self.geometry("760x840")
        self.minsize(580, 500)
        self.configure(bg=self.theme["bg"])

        # ── 顶部：板块选择 ──
        top = tk.Frame(self, bg=self.theme["toolbar"], padx=14, pady=10,
                       highlightthickness=1, highlightbackground=self.theme["border"])
        top.pack(fill=tk.X)
        tk.Label(top, text="板块链路：", bg=self.theme["toolbar"], fg=self.theme["text"],
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.LEFT, padx=(4, 6))
        self.section_var = tk.StringVar(value=SECTION_FLOW_MAP.get(default_section, ""))
        self.opt = ttk.Combobox(
            top,
            textvariable=self.section_var,
            values=[v for _, v in SECTION_FLOW_MAP.items()],
            state="readonly",
            width=18,
            font=("Microsoft YaHei UI", 9),
        )
        self.opt.pack(side=tk.LEFT)
        self.opt.bind("<<ComboboxSelected>>", lambda e: self.reload())
        self.title_label = tk.Label(top, text="", bg=self.theme["toolbar"],
                                    fg=self.theme.get("primary", "#2563eb"),
                                    font=("Microsoft YaHei UI", 10, "bold"))
        self.title_label.pack(side=tk.LEFT, padx=(12, 0))

        self.count_badge = tk.Label(
            top, text="0 步", bg=self.theme.get("primary_soft", "#dbeafe"),
            fg=self.theme.get("primary_text", "#1e40af"),
            font=("Microsoft YaHei UI", 9, "bold"), padx=8, pady=2,
        )
        self.count_badge.pack(side=tk.LEFT, padx=(8, 0))

        tk.Label(top, text="点击节点查看详情 · 滚轮滚动视口", bg=self.theme["toolbar"],
                 fg=self.theme["muted"], font=("Microsoft YaHei UI", 9)
                 ).pack(side=tk.RIGHT, padx=(0, 12))

        # ── 动作图例 ──
        legend = tk.Frame(self, bg=self.theme["bg"])
        legend.pack(fill=tk.X, padx=16, pady=(8, 4))
        tk.Label(legend, text="动作图例", bg=self.theme["bg"], fg=self.theme["muted"],
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8))
        for act, col in _LEGEND:
            sw = tk.Canvas(legend, width=12, height=12, bg=self.theme["bg"], highlightthickness=0)
            sw.create_oval(1, 1, 11, 11, fill=col, outline=col)
            sw.pack(side=tk.LEFT, padx=(0, 3))
            tk.Label(legend, text=act, bg=self.theme["bg"], fg=self.theme["muted"],
                 font=("Microsoft YaHei UI", 9)).pack(side=tk.LEFT, padx=(0, 10))

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
            if hasattr(self, "count_badge"):
                self.count_badge.configure(text="0 步")
        else:
            self.nodes = info["nodes"]
            self.title_label.configure(text=info["title"])
            if hasattr(self, "count_badge"):
                self.count_badge.configure(text=f"共 {len(self.nodes)} 步")
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
            self.canvas.itemconfig(box, outline=self.theme.get("border_dark", "#cbd5e1"), width=1.5)
            self.canvas.configure(cursor="")
        return enter, leave

    # ---------- 绘制 ----------
    def _pill(self, c, x, y, text, color):
        w = max(40, len(text) * 7 + 16)
        h = 20
        round_rectangle(c, x, y, x + w, y + h, 9, fill=color, outline=color)
        c.create_text(x + w / 2.0, y + h / 2.0, text=text, fill="#ffffff",
                      font=("Microsoft YaHei UI", 8, "bold"))
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
        width = w if w > 1 else 740
        # 响应式卡片宽度：随视口自适应拉伸，长文本排版更舒适
        node_w = min(740, max(460, width - 64))
        node_h = 108
        gap = 26
        x = max(20, (width - node_w) // 2)
        y = 20
        prev_bottom = None
        for n in self.nodes:
            if prev_bottom is not None:
                self.canvas.create_line(
                    x + node_w // 2, prev_bottom,
                    x + node_w // 2, y,
                    fill="#94a3b8", width=2, arrow=tk.LAST, arrowshape=(8, 10, 5),
                )
            color = _ACTION_COLORS.get(n["action"], _ACTION_COLORS["default"])
            # 阴影层
            round_rectangle(self.canvas, x + 2, y + 3, x + node_w + 2, y + node_h + 3, 10,
                            fill=self.theme.get("shadow", "#e2e8f0"), outline=self.theme.get("shadow", "#e2e8f0"))
            # 卡片主体
            box = round_rectangle(self.canvas, x, y, x + node_w, y + node_h, 10,
                                  fill=self.theme.get("card", "#ffffff"), outline=self.theme.get("border_dark", "#cbd5e1"), width=1.5)
            # 左侧圆形序号 badge（垂直居中）
            cx, cy = x + 34, y + node_h / 2.0
            self.canvas.create_oval(cx - 16, cy - 16, cx + 16, cy + 16,
                                    fill=color, outline=color)
            self.canvas.create_text(cx, cy, text=str(n["index"]), fill="white",
                                    font=("Microsoft YaHei UI", 11, "bold"))

            content_x = x + 62
            avail_w = node_w - 76

            # Row 1 (y + 13)：动作类型胶囊与步骤名称并排并列，杜绝层叠遮挡
            act_text = (n["action"] or "—").upper()
            pill_w = self._pill(self.canvas, content_x, y + 13, act_text, color)
            disp = n["name"] or ""
            name_avail = max(100, avail_w - pill_w - 10)
            if len(disp) > 52:
                disp = disp[:50] + "…"
            self.canvas.create_text(
                content_x + pill_w + 8, y + 14, anchor="nw", text=disp,
                fill=self.theme.get("text", "#0f172a"),
                font=("Microsoft YaHei UI", 10, "bold"),
                width=name_avail,
            )

            # Row 2 (y + 45)：目标控件独占整行，文字不再出界截断
            ctrl = n["control"] or ""
            if ctrl:
                if len(ctrl) > 65:
                    ctrl = ctrl[:63] + "…"
                self.canvas.create_text(
                    content_x, y + 45, anchor="nw", text=f"🎯 控件：{ctrl}",
                    fill=self.theme.get("text_secondary", "#334155"), font=("Microsoft YaHei UI", 9),
                    width=avail_w,
                )
            else:
                desc = n.get("description") or ""
                if desc:
                    if len(desc) > 65:
                        desc = desc[:63] + "…"
                    self.canvas.create_text(
                        content_x, y + 45, anchor="nw", text=f"ℹ️ {desc}",
                        fill=self.theme.get("muted", "#64748b"), font=("Microsoft YaHei UI", 9),
                        width=avail_w,
                    )

            # Row 3 (y + 75)：等待条件或备注
            sub_info = ""
            cw = n.get("continueWhen")
            notes = n.get("notes")
            if cw:
                cw_str = json.dumps(cw, ensure_ascii=False) if isinstance(cw, (dict, list)) else str(cw)
                sub_info = f"⏱ 等待：{cw_str}"
            elif notes:
                sub_info = f"📝 备注：{notes}"
            elif n.get("description") and ctrl:
                sub_info = f"ℹ️ {n.get('description')}"

            if sub_info:
                if len(sub_info) > 70:
                    sub_info = sub_info[:68] + "…"
                self.canvas.create_text(
                    content_x, y + 75, anchor="nw", text=sub_info,
                    fill=self.theme.get("muted", "#64748b"), font=("Microsoft YaHei UI", 8),
                    width=avail_w,
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
        self.canvas.configure(scrollregion=(0, 0, width, max(y + 20, 1)))

    # ---------- 详情 ----------
    def _open_detail(self, node):
        win = tk.Toplevel(self)
        win.title("步骤 #%d 详情" % node["index"])
        win.geometry("600x660")
        win.configure(bg=self.theme["bg"])
        color = _ACTION_COLORS.get(node["action"], _ACTION_COLORS["default"])

        # 头部色条
        header = tk.Frame(win, bg=color)
        header.pack(fill=tk.X)
        tk.Label(header, text="%d. %s" % (node["index"], node["name"]),
                 bg=color, fg="white", font=("Microsoft YaHei UI", 12, "bold"),
                 padx=16, pady=12, anchor="w", wraplength=540, justify="left").pack(side=tk.LEFT)

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
            fr.pack(fill=tk.X, pady=6, padx=(indent * 10, 0))
            tk.Label(fr, text=label, width=14, anchor="ne", bg=self.theme["bg"],
                     fg=self.theme["muted"],
                     font=("Microsoft YaHei UI", 9, "bold")).pack(side=tk.LEFT)
            if isinstance(value, (dict, list)):
                value = json.dumps(value, ensure_ascii=False)
            if not isinstance(value, str):
                value = str(value)
            if len(value) > 1000:
                value = value[:1000] + " …"
            tk.Label(fr, text=value, anchor="nw", bg=self.theme["bg"],
                     fg=color or self.theme["text"],
                     font=("Microsoft YaHei UI", 9, "bold" if bold else "normal"),
                     wraplength=460, justify="left"
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
