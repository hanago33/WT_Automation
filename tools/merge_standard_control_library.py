# encoding: utf-8
"""合并 / 去重 / 规范化 WT 控件库，生成标准控件目录 standard_control_catalog.json。

设计目标（对应需求：标准控件库 + 信息匹配）：
  - 把 control_maps/ 下所有实时快照（*_control_map.json）与旧库（library_*.json）
    合并成一个按 (窗口标题, 框架) 分组的标准目录。
  - 去重规则：automation_id 优先；否则 (name+className+controlType)；否则 (name+controlType)。
  - 权威度：high=有 automation_id；medium=有 name+className；low=仅 name；unknown=无标识。
  - 输出 standard_catalog_mismatch_report.json：标出 authority=low/unknown 的控件，
    即“收集信息与软件按钮对不上 / 不可靠”的项，供软件实时抓取快照后校正。

用法:
  python tools/merge_standard_control_library.py
  python tools/merge_standard_control_library.py --input-dir control_maps --output control_maps/standard_control_catalog.json
"""
import argparse
import json
import os
import glob
from collections import defaultdict
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(BASE)
DEFAULT_INPUT = os.path.join(REPO, "control_maps")
DEFAULT_OUT = os.path.join(DEFAULT_INPUT, "standard_control_catalog.json")
DEFAULT_REPORT = os.path.join(DEFAULT_INPUT, "standard_catalog_mismatch_report.json")

AUTHORITY_RANK = {"high": 3, "medium": 2, "low": 1, "unknown": 0}


def load_all(input_dir):
    recs = []
    patterns = ("*_control_map.json", "library_*.json")
    files = []
    for pat in patterns:
        files.extend(sorted(glob.glob(os.path.join(input_dir, pat))))
    for fp in files:
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        tw = data.get("targetWindow", {}) or {}
        fwin = str(tw.get("title", "")).strip()
        ffw = str(tw.get("frameworkId", "")).strip()
        for c in data.get("controlDefinitions", []) or []:
            rec = normalize_control(c, fwin, ffw, os.path.basename(fp))
            if rec:
                recs.append(rec)
    return recs


def normalize_control(c, fwin, ffw, src):
    ins = c.get("inspectData", {}) or {}
    name = str(c.get("name", "") or ins.get("name", "")).strip()
    automation_id = ""
    if str(c.get("targetMethod", "")).strip() == "automation_id":
        automation_id = str(c.get("targetValue", "")).strip()
    if not automation_id:
        automation_id = str(ins.get("automationId", "")).strip()
    window_title = str(c.get("windowTitle", "") or fwin).strip()
    framework_id = str(ins.get("frameworkId", "") or c.get("frameworkId", "") or ffw).strip()
    control_type = str(c.get("controlType", "") or ins.get("controlType", "")).strip()
    class_name = str(ins.get("className", "") or c.get("className", "")).strip()
    target_method = str(c.get("targetMethod", "")).strip()
    target_value = str(c.get("targetValue", "")).strip()
    return {
        "name": name,
        "automationId": automation_id,
        "windowTitle": window_title,
        "frameworkId": framework_id,
        "controlType": control_type,
        "className": class_name,
        "targetMethod": target_method,
        "targetValue": target_value,
        "source": src,
    }


def dedupe_key(rec):
    if rec["automationId"]:
        return ("aid", rec["automationId"].lower())
    if rec["name"] and rec["className"]:
        return ("nc", rec["name"].lower(), rec["className"].lower(), rec["controlType"].lower())
    if rec["name"]:
        return ("n", rec["name"].lower(), rec["controlType"].lower())
    return None


def authority(rec):
    if rec["automationId"]:
        return "high"
    if rec["name"] and rec["className"]:
        return "medium"
    if rec["name"]:
        return "low"
    return "unknown"


