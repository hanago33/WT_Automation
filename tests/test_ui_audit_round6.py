# encoding: utf-8
"""未审计模块审计修复轮二（session-20）的回归测试（2026-09-29）。

覆盖：
- 控件库采集器：合并后重建分组/勾选重置（P1◐）、未应用改名提示（P1◐）、
  画框 overlay 构造失败恢复主窗、悬停日志文件轮转、目标进程枚举 TTL 缓存；
- 模板制作器：打开目录回调异常提示、索引失败回滚图片、文件名失焦/回车提交（P1◐）、
  重新加载截图确认（P1◐）；
- 实时检测器：搜索定位高亮在刷新后保持；
- 文本工具：失败计数与结果弹窗（不再全失败仍报"完成"，P1◐）、
  文件夹回调异常处理；
- txt_merge：COM 初始化顺序（拖拽注册前置）；
- generic_flow：_ui_safe_call 后台线程不再直调 Tk、生成代码相对导入兼容。

被测对象为真实方法（轻量替身）/ 源码标记断言，不实例化窗口、无需 Tk 交互环境。
"""
import io
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import build_control_map_library as B
import build_image_template_library as T
import control_live_detector as D
import text_tools as TX
import txt_merge_tool as TM

# generic_flow 内部按「顶层模块名」互相导入，需先插目录再顶层导入
GENERIC_FLOW_DIR = os.path.join(PROJECT_DIR, "tools", "generic_flow")
if GENERIC_FLOW_DIR not in sys.path:
    sys.path.insert(0, GENERIC_FLOW_DIR)
try:
    import generic_automation as GA  # noqa: E402
except Exception:  # pragma: no cover
    GA = None


# ── 替身 ────────────────────────────────────────────────────────────────


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeRoot:
    """记录窗口操作的最小根窗口替身。"""

    def __init__(self):
        self.calls = []

    def update(self):
        self.calls.append("update")

    def deiconify(self):
        self.calls.append("deiconify")

    def lift(self):
        self.calls.append("lift")

    def focus_force(self):
        self.calls.append("focus_force")

    def update_idletasks(self):
        self.calls.append("update_idletasks")


class FakeWidget:
    def configure(self, **kwargs):
        pass


def _drain_scheduled(card, scheduled):
    """无 Tk 环境：模拟主线程事件循环，反复执行 after 收集的轮询回调直到静止。"""
    deadline = time.time() + 5
    while time.time() < deadline:
        if not scheduled:
            thread = getattr(card, "_run_thread", None)
            if thread is None or not thread.is_alive():
                break
        for fn in list(scheduled):
            scheduled.remove(fn)
            fn()
        time.sleep(0.01)


def _read_source(*parts):
    path = os.path.join(PROJECT_DIR, *parts)
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


# ── 控件库采集器 ────────────────────────────────────────────────────────


