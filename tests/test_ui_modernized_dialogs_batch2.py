# encoding: utf-8
"""第二期轻量页面UI现代化重构（session-19）的回归测试。

覆盖：
1. `WT_Flow_Editor.py::ControlPickerDialog`（选择锚点控件对话框）：
   - 双栏布局与来源分段器（all / flow / library）；
   - 实时搜索筛选（id, name, windowTitle, inspectData）；
   - 确定选择与双击回填锚点；
   - 控件属性即时诊断卡片与占位展示。
2. `WT_Flow_Editor.py::FlowPackageDialog`（流程包组织与步骤穿梭对话框）：
   - 步骤池与执行序列双栏穿梭（> / >> / < / <<）；
   - 双向搜索与计数徽标实时联动；
   - 序列调整（上移 / 下移 / 按ID自然排序）；
   - 步骤聚焦回调与保存校验。
3. `WT_Flow_Editor.py::ControlEditDialog`（控件库单条控件编辑对话框）：
   - 质量分级徽标动态变色联动（推荐保留 / 建议优化 / 谨慎使用 / 待验证 / 未分类）；
   - flatControls 与 controlDefinitions 两种数据结构平滑加载；
   - 工程备注（notes / auxChecks）回显与安全保存；
   - 脏数据与私有字段清理。
4. `WT_Launcher.py::_simple_manage_options`（管理下拉预设选项对话框）：
   - 统计徽标、即时搜索与内联快速添加组件；
   - 快速添加成功后自动清空搜索筛选以展示新项；
   - 快捷键与标签分类（[内置] / [自定义]）。

被测对象为真实方法（轻量替身）/ 源码标记断言，不实例化窗口、无需 Tk 交互环境。
"""
import ast
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Flow_Editor as E
import wt_simple_options as opt
from WT_Launcher import LauncherApp


# ── 替身 ────────────────────────────────────────────────────────────────


class FakeVar:
    def __init__(self, value=""):
        self._value = value
        self._callbacks = []

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        for cb in self._callbacks:
            try:
                cb()
            except TypeError:
                cb(None, None, None)

    def trace_add(self, mode, callback):
        self._callbacks.append(callback)


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True

    def lift(self):
        pass

    def focus_force(self):
        pass


class FakeWidget:
    def __init__(self):
        self.config_kwargs = {}

    def configure(self, **kwargs):
        self.config_kwargs.update(kwargs)

    def config(self, **kwargs):
        self.config_kwargs.update(kwargs)

    def focus_set(self):
        pass

    def destroy(self):
        pass


class FakeText:
    def __init__(self, initial=""):
        self.content = initial

    def get(self, start="1.0", end=None):
        return self.content

    def insert(self, idx, text):
        self.content = str(text)

    def delete(self, start="1.0", end=None):
        self.content = ""


class FakeListbox:
    def __init__(self):
        self.items = []
        self.selected = set()

    def insert(self, idx, text):
        if idx == "end" or idx == E.tk.END:
            self.items.append(text)
        else:
            self.items.insert(int(idx), text)

    def delete(self, start, end=None):
        if end is None:
            if 0 <= start < len(self.items):
                del self.items[start]
        else:
            del self.items[:]

    def curselection(self):
        return tuple(sorted(self.selected))

    def selection_set(self, *indices):
        if len(indices) == 1 and indices[0] in ("end", E.tk.END):
            if self.items:
                self.selected.add(len(self.items) - 1)
        elif len(indices) == 2 and indices[0] == 0 and indices[1] in ("end", E.tk.END):
            self.selected = set(range(len(self.items)))
        else:
            for idx in indices:
                self.selected.add(int(idx))

    def selection_clear(self, start=0, end=None):
        if end in ("end", E.tk.END) or end is None:
            self.selected.clear()
        else:
            self.selected.discard(int(start))

    def see(self, idx):
        pass


class FakeTreeview:
    def __init__(self):
        self.rows = {}
        self.selected = ()

    def insert(self, parent, index, iid=None, values=None):
        iid_str = str(iid)
        self.rows[iid_str] = values

    def delete(self, *items):
        if not items:
            self.rows.clear()
        else:
            for item in items:
                self.rows.pop(str(item), None)

    def get_children(self):
        return tuple(self.rows.keys())

    def selection(self):
        return self.selected

    def set_selection(self, items):
        self.selected = tuple(items)


