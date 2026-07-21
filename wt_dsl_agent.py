# encoding: utf-8
"""【向后兼容包装器】请使用 WT_AUTOMATION_Agent 包替代。

本模块保留仅用于保证已有代码的可导入性。
新代码请直接：

    from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig, DslContext

本文件自动转发至 WT_AUTOMATION_Agent 包。
"""
from __future__ import annotations

import logging as _logging
from typing import Any as _Any

_logging.warning(
    "wt_dsl_agent 已弃用，请改用 WT_AUTOMATION_Agent 包。"
    "  from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig, DslContext"
)

# ── 核心类 ──
from WT_AUTOMATION_Agent.agent import DslAgent as DslAgent
from WT_AUTOMATION_Agent.agent import DslAgentConfig as DslAgentConfig
from WT_AUTOMATION_Agent.agent import DslContext as DslContext
from WT_AUTOMATION_Agent.agent import build_system_prompt as build_system_prompt
from WT_AUTOMATION_Agent.agent import validate_step as validate_step_dict

# ── 兼容别名 ──
def _raw_step_to_full_definition(raw: dict) -> dict:
    from WT_AUTOMATION_Agent.agent import _raw_to_full_step
    return _raw_to_full_step(raw)


__all__ = [
    "DslAgentConfig",
    "DslContext",
    "DslAgent",
    "build_system_prompt",
    "validate_step_dict",
    "_raw_step_to_full_definition",
]
