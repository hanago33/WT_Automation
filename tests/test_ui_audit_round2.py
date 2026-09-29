# encoding: utf-8
"""UI 审计第二轮修复的回归测试（2026-09-28）。

覆盖 7 处「子代理报告、未逐条复核」条目中经逐条核实确认并修复的缺陷：

1. 流程编辑器表单「未应用到步骤」的修改：切步骤/关窗时三选确认（应用/放弃/取消），不再无声丢弃；
2. 控件库删除确认框：补充「会改写 flow_packages/*.json、标记来源失效」的影响说明；
3. 队列窗口 worker 线程读 Tk 变量 → 改为主线程维护的线程安全快照（行为 + 静态双重验证）；
4. 队列窗口「选择本地流程 JSON」同步上传卡 UI → 上传移入后台线程，回主线程更新；
5. 外部采集对话框：关窗前把手工输入的路径/地址落盘（不再关窗即丢）；
6. 外部采集对话框：按进程名强杀所有 UiaPeek 实例前二次确认；
7. 运行记录：流程异常最后一行按 error 级着色（kind="error"）。

跟进修复（2026-09-29，ai/session-16）：
- 缺陷 1 的三选确认原在按压期靠 `_dragging_step_iid` 跳过，但该标志在按下任意行时
  即被置位，所有鼠标点击切步都被绕过 → 改为按压期推迟、`_finish_step_drag` 松开时
  未重排再走完整确认（见 StepSwitchOnReleaseTests）。
- 多选加载第一个选中步骤原先直接重载表单、同样绕过确认 → 补上三选确认
  （取消则恢复原选中）。

被测对象是真实方法（轻量宿主/替身绑定），不实例化窗口、无需 Tk 交互环境。
"""
import ast
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Flow_Editor as E
import wt_task_queue_window as Q
from tools.external_capture import launcher_panel as LP


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeText:
    def __init__(self, text=""):
        self.text = text

    def get(self, *_args):
        return self.text


class FakeWindow:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True

    def winfo_exists(self):
        return not self.destroyed


class FakeListbox:
    def __init__(self):
        self.items = []
        self.selection = []

    def delete(self, *_args):
        self.items = []

    def insert(self, _index, value):
        self.items.append(value)

    def selection_clear(self, *_args):
        self.selection = []

    def selection_set(self, *_args):
        self.selection = list(self.items[-1:])


# ── 1. 表单「未应用改动」保护 ────────────────────────────────────────────


