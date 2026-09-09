# AGENTS.md — AI 会话工作方式声明（Worktree 隔离工作流）

> 本文档是给**所有 AI 编码助手**（ZCode / Claude / Codex / Cursor / 其他）的强制约定。
> 人类维护者请读「维护者操作」一节。如果你是 AI 且正在读本文件，即视为已接受本约定。

## 目录

1. [核心原则：三个目录，三种角色](#核心原则三个目录三种角色)
2. [AI 会话标准工作流](#ai-会话标准工作流)
3. [新 Worktree 初始化检查单](#新-worktree-初始化检查单)
4. [分支与提交规范](#分支与提交规范)
5. [禁止事项](#禁止事项)
6. [验证标准（合并前必过）](#验证标准合并前必过)
7. [维护者操作（人类）](#维护者操作人类)

## 核心原则：三个目录，三种角色

| 目录 | 分支 | 角色 | 谁在这里工作 |
|---|---|---|---|
| `D:\My_RF_Project\WT_Automation` | `main` | **主目录**：唯一可信源，保持干净、可发布 | 人类审查合并；**禁止任何 AI 直接改代码** |
| `D:\My_RF_Project\WT_automation_ai*` | `ai/session-N` | **AI 工作区**：每个 AI 会话一个，随意折腾 | AI 在这里改代码、跑测试、提交 |
| `D:\My_RF_Project\WT_automation_ai*`（丢弃态） | — | 实验失败即整体废弃，不留痕迹 | — |

一句话版本：**AI 永远不在主目录写代码。改完 → 测试 → commit → 通知人类合并。**

## AI 会话标准工作流

1. **会话开始**：确认自己所在目录是 worktree（`git rev-parse --git-dir` 输出以 `.git/worktrees/` 开头）。若发现自己在主目录里被要求改代码，**必须先提醒用户**，并建议迁移到 worktree。
2. **初始化**：按[下方检查单](#新-worktree-初始化检查单)补齐 gitignore 文件。
3. **开发**：在 worktree 分支上改代码。运行测试（`python -m pytest tests/ -q`）验证。测试不过不提交。
4. **提交**：每完成一个完整功能单元就 commit，提交信息用中文、`type(scope): 描述` 格式（见[提交规范](#分支与提交规范)）。
5. **报告**：完成后**明确告知用户**：改了什么、commit hash、测试结果，并注明"在 `ai/session-N` 分支，待人工审查合并"。**AI 不执行 `git merge` 到 main、不执行 `git push`。**
6. **收尾**：被人类告知"已合并"后，本会话即可结束。若会话被放弃，人类执行丢弃流程（见维护者操作）。

## 新 Worktree 初始化检查单

新 worktree 检出自带 git 跟踪文件，但以下 **被 gitignore 的必需文件不会出现**。开始工作前从主目录拷贝（相对仓库根）：

```bash
# Windows（Git Bash）
cp /d/My_RF_Project/WT_Automation/WT_AUTOMATION_Agent/_gui_config.json \
   /d/My_RF_Project/WT_Automation/WT_AUTOMATION_Agent/model_profiles.json \
   WT_AUTOMATION_Agent/
cp /d/My_RF_Project/WT_Automation/control_live_detector.py .
```

| 文件 | 用途 | 必需性 |
|---|---|---|
| `WT_AUTOMATION_Agent/_gui_config.json` | Agent 运行配置（含 API Key） | 用 Agent 功能时必需 |
| `WT_AUTOMATION_Agent/model_profiles.json` | LLM 模型档案（含 API Key） | 用 Agent 功能时必需 |
| `control_live_detector.py` | 开发机专用调试脚本，`tests/test_control_live_detector.py` 依赖它 | **跑全套测试必需** |

## 分支与提交规范

- 分支命名：`ai/session-<N>`（同一时间可以有多个会话并行）。
- 提交信息：中文，格式 `type(scope): 描述`，与仓库既有风格一致，例如：
  - `feat(automation): 测风塔数据配置XML生成注入、配置导入流程定义与无窗口启动`
  - `fix(automation): 队列窗口日志裁头后标签分类丢失`
- 允许的 type：`feat` / `fix` / `perf` / `refactor` / `test` / `docs` / `chore` / `style` / `release`。
- scope 一般用 `automation`；纯文档、纯依赖更新可省略 scope（如 `chore: ...`）。
- 提交前必须运行 `python -m pytest tests/ -q` 并全绿。因环境缺失（见检查单）导致的收集错误不算绿。
- **禁止 squash 式巨提交**：一个功能单元一个 commit；一次会话多个功能就多个 commit，每个都要能独立说清改了什么。

## 禁止事项

无论在哪个目录：

- **禁止** 在主目录 `WT_Automation` 修改任何代码（维护者明确要求并知悉的除外——例如让 AI 帮忙合并冲突）。
- **禁止** 执行 `git merge` 到 main、`git push`、`git rebase` 主目录 main、`git reset --hard`（reset 仅限 worktree 内、且仅针对自己会话的提交）。
- **禁止** 生成根目录散落的调试残留文件（`_diag_*.py`、`_tmp_*`、`_*.log` 等）。临时文件放系统临时目录，工作完成后清理。
- **禁止** 把 API Key、令牌、本机绝对路径写入任何被 git 跟踪的文件。
- **禁止** 修改 `.gitignore` 中关于密钥与运行时状态的既有规则。
- **禁止** 提交体积大的生成产物（截图、录像、构建产物）。

## 验证标准（合并前必过）

人类合并前逐项检查：

1. worktree 全套测试绿：`python -m pytest tests/ -q` → 741+ passed。
2. 提交历史清晰：每个 commit 一个功能单元，信息符合规范。
3. `git diff main...ai/session-N` 审查过，无密钥、无调试残留、无未说明的无关改动。
4. 新增依赖已写入 `requirements.txt`（如 `ttkbootstrap` 的先例）。

人类合并（在主目录执行）：

```bash
cd D:\My_RF_Project\WT_Automation
git merge ai/session-N          # 或先 git diff main...ai/session-N 审查
git worktree remove ../WT_automation_aiN
git branch -d ai/session-N
```

实验失败则整体丢弃：

```bash
git worktree remove --force ../WT_automation_aiN
git branch -D ai/session-N
```

## 维护者操作（人类）

- **开新 AI 会话**：`git worktree add ../WT_automation_ai2 -b ai/session-2`（在主目录执行，新目录自动创建）。
- **拷密钥**（见检查单）：两个 Agent 配置 + `control_live_detector.py`。
- **审查合并**：见上节。
- **推送 GitHub**：合并到 main 后由人类执行 `git push`（远程 `origin` = github.com/hanago33/WT_Automation）。
- **定期清理**：`git worktree list` 查看现有会话，废弃的及时移除。

## 附：为什么这样设计

历史教训（2026-09-09）：多个 AI 会话并发编辑主目录，导致（1）同一时间窗口内 `WT_Launcher.py` 被会话外改动 +27 行，与提交操作交叠，出处不明、无人负责；（2）根目录累积 14 个 `_diag_*`/`_tmp_*`/日志残留；（3）已提交内容面临被后续未提交改动覆盖的风险。Worktree 隔离后：主目录永不被 AI 触碰、丢弃一次实验只花一条命令、合并单位仍然是人类审查过的 commit。

---

*本文档由 2026-09-09 会话建立。修改本文档本身请走同样流程：在 worktree 里改、人类审查合并。*
