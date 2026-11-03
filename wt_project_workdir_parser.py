# encoding: utf-8
"""
市场项目工作文件夹解析器（Simple 模式自动解析键入值）

职责（纯函数、无 GUI 依赖）：
    读取「项目工作文件夹」（含 03-WT输入 / 04-WT输出），按命名规则解析出：
      - runtime_config    : 供注入 GM_RUNTIME_CONFIG_JSON（子进程 ${runtime.xxx} 展开）
      - text_overrides    : 步骤 actionConfig.text 的「精确文本」替换映射（写死值 → 解析值）
      - path_prefix_ovr   : 步骤文本的「路径前缀」替换映射（写死旧前缀 → 新前缀）

设计约束：
    - 未指定项目文件夹 / 目录缺失 / 文件不匹配时返回 None（由调用方保持原有行为）。
    - 不修改任何链路文件，只产出「覆盖映射」供运行期注入。
    - 项目计算参数（半径 / CFD 网格 / Cp 版本 / 测风对象 / 50年风速等）由人工确认，
      通过 project_params 传入（可从 UI 表单或 <项目>/project.params.json 读取）。

参考目录结构（C:\\Users\\14830\\Desktop\\202608_Test）：
    03-WT输入/
        01-测风塔及机位点坐标/  CFT_Project_CGCS2000 43.txt, JWD_Project_CGCS2000 43.txt
        02-地形图/              TEST1_Project_CGCS2000 43.tif
        03-测风塔数据/          C1831/  (1831-...-tim.txt, -TI.txt, -TISD.txt)
        04-功率曲线/            功率曲线.wtg, 功率曲线.txt
    04-WT输出/
        m1/ m4/ m10/  ...
"""

import json
import os
import re
import glob as _glob

# 03-WT输入 下按编号前缀 + 关键字划分的子目录（按关键字分类，不硬编码编号）
_TERRAIN_KEYWORDS = ("地形", "tif", "terrain")
_COORDS_KEYWORDS = ("坐标", "机位", "测风塔及", "coord")
_MASTDATA_KEYWORDS = ("测风塔数据", "气象", "mast")
_TURBINE_KEYWORDS = ("功率", "风机", "curve", "turbine")
_ROUGHNESS_KEYWORDS = ("粗糙度", "roughness", "rough", "地表粗糙")
# MUP 安装目录 Assets/CorrespondanceFiles 下的内置粗糙度索引（flow 原写死值）
DEFAULT_ROUGHNESS_INDEX_FILE = "ESA2020.txt"

_PROJ_ABBREV_RE = re.compile(r"(CGCS2000\s*\d{1,3}|UTM\s*\d{1,2}[A-Z]?|WGS84\s*\d{0,3})", re.IGNORECASE)
# 数字对：第一个数字前不能是字母/数字/下划线（避免把 C1831 里的 1831 当成坐标）；
# 两数之间允许半角/全角空格或半角/全角逗号分隔（用户可能用中文标点写坐标/参数行）
_COORD_PAIR_RE = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?[\s,，]++[+-]?\d+(?:\.\d+)?")
# 字段分隔：兼容半角/全角空格、半角/全角逗号，避免用户用中文标点把整行挤成无分隔
_FIELD_SEP_RE = re.compile(r"[\s,，;；]+")


def _is_terrain(name):
    return any(k in name for k in _TERRAIN_KEYWORDS)


def _is_coords(name):
    return any(k in name for k in _COORDS_KEYWORDS)


def _is_mastdata(name):
    return any(k in name for k in _MASTDATA_KEYWORDS)


def _is_turbine(name):
    return any(k in name for k in _TURBINE_KEYWORDS)


def _is_roughness(name):
    return any(k in name.lower() for k in _ROUGHNESS_KEYWORDS)


# ── 风机曲线槽位（A4：功率曲线 ≠ 推力曲线，禁止互相顶替）──
# 推力曲线：文件名含「推力」/thrust，或独立的 ct 词元（避免 project 里误命中 ct）。
_THRUST_CURVE_RE = re.compile(r"(^|[_\-\s.])ct([_\-\s.]|$)", re.IGNORECASE)
_POWER_CURVE_KEYWORDS = ("功率", "power", "cp")


def _is_thrust_curve(path):
    base = os.path.basename(str(path or "")).lower()
    return "推力" in base or "thrust" in base or bool(_THRUST_CURVE_RE.search(base))


def _is_power_curve(path):
    base = os.path.basename(str(path or "")).lower()
    if _is_thrust_curve(path):
        return False
    if base.endswith(".wtg"):
        return True
    return any(k in base for k in _POWER_CURVE_KEYWORDS)


def _scan_curve_files(input_root):
    """扫描 04-功率曲线 类目录下的曲线文件，返回 (功率曲线, 推力曲线)。"""
    files = _scan_files(input_root, (".wtg", ".txt", ".csv"))
    # 坐标/气象/配置/粗糙度文件不参与曲线分类
    files = [
        p for p in files
        if not any(k in p.lower() for k in ("-tim", "-ti.", "-tis", "配置信息"))
        and not _is_roughness(p)
        and not _is_mast_coord(p)
    ]
    power = [p for p in files if _is_power_curve(p)]
    thrust = [p for p in files if _is_thrust_curve(p)]
    return power, thrust


def _scan_files(root_dir, suffixes):
    """递归扫描 root_dir 下匹配后缀集合的文件，返回绝对路径列表（排序保证稳定）。"""
    hits = []
    if not root_dir or not os.path.isdir(root_dir):
        return hits
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for fname in filenames:
            if any(fname.lower().endswith(ext) for ext in suffixes):
                hits.append(os.path.join(dirpath, fname))
    return sorted(hits)


def _is_plausible_utm(x, y):
    """UTM 坐标合理性粗校验：X（含带号）通常 >=1e5，Y 通常 >=1e6；排除编号/行号等干扰项。"""
    try:
        x = float(x)
        y = float(y)
    except (TypeError, ValueError):
        return False
    return x >= 100000.0 and y >= 1000000.0


def _read_utm_pair(txt_path):
    """从坐标 txt 中读取首个「合理 UTM X/Y」数对。成功返回 (x, y)，失败返回 None。"""
    try:
        with open(txt_path, "r", encoding="utf-8", errors="ignore") as fobj:
            for raw_line in fobj:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                for m in _COORD_PAIR_RE.finditer(line):
                    parts = _FIELD_SEP_RE.split(m.group(0))
                    parts = [p for p in parts if p != ""]
                    if len(parts) >= 2:
                        try:
                            x = float(parts[0])
                            y = float(parts[1])
                        except ValueError:
                            continue
                        if _is_plausible_utm(x, y):
                            return (x, y)
    except OSError:
        return None
    return None


def _extract_projection_abbrev(fname):
    """从文件名提取投影缩写，如 'TEST1_Project_CGCS2000 43.tif' → 'CGCS2000 43'。"""
    base = os.path.splitext(fname)[0]
    m = _PROJ_ABBREV_RE.search(base)
    if m:
        return m.group(1)
    return ""


def _basename_without_ext(path):
    return os.path.splitext(os.path.basename(path))[0]


_MAST_TOKEN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,15}$")
# 坐标文件按前缀区分：CFT* = 测风塔坐标，JWD* = 机位点坐标（JWD 不属于测风塔对象编号）
_MAST_COORD_KEYWORDS = ("cft", "测风塔", "mast")
_TURBINE_COORD_KEYWORDS = ("jwd", "机位", "turbine")

# ── 新增：CFT信息.txt 单源（01-测风塔及机位点坐标/CFT信息.txt）──
# 格式：mastName lon lat elev hubHeight utmX utmY  （空格/制表分隔，utm 可为空）
_CFT_INFO_KEYWORDS = ("cft信息", "cft_info", "cftinfo")
_TIM_HEADER_RE_LAT = re.compile(r"Latitude\s*=\s*[Nn]\s*([+-]?\d+(?:\.\d+)?)")
_TIM_HEADER_RE_LON = re.compile(r"Longitude\s*=\s*[Ee]\s*([+-]?\d+(?:\.\d+)?)")
_TIM_HEADER_RE_ELEV = re.compile(r"Elevation\s*=\s*([+-]?\d+(?:\.\d+)?)")


def _find_cft_info_file(input_root):
    """定位 CFT信息.txt（01-测风塔及机位点坐标 下，文件名含 cft信息）。"""
    if not input_root or not os.path.isdir(input_root):
        return ""
    candidates = []
    for dirpath, _dirs, files in os.walk(input_root):
        for fname in files:
            low = fname.lower()
            if any(k in low for k in _CFT_INFO_KEYWORDS) and low.endswith(".txt"):
                candidates.append(os.path.join(dirpath, fname))
    if candidates:
        candidates.sort()
        return candidates[0]
    return ""


def _parse_cft_info_file(info_path):
    """
    解析 CFT信息.txt，返回 mastEntries 列表。
    每行：mastName lon lat elev hubHeight utmX utmY（utm 可为空）。
    忽略空行/#注释，容忍多空格/制表分隔。
    """
    entries = []
    if not info_path or not os.path.isfile(info_path):
        return entries
    try:
        with open(info_path, "r", encoding="utf-8", errors="ignore") as fobj:
            for raw in fobj:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                parts = _FIELD_SEP_RE.split(line)
                parts = [p.strip() for p in parts if p.strip() != ""]
                if len(parts) < 2:
                    continue
                mast = parts[0]
                if not _MAST_TOKEN_RE.match(mast):
                    continue
                # 7列：mast lon lat elev hub utmX utmY（后3列可缺）
                lon = parts[1] if len(parts) > 1 else ""
                lat = parts[2] if len(parts) > 2 else ""
                elev = parts[3] if len(parts) > 3 else ""
                hub = parts[4] if len(parts) > 4 else ""
                utmX = parts[5] if len(parts) > 5 else ""
                utmY = parts[6] if len(parts) > 6 else ""
                # 兼容部分用户把 lat/lon 顺序写反（通过数值范围粗判：lat 通常 15~55，lon 70~135）
                # 若 lon 看起来像 lat（<60 且 lat>70），则交换
                try:
                    lon_f = float(lon) if lon else None
                    lat_f = float(lat) if lat else None
                    if lon_f is not None and lat_f is not None:
                        if 15 <= lon_f <= 60 and 70 <= lat_f <= 135:
                            lon, lat = lat, lon
                except (TypeError, ValueError):
                    pass
                entries.append({
                    "mastName": mast,
                    "longitude": lon,
                    "latitude": lat,
                    "elevation": elev,
                    "hubHeight": hub,
                    "utmX": utmX,
                    "utmY": utmY,
                    "source": info_path,
                })
    except OSError:
        return entries
    return entries