class FormDirtyGuardTests(unittest.TestCase):
    """切步骤/关窗前：表单有未应用修改时必须给用户三选，而不是无声丢弃。"""

    def _make_app(self):
        app = object.__new__(E.FlowEditorApp)
        app.root = FakeWindow()
        app.selected_index = 0
        app._suppress_tree_select_event = False
        app.var_id = FakeVar("step_1")
        app.var_name = FakeVar("步骤一")
        app.var_action_type = FakeVar("action")
        app.var_action = FakeVar("click")
        app.description_text = FakeText("原始描述")
        app._form_baseline = app._form_snapshot()
        return app

    def test_snapshot_change_detected(self):
        app = self._make_app()
        self.assertEqual(app._form_snapshot(), app._form_baseline)
        app.var_name.set("步骤一（改）")
        self.assertNotEqual(app._form_snapshot(), app._form_baseline)

    def test_text_widget_change_detected(self):
        app = self._make_app()
        app.description_text.text = "改过的描述"
        self.assertNotEqual(app._form_snapshot(), app._form_baseline)

    def test_no_change_skips_dialog(self):
        app = self._make_app()
        with patch.object(E.messagebox, "askyesnocancel") as ask:
            self.assertTrue(app._confirm_discard_form_changes())
            ask.assert_not_called()

    def test_cancel_keeps_step(self):
        app = self._make_app()
        app.var_name.set("步骤一（改）")
        with patch.object(E.messagebox, "askyesnocancel", return_value=None):
            self.assertFalse(app._confirm_discard_form_changes())

    def test_discard_allows_continue(self):
        app = self._make_app()
        app.var_name.set("步骤一（改）")
        with patch.object(E.messagebox, "askyesnocancel", return_value=False):
            self.assertTrue(app._confirm_discard_form_changes())

    def test_apply_success_allows_continue(self):
        app = self._make_app()
        app.var_name.set("步骤一（改）")

        def _apply():
            # 模拟 cmd_apply_step 成功：基线刷新为当前表单
            app._form_baseline = app._form_snapshot()

        app.cmd_apply_step = _apply
        with patch.object(E.messagebox, "askyesnocancel", return_value=True):
            self.assertTrue(app._confirm_discard_form_changes())

    def test_apply_failure_blocks_continue(self):
        app = self._make_app()
        app.var_name.set("步骤一（改）")
        app.cmd_apply_step = lambda: None  # 应用失败：基线未更新
        with patch.object(E.messagebox, "askyesnocancel", return_value=True):
            self.assertFalse(app._confirm_discard_form_changes())

    def test_missing_baseline_is_lenient(self):
        app = self._make_app()
        app._form_baseline = None
        self.assertTrue(app._confirm_discard_form_changes())

    def test_switch_without_drag_asks_and_blocks_on_cancel(self):
        app = self._make_app()
        app.var_name.set("步骤一（改）")
        app._dragging_step_iid = ""

        class _Tree:
            def selection(self):
                return ("1",)

            def selection_set(self, value):
                pass

        app.step_tree = _Tree()
        switched = []
        app._select_step = lambda index, preserve_selection=False: switched.append(index)
        # askyesnocancel 返回 None 表示点了「取消」→ 留在当前步骤
        with patch.object(E.messagebox, "askyesnocancel", return_value=None) as ask:
            app._on_tree_select()
        ask.assert_called_once()
        self.assertEqual(switched, [])  # 用户取消 → 不切步骤

    def test_drag_in_progress_defers_switch(self):
        """按压/拖拽期间选中事件不切表单也不弹窗（模态框会打断拖拽手势），
        切换统一推迟到 _finish_step_drag 松开时处理。"""
        app = self._make_app()
        app.var_name.set("步骤一（改）")
        app._dragging_step_iid = "0"

        class _Tree:
            def selection(self):
                return ("1",)

            def selection_set(self, value):
                pass

        app.step_tree = _Tree()
        switched = []
        app._select_step = lambda index, preserve_selection=False: switched.append(index)
        with patch.object(E.messagebox, "askyesnocancel") as ask:
            app._on_tree_select()
        ask.assert_not_called()
        self.assertEqual(switched, [])  # 按压期推迟：此处既不弹窗也不切换

    def test_restore_step_selection_keeps_form(self):
        app = self._make_app()
        app.selected_index = 2

        class _Tree:
            def __init__(self):
                self.selected = None

            def selection_set(self, value):
                self.selected = value

        app.step_tree = _Tree()
        app._restore_step_selection()
        self.assertEqual(app.step_tree.selected, "2")
        self.assertFalse(app._suppress_tree_select_event)


class _FakeEvent:
    def __init__(self, y=30):
        self.y = y


class _FakeStepTree:
    """步骤树替身：identify_row 模拟松开位置，selection 模拟按压后的选中。"""

    def __init__(self, row_under_cursor, selection=("1",)):
        self.row_under_cursor = row_under_cursor
        self.selection_value = tuple(selection)
        self.selection_sets = []

    def identify_row(self, _y):
        return self.row_under_cursor

    def selection(self):
        return self.selection_value

    def selection_set(self, value):
        self.selection_sets.append(value)
        self.selection_value = (value,)


