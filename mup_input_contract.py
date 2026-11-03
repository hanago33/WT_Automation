# encoding: utf-8
"""全链输入契约（P0）：板块绑定 manifest、地形 CRS 闸、曲线/配置/粗糙度槽位判定。

职责（纯函数，无 GUI 依赖，不修改任何链路文件）：
    1. chain_manifest.json 的装载与校验：板块 key → 流程文件 → 期望 flowPackages.id
       → 期望 ${runtime.*} 键集合。绑错文件时启动即 fail-closed，不再靠人手记忆。
    2. GeoTIFF CRS 闸：读取 GeoKey 3072（ProjectedCSTypeGeoKey），非 4531 拒绝启动，
       并给出 gdal_translate / 纯字节修复两条路径。
    3. 运行前输入契约检查：地形投影、风机功率/推力曲线、粗糙度索引、配置导入分支。

设计口径：
    - 只做「判定 + 说明」，不做自动修复，不写用户文件。
    - 校验失败返回中文问题列表（可直接展示/记日志），空列表 = 通过。
    - 资产缺失（MUP 安装目录不可探测）时不把「无法验证」当成「不通过」，而是明确
      标注为未知并放行——MUP 侧仍会在步骤级 fail-closed。
"""

import json
import os
import struct

MANIFEST_FILENAME = "chain_manifest.json"

# 目标投影：CGCS2000 / 3-degree Gauss-Kruger zone 43（样例项目现场 Observed）
EXPECTED_TERRAIN_EPSG = 4531
# GeoTIFF GeoKey 编号
_GEOKEY_DIRECTORY_TAG = 34735
_GEOKEY_PROJECTED_CS = 3072
_GEOKEY_GEOGRAPHIC_CS = 1024
_GEOKEY_PROJ_LINEAR_UNITS = 3076

_EPSG_FIX_HINT = (
    "可用 gdal_translate -a_srs EPSG:{epsg} 输入.tif 输出.tif 重新声明投影；"
    "或在 GeoTIFF 的 GeoKeyDirectory(34735) 中把 Key 3072 的值改为 {epsg}（纯字节修复，"
    "改前备份原文件）。"
)


# ── chain_manifest ────────────────────────────────────────────────────────


def manifest_path(base_dir):
    return os.path.join(str(base_dir or ""), MANIFEST_FILENAME)


def load_chain_manifest(base_dir):
    """读取 <base_dir>/chain_manifest.json。

    返回 (manifest, error)。manifest 为 dict（含 sections）或 None；error 为中文原因。
    """
    path = manifest_path(base_dir)
    if not os.path.isfile(path):
        return None, "未找到板块绑定清单：{}".format(path)
    try:
        with open(path, "r", encoding="utf-8") as fobj:
            payload = json.load(fobj)
    except (OSError, ValueError) as exc:
        return None, "板块绑定清单无法读取：{}（{}）".format(path, exc)
    if not isinstance(payload, dict):
        return None, "板块绑定清单根节点必须是对象：{}".format(path)
    sections = payload.get("sections")
    if not isinstance(sections, dict) or not sections:
        return None, "板块绑定清单缺少非空 sections：{}".format(path)
    return payload, ""


def section_spec(manifest, section_key):
    sections = (manifest or {}).get("sections") or {}
    spec = sections.get(str(section_key or "").strip())
    return spec if isinstance(spec, dict) else None


def section_flow_path(base_dir, manifest, section_key):
    """板块的默认流程文件绝对路径；未声明返回空串。"""
    spec = section_spec(manifest, section_key)
    if not spec:
        return ""
    rel = str(spec.get("flow") or "").strip()
    if not rel:
        return ""
    return rel if os.path.isabs(rel) else os.path.join(str(base_dir or ""), rel)


def section_fallback_flow_path(base_dir, manifest, section_key):
    spec = section_spec(manifest, section_key)
    if not spec:
        return ""
    rel = str(spec.get("fallbackFlow") or "").strip()
    if not rel:
        return ""
    return rel if os.path.isabs(rel) else os.path.join(str(base_dir or ""), rel)


def _read_flow_payload(flow_path):
    try:
        with open(flow_path, "r", encoding="utf-8") as fobj:
            payload = json.load(fobj)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _flow_package_ids(payload):
    ids = []
    for package in (payload or {}).get("flowPackages") or []:
        if isinstance(package, dict):
            pid = str(package.get("id") or "").strip()
            if pid:
                ids.append(pid)
    return ids


def _flow_strings(payload):
    """收集流程中所有字符串（含 controls/runtimeConfig），用于占位符消费检查。"""
    out = []

    def _walk(value):
        if isinstance(value, dict):
            for item in value.values():
                _walk(item)
        elif isinstance(value, list):
            for item in value:
                _walk(item)
        elif isinstance(value, str):
            out.append(value)

    _walk(payload)
    return out


