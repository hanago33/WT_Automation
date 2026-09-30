# encoding: utf-8
"""主目录 ↔ 主干 同步盘点（AGENTS.md「单向同步」流程的固化检查）。

背景：主目录 WT_Automation 是「唯一可信源」，只允许 `主干 → 主目录` 单向同步。
2026-09-30 的三处工作区脏改动，就是「人工把主目录文件抄回主干」造成的。本脚本把
同步前必须做的盘点固化成一条命令：

  1. 三分类：M(需覆盖) / A(需新增) / D(主目录独有，需人工判断是否删除)；
  2. 对每个 M 项，判断主目录版本是否为主干**历史提交过**的版本：
       是  → 覆盖零损失（纯追赶主干）；
       否  → 存在本地独有改动，**必须先人工审**再覆盖；
  3. D 项非空即视为漂移：主目录有主干没有的内容，先查来源再同步。

用法（在主干仓库里执行；--maindir 指向主目录工作区）：

    python tools/check_maindir_sync.py --maindir D:\\My_RF_Project\\WT_Automation
    python tools/check_maindir_sync.py --maindir ... --ref origin/main --report sync.txt

退出码：0=已一致（可跳过同步）；1=存在漂移；2=环境/参数错误。
"""
import argparse
import os
import subprocess
import sys

DEFAULT_MAINDIR = r"D:\My_RF_Project\WT_Automation"
DEFAULT_REF = "origin/main"


class SyncCheckError(Exception):
    """环境或参数不满足（如不是 git 仓库、ref 不存在）。"""


def _git(repo, args, binary=False):
    """执行 git 并返回 stdout（失败抛 SyncCheckError）。"""
    try:
        completed = subprocess.run(
            ["git"] + list(args), cwd=repo,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise SyncCheckError("无法执行 git：{}".format(exc))
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip()
        raise SyncCheckError("git {} 失败：{}".format(" ".join(args), detail or completed.returncode))
    return completed.stdout if binary else completed.stdout.decode("utf-8", "replace")


def list_tree(repo, ref):
    """返回 {路径: blob_sha}；用 -z 分隔，非 ASCII 路径不做引号转义。"""
    raw = _git(repo, ["ls-tree", "-r", "-z", ref], binary=True)
    tree = {}
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        tree[path.decode("utf-8", "replace")] = meta.split()[2].decode("ascii")
    return tree


def classify(maindir_tree, trunk_tree):
    """三分类（纯函数，便于单测）。返回 (modified, added, deleted) 三个已排序列表。

    modified：两边都有但内容不同（需覆盖）
    added   ：主干有、主目录没有（需新增）
    deleted ：主目录有、主干没有（主目录独有或主干已删，需人工判断）
    """
    modified = sorted(p for p in set(maindir_tree) & set(trunk_tree)
                      if maindir_tree[p] != trunk_tree[p])
    added = sorted(set(trunk_tree) - set(maindir_tree))
    deleted = sorted(set(maindir_tree) - set(trunk_tree))
    return modified, added, deleted


def blob_in_history(repo, blob):
    """该 blob 是否出现在主干历史中（出现 → 主目录版本只是主干旧版，覆盖零损失）。"""
    out = _git(repo, ["log", "--all", "--format=%h %s", "--find-object=" + blob, "-1"])
    return out.strip()


def inspect(trunk_repo, maindir_repo, ref):
    """盘点主目录与主干（ref）的差异，返回结果字典。"""
    trunk_tree = list_tree(trunk_repo, ref)
    maindir_tree = list_tree(maindir_repo, "HEAD")
    modified, added, deleted = classify(maindir_tree, trunk_tree)

    historical, local_only = [], []
    for path in modified:
        (historical if blob_in_history(trunk_repo, maindir_tree[path]) else local_only).append(path)

    return {
        "trunk_count": len(trunk_tree),
        "maindir_count": len(maindir_tree),
        "modified": modified,
        "added": added,
        "deleted": deleted,
        "historical": historical,
        "local_only": local_only,
    }


def format_report(result, ref=DEFAULT_REF, maindir=DEFAULT_MAINDIR):
    """把结果字典渲染成可落盘的文本报告。"""
    lines = [
        "主目录 ↔ 主干 同步盘点",
        "  主目录: {}  ({} 个跟踪文件)".format(maindir, result["maindir_count"]),
        "  主干  : {}  ({} 个跟踪文件)".format(ref, result["trunk_count"]),
        "",
        "M 需覆盖: {}    A 需新增: {}    D 主目录独有: {}".format(
            len(result["modified"]), len(result["added"]), len(result["deleted"])),
        "  M 中「主干历史版本」（覆盖零损失）: {}    本地变体（必须人工审）: {}".format(
            len(result["historical"]), len(result["local_only"])),
    ]
    if result["local_only"]:
        lines += ["", "!! 以下文件在主目录有主干从未提交过的内容，覆盖前必须人工确认：" ]
        lines += ["  [本地变体] {}".format(p) for p in result["local_only"]]
    if result["deleted"]:
        lines += ["", "!! 以下文件主目录有、主干没有（删前先查来源）："]
        lines += ["  [主目录独有] {}".format(p) for p in result["deleted"]]
    for title, key in (("M 需覆盖", "modified"), ("A 需新增", "added")):
        if result[key]:
            lines += ["", "{}（{}）：".format(title, len(result[key]))]
            lines += ["  {}".format(p) for p in result[key]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description="盘点主目录与主干的同步差异（单向同步前置检查）")
    parser.add_argument("--maindir", default=DEFAULT_MAINDIR, help="主目录工作区路径（默认 %(default)s）")
    parser.add_argument("--ref", default=DEFAULT_REF, help="主干参照（默认 %(default)s）")
    parser.add_argument("--report", default="", help="把报告写入该文件（UTF-8）")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass

    maindir = os.path.abspath(args.maindir)
    if not os.path.isdir(maindir):
        print("[错误] 主目录不存在：{}".format(maindir))
        return 2

    try:
        result = inspect(trunk_repo=os.getcwd(), maindir_repo=maindir, ref=args.ref)
    except SyncCheckError as exc:
        print("[错误] {}".format(exc))
        return 2

    report = format_report(result, ref=args.ref, maindir=maindir)
    print(report)
    if args.report:
        try:
            with open(args.report, "w", encoding="utf-8") as handle:
                handle.write(report)
            print("报告已写入：{}".format(args.report))
        except OSError as exc:
            print("[警告] 报告写入失败：{}".format(exc))

    drift = bool(result["modified"] or result["added"] or result["deleted"])
    if drift:
        if result["local_only"]:
            print("结论：存在漂移，且 {} 个 M 项是本地变体——覆盖前必须人工审。".format(
                len(result["local_only"])))
        else:
            print("结论：存在漂移，可安全执行整文件同步（覆盖 + 新增），无主目录独有内容。")
        return 1
    print("结论：主目录与主干已一致，无需同步。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
