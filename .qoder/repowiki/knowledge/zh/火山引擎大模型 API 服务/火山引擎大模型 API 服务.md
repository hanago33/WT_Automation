---
kind: external_dependency
name: 火山引擎大模型 API 服务
slug: volcengine
category: external_dependency
category_hints:
    - vendor_identity
    - auth_protocol
scope:
    - '**'
---

### 火山引擎 LLM 服务集成

**角色定位**：为自然语言处理和 AI 助手功能提供大模型推理能力，支持 NL→DSL 转换、智能对话等功能。

**集成方式**：
- 环境变量配置：通过 `VOLC_API_KEY` 设置访问密钥
- OpenAI 兼容协议：使用标准的 chat completion API 接口
- 多模型支持：可配置不同的模型名称和参数

**主要用途**：
- 自然语言指令解析：将中文描述转换为结构化流程步骤
- AI 助手对话：提供智能问答和流程构建辅助
- 文本生成：自动生成测试数据、文档等

**配置要点**：
- base_url 指向火山引擎的 OpenAI 兼容端点
- model 参数指定具体使用的模型版本
- temperature、max_tokens 等生成参数可调

verify exact API/params against official docs