class ControlMapBuilderRoundTests(unittest.TestCase):
    def test_merge_rebuilds_groups_and_resets_checked(self):
        src = _read_source("build_control_map_library.py")
        self.assertIn("（已重建分组并全量勾选）", src)
        # _do_merge 成功路径：重建分组 + 全量勾选 + 清空命名输入
        idx = src.find("（已重建分组并全量勾选）")
        window = src[max(0, idx - 1200): idx]
        self.assertIn("self._rebuild_control_groups()", window)
        self.assertIn("self._all_checked_mode = True", window)
        self.assertIn("self.var_saved_control_name.set(\"\")", window)

    def test_has_pending_control_alias(self):
        win = object.__new__(B.ControlMapBuilderApp)
        win.current_payload = {
            "flatControls": [{"savedControlName": "旧名", "savedControlId": "old_id"}]
        }
        win.var_saved_control_name = FakeVar("新名")
        win.var_saved_control_id = FakeVar("")
        win._get_selected_tree_index = lambda: 0
        self.assertTrue(win._has_pending_control_alias())
        win.var_saved_control_name.set("旧名")
        self.assertFalse(win._has_pending_control_alias())
        win.var_saved_control_id.set("new_id")
        self.assertTrue(win._has_pending_control_alias())

    def test_region_overlay_failure_restores_window(self):
        win = object.__new__(B.ControlMapBuilderApp)
        win.root = FakeRoot()
        win._region_capture_title = ""
        with patch.object(B, "RegionPickerOverlay", side_effect=RuntimeError("boom")), \
                patch.object(B.messagebox, "showerror") as err:
            win._show_region_overlay(False)
        self.assertIn("deiconify", win.root.calls)
        err.assert_called_once()

    def test_hover_log_rotates_when_oversize(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = os.path.join(tmp, "hover.log")
            with io.open(log_path, "w", encoding="utf-8") as handle:
                handle.write("seed\n")
            with patch.object(B, "_HOVER_MONITOR_LOG", log_path), \
                    patch.object(B.os.path, "getsize", return_value=9 * 1024 * 1024), \
                    patch("builtins.print"):
                B._hover_log("hello")
            self.assertTrue(os.path.exists(log_path + ".old"))  # 旧文件已轮转
            self.assertTrue(os.path.exists(log_path))  # 新文件已续写

    def test_target_pids_cached_within_ttl(self):
        import psutil
        win = object.__new__(B.ControlMapBuilderApp)
        win.current_payload = {"flatControls": [{"processId": "111"}]}
        counter = {"n": 0}

        def fake_iter(*_a, **_k):
            counter["n"] += 1
            return []

        with patch.object(psutil, "process_iter", fake_iter):
            first = win._get_target_process_ids()
            second = win._get_target_process_ids()
        self.assertEqual(first, ["111"])
        self.assertEqual(second, ["111"])
        self.assertEqual(counter["n"], 1, "TTL 内不应重复全量枚举进程")


# ── 模板制作器 ──────────────────────────────────────────────────────────


class TemplateBuilderRoundTests(unittest.TestCase):
    def test_open_output_dir_error_reported(self):
        win = object.__new__(T.TemplateBuilderApp)
        win.output_dir_var = FakeVar("somewhere")
        with patch.object(T.os, "makedirs", side_effect=OSError("denied")), \
                patch.object(T.messagebox, "showerror") as err:
            win.open_output_dir()
        err.assert_called_once()

    def test_index_failure_rolls_back_image(self):
        src = _read_source("build_image_template_library.py")
        self.assertIn("图片已回滚（索引写入失败）", src)
        self.assertIn("os.remove(output_path)", src)

    def test_commit_file_name_entry(self):
        win = object.__new__(T.TemplateBuilderApp)
        win.selected_index = 0
        win.template_names = ["old_name"]
        win.file_name_var = FakeVar("new_name")
        refreshed = []
        win.refresh_listbox = lambda: refreshed.append(True)
        win._commit_file_name_entry()
        self.assertEqual(win.template_names[0], "new_name")
        self.assertEqual(refreshed, [True])
        win._commit_file_name_entry()  # 无变化不重复刷新
        self.assertEqual(refreshed, [True])

    def test_commit_file_name_entry_no_selection(self):
        win = object.__new__(T.TemplateBuilderApp)
        win.selected_index = None
        win.template_names = []
        win.file_name_var = FakeVar("x")
        win._commit_file_name_entry()  # 不应抛异常

    def test_listbox_select_commits_pending_rename_before_switch(self):
        """点击列表切换候选时，未提交的改名必须先落到【原选中候选】。

        实测 tk 8.6 真实事件流中 <<ListboxSelect>> 先于 Entry <FocusOut> 触发：
        若只靠失焦提交，切换时 file_name_var 已被新候选名字覆盖，手工改名
        仍然静默丢失（原缺陷复现）。on_listbox_select 须先提交再切换。
        """
        win = object.__new__(T.TemplateBuilderApp)
        win.selected_index = 0
        win.template_names = ["tpl_001", "tpl_002"]
        win.file_name_var = FakeVar("手工改名")  # 用户已输入但未点“应用”

        class _Listbox:
            def __init__(self, selection):
                self._selection = selection

            def curselection(self):
                return self._selection

        win.listbox = _Listbox((1,))
        win.update_preview = lambda: None
        win.refresh_canvas = lambda: None
        refreshed = []
        win.refresh_listbox = lambda: refreshed.append(True)
        win.on_listbox_select(None)
        # 改名落到原候选 0，候选 1 不被污染，输入框显示新候选的名字
        self.assertEqual(win.template_names[0], "手工改名")
        self.assertEqual(win.template_names[1], "tpl_002")
        self.assertEqual(win.selected_index, 1)
        self.assertEqual(win.file_name_var.get(), "tpl_002")
        self.assertEqual(refreshed, [True])  # 提交时刷新了一次列表

    def test_listbox_select_without_pending_rename_is_noop_commit(self):
        """无未提交改动时切换候选不产生额外写入。"""
        win = object.__new__(T.TemplateBuilderApp)
        win.selected_index = 0
        win.template_names = ["tpl_001", "tpl_002"]
        win.file_name_var = FakeVar("tpl_001")

        class _Listbox:
            def curselection(self):
                return (1,)

        win.listbox = _Listbox()
        win.update_preview = lambda: None
        win.refresh_canvas = lambda: None
        win.refresh_listbox = lambda: None
        win.on_listbox_select(None)
        self.assertEqual(win.template_names, ["tpl_001", "tpl_002"])
        self.assertEqual(win.selected_index, 1)

    def test_load_screenshot_asks_before_clearing(self):
        win = object.__new__(T.TemplateBuilderApp)
        win.candidates = [object()]
        with patch.object(T.messagebox, "askyesno", return_value=False) as ask, \
                patch.object(T.cv2, "imread") as imread:
            win.load_screenshot("x.png")
        ask.assert_called_once()
        imread.assert_not_called()  # 用户取消 → 未加载


# ── 实时检测器 ──────────────────────────────────────────────────────────


class DetectorRoundTests(unittest.TestCase):
    def test_highlight_pin_markers(self):
        src = _read_source("control_live_detector.py")
        self.assertIn("self._highlight_pin = (found_idx, time.time() + 1.5)", src)
        idx = src.find("搜索定位的固定高亮在有效期内重设")
        window = src[max(0, idx - 400): idx + 500]
        self.assertIn("self.match_listbox.itemconfig(pin_idx", window)


# ── 文本工具 ────────────────────────────────────────────────────────────


class TextToolsRoundTests(unittest.TestCase):
    def test_csv_run_all_failed_shows_error(self):
        card = object.__new__(TX.CsvConvertCard)
        card.csv_files = [os.path.join(tempfile.gettempdir(), "nonexistent_xyz.csv")]
        card.out_dir = tempfile.gettempdir()
        card.progress = {}
        card.btn_run = FakeWidget()
        card.conv_var = FakeVar("txt")
        card.merge_var = FakeVar("single")          # run() 选项快照需要（读但不使用）
        card.merge_single_header = FakeVar(True)    # 同上
        card.txt_enc_var = FakeVar("utf-8")
        card.log = lambda _m: None
        card.update_idletasks = lambda: None
        scheduled = []
        card.after = lambda ms, fn: scheduled.append(fn)  # 无 Tk 环境：收集轮询回调
        with patch.object(TX.messagebox, "showerror") as err, \
                patch.object(TX.messagebox, "showinfo") as info, \
                patch.object(TX.messagebox, "showwarning") as warn, \
                patch.object(TX.messagebox, "askyesno", return_value=True):
            card.run()
            _drain_scheduled(card, scheduled)
        err.assert_called_once()
        info.assert_not_called()
        warn.assert_not_called()

    def test_text_tools_run_all_failed_shows_error(self):
        card = object.__new__(TX.TextToolsCard)
        card.files = [os.path.join(tempfile.gettempdir(), "nonexistent_xyz.txt")]
        card.out_dir = tempfile.gettempdir()
        card.op_var = FakeVar("dedupe")
        card.out_enc = FakeVar("utf-8")
        card.split_var = FakeVar("1000")            # 参数快照需要
        card.filter_kw = FakeVar("")
        card.filter_mode = FakeVar("keep")
        card.replace_old = FakeVar("")
        card.replace_new = FakeVar("")
        card.affix_pre = FakeVar("")
        card.affix_suf = FakeVar("")
        card.case_mode = FakeVar("upper")
        card.enc_target = FakeVar("utf-8")
        card.progress = {}
        card.btn_run = FakeWidget()
        card.log = lambda _m: None
        card.update_idletasks = lambda: None
        scheduled = []
        card.after = lambda ms, fn: scheduled.append(fn)
        with patch.object(TX.messagebox, "showerror") as err, \
                patch.object(TX.messagebox, "showinfo") as info:
            card.run()
            _drain_scheduled(card, scheduled)
        err.assert_called_once()
        info.assert_not_called()

    def test_add_folder_error_reported(self):
        card = object.__new__(TX.TxtMergeCard)
        with patch.object(TX.filedialog, "askdirectory", return_value="X:/nonexistent_xyz"), \
                patch.object(TX.os, "listdir", side_effect=OSError("denied")), \
                patch.object(TX.messagebox, "showerror") as err:
            card.add_folder()
        err.assert_called_once()

    def test_txt_merge_add_folder_error_reported(self):
        app = object.__new__(TM.TxtMergeApp)
        with patch.object(TM.filedialog, "askdirectory", return_value="X:/nonexistent_xyz"), \
                patch.object(TM.os, "listdir", side_effect=OSError("denied")), \
                patch.object(TM.messagebox, "showerror") as err:
            app.add_folder()
        err.assert_called_once()


# ── txt_merge：COM 初始化顺序 ───────────────────────────────────────────


class TxtMergeComOrderTests(unittest.TestCase):
    def test_com_init_before_app_construction(self):
        src = _read_source("txt_merge_tool.py")
        idx_init = src.find("pythoncom.CoInitialize()")
        idx_app = src.find("app = TxtMergeApp(root)")
        self.assertGreater(idx_init, 0)
        self.assertGreater(idx_app, 0)
        self.assertLess(
            idx_init, idx_app,
            "CoInitialize 必须在 App 构建（RegisterDragDrop）之前执行",
        )


# ── generic_flow ────────────────────────────────────────────────────────


@unittest.skipUnless(GA is not None, "generic_automation 导入失败（环境缺依赖）")
class UiSafeCallTests(unittest.TestCase):
    def test_worker_thread_never_calls_directly(self):
        results = []
        with patch.object(GA, "monitor_window", None):
            worker = threading.Thread(
                target=lambda: results.append(GA._ui_safe_call(lambda: None))
            )
            worker.start()
            worker.join(timeout=2)
        self.assertEqual(results, [False], "后台线程在无 after 可用时不得直调 Tk")

    def test_main_thread_direct_fallback(self):
        called = []
        with patch.object(GA, "monitor_window", None):
            ok = GA._ui_safe_call(lambda: called.append(True))
        self.assertTrue(ok)
        self.assertEqual(called, [True])


class RelativeImportFallbackTests(unittest.TestCase):
    def test_locator_has_import_fallback(self):
        src = _read_source("tools", "generic_flow", "generic_flow_locator.py")
        self.assertIn("except ImportError:", src)
        self.assertIn("from generic_main_window import find_main_windows_by_process", src)

    def test_generator_template_has_import_fallback(self):
        src = _read_source("tools", "generic_flow", "make_generic_copy.py")
        self.assertIn("except ImportError:", src)
        self.assertIn("from generic_main_window import find_main_windows_by_process", src)


if __name__ == "__main__":
    unittest.main()
