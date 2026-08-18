# encoding: utf-8
"""运行日志分析工具 (WT Automation Run Log Analyzer)。

读取 ``logs/run_reports/*.json`` 运行报告（由 wt_run_reporting 产出），做两类分析：

  1) 单次运行诊断 (--run / --last)：
     - 首因定位：按执行顺序找出**第一个失败步**，视为根因；顶层 report.error 往往是
       末次报错（对应"连带失败·首因≠报错步"的排障经验）。
     - 软成功标记：经 fallback 降级才成功的步（extra.fallbackTemplateUsed）单独列出，
       它们是主定位不稳的信号。
     - 耗时热点：本次运行最慢的若干步。

  2) 多次运行聚合 (默认，或 --aggregate)：
     - 失败频次 TopN（按 stepId 聚合）。
     - 错误签名聚类：归一化 step=/control=/路径/数字后分组计数，把"同类失败"聚到一起。
     - fallback 高频步：主定位失败、依赖降级的步（改进定位策略的候选）。
     - 慢步 TopN（按平均耗时）。
     - 运行状态分布。

纯只读，仅依赖标准库，不修改任何文件。

用法示例：
    python tools/analyze_run_logs.py                 # 聚合最近 20 次运行
    python tools/analyze_run_logs.py --all           # 聚合全部历史运行
    python tools/analyze_run_logs.py --last          # 详细诊断最近一次运行
    python tools/analyze_run_logs.py --run wt_run_20260630_192831
    python tools/analyze_run_logs.py --run logs/last_run_report.json
    python tools/analyze_run_logs.py --failures-only # 聚合时只看有失败/降级的步
    python tools/analyze_run_logs.py --json          # 机器可读输出
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

try:  # 让 Windows 控制台也能正常打印中文
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# 加载与选择报告
# ---------------------------------------------------------------------------
def default_reports_dir():
    return os.path.join(BASE_DIR, "logs", "run_reports")


def load_report(path):
    """读取单个报告 JSON；失败返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def iter_report_paths(reports_dir):
    """按文件名（内含时间戳）升序返回所有运行报告路径。"""
    pattern = os.path.join(reports_dir, "wt_run_*.json")
    return sorted(glob.glob(pattern))


def resolve_single_report(value, reports_dir):
    """把 --run 的取值解析为一个报告 dict。

    支持：绝对/相对文件路径、runId（可带或不带 .json）、以及 'last'。
    """
    text = str(value or "").strip()
    if not text or text.lower() == "last":
        paths = iter_report_paths(reports_dir)
        last_report = os.path.join(BASE_DIR, "logs", "last_run_report.json")
        if not paths and os.path.isfile(last_report):
            return load_report(last_report)
        return load_report(paths[-1]) if paths else None

    # 直接是一个存在的文件路径
    if os.path.isfile(text):
        return load_report(text)
    candidate = text if os.path.isabs(text) else os.path.join(BASE_DIR, text)
    if os.path.isfile(candidate):
        return load_report(candidate)

    # 按 runId 匹配
    run_id = text[:-5] if text.endswith(".json") else text
    guess = os.path.join(reports_dir, run_id + ".json")
    if os.path.isfile(guess):
        return load_report(guess)
    # runId 现含毫秒/序号后缀（wt_run_日期_时分秒_毫秒_序号，见 wt_run_reporting）：
    # 兼容传入旧格式/部分 runId（如 wt_run_20260818_101500）时，取同前缀最新一个报告
    prefix = os.path.join(reports_dir, run_id + "_*.json")
    prefixed = sorted(glob.glob(prefix))
    if prefixed:
        return load_report(prefixed[-1])
    return None


