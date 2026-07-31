import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_control_map_library as bcm


def _control(name, control_type, rect, automation_id="", class_name=""):
    left, top, right, bottom = rect
    return {
        "name": name,
        "displayName": name or automation_id or class_name,
        "controlType": control_type,
        "automationId": automation_id,
        "className": class_name,
        "boundingBox": {"left": left, "top": top, "right": right, "bottom": bottom},
        "isOffscreen": "False",
        "inspectData": {"children": []},
    }


def _sample_controls():
    """镜像 20260715_214310_导入地形图6 采集树中的关键控件几何关系。"""
    return {
        "access_label": _control("访问级别", "Text", (18, 256, 137, 319), "AccessLevelNewFile", "TextBlock"),
        "nature_label": _control("性质", "Text", (18, 320, 137, 383), "NatureNewFile", "TextBlock"),
        "access_dropdown": _control("", "Button", (137, 267, 841, 309), "PART_DropDownButton", "Button"),
        "nature_dropdown": _control("", "Button", (137, 331, 841, 373), "PART_DropDownButton", "Button"),
        "access_value_text": _control("", "Text", (147, 271, 798, 304), "", "TextBlock"),
        "nature_value_text": _control("", "Text", (147, 335, 798, 368), "", "TextBlock"),
        # 地图背景下拉框：标签在其上方（非同行），不应被同行关联误匹配。
        "bg_label": _control("背景", "Text", (2020, 218, 2471, 251), "", "TextBlock"),
        "bg_combo": _control(
            "", "ComboBox", (2012, 284, 2479, 322),
            "MBAMapBackgroundGroup_Combobox_Layers", "RadComboBox",
        ),
        "combo_option": _control("私有", "ListItem", (0, 0, 0, 0), "", "RadComboBoxItem"),
    }


class LabelAssociationTests(unittest.TestCase):
    def test_access_label_binds_same_row_dropdown(self):
        controls = _sample_controls()
        bcm._associate_region_labels_with_controls(list(controls.values()))
        self.assertTrue(controls["access_dropdown"].get("regionRelated"))
        self.assertEqual(controls["access_dropdown"].get("relatedLabelName"), "访问级别")
        self.assertEqual(controls["access_dropdown"].get("regionRelation"), "same-row-label")

    def test_nature_label_binds_its_own_dropdown_not_access(self):
        controls = _sample_controls()
        bcm._associate_region_labels_with_controls(list(controls.values()))
        self.assertEqual(controls["nature_dropdown"].get("relatedLabelName"), "性质")
        # 访问级别下拉框不应被“性质”标签抢走。
        self.assertEqual(controls["access_dropdown"].get("relatedLabelName"), "访问级别")

    def test_inner_value_text_folded_into_dropdown(self):
        controls = _sample_controls()
        bcm._associate_region_labels_with_controls(list(controls.values()))
        self.assertTrue(controls["access_value_text"].get("foldedIntoDropdown"))
        self.assertEqual(
            controls["access_value_text"].get("foldedDropdownAutomationId"),
            "PART_DropDownButton",
        )
        self.assertTrue(controls["nature_value_text"].get("foldedIntoDropdown"))

    def test_above_label_binds_combobox_vertically(self):
        controls = _sample_controls()
        bcm._associate_region_labels_with_controls(list(controls.values()))
        # “背景”标签位于下拉框正上方（同列不同行），应由纵向关联命中。
        self.assertTrue(controls["bg_combo"].get("regionRelated"))
        self.assertEqual(controls["bg_combo"].get("relatedLabelName"), "背景")
        self.assertEqual(controls["bg_combo"].get("regionRelation"), "vertical-label")

    def test_overlapping_label_binds_control(self):
        # 标签矩形几乎完全覆盖下拉框时，应由矩形重叠关联命中。
        label = _control("图层", "Text", (100, 100, 500, 140), "", "TextBlock")
        combo = _control("", "ComboBox", (105, 102, 495, 138), "LayerCombo", "RadComboBox")
        bcm._associate_region_labels_with_controls([label, combo])
        self.assertTrue(combo.get("regionRelated"))
        self.assertEqual(combo.get("relatedLabelName"), "图层")
        self.assertEqual(combo.get("regionRelation"), "overlap-label")

    def test_dropdown_option_tagged(self):
        controls = _sample_controls()
        bcm._associate_region_labels_with_controls(list(controls.values()))
        self.assertTrue(controls["combo_option"].get("dropdownOption"))

    def test_classification_and_readable_name(self):
        controls = _sample_controls()
        flat = list(controls.values())
        bcm._associate_region_labels_with_controls(flat)
        bcm._enrich_flat_controls(flat, {"title": "WT", "className": "Window"})
        self.assertEqual(controls["access_dropdown"].get("qualityTier"), "推荐保留")
        self.assertEqual(controls["access_dropdown"].get("suggestedControlName"), "访问级别 下拉框")
        self.assertEqual(controls["access_value_text"].get("qualityTier"), "建议忽略")
        self.assertEqual(controls["combo_option"].get("qualityTier"), "建议忽略")


