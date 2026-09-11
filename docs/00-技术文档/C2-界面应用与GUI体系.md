# C2 · 界面应用与 GUI 体系

> 本篇回答：**三个 tkinter 主程序长什么样、UI 质感层怎么组织、改 GUI 时必须守哪些纪律**。
> 读者：UI 二次开发者、要加面板/加窗口的人。

---

## 1. 应用总览

| 应用 | 行数 | 定位 | 启动方式 |
|---|---:|---|---|
| `WT_Launcher.py` | 9 179 | **总控台**：流程选择 / 参数配置 / 执行 / 队列 / 监控 / 流程图 / UI-TARS 配置 | `启动WT自动化总控台*.bat` |
| `WT_Flow_Editor.py` | 10 726 | **流程编辑器**：新建/编辑/校验/保存流程定义 | 总控台内打开，或独立启动 |
| `WT_AUT_recorded.py` | 2 817 | **执行入口**（也是 worker 入口）：装配运行配置 + 编排 + 监视窗口 | `.bat` / `wt_task_server` 拉起 |

三者共用**同一套质感层**：`wt_theme`（设计令牌）、`wt_dpi`（缩放）、`wt_wheel_router`（滚轮）。

---

## 2. `WT_Launcher.py` · 总控台

### 2.1 主要类型

| 类型 | 行号 | 说明 |
|---|---|---|
| `LauncherApp` | **L1833–9054（约 7 220 行）** | 主应用类。⚠️ 超大，改动风险高 |
| `ServerMonitorWindow` | L1611 | 服务器监控窗口 |
| `RelativeRegionHelperDialog` | L407 | **相对区域辅助对话框**（量取亲戚区域坐标的神器） |

### 2.2 关键函数

| 函数 | 行号 | 作用 |
|---|---|---|
| `load_project_settings()` | L130 | 读取项目设置（`launcher_state.json` / `PROJECT_SETTINGS`） |
| `load_flow_runtime_config(flow_definition_path=None)` | L203 | 加载运行时配置 |
| `load_effective_flow_payload(flow_definition_path=None)` | L339 | **聚合注册表 + 目标流程**得到"有效 payload" |
| `validate_effective_flow_payload(...)` | L356 | 校验有效 payload |
| `validate_imported_flow_payload_or_raise(payload, source_label="导入流程")` | L362 | 导入流程时强校验 |
| `_merge_target_with_registry(target_payload, registry_payload)` | L298 | 与注册表合并 |
| `_inject_mast_config_xml_override(flow_path, text_ovr, rc, log_fn=None)` | L78 | 注入测风塔 XML 覆盖 |
| `_read_log_tail(path, max_lines=20)` | L192 | 读日志尾部 |

### 2.3 提权相关（UIPI 需要）

| 函数 | 行号 |
|---|---|
| `_process_integrity_tier(pid)` | L9055 |
| `_runs_target_elevated()` | L9089 |
| `_should_relaunch_elevated()` | L9111 |
| `_relaunch_elevated()` | L9122 |
| `_show_elevation_hint()` | L9140 |
| `_maybe_relaunch_elevated()` | L9155 |
| `main()` | L9168 |

> 💡 **为什么总控台要自提权**：目标程序（MUP）常以管理员运行。按 UIPI，低完整性级别进程无法向其发送输入/查询 UI。所以入口 `.bat` 做管理员提升，程序内还有一套检测与重新提权逻辑（见 B1 §7）。

### 2.4 UI-TARS / VLM 配置区

配置项通过环境变量透传给子进程（L5989–5997）：

```
UI_TARS_REPO_ROOT / UI_TARS_CLI_CONFIG / UI_TARS_API_KEY /
UI_TARS_MODEL / UI_TARS_VLM_BASE_URL / UI_TARS_USE_RESPONSES_API /
WT_ENABLE_AI_INTERVENTION / WT_AI_INTERVENTION_LOG_LINES
```

开关文案（L4957–4958）明确写着：**"仅在普通执行和模板兜底都失败后调用 UI-TARS"**。

---

## 3. `WT_Flow_Editor.py` · 流程编辑器

### 3.1 主要对话框

| 类型 | 行号 | 作用 |
|---|---|---|
| `NewDefaultChainDialog` | L1126 | 新建默认链路 |
| `ControlPickerDialog` | L1231 | 从控件库挑选控件 |
| `ControlEditorDialog` | L1394 | 编辑单条控件定义 |
| `_make_dialog_window(...)` | L74 | 通用对话框窗口工厂（统一尺寸/最小尺寸/关闭回调） |

### 3.2 关键函数

