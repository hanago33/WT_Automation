# 审查证据与复现脚本 —— 使用说明

> 本目录是 `ai_session_5_日志体系优化_审查归档_全轮次_20260917.md` 的配套证据。
> 归档时间：2026-09-17

## ⚠️ 跑之前必读

这些脚本是**审查过程中随手写的探针**，不是正式测试套件。使用前注意：

1. **脚本内硬编码了审查者的临时路径**，例如：
   ```python
   ROOT = r"C:\Users\14830\AppData\Local\Temp\dsh-pNekd0\ai5_testrun"
   ```
   请改成你自己的可写副本路径（见下方「准备工作」）。

2. **不要直接在 `ai5` 工作区里跑** —— 沙箱可能禁止写入，会导致
   `JsonlSidecarTests` 失败与 `log_step` 自旋（**这是环境问题，不是代码缺陷**）。

3. 脚本**不会修改 `ai5` 源工作区**（只读 + monkeypatch 落盘函数）。

## 准备工作

```powershell
# 1) 复制工作区到可写目录
$SRC = 'D:\My_RF_Project\WT_automation_ai5'
$DST = "$env:TEMP\ai5_testrun"
robocopy $SRC $DST /E /NFL /NDL /NJH /NJS /NP `
  /XD .git __pycache__ .pytest_cache .tmp_pytest .tmp_pytest_single .qoder .workbuddy-ai .codebuddy `
  /XF *.pyc

# 2) 把脚本里的 ROOT 改成 $DST
# 3) 用项目解释器跑
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
& 'D:\Program Files\Python38\python.exe' <脚本>
```

> 只有 `D:\Program Files\Python38\python.exe`（3.8.10 / pytest 8.3.5）有 pytest 与 pywinauto。

## 脚本索引（按优先级）

### 三项 P0 的复现（最重要）

| 脚本 | 验证什么 | 期望输出 |
|---|---|---|
| `testI_blocked.py` | **P0-1** `blocked_by_self_window` 分支丢日志 | 进入该分支但**无任何日志说明原因**；`RESULT=I_OK` |
| `testK_e2e_banner.py` | **P0-2** 真实调用 `_log_run_end_banner()`，成功运行实际写出 `[ERROR]` | 成功/失败两条 banner **均为 ERROR** |
| `testF_jsonl_gate.py` | **P0-3** JSONL 被门控挡住 | 默认档 2 条 DEBUG → JSONL 只 1 条 |
| `testM_query_p02.py` | **P0-2 扩散**到 `wt_log_query` | 成功运行被统计出 `ERROR 1`；ERROR 过滤只返回成功 banner |

### 冻结缺陷

| 脚本 | 验证什么 |
|---|---|
| `testL_freeze_real.py` | **真实 `tk.Text` 控件**上复现冻结（服务端 305 行，控件停在第 299 行） |
| `testG_freeze.py` | 冻结缺陷的模型仿真（早期版本，结论一致） |
| `testO_buffer_vs_freeze.py` | 两个日志面板与冻结的关系（**注意：其中关于「总控台会冻结」的推断已被证伪**，见归档 §7） |

### 已确认无问题的验证（用来确认「不用改」）

| 脚本 | 验证什么 |
|---|---|
| `ast_contract.py` | AST 全仓契约扫描（34 入口 / 148 处 `log_step`）→ 零问题 |
| `testH_bold.py` | 加粗**确实生效**（真实 Tk + `tkfont` 解析 `weight='bold'`） |
| `testN_step_filter.py` | 步骤过滤在真实数据上**可用** |
| `testA_jsonl.py` | JSONL 写入格式正确（中文不转义、空字段省略、elapsedMs 取整） |
| `testB_logstep.py` | `log_step` 门控行为正确 |
| `testC_colors.py` | 色板收口一致性 |
| `testJ_generic.py` | 生成产物分叉量化 |

### 环境问题定位

| 脚本 | 用途 |
|---|---|
| `repro_spin.py` | 用 `faulthandler` 定位沙箱禁写导致的自旋点 |
| `baseline_probe.py` | 基线探针 |

### diff 快照

| 文件 | 内容 |
|---|---|
| `p1.diff` / `p1b_prune.diff` | P1 降噪阶段 |
| `p2_jsonl.diff` / `p2_rotate.diff` | P2 JSONL / 轮转阶段 |
| `p3_style.diff` | P3 样式阶段 |
| `committed.diff` | 当时已提交的完整 diff |
| `uncommitted.diff` | 当时未提交的 diff |
| `pending.diff` | 待处理 diff（59 KB） |

> **注意**：这些 diff 是**审查期间的快照**，与当前 HEAD（`6b0346c`）已有差异。
> 需要最新 diff 请直接 `git diff main...ai/session-5`。

### 其他

| 文件 | 说明 |
|---|---|
| `wt_logging.py` | 审查期间从 `ai5` 拷出的 `wt_logging.py` 副本（11 KB），供独立探针 import |
| `test_log_noise_reduction.py` | 实施会话的降噪测试文件副本 |

## 提醒

- 归档文档 §7 列了**审查者自己报错的 7 条**，其中若干已在文档中更正。
  **以归档 §3/§4 的当前版本为准**，勿采信脚本注释里的早期推断。
- 脚本输出含中文，Windows 控制台可能显示乱码。可加 `$env:PYTHONIOENCODING='utf-8'`，
  或重定向到文件后用编辑器查看。