class StepSwitchOnReleaseTests(unittest.TestCase):
    """点击切步的确认在松开鼠标时执行（按压期会打断手势，e08c182 曾因此整体绕过）。"""

    def _make_release_app(self, pressed_row="1"):
        app = FormDirtyGuardTests._make_app(self)
        app.steps = [
            {"id": "s1", "name": "步骤一"},
            {"id": "s2", "name": "步骤二"},
        ]
        app.selected_index = 0
        app._dragging_step_iid = pressed_row
        app._drag_hover_iid = ""
        app._drag_hover_after = False
        app.step_tree = _FakeStepTree(pressed_row, selection=(pressed_row,))
        return app

    def test_release_plain_click_asks_and_blocks_on_cancel(self):
        app = self._make_release_app()
        app.var_name.set("步骤一（改）")  # 表单有未应用改动
        switched = []
        app._select_step = lambda index, preserve_selection=False: switched.append(index)
        with patch.object(E.messagebox, "askyesnocancel", return_value=None) as ask:
            app._finish_step_drag(_FakeEvent())
        ask.assert_called_once()  # 松开时手势已结束，确认必须执行
        self.assertEqual(switched, [])  # 用户取消 → 不切步骤
        self.assertEqual(app.step_tree.selection_sets, ["0"])  # 选中恢复到当前步骤
        self.assertEqual(app.selected_index, 0)

    def test_release_plain_click_proceeds_on_discard(self):
        app = self._make_release_app()
        app.var_name.set("步骤一（改）")
        switched = []
        app._select_step = lambda index, preserve_selection=False: switched.append(index)
        with patch.object(E.messagebox, "askyesnocancel", return_value=False) as ask:
            app._finish_step_drag(_FakeEvent())
        ask.assert_called_once()
        self.assertEqual(switched, [1])  # 放弃改动 → 切到点击的步骤

    def test_release_clean_click_skips_dialog(self):
        app = self._make_release_app()
        switched = []
        app._select_step = lambda index, preserve_selection=False: switched.append(index)
        with patch.object(E.messagebox, "askyesnocancel") as ask:
            app._finish_step_drag(_FakeEvent())
        ask.assert_not_called()  # 表单与基线一致 → 不弹窗直接切
        self.assertEqual(switched, [1])

    def test_release_click_on_current_step_skips_dialog(self):
        app = self._make_release_app(pressed_row="0")
        switched = []
        app._select_step = lambda index, preserve_selection=False: switched.append(index)
        with patch.object(E.messagebox, "askyesnocancel") as ask:
            app._finish_step_drag(_FakeEvent())
        ask.assert_not_called()  # 点击当前步骤 → 无切换语义，不弹窗
        self.assertEqual(switched, [0])

    def test_release_real_drag_reorders_without_confirm(self):
        app = self._make_release_app(pressed_row="0")
        app.var_name.set("步骤一（改）")  # 重排路径保持既有取舍：不弹窗
        app.step_tree.row_under_cursor = "1"
        app._drag_hover_after = True  # 悬停在行 1 下半部 → 移到行 1 之后
        dirty_marked = []
        app._mark_dirty = lambda message="": dirty_marked.append(message)
        app._refresh_steps_tree = lambda: None
        app._refresh_overview = lambda: None
        switched = []
        app._select_step = lambda index, preserve_selection=False: switched.append(index)
        with patch.object(E.messagebox, "askyesnocancel") as ask:
            app._finish_step_drag(_FakeEvent())
        ask.assert_not_called()
        self.assertEqual([s["id"] for s in app.steps], ["s2", "s1"])  # 重排生效
        self.assertEqual(app.selected_index, 1)
        self.assertTrue(dirty_marked)
        self.assertEqual(switched, [1])

    def test_release_outside_rows_restores_selection(self):
        app = self._make_release_app()
        app.step_tree.row_under_cursor = ""  # 拖出树外松开
        app._drag_hover_iid = ""
        switched = []
        app._select_step = lambda index, preserve_selection=False: switched.append(index)
        with patch.object(E.messagebox, "askyesnocancel") as ask:
            app._finish_step_drag(_FakeEvent(y=999))
        ask.assert_not_called()
        self.assertEqual(switched, [])
        self.assertEqual(app.step_tree.selection_sets, ["0"])  # 恢复选中与表单一致
        self.assertEqual(app._dragging_step_iid, "")  # 拖拽状态已清理

    def test_multi_select_asks_and_blocks_on_cancel(self):
        """多选加载第一个选中步骤，同样先确认未应用改动（原先直接重载、绕过确认）。"""
        app = FormDirtyGuardTests._make_app(self)
        app.var_name.set("步骤一（改）")
        app.steps = [{"id": "s1"}, {"id": "s2"}]
        app.status_var = FakeVar("")

        class _Tree:
            def selection(self):
                return ("0", "1")

            def selection_set(self, value):
                pass

        app.step_tree = _Tree()
        loaded = []
        app._load_step_into_form = lambda step: loaded.append(step)
        with patch.object(E.messagebox, "askyesnocancel", return_value=None) as ask:
            app._on_tree_select()
        ask.assert_called_once()
        self.assertEqual(loaded, [])  # 用户取消 → 不加载、不丢改动

    def test_multi_select_proceeds_on_discard(self):
        app = FormDirtyGuardTests._make_app(self)
        app.var_name.set("步骤一（改）")
        app.steps = [{"id": "s1", "name": "步骤一"}, {"id": "s2", "name": "步骤二"}]
        app.status_var = FakeVar("")

        class _Tree:
            def selection(self):
                return ("0", "1")

            def selection_set(self, value):
                pass

        app.step_tree = _Tree()
        loaded = []
        app._load_step_into_form = lambda step: loaded.append(step)
        with patch.object(E.messagebox, "askyesnocancel", return_value=False) as ask:
            app._on_tree_select()
        ask.assert_called_once()
        self.assertEqual(loaded, [app.steps[0]])  # 放弃改动 → 加载第一个选中步骤
        self.assertEqual(app.selected_index, 0)


