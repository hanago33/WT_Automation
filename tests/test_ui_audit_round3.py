# encoding: utf-8
"""UI 审计第三轮修复的回归测试（2026-09-29）。

覆盖 4 组经逐条核实的遗留项：

1. 状态保存双实现收敛：_simple_save_state 统一委托 _save_launcher_state（失败静默）；
2. 单例守卫 6 处：相对区域助手 / 外部采集 / 实时检测器（非模态对话框），
   模板制作器 / 导入控件 / 控件库采集器（子进程）——重复打开时复用/提示而非新建；
3. 流程编辑器关窗时停止定位探针（取消周期 after + 终止子进程）；
4. 设置落盘去抖：高频保存合并为一次写盘，关窗 flush 不丢最后一次改动。

被测对象为真实方法（轻量替身），不实例化窗口、无需 Tk 交互环境。
"""
import os
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Flow_Editor as E
import WT_Launcher as L
from tools.external_capture import launcher_panel as LP


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeWindow:
    def __init__(self, exists=True):
        self._exists = exists
        self.deiconify_called = False
        self.lift_called = False
        self.protocol_set = False
        self.destroyed = False

    def winfo_exists(self):
        return self._exists

    def deiconify(self):
        self.deiconify_called = True

    def lift(self):
        self.lift_called = True

    def protocol(self, *_args):
        self.protocol_set = True

    def destroy(self):
        self.destroyed = True


class FakeRoot:
    def __init__(self):
        self.scheduled = []
        self.cancelled = []
        self.destroyed = False
        self._seq = 0

    def after(self, delay, callback=None):
        self._seq += 1
        self.scheduled.append((delay, callback, self._seq))
        return "after#%d" % self._seq

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)

    def destroy(self):
        self.destroyed = True


class FakeProcess:
    def __init__(self, running=True):
        self._running = running

    def poll(self):
        return None if self._running else 0


# ── 1. 状态保存双实现收敛 ────────────────────────────────────────────────


class SimpleSaveDelegationTests(unittest.TestCase):
    """_simple_save_state 必须委托全量实现，不再自行读改写同一文件。"""

    def test_delegates_to_full_save(self):
        app = object.__new__(L.LauncherApp)
        called = []
        app._save_launcher_state = lambda: called.append(True)
        app._simple_save_state()
        self.assertEqual(called, [True])

    def test_swallows_errors(self):
        app = object.__new__(L.LauncherApp)

        def _boom():
            raise RuntimeError("disk full")

        app._save_launcher_state = _boom
        app._simple_save_state()  # 保持原「静默」语义，不抛出


# ── 2. 单例守卫（非模态对话框）──────────────────────────────────────────


class SingletonDialogGuardTests(unittest.TestCase):
    def _make(self):
        app = object.__new__(L.LauncherApp)
        app.root = FakeRoot()
        app.theme = {}
        app.status_var = FakeVar()
        app.current_step_var = FakeVar()
        app._append_log = lambda *args, **kwargs: None
        return app

    def test_relative_region_helper_reuses_existing(self):
        app = self._make()
        existing = type("D", (), {})()
        existing.window = FakeWindow(exists=True)
        app._relative_region_helper = existing
        with patch.object(L, "RelativeRegionHelperDialog") as ctor:
            app.open_relative_region_helper()
        ctor.assert_not_called()
        self.assertTrue(existing.window.lift_called)

    def test_relative_region_helper_creates_and_records_when_absent(self):
        app = self._make()
        created = type("D", (), {})()
        created.window = FakeWindow(exists=True)
        with patch.object(L, "RelativeRegionHelperDialog", return_value=created) as ctor:
            app.open_relative_region_helper()
        ctor.assert_called_once()
        self.assertIs(app._relative_region_helper, created)

    def test_external_capture_reuses_existing(self):
        app = self._make()
        existing = type("D", (), {})()
        existing.window = FakeWindow(exists=True)
        app._external_capture_dialog = existing
        with patch.object(LP, "ExternalCaptureDialog") as ctor:
            app.open_external_capture()
        ctor.assert_not_called()  # 已存在时不得再构造（旧版无守卫 → 会构造而失败）
        self.assertTrue(existing.window.lift_called)

    def test_external_capture_creates_and_records_when_absent(self):
        app = self._make()
        created = type("D", (), {})()
        created.window = FakeWindow(exists=True)
        with patch.object(LP, "ExternalCaptureDialog", return_value=created) as ctor:
            app.open_external_capture()
        ctor.assert_called_once()
        self.assertIs(app._external_capture_dialog, created)

    def test_live_detector_reuses_existing(self):
        app = self._make()
        existing = type("D", (), {})()
        existing.window = FakeWindow(exists=True)
        app._live_detector_window = existing
        with patch("control_live_detector.ControlLiveDetectorWindow") as ctor:
            app.open_live_detector()
        ctor.assert_not_called()  # 已存在时不得再构造（旧版无守卫 → 会构造而失败）
        self.assertTrue(existing.window.lift_called)

    def test_live_detector_creates_and_records_when_absent(self):
        app = self._make()
        created = type("D", (), {})()
        created.window = FakeWindow(exists=True)
        created.on_close = lambda: None
        with patch("control_live_detector.ControlLiveDetectorWindow", return_value=created) as ctor:
            app.open_live_detector()
        ctor.assert_called_once()
        self.assertIs(app._live_detector_window, created)
        self.assertTrue(created.window.protocol_set)


