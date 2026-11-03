# flow 流程 JSON 还原记录（2026-11-03）

## 背景
用户指示（2026-11-03）：
> 所有flow 流程JSON都还原 + 新增作副本，板块顺序不用还原，其余新增保留（新增本来就无害，无害但需要你去真正核实是否无害）

本记录覆盖「flow 还原 + 新版另存副本」部分。

## 已执行操作

### 1. 还原到 HEAD 原版（git checkout）
执行 `git checkout HEAD --` 还原以下 flow 到 HEAD 跟踪原版：

| flow 文件 | 还原前状态 | 还原后 |
|---|---|---|
| flow_packages/flow_definition_创建一个新建模.json | 改版（-line/小篇幅） | HEAD 原版 |
| flow_packages/flow_definition_发送CFD计算.json | 改版 | HEAD 原版 |
| flow_packages/flow_definition_发送综合计算.json | 改版 | HEAD 原版 |
| flow_packages/flow_definition_导入地形图.json | **-3255 行（85% 删除，679 行）** | **HEAD 原版 3830 行 / 126KB** |
| flow_packages/flow_definition_导入地形图_录制结果.json | 改版 | HEAD 原版 |
| flow_packages/flow_definition_导入并配置元素.json | 改版 | HEAD 原版 |
| flow_packages/flow_definition_新建气象数据_内网_配置导入.json | 改版 | HEAD 原版 |
| flow_packages/flow_definition_新建气象数据_外网_配置导入.json | 改版 | HEAD 原版 |
| flow_packages/flow_definition_新建风机类型.json | 改版 | HEAD 原版 |
| flow_packages/param_table_发送综合计算.json | 改版 | HEAD 原版 |

**验证**：还原后 `git status flow_packages/` 无跟踪改动；`flow_definition_导入地形图.json` 恢复 3830 行。✅

### 2. 新版另存为副本（不覆盖原名）
当前改版全部备份到 `_flow_backups_20261103/`，文件名加 `_WAVE-NEW` 后缀（10 个）：

```
_flow_backups_20261103/flow_definition_创建一个新建模_WAVE-NEW.json   (148795 B)
_flow_backups_20261103/flow_definition_发送CFD计算_WAVE-NEW.json      (79399 B)
_flow_backups_20261103/flow_definition_发送综合计算_WAVE-NEW.json     (335174 B)
_flow_backups_20261103/flow_definition_导入地形图_WAVE-NEW.json       (27489 B)
_flow_backups_20261103/flow_definition_导入地形图_录制结果_WAVE-NEW.json (22406 B)
_flow_backups_20261103/flow_definition_导入并配置元素_WAVE-NEW.json   (159533 B)
_flow_backups_20261103/flow_definition_新建气象数据_内网_配置导入_WAVE-NEW.json (118080 B)
_flow_backups_20261103/flow_definition_新建气象数据_外网_配置导入_WAVE-NEW.json (118018 B)
_flow_backups_20261103/flow_definition_新建风机类型_WAVE-NEW.json     (128886 B)
_flow_backups_20261103/param_table_发送综合计算_WAVE-NEW.json         (1754 B)
```

## 未改变（按用户指示保留）
- **板块顺序换位**：SIMPLE_SECTIONS 中 `element`/`project` 两键换位（HEAD: terrain→weather→element→turbine→project→cfd→comprehensive→export；当前: terrain→project→weather→turbine→element→cfd→comprehensive→export）**保留**，未还原。

---

## ⚠️ 最终修正（2026-11-03 追加）：flow 已恢复为新版（runtime 注入）

用户在确认改动性质后，决定**恢复 flow 的 runtime 注入改动**（除导入地形图有专门决策，见下）。因此 `flow_packages/` 当前用**新版 `_WAVE-NEW`**（非 HEAD 原版），HEAD 原版全部另存备份。

### 执行的操作
```
恢复覆盖: _flow_backups_20261103/*_WAVE-NEW.json → flow_packages/*.json （原名）
原版备份: flow_packages/（HEAD原版）→ _flow_originals_20261103/*_ORIGINAL-HEAD.json
```

### 目录现状
| 目录 | 内容 | 用途 |
|---|---|---|
| `flow_packages/` | **新版（runtime 注入）** | 实际运行 |
| `_flow_backups_20261103/` | 新版副本（同 flow_packages，`_WAVE-NEW` 后缀） | 冗余备份 |
| `_flow_originals_20261103/` | **HEAD 原版副本**（`_ORIGINAL-HEAD` 后缀） | 原版备份 |

「导入地形图」单独决策：原版 47 步（步骤 9-46 混入气象数据链）被 #4 波拆成 9 步纯地形链（副本 description 明示"原 47 步误把新建气象数据链拼在地形后,已拆回各自流程"）。用户选**选项 A**：恢复 9 步纯地形链新版，原版 47 步保留在 `_flow_originals_20261103/`。

### 验证（恢复后）
- `导入地形图`: 3830 → **679 行**（9 步纯地形链）✓
- `新建风机类型`: 0 → **3** 个 `turbineImportFilePath`（runtime 注入回归）✓
- `test_input_contract_p0.py`: 之前 5 个失败测试 → **18 passed 全绿** ✓
- `validate_chain_manifest`: 之前 7 problem → **0 problem**（manifest 与 flow 完全一致）✓

### 关键结论
HEAD 原版 flow 是硬编码本机路径（`C:\Users\14830\...`）的旧版；新版用 `${runtime.*}` 注入,是对自动化的正向改进。用户直觉「flow 的改动可保留」正确。

## 备注
- flow 还原的直接影响：`test_input_contract_p0.py` 中 5 个测试（terrain/turbine/config_import/roughness/chain_manifest）断言 flow 消费 `${runtime.*}` 键，与 HEAD 原版 flow 的硬编码路径冲突 → **这些测试在 flow 还原后必然失败**（详见 `02-审计三分类与核心裁决.md`）。
- 还原过程未删除任何文件，改版全部以 `_WAVE-NEW` 副本保留，可 100% 找回。