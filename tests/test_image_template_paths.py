# encoding: utf-8
"""图片模板索引的路径可移植性测试（本机绝对路径治理）。

背景：templates_index.json 随 git 分发到别的机器，写本机绝对路径（D:\\My_RF_Project\\...）
换机即失效，也违反 AGENTS.md「禁止把本机绝对路径写入被 git 跟踪的文件」。约定：
索引里只存相对项目根的 "/" 分隔路径，reader 侧按项目根还原为绝对路径。
"""
import json
import os
import sys

import pytest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import build_image_template_library as B  # noqa: E402
import image_template_index as I  # noqa: E402


# ── 路径互转工具 ──────────────────────────────────────────────────────────────

def test_repo_relative_path_inside_project():
    inside = os.path.join(PROJECT_DIR, "image_templates", "Icons", "确定.png")
    assert B.repo_relative_path(inside) == "image_templates/Icons/确定.png"


def test_repo_relative_path_normalizes_other_machine_absolute_path():
    """历史索引来自别的机器/盘符时按 image_templates/ 之后归一化。"""
    legacy = r"D:\My_RF_Project\WT_Automation\image_templates\WT_software_Images\screenshot_x.png"
    assert B.repo_relative_path(legacy) == "image_templates/WT_software_Images/screenshot_x.png"


def test_repo_relative_path_outside_project_returns_empty():
    assert B.repo_relative_path(r"C:\Windows\System32\notepad.exe") == ""
    assert B.repo_relative_path("") == ""


def test_repo_absolute_path_roundtrip():
    rel = "image_templates/Icons/确定.png"
    assert B.repo_absolute_path(rel) == os.path.join(PROJECT_DIR, "image_templates", "Icons", "确定.png")
    absolute = r"E:\somewhere\x.png"
    assert B.repo_absolute_path(absolute) == absolute
    assert B.repo_absolute_path("") == ""


# ── 写盘：历史绝对路径收敛为相对路径 ─────────────────────────────────────────

def test_rebuild_template_index_converts_legacy_absolute_paths():
    legacy = {
        "templates": [{
            "category": "Icons",
            "file_name": "确定",
            "image_path": r"D:\My_RF_Project\WT_Automation\image_templates\Icons\确定.png",
            "source_screenshot": r"D:\My_RF_Project\WT_Automation\image_templates\screenshot_1.png",
            "region": {"x": 1, "y": 2, "w": 3, "h": 4},
        }]
    }
    rebuilt = B.rebuild_template_index(os.path.join(PROJECT_DIR, "image_templates", "Icons"), legacy)
    record = rebuilt["templates"][0]
    assert record["image_path"] == "image_templates/Icons/确定.png"
    assert record["source_screenshot"] == "image_templates/screenshot_1.png"


# ── 读取：相对路径按项目根还原 ────────────────────────────────────────────────

def test_scan_index_files_resolves_relative_image_path(tmp_path, monkeypatch):
    root = tmp_path / "image_templates"
    (root / "Icons").mkdir(parents=True)
    png = root / "Icons" / "确定.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    (root / "Icons" / "templates_index.json").write_text(json.dumps({
        "templates": [{
            "category": "Icons",
            "file_name": "确定",
            "image_path": "image_templates/Icons/确定.png",
            "source_screenshot": "image_templates/screenshot_1.png",
        }]
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(I, "IMAGE_TEMPLATE_ROOT", str(root))
    monkeypatch.setattr(I, "PROJECT_ROOT", str(tmp_path))  # 相对路径按项目根解析
    index: dict[str, str] = {}
    I._scan_index_files(index)

    assert index["Icons/确定.png"] == str(png)
    assert all(os.path.isabs(path) for path in index.values())


# ── 规则锁：随仓库走的索引文件里不得出现本机绝对路径 ──────────────────────────

def test_tracked_template_indexes_have_no_machine_absolute_paths():
    index_files = []
    for dirpath, _dirnames, filenames in os.walk(os.path.join(PROJECT_DIR, "image_templates")):
        if "templates_index.json" in filenames:
            index_files.append(os.path.join(dirpath, "templates_index.json"))
    assert index_files, "未找到任何 templates_index.json"

    offenders = []
    for path in index_files:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        for template in payload.get("templates", []) or []:
            for key in ("image_path", "source_screenshot"):
                value = str(template.get(key, "")).strip()
                if len(value) > 1 and value[1] == ":":
                    offenders.append("{}:{}={}".format(os.path.basename(os.path.dirname(path)), key, value))
    assert offenders == [], "索引里仍有本机绝对路径：{}".format(offenders)
