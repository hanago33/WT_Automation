# encoding: utf-8
"""`WT_Flow_Editor.py` 界面交互缺陷的回归测试。

覆盖审计中「已核实」的 5 处缺陷：

1. `FlowEditorApp` 只定义 `self.root`（L5572），从未定义 `self.window`，
   但 `cmd_refresh_step_controls_from_library` / `cmd_repair_flow_audit_issues` /
   `cmd_renumber_step_ids` 共 12 处把它当作 `messagebox` 的 parent →
   一调用就 `AttributeError`（被 Tk 回调吞掉，表现为「点了没反应」或 ID 改了但无完成提示）。
2. `cmd_add_control` 未选中步骤时 `self.steps[None]` → `TypeError`。
3. `_update_action_editor_visibility` 在切到不使用目标控件的动作时会清空已选控件，
   切回来不还原 → 用户只是「路过」另一个动作就丢掉了已选控件。
4. `_show_toast_notification` 用了从未定义的 `self.default_font` → Toast 永远不显示
   （异常被外层 `except` 吞掉）。
5. `_start_concurrent_mode` 里局部 `import queue as queue_module`，
   而 `_poll_concurrent_events` 用 `except queue_module.Empty` → `NameError`。

被测对象是 `FlowEditorApp` / `ControlMapImportDialog` 的真实方法（轻量宿主绑定），
不实例化窗口、无需 Tk 交互环境。
"""
import ast
import os
import sys
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Flow_Editor as E

EDITOR_SOURCE_PATH = os.path.join(PROJECT_DIR, "WT_Flow_Editor.py")


def _read_source():
    with open(EDITOR_SOURCE_PATH, encoding="utf-8") as fh:
        return fh.read()


class FakeVar:
    """tkinter 变量的 get/set 替身。"""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeCombo:
    """ttk.Combobox 替身，只需支持 cget("values")。"""

    def __init__(self, values=()):
        self._values = list(values)

    def cget(self, key):
        assert key == "values"
        return tuple(self._values)


class UndefinedAttributeGuardTests(unittest.TestCase):
    """静态回归：这些属性在对应类中从未定义，一旦被引用就是必崩的 AttributeError。"""

    def test_flow_editor_app_never_reads_self_window(self):
        """FlowEditorApp 只有 self.root；self.window 只属于各 *Dialog 类。"""
        tree = ast.parse(_read_source())
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef) or node.name != "FlowEditorApp":
                continue
            for child in ast.walk(node):
                if (isinstance(child, ast.Attribute) and child.attr == "window"
                        and isinstance(child.value, ast.Name) and child.value.id == "self"):
                    offenders.append(child.lineno)
        self.assertEqual(offenders, [], "FlowEditorApp 内不应出现 self.window，见行 %r" % offenders)

    def test_no_reference_to_undefined_default_font(self):
        """用 AST 判定属性访问，避免把注释里的说明文字也当成引用。"""
        offenders = [
            node.lineno
            for node in ast.walk(ast.parse(_read_source()))
            if isinstance(node, ast.Attribute) and node.attr == "default_font"
        ]
        self.assertEqual(offenders, [], "不应引用未定义的 self.default_font，见行 %r" % offenders)

    def test_queue_module_local_alias_is_gone(self):
        tree = ast.parse(_read_source())
        offenders = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and node.id == "queue_module"
        ]
        self.assertEqual(offenders, [], "局部别名 queue_module 应已移除，见行 %r" % offenders)
        module_level_queue_import = any(
            isinstance(node, ast.Import)
            and any(alias.name == "queue" for alias in node.names)
            for node in tree.body
        )
        self.assertTrue(module_level_queue_import, "queue 必须在模块顶部导入")


class FlowEditorHarness(E.FlowEditorApp):
    """FlowEditorApp 的轻量宿主：var_* 属性按需自动创建，其余用桩。"""

    def __getattr__(self, name):
        if name.startswith("var_"):
            value = FakeVar("")
            object.__setattr__(self, name, value)
            return value
        raise AttributeError(name)


def make_editor_harness():
    harness = object.__new__(FlowEditorHarness)
    harness.root = object()          # 只有 root，刻意不提供 window
    return harness


class CmdRenumberStepIdsTests(unittest.TestCase):
    """缺陷 1：方法收尾用 messagebox(..., parent=self.root) 而不是 self.window。"""

    def setUp(self):
        self._patchers = [
            patch.object(E.messagebox, "askyesno", return_value=True),
            patch.object(E.messagebox, "showinfo"),
            patch.object(E.messagebox, "showerror"),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])
        self.showinfo = self.mocks[1]

    def _make(self, steps):
        harness = make_editor_harness()
        harness.steps = steps
        harness.selected_index = None
        harness._rename_step_id_in_packages = lambda _old, _new: False
        harness._mark_dirty = lambda *_a, **_k: None
        harness._refresh_steps_tree = lambda: None
        harness._refresh_overview = lambda: None
        harness._refresh_flow_packages_view = lambda: None
        return harness

    def test_renumbers_and_reports_with_root_as_parent(self):
        harness = self._make([{"id": "step_5"}, {"id": "step_2"}])
        harness.cmd_renumber_step_ids()          # 旧实现此处 AttributeError
        self.assertEqual([step["id"] for step in harness.steps], ["step_1", "step_2"])
        self.assertTrue(self.showinfo.called)
        self.assertIs(self.showinfo.call_args.kwargs.get("parent"), harness.root)

    def test_no_change_still_reports(self):
        harness = self._make([{"id": "step_1"}])
        harness.cmd_renumber_step_ids()
        self.assertTrue(self.showinfo.called)
        self.assertIs(self.showinfo.call_args.kwargs.get("parent"), harness.root)

    def test_empty_steps_returns_without_confirmation(self):
        harness = self._make([])
        harness.cmd_renumber_step_ids()
        self.assertFalse(self.mocks[0].called)


