# Excel 列映射约定（Excel Column Mapping Convention）

> 定义 Excel 参数表列头如何映射到 `flow_definition.json` 字段

## 用途

让 Agent 理解如何从 Excel 列头推导出对应的 `stepParams` 键名，
从而正确生成参数化步骤。

## 标准列映射表

| Excel 列头（中文） | Excel 列头（英文） | stepParams 键 | 用途 |
|-------------------|-------------------|---------------|------|
| 区域名称 | region_name | `regionName` | 计算域名称 |
| 经度 | lon / longitude | `lon` | 中心经度 |
| 纬度 | lat / latitude | `lat` | 中心纬度 |
| 网格分辨率 | grid_resolution | `gridResolution` | 网格间距(m) |
| 输出目录 | output_dir | `outputDir` | 结果输出路径 |
| 投影文件 | projection_file | `projectionFile` | .prj 文件路径 |
| 地形文件 | terrain_file | `terrainFile` | 地形数据路径 |
| 测风塔文件 | mast_file | `mastFile` | 测风塔数据路径 |
| 机位点文件 | turbine_positions_file | `turbinePositionsFile` | 机位坐标文件 |
| 风机厂商 | manufacturer | `manufacturer` | 风机厂商名 |
| 风机型号 | turbine_model | `turbineModel` | 风机型号 |
| 功率曲线文件 | power_curve_file | `powerCurveFile` | 功率曲线 CSV |
| 推力曲线文件 | thrust_curve_file | `thrustCurveFile` | 推力曲线 CSV |
| 风轮直径 | rotor_diameter | `rotorDiameter` | 风轮直径(m) |
| 轮毂高度 | hub_height | `hubHeight` | 轮毂高度(m) |
| 数据文件 | data_file / file_path | `filePath` | 通用数据文件路径 |
| 文件名 | file_name | `fileName` | 文件名（不含路径） |

## 在步骤中引用

Excel 列头 → `stepParams` 键 → 通过 `${stepParams.xxx}` 模板引用：

```json
{
  "stepParams": {
    "lat": "39.8",
    "lon": "116.5",
    "gridResolution": "20"
  },
  "actionConfig": {
    "action": "type_text",
    "targetValue": "纬度,Edit",
    "text": "${stepParams.lat}"
  }
}
```

## flow_excel_io.py 集成

步骤的 `stepParams` 对应 Excel 的 `stepParamsJson` 列（JSON 格式）。
导出时会将 `stepParams` 序列化写入 Excel；导入时反向回填。

## 流程包参数传递

当步骤引用流程包 (`flowRef`) 时：
- 当前步骤的 `stepParams` 会通过 `flowRefParamStack` 传递给子流程
- 子流程中可通过 `${flowRefParams.xxx}` 访问父流程传入的参数

## Agent 使用建议

1. 用户提供 Excel 时，按上表推导 stepParams 键名
2. 不在上表中的列头，用 snake_case 英文名作为键名
3. 参数值中若包含路径，推荐使用绝对路径（`${runtime.outputDir}/...` 模式）
4. 生成步骤时，`stepParams` 字段填入完整参数 JSON
