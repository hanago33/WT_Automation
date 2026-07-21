# UI测试案例

<cite>
**本文档引用的文件**   
- [README.md](file://README.md)
- [WT_Automation.robot](file://WT_Automation.robot)
- [wt_flow_executor.py](file://wt_flow_executor.py)
- [wt_flow_locator.py](file://wt_flow_locator.py)
- [wt_window_helpers.py](file://wt_window_helpers.py)
- [wt_run_reporting.py](file://wt_run_reporting.py)
- [flow_definition_创建一个新建模.json](file://flow_packages/flow_definition_创建一个新建模.json)
- [flow_definition_导入测风塔、机位点.json](file://flow_packages/flow_definition_导入测风塔、机位点.json)
- [flow_definition_新建风机类型.json](file://flow_packages/flow_definition_新建风机类型.json)
- [test_wt_flow_executor.py](file://tests/test_wt_flow_executor.py)
- [test_ui_path_selector.py](file://tests/test_ui_path_selector.py)
- [test_self_healing_locator.py](file://tests/test_self_healing_locator.py)
- [test_wt_action_validation.py](file://tests/test_wt_action_validation.py)
- [dispatch_keywords.resource](file://resources/dispatch_keywords.resource)
- [project_config.resource](file://resources/project_config.resource)
- [deploy-website.yml](file://.github/workflows/deploy-website.yml)
- [启动WT自动化总控台.bat](file://启动WT自动化总控台.bat)
</cite>

## 目录
1. [简介](#简介)
2. [项目结构](#项目结构)
3. [核心组件](#核心组件)
4. [架构总览](#架构总览)
5. [详细组件分析](#详细组件分析)
6. [依赖关系分析](#依赖关系分析)
7. [性能与压力测试](#性能与压力测试)
8. [故障排查指南](#故障排查指南)
9. [结论](#结论)
10. [附录](#附录)

## 简介
本文件面向使用WT框架进行UI自动化测试的工程师，提供一套完整的测试案例文档。内容覆盖按钮点击、文本输入、下拉选择、表格操作等常见场景；给出用例组织与命名规范；介绍测试数据准备与管理方法；总结断言验证最佳实践与错误处理策略；说明如何集成到CI/CD流水线执行；并提供性能与压力测试方案以及测试报告生成与分析方法。

## 项目结构
仓库采用“流程定义 + 关键字资源 + 执行器 + 定位器 + 报告”的分层组织方式：
- 流程定义（JSON）：描述端到端业务流，包含窗口、控件、动作序列与参数。
- 关键字资源（Robot Framework .resource）：封装常用UI操作为可复用关键字。
- 执行器与定位器：解析流程定义、定位控件、驱动UI交互。
- 辅助工具：窗口管理、报告生成、外部捕获桥接等。
- 测试套件：针对执行器、定位器、校验逻辑的单元测试与集成测试。
- CI配置：GitHub Actions工作流用于网站部署（可作为CI参考）。

```mermaid
graph TB
subgraph "测试资产"
RF["WT_Automation.robot"]
RES1["dispatch_keywords.resource"]
RES2["project_config.resource"]
FLOW1["flow_definition_创建一个新建模.json"]
FLOW2["flow_definition_导入测风塔、机位点.json"]
FLOW3["flow_definition_新建风机类型.json"]
end
subgraph "运行时核心"
EXEC["wt_flow_executor.py"]
LOC["wt_flow_locator.py"]
WIN["wt_window_helpers.py"]
REP["wt_run_reporting.py"]
end
subgraph "测试与工具"
T1["tests/test_wt_flow_executor.py"]
T2["tests/test_ui_path_selector.py"]
T3["tests/test_self_healing_locator.py"]
BAT["启动WT自动化总控台.bat"]
CI[".github/workflows/deploy-website.yml"]
end
RF --> RES1
RF --> RES2
RF --> EXEC
EXEC --> LOC
EXEC --> WIN
EXEC --> REP
FLOW1 --> EXEC
FLOW2 --> EXEC
FLOW3 --> EXEC
T1 --> EXEC
T2 --> LOC
T3 --> LOC
BAT --> RF
CI --> |触发| RF
```

图表来源
- [WT_Automation.robot:1-200](file://WT_Automation.robot#L1-L200)
- [dispatch_keywords.resource:1-200](file://resources/dispatch_keywords.resource#L1-L200)
- [project_config.resource:1-200](file://resources/project_config.resource#L1-L200)
- [wt_flow_executor.py:1-200](file://wt_flow_executor.py#L1-L200)
- [wt_flow_locator.py:1-200](file://wt_flow_locator.py#L1-L200)
- [wt_window_helpers.py:1-200](file://wt_window_helpers.py#L1-L200)
- [wt_run_reporting.py:1-200](file://wt_run_reporting.py#L1-L200)
- [flow_definition_创建一个新建模.json:1-200](file://flow_packages/flow_definition_创建一个新建模.json#L1-L200)
- [flow_definition_导入测风塔、机位点.json:1-200](file://flow_packages/flow_definition_导入测风塔、机位点.json#L1-L200)
- [flow_definition_新建风机类型.json:1-200](file://flow_packages/flow_definition_新建风机类型.json#L1-L200)
- [test_wt_flow_executor.py:1-200](file://tests/test_wt_flow_executor.py#L1-L200)
- [test_ui_path_selector.py:1-200](file://tests/test_ui_path_selector.py#L1-L200)
- [test_self_healing_locator.py:1-200](file://tests/test_self_healing_locator.py#L1-L200)
- [启动WT自动化总控台.bat:1-200](file://启动WT自动化总控台.bat#L1-L200)
- [deploy-website.yml:1-200](file://.github/workflows/deploy-website.yml#L1-L200)

章节来源
- [README.md:1-200](file://README.md#L1-L200)
- [WT_Automation.robot:1-200](file://WT_Automation.robot#L1-L200)
- [dispatch_keywords.resource:1-200](file://resources/dispatch_keywords.resource#L1-L200)
- [project_config.resource:1-200](file://resources/project_config.resource#L1-L200)
- [wt_flow_executor.py:1-200](file://wt_flow_executor.py#L1-L200)
- [wt_flow_locator.py:1-200](file://wt_flow_locator.py#L1-L200)
- [wt_window_helpers.py:1-200](file://wt_window_helpers.py#L1-L200)
- [wt_run_reporting.py:1-200](file://wt_run_reporting.py#L1-L200)
- [flow_definition_创建一个新建模.json:1-200](file://flow_packages/flow_definition_创建一个新建模.json#L1-L200)
- [flow_definition_导入测风塔、机位点.json:1-200](file://flow_packages/flow_definition_导入测风塔、机位点.json#L1-L200)
- [flow_definition_新建风机类型.json:1-200](file://flow_packages/flow_definition_新建风机类型.json#L1-L200)
- [test_wt_flow_executor.py:1-200](file://tests/test_wt_flow_executor.py#L1-L200)
- [test_ui_path_selector.py:1-200](file://tests/test_ui_path_selector.py#L1-L200)
- [test_self_healing_locator.py:1-200](file://tests/test_self_healing_locator.py#L1-L200)
- [启动WT自动化总控台.bat:1-200](file://启动WT自动化总控台.bat#L1-L200)
- [deploy-website.yml:1-200](file://.github/workflows/deploy-website.yml#L1-L200)

## 核心组件
- 流程执行器：负责加载流程定义、解析步骤、调度定位器与窗口助手、执行UI动作并收集结果。
- 控件定位器：基于路径、属性、相对区域与图像模板等多策略定位目标控件。
- 窗口助手：管理窗口发现、激活、等待就绪、句柄与进程生命周期。
- 报告模块：汇总执行结果、截图、日志与指标，输出可读报告。
- 关键字资源：将复杂操作封装为关键字，供RF脚本直接调用。
- 流程定义：以JSON描述端到端业务流，便于维护与复用。

章节来源
- [wt_flow_executor.py:1-200](file://wt_flow_executor.py#L1-L200)
- [wt_flow_locator.py:1-200](file://wt_flow_locator.py#L1-L200)
- [wt_window_helpers.py:1-200](file://wt_window_helpers.py#L1-L200)
- [wt_run_reporting.py:1-200](file://wt_run_reporting.py#L1-L200)
- [dispatch_keywords.resource:1-200](file://resources/dispatch_keywords.resource#L1-L200)
- [flow_definition_创建一个新建模.json:1-200](file://flow_packages/flow_definition_创建一个新建模.json#L1-L200)

## 架构总览
下图展示从RF脚本到执行器、定位器、窗口助手与报告的调用链，以及流程定义的注入路径。

```mermaid
sequenceDiagram
participant RF as "WT_Automation.robot"
participant KEY as "关键字资源"
participant EXEC as "流程执行器"
participant LOC as "控件定位器"
participant WIN as "窗口助手"
participant REP as "报告模块"
participant FLOW as "流程定义(JSON)"
RF->>KEY : 调用业务关键字
KEY->>EXEC : 传入步骤/参数
EXEC->>FLOW : 读取流程定义
loop 遍历步骤
EXEC->>LOC : 定位控件(路径/属性/图像)
LOC-->>EXEC : 返回控件句柄
EXEC->>WIN : 确保窗口可见/就绪
EXEC->>EXEC : 执行动作(点击/输入/选择/表格)
EXEC->>REP : 记录结果/截图/日志
end
EXEC-->>KEY : 返回执行状态
KEY-->>RF : 断言与下一步
```

图表来源
- [WT_Automation.robot:1-200](file://WT_Automation.robot#L1-L200)
- [dispatch_keywords.resource:1-200](file://resources/dispatch_keywords.resource#L1-L200)
- [wt_flow_executor.py:1-200](file://wt_flow_executor.py#L1-L200)
- [wt_flow_locator.py:1-200](file://wt_flow_locator.py#L1-L200)
- [wt_window_helpers.py:1-200](file://wt_window_helpers.py#L1-L200)
- [wt_run_reporting.py:1-200](file://wt_run_reporting.py#L1-L200)
- [flow_definition_创建一个新建模.json:1-200](file://flow_packages/flow_definition_创建一个新建模.json#L1-L200)

## 详细组件分析

### 流程执行器（wt_flow_executor.py）
职责
- 解析流程定义，按序执行步骤。
- 协调定位器与窗口助手完成UI交互。
- 聚合执行结果，驱动报告生成。

关键流程
- 初始化：加载配置、注册动作处理器、准备报告上下文。
- 执行循环：读取步骤、定位控件、执行动作、断言条件、记录指标。
- 异常处理：捕获UI异常、重试策略、失败截图与堆栈。

```mermaid
flowchart TD
Start(["开始"]) --> LoadFlow["加载流程定义"]
LoadFlow --> InitCtx["初始化执行上下文"]
InitCtx --> Loop{"是否还有步骤?"}
Loop --> |是| Resolve["解析步骤参数"]
Resolve --> Locate["定位控件"]
Locate --> EnsureWin["确保窗口就绪"]
EnsureWin --> Act["执行动作"]
Act --> AssertStep["断言/校验"]
AssertStep --> Record["记录结果/截图/日志"]
Record --> Loop
Loop --> |否| Report["生成报告"]
Report --> End(["结束"])
```

图表来源
- [wt_flow_executor.py:1-200](file://wt_flow_executor.py#L1-L200)

章节来源
- [wt_flow_executor.py:1-200](file://wt_flow_executor.py#L1-L200)
- [test_wt_flow_executor.py:1-200](file://tests/test_wt_flow_executor.py#L1-L200)

### 控件定位器（wt_flow_locator.py）
能力
- 多策略定位：UI路径、属性匹配、相对区域、图像模板。
- 自愈式定位：在轻微界面漂移时通过相似度与容错策略恢复定位。
- 缓存与索引：对稳定控件建立索引以提升性能。

典型用法
- 通过路径或属性快速定位按钮、输入框、下拉项。
- 结合相对区域定位动态列表中的某一行。
- 使用图像模板识别图标类控件。

```mermaid
classDiagram
class Locator {
+locate_by_path(path)
+locate_by_attrs(attrs)
+locate_relative(region, offset)
+locate_by_image(template)
+heal_if_needed(control)
}
class WindowHelper {
+find_window(title)
+wait_ready(timeout)
+activate()
}
Locator --> WindowHelper : "依赖窗口上下文"
```

图表来源
- [wt_flow_locator.py:1-200](file://wt_flow_locator.py#L1-L200)
- [wt_window_helpers.py:1-200](file://wt_window_helpers.py#L1-L200)

章节来源
- [wt_flow_locator.py:1-200](file://wt_flow_locator.py#L1-L200)
- [test_ui_path_selector.py:1-200](file://tests/test_ui_path_selector.py#L1-L200)
- [test_self_healing_locator.py:1-200](file://tests/test_self_healing_locator.py#L1-L200)
- [wt_window_helpers.py:1-200](file://wt_window_helpers.py#L1-L200)

### 关键字资源（dispatch_keywords.resource / project_config.resource）
作用
- 将复杂UI操作封装为关键字，如“点击按钮”、“输入文本”、“选择下拉项”、“操作表格”。
- 集中管理项目级配置，如超时、重试次数、截图路径等。

建议关键字设计
- 统一入参：窗口标题、控件标识、动作类型、参数字典。
- 统一出参：布尔成功标志、附加信息（如选中值、行号）。
- 内置断言：可选断言模式，减少用例层重复代码。

章节来源
- [dispatch_keywords.resource:1-200](file://resources/dispatch_keywords.resource#L1-L200)
- [project_config.resource:1-200](file://resources/project_config.resource#L1-L200)

### 流程定义（flow_definition_*.json）
结构要点
- 元数据：名称、版本、适用环境。
- 步骤数组：每个步骤包含动作类型、目标控件、参数、预期结果。
- 数据绑定：支持变量替换与外部数据源引用。

示例用途
- 创建新模型：打开窗口、填写表单、保存并验证提示。
- 导入数据：选择文件、确认导入、检查进度与结果。
- 新建风机类型：选择类型、设置曲线、提交并校验。

章节来源
- [flow_definition_创建一个新建模.json:1-200](file://flow_packages/flow_definition_创建一个新建模.json#L1-L200)
- [flow_definition_导入测风塔、机位点.json:1-200](file://flow_packages/flow_definition_导入测风塔、机位点.json#L1-L200)
- [flow_definition_新建风机类型.json:1-200](file://flow_packages/flow_definition_新建风机类型.json#L1-L200)

### 报告模块（wt_run_reporting.py）
功能
- 汇总用例/步骤级别的结果、耗时、截图与日志。
- 生成结构化报告（HTML/JSON），便于分析与归档。
- 支持失败重跑标记与趋势统计。

章节来源
- [wt_run_reporting.py:1-200](file://wt_run_reporting.py#L1-L200)

## 依赖关系分析
- 低耦合：执行器仅依赖定位器与窗口助手的接口，不关心具体UI实现细节。
- 高内聚：关键字资源聚焦于UI操作封装，提升复用性。
- 外部依赖：流程定义作为数据驱动源，便于非编码人员维护。

```mermaid
graph LR
RF["WT_Automation.robot"] --> KEY["关键字资源"]
KEY --> EXEC["流程执行器"]
EXEC --> LOC["控件定位器"]
EXEC --> WIN["窗口助手"]
EXEC --> REP["报告模块"]
FLOW["流程定义(JSON)"] --> EXEC
```

图表来源
- [WT_Automation.robot:1-200](file://WT_Automation.robot#L1-L200)
- [dispatch_keywords.resource:1-200](file://resources/dispatch_keywords.resource#L1-L200)
- [wt_flow_executor.py:1-200](file://wt_flow_executor.py#L1-L200)
- [wt_flow_locator.py:1-200](file://wt_flow_locator.py#L1-L200)
- [wt_window_helpers.py:1-200](file://wt_window_helpers.py#L1-L200)
- [wt_run_reporting.py:1-200](file://wt_run_reporting.py#L1-L200)
- [flow_definition_创建一个新建模.json:1-200](file://flow_packages/flow_definition_创建一个新建模.json#L1-L200)

章节来源
- [wt_flow_executor.py:1-200](file://wt_flow_executor.py#L1-L200)
- [wt_flow_locator.py:1-200](file://wt_flow_locator.py#L1-L200)
- [wt_window_helpers.py:1-200](file://wt_window_helpers.py#L1-L200)
- [wt_run_reporting.py:1-200](file://wt_run_reporting.py#L1-L200)

## 性能与压力测试
- 批量执行：通过并行运行多个流程定义或RF套件，评估吞吐与稳定性。
- 长稳测试：在固定时间窗口内循环执行关键路径，监控内存与CPU占用。
- 压测指标：平均响应时间、P95/P99延迟、失败率、截图数量与大小。
- 优化建议：启用控件索引、减少图像匹配频率、合理设置超时与重试。

[本节为通用指导，无需特定文件来源]

## 故障排查指南
常见问题与对策
- 控件未找到：检查路径/属性是否正确，必要时启用自愈定位或图像模板。
- 窗口未就绪：增加等待时间或改用“等待就绪”关键字。
- 断言失败：查看报告中的截图与日志，定位差异原因。
- 偶发失败：引入重试机制与幂等清理步骤。

章节来源
- [test_wt_action_validation.py:1-200](file://tests/test_wt_action_validation.py#L1-L200)
- [wt_run_reporting.py:1-200](file://wt_run_reporting.py#L1-L200)

## 结论
WT框架通过“流程定义 + 关键字资源 + 执行器 + 定位器 + 报告”的分层架构，提供了稳定、可维护且可扩展的UI自动化测试能力。借助数据驱动与自愈定位，能够高效覆盖按钮、输入、下拉、表格等常见场景，并易于集成至CI/CD流水线，持续保障质量。

[本节为总结性内容，无需特定文件来源]

## 附录

### 测试用例组织与命名规范
- 用例分组：按业务域划分（如“模型创建”、“数据导入”、“类型管理”）。
- 命名约定：动词+名词+场景，例如“创建新模型_正常路径”、“导入数据_大文件”。
- 步骤粒度：每个原子UI操作对应一个步骤，便于定位与重放。
- 数据隔离：使用独立数据目录与临时文件，避免相互影响。

[本节为通用指导，无需特定文件来源]

### 测试数据准备与管理
- 静态数据：放在资源目录，按场景分文件夹。
- 动态数据：在流程中生成或从上游系统获取，并在结束后清理。
- 数据校验：在报告中附带关键数据快照，便于回溯。

[本节为通用指导，无需特定文件来源]

### 断言验证最佳实践
- 显式断言：对关键状态进行断言（如对话框出现、字段值更新）。
- 复合断言：组合多项条件，提高鲁棒性。
- 失败快照：自动截取失败画面，缩短排障时间。

[本节为通用指导，无需特定文件来源]

### 错误处理策略
- 重试与退避：对不稳定操作设置指数退避重试。
- 降级策略：当主定位失败时回退到图像匹配或相对区域定位。
- 清理与恢复：无论成功失败都执行必要的清理步骤。

[本节为通用指导，无需特定文件来源]

### 集成到CI/CD流水线
- 本地执行：使用批处理脚本一键启动总控台并运行RF套件。
- 云端执行：在GitHub Actions中安装依赖、拉取代码、执行测试并上传报告。
- 通知与归档：失败时发送通知，归档报告与截图。

章节来源
- [启动WT自动化总控台.bat:1-200](file://启动WT自动化总控台.bat#L1-L200)
- [deploy-website.yml:1-200](file://.github/workflows/deploy-website.yml#L1-L200)

### 测试报告生成与分析
- 生成：执行完成后自动生成HTML/JSON报告。
- 分析：关注失败用例、耗时分布、截图差异。
- 趋势：对比历史报告，观察回归与改进。

章节来源
- [wt_run_reporting.py:1-200](file://wt_run_reporting.py#L1-L200)