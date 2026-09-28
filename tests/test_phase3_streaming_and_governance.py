# encoding: utf-8

"""Phase 3 Test Suite: Streaming (SSE), Task History Archival, and Dynamic ETA.

Verifies:
1. SQLite cold/hot task archival (`archive_completed_tasks`): terminal tasks older
   than N days are moved to `tasks_archive`; non-terminal (pending/running) are never archived.
2. Transparent fallback in `get_task`: archived tasks are seamlessly returned with `isArchived=True`.
3. `list_tasks` support for `include_archived=True` and multi-id lookups spanning archive.
4. Dynamic ETA calculation (`calculate_task_eta` & `estimate_task_eta`):
   - Terminal tasks (completed, failed, terminated, canceled)
   - Pending / paused tasks
   - Running tasks with progress percentage / step counts
5. HTTP maintenance endpoint: `POST /api/maintenance/archive` with auth & audit event logging.
6. HTTP SSE stream endpoint: `GET /api/tasks/{id}/events` compliance with text/event-stream,
   initial snapshot, incremental log streaming, and terminal end event.
7. SSE client disconnect protection (clean socket close without server crash).
"""

import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta

import wt_task_queue
import wt_task_server


def _pick_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestPhase3TaskArchivalAndETA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test_queue.db")
        wt_task_queue.init_db(self.db_path, force=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_archive_completed_tasks_basic(self):
        """超过指定天数的已结束任务迁移至 tasks_archive，运行中与近期任务不被归档。"""
        old_time = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
        recent_time = (datetime.now() - timedelta(days=5)).isoformat(timespec="seconds")

        # 1. 创建超期终态任务 (应被归档)
        task_old_done = wt_task_queue.submit_task(
            user="alice", flow_path="f1.json", db_path=self.db_path
        )
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, ended_at = ?, updated_at = ? WHERE task_id = ?",
                ("success", old_time, old_time, task_old_done["taskId"]),
            )
        conn.close()

        # 2. 创建近期终态任务 (不应被归档，因为 < 30 天)
        task_recent_done = wt_task_queue.submit_task(
            user="alice", flow_path="f2.json", db_path=self.db_path
        )
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, ended_at = ?, updated_at = ? WHERE task_id = ?",
                ("failed", recent_time, recent_time, task_recent_done["taskId"]),
            )
        conn.close()

        # 3. 创建超期运行中任务 (绝不能被归档，哪怕创建时间很早)
        task_old_running = wt_task_queue.submit_task(
            user="alice", flow_path="f3.json", db_path=self.db_path
        )
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, started_at = ?, created_at = ? WHERE task_id = ?",
                ("running", old_time, old_time, task_old_running["taskId"]),
            )
        conn.close()

        # 执行归档 (days=30)
        res = wt_task_queue.archive_completed_tasks(days=30, db_path=self.db_path)
        self.assertEqual(res["archivedCount"], 1)

        # 验证 tasks 表中仅剩近期任务与运行中任务
        active_tasks = wt_task_queue.list_tasks(db_path=self.db_path)
        active_ids = {t["taskId"] for t in active_tasks}
        self.assertNotIn(task_old_done["taskId"], active_ids)
        self.assertIn(task_recent_done["taskId"], active_ids)
        self.assertIn(task_old_running["taskId"], active_ids)

        # 验证 tasks_archive 表中包含已归档任务
        conn = wt_task_queue._connect(self.db_path)
        archived_row = conn.execute(
            "SELECT * FROM tasks_archive WHERE task_id = ?", (task_old_done["taskId"],)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(archived_row)
        self.assertEqual(archived_row["user"], "alice")

    def test_get_task_transparent_fallback_to_archive(self):
        """get_task 检索 tasks 未命中时自动回退 tasks_archive 并标记 isArchived=True。"""
        task = wt_task_queue.submit_task(
            user="bob", flow_path="f_arch.json", db_path=self.db_path
        )
        # 将任务标记为终态并归档 (days=0 立即归档)
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, ended_at = ? WHERE task_id = ?",
                ("success", wt_task_queue._now_iso(), task["taskId"]),
            )
        conn.close()

        wt_task_queue.archive_completed_tasks(days=0, db_path=self.db_path)

        # 查询 get_task
        got = wt_task_queue.get_task(task["taskId"], db_path=self.db_path)
        self.assertIsNotNone(got)
        self.assertEqual(got["taskId"], task["taskId"])
        self.assertTrue(got.get("isArchived"))

    def test_list_tasks_spanning_archive(self):
        """list_tasks 通过 task_ids 或 include_archived=True 能够跨表拉取活跃与归档数据。"""
        t1 = wt_task_queue.submit_task(user="carol", flow_path="f1.json", db_path=self.db_path)
        t2 = wt_task_queue.submit_task(user="carol", flow_path="f2.json", db_path=self.db_path)

        # 归档 t1
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, ended_at = ? WHERE task_id = ?",
                ("success", wt_task_queue._now_iso(), t1["taskId"]),
            )
        conn.close()
        wt_task_queue.archive_completed_tasks(days=0, db_path=self.db_path)

        # 默认 list_tasks 仅包含活跃表
        default_list = wt_task_queue.list_tasks(user="carol", db_path=self.db_path)
        self.assertEqual(len(default_list), 1)
        self.assertEqual(default_list[0]["taskId"], t2["taskId"])

        # 指定 task_ids 包含跨表任务，自动补全归档项
        multi_ids_list = wt_task_queue.list_tasks(
            task_ids=[t1["taskId"], t2["taskId"]], db_path=self.db_path
        )
        self.assertEqual(len(multi_ids_list), 2)
        archived_item = [t for t in multi_ids_list if t["taskId"] == t1["taskId"]][0]
        self.assertTrue(archived_item.get("isArchived"))

        # include_archived=True 显式包含归档项
        all_included = wt_task_queue.list_tasks(
            user="carol", include_archived=True, db_path=self.db_path
        )
        self.assertEqual(len(all_included), 2)

    def test_idempotency_cross_archive(self):
        """当原任务已被归档至 tasks_archive 时，相同 idempotency_key 提交能正确查重并返回已归档任务。"""
        t1 = wt_task_queue.submit_task(
            user="dave",
            flow_path="f_idem.json",
            idempotency_key="archived-key-001",
            db_path=self.db_path,
        )
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, ended_at = ? WHERE task_id = ?",
                ("success", wt_task_queue._now_iso(), t1["taskId"]),
            )
        conn.close()

        # 归档至 tasks_archive
        res = wt_task_queue.archive_completed_tasks(days=0, db_path=self.db_path)
        self.assertEqual(res["archivedCount"], 1)

        # 再次以相同的 idempotency_key 提交单任务
        t2 = wt_task_queue.submit_task(
            user="dave",
            flow_path="f_idem.json",
            idempotency_key="archived-key-001",
            db_path=self.db_path,
        )
        self.assertEqual(t2["taskId"], t1["taskId"])
        self.assertTrue(t2.get("isArchived"))

        # 批量提交中以相同的 idempotency_key 提交
        batch_res = wt_task_queue.submit_tasks_batch(
            [{"user": "dave", "flow_path": "f_idem.json", "idempotency_key": "archived-key-001"}],
            db_path=self.db_path,
        )
        self.assertEqual([t["taskId"] for t in batch_res], [t1["taskId"]])

    def test_calculate_task_eta_scenarios(self):
        """验证动态 ETA 计算在各种任务生命周期状态下的健壮性。"""
        # 1. 终态任务
        done_task = {
            "status": "success",
            "startedAt": "2026-09-09T10:00:00",
            "endedAt": "2026-09-09T10:02:30",
        }
        res_done = wt_task_queue.calculate_task_eta(done_task)
        self.assertTrue(res_done["isTerminal"])
        self.assertEqual(res_done["etaSeconds"], 0)
        self.assertEqual(res_done["elapsedSeconds"], 150)
        self.assertEqual(res_done["etaFormatted"], "已结束")

        # 2. 等待中任务
        pending_task = {"status": "pending", "startedAt": ""}
        res_pending = wt_task_queue.calculate_task_eta(pending_task)
        self.assertFalse(res_pending["isTerminal"])
        self.assertIsNone(res_pending["etaSeconds"])
        self.assertEqual(res_pending["etaFormatted"], "排队等待中")

        # 3. 运行中任务（根据百分比预估）
        started = (datetime.now() - timedelta(seconds=60)).isoformat(timespec="seconds")
        running_task_pct = {
            "status": "running",
            "startedAt": started,
            "progressPercent": 50.0,
        }
        res_pct = wt_task_queue.calculate_task_eta(running_task_pct)
        self.assertFalse(res_pct["isTerminal"])
        self.assertIsNotNone(res_pct["etaSeconds"])
        # 50% 耗时 60s，剩余预估应当在 55-65s 之间
        self.assertTrue(55 <= res_pct["etaSeconds"] <= 65)
        self.assertTrue("秒" in res_pct["etaFormatted"] or "分" in res_pct["etaFormatted"])

        # 4. 运行中任务（根据步骤数预估）
        running_task_steps = {
            "status": "running",
            "startedAt": started,
            "progressPercent": 0.0,
            "progressCurrent": 1,
            "stepsRequested": ["step1", "step2", "step3", "step4"],
        }
        res_steps = wt_task_queue.calculate_task_eta(running_task_steps)
        self.assertFalse(res_steps["isTerminal"])
        # 1/4 耗时 60s，总预估 240s，剩余约 180s
        self.assertIsNotNone(res_steps["etaSeconds"])
        self.assertTrue(170 <= res_steps["etaSeconds"] <= 190)


class TestPhase3HttpStreamingAndMaintenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.port = _pick_free_port()
        self.auth_token = "phase3_test_token"
        self.flow_dir = os.path.join(self.tmp.name, "flow_packages")
        os.makedirs(self.flow_dir, exist_ok=True)
        self.db_path = os.path.join(self.tmp.name, "tasks.db")
        self.log_dir = os.path.join(self.tmp.name, "task_logs")
        os.makedirs(self.log_dir, exist_ok=True)

        self.server = wt_task_server.TaskServer(
            ("127.0.0.1", 0),
            flow_dir=self.flow_dir,
            queue_db=self.db_path,
            task_log_dir=self.log_dir,
            auth_token=self.auth_token,
        )
        self.server._scheduler_stop.set()
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.15)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def _url(self, path):
        return "http://127.0.0.1:{}{}".format(self.port, path)

    def test_http_maintenance_archive_endpoint(self):
        """POST /api/maintenance/archive 鉴权校验、归档执行与审计日志写入。"""
        # 创建待归档任务
        task = wt_task_queue.submit_task(
            user="maint_user", flow_path="f.json", db_path=self.db_path
        )
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, ended_at = ? WHERE task_id = ?",
                ("success", wt_task_queue._now_iso(), task["taskId"]),
            )
        conn.close()

        # 1. 无权限请求返回 401
        req_no_auth = urllib.request.Request(
            self._url("/api/maintenance/archive"),
            data=json.dumps({"days": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req_no_auth, timeout=5)
        self.assertEqual(ctx.exception.code, 401)

        # 2. 带 Bearer Token 请求成功归档
        req_auth = urllib.request.Request(
            self._url("/api/maintenance/archive"),
            data=json.dumps({"days": 0, "limit": 500}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer {}".format(self.auth_token),
            },
        )
        resp = urllib.request.urlopen(req_auth, timeout=5)
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.read().decode("utf-8"))
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("archivedCount"), 1)

        # 3. 验证审计日志
        conn = wt_task_queue._connect(self.db_path)
        audit = conn.execute("SELECT * FROM audit_events WHERE action = 'archive_tasks'").fetchone()
        conn.close()
        self.assertIsNotNone(audit)

    def test_http_task_events_stream_lifecycle(self):
        """GET /api/tasks/{id}/events 提供实时 SSE 流式日志与状态推送。"""
        task = wt_task_queue.submit_task(
            user="stream_user", flow_path="f_stream.json", db_path=self.db_path
        )
        task_id = task["taskId"]
        log_file = os.path.join(self.log_dir, "{}.log".format(task_id))
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("Line 1: init\n")

        # 将任务置为 running
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, started_at = ? WHERE task_id = ?",
                ("running", wt_task_queue._now_iso(), task_id),
            )
        conn.close()

        url = self._url("/api/tasks/{}/events?token={}".format(task_id, self.auth_token))
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=10)

        # 1. 验证响应头符合标准 SSE
        self.assertEqual(resp.status, 200)
        self.assertIn("text/event-stream", resp.headers.get("Content-Type", ""))

        events_received = []

        def read_stream():
            current_event = "message"
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line or line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    current_event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[len("data:"):].strip())
                    events_received.append((current_event, payload))
                    if current_event == "end":
                        break

        reader_thread = threading.Thread(target=read_stream, daemon=True)
        reader_thread.start()

        # 等待接收初始状态与第一行日志
        time.sleep(0.6)
        event_types = [e[0] for e in events_received]
        self.assertIn("status", event_types)
        self.assertIn("log", event_types)

        # 追加新日志行
        with open(log_file, "a", encoding="utf-8") as f:
            f.write("Line 2: running calculations\n")

        time.sleep(0.8)

        # 结束任务
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, ended_at = ? WHERE task_id = ?",
                ("success", wt_task_queue._now_iso(), task_id),
            )
        conn.close()

        # 等待流正常结束
        reader_thread.join(timeout=4)
        resp.close()

        final_types = [e[0] for e in events_received]
        self.assertIn("end", final_types)

    def test_http_task_events_stream_client_disconnect_cleanly(self):
        """客户端中途断开 SSE 连接时，服务端安全捕获异常，绝无未捕获异常。"""
        task = wt_task_queue.submit_task(
            user="dc_user", flow_path="f_dc.json", db_path=self.db_path
        )
        task_id = task["taskId"]
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute("UPDATE tasks SET status = 'running' WHERE task_id = ?", (task_id,))
        conn.close()

        url = self._url("/api/tasks/{}/events".format(task_id))
        req = urllib.request.Request(url, headers={"Authorization": "Bearer {}".format(self.auth_token)})
        resp = urllib.request.urlopen(req, timeout=5)
        # 读取第一行立即粗暴关闭连接
        _ = resp.readline()
        resp.close()
        # 验证服务器依然健康
        time.sleep(0.5)
        health_req = urllib.request.Request(self._url("/api/health"))
        health_resp = urllib.request.urlopen(health_req, timeout=5)
        self.assertEqual(health_resp.status, 200)

    def test_http_task_events_stream_task_deleted_terminates(self):
        """若任务在 SSE 监听中途被删除，服务端应发送 not_found 并正常退出循环，绝不死循环。"""
        task = wt_task_queue.submit_task(
            user="del_user", flow_path="f_del.json", db_path=self.db_path
        )
        task_id = task["taskId"]
        url = self._url("/api/tasks/{}/events?token={}".format(task_id, self.auth_token))
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=10)

        events_received = []

        def read_stream():
            current_event = "message"
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line or line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    current_event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    payload = json.loads(line[len("data:"):].strip())
                    events_received.append((current_event, payload))
                    if current_event == "end":
                        break

        reader_thread = threading.Thread(target=read_stream, daemon=True)
        reader_thread.start()

        time.sleep(0.5)
        # 从数据库彻底删除任务
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        conn.close()

        # 等待流结束
        reader_thread.join(timeout=4)
        resp.close()

        end_events = [payload for event, payload in events_received if event == "end"]
        self.assertTrue(len(end_events) > 0)
        self.assertEqual(end_events[0].get("status"), "not_found")

    def test_daily_maintenance_auto_archive(self):
        """验证每日清理流程自动触发 tasks_archive 归档迁移。"""
        # 创建超期终态任务
        old_time = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
        t = wt_task_queue.submit_task(
            user="cleanup_user", flow_path="f_old.json", db_path=self.db_path
        )
        conn = wt_task_queue._connect(self.db_path)
        with conn:
            conn.execute(
                "UPDATE tasks SET status = ?, ended_at = ?, updated_at = ? WHERE task_id = ?",
                ("success", old_time, old_time, t["taskId"]),
            )
        conn.close()

        # 触发清理
        self.server._run_log_cleanup(force=True)

        # 检查是否已进入归档表
        conn = wt_task_queue._connect(self.db_path)
        arch = conn.execute("SELECT * FROM tasks_archive WHERE task_id = ?", (t["taskId"],)).fetchone()
        conn.close()
        self.assertIsNotNone(arch)


if __name__ == "__main__":
    unittest.main()

