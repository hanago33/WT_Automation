# -*- coding: utf-8 -*-
"""merge_standard_control_library 回归测试：

- 同 automation_id 多实例（半径/X/载入、PART_ContentHost 系列）不得塌缩为一条
- 多期采集同一控件正确合并（occurrences/sources/lastSeen）
- 采集端增强字段（labelText/uiPath/qualityTier 等）在合并后保留
- controlsTree 层级树按 uiPath 重建
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import merge_standard_control_library as msl


def _definition(name, aid="", control_type="Edit", label_text="", ui_path="",
                quality_tier="", target_method="", target_value="", class_name="TextBox"):
    ins = {"automationId": aid, "className": class_name, "controlType": control_type,
           "frameworkId": "WPF", "boundingRectangle": "[l=1,t=2,r=3,b=4]"}
    if label_text:
        ins["labelText"] = label_text
    return {
        "name": name,
        "controlType": control_type,
        "windowTitle": "主窗口",
        "targetMethod": target_method or ("automation_id,control_type" if aid else "name,control_type"),
        "targetValue": target_value or (f"{aid},{control_type}" if aid else f"{name},{control_type}"),
        "labelText": label_text,
        "uiPath": ui_path,
        "_qualityTier": quality_tier,
        "inspectData": ins,
    }


def _write_snapshot(dirpath, filename, definitions, scan_time="2026-07-20T10:00:00", flat_controls=None):
    payload = {
        "schemaVersion": "1.0",
        "scanMeta": {"scanTime": scan_time},
        "targetWindow": {"title": "主窗口", "frameworkId": "WPF"},
        "controlDefinitions": definitions,
    }
    if flat_controls is not None:
        payload["flatControls"] = flat_controls
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, filename), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


class MergeTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.input_dir = self._tmp.name
        self.recordings = os.path.join(self.input_dir, "recordings")
        os.makedirs(self.recordings, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _merge(self):
        groups = msl.merge(self.input_dir)
        catalog = msl.build_catalog(groups)
        self.assertEqual(len(catalog), 1, "应只有 主窗口/WPF 一个分组")
        return catalog[0]


class TestMultiInstanceNoCollapse(MergeTestBase):
    def test_same_aid_distinct_labels_not_collapsed(self):
        _write_snapshot(self.recordings, "a_control_map.json", [
            _definition("0 m", aid="textbox", label_text="半径",
                        ui_path="Win > A > t1", target_value="textbox,Edit,半径",
                        target_method="automation_id,control_type,label_text"),
            _definition("0 m", aid="textbox", label_text="X",
                        ui_path="Win > A > t2", target_value="textbox,Edit,X",
                        target_method="automation_id,control_type,label_text"),
            _definition("0 m", aid="textbox", label_text="载入",
                        ui_path="Win > A > t3", target_value="textbox,Edit,载入",
                        target_method="automation_id,control_type,label_text"),
        ])
        grp = self._merge()
        self.assertEqual(grp["controlCount"], 3, "同 aid 三实例不得塌缩")
        labels = {c.get("labelText") for c in grp["controls"]}
        self.assertEqual(labels, {"半径", "X", "载入"})
        for c in grp["controls"]:
            self.assertTrue(c["targetMethod"].endswith(",label_text"))
            self.assertIn("同automation_id多实例", ";".join(c.get("reviewReasons", [])))

    def test_same_aid_no_label_distinct_uipath_not_collapsed(self):
        _write_snapshot(self.recordings, "a_control_map.json", [
            _definition("名称输入", aid="PART_ContentHost", control_type="Edit",
                        ui_path="Win > 信息 > 名称", quality_tier="推断输入框"),
            _definition("描述输入", aid="PART_ContentHost", control_type="Edit",
                        ui_path="Win > 信息 > 描述", quality_tier="推断输入框"),
        ])
        grp = self._merge()
        self.assertEqual(grp["controlCount"], 2)
        tiers = {c.get("qualityTier") for c in grp["controls"]}
        self.assertEqual(tiers, {"推断输入框"})

    def test_composite_locator_aid_extraction(self):
        rec = msl.normalize_control(
            _definition("0 m", aid="", target_method="automation_id,control_type,label_text",
                        target_value="textbox,Edit,半径"),
            "主窗口", "WPF", "a.json")
        self.assertEqual(rec["automationId"], "textbox")


class TestCrossPeriodMerge(MergeTestBase):
    def test_locator_robustness_upgrade_found_index_to_label_text(self):
        # 旧期：found_index 消歧版；新期：label_text 复合版 → 同权威度下升级
        defs_old = [_definition("0 m", aid="textbox", label_text="半径",
                                ui_path="Win > A > t1",
                                target_method="automation_id,control_type,found_index",
                                target_value="textbox,Edit,0")]
        defs_new = [_definition("0 m", aid="textbox", label_text="半径",
                                ui_path="Win > A > t1",
                                target_method="automation_id,control_type,label_text",
                                target_value="textbox,Edit,半径")]
        _write_snapshot(self.recordings, "a_old_control_map.json", defs_old, "2026-07-01T09:00:00")
        _write_snapshot(self.recordings, "b_new_control_map.json", defs_new, "2026-07-28T11:00:00")
        grp = self._merge()
        self.assertEqual(grp["controlCount"], 1)
        ctrl = grp["controls"][0]
        self.assertEqual(ctrl["targetMethod"], "automation_id,control_type,label_text")
        self.assertEqual(ctrl["targetValue"], "textbox,Edit,半径")

    def test_locator_robustness_no_downgrade(self):
        # 先入桶 label_text 版，后来 found_index 版不得降级覆盖
        defs_old = [_definition("0 m", aid="textbox", label_text="半径",
                                ui_path="Win > A > t1",
                                target_method="automation_id,control_type,label_text",
                                target_value="textbox,Edit,半径")]
        defs_new = [_definition("0 m", aid="textbox", label_text="半径",
                                ui_path="Win > A > t1",
                                target_method="automation_id,control_type,found_index",
                                target_value="textbox,Edit,0")]
        _write_snapshot(self.recordings, "a_old_control_map.json", defs_old, "2026-07-01T09:00:00")
        _write_snapshot(self.recordings, "b_new_control_map.json", defs_new, "2026-07-28T11:00:00")
        grp = self._merge()
        self.assertEqual(grp["controlCount"], 1)
        ctrl = grp["controls"][0]
        self.assertEqual(ctrl["targetMethod"], "automation_id,control_type,label_text")
        self.assertEqual(ctrl["targetValue"], "textbox,Edit,半径")

    def test_locator_robustness_scoring(self):
        self.assertGreater(
            msl._locator_robustness("automation_id,control_type,label_text"),
            msl._locator_robustness("automation_id,control_type,found_index"),
        )
        self.assertGreater(
            msl._locator_robustness("automation_id,control_type"),
            msl._locator_robustness("automation_id,control_type,found_index"),
        )

    def test_same_control_two_periods_merged_with_freshness(self):
        defs_old = [_definition("背景", aid="cmb_bg", control_type="ComboBox", label_text="背景",
                                ui_path="Win > P > 背景")]
        defs_new = [_definition("背景", aid="cmb_bg", control_type="ComboBox", label_text="背景",
                                ui_path="Win > P > 背景")]
        _write_snapshot(self.recordings, "old_control_map.json", defs_old, "2026-07-01T09:00:00")
        _write_snapshot(self.recordings, "new_control_map.json", defs_new, "2026-07-28T11:00:00")
        grp = self._merge()
        self.assertEqual(grp["controlCount"], 1)
        entry = grp["controls"][0]
        self.assertEqual(entry["occurrences"], 2)
        self.assertEqual(len(entry["sources"]), 2)
        self.assertEqual(entry["lastSeen"], "2026-07-28T11:00:00")
        self.assertEqual(entry["labelText"], "背景")
        self.assertEqual(len(entry["sourceDetails"]), 2)

    def test_enhanced_fields_preserved_and_not_overwritten_by_empty(self):
        _write_snapshot(self.recordings, "rich_control_map.json", [
            _definition("私有", aid="btn_access", control_type="Button", label_text="访问级别",
                        ui_path="Win > P > 私有", quality_tier="优质"),
        ])
        # 后期快照同控件但增强字段缺失 → 不得覆盖已有非空值
        poor = _definition("私有", aid="btn_access", control_type="Button", label_text="",
                           ui_path="")
        _write_snapshot(self.recordings, "poor_control_map.json", [poor], "2026-07-29T09:00:00")
        grp = self._merge()
        self.assertEqual(grp["controlCount"], 1, "缺标签的新一期采集应归属既有带标签桶")
        entry = grp["controls"][0]
        self.assertEqual(entry["labelText"], "访问级别")
        self.assertEqual(entry["uiPath"], "Win > P > 私有")
        self.assertEqual(entry["qualityTier"], "优质")
        self.assertEqual(entry["occurrences"], 2)


class TestControlsTree(MergeTestBase):
    def test_controls_tree_rebuilt_from_uipath(self):
        _write_snapshot(self.recordings, "a_control_map.json", [
            _definition("名称", aid="", control_type="Text", ui_path="Win > 信息 > 名称"),
            _definition("名称输入", aid="PART_ContentHost", control_type="Edit",
                        label_text="名称", ui_path="Win > 信息 > PART_ContentHost"),
        ])
        grp = self._merge()
        tree = grp["controlsTree"]
        self.assertEqual(len(tree), 1)
        win = tree[0]
        self.assertEqual(win["name"], "Win")
        info = win["children"][0]
        self.assertEqual(info["name"], "信息")
        leaf_names = {child["name"] for child in info["children"]}
        self.assertEqual(leaf_names, {"名称", "PART_ContentHost"})
        # 叶子挂接控件引用
        edit_leaf = next(c for c in info["children"] if c["name"] == "PART_ContentHost")
        self.assertEqual(edit_leaf["controls"][0]["labelText"], "名称")

    def test_controls_without_uipath_absent_from_tree_but_kept_flat(self):
        _write_snapshot(self.recordings, "a_control_map.json", [
            _definition("无路径", aid="", control_type="Button", ui_path=""),
        ])
        grp = self._merge()
        self.assertEqual(grp["controlCount"], 1)
        self.assertEqual(grp["controlsTree"], [])


class TestMasterDerivation(MergeTestBase):
    def test_derived_master_not_collapsed_and_fields_complete(self):
        _write_snapshot(self.recordings, "a_control_map.json", [
            _definition("0 m", aid="textbox", label_text="半径",
                        ui_path="Win > A > t1", target_value="textbox,Edit,半径",
                        target_method="automation_id,control_type,label_text"),
            _definition("0 m", aid="textbox", label_text="X",
                        ui_path="Win > A > t2", target_value="textbox,Edit,X",
                        target_method="automation_id,control_type,label_text"),
            _definition("0 m", aid="textbox", label_text="载入",
                        ui_path="Win > A > t3", target_value="textbox,Edit,载入",
                        target_method="automation_id,control_type,label_text"),
        ])
        grp = self._merge()
        master = msl.build_master_payload([grp])
        flat = master["flatControls"]
        self.assertEqual(len(flat), 3, "派生 master 同样不得塌缩多实例")
        self.assertEqual(master["schemaVersion"], "1.0-master")
        self.assertEqual(master["scanMeta"]["totalControls"], 3)
        for f in flat:
            # 下游消费方依赖的顶层字段
            self.assertEqual(f["automationId"], "textbox")
            self.assertTrue(f["recommendedTargetMethod"].endswith(",label_text"))
            self.assertEqual(f["recommendedTargetMethod"], f["targetMethod"])
            self.assertEqual(f["recommendedTargetValue"], f["targetValue"])
            self.assertEqual(f["windowTitle"], "主窗口")
            self.assertEqual(f["frameworkId"], "WPF")
            self.assertIn("_sourceFile", f)
        self.assertEqual({f["labelText"] for f in flat}, {"半径", "X", "载入"})

    def test_derived_master_inspect_data_preserved_and_aid_backfilled(self):
        # aid 仅存在于复合定位 targetValue，inspectData.automationId 为空 → 派生时应补写
        _write_snapshot(self.recordings, "a_control_map.json", [
            _definition("0 m", aid="", label_text="半径", ui_path="Win > A > t1",
                        target_method="automation_id,control_type,label_text",
                        target_value="textbox,Edit,半径"),
        ])
        grp = self._merge()
        master = msl.build_master_payload([grp])
        entry = master["flatControls"][0]
        self.assertEqual(entry["automationId"], "textbox")
        self.assertEqual(entry["inspectData"]["automationId"], "textbox",
                         "匹配消费方从 inspectData 读 aid，缺省时应从顶层补写")
        self.assertEqual(entry["inspectData"]["boundingRectangle"], "[l=1,t=2,r=3,b=4]")


class TestLocatorScorePreserved(MergeTestBase):
    def test_locator_score_picked_from_sibling_flat_controls(self):
        defs = [_definition("背景", aid="cmb_bg", control_type="ComboBox",
                            label_text="背景", ui_path="Win > P > 背景")]
        flats = [{"name": "背景", "automationId": "cmb_bg",
                  "locatorScore": 88, "locatorReason": "aid唯一直接定位"}]
        _write_snapshot(self.recordings, "a_control_map.json", defs, flat_controls=flats)
        grp = self._merge()
        entry = grp["controls"][0]
        self.assertEqual(entry["locatorScore"], 88)
        self.assertEqual(entry["locatorReason"], "aid唯一直接定位")
        master = msl.build_master_payload([grp])
        self.assertEqual(master["flatControls"][0]["locatorScore"], 88)

    def test_locator_score_not_overwritten_by_missing_period(self):
        defs = [_definition("背景", aid="cmb_bg", control_type="ComboBox",
                            label_text="背景", ui_path="Win > P > 背景")]
        flats = [{"name": "背景", "automationId": "cmb_bg", "locatorScore": 88}]
        _write_snapshot(self.recordings, "a_old_control_map.json", defs,
                        "2026-07-01T09:00:00", flat_controls=flats)
        # 新一期无 flatControls（或缺评分）→ 已有 locatorScore 不得被抹掉
        _write_snapshot(self.recordings, "b_new_control_map.json", defs, "2026-07-28T11:00:00")
        grp = self._merge()
        self.assertEqual(grp["controlCount"], 1)
        self.assertEqual(grp["controls"][0]["locatorScore"], 88)


class TestRunMergeEndToEnd(MergeTestBase):
    def test_run_merge_writes_catalog_report_master(self):
        _write_snapshot(self.recordings, "a_control_map.json", [
            _definition("背景", aid="cmb_bg", control_type="ComboBox",
                        label_text="背景", ui_path="Win > P > 背景"),
            _definition("名称输入", aid="PART_ContentHost", control_type="Edit",
                        label_text="名称", ui_path="Win > 信息 > 名称", quality_tier="推断输入框"),
        ])
        out_dir = os.path.join(self.input_dir, "out")
        catalog_out = os.path.join(out_dir, "catalog.json")
        report_out = os.path.join(out_dir, "report.json")
        master_out = os.path.join(out_dir, "master.json")
        stats = msl.run_merge(self.input_dir, catalog_out, report_out, master_out)
        for path in (catalog_out, report_out, master_out):
            self.assertTrue(os.path.isfile(path), "缺少产物: %s" % path)
        self.assertEqual(stats["totalControls"], 2)
        self.assertEqual(stats["masterControls"], 2)
        self.assertEqual(stats["groups"], 1)
        with open(master_out, encoding="utf-8") as f:
            master = json.load(f)
        self.assertEqual(len(master["flatControls"]), 2)
        with open(report_out, encoding="utf-8") as f:
            report = json.load(f)
        self.assertIn("issues", report)


if __name__ == "__main__":
    unittest.main()
