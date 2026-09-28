# encoding: utf-8
"""历史流程控件来源标记回填工具。

给 flow_packages 下已存在的流程定义中，未带 sourceInfo 的控件补充来源标记，
使「控件来源标记」功能对历史数据同样生效。

推断规则（依据控件 role / inspectData.howFound / notes）：
  - role 含「来自控件库扫描」            -> origin=control_library
  - role 含「来自标准控件库」/ howFound=standard_catalog -> origin=standard_catalog
  - role 含「来自锚点」/「锚点」        -> origin=anchor_library
  - role 含「录制」/「录像」            -> origin=recorder
  - 其余（含空 role 的特殊角色）         -> origin=manual

libraryControlId 取控件自身 id；inspectData.automationId 作为补充可追溯标识。

用法：
  python tools/backfill_control_source_info.py            # 预览（dry-run），不写文件
  python tools/backfill_control_source_info.py --apply    # 实际写入
  python tools/backfill_control_source_info.py --file flow_packages/flow_definition_导入元素.json --apply  # 仅指定文件
"""
import json
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FLOW_DIR = os.path.join(BASE_DIR, "flow_packages")


def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _infer_origin(role, how_found, notes):
    role = role or ""
    how_found = (how_found or "").lower()
    notes = notes or ""
    if "来自控件库扫描" in role or "控件库" in role:
        return "control_library"
    if "来自标准控件库" in role or "standard_catalog" in how_found:
        return "standard_catalog"
    if "锚点" in role or "anchor" in how_found:
        return "anchor_library"
    if "录制" in role or "录像" in role or "recorder" in how_found:
        return "recorder"
    return "manual"


def _build_source_info(ctrl):
    inspect_data = ctrl.get("inspectData", {}) if isinstance(ctrl.get("inspectData"), dict) else {}
    origin = _infer_origin(ctrl.get("role", ""), inspect_data.get("howFound", ""), ctrl.get("notes", ""))
    cid = str(ctrl.get("id", "") or "").strip()
    aid = str(inspect_data.get("automationId", "") or "").strip()
    return {
        "origin": origin,
        "libraryControlId": cid,
        "libraryFileName": "",
        "importedBy": "历史数据回填",
        "importedAt": "",
        "edited": False,
        "editedAt": "",
        "sourceDeleted": False,
        "sourceDeletedAt": "",
        "_backfilledAutomationId": aid,
    }


def process_file(fpath, apply=False, dry_run_stats=None):
    if dry_run_stats is None:
        dry_run_stats = {"scanned": 0, "filled": 0}
    try:
        with open(fpath, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        print(f"  [跳过] 无法解析 {os.path.basename(fpath)}: {exc}")
        return dry_run_stats
    if not isinstance(payload, dict):
        return dry_run_stats
    changed = False
    for step in payload.get("steps", []) or []:
        if not isinstance(step, dict):
            continue
        for ctrl in step.get("controls", []) or []:
            if not isinstance(ctrl, dict):
                continue
            dry_run_stats["scanned"] += 1
            src = ctrl.get("sourceInfo")
            if isinstance(src, dict) and src:
                continue  # 已有来源标记，跳过
            if not apply:
                ctrl["sourceInfo"] = _build_source_info(ctrl)
                dry_run_stats["filled"] += 1
                changed = True
                continue
            ctrl["sourceInfo"] = _build_source_info(ctrl)
            dry_run_stats["filled"] += 1
            changed = True
    if apply and changed:
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"  [写入] {os.path.basename(fpath)}")
        except Exception as exc:
            print(f"  [失败] 写入 {os.path.basename(fpath)}: {exc}")
    return dry_run_stats


def main():
    apply = "--apply" in sys.argv
    only_file = None
    for i, arg in enumerate(sys.argv):
        if arg == "--file" and i + 1 < len(sys.argv):
            only_file = sys.argv[i + 1]
    targets = []
    if only_file:
        fpath = os.path.join(BASE_DIR, only_file) if not os.path.isabs(only_file) else only_file
        if os.path.isfile(fpath):
            targets.append(fpath)
        else:
            print(f"文件不存在: {fpath}")
            return
    else:
        if not os.path.isdir(FLOW_DIR):
            print(f"flow_packages 目录不存在: {FLOW_DIR}")
            return
        for fname in sorted(os.listdir(FLOW_DIR)):
            if fname.startswith("flow_definition_") and fname.endswith(".json"):
                targets.append(os.path.join(FLOW_DIR, fname))
    print(f"模式: {'APPLY（实际写入）' if apply else 'DRY-RUN（预览）'}")
    print(f"扫描文件数: {len(targets)}")
    stats = {"scanned": 0, "filled": 0}
    for fpath in targets:
        process_file(fpath, apply=apply, dry_run_stats=stats)
    print(f"\n统计: 控件总数={stats['scanned']}，待补充/已补充来源标记={stats['filled']}")
    if not apply:
        print("\n以上为预览结果。确认无误后加 --apply 实际写入。")


if __name__ == "__main__":
    main()