def flow_consumes_runtime_key(payload, key):
    needle = "${runtime." + str(key or "").strip() + "}"
    return any(needle in text for text in _flow_strings(payload))


def validate_chain_manifest(manifest, base_dir, section_keys=None, bound_flows=None):
    """校验板块绑定契约，返回问题列表（空 = 通过）。

    section_keys : 期望的板块 key（通常来自 SIMPLE_SECTIONS）；None 表示取 manifest 全部。
    bound_flows  : {section_key: 实际绑定流程路径}（用户可能在界面上改绑），
                   提供时校验「实际绑定文件」而不是 manifest 默认值。
    """
    problems = []
    sections = (manifest or {}).get("sections") or {}
    if not isinstance(sections, dict) or not sections:
        return ["板块绑定清单缺少 sections。"]

    keys = list(section_keys) if section_keys else sorted(sections)
    known = set(sections)
    for key in keys:
        if key not in sections:
            problems.append("板块 {} 未在 chain_manifest.json 中声明绑定。".format(key))
    for key in sorted(known - set(keys)):
        problems.append("chain_manifest.json 声明了未知板块 {}（SIMPLE_SECTIONS 中不存在）。".format(key))

    bound_flows = bound_flows or {}
    for key in keys:
        spec = section_spec(manifest, key)
        if not spec:
            continue
        flow_path = str(bound_flows.get(key) or "").strip() or section_flow_path(base_dir, manifest, key)
        if not flow_path:
            problems.append("板块 {} 未配置流程文件。".format(key))
            continue
        if not os.path.isfile(flow_path):
            problems.append("板块 {} 绑定的流程文件不存在：{}".format(key, flow_path))
            continue
        payload = _read_flow_payload(flow_path)
        if payload is None:
            problems.append("板块 {} 绑定的流程文件不是合法 JSON：{}".format(key, flow_path))
            continue

        expected_id = str(spec.get("flowPackageId") or "").strip()
        if expected_id:
            actual_ids = _flow_package_ids(payload)
            if expected_id not in actual_ids:
                problems.append(
                    "板块 {} 绑定的文件不含 flowPackages.id={}（实际：{}）。文件：{}".format(
                        key,
                        expected_id,
                        "、".join(actual_ids) if actual_ids else "无",
                        flow_path,
                    )
                )

        for runtime_key in spec.get("requiresRuntime") or []:
            key_text = str(runtime_key or "").strip()
            if key_text and not flow_consumes_runtime_key(payload, key_text):
                problems.append(
                    "板块 {} 的流程未消费 ${{runtime.{}}}（模板与契约不一致）。文件：{}".format(
                        key, key_text, flow_path
                    )
                )
    return problems


def find_section_key_for_flow(manifest, base_dir, flow_path):
    """按流程路径反查板块 key（含 fallbackFlow）；未命中返回空串。"""
    target = os.path.normcase(os.path.abspath(str(flow_path or ""))) if flow_path else ""
    if not target:
        return ""
    sections = (manifest or {}).get("sections") or {}
    for key, spec in sections.items():
        candidates = []
        if isinstance(spec, dict):
            candidates.extend([spec.get("flow"), spec.get("fallbackFlow")])
        for rel in candidates:
            rel = str(rel or "").strip()
            if not rel:
                continue
            full = rel if os.path.isabs(rel) else os.path.join(str(base_dir or ""), rel)
            if os.path.normcase(os.path.abspath(full)) == target:
                return key
    return ""


# ── GeoTIFF CRS ──────────────────────────────────────────────────────────


def _read_ifd_tags(fobj, endian):
    """读取首个 IFD 的 tag 表：{tag: (type, count, value_or_offset)}。"""
    fobj.seek(4)
    raw = fobj.read(4)
    if len(raw) != 4:
        raise ValueError("IFD 偏移缺失")
    ifd_offset = struct.unpack(endian + "I", raw)[0]
    fobj.seek(ifd_offset)
    raw = fobj.read(2)
    if len(raw) != 2:
        raise ValueError("IFD 数量缺失")
    count = struct.unpack(endian + "H", raw)[0]
    tags = {}
    for _ in range(count):
        entry = fobj.read(12)
        if len(entry) != 12:
            raise ValueError("IFD 条目截断")
        tag, typ, cnt, value = struct.unpack(endian + "HHII", entry)
        tags[tag] = (typ, cnt, value)
    return tags


def _read_short_array(fobj, endian, entry):
    typ, count, value = entry
    if typ != 3:
        raise ValueError("GeoKeyDirectory 不是 SHORT 数组")
    size = int(count) * 2
    if size <= 4:
        data = struct.pack(endian + "I", value)[:size]
    else:
        fobj.seek(value)
        data = fobj.read(size)
    if len(data) != size:
        raise ValueError("GeoKeyDirectory 数据截断")
    return list(struct.unpack(endian + "{}H".format(count), data))


