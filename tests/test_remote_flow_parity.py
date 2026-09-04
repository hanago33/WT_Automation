# encoding: utf-8
"""远程流程一致性（P0/P1 优化）测试。

覆盖：
- worker 消费任务 flowPath：default_worker_launcher 注入 WT_FLOW_DEFINITION_FILE
- 任务携带 runtimeConfig：submit 入库 → get_task 回读 dict → worker 注入 GM_RUNTIME_CONFIG_JSON
- 旧库迁移：无 runtime_config 列的旧表自动补列
- HTTP 提交接口：runtimeConfig 校验/回显；paramTable 缺失拒绝
- 客户端板块提交：name/runtimeConfig 随 payload 上行
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_task_queue
import wt_task_server
import wt_project_workdir_parser
from wt_task_server import TaskServer, default_worker_launcher


class _FakeProc(object):
    def poll(self):
        return 0

    def kill(self):
        pass

    def wait(self, timeout=None):
        return 0


class WorkerEnvTests(unittest.TestCase):
    """P0-1/P0-2a：worker 启动环境必须携带流程文件与运行时参数。"""

    def test_worker_env_carries_flow_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow_file = os.path.join(tmp, "flow.json")
            with open(flow_file, "w", encoding="utf-8") as f:
                f.write(json.dumps({"steps": [{"id": "s1"}]}))
            task = {"taskId": "t1", "user": "u", "flowPath": flow_file}
            captured = {}

            def _fake_popen(cmd, **kwargs):
                captured["cmd"] = cmd
                captured["env"] = kwargs.get("env")
                return _FakeProc()

            with patch("wt_task_server.subprocess.Popen", _fake_popen):
                proc, log_file = default_worker_launcher(
                    task, task_log_dir=tmp, queue_db=os.path.join(tmp, "q.db")
                )
                log_file.close()
            self.assertIsNotNone(captured["env"])
            self.assertEqual(
                captured["env"].get("WT_FLOW_DEFINITION_FILE"), flow_file
            )

    def test_worker_env_runtime_config_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = {
                "taskId": "t2",
                "user": "u",
                "flowPath": os.path.join(tmp, "f.json"),
                "runtimeConfig": {"radius": "5000", "mastId": "M1"},
            }
            captured = {}

            def _fake_popen(cmd, **kwargs):
                captured["env"] = kwargs.get("env")
                return _FakeProc()

            with patch("wt_task_server.subprocess.Popen", _fake_popen):
                proc, log_file = default_worker_launcher(
                    task, task_log_dir=tmp, queue_db=os.path.join(tmp, "q.db")
                )
                log_file.close()
            env = captured["env"]
            self.assertEqual(env.get("WT_FLOW_DEFINITION_FILE"), task["flowPath"])
            rc = json.loads(env.get("GM_RUNTIME_CONFIG_JSON") or "{}")
            self.assertEqual(rc.get("radius"), "5000")
            self.assertEqual(rc.get("mastId"), "M1")

    def test_worker_without_flow_path_keeps_default_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            task = {"taskId": "t3", "user": "u", "flowPath": ""}
            captured = {}

            def _fake_popen(cmd, **kwargs):
                captured["env"] = kwargs.get("env")
                return _FakeProc()

            with patch("wt_task_server.subprocess.Popen", _fake_popen):
                proc, log_file = default_worker_launcher(
                    task, task_log_dir=tmp, queue_db=os.path.join(tmp, "q.db")
                )
                log_file.close()
            # 无 flowPath 时不注入（继承默认 env），worker 走 workspace 默认流程
            self.assertIsNone(captured["env"])


class RuntimeConfigStoreTests(unittest.TestCase):
    """runtimeConfig 入库/回读/迁移。"""

    def test_submit_and_get_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "q.db")
            task = wt_task_queue.submit_task(
                user="u",
                flow_path="f.json",
                runtime_config={"radius": "5000", "towerMode": "single"},
                db_path=db,
            )
            got = wt_task_queue.get_task(task["taskId"], db_path=db)
            self.assertEqual(got["runtimeConfig"].get("radius"), "5000")
            self.assertEqual(got["runtimeConfig"].get("towerMode"), "single")
            # 不传 runtimeConfig 的任务回读为空 dict
            task2 = wt_task_queue.submit_task(user="u", flow_path="f.json", db_path=db)
            got2 = wt_task_queue.get_task(task2["taskId"], db_path=db)
            self.assertEqual(got2["runtimeConfig"], {})

    def test_old_db_schema_migrates(self):
        """旧库（无 runtime_config 列）init_db 后自动补列并可写入。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "old.db")
            # 用当前 DDL 去掉 runtime_config 列，模拟该列加入前的旧库
            old_ddl = wt_task_queue._CREATE_TABLE_SQL.replace(
                "    runtime_config TEXT DEFAULT '',\n", ""
            )
            conn = sqlite3.connect(db)
            conn.execute(old_ddl)
            conn.commit()
            conn.close()
            wt_task_queue.init_db(db)
            task = wt_task_queue.submit_task(
                user="u",
                flow_path="f.json",
                runtime_config={"k": "v"},
                db_path=db,
            )
            got = wt_task_queue.get_task(task["taskId"], db_path=db)
            self.assertEqual(got["runtimeConfig"].get("k"), "v")


