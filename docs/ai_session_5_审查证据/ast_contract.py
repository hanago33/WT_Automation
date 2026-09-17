# encoding: utf-8
"""AST 全仓 API 契约校验：日志体系所有公开入口的调用点 arity / kwargs 检查。

目的：捕获「新增参数插在中间导致位置参数静默错位」这类最危险的破坏
（这类错误不报错、不崩溃，只是行为悄悄改变）。

只读扫描，不导入被测模块、不执行任何业务代码。
"""
import ast
import os
import sys

ROOT = r"D:\My_RF_Project\WT_automation_ai5"
SKIP_DIRS = {"release_out", ".git", ".codebuddy", "artifacts", ".qoder",
             ".workbuddy-ai", "__pycache__", "node_modules", "tools\\ORC"}

# 目标函数 -> (必填位置参数数, 允许的最大位置参数数, 允许的关键字名集合)
SPECS = {
    "log_step": (1, 2, {"level", "step_name"}),
    "log_at": (2, 2, {"level", "message"}),
    "format_line": (2, 5, {"level", "message", "module", "run_id", "timestamp"}),
    "log_event": (2, 10, {"level", "message", "event", "step_id", "control_id",
                          "elapsed_ms", "detail", "module", "run_id", "timestamp"}),
    "configure_log_tags": (1, 6, {"widget", "dark", "tags", "font_family", "font_size"}),
    "tag_colors_for_widget": (0, 2, {"dark", "tags"}),
    "level_colors": (0, 1, {"dark"}),
    "tag_for_line": (1, 1, {"line"}),
    "tag_for_level": (1, 1, {"level"}),
    "classify": (1, 1, {"line"}),
    "detect_level": (1, 1, {"line"}),
    "level_from_line": (1, 1, {"line"}),
    "normalize_level": (1, 2, {"level", "default"}),
    "is_enabled": (1, 1, {"level"}),
    "is_debug_enabled": (0, 0, set()),
    "set_min_level": (1, 1, {"level"}),
    "reset_min_level": (0, 0, set()),
    "resolve_min_level": (0, 0, set()),
    "rotate_log": (1, 3, {"path", "max_bytes", "backup_count"}),
    "should_rotate": (1, 2, {"path", "max_bytes"}),
    "start_jsonl": (1, 1, {"path"}),
    "stop_jsonl": (0, 0, set()),
    "jsonl_path": (0, 0, set()),
    "set_run_id": (1, 1, {"run_id"}),
    "get_run_id": (0, 0, set()),
    "clear_run_id": (0, 0, set()),
    "format_run_banner": (1, 6, {"kind", "run_id", "step_count", "status",
                                 "elapsed_seconds", "extra"}),
    "extract_step_fields": (1, 1, {"message"}),
    "_log_at": (2, 2, {"level", "message"}),
    "_log_debug": (1, 1, {"message"}),
    "_log_locate_elapsed": (2, 2, {"message", "elapsed"}),
    "_locate_elapsed_level": (1, 1, {"elapsed"}),
    "_log_window_fallback_source": (2, 2, {"source", "count"}),
    "configure_flow_locator": (0, 4, {"get_step_definition", "log_step",
                                      "get_main_window_candidates", "log_at"}),
    "configure_flow_executor": (0, 26, {
        "get_step_definition", "get_flow_package", "get_step_params",
        "resolve_dynamic_value", "log_step", "log_at", "click_flow_control",
        "click_relative_region", "click_relative_anchor", "check_all_toggles",
        "select_list_items", "focus_flow_control", "type_text_into_flow_control",
        "type_text_into_relative_region", "select_dropdown_item_runtime",
        "drag_between_flow_controls", "mouse_wheel_on_flow_control",
        "wait_for_flow_control_condition", "find_flow_control",
        "get_wrapper_value_snapshot", "menu_select_flow",
        "locate_template_center_by_path", "report_step_result",
        "run_ai_intervention_after_failure", "auto_capture_template",
        "control_map_path"}),
}

# 仅在这些模块内，is_enabled / _log_at 等名字才归属 wt_logging；
# 其他文件里的 wrapper.is_enabled() 是 pywinauto UIA 控件方法，不是本体系 API。
WT_LOGGING_AWARE_FILES = {
    "WT_AUT_recorded.py", "wt_flow_locator.py", "wt_flow_executor.py",
    "WT_Launcher.py", "wt_task_queue_window.py", "wt_logging.py",
}
WT_LOGGING_ONLY_NAMES = {"is_enabled", "is_debug_enabled", "_log_at", "_log_debug"}

problems = []
stats = {}


def iter_py():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".tmp")]
        for fn in files:
            if fn.endswith(".py"):
                yield os.path.join(base, fn)


for path in iter_py():
    rel = os.path.relpath(path, ROOT)
    if rel.startswith("tools\\ORC"):
        continue
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            tree = ast.parse(f.read(), filename=path)
    except SyntaxError as exc:
        problems.append((rel, 0, "SYNTAX ERROR: %s" % exc))
        continue

    base_name = os.path.basename(path)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else None)
        if name not in SPECS:
            continue
        # 排除同名异源：非 wt_logging 感知文件里的 is_enabled 等属于其它库
        if name in WT_LOGGING_ONLY_NAMES and base_name not in WT_LOGGING_AWARE_FILES:
            continue
        minp, maxp, allowed = SPECS[name]
        npos = len(node.args)
        nstar = any(isinstance(a, ast.Starred) for a in node.args)
        kwnames = {k.arg for k in node.keywords if k.arg}
        has_dstar = any(k.arg is None for k in node.keywords)
        stats[name] = stats.get(name, 0) + 1

        if not nstar and (npos < minp or npos > maxp):
            problems.append((rel, node.lineno,
                             "%s(): 位置参数 %d 个，期望 %d..%d" % (name, npos, minp, maxp)))
        if not has_dstar:
            bad = kwnames - allowed
            if bad:
                problems.append((rel, node.lineno,
                                 "%s(): 未知关键字 %s（允许 %s）" % (name, sorted(bad), sorted(allowed))))

print("=== 扫描统计（各 API 的调用点数）===")
for k in sorted(stats):
    print("  %-30s %d" % (k, stats[k]))
print()
if problems:
    print("=== 发现 %d 处契约问题 ===" % len(problems))
    for rel, ln, msg in problems:
        print("  %s:%d  %s" % (rel, ln, msg))
else:
    print("=== 未发现位置参数/关键字契约问题 ===")
print("RESULT=AST_OK")
