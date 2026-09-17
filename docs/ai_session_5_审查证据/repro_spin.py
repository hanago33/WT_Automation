# encoding: utf-8
"""复现 LogStepLevelTests.test_debug_level_written_when_enabled 的自旋。

用 faulthandler 在 20s 后 dump 所有线程栈并退出，从而定位忙等位置。
"""
import builtins
import faulthandler
import os
import sys

sys.path.insert(0, r"D:\My_RF_Project\WT_automation_ai5")

from unittest.mock import patch

import wt_logging

faulthandler.dump_traceback_later(20, exit=True)

print("STEP1 importing WT_AUT_recorded ...", flush=True)
import WT_AUT_recorded as R

print("STEP2 imported", flush=True)

clean = {k: v for k, v in os.environ.items() if k not in ("WT_LOG_LEVEL", "WT_DEBUG_EVENTS")}
clean["WT_LOG_LEVEL"] = "DEBUG"

lines = []
with patch.dict(os.environ, clean, clear=True):
    wt_logging.reset_min_level()
    print("STEP3 min_level=%r" % (wt_logging.resolve_min_level(),), flush=True)
    with patch.object(R, "_append_log_file", lines.append), patch.object(
        builtins, "print", lambda *a, **k: None
    ):
        print("STEP4 calling log_step", flush=True)
        R.log_step("执行步骤列表: ['step_1']", level=wt_logging.DEBUG)
        print("STEP5 returned", flush=True)

print("RESULT lines=%d" % len(lines), flush=True)
