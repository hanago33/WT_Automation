# encoding: utf-8

"""Persistent task queue backed by SQLite.

The queue server and the automation worker run on the same host, so both can
open the local database directly. Only the Python standard library is used.
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DB_PATH = os.path.join(BASE_DIR, "logs", "task_queue.db")
DEFAULT_TASK_LOG_DIR = os.path.join(BASE_DIR, "logs", "tasks")
DEFAULT_TAIL = 300

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_PAUSED = "paused"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
STATUS_CANCELED = "canceled"
STATUS_TERMINATED = "terminated"
VALID_STATUSES = {
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_PAUSED,
    STATUS_SUCCESS,
    STATUS_FAILED,
    STATUS_CANCELED,
    STATUS_TERMINATED,
}

_COLUMN_TO_KEY = {
    "task_id": "taskId",
    "user": "user",
    "source_ip": "sourceIp",
    "flow_path": "flowPath",
    "steps_requested": "stepsRequested",
    "from_step": "fromStep",
    "to_step": "toStep",
    "priority": "priority",
    "scheduled_at": "scheduledAt",
    "max_attempts": "maxAttempts",
    "retry_delay_seconds": "retryDelaySeconds",
    "next_retry_at": "nextRetryAt",
    "timeout_seconds": "timeoutSeconds",
    "notify_url": "notifyUrl",
    "status": "status",
    "progress_current": "progressCurrent",
    "progress_total": "progressTotal",
    "progress_percent": "progressPercent",
    "current_step_id": "currentStepId",
    "current_step_name": "currentStepName",
    "resume_from_step": "resumeFromStep",
    "last_log": "lastLog",
    "error": "error",
    "run_id": "runId",
    "pause_requested": "pauseRequested",
    "terminate_requested": "terminateRequested",
    "attempts": "attempts",
    "created_at": "createdAt",
    "started_at": "startedAt",
    "ended_at": "endedAt",
    "updated_at": "updatedAt",
}

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    user TEXT NOT NULL,
    source_ip TEXT DEFAULT '',
    flow_path TEXT NOT NULL,
    steps_requested TEXT DEFAULT '[]',
    from_step TEXT DEFAULT '',
    to_step TEXT DEFAULT '',
    priority INTEGER DEFAULT 0,
    scheduled_at TEXT DEFAULT '',
    max_attempts INTEGER DEFAULT 1,
    retry_delay_seconds INTEGER DEFAULT 0,
    next_retry_at TEXT DEFAULT '',
    timeout_seconds INTEGER DEFAULT 0,
    notify_url TEXT DEFAULT '',
    status TEXT NOT NULL,
    progress_current INTEGER DEFAULT 0,
    progress_total INTEGER DEFAULT 0,
    progress_percent REAL DEFAULT 0,
    current_step_id TEXT DEFAULT '',
    current_step_name TEXT DEFAULT '',
    resume_from_step TEXT DEFAULT '',
    last_log TEXT DEFAULT '',
    error TEXT DEFAULT '',
    run_id TEXT DEFAULT '',
    pause_requested INTEGER DEFAULT 0,
    terminate_requested INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 1,
    created_at TEXT DEFAULT '',
    started_at TEXT DEFAULT '',
    ended_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
)
"""


_MIGRATION_COLUMNS = (
    ("priority", "INTEGER DEFAULT 0"),
    ("scheduled_at", "TEXT DEFAULT ''"),
    ("max_attempts", "INTEGER DEFAULT 1"),
    ("retry_delay_seconds", "INTEGER DEFAULT 0"),
    ("next_retry_at", "TEXT DEFAULT ''"),
    ("timeout_seconds", "INTEGER DEFAULT 0"),
    ("notify_url", "TEXT DEFAULT ''"),
)


def _ensure_columns(conn):
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    for column, definition in _MIGRATION_COLUMNS:
        if column not in existing:
            conn.execute(
                "ALTER TABLE tasks ADD COLUMN {} {}".format(column, definition)
            )


