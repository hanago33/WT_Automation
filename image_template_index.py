# encoding: utf-8
"""image_template_index —— 统一图片模板索引加载器。

把散落在 image_templates 下的模板统一成 {key: 绝对路径} 索引：
- 各子目录的 templates_index.json（采集器维护，含 category/file_name/image_path）
- recorder_captures/*.png（pywinauto_recorder 伴随拾取自动截图）
- 顶层 *.png（早期 auto_cap_* 实验产物）

供执行器模板兜底（fallbackTemplate / 控件 templateKey 自动接线）与图像匹配使用。
key 统一用相对 image_templates 根的 "/" 分隔路径，兼容多种写法：
  "recorder_captures/step_19_控件19.png"
  "Icons/确定.png"
  "image_templates/Icons/确定.png"（相对项目根）
  绝对路径
"""
from __future__ import annotations

import json
import os

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
IMAGE_TEMPLATE_ROOT = os.path.join(PROJECT_ROOT, "image_templates")

_cache: dict[str, str] | None = None


def _norm_key(rel_path: str) -> str:
    return os.path.normpath(str(rel_path or "")).replace("\\", "/").strip("/")


def _scan_index_files(index: dict[str, str]) -> None:
    """读取各子目录 templates_index.json 中的模板记录。"""
    if not os.path.isdir(IMAGE_TEMPLATE_ROOT):
        return
    for dirpath, _dirnames, filenames in os.walk(IMAGE_TEMPLATE_ROOT):
        if "templates_index.json" not in filenames:
            continue
        try:
            with open(os.path.join(dirpath, "templates_index.json"), "r", encoding="utf-8") as f:
                payload = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(payload, dict):
            continue
        rel_dir = os.path.relpath(dirpath, IMAGE_TEMPLATE_ROOT)
        for t in payload.get("templates", []) or []:
            if not isinstance(t, dict):
                continue
            file_name = str(t.get("file_name", "")).strip()
            if not file_name:
                continue
            category = str(t.get("category", "")).strip() or "default"
            image_path = str(t.get("image_path", "")).strip()
            if not image_path or not os.path.exists(image_path):
                image_path = os.path.join(dirpath, category, file_name + ".png")
            if not os.path.exists(image_path):
                continue
            # 三种 key 写法都注册，便于不同来源的引用都能命中
            index[_norm_key(os.path.join(rel_dir, file_name + ".png"))] = image_path
            index[_norm_key(os.path.join(rel_dir, category, file_name + ".png"))] = image_path
            index[_norm_key(os.path.join(category, file_name + ".png"))] = image_path


def _scan_png_dirs(index: dict[str, str]) -> None:
    """扫描 recorder_captures/、auto_captured/（递归）与顶层 *.png。"""
    if not os.path.isdir(IMAGE_TEMPLATE_ROOT):
        return
    # recorder_captures（录制伴随拾取）
    rec_dir = os.path.join(IMAGE_TEMPLATE_ROOT, "recorder_captures")
    if os.path.isdir(rec_dir):
        for fn in sorted(os.listdir(rec_dir)):
            if fn.lower().endswith(".png"):
                index[_norm_key(os.path.join("recorder_captures", fn))] = os.path.join(rec_dir, fn)
    # auto_captured（执行中自动采集，按窗口子目录递归）
    auto_dir = os.path.join(IMAGE_TEMPLATE_ROOT, "auto_captured")
    if os.path.isdir(auto_dir):
        for dirpath, _dirnames, filenames in os.walk(auto_dir):
            rel = os.path.relpath(dirpath, IMAGE_TEMPLATE_ROOT)
            for fn in sorted(filenames):
                if fn.lower().endswith(".png"):
                    index[_norm_key(os.path.join(rel, fn))] = os.path.join(dirpath, fn)
    # 顶层（auto_cap_* 等早期产物）
    for fn in sorted(os.listdir(IMAGE_TEMPLATE_ROOT)):
        if fn.lower().endswith(".png"):
            index[_norm_key(fn)] = os.path.join(IMAGE_TEMPLATE_ROOT, fn)


def sanitize_filename(name: str, fallback: str = "control") -> str:
    """把窗口标题/控件名清洗为合法且可读的文件名（与采集器命名风格一致）。"""
    import re
    cleaned = (name or "").strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    cleaned = re.sub(r'[<>:"/\\|?*]+', "", cleaned)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or fallback


def auto_capture_path(window_title: str = "", control_name: str = "") -> tuple[str, str]:
    """返回执行中自动采集模板的 (目录, 文件路径)。

    结构：image_templates/auto_captured/<窗口>/<控件名>.png
    """
    window_dir = sanitize_filename(window_title, "unknown_window")
    file_name = sanitize_filename(control_name, "control") + ".png"
    directory = os.path.join(IMAGE_TEMPLATE_ROOT, "auto_captured", window_dir)
    return directory, os.path.join(directory, file_name)