def _parse_tim_header(tim_path):
    """从 tim 文件头解析 Latitude/Longitude/Elevation，返回 dict。"""
    out = {}
    if not tim_path or not os.path.isfile(tim_path):
        return out
    try:
        with open(tim_path, "r", encoding="utf-8", errors="ignore") as fobj:
            head = fobj.read(4096)
            m = _TIM_HEADER_RE_LAT.search(head)
            if m:
                out["latitude"] = m.group(1)
            m = _TIM_HEADER_RE_LON.search(head)
            if m:
                out["longitude"] = m.group(1)
            m = _TIM_HEADER_RE_ELEV.search(head)
            if m:
                out["elevation"] = m.group(1)
    except OSError:
        pass
    return out


def _parse_wtg_height(wtg_path):
    """从 wtg xml 解析 SuggestedHeights/Height，返回字符串高度。"""
    if not wtg_path or not os.path.isfile(wtg_path):
        return ""
    try:
        txt = open(wtg_path, "r", encoding="utf-8", errors="ignore").read()
        m = re.search(r"<Height>\s*([0-9]+(?:\.[0-9]+)?)\s*</Height>", txt)
        if m:
            return m.group(1)
    except OSError:
        pass
    return ""


# 功率曲线文件名：`WT6250D220_A.4（华润Ⅰ类-0.429）.wtg` → 机型 / 性能曲线版本
_TURBINE_FILENAME_RE = re.compile(
    r"^(?P<model>[^（(【\[]+?)\s*[（(【]\s*(?P<version>[^）)】]*?)\s*[）)】]"
)


def _parse_wtg_filename(wtg_path):
    """
    从 wtg 文件名解析机型与性能曲线版本。
        'WT6250D220_A.4（华润Ⅰ类-0.429）.wtg' → ('WT6250D220_A.4', '华润Ⅰ类-0.429')
    无括号格式则返回 (文件主体, '')。失败返回 ('', '')。
    """
    if not wtg_path:
        return ("", "")
    base = os.path.splitext(os.path.basename(wtg_path))[0]
    m = _TURBINE_FILENAME_RE.search(base)
    if not m:
        return (base.strip(), "")
    return (m.group("model").strip(), m.group("version").strip())


def _parse_wtg_meta(wtg_path):
    """
    从 wtg XML 解析机型元信息：description / manufacturer / rotorDiameter / hubHeight。
    返回 dict，失败返回空 dict。
    """
    out = {}
    if not wtg_path or not os.path.isfile(wtg_path):
        return out
    try:
        txt = open(wtg_path, "r", encoding="utf-8", errors="ignore").read(8192)
    except OSError:
        return out
    m = re.search(r'<WindTurbineGenerator[^>]*Description="([^"]*)"', txt)
    if m:
        out["description"] = m.group(1).strip()
    m = re.search(r'ManufacturerName="([^"]*)"', txt)
    if m:
        out["manufacturer"] = m.group(1).strip()
    m = re.search(r'RotorDiameter="([^"]*)"', txt)
    if m:
        out["rotorDiameter"] = m.group(1).strip()
    m = re.search(r"<Height>\s*([0-9]+(?:\.[0-9]+)?)\s*</Height>", txt)
    if m:
        out["hubHeight"] = m.group(1).strip()
    return out


_PATH_HEIGHT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:m|米|mtr|meter)", re.IGNORECASE)


def _path_measure_height(path):
    """从文件路径中提取「测量高度」（如 C1831_10m_tim.txt → 10.0）。
    未识别到明确高度返回 None。仅用于同一塔多高度文件间做低高度优先排序。"""
    if not path:
        return None
    found = [float(mm.group(1)) for mm in _PATH_HEIGHT_RE.finditer(os.path.basename(path))]
    if not found:
        return None
    return min(found)


def _pick_mast_file(all_files, mast_name, input_root):
    """
    从文件列表中挑出指定测风塔的数据文件。
    优先 <03-测风塔数据>/<mastName>/ 目录下，回退「父目录名 == mastName」匹配。

    选中高度：同一塔存在多个高度文件时，优先「最低测量高度」文件（通常 10m，惯例为
    参考风速列），排序稳定（同高度保留原先后序），避免选到哪个高度不确定。
    """
    if not mast_name:
        return ""
    mast_root = ""
    if input_root and os.path.isdir(input_root):
        for name in os.listdir(input_root):
            if _is_mastdata(name):
                mast_root = os.path.join(input_root, name)
                break

    candidates = []
    if mast_root and os.path.isdir(mast_root):
        cand_dir = os.path.join(mast_root, mast_name)
        if os.path.isdir(cand_dir):
            candidates = [
                p for p in all_files
                if p.startswith(cand_dir + os.sep) or os.path.dirname(p) == cand_dir
            ]
    if not candidates:
        candidates = [p for p in all_files if os.path.basename(os.path.dirname(p)) == mast_name]
    if not candidates:
        return ""
    # 仅当存在 ≥2 个高度候选时做低高度优先；否则维持原第一个（保持稳定）。
    if len(candidates) > 1:
        candidates.sort(key=lambda p: (_path_measure_height(p) is None, _path_measure_height(p) or 0.0, p))
    return candidates[0]


def _is_mast_coord(path):
    base = os.path.basename(path).lower()
    return any(k in base for k in _MAST_COORD_KEYWORDS)


def _collect_name_tokens(txt_path):
    """
    读 txt 中所有行的首列名称（测风塔/机位点编号，如 JWD1 / C1831），过滤表头。
    表头（如“风速(m/s)”、纯数字、含括号/斜杠等）不满足编号规则时自动剔除。
    """
    tokens = []
    try:
        with open(txt_path, "r", encoding="utf-8", errors="ignore") as fobj:
            for raw_line in fobj:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                tokens_cells = [p for p in _FIELD_SEP_RE.split(line) if p != ""]
                if not tokens_cells:
                    continue
                token = tokens_cells[0]
                if _MAST_TOKEN_RE.match(token):
                    tokens.append(token)
    except OSError:
        return tokens
    return tokens


def _collect_mast_ids(input_root, coord_txts, tim_files):
    """
    识别项目文件夹中的测风塔编号列表，去重排序。来源：
      1. 03-测风塔数据 下按测风塔编号建的子目录名（如 C1831）
      2. tim 测风塔主数据文件的父目录名
      3. 测风塔/机位点坐标 txt 各行首列名称（如 JWD1/JWD2/JWD3）
    返回列表（如 ['C1831', 'JWD1', 'JWD2', 'JWD3']）。
    """
    ids = set()
    if input_root and os.path.isdir(input_root):
        for name in os.listdir(input_root):
            full = os.path.join(input_root, name)
            if os.path.isdir(full) and _is_mastdata(name):
                for sub in os.listdir(full):
                    if os.path.isdir(os.path.join(full, sub)) and not sub.startswith("."):
                        # 只收"测风塔数据文件"对应的塔 id；目录名/容器名（如 测风塔数据 本身、
                        # TI/TISD 分目录）不满足编号规则的一律剔除，避免把目录名混进 mastIds
                        if _MAST_TOKEN_RE.match(sub):
                            ids.add(sub)
    for path in tim_files:
        parent = os.path.basename(os.path.dirname(path))
        # 父目录须为「合理塔 id」，而非测风塔数据容器目录自身名或关键字子目录。
        # 文件直接挂在 <03-测风塔数据>/ 下时 parent 会把容器名当塔 id 混入。
        if parent and _MAST_TOKEN_RE.match(parent):
            ids.add(parent)
    for path in coord_txts:
        # 只把「测风塔坐标文件」里的编号计入测风塔对象（CFT*）；JWD* 为机位点，不计入
        if not _is_mast_coord(path):
            continue
        for token in _collect_name_tokens(path):
            ids.add(token)
    return sorted(ids)


def _select_terrain_file(terrain_files, project_params):
    """从候选地形中确定「唯一」文件（A2：禁止多份 tif 静默取首）。

    规则（按优先级）：
      1. 项目参数显式指定 terrainFile（绝对路径或文件名）；
      2. 只有一份候选；
      3. 多份候选中恰好一份通过 EPSG:4531 CRS 闸；
    其余情况返回空路径 + ambiguous 标记，由上层 fail-closed。
    返回 (path, info)；info 含 selection / candidates / crs。
    """
    project_params = project_params if isinstance(project_params, dict) else {}
    unique = []
    seen = set()
    for path in terrain_files or []:
        key = os.path.normcase(os.path.abspath(str(path)))
        if key in seen:
            continue
        seen.add(key)
        unique.append(str(path))

    explicit = str(project_params.get("terrainFile") or "").strip()
    if explicit:
        target = os.path.normcase(os.path.abspath(explicit))
        for path in unique:
            if os.path.normcase(os.path.abspath(path)) == target:
                return path, {"selection": "explicit", "candidates": unique, "crs": None}
        # 允许只写文件名（换机器后盘符不同）
        by_name = [p for p in unique if os.path.basename(p).lower() == os.path.basename(explicit).lower()]
        if len(by_name) == 1:
            return by_name[0], {"selection": "explicit_name", "candidates": unique, "crs": None}
        return "", {"selection": "explicit_missing", "candidates": unique, "crs": None}

    if not unique:
        return "", {"selection": "none", "candidates": [], "crs": None}
    if len(unique) == 1:
        return unique[0], {"selection": "unique", "candidates": unique, "crs": None}

    passing = []
    crs_map = {}
    try:
        import mup_input_contract
    except Exception:
        mup_input_contract = None
    if mup_input_contract is not None:
        for path in unique:
            verdict = mup_input_contract.describe_terrain_crs(path)
            crs_map[path] = verdict
            if verdict.get("ok"):
                passing.append(path)
    if len(passing) == 1:
        return passing[0], {"selection": "crs_4531", "candidates": unique, "crs": crs_map}
    return "", {"selection": "ambiguous", "candidates": unique, "crs": crs_map}


