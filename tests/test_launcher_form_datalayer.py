# -*- coding: utf-8 -*-
"""WT_Launcher 表单四接口数据层录入按钮（元素/绘图/CFD/综合）的单测。

覆盖总控台侧的编排责任，不重复接口自身的计划/校验单测：
  · 副本库白名单守卫（生产库与任意非 *_verify/_test/_sandbox 一律拒，且发生在 run() 之前）；
  · 工作目录缺失时提示并中止；
  · 每个接口各自独立的防重入锁；
  · 成功路径调用 run() 的位置/关键字参数正确（含 apply=True 与独占 out_dir）；
  · 绘图非整数跨度在 GUI 侧就用接口自己的解释者挡住（不会走到 run()）；
  · CFD 表单字段进 overrides 的键名口径，且迭代/核数非整数在 GUI 就弹回；
  · 综合非 DEMO 站点需要额外确认；
  · 结果点属推断判别值：不放行就不写，放行才映射 allow_unverified_result；
  · 按钮接线：每个板块 key 的 command 逐一比对金样，并自证「接错方法会让这条变红」；
  · 写后校验失败时，嵌在 data["verify"] 里的 checks/verify_message 要渲染到界面上。
"""
import os
import re
import contextlib
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import WT_Launcher

FORM_METHODS = (
    ("_simple_element_data_layer", "_element_import_running"),
    ("_simple_mapping_data_layer", "_mapping_import_running"),
    ("_simple_cfd_data_layer", "_cfd_import_running"),
    ("_simple_comprehensive_data_layer", "_synthesis_import_running"),
)


class _SyncThread:
    """替身线程：立刻同步执行，便于断言 run() 调用与收尾汇报。"""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)

    def join(self, timeout=None):
        pass


def _askstring_for(ordered, default=""):
    """按提示语子串匹配的 simpledialog.askstring 替身。

    匹配顺序 = ordered 顺序，与弹窗先后无关：改提示词顺序不会假通过。
    """
    def _inner(title, prompt, initialvalue=None, parent=None, **kwargs):
        for needle, value in ordered:
            if needle in prompt:
                return value
        return default
    return _inner


def _ok_result():
    import mup_weather_import as mw
    return mw.ImportResult(True, mw.ImportCode.OK, "写入并校验通过",
                           data={"verify": {"counts": {"Elements": 1}}, "out_dir": "T:/out"})


# ── 按钮接线抽取（把关 P1-1）───────────────────────────────────────────────
#
# 接线是本轮的交付物本身，而「两个字符串都出现在源码里」抓不住最常见的错误：把 CFD
# 按钮接到气象方法（其余四组方法级测试直接调用方法、绕过按钮，也抓不到）。下面的断言
# 比对的是「板块 key → 该板块里 create_flat_button 的 command 方法名」，并配一条负面
# 测试自证它真的会变红。element 板块并列挂元素与绘图两个按钮（绘图属元素类别但走独立
# 接口），所以那一个 key 对应两条 command。
BUTTON_COMMANDS = (
    ("element", ["_simple_element_data_layer", "_simple_mapping_data_layer"]),
    ("cfd", ["_simple_cfd_data_layer"]),
    ("comprehensive", ["_simple_comprehensive_data_layer"]),
    ("weather", ["_simple_weather_data_layer"]),
    ("turbine", ["_simple_turbine_data_layer"]),
    ("terrain", ["_simple_terrain_data_layer"]),
)

_KEY_BLOCK_RE = re.compile(r'^(\s*)if key == "(\w+)":\s*$')
_COMMAND_RE = re.compile(r"command=self\.(\w+)")


def _datalayer_button_blocks(src):
    """抽出数据层按钮板块：[{key, start, end, commands}]，按源码顺序。

    板块 = `if key == "x":` 行 + 其后直到第一个回到同缩进（或更浅）的非空行。只保留块内
    同时出现 create_flat_button 与「数据层录入」的板块：源码里另有两处 `if key == "cfd":`
    属于别的逻辑，用这个条件天然排除，不必把断言绑到某个函数名上。
    """
    lines = src.splitlines(keepends=True)
    offsets = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)
    offsets.append(position)                    # 哨兵：最后一行的结束偏移
    blocks = []
    index = 0
    while index < len(lines):
        match = _KEY_BLOCK_RE.match(lines[index].rstrip("\r\n"))
        if not match:
            index += 1
            continue
        indent, key = match.group(1), match.group(2)
        cursor = index + 1
        while cursor < len(lines):
            if lines[cursor].strip() and not lines[cursor].startswith(indent + " "):
                break
            cursor += 1
        text = src[offsets[index]:offsets[cursor]]
        if "create_flat_button" in text and "数据层录入" in text:
            blocks.append({"key": key, "start": offsets[index], "end": offsets[cursor],
                           "commands": _COMMAND_RE.findall(text)})
        index = cursor                          # 从板块末尾继续，天然跳过重复 key
    return blocks


def _datalayer_button_bindings(src):
    """{板块 key: [command 方法名, ...]}；同一 key 有多个按钮块时按出现顺序拼接。"""
    bindings = {}
    for block in _datalayer_button_blocks(src):
        bindings.setdefault(block["key"], []).extend(block["commands"])
    return bindings


def _rebind_first_button(src, key, method):
    """把 key 板块里第一条 `command=self.xxx` 换成 method（只在内存字符串上改，不落盘）。

    负面测试用它证明接线断言可证伪。找不到板块或找不到 command 就直接失败：
    一声不响地返回原源码会让负面测试变成永远不会红的空转。
    """
    for block in _datalayer_button_blocks(src):
        if block["key"] != key:
            continue
        match = _COMMAND_RE.search(src, block["start"], block["end"])
        if match:
            return src[:match.start()] + "command=self." + method + src[match.end():]
    raise AssertionError("没找到 key={} 的数据层按钮 command".format(key))


