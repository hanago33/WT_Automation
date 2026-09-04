# encoding: utf-8
"""运行日志优化项回归测试。

覆盖：
- P0-2  wait_for_flow_control_condition 值校验轮询复用已定位 wrapper
       （不再每轮重复整树 FindAll，实测 12s+/轮）
- P1-1  _scroll_flow_control_into_view 贴底余量：贴底控件触发一次定向滚动，
       滚轮兜底仅对真正离屏控件启用
- P2-1a _eval_precondition_skip 新增 wait_visible 前置等待（只等不跳）
"""
import os
import sys
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator
import wt_flow_executor

WRA_TILE_DEF_JSON = os.path.join(PROJECT_DIR, "flow_packages", "flow_definition_发送综合计算.json")


class WraTileMatchingTests(unittest.TestCase):
    """step_copy_select 综合瓦片定位（2026-08-25 wait_visible 诊断实据结构）。

    诊断输出：ListItem name='MBA.WRA.ViewModel.Core.MBAWRAAnalysisSummaryViewModel'
    class=ListBoxItem aid=WRAResults_ListItem_WRAResultTile，'综合1' 在子 Text
    (aid=WRAComputation_Text_Header) 上 —— 名称在子元素、类型在父元素，旧定义
    'name,control_type → 综合1,ListItem' 的 fast 复合查询永远 0 命中。
    """

    class FakeTile:
        """按诊断结构构造的瓦片替身。"""

        def __init__(self, header_text="综合1"):
            self._name = "MBA.WRA.ViewModel.Core.MBAWRAAnalysisSummaryViewModel"
            self._header = header_text
            self.element_info = SimpleNamespace(
                control_type="ListItem",
                localized_control_type="list item",
                automation_id="WRAResults_ListItem_WRAResultTile",
                framework_id="WPF",
                help_text="",
                process_id="26332",
            )

        def window_text(self):
            return self._name

        def class_name(self):
            return "ListBoxItem"

        def texts(self):
            return []

        def children(self):
            return [self._mk_text(self._header), self._mk_text(""), self._mk_text("6"), self._mk_text("风机")]

        def _mk_text(self, text):
            return SimpleNamespace(
                window_text=lambda: text,
                class_name=lambda: "TextBlock",
                element_info=SimpleNamespace(
                    control_type="Text",
                    localized_control_type="",
                    automation_id=("WRAComputation_Text_Header" if text == self._header else ""),
                    framework_id="WPF",
                ),
            )

    def _load_def(self, step_id):
        with open(WRA_TILE_DEF_JSON, encoding="utf-8") as f:
            data = json_load(f)
        for s in data["steps"]:
            if s["id"] == step_id:
                return s["controls"][0]
        raise AssertionError("step not found: %s" % step_id)

    def _old_def(self):
        return {
            "id": "ctrl_wra_synthesis1_item",
            "name": "综合1",
            "targetMethod": "name,control_type",
            "targetValue": "综合1,ListItem",
            "windowTitle": "*",
            "auxChecks": [],
            "inspectData": {},
        }

    def _label_stub(self, wrapper, label_text, allow_full_scan=True):
        expected = str(label_text or "").strip()
        if not expected:
            return False
        own = getattr(wrapper, "_name", None) or ""
        if expected in own:
            return True
        try:
            for child in wrapper.children():
                if expected in (child.window_text() or ""):
                    return True
        except Exception:
            pass
        return False

    def test_new_def_matches_tile_diagnostic_structure(self):
        new_def = self._load_def("step_copy_select")
        tile = self.FakeTile()
        with patch.object(wt_flow_locator, "wrapper_matches_label_text", side_effect=self._label_stub):
            self.assertTrue(
                wt_flow_locator.wrapper_matches_control_definition(tile, new_def),
                "新定义必须命中诊断输出的瓦片结构",
            )

    def test_new_def_uses_automation_id_fast_path(self):
        new_def = self._load_def("step_copy_select")
        queries = wt_flow_locator.build_fast_locator_queries(new_def)
        self.assertEqual(queries[0].get("automation_id"), "WRAResults_ListItem_WRAResultTile")
        self.assertIn("label_text", str(new_def.get("targetMethod", "")))

    def test_old_def_composite_query_cannot_return_candidates(self):
        old_def = self._old_def()
        tile = self.FakeTile()
        header_text = tile.children()[0]
        # 旧定义 fast 复合查询（name + control_type 同元素）：
        #  - 子 Text（name=综合1, type=Text）：类型不符 → 查询不返回；
        #  - 瓦片（type=ListItem, name=ViewModel）：pywinauto 的 title+control_type
        #    条件按其自身 Name 匹配合集，Name≠综合1 → 查询同样不返回该元素。
        #    谓词层虽因"子文本回退"能匹配瓦片，但候选从未被枚举到（生产实测
        #    3 次运行 20s+ 轮询全落空），故产品失败在枚举层而非评分层。
        self.assertFalse(wt_flow_locator.wrapper_matches_locator(header_text, "name,control_type", "综合1,ListItem"))
        # 瓦片在谓词层可匹配（子文本回退），证明修复必须来自"枚举路径"——新定义的
        # automation_id 原生 FindAll 让瓦片成为候选，再由 label_text 硬消歧选中。
        self.assertTrue(wt_flow_locator.wrapper_matches_locator(tile, "name", "综合1"))


