# encoding: utf-8
"""标签伴随采集 + Inspect 字段补全 + label_text 定位 单测。

覆盖：
- 采集端（build_control_map_library）：
  MSAA Role/State 解码、伴随输入判定、伴随几何匹配、跨栏阻挡、
  既有伴随查重标记、第三轮宽松关联、过滤器/剪枝豁免、
  backfill relatedLabelName 优先与 label_text 重评分、dumper 合并新字段。
- 运行时（wt_flow_locator）：
  label_rect 几何匹配、wrapper_matches_label_text（LabeledBy 权威 + 几何兜底）、
  wrapper_matches_locator 的 label_text 方法、build_common_locator_candidates 候选、
  get_wrapper_help_text 修复。
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import build_control_map_library as bcm
import wt_flow_locator as L


def _control(name, control_type, rect, automation_id="", class_name="", **extra):
    left, top, right, bottom = rect
    item = {
        "name": name,
        "displayName": name or automation_id or class_name,
        "controlType": control_type,
        "automationId": automation_id,
        "className": class_name,
        "boundingBox": {"left": left, "top": top, "right": right, "bottom": bottom},
        "isOffscreen": "False",
        "inspectData": {"children": []},
    }
    item.update(extra)
    return item


# ─────────────────────────────────────────────────────────────────────────────
# MSAA Role/State 解码（对齐用户提供的 Inspect 输出）
# ─────────────────────────────────────────────────────────────────────────────

def test_decode_msaa_role_push_button():
    assert bcm.decode_msaa_role(0x2B) == "按下按钮 (0x2B)"
    assert bcm.decode_msaa_role(0x29) == "静态文本 (0x29)"
    assert bcm.decode_msaa_role("") == ""
    assert bcm.decode_msaa_role(0) == ""


def test_decode_msaa_state_unavailable():
    assert bcm.decode_msaa_state(0x1) == "不可用 (0x1)"
    assert bcm.decode_msaa_state(0) == "常规 (0x0)"
    # 组合位：不可用 + 可设定焦点
    assert bcm.decode_msaa_state(0x100001) == "不可用,可设定焦点 (0x100001)"


# ─────────────────────────────────────────────────────────────────────────────
# 采集端：label_text 定位候选
# ─────────────────────────────────────────────────────────────────────────────

def test_locator_recommendation_prefers_label_text_over_ui_path():
    method, value, score, reason = bcm.build_locator_recommendation(
        {"controlType": "Edit", "labelText": "名称"}, index=5, ui_path="窗口 > 面板 > 控件5"
    )
    assert method == "label_text,control_type"
    assert value == "名称,Edit"
    assert score == 76


def test_locator_recommendation_automation_id_still_wins():
    method, value, score, _ = bcm.build_locator_recommendation(
        {"controlType": "Edit", "labelText": "名称", "automationId": "NameTextBox"}, index=5
    )
    assert method == "automation_id,control_type"
    assert score == 100


# ─────────────────────────────────────────────────────────────────────────────
# 采集端：伴随输入判定与几何
# ─────────────────────────────────────────────────────────────────────────────

def test_item_is_companion_input_strong_type():
    assert bcm._item_is_companion_input(_control("", "Edit", (0, 0, 10, 10)))
    assert bcm._item_is_companion_input(_control("", "ComboBox", (0, 0, 10, 10)))
    assert not bcm._item_is_companion_input(_control("标签", "Text", (0, 0, 10, 10)))


def test_item_is_companion_input_container_needs_evidence():
    bare = _control("", "Custom", (0, 0, 10, 10))
    assert not bcm._item_is_companion_input(bare)
    by_class = _control("", "Custom", (0, 0, 10, 10), class_name="WatermarkTextBox")
    assert bcm._item_is_companion_input(by_class)
    by_pattern = _control("", "Pane", (0, 0, 10, 10), supportedPatterns=["Value"])
    assert bcm._item_is_companion_input(by_pattern)
    by_focus = _control("", "Group", (0, 0, 10, 10), isKeyboardFocusable="True")
    assert bcm._item_is_companion_input(by_focus)


def test_label_companion_geometry_match():
    label_rect = {"left": 10, "top": 10, "right": 60, "bottom": 34}
    same_row = {"left": 80, "top": 10, "right": 300, "bottom": 34}
    assert bcm._label_companion_geometry_match(label_rect, same_row)
    below = {"left": 10, "top": 50, "right": 300, "bottom": 74}
    assert bcm._label_companion_geometry_match(label_rect, below)
    far_right = {"left": 600, "top": 10, "right": 800, "bottom": 34}
    assert not bcm._label_companion_geometry_match(label_rect, far_right)
    far_below = {"left": 10, "top": 300, "right": 300, "bottom": 324}
    assert not bcm._label_companion_geometry_match(label_rect, far_below)


def test_companion_path_blocked_by_intervening_label():
    label_a = _control("名称", "Text", (10, 10, 60, 34))
    label_b = _control("类型", "Text", (200, 10, 250, 34))
    rect_a = label_a["boundingBox"]
    rect_b = label_b["boundingBox"]
    candidate = {"left": 260, "top": 10, "right": 400, "bottom": 34}
    pairs = [(label_a, rect_a), (label_b, rect_b)]
    # 名称 → 候选之间隔着“类型”标签：阻挡
    assert bcm._companion_path_blocked_by_label(label_a, rect_a, candidate, pairs)
    # 类型 → 候选之间无其他标签：不阻挡
    assert not bcm._companion_path_blocked_by_label(label_b, rect_b, candidate, pairs)


def test_find_existing_companion_marks_and_skips_probe():
    label = _control("名称", "Text", (10, 10, 60, 34))
    edit = _control("", "Edit", (80, 10, 300, 34))
    flat = [label, edit]
    pairs = [(label, label["boundingBox"])]
    assert bcm._find_existing_companion_for_label(label, label["boundingBox"], flat, pairs)
    assert edit["regionRelated"] is True
    assert edit["relatedLabelName"] == "名称"
    assert edit["regionRelation"] == "label-companion-existing"


def test_find_existing_companion_does_not_steal():
    label = _control("名称", "Text", (10, 10, 60, 34))
    edit = _control("", "Edit", (80, 10, 300, 34), relatedLabelName="其他")
    flat = [label, edit]
    pairs = [(label, label["boundingBox"])]
    assert not bcm._find_existing_companion_for_label(label, label["boundingBox"], flat, pairs)
    assert edit["relatedLabelName"] == "其他"


# ─────────────────────────────────────────────────────────────────────────────
# 采集端：第三轮宽松关联
# ─────────────────────────────────────────────────────────────────────────────

def test_loose_companion_binds_container_input_beyond_same_row_gap():
    label = _control("名称", "Text", (10, 10, 60, 34))
    # 间隙 140px，超过同行关联的 60px 上限，但在第三轮 420px 放宽范围内
    custom_input = _control("", "Custom", (200, 10, 400, 34), supportedPatterns=["Value"])
    controls = [label, custom_input]
    bcm._associate_region_labels_with_controls(controls)
    assert custom_input.get("regionRelated") is True
    assert custom_input.get("relatedLabelName") == "名称"
    assert custom_input.get("regionRelation") == "loose-companion"


def test_loose_companion_blocked_by_intervening_label():
    label_a = _control("名称", "Text", (10, 10, 60, 34))
    label_b = _control("类型", "Text", (200, 10, 250, 34))
    edit = _control("", "Edit", (260, 10, 400, 34))
    controls = [label_a, label_b, edit]
    bcm._associate_region_labels_with_controls(controls)
    # 名称 → edit 隔着“类型”标签，第三轮不得跨栏误绑；
    # edit 可能被“类型”标签正常关联，但绝不能挂到“名称”名下。
    assert edit.get("relatedLabelName") != "名称"


# ─────────────────────────────────────────────────────────────────────────────
# 采集端：过滤器/剪枝豁免
# ─────────────────────────────────────────────────────────────────────────────

def test_filter_noise_controls_keeps_region_related_container():
    related = _control("", "Custom", (80, 10, 300, 34), regionRelated=True)
    plain = _control("", "Custom", (80, 60, 300, 84))
    kept = bcm._filter_noise_controls([related, plain], exclude_offscreen=False, exclude_unidentified_containers=True)
    assert related in kept
    assert plain not in kept


def test_prune_empty_containers_keeps_related_label_container():
    related = _control("", "Pane", (80, 10, 300, 34), relatedLabelName="名称")
    plain = _control("", "Pane", (80, 60, 300, 84))
    kept = bcm._prune_empty_unidentified_containers([related, plain])
    assert related in kept
    assert plain not in kept


# ─────────────────────────────────────────────────────────────────────────────
# 采集端：backfill relatedLabelName 优先 + label_text 重评分
# ─────────────────────────────────────────────────────────────────────────────

def test_backfill_prefers_related_label_name_and_label_text_locator():
    item = _control("", "Edit", (80, 10, 300, 34), relatedLabelName="名称",
                    locatorScore=10, recommendedTargetMethod="ui_path",
                    recommendedTargetValue="窗口 > 控件", uiPath="窗口 > 控件")
    item["regionRelation"] = "label-companion-probe"
    bcm._backfill_label_text_to_controls([item])
    assert item["labelText"] == "名称"
    assert item["labelRelation"] == "region-association"
    # name 回填用于展示，但 nameSource 标记来源
    assert item["name"] == "名称"
    assert item["nameSource"] == "relatedlabel-backfill"
    # 重评分：回填 name 不参与定位（运行时真实 Name 为空），label_text 胜出
    assert item["recommendedTargetMethod"] == "label_text,control_type"
    assert item["recommendedTargetValue"] == "名称,Edit"
    assert item["locatorScore"] == 76


def test_backfill_related_label_applies_to_container_companion():
    # 容器型伴随输入控件（未规范化为 Edit）也可通过 relatedLabelName 回填
    item = _control("", "Custom", (80, 10, 300, 34), relatedLabelName="名称",
                    supportedPatterns=["Value"], locatorScore=10,
                    recommendedTargetMethod="ui_path", recommendedTargetValue="窗口 > 控件",
                    uiPath="窗口 > 控件")
    bcm._backfill_label_text_to_controls([item])
    assert item["labelText"] == "名称"
    assert item["recommendedTargetMethod"] == "label_text,control_type"
    assert item["recommendedTargetValue"] == "名称,Custom"


def test_backfill_labeledby_still_works_without_related_label():
    item = _control("", "Edit", (80, 10, 300, 34), labeledByName="名称",
                    locatorScore=10, recommendedTargetMethod="ui_path",
                    recommendedTargetValue="窗口 > 控件", uiPath="窗口 > 控件")
    bcm._backfill_label_text_to_controls([item])
    assert item["labelText"] == "名称"
    assert item["labelRelation"] == "uia-labeledby"
    assert item["recommendedTargetMethod"] == "label_text,control_type"


# ─────────────────────────────────────────────────────────────────────────────
# 采集端：uia_tree_dumper 合并新字段
# ─────────────────────────────────────────────────────────────────────────────

def test_merge_uia_dumper_records_maps_new_fields():
    records = [
        {"index": 0, "depth": 0, "parentIndex": -1, "controlType": "Window",
         "name": "主窗口", "className": "Window", "processId": 123,
         "rect": {"X": 0, "Y": 0, "W": 800, "H": 600}},
        {"index": 1, "depth": 1, "parentIndex": 0, "controlType": "Edit",
         "localizedControlType": "编辑", "accessKey": "Alt, N",
         "patterns": "Value,Text", "expandState": "", "labeledByName": "名称",
         "frameworkId": "WPF", "isKeyboardFocusable": True, "hasKeyboardFocus": False,
         "isContentElement": True, "isControlElement": True, "isPassword": False,
         "processId": 123, "rect": {"X": 100, "Y": 10, "W": 200, "H": 24}},
    ]
    target_window = {"title": "主窗口", "className": "Window", "processId": "123", "handle": "0x1"}
    flat, seen = [], set()
    added = bcm._merge_uia_dumper_records(records, target_window, flat, seen)
    assert added == 2
    edit = next(item for item in flat if item["controlType"].lower() == "edit")
    assert edit["localizedControlType"] == "编辑"
    assert edit["accessKey"] == "Alt, N"
    assert edit["supportedPatterns"] == ["Value", "Text"]
    assert edit["labeledByName"] == "名称"
    assert edit["frameworkId"] == "WPF"
    assert edit["isKeyboardFocusable"] == "True"
    inspect = edit["inspectData"]
    assert inspect["localizedControlType"] == "编辑"
    assert inspect["supportedPatterns"] == ["Value", "Text"]
    assert inspect["labeledByName"] == "名称"
    assert inspect["isContentElement"] == "True"


# ─────────────────────────────────────────────────────────────────────────────
# 运行时：label_text 匹配
# ─────────────────────────────────────────────────────────────────────────────

class _FakeWrapper:
    def __init__(self, data):
        self._data = dict(data or {})
        ei = self._data.get("element_info") or {}
        self.element_info = types.SimpleNamespace(
            automation_id=ei.get("automation_id", ""),
            control_type=ei.get("control_type", ""),
            localized_control_type=ei.get("localized_control_type", ""),
            framework_id=ei.get("framework_id", ""),
            help_text=ei.get("help_text", ""),
            process_id=ei.get("process_id", ""),
            element=ei.get("element"),
        )

    def window_text(self):
        return self._data.get("name", "")

    def class_name(self):
        return self._data.get("className", "")


def _edit_wrapper():
    return _FakeWrapper({"element_info": {"control_type": "Edit"}})


def test_label_rect_matches_control_geometry():
    label_rect = {"left": 10, "top": 10, "right": 60, "bottom": 34}
    assert L._label_rect_matches_control(label_rect, {"left": 80, "top": 10, "right": 300, "bottom": 34})
    assert L._label_rect_matches_control(label_rect, {"left": 10, "top": 50, "right": 300, "bottom": 74})
    assert not L._label_rect_matches_control(label_rect, {"left": 600, "top": 10, "right": 800, "bottom": 34})
    assert not L._label_rect_matches_control(label_rect, None)


def test_wrapper_matches_label_text_labeledby_authoritative(monkeypatch):
    wrapper = _edit_wrapper()
    monkeypatch.setattr(L, "_read_wrapper_labeled_by_name", lambda w: "名称")
    # LabeledBy 命中即 True，无需几何
    monkeypatch.setattr(L, "_find_label_rects_for_wrapper", lambda w, label: [])
    assert L.wrapper_matches_label_text(wrapper, "名称")
    # LabeledBy 指向其他标签：权威否定，即使几何相邻也为 False
    monkeypatch.setattr(L, "_read_wrapper_labeled_by_name", lambda w: "其他")
    monkeypatch.setattr(L, "get_wrapper_rectangle", lambda w: {"left": 80, "top": 10, "right": 300, "bottom": 34})
    monkeypatch.setattr(L, "_find_label_rects_for_wrapper",
                        lambda w, label: [{"left": 10, "top": 10, "right": 60, "bottom": 34}])
    assert not L.wrapper_matches_label_text(wrapper, "名称")


def test_wrapper_matches_label_text_geometric_fallback(monkeypatch):
    wrapper = _edit_wrapper()
    monkeypatch.setattr(L, "_read_wrapper_labeled_by_name", lambda w: "")
    monkeypatch.setattr(L, "get_wrapper_rectangle", lambda w: {"left": 80, "top": 10, "right": 300, "bottom": 34})
    monkeypatch.setattr(L, "_find_label_rects_for_wrapper",
                        lambda w, label: [{"left": 10, "top": 10, "right": 60, "bottom": 34}])
    assert L.wrapper_matches_label_text(wrapper, "名称")
    # 标签太远 → False
    monkeypatch.setattr(L, "_find_label_rects_for_wrapper",
                        lambda w, label: [{"left": 10, "top": 300, "right": 60, "bottom": 324}])
    assert not L.wrapper_matches_label_text(wrapper, "名称")


def test_wrapper_matches_locator_label_text_method(monkeypatch):
    wrapper = _edit_wrapper()
    monkeypatch.setattr(L, "_read_wrapper_labeled_by_name", lambda w: "")
    monkeypatch.setattr(L, "get_wrapper_rectangle", lambda w: {"left": 80, "top": 10, "right": 300, "bottom": 34})

    def _fake_find(w, label):
        # 真实实现按标签文本精确搜索：文本不匹配时找不到标签矩形
        if label != "名称":
            return []
        return [{"left": 10, "top": 10, "right": 60, "bottom": 34}]

    monkeypatch.setattr(L, "_find_label_rects_for_wrapper", _fake_find)
    assert L.wrapper_matches_locator(wrapper, "label_text,control_type", "名称,Edit")
    # control_type 不匹配 → False（廉价条件先淘汰）
    assert not L.wrapper_matches_locator(wrapper, "label_text,control_type", "名称,ComboBox")
    # 标签文本不匹配 → False
    assert not L.wrapper_matches_locator(wrapper, "label_text", "描述")


def test_build_common_locator_candidates_includes_label_text():
    cd = {"inspectData": {"controlType": "Edit", "labelText": "名称"}}
    candidates = L.build_common_locator_candidates(cd)
    assert ("label_text,control_type", "名称,Edit") in candidates
    assert ("label_text", "名称") in candidates
    # 顶层 labelText 字段（definition 持久化）同样生效
    cd2 = {"labelText": "名称", "inspectData": {"controlType": "Edit"}}
    candidates2 = L.build_common_locator_candidates(cd2)
    assert ("label_text,control_type", "名称,Edit") in candidates2


# ─────────────────────────────────────────────────────────────────────────────
# 运行时：get_wrapper_help_text 修复
# ─────────────────────────────────────────────────────────────────────────────

def test_get_wrapper_help_text_prefers_element_info():
    wrapper = _FakeWrapper({"element_info": {"help_text": "删除"}})
    assert L.get_wrapper_help_text(wrapper) == "删除"


def test_get_wrapper_help_text_empty_when_no_element():
    # pywinauto 无 help_text 属性且 element 缺失（如 win32 路径）→ 空串不报错
    wrapper = _FakeWrapper({"element_info": {"help_text": "", "element": None}})
    assert L.get_wrapper_help_text(wrapper) == ""
