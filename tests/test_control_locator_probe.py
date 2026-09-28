# encoding: utf-8
"""control_locator_probe 探针子进程的核心搜索逻辑测试。

覆盖：found / not_found / 无目标窗口 / descendants 预算限制 / 异常转 error。
真实 UIA 遍历与原生崩溃隔离无法在单测复现，需在 MUP 上实测（见自测纪律）。
"""
import os
import sys
import time as time_mod
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import control_locator_probe as probe


class _FakeRect:
    def __init__(self, l, t, r, b):
        self.left, self.top, self.right, self.bottom = l, t, r, b


class _FakeWindow:
    def __init__(self, descendants=None):
        self._desc = list(descendants or [])

    def descendants(self, control_type=None):
        return list(self._desc)


class _FakeWidget:
    def __init__(self, name="私有", rect=(10, 20, 100, 60)):
        self.name = name
        self.rect_obj = _FakeRect(*rect)

    def rectangle(self):
        return self.rect_obj


def _base_control():
    return {
        "name": "私有",
        "targetMethod": "class_name,control_type",
        "targetValue": "TextBlock,Text",
        "controlType": "Text",
        "windowTitle": "Meteodyn Universe",
        "inspectData": {},
    }


class ProbeSearchTests(unittest.TestCase):
    def _patch_locator(self, windows, fast_by_window=None, score=90, snapshot=None):
        fast_by_window = fast_by_window or {}
        stack = ExitStack()
        stack.enter_context(patch.object(probe.flow_locator, "iter_flow_search_windows", return_value=windows))
        stack.enter_context(patch.object(probe.flow_locator, "get_wrapper_handle", side_effect=lambda w: id(w)))
        stack.enter_context(patch.object(probe.flow_locator, "score_control_match", return_value=score))

        def _fast(window, control):
            return fast_by_window.get(id(window), [])

        stack.enter_context(patch.object(probe.flow_locator, "iter_fast_locator_candidates", side_effect=_fast))
        stack.enter_context(patch.object(
            probe.flow_locator, "get_wrapper_debug_snapshot",
            return_value=snapshot if snapshot is not None else {"name": "私有", "controlType": "Text", "automationId": ""},
        ))
        return stack

    def test_found(self):
        widget = _FakeWidget()
        win = _FakeWindow()
        with self._patch_locator([win], fast_by_window={id(win): [widget]}) as _stack:
            result = probe.search_control(_base_control(), budgets={"max_windows": 2})
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["score"], 90)
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["center"], {"x": 55, "y": 40})
        self.assertEqual(result["rect"]["left"], 10)
        self.assertEqual(result["snapshot"]["name"], "私有")

    def test_found_dedup_handle(self):
        # 同一 handle 的重复候选只计一次匹配
        widget = _FakeWidget()
        win = _FakeWindow()
        dup = widget
        with self._patch_locator([win], fast_by_window={id(win): [widget, dup]}) as _stack:
            result = probe.search_control(_base_control(), budgets={"max_windows": 2})
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["match_count"], 1)

    def test_not_found(self):
        win = _FakeWindow()
        with self._patch_locator([win], fast_by_window={id(win): []}) as _stack:
            result = probe.search_control(_base_control(), budgets={"max_windows": 2})
        self.assertEqual(result["status"], "not_found")
        self.assertIn("未找到匹配控件", result["error"])
        self.assertIsNone(result["rect"])

    def test_no_window(self):
        with self._patch_locator([]) as _stack:
            result = probe.search_control(_base_control(), budgets={"max_windows": 2})
        self.assertEqual(result["status"], "not_found")
        self.assertIn("未找到目标窗口", result["error"])

    def test_descendants_stage_recovers(self):
        # 阶段1/2 未命中时，阶段3 的受限 descendants 找到候选
        widget = _FakeWidget()
        win = _FakeWindow(descendants=[widget])
        with self._patch_locator([win], fast_by_window={id(win): []}) as _stack:
            result = probe.search_control(_base_control(), budgets={"max_windows": 2})
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["match_count"], 1)

    def test_max_windows_cap(self):
        # 超过 max_windows 的窗口不应被扫描（iter_fast 只对前 cap 个窗口被调用）
        wins = [_FakeWindow() for _ in range(5)]
        fast_calls = []

        def _fast(window, control):
            fast_calls.append(id(window))
            return []

        stack = ExitStack()
        stack.enter_context(patch.object(probe.flow_locator, "iter_flow_search_windows", return_value=wins))
        stack.enter_context(patch.object(probe.flow_locator, "get_wrapper_handle", side_effect=lambda w: id(w)))
        stack.enter_context(patch.object(probe.flow_locator, "score_control_match", return_value=90))
        stack.enter_context(patch.object(probe.flow_locator, "iter_fast_locator_candidates", side_effect=_fast))
        stack.enter_context(patch.object(probe.flow_locator, "get_wrapper_debug_snapshot", return_value={}))
        with stack:
            probe.search_control(_base_control(), budgets={"max_windows": 2, "descendants_budget_seconds": 0.5})
        self.assertEqual(len(fast_calls), 2, "只应扫描前 max_windows 个窗口")

    def test_exception_becomes_error(self):
        stack = ExitStack()
        stack.enter_context(patch.object(probe.flow_locator, "iter_flow_search_windows", side_effect=RuntimeError("boom")))
        with stack:
            result = probe.search_control(_base_control(), budgets={"max_windows": 2})
        self.assertEqual(result["status"], "error")
        self.assertIn("boom", result["error"])

    def test_early_exit_stops_at_high_score(self):
        # >=100 分高置信命中立即返回：后续候选不再评分（与执行器早退语义一致）
        w1, w2 = _FakeWidget(), _FakeWidget()
        win = _FakeWindow()
        scored = []

        def spy_score(wrapper, control_definition):
            scored.append(wrapper)
            return 150

        stack = ExitStack()
        stack.enter_context(patch.object(probe.flow_locator, "iter_flow_search_windows", return_value=[win]))
        stack.enter_context(patch.object(probe.flow_locator, "get_wrapper_handle", side_effect=lambda w: id(w)))
        stack.enter_context(patch.object(probe.flow_locator, "score_control_match", side_effect=spy_score))
        stack.enter_context(patch.object(
            probe.flow_locator, "iter_fast_locator_candidates",
            side_effect=lambda window, control_definition: [w1, w2],
        ))
        stack.enter_context(patch.object(probe.flow_locator, "get_wrapper_debug_snapshot", return_value={}))
        with stack:
            result = probe.search_control(_base_control(), budgets={"max_windows": 2})
        self.assertEqual(result["status"], "found")
        self.assertEqual(len(scored), 1, "高分命中后不应继续评分后续候选")
        self.assertEqual(result["match_count"], 1)
        self.assertTrue(result["early_exit"])

    def test_no_early_exit_below_threshold(self):
        # 全部低于高置信阈值时仍收集所有匹配（保持全局最优报告）
        win = _FakeWindow()
        with self._patch_locator([win], fast_by_window={id(win): [_FakeWidget(), _FakeWidget()]}) as _stack:
            result = probe.search_control(_base_control(), budgets={"max_windows": 2})
        self.assertEqual(result["status"], "found")
        self.assertEqual(result["match_count"], 2)
        self.assertFalse(result.get("early_exit"))