class AnchorScrollIntoViewTests(unittest.TestCase):
    """锚点相对点击：离屏锚点先滚入视口，滚动无效则显式失败（复制链 step_7）。"""

    WIN = (0, 0, 2560, 1516)

    class FakeRect:
        def __init__(self, left, top, right, bottom):
            self.left, self.top, self.right, self.bottom = left, top, right, bottom

    class FakeAnchor:
        def __init__(self, rect):
            self._rect = rect
            self.element_info = SimpleNamespace(handle=12345)

        def rectangle(self):
            return self._rect

        def top_level_parent(self):
            return SimpleNamespace(rectangle=lambda: AnchorScrollIntoViewTests.FakeRect(*AnchorScrollIntoViewTests.WIN))

    def _run_anchor(self, initial_rect, scroll_ok=True):
        anchor = self.FakeAnchor(initial_rect)

        def fake_find(*a, **k):
            return anchor

        scroll_calls = []

        def fake_scroll(wrapper, step_id="", control_id="", force_top=False):
            scroll_calls.append(control_id)
            if scroll_ok:
                wrapper._rect = self.FakeRect(254, 800, 818, 840)  # 滚入视口

        with patch.object(wt_flow_locator, "find_flow_control", side_effect=fake_find), \
             patch.object(wt_flow_locator, "_activate_process_main_window", return_value=0), \
             patch.object(wt_flow_locator, "_get_top_level_hwnd_safe", return_value=0), \
             patch.object(wt_flow_locator, "_scroll_flow_control_into_view", side_effect=fake_scroll), \
             patch("pyautogui.click") as click_mock:
            ok, meta = wt_flow_locator.click_relative_anchor(
                "step_7", "control_map_2277", offset=(0, 0), timeout_seconds=2.0, anchor_align="right"
            )
        return ok, meta, scroll_calls, click_mock

    def test_offscreen_anchor_scrolled_then_clicked(self):
        # 离屏上方（复制链编辑器停在底部，控件 y=-901 场景）→ 滚入视口后正常点击
        ok, meta, scroll_calls, click_mock = self._run_anchor(
            self.FakeRect(254, -901, 818, -859), scroll_ok=True
        )
        self.assertTrue(ok)
        self.assertEqual(len(scroll_calls), 1)
        click_mock.assert_called_once()

    def test_offscreen_anchor_scroll_noop_fails_loudly(self):
        # 滚动无效仍离屏 → 显式失败，绝不盲点屏幕外坐标
        ok, meta, scroll_calls, click_mock = self._run_anchor(
            self.FakeRect(254, -901, 818, -859), scroll_ok=False
        )
        self.assertFalse(ok)
        self.assertEqual(meta.get("reason"), "anchor_offscreen_after_scroll")
        click_mock.assert_not_called()

    def test_visible_anchor_no_scroll(self):
        ok, meta, scroll_calls, click_mock = self._run_anchor(self.FakeRect(254, 700, 818, 740))
        self.assertTrue(ok)
        self.assertEqual(scroll_calls, [])
        click_mock.assert_called_once()