class CmdAddControlTests(unittest.TestCase):
    """缺陷 2：未选中步骤时不得抛 TypeError，而应给出提示。"""

    def setUp(self):
        self._patchers = [
            patch.object(E.messagebox, "showinfo"),
            patch.object(E.messagebox, "showerror"),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])
        self.showinfo = self.mocks[0]

    def _make(self, steps, selected_index):
        harness = make_editor_harness()
        harness.steps = steps
        harness.selected_index = selected_index
        harness.var_id = FakeVar("step")
        harness.var_window_title = FakeVar("窗口A")
        harness.opened = []

        def _open(control):
            harness.opened.append(control)
            return None          # 用户取消，避免走到后续追加逻辑

        harness._open_control_dialog = _open
        return harness

    def test_without_selected_step_shows_hint(self):
        harness = self._make([], None)
        harness.cmd_add_control()                # 旧实现此处 TypeError
        self.assertEqual(harness.opened, [])
        self.assertTrue(self.showinfo.called)

    def test_control_sequence_counts_selected_step_controls(self):
        harness = self._make([{"id": "step_1", "controls": [{}, {}]}], 0)
        harness.cmd_add_control()
        self.assertEqual(harness.opened[0]["id"], "step_control_3")
        self.assertEqual(harness.opened[0]["windowTitle"], "窗口A")

    def test_first_control_of_empty_step(self):
        harness = self._make([{"id": "step_1", "controls": []}], 0)
        harness.cmd_add_control()
        self.assertEqual(harness.opened[0]["id"], "step_control_1")


class ActionSwitchTargetStashTests(unittest.TestCase):
    """缺陷 3：切走再切回同一个动作时，已选目标控件不应丢失。"""

    def _make(self, action, target, shown_action):
        harness = make_editor_harness()
        harness.var_action = FakeVar(action)
        harness.var_action_type = FakeVar("action")
        harness.var_strategy = FakeVar("action")
        harness.var_target_control_id = FakeVar(target)
        harness._action_target_stash = {}
        harness._shown_action_name = shown_action
        harness.target_control_combo = FakeCombo(["ctl_1 | 按钮A"])
        # 模拟 _update_action_editor_visibility 的真实行为：不使用目标控件的动作会清空该字段
        def _refresh():
            if not E.get_action_schema(harness.var_action.get()).get("target_required"):
                harness.var_target_control_id.set("")
            harness._shown_action_name = harness.var_action.get()
        harness._update_action_editor_visibility = _refresh
        return harness

    def test_switching_away_then_back_restores_target(self):
        harness = self._make("click", "ctl_1", "click")
        harness.var_action.set("sleep")
        harness._on_action_changed()
        self.assertEqual(harness.var_target_control_id.get(), "")
        self.assertEqual(harness._action_target_stash.get("click"), "ctl_1")

        harness.var_action.set("click")
        harness._on_action_changed()
        self.assertEqual(harness.var_target_control_id.get(), "ctl_1 | 按钮A")

    def test_relative_action_switch_also_restores(self):
        """相对区域类动作同样不使用目标控件，也应能还原。"""
        harness = self._make("click", "ctl_1", "click")
        harness.var_action.set("type_text_relative")
        harness._on_action_changed()
        self.assertEqual(harness.var_target_control_id.get(), "")
        harness.var_action.set("click")
        harness._on_action_changed()
        self.assertEqual(harness.var_target_control_id.get(), "ctl_1 | 按钮A")

    def test_nothing_stashed_when_field_already_empty(self):
        harness = self._make("click", "", "click")
        harness.var_action.set("sleep")
        harness._on_action_changed()
        self.assertEqual(harness._action_target_stash, {})

    def test_switching_between_two_target_actions_keeps_each(self):
        harness = self._make("click", "ctl_1", "click")
        harness.var_action.set("sleep")
        harness._on_action_changed()
        harness.var_action.set("double_click")
        harness._on_action_changed()
        # double_click 需要目标控件但没有暂存值，应为空而不是错填
        self.assertEqual(harness.var_target_control_id.get(), "")
        harness.var_action.set("click")
        harness._on_action_changed()
        self.assertEqual(harness.var_target_control_id.get(), "ctl_1 | 按钮A")

    def test_loading_another_step_clears_stash(self):
        """暂存只在同一步骤内有效，换步骤必须清空，避免串味。"""
        harness = make_editor_harness()
        harness._action_target_stash = {"click": "ctl_1"}
        harness._shown_action_name = "click"
        harness.target_control_combo = FakeCombo(["ctl_1 | 按钮A"])
        harness.continue_when_control_combo = FakeCombo([])
        harness._refresh_input_param_options = lambda: None
        harness._sync_post_input_controls = lambda *_a, **_k: None
        harness._update_action_editor_visibility = lambda: None
        harness._load_action_editor_from_config({"actionConfig": {"action": "sleep"}})
        self.assertEqual(harness._action_target_stash, {})


if __name__ == "__main__":
    unittest.main()
