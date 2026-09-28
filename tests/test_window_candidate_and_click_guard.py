# encoding: utf-8
"""回归测试：候选窗口过滤（P0）+ 0x0 矩形点击守卫（P1）。

P0 背景：MUP 主窗口标题为空，而 Windows Terminal 窗口标题常被设为
"Meteodyn Universe / v1.10.1.0"（含关键词），会命中主窗标题正则被误纳入候选。
定位器对终端窗口（CASCADIA_HOSTING_WINDOW_CLASS / XAML Island）做 UIA 遍历会
导致气象弹窗定位失败，并在 descendants 上触发 0x80040155 原生崩溃终止流程。
修复：① find_main_windows 支持按类名排除；② _get_main_window_candidates 仅保留
进程名确属 MUPSmartClient 的窗口；③ 定位器候选包装处二次剔除终端类窗口。

P1 背景：控件矩形 0x0（未渲染）时物理点击落到屏幕 (0,0) 却报 success（实测
step_28 保存后 HPC 按钮）。修复：0x0 时优先 UIA Invoke，不支持则判未就绪失败。
"""
import os
import re
import sys
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator
import wt_window_helpers
import WT_AUT_recorded

TERMINAL_CLASS = "CASCADIA_HOSTING_WINDOW_CLASS"


class FindMainWindowsExcludeTests(unittest.TestCase):
    """find_main_windows 的类名排除：终端窗口即使标题命中也不得纳入候选。"""

    # {hwnd: (title, class_name, process_name)}
    WINDOWS = {
        1001: ("", "Window", "MUPSmartClient.exe"),                        # MUP 真主窗（标题空）
        1002: ("Meteodyn Universe / v1.10.1.0", TERMINAL_CLASS, "WindowsTerminal.exe"),  # 终端
    }

    def _find(self, exclude=()):
        windows = self.WINDOWS

        class FakeUser32:
            def IsWindowVisible(self, hwnd):
                return True

            def IsIconic(self, hwnd):
                return False

            def GetClassNameW(self, hwnd, buf, size):
                cls = windows.get(hwnd, ("", "", ""))[1]
                buf.value = cls
                return bool(cls)

            def EnumWindows(self, callback, lparam):
                for hwnd in windows:
                    callback(hwnd, 0)
                return True

        with ExitStack() as stack:
            stack.enter_context(patch.object(
                wt_window_helpers, "_get_window_owning_process_name",
                side_effect=lambda hwnd: windows.get(hwnd, ("", "", ""))[2]))
            wt_window_helpers.configure_wt_window_helpers(
                user32=FakeUser32(),
                enum_windows_proc=lambda func: func,
                get_window_text=lambda hwnd: windows.get(hwnd, ("", "", ""))[0],
                get_window_rect=lambda hwnd: type("R", (), {
                    "left": 0, "top": 0, "right": 2560, "bottom": 1516})(),
                log_step=lambda message: None,
            )
            return wt_window_helpers.find_main_windows(
                re.compile("Meteodyn Universe"),
                class_name_keywords=("MUPSmartClient",),
                exclude_class_keywords=exclude,
            )

    def test_terminal_window_with_matching_title_is_excluded(self):
        windows = self._find(exclude=(TERMINAL_CLASS,))
        handles = {w["hwnd"] for w in windows}
        self.assertIn(1001, handles, "MUP 主窗（进程名匹配）必须保留")
        self.assertNotIn(1002, handles, "终端窗口标题命中正则仍必须被类名排除")

    def test_without_exclude_terminal_is_included_control(self):
        # 对照：不传 exclude 时终端窗口仍会被标题匹配纳入（证明排除参数是生效手段）
        windows = self._find(exclude=())
        handles = {w["hwnd"] for w in windows}
        self.assertIn(1002, handles)

    def test_process_name_field_returned(self):
        windows = self._find(exclude=(TERMINAL_CLASS,))
        self.assertEqual(windows[0]["processName"], "MUPSmartClient.exe")


class MainWindowCandidatesFilterTests(unittest.TestCase):
    """_get_main_window_candidates：只保留 MUPSmartClient 进程窗口，且传类名排除参数。"""

    def _call(self, main_windows):
        captured = {}

        def fake_find(title_re, class_name_keywords=(), exclude_class_keywords=()):
            captured["class_keywords"] = tuple(class_name_keywords)
            captured["exclude"] = tuple(exclude_class_keywords)
            return list(main_windows)

        fake_helper = SimpleNamespace(find_main_windows=fake_find)
        with patch.object(WT_AUT_recorded, "_get_wt_window_helpers", return_value=fake_helper), \
             patch.object(WT_AUT_recorded, "log_step", lambda message: None):
            result = WT_AUT_recorded._get_main_window_candidates()
        return result, captured

    def test_only_mupsmartclient_process_window_kept(self):
        result, captured = self._call([
            {"hwnd": 1001, "processName": "MUPSmartClient.exe"},
            {"hwnd": 1002, "processName": "WindowsTerminal.exe"},
        ])
        self.assertEqual([w["hwnd"] for w in result], [1001])
        self.assertIn(TERMINAL_CLASS, captured["exclude"], "必须向 find_main_windows 传类名排除")

    def test_fallback_when_process_name_unreadable(self):
        # UIPI 场景下进程名读不到时不得把主窗过滤没：过滤后为空需回退原列表
        result, _ = self._call([{"hwnd": 1001, "processName": ""}])
        self.assertEqual([w["hwnd"] for w in result], [1001])


class ProgrammaticInvokeGuardTests(unittest.TestCase):
    """_try_programmatic_invoke：0x0 矩形点击前的程序化点击兜底。"""

    def test_invoke_method_used_when_present(self):
        control = MagicMock()
        self.assertTrue(wt_flow_locator._try_programmatic_invoke(control))
        control.invoke.assert_called_once()

    def test_patterns_invoke_fallback(self):
        control = MagicMock(spec=[])  # 无 invoke 属性 → 走 patterns.Invoke
        pattern_holder = MagicMock()
        control.patterns = pattern_holder
        self.assertTrue(wt_flow_locator._try_programmatic_invoke(control))
        pattern_holder.Invoke.Invoke.assert_called_once()

    def test_returns_false_when_invoke_unavailable(self):
        control = MagicMock(spec=[])  # 访问 patterns 抛 AttributeError
        self.assertFalse(wt_flow_locator._try_programmatic_invoke(control))

    def test_returns_false_for_none(self):
        self.assertFalse(wt_flow_locator._try_programmatic_invoke(None))


class TerminalLikeWindowTests(unittest.TestCase):
    """定位器第二道防线：_is_terminal_like_window 判定精确性。"""

    def test_terminal_class_detected(self):
        with patch.object(wt_flow_locator, "get_wrapper_class_name",
                          return_value="CASCADIA_HOSTING_WINDOW_CLASS"):
            self.assertTrue(wt_flow_locator._is_terminal_like_window(MagicMock()))

    def test_mup_window_class_not_flagged(self):
        with patch.object(wt_flow_locator, "get_wrapper_class_name", return_value="Window"):
            self.assertFalse(wt_flow_locator._is_terminal_like_window(MagicMock()))

    def test_class_name_exception_returns_false(self):
        with patch.object(wt_flow_locator, "get_wrapper_class_name",
                          side_effect=RuntimeError("boom")):
            self.assertFalse(wt_flow_locator._is_terminal_like_window(MagicMock()))


if __name__ == "__main__":
    unittest.main()