class WindowResponsivenessProbeTests(unittest.TestCase):
    """窗口响应探测：应用忙时跳过无响应窗口，避免 UIA 查询阻塞数分钟。"""

    def test_responsive_window_passes(self):
        with patch.object(wt_flow_locator.ctypes.windll.user32, "SendMessageTimeoutW", return_value=1):
            self.assertTrue(wt_flow_locator._window_is_responsive(12345))

    def test_unresponsive_window_rejected(self):
        with patch.object(wt_flow_locator.ctypes.windll.user32, "SendMessageTimeoutW", return_value=0):
            self.assertFalse(wt_flow_locator._window_is_responsive(12345))

    def test_zero_handle_conservative_pass(self):
        # 无句柄（WPF 深层元素）不探测，保守放行
        self.assertTrue(wt_flow_locator._window_is_responsive(0))
        self.assertTrue(wt_flow_locator._window_is_responsive(None))

    def test_probe_exception_conservative_pass(self):
        def boom(*a, **k):
            raise OSError("no user32")

        with patch.object(wt_flow_locator.ctypes.windll.user32, "SendMessageTimeoutW", side_effect=boom):
            self.assertTrue(wt_flow_locator._window_is_responsive(12345))


class ForceBottomScrollTests(unittest.TestCase):
    """preScrollToBottom：键入并行核数前强制滚到容器底部（离开固定保存栏遮挡区）。"""

    class FakeRect:
        def __init__(self, left, top, right, bottom):
            self.left, self.top, self.right, self.bottom = left, top, right, bottom

    class FakeScrollIf:
        def __init__(self, recorder):
            self.CurrentVerticallyScrollable = True
            self._recorder = recorder

        def SetScrollPercent(self, horiz, vert):
            self._recorder.append((horiz, vert))

    def test_force_bottom_scrolls_to_bottom_even_when_visible(self):
        control = MagicMock()
        win_rect = self.FakeRect(0, 0, 2560, 1516)
        # 控件可见（非离屏）但 force_bottom 必须仍执行滚动
        control.rectangle.return_value = self.FakeRect(354, 1085, 736, 1127)
        control.top_level_parent.return_value = SimpleNamespace(rectangle=lambda: win_rect)
        scroll_if = self.FakeScrollIf(recorder := [])
        ancestor = MagicMock()
        ancestor.iface_scroll = scroll_if
        ancestor.parent.return_value = None
        control.parent.return_value = ancestor

        result = wt_flow_locator._scroll_flow_control_into_view(
            control, step_id="step_26", control_id="x", force_bottom=True
        )
        self.assertTrue(result)
        self.assertIn((-1, 100.0), recorder, "force_bottom 必须 SetScrollPercent(-1, 100) 滚到底")

    def test_no_force_bottom_visible_control_no_scroll(self):
        # 未开 force_bottom：可见控件保持原行为不滚动（海拔回归护栏）
        control = MagicMock()
        win_rect = self.FakeRect(0, 0, 2560, 1516)
        control.rectangle.return_value = self.FakeRect(515, 1396, 795, 1438)
        control.top_level_parent.return_value = SimpleNamespace(rectangle=lambda: win_rect)
        scroll_if = self.FakeScrollIf(recorder := [])
        ancestor = MagicMock()
        ancestor.iface_scroll = scroll_if
        ancestor.parent.return_value = None
        control.parent.return_value = ancestor

        result = wt_flow_locator._scroll_flow_control_into_view(
            control, step_id="step_20", control_id="x", force_bottom=False
        )
        self.assertTrue(result)
        self.assertEqual(recorder, [], "贴底可见控件不得触发滚动")


