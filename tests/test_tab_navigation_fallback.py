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

        # 每次返回不同的控件，避免 seen_ids 焦点环检测；
        # 首个值为 set_focus 后的焦点落点校验（焦点=锚点自身），其后为 Tab 步进焦点
        elem1 = FakeWrapper(name="控件1", control_type="Edit", automation_id="txtStep1")
        elem2 = FakeWrapper(name="控件2", control_type="Edit", automation_id="txtStep2")
        elem3 = FakeWrapper(name="目标", control_type="Edit", automation_id="txtTarget")
        mock_focused.side_effect = [anchor, elem1, elem2, elem3]

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
    def test_low_score_returns_none(self, mock_find, mock_focused, mock_score, mock_send):
        """评分始终低于 70 时不得返回低分命中（修复 B3：防误定位污染缓存），应返回 None。"""
        anchor = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_find.return_value = anchor

        focused = FakeWrapper(name="低分控件", control_type="Edit", automation_id="txtLow")
        mock_focused.return_value = focused

        tab_nav = _make_tab_nav_config(direction="forward", steps=3)
        cd = _make_control_definition(tab_nav)
        windows = [MagicMock()]

        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1", max_tab_steps=3)

        # 修复 B3：Tab 循环结束后必须达 70 分才判命中，任意正分（10-69）不得当作成功
        assert result is None


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


class _FailingFocusWrapper(FakeWrapper):
    """set_focus 会抛异常的 wrapper，用于验证聚焦策略链降级到点击路径。"""

    def set_focus(self):
        raise RuntimeError("set_focus failed")


class _NodeWrapper(FakeWrapper):
    """带 parent/children/rectangle 的树形 wrapper，用于相邻可聚焦搜索测试。"""

    def __init__(self, name="", control_type="Edit", automation_id="", rect=(0, 0, 100, 100),
                 parent=None, children=None):
        super().__init__(name, control_type, automation_id)
        self._parent = parent
        self._children = list(children or [])
        self._rect = rect

    def parent(self):
        return self._parent

    def children(self):
        return self._children

    def rectangle(self):
        left, top, right, bottom = self._rect
        return types.SimpleNamespace(left=left, top=top, right=right, bottom=bottom)


class TestWrapperIsKeyboardFocusable:
    """键盘可聚焦判定：静态类型直接判不可聚焦，其余类型读 UIA 属性增强。"""

    def test_static_text_type_unfocusable(self):
        w = FakeWrapper(name="标签", control_type="Text")
        assert L._wrapper_is_keyboard_focusable(w) is False

    def test_editable_type_focusable_by_default(self):
        w = FakeWrapper(name="输入", control_type="Edit")
        assert L._wrapper_is_keyboard_focusable(w) is True

    def test_uia_property_true_overrides(self):
        w = FakeWrapper(name="面板", control_type="Pane")
        w.element_info.is_keyboard_focusable = True
        assert L._wrapper_is_keyboard_focusable(w) is True

    def test_uia_property_false_respected(self):
        w = FakeWrapper(name="面板", control_type="Pane")
        w.element_info.is_keyboard_focusable = False
        assert L._wrapper_is_keyboard_focusable(w) is False

    def test_none_wrapper_unfocusable(self):
        assert L._wrapper_is_keyboard_focusable(None) is False


class TestFindFocusableNeighbor:
    """相邻可聚焦搜索：兄弟同行优先 → 后代 BFS → 祖先兄弟。"""

    def test_sibling_same_row_nearest_preferred(self):
        parent = _NodeWrapper(name="容器", control_type="Pane")
        anchor = _NodeWrapper(name="标签", control_type="Text", rect=(100, 100, 200, 120), parent=parent)
        near = _NodeWrapper(name="近按钮", control_type="Button", rect=(220, 100, 300, 120), parent=parent)
        far = _NodeWrapper(name="远按钮", control_type="Button", rect=(400, 100, 500, 120), parent=parent)
        parent._children = [anchor, near, far]
        assert L._find_focusable_neighbor(anchor) is near

    def test_descendant_bfs_when_no_sibling(self):
        anchor = _NodeWrapper(name="容器文本", control_type="Text", rect=(0, 0, 100, 100))
        inner = _NodeWrapper(name="内部输入", control_type="Edit", rect=(10, 10, 80, 80))
        anchor._children = [inner]
        assert L._find_focusable_neighbor(anchor) is inner

    def test_ancestor_sibling_when_no_sibling_or_descendant(self):
        root = _NodeWrapper(name="根", control_type="Pane", rect=(0, 0, 500, 400))
        mid = _NodeWrapper(name="中层", control_type="Pane", rect=(0, 0, 100, 100), parent=root)
        anchor = _NodeWrapper(name="文本", control_type="Text", rect=(10, 10, 90, 90), parent=mid)
        btn = _NodeWrapper(name="按钮", control_type="Button", rect=(200, 50, 300, 150), parent=root)
        root._children = [mid, btn]
        mid._children = [anchor]
        assert L._find_focusable_neighbor(anchor) is btn

    def test_none_anchor_returns_none(self):
        assert L._find_focusable_neighbor(None) is None


