# encoding: utf-8

import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wt_task_server import create_server
from wt_task_queue_window import filter_tasks

import wt_task_queue


TOKEN = "secret-token"


class FakeProcess:
    def __init__(self):
        self.pid = None
        self.returncode = None

    def poll(self):
        return self.returncode

    def finish(self, code=0):
        self.returncode = code

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        self.returncode = 1


class _WebhookHandler(BaseHTTPRequestHandler):
    receiver = None

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            payload = {"raw": raw}
        self.receiver.events.append(payload)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class WebhookReceiver:
    def __init__(self):
        self.events = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _WebhookHandler)
        _WebhookHandler.receiver = self
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.url = "http://127.0.0.1:{}/hook".format(
            self.server.server_address[1]
        )

    def close(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()


def _request_json(url, method="GET", token=None, payload=None):
    headers = {"Accept": "application/json"}
    data = None
    if token:
        headers["Authorization"] = "Bearer " + token
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            payload = {"error": body}
        return exc.code, payload


def _make_server(tmp_path, **kwargs):
    options = {
        "queue_db": str(tmp_path / "queue.db"),
        "task_log_dir": str(tmp_path / "tasks"),
        "report_dir": str(tmp_path / "reports"),
        "server_log_path": str(tmp_path / "server.log"),
        "worker_launcher": lambda task, task_log_dir=None, **kw: (
            FakeProcess(),
            None,
        ),
    }
    options.update(kwargs)
    server = create_server("127.0.0.1", 0, auth_token=TOKEN, **options)
    server._scheduler_stop.set()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = "http://127.0.0.1:{}".format(server.server_address[1])
    return server, base_url, thread


def _stop_server(server, thread):
    server.shutdown()
    thread.join(timeout=3)
    server.server_close()


def _wait_for(predicate, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def _existing_flow_path():
    flow_dir = PROJECT_ROOT / "flow_packages"
    for path in sorted(flow_dir.glob("*.json")):
        if path.name != "flow_package_registry.json":
            return str(path)
    raise AssertionError("no flow package found for tests")


def test_submit_persists_notify_url(tmp_path):
    db = str(tmp_path / "queue.db")
    task = wt_task_queue.submit_task(
        "alice",
        "/flows/a.json",
        notify_url="https://example.com/hook",
        db_path=db,
    )
    assert task["notifyUrl"] == "https://example.com/hook"


def test_submit_rejects_invalid_notify_url(tmp_path):
    server, base_url, thread = _make_server(tmp_path)
    try:
        for value in ("not-a-url", "ftp://example.com", "https://" * 100):
            status, _payload = _request_json(
                "{}/api/tasks/submit".format(base_url),
                method="POST",
                token=TOKEN,
                payload={
                    "user": "alice",
                    "flowPath": _existing_flow_path(),
                    "notifyUrl": value,
                },
            )
            assert status == 400
    finally:
        _stop_server(server, thread)


def test_audit_records_submit_and_control_actions(tmp_path):
    server, base_url, thread = _make_server(tmp_path)
    try:
        status, payload = _request_json(
            "{}/api/tasks/submit".format(base_url),
            method="POST",
            token=TOKEN,
            payload={"user": "alice", "flowPath": _existing_flow_path()},
        )
        assert status == 201
        task_id = payload["task"]["taskId"]
        for action in ("pause", "resume", "cancel"):
            _request_json(
                "{}/api/tasks/{}/{}".format(base_url, task_id, action),
                method="POST",
                token=TOKEN,
                payload={},
            )
        status, payload = _request_json(
            "{}/api/audit?taskId={}".format(base_url, task_id),
            token=TOKEN,
        )
        assert status == 200
        actions = [event["action"] for event in payload["events"]]
        assert actions.count("submit") == 1
        assert "pause" in actions
        assert "resume" in actions
        assert "cancel" in actions
    finally:
        _stop_server(server, thread)


def test_audit_endpoint_requires_auth_and_filters_user(tmp_path):
    server, base_url, thread = _make_server(tmp_path)
    try:
        status, _payload = _request_json("{}/api/audit".format(base_url))
        assert status == 401
        for user in ("alice", "bob"):
            _request_json(
                "{}/api/tasks/submit".format(base_url),
                method="POST",
                token=TOKEN,
                payload={"user": user, "flowPath": _existing_flow_path()},
            )
        status, payload = _request_json(
            "{}/api/audit?user=alice".format(base_url),
            token=TOKEN,
        )
        assert status == 200
        assert len(payload["events"]) == 1
        assert payload["events"][0]["user"] == "alice"
    finally:
        _stop_server(server, thread)


def test_webhook_receives_success_event(tmp_path):
    receiver = WebhookReceiver()
    launched = []

    def launcher(task, task_log_dir=None, **kwargs):
        proc = FakeProcess()
        launched.append((task, proc))
        return proc, None

    server, base_url, thread = _make_server(
        tmp_path,
        worker_launcher=launcher,
        webhook_url=receiver.url,
    )
    try:
        status, payload = _request_json(
            "{}/api/tasks/submit".format(base_url),
            method="POST",
            token=TOKEN,
            payload={
                "user": "alice",
                "flowPath": _existing_flow_path(),
                "notifyUrl": receiver.url,
            },
        )
        assert status == 201
        task_id = payload["task"]["taskId"]
        server._scheduler_tick()
        assert len(launched) == 1
        launched[0][1].finish(0)
        server._scheduler_tick()
        assert wt_task_queue.get_task(task_id, db_path=server.queue_db)[
            "status"
        ] == "success"
        assert _wait_for(lambda: len(receiver.events) >= 1)
        event = receiver.events[0]
        assert event["event"] == "task.success"
        assert event["taskId"] == task_id
        assert event["status"] == "success"
        assert event["user"] == "alice"
    finally:
        _stop_server(server, thread)
        receiver.close()


def test_webhook_receives_failed_event_after_max_attempts(tmp_path):
    receiver = WebhookReceiver()
    launched = []

    def launcher(task, task_log_dir=None, **kwargs):
        proc = FakeProcess()
        launched.append((task, proc))
        return proc, None

    server, base_url, thread = _make_server(
        tmp_path,
        worker_launcher=launcher,
        webhook_url=receiver.url,
    )
    try:
        status, payload = _request_json(
            "{}/api/tasks/submit".format(base_url),
            method="POST",
            token=TOKEN,
            payload={
                "user": "alice",
                "flowPath": _existing_flow_path(),
                "maxAttempts": 1,
            },
        )
        assert status == 201
        task_id = payload["task"]["taskId"]
        server._scheduler_tick()
        launched[0][1].finish(1)
        server._scheduler_tick()
        assert wt_task_queue.get_task(task_id, db_path=server.queue_db)[
            "status"
        ] == "failed"
        assert _wait_for(lambda: len(receiver.events) >= 1)
        event = receiver.events[0]
        assert event["event"] == "task.failed"
        assert event["taskId"] == task_id
        assert event["status"] == "failed"
    finally:
        _stop_server(server, thread)
        receiver.close()


def test_cancel_sends_webhook(tmp_path):
    receiver = WebhookReceiver()
    server, base_url, thread = _make_server(tmp_path)
    try:
        status, payload = _request_json(
            "{}/api/tasks/submit".format(base_url),
            method="POST",
            token=TOKEN,
            payload={
                "user": "alice",
                "flowPath": _existing_flow_path(),
                "notifyUrl": receiver.url,
            },
        )
        assert status == 201
        task_id = payload["task"]["taskId"]
        status, _payload = _request_json(
            "{}/api/tasks/{}/cancel".format(base_url, task_id),
            method="POST",
            token=TOKEN,
            payload={},
        )
        assert status == 200
        assert _wait_for(lambda: len(receiver.events) >= 1)
        assert receiver.events[0]["event"] == "task.canceled"
        assert receiver.events[0]["status"] == "canceled"
    finally:
        _stop_server(server, thread)
        receiver.close()


def test_log_cleanup_removes_old_files_only(tmp_path):
    log_dir = tmp_path / "tasks"
    log_dir.mkdir()
    old_file = log_dir / "old_task.log"
    new_file = log_dir / "new_task.log"
    old_file.write_text("old", encoding="utf-8")
    new_file.write_text("new", encoding="utf-8")
    old_time = time.time() - 3 * 86400
    import os

    os.utime(old_file, (old_time, old_time))
    removed = wt_task_queue.cleanup_task_logs(
        log_dir=str(log_dir),
        retention_days=1,
        db_path=str(tmp_path / "queue.db"),
    )
    assert removed == 1
    assert not old_file.exists()
    assert new_file.exists()


def test_filter_tasks_helper():
    tasks = [
        {"taskId": "t1", "user": "alice", "status": "running", "flowPath": "/a.json"},
        {"taskId": "t2", "user": "bob", "status": "failed", "flowPath": "/b.json"},
    ]
    assert [t["taskId"] for t in filter_tasks(tasks, "运行中", "")] == ["t1"]
    assert [t["taskId"] for t in filter_tasks(tasks, "", "alice")] == ["t1"]
    assert [t["taskId"] for t in filter_tasks(tasks, "失败", "bob")] == ["t2"]
    assert filter_tasks(tasks, "全部", "") == tasks
    assert filter_tasks(tasks, "成功", "") == []


def test_health_includes_observability_fields(tmp_path):
    server, base_url, thread = _make_server(
        tmp_path,
        log_retention_days=7,
    )
    try:
        status, payload = _request_json("{}/api/health".format(base_url))
        assert status == 200
        assert payload["service"] == "wt_task_server"
        assert payload["version"] == 1
        assert payload["uptimeSeconds"] >= 0
        assert payload["logRetentionDays"] == 7
        assert payload["queueDb"] == server.queue_db
    finally:
        _stop_server(server, thread)
