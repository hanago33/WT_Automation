# encoding: utf-8
"""WT 节点定位接线（mup_login_auto 第三步）的纯逻辑单测。

不启动 GUI、不连接 MUP：用假 UIA 窗口验证 automation_id / name / point
三种定位器，以及占位空值时的挂起与绿色弧兜底分支。
"""
import os
import sys
import unittest
from unittest import mock

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import mup_login_auto as mla


class _Rect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _Info:
    def __init__(self, automation_id):
        self.automation_id = automation_id


class _Element:
    def __init__(self, automation_id="", text="", rect=None):
        self.element_info = _Info(automation_id)
        self._text = text
        self._rect = rect or _Rect(0, 0, 0, 0)

    def window_text(self):
        return self._text

    def rectangle(self):
        return self._rect


class _Window:
    def __init__(self, elements):
        self._elements = elements

    def descendants(self):
        return self._elements


class _App:
    def __init__(self, window):
        self._window = window

    def windows(self):
        return [self._window]


WT_RECT = _Rect(1200, 560, 1260, 620)


class WtNodeLocatorTests(unittest.TestCase):
    def test_automation_id_resolves_center(self):
        w = _Window([_Element("MainPage_Button_Central"),
                     _Element("WT_Node", rect=WT_RECT)])
        locator = mla.WtNodeLocator("automation_id", "WT_Node")
        self.assertEqual(mla.resolve_wt_target(w, locator), (1230, 590))

    def test_name_locator_resolves_center(self):
        w = _Window([_Element(text="WT", rect=WT_RECT)])
        locator = mla.WtNodeLocator("name", "WT")
        self.assertEqual(mla.resolve_wt_target(w, locator), (1230, 590))

    def test_point_locator_passthrough(self):
        locator = mla.WtNodeLocator("point", (10, 20))
        self.assertEqual(mla.resolve_wt_target(_Window([]), locator), (10, 20))

    def test_empty_placeholder_is_suspended(self):
        w = _Window([_Element("WT_Node", rect=WT_RECT)])
        self.assertIsNone(mla.resolve_wt_target(w, mla.WtNodeLocator("automation_id", "")))

    def test_missing_id_returns_none(self):
        w = _Window([_Element("Other")])
        self.assertIsNone(mla.resolve_wt_target(w, mla.WtNodeLocator("automation_id", "WT_Node")))

    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):
            mla.WtNodeLocator("color", "green")

    def test_bad_point_rejected(self):
        with self.assertRaises(ValueError):
            mla.WtNodeLocator("point", "1326,595")


class EnterDashboardWiringTests(unittest.TestCase):
    def test_already_on_dashboard_returns_true_without_click(self):
        w = _Window([_Element("DashboardView_Button_Refresh")])
        with mock.patch.object(mla, "_click_and_wait_dashboard") as click:
            self.assertTrue(mla.enter_dashboard(_App(w), timeout=1))
            click.assert_not_called()

    def test_placeholder_uses_green_fallback(self):
        w = _Window([])
        placeholder = mla.WtNodeLocator("automation_id", "")
        with mock.patch.object(mla, "WT_NODE_LOCATOR", placeholder):
            with mock.patch.object(mla, "_green_node_center", return_value=(11, 22)):
                with mock.patch.object(mla, "_click_and_wait_dashboard", return_value=True) as click:
                    self.assertTrue(mla.enter_dashboard(_App(w), timeout=1))
                    click.assert_called_once_with(mock.ANY, (11, 22), 1)

    def test_configured_locator_is_used_before_fallback(self):
        w = _Window([_Element("WT_Node", rect=WT_RECT)])
        locator = mla.WtNodeLocator("automation_id", "WT_Node")
        with mock.patch.object(mla, "_green_node_center") as green:
            with mock.patch.object(mla, "_click_and_wait_dashboard", return_value=True) as click:
                self.assertTrue(mla.enter_dashboard(_App(w), timeout=1, wt_locator=locator))
                click.assert_called_once_with(mock.ANY, (1230, 590), 1)
                green.assert_not_called()


if __name__ == "__main__":
    unittest.main()

