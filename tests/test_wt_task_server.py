# encoding: utf-8

import http.client
import json
import sys
import threading
import urllib.error
import urllib.request
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
        self.pid = 424242
        self.returncode = None

    def poll(self):
        return self.returncode

    def finish(self, code=0):
        self.returncode = code

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

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
        worker_launcher=lambda task, task_log_dir=None, **kwargs: (FakeProcess(), None),
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


def test_health_is_public(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, payload = _request_json("{}/api/health".format(base_url))
    assert status == 200
    assert payload["ok"] is True
    assert payload["service"] == "wt_task_server"


def test_auth_required(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, _payload = _request_json("{}/api/tasks".format(base_url))
    assert status == 401
    status, _payload = _request_json(
        "{}/api/tasks".format(base_url),
        token="wrong",
    )
    assert status == 401
    status, payload = _request_json(
        "{}/api/tasks".format(base_url),
        token=TOKEN,
    )
    assert status == 200
    assert payload["tasks"] == []


def test_submit_and_list(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={
            "user": "alice",
            "flowPath": _existing_flow_path(),
        },
    )
    assert status == 201
    task = payload["task"]
    assert task["status"] == "pending"
    assert task["user"] == "alice"

    status, payload = _request_json(
        "{}/api/tasks".format(base_url),
        token=TOKEN,
    )
    assert status == 200
    assert len(payload["tasks"]) == 1
    assert payload["tasks"][0]["taskId"] == task["taskId"]


def test_submit_validation(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, _payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={"user": "alice", "flowPath": ""},
    )
    assert status == 400
    status, _payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={"user": "alice", "flowPath": r"C:\Windows\notepad.exe"},
    )
    assert status == 400


def test_control_endpoints(task_server):
    server, base_url, db, _log_dir, _report_dir = task_server

    status, payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={"user": "alice", "flowPath": _existing_flow_path()},
    )
    task_id = payload["task"]["taskId"]
    status, payload = _request_json(
        "{}/api/tasks/{}/cancel".format(base_url, task_id),
        method="POST",
        token=TOKEN,
        payload={},
    )
    assert status == 200
    assert payload["task"]["status"] == "canceled"

    status, payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={"user": "bob", "flowPath": _existing_flow_path()},
    )
    task_id = payload["task"]["taskId"]
    status, payload = _request_json(
        "{}/api/tasks/{}/pause".format(base_url, task_id),
        method="POST",
        token=TOKEN,
        payload={},
    )
    assert status == 200
    assert payload["task"]["status"] == "paused"

    status, payload = _request_json(
        "{}/api/tasks/{}/resume".format(base_url, task_id),
        method="POST",
        token=TOKEN,
        payload={},
    )
    assert status == 200
    assert payload["task"]["status"] == "pending"

    running = wt_task_queue.claim_next_pending(db_path=db)
    assert running["taskId"] == task_id
    status, payload = _request_json(
        "{}/api/tasks/{}/terminate".format(base_url, task_id),
        method="POST",
        token=TOKEN,
        payload={},
    )
    assert status == 200
    assert payload["task"]["terminateRequested"] is True


def test_logs_and_report(task_server):
    _server, base_url, db, log_dir, report_dir = task_server
    status, payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={"user": "alice", "flowPath": _existing_flow_path()},
    )
    task_id = payload["task"]["taskId"]

    wt_task_queue.append_task_log(task_id, "task started", log_dir=log_dir)
    wt_task_queue.append_task_log(task_id, "step one", log_dir=log_dir)
    status, payload = _request_json(
        "{}/api/tasks/{}/logs?tail=10".format(base_url, task_id),
        token=TOKEN,
    )
    assert status == 200
    assert payload["totalLines"] == 2
    assert payload["lines"][-1] == "step one"

    wt_task_queue.mark_started(task_id, run_id="run-test-001", db_path=db)
    report_path = Path(report_dir) / "run-test-001.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({"runId": "run-test-001", "status": "success"}),
        encoding="utf-8",
    )
    status, payload = _request_json(
        "{}/api/tasks/{}/report".format(base_url, task_id),
        token=TOKEN,
    )
    assert status == 200
    assert payload["status"] == "success"