class TestTabNavigationFallbackAnchorFocusChain:
    """第一层：锚点聚焦策略链（set_focus → 相邻可聚焦点击 → 锚点中心点击兜底）。"""

    @patch.object(L, "pyautogui")
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_find_focusable_neighbor")
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    def test_unfocusable_text_anchor_clicks_focusable_neighbor(
        self, mock_center, mock_same, mock_neighbor, mock_focused, mock_pyautogui
    ):
        """不可聚焦 Text 锚点：优先点击可聚焦相邻控件作为 Tab 起点。"""
        anchor = FakeWrapper(name="投影系", control_type="Text", automation_id="")
        neighbor = FakeWrapper(name="投影系输入", control_type="Edit", automation_id="txtProj")
        before = FakeWrapper(name="旧焦点", control_type="Button", automation_id="btnOld")
        mock_neighbor.return_value = neighbor
        mock_focused.side_effect = [before, neighbor]  # 点击前 → 点击后
        tab_nav = {"anchorControlId": "投影系_Text", "direction": "forward", "steps": 2}
        start = L._try_focus_anchor(anchor, tab_nav, step_id="step_9", anchor_id="投影系_Text")
        assert start is neighbor
        mock_pyautogui.click.assert_called_once_with(100, 200)

    @patch.object(L, "pyautogui")
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_find_focusable_neighbor")
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    def test_unfocusable_text_anchor_falls_back_to_anchor_center(
        self, mock_center, mock_same, mock_neighbor, mock_focused, mock_pyautogui
    ):
        """无相邻可聚焦控件时，兜底点击锚点自身中心。"""
        anchor = FakeWrapper(name="投影系", control_type="Text", automation_id="")
        after = FakeWrapper(name="新焦点", control_type="Edit", automation_id="txtNew")
        before = FakeWrapper(name="旧焦点", control_type="Button", automation_id="btnOld")
        mock_neighbor.return_value = None
        mock_focused.side_effect = [before, after]
        tab_nav = {"anchorControlId": "投影系_Text", "direction": "forward", "steps": 2}
        start = L._try_focus_anchor(anchor, tab_nav, step_id="step_9", anchor_id="投影系_Text")
        assert start is after
        mock_pyautogui.click.assert_called_once_with(100, 200)

    @patch.object(L, "pyautogui")
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_find_focusable_neighbor")
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    def test_click_focus_invalid_when_focus_not_moved(
        self, mock_center, mock_same, mock_neighbor, mock_focused, mock_pyautogui
    ):
        """点击后焦点未移动（点击无效）：放弃 Tab 降级。"""
        anchor = FakeWrapper(name="投影系", control_type="Text", automation_id="")
        before = FakeWrapper(name="旧焦点", control_type="Button", automation_id="btnOld")
        mock_neighbor.return_value = None
        mock_focused.side_effect = [before, before]  # 点击前后焦点相同
        tab_nav = {"anchorControlId": "投影系_Text", "direction": "forward", "steps": 2}
        start = L._try_focus_anchor(anchor, tab_nav, step_id="step_9", anchor_id="投影系_Text")
        assert start is None

    @patch.object(L, "pyautogui")
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_find_focusable_neighbor")
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    def test_click_focus_invalid_when_focus_on_window(
        self, mock_center, mock_same, mock_neighbor, mock_focused, mock_pyautogui
    ):
        """点击后焦点落在顶层窗口（未落到子控件）：放弃 Tab 降级。"""
        anchor = FakeWrapper(name="投影系", control_type="Text", automation_id="")
        before = FakeWrapper(name="旧焦点", control_type="Button", automation_id="btnOld")
        window_wrapper = FakeWrapper(name="主窗口", control_type="Window", automation_id="")
        mock_neighbor.return_value = None
        mock_focused.side_effect = [before, window_wrapper]
        tab_nav = {"anchorControlId": "投影系_Text", "direction": "forward", "steps": 2}
        start = L._try_focus_anchor(anchor, tab_nav, step_id="step_9", anchor_id="投影系_Text")
        assert start is None

    @patch.object(L, "pyautogui")
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    def test_focusable_anchor_set_focus_direct(self, mock_center, mock_focused, mock_pyautogui):
        """可聚焦 Button 锚点：set_focus 成功且焦点落到锚点自身，直接作为起点，不执行点击。"""
        anchor = FakeWrapper(name="锚点按钮", control_type="Button", automation_id="btnAnchor")
        mock_focused.return_value = anchor  # set_focus 后焦点落点校验：焦点=锚点
        tab_nav = {"anchorControlId": "btnAnchor", "direction": "forward", "steps": 2}
        start = L._try_focus_anchor(anchor, tab_nav, step_id="step_9", anchor_id="btnAnchor")
        assert start is anchor
        mock_pyautogui.click.assert_not_called()

    @patch.object(L, "pyautogui")
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    def test_focusable_anchor_set_focus_fails_falls_back_to_click(
        self, mock_center, mock_same, mock_focused, mock_pyautogui
    ):
        """可聚焦锚点 set_focus 抛异常且激活窗口重试仍失败：降级到点击锚点中心。"""
        anchor = _FailingFocusWrapper(name="锚点按钮", control_type="Button", automation_id="btnAnchor")
        before = FakeWrapper(name="旧焦点", control_type="Button", automation_id="btnOld")
        after = FakeWrapper(name="新焦点", control_type="Edit", automation_id="txtNew")
        mock_focused.side_effect = [before, after]
        tab_nav = {"anchorControlId": "btnAnchor", "direction": "forward", "steps": 2}
        start = L._try_focus_anchor(anchor, tab_nav, step_id="step_9", anchor_id="btnAnchor")
        assert start is after
        mock_pyautogui.click.assert_called_once_with(100, 200)

    @patch.object(L, "pyautogui")
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    def test_click_twice_to_expand_returns_anchor(self, mock_center, mock_focused, mock_pyautogui):
        """clickTwiceToExpand：双击展开后直接以锚点为起点（由 Tab 循环评分兜底）。"""
        anchor = FakeWrapper(name="折叠头", control_type="Button", automation_id="btnExpand")
        tab_nav = {"anchorControlId": "btnExpand", "direction": "forward", "steps": 2, "clickTwiceToExpand": True}
        start = L._try_focus_anchor(anchor, tab_nav, step_id="step_9", anchor_id="btnExpand")
        assert start is anchor
        assert mock_pyautogui.click.call_count == 2


