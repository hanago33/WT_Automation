#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内网机一键部署（解压 + 应用）为单个 exe。

用法：
  1) 双击运行（交互模式）：按提示输入 zip 路径与内网仓库根目录。
  2) 命令行：  deploy_release.exe <发布包.zip> [内网仓库根目录]

找不到 zip 时，会在脚本所在目录 / 当前目录自动寻找最新的 发布包_*.zip。
"""
import os
import sys
import glob
import shutil
import zipfile
import tempfile


def find_zip(hint):
    if hint and os.path.isfile(hint):
        return hint
    here = os.path.dirname(os.path.abspath(__file__))
    cands = glob.glob(os.path.join(here, "发布包_*.zip"))
    cands += glob.glob(os.path.join(os.getcwd(), "发布包_*.zip"))
    if not cands:
        return None
    return max(cands, key=os.path.getmtime)


def main():
    argv = sys.argv[1:]
    zip_path = find_zip(argv[0] if argv else None)
    if not zip_path:
        zip_path = input("未找到发布包，请直接输入 zip 完整路径：\n> ").strip().strip('"')

    if not zip_path or not os.path.isfile(zip_path):
        print("错误：找不到发布包 zip 文件。")
        return 1

    target = argv[1] if len(argv) > 1 else ""
    if not target:
        target = input("请输入内网仓库根目录（如 D:\\WT_Automation）：\n> ").strip().strip('"')

    if not os.path.isdir(target):
        print(f"错误：目标目录不存在：{target}")
        return 1

    SKIP = {"清单.txt", "deleted.txt", "apply_release.py", "apply_release.exe",
            "make_release.py", "make_release.exe", "deploy_release.py"}

    # 解压到临时目录
    tmp = tempfile.mkdtemp(prefix="wt_deploy_")
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(tmp)

    copied = 0
    for root, _, files in os.walk(tmp):
        for fn in files:
            if fn in SKIP:
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, tmp)
            dst = os.path.join(target, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(full, dst)
            copied += 1

    # 处理删除清单
    deleted = 0
    del_path = os.path.join(tmp, "deleted.txt")
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

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"部署完成：覆盖 {copied} 个文件，删除 {deleted} 个文件")
    print(f"  来源：{os.path.basename(zip_path)}")
    print(f"  目标：{target}")
    return 0


if __name__ == "__main__":
    rc = main()
    if len(sys.argv) <= 1:
        input("按回车退出...")
    sys.exit(rc or 0)
