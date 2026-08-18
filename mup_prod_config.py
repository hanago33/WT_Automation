# encoding: utf-8
"""结构化提取 MUP 安装目录 PROD_default_parameters.ini（软件行为参数库）。

价值（对自动化流程）：
- roughness_pairs()：官方"粗糙度源 ↔ 索引文件"映射（实测 8 组，含 Config_5 并列
  WC10_2020↔ESA2020.txt / WC10_2021↔ESA2021.txt）。可用于"选择粗糙度索引文件"
  步骤的后置一致性校验（选了 ESA2020.txt 应对应 rough:WC10_2020 源）。
- roughness_source_to_correspondance() / correspondance_to_roughness_source()：
  正/反向查询，供 Agent 编排与运行校验。
- rough_layers()：GeoData_RoughLayers 可用图层清单。
- get_parameter()：按需取任意参数（CFD/DAS/NS/OPT、IO 目录、Cluster 等）。

安装目录复用 mup_assets 的探测（configure_mup_install_dir / install_dir），
零外部依赖；缺失时全部降级为空，不阻塞自动化。
"""
import os
from functools import lru_cache

from mup_assets import configure_mup_install_dir, install_dir, clear_cache as _clear_asset_cache

PROD_FILE_NAME = "PROD_default_parameters.ini"
CUSTOM_FILE_NAME = "Customer_default_parameters.ini"


def _prod_path():
    d = install_dir()
    return os.path.join(d, PROD_FILE_NAME) if d else ""


def _custom_path():
    d = install_dir()
    return os.path.join(d, CUSTOM_FILE_NAME) if d else ""


def _read_lines(path):
    """读取 INI 有效行（去空行/注释），返回 [(key, value)]（保留重复键顺序）。"""
    if not path or not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith(";") or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k:
                    out.append((k, v))
    except OSError:
        return []
    return out


@lru_cache(maxsize=2)
def _prod_lines():
    return _read_lines(_prod_path())


@lru_cache(maxsize=2)
def _custom_lines():
    return _read_lines(_custom_path())


def _merged_lines():
    """PROD 为基座，Customer 覆盖（后出现者覆盖前者）。"""
    lines = list(_prod_lines())
    seen = {k for k, _ in lines}
    for k, v in _custom_lines():
        if k in seen:
            lines = [(k2, v2) for k2, v2 in lines if k2 != k]
        lines.append((k, v))
        seen.add(k)
    return lines


def roughness_pairs():
    """官方"粗糙度源 ↔ 索引文件"配对列表（按 Config_N 出现顺序）。

    返回 [(source, correspondance_file), ...]；source 形如 "rough:WC10_2020"。

    配对按配置段（键前缀，如 Config_0 / MTDSiteGeoServer）进行，而不是把两个独立
    列表按序号对齐——后者在某个段仅出现一个键（RoughnessSourceName 或
    CorrespondanceFileName 缺失）时，后续所有配对会整体错位且无任何告警。
    """
    pairs = []
    seen_sections = set()
    for key, _ in _merged_lines():
        suffix = None
        if key.endswith("_RoughnessSourceName"):
            suffix = "_RoughnessSourceName"
        elif key.endswith("_CorrespondanceFileName"):
            suffix = "_CorrespondanceFileName"
        if suffix is None:
            continue
        section = key[: -len(suffix)]
        if section in seen_sections:
            continue
        seen_sections.add(section)
        source = get_parameter(section + "_RoughnessSourceName")
        file_name = get_parameter(section + "_CorrespondanceFileName")
        # 段内任一键缺失/为空即跳过该段（避免错位），不静默凑数
        if source and file_name:
            pairs.append((source, file_name))
    return pairs


def roughness_default():
    """默认粗糙度源配置（MTDSiteGeoServer_RoughnessSourceName / CorrespondanceFileName）。"""
    src = ""
    f = ""
    for k, v in _merged_lines():
        if k == "MTDSiteGeoServer_RoughnessSourceName":
            src = v
        elif k == "MTDSiteGeoServer_CorrespondanceFileName":
            f = v
    return {"source": src, "correspondance_file": f}


def roughness_source_to_correspondance(source):
    """粗糙度源 → 索引文件；source 可带/不带 "rough:" 前缀，返回文件名字符串或 None。"""
    s = str(source or "").strip()
    if not s:
        return None
    if not s.lower().startswith("rough:"):
        s = "rough:" + s
    for src, f in roughness_pairs():
        if src.strip().lower() == s.lower():
            return f
    return None


def correspondance_to_roughness_source(correspondance_file):
    """索引文件 → 粗糙度源（含 "rough:" 前缀）；返回源名或 None。"""
    v = str(correspondance_file or "").strip()
    if not v:
        return None
    low_v = v.lower()
    for src, f in roughness_pairs():
        if f.strip().lower() == low_v:
            return src
    return None


def rough_layers():
    """解析 GeoData_RoughLayers 可用图层清单，返回 [(source, code), ...]。"""
    for k, v in _merged_lines():
        if k == "GeoData_RoughLayers":
            layers = []
            for part in v.split(","):
                part = part.strip()
                if not part or "|" not in part:
                    continue
                src, code = part.split("|", 1)
                layers.append((src.strip(), code.strip()))
            return layers
    return []


def get_parameter(key):
    """按 key 取参数值（后出现者覆盖），不存在返回 None。"""
    val = None
    for k, v in _merged_lines():
        if k == key:
            val = v
    return val


def all_parameters():
    """全部参数 dict（后者覆盖；仅 PROD 键）。"""
    d = {}
    for k, v in _merged_lines():
        d[k] = v
    return d


def status():
    """诊断：配置来源与关键映射数量。"""
    return {
        "install_dir": install_dir(),
        "prod_found": bool(os.path.exists(_prod_path())),
        "custom_found": bool(os.path.exists(_custom_path())),
        "total_keys": len({k for k, _ in _prod_lines()}),
        "roughness_pairs": len(roughness_pairs()),
        "roughness_default": roughness_default(),
        "rough_layers": len(rough_layers()),
    }


def clear_cache():
    """清空解析缓存（配置更新后调用）。"""
    _prod_lines.cache_clear()
    _custom_lines.cache_clear()
    _clear_asset_cache()
