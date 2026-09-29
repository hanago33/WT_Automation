# encoding: utf-8
"""未审计模块审计（session-17）修复轮的回归测试（2026-09-29）。

覆盖审计报告「建议修复顺序 1-2」的 7 项：

1. P0-1a recordings/ 排除出检测器匹配池（load_all_other_libraries，walk 剪枝）；
2. P0-1b 控件库加载后台化（_load_library 异步、加载中防重入、失败提示）；
3. P0-2 CSV 转换同名输出覆盖确认（首次冲突一次性询问，之后沿用）；
4. P1-1 自动合并/检验定位的 except-as 变量逃逸修复（静态 + 复位兜底行为）；
5. P1-2 清空结果前停悬停（顺序断言）+ _finish_supplement 类型守卫（行为）；
6. P1-6 关窗导出三选：导出未完成不关窗（不静默丢数据）；
7. P1-10 launcher 提权关键词注入（AST：global 声明 + 赋值存在）。

被测对象为真实方法（轻量替身），不实例化窗口、无需 Tk 交互环境。
"""
import ast
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import build_control_map_library as B
import control_live_detector as D
import text_tools as T


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeLabel:
    def __init__(self):
        self.texts = []

    def config(self, **kwargs):
        if "text" in kwargs:
            self.texts.append(kwargs["text"])


class FakeButton:
    def __init__(self):
        self.states = []

    def config(self, **kwargs):
        if "state" in kwargs:
            self.states.append(kwargs["state"])


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def after(self, _delay, callback=None):
        if callback is not None:
            callback()
        return "after#1"

    def destroy(self):
        self.destroyed = True


class FakeThread:
    def __init__(self, target=None, daemon=None, **_kwargs):
        self.target = target

    def start(self):
        if self.target:
            self.target()