def _find_mast_config_xml(input_root, mast_name):
    """定位 <塔名>配置信息.xml（A5：per-mast，禁止串用其它塔的配置）。

    优先父目录名 == 塔名；否则接受文件名以塔名开头的唯一匹配。
    返回 (path, candidates)。
    """
    mast = str(mast_name or "").strip()
    if not mast or not input_root or not os.path.isdir(input_root):
        return "", []
    target = mast.lower()
    candidates = []
    for dirpath, _dirs, files in os.walk(input_root):
        for fname in files:
            low = fname.lower()
            if not low.endswith(".xml"):
                continue
            if not any(k in low for k in ("配置信息", "config")):
                continue
            if low.startswith(target) or os.path.basename(dirpath).lower() == target:
                candidates.append(os.path.join(dirpath, fname))
    if not candidates:
        return "", []
    candidates.sort(key=lambda p: (
        os.path.basename(os.path.dirname(p)).lower() != target,
        len(os.path.basename(p)),
        p,
    ))
    return candidates[0], candidates


def _resolve_roughness_slot(input_root, project_params):
    """粗糙度槽位（P0）：项目文件 > 显式索引名 > MUP 内置默认索引。

    返回 dict：path / indexFile / source(project|explicit|builtin) / candidates。
    MUP 的粗糙度是「索引文件下拉」而非项目内导入文件：项目有时会给自定义索引，
    没有时退回内置 ESA2020.txt（存在性由运行前资产校验负责，缺失即 fail-closed）。
    """
    project_params = project_params if isinstance(project_params, dict) else {}
    candidates = []
    for path in _scan_files(input_root, (".txt", ".csv", ".map", ".rough")):
        if _is_roughness(path):
            candidates.append(path)
    explicit = str(project_params.get("roughnessIndexFile") or "").strip()
    if candidates:
        candidates.sort()
        return {
            "path": candidates[0],
            "indexFile": os.path.basename(candidates[0]),
            "source": "project",
            "candidates": candidates,
        }
    if explicit:
        return {"path": "", "indexFile": explicit, "source": "explicit", "candidates": []}
    return {
        "path": "",
        "indexFile": DEFAULT_ROUGHNESS_INDEX_FILE,
        "source": "builtin",
        "candidates": [],
    }


