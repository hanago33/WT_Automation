import io
import json
import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import patch, MagicMock


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator


def _make_rect_wrapper():
    """构造带矩形、且所有输入方法都会失败的 mock wrapper（模拟 Raw View 内部宿主）。"""
    wrapper = MagicMock()
    rect = MagicMock()
    rect.left, rect.top, rect.right, rect.bottom = 100, 100, 200, 125
    wrapper.rectangle = MagicMock(return_value=rect)
    wrapper.click_input = MagicMock(side_effect=RuntimeError("click_input failed"))
    wrapper.set_focus = MagicMock(side_effect=RuntimeError("set_focus failed"))
    wrapper.set_edit_text = MagicMock(side_effect=RuntimeError("set_edit_text failed"))
    wrapper.type_keys = MagicMock(side_effect=RuntimeError("type_keys failed"))
    return wrapper


class TypeViaScreenKeyboardTests(unittest.TestCase):
    """测试 PART_ContentHost 输入兜底 _type_via_screen_keyboard。"""

    def test_types_via_screen_keyboard(self):
        wrapper = _make_rect_wrapper()
        wrapper.click_input = MagicMock()  # 模拟 pywinauto 点击聚焦成功
        with ExitStack() as stack:
            mock_send = stack.enter_context(patch.object(wt_flow_locator, "send_keys"))
            result = wt_flow_locator._type_via_screen_keyboard(wrapper, "CGCS2000 43")
            self.assertTrue(result)
            wrapper.click_input.assert_called_once()
            mock_send.assert_called_once_with("CGCS2000 43")

    def test_no_rect_returns_false(self):
        wrapper = MagicMock()
        wrapper.rectangle = MagicMock(side_effect=RuntimeError("no rect"))
        with ExitStack() as stack:
            mock_send = stack.enter_context(patch.object(wt_flow_locator, "send_keys"))
            result = wt_flow_locator._type_via_screen_keyboard(wrapper, "x")
            self.assertFalse(result)
            wrapper.click_input.assert_not_called()
            mock_send.assert_not_called()


class TypeTextIntoWrapperContentHostFallbackTests(unittest.TestCase):
    """PART_ContentHost 在 set_edit_text/type_keys 失败后回退坐标键盘输入。"""

    def _patch_helpers(self, stack, automation_id, fallback_result):
        stack.enter_context(patch.object(wt_flow_locator, "_is_editable_wrapper", return_value=False))
        stack.enter_context(patch.object(wt_flow_locator, "_resolve_editable_target", return_value=None))
        stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value="Pane"))
        stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_automation_id", return_value=automation_id))
        stack.enter_context(patch.object(wt_flow_locator, "_type_via_screen_keyboard", return_value=fallback_result))
        stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_debug_snapshot", return_value={}))
        stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_value_snapshot", return_value=""))
        stack.enter_context(patch.object(wt_flow_locator, "_emit_time_series_debug_event"))

    def test_content_host_falls_back_on_type_keys_failure(self):
        wrapper = _make_rect_wrapper()
        with ExitStack() as stack:
            self._patch_helpers(stack, "PART_ContentHost", fallback_result=True)
            result = wt_flow_locator.type_text_into_wrapper(wrapper, "CGCS2000 43")
        self.assertTrue(result)

    def test_non_content_host_does_not_fall_back(self):
        # 非 PART_ContentHost 失败时不触发坐标键盘兜底（避免误点到其它控件）
        wrapper = _make_rect_wrapper()
        with ExitStack() as stack:
            self._patch_helpers(stack, "OtherAutomationId", fallback_result=True)
            result = wt_flow_locator.type_text_into_wrapper(wrapper, "x")
        self.assertFalse(result)


class PreferTabNavigationTests(unittest.TestCase):
    """preferTabNavigation=True 时，find_flow_control 应优先走 Tab 导航降级。"""

    def test_prefer_tab_used_before_regular_locate(self):
        mock_wrapper = MagicMock()

        def get_step_definition(step_id):
            return {
                "windowTitle": "测试窗口",
                "controls": [{
                    "id": "target_ctrl",
                    "preferTabNavigation": True,
                    "tabNavigation": {"anchorControlId": "anchor_ctrl", "direction": "forward", "steps": 1},
                }],
            }

        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_GET_STEP_DEFINITION", side_effect=get_step_definition))
            stack.enter_context(patch.object(wt_flow_locator, "iter_flow_search_windows", return_value=[MagicMock()]))
            mock_tab = stack.enter_context(patch.object(wt_flow_locator, "_try_tab_navigation_fallback", return_value=(mock_wrapper, 100)))
            stack.enter_context(patch.object(wt_flow_locator, "get_cached_flow_control", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator, "cache_flow_control"))
            stack.enter_context(patch.object(wt_flow_locator, "_record_locator_timing"))
            stack.enter_context(patch.object(wt_flow_locator, "_LOG_STEP"))
            stack.enter_context(patch.object(wt_flow_locator, "_apply_self_heal_override", side_effect=lambda s, c, d: d))
            result = wt_flow_locator.find_flow_control("step_9", control_id="target_ctrl", timeout_seconds=1)
            self.assertIs(result, mock_wrapper)
            mock_tab.assert_called_once()