# ── 2. 控件库删除影响范围文案 ────────────────────────────────────────────


class DeleteConfirmMessageTests(unittest.TestCase):
    """删除控件会改写 flow_packages 下流程文件，确认框必须说明影响范围。"""

    def _make_app(self):
        # 控件库删除属于控件库维护对话框（ControlMapImportDialog），不是流程编辑器主窗口
        app = object.__new__(E.ControlMapImportDialog)
        app.window = FakeWindow()
        app.var_file_scope = FakeVar("single")
        app.control_tree = type("T", (), {"selection": staticmethod(lambda: ("0",))})()
        app.current_payload = {
            "flatControls": [{"name": "控件A"}],
            "controlDefinitions": [{"name": "控件A"}],
        }
        app._resolve_control_source_indexes = lambda selection, getter: [0]
        app._get_control_for_locator_test = lambda *args, **kwargs: None
        return app

    def test_confirm_message_mentions_flow_definition_rewrite(self):
        app = self._make_app()
        with patch.object(E.messagebox, "askyesno", return_value=False) as ask:
            app.delete_selected_controls()
        ask.assert_called_once()
        message = ask.call_args[0][1]
        self.assertIn("flow_packages", message)
        self.assertIn("来源失效", message)
        self.assertIn("控件A", message)

    def test_cancel_does_not_write_any_file(self):
        app = self._make_app()
        with patch.object(E.messagebox, "askyesno", return_value=False), \
                patch.object(E, "save_json_file") as save:
            app.delete_selected_controls()
        save.assert_not_called()


# ── 3. 队列窗口线程安全快照 ──────────────────────────────────────────────


class ThreadSafeSnapshotBehaviorTests(unittest.TestCase):
    """worker 线程只允许读 _prefs_snapshot，不允许读 Tk 变量。"""

    def _make(self):
        win = object.__new__(Q.TaskQueueWindow)
        win._prefs_snapshot = {"user": "", "token": "", "mine_only": False}
        return win

    def test_sync_updates_snapshot(self):
        win = self._make()
        win.user_var = FakeVar("alice")
        win.token_var = FakeVar("tok-1")
        win.mine_only_var = FakeVar(True)
        win._sync_prefs_snapshot()
        self.assertEqual(
            win._prefs_snapshot, {"user": "alice", "token": "tok-1", "mine_only": True}
        )

    def test_sync_is_defensive_when_vars_missing(self):
        win = self._make()
        win._sync_prefs_snapshot()  # 变量尚未创建时不抛异常
        self.assertEqual(win._prefs_snapshot, {"user": "", "token": "", "mine_only": False})

    def test_task_list_path_uses_snapshot_not_tk(self):
        win = self._make()
        win._prefs_snapshot = {"user": "bob", "token": "", "mine_only": True}
        # Tk 侧值与快照不同：worker 路径必须取快照
        win.mine_only_var = FakeVar(False)
        win.user_var = FakeVar("someone-else")
        self.assertEqual(win._task_list_path(), "/api/tasks?scope=mine&user=bob")

    def test_task_list_path_scope_all(self):
        win = self._make()
        self.assertEqual(win._task_list_path(), "/api/tasks?scope=all")

    def test_submit_local_flow_uses_snapshot_user(self):
        win = self._make()
        win._prefs_snapshot = {"user": "alice", "token": "", "mine_only": False}
        win.user_var = FakeVar("bob")  # 即便 Tk 值不同，worker 只认快照
        captured = {}
        win._post_json = (
            lambda path, payload: captured.setdefault("payload", payload) or {"name": "f"}
        )
        win._post_ui = lambda callback: callback()
        win._open_submit_dialog = lambda flows: captured.setdefault("flows", flows)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "flow.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"steps": []}, handle)
            win._submit_local_flow_worker(path)
        self.assertEqual(captured["payload"]["user"], "alice")

    def test_submit_local_flow_rejects_blank_snapshot_user(self):
        win = self._make()
        win._post_ui = lambda callback: callback()
        with patch.object(Q.messagebox, "showwarning") as warn:
            win._submit_local_flow_worker("不存在的文件.json")
        warn.assert_called_once()


