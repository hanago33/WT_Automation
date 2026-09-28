# encoding: utf-8
"""Test K: P0-2 端到端复现 —— 调用真实的 _log_run_end_banner / finalize_run_report，
捕获它们**实际发出**的日志行与级别。

此前验证的是「banner 字符串会被 detect_level 判为 ERROR」（字符串层面）。
本实验走真实函数，确认线上真的会产出红字。
"""
import builtins
import os
import sys

ROOT = r"C:\Users\14830\AppData\Local\Temp\dsh-pNekd0\ai5_testrun"
sys.path.insert(0, ROOT)

import wt_logging  # noqa: E402
import wt_run_status  # noqa: E402

wt_run_status.publish = lambda **kw: None

import WT_AUT_recorded as R  # noqa: E402
import wt_run_reporting as REP  # noqa: E402

captured = []          # (level, message)
R._append_log_file = lambda line: captured.append(("FILE", line))

real_print = builtins.print
builtins.print = lambda *a, **k: None


def run_case(title, report, status):
    captured.clear()
    env = {k: v for k, v in os.environ.items() if k not in ("WT_LOG_LEVEL", "WT_DEBUG_EVENTS")}
    os.environ.clear(); os.environ.update(env)
    wt_logging.reset_min_level()
    try:
        R._log_run_end_banner(report, status)
    except Exception as exc:
        real_print("  [%s] _log_run_end_banner 抛异常: %r" % (title, exc))
    return list(captured)


# 成功运行的真实报告结构（对照 wt_run_reporting.start_run_report）
ok_report = {
    "runId": "wt_run_20260917_102341_123_001",
    "status": "success",
    "summary": {
        "requestedCount": 16, "executedCount": 16,
        "successCount": 16, "failedCount": 0, "skippedCount": 0,
        "fallbackCount": 0, "totalElapsedSeconds": 123.4,
    },
}
fail_report = dict(ok_report)
fail_report = {
    "runId": "wt_run_20260917_102341_123_001",
    "status": "failed",
    "summary": {
        "requestedCount": 16, "executedCount": 16,
        "successCount": 1, "failedCount": 15, "skippedCount": 0,
        "fallbackCount": 0, "totalElapsedSeconds": 9.9,
    },
}

builtins.print = real_print
for title, report, status in (
    ("成功运行（失败 0）", ok_report, "成功"),
    ("失败运行（失败 15）", fail_report, "失败"),
):
    lines = run_case(title, report, status)
    real_print("=== %s ===" % title)
    if not lines:
        real_print("  （无输出）")
    for _, line in lines:
        lvl = wt_logging.detect_level(line)
        tag = wt_logging.tag_for_level(lvl)
        real_print("  判定级别=%-6s 标签=%-8s | %s" % (lvl, tag, line))
    real_print("")

real_print("=== 结论 ===")
ok_lines = run_case("ok", ok_report, "成功")
ok_levels = [wt_logging.detect_level(l) for _, l in ok_lines]
if ok_levels and all(l == "ERROR" for l in ok_levels):
    real_print("  *** 成功运行的结束 banner 被判为 ERROR（红字）—— 端到端复现成功 ***")
else:
    real_print("  成功运行的级别: %r" % ok_levels)
real_print("RESULT=K_OK")