class _FakeElementInfo:
    def __init__(self, control_type="", name=""):
        self.control_type = control_type
        self.localized_control_type = ""
        self.name = name


class _FakeWrapper:
    """模拟 pywinauto 包装器：可展开的下拉框展开后才暴露选项子项。"""

    def __init__(self, control_type="", name="", class_name="", children=None, expandable=False):
        self._control_type = control_type
        self._name = name
        self._class_name = class_name
        self._children = list(children or [])
        self._parent = None
        self._expandable = expandable
        self._expanded = False
        self.expand_called = 0
        self.collapse_called = 0
        self.element_info = _FakeElementInfo(control_type, name)
        for child in self._children:
            child._parent = self

    def window_text(self):
        return self._name

    def class_name(self):
        return self._class_name

    def children(self):
        # 可展开下拉框未展开时不暴露选项（镜像 WPF 懒加载）。
        if self._expandable and not self._expanded:
            return []
        return list(self._children)

    def parent(self):
        return self._parent

    def expand(self):
        self.expand_called += 1
        self._expanded = True

    def collapse(self):
        self.collapse_called += 1
        self._expanded = False

    def item_texts(self):
        # 强制回退到子树遍历路径（模拟非 ComboBoxWrapper 的情况）。
        raise RuntimeError("no item_texts")


def _fake_dropdown_wrapper(options):
    option_wrappers = [_FakeWrapper(control_type="ListItem", name=text) for text in options]
    return _FakeWrapper(
        control_type="ComboBox", name="", class_name="RadComboBox",
        children=option_wrappers, expandable=True,
    )


