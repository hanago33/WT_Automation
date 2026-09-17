# encoding: utf-8
"""wt_logging 统一日志基础设施测试（P0 立规）。

覆盖：
- 五级枚举、级别别名归一化、级别标签定宽对齐
- 统一行格式：字段省略规则、runId 截断、毫秒精度
- classify() 关键字兜底：修复 `已剔除错误项=0` 被误判为 error 的缺陷
- 级别开关：WT_LOG_LEVEL / WT_DEBUG_EVENTS
- 配色收口：来自 wt_theme，浅底与深底两组
- 依赖约束：本模块不得 import tkinter
"""
import os
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_logging


class LevelNormalizationTests(unittest.TestCase):
    """级别枚举与别名归一化。"""

    def test_levels_are_five_and_ordered(self):
        self.assertEqual(
            list(wt_logging.LEVELS),
            ["DEBUG", "INFO", "WARN", "ERROR", "CRIT"],
        )
        ranks = [wt_logging.level_rank(lv) for lv in wt_logging.LEVELS]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(len(set(ranks)), 5)

    def test_common_aliases_normalize(self):
        cases = {
            "warning": wt_logging.WARN,
            "WARNING": wt_logging.WARN,
            "critical": wt_logging.CRIT,
            "fatal": wt_logging.CRIT,
            "trace": wt_logging.DEBUG,
            "err": wt_logging.ERROR,
            "notice": wt_logging.INFO,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(wt_logging.normalize_level(raw), expected)

    def test_unknown_level_falls_back_to_default(self):
        self.assertEqual(wt_logging.normalize_level("bogus"), wt_logging.INFO)
        self.assertIsNone(wt_logging.normalize_level("bogus", default=None))
        self.assertEqual(wt_logging.normalize_level(None), wt_logging.INFO)

    def test_level_label_width_is_five(self):
        """级别标签必须定宽 5 字符，否则日志列会错位。"""
        for level in wt_logging.LEVELS:
            with self.subTest(level=level):
                line = wt_logging.format_line(level, "m", timestamp=1758000000.0)
                label = line.split("] [", 1)[1].split("]", 1)[0]
                self.assertEqual(len(label), 5, "级别标签应定宽 5 字符: %r" % label)

    def test_tag_mapping(self):
        self.assertEqual(wt_logging.tag_for_level(wt_logging.DEBUG), "debug")
        self.assertEqual(wt_logging.tag_for_level(wt_logging.INFO), "info")
        self.assertEqual(wt_logging.tag_for_level(wt_logging.WARN), "warning")
        self.assertEqual(wt_logging.tag_for_level(wt_logging.ERROR), "error")
        self.assertEqual(wt_logging.tag_for_level(wt_logging.CRIT), "error")


class FormatLineTests(unittest.TestCase):
    """统一行格式。"""

    def test_basic_shape_with_millis(self):
        line = wt_logging.format_line(
            wt_logging.INFO, "已通过流程链路匹配点击控件", timestamp=1758000000.123
        )
        self.assertRegex(line, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}\] \[INFO \] ")
        self.assertTrue(line.endswith("已通过流程链路匹配点击控件"))

    def test_optional_fields_omitted_when_empty(self):
        line = wt_logging.format_line(wt_logging.INFO, "msg", timestamp=1758000000.0)
        self.assertNotIn("[]", line, "空 module/run_id 不应产生空方括号")

    def test_run_id_truncated_to_eight(self):
        line = wt_logging.format_line(
            wt_logging.INFO, "msg", run_id="a1b2c3d4e5f6", timestamp=1758000000.0
        )
        self.assertIn("[a1b2c3d4]", line)
        self.assertNotIn("e5f6", line)

    def test_field_order_is_time_level_runid_module_message(self):
        line = wt_logging.format_line(
            wt_logging.WARN,
            "流程控件定位耗时较长",
            module="wt_flow_locator",
            run_id="deadbeefcafe",
            timestamp=1758000000.0,
        )
        fields = re.findall(r"\[[^\]]*\]", line)
        self.assertEqual(len(fields), 4, "应恰有 4 个方括号字段: %r" % line)
        self.assertTrue(fields[0].startswith("[2025-09-16"))
        self.assertEqual(fields[1], "[WARN ]")
        self.assertEqual(fields[2], "[deadbeef]")
        self.assertEqual(fields[3], "[wt_flow_locator]")
        self.assertTrue(line.endswith("流程控件定位耗时较长"))


