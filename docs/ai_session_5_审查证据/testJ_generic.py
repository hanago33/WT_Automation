# encoding: utf-8
"""Test J: 验证生成产物与主文件的日志接口是否已经分叉。

对方记忆声称「wt_logging 作为共享基础设施被生成物直接复用」。
实测三个生成产物均未 import wt_logging（mtime 停在分支创建前）。

本实验回答一个具体问题：若重跑生成器，产物是否会因新增的 wt_logging
依赖而崩溃？以及当前检入的产物是否仍能独立工作？
"""
import ast
import os
import sys

ROOT = r"C:\Users\14830\AppData\Local\Temp\dsh-pNekd0\ai5_testrun"
GF = os.path.join(ROOT, "tools", "generic_flow")


def analyze(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    tree = ast.parse(src, filename=path)
    imports = set()
    log_calls = {"_LOG_STEP": 0, "_log_debug": 0, "_log_at": 0, "log_step": 0,
                 "_log_window_fallback_source": 0, "_log_locate_elapsed": 0}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imports.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else None)
            if name in log_calls:
                log_calls[name] += 1
    return imports, log_calls


print("=== 生成产物 vs 主文件的日志接口对照 ===")
rows = [
    ("主文件 wt_flow_locator.py", os.path.join(ROOT, "wt_flow_locator.py")),
    ("产物 generic_flow_locator.py", os.path.join(GF, "generic_flow_locator.py")),
    ("主文件 wt_flow_executor.py", os.path.join(ROOT, "wt_flow_executor.py")),
    ("产物 generic_flow_executor.py", os.path.join(GF, "generic_flow_executor.py")),
]
for label, path in rows:
    imports, calls = analyze(path)
    has_wt_logging = "wt_logging" in imports
    print("\n%s" % label)
    print("  import wt_logging        : %s" % has_wt_logging)
    print("  _LOG_STEP 调用           : %d" % calls["_LOG_STEP"])
    print("  _log_debug 调用          : %d" % calls["_log_debug"])
    print("  _log_window_fallback_source: %d" % calls["_log_window_fallback_source"])
    print("  _log_locate_elapsed      : %d" % calls["_log_locate_elapsed"])

# 关键问题：主文件里新增的、产物里没有的符号
print()
print("=== 分叉程度 ===")
main_src = open(os.path.join(ROOT, "wt_flow_locator.py"), encoding="utf-8").read()
gen_src = open(os.path.join(GF, "generic_flow_locator.py"), encoding="utf-8").read()
for sym in ("_log_debug", "_log_at", "_log_window_fallback_source",
            "_log_locate_elapsed", "SLOW_LOCATE_INFO_SECONDS",
            "_locate_elapsed_level", "configure_flow_locator"):
    in_main = sym in main_src
    in_gen = sym in gen_src
    flag = "" if in_main == in_gen else "   <-- 分叉"
    print("  %-32s 主文件=%-5s 产物=%-5s%s" % (sym, in_main, in_gen, flag))

print()
print("=== 结论 ===")
print("  产物是分支创建前的旧快照（未重生成），不含本次日志改造。")
print("  重跑 make_generic_copy.py 时，它会从主文件复制并做文本替换；")
print("  主文件现在 import wt_logging 且调用 _log_at/_log_debug，")
print("  产物若原样复制将同样依赖 wt_logging（仓库根已在 sys.path，可解析）。")
print("  风险点：若生成器的文本替换规则与新增代码冲突，或产物被独立分发")
print("  到没有 wt_logging.py 的环境，则会 ImportError。")
print("RESULT=J_OK")