class LauncherFormHarness(unittest.TestCase):

    def setUp(self):
        self.work_dir = tempfile.mkdtemp(prefix="wt_launcher_form_")
        self._dialog_stack = []
        self._dialog_stack_started = False
        coord_dir = os.path.join(self.work_dir, "03-WT输入", "01-测风塔及机位点坐标")
        os.makedirs(coord_dir)
        self._write(os.path.join(coord_dir, "CFT信息.txt"),
                    "C1831 120.5 40.5 99 125 43555698 5145316\n")
        self._write(os.path.join(coord_dir, "CFT_Project_CGCS2000 43.txt"),
                    "C1831 43555698.0 5145316.0 99.0\n")
        self._write(os.path.join(coord_dir, "JWD_Project_CGCS2000 43.txt"),
                    "JWD1 43555800.0 5145400.0 125.0\nJWD2 43555900.0 5145500.0 125.0\n")
        self.app = self._make_app()

    def tearDown(self):
        shutil.rmtree(self.work_dir, ignore_errors=True)

    @staticmethod
    def _write(path, text):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _make_app(self, work_dir=None):
        app = object.__new__(WT_Launcher.LauncherApp)
        app.root = MagicMock()
        # root.after 同步执行回调：让 _datalayer_finish 的汇报路径也被覆盖
        app.root.after = lambda delay, fn, *args: fn(*args)
        app._append_log = MagicMock()
        app.project_work_dir = self.work_dir if work_dir is None else work_dir
        app.project_params = {"hubHeight": "125"}
        return app

    def _note(self, kind):
        def _inner(title, message, parent=None, **kwargs):
            self._dialog_stack[-1][0][kind].append((title, message))
            return None
        return _inner

    def _ask(self, title, prompt, initialvalue=None, parent=None, **kwargs):
        _recorder, answers, _confirm = self._dialog_stack[-1]
        return _askstring_for(answers)(title, prompt, initialvalue=initialvalue,
                                       parent=parent, **kwargs)

    def _ask_yes(self, title, message, parent=None, **kwargs):
        recorder, _answers, confirm = self._dialog_stack[-1]
        recorder["askyesno"].append((title, message))
        return confirm(message) if callable(confirm) else confirm

    def _dialogs(self, answers, confirm=True):
        """装好 askstring / messagebox / 同步线程 替身，返回本次调用的弹窗记录。

        confirm 可为 bool，或按确认框文案返回 bool 的可调用对象。

        同一个用例里可以多次调用（守卫类循环）：替身只装一次，每次压入一层
        「记录 + 匹配表」，事件路由到栈顶，避免 patch 叠 patch 时前一层收不到事件。
        """
        recorder = {"showerror": [], "showinfo": [], "askyesno": []}
        self._dialog_stack.append((recorder, answers, confirm))
        if not self._dialog_stack_started:
            patchers = [
                patch.object(WT_Launcher.simpledialog, "askstring", side_effect=self._ask),
                patch.object(WT_Launcher.messagebox, "showerror",
                             side_effect=self._note("showerror")),
                patch.object(WT_Launcher.messagebox, "showinfo",
                             side_effect=self._note("showinfo")),
                patch.object(WT_Launcher.messagebox, "askyesno", side_effect=self._ask_yes),
                patch.object(threading, "Thread", _SyncThread),
            ]
            for patcher in patchers:
                patcher.start()
            self.addCleanup(lambda: [p.stop() for p in reversed(patchers)])
            self._dialog_stack_started = True
        return recorder

    @contextlib.contextmanager
    def _patch_runs(self):
        """把四个接口的 run() 一起换成替身（守卫类用例用它，避免误触 sqlcmd）。"""
        import mup_cfd_import
        import mup_element_import
        import mup_mapping_import
        import mup_synthesis_import
        with contextlib.ExitStack() as stack:
            mocks = {}
            for name, module in (("element", mup_element_import),
                                 ("mapping", mup_mapping_import),
                                 ("cfd", mup_cfd_import),
                                 ("synthesis", mup_synthesis_import)):
                mocks[name] = stack.enter_context(patch.object(module, "run"))
            yield mocks


