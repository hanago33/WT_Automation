# encoding: utf-8
"""模板制作器画布拖动增量预览层测试（待修改清单 #4）。

双向断言：拖动 N 帧必须零次全量 refresh_canvas（基线每帧全量重建 → 失败）、
预览层逐帧重画、松手后恰好一次全量收敛；moving 分支拖动期不再重建列表与预览图。
"""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

import build_image_template_library as B

from _tk_support import shared_tk_root


class TemplateCanvasDragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tk, cls.root = shared_tk_root()

    def setUp(self):
        self.app = B.TemplateBuilderApp(self.root)
        self.root.update()  # 让 canvas 获得真实尺寸（refresh_canvas 依赖 winfo）
        self.app.source_image_rgb = np.zeros((120, 160, 3), dtype=np.uint8)
        self.refresh_calls = 0
        self.preview_calls = 0
        self.update_preview_calls = 0

        original_refresh = self.app.refresh_canvas
        original_preview = self.app._draw_canvas_preview_layer

        def counting_refresh():
            self.refresh_calls += 1
            original_refresh()

        def counting_preview():
            self.preview_calls += 1
            original_preview()

        # 预览图重建在无头环境下不必真实执行，只计数
        self.app.refresh_canvas = counting_refresh
        self.app._draw_canvas_preview_layer = counting_preview
        self.app.update_preview = lambda: setattr(
            self, "update_preview_calls", self.update_preview_calls + 1)

        self.app.refresh_canvas()  # 建立静态层并确定 self.scale

    def _ev(self, image_x, image_y, state=0):
        """按当前缩放把图像坐标换算成画布事件坐标。"""
        scale = self.app.scale
        return SimpleNamespace(x=int(image_x * scale), y=int(image_y * scale), state=state)

    def test_drawing_drag_uses_preview_layer_not_full_rebuild(self):
        self.app.draw_mode_var.set(True)
        self.app.on_canvas_press(self._ev(5, 5))
        base = self.refresh_calls  # press 允许一次全量

        for i in range(10):
            self.app.on_canvas_drag(self._ev(7 + i, 8))
        self.assertEqual(self.refresh_calls, base)   # 拖动帧零全量重建（基线每帧 +1）
        self.assertEqual(self.preview_calls, 10)     # 预览层逐帧重画
        self.assertTrue(self.app.canvas.find_withtag("preview"))
        self.assertEqual(self.app.temp_region.x, 5)
        self.assertEqual(self.app.temp_region.w, 11)  # 末帧图像坐标 (16,8)：16-5=11
        self.assertEqual(self.app.temp_region.h, 3)

    def test_drawing_release_converges_once(self):
        self.app.draw_mode_var.set(True)
        self.app.on_canvas_press(self._ev(5, 5))
        base = self.refresh_calls
        for i in range(10):
            self.app.on_canvas_drag(self._ev(7 + i, 8))
        with patch.object(self.app, "add_candidate_region", return_value=0):
            self.app.on_canvas_release(self._ev(40, 40))  # 35×35 图像像素 ≥ MIN_REGION_SIZE
        self.assertEqual(self.refresh_calls, base + 1)   # 松手恰好一次全量收敛
        self.assertFalse(self.app.canvas.find_withtag("preview"))
        self.assertIsNone(self.app.temp_region)

    def test_moving_drag_updates_candidate_without_per_frame_rebuilds(self):
        self.app.candidates = [B.CandidateRegion(x=10, y=10, w=30, h=20)]
        self.app.template_names = ["template_001"]
        self.app.selected_index = 0
        self.app.refresh_canvas()
        base = self.refresh_calls

        self.app.drag_action = "moving"
        self.app.drag_start = (10, 10)
        self.app.drag_start_region = B.CandidateRegion(x=10, y=10, w=30, h=20)
        self.app.drag_current_index = 0

        for i in range(6):
            self.app.on_canvas_drag(self._ev(11 + i, 10))
        self.assertEqual(self.refresh_calls, base)          # 拖动帧零全量重建
        self.assertEqual(self.preview_calls, 6)             # 预览层逐帧
        self.assertEqual(self.update_preview_calls, 0)      # 拖动帧不再重建预览图（基线每帧 +1）
        self.assertEqual(self.app.candidates[0].x, 16)      # 移动结果实时写回数据

        self.app.on_canvas_release(self._ev(16, 10))
        self.assertEqual(self.refresh_calls, base + 1)      # 松手恰好一次
        self.assertEqual(self.update_preview_calls, 1)      # 预览图松手后重建一次
        self.assertFalse(self.app.canvas.find_withtag("preview"))
        self.assertIsNone(self.app.drag_action)

    def test_moving_drag_hides_static_layer_and_restores_on_release(self):
        """审计复核 P2：被拖框的静态层（框 + 序号 + 手柄同 tag）拖动期必须隐藏。

        此前只在预览层叠加新框，旧框与 8 个手柄仍留在旧位置 → 界面上出现两个框。
        """
        self.app.candidates = [B.CandidateRegion(x=10, y=10, w=30, h=20)]
        self.app.template_names = ["template_001"]
        self.app.selected_index = 0
        self.app.refresh_canvas()
        static_items = self.app.canvas.find_withtag("cand_0")
        self.assertGreater(len(static_items), 2)  # 框 + 序号 + 手柄都在同一 tag 下

        self.app.drag_action = "moving"
        self.app.drag_start = (10, 10)
        self.app.drag_start_region = B.CandidateRegion(x=10, y=10, w=30, h=20)
        self.app.drag_current_index = 0
        for i in range(3):
            self.app.on_canvas_drag(self._ev(11 + i, 10))
        self.assertEqual(
            {self.app.canvas.itemcget(item, "state") for item in static_items}, {"hidden"})
        self.assertTrue(self.app.canvas.find_withtag("preview"))

        self.app.on_canvas_release(self._ev(13, 10))
        restored = self.app.canvas.find_withtag("cand_0")
        self.assertEqual(len(restored), len(static_items))
        self.assertEqual(
            {self.app.canvas.itemcget(item, "state") for item in restored}, {""})
        self.assertFalse(self.app.canvas.find_withtag("preview"))


if __name__ == "__main__":
    unittest.main()
