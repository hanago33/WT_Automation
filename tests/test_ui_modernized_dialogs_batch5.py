# encoding: utf-8
"""第五期流程编辑核心页面UI现代化重构（Candidate A/B/C）的单元与回归测试套件。

覆盖：
1. `WT_Flow_Editor.py::NewDefaultChainDialog`（Candidate C: 新建默认链路保存弹窗）：
   - 目录与文件名称路径构造与 .json 自动补全；
   - 目标路径存在性实时探针与重名防覆盖预警；
   - 快速填充快捷胶囊逻辑；
   - 校验拦截与确认取消生命周期。
2. `WT_Flow_Editor.py::ControlEditorDialog`（Candidate A: 细分控件精确编辑对话框）：
   - 实时三要素（ID, target_method, target_value）校验徽标状态联动；
   - 属性快速填入定位（_apply_field_to_locator）；
   - 定位表达式快速复制（copy_locator_expression）；
   - 属性快速筛选检索（_filter_parsed_fields）；
   - 控件校验与保存容错（apply_changes / build_control）；
   - Tab 导航配置序列化。
3. `WT_Flow_Editor.py::SemiAutoInspectCollectorDialog`（Candidate B: 半自动控件采集器）：
   - 采集指南折叠/展开动态切换；
   - 监听状态徽标（待命 / 交互点击监听 / 剪贴板监听）联动；
   - 候选控件列表实时检索过滤与计数徽标；
   - 候选定位表达式快速复制；
   - 单选导入、批量导入与取消退出清理。
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Flow_Editor as E


# ── 测试轻量替身 ────────────────────────────────────────────────────────


class FakeVar:
    def __init__(self, value=""):
        self._value = value
        self._callbacks = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for cb in list(self._callbacks):
            try:
                cb()
            except TypeError:
                cb(None, None, None)

    def trace_add(self, mode, callback):
        self._callbacks.append(callback)


class FakeWindow:
    def __init__(self):
        self.destroyed = False
        self.clipboard_content = ""
        self._bindings = {}

    def destroy(self):
        self.destroyed = True

    def clipboard_clear(self):
        self.clipboard_content = ""

    def clipboard_append(self, text):
        self.clipboard_content = str(text)

    def bind(self, sequence, func):
        self._bindings[sequence] = func

    def protocol(self, name, func):
        self._bindings[name] = func

    def after(self, ms, func):
        pass


class FakeWidget:
    def __init__(self):
        self.config_kwargs = {}
        self.packed = True

    def config(self, **kwargs):
        self.config_kwargs.update(kwargs)

    def configure(self, **kwargs):
        self.config_kwargs.update(kwargs)

    def pack(self, **kwargs):
        self.packed = True

    def pack_forget(self):
        self.packed = False

    def focus_set(self):
        pass

    def select_range(self, start, end):
        pass


class FakeText:
    def __init__(self, initial=""):
        self.content = initial

    def get(self, start="1.0", end="end"):
        return self.content

    def delete(self, start, end=None):
        self.content = ""

    def insert(self, index, text):
        self.content += str(text)


class FakeTreeview:
    def __init__(self):
        self.items = {}
        self._selection = ()

    def delete(self, *items):
        if not items:
            self.items.clear()
        else:
            for item in items:
                self.items.pop(str(item), None)

    def get_children(self):
        return list(self.items.keys())

    def insert(self, parent, index, iid=None, values=(), **kwargs):
        key = str(iid if iid is not None else len(self.items))
        self.items[key] = values
        return key

    def selection(self):
        return self._selection

    def selection_set(self, *items):
        self._selection = tuple(str(x) for x in items)

    def heading(self, col, text=""):
        pass

    def column(self, col, **kwargs):
        pass

    def bind(self, seq, func):
        pass


# ── 1. NewDefaultChainDialog 测试 ─────────────────────────────────────────


class NewDefaultChainDialogTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp(prefix="wt_chain_test_")
        self._patchers = [
            patch.object(E.messagebox, "showerror"),
            patch.object(E.messagebox, "askyesno", return_value=True),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])
        self.showerror = self.mocks[0]
        self.askyesno = self.mocks[1]

    def _make(self, last_directory="", last_name=""):
        dialog = object.__new__(E.NewDefaultChainDialog)
        dialog.parent = None
        dialog.result = None
        dialog.var_directory = FakeVar(last_directory or self.tmp_dir)
        dialog.var_name = FakeVar(last_name or "")
        dialog.warn_var = FakeVar("")
        dialog.probe_status_var = FakeVar("")
        dialog.probe_icon_label = FakeWidget()
        dialog.probe_msg_label = FakeWidget()
        dialog.window = FakeWindow()
        return dialog

    def test_build_path_appends_json_suffix(self):
        dialog = self._make(last_name="flow_standard")
        path = dialog._build_path()
        self.assertTrue(path.endswith("flow_standard.json"))
        self.assertEqual(os.path.dirname(path), self.tmp_dir)

    def test_build_path_returns_none_if_directory_or_name_empty(self):
        dialog = self._make(last_name="")
        self.assertIsNone(dialog._build_path())

        dialog.var_directory.set("")
        dialog.var_name.set("test")
        self.assertIsNone(dialog._build_path())

    def test_refresh_warning_detects_non_existent_file(self):
        dialog = self._make(last_name="new_test_flow")
        dialog._refresh_warning()
        self.assertEqual(dialog.warn_var.get(), "")
        self.assertIn("目标路径就绪", dialog.probe_status_var.get())
        self.assertEqual(dialog.probe_icon_label.config_kwargs.get("text"), "✓")

    def test_refresh_warning_detects_existing_file(self):
        existing_file = os.path.join(self.tmp_dir, "existing_flow.json")
        with open(existing_file, "w", encoding="utf-8") as f:
            f.write("{}")

        dialog = self._make(last_name="existing_flow")
        dialog._refresh_warning()
        self.assertIn("该路径已存在文件", dialog.warn_var.get())
        self.assertIn("同名文件已存在", dialog.probe_status_var.get())
        self.assertEqual(dialog.probe_icon_label.config_kwargs.get("text"), "⚠")

    def test_on_ok_validates_required_fields(self):
        dialog = self._make(last_name="")
        dialog.on_ok()
        self.assertTrue(self.showerror.called)
        self.assertIsNone(dialog.result)

        self.showerror.reset_mock()
        dialog.var_directory.set("")
        dialog.var_name.set("valid_name")
        dialog.on_ok()
        self.assertTrue(self.showerror.called)
        self.assertIsNone(dialog.result)

    def test_on_ok_success_returns_metadata_dict(self):
        dialog = self._make(last_name="my_flow")
        dialog.on_ok()
        self.assertIsNotNone(dialog.result)
        self.assertEqual(dialog.result["name"], "my_flow")
        self.assertTrue(dialog.result["path"].endswith("my_flow.json"))
        self.assertEqual(dialog.result["directory"], self.tmp_dir)
        self.assertTrue(dialog.window.destroyed)

    def test_on_cancel_resets_result_and_destroys(self):
        dialog = self._make(last_name="flow_standard")
        dialog.on_cancel()
        self.assertIsNone(dialog.result)
        self.assertTrue(dialog.window.destroyed)


# ── 2. ControlEditorDialog 测试 ───────────────────────────────────────────


class ControlEditorDialogTests(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            patch.object(E.messagebox, "showerror"),
            patch.object(E.messagebox, "showinfo"),
            patch.object(E.messagebox, "showwarning"),
            patch.object(E.messagebox, "askyesnocancel", return_value=False),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])
        self.showerror = self.mocks[0]
        self.showinfo = self.mocks[1]

    def _make(self, ctl_dict=None):
        dialog = object.__new__(E.ControlEditorDialog)
        dialog.parent = None
        dialog.result = None
        dialog.dirty = False
        dialog.applied = False
        dialog._loading = False
        dialog.control = ctl_dict or {
            "id": "ctl_confirm_btn",
            "name": "确认按钮",
            "role": "主操作",
            "windowTitle": "计算器",
            "targetMethod": "automation_id",
            "targetValue": "btn_confirm",
        }

        dialog.var_id = FakeVar(dialog.control.get("id", ""))
        dialog.var_name = FakeVar(dialog.control.get("name", ""))
        dialog.var_role = FakeVar(dialog.control.get("role", ""))
        dialog.var_enabled = FakeVar(True)
        dialog.var_window_title = FakeVar(dialog.control.get("windowTitle", ""))
        dialog.var_target_method = FakeVar(dialog.control.get("targetMethod", ""))
        dialog.var_target_value = FakeVar(dialog.control.get("targetValue", ""))
        dialog.var_template_key = FakeVar("")
        dialog.var_ui_path = FakeVar("")
        dialog.var_label_text = FakeVar("")
        dialog.var_related_label_name = FakeVar("")

        # Inspect 数据字段
        for field in (
            "how_found", "control_name", "class_name", "control_type",
            "localized_control_type", "automation_id", "framework_id",
            "native_window_handle", "bounding_rectangle", "process_id",
            "runtime_id", "is_enabled", "is_offscreen", "is_keyboard_focusable",
            "has_keyboard_focus", "legacy_name", "legacy_role", "legacy_state",
            "provider_description", "first_child", "last_child", "next", "previous",
        ):
            setattr(dialog, f"var_{field}", FakeVar(""))

        dialog.var_tab_anchor_id = FakeVar("")
        dialog.var_tab_direction = FakeVar("forward")
        dialog.var_tab_steps = FakeVar("1")
        dialog.var_prefer_tab_nav = FakeVar(False)
        dialog.var_tab_click_twice = FakeVar(False)

        dialog.status_var = FakeVar("")
        dialog.validation_badge_var = FakeVar("")
        dialog.validation_badge_label = FakeWidget()
        dialog.search_filter_var = FakeVar("")
        dialog._parsed_field_rows = []

        dialog.raw_text = FakeText()
        dialog.aux_checks_text = FakeText()
        dialog.children_text = FakeText()
        dialog.ancestors_text = FakeText()
        dialog.patterns_text = FakeText()
        dialog.notes_text = FakeText()
        dialog.window = FakeWindow()
        return dialog

    def test_validation_state_all_complete(self):
        dialog = self._make()
        dialog._refresh_validation_state()
        self.assertIn("✓ 定位三要素完整", dialog.validation_badge_var.get())
        self.assertIn("automation_id", dialog.validation_badge_var.get())

    def test_validation_state_missing_id(self):
        dialog = self._make()
        dialog.var_id.set("")
        dialog.var_name.set("")
        dialog._refresh_validation_state()
        self.assertIn("❌ 缺少控件ID", dialog.validation_badge_var.get())

    def test_validation_state_missing_target_method(self):
        dialog = self._make()
        dialog.var_target_method.set("")
        dialog._refresh_validation_state()
        self.assertIn("⚠ 缺少 target_method", dialog.validation_badge_var.get())

    def test_validation_state_missing_target_value(self):
        dialog = self._make()
        dialog.var_target_value.set("")
        dialog._refresh_validation_state()
        self.assertIn("⚠ 缺少 target_value", dialog.validation_badge_var.get())

    def test_apply_field_to_locator_updates_values_and_marks_dirty(self):
        dialog = self._make()
        dialog._apply_field_to_locator("automation_id", "new_auto_id_123")
        self.assertEqual(dialog.var_target_method.get(), "automation_id")
        self.assertEqual(dialog.var_target_value.get(), "new_auto_id_123")
        self.assertTrue(dialog.dirty)
        self.assertIn("已将 [automation_id] 设为目标定位", dialog.status_var.get())

    def test_apply_field_to_locator_ignores_empty_value(self):
        dialog = self._make()
        old_val = dialog.var_target_value.get()
        dialog._apply_field_to_locator("class_name", "")
        self.assertEqual(dialog.var_target_value.get(), old_val)
        self.assertIn("字段内容为空", dialog.status_var.get())

    def test_copy_locator_expression(self):
        dialog = self._make()
        dialog.copy_locator_expression()
        self.assertEqual(dialog.window.clipboard_content, "automation_id:btn_confirm")
        self.assertIn("已复制定位表达式", dialog.status_var.get())

    def test_copy_locator_expression_empty_shows_info(self):
        dialog = self._make()
        dialog.var_target_method.set("")
        dialog.var_target_value.set("")
        dialog.copy_locator_expression()
        self.assertTrue(self.showinfo.called)

    def test_filter_parsed_fields(self):
        dialog = self._make()
        w1 = FakeWidget()
        w2 = FakeWidget()
        v1 = FakeVar("WindowsForms10.BUTTON")
        v2 = FakeVar("OK")
        dialog._parsed_field_rows = [
            (w1, "ClassName", v1),
            (w2, "Name", v2),
        ]

        dialog.search_filter_var.set("class")
        dialog._filter_parsed_fields()
        self.assertTrue(w1.packed)
        self.assertFalse(w2.packed)

        dialog.search_filter_var.set("OK")
        dialog._filter_parsed_fields()
        self.assertFalse(w1.packed)
        self.assertTrue(w2.packed)

        dialog.search_filter_var.set("")
        dialog._filter_parsed_fields()
        self.assertTrue(w1.packed)
        self.assertTrue(w2.packed)

    def test_build_control_serializes_tab_navigation(self):
        dialog = self._make()
        dialog.var_tab_anchor_id.set("anchor_btn")
        dialog.var_tab_steps.set("3")
        dialog.var_tab_direction.set("backward")
        dialog.var_prefer_tab_nav.set(True)
        dialog.var_tab_click_twice.set(True)

        ctl = dialog.build_control()
        self.assertIn("tabNavigation", ctl)
        self.assertEqual(ctl["tabNavigation"]["anchorControlId"], "anchor_btn")
        self.assertEqual(ctl["tabNavigation"]["steps"], 3)
        self.assertEqual(ctl["tabNavigation"]["direction"], "backward")
        self.assertTrue(ctl["tabNavigation"]["clickTwiceToExpand"])
        self.assertTrue(ctl["preferTabNavigation"])


# ── 3. SemiAutoInspectCollectorDialog 测试 ─────────────────────────────────


class SemiAutoInspectCollectorDialogTests(unittest.TestCase):
    def setUp(self):
        self._patchers = [
            patch.object(E.messagebox, "showerror"),
            patch.object(E.messagebox, "showinfo"),
            patch.object(E.messagebox, "askyesno", return_value=True),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])
        self.showerror = self.mocks[0]
        self.showinfo = self.mocks[1]
        self.askyesno = self.mocks[2]

    def _make(self, existing=None):
        dialog = object.__new__(E.SemiAutoInspectCollectorDialog)
        dialog.parent = None
        dialog.result = None
        dialog.step_name = "测试步骤"
        dialog.default_window_title = "目标软件主界面"
        dialog.existing_controls = existing or []
        dialog.captured_controls = []
        dialog._known_raw_texts = set()
        dialog._monitoring = False
        dialog._interactive_monitoring = False
        dialog._mouse_listener = None
        dialog._keyboard_listener = None
        dialog._win32_hook = None
        dialog._tips_expanded = False

        dialog.var_target_window = FakeVar("目标软件")
        dialog.var_backend = FakeVar("uia")
        dialog.status_var = FakeVar("")
        dialog.listener_badge_var = FakeVar("")
        dialog.listener_badge_label = FakeWidget()
        dialog.candidate_filter_var = FakeVar("")
        dialog.candidate_count_var = FakeVar("")
        dialog.current_candidate_title_var = FakeVar("")

        dialog.tips_body_frame = FakeWidget()
        dialog.tips_toggle_btn = FakeWidget()
        dialog.candidate_tree = FakeTreeview()
        dialog.preview_text = FakeText()
        dialog.window = FakeWindow()
        return dialog

    def test_toggle_tips_switches_visibility(self):
        dialog = self._make()
        self.assertFalse(dialog._tips_expanded)

        dialog._toggle_tips()
        self.assertTrue(dialog._tips_expanded)
        self.assertTrue(dialog.tips_body_frame.packed)
        self.assertIn("收起", dialog.tips_toggle_btn.config_kwargs.get("text", ""))

        dialog._toggle_tips()
        self.assertFalse(dialog._tips_expanded)
        self.assertFalse(dialog.tips_body_frame.packed)
        self.assertIn("展开", dialog.tips_toggle_btn.config_kwargs.get("text", ""))

    def test_update_listener_badge_states(self):
        dialog = self._make()
        dialog._update_listener_badge()
        self.assertIn("○ 监听已停止 (待命)", dialog.listener_badge_var.get())

        dialog._monitoring = True
        dialog._update_listener_badge()
        self.assertIn("● 剪贴板监听中", dialog.listener_badge_var.get())

        dialog._interactive_monitoring = True
        dialog._update_listener_badge()
        self.assertIn("● 交互点击监听中", dialog.listener_badge_var.get())

    def test_start_and_stop_monitor(self):
        dialog = self._make()
        dialog._get_clipboard_text = lambda: "dummy"
        dialog.start_monitor()
        self.assertTrue(dialog._monitoring)
        self.assertIn("● 剪贴板监听中", dialog.listener_badge_var.get())

        dialog.stop_monitor()
        self.assertFalse(dialog._monitoring)
        self.assertIn("○ 监听已停止 (待命)", dialog.listener_badge_var.get())

    def test_refresh_candidates_and_filter(self):
        dialog = self._make()
        dialog.captured_controls = [
            {"name": "btn_login", "targetMethod": "automation_id", "targetValue": "btnLogin", "windowTitle": "登录窗口"},
            {"name": "input_user", "targetMethod": "name", "targetValue": "用户名", "windowTitle": "登录窗口"},
            {"name": "btn_cancel", "targetMethod": "class_name", "targetValue": "Button", "windowTitle": "通用弹窗"},
        ]

        dialog._refresh_candidates()
        self.assertEqual(len(dialog.candidate_tree.get_children()), 3)
        self.assertEqual(dialog.candidate_count_var.get(), "候选控件 (3 个)")

        # 搜索 "login" 应该过滤出 1 个
        dialog.candidate_filter_var.set("login")
        dialog._refresh_candidates()
        self.assertEqual(len(dialog.candidate_tree.get_children()), 1)

        # 搜索 "Button" 应该过滤出 1 个
        dialog.candidate_filter_var.set("button")
        dialog._refresh_candidates()
        self.assertEqual(len(dialog.candidate_tree.get_children()), 1)

    def test_copy_candidate_locator(self):
        dialog = self._make()
        dialog.captured_controls = [
            {"name": "btn_login", "targetMethod": "automation_id", "targetValue": "btnLogin", "windowTitle": "登录窗口"},
        ]
        dialog.candidate_tree.selection_set("0")
        dialog.copy_candidate_locator()
        self.assertEqual(dialog.window.clipboard_content, "automation_id:btnLogin")
        self.assertIn("已复制候选定位表达式", dialog.status_var.get())

    def test_import_selected_sets_single_result_and_destroys(self):
        dialog = self._make()
        dialog.captured_controls = [
            {"name": "btn_1", "targetMethod": "name", "targetValue": "A"},
            {"name": "btn_2", "targetMethod": "name", "targetValue": "B"},
        ]
        dialog.candidate_tree.selection_set("1")
        dialog.import_selected()
        self.assertEqual(len(dialog.result), 1)
        self.assertEqual(dialog.result[0]["name"], "btn_2")
        self.assertTrue(dialog.window.destroyed)

    def test_import_all_sets_full_result_and_destroys(self):
        dialog = self._make()
        dialog.captured_controls = [
            {"name": "btn_1"},
            {"name": "btn_2"},
        ]
        dialog.import_all()
        self.assertEqual(len(dialog.result), 2)
        self.assertTrue(dialog.window.destroyed)

    def test_on_cancel_resets_result_and_destroys(self):
        dialog = self._make()
        dialog.captured_controls = [{"name": "btn_1"}]
        dialog.on_cancel()
        self.assertIsNone(dialog.result)
        self.assertTrue(dialog.window.destroyed)


if __name__ == "__main__":
    unittest.main()
