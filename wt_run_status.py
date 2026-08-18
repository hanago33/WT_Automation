# encoding: utf-8

import json
import os
import tempfile
from datetime import datetime


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(BASE_DIR, "logs", "run_status.json")
DEFAULT_SOURCE = "wt_run_status"


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def default_status():
    """Return the fallback status used when the status file is unavailable."""
    return {
        "status": "idle",
        "isRunning": False,
        "activity": "等待自动化任务",
        "lastLog": "",
        "error": "",
        "runId": "",
        "startedAt": None,
        "endedAt": None,
        "updatedAt": _now_iso(),
        "source": DEFAULT_SOURCE,
    }


def load_status():
    """Read the current run status, falling back to an idle payload."""
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return default_status()


def publish(
    status=None,
    activity=None,
    last_log=None,
    error=None,
    run_id=None,
    source=DEFAULT_SOURCE,
):
    """Atomically publish the current automation run status."""
    previous = load_status()
    now = _now_iso()
    payload = dict(previous)

    if status is not None:
        payload["status"] = status
        payload["isRunning"] = status == "running"
        if status == "running":
            payload["startedAt"] = now
            payload["endedAt"] = None
            payload["error"] = ""
        elif status in ("success", "failed", "idle"):
            payload["endedAt"] = now
            if status != "failed":
                payload["error"] = ""

    if activity is not None:
        payload["activity"] = activity
    if last_log is not None:
        payload["lastLog"] = last_log
    if error is not None:
        payload["error"] = error
    if run_id is not None:
        payload["runId"] = run_id
    if source is not None:
        payload["source"] = source

    payload["updatedAt"] = now
    os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=os.path.dirname(STATUS_FILE),
            prefix="run_status.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            tmp_path = tmp.name
            json.dump(payload, tmp, ensure_ascii=False, indent=2)
        os.replace(tmp_path, STATUS_FILE)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
    return payload
