import os
import sys
import unittest
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
        window = _make_window([pane_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Pane"), \
             patch.object(wt_flow_locator, "get_wrapper_automation_id", side_effect=lambda w: "PART_ContentHost"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=85), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn, step_id="step_pane")

        self.assertIs(result, pane_wrapper)

    def test_matches_custom_part_content_host_by_label_text(self):
        """Custom 类型 + PART_ContentHost + labelText 匹配 → 成功定位。"""
        defn = self._make_control_definition("Custom", "custom", "描述", automation_id="PART_ContentHost")
        custom_wrapper = _make_fake_wrapper("Custom", "custom", automation_id="PART_ContentHost")
        window = _make_window([custom_wrapper])

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Custom"), \
             patch.object(wt_flow_locator, "get_wrapper_automation_id", side_effect=lambda w: "PART_ContentHost"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=True), \
             patch.object(wt_flow_locator, "score_control_match", return_value=85), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn, step_id="step_custom")

        self.assertIs(result, custom_wrapper)

    def test_multiple_part_content_host_distinguished_by_label(self):
        """多个 PART_ContentHost 时通过 labelText 正确区分。"""
        defn = self._make_control_definition("Pane", "pane", "名称", automation_id="PART_ContentHost")
        pane_a = _make_fake_wrapper("Pane", "pane", automation_id="PART_ContentHost")
        pane_b = _make_fake_wrapper("Pane", "pane", automation_id="PART_ContentHost")
        window = _make_window([pane_a, pane_b])

        # 只有 pane_a 的标签匹配
        def label_match_side_effect(wrapper, label):
            return wrapper is pane_a

        with patch.object(wt_flow_locator, "get_wrapper_control_type", side_effect=lambda w: "Pane"), \
             patch.object(wt_flow_locator, "get_wrapper_automation_id", side_effect=lambda w: "PART_ContentHost"), \
             patch.object(wt_flow_locator, "wrapper_matches_label_text", side_effect=label_match_side_effect), \
             patch.object(wt_flow_locator, "score_control_match", return_value=85), \
             patch.object(wt_flow_locator, "_LOG_STEP"):
            result = wt_flow_locator._try_label_to_input_fallback([window], defn)

        self.assertIs(result, pane_a)

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


if __name__ == "__main__":
    unittest.main()
