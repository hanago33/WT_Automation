# encoding: utf-8
"""Simple 模式「项目计算参数」下拉选项（Cp 版本 / 风机类型型号）新建与删除的回归测试。

分两层覆盖：
- ``wt_simple_options``：纯规则（规范化 / 新增 / 删除 / 重命名 / 排序 / 恢复默认 / 加载语义）；
- ``LauncherApp``：GUI 接线（新建、删除当前值、持久化、下拉框 values 实时刷新、
  删除正在使用的值时字段清空、取消操作不改状态）。

背景（本次修复的缺陷）：
1. 原实现只有「＋」新增，**没有删除**；
2. 新增只 append 到内部列表，Combobox 的 ``values`` 是构建时快照，
   下拉列表不会更新，必须关掉弹窗重开才生效；
3. 加载时 ``if not self.cp_version_options`` 会把「用户主动删空」误判为
   「首次使用」，导致默认值被复活。
"""
import os
import sys
import unittest
from tkinter import messagebox, simpledialog
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_simple_options as opt
from WT_Launcher import LauncherApp


# ── 测试替身 ────────────────────────────────────────────────────────────

class FakeVar(object):
    """替代 tk.StringVar。"""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeCombo(object):
    """替代 ttk.Combobox，只记录 values 刷新。"""

    def __init__(self, values=None):
        self.values = list(values or [])
        self.configure_count = 0

    def configure(self, **kwargs):
        self.configure_count += 1
        if "values" in kwargs:
            self.values = list(kwargs["values"])


class FakeStatusVar(object):
    def __init__(self):
        self.value = None

    def set(self, value):
        self.value = value


class FakeStatusLabel(object):
    def __init__(self):
        self.kwargs = None

    def config(self, **kwargs):
        self.kwargs = kwargs


THEME = {
    "bg": "#f8fafc", "text": "#0f172a", "muted": "#64748b",
    "primary": "#2563eb", "secondary": "#ffffff", "danger": "#dc2626",
    "success": "#059669", "warning": "#d97706", "border": "#e2e8f0",
}


def make_app(cp_options=None, turbine_options=None):
    """绕过 __init__ 构造 LauncherApp，只挂本功能需要的属性。"""
    app = LauncherApp.__new__(LauncherApp)
    app.root = object()
    app.theme = dict(THEME)
    app.cp_version_options = list(
        opt.DEFAULT_CP_VERSION_OPTIONS if cp_options is None else cp_options)
    app.turbine_type_options = list(
        opt.DEFAULT_TURBINE_TYPE_OPTIONS if turbine_options is None else turbine_options)
    app.simple_status_var = FakeStatusVar()
    app.simple_status_label = FakeStatusLabel()
    app.save_calls = []
    app._simple_save_state = lambda: app.save_calls.append(True)
    return app


# ── 第一层：纯规则 ──────────────────────────────────────────────────────

class NormalizeOptionsTests(unittest.TestCase):
    def test_strip_drop_empty_and_dedupe_keep_order(self):
        self.assertEqual(
            opt.normalize_options(["  Cp0.429 ", "", "Cp0.425", "Cp0.429", None, "  "]),
            ["Cp0.429", "Cp0.425"])

    def test_defaults_appended_when_missing(self):
        self.assertEqual(
            opt.normalize_options(["A"], ["B", "A"]),
            ["A", "B"])

    def test_none_input_is_tolerated(self):
        self.assertEqual(opt.normalize_options(None), [])

    def test_bare_string_is_one_option_not_char_list(self):
        """回归：list('Cp0.429') 会逐字符拆开，必须按单个选项处理。"""
        self.assertEqual(opt.normalize_options("Cp0.429"), ["Cp0.429"])
        self.assertEqual(opt.normalize_options(["A"], "B"), ["A", "B"])


