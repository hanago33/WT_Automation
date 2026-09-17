# encoding: utf-8
"""日志降噪改造（P1）测试。

覆盖三类改造：
- 定位耗时按档位分级（改造前统一 0.8s 单阈值 → 0.83s 也报警，属误报）
- 窗口严格过滤无命中的回退来源收敛为一行（改造前同路径连发两行）
- 高频 DEBUG 诊断转储受统一开关门控（改造前无条件输出）

另含降噪回归守卫：断言已移除的噪音来源不会复活。
"""
import builtins
import os
import re
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_logging
import wt_flow_locator


def _env_without_log_switch(**extra):
    """构造不含日志开关的环境（避免宿主环境变量影响断言）。"""
    clean = {
        k: v
        for k, v in os.environ.items()
        if k not in ("WT_LOG_LEVEL", "WT_DEBUG_EVENTS")
    }
    clean.update(extra)
    return patch.dict(os.environ, clean, clear=True)


class LogCaptureMixin(object):
    """把 locator 的日志出口换成列表捕获，并复位级别开关。"""

    def setUp(self):
        wt_logging.reset_min_level()
        self.addCleanup(wt_logging.reset_min_level)
        self.captured = []
        for attr in ("_LOG_STEP", "_LOG_AT"):
            p = patch.object(wt_flow_locator, attr, None)
            p.start()
            self.addCleanup(p.stop)
        # _LOG_AT 置 None → _log_at() 走「阈值门控 + _LOG_STEP」分支
        p = patch.object(wt_flow_locator, "_LOG_STEP", self.captured.append)
        p.start()
        self.addCleanup(p.stop)


class LocateElapsedTierTests(LogCaptureMixin, unittest.TestCase):
    """定位耗时分级：>=8s WARN / >=3s INFO / >=0.8s DEBUG / 否则不输出。"""

    def test_threshold_constants_are_ordered(self):
        self.assertLess(
            wt_flow_locator.SLOW_LOCATE_DEBUG_SECONDS,
            wt_flow_locator.SLOW_LOCATE_INFO_SECONDS,
        )
        self.assertLess(
            wt_flow_locator.SLOW_LOCATE_INFO_SECONDS,
            wt_flow_locator.SLOW_LOCATE_WARN_SECONDS,
        )

    def test_level_for_elapsed(self):
        cases = [
            (0.1, None),
            (0.79, None),
            (0.8, wt_logging.DEBUG),
            (2.9, wt_logging.DEBUG),
            (3.0, wt_logging.INFO),
            (7.9, wt_logging.INFO),
            (8.0, wt_logging.WARN),
            (60.0, wt_logging.WARN),
        ]
        for elapsed, expected in cases:
            with self.subTest(elapsed=elapsed):
                self.assertEqual(wt_flow_locator._locate_elapsed_level(elapsed), expected)

    def test_false_alarm_below_three_seconds_is_silent_by_default(self):
        """回归：0.83s 级别的告警在默认（INFO）档下不应输出，避免稀释真实告警。"""
        with _env_without_log_switch():
            wt_flow_locator._log_locate_elapsed("流程控件定位耗时较长: seconds=0.83", 0.83)
            self.assertEqual(self.captured, [])

    def test_slow_locate_is_reported_at_info(self):
        with _env_without_log_switch():
            wt_flow_locator._log_locate_elapsed("流程控件定位耗时较长: seconds=4.20", 4.2)
            self.assertEqual(len(self.captured), 1)
            self.assertIn("seconds=4.20", self.captured[0])

    def test_very_slow_locate_is_reported_at_warn(self):
        with _env_without_log_switch():
            wt_flow_locator._log_locate_elapsed("流程控件定位耗时较长: seconds=19.5", 19.5)
            self.assertEqual(len(self.captured), 1)

    def test_debug_tier_appears_when_debug_enabled(self):
        with _env_without_log_switch(WT_LOG_LEVEL="DEBUG"):
            wt_flow_locator._log_locate_elapsed("流程控件定位耗时较长: seconds=0.83", 0.83)
            self.assertEqual(len(self.captured), 1)


