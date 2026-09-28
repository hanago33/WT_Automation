import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_control_map_library as bcm


def _flat_item(
    name,
    runtime_id="",
    parent_index=-1,
    depth=0,
    control_type="Pane",
    class_name="",
    rect=None,
    **extra,
):
    """构造与主采集格式兼容的最小 flat_control 条目。"""
    rect = rect or {"left": 0, "top": 0, "right": 100, "bottom": 40}
    item = {
        "name": name,
        "displayName": name or "控件",
        "runtimeId": runtime_id,
        "handle": "",
        "className": class_name,
        "controlType": control_type,
        "boundingBox": dict(rect),
        "boundingRectangle": f"l={rect['left']} t={rect['top']} r={rect['right']} b={rect['bottom']}",
        "parentIndex": parent_index,
        "depth": depth,
        "treeLevel": depth,
        "uiPath": name,
        "parentPath": "",
        "inspectData": {"ancestors": [], "children": []},
    }
    item.update(extra)
    return item


class TestPrefixSubtreePaths(unittest.TestCase):
    def test_prefix_and_depth_offset(self):
        sub_flats = [
            _flat_item("面板", depth=0),
            _flat_item("按钮", parent_index=0, depth=1),
        ]
        sub_flats[1]["uiPath"] = "面板 > 按钮"
        sub_flats[1]["parentPath"] = "面板"
        sub_flats[1]["inspectData"]["ancestors"] = ["面板"]

        bcm._prefix_subtree_paths(sub_flats, ["窗口 1", "工作区"], base_depth=2)

        self.assertEqual(sub_flats[0]["uiPath"], "窗口 1 > 工作区 > 面板")
        self.assertEqual(sub_flats[0]["parentPath"], "窗口 1 > 工作区")
        self.assertEqual(sub_flats[0]["depth"], 2)
        self.assertEqual(sub_flats[0]["treeLevel"], 2)
        self.assertEqual(sub_flats[1]["uiPath"], "窗口 1 > 工作区 > 面板 > 按钮")
        self.assertEqual(sub_flats[1]["parentPath"], "窗口 1 > 工作区 > 面板")
        self.assertEqual(sub_flats[1]["depth"], 3)
        self.assertEqual(sub_flats[1]["inspectData"]["ancestors"], ["窗口 1", "工作区", "面板"])

    def test_empty_prefix_only_offsets_depth(self):
        sub_flats = [_flat_item("面板", depth=0)]
        bcm._prefix_subtree_paths(sub_flats, [], base_depth=1)
        self.assertEqual(sub_flats[0]["uiPath"], "面板")
        self.assertEqual(sub_flats[0]["depth"], 1)

    def test_no_offset_when_base_depth_zero(self):
        sub_flats = [_flat_item("面板", depth=0)]
        bcm._prefix_subtree_paths(sub_flats, ["窗口 1"], base_depth=0)
        self.assertEqual(sub_flats[0]["depth"], 0)
        self.assertEqual(sub_flats[0]["uiPath"], "窗口 1 > 面板")


class TestExtractIdentityMatchKeys(unittest.TestCase):
    def test_keys_and_usability(self):
        runtime, sig, usable = bcm._extract_identity_match_keys(
            {"runtimeId": "42.7", "name": "面板", "className": "Panel", "controlType": "Pane", "boundingRectangle": "l=0 t=0 r=1 b=1"}
        )
        self.assertEqual(runtime, "42.7")
        self.assertEqual(sig, ("面板", "Panel", "Pane"))
        self.assertTrue(usable)

    def test_signature_usable_when_only_control_type(self):
        # controlType 现在纳入签名可用性判断，仅有 controlType 时 usable=True
        _, _, usable = bcm._extract_identity_match_keys({"controlType": "Pane"})
        self.assertTrue(usable)