def _parse_work_dir(work_dir, project_params=None):
    """核心解析：输入项目文件夹 + 项目计算参数 → 覆盖映射。失败返回 None。"""
    work_dir = (work_dir or "").strip().rstrip("\\/")
    if not work_dir or not os.path.isdir(work_dir):
        return None

    project_name = os.path.basename(work_dir) or ""
    input_root = os.path.join(work_dir, "03-WT输入")
    output_root = os.path.join(work_dir, "04-WT输出")
    if not os.path.isdir(input_root):
        # 允许个别项目无输入目录，但输出目录必须有意义的处理见调用方；这里仅置标志
        pass

    project_params = project_params if isinstance(project_params, dict) else {}

    rc = {
        "projectWorkDir": work_dir,
        "projectName": project_name,
    }
    text_ovr = {}
    path_prefix_ovr = {}

    # ── 03-WT输入 文件分类扫描 ──
    terrain_files = _scan_files(input_root, (".tif", ".asc", ".tiff"))
    power_curve_files, thrust_curve_files = _scan_curve_files(input_root)
    wtg_files = power_curve_files
    tim_files = _scan_files(input_root, (".txt",))
    tim_files = [p for p in tim_files if "-tim" in p.lower() or "tim." in p.lower()]
    ti_files = [p for p in _scan_files(input_root, (".txt",)) if "-ti." in p.lower() or "ti." in p.lower()]
    tis_files = [p for p in _scan_files(input_root, (".txt",)) if "-tis" in p.lower() or "tis." in p.lower()]

    # 其余 txt（机位点 / 测风塔坐标 / 粗糙度 / 障碍物 / 网格）
    # 曲线/粗糙度文件不属于坐标槽，必须先剔除，避免被 _collect_name_tokens 当成机位编号。
    coord_txts = [
        p for p in _scan_files(input_root, (".txt",))
        if p not in tim_files + ti_files + tis_files
        and not _is_power_curve(p)
        and not _is_thrust_curve(p)
        and not _is_roughness(p)
    ]
    # 测风塔坐标文件（CFT*）与机位点坐标文件（JWD*）区分
    mast_coords = [p for p in coord_txts if _is_mast_coord(p)]
    turbine_coords = [p for p in coord_txts if not _is_mast_coord(p)]

    # ── 机位/测风塔坐标文件（元素导入槽，S4）──
    # 注意区分三个语义不同的键：
    #   · mastCoordFilePath / turbineCoordFilePath：真正的坐标文件（CFT*/JWD*.txt），
    #     供「导入并配置元素」的测风点/风机导入槽消费（S4 之前误用了 mastImportFilePath/turbineImportFilePath）。
    #   · mastImportFilePath（*-tim.txt）/ turbineImportFilePath（*.wtg）：时段数据/功率曲线，
    #     供「新建气象数据」「新建风机类型」板块消费，二者语义不同，不可互换。
    #   · turbinePosFilePath：历史遗留键（机位坐标），全仓无占位符消费，保留作向后兼容。
    if mast_coords:
        rc["mastCoordFilePath"] = mast_coords[0]
    if turbine_coords:
        rc["turbineCoordFilePath"] = turbine_coords[0]

    # ── 地形图 → sourceFilePath / 投影缩写 / 地形名 ──
    # A2：多份 tif 不再静默取首。唯一匹配（或恰好一份通过 EPSG:4531）才落 sourceFilePath；
    # 其余情况只记录候选与歧义标记，由运行前 CRS 闸 fail-closed。
    terrain_path, terrain_info = _select_terrain_file(terrain_files, project_params)
    if terrain_info.get("candidates"):
        rc["terrainCandidates"] = list(terrain_info["candidates"])
    if terrain_info.get("selection") == "ambiguous":
        rc["terrainAmbiguous"] = True
    if terrain_info.get("selection") == "explicit_missing":
        rc["terrainExplicitMissing"] = str(project_params.get("terrainFile") or "")
    if terrain_path:
        rc["sourceFilePath"] = terrain_path
        rc["projectionFilePath"] = terrain_path
        terrain_name = _basename_without_ext(terrain_path)
        rc["terrainName"] = terrain_name
        rc["terrainSelection"] = terrain_info.get("selection") or "unique"
        abbrev = _extract_projection_abbrev(terrain_path)
        if abbrev:
            rc["projectionAbbrev"] = abbrev
            # 板块1/5 中写死的投影带(如 50N) / 坐标系(如 UTM) 替换为缩写（作为下拉搜索词）
            text_ovr["50N"] = abbrev
            text_ovr["UTM"] = abbrev

    # ── 坐标文件 → UTM X/Y（优先测风塔坐标文件，取首个数对） ──
    utm_source = mast_coords or turbine_coords or coord_txts
    if utm_source:
        for coord_file in utm_source:
            pair = _read_utm_pair(coord_file)
            if pair:
                rc["utmX"] = str(pair[0])
                rc["utmY"] = str(pair[1])
                # 将流程中写死的坐标文本替换为解析值（精确匹配原写死值由调用方收集，
                # 这里仅当坐标 txt 文件名含投影缩写时补充投影）
                abbrev = _extract_projection_abbrev(coord_file)
                if abbrev and "projectionAbbrev" not in rc:
                    rc["projectionAbbrev"] = abbrev
                break
    # ── 机位点坐标文件（JWD*）→ 供板块3 机位点元素导入 ──
    if turbine_coords:
        rc["turbinePosFilePath"] = turbine_coords[0]

    # ── 空间计算域与边界自动推导（用于 导入并配置元素 与 创建一个新建模）──
    try:
        from wt_spatial_domain_calc import parse_coordinate_file as _sp_parse_file, calculate_spatial_domain as _sp_calc_domain
        _all_spatial_pts = []
        for _mf in (mast_coords or []):
            _all_spatial_pts.extend(_sp_parse_file(_mf, category="CFT"))
        for _tf in (turbine_coords or []):
            _all_spatial_pts.extend(_sp_parse_file(_tf, category="JWD"))
        if _all_spatial_pts:
            _sp_res = _sp_calc_domain(_all_spatial_pts, buffer_m=2500.0)
            rc["spatialDomain"] = _sp_res
            _sq = _sp_res["square_domain"]
            _cir = _sp_res["circles"]
            rc["domainCenterX"] = f"{_sq['center_x']:.3f}"
            rc["domainCenterY"] = f"{_sq['center_y']:.3f}"
            rc["domainRadiusR"] = str(int(round(_cir["inner_radius_R"])))
            rc["domainOuterRadius"] = f"{_cir['outer_radius_Router']:.1f}"
            rc["domainNwX"] = f"{_sq['nw_corner']['x']:.1f}"
            rc["domainNwY"] = f"{_sq['nw_corner']['y']:.1f}"
            rc["domainSeX"] = f"{_sq['se_corner']['x']:.1f}"
            rc["domainSeY"] = f"{_sq['se_corner']['y']:.1f}"
    except Exception:
        pass

    # ── CFT信息.txt 单源（01-测风塔及机位点坐标/CFT信息.txt）──
    cft_info_path = _find_cft_info_file(input_root)
    cft_entries = _parse_cft_info_file(cft_info_path)
    if cft_entries:
        rc["cftInfoPath"] = cft_info_path
        rc["mastEntries"] = cft_entries
        # 每塔气象对象名（塔名_轮毂高度_Auto，用户指定规则）：供新建气象数据命名与
        # 综合计算气象参考匹配，与测风塔元素名/其它气象数据变体区分。
        for _ent in cft_entries:
            if not isinstance(_ent, dict) or not _ent.get("mastName"):
                continue
            _mn = str(_ent.get("mastName", "")).strip()
            _hub = str(_ent.get("hubHeight", "") or "").strip()
            _ent["meteoName"] = f"{_mn}_{_hub}_Auto" if _hub else f"{_mn}_Auto"
        # 选中塔：优先 project_params.mastId，否则首行
        selected_mast = str(project_params.get("mastId", "")).strip()
        selected_entry = None
        if selected_mast:
            for ent in cft_entries:
                if ent.get("mastName") == selected_mast:
                    selected_entry = ent
                    break
        if selected_entry is None:
            selected_entry = cft_entries[0]
        # 写入选中塔的 meteo 键（优先级最高，覆盖后续回退）
        for k in ("mastName", "longitude", "latitude", "elevation", "hubHeight", "utmX", "utmY"):
            v = str(selected_entry.get(k, "")).strip()
            if v:
                rc[k] = v
                # 兼容部分流程写死用 50N/UTM 占位，这里不污染
        rc["selectedMast"] = selected_entry.get("mastName", "")
        if selected_entry.get("meteoName"):
            rc["meteoName"] = selected_entry["meteoName"]
        # 测风塔数据 → mast / TI / TISD（按选中塔优先）
        sel_mast_name = selected_entry.get("mastName", "")
        mast_tim = _pick_mast_file(tim_files, sel_mast_name, input_root)
        mast_ti = _pick_mast_file(ti_files, sel_mast_name, input_root)
        mast_tis = _pick_mast_file(tis_files, sel_mast_name, input_root)
        missing_files = []
        if mast_tim:
            rc["mastImportFilePath"] = mast_tim
        elif tim_files:
            rc["mastImportFilePath"] = tim_files[0]
            missing_files.append("tim")
        else:
            missing_files.append("tim")
        if mast_ti:
            rc["tiFilePath"] = mast_ti
        elif ti_files:
            rc["tiFilePath"] = ti_files[0]
            missing_files.append("TI")
        else:
            missing_files.append("TI")
        if mast_tis:
            rc["tisFilePath"] = mast_tis
        elif tis_files:
            rc["tisFilePath"] = tis_files[0]
            missing_files.append("TISD")
        else:
            missing_files.append("TISD")
        if missing_files:
            rc["missingMastFiles"] = missing_files
        # 回退：若 CFT信息.txt 某字段为空，则用 tim头/wtg/utm 补齐
        _need_lat = not str(rc.get("latitude", "")).strip()
        _need_lon = not str(rc.get("longitude", "")).strip()
        _need_elev = not str(rc.get("elevation", "")).strip()
        if _need_lat or _need_lon or _need_elev:
            hdr = _parse_tim_header(rc.get("mastImportFilePath", ""))
            for k in ("latitude", "longitude", "elevation"):
                if not str(rc.get(k, "")).strip() and hdr.get(k):
                    rc[k] = hdr[k]
        if not str(rc.get("hubHeight", "")).strip():
            # 优先 CFT_Project 坐标文件中的轮毂高度列，再 wtg。
            # 兼容「编号 X Y 高度」与「编号 lon lat elev hub」两种行格式：先取第5列，其次第4列，
            # 仅接受合理轮毂高度值（30~400 m），避免把海拔等误当轮毂高度。
            def _looks_like_hub(value):
                try:
                    return 30.0 <= float(value) <= 400.0
                except (TypeError, ValueError):
                    return False

            _hub = ""
            for p in mast_coords:
                try:
                    line = open(p, "r", encoding="utf-8", errors="ignore").readline()
                    parts = line.strip().split()
                    for cand in (parts[4] if len(parts) > 4 else "", parts[3] if len(parts) > 3 else ""):
                        if _looks_like_hub(cand):
                            _hub = cand
                            break
                    if _hub:
                        break
                except OSError:
                    continue
            if not _hub and wtg_files:
                _hub = _parse_wtg_height(wtg_files[0])
            if _hub:
                rc["hubHeight"] = _hub
        # utmX/Y 若仍空，保留前面坐标文件解析的 utmX/Y
    else:
        # 无 CFT信息.txt，回退旧逻辑
        if tim_files:
            rc["mastImportFilePath"] = tim_files[0]
        if ti_files:
            rc["tiFilePath"] = ti_files[0]
        if tis_files:
            rc["tisFilePath"] = tis_files[0]
        # 回退：无 CFT信息时也尝试从 tim头补 lat/lon/elev
        hdr = _parse_tim_header(rc.get("mastImportFilePath", ""))
        for k in ("latitude", "longitude", "elevation"):
            if not str(rc.get(k, "")).strip() and hdr.get(k):
                rc[k] = hdr[k]
        if not str(rc.get("hubHeight", "")).strip() and wtg_files:
            _hub = _parse_wtg_height(wtg_files[0])
            if _hub:
                rc["hubHeight"] = _hub
    # ── 测风塔编号列表（供「测风塔对象编号」下拉选项）──
    mast_ids = _collect_mast_ids(input_root, coord_txts, tim_files)
    # 若有 CFT信息.txt，则 mastIds 以其为准（更权威）
    if cft_entries:
        cft_ids = [e.get("mastName") for e in cft_entries if e.get("mastName")]
        if cft_ids:
            mast_ids = sorted(set(cft_ids + mast_ids))
    if mast_ids:
        rc["mastIds"] = mast_ids
    rc["mastIdsJoined"] = ",".join(mast_ids) if mast_ids else ""

    # ── 功率曲线 → turbineImportFilePath / 机型 / 性能曲线版本 ──
    # A4：功率曲线与推力曲线是两套槽位，禁止用 .wtg 顶替推力曲线。
    if wtg_files:
        wtg_path = wtg_files[0]
        rc["turbineImportFilePath"] = wtg_path
        model, version = _parse_wtg_filename(wtg_path)
        if model:
            rc["turbineType"] = model
        if version:
            rc["cpVersion"] = version
        meta = _parse_wtg_meta(wtg_path)
        for k in ("description", "manufacturer", "rotorDiameter"):
            if meta.get(k):
                rc.setdefault(k, meta[k])
        # 机型未从文件名解析出时，回退 XML Description 前缀（如 'WT6250D220 (6250 kW)' → 'WT6250D220'）
        if not model and meta.get("description"):
            rc["turbineType"] = meta["description"].split(" ")[0]
        if not str(rc.get("hubHeight", "")).strip() and meta.get("hubHeight"):
            rc["hubHeight"] = meta["hubHeight"]
    if thrust_curve_files:
        rc["thrustCurveFilePath"] = thrust_curve_files[0]
        rc["thrustCurveCandidates"] = list(thrust_curve_files)
    missing_turbine = []
    if not wtg_files:
        missing_turbine.append("power")
    if not thrust_curve_files:
        missing_turbine.append("thrust")
    if missing_turbine:
        rc["missingTurbineFiles"] = missing_turbine
    # ── 机位名单 turbineIds / joined ──
    # turbine_coords 为文件路径列表；此前写 r[0] 取的是路径首字符（如 C:\... 的 "C"），
    # 导致 turbineIds=["C"] 错数据。改为读取机位点坐标文件各行的首列编号（JWD1/J2…），
    # 与 mastIds 的取法同源，过滤规则一致。
    turbine_ids: list[str] = []
    if turbine_coords:
        for _tc_path in turbine_coords:
            turbine_ids.extend(_collect_name_tokens(_tc_path))
        turbine_ids = [str(t).strip() for t in turbine_ids if str(t).strip()]
        if turbine_ids:
            rc["turbineIds"] = sorted(set(turbine_ids))
            rc["turbineIdsJoined"] = ",".join(rc["turbineIds"])
    if "turbineIdsJoined" not in rc:
        rc["turbineIdsJoined"] = ""

    # ── 04-WT输出 → 导出目录 / 输出分目录(m1/m4/m10) ──
    if os.path.isdir(output_root):
        rc["outputDir"] = output_root
        mast_dirs = sorted(
            [d for d in os.listdir(output_root)
             if os.path.isdir(os.path.join(output_root, d)) and re.match(r"^m\d+$", d, re.IGNORECASE)]
        )
        if mast_dirs:
            rc["outputMasts"] = mast_dirs
            # 输出根前缀替换（旧根 → 新项目根）由 parse_project_work_dir 依据流程文件收集，这里不处理

    # 无 CFT信息时 mastName 回退（取首个 mastId）
    if not str(rc.get("mastName", "")).strip():
        if mast_ids:
            rc["mastName"] = mast_ids[0]
        elif str(project_params.get("mastId", "")).strip():
            rc["mastName"] = str(project_params.get("mastId", "")).strip()
    # mastName 与 mastId 互为别名，保持一致
    if str(rc.get("mastName", "")).strip() and not str(rc.get("mastId", "")).strip():
        rc["mastId"] = rc["mastName"]
    if str(rc.get("mastId", "")).strip() and not str(rc.get("mastName", "")).strip():
        rc["mastName"] = rc["mastId"]
    # 气象对象名兜底：选中塔 mastName + 轮毂高度 → 塔名_轮毂高度_Auto
    if not str(rc.get("meteoName", "")).strip() and str(rc.get("mastName", "")).strip():
        _mn = str(rc.get("mastName", "")).strip()
        _hub = str(rc.get("hubHeight", "") or "").strip()
        rc["meteoName"] = f"{_mn}_{_hub}_Auto" if _hub else f"{_mn}_Auto"

    # ── 配置导入槽位（A5）：<塔名>配置信息.xml 按塔解析，不跨塔串用 ──
    # 找不到时显式标记 configSource=none，由调用方改走「不导入配置」的基础气象流程；
    # 绝不回退到流程里写死的其它塔配置（C1831）路径。
    if str(rc.get("mastName", "")).strip():
        config_path, config_candidates = _find_mast_config_xml(input_root, rc.get("mastName", ""))
        if config_candidates:
            rc["configCandidates"] = config_candidates
        if config_path:
            rc["configFilePath"] = config_path
            rc["configSource"] = "file"
        else:
            rc["configSource"] = "none"

    # ── 粗糙度槽位（P0）：项目文件 > 显式索引名 > 缺失（缺失即 fail-closed）──
    roughness = _resolve_roughness_slot(input_root, project_params)
    rc["roughnessSource"] = roughness.get("source") or "none"
    if roughness.get("path"):
        rc["roughnessFilePath"] = roughness["path"]
    if roughness.get("indexFile"):
        rc["roughnessIndexFile"] = roughness["indexFile"]
    if roughness.get("candidates"):
        rc["roughnessCandidates"] = list(roughness["candidates"])

    # ── 项目计算参数（人工确认项）透传进 runtime_config ──
    calc_keys = ("radius", "cfdHRes", "cfdBuf", "cfdMax", "cfdMin",
                 "cpVersion", "mastId", "wind50", "elevation", "airDensity",
                 "turbineType", "mastName", "latitude", "longitude", "hubHeight",
                 "utmX", "utmY", "mastImportFilePath", "tiFilePath", "tisFilePath",
                 "domainCenterX", "domainCenterY", "domainNwX", "domainNwY",
                 "domainSeX", "domainSeY", "domainOuterRadius")
    for key in calc_keys:
        value = str(project_params.get(key, "")).strip()
        if value:
            rc[key] = value
            # 若调用方通过 text_ovr 的占位符键提供替换，也合并（见 build_text_overrides）
            ph_key = "${runtime." + key + "}"
            if ph_key in text_ovr:
                text_ovr[ph_key] = value

    # mastId 与 mastName 互为别名：以解析出的选中塔为准，
    # 避免用户传入的 mastId 不在 CFT 列表时与解析塔不一致。
    _rc_mast_name = str(rc.get("mastName", "")).strip()
    _rc_mast_id = str(rc.get("mastId", "")).strip()
    if _rc_mast_name:
        rc["mastId"] = _rc_mast_name
    elif _rc_mast_id:
        rc["mastName"] = _rc_mast_id

    if not any(v for v in (terrain_files, tim_files, wtg_files, coord_txts)) and not project_params:
        # 既无输入文件也无人工参数时，视为未解析
        return None

    return {
        "runtime_config": rc,
        "text_overrides": text_ovr,
        "path_prefix_overrides": path_prefix_ovr,
    }


