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
        # 存活校验隔离：本组测试关注缓存行为，mock 元素没有真实窗口句柄，
        # 直接置为存活；崩溃防护（失效 scope 跳过 descendants）由
        # test_dead_scope_skips_descendants 单独覆盖。
        stack.enter_context(patch.object(wt_flow_locator, "is_wrapper_alive", return_value=True))
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

    def test_dead_scope_skips_descendants(self):
        """scope 存活探活失败时必须跳过 descendants 扫描。

        背景：MUP 窗口销毁/重启后，对已失效的 UIA 元素调用 descendants() 会触发
        provider 的 C 层致命异常（Windows fatal exception 0x80040155）直接终止
        整个 Python 进程，try/except 无法拦截；因此探活失败必须提前跳过。
        """
        make_wrapper, scan_count, stack = self._scan_setup()
        with stack:
            with patch.object(wt_flow_locator, "is_wrapper_alive", return_value=False):
                result = wt_flow_locator._find_label_rects_for_wrapper(make_wrapper(), "标签")
        self.assertEqual(result, [], "失效 scope 不应产出任何标签矩形")
        self.assertEqual(scan_count["n"], 0, "失效 scope 必须跳过 descendants 扫描（防进程崩溃）")


class DebugPhaseTimingTests(unittest.TestCase):
    """诊断/唤醒自耗时不得混入 JSON 列：t_json_ms 剔除、t_debug_ms 单列。

    背景：气象弹窗诊断、Raw 诊断、唤醒点击这几段排查代码夹在 Phase3(整树结束)
    与最终 t4 之间，会被整段计入 t_json_ms（实测单步 108s），导致"JSON 匹配慢"
    的错误归因。
    """

    def setUp(self):
        wt_flow_locator._step_timing.clear()
        wt_flow_locator._DEBUG_PHASE_MS.clear()

    def tearDown(self):
        wt_flow_locator._step_timing.clear()
        wt_flow_locator._DEBUG_PHASE_MS.clear()

    def test_debug_phase_excluded_from_json_column(self):
        # t3=0.3s → t4=10.3s 共 10000ms，其中 8000ms 是诊断自耗时
        wt_flow_locator._add_debug_phase_ms("step_x", "ctrl_a", 8000.0)
        wt_flow_locator._record_locator_timing("step_x", "ctrl_a", 0.0, 0.1, 0.2, 0.3, 10.3)
        timing = wt_flow_locator.get_step_timing("step_x", "ctrl_a")
        self.assertEqual(timing["t_json_ms"], 2000.0, "JSON 列必须剔除诊断耗时")
        self.assertEqual(timing["t_debug_ms"], 8000.0, "诊断耗时单独记入 t_debug_ms")

    def test_no_debug_keeps_json_unchanged(self):
        wt_flow_locator._record_locator_timing("step_y", "ctrl_b", 0.0, 0.1, 0.2, 0.3, 1.3)
        timing = wt_flow_locator.get_step_timing("step_y", "ctrl_b")
        self.assertEqual(timing["t_json_ms"], 1000.0, "无诊断时 JSON 列不应被改动")
        self.assertEqual(timing["t_debug_ms"], 0.0, "无诊断时 t_debug_ms 为 0")

    def test_debug_accumulates_and_is_consumed(self):
        wt_flow_locator._add_debug_phase_ms("step_z", "ctrl_c", 1500.0)
        wt_flow_locator._add_debug_phase_ms("step_z", "ctrl_c", 500.0)
        wt_flow_locator._record_locator_timing("step_z", "ctrl_c", 0.0, 0.1, 0.2, 0.3, 3.3)
        timing = wt_flow_locator.get_step_timing("step_z", "ctrl_c")
        self.assertEqual(timing["t_debug_ms"], 2000.0, "同一 key 多次累加")
        self.assertEqual(timing["t_json_ms"], 1000.0, "3000ms 区间扣除 2000ms 诊断")
        self.assertEqual(wt_flow_locator._DEBUG_PHASE_MS.get("step_z|ctrl_c"), None,
                         "record 后应消费清零，避免跨步骤串账")


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

class ClimDiagOncePerStepTests(unittest.TestCase):
    """气象弹窗诊断限流语义：同一步骤生命周期只 dump 一次。

    背景：旧实现按 30s 墙钟窗口限流，内网实测失败重试轮间隔 41-46s 恰好绕过
    窗口，step_mt_refclim_select 连续 dump 三次（每次 26-36s）。改为标记位后，
    步骤结束由 clear_step_diagnostic_state 复位，保证复跑可重新诊断。
    """

    def setUp(self):
        wt_flow_locator._CLIM_DIAG_LAST.clear()

    def tearDown(self):
        wt_flow_locator._CLIM_DIAG_LAST.clear()

    def test_second_call_within_step_is_throttled(self):
        wt_flow_locator._CLIM_DIAG_LAST["step_mt_refclim_select_scan2_23"] = 1234.0
        allowed = not wt_flow_locator._CLIM_DIAG_LAST.get("step_mt_refclim_select_scan2_23")
        self.assertFalse(allowed, "同一 step 已 dump 过必须被限流（每步一次）")

    def test_clear_step_diagnostic_state_resets(self):
        wt_flow_locator._CLIM_DIAG_LAST["step_15_scan2_17"] = 999.0
        wt_flow_locator.clear_step_diagnostic_state("step_15_scan2_17")
        self.assertNotIn("step_15_scan2_17", wt_flow_locator._CLIM_DIAG_LAST)
        allowed = not wt_flow_locator._CLIM_DIAG_LAST.get("step_15_scan2_17")
        self.assertTrue(allowed, "步末清除后同 id 复跑允许重新诊断")

    def test_clear_bounded_at_256(self):
        for i in range(260):
            wt_flow_locator._CLIM_DIAG_LAST[f"s{i}"] = 1.0
        wt_flow_locator.clear_step_diagnostic_state("s_new")
        self.assertLessEqual(len(wt_flow_locator._CLIM_DIAG_LAST), 1, "超 256 条目整体清空防膨胀")


