# encoding: utf-8

import argparse
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS_FILE = os.path.join(BASE_DIR, "logs", "run_status.json")
LOG_FILE = os.path.join(BASE_DIR, "wt_automation.log")
REPORT_FILE = os.path.join(BASE_DIR, "logs", "last_run_report.json")
DEFAULT_TAIL = 300
MAX_TAIL = 2000


def _read_json_file(file_path, default):
    try:
        with open(file_path, "r", encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return default


def _read_log_tail(file_path, tail):
    if not os.path.exists(file_path):
        return [], 0
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as file_obj:
            lines = file_obj.readlines()
    except OSError:
        return [], 0
    return [line.rstrip("\r\n") for line in lines[-tail:]], len(lines)


class MonitorHandler(BaseHTTPRequestHandler):
    server_version = "WTMonitor/1.0"

    def log_message(self, format, *args):
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _drain_body_if_any(self):
        """排空未读请求体，避免关闭连接时因残留数据触发 RST。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return
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

    def _method_not_allowed(self):
        self._drain_body_if_any()
        self._send_json(
            {"error": "method not allowed", "allowed": ["GET"]},
            status=405,
        )

    def _route_get(self, path, query):
        if path == "/api/health":
            self._send_json({"ok": True, "service": "wt_server_monitor"})
            return
        if path == "/api/status":
            payload = _read_json_file(
                getattr(self.server, "status_file", STATUS_FILE),
                None,
            )
            if payload is None:
                payload = {
                    "status": "idle",
                    "isRunning": False,
                    "activity": "等待自动化任务",
                    "lastLog": "",
                    "error": "",
                    "runId": "",
                    "startedAt": None,
                    "endedAt": None,
                    "updatedAt": "",
                    "source": "wt_server_monitor",
                }
            self._send_json(payload)
            return
        if path == "/api/logs":
            try:
                tail = max(1, min(int(query.get("tail", [DEFAULT_TAIL])[0]), MAX_TAIL))
            except (TypeError, ValueError):
                tail = DEFAULT_TAIL
            lines, total = _read_log_tail(
                getattr(self.server, "log_file", LOG_FILE),
                tail,
            )
            self._send_json({"lines": lines, "totalLines": total})
            return
        if path == "/api/report":
            payload = _read_json_file(
                getattr(self.server, "report_file", REPORT_FILE),
                None,
            )
            if payload is None:
                self._send_json({"error": "最近运行报告不存在"}, status=404)
                return
            self._send_json(payload)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        self._route_get(parsed.path, query)

    def do_POST(self):
        self._method_not_allowed()

    def do_PUT(self):
        self._method_not_allowed()

    def do_DELETE(self):
        self._method_not_allowed()

    def do_PATCH(self):
        self._method_not_allowed()

    def do_OPTIONS(self):
        self._method_not_allowed()


class MonitorServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address,
        status_file=STATUS_FILE,
        log_file=LOG_FILE,
        report_file=REPORT_FILE,
    ):
        super().__init__(server_address, MonitorHandler)
        self.status_file = status_file
        self.log_file = log_file
        self.report_file = report_file


def create_server(
    host="0.0.0.0",
    port=8767,
    status_file=STATUS_FILE,
    log_file=LOG_FILE,
    report_file=REPORT_FILE,
):
    """Create a read-only monitor server bound to the given host and port."""
    return MonitorServer(
        (host, port),
        status_file=status_file,
        log_file=log_file,
        report_file=report_file,
    )


def serve_forever(host="0.0.0.0", port=8767):
    server = create_server(host, port)
    print(f"WT server monitor listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="WT 自动化只读服务器监控服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=8767, help="监听端口")
    args = parser.parse_args()
    serve_forever(host=args.host, port=args.port)


if __name__ == "__main__":
    main()
