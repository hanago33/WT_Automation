# encoding: utf-8
"""读取 MUP 用户配置（user.config），提取投影历史供自动化"设置投影"步骤使用。

user.config 位置：%LOCALAPPDATA%\\Meteodyn\\MUPSmartClient.exe_Url_*\\*\\user.config
（路径中的 exe 哈希/版本号随安装变化，用 glob 匹配）。

关键设置：
- ProjectionSelectionHistory：最近使用过的投影（WKT 数组）
- ProjectionPresets：用户保存的投影预置（可能为空）

本模块提供：
- projection_history()：最近使用投影 [ {name, epsg, wkt} ]
- projection_presets()：用户预置投影
- all_projections()：两者合并去重
- resolve_projection(text)：按名称/EPSG/WKT 片段匹配投影
- status()：诊断

零外部依赖（标准库 xml.etree / glob / re）。
"""
import glob
import os
import re
from functools import lru_cache

# 匹配 WKT 坐标系名称：PROJCS["xxx" / GEOGCS["xxx" / GEOCCS["xxx"
_NAME_PATTERN = re.compile(r'(?:PROJCS|GEOGCS|GEOCCS)\s*\[\s*"([^"]+)"')
# 匹配 EPSG 授权码：AUTHORITY["EPSG", "xxxx"]
_EPSG_PATTERN = re.compile(r'AUTHORITY\s*\[\s*"EPSG"\s*,\s*"(\d+)"\s*\]', re.IGNORECASE)

_SETTING_HISTORY = "ProjectionSelectionHistory"
_SETTING_PRESETS = "ProjectionPresets"


def _config_paths():
    """返回所有 user.config 候选路径（按修改时间新→旧）。"""
    base = os.environ.get("LOCALAPPDATA") or ""
    if not base:
        return []
    paths = glob.glob(
        os.path.join(base, "Meteodyn", "MUPSmartClient.exe_Url_*", "*", "user.config")
    )
    return sorted(paths, key=lambda p: os.path.getmtime(p), reverse=True)


def _read_setting_strings(setting_name):
    """读取 user.config 中指定设置名下的所有 <string> 值（跨所有候选文件）。"""
    try:
        import xml.etree.ElementTree as ET
    except ImportError:
        return []
    out = []
    for path in _config_paths():
        try:
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        for setting in root.iter("setting"):
            if setting.get("name") != setting_name:
                continue
            for node in setting.iter("string"):
                text = (node.text or "").strip()
                if text:
                    out.append(text)
    # 去重（保留顺序）
    seen = set()
    unique = []
    for v in out:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique


def _to_projection(wkt):
    m = _NAME_PATTERN.search(wkt)
    epsg_m = _EPSG_PATTERN.search(wkt)
    return {
        "name": m.group(1) if m else "",
        "epsg": epsg_m.group(1) if epsg_m else "",
        "wkt": wkt,
    }


@lru_cache(maxsize=1)
def projection_history():
    """最近使用的投影列表 [ {name, epsg, wkt} ]。"""
    return [_to_projection(w) for w in _read_setting_strings(_SETTING_HISTORY)]


@lru_cache(maxsize=1)
def projection_presets():
    """用户保存的投影预置列表。"""
    return [_to_projection(w) for w in _read_setting_strings(_SETTING_PRESETS)]


def all_projections():
    """历史 + 预置合并去重（按 name+epsg）。"""
    merged = []
    seen = set()
    for item in projection_history() + projection_presets():
        key = (item["name"], item["epsg"])
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def resolve_projection(text):
    """按名称 / EPSG / WKT 片段匹配投影。

    返回匹配项 dict；未匹配返回 None。EPSG 优先级最高（如 "EPSG:4531" / "4531"），
    其次名称子串（大小写不敏感），最后 WKT 片段。
    """
    t = str(text or "").strip()
    if not t:
        return None
    epsg = None
    m = re.search(r"(?:EPSG[:#]?)?(\d{4,6})", t, re.IGNORECASE)
    if m:
        epsg = m.group(1)
    for item in all_projections():
        if epsg and item["epsg"] == epsg:
            return item
    low = t.lower()
    for item in all_projections():
        if low in item["name"].lower():
            return item
    for item in all_projections():
        if low in item["wkt"].lower():
            return item
    return None


def status():
    """诊断：返回配置来源与投影数量。"""
    return {
        "config_count": len(_config_paths()),
        "config_paths": _config_paths()[:3],
        "history": len(projection_history()),
        "presets": len(projection_presets()),
        "sample": projection_history()[0]["name"] if projection_history() else "",
    }


def clear_cache():
    """清空缓存（user.config 更新后调用）。"""
    projection_history.cache_clear()
    projection_presets.cache_clear()