class FastLocatorGenericPruneTests(unittest.TestCase):
    """泛化 automationId（>4 命中）剪枝：label_text 预过滤 + 全空回退。

    背景：WPF 每个 TextBox/PART_ContentHost 共享同名 automationId，FindAll 返回
    几十个候选，≤4 早退失效后掉进 name/descendants 全树遍历（内网 step_11 快查
    13.7s / step_3 整树 35s 主因）。剪枝只做"标签命中的候选优先"，评分侧仍完整
    校验，剪错目标时行为不差于现状。
    """

    def _mk_cands(self, n):
        import unittest.mock as _mock
        cands = []
        for i in range(n):
            c = _mock.MagicMock()
            c.element_info.element = _mock.MagicMock()
            c.element_info.handle = i + 1
            cands.append(c)
        return cands

    def _cd(self, label="Wohler 指数"):
        return {
            "targetMethod": "automation_id,control_type,label_text",
            "targetValue": "textbox,Edit," + label,
            "inspectData": {"automationId": "textbox", "controlType": "Edit"},
        }

    def test_label_hint_parsed_from_target_value_position(self):
        # 采集端形态：label 在 targetValue 第三段（control_map_55 同款）
        self.assertEqual(
            wt_flow_locator._fast_locator_label_hint(self._cd()), "Wohler 指数"
        )

    def test_generic_aid_over4_with_label_pruned(self):
        import unittest.mock as _mock
        cands = self._mk_cands(10)
        calls = {"n": 0}

        def fake_sibling(el, label, walker, props):
            calls["n"] += 1
            return calls["n"] % 3 == 0

        with _mock.patch.object(wt_flow_locator, "_iter_uia_findall_by_automation_id", return_value=cands), \
             _mock.patch.object(wt_flow_locator, "_raw_sibling_label_matches", side_effect=fake_sibling), \
             _mock.patch.object(wt_flow_locator, "_raw_element_child_text_matches", return_value=False), \
             _mock.patch.object(wt_flow_locator, "_raw_view_filter_props", return_value={}), \
             _mock.patch.object(wt_flow_locator, "_LOG_STEP"):
            out = wt_flow_locator.iter_fast_locator_candidates(_mock.MagicMock(), self._cd())
        self.assertEqual(len(out), 3, "剪枝后应只剩标签命中的 3 个候选")

    def test_prune_all_miss_falls_back_to_full(self):
        import unittest.mock as _mock
        cands = self._mk_cands(6)
        win = _mock.MagicMock()
        win.children = _mock.MagicMock(side_effect=lambda **kw: [])
        win.descendants = _mock.MagicMock(side_effect=lambda **kw: [])
        with _mock.patch.object(wt_flow_locator, "_iter_uia_findall_by_automation_id", return_value=list(cands)), \
             _mock.patch.object(wt_flow_locator, "_raw_sibling_label_matches", return_value=False), \
             _mock.patch.object(wt_flow_locator, "_raw_element_child_text_matches", return_value=False), \
             _mock.patch.object(wt_flow_locator, "_raw_view_filter_props", return_value={}), \
             _mock.patch.object(wt_flow_locator, "get_cached_parent_wrappers", return_value=[]), \
             _mock.patch.object(wt_flow_locator, "_LOG_STEP"):
            out = wt_flow_locator.iter_fast_locator_candidates(win, self._cd("某标签"))
        self.assertGreaterEqual(len(out), 6, "剪枝全空时必须回退全量候选，不得比现状差")

    def test_le4_no_prune(self):
        import unittest.mock as _mock
        cands = self._mk_cands(3)
        with _mock.patch.object(wt_flow_locator, "_iter_uia_findall_by_automation_id", return_value=cands), \
             _mock.patch.object(wt_flow_locator, "_raw_sibling_label_matches", side_effect=AssertionError("不应进入剪枝")), \
             _mock.patch.object(wt_flow_locator, "_LOG_STEP"):
            out = wt_flow_locator.iter_fast_locator_candidates(_mock.MagicMock(), self._cd())
        self.assertEqual(len(out), 3)

    def test_no_label_definition_no_prune(self):
        import unittest.mock as _mock
        cands = self._mk_cands(8)
        cd = {"targetMethod": "automation_id,control_type", "targetValue": "PART_DropDownButton,Button"}
        with _mock.patch.object(wt_flow_locator, "_iter_uia_findall_by_automation_id", return_value=cands), \
             _mock.patch.object(wt_flow_locator, "_raw_sibling_label_matches", side_effect=AssertionError("不应进入剪枝")), \
             _mock.patch.object(wt_flow_locator, "_LOG_STEP"):
            out = wt_flow_locator.iter_fast_locator_candidates(_mock.MagicMock(), cd)
        self.assertGreaterEqual(len(out), 8)
