# encoding: utf-8
"""WT_AUTOMATION_Agent — 自然语言 RPA 流程构建 Agent。

完全自包含的模块，不依赖 WT_Automation 项目中的任何模块。
可在任何 Python 3.10+ 环境中独立使用。

快速开始（Python API）：
    from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig, DslContext
    from WT_AUTOMATION_Agent.control_index import build_context_for_agent
    from WT_AUTOMATION_Agent.skill_bridge import load_all_skills_text

    config = DslAgentConfig(base_url="https://api.openai.com/v1", api_key="sk-...")
    agent = DslAgent(config)

    skill_text = load_all_skills_text()
    context = build_context_for_agent(skill_text=skill_text)
    steps = agent.nl_to_sequence("点击确认按钮", context)

启动对话式 GUI：
    python -m WT_AUTOMATION_Agent.gui
    python -m WT_AUTOMATION_Agent.cli --gui
"""
from __future__ import annotations

from WT_AUTOMATION_Agent.agent import DslAgent, DslAgentConfig, DslContext
from WT_AUTOMATION_Agent.schemas import (
    ACTION_SCHEMAS,
    get_action_names,
    get_action_schema,
    build_action_default_config,
)
from WT_AUTOMATION_Agent.control_index import (
    build_index_from_controls,
    build_index_from_json,
    build_context_for_agent,
)
from WT_AUTOMATION_Agent.skill_bridge import (
    SkillInfo,
    discover_skills,
    load_all_skills_text,
    get_builtin_skills,
)
from WT_AUTOMATION_Agent.parameter_scan import (
    ParameterScanner,
    ScanConfig,
    ScanResult,
    ParameterRow,
)

__all__ = [
    # 核心
    "DslAgent",
    "DslAgentConfig",
    "DslContext",
    # Schema
    "ACTION_SCHEMAS",
    "get_action_names",
    "get_action_schema",
    "build_action_default_config",
    # 控件索引
    "build_index_from_controls",
    "build_index_from_json",
    "build_context_for_agent",
    # Skill
    "SkillInfo",
    "discover_skills",
    "load_all_skills_text",
    "get_builtin_skills",
    # 参数扫描
    "ParameterScanner",
    "ScanConfig",
    "ScanResult",
    "ParameterRow",
    # GUI
    "start_gui",
]


def start_gui(port: int | None = None, open_browser: bool = True):
    """启动对话式 GUI 界面（阻塞调用）。"""
    from WT_AUTOMATION_Agent.gui import start_server
    return start_server(port=port, open_browser=open_browser)
