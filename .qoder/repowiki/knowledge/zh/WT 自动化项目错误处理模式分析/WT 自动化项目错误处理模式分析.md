---
kind: error_handling
name: WT 自动化项目错误处理模式分析
category: error_handling
scope:
    - '**'
source_files:
    - WT_AUTOMATION_Agent/cli.py
    - WT_AUTOMATION_Agent/gui.py
    - WT_AUTOMATION_Agent/agent.py
    - WT_AUTOMATION_Agent/parameter_scan.py
    - WT_AUT_recorded.py
---

## 错误处理系统概述

该 WT_Automation 项目采用**混合式错误处理策略**，结合了 Python 标准异常、结构化日志记录和运行时报告机制，但没有统一的自定义错误类型体系。

## 主要错误处理模式

### 1. 标准异常传播（主流模式）
- **业务逻辑层**：广泛使用 `ValueError`、`FileNotFoundError`、`RuntimeError`、`NotADirectoryError` 等内置异常进行参数验证和状态检查
- **依赖缺失处理**：通过 `ImportError` 提供友好的安装提示，如 openpyxl 库缺失时给出明确的 pip install 指令
- **异常链式包装**：使用 `from exc` 保留原始异常上下文，便于调试追踪

### 2. 顶层异常捕获与清理
- **CLI 入口** (`cli.py`)：在 main() 函数中统一捕获所有异常，输出到 stderr 并返回非零退出码
- **GUI 服务器** (`gui.py`)：HTTP 请求处理器使用 try-except 包裹，将异常转换为 JSON 错误响应
- **自动化执行器** (`WT_AUT_recorded.py`)：run_automation() 函数作为全局异常安全网，确保运行报告正确关闭和资源清理

### 3. 结构化日志记录
- 使用 Python 标准 `logging` 模块，按模块创建独立 logger 实例
- 关键路径使用 `logger.warning()` 记录可恢复的错误条件
- 错误信息包含详细的上下文数据，便于问题诊断

### 4. 运行时错误报告
- 每个自动化运行生成独立的 JSON 报告文件，记录步骤执行状态和错误详情
- 支持成功/失败两种终态标记，错误时附带异常消息
- GUI 监控窗口实时显示错误状态并提供视觉反馈

## 架构设计决策

### 分层错误处理策略
- **底层函数**：抛出具体异常类型，保持 API 语义清晰
- **中间层**：选择性捕获并转换异常，添加业务上下文
- **顶层入口**：统一捕获、日志记录、资源清理和用户反馈

### 容错性设计
- 配置文件加载失败时回退到默认值而非中断程序
- HTTP 客户端连接断开时优雅降级而非崩溃
- 可选依赖缺失时提供清晰的安装指引

## 开发者应遵循的约定

1. **异常选择原则**：优先使用 Python 内置异常类型，避免创建自定义异常类
2. **错误信息格式**：使用中文描述错误原因，包含相关变量值和上下文信息
3. **异常链使用**：重新抛出异常时使用 `raise ... from exc` 保留调用栈
4. **日志级别规范**：使用 `warning` 记录可恢复错误，`error` 记录严重故障
5. **资源清理保证**：在 finally 块或 context manager 中确保资源释放
6. **用户友好提示**：向最终用户展示简洁的错误信息，详细堆栈仅用于开发调试