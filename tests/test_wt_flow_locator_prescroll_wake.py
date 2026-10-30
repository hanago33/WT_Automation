"""preScrollToTop 唤醒策略回归测试。

背景（2026-10-30 现场问题）：复制综合1后用「发送综合计算」流程配置综合2时，
左侧配置区一直处于"未唤醒"状态、流程卡在等待新副本编辑器。日志里的唤醒点击
坐标 (1728,260) 来自"整窗矩形 0.9W/0.25H"的兜底分支——编辑器容器未实体化时
退化成整窗矩形，点击实际落在右侧地图/顶部工具栏上，对配置区毫无作用。

修复后约定：
  1. 优先点真实控件（页面配置的唤醒控件 / 编辑器当前选中页签头）；
  2. 空白点击绝不允许落到编辑器容器矩形之外（未实体化时直接跳过）；
  3. 等待步骤的周期性补唤调用同一套真实控件优先策略。
"""

import os
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator


TAB_STRIP_ID = wt_flow_locator._WAKE_TAB_STRIP_AUTOMATION_ID
EDITOR_SCROLL_ID = wt_flow_locator._WAKE_EDITOR_SCROLL_AUTOMATION_ID


class _FakeControl(object):
    def __init__(self, name="", selected=None, control_type="tabitem", rect=None, children=None, keyboard_focus=False):
        self.name = name
        self._selected = selected
        self._control_type = control_type
        self._rect = rect or {}
        self._children = list(children or [])
        self._keyboard_focus = keyboard_focus
        self.focused = False

    def is_selected(self):
        if self._selected is None:
            raise RuntimeError("SelectionItemPattern 不可用")
        return self._selected

    def has_keyboard_focus(self):
        return self._keyboard_focus

    def children(self):
        return list(self._children)

    def set_focus(self):
        self.focused = True


def _patch_wrapper_readers():
    return patch.multiple(
        wt_flow_locator,
        get_wrapper_text=lambda wrapper: getattr(wrapper, "name", ""),
        get_wrapper_control_type=lambda wrapper: getattr(wrapper, "_control_type", ""),
        get_wrapper_class_name=lambda wrapper: getattr(wrapper, "_control_type", ""),
        get_wrapper_rectangle=lambda wrapper: dict(getattr(wrapper, "_rect", {}) or {}),
    )


def _findall_by_aid(mapping):
    def _fake(window, automation_id, *args, **kwargs):
        return list(mapping.get(automation_id, []))

    return _fake


class PrescrollWakeStrategyTests(unittest.TestCase):
    def _run_wake(self, windows, mapping, clicked, config=None, foreground=True):
        """跑一次 _prescroll_wake_editor，返回 (结果, 被点击控件列表)。"""
        config = config or {}

        def _config_text(step_id, key, default=""):
            return config.get(key, default)

        def _fake_click(control, click_kind="left", step_id="", control_id=""):
            clicked.append((control, control_id))
            return True, False, ""

        with _patch_wrapper_readers(), patch.object(
            wt_flow_locator, "_iter_uia_findall_by_automation_id", side_effect=_findall_by_aid(mapping)
        ), patch.object(
            wt_flow_locator, "_step_config_text", side_effect=_config_text
        ), patch.object(
            wt_flow_locator, "_prescroll_foreground_ready", return_value=foreground
        ), patch.object(
            wt_flow_locator, "_safe_click_input", side_effect=_fake_click
        ), patch.object(
            wt_flow_locator, "_prescroll_wake_blank_click"
        ) as blank_click, patch.object(
            wt_flow_locator.time, "sleep", return_value=None
        ):
            result = wt_flow_locator._prescroll_wake_editor(windows, "step_x")
        return result, clicked, blank_click

    def test_configured_wake_control_is_clicked_first(self):
        clicked = []
        with patch.object(wt_flow_locator, "find_flow_control", return_value=_FakeControl("配置框")):
            result, clicked, blank_click = self._run_wake(
                [object()], {}, clicked, config={"preWakeControlId": "ctrl_wake"}
            )
        self.assertTrue(result)
        self.assertEqual(len(clicked), 1)
        self.assertIn("ctrl_wake", clicked[0][1])
        blank_click.assert_not_called()

    def test_selected_editor_tab_wakes_config_area(self):
        # 真实 UIA 结构：页签条 → 面板 → TabItem（浅层 BFS 需要能找到）
        tab1 = _FakeControl("综合1", selected=False, rect={"left": 0, "top": 0, "width": 120, "height": 30})
        tab2 = _FakeControl("综合2", selected=True, rect={"left": 120, "top": 0, "width": 120, "height": 30})
        panel = _FakeControl("", selected=None, control_type="pane", children=[tab1, tab2])
        strip = _FakeControl("", control_type="tab", children=[panel])
        clicked = []
        result, clicked, blank_click = self._run_wake([object()], {TAB_STRIP_ID: [strip]}, clicked)
        self.assertTrue(result)
        self.assertEqual([item[0] for item in clicked], [tab2])
        blank_click.assert_not_called()

    def test_unselected_tabs_are_never_clicked(self):
        tab1 = _FakeControl("综合1", selected=False, rect={"left": 0, "top": 0, "width": 120, "height": 30})
        tab2 = _FakeControl("综合2", selected=None, rect={"left": 120, "top": 0, "width": 120, "height": 30})
        strip = _FakeControl("", control_type="tab", children=[tab1, tab2])
        clicked = []
        result, clicked, _ = self._run_wake([object()], {TAB_STRIP_ID: [strip]}, clicked)
        self.assertFalse(result)
        self.assertEqual(clicked, [])

    def test_keyboard_focused_tab_is_used_when_selection_pattern_missing(self):
        tab1 = _FakeControl("综合2", selected=None, rect={"left": 0, "top": 0, "width": 120, "height": 30}, keyboard_focus=True)
        tab2 = _FakeControl("综合3", selected=None, rect={"left": 120, "top": 0, "width": 120, "height": 30})
        strip = _FakeControl("", control_type="tab", children=[tab1, tab2])
        clicked = []
        result, clicked, _ = self._run_wake([object()], {TAB_STRIP_ID: [strip]}, clicked)
        self.assertTrue(result)
        self.assertEqual([item[0] for item in clicked], [tab1])

    def test_single_tab_is_clicked_without_selection_pattern(self):
        tab = _FakeControl("综合2", selected=None, rect={"left": 0, "top": 0, "width": 120, "height": 30})
        strip = _FakeControl("", control_type="tab", children=[tab])
        clicked = []
        result, clicked, _ = self._run_wake([object()], {TAB_STRIP_ID: [strip]}, clicked)
        self.assertTrue(result)
        self.assertEqual([item[0] for item in clicked], [tab])

    def test_foreground_not_mup_skips_all_clicks(self):
        tab = _FakeControl("综合1", selected=True, rect={"left": 0, "top": 0, "width": 120, "height": 30})
        strip = _FakeControl("", control_type="tab", children=[tab])
        clicked = []
        result, clicked, blank_click = self._run_wake(
            [object()], {TAB_STRIP_ID: [strip]}, clicked, foreground=False
        )
        self.assertFalse(result)
        self.assertEqual(clicked, [])
        blank_click.assert_not_called()