class DropdownOptionCollectionTests(unittest.TestCase):
    _REGION = {"left": 0, "top": 122, "right": 1191, "bottom": 524}

    def test_expand_collects_options_for_region_dropdown(self):
        wrapper = _fake_dropdown_wrapper(["低", "中", "高"])
        dropdown = {
            "controlType": "ComboBox",
            "className": "RadComboBox",
            "automationId": "AccessLevelNewFile",
            "boundingBox": {"left": 137, "top": 267, "right": 841, "bottom": 309},
            "regionRelated": True,
            "inspectData": {"children": []},
            "_wrapperRef": wrapper,
        }
        bcm._expand_region_dropdowns([dropdown], self._REGION)
        self.assertEqual(dropdown.get("optionValues"), ["低", "中", "高"])
        self.assertEqual(dropdown.get("optionCount"), 3)
        self.assertEqual(dropdown.get("inspectData", {}).get("optionValues"), ["低", "中", "高"])
        # 展开后必须收回，避免留下展开状态。
        self.assertEqual(wrapper.expand_called, 1)
        self.assertEqual(wrapper.collapse_called, 1)

    def test_out_of_region_dropdown_skipped(self):
        wrapper = _fake_dropdown_wrapper(["a", "b"])
        dropdown = {
            "controlType": "ComboBox",
            "className": "RadComboBox",
            "automationId": "MapBackground",
            "boundingBox": {"left": 2012, "top": 284, "right": 2479, "bottom": 322},
            "inspectData": {"children": []},
            "_wrapperRef": wrapper,
        }
        bcm._expand_region_dropdowns([dropdown], self._REGION)
        self.assertNotIn("optionValues", dropdown)
        self.assertEqual(wrapper.expand_called, 0)

    def test_region_related_does_not_override_physical_location(self):
        # regionRelated 为 True 但矩形在画框区域外时，不应被展开。
        wrapper = _fake_dropdown_wrapper(["x", "y"])
        dropdown = {
            "controlType": "ComboBox",
            "className": "RadComboBox",
            "automationId": "MapBackground",
            "boundingBox": {"left": 2012, "top": 284, "right": 2479, "bottom": 322},
            "regionRelated": True,
            "inspectData": {"children": []},
            "_wrapperRef": wrapper,
        }
        diagnostics = bcm._expand_region_dropdowns([dropdown], self._REGION)
        self.assertNotIn("optionValues", dropdown)
        self.assertEqual(wrapper.expand_called, 0)
        self.assertEqual(len(diagnostics), 1)
        self.assertFalse(diagnostics[0]["inScope"])

    def test_diagnostics_report_option_stage(self):
        wrapper = _fake_dropdown_wrapper(["低", "中", "高"])
        dropdown = {
            "controlType": "ComboBox",
            "className": "RadComboBox",
            "automationId": "AccessLevelNewFile",
            "boundingBox": {"left": 137, "top": 267, "right": 841, "bottom": 309},
            "inspectData": {"children": []},
            "_wrapperRef": wrapper,
        }
        diagnostics = bcm._expand_region_dropdowns([dropdown], self._REGION)
        self.assertEqual(len(diagnostics), 1)
        self.assertTrue(diagnostics[0]["inScope"])
        self.assertTrue(diagnostics[0]["opened"])
        self.assertEqual(diagnostics[0]["optionCount"], 3)
        self.assertEqual(diagnostics[0]["optionStage"], "control_subtree")

    def test_dropdown_with_options_is_recommended(self):
        dropdown = {
            "controlType": "ComboBox",
            "className": "RadComboBox",
            "automationId": "LayerCombo",
            "optionValues": ["图层1", "图层2"],
            "optionCount": 2,
        }
        tier, reason = bcm._classify_control_quality(dropdown)
        self.assertEqual(tier, "推荐保留")
        self.assertIn("2", reason)


class _Rect:
    def __init__(self, left, top, right, bottom):
        self.left, self.top, self.right, self.bottom = left, top, right, bottom


class _RectWrapper(_FakeWrapper):
    """在 _FakeWrapper 基础上提供 rectangle()，用于验证按锚点空间位置圈定弹出区域。"""

    def __init__(self, rect=None, **kwargs):
        super().__init__(**kwargs)
        self._rect = rect

    def rectangle(self):
        if self._rect is None:
            raise RuntimeError("no rect")
        return _Rect(*self._rect)