# ── 2b. 单例守卫（子进程类）──────────────────────────────────────────────


class SingletonProcessGuardTests(unittest.TestCase):
    def _make(self):
        app = object.__new__(L.LauncherApp)
        app.root = FakeRoot()
        app._append_log = lambda *args, **kwargs: None
        return app

    def test_template_builder_skips_when_running(self):
        app = self._make()
        app._template_builder_process = FakeProcess(running=True)
        with patch.object(L.subprocess, "Popen") as popen, \
                patch.object(L.messagebox, "showerror") as err:
            app.open_template_builder()
        popen.assert_not_called()
        err.assert_not_called()

    def test_control_import_skips_when_running(self):
        app = self._make()
        app._control_import_process = FakeProcess(running=True)
        with patch.object(L.subprocess, "Popen") as popen, \
                patch.object(L.messagebox, "showerror") as err:
            app.open_control_import_standalone()
        popen.assert_not_called()
        err.assert_not_called()

    def test_control_map_builder_skips_when_running(self):
        app = self._make()
        app._control_map_builder_process = FakeProcess(running=True)
        with patch.object(L.subprocess, "Popen") as popen, \
                patch.object(L.messagebox, "showerror") as err:
            app.open_control_map_builder()
        popen.assert_not_called()
        err.assert_not_called()


# ── 3. 关窗清理定位探针 ─────────────────────────────────────────────────


class ProbeCloseCleanupTests(unittest.TestCase):
    def _make(self):
        app = object.__new__(E.FlowEditorApp)
        app.root = FakeWindow(exists=True)
        app.dirty = False
        app._stop_concurrent_mode = lambda: None
        app._confirm_discard_form_changes = lambda: True
        return app

    def test_close_stops_running_probe(self):
        app = self._make()
        stopped = []
        app._stop_probe = lambda: stopped.append(True)
        app._probe_proc = object()  # 探针子进程存在
        app._probe_elapsed_timer = None
        app._on_close()
        self.assertEqual(stopped, [True])
        self.assertTrue(app.root.destroyed)

    def test_close_without_probe_does_not_stop(self):
        app = self._make()
        stopped = []
        app._stop_probe = lambda: stopped.append(True)
        app._probe_proc = None
        app._probe_elapsed_timer = None
        app._on_close()
        self.assertEqual(stopped, [])
        self.assertTrue(app.root.destroyed)


# ── 4. 设置落盘去抖 ─────────────────────────────────────────────────────


class StateSaveDebounceTests(unittest.TestCase):
    def _make(self):
        app = object.__new__(L.LauncherApp)
        app.root = FakeRoot()
        app._state_save_after_id = None
        app.process = None
        saves = []
        app._save_launcher_state = lambda: saves.append(True)
        return app, saves

    def test_schedule_coalesces_pending_save(self):
        app, saves = self._make()
        app._schedule_launcher_state_save()
        first_id = app._state_save_after_id
        self.assertIsNotNone(first_id)
        app._schedule_launcher_state_save()
        second_id = app._state_save_after_id
        self.assertNotEqual(first_id, second_id)
        self.assertIn(first_id, app.root.cancelled)  # 旧定时器被取消
        self.assertEqual(saves, [])  # 未到期不写盘

    def test_flush_executes_single_save_and_clears_id(self):
        app, saves = self._make()
        app._schedule_launcher_state_save()
        delay, callback, _seq = app.root.scheduled[-1]
        self.assertEqual(delay, 400)
        callback()  # 模拟到期
        self.assertEqual(saves, [True])
        self.assertIsNone(app._state_save_after_id)

    def test_schedule_falls_back_to_sync_when_root_unavailable(self):
        app, saves = self._make()

        class _BrokenRoot:
            def after(self, *_args, **_kwargs):
                raise RuntimeError("no root")

            def after_cancel(self, *_args):
                pass

        app.root = _BrokenRoot()
        app._schedule_launcher_state_save()
        self.assertEqual(saves, [True])  # 直接同步落盘，改动不丢

    def test_close_cancels_pending_and_flushes(self):
        app, saves = self._make()
        app._schedule_launcher_state_save()
        pending = app._state_save_after_id
        app._on_close()
        self.assertIn(pending, app.root.cancelled)
        self.assertEqual(saves, [True])  # 关窗立即落盘一次
        self.assertIsNone(app._state_save_after_id)

    def test_task_queue_settings_uses_debounced_save(self):
        app, saves = self._make()
        app._save_task_queue_settings("http://127.0.0.1:8000/", "u", "t")
        self.assertEqual(app.task_queue_url, "http://127.0.0.1:8000")
        self.assertIsNotNone(app._state_save_after_id)  # 走了防抖（未立即写盘）
        self.assertEqual(saves, [])

    def test_server_monitor_url_uses_debounced_save(self):
        app, saves = self._make()
        app._save_server_monitor_url("http://127.0.0.1:8767/")
        self.assertEqual(app.server_monitor_url, "http://127.0.0.1:8767")
        self.assertIsNotNone(app._state_save_after_id)
        self.assertEqual(saves, [])


if __name__ == "__main__":
    unittest.main()
