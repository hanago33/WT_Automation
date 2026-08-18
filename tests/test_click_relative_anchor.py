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