class TestDatalayerGuards(LauncherFormHarness):
    """四个入口共用的红线：库名守卫、工作目录、防重入、失败汇报。"""

    def test_production_db_always_rejected(self):
        for method, lock in FORM_METHODS:
            with self.subTest(method=method):
                app = self._make_app()
                recorder = self._dialogs([("目标数据库", "MUPLocalDb")])
                with self._patch_runs() as mocks:
                    getattr(app, method)()
                self.assertFalse(any(m.called for m in mocks.values()),
                                 "生产库名必须在 run() 之前就被拒")
                self.assertTrue(recorder["showerror"])
                self.assertIn("只允许副本库", recorder["showerror"][0][1])
                self.assertFalse(getattr(app, lock, False), "拒绝后不应留锁")

    def test_non_whitelist_db_rejected(self):
        for method, lock in FORM_METHODS:
            with self.subTest(method=method):
                app = self._make_app()
                recorder = self._dialogs([("目标数据库", "MUPLocalDb_site")])
                with self._patch_runs() as mocks:
                    getattr(app, method)()
                self.assertFalse(any(m.called for m in mocks.values()))
                self.assertIn("只允许副本库", recorder["showerror"][0][1])

    def test_rejection_text_promises_no_prod_switch(self):
        # 把关 P2-1：这段文案是从气象入口搬来的，指向四个接口都没有的 --danger-write-prod；
        # 照做只会拿到 argparse 报错，且与「无生产逃生门」的叙事自相矛盾。
        for method, _lock in FORM_METHODS:
            with self.subTest(method=method):
                app = self._make_app()
                recorder = self._dialogs([("目标数据库", "MUPLocalDb")])
                with self._patch_runs():
                    getattr(app, method)()
                text = recorder["showerror"][0][1]
                self.assertIn("只允许副本库", text)
                self.assertIn("不支持写生产库", text)
                for forbidden in ("danger-write-prod", "danger_write_prod"):
                    self.assertNotIn(forbidden, text)

    def test_guard_suffix_matches_shared_apply_whitelist(self):
        # GUI 守卫与 mup_weather_import.apply_ok（四个接口共用的闸门）必须同口径
        import mup_weather_import as mw
        for db in ("MUPLocalDb_a_verify", "MUPLocalDb_a_test", "MUPLocalDb_a_SANDBOX"):
            self.assertTrue(mw.apply_ok(db))
            self.assertTrue(db.lower().endswith(WT_Launcher.LauncherApp.COPY_DB_SUFFIXES))
        for db in ("MUPLocalDb", "MUPLocalDb_copy", "MUPLocalDb_verifyx"):
            self.assertFalse(mw.apply_ok(db))
            self.assertFalse(db.lower().endswith(WT_Launcher.LauncherApp.COPY_DB_SUFFIXES))

    def test_missing_work_dir_prompts(self):
        for method, lock in FORM_METHODS:
            with self.subTest(method=method):
                app = self._make_app(work_dir="")
                recorder = self._dialogs([("目标数据库", "MUPLocalDb_x_verify")])
                getattr(app, method)()
                self.assertTrue(recorder["showinfo"])
                self.assertIn("请先选择项目工作目录", recorder["showinfo"][0][1])
                self.assertFalse(getattr(app, lock, False))

    def test_reentrancy_lock_is_per_method(self):
        for method, lock in FORM_METHODS:
            with self.subTest(method=method):
                app = self._make_app()
                setattr(app, lock, True)
                recorder = self._dialogs([("目标数据库", "MUPLocalDb_x_verify")])
                getattr(app, method)()
                self.assertTrue(recorder["showinfo"])
                self.assertIn("上一次录入仍在进行", recorder["showinfo"][0][1])
                self.assertFalse(recorder["showerror"])
                self.assertTrue(getattr(app, lock), "忙时不得改锁")

    def test_cancel_at_db_prompt_is_silent(self):
        app = self._make_app()
        recorder = self._dialogs([("目标数据库", "")])
        with self._patch_runs() as mocks:
            app._simple_element_data_layer()
        self.assertFalse(recorder["showerror"])
        self.assertFalse(any(m.called for m in mocks.values()))
        self.assertFalse(getattr(app, "_element_import_running", False))

    def test_failure_report_shows_orchestration_payload(self):
        # §5 编排契约：退出码语义 + rollback/clash/verify 负载直接展示，不靠解析消息文本
        import mup_weather_import as mw
        app = self._make_app()
        res = mw.ImportResult(
            False, mw.ImportCode.VERIFY, "写后校验失败（checks 不一致），已自动回滚",
            data={"verify_message": "corner_east_edge",
                  "rollback": {"executed": True, "ok": True},
                  "clash": [[22, 2]], "out_dir": "T:/x"})
        text = app._datalayer_result_text(res)
        self.assertIn("exit=5", text)
        self.assertIn("已自动回滚", text)
        self.assertIn("corner_east_edge", text)
        self.assertIn("冲突项", text)
        self.assertIn("产物目录", text)

    # ── 把关 P2-4：四个接口的真负载把校验细节嵌在 data["verify"] 里，界面必须渲染出来 ──
    def test_result_text_renders_nested_element_checks(self):
        # element 形态：_verify 失败只给 {"checks": {label: bool}}，顶层没有 verify_message。
        # 过去只读顶层 → 界面写「看校验原因」却不给原因（哪个元素不对）。
        import mup_weather_import as mw
        app = self._make_app()
        res = mw.ImportResult(
            False, mw.ImportCode.VERIFY, "写后校验失败，已自动回滚",
            data={"plan": {}, "out_dir": "T:/x", "rollback": "T:/x/rollback.sql",
                  "verify": {"checks": {"C1831": True, "JWD2": False, "JWD3": False},
                             "rollback": {"executed": True, "ok": True}}})
        text = app._datalayer_result_text(res)
        self.assertIn("校验不一致：JWD2、JWD3", text)
        self.assertNotIn("C1831", text, "通过的项不该混进失败清单")
        # 回滚事实也嵌在 verify 下：顶层 rollback 只是 .sql 路径，两者都要看得见
        self.assertIn("回滚脚本：T:/x/rollback.sql", text)
        self.assertIn("自动回滚：已执行且成功", text)
        # 失败项上百个时不能把弹窗铺满：列前 12 个 + 总数
        many = {"E%02d" % i: i >= 20 for i in range(60)}     # E00..E19 不一致
        res_many = mw.ImportResult(False, mw.ImportCode.VERIFY, "写后校验失败，已自动回滚",
                                   data={"verify": {"checks": many}})
        line = [item for item in app._datalayer_result_text(res_many).splitlines()
                if item.startswith("校验不一致：")][0]
        self.assertEqual(12, len(line.split("：", 1)[1].split(" 等")[0].split("、")))
        self.assertIn("等 20 项", line)

    def test_result_text_renders_nested_verify_message(self):
        # mapping/cfd/synthesis 形态：verify_message 与 checks 同在 data["verify"] 下。
        import mup_weather_import as mw
        app = self._make_app()
        res = mw.ImportResult(
            False, mw.ImportCode.VERIFY, "写后校验失败（写后校验不一致），已自动回滚",
            data={"out_dir": "T:/y", "rollback": "T:/y/rollback.sql",
                  "verify": {"checks": {"corner_east_edge": False},
                             "verify_message": "写后校验不一致",
                             "rollback": {"executed": True, "ok": False}}})
        text = app._datalayer_result_text(res)
        self.assertIn("校验原因：写后校验不一致", text)
        self.assertIn("corner_east_edge", text)
        self.assertIn("自动回滚：失败（需人工介入）", text)

    def test_result_text_quiet_when_nested_checks_all_pass(self):
        # 成功负载同样是嵌套形态：全 True 的 checks 不能渲染成「校验不一致：」
        import mup_weather_import as mw
        app = self._make_app()
        res = mw.ImportResult(True, mw.ImportCode.OK, "元素写入并校验通过（2 个）",
                              data={"out_dir": "T:/z",
                                    "verify": {"checks": {"C1831": True, "JWD1": True},
                                               "element_count": 2}})
        text = app._datalayer_result_text(res)
        self.assertNotIn("校验不一致", text)
        self.assertIn("写后校验 element_count=2", text)

    def test_precondition_exit_explains_next_step(self):
        import mup_weather_import as mw
        app = self._make_app()
        res = mw.ImportResult(False, mw.ImportCode.PRECONDITION,
                              "站点 DEMO 无绘图 Mapping 元素")
        self.assertIn("exit=7", app._datalayer_result_text(res))

    def test_conflict_exit_explains_next_step(self):
        import mup_weather_import as mw
        app = self._make_app()
        res = mw.ImportResult(False, mw.ImportCode.CONFLICT, "同名已存在")
        self.assertIn("exit=6", app._datalayer_result_text(res))

    def test_worker_crash_reported_and_lock_released(self):
        app = self._make_app()
        recorder = self._dialogs([
            ("目标数据库", "MUPLocalDb_cfd_verify"), ("站点名", "DEMO"),
            ("工况名", ""), ("风向列表", "10"), ("热稳定度", ""),
            ("最大迭代次数", ""), ("每方向核数", ""), ("收敛阈值", "")])
        import mup_cfd_import
        with patch.object(mup_cfd_import, "run", side_effect=RuntimeError("boom")):
            app._simple_cfd_data_layer()
        self.assertTrue(recorder["showerror"])
        self.assertIn("boom", recorder["showerror"][0][1])
        self.assertFalse(app._cfd_import_running)


