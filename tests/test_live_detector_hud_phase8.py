# encoding: utf-8
"""Phase 8 Epic 4 实时控件探测器迷你 HUD 悬浮模式与流程编辑器一键直注测试"""

import json
import os
import sys
import tkinter as tk
from unittest.mock import MagicMock, patch

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from tests._tk_support import shared_tk_root
import control_live_detector as cld
import WT_Flow_Editor as fe


class TestLiveDetectorHUDAndPayload:
    """测试探测器定位器载荷构造与迷你 HUD 悬浮模式。"""

    @classmethod
    def setup_class(cls):
        _, cls.root = shared_tk_root()

    def test_build_locator_payload_from_raw_ctrl(self):
        """测试从纯原生捕获控件构造标准定位器载荷。"""
        raw_ctrl = {
            "name": "测风塔计算",
            "automationId": "Btn_CalculateMast",
            "className": "WPF_Button",
            "controlType": "Button",
            "windowTitle": "WT自动化主控台",
        }
        payload = cld.ControlLiveDetectorWindow._build_locator_payload(raw_ctrl, None)
        assert payload["name"] == "测风塔计算"
        assert payload["targetMethod"] == "automation_id"
        assert payload["targetValue"] == "Btn_CalculateMast"
        assert payload["source"] == "live_detector"
        assert payload["windowTitle"] == "WT自动化主控台"

        # 缺少 automationId 时回退到 name
        raw_no_aid = {
            "name": "保存配置",
            "className": "Button",
            "controlType": "Button",
        }
        payload2 = cld.ControlLiveDetectorWindow._build_locator_payload(raw_no_aid, None)
        assert payload2["targetMethod"] == "name"
        assert payload2["targetValue"] == "保存配置"

    def test_build_locator_payload_from_best_match(self):
        """测试存在总库最佳匹配时优先使用控件库权威定义。"""
        raw_ctrl = {
            "name": "未知控件",
            "automationId": "raw_aid_123",
            "className": "RawClass",
            "controlType": "Button",
        }
        best_match = {
            "score": 150,
            "library_definition": {
                "id": "mast_calc_btn_001",
                "name": "测风塔参数计算按钮",
                "targetMethod": "automation_id,control_type",
                "targetValue": "Btn_Calc,Button",
                "windowTitle": "计算对话框",
                "controlType": "Button",
                "className": "Button",
                "automationId": "Btn_Calc",
            }
        }
        payload = cld.ControlLiveDetectorWindow._build_locator_payload(raw_ctrl, best_match)
        assert payload["source"] == "library_match"
        assert payload["id"] == "mast_calc_btn_001"
        assert payload["name"] == "测风塔参数计算按钮"
        assert payload["targetMethod"] == "automation_id,control_type"
        assert payload["targetValue"] == "Btn_Calc,Button"
        assert payload["windowTitle"] == "计算对话框"
        assert payload["score"] == 150

    def test_hud_mode_toggle_and_properties(self):
        """测试迷你 HUD 悬浮模式的切换、窗体精简与参数还原。"""
        detector = cld.ControlLiveDetectorWindow(self.root, start_in_hud=False)
        try:
            assert detector.hud_mode is False

            # 切换进入迷你 HUD 模式
            detector.toggle_hud_mode()
            assert detector.hud_mode is True
            assert detector.hud_container.winfo_manager() == "pack"
            assert detector.standard_container.winfo_manager() == ""

            # 切换还原回全景标准模式
            detector.toggle_hud_mode()
            assert detector.hud_mode is False
            assert detector.hud_container.winfo_manager() == ""
            assert detector.standard_container.winfo_manager() == "pack"
        finally:
            getattr(detector.window, "destroy")()

    def test_inject_to_flow_action_with_callback(self):
        """测试当配置了 on_inject 回调时，直接回调注入。"""
        callback_mock = MagicMock()
        detector = cld.ControlLiveDetectorWindow(self.root, on_inject=callback_mock)
        try:
            detector.current_ctrl_info = {
                "name": "一键执行",
                "automationId": "btn_run",
                "controlType": "Button",
            }
            detector.current_matches = [{"score": 100}]

            with patch.object(detector, "_show_inject_toast"):
                detector.inject_to_flow_action()

            callback_mock.assert_called_once_with(
                detector.current_ctrl_info,
                {"score": 100}
            )
        finally:
            getattr(detector.window, "destroy")()

    def test_inject_to_flow_action_with_flow_editor(self):
        """测试关联 flow_editor 时调用 inject_control_as_step。"""
        mock_editor = MagicMock()
        detector = cld.ControlLiveDetectorWindow(self.root, flow_editor=mock_editor)
        try:
            detector.current_ctrl_info = {
                "name": "测风塔高程输入框",
                "automationId": "txt_mast_height",
                "controlType": "Edit",
            }
            detector.current_matches = []

            with patch.object(detector, "_show_inject_toast"):
                detector.inject_to_flow_action()

            mock_editor.inject_control_as_step.assert_called_once_with(
                detector.current_ctrl_info,
                None
            )
        finally:
            getattr(detector.window, "destroy")()


