---
kind: configuration_system
name: WT 自动化 Agent 配置系统：多源分层配置与模型档案管理
category: configuration_system
scope:
    - '**'
source_files:
    - WT_AUTOMATION_Agent/agent.py
    - WT_AUTOMATION_Agent/model_profiles.py
    - WT_AUTOMATION_Agent/_gui_config.json
    - WT_AUTOMATION_Agent/model_profiles.json
    - WT_AUTOMATION_Agent/cli.py
---

该仓库的 WT_AUTOMATION_Agent 模块实现了一套完整的配置系统，用于管理 LLM API 连接、模型切换和运行时参数。配置采用多层级优先级加载机制，支持环境变量、JSON 配置文件和内置模型档案三种来源。

**核心架构**
- `DslAgentConfig` 数据类（agent.py）是配置的核心结构体，包含 base_url、api_key、model、timeout、max_tokens、temperature、重试策略等字段
- 配置优先级：构造函数参数 > 环境变量（WT_DSL_BASE_URL、WT_DSL_API_KEY、WT_DSL_MODEL）> model_profiles.json 默认档案
- 通过 `__post_init__` 钩子实现自动降级：当环境变量未设置时，自动从 model_profiles 加载默认配置

**配置文件格式**
- `model_profiles.json`：存储多个模型配置档案，每个档案包含 base_url、api_key、model、timeout、max_retries、retry_backoff、retry_codes 等字段
- `_gui_config.json`：GUI 界面使用的简化配置，作为遗留配置被迁移到新的 profiles 格式
- 支持通过 `migrate_from_legacy()` 函数自动将旧配置导入为新格式

**配置管理功能**
- `list_profiles()`：列出所有可用配置档案
- `save_profile()` / `delete_profile()`：增删配置档案
- `set_default()`：设置默认使用的模型配置
- `get_default()`：获取默认配置，供 CLI 和 GUI 使用
- 配置字段标准化：`_normalize()` 确保 timeout、max_retries、retry_backoff 等数值字段类型正确

**运行时行为**
- CLI 模式：优先检查环境变量，未配置时提示用户设置
- GUI 模式：通过 JSON 文件配置，支持动态切换不同模型
- HTTP 客户端：使用 requests.Session + urllib3 Retry 实现指数退避重试，禁用系统代理避免公司代理干扰
- Session 缓存：按 base_url 缓存连接，减少 TCP 握手开销

**约束与验证**
- `is_ready()` 方法确保 base_url 和 api_key 都已配置
- 配置值类型转换失败时提供默认值（如 timeout=120, max_retries=3）
- JSON 解析错误时静默处理，保证系统稳定性