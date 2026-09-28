# PART_ContentHost 定位与输入驱动调研 2026-08-05

> 针对"查找投影坐标系"输入框（`PART_ContentHost_查找_Edit`）无法可靠定位与输入的问题，结合控件库数据、定位代码与实测日志做了完整调研。**结论：该控件是 WPF TextBox 的内部宿主，不能用常规 UIA 输入方法驱动，正确方式是"坐标点击聚焦 + 全局键盘"。**

## 1. 问题现象

step_9（键入-查找投影坐标系，text=`CGCS2000 43`）目标控件为 `PART_ContentHost_查找_Edit`，此前 `type_text` 反复失败：

- `action type_text 未命中控件`（`type_text_into_wrapper` 返回 False）
- 或日志假阳性：报"已通过流程链路匹配输入文本"但界面上文字没进输入框
- 或命中错误元素：日志"命中 Raw View score=143"但实际聚焦到其它输入框

改用 `send_keys` action 后实测成功（status=success，文字正确输入）。

## 2. 控件库既有标注（采集器自动生成）

`control_maps/library/library_投影系_WPF_controls.json` 中该控件自带完整说明：

```
automationId = PART_ContentHost | controlType = Pane | className = ScrollViewer
IsControlElement = False / IsContentElement = False   → 仅 Raw View 可见
IsKeyboardFocusable = False                           → 不可键盘聚焦
LabeledBy = ""                                        → 无 UIA 标签关联
SupportedPatterns: Scroll, SynchronizedInput          → 无 Value/Text Pattern
notes: 说明=同行标签对应的实际操作控件（下拉框/输入框）
      | 可自动化风险=高；原因=控件不可键盘聚焦(IsKeyboardFocusable=false)，pywinauto 难以驱动
      | 已自动收录到控件库分类：投影系 / WPF
```

**采集器已正确标注"可自动化风险=高，pywinauto 难以驱动"。**

## 3. 定位原理分析

- `PART_ContentHost` 是 WPF 每个 TextBox 的可视化内部宿主，**界面上每个输入框都有一个**，`automation_id` 不唯一。
- 定位候选 `PART_ContentHost,Pane`（`automation_id + control_type`）评分 100，运行时第一个达到阈值（`_adaptive_threshold`）的宿主即命中，**无法区分是"查找"输入框**。
- uiPath 消歧（叶对齐比对）对 Raw View 的 PART_ContentHost **实际未生效**：父链是装饰器链，与录制的 `MBAProjectionSelectionView > PART_ContentHost` 匹配不上（`checked=0` 不加不减），日志 score 恒为 143（= 120 + 14 + 8 + 1）。
- labelText 消歧（`wrapper_matches_label_text`）先查 LabeledBy（空），再查附近"标签矩形"，对内部宿主不可靠。

**踩过的坑**：曾在评分函数里加"PART_ContentHost 命中必须 labelText 匹配否则否决"，因运行时标签匹配返回 False，导致**所有** PART_ContentHost 被否决 → 定位彻底失败（228 秒超时）。**已回退**（commit 前状态，279 测试通过）。

## 4. 两条输入链路对比

| 链路 | 动作 | 结果 |
|------|------|------|
| `type_text` | `find_flow_control` → `type_text_into_wrapper`：`click_input()` → `set_edit_text()` → `type_keys()` | **失败**。`set_edit_text` 需 ValuePattern（Pane 无）；`type_keys` 是 wrapper 的 UIA 方法，对 Raw View 内部宿主失效 |
| `send_keys` | `focus_flow_control`：`find_flow_control`（score=190）→ `click_input()` 点击宿主聚焦 TextBox → executor 全局 `send_keys(text)` | **成功**。`send_keys`（`pywinauto_recorder.player.send_keys`）是独立全局键盘模拟，不依赖 wrapper，焦点在 TextBox 即输入成功 |

**核心结论**：`PART_ContentHost` 不可用常规 UIA 输入驱动（无 ValuePattern、type_keys 失效），**正确方式 = 坐标点击聚焦 + 全局键盘输入**。这与其控件库 notes 的"可自动化风险=高"标注一致。

## 5. 采集功能调研（只读，未改代码）

采集器已对齐 Inspect 能力：RawViewWalker BFS 全量遍历、MSAA 面板、UIA Patterns 检测、同行标签关联（几何判定生成 labelText/relatedLabelName）、可自动化风险评估、质量分级、重复定位器消歧（`_disambiguate_duplicate_locators`）。

发现的缺陷 / 优化点：

