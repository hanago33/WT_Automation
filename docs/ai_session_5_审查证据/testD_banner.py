# encoding: utf-8
"""Test D: 运行结束 banner 的级别判定（疑似同类关键字误判回归）。

_log_run_end_banner 以 log_step(banner) 无级别调用 -> detect_level() 按文本猜。
banner 的 extra 段会拼上「失败 {failedCount}」，失败数为 0 时也照样拼 -> 文本含"失败"。
"""
import sys

sys.path.insert(0, r"D:\My_RF_Project\WT_automation_ai5")

import wt_logging  # noqa: E402

# 复刻 _log_run_end_banner 的 extra 拼装逻辑
def build_extra(success, failed, skipped=0):
    bits = []
    if success is not None:
        bits.append("成功 {}".format(success))
    if failed is not None:
        bits.append("失败 {}".format(failed))
    if skipped:
        bits.append("跳过 {}".format(skipped))
    return " / ".join(bits) if bits else ""


cases = [
    ("全部成功", "成功", 16, 0),
    ("有失败", "失败", 14, 2),
]

for name, status, ok, bad in cases:
    banner = wt_logging.format_run_banner(
        "end",
        status=status,
        elapsed_seconds=123.4,
        step_count=ok + bad,
        extra=build_extra(ok, bad),
    )
    lvl = wt_logging.detect_level(banner)
    kind = wt_logging.tag_for_level(lvl)
    verdict = "OK" if (status == "成功") == (kind != "error") else "*** MISCLASSIFIED ***"
    print("%-10s -> level=%-6s tag=%-8s %s" % (name, lvl, kind, verdict))
    print("            %s" % banner)

print()
# 同样检查「运行开始」banner
start = wt_logging.format_run_banner("start", run_id="wt_run_20260917_102341_123_001", step_count=16)
print("start  banner -> level=%-6s tag=%s" % (wt_logging.detect_level(start), wt_logging.tag_for_level(wt_logging.detect_level(start))))
print("            %s" % start)
print("RESULT=D_OK")
