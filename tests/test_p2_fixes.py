# encoding: utf-8
"""P2 修复针对性测试：性能与清理。

覆盖：
- P2-1  label 矩形预索引：同窗口下多候选不再重复全子树扫描（O(N·T) 消除）
- P2-3  roughness_pairs 按配置段配对：某段缺键不再导致后续整体错位
"""
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator
import mup_prod_config


class LabelRectCacheTests(unittest.TestCase):
    """P2-1：同窗口同标签的矩形查找只扫描一次，后续候选查缓存。"""

    def setUp(self):
        wt_flow_locator._label_rect_cache_reset()

    def tearDown(self):
        wt_flow_locator._label_rect_cache_reset()

    def _scan_setup(self):
        top_window = MagicMock()
        scan_count = {"n": 0}

        def fake_descendants():
            scan_count["n"] += 1
            return [MagicMock()]  # 一个标签候选

        top_window.descendants.side_effect = fake_descendants

        def make_wrapper():
            wrapper = MagicMock()
            wrapper.parent.return_value = None  # 只走 top_window scope
            return wrapper

        # 补丁必须覆盖整个断言过程：把 ExitStack 一并返回，由测试体 with stack 启用
        stack = ExitStack()
        stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_top_level_window", return_value=top_window))
        stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_handle", return_value=1001))
        stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value="Text"))
        stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value="标签"))
        stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_rectangle", return_value={
            "left": 10, "top": 10, "right": 80, "bottom": 30,
        }))
        return make_wrapper, scan_count, stack

    def test_cached_across_wrappers_in_same_window(self):
        make_wrapper, scan_count, stack = self._scan_setup()
        with stack:
            self.assertEqual(len(wt_flow_locator._find_label_rects_for_wrapper(make_wrapper(), "标签")), 1)
            self.assertEqual(scan_count["n"], 1, "首次调用应执行子树扫描")

            # 同一窗口下第二个候选：应命中缓存，不再扫描
            self.assertEqual(len(wt_flow_locator._find_label_rects_for_wrapper(make_wrapper(), "标签")), 1)
            self.assertEqual(scan_count["n"], 1, "同窗口同标签第二次调用必须命中缓存，避免 O(N·T)")

    def test_different_label_text_not_cached(self):
        make_wrapper, scan_count, stack = self._scan_setup()
        with stack:
            wt_flow_locator._find_label_rects_for_wrapper(make_wrapper(), "标签")
            wt_flow_locator._find_label_rects_for_wrapper(make_wrapper(), "标签2")
        self.assertEqual(scan_count["n"], 2, "不同标签文本不共享缓存")


class RoughnessPairsSectionTests(unittest.TestCase):
    """P2-3：roughness_pairs 按配置段分组配对，避免序号错位。"""

    def test_section_pairing_skips_incomplete_section(self):
        # Config_1 只有 source、缺 CorrespondanceFileName：
        # 旧的按序号对齐会得到 WC10_2021 ↔ ESA2022.txt 的错位；新逻辑应跳过该段。
        lines = [
            ("Config_0_RoughnessSourceName", "rough:WC10_2020"),
            ("Config_0_CorrespondanceFileName", "ESA2020.txt"),
            ("Config_1_RoughnessSourceName", "rough:WC10_2021"),
            ("Config_2_RoughnessSourceName", "rough:WC10_2022"),
            ("Config_2_CorrespondanceFileName", "ESA2022.txt"),
        ]
        with patch.object(mup_prod_config, "_merged_lines", return_value=lines):
            pairs = mup_prod_config.roughness_pairs()
        self.assertEqual(pairs, [
            ("rough:WC10_2020", "ESA2020.txt"),
            ("rough:WC10_2022", "ESA2022.txt"),
        ])

    def test_reverse_order_keys_still_pair(self):
        # 文件键先于源键出现：段分组同样正确配对
        lines = [
            ("Config_0_CorrespondanceFileName", "ESA2020.txt"),
            ("Config_0_RoughnessSourceName", "rough:WC10_2020"),
        ]
        with patch.object(mup_prod_config, "_merged_lines", return_value=lines):
            pairs = mup_prod_config.roughness_pairs()
        self.assertEqual(pairs, [("rough:WC10_2020", "ESA2020.txt")])


if __name__ == "__main__":
    unittest.main()