# encoding: utf-8
"""日志查询与过滤测试（P3）。

覆盖：级别判定（显式 token 优先 / 历史行回退）、统计摘要、三种过滤条件、
读取与 CLI 入口、以及「不依赖 tkinter」的约束。

设计约束：测试自身不删除文件（环境的批量删除守卫会拦截），
统一写入 ``tests/.tmp_log_query/`` 并截断复用。
"""
import contextlib
import io
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_logging
import wt_log_query


def _line(level, message, ts=1758000000.0, run_id=None):
    """按统一格式造一行带显式级别的日志。"""
    return wt_logging.format_line(level, message, run_id=run_id, timestamp=ts)


class LevelOfTests(unittest.TestCase):
    """级别判定：显式 token 优先，历史行回退关键字推断。"""

    def test_explicit_token_wins(self):
        line = _line(wt_logging.INFO, "下拉选项枚举进度: 已剔除错误项=0")
        self.assertEqual(wt_log_query.level_of(line), wt_logging.INFO)

    def test_historical_line_falls_back_to_keywords(self):
        """改造前落盘的日志没有级别 token，不能被静默滤空。"""
        line = "[2026-09-15 14:08:51] 步骤结束: step=x, status=failed, error=控件未找到"
        self.assertEqual(wt_log_query.level_of(line), wt_logging.ERROR)

    def test_historical_success_line(self):
        line = "[2026-09-15 14:08:51] 已通过流程链路匹配点击控件"
        self.assertEqual(wt_log_query.level_of(line), wt_logging.INFO)

    # 以下两行是 WT_AUT_recorded._log_run_end_banner 与
    # wt_run_reporting.finalize_run_report 实际写出的**真实文本**。
    # 早期测试用的是一行普通步骤日志，恰好绕开了「计数型零值被判 error」的场景。
    REAL_SUCCESS_BANNER = (
        "[2026-09-17 11:37:26.129] ========== 运行结束 · 状态=成功 · 用时 123.4s · "
        "步骤 16 步 · 成功 16 / 失败 0 =========="
    )
    REAL_SUCCESS_SUMMARY = (
        "[2026-09-17 11:37:26.129] 运行结果摘要已写入: status=success, executed=16, "
        "success=16, failed=0, skipped=0, fallback=0, report=x.json"
    )

    def test_real_success_banner_is_not_error(self):
        """成功运行的收尾标记恒含「失败 0」，不得判为 ERROR。"""
        self.assertEqual(wt_log_query.level_of(self.REAL_SUCCESS_BANNER), wt_logging.INFO)

    def test_real_success_summary_is_not_error(self):
        self.assertEqual(wt_log_query.level_of(self.REAL_SUCCESS_SUMMARY), wt_logging.INFO)

    def test_error_filter_does_not_match_successful_run(self):
        """P3 的功能性误导回归：选「ERROR 及以上」不应筛出成功运行的行。"""
        log_filter = wt_log_query.LogFilter(min_level=wt_logging.ERROR)
        self.assertFalse(log_filter.matches(self.REAL_SUCCESS_BANNER))
        self.assertFalse(log_filter.matches(self.REAL_SUCCESS_SUMMARY))
        self.assertEqual(
            wt_log_query.summarize([self.REAL_SUCCESS_BANNER, self.REAL_SUCCESS_SUMMARY]),
            "共 2 行 · INFO 2",
        )

    def test_blank_line_is_info(self):
        self.assertEqual(wt_log_query.level_of(""), wt_logging.INFO)


class SummaryTests(unittest.TestCase):
    """统计摘要。"""

    LINES = None

    def setUp(self):
        self.lines = [
            _line(wt_logging.INFO, "a"),
            _line(wt_logging.INFO, "b"),
            _line(wt_logging.WARN, "c"),
            _line(wt_logging.ERROR, "d"),
            _line(wt_logging.DEBUG, "e"),
            _line(wt_logging.DEBUG, "f"),
        ]

    def test_level_counts(self):
        self.assertEqual(
            wt_log_query.level_counts(self.lines),
            {wt_logging.INFO: 2, wt_logging.WARN: 1, wt_logging.ERROR: 1, wt_logging.DEBUG: 2},
        )

    def test_level_counts_skips_blank_lines(self):
        self.assertEqual(wt_log_query.level_counts(["", "   ", "\n"]), {})

    def test_summarize_orders_by_severity(self):
        text = wt_log_query.summarize(self.lines)
        self.assertTrue(text.startswith("共 6 行"))
        self.assertLess(text.index("ERROR"), text.index("WARN"))
        self.assertLess(text.index("WARN"), text.index("INFO"))
        self.assertLess(text.index("INFO"), text.index("DEBUG"))

    def test_summarize_omits_zero_counts(self):
        text = wt_log_query.summarize([_line(wt_logging.INFO, "a")])
        self.assertEqual(text, "共 1 行 · INFO 1")

    def test_summarize_empty(self):
        self.assertEqual(wt_log_query.summarize([]), "共 0 行")


