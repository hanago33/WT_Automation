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

_PROJ_ABBREV_RE = re.compile(r"(CGCS2000\s*\d{1,3}|UTM\s*\d{1,2}[A-Z]?|WGS84\s*\d{0,3})", re.IGNORECASE)
# 数字对：第一个数字前不能是字母/数字/下划线（避免把 C1831 里的 1831 当成坐标）
_COORD_PAIR_RE = re.compile(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?\s+[+-]?\d+(?:\.\d+)?")


def _is_terrain(name):
    return any(k in name for k in _TERRAIN_KEYWORDS)


def _is_coords(name):
    return any(k in name for k in _COORDS_KEYWORDS)


def _is_mastdata(name):
    return any(k in name for k in _MASTDATA_KEYWORDS)


def _is_turbine(name):
    return any(k in name for k in _TURBINE_KEYWORDS)


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
                    parts = m.group(0).split()
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
                parts = re.split(r"[\s,;]+", line)
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


def _pick_mast_file(all_files, mast_name, input_root):
    """
    从文件列表中挑出指定测风塔的数据文件。
    优先 <03-测风塔数据>/<mastName>/ 目录下，回退「父目录名 == mastName」匹配。
    未匹配返回 ""。
    """
    if not mast_name:
        return ""
    mast_root = ""
    if input_root and os.path.isdir(input_root):
        for name in os.listdir(input_root):
            if _is_mastdata(name):
                mast_root = os.path.join(input_root, name)
                break
    if mast_root and os.path.isdir(mast_root):
        cand_dir = os.path.join(mast_root, mast_name)
        if os.path.isdir(cand_dir):
            for p in all_files:
                if p.startswith(cand_dir + os.sep) or os.path.dirname(p) == cand_dir:
                    return p
    for p in all_files:
        if os.path.basename(os.path.dirname(p)) == mast_name:
            return p
    return ""


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
                token = line.split()[0]
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
                        ids.add(sub)
    for path in tim_files:
        parent = os.path.basename(os.path.dirname(path))
        if parent and parent != "03-WT输入":
            ids.add(parent)
    for path in coord_txts:
        # 只把「测风塔坐标文件」里的编号计入测风塔对象（CFT*）；JWD* 为机位点，不计入
        if not _is_mast_coord(path):
            continue
        for token in _collect_name_tokens(path):
            ids.add(token)
    return sorted(ids)


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
    wtg_files = _scan_files(input_root, (".wtg",))
    tim_files = _scan_files(input_root, (".txt",))
    tim_files = [p for p in tim_files if "-tim" in p.lower() or "tim." in p.lower()]
    ti_files = [p for p in _scan_files(input_root, (".txt",)) if "-ti." in p.lower() or "ti." in p.lower()]
    tis_files = [p for p in _scan_files(input_root, (".txt",)) if "-tis" in p.lower() or "tis." in p.lower()]

    # 其余 txt（机位点 / 测风塔坐标 / 粗糙度 / 障碍物 / 网格）
    coord_txts = [p for p in _scan_files(input_root, (".txt",)) if p not in tim_files + ti_files + tis_files]
    # 测风塔坐标文件（CFT*）与机位点坐标文件（JWD*）区分
    mast_coords = [p for p in coord_txts if _is_mast_coord(p)]
    turbine_coords = [p for p in coord_txts if not _is_mast_coord(p)]

    # ── 地形图 → sourceFilePath / 投影缩写 / 地形名 ──
    if terrain_files:
        terrain_path = terrain_files[0]
        rc["sourceFilePath"] = terrain_path
        rc["projectionFilePath"] = terrain_path
        terrain_name = _basename_without_ext(terrain_path)
        rc["terrainName"] = terrain_name
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

    # ── CFT信息.txt 单源（01-测风塔及机位点坐标/CFT信息.txt）──
    cft_info_path = _find_cft_info_file(input_root)
    cft_entries = _parse_cft_info_file(cft_info_path)
    if cft_entries:
        rc["cftInfoPath"] = cft_info_path
        rc["mastEntries"] = cft_entries
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
    # ── 机位名单 turbineIds / joined ──
    turbine_ids: list[str] = []
    if turbine_coords:
        turbine_ids = [str(r[0]).strip() for r in turbine_coords
                       if r and str(r[0]).strip()]
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

    # ── 项目计算参数（人工确认项）透传进 runtime_config ──
    calc_keys = ("radius", "cfdHRes", "cfdBuf", "cfdMax", "cfdMin",
                 "cpVersion", "mastId", "wind50", "elevation", "airDensity",
                 "turbineType", "mastName", "latitude", "longitude", "hubHeight",
                 "utmX", "utmY", "mastImportFilePath", "tiFilePath", "tisFilePath")
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
        "turbine_pos_file": rc.get("turbinePosFilePath", ""),
        "mast_tim": rc.get("mastImportFilePath", ""),
        "mast_ti": rc.get("tiFilePath", ""),
        "mast_tis": rc.get("tisFilePath", ""),
        "turbine_curve": rc.get("turbineImportFilePath", ""),
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
    if not flow_path or not os.path.isfile(flow_path):
        return overrides

    try:
        with open(flow_path, "r", encoding="utf-8") as fobj:
            payload = json.load(fobj)
    except (OSError, ValueError):
        return overrides
    if not isinstance(payload, dict):
        return overrides

    old_runtime = payload.get("runtimeConfig") or {}
    new_runtime = runtime_config or {}

    def _same_ext(a, b):
        return os.path.splitext(str(a))[1].lower() == os.path.splitext(str(b))[1].lower()

    # 旧文件名主体 → 新文件名主体（如 旧地形名 C1831 → 新 TEST1_Project_CGCS2000 43）
    def _collect_name_pairs():
        pairs = []
        for key in ("sourceFilePath", "mastImportFilePath", "turbineImportFilePath", "outputDir"):
            old = str(old_runtime.get(key, "")).strip()
            new = str(new_runtime.get(key, "")).strip()
            if old and new and os.path.basename(old) != os.path.basename(new) and _same_ext(old, new):
                old_base = _basename_without_ext(old)
                new_base = _basename_without_ext(new)
                if old_base and new_base:
                    pairs.append((old_base, new_base))
                # 输出路径旧前缀 → 新根
                if key == "outputDir" and old and new:
                    overrides.setdefault(old, new)
        return pairs

    name_pairs = _collect_name_pairs()

    # 收集旧 runtimeConfig 里未在解析器覆盖中的旧值（如 sourceFilePath 旧文件路径）→ 新值
    for key, new_val in new_runtime.items():
        if key in old_runtime:
            old_val = str(old_runtime[key])
            new_val_str = str(new_val)
            if old_val and new_val_str and old_val != new_val_str and not old_val.startswith("${"):
                if key in ("sourceFilePath", "mastImportFilePath", "turbineImportFilePath", "outputDir"):
                    if _same_ext(old_val, new_val_str):
                        overrides[old_val] = new_val_str

    # 将文件名主体替换合并（旧地形名 → 新地形名）
    for old_base, new_base in name_pairs:
        if old_base and new_base:
            overrides[old_base] = new_base

    # ── 气象流程（新建气象数据）写死值精确替换 ──
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
    def _add_meteo_override(old_val, new_val):
        if not old_val or not new_val:
            return
        old_s = str(old_val).strip()
        new_s = str(new_val).strip()
        if old_s and new_s and old_s != new_s and old_s in _old_texts:
            overrides[old_s] = new_s

    # 硬编码 meteo 键（CFT01/99/40.5/120.5/125）→ 新 rc
    _meteo_map = [
        ("CFT01", new_runtime.get("mastName")),
        ("CFT1", new_runtime.get("mastName")),
        ("99", new_runtime.get("elevation")),
        ("40.5", new_runtime.get("latitude")),
        ("120.5", new_runtime.get("longitude")),
        ("125", new_runtime.get("hubHeight")),
    ]
    for _old, _new in _meteo_map:
        _add_meteo_override(_old, _new)
    #  hub 高度多处复用：若新 hub 与旧 125 相同则无需覆盖，但仍处理 elevation 误用 99 的情况
    #  文件路径精确替换（tim/TI/TISD）
    for _old in _old_text_list:
        low = _old.lower()
        is_path = (":\\" in _old or ":/" in _old or _old.lower().endswith(".txt")) and os.path.isabs(_old)
        if not is_path:
            # 也处理非绝对但含 tim 关键字的旧值（部分流程写死为全路径，必为绝对）
            if not (low.endswith(".txt") and ("tim" in low or "ti" in low or "tis" in low)):
                continue
            if ":\\" not in _old and ":/" not in _old:
                continue
        new_target = ""
        if "tis" in low:
            new_target = str(new_runtime.get("tisFilePath", "")).strip()
        elif "-ti." in low or low.endswith("-ti.txt") or (low.endswith("ti.txt") and "tim" not in low and "tis" not in low):
            new_target = str(new_runtime.get("tiFilePath", "")).strip()
        elif "tim" in low:
            new_target = str(new_runtime.get("mastImportFilePath", "")).strip()
        if new_target and _old != new_target:
            overrides[_old] = new_target

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