class TaskStateError(Exception):
    """Raised when an operation is not valid for the current task status."""


def _now_iso():
    return datetime.now().isoformat(timespec="seconds")


def _connect(db_path):
    if db_path == ":memory:":
        conn = sqlite3.connect(db_path, timeout=15)
    else:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 15000")
    except sqlite3.Error:
        pass
    return conn


# 建表 DDL 只需在每个进程内对同一 db_path 执行一次；后续查询直接复用。
# 原实现每次 get_task / list_tasks / get_queue_stats 等都完整跑一遍
# PRAGMA + CREATE TABLE + 4 个 CREATE INDEX，客户端轮询越频繁开销越明显。
_DB_INIT_LOCK = threading.Lock()
_DB_INITIALIZED = set()


def init_db(db_path=DEFAULT_DB_PATH, force=False):
    """确保任务库表结构就绪；同一进程内对同一 db_path 只真正建表一次。

    运维清库等需要重建表结构的场景，传 force=True 强制执行。
    """
    if db_path == ":memory:":
        # 内存库每次 sqlite3.connect 都是全新实例，跳过建表会破坏测试
        _init_db_once(db_path)
        return
    key = os.path.abspath(db_path)
    with _DB_INIT_LOCK:
        if not force and key in _DB_INITIALIZED:
            return
        _init_db_once(db_path)
        _DB_INITIALIZED.add(key)


