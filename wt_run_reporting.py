# encoding: utf-8

import json
import os
import tempfile
from datetime import datetime


_BASE_DIR = os.path.dirname(__file__)
_LOG_STEP = lambda message: None
# 进程内运行序号：与毫秒组合保证同一 tick 内多次运行 runId 仍唯一（Windows 时钟粒度粗）
_RUN_SEQUENCE = [0]


def configure_run_reporting(base_dir=None, log_step=None):
    global _BASE_DIR, _LOG_STEP
    if base_dir:
        _BASE_DIR = base_dir
    if callable(log_step):
        _LOG_STEP = log_step


def _ensure_report_dir():
    report_dir = os.path.join(_BASE_DIR, "logs", "run_reports")
    os.makedirs(report_dir, exist_ok=True)
    return report_dir


def _atomic_write_json(path, data):
    """原子写 JSON：先写临时文件再 os.replace，避免进程中断留下截断文件。"""
    dir_path = os.path.dirname(path)
    os.makedirs(dir_path, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp_report_", suffix=".json", dir=dir_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            json.dump(data, file_obj, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def start_run_report(steps_to_run, runtime_config):
    started_at = datetime.now().isoformat(timespec="seconds")
    now = datetime.now()
    # runId 追加毫秒+进程内序号：同一秒甚至同一 tick 内多次运行（快速重跑/双实例）
    # 也不会互相覆盖报告文件
    _RUN_SEQUENCE[0] += 1
    run_id = now.strftime("wt_run_%Y%m%d_%H%M%S") + "_{:03d}_{:03d}".format(
        now.microsecond // 1000, _RUN_SEQUENCE[0] % 1000
    )
    return {
        "runId": run_id,
        "startedAt": started_at,
        "endedAt": "",
        "status": "running",
        "stepsRequested": list(steps_to_run or []),
        "runtimeConfig": dict(runtime_config or {}),
        "stepResults": [],
        "summary": {
            "requestedCount": len(list(steps_to_run or [])),
            "executedCount": 0,
            "successCount": 0,
            "failedCount": 0,
            "skippedCount": 0,
            "fallbackCount": 0,
            "totalElapsedSeconds": 0.0,
        },
        "error": "",
        "reportPath": "",
        "lastReportPath": "",
    }


def report_step_result(run_report, step_id, step_name, status, action_type="", strategy="", elapsed=0.0, error="", extra=None):
    if not isinstance(run_report, dict):
        return
    extra = dict(extra or {})
    elapsed_seconds = round(float(elapsed or 0.0), 3)
    result = {
        "stepId": str(step_id or "").strip(),
        "stepName": str(step_name or "").strip(),
        "status": str(status or "unknown").strip() or "unknown",
        "actionType": str(action_type or "").strip(),
        "strategy": str(strategy or "").strip(),
        "elapsedSeconds": elapsed_seconds,
        "error": str(error or "").strip(),
        "extra": extra,
    }
    run_report.setdefault("stepResults", []).append(result)
    summary = run_report.setdefault("summary", {})
    summary["executedCount"] = len(run_report.get("stepResults", []))
    summary["totalElapsedSeconds"] = round(float(summary.get("totalElapsedSeconds", 0.0)) + elapsed_seconds, 3)
    if result["status"] == "success":
        summary["successCount"] = int(summary.get("successCount", 0)) + 1
    elif result["status"] == "failed":
        summary["failedCount"] = int(summary.get("failedCount", 0)) + 1
    else:
        summary["skippedCount"] = int(summary.get("skippedCount", 0)) + 1
    if extra.get("fallbackTemplateUsed") or extra.get("fallbackUsed"):
        summary["fallbackCount"] = int(summary.get("fallbackCount", 0)) + 1
        fb_level = extra.get("fallbackLevel")
        if fb_level:
            level_counts = summary.setdefault("fallbackLevelCounts", {})
            level_key = str(fb_level)
            level_counts[level_key] = int(level_counts.get(level_key, 0)) + 1


def _resolve_final_status(run_report, requested_status):
    normalized_status = str(requested_status or "unknown").strip() or "unknown"
    if normalized_status != "success" or not isinstance(run_report, dict):
        return normalized_status
    summary = run_report.get("summary", {}) if isinstance(run_report.get("summary"), dict) else {}
    failed_count = int(summary.get("failedCount", 0) or 0)
    if failed_count > 0:
        return "partial_success"
    return normalized_status


def finalize_run_report(run_report, status, error=""):
    if not isinstance(run_report, dict):
        return ""
    run_report["status"] = _resolve_final_status(run_report, status)
    run_report["endedAt"] = datetime.now().isoformat(timespec="seconds")
    run_report["error"] = str(error or "").strip()

    report_dir = _ensure_report_dir()
    report_name = f"{run_report.get('runId', 'wt_run')}.json"
    report_path = os.path.join(report_dir, report_name)
    last_report_path = os.path.join(_BASE_DIR, "logs", "last_run_report.json")
    os.makedirs(os.path.dirname(last_report_path), exist_ok=True)
    run_report["reportPath"] = report_path
    run_report["lastReportPath"] = last_report_path

    for target_path in [report_path, last_report_path]:
        _atomic_write_json(target_path, run_report)

    _summary = run_report.get("summary", {})
    if not isinstance(_summary, dict):
        _summary = {}
    _LOG_STEP(
        "运行结果摘要已写入: "
        f"status={run_report.get('status', '')}, "
        f"executed={_summary.get('executedCount', 0)}, "
        f"success={_summary.get('successCount', 0)}, "
        f"failed={_summary.get('failedCount', 0)}, "
        f"skipped={_summary.get('skippedCount', 0)}, "
        f"fallback={_summary.get('fallbackCount', 0)}, "
        f"report={report_path}"
    )
    return report_path
