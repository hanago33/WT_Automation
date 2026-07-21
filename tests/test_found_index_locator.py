# encoding: utf-8
"""父链引导 found_index 定位单测：运行时同级序号匹配、并列消歧加分、录制序号保留。

覆盖场景：列表中第 N 项，automationId 版本升级后失效（_5 -> _6），name 通用（"列表项"），
仅靠 automation_id / name 无法唯一定位，需要父容器内同级序号消歧。
"""

import types

import pytest

import wt_flow_locator as L
import flow_recorder_converter as C


class FakeWrapper:
    """模拟 pywinauto wrapper：支持 parent()/children() 以计算同级序号。"""

    def __init__(self, name="", control_type="", class_name="", automation_id="", children=None):
        self._name = name
        self._class_name = class_name
        self._children = list(children or [])
        self._parent = None
        self.element_info = types.SimpleNamespace(
            control_type=control_type,
            localized_control_type="",
            automation_id=automation_id,
            framework_id="",
            help_text="",
            process_id="",
            runtime_id="",
        )
        for child in self._children:
            child._parent = self

    def window_text(self):
        return self._name

    def class_name(self):
        return self._class_name

    def parent(self):
        return self._parent

    def children(self):
        return list(self._children)


def _make_list(item_ids):
    """构造一个 List 容器，内含若干 ListItem（name 全为通用"列表项"）。"""
    items = [
        FakeWrapper(name="列表项", control_type="ListItem", automation_id=aid)
        for aid in item_ids
    ]
    container = FakeWrapper(name="容器", control_type="List", children=items)
    return container, items


def test_get_wrapper_found_index_counts_same_type_siblings():
    _container, items = _make_list(["item_1", "item_2", "item_3", "item_4"])
    assert L.get_wrapper_found_index(items[0], "control_type", "ListItem") == 0
    assert L.get_wrapper_found_index(items[2], "control_type", "ListItem") == 2
    assert L.get_wrapper_found_index(items[3], "control_type", "ListItem") == 3


def test_get_wrapper_found_index_skips_other_types():
    button = FakeWrapper(name="按钮", control_type="Button")
    item_a = FakeWrapper(name="列表项", control_type="ListItem")
    item_b = FakeWrapper(name="列表项", control_type="ListItem")
    FakeWrapper(name="容器", control_type="List", children=[button, item_a, item_b])
    # Button 不计入 ListItem 范围，item_b 仍是第 1 个（0 基）
    assert L.get_wrapper_found_index(item_a, "control_type", "ListItem") == 0
    assert L.get_wrapper_found_index(item_b, "control_type", "ListItem") == 1


def test_get_wrapper_found_index_returns_negative_without_parent():
    orphan = FakeWrapper(name="孤儿", control_type="ListItem")
    assert L.get_wrapper_found_index(orphan, "control_type", "ListItem") == -1


def test_wrapper_matches_locator_found_index_hard_match():
    _container, items = _make_list(["a", "b", "c", "d"])
    # 第 3 个（index 2）匹配 control_type,found_index = ListItem,2
    assert L.wrapper_matches_locator(items[2], "control_type,found_index", "ListItem,2") is True
    assert L.wrapper_matches_locator(items[0], "control_type,found_index", "ListItem,2") is False


def test_wrapper_matches_locator_found_index_bad_int_fails():
    _container, items = _make_list(["a", "b"])
    assert L.wrapper_matches_locator(items[0], "control_type,found_index", "ListItem,x") is False


def test_build_common_locator_candidates_appends_found_index_as_low_priority():
    cd = {
        "inspectData": {
            "automationId": "item_5",
            "name": "列表项",
            "controlType": "ListItem",
            "foundIndex": 2,
        }
    }
    candidates = L.build_common_locator_candidates(cd)
    assert ("control_type,found_index", "ListItem,2") in candidates
    # found_index 必须排在 automation_id / name 之后（最低优先级回退）
    fi_pos = candidates.index(("control_type,found_index", "ListItem,2"))
    name_pos = candidates.index(("name", "列表项"))
    assert fi_pos > name_pos


def test_build_common_locator_candidates_omits_found_index_when_absent():
    cd = {"inspectData": {"automationId": "btnOK", "name": "确定", "controlType": "Button"}}
    candidates = L.build_common_locator_candidates(cd)
    assert not any(method.endswith("found_index") for method, _ in candidates)


def test_found_index_breaks_tie_between_identical_siblings():
    """核心场景：automationId 全部升级失效、name 通用，序号命中者应打分最高。"""
    # 录制的是第 3 项（index 2），automationId 记录为 item_5
    cd = {
        "inspectData": {
            "automationId": "item_5",
            "name": "列表项",
            "controlType": "ListItem",
            "foundIndex": 2,
        }
    }
    # 版本升级后实际 automationId 变成 item_6xxx，全部失效；name 仍是通用"列表项"
    _container, items = _make_list(["item_6a", "item_6b", "item_6c", "item_6d"])
    correct = items[2]
    wrong = items[0]
    score_correct = L.get_control_definition_match_score(correct, cd)
    score_wrong = L.get_control_definition_match_score(wrong, cd)
    assert score_correct > score_wrong
    assert score_correct - score_wrong == 12  # 仅序号消歧加分


def test_found_index_no_bonus_when_index_mismatch():
    cd = {"inspectData": {"name": "列表项", "controlType": "ListItem", "foundIndex": 99}}
    _container, items = _make_list(["a", "b"])
    # 期望序号 99 不存在，无人获得消歧加分 -> 两项打分相同
    assert L.get_control_definition_match_score(items[0], cd) == L.get_control_definition_match_score(items[1], cd)


# --- 转换器侧：录制 #[范围,N] 序号保留 ---

@pytest.mark.parametrize("segment,expected", [
    ("打开||Button#[带号:||ComboBox,0]", 0),
    ("||Image#[||Custom->||Button->||Image,4]%(8.00,-32.00)", 4),
    ("Custom#[1,3]", 3),
    ("列表项||ListItem#[列表项||ListItem,5]", 5),
    ("确定||Button", -1),
    ("||Pane", -1),
])
def test_extract_segment_found_index(segment, expected):
    assert C._extract_segment_found_index(segment) == expected


def test_build_control_definition_preserves_found_index():
    cd = C._build_control_definition(
        "c1",
        "主窗口||Window->列表||List->列表项||ListItem#[列表项||ListItem,5]",
        "主窗口",
        stats={"foundIndexPreserved": 0},
    )
    assert cd["inspectData"].get("foundIndex") == 5


def test_build_control_definition_without_found_index_has_no_key():
    cd = C._build_control_definition(
        "c1", "主窗口||Window->确定||Button", "主窗口", stats=None
    )
    assert "foundIndex" not in cd["inspectData"]
