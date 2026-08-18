# encoding: utf-8

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_cli_requires_auth_token():
    proc = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "wt_task_server.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(_free_port()),
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.returncode != 0
    assert "auth-token" in proc.stderr


def test_cli_starts_and_health(tmp_path):
    port = _free_port()
    db = str(tmp_path / "queue.db")
    proc = subprocess.Popen(
        [
            sys.executable,
            str(PROJECT_ROOT / "wt_task_server.py"),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--auth-token",
            "secret-token",
            "--db",
            db,
        ],
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        url = "http://127.0.0.1:{}/api/health".format(port)
        payload = None
        deadline = time.time() + 10
        while time.time() < deadline:
            if proc.poll() is not None:
                out, err = proc.communicate(timeout=5)
                raise AssertionError(
                    "server exited early: rc={} out={!r} err={!r}".format(
                        proc.returncode, out, err
                    )
                )
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                    break
            except Exception:
                time.sleep(0.2)
        assert payload is not None, "health endpoint did not respond in time"
        assert payload["ok"] is True
        assert payload["service"] == "wt_task_server"
        assert Path(db).exists()
    finally:
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.communicate(timeout=5)
        except Exception:
            proc.kill()


def test_default_worker_launcher_builds_expected_command(tmp_path):
    sys.path.insert(0, str(PROJECT_ROOT))
    from wt_task_server import AUTOMATION_SCRIPT, default_worker_launcher

    task = {
        "taskId": "task_123",
        "user": "alice",
        "flowPath": "C:/flows/a.json",
        "stepsRequested": ["s1", "s2"],
        "fromStep": "",
        "toStep": "s2",
        "resumeFromStep": "",
    }
    log_dir = str(tmp_path / "tasks")
    db_path = str(tmp_path / "queue.db")
    with mock.patch("wt_task_server.subprocess.Popen") as popen_cls:
        proc = mock.Mock()
        popen_cls.return_value = proc
        launched_proc, log_file = default_worker_launcher(
            task, task_log_dir=log_dir, queue_db=db_path
        )
        log_file.close()
    assert launched_proc is proc
    cmd = popen_cls.call_args[0][0]
    assert cmd[0] == sys.executable
    assert os.path.normcase(cmd[1]) == os.path.normcase(AUTOMATION_SCRIPT)
    assert "--no-monitor" in cmd
    assert cmd[cmd.index("--task-id") + 1] == "task_123"
    assert cmd[cmd.index("--task-user") + 1] == "alice"
    assert cmd[cmd.index("--task-db") + 1] == db_path
    assert cmd[cmd.index("--steps") + 1] == "s1,s2"
    assert cmd[cmd.index("--to-step") + 1] == "s2"
    assert "--skip-setup" not in cmd


def test_automation_cli_flags_match_launcher():
    source = (PROJECT_ROOT / "WT_AUT_recorded.py").read_text(encoding="utf-8")
    for flag in (
        "--no-monitor",
        "--task-id",
        "--task-user",
        "--task-db",
        "--steps",
        "--from-step",
        "--to-step",
        "--skip-setup",
    ):
        assert flag in source
