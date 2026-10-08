# -*- coding: utf-8 -*-
"""Unit tests for Phase 8 Epic 3: Queue Global Dashboard & Batch Mast Queue Generator."""

import json
import os
import wt_task_queue_window


class MockBadge:
    def __init__(self, text="", tone="muted"):
        self.text = text
        self.tone = tone

    def set_badge(self, new_text, new_tone):
        self.text = new_text
        self.tone = new_tone


def make_mock_queue_window():
    window = object.__new__(wt_task_queue_window.TaskQueueWindow)
    window.base_url = "http://127.0.0.1:8768"
    window.monitor_base_url = "http://127.0.0.1:8767"
    window.project_dir = "E:/Test/Proj"
    window.calls = []
    window.log_lines = []

    class _Var:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

        def set(self, val):
            self.value = val

    window.token_var = _Var("secret")
    window.user_var = _Var("alice")
    window.status_filter_var = _Var("全部")
    window.keyword_var = _Var("")
    window.conn_var = _Var("未连接")
    window.stats_var = _Var("")

    window.stat_badge_total = MockBadge()
    window.stat_badge_pending = MockBadge()
    window.stat_badge_running = MockBadge()
    window.stat_badge_success = MockBadge()
    window.stat_badge_failed = MockBadge()

    window.window = object()
    window._closing = False

    def post_ui(callback):
        window.calls.append(("post_ui", callback))
        if callable(callback):
            try:
                callback()
            except Exception:
                pass

    def post_json(path, payload):
        window.calls.append(("post", path, payload))
        if path == "/api/flows/upload":
            return {"flowPath": "/flows/uploaded.json"}
        return {"task": {"taskId": "task_mast_test"}}

    def get_json(path):
        window.calls.append(("get", path, None))
        if path == "/api/flows":
            return {"flows": [{"name": "标准化测风流.json", "path": "/flows/std.json"}]}
        return {"task": {"taskId": "task_1", "status": "running"}}

    def refresh():
        window.calls.append(("refresh", None))

    window._post_ui = post_ui
    window._post_json = post_json
    window._get_json = get_json
    window.refresh = refresh
    return window


def test_queue_dashboard_update_with_stats_dict():
    window = make_mock_queue_window()
    stats = {
        "byStatus": {
            "pending": 5,
            "running": 2,
            "success": 10,
            "failed": 3,
            "terminated": 1,
        }
    }
    window._update_queue_dashboard(tasks=[], stats=stats)

    assert window.stat_badge_total.text == "总计 21"
    assert window.stat_badge_pending.text == "排队中 5"
    assert window.stat_badge_pending.tone == "info"
    assert window.stat_badge_running.text == "运行中 2"
    assert window.stat_badge_running.tone == "primary"
    assert window.stat_badge_success.text == "成功 10"
    assert window.stat_badge_success.tone == "success"
    assert window.stat_badge_failed.text == "失败 4"
    assert window.stat_badge_failed.tone == "danger"


def test_queue_dashboard_update_fallback_from_tasks_list():
    window = make_mock_queue_window()
    tasks = [
        {"taskId": "t1", "status": "pending"},
        {"taskId": "t2", "status": "queued"},
        {"taskId": "t3", "status": "running"},
        {"taskId": "t4", "status": "success"},
        {"taskId": "t5", "status": "failed"},
    ]
    window._update_queue_dashboard(tasks=tasks, stats=None)

    assert window.stat_badge_total.text == "总计 5"
    assert window.stat_badge_pending.text == "排队中 2"
    assert window.stat_badge_running.text == "运行中 1"
    assert window.stat_badge_success.text == "成功 1"
    assert window.stat_badge_failed.text == "失败 1"


def test_retry_all_failed_tasks_worker():
    window = make_mock_queue_window()
    failed_tasks = [
        {"taskId": "task_err_1", "status": "failed"},
        {"taskId": "task_err_2", "status": "terminated"},
    ]
    window._retry_all_failed_worker(failed_tasks)

    posts = [c for c in window.calls if c[0] == "post"]
    assert len(posts) == 2
    assert posts[0][1] == "/api/tasks/task_err_1/resume"
    assert posts[1][1] == "/api/tasks/task_err_2/resume"
    assert any(c[0] == "refresh" for c in window.calls)


def test_clear_finished_tasks_worker():
    window = make_mock_queue_window()
    finished_tasks = [
        {"taskId": "task_done_1", "status": "success"},
        {"taskId": "task_done_2", "status": "canceled"},
    ]
    window._clear_finished_worker(finished_tasks)

    posts = [c for c in window.calls if c[0] == "post"]
    assert len(posts) == 2
    assert posts[0][1] == "/api/tasks/task_done_1/delete"
    assert posts[1][1] == "/api/tasks/task_done_2/delete"
    assert any(c[0] == "refresh" for c in window.calls)