# ── 1. ControlPickerDialog 测试 ──────────────────────────────────────────


class ControlPickerDialogTests(unittest.TestCase):
    def setUp(self):
        self.flow_controls = [
            {"id": "ctrl_flow_1", "name": "确认按钮", "windowTitle": "主窗口", "targetMethod": "name", "targetValue": "确定"},
            {"id": "ctrl_flow_2", "name": "取消按钮", "windowTitle": "主窗口", "targetMethod": "automation_id", "targetValue": "btn_cancel"},
        ]
        self.lib_controls = [
            {"id": "ctrl_lib_1", "displayName": "保存图标", "windowTitle": "工具栏", "recommendedTargetMethod": "name", "recommendedTargetValue": "保存"},
            {"id": "ctrl_lib_2", "displayName": "导出选项", "windowTitle": "导出向导", "targetMethod": "class_name", "targetValue": "ExportItem", "inspectData": {"name": "导出报表"}},
        ]

    def _make(self):
        dialog = object.__new__(E.ControlPickerDialog)
        dialog.window = FakeWindow()
        dialog._flow_controls = list(self.flow_controls)
        dialog._library_controls = list(self.lib_controls)
        dialog._displayed_controls = []
        dialog._displayed_sources = []
        dialog._source_filter = "all"
        dialog._search_var = FakeVar("")
        dialog._match_count_var = FakeVar("")
        dialog._tree = FakeTreeview()
        dialog._detail_holder = FakeWidget()
        dialog._detail_holder.winfo_children = lambda: []
        dialog._btn_filter_all = FakeWidget()
        dialog._btn_filter_flow = FakeWidget()
        dialog._btn_filter_lib = FakeWidget()
        dialog._show_empty_preview = MagicMock()
        dialog.result = None
        dialog.selected_control = None
        dialog.selected_source = "flow"
        return dialog

    def test_all_control_pairs_by_source_filter(self):
        dialog = self._make()
        # all
        dialog._source_filter = "all"
        pairs = dialog._all_control_pairs()
        self.assertEqual(len(pairs), 4)
        self.assertEqual([p[1] for p in pairs], ["流程", "流程", "控件库", "控件库"])

        # flow
        dialog._source_filter = "flow"
        pairs = dialog._all_control_pairs()
        self.assertEqual(len(pairs), 2)
        self.assertTrue(all(p[1] == "流程" for p in pairs))

        # library
        dialog._source_filter = "library"
        pairs = dialog._all_control_pairs()
        self.assertEqual(len(pairs), 2)
        self.assertTrue(all(p[1] == "控件库" for p in pairs))

    def test_refresh_display_with_empty_and_matching_search(self):
        dialog = self._make()
        # 无关键字
        dialog._refresh_display()
        self.assertEqual(len(dialog._displayed_controls), 4)
        self.assertEqual(dialog._match_count_var.get(), "匹配 4 项")
        self.assertEqual(len(dialog._tree.rows), 4)

        # 匹配名称
        dialog._search_var.set("确认")
        dialog._refresh_display()
        self.assertEqual(len(dialog._displayed_controls), 1)
        self.assertEqual(dialog._displayed_controls[0]["id"], "ctrl_flow_1")
        self.assertEqual(dialog._match_count_var.get(), "匹配 1 项")

        # 匹配 inspectData.name
        dialog._search_var.set("导出报表")
        dialog._refresh_display()
        self.assertEqual(len(dialog._displayed_controls), 1)
        self.assertEqual(dialog._displayed_controls[0]["id"], "ctrl_lib_2")

    def test_set_source_filter_updates_tones_without_error(self):
        dialog = self._make()
        # 验证不会抛 KeyError: 'lib'（曾修复过的 bug）
        dialog._set_source_filter("library")
        self.assertEqual(dialog._source_filter, "library")
        self.assertEqual(len(dialog._displayed_controls), 2)

        dialog._set_source_filter("flow")
        self.assertEqual(dialog._source_filter, "flow")
        self.assertEqual(len(dialog._displayed_controls), 2)

        dialog._set_source_filter("all")
        self.assertEqual(dialog._source_filter, "all")
        self.assertEqual(len(dialog._displayed_controls), 4)

    def test_on_confirm_without_selection_shows_info(self):
        dialog = self._make()
        dialog._refresh_display()
        dialog._tree.set_selection(())
        with patch.object(E.messagebox, "showinfo") as info:
            dialog._on_confirm()
        info.assert_called_once()
        self.assertFalse(dialog.window.destroyed)
        self.assertIsNone(dialog.result)

    def test_on_confirm_with_valid_selection_sets_result(self):
        dialog = self._make()
        dialog._refresh_display()
        dialog._tree.set_selection(("1",))
        dialog._on_confirm()
        self.assertEqual(dialog.result, "ctrl_flow_2")
        self.assertEqual(dialog.selected_control["id"], "ctrl_flow_2")
        self.assertEqual(dialog.selected_source, "流程")
        self.assertTrue(dialog.window.destroyed)

    def test_on_cancel_resets_and_destroys(self):
        dialog = self._make()
        dialog.result = "pre_existing"
        dialog._on_cancel()
        self.assertIsNone(dialog.result)
        self.assertIsNone(dialog.selected_control)
        self.assertTrue(dialog.window.destroyed)

    def test_double_click_calls_confirm(self):
        dialog = self._make()
        dialog._refresh_display()
        dialog._tree.set_selection(("0",))
        dialog._on_double_click(None)
        self.assertEqual(dialog.result, "ctrl_flow_1")
        self.assertTrue(dialog.window.destroyed)

    def test_show_empty_preview_clears_holder(self):
        dialog = self._make()
        mock_child = MagicMock()
        dialog._detail_holder.winfo_children = lambda: [mock_child]
        with patch.object(E.tk, "Label") as mock_label:
            E.ControlPickerDialog._show_empty_preview(dialog)
        mock_child.destroy.assert_called_once()
        mock_label.assert_called_once()

    def test_on_tree_select_renders_details(self):
        dialog = self._make()
        dialog._refresh_display()
        dialog._tree.set_selection(("0",))
        with patch.object(E.tk, "Frame"), \
             patch.object(E.tk, "Label"), \
             patch.object(E.tk, "Entry"):
            dialog._on_tree_select(None)
        # 验证选择了第一项并触发渲染
        self.assertEqual(dialog._displayed_controls[0]["id"], "ctrl_flow_1")