class TestFindTreeNodeByIdentity(unittest.TestCase):
    def _tree(self):
        return {
            "name": "窗口",
            "runtimeId": "42.1",
            "className": "Window",
            "controlType": "Window",
            "boundingRectangle": "l=0 t=0 r=800 b=600",
            "children": [
                {
                    "name": "面板",
                    "runtimeId": "42.7",
                    "className": "Panel",
                    "controlType": "Pane",
                    "boundingRectangle": "l=0 t=0 r=400 b=600",
                    "children": [],
                },
                {
                    "name": "面板",
                    "runtimeId": "",
                    "className": "Panel",
                    "controlType": "Pane",
                    "boundingRectangle": "l=400 t=0 r=800 b=600",
                    "children": [],
                },
            ],
        }

    def test_runtime_id_has_priority(self):
        tree = self._tree()
        target = {
            "runtimeId": "42.7",
            # 签名故意指向另一个节点，验证 runtimeId 优先
            "name": "面板",
            "className": "Panel",
            "controlType": "Pane",
            "boundingRectangle": "l=400 t=0 r=800 b=600",
        }
        node = bcm._find_tree_node_by_identity(tree, target)
        self.assertEqual(node["runtimeId"], "42.7")

    def test_signature_fallback_without_runtime_id(self):
        tree = self._tree()
        target = {
            "runtimeId": "",
            "name": "面板",
            "className": "Panel",
            "controlType": "Pane",
            "boundingRectangle": "l=400 t=0 r=800 b=600",
        }
        node = bcm._find_tree_node_by_identity(tree, target)
        self.assertEqual(node["boundingRectangle"], "l=400 t=0 r=800 b=600")

    def test_not_found_returns_none(self):
        node = bcm._find_tree_node_by_identity(
            self._tree(),
            {"runtimeId": "99.99", "name": "不存在", "className": "X", "controlType": "Button", "boundingRectangle": "l=1 t=1 r=2 b=2"},
        )
        self.assertIsNone(node)