1. **高风险信号未联动到定位策略**：`IsKeyboardFocusable=false` 的控件被标注"可自动化风险=高"，却仍给最高分定位候选（automation_id + control_type = 100 分），且不提示替代驱动方式。应降级定位策略 + 明确标注"需坐标/Tab 方式驱动"。
2. **不唯一 automationId 的消歧未贯彻到流程**：`_disambiguate_duplicate_locators` 对多个共用定位器的控件会用 label_text/found_index 消歧，但 flow 定义里 step_9 的控件 `targetMethod` 仍是 `automation_id,control_type`、消歧信息在"采集→流程"链路丢失。需确认是导入流程时丢的，还是流程用的是旧采集版本。
3. **PART_ContentHost 被标成"可编辑控件"**：role 标为"可编辑控件：扫描"，但实际是 Pane、不可聚焦、无 ValuePattern，误导后续以为可直接输入。

## 6. 后续待决策

- [x] 按第 5 节 ①②③ 优化采集端（2026-08-06 实施，见第 7 节）
- [ ] 是否把"PART_ContentHost 自动降级为 click_input + send_keys"固化为执行策略，使 `type_text` 无需手动改成 `send_keys`
- [ ] 是否处理"输入后无验证"问题（send_keys 依赖当前焦点，命中错误宿主会输入到别处）

## 7. 采集端优化与三方一致性（2026-08-06 实施）

### 7.1 采集端优化（build_control_map_library.py）

1. **B2：质量分级尊重 `foldedIntoParent`**
   `_classify_control_quality` 对折叠进父级 TextBox 的 PART_ContentHost 返回"建议忽略"，不再被 `_enrich_flat_controls` 覆盖成"推荐保留"。

2. **A 修复：PART_ContentHost 折叠增强**
   `_normalize_textbox_wrappers` 不再只看直接父级，而是：
   - 沿祖先链多层向上找 TextBox（`_find_textbox_ancestor`）；
   - 找不到时按位置重叠匹配同位置的 TextBox（`_find_overlapping_textbox`）；
   - 找到 → 折叠（foldedIntoParent=True）；找不到 → 孤儿保留。

3. **折叠条件放宽**
   合并后 PART_ContentHost 的 controlType 可能已是 Edit（前置规范化），情况 2 不再限定 pane/custom，只要 `automationId=="PART_ContentHost"` 且未折叠就尝试。模拟 Test2 数据：15 个 PART_ContentHost 中 10 个成功折叠。

4. **入库过滤 foldedIntoParent**（`_should_include_definition`）
   4 处控件定义收集点统一过滤折叠的 PART_ContentHost，使其彻底不进库。

### 7.2 三方一致性检查

| 环节 | 匹配逻辑 | 结论 |
|------|---------|------|
| 运行时定位（find_flow_control） | build_common_locator_candidates | 基准 |
| 编辑器定位校验（test_selected_locator） | 复用 wt_flow_locator | ✅ 一致 |
| 采集端 targetMethod/targetValue | 运行时语法全支持 | ✅ 一致 |
| control_live_detector | 独立字段评分 → **已统一**（优化1：伪 wrapper + 复用候选匹配） | ✅ 一致 |

### 7.3 运行时 label_text 消歧增强（优化3）
`wrapper_matches_label_text` 对 PART_ContentHost 增加"兄弟 TextBlock"兜底（`_match_sibling_text_block_label`）：LabeledBy 为空、附近标签矩形落空时，检查同父兄弟 TextBlock 文本是否等于预期标签。

### 7.4 效果与剩余问题
- 有 TextBox 表现的输入框（半径/X/载入等）：PART_ContentHost 折叠 → 入库只保留父级 textbox（不再重复）。
- 孤儿 PART_ContentHost（查找/名称/描述等组合控件）：无父级 TextBox，保留为输入控件，用坐标点击 + send_keys 驱动（已验证可行）。
- 剩余：
  - **textbox 层重复（已知小问题）**：`_merge_flat_controls` 按 runtimeId/name 去重，uia/win32 两个 backend 对同一 textbox 的 name 不同（"0 m"显示值 vs "半径"标签回填）或 runtimeId 不同，导致同位置保留多个 textbox 表现。可后续增强为"同位置输入框合并，保留标签回填版"。
  - send_keys 依赖当前焦点，无输入后验证。
  - ~~`type_text` 尚未自动降级为 send_keys~~（2026-08-06 已实现：`type_text_into_wrapper` 输入失败时自动走 `_type_via_screen_keyboard`，聚焦从 pyautogui.click 改为 control.click_input，与 send_keys action 同链路）

