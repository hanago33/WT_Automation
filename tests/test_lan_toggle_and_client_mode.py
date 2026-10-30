# -*- coding: utf-8 -*-
"""本机/远程模式一致性、内网服务单开关与“提交后可见性”回查的回归测试。

覆盖四类现场问题：
1. 「启动内网服务」「停止服务」两个按钮并存 → 合并为随状态自动切换的单开关；
2. Simple 界面切远程后，监控窗口仍是服务器角色 → 显式客户端模式；
3. 监控服务(8767)正常却提示连不上任务服务(8768) → 横幅要说明“是谁不通/地址是否写岔”；
4. 提交成功但服务端队列看不到 → 队列列表改为返回最新 N 条 + 提交后回查服务端。
"""

import json
import os
import tkinter as tk

import pytest

import wt_queue_selfcheck
import wt_task_queue
import wt_task_queue_window


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def _bare_window():
    """只装配被测方法所需字段，避免拉起整个窗口与轮询线程。"""
    window = object.__new__(wt_task_queue_window.TaskQueueWindow)
    window.base_url = "http://10.102.96.63:8768"
    window.monitor_base_url = "http://10.102.96.63:8767"
    window.log_lines = []
    window._append_log_text = lambda line: window.log_lines.append(line)
    window._queue_ever_ok = True
    window._monitor_ever_ok = True
    return window


def test_toggle_button_switches_text_with_local_service_state(monkeypatch):
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("no display available")
    try:
        window = _bare_window()
        window._lan_toggle_btn = tk.Button(root, text="🚀 一键启动内网服务")
        monkeypatch.setattr(
            wt_queue_selfcheck, "get_local_service_status", lambda: {"task_running": False}
        )
        window._refresh_lan_toggle_button()
        assert "一键启动内网服务" in window._lan_toggle_btn.cget("text")

        monkeypatch.setattr(
            wt_queue_selfcheck, "get_local_service_status", lambda: {"task_running": True}
        )
        window._refresh_lan_toggle_button()
        assert "停止内网服务" in window._lan_toggle_btn.cget("text")
    finally:
        root.destroy()


def test_toggle_action_dispatches_start_or_stop(monkeypatch):
    window = _bare_window()
    calls = []
    window.start_lan_services_action = lambda: calls.append("start")
    window.stop_lan_services_action = lambda: calls.append("stop")

    monkeypatch.setattr(
        wt_queue_selfcheck, "get_local_service_status", lambda: {"task_running": False}
    )
    window.toggle_lan_services_action()
    assert calls == ["start"]

    monkeypatch.setattr(
        wt_queue_selfcheck, "get_local_service_status", lambda: {"monitor_running": True}
    )
    window.toggle_lan_services_action()
    assert calls == ["start", "stop"]


def test_lan_control_row_has_single_action_button():
    """原界面并排两个按钮（启动/停止）；现在只允许一个随状态切换的开关。"""
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("no display available")
    window = None
    try:
        window = wt_task_queue_window.TaskQueueWindow(
            root, "http://127.0.0.1:8768", "alice", "wt2026"
        )
        assert window._lan_toggle_btn.cget("command")
        assert "一键启动内网服务" in window._lan_toggle_btn.cget("text") or (
            "停止内网服务" in window._lan_toggle_btn.cget("text")
        )
        texts = []
        parent = window._lan_toggle_btn.master
        for child in parent.winfo_children():
            try:
                texts.append(str(child.cget("text")))
            except tk.TclError:
                continue
        assert not any("停止服务" == text.strip() for text in texts)
    finally:
        if window is not None:
            window.window.destroy()
        root.destroy()


def test_client_mode_hides_local_service_controls():
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("no display available")
    window = None
    try:
        window = wt_task_queue_window.TaskQueueWindow(
            root, "http://127.0.0.1:8768", "alice", "wt2026"
        )
        window.set_client_mode(True)
        assert window.is_client_mode() is True
        assert window._lan_toggle_btn.winfo_manager() == ""
        assert window._lan_client_hint.winfo_manager() != ""
        assert "远程" in window.window.title()

        window.set_client_mode(False)
        assert window.is_client_mode() is False
        assert window._lan_toggle_btn.winfo_manager() != ""
        assert window._lan_client_hint.winfo_manager() == ""
    finally:
        if window is not None:
            window.window.destroy()
        root.destroy()


def test_client_mode_with_local_url_points_out_missing_server_address():
    """切了远程却还连着本机：横幅必须点明“请改为服务器 IP”，而不是假装正常。"""
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError:
        pytest.skip("no display available")
    window = None
    try:
        window = wt_task_queue_window.TaskQueueWindow(
            root, "http://127.0.0.1:8768", "alice", "wt2026", initial_client_mode=True
        )
        window._refresh_role_banner()
        assert "远程提交客户端" in window._role_banner_var.get()
        assert "请把任务服务地址改为服务器 IP" in window._role_banner_var.get()
    finally:
        if window is not None:
            window.window.destroy()
        root.destroy()


