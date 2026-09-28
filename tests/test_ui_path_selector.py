# encoding: utf-8
"""#7 唯一选择器单测：定位器 ui_path 硬匹配 + 转换器唯一选择器生成。"""

import types

import pytest

import wt_flow_locator as L
import flow_recorder_converter as C


class FakeWrapper:
    def __init__(self, name, ctype, parent=None):
        self._name = name
        self._type = ctype
        self._parent = parent

    def parent(self):
        return self._parent


@pytest.fixture(autouse=True)
def _patch_wrapper_getters(monkeypatch):
    monkeypatch.setattr(L, "get_wrapper_text", lambda w: w._name)
    monkeypatch.setattr(L, "get_wrapper_control_type", lambda w: w._type)


def _chain():
    win = FakeWrapper("主窗口", "Window")
    grp = FakeWrapper("确认组", "Group", parent=win)
    btn = FakeWrapper("确定", "Button", parent=grp)
    return btn


def test_parse_recorded_uipath_handles_separators_and_coords():
    p = "主窗口||Window->确认组||Group->确定||Button%(-12,-34)"
    segs = L._parse_recorded_uipath(p)
    assert segs[0] == ("主窗口", "Window")  # 根->叶顺序
    assert segs[-1] == ("确定", "Button")
    assert len(segs) == 3


def test_ui_path_match_when_path_tail_aligns():
    btn = _chain()
    path = "主窗口||Window->确认组||Group->确定||Button"
    assert L.wrapper_matches_locator(btn, "ui_path", path) is True


def test_ui_path_no_match_when_ancestor_diverges():
    btn = _chain()
    # 中间祖先名称不一致
    path = "主窗口||Window->其它组||Group->确定||Button"
    assert L.wrapper_matches_locator(btn, "ui_path", path) is False


def test_ui_path_no_match_when_recorded_longer_than_actual():
    leaf = FakeWrapper("确定", "Button")  # 无父链
    path = "主窗口||Window->确认组||Group->确定||Button"
    assert L.wrapper_matches_locator(leaf, "ui_path", path) is False


def test_build_common_locator_candidates_includes_ui_path():
    cd = {"uiPath": "主窗口||Window->确定||Button", "inspectData": {"name": "确定", "controlType": "Button"}}
    candidates = L.build_common_locator_candidates(cd)
    assert ("ui_path", "主窗口||Window->确定||Button") in candidates


def test_build_common_locator_candidates_skips_shallow_ui_path():
    cd = {"uiPath": "确定||Button", "inspectData": {"name": "确定", "controlType": "Button"}}
    candidates = L.build_common_locator_candidates(cd)
    assert not any(method == "ui_path" for method, _ in candidates)


def test_build_ancestor_signatures_excludes_leaf():
    sigs = C._build_ancestor_signatures("主窗口||Window->确认组||Group->确定||Button")
    assert "确定" not in " ".join(sigs)
    assert any("确认组" in s for s in sigs)
    assert any("主窗口" in s for s in sigs)


def test_build_control_definition_fixes_automation_id_and_recommends_ui_path():
    cd = C._build_control_definition("c1", "主窗口||Window->确认组||Group->确定||Button", "主窗口", stats=None)
    inspect = cd["inspectData"]
    assert inspect["automationId"] == ""  # 不再误填 name
    assert inspect["ancestors"]
    assert inspect["recommendedTargetMethod"] == "ui_path"
    assert inspect["recommendedTargetValue"] == "主窗口||Window->确认组||Group->确定||Button"


def test_build_control_definition_shallow_path_keeps_name_selector():
    cd = C._build_control_definition("c1", "确定||Button", "主窗口", stats=None)
    inspect = cd["inspectData"]
    assert inspect["recommendedTargetMethod"] != "ui_path"
    assert inspect["automationId"] == ""


def test_unique_path_selector_counted_in_stats():
    stats = {"uniquePathSelectors": 0}
    C._build_control_definition("c1", "主窗口||Window->确认组||Group->确定||Button", "主窗口", stats=stats)
    assert stats["uniquePathSelectors"] == 1
    C._build_control_definition("c2", "确定||Button", "主窗口", stats=stats)
    assert stats["uniquePathSelectors"] == 1  # 浅路径不计数
