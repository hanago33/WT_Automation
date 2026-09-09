# 会话沉淀：远程流程一致性修复——worker 消费 flowPath、任务携带项目参数、依赖校验（20260903）

> 范围：`wt_task_server.py`（worker env 注入 + submit 收 runtimeConfig + paramTable 校验 +
> 调度器自愈 + 409 中文化）/ `wt_task_queue.py`（runtime_config 列）/ `WT_AUT_recorded.py`
> （空步骤报错带流程路径）/ `WT_Launcher.py`（_prepare_remote_sections 项目解析注入）/
> `wt_task_queue_window.py`（控制按钮置灰 + name/runtimeConfig 上行 + 结果可见化）/
> `tests/test_simple_remote_p1.py`、`tests/test_remote_flow_parity.py`（新）
>
> 关联文档：《会话沉淀_远程任务未触发服务器_总控台地址指向本机与一键会话修复_20260902.md》
>
> 结论：**远程执行与本地提交内容脱节的总根因 = 任务记录里的 flowPath 只进日志、
> 不进执行——worker 永远跑服务器上 `workspace/flow_definition.json` 旧链路。**
> 上传/存储/版本归档机制本身早已存在且健壮，断的是"最后一环"。

---

## 〇、一句话索引（症状 → 根因 → 修复）

| 症状/问题 | 根因 | 修复 |
|---|---|---|
| 远程任务执行行为与本地提交的流程完全无关 | worker 不消费 flowPath（P0-1） | worker env 注入 `WT_FLOW_DEFINITION_FILE` |
| 远程任务拿不到项目参数（半径/风机/Cp/塔） | 任务不携带运行时参数（P0-2） | 任务记录新增 runtimeConfig + worker 注入 `GM_RUNTIME_CONFIG_JSON` |
| 多塔流程远程只跑一次/默认塔 | 远程提交无本地多塔展开等价物 | `_prepare_remote_sections` 按塔展开（towerMode=single） |
| 排队任务到执行期才发现参数表缺失 | 无依赖校验（P1） | submit 时校验 paramTable 在服务器存在 |
| 日志 `no such table: tasks` 每秒刷屏 | 测试遗留线程 + init_db 缓存 + db 被删 | 调度器自愈重建表 + 测试隔离日志路径 |
| running 任务点取消必 409 / 删除报错难懂 | UI 不拦 + 错误信息英文 | 按钮按状态置灰 + 409 中文说明 |

---

## 一、决定性根因（P0-1）：flowPath 从未到达 worker

### 1.1 证据链
- `wt_task_server.default_worker_launcher`：命令行只有 `--task-id/--task-user/--task-db/...`，
  **无 flowPath、无 `--flow` 参数；`subprocess.Popen` 未传 `env`**。
  flowPath 唯一去向是日志行 `[queue] starting task ... flow=...`。
- `WT_AUT_recorded.py:75`：`FLOW_DEFINITION_FILE = os.environ.get("WT_FLOW_DEFINITION_FILE",
  <项目目录>/workspace/flow_definition.json)`——无人设 env → 永远跑 workspace 旧链路。
- 本地 workspace/flow_definition.json 停留在 2026-07-15（46 步）。
- 2026-08-31 任务日志：记录 `flow=flow_0.json`，但执行步骤列表与 workspace 旧链路吻合。

### 1.2 修复
`default_worker_launcher` 在 Popen 前构造 `worker_env`：
- flowPath 非空 → `WT_FLOW_DEFINITION_FILE=<服务器端上传落盘路径>`；
  **文件缺失时故意不回退**——worker 以"流程文件不存在"显式失败，不静默跑旧流程。
- worker 端 paramTable 相对路径按该文件所在目录解析 → 自动对齐服务器
  `flow_packages/param_table_*.xlsx`（与本地行为一致）。
- `WT_AUT_recorded.py` 空步骤报错信息带上 `FLOW_DEFINITION_FILE` 实际路径。

### 1.3 上传/存储机制澄清（原本就是好的）
客户端读**文件内容**上传（非仅路径）：sha256 与服务器版本一致则跳过上传（幂等）；
服务端 `/api/flows/upload` 原子写入 `flow_packages/<名>.json`，旧版归档 `<名>.vN.json`
+ 台账 `flow_package_registry.json`。断的只是"任务→worker"这一环。

---

## 二、任务携带运行时参数（P0-2）

### 2.1 服务端
- `wt_task_queue.py`：tasks 表新增 `runtime_config TEXT DEFAULT ''`
  （建表 DDL + `_MIGRATION_COLUMNS` 自动补列，旧库无需手工迁移）；
  `_COLUMN_TO_KEY` 映射 `runtimeConfig`；`_row_to_task` 反序列化为 dict；
  `submit_task(..., runtime_config=None)`。
