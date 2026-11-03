# 2026-11-03 改动还原 + 审计 + 修复 记录索引

本次工作：按用户指示还原 flow、保留板块顺序、新增作副本、独立审计"新增是否无害"、并按推荐方案修复 `_chain_block_if_invalid` 运行硬阻断、落盘留痕。

## 文件
| 文件 | 内容 |
|---|---|
| `01-flow还原与新增副本_记录.md` | flow 还原清单 + `_flow_backups_20261103/` 副本对照 |
| `02_审计三分类与核心裁决.md` | 三类别审计结论 + WT_Launcher 判据接线核心裁决 + 7 problem 明细 |
| `03_chain_block_if_invalid_修改记录.md` | 本次唯一代码修改：前后代码 + 完整验证证据 + 回滚法 |
| `04_回滚清单_全量.md` | 全部改动到任意受控状态的可回滚方案 |

## ⚡ 最终结论（2026-11-03 收束）
- **flow 最终使用新版（runtime 注入）**，HEAD 原版全部备份到 `_flow_originals_20261103/`。原因：经 diff 证实，除导入地形图外其余 flow 改动均为「输入值来源 硬编码路径 → ${runtime.*}」的正向改进，未改任何动作/控件/顺序；`validate_chain_manifest` 7 problem 全因此消除。
- **导入地形图单独决策 = 选项 A**：恢复 9 步纯地形链新版（原 47 步混气象链被拆），原版留 `_flow_originals/`。
- 板块顺序换位保留。
- 独立审计：`mup_assets/mup_data_files/WT_AUT_recorded/parser/generic_automation/wt_run_reporting` = 纯新增/等价重构，安全保留。
- `_chain_block_if_invalid` 已按方案 C 修改（致命阻断、契约警告）——但 flow 恢复后 manifest 0 problem，该告警路径当前不触发，修改是防御性降级。
- **全量测试通过（exit code 0）**，`test_input_contract_p0` 从 5 失败 → 全绿。
- 未决遗留：`generic_automation.py` 1084-1157 死代码块（丢 towers 统计，纯冗余，用户决定不改，记待办）。

## 待决策项
- `generic_automation.py` 死代码块（已决定不改，仅记录）。
- 全部改动未提交（git status 可见 8 改 + 大量 ??），等用户确认后再 commit。