def _init_db_once(db_path):
    conn = _connect(db_path)
    try:
        if db_path != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(_CREATE_TABLE_SQL)
        _ensure_columns(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_user ON tasks(user)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT '',
                user TEXT DEFAULT '',
                source_ip TEXT DEFAULT '',
                action TEXT NOT NULL,
                task_id TEXT DEFAULT '',
                result TEXT DEFAULT '',
                detail TEXT DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_task ON audit_events(task_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_events(user)")
        conn.commit()
    finally:
        conn.close()


def _row_to_task(row):
    if row is None:
        return None
    task = {}
    for column, value in dict(row).items():
        task[_COLUMN_TO_KEY.get(column, column)] = value
    try:
        task["stepsRequested"] = json.loads(task.get("stepsRequested") or "[]")
    except (TypeError, ValueError):
        task["stepsRequested"] = []
    for key in ("pauseRequested", "terminateRequested"):
        task[key] = bool(task.get(key))
    return task


def get_task(task_id, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        return _row_to_task(row)
    finally:
        conn.close()


def submit_task(
    user,
    flow_path,
    source_ip="",
    steps=None,
    from_step="",
    to_step="",
    priority=0,
    scheduled_at="",
    max_attempts=1,
    retry_delay_seconds=0,
    timeout_seconds=0,
    notify_url="",
    db_path=DEFAULT_DB_PATH,
):
    init_db(db_path)
    task_id = "task_{}_{}".format(
        datetime.now().strftime("%Y%m%d_%H%M%S"),
        uuid.uuid4().hex[:6],
    )
    now = _now_iso()
    steps_requested = list(steps or [])
    priority = int(priority or 0)
    scheduled_at = str(scheduled_at or "")
    max_attempts = max(1, int(max_attempts or 1))
    retry_delay_seconds = max(0, int(retry_delay_seconds or 0))
    timeout_seconds = max(0, int(timeout_seconds or 0))
    notify_url = str(notify_url or "").strip()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO tasks (
                task_id, user, source_ip, flow_path, steps_requested,
                from_step, to_step, priority, scheduled_at, max_attempts,
                retry_delay_seconds, next_retry_at, timeout_seconds,
                notify_url, status,
                progress_current, progress_total, progress_percent,
                current_step_id, current_step_name, resume_from_step,
                last_log, error, run_id, pause_requested, terminate_requested,
                attempts, created_at, started_at, ended_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0,
                       '', '', '', '', '', '', 0, 0, 1, ?, '', '', ?)
            """,
            (
                task_id,
                user,
                source_ip,
                flow_path,
                json.dumps(steps_requested, ensure_ascii=False),
                from_step,
                to_step,
                priority,
                scheduled_at,
                max_attempts,
                retry_delay_seconds,
                "",
                timeout_seconds,
                notify_url,
                STATUS_PENDING,
                now,
                now,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def list_tasks(user=None, scope="all", limit=200, db_path=DEFAULT_DB_PATH, task_ids=None):
    """列出任务。

    传 task_ids 时只取回这一批任务，客户端可用 1 次请求替代 N 次 get_task，
    避免轮询场景下的 N+1 查询。不传时行为与旧版本完全一致。
    """
    init_db(db_path)
    conn = _connect(db_path)
    try:
        query = "SELECT * FROM tasks"
        params = []
        conditions = []
        ids = [str(item).strip() for item in (task_ids or []) if str(item).strip()]
        if ids:
            # 数量上限与 limit 对齐，避免拼出过长的 IN 子句
            ids = ids[: max(1, int(limit))]
            conditions.append("task_id IN ({})".format(",".join("?" * len(ids))))
            params.extend(ids)
        if scope == "mine" and user:
            conditions.append("user = ?")
            params.append(user)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at ASC, rowid ASC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(query, params).fetchall()
        return [_row_to_task(row) for row in rows]
    finally:
        conn.close()


def claim_next_pending(db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        already_running = conn.execute(
            "SELECT 1 FROM tasks WHERE status = ? LIMIT 1",
            (STATUS_RUNNING,),
        ).fetchone()
        if already_running is not None:
            conn.rollback()
            return None
        now = _now_iso()
        row = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status = ? AND pause_requested = 0
                AND (scheduled_at = '' OR scheduled_at <= ?)
                AND (next_retry_at = '' OR next_retry_at <= ?)
            ORDER BY priority DESC, created_at ASC, rowid ASC
            LIMIT 1
            """,
            (STATUS_PENDING, now, now),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None
        conn.execute(
            """
            UPDATE tasks SET status = ?,
                started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                updated_at = ?, terminate_requested = 0
            WHERE task_id = ?
            """,
            (STATUS_RUNNING, now, now, row["task_id"]),
        )
        conn.commit()
        updated = conn.execute(
            "SELECT * FROM tasks WHERE task_id = ?",
            (row["task_id"],),
        ).fetchone()
        return _row_to_task(updated)
    finally:
        conn.close()


def mark_started(task_id, run_id=None, db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE tasks SET status = ?, run_id = COALESCE(?, run_id),
                started_at = CASE WHEN started_at = '' THEN ? ELSE started_at END,
                updated_at = ?, terminate_requested = 0
            WHERE task_id = ?
            """,
            (STATUS_RUNNING, run_id or "", now, now, task_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def update_progress(
    task_id,
    current_step_id=None,
    current_step_name=None,
    progress_current=None,
    progress_total=None,
    progress_percent=None,
    resume_from_step=None,
    last_log=None,
    db_path=DEFAULT_DB_PATH,
):
    init_db(db_path)
    updates = []
    params = []
    mapping = (
        ("current_step_id", current_step_id),
        ("current_step_name", current_step_name),
        ("progress_current", progress_current),
        ("progress_total", progress_total),
        ("progress_percent", progress_percent),
        ("resume_from_step", resume_from_step),
        ("last_log", last_log),
    )
    for column, value in mapping:
        if value is not None:
            updates.append("{} = ?".format(column))
            params.append(value)
    if not updates:
        return get_task(task_id, db_path=db_path)
    updates.append("updated_at = ?")
    params.append(_now_iso())
    params.append(task_id)
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE tasks SET {} WHERE task_id = ?".format(", ".join(updates)),
            params,
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def mark_success(task_id, run_id=None, last_log=None, db_path=DEFAULT_DB_PATH):
    """把任务标记为成功；仅当任务当前为 running 时生效（状态守卫）。

    状态守卫用于防止"迟到 worker"覆盖已被其他路径改变的任务：例如服务端重启后
    孤儿恢复把任务标为 failed，若旧 worker 进程存活并稍后完成，守卫会让这次
    mark_success 变成 no-op，避免任务状态被来回覆盖。
    """
    init_db(db_path)
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE tasks SET status = ?, run_id = COALESCE(?, run_id),
                ended_at = ?, error = '', last_log = COALESCE(?, last_log),
                pause_requested = 0, terminate_requested = 0, updated_at = ?
            WHERE task_id = ? AND status = ?
            """,
            (
                STATUS_SUCCESS,
                run_id or "",
                now,
                last_log or "",
                now,
                task_id,
                STATUS_RUNNING,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def mark_failed(task_id, error="", run_id=None, db_path=DEFAULT_DB_PATH):
    """把任务标记为失败；仅当任务当前为 running 或 failed 时生效（状态守卫）。

    failed 前驱保留给重试收尾（handle_failure 从 failed 状态再次写失败）；
    running 是运行中失败的正常前驱。其余状态（success/pending/canceled 等）
    说明任务已被其他路径改变，迟到 worker 的失败上报为 no-op。
    """
    init_db(db_path)
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE tasks SET status = ?, run_id = COALESCE(?, run_id),
                ended_at = ?, error = ?, pause_requested = 0,
                terminate_requested = 0, updated_at = ?
            WHERE task_id = ? AND status IN (?, ?)
            """,
            (
                STATUS_FAILED,
                run_id or "",
                now,
                error,
                now,
                task_id,
                STATUS_RUNNING,
                STATUS_FAILED,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def mark_paused(task_id, resume_from_step=None, db_path=DEFAULT_DB_PATH):
    """把任务标记为暂停；仅当任务当前为 running 或 pending 时生效（状态守卫）。

    pending 前驱来自 request_pause：排队中的任务可直接暂停（跳过运行）；
    running 是运行中收到暂停请求后的正常前驱。
    """
    init_db(db_path)
    now = _now_iso()
    conn = _connect(db_path)
    try:
        if resume_from_step:
            conn.execute(
                """
                UPDATE tasks SET status = ?, pause_requested = 0, ended_at = ?,
                    resume_from_step = ?, updated_at = ?
                WHERE task_id = ? AND status IN (?, ?)
                """,
                (
                    STATUS_PAUSED,
                    now,
                    resume_from_step,
                    now,
                    task_id,
                    STATUS_RUNNING,
                    STATUS_PENDING,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE tasks SET status = ?, pause_requested = 0, ended_at = ?,
                    updated_at = ?
                WHERE task_id = ? AND status IN (?, ?)
                """,
                (STATUS_PAUSED, now, now, task_id, STATUS_RUNNING, STATUS_PENDING),
            )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def mark_terminated(task_id, error="", db_path=DEFAULT_DB_PATH):
    """把任务标记为终止；仅当任务当前为 running 或 paused 时生效（状态守卫）。

    优雅关闭/超时/手动终止都从 running 进入 terminated；paused 任务也可能被
    终止。其余状态（success/failed/pending 等）说明任务已被其他路径收尾，
    迟到请求为 no-op。
    """
    init_db(db_path)
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE tasks SET status = ?, terminate_requested = 0, ended_at = ?,
                error = ?, updated_at = ?
            WHERE task_id = ? AND status IN (?, ?)
            """,
            (
                STATUS_TERMINATED,
                now,
                error,
                now,
                task_id,
                STATUS_RUNNING,
                STATUS_PAUSED,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def request_pause(task_id, db_path=DEFAULT_DB_PATH):
    task = get_task(task_id, db_path=db_path)
    if task is None:
        raise TaskStateError("task not found")
    if task["status"] == STATUS_PENDING:
        return mark_paused(task_id, resume_from_step=task.get("resumeFromStep") or "", db_path=db_path)
    if task["status"] == STATUS_RUNNING:
        init_db(db_path)
        conn = _connect(db_path)
        try:
            conn.execute(
                "UPDATE tasks SET pause_requested = 1, updated_at = ? WHERE task_id = ?",
                (_now_iso(), task_id),
            )
            conn.commit()
        finally:
            conn.close()
        return get_task(task_id, db_path=db_path)
    raise TaskStateError("pause is only valid for pending or running tasks")


def request_terminate(task_id, db_path=DEFAULT_DB_PATH):
    task = get_task(task_id, db_path=db_path)
    if task is None:
        raise TaskStateError("task not found")
    if task["status"] != STATUS_RUNNING:
        raise TaskStateError("terminate is only valid for running tasks")
    init_db(db_path)
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE tasks SET terminate_requested = 1, updated_at = ? WHERE task_id = ?",
            (_now_iso(), task_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def cancel_task(task_id, db_path=DEFAULT_DB_PATH):
    task = get_task(task_id, db_path=db_path)
    if task is None:
        raise TaskStateError("task not found")
    if task["status"] != STATUS_PENDING:
        raise TaskStateError("cancel is only valid for pending tasks")
    init_db(db_path)
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE tasks SET status = ?, ended_at = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (STATUS_CANCELED, now, now, task_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def resume_task(task_id, db_path=DEFAULT_DB_PATH):
    task = get_task(task_id, db_path=db_path)
    if task is None:
        raise TaskStateError("task not found")
    if task["status"] not in (STATUS_PAUSED, STATUS_FAILED, STATUS_TERMINATED):
        raise TaskStateError("resume is only valid for paused, failed or terminated tasks")
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE tasks SET status = ?, attempts = attempts + 1,
                pause_requested = 0, terminate_requested = 0, ended_at = '',
                error = '', run_id = '', started_at = '', updated_at = ?
            WHERE task_id = ?
            """,
            (STATUS_PENDING, now, task_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def schedule_retry(task_id, error="", db_path=DEFAULT_DB_PATH):
    task = get_task(task_id, db_path=db_path)
    if task is None:
        return None
    now = _now_iso()
    delay = max(0, int(task.get("retryDelaySeconds") or 0))
    if delay:
        next_retry_at = (datetime.now() + timedelta(seconds=delay)).isoformat(
            timespec="seconds"
        )
    else:
        next_retry_at = ""
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            UPDATE tasks SET status = ?, attempts = attempts + 1,
                pause_requested = 0, terminate_requested = 0, ended_at = '',
                started_at = '', run_id = '', next_retry_at = ?,
                error = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (STATUS_PENDING, next_retry_at, error, now, task_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_task(task_id, db_path=db_path)


def handle_failure(task_id, error="", db_path=DEFAULT_DB_PATH):
    task = get_task(task_id, db_path=db_path)
    if task is None:
        return None
    if task.get("status") not in (STATUS_RUNNING, STATUS_FAILED):
        return task
    error = error or task.get("error") or "task failed"
    max_attempts = max(1, int(task.get("maxAttempts") or 1))
    attempts = max(1, int(task.get("attempts") or 1))
    if attempts < max_attempts:
        return schedule_retry(task_id, error=error, db_path=db_path)
    return mark_failed(task_id, error=error, db_path=db_path)


def recover_orphan_running_tasks(db_path=DEFAULT_DB_PATH, max_age_seconds=120):
    """服务端重启后恢复孤儿任务：把遗留的 running 任务标记为失败。

    服务端把任务状态与子进程生命周期绑定在内存（_running）中；进程退出后，遗留的
    running 任务没有任何进程再更新它，而 claim_next_pending 检测到存在 running 任务
    时不会派发新任务，导致整条队列卡死。服务端启动时调用本函数把这些孤儿任务标记为
    失败（错误信息可辨），队列即可继续派发，不会永久阻塞。

    这里选择"标记失败"而非自动重排队：worker 子进程可能独立存活，自动重跑会造成
    同一任务重复执行；标记失败后由用户在队列窗口手动重试，避免重复。

    max_age_seconds：仅恢复 updated_at 距今超过该阈值的 running 任务。updated_at
    在认领/进度刷新时都会被更新，代表"最近活跃"：刚被另一实例认领的任务
    updated_at 很新，不会被误杀；崩溃遗留的孤儿任务已长时间无更新，会被恢复。
    代价是崩溃后该阈值内重启不会自动恢复，可由队列窗口手动处理。

    返回被标记失败的任务数量。
    """
    init_db(db_path)
    cutoff = (
        datetime.now() - timedelta(seconds=max(0, int(max_age_seconds)))
    ).isoformat(timespec="seconds")
    conn = _connect(db_path)
    orphan_ids = []
    try:
        rows = conn.execute(
            """
            SELECT task_id FROM tasks
            WHERE status = ? AND (updated_at = '' OR updated_at <= ?)
            """,
            (STATUS_RUNNING, cutoff),
        ).fetchall()
        orphan_ids = [row["task_id"] for row in rows]
    finally:
        conn.close()
    error = (
        "服务端重启导致任务中断，已标记为失败（worker 状态已丢失），可手动重试"
    )
    for task_id in orphan_ids:
        mark_failed(task_id, error=error, db_path=db_path)
    return len(orphan_ids)


def get_queue_stats(db_path=DEFAULT_DB_PATH):
    init_db(db_path)
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
        ).fetchall()
        by_status = {status: 0 for status in VALID_STATUSES}
        total = 0
        for row in rows:
            by_status[row["status"]] = int(row["count"])
            total += int(row["count"])
        now = datetime.now()
        today_start = now.replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat(timespec="seconds")
        last_24h_start = (now - timedelta(days=1)).isoformat(timespec="seconds")

        def count_where(condition, params):
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM tasks WHERE " + condition,
                    params,
                ).fetchone()[0]
            )

        today_submitted = count_where("created_at >= ?", (today_start,))
        today_completed = count_where(
            "ended_at >= ? AND status IN (?, ?, ?, ?)",
            (
                today_start,
                STATUS_SUCCESS,
                STATUS_FAILED,
                STATUS_CANCELED,
                STATUS_TERMINATED,
            ),
        )
        success_24h = count_where(
            "status = ? AND ended_at >= ?",
            (STATUS_SUCCESS, last_24h_start),
        )
        failed_24h = count_where(
            "status = ? AND ended_at >= ?",
            (STATUS_FAILED, last_24h_start),
        )
        success_rate = None
        denominator = success_24h + failed_24h
        if denominator:
            success_rate = round(success_24h / denominator, 4)

        def average_seconds(start_col, end_col):
            row = conn.execute(
                "SELECT AVG(julianday({end}) - julianday({start})) * 86400.0 "
                "AS value FROM tasks WHERE {start} != '' AND {end} != '' "
                "AND {end} > {start}".format(start=start_col, end=end_col)
            ).fetchone()
            value = row["value"] if row is not None else None
            if value is None:
                return None
            return round(float(value), 1)

        return {
            "total": total,
            "byStatus": by_status,
            "pending": by_status[STATUS_PENDING],
            "running": by_status[STATUS_RUNNING],
            "paused": by_status[STATUS_PAUSED],
            "success": by_status[STATUS_SUCCESS],
            "failed": by_status[STATUS_FAILED],
            "canceled": by_status[STATUS_CANCELED],
            "terminated": by_status[STATUS_TERMINATED],
            "todaySubmitted": today_submitted,
            "todayCompleted": today_completed,
            "successLast24h": success_24h,
            "failedLast24h": failed_24h,
            "successRateLast24h": success_rate,
            "averageWaitSeconds": average_seconds("created_at", "started_at"),
            "averageRunSeconds": average_seconds("started_at", "ended_at"),
            "updatedAt": _now_iso(),
        }
    finally:
        conn.close()


def append_task_log(task_id, text, log_dir=DEFAULT_TASK_LOG_DIR):
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "{}.log".format(task_id))
    with open(path, "a", encoding="utf-8") as file_obj:
        file_obj.write("{}\n".format(text))
    return path


def read_task_log_tail(task_id, tail=DEFAULT_TAIL, log_dir=DEFAULT_TASK_LOG_DIR):
    path = os.path.join(log_dir, "{}.log".format(task_id))
    if not os.path.exists(path):
        return [], 0
    if tail <= 0:
        return [], 0
    try:
        with open(path, "rb") as file_obj:
            total_lines = 0
            while True:
                block = file_obj.read(1024 * 1024)
                if not block:
                    break
                total_lines += block.count(b"\n")
            file_obj.seek(0, os.SEEK_END)
            size = file_obj.tell()
            if size == 0:
                return [], 0
            pos = size
            fragments = []
            carry = b""
            while pos > 0:
                read_size = min(65536, pos)
                pos -= read_size
                file_obj.seek(pos)
                data = file_obj.read(read_size) + carry
                parts = data.split(b"\n")
                carry = parts[0]
                fragments = parts[1:] + fragments
                if len(fragments) >= tail + 1:
                    break
            if carry:
                fragments = [carry] + fragments
            if fragments and fragments[-1] == b"":
                fragments.pop()
            lines = [
                fragment.decode("utf-8", errors="replace").rstrip("\r")
                for fragment in fragments[-tail:]
            ]
            return lines, total_lines
    except OSError:
        return [], 0


def task_log_path(task_id, log_dir=DEFAULT_TASK_LOG_DIR):
    return os.path.join(log_dir, "{}.log".format(task_id))

def add_audit_event(
    user="",
    source_ip="",
    action="",
    task_id="",
    result="",
    detail="",
    db_path=DEFAULT_DB_PATH,
):
    init_db(db_path)
    now = _now_iso()
    conn = _connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO audit_events (
                created_at, user, source_ip, action, task_id, result, detail
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                str(user or ""),
                str(source_ip or ""),
                str(action or ""),
                str(task_id or ""),
                str(result or ""),
                str(detail or ""),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "createdAt": now,
        "user": str(user or ""),
        "sourceIp": str(source_ip or ""),
        "action": str(action or ""),
        "taskId": str(task_id or ""),
        "result": str(result or ""),
        "detail": str(detail or ""),
    }


def list_audit_events(
    task_id=None,
    user=None,
    limit=200,
    db_path=DEFAULT_DB_PATH,
):
    init_db(db_path)
    conn = _connect(db_path)
    try:
        query = "SELECT * FROM audit_events"
        conditions = []
        params = []
        if task_id:
            conditions.append("task_id = ?")
            params.append(task_id)
        if user:
            conditions.append("user = ?")
            params.append(user)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(query, params).fetchall()
        events = []
        for row in rows:
            event = dict(row)
            event["createdAt"] = event.pop("created_at", "")
            event["sourceIp"] = event.pop("source_ip", "")
            event["taskId"] = event.pop("task_id", "")
            events.append(event)
        return events
    finally:
        conn.close()


def cleanup_task_logs(
    log_dir=DEFAULT_TASK_LOG_DIR,
    retention_days=30,
    db_path=DEFAULT_DB_PATH,
):
    """Remove task log files older than retention_days; returns count."""
    removed = 0
    cutoff = time.time() - max(0, int(retention_days or 30)) * 86400
    try:
        for name in os.listdir(log_dir):
            if not name.endswith(".log"):
                continue
            path = os.path.join(log_dir, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    except OSError:
        pass
    return removed
