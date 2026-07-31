# WT_Automation v4.0.0

面向 Meteodyn WT / Global Mapper 桌面软件的 UI 自动化平台，本次为一次能力大版本更新：**控件采集引擎全面增强、AI Agent 模块化扩展，并完成密钥安全加固**。

## ✨ 控件采集引擎（build_control_map_library）
- **RawViewWalker BFS 整树遍历**：改用 UIA 原始视图 + 广度优先遍历替代 pywinauto 默认 `children()`，采集更完整、更快，单窗口可稳定采集 1000+ 控件。
- **Control Patterns 检测（对齐 Inspect "Action" 菜单）**：新增 `supportedPatterns` 字段，通过 `IsXxxPatternAvailable` 布尔属性检测元素支持的全部 UIA 控制模式（Invoke / Value / ExpandCollapse / Toggle / Scroll 等 32 种），**零副作用、不误触发界面动作**，约 1.6ms/控件。
- **ExpandCollapse 状态采集**：为下拉框 / 树 / 折叠面板补充 `expandCollapseState`。
- **树结构元数据增强**：每个控件补充 `pathHash`、`childCount`、`isTransparentContainer`、`flatIndex`，并在全部探针补采完成后统一重建控件树，保证上下级关系完整。
- **层级树 GUI 浏览器**：采集器界面新增"层级树视图"，可直接按父子结构浏览、反查控件详情。
- **数据流时序修复**：元数据增强与控件树构建统一延后至所有探针补采之后执行，避免遗漏与信息缺失。

## 🤖 AI Agent 能力扩展（WT_AUTOMATION_Agent）
- 新增模块：`control_search`（控件检索）、`flow_ops`（流程操作）、`knowledge_base`（领域知识库）、`log_diagnosis`（运行日志诊断）、`memory`（记忆）、`model_profiles`（多模型配置档案）。
- 自然语言 → RPA 步骤转换、Skill 领域知识注入等能力进一步完善。

## 📚 文档与知识资产
- 新增《Inspect UIA 调研手册》，沉淀三视图 / Control Patterns / 字段对照等逆向经验。
- 补充 repowiki 中文知识库、控件库与流程包定义更新。

## 🔒 安全加固
- 将含明文 API Key 的 `_gui_config.json`、`model_profiles.json` 移出版本控制并加入 `.gitignore`，避免密钥入库泄露。
- ⚠️ 历史提交中出现过的旧密钥请在服务商后台吊销/重置。

## ✅ 质量
- 单元测试全部通过（131 passed）。

---
**Full Changelog**: https://github.com/hanago33/WT_Automation/compare/V3.0.0...v4.0.0
