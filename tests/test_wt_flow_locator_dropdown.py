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


class DirectOptionClickTests(unittest.TestCase):
    """阶段3：目标控件本身就是下拉选项（targetValue 含 ListBoxItem/ListItem/MenuItem）
    且已可见时，直接点击该选项完成选择，不再依赖 Popup 窗口枚举。"""

    CONTROL = {
        "id": "dropdown_item",
        "name": "日期时间",
        "targetValue": "DateTime,ListBoxItem",
        "inspectData": {"name": "DateTime", "children": ["日期时间 | TextBlock | Text"]},
    }

    def _select(self, visible=True, log_capture=None):
        dropdown_wrapper = MagicMock()
        log_capture = log_capture if log_capture is not None else []
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_GET_STEP_DEFINITION", return_value={"id": "s1"}))
            stack.enter_context(patch.object(wt_flow_locator, "get_flow_control_definition", return_value=self.CONTROL))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_target_texts", return_value=["datetime", "日期时间"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_expected_window_titles", return_value=["导入时间序列文件"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=123))
            stack.enter_context(patch.object(wt_flow_locator, "_try_get_window_by_handle", return_value=MagicMock()))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=0))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value="DateTime"))
            stack.enter_context(patch.object(wt_flow_locator, "_collect_dropdown_windows", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "find_flow_control", return_value=dropdown_wrapper))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_value", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "click_wrapper_center", return_value=(True, {})))
            stack.enter_context(patch.object(wt_flow_locator, "iter_dropdown_runtime_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_raw_view_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_win32_text_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "score_dropdown_runtime_candidate", return_value=90))
            stack.enter_context(patch.object(wt_flow_locator, "click_dropdown_runtime_candidate", return_value=(True, {"method": "click_input"})))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_debug_snapshot", return_value={}))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_class_name", return_value="ListBoxItem"))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value="ListItem"))
            stack.enter_context(patch.object(wt_flow_locator, "_candidate_has_visible_rect", return_value=visible))
            stack.enter_context(patch.object(wt_flow_locator, "normalize_match_text", side_effect=lambda v: str(v).lower()))
            stack.enter_context(patch.object(wt_flow_locator, "is_placeholder_text", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator.time, "sleep", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator, "_LOG_STEP", side_effect=lambda msg, *a, **k: log_capture.append(msg)))
            return wt_flow_locator.select_dropdown_item_runtime("s1", "dropdown_item", timeout_seconds=0.3)

    def test_direct_click_visible_option_succeeds(self):
        ok, meta = self._select(visible=True)
        self.assertTrue(ok)
        self.assertEqual(meta.get("method"), "direct_option_click")
        self.assertEqual(meta.get("valueVerified"), "DateTime")
        self.assertEqual(meta.get("clickMeta", {}).get("method"), "click_input")

    def test_not_visible_option_logs_not_expanded(self):
        logged = []
        ok, meta = self._select(visible=False, log_capture=logged)
        self.assertFalse(ok)
        self.assertTrue(
            any("疑似下拉未展开" in msg for msg in logged),
            "目标选项无可见矩形时必须记录'疑似下拉未展开'诊断日志",
        )