class TestElementDataLayer(LauncherFormHarness):

    ANSWERS = [
        ("目标数据库", "MUPLocalDb_element_verify"),
        ("站点名", "test"),
        ("计算案例名", "WT"),
        ("挂载配置名", "CaseElementConfiguration"),
        ("追加元素", ""),
    ]

    def test_success_calls_run_with_parsed_specs(self):
        import mup_element_import
        app = self._make_app()
        self._dialogs(self.ANSWERS)
        with patch("mup_element_import.run", return_value=_ok_result()) as run_mock:
            app._simple_element_data_layer()
        self.assertTrue(run_mock.called)
        args, kwargs = run_mock.call_args
        specs = args[0]
        self.assertEqual(args[1], "MUPLocalDb_element_verify")
        self.assertEqual(kwargs["site"], "test")
        self.assertEqual(kwargs["case"], "WT")
        self.assertEqual(kwargs["config_name"], "CaseElementConfiguration")
        self.assertTrue(kwargs["apply"])
        self.assertIsInstance(kwargs["out_dir"], Path)
        self.assertTrue(str(kwargs["out_dir"]).startswith(
            os.path.join(self.work_dir, "04-WT输出", "element_import")))
        self.assertFalse(kwargs["allow_unverified_result"])
        self.assertEqual({s.kind for s in specs}, {"mast", "turbine"})
        mast = [s for s in specs if s.kind == "mast"][0]
        self.assertEqual((mast.label, mast.x, mast.y, mast.z),
                         ("C1831", 43555698.0, 5145316.0, 99.0))
        self.assertEqual(len([s for s in specs if s.kind == "turbine"]), 2)
        self.assertTrue(all(isinstance(s, mup_element_import.ElementSpec) for s in specs))

    def test_result_point_needs_explicit_approval(self):
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[-1] = ("追加元素", "result,P1,43555698.0,5145316.0,99.0")
        recorder = self._dialogs(answers, confirm=False)
        with patch("mup_element_import.run") as run_mock:
            app._simple_element_data_layer()
        self.assertFalse(run_mock.called)
        self.assertTrue(any("MTDElementPoint" in msg for _, msg in recorder["askyesno"]))

    def test_approved_result_point_sets_allow_flag(self):
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[-1] = ("追加元素", "result,P1,43555698.0,5145316.0,99.0")
        self._dialogs(answers, confirm=True)
        with patch("mup_element_import.run", return_value=_ok_result()) as run_mock:
            app._simple_element_data_layer()
        args, kwargs = run_mock.call_args
        self.assertTrue(kwargs["allow_unverified_result"])
        self.assertIn("result", {s.kind for s in args[0]})

    def test_bad_extra_line_rejected_before_run(self):
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[-1] = ("追加元素", "mast,M9,1,2")
        recorder = self._dialogs(answers)
        with patch("mup_element_import.run") as run_mock:
            app._simple_element_data_layer()
        self.assertFalse(run_mock.called)
        self.assertTrue(recorder["showerror"])

    def test_multiple_segments_separated_by_semicolon(self):
        # 本机 askstring 是单行 Entry：多条元素靠分号分隔，全角分号也要认
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[-1] = ("追加元素",
                       "result,P1,43555698.0,5145316.0,99.0；result,P2,43555798.0,5145416.0,99.0")
        self._dialogs(answers, confirm=True)
        with patch("mup_element_import.run", return_value=_ok_result()) as run_mock:
            app._simple_element_data_layer()
        specs = run_mock.call_args[0][0]
        self.assertEqual([s.label for s in specs if s.kind == "result"], ["P1", "P2"])
        self.assertTrue(run_mock.call_args[1]["allow_unverified_result"])

    def test_no_points_prompts(self):
        empty = tempfile.mkdtemp(prefix="wt_launcher_form_empty_")
        self.addCleanup(lambda: shutil.rmtree(empty, ignore_errors=True))
        app = self._make_app(work_dir=empty)
        recorder = self._dialogs(self.ANSWERS)
        with patch("mup_element_import.run") as run_mock:
            app._simple_element_data_layer()
        self.assertFalse(run_mock.called)
        self.assertTrue(recorder["showinfo"])


class TestMappingDataLayer(LauncherFormHarness):

    def _answers(self, bounds):
        return [
            ("目标数据库", "MUPLocalDb_mapping_verify"),
            ("站点名", "test"),
            ("XMin", bounds),
            ("高度", ""),
            ("步长", ""),
            ("绘图名称", ""),
        ]

    def test_integer_span_calls_run(self):
        import mup_mapping_import
        app = self._make_app()
        self._dialogs(self._answers("43555000,43556000,5145000,5146000"))
        with patch("mup_mapping_import.run", return_value=_ok_result()) as run_mock:
            app._simple_mapping_data_layer()
        args, kwargs = run_mock.call_args
        spec = args[0]
        self.assertIsInstance(spec, mup_mapping_import.MappingSpec)
        self.assertEqual(args[1], "MUPLocalDb_mapping_verify")
        self.assertTrue(kwargs["apply"])
        self.assertTrue(kwargs["link_configuration"])
        self.assertIsInstance(kwargs["out_dir"], Path)
        self.assertEqual((spec.nw_x, spec.nw_y, spec.se_x, spec.se_y),
                         (43555000.0, 5146000.0, 43556000.0, 5145000.0))
        self.assertEqual(spec.height, WT_Launcher.LauncherApp.MAPPING_DEFAULT_HEIGHT)
        self.assertEqual(spec.step, WT_Launcher.LauncherApp.MAPPING_DEFAULT_STEP)

    def test_declining_link_still_writes_element(self):
        app = self._make_app()
        # 第一个确认框（是否挂配置）答否，最终确认答是
        recorder = self._dialogs(
            self._answers("43555000,43556000,5145000,5146000"),
            confirm=lambda msg: "Configuration_Element" not in msg)
        with patch("mup_mapping_import.run", return_value=_ok_result()) as run_mock:
            app._simple_mapping_data_layer()
        self.assertTrue(run_mock.called)
        self.assertFalse(run_mock.call_args[1]["link_configuration"])

    def test_cancel_at_final_confirm_does_not_run(self):
        app = self._make_app()
        self._dialogs(self._answers("43555000,43556000,5145000,5146000"), confirm=False)
        with patch("mup_mapping_import.run") as run_mock:
            app._simple_mapping_data_layer()
        self.assertFalse(run_mock.called)

    def test_non_integer_span_blocked_at_gui(self):
        # G-1：非整数跨度现在在计划期硬拒（exit 2）；GUI 先用接口自己的解释者挡住，不写库
        app = self._make_app()
        recorder = self._dialogs(self._answers("43555000,43555950,5145000,5146000"))
        with patch("mup_mapping_import.run") as run_mock:
            app._simple_mapping_data_layer()
        self.assertFalse(run_mock.called)
        self.assertTrue(recorder["showerror"])
        self.assertIn("整数倍", recorder["showerror"][0][1])

    def test_default_bounds_snap_to_step_multiple(self):
        app = self._make_app()
        bounds = app._datalayer_mapping_default_bounds(self.work_dir)
        self.assertIsNotNone(bounds)
        step = WT_Launcher.LauncherApp.MAPPING_DEFAULT_STEP
        x_min, x_max, y_min, y_max = bounds
        for span in (x_max - x_min, y_max - y_min):
            self.assertAlmostEqual(span / step, round(span / step), places=6)

    def test_corner_order_rejected(self):
        app = self._make_app()
        recorder = self._dialogs(self._answers("43556000,43555000,5145000,5146000"))
        with patch("mup_mapping_import.run") as run_mock:
            app._simple_mapping_data_layer()
        self.assertFalse(run_mock.called)
        self.assertTrue(recorder["showerror"])

    def test_non_numeric_bounds_rejected(self):
        app = self._make_app()
        recorder = self._dialogs(self._answers("abc,def,ghi,jkl"))
        with patch("mup_mapping_import.run") as run_mock:
            app._simple_mapping_data_layer()
        self.assertFalse(run_mock.called)
        self.assertTrue(recorder["showerror"])