class LoadOptionsTests(unittest.TestCase):
    def test_missing_key_seeds_defaults(self):
        self.assertEqual(
            opt.load_options(None, opt.DEFAULT_CP_VERSION_OPTIONS),
            list(opt.DEFAULT_CP_VERSION_OPTIONS))

    def test_explicit_empty_list_is_respected(self):
        """用户把选项删空后，重启不应被默认值复活。"""
        self.assertEqual(opt.load_options([], opt.DEFAULT_CP_VERSION_OPTIONS), [])

    def test_wrong_type_falls_back_to_defaults(self):
        self.assertEqual(
            opt.load_options("not-a-list", ("X",)), ["X"])


class AddOptionTests(unittest.TestCase):
    def test_add_appends_and_strips(self):
        options, status = opt.add_option(["Cp0.429"], "  Cp0.425 ")
        self.assertEqual(status, opt.RESULT_ADDED)
        self.assertEqual(options, ["Cp0.429", "Cp0.425"])

    def test_duplicate_is_reported_without_change(self):
        options, status = opt.add_option(["Cp0.429"], "Cp0.429")
        self.assertEqual(status, opt.RESULT_DUPLICATE)
        self.assertEqual(options, ["Cp0.429"])

    def test_empty_is_rejected(self):
        options, status = opt.add_option(["Cp0.429"], "   ")
        self.assertEqual(status, opt.RESULT_EMPTY)
        self.assertEqual(options, ["Cp0.429"])

    def test_too_long_is_rejected(self):
        options, status = opt.add_option([], "X" * (opt.MAX_OPTION_LENGTH + 1))
        self.assertEqual(status, opt.RESULT_TOO_LONG)
        self.assertEqual(options, [])

    def test_input_list_is_not_mutated(self):
        original = ["Cp0.429"]
        opt.add_option(original, "Cp0.425")
        self.assertEqual(original, ["Cp0.429"])


class RemoveOptionTests(unittest.TestCase):
    def test_remove_single(self):
        options, status = opt.remove_option(["Cp0.429", "Cp0.425"], "Cp0.429")
        self.assertEqual(status, opt.RESULT_REMOVED)
        self.assertEqual(options, ["Cp0.425"])

    def test_remove_missing(self):
        options, status = opt.remove_option(["Cp0.429"], "Cp0.5")
        self.assertEqual(status, opt.RESULT_NOT_FOUND)
        self.assertEqual(options, ["Cp0.429"])

    def test_remove_blank_value(self):
        _, status = opt.remove_option(["Cp0.429"], "   ")
        self.assertEqual(status, opt.RESULT_EMPTY)

    def test_remove_many(self):
        options, status = opt.remove_options(
            ["a", "b", "c", "d"], ["b", "d", "  ", "zzz"])
        self.assertEqual(status, opt.RESULT_REMOVED)
        self.assertEqual(options, ["a", "c"])

    def test_remove_many_nothing_matched(self):
        options, status = opt.remove_options(["a"], ["z"])
        self.assertEqual(status, opt.RESULT_NOT_FOUND)
        self.assertEqual(options, ["a"])

    def test_remove_can_leave_list_empty(self):
        options, status = opt.remove_option(["only"], "only")
        self.assertEqual(status, opt.RESULT_REMOVED)
        self.assertEqual(options, [])


class RenameMoveResetTests(unittest.TestCase):
    def test_rename_keeps_position(self):
        options, status = opt.rename_option(["a", "b", "c"], "b", "B")
        self.assertEqual(status, opt.RESULT_RENAMED)
        self.assertEqual(options, ["a", "B", "c"])

    def test_rename_to_existing_is_rejected(self):
        options, status = opt.rename_option(["a", "b"], "a", "b")
        self.assertEqual(status, opt.RESULT_DUPLICATE)
        self.assertEqual(options, ["a", "b"])

    def test_rename_missing_source(self):
        _, status = opt.rename_option(["a"], "z", "y")
        self.assertEqual(status, opt.RESULT_NOT_FOUND)

    def test_move_up_and_down(self):
        options, status = opt.move_option(["a", "b", "c"], "c", -1)
        self.assertEqual(status, opt.RESULT_MOVED)
        self.assertEqual(options, ["a", "c", "b"])
        options, status = opt.move_option(options, "a", 1)
        self.assertEqual(options, ["c", "a", "b"])

    def test_move_beyond_boundary(self):
        options, status = opt.move_option(["a", "b"], "a", -1)
        self.assertEqual(status, opt.RESULT_BOUNDARY)
        self.assertEqual(options, ["a", "b"])

    def test_reset_restores_defaults(self):
        self.assertEqual(
            opt.reset_options(opt.DEFAULT_TURBINE_TYPE_OPTIONS),
            list(opt.DEFAULT_TURBINE_TYPE_OPTIONS))


