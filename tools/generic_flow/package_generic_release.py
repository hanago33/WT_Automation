# -*- coding: utf-8 -*-
"""把 generic_flow/ 通用化副本打成内网发布包（zip + 清单.txt）。

格式与项目 make_release.py 一致：zip 内保持相对仓库根目录结构
（tools/generic_flow/...），并附带 清单.txt 与 apply_release.py，
目标机可用 deploy_release.exe 一键解压覆盖到内网仓库根。

运行：
    python tools/generic_flow/package_generic_release.py
产物：release_out/发布包_generic_flow_<时间戳>.zip
"""
import datetime
import io
import os
import shutil
import zipfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SRC_SUBDIR = os.path.join("tools", "generic_flow")          # 相对仓库根的子目录
SRC_DIR = os.path.join(REPO, SRC_SUBDIR)
OUT_DIR = os.path.join(REPO, "release_out")

# 副本目录里不需要进包的文件
SKIP_FILES = {"make_generic_copy.py", "make_generic_automation.py",
              "package_generic_release.py", "__pycache__"}


def iter_files():
    for root, dirs, files in os.walk(SRC_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_FILES]
        for fn in files:
            if fn in SKIP_FILES:
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, REPO)
            yield rel.replace(os.sep, "/")


def main():
    files = sorted(iter_files())
    if not files:
        print("没有可打包的文件。")
        return
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    pkg_name = "发布包_generic_flow_" + ts
    pkg_dir = os.path.join(OUT_DIR, pkg_name)
    if os.path.exists(pkg_dir):
        shutil.rmtree(pkg_dir)
    os.makedirs(pkg_dir, exist_ok=True)

    copied = 0
    for rel in files:
        dst = os.path.join(pkg_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(REPO, rel), dst)
        copied += 1

    # 清单.txt
    with io.open(os.path.join(pkg_dir, "清单.txt"), "w", encoding="utf-8") as f:
        f.write("WT_Automation 通用化副本发布包（去 WT/MUP 绑定）\n")
        f.write("生成时间：%s\n" % ts)
        f.write("=" * 40 + "\n")
        f.write("新增/修改文件（%d 个）：\n" % copied)
        for rel in files:
            f.write("  + " + rel + "\n")
        f.write("\n目标机操作：解压后运行  python apply_release.py <内网仓库根目录>\n")
        f.write("  或双击 deploy_release.exe 一键解压+覆盖。\n")
        f.write("\n注意：本包仅含通用化代码，目标机需已部署完整运行版环境\n")
        f.write("（含 wt_flow_editor_utils / wt_action_schema / 第三方依赖）。\n")
        f.write("控件库(control_maps/)需针对目标软件重新采集。\n")

    # 附带应用脚本
    apply_src = os.path.join(REPO, "apply_release.py")
    if os.path.isfile(apply_src):
        shutil.copy2(apply_src, os.path.join(pkg_dir, "apply_release.py"))

    zip_path = os.path.join(OUT_DIR, pkg_name + ".zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, fnames in os.walk(pkg_dir):
            for fn in fnames:
                full = os.path.join(root, fn)
                z.write(full, os.path.relpath(full, pkg_dir))

    print("已生成：%s" % zip_path)
    print("  文件数：%d，体积：%.1f MB" % (copied, os.path.getsize(zip_path) / 1024 / 1024))


if __name__ == "__main__":
    main()