class TestCfdDataLayer(LauncherFormHarness):

    ANSWERS = [
        ("目标数据库", "MUPLocalDb_cfd_verify"),
        ("站点名", "DEMO"),
        ("工况名", ""),
        ("风向列表", "10,30,50"),
        ("热稳定度", "2"),
        ("最大迭代次数", "100"),
        ("每方向核数", "8"),
        ("收敛阈值", "90"),
    ]

    def test_success_builds_spec_and_runs(self):
        import mup_cfd_import
        app = self._make_app()
        self._dialogs(self.ANSWERS)
        with patch("mup_cfd_import.run", return_value=_ok_result()) as run_mock:
            app._simple_cfd_data_layer()
        args, kwargs = run_mock.call_args
        spec = args[0]
        self.assertIsInstance(spec, mup_cfd_import.CfdSpec)
        self.assertEqual(args[1], "MUPLocalDb_cfd_verify")
        self.assertTrue(kwargs["apply"])
        self.assertIsInstance(kwargs["out_dir"], Path)
        self.assertTrue(str(kwargs["out_dir"]).startswith(
            os.path.join(self.work_dir, "04-WT输出", "cfd_import")))
        self.assertEqual(spec.site, "DEMO")
        self.assertIsNone(spec.case)
        self.assertEqual(spec.directions, [10, 30, 50])
        self.assertEqual(spec.stability, 2)
        self.assertFalse(spec.atlas)          # Atlas 无金样，总控台不开这个口子
        self.assertEqual(spec.overrides, {
            "DAS_NumberIterations": "100",
            "DAS_NumberThreads": "8",
            "DAS_ConvergenceThreshold": "90",
        })

    def test_blank_form_fields_do_not_override(self):
        app = self._make_app()
        self._dialogs([
            ("目标数据库", "MUPLocalDb_cfd_verify"), ("站点名", "DEMO"),
            ("工况名", "WT"), ("风向列表", ""), ("热稳定度", ""),
            ("最大迭代次数", ""), ("每方向核数", ""), ("收敛阈值", "")])
        with patch("mup_cfd_import.run", return_value=_ok_result()) as run_mock:
            app._simple_cfd_data_layer()
        spec = run_mock.call_args[0][0]
        self.assertIsNone(spec.directions)     # 留空 = 接口按气象风频自动取
        self.assertIsNone(spec.stability)
        self.assertEqual(spec.overrides, {})
        self.assertEqual(spec.case, "WT")

    def test_out_of_range_direction_rejected_before_run(self):
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[3] = ("风向列表", "400")
        recorder = self._dialogs(answers)
        with patch("mup_cfd_import.run") as run_mock:
            app._simple_cfd_data_layer()
        self.assertFalse(run_mock.called)
        self.assertTrue(recorder["showerror"])

    def test_bad_convergence_threshold_rejected(self):
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[7] = ("收敛阈值", "150")
        recorder = self._dialogs(answers)
        with patch("mup_cfd_import.run") as run_mock:
            app._simple_cfd_data_layer()
        self.assertFalse(run_mock.called)
        self.assertIn("DAS_ConvergenceThreshold", recorder["showerror"][0][1])

    def test_non_integer_stability_rejected(self):
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[4] = ("热稳定度", "中性")
        recorder = self._dialogs(answers)
        with patch("mup_cfd_import.run") as run_mock:
            app._simple_cfd_data_layer()
        self.assertFalse(run_mock.called)
        self.assertTrue(recorder["showerror"])

    def test_confirm_mentions_no_solver_run(self):
        app = self._make_app()
        recorder = self._dialogs(self.ANSWERS)
        with patch("mup_cfd_import.run", return_value=_ok_result()):
            app._simple_cfd_data_layer()
        self.assertTrue(any("绝不提交 HPC" in msg for _, msg in recorder["askyesno"]))

    # ── 把关 P1-2：迭代次数/核数在快照里按整数解释，非数字不许走下去 ──
    def _cfd_with_number(self, answers_index, needle, value):
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[answers_index] = (needle, value)
        recorder = self._dialogs(answers)
        with patch("mup_cfd_import.run") as run_mock:
            app._simple_cfd_data_layer()
        return app, recorder, run_mock

    def test_non_numeric_iterations_blocked_at_gui(self):
        app, recorder, run_mock = self._cfd_with_number(5, "最大迭代次数", "abc")
        self.assertFalse(run_mock.called, "非数字必须挡在 run() 之前，不进参数快照")
        self.assertTrue(recorder["showerror"])
        self.assertIn("最大迭代次数", recorder["showerror"][0][1])
        self.assertIn("abc", recorder["showerror"][0][1])
        self.assertFalse(getattr(app, "_cfd_import_running", False), "弹回后不应留锁")

    def test_fractional_threads_blocked_at_gui(self):
        app, recorder, run_mock = self._cfd_with_number(6, "每方向核数", "1.5")
        self.assertFalse(run_mock.called)
        self.assertIn("每方向核数", recorder["showerror"][0][1])
        self.assertFalse(getattr(app, "_cfd_import_running", False))

    def test_non_positive_iterations_blocked_at_gui(self):
        # 接口门是「正整数」，GUI 门同口径：0 核 / 0 次迭代不是「沿用默认」，是错的输入
        app, recorder, run_mock = self._cfd_with_number(5, "最大迭代次数", "0")
        self.assertFalse(run_mock.called)
        self.assertIn("不小于 1", recorder["showerror"][0][1])
        self.assertFalse(getattr(app, "_cfd_import_running", False))

    def test_cancel_at_iterations_prompt_aborts_silently(self):
        # 取消 ≠ 留空：留空是「不覆盖快照」（见 test_blank_form_fields_do_not_override），
        # 取消必须中止，且不该弹一个「不是整数」的错误。
        app, recorder, run_mock = self._cfd_with_number(5, "最大迭代次数", None)
        self.assertFalse(run_mock.called)
        self.assertFalse(recorder["showerror"])
        self.assertFalse(getattr(app, "_cfd_import_running", False))

    def test_iterations_written_as_canonical_integer(self):
        # 收口后写进快照的必须是纯十进制整数：接口侧 _positive_int_issues 认的就是这个形态
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[5] = ("最大迭代次数", " 12 ")
        self._dialogs(answers)
        with patch("mup_cfd_import.run", return_value=_ok_result()) as run_mock:
            app._simple_cfd_data_layer()
        spec = run_mock.call_args[0][0]
        self.assertEqual("12", spec.overrides["DAS_NumberIterations"])
        self.assertEqual([], spec.validate())


