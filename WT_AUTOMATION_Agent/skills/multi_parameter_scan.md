# 多参数扫描（Multi-Parameter Scan）

> 如何用 Excel 参数表驱动 WT_Automation 流程批量执行

## 概述

多参数扫描是一种**用一组 Excel 参数行取代逐个手动配置**的模式。
每行参数会驱动同一套业务流程运行一次，适用于：
- 批量创建多个 CFD 计算域（不同经纬度、不同网格参数）
- 批量导入多组测风塔数据（不同文件路径、不同坐标）
- 批量新建风机类型（不同厂商、不同功率曲线）

## 技术链路

```
Excel 参数表 (每行=一组参数)
    │
    ▼
parameter_scan.py 读取 → 生成 flow_definition.json
    │
    ▼
每个步骤的 stepParams 注入 ${stepParams.xxx}
    │
    ▼
WT_AUT_recorded.py 执行时 _resolve_dynamic_value 替换模板变量
```

## Excel 参数表格式

| region_name | lon | lat | grid_resolution | output_dir |
|-------------|-----|-----|-----------------|-------------|
| 风场A | 116.5 | 39.8 | 20 | D:\output\A |
| 风场B | 116.7 | 39.9 | 25 | D:\output\B |

- 第一行 = 表头（对应 `${stepParams.xxx}` 中的 `xxx`）
- 从第二行起每行 = 一套完整参数
- 支持多种数据源：`.xlsx`、`.csv`、`.tsv`、Excel 的 Sheet 指定

## 关键模板语法

在步骤的 `text` / `actionConfig.text` / `relativeRegion` 等字段中使用 `${...}` 动态引用：

```json
{
  "stepParams": {},
  "actionConfig": {
    "action": "type_text",
    "text": "${stepParams.lon}"
  }
}
```

支持的引用域：
- `${stepParams.xxx}` — 当前步骤的 stepParams（参数扫描核心）
- `${runtime.gmExe}` — 运行时配置
- `${flowRefParams.xxx}` — 流程包引用参数
- `${steps.step_xx.output}` — 前置步骤输出
- `${context.source_basename}` — 源文件名（不含路径）

## Agent 使用模式

用户说：*"帮我用 Excel 批量扫描3组参数，每组参数运行风机类型创建流程"*

Agent 应：
1. 调用 `parameter_scan` 工具读取 Excel
2. 为每行参数生成一组步骤（参数值注入 `${stepParams.xxx}`）
3. 生成的 flow_definition 每步的 `stepParams` 填入该行参数
4. 输出合并的 flow 定义供直接运行