class OffscreenFocusTests(unittest.TestCase):
    """send_keys/键入路径：矩形仍离屏时改用 SetFocus 程序化聚焦（海拔/空气密度被遮挡场景）。"""

    class FakeWin:
        def rectangle(self):
            return SimpleNamespace(left=0, top=0, right=2560, bottom=1516)

    class FakeCtrl:
        def __init__(self, rect):
            self._rect = rect
            self.set_focus_calls = 0
            self.click_calls = 0

        def rectangle(self):
            return self._rect

        def top_level_parent(self):
            return OffscreenFocusTests.FakeWin()

        def set_focus(self):
            self.set_focus_calls += 1

        def click_input(self):
            self.click_calls += 1

    def _run_focus(self, rect):
        ctrl = self.FakeCtrl(rect)
        with patch.object(wt_flow_locator, "find_flow_control", return_value=ctrl), \
             patch.object(wt_flow_locator, "_scroll_flow_control_into_view", return_value=False):
            ok = wt_flow_locator.focus_flow_control("step_x", "ctrl")
        return ok, ctrl

    def test_offscreen_uses_set_focus_not_click(self):
        # 滚动后仍离屏（海拔 y=-362 场景）：不得坐标点击，改用 SetFocus
        ok, ctrl = self._run_focus(self.FakeCtrlSimple(515, -362, 795, -320))
        self.assertTrue(ok)
        self.assertEqual(ctrl.set_focus_calls, 1)
        self.assertEqual(ctrl.click_calls, 0)

    def test_visible_uses_normal_click(self):
        ok, ctrl = self._run_focus(self.FakeCtrlSimple(515, 700, 795, 742))
        self.assertTrue(ok)
        self.assertEqual(ctrl.click_calls, 1)
        self.assertEqual(ctrl.set_focus_calls, 0)

    @staticmethod
    def FakeCtrlSimple(left, top, right, bottom):
        return SimpleNamespace(left=left, top=top, right=right, bottom=bottom)


class SearchBoxUiPathVetoTests(unittest.TestCase):
    """全文检索搜索框 ui_path 硬消歧：同名搜索框散布多面板时排除错误候选。"""

    class FakeNode:
        def __init__(self, name, control_type, parent=None):
            self._name = name
            self._parent = parent
            self.element_info = SimpleNamespace(
                control_type=control_type,
                localized_control_type="",
                automation_id="",
                framework_id="WPF",
                help_text="",
                process_id="1",
            )

        def window_text(self):
            return self._name

        def class_name(self):
            return ""

        def parent(self):
            return self._parent

    def _def(self):
        return {
            "id": "全文检索_Text",
            "name": "全文检索",
            "targetMethod": "name,control_type,ui_path",
            "targetValue": "全文检索,Text,MBAWRAWindTurbineTypeGridViewSelectorControl > 全文检索",
            "windowTitle": "*",
            "auxChecks": [],
            "inspectData": {"name": "全文检索", "controlType": "Text"},
        }

    def _legacy_def(self):
        d = self._def()
        d["targetMethod"] = "name,control_type"
        d["targetValue"] = "全文检索,Text"
        return d

    def test_correct_ancestry_matches(self):
        good = self.FakeNode("全文检索", "Text",
                             parent=self.FakeNode("MBAWRAWindTurbineTypeGridViewSelectorControl", "Custom"))
        with patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=False):
            self.assertGreater(
                wt_flow_locator.get_control_definition_match_score(good, self._def()), 0
            )

    def test_wrong_ancestry_vetoed_with_ui_path(self):
        # 另一面板的同名搜索框（父链不同）：ui_path 消歧必须一票否决
        wrong = self.FakeNode("全文检索", "Text", parent=self.FakeNode("SomeOtherPanel", "Custom"))
        score = wt_flow_locator.get_control_definition_match_score(wrong, self._def())
        self.assertEqual(score, -1, "ui_path 消歧下父链不匹配的候选必须被否决")

    def test_legacy_def_cannot_distinguish(self):
        # 旧定义（无 ui_path）：错误候选靠 name 回退照样得分——固化"飞点"根因
        wrong = self.FakeNode("全文检索", "Text", parent=self.FakeNode("SomeOtherPanel", "Custom"))
        with patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=False):
            self.assertGreater(
                wt_flow_locator.get_control_definition_match_score(wrong, self._legacy_def()), 0
            )


