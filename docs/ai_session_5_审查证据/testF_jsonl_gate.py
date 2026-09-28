# encoding: utf-8
"""Test F: JSONL 旁路是否真的保留了「全量调试数据」（P2 的设计承诺）？

P2 提交信息称：
  "设计：文本日志为人读而精简，JSONL 为机读而完整。两者同源同时写，
   因此「精简文本」与「保留全量调试数据」不再互相冲突。"

但 log_step 中 log_event() 位于 is_enabled() 门控的 return 之后。
本实验验证：默认档下被降为 DEBUG 的诊断行，是否进入 JSONL。
"""
import builtins
import json
import os
import sys
import tempfile

sys.path.insert(0, r"D:\My_RF_Project\WT_automation_ai5")

import wt_logging  # noqa: E402
import wt_run_status  # noqa: E402

wt_run_status.publish = lambda **kw: None  # 避免写 ai5/logs（沙箱禁写）

import WT_AUT_recorded as R  # noqa: E402

work = tempfile.mkdtemp(prefix="jsonl_gate_probe_")
jsonl = os.path.join(work, "run.jsonl")
text_lines = []
R._append_log_file = text_lines.append

real_print = builtins.print
builtins.print = lambda *a, **k: None


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ("WT_LOG_LEVEL", "WT_DEBUG_EVENTS")}
    env.update(extra)
    os.environ.clear()
    os.environ.update(env)
    wt_logging.reset_min_level()


def read_jsonl():
    if not os.path.exists(jsonl):
        return []
    with open(jsonl, "r", encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


results = {}
try:
    # ── 场景 A：默认档（INFO），写入一条 DEBUG 诊断 + 一条 INFO ──
    clean_env()
    wt_logging.set_run_id("wt_run_probe")
    wt_logging.start_jsonl(jsonl)
    R.log_step("[DEBUG] click_flow_control 判定: step=step_7", level=wt_logging.DEBUG)
    R.log_step("[定位耗时] step=step_7, 总计=120.0ms", level=wt_logging.DEBUG)
    R.log_step("已通过流程链路匹配点击控件: step=step_7")
    text_a = list(text_lines)
    json_a = read_jsonl()
    results["A_text_lines"] = len(text_a)
    results["A_json_records"] = len(json_a)
    results["A_json_levels"] = [r.get("level") for r in json_a]

    # ── 场景 B：DEBUG 档，同样三条 ──
    text_lines.clear()
    wt_logging.stop_jsonl()
    os.remove(jsonl)
    clean_env(WT_LOG_LEVEL="DEBUG")
    wt_logging.start_jsonl(jsonl)
    R.log_step("[DEBUG] click_flow_control 判定: step=step_7", level=wt_logging.DEBUG)
    R.log_step("[定位耗时] step=step_7, 总计=120.0ms", level=wt_logging.DEBUG)
    R.log_step("已通过流程链路匹配点击控件: step=step_7")
    results["B_text_lines"] = len(text_lines)
    results["B_json_records"] = len(read_jsonl())
    results["B_json_levels"] = [r.get("level") for r in read_jsonl()]
finally:
    builtins.print = real_print
    wt_logging.stop_jsonl()

real_print("=== 场景 A：默认档（INFO）===")
real_print("  文本日志行数 = %d" % results["A_text_lines"])
real_print("  JSONL 记录数 = %d" % results["A_json_records"])
real_print("  JSONL 里的级别 = %r" % (results["A_json_levels"],))
real_print("")
real_print("=== 场景 B：WT_LOG_LEVEL=DEBUG ===")
real_print("  文本日志行数 = %d" % results["B_text_lines"])
real_print("  JSONL 记录数 = %d" % results["B_json_records"])
real_print("  JSONL 里的级别 = %r" % (results["B_json_levels"],))
real_print("")
if results["A_json_records"] == 1:
    real_print("*** 结论：默认档下 2 条 DEBUG 诊断行「文本与 JSONL 双双丢失」***")
    real_print("*** JSONL 并未保留「全量调试数据」——它同样被 is_enabled() 门控挡掉了 ***")
else:
    real_print("结论：JSONL 记录了 %d 条" % results["A_json_records"])
real_print("RESULT=F_OK")
