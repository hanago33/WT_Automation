# encoding: utf-8
"""未审计模块审计修复轮（session-19）的回归测试（2026-09-29）。

覆盖审计报告「建议修复顺序 3-4」：

P1 第二批：
1. P1-3 控件库采集器定点/选中补采卸载到 worker 线程（不再 45 秒冻结 UI）；
2. P1-4/P1-5 检测器监控线程：选项快照传入（不读 Tk 变量）+ 代次校验（无残留双线程）
   + 异常不再被完全吞掉；
3. P1-7 合并入库：确认 + .bak 备份 + 临时文件原子替换 + 台账累计；
4. P1-8 模板制作器：候选框命名避重 + 同名 PNG 覆盖确认；
5. P1-9 launcher：日志经主线程 after 派发 + log_step 包装（保留落盘/状态上报）。

P2 批次（精选）：
- 控件库采集器：兜底展开键名、悬停连续错误完整清理、加载覆盖确认、高亮定时器、
  补采后标题恢复；
- 检测器：保存选中行、高亮定时器可取消、来源统计按文件；
- 模板制作器：索引原子写、Del 键不误删；
- 文本工具：拆分行数校验、清空确认、日志上限；
- generic_flow：监视窗置顶回退、log_step 文件写保护、进程名 basename、日志上限。

被测对象为真实方法（轻量替身）/ 源码标记断言，不实例化窗口、无需 Tk 交互环境。
"""
import ast
import io
import os
import queue as queue_module
import sys
import tempfile
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

# generic_flow 内部按「顶层模块名」互相导入（import generic_flow_executor），
# 必须先把该目录加入 sys.path 再顶层导入，与项目自身用法一致。
GENERIC_FLOW_DIR = os.path.join(PROJECT_DIR, "tools", "generic_flow")
if GENERIC_FLOW_DIR not in sys.path:
    sys.path.insert(0, GENERIC_FLOW_DIR)

try:
    import generic_launcher as GL  # noqa: E402
except Exception:  # pragma: no cover - 环境缺依赖时跳过相关用例
    GL = None
import generic_main_window as GM  # noqa: E402


# ── 替身 ────────────────────────────────────────────────────────────────


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeLabel:
    def __init__(self):
        self.texts = []

    def config(self, **kwargs):
        if "text" in kwargs:
            self.texts.append(kwargs["text"])


class FakeRoot:
    def __init__(self):
        self.calls = []
        self.cancelled = []

    def after(self, delay, callback=None, *args):
        self.calls.append((delay, callback, args))
        if callback is not None:
            callback(*args)
        return "after#1"

    def after_cancel(self, after_id):
        self.cancelled.append(after_id)


class FakeTextWidget:
    def __init__(self):
        self.texts = []
        self.states = []

    def configure(self, **kwargs):
        if "state" in kwargs:
            self.states.append(kwargs["state"])

    def insert(self, _index, text, *_args):
        self.texts.append(text)

    def index(self, _spec):
        return "1.0"

    def see(self, *_args):
        pass


class FakeListbox:
    def __init__(self, selection=()):
        self._selection = selection

    def curselection(self):
        return self._selection