class PrescrollBlankClickGeometryTests(unittest.TestCase):
    def test_no_click_when_editor_container_not_materialized(self):
        # 回归：容器未实体化时旧实现用整窗矩形 (0.9W,0.25H)=(1728,260)，点在右侧地图上
        window = _FakeControl(rect={"left": 0, "top": 0, "right": 1920, "bottom": 1040, "width": 1920, "height": 1040})
        with _patch_wrapper_readers(), patch.object(
            wt_flow_locator, "_iter_uia_findall_by_automation_id", side_effect=_findall_by_aid({})
        ), patch.object(
            wt_flow_locator, "_prescroll_foreground_ready", return_value=True
        ), patch(
            "pyautogui.click"
        ) as pyautogui_click:
            wt_flow_locator._prescroll_wake_blank_click([window], "step_x")
        pyautogui_click.assert_not_called()

    def test_click_stays_inside_editor_container(self):
        editor_rect = {"left": 100, "top": 200, "right": 1100, "bottom": 1000, "width": 1000, "height": 800}
        editor = _FakeControl(rect=editor_rect)
        window = _FakeControl(rect={"left": 0, "top": 0, "right": 1920, "bottom": 1040, "width": 1920, "height": 1040})
        with _patch_wrapper_readers(), patch.object(
            wt_flow_locator, "_iter_uia_findall_by_automation_id",
            side_effect=_findall_by_aid({EDITOR_SCROLL_ID: [editor]}),
        ), patch.object(
            wt_flow_locator, "_prescroll_foreground_ready", return_value=True
        ), patch.object(
            wt_flow_locator.time, "sleep", return_value=None
        ), patch(
            "pyautogui.click"
        ) as pyautogui_click:
            wt_flow_locator._prescroll_wake_blank_click([window], "step_x")
        pyautogui_click.assert_called_once_with(100 + 900, 200 + 200)


class PrescrollWaitLoopWakeTests(unittest.TestCase):
    def test_wait_loop_periodic_wake_uses_real_control_strategy(self):
        with patch.multiple(
            wt_flow_locator,
            _GET_STEP_DEFINITION=lambda step_id: {},
        ), patch.object(
            wt_flow_locator, "_step_config_bool", return_value=True
        ), patch.object(
            wt_flow_locator, "find_flow_control", return_value=None
        ), patch.object(
            wt_flow_locator, "iter_flow_search_windows", return_value=[]
        ), patch.object(
            wt_flow_locator, "_prescroll_wake_editor"
        ) as wake_editor, patch.object(
            wt_flow_locator, "_prescroll_wake_blank_click"
        ) as blank_click:
            self.assertFalse(
                wt_flow_locator.wait_for_flow_control_condition(
                    "step_x", "ctrl_x", condition="exists", timeout_seconds=0.5, poll_interval_seconds=0.1
                )
            )
        self.assertTrue(wake_editor.called)
        blank_click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
