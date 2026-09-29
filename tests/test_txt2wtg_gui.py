# encoding: utf-8
"""Tests for txt2wtg/txt2wtg_gui.py."""

import os
import sys
import tempfile
import unittest
import tkinter as tk

# Ensure txt2wtg is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TXT2WTG_DIR = os.path.join(BASE_DIR, "txt2wtg")
if TXT2WTG_DIR not in sys.path:
    sys.path.insert(0, TXT2WTG_DIR)

import txt2wtg_gui as twg
import txt2wtg_core as core


class TestTxt2WtgGuiHeadless(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # App 是 tk.Tk 子类，无法挂 tests/_tk_support.py 的共享根；改为类级别
        # 只建一次、绝不 destroy —— 同样消灭「创建→销毁→再创建」循环，
        # 否则多文件子集运行时后续 Tk() 会稳定失败
        cls.app = twg.App()
        cls.app.withdraw()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_app_initialization(self):
        self.assertIsNotNone(self.app.input_path)
        self.assertIsNotNone(self.app.out_path)
        self.assertIn("diameter", self.app.vars)
        self.assertIn("rated", self.app.vars)
        self.assertIsNotNone(self.app.btn_run)

    def test_sample_file_preview_and_conversion(self):
        sample_file = os.path.join(TXT2WTG_DIR, "sample", "WT6250D220.txt")
        self.assertTrue(os.path.isfile(sample_file), f"Sample file not found: {sample_file}")

        self.app.input_path.set(sample_file)
        self.app._refresh_out_default()
        out_file = os.path.join(self.temp_dir.name, "test_output.wtg")
        self.app.out_path.set(out_file)

        # Trigger drawing without error
        self.app._draw_preview()

        # Run conversion (mock messagebox to prevent modal block in headless tests)
        from unittest.mock import patch
        with patch("tkinter.messagebox.showinfo"):
            self.app._run()

        self.assertTrue(os.path.isfile(out_file), "WTG file was not generated")
        self.assertGreater(os.path.getsize(out_file), 100)


if __name__ == "__main__":
    unittest.main()
