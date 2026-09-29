# encoding: utf-8
"""小-中批修复（session-22）的回归测试（2026-09-29）。

覆盖：
- #4 模板制作器「截屏(3秒后)」：after 链倒计时（不阻塞主循环、不可重入）；
- #5 候选区滚动接入 wt_wheel_router（消除 int(delta/120) 触控板零响应）；
- #7 CsvConvertCard 参数随模式联动禁用（转换类型/合并方式）；
- #8 generic_automation 关窗强退兜底（流程卡死时可强制退出）；
- #9 生成脚本：逐替换点校验（_replace_checked）、写盘备份+原子替换、
  README/run_example 人工维护版不被覆盖、当前母版全链路可生成。

被测对象为真实方法（轻量替身）/ 源码标记断言，不实例化窗口、无需 Tk 交互环境。
"""
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import build_image_template_library as T
import text_tools as TX

GENERIC_FLOW_DIR = os.path.join(PROJECT_DIR, "tools", "generic_flow")
if GENERIC_FLOW_DIR not in sys.path:
    sys.path.insert(0, GENERIC_FLOW_DIR)

try:
    import make_generic_copy as MGC  # noqa: E402
except Exception:  # pragma: no cover
    MGC = None


# ── 替身 ────────────────────────────────────────────────────────────────


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value

    def trace_add(self, *_a, **_k):
        pass


class FakeRoot:
    """记录 after 排定但不执行（倒计时场景需手动驱动 tick）。"""

    def __init__(self):
        self.after_calls = []

    def after(self, delay, callback=None, *args):
        self.after_calls.append((delay, callback, args))
        return "after#1"


class FakeStatefulWidget:
    def __init__(self):
        self.states = []

    def config(self, **kwargs):
        if "state" in kwargs:
            self.states.append(kwargs["state"])


def _read_source(*parts):
    path = os.path.join(PROJECT_DIR, *parts)
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


# ── #4 截屏倒计时 ───────────────────────────────────────────────────────


class ScreenshotCountdownTests(unittest.TestCase):
    def _make(self):
        win = object.__new__(T.TemplateBuilderApp)
        win.status_var = FakeVar("")
        win.root = FakeRoot()
        win._do_take_screenshot = lambda: None
        return win

    def test_take_screenshot_schedules_without_blocking(self):
        win = self._make()
        win.take_screenshot()
        self.assertTrue(win._screenshot_countdown_running)
        self.assertEqual(len(win.root.after_calls), 1)  # 仅排定，未阻塞
        win.take_screenshot()  # 倒计时中重复点击被忽略
        self.assertEqual(len(win.root.after_calls), 1)

    def test_countdown_tick_sequence(self):
        win = self._make()
        fired = []
        win._do_take_screenshot = lambda: fired.append(True)
        win._screenshot_countdown_running = True
        win._screenshot_countdown = 3
        win._screenshot_countdown_tick()  # 3 → 2
        self.assertEqual(win._screenshot_countdown, 2)
        self.assertEqual(fired, [])
        win._screenshot_countdown_tick()  # 2 → 1
        win._screenshot_countdown_tick()  # 1 → 0 → 执行截屏
        self.assertEqual(fired, [True])
        self.assertFalse(win._screenshot_countdown_running)

    def test_no_blocking_sleep_in_take_screenshot(self):
        src = _read_source("build_image_template_library.py")
        start = src.find("def take_screenshot")
        end = src.find("def _do_take_screenshot")
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        self.assertNotIn("time.sleep", src[start:end])


# ── #5 滚轮路由 ─────────────────────────────────────────────────────────


class WheelRouterMarkerTests(unittest.TestCase):
    def test_candidate_scroll_uses_shared_router(self):
        src = _read_source("build_image_template_library.py")
        self.assertIn("wt_wheel_router.register(self.right_canvas)", src)
        self.assertIn("wt_wheel_router.bind_root(self.root)", src)
        self.assertNotIn("def on_mousewheel", src)  # 旧处理已删除
        self.assertNotIn("int(-1 * (event.delta / 120))", src)  # 截断写法已消除


# ── #7 参数联动 ─────────────────────────────────────────────────────────


