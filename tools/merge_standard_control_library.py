# encoding: utf-8
"""合并 / 去重 / 规范化 WT 控件库，生成标准控件目录 standard_control_catalog.json。

设计目标（对应需求：标准控件库 + 信息匹配 + 整体控件树）：
  - 把 control_maps/ 下所有实时快照（*_control_map.json）与旧库（library_*.json）
    合并成一个按 (窗口标题, 框架) 分组的标准目录。
  - 去重规则：automation_id 优先；否则 (name+className+controlType)；否则 (name+controlType)。
    多实例区分：同 automation_id 的不同控件（如"半径/X/载入"同 aid=textbox、
    PART_ContentHost/PART_DropDownButton 系列）按 关联标签(labelText/relatedLabelName)
    → uiPath 逐层判别，不再塌缩为一条。
  - 权威度：high=有 automation_id；medium=有 name+className；low=仅 name；unknown=无标识。
    权威度提升时替换定位器；同权威度不覆盖已有非空字段（控件信息保留原则）。
  - 保留采集端全量关键字段：labelText/labelRelation/qualityTier/uiPath/supportedPatterns/
    legacyRoleText 等，并记录每期的来源文件与扫描时间（lastSeen 供新旧判断）。
  - 每组额外输出 controlsTree：按 uiPath 重建的层级树，叶子挂接目录控件引用，
    方便按"窗口 > 容器 > 控件"查询追随。
  - 输出 standard_catalog_mismatch_report.json：标出 authority=low/unknown、
    推断输入框（qualityTier=推断输入框）、同 aid 多实例组等待人工复核项。

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
DEFAULT_OUT = os.path.join(DEFAULT_INPUT, "standard", "standard_control_catalog.json")
DEFAULT_REPORT = os.path.join(DEFAULT_INPUT, "standard", "standard_catalog_mismatch_report.json")
DEFAULT_MASTER_OUT = os.path.join(DEFAULT_INPUT, "standard", "总控件信息.json")

AUTHORITY_RANK = {"high": 3, "medium": 2, "low": 1, "unknown": 0}

# 合并时需逐字段保留的采集端增强元数据（非空才输出，已有非空值不被低权威来源覆盖）
_PRESERVE_SCALAR_FIELDS = (
    "labelText", "labelRelation", "nameSource", "qualityTier", "uiPath",
    "localizedControlType", "accessKey", "helpText", "functionText", "boundingRectangle",
    "legacyRoleText", "legacyStateText", "locatorReason",
)
_PRESERVE_LIST_FIELDS = ("supportedPatterns", "optionValues")


def load_all(input_dir):
    recs = []
    files = []
    files.extend(sorted(glob.glob(os.path.join(input_dir, "recordings", "*.json"))))
    files.extend(sorted(glob.glob(os.path.join(input_dir, "library", "library_*.json"))))
    # 外部采集适配器（uia-peek / axe-windows）直接落盘到 control_maps/ 根目录，
    # 命名形如 {ts}_{title}_uiapeek_control_map.json / {ts}_pid{pid}_axewindows_control_map.json。
    # 之前只读 recordings/ 与 library/，导致这些新采集文件永远进不了标准库合并。
    files.extend(sorted(glob.glob(os.path.join(input_dir, "*_control_map.json"))))
    for fp in files:
        try:
            data = json.load(open(fp, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        tw = data.get("targetWindow", {}) or {}
        fwin = str(tw.get("title", "")).strip()
        ffw = str(tw.get("frameworkId", "")).strip()
        scan_time = str((data.get("scanMeta", {}) or {}).get("scanTime", "")).strip()
        flat_list = data.get("flatControls", []) or []
        for idx, c in enumerate(data.get("controlDefinitions", []) or []):
            rec = normalize_control(c, fwin, ffw, os.path.basename(fp), scan_time)
            if not rec:
                continue
            # controlDefinitions 不含 locatorScore/locatorReason（仅在同源 flatControls 中）；
            # 两者按下标一一对应（采集端按 flatControls 顺序构建 definitions），补全后再入桶，
            # 否则派生的 总控件信息.json 会丢失 control_search 补全层依赖的 locatorScore。
            if idx < len(flat_list) and isinstance(flat_list[idx], dict):
                flat = flat_list[idx]
                if rec.get("locatorScore") is None and flat.get("locatorScore") is not None:
                    rec["locatorScore"] = flat.get("locatorScore")
                if not rec.get("locatorReason") and str(flat.get("locatorReason", "")).strip():
                    rec["locatorReason"] = str(flat.get("locatorReason", "")).strip()
            recs.append(rec)
    return recs


def _is_non_empty_dict(d):
    return isinstance(d, dict) and len(d) > 0


def _is_non_empty_list(lst):
    return isinstance(lst, list) and len(lst) > 0


def normalize_control(c, fwin, ffw, src, scan_time=""):
    ins = c.get("inspectData", {}) or {}
    name = str(c.get("name", "") or ins.get("name", "")).strip()
    target_method = str(c.get("targetMethod", "")).strip()
    target_value = str(c.get("targetValue", "")).strip()
    # automationId 提取：单值与复合定位（automation_id,control_type[,label_text]）都支持
    automation_id = ""
    method_parts = [p.strip() for p in target_method.split(",") if p.strip()]
    value_parts = [p.strip() for p in target_value.split(",")]
    if method_parts and method_parts[0] == "automation_id" and value_parts:
        automation_id = value_parts[0]
    if not automation_id:
        automation_id = str(ins.get("automationId", "")).strip()
    window_title = str(c.get("windowTitle", "") or fwin).strip()
    framework_id = str(ins.get("frameworkId", "") or c.get("frameworkId", "") or ffw).strip()
    control_type = str(c.get("controlType", "") or ins.get("controlType", "")).strip()
    class_name = str(ins.get("className", "") or c.get("className", "")).strip()
    # 保留 inspectData（含 optionValues / dropdownValueText 等运行时依赖字段）
    inspect_data = dict(ins) if _is_non_empty_dict(ins) else {}
    rec = {
        "name": name,
        "automationId": automation_id,
        "windowTitle": window_title,
        "frameworkId": framework_id,
        "controlType": control_type,
        "className": class_name,
        "targetMethod": target_method,
        "targetValue": target_value,
        "inspectData": inspect_data,
        "suggestedControlName": str(c.get("suggestedControlName", "")).strip(),
        "displayName": str(c.get("displayName", "")).strip(),
        "relatedLabelName": str(c.get("relatedLabelName", "") or ins.get("relatedLabelName", "")).strip(),
        # labelText 缺省时回退 relatedLabelName（与采集端 backfill 行为一致）
        "labelText": (
            str(c.get("labelText", "") or ins.get("labelText", "")).strip()
            or str(c.get("relatedLabelName", "") or ins.get("relatedLabelName", "")).strip()
        ),
        "labelRelation": str(c.get("labelRelation", "") or ins.get("labelRelation", "")).strip(),
        "nameSource": str(c.get("nameSource", "") or ins.get("nameSource", "")).strip(),
        "qualityTier": str(c.get("_qualityTier", "") or c.get("qualityTier", "")).strip(),
        "uiPath": str(c.get("uiPath", "")).strip(),
        "localizedControlType": str(c.get("localizedControlType", "") or ins.get("localizedControlType", "")).strip(),
        "accessKey": str(c.get("accessKey", "") or ins.get("accessKey", "")).strip(),
        "helpText": str(c.get("helpText", "") or ins.get("helpText", "")).strip(),
        "functionText": str(c.get("functionText", "") or ins.get("functionText", "")).strip(),
        "boundingRectangle": str(ins.get("boundingRectangle", "")).strip(),
        "legacyRoleText": str(c.get("legacyRoleText", "") or ins.get("legacyRoleText", "")).strip(),
        "legacyStateText": str(c.get("legacyStateText", "") or ins.get("legacyStateText", "")).strip(),
        "isEnabled": c.get("isEnabled"),
        # locatorScore 为数值评分，None 表示缺失（与 isEnabled 同语义：缺失不覆盖已有）
        "locatorScore": c.get("locatorScore") if c.get("locatorScore") is not None else ins.get("locatorScore"),
        "locatorReason": str(c.get("locatorReason", "") or ins.get("locatorReason", "")).strip(),
        "supportedPatterns": [str(p) for p in (c.get("supportedPatterns") or ins.get("supportedPatterns") or []) if str(p).strip()],
        "optionValues": list(c.get("optionValues", []) or []),
        "dropdownValueText": str(c.get("dropdownValueText", "")).strip(),
        "source": src,
        "scanTime": scan_time,
    }
    return rec


def _discriminator(rec):
    """同 automation_id 多实例判别：关联标签优先，其次 uiPath。

    "半径/X/载入"同 aid=textbox、PART_ContentHost/PART_DropDownButton 系列
    靠此判别避免塌缩为一条；两者皆无的旧数据退回共享桶并标记复核。
    """
    disc = (rec.get("labelText", "") or rec.get("relatedLabelName", "")).strip().lower()
    if disc:
        return "l:" + disc
    ui_path = rec.get("uiPath", "").strip().lower()
    if ui_path:
        return "p:" + ui_path
    return ""


def dedupe_key(rec):
    disc = _discriminator(rec)
    if rec["automationId"]:
        base = ("aid", rec["automationId"].lower())
        return base + ((disc,) if disc else ())
    if rec["name"] and rec["className"]:
        return ("nc", rec["name"].lower(), rec["className"].lower(), rec["controlType"].lower(), disc)
    if rec["name"]:
        return ("n", rec["name"].lower(), rec["controlType"].lower(), disc)
    return None


def authority(rec):
    if rec["automationId"]:
        return "high"
    if rec["name"] and rec["className"]:
        return "medium"
    if rec["name"]:
        return "low"
    return "unknown"


def _locator_robustness(method):
    """定位器稳健性评分：label_text/help_text 复合定位抗布局变动，found_index 依赖遍历顺序易碎。

    同权威度时新期采集的更稳健定位器（如 automation_id,control_type,label_text,help_text）
    应替换旧期的 found_index 消歧版或裸 label_text 版，否则目录永远停留在先入桶的脆弱定位。
    help_text 是控件自身 UIA 属性（本地化资源真实功能名），比父容器兄弟 Text 查找更抗树结构
    变化，与 label_text 组合是双保险，故同样计 +1。
    """
    parts = [p.strip() for p in str(method).split(",") if p.strip()]
    score = 0
    if "label_text" in parts:
        score += 1
    if "help_text" in parts:
        score += 1
    if "found_index" in parts:
        score -= 1
    return score


def _absorb_bucket(target, source):
    """把裸 aid 桶并入带判别的目标桶：计数/来源合并，字段仅空值补缺。"""
    target["occurrences"] += source["occurrences"]
    target["sources"] |= source["sources"]
    target["sourceDetails"].extend(source["sourceDetails"])
    if source.get("lastSeen", "") > target.get("lastSeen", ""):
        target["lastSeen"] = source["lastSeen"]
    if _is_non_empty_dict(source.get("inspectData", {})) and not _is_non_empty_dict(target.get("inspectData", {})):
        target["inspectData"] = source["inspectData"]
    for field in _PRESERVE_SCALAR_FIELDS + ("displayName", "suggestedControlName", "relatedLabelName", "dropdownValueText"):
        if str(source.get(field, "")).strip() and not str(target.get(field, "")).strip():
            target[field] = source[field]
    for field in _PRESERVE_LIST_FIELDS:
        if _is_non_empty_list(source.get(field, [])) and not _is_non_empty_list(target.get(field, [])):
            target[field] = source[field]
    if source.get("isEnabled") is not None and target.get("isEnabled") is None:
        target["isEnabled"] = source["isEnabled"]
    if source.get("locatorScore") is not None and target.get("locatorScore") is None:
        target["locatorScore"] = source["locatorScore"]


def _review_reasons(auth, quality_tier, aid_multi_instance):
    reasons = []
    if auth in ("low", "unknown"):
        reasons.append("无automation_id，定位靠name，易与软件按钮对不上")
    if quality_tier == "推断输入框":
        reasons.append("推断输入框(PART_ContentHost提升)，建议检验定位复核")
    if aid_multi_instance:
        reasons.append("同automation_id多实例，按关联标签/uiPath区分，建议抽查")
    return reasons


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
                "automationId": rec["automationId"],
                "targetMethod": rec["targetMethod"],
                "targetValue": rec["targetValue"],
                "inspectData": rec.get("inspectData", {}),
                "isEnabled": rec.get("isEnabled"),
                "locatorScore": rec.get("locatorScore"),
                "authority": auth,
                "occurrences": 0,
                "sources": set(),
                "sourceDetails": [],
                "lastSeen": "",
            }
            # 创建桶时即复制首个来源的增强字段，否则单来源控件的 labelText 等会丢失
            for field in _PRESERVE_SCALAR_FIELDS + ("displayName", "suggestedControlName", "relatedLabelName", "dropdownValueText"):
                if str(rec.get(field, "")).strip():
                    bucket[key][field] = rec[field]
            for field in _PRESERVE_LIST_FIELDS:
                if _is_non_empty_list(rec.get(field, [])):
                    bucket[key][field] = rec[field]
        item = bucket[key]
        item["occurrences"] += 1
        item["sources"].add(rec["source"])
        item["sourceDetails"].append({"file": rec["source"], "scanTime": rec.get("scanTime", "")})
        if rec.get("scanTime", "") > item["lastSeen"]:
            item["lastSeen"] = rec["scanTime"]
        # 提升权威度：保留更高 authority 的 targetMethod/targetValue
        if AUTHORITY_RANK[auth] > AUTHORITY_RANK[item["authority"]] or (
            item["targetMethod"] in ("", "name") and rec["targetMethod"].split(",")[0].strip() == "automation_id"
        ):
            item["authority"] = auth
            item["targetMethod"] = rec["targetMethod"]
            item["targetValue"] = rec["targetValue"]
            item["name"] = rec["name"] or item["name"]
            # 高权威来源的元数据优先（空值不覆盖已有非空值）
            if _is_non_empty_dict(rec.get("inspectData", {})):
                item["inspectData"] = rec["inspectData"]
            for field in _PRESERVE_SCALAR_FIELDS + ("displayName", "suggestedControlName", "relatedLabelName", "dropdownValueText"):
                if str(rec.get(field, "")).strip():
                    item[field] = rec[field]
            for field in _PRESERVE_LIST_FIELDS:
                if _is_non_empty_list(rec.get(field, [])):
                    item[field] = rec[field]
            if rec.get("isEnabled") is not None:
                item["isEnabled"] = rec["isEnabled"]
            if rec.get("locatorScore") is not None:
                item["locatorScore"] = rec["locatorScore"]
        # 即使权威度不高，增强信息也应保留（不覆盖已有的非空值）
        else:
            # 同权威度下定位器稳健性升级：label_text 复合定位替换 found_index 消歧
            if (
                AUTHORITY_RANK[auth] == AUTHORITY_RANK[item["authority"]]
                and rec["targetMethod"]
                and _locator_robustness(rec["targetMethod"]) > _locator_robustness(item["targetMethod"])
            ):
                item["targetMethod"] = rec["targetMethod"]
                item["targetValue"] = rec["targetValue"]
            if _is_non_empty_dict(rec.get("inspectData", {})) and not _is_non_empty_dict(item.get("inspectData", {})):
                item["inspectData"] = rec["inspectData"]
            for field in _PRESERVE_SCALAR_FIELDS + ("displayName", "suggestedControlName", "relatedLabelName", "dropdownValueText"):
                if str(rec.get(field, "")).strip() and not str(item.get(field, "")).strip():
                    item[field] = rec[field]
            for field in _PRESERVE_LIST_FIELDS:
                if _is_non_empty_list(rec.get(field, [])) and not _is_non_empty_list(item.get(field, [])):
                    item[field] = rec[field]
            if rec.get("isEnabled") is not None and item.get("isEnabled") is None:
                item["isEnabled"] = rec["isEnabled"]
            if rec.get("locatorScore") is not None and item.get("locatorScore") is None:
                item["locatorScore"] = rec["locatorScore"]

    # 裸 aid 桶归属后处理：无判别信息（缺标签/路径）的采集若只有唯一一个
    # 带判别子桶，则并入该桶——避免跨期"一期有标签、一期无标签"重复建档；
    # 带判别子桶多于一个时无法安全归属，保留裸桶并交由复核报告提示。
    for grp in groups.values():
        controls = grp["controls"]
        aid_families = defaultdict(list)
        for key in list(controls.keys()):
            if key[0] == "aid":
                aid_families[key[1]].append(key)
        for _aid, keys in aid_families.items():
            bare_keys = [k for k in keys if len(k) == 2]
            tagged = [k for k in keys if len(k) > 2]
            if len(bare_keys) == 1 and len(tagged) == 1:
                source = controls.pop(bare_keys[0])
                _absorb_bucket(controls[tagged[0]], source)

    # 同 aid 多实例标记（供报告与前端提示）
    for grp in groups.values():
        aid_counts = defaultdict(int)
        for key in grp["controls"]:
            if key[0] == "aid":
                aid_counts[key[1]] += 1
        for key, item in grp["controls"].items():
            multi = key[0] == "aid" and aid_counts.get(key[1], 0) > 1
            item["reviewReasons"] = _review_reasons(item["authority"], item.get("qualityTier", ""), multi)
            item["needsReview"] = bool(item["reviewReasons"])
    return groups


def build_controls_tree(controls):
    """按 uiPath 把平铺目录重建为层级树，叶子挂接控件引用，方便查询追随。"""
    root_children = []
    node_index = {}

    def _ensure_node(parent_list, path_prefix, seg):
        key = path_prefix + ">" + seg if path_prefix else seg
        node = node_index.get(key)
        if node is None:
            node = {"name": seg, "children": []}
            node_index[key] = node
            parent_list.append(node)
        return node, key

    for ctrl in controls:
        path = str(ctrl.get("uiPath", "")).strip()
        if not path:
            continue
        segments = [seg.strip() for seg in path.split(">") if seg.strip()]
        if not segments:
            continue
        parent_list = root_children
        prefix = ""
        node = None
        for seg in segments:
            node, prefix = _ensure_node(parent_list, prefix, seg)
            parent_list = node["children"]
        if node is not None:
            node.setdefault("controls", []).append({
                "name": ctrl.get("name", ""),
                "controlType": ctrl.get("controlType", ""),
                "targetMethod": ctrl.get("targetMethod", ""),
                "targetValue": ctrl.get("targetValue", ""),
                "authority": ctrl.get("authority", ""),
                "labelText": ctrl.get("labelText", ""),
            })
    return root_children


def build_catalog(groups):
    catalog_groups = []
    for (window_title, framework_id), grp in sorted(groups.items(), key=lambda kv: (str(kv[0][0]), str(kv[0][1]))):
        controls = []
        for key, item in grp["controls"].items():
            entry = {
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
            }
            if item.get("automationId"):
                entry["automationId"] = item["automationId"]
            if item.get("locatorScore") is not None:
                entry["locatorScore"] = item["locatorScore"]
            if item.get("reviewReasons"):
                entry["reviewReasons"] = item["reviewReasons"]
            if item.get("lastSeen"):
                entry["lastSeen"] = item["lastSeen"]
            if item.get("sourceDetails"):
                entry["sourceDetails"] = item["sourceDetails"]
            # 保留运行时依赖的 inspectData / 采集端增强字段（非空才输出）
            if _is_non_empty_dict(item.get("inspectData", {})):
                entry["inspectData"] = item["inspectData"]
            for field in _PRESERVE_SCALAR_FIELDS + ("displayName", "suggestedControlName", "relatedLabelName", "dropdownValueText"):
                if str(item.get(field, "")).strip():
                    entry[field] = item[field]
            for field in _PRESERVE_LIST_FIELDS:
                if _is_non_empty_list(item.get(field, [])):
                    entry[field] = item[field]
            if item.get("isEnabled") is not None:
                entry["isEnabled"] = item["isEnabled"]
            controls.append(entry)
        controls.sort(key=lambda c: (-AUTHORITY_RANK[c["authority"]], -c["occurrences"], str(c["name"])))
        catalog_groups.append({
            "windowTitle": window_title,
            "frameworkId": framework_id,
            "controlCount": len(controls),
            "highCount": sum(1 for c in controls if c["authority"] == "high"),
            "mediumCount": sum(1 for c in controls if c["authority"] == "medium"),
            "lowCount": sum(1 for c in controls if c["authority"] in ("low", "unknown")),
            "controls": controls,
            # 整体控件树：按 uiPath 重建的层级视图，与平铺 controls 并存（查询追随用）
            "controlsTree": build_controls_tree(controls),
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
                    "qualityTier": item.get("qualityTier", ""),
                    "labelText": item.get("labelText", ""),
                    "uiPath": item.get("uiPath", ""),
                    "occurrences": item["occurrences"],
                    "sources": sorted(item["sources"]),
                    "lastSeen": item.get("lastSeen", ""),
                    "reviewReasons": item.get("reviewReasons", []),
                    "issue": "；".join(item.get("reviewReasons", []))
                             or "建议用 build_control_map_library.py 实时抓取权威快照后重新合并",
                })
    issues.sort(key=lambda x: (str(x["windowTitle"]), x["authority"], str(x["name"])))
    return issues


def build_master_payload(catalog_groups):
    """从标准目录派生旧版 总控件信息.json（flatControls 平铺）payload。

    派生而非独立合并：保证 master 与 catalog 数据同源（多实例不塌缩、
    增强字段全保留）。下游消费方（编辑器 master 视图 / 控件匹配补全 /
    control_live_detector / wt_control_index master 层 / control_search
    补全层）均读 flatControls 平铺格式，字段名对齐采集端：
    automationId 顶层 + recommendedTargetMethod/Value + inspectData 完整保留。
    """
    flat = []
    source_files = set()
    for grp in catalog_groups:
        win = str(grp.get("windowTitle", "")).strip()
        for item in grp.get("controls", []) or []:
            if not isinstance(item, dict):
                continue
            entry = {
                "name": item.get("name", ""),
                "controlType": item.get("controlType", ""),
                "className": item.get("className", ""),
                "frameworkId": item.get("frameworkId", ""),
                "windowTitle": win,
                "automationId": str(item.get("automationId", "")).strip(),
                "recommendedTargetMethod": item.get("targetMethod", ""),
                "recommendedTargetValue": item.get("targetValue", ""),
                "targetMethod": item.get("targetMethod", ""),
                "targetValue": item.get("targetValue", ""),
            }
            ins = item.get("inspectData")
            if _is_non_empty_dict(ins):
                ins = dict(ins)
                # 匹配消费方从 inspectData 读 automationId：顶层有值而 ins 缺省时补写（增强不删减）
                if entry["automationId"] and not str(ins.get("automationId", "")).strip():
                    ins["automationId"] = entry["automationId"]
                entry["inspectData"] = ins
            if item.get("isEnabled") is not None:
                entry["isEnabled"] = item["isEnabled"]
            if item.get("locatorScore") is not None:
                entry["locatorScore"] = item["locatorScore"]
            for field in _PRESERVE_SCALAR_FIELDS + ("displayName", "suggestedControlName", "relatedLabelName", "dropdownValueText"):
                if str(item.get(field, "")).strip():
                    entry[field] = item[field]
            for field in _PRESERVE_LIST_FIELDS:
                if _is_non_empty_list(item.get(field, [])):
                    entry[field] = item[field]
            for field in ("authority", "occurrences", "lastSeen"):
                if item.get(field) not in (None, ""):
                    entry[field] = item[field]
            if item.get("needsReview"):
                entry["needsReview"] = True
            if item.get("reviewReasons"):
                entry["reviewReasons"] = item["reviewReasons"]
            srcs = item.get("sources") or []
            if srcs:
                entry["_sourceFile"] = ", ".join(str(s) for s in srcs)
                source_files.update(str(s) for s in srcs)
            flat.append(entry)
    return {
        "schemaVersion": "1.0-master",
        "scanMeta": {
            "scanTime": datetime.now().isoformat(timespec="seconds"),
            "mode": "master-derived-from-standard-catalog",
            "totalControls": len(flat),
            "rawTotalControls": len(flat),
            "duplicatesRemoved": 0,
            "sourceFiles": sorted(source_files),
        },
        "targetWindow": {"title": "总控件信息（标准目录派生：%d个窗口分组，%d个控件）" % (len(catalog_groups), len(flat))},
        "flatControls": flat,
    }


def run_merge(input_dir=DEFAULT_INPUT, catalog_out=DEFAULT_OUT, report_out=DEFAULT_REPORT,
              master_out=DEFAULT_MASTER_OUT, progress_callback=None):
    """一站式合并：标准目录（权威） + 复核报告 + 总控件信息（派生平铺）。

    供命令行 main() 与 WT_Flow_Editor 控件维护界面"合并去重并保存"按钮共用，
    保证两份产物数据同源。返回统计信息 dict。

    Args:
        progress_callback: 可选回调 fn(percent: int, message: str)，合并期间推送进度。
    """
    if progress_callback:
        progress_callback(0, "读取控件目录…")
    groups = merge(input_dir)
    if progress_callback:
        progress_callback(25, f"已合并 {len(groups)} 个窗口分组，生成 catalog…")
    catalog = build_catalog(groups)
    if progress_callback:
        progress_callback(50, "生成复核报告…")
    report = build_report(groups)
    if progress_callback:
        progress_callback(75, "生成总控件信息…")
    master = build_master_payload(catalog)

    total = sum(g["controlCount"] for g in catalog)
    generated_at = datetime.now().isoformat(timespec="seconds")
    payload = {
        "schemaVersion": "standard-catalog-1.1",
        "generatedAt": generated_at,
        "sourceDir": os.path.abspath(input_dir),
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
    for path, data in (
        (catalog_out, payload),
        (report_out, {"generatedAt": generated_at, "issues": report}),
        (master_out, master),
    ):
        dir_name = os.path.dirname(os.path.abspath(path))
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return {
        "groups": len(catalog),
        "totalControls": total,
        "high": payload["summary"]["high"],
        "medium": payload["summary"]["medium"],
        "lowOrUnknown": payload["summary"]["lowOrUnknown"],
        "needsReview": len(report),
        "masterControls": len(master["flatControls"]),
        "catalogPath": catalog_out,
        "reportPath": report_out,
        "masterPath": master_out,
    }


def main():
    ap = argparse.ArgumentParser(description="合并/去重/规范化 WT 控件库为标准目录")
    ap.add_argument("--input-dir", default=DEFAULT_INPUT)
    ap.add_argument("--output", default=DEFAULT_OUT)
    ap.add_argument("--report", default=DEFAULT_REPORT)
    ap.add_argument("--master", default=DEFAULT_MASTER_OUT)
    args = ap.parse_args()

    stats = run_merge(args.input_dir, args.output, args.report, args.master)
    print(f"标准库已生成: {args.output}")
    print(f"  分组(窗口): {stats['groups']}  控件总数: {stats['totalControls']}")
    print(f"  high(有automation_id): {stats['high']}  medium(name+class): {stats['medium']}  low/unknown(需复核): {stats['lowOrUnknown']}")
    print(f"  待复核项: {stats['needsReview']}  -> 见 {args.report}")
    print(f"总控件信息(派生平铺): {args.master}  控件数: {stats['masterControls']}")


if __name__ == "__main__":
    main()
