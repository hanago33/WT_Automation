---
kind: design
name: 通过 Function Calling 的轻量级 NL-DSL Agent 集成编辑器
source: session
category: adr
---

# 通过 Function Calling 的轻量级 NL-DSL Agent 集成编辑器

_来源：26033ab → 0b3926e 提交周期内记录的编码计划——内容为规划时意图，实现可能滞后或有出入。_

**状态：** accepted

## 背景
需要在 WT_Flow_Editor 中引入自然语言到 DSL 步骤的转换能力，但要求执行时完全无感知、不修改现有 action schema 和 flow_definition.json 格式，且避免引入 LangChain 等重量级框架。

## 决策驱动
- 编辑期工作、执行期无感知
- 最小依赖（仅 requests）
- 保持现有 action schema 和 control 定义不变
- 用户自行管理 LLM 配置

## 备选方案
- **OpenAI-compatible Function Calling + 轻量 DslAgent** — 优点：无需额外框架、直接调用 /v1/chat/completions、tool 定义从 ACTION_SCHEMAS 自动生成、响应解析简单
- **LangChain/LangGraph 等重型框架** _（已否决）_ — 优点：生态丰富、有现成的 agent 模式；缺点：引入大量依赖、学习成本高、与项目最小依赖原则冲突

## 决策
在 wt_dsl_agent.py 中实现基于 OpenAI Function Calling 的 DslAgent，通过 build_action_schema_hint 自动从 ACTION_SCHEMAS 生成 tool 定义，使用 nl_to_step/nl_to_sequence 两个接口将自然语言转换为标准 step dict，并在 WT_Flow_Editor 中添加 NL 输入面板和 LLM 配置管理。

## 影响
新增 wt_dsl_agent.py、wt_control_index.py 和 tests/test_dsl_agent.py；WT_Flow_Editor 增加约 150 行 NL 相关代码；editor_state.json 扩展 LLM 配置字段；执行引擎完全不受影响，保持了编辑/执行解耦的架构。