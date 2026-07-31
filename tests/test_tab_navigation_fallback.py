# encoding: utf-8
"""Tab 导航降级功能单元测试：覆盖配置缺失、锚点失败、匹配通过/不通过、递归保护、方向支持。"""

import types

import pytest
from unittest.mock import patch, MagicMock

import wt_flow_locator as L


# ── FakeWrapper ──────────────────────────────────────────────────────────────

class FakeWrapper:
    """模拟 pywinauto wrapper，支持焦点元素和锚点控件场景。"""

    def __init__(self, name="", control_type="Edit", automation_id="", process_id=1234):
        self._name = name
        self.element_info = types.SimpleNamespace(
            control_type=control_type,
            localized_control_type="",
            automation_id=automation_id,
            framework_id="WPF",
            help_text="",
            process_id=process_id,
            runtime_id="",
            name=name,
        )

    def window_text(self):
        return self._name

    def class_name(self):
        return "Edit"

    def set_focus(self):
        pass

    def click_input(self):
        pass

    def process_id(self):
        return self.element_info.process_id


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_control_definition(tab_navigation=None, control_id="target_input"):
    """构造带有 tabNavigation 配置的控件定义。"""
    cd = {
        "id": control_id,
        "inspectData": {
            "controlType": "Edit",
            "localizedControlType": "",
            "automationId": "txtTarget",
            "name": "目标输入框",
        },
    }
    if tab_navigation is not None:
        cd["tabNavigation"] = tab_navigation
    return cd


def _make_tab_nav_config(anchor="anchor_btn", direction="forward", steps=5):
    """构造 tabNavigation 配置字典。"""
    return {
        "anchorControlId": anchor,
        "direction": direction,
        "steps": steps,
    }


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_tab_nav_state():
    """每个测试前后清理防递归集合。"""
    L._TAB_NAV_IN_PROGRESS.clear()
    yield
    L._TAB_NAV_IN_PROGRESS.clear()


# ── 测试场景 ─────────────────────────────────────────────────────────────────

class TestTabNavigationFallbackConfigMissing:
    """配置缺失不触发降级。"""

    def test_no_tab_navigation_field_returns_none(self):
        """control_definition 中没有 tabNavigation 字段时，函数应返回 None。"""
        cd = _make_control_definition(tab_navigation=None)
        windows = [MagicMock()]
        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert result is None

    def test_tab_navigation_is_empty_dict_returns_none(self):
        """tabNavigation 为空字典时，函数应返回 None。"""
        cd = _make_control_definition(tab_navigation={})
        windows = [MagicMock()]
        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert result is None

    def test_non_dict_control_definition_returns_none(self):
        """control_definition 不是字典时，函数应返回 None。"""
        result = L._try_tab_navigation_fallback([MagicMock()], "not_a_dict", step_id="s1")
        assert result is None

    def test_empty_windows_returns_none(self):
        """windows 为空列表时，函数应返回 None。"""
        cd = _make_control_definition(_make_tab_nav_config())
        result = L._try_tab_navigation_fallback([], cd, step_id="step1")
        assert result is None


class TestTabNavigationFallbackAnchorFailure:
    """锚点定位失败返回 None。"""

    @patch.object(L, "find_flow_control", return_value=None)
    def test_anchor_not_found_returns_none(self, _mock_find):
        """find_flow_control 返回 None 时，函数应返回 None。"""
        tab_nav = _make_tab_nav_config(anchor="missing_anchor")
        cd = _make_control_definition(tab_nav)
        windows = [MagicMock()]
        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert result is None

    @patch.object(L, "find_flow_control", side_effect=Exception("UIA error"))
    def test_anchor_find_raises_returns_none(self, _mock_find):
        """find_flow_control 抛异常时，函数应返回 None。"""
        tab_nav = _make_tab_nav_config(anchor="anchor_btn")
        cd = _make_control_definition(tab_nav)
        windows = [MagicMock()]
        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert result is None


class TestTabNavigationFallbackMatchPass:
    """匹配验证通过：焦点元素评分 >= 70 时立即返回。"""

    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=85)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "find_flow_control")
    def test_focused_element_matches_returns_wrapper_and_score(
        self, mock_find, mock_focused, mock_score, mock_send
    ):
        """焦点元素评分 >= 70 时，函数返回 (wrapper, score)。"""
        anchor = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_find.return_value = anchor

        target = FakeWrapper(name="目标", control_type="Edit", automation_id="txtTarget")
        mock_focused.return_value = target

        tab_nav = _make_tab_nav_config(direction="forward", steps=5)
        cd = _make_control_definition(tab_nav)
        windows = [MagicMock()]

        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")

        assert result is not None
        wrapper, score = result
        assert wrapper is target
        assert score == 85
        # 第一次 Tab 就命中，只需发送 1 次
        assert mock_send.call_count == 1

    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", side_effect=[30, 40, 90])
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "find_flow_control")
    def test_matches_on_third_tab_step(
        self, mock_find, mock_focused, mock_score, mock_send
    ):
        """前两次不匹配，第三次 Tab 命中。每次焦点元素不同以避免焦点环检测。"""
        anchor = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_find.return_value = anchor

        # 每次返回不同的控件，避免 seen_ids 焦点环检测
        elem1 = FakeWrapper(name="控件1", control_type="Edit", automation_id="txtStep1")
        elem2 = FakeWrapper(name="控件2", control_type="Edit", automation_id="txtStep2")
        elem3 = FakeWrapper(name="目标", control_type="Edit", automation_id="txtTarget")
        mock_focused.side_effect = [elem1, elem2, elem3]

        tab_nav = _make_tab_nav_config(direction="forward", steps=5)
        cd = _make_control_definition(tab_nav)
        windows = [MagicMock()]

        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")

        assert result is not None
        wrapper, score = result
        assert wrapper is elem3
        assert score == 90
        assert mock_send.call_count == 3


