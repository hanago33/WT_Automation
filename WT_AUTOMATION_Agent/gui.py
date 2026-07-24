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
import webbrowser
import threading
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
    return build_context_for_agent(
        flow_path=flow_path or None,
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
            if mode == "sequence":
                steps = agent.nl_to_sequence(message, context, conversation_id=conversation_id, compress=compress)
                return _json_response(handler, {"type": "steps", "steps": steps, "mode": "sequence"})
            elif mode == "step":
                steps = agent.nl_to_step(message, context, conversation_id=conversation_id, compress=compress)
                return _json_response(handler, {"type": "steps", "steps": steps, "mode": "step"})
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
<title>WT_AUTOMATION Agent - 自然语言 RPA 对话助手</title>
<style>
:root {
  --bg: #f8f9fb;
  --sidebar-bg: #1e1f2b;
  --sidebar-text: #c8c9d4;
  --sidebar-hover: #2a2b3d;
  --sidebar-active: #32334a;
  --input-bg: #2a2b3d;
  --input-border: #3d3e55;
  --input-text: #e0e0f0;
  --accent: #6c5ce7;
  --accent-hover: #7d6ff0;
  --accent-light: #a29bfe;
  --user-bubble: #6c5ce7;
  --user-bubble-text: #fff;
  --agent-bubble: #fff;
  --agent-bubble-text: #2d2d3f;
  --chat-bg: #f0f1f5;
  --border: #e2e3e9;
  --text: #2d2d3f;
  --text-secondary: #6b6d7f;
  --danger: #e74c3c;
  --success: #27ae60;
  --warning: #f39c12;
  --radius: 12px;
  --radius-sm: 8px;
  --shadow: 0 2px 8px rgba(0,0,0,0.06);
  --shadow-lg: 0 8px 32px rgba(0,0,0,0.1);
  --font: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:var(--font); background:var(--bg); height:100vh; display:flex; overflow:hidden; color:var(--text); }

/* Sidebar */
.sidebar {
  width:280px; min-width:280px; background:var(--sidebar-bg); color:var(--sidebar-text);
  display:flex; flex-direction:column; border-right:1px solid rgba(255,255,255,0.06);
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
}
.messages::-webkit-scrollbar { width:5px; }
.messages::-webkit-scrollbar-thumb { background:rgba(0,0,0,0.12); border-radius:4px; }

.empty-state { flex:1; display:flex; flex-direction:column; align-items:center; justify-content:center; color:var(--text-secondary); text-align:center; padding:40px; }
.empty-state .icon { font-size:48px; margin-bottom:16px; opacity:0.5; }
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
  background:rgba(108,92,231,0.06); padding:8px 12px; border-radius:var(--radius-sm);
  font-size:12px; margin-top:6px; display:flex; align-items:center; gap:8px;
}
.steps-summary .badge { background:var(--accent); color:#fff; font-size:10px; padding:2px 8px; border-radius:10px; font-weight:600; }
.step-detail { margin-top:8px; border:1px solid var(--border); border-radius:var(--radius-sm); overflow:hidden; }
.step-detail .step-item {
  padding:8px 12px; border-bottom:1px solid var(--border); font-size:12px;
  display:flex; align-items:center; gap:8px; cursor:pointer; transition:background 0.15s;
}
.step-detail .step-item:last-child { border-bottom:none; }
.step-detail .step-item:hover { background:rgba(108,92,231,0.04); }
.step-detail .step-item .step-action { color:var(--accent); font-weight:600; min-width:50px; }

/* 序列模式：可拖拽排序 / 可展开步骤清单 */
.seq-steps { margin-top:8px; border:1px solid var(--border); border-radius:var(--radius-sm); overflow:hidden; }
.seq-step {
  display:flex; align-items:center; gap:8px; padding:8px 10px;
  border-bottom:1px solid var(--border); font-size:12px; background:#fff;
  transition:background 0.15s; cursor:default;
}
.seq-step:last-child { border-bottom:none; }
.seq-step.expanded { background:rgba(108,92,231,0.05); }
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
.seq-step .seq-copy:hover { background:rgba(108,92,231,0.08); }
.seq-step .seq-detail {
  flex-basis:100%; order:9; margin:6px 0 0; padding:8px 10px; background:#f7f7fb;
  border-radius:var(--radius-sm); font-size:11px; line-height:1.5; max-height:240px; overflow:auto;
  white-space:pre-wrap; word-break:break-all;
}
.seq-footer { margin-top:8px; }
.seq-footer .btn-sm { font-size:12px; padding:4px 12px; }
.seq-step:focus { outline:2px solid var(--accent); outline-offset:-2px; }
.seq-step:focus:not(.expanded) { background:rgba(108,92,231,0.06); }

/* Input area */
.input-area {
  padding:14px 20px; background:#fff; border-top:1px solid var(--border);
  display:flex; gap:10px; align-items:flex-end;
}
.input-area textarea {
  flex:1; padding:12px 16px; border:1px solid var(--border); border-radius:var(--radius);
  font-size:13px; font-family:var(--font); resize:none; outline:none; max-height:150px;
  transition:border-color 0.2s; line-height:1.5;
}
.input-area textarea:focus { border-color:var(--accent); }
.input-area .send-btn {
  width:42px; height:42px; border-radius:50%; border:none; background:var(--accent);
  color:#fff; font-size:18px; cursor:pointer; display:flex; align-items:center;
  justify-content:center; transition:all 0.2s; flex-shrink:0;
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
</style>
</head>
<body>

<!-- Sidebar -->
<div class="sidebar" id="sidebar">
  <div class="sidebar-header">
    <h2>⚡ WT Agent</h2>
    <div class="sub">自然语言 RPA 流程构建助手</div>
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
        <button class="btn btn-secondary btn-sm" style="width:100%;" onclick="buildKb()">📚 构建/刷新知识库索引</button>
        <div id="kb-status" style="font-size:11px; color:var(--text-secondary); margin-top:6px;">未构建</div>
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
    <div style="display:flex; align-items:center; gap:12px; flex:1;">
      <h3>💬 对话</h3>
      <div id="current-conversation-info" style="font-size:11px; color:var(--text-muted); padding:3px 8px; background:rgba(255,255,255,0.05); border-radius:12px; display:none;">
        <span id="current-conv-title">新会话</span>
      </div>
    </div>
    <div class="actions">
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
      <p>你好！我是 WT RPA 流程构建助手</p>
      <div class="hint">
        配置好 LLM 连接后，你可以：<br>
        • 用自然语言描述要执行的 UI 操作<br>
        • 切换模式进行单步/序列转换<br>
        • 在侧边栏加载控件库文件获得更精准的匹配
      </div>
    </div>
  </div>
  <div class="input-area">
    <textarea id="user-input" rows="1" placeholder="输入你的 RPA 操作指令..."
      onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendMessage();}"></textarea>
    <button class="send-btn" id="send-btn" onclick="sendMessage()" title="发送">▶</button>
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
  emptyState.innerHTML = '<div class="icon">🤖</div><p>聊天已清空</p>';
  messagesContainer.appendChild(emptyState);
}

function scrollToBottom() {
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
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
      '已索引 ' + (data.sources||0) + ' 个源 / ' + (data.chunks||0) + ' 个片段';
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
    if (data.built) {
      document.getElementById('kb-status').textContent =
        '已索引 ' + (data.sources||0) + ' 个源 / ' + (data.chunks||0) + ' 个片段';
    }
  } catch (e) {}
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
    div.innerHTML = `
      <div class="empty-icon">💬</div>
      <div class="empty-title">开始对话</div>
      <div class="empty-hint">输入自然语言指令，或选择转换模式</div>
    `;
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