class PopupWrapperMergeTests(unittest.TestCase):
    """防御：find_flow_control 定位到 WPF Popup 窗口时并入枚举窗口列表（防 _collect 漏收）。"""

    def test_popup_wrapper_joined_into_dropdown_windows(self):
        popup_wrapper = MagicMock()
        seen_windows = []
        time_state = {"n": 0}

        def fake_iter(windows=None):
            seen_windows.append(list(windows or []))
            return []

        def fake_time():
            time_state["n"] += 1
            return 0.0 if time_state["n"] <= 8 else 100.0

        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_GET_STEP_DEFINITION", return_value={"id": "s1"}))
            stack.enter_context(patch.object(wt_flow_locator, "get_flow_control_definition", return_value={
                "id": "dd", "name": "日期时间", "targetValue": "DateTime,ListBoxItem",
                "inspectData": {"name": "DateTime", "children": ["日期时间 | TextBlock | Text"]},
            }))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_target_texts", return_value=["datetime"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_expected_window_titles", return_value=["导入时间序列文件"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=123))
            stack.enter_context(patch.object(wt_flow_locator, "_try_get_window_by_handle", return_value=MagicMock()))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=0))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value="DateTime"))
            stack.enter_context(patch.object(wt_flow_locator, "_collect_dropdown_windows", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "find_flow_control", return_value=popup_wrapper))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_value", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_class_name", return_value="Popup"))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value="Window"))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_handle", return_value=999))
            stack.enter_context(patch.object(wt_flow_locator, "_candidate_has_visible_rect", return_value=True))
            stack.enter_context(patch.object(wt_flow_locator, "score_dropdown_runtime_candidate", return_value=-1))
            stack.enter_context(patch.object(wt_flow_locator, "click_dropdown_runtime_candidate", return_value=(False, {})))
            stack.enter_context(patch.object(wt_flow_locator, "iter_dropdown_runtime_candidates", side_effect=fake_iter))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_raw_view_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_win32_text_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "normalize_match_text", side_effect=lambda v: str(v).lower()))
            stack.enter_context(patch.object(wt_flow_locator, "is_placeholder_text", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator.time, "sleep", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator.time, "time", side_effect=fake_time))
            stack.enter_context(patch.object(wt_flow_locator, "_LOG_STEP", side_effect=lambda *a, **k: None))
            ok, _ = wt_flow_locator.select_dropdown_item_runtime("s1", "dd", timeout_seconds=0.2)

        self.assertFalse(ok)
        self.assertTrue(
            any(popup_wrapper in wl for wl in seen_windows),
            "Popup 窗口必须并入枚举窗口列表（否则枚举阶段漏掉展开的下拉选项）",
        )


class RawViewNestedOptionEnumerationTests(unittest.TestCase):
    """Raw View 枚举必须穿过中间容器（ScrollViewer/ListBox）找到嵌套的 ListItem。

    曾因预过滤把非 ListItem 容器剪枝导致其子树选项永远遍历不到（raw探针=0）。
    """

    def _collect(self):
        import pywinauto.controls.uiawrapper
        import pywinauto.uia_defines
        import pywinauto.uia_element_info

        class FakeElement:
            def __init__(self, ctype, cname):
                self._ct = ctype
                self._cn = cname
                self.children = []

            def GetCurrentPropertyValue(self, prop_id):
                if prop_id == 30003:
                    return self._ct
                if prop_id == 30012:
                    return self._cn
                return None

        root = FakeElement(50033, "Window")  # Popup 根
        container = FakeElement(50033, "DropDownScrollViewer")  # 中间容器（非 ListItem）
        item = FakeElement(50007, "ListBoxItem")  # 目标选项
        root.children = [container]
        container.children = [item]

        next_of = {}
        stack = [root]
        while stack:
            el = stack.pop()
            for i, kid in enumerate(el.children):
                next_of[id(kid)] = el.children[i + 1] if i + 1 < len(el.children) else None
                stack.append(kid)

        class FakeWalker:
            def GetFirstChildElement(self, el):
                return el.children[0] if el.children else None

            def GetNextSiblingElement(self, el):
                return next_of.get(id(el))

        fake_window = MagicMock()
        fake_window.element_info.element = root

        with ExitStack() as stack_ctx:
            stack_ctx.enter_context(patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=0))
            stack_ctx.enter_context(patch.object(wt_flow_locator, "is_automation_window", return_value=False))
            stack_ctx.enter_context(patch.object(wt_flow_locator, "get_wrapper_handle", return_value=1))
            stack_ctx.enter_context(patch.object(wt_flow_locator, "is_dropdown_like_wrapper", return_value=True))
            stack_ctx.enter_context(patch.object(
                wt_flow_locator,
                "_dropdown_raw_filter_props",
                return_value={"control_type": 30003, "class_name": 30012},
            ))
            iuia_mock = MagicMock()
            iuia_mock.iuia.RawViewWalker = FakeWalker()
            stack_ctx.enter_context(patch.object(pywinauto.uia_defines, "IUIA", return_value=iuia_mock))
            stack_ctx.enter_context(patch.object(pywinauto.controls.uiawrapper, "UIAWrapper", return_value=MagicMock()))
            stack_ctx.enter_context(patch.object(pywinauto.uia_element_info, "UIAElementInfo", return_value=MagicMock()))
            return list(wt_flow_locator._iter_dropdown_raw_view_candidates([fake_window]))

    def test_nested_option_inside_scrollviewer_is_enumerated(self):
        candidates = self._collect()
        self.assertEqual(len(candidates), 1, "嵌套在容器内的 ListItem 必须被 Raw View 枚举到")


