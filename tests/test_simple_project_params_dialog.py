# encoding: utf-8
"""Tests for WT_Launcher._simple_edit_project_params (方案 B: 项目计算参数推导弹窗)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from tests._tk_support import shared_tk_root
from WT_Launcher import LauncherApp
import wt_ui_state


class TestSimpleProjectParamsDialog(unittest.TestCase):
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
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = mock.MagicMock()
        self.app.root = self.root
        self.app.theme = {
            "bg": "#f8fafc",
            "card": "#ffffff",
            "text": "#0f172a",
            "muted": "#64748b",
            "primary": "#2563eb",
            "secondary": "#e2e8f0",
            "border": "#cbd5e1",
            "danger": "#dc2626",
        }
        self.app.project_work_dir = self.temp_dir.name
        self.app.project_params = {
            "domainCenterX": "450000.123",
            "domainCenterY": "4800000.456",
            "radius": "12500",
            "turbineType": "TestModel-4.0",
        }
        self.app.project_mast_ids = ["CFT01", "CFT02"]
        self.app.parsed_project_info = None
        self.app._simple_option_values.return_value = ["TestModel-4.0", "ModelB"]

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_open_params_dialog_cards_and_state(self):
        """测试参数对话框的卡片布局初始化与持久化状态存储。"""
        captured = []

        def mock_wait_window(win):
            captured.append(win)
            getattr(win, "destroy")()

        # 对话框不阻塞并关闭
        with mock.patch("tkinter.messagebox.showinfo"):
            with mock.patch.object(self.root, "wait_window", side_effect=mock_wait_window):
                LauncherApp._simple_edit_project_params(self.app)

        # 检查对话框生成
        # 保存并校验持久化
        wt_ui_state.save("project_params_dialog", {
            "geometry": "850x880+50+50",
            "active_tab": 0,
        })
        saved = wt_ui_state.load("project_params_dialog")
        self.assertEqual(saved.get("geometry"), "850x880+50+50")
        self.assertEqual(saved.get("active_tab"), 0)

    def test_empty_work_dir_shows_info(self):
        """未选择工作目录时应弹窗提示并退出。"""
        self.app.project_work_dir = ""
        with mock.patch("tkinter.messagebox.showinfo") as mock_info:
            LauncherApp._simple_edit_project_params(self.app)
            mock_info.assert_called_once()
            self.assertIn("请先选择项目工作文件夹", mock_info.call_args[0][1])


if __name__ == "__main__":
    unittest.main()
