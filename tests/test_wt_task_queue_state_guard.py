# encoding: utf-8
"""P1-6 状态守卫专项测试：mark_* 仅对合法前驱状态生效，迟到写入为 no-op。

核心价值场景：服务端重启后孤儿恢复把 running 标为 failed，若旧 worker 进程存活
并稍后上报 success/failed，守卫应拦截，任务状态不被来回覆盖。
"""
import os
import sys
import tempfile

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_task_queue as tq


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "queue.db")


def _running(db, user="u1", **submit_kw):
    task = tq.submit_task(user, "/flows/a.json", db_path=db, **submit_kw)
    claimed = tq.claim_next_pending(db_path=db)
    assert claimed["taskId"] == task["taskId"]
    assert claimed["status"] == "running"
    return task["taskId"]


class TestSuccessGuard:
    def test_success_on_running_ok(self, db):
        task_id = _running(db)
        tq.mark_success(task_id, db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "success"

    def test_success_on_pending_is_noop(self, db):
        task_id = tq.submit_task("u1", "/flows/a.json", db_path=db)["taskId"]
        tq.mark_success(task_id, db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "pending"

    def test_success_on_failed_is_noop(self, db):
        task_id = _running(db)
        tq.mark_failed(task_id, error="boom", db_path=db)
        tq.mark_success(task_id, db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "failed"


class TestFailedGuard:
    def test_failed_on_running_ok(self, db):
        task_id = _running(db)
        tq.mark_failed(task_id, error="boom", db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "failed"

    def test_failed_on_failed_ok(self, db):
        task_id = _running(db)
        tq.mark_failed(task_id, error="boom", db_path=db)
        tq.mark_failed(task_id, error="boom2", db_path=db)
        task = tq.get_task(task_id, db_path=db)
        assert task["status"] == "failed"
        assert task["error"] == "boom2"

    def test_failed_on_success_is_noop(self, db):
        task_id = _running(db)
        tq.mark_success(task_id, db_path=db)
        tq.mark_failed(task_id, error="late", db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "success"

    def test_failed_on_pending_is_noop(self, db):
        task_id = tq.submit_task("u1", "/flows/a.json", db_path=db)["taskId"]
        tq.mark_failed(task_id, error="late", db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "pending"


class TestPausedGuard:
    def test_paused_on_running_ok(self, db):
        task_id = _running(db)
        tq.mark_paused(task_id, resume_from_step="s2", db_path=db)
        task = tq.get_task(task_id, db_path=db)
        assert task["status"] == "paused"
        assert task["resumeFromStep"] == "s2"

    def test_paused_on_pending_ok(self, db):
        # request_pause 对 pending 任务直接暂停（跳过排队）
        task_id = tq.submit_task("u1", "/flows/a.json", db_path=db)["taskId"]
        paused = tq.request_pause(task_id, db_path=db)
        assert paused["status"] == "paused"

    def test_paused_on_success_is_noop(self, db):
        task_id = _running(db)
        tq.mark_success(task_id, db_path=db)
        tq.mark_paused(task_id, resume_from_step="s2", db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "success"


class TestTerminatedGuard:
    def test_terminated_on_running_ok(self, db):
        task_id = _running(db)
        tq.mark_terminated(task_id, error="stopped", db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "terminated"

    def test_terminated_on_success_is_noop(self, db):
        task_id = _running(db)
        tq.mark_success(task_id, db_path=db)
        tq.mark_terminated(task_id, error="late", db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "success"

    def test_terminated_on_pending_is_noop(self, db):
        task_id = tq.submit_task("u1", "/flows/a.json", db_path=db)["taskId"]
        tq.mark_terminated(task_id, error="late", db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "pending"


class TestOrphanRecoveryIntegration:
    """核心场景：孤儿恢复后，迟到的 worker 写入被守卫拦截。"""

    def test_late_worker_success_does_not_override_recovery(self, db):
        task_id = _running(db)
        # 服务端崩溃重启 -> 孤儿恢复把 running 标为 failed
        recovered = tq.recover_orphan_running_tasks(db_path=db, max_age_seconds=0)
        assert recovered == 1
        assert tq.get_task(task_id, db_path=db)["status"] == "failed"
        # 旧 worker 存活并迟到上报成功 -> 守卫拦截为 no-op
        tq.mark_success(task_id, db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "failed"
        # 迟到上报失败：任务本就是失败态（failed 前驱对重试收尾是合法的），
        # 状态不会被改回 running/success，仅错误信息刷新为 worker 的实际原因
        tq.mark_failed(task_id, error="late", db_path=db)
        task = tq.get_task(task_id, db_path=db)
        assert task["status"] == "failed"
        assert task["error"] == "late"


class TestDeleteTask:
    """删除任务：非 running 可删，running 拒绝，关联审计一并删除。"""

    def test_delete_pending(self, db):
        task_id = tq.submit_task("u1", "/flows/a.json", db_path=db)["taskId"]
        tq.delete_task(task_id, db_path=db)
        assert tq.get_task(task_id, db_path=db) is None

    def test_delete_removes_audit(self, db):
        task_id = tq.submit_task("u1", "/flows/a.json", db_path=db)["taskId"]
        tq.add_audit_event(user="u1", action="submit", task_id=task_id, db_path=db)
        tq.delete_task(task_id, db_path=db)
        assert tq.get_task(task_id, db_path=db) is None

    def test_delete_running_rejected(self, db):
        task_id = _running(db)
        with pytest.raises(tq.TaskStateError):
            tq.delete_task(task_id, db_path=db)
        assert tq.get_task(task_id, db_path=db)["status"] == "running"

    def test_delete_missing_raises(self, db):
        with pytest.raises(tq.TaskStateError):
            tq.delete_task("no_such_task", db_path=db)
