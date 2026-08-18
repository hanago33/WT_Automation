# encoding: utf-8

import json
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import WT_Launcher
import wt_flow_executor
import wt_flow_locator
import wt_projection_helpers
import wt_task_server


def test_debug_events_disabled_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("WT_DEBUG_EVENTS", raising=False)
    monkeypatch.setattr(wt_flow_locator, "_DEBUG_EVENTS_DIR", str(tmp_path))
    wt_flow_locator._emit_debug_event("h1", "loc", "must not be written")
    assert list(tmp_path.iterdir()) == []

    monkeypatch.setenv("WT_DEBUG_EVENTS", "1")
    wt_flow_locator._emit_debug_event("h1", "loc", "must be written")
    ndjson_files = list(tmp_path.glob("*.ndjson"))
    assert len(ndjson_files) == 1
    payload = json.loads(ndjson_files[0].read_text(encoding="utf-8").strip())
    assert payload["msg"] == "must be written"


def test_silent_exception_counts_appear_in_failure_log(monkeypatch):
    captured = []
    monkeypatch.setattr(wt_flow_locator, "_LOG_STEP", captured.append)
    monkeypatch.setattr(
        wt_flow_locator,
        "_GET_STEP_DEFINITION",
        lambda step_id: {
            "id": step_id,
            "controls": [{"id": "c1", "targetMethod": "automation_id", "targetValue": "missing"}],
        },
    )
    monkeypatch.setattr(
        wt_flow_locator,
        "_snapshot_silent_exception_counts",
        lambda: {"uia_findall": 2},
    )

    wt_flow_locator.find_flow_control("step_missing", "c1", timeout_seconds=0)

    failure_lines = [line for line in captured if "流程控件定位失败" in line]
    assert failure_lines
    assert 'silent_exception_counts={"uia_findall": 2}' in failure_lines[-1]


def test_record_silent_exception_accumulates():
    wt_flow_locator._reset_silent_exception_counts()
    wt_flow_locator._record_silent_exception("phase_a", ValueError("x"))
    wt_flow_locator._record_silent_exception("phase_a")
    wt_flow_locator._record_silent_exception("phase_b")
    assert wt_flow_locator._snapshot_silent_exception_counts() == {"phase_a": 2, "phase_b": 1}
    wt_flow_locator._reset_silent_exception_counts()
    assert wt_flow_locator._snapshot_silent_exception_counts() == {}


def test_run_ui_tars_timeout_writes_unique_logs(monkeypatch, tmp_path):
    runner = tmp_path / "ui_tars_runner.js"
    runner.write_text("module.exports = {}", encoding="utf-8")
    monkeypatch.setattr(wt_projection_helpers, "_GET_UI_TARS_RUNNER", lambda: str(runner))
    monkeypatch.setattr(wt_projection_helpers, "__file__", str(tmp_path / "wt_projection_helpers.py"))
    monkeypatch.setenv("VOLC_API_KEY", "test-key")
    monkeypatch.setenv("WT_UI_TARS_TIMEOUT_SECONDS", "5")

    calls = []

    def fake_run(*args, **kwargs):
        calls.append(kwargs.get("timeout"))
        raise subprocess.TimeoutExpired(
            cmd=kwargs.get("args") or args[0],
            timeout=kwargs["timeout"],
            output="partial stdout",
            stderr="partial stderr",
        )

    monkeypatch.setattr(wt_projection_helpers.subprocess, "run", fake_run)

    for _ in range(2):
        with pytest.raises(RuntimeError) as exc_info:
            wt_projection_helpers.run_ui_tars("click ok", step_name="投影/配置 步骤")
        assert "超时" in str(exc_info.value)

    assert calls == [5, 5]
    stdout_logs = sorted(tmp_path.glob("ui_tars_*_stdout.log"))
    stderr_logs = sorted(tmp_path.glob("ui_tars_*_stderr.log"))
    assert len(stdout_logs) == 2
    assert len(stderr_logs) == 2
    assert len({path.name for path in stdout_logs + stderr_logs}) == 4
    assert "partial stdout" in stdout_logs[0].read_text(encoding="utf-8")
    assert "partial stderr" in stderr_logs[0].read_text(encoding="utf-8")
    assert "投影" in stdout_logs[0].name


def test_kill_process_taskkill_timeout(monkeypatch):
    class FakeProc:
        pid = 424242

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout"))

    handler = object.__new__(wt_task_server.TaskQueueHandler)
    monkeypatch.setattr(wt_task_server.subprocess, "run", fake_run)
    wt_task_server.TaskServer._kill_process(handler, FakeProc())

    assert len(calls) == 1
    assert calls[0][0] == ["taskkill", "/F", "/T", "/PID", "424242"]
    assert calls[0][1]["timeout"] == 10


def test_save_json_file_atomic_no_tmp_leftover(tmp_path):
    target = tmp_path / "launcher_state.json"
    WT_Launcher.save_json_file(str(target), {"mode": "simple"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"mode": "simple"}
    assert list(tmp_path.glob("*.tmp")) == []


def test_write_feedback_to_flow_atomic_no_tmp_leftover(tmp_path):
    flow_path = tmp_path / "flow.json"
    flow_path.write_text(json.dumps({"steps": [], "feedbackHistory": []}), encoding="utf-8")
    wt_flow_executor.configure_flow_executor(log_step=lambda message: None)
    wt_flow_executor._write_feedback_to_flow(
        {"flowDefinitionPath": str(flow_path)}, "s1", {"type": "atomic"}
    )
    saved = json.loads(flow_path.read_text(encoding="utf-8"))
    assert [entry["type"] for entry in saved["feedbackHistory"]] == ["atomic"]
    assert list(tmp_path.glob("*.tmp")) == []


TOKEN = "round2-token"


class _FakeProcess:
    pid = 1
    returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.returncode = 1


def _post_json(url, payload, token):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
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


def test_flow_upload_atomic_no_tmp_leftover(tmp_path):
    flow_dir = tmp_path / "flows"
    flow_dir.mkdir()
    server = wt_task_server.create_server(
        "127.0.0.1",
        0,
        auth_token=TOKEN,
        queue_db=str(tmp_path / "queue.db"),
        worker_launcher=lambda task, task_log_dir=None, **kwargs: (_FakeProcess(), None),
        task_log_dir=str(tmp_path / "tasks"),
        report_dir=str(tmp_path / "reports"),
        flow_dir=str(flow_dir),
        server_log_path=str(tmp_path / "server.log"),
    )
    server._scheduler_stop.set()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = "http://127.0.0.1:{}".format(server.server_address[1])
        status, payload = _post_json(
            "{}/api/flows/upload".format(base_url),
            {"name": "remote_flow.json", "content": {"steps": [{"id": "s1"}]}},
            TOKEN,
        )
        assert status == 201
        saved = json.loads(Path(payload["flowPath"]).read_text(encoding="utf-8"))
        assert saved["steps"][0]["id"] == "s1"
        assert list(flow_dir.glob("*.tmp")) == []
    finally:
        server.shutdown()
        thread.join(timeout=3)
        server.server_close()
