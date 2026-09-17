# encoding: utf-8
"""Test M: wt_log_query 是否继承 P0-2 的假红字。

level_of() 对无显式级别 token 的行回退到 wt_logging.detect_level() ——
正是 P0-2 里把「运行结束 · 状态=成功 · 失败 0」判为 ERROR 的那个函数。

问题：新模块的摘要/过滤会把成功运行统计成 ERROR 吗？
另外：本模块自称「历史行回退关键字推断，避免老日志被静默滤空」，
那么对**老格式**（无级别 token）的成功运行结束行，摘要会怎么报？
"""
import os
import sys

ROOT = r"C:\Users\14830\AppData\Local\Temp\dsh-pNekd0\ai5_testrun"
sys.path.insert(0, ROOT)

import wt_log_query as Q  # noqa: E402
import wt_logging  # noqa: E402

print("=== 场景 1：新格式日志（带显式级别 token）—— 成功运行 ===")
new_lines = [
    "[2026-09-17 10:00:00.000] [INFO ] [WT_AUT_recorded] ========== 运行开始 · runId=x · 待执行 16 步 ==========",
    "[2026-09-17 10:00:01.000] [INFO ] [WT_AUT_recorded] 步骤 1 完成",
    "[2026-09-17 10:00:02.000] [ERROR] [WT_AUT_recorded] ========== 运行结束 · 状态=成功 · 用时 2s · 步骤 16 步 · 成功 16 / 失败 0 ==========",
]
for line in new_lines:
    print("  level_of=%-6s | %s" % (Q.level_of(line), line[-60:]))
print("  摘要: %s" % Q.summarize(new_lines))
cnt = Q.level_counts(new_lines)
print("  ERROR 计数 = %d  %s" % (cnt.get("ERROR", 0),
      "<-- 成功运行被统计出 ERROR" if cnt.get("ERROR") else ""))

print()
print("=== 场景 2：老格式日志（无级别 token）—— 成功运行 ===")
old_lines = [
    "[2026-09-17 10:00:00.000] ========== 运行开始 · runId=x · 待执行 16 步 ==========",
    "[2026-09-17 10:00:01.000] 步骤 1 完成",
    "[2026-09-17 10:00:02.000] ========== 运行结束 · 状态=成功 · 用时 2s · 步骤 16 步 · 成功 16 / 失败 0 ==========",
]
for line in old_lines:
    print("  level_of=%-6s | %s" % (Q.level_of(line), line[-60:]))
print("  摘要: %s" % Q.summarize(old_lines))
cnt2 = Q.level_counts(old_lines)
print("  ERROR 计数 = %d  %s" % (cnt2.get("ERROR", 0),
      "<-- 老日志的成功运行也被统计出 ERROR" if cnt2.get("ERROR") else ""))

print()
print("=== 场景 3：过滤影响（用户选「ERROR 及以上」）===")
flt = Q.LogFilter(min_level="ERROR")
kept = flt.apply(new_lines)
print("  新格式下选中 %d 行：" % len(kept))
for line in kept:
    print("    %s" % line[-70:])
print()
print("  -> 用户想看「哪里出错了」，得到的是运行成功的那行。")

print()
print("=== 结论 ===")
if cnt.get("ERROR") or cnt2.get("ERROR"):
    print("  *** wt_log_query 继承 P0-2：成功运行的收尾行被计为 ERROR ***")
    print("  *** 且该模块的用途正是「定位第一个出问题的步骤」—— 假红字直接误导 ***")
else:
    print("  未继承 P0-2")
print("RESULT=M_OK")