def parse_project_work_dir(work_dir, project_params=None, flow_path=None):
    """
    对外入口：解析项目文件夹，产出运行期覆盖映射。

    参数：
        work_dir        : 项目工作文件夹绝对路径
        project_params  : 项目计算参数字典（半径/CFD网格/Cp版本/测风对象/50年风速等，人工确认）
        flow_path       : 可选。当前板块流程文件路径，用于收集「写死的旧文本」构造 text_overrides。

    返回 dict 或 None（未指定/目录无效）。
    """
    result = _parse_work_dir(work_dir, project_params)
    if result is None:
        return None
    if flow_path:
        result["text_overrides"] = build_text_overrides(
            flow_path, result["runtime_config"], result["text_overrides"]
        )
        # 收集流程中写死的旧输出根前缀（如 ...\\测试项目数据\\04-WT输出），替换为新项目根
        old_prefixes = _collect_old_output_prefixes(flow_path)
        if old_prefixes:
            path_ovr = result.get("path_prefix_overrides") or {}
            for old_pre in old_prefixes:
                if old_pre and old_pre not in path_ovr:
                    path_ovr[old_pre] = work_dir
            result["path_prefix_overrides"] = path_ovr
    return result


def list_mast_entries(work_dir):
    """返回 CFT信息.txt 的全部 mastEntries 列表（空列表表示无）。"""
    work_dir = (work_dir or "").strip().rstrip("\\/")
    if not work_dir or not os.path.isdir(work_dir):
        return []
    input_root = os.path.join(work_dir, "03-WT输入")
    cft_path = _find_cft_info_file(input_root)
    return _parse_cft_info_file(cft_path)


def summarize_project_work_dir(work_dir, project_params=None):
    """
    汇总「解析参数」展示数据（供 Simple 模式「项目计算参数（人工确认）」对话框的
    「解析参数」标签页查看；纯数据，无 GUI 依赖）。

    返回结构化的 dict：
        {
            "project_name", "work_dir", "projection_abbrev",
            "utm_x", "utm_y",
            "terrain_file", "terrain_name",
            "turbine_pos_file", "turbines": [{"name": ...}],
            "mast_tim", "mast_ti", "mast_tis",
            "turbine_curve",
            "output_dir", "output_masts",
            "mast_ids", "masts"(CFT信息.txt 条目列表), "cft_info_file",
        }
    目录无效 / 未解析到任何内容时返回 None。
    """
    result = _parse_work_dir(work_dir, project_params)
    if result is None:
        return None
    rc = result.get("runtime_config") or {}
    summary = {
        "project_name": rc.get("projectName", ""),
        "work_dir": rc.get("projectWorkDir", ""),
        "projection_abbrev": rc.get("projectionAbbrev", ""),
        "utm_x": rc.get("utmX", ""),
        "utm_y": rc.get("utmY", ""),
        "terrain_file": rc.get("sourceFilePath", ""),
        "terrain_name": rc.get("terrainName", ""),
        "terrain_selection": rc.get("terrainSelection", ""),
        "terrain_ambiguous": bool(rc.get("terrainAmbiguous")),
        "terrain_candidates": list(rc.get("terrainCandidates") or []),
        "turbine_pos_file": rc.get("turbinePosFilePath", ""),
        "mast_tim": rc.get("mastImportFilePath", ""),
        "mast_ti": rc.get("tiFilePath", ""),
        "mast_tis": rc.get("tisFilePath", ""),
        "turbine_curve": rc.get("turbineImportFilePath", ""),
        "thrust_curve": rc.get("thrustCurveFilePath", ""),
        "missing_turbine_files": list(rc.get("missingTurbineFiles") or []),
        "config_file": rc.get("configFilePath", ""),
        "config_source": rc.get("configSource", ""),
        "roughness_file": rc.get("roughnessFilePath", ""),
        "roughness_index": rc.get("roughnessIndexFile", ""),
        "roughness_source": rc.get("roughnessSource", ""),
        # 风机类型 / 性能曲线版本（从 04-功率曲线 文件名与 wtg 元信息解析）
        "turbine_type": rc.get("turbineType", ""),
        "cp_version": rc.get("cpVersion", ""),
        "rotor_diameter": rc.get("rotorDiameter", ""),
        "turbine_manufacturer": rc.get("manufacturer", ""),
        "turbine_description": rc.get("description", ""),
        "output_dir": rc.get("outputDir", ""),
        "output_masts": list(rc.get("outputMasts") or []),
        "mast_ids": list(rc.get("mastIds") or []),
        "masts": list(rc.get("mastEntries") or []),
        "cft_info_file": rc.get("cftInfoPath", ""),
        # 空间计算域与双圆
        "spatial_domain": rc.get("spatialDomain"),
        "domain_center_x": rc.get("domainCenterX", ""),
        "domain_center_y": rc.get("domainCenterY", ""),
        "domain_radius_r": rc.get("domainRadiusR", ""),
        "domain_outer_radius": rc.get("domainOuterRadius", ""),
        "domain_nw_x": rc.get("domainNwX", ""),
        "domain_nw_y": rc.get("domainNwY", ""),
        "domain_se_x": rc.get("domainSeX", ""),
        "domain_se_y": rc.get("domainSeY", ""),
    }
    # 机位点编号：从机位点坐标文件首列读取（编号规则已由 _collect_name_tokens 过滤）
    turbines = []
    turbine_path = summary["turbine_pos_file"]
    if turbine_path:
        for token in _collect_name_tokens(turbine_path):
            turbines.append({"name": token})
    summary["turbines"] = turbines
    # 按测风塔分组的气象数据文件（tim / TI / TISD）
    summary["mast_data"] = _collect_mast_data_files(work_dir, summary)
    return summary


def _collect_mast_data_files(work_dir, summary):
    """
    按测风塔编号组织各塔的气象数据文件（tim/TI/TISD），供「解析参数」页展示全部测风塔。
    返回 [{"mastName", "tim", "ti", "tis"}, ...]；无测风塔时返回 []。
    """
    mast_names = [str(e.get("mastName", "")).strip() for e in summary.get("masts") or []]
    mast_names = [n for n in mast_names if n]
    if not mast_names:
        mast_names = [str(n) for n in summary.get("mast_ids") or []]
    if not mast_names:
        return []
    input_root = os.path.join(summary.get("work_dir", ""), "03-WT输入")
    if not os.path.isdir(input_root):
        return []
    all_txt = _scan_files(input_root, (".txt",))
    tim_files = [p for p in all_txt if "-tim" in p.lower() or "tim." in p.lower()]
    ti_files = [p for p in all_txt if "-ti." in p.lower() or "ti." in p.lower()]
    tis_files = [p for p in all_txt if "-tis" in p.lower() or "tis." in p.lower()]
    out = []
    for mast_name in mast_names:
        out.append({
            "mastName": mast_name,
            "tim": _pick_mast_file(tim_files, mast_name, input_root),
            "ti": _pick_mast_file(ti_files, mast_name, input_root),
            "tis": _pick_mast_file(tis_files, mast_name, input_root),
        })
    return out