def test_banner_hint_explains_which_service_is_down():
    window = _bare_window()
    hint = window._banner_context_hint(is_queue=True)
    assert "只是任务服务 8768 不通" in hint

    window.monitor_base_url = "http://10.102.96.36:8767"
    hint = window._banner_context_hint(is_queue=True)
    assert "不是同一台机器" in hint
    assert "10.102.96.63" in hint and "10.102.96.36" in hint


def test_banner_hint_for_monitor_down_when_queue_ok():
    window = _bare_window()
    window.monitor_base_url = "http://10.102.96.63:8767"
    hint = window._banner_context_hint(is_queue=False)
    assert "只是监控服务 8767 不通" in hint


def test_check_lan_connection_verifies_token_on_authenticated_endpoint(monkeypatch):
    """/api/health 免鉴权：健康检查通过不能当作“令牌通过”。"""
    monkeypatch.setattr(wt_queue_selfcheck, "port_open", lambda *a, **k: True)

    def fake_wait(host, port, token, path, seconds=15):
        if path == "/api/queue/stats":
            return "UNAUTHORIZED"
        return {"service": "wt_task_server"}

    monkeypatch.setattr(wt_queue_selfcheck, "wait_service", fake_wait)
    res = wt_queue_selfcheck.check_lan_connection("10.0.0.9", token="bad")
    assert res["ok"] is False
    assert res["auth_ok"] is False
    assert "401" in res["error"]
    assert "令牌" in res["diagnosis"]


def test_check_lan_connection_reports_unresponsive_authenticated_api(monkeypatch):
    monkeypatch.setattr(wt_queue_selfcheck, "port_open", lambda *a, **k: True)

    def fake_wait(host, port, token, path, seconds=15):
        if path == "/api/queue/stats":
            return None
        return {"service": "wt_task_server"}

    monkeypatch.setattr(wt_queue_selfcheck, "wait_service", fake_wait)
    res = wt_queue_selfcheck.check_lan_connection("10.0.0.9", token="wt2026")
    assert res["ok"] is False
    assert res["port_open"] is True
    assert "鉴权" in res["error"] or "接口" in res["error"]


def test_list_tasks_returns_newest_within_limit(tmp_path):
    """超过 limit 后必须返回最新任务，否则新提交的任务永远不显示。"""
    db_path = str(tmp_path / "queue.db")
    created = [
        wt_task_queue.submit_task(user="alice", flow_path="f{}.json".format(i), db_path=db_path)
        for i in range(8)
    ]
    newest = wt_task_queue.list_tasks(db_path=db_path, limit=3)
    assert [task["taskId"] for task in newest] == [task["taskId"] for task in created[-3:]]
    # 仍按创建时间升序返回，界面渲染顺序不变
    assert newest[0]["createdAt"] <= newest[-1]["createdAt"]


def test_verify_submitted_tasks_visible_flags_missing_tasks():
    window = _bare_window()
    window._get_json = lambda path: {"tasks": [{"taskId": "task_1"}]}
    confirmed = window._verify_submitted_tasks_visible(["task_1"])
    assert "已确认" in confirmed and "10.102.96.63" in confirmed

    notice = window._verify_submitted_tasks_visible(["task_1", "task_2"])
    assert "没查到" in notice and "只看我的任务" in notice


def test_verify_submitted_tasks_visible_is_silent_on_query_failure():
    window = _bare_window()

    def boom(path):
        raise OSError("connection refused")

    window._get_json = boom
    notice = window._verify_submitted_tasks_visible(["task_1"])
    assert "回查失败" in notice


def test_launcher_switches_target_url_with_simple_mode():
    import WT_Launcher

    app = object.__new__(WT_Launcher.LauncherApp)
    app.simple_remote_task_url = "http://10.102.96.63:8768"
    app.task_queue_url = "http://127.0.0.1:8768"
    app.server_monitor_url = "http://127.0.0.1:8767"
    app.history_server_urls = []

    assert app._is_local_queue_url("http://127.0.0.1:8768") is True
    assert app._is_local_queue_url("http://10.102.96.63:8768") is False

    app._sync_simple_remote_target(True)
    assert app.task_queue_url == "http://10.102.96.63:8768"
    assert app.server_monitor_url == "http://10.102.96.63:8767"

    app._sync_simple_remote_target(False)
    assert app.task_queue_url == "http://127.0.0.1:8768"
    assert app.server_monitor_url == "http://127.0.0.1:8767"


def test_launcher_keeps_address_when_no_remote_target_known():
    """切远程但没有任何已知服务器地址：不得把本机地址当成远程目标。"""
    import WT_Launcher

    app = object.__new__(WT_Launcher.LauncherApp)
    app.simple_remote_task_url = ""
    app.task_queue_url = "http://127.0.0.1:8768"
    app.history_server_urls = ["http://127.0.0.1:8768"]

    app._sync_simple_remote_target(True)
    assert app.task_queue_url == "http://127.0.0.1:8768"
    assert app._known_remote_queue_url() == ""