class WorkerPathStaticGuardTests(unittest.TestCase):
    """静态守护：worker 线程路径不得再出现对 Tk 变量的读取（AST 级，不受注释影响）。"""

    @classmethod
    def setUpClass(cls):
        path = os.path.join(PROJECT_DIR, "wt_task_queue_window.py")
        with open(path, encoding="utf-8") as handle:
            cls.tree = ast.parse(handle.read())

    def _find_function(self, name):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ClassDef) and node.name == "TaskQueueWindow":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == name:
                        return item
        return None

    def test_worker_functions_do_not_touch_tk_vars(self):
        for func_name in (
            "_queue_request",
            "_task_list_path",
            "_submit_local_flow_worker",
            "_upload_local_flow_worker",
        ):
            node = self._find_function(func_name)
            self.assertIsNotNone(node, func_name)
            accessed = set()
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Attribute)
                    and isinstance(sub.value, ast.Name)
                    and sub.value.id == "self"
                ):
                    accessed.add(sub.attr)
            for forbidden in ("user_var", "token_var", "mine_only_var", "url_var", "monitor_url_var"):
                self.assertNotIn(
                    forbidden,
                    accessed,
                    "{} 不应读取 Tk 变量 {}".format(func_name, forbidden),
                )


# ── 4. 本地流程上传线程化 ────────────────────────────────────────────────


class UploadThreadingTests(unittest.TestCase):
    """「选择本地流程 JSON」的网络上传必须离开 UI 线程。"""

    def _make(self):
        win = object.__new__(Q.TaskQueueWindow)
        win._prefs_snapshot = {"user": "alice", "token": "", "mine_only": False}
        return win

    def test_pick_local_flow_dispatches_upload_to_thread(self):
        win = self._make()
        started = {}

        class _FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                started["target"] = target
                started["args"] = args
                started["daemon"] = daemon

            def start(self):
                started["started"] = True

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "flow.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"steps": []}, handle)
            with patch.object(Q.filedialog, "askopenfilename", return_value=path), \
                    patch.object(Q.threading, "Thread", _FakeThread):
                win._pick_local_flow_into_dialog(FakeWindow(), FakeListbox(), [])
        self.assertTrue(started.get("started"))
        self.assertEqual(started["target"], win._upload_local_flow_worker)
        self.assertTrue(started["daemon"])
        self.assertIn("alice", started["args"])

    def test_upload_worker_updates_list_on_success(self):
        win = self._make()
        win._post_json = lambda path, payload: {"name": "flow.json", "flowPath": "/x/flow.json"}
        win._post_ui = lambda callback: callback()
        dialog = FakeWindow()
        listbox = FakeListbox()
        flows = []
        with patch.object(Q.messagebox, "showinfo") as info:
            win._upload_local_flow_worker(
                dialog, listbox, flows, "C:/tmp/flow.json", {"steps": []}, "alice"
            )
        self.assertEqual(listbox.items, ["flow.json"])
        self.assertEqual(flows[0]["path"], "/x/flow.json")
        info.assert_called_once()

    def test_upload_worker_skips_ui_when_dialog_closed(self):
        win = self._make()
        win._post_json = lambda path, payload: {"name": "flow.json", "flowPath": "/x"}
        win._post_ui = lambda callback: callback()
        dialog = FakeWindow()
        dialog.destroy()  # 用户在上传期间关闭了对话框
        listbox = FakeListbox()
        flows = []
        with patch.object(Q.messagebox, "showinfo") as info:
            win._upload_local_flow_worker(
                dialog, listbox, flows, "C:/tmp/flow.json", {}, "alice"
            )
        info.assert_not_called()
        self.assertEqual(listbox.items, [])
        self.assertEqual(flows, [])

    def test_upload_worker_reports_error_when_dialog_alive(self):
        win = self._make()

        def _boom(path, payload):
            raise RuntimeError("connection refused")

        win._post_json = _boom
        win._post_ui = lambda callback: callback()
        win._friendly_error = lambda exc: "无法连接：" + str(exc)
        with patch.object(Q.messagebox, "showerror") as err:
            win._upload_local_flow_worker(
                FakeWindow(), FakeListbox(), [], "C:/tmp/f.json", {}, "alice"
            )
        err.assert_called_once()
        self.assertIn("无法连接", err.call_args[0][1])


