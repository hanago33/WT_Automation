# D7 · AI 助手使用手册（CLI / GUI）

> 本篇回答：**AI 助手能帮我做什么、怎么用、输出怎么落回流程**。
> 读者：想用自然语言生成/审计/修复流程的人。
>
> 子系统组成在 C6；本篇是**操作手册**。

---

## 1. 先明确边界

| ✅ 能做 | ❌ 不做 |
|---|---|
| 自然语言 → 流程步骤（DSL） | **不驱动 MUP**（执行永远由 L3 执行链路负责） |
| 检索控件库、推荐定位方式 | 不保证生成的控件定位一次就对 |
| 审计流程、指出问题 | 不替你判断业务正确性 |
| 用控件库回填残缺控件定义 | 不修改生产流程文件（除非你显式导入） |
| 参数扫描、日志诊断 | 不联网时不可用（内网通常无 LLM 服务） |

> 🚨 **最重要的一条**：AI 产出的步骤**必须经过编辑器校验 + 实跑验证**。它和人工写的步骤一样会假成功。

---

## 2. 启动

| 方式 | 命令 |
|---|---|
| GUI（推荐） | 双击 `启动WT_AI助手.bat` |
| GUI（命令行） | `python -m WT_AUTOMATION_Agent --gui [--gui-port N] [--gui-no-browser]` |
| 编程 | `from WT_AUTOMATION_Agent import start_gui; start_gui(port=None, open_browser=True)` |
| CLI | `python -m WT_AUTOMATION_Agent "<自然语言指令>" [选项]` |

---

## 3. CLI 参数

| 参数 | 说明 |
|---|---|
| `instruction`（位置参数） | 自然语言指令 |
| `--sequence` / `-s` | 作为**多步序列**处理（使用 `add_sequence`） |
| `--output` / `-o` | 输出到 JSON 文件 |
| `--list-schemas` | 列出所有支持的 action 及其参数 |
| `--list-skills` | 列出可用的内置 Skill |
| `--project-desc` | 项目描述文字（给模型更多上下文） |
| `--control-file` | 控件库 JSON 文件路径（`flow_definition.json` 格式） |
| `--chat` | 对话模式（按文本返回，**不生成步骤**） |
| `--verbose` / `-v` | 详细日志 |
| `--gui` | 启动对话式 GUI |
| `--gui-port` / `-p` | GUI 监听端口（默认自动选择） |
| `--gui-no-browser` | 不自动打开浏览器 |

### 3.1 典型用法

```powershell
# 先看看它认识哪些动作与技能
python -m WT_AUTOMATION_Agent --list-schemas
python -m WT_AUTOMATION_Agent --list-skills

# 生成单步
python -m WT_AUTOMATION_Agent "点击综合页签" --output step.json

# 生成多步序列，并给控件库与项目描述
python -m WT_AUTOMATION_Agent "新建一个测风塔气象对象并导入数据" ^
  --sequence ^
  --control-file flow_packages/flow_definition.json ^
  --project-desc "某风电场，UTM 投影，单塔 M1" ^
  --output steps.json -v

# 只想问问题，不要步骤
python -m WT_AUTOMATION_Agent "click 和 click_relative_region 有什么区别" --chat
```

---

## 4. 配置模型

模型档案由 `WT_AUTOMATION_Agent/model_profiles.py` 管理：

| 函数 | 行号 | 作用 |
|---|---|---|
| `list_profiles()` | L62 | 列出档案 |
| `get_profile(name)` | L72 | 读取 |
| `save_profile(name, raw)` | L78 | 保存 |
| `delete_profile(name)` | L91 | 删除 |
| `set_default(name)` | L103 | 设为默认 |
| `get_default()` | L112 | 取默认 |
| `migrate_from_legacy()` | L119 | 从旧格式迁移 |

配置文件：

| 文件 | 说明 | 是否入库 |
|---|---|---|
| `WT_AUTOMATION_Agent/model_profiles.json` | 模型档案（**含 API Key**） | ❌ gitignore |
| `WT_AUTOMATION_Agent/_gui_config.json` | Agent 运行配置 | ❌ gitignore |

> 🚨 两个文件都被 gitignore 且从未入库（E4 §6.1 已核实）。**新 worktree / 换机必须人工拷贝**，禁止提交。

---

## 5. 技能系统

