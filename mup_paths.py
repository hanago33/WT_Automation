# encoding: utf-8
"""MUP 用户数据根 / vault 根的**唯一解析器**（纯 stdlib、纯只读）。

为什么存在：同一语义此前有三套实现，且互相漂移——
  · `mup_data_files.MUP_DATA_ROOT`：**模块导入时**固化 env，且硬编码 `C:\\ProgramData\\Meteodyn\\MUP`；
  · `mup_assets.MUP_DATA_ROOT`：同一份硬编码的复制（换机即静默空）；
  · `mup_vault_export._mup_base_root()`：自带 env→ini→盘符→默认四级链。
本机真实数据根在 `H:\\ProgramData\\Meteodyn\\MUP`（官方 `PROD_MUPSmartClient.ini` 的 `[data] path`），
C: 不存在 ⇒ 上面两处副本永远返回空、落盘证据静默失效。本模块把三处收敛为单一 owner。

解析优先级（每次调用重新解析，**不缓存**；运行期改 env 立即生效）：
  1. 环境变量 `MUP_VAULT_ROOT` / `MUP_DATA_DIR`（允许直接给 base 根或 local_* 目录）
  2. 已安装 MUP 的 `PROD_MUPSmartClient.ini` → `[data] path`（官方真实解析链）
  3. 常见盘符物理存在性探测（H:, C:, D:, E:）
  4. 官方默认 `C:\\ProgramData\\Meteodyn\\MUP`（可能不存在，由调用方降级）
"""
import os
import glob
import configparser

# 环境变量键：两套历史命名并存，按优先级顺序解析
ENV_KEYS = ("MUP_VAULT_ROOT", "MUP_DATA_DIR")

# 盘符探测顺序：本机真实数据根在 H:（官方 ini 也在 H:）
DEFAULT_DRIVES = ("H:", "C:", "D:", "E:")

# 已安装 MUP 的官方配置文件（[data] path 即用户数据根）
INSTALL_INI_CANDIDATES = (
    r"H:\Program Files\Meteodyn\MeteodynUniverse\PROD_MUPSmartClient.ini",
    r"C:\Program Files\Meteodyn\MeteodynUniverse\PROD_MUPSmartClient.ini",
    r"D:\Program Files\Meteodyn\MeteodynUniverse\PROD_MUPSmartClient.ini",
    r"E:\Program Files\Meteodyn\MeteodynUniverse\PROD_MUPSmartClient.ini",
)

DEFAULT_DATA_ROOT = r"C:\ProgramData\Meteodyn\MUP"


def _env_base():
    """环境变量指定的数据根（存在才返回）。"""
    for key in ENV_KEYS:
        value = os.environ.get(key)
        if value and os.path.isdir(value):
            return value
    return ""


def _ini_base():
    """官方 ini 的 [data] path（默认参数文件，本机只读）。"""
    for ini_path in INSTALL_INI_CANDIDATES:
        if not os.path.isfile(ini_path):
            continue
        try:
            cp = configparser.ConfigParser()
            cp.read(ini_path, encoding="utf-8")
            if cp.has_option("data", "path"):
                expanded = os.path.expandvars(cp.get("data", "path").strip())
                if os.path.isdir(expanded):
                    return expanded
        except Exception:
            continue
    return ""


def _drive_base():
    """按盘符顺序找 `<盘>:\\ProgramData\\Meteodyn\\MUP`。"""
    for drive in DEFAULT_DRIVES:
        candidate = os.path.join(drive, os.sep, "ProgramData", "Meteodyn", "MUP")
        if os.path.isdir(candidate):
            return candidate
    return ""


def data_root():
    """返回 MUP 用户数据根（base 根，其下为 local_<用户>_<GUID>）。

    解析顺序见模块 docstring。第 4 级兜底即使目录不存在也返回默认字符串，
    调用方（locate_data_dir / snapshot）用 isdir 自行降级为空。
    """
    for resolver in (_env_base, _ini_base, _drive_base):
        found = resolver()
        if found:
            return found
    return os.environ.get("MUP_DATA_DIR", DEFAULT_DATA_ROOT)


def local_dirs(base=None):
    """返回 base 下全部 `local_*` 目录（按修改时间新→旧）。"""
    base = base or data_root()
    if not base or not os.path.isdir(base):
        return []
    try:
        candidates = glob.glob(os.path.join(base, "local_*"))
        candidates.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    except OSError:
        return []
    return candidates


def newest_local(base=None):
    """返回 base 下最新的 `local_*` 目录；没有则空串。"""
    found = local_dirs(base)
    return found[0] if found else ""


def vault_root(explicit=None):
    """返回 vault 根（`local_<用户>_<GUID>`）。

    显式参数优先：既接受 vault 根（含 SYNTHESIS_FILES），也接受 base 根（自动补最新 local_*）。
    不可用返回空串（调用方降级为空，不崩）。
    """
    if explicit:
        root = str(explicit).strip()
        if root and os.path.isdir(root):
            if os.path.isdir(os.path.join(root, "SYNTHESIS_FILES")):
                return root
            local = newest_local(root)
            return local or root
        return ""
    base = data_root()
    if not base or not os.path.isdir(base):
        return ""
    local = newest_local(base)
    if local:
        return local
    # base 根自身即数据目录（无 local_* 的部署形态）也接受
    return base if os.path.isdir(os.path.join(base, "SYNTHESIS_FILES")) else ""