def _read_source(*parts):
    path = os.path.join(PROJECT_DIR, *parts)
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def _find_class_method(tree, class_name, method_name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return item
    return None


# ── P1-3：补采线程化 ────────────────────────────────────────────────────


class SupplementWorkerDispatchTests(unittest.TestCase):
    def test_point_supplement_dispatches_to_worker(self):
        win = object.__new__(B.ControlMapBuilderApp)
        win._worker_queue = queue_module.Queue()
        win._start_worker_thread = lambda: None
        win.var_supplement_climb = FakeVar("0")
        win._bring_to_front_temporarily = lambda *a, **k: None
        with patch.object(B.user32, "GetCursorPos", lambda *_a: None):
            win._do_point_supplement()
        func, args, kwargs, callback = win._worker_queue.get_nowait()
        self.assertIs(func, B.collect_subtree_at_point)
        self.assertEqual(args, (0, 0))
        self.assertEqual(callback, win._supplement_worker_done)
        self.assertIn("climb_levels", kwargs)
        self.assertIn("status_callback", kwargs)

    def test_selected_supplement_dispatches_to_worker(self):
        win = object.__new__(B.ControlMapBuilderApp)
        win._worker_queue = queue_module.Queue()
        win._start_worker_thread = lambda: None
        win._bring_to_front_temporarily = lambda *a, **k: None
        win._pending_supplement_expected = {
            "boundingBox": {"left": 0, "top": 0, "right": 10, "bottom": 10}
        }
        with patch.object(B, "_rect_center", return_value=(5, 5)):
            win._do_selected_supplement()
        func, args, kwargs, callback = win._worker_queue.get_nowait()
        self.assertEqual(args, (5, 5))
        self.assertIn("expected", kwargs)
        self.assertEqual(callback, win._supplement_worker_done)

    def test_on_subtree_collected_respects_interactive(self):
        win = object.__new__(B.ControlMapBuilderApp)
        captured = {}
        win._finish_supplement = lambda *a, **k: captured.update(k)
        win._worker_last_source = "定点(1,2)"
        win._on_subtree_collected(([{"n": 1}], {}, ""), interactive=True)
        self.assertTrue(captured.get("interactive"))

    def test_supplement_worker_done_uses_interactive_true(self):
        win = object.__new__(B.ControlMapBuilderApp)
        captured = {}
        win._finish_supplement = lambda *a, **k: captured.update(k)
        win._worker_last_source = "选中控件"
        win._supplement_worker_done(([], {}, "boom"))
        self.assertTrue(captured.get("interactive"))

    def test_hover_path_resets_view_keys_after_supplement(self):
        """悬停路径回调也必须重置查看层 key。

        80d8deb 重构时这三行被误挪进 _supplement_worker_done（仅定点/选中路径），
        悬停/热键补采后层级树不再自动聚焦新入库控件（session-21 修正）。
        """
        win = object.__new__(B.ControlMapBuilderApp)
        win._finish_supplement = lambda *a, **k: None
        win._worker_last_source = "悬停(1,2)"
        win._hover_last_hit_key = "aid|x|y"
        win._hover_last_fresh_xy = (1, 2)
        win._hover_existing_index = 3
        win._on_subtree_collected(([{"n": 1}], {}, ""), interactive=False)
        self.assertEqual(win._hover_last_hit_key, "")
        self.assertIsNone(win._hover_last_fresh_xy)
        self.assertIsNone(win._hover_existing_index)

    def test_supplement_worker_done_also_resets_view_keys(self):
        win = object.__new__(B.ControlMapBuilderApp)
        win._finish_supplement = lambda *a, **k: None
        win._worker_last_source = "选中控件"
        win._hover_last_hit_key = "aid|x|y"
        win._hover_last_fresh_xy = (1, 2)
        win._hover_existing_index = 3
        win._supplement_worker_done(([{"n": 1}], {}, ""))
        self.assertEqual(win._hover_last_hit_key, "")
        self.assertIsNone(win._hover_last_fresh_xy)
        self.assertIsNone(win._hover_existing_index)


# ── P1-4 / P1-5：监控线程纪律 ───────────────────────────────────────────


class MonitorThreadDisciplineTests(unittest.TestCase):
    def test_monitor_loop_does_not_touch_tk_vars(self):
        src = _read_source("control_live_detector.py")
        tree = ast.parse(src)
        func = _find_class_method(tree, "ControlLiveDetectorWindow", "_monitor_loop")
        self.assertIsNotNone(func)
        accessed = set()
        for node in ast.walk(func):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
            ):
                accessed.add(node.attr)
        for forbidden in ("depth_var", "smart_var", "raw_view_var", "backend_var"):
            self.assertNotIn(forbidden, accessed, "_monitor_loop 不得读 Tk 变量 " + forbidden)

    def test_start_monitoring_snapshots_and_generation(self):
        src = _read_source("control_live_detector.py")
        self.assertIn('"depth": depth,', src)
        self.assertIn('self._monitor_gen = getattr(self, "_monitor_gen", 0) + 1', src)
        self.assertIn("args=(monitor_opts, self._monitor_gen)", src)
        self.assertIn('while self.monitoring and getattr(self, "_monitor_gen", 0) == gen:', src)

    def test_monitor_loop_exits_on_gen_mismatch(self):
        win = object.__new__(D.ControlLiveDetectorWindow)
        win.monitoring = True
        win.paused = False
        win._monitor_gen = 5
        # 代次不匹配：循环体一次都不执行，函数立即返回（不依赖任何 Tk 属性）
        win._monitor_loop(
            {"depth": 1, "smart": False, "use_raw": False, "backend": "uia"}, 4
        )


# ── P1-7：合并写盘安全 ──────────────────────────────────────────────────


