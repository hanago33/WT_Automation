# AI辅助系统

<cite>
**本文引用的文件**   
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [WT_AUTOMATION_Agent/gui.py](file://WT_AUTOMATION_Agent/gui.py)
- [WT_AUTOMATION_Agent/schemas.py](file://WT_AUTOMATION_Agent/schemas.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [flow_recorder_converter.py](file://flow_recorder_converter.py)
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [tools/external_capture/pywinauto_backend.py](file://tools/external_capture/pywinauto_backend.py)
- [tools/external_capture/uiapeek_client.py](file://tools/external_capture/uiapeek_client.py)
- [resources/dispatch_keywords.resource](file://resources/dispatch_keywords.resource)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)
- [samples/recorder_scripts/Skill/GM_add1.py](file://samples/recorder_scripts/Skill/GM_add1.py)
</cite>

## 目录
1. [简介](#简介)
2. [项目结构](#项目结构)
3. [核心组件](#核心组件)
4. [架构总览](#架构总览)
5. [详细组件分析](#详细组件分析)
6. [依赖关系分析](#依赖关系分析)
7. [性能考虑](#性能考虑)
8. [故障排查指南](#故障排查指南)
9. [结论](#结论)
10. [附录](#附录)

## 简介
本文件面向WT自动化框架的AI辅助系统，系统性阐述Agent核心架构与工作原理、技能桥接扩展机制、CLI命令接口设计、DSL定义与解析流程、配置与自定义开发指南、自然语言到脚本的端到端示例、模型训练与微调方法，以及性能监控与优化策略。目标是帮助开发者快速理解并高效扩展AI能力，提升自动化效率与稳定性。

## 项目结构
WT自动化框架的AI辅助子系统主要位于WT_AUTOMATION_Agent目录，并与根目录下的DSL与录制转换模块协同工作；外部UI捕获与控件索引库为AI识别与定位提供基础数据支撑。

```mermaid
graph TB
subgraph "AI助手"
A["agent.py<br/>Agent主入口"]
B["cli.py<br/>命令行接口"]
C["skill_bridge.py<br/>技能桥接"]
D["control_index.py<br/>控件索引"]
E["gui.py<br/>图形界面"]
F["schemas.py<br/>数据模式"]
end
subgraph "DSL与录制"
G["wt_dsl_agent.py<br/>DSL解析与生成"]
H["flow_recorder_converter.py<br/>录制脚本转换"]
end
subgraph "外部捕获与控件库"
I["capture.py<br/>屏幕/窗口捕获"]
J["pywinauto_backend.py<br/>PyWinauto后端"]
K["uiapeek_client.py<br/>UIA客户端"]
L["standard_control_catalog.json<br/>标准控件目录"]
end
A --> C
A --> D
A --> F
B --> A
G --> A
H --> A
A --> I
I --> J
I --> K
A --> L
```

图表来源
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [WT_AUTOMATION_Agent/gui.py](file://WT_AUTOMATION_Agent/gui.py)
- [WT_AUTOMATION_Agent/schemas.py](file://WT_AUTOMATION_Agent/schemas.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [flow_recorder_converter.py](file://flow_recorder_converter.py)
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [tools/external_capture/pywinauto_backend.py](file://tools/external_capture/pywinauto_backend.py)
- [tools/external_capture/uiapeek_client.py](file://tools/external_capture/uiapeek_client.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)

章节来源
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [WT_AUTOMATION_Agent/gui.py](file://WT_AUTOMATION_Agent/gui.py)
- [WT_AUTOMATION_Agent/schemas.py](file://WT_AUTOMATION_Agent/schemas.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [flow_recorder_converter.py](file://flow_recorder_converter.py)
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [tools/external_capture/pywinauto_backend.py](file://tools/external_capture/pywinauto_backend.py)
- [tools/external_capture/uiapeek_client.py](file://tools/external_capture/uiapeek_client.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)

## 核心组件
- Agent主入口：负责接收用户意图（自然语言或DSL）、调度NLP与意图识别、调用技能桥接执行具体动作、生成并输出可执行的自动化脚本或流程步骤。
- CLI命令接口：提供命令行工具，支持从终端直接驱动AI助手完成需求描述、脚本生成与执行。
- 技能桥接系统：将AI生成的抽象操作映射到具体UI控件或业务API，支持动态注册新技能与工具。
- 控件索引与标准目录：维护UI控件的结构化索引与标准控件目录，辅助AI进行精准定位与自修复。
- DSL解析器：将领域特定语言转换为内部中间表示，再交由Agent编排执行。
- 录制转换器：将录制的操作步骤转换为标准化流程定义，供AI学习与复用。
- 外部捕获与后端：通过屏幕截图、UIA/Win32等后端获取UI状态，为AI提供感知输入。

章节来源
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [flow_recorder_converter.py](file://flow_recorder_converter.py)
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [tools/external_capture/pywinauto_backend.py](file://tools/external_capture/pywinauto_backend.py)
- [tools/external_capture/uiapeek_client.py](file://tools/external_capture/uiapeek_client.py)

## 架构总览
AI辅助系统的整体架构围绕“感知—理解—决策—执行—反馈”闭环构建：
- 感知层：通过外部捕获模块采集UI截图与控件树信息，结合控件索引与标准目录，形成结构化上下文。
- 理解层：对自然语言进行分词、语义抽取与意图分类，必要时借助DSL进行结构化表达。
- 决策层：基于意图与上下文，选择合适技能与工具，规划执行序列。
- 执行层：通过技能桥接调用具体UI操作或业务API，产出标准化流程步骤或脚本。
- 反馈层：记录执行结果、错误与日志，用于自修复与模型优化。

```mermaid
sequenceDiagram
participant User as "用户"
participant CLI as "CLI接口"
participant Agent as "Agent主入口"
participant NLP as "NLP与意图识别"
participant Bridge as "技能桥接"
participant Capture as "外部捕获"
participant Index as "控件索引/标准目录"
participant DSL as "DSL解析器"
participant Exec as "执行引擎"
User->>CLI : "提交自然语言需求"
CLI->>Agent : "解析参数并转发请求"
Agent->>NLP : "文本预处理与意图分类"
NLP-->>Agent : "意图+实体+约束"
Agent->>Index : "查询相关控件与上下文"
Index-->>Agent : "控件元数据"
Agent->>Capture : "获取当前UI快照"
Capture-->>Agent : "截图/控件树"
Agent->>Bridge : "选择并绑定技能"
Bridge-->>Agent : "可执行步骤列表"
Agent->>DSL : "生成/校验DSL中间表示"
DSL-->>Agent : "标准化流程"
Agent->>Exec : "执行流程并收集结果"
Exec-->>Agent : "执行报告"
Agent-->>CLI : "返回脚本/结果"
CLI-->>User : "展示输出与下一步建议"
```

图表来源
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)

## 详细组件分析

### Agent核心架构与工作原理
- 职责边界
  - 接收并规范化输入（自然语言、DSL片段、录制产物）。
  - 协调NLP与意图识别，提取关键实体与约束条件。
  - 结合控件索引与标准目录，进行上下文增强与定位预筛选。
  - 调用技能桥接，将抽象意图映射为具体UI操作或API调用。
  - 生成并校验DSL中间表示，确保可执行性与一致性。
  - 执行编排、错误处理与结果上报。
- 关键流程
  - 输入预处理：清洗、分句、实体抽取。
  - 意图分类：基于关键词与规则/模型进行分类。
  - 上下文构建：检索控件索引、加载标准目录、抓取UI快照。
  - 技能选择：根据意图与约束匹配技能模板。
  - 脚本生成：输出标准化步骤或脚本，支持回放与调试。
  - 执行与反馈：执行步骤、捕获异常、生成诊断信息。

```mermaid
classDiagram
class Agent {
+接收输入()
+预处理()
+意图识别()
+构建上下文()
+选择技能()
+生成DSL()
+执行编排()
+错误处理()
+输出结果()
}
class SkillBridge {
+注册技能()
+匹配技能()
+绑定参数()
+执行动作()
}
class ControlIndex {
+加载索引()
+检索控件()
+更新缓存()
}
class StandardCatalog {
+加载标准目录()
+匹配控件类型()
}
class Capture {
+截取屏幕()
+获取控件树()
}
class DSLParser {
+解析DSL()
+校验结构()
+生成IR()
}
Agent --> SkillBridge : "调用"
Agent --> ControlIndex : "查询"
Agent --> StandardCatalog : "参考"
Agent --> Capture : "感知"
Agent --> DSLParser : "生成/校验"
```

图表来源
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)

章节来源
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)

### 自然语言处理、意图识别与脚本生成机制
- 自然语言处理
  - 文本清洗与分句：去除噪声、统一标点、按业务语义切分。
  - 实体抽取：识别目标对象、字段名、数值、路径等关键实体。
  - 语义归一化：同义词映射、单位换算、范围规范化。
- 意图识别
  - 基于关键词资源与规则的分类器，结合轻量模型进行置信度评估。
  - 多意图融合：复杂需求拆分为子意图，形成执行序列。
- 脚本生成
  - 将意图与实体映射为标准化的流程步骤或DSL节点。
  - 生成前后校验：结构完整性、参数合法性、控件存在性检查。
  - 输出形式：可执行脚本、流程图定义、调试日志。

```mermaid
flowchart TD
Start(["开始"]) --> Clean["文本清洗与分句"]
Clean --> Extract["实体抽取与归一化"]
Extract --> Classify["意图分类与置信度评估"]
Classify --> Merge{"多意图?"}
Merge --> |是| Split["拆分子意图"]
Merge --> |否| Plan["规划执行序列"]
Split --> Plan
Plan --> Map["映射到技能与控件"]
Map --> Validate["结构与参数校验"]
Validate --> Output["生成脚本/DSL"]
Output --> End(["结束"])
```

图表来源
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [resources/dispatch_keywords.resource](file://resources/dispatch_keywords.resource)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)

章节来源
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [resources/dispatch_keywords.resource](file://resources/dispatch_keywords.resource)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)

### 技能桥接系统与扩展指南
- 设计要点
  - 技能注册：以声明式方式注册技能名称、触发条件、参数模式与执行函数。
  - 技能匹配：根据意图、实体与上下文选择最合适的技能。
  - 参数绑定：自动填充默认值、从控件索引推断、从标准目录补全。
  - 执行封装：统一异常处理、重试策略、结果格式化。
- 扩展新技能
  - 新增技能定义文件，包含元数据与实现函数。
  - 在桥接系统中注册技能，并提供测试用例验证。
  - 更新标准目录与控件索引，确保AI能正确定位目标控件。
- 工具集成
  - 将外部工具封装为技能，暴露统一接口。
  - 支持异步执行与超时控制，保证系统稳定性。

```mermaid
classDiagram
class SkillRegistry {
+注册(技能)
+查找(意图, 实体)
+执行(技能, 参数)
}
class SkillDefinition {
+名称
+触发条件
+参数模式
+执行函数
}
class ParameterBinder {
+推断默认值()
+从索引填充()
+从目录补全()
}
class Executor {
+调用技能()
+异常处理()
+重试策略()
+结果格式化()
}
SkillRegistry --> SkillDefinition : "管理"
SkillRegistry --> ParameterBinder : "绑定参数"
SkillRegistry --> Executor : "执行"
```

图表来源
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)

章节来源
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)

### CLI命令接口设计与使用
- 设计原则
  - 简洁直观：常用命令短小精悍，支持参数组合。
  - 幂等稳定：重复执行不产生副作用，失败可恢复。
  - 可观测：输出结构化日志与结果，便于自动化集成。
- 典型命令
  - 启动助手：进入交互模式，接受自然语言输入。
  - 生成脚本：根据需求描述生成标准化脚本或DSL。
  - 执行流程：运行已生成的流程并返回结果。
  - 调试模式：开启详细日志与断点，辅助问题定位。
- 使用示例
  - 通过命令行传入需求描述，输出可执行脚本路径与执行报告。
  - 结合环境变量切换不同模型或后端。

章节来源
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)

### DSL定义与解析机制
- DSL目标
  - 以结构化语言描述自动化流程，便于AI生成与人类编辑。
  - 支持条件分支、循环、参数化与错误处理。
- 解析流程
  - 语法解析：将DSL文本转换为AST。
  - 语义校验：检查节点类型、参数合法性与依赖关系。
  - IR生成：转换为内部表示，供执行引擎消费。
- 与Agent协作
  - Agent生成DSL片段，经解析器校验后纳入执行计划。
  - 录制转换器可将历史录制产物转为DSL，供AI学习。

```mermaid
flowchart TD
Parse["解析DSL文本"] --> AST["构建AST"]
AST --> Validate["语义校验"]
Validate --> IR["生成IR"]
IR --> Execute["执行引擎消费"]
```

图表来源
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [flow_recorder_converter.py](file://flow_recorder_converter.py)

章节来源
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [flow_recorder_converter.py](file://flow_recorder_converter.py)

### 外部捕获与控件定位
- 捕获策略
  - 屏幕截图：用于视觉匹配与漂移检测。
  - 控件树：通过UIA或Win32后端获取控件属性与层级。
- 后端选择
  - PyWinauto后端：适用于Win32/WPF控件。
  - UIA客户端：适用于现代UI框架与跨平台场景。
- 控件索引
  - 维护控件元数据与路径，加速AI定位与自修复。
  - 标准目录提供通用控件类型与行为约定。

章节来源
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [tools/external_capture/pywinauto_backend.py](file://tools/external_capture/pywinauto_backend.py)
- [tools/external_capture/uiapeek_client.py](file://tools/external_capture/uiapeek_client.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)

### GUI与可视化辅助
- 功能概述
  - 提供图形界面，便于非技术用户输入需求与查看结果。
  - 集成日志面板、控件预览与调试工具。
- 与CLI互补
  - GUI适合探索与演示，CLI适合批处理与CI集成。

章节来源
- [WT_AUTOMATION_Agent/gui.py](file://WT_AUTOMATION_Agent/gui.py)

### 数据模式与校验
- 模式定义
  - 使用统一的数据模式描述输入输出结构，确保一致性。
- 校验策略
  - 运行时校验：在执行前检查必填字段与类型。
  - 容错处理：缺失字段时采用默认值或提示补充。

章节来源
- [WT_AUTOMATION_Agent/schemas.py](file://WT_AUTOMATION_Agent/schemas.py)

## 依赖关系分析
AI辅助系统的关键依赖包括：
- 内部依赖
  - Agent依赖技能桥接、控件索引、DSL解析器与外部捕获。
  - CLI与GUI作为前端入口，调用Agent完成核心逻辑。
- 外部依赖
  - UIA/Win32后端用于控件树与截图。
  - 标准控件目录与关键词资源用于意图识别与控件匹配。
  - 录制转换器用于历史数据复用。

```mermaid
graph LR
CLI["CLI"] --> Agent["Agent"]
GUI["GUI"] --> Agent
Agent --> Bridge["技能桥接"]
Agent --> Index["控件索引"]
Agent --> DSL["DSL解析器"]
Agent --> Capture["外部捕获"]
Capture --> PyW["PyWinauto后端"]
Capture --> UIA["UIA客户端"]
Agent --> Catalog["标准控件目录"]
Agent --> Keywords["关键词资源"]
Agent --> Recorder["录制转换器"]
```

图表来源
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)
- [WT_AUTOMATION_Agent/gui.py](file://WT_AUTOMATION_Agent/gui.py)
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [tools/external_capture/pywinauto_backend.py](file://tools/external_capture/pywinauto_backend.py)
- [tools/external_capture/uiapeek_client.py](file://tools/external_capture/uiapeek_client.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)
- [resources/dispatch_keywords.resource](file://resources/dispatch_keywords.resource)
- [flow_recorder_converter.py](file://flow_recorder_converter.py)

章节来源
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)
- [WT_AUTOMATION_Agent/gui.py](file://WT_AUTOMATION_Agent/gui.py)
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [tools/external_capture/capture.py](file://tools/external_capture/capture.py)
- [tools/external_capture/pywinauto_backend.py](file://tools/external_capture/pywinauto_backend.py)
- [tools/external_capture/uiapeek_client.py](file://tools/external_capture/uiapeek_client.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)
- [resources/dispatch_keywords.resource](file://resources/dispatch_keywords.resource)
- [flow_recorder_converter.py](file://flow_recorder_converter.py)

## 性能考虑
- 缓存与索引
  - 控件索引与标准目录应定期更新并缓存，减少IO开销。
  - 截图与控件树缓存，避免重复采集。
- 并发与异步
  - 技能执行支持异步与超时控制，提高吞吐。
  - 批量任务采用队列与限流，防止资源争用。
- 模型推理优化
  - 使用本地轻量模型或缓存推理结果，降低延迟。
  - 对高频意图进行预计算与模板化。
- 监控与告警
  - 记录关键指标：响应时间、成功率、错误分布。
  - 设置阈值告警，及时发现问题。

[本节为通用指导，无需列出具体文件来源]

## 故障排查指南
- 常见问题
  - 控件定位失败：检查控件索引是否最新，确认标准目录覆盖目标控件类型。
  - 意图识别不准：扩充关键词资源与训练数据，调整分类阈值。
  - 执行异常：查看执行日志与断点信息，确认参数绑定是否正确。
- 调试技巧
  - 启用调试模式，输出详细日志与中间结果。
  - 使用录制转换器回放历史步骤，对比差异定位问题。
  - 通过GUI的控件预览与日志面板快速定位。

章节来源
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)
- [flow_recorder_converter.py](file://flow_recorder_converter.py)

## 结论
WT自动化框架的AI辅助系统以Agent为核心，整合NLP、意图识别、技能桥接、DSL解析与外部捕获，形成完整的“感知—理解—决策—执行—反馈”闭环。通过标准化的数据模式与可扩展的技能体系，系统能够灵活适配新的AI技能与工具集成。配合CLI与GUI双入口，既满足专业用户的深度定制，也兼顾普通用户的易用性。未来可通过持续优化模型与监控策略，进一步提升准确率与稳定性。

[本节为总结性内容，无需列出具体文件来源]

## 附录

### 实际使用示例：用自然语言描述自动化需求
- 示例场景
  - 打开某软件窗口，选择指定菜单项，填写表单并提交。
- 端到端流程
  - 用户在CLI或GUI中输入自然语言描述。
  - Agent进行NLP与意图识别，提取目标窗口、菜单项与表单字段。
  - 结合控件索引与标准目录，定位控件并生成DSL步骤。
  - 执行流程并返回结果与报告。
- 参考路径
  - 自然语言输入与输出：[CLI接口](file://WT_AUTOMATION_Agent/cli.py)
  - 意图识别与脚本生成：[Agent主入口](file://WT_AUTOMATION_Agent/agent.py)
  - DSL生成与校验：[DSL解析器](file://wt_dsl_agent.py)
  - 控件定位与索引：[控件索引](file://WT_AUTOMATION_Agent/control_index.py)、[标准控件目录](file://control_maps/standard_control_catalog.json)
  - 录制脚本参考：[GM_add1.py](file://samples/recorder_scripts/Skill/GM_add1.py)

章节来源
- [WT_AUTOMATION_Agent/cli.py](file://WT_AUTOMATION_Agent/cli.py)
- [WT_AUTOMATION_Agent/agent.py](file://WT_AUTOMATION_Agent/agent.py)
- [wt_dsl_agent.py](file://wt_dsl_agent.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)
- [samples/recorder_scripts/Skill/GM_add1.py](file://samples/recorder_scripts/Skill/GM_add1.py)

### AI模型的训练与微调方法
- 数据准备
  - 收集历史自然语言需求与对应脚本/DSL，构建指令-执行对。
  - 标注意图类别与实体，形成监督信号。
- 训练策略
  - 使用轻量模型进行意图分类与实体抽取的微调。
  - 引入领域词典与规则，提升鲁棒性。
- 评估与迭代
  - 以准确率、召回率与F1为指标评估模型效果。
  - 持续收集线上反馈，增量训练与版本管理。

[本节为通用指导，无需列出具体文件来源]

### 配置选项与自定义开发指南
- 配置项
  - 模型路径与参数：选择本地或远程模型，设置推理参数。
  - 技能注册表：声明式注册新技能与工具。
  - 控件索引与标准目录：维护路径与更新策略。
  - 日志与监控：开关详细日志，配置指标上报。
- 自定义开发
  - 新增技能：定义元数据与实现函数，注册到桥接系统。
  - 扩展NLP：添加领域词典与规则，优化意图分类。
  - 集成外部工具：封装为技能，提供统一接口与错误处理。

章节来源
- [WT_AUTOMATION_Agent/skill_bridge.py](file://WT_AUTOMATION_Agent/skill_bridge.py)
- [WT_AUTOMATION_Agent/control_index.py](file://WT_AUTOMATION_Agent/control_index.py)
- [control_maps/standard_control_catalog.json](file://control_maps/standard_control_catalog.json)
- [resources/dispatch_keywords.resource](file://resources/dispatch_keywords.resource)