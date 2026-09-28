# encoding: utf-8

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

from wt_server_monitor import create_server
import wt_run_status


def _get_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


@pytest.fixture
def monitor(tmp_path):
    status_file = tmp_path / "run_status.json"
    log_file = tmp_path / "wt_automation.log"
    report_file = tmp_path / "last_run_report.json"

    status_file.write_text(
        json.dumps(
            {
                "status": "running",
                "isRunning": True,
                "activity": "processing",
                "lastLog": "[2026-08-10 12:00:00] working",
                "error": "",
                "runId": "run-001",
                "startedAt": "2026-08-10T12:00:00",
                "endedAt": None,
                "updatedAt": "2026-08-10T12:00:01",
                "source": "WT_AUT_recorded",
            }
        ),
        encoding="utf-8",
    )
    log_file.write_text(
        "\n".join(f"line-{index}" for index in range(10)),
        encoding="utf-8",
    )
    report_file.write_text(
        json.dumps({"runId": "run-001", "status": "success"}),
        encoding="utf-8",
    )

    server = create_server(
        "127.0.0.1",
        0,
        status_file=str(status_file),
        log_file=str(log_file),
        report_file=str(report_file),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    yield server, base_url
    server.shutdown()
    thread.join(timeout=3)
    server.server_close()


def test_health(monitor):
    _server, base_url = monitor
    payload = _get_json(f"{base_url}/api/health")
    assert payload["ok"] is True
    assert payload["service"] == "wt_server_monitor"


def test_status(monitor):
    _server, base_url = monitor
    payload = _get_json(f"{base_url}/api/status")
    assert payload["status"] == "running"
    assert payload["isRunning"] is True
    assert payload["activity"] == "processing"
    assert payload["runId"] == "run-001"


def test_logs_tail(monitor):
    _server, base_url = monitor
    payload = _get_json(f"{base_url}/api/logs?tail=3")
    assert payload["totalLines"] == 10
    assert payload["lines"] == ["line-7", "line-8", "line-9"]


def test_report(monitor):
    _server, base_url = monitor
    payload = _get_json(f"{base_url}/api/report")
    assert payload["status"] == "success"
    assert payload["runId"] == "run-001"


def test_non_get_methods_rejected(monitor):
    _server, base_url = monitor
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        request = urllib.request.Request(
            f"{base_url}/api/status",
            method=method,
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=3)
        assert exc_info.value.code == 405


def test_no_control_endpoints_are_defined():
    source = (PROJECT_ROOT / "wt_server_monitor.py").read_text(encoding="utf-8")
    for marker in ("/api/stop", "/api/start", "/api/queue"):
        assert marker not in source


def test_status_default_when_file_missing(tmp_path):
    server = create_server(
        "127.0.0.1",
        0,
        status_file=str(tmp_path / "missing_status.json"),
        log_file=str(tmp_path / "missing.log"),
        report_file=str(tmp_path / "missing_report.json"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        payload = _get_json(f"{base_url}/api/status")
        assert payload["status"] == "idle"
        assert payload["isRunning"] is False
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_report_missing_returns_404(tmp_path):
    server = create_server(
        "127.0.0.1",
        0,
        status_file=str(tmp_path / "missing_status.json"),
        log_file=str(tmp_path / "missing.log"),
        report_file=str(tmp_path / "missing_report.json"),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _get_json(f"{base_url}/api/report")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()


def test_run_status_publish_and_load(tmp_path, monkeypatch):
    status_path = tmp_path / "run_status.json"
    monkeypatch.setattr(wt_run_status, "STATUS_FILE", str(status_path))

    wt_run_status.publish(
        status="running",
        activity="started",
        last_log="flow started",
        run_id="run-001",
    )
    payload = wt_run_status.load_status()
    assert payload["status"] == "running"
    assert payload["isRunning"] is True
    assert payload["activity"] == "started"
    assert payload["runId"] == "run-001"

    wt_run_status.publish(status="success", activity="finished")
    payload = wt_run_status.load_status()
    assert payload["status"] == "success"
    assert payload["isRunning"] is False
    assert payload["endedAt"] is not None