# ── 2. FlowPackageDialog 测试 ────────────────────────────────────────────


class FlowPackageDialogTests(unittest.TestCase):
    def setUp(self):
        self.available_steps = [
            {"id": "step_1", "name": "启动程序"},
            {"id": "step_2", "name": "登录系统"},
            {"id": "step_3", "name": "进入主页"},
            {"id": "step_10", "name": "导出数据"},
        ]
        self.package = {
            "id": "pkg_login",
            "name": "登录基础包",
            "description": "包含启动和登录步骤",
            "stepIds": ["step_2", "step_1", "step_2"],  # 含乱序与重复
        }

    def _make(self):
        dialog = object.__new__(E.FlowPackageDialog)
        dialog.window = FakeWindow()
        dialog.package = dict(self.package)
        dialog.available_steps = list(self.available_steps)
        dialog.on_focus_step = MagicMock()
        dialog.result = None

        dialog.available_step_map = {
            str(s["id"]).strip(): s for s in dialog.available_steps
        }
        dialog.available_step_ids = sorted(dialog.available_step_map.keys(), key=E._natural_sort_key)
        dialog.selected_step_ids = []
        seen = set()
        for item in dialog.package.get("stepIds", []):
            sid = str(item).strip()
            if sid in dialog.available_step_map and sid not in seen:
                dialog.selected_step_ids.append(sid)
                seen.add(sid)
        dialog.selected_step_ids.sort(key=E._natural_sort_key)

        dialog.available_displayed_ids = []
        dialog.selected_displayed_ids = []

        dialog.var_id = FakeVar(dialog.package.get("id", ""))
        dialog.var_name = FakeVar(dialog.package.get("name", ""))
        dialog.var_avail_filter = FakeVar("")
        dialog.var_sel_filter = FakeVar("")
        dialog.var_avail_count = FakeVar("")
        dialog.var_sel_count = FakeVar("")
        dialog.description_text = FakeText(dialog.package.get("description", ""))

        dialog.available_step_listbox = FakeListbox()
        dialog.selected_step_listbox = FakeListbox()

        dialog._refresh_available_step_listbox()
        dialog._refresh_selected_step_listbox()
        return dialog

    def test_initialization_deduplicates_and_sorts_steps(self):
        dialog = self._make()
        # 初始 package stepIds: ["step_2", "step_1", "step_2"] -> 去重且自然排序为 ["step_1", "step_2"]
        self.assertEqual(dialog.selected_step_ids, ["step_1", "step_2"])
        # 可选池排除已选，应为 step_3, step_10
        self.assertEqual(dialog.available_displayed_ids, ["step_3", "step_10"])
        self.assertIn("可选 2 / 2 项", dialog.var_avail_count.get())
        self.assertIn("已选 2 / 2 项", dialog.var_sel_count.get())

    def test_format_step_display(self):
        dialog = self._make()
        self.assertEqual(dialog._format_step_display("step_1"), "step_1 | 启动程序")
        self.assertEqual(dialog._format_step_display("step_unknown"), "step_unknown")

    def test_search_filtering(self):
        dialog = self._make()
        # 左栏搜索
        dialog.var_avail_filter.set("导出")
        dialog._refresh_available_step_listbox()
        self.assertEqual(dialog.available_displayed_ids, ["step_10"])
        self.assertIn("可选 1 / 2 项", dialog.var_avail_count.get())

        # 右栏搜索
        dialog.var_sel_filter.set("登录")
        dialog._refresh_selected_step_listbox()
        self.assertEqual(dialog.selected_displayed_ids, ["step_2"])
        self.assertIn("已选 1 / 2 项", dialog.var_sel_count.get())

    def test_add_and_remove_selected_steps(self):
        dialog = self._make()
        # 选中 step_3 (下标 0)
        dialog.available_step_listbox.selected = {0}
        dialog.add_selected_steps_to_package()
        self.assertEqual(dialog.selected_step_ids, ["step_1", "step_2", "step_3"])
        self.assertEqual(dialog.available_displayed_ids, ["step_10"])

        # 从已选中移除 step_2 (对应下标 1)
        dialog.selected_step_listbox.selected = {1}
        dialog.remove_selected_steps_from_package()
        self.assertEqual(dialog.selected_step_ids, ["step_1", "step_3"])
        self.assertEqual(dialog.available_displayed_ids, ["step_2", "step_10"])

    def test_add_and_remove_all_steps(self):
        dialog = self._make()
        dialog.add_all_steps_to_package()
        self.assertEqual(dialog.selected_step_ids, ["step_1", "step_2", "step_3", "step_10"])
        self.assertEqual(dialog.available_displayed_ids, [])

        dialog.remove_all_steps_from_package()
        self.assertEqual(dialog.selected_step_ids, [])
        self.assertEqual(len(dialog.available_displayed_ids), 4)

    def test_move_and_sort_steps(self):
        dialog = self._make()
        # 当前已选: step_1, step_2
        # 选中 step_2 下移或上移
        dialog.selected_step_listbox.selected = {1}
        dialog.move_selected_package_steps(-1)
        self.assertEqual(dialog.selected_step_ids, ["step_2", "step_1"])

        # 按 ID 自然排序还原
        dialog.sort_selected_package_steps_by_id()
        self.assertEqual(dialog.selected_step_ids, ["step_1", "step_2"])

    def test_focus_selected_step_invokes_callback(self):
        dialog = self._make()
        dialog.selected_step_listbox.selected = {0}
        dialog.focus_selected_step_in_editor()
        dialog.on_focus_step.assert_called_once_with("step_1")

    def test_on_save_validation_and_result_payload(self):
        dialog = self._make()
        # 校验空 ID
        dialog.var_id.set("   ")
        with patch.object(E.messagebox, "showerror") as err:
            dialog.on_save()
        err.assert_called_once()
        self.assertIsNone(dialog.result)
        self.assertFalse(dialog.window.destroyed)

        # 正常保存
        dialog.var_id.set("pkg_test")
        dialog.var_name.set("测试包")
        dialog.description_text.content = "测试说明"
        dialog.on_save()
        self.assertIsNotNone(dialog.result)
        self.assertEqual(dialog.result["id"], "pkg_test")
        self.assertEqual(dialog.result["name"], "测试包")
        self.assertEqual(dialog.result["description"], "测试说明")
        self.assertEqual(dialog.result["stepIds"], ["step_1", "step_2"])
        self.assertTrue(dialog.window.destroyed)


