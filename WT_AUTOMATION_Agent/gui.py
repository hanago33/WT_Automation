# encoding: utf-8
"""WT_AUTOMATION_Agent 对话式 GUI —— 现代聊天界面。

零额外依赖，纯 Python 标准库实现。
启动后自动打开浏览器，提供：
  - 对话式自然语言 → 步骤转换
  - LLM 配置面板（Base URL / API Key / Model）
  - 测试连接 / 保存配置到本地
  - 控件库加载 / Skill 管理

启动方式：
    python -m WT_AUTOMATION_Agent.gui
    python -m WT_AUTOMATION_Agent.cli --gui
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
import webbrowser
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs


# ---------------------------------------------------------------------------
# 配置持久化
# ---------------------------------------------------------------------------

CONFIG_FILE = Path(__file__).resolve().parent / "_gui_config.json"


def _load_saved_config() -> dict[str, str]:
    """加载本地保存的配置。"""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config_to_file(data: dict[str, str]) -> None:
    """持久化配置到本地。"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# API 路由处理
# ---------------------------------------------------------------------------

def _json_response(handler, data: Any, status: int = 200) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        handler.wfile.write(body)
    except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError, OSError):
        pass  # 客户端已断开，忽略


def _read_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    body = handler.rfile.read(length)
    return json.loads(body.decode("utf-8"))


def _parse_retry_codes(val) -> tuple:
    """将 '429,500,502,503,504' 转为 (429, 500, ...)。"""
    if isinstance(val, (list, tuple)):
        return tuple(int(v) for v in val)
    try:
        return tuple(int(x.strip()) for x in str(val).split(",") if x.strip())
    except (ValueError, TypeError):
        return (429, 500, 502, 503, 504)


def _build_agent(config_data: dict):
    from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig

    return DslAgent(DslAgentConfig(
        base_url=config_data.get("base_url", ""),
        api_key=config_data.get("api_key", ""),
        model=config_data.get("model", "gpt-4o"),
        timeout=int(config_data.get("timeout", 120)),
        max_retries=int(config_data.get("max_retries", 3)),
        retry_backoff=float(config_data.get("retry_backoff", 2.0)),
        retry_on_status=_parse_retry_codes(config_data.get("retry_codes", "")),
    ))


def _build_context(flow_path: str = "", project_desc: str = ""):
    from WT_AUTOMATION_Agent.control_index import build_context_for_agent
    from WT_AUTOMATION_Agent.skill_bridge import load_all_skills_text

    skill_text = load_all_skills_text()
    # 注意：不再把 flow_definition.json 作为“可用控件库”注入 —— 那套索引只有
    # 4 个字段且与 find_control 工具的数据源（control_maps）不一致，会误导模型。
    # 控件库信息统一由 control_search（tree_summary 概览 + find_control 工具）提供。
    return build_context_for_agent(
        flow_path=None,
        project_description=project_desc,
        skill_text=skill_text,
    )