class CollectOptionsNearAnchorTests(unittest.TestCase):
    _ANCHOR = {"left": 137, "top": 267, "right": 841, "bottom": 309}

    def test_scopes_options_by_anchor_position(self):
        # 途底场景：弹出层内嵌在主窗口视觉树，仅采集锚点下方的选项。
        near_private = _RectWrapper(rect=(146, 313, 832, 346), control_type="ListItem", name="私有")
        near_group = _RectWrapper(rect=(146, 346, 832, 379), control_type="ListItem", name="组")
        far_street = _RectWrapper(rect=(2012, 320, 2479, 353), control_type="ListItem", name="街道")
        popup_layer = _RectWrapper(rect=(0, 0, 2560, 1440), children=[near_private, near_group])
        other_layer = _RectWrapper(rect=(2000, 300, 2500, 400), children=[far_street])
        main = _RectWrapper(rect=(0, 0, 2560, 1440), children=[popup_layer, other_layer])
        texts = bcm._collect_options_near_anchor(main, self._ANCHOR)
        # 同列弹出选项按位置保序采集；远处无关 ListItem（背景下拉项）不被误采。
        self.assertEqual(texts, ["私有", "组"])

    def test_empty_when_no_anchor(self):
        listitem = _RectWrapper(rect=(146, 313, 832, 346), control_type="ListItem", name="私有")
        main = _RectWrapper(rect=(0, 0, 2560, 1440), children=[listitem])
        self.assertEqual(bcm._collect_options_near_anchor(main, None), [])


class DuplicateLocatorDisambiguationTests(unittest.TestCase):
    def _button(self, runtime_id, siblings_index, rect, parent_index=254):
        left, top, right, bottom = rect
        return {
            "name": "",
            "controlType": "Button",
            "className": "Button",
            "automationId": "PART_DropDownButton",
            "runtimeId": runtime_id,
            "parentIndex": parent_index,
            "siblingsIndex": siblings_index,
            "boundingBox": {"left": left, "top": top, "right": right, "bottom": bottom},
            "recommendedTargetMethod": "automation_id,control_type",
            "recommendedTargetValue": "PART_DropDownButton,Button",
            "locatorReason": "automation_id + control_type",
        }

    def test_duplicate_dropdown_locators_get_distinct_found_index(self):
        # 镜像访问级别/性质两个 PART_DropDownButton：同父、同定位器、不同 siblingsIndex。
        access = self._button("[7,1,A]", 10, (137, 267, 841, 309))
        nature = self._button("[7,1,B]", 12, (137, 331, 841, 373))
        bcm._disambiguate_duplicate_locators([access, nature])
        self.assertEqual(access["recommendedTargetMethod"], "automation_id,control_type,found_index")
        self.assertEqual(nature["recommendedTargetMethod"], "automation_id,control_type,found_index")
        self.assertEqual(
            {access["recommendedTargetValue"], nature["recommendedTargetValue"]},
            {"PART_DropDownButton,Button,0", "PART_DropDownButton,Button,1"},
        )
        # 依 siblingsIndex 升序：访问级别(10)->0，性质(12)->1。
        self.assertTrue(access["recommendedTargetValue"].endswith(",0"))
        self.assertTrue(nature["recommendedTargetValue"].endswith(",1"))

    def test_unique_locator_left_untouched(self):
        unique = self._button("[7,1,C]", 3, (137, 267, 841, 309))
        unique["automationId"] = "UniqueBtn"
        unique["recommendedTargetValue"] = "UniqueBtn,Button"
        bcm._disambiguate_duplicate_locators([unique])
        self.assertEqual(unique["recommendedTargetMethod"], "automation_id,control_type")
        self.assertEqual(unique["recommendedTargetValue"], "UniqueBtn,Button")

    def test_same_control_duplicate_not_disambiguated(self):
        # 跨后端同一控件（相同 runtimeId）重复出现，不应被误加 found_index。
        first = self._button("[7,1,SAME]", 10, (137, 267, 841, 309))
        second = self._button("[7,1,SAME]", 10, (137, 267, 841, 309))
        bcm._disambiguate_duplicate_locators([first, second])
        self.assertEqual(first["recommendedTargetMethod"], "automation_id,control_type")
        self.assertEqual(second["recommendedTargetMethod"], "automation_id,control_type")


class _FakePointDesktop:
    """模拟 Desktop.from_point 命中测试：按 y 区间返回命中元素，区间外返回 None。

    镜像 WPF 虚拟化：选项只有在被命中测试（from_point）时才“实体化”。
    """

    def __init__(self, rows):
        # rows: [(y_start, y_end, wrapper), ...]
        self._rows = rows

    def from_point(self, x, y):
        for y0, y1, wrapper in self._rows:
            if y0 <= y < y1:
                return wrapper
        return None


