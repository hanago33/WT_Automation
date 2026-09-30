# encoding: utf-8
"""text_tools 线程化测试（待修改清单 #1）。

双向断言：批量转换/处理必须在 worker 线程执行（基线 UI 线程同步实现时失败）；
覆盖确认改为运行前预扫描四选一（基线在循环内弹窗，无预扫描方法 → 收集/属性错误）；
取消在逐文件边界生效且收尾提示「已取消（完成 N/M）」。
"""
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import text_tools
from _tk_support import shared_tk_root


def _pump(root, predicate, timeout=5.0, settle=30):
    deadline = time.time() + timeout
    while not predicate() and time.time() < deadline:
        root.update()
        time.sleep(0.01)
    for _ in range(settle):
        root.update()
        time.sleep(0.01)


def _make_csvs(tmp_dir, n=2):
    paths = []
    for i in range(n):
        p = os.path.join(tmp_dir, f"data{i}.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write("名称,数值\n甲,1\n乙,2\n")
        paths.append(p)
    return paths


class CsvPreScanTests(unittest.TestCase):
    """预扫描冲突清单与策略解析（纯逻辑层，不启动 worker）。"""

    @classmethod
    def setUpClass(cls):
        cls.tk, cls.root = shared_tk_root()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.card = text_tools.CsvConvertCard(self.root)
        self.card.csv_files = _make_csvs(self.tmp.name)
        self.out = os.path.join(self.tmp.name, "out")
        self.card.out_dir = self.out

    def test_collect_targets_per_mode(self):
        self.card.conv_var.set("xlsx")
        self.card.merge_var.set("single")
        self.assertEqual(self.card._collect_output_targets(self.out), [
            os.path.join(self.out, "data0.xlsx"),
            os.path.join(self.out, "data1.xlsx"),
        ])
        self.card.merge_var.set("multi")
        self.assertEqual(self.card._collect_output_targets(self.out),
                         [os.path.join(self.out, "merged.xlsx")])
        self.card.merge_var.set("one")
        self.assertEqual(self.card._collect_output_targets(self.out),
                         [os.path.join(self.out, "merged_single.xlsx")])
        self.card.conv_var.set("txt")
        self.assertEqual([os.path.basename(p) for p in self.card._collect_output_targets(self.out)],
                         ["data0.txt", "data1.txt"])

    def test_resolve_policy_without_conflicts_skips_dialog(self):
        with patch.object(self.card, "_ask_overwrite_policy_dialog") as dialog:
            policy, allow = self.card._resolve_overwrite_policy([])
        self.assertEqual(policy, "overwrite")
        dialog.assert_not_called()

    def test_resolve_policy_ask_builds_allow_set(self):
        conflicts = [os.path.join(self.out, "data0.xlsx"), os.path.join(self.out, "data1.xlsx")]
        with patch.object(self.card, "_ask_overwrite_policy_dialog", return_value="ask"), \
             patch("text_tools.messagebox.askyesno", side_effect=[True, False]):
            policy, allow = self.card._resolve_overwrite_policy(conflicts)
        self.assertEqual(policy, "ask")
        self.assertEqual(allow, {conflicts[0]})


class CsvWorkerRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tk, cls.root = shared_tk_root()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.card = text_tools.CsvConvertCard(self.root)
        self.card.csv_files = _make_csvs(self.tmp.name)
        self.out = os.path.join(self.tmp.name, "out")
        self.card.out_dir = self.out
        self.card.conv_var.set("xlsx")
        self.card.merge_var.set("single")

    def test_run_executes_on_worker_thread(self):
        """双向断言：转换在 worker 线程执行（基线同步实现时失败）。"""
        seen = {}
        real_convert = text_tools.convert_csv_to_sheet

        def spy(ws, path, **kwargs):
            seen["thread"] = threading.current_thread()
            return real_convert(ws, path, **kwargs)

        with patch("text_tools.convert_csv_to_sheet", side_effect=spy), \
             patch("text_tools.messagebox.showinfo") as showinfo, \
             patch.object(self.card, "_ask_overwrite_policy_dialog",
                          side_effect=AssertionError("无冲突时不应弹出策略对话框")):
            self.card.run()
            _pump(self.root, lambda: self.card.btn_run.cget("text") == "开始转换")

        self.assertTrue(seen)
        self.assertIsNot(seen["thread"], threading.main_thread())
        self.assertTrue(os.path.isfile(os.path.join(self.out, "data0.xlsx")))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "data1.xlsx")))
        self.assertIn("全部处理完成", self.card.log_text.get("1.0", "end"))
        self.assertIn("转换完成", showinfo.call_args.args[1])

    def test_prescan_skip_keeps_existing_files(self):
        dst0 = os.path.join(self.out, "data0.xlsx")
        os.makedirs(self.out)
        with open(dst0, "wb") as f:
            f.write(b"old-bytes")

        with patch.object(self.card, "_ask_overwrite_policy_dialog",
                          return_value="skip") as dialog, \
             patch("text_tools.messagebox.showinfo"):
            self.card.run()
            _pump(self.root, lambda: self.card.btn_run.cget("text") == "开始转换")

        dialog.assert_called_once_with([dst0])  # 只有 data0 冲突
        with open(dst0, "rb") as f:
            self.assertEqual(f.read(), b"old-bytes")  # 跳过：原文件未动
        self.assertTrue(os.path.isfile(os.path.join(self.out, "data1.xlsx")))
        self.assertIn("[跳过]", self.card.log_text.get("1.0", "end"))

    def test_prescan_overwrite_rewrites_existing(self):
        dst0 = os.path.join(self.out, "data0.xlsx")
        os.makedirs(self.out)
        with open(dst0, "wb") as f:
            f.write(b"old-bytes")

        with patch.object(self.card, "_ask_overwrite_policy_dialog",
                          return_value="overwrite"), \
             patch("text_tools.messagebox.showinfo"):
            self.card.run()
            _pump(self.root, lambda: self.card.btn_run.cget("text") == "开始转换")

        with open(dst0, "rb") as f:
            self.assertNotEqual(f.read(), b"old-bytes")  # 覆盖：已重写

    def test_prescan_ask_honors_per_file_choice(self):
        dst0 = os.path.join(self.out, "data0.xlsx")
        dst1 = os.path.join(self.out, "data1.xlsx")
        os.makedirs(self.out)
        for dst in (dst0, dst1):
            with open(dst, "wb") as f:
                f.write(b"old-bytes")

        with patch.object(self.card, "_ask_overwrite_policy_dialog",
                          return_value="ask"), \
             patch("text_tools.messagebox.askyesno", side_effect=[True, False]), \
             patch("text_tools.messagebox.showinfo"):
            self.card.run()
            _pump(self.root, lambda: self.card.btn_run.cget("text") == "开始转换")

        with open(dst0, "rb") as f:
            self.assertNotEqual(f.read(), b"old-bytes")  # 逐个确认允许覆盖
        with open(dst1, "rb") as f:
            self.assertEqual(f.read(), b"old-bytes")     # 未允许 → 跳过

    def test_dialog_cancel_aborts_before_start(self):
        dst0 = os.path.join(self.out, "data0.xlsx")
        os.makedirs(self.out)
        with open(dst0, "wb") as f:
            f.write(b"old-bytes")

        with patch.object(self.card, "_ask_overwrite_policy_dialog",
                          return_value=None), \
             patch("text_tools.messagebox.showinfo") as showinfo:
            self.card.run()
            _pump(self.root, lambda: True)

        self.assertIn("已取消：未开始转换", self.card.log_text.get("1.0", "end"))
        showinfo.assert_not_called()  # 未启动转换，无完成弹窗
        self.assertFalse(hasattr(self.card, "_run_thread"))
        with open(dst0, "rb") as f:
            self.assertEqual(f.read(), b"old-bytes")

    def test_second_click_cancels_midway(self):
        started = threading.Event()
        release = threading.Event()
        real_convert = text_tools.convert_csv_to_sheet

        def slow(ws, path, **kwargs):
            started.set()
            release.wait(5)
            return real_convert(ws, path, **kwargs)

        with patch("text_tools.convert_csv_to_sheet", side_effect=slow), \
             patch("text_tools.messagebox.showinfo"), \
             patch("text_tools.messagebox.showwarning") as warn:
            self.card.run()
            _pump(self.root, started.is_set, settle=5)
            self.assertEqual(self.card.btn_run.cget("text"), "⨂ 取消转换")

            self.card.run()  # 第二次点击 = 请求取消
            self.assertTrue(self.card._cancel_event.is_set())
            release.set()
            _pump(self.root, lambda: self.card.btn_run.cget("text") == "开始转换")

        self.assertTrue(warn.called)
        self.assertEqual(warn.call_args.args[0], "已取消")
        self.assertIn("已取消：完成 1/2", self.card.log_text.get("1.0", "end"))
        self.assertFalse(os.path.isfile(os.path.join(self.out, "data1.xlsx")))


class TextToolsWorkerRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tk, cls.root = shared_tk_root()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.card = text_tools.TextToolsCard(self.root)
        self.files = []
        for i in range(2):
            p = os.path.join(self.tmp.name, f"note{i}.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("甲乙丙\n丁戊己\n")
            self.files.append(p)
        self.card.files = list(self.files)
        self.out = os.path.join(self.tmp.name, "out")
        self.card.out_dir = self.out
        self.card.op_var.set("replace")
        self.card.replace_old.set("甲")
        self.card.replace_new.set("X")

    def test_replace_runs_on_worker_with_params_snapshot(self):
        """双向断言：处理在 worker 线程执行且使用主线程快照的参数。"""
        seen = {}
        real_replace = text_tools.replace_keyword

        def spy(src, dst, old, new, **kwargs):
            seen["thread"] = threading.current_thread()
            seen["old"], seen["new"] = old, new
            return real_replace(src, dst, old, new, **kwargs)

        with patch("text_tools.replace_keyword", side_effect=spy), \
             patch("text_tools.messagebox.showinfo"):
            self.card.run()
            _pump(self.root, lambda: self.card.btn_run.cget("text") == "开始处理")

        self.assertIsNot(seen["thread"], threading.main_thread())
        self.assertEqual((seen["old"], seen["new"]), ("甲", "X"))
        out0 = os.path.join(self.out, "note0_替换.txt")
        with open(out0, "r", encoding="utf-8-sig") as f:
            self.assertIn("X乙丙", f.read())
        self.assertIn("处理完成", self.card.log_text.get("1.0", "end"))

    def test_second_click_cancels_midway(self):
        started = threading.Event()
        release = threading.Event()
        real_replace = text_tools.replace_keyword

        def slow(src, dst, old, new, **kwargs):
            started.set()
            release.wait(5)
            return real_replace(src, dst, old, new, **kwargs)

        with patch("text_tools.replace_keyword", side_effect=slow), \
             patch("text_tools.messagebox.showinfo"), \
             patch("text_tools.messagebox.showwarning") as warn:
            self.card.run()
            _pump(self.root, started.is_set, settle=5)
            self.assertEqual(self.card.btn_run.cget("text"), "⨂ 取消处理")

            self.card.run()
            self.assertTrue(self.card._cancel_event.is_set())
            release.set()
            _pump(self.root, lambda: self.card.btn_run.cget("text") == "开始处理")

        self.assertTrue(warn.called)
        self.assertEqual(warn.call_args.args[0], "已取消")
        self.assertIn("已取消：完成 1/2", self.card.log_text.get("1.0", "end"))


# ── 审计收尾回归：worker 零 Tk 读取 / 缺省 skip / 接入层键名往返 / TxtMergeCard worker ──

class _FakeVar:
    def __init__(self, value=""):
        self._v = value

    def get(self):
        return self._v

    def set(self, value):
        self._v = value


class _FakeCard:
    """最小替身：只带被测代码读写的属性。"""


class WorkerTkIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tk, cls.root = shared_tk_root()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name, content):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def test_process_one_never_touches_tk_when_params_given(self):
        """params 提供时 worker 零 Tk 读取（基线 dict.get 默认值急切求值 → 失败）。"""
        card = object.__new__(text_tools.TextToolsCard)
        card.log = lambda _m: None
        # 故意不挂任何 Tk 变量
        params = dict(
            split_lines="1000", filter_kw="", filter_mode="keep",
            replace_old="甲", replace_new="X", affix_pre="", affix_suf="",
            case_mode="upper", enc_target="utf-8",
        )
        src = self._write("in.txt", "甲乙丙\n")
        out_dir = self.tmp.name
        card._process_one(src, out_dir, "replace", "utf-8-sig", params)
        with open(os.path.join(out_dir, "in_替换.txt"), "r", encoding="utf-8-sig") as f:
            self.assertIn("X乙丙", f.read())

    def test_process_one_partial_params_uses_empty_defaults_without_tk(self):
        """部分 params（缺 op 需要的键）：以空串兜底给出业务提示，不 KeyError、不回头读 Tk。"""
        card = object.__new__(text_tools.TextToolsCard)
        card.log = lambda _m: None
        src = self._write("in2.txt", "甲乙丙\n")
        with self.assertRaises(ValueError) as ctx:
            card._process_one(src, self.tmp.name, "replace", "utf-8-sig",
                              {"filter_kw": "与 replace 无关的键"})
        self.assertIn("查找", str(ctx.exception))

    def test_build_merged_single_sheet_progress_uses_snapshot_total(self):
        """审计复核 P3：进度上限取传入快照长度，而非实时 self.csv_files。"""
        card = object.__new__(text_tools.CsvConvertCard)
        card.log = lambda _m: None
        card._cancel_event = threading.Event()
        card._batch_failed = 0
        card._confirm_overwrite = lambda _dst: True
        srcs = []
        for i in range(2):
            p = os.path.join(self.tmp.name, f"d{i}.csv")
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write("名称,数值\n甲,1\n")
            srcs.append(p)
        card.csv_files = ["不存在的实时列表项.csv"]  # 与快照故意不同，用于暴露漏改

        seen = []
        card._build_merged_single_sheet(
            self.tmp.name, lambda cur, total: seen.append((cur, total)), srcs)
        self.assertEqual(seen, [(1, 2), (2, 2)])

    def test_confirm_overwrite_defaults_to_skip_without_prescan(self):
        """绕过预扫描的异常路径缺省 skip：宁可少写盘，不静默覆盖（审计 P2）。"""
        card = object.__new__(text_tools.CsvConvertCard)
        with patch.object(text_tools.os.path, "exists", return_value=True):
            self.assertFalse(card._confirm_overwrite("x.xlsx"))

    def test_app_ui_state_collect_apply_roundtrip(self):
        """接入层键名往返：_collect 的键必须被 _apply 认领（审计 P3：键名写错不可发现）。"""
        from types import SimpleNamespace

        app = object.__new__(text_tools.App)
        app.root = SimpleNamespace(winfo_geometry=lambda: "680x600+6+28")
        app.txt_card = _FakeCard()
        app.csv_card = _FakeCard()
        app.tools_card = _FakeCard()

        app.txt_card.join_var = _FakeVar("newline")
        app.txt_card.header_var = _FakeVar(True)
        app.txt_card.sep_var = _FakeVar("==")
        app.txt_card.empty_var = _FakeVar(True)
        app.txt_card.enc_var = _FakeVar("gbk")
        app.csv_card.conv_var = _FakeVar("txt")
        app.csv_card.merge_var = _FakeVar("one")
        app.csv_card.merge_single_header = _FakeVar(False)
        app.csv_card.txt_enc_var = _FakeVar("gbk")
        app.csv_card.out_dir = r"C:\out\csv"
        app.csv_card.var_out = _FakeVar(r"C:\out\csv")
        app.tools_card.op_var = _FakeVar("replace")
        app.tools_card.out_enc = _FakeVar("utf-8")
        app.tools_card.split_var = _FakeVar("20")
        app.tools_card.filter_kw = _FakeVar("kw")
        app.tools_card.filter_mode = _FakeVar("drop")
        app.tools_card.replace_old = _FakeVar("old")
        app.tools_card.replace_new = _FakeVar("new")
        app.tools_card.affix_pre = _FakeVar("[")
        app.tools_card.affix_suf = _FakeVar("]")
        app.tools_card.case_mode = _FakeVar("lower")
        app.tools_card.enc_target = _FakeVar("big5")
        app.tools_card.out_dir = r"C:\out	xt"
        app.tools_card.var_out = _FakeVar(r"C:\out	xt")

        state = app._collect_ui_state()
        self.assertEqual(state["geometry"], "680x600+6+28")

        # 全新替身（全部默认值），应用同一份 state 后应与收集时一致
        fresh_csv = _FakeCard()
        fresh_csv.var_out = _FakeVar("")
        fresh_tools = _FakeCard()
        fresh_tools.var_out = _FakeVar("")
        app2 = object.__new__(text_tools.App)
        app2.root = app.root
        app2.txt_card = _FakeCard()
        app2.txt_card.join_var = _FakeVar()
        app2.txt_card.header_var = _FakeVar()
        app2.txt_card.sep_var = _FakeVar()
        app2.txt_card.empty_var = _FakeVar()
        app2.txt_card.enc_var = _FakeVar()
        app2.csv_card = fresh_csv
        app2.csv_card.conv_var = _FakeVar()
        app2.csv_card.merge_var = _FakeVar()
        app2.csv_card.merge_single_header = _FakeVar()
        app2.csv_card.txt_enc_var = _FakeVar()
        app2.tools_card = fresh_tools
        app2.tools_card.op_var = _FakeVar()
        app2.tools_card.out_enc = _FakeVar()
        app2.tools_card.split_var = _FakeVar()
        app2.tools_card.filter_kw = _FakeVar()
        app2.tools_card.filter_mode = _FakeVar()
        app2.tools_card.replace_old = _FakeVar()
        app2.tools_card.replace_new = _FakeVar()
        app2.tools_card.affix_pre = _FakeVar()
        app2.tools_card.affix_suf = _FakeVar()
        app2.tools_card.case_mode = _FakeVar()
        app2.tools_card.enc_target = _FakeVar()

        app2._apply_ui_state(state)

        self.assertEqual(app2.txt_card.join_var.get(), "newline")
        self.assertEqual(app2.txt_card.header_var.get(), True)
        self.assertEqual(app2.csv_card.conv_var.get(), "txt")
        self.assertEqual(app2.csv_card.merge_var.get(), "one")
        self.assertEqual(app2.csv_card.merge_single_header.get(), False)
        self.assertEqual(app2.csv_card.out_dir, r"C:\out\csv")
        self.assertEqual(app2.tools_card.op_var.get(), "replace")
        self.assertEqual(app2.tools_card.filter_mode.get(), "drop")
        self.assertEqual(app2.tools_card.affix_pre.get(), "[")
        self.assertEqual(app2.tools_card.out_dir, r"C:\out	xt")


