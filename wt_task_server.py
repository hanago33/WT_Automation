# encoding: utf-8

"""HTTP task queue service for the WT automation host.

The service exposes authenticated queue endpoints to local clients and starts
WT_AUT_recorded.py as a worker subprocess. Only the standard library is used.
"""

import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import wt_task_queue


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AUTOMATION_SCRIPT = os.path.join(BASE_DIR, "WT_AUT_recorded.py")
DEFAULT_DB_PATH = wt_task_queue.DEFAULT_DB_PATH
DEFAULT_TASK_LOG_DIR = wt_task_queue.DEFAULT_TASK_LOG_DIR
DEFAULT_REPORT_DIR = os.path.join(BASE_DIR, "logs", "run_reports")
DEFAULT_PORT = 8768
DEFAULT_SERVER_LOG = os.path.join(BASE_DIR, "logs", "task_server.log")
MAX_BODY_BYTES = 1024 * 1024
DEFAULT_WEBHOOK = ""
DEFAULT_LOG_RETENTION_DAYS = 30
FLOW_VERSION_LEDGER = ".flow_versions.json"
_FLOW_VERSION_LOCK = threading.Lock()
STATUS_PENDING = wt_task_queue.STATUS_PENDING
STATUS_RUNNING = wt_task_queue.STATUS_RUNNING
STATUS_FAILED = wt_task_queue.STATUS_FAILED


