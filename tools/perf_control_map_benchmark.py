#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WT_Flow_Editor 控件映射导入对话框 —— 性能基准与瓶颈定位脚本。

只读诊断工具：不修改任何业务数据，不改动生产代码。

用于量化三类症状各自的耗时归属：
  1. 打开对话框慢     -> 阶段 S1/S2/S3（加载 / 展平 / 过滤排序）
  2. 加载大文件假死   -> 阶段 S1 + S4（JSON 解析 + Treeview 插入）
  3. 滚动卡顿         -> 阶段 S6（滚动重绘）与 S4 插入行数吞吐曲线

被测对象是 ControlMapImportDialog 的真实方法（通过轻量 Harness 绑定），
不实例化完整对话框，因此无需人工交互即可运行。

用法：
    python tools/perf_control_map_benchmark.py
    python tools/perf_control_map_benchmark.py --limit 5000
    python tools/perf_control_map_benchmark.py --file control_maps/recordings/xxx_map.json --tree
    python tools/perf_control_map_benchmark.py --no-gui --profile
"""
from __future__ import annotations

import argparse
import cProfile
import os
import pstats
import sys
import time
from io import StringIO

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import WT_Flow_Editor as E  # noqa: E402

DEFAULT_FILE = os.path.join(BASE_DIR, "control_maps", "standard", "总控件信息.json")
INSERT_SAMPLE_SIZES = (100, 500, 2000, 5000, 20000)


class _Var:
    """模拟 tkinter 变量的 get/set，供真实业务方法调用。"""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class Harness:
    """绑定 ControlMapImportDialog 真实方法的轻量宿主，仅提供方法依赖的属性。"""

    _real_build_controls = E.ControlMapImportDialog._build_controls_from_payload
    _merge_control_map_control_metadata = E.ControlMapImportDialog._merge_control_map_control_metadata
    _get_filtered_controls = E.ControlMapImportDialog._get_filtered_controls
    _matches_time_filter = E.ControlMapImportDialog._matches_time_filter
    _control_map_timestamp = E.ControlMapImportDialog._control_map_timestamp
    _control_display_fields = E.ControlMapImportDialog._control_display_fields

    def __init__(self, payload, limit=0):
        self.current_payload = payload
        self._controls_cache_key = None
        self._controls_cache_value = []
        self._controls_override = None
        self._limit = limit
        self.default_window_title = ""
        self.var_filter = _Var("")
        self.var_sort = _Var("添加时间-新到旧")
        self.var_time_filter = _Var("全部时间")
        self.var_view_mode = _Var("flat")
        self.var_file_scope = _Var("master")

    def _build_controls_from_payload(self):
        controls = self._real_build_controls()
        if self._limit and len(controls) > self._limit:
            self._controls_override = controls[: self._limit]
            return self._controls_override
        self._controls_override = controls
        return controls


class Timer:
    def __init__(self):
        self.elapsed = 0.0

    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc):
        self.elapsed = time.perf_counter() - self._start
        return False


def fmt_ms(seconds):
    return f"{seconds * 1000:,.1f} ms"


def fmt_size(num_bytes):
    return f"{num_bytes / 1024 / 1024:,.2f} MB"


def hr(title=""):
    line = "=" * 78
    print(f"\n{line}")
    if title:
        print(f"  {title}")
        print(line)


# --------------------------------------------------------------------------
# 数据阶段
# --------------------------------------------------------------------------
def stage_file_list(sample_loads=1):
    """复现 _refresh_file_list 的前半段：目录扫描 + 元数据索引命中率。

    这是打开对话框的第一步。索引失效时会逐个 json.load 未命中文件，
    本阶段用中位数大小的文件抽样测吞吐，再外推全量耗时（避免真跑 1.36GB）。
    """
    hr("S0  控件库文件列表扫描 (_refresh_file_list)")
    root_dir = E.CONTROL_MAP_DIR
    print(f"控件库目录 : {root_dir}")

    t0 = time.perf_counter()
    collected = []
    for dir_root, _sub_dirs, dir_file_names in os.walk(root_dir):
        for file_name in dir_file_names:
            if file_name.lower().endswith(".json") and not file_name.startswith("."):
                collected.append((file_name, os.path.join(dir_root, file_name)))
    t_walk = time.perf_counter() - t0
    print(f"os.walk 扫描 : {fmt_ms(t_walk)}   ->   {len(collected):,} 个 json")

    index_path = os.path.join(root_dir, ".library_index.json")
    has_index = os.path.exists(index_path)
    cache = {}
    t_index = 0.0
    if has_index:
        t0 = time.perf_counter()
        payload = E.load_json_file(index_path)
        t_index = time.perf_counter() - t0
        if isinstance(payload, dict) and isinstance(payload.get("files"), dict):
            cache = payload["files"]
    print(f"元数据索引   : {'存在' if has_index else '缺失'}  "
          f"({fmt_size(os.path.getsize(index_path)) if has_index else '-'})  "
          f"加载 {fmt_ms(t_index)}  条目 {len(cache):,}")

    hits = 0
    misses = []
    miss_bytes = 0
    for _name, path in collected:
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            continue
        cached = cache.get(path)
        if isinstance(cached, dict) and abs(float(cached.get("mtime", 0) or 0) - mtime) < 1.0:
            hits += 1
        else:
            misses.append(path)
            try:
                miss_bytes += os.path.getsize(path)
            except Exception:
                pass
    print(f"索引命中     : {hits:,}    未命中 : {len(misses):,}  ({fmt_size(miss_bytes)})")

    est = 0.0
    if misses:
        ordered = sorted(misses, key=lambda p: os.path.getsize(p))
        probes = ordered[len(ordered) // 2: len(ordered) // 2 + sample_loads] or ordered[:1]
        probe_bytes = sum(os.path.getsize(p) for p in probes)
        t0 = time.perf_counter()
        for path in probes:
            E.load_json_file(path)
        elapsed = time.perf_counter() - t0
        rate = probe_bytes / elapsed if elapsed > 0 else 0.0
        est = miss_bytes / rate if rate > 0 else 0.0
        print(f"抽样 {len(probes)} 个未命中文件 json.load : {fmt_ms(elapsed)} "
              f"({rate / 1024 / 1024:,.1f} MB/s)")
        print(f">> 索引未命中时，本阶段预计阻塞 {est:,.1f} s（需 json.load {fmt_size(miss_bytes)}）")
        print(">> 该时段在主线程同步执行，界面表现为「假死」。")
    else:
        print(">> 索引完全命中，文件列表阶段几乎无阻塞。")

    return {"walk_s": t_walk, "index_s": t_index, "hits": hits,
            "misses": len(misses), "miss_bytes": miss_bytes, "miss_est_s": est}


def stage_load(path):
    hr("S1  JSON 加载")
    size = os.path.getsize(path)
    with Timer() as t:
        payload = E.load_json_file(path)
    flat_count = len(payload.get("flatControls", []) or []) if isinstance(payload, dict) else 0
    defs_count = len(payload.get("controlDefinitions", []) or []) if isinstance(payload, dict) else 0
    has_tree = isinstance(payload, dict) and isinstance(payload.get("controlsTree"), dict)
    print(f"文件         : {os.path.relpath(path, BASE_DIR)}")
    print(f"磁盘大小     : {fmt_size(size)}")
    print(f"加载耗时     : {fmt_ms(t.elapsed)}   ({size / 1024 / 1024 / max(t.elapsed, 1e-9):,.1f} MB/s)")
    print(f"flatControls : {flat_count:,}")
    print(f"controlDefs  : {defs_count:,}")
    print(f"controlsTree : {'有' if has_tree else '无'}")
    return payload, {"load_s": t.elapsed, "size": size,
                     "flat_count": flat_count, "defs_count": defs_count, "has_tree": has_tree}


def stage_flatten(harness):
    hr("S2  控件展平 (normalize + metadata merge)")
    with Timer() as cold:
        controls_cold = harness._build_controls_from_payload()
    with Timer() as warm:
        harness._build_controls_from_payload()

    # 缓存健康度诊断：生产代码用 `self._controls_cache_key is self.current_payload`
    # 做对象身份比较。热路径耗时 >> 冷路径则说明缓存未生效。
    is_hit = harness._controls_cache_key is harness.current_payload

    print(f"控件总数     : {len(controls_cold):,}")
    print(f"首次(冷)     : {fmt_ms(cold.elapsed)}")
    print(f"缓存命中(热) : {fmt_ms(warm.elapsed)}")
    print()
    if is_hit and warm.elapsed < 0.001:
        print("[CACHE OK] 热路径命中缓存（对象身份比较生效），重复刷新零重建。")
    elif is_hit:
        print(f"[CACHE OK] 命中，耗时 {fmt_ms(warm.elapsed)}")
    else:
        print("[CACHE BROKEN] 热路径未命中缓存，每次刷新全量重建——回退到修复前行为。")
    return {"cold_s": cold.elapsed, "warm_s": warm.elapsed,
            "count": len(controls_cold), "cache_ok": bool(is_hit)}


def stage_filter(harness, do_profile):
    hr("S3  过滤 + 排序 (_get_filtered_controls)")
    if do_profile:
        profiler = cProfile.Profile()
        profiler.enable()
        controls = harness._get_filtered_controls()
        profiler.disable()
        stream = StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("tottime").print_stats(12)
        print("--- cProfile top 12 (by tottime) ---")
        print(stream.getvalue().strip())
        print("-" * 78)
    else:
        with Timer() as t:
            controls = harness._get_filtered_controls()
        print(f"结果控件数   : {len(controls):,}")
        print(f"耗时         : {fmt_ms(t.elapsed)}")

    raw = harness._controls_override or []
    keyword = harness.var_filter.get().strip().lower()

    def haystack_of(control):
        return " ".join([
            str(control.get("id", "")),
            str(control.get("name", "")),
            str(control.get("windowTitle", "")),
            str(control.get("targetMethod", "")),
            str(control.get("targetValue", "")),
            str(control.get("_qualityTier", "")),
            str(control.get("_qualityReason", "")),
            str(control.get("labelText", "") or (control.get("inspectData", {}) or {}).get("labelText", "")),
            str((control.get("inspectData", {}) or {}).get("className", "")),
            str((control.get("inspectData", {}) or {}).get("controlType", "")),
            str(control.get("_addedAt", "")),
        ]).lower()

    # 分解 1：仅过滤（与生产一致的 haystack + 时间过滤），不排序
    with Timer() as t_filter:
        kept = [c for c in raw if (not keyword or keyword in haystack_of(c))
                and harness._matches_time_filter({"scanTime": c.get("_addedAt", "")})]

    # 分解 2：当前实现的完整过滤 + 排序
    with Timer() as t2:
        controls = harness._get_filtered_controls()

    # 分解 3：绕过 lru_cache 的裸排序成本（修复前行为：每控件实时 strptime 解析）
    # 生产函数 control_map_timestamp 带 lru_cache，这里直接用未缓存的解析路径量化收益。
    def _raw_timestamp(value):
        text = str(value or "").strip()
        if not text:
            return 0.0
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                from datetime import datetime as _dt
                return _dt.strptime(text, fmt).timestamp()
            except Exception:
                continue
        try:
            from datetime import datetime as _dt
            return _dt.fromisoformat(text).timestamp()
        except Exception:
            return 0.0

    with Timer() as t3b:
        decorated = [(_raw_timestamp(c.get("_addedAt", "")),
                      str(c.get("name", "")).strip(), c) for c in kept]
        decorated.sort(key=lambda item: (item[0], item[1]), reverse=True)
        _ = [item[2] for item in decorated]

    sort_now = max(t2.elapsed - t_filter.elapsed, 0.0)
    sort_uncached = max(t3b.elapsed, 0.0)
    print(f"结果控件数   : {len(controls):,}")
    print(f"完整耗时     : {fmt_ms(t2.elapsed)}")
    print(f"  ├─ 过滤段  : {fmt_ms(t_filter.elapsed)}")
    print(f"  └─ 排序段  : {fmt_ms(sort_now)}  (lru_cache 生效)")
    print(f"  无缓存对照 : {fmt_ms(sort_uncached)}  (lru_cache 省约 {fmt_ms(max(sort_uncached - sort_now, 0.0))})")
    return {"filter_s": t2.elapsed, "filter_only_s": t_filter.elapsed,
            "sort_s": sort_now, "sort_best_s": sort_now, "sort_uncached_s": sort_uncached,
            "count": len(controls)}


# --------------------------------------------------------------------------
# GUI 阶段
# --------------------------------------------------------------------------
def stage_treeview(controls, sizes, do_tree, payload, visible=False):
    hr("S4  Treeview 插入吞吐 (flat 视图)")
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception as exc:
        print(f"跳过：tkinter 不可用 ({exc})")
        return {}

    root = tk.Tk()
    root.withdraw()
    results = []
    total = len(controls)
    sizes = [n for n in sizes if n <= total] + ([total] if total > max(sizes) else [])

    for n in sizes:
        tree = ttk.Treeview(
            root,
            columns=("seq", "name", "ctrl_type", "quality", "locator", "window"),
            show="headings",
            height=24,
        )
        tree.pack(fill=tk.BOTH, expand=True)
        for col, w in (("seq", 40), ("name", 165), ("ctrl_type", 75),
                       ("quality", 65), ("locator", 165), ("window", 115)):
            tree.heading(col, text=col)
            tree.column(col, width=w)
        root.update()

        t0 = time.perf_counter()
        for i in range(n):
            control = controls[i]
            name, quality, ctype, locator, window = E.ControlMapImportDialog._control_display_fields(control)
            tree.insert("", tk.END, iid=str(i), values=(i + 1, name, ctype, quality, locator, window))
        t_insert = time.perf_counter() - t0

        t1 = time.perf_counter()
        root.update()
        t_layout = time.perf_counter() - t1

        t2 = time.perf_counter()
        tree.delete(*tree.get_children())
        root.update()
        t_clear = time.perf_counter() - t2

        per_row = (t_insert / n * 1e6) if n else 0.0
        results.append((n, t_insert, t_layout, t_clear, per_row))
        tree.destroy()

    print(f"{'插入行数':>10} | {'插入耗时':>12} | {'布局/绘制':>12} | {'清空耗时':>12} | {'单行':>10}")
    print("-" * 78)
    for n, t_insert, t_layout, t_clear, per_row in results:
        print(f"{n:>10,} | {fmt_ms(t_insert):>12} | {fmt_ms(t_layout):>12} | "
              f"{fmt_ms(t_clear):>12} | {per_row:>8.1f} us")

    if results:
        last = results[-1]
        projected = {n: t for n, t, _, _, _ in results}
        print(f"\n当前数据量 {total:,} 行 -> 插入 {fmt_ms(last[1])} + 布局 {fmt_ms(last[2])}"
              f" = {fmt_ms(last[1] + last[2])}")
        if len(results) >= 2:
            small, large = results[0], results[-1]
            if small[0] and large[0] != small[0]:
                scale = large[0] / small[0]
                ratio = (large[1] / small[1]) if small[1] > 0 else 0
                print(f"规模放大 {scale:,.1f}x 时插入耗时放大 {ratio:,.1f}x -> "
                      f"{'线性增长(接近 O(n))' if ratio > scale * 0.6 else '次线性'}")

    scroll_ms = 0.0
    if results and results[-1][0] > 0:
        hr("S6  滚动重绘")
        if not visible:
            print("跳过真实滚动测量：窗口 withdraw 时 Tk 不产生实际绘制，测得的是 no-op。")
            print("                  加 --visible 开启真实绘制测量（会短暂弹窗，约 3 秒）。")
        else:
            n = min(total, 20000)
            tree = ttk.Treeview(root, columns=("seq", "name"), show="headings", height=24)
            tree.pack(fill=tk.BOTH, expand=True)
            for i in range(n):
                tree.insert("", tk.END, iid=str(i), values=(i + 1, controls[i].get("name", "")))
            root.geometry("900x600+40+40")
            root.deiconify()
            root.update()
            steps = 30
            t0 = time.perf_counter()
            for k in range(steps):
                tree.yview_moveto((k % steps) / steps)
                root.update()
            scroll_ms = (time.perf_counter() - t0) * 1000
            print(f"行数 {n:,} / 滚动 {steps} 帧 : {scroll_ms:,.1f} ms  "
                  f"(单帧 {scroll_ms / steps:,.2f} ms)")
            root.withdraw()
            tree.destroy()

    tree_stats = {}
    if do_tree:
        tree_stats = stage_tree_view(payload, root)

    root.destroy()
    return {"insert_rows": results, "scroll_ms": scroll_ms, "tree": tree_stats}


def stage_tree_view(payload, root):
    """树形视图 controlsTree 递归渲染：分离「节点命名计算」与「Tk 层级插入」两部分。"""
    hr("S5  树形视图递归渲染 (controlsTree)")
    tree_data = payload.get("controlsTree") if isinstance(payload, dict) else None
    if not isinstance(tree_data, dict):
        print("该 payload 无 controlsTree（master 等派生产物无此字段），跳过。")
        return {}

    import tkinter as tk
    from tkinter import ttk

    t0 = time.perf_counter()
    try:
        from build_control_map_library import _extract_functional_name
    except Exception as exc:
        print(f"import build_control_map_library 失败，跳过: {exc}")
        return {}
    t_import = time.perf_counter() - t0
    print(f"import build_control_map_library : {fmt_ms(t_import)}  (8948 行模块，首次加载)")

    flat = payload.get("flatControls", []) or []
    flat_by_index = {i: item for i, item in enumerate(flat)}

    ordered = []

    def walk(node, parent_idx, depth):
        idx = len(ordered)
        ordered.append((node, parent_idx, depth))
        for child in node.get("children", []) or []:
            if isinstance(child, dict):
                walk(child, idx, depth + 1)

    try:
        walk(tree_data, -1, 0)
    except RecursionError:
        print("控件树过深，递归失败，跳过。")
        return {}
    print(f"controlsTree 节点数 : {len(ordered):,}")

    t0 = time.perf_counter()
    for node, _pidx, _depth in ordered:
        try:
            ctrl = flat_by_index.get(int(node.get("index", -1)), node)
        except Exception:
            ctrl = node
        _extract_functional_name(ctrl)
    t_name = time.perf_counter() - t0
    print(f"节点命名计算        : {fmt_ms(t_name)}  ({t_name / len(ordered) * 1e6:,.1f} us/节点)")

    widget = ttk.Treeview(root, columns=("ctrl_type", "quality", "locator", "window"),
                          show="tree headings", height=24)
    widget.pack(fill=tk.BOTH, expand=True)
    root.update()

    iids = [None] * len(ordered)
    t0 = time.perf_counter()
    for idx, (node, pidx, depth) in enumerate(ordered):
        try:
            ctrl = flat_by_index.get(int(node.get("index", -1)), node)
        except Exception:
            ctrl = node
        iid = f"hierarchy:{idx}"
        widget.insert(
            "" if pidx < 0 else iids[pidx], tk.END, iid=iid,
            open=(depth < 2),
            text=str(ctrl.get("displayName", "") or ctrl.get("name", "") or "?")[:40],
            values=(str(ctrl.get("controlType", "")), str(ctrl.get("qualityTier", "")),
                    "", str(ctrl.get("windowTitle", ""))),
        )
        iids[idx] = iid
    root.update()
    t_insert = time.perf_counter() - t0
    print(f"Tk 层级插入         : {fmt_ms(t_insert)}  ({t_insert / len(ordered) * 1e6:,.1f} us/节点)")
    widget.destroy()
    return {"nodes": len(ordered), "name_s": t_name, "insert_s": t_insert, "import_s": t_import}


# --------------------------------------------------------------------------
# 结论
# --------------------------------------------------------------------------
def verdict(scan_stats, data_stats, flat_stats, filter_stats, gui_stats, total_controls,
            app_stats=None):
    hr("瓶颈判定")
    items = []
    if scan_stats and scan_stats.get("miss_est_s"):
        items.append(("S0 文件列表扫描(索引未命中)", scan_stats["miss_est_s"]))
    if data_stats:
        items.append(("S1 JSON 加载", data_stats["load_s"]))
    if flat_stats:
        items.append(("S2 控件展平", flat_stats["cold_s"]))
    if filter_stats:
        items.append(("S3 过滤+排序", filter_stats["filter_s"]))
    if gui_stats and gui_stats.get("insert_rows"):
        rows = gui_stats["insert_rows"]
        full = rows[-1]
        items.append((f"S4 Treeview 插入 {full[0]:,} 行", full[1] + full[2]))

    total_ms = sum(v for _, v in items) * 1000
    print(f"{'阶段':<34} | {'耗时':>12} | 占比")
    print("-" * 78)
    for name, sec in sorted(items, key=lambda kv: kv[1], reverse=True):
        pct = (sec * 1000 / total_ms * 100) if total_ms else 0
        bar = "#" * int(pct / 2)
        print(f"{name:<34} | {fmt_ms(sec):>12} | {pct:>5.1f}% {bar}")
    print(f"{'合计':<34} | {fmt_ms(total_ms / 1000):>12} |")

    top = max(items, key=lambda kv: kv[1]) if items else None
    print()
    if top:
        print(f"首要瓶颈: {top[0]}  ({fmt_ms(top[1])})")

    if gui_stats and gui_stats.get("insert_rows"):
        rows = gui_stats["insert_rows"]
        full = rows[-1]
        per_row_ms = full[4] / 1000.0
        print(f"插入单价  : {full[4]:,.1f} us/行 -> {total_controls:,} 行需 "
              f"{total_controls * per_row_ms / 1000:,.2f} s")
        page = min(total_controls, 500)
        print(f"若只插首页: {page:,} 行约 {page * per_row_ms:,.1f} ms "
              f"(对比全量 {full[1] * 1000:,.1f} ms)")

    if flat_stats and not flat_stats.get("cache_ok", True):
        print("缓存异常   : 控件列表缓存未命中，热路径仍在全量重建，需回查 _build_controls_from_payload。")

    if filter_stats and filter_stats.get("sort_uncached_s", 0) > 0:
        gain = filter_stats["sort_uncached_s"] - filter_stats["sort_s"]
        if gain > 0.05:
            print(f"时间戳缓存 : lru_cache 让排序段省约 {fmt_ms(gain)}"
                  f"  (裸解析 {fmt_ms(filter_stats['sort_uncached_s'])} -> {fmt_ms(filter_stats['sort_s'])})")

    if app_stats:
        print(f"主编辑器构建: {fmt_ms(app_stats['build_s'] + app_stats['layout_s'])}  "
              f"({app_stats['widgets']:,} widgets, {app_stats['canvases']} 个 Canvas)")
        if app_stats.get("scroll_ms"):
            per_frame = app_stats["scroll_ms"] / 30
            tag = "卡顿(<60fps)" if per_frame > 16.7 else "流畅"
            print(f"Canvas 滚动 : {per_frame:,.2f} ms/帧  [{tag}]")


def stage_preview(controls, samples=300):
    """选中行变化时重建预览文本的成本（点击 / 键盘导航每次都触发）。"""
    hr("S8  选中预览构建 (_format_control_detail_for_display)")
    if not controls:
        return {}
    fmt = E.ControlMapImportDialog._format_control_detail_for_display
    probe = controls[:samples]
    t0 = time.perf_counter()
    for control in probe:
        fmt(None, control)  # 该方法不依赖 self，传 None 即可
    elapsed = time.perf_counter() - t0
    per_call = elapsed / len(probe)
    print(f"样本 {len(probe):,} 个控件 : {fmt_ms(elapsed)}  ({per_call * 1e6:,.1f} us/次)")
    print("每次选中变化（点击 / 方向键 / 滚动后点选）触发一次；")
    print("若该值 > 2ms 且用户连续方向键浏览，会表现为明显粘滞。")
    return {"per_call_s": per_call}


def count_widgets(widget):
    total = 1
    for child in widget.winfo_children():
        total += count_widgets(child)
    return total


def find_canvases(widget, out):
    if widget.winfo_class() == "Canvas":
        out.append(widget)
    for child in widget.winfo_children():
        find_canvases(child, out)
    return out


def stage_app(visible):
    """主编辑器构建耗时与 Canvas 可滚动面板的滚动开销。

    Treeview 自身滚动很快（见 S6），主窗口的 Canvas 内嵌面板才是
    Tkinter 经典卡顿点：面板内 widget 越多，滚动重绘代价越高。
    """
    hr("S7  主编辑器 FlowEditorApp 构建 + Canvas 可滚动面板")
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()
    try:
        t0 = time.perf_counter()
        E.FlowEditorApp(root)
        t_build = time.perf_counter() - t0
        t0 = time.perf_counter()
        root.update()
        t_layout = time.perf_counter() - t0
    except Exception as exc:
        print(f"FlowEditorApp 构建失败，跳过: {type(exc).__name__}: {exc}")
        root.destroy()
        return {}

    widgets = count_widgets(root)
    canvases = find_canvases(root, [])
    print(f"构建耗时    : {fmt_ms(t_build)}")
    print(f"首次布局    : {fmt_ms(t_layout)}")
    print(f"widget 总数 : {widgets:,}")
    print(f"Canvas 数量 : {len(canvases)}")

    scroll_ms = 0.0
    if canvases:
        target = max(canvases, key=count_widgets)
        inner = count_widgets(target)
        print(f"最大 Canvas 内嵌 widget : {inner:,}")
        if visible:
            root.geometry("1400x900+20+20")
            root.deiconify()
            root.update()
            steps = 30
            t0 = time.perf_counter()
            for k in range(steps):
                target.yview_moveto((k % steps) / steps)
                root.update()
            scroll_ms = (time.perf_counter() - t0) * 1000
            print(f"Canvas 滚动 : {scroll_ms:,.1f} ms / {steps} 帧 "
                  f"(单帧 {scroll_ms / steps:,.2f} ms)")
            root.withdraw()
        else:
            print("加 --visible 可测该 Canvas 的真实滚动绘制开销。")
    root.destroy()
    return {"build_s": t_build, "layout_s": t_layout, "widgets": widgets,
            "canvases": len(canvases), "scroll_ms": scroll_ms}


def pick_file(rule):
    """按规则挑选 control_map 文件，避免命令行传递中文路径的编码问题。"""
    if rule == "master":
        return DEFAULT_FILE
    root_dir = os.path.join(BASE_DIR, "control_maps")
    found = []
    for dir_root, _dirs, names in os.walk(root_dir):
        for name in names:
            if name.lower().endswith(".json") and not name.startswith("."):
                found.append(os.path.join(dir_root, name))
    if not found:
        return DEFAULT_FILE
    found.sort(key=lambda p: os.path.getsize(p), reverse=(rule == "largest"))
    return found[0]


def main():
    parser = argparse.ArgumentParser(description="控件映射导入对话框性能基准")
    parser.add_argument("--file", default=None, help="待测 control_map JSON 路径")
    parser.add_argument("--pick", choices=["master", "largest", "smallest"], default="master",
                        help="按规则自动选文件，避免命令行传中文路径的编码问题")
    parser.add_argument("--limit", type=int, default=0, help="只取前 N 个控件（用于吞吐曲线外推）")
    parser.add_argument("--no-gui", action="store_true", help="跳过 Treeview/滚动测量")
    parser.add_argument("--profile", action="store_true", help="对过滤阶段输出 cProfile")
    parser.add_argument("--tree", action="store_true", help="额外测量 controlsTree 递归渲染")
    parser.add_argument("--visible", action="store_true",
                        help="真实显示窗口以测量滚动绘制开销（会短暂弹窗）")
    parser.add_argument("--no-scan", action="store_true", help="跳过 S0 控件库目录扫描测量")
    parser.add_argument("--app", action="store_true", help="额外测量主编辑器构建与 Canvas 滚动")
    args = parser.parse_args()

    path = args.file if args.pick == "master" and not args.file else None
    if path is None:
        path = pick_file(args.pick)
    elif not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    if not os.path.exists(path):
        print(f"文件不存在: {path}")
        return 2

    print("WT_Flow_Editor 控件映射导入对话框 —— 性能基准")
    print(f"数据源: {path}")

    scan_stats = {}
    if not args.no_scan:
        scan_stats = stage_file_list()

    payload, data_stats = stage_load(path)
    if not isinstance(payload, dict):
        print("payload 不是 dict，终止。")
        return 1

    harness = Harness(payload, args.limit)
    flat_stats = stage_flatten(harness)
    filter_stats = stage_filter(harness, args.profile)
    stage_preview(harness._controls_override or [])

    gui_stats = {}
    if not args.no_gui:
        controls = harness._controls_override or []
        if controls:
            gui_stats = stage_treeview(controls, INSERT_SAMPLE_SIZES, args.tree, payload, args.visible)
        else:
            print("\n无控件数据，跳过 GUI 阶段。")

    app_stats = {}
    if args.app:
        app_stats = stage_app(args.visible)

    verdict(scan_stats, data_stats, flat_stats, filter_stats, gui_stats,
            flat_stats["count"], app_stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