def handle_api(path: str, handler) -> None:
    """简易路由分发。"""
    parsed = urlparse(path)
    route = parsed.path.rstrip("/")

    if handler.command == "OPTIONS":
        handler.send_response(204)
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")
        handler.end_headers()
        return

    try:
        if route == "/api/config" and handler.command == "POST":
            data = _read_body(handler)
            _save_config_to_file(data)
            return _json_response(handler, {"ok": True, "config": data})

        if route == "/api/config" and handler.command == "GET":
            saved = _load_saved_config()
            from WT_AUTOMATION_Agent import DslAgentConfig
            c = DslAgentConfig()
            retry_codes = saved.get("retry_codes", "")
            if isinstance(retry_codes, (list, tuple)):
                retry_codes = ",".join(str(x) for x in retry_codes)
            return _json_response(handler, {
                "base_url": saved.get("base_url", c.base_url),
                "api_key": saved.get("api_key", c.api_key),
                "model": saved.get("model", c.model),
                "project_desc": saved.get("project_desc", ""),
                "control_file": saved.get("control_file", ""),
                "timeout": saved.get("timeout", c.timeout),
                "max_retries": saved.get("max_retries", c.max_retries),
                "retry_backoff": saved.get("retry_backoff", c.retry_backoff),
                "retry_codes": retry_codes or "429,500,502,503,504",
            })

        if route == "/api/test-connection" and handler.command == "POST":
            data = _read_body(handler)
            agent = _build_agent(data)
            ok = agent.test_connection()
            return _json_response(handler, {"ok": ok, "message": "连接成功" if ok else "连接失败"})

        if route == "/api/schemas" and handler.command == "GET":
            from WT_AUTOMATION_Agent.schemas import get_action_names, get_action_schema
            schemas = {name: get_action_schema(name) for name in get_action_names()}
            return _json_response(handler, schemas)

        if route == "/api/skills" and handler.command == "GET":
            from WT_AUTOMATION_Agent.skill_bridge import get_builtin_skills, discover_skills
            builtin = get_builtin_skills()
            extra = discover_skills([".codebuddy/skills", ".agents"])
            all_skills = builtin + extra
            return _json_response(handler, [
                {"name": s.name, "description": s.description, "file_path": s.file_path}
                for s in all_skills
            ])

        # ── 多模型配置档案 ──
        if route == "/api/profiles" and handler.command == "GET":
            from WT_AUTOMATION_Agent.model_profiles import migrate_from_legacy, list_profiles
            migrate_from_legacy()
            return _json_response(handler, list_profiles())
        if route == "/api/profiles" and handler.command == "POST":
            from WT_AUTOMATION_Agent.model_profiles import save_profile
            data = _read_body(handler)
            ok = save_profile(data.get("name", ""), data.get("config", {}))
            return _json_response(handler, {"ok": ok})
        if route == "/api/profiles" and handler.command == "DELETE":
            from WT_AUTOMATION_Agent.model_profiles import delete_profile
            from urllib.parse import parse_qs
            qs = parse_qs(parsed.query)
            name = (qs.get("name") or [""])[0]
            ok = delete_profile(name)
            return _json_response(handler, {"ok": ok})
        if route == "/api/profiles" and handler.command == "PATCH":
            from WT_AUTOMATION_Agent.model_profiles import set_default
            data = _read_body(handler)
            ok = set_default(data.get("name", ""))
            return _json_response(handler, {"ok": ok})

        # ── 项目知识库 ──
        if route == "/api/kb/build" and handler.command == "POST":
            from WT_AUTOMATION_Agent import knowledge_base
            kb = knowledge_base.rebuild()
            return _json_response(handler, kb.status())
        if route == "/api/kb/status" and handler.command == "GET":
            from WT_AUTOMATION_Agent import knowledge_base
            return _json_response(handler, knowledge_base.get_knowledge_base().status())
        if route == "/api/kb/sources" and handler.command == "GET":
            from WT_AUTOMATION_Agent import knowledge_base
            return _json_response(handler, knowledge_base.get_knowledge_base().list_sources())
        if route == "/api/kb/search" and handler.command == "POST":
            from WT_AUTOMATION_Agent import knowledge_base
            data = _read_body(handler)
            try:
                top_k = int(data.get("top_k", 5))
            except (TypeError, ValueError):
                top_k = 5
            hits = knowledge_base.get_knowledge_base().retrieve(data.get("query", ""), top_k=top_k)
            return _json_response(handler, hits)

        # ── 控件库语义检索 ──
        if route == "/api/control-search" and handler.command == "POST":
            from WT_AUTOMATION_Agent import control_search
            data = _read_body(handler)
            query = (data.get("query") or "").strip()
            try:
                top_k = int(data.get("top_k") or 5)
            except (TypeError, ValueError):
                top_k = 5
            if not query:
                return _json_response(handler, {"status": "error", "message": "查询为空"}, 400)
            cands = control_search.find_controls(query, top_k=top_k)
            return _json_response(handler, {
                "status": "ok", "query": query,
                "count": len(cands), "candidates": cands,
            })

        if route == "/api/control-stats" and handler.command == "GET":
            from WT_AUTOMATION_Agent import control_search
            return _json_response(handler, control_search.stats())

        # ── 项目资产总览（控件库 / 流程包 / 知识库 / 技能）──
        if route == "/api/overview" and handler.command == "GET":
            import glob
            from WT_AUTOMATION_Agent import control_search
            from WT_AUTOMATION_Agent.knowledge_base import get_knowledge_base
            from WT_AUTOMATION_Agent.skill_bridge import get_builtin_skills
            flow_packages = glob.glob(
                os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "flow_packages", "*.json")
            )
            kb = get_knowledge_base().status()
            return _json_response(handler, {
                "controls": control_search.stats().get("total", 0),
                "flows": len(flow_packages),
                "kb_sources": kb.get("sources", 0),
                "kb_chunks": kb.get("chunks", 0),
                "kb_areas": kb.get("areas", {}),
                "skills": len(get_builtin_skills()),
            })

        # ── 流程解释 / 编辑 / 比对 ──
        if route == "/api/flow/explain" and handler.command == "POST":
            from WT_AUTOMATION_Agent import flow_ops
            data = _read_body(handler)
            flow = flow_ops.load_flow((data.get("flow_path") or "").strip())
            if not flow:
                return _json_response(handler, {"status": "error", "message": "流程文件不存在或解析失败"}, 400)
            agent = _build_agent(data.get("config", {}))
            answer = agent.explain_flow(flow, (data.get("question") or "").strip())
            return _json_response(handler, {"status": "ok", "answer": answer})

        if route == "/api/flow/edit" and handler.command == "POST":
            from WT_AUTOMATION_Agent import flow_ops
            data = _read_body(handler)
            flow = flow_ops.load_flow((data.get("flow_path") or "").strip())
            if not flow:
                return _json_response(handler, {"status": "error", "message": "流程文件不存在或解析失败"}, 400)
            agent = _build_agent(data.get("config", {}))
            result = agent.edit_flow(flow, (data.get("instruction") or "").strip(), write_back=False)
            return _json_response(handler, {"status": "ok" if result.get("ok") else "partial", **result})

        if route == "/api/flow/diff" and handler.command == "POST":
            from WT_AUTOMATION_Agent import flow_ops
            data = _read_body(handler)
            flow_a = flow_ops.load_flow((data.get("flow_a") or "").strip())
            flow_b = flow_ops.load_flow((data.get("flow_b") or "").strip())
            if not flow_a or not flow_b:
                return _json_response(handler, {"status": "error", "message": "两个流程文件均需有效"}, 400)
            agent = _build_agent(data.get("config", {}))
            answer = agent.diff_flows(flow_a, flow_b)
            return _json_response(handler, {"status": "ok", "answer": answer})

        # ── 流程链路检查审核纠错（确定性规则 + LLM 语义审核） ──
        if route == "/api/flow/audit" and handler.command == "POST":
            from WT_AUTOMATION_Agent import flow_ops
            data = _read_body(handler)
            flow_path = (data.get("flow_path") or "").strip()
            flow = flow_ops.load_flow(flow_path)
            if not flow:
                return _json_response(handler, {"status": "error", "message": "流程文件不存在或解析失败"}, 400)
            agent = _build_agent(data.get("config", {}))
            try:
                report = agent.audit_flow(flow)
            except Exception as exc:
                return _json_response(handler, {"status": "error", "message": f"审核失败：{exc}"}, 500)
            return _json_response(handler, {"status": "ok", **report})

        # ── 一键修复：备份 → 确定性修复 → LLM 语义建议逐条确认后写回 ──
        if route == "/api/flow/repair" and handler.command == "POST":
            from WT_AUTOMATION_Agent import flow_ops, flow_repair

            data = _read_body(handler)
            flow_path = (data.get("flow_path") or "").strip()
            flow = flow_ops.load_flow(flow_path)
            if not flow:
                return _json_response(handler, {"status": "error", "message": "流程文件不存在或解析失败"}, 400)
            agent = _build_agent(data.get("config", {}))
            try:
                repaired, report = flow_repair.repair_flow_definition(flow)
                llm_suggestions = agent.repair_flow_suggestions(repaired, report)
                apply = data.get("apply")
                written = False
                backup_path = ""
                if apply is not None:
                    if apply == "all":
                        apply_indices = list(range(len(llm_suggestions)))
                    elif isinstance(apply, list):
                        apply_indices = []
                        for raw_idx in apply:
                            try:
                                idx = int(raw_idx)
                            except (TypeError, ValueError):
                                continue
                            if 0 <= idx < len(llm_suggestions):
                                apply_indices.append(idx)
                    else:
                        apply_indices = []
                    for idx in apply_indices:
                        item = llm_suggestions[idx]
                        patch = item.get("proposed_patch") or {}
                        step_index = int(item.get("step_index", 0) or 0)
                        steps = repaired.get("steps", [])
                        if not (1 <= step_index <= len(steps)):
                            continue
                        step = steps[step_index - 1]
                        if isinstance(step, dict) and isinstance(patch, dict):
                            step.update(patch)
                    backup_path = flow_repair.save_with_backup(flow_path, repaired, report)
                    written = True
                return _json_response(handler, {
                    "status": "ok",
                    "written": written,
                    "backup_path": backup_path,
                    "summary": report.get("summary", ""),
                    "auto_fixed_count": report.get("auto_fixed_count", 0),
                    "pending_confirm_count": report.get("pending_confirm_count", 0),
                    "pending_confirm": report.get("pending_confirm", []),
                    "llm_suggestions": llm_suggestions,
                })
            except Exception as exc:
                return _json_response(handler, {"status": "error", "message": f"修复失败：{exc}"}, 500)


        # ── 自然语言生成 → 保存为流程文件（组装完整 flow_definition + 执行器校验 + 落盘） ──
        if route == "/api/flow/save" and handler.command == "POST":
            data = _read_body(handler)
            steps = data.get("steps")
            if not isinstance(steps, list):
                return _json_response(handler, {"status": "error", "message": "缺少 steps 列表"}, 400)

            # 默认保存为另存新文件（带时间戳），不覆盖链路编辑器现有的 flow_definition.json
            flow_path = (data.get("flow_path") or "").strip()
            if not flow_path:
                _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                _stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                flow_path = os.path.join(_project_root, "workspace", f"flow_definition_agent_{_stamp}.json")

            # 清洗步骤：去除前端临时字段、补空 id
            clean: list[dict] = []
            for s in steps:
                if not isinstance(s, dict):
                    continue
                s = {k: v for k, v in s.items() if not str(k).startswith("__")}
                if not str(s.get("id", "")).strip():
                    s["id"] = "step_" + uuid.uuid4().hex[:10]
                clean.append(s)
            if not clean:
                return _json_response(handler, {"status": "error", "message": "没有可保存的步骤"}, 400)

            # 组装顶层结构；目标文件已存在则沿用其 runtimeConfig / flowPackages / aiAgentConfig
            flow: dict[str, Any] = {
                "version": "1.0",
                "project": "WT_Automation",
                "description": "由 WT Agent 自然语言生成",
                "lastUpdated": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                "runtimeConfig": {"gmExe": "", "sourceFilePath": "", "outputDir": "", "projectionFilePath": ""},
                "flowPackages": [],
                "steps": clean,
            }
            try:
                if os.path.exists(flow_path):
                    with open(flow_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                    if isinstance(existing, dict):
                        for k in ("version", "project", "description", "runtimeConfig", "flowPackages", "aiAgentConfig"):
                            if existing.get(k):
                                flow[k] = existing[k]
            except (OSError, json.JSONDecodeError):
                pass
            flow["steps"] = clean

            # 自动关联模板兜底：步骤未显式配置 fallbackTemplate 且控件存在可解析的
            # templateKey（采集器/伴随拾取已回填）时自动写入，与链路编辑器保存行为一致
            try:
                _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if _project_root not in sys.path:
                    sys.path.insert(0, _project_root)
                import image_template_index
                image_template_index.auto_associate_fallback_templates(clean)
            except Exception:
                pass

            # 用执行器同源校验（与链路编辑器加载时一致），不通过也能保存但会返回错误清单
            errors: list[str] = []
            try:
                _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                if _project_root not in sys.path:
                    sys.path.insert(0, _project_root)
                from wt_flow_validation import validate_step_definition
                for i, st in enumerate(clean, 1):
                    for e in validate_step_definition(st):
                        errors.append(f"步骤{i}: {e}")
            except ImportError:
                errors.append("警告：未能加载执行器校验模块 wt_flow_validation，已跳过校验。")

            try:
                os.makedirs(os.path.dirname(flow_path), exist_ok=True)
                with open(flow_path, "w", encoding="utf-8") as f:
                    json.dump(flow, f, ensure_ascii=False, indent=2)
            except OSError as exc:
                return _json_response(handler, {"status": "error", "message": f"保存失败：{exc}"}, 500)

            return _json_response(handler, {
                "status": "ok" if not errors else "warning",
                "saved": True,
                "path": flow_path,
                "step_count": len(clean),
                "errors": errors,
            })

        # 打开已保存流程文件所在目录（Windows）
        if route == "/api/flow/open-dir" and handler.command == "POST":
            data = _read_body(handler)
            path = (data.get("path") or "").strip()
            if not path or not os.path.exists(path):
                return _json_response(handler, {"status": "error", "message": "路径不存在"}, 400)
            try:
                if not hasattr(os, "startfile"):
                    return _json_response(handler, {"status": "error", "message": "当前系统不支持打开目录"}, 400)
                os.startfile(os.path.dirname(os.path.abspath(path)))  # type: ignore[attr-defined]
            except OSError as exc:
                return _json_response(handler, {"status": "error", "message": str(exc)}, 500)
            return _json_response(handler, {"status": "ok"})

        # ── 执行日志 / 运行报告诊断 ──
        if route == "/api/log/diagnose" and handler.command == "POST":
            from WT_AUTOMATION_Agent import flow_ops, log_diagnosis
            data = _read_body(handler)
            log_input = (data.get("log_input") or "").strip()
            flow_path = (data.get("flow_path") or "").strip()
            flow_steps = None
            if flow_path and os.path.exists(flow_path):
                fl = flow_ops.load_flow(flow_path)
                if fl:
                    flow_steps = fl.get("steps", [])
            if not log_input:
                return _json_response(handler, {"status": "error", "message": "请提供日志内容或报告路径"}, 400)
            agent = _build_agent(data.get("config", {}))
            answer = agent.diagnose_log(log_input, flow_steps=flow_steps)
            return _json_response(handler, {"status": "ok", "answer": answer})

        if route == "/api/chat" and handler.command == "POST":
            data = _read_body(handler)
            config_data = data.get("config", {})
            message = data.get("message", "")
            flow_path = config_data.get("control_file", "")
            project_desc = config_data.get("project_desc", "")
            conversation_id = data.get("conversation_id") or None
            kb_enabled = bool(data.get("kb_enabled", True))
            compress = bool(data.get("compress", True))

            if not message.strip():
                return _json_response(handler, {"error": "消息不能为空"}, 400)

            agent = _build_agent(config_data)
            context = _build_context(flow_path, project_desc)

            # 判断是对话还是转换
            mode = data.get("mode", "chat")
            if mode in ("sequence", "step"):
                if mode == "sequence":
                    steps = agent.nl_to_sequence(message, context, conversation_id=conversation_id, compress=compress)
                else:
                    steps = agent.nl_to_step(message, context, conversation_id=conversation_id, compress=compress)
                resp: dict[str, Any] = {"type": "steps", "steps": steps, "mode": mode}
                # 生成失败时返回诊断信息（如模型不支持 function calling），供前端提示
                diag = getattr(agent, "_last_generation_diagnostic", None)
                if not steps and diag:
                    resp["warning"] = diag
                return _json_response(handler, resp)
            else:
                reply = agent.chat(message, context, conversation_id=conversation_id,
                                   kb_enabled=kb_enabled, compress=compress)
                return _json_response(handler, {"type": "chat", "reply": reply, "conversation_id": conversation_id})

        # 会话管理 API
        if route == "/api/conversations" and handler.command == "GET":
            from WT_AUTOMATION_Agent.history_store import list_conversations
            sessions = list_conversations()
            return _json_response(handler, sessions)

        if route == "/api/conversations" and handler.command == "POST":
            from WT_AUTOMATION_Agent.agent import DslAgent
            data = _read_body(handler)
            title = data.get("title", "")
            agent = _build_agent(data.get("config", {}))
            conv_id = agent.create_conversation(title=title)
            return _json_response(handler, {"id": conv_id, "title": title})

        if route.startswith("/api/conversations/") and handler.command == "GET":
            from WT_AUTOMATION_Agent.history_store import load_conversation
            session_id = route.split("/")[-1]
            conv = load_conversation(session_id)
            if not conv:
                return _json_response(handler, {"error": "会话不存在"}, 404)
            return _json_response(handler, conv.to_dict())

        if route.startswith("/api/conversations/") and handler.command == "DELETE":
            from WT_AUTOMATION_Agent.agent import DslAgent
            session_id = route.split("/")[-1]
            agent = _build_agent({})
            ok = agent.delete_conversation(session_id)
            return _json_response(handler, {"ok": ok})

        if route.startswith("/api/conversations/") and handler.command == "PATCH":
            from WT_AUTOMATION_Agent.agent import DslAgent
            session_id = route.split("/")[-1]
            data = _read_body(handler)
            agent = _build_agent({})
            if "title" in data:
                ok = agent.rename_conversation(session_id, data["title"])
                return _json_response(handler, {"ok": ok})
            if data.get("clear"):
                ok = agent.clear_conversation(session_id)
                return _json_response(handler, {"ok": ok})
            return _json_response(handler, {"error": "无效操作"}, 400)

        return _json_response(handler, {"error": "Not Found"}, 404)

    except Exception as exc:
        return _json_response(handler, {"error": str(exc)}, 500)


# ---------------------------------------------------------------------------
# HTML 模板
# ---------------------------------------------------------------------------

HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CRRC 中车 · WT 智能流程构建助手</title>
<style>
:root {
  --bg: #F6F3F1;
  --sidebar-bg: #2A0A10;
  --sidebar-text: #EFD9DC;
  --sidebar-hover: rgba(255,255,255,0.09);
  --sidebar-active: rgba(200,16,46,0.32);
  --input-bg: rgba(255,255,255,0.10);
  --input-border: rgba(255,255,255,0.20);
  --input-text: #FCEDEF;
  --accent: #C8102E;
  --accent-hover: #9F0D23;
  --accent-light: #E9A5AD;
  --accent2: #3A4553;
  --gold: #C7A24B;
  --user-bubble: #C8102E;
  --user-bubble-text: #fff;
  --agent-bubble: #fff;
  --agent-bubble-text: #2F2A2B;
  --chat-bg: #F1ECEC;
  --border: #E6D9DB;
  --text: #2F2A2B;
  --text-secondary: #8A7E80;
  --danger: #E04545;
  --success: #1BBF73;
  --warning: #F5A623;
  --radius: 14px;
  --radius-sm: 8px;
  --shadow: 0 2px 8px rgba(42,10,16,0.07);
  --shadow-lg: 0 10px 34px rgba(42,10,16,0.16);
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:var(--font); background:var(--bg); height:100vh; display:flex; overflow:hidden; color:var(--text); }

/* ── CRRC 中车品牌 ── */
.brand { display:flex; align-items:center; gap:10px; }
.brand-text { display:flex; flex-direction:column; min-width:0; }
.brand-logo {
  width:40px; height:40px; border-radius:10px; flex-shrink:0;
  background:linear-gradient(135deg,#D71920 0%,#7A0F1B 60%,#2A0A10 100%);
  color:#fff; display:flex; align-items:center; justify-content:center;
  font-size:11px; font-weight:800; letter-spacing:0.5px;
  box-shadow:0 3px 10px rgba(200,16,46,0.38);
}
.brand-logo-sm {
  width:32px; height:32px; border-radius:8px; flex-shrink:0;
  background:linear-gradient(135deg,#D71920 0%,#7A0F1B 60%,#2A0A10 100%);
  color:#fff; display:flex; align-items:center; justify-content:center;
  font-size:9px; font-weight:800; letter-spacing:0.5px;
}
.brand-title { font-size:15px; font-weight:800; color:#fff; line-height:1.25; }
.brand-sub { font-size:11px; color:rgba(255,255,255,0.66); margin-top:2px; letter-spacing:0.3px; }
.brand-badge {
  display:inline-flex; align-items:center; gap:4px; margin-left:8px;
  font-size:9px; font-weight:700; letter-spacing:0.6px;
  color:#F4E3C5; background:rgba(199,162,75,0.16);
  border:1px solid rgba(199,162,75,0.4); padding:2px 8px; border-radius:999px;
}
.brand-dark .brand-title { color:var(--text); }
.brand-dark .brand-sub { color:var(--text-secondary); }

/* 快捷指令 */
.quick-chips { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin-top:20px; max-width:480px; }
.quick-chip {
  padding:7px 15px; border-radius:999px; border:1px solid #F1C1C8;
  background:#fff; color:var(--accent); font-size:12px; font-weight:600; cursor:pointer;
  font-family:var(--font); transition:all .18s; box-shadow:0 1px 3px rgba(92,10,18,0.08);
}
.quick-chip:hover { background:var(--accent); color:#fff; border-color:var(--accent); transform:translateY(-1px); box-shadow:0 5px 14px rgba(200,16,46,0.30); }
.quick-chip:active { transform:translateY(0); }

/* 连接状态胶囊 */
.conn-pill {
  display:inline-flex; align-items:center; gap:6px; padding:5px 12px; border-radius:999px;
  font-size:11px; font-weight:700; background:#FBEEF0; border:1px solid var(--border); color:var(--text-secondary);
}
.conn-pill .dot { width:7px; height:7px; border-radius:50%; background:#CDB3B8; }
.conn-pill.on { color:#0B7A45; border-color:#A9DFC4; background:#EAF7F0; }
.conn-pill.on .dot { background:#1BBF73; box-shadow:0 0 0 3px rgba(27,191,115,0.18); }

/* Sidebar */
.sidebar {
  width:280px; min-width:280px; background:linear-gradient(180deg,#8E111F 0%,#5C0A12 45%,#2A0A10 100%);
  color:var(--sidebar-text); display:flex; flex-direction:column;
  border-right:1px solid rgba(255,255,255,0.08);
}
.sidebar-header {
  padding:20px 18px 14px; border-bottom:1px solid rgba(255,255,255,0.06);
}
.sidebar-header h2 { font-size:16px; font-weight:700; color:#fff; letter-spacing:-0.3px; }
.sidebar-header .sub { font-size:11px; color:var(--accent-light); margin-top:2px; }
.sidebar-body { flex:1; overflow-y:auto; padding:14px 12px; }
.sidebar-body::-webkit-scrollbar { width:4px; }
.sidebar-body::-webkit-scrollbar-thumb { background:rgba(255,255,255,0.1); border-radius:4px; }

.config-section { margin-bottom:16px; }
.config-section label { display:block; font-size:11px; font-weight:600; color:var(--sidebar-text); margin-bottom:5px; text-transform:uppercase; letter-spacing:0.5px; opacity:0.7; }
.config-section input, .config-section textarea {
  width:100%; padding:9px 11px; border:1px solid var(--input-border); border-radius:var(--radius-sm);
  background:var(--input-bg); color:var(--input-text); font-size:12px; font-family:var(--font);
  outline:none; transition:border-color 0.2s;
}
.config-section input:focus, .config-section textarea:focus { border-color:var(--accent); }
.config-section textarea { resize:vertical; min-height:50px; }
.api-key-wrap { position:relative; }
.api-key-wrap input { padding-right:40px; }
.api-key-wrap .toggle { position:absolute; right:8px; top:50%; transform:translateY(-50%); background:none; border:none; color:var(--sidebar-text); cursor:pointer; font-size:14px; opacity:0.6; }
.api-key-wrap .toggle:hover { opacity:1; }

.btn {
  display:inline-flex; align-items:center; justify-content:center; gap:6px;
  padding:8px 14px; border:none; border-radius:var(--radius-sm); font-size:12px; font-weight:600;
  cursor:pointer; transition:all 0.2s; font-family:var(--font);
}
.btn-primary { background:var(--accent); color:#fff; width:100%; }
.btn-primary:hover { background:var(--accent-hover); }
.btn-secondary { background:var(--sidebar-hover); color:var(--sidebar-text); width:100%; }
.btn-secondary:hover { background:var(--sidebar-active); }
.btn-sm { padding:6px 10px; font-size:11px; }
.btn-row { display:flex; gap:6px; margin-top:6px; }
.status-dot { display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:4px; }
.status-dot.connected { background:var(--success); }
.status-dot.disconnected { background:var(--danger); }
.collapsible-header {
  cursor:pointer; display:flex; align-items:center; justify-content:space-between;
  font-size:11px; font-weight:600; color:var(--sidebar-text); opacity:0.6;
  text-transform:uppercase; letter-spacing:0.5px; padding:4px 0; margin-bottom:4px;
}
.collapsible-header:hover { opacity:1; }
.collapsible-header .arrow { transition:transform 0.2s; font-size:10px; }
.collapsible-header.open .arrow { transform:rotate(90deg); }
.collapsible-body { display:none; }
.collapsible-body.open { display:block; }
.advanced-row { display:flex; gap:6px; margin-bottom:12px; }
.advanced-row .half { flex:1; }
.advanced-row label { display:block; font-size:10px; color:var(--sidebar-text); opacity:0.5; margin-bottom:3px; }
.advanced-row input { width:100%; padding:6px 8px; border:1px solid var(--input-border); border-radius:4px; background:var(--input-bg); color:var(--input-text); font-size:11px; font-family:var(--font); outline:none; }
.advanced-row input:focus { border-color:var(--accent); }

/* Chat area */
.main { flex:1; display:flex; flex-direction:column; min-width:0; }
.chat-header {
  padding:14px 20px; background:#fff; border-bottom:1px solid var(--border);
  border-top:3px solid var(--accent);
  display:flex; align-items:center; justify-content:space-between;
}
.chat-header h3 { font-size:15px; font-weight:600; color:var(--text); }
.chat-header .actions { display:flex; gap:8px; align-items:center; }
.mode-toggle { display:flex; background:var(--chat-bg); border-radius:var(--radius-sm); padding:2px; }
.mode-btn {
  padding:6px 12px; border:none; background:none; font-size:11px; font-weight:600;
  cursor:pointer; border-radius:6px; color:var(--text-secondary); font-family:var(--font);
  transition:all 0.15s;
}
.mode-btn.active { background:#fff; color:var(--accent); box-shadow:var(--shadow); }

.messages {
  flex:1; overflow-y:auto; padding:20px;
  display:flex; flex-direction:column; gap:16px; background:var(--chat-bg);
  background:radial-gradient(1200px 320px at 50% -80px, rgba(200,16,46,0.06) 0%, rgba(200,16,46,0) 70%), var(--chat-bg);
}
.messages::-webkit-scrollbar { width:5px; }
.messages::-webkit-scrollbar-thumb { background:rgba(0,0,0,0.12); border-radius:4px; }

.empty-state { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; color:var(--text-secondary); text-align:center; padding:40px; }
.empty-state .icon {
  width:72px; height:72px; font-size:34px; margin-bottom:18px; opacity:1;
  background:linear-gradient(135deg,#D71920,#C8102E); color:#fff; border-radius:50%;
  display:flex; align-items:center; justify-content:center;
  box-shadow:0 10px 26px rgba(200,16,46,0.32);
}
.empty-state p { font-size:14px; margin-bottom:4px; }
.empty-state .hint { font-size:12px; opacity:0.6; max-width:400px; line-height:1.6; }

.message { display:flex; gap:10px; max-width:85%; animation:fadeIn 0.2s ease; }
@keyframes fadeIn { from{opacity:0;transform:translateY(6px);} to{opacity:1;transform:translateY(0);} }
.message.user { align-self:flex-end; flex-direction:row-reverse; }
.message.agent { align-self:flex-start; }
.message .avatar {
  width:32px; height:32px; border-radius:50%; display:flex; align-items:center;
  justify-content:center; font-size:15px; flex-shrink:0;
}
.message.user .avatar { background:var(--accent); color:#fff; }
.message.agent .avatar { background:#e8e8f0; color:var(--accent); }
.message .bubble {
  padding:12px 16px; border-radius:var(--radius); font-size:13px; line-height:1.6;
  word-break:break-word; position:relative;
}
.message.user .bubble { background:var(--user-bubble); color:var(--user-bubble-text); border-bottom-right-radius:4px; }
.message.agent .bubble { background:var(--agent-bubble); color:var(--agent-bubble-text); border-bottom-left-radius:4px; box-shadow:var(--shadow); }
.message .bubble pre {
  background:rgba(0,0,0,0.06); padding:10px 14px; border-radius:var(--radius-sm);
  overflow-x:auto; font-size:11px; margin:8px 0; white-space:pre-wrap; word-break:break-all;
  max-height:300px; overflow-y:auto; position:relative;
}
.message .bubble code { font-family:"SF Mono","Fira Code","Consolas",monospace; font-size:11px; }
.message .meta { font-size:10px; opacity:0.5; margin-top:6px; display:flex; align-items:center; gap:8px; }
.copy-btn {
  background:none; border:none; color:var(--text-secondary); cursor:pointer; font-size:11px;
  padding:2px 6px; border-radius:4px; opacity:0.5; transition:opacity 0.2s;
}
.copy-btn:hover { opacity:1; background:rgba(0,0,0,0.05); }
.steps-summary {
  background:rgba(200,16,46,0.06); padding:8px 12px; border-radius:var(--radius-sm);
  font-size:12px; margin-top:6px; display:flex; align-items:center; gap:8px;
}
.steps-summary .badge { background:var(--accent); color:#fff; font-size:10px; padding:2px 8px; border-radius:10px; font-weight:600; }
.step-detail { margin-top:8px; border:1px solid var(--border); border-radius:var(--radius-sm); overflow:hidden; }
.step-detail .step-item {
  padding:8px 12px; border-bottom:1px solid var(--border); font-size:12px;
  display:flex; align-items:center; gap:8px; cursor:pointer; transition:background 0.15s;
}
.step-detail .step-item:last-child { border-bottom:none; }
.step-detail .step-item:hover { background:rgba(200,16,46,0.04); }
.step-detail .step-item .step-action { color:var(--accent); font-weight:600; min-width:50px; }

/* 序列模式：可拖拽排序 / 可展开步骤清单 */
.seq-steps { margin-top:8px; border:1px solid var(--border); border-radius:var(--radius-sm); overflow:hidden; }
.seq-step {
  display:flex; align-items:center; gap:8px; padding:8px 10px;
  border-bottom:1px solid var(--border); font-size:12px; background:#fff;
  transition:background 0.15s; cursor:default;
}
.seq-step:last-child { border-bottom:none; }
.seq-step.expanded { background:rgba(200,16,46,0.05); }
.seq-step.dragging { opacity:0.45; }
.seq-step.drag-before { box-shadow:inset 0 2px 0 0 var(--accent); }
.seq-step.drag-after { box-shadow:inset 0 -2px 0 0 var(--accent); }
.seq-step .drag-handle { cursor:grab; color:#a0a0a8; font-size:14px; user-select:none; }
.seq-step .drag-handle:active { cursor:grabbing; }
.seq-step .seq-toggle {
  border:none; background:transparent; cursor:pointer; color:var(--accent);
  font-size:10px; width:18px; padding:0;
}
.seq-step .seq-num {
  background:var(--accent); color:#fff; font-size:10px; font-weight:600;
  min-width:18px; height:18px; line-height:18px; text-align:center; border-radius:9px; flex:none;
}
.seq-step .seq-action { color:var(--accent); font-weight:600; min-width:54px; }
.seq-step .seq-name { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.seq-step .seq-copy {
  border:1px solid var(--border); background:#fff; border-radius:4px; cursor:pointer;
  font-size:11px; padding:2px 8px; color:#555; flex:none;
}
.seq-step .seq-copy:hover { background:rgba(200,16,46,0.08); }
.seq-step .seq-detail {
  flex-basis:100%; order:9; margin:6px 0 0; padding:8px 10px; background:#f7f7fb;
  border-radius:var(--radius-sm); font-size:11px; line-height:1.5; max-height:240px; overflow:auto;
  white-space:pre-wrap; word-break:break-all;
}
.seq-footer { margin-top:8px; display:flex; gap:6px; }
.seq-footer .btn { width:auto; flex:1; }
.seq-footer .btn-sm { font-size:12px; padding:4px 12px; }
.seq-step:focus { outline:2px solid var(--accent); outline-offset:-2px; }
.seq-step:focus:not(.expanded) { background:rgba(200,16,46,0.06); }

/* Input area */
.input-area {
  padding:14px 20px; background:#fff; border-top:1px solid var(--border);
  display:flex; gap:10px; align-items:flex-end; flex-wrap:wrap;
}
.mode-banner {
  flex-basis:100%; padding:6px 12px; margin-bottom:2px; border-radius:8px;
  background:#FFF7E6; border:1px solid #F2D29A; color:#8A5B10; font-size:12px;
}
.input-area textarea {
  flex:1; padding:12px 16px; border:1px solid var(--border); border-radius:var(--radius);
  font-size:13px; font-family:var(--font); resize:none; outline:none; max-height:150px;
  transition:border-color 0.2s; line-height:1.5;
}
.input-area textarea:focus { border-color:var(--accent); }
.input-area .send-btn {
  width:42px; height:42px; border-radius:50%; border:none;
  background:linear-gradient(135deg,#D71920,#A00D24);
  color:#fff; font-size:18px; cursor:pointer; display:flex; align-items:center;
  justify-content:center; transition:all 0.2s; flex-shrink:0;
  box-shadow:0 4px 12px rgba(200,16,46,0.30);
}
.input-area .send-btn:hover { background:var(--accent-hover); transform:scale(1.04); }
.input-area .send-btn:disabled { background:#ccc; cursor:not-allowed; transform:none; }

/* Toast */
.toast {
  position:fixed; top:20px; right:20px; padding:12px 18px; border-radius:var(--radius-sm);
  color:#fff; font-size:13px; font-weight:600; z-index:1000; animation:slideIn 0.3s ease;
  box-shadow:var(--shadow-lg);
}
@keyframes slideIn { from{opacity:0;transform:translateX(20px);} to{opacity:1;transform:translateX(0);} }
.toast.success { background:var(--success); }
.toast.error { background:var(--danger); }
.toast.info { background:var(--accent); }

/* Conversation list */
.conv-item {
  display:flex; align-items:center; justify-content:space-between;
  padding:6px 8px; margin:2px 0; border-radius:6px;
  font-size:12px; color:rgba(255,255,255,0.7); cursor:pointer;
  transition:background 0.15s;
}
.conv-item:hover { background:rgba(255,255,255,0.08); }
.conv-item.active { background:rgba(255,255,255,0.12); color:#fff; }
.conv-item .conv-title {
  flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.conv-item .conv-actions { display:none; flex-gap:4px; }
.conv-item:hover .conv-actions { display:flex; gap:2px; }
.conv-item .conv-actions button {
  background:none; border:none; cursor:pointer; font-size:10px; padding:2px 4px;
}

/* Loading dots */
.typing-indicator { display:flex; gap:4px; padding:4px 0; }
.typing-indicator span {
  width:7px; height:7px; border-radius:50%; background:var(--accent);
  animation:bounce 1.2s infinite ease;
}
.typing-indicator span:nth-child(2) { animation-delay:0.15s; }
.typing-indicator span:nth-child(3) { animation-delay:0.3s; }
@keyframes bounce { 0%,60%,100%{transform:translateY(0);opacity:0.4;} 30%{transform:translateY(-6px);opacity:1;} }

/* 悬浮知识问答球 + 独立会话面板 */
.kb-fab {
  position:fixed; top:76px; right:18px; z-index:900;
  width:46px; height:46px; border-radius:50%; border:none; cursor:pointer;
  background:linear-gradient(135deg,#C8102E,#7A0F1B);
  color:#fff; font-size:19px; display:flex; align-items:center; justify-content:center;
  box-shadow:0 6px 18px rgba(200,16,46,0.32); transition:transform .18s, box-shadow .18s;
}
.kb-fab:hover { transform:scale(1.08); box-shadow:0 8px 24px rgba(200,16,46,0.45); }
.kb-fab.hidden { display:none; }
.kb-fab-panel {
  position:fixed; top:132px; right:18px; z-index:899;
  width:382px; max-height:calc(100vh - 160px); display:flex; flex-direction:column;
  background:#fff; border:1px solid var(--border); border-radius:16px;
  box-shadow:var(--shadow-lg); overflow:hidden; animation:fadeIn .18s ease;
}
.kb-fab-panel.hidden { display:none; }
.kb-fab-head {
  display:flex; align-items:center; gap:8px; padding:11px 14px;
  background:linear-gradient(135deg,#C8102E 0%,#7A0F1B 100%);
  color:#fff; font-weight:700; font-size:13px;
}
.kb-fab-head .spacer { flex:1; }
.kb-fab-head button {
  background:rgba(255,255,255,0.16); border:none; color:#fff; border-radius:6px;
  width:24px; height:24px; cursor:pointer; font-size:12px; line-height:1;
}
.kb-fab-head button:hover { background:rgba(255,255,255,0.32); }
.kb-fab-body { padding:12px 14px; display:flex; flex-direction:column; gap:8px; overflow:auto; }
.kb-fab-body input {
  padding:8px 12px; border:1px solid var(--border); border-radius:8px;
  font-size:12px; font-family:var(--font); outline:none; color:var(--text);
}
.kb-fab-body input:focus { border-color:var(--accent); }
.kb-fab-body .btn { width:100%; }
.kb-fab-results { font-size:12px; line-height:1.6; overflow:auto; }
.kb-fab-results .kbf-item { padding:8px 10px; border:1px solid var(--border); border-radius:8px; margin-bottom:8px; background:#FBF7F7; }
.kb-fab-results .kbf-title { font-weight:700; color:var(--accent); margin-bottom:2px; }
.kb-fab-results .kbf-src { font-size:10px; color:var(--text-secondary); word-break:break-all; }
.kb-fab-results .kbf-text { margin-top:4px; color:var(--text); }
.kb-fab-results .kbf-empty { color:var(--text-secondary); text-align:center; padding:16px 0; }

/* Responsive toggle */
.sidebar-toggle {
  display:none; position:fixed; top:12px; left:12px; z-index:100;
  width:36px; height:36px; border-radius:50%; background:var(--sidebar-bg); color:#fff;
  border:none; font-size:18px; cursor:pointer; align-items:center; justify-content:center;
}
@media (max-width:768px) {
  .sidebar { position:fixed; left:-280px; top:0; bottom:0; z-index:50; transition:left 0.3s; }
  .sidebar.open { left:0; }
  .sidebar-toggle { display:flex; }
  .message { max-width:95%; }
}

/* 步骤编辑弹窗 */
.modal-overlay {
  position:fixed; inset:0; z-index:1200; background:rgba(20,6,8,0.45);
  display:flex; align-items:center; justify-content:center; animation:fadeIn .15s ease;
}
.modal {
  width:420px; max-width:calc(100vw - 40px); background:#fff; border-radius:14px;
  box-shadow:var(--shadow-lg); overflow:hidden;
}
.modal-head {
  display:flex; align-items:center; gap:8px; padding:12px 16px; font-weight:700; font-size:13px;
  background:linear-gradient(135deg,#C8102E 0%,#7A0F1B 100%); color:#fff;
}
.modal-head .spacer { flex:1; }
.modal-head button { background:rgba(255,255,255,0.16); border:none; color:#fff; border-radius:6px; width:24px; height:24px; cursor:pointer; font-size:12px; }
.modal-head button:hover { background:rgba(255,255,255,0.32); }
.modal-body { padding:14px 16px; display:flex; flex-direction:column; gap:10px; max-height:60vh; overflow:auto; }
.modal-body label { font-size:11px; font-weight:600; color:var(--text-secondary); margin-bottom:-6px; }
.modal-body input, .modal-body select {
  width:100%; padding:8px 10px; border:1px solid var(--border); border-radius:8px;
  font-size:12px; font-family:var(--font); outline:none; color:var(--text); background:#fff;
}
.modal-body input:focus, .modal-body select:focus { border-color:var(--accent); }
.modal-foot { padding:12px 16px; border-top:1px solid var(--border); display:flex; justify-content:flex-end; gap:8px; }
.modal-foot .btn { width:auto; }

/* 保存路径条 */
.seq-saved {
  display:flex; align-items:center; gap:8px; margin-top:10px; flex-wrap:wrap;
  padding:8px 12px; background:#EAF7F0; border:1px solid #A9DFC4; border-radius:8px;
  font-size:12px; color:#0B7A45;
}
.seq-saved code { word-break:break-all; color:#085c34; font-family:Consolas,monospace; font-size:11px; }
.seq-saved .btn { width:auto; margin-left:auto; }
</style>
</head>
<body>

<!-- Sidebar -->
<div class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <div class="brand">
      <div class="brand-logo">CRRC</div>
      <div class="brand-text">
        <div class="brand-title">WT Agent</div>
        <div class="brand-sub">中国中车 · 智能流程构建平台</div>
      </div>
    </div>
  </div>
  <div class="sidebar-body">
    <div class="config-section">
      <label>LLM Base URL</label>
      <input type="text" id="cfg-base-url" placeholder="https://api.openai.com/v1">
    </div>
    <div class="config-section">
      <label>API Key</label>
      <div class="api-key-wrap">
        <input type="password" id="cfg-api-key" placeholder="sk-...">
        <button class="toggle" onclick="toggleApiKey()" title="显示/隐藏">👁</button>
      </div>
    </div>
    <div class="config-section">
      <label>模型名称</label>
      <input type="text" id="cfg-model" placeholder="gpt-4o">
    </div>
    <div class="config-section">
      <label>项目描述</label>
      <input type="text" id="cfg-project-desc" placeholder="Meteodyn WT 风资源仿真软件自动化">
    </div>
    <div class="config-section">
      <label>控件库文件路径</label>
      <input type="text" id="cfg-control-file" placeholder="flow_definition.json（可选）">
    </div>
    <!-- 高级选项 -->
    <div style="margin-bottom:12px;">
      <div class="collapsible-header" onclick="toggleAdvanced()" id="adv-header">
        <span>⚙ 高级选项</span>
        <span class="arrow">▶</span>
      </div>
      <div class="collapsible-body" id="adv-body">
        <div class="advanced-row">
          <div class="half">
            <label>超时（秒）</label>
            <input type="number" id="cfg-timeout" value="120" min="10" max="600">
          </div>
          <div class="half">
            <label>重试次数</label>
            <input type="number" id="cfg-retries" value="3" min="0" max="10">
          </div>
        </div>
        <div class="advanced-row">
          <div class="half">
            <label>退避因子</label>
            <input type="number" id="cfg-backoff" value="2.0" min="0.5" max="10" step="0.5">
          </div>
          <div class="half">
            <label>重试状态码</label>
            <input type="text" id="cfg-retry-codes" value="429,500,502,503,504">
          </div>
        </div>
      </div>
    </div>

    <div style="margin-bottom:8px;">
      <span class="status-dot disconnected" id="status-dot"></span>
      <span style="font-size:11px;" id="status-text">未连接</span>
    </div>
    <button class="btn btn-primary" onclick="testConnection()">🔗 测试连接</button>
    <div class="btn-row">
      <button class="btn btn-secondary btn-sm" onclick="saveConfig()">💾 保存配置</button>
      <button class="btn btn-secondary btn-sm" onclick="loadConfig()">📂 加载配置</button>
    </div>
    <div style="margin-top:12px; display:flex; gap:6px;">
      <button class="btn btn-secondary btn-sm" onclick="listSchemas()">📋 Actions</button>
      <button class="btn btn-secondary btn-sm" onclick="listSkills()">🔧 Skills</button>
    </div>

    <!-- 模型配置档案 -->
    <div style="margin-top:14px; border-top:1px solid rgba(255,255,255,0.06); padding-top:12px;">
      <div class="collapsible-header" onclick="toggleProfiles()" id="prof-header">
        <span>🧩 模型配置档案</span><span class="arrow">▶</span>
      </div>
      <div class="collapsible-body" id="prof-body">
        <div class="config-section">
          <label>已保存档案</label>
          <select id="profile-select" onchange="loadProfile()" style="width:100%; padding:8px; border-radius:6px; background:var(--input-bg); color:var(--input-text); border:1px solid var(--input-border);">
            <option value="">— 选择档案 —</option>
          </select>
        </div>
        <div class="config-section">
          <label>档案名称</label>
          <input type="text" id="profile-name" placeholder="例如：OpenAI / 本地Ollama / 火山方舟">
        </div>
        <div class="btn-row">
          <button class="btn btn-secondary btn-sm" onclick="saveProfile()">💾 存为档案</button>
          <button class="btn btn-secondary btn-sm" onclick="deleteProfile()">🗑 删除</button>
        </div>
        <button class="btn btn-secondary btn-sm" style="width:100%; margin-top:6px;" onclick="setDefaultProfile()">⭐ 设为默认</button>
      </div>
    </div>

    <!-- 智能增强 -->
    <div style="margin-top:14px; border-top:1px solid rgba(255,255,255,0.06); padding-top:12px;">
      <div class="collapsible-header" onclick="toggleEnhance()" id="enh-header">
        <span>🧠 智能增强</span><span class="arrow">▶</span>
      </div>
      <div class="collapsible-body" id="enh-body">
        <label style="display:flex; align-items:center; gap:8px; font-size:12px; color:var(--sidebar-text); margin-bottom:8px; cursor:pointer;">
          <input type="checkbox" id="opt-kb" checked onchange="onKbToggle()"> 启用项目知识库问答
        </label>
        <label style="display:flex; align-items:center; gap:8px; font-size:12px; color:var(--sidebar-text); margin-bottom:8px; cursor:pointer;">
          <input type="checkbox" id="opt-compress" checked> 启用长对话记忆压缩
        </label>
        <label style="display:flex; align-items:center; gap:8px; font-size:12px; color:var(--sidebar-text); margin-bottom:8px; cursor:pointer;">
          <input type="checkbox" id="opt-kb-fab" checked onchange="onKbFabToggle()"> 启用右上角悬浮知识问答
        </label>
        <button class="btn btn-secondary btn-sm" style="width:100%;" onclick="buildKb()">📚 构建/刷新知识库索引</button>
        <div id="kb-status" style="font-size:11px; color:var(--text-secondary); margin-top:6px;">未构建</div>
        <div id="kb-areas" style="font-size:11px; color:var(--text-secondary); margin-top:4px; line-height:1.7;"></div>
      </div>
    </div>

    <!-- 流程助手 -->
    <div style="margin-top:16px; border-top:1px solid rgba(255,255,255,0.06); padding-top:12px;">
      <div class="collapsible-header" onclick="toggleFlowTools()" id="ft-header">
        <span>🛠 流程助手</span><span class="arrow">▶</span>
      </div>
      <div class="collapsible-body" id="ft-body">
        <div id="ft-stats" style="font-size:11px; color:var(--text-secondary); margin-bottom:8px;">控件库加载中…</div>
        <div class="config-section">
          <label>控件语义检索</label>
          <input type="text" id="ft-query" placeholder="如：风机类型下拉框">
          <button class="btn btn-secondary btn-sm" style="width:100%; margin-top:6px;" onclick="flowSearch()">🔎 检索控件</button>
        </div>
        <div class="config-section">
          <label>流程文件 / 报告路径</label>
          <input type="text" id="ft-flow" placeholder="flow_definition.json 路径">
          <input type="text" id="ft-flow-b" placeholder="比对用第二份流程（可选）" style="margin-top:6px;">
        </div>
        <div class="config-section">
          <label>指令 / 问题 / 日志内容</label>
          <textarea id="ft-instr" placeholder="解释这个流程；把第3步加超时；或粘贴运行日志/报告路径"></textarea>
        </div>
        <div class="btn-row">
          <button class="btn btn-secondary btn-sm" onclick="flowExplain()">💡 解释</button>
          <button class="btn btn-secondary btn-sm" onclick="flowEdit()">✏️ 编辑</button>
        </div>
        <div class="btn-row">
          <button class="btn btn-secondary btn-sm" onclick="flowDiff()">🔀 比对</button>
          <button class="btn btn-secondary btn-sm" onclick="logDiagnose()">🩺 日志诊断</button>
        </div>
        <button class="btn btn-secondary btn-sm" style="width:100%; margin-top:6px; background:#8B5CF6; color:#fff;" onclick="auditFlow()" title="确定性规则 + 模型语义审核，检查动作/控件/参数并给出纠错建议">🧹 流程检查纠错</button>
        <button class="btn btn-secondary btn-sm" style="width:100%; margin-top:6px; background:#F59E0B; color:#fff;" onclick="repairFlow()" title="备份后自动修复确定性问题，展示模型语义建议供逐条确认">🔧 一键修复</button>
      </div>
    </div>

    <!-- 项目资产总览 -->
    <div style="margin-top:16px; border-top:1px solid rgba(255,255,255,0.06); padding-top:12px;">
      <div class="collapsible-header" onclick="toggleOverview()" id="ov-header">
        <span>📊 项目资产总览</span><span class="arrow">▶</span>
      </div>
      <div class="collapsible-body" id="ov-body">
        <div id="ov-stats" style="font-size:11px; color:var(--text-secondary); line-height:1.8;">加载中…</div>
      </div>
    </div>

    <!-- 历史会话 -->
    <div style="margin-top:16px; border-top:1px solid rgba(255,255,255,0.06); padding-top:12px;">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
        <span style="font-size:11px; font-weight:600; color:var(--sidebar-text); opacity:0.6; text-transform:uppercase;">历史会话</span>
        <button class="btn btn-secondary btn-sm" onclick="createConversation()" title="新建会话" style="padding:4px 8px;">➕</button>
      </div>
      <div id="conversation-list" style="max-height:200px; overflow-y:auto;"></div>
    </div>
  </div>
</div>

<button class="sidebar-toggle" id="sidebar-toggle" onclick="toggleSidebar()">☰</button>

<!-- Main -->
<div class="main">
  <div class="chat-header">
    <div style="display:flex; align-items:center; gap:12px; flex:1; min-width:0;">
      <div class="brand brand-dark">
        <div class="brand-logo-sm">CRRC</div>
        <div class="brand-text">
          <div class="brand-title">WT 智能流程构建助手</div>
          <div class="brand-sub">中国中车 · 自然语言 RPA 流程编排</div>
        </div>
      </div>
      <div id="current-conversation-info" style="font-size:11px; color:var(--text-secondary); padding:3px 10px; background:var(--chat-bg); border:1px solid var(--border); border-radius:999px; display:none;">
        <span id="current-conv-title">新会话</span>
      </div>
    </div>
    <div class="actions">
      <div class="conn-pill" id="conn-pill"><span class="dot"></span><span id="conn-pill-text">未连接</span></div>
      <div class="mode-toggle">
        <button class="mode-btn active" data-mode="chat" onclick="setMode('chat')">💬 对话</button>
        <button class="mode-btn" data-mode="step" onclick="setMode('step')">📝 单步转换</button>
        <button class="mode-btn" data-mode="sequence" onclick="setMode('sequence')">📋 序列转换</button>
      </div>
      <button class="btn btn-secondary btn-sm" onclick="clearChat()">🗑 清空</button>
    </div>
  </div>
  <div class="messages" id="messages">
    <div class="empty-state" id="empty-state">
      <div class="icon">🤖</div>
      <p>你好，我是 WT 智能流程构建助手</p>
      <div class="hint">
        配置好 LLM 连接后，你可以用自然语言描述要执行的 UI 操作，<br>
        或点击下方快捷指令快速开始 —— 支持单步 / 序列转换与控件库精准匹配
      </div>
      <div class="quick-chips">
        <button class="quick-chip" data-t="请新建一个风机类型" onclick="fillQuick(this.dataset.t)">新建风机类型</button>
        <button class="quick-chip" data-t="导入一份地形图文件" onclick="fillQuick(this.dataset.t)">导入地形图</button>
        <button class="quick-chip" data-t="录入测风塔数据" onclick="fillQuick(this.dataset.t)">录入测风塔数据</button>
        <button class="quick-chip" data-t="设置风机类型下拉框并确认保存" onclick="fillQuick(this.dataset.t)">风机类型下拉框</button>
      </div>
    </div>
  </div>
  <div class="input-area">
    <div class="mode-banner" id="mode-banner" style="display:none;"></div>
    <textarea id="user-input" rows="1" placeholder="输入你的 RPA 操作指令..."
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage();}"></textarea>
    <button class="send-btn" id="send-btn" onclick="sendMessage()" title="发送">▶</button>
  </div>
</div>

<!-- 右上角悬浮知识问答（独立会话，不影响主对话） -->
<button class="kb-fab" id="kb-fab" onclick="toggleKbFabPanel()" title="本地知识问答（不消耗 Token）">📖</button>
<div class="kb-fab-panel hidden" id="kb-fab-panel">
  <div class="kb-fab-head">
    <span>📖 本地知识问答</span>
    <span class="spacer"></span>
    <button onclick="toggleKbFabPanel()" title="收起面板">−</button>
    <button onclick="closeKbFabPanel()" title="关闭面板">✕</button>
  </div>
  <div class="kb-fab-body">
    <input type="text" id="kb-fab-query" placeholder="问 repowiki / docs，如：控件库如何采集？"
      onkeydown="if(event.key==='Enter'){kbAskFloating();}">
    <button class="btn btn-primary" onclick="kbAskFloating()">🔎 检索知识片段</button>
    <div class="kb-fab-results" id="kb-fab-results">
      <div class="kbf-empty">输入问题检索 repowiki / docs 等知识库，不消耗 Token。</div>
    </div>
  </div>
</div>

<script>
// ── State ──
let currentMode = 'chat';
let messagesContainer = document.getElementById('messages');
let emptyState = document.getElementById('empty-state');
let isLoading = false;
let _currentConversationId = null;  // 当前会话 ID
let _editingConvId = null;  // 正在编辑标题的会话 ID
let ACTION_SCHEMAS = {};   // action 名 → schema（含 input_key），用于步骤编辑弹窗
let ACTION_NAMES = [];     // 动作名列表

// ── Sidebar ──
function toggleSidebar() {
  document.getElementById('sidebar').classList.toggle('open');
}
function toggleAdvanced() {
  let header = document.getElementById('adv-header');
  let body = document.getElementById('adv-body');
  header.classList.toggle('open');
  body.classList.toggle('open');
}
function toggleApiKey() {
  let el = document.getElementById('cfg-api-key');
  el.type = el.type === 'password' ? 'text' : 'password';
}

// ── Mode ──
function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.remove('active'));
  document.querySelector(`[data-mode="${mode}"]`).classList.add('active');
  updateModeBanner();
}

// 按当前模式更新输入区提示条与占位符，避免误以为还在普通对话
function updateModeBanner() {
  let banner = document.getElementById('mode-banner');
  let input = document.getElementById('user-input');
  if (!banner || !input) return;
  let map = {
    chat:      {banner: '', placeholder: '输入你的 RPA 操作指令...'},
    step:      {banner: '📝 单步转换：输入将转换为 1 个动作步骤，生成后可直接编辑 / 重新生成 / 保存', placeholder: '描述一步操作，如：点击风机类型下拉框'},
    sequence:  {banner: '📋 序列转换：输入将转换为步骤序列，可拖拽排序、逐行编辑、重新生成并保存为流程文件', placeholder: '描述一个流程，如：新建风机类型并导入地形图'},
  };
  let m = map[currentMode] || map.chat;
  if (m.banner) { banner.textContent = m.banner; banner.style.display = 'block'; }
  else { banner.style.display = 'none'; }
  input.placeholder = m.placeholder;
}

// ── Messages ──
function addMessage(role, content, extra) {
  if (emptyState) { emptyState.remove(); emptyState = null; }
  let div = document.createElement('div');
  div.className = 'message ' + role;

  let avatarIcon = role === 'user' ? '👤' : '🤖';
  div.innerHTML = `<div class="avatar">${avatarIcon}</div><div class="bubble"></div>`;

  let bubble = div.querySelector('.bubble');

  if (typeof content === 'string') {
    // Render markdown-like text
    let html = escapeHtml(content)
      .replace(/```(\w*)\n?([\s\S]*?)```/g, (m, lang, code) =>
        '<pre><code>' + escapeHtml(code.trim()) + '</code><button class="copy-btn" onclick="copyCode(this)">复制</button></pre>')
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br>');
    bubble.innerHTML = html;
  } else {
    bubble.innerHTML = content;
  }

  if (extra) {
    let meta = document.createElement('div');
    meta.className = 'meta';
    meta.innerHTML = extra;
    bubble.appendChild(meta);
  }

  messagesContainer.appendChild(div);
  scrollToBottom();
  return div;
}

function addStepsMessage(steps, mode) {
  if (emptyState) { emptyState.remove(); emptyState = null; }
  let div = document.createElement('div');
  div.className = 'message agent';
  div.innerHTML = '<div class="avatar">🤖</div>';

  // 序列模式（且多于 1 步）启用拖拽排序；单步模式同样使用可展开 / 可复制的步骤行
  let drag = (mode === 'sequence' && steps.length > 1);
  div.appendChild(buildSeqBlock(steps, drag));

  div.__steps = steps;
  div.__mode = mode;
  messagesContainer.appendChild(div);
  scrollToBottom();
  return div;
}

// ── 步骤清单：可展开 / 可复制；序列模式额外支持拖拽与方向键排序 ──
function buildSeqBlock(steps, drag) {
  steps.forEach((s, i) => { if (!s.__seqKey) s.__seqKey = 'sk_' + Date.now() + '_' + i; });
  let bubble = document.createElement('div');
  bubble.className = 'bubble';

  let header = document.createElement('div');
  header.className = 'steps-summary';
  header.innerHTML = '<span class="badge">' + steps.length + ' 步</span>' +
    (drag ? '已生成步骤序列 · 拖拽 ⠿ 或聚焦后方向键排序，点击 ▶ 展开'
          : '已生成步骤' + (steps.length > 1 ? '序列' : '') + ' · 点击 ▶ 展开');
  bubble.appendChild(header);

  let list = document.createElement('div');
  list.className = 'seq-steps';
  steps.forEach((s, i) => list.appendChild(buildSeqRow(s, i, drag)));
  bubble.appendChild(list);

  let footer = document.createElement('div');
  footer.className = 'seq-footer';
  let copyBtn = document.createElement('button');
  copyBtn.className = 'btn btn-secondary btn-sm';
  copyBtn.textContent = drag ? '📋 复制排序后 JSON' : '📋 复制 JSON';
  copyBtn.onclick = function () { copySeqJson(this); };
  footer.appendChild(copyBtn);
  let exportBtn = document.createElement('button');
  exportBtn.className = 'btn btn-primary btn-sm';
  exportBtn.textContent = '📤 导出 flow_definition';
  exportBtn.title = '按执行器格式 {"steps":[...]} 复制到剪贴板';
  exportBtn.onclick = function () { exportFlowDef(this); };
  footer.appendChild(exportBtn);
  let saveBtn = document.createElement('button');
  saveBtn.className = 'btn btn-secondary btn-sm';
  saveBtn.textContent = '💾 保存为流程文件';
  saveBtn.title = '组装完整 flow_definition.json 保存到 workspace（执行器同源校验）';
  saveBtn.onclick = function () { saveFlowDef(this); };
  footer.appendChild(saveBtn);
  bubble.appendChild(footer);

  return bubble;
}

function buildSeqRow(s, i, drag) {
  let ac = s.actionConfig || {};
  let row = document.createElement('div');
  row.className = 'seq-step';
  row.dataset.key = s.__seqKey;
  if (drag) {
    row.draggable = true;
    row.tabIndex = 0;
    row.setAttribute('aria-label', '步骤 ' + (i + 1) + '，使用上下方向键排序');
  }

  let handle = drag ? '<span class="drag-handle" title="拖拽排序（或聚焦后按方向键）">⠿</span>' : '';
  row.innerHTML =
    handle +
    '<button class="seq-toggle" onclick="toggleSeqStep(this)">' + (s.__expanded ? '▼' : '▶') + '</button>' +
    '<span class="seq-num">' + (i + 1) + '</span>' +
    '<span class="seq-action">' + escapeHtml(ac.action || '?') + '</span>' +
    '<span class="seq-name">' + escapeHtml(s.name || ('Step ' + (i + 1))) + '</span>' +
    '<button class="seq-copy" onclick="editSeqStep(this)">编辑</button>' +
    '<button class="seq-copy" onclick="regenSeqStep(this)" title="让模型按此步骤意图重新生成">🔄</button>' +
    '<button class="seq-copy" onclick="copySeqStep(this)">复制</button>' +
    '<pre class="seq-detail"' + (s.__expanded ? '' : ' style="display:none"') + '>' +
      escapeHtml(JSON.stringify(s, null, 2)) + '</pre>';

  if (drag) {
    row.addEventListener('dragstart', seqDragStart);
    row.addEventListener('dragover', seqDragOver);
    row.addEventListener('dragleave', seqDragLeave);
    row.addEventListener('drop', seqDrop);
    row.addEventListener('dragend', seqDragEnd);
    row.addEventListener('keydown', seqKeyNav);
  }
  return row;
}

function toggleSeqStep(btn) {
  let row = btn.closest('.seq-step');
  let detail = row.querySelector('.seq-detail');
  let expanded = detail.style.display === 'none';
  detail.style.display = expanded ? 'block' : 'none';
  btn.textContent = expanded ? '▼' : '▶';
  row.classList.toggle('expanded', expanded);
  let msgEl = row.closest('.message');
  let step = msgEl.__steps.find(s => s.__seqKey === row.dataset.key);
  if (step) step.__expanded = expanded;
}

function copySeqStep(btn) {
  let row = btn.closest('.seq-step');
  let msgEl = row.closest('.message');
  let step = msgEl.__steps.find(s => s.__seqKey === row.dataset.key);
  if (!step) return;
  let clean = _cleanStep(step);
  navigator.clipboard.writeText(JSON.stringify(clean, null, 2)).then(() => {
    let old = btn.textContent; btn.textContent = '已复制'; setTimeout(() => btn.textContent = old, 1200);
  });
}

// ── 步骤行内编辑：打开弹窗修改名称 / 动作 / control_id / 输入参数 ──
function editSeqStep(btn) {
  let row = btn.closest('.seq-step');
  let msgEl = row.closest('.message');
  let step = msgEl.__steps.find(s => s.__seqKey === row.dataset.key);
  if (!step) return;
  let ac = step.actionConfig = step.actionConfig || {};
  let inputKey = (ACTION_SCHEMAS[ac.action] || {}).input_key || 'text';
  let actionOpts = ACTION_NAMES.length
    ? ACTION_NAMES.map(n => '<option value="' + n + '"' + (n === ac.action ? ' selected' : '') + '>' + n + '</option>').join('')
    : '<option value="' + escapeHtml(ac.action || 'click') + '">' + escapeHtml(ac.action || 'click') + '</option>';

  let overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML =
    '<div class="modal">' +
      '<div class="modal-head"><span>✏️ 编辑步骤</span><span class="spacer"></span>' +
        '<button onclick="this.closest(\'.modal-overlay\').remove()">✕</button></div>' +
      '<div class="modal-body">' +
        '<label>步骤名称</label>' +
        '<input id="med-name" value="' + escapeHtml(step.name || '') + '">' +
        '<label>动作</label>' +
        '<select id="med-action">' + actionOpts + '</select>' +
        '<label>目标控件 control_id</label>' +
        '<input id="med-control" value="' + escapeHtml(ac.controlId || '') + '" placeholder="留空=相对区域/无控件动作">' +
        '<label>输入 / 参数</label>' +
        '<input id="med-input" value="' + escapeHtml(String(ac[inputKey] != null ? ac[inputKey] : ac.text || '')) + '" placeholder="按所选动作的输入字段填写">' +
      '</div>' +
      '<div class="modal-foot">' +
        '<button class="btn btn-secondary btn-sm" onclick="this.closest(\'.modal-overlay\').remove()">取消</button>' +
        '<button class="btn btn-primary btn-sm" onclick="saveSeqStepEdit(this)">保存</button>' +
      '</div>' +
    '</div>';
  overlay.__msgEl = msgEl;
  overlay.__seqKey = step.__seqKey;
  document.body.appendChild(overlay);
  document.getElementById('med-name').focus();
}

function saveSeqStepEdit(btn) {
  let overlay = btn.closest('.modal-overlay');
  let msgEl = overlay.__msgEl;
  let step = msgEl.__steps.find(s => s.__seqKey === overlay.__seqKey);
  if (!step) return;
  let ac = step.actionConfig = step.actionConfig || {};
  let name = document.getElementById('med-name').value.trim();
  let action = document.getElementById('med-action').value.trim();
  let controlId = document.getElementById('med-control').value.trim();
  let inputVal = document.getElementById('med-input').value;
  if (!name) { showToast('步骤名称不能为空', 'error'); return; }

  step.name = name;
  ac.action = action || 'click';
  if (controlId) ac.controlId = controlId; else delete ac.controlId;
  // 输入值映射到新动作的 input_key（type_text→text、set_combobox→value、menu_select→menuPath…）
  let inputKey = (ACTION_SCHEMAS[action] || {}).input_key || 'text';
  if (inputVal !== '') ac[inputKey] = inputVal; else delete ac[inputKey];
  // 同步控件细分清单，保证 control_id 存在于 controls（执行器校验要求）
  let controls = Array.isArray(step.controls) ? step.controls : [];
  if (controlId) {
    if (controls.length && controls[0] && controls[0].id) {
      controls[0].id = controlId;
      controls[0].targetValue = controlId;
      if (controls[0].name && !step.name) controls[0].name = name;
    } else {
      step.controls = [{id: controlId, name: name, enabled: true, targetMethod: 'automation_id', targetValue: controlId}];
    }
  } else if (controls.length && controls[0] && !controls[0].id) {
    delete step.controls;
  }
  overlay.remove();
  refreshSeqMessage(msgEl);
  showToast('步骤已更新', 'success');
}

// 重建整条消息的步骤区块（编辑/重新生成后同步显示与 __steps 数据）
function refreshSeqMessage(msgEl) {
  let steps = msgEl.__steps;
  let bubble = msgEl.querySelector('.bubble');
  if (!bubble || !steps) return;
  let drag = (msgEl.__mode === 'sequence' && steps.length > 1);
  let fresh = buildSeqBlock(steps, drag);
  bubble.replaceWith(fresh);
}

// ── 单步重新生成：让模型按该步骤意图重做（修正动作/控件/参数） ──
async function regenSeqStep(btn) {
  let row = btn.closest('.seq-step');
  let msgEl = row.closest('.message');
  let step = msgEl.__steps.find(s => s.__seqKey === row.dataset.key);
  if (!step) return;
  let cfg = getConfig();
  if (!cfg.base_url || !cfg.api_key) { showToast('请先配置 LLM 连接', 'error'); return; }
  let ac = step.actionConfig || {};
  let inputKey = (ACTION_SCHEMAS[ac.action] || {}).input_key || 'text';
  let inputVal = ac[inputKey] != null ? ac[inputKey] : (ac.text || '');
  let desc = '重新生成以下自动化步骤（保持原意图，修正动作、控件与参数）：\n' +
    '步骤名称：' + (step.name || '') + '\n' +
    '动作：' + (ac.action || '') + '\n' +
    '目标控件：' + (ac.controlId || '') + '\n' +
    '输入参数：' + String(inputVal || '') + '\n' +
    (step.description ? '说明：' + step.description + '\n' : '');
  let old = btn.textContent; btn.textContent = '⏳'; btn.disabled = true;
  try {
    let result = await apiCall('/api/chat', {config: cfg, message: desc, mode: 'step'});
    if (result.type === 'steps' && result.steps && result.steps.length) {
      let idx = msgEl.__steps.indexOf(step);
      if (idx >= 0) {
        let fresh = result.steps[0];
        fresh.__seqKey = step.__seqKey;
        fresh.__expanded = step.__expanded;
        msgEl.__steps[idx] = fresh;
        refreshSeqMessage(msgEl);
        showToast('步骤已重新生成', 'success');
      }
    } else {
      showToast('重新生成失败：' + (result.warning || '模型未返回步骤'), 'error');
    }
  } catch (e) {
    showToast('重新生成出错：' + e, 'error');
  } finally {
    btn.textContent = old; btn.disabled = false;
  }
}

function copySeqJson(btn) {
  let msgEl = btn.closest('.message');
  let clean = msgEl.__steps.map(s => _cleanStep(s));
  navigator.clipboard.writeText(JSON.stringify(clean, null, 2)).then(() => {
    let old = btn.textContent; btn.textContent = '已复制 ✓'; setTimeout(() => btn.textContent = old, 1500);
  });
}

function _cleanStep(s) {
  let c = Object.assign({}, s);
  delete c.__seqKey; delete c.__expanded;
  return c;
}

// 按 DOM 顺序同步 steps 数组并重编号
function syncSeqOrder(list, msgEl) {
  let order = Array.from(list.querySelectorAll('.seq-step')).map(el => el.dataset.key);
  msgEl.__steps.sort((a, b) => order.indexOf(a.__seqKey) - order.indexOf(b.__seqKey));
  Array.from(list.querySelectorAll('.seq-step')).forEach((el, i) => {
    el.querySelector('.seq-num').textContent = (i + 1);
  });
}

// 拖拽排序
let _seqDragSrc = null;
function seqDragStart(e) {
  _seqDragSrc = e.currentTarget;
  e.currentTarget.classList.add('dragging');
  e.dataTransfer.effectAllowed = 'move';
  try { e.dataTransfer.setData('text/plain', e.currentTarget.dataset.key); } catch (err) {}
}
function seqDragOver(e) {
  e.preventDefault();
  if (!_seqDragSrc || e.currentTarget === _seqDragSrc) return;
  let rect = e.currentTarget.getBoundingClientRect();
  let after = (e.clientY - rect.top) > rect.height / 2;
  e.currentTarget.classList.toggle('drag-after', after);
  e.currentTarget.classList.toggle('drag-before', !after);
}
function seqDragLeave(e) {
  e.currentTarget.classList.remove('drag-after', 'drag-before');
}
function seqDrop(e) {
  e.preventDefault();
  let target = e.currentTarget;
  if (!_seqDragSrc || target === _seqDragSrc) { clearSeqDrag(); return; }
  let list = target.parentElement;
  let after = (e.clientY - target.getBoundingClientRect().top) > target.getBoundingClientRect().height / 2;
  if (after) list.insertBefore(_seqDragSrc, target.nextSibling);
  else list.insertBefore(_seqDragSrc, target);
  syncSeqOrder(list, target.closest('.message'));
  clearSeqDrag();
}
function seqDragEnd() { clearSeqDrag(); }
function clearSeqDrag() {
  if (_seqDragSrc) _seqDragSrc.classList.remove('dragging');
  document.querySelectorAll('.seq-step').forEach(el => el.classList.remove('drag-after', 'drag-before'));
  _seqDragSrc = null;
}

// 键盘可达性：聚焦步骤行后，方向键上下移动排序
function seqKeyNav(e) {
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
  e.preventDefault();
  let row = e.currentTarget;
  let list = row.parentElement;
  let rows = Array.from(list.querySelectorAll('.seq-step'));
  let idx = rows.indexOf(row);
  let targetIdx = e.key === 'ArrowUp' ? idx - 1 : idx + 1;
  if (targetIdx < 0 || targetIdx >= rows.length) return;
  let target = rows[targetIdx];
  if (e.key === 'ArrowUp') list.insertBefore(row, target);
  else list.insertBefore(row, target.nextSibling);
  syncSeqOrder(list, row.closest('.message'));
  row.focus();
}

function addLoadingMessage() {
  if (emptyState) { emptyState.remove(); emptyState = null; }
  let div = document.createElement('div');
  div.className = 'message agent';
  div.id = 'loading-msg';
  div.innerHTML = '<div class="avatar">🤖</div><div class="bubble"><div class="typing-indicator"><span></span><span></span><span></span></div></div>';
  messagesContainer.appendChild(div);
  scrollToBottom();
  return div;
}

function removeLoadingMessage() {
  let el = document.getElementById('loading-msg');
  if (el) el.remove();
}

function clearChat() {
  messagesContainer.innerHTML = '';
  emptyState = document.createElement('div');
  emptyState.className = 'empty-state';
  emptyState.id = 'empty-state';
  emptyState.innerHTML = emptyStateHTML();
  messagesContainer.appendChild(emptyState);
}

function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// 快捷指令：把示例填入输入框
function fillQuick(text) {
  let input = document.getElementById('user-input');
  input.value = text || '';
  input.focus();
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 150) + 'px';
}

// 按执行器格式导出：{"steps":[...]}（与 WT_AUT_recorded.py 可消费的结构一致）
function exportFlowDef(btn) {
  let msgEl = btn.closest('.message');
  let clean = msgEl.__steps.map(s => _cleanStep(s));
  let flow = {steps: clean};
  navigator.clipboard.writeText(JSON.stringify(flow, null, 2)).then(() => {
    let old = btn.textContent;
    btn.textContent = '已复制 flow_definition ✓';
    setTimeout(() => btn.textContent = old, 1800);
  });
}

// 保存为流程文件：后端组装完整 flow_definition 并用执行器同源规则校验后落盘
async function saveFlowDef(btn) {
  let msgEl = btn.closest('.message');
  let clean = msgEl.__steps.map(s => _cleanStep(s));
  let old = btn.textContent;
  btn.textContent = '保存中...';
  btn.disabled = true;
  try {
    let cfg = getConfig();
    let resp = await apiCall('/api/flow/save', {steps: clean, config: cfg});
    if (resp.status === 'error') {
      showToast('保存失败：' + (resp.message || '未知错误'), 'error');
    } else if (resp.status === 'warning') {
      let list = (resp.errors || []).join('\n');
      alert('已保存到：' + resp.path + '\n\n存在校验警告（请修正后重试）：\n' + list);
    } else {
      showSavedPath(msgEl, resp.path);
      showToast('已保存 ' + resp.step_count + ' 个步骤', 'success');
    }
  } catch (e) {
    showToast('保存失败：' + e, 'error');
  } finally {
    btn.textContent = old;
    btn.disabled = false;
  }
}

// 保存成功后展示路径条（含「打开目录」）
function showSavedPath(msgEl, path) {
  let old = msgEl.querySelector('.seq-saved');
  if (old) old.remove();
  let bar = document.createElement('div');
  bar.className = 'seq-saved';
  bar.innerHTML = '💾 已保存：<code>' + escapeHtml(path) + '</code>' +
    '<button class="btn btn-secondary btn-sm" onclick="openSavedDir(this)">打开目录</button>';
  bar.__path = path;
  let bubble = msgEl.querySelector('.bubble');
  if (bubble && bubble.nextSibling) msgEl.insertBefore(bar, bubble.nextSibling);
  else msgEl.appendChild(bar);
}

function openSavedDir(btn) {
  let bar = btn.closest('.seq-saved');
  let path = bar && bar.__path;
  if (!path) return;
  fetch('/api/flow/open-dir', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({path: path}),
  }).then(r => r.json()).then(d => {
    if (d.status === 'error') showToast('打开目录失败：' + d.message, 'error');
  }).catch(() => {});
}

// 空状态统一模板（含快捷指令）
function emptyStateHTML() {
  const chips = [
    ['请新建一个风机类型', '新建风机类型'],
    ['导入一份地形图文件', '导入地形图'],
    ['录入测风塔数据', '录入测风塔数据'],
    ['设置风机类型下拉框并确认保存', '风机类型下拉框'],
  ];
  let chipHtml = chips.map(c =>
    '<button class="quick-chip" data-t="' + c[0] + '" onclick="fillQuick(this.dataset.t)">' + c[1] + '</button>'
  ).join('');
  return '<div class="icon">🤖</div>' +
    '<p>聊天已清空</p>' +
    '<div class="hint">用自然语言描述要执行的 UI 操作，或点击快捷指令快速开始：</div>' +
    '<div class="quick-chips">' + chipHtml + '</div>';
}

// ── API calls ──
async function apiCall(url, body) {
  let resp = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(body),
  });
  return resp.json();
}

// 加载动作 schema 缓存（供步骤编辑弹窗使用）
async function loadSchemas() {
  try {
    let resp = await fetch('/api/schemas');
    let data = await resp.json();
    if (data && typeof data === 'object') {
      ACTION_SCHEMAS = data;
      ACTION_NAMES = Object.keys(data);
    }
  } catch (e) { /* 静默，编辑弹窗回退为文本输入 */ }
}

function getConfig() {
  return {
    base_url: document.getElementById('cfg-base-url').value.trim(),
    api_key: document.getElementById('cfg-api-key').value.trim(),
    model: document.getElementById('cfg-model').value.trim() || 'gpt-4o',
    project_desc: document.getElementById('cfg-project-desc').value.trim(),
    control_file: document.getElementById('cfg-control-file').value.trim(),
    timeout: parseInt(document.getElementById('cfg-timeout').value) || 120,
    max_retries: parseInt(document.getElementById('cfg-retries').value) || 3,
    retry_backoff: parseFloat(document.getElementById('cfg-backoff').value) || 2.0,
    retry_codes: document.getElementById('cfg-retry-codes').value.trim() || '429,500,502,503,504',
  };
}

async function sendMessage() {
  let input = document.getElementById('user-input');
  let text = input.value.trim();
  if (!text || isLoading) return;

  let cfg = getConfig();
  if (!cfg.base_url || !cfg.api_key) {
    showToast('请先在侧边栏配置 Base URL 和 API Key', 'error');
    return;
  }

  // 自动创建会话（如果还没有）
  if (!_currentConversationId) {
    let resp = await fetch('/api/conversations', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({title: text.slice(0, 30)})  // 用第一条消息作为标题
    });
    let data = await resp.json();
    _currentConversationId = data.id;
    await loadConversations();  // 刷新侧边栏
    updateConversationInfo(text.slice(0, 20) + '...');  // 显示会话信息
  }

  isLoading = true;
  document.getElementById('send-btn').disabled = true;
  input.value = '';
  input.style.height = 'auto';

  addMessage('user', text);
  addLoadingMessage();

  try {
    let body = { config: cfg, message: text, mode: currentMode };
    if (_currentConversationId) body.conversation_id = _currentConversationId;
    let enh = getEnhanceOptions();
    body.kb_enabled = enh.kb_enabled;
    body.compress = enh.compress;
    let result = await apiCall('/api/chat', body);
    removeLoadingMessage();

    if (result.error) {
      addMessage('agent', '❌ 错误: ' + escapeHtml(result.error));
      updateStatus(false);
    } else if (result.type === 'steps') {
      addStepsMessage(result.steps, result.mode);
      if (result.warning) {
        addMessage('agent', '⚠️ ' + escapeHtml(result.warning));
      }
      updateStatus(true);
    } else {
      addMessage('agent', result.reply || '(无响应)');
      updateStatus(true);
    }
  } catch (e) {
    removeLoadingMessage();
    addMessage('agent', '❌ 网络错误: ' + escapeHtml(e.message));
    updateStatus(false);
  }

  isLoading = false;
  document.getElementById('send-btn').disabled = false;
  input.focus();
}

async function testConnection() {
  let cfg = getConfig();
  if (!cfg.base_url || !cfg.api_key) {
    showToast('请先填写 Base URL 和 API Key', 'error');
    return;
  }
  let btn = event.target;
  btn.textContent = '⏳ 测试中...';
  btn.disabled = true;
  try {
    let resp = await fetch('/api/test-connection', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(cfg),
    });
    let data = await resp.json();
    updateStatus(data.ok);
    showToast(data.message, data.ok ? 'success' : 'error');
  } catch (e) {
    updateStatus(false);
    showToast('连接失败: ' + e.message, 'error');
  }
  btn.textContent = '🔗 测试连接';
  btn.disabled = false;
}

function updateStatus(connected) {
  let dot = document.getElementById('status-dot');
  let text = document.getElementById('status-text');
  dot.className = 'status-dot ' + (connected ? 'connected' : 'disconnected');
  text.textContent = connected ? '已连接' : '未连接';
  let pill = document.getElementById('conn-pill');
  let pillText = document.getElementById('conn-pill-text');
  if (pill) pill.classList.toggle('on', connected);
  if (pillText) pillText.textContent = connected ? '已连接' : '未连接';
}

async function saveConfig() {
  let cfg = getConfig();
  await fetch('/api/config', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(cfg),
  });
  showToast('配置已保存', 'success');
}

async function loadConfig() {
  let resp = await fetch('/api/config');
  let cfg = await resp.json();
  document.getElementById('cfg-base-url').value = cfg.base_url || '';
  document.getElementById('cfg-api-key').value = cfg.api_key || '';
  document.getElementById('cfg-model').value = cfg.model || 'gpt-4o';
  document.getElementById('cfg-project-desc').value = cfg.project_desc || '';
  document.getElementById('cfg-control-file').value = cfg.control_file || '';
  document.getElementById('cfg-timeout').value = cfg.timeout || 120;
  document.getElementById('cfg-retries').value = cfg.max_retries || 3;
  document.getElementById('cfg-backoff').value = cfg.retry_backoff || 2.0;
  document.getElementById('cfg-retry-codes').value = cfg.retry_codes || '429,500,502,503,504';
  showToast('配置已加载', 'info');
}

// ── 模型配置档案 ──
function toggleProfiles() {
  document.getElementById('prof-header').classList.toggle('open');
  document.getElementById('prof-body').classList.toggle('open');
}
function toggleEnhance() {
  document.getElementById('enh-header').classList.toggle('open');
  document.getElementById('enh-body').classList.toggle('open');
}

async function loadProfiles() {
  try {
    let resp = await fetch('/api/profiles');
    let data = await resp.json();
    let sel = document.getElementById('profile-select');
    sel.innerHTML = '<option value="">— 选择档案 —</option>';
    Object.keys(data.profiles || {}).forEach(name => {
      let opt = document.createElement('option');
      opt.value = name;
      opt.textContent = name + (data.default === name ? ' (默认)' : '');
      if (data.default === name) opt.selected = true;
      sel.appendChild(opt);
    });
  } catch (e) {}
}

async function loadProfile() {
  let name = document.getElementById('profile-select').value;
  if (!name) return;
  try {
    let resp = await fetch('/api/profiles');
    let data = await resp.json();
    let cfg = data.profiles[name];
    if (!cfg) return;
    document.getElementById('cfg-base-url').value = cfg.base_url || '';
    document.getElementById('cfg-api-key').value = cfg.api_key || '';
    document.getElementById('cfg-model').value = cfg.model || 'gpt-4o';
    document.getElementById('cfg-timeout').value = cfg.timeout || 120;
    document.getElementById('cfg-retries').value = cfg.max_retries || 3;
    document.getElementById('cfg-backoff').value = cfg.retry_backoff || 2.0;
    document.getElementById('cfg-retry-codes').value = cfg.retry_codes || '429,500,502,503,504';
    document.getElementById('profile-name').value = name;
    showToast('已加载档案: ' + name, 'info');
  } catch (e) {}
}

async function saveProfile() {
  let name = document.getElementById('profile-name').value.trim();
  if (!name) { showToast('请先填写档案名称', 'error'); return; }
  let cfg = getConfig();
  await fetch('/api/profiles', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, config: cfg}),
  });
  await loadProfiles();
  showToast('档案已保存: ' + name, 'success');
}

async function deleteProfile() {
  let name = document.getElementById('profile-select').value;
  if (!name) { showToast('请先选择一个档案', 'error'); return; }
  if (!confirm('确定删除档案「' + name + '」？')) return;
  await fetch('/api/profiles?name=' + encodeURIComponent(name), {method: 'DELETE'});
  document.getElementById('profile-name').value = '';
  await loadProfiles();
  showToast('档案已删除', 'info');
}

async function setDefaultProfile() {
  let name = document.getElementById('profile-select').value;
  if (!name) { showToast('请先选择一个档案', 'error'); return; }
  await fetch('/api/profiles', {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name}),
  });
  await loadProfiles();
  showToast('已设为默认: ' + name, 'success');
}

// ── 知识库 ──
async function buildKb() {
  let btn = event.target;
  let old = btn.textContent;
  btn.textContent = '⏳ 构建中...';
  btn.disabled = true;
  try {
    let resp = await fetch('/api/kb/build', {method: 'POST'});
    let data = await resp.json();
    document.getElementById('kb-status').textContent =
      '✅ 已索引 ' + (data.sources||0) + ' 个源 / ' + (data.chunks||0) + ' 个片段';
    loadKbStatus();
    loadOverview();
    showToast('知识库索引已构建', 'success');
  } catch (e) {
    showToast('构建失败: ' + e.message, 'error');
  }
  btn.textContent = old;
  btn.disabled = false;
}

function getEnhanceOptions() {
  return {
    kb_enabled: document.getElementById('opt-kb').checked,
    compress: document.getElementById('opt-compress').checked,
  };
}

async function loadKbStatus() {
  try {
    let resp = await fetch('/api/kb/status');
    let data = await resp.json();
    let statusEl = document.getElementById('kb-status');
    if (data.built) {
      statusEl.textContent = '✅ 已索引 ' + (data.sources||0) + ' 个源 / ' + (data.chunks||0) + ' 个片段';
    }
    let areasEl = document.getElementById('kb-areas');
    if (areasEl && data.areas) {
      areasEl.innerHTML = Object.entries(data.areas).map(([k, v]) =>
        '<span style="display:inline-block; background:rgba(255,255,255,0.08); padding:1px 8px; border-radius:8px; margin:2px 4px 0 0;">' +
        escapeHtml(k) + ' ' + v + '</span>'
      ).join('');
    }
  } catch (e) {}
}

// 悬浮知识问答（不消耗 LLM Token）：检索 repowiki/docs 等知识库片段，
// 结果渲染在独立悬浮面板内，不影响主对话区。
async function kbAskFloating() {
  let input = document.getElementById('kb-fab-query');
  let q = (input.value || '').trim();
  if (!q) { showToast('请输入知识问题', 'error'); return; }
  let box = document.getElementById('kb-fab-results');
  box.innerHTML = '<div class="kbf-empty">⏳ 检索中…</div>';
  try {
    let resp = await fetch('/api/kb/search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query: q, top_k: 3}),
    });
    let data = await resp.json();
    if (!data || !data.length) {
      box.innerHTML = '<div class="kbf-empty">未在知识库中检索到相关内容。可点击"📚 构建/刷新知识库索引"后再试。</div>';
      return;
    }
    box.innerHTML = data.map(h =>
      '<div class="kbf-item">' +
        '<div class="kbf-title">' + escapeHtml(h.title || '(无标题)') + '</div>' +
        '<div class="kbf-src">' + escapeHtml(h.source || '') + '</div>' +
        '<div class="kbf-text">' + escapeHtml((h.text || '').slice(0, 260)) + '</div>' +
      '</div>'
    ).join('');
    box.scrollTop = 0;
  } catch (e) {
    box.innerHTML = '<div class="kbf-empty">❌ ' + escapeHtml(e.message) + '</div>';
  }
}

// ── 悬浮球开关（偏好存 localStorage，默认启用）──
function isKbFabEnabled() {
  let v = localStorage.getItem('wt_kb_fab');
  return v === null ? true : v === '1';
}
function applyKbFab() {
  let on = isKbFabEnabled();
  document.getElementById('kb-fab').classList.toggle('hidden', !on);
  if (!on) closeKbFabPanel();
}
function onKbFabToggle() {
  localStorage.setItem('wt_kb_fab', document.getElementById('opt-kb-fab').checked ? '1' : '0');
  applyKbFab();
}
function toggleKbFabPanel() {
  let p = document.getElementById('kb-fab-panel');
  p.classList.toggle('hidden');
  if (!p.classList.contains('hidden')) document.getElementById('kb-fab-query').focus();
}
function closeKbFabPanel() {
  document.getElementById('kb-fab-panel').classList.add('hidden');
}
function initKbFab() {
  let on = isKbFabEnabled();
  document.getElementById('opt-kb-fab').checked = on;
  applyKbFab();
}

// 项目资产总览：控件库 / 流程包 / 知识库 / 技能
async function loadOverview() {
  try {
    let resp = await fetch('/api/overview');
    let d = await resp.json();
    let el = document.getElementById('ov-stats');
    if (!el) return;
    let rows = [
      ['🧩 控件库控件', d.controls || 0],
      ['📦 流程包', d.flows || 0],
      ['📚 知识库片段', (d.kb_chunks || 0) + ' / ' + (d.kb_sources || 0) + ' 源'],
      ['🔧 内置技能', d.skills || 0],
    ];
    el.innerHTML = rows.map(r =>
      '<div style="display:flex; justify-content:space-between; border-bottom:1px dashed rgba(255,255,255,0.08); padding:3px 0;">' +
      '<span>' + r[0] + '</span><span style="font-weight:700; color:#fff;">' + r[1] + '</span></div>'
    ).join('') +
    (d.kb_areas && Object.keys(d.kb_areas).length ? '\n<div style="margin-top:4px; opacity:0.85;">' +
      Object.entries(d.kb_areas).map(([k, v]) => k + ' ' + v).join(' · ') + '</div>' : '');
  } catch (e) {}
}

function toggleOverview() {
  document.getElementById('ov-header').classList.toggle('open');
  document.getElementById('ov-body').classList.toggle('open');
}

function onKbToggle() {}

function toggleFlowTools() {
  document.getElementById('ft-header').classList.toggle('open');
  document.getElementById('ft-body').classList.toggle('open');
}

async function flowSearch() {
  let q = document.getElementById('ft-query').value.trim();
  if (!q) { showToast('请输入控件描述', 'error'); return; }
  if (!getConfig().base_url) { showToast('请先配置 LLM 连接', 'error'); return; }
  addMessage('user', '🔎 检索控件：' + q);
  addLoadingMessage();
  try {
    let resp = await fetch('/api/control-search', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({query: q, top_k: 5}),
    });
    let data = await resp.json();
    removeLoadingMessage();
    if (data.status === 'error') { addMessage('agent', '❌ ' + data.message); return; }
    let text = '## 控件库检索结果（' + data.count + ' 个候选）\n\n';
    data.candidates.forEach((c, i) => {
      text += (i+1) + '. **' + (c.name || '(未命名)') + '**\n';
      text += '   - control_id: `' + c.targetValue + '`\n';
      text += '   - 类型: ' + (c.controlType || '?') + ' / 权威度: ' + (c.authority || 'N/A') + '\n';
      if (c.notes) text += '   - 备注: ' + c.notes + '\n';
    });
    text += '\n> 把上面的 control_id 填入 add_step 即可复用现有资产。';
    addMessage('agent', text);
  } catch (e) {
    removeLoadingMessage();
    addMessage('agent', '❌ ' + e.message);
  }
}

async function _flowPost(url, body) {
  let cfg = getConfig();
  if (!cfg.base_url) { showToast('请先配置 LLM 连接', 'error'); return null; }
  addLoadingMessage();
  try {
    let resp = await fetch(url, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(Object.assign({config: cfg}, body)),
    });
    let data = await resp.json();
    removeLoadingMessage();
    return data;
  } catch (e) {
    removeLoadingMessage();
    addMessage('agent', '❌ ' + e.message);
    return null;
  }
}

async function flowExplain() {
  let path = document.getElementById('ft-flow').value.trim();
  let instr = document.getElementById('ft-instr').value.trim();
  if (!path) { showToast('请填写流程文件路径', 'error'); return; }
  addMessage('user', '💡 解释流程：' + path + (instr ? '\n' + instr : ''));
  let data = await _flowPost('/api/flow/explain', {flow_path: path, question: instr});
  if (data) addMessage('agent', data.answer || '(无响应)');
}

async function flowEdit() {
  let path = document.getElementById('ft-flow').value.trim();
  let instr = document.getElementById('ft-instr').value.trim();
  if (!path) { showToast('请填写流程文件路径', 'error'); return; }
  if (!instr) { showToast('请填写修改指令', 'error'); return; }
  addMessage('user', '✏️ 编辑流程：' + instr);
  let data = await _flowPost('/api/flow/edit', {flow_path: path, instruction: instr, write_back: false});
  if (data) {
    if (data.ok) {
      addMessage('agent', '✅ 已生成修改后步骤（共 ' + data.steps.length + ' 步）。\n\n' + (data.raw || ''));
    } else {
      addMessage('agent', '⚠️ 模型未返回可解析的 JSON 步骤，原始回复：\n\n' + (data.raw || ''));
    }
  }
}

async function flowDiff() {
  let a = document.getElementById('ft-flow').value.trim();
  let b = document.getElementById('ft-flow-b').value.trim();
  if (!a || !b) { showToast('请填写两份流程文件路径', 'error'); return; }
  addMessage('user', '🔀 比对流程 A/B');
  let data = await _flowPost('/api/flow/diff', {flow_a: a, flow_b: b});
  if (data) addMessage('agent', data.answer || '(无响应)');
}

async function logDiagnose() {
  let flow = document.getElementById('ft-flow').value.trim();
  let instr = document.getElementById('ft-instr').value.trim();
  if (!instr) { showToast('请粘贴日志内容或报告路径', 'error'); return; }
  addMessage('user', '🩺 日志诊断');
  let data = await _flowPost('/api/log/diagnose', {log_input: instr, flow_path: flow});
  if (data) addMessage('agent', data.answer || '(无响应)');
}

// 流程链路检查审核纠错：确定性规则（动作/控件/类型匹配/参数）+ 模型语义审核
async function auditFlow() {
  let path = document.getElementById('ft-flow').value.trim();
  if (!path) { showToast('请输入流程文件路径', 'error'); return; }
  if (!getConfig().base_url) { showToast('语义审核需要配置 LLM 连接', 'error'); return; }
  addMessage('user', '🧹 流程检查纠错：' + path);
  addLoadingMessage();
  try {
    let cfg = getConfig();
    let resp = await fetch('/api/flow/audit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({flow_path: path, config: cfg}),
    });
    let data = await resp.json();
    removeLoadingMessage();
    if (data.status === 'error') { addMessage('agent', '❌ ' + data.message); return; }

    let issues = (data.rules && data.rules.issues) || [];
    let llmItems = data.llm || [];
    let lines = ['## 流程检查报告'];
    lines.push(data.summary || '');
    if (!issues.length && !llmItems.length) {
      lines.push('✅ 未发现问题');
    }
    issues.forEach((it, i) => {
      let mark = it.level === 'error' ? '❌' : '⚠️';
      lines.push((i + 1) + '. ' + mark + ' [' + it.category + '] 步骤' + it.step_index + ' ' + (it.step_name || '') + '：' + it.message);
      if (it.suggestion) lines.push('   建议：' + it.suggestion);
    });
    llmItems.forEach((it) => {
      lines.push('🔎 [模型语义] 步骤' + it.step_index + '：' + it.issue);
      if (it.suggestion) lines.push('   建议：' + it.suggestion);
    });
    addMessage('agent', lines.join('\n'));
  } catch (e) {
    removeLoadingMessage();
    addMessage('agent', '❌ 检查出错：' + escapeHtml(e.message));
  }
}

async function repairFlow() {
  let path = document.getElementById('ft-flow').value.trim();
  if (!path) { showToast('请输入流程文件路径', 'error'); return; }
  if (!getConfig().base_url) { showToast('未配置 LLM，将只执行确定性修复', 'error'); }
  addMessage('user', '🔧 一键修复：' + path);
  addLoadingMessage();
  try {
    let cfg = getConfig();
    let resp = await fetch('/api/flow/repair', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({flow_path: path, config: cfg}),
    });
    let data = await resp.json();
    removeLoadingMessage();
    if (data.status === 'error') { addMessage('agent', '❌ ' + data.message); return; }
    let lines = ['## 一键修复预览', data.summary || '', '自动修复：' + data.auto_fixed_count + ' 项 | 待确认：' + data.pending_confirm_count + ' 项'];
    (data.pending_confirm || []).forEach((it) => {
      lines.push('🔎 步骤' + it.step_index + ' ' + (it.step_name || '') + '：' + it.message);
      if (it.suggestion) lines.push('   建议：' + it.suggestion);
    });
    let suggestions = data.llm_suggestions || [];
    suggestions.forEach((it, i) => {
      lines.push('🔧 [模型建议 ' + (i + 1) + '] 步骤' + it.step_index + '：' + it.issue);
      if (it.suggestion) lines.push('   建议：' + it.suggestion);
    });
    let msg = addMessage('agent', lines.join('\n'));
    let bubble = msg.querySelector('.bubble');
    let applied = new Set(suggestions.map((_, i) => i));
    suggestions.forEach((it, i) => {
      let row = document.createElement('div');
      row.style.marginTop = '6px';
      let toggle = document.createElement('button');
      toggle.className = 'btn btn-sm';
      toggle.textContent = '✓ 应用' + (i + 1);
      toggle.onclick = () => {
        if (applied.has(i)) { applied.delete(i); toggle.textContent = '✗ 忽略' + (i + 1); }
        else { applied.add(i); toggle.textContent = '✓ 应用' + (i + 1); }
      };
      row.appendChild(toggle);
      bubble.appendChild(row);
    });
    let writeBtn = document.createElement('button');
    writeBtn.className = 'btn btn-primary btn-sm';
    writeBtn.textContent = suggestions.length ? '✅ 确认应用并写回' : '✅ 写回自动修复';
    writeBtn.style.marginTop = '8px';
    writeBtn.onclick = () => repairWrite(path, Array.from(applied));
    bubble.appendChild(writeBtn);
  } catch (e) {
    removeLoadingMessage();
    addMessage('agent', '❌ 修复失败：' + escapeHtml(e.message));
  }
}

async function repairWrite(path, indices) {
  addLoadingMessage();
  try {
    let cfg = getConfig();
    let resp = await fetch('/api/flow/repair', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({flow_path: path, config: cfg, apply: indices}),
    });
    let data = await resp.json();
    removeLoadingMessage();
    if (data.status === 'error') { addMessage('agent', '❌ ' + data.message); return; }
    let lines = ['## 修复写回完成', data.summary || ''];
    if (data.backup_path) lines.push('备份文件：' + data.backup_path);
    lines.push('已写回：' + path);
    addMessage('agent', lines.join('\n'));
  } catch (e) {
    removeLoadingMessage();
    addMessage('agent', '❌ 写回失败：' + escapeHtml(e.message));
  }
}

async function loadControlStats() {
  try {
    let resp = await fetch('/api/control-stats');
    let data = await resp.json();
    let el = document.getElementById('ft-stats');
    if (el) el.textContent = '控件库：' + (data.total || 0) + ' 个控件（含 targetValue ' + (data.with_target || 0) + '）';
  } catch (e) {}
}

async function listSchemas() {
  let resp = await fetch('/api/schemas');
  let schemas = await resp.json();
  let text = '## 可用 Action 列表\n\n';
  for (let [name, s] of Object.entries(schemas)) {
    text += '- **' + name + '**: ' + (s.label || '') + ' - ' + (s.description || '') + '\n';
  }
  addMessage('agent', text);
}

async function listSkills() {
  let resp = await fetch('/api/skills');
  let skills = await resp.json();
  let text = '## 可用 Skill 列表\n\n';
  skills.forEach(s => {
    text += '- **' + escapeHtml(s.name) + '**: ' + escapeHtml(s.description || '') + '\n';
  });
  addMessage('agent', text);
}

// ── Conversation Management ──
async function loadConversations() {
  let resp = await fetch('/api/conversations');
  let sessions = await resp.json();
  let list = document.getElementById('conversation-list');
  if (!list) return;
  list.innerHTML = '';

  if (!sessions.length) {
    list.innerHTML = '<div style="font-size:11px; color:rgba(255,255,255,0.3); text-align:center; padding:8px;">暂无会话</div>';
    return;
  }

  sessions.forEach(s => {
    let div = document.createElement('div');
    div.className = 'conv-item' + (s.id === _currentConversationId ? ' active' : '');
    div.dataset.id = s.id;
    div.innerHTML = `
      <span class="conv-title" onclick="loadConversation('${s.id}')">${escapeHtml(s.title)}</span>
      <span class="conv-actions">
        <button onclick="event.stopPropagation(); renameConversation('${s.id}')" title="重命名">✏️</button>
        <button onclick="event.stopPropagation(); deleteConversation('${s.id}')" title="删除">🗑️</button>
      </span>
    `;
    list.appendChild(div);
  });
}

async function createConversation() {
  let resp = await fetch('/api/conversations', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title: ''})
  });
  let data = await resp.json();
  _currentConversationId = data.id;
  messagesContainer.innerHTML = '';
  showEmptyState();
  addMessage('agent', '✅ 新会话已创建。你好！有什么我可以帮你的吗？');
  loadConversations();
}

async function loadConversation(sessionId) {
  // 如果有未保存的消息，先提示
  let resp = await fetch('/api/conversations/' + sessionId);
  let conv = await resp.json();
  if (!conv.id) return;

  _currentConversationId = sessionId;

  // 清空当前消息
  messagesContainer.innerHTML = '';
  showEmptyState();

  // 渲染历史消息
  let messages = conv.messages || [];
  messages.forEach(msg => {
    if (msg.role === 'user') {
      addMessage('user', msg.content);
    } else if (msg.role === 'assistant') {
      let extra = msg.extra || {};
      if (extra.steps) {
        addMessage('agent', '已生成步骤：', extra);
      } else {
        addMessage('agent', msg.content);
      }
    }
  });

  // 更新侧边栏高亮
  document.querySelectorAll('.conv-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === sessionId);
  });

  // 更新会话信息栏
  updateConversationInfo(conv.title);
}

async function renameConversation(sessionId) {
  let newTitle = prompt('输入新标题：');
  if (!newTitle) return;
  await fetch('/api/conversations/' + sessionId, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({title: newTitle})
  });
  loadConversations();
}

async function deleteConversation(sessionId) {
  if (!confirm('确定删除该会话？')) return;
  await fetch('/api/conversations/' + sessionId, {method: 'DELETE'});
  if (_currentConversationId === sessionId) {
    _currentConversationId = null;
    messagesContainer.innerHTML = '';
    showEmptyState();
  }
  loadConversations();
}

function showEmptyState() {
  if (!document.getElementById('empty-state')) {
    let div = document.createElement('div');
    div.id = 'empty-state';
    div.className = 'empty-state';
    div.innerHTML = emptyStateHTML();
    messagesContainer.appendChild(div);
  }
}

// ── Helpers ──
function escapeHtml(s) {
  let d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function copyCode(btn) {
  let pre = btn.parentElement;
  let code = pre.querySelector('code').textContent;
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = '已复制!';
    setTimeout(() => btn.textContent = '复制', 1500);
  });
}

function showToast(msg, type) {
  let toast = document.createElement('div');
  toast.className = 'toast ' + type;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 2800);
}

