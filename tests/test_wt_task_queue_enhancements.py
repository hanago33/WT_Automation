# encoding: utf-8

import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import wt_task_queue


def test_submit_persists_priority_schedule_retry_timeout(tmp_path):
    db = str(tmp_path / "queue.db")
    task = wt_task_queue.submit_task(
        "alice",
        "/flows/a.json",
        priority=7,
        scheduled_at="2026-08-11T12:00:00",
        max_attempts=5,
        retry_delay_seconds=60,
        timeout_seconds=300,
        db_path=db,
    )
    assert task["priority"] == 7
    assert task["scheduledAt"] == "2026-08-11T12:00:00"
    assert task["maxAttempts"] == 5
    assert task["retryDelaySeconds"] == 60
    assert task["timeoutSeconds"] == 300
    assert task["attempts"] == 1
    assert task["nextRetryAt"] == ""


def test_priority_controls_claim_order(tmp_path):
    db = str(tmp_path / "queue.db")
    low = wt_task_queue.submit_task("u1", "/flows/low.json", priority=0, db_path=db)
    high = wt_task_queue.submit_task("u2", "/flows/high.json", priority=10, db_path=db)
    medium = wt_task_queue.submit_task("u3", "/flows/med.json", priority=5, db_path=db)

    claimed = wt_task_queue.claim_next_pending(db_path=db)
    assert claimed["taskId"] == high["taskId"]
    wt_task_queue.mark_success(claimed["taskId"], db_path=db)

    claimed = wt_task_queue.claim_next_pending(db_path=db)
    assert claimed["taskId"] == medium["taskId"]
    wt_task_queue.mark_success(claimed["taskId"], db_path=db)

    claimed = wt_task_queue.claim_next_pending(db_path=db)
    assert claimed["taskId"] == low["taskId"]


def test_scheduled_task_is_not_claimed_before_time(tmp_path):
    db = str(tmp_path / "queue.db")
    immediate = wt_task_queue.submit_task("u1", "/flows/a.json", db_path=db)
    future = wt_task_queue.submit_task(
        "u2",
        "/flows/b.json",
        scheduled_at=(datetime.now() + timedelta(hours=1)).isoformat(
            timespec="seconds"
        ),
        db_path=db,
    )

    claimed = wt_task_queue.claim_next_pending(db_path=db)
    assert claimed["taskId"] == immediate["taskId"]
    wt_task_queue.mark_success(immediate["taskId"], db_path=db)
    assert wt_task_queue.claim_next_pending(db_path=db) is None

    past = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    conn = wt_task_queue._connect(db)
    conn.execute(
        "UPDATE tasks SET scheduled_at = ? WHERE task_id = ?",
        (past, future["taskId"]),
    )
    conn.commit()
    conn.close()

    claimed = wt_task_queue.claim_next_pending(db_path=db)
    assert claimed["taskId"] == future["taskId"]


def test_auto_retry_until_max_attempts(tmp_path):
    db = str(tmp_path / "queue.db")
    task = wt_task_queue.submit_task(
        "u1",
        "/flows/a.json",
        max_attempts=3,
        retry_delay_seconds=0,
        db_path=db,
    )
    task_id = task["taskId"]

    for expected_attempt in (1, 2, 3):
        claimed = wt_task_queue.claim_next_pending(db_path=db)
        assert claimed["taskId"] == task_id
        assert claimed["attempts"] == expected_attempt
        wt_task_queue.mark_failed(task_id, error="boom", db_path=db)
        result = wt_task_queue.handle_failure(task_id, error="boom", db_path=db)
        if expected_attempt < 3:
            assert result["status"] == "pending"
            assert result["attempts"] == expected_attempt + 1
        else:
            assert result["status"] == "failed"
            assert result["attempts"] == 3

    assert wt_task_queue.claim_next_pending(db_path=db) is None


def test_retry_delay_blocks_claim_until_next_retry_at(tmp_path):
    db = str(tmp_path / "queue.db")
    task = wt_task_queue.submit_task(
        "u1",
        "/flows/a.json",
        max_attempts=2,
        retry_delay_seconds=3600,
        db_path=db,
    )
    wt_task_queue.claim_next_pending(db_path=db)
    wt_task_queue.mark_failed(task["taskId"], error="boom", db_path=db)
    result = wt_task_queue.handle_failure(task["taskId"], error="boom", db_path=db)

    assert result["status"] == "pending"
    assert result["attempts"] == 2
    assert result["nextRetryAt"]
    assert wt_task_queue.claim_next_pending(db_path=db) is None

    past = (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")
    conn = wt_task_queue._connect(db)
    conn.execute(
        "UPDATE tasks SET next_retry_at = ? WHERE task_id = ?",
        (past, task["taskId"]),
    )
    conn.commit()
    conn.close()

    claimed = wt_task_queue.claim_next_pending(db_path=db)
    assert claimed["taskId"] == task["taskId"]


def test_queue_stats_counts_and_rates(tmp_path):
    db = str(tmp_path / "queue.db")
    first = wt_task_queue.submit_task("u1", "/flows/a.json", db_path=db)
    second = wt_task_queue.submit_task("u2", "/flows/b.json", db_path=db)
    third = wt_task_queue.submit_task("u3", "/flows/c.json", db_path=db)

    wt_task_queue.claim_next_pending(db_path=db)
    wt_task_queue.mark_success(first["taskId"], db_path=db)
    wt_task_queue.claim_next_pending(db_path=db)
    wt_task_queue.mark_failed(second["taskId"], error="boom", db_path=db)

    stats = wt_task_queue.get_queue_stats(db_path=db)
    assert stats["total"] == 3
    assert stats["pending"] == 1
    assert stats["running"] == 0
    assert stats["success"] == 1
    assert stats["failed"] == 1
    assert stats["byStatus"]["pending"] == 1
    assert stats["todaySubmitted"] == 3
    assert stats["todayCompleted"] == 2
    assert stats["successLast24h"] == 1
    assert stats["failedLast24h"] == 1
    assert stats["successRateLast24h"] == 0.5
    assert third["taskId"] != ""


def test_init_db_migrates_existing_tasks_table(tmp_path):
    db = str(tmp_path / "queue.db")
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY,
            user TEXT NOT NULL,
            source_ip TEXT DEFAULT '',
            flow_path TEXT NOT NULL,
            steps_requested TEXT DEFAULT '[]',
            from_step TEXT DEFAULT '',
            to_step TEXT DEFAULT '',
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
    )
    conn.commit()
    conn.close()

    wt_task_queue.init_db(db)
    task = wt_task_queue.submit_task(
        "u1",
        "/flows/a.json",
        priority=2,
        max_attempts=2,
        timeout_seconds=10,
        db_path=db,
    )
    assert task["priority"] == 2
    assert task["maxAttempts"] == 2
    assert task["timeoutSeconds"] == 10
