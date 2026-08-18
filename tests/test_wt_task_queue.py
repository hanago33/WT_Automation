# encoding: utf-8

import os
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import wt_task_queue
from wt_task_queue import TaskStateError


def test_submit_and_list_user_filter(tmp_path):
    db = str(tmp_path / "queue.db")
    task_a = wt_task_queue.submit_task("alice", "/flows/a.json", db_path=db)
    task_b = wt_task_queue.submit_task("bob", "/flows/b.json", db_path=db)

    assert task_a["status"] == "pending"
    assert task_a["user"] == "alice"
    assert task_b["user"] == "bob"
    assert len(wt_task_queue.list_tasks(db_path=db)) == 2

    mine = wt_task_queue.list_tasks(user="alice", scope="mine", db_path=db)
    assert len(mine) == 1
    assert mine[0]["user"] == "alice"


def test_fifo_and_single_running(tmp_path):
    db = str(tmp_path / "queue.db")
    first = wt_task_queue.submit_task("u1", "/flows/1.json", db_path=db)
    second = wt_task_queue.submit_task("u2", "/flows/2.json", db_path=db)
    third = wt_task_queue.submit_task("u3", "/flows/3.json", db_path=db)

    claimed = wt_task_queue.claim_next_pending(db_path=db)
    assert claimed["taskId"] == first["taskId"]
    assert claimed["status"] == "running"
    assert wt_task_queue.claim_next_pending(db_path=db) is None

    wt_task_queue.mark_success(first["taskId"], db_path=db)
    claimed_second = wt_task_queue.claim_next_pending(db_path=db)
    assert claimed_second["taskId"] == second["taskId"]

    wt_task_queue.mark_success(second["taskId"], db_path=db)
    claimed_third = wt_task_queue.claim_next_pending(db_path=db)
    assert claimed_third["taskId"] == third["taskId"]


def test_pause_resume_terminate_cancel(tmp_path):
    db = str(tmp_path / "queue.db")
    task = wt_task_queue.submit_task("u1", "/flows/a.json", db_path=db)

    paused = wt_task_queue.request_pause(task["taskId"], db_path=db)
    assert paused["status"] == "paused"

    resumed = wt_task_queue.resume_task(task["taskId"], db_path=db)
    assert resumed["status"] == "pending"
    assert resumed["attempts"] == 2

    running = wt_task_queue.claim_next_pending(db_path=db)
    assert running["taskId"] == task["taskId"]
    flagged = wt_task_queue.request_pause(task["taskId"], db_path=db)
    assert flagged["pauseRequested"] is True

    paused_again = wt_task_queue.mark_paused(
        task["taskId"],
        resume_from_step="step_2",
        db_path=db,
    )
    assert paused_again["status"] == "paused"
    assert paused_again["resumeFromStep"] == "step_2"

    wt_task_queue.resume_task(task["taskId"], db_path=db)
    running_again = wt_task_queue.claim_next_pending(db_path=db)
    assert running_again["taskId"] == task["taskId"]

    terminated = wt_task_queue.request_terminate(task["taskId"], db_path=db)
    assert terminated["terminateRequested"] is True
    wt_task_queue.mark_terminated(task["taskId"], error="stopped", db_path=db)
    assert wt_task_queue.get_task(task["taskId"], db_path=db)["status"] == "terminated"

    pending = wt_task_queue.submit_task("u2", "/flows/b.json", db_path=db)
    canceled = wt_task_queue.cancel_task(pending["taskId"], db_path=db)
    assert canceled["status"] == "canceled"

    with pytest.raises(TaskStateError):
        wt_task_queue.request_terminate(pending["taskId"], db_path=db)


def test_progress_update(tmp_path):
    db = str(tmp_path / "queue.db")
    task = wt_task_queue.submit_task("u1", "/flows/a.json", db_path=db)
    wt_task_queue.claim_next_pending(db_path=db)

    updated = wt_task_queue.update_progress(
        task["taskId"],
        current_step_id="step_1",
        current_step_name="launch",
        progress_current=1,
        progress_total=3,
        progress_percent=33.333,
        resume_from_step="step_2",
        last_log="step one done",
        db_path=db,
    )
    assert updated["currentStepId"] == "step_1"
    assert updated["currentStepName"] == "launch"
    assert updated["progressCurrent"] == 1
    assert updated["progressTotal"] == 3
    assert updated["resumeFromStep"] == "step_2"
    assert updated["lastLog"] == "step one done"


def test_task_log_tail(tmp_path):
    log_dir = str(tmp_path / "tasks")
    wt_task_queue.append_task_log("task_1", "first", log_dir=log_dir)
    wt_task_queue.append_task_log("task_1", "second", log_dir=log_dir)
    wt_task_queue.append_task_log("task_1", "third", log_dir=log_dir)

    lines, total = wt_task_queue.read_task_log_tail(
        "task_1",
        tail=2,
        log_dir=log_dir,
    )
    assert total == 3
    assert lines == ["second", "third"]


def test_large_log_tail_reads_last_lines(tmp_path):
    log_dir = str(tmp_path / "tasks")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "task_big.log")
    with open(path, "w", encoding="utf-8") as file_obj:
        for index in range(10000):
            file_obj.write("line {:05d}\n".format(index))

    lines, total = wt_task_queue.read_task_log_tail(
        "task_big",
        tail=5,
        log_dir=log_dir,
    )
    assert total == 10000
    assert lines == [
        "line 09995",
        "line 09996",
        "line 09997",
        "line 09998",
        "line 09999",
    ]
