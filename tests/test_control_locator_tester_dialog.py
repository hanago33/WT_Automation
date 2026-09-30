# encoding: utf-8
"""Tests for WT_Flow_Editor.ControlLocatorTesterDialog (方案 C: 控件定位检验透视台)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from _tk_support import shared_tk_root
from WT_Flow_Editor import ControlLocatorTesterDialog
import wt_ui_state


class TestControlLocatorTesterDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tk, cls.root = shared_tk_root()
        cls._state_tmp = tempfile.TemporaryDirectory()
        cls._orig_state_env = os.environ.get("WT_UI_STATE_FILE")
        os.environ["WT_UI_STATE_FILE"] = os.path.join(cls._state_tmp.name, "ui_state.json")

    @classmethod
    def tearDownClass(cls):
        if cls._orig_state_env is not None:
            os.environ["WT_UI_STATE_FILE"] = cls._orig_state_env
        else:
            os.environ.pop("WT_UI_STATE_FILE", None)
        cls._state_tmp.cleanup()

    def setUp(self):
        self.dialogs = []

    def tearDown(self):
        for dlg in self.dialogs:
            try:
                getattr(dlg.window, "destroy")()
            except Exception:
                pass
        self.dialogs.clear()

    def _create_dialog(self, initial_control=None):
        dlg = ControlLocatorTesterDialog(self.root, initial_control=initial_control)
        self.dialogs.append(dlg)
        return dlg

    def test_dialog_init_default(self):
        """测试检验器默认初始化状态与控件元素存在性。"""
        dlg = self._create_dialog()
        self.assertIn("透视台", dlg.window.title())
        self.assertEqual(dlg.var_locator_method.get(), "automation_id")
        self.assertEqual(dlg.var_test_result.get(), "待检验")
        self.assertEqual(dlg.lbl_status_badge.cget("text"), "● 待检验")
        self.assertTrue(dlg.var_auto_highlight.get())
        self.assertIsNotNone(dlg.window_listbox)
        self.assertIsNotNone(dlg.result_text)

    def test_dialog_init_with_initial_control(self):
        """测试携带采集候选控件字典初始化时的字段装载。"""
        ctrl = {
            "name": "btn_save",
            "targetMethod": "name",
            "targetValue": "保存",
            "windowTitle": "WT主窗口",
        }
        dlg = self._create_dialog(initial_control=ctrl)
        self.assertEqual(dlg.var_locator_method.get(), "name")
        self.assertEqual(dlg.var_locator_value.get(), "保存")
        self.assertEqual(dlg.var_target_window.get(), "WT主窗口")
        self.assertIn("btn_save", dlg.var_status.get())

    def test_window_list_filter_and_select(self):
        """测试窗口列表的即时过滤与选中绑定。"""
        dlg = self._create_dialog()
        dlg._all_windows = [
            {"title": "WT自动化主控台", "className": "TkTopLevel", "hwnd": 1001},
            {"title": "记事本", "className": "Notepad", "hwnd": 1002},
            {"title": "计算器", "className": "CalcFrame", "hwnd": 1003},
        ]
        dlg.var_window_filter.set("记事本")
        dlg._apply_window_filter()
        self.assertEqual(dlg.window_listbox.size(), 1)
        self.assertIn("记事本", dlg.window_listbox.get(0))

        # 选中第一项
        dlg.window_listbox.selection_set(0)
        dlg._on_window_select()
        self.assertEqual(dlg.var_target_window.get(), "记事本")
        self.assertEqual(dlg._selected_hwnd, 1002)

    def test_update_result_view_success_and_copy(self):
        """测试成功定位结果的指标卡片更新与剪贴板复制。"""
        dlg = self._create_dialog()

        class DummyRect:
            left = 120
            top = 200
            right = 260
            bottom = 250

        dlg._update_result_view(
            status="定位成功",
            elapsed_ms=18.6,
            success=True,
            target_title="记事本",
            method="name",
            value="确定",
            name="确定",
            class_name="Button",
            automation_id="btn_ok",
            rect=DummyRect(),
            is_visible=True,
            is_enabled=True,
        )

        self.assertEqual(dlg.lbl_m_status.cget("text"), "✓ 成功命中")
        self.assertEqual(dlg.lbl_m_timing.cget("text"), "18.6 ms")
        self.assertEqual(dlg.lbl_m_rect.cget("text"), "140×50 @ (120, 200)")
        self.assertIn("140×50", dlg.result_text.get("1.0", "end"))

        # 测试复制
        dlg._copy_report()
        self.assertIn("已成功复制", dlg.var_status.get())

    def test_apply_and_close(self):
        """测试采纳当前定位规则并返回结果字典。"""
        dlg = self._create_dialog()
        dlg.var_target_window.set("测试窗口")
        dlg.var_locator_method.set("automation_id")
        dlg.var_locator_value.set("txt_input")

        dlg._apply_and_close()
        self.assertIsNotNone(dlg.result)
        self.assertEqual(dlg.result["windowTitle"], "测试窗口")
        self.assertEqual(dlg.result["targetMethod"], "automation_id")
        self.assertEqual(dlg.result["targetValue"], "txt_input")

    def test_flash_highlight(self):
        """测试屏幕半透明高亮线框生成与自动销毁。"""
        dlg = self._create_dialog()

        class DummyRect:
            left = 150
            top = 150
            right = 300
            bottom = 220

        overlay = dlg._flash_highlight(DummyRect())
        self.assertIsNotNone(overlay)
        getattr(overlay, "destroy")()

    def test_ui_state_persistence(self):
        """测试窗口参数与状态持久化。"""
        dlg = self._create_dialog()
        dlg.var_target_window.set("持久化窗口")
        dlg.var_locator_method.set("class_name")
        dlg.var_locator_value.set("EditControl")
        dlg._save_ui_state()

        saved = wt_ui_state.load("flow_editor_locator_tester")
        self.assertIsInstance(saved, dict)
        self.assertEqual(saved.get("target_window"), "持久化窗口")
        self.assertEqual(saved.get("locator_method"), "class_name")
        self.assertEqual(saved.get("locator_value"), "EditControl")


if __name__ == "__main__":
    unittest.main()