- `_handle_submit`：接收 payload `runtimeConfig`（必须 JSON object，否则 400）。
- `default_worker_launcher`：runtimeConfig 非空 → env 注入
  `GM_RUNTIME_CONFIG_JSON`（优先级与本地一致：env > 流程文件内 runtimeConfig）。

### 2.2 客户端（对齐本地 Simple 运行样板）
`WT_Launcher._submit_sections_to_remote` 入口先过 `_prepare_remote_sections`：
- **未指定项目工作文件夹**：原样返回（行为与旧版一致）。
- **指定项目工作文件夹**：逐板块 `parse_project_work_dir`（含 flow_path 构造
  text_overrides），覆盖应用后写 `workspace/flow_definition_remote_tmp_<key>.json`
  上传；板块带 `name`（原始文件名，上传/幂等比对用）与 `runtimeConfig`。
- **paramTable 保持相对路径**（本地运行是转绝对路径，远程不能转——服务器路径
  不同，交由 worker 按服务器 flow_packages 解析）。
- **逐塔录入类流程**（新建气象数据/气象数据录入）多塔时按 `parse_all_masts`
  展开为 N 个板块任务（标题"板块·塔名"、towerMode=single），缺数据塔跳过并
  记日志；与本地多塔串行队列语义一致。mastId 单塔选择对非录入流程生效。
- `_write_project_tmp_flow` / `_for_mast` 新增 `absolutize_param_table`、`tmp_name` 参数。

---

## 三、P1：依赖校验与可见化
- 服务端 submit：流程 `paramTable` 为相对路径且在服务器 flow_packages 内不存在 →
  400「paramTable not found on server: xxx（参数表未随发布包部署到服务器…）」，
  把"执行期莫名失败"提前到"提交时明确拒绝"。
- 客户端提交结果：`已提交（含项目参数）`、`已提交（含项目参数，内容未变化，跳过上传）`；
  提交完成汇总显示"含项目参数 N 个"。逐板块日志行带参数标记。

---

## 四、附带修复（本会话早前完成）
1. `tests/test_simple_remote_p1.py` 两个内联 TaskServer 补 `server_log_path/task_log_dir`
   → 真实 `logs/task_server.log` 零测试污染。
2. 调度器自愈：`no such table` 类异常自动 `init_db(force=True)` 重建表（运维清库/
   测试遗留线程场景不再每秒刷错），只记一条自愈事件。
3. 队列窗口控制按钮按所选任务状态置灰（取消仅 pending、终止仅 running、删除非
   running、继续 paused/failed/terminated、暂停 pending/running；未选中全禁用），
   挂接选中事件/列表刷新/窗口初始三处。
4. 服务端 409 中文对照表 `_CONTROL_ERROR_ZH`（保留原英文便于检索日志）。

---

## 五、验证
- `tests/test_remote_flow_parity.py`（新，13 个用例）：worker env 注入、runtimeConfig
  入库回读/旧库迁移、HTTP 提交回显与 paramTable 拒绝、端到端（真实调度器+真实
  Popen+探针子进程验证 worker 收到上传流程与参数）、客户端板块上行、多塔展开。
- 全量回归 682 passed（`test_plan_optimizations` 一个失败为工作区既有未提交改动
  `wt_flow_executor.py` listitem 标记所致，与本批无关）。
- **测试写自动化子进程时必须 patch `wt_task_server.AUTOMATION_SCRIPT`**——
  本次曾漏 patch 导致真实 worker 被短暂拉起（已由 server_close 清理，无副作用）。

---

## 六、Lesson
1. **"接收了"≠"会执行"**：判断链路完整性要看最后一跳（进程启动参数/环境），
   不能只看存储与日志。flowPath 三处一致才算通：客户端上传名 → 任务记录 → worker env。
2. 远程与本地同一功能（项目参数注入）必须走同一机制（GM_RUNTIME_CONFIG_JSON），
   否则行为必然漂移。
3. 路径类参数（GM_EXE/输出目录/项目工作目录）属服务器环境，应由服务器配置/部署
   提供；客户端只传业务参数。项目工作文件夹若远程使用，须在服务器同路径准备数据。
4. 给测试造"会 spawn 子进程"的服务器时，AUTOMATION_SCRIPT 必须打桩。

---

## 七、部署清单（上线本批修复）
1. `python make_release.py` 重新打包（含 wt_task_server/wt_task_queue/WT_AUT_recorded/
   WT_Launcher/wt_task_queue_window + 新测试）。
