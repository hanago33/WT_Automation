# `_chain_block_if_invalid` 修改记录（2026-11-03）

## 决策
用户选择方案 C（推荐）：让 `_chain_block_if_invalid` **只拦真实致命错误，契约元数据不一致降级为告警**，解除当前 flow 还原后对运行的硬阻断，同时保留契约校验价值。

## 修改前（fail-closed 硬断）
```python
def _chain_block_if_invalid(self):
    """运行/提交前拦截：绑错板块不允许继续（fail-closed）。"""
    problems = self._chain_validate(log=True)
    if not problems:
        return False
    messagebox.showerror(
        "板块绑定校验未通过",
        "以下板块绑定与 chain_manifest.json 契约不一致，已阻止运行：\n\n{}\n\n"
        "请在对应板块重新选择正确的流程文件（或修正 chain_manifest.json）。".format(
            "\n".join("· " + p for p in problems[:10])
        ),
    )
    return True
```
**问题**：`validate_chain_manifest` 返回的 problem 混在一起。flow 被还原导致 `flowPackageId`/`${runtime.*}` 错位（7 个可降级问题）也会触发 messagebox 硬断 → 每次本地运行/远端提交都被卡住。

## 修改后（致命才阻断，契约不一致仅告警）
新增静态分类器 `_chain_problem_is_fatal` + 改写 `_chain_block_if_invalid`：

```python
@staticmethod
def _chain_problem_is_fatal(problem):
    """区分契约校验致命问题 vs 可降级问题。

    致命（真绑定错误，必须阻止运行）：
      · 绑定清单不可用 / 板块未声明 / 未知板块 / 未配置流程
      · 流程文件不存在 / 非法 JSON —— 文件层面缺漏，运行必挂
    可降级（manifest 与 flow 契约不一致，多半是 flow 被还原或 manifest 待更新）：
      · flowPackageId 不匹配（"不含 flowPackages.id="）
      · ${runtime.*} 未消费（"未消费"）
    """
    return (
        "无法读取" in problem or "不可用" in problem
        or "未在" in problem or "未知板块" in problem
        or "未配置流程" in problem or "文件不存在" in problem
        or "不是合法 JSON" in problem
    )

def _chain_block_if_invalid(self):
    """运行/提交前拦截：致命绑定问题阻止运行（fail-closed），契约不一致仅告警。

    设计（2026-11-03 修订）：flow 文件可能被还原/回滚，或 chain_manifest.json 仍
    声明旧 id/旧 runtime 键，这种情况下「板块与实际流程文件不一致」若一味硬断会让
    自动化无法启动。故分为两层：
      · 致命（文件/清单缺漏）→ 仍 messagebox 硬断，禁止继续；
      · 可降级（flowPackageId / runtime 键错位）→ 仅写入日志 + 状态提示，不阻断。
    """
    problems = self._chain_validate(log=True)
    if not problems:
        return False
    fatal = [p for p in problems if self._chain_problem_is_fatal(p)]
    degraded = [p for p in problems if not self._chain_problem_is_fatal(p)]
    if degraded:
        self._append_log(
            "板块绑定契约警告（不阻断）：{}".format("；".join(degraded[:10])), tag="warning"
        )
    if not fatal:
        self._simple_set_status_safe(
            "板块绑定契约存在 {} 项警告（不阻断运行）".format(len(degraded)), "warning"
        )
        return False
    messagebox.showerror(
        "板块绑定校验未通过",
        "以下板块绑定存在致命问题，已阻止运行：\n\n{}\n\n"
        "请在对应板块重新选择正确的流程文件（或修正 chain_manifest.json）。".format(
            "\n".join("· " + p for p in fatal[:10])
        ),
    )
    return True
```

位置：`WT_Launcher.py` ~2992-3045（静态方法 `_chain_problem_is_fatal` ≈2992-3010，实例方法 `_chain_block_if_invalid` ≈3012-3045）。

## 验证证据（全部实测通过）

### 1. 语法与 import
```
python -m py_compile WT_Launcher.py mup_input_contract.py  → 语法 OK
python -c "import WT_Launcher"                               → 无异常
assert hasattr(LauncherApp, '_chain_problem_is_fatal')       → True
assert hasattr(LauncherApp, '_chain_block_if_invalid')       → True
```

### 2. 分类器单测（10 样例全过）
```
OK  可选降级  "板块 terrain 不含 flowPackages.id=地形导入"          → fatal=False
OK  可选降级  "板块 project 未消费 ${runtime.projectName}"          → fatal=False
OK  可选降级  "板块 turbine 未消费 ${runtime.turbineImportFilePath}" → fatal=False
OK  可选降级  "板块 element 未消费 ${runtime.mastCoordFilePath}"    → fatal=False
OK  致命  "板块 x 是未知板块"                                        → fatal=True
OK  致命  "板块 x 未配置流程文件。"                                  → fatal=True
OK  致命  "板块 x 文件不存在"                                        → fatal=True
OK  致命  "板块 x 不是合法 JSON"                                     → fatal=True
OK  致命  "板块绑定清单不可用"                                       → fatal=True
OK  致命  "板块绑定清单无法读取"                                     → fatal=True
```

### 3. 集成模拟：当前仓库实际状态（7 个真实 problem）
```
total=7  fatal=0  degraded=7
BLOCKED final -> False（正确：不阻断，仅警告）
```
覆盖全部 7 个真实 problem（2 flowPackageId + 5 runtime）。→ **解除运行硬断**。

### 4. 严苛场景：致命错误仍阻断
```
Missing-flow scenario: total=8 fatal=1
  FATAL: 板块 cfd 绑定的流程文件不存在：.../不存在.json
BLOCKED final -> True（正确：flow 缺失必须阻断）
Manifest-unavailable is fatal -> True（正确）
```

## 回滚方法
本修改仅涉及 `WT_Launcher.py` 两处：
1. `_chain_problem_is_fatal`（静态方法，≈2992-3010）
2. `_chain_block_if_invalid`（≈3012-3045）

回滚到**修改前**版（fail-closed 硬断）：用「修改前」代码块整体替换这两处即可。或回滚到 **HEAD 原版**（无判据接线）：`git checkout HEAD -- WT_Launcher.py`（但会连同板块顺序换位一起还原，需另行保留）。

## 影响面
- 不改动 `validate_chain_manifest` / `mup_input_contract` 本身（校验逻辑保持精确）。
- 不改动 `_chain_validate`（log/status 行为保留）。
- 只改 `_chain_block_if_invalid` 调用端的处理：致命→阻断，可降级→警告。
- 两个调用点（remote submit 5702 / local run 6206）无需改动，语义自动继承。