def diagnose_project_work_dir(work_dir):
    """
    诊断项目文件夹结构，返回可读信息（供 UI 提示「未解析到项目参数」的具体原因）。

    返回 dict：
        {
            "work_dir": str,
            "exists": bool,
            "input_root_exists": bool, "output_root_exists": bool,
            "file_counts": {"tif": int, "wtg": int, "txt": int},
            "hints": [str, ...],
        }
    """
    work_dir = (work_dir or "").strip().rstrip("\\/")
    diag = {
        "work_dir": work_dir,
        "exists": bool(work_dir) and os.path.isdir(work_dir),
        "input_root_exists": False,
        "output_root_exists": False,
        "file_counts": {},
        "hints": [],
    }
    if not work_dir:
        diag["hints"].append("未指定项目文件夹")
        return diag
    if not diag["exists"]:
        diag["hints"].append("目录不存在或无法访问：{}".format(work_dir))
        return diag
    input_root = os.path.join(work_dir, "03-WT输入")
    output_root = os.path.join(work_dir, "04-WT输出")
    diag["input_root_exists"] = os.path.isdir(input_root)
    diag["output_root_exists"] = os.path.isdir(output_root)
    if not diag["input_root_exists"]:
        diag["hints"].append("缺少 03-WT输入 目录（{0}）".format(input_root))
    if not diag["output_root_exists"]:
        diag["hints"].append("缺少 04-WT输出 目录（{0}）".format(output_root))
    if diag["input_root_exists"]:
        for ext, key in ((".tif", "tif"), (".asc", "asc"), (".wtg", "wtg"), (".txt", "txt")):
            diag["file_counts"][key] = len(_scan_files(input_root, (ext,)))
    if diag["input_root_exists"] and not any(diag["file_counts"].values()):
        diag["hints"].append("03-WT输入 下未扫描到 .tif / .asc / .wtg / .txt 文件")
    if diag["input_root_exists"] and diag["file_counts"].get("txt"):
        # 细化：是否识别到 tim / 坐标 / 功率曲线等关键文件
        tim_cnt = len([p for p in _scan_files(input_root, (".txt",)) if "-tim" in p.lower() or "tim." in p.lower()])
        if not tim_cnt:
            diag["hints"].append("未识别到测风塔主数据文件（文件名应含 -tim，如 1831-...-tim.txt）")
    return diag


def parse_all_masts(work_dir, project_params=None, flow_path=None):
    """
    多塔展开：对 CFT信息.txt 每一行产出一套覆盖映射。
    返回 list[dict]，每项同 parse_project_work_dir 返回结构，额外含 mastName。
    若无 CFT信息.txt，则返回单元素列表（原单塔逻辑）。
    """
    work_dir = (work_dir or "").strip().rstrip("\\/")
    entries = list_mast_entries(work_dir)
    if not entries:
        single = parse_project_work_dir(work_dir, project_params, flow_path)
        return [single] if single else []
    out = []
    base_params = dict(project_params or {})
    for ent in entries:
        mast = ent.get("mastName", "")
        if not mast:
            continue
        params = dict(base_params)
        params["mastId"] = mast
        # 同步 meteo 字段到 params，确保 _parse_work_dir 选中该塔
        for k in ("latitude", "longitude", "elevation", "hubHeight", "utmX", "utmY"):
            if ent.get(k):
                params[k] = ent[k]
        parsed = parse_project_work_dir(work_dir, params, flow_path)
        if parsed:
            out.append(parsed)
    return out


def _collect_old_output_prefixes(flow_path):
    """
    从流程文件步骤文本中收集「04-WT输出」所在的旧项目根前缀，用于导出路径前缀替换。
    返回前缀列表（如 ['C:\\\\Users\\\\DELL\\\\Desktop\\\\测试项目数据']）。
    """
    if not flow_path or not os.path.isfile(flow_path):
        return []
    try:
        with open(flow_path, "r", encoding="utf-8") as fobj:
            payload = json.load(fobj)
    except (OSError, ValueError):
        return []
    prefixes = set()

    def _collect(value):
        if isinstance(value, str) and "04-WT输出" in value:
            idx = value.find("04-WT输出")
            pre = value[:idx].rstrip("\\/")
            if pre:
                prefixes.add(pre)

    runtime_cfg = payload.get("runtimeConfig")
    if isinstance(runtime_cfg, dict):
        for value in runtime_cfg.values():
            _collect(value)
    steps = payload.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if not isinstance(step, dict):
                continue
            action_config = step.get("actionConfig")
            if isinstance(action_config, dict):
                for key, value in action_config.items():
                    _collect(value)
    return sorted(prefixes)