// ── Auto resize textarea ──
document.getElementById('user-input').addEventListener('input', function() {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 150) + 'px';
});

// ── Init ──
loadConfig();
loadConversations();
loadProfiles();
loadKbStatus();
loadControlStats();
loadOverview();
initKbFab();
loadSchemas();

// ── Conversation Info Bar ──
function updateConversationInfo(title) {
  let info = document.getElementById('current-conversation-info');
  let titleSpan = document.getElementById('current-conv-title');
  if (info && titleSpan) {
    if (title) {
      titleSpan.textContent = title;
      info.style.display = 'inline-flex';
    } else {
      info.style.display = 'none';
    }
  }
}
</script>
</body>
</html>'''


# ---------------------------------------------------------------------------
# HTTP 服务
# ---------------------------------------------------------------------------

class AgentGUIHandler(BaseHTTPRequestHandler):
    """处理所有 HTTP 请求。"""

    def log_message(self, format, *args):
        """静默日志。"""
        pass

    def do_OPTIONS(self):
        handle_api(self.path, self)

    def do_GET(self):
        path = urlparse(self.path).path.rstrip("/")
        if path in ("", "/"):
            self._serve_html()
        elif path.startswith("/api/"):
            handle_api(self.path, self)
        else:
            self.send_error(404)

    def do_POST(self):
        if self.path.startswith("/api/"):
            handle_api(self.path, self)
        else:
            self.send_error(404)

    def do_PATCH(self):
        if self.path.startswith("/api/"):
            handle_api(self.path, self)
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            handle_api(self.path, self)
        else:
            self.send_error(404)

    def _serve_html(self):
        html = HTML_TEMPLATE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)


def find_free_port(start: int = 8765, end: int = 8799) -> int:
    """找到可用端口。"""
    import socket
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def start_server(port: int | None = None, open_browser: bool = True) -> HTTPServer:
    """启动 GUI 服务器，返回 server 实例。"""
    if port is None:
        port = find_free_port()

    server = HTTPServer(("127.0.0.1", port), AgentGUIHandler)
    url = f"http://127.0.0.1:{port}"

    print(f"\n{'='*55}")
    print(f"  WT_AUTOMATION Agent GUI 已启动")
    print(f"  地址: {url}")
    print(f"  按 Ctrl+C 停止服务")
    print(f"{'='*55}\n")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务器已停止。")
        server.shutdown()

    return server


def main() -> None:
    """GUI 入口。"""
    port = None
    no_browser = False

    # 简易参数解析
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] in ("--port", "-p") and i + 1 < len(args):
            port = int(args[i + 1])
            i += 2
        elif args[i] in ("--no-browser", "-nb"):
            no_browser = True
            i += 1
        elif args[i] in ("--help", "-h"):
            print("WT_AUTOMATION_Agent GUI")
            print("  --port, -p PORT    指定监听端口")
            print("  --no-browser, -nb  不自动打开浏览器")
            return
        else:
            i += 1

    start_server(port=port, open_browser=not no_browser)


if __name__ == "__main__":
    main()