class TestFlowEditorStepInjection:
    """测试流程编辑器 inject_control_as_step 功能及类型动作推断。"""

    @classmethod
    def setup_class(cls):
        _, cls.root = shared_tk_root()

    def test_flow_editor_inject_control_as_step_button(self):
        """测试将 Button 控件注入为 click 步骤并添加到 steps。"""
        editor = MagicMock()
        editor.root = self.root
        editor.steps = []
        editor.dirty = False
        editor.status_var = tk.StringVar(self.root)
        editor._match_control_in_master_library = MagicMock(return_value=None)
        editor._infer_action_type = fe.FlowEditorApp._infer_action_type.__get__(editor, fe.FlowEditorApp)
        editor._build_step_from_control = fe.FlowEditorApp._build_step_from_control.__get__(editor, fe.FlowEditorApp)
        editor._append_step_to_flow = fe.FlowEditorApp._append_step_to_flow.__get__(editor, fe.FlowEditorApp)
        editor._refresh_steps_tree = MagicMock()
        editor._select_step = MagicMock()
        editor._set_title = MagicMock()

        # 调用真实 inject_control_as_step
        inject_fn = fe.FlowEditorApp.inject_control_as_step.__get__(editor, fe.FlowEditorApp)

        ctrl_data = {
            "name": "刷新测风塔列表",
            "automationId": "btn_refresh_masts",
            "controlType": "Button",
            "className": "Button",
            "windowTitle": "WT主界面",
        }

        step = inject_fn(ctrl_data)
        assert step is not None
        assert step["actionConfig"]["action"] == "click"
        assert "刷新测风塔列表" in step["name"]
        assert step["description"].startswith("实时探测直注：")
        assert len(editor.steps) == 1
        assert editor.dirty is True
        editor._refresh_steps_tree.assert_called_once()
        editor._select_step.assert_called_once_with(0)

    def test_flow_editor_inject_control_as_step_textbox(self):
        """测试将 Edit/TextBox 控件注入为 type_text 步骤。"""
        editor = MagicMock()
        editor.root = self.root
        editor.steps = []
        editor.dirty = False
        editor.status_var = tk.StringVar(self.root)
        editor._match_control_in_master_library = MagicMock(return_value=None)
        editor._infer_action_type = fe.FlowEditorApp._infer_action_type.__get__(editor, fe.FlowEditorApp)
        editor._build_step_from_control = fe.FlowEditorApp._build_step_from_control.__get__(editor, fe.FlowEditorApp)
        editor._append_step_to_flow = fe.FlowEditorApp._append_step_to_flow.__get__(editor, fe.FlowEditorApp)
        editor._refresh_steps_tree = MagicMock()
        editor._select_step = MagicMock()
        editor._set_title = MagicMock()

        inject_fn = fe.FlowEditorApp.inject_control_as_step.__get__(editor, fe.FlowEditorApp)

        ctrl_data = {
            "name": "风速上限输入",
            "automationId": "txt_speed_limit",
            "controlType": "Edit",
            "className": "TextBox",
            "windowTitle": "参数设置",
        }

        step = inject_fn(ctrl_data)
        assert step is not None
        assert step["actionConfig"]["action"] == "type_text"
        assert "风速上限输入" in step["name"]

    def test_flow_editor_inject_control_as_step_combobox(self):
        """测试将 ComboBox 控件注入为 select_dropdown_item_runtime 步骤。"""
        editor = MagicMock()
        editor.root = self.root
        editor.steps = []
        editor.dirty = False
        editor.status_var = tk.StringVar(self.root)
        editor._match_control_in_master_library = MagicMock(return_value=None)
        editor._infer_action_type = fe.FlowEditorApp._infer_action_type.__get__(editor, fe.FlowEditorApp)
        editor._build_step_from_control = fe.FlowEditorApp._build_step_from_control.__get__(editor, fe.FlowEditorApp)
        editor._append_step_to_flow = fe.FlowEditorApp._append_step_to_flow.__get__(editor, fe.FlowEditorApp)
        editor._refresh_steps_tree = MagicMock()
        editor._select_step = MagicMock()
        editor._set_title = MagicMock()

        inject_fn = fe.FlowEditorApp.inject_control_as_step.__get__(editor, fe.FlowEditorApp)

        ctrl_data = {
            "name": "测风塔扇区选择",
            "automationId": "cmb_sector",
            "controlType": "ComboBox",
            "className": "ComboBox",
            "windowTitle": "参数设置",
        }

        step = inject_fn(ctrl_data)
        assert step is not None
        assert step["actionConfig"]["action"] == "select_dropdown_item_runtime"