def _read_json_file(file_path, default=None):
    try:
        with open(file_path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return default


def _safe_flow_name(name):
    """把上传文件名清洗成安全、可直接写入 flow_packages 的名称。"""
    raw = os.path.basename(str(name or "").strip())
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_ .")
    cleaned = cleaned.strip(" .")
    if not cleaned or cleaned in (".", ".."):
        return ""
    if len(cleaned) > 120:
        cleaned = cleaned[:120]
    if not cleaned.lower().endswith(".json"):
        cleaned += ".json"
    return cleaned


def _load_flow_version_ledger(flow_dir):
    ledger = os.path.join(flow_dir, FLOW_VERSION_LEDGER)
    data = _read_json_file(ledger, {})
    if not isinstance(data, dict):
        return {}
    return data


def _save_flow_version_ledger(flow_dir, ledger):
    target = os.path.join(flow_dir, FLOW_VERSION_LEDGER)
    tmp_path = target + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as file_obj:
        json.dump(ledger, file_obj, ensure_ascii=False, indent=2)
    os.replace(tmp_path, target)


def _version_file_name(name, number):
    stem = name[:-5] if name.lower().endswith(".json") else name
    return "{}.v{}.json".format(stem, number)


def _sha256_text(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _coerce_int_field(payload, name, default, min_value=None):
    value = payload.get(name, default)
    if value is None or (isinstance(value, str) and not value.strip()):
        return default, None
    if isinstance(value, bool):
        return None, "{} must be an integer".format(name)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, "{} must be an integer".format(name)
    if min_value is not None and number < min_value:
        return None, "{} must be >= {}".format(name, min_value)
    return number, None


def _normalize_scheduled_at(value):
    if value is None or value == "":
        return "", None
    if not isinstance(value, str):
        return None, "scheduledAt must be an ISO datetime string"
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None, "scheduledAt must be an ISO datetime string"
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.isoformat(timespec="seconds"), None


def _validate_notify_url(value):
    if value is None or value == "":
        return "", None
    if not isinstance(value, str):
        return None, "notifyUrl must be a URL string"
    value = value.strip()
    if len(value) > 500:
        return None, "notifyUrl too long"
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None, "notifyUrl must be an http(s) URL"
    return value, None


def log_server_event(message, log_path=DEFAULT_SERVER_LOG):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as file_obj:
            file_obj.write(
                "[{}] {}\n".format(
                    datetime.now().isoformat(timespec="seconds"),
                    message,
                )
            )
    except OSError:
        pass


def default_worker_launcher(
    task,
    task_log_dir=DEFAULT_TASK_LOG_DIR,
    queue_db=DEFAULT_DB_PATH,
):
    cmd = [
        sys.executable,
        AUTOMATION_SCRIPT,
        "--no-monitor",
        "--task-id",
        task["taskId"],
        "--task-user",
        task["user"],
        "--task-db",
        queue_db,
    ]
    steps = task.get("stepsRequested") or []
    if steps:
        cmd += ["--steps", ",".join(str(step_id) for step_id in steps)]
    from_step = task.get("resumeFromStep") or task.get("fromStep") or ""
    if from_step:
        cmd += ["--from-step", from_step]
    if task.get("toStep"):
        cmd += ["--to-step", task["toStep"]]
    if task.get("resumeFromStep"):
        cmd += ["--skip-setup"]

    os.makedirs(task_log_dir, exist_ok=True)
    log_path = wt_task_queue.task_log_path(task["taskId"], task_log_dir)
    log_file = open(log_path, "a", encoding="utf-8")
    log_file.write(
        "[queue] starting task {} user={} flow={}\n".format(
            task["taskId"],
            task["user"],
            task.get("flowPath", ""),
        )
    )
    log_file.flush()
    proc = subprocess.Popen(
        cmd,
        cwd=BASE_DIR,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc, log_file


class TaskQueueHandler(BaseHTTPRequestHandler):
    server_version = "WTTaskQueue/1.0"

    def log_message(self, format, *args):
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({"error": message}, status=status)

    def _method_not_allowed(self):
        self._drain_body_if_any()
        self._send_json(
            {"error": "method not allowed", "allowed": ["GET", "POST", "OPTIONS"]},
            status=405,
        )

    def _check_auth(self):
        expected = getattr(self.server, "auth_token", "") or ""
        if not expected:
            self._send_unauthorized()
            return False
        header = self.headers.get("Authorization", "")
        token = header[len("Bearer ") :] if header.startswith("Bearer ") else ""
        if token and secrets.compare_digest(token.strip(), expected):
            return True
        self._send_unauthorized()
        return False

    def _drain_body_if_any(self):
        """排空未读请求体，避免关闭连接时因残留数据触发 RST。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if 0 < length <= MAX_BODY_BYTES:
            self._drain_body(length)

    def _send_unauthorized(self):
        self._drain_body_if_any()
        self._send_error(401, "unauthorized")

    def _drain_body(self, length):
        """读取并丢弃请求体，避免返回错误时客户端仍在发送数据。"""
        remaining = length
        chunk = 64 * 1024
        try:
            while remaining > 0:
                size = min(chunk, remaining)
                if not self.rfile.read(size):
                    break
                remaining -= size
        except Exception:
            pass

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return None, "invalid Content-Length"
        if length < 0:
            return None, "invalid Content-Length"
        if length > MAX_BODY_BYTES:
            self._drain_body(length)
            return None, (413, "request body too large")
        if length == 0:
            return {}, None
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None, "invalid JSON body"
        if not isinstance(payload, dict):
            return None, "JSON body must be an object"
        return payload, None

    def _list_flows(self):
        flow_dir = os.path.abspath(
            getattr(self.server, "flow_dir", os.path.join(BASE_DIR, "flow_packages"))
        )
        flows = []
        ledger = _load_flow_version_ledger(flow_dir)
        archive_files = set()
        for flow_name, flow_entry in ledger.items():
            if not isinstance(flow_entry, dict):
                continue
            for version in flow_entry.get("versions") or []:
                archive_name = str(version.get("file") or "")
                if archive_name and archive_name != flow_name:
                    archive_files.add(archive_name)
        if os.path.isdir(flow_dir):
            for name in sorted(os.listdir(flow_dir)):
                if not name.endswith(".json"):
                    continue
                lowered = name.lower()
                if name == "flow_package_registry.json" or ".bak" in lowered:
                    continue
                if name in archive_files:
                    continue
                versions = []
                for version in (ledger.get(name) or {}).get("versions") or []:
                    versions.append(
                        {
                            "version": version.get("version"),
                            "file": os.path.join(flow_dir, version.get("file", "")),
                            "user": version.get("user", ""),
                            "uploadedAt": version.get("uploadedAt", ""),
                            "sha256": version.get("sha256", ""),
                        }
                    )
                flows.append(
                    {
                        "path": os.path.join(flow_dir, name),
                        "name": name,
                        "versions": versions,
                        "versionCount": len(versions),
                    }
                )
        return {"flows": flows}

    def _handle_flow_upload(self):
        payload, error = self._read_json_body()
        if error:
            if isinstance(error, tuple):
                self._send_error(error[0], error[1])
            else:
                self._send_error(400, error)
            return
        name = _safe_flow_name(payload.get("name"))
        content = payload.get("content")
        if not name:
            self._send_error(400, "invalid flow file name")
            return
        lowered = name.lower()
        if lowered == "flow_package_registry.json" or ".bak" in lowered:
            self._send_error(400, "reserved flow file name")
            return
        if not isinstance(content, dict):
            self._send_error(400, "flow content must be a JSON object")
            return
        if "steps" in content and not isinstance(content["steps"], list):
            self._send_error(400, "flow content steps must be a list")
            return
        if "flowPackages" in content and not isinstance(content["flowPackages"], list):
            self._send_error(400, "flow content flowPackages must be a list")
            return
        flow_dir = os.path.abspath(
            getattr(self.server, "flow_dir", os.path.join(BASE_DIR, "flow_packages"))
        )
        abs_flow = os.path.abspath(os.path.join(flow_dir, name))
        try:
            inside = os.path.commonpath([flow_dir, abs_flow]) == flow_dir
        except ValueError:
            inside = False
        if not inside:
            self._send_error(400, "invalid flow file name")
            return
        serialized = json.dumps(content, ensure_ascii=False, indent=2)
        digest = _sha256_text(serialized)
        uploaded_at = datetime.now().isoformat(timespec="seconds")
        upload_user = str(payload.get("user") or "").strip()
        with _FLOW_VERSION_LOCK:
            try:
                os.makedirs(flow_dir, exist_ok=True)
                ledger = _load_flow_version_ledger(flow_dir)
                entry = ledger.get(name)
                if not isinstance(entry, dict):
                    entry = {"versions": []}
                    ledger[name] = entry
                old_serialized = None
                try:
                    with open(abs_flow, "r", encoding="utf-8") as file_obj:
                        old_serialized = file_obj.read()
                except OSError:
                    old_serialized = None
                existing_versions = entry.get("versions") or []
                version = 1
                if existing_versions:
                    version = max(
                        (int(item.get("version") or 0) for item in existing_versions),
                        default=0,
                    ) + 1
                if old_serialized is not None:
                    archive_number = max(version - 1, 1)
                    archive_name = _version_file_name(name, archive_number)
                    while os.path.exists(os.path.join(flow_dir, archive_name)):
                        archive_number += 1
                        archive_name = _version_file_name(name, archive_number)
                    with open(os.path.join(flow_dir, archive_name), "w", encoding="utf-8") as file_obj:
                        file_obj.write(old_serialized)
                    replaced = False
                    canonical_file = os.path.basename(abs_flow)
                    for record in reversed(existing_versions):
                        if (
                            int(record.get("version") or 0) == archive_number
                            and record.get("file") == canonical_file
                        ):
                            record["file"] = archive_name
                            record["sha256"] = _sha256_text(old_serialized)
                            replaced = True
                            break
                    if not replaced:
                        for record in reversed(existing_versions):
                            if record.get("file") == canonical_file:
                                record["file"] = archive_name
                                record["sha256"] = _sha256_text(old_serialized)
                                replaced = True
                                break
                    if not replaced:
                        existing_versions.append(
                            {
                                "version": archive_number,
                                "file": archive_name,
                                "user": entry.get("lastUser", ""),
                                "uploadedAt": entry.get("lastUploadedAt", ""),
                                "sha256": _sha256_text(old_serialized),
                            }
                        )
                tmp_flow = abs_flow + ".tmp"
                try:
                    with open(tmp_flow, "w", encoding="utf-8") as file_obj:
                        file_obj.write(serialized)
                    os.replace(tmp_flow, abs_flow)
                finally:
                    if os.path.exists(tmp_flow):
                        try:
                            os.remove(tmp_flow)
                        except OSError:
                            pass
                existing_versions.append(
                    {
                        "version": version,
                        "file": os.path.basename(abs_flow),
                        "user": upload_user,
                        "uploadedAt": uploaded_at,
                        "sha256": digest,
                    }
                )
                entry["versions"] = existing_versions
                entry["lastUser"] = upload_user
                entry["lastUploadedAt"] = uploaded_at
                entry["currentVersion"] = version
                _save_flow_version_ledger(flow_dir, ledger)
            except OSError as exc:
                self._send_error(500, "failed to save flow: {}".format(exc))
                return
        wt_task_queue.add_audit_event(
            user=upload_user,
            source_ip=self.client_address[0] if self.client_address else "",
            action="flow_upload",
            result="ok",
            detail="name={} version={}".format(name, version),
            db_path=getattr(self.server, "queue_db", DEFAULT_DB_PATH),
        )
        self._send_json(
            {
                "flowPath": abs_flow,
                "name": name,
                "version": version,
                "uploadedAt": uploaded_at,
                "sha256": digest,
            },
            status=201,
        )

    def _handle_submit(self):
        payload, error = self._read_json_body()
        if error:
            if isinstance(error, tuple):
                self._send_error(error[0], error[1])
            else:
                self._send_error(400, error)
            return
        user = str(payload.get("user") or "").strip()
        flow_path = str(payload.get("flowPath") or "").strip()
        if not user or not flow_path:
            self._send_error(400, "user and flowPath are required")
            return
        flow_dir = os.path.abspath(
            getattr(self.server, "flow_dir", os.path.join(BASE_DIR, "flow_packages"))
        )
        abs_flow = os.path.abspath(flow_path)
        try:
            inside = os.path.commonpath([flow_dir, abs_flow]) == flow_dir
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(abs_flow):
            self._send_error(400, "flowPath must be a JSON flow inside flow_packages")
            return
        if _read_json_file(abs_flow) is None:
            self._send_error(400, "flowPath is not a valid JSON object")
            return
        if "steps" in payload:
            raw_steps = payload["steps"]
        else:
            raw_steps = []
        if isinstance(raw_steps, str):
            steps = [item.strip() for item in raw_steps.split(",") if item.strip()]
        elif isinstance(raw_steps, list):
            steps = []
            for item in raw_steps:
                if not isinstance(item, (str, int)):
                    self._send_error(400, "steps must contain only strings")
                    return
                value = str(item).strip()
                if value:
                    steps.append(value)
        else:
            self._send_error(400, "steps must be a comma string or an array")
            return
        priority, error = _coerce_int_field(payload, "priority", 0)
        if error:
            self._send_error(400, error)
            return
        max_attempts, error = _coerce_int_field(
            payload,
            "maxAttempts",
            1,
            min_value=1,
        )
        if error:
            self._send_error(400, error)
            return
        retry_delay_seconds, error = _coerce_int_field(
            payload,
            "retryDelaySeconds",
            0,
            min_value=0,
        )
        if error:
            self._send_error(400, error)
            return
        timeout_seconds, error = _coerce_int_field(
            payload,
            "timeoutSeconds",
            0,
            min_value=0,
        )
        if error:
            self._send_error(400, error)
            return
        scheduled_at, error = _normalize_scheduled_at(payload.get("scheduledAt"))
        if error:
            self._send_error(400, error)
            return
        notify_url, error = _validate_notify_url(payload.get("notifyUrl"))
        if error:
            self._send_error(400, error)
            return
        source_ip = self.client_address[0] if self.client_address else ""
        task = wt_task_queue.submit_task(
            user=user,
            flow_path=abs_flow,
            source_ip=source_ip,
            steps=steps,
            from_step=str(payload.get("fromStep") or "").strip(),
            to_step=str(payload.get("toStep") or "").strip(),
            priority=priority,
            scheduled_at=scheduled_at,
            max_attempts=max_attempts,
            retry_delay_seconds=retry_delay_seconds,
            timeout_seconds=timeout_seconds,
            notify_url=notify_url,
            db_path=getattr(self.server, "queue_db", DEFAULT_DB_PATH),
        )
        wt_task_queue.add_audit_event(
            user=user,
            source_ip=source_ip,
            action="submit",
            task_id=task["taskId"],
            result="ok",
            detail="flowPath={}".format(abs_flow),
            db_path=getattr(self.server, "queue_db", DEFAULT_DB_PATH),
        )
        self._send_json({"task": task}, status=201)

    def _route_get(self, path, query):
        if path == "/api/health":
            self._send_json(
                {
                    "ok": True,
                    "service": "wt_task_server",
                    "version": 1,
                    "uptimeSeconds": round(
                        time.time() - getattr(self.server, "_started_at", time.time()),
                        1,
                    ),
                    "queueDb": getattr(self.server, "queue_db", ""),
                    "runningCount": len(getattr(self.server, "_running", {})),
                    "logRetentionDays": getattr(
                        self.server, "log_retention_days", 30
                    ),
                }
            )
            return
        if not self._check_auth():
            return
        if path == "/api/queue/stats":
            self._send_json(
                wt_task_queue.get_queue_stats(
                    db_path=getattr(self.server, "queue_db", DEFAULT_DB_PATH)
                )
            )
            return
        if path == "/api/audit":
            task_id = str(query.get("taskId", [""])[0] or "").strip()
            user = str(query.get("user", [""])[0] or "").strip()
            try:
                limit = int(query.get("limit", ["200"])[0] or 200)
            except (TypeError, ValueError):
                self._send_error(400, "invalid limit")
                return
            limit = max(1, min(limit, 500))
            events = wt_task_queue.list_audit_events(
                task_id=task_id or None,
                user=user or None,
                limit=limit,
                db_path=getattr(self.server, "queue_db", DEFAULT_DB_PATH),
            )
            self._send_json({"events": events})
            return
        if path == "/api/flows":
            self._send_json(self._list_flows())
            return
        if path == "/api/flows/version":
            self._handle_flow_version_get(query)
            return
        if path == "/api/tasks":
            scope = str(query.get("scope", ["all"])[0] or "all").strip()
            user = str(query.get("user", [""])[0] or "").strip()
            try:
                limit = int(query.get("limit", ["200"])[0] or 200)
            except (TypeError, ValueError):
                self._send_error(400, "invalid limit")
                return
            limit = max(1, min(limit, 500))
            if scope not in ("all", "mine"):
                self._send_error(400, "scope must be all or mine")
                return
            if scope == "mine" and not user:
                self._send_error(400, "user is required when scope=mine")
                return
            tasks = wt_task_queue.list_tasks(
                user=user,
                scope=scope,
                limit=limit,
                db_path=getattr(self.server, "queue_db", DEFAULT_DB_PATH),
            )
            self._send_json({"tasks": tasks})
            return
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "api" and parts[1] == "tasks":
            task = wt_task_queue.get_task(
                parts[2],
                db_path=getattr(self.server, "queue_db", DEFAULT_DB_PATH),
            )
            if task is None:
                self._send_error(404, "task not found")
                return
            self._send_json({"task": task})
            return
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks":
            task_id = parts[2]
            sub_path = parts[3]
            if sub_path == "logs":
                try:
                    tail = int(query.get("tail", ["300"])[0])
                except (TypeError, ValueError):
                    self._send_error(400, "invalid tail")
                    return
                tail = max(1, min(tail, 2000))
                lines, total = wt_task_queue.read_task_log_tail(
                    task_id,
                    tail=tail,
                    log_dir=getattr(self.server, "task_log_dir", DEFAULT_TASK_LOG_DIR),
                )
                self._send_json({"lines": lines, "totalLines": total})
                return
            if sub_path == "report":
                task = wt_task_queue.get_task(
                    task_id,
                    db_path=getattr(self.server, "queue_db", DEFAULT_DB_PATH),
                )
                run_id = (task or {}).get("runId") or ""
                if not run_id:
                    self._send_error(404, "task report not found")
                    return
                report_path = os.path.join(
                    getattr(self.server, "report_dir", DEFAULT_REPORT_DIR),
                    "{}.json".format(run_id),
                )
                payload = _read_json_file(report_path)
                if payload is None:
                    self._send_error(404, "task report not found")
                    return
                self._send_json(payload)
                return
        self._send_error(404, "not found")

    def _handle_flow_version_get(self, query):
        name = str(query.get("name", [""])[0] or "").strip()
        try:
            version = int(query.get("version", [""])[0] or 0)
        except (TypeError, ValueError):
            self._send_error(400, "invalid version")
            return
        if not name or version < 1:
            self._send_error(400, "name and version are required")
            return
        flow_dir = os.path.abspath(
            getattr(self.server, "flow_dir", os.path.join(BASE_DIR, "flow_packages"))
        )
        ledger = _load_flow_version_ledger(flow_dir)
        version_record = None
        for item in (ledger.get(name) or {}).get("versions") or []:
            if int(item.get("version") or 0) == version:
                version_record = item
                break
        if version_record is None:
            self._send_error(404, "flow version not found")
            return
        version_path = os.path.abspath(os.path.join(flow_dir, str(version_record.get("file") or "")))
        try:
            inside = os.path.commonpath([flow_dir, version_path]) == flow_dir
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(version_path):
            self._send_error(404, "flow version file not found")
            return
        content = _read_json_file(version_path)
        if content is None:
            self._send_error(500, "flow version is not valid JSON")
            return
        self._send_json(
            {
                "name": name,
                "version": version,
                "file": os.path.basename(version_path),
                "content": content,
                "user": version_record.get("user", ""),
                "uploadedAt": version_record.get("uploadedAt", ""),
                "sha256": version_record.get("sha256", ""),
            }
        )

    def _handle_control(self, task_id, action):
        queue_db = getattr(self.server, "queue_db", DEFAULT_DB_PATH)
        try:
            if action == "pause":
                task = wt_task_queue.request_pause(task_id, db_path=queue_db)
            elif action == "resume":
                task = wt_task_queue.resume_task(task_id, db_path=queue_db)
            elif action == "terminate":
                task = wt_task_queue.request_terminate(task_id, db_path=queue_db)
            elif action == "cancel":
                task = wt_task_queue.cancel_task(task_id, db_path=queue_db)
            else:
                self._send_error(404, "not found")
                return
        except wt_task_queue.TaskStateError as exc:
            self._send_error(409, str(exc))
            return
        wt_task_queue.add_audit_event(
            user=(task or {}).get("user", ""),
            source_ip=self.client_address[0] if self.client_address else "",
            action=action,
            task_id=task_id,
            result="ok",
            detail="status={}".format((task or {}).get("status", "")),
            db_path=queue_db,
        )
        if action == "cancel":
            self.server._notify_webhook(task, "task.canceled")
        self._send_json({"task": task})

    def _route_post(self, path):
        if not self._check_auth():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = -1
        if length > MAX_BODY_BYTES:
            self._drain_body(length)
            self._send_error(413, "request body too large")
            return
        if path == "/api/flows/upload":
            self._handle_flow_upload()
            return
        if path == "/api/tasks/submit":
            self._handle_submit()
            return
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "tasks":
            self._handle_control(parts[2], parts[3])
            self._drain_body_if_any()
            return
        self._drain_body_if_any()
        self._send_error(404, "not found")

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        self._route_get(parsed.path, query)

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        self._route_post(parsed.path)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PUT(self):
        self._method_not_allowed()

    def do_DELETE(self):
        self._method_not_allowed()

    def do_PATCH(self):
        self._method_not_allowed()


class TaskServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address,
        auth_token="",
        queue_db=DEFAULT_DB_PATH,
        worker_launcher=None,
        task_log_dir=DEFAULT_TASK_LOG_DIR,
        report_dir=DEFAULT_REPORT_DIR,
        flow_dir=None,
        server_log_path=DEFAULT_SERVER_LOG,
        webhook_url=DEFAULT_WEBHOOK,
        log_retention_days=DEFAULT_LOG_RETENTION_DAYS,
    ):
        super().__init__(server_address, TaskQueueHandler)
        self.auth_token = auth_token
        self.queue_db = queue_db
        self.task_log_dir = task_log_dir
        self.report_dir = report_dir
        self.flow_dir = flow_dir or os.path.join(BASE_DIR, "flow_packages")
        self.server_log_path = server_log_path
        self.webhook_url = str(webhook_url or "").strip()
        self.log_retention_days = max(0, int(log_retention_days or 30))
        self._started_at = time.time()
        self._last_cleanup_date = ""
        self.worker_launcher = worker_launcher or default_worker_launcher
        self._running = {}
        self._running_files = {}
        self._running_lock = threading.Lock()
        self._scheduler_stop = threading.Event()
        self._scheduler_thread = None
        wt_task_queue.init_db(queue_db)

    def start_scheduler(self):
        self._run_log_cleanup()
        if self._scheduler_thread is None:
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop,
                daemon=True,
            )
            self._scheduler_thread.start()

    def serve_forever(self, poll_interval=0.5):
        self.start_scheduler()
        super().serve_forever(poll_interval=poll_interval)

    def server_close(self):
        self._scheduler_stop.set()
        if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=3)
        with self._running_lock:
            for task_id, proc in list(self._running.items()):
                self._kill_process(proc)
                stopped_task = wt_task_queue.mark_terminated(
                    task_id,
                    error="task queue server stopped",
                    db_path=self.queue_db,
                )
                self._notify_webhook(stopped_task, "task.terminated")
            for file_obj in list(self._running_files.values()):
                try:
                    file_obj.close()
                except Exception:
                    pass
            self._running.clear()
            self._running_files.clear()
        log_server_event(
            "task queue server stopped",
            log_path=self.server_log_path,
        )
        super().server_close()

    def _run_log_cleanup(self):
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_cleanup_date == today:
            return
        self._last_cleanup_date = today
        removed = wt_task_queue.cleanup_task_logs(
            log_dir=self.task_log_dir,
            retention_days=self.log_retention_days,
            db_path=self.queue_db,
        )
        if removed:
            log_server_event(
                "cleaned {} task log files (retention {}d)".format(
                    removed, self.log_retention_days
                ),
                log_path=self.server_log_path,
            )

    def _notify_webhook(self, task, event="task.finished"):
        if not task:
            return
        url = str(task.get("notifyUrl") or "").strip() or self.webhook_url
        if not url:
            return
        threading.Thread(
            target=self._send_webhook,
            args=(task, event, url),
            daemon=True,
        ).start()

    def _send_webhook(self, task, event, url):
        run_id = task.get("runId") or ""
        report_path = ""
        if run_id:
            report_path = "/api/tasks/{}/report".format(
                urllib.parse.quote(task.get("taskId", ""))
            )
        payload = {
            "event": event,
            "taskId": task.get("taskId", ""),
            "status": task.get("status", ""),
            "user": task.get("user", ""),
            "flowPath": task.get("flowPath", ""),
            "error": task.get("error", ""),
            "progressPercent": task.get("progressPercent", 0),
            "runId": run_id,
            "attempts": task.get("attempts", 1),
            "maxAttempts": task.get("maxAttempts", 1),
            "reportPath": report_path,
            "updatedAt": task.get("updatedAt", ""),
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "WTTaskQueue/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                response.read()
            log_server_event(
                "webhook sent for task {}".format(task.get("taskId", "")),
                log_path=self.server_log_path,
            )
        except Exception as exc:
            log_server_event(
                "webhook failed for task {}: {}".format(
                    task.get("taskId", ""), exc
                ),
                log_path=self.server_log_path,
            )

    def _kill_process(self, proc):
        try:
            if getattr(proc, "poll", lambda: None)() is not None:
                return
            pid = getattr(proc, "pid", None)
            if pid:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                )
                proc.wait(timeout=10)
                return
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass

    def _task_timed_out(self, task):
        timeout = int(task.get("timeoutSeconds") or 0)
        if timeout <= 0:
            return False
        started_at = str(task.get("startedAt") or "")
        if not started_at:
            return False
        try:
            started = datetime.fromisoformat(started_at)
        except (TypeError, ValueError):
            return False
        return (datetime.now() - started).total_seconds() >= timeout


    def _scheduler_loop(self):
        while not self._scheduler_stop.is_set():
            try:
                self._run_log_cleanup()
                self._scheduler_tick()
            except Exception as exc:
                log_server_event(
                    "scheduler error: {}".format(exc),
                    log_path=self.server_log_path,
                )
            self._scheduler_stop.wait(1.0)

    def _scheduler_tick(self):
        with self._running_lock:
            if self._running:
                task_id, proc = next(iter(self._running.items()))
                task = wt_task_queue.get_task(task_id, db_path=self.queue_db)
                if task and task.get("terminateRequested") and proc.poll() is None:
                    self._kill_process(proc)
                if task and self._task_timed_out(task) and proc.poll() is None:
                    timeout = int(task.get("timeoutSeconds") or 0)
                    self._kill_process(proc)
                    wt_task_queue.mark_failed(
                        task_id,
                        error="task timed out after {} seconds".format(timeout),
                        db_path=self.queue_db,
                    )
                    log_server_event(
                        "task {} timed out after {}s".format(task_id, timeout),
                        log_path=self.server_log_path,
                    )
                if proc.poll() is not None:
                    self._finalize_finished(task_id, task, proc)
                    self._running.pop(task_id, None)
                    file_obj = self._running_files.pop(task_id, None)
                    if file_obj is not None:
                        try:
                            file_obj.close()
                        except Exception:
                            pass
                return
        task = wt_task_queue.claim_next_pending(db_path=self.queue_db)
        if task:
            self._launch_worker(task)

    def _finalize_finished(self, task_id, task, proc):
        task = wt_task_queue.get_task(task_id, db_path=self.queue_db)
        if task is None:
            return
        if task.get("status") == STATUS_FAILED:
            result = wt_task_queue.handle_failure(
                task_id,
                error=task.get("error") or "",
                db_path=self.queue_db,
            )
            if result is not None and result.get("status") == STATUS_PENDING:
                log_server_event(
                    "task {} retry scheduled (attempt {}/{})".format(
                        task_id,
                        result.get("attempts"),
                        result.get("maxAttempts"),
                    ),
                    log_path=self.server_log_path,
                )
            else:
                log_server_event(
                    "task {} finished failed".format(task_id),
                    log_path=self.server_log_path,
                )
                self._notify_webhook(result, "task.failed")
            return
        if task.get("status") != STATUS_RUNNING:
            return
        if task.get("terminateRequested"):
            terminated = wt_task_queue.mark_terminated(
                task_id,
                error="terminate requested",
                db_path=self.queue_db,
            )
            log_server_event(
                "task {} terminated".format(task_id),
                log_path=self.server_log_path,
            )
            self._notify_webhook(terminated, "task.terminated")
            return
        code = getattr(proc, "returncode", None)
        if code == 0:
            succeeded = wt_task_queue.mark_success(
                task_id, db_path=self.queue_db
            )
            log_server_event(
                "task {} finished success".format(task_id),
                log_path=self.server_log_path,
            )
            self._notify_webhook(succeeded, "task.success")
        else:
            result = wt_task_queue.handle_failure(
                task_id,
                error="automation exited with code {}".format(code),
                db_path=self.queue_db,
            )
            if result is not None and result.get("status") == STATUS_PENDING:
                log_server_event(
                    "task {} retry scheduled (attempt {}/{})".format(
                        task_id,
                        result.get("attempts"),
                        result.get("maxAttempts"),
                    ),
                    log_path=self.server_log_path,
                )
            else:
                log_server_event(
                    "task {} finished failed (exit {})".format(task_id, code),
                    log_path=self.server_log_path,
                )
                self._notify_webhook(result, "task.failed")

    def _launch_worker(self, task):
        try:
            proc, file_obj = self.worker_launcher(
                task,
                task_log_dir=self.task_log_dir,
                queue_db=self.queue_db,
            )
        except Exception as exc:
            message = "failed to launch worker: {}".format(exc)
            result = wt_task_queue.handle_failure(
                task["taskId"],
                error=message,
                db_path=self.queue_db,
            )
            log_server_event(
                "task {} launch failed: {}".format(task["taskId"], exc),
                log_path=self.server_log_path,
            )
            if result is not None and result.get("status") == STATUS_PENDING:
                log_server_event(
                    "task {} retry scheduled after launch failure (attempt {}/{})".format(
                        task["taskId"],
                        result.get("attempts"),
                        result.get("maxAttempts"),
                    ),
                    log_path=self.server_log_path,
                )
            else:
                self._notify_webhook(result, "task.failed")
            return
        with self._running_lock:
            self._running[task["taskId"]] = proc
            if file_obj is not None:
                self._running_files[task["taskId"]] = file_obj
        log_server_event(
            "task {} worker started".format(task["taskId"]),
            log_path=self.server_log_path,
        )


def create_server(
    host="0.0.0.0",
    port=DEFAULT_PORT,
    auth_token="",
    queue_db=DEFAULT_DB_PATH,
    worker_launcher=None,
    task_log_dir=DEFAULT_TASK_LOG_DIR,
    report_dir=DEFAULT_REPORT_DIR,
    flow_dir=None,
    server_log_path=DEFAULT_SERVER_LOG,
    webhook_url=DEFAULT_WEBHOOK,
    log_retention_days=DEFAULT_LOG_RETENTION_DAYS,
):
    return TaskServer(
        (host, port),
        auth_token=auth_token,
        queue_db=queue_db,
        worker_launcher=worker_launcher,
        task_log_dir=task_log_dir,
        report_dir=report_dir,
        flow_dir=flow_dir,
        server_log_path=server_log_path,
        webhook_url=webhook_url,
        log_retention_days=log_retention_days,
    )


def serve_forever(
    host="0.0.0.0",
    port=DEFAULT_PORT,
    auth_token="",
    queue_db=DEFAULT_DB_PATH,
    webhook_url=DEFAULT_WEBHOOK,
    log_retention_days=DEFAULT_LOG_RETENTION_DAYS,
):
    server = create_server(
        host,
        port,
        auth_token=auth_token,
        queue_db=queue_db,
        webhook_url=webhook_url,
        log_retention_days=log_retention_days,
    )
    print("WT task queue service listening on {}:{}".format(host, port), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="WT automation task queue service")
    parser.add_argument("--host", default="0.0.0.0", help="listen address")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="listen port")
    parser.add_argument("--auth-token", dest="auth_token", required=True, help="shared bearer token")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="task queue sqlite path")
    parser.add_argument("--webhook", default=DEFAULT_WEBHOOK, help="global webhook URL for task terminal events")
    parser.add_argument("--log-retention-days", dest="log_retention_days", type=int, default=DEFAULT_LOG_RETENTION_DAYS, help="task log retention days")
    args = parser.parse_args()
    serve_forever(
        host=args.host,
        port=args.port,
        auth_token=args.auth_token,
        queue_db=args.db,
        webhook_url=args.webhook,
        log_retention_days=args.log_retention_days,
    )


if __name__ == "__main__":
    main()