class TabNavFocusGuardTests(unittest.TestCase):
    """Tab 导航锚点聚焦校验：Text 等不可聚焦控件时放弃，避免从错误起点误 Tab。"""

    def test_unfocusable_anchor_aborts_tab_nav(self):
        anchor = MagicMock()
        anchor.element_info.control_type = "Text"
        anchor.element_info.localized_control_type = ""
        other = MagicMock()
        other.element_info.control_type = "Button"
        other.element_info.localized_control_type = ""
        control_definition = {
            "id": "target_ctrl",
            "controlType": "Edit",
            "tabNavigation": {"anchorControlId": "anchor_ctrl", "direction": "forward", "steps": 1},
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "find_flow_control", return_value=anchor))
            # 焦点始终停在 other 控件：set_focus / 点击都无法把焦点落到 Text 锚点
            stack.enter_context(patch.object(wt_flow_locator, "_get_focused_element", return_value=other))
            stack.enter_context(patch.object(wt_flow_locator, "_is_same_wrapper", side_effect=lambda a, b: a is b))
            # 无可聚焦相邻控件可借力（该能力由 _find_focusable_neighbor 单独测试覆盖）
            stack.enter_context(patch.object(wt_flow_locator, "_find_focusable_neighbor", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_center", return_value=(100, 100)))
            stack.enter_context(patch.object(wt_flow_locator.pyautogui, "click"))
            stack.enter_context(patch.object(wt_flow_locator, "_LOG_STEP"))
            result = wt_flow_locator._try_tab_navigation_fallback([MagicMock()], control_definition, step_id="step_9")
        self.assertIsNone(result)

    def test_click_twice_to_expand_clicks_anchor_twice(self):
        anchor = MagicMock()
        anchor.element_info.control_type = "Button"
        anchor.element_info.localized_control_type = ""
        control_definition = {
            "id": "target_ctrl",
            "controlType": "Edit",
            "tabNavigation": {
                "anchorControlId": "anchor_ctrl",
                "direction": "forward",
                "steps": 1,
                "clickTwiceToExpand": True,
            },
        }
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "find_flow_control", return_value=anchor))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_center", return_value=(100, 100)))
            mock_click = stack.enter_context(patch.object(wt_flow_locator.pyautogui, "click"))
            stack.enter_context(patch.object(wt_flow_locator, "send_keys"))
            stack.enter_context(patch.object(wt_flow_locator, "_get_focused_element", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator, "score_control_match", return_value=20))
            stack.enter_context(patch.object(wt_flow_locator, "_is_same_wrapper", side_effect=lambda a, b: a is b))
            stack.enter_context(patch.object(wt_flow_locator, "_LOG_STEP"))
            wt_flow_locator._try_tab_navigation_fallback(
                [MagicMock()], control_definition, step_id="step_9", max_tab_steps=1
            )
        # clickTwiceToExpand 配置下：折叠头点击一次折叠、再点一次展开，共两次点击
        self.assertEqual(mock_click.call_count, 2)


