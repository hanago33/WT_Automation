# InterestAreas 控件按节点消歧修复记录 2026-08-20

> 本文件沉淀 2026-08-20 会话：Meteodyn Universe 中测风点/风机/结果点/绘图/配置等节点共用同一套 M 系列图标按钮（InterestAreas_Button_*），此前 Edit/Delete/Import 无法按节点区分，导致流程"猜不到"。本次定位根因并修复采集端 + 合并工具，重合并主库，按节点补齐标签。
> 相关模块：`build_control_map_library.py`（采集器）、`tools/merge_standard_control_library.py`（合并工具）、`control_maps/standard/总控件信息.json`（主库）。
> 背景知识：WPF 图标按钮 UIA Name 是 SVG path（M6/M19/M20…），真实语义来自 helpText/functionText，见 [[控件语义体系与采集器扫描修复记录_20260811]]。

---

## 1. 问题描述

Meteodyn Universe 里每个逻辑节点（**测风点 / 风机 / 结果点 / 绘图 / 配置 / 风廓线 / Lidar / 中尺度单元**）都有自己的面板，面板顶部是一套 tile 工具栏，包含语义相同、**automationId 完全相同**的图标按钮：

| automationId | functionText |
|---|---|
| `InterestAreas_Button_Add` | 添加新元素 / 添加新配置 |
| `InterestAreas_Button_Edit` | 编辑选中的元素 |
| `InterestAreas_Button_Delete` | 删除所选的元素 |
| `InterestAreas_Button_Import` | 从一个文件导入元素（可接受格式：X Y 高度1 高度2...）|
| `InterestAreas_Button_ToggleTileState` | 最大化 |

采集到的信号原本都在，但**合并进主库后 Edit/Delete/Import 没能按节点区分**：这些按钮在主库里的 `relatedLabelName`/`labelText` 被污染成同父容器的字段标签（"载入"/"计算尾流效应"/"类型"/"高度 (m)" 等），而不是节点名。结果是流程无法告诉「风机节点的 Edit」和「测风点节点的 Edit」的区别——"猜不到 / 区分不了"。

### 具体诉求（按优先级）
1. **配置、绘图** 的 **Add** 按钮可区分。
2. **测风点、风机** 的 **"从一个文件导入元素"**（Import）可区分。

---

## 2. 关键调研结论（先入为主，避免重踩）

- **区分信号存在且已采到**：每个面板的标题控件 automationId=`InterestAreasView_Tile_Header`，`controlType=Text`，`name` 即节点名（测风点/风机/…），且是各 InterestAreas 按钮的**同层兄弟**（同 parentIndex，父为 `ToggleTileState`）。采集文件里这 8 个标题全都有。
- **采集器本就有"面板标题消歧"三级降级**（name → label_text → 面板标题 → found_index），但 `_extract_panel_title` 取的是**第一个**非 SVG 短文本兄弟。对 Add 恰好命中标题，对 Edit/Delete/Import 会命中更靠前的字段标签兄弟，取到错误标签。
- **主库已按节点保留 Add**（每节点 1 条 + `relatedLabelName`=节点名），但 Edit/Delete/Import 被污染标签合并错桶。
- **这些按钮当前未被任何 flow step 绑定**（grep `flow_packages/` 无 `InterestAreas` 命中）——修复不破坏现有流程，且为将来按节点绑定铺路。
- 本文档出现的"乱码"`�D..` 曾是**控制台 GBK 显示问题**，非数据损坏；用 code-point 校验（每字符 `hex(ord())`）确认采集/主库里的中文是真正常。

---

## 3. 根因

`_extract_panel_title`（`build_control_map_library.py` ~4594）返回「父容器内第一个短文本兄弟」，对 Edit/Delete/Import 会取到 `载入/计算尾流效应/类型/高度 (m)` 等**字段标签**而不是面板标题。合并工具 `_discriminator` 读 `labelText`/`relatedLabelName` 分桶，于是这些按钮没按节点分桶、标签错乱。

注意：录制文件里 `recommendedTargetValue` 的**第 3 段**（`InterestAreas_Button_Edit,Button,测风点,...`）其实已带正确节点，但**字段** `labelText`/`relatedLabelName` 是错的——两者不一致，是根因的直接证据。

---

## 4. 修复（方案 A，最小改动）

### 4.1 采集端 `build_control_map_library.py` — `_extract_panel_title`
- 在遍历兄弟前，**优先查找 automationId=`InterestAreasView_Tile_Header` 的兄弟并返回其 `name`**（权威面板节点名）。
- 找不到 TileHeader 才回退旧的"首个非 SVG 短文本兄弟"逻辑 → 不改变非 interest-area 面板行为。

### 4.2 合并工具 `tools/merge_standard_control_library.py`
- `load_all`：为每个 recording 的 flatControls 建立 `parentIndex → TileHeader name` 查找表；按下标把对应 flatControl 的父容器标题经新参 `panel_title` 传入 `normalize_control`（新参在末尾、默认空，向后兼容）。
- `normalize_control`：对 `InterestAreas_Button_*`，用权威 `panel_title` 统一覆盖 `relatedLabelName`/`labelText`；无 TileHeader 时回退 `targetValue` 第 3 段的节点名（仅当它是已知节点集合）。
- 用 merge-tool 补丁 + 重跑合并，而非一次性脚本：可从 recordings 可复现重建标准库，**下一次采集/合并也自动正确**，并保留 `_discriminator` 既有分桶机制。

### 4.3 测试 `tests/test_merge_standard_control_library.py`
新增 2 个回归测试：①污染 labelText 按 TileHeader 修复并按节点正确分桶；②无 TileHeader 时回退 targetValue 节点名。