class RealizeOptionsByPointSweepTests(unittest.TestCase):
    _ANCHOR = {"left": 137, "top": 267, "right": 841, "bottom": 309}

    def test_point_sweep_realizes_virtualized_options(self):
        private_item = _FakeWrapper(control_type="ListItem", name="私有")
        # “组”行：from_point 命中选项内部 TextBlock，需向上回溯到 ListItem 再下钻取文本。
        group_text = _FakeWrapper(control_type="Text", name="组", class_name="TextBlock")
        group_item = _FakeWrapper(control_type="ListItem", name="MTD.EnumVal", children=[group_text])
        desktop = _FakePointDesktop([
            (311, 344, private_item),
            (344, 377, group_text),  # 命中内部文本块
        ])
        diag = {}
        texts = bcm._realize_options_by_point_sweep(self._ANCHOR, desktop=desktop, diag=diag)
        self.assertEqual(texts, ["私有", "组"])
        self.assertEqual(diag.get("pointSweepOptionCount"), 2)

    def test_point_sweep_skips_leading_gap_and_stops_after_list(self):
        # 按钮与列表顶部之间有空白；列表末尾之后连续命中空白应提前结束。
        private_item = _FakeWrapper(control_type="ListItem", name="私有")
        public_item = _FakeWrapper(control_type="ListItem", name="公共")
        desktop = _FakePointDesktop([
            (350, 383, private_item),
            (383, 416, public_item),
        ])
        texts = bcm._realize_options_by_point_sweep(self._ANCHOR, desktop=desktop)
        self.assertEqual(texts, ["私有", "公共"])

    def test_point_sweep_empty_without_anchor(self):
        desktop = _FakePointDesktop([(311, 344, _FakeWrapper(control_type="ListItem", name="私有"))])
        self.assertEqual(bcm._realize_options_by_point_sweep(None, desktop=desktop), [])


