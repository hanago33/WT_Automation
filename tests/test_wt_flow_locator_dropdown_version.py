"""版本下拉（Telerik PART_DropDownButton + 同行值文本）选值正确性回归测试。

现场问题（2026-10-30）：「发送综合计算」step_10 选择版本下拉项后没有选到正确
版本（参数表 cpVersion=Cp0.429），且流程报成功。代码侧三处成因：

1. 显示值读取：`PART_DropDownButton` 无 ValuePattern、运行时 UIA Name 为空，
   真实显示值是与它同行的 `WindTurbineTypeCurveParameter_TextBlock_Value`。
   读不到值 → 点击后无法校验 → "显示值不可读，保留点击证据" 直接报成功。
2. 候选匹配允许反向包含：目标 `Cp0.429` 会命中候选 `Cp0.4`（候选是目标子串），
   评分可过 70 阈值并被点击；点扫掠同理，扫到即点。
3. 键盘索引导航信任控件库 optionValues：版本下拉只采到一项（当前值 Cp0.429），
   据此算出的 index=0 会走 HOME 归零 + ENTER，把高亮落到真实列表首项。
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator


BUTTON_RECT = {"left": 1242, "top": 480, "right": 1665, "bottom": 522, "width": 423, "height": 42}
LABEL_RECT = {"left": 1044, "top": 480, "right": 1233, "bottom": 522, "width": 189, "height": 42}
VALUE_RECT = {"left": 1252, "top": 484, "right": 1622, "bottom": 517, "width": 370, "height": 33}


class _FakeWrapper(object):
    def __init__(self, text="", control_type="Text", rect=None, children=None, parent=None):
        self._text = text
        self._control_type = control_type
        self._rect = rect or {}
        self._children = list(children or [])
        self._parent = parent

    def parent(self):
        if self._parent is None:
            raise RuntimeError("no parent")
        return self._parent

    def children(self):
        return list(self._children)


def _patch_wrapper_readers():
    return patch.multiple(
        wt_flow_locator,
        get_wrapper_text=lambda wrapper: getattr(wrapper, "_text", ""),
        get_wrapper_control_type=lambda wrapper: getattr(wrapper, "_control_type", ""),
        get_wrapper_class_name=lambda wrapper: getattr(wrapper, "_control_type", ""),
        get_wrapper_rectangle=lambda wrapper: dict(getattr(wrapper, "_rect", {}) or {}),
        get_wrapper_value=lambda wrapper, climb_parents=True: "",
    )


class DropdownDisplayValueReadTests(unittest.TestCase):
    def test_row_value_textblock_is_read_as_display_value(self):
        value_text = _FakeWrapper("Cp0.429", "Text", VALUE_RECT)
        label_text = _FakeWrapper("版本", "Text", LABEL_RECT)
        parent = _FakeWrapper("", "Custom", BUTTON_RECT, children=[label_text, value_text])
        button = _FakeWrapper("版本 下拉框", "Button", BUTTON_RECT, parent=parent)

        with _patch_wrapper_readers():
            display = wt_flow_locator._read_dropdown_display_text(button, ["版本 下拉框", "版本"])
        self.assertEqual(display, "Cp0.429")

    def test_label_like_name_is_not_treated_as_value(self):
        button = _FakeWrapper("版本 下拉框", "Button", BUTTON_RECT)
        with _patch_wrapper_readers():
            display = wt_flow_locator._read_dropdown_display_text(button, ["版本 下拉框", "版本"])
        self.assertEqual(display, "", "控件自身标签不能被当成当前选中值")

    def test_self_label_texts_collected_from_definition(self):
        labels = wt_flow_locator._dropdown_self_label_texts(
            {"name": "版本 下拉框", "labelText": "版本", "inspectData": {"name": ""}}
        )
        self.assertIn("版本 下拉框", labels)
        self.assertIn("版本", labels)


class DropdownDisplayMatchTests(unittest.TestCase):
    def test_exact_match(self):
        self.assertTrue(wt_flow_locator._dropdown_display_matches_target("Cp0.429", ["Cp0.429"]))

    def test_prefix_version_is_not_a_match(self):
        self.assertFalse(wt_flow_locator._dropdown_display_matches_target("Cp0.4", ["Cp0.429"]))
        self.assertFalse(wt_flow_locator._dropdown_display_matches_target("Cp0.42", ["Cp0.429"]))

    def test_longer_version_is_not_a_match(self):
        self.assertFalse(wt_flow_locator._dropdown_display_matches_target("Cp0.4291", ["Cp0.429"]))

    def test_suffix_decoration_is_tolerated(self):
        self.assertTrue(wt_flow_locator._dropdown_display_matches_target("Cp0.429（默认）", ["Cp0.429"]))

    def test_candidate_token_reverse_match_only_when_not_strict(self):
        self.assertTrue(
            wt_flow_locator._dropdown_candidate_token_matches(["wt6250d220"], "wt6250d220 文本", strict=False)
        )
        self.assertFalse(
            wt_flow_locator._dropdown_candidate_token_matches(["cp0.4"], "cp0.429", strict=True)
        )
        self.assertTrue(
            wt_flow_locator._dropdown_candidate_token_matches(["cp0.429"], "cp0.429", strict=True)
        )


class PointSweepStrictMatchTests(unittest.TestCase):
    """点扫掠不得因"选项是目标子串"而直接点击（Cp0.4 ⊂ Cp0.429）。"""

    ANCHOR = {"left": 100, "top": 200, "right": 300, "bottom": 250, "width": 200, "height": 50}

    def _sweep(self, texts):
        """按顺序返回候选文本，返回 (result, stats, mouse_events)。"""
        fake_user32 = MagicMock()
        fake_windll = MagicMock()
        fake_windll.user32 = fake_user32
        wrappers = [MagicMock() for _ in texts]
        desktop_mock = MagicMock()
        desktop_mock.from_point.side_effect = wrappers + [None] * 20
        text_iter = iter(texts + [""] * 20)
        with patch.object(wt_flow_locator.ctypes, "windll", fake_windll), patch.object(
            wt_flow_locator, "_nearest_dropdown_option_wrapper", side_effect=lambda wrapper: wrapper
        ), patch.object(
            wt_flow_locator, "_extract_dropdown_option_text", side_effect=lambda wrapper: next(text_iter)
        ), patch(
            "pywinauto.Desktop", return_value=desktop_mock
        ):
            result = wt_flow_locator._sweep_dropdown_option_by_point(
                self.ANCHOR, ["Cp0.429"], move_cursor=False
            )
        return result, fake_user32

    def test_prefix_option_is_not_clicked(self):
        (option, text, stats), fake_user32 = self._sweep(["Cp0.4"])
        self.assertIsNone(option)
        self.assertEqual(text, "")
        self.assertEqual(stats.get("weakHitText"), "Cp0.4")
        fake_user32.mouse_event.assert_not_called()

    def test_exact_option_wins_over_earlier_prefix_option(self):
        (option, text, stats), fake_user32 = self._sweep(["Cp0.4", "Cp0.429"])
        self.assertIsNotNone(option)
        self.assertEqual(text, "Cp0.429")
        # 一次点击 = LEFTDOWN + LEFTUP 两次 mouse_event；前缀项不得被点击
        self.assertEqual(fake_user32.mouse_event.call_count, 2)


class IndexNavigationGuardTests(unittest.TestCase):
    """optionValues 只有一项（=当前显示值）时不得据此做 HOME/索引导航。"""

    def test_single_option_values_do_not_drive_home_navigation(self):
        sent = []
        dropdown_wrapper = MagicMock()
        with patch.object(wt_flow_locator, "_GET_STEP_DEFINITION", return_value={"id": "step_10"}), patch.object(
            wt_flow_locator,
            "get_flow_control_definition",
            return_value={
                "id": "ctrl_version",
                "name": "版本 下拉框",
                "labelText": "版本",
                "targetValue": "PART_DropDownButton,Button,版本",
                "inspectData": {"optionValues": ["Cp0.429"]},
            },
        ), patch.object(
            wt_flow_locator, "get_foreground_window_handle", return_value=0
        ), patch.object(
            wt_flow_locator, "_try_get_window_by_handle", return_value=MagicMock()
        ), patch.object(
            wt_flow_locator, "get_wrapper_process_id", return_value=0
        ), patch.object(
            wt_flow_locator, "_collect_dropdown_windows", return_value=[]
        ), patch.object(
            wt_flow_locator, "find_flow_control", return_value=dropdown_wrapper
        ), patch.object(
            wt_flow_locator, "get_wrapper_toggle_state", return_value="1"
        ), patch.object(
            wt_flow_locator, "click_wrapper_center", return_value=(True, {})
        ), patch.object(
            wt_flow_locator, "iter_dropdown_runtime_candidates", return_value=[]
        ), patch.object(
            wt_flow_locator, "_iter_dropdown_raw_view_candidates", return_value=[]
        ), patch.object(
            wt_flow_locator, "_iter_dropdown_win32_text_candidates", return_value=[]
        ), patch.object(
            wt_flow_locator, "score_dropdown_runtime_candidate", return_value=-1
        ), patch.object(
            wt_flow_locator, "_read_dropdown_display_text", return_value=""
        ), patch.object(
            wt_flow_locator, "is_wrapper_alive", return_value=True
        ), patch.object(
            wt_flow_locator, "send_keys", side_effect=lambda text, *a, **k: sent.append(text)
        ), patch.object(
            wt_flow_locator.time, "sleep", return_value=None
        ), patch.object(
            wt_flow_locator, "_LOG_STEP", side_effect=lambda *a, **k: None
        ):
            ok, _meta = wt_flow_locator.select_dropdown_item_runtime(
                "step_10", "ctrl_version", timeout_seconds=0.2, target_option="Cp0.429"
            )
        self.assertFalse(ok)
        self.assertNotIn("{HOME}", sent, "单元素 optionValues 不得触发 HOME 归零导航")
        self.assertTrue(any("Cp0.429" in item for item in sent), "应转键入搜索兜底")


if __name__ == "__main__":
    unittest.main()