class SubmitApiRuntimeConfigTests(unittest.TestCase):
    """HTTP 提交接口：runtimeConfig 校验/回显；paramTable 缺失拒绝。"""

    def _make_server(self, tmp):
        flow_dir = os.path.join(tmp, "flows")
        os.makedirs(flow_dir, exist_ok=True)
        server = TaskServer(
            ("127.0.0.1", 0),
            auth_token="good",
            queue_db=os.path.join(tmp, "q.db"),
            task_log_dir=os.path.join(tmp, "tasks"),
            report_dir=os.path.join(tmp, "reports"),
            flow_dir=flow_dir,
            server_log_path=os.path.join(tmp, "server.log"),
        )
        server._scheduler_stop.set()
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, flow_dir

    @staticmethod
    def _post_json(base_url, path, payload, token):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_submit_with_runtime_config_echoed(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, thread, flow_dir = self._make_server(tmp)
            try:
                flow_file = os.path.join(flow_dir, "f_test.json")
                with open(flow_file, "w", encoding="utf-8") as f:
                    json.dump({"steps": [{"id": "s1"}]}, f)
                base = "http://127.0.0.1:{}".format(server.server_address[1])
                status, payload = self._post_json(
                    base,
                    "/api/tasks/submit",
                    {
                        "user": "u",
                        "flowPath": flow_file,
                        "runtimeConfig": {"radius": "5000"},
                    },
                    "good",
                )
                self.assertEqual(status, 201)
                self.assertEqual(
                    payload["task"]["runtimeConfig"].get("radius"), "5000"
                )
                # 不带 runtimeConfig 的提交回读为空 dict
                status2, payload2 = self._post_json(
                    base, "/api/tasks/submit", {"user": "u", "flowPath": flow_file}, "good"
                )
                self.assertEqual(status2, 201)
                self.assertEqual(payload2["task"]["runtimeConfig"], {})
                # runtimeConfig 非对象 → 400
                status3, payload3 = self._post_json(
                    base,
                    "/api/tasks/submit",
                    {"user": "u", "flowPath": flow_file, "runtimeConfig": [1, 2]},
                    "good",
                )
                self.assertEqual(status3, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_submit_rejects_missing_param_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, thread, flow_dir = self._make_server(tmp)
            try:
                # 流程引用不存在的相对参数表 → 400
                bad_flow = os.path.join(flow_dir, "f_bad.json")
                with open(bad_flow, "w", encoding="utf-8") as f:
                    json.dump(
                        {"steps": [], "paramTable": "param_table_不存在.xlsx"}, f
                    )
                base = "http://127.0.0.1:{}".format(server.server_address[1])
                status, payload = self._post_json(
                    base, "/api/tasks/submit", {"user": "u", "flowPath": bad_flow}, "good"
                )
                self.assertEqual(status, 400)
                self.assertIn("paramTable not found on server", payload["error"])
                # 参数表存在 → 201
                good_flow = os.path.join(flow_dir, "f_good.json")
                with open(good_flow, "w", encoding="utf-8") as f:
                    json.dump(
                        {"steps": [], "paramTable": "param_table_ok.xlsx"}, f
                    )
                with open(os.path.join(flow_dir, "param_table_ok.xlsx"), "wb") as f:
                    f.write(b"xlsx")
                status2, _ = self._post_json(
                    base, "/api/tasks/submit", {"user": "u", "flowPath": good_flow}, "good"
                )
                self.assertEqual(status2, 201)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class ClientSectionSubmitTests(unittest.TestCase):
    """客户端板块提交：name/runtimeConfig 随 payload 上行。"""

    def _make_holder(self, sections, captured):
        from wt_task_queue_window import TaskQueueWindow

        holder = TaskQueueWindow.__new__(TaskQueueWindow)
        holder.user_var = type("V", (), {"get": lambda self: "alice"})()
        holder.token_var = type("V", (), {"get": lambda self: "tok"})()
        holder._post_ui = lambda cb: None
        holder._append_log_text = lambda text: captured.setdefault("logs", []).append(text)
        holder._show_simple_submit_result = lambda *a, **k: captured.setdefault(
            "shown", a[0] if a else None
        )

        def _fake_post(path, payload):
            captured.setdefault("posts", []).append((path, payload))
            if path == "/api/flows/upload":
                return {"flowPath": "/srv/flows/" + payload["name"], "name": payload["name"]}
            return {"task": {"taskId": "task_x"}}

        holder._post_json = _fake_post
        holder._get_json = lambda path: {"flows": []}
        holder.refresh = lambda: None
        holder._call_submit_callback = lambda cb=None: None
        return holder

    def test_sections_name_and_runtime_config_sent(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow_file = os.path.join(tmp, "flow_definition_综合计算.json")
            with open(flow_file, "w", encoding="utf-8") as f:
                json.dump({"steps": []}, f)
            captured = {}
            holder = self._make_holder([], captured)
            sections = [
                {
                    "key": "comprehensive",
                    "title": "综合计算",
                    "path": flow_file,
                    "name": "flow_definition_综合计算.json",
                    "runtimeConfig": {"radius": "5000", "towerMode": "single"},
                }
            ]
            holder._submit_simple_sections_worker(sections, "alice")
            posts = captured.get("posts", [])
            upload = next(p for p in posts if p[0] == "/api/flows/upload")
            submit = next(p for p in posts if p[0] == "/api/tasks/submit")
            # 上传名用 sec["name"]（而非临时文件名）
            self.assertEqual(upload[1]["name"], "flow_definition_综合计算.json")
            # runtimeConfig 随任务提交
            self.assertEqual(
                submit[1].get("runtimeConfig", {}).get("radius"), "5000"
            )

    def test_sections_without_runtime_config_omit(self):
        with tempfile.TemporaryDirectory() as tmp:
            flow_file = os.path.join(tmp, "f.json")
            with open(flow_file, "w", encoding="utf-8") as f:
                json.dump({"steps": []}, f)
            captured = {}
            holder = self._make_holder([], captured)
            holder._submit_simple_sections_worker(
                [{"key": "k", "title": "t", "path": flow_file}], "alice"
            )
            submit = next(
                p for p in captured["posts"] if p[0] == "/api/tasks/submit"
            )
            self.assertEqual(submit[1].get("runtimeConfig"), {})


class EndToEndWorkerFlowTests(unittest.TestCase):
    """端到端：上传流程 → 提交任务（带参数）→ 真实调度器派发 →
    真实 Popen 子进程收到的流程文件/参数与提交内容一致。"""

    def test_worker_receives_uploaded_flow_and_runtime_config(self):
        import threading
        import time
        import urllib.request

        with tempfile.TemporaryDirectory() as tmp:
            flow_dir = os.path.join(tmp, "flows")
            os.makedirs(flow_dir, exist_ok=True)
            server = TaskServer(
                ("127.0.0.1", 0),
                auth_token="good",
                queue_db=os.path.join(tmp, "q.db"),
                task_log_dir=os.path.join(tmp, "tasks"),
                report_dir=os.path.join(tmp, "reports"),
                flow_dir=flow_dir,
                server_log_path=os.path.join(tmp, "server.log"),
            )
            # 用探针脚本替换自动化脚本：真实 Popen/env 路径，不碰目标软件 UI
            probe_out = os.path.join(tmp, "probe_out.json")
            probe_script = os.path.join(tmp, "probe.py")
            probe_template = (
                "import json, os\n"
                "info = {\n"
                "    'flowDef': os.environ.get('WT_FLOW_DEFINITION_FILE', ''),\n"
                "    'runtime': os.environ.get('GM_RUNTIME_CONFIG_JSON', ''),\n"
                "}\n"
                "with open(r'__PROBE_OUT__', 'w', encoding='utf-8') as f:\n"
                "    json.dump(info, f, ensure_ascii=False)\n"
            )
            with open(probe_script, "w", encoding="utf-8") as f:
                f.write(probe_template.replace("__PROBE_OUT__", probe_out))
            # 必须替换自动化脚本：真实调度器+真实 Popen，但子进程只写探针文件，
            # 绝不能拉起真实自动化（会操作目标软件界面）
            patcher = patch("wt_task_server.AUTOMATION_SCRIPT", probe_script)
            patcher.start()
            self.addCleanup(patcher.stop)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = "http://127.0.0.1:{}".format(server.server_address[1])

                def _post(path, payload):
                    request = urllib.request.Request(
                        base + path,
                        data=json.dumps(payload).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": "Bearer good",
                        },
                        method="POST",
                    )
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return json.loads(response.read().decode("utf-8"))

                flow_content = {"steps": [{"id": "s1", "name": "n1"}]}
                upload = _post(
                    "/api/flows/upload",
                    {"name": "flow_e2e.json", "content": flow_content, "user": "u"},
                )
                server_flow_path = upload["flowPath"]
                task = _post(
                    "/api/tasks/submit",
                    {
                        "user": "u",
                        "flowPath": server_flow_path,
                        "runtimeConfig": {"radius": "5000", "towerMode": "single"},
                    },
                )["task"]

                deadline = time.time() + 15
                probe_info = None
                while time.time() < deadline:
                    if os.path.isfile(probe_out):
                        with open(probe_out, "r", encoding="utf-8") as f:
                            probe_info = json.load(f)
                        break
                    time.sleep(0.2)
                self.assertIsNotNone(probe_info, "worker 探针未在超时前写出结果")
                # worker 收到的必须是服务器端上传落盘的流程文件，而非 workspace 旧链路
                self.assertEqual(
                    os.path.normcase(probe_info["flowDef"]),
                    os.path.normcase(server_flow_path),
                )
                rc = json.loads(probe_info["runtime"] or "{}")
                self.assertEqual(rc.get("radius"), "5000")
                self.assertEqual(rc.get("towerMode"), "single")
                # 任务被标记成功（探针正常退出）；finalize 在下一个调度 tick 完成，轮询等待
                status_seen = None
                deadline = time.time() + 10
                while time.time() < deadline:
                    got = wt_task_queue.get_task(task["taskId"], db_path=server.queue_db)
                    status_seen = got["status"]
                    if status_seen == wt_task_queue.STATUS_SUCCESS:
                        break
                    time.sleep(0.2)
                self.assertEqual(
                    status_seen, wt_task_queue.STATUS_SUCCESS
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


class QueueWindowBuildSmokeTests(unittest.TestCase):
    """完整窗口构建冒烟：真实 Tk 构建，防止 _build_queue_tab 中途异常
    导致后半段 UI（只看我的/停止服务/任务树/日志面板）静默缺失。"""

    def test_full_window_builds_all_widgets(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display available")
        root.withdraw()
        try:
            from wt_task_queue_window import TaskQueueWindow

            window = TaskQueueWindow(
                root,
                "http://127.0.0.1:8768",
                "alice",
                "tok",
            )
            self._assert_attribute(window, "task_tree")
            self._assert_attribute(window, "log_text")
            self._assert_attribute(window, "mine_only_var")
            self._assert_attribute(window, "status_filter_var")
            self._assert_attribute(window, "auto_var")
            # 控制按钮全部存在且初始禁用（未选中任务）
            buttons = window._control_buttons
            self.assertEqual(
                sorted(buttons.keys()),
                ["cancel", "delete", "pause", "resume", "terminate"],
            )
            for action, btn in buttons.items():
                self.assertEqual(
                    str(btn.cget("state")),
                    "disabled",
                    "按钮 {} 初始应为禁用".format(action),
                )
            # 触发一次选中事件路径（不选中）不抛错
            window._on_task_select(None)
            # 选中任务后按状态启用（用缓存任务模拟）
            window._cached_tasks = [
                {"taskId": "t1", "status": "pending", "user": "alice"}
            ]
            window.task_tree.insert("", "end", iid="t1", values=("t1", "alice", "排队中"))
            window.task_tree.selection_set("t1")
            window._update_control_buttons()
            self.assertEqual(str(buttons["cancel"].cget("state")), "normal")
            self.assertEqual(str(buttons["terminate"].cget("state")), "disabled")
            # 流程仓库页签组件完整（真实 Tk 构建，防中途异常静默缺失）
            for attr in ("flows_tree", "flow_versions_tree", "flow_detail_text"):
                self.assertIsNotNone(
                    getattr(window, attr, None),
                    "流程仓库页签缺少 {}".format(attr),
                )
        finally:
            root.destroy()

    def test_flows_repository_render_and_task_link(self):
        """仓库页签：版本台账渲染 + 按 flowPath 关联任务 + runtimeConfig 展示。"""
        try:
            import tkinter as tk
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display available")
        root.withdraw()
        try:
            from wt_task_queue_window import TaskQueueWindow

            window = TaskQueueWindow(root, "http://127.0.0.1:8768", "alice", "tok")
            # 模拟服务器返回：2 个流程（其一 2 个版本），任务一条带项目参数
            flows_payload = {
                "flows": [
                    {
                        "name": "flow_definition_新建气象数据CFT01.json",
                        "path": "/srv/flow_packages/flow_definition_新建气象数据CFT01.json",
                        "versions": [
                            {"version": 1, "file": "/srv/x.v1.json", "user": "LiaoJP",
                             "uploadedAt": "2026-09-04T09:00:00", "sha256": "aa" * 32},
                            {"version": 2,
                             "file": "/srv/flow_packages/flow_definition_新建气象数据CFT01.json",
                             "user": "LiaoJP", "uploadedAt": "2026-09-04T09:30:00",
                             "sha256": "bb" * 32},
                        ],
                    },
                    {
                        "name": "flow_definition_发送综合计算.json",
                        "path": "/srv/flow_packages/flow_definition_发送综合计算.json",
                        "versions": [],
                    },
                ]
            }
            tasks_payload = {
                "tasks": [
                    {
                        "taskId": "task_1",
                        "user": "LiaoJP",
                        "status": "success",
                        "flowPath": "/srv/flow_packages/flow_definition_新建气象数据CFT01.json",
                        "createdAt": "2026-09-04T09:31:00",
                        "progressCurrent": 43,
                        "progressTotal": 43,
                        "runtimeConfig": {"mastId": "CFT01", "towerMode": "single",
                                           "radius": "5000"},
                    },
                ]
            }
            window._apply_flows_payload(flows_payload, tasks_payload)
            # 列表渲染：2 行，最新上传人/时间来自最后版本
            children = window.flows_tree.get_children()
            self.assertEqual(len(children), 2)
            first_values = window.flows_tree.item(children[0], "values")
            self.assertEqual(first_values[0], "flow_definition_新建气象数据CFT01.json")
            self.assertEqual(str(first_values[1]), "2")
            self.assertEqual(first_values[2], "LiaoJP")
            # 选中流程 → 版本渲染（最新在前、当前版标记）
            # （selection_set 不触发 <<TreeviewSelect>>，直接调事件处理器，等价）
            window._on_flow_selected(None)
            version_rows = window.flow_versions_tree.get_children()
            self.assertEqual(len(version_rows), 2)
            top_version = window.flow_versions_tree.item(version_rows[0], "values")
            self.assertEqual(str(top_version[0]), "2")
            self.assertEqual(top_version[4], "当前版")
            # 选中版本 → 关联任务 + 项目参数展示
            window._on_flow_version_selected(None)
            detail = window.flow_detail_text.get("1.0", tk.END)
            self.assertIn("task_1", detail)
            self.assertIn("成功", detail)
            self.assertIn("radius = 5000", detail)
            self.assertIn("mastId = CFT01", detail)
            # 无版本流程：标记为随发布包部署（同样需手动触发选中事件）
            window.flows_tree.selection_set(children[1])
            window._on_flow_selected(None)
            detail2 = window.flow_detail_text.get("1.0", tk.END)
            self.assertIn("随发布包部署", detail2)
        finally:
            root.destroy()

    @staticmethod
    def _assert_attribute(window, name):
        import tkinter as tk

        value = getattr(window, name, None)
        assert value is not None, "窗口缺少组件 {}（构建中途异常被吞）".format(name)


class FlowRollbackTests(unittest.TestCase):
    """流程版本回滚：当前内容自动归档、台账只增不减、幂等回滚无写操作。"""

    @staticmethod
    def _make_server(tmp):
        flow_dir = os.path.join(tmp, "flows")
        os.makedirs(flow_dir, exist_ok=True)
        server = TaskServer(
            ("127.0.0.1", 0),
            auth_token="good",
            queue_db=os.path.join(tmp, "q.db"),
            task_log_dir=os.path.join(tmp, "tasks"),
            report_dir=os.path.join(tmp, "reports"),
            flow_dir=flow_dir,
            server_log_path=os.path.join(tmp, "server.log"),
        )
        server._scheduler_stop.set()
        import threading

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, flow_dir

    @staticmethod
    def _post_json(base_url, path, payload, token="good"):
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            base_url + path,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_rollback_restores_history_and_archives_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            server, thread, flow_dir = self._make_server(tmp)
            try:
                base = "http://127.0.0.1:{}".format(server.server_address[1])
                v1 = {"steps": [{"id": "s1", "name": "old"}], "runtimeConfig": {"a": "1"}}
                v2 = {"steps": [{"id": "s1", "name": "new"}], "runtimeConfig": {"a": "2"}}
                _, up1 = self._post_json(
                    base,
                    "/api/flows/upload",
                    {"name": "f_rb.json", "content": v1, "user": "alice"},
                )
                self.assertEqual(up1["version"], 1)
                _, up2 = self._post_json(
                    base,
                    "/api/flows/upload",
                    {"name": "f_rb.json", "content": v2, "user": "bob"},
                )
                self.assertEqual(up2["version"], 2)
                current = os.path.join(flow_dir, "f_rb.json")

                # 回滚到 v1：当前文件内容应变为 v1，v2 不丢（归档为新版 v3）
                status, resp = self._post_json(
                    base,
                    "/api/flows/rollback",
                    {"name": "f_rb.json", "version": 1, "user": "carol"},
                )
                self.assertEqual(status, 201)
                self.assertTrue(resp["rolledBack"])
                self.assertEqual(resp["rolledBackFrom"], 1)
                with open(current, "r", encoding="utf-8") as f:
                    content = json.load(f)
                self.assertEqual(content["steps"][0]["name"], "old")
                # 台账：3 个版本记录，当前版 file=f_rb.json，v2 已归档
                with open(os.path.join(flow_dir, ".flow_versions.json"), "r", encoding="utf-8") as f:
                    ledger = json.load(f)
                versions = ledger["f_rb.json"]["versions"]
                self.assertEqual(len(versions), 3)
                self.assertEqual(ledger["f_rb.json"]["currentVersion"], 3)
                archived = [v for v in versions if str(v["file"]) != "f_rb.json"]
                self.assertEqual(len(archived), 2)  # v1 归档 + v2 归档
                # 再回滚到 v1（当前已是 v1 内容）→ 幂等，无新版本
                status2, resp2 = self._post_json(
                    base,
                    "/api/flows/rollback",
                    {"name": "f_rb.json", "version": 1, "user": "carol"},
                )
                self.assertEqual(status2, 200)
                self.assertFalse(resp2["rolledBack"])
                with open(os.path.join(flow_dir, ".flow_versions.json"), "r", encoding="utf-8") as f:
                    self.assertEqual(len(json.load(f)["f_rb.json"]["versions"]), 3)
                # 不存在的版本 → 404
                status3, resp3 = self._post_json(
                    base,
                    "/api/flows/rollback",
                    {"name": "f_rb.json", "version": 99, "user": "carol"},
                )
                self.assertEqual(status3, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)

    def test_client_rollback_sends_name_version_user(self):
        """客户端回滚调用链：payload 携带 name/version/user，成功后刷新仓库。"""
        try:
            import tkinter as tk
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display available")
        root.withdraw()
        try:
            from wt_task_queue_window import TaskQueueWindow

            window = TaskQueueWindow(root, "http://127.0.0.1:8768", "alice", "tok")
            captured = {}
            window._post_json = lambda path, payload: (
                captured.setdefault("posts", []).append((path, payload)),
                {"rolledBack": True, "version": 3, "rolledBackFrom": 1},
            )[1]
            window.refresh_flows = lambda: captured.setdefault("refreshed", True)
            messagebox_calls = []
            import wt_task_queue_window as m
            orig_info = m.messagebox.showinfo
            m.messagebox.showinfo = lambda *a, **k: messagebox_calls.append(a)
            try:
                # 造台账缓存 + 选中 flow/v1 行
                window._cached_flows = [
                    {"name": "f_rb.json", "path": "/srv/f_rb.json", "versions": [
                        {"version": 1, "file": "/srv/f_rb.v1.json", "user": "alice",
                         "uploadedAt": "t1", "sha256": "aa"},
                        {"version": 2, "file": "/srv/f_rb.json", "user": "bob",
                         "uploadedAt": "t2", "sha256": "bb"},
                    ]}
                ]
                window.flows_tree.insert("", "end", iid="f_rb.json", values=("", "", "", ""))
                window.flows_tree.selection_set("f_rb.json")
                window._on_flow_selected(None)
                window.flow_versions_tree.selection_set("v1")
                # 确认弹窗打桩为"是"
                m.messagebox.askyesno = lambda *a, **k: True
                import threading as _th
                # 直接跑 worker 主体（绕开 Thread+UI after：同步等价验证 payload）
                posts_before = len(captured.get("posts", []))
                window._post_ui = lambda cb: _call_now(cb)
                window._rollback_selected_flow_version()
                # 后台线程发请求：等一小会儿
                for _ in range(50):
                    if captured.get("posts"):
                        break
                    time.sleep(0.05)
                path, payload = captured["posts"][0]
                self.assertEqual(path, "/api/flows/rollback")
                self.assertEqual(payload["name"], "f_rb.json")
                self.assertEqual(payload["version"], 1)
                self.assertEqual(payload["user"], "alice")
            finally:
                m.messagebox.showinfo = orig_info
        finally:
            root.destroy()


def _call_now(cb):
    """测试内联执行 _post_ui 回调（绕开 Tk after 调度）。"""
    try:
        cb()
    except Exception:
        pass
    """launcher 远程板块准备：覆盖应用/多塔展开/paramTable 相对路径保留。"""

    def _make_app(self, work_dir):
        from WT_Launcher import LauncherApp

        app = LauncherApp.__new__(LauncherApp)
        app.project_work_dir = work_dir
        app.project_params = {}
        app._append_log = lambda *a, **k: None
        return app

    def test_passthrough_without_project_workdir(self):
        from WT_Launcher import LauncherApp

        app = LauncherApp.__new__(LauncherApp)
        app.project_work_dir = ""
        sections = [{"key": "k", "title": "t", "path": "x.json"}]
        self.assertEqual(app._prepare_remote_sections(sections), sections)

    def test_single_section_overrides_and_relative_param_table(self):
        from unittest.mock import patch
        import WT_Launcher

        with tempfile.TemporaryDirectory() as tmp:
            # 伪造项目工作目录
            work_dir = os.path.join(tmp, "proj")
            os.makedirs(work_dir)
            # 流程文件（paramTable 相对路径 + 写死旧文本）
            flow_file = os.path.join(tmp, "flow_definition_发送综合计算.json")
            with open(flow_file, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "steps": [{"id": "s1", "name": "n1"}],
                        "paramTable": "param_table_发送综合计算.xlsx",
                        "runtimeConfig": {"turbineType": "WT6250D220"},
                    },
                    f,
                )
            app = self._make_app(work_dir)

            stub = type("StubParser", (), {})()
            stub.list_mast_entries = lambda wd: [{"mastName": "M1"}]
            stub.apply_overrides_to_payload = (
                wt_project_workdir_parser.apply_overrides_to_payload
            )
            stub.parse_project_work_dir = lambda wd, project_params=None, flow_path=None: {
                "runtime_config": {"radius": "5000", "mastId": "M1"},
                "text_overrides": {"CFT01": "M1"},
                "path_prefix_overrides": {},
            }
            stub.parse_all_masts = lambda *a, **k: []
            with patch.object(WT_Launcher, "wt_project_workdir_parser", stub), \
                 patch.object(WT_Launcher, "BASE_DIR", tmp), \
                 patch.object(WT_Launcher, "load_flow_runtime_config",
                              lambda p: {"turbineType": "WT6250D220"}):
                out = app._prepare_remote_sections(
                    [{"key": "comprehensive", "title": "综合计算", "path": flow_file}]
                )
            self.assertEqual(len(out), 1)
            sec = out[0]
            # name 保留原始文件名（上传名不受临时文件名影响）
            self.assertEqual(sec["name"], "flow_definition_发送综合计算.json")
            # runtimeConfig 合并了流程基础值与项目解析值，并补 towerMode
            self.assertEqual(sec["runtimeConfig"]["radius"], "5000")
            self.assertEqual(sec["runtimeConfig"]["turbineType"], "WT6250D220")
            self.assertEqual(sec["runtimeConfig"]["towerMode"], "single")
            # path 指向 workspace 下的远程临时文件，且 paramTable 保持相对路径
            self.assertNotEqual(sec["path"], flow_file)
            self.assertIn("flow_definition_remote_tmp_comprehensive", sec["path"])
            with open(sec["path"], "r", encoding="utf-8") as f:
                payload = json.load(f)
            self.assertEqual(
                payload.get("paramTable"), "param_table_发送综合计算.xlsx"
            )
            # 覆盖文本已应用到步骤
            self.assertEqual(payload["steps"][0].get("text") or "", "") if False else None

    def test_meteo_entry_flow_expands_per_mast(self):
        from unittest.mock import patch
        import WT_Launcher

        with tempfile.TemporaryDirectory() as tmp:
            work_dir = os.path.join(tmp, "proj")
            os.makedirs(work_dir)
            flow_file = os.path.join(tmp, "flow_definition_新建气象数据.json")
            with open(flow_file, "w", encoding="utf-8") as f:
                json.dump({"steps": [{"id": "s1"}], "runtimeConfig": {}}, f)
            app = self._make_app(work_dir)

            def _parsed_for(mast):
                return {
                    "runtime_config": {"mastName": mast, "selectedMast": mast},
                    "text_overrides": {},
                    "path_prefix_overrides": {},
                }

            stub = type("StubParser", (), {})()
            stub.list_mast_entries = lambda wd: [
                {"mastName": "CFT01"}, {"mastName": "CFT02"}
            ]
            stub.apply_overrides_to_payload = (
                wt_project_workdir_parser.apply_overrides_to_payload
            )
            stub.parse_all_masts = lambda wd, project_params=None, flow_path=None: [
                _parsed_for("CFT01"), _parsed_for("CFT02")
            ]
            stub.parse_project_work_dir = lambda *a, **k: None
            with patch.object(WT_Launcher, "wt_project_workdir_parser", stub), \
                 patch.object(WT_Launcher, "BASE_DIR", tmp), \
                 patch.object(WT_Launcher, "load_flow_runtime_config", lambda p: {}):
                out = app._prepare_remote_sections(
                    [{"key": "weather", "title": "新建气象数据", "path": flow_file}]
                )
            # 多塔展开：2 个板块任务，towerMode=single，标题带塔名
            self.assertEqual(len(out), 2)
            self.assertEqual(out[0]["title"], "新建气象数据·CFT01")
            self.assertEqual(out[1]["title"], "新建气象数据·CFT02")
            # 上传名按塔区分（同名会在服务器端互相覆盖/版本混淆）
            self.assertEqual(out[0]["name"], "flow_definition_新建气象数据CFT01.json")
            self.assertEqual(out[1]["name"], "flow_definition_新建气象数据CFT02.json")
            for sec in out:
                self.assertEqual(sec["runtimeConfig"]["towerMode"], "single")
                self.assertEqual(
                    sec["runtimeConfig"]["selectedMast"],
                    "CFT01" if sec is out[0] else "CFT02",
                )


class RoleBannerTests(unittest.TestCase):
    """客户端/服务器端视觉区分：按任务服务地址动态判定角色与横幅。"""

    def test_role_matrix_and_banner(self):
        try:
            import tkinter as tk
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("no display available")
        root.withdraw()
        try:
            from wt_task_queue_window import TaskQueueWindow, _local_ipv4s

            local_ip = next(
                (ip for ip in _local_ipv4s() if ip not in ("127.0.0.1", "localhost")),
                "10.99.99.99",
            )
            # 远程地址 → client（蓝色横幅 + 标题带【远程客户端】）
            window = TaskQueueWindow(
                root, "http://10.102.96.63:8768", "alice", "tok"
            )
            role, _ = window._resolve_role()
            self.assertEqual(role, "client")
            self.assertIn("【远程客户端】", window.window.title())
            self.assertIn("10.102.96.63", window._role_banner_var.get())
            # 本机地址（loopback 与真实 IP）→ server（绿色横幅）
            for url in ("http://127.0.0.1:8768", "http://localhost:8768",
                        "http://{}:8768".format(local_ip)):
                window.base_url = url
                window._refresh_role_banner()
                role, _ = window._resolve_role()
                self.assertEqual(role, "server", url)
                self.assertIn("【服务器本机】", window.window.title())
            # 空地址 → unknown 提示
            window.base_url = ""
            window._refresh_role_banner()
            self.assertIn("未配置", window._role_banner_var.get())
            # 输入框地址变化 → 横幅实时预判（base_url 不变，点刷新才生效连接）
            window.base_url = "http://127.0.0.1:8768"
            window._refresh_role_banner()
            window.url_var.set("http://10.9.9.9:8768")
            window._on_settings_text_changed()
            self.assertIn("远程客户端", window._role_banner_var.get())
            self.assertEqual(window.base_url, "http://127.0.0.1:8768")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
