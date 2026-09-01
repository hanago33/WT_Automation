#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成发布包 / 全量包：
  增量发布包：python make_release.py
    只含相对上次同步点(wt_last_sync)的改动，内网日常更新用。
  全量快照包：python make_release.py --full
    含所有已跟踪 + 未跟踪(非忽略)的正式文件，全新内网机首次部署用。

内网机应用：解压后运行  python apply_release.py <内网仓库根目录>
           （或用 deploy_release.exe 一键解压+覆盖）
"""
import subprocess
import os
import sys
import shutil
import zipfile
import datetime

REPO = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                               text=True).strip()
SYNC_REF = "wt_last_sync"
OUT_DIR = os.path.join(REPO, "release_out")
# 运行时不需要、且可能很大的目录，打包时跳过
EXCLUDE_TOP_DIRS = {".zcode", "dist", "release_out", ".git", "build"}
# 同步工具自身不进发布包（避免把 ~5MB 的 exe 反复打包带过去）
# 注意：apply_release.py（仅 1.5KB）仍保留，供内网机使用
EXCLUDE_FILES = {"make_release.py", "make_release.exe", "apply_release.exe",
                 "deploy_release.py", "deploy_release.exe"}
FULL = "--full" in sys.argv


def git(*args):
    return subprocess.check_output(
        ["git", "-c", "core.quotePath=false", *args],
        cwd=REPO, text=True, encoding="utf-8").splitlines()


def top_dir(path: str) -> str:
    return path.split("/", 1)[0].split(os.sep, 1)[0]


def filter_paths(paths):
    return {p for p in paths
            if top_dir(p) not in EXCLUDE_TOP_DIRS
            and os.path.basename(p) not in EXCLUDE_FILES}


if FULL:
    # 全量：所有已跟踪文件 + 所有未跟踪(非忽略)文件
    changed = set(git("ls-files"))
    changed |= set(git("ls-files", "--others", "--exclude-standard"))
    deleted = set()
    pkg_prefix = "全量包"
else:
    changed = set()
    deleted = set()
    # 1) 已提交但未同步的部分（基于上次同步点）
    if subprocess.run(["git", "rev-parse", "--verify", SYNC_REF],
                      cwd=REPO, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode == 0:
        for p in git("diff", "--name-only", SYNC_REF, "HEAD"):
            if p:
                changed.add(p)
        for p in git("diff", "--name-only", "--diff-filter=D", SYNC_REF, "HEAD"):
            if p:
                deleted.add(p)
    # 2) 工作区相对 HEAD 的改动（含未提交修改 / 删除）
    for p in git("diff", "--name-only", "HEAD"):
        if p:
            changed.add(p)
    for p in git("diff", "--name-only", "--diff-filter=D", "HEAD"):
        if p:
            deleted.add(p)
    # 3) 新增的未跟踪文件（排除 .gitignore 忽略项）
    for p in git("ls-files", "--others", "--exclude-standard"):
        if p:
            changed.add(p)
    pkg_prefix = "发布包"

# 过滤：去掉运行期不需要的目录 + 同步工具本身 + 已删除的文件不复制
changed = filter_paths(changed)
changed -= deleted

if not changed and not deleted:
    print("没有需要打包的文件。")
    sys.exit(0)

short = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                cwd=REPO, text=True).strip()
ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
pkg_name = f"{pkg_prefix}_{ts}_{short}"
pkg_dir = os.path.join(OUT_DIR, pkg_name)
if os.path.exists(pkg_dir):
    shutil.rmtree(pkg_dir)
os.makedirs(pkg_dir)

# 复制文件（保持相对目录结构）
copied = 0
for rel in sorted(changed):
    src = os.path.join(REPO, rel)
    if not os.path.isfile(src):
        continue
    dst = os.path.join(pkg_dir, rel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    copied += 1

# 删除清单
with open(os.path.join(pkg_dir, "deleted.txt"), "w", encoding="utf-8") as f:
    for rel in sorted(deleted):
        f.write(rel + "\n")

# 清单.txt
head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO,
                               text=True).strip()
with open(os.path.join(pkg_dir, "清单.txt"), "w", encoding="utf-8") as f:
    f.write("WT_Automation " + ("全量快照包" if FULL else "发布包") + "\n")
    f.write(f"生成时间：{ts}\n")
    f.write(f"源提交：{head}\n")
    if not FULL:
        f.write(f"同步基准：{SYNC_REF}\n")
    f.write("=" * 40 + "\n")
    f.write(f"{'全量' if FULL else '新增/修改'}文件（{copied} 个）：\n")
    for rel in sorted(changed):
        f.write("  + " + rel + "\n")
    if deleted:
        f.write(f"\n需删除文件（{len(deleted)} 个）：\n")
        for rel in sorted(deleted):
            f.write("  - " + rel + "\n")
    f.write("\n内网机操作：解压后运行  python apply_release.py <内网仓库根目录>\n")

# 把应用脚本放进包里，方便内网机使用
apply_src = os.path.join(REPO, "apply_release.py")
if os.path.isfile(apply_src):
    shutil.copy2(apply_src, os.path.join(pkg_dir, "apply_release.py"))

# 打包
zip_path = os.path.join(OUT_DIR, pkg_name + ".zip")
if os.path.exists(zip_path):
    os.remove(zip_path)
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
    for root, _, files in os.walk(pkg_dir):
        for fn in files:
            full = os.path.join(root, fn)
            z.write(full, os.path.relpath(full, pkg_dir))

print(f"已生成：{zip_path}")
print(f"  文件数：{copied}，体积：{os.path.getsize(zip_path) / 1024 / 1024:.1f} MB")

# 更新同步基准（仅增量模式）
if not FULL:
    subprocess.run(["git", "tag", "-f", SYNC_REF, "HEAD"], cwd=REPO, check=True)
    print(f"已更新同步基准 {SYNC_REF} -> {short}")
