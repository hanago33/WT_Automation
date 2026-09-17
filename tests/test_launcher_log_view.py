# encoding: utf-8
"""总控台「运行日志」页过滤与联动的测试（P3）。

覆盖：
- 行缓冲与过滤条件的相互作用（缓冲保留全量、上屏只放命中的行）
- 过滤条读取 / 清除 / 状态文案
- 「运行报告 → 运行日志」联动：点某步骤即筛出该步日志并切页
- 缓冲行数上限

刻意**不使用真实 Tk 控件**：同进程内反复创建 Tk 根窗口会让后续类建不出根窗口
（见 test_log_lifecycle 的教训）。本组用记录式替身验证与控件无关的决策逻辑；
Tk 行号语义由 MonitorWindowLineCapTests 用真控件单独验证。
"""
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_log_query
import wt_logging
import WT_Launcher


class _FakeTextWidget(object):
    """记录式替身：只实现日志视图用到的方法。"""

    def __init__(self):
        self.lines = []          # 当前上屏的 (text, tag)
        self.cleared = 0
        self.trim_deletes = []
        self.state = None

    def config(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def insert(self, _index, text, tag=None):
        self.lines.append((str(text).rstrip("\n"), tag))

    def delete(self, start, end):
        if str(end) == "end":
            self.cleared += 1
            self.lines = []
        else:
            self.trim_deletes.append((str(start), str(end)))

    def index(self, _spec):
        return "%d.0" % (len(self.lines) + 1)

    def see(self, _index):
        pass

    def texts(self):
        return [text for text, _tag in self.lines]


class _FakeVar(object):
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _FakeNotebook(object):
    def __init__(self):
        self.selected = []

    def select(self, tab):
        self.selected.append(tab)


class _FakeLogTab(object):
    pass


def _make_app(lines=None, level_label=None, keyword="", step=""):
    """绕过 __init__ 构造，只装配日志视图需要的那部分状态。

    ``_log_all_lines`` 的生产契约是 ``[(文本, 标签)]``，这里按同样结构装配。
    """
    app = WT_Launcher.LauncherApp.__new__(WT_Launcher.LauncherApp)
    app.log_text = _FakeTextWidget()
    app._log_all_lines = [
        (str(line), wt_logging.tag_for_line(str(line))) for line in (lines or [])
    ]
    app._log_filter = wt_log_query.LogFilter()
    app.log_level_var = _FakeVar(
        level_label if level_label is not None else wt_log_query.LEVEL_CHOICES[0][0]
    )
    app.log_keyword_var = _FakeVar(keyword)
    app.log_step_var = _FakeVar(step)
    app.log_filter_status_var = _FakeVar("")
    app.report_notebook = _FakeNotebook()
    app.log_tab = _FakeLogTab()
    return app


def _line(level, message, ts=1758000000.0):
    return wt_logging.format_line(level, message, timestamp=ts)


SAMPLE = [
    _line(wt_logging.INFO, "已通过流程链路匹配点击控件: step=step_1, control=c1"),
    _line(wt_logging.WARN, "流程控件定位耗时较长: step=step_1, control=c1, seconds=8.2"),
    _line(wt_logging.ERROR, "定位失败：未找到匹配控件 step=step_2, control=c2"),
    _line(wt_logging.INFO, "步骤结束: step=step_2, status=failed"),
]


class AppendAndBufferTests(unittest.TestCase):
    """追加：全量入缓冲，只有命中的行上屏。"""

    def test_append_without_filter_shows_everything(self):
        app = _make_app()
        for line in SAMPLE:
            app._append_log(line)
        self.assertEqual(len(app._log_all_lines), len(SAMPLE))
        self.assertEqual(app.log_text.texts(), SAMPLE)

    def test_append_still_buffers_when_filtered_out(self):
        """被过滤掉的行必须留在缓冲里 —— 否则清除筛选后无法恢复。"""
        app = _make_app(level_label="ERROR 及以上")
        app._log_filter = app._build_log_filter()
        for line in SAMPLE:
            app._append_log(line)
        self.assertEqual(len(app._log_all_lines), len(SAMPLE), "缓冲应保留全量行")
        self.assertEqual(len(app.log_text.lines), 1, "只有 ERROR 行应上屏")

    def test_append_ignores_blank_message(self):
        app = _make_app()
        app._append_log("")
        app._append_log("   ")
        self.assertEqual(app._log_all_lines, [])
        self.assertEqual(app.log_text.lines, [])

    def test_append_keeps_tag(self):
        app = _make_app()
        app._append_log("某条消息", tag="warning")
        self.assertEqual(app.log_text.lines[0][1], "warning")

    def test_buffer_is_capped(self):
        app = _make_app()
        app._append_log("第一行")
        original = WT_Launcher.LOG_VIEW_MAX_LINES
        try:
            WT_Launcher.LOG_VIEW_MAX_LINES = 5
            for i in range(20):
                app._append_log("第 %d 行" % i)
            self.assertEqual(len(app._log_all_lines), 5)
        finally:
            WT_Launcher.LOG_VIEW_MAX_LINES = original

    def test_trim_is_invoked_when_view_exceeds_cap(self):
        app = _make_app()
        original = WT_Launcher.LOG_VIEW_MAX_LINES
        try:
            WT_Launcher.LOG_VIEW_MAX_LINES = 3
            for i in range(10):
                app._append_log("第 %d 行" % i)
            self.assertTrue(app.log_text.trim_deletes, "超上限时应触发头部裁剪")
        finally:
            WT_Launcher.LOG_VIEW_MAX_LINES = original


class FilterBarTests(unittest.TestCase):
    """过滤条的读取 / 清除 / 状态文案。"""

    def test_build_filter_maps_level_label(self):
        app = _make_app(level_label="WARN 及以上", keyword=" 失败 ", step=" step_2 ")
        log_filter = app._build_log_filter()
        self.assertEqual(log_filter.min_level, wt_logging.WARN)
        self.assertEqual(log_filter.keyword, "失败")
        self.assertEqual(log_filter.step_id, "step_2")

    def test_build_filter_first_choice_means_no_level_limit(self):
        app = _make_app()
        self.assertIsNone(app._build_log_filter().min_level)

    def test_every_level_choice_is_selectable(self):
        for label, expected in wt_log_query.LEVEL_CHOICES:
            with self.subTest(label=label):
                app = _make_app(level_label=label)
                self.assertEqual(app._build_log_filter().min_level, expected or None)

    def test_apply_filter_rerenders(self):
        app = _make_app(lines=SAMPLE, level_label="ERROR 及以上")
        app._apply_log_filter()
        self.assertEqual(len(app.log_text.lines), 1)
        self.assertIn("定位失败", app.log_text.texts()[0])

    def test_clear_filter_restores_all_lines(self):
        app = _make_app(lines=SAMPLE, level_label="ERROR 及以上", keyword="失败")
        app._apply_log_filter()
        self.assertEqual(len(app.log_text.lines), 1)
        app._clear_log_filter()
        self.assertEqual(len(app.log_text.lines), len(SAMPLE))
        self.assertFalse(app._log_filter.is_active())
        self.assertEqual(app.log_level_var.get(), wt_log_query.LEVEL_CHOICES[0][0])
        self.assertEqual(app.log_keyword_var.get(), "")
        self.assertEqual(app.log_step_var.get(), "")

    def test_status_text_when_unfiltered(self):
        app = _make_app(lines=SAMPLE)
        app._render_log_view()
        status = app.log_filter_status_var.get()
        self.assertIn("未过滤", status)
        self.assertIn("4", status)

    def test_status_text_when_filtered(self):
        app = _make_app(lines=SAMPLE, level_label="ERROR 及以上")
        app._apply_log_filter()
        status = app.log_filter_status_var.get()
        self.assertIn("ERROR 及以上", status)
        self.assertIn("显示 1/4 行", status)

    def test_render_clears_previous_content(self):
        app = _make_app(lines=SAMPLE)
        app._render_log_view()
        app._render_log_view()
        self.assertEqual(app.log_text.cleared, 2)
        self.assertEqual(len(app.log_text.lines), len(SAMPLE), "重渲染不应重复累积")


class StepLinkageTests(unittest.TestCase):
    """运行报告 → 运行日志 联动。"""

    def test_filter_log_by_step_sets_condition_and_renders(self):
        app = _make_app(lines=SAMPLE)
        app._filter_log_by_step("step_2")
        self.assertEqual(app.log_step_var.get(), "step_2")
        self.assertEqual(app._log_filter.step_id, "step_2")
        self.assertEqual(len(app.log_text.lines), 2)

    def test_filter_log_by_step_switches_to_log_tab(self):
        app = _make_app(lines=SAMPLE)
        app._filter_log_by_step("step_1")
        self.assertEqual(app.report_notebook.selected, [app.log_tab])

    def test_empty_step_id_is_noop(self):
        """树刷新清空选中时也会触发回调，此时不应改动筛选状态。"""
        app = _make_app(lines=SAMPLE)
        app._filter_log_by_step("")
        self.assertEqual(app.log_step_var.get(), "")
        self.assertEqual(app.report_notebook.selected, [])
        self.assertFalse(app._log_filter.is_active())

    def test_linkage_combines_with_existing_keyword(self):
        app = _make_app(lines=SAMPLE, keyword="定位")
        app._filter_log_by_step("step_2")
        self.assertEqual(len(app.log_text.lines), 1)
        self.assertIn("定位失败", app.log_text.texts()[0])


class IntegrationWithQueryModuleTests(unittest.TestCase):
    """视图与 wt_log_query 使用同一套过滤语义（避免两处规则分叉）。"""

    def test_view_result_matches_module_apply(self):
        app = _make_app(lines=SAMPLE, level_label="WARN 及以上", step="step_1")
        app._apply_log_filter()
        expected = wt_log_query.LogFilter(
            min_level=wt_logging.WARN, step_id="step_1"
        ).apply(SAMPLE)
        self.assertEqual(app.log_text.texts(), expected)


class LogFilterBarSmokeTests(unittest.TestCase):
    """过滤条的真实构建冒烟。

    本文件唯一依赖 Tk 的一组 —— 控件构建期的选项错误（字体/取值/参数名）
    只有真控件能暴露，替身验证不到。其余测试刻意不建 Tk 根窗口。
    """

    @classmethod
    def setUpClass(cls):
        try:
            import tkinter as tk
        except Exception as exc:
            raise unittest.SkipTest("无 tkinter 可用：%r" % (exc,))
        cls.tk = tk
        try:
            cls.root = tk.Tk()
        except Exception as exc:
            raise unittest.SkipTest("无法创建 Tk 根窗口：%r" % (exc,))
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def _build(self):
        import wt_theme

        app = WT_Launcher.LauncherApp.__new__(WT_Launcher.LauncherApp)
        app.theme = wt_theme.get_palette()
        app._log_all_lines = []
        app._log_filter = wt_log_query.LogFilter()
        app.log_text = None
        app._build_log_filter_bar(self.tk.Frame(self.root))
        return app

    def test_filter_bar_builds_and_reads_back(self):
        app = self._build()
        self.assertEqual(app.log_level_var.get(), wt_log_query.LEVEL_CHOICES[0][0])
        self.assertIsNone(app._build_log_filter().min_level)

        app.log_level_var.set("WARN 及以上")
        app.log_keyword_var.set("失败")
        log_filter = app._build_log_filter()
        self.assertEqual(log_filter.min_level, wt_logging.WARN)
        self.assertEqual(log_filter.keyword, "失败")

    def test_level_combo_lists_every_choice(self):
        app = self._build()
        self.assertEqual(
            list(app.log_level_combo.cget("values")),
            [label for label, _value in wt_log_query.LEVEL_CHOICES],
        )

    def test_clear_filter_resets_widget_variables(self):
        app = self._build()
        app.log_level_var.set("ERROR 及以上")
        app.log_keyword_var.set("x")
        app.log_step_var.set("step_1")
        app._clear_log_filter()
        self.assertEqual(app.log_level_var.get(), wt_log_query.LEVEL_CHOICES[0][0])
        self.assertEqual(app.log_keyword_var.get(), "")
        self.assertEqual(app.log_step_var.get(), "")


if __name__ == "__main__":
    unittest.main()