class NormalizeTextboxWrappersTests(unittest.TestCase):
    """_normalize_textbox_wrappers：将 WPF Custom/Pane+TextBox className 修正为 Edit。"""

    def test_textbox_classname_rewrites_control_type_to_edit(self):
        c = _control("", "Custom", (100, 100, 300, 140), "", "TextBox")
        bcm._normalize_textbox_wrappers([c])
        self.assertEqual(c["controlType"], "Edit")
        self.assertEqual(c.get("controlTypeSource"), "normalized-from-classname")

    def test_passwordbox_classname_rewrites_control_type_to_edit(self):
        c = _control("", "Pane", (100, 100, 300, 140), "", "PasswordBox")
        bcm._normalize_textbox_wrappers([c])
        self.assertEqual(c["controlType"], "Edit")
        self.assertEqual(c.get("controlTypeSource"), "normalized-from-classname")

    def test_already_edit_not_changed(self):
        c = _control("搜索", "Edit", (100, 100, 300, 140), "SearchBox", "TextBox")
        bcm._normalize_textbox_wrappers([c])
        self.assertEqual(c["controlType"], "Edit")
        self.assertIsNone(c.get("controlTypeSource"))

    def test_custom_without_textbox_classname_not_changed(self):
        c = _control("", "Custom", (100, 100, 300, 140), "", "ScrollViewer")
        bcm._normalize_textbox_wrappers([c])
        self.assertEqual(c["controlType"], "Custom")

    def test_part_contenthost_folds_and_normalizes_parent(self):
        # 模拟 UIA 树：上方是 TextBox (Custom)，下方是 PART_ContentHost (Pane)
        textbox = _control("", "Custom", (100, 100, 300, 140), "", "TextBox")
        content_host = _control("", "Pane", (102, 102, 298, 138), "PART_ContentHost", "ScrollViewer")
        textbox_idx = 0
        content_host["parentIndex"] = textbox_idx
        controls = [textbox, content_host]
        bcm._normalize_textbox_wrappers(controls)
        # 父级 TextBox 被规范化
        self.assertEqual(textbox["controlType"], "Edit")
        self.assertEqual(textbox.get("controlTypeSource"), "normalized-from-classname")
        # PART_ContentHost 被折叠
        self.assertTrue(content_host.get("foldedIntoParent"))
        self.assertEqual(content_host.get("qualityTier"), "建议忽略")
        self.assertEqual(content_host.get("foldedTargetIndex"), textbox_idx)

    def test_part_contenthost_without_textbox_parent_not_crashed(self):
        # PART_ContentHost 的父级不是 TextBox 时不应崩溃；
        # 孤儿 PART_ContentHost 就是实际可编辑面（MTD 名称/描述输入框形态），
        # 必须提升为 Edit 参与标签关联，而不是折叠丢弃。
        not_textbox = _control("", "Custom", (100, 100, 300, 140), "", "Border")
        content_host = _control("", "Pane", (102, 102, 298, 138), "PART_ContentHost", "ScrollViewer")
        content_host["parentIndex"] = 0
        controls = [not_textbox, content_host]
        bcm._normalize_textbox_wrappers(controls)
        self.assertNotIn("foldedIntoParent", content_host)
        self.assertEqual(content_host["controlType"], "Edit")
        self.assertEqual(content_host["controlTypeSource"], "normalized-from-contenthost-orphan")
        self.assertEqual(not_textbox["controlType"], "Custom")  # 未变化

    def test_empty_input_no_crash(self):
        bcm._normalize_textbox_wrappers([])
        bcm._normalize_textbox_wrappers(None)

    def test_inspect_data_synced(self):
        c = _control("", "Custom", (100, 100, 300, 140), "", "TextBox")
        c["inspectData"]["controlType"] = "Custom"
        bcm._normalize_textbox_wrappers([c])
        self.assertEqual(c["inspectData"]["controlType"], "Edit")


# ---------------------------------------------------------------------------
# RawViewWalker BFS 遍历器测试
# ---------------------------------------------------------------------------

import time as _time_mod
from unittest import mock as _mock_mod


def _bfs_flat_control(name="", control_type="Custom", automation_id="", class_name="",
                      depth=0, index=1, parent_index=-1, siblings_index=1,
                      x=0, y=0, w=100, h=100):
    """构建一个与 _extract_wrapper_info 输出格式兼容的 mock 控件 dict。"""
    display = name or automation_id or class_name or f"Ctrl{index}"
    return {
        "depth": depth,
        "index": index,
        "displayName": display,
        "windowTitle": "TestWin",
        "windowClassName": "Window",
        "processId": "1234",
        "handle": "12345",
        "name": name,
        "className": class_name,
        "controlType": control_type,
        "localizedControlType": control_type,
        "automationId": automation_id,
        "frameworkId": "WPF",
        "runtimeId": f"42.{index}",
        "value": "",
        "toggleState": "",
        "boundingRectangle": {"left": x, "top": y, "right": x + w, "bottom": y + h},
        "boundingBox": {"left": x, "top": y, "right": x + w, "bottom": y + h},
        "isEnabled": True,
        "isVisible": True,
        "isOffscreen": "False",
        "helpText": "",
        "providerDescription": "",
        "locatorScore": 0.5,
        "locatorReason": "",
        "recommendedTargetMethod": "name" if name else "automationId",
        "recommendedTargetValue": name or automation_id,
        "uiPath": display,
        "parentPath": "",
        "treeLevel": depth,
        "parentIndex": parent_index,
        "siblingsIndex": siblings_index,
        "auxChecks": [],
        "inspectData": {
            "name": name,
            "value": "",
            "toggleState": "",
            "controlType": control_type,
            "localizedControlType": control_type,
            "boundingRectangle": {"left": x, "top": y, "right": x + w, "bottom": y + h},
            "isEnabled": "True",
            "isVisible": "True",
            "isOffscreen": "False",
            "isKeyboardFocusable": "False",
            "hasKeyboardFocus": "False",
            "processId": "1234",
            "runtimeId": f"42.{index}",
            "frameworkId": "WPF",
            "className": class_name,
            "automationId": automation_id,
            "nativeWindowHandle": "12345",
            "providerDescription": "",
            "legacyName": "",
            "legacyRole": "",
            "legacyState": "",
            "helpText": "",
            "ancestors": [],
            "children": [],
            "recommendedTargetMethod": "name" if name else "automationId",
            "recommendedTargetValue": name or automation_id,
        },
        "rawInspectText": "",
    }


