#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内网机应用发布包：把 zip 解压后的文件按相对路径覆盖到目标仓库，并处理删除。

用法（解压发布包后，在发布包目录里运行）：
    python apply_release.py D:/WT_Automation

参数：
    目标仓库根目录（不传则使用当前目录）。
会自动跳过 清单.txt / deleted.txt / apply_release.py 自身。
"""
import os
import sys
import shutil

pkg_dir = os.path.dirname(os.path.abspath(__file__))
target = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()

SKIP = {"清单.txt", "deleted.txt", "apply_release.py"}

copied = 0
for root, _, files in os.walk(pkg_dir):
    for fn in files:
        if fn in SKIP:
            continue
        full = os.path.join(root, fn)
        rel = os.path.relpath(full, pkg_dir)
        dst = os.path.join(target, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(full, dst)
        copied += 1

deleted = 0
del_path = os.path.join(pkg_dir, "deleted.txt")
if os.path.isfile(del_path):
    with open(del_path, encoding="utf-8") as f:
        for line in f:
            rel = line.strip()
            if not rel:
                continue
            p = os.path.join(target, rel)
            if os.path.isfile(p):
                os.remove(p)
                deleted += 1

print(f"应用完成：覆盖 {copied} 个文件，删除 {deleted} 个文件 -> {target}")