def build_text_overrides(flow_path, runtime_config, base_overrides=None):
    """
    从流程文件收集「写死的旧文本」，与解析出的新值配对，构造精确文本替换映射。

    匹配的旧文本来源：
      - runtimeConfig.sourceFilePath / outputDir 中携带的项目前缀
      - 步骤 actionConfig.text 中与 runtimeConfig 旧值同名的文件名主体
      - 坐标类步骤的数值文本（调用方难以精确识别时，此处仅做文件名主体替换）
    """
    overrides = dict(base_overrides or {})
    # 受审计的「必需命中」键：语义明确的写死值替换（气象/空间/气象数据文件路径）。
    # 条件性替换（投影缩写 50N/UTM、文件名主体、输出前缀）不在此列——它们本就带
    # 不确定语义（可能该板块不含该文本），0 命中不算静默假成功。
    required_keys = set()

    if not flow_path or not os.path.isfile(flow_path):
        return _attach_override_audit(flow_path, overrides, required_keys)

    try:
        with open(flow_path, "r", encoding="utf-8") as fobj:
            payload = json.load(fobj)
    except (OSError, ValueError):
        return _attach_override_audit(flow_path, overrides, required_keys)
    if not isinstance(payload, dict):
        return _attach_override_audit(flow_path, overrides, required_keys)

    old_runtime = payload.get("runtimeConfig") or {}
    new_runtime = runtime_config or {}

    def _same_ext(a, b):
        return os.path.splitext(str(a))[1].lower() == os.path.splitext(str(b))[1].lower()

    # 收集旧 actionConfig.text
    _old_texts = set()
    _old_text_list = []
    for _step in (payload.get("steps") or []):
        if not isinstance(_step, dict):
            continue
        _ac = _step.get("actionConfig")
        if isinstance(_ac, dict):
            _t = _ac.get("text")
            if isinstance(_t, str) and _t.strip():
                _old_texts.add(_t.strip())
                _old_text_list.append(_t.strip())

    # ── A6 槽位化覆盖映射（总纲 §4-A6）：按槽位而不是 runtime 键配对 ──
    # 核心原则：
    #   1. mastCoordFilePath 只与坐标槽配对；
    #   2. mastImportFilePath 只与时序槽配对；
    #   3. 同一旧文本命中多个槽位时拒绝启动（fail-closed），不再静默选一个；
    #   4. 同时含坐标与时序字面量的流程，映射不得交叉。
    _SLOT_MAST_COORD = "mastCoord"
    _SLOT_MAST_TIM = "mastTim"
    _SLOT_TURBINE_COORD = "turbineCoord"
    _SLOT_TURBINE_CURVE = "turbineCurve"
    _SLOT_TI = "ti"
    _SLOT_TIS = "tis"
    _SLOT_TERRAIN = "terrain"
    _SLOT_OUTPUT_DIR = "outputDir"

    _slot_targets_full = {
        _SLOT_MAST_COORD: str(new_runtime.get("mastCoordFilePath", "")).strip(),
        _SLOT_MAST_TIM: str(new_runtime.get("mastImportFilePath", "")).strip(),
        _SLOT_TURBINE_COORD: str(new_runtime.get("turbineCoordFilePath", "")).strip(),
        _SLOT_TURBINE_CURVE: str(new_runtime.get("turbineImportFilePath", "")).strip(),
        _SLOT_TI: str(new_runtime.get("tiFilePath", "")).strip(),
        _SLOT_TIS: str(new_runtime.get("tisFilePath", "")).strip(),
        _SLOT_TERRAIN: str(new_runtime.get("sourceFilePath", "")).strip(),
        _SLOT_OUTPUT_DIR: str(new_runtime.get("outputDir", "")).strip(),
    }
    _slot_targets_base = {
        s: (_basename_without_ext(p) if s != _SLOT_OUTPUT_DIR else p)
        for s, p in _slot_targets_full.items()
    }

    def _match_slots(cand):
        """按槽位语义（非 runtime 键名）判断 candidate 命中哪些槽位。返回匹配槽位集合。"""
        cand_str = str(cand).strip()
        if not cand_str or cand_str.startswith("${"):
            return set()

        low = cand_str.lower()
        base = os.path.basename(cand_str).lower()
        ext = os.path.splitext(cand_str)[1].lower()

        # 排除已由数值/气象名称作用域映射处理的非文件字面量
        if low in ("cft01", "cft1", "99", "40.5", "120.5", "125", "35.0", "1.220", "5000",
                   "43557986.525", "5136125.583", "43546603.1", "5151337.3", "43559303.1", "5138637.3"):
            return set()

        matched = set()

        is_mast_coord_kw = (
            ("cft" in base and low not in ("cft01", "cft1"))
            or "测风塔" in base
            or ("coord" in base and "jwd" not in base and "turbine" not in base)
        )
        is_turb_coord_kw = (
            "jwd" in base
            or "机位" in base
            or ("coord" in base and ("jwd" in base or "turbine" in base))
        )
        is_mast_tim_kw = ("tim" in base and "tis" not in base)

        # 1. 输出目录槽 (outputDir)
        old_out = str(old_runtime.get("outputDir", "")).strip()
        if old_out and cand_str == old_out:
            matched.add(_SLOT_OUTPUT_DIR)

        # 2. 地形槽 (tif / asc / dem / sourceFilePath)
        old_src = str(old_runtime.get("sourceFilePath", "")).strip()
        if ext in (".tif", ".tiff", ".asc", ".dem"):
            matched.add(_SLOT_TERRAIN)
        elif old_src and (cand_str == old_src or cand_str == _basename_without_ext(old_src)):
            if ext in ("", ".tif", ".tiff", ".asc", ".dem"):
                matched.add(_SLOT_TERRAIN)

        # 3. 风机/功率曲线槽 (.wtg / turbineImportFilePath)
        old_turb_imp = str(old_runtime.get("turbineImportFilePath", "")).strip()
        if ext == ".wtg":
            matched.add(_SLOT_TURBINE_CURVE)
        elif old_turb_imp and (cand_str == old_turb_imp or cand_str == _basename_without_ext(old_turb_imp)):
            # 若本身为机位坐标文件（如 JWD_Project_43.txt），绝不误入功率曲线槽
            if not is_turb_coord_kw and ext in ("", ".wtg"):
                matched.add(_SLOT_TURBINE_CURVE)

        # 4. TIS 槽 (tisFilePath)
        if "tis" in base:
            matched.add(_SLOT_TIS)

        # 5. TI 槽 (tiFilePath)
        if "-ti." in base or base.endswith("-ti.txt") or (base.endswith("ti.txt") and "tim" not in base and "tis" not in base):
            matched.add(_SLOT_TI)

        # 6. 测风塔时序槽 (mastTim / mastImportFilePath):
        # 必须具备时序语义：包含 "tim" 且非 tis
        # 或者是 old_runtime 的 mastImportFilePath（但若其本身为坐标文件则绝不入此槽，A6 核心）
        old_mast_imp = str(old_runtime.get("mastImportFilePath", "")).strip()
        old_mast_coord = str(old_runtime.get("mastCoordFilePath", "")).strip()

        is_mast_coord_kw = (
            "cft" in base
            or "测风塔" in base
            or ("coord" in base and "jwd" not in base and "turbine" not in base)
        )
        is_mast_tim_kw = ("tim" in base and "tis" not in base)

        if is_mast_tim_kw:
            if ext in ("", ".txt") or (old_mast_imp and ext == os.path.splitext(old_mast_imp)[1].lower()):
                matched.add(_SLOT_MAST_TIM)
        elif old_mast_imp and (cand_str == old_mast_imp or cand_str == _basename_without_ext(old_mast_imp)):
            if not is_mast_coord_kw:
                if ext in ("", ".txt") or ext == os.path.splitext(old_mast_imp)[1].lower():
                    matched.add(_SLOT_MAST_TIM)

        # 7. 测风塔坐标槽 (mastCoord / mastCoordFilePath):
        # 必须具备坐标语义：包含 cft / 测风塔 / (coord 且非 turbine/jwd)
        # 或者等于 old_runtime 的 mastCoordFilePath
        if is_mast_coord_kw:
            if ext in ("", ".txt") or (old_mast_coord and ext == os.path.splitext(old_mast_coord)[1].lower()):
                matched.add(_SLOT_MAST_COORD)
        elif old_mast_coord and (cand_str == old_mast_coord or cand_str == _basename_without_ext(old_mast_coord)):
            if ext in ("", ".txt") or ext == os.path.splitext(old_mast_coord)[1].lower():
                matched.add(_SLOT_MAST_COORD)

        # 8. 机位点坐标槽 (turbineCoord / turbineCoordFilePath):
        old_turb_coord = str(old_runtime.get("turbineCoordFilePath", "")).strip()
        is_turb_coord_kw = (
            "jwd" in base
            or "机位" in base
            or ("coord" in base and ("jwd" in base or "turbine" in base))
        )
        if is_turb_coord_kw:
            if ext in ("", ".txt") or (old_turb_coord and ext == os.path.splitext(old_turb_coord)[1].lower()):
                matched.add(_SLOT_TURBINE_COORD)
        elif old_turb_coord and (cand_str == old_turb_coord or cand_str == _basename_without_ext(old_turb_coord)):
            if ext in ("", ".txt") or ext == os.path.splitext(old_turb_coord)[1].lower():
                matched.add(_SLOT_TURBINE_COORD)

        return matched

    def _is_file_candidate(cand):
        c_str = str(cand).strip()
        if not c_str or c_str.startswith("${"):
            return False
        low = c_str.lower()
        if low in ("cft01", "cft1", "99", "40.5", "120.5", "125", "35.0", "1.220", "5000",
                   "43557986.525", "5136125.583", "43546603.1", "5151337.3", "43559303.1", "5138637.3"):
            return False
        ext = os.path.splitext(c_str)[1].lower()
        if ext in (".txt", ".tif", ".tiff", ".asc", ".dem", ".wtg"):
            return True
        if ":\\" in c_str or ":/" in c_str or "\\" in c_str or "/" in c_str:
            return True
        base = os.path.basename(c_str).lower()
        if any(kw in base for kw in ("tim", "tis", "-ti.", "cft", "jwd", "coord", "测风塔", "机位")):
            return True
        for k in ("sourceFilePath", "mastImportFilePath", "turbineImportFilePath",
                  "mastCoordFilePath", "turbineCoordFilePath", "outputDir"):
            v = str(old_runtime.get(k, "")).strip()
            if v and (c_str == v or c_str == _basename_without_ext(v)):
                return True
        return False

    # 收集待判槽候选集：old_runtime 路径/主体 + steps 中的文件类字面量
    _slot_candidates = set()
    for _k in ("sourceFilePath", "mastImportFilePath", "turbineImportFilePath",
               "mastCoordFilePath", "turbineCoordFilePath", "outputDir"):
        _v = str(old_runtime.get(_k, "")).strip()
        if _v and not _v.startswith("${"):
            _slot_candidates.add(_v)
            _b = _basename_without_ext(_v)
            if _b:
                _slot_candidates.add(_b)

    for _t in _old_text_list:
        if _is_file_candidate(_t):
            _slot_candidates.add(_t)
            if ":\\" in _t or ":/" in _t or os.path.splitext(_t)[1]:
                _b = _basename_without_ext(_t)
                if _b and _is_file_candidate(_b):
                    _slot_candidates.add(_b)

    # 遍历候选集按槽位配对；长串优先（路径优先于文件名主体）
    for _cand in sorted(_slot_candidates, key=lambda x: (len(x), x), reverse=True):
        _slots = _match_slots(_cand)
        if len(_slots) > 1:
            raise ValueError(
                "旧文本 '{0}' 同时命中多个槽位 {1}（拒绝启动，fail-closed）".format(
                    _cand, sorted(_slots)
                )
            )
        if len(_slots) == 1:
            _slot = next(iter(_slots))
            if _slot == _SLOT_OUTPUT_DIR:
                _new_tgt = _slot_targets_full.get(_slot, "")
                if _new_tgt and _cand != _new_tgt:
                    overrides.setdefault(_cand, _new_tgt)
            else:
                _has_ext = bool(os.path.splitext(_cand)[1])
                _is_path = ":\\" in _cand or ":/" in _cand or "\\" in _cand or "/" in _cand
                if _has_ext or _is_path:
                    _new_tgt = _slot_targets_full.get(_slot, "")
                    if _new_tgt and _cand != _new_tgt:
                        if not _has_ext or _same_ext(_cand, _new_tgt):
                            overrides[_cand] = _new_tgt
                            if _cand in _old_texts:
                                required_keys.add(_cand)
                else:
                    _new_tgt = _slot_targets_base.get(_slot, "")
                    if _new_tgt and _cand != _new_tgt:
                        overrides[_cand] = _new_tgt
                        if _cand in _old_texts:
                            required_keys.add(_cand)
    # ── 流程家族（只读判定）：决定哪些写死值只对该类流程纳入必需命中审计 ──
    # F1 修复（T4 复核）：旧实现在 __只要项目参数提供新值__ 就把全部气象/空间键加进每个
    # flow 的 required_keys，有测风塔/气象信息的项目就让【非气象/非建模】板块（发送CFD、
    # 创建综合、Simple 全队列含气象板块）被 zero_hit 非空拒启。改按 flow 家族收窄：
    #   - meteo 字面量（CFT01/99/40.5/120.5/125）：仅气象数据录入家族纳入必需（含模板。
    #     映射_target 即该家族 4 个"新建气象数据_*"文件 + 基础 flow_definition）。
    #   - 综合数值（35.0/1.220）：仅发送综合计算家族纳入必需。
    #   - 建模域键（domainCenter*/domainRadiusR=创建新建模 3 键 / domainNw|Se*=导入并配置
    #     元素 4 键）：分别仅对对应建模/元素家族纳入必需。
    # 家族依据 flow 文件名 / flowPackages.id（只读、唯一不串扰），不依赖运行时 rc，
    # 也不靠宽松的"payload 含某词"（发送CFD/发送综合等 payload 也带气象/新建模字样）。
    _flow_basename = os.path.basename(str(flow_path or ""))
    _flow_pkg_ids = {
        str(p.get("id")) for p in (payload.get("flowPackages") or []) if isinstance(p, dict)
    }
    _is_meteo_flow = (
        "新建气象数据" in _flow_basename
        or "气象数据录入" in _flow_basename
        or "气象数据录入" in _flow_pkg_ids
    )
    _is_composite_flow = "发送综合计算" in _flow_basename
    _is_create_model_flow = "创建一个新建模" in _flow_basename
    _is_element_flow = "导入并配置元素" in _flow_basename

    def _add_scoped_override(old_val, new_val, *, required):
        """把旧文本加入覆盖（若 flow 文本实际含它并替换），并按 required 登记必需命中。

        - 替换语义与旧版完全一致：只要该 flow 的 actionConfig.text 实际含旧值且新值非空、
          与旧值不同，就写入 overrides（无论是否该家族）。apply_overrides_to_payload 不受
          必需判定影响。
        - 必需命中（继承并强化 fail-closed）：仅当 required=True（该 flow 属于该家族，该键
          是它真正需要的）才登记为必需——
            * 新值与旧值相同：模板已含正确值，无需替换，也不登记必需（不误拒）；
            * 新值为空（项目没配该关键值，如气象 flow 缺 meteoName / 建模 flow 缺 domain*）：
              登记必需但不进 overrides → 审计 0 命中 → 硬失败拒启；
            * 模板改版致旧值消失：登记必需但 old_s 不在 _old_texts → 0 命中 → 硬失败。
          非家族 flow（required=False）绝不因该键被拒启（F1 收窄核心）。
        """
        old_s = str(old_val).strip()
        if not old_s:
            return
        new_s = str(new_val or "").strip()
        if required:
            if new_s == old_s:
                # 模板已含正确默认值，无替换意图也不缺配 → 不登记必需、不误拒。
                return
            required_keys.add(old_s)
        if new_s and new_s != old_s and old_s in _old_texts:
            overrides[old_s] = new_s

    # ── 气象数据录入 家族：meteo 字面量（CFT01/99/40.5/120.5/125）→ 新 rc ──
    # CFT01（气象对象名）替换为 meteoName（塔名_轮毂高度_Auto），与测风塔元素名区分。
    # CFT1 已删除：任何 flow 均无该旧值，在映射数组里纯属噪音（T4 指认）。
    _meteo_map = [
        ("CFT01", new_runtime.get("meteoName") or new_runtime.get("mastName")),
        ("99", new_runtime.get("elevation")),
        ("40.5", new_runtime.get("latitude")),
        ("120.5", new_runtime.get("longitude")),
        ("125", new_runtime.get("hubHeight")),
    ]
    for _old, _new in _meteo_map:
        _add_scoped_override(_old, _new, required=_is_meteo_flow)

    # ── 发送综合计算 家族：综合数值项（35.0 极风 / 1.220 空气密度）→ 项目人工确认值 ──
    # 同步约束：WT_Launcher.DEFAULT_PROJECT_PARAMS 的 wind50/airDensity 已与模板对齐，
    # 避免"弹窗预填默认值经保存无条件固化"（_save 会把全部非空字段写入 project_params）
    # 后，映射上线把未人工确认的默认值静默替换进输入框。
    _composite_map = [
        ("35.0", new_runtime.get("wind50")),
        ("1.220", new_runtime.get("airDensity")),
    ]
    for _old, _new in _composite_map:
        _add_scoped_override(_old, _new, required=_is_composite_flow)

    # ── 空间计算域精确覆盖：仅对应建模/元素家族纳入必需 ──
    # 创建一个新建模 3 键：domainCenterX/Y、domainRadiusR=5000
    _create_model_map = [
        ("43557986.525", new_runtime.get("domainCenterX")),
        ("5136125.583", new_runtime.get("domainCenterY")),
        ("5000", new_runtime.get("domainRadiusR")),
    ]
    for _old, _new in _create_model_map:
        _add_scoped_override(_old, _new, required=_is_create_model_flow)
    # 导入并配置元素 4 键：domainNwX/Y、domainSeX/Y（绘图元素）
    _element_map = [
        ("43546603.1", new_runtime.get("domainNwX")),
        ("5151337.3", new_runtime.get("domainNwY")),
        ("43559303.1", new_runtime.get("domainSeX")),
        ("5138637.3", new_runtime.get("domainSeY")),
    ]
    for _old, _new in _element_map:
        _add_scoped_override(_old, _new, required=_is_element_flow)

    return _attach_override_audit(flow_path, overrides, required_keys)