class PointSweepOptionTests(unittest.TestCase):
    """方案A：点扫掠（物理移动鼠标触发实体化 + Desktop.from_point 命中）选中虚拟化下拉选项。"""

    ANCHOR = {"left": 100, "top": 200, "right": 300, "bottom": 250, "width": 200, "height": 50}

    def test_sweep_matches_target_option_text(self):
        import pywinauto
        fake_option = MagicMock()
        desktop_mock = MagicMock()
        desktop_mock.from_point.return_value = MagicMock()
        with patch.object(wt_flow_locator, "_nearest_dropdown_option_wrapper", return_value=fake_option), \
             patch.object(wt_flow_locator, "_extract_dropdown_option_text", return_value="DateTime"), \
             patch.object(pywinauto, "Desktop", return_value=desktop_mock):
            option, text, stats = wt_flow_locator._sweep_dropdown_option_by_point(
                self.ANCHOR, ["datetime"], move_cursor=False
            )
        self.assertEqual(text, "DateTime")
        self.assertIs(option, fake_option)
        self.assertGreater(stats["probes"], 0)
        self.assertGreater(stats["hits"], 0)

    def test_sweep_no_match_returns_empty(self):
        import pywinauto
        desktop_mock = MagicMock()
        desktop_mock.from_point.return_value = None
        with patch.object(pywinauto, "Desktop", return_value=desktop_mock):
            option, text, stats = wt_flow_locator._sweep_dropdown_option_by_point(
                self.ANCHOR, ["datetime"], move_cursor=False
            )
        self.assertIsNone(option)
        self.assertEqual(text, "")

    def test_point_sweep_fallback_succeeds(self):
        dropdown_wrapper = MagicMock()
        candidate = MagicMock()
        time_state = {"n": 0}

        def fake_time():
            time_state["n"] += 1
            return 0.0 if time_state["n"] <= 8 else 100.0

        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_GET_STEP_DEFINITION", return_value={"id": "s1"}))
            stack.enter_context(patch.object(wt_flow_locator, "get_flow_control_definition", return_value={
                "id": "dd", "name": "日期时间", "targetValue": "DateTime,ListBoxItem",
                "inspectData": {"name": "DateTime", "children": ["日期时间 | TextBlock | Text"]},
            }))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_target_texts", return_value=["datetime"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_expected_window_titles", return_value=["导入时间序列文件"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=123))
            stack.enter_context(patch.object(wt_flow_locator, "_try_get_window_by_handle", return_value=MagicMock()))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=0))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value="DateTime"))
            stack.enter_context(patch.object(wt_flow_locator, "_collect_dropdown_windows", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "find_flow_control", return_value=dropdown_wrapper))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_value", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_class_name", return_value="Popup"))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value="Window"))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_handle", return_value=999))
            stack.enter_context(patch.object(wt_flow_locator, "_candidate_has_visible_rect", return_value=True))
            stack.enter_context(patch.object(wt_flow_locator, "score_dropdown_runtime_candidate", return_value=-1))
            stack.enter_context(patch.object(wt_flow_locator, "click_dropdown_runtime_candidate", return_value=(False, {})))
            stack.enter_context(patch.object(wt_flow_locator, "iter_dropdown_runtime_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_raw_view_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_win32_text_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "_get_dropdown_sweep_anchor", return_value=self.ANCHOR))
            stack.enter_context(patch.object(
                wt_flow_locator, "_sweep_dropdown_option_by_point",
                return_value=(candidate, "DateTime", {"probes": 5, "hits": 1}),
            ))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_debug_snapshot", return_value={}))
            stack.enter_context(patch.object(wt_flow_locator, "normalize_match_text", side_effect=lambda v: str(v).lower()))
            stack.enter_context(patch.object(wt_flow_locator, "is_placeholder_text", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator.time, "sleep", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator.time, "time", side_effect=fake_time))
            stack.enter_context(patch.object(wt_flow_locator, "_LOG_STEP", side_effect=lambda *a, **k: None))
            ok, meta = wt_flow_locator.select_dropdown_item_runtime("s1", "dd", timeout_seconds=0.2)

        self.assertTrue(ok)
        self.assertEqual(meta.get("method"), "point_sweep")
        self.assertEqual(meta.get("valueVerified"), "DateTime")


class DropdownCandidateVerificationTests(unittest.TestCase):
    """候选点击后能读显示值时必须校验等于目标，读不到才保留点击证据。"""

    def _select(self, display_value):
        dropdown_wrapper = MagicMock()
        candidate = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_GET_STEP_DEFINITION", return_value={"id": "s1"}))
            stack.enter_context(patch.object(wt_flow_locator, "get_flow_control_definition", return_value={"id": "dd", "name": "Dropdown", "inspectData": {}}))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_target_texts", return_value=["Target"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_expected_window_titles", return_value=["Window"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=123))
            stack.enter_context(patch.object(wt_flow_locator, "_try_get_window_by_handle", return_value=MagicMock()))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=0))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "_collect_dropdown_windows", return_value=[MagicMock()]))
            stack.enter_context(patch.object(wt_flow_locator, "find_flow_control", return_value=dropdown_wrapper))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_value", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value="1"))
            stack.enter_context(patch.object(wt_flow_locator, "click_wrapper_center", return_value=(True, {})))
            stack.enter_context(patch.object(wt_flow_locator, "iter_dropdown_runtime_candidates", return_value=[candidate]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_raw_view_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_win32_text_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "score_dropdown_runtime_candidate", return_value=90))
            stack.enter_context(patch.object(wt_flow_locator, "click_dropdown_runtime_candidate", return_value=(True, {"method": "click_input"})))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_debug_snapshot", return_value={}))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_class_name", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_rectangle", return_value={}))
            stack.enter_context(patch.object(wt_flow_locator, "_read_dropdown_display_text", return_value=display_value))
            stack.enter_context(patch.object(wt_flow_locator, "normalize_match_text", side_effect=lambda value: str(value).lower()))
            stack.enter_context(patch.object(wt_flow_locator, "is_placeholder_text", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator, "send_keys", side_effect=lambda *args, **kwargs: None))
            stack.enter_context(patch.object(wt_flow_locator.time, "sleep", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator, "_LOG_STEP", side_effect=lambda *args, **kwargs: None))
            return wt_flow_locator.select_dropdown_item_runtime(
                "s1",
                "dd",
                timeout_seconds=0.3,
                window_title_hint="",
                target_option="Target",
            )

    def test_candidate_click_with_matching_display_value_succeeds(self):
        ok, meta = self._select("Target")
        self.assertTrue(ok)
        self.assertEqual(meta.get("valueVerified"), "Target")

    def test_candidate_click_with_mismatched_display_value_fails(self):
        ok, meta = self._select("Wrong")
        self.assertFalse(ok)
        self.assertNotIn("valueVerified", meta)