class TestTabNavigationFallbackStepWidening:
    """起点偏移时 Tab 步数上限自适应：起点非锚点自身则放宽，起点=锚点则保持精确。"""

    @patch.object(L, "pyautogui")
    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=20)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    @patch.object(L, "find_flow_control")
    def test_tab_steps_remain_exact_when_start_is_anchor(
        self, mock_find, mock_center, mock_same, mock_focused, mock_score, mock_send, mock_pyautogui
    ):
        """可聚焦 Button 锚点 set_focus 成功：起点=锚点自身，步数上限保持配置 steps=2。"""
        anchor = FakeWrapper(name="锚点按钮", control_type="Button", automation_id="btnAnchor")
        mock_find.return_value = anchor
        # 可聚焦锚点 set_focus 后先做焦点落点校验（焦点=锚点自身），Tab 循环 2 步各取一次焦点
        mock_focused.side_effect = [
            anchor,
            FakeWrapper(name="步1", control_type="Edit", automation_id="txt1"),
            FakeWrapper(name="步2", control_type="Edit", automation_id="txt2"),
        ]
        cd = _make_control_definition(_make_tab_nav_config(direction="forward", steps=2))
        windows = [MagicMock()]
        L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert mock_send.call_count == 2
        mock_pyautogui.click.assert_not_called()

    @patch.object(L, "pyautogui")
    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=20)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_find_focusable_neighbor")
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    @patch.object(L, "find_flow_control")
    def test_tab_steps_widened_when_start_shifted_to_neighbor(
        self, mock_find, mock_center, mock_same, mock_neighbor, mock_focused, mock_score, mock_send, mock_pyautogui
    ):
        """不可聚焦 Text 锚点点击邻居起点：步数上限放宽为 steps + 缓冲（2+6=8）。"""
        anchor = FakeWrapper(name="投影系", control_type="Text", automation_id="")
        neighbor = FakeWrapper(name="投影系输入", control_type="Edit", automation_id="txtProj")
        before = FakeWrapper(name="旧焦点", control_type="Button", automation_id="btnOld")
        mock_find.return_value = anchor
        mock_neighbor.return_value = neighbor
        tab_steps = L._TAB_NAV_START_OFFSET_BUFFER + 2  # 放宽后的上限
        mock_focused.side_effect = [before, neighbor] + [
            FakeWrapper(name="步{}".format(i), control_type="Edit", automation_id="txt{}".format(i))
            for i in range(tab_steps)
        ]
        cd = _make_control_definition(_make_tab_nav_config(direction="forward", steps=2))
        windows = [MagicMock()]
        L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert mock_send.call_count == tab_steps

    @patch.object(L, "pyautogui")
    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=20)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_find_focusable_neighbor", return_value=None)
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    @patch.object(L, "find_flow_control")
    def test_tab_steps_widened_when_start_shifted_to_anchor_center(
        self, mock_find, mock_center, mock_same, mock_neighbor, mock_focused, mock_score, mock_send, mock_pyautogui
    ):
        """无邻居时点击锚点中心、落点为其它控件：同样放宽步数上限。"""
        anchor = FakeWrapper(name="投影系", control_type="Text", automation_id="")
        after = FakeWrapper(name="落点", control_type="Edit", automation_id="txtDrop")
        before = FakeWrapper(name="旧焦点", control_type="Button", automation_id="btnOld")
        mock_find.return_value = anchor
        tab_steps = L._TAB_NAV_START_OFFSET_BUFFER + 2
        mock_focused.side_effect = [before, after] + [
            FakeWrapper(name="步{}".format(i), control_type="Edit", automation_id="txt{}".format(i))
            for i in range(tab_steps)
        ]
        cd = _make_control_definition(_make_tab_nav_config(direction="forward", steps=2))
        windows = [MagicMock()]
        L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert mock_send.call_count == tab_steps


