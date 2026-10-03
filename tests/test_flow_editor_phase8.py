# -*- coding: utf-8 -*-
"""Unit tests for WT_Flow_Editor Phase 8 Epic 2:
- Inline step validation badges in step tree (_validate_step_inline)
- Two-way interactive flow graph integration (wt_flow_graph with flow_data & on_node_click)
- In-place single step test run (cmd_test_run_current_step & StepTestRunDialog)
"""
import copy
import os
import sys
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from tests._tk_support import shared_tk_root

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


class TestProgrammaticReselectSemantics:
    """程序性重选中的事件语义回归：同索引选中不得再触发确认或表单重载。

    <<TreeviewSelect>> 为异步投递，selection_set 的抑制标志复位后事件才到达
    _on_tree_select。同索引重选若不短路：「取消」确认会经 _restore_step_selection
    无限回环；空格切换启用（已写入步骤数据）会被误报为未应用修改而弹窗；过滤
    每键还会引发双倍全量表单重载。本组测试以真实 Tk 事件循环验证。
    """

    @classmethod
    def setup_class(cls):
        cls.tk, cls.root = shared_tk_root()

    def _steps(self):
        return [
            {"id": f"s{i}", "name": f"步骤{i}", "actionType": "script", "enabled": True}
            for i in range(3)
        ]

    def _make_editor(self, steps):
        editor = object.__new__(WT_Flow_Editor.FlowEditorApp)
        editor.root = self.root
        editor.steps = copy.deepcopy(steps)
        editor.selected_index = 0
        editor.dirty = False
        editor.step_search_query = FakeVar("")
        editor.step_filter_category = FakeVar("全部")
        editor.current_package_step_filter_id = ""
        editor._get_package_by_id = lambda pid: None
        editor.status_var = FakeVar("")
        editor.step_count_badge_var = FakeVar("")
        editor.step_tree = ttk.Treeview(self.root, columns=("a",), show="headings")
        for i in range(len(editor.steps)):
            editor.step_tree.insert("", "end", iid=str(i), values=(i,))
        editor.step_tree.selection_set("0")
        editor.step_tree.bind("<<TreeviewSelect>>", editor._on_tree_select)
        editor._suppress_tree_select_event = False
        editor._dragging_step_iid = ""
        editor._set_title = lambda: None
        # 摘要类辅助与本组测试无关，置空让 _refresh_steps_tree 以真实逻辑运行
        editor._build_step_package_names_map = lambda: {}
        editor._validate_step_inline = lambda step: ""
        editor._build_step_tree_action_summary = lambda step: ""
        editor._build_step_tree_target_summary = lambda step: ""

        def visible_indexes():
            query = editor.step_search_query.get().strip().lower()
            if not query:
                return list(range(len(editor.steps)))
            return [i for i, s in enumerate(editor.steps) if query in str(s.get("name", "")).lower()]

        editor._get_visible_step_indexes = visible_indexes

        self.confirm_calls = []
        self.confirm_answer = True
        self.select_calls = []
        editor._confirm_discard_form_changes = self._fake_confirm

        def fake_select(index, preserve_selection=False):
            self.select_calls.append(index)
            editor.selected_index = index

        editor._select_step = fake_select
        return editor

    def _fake_confirm(self):
        self.confirm_calls.append(1)
        return self.confirm_answer

    def _pump(self, rounds=10):
        for _ in range(rounds):
            self.root.update()

    def test_toggle_enabled_does_not_retrigger_confirm(self):
        """空格切换启用：改动已写入步骤数据，异步重选不得触发确认弹窗。"""
        editor = self._make_editor(self._steps())
        editor.var_enabled = FakeVar(True)
        editor._form_baseline = editor._form_snapshot()  # 干净基线

        editor.cmd_toggle_selected_step_enabled()
        self._pump()
        getattr(editor.step_tree, "destroy")()

        assert editor.steps[0]["enabled"] is False
        assert self.confirm_calls == []
        assert editor._form_baseline == editor._form_snapshot()

    def test_toggle_preserves_other_fields_dirty_state(self):
        """脏表单（其他字段未应用修改）按空格切换启用：基线仅同步 var_enabled 项。

        其他字段的脏标记必须原样保留——随后切步时确认框必须弹出拦截，
        未应用的编辑不得被静默吞掉。
        """
        editor = self._make_editor(self._steps())
        editor.var_name = FakeVar("原始名称")
        editor.var_enabled = FakeVar(True)
        editor._form_baseline = editor._form_snapshot()  # 干净基线
        editor.var_name.set("改成一半的名字")  # 用户改了名称，未点「应用到步骤」

        editor.cmd_toggle_selected_step_enabled()
        self._pump()
        getattr(editor.step_tree, "destroy")()

        assert self.confirm_calls == []  # 切换本身不弹窗
        assert editor._form_snapshot() != editor._form_baseline  # 仍判定为脏

        # 随后点击切换到其他步骤：必须被确认拦截，取消后留在原步骤
        self.confirm_answer = False
        self.confirm_calls.clear()
        editor.step_tree = ttk.Treeview(self.root, columns=("a",), show="headings")
        for i in range(len(editor.steps)):
            editor.step_tree.insert("", "end", iid=str(i), values=(i,))
        editor.step_tree.selection_set("0")
        editor.step_tree.bind("<<TreeviewSelect>>", editor._on_tree_select)
        editor.step_tree.selection_set("1")  # 模拟点击另一行
        self._pump()
        getattr(editor.step_tree, "destroy")()

        assert len(self.confirm_calls) == 1
        assert self.select_calls == []
        assert editor.selected_index == 0

    def test_filter_cancel_confirms_once_without_loop(self):
        """脏表单过滤切步 + 用户取消：确认恰好一次，无回环，不切步。"""
        editor = self._make_editor(self._steps())
        editor._form_baseline = (("var_name", "未应用修改"),)  # 与空快照不一致 → 视为有未应用修改
        self.confirm_answer = False  # 用户点「取消」
        editor.step_search_query.set("步骤1")  # 当前编辑步骤不在过滤结果里

        editor._on_step_filter_changed()
        self._pump()
        getattr(editor.step_tree, "destroy")()

        assert len(self.confirm_calls) == 1
        assert self.select_calls == []
        assert editor.selected_index == 0

    def test_filter_switch_on_clean_form_reloads_once(self):
        """干净表单过滤切步：_select_step 恰好一次（修复前异步事件双倍重载）。"""
        editor = self._make_editor(self._steps())
        editor._form_baseline = None  # 无基线 → 确认静默放行
        editor.step_search_query.set("步骤2")

        editor._on_step_filter_changed()
        self._pump()
        getattr(editor.step_tree, "destroy")()

        assert len(self.confirm_calls) == 1
        assert self.select_calls == [2]
        assert editor.selected_index == 2


if __name__ == "__main__":
    unittest.main()
