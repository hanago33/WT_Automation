# encoding: utf-8

import json
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wt_task_server import create_server

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


def _existing_flow_path():
    flow_dir = PROJECT_ROOT / "flow_packages"
    for path in sorted(flow_dir.glob("*.json")):
        if path.name != "flow_package_registry.json":
            return str(path)
    raise AssertionError("no flow package found for tests")


@pytest.fixture
def task_server(tmp_path):
    db = str(tmp_path / "queue.db")
    log_dir = str(tmp_path / "tasks")
    report_dir = str(tmp_path / "reports")
    server = create_server(
        "127.0.0.1",
        0,
        auth_token=TOKEN,
        queue_db=db,
        worker_launcher=lambda task, task_log_dir=None, **kwargs: (
            FakeProcess(),
            None,
        ),
        task_log_dir=log_dir,
        report_dir=report_dir,
        server_log_path=str(tmp_path / "server.log"),
    )
    server._scheduler_stop.set()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = "http://127.0.0.1:{}".format(server.server_address[1])
    yield server, base_url, db, log_dir, report_dir
    server.shutdown()
    thread.join(timeout=3)
    server.server_close()


def test_submit_new_options_and_queue_stats(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={
            "user": "alice",
            "flowPath": _existing_flow_path(),
            "priority": 5,
            "scheduledAt": "2026-08-11T12:00:00",
            "maxAttempts": 3,
            "retryDelaySeconds": 10,
            "timeoutSeconds": 120,
        },
    )
    assert status == 201
    task = payload["task"]
    assert task["priority"] == 5
    assert task["scheduledAt"] == "2026-08-11T12:00:00"
    assert task["maxAttempts"] == 3
    assert task["retryDelaySeconds"] == 10
    assert task["timeoutSeconds"] == 120

    status, payload = _request_json(
        "{}/api/queue/stats".format(base_url),
        token=TOKEN,
    )
    assert status == 200
    assert payload["total"] == 1
    assert payload["pending"] == 1
    assert payload["todaySubmitted"] == 1


def test_submit_rejects_invalid_enhanced_options(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    invalid_payloads = (
        {"priority": "abc"},
        {"priority": True},
        {"maxAttempts": 0},
        {"retryDelaySeconds": -1},
        {"timeoutSeconds": -1},
        {"scheduledAt": "not-a-datetime"},
    )
    for extra in invalid_payloads:
        payload = {
            "user": "alice",
            "flowPath": _existing_flow_path(),
        }
        payload.update(extra)
        status, _response = _request_json(
            "{}/api/tasks/submit".format(base_url),
            method="POST",
            token=TOKEN,
            payload=payload,
        )
        assert status == 400


def test_scheduler_auto_retry_on_failure(tmp_path):
    db = str(tmp_path / "queue.db")
    launched = []

    def launcher(task, task_log_dir=None, **kwargs):
        proc = FakeProcess()
        launched.append((task, proc))
        return proc, None

    server = create_server(
        "127.0.0.1",
        0,
        auth_token=TOKEN,
        queue_db=db,
        worker_launcher=launcher,
        server_log_path=str(tmp_path / "server.log"),
    )
    server._scheduler_stop.set()
    try:
        task = wt_task_queue.submit_task(
            "alice",
            "/flows/a.json",
            max_attempts=2,
            retry_delay_seconds=0,
            db_path=db,
        )
        server._scheduler_tick()
        assert len(launched) == 1
        assert wt_task_queue.get_task(task["taskId"], db_path=db)["status"] == "running"

        launched[0][1].finish(1)
        server._scheduler_tick()
        stored = wt_task_queue.get_task(task["taskId"], db_path=db)
        assert stored["status"] == "pending"
        assert stored["attempts"] == 2

        server._scheduler_tick()
        assert len(launched) == 2
        assert wt_task_queue.get_task(task["taskId"], db_path=db)["status"] == "running"
    finally:
        server.server_close()


def test_scheduler_timeout_kills_and_retries(tmp_path):
    db = str(tmp_path / "queue.db")
    launched = []

    def launcher(task, task_log_dir=None, **kwargs):
        proc = FakeProcess()
        launched.append((task, proc))
        return proc, None

    server = create_server(
        "127.0.0.1",
        0,
        auth_token=TOKEN,
        queue_db=db,
        worker_launcher=launcher,
        server_log_path=str(tmp_path / "server.log"),
    )
    server._scheduler_stop.set()
    try:
        task = wt_task_queue.submit_task(
            "alice",
            "/flows/a.json",
            max_attempts=2,
            timeout_seconds=60,
            db_path=db,
        )
        server._scheduler_tick()
        assert len(launched) == 1

        old_start = (datetime.now() - timedelta(hours=1)).isoformat(
            timespec="seconds"
        )
        conn = wt_task_queue._connect(db)
        conn.execute(
            "UPDATE tasks SET started_at = ? WHERE task_id = ?",
            (old_start, task["taskId"]),
        )
        conn.commit()
        conn.close()

        server._scheduler_tick()
        stored = wt_task_queue.get_task(task["taskId"], db_path=db)
        assert stored["status"] == "pending"
        assert stored["attempts"] == 2
        assert "timed out" in stored["error"]
        assert len(launched) == 1

        server._scheduler_tick()
        assert len(launched) == 2
        assert wt_task_queue.get_task(task["taskId"], db_path=db)["status"] == "running"
    finally:
        server.server_close()

def test_upload_submit_scheduler_success_preserves_chinese_space_name(tmp_path):
    flow_dir = tmp_path / "flows"
    flow_dir.mkdir()
    db = str(tmp_path / "queue.db")
    launched = []

    def launcher(task, task_log_dir=None, **kwargs):
        proc = FakeProcess()
        launched.append((task, proc))
        return proc, None

    server = create_server(
        "127.0.0.1",
        0,
        auth_token=TOKEN,
        queue_db=db,
        worker_launcher=launcher,
        task_log_dir=str(tmp_path / "tasks"),
        report_dir=str(tmp_path / "reports"),
        flow_dir=str(flow_dir),
        server_log_path=str(tmp_path / "server.log"),
    )
    server._scheduler_stop.set()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = "http://127.0.0.1:{}".format(server.server_address[1])
    try:
        content = {"name": "提交测试", "steps": [{"id": "step1"}]}
        status, payload = _request_json(
            "{}/api/flows/upload".format(base_url),
            method="POST",
            token=TOKEN,
            payload={"name": "我的流程 2026.json", "content": content},
        )
        assert status == 201
        saved = Path(payload["flowPath"])
        assert saved.parent == flow_dir
        assert saved.name == "我的流程 2026.json"

        status, payload = _request_json(
            "{}/api/tasks/submit".format(base_url),
            method="POST",
            token=TOKEN,
            payload={"user": "alice", "flowPath": payload["flowPath"]},
        )
        assert status == 201
        task_id = payload["task"]["taskId"]

        server._scheduler_tick()
        assert len(launched) == 1
        task = wt_task_queue.get_task(task_id, db_path=db)
        assert task["status"] == "running"
        assert task["flowPath"] == str(flow_dir / "我的流程 2026.json")

        launched[0][1].finish(0)
        server._scheduler_tick()
        assert wt_task_queue.get_task(task_id, db_path=db)["status"] == "success"
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