# ---------------------------------------------------------------------------
# 分析辅助
# ---------------------------------------------------------------------------
def normalize_error_signature(error):
    """把具体错误串归一化为可聚类的"签名"，抹掉步骤/控件/路径/数字等易变部分。"""
    signature = str(error or "").strip()
    if not signature:
        return ""
    signature = re.sub(r"step=[^,\s]+", "step=*", signature)
    signature = re.sub(r"control=[^,\s]+", "control=*", signature)
    signature = re.sub(r"phase=[^,\s]+", "phase=*", signature)
    signature = re.sub(r"condition=[^,\s]+", "condition=*", signature)
    signature = re.sub(r"[A-Za-z]:\\[^\s,]+", "<path>", signature)
    signature = re.sub(r"\d+(?:\.\d+)?", "*", signature)
    signature = re.sub(r"\s+", " ", signature).strip()
    return signature


def step_results(report):
    results = report.get("stepResults", []) if isinstance(report, dict) else []
    return [r for r in results if isinstance(r, dict)]


def used_fallback(result):
    """该步是否触及了 fallback 降级（成功用了降级 / 尝试过降级 / 有降级原因）。"""
    extra = result.get("extra", {}) if isinstance(result.get("extra"), dict) else {}
    keys = ("fallbackTemplateUsed", "fallbackTemplateAttempted", "fallbackReason", "fallbackError")
    return any(extra.get(key) for key in keys)


def find_first_failure(report):
    """按执行顺序返回第一个 status==failed 的步（根因候选），无则 None。"""
    for result in step_results(report):
        if str(result.get("status", "")).strip() == "failed":
            return result
    return None


def _fmt_seconds(value):
    try:
        return f"{float(value):.2f}s"
    except Exception:
        return "0.00s"


# ---------------------------------------------------------------------------
# 单次运行诊断
# ---------------------------------------------------------------------------
def build_single_analysis(report):
    results = step_results(report)
    summary = report.get("summary", {}) if isinstance(report.get("summary"), dict) else {}
    first_failure = find_first_failure(report)

    failures = [r for r in results if str(r.get("status")) == "failed"]
    soft_success = [r for r in results if str(r.get("status")) == "success" and used_fallback(r)]
    slowest = sorted(results, key=lambda r: float(r.get("elapsedSeconds", 0) or 0), reverse=True)

    return {
        "runId": report.get("runId", ""),
        "status": report.get("status", ""),
        "startedAt": report.get("startedAt", ""),
        "endedAt": report.get("endedAt", ""),
        "topLevelError": report.get("error", ""),
        "summary": summary,
        "rootCause": first_failure,
        "failures": failures,
        "softSuccess": soft_success,
        "slowest": slowest[:5],
    }


def print_single_analysis(analysis):
    print("=" * 72)
    print(f"单次运行诊断  runId={analysis['runId']}  status={analysis['status']}")
    print(f"  时间: {analysis['startedAt']} → {analysis['endedAt']}")
    summary = analysis["summary"]
    print(
        "  概况: 请求 {req} / 执行 {exe} / 成功 {ok} / 失败 {fail} / 跳过 {skip} / 降级 {fb} / 总耗时 {sec}s".format(
            req=summary.get("requestedCount", 0),
            exe=summary.get("executedCount", 0),
            ok=summary.get("successCount", 0),
            fail=summary.get("failedCount", 0),
            skip=summary.get("skippedCount", 0),
            fb=summary.get("fallbackCount", 0),
            sec=summary.get("totalElapsedSeconds", 0),
        )
    )

    root = analysis["rootCause"]
    print("-" * 72)
    if root is None:
        print("首因定位: 无失败步 ✅")
    else:
        print("首因定位 (第一个失败步 = 根因候选):")
        print(f"  ▶ {root.get('stepId', '')}  {root.get('stepName', '')}")
        print(f"    error: {root.get('error', '')}")
        top_error = str(analysis["topLevelError"] or "").strip()
        root_error = str(root.get("error", "") or "").strip()
        if top_error and top_error != root_error:
            print(f"  ⚠ 顶层 report.error 是末次报错，可能非根因: {top_error}")
        later_failures = [f for f in analysis["failures"] if f is not root]
        if later_failures:
            print(f"  后续连带失败 {len(later_failures)} 步 (多半是首因未生效导致):")
            for fail in later_failures:
                print(f"    - {fail.get('stepId', '')}  {fail.get('stepName', '')}: {fail.get('error', '')}")

    soft = analysis["softSuccess"]
    if soft:
        print("-" * 72)
        print(f"软成功 (经 fallback 降级才成功, 主定位不稳信号) {len(soft)} 步:")
        for result in soft:
            extra = result.get("extra", {})
            reason = extra.get("fallbackReason") or extra.get("fallbackTemplateUsed") or ""
            print(f"  ⚠ {result.get('stepId', '')}  {result.get('stepName', '')}: {reason}")

    if analysis["slowest"]:
        print("-" * 72)
        print("耗时热点 (Top 5):")
        for result in analysis["slowest"]:
            print(
                f"  {_fmt_seconds(result.get('elapsedSeconds'))}  "
                f"{result.get('stepId', '')}  {result.get('stepName', '')}"
            )
    print("=" * 72)


