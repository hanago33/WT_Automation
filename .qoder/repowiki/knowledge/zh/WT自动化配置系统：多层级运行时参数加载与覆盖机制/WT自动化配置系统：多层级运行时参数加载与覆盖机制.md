---
kind: configuration_system
name: WT自动化配置系统：多层级运行时参数加载与覆盖机制
category: configuration_system
scope:
    - '**'
source_files:
    - resources/project_config.resource
    - flow_definition.json
    - WT_AUT_recorded.py
    - WT_Launcher.py
    - WT_Flow_Editor.py
    - flow_packages/flow_package_registry.json
    - editor_state.json
    - WT_AUTOMATION_Agent/_gui_config.json
    - resources/dispatch_keywords.resource
---

## 配置系统概述

WT_Automation项目采用**多层级配置加载系统**，支持从多个来源动态加载和合并运行时配置。该系统没有使用传统的配置文件框架（如configparser、pydantic-settings等），而是通过自定义的Python逻辑实现灵活的配置优先级管理。

## 配置层级结构

### 1. 项目级配置（Project Settings）
- **文件位置**: `resources/project_config.resource`
- **格式**: Robot Framework资源文件格式，使用`${KEY} value`语法
- **内容**: 包含AI服务配置、路径设置、超时参数等基础配置
- **加载函数**: `_load_project_settings()` 和 `load_project_settings()`

### 2. 流程定义配置（Flow Definition）
- **主文件**: `flow_definition.json`
- **注册表**: `flow_packages/flow_package_registry.json`
- **格式**: JSON结构，包含`runtimeConfig`、`flowPackages`、`steps`等字段
- **特性**: 支持流程包引用和步骤复用

### 3. 环境变量配置（Environment Variables）
- **关键变量**:
  - `GM_RUNTIME_CONFIG_JSON`: JSON格式的运行时配置覆盖
  - `WT_FLOW_DEFINITION_FILE`: 指定流程定义文件路径
  - `WT_ENABLE_AI_INTERVENTION`: AI介入开关
  - `WT_DSL_BASE_URL`, `WT_DSL_API_KEY`, `WT_DSL_MODEL`: DSL Agent配置

### 4. 编辑器状态配置（Editor State）
- **文件**: `editor_state.json`
- **用途**: 保存编辑器的用户偏好和临时状态

## 配置加载优先级

系统实现了明确的配置优先级机制：

```python
# 优先级顺序（从高到低）
1. 环境变量 (GM_RUNTIME_CONFIG_JSON)
2. 流程定义文件 (flow_definition.json)
3. 项目配置 (project_config.resource)
4. 硬编码默认值
```

### 核心加载逻辑
在`WT_AUT_recorded.py`中的`_load_runtime_config()`函数实现了完整的配置合并：

```python
def _pick_value(key, legacy_key, default_value):
    for source in (env_runtime, flow_runtime):  # 环境变量 > 流程定义
        value = source.get(key)
        if value not in (None, ""):
            return str(value).strip()
    value = project_settings.get(legacy_key, "")  # 项目配置
    if value not in (None, ""):
        return str(value).strip()
    return str(default_value).strip()  # 默认值
```

## 关键配置项

### 运行时配置（runtimeConfig）
- `gmExe`: Global Mapper可执行文件路径
- `sourceFilePath`: 源数据文件路径
- `outputDir`: 输出目录
- `projectionFilePath`: 投影文件路径

### AI服务配置
- `PROVIDER`: AI服务提供商（volcengine）
- `MODEL_NAME`: 模型名称
- `UI_TARS_VLM_BASE_URL`: VLM服务地址
- `VOLC_API_KEY`: API密钥

### UI-TARS集成配置
- `UI_TARS_REPO_ROOT`: UI-TARS仓库根目录
- `UI_TARS_CLI_CONFIG`: CLI配置文件路径
- `UI_TARS_RUNNER`: 执行器脚本路径

## 配置验证与容错

### 配置校验
- 使用`validate_flow_definition()`函数对流程定义进行结构化验证
- 支持缺失配置时的降级处理
- 提供详细的错误信息和回退机制

### 缓存机制
- 使用`@lru_cache(maxsize=1)`装饰器缓存配置加载结果
- 支持运行时刷新配置的`_refresh_flow_caches()`函数

## 扩展性设计

### 动态配置解析
系统支持在配置中使用表达式语言：
- `${runtime.sourceFilePath}`: 引用运行时配置
- `${stepParams.xxx}`: 引用步骤参数
- `${context.xxx}`: 引用执行上下文

### 多环境支持
通过环境变量实现不同环境的配置切换，无需修改代码或配置文件。

## 最佳实践建议

1. **敏感信息**: 使用环境变量存储API密钥等敏感配置
2. **环境隔离**: 通过`GM_RUNTIME_CONFIG_JSON`实现不同部署环境的配置隔离
3. **版本控制**: 将`flow_definition.json`纳入版本控制，但排除敏感配置
4. **配置文档化**: 在`project_config.resource`中添加注释说明各配置项用途
5. **错误处理**: 利用系统的容错机制，确保单点配置失败不影响整体运行