# ── 5. 外部采集对话框：关窗落盘 / 强杀确认 ──────────────────────────────


class LauncherPersistOnCloseTests(unittest.TestCase):
    """手工输入（不经"浏览"按钮）的路径/地址，关窗前必须落盘。"""

    def _make_panel(self):
        panel = object.__new__(LP.ExternalCaptureDialog)
        panel.window = FakeWindow()
        panel.cfg = {}
        panel.var_uiapeek_exe = FakeVar("C:/tools/UiaPeek.exe")
        panel.var_axe_cli_exe = FakeVar("C:/tools/AxeWindowsCLI.exe")
        panel.var_base_url = FakeVar("http://127.0.0.1:9955")
        return panel

    def test_close_persists_manual_inputs(self):
        panel = self._make_panel()
        saved = {}
        with patch.object(LP, "_save_config", lambda cfg: saved.update(cfg)):
            panel._on_close()
        self.assertEqual(saved.get("uiapeek_exe"), "C:/tools/UiaPeek.exe")
        self.assertEqual(saved.get("axe_cli_exe"), "C:/tools/AxeWindowsCLI.exe")
        self.assertEqual(saved.get("uiapeek_base_url"), "http://127.0.0.1:9955")
        self.assertTrue(panel.window.destroyed)

    def test_persist_failure_does_not_block_close(self):
        panel = self._make_panel()

        def _boom():
            raise RuntimeError("disk full")

        panel._persist_paths = _boom
        panel._on_close()  # 不应抛出
        self.assertTrue(panel.window.destroyed)


class StopUiapeekConfirmTests(unittest.TestCase):
    """提权实例只能按进程名强杀（会连带手动实例），必须先确认。"""

    def _make_panel(self):
        panel = object.__new__(LP.ExternalCaptureDialog)
        panel.window = FakeWindow()
        panel._uiapeek_elevated = True
        panel._uiapeek_proc = None
        panel.var_service_status = FakeVar("运行中")
        panel._log = lambda *args, **kwargs: None
        return panel

    def test_stop_asks_before_kill(self):
        panel = self._make_panel()
        killed = []
        with patch.object(LP, "_kill_uiapeek", lambda: killed.append(True)), \
                patch.object(LP.messagebox, "askyesno", return_value=False) as ask:
            panel.stop_uiapeek_service()
        ask.assert_called_once()
        self.assertEqual(killed, [])
        self.assertTrue(panel._uiapeek_elevated)
        self.assertEqual(panel.var_service_status.get(), "运行中")

    def test_confirmed_stop_kills(self):
        panel = self._make_panel()
        killed = []
        with patch.object(LP, "_kill_uiapeek", lambda: killed.append(True)), \
                patch.object(LP.messagebox, "askyesno", return_value=True), \
                patch.object(LP.messagebox, "showinfo"):
            panel.stop_uiapeek_service()
        self.assertEqual(killed, [True])
        self.assertFalse(panel._uiapeek_elevated)


# ── 6. 流程异常末行着色 ─────────────────────────────────────────────────


class RecordedErrorKindTests(unittest.TestCase):
    """流程异常最后一行必须以 kind="error" 记录（AST 检查，不受注释影响）。"""

    def test_error_line_logged_with_error_kind(self):
        path = os.path.join(PROJECT_DIR, "WT_AUT_recorded.py")
        with open(path, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        found = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "log"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Name) and first.id == "error_msg"):
                continue
            keywords = {kw.arg: kw.value for kw in node.keywords}
            kind = keywords.get("kind")
            if isinstance(kind, ast.Constant) and kind.value == "error":
                found = True
        self.assertTrue(
            found, "流程异常最后一行应以 kind='error' 记录（AST 检查）"
        )


if __name__ == "__main__":
    unittest.main()
