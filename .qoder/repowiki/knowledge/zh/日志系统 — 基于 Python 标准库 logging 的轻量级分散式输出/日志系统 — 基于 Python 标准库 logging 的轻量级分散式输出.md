---
kind: logging_system
name: 日志系统 — 基于 Python 标准库 logging 的轻量级分散式输出
category: logging_system
scope:
    - '**'
source_files:
    - WT_AUTOMATION_Agent/agent.py
    - WT_AUTOMATION_Agent/cli.py
    - tools/external_capture/uiapeek_client.py
    - wt_dsl_agent.py
---

## 1. 使用的系统与框架
- 仅使用 Python 标准库 logging，未引入 loguru、structlog、sentry 等第三方日志框架。
- 无全局 logger 初始化模块，也未定义统一的 Handler/Formatter/Filter，属于散点式使用。

## 2. 关键文件与位置
- WT_AUTOMATION_Agent/agent.py：通过 logger = logging.getLogger(__name__) 创建模块级 logger，并在步骤校验失败时调用 logger.warning(...)
- WT_AUTOMATION_Agent/cli.py：在 --verbose 参数下调用 logging.basicConfig(level=logging.INFO) 做一次性根配置；其余 CLI 输出直接使用 print(...) / print(..., file=sys.stderr)
- tools/external_capture/uiapeek_client.py：为第三方 signalrcore HubConnectionBuilder 传入 configure_logging({"level": logging.DEBUG})，并 try/except 忽略配置异常
- wt_dsl_agent.py：作为向后兼容包装器，用 _logging.warning(...) 打印弃用提示
- skill_bridge.py：在动态 import 后局部 import logging，但未见实际 logger 调用（可能预留）

## 3. 架构与约定
- Logger 命名：遵循 logging.getLogger(__name__) 惯例，按模块划分 logger 实例
- 级别策略：CLI 层通过 --verbose 将根 logger 设为 INFO；业务代码中仅出现 warning 级别，未见 debug/info/error/exception 的系统性使用
- 输出目标：默认直接输出到 stderr（basicConfig 行为），没有文件轮转、JSON 结构化或集中收集机制
- 外部依赖隔离：对 signalrcore 的日志配置采用 try/except 包裹，避免第三方库破坏应用日志体系
- CLI 输出与日志混用：大量用户可见信息仍通过 print 输出，而非走 logger，导致 CLI 模式与 Agent 内部日志风格不一致

## 4. 开发者应遵循的规则
- 如需新增日志：优先使用 logger = logging.getLogger(__name__) + logger.debug/info/warning/error，避免再写 print
- 统一级别：调试信息用 debug，运行期告警用 warning，错误路径用 error，不要混用 print 代替日志
- 若需持久化日志（文件/JSON/远程收集），应在入口（如 cli.py 或总控台）集中配置 Handler/Formatter，而不是在各模块内重复 basicConfig
- 对第三方库（如 signalrcore）的日志配置要加 try/except 保护，防止其内部 API 变更影响主程序