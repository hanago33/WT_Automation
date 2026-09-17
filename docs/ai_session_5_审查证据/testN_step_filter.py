# encoding: utf-8
"""Test N: P3 的核心卖点「报告页点某步骤 -> 筛出该步日志」在真实数据上是否成立。

wt_logging.extract_step_fields 用正则 \bstep=([^,\s]+) 从**整行文本**里抓 step=。
但真实日志里 step= 只出现在少数错误/告警消息的文本中。

关键问题：正常步骤日志（如「步骤 X 完成」）是否带 step 字段？
如果不带，那么「点某步骤筛日志」在成功路径上会筛出 0 行。
"""
import os
import sys

ROOT = r"C:\Users\14830\AppData\Local\Temp\dsh-pNekd0\ai5_testrun"
sys.path.insert(0, ROOT)

import wt_log_query as Q  # noqa: E402
import wt_logging as L  # noqa: E402

log = os.path.join(ROOT, "wt_automation.log")
lines = Q.read_log_lines(log)
print("真实日志总行数: %d" % len(lines))
print()

with_step = [(line, L.extract_step_fields(line)[0]) for line in lines
             if L.extract_step_fields(line)[0]]
print("含 step 字段的行数: %d / %d" % (len(with_step), len(lines)))
print()
print("--- 前 5 条含 step 的行（看看都是什么性质）---")
for line, sid in with_step[:5]:
    print("  step=%-20s %s" % (sid, line[-70:]))

print()
print("--- 对照：正常步骤日志是否带 step ---")
normal = [line for line in lines if ("完成" in line or "开始" in line)]
print("  含「完成/开始」的行数: %d" % len(normal))
normal_with_step = [line for line in normal if L.extract_step_fields(line)[0]]
print("  其中带 step 字段的: %d" % len(normal_with_step))
if normal:
    print("  示例: %s" % normal[0][-80:])
    print("         extract_step_fields -> %r" % (L.extract_step_fields(normal[0]),))

print()
print("=== 结论 ===")
if len(normal) > 0 and len(normal_with_step) == 0:
    print("  *** 正常步骤日志（完成/开始）完全不带 step 字段 ***")
    print("  *** 「点某步骤筛出该步日志」在成功路径上会筛出 0 行 ***")
    print("  *** 只有恰好触发了带 step= 的错误/告警消息的步骤才有日志可筛 ***")
elif len(normal_with_step) > 0:
    print("  正常步骤日志带 step 字段: %d 条，联动可用" % len(normal_with_step))
else:
    print("  样本不足")
print("RESULT=N_OK")