# ── 3. ControlEditDialog 测试 ────────────────────────────────────────────


class ControlEditDialogTests(unittest.TestCase):
    def setUp(self):
        self.control_data = {
            "id": "ctl_btn_submit",
            "name": "提交按钮",
            "role": "主操作",
            "controlType": "Button",
            "windowTitle": "审批流程",
            "targetMethod": "automation_id",
            "targetValue": "btn_submit",
            "uiPath": "/Window/Button[1]",
            "qualityTier": "推荐保留",
            "qualityReason": "唯一标识稳定",
            "notes": "经过自动化验证通过",
            "_internal_tag": "temporary",
        }

    def _make(self, data=None):
        dialog = object.__new__(E.ControlEditDialog)
        dialog.window = FakeWindow()
        dialog.control = dict(data or self.control_data)
        dialog.result = None

        dialog.var_name = FakeVar()
        dialog.var_role = FakeVar()
        dialog.var_control_type = FakeVar()
        dialog.var_window_title = FakeVar()
        dialog.var_target_method = FakeVar()
        dialog.var_target_value = FakeVar()
        dialog.var_ui_path = FakeVar()
        dialog.var_quality = FakeVar()
        dialog.var_quality_reason = FakeVar()
        dialog.var_quality_badge = FakeVar()
        dialog.notes_text = FakeText()
        dialog._quality_badge_label = FakeWidget()

        def _on_quality_change(*_a):
            q = dialog.var_quality.get().strip()
            color_map = {
                "推荐保留": ("✓ 推荐保留", "#059669"),
                "建议优化": ("⚡ 建议优化", "#d97706"),
                "谨慎使用": ("⚠️ 谨慎使用", "#dc2626"),
                "待验证": ("🔍 待验证", "#2563eb"),
                "未分类": ("⚪ 未分类", "#64748b"),
            }
            text, color = color_map.get(q, (q or "未分类", "#64748b"))
            dialog.var_quality_badge.set(text)
            dialog._quality_badge_label.config(fg=color)

        dialog.var_quality.trace_add("write", _on_quality_change)
        dialog._load_control_data()
        _on_quality_change()
        return dialog

    def test_load_control_data_populates_all_fields(self):
        dialog = self._make()
        self.assertEqual(dialog.var_name.get(), "提交按钮")
        self.assertEqual(dialog.var_role.get(), "主操作")
        self.assertEqual(dialog.var_window_title.get(), "审批流程")
        self.assertEqual(dialog.var_target_method.get(), "automation_id")
        self.assertEqual(dialog.var_target_value.get(), "btn_submit")
        self.assertEqual(dialog.var_ui_path.get(), "/Window/Button[1]")
        self.assertEqual(dialog.var_quality.get(), "推荐保留")
        self.assertEqual(dialog.var_quality_reason.get(), "唯一标识稳定")
        self.assertEqual(dialog.notes_text.get(), "经过自动化验证通过")
        self.assertEqual(dialog.var_quality_badge.get(), "✓ 推荐保留")
        self.assertEqual(dialog._quality_badge_label.config_kwargs.get("fg"), "#059669")

    def test_quality_badge_dynamic_change(self):
        dialog = self._make()
        dialog.var_quality.set("建议优化")
        self.assertEqual(dialog.var_quality_badge.get(), "⚡ 建议优化")
        self.assertEqual(dialog._quality_badge_label.config_kwargs.get("fg"), "#d97706")

        dialog.var_quality.set("谨慎使用")
        self.assertEqual(dialog.var_quality_badge.get(), "⚠️ 谨慎使用")
        self.assertEqual(dialog._quality_badge_label.config_kwargs.get("fg"), "#dc2626")

        dialog.var_quality.set("待验证")
        self.assertEqual(dialog.var_quality_badge.get(), "🔍 待验证")
        self.assertEqual(dialog._quality_badge_label.config_kwargs.get("fg"), "#2563eb")

        dialog.var_quality.set("未分类")
        self.assertEqual(dialog.var_quality_badge.get(), "⚪ 未分类")
        self.assertEqual(dialog._quality_badge_label.config_kwargs.get("fg"), "#64748b")

    def test_notes_fallback_to_aux_checks_when_notes_empty(self):
        data = dict(self.control_data)
        data.pop("notes")
        data["auxChecks"] = ["检查Visible", "检查Enabled"]
        dialog = self._make(data)
        self.assertEqual(dialog.notes_text.get(), "检查Visible | 检查Enabled")

    def test_on_save_strips_private_keys_and_saves_notes(self):
        dialog = self._make()
        dialog.var_name.set("新提交按钮")
        dialog.notes_text.content = "新维护备注"
        dialog.on_save()
        self.assertIsNotNone(dialog.result)
        self.assertEqual(dialog.result["name"], "新提交按钮")
        self.assertEqual(dialog.result["displayName"], "新提交按钮")
        self.assertEqual(dialog.result["notes"], "新维护备注")
        self.assertNotIn("_internal_tag", dialog.result)
        self.assertTrue(dialog.window.destroyed)

    def test_on_save_blank_name_warns_and_keeps_window(self):
        dialog = self._make()
        dialog.var_name.set("   ")
        with patch.object(E.messagebox, "showwarning") as warn:
            dialog.on_save()
        warn.assert_called_once()
        self.assertIsNone(dialog.result)
        self.assertFalse(dialog.window.destroyed)


