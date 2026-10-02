# -*- coding: utf-8 -*-
"""Unit tests for WT_Flow_Editor Phase 8 Epic 2:
- Inline step validation badges in step tree (_validate_step_inline)
- Two-way interactive flow graph integration (wt_flow_graph with flow_data & on_node_click)
- In-place single step test run (cmd_test_run_current_step & StepTestRunDialog)
"""
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import WT_Flow_Editor
import wt_flow_graph


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class TestInlineStepValidation(unittest.TestCase):
    def setUp(self):
        self.app = object.__new__(WT_Flow_Editor.FlowEditorApp)

    def test_validate_step_missing_id(self):
        step = {"name": "无ID步骤"}
        self.assertEqual(self.app._validate_step_inline(step), "缺少ID")

    def test_validate_action_step_missing_action(self):
        step = {
            "id": "step_01",
            "name": "步骤一",
            "actionType": "action",
            "actionConfig": {}
        }
        self.assertEqual(self.app._validate_step_inline(step), "未设动作")

    def test_validate_action_step_missing_target_control(self):
        step = {
            "id": "step_01",
            "name": "步骤一",
            "actionType": "action",
            "actionConfig": {"action": "click"}
        }
        self.assertEqual(self.app._validate_step_inline(step), "缺目标控件")

    def test_validate_action_step_with_target_valid(self):
        step = {
            "id": "step_01",
            "name": "步骤一",
            "actionType": "action",
            "actionConfig": {"action": "click", "controlId": "btn_confirm"}
        }
        self.assertEqual(self.app._validate_step_inline(step), "")

    def test_validate_anchor_action_missing_anchor(self):
        step = {
            "id": "step_01",
            "name": "相对锚点点击",
            "actionType": "action",
            "actionConfig": {"action": "click_relative_anchor"}
        }
        self.assertEqual(self.app._validate_step_inline(step), "缺锚点控件")

    def test_validate_flow_ref_missing_ref(self):
        step = {
            "id": "step_01",
            "name": "引用流程",
            "actionType": "flow_ref",
        }
        self.assertEqual(self.app._validate_step_inline(step), "缺引用包")

    def test_validate_timeout_out_of_bounds(self):
        step = {
            "id": "step_01",
            "name": "超时异常",
            "actionType": "script",
            "timeout": 9999
        }
        self.assertEqual(self.app._validate_step_inline(step), "超时值异常")


class TestFlowGraphIntegration(unittest.TestCase):
    def test_parse_flow_data_to_nodes(self):
        data = {
            "description": "测试链路",
            "flowPackages": [{"id": "pkg_1", "name": "核心包", "stepIds": ["s1", "s2"]}],
            "steps": [
                {"id": "s1", "name": "点击按钮", "actionConfig": {"action": "click"}, "controls": [{"name": "确认"}]},
                {"id": "s2", "name": "输入文本", "actionConfig": {"action": "input"}},
            ]
        }
        result = wt_flow_graph.parse_flow_data_to_nodes(data)
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "核心包")
        self.assertEqual(len(result["nodes"]), 2)
        self.assertEqual(result["nodes"][0]["id"], "s1")
        self.assertEqual(result["nodes"][0]["action"], "click")
        self.assertEqual(result["nodes"][0]["control"], "确认")

    def test_flow_graph_node_click_callback(self):
        data = {
            "steps": [{"id": "s1", "name": "第一步", "actionConfig": {"action": "click"}}]
        }
        mock_cb = MagicMock()
        graph_win = object.__new__(wt_flow_graph.FlowGraphWindow)
        graph_win.on_node_click = mock_cb
        graph_win._open_detail = MagicMock()

        node = {"id": "s1", "name": "第一步"}
        graph_win._handle_node_click(node)
        mock_cb.assert_called_once_with("s1")
        graph_win._open_detail.assert_called_once_with(node)


class TestStepTestRunDialogSetup(unittest.TestCase):
    def test_cmd_test_run_current_step_validation(self):
        app = object.__new__(WT_Flow_Editor.FlowEditorApp)
        app.root = MagicMock()
        app.steps = [{"id": "step_01", "name": "第一步"}]
        app.selected_index = None

        with patch.object(WT_Flow_Editor.messagebox, "showinfo") as mock_info:
            app.cmd_test_run_current_step()
            mock_info.assert_called()


if __name__ == "__main__":
    unittest.main()
