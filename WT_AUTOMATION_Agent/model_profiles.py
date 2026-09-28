# encoding: utf-8
"""多模型配置档案管理。

支持保存 / 加载 / 删除 / 切换多套大模型配置
（Base URL / API Key / 模型名 / 高级参数），实现"一套配置一套模型"的灵活切换。

存储文件: WT_AUTOMATION_Agent/model_profiles.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROFILES_FILE = Path(__file__).resolve().parent / "model_profiles.json"
LEGACY_CONFIG_FILE = Path(__file__).resolve().parent / "_gui_config.json"

# 一个模型配置档案包含的字段
PROFILE_FIELDS = (
    "base_url", "api_key", "model", "timeout",
    "max_retries", "retry_backoff", "retry_codes",
)


def _load() -> dict[str, Any]:
    if PROFILES_FILE.exists():
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "profiles" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"default": None, "profiles": {}}


def _save(data: dict[str, Any]) -> None:
    try:
        with open(PROFILES_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _normalize(raw: dict[str, Any]) -> dict[str, Any]:
    cfg = {k: raw.get(k, "") for k in PROFILE_FIELDS}
    try:
        cfg["timeout"] = int(cfg["timeout"] or 120)
    except (TypeError, ValueError):
        cfg["timeout"] = 120
    try:
        cfg["max_retries"] = int(cfg["max_retries"] or 3)
    except (TypeError, ValueError):
        cfg["max_retries"] = 3
    try:
        cfg["retry_backoff"] = float(cfg["retry_backoff"] or 2.0)
    except (TypeError, ValueError):
        cfg["retry_backoff"] = 2.0
    return cfg


def list_profiles() -> dict[str, Any]:
    """返回 {default, profiles:{name:cfg}}。"""
    data = _load()
    profiles = {name: _normalize(cfg) for name, cfg in data.get("profiles", {}).items()}
    default = data.get("default")
    if default not in profiles:
        default = next(iter(profiles), None)
    return {"default": default, "profiles": profiles}


def get_profile(name: str) -> dict[str, Any] | None:
    data = _load()
    cfg = data.get("profiles", {}).get(name)
    return _normalize(cfg) if cfg else None


def save_profile(name: str, raw: dict[str, Any]) -> bool:
    """保存（新增或覆盖）一个模型配置档案。"""
    name = (name or "").strip()
    if not name:
        return False
    data = _load()
    data.setdefault("profiles", {})[name] = _normalize(raw)
    if not data.get("default"):
        data["default"] = name
    _save(data)
    return True


def delete_profile(name: str) -> bool:
    data = _load()
    profiles = data.get("profiles", {})
    if name not in profiles:
        return False
    del profiles[name]
    if data.get("default") == name:
        data["default"] = next(iter(profiles), None)
    _save(data)
    return True


def set_default(name: str) -> bool:
    data = _load()
    if name not in data.get("profiles", {}):
        return False
    data["default"] = name
    _save(data)
    return True


def get_default() -> dict[str, Any] | None:
    info = list_profiles()
    if info["default"]:
        return get_profile(info["default"])
    return None


def migrate_from_legacy() -> bool:
    """若还没有任何档案，则把旧的 _gui_config.json 导入为「默认配置」。"""
    data = _load()
    if data.get("profiles"):
        return False
    if not LEGACY_CONFIG_FILE.exists():
        return False
    try:
        with open(LEGACY_CONFIG_FILE, "r", encoding="utf-8") as f:
            legacy = json.load(f)
    except (json.JSONDecodeError, OSError):
        return False
    if not legacy.get("base_url") and not legacy.get("api_key"):
        return False
    save_profile("默认配置", legacy)
    return True