class TestTabNavigationFallbackFocusGuard:
    """逻辑防护：set_focus 落点校验与 Tab 步进窗口级防护。"""

    @patch.object(L, "pyautogui")
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_find_focusable_neighbor", return_value=None)
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    def test_set_focus_invalid_falls_back_to_click_when_focus_on_window(
        self, mock_center, mock_same, mock_neighbor, mock_focused, mock_pyautogui
    ):
        """可聚焦锚点 set_focus 未生效（焦点仍停留在顶层窗口）：降级到点击锚点中心。"""
        anchor = FakeWrapper(name="锚点按钮", control_type="Button", automation_id="btnAnchor")
        window_wrapper = FakeWrapper(name="主窗口", control_type="Window", automation_id="")
        before = FakeWrapper(name="旧焦点", control_type="Button", automation_id="btnOld")
        after = FakeWrapper(name="新焦点", control_type="Edit", automation_id="txtNew")
        # 第 1 次=set_focus 落点校验（焦点在 Window → 未生效）；第 2 次=点击前焦点；第 3 次=点击后焦点
        mock_focused.side_effect = [window_wrapper, before, after]
        tab_nav = {"anchorControlId": "btnAnchor", "direction": "forward", "steps": 2}
        start = L._try_focus_anchor(anchor, tab_nav, step_id="step_9", anchor_id="btnAnchor")
        assert start is after
        mock_pyautogui.click.assert_called_once_with(100, 200)

    @patch.object(L, "pyautogui")
    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=0)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "_is_same_wrapper", side_effect=lambda a, b: a is b)
    @patch.object(L, "get_wrapper_center", return_value=(100, 200))
    @patch.object(L, "find_flow_control")
    def test_tab_nav_aborts_when_focus_on_window_after_tab(
        self, mock_find, mock_center, mock_same, mock_focused, mock_score, mock_send, mock_pyautogui
    ):
        """Tab 步进后焦点落在顶层窗口：立即放弃，不再继续 Tab。"""
        anchor = FakeWrapper(name="锚点按钮", control_type="Button", automation_id="btnAnchor")
        window_wrapper = FakeWrapper(name="主窗口", control_type="Window", automation_id="")
        mock_find.return_value = anchor
        # 第 1 次=set_focus 落点校验（焦点=锚点自身）；第 2 次=Tab 1 步后焦点（落在 Window）
        mock_focused.side_effect = [anchor, window_wrapper]
        cd = _make_control_definition(_make_tab_nav_config(direction="forward", steps=5))
        windows = [MagicMock()]
        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert result is None
        assert mock_send.call_count == 1