class DescribeResultTests(unittest.TestCase):
    def test_messages_are_kind_aware(self):
        self.assertIn("Cp 版本",
                      opt.describe_result("cpVersion", opt.RESULT_ADDED, "Cp0.5"))
        self.assertIn("已存在",
                      opt.describe_result("cpVersion", opt.RESULT_DUPLICATE, "Cp0.5"))
        self.assertIn("2 个",
                      opt.describe_result("turbineType", opt.RESULT_REMOVED, "A", 2))

    def test_reset_message(self):
        self.assertIn("恢复为内置默认",
                      opt.describe_result("cpVersion", opt.RESULT_RESET))

    def test_status_kind_mapping(self):
        self.assertEqual(opt.status_kind_for(opt.RESULT_ADDED), "success")
        self.assertEqual(opt.status_kind_for(opt.RESULT_RESET), "success")
        self.assertEqual(opt.status_kind_for(opt.RESULT_DUPLICATE), "warning")
        self.assertEqual(opt.status_kind_for("unknown"), "idle")


# ── 第二层：LauncherApp 接线 ────────────────────────────────────────────

class LauncherOptionAccessTests(unittest.TestCase):
    def test_option_kinds_attrs_exist_on_launcher(self):
        app = make_app()
        for kind, meta in opt.OPTION_KINDS.items():
            self.assertTrue(hasattr(app, meta["attr"]),
                            "{} 缺少属性 {}".format(kind, meta["attr"]))
            self.assertEqual(app._simple_option_values(kind),
                             list(getattr(app, meta["attr"])))

    def test_option_values_returns_copy(self):
        app = make_app(cp_options=["Cp0.429"])
        values = app._simple_option_values("cpVersion")
        values.append("Cp0.9")
        self.assertEqual(app.cp_version_options, ["Cp0.429"])

    def test_set_option_values_normalizes_and_persists(self):
        app = make_app(cp_options=["Cp0.429"])
        app._simple_set_option_values("cpVersion", ["  Cp0.5 ", "Cp0.5", ""])
        self.assertEqual(app.cp_version_options, ["Cp0.5"])
        self.assertTrue(app.save_calls)

    def test_unknown_kind_is_noop(self):
        app = make_app()
        app._simple_set_option_values("nope", ["x"])
        self.assertFalse(app.save_calls)