class DismissExpandedDropdownTests(unittest.TestCase):
    """下拉失败止血：实时复查确认 MUP Popup 仍在才发全局 ESC（枚举快照不可作发送依据）。

    枚举阶段快照与失败返回之间可能隔数十秒，候选点击/键盘 ENTER 可能已把弹层关掉；
    此时全局 ESC 会落到前台焦点窗口（MUP 主窗口或用户其他应用）。
    弹层识别走"win32 枚举收窄 + 逐句柄 UIA 类名/控件类型"（MUP 窗口 win32 注册类是
    HwndWrapper[...]，不含 "popup"，win32 类名不可作识别依据）。
    """

    @staticmethod
    def _win_info(hwnd, pid="4321", win_class="HwndWrapper[MUPSmartClient.exe;;{guid}]"):
        return {
            "hwnd": hwnd,
            "title": "",
            "className": win_class,
            "processId": pid,
            "processName": "MUPSmartClient",
        }

    class _FakeWin:
        def __init__(self, ui_class, ui_type):
            self._ui_class = ui_class
            self._ui_type = ui_type

    def _run(self, live_windows, fg_pid, expected_pids, window_classes):
        with ExitStack() as stack:
            stack.enter_context(
                patch.object(wt_flow_locator, "_enum_visible_mup_win32_windows", return_value=live_windows)
            )
            stack.enter_context(patch.object(
                wt_flow_locator, "_try_get_window_by_handle",
                side_effect=lambda handle: self._FakeWin(*window_classes[str(handle)])
                if str(handle) in window_classes else None,
            ))
            stack.enter_context(patch.object(
                wt_flow_locator, "get_wrapper_class_name",
                side_effect=lambda win: getattr(win, "_ui_class", ""),
            ))
            stack.enter_context(patch.object(
                wt_flow_locator, "get_wrapper_control_type",
                side_effect=lambda win: getattr(win, "_ui_type", ""),
            ))
            stack.enter_context(patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=999001))
            stack.enter_context(patch.object(wt_flow_locator, "_get_process_id_from_handle", return_value=fg_pid))
            stack.enter_context(patch.object(wt_flow_locator.time, "sleep", return_value=None))
            send_mock = stack.enter_context(patch.object(wt_flow_locator, "send_keys"))
            log_mock = stack.enter_context(patch.object(wt_flow_locator, "_LOG_STEP"))
            wt_flow_locator._dismiss_expanded_dropdown(
                step_id="s1", control_id="c1", expected_process_ids=expected_pids
            )
            return send_mock, log_mock

    def test_sends_esc_when_live_popup_present(self):
        send_mock, log_mock = self._run(
            live_windows=[self._win_info("0x1a2b")],
            fg_pid=4321,
            expected_pids={"4321"},
            window_classes={"0x1a2b": ("Popup", "Window")},
        )
        send_mock.assert_called_once_with("{ESC}")
        logged = " ".join(str(call) for call in log_mock.call_args_list)
        self.assertIn("step=s1", logged)
        self.assertIn("c1", logged)

    def test_popup_detected_by_control_type_fallback(self):
        # 类名异常时控件类型含 "popup" 仍可识别（与旧实现的双信号语义一致）
        send_mock, _ = self._run(
            live_windows=[self._win_info("0x1a2b")],
            fg_pid=4321,
            expected_pids={"4321"},
            window_classes={"0x1a2b": ("HwndWrapper[MUPSmartClient.exe;;{g}]", "popup")},
        )
        send_mock.assert_called_once_with("{ESC}")

    def test_skips_esc_when_live_popup_gone(self):
        # 陈旧快照曾有弹层，但实时枚举只剩主窗口（win32 注册类 HwndWrapper）→ 不得发全局 ESC
        send_mock, _ = self._run(
            live_windows=[self._win_info("0x1")],
            fg_pid=4321,
            expected_pids={"4321"},
            window_classes={"0x1": ("HwndWrapper[MUPSmartClient.exe;;{g}]", "Window")},
        )
        send_mock.assert_not_called()

    def test_skips_esc_when_popup_belongs_to_foreign_process(self):
        send_mock, _ = self._run(
            live_windows=[self._win_info("0x1a2b", pid="8888")],
            fg_pid=4321,
            expected_pids={"4321"},
            window_classes={"0x1a2b": ("Popup", "Window")},
        )
        send_mock.assert_not_called()

    def test_skips_esc_when_foreground_is_foreign_app(self):
        # 前台确定是别的进程且目标进程 id 已知 → 不把 ESC 打进用户正在操作的应用
        send_mock, _ = self._run(
            live_windows=[self._win_info("0x1a2b")],
            fg_pid=7777,
            expected_pids={"4321"},
            window_classes={"0x1a2b": ("Popup", "Window")},
        )
        send_mock.assert_not_called()

    def test_sends_esc_when_foreground_undeterminable(self):
        # 实测事故中前台读取返回空：以实时弹层为准，不得因前台未知而放弃止血
        send_mock, _ = self._run(
            live_windows=[self._win_info("0x1a2b")],
            fg_pid=0,
            expected_pids={"4321"},
            window_classes={"0x1a2b": ("Popup", "Window")},
        )
        send_mock.assert_called_once_with("{ESC}")


if __name__ == "__main__":
    unittest.main()