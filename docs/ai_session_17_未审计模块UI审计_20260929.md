# 未审计模块 UI 审计（ai/session-17，2026-09-29）

> **性质**：只读审计，**未改动任何业务代码**。
> **审计基准**：`main @ 58717b3`（行号均为该版本）。
> **动机**：补齐 `ai_session_6` 审计时明确「未覆盖」的模块（当时范围外）：
> `build_control_map_library.py`、`build_image_template_library.py`、`text_tools.py`、
> `txt_merge_tool.py`、`control_live_detector.py`、`tools/generic_flow/*`。
> **方法**：三路只读深读（code-explorer 子代理并行）＋ 主代理复核最高优先级结论
> （实测目录规模、grep 赋值点、逐处读码闭环）。
> **证据等级**：✅ = 主代理亲自复核并附证据；◐ = 子代理报告、未逐条复核（修前请自行确认）。

---

## P0（2 项，全部 ✅ 已核实）

### P0-1 ✅ `control_live_detector.py:1209-1219`、`281-323` — 打开检测器即在 UI 线程加载全部控件库（实测 1.35 GB），界面长时间假死

- **机制**：`_load_library` 在 UI 线程同步调用 `load_master_library()` +
  `load_all_other_libraries()`；后者 `os.walk(control_maps)` 递归 `json.load` **所有** JSON，
  `skip_names` 只排除 3 个 standard 文件（**不排除 recordings/**）；`window.update()` 仅刷新一帧提示。
- **实测证据**：`control_maps` 目录 = **171 个 JSON / 1351.3 MB**（recordings 95 个未排除；
  最大单文件 51 MB，≥10 MB 的约 45 个）。
- **用户可见症状**：窗口出现后立刻"未响应"数十秒至数分钟、内存数 GB；
  每次「刷新库」、每次「合并入库」完成后都会重来一次。
- **修法**：只加载 standard + library（recordings 是采集素材，不应进匹配池）；
  加载移入后台线程 + 进度条 + 期间禁用按钮。

### P0-2 ✅ `text_tools.py:561-564`（配合 `522-523`、`633`） — CSV→XLSX/TXT 转换直接覆盖同名文件：无存在性检查、无确认

- **机制**：输出路径由源文件名派生（`name + ".xlsx"`），默认输出目录 = 源文件目录；
  `wb.save(dst)` / `csv_to_txt(src, dst)` 直接覆盖；全文件 `askyesno|askokcancel` 命中 0。
- **用户可见症状**：目录里已有用户自己的 `报表.xlsx` 时，点一次「开始转换」即被转换结果替换，
  全程无提示、不可撤销。
- **修法**：写盘前 `os.path.exists` → `askyesno`（可提供"全部覆盖/全部跳过"）。

---

## P1（✅ 已核实 10 项）

### P1-1 ✅ `build_control_map_library.py:8745-8746` — `except ... as exc` 逃逸进延迟 lambda：自动合并失败 100% 静默且功能永久停摆

```python
8745: except Exception as exc:
8746:     self.root.after(0, lambda: self._on_auto_merge_done(None, exc, backup_path))
```
- **机制**：Python 3 在 except 块结束时 `del exc`；回调执行时闭包 cell 已空 → `NameError` 被 Tk 回调吞掉。
  `_on_auto_merge_done`（8781-8782）是**唯一**复位 `_auto_merge_running` 的地方 →
  此后每次自动合并都被防重入判断静默跳过。
- **症状**：状态栏永久停在"正在自动合并入库…"，重启才能恢复。
- **修法**：except 内先 `err = exc`；复位改 `try/finally`。

### P1-2 ✅ `build_control_map_library.py:8493`（配合 `8376`、`8227`） — "清空当前结果"后悬停跟踪未停：后续补采回调读 `None` 抛异常被吞

- **机制**：清空路径把 `current_payload=None` 并 `_stop_probe_thread()`，但未调 `_stop_hover_supplement`
  （对照 8671-8672「加载控件库」路径有调）；悬停 tick 继续补采，回调 `_finish_supplement` 首行读
  `self.current_payload.get(...)` → `AttributeError` 被 Tk 吞。
- **症状**：清空后悬停红框不再跟随、不再入库；按钮文案仍显示"⏹ 停止悬停跟踪"；stderr 持续报错。
- **修法**：清空前先停悬停；`_finish_supplement` 入口加 payload 类型守卫。

### P1-3 ✅ `build_control_map_library.py:8288-8303` — 定点/选中补采在 UI 线程同步执行秒级 UIA 遍历（默认上限 45 秒）

- **机制**：`_do_point_supplement` 经 `root.after` 调度到 UI 线程后**同步**调用
  `collect_subtree_at_point`（未传 max_depth/timeout → 默认 18 层 / 45s）；
  同文件 5803-5805 注释明确该函数"秒级操作……同步执行会严重阻塞 UI"（悬停路径已走 worker）。
- **症状**：点击后主界面（含置顶浮窗）完全冻结、进度不刷新，最长 45 秒，用户易误判死机。
- **修法**：复用手动补采 worker；至少显式传 `max_depth=6, scan_timeout_seconds=8`。

### P1-4 ✅ `control_live_detector.py:1297-1305`、`1334-1335` — 监控线程直接读 Tk 变量，异常被宽 except 吞 →「检测中还活着，但什么都不更新」

- **机制**：`_monitor_loop`（线程目标）内直接 `self.depth_var.get()` / `smart_var` / `raw_view_var` /
  `backend_var`；循环尾 `except Exception: time.sleep(0.2)` 无计数、无日志、无提示。
- **症状**：偶发捕获框与匹配列表完全不刷新，状态栏仍显示"检测中"；只能反复停止/开始碰运气。
- **修法**：启动时把选项快照为普通值传入线程；except 至少记日志 + 状态栏提示一次。

### P1-5 ✅ `control_live_detector.py:1272-1281`、`1286`、`1253` — 停止后立即开始会产生两个监控线程

- **机制**：`stop_monitoring` 只置 `self.monitoring=False`，不 join、无线程代次；
  循环条件读共享标志 + `sleep(0.05/0.2)`；`start_monitoring` 只判标志并**覆盖**线程引用。
- **症状**：停止后 0.1~0.3s 内再点"开始检测"→ 两线程同时探测/刷新（CPU×2、列表闪烁、
  结果跳变），旧线程失控无法停止。
- **修法**：迭代内校验 `threading.current_thread() is self.monitor_thread`；stop 时 `join(timeout=1)`。

### P1-6 ✅ `control_live_detector.py:1717-1724`（配合 `1473-1496`） — 关窗答"是导出"但取消文件对话框 → 整场已保存控件静默丢失

- **机制**：`on_close` → `askyesno("是否导出…")` → `export_saved_controls()`（用户取消时 `fpath` 空 →
  直接 `return` 无提示）→ **无条件** `self.window.destroy()`；工具栏无独立的"导出已保存"按钮。
- **症状**：用户明确选择"是"后，在保存对话框按取消 → 窗口照常关闭，本次会话收集的控件全丢。
- **修法**：导出未成功不 destroy（保留窗口并提示）；加常驻导出按钮。

### P1-7 ✅ `control_live_detector.py:1982-2073`（配合 `2067-2068`） —「合并入库」无二次确认、无备份、非原子覆盖写目标库（可能 27 MB 主库），全程 UI 线程

- **机制**：`_execute_merge` 校验仅到"源未加载 / 源与目标同路径"，**无 askyesno**；
  写盘 `open(tgt, "w") + json.dump`（先截断，无临时文件、无备份）；
  `mergeAdded/mergeUpdated` 为**覆盖**赋值（2067-2068），与 canonical 实现（累加）行为分叉。
- **症状**：选错目标文件点一次即整库重写；写盘中断（磁盘满/进程被杀）→ 目标库截断损坏、无备份
  不可恢复；合并大库期间界面冻结。
- **修法**：合并前 `askyesno`（显示目标路径 + 新增/覆盖数）；临时文件 + `os.replace`；自动 `.bak`；
  统计改为累加并与 canonical 共用实现；后台线程执行。

### P1-8 ✅ `build_image_template_library.py:930-941`（配合 `1442-1458`、`1802-1812`） — 模板名冲突静默覆盖：删框后新增必重名，「全部成功」却丢一个

- **机制**：`add_candidate_region` 以 `len(self.candidates):03d` 命名（删框后序号空洞 → 新框与现存框重名）；
  `save_region_by_index` 不查重直接 `crop_image.save`；索引按 `(category, file_name)` 替换旧记录；
  批量保存仍按 `saved_count` 报"全部保存成功"。
- **症状**：两个同名框批量保存后，磁盘图片与索引里前者均被后者覆盖，界面显示两个都成功。
- **修法**：命名取已用序号 `max+1`（或自动 `_2`）；保存前存在性检查/自动改名；批量前预检重名。

### P1-9 ✅ `tools/generic_flow/generic_launcher.py:204-208`、`242`、`249-254` — 后台线程直接写 Tk Text（整体替换 `log_step`，绕过模块自带的 `_ui_safe_call` 保护）

- **机制**：`_append` 直接 `configure/insert/see`；`_run` 用
  `generic_automation.log_step = lambda msg: self._append(str(msg))` **整体替换**；
  `_run_worker`（后台线程）与自动化线程均会调到；被替换的原函数恰是模块中唯一经 `_ui_safe_call`
  调度、并负责落盘与状态上报的实现（模块注释明示跨线程 Tcl 重入风险）。
- **症状**：日志偶发丢失/错乱；极端情况重入崩溃；`wt_automation.log` 不再增长、run_status 不再更新。
- **修法**：`_append` 经 `root.after(0, ...)` 单一入口；包装而非替换原 `log_step`。

### P1-10 ✅ `tools/generic_flow/generic_launcher.py:29`（配合 `73`、`79`、`261`） — 提权预检是死代码：`_TARGET_PROCESS_KEYWORD` 无任何赋值点

- **机制**：全文件 grep 仅 4 处：29 赋 `""`、73/79/261 只读 → `_runs_target_elevated()` 首行即 `return False`；
  `_maybe_relaunch_elevated` / `_show_elevation_hint` 全部不可达。
- **症状**：目标软件以管理员运行时（UIPI 隔离）不会自动提权、也无提示，
  用户只看到一连串"未命中控件"，真正根因被隐藏。
- **修法**：`_run` 中同步 `global _TARGET_PROCESS_KEYWORD; _TARGET_PROCESS_KEYWORD = kw`。

---

## P1（◐ 子代理报告，未逐条复核 —— 修前请自行确认）

| 位置 | 症状 |
|---|---|
| `build_control_map_library.py:9341-9344`（配合 `6500-6507`） | 合并入库成功后未重建分组/未重置勾选 → 树显示旧组号配新数据；新库更短时可能 `IndexError` 被当成"保存失败"弹窗；之后保存会按旧勾选写出错误库文件 |
| `build_image_template_library.py:1460-1481`、`818-823`、`1818-1821` | 保存/导出/打开目录回调全部无 try → 路径异常时"点了没反应"；且"先落图后写索引"失败留半成品 |
| `build_image_template_library.py:1246-1253`、`1408-1504` | 手工改好文件名后切换候选框 → 输入被静默丢弃（无 `<FocusOut>`/`<Return>` 提交） |
| `build_image_template_library.py:1397-1440`、`329-376` | OCR 命名在 UI 线程串行起子进程且无 timeout → 批量命名界面假死、不可取消 |
| `build_image_template_library.py:825-842` | 重新加载截图清空候选框**并清空撤回栈**，无确认、无未保存标记 |
| `text_tools.py:518-552`、`908-940` | 转换/处理在 UI 线程同步执行（仅 `update_idletasks`）→ 窗口点不动、无取消 |
| `text_tools.py:546-547`、`934-935` | 全部文件失败仍弹"转换完成！/处理完成！"，与日志矛盾 |
| `txt_merge_tool.py:342-371` | 合并全程 UI 线程同步、无进度、无取消 |

---

## P2 汇总（◐ 子代理报告）

- **控件库采集器（build_control_map_library.py）**：悬停"连续错误自动禁用"不清理（置顶不回退/全局热键不停/红框 overlay 泄漏）；
  `group.get('id')` 键不存在导致兜底展开恒空转；`7195-7196` 同类 `except as exc` 延迟引用（检验定位按钮空转 90 秒）；
  改名输入未点"应用到当前控件"即被保存流程忽略；「加载控件库文件」覆盖当前结果无确认；
  画框 overlay 构造失败主窗口不恢复（"界面消失"）；`psutil.process_iter` 在 UI 线程周期枚举；
  高亮 `after` 未存 id（旧 overlay 销毁后回调 TclError）；补采复用扫描进度回调把窗口标题改后不恢复 + 悬停日志文件无上限。
- **实时检测器（control_live_detector.py）**：双击匹配行/回车保存的永远是第 1 行；
  `after` 未存 id 且 1.5s 高亮被下一次刷新冲掉；暂停按钮 Enter/Leave 还原初始底色（色态与状态语义不符）；
  合并台账覆盖而非累加（已并入 P1-7）；`load_all_other_libraries` 来源列表按累计列表判定（死逻辑）。
- **模板制作器（build_image_template_library.py）**：滚轮 `int(delta/120)` 未复用 `wt_wheel_router`（触控板零响应）；
  「截屏(3秒后)」用 `update()+sleep` 阻塞且可重入；`templates_index.json` 非原子写（中断即索引损坏）；
  拖动每帧全量重建候选列表与预览；`<Delete>` 绑在根窗口（文件名输入框里按 Del 同时删框）。
- **小工具（text_tools / txt_merge_tool）**：目录扫描/建目录回调无 try；参数控件不随模式联动禁用；
  日志面板无行数上限；拆分行数无校验（0 抛英文错误 / 负数静默产出 0 文件）；「清空列表」无确认；
  OLE 拖拽 COM 初始化缺失或顺序错误；TXT 合并存在两份近乎逐行复制的实现。
- **generic_flow**：流程结束后监视窗置顶/抢焦点后永不恢复；关窗只置停止标志、流程卡死时无法关闭（无强退兜底）；
  `_ui_safe_call` 自身与注释矛盾（后台线程 `winfo_exists`/兜底直调 UI）；
  `log_step` 写文件无保护（文件被占用 → 流程失败且原因不可读）；
  `generic_main_window._pid_of` 用"精确等于进程名"比较而调用方传完整路径（兜底恒空）；
  生成的兜底代码用相对导入（顶层导入场景恒 ImportError 被静默吞）；生成/打包脚本替换不校验、覆盖写不备份；
  launcher/文本工具界面状态全部只存内存（关窗即丢）。

---

## 建议修复顺序

1. **立即（低成本高收益，合计 < 30 行）**：P1-1（exc 逃逸）、P1-2（清空停悬停）、P1-10（死代码 1 行）、
   P0-2（覆盖确认）、P1-6（关窗导出保留窗口）。
2. **P0-1 库加载治理**（范围收敛 + 后台化）：同时缓解检测器 CPU 打满（子代理 #2）与内存峰值，
   建议与"recordings 是否应进匹配池"的约定一起定。
3. **P1-3/4/5/7/8/9**：线程纪律与写盘安全（均可参照本仓既有模式：worker + `_post_ui`、
   temp + `os.replace`、主线程快照）。
4. **P2 批次**：按模块排期，建议优先"静默失败/数据覆盖"类（模板库重名、索引原子写、日志上限）。

## 覆盖度与未覆盖

- **已通读**：`build_control_map_library.py`（GUI 类全类 + 辅助函数）、`control_live_detector.py` 全文、
  `build_image_template_library.py` 全文、`text_tools.py` 全文、`txt_merge_tool.py` 全文、
  `tools/generic_flow/` 的 GUI 相关文件（launcher / main_window / 生成与打包脚本；
  `generic_automation.py` 的 GUI 面：MonitorWindow / `_ui_safe_call` / 任务栏进度 / 停止与收尾分支）。
- **未覆盖**：`generic_flow_locator.py`（448 KB，纯逻辑无 GUI）、`generic_automation.py` 的纯逻辑部分、
  图像算法与 OCR 质量、评分数值合理性、DPI/渲染细节；未实机运行（依赖运行时行为的判定均已标 ◐/疑似）。
- **免除**：「重复打开」维度中，检测器/模板制作器在 `WT_Launcher.py` 已有单例守卫（session-15 已覆盖），
  故未列缺陷。

> 附注：审计执行期间发现 `WT_automation_ai16` 工作区被**并行会话**占用
> （其分支上有提交 `6c2313e`，修复 session-14 遗漏的"切步确认被普通点击绕过"，与本报告范围无重叠）。
> 本报告全程只读，基准为 `main @ 58717b3`。
