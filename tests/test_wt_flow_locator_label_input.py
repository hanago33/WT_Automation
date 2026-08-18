import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator


def _make_fake_element_info(control_type, localized_control_type=""):
    """构造模拟 element_info，供 get_wrapper_control_type 读取。"""
    info = MagicMock()
    info.control_type = control_type
    info.localized_control_type = localized_control_type
    return info


def _make_fake_wrapper(control_type, localized_control_type="", name="", rect=None, automation_id=""):
    """构造模拟 wrapper 对象。"""
    wrapper = MagicMock()
    wrapper.element_info = _make_fake_element_info(control_type, localized_control_type)
    wrapper.element_info.automation_id = automation_id
    wrapper.window_text = MagicMock(return_value=name)
    if rect is None:
        rect = {"left": 100, "top": 100, "right": 200, "bottom": 125}
    wrapper.rectangle = MagicMock(return_value=rect)
    return wrapper


def _make_window(descendants_list):
    """构造模拟窗口 wrapper，descendants() 返回指定列表。"""
    window = MagicMock()
    window.descendants = MagicMock(return_value=descendants_list)
    return window


class TryLabelToInputFallbackTests(unittest.TestCase):
    """测试 _try_label_to_input_fallback 降级策略。"""

    def _make_control_definition(self, control_type, localized_control_type, label_text, inspect_label_text="", automation_id=""):
        """构造控件定义字典，模拟采集端数据。"""
        inspect = {
            "controlType": control_type,
            "localizedControlType": localized_control_type,
            "labelText": inspect_label_text,
        }
        if automation_id:
            inspect["automationId"] = automation_id
        return {
            "name": "",
            "labelText": label_text,
            "inspectData": inspect,
        }

    # ---- 前置条件过滤 ----

    def test_returns_none_when_control_definition_not_dict(self):
        result = wt_flow_locator._try_label_to_input_fallback([], "not_a_dict")
        self.assertIsNone(result)

    def test_returns_none_when_windows_empty(self):
        defn = self._make_control_definition("Edit", "edit", "名称")
        result = wt_flow_locator._try_label_to_input_fallback([], defn)
        self.assertIsNone(result)

    def test_returns_none_when_control_type_not_input_like(self):
        """非输入类控件（Text 且无 PART_ContentHost）应返回 None。"""
        defn = self._make_control_definition("Text", "text", "名称")
        window = _make_window([])
        result = wt_flow_locator._try_label_to_input_fallback([window], defn)
        self.assertIsNone(result)

    def test_returns_none_when_pane_without_part_content_host(self):
        """Pane 类型但 automationId 不是 PART_ContentHost 时应返回 None。"""
        defn = self._make_control_definition("Pane", "pane", "名称")
        window = _make_window([])
        result = wt_flow_locator._try_label_to_input_fallback([window], defn)
        self.assertIsNone(result)

    def test_returns_none_when_label_text_empty(self):
        defn = self._make_control_definition("Edit", "edit", "")
        window = _make_window([])
        result = wt_flow_locator._try_label_to_input_fallback([window], defn)
        self.assertIsNone(result)

    def test_returns_none_when_no_label_text_at_all(self):
        defn = {
            "name": "",
            "inspectData": {"controlType": "Edit", "localizedControlType": "edit"},
        }
        window = _make_window([])
        result = wt_flow_locator._try_label_to_input_fallback([window], defn)
        self.assertIsNone(result)

    # ---- 正常匹配 ----

    def test_matches_edit_control_by_label_text(self):
        defn = self._make_control_definition("Edit", "edit", "名称")
        edit_wrapper = _make_fake_wrapper("Edit", "edit")
        window = _make_window([edit_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Edit"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=85), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn, step_id="step_1")

        self.assertIs(result, edit_wrapper)

    def test_matches_combobox_control_by_label_text(self):
        defn = self._make_control_definition("ComboBox", "combo box", "访问级别")
        combo_wrapper = _make_fake_wrapper("ComboBox", "combo box")
        window = _make_window([combo_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "ComboBox"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=90), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn, step_id="step_2")

        self.assertIs(result, combo_wrapper)

    # ---- Pane + PART_ContentHost 匹配 ----

    def test_matches_pane_part_content_host_by_label_text(self):
        """Pane 类型 + PART_ContentHost + labelText 匹配 → 成功定位。"""
        defn = self._make_control_definition("Pane", "pane", "名称", automation_id="PART_ContentHost")
        pane_wrapper = _make_fake_wrapper("Pane", "pane", automation_id="PART_ContentHost")
        edit_wrapper = _make_fake_wrapper("Edit", "edit")
        window = _make_window([pane_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Pane"), \
             patch.object(wt_flow_locator, "get_wrapper_automation_id", side_effect=lambda w: "PART_ContentHost"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "_resolve_editable_target", return_value=edit_wrapper), \
             patch.object(wt_flow_locator, "score_control_match", return_value=85), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn, step_id="step_pane")

        self.assertIs(result, edit_wrapper)

    def test_matches_custom_part_content_host_by_label_text(self):
        """Custom 类型 + PART_ContentHost + labelText 匹配 → 成功定位。"""
        defn = self._make_control_definition("Custom", "custom", "描述", automation_id="PART_ContentHost")
        custom_wrapper = _make_fake_wrapper("Custom", "custom", automation_id="PART_ContentHost")
        edit_wrapper = _make_fake_wrapper("Edit", "edit")
        window = _make_window([custom_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Custom"), \
             patch.object(wt_flow_locator, "get_wrapper_automation_id", side_effect=lambda w: "PART_ContentHost"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "_resolve_editable_target", return_value=edit_wrapper), \
             patch.object(wt_flow_locator, "score_control_match", return_value=85), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn, step_id="step_custom")

        self.assertIs(result, edit_wrapper)

    def test_matches_real_edit_for_part_content_host_definition(self):
        defn = self._make_control_definition("Pane", "pane", "查找", automation_id="PART_ContentHost")
        edit_wrapper = _make_fake_wrapper("Edit", "edit")
        window = _make_window([edit_wrapper])

        with patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=75), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn, step_id="step_9")

        self.assertIs(result, edit_wrapper)

    def test_resolves_part_content_host_to_textbox_ancestor(self):
        content_host = _make_fake_wrapper("Pane", "pane", automation_id="PART_ContentHost")
        textbox = _make_fake_wrapper("Custom", "custom")
        textbox.class_name = MagicMock(return_value="TextBox")
        content_host.parent = MagicMock(return_value=textbox)

        result = wt_flow_locator._resolve_editable_target(content_host)

        self.assertIs(result, textbox)

    def test_multiple_part_content_host_distinguished_by_label(self):
        """多个 PART_ContentHost 时通过 labelText 正确区分。"""
        defn = self._make_control_definition("Pane", "pane", "名称", automation_id="PART_ContentHost")
        pane_a = _make_fake_wrapper("Pane", "pane", automation_id="PART_ContentHost")
        pane_b = _make_fake_wrapper("Pane", "pane", automation_id="PART_ContentHost")
        edit_a = _make_fake_wrapper("Edit", "edit")
        window = _make_window([pane_a, pane_b])

        # 只有 pane_a 的标签匹配
        def label_match_side_effect(wrapper, label):
            return wrapper is pane_a

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Pane"), \
             patch.object(wt_flow_locator, "get_wrapper_automation_id", side_effect=lambda w: "PART_ContentHost"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", side_effect=label_match_side_effect), \
             patch.object(wt_flow_locator, "_resolve_editable_target", side_effect=lambda w: edit_a if w is pane_a else None), \
             patch.object(wt_flow_locator, "score_control_match", return_value=85), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn)

        self.assertIs(result, edit_a)

    def test_skips_pane_without_part_content_host_in_candidates(self):
        """遍历候选时，Pane 但无 PART_ContentHost 的控件应被跳过。"""
        defn = self._make_control_definition("Edit", "edit", "名称")
        pane_no_aid = _make_fake_wrapper("Pane", "pane", automation_id="")
        window = _make_window([pane_no_aid])

        with patch.object(wt_flow_locator, "get_wrapper_control_type",
                          side_effect=lambda w: w.element_info.control_type), \
             patch.object(wt_flow_locator, "get_wrapper_automation_id",
                          side_effect=lambda w: w.element_info.automation_id), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=80):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn)

        self.assertIsNone(result)

    # ---- 跳过不匹配控件 ----

    def test_skips_non_input_candidates(self):
        defn = self._make_control_definition("Edit", "edit", "名称")
        text_wrapper = _make_fake_wrapper("Text", "text")
        button_wrapper = _make_fake_wrapper("Button", "button")
        window = _make_window([text_wrapper, button_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type",
                          side_effect=lambda w: w.element_info.control_type), \
             patch.object(wt_flow_locator, "get_wrapper_automation_id",
                          side_effect=lambda w: w.element_info.automation_id), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=80):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn)

        self.assertIsNone(result)

    def test_skips_edit_with_non_matching_label(self):
        defn = self._make_control_definition("Edit", "edit", "名称")
        edit_wrapper = _make_fake_wrapper("Edit", "edit")
        window = _make_window([edit_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Edit"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=False):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn)

        self.assertIsNone(result)

    # ---- 多候选评分选择 ----

    def test_picks_higher_scoring_candidate(self):
        defn = self._make_control_definition("Edit", "edit", "描述")
        edit_a = _make_fake_wrapper("Edit", "edit", name="a")
        edit_b = _make_fake_wrapper("Edit", "edit", name="b")
        window = _make_window([edit_a, edit_b])

        scores = {id(edit_a): 70, id(edit_b): 95}

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Edit"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match",
                          side_effect=lambda w, d: scores.get(id(w), 0)), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn)

        self.assertIs(result, edit_b)

    # ---- 最低分保底 ----

    def test_score_floor_at_80_for_label_match(self):
        """labelText 命中时，即使 score_control_match 给低分也应保底 80。"""
        defn = self._make_control_definition("Edit", "edit", "名称")
        edit_wrapper = _make_fake_wrapper("Edit", "edit")
        window = _make_window([edit_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Edit"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=30), \
             patch.object(wt_flow_locator, "_LOG_STEP") as mock_log:
            result = wt_flow_locator._try_label_to_input_fallback([window], defn, step_id="step_x")

        self.assertIs(result, edit_wrapper)
        # 验证日志中 score=80（保底分）而非原始 30
        log_call_args = mock_log.call_args[0][0]
        self.assertIn("score=80", log_call_args)

    # ---- 多窗口搜索 ----

    def test_searches_across_multiple_windows(self):
        defn = self._make_control_definition("Edit", "edit", "名称")
        edit_in_win2 = _make_fake_wrapper("Edit", "edit")
        window1 = _make_window([])  # 窗口1无匹配
        window2 = _make_window([edit_in_win2])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Edit"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=85), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window1, window2], defn)

        self.assertIs(result, edit_in_win2)

    # ---- labelText 从 inspectData 回退读取 ----

    def test_reads_label_text_from_inspect_data_when_top_level_empty(self):
        defn = {
            "name": "",
            "labelText": "",
            "inspectData": {
                "controlType": "Edit",
                "localizedControlType": "edit",
                "labelText": "从inspect读取",
            },
        }
        edit_wrapper = _make_fake_wrapper("Edit", "edit")
        window = _make_window([edit_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Edit"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=85), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn)

        self.assertIs(result, edit_wrapper)

    # ---- 异常容错 ----

    def test_handles_descendants_exception_gracefully(self):
        defn = self._make_control_definition("Edit", "edit", "名称")
        window = MagicMock()
        window.descendants = MagicMock(side_effect=Exception("UIA枚举失败"))

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Edit"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn)

        self.assertIsNone(result)


class PartContentHostTypeRelaxationTests(unittest.TestCase):
    """PART_ContentHost 的 Raw View 类型放宽：运行时类型可为 Pane/Edit 等。"""

    def _make_definition(self, automation_id="PART_ContentHost", control_type="Pane"):
        return {
            "name": "",
            "labelText": "查找",
            "targetMethod": "automation_id,control_type",
            "targetValue": "PART_ContentHost,Pane",
            "uiPath": "Window_Main > MBAProjectionSelectionView > PART_ContentHost",
            "inspectData": {
                "automationId": automation_id,
                "controlType": control_type,
                "localizedControlType": "pane",
            },
        }

    def _neutral_patches(self):
        """把所有辅助评分路径固定为中性值，隔离被测逻辑。"""
        return [
            patch.object(wt_flow_locator, "get_wrapper_automation_id", side_effect=lambda w: "PART_ContentHost"),
            patch.object(wt_flow_locator, "get_wrapper_text", return_value=""),
            patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=False),
            patch.object(wt_flow_locator, "_build_wrapper_path_signature", return_value=[]),
            patch.object(wt_flow_locator, "get_wrapper_found_index", return_value=-1),
            patch.object(wt_flow_locator, "get_wrapper_class_name", return_value=""),
            patch.object(wt_flow_locator, "get_wrapper_framework_id", return_value=""),
            patch.object(wt_flow_locator, "get_wrapper_localized_control_type", return_value=""),
            patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=""),
            patch.object(wt_flow_locator, "get_wrapper_parent_signatures", return_value=[]),
            patch.object(wt_flow_locator, "get_wrapper_child_signatures", return_value=[]),
            patch.object(wt_flow_locator, "get_wrapper_is_enabled", return_value=""),
            patch.object(wt_flow_locator, "get_wrapper_is_offscreen", return_value=""),
            patch.object(wt_flow_locator, "get_wrapper_is_keyboard_focusable", return_value=""),
            patch.object(wt_flow_locator, "get_wrapper_has_keyboard_focus", return_value=""),
            patch.object(wt_flow_locator, "get_wrapper_help_text", return_value=""),
        ]

    def test_runtime_edit_type_still_matches_when_aid_is_part_content_host(self):
        """运行时 Raw View 报 Edit（而非采集的 Pane）时，aid 命中 PART_ContentHost 应匹配。"""
        defn = self._make_definition()
        wrapper = _make_fake_wrapper("Edit", "edit", automation_id="PART_ContentHost")

        with ExitStack() as stack:
            for p in self._neutral_patches():
                stack.enter_context(p)
            score = wt_flow_locator.get_control_definition_match_score(wrapper, defn)

        self.assertGreater(score, 0)

    def test_runtime_edit_type_without_aid_not_matched(self):
        """aid 不是 PART_ContentHost 的 Edit 即使类型匹配也不应被放宽逻辑放行。"""
        defn = self._make_definition()
        wrapper = _make_fake_wrapper("Edit", "edit", automation_id="OtherTextBox")

        with ExitStack() as stack:
            for p in self._neutral_patches():
                stack.enter_context(p)
            wt_flow_locator.get_wrapper_automation_id = lambda w: "OtherTextBox"
            score = wt_flow_locator.get_control_definition_match_score(wrapper, defn)

        self.assertLess(score, 0)

    def test_runtime_pane_type_still_matches(self):
        """运行时类型仍为 Pane 时保持既有严格匹配行为（回归保护）。"""
        defn = self._make_definition()
        wrapper = _make_fake_wrapper("Pane", "pane", automation_id="PART_ContentHost")

        with ExitStack() as stack:
            for p in self._neutral_patches():
                stack.enter_context(p)
            score = wt_flow_locator.get_control_definition_match_score(wrapper, defn)

        self.assertGreater(score, 0)


if __name__ == "__main__":
    unittest.main()