class TestTabNavigationFallbackMatchFail:
    """匹配验证不通过：所有步骤评分均 < 70 且无有效候选时返回 None。"""

    @patch.object(L, "send_keys")
    @patch.object(L, "_get_focused_element", return_value=None)
    @patch.object(L, "find_flow_control")
    def test_no_focused_element_returns_none(self, mock_find, mock_focused, mock_send):
        """始终无法获取焦点元素时，函数返回 None。"""
        anchor = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_find.return_value = anchor

        tab_nav = _make_tab_nav_config(direction="forward", steps=5)
        cd = _make_control_definition(tab_nav)
        windows = [MagicMock()]

        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1", max_tab_steps=5)
        assert result is None

    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=20)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "find_flow_control")
    def test_low_score_returns_best_candidate(self, mock_find, mock_focused, mock_score, mock_send):
        """评分始终低于 70 但有候选时，返回最佳候选（供上层决策）。"""
        anchor = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_find.return_value = anchor

        focused = FakeWrapper(name="低分控件", control_type="Edit", automation_id="txtLow")
        mock_focused.return_value = focused

        tab_nav = _make_tab_nav_config(direction="forward", steps=3)
        cd = _make_control_definition(tab_nav)
        windows = [MagicMock()]

        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1", max_tab_steps=3)

        # 代码逻辑：有候选但未达阈值时返回最佳候选
        assert result is not None
        wrapper, score = result
        assert wrapper is focused
        assert score == 20


class TestTabNavigationFallbackRecursionProtection:
    """递归保护：_TAB_NAV_IN_PROGRESS 中已有当前 control_id 时立即返回 None。"""

    def test_already_in_progress_returns_none(self):
        """control_id 已在 _TAB_NAV_IN_PROGRESS 中时，函数立即返回 None。"""
        cd = _make_control_definition(_make_tab_nav_config(), control_id="target_input")
        L._TAB_NAV_IN_PROGRESS.add("target_input")

        windows = [MagicMock()]
        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert result is None

    def test_different_control_id_not_blocked(self):
        """不同的 control_id 不受递归保护影响。"""
        cd = _make_control_definition(_make_tab_nav_config(), control_id="target_input")
        L._TAB_NAV_IN_PROGRESS.add("other_control")

        # 虽然 other_control 在处理中，但 target_input 不应被阻止
        # 由于没有 mock find_flow_control，锚点找不到会返回 None，但不会因递归保护而返回
        with patch.object(L, "find_flow_control", return_value=None):
            windows = [MagicMock()]
            result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
            # 返回 None 是因为锚点找不到，而非递归保护
            assert result is None
        assert "target_input" not in L._TAB_NAV_IN_PROGRESS

    def test_cleanup_after_completion(self):
        """函数执行完毕后，control_id 从 _TAB_NAV_IN_PROGRESS 中移除。"""
        cd = _make_control_definition(_make_tab_nav_config(), control_id="cleanup_test")
        windows = [MagicMock()]

        with patch.object(L, "find_flow_control", return_value=None):
            L._try_tab_navigation_fallback(windows, cd, step_id="step1")

        assert "cleanup_test" not in L._TAB_NAV_IN_PROGRESS


class TestTabNavigationFallbackDirection:
    """方向支持：forward（Tab）和 backward（Shift+Tab）。"""

    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=80)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "find_flow_control")
    def test_forward_direction_sends_tab(self, mock_find, mock_focused, mock_score, mock_send):
        """forward 方向发送 {TAB} 键。"""
        anchor = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_find.return_value = anchor

        target = FakeWrapper(name="目标", control_type="Edit", automation_id="txtTarget")
        mock_focused.return_value = target

        tab_nav = _make_tab_nav_config(direction="forward", steps=3)
        cd = _make_control_definition(tab_nav)
        windows = [MagicMock()]

        L._try_tab_navigation_fallback(windows, cd, step_id="step1")

        mock_send.assert_called_with("{TAB}")

    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=80)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "find_flow_control")
    def test_backward_direction_sends_shift_tab(self, mock_find, mock_focused, mock_score, mock_send):
        """backward 方向发送 +{TAB}（Shift+Tab）键。"""
        anchor = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_find.return_value = anchor

        target = FakeWrapper(name="目标", control_type="Edit", automation_id="txtTarget")
        mock_focused.return_value = target

        tab_nav = _make_tab_nav_config(direction="backward", steps=3)
        cd = _make_control_definition(tab_nav)
        windows = [MagicMock()]

        L._try_tab_navigation_fallback(windows, cd, step_id="step1")

        mock_send.assert_called_with("+{TAB}")


class TestTabNavigationFallbackNonInputControl:
    """非输入类控件不触发 Tab 导航降级。"""

    def test_button_control_type_returns_none(self):
        """controlType 为 Button 时，不属于输入类控件，函数返回 None。"""
        tab_nav = _make_tab_nav_config()
        cd = {
            "id": "btn_test",
            "inspectData": {
                "controlType": "Button",
                "localizedControlType": "",
                "automationId": "btnOK",
            },
            "tabNavigation": tab_nav,
        }
        windows = [MagicMock()]
        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert result is None