| 函数 | 行号 | 作用 |
|---|---|---|
| `sync_flow_package_registry(source_definition_path, runtime_config, flow_packages, steps)` | L894 | **保存时自动同步注册表**（不必手改大文件） |
| `sync_launcher_flow_definition_path(source_definition_path)` | L924 | 同步总控台当前流程路径 |
| `collect_flow_package_step_ids(flow_packages)` | L865 | 收集包内 stepId |
| `resolve_initial_definition_path()` | L878 | 决定初始打开哪个流程定义 |
| `normalize_step(step, index)` | L978 | ⚠️ **白名单重建**步骤——新增字段必须在此显式放行 |
| `build_locator_recommendation(parsed)` | L937 | 定位推荐 |
| `parse_inspect_text(raw_text)` | L942 | 解析 Inspect 文本 |
| `normalize_control(control, index)` / `normalize_control_type_name(...)` | L946 / L933 | 控件归一化 |
| `build_synthetic_inspect_text(inspect_data)` | L974 | 反向生成 Inspect 文本 |
| 控件库回写族 | `_build_control_library_category`(L986) / `_build_control_library_control_entry`(L1017) / `_build_control_library_payload`(L1045) / `_merge_controls_into_library_payload`(L1067) | 把编辑器里的控件定义写回控件库 |
| `_launch_python_script(script_path)` | L958 | ⚠️ 已知重复：文件内 4–5 处几乎相同的 `subprocess.Popen` |
| 控件库读取 | `_load_control_library_controls(refresh=False)` | L1359 |

### 3.3 已知技术债（`FlowEditorApp` 约 4 450 行上帝类）

| 问题 | 建议 |
|---|---|
| 4–5 处重复的 `subprocess.Popen([sys.executable, script], cwd=BASE_DIR, ...)` | 抽 `_launch_script(script_path)` 统一收口（顺带解决孤儿进程不回收） |
| `_strip_wrapping_quotes` 在编辑器与 `flow_recorder_converter` 各一份；`slugify` 在三个采集器各一份 | 上提到 `wt_flow_editor_utils.py` 或新建 `common/textutil.py` |
| 后台线程直接碰 Tk widget | 统一 `_run_async(task, on_done)`，只经 `root.after(0, on_done)` 回主线程 |
| GUI 与业务混杂 | 抽 `FlowDocumentService`（拼 JSON + 校验 + 写文件 + 同步注册表），使保存/校验/撤销可脱离 Tk 测试 |

> ⚠️ **Tk 线程安全是硬约束**：从非主线程调 `root.update()` 会**递归进入 Tcl 事件循环** → access violation 原生崩溃，Python 抓不到。详见 B1 §8。

---

## 4. `WT_AUT_recorded.py` · 执行入口 / Worker

### 4.1 主要类型与函数

| 成员 | 行号 | 作用 |
|---|---|---|
| `MonitorWindow` | L318 | 执行监视窗口（log / update_status / set_success / set_error） |
| `_ui_safe_call(callback)` | L461 | **跨线程 UI 调用的唯一通道**：有 mainloop 时 `root.after(0, cb)`，否则直调兜底 |
| `log_step(step_name)` | L484 | 步骤日志 |
| `_TaskbarProgress` | L111 | 任务栏进度（**必须在主线程创建**） |
| `_init_taskbar_progress()` | L245 | 初始化 |
| `_update_taskbar_progress(current, total, status)` | L252 | 更新 |
| `_update_progress_title(current, total, step_name, status)` | L269 | 标题更新 |
| `_load_flow_payload()` / `_load_flow_definition()` | L592 / L1041 | 加载流程 |
| `_apply_param_table_expansion(merged_payload, target_payload)` | L738 | **参数矩阵展开** |
| `_load_runtime_config()` / `_apply_runtime_config()` | L949 / L1017 | 运行时配置 |
| `_apply_json_env_runtime()` | L726 | 从环境变量注入运行时 |
| `_auto_capture_template(step_id, step_definition)` | L1167 | 执行中自动采模板 |
| 模块代理族 | `_make_module_proxy`(L1372) / `_install_module_proxies`(L1379) / `_get_flow_executor`(L1260) 等 | 延迟加载并代理各核心模块 |
| `_record_step_result(...)` | L1480 | 结果记录 |
| `_force_utf8_stdio()` | L554 | 强制 UTF-8 输出（Windows 控制台编码坑） |

### 4.2 三条运行时纪律（都是踩出来的）

| 纪律 | 原因 |
|---|---|
| **入口 `gc.disable()`，且 `finally` 里不要 `gc.enable()`** | 保持禁用直到 `os._exit()`；异常 unwind 后重开 GC 会在收尾阶段收集失效 COM 指针 → 原生崩溃（B1 §8 / 模式 H-3） |
| **Tk 与 COM 一律主线程** | `MonitorWindow` 的方法去掉 `self.root.update()`；`_TaskbarProgress` 必须在**主线程**创建（后台创建 + 主线程 ctypes 直调 = 跨公寓崩溃，模式 H-2） |
| **收尾用 `os._exit(exit_code)`** | 跳过 `Py_Finalize` 对失效 COM 指针的释放；成功 0 / 异常 1 |