技能是 Agent 的**软知识**，放在 `WT_AUTOMATION_Agent/skills/`。

| 现有技能 | 用途 |
|---|---|
| `step_authoring_best_practices.md` | **步骤编写规范**（写步骤必读） |
| `excel_column_mapping.md` | Excel 列映射 |
| `meteodyn_wt_domain.md` | MUP 领域知识 |
| `multi_parameter_scan.md` | 多参数扫描 |
| `flow_audit.md` | 流程审计要点 |
| `template_auto_association.md` | 模板自动关联 |
| `asset_assisted_flow_building.md` | 资产辅助的流程构建 |
| `agent_usage_guide.md` | Agent 使用指南 |
| `agent_architecture.md` | Agent 架构说明 |
| `agent_troubleshooting.md` | Agent 排障 |
| `debug_lessons_learned.md` | 调试经验教训 |

**支持格式**：`.md` / `.json` / `.xlsx` / `.csv` / `.pdf` / `.docx`（由 `skill_bridge` 的对应加载器处理）。

> 💡 **扩展 Agent 的最低成本方式就是加一份 skill 文件**，不用改代码（C6 §6）。

---

## 6. 六类能力

| 能力 | 模块 | 说明 |
|---|---|---|
| NL → 步骤 | `agent.py`（Function Calling） | 产出可导入的步骤 JSON |
| 控件检索 | `control_search.py` / `control_index.py` | 结构化检索 / LLM 可读索引 |
| 流程审计 | `flow_audit.py` | `audit_flow(flow)` 产出分级问题清单 |
| 流程修复 | `flow_repair.py` | 用控件库回填残缺控件定义、清洗 SVG 名称 |
| 流程编辑/差异 | `flow_ops.py` | `flow_to_text` / `diff_flows_structural` / `apply_edit_patch` |
| 日志诊断 | `log_diagnosis.py` | 喂 `logs/last_run_report.json` 做根因分析 |

**日志诊断示例思路**（`log_diagnosis.py`）：

```
load_run_report(path) → extract_failures(report) → build_diagnosis_prompt(log_text, flow_steps)
```

> 💡 这是把 F1/F2 的排障经验自动化的入口——出故障后先让它读报告，往往能直接指出"首因在哪个 step"。

---

## 7. 输出怎么用

```mermaid
flowchart LR
    A["AI 产出步骤 JSON"] --> B["WT_Flow_Editor 打开/粘贴"]
    B --> C["统一校验（validate_flow_definition）"]
    C --> D["人工复核：控件ID/消歧/提交手段/状态校验"]
    D --> E["保存（自动同步注册表）"]
    E --> F["实跑验证 → 看 last_run_report.json"]
```

| # | 复核点 |
|---|---|
| 1 | `controlId` 是否在本步骤 `controls[]` 里 |
| 2 | 同名控件是否带了 **`label_text` / `ui_path`** 消歧 |
| 3 | 输入类动作是否有**提交手段**（`postInputKeys`） |
| 4 | 关键动作是否有 **`continueWhen`** 状态校验 |
| 5 | 实跑后报告里是否出现 `fallbackUsed` |

---

## 8. 常见问题

| 症状 | 处理 |
|---|---|
| 提示未获取到 API Key | 配 `model_profiles.json` 或环境变量（E4 §5） |
| 生成的动作名不认识 | 用 `--list-schemas` 对照；可能是模型幻觉，需人工改 |
| 生成的控件定位总是不对 | 传 `--control-file` 给控件库；确认控件库里有该窗口 |
| 内网用不了 | LLM 服务需外网；内网通常不可用，改用编辑器手工/蓝图生成 |
| AI 说成功了但界面没变 | **假成功**——回到 B5 §1，加 `continueWhen` 再验 |

---

## 9. 相关文档

| 想做什么 | 去哪 |
|---|---|
| 子系统模块清单 | C6 |
| 动作字段 | E1 |
| 模型/端点配置 | E4 |
| 完整作业怎么串 | D8 |

---

核验：2026-09-11 / 涉及：`WT_AUTOMATION_Agent/cli.py`(L20–82)、`__init__.py`(L79)、`model_profiles.py`(L62–119)、`skill_bridge.py`(L58–366)、`flow_audit.py`、`flow_repair.py`、`flow_ops.py`、`log_diagnosis.py`、`启动WT_AI助手.bat`
