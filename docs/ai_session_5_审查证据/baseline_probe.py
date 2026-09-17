# encoding: utf-8
"""对照实验：在主目录（main，未改动）跑同样的 log_step 调用。

若主目录同样自旋，说明自旋是环境/沙箱造成，与本次改动无关。
"""
import faulthandler
import os
import sys

ROOT = sys.argv[1]
sys.path.insert(0, ROOT)

faulthandler.dump_traceback_later(25, exit=True)

print("ROOT=%s" % ROOT, flush=True)
import wt_logging_stub_check  # noqa  (only exists in ai5; ignore failure)
