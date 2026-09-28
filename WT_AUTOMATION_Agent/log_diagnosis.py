# encoding: utf-8
"""log_diagnosis —— 读取执行日志/运行报告，定位失败步骤并构建诊断提示。

Agent 可把运行日志（wt_automation.log）或运行报告（logs/run_reports/*.json）
喂给本模块，得到一份结构化的诊断提示，再由 LLM 给出修复建议。
"""
from __future__ import annotations

import json
import os
from typing import Any


def load_run_report(path: str) -> dict[str, Any] | None:
    """加载 JSON 格式的运行报告。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def parse_run_log_file(path: str) -> str:
    """读取纯文本运行日志。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


def extract_failures(report: dict[str, Any]) -> list[dict[str, Any]]:
    """从运行报告中提取失败/异常步骤。"""
    failures: list[dict[str, Any]] = []
    for r in report.get("stepResults", []) or []:
        status = str(r.get("status", "")).lower()
        err = str(r.get("error", "") or "").strip()
        if status in ("failed", "error", "fallback") or err:
            failures.append({
                "stepId": r.get("stepId", ""),
                "stepName": r.get("stepName", ""),
                "status": r.get("status", ""),
                "error": err,
            })
    return failures


def build_diagnosis_prompt(log_text: str, flow_steps: list[dict[str, Any]] | None = None) -> str:
    """构建给 LLM 的诊断提示词。"""
    prompt = [
        "你是一名 WT（Meteodyn WT）桌面自动化调试专家。",
        "下面是一段执行日志/运行报告，请完成以下任务：",
        "1) 定位失败或异常的 Step（给出 stepId / stepName）；",
        "2) 分析可能的根因（如控件未找到、假成功、窗口错位、输入未提交、超时等）；",
        "3) 给出可操作的修复建议，尽量具体到对应步骤的 "
        "actionConfig（controlId / timeoutSeconds / onError / fallbackChain）应如何修改。",
        "",
        "【执行日志/报告】",
        log_text.strip() if log_text and log_text.strip() else "（空）",
    ]
    if flow_steps:
        prompt.append("")
        prompt.append("【相关流程步骤（用于对照定位）】")
        for i, s in enumerate(flow_steps, 1):
            ac = s.get("actionConfig", {}) or {}
            prompt.append(
                f"  {i}. {s.get('name', '')} action={ac.get('action','')} "
                f"controlId={ac.get('controlId','')} onError={ac.get('onError','')}"
            )
    prompt.append("")
    prompt.append("请按'失败定位 → 原因分析 → 修复建议'三段式用中文回答。")
    return "\n".join(prompt)