2. 服务器：`服务器一键会话修复.bat 发布包_xxx.zip`（部署+重启+诊断一步）。
   旧库 runtime_config 列由服务启动时自动迁移，无需手工处理。
3. 本地：**重启总控台**加载新客户端代码（远程提交的项目参数注入在客户端）。
4. 验证：本地提交一个板块 → 提交结果显示「含项目参数」→ 服务器 `logs\tasks\`
   新日志的 `[queue] starting task ... flow=<flow_packages 内路径>` → 任务执行步骤
   与所提交板块一致 → 服务器 MUP 界面状态随之变化。

---

## 八、追加（20260904）：窗口构建回归与发布

### 8.1 生产反馈三个症状 → 同一根因
首轮部署后用户反馈：①本地/服务器都看不到任何运行日志；②Simple 勾选板块点
"提交所选板块到远程队列"后只弹了队列窗口，无任何提交反馈；③队列窗口里
"只看我的任务""停止队列服务"等 UI 消失。

根因（单一）：上批给 `_build_queue_tab` 加"初始禁用控制按钮"时，`_update_control_buttons()`
被插在 **task_tree 创建之前**（约 214 行 vs 312 行）→ `_selected_task_id()` 里
`self.task_tree.selection()` 抛 **AttributeError** → `_build_queue_tab` 构建中断 →
后半段 UI（只看我的/停止服务/任务树/日志面板/统计区）全部缺失；`open_task_queue()`
里异常被 try/except 吞掉，`_task_queue_window=None` → 板块提交链路静默返回、
`_simple_remote_submitting` 标志不复位 → 症状②③；窗口残缺导致日志面板不存在 → 症状①。

> **Lesson（UI 构建改动必须整窗冒烟）**：单元测试都是"绕过构建直接调方法"，
> 没有一条测试真正把窗口完整建出来。本次新增 `QueueWindowBuildSmokeTests`
> （真实 Tk 完整构建，断言 task_tree/log_text/mine_only_var/按钮初始禁用/选中启用），
> 已补上这层防护。

### 8.2 修复内容
1. `_selected_task_id`：`task_tree` 未创建/已销毁时返回空串（防御式）。
2. 初始置灰调用移到 `_build_queue_tab` **末尾**（全部组件建完之后）。
3. `_submit_sections_to_remote`：窗口创建失败时复位提交标志+恢复按钮+状态栏
   提示"远程提交失败：任务队列窗口创建异常"（不再静默）。
4. 多塔展开上传名按塔区分：`flow_definition_新建气象数据CFT01.json` 等，
   避免同名互覆/版本归档混淆（幂等跳过判定按 名字+sha256，同名不同内容
   会导致每塔都触发重新上传并归档一版）。

### 8.3 流程仓库页签（20260904 追加）
队列窗口新增第三页签「流程仓库」，把已有但无前端的版本台账可视化：
- 左侧流程列表（flow_packages 内全部流程：版本数/最新上传人/最新上传时间）；
- 右侧版本历史（每版：版本号/上传人/上传时间/sha256 前 12 位/是否当前版；
  无上传记录的显示"随发布包部署"）；
- 底部**关联任务与项目参数**：按 flowPath 匹配最近 20 条任务，逐条展示
  taskId/状态/提交人/进度 + **runtimeConfig 全量键值**（谁带着什么项目参数提交的
  这个流程，一目了然）；无参数任务标注"流程文件自包含或旧版提交"。
- 数据源：GET /api/flows（台账）+ GET /api/tasks?limit=200；跟随队列自动刷新
  （独立"随队列自动刷新"开关，默认开），手动"刷新"与窗口打开时也拉一次；
  刷新后保持已选流程，无选择时默认选第一个。
- 测试：test_remote_flow_parity.py 新增仓库渲染/版本关联/参数展示用例
  （真实 Tk 构建），全套 15/15。

### 8.4 附带修复（回归中发现）：UI-TARS 日志同名覆盖
`wt_projection_helpers.run_ui_tars` 日志名时间戳 `%Y%m%d_%H%M%S_%f` 在 Windows
上微秒段精度约 15ms——**连续两次调用 99%+ 同名**（实测 200 组双采样 199 组相同），
连续超时/失败时 stdout/stderr 日志互相覆盖。修复：文件名追加进程内单调递增序号
`_UI_TARS_LOG_SEQ`，任意两次调用必不同。此前 test_round2_hardening 该用例
间歇失败即此因（单跑通过、与其他用例连跑触发），修复后稳定通过。

### 8.5 既有未处理项（不属本批）
`test_plan_optimizations` 2 个用例失败：工作区既有未提交改动
（wt_flow_executor.py 的 listitem 不可读标记 + wait 看门狗线程化）与旧断言
（"wrapper 复用不重定位"）冲突——stash 改动后基线同样失败，属上个会话遗留，
待该改动定稿时同步更新断言。

### 8.8 客户端/服务器端视觉区分（20260904 追加）
同一份队列窗口既在本地机（连远程服务器）也在服务器本机（连 127.0.0.1）使用，
两个界面完全同款曾直接导致 20260902 的"127.0.0.1 发错机器"事故排障困难。
现按**任务服务地址动态判定运行角色**，窗口顶部新增整行角色横幅 + 标题栏后缀：
- 地址 ∈ 本机（127.0.0.1/localhost/本机任一真实 IP）→ **绿色横幅**
  「服务器本机控制台 —— 任务队列服务运行在本机，操作直接影响本机 MUP」，
  标题「任务与服务器监控【服务器本机】」；
- 地址指向其他机器 → **蓝色横幅**「远程客户端 —— 任务将发送到服务器 <IP> 执行
  （本机 IP：<本机 IP>）」，标题【远程客户端】；目标/本机 IP 对照可当场发现
  地址填错（如总控台在内网本地机却填了 127.0.0.1 → 横幅变绿即示警）；
- 地址为空 → 灰色「未配置任务服务地址」。
- 地址栏**输入过程中**横幅实时预判（连接仍用旧地址，点"刷新"才切换 base_url）；
  刷新/重新打开窗口也会重判。判定基于 `_local_ipv4s()`（getaddrinfo 收集本机全部
  IPv4，无网络请求）。
- 测试：RoleBannerTests（角色矩阵 loopback/真实IP/远程/空地址、标题后缀、
  输入框预判不切连接），全套 15/15；相关回归 125 passed。
- 发布包：`release_out/发布包_20260904_113123_9ea7726.zip`。

### 8.7 版本回滚（20260904 追加）
流程仓库页签补齐"回滚"闭环：
- 服务端新增 `POST /api/flows/rollback` `{name, version, user}`：把台账中指定
  历史版本内容原子恢复为当前文件；**当前内容自动按下一版本号归档**（复用 upload
  的归档语义，台账只增不减——回滚在台账里表现为一次新"上传"，user=回滚操作者，
  记审计事件 flow_rollback）。目标版本 sha256 == 当前内容 → 幂等成功（200，
  rolledBack=False，零写操作）；版本不存在 → 404。
- 客户端版本历史区新增「回滚到此版」按钮：选中历史版本 → 确认弹窗（明示
  "当前内容会自动归档，历史不丢"）→ 后台调接口 → 成功/幂等/失败分别弹窗，
  完成后自动刷新仓库。
- 注意：回滚只影响**后续提交的任务**；已入队任务记录的 flowPath 不变。
- 测试：回滚 API（归档/台账 3 版/幂等/404）+ 客户端 payload 调用链，全套 14/14；
  相关回归 137 passed。台账文件为 `flow_packages/.flow_versions.json`（隐藏文件，
  非 flow_package_registry.json）。
- 发布包：`release_out/发布包_20260904_111950_9ea7726.zip`。

### 8.6 发布包
`release_out/发布包_20260904_110152_9ea7726.zip`（46 文件 3.7MB）：
窗口构建修复 + 提交标志复位 + 多塔上传名 + **流程仓库页签** + ui_tars 唯一序号
+ P0/P1 全部改动 + 测试与文档。

部署顺序（勿反）：服务器先解压覆盖 → `服务器一键会话修复.bat`（重启服务）→
本地解压覆盖 → **本地重启总控台**（客户端修复在 launcher/窗口代码里，必须重启进程）。
1. `python make_release.py` 重新打包（含 wt_task_server/wt_task_queue/WT_AUT_recorded/
   WT_Launcher/wt_task_queue_window + 新测试）。
2. 服务器：`服务器一键会话修复.bat 发布包_xxx.zip`（部署+重启+诊断一步）。
   旧库 runtime_config 列由服务启动时自动迁移，无需手工处理。
3. 本地：**重启总控台**加载新客户端代码（远程提交的项目参数注入在客户端）。
4. 验证：本地提交一个板块 → 提交结果显示「含项目参数」→ 服务器 `logs\tasks\`
   新日志的 `[queue] starting task ... flow=<flow_packages 内路径>` → 任务执行步骤
   与所提交板块一致 → 服务器 MUP 界面状态随之变化。
