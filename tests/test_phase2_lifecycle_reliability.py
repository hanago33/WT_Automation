# encoding: utf-8
"""阶段二测试套件：任务生命周期与核心可靠性加固测试。

测试矩阵：
1. SQLite 引擎任务幂等提交键 (Idempotency Key) 查重与隔离；
2. SQLite 引擎批量任务原子入库 (All or Nothing 事务回滚)；
3. HTTP 接口单任务幂等提交与重放标识；
4. HTTP 接口批量原子提交 (POST /api/tasks/batch-submit) 成功与失败校验；
5. Windows RPA 桌面交互状态前置预检 (check_desktop_interactive) 与健康检查接口暴露。
"""

import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

import wt_task_queue
from wt_task_server import TaskServer


class TestTaskQueueIdempotencyAndBatch(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_tasks.db")
        wt_task_queue.init_db(self.db_path, force=True)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_single_task_idempotency(self):
        """测试使用幂等键提交任务防重机制。"""
        # 1. 首次提交带幂等键
        task1 = wt_task_queue.submit_task(
            user="alice",
            flow_path="flow_a.json",
            idempotency_key="key_abc_123",
            db_path=self.db_path,
        )
        self.assertIsNotNone(task1)
        self.assertEqual(task1.get("idempotencyKey"), "key_abc_123")
        self.assertFalse(task1.get("idempotentReplay", False))

        # 2. 相同用户重复提交相同幂等键 -> 返回既有任务，标记 idempotentReplay
        task2 = wt_task_queue.submit_task(
            user="alice",
            flow_path="flow_a.json",
            idempotency_key="key_abc_123",
            db_path=self.db_path,
        )
        self.assertEqual(task2["taskId"], task1["taskId"])
        self.assertTrue(task2.get("idempotentReplay"))

        # 验证数据库中确实只有 1 条记录
        all_tasks = wt_task_queue.list_tasks(db_path=self.db_path)
        self.assertEqual(len(all_tasks), 1)

        # 3. 不同用户使用相同幂等键 -> 用户间隔离，生成新任务
        task3 = wt_task_queue.submit_task(
            user="bob",
            flow_path="flow_a.json",
            idempotency_key="key_abc_123",
            db_path=self.db_path,
        )
        self.assertNotEqual(task3["taskId"], task1["taskId"])
        self.assertEqual(task3["user"], "bob")

        all_tasks = wt_task_queue.list_tasks(db_path=self.db_path)
        self.assertEqual(len(all_tasks), 2)

    def test_batch_tasks_atomic_success(self):
        """测试批量原子任务全部合法时的事务入库。"""
        specs = [
            {"user": "alice", "flow_path": "f1.json", "priority": 1, "idempotency_key": "b1"},
            {"user": "alice", "flow_path": "f2.json", "priority": 2, "idempotency_key": "b2"},
            {"user": "alice", "flow_path": "f3.json", "priority": 3, "idempotency_key": "b3"},
        ]
        results = wt_task_queue.submit_tasks_batch(specs, db_path=self.db_path)
        self.assertEqual(len(results), 3)
        self.assertEqual([t["priority"] for t in results], [1, 2, 3])

        all_tasks = wt_task_queue.list_tasks(db_path=self.db_path)
        self.assertEqual(len(all_tasks), 3)

    def test_batch_tasks_atomic_rollback_on_error(self):
        """测试批量任务中一旦有非法任务项，全量回滚且数据库零残留。"""
        specs = [
            {"user": "alice", "flow_path": "f1.json"},
            {"user": "", "flow_path": "f2.json"},  # 非法项：缺失 user
            {"user": "alice", "flow_path": "f3.json"},
        ]
        with self.assertRaises(ValueError):
            wt_task_queue.submit_tasks_batch(specs, db_path=self.db_path)

        # 必须全量回滚，库中无任何残留任务
        all_tasks = wt_task_queue.list_tasks(db_path=self.db_path)
        self.assertEqual(len(all_tasks), 0)


class TestHttpTaskServerPhase2(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.queue_db = os.path.join(self.tmp_dir.name, "queue.db")
        self.task_log_dir = os.path.join(self.tmp_dir.name, "tasks")
        self.report_dir = os.path.join(self.tmp_dir.name, "reports")
        self.flow_dir = os.path.join(self.tmp_dir.name, "flow_packages")
        self.server_log = os.path.join(self.tmp_dir.name, "server.log")
        os.makedirs(self.flow_dir, exist_ok=True)

        # 创建一个合法的流程定义文件
        self.test_flow_name = "test_flow.json"
        self.test_flow_path = os.path.join(self.flow_dir, self.test_flow_name)
        with open(self.test_flow_path, "w", encoding="utf-8") as f:
            json.dump({"name": "test_flow", "steps": ["s1", "s2"]}, f)

        self.server = TaskServer(
            ("127.0.0.1", 0),
            auth_token="test_token",
            queue_db=self.queue_db,
            task_log_dir=self.task_log_dir,
            report_dir=self.report_dir,
            flow_dir=self.flow_dir,
            server_log_path=self.server_log,
        )
        self.server._scheduler_stop.set()
        import threading

        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = "http://{}:{}".format(host, port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp_dir.cleanup()

    def _post_json(self, path, payload, token="test_token"):
        req = urllib.request.Request(
            "{}{}".format(self.base_url, path),
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": "Bearer {}".format(token),
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def _get_json(self, path, token="test_token"):
        headers = {}
        if token:
            headers["Authorization"] = "Bearer {}".format(token)
        req = urllib.request.Request("{}{}".format(self.base_url, path), headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_http_submit_idempotency_replay(self):
        """测试 HTTP 提交接口的幂等去重与重放回显。"""
        payload = {
            "user": "alice",
            "flowPath": self.test_flow_path,
            "idempotencyKey": "http_idempotent_key_001",
        }
        # 第一次提交 -> 201 Created
        status1, body1 = self._post_json("/api/tasks/submit", payload)
        self.assertEqual(status1, 201)
        task_id_1 = body1["task"]["taskId"]
        self.assertFalse(body1.get("idempotentReplay", False))

        # 携带相同 idempotencyKey 再次提交 -> 201 Created，命中重放，返回相同 taskId
        status2, body2 = self._post_json("/api/tasks/submit", payload)
        self.assertEqual(status2, 201)
        self.assertEqual(body2["task"]["taskId"], task_id_1)
        self.assertTrue(body2.get("idempotentReplay"))

    def test_http_batch_submit_endpoint(self):
        """测试 POST /api/tasks/batch-submit 批量原子提交。"""
        batch_payload = {
            "tasks": [
                {
                    "user": "alice",
                    "flowPath": self.test_flow_path,
                    "priority": 5,
                    "idempotencyKey": "batch_item_1",
                },
                {
                    "user": "alice",
                    "flowPath": self.test_flow_path,
                    "priority": 10,
                    "idempotencyKey": "batch_item_2",
                },
            ]
        }
        status, body = self._post_json("/api/tasks/batch-submit", batch_payload)
        self.assertEqual(status, 201)
        self.assertEqual(body["count"], 2)
        tasks = body["tasks"]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["priority"], 5)
        self.assertEqual(tasks[1]["priority"], 10)

    def test_http_batch_submit_validation_failure(self):
        """测试批量提交中存在非法项时报错且不写入任何任务。"""
        bad_batch_payload = {
            "tasks": [
                {
                    "user": "alice",
                    "flowPath": self.test_flow_path,
                },
                {
                    "user": "alice",
                    "flowPath": os.path.join(self.flow_dir, "non_existent_flow.json"),
                },
            ]
        }
        try:
            self._post_json("/api/tasks/batch-submit", bad_batch_payload)
            self.fail("Expected HTTPError 400")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
            err_msg = exc.read().decode("utf-8")
            self.assertIn("tasks[1]", err_msg)

        # 验证数据库无任何脏数据
        status, q_stats = self._get_json("/api/queue/stats")
        self.assertEqual(q_stats.get("total", 0), 0)

    def test_health_endpoint_includes_desktop_interactive(self):
        """测试 /api/health 包含桌面交互自检信息。"""
        status, health = self._get_json("/api/health", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertIn("desktopInteractive", health)
        self.assertIn("desktopSession", health)


class TestWindowsDesktopCheck(unittest.TestCase):
    def test_check_desktop_interactive_real_or_mock(self):
        """测试 check_desktop_interactive 正常运行与 mock 锁屏状态。"""
        # 1. 真实系统下运行（应返回有效元组）
        is_interactive, desc = wt_task_queue.check_desktop_interactive()
        self.assertIsInstance(is_interactive, bool)
        self.assertIsInstance(desc, str)

        # 2. Mock 模拟非 Windows 平台
        with mock.patch("wt_task_queue.sys.platform", "linux"):
            ok, msg = wt_task_queue.check_desktop_interactive()
            self.assertTrue(ok)
            self.assertIn("非 Windows", msg)

        # 3. Mock 模拟 Windows 桌面被锁屏（OpenInputDesktop 返回 0）
        if sys.platform == "win32":
            with mock.patch("ctypes.windll.user32.OpenInputDesktop", return_value=0):
                with mock.patch("ctypes.GetLastError", return_value=5):
                    ok, msg = wt_task_queue.check_desktop_interactive()
                    self.assertFalse(ok)
                    self.assertIn("锁定", msg)


if __name__ == "__main__":
    unittest.main()
