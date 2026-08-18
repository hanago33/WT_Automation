# -*- coding: utf-8 -*-

"""Tests for TaskQueueWindow batch submit and public control APIs."""

import json

import wt_task_queue_window


def make_window(upload_response=None, submit_response=None):
    window = object.__new__(wt_task_queue_window.TaskQueueWindow)
    window.base_url = "http://127.0.0.1:8768"
    window.monitor_base_url = "http://127.0.0.1:8767"

    class _Var:
        def __init__(self, value=""):
            self.value = value

        def get(self):
            return self.value

    window.token_var = _Var("secret")
    window.user_var = _Var("alice")
    window.upload_response = upload_response or {"flowPath": "/flows/uploaded.json"}
    window.submit_response = submit_response or {
        "task": {"taskId": "task_test_0001"}
    }
    window.calls = []
    window.log_lines = []

    def post_ui(callback):
        window.calls.append(("post_ui", callback, None))
        callback()

    def render_logs(lines):
        window.log_lines.extend(lines)

    def append_log(line):
        window.log_lines.append(line)

    def post_json(path, payload):
        window.calls.append(("post", path, payload))
        if path == "/api/flows/upload":
            return window.upload_response
        return window.submit_response

    def get_json(path):
        window.calls.append(("get", path, None))
        return {"task": {"taskId": "task_test_0001", "status": "running"}}

    def refresh():
        window.calls.append(("refresh", None, None))

    window._post_ui = post_ui
    window._render_logs = render_logs
    window._append_log_text = append_log
    window._post_json = post_json
    window._get_json = get_json
    window.refresh = refresh
    window._closing = False
    window._after_id = None
    window._monitor_after_id = None
    window._queue_fail_streak = 0
    window._monitor_fail_streak = 0
    return window


def _write_flow(tmp_path, name="section_flow.json"):
    flow = {"name": "test flow", "steps": []}
    path = tmp_path / name
    path.write_text(
        json.dumps(flow, ensure_ascii=False),
        encoding="utf-8",
    )
    return str(path)


def test_batch_submit_collects_task_ids_and_calls_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(wt_task_queue_window.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(wt_task_queue_window.messagebox, "showwarning", lambda *a, **k: None)
    window = make_window()
    sections = [
        {"key": "sec_a", "title": "板块A", "path": _write_flow(tmp_path, "a.json")},
        {"key": "sec_b", "title": "板块B", "path": _write_flow(tmp_path, "b.json")},
    ]
    completed = []

    window._submit_simple_sections_worker(
        sections,
        "alice",
        completed_callback=lambda ids, results: completed.append((ids, results)),
    )

    assert len(completed) == 1
    task_ids, results = completed[0]
    assert task_ids == ["task_test_0001", "task_test_0001"]
    assert [item[1] for item in results] == ["已提交", "已提交"]
    posts = [call for call in window.calls if call[0] == "post"]
    assert len(posts) == 4
    assert posts[0][1] == "/api/flows/upload"
    assert posts[1][1] == "/api/tasks/submit"
    assert posts[1][2]["user"] == "alice"


def test_batch_submit_missing_task_id_returns_empty_list(tmp_path, monkeypatch):
    monkeypatch.setattr(wt_task_queue_window.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(wt_task_queue_window.messagebox, "showwarning", lambda *a, **k: None)
    window = make_window(submit_response={"task": {"taskId": ""}})
    sections = [
        {"key": "sec_a", "title": "板块A", "path": _write_flow(tmp_path, "a.json")},
    ]
    completed = []

    window._submit_simple_sections_worker(
        sections,
        "alice",
        completed_callback=lambda ids, results: completed.append((ids, results)),
    )

    assert len(completed) == 1
    task_ids, results = completed[0]
    assert task_ids == []
    assert results[0][1] == "已提交"


def test_get_task_detail_uses_task_id_path():
    window = make_window()
    payload = window.get_task_detail("task_abc")
    assert payload["task"]["taskId"] == "task_test_0001"
    assert window.calls[0] == ("get", "/api/tasks/task_abc", None)


def test_control_task_posts_action_and_returns_success():
    window = make_window()
    assert window.control_task("task_abc", "terminate") is True
    posts = [call for call in window.calls if call[0] == "post"]
    assert posts[0][1] == "/api/tasks/task_abc/terminate"
    assert any(call[0] == "refresh" for call in window.calls)


def test_control_task_returns_false_on_error():
    window = make_window()

    def boom(path, payload):
        raise OSError("connection refused")

    window._post_json = boom
    assert window.control_task("task_abc", "terminate") is False


def test_batch_submit_skips_identical_upload_when_flow_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(wt_task_queue_window.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(wt_task_queue_window.messagebox, "showwarning", lambda *a, **k: None)
    window = make_window()
    flow_content = {"name": "test flow", "steps": []}
    path = tmp_path / "same_flow.json"
    path.write_text(json.dumps(flow_content, ensure_ascii=False), encoding="utf-8")
    digest = wt_task_queue_window.TaskQueueWindow._flow_sha256(flow_content)
    window._get_json = lambda p: {
        "flows": [
            {
                "name": "same_flow.json",
                "path": "/flows/server/same_flow.json",
                "versions": [{"sha256": digest}],
            }
        ]
    }
    completed = []
    window._submit_simple_sections_worker(
        [{"key": "a", "title": "板块A", "path": str(path)}],
        "alice",
        skip_identical=True,
        completed_callback=lambda ids, results: completed.append((ids, results)),
    )
    assert completed[0][0] == ["task_test_0001"]
    assert completed[0][1][0][1] == "已提交（内容未变化，跳过上传）"
    posts = [call for call in window.calls if call[0] == "post"]
    assert len(posts) == 1
    assert posts[0][1] == "/api/tasks/submit"
    assert posts[0][2]["flowPath"] == "/flows/server/same_flow.json"


def test_batch_submit_uploads_when_flow_content_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(wt_task_queue_window.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(wt_task_queue_window.messagebox, "showwarning", lambda *a, **k: None)
    window = make_window()
    path = tmp_path / "changed_flow.json"
    path.write_text(json.dumps({"name": "test flow", "steps": []}, ensure_ascii=False),
                   encoding="utf-8")
    window._get_json = lambda p: {
        "flows": [
            {
                "name": "changed_flow.json",
                "path": "/flows/server/changed_flow.json",
                "versions": [{"sha256": "0" * 64}],
            }
        ]
    }
    completed = []
    window._submit_simple_sections_worker(
        [{"key": "a", "title": "板块A", "path": str(path)}],
        "alice",
        skip_identical=True,
        completed_callback=lambda ids, results: completed.append((ids, results)),
    )
    posts = [call for call in window.calls if call[0] == "post"]
    assert [call[1] for call in posts] == ["/api/flows/upload", "/api/tasks/submit"]
    assert completed[0][1][0][1] == "已提交"
