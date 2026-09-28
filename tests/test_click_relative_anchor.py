# encoding: utf-8

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import wt_flow_locator as locator


class FakeRect:
    def __init__(self, left, top, right, bottom):
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class FakeAnchor:
    def rectangle(self):
        return FakeRect(100, 200, 300, 400)


def test_click_relative_anchor_uses_anchor_center_plus_offset(monkeypatch):
    clicked = []
    monkeypatch.setattr(locator, "find_flow_control", lambda *args, **kwargs: FakeAnchor())
    monkeypatch.setattr(locator, "_activate_process_main_window", lambda *args, **kwargs: None)
    monkeypatch.setattr(locator, "_get_top_level_hwnd_safe", lambda wrapper: 0)
    monkeypatch.setattr(locator.pyautogui, "click", lambda x, y: clicked.append((x, y)))

    ok, meta = locator.click_relative_anchor(
        "step_anchor", "anchor_1", offset=(10, -5), timeout_seconds=1
    )

    assert ok is True
    assert clicked == [(210, 295)]
    assert meta["clickPoint"] == {"x": 210, "y": 295}
    assert meta["anchorAlign"] == "center"
    assert meta["anchorBase"] == {"x": 200, "y": 300}


def test_click_relative_anchor_align_right_uses_right_edge(monkeypatch):
    clicked = []
    monkeypatch.setattr(locator, "find_flow_control", lambda *args, **kwargs: FakeAnchor())
    monkeypatch.setattr(locator, "_activate_process_main_window", lambda *args, **kwargs: None)
    monkeypatch.setattr(locator, "_get_top_level_hwnd_safe", lambda wrapper: 0)
    monkeypatch.setattr(locator.pyautogui, "click", lambda x, y: clicked.append((x, y)))

    # FakeAnchor rect = (100, 200, 300, 400)；right 基准 = (300, 300)，再向左偏移 -20
    ok, meta = locator.click_relative_anchor(
        "step_anchor", "anchor_1", offset=(-20, 0), timeout_seconds=1, anchor_align="right"
    )

    assert ok is True
    assert clicked == [(280, 300)]
    assert meta["clickPoint"] == {"x": 280, "y": 300}
    assert meta["anchorAlign"] == "right"
    assert meta["anchorBase"] == {"x": 300, "y": 300}


def test_click_relative_anchor_align_left_uses_left_edge(monkeypatch):
    clicked = []
    monkeypatch.setattr(locator, "find_flow_control", lambda *args, **kwargs: FakeAnchor())
    monkeypatch.setattr(locator, "_activate_process_main_window", lambda *args, **kwargs: None)
    monkeypatch.setattr(locator, "_get_top_level_hwnd_safe", lambda wrapper: 0)
    monkeypatch.setattr(locator.pyautogui, "click", lambda x, y: clicked.append((x, y)))

    ok, meta = locator.click_relative_anchor(
        "step_anchor", "anchor_1", offset=(10, 0), timeout_seconds=1, anchor_align="left"
    )

    assert ok is True
    assert clicked == [(110, 300)]
    assert meta["clickPoint"] == {"x": 110, "y": 300}
    assert meta["anchorAlign"] == "left"