class TestMergeSupplementIntoPayload(unittest.TestCase):
    def _payload(self, definitions_aligned=True):
        root = _flat_item("窗口", runtime_id="42.1", control_type="Window", class_name="Window",
                          rect={"left": 0, "top": 0, "right": 800, "bottom": 600})
        panel = _flat_item("面板", runtime_id="42.7", class_name="Panel", parent_index=0, depth=1,
                           rect={"left": 0, "top": 0, "right": 400, "bottom": 600})
        flat = [root, panel]
        tree = {
            "name": "窗口",
            "runtimeId": "42.1",
            "className": "Window",
            "controlType": "Window",
            "boundingRectangle": root["boundingRectangle"],
            "flatIndex": 0,
            "children": [
                {
                    "name": "面板",
                    "runtimeId": "42.7",
                    "className": "Panel",
                    "controlType": "Pane",
                    "boundingRectangle": panel["boundingRectangle"],
                    "flatIndex": 1,
                    "childCount": 0,
                    "children": [],
                }
            ],
        }
        definitions = [{"id": f"def_{i}"} for i in range(len(flat))] if definitions_aligned else [{"id": "def_only"}]
        return {
            "targetWindow": {"title": "窗口", "className": "Window"},
            "flatControls": flat,
            "controlDefinitions": definitions,
            "controlsTree": tree,
            "scanMeta": {"totalControls": len(flat), "controlTypeSummary": {"Window": 1, "Pane": 1}},
        }

    def _sub_flats(self):
        """补采结果：根为已存在的面板（42.7），带两个新子控件。"""
        panel = _flat_item("面板", runtime_id="42.7", class_name="Panel", depth=1,
                           rect={"left": 0, "top": 0, "right": 400, "bottom": 600},
                           supportedPatterns=["ExpandCollapse"], expandCollapseState="Expanded")
        btn = _flat_item("确定", runtime_id="42.20", control_type="Button", class_name="Button",
                         parent_index=0, depth=2, rect={"left": 10, "top": 10, "right": 90, "bottom": 40})
        text = _flat_item("提示", runtime_id="42.21", control_type="Text", class_name="TextBlock",
                          parent_index=1, depth=3, rect={"left": 12, "top": 12, "right": 80, "bottom": 30})
        return [panel, btn, text]

    def test_dedup_append_and_fill_empty_fields(self):
        payload = self._payload()
        added, anchor_found = bcm.merge_supplement_into_payload(payload, self._sub_flats())

        self.assertEqual(added, 2)
        self.assertTrue(anchor_found)
        flat = payload["flatControls"]
        self.assertEqual(len(flat), 4)
        # 既有条目下标不变，且被实时结果补空（不覆盖既有值）
        self.assertEqual(flat[1]["runtimeId"], "42.7")
        self.assertEqual(flat[1]["supportedPatterns"], ["ExpandCollapse"])
        self.assertEqual(flat[1]["expandCollapseState"], "Expanded")
        # 新条目 parentIndex 已改写为合并后下标空间
        self.assertEqual(flat[2]["runtimeId"], "42.20")
        self.assertEqual(flat[2]["parentIndex"], 1)
        self.assertEqual(flat[3]["parentIndex"], 2)
        self.assertEqual(flat[2]["supplementSource"], "point_supplement")
        # 新条目经过增强（含质量分级字段）
        self.assertIn("qualityTier", flat[2])

    def test_fill_does_not_overwrite_existing_value(self):
        payload = self._payload()
        payload["flatControls"][1]["expandCollapseState"] = "Collapsed"
        bcm.merge_supplement_into_payload(payload, self._sub_flats())
        self.assertEqual(payload["flatControls"][1]["expandCollapseState"], "Collapsed")

    def test_anchor_children_merged_with_remapped_flat_index(self):
        payload = self._payload()
        bcm.merge_supplement_into_payload(payload, self._sub_flats())
        anchor = payload["controlsTree"]["children"][0]
        self.assertEqual(anchor["runtimeId"], "42.7")
        self.assertEqual(anchor["childCount"], 1)
        btn_node = anchor["children"][0]
        self.assertEqual(btn_node["runtimeId"], "42.20")
        self.assertEqual(btn_node["flatIndex"], 2)
        self.assertEqual(btn_node["children"][0]["flatIndex"], 3)

    def test_shallow_supplement_does_not_prune_existing_deep_children(self):
        # 场景回归：悬停补采深度受限，合并时必须保留旧扫描中更深的分支（零丢弃）
        payload = self._payload()
        deep_node = {
            "name": "深层控件",
            "runtimeId": "42.555",
            "className": "X",
            "controlType": "Button",
            "boundingRectangle": "l=1 t=1 r=2 b=2",
            "flatIndex": 1,
            "children": [],
        }
        anchor = payload["controlsTree"]["children"][0]
        anchor["children"] = [deep_node]
        anchor["childCount"] = 1
        bcm.merge_supplement_into_payload(payload, self._sub_flats())
        kept_ids = [child.get("runtimeId") for child in payload["controlsTree"]["children"][0]["children"]]
        self.assertIn("42.20", kept_ids)  # 本次新采的按钮
        self.assertIn("42.555", kept_ids)  # 旧的深层节点被保留

    def test_anchor_missing_appends_to_tree_root(self):
        payload = self._payload()
        sub_flats = self._sub_flats()
        for item in sub_flats:
            item["runtimeId"] = item["runtimeId"].replace("42.", "77.")
            item["name"] = item["name"] + "_新"
            item["boundingRectangle"] = "l=900 t=900 r=999 b=999"
            item["boundingBox"] = {"left": 900, "top": 900, "right": 999, "bottom": 999}
        added, anchor_found = bcm.merge_supplement_into_payload(payload, sub_flats)
        self.assertEqual(added, 3)
        self.assertFalse(anchor_found)
        root_children = payload["controlsTree"]["children"]
        self.assertEqual(root_children[-1]["runtimeId"], "77.7")

    def test_empty_tree_uses_subtree_as_root(self):
        payload = self._payload()
        payload["controlsTree"] = {}
        added, anchor_found = bcm.merge_supplement_into_payload(payload, self._sub_flats())
        self.assertTrue(anchor_found)
        self.assertEqual(payload["controlsTree"].get("runtimeId"), "42.7")

    def test_definitions_appended_only_when_aligned(self):
        aligned = self._payload(definitions_aligned=True)
        bcm.merge_supplement_into_payload(aligned, self._sub_flats())
        self.assertEqual(len(aligned["controlDefinitions"]), len(aligned["flatControls"]))

        misaligned = self._payload(definitions_aligned=False)
        bcm.merge_supplement_into_payload(misaligned, self._sub_flats())
        self.assertEqual(len(misaligned["controlDefinitions"]), 1)

    def test_scan_meta_updated(self):
        payload = self._payload()
        bcm.merge_supplement_into_payload(payload, self._sub_flats())
        meta = payload["scanMeta"]
        self.assertEqual(meta["totalControls"], 4)
        self.assertEqual(meta["controlTypeSummary"]["Button"], 1)
        self.assertEqual(meta["controlTypeSummary"]["Text"], 1)
        scans = meta["supplementScans"]
        self.assertEqual(len(scans), 1)
        self.assertEqual(scans[0]["collected"], 3)
        self.assertEqual(scans[0]["added"], 2)
        self.assertTrue(scans[0]["anchorFound"])

    def test_second_merge_is_idempotent_for_same_subtree(self):
        payload = self._payload()
        bcm.merge_supplement_into_payload(payload, self._sub_flats())
        added, _ = bcm.merge_supplement_into_payload(payload, self._sub_flats())
        self.assertEqual(added, 0)
        self.assertEqual(len(payload["flatControls"]), 4)
        self.assertEqual(len(payload["scanMeta"]["supplementScans"]), 2)

    def test_empty_inputs_return_zero(self):
        self.assertEqual(bcm.merge_supplement_into_payload({}, []), (0, False))
        self.assertEqual(bcm.merge_supplement_into_payload(None, [_flat_item("x")]), (0, False))