class CloseButtonVetoTests(unittest.TestCase):
    """编辑器关闭按钮（ctrl_close_editor）双实例消歧：窗口内 MUPMicroScaleView 版 vs 窗口外 MUPTaskMainView 版。"""

    class FakeNode:
        def __init__(self, name, class_name, control_type, parent=None):
            self._name = name
            self._class = class_name
            self._parent = parent
            self.element_info = SimpleNamespace(
                control_type=control_type, localized_control_type="",
                automation_id="MUPHeaderedClosableContentControl_Button_Close",
                framework_id="WPF", help_text="", process_id="1",
            )

        def window_text(self):
            return self._name

        def class_name(self):
            return self._class

        def parent(self):
            return self._parent

    def _def(self):
        return {
            "id": "ctrl_close_editor",
            "name": "MUPHeaderedClosableContentControl_Button_Close",
            "targetMethod": "automation_id,control_type,ui_path",
            "targetValue": "MUPHeaderedClosableContentControl_Button_Close,Button,MUPMicroScaleView > M219.76711",
            "windowTitle": "*",
            "auxChecks": [],
            "inspectData": {"automationId": "MUPHeaderedClosableContentControl_Button_Close", "controlType": "Button"},
        }

    def _make(self, parent_class):
        # 叶子 Name 为空、类名 M219.76711…（真实结构：叶子/父级 Name 均为空，靠类名区分）
        leaf = self.FakeNode("", "M219.76711,609.1864L218.51466", "Button",
                             parent=self.FakeNode("", parent_class, "Custom"))
        return leaf

    def test_visible_instance_under_microscale_matches(self):
        good = self._make("MUPMicroScaleView")
        with patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=False):
            self.assertGreater(
                wt_flow_locator.get_control_definition_match_score(good, self._def()), 0,
                "MUPMicroScaleView 下的关闭按钮必须命中（x≈2509 窗口内实例）",
            )

    def test_hidden_instance_under_taskmain_vetoed(self):
        wrong = self._make("MUPTaskMainView")
        score = wt_flow_locator.get_control_definition_match_score(wrong, self._def())
        self.assertEqual(score, -1, "MUPTaskMainView 下的离屏重复实例必须被否决（x≈3033）")

    def test_legacy_def_cannot_distinguish(self):
        # 旧定义（仅 aid+type）：两个实例都命中，先枚举者胜出（窗口外实例）——固化根因
        d = self._def()
        d["targetMethod"] = "automation_id,control_type"
        d["targetValue"] = "MUPHeaderedClosableContentControl_Button_Close,Button"
        with patch.object(wt_flow_locator, "wrapper_matches_label_text", return_value=False):
            self.assertGreater(
                wt_flow_locator.get_control_definition_match_score(self._make("MUPTaskMainView"), d), 0
            )


def json_load(f):
    import json
    return json.load(f)


class FakeValueControl:
    """满足校验轮询的最小 wrapper 替身。"""

    def __init__(self, value="0", visible=True):
        self.value = value
        self.visible = visible

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return True


