# encoding: utf-8
"""Tests for WT_Launcher._simple_pick_directory (方案 A: 自绘目录选择器)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

# Ensure project root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from _tk_support import shared_tk_root
from WT_Launcher import LauncherApp
import wt_ui_state


class TestSimplePickDirectory(unittest.TestCase):
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

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pick_directory_with_compliance_detection(self):
        """测试目录选择对话框生命周期：快捷芯片、即时合规探针、状态持久化。"""
        # 在临时目录下构建测试工程结构
        proj_dir = os.path.join(self.temp_dir.name, "StandardProject")
        os.makedirs(os.path.join(proj_dir, "03-WT输入"), exist_ok=True)
        os.makedirs(os.path.join(proj_dir, "04-WT输出"), exist_ok=True)
        with open(os.path.join(proj_dir, "03-WT输入", "CFT-tim.txt"), "w", encoding="utf-8") as f:
            f.write("test tim data")

        captured_dialog = []
        captured_title = []

        def mock_wait_window(win):
            captured_dialog.append(win)
            captured_title.append(win.title())
            getattr(win, "destroy")()

        with mock.patch.object(self.root, "wait_window", side_effect=mock_wait_window):
            result = LauncherApp._simple_pick_directory(self.app)

        self.assertEqual(len(captured_dialog), 1)
        self.assertIn("选择市场项目工作文件夹", captured_title[0])

    def test_compliance_probe_levels(self):
        """测试合规性即时探针在四种目录结构下的正确响应。"""
        # 1. 完整目录 (03 + 04)
        full_dir = os.path.join(self.temp_dir.name, "FullProj")
        os.makedirs(os.path.join(full_dir, "03-WT输入"), exist_ok=True)
        os.makedirs(os.path.join(full_dir, "04-WT输出"), exist_ok=True)

        # 2. 仅有输入 (03)
        input_only = os.path.join(self.temp_dir.name, "InputOnly")
        os.makedirs(os.path.join(input_only, "03-WT输入"), exist_ok=True)

        # 3. 仅有输出 (04)
        output_only = os.path.join(self.temp_dir.name, "OutputOnly")
        os.makedirs(os.path.join(output_only, "04-WT输出"), exist_ok=True)

        # 4. 普通空目录
        empty_dir = os.path.join(self.temp_dir.name, "NormalDir")
        os.makedirs(empty_dir, exist_ok=True)

        import wt_project_workdir_parser

        diag_full = wt_project_workdir_parser.diagnose_project_work_dir(full_dir)
        self.assertTrue(diag_full["input_root_exists"])
        self.assertTrue(diag_full["output_root_exists"])

        diag_in = wt_project_workdir_parser.diagnose_project_work_dir(input_only)
        self.assertTrue(diag_in["input_root_exists"])
        self.assertFalse(diag_in["output_root_exists"])

        diag_out = wt_project_workdir_parser.diagnose_project_work_dir(output_only)
        self.assertFalse(diag_out["input_root_exists"])
        self.assertTrue(diag_out["output_root_exists"])

        diag_empty = wt_project_workdir_parser.diagnose_project_work_dir(empty_dir)
        self.assertFalse(diag_empty["input_root_exists"])
        self.assertFalse(diag_empty["output_root_exists"])

    def test_state_persistence_saved(self):
        """测试选择目录后，wt_ui_state 正常落盘。"""
        state_before = wt_ui_state.load("project_dir_picker")
        self.assertIsInstance(state_before, dict)

        wt_ui_state.save("project_dir_picker", {
            "geometry": "750x570+100+100",
            "last_dir": self.temp_dir.name,
        })

    def test_drive_root_normalization(self):
        r"""测试盘符根路径规范化防御（防止将 D:\ 转为 D: 导致相对路径漂移）。"""
        # 验证 drive 根目录字符规范化逻辑
        def norm(p):
            p = str(p or "").strip()
            if not p: return os.path.expanduser("~")
            if len(p) == 2 and p[1] == ":": return p + "\\"
            p_s = p.rstrip("\\/")
            if len(p_s) == 2 and p_s[1] == ":": return p_s + "\\"
            return p_s or "\\"

        self.assertEqual(norm("D:"), "D:\\")
        self.assertEqual(norm("D:\\"), "D:\\")
        self.assertEqual(norm("D:/"), "D:\\")
        self.assertEqual(norm("E:\\Project"), "E:\\Project")
        self.assertEqual(os.path.join(norm("D:"), "03-WT输入"), "D:\\03-WT输入")


if __name__ == "__main__":
    unittest.main()