class TestComprehensiveDataLayer(LauncherFormHarness):

    ANSWERS = [
        ("目标数据库", "MUPLocalDb_synth_verify"),
        ("站点名", "DEMO"),
        ("工况名", ""),
        ("克隆源综合", "综合2"),
        ("风机配置名", "CaseElementConfiguration"),
        ("综合名称", ""),
    ]

    def test_success_builds_spec_and_runs(self):
        import mup_synthesis_import
        app = self._make_app()
        self._dialogs(self.ANSWERS)
        with patch("mup_synthesis_import.run", return_value=_ok_result()) as run_mock:
            app._simple_comprehensive_data_layer()
        args, kwargs = run_mock.call_args
        spec = args[0]
        self.assertIsInstance(spec, mup_synthesis_import.SynthesisSpec)
        self.assertEqual(args[1], "MUPLocalDb_synth_verify")
        self.assertTrue(kwargs["apply"])
        self.assertIsInstance(kwargs["out_dir"], Path)
        self.assertEqual(spec.site, "DEMO")
        self.assertIsNone(spec.case)
        self.assertEqual(spec.clone_from, "综合2")
        self.assertEqual(spec.configuration, "CaseElementConfiguration")
        self.assertFalse(spec.clear_extreme_wind_reference)

    def test_site_defaults_to_demo(self):
        app = self._make_app()
        seen = {}

        def _inner(title, prompt, initialvalue=None, parent=None, **kwargs):
            if "目标数据库" in prompt:
                return "MUPLocalDb_synth_verify"
            if "站点名" in prompt:
                seen["initial"] = initialvalue
                return None
            return ""
        with patch.object(WT_Launcher.simpledialog, "askstring", side_effect=_inner), \
                patch.object(WT_Launcher.messagebox, "showerror"), \
                patch.object(WT_Launcher.messagebox, "showinfo"), \
                patch("mup_synthesis_import.run") as run_mock:
            app._simple_comprehensive_data_layer()
        self.assertEqual(seen["initial"], "DEMO")
        self.assertFalse(run_mock.called)

    def test_non_demo_site_needs_extra_confirm(self):
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[1] = ("站点名", "OTHERSITE")
        recorder = self._dialogs(answers, confirm=False)
        with patch("mup_synthesis_import.run") as run_mock:
            app._simple_comprehensive_data_layer()
        self.assertFalse(run_mock.called)
        self.assertTrue(any("DEMO" in msg for _, msg in recorder["askyesno"]))

    def test_non_demo_site_confirmed_proceeds(self):
        app = self._make_app()
        answers = list(self.ANSWERS)
        answers[1] = ("站点名", "OTHERSITE")
        self._dialogs(answers, confirm=True)
        with patch("mup_synthesis_import.run", return_value=_ok_result()) as run_mock:
            app._simple_comprehensive_data_layer()
        self.assertEqual(run_mock.call_args[0][0].site, "OTHERSITE")

    def test_confirm_mentions_ewr_and_no_replace(self):
        app = self._make_app()
        recorder = self._dialogs(self.ANSWERS)
        with patch("mup_synthesis_import.run", return_value=_ok_result()):
            app._simple_comprehensive_data_layer()
        messages = [msg for _, msg in recorder["askyesno"]]
        self.assertTrue(any("EWR" in msg for msg in messages))
        self.assertTrue(any("--replace" in msg for msg in messages))


class TestLauncherWiring(unittest.TestCase):
    """按钮 → 接口方法接线（把关 P1-1：断言必须能证伪「接错方法」）。"""

    def setUp(self):
        import inspect
        self.src = inspect.getsource(WT_Launcher)
        self.bindings = _datalayer_button_bindings(self.src)

    def _assert_bindings(self, bindings):
        """接线断言本体：每个板块 key 的 command 列表逐一等于金样。

        刻意不用 subTest：subTest 里的失败被记录后不再向外抛，负面测试就没法用它自证；
        板块名已经写进失败消息，不需要 subTest 也要能定位。
        """
        for key, expected in BUTTON_COMMANDS:
            self.assertEqual(list(expected), bindings.get(key),
                             'key "{}" 的数据层按钮 command 接线不符'.format(key))

    def test_buttons_bound_to_their_own_datalayer_method(self):
        # 四个新入口 + 既有三按钮的 key→command 全在金样里：改板块名、漏挂按钮、
        # 接到别的方法，都会让对应那条变红（旧写法只 grep 字符串是否存在，抓不住）。
        self._assert_bindings(self.bindings)

    def test_wiring_assertion_catches_a_wrong_rebind(self):
        # 负面证明：把 CFD 按钮接到气象方法（本轮最可能的接线错误），同一断言必须红。
        self.assertEqual(["_simple_cfd_data_layer"], self.bindings["cfd"],
                         "真源码的 CFD 按钮已不是金样，这条负面测试没有基线")
        mutated = _rebind_first_button(self.src, "cfd", "_simple_weather_data_layer")
        mutated_bindings = _datalayer_button_bindings(mutated)
        # 比对小字典而不是 70 万字符的源码：万一替换没生效，消息里也不能刷出一整份文件
        self.assertNotEqual(mutated_bindings, self.bindings, "替换没生效，负面测试是空的")
        self.assertEqual(["_simple_weather_data_layer"], mutated_bindings["cfd"])
        with self.assertRaises(AssertionError):
            self._assert_bindings(mutated_bindings)

    def test_bound_commands_are_real_methods(self):
        # 接到「不存在的方法」也要红：金样只钉住名字，能否解析由这条兜底。
        self.assertTrue(self.bindings, "一个数据层按钮板块都没抽到，抽取器本身失效了")
        for key, commands in sorted(self.bindings.items()):
            for name in commands:
                self.assertTrue(callable(getattr(WT_Launcher.LauncherApp, name, None)),
                                "{} 板块绑到了不存在的方法 {}".format(key, name))

    def test_button_keys_are_real_sections(self):
        # 板块 key 与 SIMPLE_SECTIONS 对不上时，按钮会静默消失而源码级断言仍全绿（把关 §5.5）
        section_keys = {sec["key"] for sec in WT_Launcher.LauncherApp.SIMPLE_SECTIONS}
        for key, _expected in BUTTON_COMMANDS:
            self.assertIn(key, section_keys, "{} 不在 SIMPLE_SECTIONS 里".format(key))

    def test_button_labels_and_interfaces_imported(self):
        # 元素/绘图两个按钮共用 element 板块，靠文案区分；四个接口都要在总控台可导入。
        self.assertIn('text="⚡ 数据层录入(元素)"', self.src)
        self.assertIn('text="⚡ 数据层录入(绘图)"', self.src)
        for name in ("mup_element_import", "mup_mapping_import",
                     "mup_cfd_import", "mup_synthesis_import"):
            self.assertIn("import {}".format(name), self.src)

    def test_gui_has_no_prod_escape_hatch(self):
        # 这条是名字级弱断言（把关 P2-2）：源码里出现的是连字符 --danger-write-prod，
        # 查下划线变量名恒真，换别名（force / allow_prod）的旁路它抓不到。真正拦住生产库
        # 的是行为级的 test_production_db_always_rejected（四方法逐一断言 run() 未被调用）。
        self.assertNotIn("danger_write_prod", self.src)


