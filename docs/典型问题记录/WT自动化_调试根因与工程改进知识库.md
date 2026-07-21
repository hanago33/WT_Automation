# WT 自动化 · 调试根因与工程改进知识库

> 面向人的综合知识库。内容来自两条线：①项目内 10 份 `docs/debug/debug-*.md` 调试记录；②围绕定位/执行/转换链路的工程改进对话。
> 目的：让后续类似问题能快速对号入座、复用修复范式；也作为 Agent skill（`.codebuddy/skills/wt-automation-lessons/`）与内置 skill 的同源出处。

---

## 一、核心心智模型（先读这个）

1. **"动作执行成功" ≠ "业务生效"。** 日志打印"点击成功/输入成功"只代表控件被操作，不代表 UI 真的响应。必须校验状态变化（窗口出现、值提交、行新增）。绝大多数疑难 bug 都是"假成功"。
2. **报错步往往是连带受害者，不是首因。** step_39 报错，真因常在 step_36→37 的某个假成功。定位时先回溯到"第一个界面没按预期变化"的步骤。
3. **运行时定位是"全遍历 + 打分取最高"，不是条件查询。** `window.descendants()` 逐个 `get_control_definition_match_score` 打分，分最高者胜。因此"评分过宽"会误命中，"缓存未校验归属"会命中错对象。
4. **定位是分层降级的**：复合精确定位 → ui_path 路径对齐 → 模板图像 → 坐标。越靠后越脆，命中靠后层要视为"退化告警"并触发自愈/反馈。
5. **修复要最小化 + 取证驱动。** 先最小插桩（ndjson）证伪假设，再做最小改动，最后 pre-fix / post-fix 对比验证。不要凭猜叠加逻辑。

---

## 二、运行时根因模式库（症状 → 根因 → 修复范式）

### 模式 A：假成功 · 命中文本展示层
- **症状指纹**：日志成功但界面无任何变化；命中控件 `controlType=Text/TextBlock`，`IsKeyboardFocusable=False`，无 `Invoke/Selection/Value/Toggle` 任一模式。
- **根因**：打分命中了只读的显示层文字，而非真正可交互控件。
- **修复范式**：为该步补真实可点击目标或改用 `click_relative_region`；在评分/候选里降权纯展示型控件。
- **代码锚点**：`click_flow_control`、`get_control_definition_match_score`。
- **来源**：`debug-private-group-click.md`。

### 模式 B：假成功 · 输入未提交
- **症状指纹**：输入这步"成功"，但下一步依赖该值的操作失败，或业务未生效；常见于日期/高度/路径等文本框。
- **根因**：值写入了控件但未失焦/未回车，程序未提交。
- **修复范式**：输入动作补 `postInputKeys:{TAB}`（或 `{ENTER}`）强制提交与失焦。
- **代码锚点**：步骤 `postInputKeys`、`flow_definition.json`。
- **来源**：`debug-step37-add-data-miss.md`、`debug-time-series-input-regression.md`、`debug-default-height-relative-input.md`。

### 模式 C：错窗口 · 无标题主窗冒充弹窗
- **症状指纹**：相对区域点击落到主窗口（如 `Window_Main`，rect≈`(0,0,2560,1516)`）而非目标弹窗。
- **根因**：空标题 WPF 窗口评分过宽，被当成候选弹窗。
- **修复范式**：前台窗口优先；空标题窗口只在"确为前台"时作兜底；收紧标题匹配评分。
- **代码锚点**：`find_flow_window_for_relative_region`、`score_window_against_spec`。
- **来源**：`debug-add-data-false-hit.md`。

### 模式 D：矩形基准漂移
- **症状指纹**：整段相对区域点击统一偏移；外框 rect 与内容区 rect 不一致。
- **根因**：`rectangle()` 返回的基准不稳定（外框 vs 客户区）。
- **修复范式**：优先用原生窗口句柄 `GetWindowRect` 取稳定基准。
- **代码锚点**：`get_wrapper_rectangle`。
- **来源**：`debug-relative-region-offset.md`。