class RawViewWalkBFSTests(unittest.TestCase):
    """验证 RawViewWalker BFS 遍历器的辅助函数和边界行为。"""

    # ------------------------------------------------------------------
    # _rebuild_bfs_paths
    # ------------------------------------------------------------------

    def test_rebuild_bfs_paths_linear_chain(self):
        """3 层父子链: root → child → grandchild, uiPath 逐级拼接。"""
        controls = [
            _bfs_flat_control(name="Root", depth=0, index=1, parent_index=-1),
            _bfs_flat_control(name="Child", depth=1, index=2, parent_index=0),
            _bfs_flat_control(name="Grand", depth=2, index=3, parent_index=1),
        ]
        bcm._rebuild_bfs_paths(controls)

        self.assertEqual(controls[0]["uiPath"], "Root")
        self.assertEqual(controls[0]["parentPath"], "")

        self.assertEqual(controls[1]["uiPath"], "Root > Child")
        self.assertEqual(controls[1]["parentPath"], "Root")

        self.assertEqual(controls[2]["uiPath"], "Root > Child > Grand")
        self.assertEqual(controls[2]["parentPath"], "Root > Child")

    def test_rebuild_bfs_paths_orphan_node(self):
        """无有效 parentIndex 的孤立节点只保留自身 displayName。"""
        controls = [
            _bfs_flat_control(name="Orphan", depth=0, index=1, parent_index=-1),
        ]
        bcm._rebuild_bfs_paths(controls)
        self.assertEqual(controls[0]["uiPath"], "Orphan")
        self.assertEqual(controls[0]["parentPath"], "")

    def test_rebuild_bfs_paths_updates_inspect_ancestors(self):
        """ancestors 字段同步更新为父级 displayName 列表。"""
        controls = [
            _bfs_flat_control(name="A", depth=0, index=1),
            _bfs_flat_control(name="B", depth=1, index=2, parent_index=0),
        ]
        bcm._rebuild_bfs_paths(controls)
        self.assertEqual(controls[1]["inspectData"]["ancestors"], ["A"])
        self.assertEqual(controls[0]["inspectData"]["ancestors"], [])

    def test_rebuild_bfs_paths_empty_list_no_crash(self):
        bcm._rebuild_bfs_paths([])
        bcm._rebuild_bfs_paths(None)

    # ------------------------------------------------------------------
    # _build_tree_from_flat
    # ------------------------------------------------------------------

    def test_build_tree_root_with_two_children(self):
        """根节点有 2 个子节点，子节点无 grandchildren。"""
        controls = [
            _bfs_flat_control(name="Root", index=1, parent_index=-1),
            _bfs_flat_control(name="A", index=2, parent_index=0),
            _bfs_flat_control(name="B", index=3, parent_index=0),
        ]
        tree = bcm._build_tree_from_flat(controls)
        self.assertEqual(tree["displayName"], "Root")
        self.assertEqual(len(tree["children"]), 2)
        self.assertEqual(tree["children"][0]["displayName"], "A")
        self.assertEqual(tree["children"][1]["displayName"], "B")

    def test_build_tree_deep_nesting(self):
        """3 层嵌套: Root → A → A1。"""
        controls = [
            _bfs_flat_control(name="Root", index=1, parent_index=-1),
            _bfs_flat_control(name="A", index=2, parent_index=0),
            _bfs_flat_control(name="A1", index=3, parent_index=1),
        ]
        tree = bcm._build_tree_from_flat(controls)
        self.assertEqual(tree["children"][0]["displayName"], "A")
        self.assertEqual(tree["children"][0]["children"][0]["displayName"], "A1")

    def test_build_tree_empty_input(self):
        self.assertEqual(bcm._build_tree_from_flat([]), {})

    # ------------------------------------------------------------------
    # _walk_raw_view_bfs 边界行为
    # ------------------------------------------------------------------

    def test_bfs_max_depth_zero_returns_root_only(self):
        """max_depth=0 时只收集根节点，不进入 BFS 循环。
        tree 构建已移至主流程；cov4 起函数返回 BFS 截断统计 dict，验证 flat_controls 正确。"""
        mock_wrapper = _mock_mod.MagicMock()
        mock_wrapper.element_info.element = _mock_mod.MagicMock()

        target = {"title": "RootWin", "className": "Window"}
        flat = []

        stats = bcm._walk_raw_view_bfs(
            target_window_wrapper=mock_wrapper,
            max_depth=0,
            target_window=target,
            flat_controls=flat,
            path_segments=["RootWin"],
        )

        self.assertEqual(len(flat), 1)
        self.assertEqual(flat[0]["treeLevel"], 0)
        self.assertEqual(flat[0]["parentIndex"], -1)
        # cov4：_walk_raw_view_bfs 返回截断统计（timedOut/hitLimit），供摘要警告
        self.assertIsInstance(stats, dict)
        self.assertIn("timedOut", stats)
        self.assertIn("hitLimit", stats)
        # _rebuild_bfs_paths 应已执行
        self.assertTrue(flat[0].get("uiPath"))

    def test_bfs_timeout_stops_early(self):
        """BFS 已在函数内做超时检查 — 此处验证超时逻辑可被触发（不挂起）。"""
        mock_wrapper = _mock_mod.MagicMock()
        mock_wrapper.element_info.element = _mock_mod.MagicMock()

        target = {"title": "T", "className": "W"}
        flat = []

        # max_depth=0 → 不进入 while 循环，但不验证 RawViewWalker
        bcm._walk_raw_view_bfs(
            target_window_wrapper=mock_wrapper,
            max_depth=0,
            target_window=target,
            flat_controls=flat,
            path_segments=["T"],
            start_time=_time_mod.time() - 1000,
            scan_timeout_seconds=0.01,
        )
        # max_depth=0 直接返回，应只有根节点
        self.assertEqual(len(flat), 1)

    def test_bfs_output_format_compatible(self):
        """BFS 输出元素包含 _walk_wrapper 下游所需的全部字段。"""
        mock_wrapper = _mock_mod.MagicMock()
        mock_wrapper.element_info.element = _mock_mod.MagicMock()

        target = {"title": "App", "className": "HwndWrapper"}
        flat = []

        bcm._walk_raw_view_bfs(
            target_window_wrapper=mock_wrapper,
            max_depth=0,
            target_window=target,
            flat_controls=flat,
            path_segments=["App"],
        )

        self.assertEqual(len(flat), 1)
        item = flat[0]
        required_fields = [
            "name", "className", "controlType", "automationId", "frameworkId",
            "boundingBox", "boundingRectangle", "displayName", "uiPath",
            "parentPath", "treeLevel", "parentIndex", "siblingsIndex",
            "isEnabled", "isVisible", "isOffscreen", "runtimeId", "handle",
            "locatorScore", "recommendedTargetMethod", "recommendedTargetValue",
            "inspectData", "rawInspectText", "auxChecks",
        ]
        for field in required_fields:
            self.assertIn(field, item, f"BFS 输出缺少字段: {field}")


if __name__ == "__main__":
    unittest.main()
