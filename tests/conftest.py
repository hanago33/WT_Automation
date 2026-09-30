# -*- coding: utf-8 -*-
"""pytest 全局配置（`tests/` 作用域）。

**为什么存在**：开发机 / IDE 会话会注入 ``HTTP_PROXY`` / ``HTTPS_PROXY`` /
``ALL_PROXY``（如本地 7890 代理软件），``urllib`` 会把这些设置也应用到对
``127.0.0.1`` 的回环请求上 —— 任务服务器、监控服务、可观测性等 50+ 项
真实 HTTP 测试因此整体「请求超时」假红（2026-09-28 实测：代理存在时
54 failed；声明 NO_PROXY 后 102 passed，产品代码零回归）。

**修法**：仅在本 pytest 进程内，把回环主机补充进 ``NO_PROXY`` / ``no_proxy``；
保留用户已有条目不覆盖，不写回 shell 环境（进程退出即失效）。
守卫测试见 ``tests/test_no_proxy_guard.py``。
"""

import os

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def _extend_no_proxy(value):
    items = [item.strip() for item in value.split(",") if item.strip()]
    for host in _LOOPBACK_HOSTS:
        if host not in items:
            items.append(host)
    return ",".join(items)


for _name in ("NO_PROXY", "no_proxy"):
    os.environ[_name] = _extend_no_proxy(os.environ.get(_name, ""))