### 模式 E：缓存污染 · 未校验窗口归属
- **症状指纹**：改了评分逻辑仍反复误命中同一个错对象；换窗口后仍拿旧结果。
- **根因**：`FLOW_CONTROL_CACHE` 命中未校验当前窗口归属。
- **修复范式**：缓存键/命中处加"窗口归属校验"，归属不符即失效重查。
- **代码锚点**：`get_cached_flow_control`。
- **来源**：`debug-private-group-click.md`（第 3 轮）。

### 模式 F：同名/同类控件误命中（→ found_index 消歧）
- **症状指纹**：一排同 `control_type`、`name` 通用（如"列表项"）的控件里点错序号；`automation_id` 含 `_5`，版本升级后变 `_6`，仅靠 id/name 全失效。
- **根因**：缺少稳定的"同级序号"消歧手段；ancestors 仅作加分项。
- **修复范式**：录制序号 `Name||ControlType#[范围,N]` 保留为 `inspectData.foundIndex` → 运行时 `get_wrapper_found_index`（父容器内同类同级 0 基序号）→ 作**最低优先回退候选** + 打分 **+12 并列消歧**（永不覆盖可靠 id/name）。
- **代码锚点**：`wt_flow_locator.py:get_wrapper_found_index / wrapper_matches_locator / build_common_locator_candidates / get_control_definition_match_score`；`flow_recorder_converter.py:_extract_segment_found_index`。
- **来源**：`debug-click-9-miss.md`、`debug-cft02-step16-drift.md` + 本轮改进。

### 模式 G：连带失败 · 首因≠报错步
- **症状指纹**：末尾某步报"找不到控件"，但该步定位本身没问题。
- **根因**：上游某步假成功（未真正打开面板/未提交），导致后续界面前置条件不满足。
- **修复范式**：回溯到第一个"界面未按预期变化"的步骤修复；不要在报错步打补丁。
- **来源**：`debug-add-data-false-hit.md`、`debug-step37-add-data-miss.md`。

---

## 三、工程改进决策与契约（定位/执行/转换链路）

### 1. 分层定位与复合定位器
- **复合定位器**：`method/value` 逗号成对拼接（如 `automation_id,control_type`），运行时 split 后逐段 **AND** 匹配，全命中才算命中。
- **优先级（得分从高到低）**：`automation_id,control_type`(100) > `automation_id` > `ui_path`(深度≥2) > `name,control_type` > `name` > `class_name,...` > `control_type,found_index`(回退)。
- **ui_path**：录成 `主窗||Window->组||Group->确定||Button`，运行时从叶子 `.parent()` 逐级重建实际路径尾部对齐比对；深度≥2 才启用，避免过泛。
- **要点**：动态控件优先靠"组合"稳住（id+type 或 name+type），复杂嵌套靠 ui_path 尾部对齐，最后才落 found_index / 模板 / 坐标。

### 2. fallbackChain 自适应降级链
- 与旧的字符串 `fallbacks` 解耦，结构化为 4 级：**L1 主定位 → L2 ui_path_search → L3 template（模板图像）→ L4 coordinate（坐标）**。
- 命中非 L1 即视为"退化"，应触发反馈记录与自愈学习，而非静默通过。

### 3. 运行时反馈闭环
- 执行上下文携带 `flowDefinitionPath` / `runId`，运行结果回写 `feedbackHistory`：`fallback_recovery` / `fallback_template_recovery` / `step_failure` 等类型。
- 汇总进质量报告，形成"运行 → 反馈 → 优化定义"的闭环。

### 4. 自愈式选择器
- `detect_healed_locator` 发现降级命中（priority>0）→ `record_self_heal` 持久化 override 到 `self_heal_store.json` → 下次该控件提到 priority 0，逐步收敛。

### 5. found_index 父链引导消歧（本轮新增）
- 语义：**父容器内同类同级的 0 基序号**（区别于采集侧 `len(flat_controls)+1` 的全局遍历序号）。
- 判同三重：对象同一 > `runtime_id` > 矩形（`_is_same_wrapper`）。
- 定位策略：**低优先回退 + 打分 +12 消歧**。因录制范围有时是"窗口内匹配路径第 N 个"，不严格等于"直接父容器第 N 个"，故绝不用作高优先硬匹配去覆盖可靠 id/name。