class TestSignatureWiring(LauncherFormHarness):
    """GUI 实际传给 run() 的实参必须能绑到接口自己的签名上（签名接线由机器判定）。"""

    CASES = (
        ("_simple_element_data_layer", "mup_element_import", [
            ("目标数据库", "MUPLocalDb_element_verify"), ("站点名", "test"),
            ("计算案例名", "WT"), ("挂载配置名", "CaseElementConfiguration"),
            ("追加元素", "result,P1,43555698.0,5145316.0,99.0")]),
        ("_simple_mapping_data_layer", "mup_mapping_import", [
            ("目标数据库", "MUPLocalDb_mapping_verify"), ("站点名", "test"),
            ("XMin", "43555000,43556000,5145000,5146000"),
            ("高度", "125"), ("步长", "100"), ("绘图名称", "Mapping-test")]),
        ("_simple_cfd_data_layer", "mup_cfd_import", [
            ("目标数据库", "MUPLocalDb_cfd_verify"), ("站点名", "DEMO"),
            ("工况名", "WT"), ("风向列表", "10，30"), ("热稳定度", "2"),
            ("最大迭代次数", "100"), ("每方向核数", "8"), ("收敛阈值", "90")]),
        ("_simple_comprehensive_data_layer", "mup_synthesis_import", [
            ("目标数据库", "MUPLocalDb_synth_verify"), ("站点名", "DEMO"),
            ("工况名", ""), ("克隆源综合", "2"), ("风机配置名", "CaseElementConfiguration"),
            ("综合名称", "")]),
    )

    def test_run_call_binds_to_interface_signature(self):
        import importlib
        import inspect
        for method, module_name, answers in self.CASES:
            with self.subTest(method=method):
                self._dialogs(answers, confirm=True)
                module = importlib.import_module(module_name)
                with patch.object(module, "run", return_value=_ok_result()) as run_mock:
                    getattr(self.app, method)()
                self.assertTrue(run_mock.called, "{} 未调用 run()".format(module_name))
                args, kwargs = run_mock.call_args
                # bind 只校验名称/arity：GUI 不得自创接口签名里没有的关键字
                inspect.signature(module.run).bind(*args, **kwargs)
                self.assertTrue(kwargs["apply"], "确认之后的成功路径必须显式 apply")
                self.assertIsInstance(kwargs["out_dir"], Path)
                # 前两个位置参数固定是 (输入模型, 副本库名)
                self.assertEqual(args[1], answers[0][1])
                self.assertTrue(args[1].lower().endswith(
                    WT_Launcher.LauncherApp.COPY_DB_SUFFIXES))




# ── 真写副本库自证（默认跳过）──────────────────────────────────────────────
#
# 每一轮接入改动都要自证一次「四个入口真的能写进去」，不能只引上一轮的数。
# 打开方式（必须显式给一个合法副本库名，名字不过白名单就永远不会启用）：
#
#   1) 备份生产：BACKUP DATABASE [MUPLocalDb] TO DISK=... WITH COPY_ONLY, INIT;
#   2) 还原副本：RESTORE DATABASE [MUPLocalDb_selfprobe_verify] FROM DISK=...
#                WITH MOVE N'MUPLocalDb' TO ..., MOVE N'MUPLocalDb_log' TO ...;
#   3) $env:WT_MUP_SELFPROBE_DB = "MUPLocalDb_selfprobe_verify"
#   4) python -m pytest tests/test_launcher_form_datalayer.py -k CopyDbRealWrite
#
# 红线不变：只写这份本轮新还原的副本库；四个接口都不碰 vault、不启 MUP GUI、
# 不触发 solver/HPC、不写 CFDOutputs。生产库不在任何写目标里，且副本库里
# CFDOutputs 的行数在 CFD/综合写入前后都必须一致。
_REAL_WRITE_DB = ""
_env_db = os.environ.get("WT_MUP_SELFPROBE_DB", "").strip()
if _env_db.lower().endswith(WT_Launcher.LauncherApp.COPY_DB_SUFFIXES):
    _REAL_WRITE_DB = _env_db


@unittest.skipUnless(_REAL_WRITE_DB,
                     "需要环境变量 WT_MUP_SELFPROBE_DB=<*_verify/_test/_sandbox 副本库>，"
                     "且该库必须是本轮从生产 COPY_ONLY 备份还原出来的新副本")
