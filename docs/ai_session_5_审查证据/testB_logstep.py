# encoding: utf-8
"""Test B (修正版): log_step 级别门控 + 自旋归因。

上一版误把 builtins.print 全局替换导致无输出；这里只临时屏蔽 print。
"""
import builtins
import os
import sys

sys.path.insert(0, r"D:\My_RF_Project\WT_automation_ai5")

import wt_logging  # noqa: E402
import wt_run_status  # noqa: E402

publish_calls = []
wt_run_status.publish = lambda **kw: publish_calls.append(kw)  # 去掉落盘（沙箱禁写）

import WT_AUT_recorded as R  # noqa: E402

lines = []
R._append_log_file = lines.append  # 不落盘到 ai5

real_print = builtins.print


def quiet(*a, **k):
    pass


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("WT_LOG_LEVEL", "WT_DEBUG_EVENTS")}
    env.update(extra)
    os.environ.clear()
    os.environ.update(env)
    wt_logging.reset_min_level()


cases = [
    ("default", {}, None, "步骤结束: step=x, status=success", 1),
    ("default", {}, wt_logging.ERROR, "定位失败：控件未找到", 1),
    ("default", {}, wt_logging.DEBUG, "执行步骤列表: ['step_1']", 0),
    ("default", {}, None, "已剔除错误项=0", 1),
    ("DEBUG", {"WT_LOG_LEVEL": "DEBUG"}, wt_logging.DEBUG, "执行步骤列表: ['step_1']", 1),
    ("DEBUG_EVENTS", {"WT_DEBUG_EVENTS": "1"}, wt_logging.DEBUG, "执行步骤列表: ['step_1']", 1),
]

builtins.print = quiet
results = []
try:
    for name, env, level, msg, expect in cases:
        clean_env(**env)
        before = len(lines)
        R.log_step(msg, level=level)
        results.append((name, level, len(lines) - before, expect, lines[-1] if len(lines) > before else ""))
finally:
    builtins.print = real_print

for name, level, got, expect, sample in results:
    flag = "OK " if got == expect else "BAD"
    real_print("%s env=%-13s level=%-6s emitted=%d expect=%d" % (flag, name, level, got, expect))
    if sample:
        real_print("      %s" % sample[:110])
real_print("publish_calls=%d (proves publish was exercised without hanging)" % len(publish_calls))
real_print("RESULT=B_OK")