class ContinueWhenCachePollingTests(unittest.TestCase):
    """P0-2：value_equals 轮询复用已定位 wrapper，不重复整树定位。"""

    def _run_wait(self, fake_values, alive_results=None):
        find_calls = []

        def fake_find(step_id, control_id=None, **kwargs):
            find_calls.append(control_id)
            return SyntheticHolder(fake_values, alive_results)

        class SyntheticHolder:
            def __init__(self, values, alive_results):
                self._values = list(values)
                self._alive_results = alive_results or []
                self._read_count = 0

            def is_visible(self):
                return True

            def is_enabled(self):
                return True

            def next_value(self):
                if self._read_count < len(self._values):
                    v = self._values[self._read_count]
                else:
                    v = self._values[-1]
                self._read_count += 1
                return v

            def alive(self):
                if self._alive_results:
                    return self._alive_results.pop(0)
                return True

        holder = {"current": None}

        def fake_get_wrapper_value(wrapper):
            return wrapper.next_value()

        def fake_is_wrapper_alive(wrapper):
            return wrapper.alive()

        def fake_get_cached_flow_control(*a, **k):
            return None

        fake_def = {"id": "step_20", "targetMethod": "a,b", "targetValue": "x,y"}
        with patch.object(wt_flow_locator, "get_flow_control_definition", return_value=fake_def), \
             patch.object(wt_flow_locator, "get_cached_flow_control", side_effect=fake_get_cached_flow_control), \
             patch.object(wt_flow_locator, "find_flow_control", side_effect=fake_find), \
             patch.object(wt_flow_locator, "get_wrapper_value", side_effect=fake_get_wrapper_value), \
             patch.object(wt_flow_locator, "is_wrapper_alive", side_effect=fake_is_wrapper_alive):
            ok = wt_flow_locator.wait_for_flow_control_condition(
                "step_20", "textbox_x", condition="value_equals",
                expected_value="99", timeout_seconds=1.0, poll_interval_seconds=0.01,
            )
        return ok, find_calls

    def test_value_equals_polls_held_wrapper_without_relocate(self):
        # 值在第 2 轮轮询才就绪：第 2 轮复用 held wrapper 直接命中，不再定位。
        # 9-1 wohler 修复（ValuePattern 滞后兜底）后，"值不匹配"当轮会补一次
        # _fresh_control 重定位再读值，故共 2 次 find：
        #   find1 = 首轮定位；find2 = 第 1 轮值不匹配的重定位兜底。
        ok, find_calls = self._run_wait(["0", "99"])
        self.assertTrue(ok)
        self.assertEqual(len(find_calls), 2, "首轮定位 1 次 + 值不匹配当轮重定位兜底 1 次")

    def test_cache_hit_via_empty_hint_fallback(self):
        # 校验轮询的 window_title_hint 与动作阶段不一致时，靠空 hint 兜底命中缓存，
        # 全程零定位（step_20 校验段 12s FindAll 的修复目标）
        cached = MagicMock()
        cached.is_visible.return_value = True
        cached.is_enabled.return_value = True

        def fake_get_cached(step_id, control_definition, window_title_hint=""):
            return None if window_title_hint == "main_window" else cached

        find_calls = []

        def fake_find(*args, **kwargs):
            find_calls.append(1)
            raise AssertionError("命中缓存时不得调用 find_flow_control")

        with patch.object(wt_flow_locator, "get_flow_control_definition", return_value={"id": "c"}), \
             patch.object(wt_flow_locator, "get_cached_flow_control", side_effect=fake_get_cached), \
             patch.object(wt_flow_locator, "find_flow_control", side_effect=fake_find), \
             patch.object(wt_flow_locator, "get_wrapper_value", return_value="99"), \
             patch.object(wt_flow_locator, "is_wrapper_alive", return_value=True):
            ok = wt_flow_locator.wait_for_flow_control_condition(
                "step_20", "c", condition="value_equals", expected_value="99",
                timeout_seconds=1.0, poll_interval_seconds=0.01,
                window_title_hint="main_window",
            )
        self.assertTrue(ok)
        self.assertEqual(find_calls, [])

    def test_dead_wrapper_drops_and_relocates(self):
        # 持有的 wrapper 失效后重新定位（控件销毁/重绘场景）。9-1 重定位兜底后，
        # 每次"值不匹配"当轮各补 1 次 _fresh_control 定位，find 数因此放大：
        #   find1 首轮定位 H1；find2 第1轮值不匹配重定位；find3 第2轮值不匹配重定位；
        #   第2轮尾 is_wrapper_alive(H1)=False → held 置空；find4 第3轮重新定位 H4；
        #   find5 第3轮值不匹配重定位；find6 第4轮值不匹配重定位；
        #   第5轮 H4 值读到 99 命中（held 复用，不再定位）。
        ok, find_calls = self._run_wait(["0", "0", "99"], alive_results=[True, False, True, True])
        self.assertTrue(ok)
        self.assertEqual(len(find_calls), 6, "定位 + 失效重定位 + 逐轮值不匹配重定位 = 6 次")

    def test_gone_condition_refreshes_each_poll(self):
        find_calls = []
        fake_control = FakeValueControl(value="x")
        calls = {"n": 0}

        def fake_find(step_id, control_id=None, **kwargs):
            find_calls.append(control_id)
            calls["n"] += 1
            return None if calls["n"] > 1 else fake_control  # 首轮存在、次轮消失

        with patch.object(wt_flow_locator, "find_flow_control", side_effect=fake_find), \
             patch.object(wt_flow_locator, "get_cached_flow_control", return_value=None), \
             patch.object(wt_flow_locator, "get_flow_control_definition", return_value={}):
            ok = wt_flow_locator.wait_for_flow_control_condition(
                "s", "c", condition="gone", timeout_seconds=1.0, poll_interval_seconds=0.01,
            )
        self.assertTrue(ok)  # 首轮存在 → 次轮消失 → gone 满足
        self.assertGreaterEqual(len(find_calls), 2, "gone 条件必须每轮全新枚举，不得持有缓存")