class TestMergeTreeChildrenPreserving(unittest.TestCase):
    def _node(self, name, runtime_id="", children=None, **extra):
        node = {
            "name": name,
            "runtimeId": runtime_id,
            "className": "Panel",
            "controlType": "Pane",
            "boundingRectangle": f"l=0 t=0 r=100 b=40 {name}",
            "children": children or [],
        }
        node.update(extra)
        return node

    def test_shallow_supplement_preserves_deeper_old_branches(self):
        old_deep = self._node("深层按钮", "42.99")
        old_child = self._node("容器", "42.50", children=[old_deep])
        new_child = self._node("容器", "42.50")  # 浅补采：同一容器但没采到子节点
        merged = bcm._merge_tree_children_preserving([old_child], [new_child])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["runtimeId"], "42.50")
        self.assertEqual(merged[0]["children"][0]["runtimeId"], "42.99")
        self.assertEqual(merged[0]["childCount"], 1)

    def test_unmatched_old_nodes_are_kept_after_new_ones(self):
        old_only = self._node("旧节点", "42.1")
        new_only = self._node("新节点", "42.2")
        merged = bcm._merge_tree_children_preserving([old_only], [new_only])
        self.assertEqual([node["runtimeId"] for node in merged], ["42.2", "42.1"])

    def test_matched_node_inherits_user_fields_by_fill_empty(self):
        old = self._node("容器", "42.7", savedControlName="我的面板", locatorScore=88)
        new = self._node("容器", "42.7", locatorScore=10)  # 实时采集不含用户命名
        merged = bcm._merge_tree_children_preserving([old], [new])
        self.assertEqual(merged[0]["savedControlName"], "我的面板")
        # 只补空，不覆盖实时新值
        self.assertEqual(merged[0]["locatorScore"], 10)

    def test_signature_fallback_matches_when_runtime_id_missing(self):
        old = self._node("容器", "", savedControlName="命名")
        new = self._node("容器", "")
        merged = bcm._merge_tree_children_preserving([old], [new])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["savedControlName"], "命名")

    def test_empty_side_passthrough(self):
        node = self._node("x", "1")
        self.assertEqual(bcm._merge_tree_children_preserving([], [node]), [node])
        self.assertEqual(bcm._merge_tree_children_preserving([node], []), [node])


if __name__ == "__main__":
    unittest.main()
