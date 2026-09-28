# encoding: utf-8
"""WT 内网队列服务一键自检脚本。

用途（把"启动服务 + 健康检查 + 连通检测"自动化，只留输入服务器 IP 这一步手动）：

  服务器上：  python wt_queue_selfcheck.py --server [--token 令牌]
      自动：检查/启动任务队列服务(8768) 与 监控服务(8767) -> 健康检查 -> 打印本机 IP 与令牌
  本地：      python wt_queue_selfcheck.py --check 服务器IP [--token 令牌]
      自动：TCP 端口探测 -> 健康检查 -> 打印下一步指引

配套一键入口（双击即用）：
  start_queue_service.bat   服务器：一键启动 + 自检
  check_queue_link.bat      本地：输入服务器 IP 后自动检测
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

PORT = 8768
MONITOR_PORT = 8767
DEFAULT_TOKEN = "wt2026"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_SCRIPT = os.path.join(BASE_DIR, "wt_task_server.py")
MONITOR_SCRIPT = os.path.join(BASE_DIR, "wt_server_monitor.py")


def ask_yes_no(question):
    """交互确认；非交互环境默认按'是'处理，保证 bat 一键流程不被卡住。"""
    try:
        answer = input(question + " [Y/n] ").strip().lower()
        return answer in ("", "y", "yes")
    except EOFError:
        return True


def http_get(url, token=None, timeout=4):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def port_open(host, port, timeout=2):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_service(host, port, token, path, seconds=15):
    """等待服务就绪；返回 payload dict / "UNAUTHORIZED" / None(超时)。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            status, payload = http_get(
                "http://{}:{}{}".format(host, port, path), token
            )
            if status == 200:
                return payload
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return "UNAUTHORIZED"
        except Exception:
            pass
        time.sleep(0.5)
    return None


def local_ipv4s():
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ips.add("127.0.0.1")
    return sorted(ips)