class WindowFallbackLoggingTests(LogCaptureMixin, unittest.TestCase):
    """窗口严格过滤无命中后的回退来源：收敛为一行，且按来源分级。"""

    def test_normal_degradation_paths_are_debug_only(self):
        """主窗候选 / win32 枚举属正常降级，默认档下不输出。"""
        with _env_without_log_switch():
            for source in ("main_window_candidates", "win32_enumeration"):
                with self.subTest(source=source):
                    self.captured[:] = []
                    wt_flow_locator._log_window_fallback_source(source, 1)
                    self.assertEqual(self.captured, [])

    def test_foreground_fallback_keeps_info(self):
        """前置窗口回退是可能命中错误窗口的降级信号（知识库模式 C），保留 INFO。"""
        with _env_without_log_switch():
            wt_flow_locator._log_window_fallback_source("foreground_window", 1)
            self.assertEqual(len(self.captured), 1)
            self.assertIn("foreground_window", self.captured[0])

    def test_single_line_per_call(self):
        """回归：改造前同路径连发「采用主窗候选」+「回退候选窗口」两行。"""
        with _env_without_log_switch(WT_LOG_LEVEL="DEBUG"):
            wt_flow_locator._log_window_fallback_source("main_window_candidates", 1)
            self.assertEqual(len(self.captured), 1, "每次回退只应产生一行日志")

    def test_debug_path_emits_when_enabled(self):
        with _env_without_log_switch(WT_LOG_LEVEL="DEBUG"):
            wt_flow_locator._log_window_fallback_source("main_window_candidates", 2)
            self.assertEqual(len(self.captured), 1)
            self.assertIn("候选 2 个", self.captured[0])


class DebugGateTests(LogCaptureMixin, unittest.TestCase):
    """高频诊断转储受统一开关门控。"""

    def test_debug_lines_suppressed_by_default(self):
        with _env_without_log_switch():
            wt_flow_locator._log_debug("[DEBUG] click_flow_control 判定: step=x")
            self.assertEqual(self.captured, [])

    def test_debug_lines_emitted_with_debug_events_switch(self):
        """WT_DEBUG_EVENTS=1 与 WT_LOG_LEVEL=DEBUG 等效，避免两个互不相干的开关。"""
        with _env_without_log_switch(WT_DEBUG_EVENTS="1"):
            wt_flow_locator._log_debug("[DEBUG] click_flow_control 判定: step=x")
            self.assertEqual(len(self.captured), 1)

    def test_injected_level_aware_logger_takes_precedence(self):
        received = []
        with patch.object(wt_flow_locator, "_LOG_AT", lambda lv, msg: received.append((lv, msg))):
            wt_flow_locator._log_debug("[DEBUG] 判定")
        self.assertEqual(received, [(wt_logging.DEBUG, "[DEBUG] 判定")])


class ExecutorDebugGateTests(unittest.TestCase):
    """executor 侧同样具备级别门控与单参数兼容。"""

    def setUp(self):
        wt_logging.reset_min_level()
        self.addCleanup(wt_logging.reset_min_level)

    def test_executor_debug_gated(self):
        import wt_flow_executor

        captured = []
        with _env_without_log_switch():
            with patch.object(wt_flow_executor, "_LOG_AT", None), \
                 patch.object(wt_flow_executor, "_LOG_STEP", captured.append):
                wt_flow_executor._log_debug("[DEBUG] executor 判定")
                self.assertEqual(captured, [])
                wt_flow_executor._LOG_STEP("步骤结束: step=x, status=success")
                self.assertEqual(len(captured), 1)

    def test_executor_configure_accepts_log_at(self):
        import wt_flow_executor

        received = []
        try:
            wt_flow_executor.configure_flow_executor(
                log_at=lambda lv, msg: received.append((lv, msg))
            )
            wt_flow_executor._log_at(wt_logging.WARN, "w")
            self.assertEqual(received, [(wt_logging.WARN, "w")])
        finally:
            wt_flow_executor._LOG_AT = None