class LogFilterTests(unittest.TestCase):
    """过滤条件：级别 / 关键字 / 步骤，三者取「与」。"""

    def setUp(self):
        self.lines = [
            _line(wt_logging.INFO, "已通过流程链路匹配点击控件: step=step_1, control=c1"),
            _line(wt_logging.WARN, "流程控件定位耗时较长: step=step_1, control=c1, seconds=8.2"),
            _line(wt_logging.ERROR, "定位失败：未找到匹配控件 step=step_2, control=c2"),
            _line(wt_logging.DEBUG, "[DEBUG] click_flow_control 判定: step=step_2, control=c2"),
            _line(wt_logging.INFO, "========== 运行开始 · runId=wt_run_x · 待执行 3 步 =========="),
        ]

    def test_inactive_filter_returns_all(self):
        log_filter = wt_log_query.LogFilter()
        self.assertFalse(log_filter.is_active())
        self.assertEqual(log_filter.apply(self.lines), self.lines)

    def test_min_level_filter(self):
        log_filter = wt_log_query.LogFilter(min_level=wt_logging.WARN)
        shown = log_filter.apply(self.lines)
        self.assertEqual(len(shown), 2)
        self.assertTrue(all(wt_log_query.level_of(l) in ("WARN", "ERROR") for l in shown))

    def test_min_level_debug_keeps_everything(self):
        log_filter = wt_log_query.LogFilter(min_level=wt_logging.DEBUG)
        self.assertEqual(len(log_filter.apply(self.lines)), len(self.lines))

    def test_keyword_filter(self):
        log_filter = wt_log_query.LogFilter(keyword="未找到")
        shown = log_filter.apply(self.lines)
        self.assertEqual(len(shown), 1)
        self.assertIn("定位失败", shown[0])

    def test_step_filter_is_exact_on_declared_step_id(self):
        log_filter = wt_log_query.LogFilter(step_id="step_2")
        shown = log_filter.apply(self.lines)
        self.assertEqual(len(shown), 2)
        for line in shown:
            step, _ = wt_logging.extract_step_fields(line)
            self.assertEqual(step, "step_2")

    def test_step_filter_excludes_lines_without_step_id(self):
        """不带 step= 的运行边界行不参与步骤过滤，保证结果可预期。"""
        log_filter = wt_log_query.LogFilter(step_id="step_1")
        shown = log_filter.apply(self.lines)
        self.assertNotIn(self.lines[4], shown)

    def test_conditions_are_combined_with_and(self):
        log_filter = wt_log_query.LogFilter(
            min_level=wt_logging.ERROR, step_id="step_2"
        )
        shown = log_filter.apply(self.lines)
        self.assertEqual(len(shown), 1)
        self.assertIn("定位失败", shown[0])

    def test_blank_lines_never_match(self):
        self.assertFalse(wt_log_query.LogFilter(keyword="a").matches(""))
        self.assertFalse(wt_log_query.LogFilter(keyword="a").matches("   "))

    def test_keyword_is_whitespace_trimmed(self):
        self.assertEqual(wt_log_query.LogFilter(keyword="  失败  ").keyword, "失败")

    def test_invalid_level_is_ignored(self):
        log_filter = wt_log_query.LogFilter(min_level="NOT_A_LEVEL")
        self.assertIsNone(log_filter.min_level)
        self.assertFalse(log_filter.is_active())

    def test_describe_when_inactive(self):
        text = wt_log_query.LogFilter().describe(134, 134)
        self.assertIn("未过滤", text)
        self.assertIn("134", text)

    def test_describe_lists_active_conditions(self):
        text = wt_log_query.LogFilter(
            min_level=wt_logging.WARN, keyword="失败", step_id="step_2"
        ).describe(134, 3)
        self.assertIn("WARN 及以上", text)
        self.assertIn("失败", text)
        self.assertIn("step_2", text)
        self.assertIn("显示 3/134 行", text)