class LabelTextSiblingFallbackTests(unittest.TestCase):
    """PART_ContentHost 的 label_text 匹配：兄弟 TextBlock 兜底。"""

    def _make_content_host(self, siblings=None):
        wrapper = MagicMock()
        wrapper.element_info.automation_id = "PART_ContentHost"
        wrapper.element_info.control_type = "Pane"
        parent = MagicMock()
        wrapper.parent = MagicMock(return_value=parent)
        children = list(siblings or [])
        if wrapper not in children:
            children.append(wrapper)  # 真实 DOM 中 wrapper 自身也在父级 children 里
        parent.children = MagicMock(return_value=children)
        return wrapper

    def _make_text_block(self, text):
        label = MagicMock()
        label.element_info.control_type = "Text"
        label.window_text = MagicMock(return_value=text)
        return label

    def test_match_sibling_text_block(self):
        label = self._make_text_block("查找")
        wrapper = self._make_content_host(siblings=[label])
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_is_same_wrapper", side_effect=lambda a, b: a is b))
            result = wt_flow_locator._match_sibling_text_block_label(wrapper, "查找")
        self.assertTrue(result)

    def test_no_matching_sibling(self):
        wrapper = self._make_content_host(siblings=[])
        self.assertFalse(wt_flow_locator._match_sibling_text_block_label(wrapper, "查找"))

    def test_wrapper_matches_label_text_falls_back_to_sibling(self):
        label = self._make_text_block("查找")
        wrapper = self._make_content_host(siblings=[label])
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_find_label_rects_for_wrapper", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_rectangle", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator, "_read_wrapper_labeled_by_name", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "_is_same_wrapper", side_effect=lambda a, b: a is b))
            result = wt_flow_locator.wrapper_matches_label_text(wrapper, "查找")
        self.assertTrue(result)

    def test_wrapper_own_name_counts_as_label(self):
        # 按钮自身 name 即标签文本（如"地形"按钮），无需附近标签也能匹配
        wrapper = MagicMock()
        wrapper.window_text = MagicMock(return_value="地形")
        self.assertTrue(wt_flow_locator.wrapper_matches_label_text(wrapper, "地形"))


class SharedAutomationIdDataFixTests(unittest.TestCase):
    """综合卡片标题模板坑的数据层修复回归（不依赖全局 name 否决逻辑）：

    step_23/35 的控件定义已清空共享 automationId（WRAComputation_Text_Header），
    定位只靠 name 精确匹配；同一模板 automationId 的相邻卡片（如综合1 标题）
    不得再以高置信命中。防止将来重新采集把 automationId 加回去导致误选。
    """

    FLOW_PATH = os.path.join(PROJECT_DIR, "flow_packages", "flow_definition_导出综合计算结果.json")

    def _control_definition(self, step_id, control_id):
        payload = json.load(io.open(self.FLOW_PATH, encoding="utf-8-sig"))
        for step in payload["steps"]:
            if step["id"] == step_id:
                for control in step["controls"]:
                    if control["id"] == control_id:
                        return control
        raise AssertionError("控件定义未找到: %s/%s" % (step_id, control_id))

    def _score(self, definition, wrapper_name, wrapper_aid):
        wrapper = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value=wrapper_name))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value="Text"))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_automation_id", return_value=wrapper_aid))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_class_name", return_value="TextBlock"))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_runtime_text_candidates", return_value=[]))
            return wt_flow_locator.get_control_definition_match_score(wrapper, definition)

    def test_step23_definition_picks_only_zonghe2(self):
        cdef = self._control_definition("step_23", "step_19_control_1")
        # 数据层前提：共享 automationId 已清空
        self.assertEqual(str((cdef.get("inspectData", {}) or {}).get("automationId", "")).strip(), "", "step_23 仍带共享 automationId")
        score_other = self._score(cdef, "综合1", "WRAComputation_Text_Header")
        score_target = self._score(cdef, "综合2", "WRAComputation_Text_Header")
        self.assertLess(score_other, 100)          # 名字不匹配者不得高置信
        self.assertGreaterEqual(score_target, 120)  # 真目标精确命中

    def test_step35_definition_picks_only_zonghe3(self):
        cdef = self._control_definition("step_35", "step_29_control_1")
        self.assertEqual(str((cdef.get("inspectData", {}) or {}).get("automationId", "")).strip(), "", "step_35 仍带共享 automationId")
        score_other = self._score(cdef, "综合2", "WRAComputation_Text_Header")
        score_target = self._score(cdef, "综合3", "WRAComputation_Text_Header")
        self.assertLess(score_other, 100)
        self.assertGreaterEqual(score_target, 120)


class ClickRetryToggleGuardTests(unittest.TestCase):
    """ToggleButton/Expander 头点击不重试，避免"展开又折叠"。"""

    def test_toggle_control_not_retried(self):
        control, fg_before, fg_after = MagicMock(), MagicMock(), MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "is_automation_window", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_handle", side_effect=lambda w: id(w)))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=1234))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value="0"))
            result = wt_flow_locator.should_retry_click_after_focus_switch(control, fg_before, fg_after)
        self.assertFalse(result)

    def test_non_toggle_unfocusable_control_retried(self):
        control, fg_before, fg_after = MagicMock(), MagicMock(), MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "is_automation_window", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_handle", side_effect=lambda w: id(w)))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=1234))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "is_text_like_wrapper", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_is_keyboard_focusable", return_value="False"))
            result = wt_flow_locator.should_retry_click_after_focus_switch(control, fg_before, fg_after)
        self.assertTrue(result)