---

## 5. `wt_theme.py`（504 行）· 设计系统

定位：**统一设计令牌（Design Tokens）**，让 40 余个自绘窗口有一致的工业软件质感。

| 成员 | 行号 | 作用 |
|---|---|---|
| `has_ttkbootstrap()` | L97 | 检测可选依赖 |
| `WTThemeManager` | L102 | 主题管理器（深/浅色、边框、间距、字体） |
| `get_theme_manager()` / `get_palette()` | L240 / L244 | 单例与调色板 |
| `create_flat_button(...)` | L250 | 扁平按钮工厂 |
| `create_badge(parent, text, tone="info", font=None)` | L382 | 徽标 |
| `create_card_frame(parent, padx=12, pady=12, border=True, **kwargs)` | L416 | 卡片容器 |
| `create_modern_scrollbar(parent, orient, command)` | L431 | 现代滚动条 |
| `create_pill_selector(parent, options, variable, on_change=None)` | L449 | 药丸选择器 |

- 主色：**蓝灰 `#2563eb`**
- **缺失即降级**：未安装 `ttkbootstrap` 时自动退化为标准库现代风格，**零额外依赖也能跑**（A2 §4 原则 7）。

---

## 6. `wt_dpi.py`（277 行）· 高 DPI 与界面缩放

| 能力 | 说明 |
|---|---|
| Per-Monitor DPI Aware (V2) | `enable_process_dpi_awareness()`，必须在 `tk.Tk()` **之前** |
| 自动缩放 | `compute_scale(root)`，必须在 `tk.Tk()` 之后、布局之前 |
| 几何 monkeypatch | 对 `tk.Widget.geometry` 一次性 patch：尺寸按系数缩放，**`+X+Y` 屏幕坐标保持不动** |
| 手动档位 | 自动 / 100% / 125% / 150% / 175% / 200%；首次默认 **150%** |
| 共享配置 | `workspace/ui_scale.json`，**所有 tkinter 窗口共用一份** |
| 用法 | `wt_dpi.geometry(self.window, 1500, 900)` 代替 `self.window.geometry("1500x900")` |

**原理与背景见 B1 §5。**

---

## 7. `wt_wheel_router.py`（145 行）· 统一滚轮路由

| 成员 | 行号 | 作用 |
|---|---|---|
| `WheelRouter` | L46 | 路由器主体 |
| `register(canvas)` | L140 | 注册可滚动画布 |
| `bind_root(root)` | L144 | 绑定到根窗口 |

**为什么需要它**：Tk 8.6 的 `<MouseWheel>` 事件只送给**鼠标下的控件**，而非焦点控件；多个可滚动画布共存的窗口里，滚轮"滚错地方/完全不滚"是必然的。统一路由后按命中区域分发。

设计依据：Tk 8.6 语义 + 社区交叉验证（2026-09，`docs/04-调研与对标/Tk与Tkinter社区调研_...md`）。

---

## 8. 改 GUI 时的检查清单

| # | 检查项 |
|---|---|
| 1 | 是否在 `tk.Tk()` **之前**调了 `enable_process_dpi_awareness()`、**之后**调了 `compute_scale(root)`？ |
| 2 | 尺寸是否走 `wt_dpi.geometry(...)` / `wt_dpi.scale(...)`，而不是硬编码字符串？ |
| 3 | 新窗口是否用了 `wt_theme` 的工厂函数（按钮/卡片/徽标/滚动条），而不是裸 `tk` 控件？ |
| 4 | 多画布窗口是否 `WheelRouter.register(canvas)` 并 `bind_root`？ |
| 5 | 后台线程里是否**只**通过 `_ui_safe_call` / `root.after` 碰 UI？ |
| 6 | COM 对象（如 `_TaskbarProgress`）是否在**主线程**创建？ |
| 7 | `flow` 相关的新字段是否在 `normalize_step` 白名单里放行（否则保存即丢失）？ |
| 8 | 保存流程后是否走了 `sync_flow_package_registry`（否则注册表不同步）？ |

---

核验：2026-09-11 / 涉及：`WT_Launcher.py`(L78, L130, L203–407, L1611, L1833, L4957–4958, L5989–5997, L9055–9168)、`WT_Flow_Editor.py`(L74–144, L836–1118, L1126–1394)、`WT_AUT_recorded.py`(L111–554, L592–1504)、`wt_theme.py`(L97–449)、`wt_dpi.py`(L1–100)、`wt_wheel_router.py`(L46–144)、`docs/04-调研与对标/Tk与Tkinter社区调研_Tk版本升级与最佳实践交叉验证_20260909.md`
