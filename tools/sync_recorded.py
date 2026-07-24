"""将 Pywinauto recorder 默认输出目录里新录制的脚本同步到项目。

Pywinauto recorder 的保存目录在其库内硬编码为：
    Path.home() / 'Pywinauto recorder'
（见 site-packages/pywinauto_recorder/recorder.py 的 _write_in_file），
无法通过配置修改，因此采用“每次录制后同步一次”的方式归集到项目。

目标目录： <项目根>/samples/recorder_scripts/  （项目约定的录制脚本收录位置）

增量策略（关键）：
    使用清单文件 tools/.sync_state.json 记录“已经处理过的源文件名”。
    一旦某个 `recorded *.py` 被同步过，就会登记进清单；即使之后你在项目里
    把它改名或删除，也不会被再次搬回。这样解决了“老文件反复搬回 / 改名后重复
    复制”的问题。

用法：
    python tools/sync_recorded.py            # 增量：仅最新一个（默认，最常用）
    python tools/sync_recorded.py --all      # 增量：所有尚未登记的新文件
    python tools/sync_recorded.py --rebuild  # 把当前源目录全部登记为基线（不搬运）
"""
import argparse
import glob
import json
import os
import shutil

# Pywinauto recorder 默认输出目录（库硬编码，勿改）
SOURCE_DIR = os.path.join(os.path.expanduser("~"), "Pywinauto recorder")

# 项目约定的录制脚本收录目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST_DIR = os.path.join(PROJECT_ROOT, "samples", "recorder_scripts")

# 增量清单：记录已处理过的源文件名
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".sync_state.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".last_sync.log")


def _load_state():
    if not os.path.exists(STATE_FILE):
        return {"synced": []}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as fp:
            data = json.load(fp)
        if isinstance(data, dict) and isinstance(data.get("synced"), list):
            return data
    except Exception:
        pass
    return {"synced": []}


def _save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as fp:
            json.dump(state, fp, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _list_source_files():
    """返回源目录里的 recorded 脚本，按修改时间从旧到新排序。"""
    files = glob.glob(os.path.join(SOURCE_DIR, "recorded *.py"))
    files.sort(key=lambda p: os.path.getmtime(p))
    return files


def sync(mode="latest"):
    """执行同步。

    mode:
        "latest" —— 仅同步最近一次新录制（默认）。
        "all"    —— 同步所有“尚未登记且项目里不存在”的新文件。
        "rebuild"—— 把源目录现有文件全部登记为基线，不搬运任何文件。

    返回结构化结果 dict，供 GUI 直接调用；同时打印并写入日志。
    """
    result = {
        "source": SOURCE_DIR,
        "dest": DEST_DIR,
        "mode": mode,
        "total": 0,
        "copied": [],
        "skipped_existing": [],
        "skipped_synced": [],
        "message": "",
        "ok": True,
    }

    if not os.path.isdir(SOURCE_DIR):
        result["ok"] = False
        result["message"] = "未找到 recorder 目录： " + SOURCE_DIR
        _finish(result)
        return result

    os.makedirs(DEST_DIR, exist_ok=True)
    state = _load_state()
    synced = set(state.get("synced", []))

    files = _list_source_files()
    result["total"] = len(files)

    if mode == "rebuild":
        for f in files:
            synced.add(os.path.basename(f))
        state["synced"] = sorted(synced)
        _save_state(state)
        result["message"] = "已把源目录现有 %d 个文件登记为基线（未搬运）。之后只同步新录制的文件。" % len(files)
        _finish(result)
        return result

    # 增量候选：项目里不存在、且清单里没登记过的
    candidates = []
    for f in files:
        name = os.path.basename(f)
        target = os.path.join(DEST_DIR, name)
        if os.path.exists(target):
            result["skipped_existing"].append(name)
            synced.add(name)  # 已在项目中，纳入清单以便跟踪
            continue
        if name in synced:
            result["skipped_synced"].append(name)  # 之前已同步（可能被改名/删除），不再搬回
            continue
        candidates.append((f, name, target))

    if mode == "latest" and candidates:
        # 只取修改时间最新的那一个（candidates 已按旧->新排序）
        candidates = [candidates[-1]]

    for f, name, target in candidates:
        shutil.copy2(f, target)
        synced.add(name)
        result["copied"].append(name)

    state["synced"] = sorted(synced)
    _save_state(state)

    if result["copied"]:
        result["message"] = "已同步 %d 个新录制脚本到项目。" % len(result["copied"])
    else:
        result["message"] = "没有新文件需要同步（最新录制已在项目中或已登记）。"

    _finish(result)
    return result


def _finish(result):
    lines = []
    lines.append("源目录： %s" % result["source"])
    lines.append("目标目录： %s" % result["dest"])
    lines.append("模式： %s" % result["mode"])
    lines.append("源目录录制文件总数： %d" % result["total"])
    if result["copied"]:
        lines.append("已同步到项目：")
        for n in result["copied"]:
            lines.append("  + " + n)
    lines.append(result["message"])
    if result["skipped_existing"]:
        lines.append("（项目已存在跳过 %d 个）" % len(result["skipped_existing"]))
    if result["skipped_synced"]:
        lines.append("（已登记跳过 %d 个，不再搬回）" % len(result["skipped_synced"]))

    text = "\n".join(lines)
    result["log_text"] = text
    print(text)
    try:
        with open(LOG_FILE, "w", encoding="utf-8") as lf:
            lf.write(text + "\n")
    except Exception:
        pass


def _parse_args():
    parser = argparse.ArgumentParser(description="同步 Pywinauto recorder 新录制脚本到项目（增量）")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="同步所有尚未登记的新文件")
    group.add_argument("--rebuild", action="store_true", help="把源目录现有文件登记为基线，不搬运")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    if args.rebuild:
        sync(mode="rebuild")
    elif args.all:
        sync(mode="all")
    else:
        sync(mode="latest")