class LogStepLevelTests(unittest.TestCase):
    """WT_AUT_recorded.log_step：级别门控 + 统一行格式 + 单参数兼容。

    通过替换 _append_log_file 捕获落盘内容 —— 测试自身不写盘、不删文件。
    """

    def setUp(self):
        wt_logging.reset_min_level()
        self.addCleanup(wt_logging.reset_min_level)
        import WT_AUT_recorded

        self.recorder = WT_AUT_recorded
        self.lines = []
        for target, attr, repl in (
            (WT_AUT_recorded, "_append_log_file", self.lines.append),
            (builtins, "print", lambda *a, **k: None),
        ):
            p = patch.object(target, attr, repl)
            p.start()
            self.addCleanup(p.stop)

    def test_positional_single_arg_still_works(self):
        """大量既有调用点与测试依赖 log_step(message) 单参数形式。"""
        with _env_without_log_switch():
            self.recorder.log_step("步骤结束: step=x, status=success")
        self.assertEqual(len(self.lines), 1)
        self.assertRegex(
            self.lines[0],
            r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] \[INFO \] ",
        )

    def test_level_token_present_and_width_fixed(self):
        with _env_without_log_switch():
            self.recorder.log_step("定位失败：控件未找到", level=wt_logging.ERROR)
        line = self.lines[0]
        self.assertIn("[ERROR]", line)
        # 取第二个方括号字段的内容（第一个是时间戳），级别内容应定宽 5 字符
        label = re.findall(r"\[([^\]]*)\]", line)[1]
        self.assertEqual(len(label), 5, "级别标签应定宽 5 字符: %r" % label)

    def test_debug_level_is_dropped_by_default(self):
        with _env_without_log_switch():
            self.recorder.log_step("执行步骤列表: ['step_1', 'step_2']", level=wt_logging.DEBUG)
        self.assertEqual(self.lines, [], "DEBUG 行默认不应落盘")

    def test_debug_level_written_when_enabled(self):
        with _env_without_log_switch(WT_LOG_LEVEL="DEBUG"):
            self.recorder.log_step("执行步骤列表: ['step_1']", level=wt_logging.DEBUG)
        self.assertEqual(len(self.lines), 1)

    def test_level_inferred_when_not_given(self):
        with _env_without_log_switch():
            self.recorder.log_step("已剔除错误项=0")
        self.assertIn("[INFO ]", self.lines[0], "无害上下文不应被判为 ERROR")


class NoiseRegressionGuardTests(unittest.TestCase):
    """降噪回归守卫：已被移除的噪音来源不得复活。

    只扫代码行（跳过注释），避免历史注释里的示例文本造成误报。
    """

    def _source(self, name):
        with open(os.path.join(PROJECT_DIR, name), "r", encoding="utf-8") as f:
            return f.read()

    def _code_only(self, name):
        """去掉整行注释后的源码 —— 历史注释里会引用旧日志文本，不应误判。"""
        kept = []
        for raw in self._source(name).splitlines():
            if raw.strip().startswith("#"):
                continue
            kept.append(raw)
        return "\n".join(kept)

    def test_duplicated_window_filter_lines_removed(self):
        code = self._code_only("wt_flow_locator.py")
        for removed in (
            "窗口过滤严格无命中，采用运行时主窗口候选",
            "窗口过滤严格无命中，回退候选窗口",
            "窗口过滤严格无命中，回退前置窗口单候选",
        ):
            with self.subTest(removed=removed):
                self.assertNotIn(removed, code, "重复噪音行已收敛，不应复活")

    def test_consolidated_window_fallback_line_exists(self):
        code = self._code_only("wt_flow_locator.py")
        self.assertIn("窗口严格过滤无命中，回退来源=", code)

    def test_single_threshold_constant_removed(self):
        code = self._code_only("wt_flow_locator.py")
        self.assertNotIn("elapsed >= 0.8:", code, "单阈值误报判定应已替换为分级")

    def test_debug_dumps_are_gated(self):
        code = self._code_only("wt_flow_locator.py")
        for gated in (
            "[DEBUG] click_flow_control 判定",
            "[DEBUG] type_text_into_flow_control 判定",
        ):
            with self.subTest(gated=gated):
                # 不得再以 _LOG_STEP（无条件出口）发出
                self.assertNotIn("_LOG_STEP(\n        f\"%s" % gated, code)
        self.assertIn("_log_debug(", code, "诊断转储应走级别门控出口")

    def test_step_list_dump_is_summarized(self):
        code = self._code_only("WT_AUT_recorded.py")
        self.assertIn("已解析待执行步骤", code)
        self.assertIn("完整清单见运行报告", code)

    def test_prune_count_demoted_to_debug(self):
        """剪枝计数属内部算法细节，走 DEBUG 出口；0 命中回退仍保留 INFO。"""
        code = self._code_only("wt_flow_locator.py")
        self.assertNotIn('_LOG_STEP(\n                    "[快查剪枝] 泛化', code)
        self.assertIn("[快查剪枝] 泛化", code, "消息本身应保留，只是换级别出口")
        self.assertIn("[快查剪枝] label_text 预过滤 0 命中", code)


if __name__ == "__main__":
    unittest.main()
