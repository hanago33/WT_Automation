# encoding: utf-8
"""多选下拉（Telerik MTDGroupComboBoxMultiSelection）选项文本消歧测试。

覆盖场景（对照组：flow_definition_发送综合计算.json step_6 / step_cfg_draw_select）：
- 所有下拉选项共享同一 automationId（MTDGroupComboBoxMultiSelection_ComboBoxItem），
  仅 automation_id,control_type 定位时第一个命中项（默认配置）胜出 → 勾错项；
- targetMethod 加 label_text 后，label 不匹配的候选被硬否决（score == -1），
  只有文本匹配项通过；
- 引擎兜底：SelectionItemPattern 选中错项后，按期望文本在兄弟项中重选，
  重选无果时中止步骤（不再静默 success）。
"""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator


class FakeWrapper:
    """满足 get_wrapper_* 访问器的最小 UIA wrapper 替身。"""

    def __init__(self, name="", automation_id="", control_type="ListItem",
                 class_name="RadComboBoxItem", localized_control_type="list item",
                 framework_id="WPF"):
        self._name = name
        self._class_name = class_name
        self.element_info = SimpleNamespace(
            automation_id=automation_id,
            control_type=control_type,
            localized_control_type=localized_control_type,
            framework_id=framework_id,
            help_text="",
            process_id="123",
        )

    def window_text(self):
        return self._name

    def class_name(self):
        return self._class_name


def make_control_def(label_in_method=True, expect_name="不带绘图"):
    methods = ["automation_id", "control_type"] + (["label_text"] if label_in_method else [])
    values = ["MTDGroupComboBoxMultiSelection_ComboBoxItem", "ListItem"] + (
        [expect_name] if label_in_method else []
    )
    return {
        "id": "control_map_1261",
        "name": expect_name,
        "targetMethod": ",".join(methods),
        "targetValue": ",".join(values),
        "labelText": expect_name,
        "relatedLabelName": expect_name,
        "windowTitle": "*",
        "uiPath": "",
        "auxChecks": [],
        "inspectData": {
            "automationId": "MTDGroupComboBoxMultiSelection_ComboBoxItem",
            "name": expect_name,
            "controlType": "ListItem",
            "localizedControlType": "list item",
            "className": "RadComboBoxItem",
            "frameworkId": "WPF",
        },
    }


class LabelTextVetoTests(unittest.TestCase):
    """label_text 硬消歧：不匹配的共享 automationId 项必须被否决。"""

    def setUp(self):
        self.fake_label_match = lambda wrapper, label_text, allow_full_scan=True: bool(
            label_text and label_text in (getattr(wrapper, "_name", None) or "")
        )
        self.def_fixed = make_control_def(label_in_method=True)
        self.def_legacy = make_control_def(label_in_method=False)

    def _score(self, wrapper, control_def):
        with patch.object(wt_flow_locator, "wrapper_matches_label_text", side_effect=self.fake_label_match):
            return wt_flow_locator.get_control_definition_match_score(wrapper, control_def)

    def test_fixed_def_rejects_first_dropdown_item(self):
        # 下拉第一项"默认配置"：同 automationId 同类型，但 label 不匹配 → 硬否决
        wrapper = FakeWrapper(name="默认配置", automation_id="MTDGroupComboBoxMultiSelection_ComboBoxItem")
        self.assertEqual(self._score(wrapper, self.def_fixed), -1)
        with patch.object(wt_flow_locator, "wrapper_matches_label_text", side_effect=self.fake_label_match):
            self.assertFalse(wt_flow_locator.wrapper_matches_control_definition(wrapper, self.def_fixed))

    def test_fixed_def_rejects_other_options(self):
        for name in ("带绘图", "没绘图", "综合1"):
            wrapper = FakeWrapper(name=name, automation_id="MTDGroupComboBoxMultiSelection_ComboBoxItem")
            self.assertEqual(
                self._score(wrapper, self.def_fixed), -1,
                "label 不匹配的选项不应通过 %r" % name,
            )

    def test_fixed_def_hits_intended_item(self):
        wrapper = FakeWrapper(name="不带绘图", automation_id="MTDGroupComboBoxMultiSelection_ComboBoxItem")
        score = self._score(wrapper, self.def_fixed)
        self.assertGreater(score, 0)
        with patch.object(wt_flow_locator, "wrapper_matches_label_text", side_effect=self.fake_label_match):
            self.assertTrue(wt_flow_locator.wrapper_matches_control_definition(wrapper, self.def_fixed))

    def test_draw_select_fixed_def_hits_its_item(self):
        control_def = make_control_def(label_in_method=True, expect_name="带绘图")
        wrapper = FakeWrapper(name="带绘图", automation_id="MTDGroupComboBoxMultiSelection_ComboBoxItem")
        with patch.object(wt_flow_locator, "wrapper_matches_label_text", side_effect=self.fake_label_match):
            self.assertTrue(wt_flow_locator.wrapper_matches_control_definition(wrapper, control_def))

    def test_legacy_def_still_matches_first_item(self):
        # 旧配置（无 label_text）：第一项"默认配置"仍会通过 —— 说明为什么旧配置会勾错
        wrapper = FakeWrapper(name="默认配置", automation_id="MTDGroupComboBoxMultiSelection_ComboBoxItem")
        self.assertGreater(self._score(wrapper, self.def_legacy), 0)