class TestTwoLevelAnchorLookup:
    """两级锚点查找：轻量 fast 优先（复用主流程 windows），未命中回退递归。"""

    @staticmethod
    def _step_def_with_anchor():
        return {"controls": [{"id": "anchor_btn", "controlType": "Button", "inspectData": {}}]}

    @patch.object(L, "find_flow_control")
    @patch.object(L, "score_control_match", return_value=100)
    @patch.object(L, "wrapper_matches_control_definition", return_value=True)
    @patch.object(L, "iter_fast_locator_candidates")
    @patch.object(L, "_GET_STEP_DEFINITION")
    @patch.object(L, "_apply_self_heal_override", side_effect=lambda s, c, d: d)
    def test_lightweight_hit_skips_recursive(
        self, mock_heal, mock_get_def, mock_fast, mock_match, mock_score, mock_find
    ):
        """fast 查询命中锚点：直接返回，不再调用递归 find_flow_control。"""
        mock_get_def.return_value = self._step_def_with_anchor()
        candidate = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_fast.return_value = [candidate]
        windows = [MagicMock()]
        result = L._try_find_anchor_in_windows(windows, "step_9", "anchor_btn")
        assert result is candidate
        mock_find.assert_not_called()

    @patch.object(L, "iter_fast_locator_candidates", return_value=[])
    @patch.object(L, "_GET_STEP_DEFINITION")
    @patch.object(L, "_apply_self_heal_override", side_effect=lambda s, c, d: d)
    def test_lightweight_miss_returns_none_for_recursive_fallback(
        self, mock_heal, mock_get_def, mock_fast
    ):
        """fast 无命中：返回 None，交由调用方回退完整递归定位。"""
        mock_get_def.return_value = self._step_def_with_anchor()
        windows = [MagicMock()]
        assert L._try_find_anchor_in_windows(windows, "step_9", "anchor_btn") is None

    def test_empty_windows_returns_none(self):
        assert L._try_find_anchor_in_windows([], "step_9", "anchor_btn") is None

    def test_no_anchor_definition_returns_none(self):
        assert L._try_find_anchor_in_windows([MagicMock()], "step_9", "missing_anchor") is None

    @patch.object(L, "pyautogui")
    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=85)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "find_flow_control")
    @patch.object(L, "_try_find_anchor_in_windows")
    def test_tab_navigation_uses_lightweight_anchor_first(
        self, mock_light, mock_find, mock_focused, mock_score, mock_send, mock_pyautogui
    ):
        """主流程：轻量锚点命中时，find_flow_control 不被调用，Tab 正常执行。"""
        anchor = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_light.return_value = anchor
        target = FakeWrapper(name="目标", control_type="Edit", automation_id="txtTarget")
        mock_focused.return_value = target  # set_focus 落点校验与 Tab 步进均返回目标（非 Window）
        cd = _make_control_definition(_make_tab_nav_config(direction="forward", steps=1))
        windows = [MagicMock()]
        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert result is not None
        wrapper, score = result
        assert wrapper is target
        mock_find.assert_not_called()

    @patch.object(L, "pyautogui")
    @patch.object(L, "send_keys")
    @patch.object(L, "score_control_match", return_value=85)
    @patch.object(L, "_get_focused_element")
    @patch.object(L, "find_flow_control")
    @patch.object(L, "_try_find_anchor_in_windows", return_value=None)
    def test_tab_navigation_falls_back_to_recursive_anchor_lookup(
        self, mock_light, mock_find, mock_focused, mock_score, mock_send, mock_pyautogui
    ):
        """主流程：轻量未命中时回退 find_flow_control 递归定位锚点。"""
        anchor = FakeWrapper(name="锚点", control_type="Button", automation_id="anchor_btn")
        mock_find.return_value = anchor
        mock_focused.return_value = anchor
        cd = _make_control_definition(_make_tab_nav_config(direction="forward", steps=1))
        windows = [MagicMock()]
        result = L._try_tab_navigation_fallback(windows, cd, step_id="step1")
        assert result is not None
        mock_find.assert_called_once()