def _read_source(*parts):
    path = os.path.join(PROJECT_DIR, *parts)
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def _find_class_method(tree, class_name, method_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    return None


# ── 1. P0-1a：recordings 排除 ────────────────────────────────────────────


class RecordingsExclusionTests(unittest.TestCase):
    def test_recordings_excluded_but_library_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib_dir = os.path.join(tmp, "control_maps", "library")
            rec_dir = os.path.join(tmp, "control_maps", "recordings")
            os.makedirs(lib_dir)
            os.makedirs(rec_dir)
            with io.open(os.path.join(lib_dir, "a.json"), "w", encoding="utf-8") as handle:
                json.dump({"flatControls": [{"name": "ctrl-a"}]}, handle)
            with io.open(os.path.join(rec_dir, "big.json"), "w", encoding="utf-8") as handle:
                json.dump({"flatControls": [{"name": "rec-ctrl"}]}, handle)
            controls, _sources = D.load_all_other_libraries(base_dir=tmp)
        names = {ctrl.get("name") for ctrl in controls}
        self.assertEqual(names, {"ctrl-a"})

    def test_standard_master_file_still_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib_dir = os.path.join(tmp, "control_maps", "library")
            os.makedirs(lib_dir)
            with io.open(os.path.join(lib_dir, "总控件信息.json"), "w", encoding="utf-8") as handle:
                json.dump({"flatControls": [{"name": "master"}]}, handle)
            controls, _sources = D.load_all_other_libraries(base_dir=tmp)
        self.assertEqual(controls, [])

    def test_missing_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            controls, sources = D.load_all_other_libraries(base_dir=tmp)
        self.assertEqual(controls, [])
        self.assertEqual(sources, [])


# ── 2. P0-1b：加载后台化 ─────────────────────────────────────────────────


class LibraryLoadAsyncTests(unittest.TestCase):
    def _make(self):
        win = object.__new__(D.ControlLiveDetectorWindow)
        win.status_label = FakeLabel()
        win.lib_count_label = FakeLabel()
        win.refresh_btn = FakeButton()
        win.window = FakeWindow()
        win._library_loading = False
        win.master_controls = []
        win.other_controls = []
        return win

    def test_load_fills_results_and_resets_flag(self):
        win = self._make()
        with patch.object(D, "load_master_library", return_value=([{"name": "m"}], "")), \
                patch.object(D, "load_all_other_libraries", return_value=([{"name": "o"}], [])), \
                patch.object(D.threading, "Thread", FakeThread):
            win._load_library()
        self.assertEqual(win.master_controls, [{"name": "m"}])
        self.assertEqual(win.other_controls, [{"name": "o"}])
        self.assertFalse(win._library_loading)
        self.assertIn("normal", win.refresh_btn.states)  # 按钮恢复可用

    def test_reentrant_load_is_ignored(self):
        win = self._make()
        win._library_loading = True
        with patch.object(D.threading, "Thread") as thread_cls:
            win._load_library()
        thread_cls.assert_not_called()

    def test_load_failure_marks_status(self):
        win = self._make()

        def _boom():
            raise RuntimeError("boom")

        with patch.object(D, "load_master_library", _boom), \
                patch.object(D.threading, "Thread", FakeThread):
            win._load_library()
        self.assertFalse(win._library_loading)
        self.assertTrue(any("失败" in text for text in win.status_label.texts))


class StartMonitoringDuringLoadTests(unittest.TestCase):
    def test_start_monitoring_blocked_while_loading(self):
        win = object.__new__(D.ControlLiveDetectorWindow)
        win.monitoring = False
        win._library_loading = True
        win.window = None
        with patch.object(D.messagebox, "showinfo") as info:
            win.start_monitoring()
        info.assert_called_once()


# ── 3. P0-2：覆盖确认 ────────────────────────────────────────────────────


class OverwriteConfirmTests(unittest.TestCase):
    def _make_card(self):
        card = object.__new__(T.CsvConvertCard)
        card._overwrite_policy = None
        return card

    def test_no_conflict_allows_without_dialog(self):
        card = self._make_card()
        with patch.object(T.os.path, "exists", return_value=False), \
                patch.object(T.messagebox, "askyesno") as ask:
            self.assertTrue(card._confirm_overwrite("out.xlsx"))
        ask.assert_not_called()

    def test_first_conflict_asks_once_and_remembers_skip(self):
        card = self._make_card()
        with patch.object(T.os.path, "exists", return_value=True), \
                patch.object(T.messagebox, "askyesno", return_value=False) as ask:
            self.assertFalse(card._confirm_overwrite("a.xlsx"))
            self.assertFalse(card._confirm_overwrite("b.xlsx"))
        ask.assert_called_once()
        self.assertFalse(card._overwrite_policy)

    def test_conflict_yes_remembers_overwrite(self):
        card = self._make_card()
        with patch.object(T.os.path, "exists", return_value=True), \
                patch.object(T.messagebox, "askyesno", return_value=True) as ask:
            self.assertTrue(card._confirm_overwrite("a.xlsx"))
            self.assertTrue(card._confirm_overwrite("b.xlsx"))
        ask.assert_called_once()
        self.assertTrue(card._overwrite_policy)

    def test_run_resets_policy_each_time(self):
        src = _read_source("text_tools.py")
        self.assertIn("self._overwrite_policy = None  # 本次转换的覆盖策略在首次冲突时决定", src)


# ── 4. P1-1：except 变量逃逸修复 ─────────────────────────────────────────


class ExceptVarEscapeStaticTests(unittest.TestCase):
    def test_deferred_callbacks_no_longer_reference_except_var(self):
        src = _read_source("build_control_map_library.py")
        # 旧模式（exc 直接进延迟回调）必须消失
        self.assertNotIn("lambda: self._on_auto_merge_done(None, exc", src)
        self.assertNotIn("seq, str(exc)))", src)
        # 新模式（先落普通字符串、默认参数捕获）必须存在
        self.assertIn("lambda t=err_text: self._on_auto_merge_done(None, t, backup_path)", src)
        self.assertIn("lambda t=err_text: self._handle_probe_result(output_path, -1, start, seq, t)", src)


class AutoMergeDoneResetTests(unittest.TestCase):
    def _make(self):
        win = object.__new__(B.ControlMapBuilderApp)
        win._auto_merge_running = True
        win.var_status = FakeVar()
        return win

    def test_reset_on_error_branch(self):
        win = self._make()
        win._on_auto_merge_done(None, "boom", "")
        self.assertFalse(win._auto_merge_running)
        self.assertIn("失败", win.var_status.get())

    def test_reset_even_if_formatting_raises(self):
        win = self._make()

        class _BadStats(dict):
            def get(self, key, default=None):
                raise RuntimeError("bad stats")

        # 异常会继续向 Tk 回调层传播（由 Tk 打印 traceback），但 finally 必须已复位标志
        with self.assertRaises(RuntimeError):
            win._on_auto_merge_done(_BadStats(), None, "")
        self.assertFalse(win._auto_merge_running)  # finally 兜底复位


# ── 5. P1-2：清空停悬停 + 补采收尾守卫 ──────────────────────────────────


class HoverClearOrderTests(unittest.TestCase):
    def test_clear_stops_hover_before_payload_reset(self):
        src = _read_source("build_control_map_library.py")
        tree = ast.parse(src)
        func = _find_class_method(tree, "ControlMapBuilderApp", "cmd_clear_current_payload")
        self.assertIsNotNone(func)
        segment = ast.get_source_segment(src, func)
        self.assertIn("_stop_hover_supplement", segment)
        self.assertLess(
            segment.index("_stop_hover_supplement"),
            segment.index("self.current_payload = None"),
            "必须先把悬停跟踪停掉，再置空 payload",
        )


class FinishSupplementGuardTests(unittest.TestCase):
    def test_discards_when_payload_already_cleared(self):
        win = object.__new__(B.ControlMapBuilderApp)
        win.current_payload = None
        win.var_status = FakeVar("")
        with patch.object(B.messagebox, "showwarning") as warn:
            win._finish_supplement([{"name": "x"}], {}, None)
        warn.assert_not_called()
        self.assertEqual(win.var_status.get(), "")  # 安静丢弃，不触碰任何状态


# ── 6. P1-6：关窗导出三选 ────────────────────────────────────────────────


class ExportOnCloseTests(unittest.TestCase):
    def _make(self):
        win = object.__new__(D.ControlLiveDetectorWindow)
        win.window = FakeWindow()
        win.saved_controls = [{"a": 1}]
        win.stop_monitoring = lambda: None
        return win

    def test_cancel_keeps_window(self):
        win = self._make()
        with patch.object(D.messagebox, "askyesnocancel", return_value=None):
            win.on_close()
        self.assertFalse(win.window.destroyed)

    def test_no_export_destroys_directly(self):
        win = self._make()
        with patch.object(D.messagebox, "askyesnocancel", return_value=False):
            win.on_close()
        self.assertTrue(win.window.destroyed)

    def test_yes_but_export_cancelled_keeps_window(self):
        win = self._make()
        win.export_saved_controls = lambda: False
        with patch.object(D.messagebox, "askyesnocancel", return_value=True):
            win.on_close()
        self.assertFalse(win.window.destroyed)

    def test_yes_and_export_ok_destroys(self):
        win = self._make()
        win.export_saved_controls = lambda: True
        with patch.object(D.messagebox, "askyesnocancel", return_value=True):
            win.on_close()
        self.assertTrue(win.window.destroyed)

    def test_no_saved_controls_destroys_directly(self):
        win = self._make()
        win.saved_controls = []
        win.on_close()
        self.assertTrue(win.window.destroyed)


# ── 7. P1-10：launcher 提权关键词注入 ────────────────────────────────────


class LauncherKeywordInjectionTests(unittest.TestCase):
    def test_run_declares_global_and_assigns_keyword(self):
        src = _read_source("tools", "generic_flow", "generic_launcher.py")
        tree = ast.parse(src)
        run = _find_class_method(tree, "GenericLauncherUI", "_run")
        self.assertIsNotNone(run)
        has_global = any(
            isinstance(node, ast.Global) and "_TARGET_PROCESS_KEYWORD" in node.names
            for node in ast.walk(run)
        )
        assigns = [
            node
            for node in ast.walk(run)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_TARGET_PROCESS_KEYWORD"
                for target in node.targets
            )
        ]
        self.assertTrue(has_global, "_run 需声明 global _TARGET_PROCESS_KEYWORD")
        self.assertTrue(assigns, "_run 需给 _TARGET_PROCESS_KEYWORD 赋值（否则提权检测恒为死代码）")


if __name__ == "__main__":
    unittest.main()