def test_batch_mast_queue_worker_submits_masts(tmp_path):
    window = make_mock_queue_window()

    dialog = object.__new__(wt_task_queue_window.BatchMastQueueDialog)
    dialog.queue_window = window
    dialog.local_flow_content = None
    dialog.server_flows = [
        {"name": "测风分析流", "path": "/flows/wind.json"}
    ]
    dialog._is_submitting = False
    dialog.dialog = object()

    class _MockStatusLbl:
        def config(self, **kwargs):
            pass

    dialog.status_lbl = _MockStatusLbl()

    selected_masts = [
        {"mastName": "1831", "hubHeight": "80", "lon": "115.1", "lat": "41.2", "elev": "1200", "utmX": "500000", "utmY": "4500000"},
        {"mastName": "1832", "hubHeight": "100", "longitude": "115.2", "latitude": "41.3", "elevation": "1250", "utmX": "501000", "utmY": "4501000"},
    ]

    completed = []

    dialog._submit_batch_masts_worker(
        selected_masts=selected_masts,
        chosen_flow_text="测风分析流",
        work_dir=str(tmp_path),
        user="bob",
        priority=5,
        max_attempts=2,
        timeout_seconds=3600,
        completed_callback=lambda ids, succ, fail: completed.append((ids, succ, fail)),
    )

    assert len(completed) == 1
    ids, succ, fail = completed[0]
    assert succ == 2
    assert fail == 0
    assert len(ids) == 2

    posts = [c for c in window.calls if c[0] == "post"]
    assert len(posts) == 2
    for p in posts:
        assert p[1] == "/api/tasks/submit"
        payload = p[2]
        assert payload["user"] == "bob"
        assert payload["flowPath"] == "/flows/wind.json"
        assert payload["priority"] == 5
        assert payload["maxAttempts"] == 2
        assert payload["timeoutSeconds"] == 3600
        assert payload["runtimeConfig"]["projectWorkDir"] == str(tmp_path)
        assert bool(payload.get("idempotencyKey"))

    assert posts[0][2]["runtimeConfig"]["mastId"] == "1831"
    assert posts[0][2]["runtimeConfig"]["hubHeight"] == "80"
    assert posts[0][2]["runtimeConfig"]["longitude"] == "115.1"
    assert posts[0][2]["runtimeConfig"]["latitude"] == "41.2"
    assert posts[0][2]["runtimeConfig"]["elevation"] == "1200"

    assert posts[1][2]["runtimeConfig"]["mastId"] == "1832"
    assert posts[1][2]["runtimeConfig"]["hubHeight"] == "100"
    assert posts[1][2]["runtimeConfig"]["longitude"] == "115.2"
    assert posts[1][2]["runtimeConfig"]["latitude"] == "41.3"
    assert posts[1][2]["runtimeConfig"]["elevation"] == "1250"


def test_batch_mast_queue_worker_uploads_local_flow(tmp_path):
    window = make_mock_queue_window()

    flow_file = tmp_path / "local_test_flow.json"
    flow_file.write_text(json.dumps({"name": "本地自定义流", "steps": []}), encoding="utf-8")

    dialog = object.__new__(wt_task_queue_window.BatchMastQueueDialog)
    dialog.queue_window = window
    dialog.local_flow_path = str(flow_file)
    dialog.local_flow_content = {"name": "本地自定义流", "steps": []}
    dialog.server_flows = []
    dialog._is_submitting = False
    dialog.dialog = object()

    class _MockStatusLbl:
        def config(self, **kwargs):
            pass

    dialog.status_lbl = _MockStatusLbl()

    selected_masts = [
        {"mastName": "1831", "hubHeight": "80"},
    ]

    completed = []

    dialog._submit_batch_masts_worker(
        selected_masts=selected_masts,
        chosen_flow_text="[本地] local_test_flow.json",
        work_dir=str(tmp_path),
        user="alice",
        priority=0,
        max_attempts=1,
        timeout_seconds=0,
        completed_callback=lambda ids, succ, fail: completed.append((ids, succ, fail)),
    )

    assert len(completed) == 1
    ids, succ, fail = completed[0]
    assert succ == 1

    posts = [c for c in window.calls if c[0] == "post"]
    assert len(posts) == 2
    # First post is upload
    assert posts[0][1] == "/api/flows/upload"
    assert posts[0][2]["user"] == "alice"
    assert posts[0][2]["name"] == "local_test_flow.json"
    # Second post is submit
    assert posts[1][1] == "/api/tasks/submit"
    assert posts[1][2]["flowPath"] == "/flows/uploaded.json"
    assert posts[1][2]["runtimeConfig"]["mastId"] == "1831"


def test_submit_simple_sections_distinct_idempotency_tokens(tmp_path):
    window = make_mock_queue_window()
    flow_file = tmp_path / "flow_common.json"
    flow_file.write_text(json.dumps({"name": "公共流程", "steps": []}), encoding="utf-8")

    sections = [
        {"key": "sec_1", "title": "板块1", "path": str(flow_file)},
        {"key": "sec_2", "title": "板块2", "path": str(flow_file)},
    ]

    completed = []
    window._submit_simple_sections_worker(
        sections=sections,
        user="bob",
        completed_callback=lambda results, task_ids: completed.append((results, task_ids)),
    )

    submit_posts = [c for c in window.calls if c[0] == "post" and c[1] == "/api/tasks/submit"]
    assert len(submit_posts) == 2
    token1 = submit_posts[0][2].get("idempotencyKey")
    token2 = submit_posts[1][2].get("idempotencyKey")
    assert token1 != token2
    assert token1.startswith("cli_")
    assert token2.startswith("cli_")