class ScrollViewBottomMarginTests(unittest.TestCase):
    """P1-1 实机回归护栏：贴底但窗口内可见的控件一律不再触发滚动。

    教训（step_20 海拔）："贴底余量"曾让 ScrollViewer 祖先整页滚动把控件
    滚出屏幕（rect 1396→-362），键入落空、value_equals 永不满足。回滚后
    贴底可见控件保持"不滚动直接键入"的原行为。
    """

    WINDOW_RECT = (0, 0, 2560, 1516)

    class FakeRect:
        def __init__(self, left, top, right, bottom):
            self.left, self.top, self.right, self.bottom = left, top, right, bottom

    class FakeWin:
        def rectangle(self):
            return ScrollViewBottomMarginTests.FakeRect(*ScrollViewBottomMarginTests.WINDOW_RECT)

    class FakeControl:
        def __init__(self, rect):
            self._rect = rect
            self.window = ScrollViewBottomMarginTests.FakeWin()

        def rectangle(self):
            return self._rect

        def top_level_parent(self):
            return self.window

        @property
        def iface_scroll_item(self):
            raise AttributeError("no scroll item pattern")

        def parent(self):
            return ScrollViewBottomMarginTests.FakePlainAncestor()

    class FakePlainAncestor:
        def parent(self):
            return None

        @property
        def iface_scroll(self):
            raise AttributeError("no scroll pattern")

    def _scroll(self, rect):
        control = self.FakeControl(rect)
        with patch("pyautogui.moveTo") as move_mock, patch("pyautogui.scroll") as wheel_mock:
            result = wt_flow_locator._scroll_flow_control_into_view(control, step_id="step_20", control_id="x")
        return result, move_mock, wheel_mock

    def test_mid_view_control_no_scroll(self):
        result, move_mock, wheel_mock = self._scroll(self.FakeRect(254, 700, 818, 740))
        self.assertTrue(result)
        move_mock.assert_not_called()
        wheel_mock.assert_not_called()

    def test_bottom_dirty_but_visible_no_scroll(self):
        # step_20 海拔同款：bottom 1438 距窗口底 78px，窗口内可见 → 不得触发任何滚动
        # （曾因滚动把控件甩出屏幕导致键入落空）
        result, move_mock, wheel_mock = self._scroll(self.FakeRect(515, 1396, 795, 1438))
        self.assertTrue(result)
        move_mock.assert_not_called()
        wheel_mock.assert_not_called()

    def test_offscreen_still_uses_wheel_fallback(self):
        # 真正离屏（窗口上方）保留滚轮兜底
        result, move_mock, wheel_mock = self._scroll(self.FakeRect(254, -100, 818, -60))
        self.assertFalse(result)
        move_mock.assert_called()
        self.assertGreaterEqual(wheel_mock.call_count, 1)


