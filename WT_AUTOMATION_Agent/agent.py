# encoding: utf-8
"""WT_AUTOMATION_Agent 核心模块 —— 自然语言 → 自动化步骤转换器。

完全自包含，不依赖 WT_Automation 项目中的任何模块。
可在任何 Python 3.10+ 环境中使用，唯一外部依赖是 requests。

使用方式：
    from WT_AUTOMATION_Agent import DslAgent, DslAgentConfig

    config = DslAgentConfig(base_url="...", api_key="...")
    agent = DslAgent(config)
    steps = agent.nl_to_sequence("点击按钮然后输入文本", context)

多轮对话支持：
    agent = DslAgent(config)
    conv_id = agent.create_conversation()
    agent.nl_to_sequence("打开文件菜单", context, conversation_id=conv_id)
    agent.nl_to_sequence("选择新建", context, conversation_id=conv_id)  # 上下文保留
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from WT_AUTOMATION_Agent.schemas import (
    ACTION_SCHEMAS,
    build_action_default_config,
    build_action_schema_hint,
    get_action_names,
    get_action_schema,
)
from WT_AUTOMATION_Agent.parameter_scan import ParameterScanner, ScanResult
from WT_AUTOMATION_Agent.history_store import (
    Conversation,
    add_message,
    clear_conversation,
    create_conversation,
    delete_conversation,
    get_messages_for_llm,
    list_conversations,
    load_conversation,
    rename_conversation,
    save_conversation,
)
from WT_AUTOMATION_Agent import memory
from WT_AUTOMATION_Agent import knowledge_base
from WT_AUTOMATION_Agent import control_search
from WT_AUTOMATION_Agent import flow_ops
from WT_AUTOMATION_Agent import log_diagnosis

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

@dataclass
class DslAgentConfig:
    """DSL Agent 的 LLM 连接配置。

    支持任何 OpenAI-compatible API（包括中转站）。密钥不硬编码。
    优先级：构造函数参数 > 环境变量。
    """
    base_url: str = ""
    api_key: str = ""
    model: str = "gpt-4o"
    timeout: int = 120
    """单次请求超时秒数。中转站建议 120+。"""
    max_tokens: int = 2048
    temperature: float = 0.1  # 低温度保证稳定输出
    max_retries: int = 3
    """失败重试次数（指数退避：1s → 2s → 4s）。"""
    retry_backoff: float = 2.0
    """重试退避因子。"""
    retry_on_status: tuple = (429, 500, 502, 503, 504)
    """触发重试的 HTTP 状态码。"""

    # 环境变量名
    ENV_KEY_BASE_URL = "WT_DSL_BASE_URL"
    ENV_KEY_API_KEY = "WT_DSL_API_KEY"
    ENV_KEY_MODEL = "WT_DSL_MODEL"

    def __post_init__(self):
        if not self.base_url:
            self.base_url = os.environ.get(self.ENV_KEY_BASE_URL, "")
        if not self.api_key:
            self.api_key = os.environ.get(self.ENV_KEY_API_KEY, "")
        if not self.model or self.model == "gpt-4o":
            self.model = os.environ.get(self.ENV_KEY_MODEL, "gpt-4o")
        # 兜底：环境变量未配置时，回退到 model_profiles 的默认档案，
        # 这样即便只通过 GUI/JSON 配置了"模型配置档案"，CLI 也能直接用它。
        if not self.base_url or not self.api_key:
            try:
                from WT_AUTOMATION_Agent.model_profiles import get_default
                prof = get_default()
                if prof:
                    self.base_url = self.base_url or (prof.get("base_url") or "").rstrip("/")
                    self.api_key = self.api_key or (prof.get("api_key") or "")
                    if not self.model or self.model == "gpt-4o":
                        self.model = prof.get("model") or self.model
                    if prof.get("timeout"):
                        try:
                            self.timeout = int(prof["timeout"])
                        except (TypeError, ValueError):
                            pass
            except Exception:
                logger.exception("回退到 model_profiles 默认档案失败")

    def is_ready(self) -> bool:
        return bool(self.base_url and self.api_key)


# ---------------------------------------------------------------------------
# 上下文
# ---------------------------------------------------------------------------

@dataclass
class DslContext:
    """Agent 执行转换时需要的工程上下文。"""
    control_index_text: str = ""
    """控件库索引文本。"""

    action_schema_text: str = ""
    """动作 schema 说明文本。"""

    examples_text: str = ""
    """Few-shot 示例步骤 JSON。"""

    current_step_names: list[str] = field(default_factory=list)
    """当前流程中已存在的步骤名称列表。"""

    project_description: str = ""
    """当前自动化项目的简短描述。"""

    skill_context_text: str = ""
    """Skill 提供的领域知识文本（由 skill_bridge 加载）。"""

    available_excel_params: str = ""
    """可用的 Excel 参数表提示（用于参数扫描意图识别）。"""


# ---------------------------------------------------------------------------
# Function Calling 工具定义
# ---------------------------------------------------------------------------

def _build_tools_definition() -> list[dict]:
    """从 ACTION_SCHEMAS 自动生成 Function Calling 定义。"""
    action_enum = list(get_action_names())
    action_lines = []
    for name in get_action_names():
        schema = get_action_schema(name)
        action_lines.append(f"  - {name}: {schema.get('label')} - {schema.get('description')}")

    return [
        {
            "type": "function",
            "function": {
                "name": "add_step",
                "description": (
                    "在自动化流程中添加一个步骤。\n可用 action:\n" + "\n".join(action_lines)
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": action_enum,
                            "description": "动作类型。click=左键单击，right_click=右键，double_click=双击，type_text=输入文本，send_keys=发送按键，type_text_relative=父窗口区域输入，click_relative_region=父窗口区域点击，click_relative_anchor=锚点相对点击，select_dropdown_item_runtime=运行时下拉选择，wait_for_control=等待控件，mouse_wheel=滚轮，sleep=等待，drag_and_drop=拖放，log=日志。",
                        },
                        "control_id": {
                            "type": "string",
                            "description": "目标控件 ID。从控件库中选择精确的 ID。如果找不到匹配的控件，可以描述需要的控件特征。",
                        },
                        "text": {
                            "type": "string",
                            "description": "输入文本或参数值。用于 type_text/send_keys（输入内容）、mouse_wheel（滚轮值）、sleep（等待秒数）、log（日志信息）。",
                        },
                        "timeout_seconds": {
                            "type": "number",
                            "description": "等待控件的超时秒数，默认 3。",
                        },
                        "window_title_hint": {
                            "type": "string",
                            "description": "目标窗口标题提示。",
                        },
                        "step_name": {
                            "type": "string",
                            "description": "步骤名称，建议如'点击-按钮名'或'输入-字段名'。",
                        },
                        "wait_after": {
                            "type": "number",
                            "description": "动作完成后等待秒数，默认 0.3。",
                        },
                        "retry_count": {
                            "type": "integer",
                            "description": "失败重试次数，默认 0。",
                        },
                        "on_error": {
                            "type": "string",
                            "enum": ["continue", "retry", "stop", "fallback", "ask"],
                            "description": "失败处理方式：continue=跳过，retry=重试，stop=停止，fallback=兜底，ask=AI介入。",
                        },
                        "description": {
                            "type": "string",
                            "description": "步骤的业务说明，方便维护。",
                        },
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_sequence",
                "description": (
                    "批量添加多个步骤。适合需要连续操作的场景。\n可用 action:\n" + "\n".join(action_lines)
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "steps": {
                            "type": "array",
                            "description": "连续步骤列表，每个步骤字段与 add_step 一致。",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "enum": action_enum,
                                        "description": "动作类型。",
                                    },
                                    "control_id": {
                                        "type": "string",
                                        "description": "目标控件 ID。",
                                    },
                                    "text": {
                                        "type": "string",
                                        "description": "输入文本。",
                                    },
                                    "timeout_seconds": {
                                        "type": "number",
                                        "description": "超时秒数。",
                                    },
                                    "window_title_hint": {
                                        "type": "string",
                                        "description": "窗口标题提示。",
                                    },
                                    "step_name": {
                                        "type": "string",
                                        "description": "步骤名称。",
                                    },
                                    "wait_after": {
                                        "type": "number",
                                        "description": "完成后等待秒数。",
                                    },
                                    "retry_count": {
                                        "type": "integer",
                                        "description": "重试次数。",
                                    },
                                    "on_error": {
                                        "type": "string",
                                        "enum": ["continue", "retry", "stop", "fallback", "ask"],
                                        "description": "失败处理方式。",
                                    },
                                    "description": {
                                        "type": "string",
                                        "description": "步骤说明。",
                                    },
                                },
                                "required": ["action"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["steps"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": "add_parameter_scan",
                "description": (
                    "多参数扫描：从 Excel 参数表读取多行参数，每行驱动一组模板步骤执行一次。"
                    "适合批量创建、批量导入等需用不同参数重复同一流程的场景。"
                    "参数表的每一行对应 ${stepParams.xxx} 中的一组值。\n"
                    "可用 action:\n" + "\n".join(action_lines)
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "excel_path": {
                            "type": "string",
                            "description": "参数 Excel 文件路径（.xlsx/.csv）。",
                        },
                        "sheet_name": {
                            "type": "string",
                            "description": "Excel Sheet 名称，默认 Sheet1。",
                        },
                        "template_steps": {
                            "type": "array",
                            "description": (
                                "模板步骤列表，每个步骤的 text 等字段可用 ${stepParams.xxx} "
                                "占位符引用 Excel 列头。每行参数会生成一份完整副本。"
                            ),
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {
                                        "type": "string",
                                        "enum": action_enum,
                                        "description": "动作类型。",
                                    },
                                    "control_id": {
                                        "type": "string",
                                        "description": "目标控件 ID。",
                                    },
                                    "text": {
                                        "type": "string",
                                        "description": "输入文本，可用 ${stepParams.xxx} 引用参数。",
                                    },
                                    "timeout_seconds": {
                                        "type": "number",
                                        "description": "超时秒数。",
                                    },
                                    "window_title_hint": {
                                        "type": "string",
                                        "description": "窗口标题提示。",
                                    },
                                    "step_name": {
                                        "type": "string",
                                        "description": "步骤名称（可含 ${stepParams.xxx}）。",
                                    },
                                    "wait_after": {"type": "number", "description": "完成后等待秒数。"},
                                    "retry_count": {"type": "integer", "description": "重试次数。"},
                                    "on_error": {
                                        "type": "string",
                                        "enum": ["continue", "retry", "stop", "fallback", "ask"],
                                        "description": "失败处理方式。",
                                    },
                                    "description": {"type": "string", "description": "步骤说明。"},
                                },
                                "required": ["action"],
                                "additionalProperties": False,
                            },
                        },
                        "max_rows": {
                            "type": "integer",
                            "description": "最大扫描行数限制（0=全部）。",
                        },
                    },
                    "required": ["excel_path", "template_steps"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": "find_control",
                "description": (
                    "在控件库（control_maps）里按自然语言描述检索真实存在的控件，"
                    "返回候选控件及其 control_id（targetValue）。"
                    "当你不确定某一步的 control_id，或想确认某个控件是否真实存在时，先调用本工具，"
                    "再把返回的 targetValue 填入 add_step / add_sequence 的 control_id 字段，"
                    "可显著减少'点击了不存在的控件'这类失败。"
                    "例如 find_control('风机类型下拉框') 或 find_control('GeographicalData')。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "控件描述，可含中文（如'风机类型下拉框'）或英文标识片段（如'GeographicalData'）。",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "返回的候选控件数量，默认 5。",
                        },
                        "action": {
                            "type": "string",
                            "description": (
                                "可选：该控件将执行的动作名（如 click / type_text / "
                                "set_combobox），传入后会优先返回能执行该动作的控件类型。"
                            ),
                        },
                        "within": {
                            "type": "string",
                            "description": (
                                "可选：限定在某窗口/视图（uiPath 祖先）范围内检索，"
                                "如 'MUPWindTurbineTypeMainView' 只在风机类型视图里找，"
                                "能大幅提升歧义控件的命中精度。"
                            ),
                        },
                    },
                    "required": ["query"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
        {
            "type": "function",
            "function": {
                "name": "control_tree",
                "description": (
                    "返回 WT 应用的控件层级树（按 uiPath：Window > 视图 > 容器 > 控件），"
                    "列出各窗口/视图及其下控件数。当你要定位某功能在哪个视图、或对某视图内控件"
                    "做精确检索前，先调用本工具获得视图名，再传 within 给 find_control 缩小范围。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "max_depth": {
                            "type": "integer",
                            "description": "树展开深度，默认 3（1=只到视图层）。",
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        },
    ]


_TOOLS = _build_tools_definition()


# ---------------------------------------------------------------------------
# System Prompt 构建
# ---------------------------------------------------------------------------

def build_system_prompt(context: DslContext | None = None) -> str:
    """构建 System Prompt。"""
    parts = [
        "你是一个 RPA (Robotic Process Automation) 流程构建助手。",
        "你的任务是将用户的自然语言指令转换为标准化的自动化步骤定义。",
        "",
        "## 工作方式",
        "1. 理解用户用自然语言描述的 RPA 操作意图。",
        "2. 选择合适的 action 类型，并从控件库中匹配合适的控件。",
        "3. 通过 add_step（单步）或 add_sequence（多步）Function Calling 输出。",
        "4. 不确定时根据语义猜测最合适的 action 和控件，不要拒绝回答。",
        "",
    ]

    ctx = context or DslContext()

    # 动作说明
    if ctx.action_schema_text:
        parts.append(ctx.action_schema_text)
    else:
        schema_lines = ["## 可用动作说明"]
        for name in get_action_names():
            schema_lines.append(f"  - {name}: {build_action_schema_hint(name)}")
        parts.append("\n".join(schema_lines))

    # Skill 领域知识
    if ctx.skill_context_text:
        parts.extend(["", "## 领域知识（来自 Skill）", ctx.skill_context_text])

    # 控件库
    if ctx.control_index_text:
        parts.extend(["", "## 可用控件库", ctx.control_index_text])

    # 已有步骤
    if ctx.current_step_names:
        parts.extend(["", "## 已有步骤", *[f"  - {n}" for n in ctx.current_step_names]])

    # 项目描述
    if ctx.project_description:
        parts.extend(["", "## 项目描述", ctx.project_description])

    # 输出规范
    parts.extend([
        "",
        "## 输出规范",
        "1. action 必须从列表中选择。",
        "2. control_id 尽量从控件库中选择。",
        "3. step_name 格式：'动作-对象'（如'点击-风机类型'）。",
        "4. 非必要字段不填，系统补默认值。",
        "5. 连续操作用 add_sequence 一次性输出。",
        "6. 使用 snake_case 字段名（control_id, step_name）。",
    ])

    # 参数扫描与 Excel 联动知识（关键）
    parts.extend([
        "",
        "## 多参数扫描规则",
        "当用户说'批量''扫描参数''用 Excel 驱动''多组参数运行'时：",
        "1. 使用 add_parameter_scan 工具。",
        "2. 每个模板步骤的 text/step_name 字段支持 ${stepParams.xxx} 动态引用。",
        "3. Excel 列头 → stepParams 键名（以下为已知映射）：",
        "   - 经度/lon → ${stepParams.lon}",
        "   - 纬度/lat → ${stepParams.lat}",
        "   - 网格分辨率/grid_resolution → ${stepParams.gridResolution}",
        "   - 输出目录/output_dir → ${stepParams.outputDir}",
        "   - 投影文件/projection_file → ${stepParams.projectionFile}",
        "   - 地形文件/terrain_file → ${stepParams.terrainFile}",
        "   - 测风塔文件/mast_file → ${stepParams.mastFile}",
        "   - 风机厂商/manufacturer → ${stepParams.manufacturer}",
        "   - 风机型号/turbine_model → ${stepParams.turbineModel}",
        "   - 功率曲线文件/power_curve_file → ${stepParams.powerCurveFile}",
        "   - 推力曲线文件/thrust_curve_file → ${stepParams.thrustCurveFile}",
        "   - 数据文件/file_path → ${stepParams.filePath}",
        "4. 不在上述映射中的列头，使用 snake_case 英文名。",
        "5. 若用户提供 Excel 文件路径，先尝试读取其列头来推断参数名。",
    ])

    if ctx.examples_text:
        parts.extend(["", "## 参考示例", ctx.examples_text])

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# LLM 调用与响应解析
# ---------------------------------------------------------------------------

def _build_session(config: DslAgentConfig) -> "requests.Session":
    """构建带重试逻辑的 requests.Session。

    使用 urllib3 Retry 实现指数退避重试，零额外依赖。
    参考：OpenAI Python SDK / langchain 的通用做法。
    """
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry_strategy = Retry(
        total=config.max_retries,
        backoff_factor=config.retry_backoff,
        status_forcelist=list(config.retry_on_status),
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    # 禁用系统代理（避免公司代理拦截对中转站的请求）
    session.trust_env = False
    return session


# 模块级 Session 缓存（连接复用，减少 TCP 握手开销）
_sessions: dict[str, "requests.Session"] = {}


def _get_session(config: DslAgentConfig) -> "requests.Session":
    """获取或创建 Session（按 base_url 缓存）。"""
    key = config.base_url
    if key not in _sessions:
        _sessions[key] = _build_session(config)
    return _sessions[key]


def _call_llm(
    config: DslAgentConfig,
    messages: list[dict],
    tools: list[dict] | None = None,
) -> dict[str, Any]:
    """调用 OpenAI-compatible Chat Completion API。

    自动重试：连接超时 / 读取超时 / 429 限流 / 5xx 服务端错误。
    退避策略：第1次重试等 1*backoff=2s，第2次等 4s，第3次等 8s。
    """
    url = config.base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        "max_tokens": config.max_tokens,
        "temperature": config.temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    session = _get_session(config)
    # 使用 connect timeout 和 read timeout 分离（中转站连接快但响应慢）
    resp = session.post(
        url,
        headers=headers,
        json=payload,
        timeout=(15, config.timeout),  # (connect=15s, read=config.timeout)
    )
    resp.raise_for_status()
    return resp.json()


def _parse_tool_calls(response: dict[str, Any]) -> list[dict[str, Any]]:
    choices = response.get("choices", [])
    if not choices:
        raise ValueError(f"LLM 返回空 choices: {response}")

    message = choices[0].get("message", {})
    tool_calls = message.get("tool_calls", [])
    if not tool_calls:
        content = message.get("content", "")
        raise ValueError(f"LLM 未返回 tool_calls, 响应: {content[:200]}")

    raw_steps: list[dict] = []
    for tc in tool_calls:
        func = tc.get("function", {})
        name = func.get("name", "")
        try:
            args = json.loads(func.get("arguments", "{}"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 解析失败: {exc}") from exc

        if name == "add_step":
            raw_steps.append(args)
        elif name == "add_sequence":
            raw_steps.extend(args.get("steps", []))

    return raw_steps


def _extract_json_array(text: str | None) -> list | None:
    """从 LLM 回复中提取 JSON 数组（兼容代码块包裹 / 前后多余文字）。"""
    if not text:
        return None
    try:
        obj = json.loads(text.strip())
        if isinstance(obj, list):
            return obj
    except (json.JSONDecodeError, ValueError):
        pass
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, list):
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# ---------------------------------------------------------------------------
# 步骤生成
# ---------------------------------------------------------------------------

def _raw_to_full_step(raw: dict[str, Any]) -> dict[str, Any]:
    """将 LLM 返回的简略参数补全为完整步骤定义。"""
    action_name = str(raw.get("action", "click")).strip() or "click"
    control_id = str(raw.get("control_id", "")).strip()
    text = str(raw.get("text", "")).strip()
    step_name = str(raw.get("step_name", "")).strip()
    timeout_seconds = raw.get("timeout_seconds")
    window_title_hint = str(raw.get("window_title_hint", "")).strip()
    wait_after = raw.get("wait_after")
    retry_count = raw.get("retry_count")
    on_error = str(raw.get("on_error", "")).strip() or ""
    description = str(raw.get("description", "")).strip()

    overrides: dict[str, Any] = {}
    if control_id:
        overrides["controlId"] = control_id
    if text:
        overrides["text"] = text
    if timeout_seconds is not None:
        overrides["timeoutSeconds"] = float(timeout_seconds)
    if window_title_hint:
        overrides["windowTitleHint"] = window_title_hint
    if wait_after is not None:
        overrides["waitAfter"] = float(wait_after)
    if retry_count is not None:
        overrides["retryCount"] = int(retry_count)
    if on_error:
        overrides["onError"] = on_error

    action_config = build_action_default_config(action_name, **overrides)
    schema = get_action_schema(action_name)

    step: dict[str, Any] = {
        "id": "",
        "name": step_name or _auto_name(action_name, control_id, text, schema),
        "stage": "converted",
        "strategy": "action",
        "actionType": "action",
        "topLevel": True,
        "enabled": True,
        "actionConfig": action_config,
        "description": description,
    }

    if control_id:
        step["controls"] = [_build_step_control(action_name, control_id)]

    return step


def _build_step_control(action_name: str, control_id: str) -> dict[str, Any]:
    """把 control_id 对标到控件库真实控件，回填名称/定位方法/uiPath 等信息。

    对标策略（依赖 control_search.best_control_for_step）：
    1. 精确命中（targetValue / automationId 全等）→ 直接采用库内记录；
    2. 未精确命中 → 按动作语义模糊检索，高置信候选（score>=6）才采用，
       并在 notes 中标注"模糊匹配"，方便人工复核；
    3. 完全未命中 → 保留原 control_id，notes 标注"未在控件库中找到"。
    """
    fallback = {
        "id": control_id,
        "name": f"{action_name} 目标控件",
        "role": "",
        "enabled": True,
        "targetMethod": "automation_id",
        "targetValue": control_id,
        "notes": "未在控件库中找到，请人工确认该控件真实存在",
    }
    if control_search is None:
        fallback.pop("notes", None)
        return fallback
    try:
        rec, exact = control_search.best_control_for_step(action_name, control_id)
    except Exception:
        logger.exception("control_search.best_control_for_step 调用失败")
        fallback.pop("notes", None)
        return fallback
    if rec is None:
        return fallback

    target_method = str(rec.get("targetMethod", "")).strip() or "automation_id"
    target_value = str(rec.get("targetValue", "")).strip() or control_id
    name = str(rec.get("name", "")).strip() \
        or str(rec.get("labelText", "")).strip() \
        or f"{action_name} 目标控件"
    notes_parts: list[str] = []
    if not exact:
        notes_parts.append(f"由控件库模糊匹配（原 control_id: {control_id}），建议复核")
    if rec.get("labelText") and rec.get("labelText") != name:
        notes_parts.append(f"关联标签: {rec['labelText']}")
    if rec.get("optionValues"):
        notes_parts.append("下拉选项: " + " / ".join(rec["optionValues"][:8]))

    control: dict[str, Any] = {
        "id": control_id,
        "name": name,
        "role": str(rec.get("controlType", "")),
        "enabled": True,
        "targetMethod": target_method,
        "targetValue": target_value,
    }
    if rec.get("uiPath"):
        control["uiPath"] = rec["uiPath"]
    if notes_parts:
        control["notes"] = "；".join(notes_parts)
    return control


def _auto_name(action: str, control_id: str, text: str, schema: dict | None = None) -> str:
    if not schema:
        schema = get_action_schema(action)
    label = schema.get("label", action)
    if control_id:
        readable = control_id.replace("_", " ").replace("-", " ")
        if len(readable) > 40:
            readable = readable[-40:]
        return f"{label}-{readable}"
    if text:
        return f"{label}-{text[:20]}"
    return label


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def validate_step(step: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    ac = step.get("actionConfig", {})
    if not isinstance(ac, dict):
        errors.append("缺少 actionConfig")
        return errors

    action_name = ac.get("action", "")
    if action_name not in get_action_names():
        errors.append(f"不支持的 action: {action_name}")

    schema = get_action_schema(action_name)
    if schema.get("target_required") and not ac.get("controlId"):
        errors.append(f"'{action_name}' 需要 controlId")
    if schema.get("input_required") and schema.get("input_key"):
        if not ac.get(schema["input_key"]):
            errors.append(f"'{action_name}' 需要 {schema['input_key']}")

    return errors


# ---------------------------------------------------------------------------
# DslAgent 主类
# ---------------------------------------------------------------------------

class DslAgent:
    """DSL Agent 主类 —— 将自然语言 RPA 指令转为结构化步骤定义。

    完全自包含，可在任何 Python 环境中独立使用。
    支持多轮对话，可通过 conversation_id 保持上下文。
    """

    def __init__(self, config: DslAgentConfig, max_tool_iterations: int = 4):
        self.config = config
        self._tools = _TOOLS
        self._max_tool_iterations = max_tool_iterations

    def _ensure_ready(self):
        if not self.config.is_ready():
            raise RuntimeError(
                "DSL Agent 未配置。请设置 base_url 和 api_key，"
                "或设置环境变量 WT_DSL_BASE_URL / WT_DSL_API_KEY。"
            )

    # -------------------------------------------------------------------------
    # 会话管理
    # -------------------------------------------------------------------------

    def create_conversation(self, title: str = "") -> str:
        """创建新会话，返回会话 ID。"""
        conv = Conversation.new(title=title)
        save_conversation(conv)
        return conv.id

    def list_conversations(self) -> list[dict[str, Any]]:
        """列出所有会话。"""
        return list_conversations()

    def load_conversation(self, session_id: str) -> Conversation | None:
        """加载指定会话。"""
        return load_conversation(session_id)

    def delete_conversation(self, session_id: str) -> bool:
        """删除指定会话。"""
        return delete_conversation(session_id)

    def rename_conversation(self, session_id: str, new_title: str) -> bool:
        """重命名会话。"""
        return rename_conversation(session_id, new_title)

    def clear_conversation(self, session_id: str) -> bool:
        """清空会话消息（保留会话）。"""
        return clear_conversation(session_id)

    # -------------------------------------------------------------------------
    # 步骤生成
    # -------------------------------------------------------------------------

    def nl_to_step(
        self,
        nl_text: str,
        context: DslContext | None = None,
        conversation_id: str | None = None,
        *,
        compress: bool = True,
    ) -> list[dict]:
        """自然语言指令 → 一个或多个步骤。

        Args:
            nl_text: 自然语言指令
            context: 工程上下文
            conversation_id: 会话 ID（用于多轮对话上下文保留）
        """
        self._ensure_ready()
        system_prompt = build_system_prompt(context)

        if conversation_id:
            # 多轮对话：从历史加载消息（可压缩）
            history = self._history_messages(system_prompt, conversation_id, compress)
            messages = history + [{"role": "user", "content": f"请将以下指令转换为自动化步骤：\n\n{nl_text}\n\n使用 add_step 或 add_sequence。"}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请将以下指令转换为自动化步骤：\n\n{nl_text}\n\n使用 add_step 或 add_sequence。"},
            ]

        steps = self._run(messages)

        # 记录到历史
        if conversation_id:
            add_message(conversation_id, "user", f"请将以下指令转换为自动化步骤：\n\n{nl_text}")
            # 记录结果（简化）
            step_names = [s.get("name", "") for s in steps]
            add_message(conversation_id, "assistant", f"生成了 {len(steps)} 个步骤：{', '.join(step_names)}", {"steps": steps})

        return steps

    def nl_to_sequence(
        self,
        nl_text: str,
        context: DslContext | None = None,
        conversation_id: str | None = None,
        *,
        compress: bool = True,
    ) -> list[dict]:
        """自然语言流程 → 步骤序列。

        Args:
            nl_text: 自然语言流程描述
            context: 工程上下文
            conversation_id: 会话 ID（用于多轮对话上下文保留）
        """
        self._ensure_ready()
        system_prompt = build_system_prompt(context)

        if conversation_id:
            history = self._history_messages(system_prompt, conversation_id, compress)
            messages = history + [{"role": "user", "content": f"请将以下流程转换为自动化步骤序列：\n\n{nl_text}\n\n使用 add_sequence 一次性输出。"}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请将以下流程转换为自动化步骤序列：\n\n{nl_text}\n\n使用 add_sequence 一次性输出。"},
            ]

        steps = self._run(messages)

        # 记录到历史
        if conversation_id:
            add_message(conversation_id, "user", f"请将以下流程转换为自动化步骤序列：\n\n{nl_text}")
            step_names = [s.get("name", "") for s in steps]
            add_message(conversation_id, "assistant", f"生成了 {len(steps)} 个步骤：{', '.join(step_names)}", {"steps": steps})

        return steps

    def _run(self, messages: list[dict]) -> list[dict]:
        """调用 LLM 并解析工具调用。

        支持工具循环：若模型先调用 find_control（检索控件库），则把检索结果
        作为 tool 消息回灌，再让模型基于真实控件 ID 发出 add_step / add_sequence。
        """
        steps: list[dict] = []
        for _ in range(getattr(self, "_max_tool_iterations", 4)):
            response = _call_llm(self.config, messages, self._tools)
            message = response.get("choices", [{}])[0].get("message", {})
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                logger.warning("LLM 未返回 tool_calls，返回空步骤")
                break
            messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            })
            lookup_calls = [
                tc for tc in tool_calls
                if tc.get("function", {}).get("name") in ("find_control", "control_tree")
            ]
            if lookup_calls:
                for tc in lookup_calls:
                    try:
                        args = json.loads(tc.get("function", {}).get("arguments", "{}") or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    name = tc.get("function", {}).get("name")
                    if name == "control_tree":
                        result = self._exec_control_tree(args)
                        tool_name = "control_tree"
                    else:
                        result = self._exec_find_control(args)
                        tool_name = "find_control"
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id"),
                        "name": tool_name,
                        "content": result,
                    })
                continue
            raw = _parse_tool_calls(response)
            for r in raw:
                step = _raw_to_full_step(r)
                errs = validate_step(step)
                if errs:
                    logger.warning("步骤校验警告: %s; step=%s", "; ".join(errs), step.get("name", ""))
                steps.append(step)
            break
        return steps

    def _exec_find_control(self, args: dict) -> str:
        """执行 find_control 工具：在控件库检索真实控件，返回给 LLM 的文本结果。"""
        if control_search is None:
            return "控件检索模块不可用。"
        query = str(args.get("query") or args.get("name") or args.get("control") or "").strip()
        try:
            top_k = int(args.get("top_k") or 5)
        except (TypeError, ValueError):
            top_k = 5
        if not query:
            return "查询为空，请提供控件描述（例如 '风机类型下拉框'）。"
        action = str(args.get("action") or "").strip()
        within = str(args.get("within") or "").strip() or None
        return control_search.search_text(query, top_k=top_k, action=action, within=within)

    def _exec_control_tree(self, args: dict) -> str:
        """执行 control_tree 工具：返回 WT 应用控件层级树大纲。"""
        if control_search is None:
            return "控件检索模块不可用。"
        try:
            max_depth = int(args.get("max_depth") or 3)
        except (TypeError, ValueError):
            max_depth = 3
        return control_search.tree_summary(max_depth=max_depth)

    def test_connection(self) -> bool:
        """测试 API 连接。"""
        if not self.config.is_ready():
            return False
        try:
            resp = _call_llm(self.config, [{"role": "user", "content": "回复 OK 表示连接正常。"}])
            choices = resp.get("choices", [])
            return bool(choices and choices[0].get("message", {}).get("content"))
        except Exception:
            return False

    # -------------------------------------------------------------------------
    # 记忆压缩 / 知识库 辅助
    # -------------------------------------------------------------------------

    def _summarize(self, text: str) -> str:
        """用 LLM 把一段对话历史压缩为摘要。"""
        try:
            resp = _call_llm(self.config, [
                {"role": "system", "content": memory.SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ])
            return resp.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
        except Exception as exc:
            logger.warning("对话摘要生成失败: %s", exc)
            return ""

    def _kb_context(self, query: str) -> str:
        """检索项目知识库，返回可注入 system prompt 的上下文文本。"""
        try:
            return knowledge_base.get_knowledge_base().build_context(query)
        except Exception as exc:
            logger.warning("知识库检索失败: %s", exc)
            return ""

    def _history_messages(self, system_prompt: str, conversation_id: str | None, compress: bool) -> list[dict]:
        """构造带压缩历史的 LLM 消息列表。"""
        return memory.prepare_messages(
            system_prompt, conversation_id or "",
            summarizer=self._summarize if compress else None,
            compress=compress,
        )

    def chat(
        self,
        user_message: str,
        context: DslContext | None = None,
        conversation_id: str | None = None,
        *,
        kb_enabled: bool = True,
        compress: bool = True,
    ) -> str:
        """通用对话接口，不强制 Function Calling，返回文本。

        适合让 LLM 回答关于流程的问题、做分析等。
        支持多轮对话（传入 conversation_id）。
        可通过 kb_enabled 注入项目知识库上下文，通过 compress 启用长对话记忆压缩。

        Args:
            user_message: 用户消息
            context: 工程上下文
            conversation_id: 会话 ID（用于多轮对话上下文保留）
            kb_enabled: 是否注入项目知识库检索结果
            compress: 是否对早期对话进行记忆压缩

        Returns:
            LLM 响应文本
        """
        self._ensure_ready()
        system_prompt = build_system_prompt(context)

        if kb_enabled:
            kb_ctx = self._kb_context(user_message)
            if kb_ctx:
                system_prompt = (
                    system_prompt
                    + "\n\n## 项目知识库参考\n下面是项目自有知识库中与用户问题可能相关的内容，"
                    "仅作为背景参考；若与用户问题无关或相互矛盾，以你自己的判断为准：\n"
                    + kb_ctx
                )

        if conversation_id:
            history = self._history_messages(system_prompt, conversation_id, compress)
            messages = history + [{"role": "user", "content": user_message}]
        else:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

        resp = _call_llm(self.config, messages)  # 不传 tools，纯对话
        choices = resp.get("choices", [])
        reply = ""
        if choices:
            reply = choices[0].get("message", {}).get("content", "")

        # 记录到历史
        if conversation_id and reply:
            add_message(conversation_id, "user", user_message)
            add_message(conversation_id, "assistant", reply)

        return reply

    # ------------------------------------------------------------------
    # 流程维护能力：解释 / 编辑 / 比对
    # ------------------------------------------------------------------

    def explain_flow(self, flow: dict[str, Any], question: str = "") -> str:
        """解释一份已有流程定义（flow_definition.json）。"""
        self._ensure_ready()
        text = flow_ops.flow_to_text(flow)
        prompt = (
            "你是一名 WT（Meteodyn WT）桌面自动化流程工程师。下面是一份自动化流程定义，"
            "请基于它回答用户的问题。\n\n"
            f"【流程定义】\n{text}\n\n"
            f"【问题】{question or '请概述这个流程在做什么、每一步的作用，以及潜在脆弱点和失败风险。'}"
        )
        return self.chat(prompt, kb_enabled=True, compress=False)

    def edit_flow(self, flow: dict[str, Any], instruction: str, write_back: bool = False) -> dict[str, Any]:
        """按自然语言指令修改一份流程的步骤，返回 {ok, steps, raw}。

        若 write_back=True 且解析成功，则直接写回传入的 flow 字典（由调用方负责落盘）。
        """
        self._ensure_ready()
        steps = flow.get("steps", [])
        prompt = (
            "你是一名 WT 自动化流程工程师。下面是当前流程的步骤列表（JSON 数组）。"
            "请根据用户指令修改，并只输出修改后的完整步骤 JSON 数组，"
            "不要包含任何解释文字、也不要使用代码块标记。\n\n"
            f"【当前步骤】\n{json.dumps(steps, ensure_ascii=False, indent=2)}\n\n"
            f"【修改指令】{instruction}\n\n"
            "要求：保持每个步骤原有字段结构；新增步骤请补全字段；"
            "control_id 必须使用控件库中真实存在的标识（如 MUPMicroscaleInformationViewModel_Button_...）。"
        )
        reply = self.chat(prompt, kb_enabled=False, compress=False)
        new_steps = _extract_json_array(reply)
        result: dict[str, Any] = {
            "instruction": instruction,
            "ok": bool(new_steps),
            "steps": new_steps if new_steps else steps,
            "raw": reply,
        }
        if new_steps and write_back:
            flow["steps"] = new_steps
        return result

    def diff_flows(self, flow_a: dict[str, Any], flow_b: dict[str, Any]) -> str:
        """对比两份流程定义，返回带风险说明的差异分析。"""
        self._ensure_ready()
        struct = flow_ops.diff_flows_structural(flow_a, flow_b)
        prompt = (
            "你是一名 WT 自动化流程工程师。请基于以下两份流程的结构化差异，"
            "用简洁中文说明步骤的增/删/改、参数变化与潜在风险。\n\n"
            f"【结构化差异】\n{struct}\n"
        )
        return self.chat(prompt, kb_enabled=False, compress=False)

    # ------------------------------------------------------------------
    # 执行日志 / 运行报告诊断
    # ------------------------------------------------------------------

    def diagnose_log(self, log_input: str, flow_steps: list[dict[str, Any]] | None = None) -> str:
        """诊断一段执行日志或运行报告，定位失败步骤并给出修复建议。

        Args:
            log_input: 日志文本、JSON 报告文本，或本地日志/报告文件路径。
            flow_steps: 可选，相关流程的步骤列表（用于对照定位）。
        """
        self._ensure_ready()
        log_text: str | None = None
        p = (log_input or "").strip()
        if p and (p.startswith("{") or p.startswith("[")) and len(p) > 50:
            log_text = p
        elif p and os.path.exists(p):
            if p.lower().endswith(".json"):
                report = log_diagnosis.load_run_report(p)
                if report:
                    log_text = json.dumps(report, ensure_ascii=False, indent=2)
                    if not flow_steps:
                        flow_steps = [
                            s for s in report.get("steps", []) if isinstance(s, dict)
                        ]
            else:
                log_text = log_diagnosis.parse_run_log_file(p)
        if log_text is None:
            log_text = p
        prompt = log_diagnosis.build_diagnosis_prompt(log_text, flow_steps)
        return self.chat(prompt, kb_enabled=True, compress=False)
