"""采集覆盖度回归测试：

- 可交互 Pattern 感知保留：无标识但可交互的容器（自定义输入框/图形按钮）不得被过滤误丢
- WPF 输入框规范化 2.0：className 子串扩展 + Value/Text Pattern 判定为 Edit
- UIA LabeledBy 权威标签关联优先于几何最近邻
- controlDefinition 持久化标签关联与交互元数据
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_control_map_library as bcm


def _item(control_type="Custom", name="", automation_id="", class_name="",
          patterns=None, rect=None, parent_index=-1, **extra):
    rect = rect or {"left": 100, "top": 100, "right": 300, "bottom": 130}
    item = {
        "name": name,
        "automationId": automation_id,
        "className": class_name,
        "controlType": control_type,
        "boundingBox": dict(rect),
        "parentIndex": parent_index,
        "supportedPatterns": list(patterns or []),
        "inspectData": {},
    }
    item.update(extra)
    return item


class TestActionablePattern(unittest.TestCase):
    def test_pattern_hit(self):
        self.assertTrue(bcm._item_has_actionable_pattern(_item(patterns=["Value"])))
        self.assertTrue(bcm._item_has_actionable_pattern(_item(patterns=["Invoke", "ExpandCollapse"])))

    def test_keyboard_focusable_fallback(self):
        self.assertTrue(bcm._item_has_actionable_pattern(_item(isKeyboardFocusable="true")))
        # isKeyboardFocusable 只存在于 inspectData 时也能识别
        item = _item()
        item["inspectData"] = {"isKeyboardFocusable": "True"}
        self.assertTrue(bcm._item_has_actionable_pattern(item))

    def test_non_actionable(self):
        self.assertFalse(bcm._item_has_actionable_pattern(_item()))
        self.assertFalse(bcm._item_has_actionable_pattern(_item(patterns=["Scroll"])))
        self.assertFalse(bcm._item_has_actionable_pattern(None))
        self.assertFalse(bcm._item_has_actionable_pattern("x"))


class TestFilterNoiseControls(unittest.TestCase):
    def test_actionable_unidentified_container_kept(self):
        # 自定义输入框：Custom 无 name/automationId，但支持 ValuePattern → 必须保留
        custom_edit = _item(control_type="Custom", patterns=["Value"])
        # 图形按钮：Pane 无标识，但支持 Invoke → 必须保留
        glyph_button = _item(control_type="Pane", patterns=["Invoke"])
        kept = bcm._filter_noise_controls([custom_edit, glyph_button])
        self.assertEqual(len(kept), 2)

    def test_non_actionable_unidentified_container_dropped(self):
        noise = _item(control_type="Custom")
        identified = _item(control_type="Pane", automation_id="MainPanel")
        kept = bcm._filter_noise_controls([noise, identified])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["automationId"], "MainPanel")

    def test_focusable_unidentified_container_kept(self):
        focusable = _item(control_type="Group", isKeyboardFocusable="true")
        kept = bcm._filter_noise_controls([focusable])
        self.assertEqual(len(kept), 1)


class TestPruneEmptyContainers(unittest.TestCase):
    def test_actionable_empty_container_kept(self):
        custom_edit = _item(control_type="Custom", patterns=["Text"])
        kept = bcm._prune_empty_unidentified_containers([custom_edit])
        self.assertEqual(len(kept), 1)

    def test_non_actionable_empty_container_dropped(self):
        noise = _item(control_type="Pane")
        kept = bcm._prune_empty_unidentified_containers([noise])
        self.assertEqual(len(kept), 0)


class TestNormalizeTextboxWrappers(unittest.TestCase):
    def test_classname_substring_normalized(self):
        # WPF 自定义输入框变体（WatermarkTextBox/TextBoxEx）按子串识别
        for class_name in ("WatermarkTextBox", "TextBoxEx", "MyMaskedTextBox"):
            item = _item(control_type="Custom", class_name=class_name)
            bcm._normalize_textbox_wrappers([item])
            self.assertEqual(item["controlType"], "Edit", class_name)
            self.assertEqual(item["controlTypeSource"], "normalized-from-classname")

    def test_value_pattern_normalized(self):
        # 无标识容器 + ValuePattern → 几乎必是可输入控件
        item = _item(control_type="Custom", patterns=["Value"])
        bcm._normalize_textbox_wrappers([item])
        self.assertEqual(item["controlType"], "Edit")
        self.assertEqual(item["controlTypeSource"], "normalized-from-valuepattern")
        self.assertEqual(item["inspectData"]["controlType"], "Edit")

    def test_text_pattern_normalized(self):
        item = _item(control_type="Pane", patterns=["Text"])
        bcm._normalize_textbox_wrappers([item])
        self.assertEqual(item["controlType"], "Edit")

    def test_plain_container_untouched(self):
        item = _item(control_type="Pane", class_name="Grid")
        bcm._normalize_textbox_wrappers([item])
        self.assertEqual(item["controlType"], "Pane")

    def test_content_host_still_folded(self):
        # PART_ContentHost 折叠行为不破坏（即使它被情况 3 改写类型）
        parent = _item(control_type="Custom", class_name="TextBox", parent_index=-1)
        host = _item(control_type="Pane", automation_id="PART_ContentHost",
                     parent_index=0, patterns=["Text"])
        bcm._normalize_textbox_wrappers([parent, host])
        self.assertTrue(host["foldedIntoParent"])
        self.assertEqual(parent["controlType"], "Edit")


class TestBackfillLabeledBy(unittest.TestCase):
    def _label(self, name, rect):
        return _item(control_type="Text", name=name, rect=rect)

    def test_labeledby_takes_priority_over_geometry(self):
        # 权威 LabeledBy="名称"；几何上更近的标签是"无关"——必须选权威值
        edit = _item(control_type="Edit", labeledByName="名称",
                     rect={"left": 100, "top": 100, "right": 300, "bottom": 130})
        other_label = self._label("无关", {"left": 10, "top": 100, "right": 90, "bottom": 130})
        bcm._backfill_label_text_to_controls([edit, other_label])
        self.assertEqual(edit["labelText"], "名称")
        self.assertEqual(edit["labelRelation"], "uia-labeledby")
        self.assertEqual(edit["name"], "名称")
        self.assertEqual(edit["nameSource"], "labeledby-backfill")

    def test_geometry_fallback_without_labeledby(self):
        # 弱定位控件无 labeledByName/relatedLabelName 时，
        # labelText 与 name 均由几何最近邻标签回填
        edit = _item(control_type="Edit",
                     rect={"left": 100, "top": 100, "right": 300, "bottom": 130})
        label = self._label("名称", {"left": 10, "top": 100, "right": 90, "bottom": 130})
        bcm._backfill_label_text_to_controls([edit, label])
        self.assertEqual(edit["labelText"], "名称")
        self.assertEqual(edit["labelRelation"], "nearest-text-label")
        self.assertEqual(edit["name"], "名称")
        self.assertEqual(edit["nameSource"], "label-backfill")

    def test_labeledby_works_without_any_text_labels(self):
        # 窗口内没有任何 Text 标签时，LabeledBy 关联仍然生效（不再被 labels 早退跳过）
        edit = _item(control_type="Edit", labeledByName="型号")
        bcm._backfill_label_text_to_controls([edit])
        self.assertEqual(edit["labelText"], "型号")
        self.assertEqual(edit["name"], "型号")

    def test_label_text_priority_chain(self):
        """labelText 来源优先级链：relatedLabelName > labeledByName > 几何最近邻。

        强定位控件（name+automationId）仅回填 labelText，name 与推荐定位器不变；
        弱定位控件（无 name 无 automationId）同时用最高优先级来源回填 name。
        """
        rect_edit = {"left": 100, "top": 100, "right": 300, "bottom": 130}
        rect_label = {"left": 10, "top": 100, "right": 90, "bottom": 130}

        # ── 强定位控件 ──────────────────────────────────

        # ① 三源同时存在 → relatedLabelName 胜出，name 不变
        strong_all3 = _item(control_type="Edit", name="已有", automation_id="NameBox",
                            labeledByName="UIA标签", relatedLabelName="区域标签",
                            rect=rect_edit)
        geo_label = self._label("几何标签", rect_label)
        bcm._backfill_label_text_to_controls([strong_all3, geo_label])
        self.assertEqual(strong_all3["labelText"], "区域标签")
        self.assertEqual(strong_all3["labelRelation"], "region-association")
        self.assertEqual(strong_all3["name"], "已有")

        # ② 仅有 labeledByName + 几何标签 → labeledByName 胜出，name 不变
        strong_lby_geo = _item(control_type="Edit", name="已有", automation_id="NameBox",
                               labeledByName="UIA标签", rect=rect_edit)
        geo_label2 = self._label("几何标签", rect_label)
        bcm._backfill_label_text_to_controls([strong_lby_geo, geo_label2])
        self.assertEqual(strong_lby_geo["labelText"], "UIA标签")
        self.assertEqual(strong_lby_geo["labelRelation"], "uia-labeledby")
        self.assertEqual(strong_lby_geo["name"], "已有")

        # ②b 仅有几何最近邻 → labelText 回填，name 不变
        strong_geo = _item(control_type="Edit", name="已有", automation_id="NameBox",
                           rect=rect_edit)
        geo_label2b = self._label("几何标签", rect_label)
        bcm._backfill_label_text_to_controls([strong_geo, geo_label2b])
        self.assertEqual(strong_geo["labelText"], "几何标签")
        self.assertEqual(strong_geo["labelRelation"], "nearest-text-label")
        self.assertEqual(strong_geo["name"], "已有")

        # ── 弱定位控件 ──────────────────────────────────

        # ③ 三源同时存在 → relatedLabelName 胜出，name 由它回填
        weak_all3 = _item(control_type="Edit",
                          labeledByName="UIA标签", relatedLabelName="区域标签",
                          rect=rect_edit)
        geo_label3 = self._label("几何标签", rect_label)
        bcm._backfill_label_text_to_controls([weak_all3, geo_label3])
        self.assertEqual(weak_all3["labelText"], "区域标签")
        self.assertEqual(weak_all3["labelRelation"], "region-association")
        self.assertEqual(weak_all3["name"], "区域标签")
        self.assertEqual(weak_all3["nameSource"], "relatedlabel-backfill")

        # ③b 仅有 labeledByName + 几何标签 → labeledByName 胜出，name 由它回填
        weak_lby_geo = _item(control_type="Edit",
                             labeledByName="UIA标签", rect=rect_edit)
        geo_label3b = self._label("几何标签", rect_label)
        bcm._backfill_label_text_to_controls([weak_lby_geo, geo_label3b])
        self.assertEqual(weak_lby_geo["labelText"], "UIA标签")
        self.assertEqual(weak_lby_geo["labelRelation"], "uia-labeledby")
        self.assertEqual(weak_lby_geo["name"], "UIA标签")
        self.assertEqual(weak_lby_geo["nameSource"], "labeledby-backfill")

        # ④ 无 labeledByName/relatedLabelName → 几何最近邻回填 labelText 与 name
        weak_geo = _item(control_type="Edit", rect=rect_edit)
        geo_label4 = self._label("几何标签", rect_label)
        bcm._backfill_label_text_to_controls([weak_geo, geo_label4])
        self.assertEqual(weak_geo["labelText"], "几何标签")
        self.assertEqual(weak_geo["labelRelation"], "nearest-text-label")
        self.assertEqual(weak_geo["name"], "几何标签")
        self.assertEqual(weak_geo["nameSource"], "label-backfill")


class TestControlDefinitionFields(unittest.TestCase):
    def test_definition_carries_label_and_interaction_fields(self):
        item = _item(
            control_type="Edit", name="名称", class_name="TextBox",
            patterns=["Value", "Text"], labelText="名称", labelRelation="uia-labeledby",
            labeledByAutomationId="Label_Name", accessKey="", isEnabled=True,
            helpText="请输入名称",
        )
        item["recommendedTargetMethod"] = "name,control_type"
        item["recommendedTargetValue"] = "名称,Edit"
        definition = bcm._build_control_definition_from_flat(item, set())
        self.assertEqual(definition["labelText"], "名称")
        self.assertEqual(definition["labelRelation"], "uia-labeledby")
        self.assertEqual(definition["labeledByAutomationId"], "Label_Name")
        self.assertEqual(definition["supportedPatterns"], ["Value", "Text"])
        self.assertTrue(definition["isEnabled"])
        self.assertEqual(definition["helpText"], "请输入名称")


class TestOrphanContentHostPromotion(unittest.TestCase):
    """孤儿 PART_ContentHost（UIA 树中无父级 TextBox，MTD 名称/描述输入框形态）
    必须提升为 Edit 参与标签关联，而不是被折叠丢弃。"""

    def test_orphan_content_host_promoted_to_edit(self):
        host = _item(control_type="Pane", automation_id="PART_ContentHost",
                     class_name="ScrollViewer", parent_index=99)
        bcm._normalize_textbox_wrappers([host])
        self.assertEqual(host["controlType"], "Edit")
        self.assertEqual(host["controlTypeSource"], "normalized-from-contenthost-orphan")
        self.assertNotIn("foldedIntoParent", host)
        self.assertEqual(host["qualityTier"], "推断输入框")
        self.assertEqual(host["inspectData"]["controlType"], "Edit")

    def test_parented_content_host_still_folded(self):
        parent = _item(control_type="Custom", class_name="TextBox", parent_index=-1)
        host = _item(control_type="Pane", automation_id="PART_ContentHost",
                     class_name="ScrollViewer", parent_index=0)
        bcm._normalize_textbox_wrappers([parent, host])
        self.assertTrue(host["foldedIntoParent"])
        self.assertEqual(host["foldedTargetIndex"], 0)
        self.assertEqual(host["qualityTier"], "建议忽略")


class TestStrongLocatorLabelTextBackfill(unittest.TestCase):
    """强定位控件（有 name+automationId）也要补写 labelText 供消歧与展示，
    但 name 与推荐定位器保持不变。"""

    def test_strong_locator_gets_label_text_only(self):
        edit = _item(control_type="Edit", name="0 m", automation_id="textbox",
                     relatedLabelName="半径")
        edit["recommendedTargetMethod"] = "automation_id,control_type"
        edit["recommendedTargetValue"] = "textbox,Edit"
        bcm._backfill_label_text_to_controls([edit])
        self.assertEqual(edit["labelText"], "半径")
        self.assertEqual(edit["labelRelation"], "region-association")
        # 强定位控件的 name 与推荐定位器不被改动
        self.assertEqual(edit["name"], "0 m")
        self.assertNotIn("nameSource", edit)
        self.assertEqual(edit["recommendedTargetMethod"], "automation_id,control_type")

    def test_weak_locator_still_gets_name_backfill(self):
        edit = _item(control_type="Edit", relatedLabelName="名称")
        bcm._backfill_label_text_to_controls([edit])
        self.assertEqual(edit["labelText"], "名称")
        self.assertEqual(edit["name"], "名称")
        self.assertEqual(edit["nameSource"], "relatedlabel-backfill")


class TestLabelTextDisambiguation(unittest.TestCase):
    """重复定位器消歧：成员各有互不相同的关联标签时优先 label_text 消歧，
    比 found_index 更抗布局与顺序变动。"""

    def _edit(self, runtime_id, label_text, rect_top):
        return {
            "name": "0 m",
            "controlType": "Edit",
            "className": "TextBox",
            "automationId": "textbox",
            "runtimeId": runtime_id,
            "parentIndex": 10,
            "siblingsIndex": 0,
            "labelText": label_text,
            "boundingBox": {"left": 60, "top": rect_top, "right": 336, "bottom": rect_top + 42},
            "recommendedTargetMethod": "automation_id,control_type",
            "recommendedTargetValue": "textbox,Edit",
            "locatorReason": "automation_id + control_type",
        }

    def test_distinct_labels_prefer_label_text(self):
        first = self._edit("[7,1,A]", "半径", 100)
        second = self._edit("[7,1,B]", "载入", 200)
        bcm._disambiguate_duplicate_locators([first, second])
        self.assertEqual(first["recommendedTargetMethod"], "automation_id,control_type,label_text")
        self.assertEqual(first["recommendedTargetValue"], "textbox,Edit,半径")
        self.assertEqual(second["recommendedTargetValue"], "textbox,Edit,载入")
        self.assertIn("label_text消歧", first["locatorReason"])

    def test_missing_or_duplicate_labels_fall_back_to_found_index(self):
        first = self._edit("[7,1,A]", "", 100)
        second = self._edit("[7,1,B]", "载入", 200)
        first["siblingsIndex"] = 0
        second["siblingsIndex"] = 1
        bcm._disambiguate_duplicate_locators([first, second])
        # 有成员缺标签 → 整组退回 found_index，保证同组消歧方式一致
        self.assertEqual(first["recommendedTargetMethod"], "automation_id,control_type,found_index")
        self.assertEqual(second["recommendedTargetMethod"], "automation_id,control_type,found_index")

    def test_related_label_name_used_when_label_text_absent(self):
        first = self._edit("[7,1,A]", "", 100)
        second = self._edit("[7,1,B]", "", 200)
        first["relatedLabelName"] = "纬度"
        second["relatedLabelName"] = "经度"
        bcm._disambiguate_duplicate_locators([first, second])
        self.assertEqual(first["recommendedTargetValue"], "textbox,Edit,纬度")
        self.assertEqual(second["recommendedTargetValue"], "textbox,Edit,经度")


if __name__ == "__main__":
    unittest.main()
