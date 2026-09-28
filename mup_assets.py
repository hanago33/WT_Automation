# encoding: utf-8
"""读取 MUP 安装目录资产，为自动化提供权威选项数据与校验。

背景：MUP（WT Meteodyn Universe）的"粗糙度索引文件"下拉框选项来自
<MUP安装目录>/Assets/CorrespondanceFiles/*.txt（如 ESA2020.txt）。
采集器得到的 optionValues 常为空，导致键盘导航兜底不可用、键入搜索无法校验。

安装目录探测优先级（换机/路径变化也能自动找到）：
  1. 环境变量 MUP_INSTALL_DIR
  2. configure_mup_install_dir() 传入的 GM_EXE / 安装目录提示
  3. 注册表卸载信息（DisplayName 含 Meteodyn）
  4. 默认官方安装路径

本模块提供：
- list_correspondance_files()：扫描目录，返回 文件名 → 内容摘要
- correspondance_option_values()：缓存的下拉框候选文件名列表
- inject_dropdown_option_values()：粗糙度索引类控件且选项为空时注入权威名单
- resolve_index_file()：后置校验所选文件真实存在
- status()：资产状态诊断（供总控台环境检测 / 执行器运行前日志）

零外部依赖；安装目录缺失/读取失败时自动降级为空，不影响自动化主流程。
"""
import os
import glob
import sys
from functools import lru_cache

# 默认官方安装位置（探测兜底）
MUP_DEFAULT_DIR = r"C:\Program Files\Meteodyn\MeteodynUniverse"

# ProgramData 用户数据目录（粗糙度对应文件副本 + TIFF 版文件来源）
MUP_DATA_ROOT = os.environ.get("MUP_DATA_DIR", r"C:\ProgramData\Meteodyn\MUP")

# 探测提示：可由执行器/总控台在运行前传入 GM_EXE 等路径
_exe_hint = os.environ.get("MUP_INSTALL_DIR") or None

# 粗糙度索引文件类控件识别关键词（控件名/labelText/uiPath 等命中任一）
_CONTROL_KEYWORDS = (
    "粗糙度",
    "roughness",
    "correspondance",
    "correspondancefile",
    "索引文件",
    "indexfile",
    "粗糙地表",
    "rough",
)


def configure_mup_install_dir(hint):
    """设置安装目录探测提示（GM_EXE 或目录路径），并清空相关缓存。

    换机时安装路径可能变化，由总控台/执行器在运行前传入实际路径。
    """
    global _exe_hint
    if hint:
        _exe_hint = str(hint)
    clear_cache()


def _looks_like_mup_install_dir(path):
    """判断一个目录是否为 MUP 安装目录。"""
    if not path or not os.path.isdir(path):
        return False
    if os.path.isfile(os.path.join(path, "MUPSmartClient.exe")):
        return True
    return os.path.isdir(os.path.join(path, "Assets", "CorrespondanceFiles"))


def _install_dir_from_exe_hint():
    """从 GM_EXE / 目录提示推断安装目录。"""
    if not _exe_hint:
        return None
    hint = _exe_hint
    if os.path.isdir(hint) and _looks_like_mup_install_dir(hint):
        return hint
    if os.path.isfile(hint):
        parent = os.path.dirname(hint)
        if _looks_like_mup_install_dir(parent):
            return parent
        # 可能是 Bin 下路径（MUP 安装目录/bin/gdal/...）
        for _ in range(3):
            parent = os.path.dirname(parent)
            if _looks_like_mup_install_dir(parent):
                return parent
    return None


def _install_dir_from_registry():
    """从注册表卸载信息探测安装目录（含 32/64 位视图）。"""
    try:
        import winreg
    except ImportError:
        return None
    roots = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for root_key, sub in roots:
        try:
            key = winreg.OpenKey(root_key, sub)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(key)[0]):
            try:
                name = winreg.EnumKey(key, i)
                sub_key = winreg.OpenKey(key, name)
                display = ""
                try:
                    display = str(winreg.QueryValueEx(sub_key, "DisplayName")[0] or "")
                except OSError:
                    pass
                if "Meteodyn" in display or "MUP" in display.upper():
                    for prop in ("InstallLocation", "DisplayIcon"):
                        try:
                            val = str(winreg.QueryValueEx(sub_key, prop)[0] or "")
                        except OSError:
                            continue
                        if prop == "DisplayIcon":
                            val = os.path.dirname(val) if os.path.isfile(val) else val
                        if _looks_like_mup_install_dir(val):
                            return val
            except OSError:
                continue
    return None


def _detect_install_dir():
    """按优先级探测安装目录，返回 (目录, 来源)。

    顺序：环境变量/configure hint 直达目录 → GM_EXE 推断 → 注册表 → 默认路径。
    """
    d = _install_dir_from_exe_hint()
    if d:
        return d, "env_or_hint" if (_exe_hint and os.path.isdir(_exe_hint)) else "gm_exe"
    d = _install_dir_from_registry()
    if d:
        return d, "registry"
    if _looks_like_mup_install_dir(MUP_DEFAULT_DIR):
        return MUP_DEFAULT_DIR, "default"
    return None, None


