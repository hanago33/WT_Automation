# encoding: utf-8
"""Test O: 新的 2000 行缓冲 vs 冻结缺陷的交互。

P3 加了 LOG_VIEW_MAX_LINES=2000 的独立缓冲，看起来绕开了 tail=300 的限制。
关键问题：WT_Launcher 的「运行日志」页数据源是**本地文件全量读取**，
还是跟 wt_task_queue_window 一样走 /api/logs?tail=300？

若是前者 -> 2000 行缓冲确实解决了"只能看尾部"的问题（且不受冻结影响）
若是后者 -> 2000 行缓冲永远填不满，且仍会冻结
"""
import os
import re
import sys

ROOT = r"C:\Users\14830\AppData\Local\Temp\dsh-pNekd0\ai5_testrun"
LAUNCHER = os.path.join(ROOT, "WT_Launcher.py")
src = open(LAUNCHER, encoding="utf-8", errors="replace").read()

print("=== WT_Launcher「运行日志」页的数据来源 ===")
hits = []
for m in re.finditer(r"^.*(api/logs|/logs\?tail|tail=|_load_recent_log|LOG_FILE).*$", src, re.M):
    line = m.group(0).strip()
    if line.startswith("#"):
        continue
    hits.append(line)

seen = set()
for h in hits:
    if h in seen:
        continue
    seen.add(h)
    print("   %s" % h[:130])

print()
print("=== 判定 ===")
uses_api = "api/logs" in src or "/logs?tail" in src
uses_local = "_load_recent_log" in src and "LOG_FILE" in src
print("  走远程 API (/api/logs?tail) : %s" % uses_api)
print("  走本地文件 (LOG_FILE)       : %s" % uses_local)

if uses_local and not uses_api:
    print()
    print("  *** 运行日志页读取的是本地 wt_automation.log 全文 ***")
    print("  *** 不受服务端 tail=300 限制，也不会命中 _append_lines_incremental 的冻结 ***")
    print("  *** 即：P3 的 2000 行缓冲客观上绕开了冻结缺陷（对总控台而言）***")
elif uses_api:
    print()
    print("  *** 仍走远程 API -> 2000 行缓冲填不满，且仍会冻结 ***")

print()
print("=== 但 wt_task_queue_window 的冻结是否仍在？===")
q = os.path.join(ROOT, "wt_task_queue_window.py")
qsrc = open(q, encoding="utf-8", errors="replace").read()
print("  _LOG_MAX_LINES = 400 存在 : %s" % ("_LOG_MAX_LINES = 400" in qsrc))
print("  /api/logs?tail 存在       : %s" % ("/api/logs?tail" in qsrc))
print("  -> 队列窗口的「运行日志」页签冻结缺陷**仍未修复**（P3 只改了总控台）")
print("RESULT=O_OK")