class TestCopyDbRealWrite(LauncherFormHarness):
    """驱动真实 GUI 方法 + 真实 run(apply=True)，断言副本库里真的多出这些行。"""

    TIMEOUT = 420.0

    def setUp(self):
        super().setUp()
        import mup_weather_import as mw
        rows = mw._query_rows(_REAL_WRITE_DB,
                              "SELECT COUNT(*) FROM dbo.Sites "
                              "WHERE Name IN ('test','DEMO');")
        if not rows or rows[0][0] != "2":
            self.skipTest("副本库里缺 test/DEMO 站点，真写自证的前置条件不成立")
        self.tag = time.strftime("%H%M%S")
        # 坐标文件里的标签换成一轮一个：同一副本库上重跑不会撞同名元素（exit=6）
        coord = os.path.join(self.work_dir, "03-WT输入", "01-测风塔及机位点坐标")
        self._write(os.path.join(coord, "CFT信息.txt"),
                    "CFT%s 120.5 40.5 99 125 43554124 5145316\n" % self.tag)
        self._write(os.path.join(coord, "CFT_Project_CGCS2000 43.txt"),
                    "CFT%s 43554124.0 5145316.0 99.0\n" % self.tag)
        self._write(os.path.join(coord, "JWD_Project_CGCS2000 43.txt"),
                    "JWD%s 43554224.0 5145416.0 125.0\n" % self.tag)
        self.results = []

    # ── 驱动骨架：弹窗替身 + 真线程 + 轮询（绝不换 threading.Thread）──
    def _drive(self, module_name, method, lock, answers):
        import mup_weather_import as mw
        module = __import__(module_name)
        real_run = module.run

        def recording_run(*args, **kwargs):
            res = real_run(*args, **kwargs)
            self.results.append((args, kwargs, res))
            return res

        def _ask(title, prompt, initialvalue=None, parent=None, **kwargs):
            for needle, value in answers:
                if needle in prompt:
                    return value
            return initialvalue if initialvalue is not None else ""

        confirms = []
        patchers = [
            patch.object(WT_Launcher.simpledialog, "askstring", side_effect=_ask),
            patch.object(WT_Launcher.messagebox, "showerror",
                         side_effect=lambda *a, **k: None),
            patch.object(WT_Launcher.messagebox, "showinfo",
                         side_effect=lambda *a, **k: None),
            patch.object(WT_Launcher.messagebox, "askyesno",
                         side_effect=lambda t, m, parent=None, **k: (
                             confirms.append(m) or True)),
            patch.object(module, "run", side_effect=recording_run),
        ]
        for patcher in patchers:
            patcher.start()
        try:
            getattr(self.app, method)()
            deadline = time.time() + self.TIMEOUT
            while time.time() < deadline:
                if self.results and not getattr(self.app, lock, True):
                    break
                time.sleep(0.5)
            else:
                self.fail("%s 没有在 %.0fs 内收尾" % (method, self.TIMEOUT))
        finally:
            for patcher in reversed(patchers):
                patcher.stop()
        self.assertEqual(len(self.results), 1, "run() 应该只被调用一次")
        args, kwargs, res = self.results[0]
        self.assertTrue(kwargs["apply"], "自证必须走真写分支")
        self.assertEqual(args[1], _REAL_WRITE_DB)
        # 确认框必须点名目标副本库并讲明白红线（写的是副本库，不是生产/vault）
        self.assertTrue(any("副本库" in text and _REAL_WRITE_DB in text
                            for text in confirms),
                        "确认框没有说明只写副本库")
        self.assertTrue(res.ok, "exit=%s message=%s" % (res.exit_code(), res.message))
        return res

    @staticmethod
    def _count(db, table, where="1=1"):
        import mup_weather_import as mw
        return int(mw._query_rows(db, "SELECT COUNT(*) FROM dbo.%s WHERE %s;"
                                  % (table, where))[0][0])

    def test_01_element_writes_rows(self):
        before = self._count(_REAL_WRITE_DB, "Elements")
        res = self._drive(
            "mup_element_import", "_simple_element_data_layer",
            "_element_import_running",
            [("目标数据库", _REAL_WRITE_DB), ("站点名", "test"),
             ("计算案例名", "WT"), ("挂载配置名", "CaseElementConfiguration"),
             ("追加元素", "result,RPT%s,43554124,5145316,99" % self.tag)])
        self.assertEqual(self._count(_REAL_WRITE_DB, "Elements"), before + 3)
        self.assertEqual(
            self._count(_REAL_WRITE_DB, "Elements",
                        "Discriminator='MTDElementPoint' AND Label='RPT%s'" % self.tag),
            1, "结果点（推断判别值）应该被放行写入")
        self.assertIn("element_count", (res.data or {}).get("verify", {}))

    def test_02_mapping_writes_quad_geometry(self):
        res = self._drive(
            "mup_mapping_import", "_simple_mapping_data_layer",
            "_mapping_import_running",
            [("目标数据库", _REAL_WRITE_DB), ("站点名", "test"),
             ("XMin", "43553124,43555124,5144316,5146316"),
             ("高度", "125"), ("步长", "100"),
             ("绘图名称", "MAP%s" % self.tag)])
        rows = __import__("mup_weather_import")._query_rows(
            _REAL_WRITE_DB,
            "SELECT Discriminator, Quad_Edge1_NbStep, Quad_Edge1_StepLength "
            "FROM dbo.Elements WHERE Label='MAP%s';" % self.tag)
        self.assertEqual(rows, [["MTDElementMapping", "20", "100.0"]])
        # 接口自己的写后回读全绿：几何、挂载、角点朝向都被逐列证明过
        checks = res.data["verify"]["checks"]
        self.assertTrue(all(checks.values()), "写后校验有未通过项：%s" % checks)
        self.assertEqual(res.data["plan"]["label"], "MAP%s" % self.tag)

    def test_03_cfd_writes_objects_without_outputs(self):
        # 取两个尚未存在的风向，避免和副本库里已有工况撞 (Direction,Stability)（exit=6）
        import mup_weather_import as mw
        used = {row[0] for row in mw._query_rows(
            _REAL_WRITE_DB, "SELECT Direction FROM dbo.CFD WHERE Stability=2;")}
        fresh = [str(d) for d in range(1, 360) if str(d) not in used][:2]
        self.assertEqual(len(fresh), 2, "副本库里可用风向不足")
        outputs_before = self._count(_REAL_WRITE_DB, "CFDOutputs")
        before = self._count(_REAL_WRITE_DB, "CFD")
        self._drive(
            "mup_cfd_import", "_simple_cfd_data_layer", "_cfd_import_running",
            [("目标数据库", _REAL_WRITE_DB), ("站点名", "DEMO"), ("工况名", "WT"),
             ("风向列表", ",".join(fresh)), ("热稳定度", "2"),
             ("最大迭代次数", "100"), ("每方向核数", "8"), ("收敛阈值", "90")])
        self.assertEqual(self._count(_REAL_WRITE_DB, "CFD"), before + 2)
        # 红线：只建对象不实跑 —— solver 产物表一格都不许动
        self.assertEqual(self._count(_REAL_WRITE_DB, "CFDOutputs"), outputs_before)

    def test_04_synthesis_clones_from_existing(self):
        before = self._count(_REAL_WRITE_DB, "WRAAnalysis")
        self._drive(
            "mup_synthesis_import", "_simple_comprehensive_data_layer",
            "_synthesis_import_running",
            [("目标数据库", _REAL_WRITE_DB), ("站点名", "DEMO"), ("工况名", ""),
             ("克隆源综合", ""), ("风机配置名", "CaseElementConfiguration"),
             ("综合名称", "SYN%s" % self.tag)])
        self.assertEqual(self._count(_REAL_WRITE_DB, "WRAAnalysis"), before + 1)
        new_id = ("(SELECT TOP 1 Id FROM dbo.WRAAnalysis WHERE Name='SYN%s')"
                  % self.tag)
        self.assertGreater(
            self._count(_REAL_WRITE_DB, "WRAAnalysisElements",
                        "WRAAnalysis_Id=" + new_id), 0, "EWR 克隆出的元素引用为空")


if __name__ == "__main__":
    unittest.main()
