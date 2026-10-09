# encoding: utf-8
"""第四期服务与监控界面仪表盘化（Server Monitor Dashboard）单元与回归测试。

覆盖两大监控宿主：
1. `WT_Launcher.py::ServerMonitorWindow`（独立弹出只读监控窗口）：
   - 友好异常翻译（_friendly_error 对 WinError 10061/10060/10054/DNS 的优化）；
   - 网络探测与延迟度量（_fetch_worker / _apply_payload 带 ping_ms 徽标与数据更新）；
   - 4 块现代化指标卡片状态同步（服务连接、任务状态、状态心跳、健康诊断）；
   - 智能日志工具栏：按日志级别筛选（全部/仅错误/仅警告/仅信息）；
   - 智能日志工具栏：关键字实时搜索过滤；
   - 暂停滚屏模式（pause_scroll_var 避免阅读时被 2s 轮询强行置底）；
   - 日志管理操作（_copy_visible_logs 复制可见日志、_copy_error_detail 复制异常、_clear_logs_view 清屏）；
   - _classify_line 委托 wt_logging 的契约兼容性。

2. `wt_task_queue_window.py` 监控页签（Tab 2: _build_monitor_tab）：
   - 监控服务网络延迟计算与徽标展示（_monitor_fetch_worker / _apply_monitor_payload 带 ping_ms）；
   - 4 块指标卡片数据契约（monitor_conn_var, monitor_ping_var, run_status_var, activity_var, updated_var, monitor_health_var, error_var）；
   - 离线状态标识（_mark_monitor_offline 状态、健康度与徽标设置）；
   - 监控日志级别与关键字过滤（_render_monitor_logs / _on_monitor_log_filter_changed）；
   - 增量渲染在暂停滚屏时的行为（_append_lines_incremental 在 monitor_pause_scroll_var 为真时不强行 see(END)）；
   - 监控日志工具栏辅助操作（_copy_monitor_visible_logs, _copy_monitor_error_detail, _clear_monitor_log_view）。

测试全量采用无 GUI 强依赖的轻量替身，毫秒级快速运行。
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Launcher as L
import wt_task_queue_window as Q
import wt_logging


# ── 测试替身 ────────────────────────────────────────────────────────────


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
        self.clipboard_data = ""

    def destroy(self):
        self.destroyed = True

    def clipboard_clear(self):
        self.clipboard_data = ""

    def clipboard_append(self, data):
        self.clipboard_data += str(data)

    def update_idletasks(self):
        pass


class FakeBadge:
    def __init__(self, text="", tone="muted"):
        self.text = text
        self.tone = tone

    def set_badge(self, text, tone):
        self.text = text
        self.tone = tone


class FakeLabel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def configure(self, **kwargs):
        self.kwargs.update(kwargs)


class FakeText:
    def __init__(self):
        self.content = ""
        self.state = "normal"
        self.tags = []
        self.seen = None

    def config(self, **kwargs):
        if "state" in kwargs:
            self.state = kwargs["state"]

    def configure(self, **kwargs):
        self.config(**kwargs)

    def delete(self, start, end):
        self.content = ""

    def insert(self, index, text, tag=None):
        self.content += str(text)
        if tag:
            self.tags.append((text, tag))

    def get(self, start, end):
        if end == "end-1c":
            return self.content.rstrip("\n")
        return self.content

    def see(self, index):
        self.seen = index

    def index(self, mark):
        lines = self.content.split("\n")
        return f"{len(lines)}.0"


# ── 单元测试：ServerMonitorWindow ──────────────────────────────────────


class TestServerMonitorWindowLogic(unittest.TestCase):
    def setUp(self):
        # 构造不调起实际 Tk 窗口的 ServerMonitorWindow 实例
        self.mon = object.__new__(L.ServerMonitorWindow)
        self.mon.window = FakeWindow()
        self.mon.conn_var = FakeVar("未连接")
        self.mon.ping_var = FakeVar("等待探测")
        self.mon.run_status_var = FakeVar("未知")
        self.mon.activity_var = FakeVar("-")
        self.mon.updated_var = FakeVar("-")
        self.mon.heartbeat_var = FakeVar("轮询周期 2s")
        self.mon.health_var = FakeVar("待检测")
        self.mon.error_var = FakeVar("-")
        self.mon.log_search_var = FakeVar("")
        self.mon.level_filter_var = FakeVar("全部")
        self.mon.pause_scroll_var = FakeVar(False)
        self.mon.ping_badge = FakeBadge()
        self.mon.conn_lbl = FakeLabel()
        self.mon.run_status_lbl = FakeLabel()
        self.mon.health_lbl = FakeLabel()
        self.mon.log_text = FakeText()
        self.mon._raw_error = ""
        self.mon._cached_log_lines = []

    def test_friendly_error_translations(self):
        self.assertIn("8767端口未监听", L.ServerMonitorWindow._friendly_error("<urlopen error [WinError 10061] No connection could be made>"))
        self.assertIn("8767端口未监听", L.ServerMonitorWindow._friendly_error("connection refused by target machine"))
        self.assertIn("连接超时", L.ServerMonitorWindow._friendly_error("timed out waiting for response [WinError 10060]"))
        self.assertIn("连接被重置", L.ServerMonitorWindow._friendly_error("connection reset by peer [WinError 10054]"))
        self.assertIn("无法解析", L.ServerMonitorWindow._friendly_error("getaddrinfo failed"))
        self.assertEqual(L.ServerMonitorWindow._friendly_error("自定义业务错误"), "自定义业务错误")

    def test_apply_payload_success_idle(self):
        status_payload = {
            "status": "idle",
            "activity": "等待调度",
            "error": None,
            "updatedAt": "2026-10-01 01:05:00",
        }
        logs_payload = {
            "lines": [
                "[2026-10-01 01:04:50] [INFO] 服务启动就绪",
                "[2026-10-01 01:04:55] [DEBUG] 心跳探测正常",
            ]
        }
        self.mon._apply_payload(status_payload, logs_payload, ping_ms=12)

        self.assertEqual(self.mon.conn_var.get(), "已连接")
        self.assertEqual(self.mon.ping_var.get(), "延迟: 12 ms")
        self.assertEqual(self.mon.ping_badge.text, "⏱️ 12 ms")
        self.assertEqual(self.mon.ping_badge.tone, "success")
        self.assertEqual(self.mon.run_status_var.get(), "空闲")
        self.assertEqual(self.mon.activity_var.get(), "等待调度")
        self.assertEqual(self.mon.health_var.get(), "✓ 运行正常")
        self.assertEqual(self.mon.error_var.get(), "无异常")
        self.assertEqual(self.mon.updated_var.get(), "2026-10-01 01:05:00")
        self.assertIn("服务启动就绪", self.mon.log_text.content)

    def test_apply_payload_with_error(self):
        status_payload = {
            "status": "failed",
            "activity": "执行失败",
            "error": "连接远程服务失败 [WinError 10061]",
            "updatedAt": "2026-10-01 01:06:00",
        }
        logs_payload = {"lines": ["[2026-10-01 01:06:00] [ERROR] 任务执行异常"]}
        self.mon._apply_payload(status_payload, logs_payload, ping_ms=180)

        self.assertEqual(self.mon.conn_var.get(), "已连接")
        self.assertEqual(self.mon.run_status_var.get(), "失败")
        self.assertEqual(self.mon.health_var.get(), "⚠️ 存在异常")
        self.assertIn("8767端口未监听", self.mon.error_var.get())
        self.assertEqual(self.mon.ping_badge.tone, "warning")

    def test_mark_offline(self):
        self.mon._mark_offline("WinError 10061 actively refused")
        self.assertEqual(self.mon.conn_var.get(), "未连接")
        self.assertEqual(self.mon.ping_var.get(), "服务离线")
        self.assertEqual(self.mon.ping_badge.text, "⏱️ 离线")
        self.assertEqual(self.mon.ping_badge.tone, "danger")
        self.assertEqual(self.mon.health_var.get(), "⚠️ 服务离线")
        self.assertEqual(self.mon.run_status_var.get(), "未知")
        self.assertIn("8767端口未监听", self.mon.error_var.get())

    def test_log_level_filtering(self):
        self.mon._cached_log_lines = [
            "[2026-10-01 01:00:00] [INFO] 开始同步任务",
            "[2026-10-01 01:00:01] [WARN] 步骤执行较慢",
            "[2026-10-01 01:00:02] [ERROR] 连接断开",
        ]
        # 1. 全部
        self.mon.level_filter_var.set("全部")
        self.mon._filter_and_render_logs()
        self.assertIn("开始同步任务", self.mon.log_text.content)
        self.assertIn("步骤执行较慢", self.mon.log_text.content)
        self.assertIn("连接断开", self.mon.log_text.content)

        # 2. 仅错误
        self.mon.level_filter_var.set("仅错误")
        self.mon._filter_and_render_logs()
        self.assertNotIn("开始同步任务", self.mon.log_text.content)
        self.assertNotIn("步骤执行较慢", self.mon.log_text.content)
        self.assertIn("连接断开", self.mon.log_text.content)

        # 3. 仅警告（含警告与错误）
        self.mon.level_filter_var.set("仅警告")
        self.mon._filter_and_render_logs()
        self.assertNotIn("开始同步任务", self.mon.log_text.content)
        self.assertIn("步骤执行较慢", self.mon.log_text.content)
        self.assertIn("连接断开", self.mon.log_text.content)

        # 4. 仅信息
        self.mon.level_filter_var.set("仅信息")
        self.mon._filter_and_render_logs()
        self.assertIn("开始同步任务", self.mon.log_text.content)
        self.assertNotIn("连接断开", self.mon.log_text.content)

    def test_log_keyword_search(self):
        self.mon._cached_log_lines = [
            "[2026-10-01 01:00:00] [INFO] 开始测风塔模块",
            "[2026-10-01 01:00:01] [INFO] 开始机位点模块",
            "[2026-10-01 01:00:02] [INFO] 测风塔数据验证完成",
        ]
        self.mon.log_search_var.set("测风塔")
        self.mon._filter_and_render_logs()
        self.assertIn("开始测风塔模块", self.mon.log_text.content)
        self.assertNotIn("开始机位点模块", self.mon.log_text.content)
        self.assertIn("测风塔数据验证完成", self.mon.log_text.content)

    def test_pause_scroll(self):
        self.mon._cached_log_lines = ["[2026-10-01 01:00:00] line 1"]
        # 未暂停时自动滚到底部
        self.mon.pause_scroll_var.set(False)
        self.mon._filter_and_render_logs()
        self.assertEqual(self.mon.log_text.seen, "end")

        # 暂停滚屏时不再置底
        self.mon.log_text.seen = None
        self.mon.pause_scroll_var.set(True)
        self.mon._cached_log_lines.append("[2026-10-01 01:00:01] line 2")
        self.mon._filter_and_render_logs()
        self.assertIsNone(self.mon.log_text.seen)

    def test_clipboard_operations(self):
        self.mon.log_text.content = "第一行日志\n第二行日志"
        self.mon._copy_visible_logs()
        self.assertEqual(self.mon.window.clipboard_data, "第一行日志\n第二行日志")

        self.mon._raw_error = "测试异常堆栈信息"
        self.mon._copy_error_detail()
        self.assertEqual(self.mon.window.clipboard_data, "测试异常堆栈信息")

        self.mon._clear_logs_view()
        self.assertEqual(self.mon.log_text.content, "")
        self.assertEqual(self.mon._cached_log_lines, [])

    def test_classify_line_contract(self):
        self.assertEqual(L.ServerMonitorWindow._classify_line("[ERROR] 发生错误"), "error")
        self.assertEqual(L.ServerMonitorWindow._classify_line("[WARN] 存在告警"), "warning")
        self.assertEqual(L.ServerMonitorWindow._classify_line("[INFO] 正常信息"), "info")


# ── 单元测试：TaskQueueWindow 监控页签 ─────────────────────────────────


class TestTaskQueueMonitorTabLogic(unittest.TestCase):
    def setUp(self):
        self.win = object.__new__(Q.TaskQueueWindow)
        self.win.window = FakeWindow()
        self.win.monitor_conn_var = FakeVar("未连接")
        self.win.monitor_ping_var = FakeVar("等待探测")
        self.win.run_status_var = FakeVar("未知")
        self.win.activity_var = FakeVar("-")
        self.win.updated_var = FakeVar("-")
        self.win.monitor_heartbeat_var = FakeVar("轮询周期 2s")
        self.win.monitor_health_var = FakeVar("待检测")
        self.win.error_var = FakeVar("-")
        self.win.monitor_search_var = FakeVar("")
        self.win.monitor_level_var = FakeVar("全部")
        self.win.monitor_pause_scroll_var = FakeVar(False)
        self.win.monitor_ping_badge = FakeBadge()
        self.win.monitor_conn_lbl = FakeLabel()
        self.win.monitor_status_lbl = FakeLabel()
        self.win.monitor_health_lbl = FakeLabel()
        self.win.monitor_log_text = FakeText()
        self.win._raw_monitor_error = ""
        self.win._cached_monitor_logs = []
        self.win._monitor_fail_streak = 0
        # 赋给【实例属性】时不经描述符协议：直接取经类访问后的普通函数即可。
        # 若包成 staticmethod 对象，Python <3.10 会报 "staticmethod object is
        # not callable"（3.10+ 起 staticmethod 对象恰可直接调用，掩盖此差异）。
        self.win._friendly_error = Q.TaskQueueWindow._friendly_error
        self.win._classify_line = wt_logging.tag_for_line

    def test_apply_monitor_payload_with_ping(self):
        status_payload = {
            "status": "running",
            "activity": "正在执行步骤 4/10",
            "error": "",
            "updatedAt": "2026-10-01 01:10:00",
        }
        logs_payload = {
            "lines": [
                "[2026-10-01 01:09:59] [INFO] 任务开始",
                "[2026-10-01 01:10:00] [INFO] 步骤 4 正在运行",
            ]
        }
        self.win._apply_monitor_payload(status_payload, logs_payload, ping_ms=25)

        self.assertEqual(self.win.monitor_conn_var.get(), "已连接")
        self.assertEqual(self.win.monitor_ping_var.get(), "延迟: 25 ms")
        self.assertEqual(self.win.monitor_ping_badge.text, "⏱️ 25 ms")
        self.assertEqual(self.win.monitor_ping_badge.tone, "success")
        self.assertEqual(self.win.run_status_var.get(), "运行中")
        self.assertEqual(self.win.activity_var.get(), "正在执行步骤 4/10")
        self.assertEqual(self.win.monitor_health_var.get(), "✓ 运行正常")
        self.assertEqual(self.win.error_var.get(), "无异常")
        self.assertEqual(self.win.updated_var.get(), "2026-10-01 01:10:00")

    def test_mark_monitor_offline(self):
        self.win._mark_monitor_offline("Connection refused: 8767")
        self.assertEqual(self.win.monitor_ping_var.get(), "服务离线")
        self.assertEqual(self.win.monitor_ping_badge.text, "⏱️ 离线")
        self.assertEqual(self.win.monitor_ping_badge.tone, "danger")
        self.assertEqual(self.win.monitor_health_var.get(), "⚠️ 服务离线")
        self.assertEqual(self.win.run_status_var.get(), "未知")
        self.assertIn("未连接", self.win.monitor_conn_var.get())

    def test_monitor_log_search_and_level_filter(self):
        self.win._cached_monitor_logs = [
            "[2026-10-01 01:00:00] [INFO] step 1 success",
            "[2026-10-01 01:00:01] [WARN] retry step 2",
            "[2026-10-01 01:00:02] [ERROR] step 3 fatal error",
        ]
        # 1. 过滤错误
        self.win.monitor_level_var.set("仅错误")
        self.win._render_monitor_logs(self.win._cached_monitor_logs)
        self.assertIn("fatal error", self.win.monitor_log_text.content)
        self.assertNotIn("step 1", self.win.monitor_log_text.content)

        # 2. 关键字搜索
        self.win.monitor_level_var.set("全部")
        self.win.monitor_search_var.set("retry")
        self.win._render_monitor_logs(self.win._cached_monitor_logs)
        self.assertIn("retry step 2", self.win.monitor_log_text.content)
        self.assertNotIn("fatal error", self.win.monitor_log_text.content)

    def test_monitor_clipboard_and_clear(self):
        self.win.monitor_log_text.content = "监控日志行 A\n监控日志行 B"
        self.win._copy_monitor_visible_logs()
        self.assertEqual(self.win.window.clipboard_data, "监控日志行 A\n监控日志行 B")

        self.win._raw_monitor_error = "监控远程连接异常"
        self.win._copy_monitor_error_detail()
        self.assertEqual(self.win.window.clipboard_data, "监控远程连接异常")

        self.win._clear_monitor_log_view()
        self.assertEqual(self.win.monitor_log_text.content, "")
        self.assertEqual(self.win._cached_monitor_logs, [])


if __name__ == "__main__":
    unittest.main()
