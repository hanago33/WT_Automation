# encoding: utf-8
"""模板制作器 OCR 命名线程化测试（待修改清单 #3）。

覆盖：
- run_tesseract_cli 的 timeout 语义（超时返回 None，与「识别为空 ""」区分）
- ocr_name_from_raw 纯函数（文本/空/超时三态）
- 批量命名的 worker 执行、逐框回写、取消与超时统计（UI 层，共享 Tk 根）
"""
import subprocess
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import build_image_template_library as B

from _tk_support import shared_tk_root


class OcrNameFromRawTests(unittest.TestCase):
    def test_text_returns_sanitized_name(self):
        name, timed_out = B.ocr_name_from_raw("保存", 4)
        self.assertFalse(timed_out)
        self.assertEqual(name, B.sanitize_template_name("保存", "template_005"))

    def test_empty_text_returns_fallback(self):
        name, timed_out = B.ocr_name_from_raw("", 4)
        self.assertFalse(timed_out)
        self.assertEqual(name, "template_005")

    def test_none_means_timeout(self):
        name, timed_out = B.ocr_name_from_raw(None, 4)
        self.assertTrue(timed_out)
        self.assertEqual(name, "template_005")


class RunTesseractCliTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.image = B.Image.new("L", (20, 10))

    def test_returns_empty_when_no_tesseract(self):
        with patch.object(B, "TESSERACT_EXE", ""):
            self.assertEqual(B.run_tesseract_cli(self.image), "")

    def test_timeout_returns_none(self):
        with patch.object(B, "TESSERACT_EXE", "tesseract.exe"), \
             patch.object(B.subprocess, "run",
                          side_effect=subprocess.TimeoutExpired(cmd="tesseract.exe", timeout=1)):
            self.assertIsNone(B.run_tesseract_cli(self.image, timeout=1.0))

    def test_success_returns_stripped_text(self):
        fake = SimpleNamespace(returncode=0, stdout="  识别文本  ")
        with patch.object(B, "TESSERACT_EXE", "tesseract.exe"), \
             patch.object(B.subprocess, "run", return_value=fake) as run_mock:
            self.assertEqual(B.run_tesseract_cli(self.image, timeout=5.0), "识别文本")
        self.assertEqual(run_mock.call_args.kwargs.get("timeout"), 5.0)

    def test_nonzero_exit_returns_empty(self):
        fake = SimpleNamespace(returncode=1, stdout="")
        with patch.object(B, "TESSERACT_EXE", "tesseract.exe"), \
             patch.object(B.subprocess, "run", return_value=fake):
            self.assertEqual(B.run_tesseract_cli(self.image), "")


class OcrBatchNamingTests(unittest.TestCase):
    """批量 OCR 命名：worker 执行、逐框回写、取消路径（双向断言见注释）。"""

    @classmethod
    def setUpClass(cls):
        cls.tk, cls.root = shared_tk_root()

    def setUp(self):
        self.app = B.TemplateBuilderApp(self.root)
        self.root.update()
        self.app.source_image_rgb = np.zeros((60, 80, 3), dtype=np.uint8)
        self.app.candidates = [
            B.CandidateRegion(x=5, y=5, w=20, h=10),
            B.CandidateRegion(x=30, y=5, w=20, h=10),
        ]
        self.app.template_names = ["template_001", "template_002"]
        self.app.refresh_listbox()
        # 走应用自身的选区 API（同步列表框选中态），否则 sync_selection_from_listbox
        # 会按 selected_index 回退成单选
        self.app.set_selected_indices({0, 1}, active_index=0)

    def _pump_until(self, predicate, timeout=5.0):
        deadline = time.time() + timeout
        while not predicate() and time.time() < deadline:
            self.root.update()
            time.sleep(0.01)
        for _ in range(30):
            self.root.update()
            time.sleep(0.01)

    def test_batch_runs_off_ui_thread_and_writes_back_per_box(self):
        """双向断言：worker 线程执行 + 逐框回写（基线 UI 线程串行实现时失败）。"""
        seen_threads = set()
        started = threading.Event()
        release = threading.Event()

        def fake_ocr(crop_image, timeout=None):
            seen_threads.add(threading.current_thread())
            started.set()
            release.wait(5)  # 第一框稍作停留，验证进度事件与线程
            return "区块A"

        with patch.object(B, "extract_text_with_ocr", side_effect=fake_ocr), \
             patch.object(self.app, "ensure_ocr_available", return_value=True), \
             patch("build_image_template_library.messagebox.showinfo"), \
             patch("build_image_template_library.messagebox.showerror"):
            self.app.ocr_name_selected()
            self._pump_until(started.is_set)
            self.assertEqual(self.app.btn_ocr_name_selected.cget("text"), "⨂ 取消命名")
            release.set()
            self._pump_until(lambda: getattr(self.app, "_ocr_thread", None) is None
                             or not self.app._ocr_thread.is_alive())

        self.assertTrue(seen_threads)
        self.assertNotIn(threading.main_thread(), seen_threads)
        expected = B.sanitize_template_name("区块A", "template_001")
        self.assertEqual(self.app.template_names[0], expected)
        self.assertEqual(self.app.template_names[1], expected)
        self.assertIn("OCR 批量命名完成", self.app.status_var.get())
        self.assertIn("共处理 2", self.app.status_var.get())
        self.assertEqual(self.app.btn_ocr_name_selected.cget("text"), "OCR命名选中")

    def test_second_click_cancels_and_reports_partial(self):
        started = threading.Event()
        release = threading.Event()

        def slow_ocr(crop_image, timeout=None):
            started.set()
            release.wait(5)
            return ""

        with patch.object(B, "extract_text_with_ocr", side_effect=slow_ocr), \
             patch.object(self.app, "ensure_ocr_available", return_value=True), \
             patch("build_image_template_library.messagebox.showinfo"), \
             patch("build_image_template_library.messagebox.showerror"):
            self.app.ocr_name_selected()
            self._pump_until(started.is_set)

            self.app.ocr_name_selected()  # 第二次点击 = 请求取消
            self.assertTrue(self.app._ocr_cancel.is_set())
            release.set()
            self._pump_until(lambda: not self.app._ocr_thread.is_alive())

        self.assertEqual(self.app.btn_ocr_name_selected.cget("text"), "OCR命名选中")
        self.assertIn("已取消", self.app.status_var.get())
        self.assertIn("完成 1/2", self.app.status_var.get())

    def test_timeout_boxes_counted_and_fallback_named(self):
        calls = {"n": 0}

        def timeout_first(crop_image, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # 第一框超时
            return "文本B"

        with patch.object(B, "extract_text_with_ocr", side_effect=timeout_first), \
             patch.object(self.app, "ensure_ocr_available", return_value=True), \
             patch("build_image_template_library.messagebox.showinfo"), \
             patch("build_image_template_library.messagebox.showerror"):
            self.app.ocr_name_selected()
            self._pump_until(lambda: "完成" in self.app.status_var.get())

        self.assertEqual(self.app.template_names[0], "template_001")  # 超时回退默认名
        self.assertEqual(self.app.template_names[1],
                         B.sanitize_template_name("文本B", "template_002"))
        self.assertIn("超时 1", self.app.status_var.get())


if __name__ == "__main__":
    unittest.main()