class ConvOptionSyncTests(unittest.TestCase):
    def _make(self):
        card = object.__new__(TX.CsvConvertCard)
        card.conv_var = FakeVar("xlsx")
        card.merge_var = FakeVar("one")
        card.merge_header_chk = FakeStatefulWidget()
        card._merge_rb_list = [FakeStatefulWidget() for _ in range(3)]
        card._txt_enc_rb_list = [FakeStatefulWidget() for _ in range(3)]
        return card

    def test_sync_xlsx_one_mode(self):
        card = self._make()
        card._sync_option_states()
        self.assertEqual(card._merge_rb_list[0].states[-1], "normal")
        self.assertEqual(card.merge_header_chk.states[-1], "normal")
        self.assertEqual(card._txt_enc_rb_list[0].states[-1], "disabled")

    def test_sync_txt_mode_disables_xlsx_options(self):
        card = self._make()
        card.conv_var.set("txt")
        card._sync_option_states()
        self.assertEqual(card._merge_rb_list[0].states[-1], "disabled")
        self.assertEqual(card.merge_header_chk.states[-1], "disabled")
        self.assertEqual(card._txt_enc_rb_list[0].states[-1], "normal")

    def test_sync_xlsx_non_one_disables_header_check(self):
        card = self._make()
        card.merge_var.set("single")
        card._sync_option_states()
        self.assertEqual(card._merge_rb_list[0].states[-1], "normal")
        self.assertEqual(card.merge_header_chk.states[-1], "disabled")

    def test_trace_registered(self):
        src = _read_source("text_tools.py")
        self.assertIn('self.conv_var.trace_add("write", lambda *a: self._sync_option_states())', src)


# ── #8 关窗强退兜底 ─────────────────────────────────────────────────────


class ForceExitMarkerTests(unittest.TestCase):
    def test_force_exit_fallback_markers(self):
        src = _read_source("tools", "generic_flow", "generic_automation.py")
        self.assertIn("_force_prompt_state", src)
        self.assertIn("是否强制退出？强制退出会跳过收尾，运行报告可能不完整。", src)
        self.assertIn("_stop_requested_at", src)
        self.assertIn("waited >= 15", src)


# ── #9 生成脚本 ─────────────────────────────────────────────────────────


@unittest.skipUnless(MGC is not None, "make_generic_copy 导入失败")
class GeneratorTests(unittest.TestCase):
    def test_replace_checked_raises_on_stale_point(self):
        with self.assertRaises(RuntimeError):
            MGC._replace_checked("abc", "zzz-not-present", "yyy")
        self.assertEqual(MGC._replace_checked("abc", "a", "A"), "Abc")
        self.assertEqual(MGC._replace_checked("aaa", "a", "b", 1), "baa")

    def test_write_text_backs_up_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.py")
            MGC.write_text(path, "v1")
            self.assertTrue(os.path.exists(path))
            MGC.write_text(path, "v2")
            with io.open(path, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "v2")
            with io.open(path + ".bak", encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "v1")  # 覆盖前备份
            self.assertFalse(os.path.exists(path + ".tmp"))  # 临时文件已原子替换

    def test_make_locator_on_current_master(self):
        """在真实母版上跑全链路：替换点失配会 raise，且 cfg_api 必须插入成功。"""
        master = os.path.join(PROJECT_DIR, "wt_flow_locator.py")
        text = MGC.read_text(master)
        out = MGC.make_locator(text)
        self.assertIn("def config_generic_target", out)  # 替换 11（cfg_api 注入）已生效
        self.assertIn("def _enum_target_win32_windows", out)
        self.assertIn("_TARGET_WINDOW_KEYWORDS = ()", out)

    def test_generated_locator_no_stale_assignment(self):
        src = _read_source("tools", "generic_flow", "generic_flow_locator.py")
        self.assertNotIn("_MUP_WINDOW_KEYWORDS = (", src)  # 赋值行已通用化
        self.assertNotIn("def _enum_visible_mup_win32_windows", src)
        self.assertIn("def configure_flow_locator(get_step_definition=None, log_step=None, get_main_window_candidates=None, log_at=None):", src)

    def test_readme_and_example_not_overwritten(self):
        src = _read_source("tools", "generic_flow", "make_generic_copy.py")
        self.assertIn("人工维护版不覆盖", src)
        self.assertIn("if not os.path.exists(readme_path):", src)
        self.assertIn("if not os.path.exists(example_path):", src)

    def test_helper_keeps_basename_fix(self):
        src = _read_source("tools", "generic_flow", "make_generic_copy.py")
        self.assertIn('os.path.basename(process_name or "").lower()', src)


if __name__ == "__main__":
    unittest.main()
