---
kind: external_dependency
name: Robot Framework 测试与调度框架
slug: robot-framework
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
---

### Robot Framework 在流程编排中的集成

**角色定位**：作为高层流程编排和测试框架，提供可视化的测试用例管理和执行环境。

**集成模式**：
- Keyword 驱动：通过 `.resource` 文件定义业务关键词
- 流程编排：`.robot` 文件组织复杂的业务流程
- 结果报告：自动生成 HTML 格式的测试报告

**关键特性**：
- 支持并行执行多个流程
- 内置丰富的断言和日志功能
- 与 Python 代码无缝集成
- 提供 Web 风格的报告界面

**使用场景**：
- 端到端业务流程测试
- 定时任务调度
- 回归测试套件管理

verify exact API/params against official docs