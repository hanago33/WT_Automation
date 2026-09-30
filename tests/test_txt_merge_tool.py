# encoding: utf-8
"""Tests for txt_merge_tool.py."""

import os
import sys
import tempfile
import threading
import time
import unittest
import tkinter as tk
from unittest.mock import patch

import txt_merge_tool as tmt
import wt_txt_merge_core
from _tk_support import shared_tk_root


def _make_temp_files(temp_dir, n=3):
    """生成 n 个非空输入文件，返回路径列表。"""
    paths = []
    for i in range(n):
        p = os.path.join(temp_dir, f"f{i}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"内容 {i}\n" * 30)
        paths.append(p)
    return paths


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

        # Clear（合并后 clear_list 带确认对话框，测试中 mock 掉避免模态弹窗）
        with patch("txt_merge_tool.messagebox.askyesno", return_value=True):
            self.app.clear_list()
        self.assertEqual(len(self.app.files), 0)

    def test_append_paths_keeps_brace_ending_names(self):
        """拖拽路径清洗只剥成对花括号：名称首尾本身含 {} 的合法目录不得被截断。

        旧实现 str.strip("{}'\"") 会把 `Data{2024}` 这类目录截成 `…Data{2024`
        导致整个目录被静默丢弃（session-24 修正）。
        """
        brace_dir = os.path.join(self.temp_dir.name, "Data{2024}")
        os.makedirs(brace_dir, exist_ok=True)
        inner = os.path.join(brace_dir, "report.txt")
        with open(inner, "w", encoding="utf-8") as f:
            f.write("content")

        # 模拟 splitlist 失败兜底：整串被花括号包裹
        self.app._append_paths(["{" + brace_dir + "}"])
        self.assertEqual(self.app.files, [inner])

        # 名称正常的文件不受影响（成对剥壳后原样保留）
        plain = os.path.join(self.temp_dir.name, "notes.txt")
        with open(plain, "w", encoding="utf-8") as f:
            f.write("content")
        self.app._append_paths(["{" + plain + "}"])
        self.assertIn(plain, self.app.files)

    def test_ui_components(self):
        self.root.update_idletasks()
        self.assertIsNotNone(self.app.badge_status)
        self.assertIsNotNone(self.app.btn_merge)
        self.assertEqual(self.app.btn_merge["text"], "★ 开始合并文件")
        self.assertTrue(self.app.listbox.winfo_exists())
        self.assertTrue(self.app.btn_merge.winfo_exists())

    def _pump_until(self, predicate, timeout=5.0):
        """泵事件循环直到 predicate 成立；再补几轮让 after(0) 派发的收尾执行完。"""
        deadline = time.time() + timeout
        while not predicate() and time.time() < deadline:
            self.root.update()
            time.sleep(0.01)
        for _ in range(30):
            self.root.update()
            time.sleep(0.01)

    def test_merge_runs_off_ui_thread_and_restores_button(self):
        """#2 双向断言：合并必须在 worker 线程执行（基线同步实现时此测试失败）。"""
        out_path = os.path.join(self.temp_dir.name, "merged_out.txt")
        self.app._append_paths(_make_temp_files(self.temp_dir.name))
        seen = {}
        worker_done = threading.Event()
        real_merge = wt_txt_merge_core.merge_txt_files

        def fake_core(paths, out, **kwargs):
            seen["thread"] = threading.current_thread()
            result = real_merge(paths, out, **kwargs)
            worker_done.set()
            return result

        with patch("txt_merge_tool.filedialog.asksaveasfilename", return_value=out_path), \
             patch("txt_merge_tool.messagebox.showinfo"), \
             patch("txt_merge_tool.merge_txt_files", side_effect=fake_core):
            self.app.merge()
            self._pump_until(worker_done.is_set)

        self.assertTrue(worker_done.is_set())
        self.assertIsNot(seen["thread"], threading.main_thread())
        self.assertTrue(os.path.isfile(out_path))
        self.assertEqual(self.app.btn_merge.cget("text"), "★ 开始合并文件")
        self.assertIn("合并完成", self.app.status.cget("text"))

    def test_second_click_requests_cancel_and_reports(self):
        """#2：运行中再次点击 = 置位取消事件；收尾走「已取消」分支并恢复按钮。"""
        out_path = os.path.join(self.temp_dir.name, "merged_cancel.txt")
        started = threading.Event()
        release = threading.Event()

        def slow_core(paths, out, cancel_event=None, **kwargs):
            started.set()
            if cancel_event is not None:
                cancel_event.wait(5)  # 模拟大文件合并卡住，等取消请求
            raise wt_txt_merge_core.MergeCancelled(done=0)

        self.app._append_paths(_make_temp_files(self.temp_dir.name, n=2))
        with patch("txt_merge_tool.filedialog.asksaveasfilename", return_value=out_path), \
             patch("txt_merge_tool.messagebox.showinfo"), \
             patch("txt_merge_tool.messagebox.showerror"), \
             patch("txt_merge_tool.merge_txt_files", side_effect=slow_core):
            self.app.merge()
            self._pump_until(started.is_set)
            self.assertEqual(self.app.btn_merge.cget("text"), "⨂ 取消合并")

            self.app.merge()  # 第二次点击 = 请求取消
            self.assertTrue(self.app._merge_cancel.is_set())
            release.set()
            self._pump_until(lambda: not self.app._merge_thread.is_alive())

        self.assertEqual(self.app.btn_merge.cget("text"), "★ 开始合并文件")
        self.assertIn("已取消", self.app.status.cget("text"))


if __name__ == "__main__":
    unittest.main()
