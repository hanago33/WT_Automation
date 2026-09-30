# encoding: utf-8
"""界面「静默失败 / 缺少确认」类缺陷的回归测试。

覆盖审计中经逐条核实确认的 7 处：

1. `ControlEditorDialog.apply_changes` 无 try/except —— `build_control()` 对空
   target_method / target_value 抛 ValueError，异常直冒到 Tk 按钮回调被吞掉，
   表现为「点了保存当前控件 / 保存并关闭完全没反应」；`on_confirm` 还无条件关窗。
2. `ControlEditDialog.on_save` 只判 `"inspectData" not in flat_item`，
   JSON 里写成 `"inspectData": null` 时会走到 `None["name"] = ...` 抛 TypeError。
3. `wt_flow_graph.load_flow_definition` 的 `json.load` 无保护，流程文件损坏时
   异常直冒到 `<<ComboboxSelected>>` 回调被 Tk 吞掉（切下拉框「没反应」）。
4. 队列窗口状态筛选手工列举 6 项，漏了 STATUS_LABELS 里的「已取消 / 已终止」。
5. `TaskQueueWindow.control_task` `except Exception: return False` 吞掉失败原因。
6. `control_action` 只有「删除」有二次确认，「终止 / 取消」误点即生效。
7. `_service_action` 的「停止服务」无任何确认。

被测对象是真实方法（轻量宿主绑定），不实例化窗口、无需 Tk 交互环境。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Flow_Editor as E
import wt_flow_graph as G
import wt_task_queue_window as Q


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


# ── 1. apply_changes / on_confirm ────────────────────────────────────────

class ApplyChangesValidationTests(unittest.TestCase):
    """校验失败必须提示用户，且不能关窗。"""

    def setUp(self):
        self._patchers = [
            patch.object(E.messagebox, "showerror"),
            patch.object(E.messagebox, "showinfo"),
            patch.object(E.messagebox, "showwarning"),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])
        self.showerror = self.mocks[0]

    def _make(self, build_raises=None):
        dialog = object.__new__(E.ControlEditorDialog)
        dialog.window = FakeWindow()
        dialog.status_var = FakeVar("")
        dialog.dirty = True
        dialog.applied = False
        dialog.result = None
        dialog.control = None

        def _build():
            if build_raises is not None:
                raise build_raises
            return {"id": "ctl_1", "name": "控件A"}

        dialog.build_control = _build
        return dialog

    def test_validation_error_shows_message_and_returns_false(self):
        dialog = self._make(build_raises=ValueError("target_method * 不能为空。"))
        self.assertFalse(dialog.apply_changes())          # 旧实现直接抛异常
        self.assertTrue(self.showerror.called)
        self.assertIn("target_method", self.showerror.call_args[0][1])
        self.assertFalse(dialog.applied)
        self.assertIsNone(dialog.result)

    def test_success_returns_true_and_marks_applied(self):
        dialog = self._make()
        self.assertTrue(dialog.apply_changes())
        self.assertTrue(dialog.applied)
        self.assertEqual(dialog.result["id"], "ctl_1")

    def test_close_after_success_destroys_window(self):
        dialog = self._make()
        dialog.apply_changes(close_after=True)
        self.assertTrue(dialog.window.destroyed)

    def test_on_confirm_keeps_window_open_on_validation_error(self):
        dialog = self._make(build_raises=ValueError("target_value * 不能为空。"))
        dialog.on_confirm()
        self.assertFalse(dialog.window.destroyed, "校验失败时不应关窗丢输入")
        self.assertTrue(self.showerror.called)

    def test_on_confirm_destroys_window_on_success(self):
        dialog = self._make()
        dialog.on_confirm()
        self.assertTrue(dialog.window.destroyed)


# ── 2. ControlEditDialog.on_save 的 inspectData 脏数据 ────────────────────

class ControlEditOnSaveTests(unittest.TestCase):
    """inspectData 缺失或为 null 时都不应崩。"""

    def setUp(self):
        self._patchers = [
            patch.object(E.messagebox, "showwarning"),
            patch.object(E.messagebox, "showerror"),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])

    def _make(self, control):
        dialog = object.__new__(E.ControlEditDialog)
        dialog.window = FakeWindow()
        dialog.control = control
        dialog.result = None
        for name in ("var_name", "var_role", "var_window_title", "var_target_method",
                     "var_target_value", "var_ui_path", "var_quality", "var_quality_reason"):
            setattr(dialog, name, FakeVar(""))
        dialog.var_name.set("控件A")
        return dialog

    def test_inspect_data_null_is_replaced(self):
        dialog = self._make({"id": "ctl_1", "inspectData": None})
        dialog.on_save()                                   # 旧实现 TypeError
        self.assertEqual(dialog.result["inspectData"], {"name": "控件A"})

    def test_inspect_data_missing_is_created(self):
        dialog = self._make({"id": "ctl_1"})
        dialog.on_save()
        self.assertEqual(dialog.result["inspectData"], {"name": "控件A"})

    def test_existing_inspect_data_is_kept(self):
        dialog = self._make({"id": "ctl_1", "inspectData": {"className": "Button", "name": "旧名"}})
        dialog.on_save()
        self.assertEqual(dialog.result["inspectData"]["className"], "Button")
        self.assertEqual(dialog.result["inspectData"]["name"], "控件A")

    def test_blank_name_still_warns_and_keeps_window(self):
        dialog = self._make({"id": "ctl_1"})
        dialog.var_name.set("   ")
        dialog.on_save()
        self.assertIsNone(dialog.result)
        self.assertFalse(dialog.window.destroyed)
        self.assertTrue(self.mocks[0].called)


# ── 3. load_flow_definition 容错 ─────────────────────────────────────────

class LoadFlowDefinitionTests(unittest.TestCase):
    """流程文件损坏时必须返回 None，而不是把异常抛给 Tk 回调。"""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp(prefix="wt_flow_graph_")
        self.addCleanup(shutil.rmtree, self.base_dir, ignore_errors=True)
        self.pkg_dir = os.path.join(self.base_dir, "flow_packages")
        os.makedirs(self.pkg_dir, exist_ok=True)
        self.section_key = sorted(G.SECTION_FLOW_MAP)[0]
        self.path = os.path.join(
            self.pkg_dir, "flow_definition_%s.json" % G.SECTION_FLOW_MAP[self.section_key])

    def _write(self, content):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(content)

    def test_malformed_json_returns_none(self):
        self._write('{"flowPackages": [')                # 半写/损坏
        self.assertIsNone(G.load_flow_definition(self.base_dir, self.section_key))

    def test_non_dict_json_returns_none(self):
        self._write('[1, 2, 3]')
        self.assertIsNone(G.load_flow_definition(self.base_dir, self.section_key))

    def test_missing_file_returns_none(self):
        self.assertIsNone(G.load_flow_definition(self.base_dir, self.section_key))

    def test_unknown_section_returns_none(self):
        self.assertIsNone(G.load_flow_definition(self.base_dir, "no_such_section"))

    def test_valid_file_still_loads(self):
        self._write(json.dumps({
            "flowPackages": [{"stepIds": ["step_1"]}],
            "steps": [{"id": "step_1", "name": "第一步"}],
        }))
        info = G.load_flow_definition(self.base_dir, self.section_key)
        self.assertIsNotNone(info)
        self.assertEqual(len(info["nodes"]), 1)


# ── 4. 状态筛选选项与状态模型同步 ────────────────────────────────────────

class StatusFilterOptionsTests(unittest.TestCase):
    def test_all_status_labels_are_selectable(self):
        values = [value for _label, value in Q.build_status_filter_options()]
        self.assertEqual(values[0], "全部")
        for label in Q.STATUS_LABELS.values():
            self.assertIn(label, values, "状态「%s」无法筛选" % label)

    def test_canceled_and_terminated_present(self):
        values = [value for _label, value in Q.build_status_filter_options()]
        self.assertIn("已取消", values)
        self.assertIn("已终止", values)

    def test_no_duplicate_options(self):
        values = [value for _label, value in Q.build_status_filter_options()]
        self.assertEqual(len(values), len(set(values)))


# ── 5~7. 队列窗口：失败可见性 与 破坏性操作确认 ──────────────────────────

class _FakeThread:
    instances = []

    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        _FakeThread.instances.append(self)

    def start(self):
        pass


class QueueWindowHarness:
    """仅提供被测方法用到的属性。"""

    def __init__(self, selected_task_id="task_1"):
        self._selected_task_id_value = selected_task_id
        self.log_lines = []
        self.stopped = []

    def _selected_task_id(self):
        return self._selected_task_id_value

    def _append_log_text(self, line):
        self.log_lines.append(line)

    def _post_ui(self, callback):
        callback()

    def _friendly_error(self, exc):
        return "ERR:{}".format(exc)

    def _post_json(self, path, payload):
        raise RuntimeError("boom")


def make_queue_harness(selected_task_id="task_1"):
    harness = QueueWindowHarness(selected_task_id)
    for name in ("control_task", "control_action", "_service_action"):
        setattr(harness, name, getattr(Q.TaskQueueWindow, name).__get__(harness, Q.TaskQueueWindow))
    harness.window = FakeWindow()
    harness.on_start_service = None
    harness.on_stop_service = lambda service: harness.stopped.append(service)
    harness._send_control_worker = lambda *_a, **_k: None
    return harness


class ControlTaskFailureTests(unittest.TestCase):
    def test_failure_returns_false_and_is_logged(self):
        harness = make_queue_harness()
        self.assertFalse(harness.control_task("task_1", "terminate"))
        self.assertTrue(harness.log_lines, "失败原因必须写进日志面板")
        self.assertIn("boom", harness.log_lines[-1])

    def test_blank_task_id_is_ignored_without_log(self):
        harness = make_queue_harness()
        self.assertFalse(harness.control_task("   ", "terminate"))
        self.assertEqual(harness.log_lines, [])


class ControlActionConfirmTests(unittest.TestCase):
    """终止/取消/删除都必须先确认；确认取消则不得派发线程。"""

    def setUp(self):
        _FakeThread.instances = []
        self._patchers = [
            patch.object(Q, "threading", SimpleNamespace(Thread=_FakeThread)),
            patch.object(Q.messagebox, "askyesno", return_value=False),
            patch.object(Q.messagebox, "showinfo"),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])
        self.askyesno = self.mocks[1]

    def _run(self, action):
        harness = make_queue_harness()
        harness.control_action(action)
        return harness

    def test_terminate_asks_before_dispatch(self):
        self._run("terminate")
        self.assertTrue(self.askyesno.called)
        self.assertEqual(_FakeThread.instances, [], "用户取消后不应派发操作")

    def test_cancel_asks_before_dispatch(self):
        self._run("cancel")
        self.assertTrue(self.askyesno.called)
        self.assertEqual(_FakeThread.instances, [])

    def test_delete_still_asks(self):
        self._run("delete")
        self.assertTrue(self.askyesno.called)
        self.assertEqual(_FakeThread.instances, [])

    def test_pause_does_not_ask(self):
        """暂停可逆，不应打扰用户。"""
        self._run("pause")
        self.assertFalse(self.askyesno.called)
        self.assertEqual(len(_FakeThread.instances), 1)

    def test_confirmed_terminate_dispatches(self):
        self.askyesno.return_value = True
        self._run("terminate")
        self.assertEqual(len(_FakeThread.instances), 1)

    def test_no_task_selected_shows_hint_and_does_not_ask(self):
        harness = make_queue_harness(selected_task_id="")
        harness.control_action("terminate")
        self.assertFalse(self.askyesno.called)
        self.assertEqual(_FakeThread.instances, [])


class ServiceStopConfirmTests(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            patch.object(Q.messagebox, "askyesno", return_value=False),
            patch.object(Q.messagebox, "showinfo"),
            patch.object(Q.messagebox, "showerror"),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])
        self.askyesno = self.mocks[0]

    def test_stop_service_asks_first(self):
        harness = make_queue_harness()
        harness._service_action("stop", "task")
        self.assertTrue(self.askyesno.called)
        self.assertEqual(harness.stopped, [], "用户取消后不应停止服务")

    def test_confirmed_stop_calls_callback(self):
        self.askyesno.return_value = True
        harness = make_queue_harness()
        harness._service_action("stop", "task")
        self.assertEqual(harness.stopped, ["task"])

    def test_start_does_not_ask(self):
        harness = make_queue_harness()
        harness.on_start_service = lambda service: harness.stopped.append(("start", service))
        harness._service_action("start", "task")
        self.assertFalse(self.askyesno.called)
        self.assertEqual(harness.stopped, [("start", "task")])


if __name__ == "__main__":
    unittest.main()