class ClassifyTests(unittest.TestCase):
    """关键字兜底分类（改造前 4 个独立分类器的统一替代）。"""

    def test_benign_error_wording_is_not_classified_as_error(self):
        """回归：`已剔除错误项=0` 含「错误」二字，改造前被误判为 error（红字）。"""
        lines = [
            "下拉选项枚举进度: step=step_10, 已剔除错误项=0",
            "下拉选项枚举进度: step=x, elapsed=19.5s, 已剔除错误项=0",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(wt_logging.classify(line), "info")

    def test_real_errors_are_detected(self):
        lines = [
            "步骤结束: step=x, status=failed, error=控件未找到",
            "步骤结束: step=x, status=failure",
            "定位失败: 未找到匹配控件",
            "Traceback (most recent call last):",
            "致命错误：进程崩溃",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(wt_logging.classify(line), "error")

    def test_warning_success_system_and_info(self):
        cases = [
            ("流程控件定位耗时较长: step=x, seconds=8.2", "warning"),
            ("前置[wait_visible] 等待超时", "warning"),
            ("Fallback 链执行成功: step=x", "success"),
            ("已通过流程链路匹配点击控件: step=x", "success"),
            ("开始执行步骤: step=x", "system"),
            ("已通过流程链路匹配输入文本: step=x", "success"),
        ]
        for line, expected in cases:
            with self.subTest(line=line):
                self.assertEqual(wt_logging.classify(line), expected)

    def test_plain_line_is_info(self):
        line = "[FlowLocator] 窗口过滤严格无命中，采用运行时主窗口候选 1 个"
        self.assertEqual(wt_logging.classify(line), "info")

    def test_empty_input_is_info(self):
        self.assertEqual(wt_logging.classify(""), "info")
        self.assertEqual(wt_logging.classify(None), "info")

    def test_detect_level_maps_tags_back_to_levels(self):
        self.assertEqual(wt_logging.detect_level("定位失败"), wt_logging.ERROR)
        self.assertEqual(wt_logging.detect_level("致命错误"), wt_logging.CRIT)
        self.assertEqual(wt_logging.detect_level("警告：超时"), wt_logging.WARN)
        self.assertEqual(wt_logging.detect_level("开始执行步骤"), wt_logging.INFO)


class MinLevelGateTests(unittest.TestCase):
    """级别开关：默认 INFO；WT_LOG_LEVEL / WT_DEBUG_EVENTS 可放开 DEBUG。"""

    def setUp(self):
        wt_logging.reset_min_level()
        self.addCleanup(wt_logging.reset_min_level)

    def _env(self, **values):
        clean = {k: v for k, v in os.environ.items() if k not in ("WT_LOG_LEVEL", "WT_DEBUG_EVENTS")}
        clean.update(values)
        return patch.dict(os.environ, clean, clear=True)

    def test_default_is_info_and_debug_gated(self):
        with self._env():
            self.assertEqual(wt_logging.resolve_min_level(), wt_logging.INFO)
            self.assertFalse(wt_logging.is_debug_enabled())
            self.assertTrue(wt_logging.is_enabled(wt_logging.INFO))
            self.assertFalse(wt_logging.is_enabled(wt_logging.DEBUG))

    def test_explicit_log_level_opens_debug(self):
        with self._env(WT_LOG_LEVEL="DEBUG"):
            self.assertTrue(wt_logging.is_debug_enabled())

    def test_debug_events_env_also_opens_debug(self):
        """与既有 ndjson 调试事件开关保持一致，避免出现两个互不相干的开关。"""
        with self._env(WT_DEBUG_EVENTS="1"):
            self.assertTrue(wt_logging.is_debug_enabled())

    def test_explicit_log_level_wins_over_debug_events(self):
        with self._env(WT_LOG_LEVEL="ERROR", WT_DEBUG_EVENTS="1"):
            self.assertEqual(wt_logging.resolve_min_level(), wt_logging.ERROR)
            self.assertFalse(wt_logging.is_enabled(wt_logging.WARN))
            self.assertTrue(wt_logging.is_enabled(wt_logging.ERROR))

    def test_set_and_reset_min_level(self):
        with self._env():
            wt_logging.set_min_level(wt_logging.ERROR)
            self.assertFalse(wt_logging.is_enabled(wt_logging.WARN))
            wt_logging.reset_min_level()
            self.assertTrue(wt_logging.is_enabled(wt_logging.WARN))


class ColorPaletteTests(unittest.TestCase):
    """配色收口：唯一来源为 wt_theme，浅底/深底两组。"""

    REQUIRED_KEYS = ("debug", "info", "warning", "error", "success", "system")
    REQUIRED_LEVELS = ("DEBUG", "INFO", "WARN", "ERROR", "CRIT")

    def test_palette_comes_from_wt_theme(self):
        import wt_theme

        for dark in (False, True):
            with self.subTest(dark=dark):
                self.assertEqual(
                    wt_logging.level_colors(dark=dark),
                    wt_theme.get_log_level_colors(dark=dark),
                )

    def test_both_variants_cover_all_tags_and_levels(self):
        for dark in (False, True):
            colors = wt_logging.level_colors(dark=dark)
            for key in self.REQUIRED_KEYS + self.REQUIRED_LEVELS:
                with self.subTest(dark=dark, key=key):
                    self.assertIn(key, colors)
                    self.assertRegex(colors[key], r"^#[0-9a-fA-F]{6}$")

    def test_light_and_dark_use_different_luminance(self):
        light = wt_logging.level_colors(dark=False)
        dark = wt_logging.level_colors(dark=True)
        self.assertNotEqual(light["info"], dark["info"])
        self.assertNotEqual(light["error"], dark["error"])

    def test_semantic_hues_follow_convention(self):
        """INFO 为正文色（浅底深、深底浅）；ERROR 偏红、WARN 偏琥珀。"""
        light = wt_logging.level_colors(dark=False)
        dark = wt_logging.level_colors(dark=True)

        def rgb(hex_color):
            h = hex_color.lstrip("#")
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))

        light_info = rgb(light["info"])
        dark_info = rgb(dark["info"])
        self.assertLess(sum(light_info), 200, "浅底 INFO 应为深色文字")
        self.assertGreater(sum(dark_info), 500, "深底 INFO 应为浅色文字")

        for colors in (light, dark):
            r, g, b = rgb(colors["error"])
            self.assertGreater(r, g, "ERROR 应偏红: %s" % colors["error"])
            self.assertGreater(r, b, "ERROR 应偏红: %s" % colors["error"])
            wr, wg, wb = rgb(colors["warning"])
            self.assertGreater(wr, wb, "WARN 应偏暖琥珀: %s" % colors["warning"])
            self.assertGreater(wg, wb, "WARN 应偏暖琥珀: %s" % colors["warning"])

    def test_tag_colors_for_widget_respects_requested_subset(self):
        subset = wt_logging.tag_colors_for_widget(
            dark=False, tags=("time", "debug", "info", "error")
        )
        tags = [tag for tag, _ in subset]
        self.assertIn("debug", tags)
        self.assertIn("info", tags)
        self.assertNotIn("system", tags, "未请求的标签不应出现")
        self.assertNotIn("time", tags, "调色板未定义的标签应被跳过")


class DependencyConstraintTests(unittest.TestCase):
    """wt_logging 会被 headless 流程/CLI 导入，不得引入 tkinter 依赖。"""

    def test_module_does_not_import_tkinter(self):
        code = (
            "import sys; sys.path.insert(0, %r); "
            "import wt_logging; "
            "print('tkinter' in sys.modules)"
        ) % PROJECT_DIR
        out = subprocess.check_output(
            [sys.executable, "-c", code],
            cwd=PROJECT_DIR,
            stderr=subprocess.STDOUT,
        ).decode("utf-8", "replace").strip().splitlines()[-1]
        self.assertEqual(out, "False", "wt_logging 不应 import tkinter")


if __name__ == "__main__":
    unittest.main()
