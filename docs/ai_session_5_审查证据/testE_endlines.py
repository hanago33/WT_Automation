# encoding: utf-8
"""Test E: 每次运行收尾的两行是否被误判为 ERROR（同类关键字误判回归）。

两处都是 log_step(单参数) -> detect_level() 猜级别：
1) _log_run_end_banner 的 banner（extra 段恒含「失败 N」，N=0 时也在）
2) wt_run_reporting.finalize_run_report 的「运行结果摘要已写入 ... failed=0」
"""
import sys

sys.path.insert(0, r"D:\My_RF_Project\WT_automation_ai5")

import wt_logging  # noqa: E402

lines = [
    # 1) 运行结束 banner（成功运行，失败 0）
    "========== 运行结束 · 状态=成功 · 用时 123.4s · 步骤 16 步 · 成功 16 / 失败 0 ==========",
    # 2) wt_run_reporting L154-161 的摘要行（成功运行）
    "运行结果摘要已写入: status=success, executed=16, success=16, failed=0, skipped=0, fallback=0, report=logs/last_run_report.json",
    # 对照：真正的失败
    "========== 运行结束 · 状态=失败 · 用时 9.9s · 步骤 16 步 · 成功 1 / 失败 15 ==========",
]

for s in lines:
    lvl = wt_logging.detect_level(s)
    print("level=%-6s tag=%-8s | %s" % (lvl, wt_logging.tag_for_level(lvl), s[:95]))

print()
print("benign contexts = %r" % (wt_logging._BENIGN_ERROR_CONTEXT,))
print("does 'failed=0' match any benign context? -> %s" % any(
    c in lines[1] for c in wt_logging._BENIGN_ERROR_CONTEXT))
print("does '失败 0' match any benign context? -> %s" % any(
    c in lines[0] for c in wt_logging._BENIGN_ERROR_CONTEXT))
print("RESULT=E_OK")