class MergeSafetyTests(unittest.TestCase):
    def test_merge_confirm_backup_atomic_ledger(self):
        src = _read_source("control_live_detector.py")
        self.assertIn("确认合并入库", src)  # 二次确认
        self.assertIn('backup_path = tgt_path + ".bak"', src)  # 备份
        self.assertIn("os.replace(tmp_path, tgt_path)", src)  # 原子替换
        self.assertIn(
            'meta["mergeAdded"] = int(meta.get("mergeAdded", 0) or 0) + added', src
        )  # 台账累计


# ── P1-8：模板命名避重与覆盖确认 ────────────────────────────────────────


class TemplateNameTests(unittest.TestCase):
    def _make_win(self, names):
        win = object.__new__(T.TemplateBuilderApp)
        win.candidates = [object() for _ in range(len(names))]
        win.template_names = list(names)
        win.selected_index = None
        win.selected_indices = set()
        win.file_name_var = FakeVar("")
        win.refresh_listbox = lambda: None
        win.refresh_canvas = lambda: None
        win.update_preview = lambda: None
        return win

    def test_add_candidate_region_avoids_dangling_name(self):
        prefix = T.DEFAULT_TEMPLATE_PREFIX
        win = self._make_win([f"{prefix}_001", f"{prefix}_003"])  # 删过 #2 的残留
        win.add_candidate_region(object(), select_new=True)
        self.assertEqual(win.template_names[-1], f"{prefix}_004")
        self.assertNotIn(win.template_names[-1], win.template_names[:-1])

    def test_overwrite_policy_asks_once(self):
        win = object.__new__(T.TemplateBuilderApp)
        win._overwrite_policy = None
        with patch.object(T.os.path, "exists", return_value=True), \
                patch.object(T.messagebox, "askyesno", return_value=False) as ask:
            self.assertFalse(win._confirm_overwrite_template("x.png"))
            self.assertFalse(win._confirm_overwrite_template("y.png"))
        ask.assert_called_once()


# ── P1-9：launcher 日志线程纪律 ─────────────────────────────────────────


@unittest.skipUnless(GL is not None, "generic_launcher 导入失败（环境缺依赖）")
class LauncherThreadSafeLogTests(unittest.TestCase):
    def test_append_dispatches_via_after(self):
        win = object.__new__(GL.GenericLauncherUI)
        win.root = FakeRoot()
        win.log = FakeTextWidget()
        win._append("hello")
        self.assertEqual(len(win.root.calls), 1)
        delay, callback, args = win.root.calls[0]
        self.assertEqual(delay, 0)
        self.assertEqual(callback, win._append_ui)
        self.assertEqual(args, ("hello",))
        self.assertEqual(win.log.texts, ["hello\n"])

    def test_log_step_wrapped_not_replaced(self):
        src = _read_source("tools", "generic_flow", "generic_launcher.py")
        self.assertIn("_launcher_log_wrapped", src)
        self.assertIn("generic_automation.log_step = _launcher_log_step", src)
        self.assertNotIn("log_step = lambda msg: self._append(str(msg))", src)


# ── P2：控件库采集器 ────────────────────────────────────────────────────


class ControlMapBuilderP2Tests(unittest.TestCase):
    def test_p2_markers(self):
        src = _read_source("build_control_map_library.py")
        self.assertIn("last_group.get('key', '')", src)  # 兜底展开键名
        self.assertIn("悬停补采连续异常，已自动停止。", src)  # 连续错误完整清理
        self.assertIn("确认加载", src)  # 加载覆盖确认
        self.assertIn("_locator_highlight_after_id = overlay.after", src)  # 高亮定时器
        self.assertIn("补采复用扫描进度回调改写了窗口标题", src)  # 标题恢复


# ── P2：实时检测器 ──────────────────────────────────────────────────────