def ensure_service(script, port, token, name):
    """端口未监听则后台启动对应服务（已监听则跳过，幂等）。"""
    if port_open("127.0.0.1", port):
        print("[OK] {} 端口 {} 已在监听，跳过启动。".format(name, port))
        return
    if not os.path.isfile(script):
        print("[ERROR] 未找到 {}：请确认脚本与 wt_queue_selfcheck.py 在同一目录。".format(
            os.path.basename(script)
        ))
        return
    print("启动 {} (端口 {}) ...".format(name, port))
    log_path = os.path.join(BASE_DIR, "logs", os.path.splitext(os.path.basename(script))[0] + ".log")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
    except OSError:
        pass
    cmd = [sys.executable, script]
    if token:
        cmd += ["--auth-token", token]
    with open(log_path, "a", encoding="utf-8") as log:
        subprocess.Popen(
            cmd,
            cwd=BASE_DIR,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def cmd_stop():
    """停止本机 8768/8767 上的服务进程（配套 --server 的停止入口）。"""
    killed = _kill_by_port(PORT) + _kill_by_port(MONITOR_PORT)
    if killed:
        print("[OK] 已停止 {} 个服务进程（任务队列/监控）。".format(killed))
    else:
        print("[INFO] 未发现 8768/8767 上的服务进程，无需停止。")
    return 0


def _kill_by_port(port):
    """按监听端口结束本机进程（返回被杀进程数）。"""
    pids = set()
    try:
        result = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if ":{} ".format(port) in line and "LISTENING" in line:
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(parts[-1])
    except Exception:
        return 0
    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", pid],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass
    return len(pids)


def cmd_server(token):
    print("=" * 52)
    print("  内网队列服务：一键启动 + 自检")
    print("=" * 52)
    ensure_service(SERVER_SCRIPT, PORT, token, "任务队列服务")
    ensure_service(MONITOR_SCRIPT, MONITOR_PORT, "", "监控服务")
    print("等待服务就绪 ...")

    health = wait_service("127.0.0.1", PORT, token, "/api/health")
    monitor = wait_service("127.0.0.1", MONITOR_PORT, "", "/api/status")

    if health == "UNAUTHORIZED":
        print("[FAIL] 队列服务端口通但令牌不匹配（服务可能已用别的 --auth-token 启动）。")
        print("       请用 --token 传入正确令牌，或停掉旧进程后重试。")
        return 2
    if health is None:
        print("[FAIL] 队列服务未就绪：请查看 logs/task_server.log 确认启动报错。")
        return 2
    print("[OK] 队列服务健康：{}".format(health.get("service", "?")))
    if monitor is None:
        print("[WARN] 监控服务(8767)未就绪：可忽略，总控台仍可提交任务；或查看 logs/wt_server_monitor.log。")
    else:
        print("[OK] 监控服务健康：{}".format(
            monitor.get("status") or monitor.get("service") or "ok"
        ))

    print("-" * 52)
    print("本地客户端配置：")
    print("  服务器地址：http://{}:{}/".format(local_ipv4s()[0], PORT))
    print("  本机可用 IP：{}".format(" / ".join(local_ipv4s())))
    print("  令牌：{}".format(token))
    print("  用户名：任意（如 alice）")
    print("-" * 52)
    print("下一步：在本地双击 check_queue_link.bat 输入上面的 IP，或直接打开总控台"
          "「任务与服务器监控」窗口填写连接参数。")
    return 0


def cmd_check(host, token):
    host = (host or "").strip()
    if not host:
        print("[ERROR] 缺少服务器 IP。用法：python wt_queue_selfcheck.py --check 服务器IP")
        return 2
    print("=" * 52)
    print("  内网连接检测：本地 -> {}".format(host))
    print("=" * 52)
    if not port_open(host, PORT):
        print("[FAIL] 端口 {} 不通：请确认①服务器已跑 start_queue_service.bat，"
              "②IP 正确，③服务器防火墙放行 8768 入站。".format(PORT))
        return 2
    print("[OK] TCP 端口 {} 可达".format(PORT))

    health = wait_service(host, PORT, token, "/api/health")
    if health == "UNAUTHORIZED":
        print("[FAIL] 令牌不匹配（401）：总控台/脚本填写的令牌须与服务器启动时一致（默认 {}）。".format(token))
        return 2
    if health is None:
        print("[FAIL] 端口通但健康检查失败：服务可能启动异常，请查看服务器 logs/task_server.log。")
        return 2
    print("[OK] 服务健康：{}".format(health.get("service", "?")))
    print("-" * 52)
    print("链接测试通过。下一步（仅需一次）：")
    print("  总控台 → 「任务与服务器监控」→ 地址 http://{}:{}/ → 令牌 {} → 用户名任意 → 刷新。".format(
        host, PORT, token
    ))
    print("  然后到 Simple 界面勾选板块 → 提交所选板块到远程队列，即可端到端验证。")
    return 0


def main():
    parser = argparse.ArgumentParser(description="WT 内网队列服务一键自检")
    parser.add_argument("--server", action="store_true", help="服务器上：启动并自检队列服务")
    parser.add_argument("--check", metavar="SERVER_IP", help="本地：检测到服务器的连通性")
    parser.add_argument("--stop", action="store_true", help="停止本机 8768/8767 上的服务进程")
    parser.add_argument("--force", action="store_true",
                        help="与 --server 连用：先按端口杀干净旧服务，再在当前会话启动（幂等重启）")
    parser.add_argument("--token", default=DEFAULT_TOKEN,
                        help="服务令牌（默认 {}，与实际启动参数一致才通过）".format(DEFAULT_TOKEN))
    args = parser.parse_args()
    if args.server:
        if args.force:
            print("== --force：先停止旧服务（确保服务在当前会话重启） ==")
            cmd_stop()
        return cmd_server(args.token)
    if args.check:
        return cmd_check(args.check, args.token)
    if args.stop:
        return cmd_stop()
    print("用法：")
    print("  服务器上执行： python wt_queue_selfcheck.py --server            # 启动 + 自检")
    print("  服务器上执行： python wt_queue_selfcheck.py --server --force     # 杀旧服务 + 当前会话重启（推荐）")
    print("  本地执行：     python wt_queue_selfcheck.py --check 服务器IP")
    print("  停止服务：     python wt_queue_selfcheck.py --stop")
    return 1


if __name__ == "__main__":
    sys.exit(main())