def read_geotiff_crs(path):
    """读取 GeoTIFF 投影标识。

    返回 dict：
        {"ok": bool, "epsg": int|None, "projected_epsg": int|None,
         "geographic_epsg": int|None, "linear_units": int|None,
         "error": str, "bigtiff": bool}
    仅支持经典 TIFF（magic 42）；BigTIFF（43）明确报不支持，不猜测。
    """
    info = {
        "ok": False,
        "epsg": None,
        "projected_epsg": None,
        "geographic_epsg": None,
        "linear_units": None,
        "error": "",
        "bigtiff": False,
    }
    path = str(path or "").strip()
    if not path or not os.path.isfile(path):
        info["error"] = "文件不存在"
        return info
    try:
        with open(path, "rb") as fobj:
            head = fobj.read(8)
            if len(head) < 8:
                info["error"] = "文件头不完整"
                return info
            if head[:2] == b"II":
                endian = "<"
            elif head[:2] == b"MM":
                endian = ">"
            else:
                info["error"] = "不是 TIFF 文件（字节序标记缺失）"
                return info
            magic = struct.unpack(endian + "H", head[2:4])[0]
            if magic == 43:
                info["bigtiff"] = True
                info["error"] = "BigTIFF（magic=43）暂不支持，请用 GDAL 转成经典 GeoTIFF"
                return info
            if magic != 42:
                info["error"] = "不是经典 TIFF（magic={}）".format(magic)
                return info
            tags = _read_ifd_tags(fobj, endian)
            entry = tags.get(_GEOKEY_DIRECTORY_TAG)
            if not entry:
                info["error"] = "缺少 GeoKeyDirectory(34735)：未声明投影"
                return info
            shorts = _read_short_array(fobj, endian, entry)
            if len(shorts) < 4:
                info["error"] = "GeoKeyDirectory 头不完整"
                return info
            key_count = int(shorts[3])
            keys = {}
            for index in range(key_count):
                base = 4 + index * 4
                if base + 3 >= len(shorts):
                    break
                key_id, location, count, offset = shorts[base:base + 4]
                keys[key_id] = (location, count, offset)

            def _key_value(key_id):
                got = keys.get(key_id)
                if not got:
                    return None
                location, count, offset = got
                if location != 0 or count < 1:
                    return None
                return int(offset)

            info["projected_epsg"] = _key_value(_GEOKEY_PROJECTED_CS)
            info["geographic_epsg"] = _key_value(_GEOKEY_GEOGRAPHIC_CS)
            info["linear_units"] = _key_value(_GEOKEY_PROJ_LINEAR_UNITS)
            info["epsg"] = info["projected_epsg"] or info["geographic_epsg"]
            info["ok"] = True
            return info
    except (OSError, ValueError, struct.error) as exc:
        info["error"] = str(exc)
        return info


def describe_terrain_crs(path, expected_epsg=EXPECTED_TERRAIN_EPSG):
    """地形 CRS 闸判定。

    返回 {"ok": bool, "epsg": int|None, "message": str}。
    """
    path = str(path or "").strip()
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".tif", ".tiff"):
        return {
            "ok": False,
            "epsg": None,
            "message": "地形文件不是 GeoTIFF（{}），无法校验投影；请改用带 EPSG:{} 的 .tif。".format(
                ext or "未知后缀", expected_epsg
            ),
        }
    crs = read_geotiff_crs(path)
    if not crs.get("ok"):
        return {
            "ok": False,
            "epsg": crs.get("epsg"),
            "message": "地形 CRS 闸未通过：{}（{}）。{}".format(
                crs.get("error") or "无法读取投影",
                os.path.basename(path),
                _EPSG_FIX_HINT.format(epsg=expected_epsg),
            ),
        }
    epsg = crs.get("epsg")
    if epsg == expected_epsg:
        return {"ok": True, "epsg": epsg, "message": "地形 CRS 通过：EPSG:{}".format(epsg)}
    return {
        "ok": False,
        "epsg": epsg,
        "message": "地形 CRS 闸未通过：{} 的 Key 3072={}（期望 EPSG:{}）。{}".format(
            os.path.basename(path),
            epsg if epsg is not None else "缺失",
            expected_epsg,
            _EPSG_FIX_HINT.format(epsg=expected_epsg),
        ),
    }


# ── 运行前输入契约 ───────────────────────────────────────────────────────

