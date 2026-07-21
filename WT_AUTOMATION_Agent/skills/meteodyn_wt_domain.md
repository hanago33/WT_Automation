# Meteodyn WT 领域知识（Domain Knowledge Bridge）

> 桥接 `.codebuddy/skills/meteodyn-wt-knowledge/` 中的详细知识，提供 Agent 可直接使用的精简版

## WT_Automation 系统架构

六层架构：
1. **执行入口层** (`WT_AUT_recorded.py`) — 流程调度、动态值解析 `${...}`
2. **控件定位层** (`wt_flow_locator.py`) — 多属性评分、三级兜底链（结构化→模板→AI）
3. **动作执行层** (`wt_flow_executor.py`) — 14 种 action 类型
4. **业务步骤层** (`wt_business_steps.py`) — 高层业务封装
5. **辅助层** (projection/template/window helpers)
6. **报告层** (`wt_run_reporting.py`) — 结构化运行报告

三级兜底链：结构化定位 → 模板匹配（`image_templates/`）→ AI 视觉模型

## 控件定位核心公式

评分公式：`score = automationId × w1 + name × w2 + class_name × w3 + control_type × w4 + runtime_id × w5`
- 权重递减：优先 precision 高的属性

父窗口相对区域：
```
click_x = parent_window.x + (parent_window.width  * regionX) + anchor_offset_x
click_y = parent_window.y + (parent_window.height * regionY) + anchor_offset_y
```

## Meteodyn WT 工作流

1. **数据导入**：地理数据（SRTM/ESA/NLCD，支持 ASC/MAP/XYZ/CHM/SHP/TIFF 格式）
2. **项目定义**：计算域、投影设置、网格生成
3. **CFD 计算**：Migal-S 求解器，收敛阈值：主风向 ≥98%、非主风向 ≥95%
4. **综合 (Synthesis)**：AEP 计算、Weibull 拟合（3种方法）、湍流修正、极端风速
5. **布局优化**：目标函数最大化 AEP，停止规则（迭代收敛），分组、固定位置、REWS 近似
6. **结果输出**：报表、图表、GIS 导出

## 关键参数与阈值

| 参数 | 推荐值 | 说明 |
|------|--------|------|
| CFD 收敛率 | 主风向 ≥98%，非主风向 ≥95% | 低于此值需重新计算 |
| 大气稳定性 | 0-9 (3L 模型) | 0=极不稳定, 可参考稳态最大轮毂高度风速频率占比的均值 |
| 尾流模型 | LWF + Park/EVM | 推力系数耦合 |
| 网格分辨率 | 20-25m | 复杂地形细化到 10m |
| Weibull 拟合 | 3 种方法备选 | 根据数据质量选择 |
| 整体范围（计算域） | R = R1 + R2（R1≥0km， R2 min(0.6D, 0.5R1)km） | 覆盖≥3×风轮直径 |

## 常见文件格式

- 地形：`.asc`（Arc/Info ASCII Grid）、`.map`（CFDTool 网格格式）、`.tif`（Global Mapper）
- 气象：`.txt`（统计/时间序列）、`.tab`
- 风机：功率/推力曲线 CSV
