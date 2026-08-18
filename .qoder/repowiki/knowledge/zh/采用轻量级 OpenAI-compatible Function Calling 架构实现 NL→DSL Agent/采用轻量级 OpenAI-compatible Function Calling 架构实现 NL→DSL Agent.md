---
kind: design
name: 采用轻量级 OpenAI-compatible Function Calling 架构实现 NL→DSL Agent
source: session
category: adr
---

# 采用轻量级 OpenAI-compatible Function Calling 架构实现 NL→DSL Agent

_来源：0b3926e → d3bbc69 提交周期内记录的编码计划——内容为规划时意图，实现可能滞后或有出入。_

**状态：** accepted

## 背景
需要在编辑器中集成自然语言到 DSL 步骤的转换能力，但要求执行时完全无感知、不引入重量级框架（如 LangChain）、最小依赖、用户自管 LLM 配置。

## 决策驱动
- 零侵入性（编辑时工作，执行时无感知）
- 最小依赖（仅 requests）
- 用户自管配置（API key/base_url/model）
- 与现有 action schema 完全兼容

## 备选方案
- **OpenAI-compatible Function Calling + requests** — 优点：无需额外框架依赖；直接从 ACTION_SCHEMAS 自动生成 tool 定义；响应解析简单明确；可对接任意 OpenAI 兼容 API
- **LangChain/LlamaIndex 等重型框架** _（已否决）_ — 优点：功能丰富，生态完善；缺点：引入大量依赖；执行时需要运行时依赖；配置复杂
- **纯 prompt engineering 直接生成 JSON** _（已否决）_ — 优点：实现简单；缺点：稳定性差，难以保证输出格式正确；无法利用结构化 function calling 的优势

## 决策
创建独立的 `wt_dsl_agent.py` 模块，使用 `requests` 直接调用 OpenAI-compatible Chat Completion API (`POST /v1/chat/completions`)；从 `ACTION_SCHEMAS` 自动生成 Function Calling 工具定义；通过 `tool_calls` 响应解析提取结构化参数；新建 `wt_control_index.py` 构建控件库索引文本供 LLM 理解上下文；编辑器集成仅添加 UI 面板和配置管理，不修改任何执行引擎或业务逻辑。

## 影响
新增三个文件：核心 Agent 模块（~350行）、控件索引模块（~120行）、单元测试（~150行）；编辑器通过 `editor_state.json` 持久化 LLM 配置；转换后的步骤可直接保存为 `flow_definition.json` 并被现有执行引擎消费；需要维护 system prompt 模板和 few-shot 示例以保证转换质量。