class DetectorP2Tests(unittest.TestCase):
    def _make(self):
        win = object.__new__(D.ControlLiveDetectorWindow)
        win.current_ctrl_info = {"name": "cap"}
        win.saved_controls = []
        win.saved_count_label = FakeLabel()
        win.status_label = FakeLabel()
        win.window = None
        win.current_matches = [
            {"library_definition": {"name": "A"}, "library_control": {"id": "A"},
             "score": 100, "reasons": []},
            {"library_definition": {"name": "B"}, "library_control": {"id": "B"},
             "score": 90, "reasons": []},
        ]
        return win

    def test_save_current_match_uses_selection(self):
        win = self._make()
        win.match_listbox = FakeListbox(selection=(1,))
        win.save_current_match()
        self.assertEqual(win.saved_controls[0]["matchedControl"], {"id": "B"})

    def test_save_current_match_falls_back_to_first(self):
        win = self._make()
        win.match_listbox = FakeListbox(selection=())
        win.save_current_match()
        self.assertEqual(win.saved_controls[0]["matchedControl"], {"id": "A"})

    def test_sources_counted_per_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib_dir = os.path.join(tmp, "control_maps", "library")
            os.makedirs(lib_dir)
            with io.open(os.path.join(lib_dir, "a.json"), "w", encoding="utf-8") as handle:
                handle.write('{"flatControls": [{"name": "ctrl"}]}')
            with io.open(os.path.join(lib_dir, "b.json"), "w", encoding="utf-8") as handle:
                handle.write('{"flatControls": []}')
            _controls, sources = D.load_all_other_libraries(base_dir=tmp)
        self.assertEqual(sources, ["a.json"])  # 空文件不再被误记为来源

    def test_highlight_after_id_tracked(self):
        src = _read_source("control_live_detector.py")
        self.assertIn("_match_highlight_after_id", src)


# ── P2：模板制作器 ──────────────────────────────────────────────────────


class TemplateToolP2Tests(unittest.TestCase):
    def test_index_atomic_write(self):
        src = _read_source("build_image_template_library.py")
        self.assertIn("os.replace(tmp_path, index_path)", src)

    def test_delete_key_ignored_in_entry(self):
        win = object.__new__(T.TemplateBuilderApp)
        win.selected_indices = {0}
        called = []
        win.delete_selected_regions = lambda: called.append(True)

        class Entry:
            pass

        win.on_delete_key(types.SimpleNamespace(widget=Entry()))
        self.assertEqual(called, [])  # 输入控件内按 Del → 不删候选框
        win.on_delete_key(types.SimpleNamespace(widget=object()))
        self.assertEqual(called, [True])


# ── P2：文本工具 ────────────────────────────────────────────────────────


class TextToolsP2Tests(unittest.TestCase):
    def test_split_positive_validation(self):
        src = _read_source("text_tools.py")
        self.assertIn("每个文件行数必须是正整数", src)

    def test_log_line_cap_applied_twice(self):
        src = _read_source("text_tools.py")
        self.assertEqual(src.count("total_lines > 500"), 2)

    def test_text_tools_clear_list_asks(self):
        card = object.__new__(TX.TxtMergeCard)
        card.files = ["a.txt"]
        refreshed = []
        card.refresh_list = lambda: refreshed.append(True)
        with patch.object(TX.messagebox, "askyesno", return_value=False) as ask:
            card.clear_list()
        ask.assert_called_once()
        self.assertEqual(card.files, ["a.txt"])
        self.assertEqual(refreshed, [])
        with patch.object(TX.messagebox, "askyesno", return_value=True):
            card.clear_list()
        self.assertEqual(card.files, [])
        self.assertEqual(refreshed, [True])

    def test_txt_merge_clear_list_asks(self):
        app = object.__new__(TM.TxtMergeApp)
        app.files = ["a.txt"]
        refreshed = []
        app.refresh_list = lambda: refreshed.append(True)
        with patch.object(TM.messagebox, "askyesno", return_value=False) as ask:
            app.clear_list()
        ask.assert_called_once()
        self.assertEqual(app.files, ["a.txt"])


# ── P2：generic_flow ────────────────────────────────────────────────────


class GenericFlowP2Tests(unittest.TestCase):
    class _Proc:
        def __init__(self, pid, name):
            self.info = {"pid": pid, "name": name}

    def test_pid_of_takes_basename(self):
        with patch.object(
            GM.psutil, "process_iter", return_value=[self._Proc(42, "MUPSmartClient.exe")]
        ):
            pids = GM._pid_of(r"C:\Program Files\Meteodyn\MUPSmartClient.exe")
        self.assertEqual(pids, [42])

    def test_markers(self):
        src = _read_source("tools", "generic_flow", "generic_automation.py")
        self.assertIn("_release_notice_topmost", src)  # 置顶回退
        self.assertIn("except OSError:", src)  # log_step 文件写保护
        self.assertIn("total_lines > 1000", src)  # 监视窗日志上限
        src_launcher = _read_source("tools", "generic_flow", "generic_launcher.py")
        self.assertIn("total_lines > 1000", src_launcher)  # launcher 日志上限


if __name__ == "__main__":
    unittest.main()
