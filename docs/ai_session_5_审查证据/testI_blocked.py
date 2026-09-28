# encoding: utf-8
"""Test I: 动态复现 P0-1 —— blocked_by_self_window 分支的日志丢失。

驱动 iter_flow_search_windows 走到「前台是自动化自身窗口 -> 放弃回退」这条
真实分支，捕获其实际输出的日志行，与 main 基线应输出的行做对比。

路径条件（对照 wt_flow_locator.py:6075-6136）：
  1) ranked_windows 为空
  2) 无 title_candidates  -> 进入回退块
  3) _GET_MAIN_WINDOW_CANDIDATES() 包装后为空   -> source=""
  4) _enum_visible_mup_win32_windows() 包装后为空 -> source 仍为 ""
  5) 前台窗口存在且 is_automation_window 为真 -> source="blocked_by_self_window"
  6) result 仍为空 -> 只输出「窗口过滤严格…跳过全窗口软化」并 return []
"""
import sys
from unittest.mock import patch

ROOT = r"C:\Users\14830\AppData\Local\Temp\dsh-pNekd0\ai5_testrun"
sys.path.insert(0, ROOT)

import wt_flow_locator as L  # noqa: E402

logs = []
L._LOG_STEP = logs.append
L._LOG_AT = lambda level, msg: logs.append("[%s] %s" % (level, msg))


class FakeWrapper(object):
    """仅用于让 is_automation_window 判定为真。"""

    def __init__(self, title):
        self.title = title
        self.element_info = type("EI", (), {"handle": 999})()


self_window = FakeWrapper("WT自动化 进度")

with patch.object(L, "get_cached_flow_windows", return_value=None), \
     patch.object(L, "make_flow_window_cache_key", return_value="k"), \
     patch.object(L, "get_control_process_candidates", return_value=[]), \
     patch.object(L, "_wrap_win32_window_candidates", return_value=[]), \
     patch.object(L, "_GET_MAIN_WINDOW_CANDIDATES", return_value=[]), \
     patch.object(L, "_enum_visible_mup_win32_windows", return_value=[]), \
     patch.object(L, "get_foreground_window_handle", return_value=12345), \
     patch.object(L, "_try_get_window_by_handle", return_value=self_window), \
     patch.object(L, "is_automation_window", return_value=True):

    result = L.iter_flow_search_windows({}, window_title_hint="", control_definition=None)

print("=== 实际输出 ===")
print("  返回结果 = %r" % (result,))
print("  捕获日志 %d 行：" % len(logs))
for line in logs:
    print("    %s" % line)

print()
print("=== 判定 ===")
joined = "\n".join(logs)
has_self_block_note = ("放弃本次窗口回退" in joined) or ("blocked_by_self_window" in joined)
print("  是否输出了「放弃本次窗口回退 / blocked_by_self_window」提示？ -> %s" % has_self_block_note)
print("  是否只输出了通用兜底行「跳过全窗口软化」？ -> %s"
      % any("跳过全窗口软化" in l for l in logs))
print()
if not has_self_block_note:
    print("*** 复现成功：进入 blocked_by_self_window 分支，但没有任何日志说明原因 ***")
    print("*** 排错者只能看到「跳过全窗口软化」，无法知道是自身窗口挡住了回退 ***")
else:
    print("未复现（该分支有日志输出）")
print("RESULT=I_OK")
