# -*- coding: utf-8 -*-
"""回环免代理守卫：conftest.py 的声明一旦失效立即暴露（而非 50+ 项服务测试集体假红）。

背景与修法见 ``tests/conftest.py``。本测试不含产品代码逻辑，只守护测试基础设施。
"""
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer


class _OKHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def test_loopback_request_bypasses_proxy():
    server = HTTPServer(("127.0.0.1", 0), _OKHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = "http://127.0.0.1:{}/".format(server.server_port)
        # 环境配置了 HTTP(S)_PROXY 时，若无 conftest 兜底，这里会走代理而失败
        with urllib.request.urlopen(url, timeout=5) as response:
            assert response.read() == b"ok"
    finally:
        server.shutdown()
        server.server_close()