# ---------------------------------------------------------------------------
# 多次运行聚合
# ---------------------------------------------------------------------------
def build_aggregate_analysis(reports, top_n=10):
    status_dist = defaultdict(int)
    error_sig = defaultdict(lambda: {"count": 0, "sample": ""})
    step_stats = defaultdict(lambda: {"name": "", "runs": 0, "fail": 0, "fallback": 0, "elapsed": []})

    for report in reports:
        status_dist[str(report.get("status", "unknown")) or "unknown"] += 1
        for result in step_results(report):
            step_id = str(result.get("stepId", "")).strip() or "<unknown>"
            stat = step_stats[step_id]
            stat["runs"] += 1
            if result.get("stepName"):
                stat["name"] = result.get("stepName")
            elapsed = float(result.get("elapsedSeconds", 0) or 0)
            stat["elapsed"].append(elapsed)
            status = str(result.get("status", "")).strip()
            if status == "failed":
                stat["fail"] += 1
                signature = normalize_error_signature(result.get("error", ""))
                if signature:
                    bucket = error_sig[signature]
                    bucket["count"] += 1
                    if not bucket["sample"]:
                        bucket["sample"] = str(result.get("error", "")).strip()
            if used_fallback(result):
                stat["fallback"] += 1

    def avg(values):
        return round(sum(values) / len(values), 2) if values else 0.0

    top_failures = sorted(
        [(sid, s) for sid, s in step_stats.items() if s["fail"] > 0],
        key=lambda kv: kv[1]["fail"], reverse=True,
    )[:top_n]
    top_fallback = sorted(
        [(sid, s) for sid, s in step_stats.items() if s["fallback"] > 0],
        key=lambda kv: kv[1]["fallback"], reverse=True,
    )[:top_n]
    top_slow = sorted(
        [(sid, s) for sid, s in step_stats.items() if s["elapsed"]],
        key=lambda kv: avg(kv[1]["elapsed"]), reverse=True,
    )[:top_n]
    top_errors = sorted(error_sig.items(), key=lambda kv: kv[1]["count"], reverse=True)[:top_n]

    return {
        "runCount": len(reports),
        "statusDist": dict(status_dist),
        "topFailures": [
            {"stepId": sid, "stepName": s["name"], "fail": s["fail"], "runs": s["runs"]}
            for sid, s in top_failures
        ],
        "topFallback": [
            {"stepId": sid, "stepName": s["name"], "fallback": s["fallback"], "runs": s["runs"]}
            for sid, s in top_fallback
        ],
        "topSlow": [
            {"stepId": sid, "stepName": s["name"], "avgSeconds": avg(s["elapsed"]),
             "maxSeconds": round(max(s["elapsed"]), 2), "runs": s["runs"]}
            for sid, s in top_slow
        ],
        "topErrors": [
            {"signature": sig, "count": info["count"], "sample": info["sample"]}
            for sig, info in top_errors
        ],
    }