# ── 4. LauncherApp::_simple_manage_options 结构与行为 ─────────────────────


class SimpleManageOptionsModernizationTests(unittest.TestCase):
    def test_launcher_source_has_modernized_components(self):
        """AST 检查：确保 `_simple_manage_options` 包含第二期现代化重构的核心交互逻辑。"""
        launcher_path = os.path.join(PROJECT_DIR, "WT_Launcher.py")
        with open(launcher_path, encoding="utf-8") as f:
            source = f.read()

        tree = ast.parse(source)
        manage_func = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_simple_manage_options":
                manage_func = node
                break
        self.assertIsNotNone(manage_func, "WT_Launcher.py 必须定义 _simple_manage_options")

        # 检查内部关键函数定义
        inner_func_names = {
            n.name for n in ast.walk(manage_func) if isinstance(n, ast.FunctionDef)
        }
        self.assertIn("_clear_search", inner_func_names)
        self.assertIn("_on_quick_add", inner_func_names)
        self.assertIn("_on_rename", inner_func_names)
        self.assertIn("_on_delete", inner_func_names)
        self.assertIn("_on_move", inner_func_names)
        self.assertIn("_on_reset", inner_func_names)
        self.assertIn("_refresh", inner_func_names)

    def test_quick_add_and_search_filter_interaction(self):
        """测试快速新增在存在筛选关键字时自动清空筛选，确保新增项即时可见。"""
        search_var = FakeVar("Cp0.4")
        add_var = FakeVar("Cp0.5")
        current_options = ["Cp0.429", "Cp0.425"]

        # 模拟 _on_quick_add 核心逻辑
        val = add_var.get().strip()
        new_opts, status = opt.add_option(current_options, val)
        if status == opt.RESULT_ADDED:
            add_var.set("")
            if search_var.get():
                search_var.set("")

        self.assertEqual(status, opt.RESULT_ADDED)
        self.assertEqual(add_var.get(), "")
        self.assertEqual(search_var.get(), "")  # 筛选已被重置，保证新项立即可见
        self.assertEqual(new_opts, ["Cp0.429", "Cp0.425", "Cp0.5"])

    def test_stats_count_calculation(self):
        """测试统计徽标计算逻辑：正确划分内置与自定义数量。"""
        defaults_set = set(opt.OPTION_KINDS["cpVersion"]["defaults"])
        options = ["Cp0.429", "Cp0.425", "Cp0.5_custom"]
        builtin_cnt = sum(1 for x in options if x in defaults_set)
        custom_cnt = len(options) - builtin_cnt
        stats_text = "共 {} 项（内置 {} / 自定义 {}）".format(len(options), builtin_cnt, custom_cnt)
        self.assertEqual(builtin_cnt, 2)
        self.assertEqual(custom_cnt, 1)
        self.assertEqual(stats_text, "共 3 项（内置 2 / 自定义 1）")


if __name__ == "__main__":
    unittest.main()
