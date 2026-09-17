# encoding: utf-8
"""Test C: 成功/完成类日志行的着色是否发生变化（潜在视觉回归）。

改造前 WT_AUT_recorded.log_step 用关键字把「完成/成功」判为 kind="success"（绿）。
改造后走 detect_level() -> tag_for_level()。检查新链路是否还能产出 success 标签。
"""
import sys

sys.path.insert(0, r"D:\My_RF_Project\WT_automation_ai5")

import wt_logging  # noqa: E402

samples = [
    "步骤结束: step=step_1, status=success",
    "已通过流程链路匹配点击控件: ctrl=btn_ok",
    "WT自动化流程完成",
    "下拉选项枚举进度: 已剔除错误项=0",
    "流程控件定位耗时较长: seconds=9.30",
    "定位失败：控件未找到",
    "[FlowLocator] 窗口严格过滤无命中，回退来源=foreground_window，候选 1 个",
]

print("%-58s %-10s %-8s %-8s" % ("message", "detect", "kind", "classify"))
for m in samples:
    lvl = wt_logging.detect_level(m)
    kind = wt_logging.tag_for_level(lvl)
    print("%-58s %-10s %-8s %-8s" % (m[:56], lvl, kind, wt_logging.classify(m)))

print()
print("TAG_BY_LEVEL = %r" % (wt_logging._TAG_BY_LEVEL,))
print("can tag_for_level EVER return 'success'? -> %s" % (
    "success" in set(wt_logging._TAG_BY_LEVEL.values())))
print()
print("--- 对照：旧实现的关键字规则（改造前 log_step 的行为）---")


def legacy_kind(step_name):
    if any(k in step_name for k in ("失败", "错误")):
        return "error"
    if any(k in step_name for k in ("完成", "成功")):
        return "success"
    if any(k in step_name for k in ("警告", "跳过")):
        return "warning"
    return "info"


for m in samples:
    old = legacy_kind(m)
    new = wt_logging.tag_for_level(wt_logging.detect_level(m))
    if old != new:
        print("  CHANGED  %-52s %s -> %s" % (m[:50], old, new))
print("RESULT=C_OK")
