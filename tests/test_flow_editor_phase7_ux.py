# -*- coding: utf-8 -*-
"""Unit tests for WT_Flow_Editor Phase 7 UI/UX modernizations:
- Step real-time keyword search and category filtering
- Step count badge updates
- Form dirty badge tracking
- Step context menu commands: duplicate, copy/paste, enable/disable toggle
- CLI arguments support (--step-id)
"""
import copy
import unittest
from unittest.mock import patch, MagicMock

import WT_Flow_Editor as E


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FlowEditorHarness(E.FlowEditorApp):
    def __getattr__(self, name):
        if name.startswith("var_"):
            value = FakeVar("")
            object.__setattr__(self, name, value)
            return value
        raise AttributeError(name)


def make_harness(steps=None):
    harness = object.__new__(FlowEditorHarness)
    harness.root = MagicMock()
    harness.steps = copy.deepcopy(steps or [])
    harness.selected_index = 0 if harness.steps else None
    harness.step_search_query = FakeVar("")
    harness.step_filter_category = FakeVar("全部")
    harness.step_count_badge_var = FakeVar("")
    harness.step_title_display_var = FakeVar("")
    harness.step_dirty_badge_var = FakeVar("")
    harness.status_var = FakeVar("")
    harness.current_package_step_filter_id = ""
    harness.flow_packages = []
    harness._step_clipboard = None
    harness.step_tree = MagicMock()
    harness.step_tree.selection.return_value = ["0"]
    harness.btn_apply_sticky = MagicMock()
    harness.btn_reset_sticky = MagicMock()
    harness.badge_dirty_sticky = MagicMock()
    harness._get_package_by_id = lambda pid: None
    harness._load_step_into_form = MagicMock()
    harness._confirm_discard_form_changes = lambda: True
    harness._update_step_header_display = MagicMock()
    harness._refresh_overview = MagicMock()
    return harness


class TestFlowEditorPhase7UX(unittest.TestCase):
    def test_cli_parser_accepts_step_id_and_flow_file(self):
        parser = E.build_cli_parser()
        args = parser.parse_args(["my_flow.json", "--step-id", "step_05"])
        self.assertEqual(args.flow_file, "my_flow.json")
        self.assertEqual(args.step_id, "step_05")

    def test_get_visible_step_indexes_unfiltered(self):
        steps = [
            {"id": "step_01", "name": "启动系统", "enabled": True},
            {"id": "step_02", "name": "点击按钮", "actionType": "click", "enabled": False},
            {"id": "step_03", "name": "执行子链路", "subflow": "sub.json", "enabled": True},
        ]
        h = make_harness(steps)
        visible = h._get_visible_step_indexes()
        self.assertEqual(visible, [0, 1, 2])

    def test_get_visible_step_indexes_query_filter(self):
        steps = [
            {"id": "step_01", "name": "启动系统", "enabled": True},
            {"id": "step_02", "name": "点击登录按钮", "actionType": "click", "enabled": True},
            {"id": "step_03", "name": "导入测风塔数据", "enabled": True},
        ]
        h = make_harness(steps)
        h.step_search_query.set("登录")
        visible = h._get_visible_step_indexes()
        self.assertEqual(visible, [1])

        h.step_search_query.set("step_03")
        visible = h._get_visible_step_indexes()
        self.assertEqual(visible, [2])

    def test_get_visible_step_indexes_category_filter(self):
        steps = [
            {"id": "step_01", "name": "启动系统", "enabled": True, "actionType": "launch"},
            {"id": "step_02", "name": "已停用步骤", "enabled": False, "actionType": "click"},
            {"id": "step_03", "name": "子链路步骤", "enabled": True, "subflow": "sub.json"},
            {"id": "step_04", "name": "图像匹配步骤", "enabled": True, "action": {"image_template": "tpl.png"}},
        ]
        h = make_harness(steps)

        h.step_filter_category.set("仅启用")
        self.assertEqual(h._get_visible_step_indexes(), [0, 2, 3])

        h.step_filter_category.set("仅停用")
        self.assertEqual(h._get_visible_step_indexes(), [1])

        h.step_filter_category.set("仅动作步")
        self.assertEqual(h._get_visible_step_indexes(), [0, 1])

        h.step_filter_category.set("仅子链路")
        self.assertEqual(h._get_visible_step_indexes(), [2])

        h.step_filter_category.set("带模板")
        self.assertEqual(h._get_visible_step_indexes(), [3])

    def test_step_copy_and_paste_below(self):
        steps = [
            {"id": "step_01", "name": "原始步骤", "actionType": "click"},
            {"id": "step_02", "name": "第二步", "actionType": "input"},
        ]
        h = make_harness(steps)
        h.selected_index = 0
        h._mark_dirty = MagicMock()
        h._refresh_steps_tree = MagicMock()
        h._select_step_by_index = MagicMock()

        # 复制步骤
        h.cmd_copy_selected_step()
        self.assertIsNotNone(h._step_clipboard)
        self.assertEqual(h._step_clipboard["id"], "step_01")

        # 粘贴在下方
        h.cmd_paste_step_below()
        self.assertEqual(len(h.steps), 3)
        self.assertEqual(h.steps[1]["name"], "原始步骤 (粘贴)")
        self.assertNotEqual(h.steps[1]["id"], "step_01")
        self.assertTrue(h.steps[1]["id"].startswith("step_01_copy"))

    def test_cmd_toggle_selected_step_enabled(self):
        steps = [
            {"id": "step_01", "name": "步骤一", "enabled": True},
        ]
        h = make_harness(steps)
        h.selected_index = 0
        h._mark_dirty = MagicMock()
        h._refresh_steps_tree = MagicMock()

        h.cmd_toggle_selected_step_enabled()
        self.assertFalse(h.steps[0]["enabled"])

        h.cmd_toggle_selected_step_enabled()
        self.assertTrue(h.steps[0]["enabled"])

    def test_focus_step_by_id_clears_filter_and_focuses(self):
        steps = [
            {"id": "step_01", "name": "步骤一"},
            {"id": "step_target", "name": "目标步骤"},
        ]
        h = make_harness(steps)
        h.step_search_query.set("something_else")
        h.step_filter_category.set("仅停用")
        h._refresh_steps_tree = MagicMock()
        h._select_step = MagicMock()

        found = h._focus_step_by_id("step_target")
        self.assertTrue(found)
        self.assertEqual(h.step_search_query.get(), "")
        self.assertEqual(h.step_filter_category.get(), "全部")
        h._select_step.assert_called_with(1)

    def test_is_form_dirty_detection(self):
        steps = [
            {"id": "step_01", "name": "步骤一", "actionType": "click", "notes": ""},
        ]
        h = make_harness(steps)
        h.selected_index = 0
        h.var_id.set("step_01")
        h.var_name.set("步骤一")
        h.var_action_type.set("click")
        h._form_baseline = h._form_snapshot()

        # 初始未改动
        self.assertFalse(h._is_form_dirty())

        # 修改名称后变为 dirty
        h.var_name.set("步骤一 (已改动)")
        self.assertTrue(h._is_form_dirty())


if __name__ == "__main__":
    unittest.main()
