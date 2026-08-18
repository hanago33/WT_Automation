# encoding: utf-8
"""存量录制截图批量归档到 auto_captured 体系（统一索引）。

背景：
- recorder_captures/*.png 是 pywinauto_recorder 伴随拾取时代自动截的模板，
  已能被 image_template_index 索引（recorder_captures/ 直接扫描）；
- auto_captured/<窗口>/<控件名>.png 是执行中自动采集（P0/P1）的规范目录，
  按窗口分目录、可重复覆盖更新。

本脚本把存量 recorder_captures 截图**复制**归档到
    image_templates/auto_captured/recorder_legacy/<同名>.png
使两类来源归入同一维护目录；同时保留 recorder_captures 原文件不删除
（旧流程文件里的 templateKey 引用继续有效，非破坏性）。

安全策略：
- 目标已存在且图片一致（pHash 对比）→ 跳过（视为已导入）；
- 目标已存在但不一致 → 不覆盖、仅报告冲突（防止破坏已有可用模板）；
- 支持 --dry-run 预览，不实际写入。

用法：
    python build_auto_capture_index.py            # 实际执行导入
    python build_auto_capture_index.py --dry-run  # 仅预览将导入的清单
"""
from __future__ import annotations

import argparse
import os
import shutil

import image_template_index

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
IMAGE_TEMPLATE_ROOT = os.path.join(PROJECT_ROOT, "image_templates")
RECORDER_DIR = os.path.join(IMAGE_TEMPLATE_ROOT, "recorder_captures")
LEGACY_SUBDIR = os.path.join(IMAGE_TEMPLATE_ROOT, "auto_captured", "recorder_legacy")


def import_recorder_captures(dry_run: bool = False) -> dict:
    """把 recorder_captures/*.png 归档到 auto_captured/recorder_legacy/。

    返回统计：{"imported": n, "skipped_same": n, "conflict": n, "missing": n}。
    """
    stats = {"imported": 0, "skipped_same": 0, "conflict": 0, "missing": 0}
    if not os.path.isdir(RECORDER_DIR):
        return stats

    os.makedirs(LEGACY_SUBDIR, exist_ok=True)
    for fn in sorted(os.listdir(RECORDER_DIR)):
        if not fn.lower().endswith(".png"):
            continue
        src = os.path.join(RECORDER_DIR, fn)
        dst = os.path.join(LEGACY_SUBDIR, fn)
        if os.path.exists(dst):
            if image_template_index.images_are_similar(src, dst):
                stats["skipped_same"] += 1
            else:
                stats["conflict"] += 1
                print(f"[冲突] {fn}: 目标已存在但内容不一致，跳过（避免覆盖已有模板）")
            continue
        if dry_run:
            print(f"[预览] 将导入: {fn}")
            stats["missing"] += 1
            continue
        try:
            shutil.copy2(src, dst)
            print(f"[导入] {fn} -> auto_captured/recorder_legacy/{fn}")
            stats["imported"] += 1
        except OSError as exc:
            print(f"[失败] {fn}: {exc}")
            stats["conflict"] += 1

    if not dry_run:
        image_template_index.reload()  # 刷新统一索引，新归档立即可被引用
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="存量录制截图批量归档到 auto_captured 体系")
    parser.add_argument("--dry-run", action="store_true", help="仅预览将导入的清单，不实际写入")
    args = parser.parse_args()

    stats = import_recorder_captures(dry_run=args.dry_run)
    print("-" * 56)
    print(f"汇总: 导入={stats['imported']}  已存在且一致(跳过)={stats['skipped_same']}  "
          f"冲突(跳过)={stats['conflict']}  待导入(预览)={stats['missing']}")
    index = image_template_index.build_index()
    print(f"统一索引模板总数: {len(index)}")


if __name__ == "__main__":
    main()
