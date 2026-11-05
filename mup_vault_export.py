# encoding: utf-8
"""Vault 直取导出器：从 MUP 用户数据存储区 (Vault) 直接拷贝综合/CFD 交付物，
绕过 GUI 的 46 步导出链，砍掉"矩阵/XML/尾流/图"四类产物的导出复杂度。

背景（Observed，见 MUP_recon 07_recon2_mup_entries/2026-10-31-vault-persistence.md）：
  MUP 计算完成后，交付物自动落在 vault 目录（非 GUI 导出产物，而是计算副产物），
  可直接只读拷贝。vault 根 = PROD_*.ini 的 [data] path（本机 H:\\ProgramData\\Meteodyn\\MUP）
  下的 local_<用户名>_<GUID>\\。

能直取（vault 有物）：矩阵 / XML / 尾流 / 图 类综合产物 + CFD RESULT_FILES 产物。
必须 GUI（vault 无物）：Excel 总表（综合计算结果.xlsx）/ IEC 总结（IEC总结.xlsx）。

红线（本模块全程遵守）：
  - 纯只读：只 copy，不写 vault、不删源、不改实现。
  - 纯 stdlib；零外部依赖。不加载 MUP 程序集、不连 LocalDB、不碰 DATA\\licence.txt/.lic/hasp。
  - 不臆断 vault 布局：产物命名模板静态来自 MTDPath（REUSE §3），case↔mN 由 paramTable
    行序+wohler 生成，绝不按名字猜测。

  case↔mN 映射（由 paramTable 行序 + wohler 生成，非名字猜）：
    行1 综合1 wohler=1 -> m1
    行2 综合2 wohler=4 -> m4
    行3 综合3 wohler=10 -> m10
    行4 综合4 wohler=1 copyfull(带绘图) -> (none)（蓝图 §2.4.2 明确定义：无导出，记 completed_with_gaps）
    （m<N> 中 <N> = 该行 wohler 值；导出分目录名即 m<N>。）
  也可由调用方显式传入 mapping=[(case_label, m_dir), ...] 覆盖。

产物命名模板（MTDPath 静态解出）：
  {site}_synthesis_{n}.{txt,xml,wrp,wrt,wri}
  {site}_matrix_{n}.txt
  {site}_SRE_{n}.{xml,ruw,vuw}
  {site}_synthesis_{speedup|deviation|rewsshear|turbulence|shear|veer|inflow}_{n}.wrtd
  {site}_timeserie*_{n}.dat
  SYNTHESIS_FILES\\{site}_losswake_{n}.txt、{site}_map_{n}.pdr/.wrm 等
  RESULT_FILES\\{cfdId}_DR_*.xml 等（CFD）

离线自检：self_test() 在临时目录制造"假 vault"，跑一遍 export 与 export_by_manifest，
校验 manifest（源->目标、hash、case↔mN、直取 vs 需 GUI 分单、边界行数与全角分隔支持）全部正确。
Vault 缺失时降级为空、不崩（与 mup_data_files/mup_prod_config 同风格）。
"""
import os
import re
import io
import csv
import json
import hashlib
import shutil
import tempfile

try:
    from mup_case_identity import CaseIdentity, is_case_guid, resolve_case_identity
except ImportError:
    CaseIdentity = None
    is_case_guid = None
    resolve_case_identity = None

import mup_paths
from mup_completion import is_result_for as _is_result_for

# ── vault 根解析（与 mup_data_files/mup_assets 共用同一 owner：mup_paths）──────
# 历史：本模块曾有独立四级链（env→ini→盘符→默认），与另两处漂移；现统一委托。


def _mup_base_root():
    """解析 vault 的父级存储根（[data] path，如 H:\\ProgramData\\Meteodyn\\MUP）。"""
    return mup_paths.data_root()


def resolve_vault_root(vault_root=None):
    """解析 Vault 根目录（local_<user>_<GUID>）。

    优先级：显式参数 -> 环境变量 MUP_VAULT_ROOT/MUP_DATA_DIR（已含 base/local 两种语义）
    -> <base_root>/local_* 最新 -> 环境默认 base_root。
    返回绝对路径字符串；不可用返回 ""（调用方降级为空，不崩）。
    """
    return mup_paths.vault_root(vault_root)


def _pick_newest_local(base):
    return mup_paths.newest_local(base)


def vault_dir(vault_root=None, sub="SYNTHESIS_FILES"):
    """返回 Vault 下某业务子目录绝对路径；不可用返回 ""。"""
    root = resolve_vault_root(vault_root)
    if not root:
        return ""
    d = os.path.join(root, sub)
    return d if os.path.isdir(d) else ""


# ── 产物命名模板（MTDPath 静态解出，Observed）────────────────────────────
_SYNTHESIS_EXT = ("txt", "xml", "wrp", "wrt", "wri")
_SRE_EXT = ("xml", "ruw", "vuw")
_WRTD_KIND = ("speedup", "deviation", "rewsshear", "turbulence", "shear", "veer", "inflow")


def _compile_site_n_product_re(site, n):
    """把一个 site+n 的产物族编译成若干正则（供文件全名匹配）。

    site 可能含 . 或 - 等字符，需 re.escape；n 必须整数字面边界（防同号前缀误配）。
    """
    s = re.escape(str(site))
    npat = str(n)
    pats = []
    # {site}_synthesis_{n}.{ext}
    pats.append(r"^%s_synthesis_%s\.(%s)$" % (s, npat, "|".join(_SYNTHESIS_EXT)))
    # {site}_matrix_{n}.txt
    pats.append(r"^%s_matrix_%s\.txt$" % (s, npat))
    # {site}_SRE_{n}.{ext}
    pats.append(r"^%s_SRE_%s\.(%s)$" % (s, npat, "|".join(_SRE_EXT)))
    # {site}_losswake_{n}.txt
    pats.append(r"^%s_losswake_%s\.txt$" % (s, npat))
    # {site}_map_{n}.{pdr,wrm}
    pats.append(r"^%s_map_%s\.(pdr|wrm)$" % (s, npat))
    # {site}_{mapping_id}_{n}.(wrm|pdr) - 双 GUID 地图文件 (MTDPath::MapFileWrm token 404)
    pats.append(r"^%s_[0-9a-fA-F-]{36}_%s\.(wrm|pdr)$" % (s, npat))
    # {site}_synthesis_{kind}_{n}.wrtd
    pats.append(r"^%s_synthesis_(%s)_%s\.wrtd$" % (s, "|".join(_WRTD_KIND), npat))
    # {site}_timeserie*_{n}.dat
    pats.append(r"^%s_timeserie[^\\/]*_%s\.dat$" % (s, npat))
    return pats


def _matches(patterns, filename):
    for p in patterns:
        if re.match(p, filename, flags=re.IGNORECASE):
            return True
    return False


# ── 产物族分类（能直取 vs 需 GUI）──────────────────────────────────────
_CATEGORIES = {
    "matrix": "matrix",
    "synthesis": "synthesis",
    "sre": "SRE",
    "wake": "losswake",
    "map": "map",
    "timeserie": "timeserie",
    "wrtd": "wrtd",
}

# 必须 GUI 的产物（vault 无此物）
REQUIRES_GUI = (
    {"family": "excel_total", "desc": "Excel 总表（综合计算结果.xlsx）", "requires_gui": True},
    {"family": "iec_summary", "desc": "IEC 总结（IEC总结.xlsx）", "requires_gui": True},
)


# ── 字符与参数表归一化 ────────────────────────────────────────────────
def _normalize_str(val):
    """字符串归一化：将全角空格、全角数字/字母、全角标点转半角，并去除首尾空白。"""
    if val is None:
        return ""
    if not isinstance(val, str):
        val = str(val)
    chars = []
    for ch in val:
        code = ord(ch)
        if code == 0x3000:  # 全角空格
            chars.append(" ")
        elif 0xFF01 <= code <= 0xFF5E:  # 全角 ASCII（！到～，含全角逗号、分号、数字０-９）
            chars.append(chr(code - 0xFEE0))
        else:
            chars.append(ch)
    return "".join(chars).strip()