### 4.4 数据备份
主库及配套 catalog/report 已备份至 `control_maps/standard/backups/*_20260820_204139_pre-IA-label-fix.json`。`release_out/` 副本未改动。

---

## 5. 效果量化

主库 `总控件信息.json`：flatControls 4496 → **4448**。非 InterestAreas 控件数完全不变（4401=4401）；48 条差异全部来自原先被污染、现正确归并到每节点唯一条目的 InterestAreas 按钮。

最终每个按钮恰好**每节点 1 条**，`recommendedTargetValue = 短aid,Button,节点,功能文本`，`relatedLabelName` 与 `labelText` 均等于节点名：

| 按钮 | 覆盖节点 | 条数 |
|---|---|---|
| Add | 8 个 | 8 |
| Edit | 8 个 | 8 |
| Delete | 8 个 | 8 |
| Import | 7 个（配置节点本无 Import，正确） | 7 |
| ToggleTileState | 8 个 | 8 |

诉求落实（直接查主库）：
- 配置/绘图 Add：`InterestAreas_Button_Add,Button,配置,添加新配置`、`...,绘图,添加新元素`。
- 测风点/风机 Import：`InterestAreas_Button_Import,Button,测风点,从一个文件导入元素（…）`、`...,风机,...`。

---

## 6. 验证

- `python -m pytest tests/ -q` → **524 passed, 0 failed**（含新增 2 条，以及既有 `test_acquisition_coverage.py`/`test_control_map_label_association.py`）。
- **注意**：本记录为离线数据核对 + 测试通过，**未在真实 Meteodyn Universe 上实跑验证**。按项目"改动需实测后交付"的约定，实际跑流程点这些按钮前，建议先在目标软件上验证一次运行时能否正确点到某节点（如风机节点 Import）的按钮。

---

## 7. 遗留 / 说明

- `Import-配置` 不存在（配置节点无 Import 按钮，只有 添加新配置），未虚构。
- `Import-风廓线`（functionText 为"导入风廓线"）单独成条目且 label 正确，未触碰。
- legacy 旧库 `library_*.json` 中的个别 found_index/旧裸条目不属 InterestAreas 图标，未触碰。

---

# 2026-08-21 追加：合并入库对话框路径同样塌缩，已修复并补齐测试

> 上篇修复的是 **canonical 合并**（`merge_standard_control_library.run_merge`），实测 `总控件信息.json` 每节点 1 条。但用户实际走的「采集 → 📥 合并入库」是**另一条独立去重路径**，重新合并后仍塌缩为每按钮 1 条 → 上篇修复"看起来对了、一跑又没了"。

## 1. 根因

「合并入库」对话框的去重键实现（两处）：
- `build_control_map_library.py : _merge_dedup_key`
- `control_live_detector.py : _build_dedup_key`

默认模式键为 `("aid", automationId, controlType, name)`。InterestAreas 各节点按钮 `automationId` 相同、`name` 为空（实际是 SVG path 图标），故 **8 个节点键完全相同 → 被误并为 1 条**。canonical 合并靠 `labelText` 判别分桶，但这两处对话框路径并未实现同款判别。

## 2. 修复（两种实现同步、与 canonical 判定一致）

三种去重模式（`aid` / `uiPath` / `name+ct`）的键均追加 `"ia:<节点>"` 区分符，节点名来源三级降级：

1. **TileHeader 兄弟面板节点名**（最权威，兼容旧采集格式）——新增 `_build_ia_panel_title_map()`，从 flatControls 中 `automationId=InterestAreasView_Tile_Header` 兄弟的 `parentIndex → name` 建表，靠按钮自身 `parentIndex` 反查；
2. **labelText / relatedLabelName**（新采集已带）；
3. **recommendedTargetValue 第 3 段节点名**（兜底，仅当命中已知节点集合）。

特别注意 **uiPath 模式**：各节点按钮共享同一 uiPath（`...MUPMSCInterestAreasViewModel > <SVG-path>`，仅配置节点独占 `...MUPElementsConfigurationsViewModel` 一条），此前 7 个节点被并成 1 条，追加标签后同样按节点区分。

涉及文件：
- `build_control_map_library.py`：`_merge_dedup_key` / `_interestarea_node_label` / `_build_ia_panel_title_map`；调用点 `_do_preview`、`_merge_payloads_into_target`。
- `control_live_detector.py`：`_build_dedup_key` / `_interestarea_node_label` / `_build_ia_panel_title_map`（static）；调用点 `_execute_merge`。
- 知识库模式 L 的代码锚点已同步更新。

## 3. 验证

- 新增回归测试 `tests/test_merge_dialog_dedup.py`（9 条）：三种模式 8 节点不塌缩、旧格式（labelText 空 + rtv 数字）经 TileHeader 恢复 8 节点、非 IA 控件零回归、两处实现 key 一致、`_merge_payloads_into_target` 端到端 8 节点保留且幂等。
- 全量扫描 `control_maps/**`（109,128 控件）：异常 0、非 IA 控件带 `ia:` 后缀 **0**。
- 旧录制 `20260730_...`（rtv 第 3 段为数字 `,1/,2/,3`）：三种模式 miss 均 0，恢复 8/8/8/8/7。
- 既有 `tests/test_merge_standard_control_library.py` 17 条全部通过（canonical 路径零回归）。

## 4. 遗留 / 注意

- **uiPath 旧产物孤儿条目**：若目标库曾用旧 uiPath 键（`("ui", path)`）合并，新代码合并不会自动清理旧的折叠条目，需一次性重建目标库。
- 对话框两条实现存在**既有分歧**（`name` 回退是否含 `displayName`），对 IA 控件无影响，未改动。
- `release_out/` 发布包仍为旧代码，发布前需重新构建。