class PreconditionWaitVisibleTests(unittest.TestCase):
    """P2-1a：wait_visible 前置等待——只等不跳。"""

    class FakeLocated:
        pass

    def test_wait_visible_waits_then_returns_none(self):
        responses = iter([None, None, self.FakeLocated()])

        def fake_locate(*args, **kwargs):
            try:
                return next(responses)
            except StopIteration:
                return self.FakeLocated()

        action_config = {
            "precondition": {
                "condition": "wait_visible",
                "controlId": "ctrl_wra_synthesis1_item",
                "timeoutSeconds": 1.0,
            }
        }
        step_definition = {"id": "step_copy_select"}
        with patch.object(wt_flow_executor, "_LOCATE_FLOW_CONTROL", side_effect=fake_locate), \
             patch.object(wt_flow_locator, "get_wrapper_is_offscreen", return_value="False"):
            result = wt_flow_executor._eval_precondition_skip(
                "step_copy_select", action_config, step_definition
            )
        self.assertIsNone(result, "wait_visible 只等待，永不跳过动作")

    def test_wait_visible_timeout_still_returns_none(self):
        def fake_locate(*args, **kwargs):
            return None

        action_config = {
            "precondition": {
                "condition": "wait_visible",
                "controlId": "never_appears",
                "timeoutSeconds": 0.3,
            }
        }
        step_definition = {"id": "step_copy_select"}
        started = time.time()
        # 阻断超时诊断的真实 UIA 访问（测试环境不得触碰真实桌面窗口，
        # comtypes 对象延迟回收会触发 0xc0000374 堆损坏）
        with patch.object(wt_flow_executor, "_LOCATE_FLOW_CONTROL", side_effect=fake_locate), \
             patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=0), \
             patch.object(wt_flow_locator, "_try_get_window_by_handle", return_value=None):
            result = wt_flow_executor._eval_precondition_skip(
                "step_copy_select", action_config, step_definition
            )
        elapsed = time.time() - started
        self.assertIsNone(result)
        self.assertLess(elapsed, 3.0, "超时后应立即放行，不拖垮步骤")

    def test_toggle_precondition_untouched(self):
        # 旧 toggle 前置逻辑不受影响（回归护栏）
        def fake_locate(*args, **kwargs):
            return MagicMock()

        action_config = {
            "precondition": {"condition": "toggle", "expected": "off", "controlId": "tbox"}
        }
        step_definition = {"id": "step_x"}
        with patch.object(wt_flow_executor, "_LOCATE_FLOW_CONTROL", side_effect=fake_locate), \
             patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value="1"):
            result = wt_flow_executor._eval_precondition_skip("step_x", action_config, step_definition)
        self.assertIsNotNone(result, "toggle 前置：已勾选(target on 状态)时应跳过，保持旧语义")


if __name__ == "__main__":
    unittest.main()