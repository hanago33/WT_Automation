# WT Automation Project Architecture

## 当前模块分层
- `WT_AUT_recorded.py`: 执行入口、运行配置装配、总流程编排。
- `wt_flow_locator.py`: 控件定位、评分、窗口筛选、缓存。
- `wt_flow_executor.py`: action 执行、flow_ref 调度、fallback 执行。
- `wt_business_steps.py`: WT 业务步骤实现。
- `wt_projection_helpers.py`: 投影配置、模板识别、AI 提示词与图像兜底。
- `wt_window_helpers.py`: 主窗口激活、打开文件对话框、通用窗口辅助。
- `wt_run_reporting.py`: 结构化运行结果输出，生成 `logs/run_reports/*.json` 和 `logs/last_run_report.json`，并汇总执行数、失败数、fallback 次数与耗时。

## 编辑器相关
- `WT_Flow_Editor.py`: 可视化流程编辑器主界面。
- `wt_flow_editor_utils.py`: Inspect 解析、控件/步骤/运行参数归一化。
- `wt_action_schema.py`: action 字段 schema、必填规则、界面提示文案。
- `wt_flow_validation.py`: 步骤级和流程级统一校验。
- `wt_action_defaults.py`: 动作默认等待、超时配置。

## 转换器与控件资产
- `flow_recorder_converter.py`: recorder 语义转换、控件库匹配、运行参数抽取、待复核步骤标记。
- `build_control_map_library.py`: 控件库采集与保存。
- `build_image_template_library.py`: 模板制作与截图。
- `control_maps/`: 控件库定义。
- `image_templates/`: 模板图片。

## 推荐工作流
1. 先用控件采集器生成或维护 `control_maps`。
2. 在 `WT_Flow_Editor.py` 中新建步骤或导入 recorder 转换结果。
3. 优先使用可视化字段配置 action，不足时再补高级 JSON。
4. 保存前由统一校验器检查 `controlId`、flow_ref、流程包引用等一致性。
5. 运行后查看 `logs/last_run_report.json` 和时间戳报告，快速定位失败步骤、fallback 使用情况与总耗时。

## 近期新增能力
- 执行链路输出结构化运行报告，便于回归和定位失败步骤。
- 编辑器使用 action schema 驱动字段显隐和必填提示，减少手工试错。
- recorder 转换支持把明显的文件路径常量提升为运行参数占位，并标记待复核步骤。
- 已补最小回归测试，覆盖 action schema、校验器、编辑器工具和转换器参数抽取。