def test_method_not_allowed(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    for method in ("PUT", "DELETE", "PATCH"):
        status, _payload = _request_json(
            "{}/api/tasks".format(base_url),
            method=method,
            token=TOKEN,
            payload={},
        )
        assert status == 405


def test_unknown_path_returns_404(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, _payload = _request_json(
        "{}/api/unknown".format(base_url),
        token=TOKEN,
    )
    assert status == 404


def test_scheduler_marks_launch_failure(tmp_path):
    db = str(tmp_path / "queue.db")
    server_log = str(tmp_path / "server.log")

    def broken_launcher(task, task_log_dir=None, **kwargs):
        raise RuntimeError("boom")

    server = create_server(
        "127.0.0.1",
        0,
        auth_token=TOKEN,
        queue_db=db,
        worker_launcher=broken_launcher,
        server_log_path=server_log,
    )
    server._scheduler_stop.set()
    try:
        task = wt_task_queue.submit_task("u1", "/flows/1.json", db_path=db)
        server._scheduler_tick()
        stored = wt_task_queue.get_task(task["taskId"], db_path=db)
        assert stored["status"] == "failed"
        assert "boom" in stored["error"]
    finally:
        server.server_close()
    log_text = Path(server_log).read_text(encoding="utf-8")
    assert "launch failed" in log_text
    assert "boom" in log_text


def test_scheduler_claims_one_at_a_time(tmp_path):
    db = str(tmp_path / "queue.db")
    launched = []
    server = create_server(
        "127.0.0.1",
        0,
        auth_token=TOKEN,
        queue_db=db,
        worker_launcher=lambda task, task_log_dir=None, **kwargs: (
            launched.append((task, FakeProcess())) or (launched[-1][1], None)
        ),
        server_log_path=str(tmp_path / "server.log"),
    )
    server._scheduler_stop.set()
    try:
        first = wt_task_queue.submit_task("u1", "/flows/1.json", db_path=db)
        second = wt_task_queue.submit_task("u2", "/flows/2.json", db_path=db)

        server._scheduler_tick()
        assert wt_task_queue.get_task(first["taskId"], db_path=db)["status"] == "running"
        assert wt_task_queue.get_task(second["taskId"], db_path=db)["status"] == "pending"
        assert len(launched) == 1

        server._scheduler_tick()
        assert len(launched) == 1
        assert wt_task_queue.get_task(second["taskId"], db_path=db)["status"] == "pending"

        launched[0][1].finish(0)
        wt_task_queue.mark_success(first["taskId"], db_path=db)
        server._scheduler_tick()
        server._scheduler_tick()

        assert len(launched) == 2
        assert wt_task_queue.get_task(second["taskId"], db_path=db)["status"] == "running"
    finally:
        server.server_close()


def test_scope_and_limit_validation(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, _payload = _request_json(
        "{}/api/tasks?scope=mine".format(base_url),
        token=TOKEN,
    )
    assert status == 400

    status, payload = _request_json(
        "{}/api/tasks?scope=mine&user=alice".format(base_url),
        token=TOKEN,
    )
    assert status == 200
    assert payload["tasks"] == []

    status, _payload = _request_json(
        "{}/api/tasks?scope=other".format(base_url),
        token=TOKEN,
    )
    assert status == 400

    status, _payload = _request_json(
        "{}/api/tasks?limit=abc".format(base_url),
        token=TOKEN,
    )
    assert status == 400

    status, _payload = _request_json(
        "{}/api/tasks?limit=99999".format(base_url),
        token=TOKEN,
    )
    assert status == 200


def test_invalid_tail_validation(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={"user": "alice", "flowPath": _existing_flow_path()},
    )
    task_id = payload["task"]["taskId"]

    status, _payload = _request_json(
        "{}/api/tasks/{}/logs?tail=abc".format(base_url, task_id),
        token=TOKEN,
    )
    assert status == 400

    status, payload = _request_json(
        "{}/api/tasks/{}/logs?tail=10".format(base_url, task_id),
        token=TOKEN,
    )
    assert status == 200
    assert payload["totalLines"] == 0


def test_submit_rejects_steps_type(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, _payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={
            "user": "alice",
            "flowPath": _existing_flow_path(),
            "steps": {"bad": 1},
        },
    )
    assert status == 400


def test_oversized_body_rejected(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    status, _payload = _request_json(
        "{}/api/tasks/submit".format(base_url),
        method="POST",
        token=TOKEN,
        payload={
            "user": "alice",
            "flowPath": _existing_flow_path(),
            "pad": "x" * (1024 * 1024 + 16),
        },
    )
    assert status == 413


def test_invalid_content_length_rejected(task_server):
    _server, base_url, _db, _log_dir, _report_dir = task_server
    host, port_text = base_url.split("://")[1].split(":")
    conn = http.client.HTTPConnection(host, int(port_text), timeout=3)
    try:
        conn.putrequest("POST", "/api/tasks/submit")
        conn.putheader("Content-Length", "not-a-number")
        conn.putheader("Authorization", "Bearer " + TOKEN)
        conn.putheader("Accept", "application/json")
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        assert response.status == 400
    finally:
        conn.close()


def test_submit_rejects_invalid_flow_json(tmp_path):
    flow_dir = tmp_path / "flows"
    flow_dir.mkdir()
    bad_flow = flow_dir / "bad.json"
    bad_flow.write_text("{not valid json", encoding="utf-8")

    server = create_server(
        "127.0.0.1",
        0,
        auth_token=TOKEN,
        queue_db=str(tmp_path / "queue.db"),
        worker_launcher=lambda task, task_log_dir=None, **kwargs: (FakeProcess(), None),
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
        status, _payload = _request_json(
            "{}/api/tasks/submit".format(base_url),
            method="POST",
            token=TOKEN,
            payload={"user": "alice", "flowPath": str(bad_flow)},
        )
        assert status == 400
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def _upload_server(tmp_path):
    flow_dir = tmp_path / "flows"
    flow_dir.mkdir()
    server = create_server(
        "127.0.0.1",
        0,
        auth_token=TOKEN,
        queue_db=str(tmp_path / "queue.db"),
        worker_launcher=lambda task, task_log_dir=None, **kwargs: (FakeProcess(), None),
        task_log_dir=str(tmp_path / "tasks"),
        report_dir=str(tmp_path / "reports"),
        flow_dir=str(flow_dir),
        server_log_path=str(tmp_path / "server.log"),
    )
    server._scheduler_stop.set()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = "http://127.0.0.1:{}".format(server.server_address[1])
    return server, base_url, flow_dir, thread


def test_flow_upload_success(tmp_path):
    server, base_url, flow_dir, thread = _upload_server(tmp_path)
    try:
        content = {"name": "上传测试", "steps": [{"id": "step1"}]}
        status, payload = _request_json(
            "{}/api/flows/upload".format(base_url),
            method="POST",
            token=TOKEN,
            payload={"name": "remote_flow.json", "content": content},
        )
        assert status == 201
        saved = Path(payload["flowPath"])
        assert saved.parent == flow_dir
        assert saved.is_file()
        assert json.loads(saved.read_text(encoding="utf-8")) == content
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_flow_upload_sanitizes_name(tmp_path):
    server, base_url, flow_dir, thread = _upload_server(tmp_path)
    try:
        status, payload = _request_json(
            "{}/api/flows/upload".format(base_url),
            method="POST",
            token=TOKEN,
            payload={
                "name": "..\\..\\evil/我的流程.json",
                "content": {"steps": []},
            },
        )
        assert status == 201
        saved = Path(payload["flowPath"])
        assert saved.parent == flow_dir
        assert ".." not in saved.name
        assert "/" not in saved.name
        assert "\\" not in saved.name
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_flow_upload_rejects_bad_content(tmp_path):
    server, base_url, _flow_dir, thread = _upload_server(tmp_path)
    try:
        status, _payload = _request_json(
            "{}/api/flows/upload".format(base_url),
            method="POST",
            token=TOKEN,
            payload={"name": "bad.json", "content": ["not", "a", "dict"]},
        )
        assert status == 400
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_flow_upload_requires_auth(tmp_path):
    server, base_url, _flow_dir, thread = _upload_server(tmp_path)
    try:
        status, _payload = _request_json(
            "{}/api/flows/upload".format(base_url),
            method="POST",
            payload={"name": "no_auth.json", "content": {"steps": []}},
        )
        assert status == 401
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

def test_flow_upload_rejects_bad_known_fields(tmp_path):
    server, base_url, _flow_dir, thread = _upload_server(tmp_path)
    try:
        for content in ({"steps": "not-a-list"}, {"flowPackages": {}}):
            status, _payload = _request_json(
                "{}/api/flows/upload".format(base_url),
                method="POST",
                token=TOKEN,
                payload={"name": "bad.json", "content": content},
            )
            assert status == 400
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()

def test_flow_upload_rejects_reserved_names(tmp_path):
    server, base_url, flow_dir, thread = _upload_server(tmp_path)
    try:
        registry = flow_dir / "flow_package_registry.json"
        registry.write_text('{"original": true}', encoding="utf-8")
        backup = flow_dir / "old_flow.json.bak.json"
        backup.write_text('{"original": true}', encoding="utf-8")
        for name in ("flow_package_registry.json", "old_flow.json.bak.json"):
            status, _payload = _request_json(
                "{}/api/flows/upload".format(base_url),
                method="POST",
                token=TOKEN,
                payload={"name": name, "content": {"steps": [{"id": "x"}]}},
            )
            assert status == 400
        assert json.loads(registry.read_text(encoding="utf-8")) == {"original": True}
        assert json.loads(backup.read_text(encoding="utf-8")) == {"original": True}
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def _upload_flow(base_url, name, content, user="alice"):
    return _request_json(
        "{}/api/flows/upload".format(base_url),
        method="POST",
        token=TOKEN,
        payload={"name": name, "content": content, "user": user},
    )


def test_flow_upload_creates_version_history(tmp_path):
    server, base_url, flow_dir, thread = _upload_server(tmp_path)
    try:
        name = "versioned_flow.json"
        first = {"name": "v1", "steps": [{"id": "s1"}]}
        second = {"name": "v2", "steps": [{"id": "s2"}]}
        status, payload = _upload_flow(base_url, name, first)
        assert status == 201
        assert payload["version"] == 1
        status, payload = _upload_flow(base_url, name, second)
        assert status == 201
        assert payload["version"] == 2
        canonical = flow_dir / name
        archive = flow_dir / "versioned_flow.v1.json"
        assert json.loads(canonical.read_text(encoding="utf-8")) == second
        assert json.loads(archive.read_text(encoding="utf-8")) == first
        ledger = json.loads(
            (flow_dir / ".flow_versions.json").read_text(encoding="utf-8")
        )
        assert [v["version"] for v in ledger[name]["versions"]] == [1, 2]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_flow_list_excludes_archives_and_reports_versions(tmp_path):
    server, base_url, flow_dir, thread = _upload_server(tmp_path)
    try:
        name = "versioned_flow.json"
        _upload_flow(base_url, name, {"name": "v1", "steps": [{"id": "s1"}]})
        _upload_flow(base_url, name, {"name": "v2", "steps": [{"id": "s2"}]})
        status, payload = _request_json(
            "{}/api/flows".format(base_url),
            token=TOKEN,
        )
        assert status == 200
        names = [item["name"] for item in payload["flows"]]
        assert name in names
        assert "versioned_flow.v1.json" not in names
        flow = next(item for item in payload["flows"] if item["name"] == name)
        assert flow["versionCount"] == 2
        assert [v["version"] for v in flow["versions"]] == [1, 2]
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_flow_version_read(tmp_path):
    server, base_url, flow_dir, thread = _upload_server(tmp_path)
    try:
        name = "versioned_flow.json"
        first = {"name": "v1", "steps": [{"id": "s1"}]}
        second = {"name": "v2", "steps": [{"id": "s2"}]}
        _upload_flow(base_url, name, first)
        _upload_flow(base_url, name, second)
        status, payload = _request_json(
            "{}/api/flows/version?name={}&version=1".format(base_url, name),
            token=TOKEN,
        )
        assert status == 200
        assert payload["version"] == 1
        assert payload["content"] == first
        status, payload = _request_json(
            "{}/api/flows/version?name={}&version=2".format(base_url, name),
            token=TOKEN,
        )
        assert status == 200
        assert payload["content"] == second
        status, _payload = _request_json(
            "{}/api/flows/version?name={}&version=9".format(base_url, name),
            token=TOKEN,
        )
        assert status == 404
        status, _payload = _request_json(
            "{}/api/flows/version?name={}&version=0".format(base_url, name),
            token=TOKEN,
        )
        assert status == 400
        status, _payload = _request_json(
            "{}/api/flows/version?name=missing.json&version=1".format(base_url),
            token=TOKEN,
        )
        assert status == 404
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