def install_dir():
    """返回探测到的 MUP 安装目录；不可用返回空串。"""
    d, _ = _detect_install_dir()
    return d or ""


def correspondance_dir():
    """返回有效安装目录下的 CorrespondanceFiles 目录；不可用返回空串。"""
    install_dir, _ = _detect_install_dir()
    if not install_dir:
        return ""
    return os.path.join(install_dir, "Assets", "CorrespondanceFiles")


def _programdata_correspondance_dirs():
    """返回 ProgramData 下所有 TOPOGRAPHY_CORRESPONDENCE 目录（glob local_*）。"""
    if not os.path.isdir(MUP_DATA_ROOT):
        return []
    dirs = glob.glob(os.path.join(MUP_DATA_ROOT, "local_*", "DATA", "TOPOGRAPHY_CORRESPONDENCE"))
    return sorted(dirs)


def _scan_dir_for_txt(base):
    """扫描单个目录的 *.txt，返回 {文件名: {path, lines, first_line, sample, source_dir}}。"""
    files = {}
    if not base or not os.path.isdir(base):
        return files
    for fn in sorted(os.listdir(base)):
        if not fn.lower().endswith(".txt"):
            continue
        path = os.path.join(base, fn)
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                lines = [ln.strip() for ln in fh.read().splitlines() if ln.strip()]
        except OSError:
            continue
        files[fn] = {
            "path": path,
            "lines": len(lines),
            "first_line": lines[0] if lines else "",
            "sample": lines[:3],
            "source_dir": base,
        }
    return files


def list_correspondance_files():
    """扫描安装目录 + ProgramData 数据目录的所有 *.txt，合并返回。

    来源合并（同文件名时 ProgramData 副本优先，因其含更新的 TIFF 版）：
      1. <安装目录>/Assets/CorrespondanceFiles/*.txt（如 ESA2020.txt）
      2. <数据目录>/local_*/DATA/TOPOGRAPHY_CORRESPONDENCE/*.txt
         （含 TIFF 版：TiffCLC.txt / TiffESA2009.txt / TiffESA2010.txt / TiffNLCD.txt）

    返回 {文件名: {path, lines, first_line, sample, source_dir}}。
    目录不存在或读取失败时返回空 dict（自动化不因资产缺失而崩溃）。
    """
    files = {}
    # 安装目录（基座）
    for fn, info in _scan_dir_for_txt(correspondance_dir()).items():
        files[fn] = info
    # ProgramData 副本（同文件名覆盖优先；补充 TIFF 版）
    for base in _programdata_correspondance_dirs():
        for fn, info in _scan_dir_for_txt(base).items():
            files[fn] = info
    return files


@lru_cache(maxsize=1)
def correspondance_option_values():
    """返回下拉框候选文件名列表（按文件名排序，供 optionValues 注入）。"""
    return [fn for fn in list_correspondance_files() if fn.lower().endswith(".txt")]


def is_roughness_index_control(control_definition):
    """判断控件是否为"粗糙度索引文件"类下拉框。"""
    if not isinstance(control_definition, dict):
        return False
    haystack = " ".join(
        str(control_definition.get(k) or "")
        for k in (
            "name",
            "labelText",
            "automationId",
            "uiPath",
            "className",
            "controlType",
            "notes",
            "windowTitle",
        )
    ).lower()
    return any(k in haystack for k in _CONTROL_KEYWORDS)


def inject_dropdown_option_values(control_definition, option_values):
    """当控件为粗糙度索引类且 optionValues 为空时，注入权威文件名单。

    返回 (注入后的list, injected: bool)。
    注意：注入名单按目录排序，顺序不一定与 UI 下拉框一致，
    调用方若用于键盘导航，应在导航后做显示值验证，避免顺序错位点错。
    """
    if option_values:
        return option_values, False
    if not is_roughness_index_control(control_definition):
        return option_values, False
    files = correspondance_option_values()
    if not files:
        return option_values, False
    return list(files), True


def resolve_index_file(value):
    """后置校验：value 是否为真实的粗糙度索引文件（兼容带/不带 .txt）。

    返回匹配的文件信息 dict；不存在返回 None。
    """
    v = str(value or "").strip().lower()
    if not v:
        return None
    files = list_correspondance_files()
    lower_map = {k.lower(): k for k in files}
    real = lower_map.get(v)
    if real is not None:
        return files[real]
    if not v.endswith(".txt"):
        real = lower_map.get(v + ".txt")
        if real is not None:
            return files[real]
    return None


def status():
    """资产状态诊断：供总控台环境检测 / 执行器运行前日志。"""
    install_dir, source = _detect_install_dir()
    files = list_correspondance_files() if install_dir else {}
    return {
        "found": bool(install_dir),
        "install_dir": install_dir or "",
        "detect_source": source or "",
        "correspondance_dir": correspondance_dir(),
        "option_count": len(files),
        "options": sorted(files),
    }


def clear_cache():
    """清空选项缓存（安装目录更新/配置变化后调用）。"""
    correspondance_option_values.cache_clear()