# 审计结果在 text_overrides dict 内的保留键（键名以 "__" 起止，避免与真实旧文本 Key 冲突）
# 调用方（如 WT_Launcher 的启动前置检查）用公开名读取；内部保留旧私有名作别名。
OVERRIDE_AUDIT_KEY = "__override_audit__"
_OVERRIDE_AUDIT_KEY = OVERRIDE_AUDIT_KEY


def audit_text_override_hits(flow_path, overrides, required_keys=None):
    """
    覆盖命中审计：对受导的必需键，统计它在流程步骤 actionConfig.text 中的命中次数。

    关键点：apply_overrides_to_payload 只替换与 Key 完全相等的 text 值。若某必需键在流程里
    根本不存在（流程已改版 / 旧值写法与当前模板不符），覆盖会「静默不生效」——上层还以为
    配置成功。本函数把 0 命中项组装为失败诊断，供调用方判定而非静默吞掉。

    参数：
        flow_path     : 流程 JSON 路径
        overrides     : 已构造的覆盖 dict {old_text: new_text}
        required_keys : 必须命中的旧文本集合；None 表示"全部覆盖键"都算必需。

    返回 dict：
        {
            "hits": {old_text: count},   # 每个覆盖 Key 的实际命中次数
            "zero_hit": ["old1", ...],   # 必需键中 0 命中（未生效）者
            "required": [old1, ...],     # 本次判定为必需的键
        }
    流程无效（不可读/无 steps）返回 {"hits": {}, "zero_hit": [], "required": []}。
    """
    all_keys = list(overrides or {})
    if required_keys is None:
        required = list(all_keys)
    else:
        required_set = set(required_keys)
        # required 包含两类：a) 已在 overrides 中的覆盖键；b) 仅登记为必需但模板不含
        # 该旧值（模板改版/旧值漂移）而尚未进入 overrides 的键——后者正是审计要抓的"静默 0 命中"。
        required = [k for k in all_keys if k in required_set] + \
                   [k for k in required_keys if k not in overrides]
    meta = {"hits": {}, "zero_hit": [], "required": required}
    if not flow_path or not os.path.isfile(flow_path):
        return meta
    try:
        with open(flow_path, "r", encoding="utf-8") as fobj:
            payload = json.load(fobj)
    except (OSError, ValueError):
        return meta
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return meta

    for key in all_keys:
        meta["hits"][key] = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        ac = step.get("actionConfig")
        if isinstance(ac, dict) and isinstance(ac.get("text"), str):
            t = ac.get("text").strip()
            if t in meta["hits"]:
                meta["hits"][t] += 1
    meta["zero_hit"] = [k for k in required if meta["hits"].get(k, 0) == 0]
    return meta


def _attach_override_audit(flow_path, overrides, required_keys):
    """
    在返回 overrides 前写入命中审计，供调用方判定「覆盖是否真的会生效」。
    把审计结果塞进 overrides 的保留键 **_OVERRIDE_AUDIT_KEY**，不修改链路的替换语义
    （apply_overrides_to_payload 不受该键影响）。
    """
    overrides.pop(_OVERRIDE_AUDIT_KEY, None)
    overrides[_OVERRIDE_AUDIT_KEY] = audit_text_override_hits(flow_path, overrides, required_keys)
    return overrides


def apply_overrides_to_payload(payload, text_overrides=None, path_prefix_overrides=None):
    """
    将覆盖映射应用到流程 payload（内存副本），用于运行期写临时流程文件。

    仅替换 actionConfig.text / runtimeConfig 字段值：
      - 精确文本替换（text_overrides）
      - 路径前缀替换（path_prefix_overrides）
    返回新的 payload 副本；不改原对象。
    """
    if not payload:
        return payload
    import copy

    text_ovr = text_overrides or {}
    path_ovr = path_prefix_overrides or {}
    if not text_ovr and not path_ovr:
        return payload

    result = copy.deepcopy(payload)

    def _replace_str(value):
        if not isinstance(value, str):
            return value
        out = value
        if out in text_ovr:
            out = text_ovr[out]
        for old_pre, new_pre in path_ovr.items():
            if old_pre and out.startswith(old_pre):
                out = new_pre + out[len(old_pre):]
                break
        return out

    def _walk(obj):
        if isinstance(obj, dict):
            for k, v in list(obj.items()):
                if k == "text" and isinstance(v, str):
                    obj[k] = _replace_str(v)
                elif isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, (dict, list)):
                    _walk(item)

    runtime_cfg = result.get("runtimeConfig")
    if isinstance(runtime_cfg, dict):
        for k, v in list(runtime_cfg.items()):
            if isinstance(v, str):
                runtime_cfg[k] = _replace_str(v)

    steps = result.get("steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                _walk(step.get("actionConfig"))

    return result


def load_project_params_from_work_dir(work_dir):
    """读取 <项目>/project.params.json（可选），返回 dict；不存在返回 {}。"""
    work_dir = (work_dir or "").strip().rstrip("\\/")
    if not work_dir or not os.path.isdir(work_dir):
        return {}
    params_path = os.path.join(work_dir, "project.params.json")
    if not os.path.isfile(params_path):
        return {}
    try:
        with open(params_path, "r", encoding="utf-8") as fobj:
            payload = json.load(fobj)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def save_project_params_to_work_dir(work_dir, project_params):
    """把人工确认的项目计算参数写入 <项目>/project.params.json（与 load 配对）。

    此前项目里这份文件只"读"不"写"：界面设的 cpVersion / 机型 / 海拔等只存在本机
    launcher_state，换会话、换机器或远程队列 worker 都拿不到，表现为"界面设了 Cp
    版本，跑起来仍是参数表默认值"。写入后这份文件即成为该项目的唯一可信参数载体：
    本地启动、提交远程队列、worker 运行期补齐都读同一份。

    返回 (ok, message)；ok=False 时 message 说明原因（目录不存在/无写权限）。
    """
    work_dir = (work_dir or "").strip().rstrip("\\/")
    if not work_dir or not os.path.isdir(work_dir):
        return False, "项目文件夹不存在"
    payload = {}
    if isinstance(project_params, dict):
        for key, value in project_params.items():
            key_text = str(key or "").strip()
            value_text = str(value or "").strip()
            if key_text and value_text:
                payload[key_text] = value_text
    params_path = os.path.join(work_dir, "project.params.json")
    tmp_path = params_path + ".tmp"
    try:
        if not payload:
            # 界面把参数清空时同步删文件，避免"界面空了、磁盘还留着旧版本"
            if os.path.isfile(params_path):
                os.remove(params_path)
            return True, "参数为空，已清除 project.params.json"
        with open(tmp_path, "w", encoding="utf-8") as fobj:
            json.dump(payload, fobj, ensure_ascii=False, indent=2)
            fobj.write("\n")
        os.replace(tmp_path, params_path)
        return True, params_path
    except OSError as exc:
        try:
            if os.path.isfile(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return False, str(exc)
