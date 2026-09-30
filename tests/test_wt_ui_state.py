# encoding: utf-8
"""wt_ui_state 界面状态持久化测试（待修改清单 #6）。"""
import json
import os

import pytest

import wt_ui_state


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "ui_state.json"
    monkeypatch.setenv("WT_UI_STATE_FILE", str(path))
    return path


def test_load_missing_file_returns_empty_dict(state_file):
    assert wt_ui_state.load("tool_a") == {}


def test_save_then_load_roundtrip_isolated_per_key(state_file):
    assert wt_ui_state.save("tool_a", {"geometry": "800x600+10+20", "flag": True})
    assert wt_ui_state.save("tool_b", {"op": "replace"})
    assert wt_ui_state.load("tool_a") == {"geometry": "800x600+10+20", "flag": True}
    assert wt_ui_state.load("tool_b") == {"op": "replace"}


def test_save_second_key_does_not_clobber_first(state_file):
    wt_ui_state.save("tool_a", {"a": 1})
    wt_ui_state.save("tool_b", {"b": 2})
    assert wt_ui_state.load("tool_a") == {"a": 1}


def test_corrupted_json_loads_empty_and_save_recovers(state_file):
    state_file.write_text("{ this is not json", encoding="utf-8")
    assert wt_ui_state.load("tool_a") == {}
    assert wt_ui_state.save("tool_a", {"k": "v"})
    assert wt_ui_state.load("tool_a") == {"k": "v"}


def test_non_scalar_values_are_filtered(state_file):
    wt_ui_state.save("tool_a", {
        "ok": "文本",
        "count": 3,
        "ratio": 0.5,
        "flag": False,
        "files": ["a.txt", "b.txt"],   # 业务数据不落盘
        "nested": {"x": 1},
        "nothing": None,
    })
    assert wt_ui_state.load("tool_a") == {
        "ok": "文本", "count": 3, "ratio": 0.5, "flag": False,
    }


def test_load_non_dict_section_returns_empty(state_file):
    state_file.write_text(json.dumps({"tool_a": "garbage"}), encoding="utf-8")
    assert wt_ui_state.load("tool_a") == {}


def test_state_file_env_override_is_honored(state_file, tmp_path, monkeypatch):
    other = tmp_path / "other.json"
    monkeypatch.setenv("WT_UI_STATE_FILE", str(other))
    wt_ui_state.save("tool_a", {"k": "v"})
    assert other.exists()
    assert not state_file.exists()  # 未污染默认文件


def test_default_path_under_appdata_wt_automation(monkeypatch):
    monkeypatch.setenv("APPDATA", r"C:\Users\x\AppData\Roaming")
    monkeypatch.delenv("WT_UI_STATE_FILE", raising=False)
    assert wt_ui_state.state_path() == \
        r"C:\Users\x\AppData\Roaming" + os.sep + "wt_automation" + os.sep + "ui_state.json"


# ── 几何纠偏 ──────────────────────────────────────────────────────────────────

def test_sanitize_keeps_onscreen_geometry():
    assert wt_ui_state.sanitize_geometry("800x600+10+20") == "800x600+10+20"


def test_sanitize_resets_offscreen_window():
    # 保存于第二显示器（x=2560），拔掉后完全不可见 → 复位到安全位置
    result = wt_ui_state.sanitize_geometry("800x600+2560+100", virtual_rect=(0, 0, 1920, 1080))
    assert result == "800x600+40+40"


def test_sanitize_resets_window_beyond_bottom():
    result = wt_ui_state.sanitize_geometry("800x600+10+3000", virtual_rect=(0, 0, 1920, 1080))
    assert result == "800x600+40+40"


def test_sanitize_clamps_oversize_window():
    result = wt_ui_state.sanitize_geometry("5000x4000+10+10", virtual_rect=(0, 0, 1920, 1080))
    assert result == "1920x1080+10+10"


def test_sanitize_multi_monitor_negative_origin_preserved():
    # 第二显示器在主屏左侧（虚拟桌面 x=-1920）：合法位置不得被误杀
    result = wt_ui_state.sanitize_geometry("800x600-1800+100", virtual_rect=(-1920, 0, 3840, 1080))
    assert result == "800x600-1800+100"


def test_sanitize_malformed_returns_none():
    assert wt_ui_state.sanitize_geometry("not-a-geometry") is None
    assert wt_ui_state.sanitize_geometry("") is None
    assert wt_ui_state.sanitize_geometry(None) is None


def test_apply_window_geometry_skips_when_missing():
    class FakeWindow:
        def __init__(self):
            self.called = None

        def geometry(self, value):
            self.called = value

    win = FakeWindow()
    assert not wt_ui_state.apply_window_geometry(win, None)
    assert win.called is None
    assert wt_ui_state.apply_window_geometry(win, "800x600+10+20")
    assert win.called == "800x600+10+20"


def test_sanitize_defaults_to_probed_virtual_screen(monkeypatch):
    """审计 P2 回归：不传 virtual_rect 时必须探测真实虚拟桌面。

    2K/4K 机器上右侧屏幕的合法窗口不得被 1920×1080 写死兜底误杀。
    """
    monkeypatch.setattr(wt_ui_state, "virtual_screen_rect", lambda: (0, 0, 2560, 1440))
    assert wt_ui_state.sanitize_geometry("800x600+2000+100") == "800x600+2000+100"
    assert wt_ui_state.sanitize_geometry("1200x1400+100+50") == "1200x1400+100+50"


def test_sanitize_falls_back_when_probe_unavailable(monkeypatch):
    """探测失败时才退回 1920×1080 近似。"""
    monkeypatch.setattr(wt_ui_state, "virtual_screen_rect", lambda: None)
    assert wt_ui_state.sanitize_geometry("800x600+2000+100") == "800x600+40+40"


def test_save_tmp_file_is_pid_suffixed(state_file, monkeypatch):
    """多进程同时关窗不争用同一 .tmp（审计 P3）。"""
    import wt_ui_state as m
    seen = {}
    real_replace = os.replace

    def spy_replace(src, dst):
        seen["tmp"] = os.path.basename(src)
        return real_replace(src, dst)

    monkeypatch.setattr(m.os, "replace", spy_replace)
    wt_ui_state.save("tool_a", {"k": "v"})
    assert ".tmp" in seen["tmp"]
    assert str(os.getpid()) in seen["tmp"]