def print_aggregate_analysis(analysis, failures_only=False):
    print("=" * 72)
    print(f"多次运行聚合分析  (共 {analysis['runCount']} 次运行)")
    dist = analysis["statusDist"]
    print("  状态分布: " + ", ".join(f"{k}={v}" for k, v in sorted(dist.items())))

    print("-" * 72)
    print("失败频次 TopN (按步聚合):")
    if analysis["topFailures"]:
        for item in analysis["topFailures"]:
            print(f"  {item['fail']:>3} 次失败 / 出现 {item['runs']:>3} 次  "
                  f"{item['stepId']}  {item['stepName']}")
    else:
        print("  无失败记录 ✅")

    print("-" * 72)
    print("错误签名聚类 TopN (同类失败归组):")
    if analysis["topErrors"]:
        for item in analysis["topErrors"]:
            print(f"  {item['count']:>3} 次  [{item['signature']}]")
            if item["sample"]:
                print(f"        样例: {item['sample']}")
    else:
        print("  无错误记录 ✅")

    print("-" * 72)
    print("fallback 高频步 TopN (主定位不稳, 定位策略改进候选):")
    if analysis["topFallback"]:
        for item in analysis["topFallback"]:
            print(f"  {item['fallback']:>3} 次降级 / 出现 {item['runs']:>3} 次  "
                  f"{item['stepId']}  {item['stepName']}")
    else:
        print("  无降级记录 ✅")

    if not failures_only:
        print("-" * 72)
        print("慢步 TopN (按平均耗时):")
        for item in analysis["topSlow"]:
            print(f"  平均 {item['avgSeconds']:>6}s / 峰值 {item['maxSeconds']:>6}s / "
                  f"出现 {item['runs']:>3} 次  {item['stepId']}  {item['stepName']}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="WT Automation 运行日志分析工具 (只读)。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--run", metavar="ID|PATH",
                        help="诊断单次运行: runId、报告文件路径, 或 'last'。")
    parser.add_argument("--last", action="store_true",
                        help="诊断最近一次运行 (等价于 --run last)。")
    parser.add_argument("--aggregate", action="store_true",
                        help="强制走多次聚合分析 (默认行为)。")
    parser.add_argument("--limit", type=int, default=20,
                        help="聚合时纳入的最近运行数量 (默认 20)。")
    parser.add_argument("--all", action="store_true",
                        help="聚合全部历史运行 (忽略 --limit)。")
    parser.add_argument("--top", type=int, default=10,
                        help="各 TopN 列表长度 (默认 10)。")
    parser.add_argument("--failures-only", action="store_true",
                        help="聚合时只展示失败/降级相关信息 (略过慢步)。")
    parser.add_argument("--reports-dir", default=None,
                        help="报告目录 (默认 logs/run_reports)。")
    parser.add_argument("--json", action="store_true",
                        help="以 JSON 输出分析结果 (机器可读)。")
    return parser


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    reports_dir = args.reports_dir or default_reports_dir()

    # 单次诊断
    if args.last or args.run:
        report = resolve_single_report(args.run if args.run else "last", reports_dir)
        if report is None:
            print("未找到可分析的运行报告。", file=sys.stderr)
            return 2
        analysis = build_single_analysis(report)
        if args.json:
            print(json.dumps(analysis, ensure_ascii=False, indent=2))
        else:
            print_single_analysis(analysis)
        return 0

    # 多次聚合
    paths = iter_report_paths(reports_dir)
    if not paths:
        print(f"报告目录为空: {reports_dir}", file=sys.stderr)
        return 2
    if not args.all:
        paths = paths[-max(1, args.limit):]
    reports = [r for r in (load_report(p) for p in paths) if r is not None]
    if not reports:
        print("报告解析失败, 无有效数据。", file=sys.stderr)
        return 2

    analysis = build_aggregate_analysis(reports, top_n=max(1, args.top))
    if args.json:
        print(json.dumps(analysis, ensure_ascii=False, indent=2))
    else:
        print_aggregate_analysis(analysis, failures_only=args.failures_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
