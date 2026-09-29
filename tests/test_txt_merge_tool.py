# encoding: utf-8
"""Tests for txt_merge_tool.py."""

import os
import sys
import tempfile
import unittest
import tkinter as tk

import txt_merge_tool as tmt
from _tk_support import shared_tk_root


class TestTxtMergeLogic(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.f1 = os.path.join(self.temp_dir.name, "a.txt")
        self.f2 = os.path.join(self.temp_dir.name, "b.txt")
        with open(self.f1, "w", encoding="utf-8") as f:
            f.write("Line 1 in a\nLine 2 in a\n\n")
        with open(self.f2, "w", encoding="gbk") as f:
            f.write("Line 1 in b (GBK)\n")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_text_file(self):
        content, enc = tmt.read_text_file(self.f1)
        self.assertIn("Line 1 in a", content)
        self.assertIn("utf-8", enc.lower())

        content_b, enc_b = tmt.read_text_file(self.f2)
        self.assertIn("Line 1 in b (GBK)", content_b)

    def test_merge_direct(self):
        out = os.path.join(self.temp_dir.name, "out_direct.txt")
        count, chars = tmt.merge_txt_files(
            [self.f1, self.f2],
            out,
            newline_between=False,
            add_headers=False,
        )
        self.assertEqual(count, 2)
        self.assertGreater(chars, 0)
        with open(out, "r", encoding="utf-8-sig") as f:
            merged = f.read()
        self.assertIn("Line 1 in a", merged)
        self.assertIn("Line 1 in b (GBK)", merged)

    def test_merge_with_headers_and_separator(self):
        out = os.path.join(self.temp_dir.name, "out_headers.txt")
        count, chars = tmt.merge_txt_files(
            [self.f1, self.f2],
            out,
            newline_between=True,
            add_headers=True,
            separator="--- SEP ---",
            remove_empty_lines=True,
        )
        self.assertEqual(count, 2)
        with open(out, "r", encoding="utf-8-sig") as f:
            merged = f.read()
        self.assertIn("a.txt", merged)
        self.assertIn("b.txt", merged)
        self.assertIn("--- SEP ---", merged)
        self.assertNotIn("\n\n", merged)


class TestTxtMergeAppHeadless(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 共享 Tk 根窗口（见 tests/_tk_support.py）：进程内只建一次、绝不 destroy，
        # 「创建→销毁→再创建」循环会让后续 Tk() 稳定失败（多文件子集运行必踩）
        cls.tk, cls.root = shared_tk_root()

    def setUp(self):
        self.app = tmt.TxtMergeApp(self.root)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_app_list_manipulation(self):
        p1 = os.path.join(self.temp_dir.name, "beta.txt")
        p2 = os.path.join(self.temp_dir.name, "alpha.txt")
        for p in (p1, p2):
            with open(p, "w", encoding="utf-8") as f:
                f.write("content")

        self.app._append_paths([p1, p2])
        self.assertEqual(len(self.app.files), 2)
        self.assertEqual(self.app.files[0], p1)

        # Sort
        self.app.sort_by_name()
        self.assertEqual(self.app.files[0], p2)

        # Move
        self.app.listbox.selection_set(0)
        self.app.move(1)
        self.assertEqual(self.app.files[0], p1)

        # Remove selected
        self.app.listbox.selection_clear(0, tk.END)
        self.app.listbox.selection_set(0)
        self.app.remove_selected()
        self.assertEqual(len(self.app.files), 1)

        # Clear
        self.app.clear_list()
        self.assertEqual(len(self.app.files), 0)

    def test_ui_components(self):
        self.root.update_idletasks()
        self.assertIsNotNone(self.app.badge_status)
        self.assertIsNotNone(self.app.btn_merge)
        self.assertEqual(self.app.btn_merge["text"], "★ 开始合并文件")
        self.assertTrue(self.app.listbox.winfo_exists())
        self.assertTrue(self.app.btn_merge.winfo_exists())


if __name__ == "__main__":
    unittest.main()