def merge(input_dir):
    recs = load_all(input_dir)
    groups = defaultdict(lambda: {"controls": {}})
    for rec in recs:
        gkey = (rec["windowTitle"], rec["frameworkId"])
        grp = groups[gkey]
        key = dedupe_key(rec)
        if key is None:
            # 无标识，记入低价值列表，但仍按 name 模糊归档以便报告
            key = ("none", rec["name"].lower() or "unnamed", len(grp["controls"]))
            rec["_no_identity"] = True
        auth = authority(rec)
        bucket = grp["controls"]
        if key not in bucket:
            bucket[key] = {
                "name": rec["name"],
                "controlType": rec["controlType"],
                "className": rec["className"],
                "frameworkId": rec["frameworkId"],
                "targetMethod": rec["targetMethod"],
                "targetValue": rec["targetValue"],
                "authority": auth,
                "occurrences": 0,
                "sources": set(),
                "needsReview": auth in ("low", "unknown"),
            }
        item = bucket[key]
        item["occurrences"] += 1
        item["sources"].add(rec["source"])
        # 提升权威度：保留更高 authority 的 targetMethod/targetValue
        if AUTHORITY_RANK[auth] > AUTHORITY_RANK[item["authority"]] or (
            item["targetMethod"] in ("", "name") and rec["targetMethod"] == "automation_id"
        ):
            item["authority"] = auth
            item["targetMethod"] = rec["targetMethod"]
            item["targetValue"] = rec["targetValue"]
            item["name"] = rec["name"] or item["name"]
            item["needsReview"] = auth in ("low", "unknown")
    return groups


def build_catalog(groups):
    catalog_groups = []
    for (window_title, framework_id), grp in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        controls = []
        for key, item in grp["controls"].items():
            controls.append({
                "name": item["name"],
                "controlType": item["controlType"],
                "className": item["className"],
                "frameworkId": framework_id,
                "targetMethod": item["targetMethod"],
                "targetValue": item["targetValue"],
                "authority": item["authority"],
                "occurrences": item["occurrences"],
                "sources": sorted(item["sources"]),
                "needsReview": item["needsReview"],
            })
        controls.sort(key=lambda c: (-AUTHORITY_RANK[c["authority"]], -c["occurrences"], str(c["name"])))
        catalog_groups.append({
            "windowTitle": window_title,
            "frameworkId": framework_id,
            "controlCount": len(controls),
            "highCount": sum(1 for c in controls if c["authority"] == "high"),
            "mediumCount": sum(1 for c in controls if c["authority"] == "medium"),
            "lowCount": sum(1 for c in controls if c["authority"] in ("low", "unknown")),
            "controls": controls,
        })
    return catalog_groups


def build_report(groups):
    issues = []
    for (window_title, framework_id), grp in groups.items():
        for key, item in grp["controls"].items():
            if item["needsReview"]:
                issues.append({
                    "windowTitle": window_title,
                    "frameworkId": framework_id,
                    "name": item["name"],
                    "authority": item["authority"],
                    "occurrences": item["occurrences"],
                    "sources": sorted(item["sources"]),
                    "issue": "无 automation_id，定位靠 name，易与软件按钮对不上，建议用 build_control_map_library.py 实时抓取权威快照后重新合并",
                })
    issues.sort(key=lambda x: (str(x["windowTitle"]), x["authority"], str(x["name"])))
    return issues


def main():
    ap = argparse.ArgumentParser(description="合并/去重/规范化 WT 控件库为标准目录")
    ap.add_argument("--input-dir", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUT)
    ap.add_argument("--report", default=DEFAULT_REPORT)
    args = ap.parse_args()

    groups = merge(args.input_dir)
    catalog = build_catalog(groups)
    report = build_report(groups)

    total = sum(g["controlCount"] for g in catalog)
    payload = {
        "schemaVersion": "standard-catalog-1.0",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "sourceDir": os.path.abspath(args.input_dir),
        "summary": {
            "groups": len(catalog),
            "totalControls": total,
            "high": sum(g["highCount"] for g in catalog),
            "medium": sum(g["mediumCount"] for g in catalog),
            "lowOrUnknown": sum(g["lowCount"] for g in catalog),
            "needsReview": len(report),
        },
        "groups": catalog,
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump({"generatedAt": payload["generatedAt"], "issues": report}, f, ensure_ascii=False, indent=2)

    s = payload["summary"]
    print(f"标准库已生成: {args.output}")
    print(f"  分组(窗口): {s['groups']}  控件总数: {s['totalControls']}")
    print(f"  high(有automation_id): {s['high']}  medium(name+class): {s['medium']}  low/unknown(需复核): {s['lowOrUnknown']}")
    print(f"  对不上/需复核项: {s['needsReview']}  -> 见 {args.report}")


if __name__ == "__main__":
    main()