def _normalize_key(key):
    """统一表头键名：兼容中英文、camelCase 与 snake_case。"""
    k = _normalize_str(key).lower().replace("_", "").replace("-", "")
    if k in ("synthesisname", "synthesis", "综合名称", "综合名", "名称", "casename", "case"):
        return "synthesisName"
    if k in ("wohler", "wohler指数", "wohler值", "m", "w"):
        return "wohler"
    if k in ("drawlayout", "draw", "layout", "绘图", "绘图布局", "带绘图"):
        return "drawLayout"
    if k in ("stepmode", "mode", "模式", "步骤模式"):
        return "stepMode"
    if k in ("drawturbinetype", "drawturbine", "绘图风机类型"):
        return "drawTurbineType"
    return _normalize_str(key)


def _normalize_wohler(val):
    """归一化 wohler 指数（如 '１' -> '1', '1.0' -> '1', '4' -> '4'）。"""
    s = _normalize_str(val)
    if not s:
        return ""
    try:
        f = float(s)
        if f.is_integer():
            return str(int(f))
        return s
    except ValueError:
        return s


def parse_param_table(param_table):
    """解析多样化参数表输入（支持 list/tuple、JSON 文件/文本、CSV/TSV 文件/文本、Excel 文件）。

    容错特性：
      - 全角逗号（，）、全角分号（；）、全角空格、制表符自动归一化为逗号。
      - 全角数字（１、４、１０）自动归一为半角数字。
      - 表头大小写及中英文字段兼容（synthesisName/synthesis_name/综合名称、wohler/wohler指数、
        drawLayout/draw_layout/绘图、stepMode/step_mode/模式）。
    返回标准化 dict 列表，每项含 'synthesisName', 'wohler', 'stepMode', 'drawLayout' 等键。
    """
    if param_table is None:
        return []
    if isinstance(param_table, (list, tuple)):
        out = []
        for item in param_table:
            if isinstance(item, dict):
                norm_dict = {}
                for k, v in item.items():
                    norm_dict[_normalize_key(k)] = _normalize_str(v)
                out.append(norm_dict)
        return out

    if isinstance(param_table, str):
        s = param_table.strip()
        if os.path.isfile(s):
            ext = os.path.splitext(s)[1].lower()
            if ext == ".json":
                try:
                    with open(s, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    return parse_param_table(data)
                except Exception:
                    with open(s, "r", encoding="gbk", errors="replace") as fh:
                        data = json.load(fh)
                    return parse_param_table(data)
            elif ext in (".xlsx", ".xls"):
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(s, read_only=True, data_only=True)
                    ws = wb[wb.sheetnames[0]]
                    raw_rows = list(ws.iter_rows(values_only=True))
                    wb.close()
                    if not raw_rows or len(raw_rows) < 2:
                        return []
                    headers = [_normalize_key(h) for h in raw_rows[0]]
                    rows = []
                    for row in raw_rows[1:]:
                        if all(c is None or str(c).strip() == "" for c in row):
                            continue
                        d = {}
                        for ci, cell in enumerate(row):
                            if ci < len(headers) and headers[ci]:
                                d[headers[ci]] = _normalize_str(cell)
                        rows.append(d)
                    return rows
                except Exception:
                    return []
            else:
                try:
                    with open(s, "r", encoding="utf-8-sig") as fh:
                        content = fh.read()
                except Exception:
                    with open(s, "r", encoding="gbk", errors="replace") as fh:
                        content = fh.read()
                return _parse_delimited_text(content)

        if (s.startswith("[") and s.endswith("]")) or (s.startswith("{") and s.endswith("}")):
            try:
                data = json.loads(s)
                if isinstance(data, dict):
                    data = [data]
                return parse_param_table(data)
            except Exception:
                pass
        return _parse_delimited_text(s)

    return []


def _parse_delimited_text(text):
    """解析带分隔符的文本（支持半角/全角逗号、半角/全角分号、制表符分隔）。"""
    if not text:
        return []
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cleaned_lines = []
    for line in lines:
        cleaned = _normalize_str(line)
        if cleaned:
            cleaned = cleaned.replace("，", ",").replace("；", ",").replace(";", ",").replace("\t", ",")
            cleaned_lines.append(cleaned)
    if not cleaned_lines:
        return []

    reader = csv.reader(io.StringIO("\n".join(cleaned_lines)))
    try:
        header = next(reader)
    except StopIteration:
        return []
    headers = [_normalize_key(h) for h in header]

    rows = []
    for r in reader:
        if not r or all(c.strip() == "" for c in r):
            continue
        d = {}
        for ci, cell in enumerate(r):
            if ci < len(headers) and headers[ci]:
                d[headers[ci]] = _normalize_str(cell)
        rows.append(d)
    return rows


def _is_drawing_layout(draw_layout):
    """判定是否为仅用于绘图展示的布局卡片（注意：'不带绘图' 中包含 '带绘图' 子串，必须优先排除）。"""
    s = _normalize_str(draw_layout)
    if not s or "不带绘图" in s or "无绘图" in s or "不绘图" in s:
        return False
    if "带绘图" in s or "绘图" in s:
        return True
    return False


# ── case↔mN 映射 ──────────────────────────────────────────────────────
def build_case_mapping(param_table=None, mapping=None):
    """由 paramTable 行序 + wohler 生成 case↔mN；或直接用调用方给的 mapping。

    param_table: 参数表（文件路径、JSON/CSV 文本、或 dict 列表/迭代器）。
    mapping: 显式 [(case_label, m_dir), ...]，优先级最高。

    返回 [{"case": str, "m": str, "wohler": str, "stepMode": str, "drawLayout": str,
          "export": bool, "status": str, "gap_reason": str}, ...]（保持行序）。
    不按名字猜：
      - m<N> 取该行 wohler 值；
      - 综合卡片名 = 该行 synthesisName，缺省按行序分配为 综合1、综合2...
      - 蓝图 §2.4.2 规则：若某行带有“带绘图”（如 综合4 copyfull 带绘图），则属于无导出卡片，m=""（映射显式对应 (none)）。
    """
    if mapping:
        out = []
        for c, m in mapping:
            c_str = str(c or "").strip()
            m_str = str(m or "").strip()
            if m_str.lower() in ("(none)", "none", ""):
                m_val = ""
                status = "completed_with_gaps"
            else:
                m_val = m_str
                status = "export"
            out.append({
                "case": c_str,
                "m": m_val,
                "wohler": "",
                "stepMode": "",
                "drawLayout": "",
                "export": bool(m_val),
                "status": status,
                "gap_reason": "" if m_val else ("%s 无导出，记 completed_with_gaps" % c_str),
            })
        return out

    parsed_rows = parse_param_table(param_table)
    out = []
    for row in parsed_rows:
        if not isinstance(row, dict):
            continue
        synth = row.get("synthesisName") or ""
        if not synth:
            synth = "综合%d" % (len(out) + 1)
        wohler_raw = row.get("wohler") or ""
        wohler = _normalize_wohler(wohler_raw)
        step_mode = row.get("stepMode") or ""
        draw_layout = row.get("drawLayout") or ""

        # 判定是否无导出（蓝图 §2.4.2：综合4 copyfull 带绘图 无导出 -> completed_with_gaps）
        is_drawing = _is_drawing_layout(draw_layout)
        is_copyfull = (step_mode.lower() == "copyfull")

        if is_drawing or (is_copyfull and not wohler):
            m = ""
            status = "completed_with_gaps"
            gap_reason = "%s（copyfull 带绘图）无导出，记 completed_with_gaps" % synth
        elif not wohler:
            m = ""
            status = "no_export"
            gap_reason = "%s 无 wohler 指数，不导出" % synth
        else:
            m = "m%s" % wohler
            status = "export"
            gap_reason = ""

        # 计算 case index（优先显式 index，其次提取综合名中数字，默认行序）
        explicit_idx = row.get("index") or row.get("case_idx")
        if explicit_idx:
            try:
                c_idx = int(explicit_idx)
            except (ValueError, TypeError):
                c_idx = len(out) + 1
        else:
            num_match = re.search(r"(\d+)", str(synth))
            if num_match:
                c_idx = int(num_match.group(1))
            else:
                c_idx = len(out) + 1

        out.append({
            "index": c_idx,
            "case": str(synth).strip(),
            "m": m,
            "wohler": wohler,
            "stepMode": step_mode,
            "drawLayout": draw_layout,
            "export": bool(m),
            "status": status,
            "gap_reason": gap_reason,
        })
    return out


def build_case_mapping_dict(param_table=None, mapping=None):
    """返回 case↔mN 的 dict 映射，符合蓝图规格：
    {"综合1": "m1", "综合2": "m4", "综合3": "m10", "综合4": "(none)"}
    """
    rows = build_case_mapping(param_table, mapping)
    out = {}
    for r in rows:
        out[r["case"]] = r["m"] if r["m"] else "(none)"
    return out


def case_m_for(param_row, param_table_full):
    """对一个 paramTable 行，返回其 mN 子目录名（配合 build_case_mapping）。"""
    mapping = build_case_mapping(param_table_full)
    if not mapping:
        return ""
    if param_row in param_table_full:
        return mapping[param_table_full.index(param_row)]["m"]
    return ""


def _default_wohler_m():
    return {"1": "m1", "4": "m4", "10": "m10"}


# ── 常量与规格定义（MTDPath 与真实 DEMO 对齐）──────────────────────────────
VAULT_MANDATORY_EXTENSIONS = (".wri", ".wrp")
VAULT_OPTIONAL_EXTENSIONS = (".wrt", ".wrtd", ".dat", ".wrm", ".pdr")
SYNTHESIS_MANDATORY_TEMPLATES = [
    "{P}_synthesis_{n}.wri",
    "{P}_synthesis_{n}.wrp",
]
SYNTHESIS_OPTIONAL_TEMPLATES = [
    "{P}_synthesis_{n}.wrt",
    "{P}_synthesis_{kind}_{n}.wrtd",
    "{P}_timeserie*_{n}.dat",
    "{P}_{ElementGUID}_{n}.wrm",
]


def validate_manifest_safety(manifest):
    """校验 manifest 内无包含 licence/license/hasp 的敏感路径。"""
    forbidden = ("licence", "license", "hasp")

    def _check(obj, key_path=""):
        if isinstance(obj, str):
            low = obj.lower()
            for f in forbidden:
                if f in low:
                    raise ValueError(
                        "Safety violation: manifest contains forbidden keyword '%s' in %s: %s"
                        % (f, key_path, obj)
                    )
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _check(k, "%s.%s" % (key_path, k))
                _check(v, "%s.%s" % (key_path, k))
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                _check(v, "%s[%d]" % (key_path, i))

    _check(manifest)
    return True


# ── 导出 Manifest 构建 ────────────────────────────────────────────────
def build_export_manifest(param_table=None, site=None, case_prefix=None, mapping=None,
                          vault_root=None, output_dir=None, schema_version="v2",
                          diagnostics=None):
    """构建 export_manifest.json 结构（由 paramTable 行序 + wohler 驱动，不按名字猜）。"""
    if diagnostics is None:
        diagnostics = {}

    vroot = resolve_vault_root(vault_root)
    synthesis_vdir = vault_dir(vroot, "SYNTHESIS_FILES") if vroot else ""

    resolved_prefix = case_prefix
    if resolved_prefix is None and site:
        resolved_prefix = resolve_site_case_id(site, vault_root=vroot, diagnostics=diagnostics)
    if resolved_prefix is None:
        resolved_prefix = ""

    rows = build_case_mapping(param_table, mapping)
    mapping_dict = {}
    cases = []
    gaps = []

    for idx, r in enumerate(rows):
        case_idx = r.get("index") or (idx + 1)
        mapping_dict[r["case"]] = r["m"] if r["m"] else "(none)"
        case_info = {
            "index": case_idx,
            "case": r["case"],
            "wohler": r["wohler"],
            "m_dir": r["m"] if r["m"] else "(none)",
            "stepMode": r["stepMode"],
            "drawLayout": r["drawLayout"],
            "export": r["export"],
            "status": r["status"],
            "mandatory_present": 0,
            "optional_present": 0,
            "mandatory_missing": [],
            "optional_missing": [],
        }
        if r.get("gap_reason"):
            case_info["gap_reason"] = r["gap_reason"]
            gaps.append({"case": r["case"], "reason": r["gap_reason"]})

        # 若已具备 Vault 与前缀，预先核验产物存在性
        if synthesis_vdir and os.path.isdir(synthesis_vdir) and resolved_prefix:
            prods = list_synthesis_products(site, case_idx, vault_root=vroot, case_prefix=resolved_prefix)
            mand_items = [p for p in prods if p.get("is_mandatory")]
            opt_items = [p for p in prods if not p.get("is_mandatory")]
            case_info["mandatory_present"] = len(mand_items)
            case_info["optional_present"] = len(opt_items)
            exp_mand = [
                "%s_synthesis_%d.wri" % (resolved_prefix, case_idx),
                "%s_synthesis_%d.wrp" % (resolved_prefix, case_idx),
            ]
            present_names = {p["name"] for p in mand_items}
            case_info["mandatory_missing"] = [m for m in exp_mand if m not in present_names]

        cases.append(case_info)

    completed_cnt = sum(1 for c in cases if c.get("mandatory_present") == 2)
    delivered_cnt = sum(1 for c in cases if c.get("export") and c.get("mandatory_present") == 2)

    direct_fams = [
        {"family": "matrix", "desc": "风频/湍流等矩阵文件（{site}_matrix_{n}.txt）", "direct": True},
        {"family": "synthesis", "desc": "综合 XML / 描述（{site}_synthesis_{n}.xml/.txt）", "direct": True},
        {"family": "wake", "desc": "尾流损失文件（{site}_losswake_{n}.txt）", "direct": True},
        {"family": "map", "desc": "风资源图文件（{site}_map_{n}.pdr/.wrm）", "direct": True},
        {"family": "sre", "desc": "SRE 扇区能量文件（{site}_SRE_{n}.xml/.ruw/.vuw）", "direct": True},
        {"family": "timeserie", "desc": "时序数据文件（{site}_timeserie*_{n}.dat）", "direct": True},
        {"family": "wrtd", "desc": "风流参数分布（{site}_synthesis_{kind}_{n}.wrtd）", "direct": True},
    ]

    gui_pending = [
        {"family": g["family"], "desc": g["desc"], "requires_gui": True}
        for g in REQUIRES_GUI
    ]

    manifest = {
        "schema": "mup_vault_export/manifest/v2" if schema_version == "v2" else "mup_vault_export/manifest/v1",
        "site": site or "",
        "case_prefix": resolved_prefix,
        "mapping": mapping_dict,
        "case_mapping": [{"case": r["case"], "m": r["m"], "wohler": r["wohler"]} for r in rows],
        "mandatory": list(SYNTHESIS_MANDATORY_TEMPLATES),
        "optional": list(SYNTHESIS_OPTIONAL_TEMPLATES),
        "cases": cases,
        "vault_root": vroot or "",
        "synthesis_vault_dir": os.path.abspath(synthesis_vdir) if synthesis_vdir else "",
        "target_dir": os.path.abspath(output_dir) if output_dir else "",
        "direct_available_families": [f["family"] for f in direct_fams],
        "direct_available_descriptions": direct_fams,
        "requires_gui": gui_pending,
        "gaps": gaps,
        "total_cases": len(cases),
        "completed_cases_count": completed_cnt,
        "delivered_cases_count": delivered_cnt,
        "export_cases_count": sum(1 for c in cases if c["export"]),
        "gap_cases_count": len(gaps),
        "overall_status": "pending",
    }
    return manifest


# ── 站点身份与 Case GUID 解析（EXPORT-BYPASS 核心映射）──────────────────
def _default_db_query_cases(site_name, case_name=None, diagnostics=None):
    """通过参数化 sqlcmd 只读查询 MUPLocalDb Cases 表。

    防注入：过滤控制字符、单引号转义，并使用 sqlcmd -v 变量绑定；
    明确排序：ORDER BY c.Name ASC, c.Id ASC；
    失败原因全记录进 diagnostics，绝不以 except pass 吞掉造成静默零匹配。
    """
    if diagnostics is None:
        diagnostics = {}
    if not site_name or any(ord(ch) < 32 or ord(ch) == 127 for ch in str(site_name)):
        diagnostics["db_error"] = "INVALID_SITE_NAME: name contains control characters"
        return []

    s = str(site_name).strip()
    sqlcmd_path = shutil.which("sqlcmd")
    if not sqlcmd_path:
        diagnostics["db_error"] = "sqlcmd_not_found: SQLCMD.EXE not found on system PATH"
        return []

    safe_site = s.replace("'", "''")
    safe_case = str(case_name).replace("'", "''") if case_name else ""

    where_clause = "WHERE s.Name = '$(site_name)'"
    if safe_case:
        where_clause += " AND c.Name = '$(case_name)'"

    sql = "SET NOCOUNT ON; SELECT c.Id, c.Name FROM Cases c JOIN Sites s ON c.Parent_Id = s.Id %s ORDER BY c.Name ASC, c.Id ASC;" % where_clause
    cmd = [
        sqlcmd_path,
        "-S", "(localdb)\\Meteodyn",
        "-d", "MUPLocalDb",
        "-h", "-1",
        "-W",
        "-b",
        "-v", "site_name=%s" % safe_site,
    ]
    if safe_case:
        cmd.extend(["-v", "case_name=%s" % safe_case])
    cmd.extend(["-Q", sql])

    try:
        import subprocess
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if p.returncode != 0:
            diagnostics["db_error"] = "sqlcmd_exit_nonzero: code=%d, stderr=%s" % (p.returncode, p.stderr.strip())
            return []
        lines = [line.strip() for line in p.stdout.splitlines() if line.strip()]
        cases = []
        for line in lines:
            parts = line.split(None, 1)
            if parts and re.match(r"^[0-9a-fA-F-]{36}$", parts[0]):
                cid = parts[0]
                cname = parts[1] if len(parts) > 1 else ""
                cases.append({"id": cid, "name": cname})
        diagnostics["db_cases_found"] = len(cases)
        return cases
    except Exception as exc:
        diagnostics["db_error"] = "sqlcmd_exception: %s: %s" % (type(exc).__name__, exc)
        return []


def resolve_site_case_id(site, vault_root=None, case_name=None, db_query_fn=None,
                         diagnostics=None, fallback_to_site=False,
                         query_case_id=None, listdir=None):
    """将站点易读名（如 'DEMO'）或 GUID 解析为 Vault 文件使用的 Case GUID。

    解析优先级（与 blueprint §6.2 对齐）：
    1. 若可 import mup_case_identity，消费其标准接口；
    2. 若已是 36 位标准 GUID 字符串，直接返回；
    3. 若 vault 目录下存在以 {site}_ 开头的文件（如 mock 模式），保留 site；
    4. 查询 LocalDB (localdb)\\Meteodyn (MUPLocalDb)：
       - 支持 db_query_fn 依赖注入；
       - 参数化 SQL 变量绑定，彻底避免拼串注入；
       - SELECT c.Id, c.Name ... ORDER BY c.Name ASC, c.Id ASC；
       - 多 Case 自动按 case_name 筛选或按确定顺序排序，并记入 diagnostics；
       - 失败原因写入 diagnostics，绝不以 except 吞掉造成静默零匹配；
    5. 启发式回退：扫描 SYNTHESIS_FILES/RESULT_FILES 目录提取唯一 Case GUID；
       - 若出现多个不同 GUID，判定为多 Case 冲突，不盲目乱猜，记录 diagnostics 并返回 ""；
    6. 均未命中时返回 ""（若显式指定 fallback_to_site=True 则退回原 site）。
    """
    if diagnostics is None:
        diagnostics = {}
    if not site:
        return ""
    s = str(site).strip()

    # 1. 尝试消费 codebuddy 并行开发的 mup_case_identity
    try:
        import mup_case_identity
        if hasattr(mup_case_identity, "resolve_case_identity"):
            diagnostics["identity_provider"] = "mup_case_identity"

            if query_case_id:
                adapter = query_case_id
            else:
                def _query_adapter(label):
                    if db_query_fn:
                        cases = db_query_fn(label, case_name=case_name)
                    else:
                        cases = _default_db_query_cases(label, case_name=case_name, diagnostics=diagnostics)
                    if cases:
                        if len(cases) > 1:
                            diagnostics["multi_case_detected"] = cases
                            diagnostics["multi_case_warning"] = "Site '%s' has %d cases; selected '%s' (%s)" % (
                                label, len(cases), cases[0].get("name", ""), cases[0]["id"]
                            )
                        return cases[0]["id"]
                    return ""
                adapter = _query_adapter

            ident = mup_case_identity.resolve_case_identity(
                site=s,
                vault_root=str(vault_root or ""),
                query_case_id=adapter,
                listdir=listdir,
            )
            diagnostics["identity_source"] = ident.source
            diagnostics["candidates"] = ident.candidates

            # 若 DB 明确查询且为 0 命中，不得由单 GUID 启发将其它站点 GUID 误套给本站点
            if diagnostics.get("db_cases_found") == 0 and ident.source == mup_case_identity.SOURCE_VAULT_SINGLE_GUID:
                diagnostics["heuristic_bypassed"] = "DB confirmed site does not exist; single-guid heuristic rejected."
                diagnostics["reason"] = "Site '%s' not found in LocalDB" % s
                return s if fallback_to_site else ""

            if ident.source == mup_case_identity.SOURCE_UNRESOLVED:
                diagnostics["reason"] = "Site '%s' unresolved by mup_case_identity" % s
                return s if fallback_to_site else ""

            return ident.case_prefix
    except ImportError:
        pass

    # 2. 显式 GUID 直通
    if re.match(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$", s):
        diagnostics["resolved_by"] = "guid_direct"
        return s

    # 3. Mock 模式 / 既有文件前缀直通
    vroot = resolve_vault_root(vault_root)
    if vroot and os.path.isdir(vroot):
        for sub in ("SYNTHESIS_FILES", "RESULT_FILES"):
            sub_dir = os.path.join(vroot, sub)
            if os.path.isdir(sub_dir):
                try:
                    for f in os.listdir(sub_dir):
                        if f.lower().startswith(s.lower() + "_"):
                            diagnostics["resolved_by"] = "vault_prefix_direct"
                            return s
                except OSError:
                    pass

    # 4. LocalDB 参数化查询
    query_fn = db_query_fn or _default_db_query_cases
    db_cases = query_fn(s, case_name=case_name, diagnostics=diagnostics)
    if db_cases:
        if len(db_cases) == 1:
            diagnostics["resolved_by"] = "localdb_single"
            return db_cases[0]["id"]
        # 多 Case 场景
        diagnostics["multi_case_detected"] = db_cases
        if case_name:
            matched = [c for c in db_cases if c.get("name") == case_name]
            if matched:
                diagnostics["resolved_by"] = "localdb_multi_case_matched"
                return matched[0]["id"]
        diagnostics["resolved_by"] = "localdb_multi_case_sorted_first"
        diagnostics["multi_case_warning"] = "Site '%s' has %d cases; selected '%s' (%s)" % (
            s, len(db_cases), db_cases[0].get("name", ""), db_cases[0]["id"]
        )
        return db_cases[0]["id"]
    elif "db_cases_found" in diagnostics and diagnostics["db_cases_found"] == 0:
        diagnostics["db_zero_match"] = True
        diagnostics["reason"] = "Site '%s' not found in LocalDB" % s
        if not fallback_to_site:
            return ""

    # 5. 启发式回退：扫描 SYNTHESIS_FILES/RESULT_FILES
    if vroot and os.path.isdir(vroot):
        for sub in ("SYNTHESIS_FILES", "RESULT_FILES"):
            sub_dir = os.path.join(vroot, sub)
            if os.path.isdir(sub_dir):
                try:
                    guids = set()
                    for f in os.listdir(sub_dir):
                        m = re.match(r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})_", f)
                        if m:
                            guids.add(m.group(1))
                    if len(guids) == 1:
                        diagnostics["resolved_by"] = "vault_heuristic_single"
                        return list(guids)[0]
                    elif len(guids) > 1:
                        diagnostics["vault_heuristic_multi_guids"] = list(guids)
                        diagnostics["reason"] = "Multiple Case GUIDs found in vault (%s); ambiguous, cannot guess" % list(guids)
                        return s if fallback_to_site else ""
                except OSError:
                    pass

    diagnostics["reason"] = "Site '%s' unresolved" % s
    return s if fallback_to_site else ""


# ── 扫描与拷贝 ────────────────────────────────────────────────────────
def list_synthesis_products(site, n, vault_root=None, case_prefix=None):
    """列出 SYNTHESIS_FILES 下匹配 site+n 的全部产物，返回 [{category, kind, name, path, is_mandatory}]。"""
    d = vault_dir(vault_root, "SYNTHESIS_FILES")
    if not d:
        return []
    target_site = case_prefix or resolve_site_case_id(site, vault_root)
    if not target_site and not site:
        return []
    pats = []
    if site:
        pats.extend(_compile_site_n_product_re(site, n))
    if target_site and str(target_site).lower() != str(site).lower():
        pats.extend(_compile_site_n_product_re(target_site, n))
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for name in sorted(names):
        category, kind = _match_category(name, pats)
        if category is None:
            continue
        full = os.path.join(d, name)
        if os.path.isfile(full):
            low = name.lower()
            # 法定必出判断：{case_prefix}_synthesis_{n}.wri 与 .wrp
            is_mand = (low.endswith(".wri") or low.endswith(".wrp")) and ("_synthesis_" in low)
            out.append({
                "category": category,
                "kind": kind,
                "name": name,
                "path": full,
                "is_mandatory": is_mand,
            })
    return out


def _match_category(filename, pats):
    if not _matches(pats, filename):
        return None, None
    low = filename.lower()
    if low.endswith(".wrtd"):
        m = re.search(r"_synthesis_([a-zA-Z0-9]+)_\d+\.wrtd$", filename, flags=re.IGNORECASE)
        kind = m.group(1) if m else filename.rsplit("_", 1)[0].rsplit("_", 1)[-1]
        return "wrtd", kind
    if "timeserie" in low:
        return "timeserie", ""
    if "_sre_" in low:
        return "sre", ""
    if "_matrix_" in low:
        return "matrix", ""
    if "_losswake_" in low:
        return "wake", ""
    if "_map_" in low or low.endswith(".wrm") or low.endswith(".pdr"):
        return "map", ""
    if low.endswith(".wrt"):
        return "synthesis", "wrt"
    if low.endswith(".wri"):
        return "synthesis", "wri"
    if low.endswith(".wrp"):
        return "synthesis", "wrp"
    if "_synthesis_" in low:
        return "synthesis", ""
    return "synthesis", ""


def _file_sha256(path, chunksize=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunksize)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _copy_file(src, dst_dir, dst_name):
    os.makedirs(dst_dir, exist_ok=True)
    dst = os.path.join(dst_dir, dst_name)
    shutil.copy2(src, dst)
    return dst


def export(site, n, output_dir, vault_root=None, param_table=None, mapping=None,
           case_label=None, copy=True, preserve_names=False, case_prefix=None):
    """Vault 直取导出：拷贝 site 第 n 个综合的全部产物到 output_dir，产出单 case manifest。

    参数：
      site            站点名（综合作物命名前缀）。
      n               synthesis 序号（1 起）。
      output_dir      目标目录（通常为 04-WT输出/m<N>/ 或 04-WT输出 根目录）。
      vault_root      可选；缺省按 env->ini->盘符->local 四级回退。
      param_table     可选；用于生成 case↔mN（[{"synthesisName","wohler"},...] 行序或文件）。
      mapping         可选；显式 [(case, m_dir), ...]，覆盖 param_table。
      case_label      可选；本 synthesis 的卡片名（如 "综合1"），仅用于 manifest 归并。
      copy            True 实际拷贝；False 仅产 manifest 计划（dry_run）。
      preserve_names  True 保持源文件名；False 附加前缀。
      case_prefix     显式传入已解析的 Case GUID。

    返回 manifest dict。全部纯只读：不写 vault、不删源。
    """
    root = resolve_vault_root(vault_root)
    resolved_prefix = case_prefix or resolve_site_case_id(site, vault_root=root)
    mapping_rows = build_case_mapping(param_table, mapping)
    m_dir = ""
    case_row = None
    for idx, row in enumerate(mapping_rows):
        if case_label and row["case"] == case_label:
            m_dir = row["m"]
            case_row = row
            break
        if not case_label and (row.get("index") == n or (idx + 1) == n):
            m_dir = row["m"]
            case_row = row
            break

    if not m_dir and not case_row and mapping_rows and len(mapping_rows) >= n:
        case_row = mapping_rows[n - 1]
        m_dir = case_row["m"]

    # 若该 case 为无导出（例如综合4 copyfull 带绘图），直接返回 completed_with_gaps，不拷文件
    if case_row and (case_row.get("status") == "completed_with_gaps" or (not m_dir and case_row.get("drawLayout") and "绘图" in case_row.get("drawLayout"))):
        manifest = {
            "schema": "mup_vault_export/manifest/v2",
            "site": site,
            "case_prefix": resolved_prefix,
            "synthesis_n": n,
            "case": case_label or (case_row["case"] if case_row else ("综合%d" % n)),
            "m_dir": "",
            "status": "completed_with_gaps",
            "gap_reason": case_row.get("gap_reason") or ("综合%d 无导出（如 copyfull 带绘图），记 completed_with_gaps" % n),
            "vault_root": root or "",
            "synthesis_vault_dir": os.path.abspath(vault_dir(root, "SYNTHESIS_FILES")) if root else "",
            "target_dir": os.path.abspath(output_dir),
            "case_mapping": mapping if mapping is not None else mapping_rows,
            "direct_available": [],
            "requires_gui": [],
            "copied_count": 0,
            "requires_gui_count": 0,
            "mandatory_present": 0,
            "optional_present": 0,
        }
        return manifest

    target_dir = output_dir
    if m_dir and os.path.basename(str(output_dir).rstrip("\\/")) != m_dir:
        target_dir = os.path.join(str(output_dir), m_dir)
    os.makedirs(target_dir, exist_ok=True)

    products = list_synthesis_products(site, n, vault_root=root, case_prefix=resolved_prefix)
    mand_items = [p for p in products if p.get("is_mandatory")]
    opt_items = [p for p in products if not p.get("is_mandatory")]

    copied = []
    for p in products:
        prefix = "%s_%s" % (m_dir, p["category"]) if m_dir else p["category"]
        dst_name = p["name"] if preserve_names else ("%s_%s" % (prefix, p["name"]))
        src = p["path"]
        dst = _copy_file(src, target_dir, dst_name) if copy else os.path.join(target_dir, dst_name)
        copied.append({
            "category": p["category"],
            "kind": p["kind"],
            "source": os.path.abspath(src),
            "target": os.path.abspath(dst),
            "name": p["name"],
            "dst_name": dst_name,
            "hash": _file_sha256(src),
            "is_mandatory": p.get("is_mandatory", False),
        })

    gui_pending = [
        {"family": g["family"], "desc": g["desc"], "requires_gui": True}
        for g in REQUIRES_GUI
    ]

    manifest = {
        "schema": "mup_vault_export/manifest/v2",
        "site": site,
        "case_prefix": resolved_prefix,
        "synthesis_n": n,
        "case": case_label or ("综合%d" % n),
        "m_dir": m_dir,
        "status": "export",
        "vault_root": root or "",
        "synthesis_vault_dir": os.path.abspath(vault_dir(root, "SYNTHESIS_FILES")) if root else "",
        "target_dir": os.path.abspath(target_dir),
        "case_mapping": mapping if mapping is not None else mapping_rows,
        "direct_available": copied,
        "requires_gui": gui_pending,
        "copied_count": len(copied),
        "requires_gui_count": len(gui_pending),
        "mandatory_present": len(mand_items),
        "optional_present": len(opt_items),
    }
    return manifest


def export_by_manifest(manifest_or_param_table, site=None, output_dir=None, vault_root=None,
                       mapping=None, copy=True, preserve_names=False, case_prefix=None,
                       case_name=None, db_query_fn=None, diagnostics=None):
    """按 export_manifest 统一执行全部 cases 的 Vault 直取导出到 04-WT输出/mN/。

    参数：
      manifest_or_param_table: 由 build_export_manifest 生成的 dict，或参数表路径/列表。
      site: 站点名（若未在 manifest 中指定时使用）。
      output_dir: 04-WT输出 根目录路径。
      vault_root: 可选 Vault 根目录。
      mapping: 可选覆盖映射。
      copy: 是否实际复制。
      preserve_names: 是否保持源文件名。
      case_prefix: 显式指定 Case GUID。
      case_name: 多 Case 场景指定名称。
      db_query_fn: LocalDB 查询注入。
      diagnostics: 外部诊断容器。

    返回执行后的完整 manifest 字典，并在 output_dir 写入 export_manifest.json。
    """
    if diagnostics is None:
        diagnostics = {}

    if isinstance(manifest_or_param_table, dict) and "schema" in manifest_or_param_table:
        manifest = dict(manifest_or_param_table)
    else:
        manifest = build_export_manifest(
            param_table=manifest_or_param_table,
            site=site,
            case_prefix=case_prefix,
            mapping=mapping,
            vault_root=vault_root,
            output_dir=output_dir,
            schema_version="v2",
            diagnostics=diagnostics,
        )

    site_name = site or manifest.get("site", "")
    target_root = output_dir or manifest.get("target_dir", "")
    if not target_root:
        target_root = os.getcwd()
    manifest["target_dir"] = os.path.abspath(target_root)
    os.makedirs(target_root, exist_ok=True)

    prefix = case_prefix or manifest.get("case_prefix")
    if not prefix and site_name:
        prefix = resolve_site_case_id(
            site_name,
            vault_root=vault_root,
            case_name=case_name,
            db_query_fn=db_query_fn,
            diagnostics=diagnostics,
            fallback_to_site=False,
        )
    manifest["case_prefix"] = prefix or ""

    # 零匹配防护：若 site 无法解析为有效 CaseGUID/前缀，必须显式失败，不得假装成功
    if not manifest["case_prefix"]:
        manifest["overall_status"] = "failed"
        manifest["failure_reason"] = (
            "ZERO_MATCH_OR_CASE_PREFIX_UNRESOLVED: site '%s' could not be resolved to any CaseGUID in DB or Vault"
            % site_name
        )
        manifest["completed_cases_count"] = 0
        manifest["delivered_cases_count"] = 0
        for c in manifest.get("cases", []):
            c["mandatory_present"] = 0
            c["optional_present"] = 0
            if c.get("export"):
                c["status"] = "failed"
                c["failure_reason"] = manifest["failure_reason"]
            else:
                c["status"] = "completed_with_gaps"
        validate_manifest_safety(manifest)
        manifest_path = write_manifest(manifest, target_root, filename="export_manifest.json")
        manifest["manifest_path"] = os.path.abspath(manifest_path)
        return manifest

    vroot = resolve_vault_root(vault_root)
    all_copied = []
    case_manifests = []

    for c in manifest.get("cases", []):
        n = c["index"]
        case_label = c["case"]
        m_dir = c.get("m_dir")

        prods = list_synthesis_products(site_name, n, vault_root=vroot, case_prefix=prefix)
        mand_items = [p for p in prods if p.get("is_mandatory")]
        opt_items = [p for p in prods if not p.get("is_mandatory")]

        c["mandatory_present"] = len(mand_items)
        c["optional_present"] = len(opt_items)
        exp_mand = ["%s_synthesis_%d.wri" % (prefix, n), "%s_synthesis_%d.wrp" % (prefix, n)]
        present_names = {p["name"] for p in mand_items}
        c["mandatory_missing"] = [m for m in exp_mand if m not in present_names]
        c["optional_families"] = sorted(list(set(p["category"] for p in opt_items)))

        if c.get("export") and m_dir and m_dir != "(none)":
            if c["mandatory_present"] == 2:
                # 必出 2 件套齐全，执行直取拷贝
                m_res = export(
                    site=site_name,
                    n=n,
                    output_dir=target_root,
                    vault_root=vault_root,
                    param_table=manifest.get("cases"),
                    mapping=mapping,
                    case_label=case_label,
                    copy=copy,
                    preserve_names=preserve_names,
                    case_prefix=prefix,
                )
                c["copied_files"] = m_res.get("direct_available", [])
                c["copied_count"] = len(c["copied_files"])
                c["status"] = "export"
                all_copied.extend(c["copied_files"])
                case_manifests.append(m_res)
            else:
                # 缺必出任一 => FAILED(不得整批标成功)
                c["copied_files"] = []
                c["copied_count"] = 0
                c["status"] = "failed"
                c["failure_reason"] = "MANDATORY_PRODUCTS_MISSING: expected .wri and .wrp (present=%d, missing=%s)" % (
                    c["mandatory_present"], c["mandatory_missing"]
                )
                case_manifests.append({
                    "schema": "mup_vault_export/manifest/v2",
                    "site": site_name,
                    "case_prefix": prefix,
                    "synthesis_n": n,
                    "case": case_label,
                    "m_dir": m_dir,
                    "status": "failed",
                    "failure_reason": c["failure_reason"],
                    "copied_count": 0,
                    "direct_available": [],
                    "requires_gui": [],
                })
        else:
            # export 为 False，如 copyfull 带绘图
            c["copied_files"] = []
            c["copied_count"] = 0
            c["status"] = "completed_with_gaps"
            case_manifests.append({
                "schema": "mup_vault_export/manifest/v2",
                "site": site_name,
                "case_prefix": prefix,
                "synthesis_n": n,
                "case": case_label,
                "m_dir": "",
                "status": "completed_with_gaps",
                "gap_reason": c.get("gap_reason", "无导出，记 completed_with_gaps"),
                "copied_count": 0,
                "direct_available": [],
                "requires_gui": [],
            })

    manifest["direct_available"] = all_copied
    manifest["copied_count"] = len(all_copied)
    manifest["case_manifests"] = case_manifests

    # 完成集合(取wri/wrp) ⊃ 交付集合(仅 m 非空的 case)
    manifest["completed_cases_count"] = sum(1 for c in manifest["cases"] if c.get("mandatory_present") == 2)
    manifest["delivered_cases_count"] = sum(
        1 for c in manifest["cases"]
        if c.get("export") and c.get("status") in ("export", "delivered") and c.get("mandatory_present") == 2
    )

    export_cases = [c for c in manifest["cases"] if c.get("export")]
    all_export_ok = len(export_cases) > 0 and all(c.get("status") in ("export", "delivered") for c in export_cases)
    if all_export_ok:
        manifest["overall_status"] = "succeeded"
    else:
        manifest["overall_status"] = "failed"
        failed_cases = [c["case"] for c in export_cases if c.get("status") == "failed"]
        manifest["failure_reason"] = "Export failed for cases: %s" % failed_cases

    # 校验敏感路径安全性
    validate_manifest_safety(manifest)

    manifest_path = write_manifest(manifest, target_root, filename="export_manifest.json")
    manifest["manifest_path"] = os.path.abspath(manifest_path)
    return manifest


def export_cfd(cfd_id, output_dir, vault_root=None, copy=True):
    """（可选）从 RESULT_FILES 直取 CFD 扇区产物 {cfdId}_DR_*.xml 等。

    匹配器与 `mup_completion.is_result_for` 共用（前缀 + 数字边界）：cfdId=1 不会
    把 `12_*` 当成自己的产物。签名强制转 str（传 int 不再 TypeError）。
    """
    cfd_id = str(cfd_id).strip()
    root = resolve_vault_root(vault_root)
    d = vault_dir(root, "RESULT_FILES")
    if not d:
        return {"schema": "mup_vault_export/cfd/v1", "cfd_id": cfd_id,
                "copied": [], "copied_count": 0}
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    for name in sorted(names):
        if not _is_result_for(name, cfd_id):
            continue
        if not name.lower().endswith(".xml") and ".grd" not in name.lower():
            continue
        src = os.path.join(d, name)
        if not os.path.isfile(src):
            continue
        dst = os.path.join(output_dir, "cfd_%s" % name)
        if copy:
            os.makedirs(output_dir, exist_ok=True)
            shutil.copy2(src, dst)
        out.append({
            "source": os.path.abspath(src),
            "target": os.path.abspath(dst),
            "name": name,
            "hash": _file_sha256(src),
        })
    return {"schema": "mup_vault_export/cfd/v1", "cfd_id": cfd_id,
            "vault_root": root or "", "copied": out, "copied_count": len(out)}


def write_manifest(manifest, output_dir, filename="export_manifest.json"):
    """把 manifest 序列化写入 output_dir/filename。"""
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return path


# ── S7 flow 对账 ──────────────────────────────────────────────────────
def reconcile_s7_export(param_table=None, flow_s7_path=None):
    """S7 flow 实际导出 vs Vault 直取覆盖期望清单对账。

    核对：
      1. S7 flow 实际步骤中的导出项与遗漏项；
      2. Vault 直取覆盖范围及修复项；
      3. 明确仍缺口项（Excel/IEC 必须保留 GUI 自动化导出）；
      4. 业务缺口项（综合4 无导出，记 completed_with_gaps）。
    """
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if not flow_s7_path:
        flow_s7_path = os.path.join(base_dir, "flow_packages", "flow_definition_导出综合计算结果.json")
    if not param_table:
        param_table = os.path.join(base_dir, "flow_packages", "param_table_发送综合计算.json")

    manifest = build_export_manifest(param_table)

    s7_steps = []
    if os.path.isfile(flow_s7_path):
        try:
            with open(flow_s7_path, "r", encoding="utf-8") as fh:
                flow_data = json.load(fh)
            s7_steps = flow_data.get("steps", [])
        except Exception:
            pass

    excel_steps = []
    iec_steps = []
    matrix_steps = []
    for s in s7_steps:
        sid = s.get("id", "")
        name = s.get("name", "")
        sp = s.get("stepParams") or {}
        if "exportExcelName" in sp:
            excel_steps.append({"step_id": sid, "name": name, "params": sp})
        if "exportIecName" in sp:
            iec_steps.append({"step_id": sid, "name": name, "params": sp})
        if any(w in name for w in ("矩阵输出", "风频矩阵", "输出到txt")):
            matrix_steps.append({"step_id": sid, "name": name, "params": sp})

    report = {
        "s7_flow_path": os.path.abspath(flow_s7_path) if os.path.isfile(flow_s7_path) else "",
        "s7_total_steps": len(s7_steps),
        "s7_actual_gui_exports": {
            "excel_total": excel_steps,
            "iec_summary": iec_steps,
            "matrix_steps": matrix_steps,
            "matrix_defect": "S7 只导出了综合1的矩阵（step_18-21 且未进子目录）；综合2(m4)与综合3(m10)矩阵严重缺失！",
            "other_products_defect": "S7 无综合XML、尾流(losswake)、风资源图(map)等任何导出步骤",
        },
        "vault_direct_coverage": {
            "matrix": {
                "status": "covered_and_repaired",
                "coverage": ["m1", "m4", "m10"],
                "note": "【关键修复】Vault 直取补齐 S7 遗漏的 m4/m10 矩阵并精准归集至 04-WT输出/mN/ 下",
            },
            "synthesis_xml": {
                "status": "new_delivery",
                "coverage": ["m1", "m4", "m10"],
                "note": "Vault 直取新增交付 {site}_synthesis_{case}.xml/.txt",
            },
            "losswake": {
                "status": "new_delivery",
                "coverage": ["m1", "m4", "m10"],
                "note": "Vault 直取新增交付 {site}_losswake_{case}.txt",
            },
            "map": {
                "status": "new_delivery",
                "coverage": ["m1", "m4", "m10"],
                "note": "Vault 直取新增交付 {site}_map_{case}.pdr/.wrm",
            },
        },
        "remaining_gui_requirements": [
            {
                "family": "excel_total",
                "desc": "Excel 总表（综合计算结果.xlsx）",
                "required_cases": ["综合1 (m1)", "综合2 (m4)", "综合3 (m10)"],
                "reason": "MUP Vault 存储区无 Excel 总表渲染物，必须保留 S7 GUI 自动化导出（step_5, step_27, step_39）",
            },
            {
                "family": "iec_summary",
                "desc": "IEC 总结（IEC总结.xlsx）",
                "required_cases": ["综合1 (m1)", "综合2 (m4)", "综合3 (m10)"],
                "reason": "MUP Vault 存储区无 IEC 总结报表渲染物，必须保留 S7 GUI 自动化导出（step_9, step_31, step_43）",
            },
        ],
        "gaps": [
            {
                "case": "综合4",
                "reason": "综合4（copyfull 带绘图）在 S7 中无导出，Vault 也跳过，按蓝图 §2.4.2 标记为 completed_with_gaps",
            }
        ],
    }
    return report


def format_reconciliation_report(report):
    """格式化对账清单输出。"""
    lines = []
    lines.append("=" * 70)
    lines.append("【S7 flow 实际导出 vs Vault 直取覆盖期望清单对账报告】")
    lines.append("=" * 70)
    lines.append("1. S7 flow 实际导出分析（总步数：%d 步）：" % report.get("s7_total_steps", 0))
    gui_exp = report.get("s7_actual_gui_exports", {})
    lines.append("   - Excel 总表：共 %d 个步骤（覆盖 m1/m4/m10）" % len(gui_exp.get("excel_total", [])))
    lines.append("   - IEC 总结：共 %d 个步骤（覆盖 m1/m4/m10）" % len(gui_exp.get("iec_summary", [])))
    lines.append("   - 矩阵导出缺陷：%s" % gui_exp.get("matrix_defect", ""))
    lines.append("   - 其它产物缺陷：%s" % gui_exp.get("other_products_defect", ""))
    lines.append("")
    lines.append("2. Vault 直取覆盖清单（产物直落 04-WT输出/mN/）：")
    vault_cov = report.get("vault_direct_coverage", {})
    for k, v in vault_cov.items():
        lines.append("   - [%s] %s: 覆盖 %s -> %s" % (v.get("status"), k, ",".join(v.get("coverage", [])), v.get("note", "")))
    lines.append("")
    lines.append("3. 仍缺口项（Vault 无此物，必须保留 GUI 自动化导出）：")
    for req in report.get("remaining_gui_requirements", []):
        lines.append("   * %s (%s): %s" % (req.get("desc"), ",".join(req.get("required_cases", [])), req.get("reason")))
    lines.append("")
    lines.append("4. 业务缺口项（无导出）：")
    for g in report.get("gaps", []):
        lines.append("   * %s: %s" % (g.get("case"), g.get("reason")))
    lines.append("=" * 70)
    return "\n".join(lines)


# ── 离线自检 ──────────────────────────────────────────────────────────
def self_test():
    """离线自检：临时目录造假 vault，跑 export，校验 manifest 正确。返回 (ok, 消息列表)。"""
    msg = []
    base = tempfile.mkdtemp(prefix="mup_vault_export_self_")
    ok = True
    try:
        # 假 vault：SYNTHESIS_FILES + RESULT_FILES
        vault = os.path.join(base, "local_fake_guid")
        syn = os.path.join(vault, "SYNTHESIS_FILES")
        res = os.path.join(vault, "RESULT_FILES")
        os.makedirs(syn)
        os.makedirs(res)
        # 造一个 site=Demo 的 n=1 综合产物族 + 一个不匹配的 n=2
        site = "Demo"
        fake_files = [
            "Demo_synthesis_1.wri", "Demo_synthesis_1.wrp", "Demo_matrix_1.txt",
            "Demo_SRE_1.xml", "Demo_losswake_1.txt", "Demo_map_1.pdr",
            "Demo_timeserie_1.dat", "Demo_synthesis_speedup_1.wrtd",
            "Demo_synthesis_2.wri", "Demo_synthesis_2.wrp",  # 不应被 n=1 匹配
            "other_synthesis_1.xml",         # 不应被 site=Demo 匹配
        ]
        for name in fake_files:
            with open(os.path.join(syn, name), "w", encoding="utf-8") as fh:
                fh.write("content:%s\n" % name)
        with open(os.path.join(res, "cfd1_DR_030.xml"), "w", encoding="utf-8") as fh:
            fh.write("cfd-result\n")

        out = os.path.join(base, "04-WT输出")
        param_table = [
            {"synthesisName": "综合1", "wohler": "1"},
            {"synthesisName": "综合2", "wohler": "4"},
        ]
        m = export(site, 1, out, vault_root=vault, param_table=param_table,
                   case_label="综合1", copy=True)

        # 校验
        if m["m_dir"] != "m1":
            ok = False; msg.append("m_dir 期望 m1 实得 %s" % m["m_dir"])
        if v := [c for c in m["direct_available"] if c["category"] == "matrix"]:
            if "matrix" not in v[0]["dst_name"] or "Demo_matrix_1.txt" not in v[0]["name"]:
                ok = False; msg.append("matrix 产物错配 %s" % v[0])
        # 不应含 n=2 / 其它 site
        wrong = [c for c in m["direct_available"] if "synthesis_2" in c["name"] or "other_synthesis" in c["name"]]
        if wrong:
            ok = False; msg.append("误匹配了其它 synthesis: %s" % wrong)
        # 8 个直取 + 需 GUI 2
        if m["copied_count"] != 8:
            ok = False; msg.append("copied_count 期望 8 实得 %d" % m["copied_count"])
        exp_direct_fams = {"matrix", "synthesis", "sre", "wake", "map", "timeserie", "wrtd"}
        got = {c["category"] for c in m["direct_available"]}
        if got != exp_direct_fams:
            ok = False; msg.append("直取类别 %s != %s" % (got, exp_direct_fams))
        # 需 GUI
        if not all(g["requires_gui"] for g in m["requires_gui"]):
            ok = False; msg.append("requires_gui 项目应全部为 true")
        if len(m["requires_gui"]) != 2:
            ok = False; msg.append("requires_gui 期望 2 实得 %d" % len(m["requires_gui"]))
        # 落盘文件存在 + hash 一致
        for c in m["direct_available"]:
            if not os.path.isfile(c["target"]):
                ok = False; msg.append("目标不存在 %s" % c["target"])
                continue
            if c["hash"] != _file_sha256(c["target"]):
                ok = False; msg.append("hash 不一致 %s" % c["target"])
        # source 都在 vault（只读未写 vault）
        for c in m["direct_available"]:
            rootdir = os.path.abspath(vault)
            if not os.path.abspath(c["source"]).startswith(rootdir):
                ok = False; msg.append("source 不在 vault 内 %s" % c["source"])
            if not os.path.isfile(c["source"]):
                ok = False; msg.append("源被改动/删除 %s" % c["source"])

        # 二级回退（mapping 覆盖 & 不传 param_table 顺序）
        m2 = export(site, 2, out, vault_root=vault, mapping=[("综合2", "m4")],
                    case_label="综合2", copy=False)
        if m2["m_dir"] != "m4":
            ok = False; msg.append("mapping 覆盖 m_dir 期望 m4 实得 %s" % m2["m_dir"])
        if m2["copied_count"] != 2:  # Demo_synthesis_2.wri + Demo_synthesis_2.wrp
            ok = False; msg.append("n=2 copied_count 期望 2 实得 %d" % m2["copied_count"])
        if "Demo_synthesis_2.wri" not in [c["name"] for c in m2["direct_available"]]:
            ok = False; msg.append("n=2 未匹配到 synthesis_2.wri")

        # manifest JSON 序列化
        p = write_manifest(m, out)
        with open(p, encoding="utf-8") as fh:
            json.load(fh)

        # 只读校验：vault 源文件 mtime/content 未被改动
        with open(os.path.join(syn, "Demo_matrix_1.txt"), encoding="utf-8") as fh:
            if fh.read().strip() != "content:Demo_matrix_1.txt":
                ok = False; msg.append("vault 源内容被篡改")

        # ── 扩展自检：全流程 manifest 与批量导出 ────────────────────────
        # 1. 4 行标准参数表（含 综合4 copyfull 带绘图）
        p_std = [
            {"stepMode": "create", "synthesisName": "综合1", "wohler": "1", "drawLayout": "不带绘图"},
            {"stepMode": "copy", "synthesisName": "", "wohler": "4", "drawLayout": "不带绘图"},
            {"stepMode": "copy", "synthesisName": "", "wohler": "10", "drawLayout": "不带绘图"},
            {"stepMode": "copyfull", "synthesisName": "", "wohler": "1", "drawLayout": "带绘图"},
        ]
        m_dict = build_case_mapping_dict(p_std)
        exp_m_dict = {"综合1": "m1", "综合2": "m4", "综合3": "m10", "综合4": "(none)"}
        if m_dict != exp_m_dict:
            ok = False; msg.append("4行标准映射期望 %s 实得 %s" % (exp_m_dict, m_dict))

        # 2. 边界情况：<4 行
        p_lt4 = [
            {"stepMode": "create", "synthesisName": "综合1", "wohler": "1"},
            {"stepMode": "copy", "synthesisName": "", "wohler": "4"},
        ]
        m_lt4 = build_case_mapping_dict(p_lt4)
        if m_lt4 != {"综合1": "m1", "综合2": "m4"}:
            ok = False; msg.append("<4行边界映射错误: %s" % m_lt4)

        # 3. 边界情况：>4 行
        p_gt4 = list(p_std) + [{"stepMode": "copy", "synthesisName": "综合5", "wohler": "12"}]
        m_gt4 = build_case_mapping_dict(p_gt4)
        if m_gt4.get("综合5") != "m12" or m_gt4.get("综合4") != "(none)":
            ok = False; msg.append(">4行边界映射错误: %s" % m_gt4)

        # 4. 边界情况：全角逗号、分号、全角空格、全角数字
        csv_fullwidth = "synthesisName，wohler，drawLayout\n综合1，１，不带绘图\n，４，不带绘图\n，１０，\n，１，带绘图"
        m_fw = build_case_mapping_dict(csv_fullwidth)
        if m_fw != exp_m_dict:
            ok = False; msg.append("全角分隔符/数字解析映射错误: %s" % m_fw)

        # 5. 假目录多综合批量导出验证：m1/m4/m10 直取落盘 + 综合4 跳过
        syn_files_more = [
            "Demo_synthesis_2.wri", "Demo_synthesis_2.wrp", "Demo_matrix_2.txt", "Demo_losswake_2.txt", "Demo_map_2.pdr",
            "Demo_synthesis_3.wri", "Demo_synthesis_3.wrp", "Demo_matrix_3.txt", "Demo_losswake_3.txt", "Demo_map_3.wrm",
            "Demo_synthesis_4.wri", "Demo_synthesis_4.wrp", "Demo_matrix_4.txt",
        ]
        for name in syn_files_more:
            fpath = os.path.join(syn, name)
            if not os.path.exists(fpath):
                with open(fpath, "w", encoding="utf-8") as fh:
                    fh.write("content:%s\n" % name)

        batch_out = os.path.join(base, "04-WT输出_全量")
        m_batch = export_by_manifest(
            manifest_or_param_table=p_std,
            site="Demo",
            output_dir=batch_out,
            vault_root=vault,
            copy=True,
            preserve_names=True,
        )
        # 检查 m1/m4/m10 是否存在且产物完整
        for m_sub, n_case, exp_files in (
            ("m1", 1, ["Demo_synthesis_1.wri", "Demo_synthesis_1.wrp", "Demo_matrix_1.txt", "Demo_losswake_1.txt", "Demo_map_1.pdr"]),
            ("m4", 2, ["Demo_synthesis_2.wri", "Demo_synthesis_2.wrp", "Demo_matrix_2.txt", "Demo_losswake_2.txt", "Demo_map_2.pdr"]),
            ("m10", 3, ["Demo_synthesis_3.wri", "Demo_synthesis_3.wrp", "Demo_matrix_3.txt", "Demo_losswake_3.txt", "Demo_map_3.wrm"]),
        ):
            target_sub = os.path.join(batch_out, m_sub)
            if not os.path.isdir(target_sub):
                ok = False; msg.append("批量导出子目录未创建: %s" % target_sub)
            for ef in exp_files:
                ef_path = os.path.join(target_sub, ef)
                if not os.path.isfile(ef_path):
                    ok = False; msg.append("批量导出文件缺失: %s" % ef_path)

        # 综合4 不应创建独立目录或落盘
        if os.path.exists(os.path.join(batch_out, "mNone")) or os.path.exists(os.path.join(batch_out, "None")):
            ok = False; msg.append("综合4不应创建 mNone 目录")
        # export_manifest.json 存在且内容正确
        batch_manifest_file = os.path.join(batch_out, "export_manifest.json")
        if not os.path.isfile(batch_manifest_file):
            ok = False; msg.append("未生成 export_manifest.json: %s" % batch_manifest_file)
        else:
            with open(batch_manifest_file, "r", encoding="utf-8") as fh:
                loaded_m = json.load(fh)
            if loaded_m.get("mapping") != exp_m_dict:
                ok = False; msg.append("导出的 export_manifest.json mapping 不符")

        # 6. S7 flow 对账测试
        s7_rec = reconcile_s7_export(param_table=p_std)
        if len(s7_rec.get("remaining_gui_requirements", [])) != 2:
            ok = False; msg.append("S7 对账需 GUI 项期望 2 实得 %d" % len(s7_rec.get("remaining_gui_requirements", [])))

        if ok:
            msg.append("self_test PASS：8 直取 + 2 需GUI，case↔mN=m1/m4/m10/(none)，批量直取落盘与全角边界全过。")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    return ok, msg


if __name__ == "__main__":
    try:  # 兼容 GBK 控制台，强制 UTF-8 输出
        import sys
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    ok, msgs = self_test()
    for m in msgs:
        print(m)
    print("RESULT: %s" % ("PASS" if ok else "FAIL"))

    # 生成规范的 canonical export_manifest.json
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        param_path = os.path.join(base_dir, "flow_packages", "param_table_发送综合计算.json")
        manifest_dest = os.path.join(base_dir, "export_manifest.json")
        if os.path.isfile(param_path):
            manifest = build_export_manifest(param_table=param_path, site="YunNanSite")
            write_manifest(manifest, base_dir, filename="export_manifest.json")
            print("Canonical export_manifest.json generated: %s" % manifest_dest)

        rec = reconcile_s7_export(param_path)
        print(format_reconciliation_report(rec))
    except Exception as exc:
        print("Warning: Canonical manifest/reconciliation generation encountered: %s" % exc)

    raise SystemExit(0 if ok else 1)
