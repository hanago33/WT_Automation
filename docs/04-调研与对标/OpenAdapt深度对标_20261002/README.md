# OpenAdapt（OpenAdaptAI）深度对标与 WT_Automation 改进规划 · 导读

- **调研对象**：OpenAdapt 三仓库源码（`OpenAdapt` 主仓库含 legacy 冻结实现 + 架构文档、`openadapt-flow` 演示编译器/治理运行时、`openadapt-types`/capture 经文档确认），全部 MIT 许可
- **调研焦点**：录制事件数据模型、演示→确定性程序编译、效果契约与独立验证、执行结果分类学、分辨阶梯与自愈治理
- **调研日期**：2026-10-02/03
- **调研方法**：GitHub 主仓库 tarball 全量解包 + flow 仓库经 API 精准抓取 17 个核心源码/设计文件逐读；所有结论标注 `文件:行号` 或文档章节
- **性质**：纯调研与规划沉淀，**未修改 WT_Automation 任何功能代码**
- **一句话结论**：OpenAdapt 是全场竞品中唯一把「**桌面自动化的事务正确性**」做成一等公民的项目——它的效果验证器用故障矩阵实证了"屏幕验证在 7 类事务故障中 5 类会静默误判"，它的结果分类学（VERIFIED / COMPLETED_UNVERIFIED / RECONCILIATION_REQUIRED / HALTED）让"中断绝不报成功"成为代码级约束；这恰好是 WT run report 与队列事务语义要走的下一步。**同时修正此前一个判断：OpenAdapt 并非"早期项目"**——其 flow 引擎的工程成熟度（fail-closed 门禁、故障基准、治理修复生命周期）远超其 star 数给人的印象。

## 文档索引

| 文件 | 内容 |
|---|---|
| [01_项目全景与架构演进.md](01_项目全景与架构演进.md) | 三代架构演进（alpha 三段管线 → meta-package → **受治理的演示编译器**）；抽象阶梯 L0-L4；DEMONSTRATE/LEARN/EXECUTE 三阶段；26 个公共包拆分；成熟度重评 |
| [02_录制数据模型与事件聚合.md](02_录制数据模型与事件聚合.md) | legacy 精读：Recording/ActionEvent 完整 schema（五元组引用、canonical 键三态、聚合父子层级、脱敏内嵌）；8 步事件聚合管线；对 WT 录制转换器的对照 |
| [03_演示编译与效果契约.md](03_演示编译与效果契约.md) | flow 精读：IR 类型词汇表（57 类）；效果挖掘的**诚实四分法**（derived/readback/placeholder/none）；Effect 三种契约类型；VerificationTier 证据强度分级；身份带 |
| [04_治理运行时与结果分类学.md](04_治理运行时与结果分类学.md) | 分辨阶梯（结构优先，实测 21/21 vs 视觉 6/21）；多实例消歧门（局部性+唯一性）；自愈锚点刷新；效果验证三值裁决与故障矩阵；**修复生命周期 8 态状态机**；REPORT.md 解剖 |
| [05_WT对照借鉴规划.md](05_WT对照借鉴规划.md) | 12 维对照矩阵；**可移植机制清单**（消歧门→卷六内核增补、结果分类学→run report、效果四分法→录制转换器、锚点治理→模板自更新开关改造）；不建议照抄项；与 Airtest/影刃两套调研的合并视图 |

## 最值得先看的八项发现（TL;DR）

1. **屏幕验证被实证打脸**：事务故障研究证明 7 类故障中 5 类（重复提交/乐观UI后拒/部分保存/并发覆盖/双击多记录）屏幕上全是"已保存"横幅，**只有读系统台账（REST/FHIR/SQL/文件哈希）才能识别**（docs/design/EFFECT_VERIFIER.md 故障矩阵表）。WT 的 image_appear 等待只能回答"像素对不对"，回答不了"事务成没成"。
2. **三值裁决 fail-closed**：`CONFIRMED / REFUTED / INDETERMINATE`，后两者一律 HALT——"过期 token 永不误判为记录不存在"；没有"大概没问题"（verification.py:46-97 同族设计）。
3. **结果分类学落到代码**：`VERIFIED`（生产契约确认业务效果）≠ `COMPLETED_UNVERIFIED`（Demo 结果，**不是生产成功**）；`RECONCILIATION_REQUIRED` 永不被降级成 `HALTED_BEFORE_EFFECT`——"运行时不知道的事不能假装知道"（architecture.md Result states + PROCESS_CONTRACT_V1.md Outcome rules）。
4. **演示编译的诚实四分法**：效果契约只能从演示"真的观察到"的台账快照里挖（`sor_before/sor_after`）；挖不出→ 显式 placeholder（哨兵值保证永不假确认）或诚实的"无可验证效果"，**拒绝发明端点**（compiler/effect_mining.py 模块 docstring）。编译期零模型调用。
5. **分辨阶梯**：结构元素（DOM/UIA）→ 局部模板 → 全局模板 → OCR → 几何地标 → （可选）grounding 模型；桌面漂移基准上**结构阶梯 21/21 vs 纯视觉 6/21**；健康路径毫秒级、零模型成本（docs/RESOLUTION_LADDER.md）。
6. **多实例消歧门**（vision/match.py:68-242）：重复控件（每行一个编辑铅笔）在 `TM_CCOEFF_NORMED` 下个个 ~1.0，raw arg-max 点谁看运气——**局部性优先**（退化真目标胜过别处更清晰的相似物）+ **唯一性拒绝**（≥2 峰过阈值且无一在预期位置 → 返回 None 落阶梯/停机）+ **邻位拒绝** + **降级同类怀疑**（第二响应 ≥0.7 即视为重复控件嫌疑）。WT 的 InterestAreas 消歧案例（docs/03-专题记录）是同一失败类。
7. **自愈也要走治理**：heal 产物只是 **candidate**，必须过 8 态状态机（reviewed→replay_passed→fault_passed→approved→staged→canary→active），合同不变量硬拒绝任何安全弱化，**模型永不自晋升**（`assert_not_model_actor` 代码级断言）；canary 出现一次 silent-incorrect 立即自动回滚。对照 WT 现有"自动更新模板"开关——**方向相反**：WT 是直接覆盖，OpenAdapt 是提案-审批-金丝雀。
8. **报告的诚实性清单**（runtime/report.py）：每步 before/after 帧、身份门四态（verified/mismatch/**abstain**/unreadable——弃权也标记，绝不静默）、model_calls 与 est_model_cost_usd、**截图出网透明度**（接了 grounding 组件就警告"截图可能离开本机"）、**身份保护覆盖率**（N/M 点击已武装，未武装点击无身份检查——写在每份报告里）、裸 PHI 写盘告警。

## 使用约定

- OpenAdapt 侧事实标注来源（文档章节或 `文件:行号`）；源码位置：主仓库 `%TEMP%\openadapt_gh\OpenAdapt-main\`（含 `legacy/openadapt/` 冻结实现），flow 关键文件 `%TEMP%\oa_flow_files\`；
- 卷五的借鉴设计是规划稿，落地时按 AGENTS.md 在 worktree 中实施并全量回归；
- 许可：MIT——WT 可放心移植其模式与代码片段（注明出处）。