class TxtMergeCardWorkerTests(unittest.TestCase):
    """TxtMergeCard.merge（text_tools 侧卡片）同步实现线程化后的行为。"""

    @classmethod
    def setUpClass(cls):
        cls.tk, cls.root = shared_tk_root()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.card = text_tools.TxtMergeCard(self.root)
        self.files = []
        for i in range(2):
            p = os.path.join(self.tmp.name, f"t{i}.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("甲乙丙\n")
            self.files.append(p)
        self.card.files = list(self.files)
        self.out = os.path.join(self.tmp.name, "merged.txt")

    def test_merge_runs_on_worker_thread(self):
        """双向断言：基线同步实现时失败。"""
        seen = {}
        real_merge = text_tools.merge_txt_files

        def spy(paths, out, **kwargs):
            seen["thread"] = threading.current_thread()
            return real_merge(paths, out, **kwargs)

        with patch("text_tools.filedialog.asksaveasfilename", return_value=self.out), \
             patch("text_tools.messagebox.showinfo"), \
             patch("text_tools.merge_txt_files", side_effect=spy):
            self.card.merge()
            deadline = time.time() + 5
            while "thread" not in seen and time.time() < deadline:
                self.root.update()
                time.sleep(0.01)
            for _ in range(30):
                self.root.update()
                time.sleep(0.01)

        self.assertIsNot(seen["thread"], threading.main_thread())
        self.assertTrue(os.path.isfile(self.out))
        self.assertEqual(self.card.btn_merge.cget("text"), "合并为单个 txt")
        self.assertIn("合并完成", self.card.status.cget("text"))

    def test_merge_passes_progress_callback_to_core(self):
        """审计复核 P3：卡片把 progress_callback 透传核心，否则没有 N/M 进度。"""
        seen = {}
        real_merge = text_tools.merge_txt_files

        def spy(paths, out, progress_callback=None, **kwargs):
            seen["has_progress"] = progress_callback is not None
            if progress_callback is not None:
                progress_callback(1, len(paths))  # 不得抛异常
            return real_merge(paths, out, **kwargs)

        with patch("text_tools.filedialog.asksaveasfilename", return_value=self.out), \
             patch("text_tools.messagebox.showinfo"), \
             patch("text_tools.merge_txt_files", side_effect=spy):
            self.card.merge()
            deadline = time.time() + 5
            while "has_progress" not in seen and time.time() < deadline:
                self.root.update()
                time.sleep(0.01)
            for _ in range(30):
                self.root.update()
                time.sleep(0.01)

        self.assertTrue(seen.get("has_progress"))
        self.assertIn("合并完成", self.card.status.cget("text"))


if __name__ == "__main__":
    unittest.main()