class LauncherAddOptionTests(unittest.TestCase):
    def test_add_updates_list_and_refreshes_combo(self):
        app = make_app(cp_options=["Cp0.429"])
        var, combo = FakeVar("Cp0.429"), FakeCombo(["Cp0.429"])
        with patch.object(simpledialog, "askstring", return_value="Cp0.5"):
            app._simple_add_option("cpVersion", var, combo)
        self.assertEqual(app.cp_version_options, ["Cp0.429", "Cp0.5"])
        self.assertEqual(combo.values, ["Cp0.429", "Cp0.5"])  # 下拉框已实时刷新
        self.assertEqual(var.get(), "Cp0.5")                  # 新建后自动选中
        self.assertTrue(app.save_calls)
        self.assertIn("Cp0.5", app.simple_status_var.value)

    def test_add_strips_whitespace(self):
        app = make_app(turbine_options=[])
        var, combo = FakeVar(""), FakeCombo([])
        with patch.object(simpledialog, "askstring", return_value="  WT6250D220  "):
            app._simple_add_option("turbineType", var, combo)
        self.assertEqual(app.turbine_type_options, ["WT6250D220"])

    def test_add_duplicate_selects_existing_without_duplicating(self):
        app = make_app(cp_options=["Cp0.429"])
        var, combo = FakeVar(""), FakeCombo([])
        with patch.object(simpledialog, "askstring", return_value="Cp0.429"):
            app._simple_add_option("cpVersion", var, combo)
        self.assertEqual(app.cp_version_options, ["Cp0.429"])
        self.assertEqual(var.get(), "Cp0.429")
        self.assertFalse(app.save_calls)                      # 无变更不写盘
        self.assertIn("已存在", app.simple_status_var.value)

    def test_add_cancel_changes_nothing(self):
        app = make_app(cp_options=["Cp0.429"])
        var, combo = FakeVar("Cp0.429"), FakeCombo(["Cp0.429"])
        with patch.object(simpledialog, "askstring", return_value=None):
            app._simple_add_option("cpVersion", var, combo)
        self.assertEqual(app.cp_version_options, ["Cp0.429"])
        self.assertFalse(app.save_calls)
        self.assertIsNone(app.simple_status_var.value)

    def test_add_blank_shows_info_and_keeps_list(self):
        app = make_app(cp_options=["Cp0.429"])
        var, combo = FakeVar(""), FakeCombo([])
        with patch.object(simpledialog, "askstring", return_value="   "), \
                patch.object(messagebox, "showinfo") as info:
            app._simple_add_option("cpVersion", var, combo)
        info.assert_called_once()
        self.assertEqual(app.cp_version_options, ["Cp0.429"])
        self.assertFalse(app.save_calls)

    def test_add_works_without_combo_or_var(self):
        """按钮回调允许不传控件（例如未来在别处复用）。"""
        app = make_app(cp_options=["Cp0.429"])
        with patch.object(simpledialog, "askstring", return_value="Cp0.5"):
            app._simple_add_option("cpVersion")
        self.assertEqual(app.cp_version_options, ["Cp0.429", "Cp0.5"])


