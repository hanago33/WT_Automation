# -*- coding: utf-8 -*-
"""「合并入库」对话框去重路径回归测试（build_control_map_library / control_live_detector）。

背景：interest-area 模板复制按钮（各面板同名 automationId 的 Add/Edit/Delete/…）在
canonical 合并（merge_standard_control_library）中已按节点分桶，但「📥 合并入库」对话框
走的是独立的 _merge_dedup_key / _build_dedup_key，其键 (aid, ct, name) 中 name 为空
（SVG path）→ 8 个节点被误并为 1 条。修复后三种去重模式（aid / uiPath / name+ct）均追加
"ia:<节点>" 区分符，节点名来源三级：TileHeader 面板标题映射 → labelText/relatedLabelName
→ recommendedTargetValue 第 3 段节点名。

覆盖：
- 新采集（labelText 已带节点名）三种模式均 8 节点不塌缩
- 旧采集（labelText 空、rtv 第 3 段为数字）经 TileHeader 映射恢复 8 节点
- 非 interest-area 控件绝不追加 "ia:" 后缀（零回归）
- 两处实现（build_control_map_library / control_live_detector）行为一致
- _merge_payloads_into_target 端到端：8 节点全保留
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_control_map_library as bcm
import control_live_detector as cld

NODES = ["测风点", "风机", "结果点", "绘图", "配置", "风廓线", "Lidar", "中尺度单元"]
MODES = ["automationId+controlType+name", "uiPath", "name+controlType"]


class _StubDialog(cld.MergeDialog):
    """规避 GUI 初始化，直接调用去重键方法。"""

    def __init__(self):
        pass


def _flat_ia(parent_idx, aid, node, label=None, rtv_node=None, ui_path="Win > IA > SVG"):
    """构造 interest-area 图标按钮 flat control。

    label: 采集端已写入的节点名（None 表示旧采集无 labelText）。
    rtv_node: recommendedTargetValue 第 3 段（旧采集为数字，如 "1"）。
    """
    if rtv_node is None:
        rtv = "%s,Button,%s,功能文本" % (aid, node)
    else:
        rtv = "%s,Button,%s" % (aid, rtv_node)
    return {
        "name": "M19,13L13,13 13,19 11,19 11,13 5,13 5,11 11,11 11,5 13,5 13,11 19,11 19,13z",
        "controlType": "Button",
        "automationId": aid,
        "parentIndex": parent_idx,
        "uiPath": ui_path,
        "labelText": label or "",
        "relatedLabelName": label or "",
        "recommendedTargetMethod": "automation_id,control_type,label_text",
        "recommendedTargetValue": rtv,
        "inspectData": {"automationId": aid, "controlType": "Button",
                        "className": "Button", "frameworkId": "WPF"},
    }


def _tile_header(parent_idx, node):
    return {
        "automationId": "InterestAreasView_Tile_Header",
        "controlType": "Text",
        "name": node,
        "parentIndex": parent_idx,
    }


class TestDialogDedupKeyPerNode(unittest.TestCase):
    def setUp(self):
        self.stub = _StubDialog()

    def _eight_labeled(self):
        return [_flat_ia(1000 + i, "InterestAreas_Button_Add", node, label=node)
                for i, node in enumerate(NODES)]

    def test_default_mode_eight_distinct_when_labeled(self):
        flats = self._eight_labeled()
        keys = {bcm._merge_dedup_key(c, "automationId+controlType+name") for c in flats}
        self.assertEqual(len(keys), 8)

    def test_uipath_mode_eight_distinct_when_labeled(self):
        # 各节点按钮共享同一 uiPath，仅靠节点标签区分
        flats = self._eight_labeled()
        keys = {bcm._merge_dedup_key(c, "uiPath") for c in flats}
        self.assertEqual(len(keys), 8)

    def test_name_ct_mode_eight_distinct_when_labeled(self):
        flats = self._eight_labeled()
        keys = {bcm._merge_dedup_key(c, "name+controlType") for c in flats}
        self.assertEqual(len(keys), 8)

    def test_old_format_tile_header_recovers_eight(self):
        # 旧采集：labelText 空、rtv 第 3 段为数字 → 无映射时塌缩为 1
        flats = []
        headers = []
        for i, node in enumerate(NODES):
            pid = 2000 + i
            flats.append(_flat_ia(pid, "InterestAreas_Button_Add", node,
                                  label=None, rtv_node=str(i + 1)))
            headers.append(_tile_header(pid, node))
        keys_no_map = {bcm._merge_dedup_key(c, "automationId+controlType+name") for c in flats}
        self.assertEqual(len(keys_no_map), 1, "旧格式无 TileHeader 映射时必然塌缩")
        pmap = bcm._build_ia_panel_title_map(headers + flats)
        keys_map = {bcm._merge_dedup_key(c, "automationId+controlType+name", pmap) for c in flats}
        self.assertEqual(len(keys_map), 8, "TileHeader 映射应恢复 8 节点")

    def test_old_format_uipath_mode_tile_header_recovers_eight(self):
        flats = []
        headers = []
        for i, node in enumerate(NODES):
            pid = 3000 + i
            flats.append(_flat_ia(pid, "InterestAreas_Button_Add", node,
                                  label=None, rtv_node=str(i + 1)))
            headers.append(_tile_header(pid, node))
        pmap = bcm._build_ia_panel_title_map(headers + flats)
        keys = {bcm._merge_dedup_key(c, "uiPath", pmap) for c in flats}
        self.assertEqual(len(keys), 8)

    def test_non_ia_never_suffixed(self):
        ordinary = [
            {"automationId": "textbox", "controlType": "Edit", "name": "半径",
             "inspectData": {"automationId": "textbox", "controlType": "Edit"}},
            {"automationId": "", "controlType": "Button", "name": "确定",
             "inspectData": {"controlType": "Button"}},
            {"automationId": "OtherButton_X", "controlType": "Button", "name": "",
             "inspectData": {"automationId": "OtherButton_X"}},
        ]
        for mode in MODES:
            for c in ordinary:
                k = bcm._merge_dedup_key(c, mode, {})
                self.assertFalse(any(str(x).startswith("ia:") for x in k),
                                 "%s 不应追加 ia: 后缀: %r" % (mode, k))
                kc = self.stub._build_dedup_key(c, mode, {})
                self.assertFalse(any(str(x).startswith("ia:") for x in kc),
                                 "cld %s 不应追加 ia: 后缀: %r" % (mode, kc))

    def test_bcm_and_cld_consistent(self):
        flats = self._eight_labeled()
        pmap = bcm._build_ia_panel_title_map(flats)
        for mode in MODES:
            kb = {bcm._merge_dedup_key(c, mode, pmap) for c in flats}
            kc = {self.stub._build_dedup_key(c, mode, pmap) for c in flats}
            self.assertEqual(kb, kc, "两处实现 key 集合应一致 (%s)" % mode)

    def test_merge_payloads_into_target_keeps_eight(self):
        src = {"flatControls": self._eight_labeled()}
        target = {"flatControls": []}
        merged, stats = bcm._merge_payloads_into_target(
            target, [src], "automationId+controlType+name", False)
        self.assertEqual(stats["added"], 8)
        adds = [c for c in merged["flatControls"]
                if c.get("automationId") == "InterestAreas_Button_Add"]
        self.assertEqual(len(adds), 8, "合并入库后 8 节点按钮应全保留")

    def test_merge_payloads_into_target_idempotent(self):
        src = {"flatControls": self._eight_labeled()}
        merged, stats = bcm._merge_payloads_into_target(
            dict(src), [src], "automationId+controlType+name", False)
        self.assertEqual(stats["added"], 0, "同一源合回自身应幂等")
        adds = [c for c in merged["flatControls"]
                if c.get("automationId") == "InterestAreas_Button_Add"]
        self.assertEqual(len(adds), 8)


if __name__ == "__main__":
    unittest.main()
