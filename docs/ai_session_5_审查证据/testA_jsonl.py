# encoding: utf-8
"""Test A: 在可写的临时目录里验证 JSONL 旁路是否真的工作。

只依赖 wt_logging.py（纯标准库）。用于判定 ai5 工作区里那 12 个
JsonlSidecarTests 失败是「代码缺陷」还是「我的沙箱禁止写 ai5」的假象。
"""
import json
import os
import shutil
import sys
import tempfile

SRC = r"D:\My_RF_Project\WT_automation_ai5\wt_logging.py"
work = tempfile.mkdtemp(prefix="wt_logging_probe_")
shutil.copy2(SRC, os.path.join(work, "wt_logging.py"))
sys.path.insert(0, work)

import wt_logging  # noqa: E402

probe = os.path.join(work, "sub", "probe.jsonl")
print("work=%s" % work)

# 1) 未开启会话：no-op
print("jsonl_path(initial)=%r" % wt_logging.jsonl_path())
print("log_event without session -> %r" % (wt_logging.log_event(wt_logging.INFO, "x"),))

# 2) 开启会话（含自动建父目录）
print("start_jsonl -> %r" % wt_logging.start_jsonl(probe))

# 3) 中文不转义 + 每事件一行 + 字段省略
wt_logging.set_run_id("wt_run_20260917_102341_123_001")
wt_logging.log_event(wt_logging.INFO, "已通过流程链路匹配点击控件")
wt_logging.log_event(wt_logging.WARN, "流程控件定位耗时较长: step=step_7, control=btn_ok, seconds=9.30")
wt_logging.log_event(wt_logging.DEBUG, "无步骤上下文的一行", elapsed_ms=12.3456)

with open(probe, "r", encoding="utf-8") as f:
    raw = f.read()
records = [json.loads(l) for l in raw.splitlines() if l.strip()]

print("lines=%d" % len(records))
print("chinese_unescaped=%s" % ("已通过流程链路匹配点击控件" in raw and "\\u" not in raw))
for r in records:
    print("  keys=%s" % (list(r.keys()),))
print("extract_step_fields -> %r" % (wt_logging.extract_step_fields(
    "流程控件定位耗时较长: step=step_7, control=btn_ok, seconds=9.30"),))
print("empty optional omitted=%s" % (
    all(k not in records[0] for k in ("stepId", "controlId", "event", "elapsedMs", "detail")),))
print("elapsed rounded=%r" % (records[2].get("elapsedMs"),))
print("message is last key=%s" % (list(records[0].keys())[-1] == "message",))
wt_logging.stop_jsonl()
print("jsonl_path(after stop)=%r" % wt_logging.jsonl_path())

# 4) 轮转
big = os.path.join(work, "big.log")
with open(big, "w", encoding="utf-8") as f:
    f.write("x" * 100)
print("should_rotate(limit=10)=%s" % wt_logging.should_rotate(big, max_bytes=10))
print("rotate_log(limit=10)=%s" % wt_logging.rotate_log(big, max_bytes=10, backup_count=3))
print("backup exists=%s" % os.path.exists(big + ".1"))
print("RESULT=A_OK")