class BoundedDescendantsTests(unittest.TestCase):
    def test_caps_by_element_count(self):
        win = _FakeWindow(descendants=[_FakeWidget() for _ in range(10)])
        out = probe._bounded_descendants(win, "Text", time_mod.time() + 30, 3)
        self.assertEqual(len(out), 3)

    def test_caps_by_deadline(self):
        win = _FakeWindow(descendants=[_FakeWidget() for _ in range(100)])
        out = probe._bounded_descendants(win, "Text", time_mod.time() - 1, 100000)
        self.assertEqual(out, [], "已过 deadline 时不应遍历")

    def test_respects_expected_type(self):
        win = _FakeWindow(descendants=[_FakeWidget() for _ in range(5)])
        seen = {"type": None}
        def _desc(control_type=None):
            seen["type"] = control_type
            return list(win._desc)
        win.descendants = _desc
        probe._bounded_descendants(win, "ListBoxItem", time_mod.time() + 30, 10)
        self.assertEqual(seen["type"], "ListBoxItem")

    def test_descendants_error_returns_empty(self):
        win = _FakeWindow()
        def _boom(control_type=None):
            raise RuntimeError("native crash")
        win.descendants = _boom
        out = probe._bounded_descendants(win, "Text", time_mod.time() + 30, 10)
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()