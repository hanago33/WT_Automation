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
import json
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

    def test_count_zero_summary_is_not_an_error(self):
        """计数型零值：运行摘要/收尾标记恒含「失败 0」「failed=0」，
        成功运行也一定有这些字段 —— 不能判成 ERROR（含「成功」时判 success 是对的）。"""
        lines = [
            "========== 运行结束 · 状态=成功 · 用时 123.4s · 步骤 16 步 · 成功 16 / 失败 0 ==========",
            "运行结果摘要已写入: status=success, executed=16, success=16, failed=0, skipped=0",
            "运行结果摘要已写入: status=success, FAILED=0",
            "全流程 失败 0 次",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertNotEqual(
                    wt_logging.classify(line), "error", "计数为 0 不应判为 error"
                )

    def test_successful_summary_is_coloured_success(self):
        """成功收尾行应拿到 success 着色，而不是被 error 抢走。"""
        line = (
            "========== 运行结束 · 状态=成功 · 用时 123.4s · "
            "步骤 16 步 · 成功 16 / 失败 0 =========="
        )
        self.assertEqual(wt_logging.classify(line), "success")

    def test_count_zero_does_not_mask_real_failures(self):
        """白名单只针对「0」，真实失败计数仍须判 error。"""
        lines = [
            "运行结果摘要已写入: status=failed, executed=16, success=1, failed=15",
            "步骤结束: step=x, status=failed, error=控件未找到",
            "失败 3 次后放弃",
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(wt_logging.classify(line), "error")

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


class TagForLineTests(unittest.TestCase):
    """UI 侧着色入口：优先读写入方显式级别，缺失时才关键字兜底。"""

    def test_explicit_level_token_wins_over_keywords(self):
        """行内含显式 [INFO ] 时，即使正文含"错误"也应按写入方级别着色。"""
        line = wt_logging.format_line(
            wt_logging.INFO, "下拉选项枚举进度: 已剔除错误项=0", timestamp=1758000000.0
        )
        self.assertEqual(wt_logging.tag_for_line(line), "info")

    def test_explicit_error_level_is_honoured(self):
        line = wt_logging.format_line(
            wt_logging.ERROR, "定位失败：控件未找到", timestamp=1758000000.0
        )
        self.assertEqual(wt_logging.tag_for_line(line), "error")

    def test_explicit_debug_level_maps_to_debug_tag(self):
        line = wt_logging.format_line(
            wt_logging.DEBUG, "[DEBUG] click_flow_control 判定", timestamp=1758000000.0
        )
        self.assertEqual(wt_logging.tag_for_line(line), "debug")

    def test_falls_back_to_keywords_without_level_token(self):
        """历史遗留的纯文本行（改造前落盘的日志）仍能正确着色。"""
        legacy = "[2026-09-15 14:08:51] 步骤结束: step=x, status=failed, error=控件未找到"
        self.assertEqual(wt_logging.tag_for_line(legacy), "error")
        self.assertEqual(
            wt_logging.tag_for_line("[2026-09-15 14:08:51] 已通过流程链路匹配点击控件"),
            "success",
        )

    def test_level_from_line_returns_none_without_token(self):
        self.assertIsNone(wt_logging.level_from_line("没有级别标记的一行"))
        self.assertIsNone(wt_logging.level_from_line(""))
        self.assertIsNone(wt_logging.level_from_line(None))

    def test_level_from_line_reads_each_level(self):
        for level in wt_logging.LEVELS:
            with self.subTest(level=level):
                line = wt_logging.format_line(level, "m", timestamp=1758000000.0)
                self.assertEqual(wt_logging.level_from_line(line), level)


class ClassifierConsolidationTests(unittest.TestCase):
    """改造前存在 4 个各不相同的分类器，现已全部委托 wt_logging。"""

    def test_launcher_classifiers_delegate(self):
        import WT_Launcher

        sample = wt_logging.format_line(
            wt_logging.WARN, "流程控件定位耗时较长", timestamp=1758000000.0
        )
        self.assertEqual(WT_Launcher.ServerMonitorWindow._classify_line(sample), "warning")

        app = WT_Launcher.LauncherApp.__new__(WT_Launcher.LauncherApp)
        self.assertEqual(app._tag_for_line(sample), "warning")

    def test_queue_window_classifier_delegates(self):
        import wt_task_queue_window

        sample = wt_logging.format_line(
            wt_logging.ERROR, "定位失败", timestamp=1758000000.0
        )
        self.assertEqual(
            wt_task_queue_window.TaskQueueWindow._classify_line(sample), "error"
        )

    def test_widget_tag_sets_cover_debug(self):
        """监视器窗口原先白名单缺 debug，DEBUG 行会被强制降级为 info 色。"""
        for dark in (False, True):
            tags = dict(wt_logging.tag_colors_for_widget(dark=dark))
            self.assertIn("debug", tags)
            self.assertIn("system", tags)


class RunBannerTests(unittest.TestCase):
    """运行标识与运行边界标记（人读日志里定位一次运行的起止）。"""

    def setUp(self):
        wt_logging.clear_run_id()
        self.addCleanup(wt_logging.clear_run_id)

    def test_run_id_roundtrip(self):
        self.assertEqual(wt_logging.get_run_id(), "")
        wt_logging.set_run_id("wt_run_20260917_102341_123_001")
        self.assertEqual(wt_logging.get_run_id(), "wt_run_20260917_102341_123_001")
        wt_logging.clear_run_id()
        self.assertEqual(wt_logging.get_run_id(), "")

    def test_start_banner_contains_run_id_and_step_count(self):
        wt_logging.set_run_id("wt_run_20260917_102341_123_001")
        banner = wt_logging.format_run_banner("start", step_count=111)
        self.assertIn("运行开始", banner)
        self.assertIn("wt_run_20260917_102341_123_001", banner)
        self.assertIn("待执行 111 步", banner)
        self.assertTrue(banner.startswith("======") and banner.endswith("======"))

    def test_end_banner_contains_status_elapsed_and_counts(self):
        banner = wt_logging.format_run_banner(
            "end", status="成功", elapsed_seconds=123.44, step_count=111,
            extra="成功 111 / 失败 0",
        )
        self.assertIn("运行结束", banner)
        self.assertIn("状态=成功", banner)
        self.assertIn("用时 123.4s", banner)
        self.assertIn("成功 111 / 失败 0", banner)

    def test_banner_omits_run_id_when_absent(self):
        """未登记 runId 时不应出现空字段。"""
        banner = wt_logging.format_run_banner("start", step_count=5)
        self.assertNotIn("runId=", banner)
        self.assertNotIn("·  ·", banner)

    def test_banner_does_not_duplicate_timestamp(self):
        """行首已有时间戳，标记本身不再重复，避免视觉冗余。"""
        banner = wt_logging.format_run_banner("start", run_id="wt_run_x", step_count=1)
        self.assertNotRegex(banner, r"\d{4}-\d{2}-\d{2}")

    def test_banner_is_greppable_by_kind(self):
        for kind, marker in (("start", "运行开始"), ("end", "运行结束")):
            with self.subTest(kind=kind):
                self.assertIn(marker, wt_logging.format_run_banner(kind))


class JsonlSidecarTests(unittest.TestCase):
    """JSONL 旁路：文本日志的机读镜像。

    测试自身不删文件（截断代替删除），避免触发环境的批量删除守卫。
    """

    PROBE_DIR = os.path.join(TESTS_DIR, ".tmp_jsonl")
    PROBE_PATH = os.path.join(PROBE_DIR, "probe.jsonl")

    def setUp(self):
        wt_logging.stop_jsonl()
        wt_logging.clear_run_id()
        self.addCleanup(wt_logging.stop_jsonl)
        self.addCleanup(wt_logging.clear_run_id)
        os.makedirs(self.PROBE_DIR, exist_ok=True)
        with open(self.PROBE_PATH, "w", encoding="utf-8"):
            pass  # 截断而非删除

    def _records(self):
        with open(self.PROBE_PATH, "r", encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_no_session_is_a_silent_noop(self):
        """未开启旁路时不应写盘、不应抛错。"""
        self.assertEqual(wt_logging.jsonl_path(), "")
        self.assertIsNone(wt_logging.log_event(wt_logging.INFO, "x"))
        self.assertEqual(self._records(), [])

    def test_start_and_stop_session(self):
        self.assertEqual(wt_logging.start_jsonl(self.PROBE_PATH), self.PROBE_PATH)
        self.assertEqual(wt_logging.jsonl_path(), self.PROBE_PATH)
        wt_logging.stop_jsonl()
        self.assertEqual(wt_logging.jsonl_path(), "")

    def test_one_line_per_event(self):
        wt_logging.start_jsonl(self.PROBE_PATH)
        for i in range(3):
            wt_logging.log_event(wt_logging.INFO, "第 %d 条" % i)
        self.assertEqual(len(self._records()), 3)

    def test_chinese_is_not_escaped(self):
        """人读友好：中文原样写入，不转成 \\uXXXX。"""
        wt_logging.start_jsonl(self.PROBE_PATH)
        wt_logging.log_event(wt_logging.INFO, "已通过流程链路匹配点击控件")
        with open(self.PROBE_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
        self.assertIn("已通过流程链路匹配点击控件", raw)
        self.assertNotIn("\\u", raw)

    def test_empty_optional_fields_are_omitted(self):
        """空字段整字段省略，让常见行保持简短。"""
        wt_logging.start_jsonl(self.PROBE_PATH)
        wt_logging.log_event(wt_logging.INFO, "无步骤上下文的一行")
        record = self._records()[0]
        for key in ("stepId", "controlId", "event", "elapsedMs", "detail", "module"):
            with self.subTest(key=key):
                self.assertNotIn(key, record)
        self.assertIn("ts", record)
        self.assertIn("level", record)
        self.assertIn("message", record)

    def test_run_id_is_attached(self):
        wt_logging.set_run_id("wt_run_20260917_102341_123_001")
        wt_logging.start_jsonl(self.PROBE_PATH)
        wt_logging.log_event(wt_logging.INFO, "x")
        self.assertEqual(
            self._records()[0]["runId"], "wt_run_20260917_102341_123_001"
        )

    def test_step_fields_extracted_from_message(self):
        wt_logging.start_jsonl(self.PROBE_PATH)
        wt_logging.log_event(
            wt_logging.INFO,
            "已通过流程链路匹配点击控件: step=step_1_scan2_1, control=control_map_439",
        )
        record = self._records()[0]
        self.assertEqual(record["stepId"], "step_1_scan2_1")
        self.assertEqual(record["controlId"], "control_map_439")

    def test_ctrl_alias_is_recognised(self):
        wt_logging.start_jsonl(self.PROBE_PATH)
        wt_logging.log_event(wt_logging.INFO, "定位失败: step=step_12_scan2_12, ctrl=ctrl_refpoint")
        self.assertEqual(self._records()[0]["controlId"], "ctrl_refpoint")

    def test_explicit_step_fields_win_over_extraction(self):
        wt_logging.start_jsonl(self.PROBE_PATH)
        wt_logging.log_event(
            wt_logging.INFO, "step=auto, control=auto_ctrl", step_id="explicit", control_id="exp_ctrl"
        )
        record = self._records()[0]
        self.assertEqual(record["stepId"], "explicit")
        self.assertEqual(record["controlId"], "exp_ctrl")

    def test_message_is_last_field_for_readability(self):
        """message 长度最不可控，置末以保持前面标识字段列对齐。"""
        wt_logging.set_run_id("wt_run_probe")
        wt_logging.start_jsonl(self.PROBE_PATH)
        wt_logging.log_event(
            wt_logging.ERROR, "定位失败", step_id="s1", control_id="c1",
            event="locate_fail", elapsed_ms=1234.5,
        )
        keys = list(self._records()[0].keys())
        self.assertEqual(keys[-1], "message")
        self.assertEqual(keys[:3], ["ts", "level", "runId"])

    def test_elapsed_ms_is_rounded(self):
        wt_logging.start_jsonl(self.PROBE_PATH)
        wt_logging.log_event(wt_logging.INFO, "x", elapsed_ms=8200.123456)
        self.assertEqual(self._records()[0]["elapsedMs"], 8200.12)

    def test_extract_step_fields_handles_missing(self):
        self.assertEqual(wt_logging.extract_step_fields(""), ("", ""))
        self.assertEqual(wt_logging.extract_step_fields(None), ("", ""))
        self.assertEqual(wt_logging.extract_step_fields("没有字段"), ("", ""))


class TagForMessageTests(unittest.TestCase):
    """着色标签：级别定基调，INFO/DEBUG 档按内容细分 success/system。

    回归背景：只按级别取 tag 会让 ``tag_for_level(INFO)`` 恒为 ``info``，
    「WT自动化流程完成」由绿变灰，色板里的 success 色值再无任何路径产出。
    """

    def test_completion_line_gets_success_tag(self):
        self.assertEqual(
            wt_logging.tag_for_message(wt_logging.INFO, "WT自动化流程完成"), "success"
        )

    def test_start_line_gets_system_tag(self):
        self.assertEqual(
            wt_logging.tag_for_message(wt_logging.INFO, "开始执行步骤: step=step_1"), "system"
        )

    def test_plain_info_stays_info(self):
        """既无成功/开始语义、也无异常语义的行保持 info。"""
        self.assertEqual(
            wt_logging.tag_for_message(wt_logging.INFO, "下拉选项枚举进度: step=step_1"),
            "info",
        )

    def test_success_wording_gets_success_tag(self):
        """「已通过」属成功语义 —— 动作成功行应保留绿色。"""
        self.assertEqual(
            wt_logging.tag_for_message(wt_logging.INFO, "已通过流程链路匹配点击控件"),
            "success",
        )

    def test_error_level_is_not_overridden_by_content(self):
        """ERROR 的语义由级别表达，不被内容改写。"""
        self.assertEqual(
            wt_logging.tag_for_message(wt_logging.ERROR, "运行结束 · 成功 16 / 失败 0"),
            "error",
        )

    def test_success_wording_at_error_level_stays_error(self):
        self.assertEqual(
            wt_logging.tag_for_message(wt_logging.ERROR, "流程完成但存在失败步骤"), "error"
        )

    def test_debug_level_can_still_be_system(self):
        self.assertEqual(
            wt_logging.tag_for_message(wt_logging.DEBUG, "启动时预扫描"), "system"
        )

    def test_warning_level_is_not_overridden(self):
        self.assertEqual(
            wt_logging.tag_for_message(wt_logging.WARN, "流程完成但有跳过"), "warning"
        )


class JsonlRetentionTests(unittest.TestCase):
    """JSONL 目录保留策略：每次运行一个文件，必须设上限，否则长期累积。"""

    PROBE_DIR = os.path.join(TESTS_DIR, ".tmp_jsonl_retention")

    def setUp(self):
        os.makedirs(self.PROBE_DIR, exist_ok=True)
        for index in range(6):
            path = os.path.join(self.PROBE_DIR, "wt_run_20260917_10%02d000_000_001.jsonl" % index)
            with open(path, "w", encoding="utf-8") as f:
                f.write("{}\n")

    def _names(self):
        return sorted(n for n in os.listdir(self.PROBE_DIR) if n.endswith(".jsonl"))

    def test_selection_keeps_latest_n(self):
        """选择逻辑是纯函数，不依赖真的删除（测试环境可能禁删）。"""
        targets = wt_logging.jsonl_files_to_prune(
            os.path.join(self.PROBE_DIR, "new.jsonl"), keep=2
        )
        self.assertEqual(len(targets), 4)
        self.assertTrue(all(t.endswith(".jsonl") for t in targets))
        self.assertNotIn("_100500_", " ".join(os.path.basename(t) for t in targets),
                         "最新的两个不应在清理列表中")

    def test_selection_noop_when_below_limit(self):
        self.assertEqual(
            wt_logging.jsonl_files_to_prune(os.path.join(self.PROBE_DIR, "n.jsonl"), keep=100),
            [],
        )

    def test_zero_or_negative_keep_is_noop(self):
        for keep in (0, -1):
            with self.subTest(keep=keep):
                self.assertEqual(
                    wt_logging.jsonl_files_to_prune(
                        os.path.join(self.PROBE_DIR, "n.jsonl"), keep=keep
                    ),
                    [],
                )

    def test_missing_directory_is_safe(self):
        self.assertEqual(
            wt_logging.jsonl_files_to_prune(os.path.join(self.PROBE_DIR, "无目录", "n.jsonl")),
            [],
        )
        self.assertEqual(wt_logging.jsonl_files_to_prune(""), [])

    def test_prune_actually_removes_files(self):
        """真实删除路径。环境禁删（沙箱守卫）时跳过，不算失败。"""
        try:
            removed = wt_logging.prune_jsonl_dir(
                os.path.join(self.PROBE_DIR, "new.jsonl"), keep=2
            )
        except SystemExit:
            self.skipTest("环境禁删（沙箱批量删除守卫）")
        self.assertEqual(removed, 4)
        self.assertEqual(len(self._names()), 2)

    def test_default_keep_is_sane(self):
        self.assertGreaterEqual(wt_logging.JSONL_KEEP_RUNS, 10)


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