def images_are_similar(a, b, threshold: int = 8) -> bool:
    """感知哈希对比两张模板图片是否一致（汉明距离 <= threshold 视为一致）。

    a / b 可为文件路径或 PIL.Image 对象（支持“新截图在内存、旧模板在磁盘”的对比场景）。
    用于“自动更新模板”开关：每次运行截图与上次模板对比，
    一致则保留（说明控件外观未变），不一致才替换——避免把假成功/界面波动
    的错误截图覆盖掉已有可用模板。
    """
    try:
        import cv2
        import numpy as np
    except Exception:
        return False

    def _gray(img):
        if isinstance(img, str):
            if not img or not os.path.exists(img):
                return None
            try:
                # cv2.imread 对中文/非 ASCII 路径在 Windows 上不可靠（ANSI 窄字符编码），
                # 用 np.fromfile 读取字节 + cv2.imdecode 解码，保证中文控件名模板可对比。
                data = np.fromfile(img, dtype=np.uint8)
                arr = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
                return arr if arr is not None else cv2.imread(img, cv2.IMREAD_GRAYSCALE)
            except Exception:
                return None
        try:
            return np.array(img.convert("L"))
        except Exception:
            return None

    def _p_hash(arr):
        if arr is None or arr.size == 0:
            return None
        resized = cv2.resize(arr, (32, 32), interpolation=cv2.INTER_AREA)
        dct = cv2.dct(np.float32(resized))
        low = dct[:8, :8]
        return (low > low.mean()).flatten()

    try:
        ga = _gray(a)
        gb = _gray(b)
        ha = _p_hash(ga)
        hb = _p_hash(gb)
        if ha is None or hb is None:
            return False
        # 整体明暗差异守卫：灰度均值差过大说明控件/状态明显不同（如纯色块场景
        # 下 pHash 的 DCT 低频会趋同造成误判），直接判为不相似
        mean_a = float(np.mean(ga))
        mean_b = float(np.mean(gb))
        if abs(mean_a - mean_b) > 20.0:
            return False
        return int(np.count_nonzero(ha != hb)) <= threshold
    except Exception:
        return False


def build_index() -> dict[str, str]:
    """构建（缓存）统一模板索引：{key: 绝对路径}。"""
    global _cache
    if _cache is None:
        index: dict[str, str] = {}
        if os.path.isdir(IMAGE_TEMPLATE_ROOT):
            _scan_index_files(index)
            _scan_png_dirs(index)
        _cache = index
    return _cache


def reload() -> None:
    """清空索引缓存，下次调用重新扫描磁盘。"""
    global _cache
    _cache = None


def get_template_path(key_or_path: str | None) -> str:
    """把模板引用（key / 相对路径 / 绝对路径）解析为真实文件路径，找不到返回空串。"""
    s = str(key_or_path or "").strip()
    if not s:
        return ""
    if os.path.isabs(s):
        return s if os.path.exists(s) else ""
    norm = s.replace("\\", "/")
    # 带 image_templates 前缀 → 相对项目根
    if norm.startswith("image_templates/"):
        p = os.path.join(PROJECT_ROOT, *norm.split("/"))
        return p if os.path.exists(p) else ""
    index = build_index()
    if norm in index:
        p = index[norm]
        return p if os.path.exists(p) else ""
    # 相对 image_templates 根
    p = os.path.join(IMAGE_TEMPLATE_ROOT, *norm.split("/"))
    if os.path.exists(p):
        return p
    # 相对项目根
    p2 = os.path.join(PROJECT_ROOT, *norm.split("/"))
    return p2 if os.path.exists(p2) else ""


def summary() -> dict[str, int]:
    """模板索引规模统计（用于状态展示/调试）。"""
    index = build_index()
    return {"total": len(index), "root": IMAGE_TEMPLATE_ROOT}


def resolve_fallback_template(step_definition: dict | None) -> str:
    """从步骤的 controls[].templateKey 自动解析模板兜底路径（相对项目根）。

    返回形如 "image_templates/recorder_captures/step_19_控件19.png"，可直接写入
    actionConfig.fallbackTemplate（执行器 resolve_fallback_template_path 按项目根拼接）。
    templateKey 为空、指向不存在文件、或为老式语义 ID（如 config_button）时返回空串。
    供编辑器保存 / Agent 生成流程时自动关联模板，把运行时自动接线提前到生成时落盘。
    """
    if not isinstance(step_definition, dict):
        return ""
    controls = step_definition.get("controls")
    if not isinstance(controls, list):
        return ""
    for control in controls:
        if not isinstance(control, dict):
            continue
        key = str(control.get("templateKey", "")).strip()
        if not key:
            continue
        path = get_template_path(key)  # 找不到返回空串
        if not path or not os.path.exists(path):
            continue
        rel = os.path.relpath(path, PROJECT_ROOT).replace("\\", "/")
        if not rel.startswith("image_templates/"):
            rel = "image_templates/" + rel
        return rel
    return ""


def auto_associate_fallback_templates(steps: list | None) -> int:
    """批量自动关联：为每个步骤补全 actionConfig.fallbackTemplate。

    仅当步骤未显式配置 fallbackTemplate、且其细分控件存在可解析的 templateKey 时写入，
    不覆盖用户已有配置（非破坏性）。返回实际写入的步骤数。
    """
    if not isinstance(steps, list):
        return 0
    count = 0
    for step in steps:
        if not isinstance(step, dict):
            continue
        action_config = step.get("actionConfig")
        if not isinstance(action_config, dict):
            action_config = {}
            step["actionConfig"] = action_config
        if str(action_config.get("fallbackTemplate", "")).strip():
            continue  # 已有显式配置，不覆盖
        rel = resolve_fallback_template(step)
        if not rel:
            continue
        action_config["fallbackTemplate"] = rel
        count += 1
    return count
