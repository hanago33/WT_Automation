# encoding: utf-8
"""读取 MUP 用户数据目录（C:\\ProgramData\\Meteodyn\\MUP），提供流程产物落盘检测。

背景：MUP 每个流程动作都有文件落盘在用户数据目录：
  OROGRAPHY/  <project>_<zone>.tif      —— 导入的地形数据
  TIMESERIES/ *.tim                     —— 导入的测风塔时间序列
  ROUGHNESS/  粗糙度图层文件
  RESULT_FILES/  计算产物
  SYNTHESIS_FILES/ 合成 AEP 产物
自动化"导入/计算/合成"步骤执行后，对比运行前后文件清单即可获得
"是否真生效"的客观铁证（不依赖 UI 读值，避免假成功）。

目录命名：local_<用户名>_<机器GUID>（跨机器变化，用 glob 匹配）。
数据目录可通过环境变量 MUP_DATA_DIR 覆盖（测试/换机场景）。

本模块提供：
- locate_data_dir()：定位用户数据目录
- snapshot()：记录相关子目录全部文件 {子目录: {文件名: {size, mtime}}}
- diff(a, b)：对比两个快照，返回新增/删除/变化（按业务目录归类）
- list_new_files(a, b, dirname)：返回指定业务目录新增文件列表

零外部依赖；数据目录缺失时全部降级为空，不阻塞自动化。
"""
import os
import glob
from functools import lru_cache

MUP_DATA_ROOT = os.environ.get("MUP_DATA_DIR", r"C:\ProgramData\Meteodyn\MUP")

# 关注的业务子目录（相对用户数据目录），中文名用于报告展示
_BUSINESS_DIRS = (
    ("DATAFILES\\OROGRAPHY", "terrain"),
    ("DATAFILES\\TIMESERIES", "timeseries"),
    ("DATAFILES\\ROUGHNESS", "roughness"),
    ("RESULT_FILES", "results"),
    ("SYNTHESIS_FILES", "synthesis"),
)


def locate_data_dir():
    """定位用户数据目录（local_* 子目录，按修改时间取最新）；不可用返回空串。"""
    if not os.path.isdir(MUP_DATA_ROOT):
        return ""
    candidates = glob.glob(os.path.join(MUP_DATA_ROOT, "local_*"))
    if not candidates:
        return ""
    candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return candidates[0]


def _dir_path(business_key):
    """业务目录绝对路径。business_key 为 "terrain"/"timeseries"/..."""
    data_dir = locate_data_dir()
    if not data_dir:
        return ""
    rel = rel_dir(business_key)
    return os.path.join(data_dir, rel) if rel else ""


def snapshot():
    """记录各业务目录文件清单。

    返回 {"terrain": {filename: {"size":int,"mtime":float}}, "timeseries": {...}, ...,
          "_meta": {"data_dir": str}}。
    "_meta" 记录本次快照对应的用户数据目录，供 diff 检测两条快照之间的目录漂移
    （换用户/换机同步可能出现新的 local_* 目录，文件级对比会失真）。
    """
    data_dir = locate_data_dir()
    result = {"_meta": {"data_dir": data_dir}}
    for _, business_key in _BUSINESS_DIRS:
        result[business_key] = {}
        base = os.path.join(data_dir, rel_dir(business_key)) if data_dir else ""
        if not base or not os.path.isdir(base):
            continue
        try:
            for fn in os.listdir(base):
                full = os.path.join(base, fn)
                if not os.path.isfile(full):
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                result[business_key][fn] = {"size": st.st_size, "mtime": st.st_mtime}
        except OSError:
            continue
    return result


def rel_dir(business_key):
    """业务目录相对用户数据目录的路径。business_key 为 "terrain"/"timeseries"/..."""
    return {"terrain": "DATAFILES\\OROGRAPHY", "timeseries": "DATAFILES\\TIMESERIES",
            "roughness": "DATAFILES\\ROUGHNESS", "results": "RESULT_FILES",
            "synthesis": "SYNTHESIS_FILES"}.get(business_key) or ""


def diff(before, after):
    """对比两个快照，返回按业务目录归类的新增/删除/变化文件。

    返回 {
      "new": {business_key: [filename,...]},
      "changed": {business_key: [filename,...]},
      "deleted": {business_key: [filename,...]},
      "newCount": int, "changedCount": int, "deletedCount": int,
      "dirChanged": bool(可选，目录漂移时为 True，此时不产出文件级差异),
    }
    """
    before = before or {}
    after = after or {}
    out = {"new": {}, "changed": {}, "deleted": {}, "newCount": 0, "changedCount": 0, "deletedCount": 0}
    before_meta = before.get("_meta") if isinstance(before.get("_meta"), dict) else {}
    after_meta = after.get("_meta") if isinstance(after.get("_meta"), dict) else {}
    before_dir = before_meta.get("data_dir") or ""
    after_dir = after_meta.get("data_dir") or ""
    if before_dir and after_dir and before_dir != after_dir:
        # 目录漂移：前后快照来自不同的用户数据目录（换用户/换机同步新增 local_*），
        # 文件级对比会大面积假"新增/删除"，只上报漂移标志，避免误导性铁证。
        out["dirChanged"] = True
        return out
    all_keys = set(before) | set(after)
    for key in all_keys:
        if key == "_meta":
            continue
        b = before.get(key) or {}
        a = after.get(key) or {}
        new = [fn for fn in a if fn not in b]
        deleted = [fn for fn in b if fn not in a]
        changed = [
            fn for fn in a
            if fn in b
            and (b[fn].get("size") != a[fn].get("size")
                 or abs((b[fn].get("mtime") or 0) - (a[fn].get("mtime") or 0)) > 2.0)
        ]
        if new:
            out["new"][key] = new
            out["newCount"] += len(new)
        if changed:
            out["changed"][key] = changed
            out["changedCount"] += len(changed)
        if deleted:
            out["deleted"][key] = deleted
            out["deletedCount"] += len(deleted)
    return out


def list_new_files(before, after, business_key):
    """返回指定业务目录新增文件列表（after 相对 before）。"""
    d = diff(before or {}, after or {})
    return d.get("new", {}).get(business_key, [])


def clear_cache():
    """清空目录定位缓存。"""
    # locate_data_dir 未被 lru_cache 装饰，防御性调用（外部可能按惯例调用 clear_cache）
    clear = getattr(locate_data_dir, "cache_clear", None)
    if callable(clear):
        clear()