class LauncherRemoveOptionTests(unittest.TestCase):
    def test_remove_current_clears_field_when_in_use(self):
        app = make_app(cp_options=["Cp0.429", "Cp0.425"])
        var, combo = FakeVar("Cp0.425"), FakeCombo(["Cp0.429", "Cp0.425"])
        with patch.object(messagebox, "askyesno", return_value=True):
            removed = app._simple_remove_current_option("cpVersion", var, combo)
        self.assertTrue(removed)
        self.assertEqual(app.cp_version_options, ["Cp0.429"])
        self.assertEqual(combo.values, ["Cp0.429"])
        self.assertEqual(var.get(), "")                       # 字段被清空
        self.assertTrue(app.save_calls)
        self.assertIn("已删除", app.simple_status_var.value)

    def test_remove_cancelled_keeps_everything(self):
        app = make_app(cp_options=["Cp0.429", "Cp0.425"])
        var, combo = FakeVar("Cp0.425"), FakeCombo(["Cp0.429", "Cp0.425"])
        with patch.object(messagebox, "askyesno", return_value=False):
            removed = app._simple_remove_current_option("cpVersion", var, combo)
        self.assertFalse(removed)
        self.assertEqual(app.cp_version_options, ["Cp0.429", "Cp0.425"])
        self.assertEqual(var.get(), "Cp0.425")
        self.assertFalse(app.save_calls)

    def test_remove_with_empty_field_shows_info(self):
        app = make_app(cp_options=["Cp0.429"])
        var, combo = FakeVar(""), FakeCombo([])
        with patch.object(messagebox, "showinfo") as info:
            removed = app._simple_remove_current_option("cpVersion", var, combo)
        self.assertFalse(removed)
        info.assert_called_once()
        self.assertEqual(app.cp_version_options, ["Cp0.429"])

    def test_remove_value_not_in_list_shows_info(self):
        app = make_app(cp_options=["Cp0.429"])
        var, combo = FakeVar("手输的值"), FakeCombo([])
        with patch.object(messagebox, "showinfo") as info, \
                patch.object(messagebox, "askyesno") as confirm:
            removed = app._simple_remove_current_option("cpVersion", var, combo)
        self.assertFalse(removed)
        info.assert_called_once()
        confirm.assert_not_called()
        self.assertEqual(app.cp_version_options, ["Cp0.429"])

    def test_remove_many_keeps_field_when_not_in_use(self):
        """管理对话框多选删除：当前字段不在被删项里则保留原值。"""
        app = make_app(cp_options=["Cp0.429", "Cp0.425", "Cp0.405"])
        var, combo = FakeVar("Cp0.429"), FakeCombo([])
        with patch.object(messagebox, "askyesno", return_value=True):
            removed = app._simple_remove_options(
                "cpVersion", ["Cp0.425", "Cp0.405"], var, combo)
        self.assertTrue(removed)
        self.assertEqual(app.cp_version_options, ["Cp0.429"])
        self.assertEqual(var.get(), "Cp0.429")
        self.assertEqual(combo.values, ["Cp0.429"])

    def test_remove_many_dedupes_input(self):
        app = make_app(cp_options=["a", "b"])
        var, combo = FakeVar(""), FakeCombo([])
        with patch.object(messagebox, "askyesno", return_value=True):
            app._simple_remove_options("cpVersion", ["b", "b", " b "], var, combo)
        self.assertEqual(app.cp_version_options, ["a"])

    def test_remove_can_empty_the_list_and_stays_empty(self):
        """删空后写盘的是空列表，重启不再被默认值复活。"""
        app = make_app(cp_options=["Cp0.429"])
        var, combo = FakeVar("Cp0.429"), FakeCombo([])
        with patch.object(messagebox, "askyesno", return_value=True):
            app._simple_remove_options("cpVersion", ["Cp0.429"], var, combo)
        self.assertEqual(app.cp_version_options, [])
        self.assertEqual(
            opt.load_options(app.cp_version_options, opt.DEFAULT_CP_VERSION_OPTIONS), [])

    def test_remove_skips_confirm_when_disabled(self):
        app = make_app(cp_options=["Cp0.429"])
        var, combo = FakeVar(""), FakeCombo([])
        with patch.object(messagebox, "askyesno") as confirm:
            removed = app._simple_remove_options(
                "cpVersion", ["Cp0.429"], var, combo, confirm=False)
        self.assertTrue(removed)
        confirm.assert_not_called()


class LauncherManageDialogWiringTests(unittest.TestCase):
    def test_manage_and_handler_methods_exist(self):
        for name in ("_simple_manage_options", "_simple_add_option",
                     "_simple_remove_current_option", "_simple_remove_options",
                     "_simple_refresh_option_combo", "_simple_option_values",
                     "_simple_set_option_values"):
            self.assertTrue(callable(getattr(LauncherApp, name, None)),
                            "LauncherApp 缺少 {}".format(name))

    def test_refresh_combo_pushes_latest_values(self):
        app = make_app(turbine_options=["WT6250D220", "WT7150D200"])
        combo = FakeCombo(["stale"])
        app._simple_refresh_option_combo("turbineType", combo)
        self.assertEqual(combo.values, ["WT6250D220", "WT7150D200"])

    def test_refresh_combo_tolerates_broken_widget(self):
        class BrokenCombo(object):
            def configure(self, **_kwargs):
                raise RuntimeError("widget destroyed")

        app = make_app()
        app._simple_refresh_option_combo("cpVersion", BrokenCombo())  # 不应抛异常

    def test_unknown_kind_is_ignored(self):
        app = make_app()
        var, combo = FakeVar("x"), FakeCombo([])
        app._simple_add_option("nope", var, combo)
        app._simple_remove_current_option("nope", var, combo)
        self.assertFalse(app.save_calls)


if __name__ == "__main__":
    unittest.main()