### 6. 转换器（recorder → flow definition）
- 保留录制 UIPath 段尾 `#[范围,N]` 的序号为 `foundIndex`（此前被 `_strip_segment_index` 直接丢弃）。
- 具备增量合并、截图关联、质量报告（`conversionMeta`、`stats`）能力。
- **注意**：`_clean_control_definition` 会把 `inspectData` 值统一 `normalize`（int→str），运行时须用 `int()` 容错解析。

---

## 四、字段契约贯通纪律（最易踩的坑）

- **白名单重建陷阱**：`normalize_step` 用白名单重建步骤时，任何新增字段（如 `fallbackChain`、`_` 前缀内部字段）必须显式放行，否则会被静默丢弃 → 表现为"改了没生效"。
- **Excel 往返五处同步**：新增一个字段要同时改 **列定义 / 规范化 / 写出 / 读入 / 清理** 五处，缺一处就往返丢数据。
- **公共入口签名变更要同步调用点**：曾因 `configure_flow_executor` 签名缺参导致 `NameError` 阻断整个执行器。改公共函数签名时，务必全仓搜索调用点同步。
- **测试对齐更强行为，而非掩盖**：行为增强后过期测试应改为断言"更强的新行为"，不要放宽断言掩盖回归。

---

## 五、调试方法论（可直接照做的流程）

1. **复现 + 最小插桩**：在关键分支落 ndjson 取证日志（见 `.dbg/trae-debug-log-*.ndjson`），记录候选、得分、命中窗口 rect、控件模式。
2. **假设 → 证伪**：写下 2–3 个可证伪假设，用取证数据逐个排除，锁定唯一根因。
3. **pre-fix / post-fix 对比**：修改前后各跑一次，对比同一步的候选/得分/命中，确认因果。
4. **最小修复**：只改根因点，避免叠加"防御式补丁"污染打分逻辑。
5. **回归**：跑相关单测（`tests/`），确认无连带回归；必要时补断言更强行为的新测试。
6. **沉淀**：按"背景/症状 → 假设 → 调试计划 → 运行时证据 → 结论 → 修复 → 验证"结构记进 `docs/debug/`。

---

## 六、代码锚点速查表

| 关注点 | 文件 : 符号 |
|---|---|
| 复合定位匹配 | `wt_flow_locator.py : wrapper_matches_locator` |
| 候选生成 | `wt_flow_locator.py : build_common_locator_candidates` |
| 打分 | `wt_flow_locator.py : get_control_definition_match_score` |
| 同级序号 | `wt_flow_locator.py : get_wrapper_found_index / _is_same_wrapper` |
| 矩形基准 | `wt_flow_locator.py : get_wrapper_rectangle` |
| 自愈 | `wt_flow_locator.py : detect_healed_locator / record_self_heal` |
| 点击 | `wt_flow_executor.py : click_flow_control` |
| 窗口选择 | `wt_flow_locator.py : find_flow_window_for_relative_region / score_window_against_spec` |
| 缓存 | `wt_flow_locator.py : get_cached_flow_control` |
| 定位推荐 | `build_control_map_library.py : build_locator_recommendation` |
| 序号保留 | `flow_recorder_converter.py : _extract_segment_found_index / _build_control_definition` |
| 字段白名单 | `wt_flow_validation.py : normalize_step` |
| Excel 往返 | `flow_excel_io.py` |

---

## 七、维护约定

- 本文件是**人读同源出处**。Agent 侧同源产物：`.codebuddy/skills/wt-automation-lessons/SKILL.md`（skill 包）+ `skill_bridge.py` 内置 skill（保证被 Agent 上下文加载）。
- 新增一类根因或工程改进时，先更新本文件，再同步 skill 侧的精简规则。
- 暂不产出机器可读的 JSON 症状指纹库（按当前决策"先只做人读的 md"）。