_REQUIRED_KEY_LABELS = {
    "sourceFilePath": "地形文件",
    "turbineImportFilePath": "风机功率曲线（.wtg）",
    "thrustCurveFilePath": "风机推力曲线（*.txt / *推力* / *ct*）",
    "mastImportFilePath": "测风塔时段数据（*-tim.txt）",
    "tiFilePath": "湍流强度数据（*-TI.txt）",
    "tisFilePath": "湍流强度标准差数据（*-TISD.txt）",
    "mastCoordFilePath": "测风塔坐标文件（CFT*.txt）",
    "turbineCoordFilePath": "机位点坐标文件（JWD*.txt）",
    "roughnessIndexFile": "粗糙度索引文件",
    "outputDir": "输出目录（04-WT输出）",
    "mastName": "测风塔编号",
}


def check_run_contract(section_key, spec, runtime_config, work_dir=""):
    """运行前检查某个板块的输入契约，返回问题列表。

    只检查 manifest 中显式声明的约束：
      - requiresRuntime：键必须有非空值；
      - terrainCrsGate：地形必须通过 EPSG 闸；
      - requireRoughness：粗糙度槽位必须已解析（项目文件或显式索引名）；
      - configImport：configSource=none 不算错误（调用方改用基础流程）。
    """
    problems = []
    rc = runtime_config if isinstance(runtime_config, dict) else {}
    spec = spec if isinstance(spec, dict) else {}

    for runtime_key in spec.get("requiresRuntime") or []:
        key = str(runtime_key or "").strip()
        if not key:
            continue
        if not str(rc.get(key) or "").strip():
            if key == "thrustCurveFilePath":
                problems.append(
                    "板块 {} 推力曲线缺失（${{runtime.thrustCurveFilePath}} 为空）："
                    "禁止用功率曲线 .wtg 顶替。".format(section_key)
                )
            else:
                problems.append(
                    "板块 {} 缺少输入：{}（${{runtime.{}}} 为空）。".format(
                        section_key, _REQUIRED_KEY_LABELS.get(key, key), key
                    )
                )

    if spec.get("terrainCrsGate"):
        terrain = str(rc.get("sourceFilePath") or "").strip()
        if terrain:
            verdict = describe_terrain_crs(terrain)
            if not verdict.get("ok"):
                problems.append("板块 {}：{}".format(section_key, verdict.get("message")))

    if spec.get("requireRoughness"):
        index_file = str(rc.get("roughnessIndexFile") or "").strip()
        if not index_file:
            problems.append(
                "板块 {} 缺少粗糙度槽位：未解析到粗糙度索引（项目文件、"
                "roughnessIndexFile 参数与内置默认均为空）。".format(section_key)
            )

    if spec.get("configImport"):
        source = str(rc.get("configSource") or "").strip().lower()
        if source == "file" and not str(rc.get("configFilePath") or "").strip():
            problems.append(
                "板块 {} config_source=file 但 configFilePath 为空（解析结果不自洽）。".format(section_key)
            )
        elif source not in ("file", "none"):
            problems.append(
                "板块 {} 的配置来源未判定（configSource={}）：无法确定是否导入配置。".format(
                    section_key, source or "空"
                )
            )

    if spec.get("terrainCrsGate") and rc.get("terrainAmbiguous"):
        candidates = rc.get("terrainCandidates") or []
        problems.append(
            "板块 {} 的地形文件不唯一（{} 份候选：{}），需在项目参数中显式指定 terrainFile，"
            "或保证只有一份通过 EPSG:{}。".format(
                section_key,
                len(candidates),
                "、".join(os.path.basename(str(p)) for p in candidates[:6]),
                EXPECTED_TERRAIN_EPSG,
            )
        )
    if spec.get("terrainCrsGate") and rc.get("terrainExplicitMissing"):
        problems.append(
            "板块 {} 显式指定的地形文件不在候选中：{}。".format(
                section_key, rc.get("terrainExplicitMissing")
            )
        )
    return problems


def roughness_asset_status(index_file, assets_module=None):
    """校验粗糙度索引名是否真实存在于 MUP 资产目录。

    返回 {"checked": bool, "found": bool, "options": list, "message": str}。
    资产目录不可探测时 checked=False（未知，不阻断）。
    """
    name = str(index_file or "").strip()
    out = {"checked": False, "found": False, "options": [], "message": ""}
    if not name:
        return out
    try:
        if assets_module is None:
            import mup_assets as assets_module
        status = assets_module.status()
    except Exception as exc:  # 资产探测失败不阻断，标注未知
        out["message"] = "MUP 资产目录不可探测（{}），跳过粗糙度索引校验".format(exc)
        return out
    if not status.get("found"):
        out["message"] = "未探测到 MUP 安装目录，跳过粗糙度索引校验"
        return out
    options = list(status.get("options") or [])
    out["checked"] = True
    out["options"] = options
    out["found"] = str(name).lower() in {str(o).lower() for o in options}
    if not out["found"]:
        out["message"] = "粗糙度索引 {} 不在 MUP 资产目录（可选：{}）".format(
            name, "、".join(options[:8]) or "无"
        )
    return out
