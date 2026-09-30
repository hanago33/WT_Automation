---
kind: logging_system
name: WT 自动化日志系统：双轨日志与结构化运行报告
category: logging_system
scope:
    - '**'
source_files:
    - WT_AUTOMATION_Agent/agent.py
    - WT_AUTOMATION_Agent/cli.py
    - WT_AUTOMATION_Agent/log_diagnosis.py
    - WT_AUT_recorded.py
    - wt_run_reporting.py
    - tools/analyze_run_logs.py
    - WT_Launcher.py
---

## 1. 使用的系统与框架

本项目采用两套并行的日志体系：
- Python 标准库 logging：用于 Agent 模块（WT_AUTOMATION_Agent/agent.py、cli.py、skill_bridge.py）的开发期调试，通过 logging.getLogger(__name__) 获取 logger，使用 logger.exception() / logger.warning() 输出异常与警告。
- 自定义文件写入 + JSON 运行报告：核心执行器 WT_AUT_recorded.py 不使用 logging 框架，而是通过 log_step() 函数将时间戳+步骤名追加写入 wt_automation.log，同时由 wt_run_reporting.py 生成结构化的 JSON 运行报告（logs/run_reports/wt_run_YYYYMMDD_HHMMSS.json）。

CLI 入口通过 --verbose 参数按需调用 logging.basicConfig(level=logging.INFO) 启用控制台日志，默认不输出任何日志。

## 2. 关键文件与包
- WT_AUTOMATION_Agent/agent.py：Agent 核心逻辑，使用 logging.getLogger(__name__) 记录异常与警告。
- WT_AUTOMATION_Agent/cli.py：CLI 入口，仅在 --verbose 时初始化 basicConfig。
- WT_AUTOMATION_Agent/log_diagnosis.py：读取 wt_automation.log 或运行报告 JSON，构建诊断提示词供 LLM 分析失败根因。
- WT_AUT_recorded.py：流程执行器，定义 LOG_FILE = os.path.join(..., "wt_automation.log")，通过 log_step() 追加写入文本日志。
- wt_run_reporting.py：运行报告生成器，维护 logs/run_reports/ 目录，产出带 stepResults、summary、fallbackCount 等字段的 JSON 报告。
- tools/analyze_run_logs.py：只读分析工具，聚合多次运行报告，进行失败频次统计、错误签名聚类、慢步 TopN 等。
- WT_Launcher.py：GUI 启动器，提供“打开运行日志”菜单项直接打开 wt_automation.log。

## 3. 架构与约定
- 文本日志（wt_automation.log）：每行格式为 [YYYY-MM-DD HH:MM:SS] 步骤描述，按追加模式写入，无轮转机制。GUI 监视器窗口实时显示最新日志行。
- 结构化运行报告：每次执行通过 start_run_report() 创建报告，每个步骤通过 report_step_result() 记录 stepId、stepName、status（success/failed/skipped/fallback）、actionType、strategy、elapsedSeconds、error、extra 字段；执行结束通过 finalize_run_report() 写入 logs/run_reports/ 并同步更新 logs/last_run_report.json。
- 诊断链路：log_diagnosis.py 的 parse_run_log_file() 和 extract_failures() 分别解析文本日志与 JSON 报告，build_diagnosis_prompt() 将失败信息组织成三段式（失败定位→原因分析→修复建议）提示词，供 LLM 给出具体修复建议。
- 分析工具链：tools/analyze_run_logs.py 支持单次运行诊断（--run/--last）与多运行聚合（默认），提供失败频次 TopN、错误签名聚类、fallback 高频步识别、耗时热点分析等功能。

## 4. 约定与约束
- 日志级别策略：Agent 模块仅记录 exception 与 warning 级别；CLI 默认静默，需显式 --verbose 才启用 INFO 级别控制台输出。
- 日志文件位置固定：wt_automation.log 始终位于项目根目录，不可通过配置更改；运行报告统一写入 logs/run_reports/ 子目录。
- 报告字段规范：stepResults 中每条结果必须包含 stepId、stepName、status、elapsedSeconds、error 字段；summary 中维护 executedCount、successCount、failedCount、skippedCount、fallbackCount、totalElapsedSeconds 等聚合指标。
- Fallback 标记约定：当步骤通过降级模板成功时，需在 extra 中设置 fallbackTemplateUsed=true，并在 summary.fallbackCount 中累计计数，便于分析定位稳定性。
- 诊断输入约定：log_diagnosis.py 期望日志路径指向 wt_automation.log，报告路径指向 logs/run_reports/*.json 或 logs/last_run_report.json。
- 向后兼容包装：wt_dsl_agent.py 通过 _logging.warning() 输出弃用警告，引导新代码使用 WT_AUTOMATION_Agent 包。

该日志系统以轻量文本日志 + 丰富结构化报告的双轨设计，既满足开发调试需求，又为自动化测试与持续集成提供了可机器解析的运行数据。