class FakeItem:
    """满足 _find_list_item_with_text 访问的最小列表项替身。"""

    def __init__(self, name, children_texts=(), handle=None, container=None):
        self._name = name
        self._children_texts = list(children_texts)
        self._container = container
        self.element_info = SimpleNamespace(
            handle=handle if handle is not None else id(self),
            control_type="ListItem",
            automation_id="MTDGroupComboBoxMultiSelection_ComboBoxItem",
        )

    def window_text(self):
        return self._name

    def class_name(self):
        return "RadComboBoxItem"

    def parent(self):
        return self._container

    def descendants(self, control_type=None, **kwargs):
        if control_type == "Text":
            return [FakeText(t) for t in self._children_texts]
        return []

    def select(self):
        pass


class FakeText:
    def __init__(self, text):
        self._text = text

    def window_text(self):
        return self._text


class FakeContainer:
    def __init__(self, children):
        self._children = children

    def descendants(self, control_type=None, **kwargs):
        if control_type == "ListItem":
            return list(self._children)
        return []


class ReselectByTextTests(unittest.TestCase):
    """选中错项后按期望文本在兄弟项中重选。"""

    def _make_dropdown(self, anchor_name="默认配置"):
        items = [
            FakeItem("默认配置", children_texts=["默认配置"], handle=101),
            FakeItem("不带绘图", children_texts=["不带绘图"], handle=102),
            FakeItem("带绘图", children_texts=["带绘图"], handle=103),
        ]
        container = FakeContainer(items)
        for item in items:
            item._container = container
        anchor = next(item for item in items if item._name == anchor_name)
        return anchor, items

    def test_reselect_picks_matching_sibling(self):
        anchor, items = self._make_dropdown()
        found = wt_flow_locator._find_list_item_with_text(anchor, "不带绘图")
        self.assertIsNotNone(found)
        self.assertIs(found, items[1])

    def test_reselect_skips_anchor_itself(self):
        # 锚点自身文本恰好含期望文本时，不应重选自身（自身已证明是错项）
        anchor_wrong = FakeItem("不带绘图", children_texts=["不带绘图"], handle=201)
        container = FakeContainer([anchor_wrong, FakeItem("带绘图", children_texts=["带绘图"], handle=202)])
        anchor_wrong._container = container
        found = wt_flow_locator._find_list_item_with_text(anchor_wrong, "不带绘图")
        self.assertIsNone(found)

    def test_reselect_none_when_absent(self):
        anchor, items = self._make_dropdown()
        found = wt_flow_locator._find_list_item_with_text(anchor, "不存在的选项")
        self.assertIsNone(found)

    def test_reselect_matches_by_child_text_when_own_name_empty(self):
        # 自身 Name 为空的项（文本在子节点）：靠子文本命中
        items = [
            FakeItem("", children_texts=["默认配置"], handle=301),
            FakeItem("", children_texts=["不带绘图"], handle=302),
        ]
        container = FakeContainer(items)
        for item in items:
            item._container = container
        found = wt_flow_locator._find_list_item_with_text(items[0], "不带绘图")
        self.assertIs(found, items[1])


class FakeComboBox:
    """带 collapse() 记录的最小 ComboBox 替身。"""

    def __init__(self, collapse_raises=False):
        self.collapse_calls = 0
        self._collapse_raises = collapse_raises
        self.element_info = SimpleNamespace(
            control_type="ComboBox",
            localized_control_type="combo box",
            automation_id="MUPWRAAnalysisEditorView_RadComboBox_SelectLayout",
        )

    def parent(self):
        return None

    def collapse(self):
        self.collapse_calls += 1
        if self._collapse_raises:
            raise RuntimeError("collapse unavailable")


class FakeChainItem:
    """可挂在 ComboBox 祖先链下的列表项替身。"""

    def __init__(self, parent=None, control_type="ListItem"):
        self._parent = parent
        self.element_info = SimpleNamespace(
            control_type=control_type,
            localized_control_type="list item",
            automation_id="MTDGroupComboBoxMultiSelection_ComboBoxItem",
        )

    def parent(self):
        return self._parent


class CollapsePopupTests(unittest.TestCase):
    """选中下拉项后收起父级 ComboBox 弹出层。"""

    def test_collapse_finds_combo_ancestor(self):
        combo = FakeComboBox()
        item = FakeChainItem(parent=combo)
        wt_flow_locator._collapse_parent_combo_popup(item, step_id="step_6", control_id="control_map_1261")
        self.assertEqual(combo.collapse_calls, 1)

    def test_collapse_walks_multi_level_chain(self):
        combo = FakeComboBox()
        custom = FakeChainItem(parent=combo, control_type="Custom")
        item = FakeChainItem(parent=custom)
        wt_flow_locator._collapse_parent_combo_popup(item)
        self.assertEqual(combo.collapse_calls, 1)

    def test_collapse_no_combo_ancestor_is_noop(self):
        panel = FakeChainItem(parent=None, control_type="Pane")
        item = FakeChainItem(parent=panel)
        # 不抛异常即为通过（列表项/普通卡片无 ComboBox 祖先时不得影响）
        wt_flow_locator._collapse_parent_combo_popup(item)

    def test_collapse_error_is_swallowed(self):
        combo = FakeComboBox(collapse_raises=True)
        item = FakeChainItem(parent=combo)
        wt_flow_locator._collapse_parent_combo_popup(item, step_id="step_6", control_id="x")
        self.assertEqual(combo.collapse_calls, 1)

    def test_collapse_none_item_is_noop(self):
        wt_flow_locator._collapse_parent_combo_popup(None)

    def test_collapse_bounded_by_max_depth(self):
        # 祖先链超过 max_depth 且无 ComboBox 时不得无限上溯崩溃
        chain_item = None
        for _ in range(15):
            chain_item = FakeChainItem(parent=chain_item)
        wt_flow_locator._collapse_parent_combo_popup(chain_item, max_depth=10)


if __name__ == "__main__":
    unittest.main()