class ReadAndQueryTests(unittest.TestCase):
    """读取与端到端查询。"""

    PROBE_DIR = os.path.join(TESTS_DIR, ".tmp_log_query")

    def setUp(self):
        os.makedirs(self.PROBE_DIR, exist_ok=True)
        self.path = os.path.join(self.PROBE_DIR, "probe.log")
        lines = [
            _line(wt_logging.INFO, "步骤一: step=step_1"),
            "",
            _line(wt_logging.WARN, "慢: step=step_2, seconds=8.2"),
            _line(wt_logging.ERROR, "失败: step=step_2"),
        ]
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    def test_read_skips_blank_lines(self):
        lines = wt_log_query.read_log_lines(self.path)
        self.assertEqual(len(lines), 3)

    def test_read_tail(self):
        lines = wt_log_query.read_log_lines(self.path, tail=2)
        self.assertEqual(len(lines), 2)
        self.assertIn("失败", lines[-1])

    def test_read_missing_file_returns_empty(self):
        self.assertEqual(wt_log_query.read_log_lines(os.path.join(self.PROBE_DIR, "无.log")), [])

    def test_query_returns_shown_all_and_status(self):
        shown, all_lines, status = wt_log_query.query(
            self.path, wt_log_query.LogFilter(min_level=wt_logging.WARN)
        )
        self.assertEqual(len(all_lines), 3)
        self.assertEqual(len(shown), 2)
        self.assertIn("显示 2/3 行", status)

    def test_query_without_filter(self):
        shown, all_lines, status = wt_log_query.query(self.path)
        self.assertEqual(len(shown), len(all_lines))
        self.assertIn("未过滤", status)


class CliTests(unittest.TestCase):
    """命令行入口：让日志查询在 GUI 之外也可用。"""

    PROBE_DIR = os.path.join(TESTS_DIR, ".tmp_log_query")

    def setUp(self):
        os.makedirs(self.PROBE_DIR, exist_ok=True)
        self.path = os.path.join(self.PROBE_DIR, "cli.log")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join([
                _line(wt_logging.INFO, "步骤一: step=step_1"),
                _line(wt_logging.WARN, "慢: step=step_2, seconds=8.2"),
                _line(wt_logging.ERROR, "失败: step=step_2"),
            ]) + "\n")

    def _run(self, *argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = wt_log_query.main(list(argv))
        return code, buffer.getvalue()

    def test_summary_mode(self):
        code, out = self._run(self.path, "--summary")
        self.assertEqual(code, 0)
        self.assertIn("共 3 行", out)
        self.assertIn("ERROR 1", out)

    def test_level_filter(self):
        code, out = self._run(self.path, "--min-level", "ERROR")
        self.assertEqual(code, 0)
        self.assertIn("显示 1/3 行", out)
        self.assertIn("失败", out)
        self.assertNotIn("步骤一", out)

    def test_step_filter(self):
        code, out = self._run(self.path, "--step", "step_2")
        self.assertEqual(code, 0)
        self.assertIn("显示 2/3 行", out)

    def test_keyword_filter(self):
        code, out = self._run(self.path, "--keyword", "慢")
        self.assertEqual(code, 0)
        self.assertIn("显示 1/3 行", out)

    def test_limit_keeps_latest_lines(self):
        code, out = self._run(self.path, "--limit", "1")
        self.assertEqual(code, 0)
        self.assertIn("仅显示最后 1 行", out)
        self.assertIn("失败", out)

    def test_missing_file_returns_two(self):
        code, out = self._run(os.path.join(self.PROBE_DIR, "不存在.log"))
        self.assertEqual(code, 2)


class DependencyConstraintTests(unittest.TestCase):
    """会被 CLI 与 headless 流程导入，不得引入 tkinter 依赖。"""

    def test_module_does_not_import_tkinter(self):
        import subprocess

        code = (
            "import sys; sys.path.insert(0, %r); "
            "import wt_log_query; "
            "print('tkinter' in sys.modules)"
        ) % PROJECT_DIR
        out = subprocess.check_output(
            [sys.executable, "-c", code], cwd=PROJECT_DIR, stderr=subprocess.STDOUT
        ).decode("utf-8", "replace").strip().splitlines()[-1]
        self.assertEqual(out, "False", "wt_log_query 不应 import tkinter")


if __name__ == "__main__":
    unittest.main()
