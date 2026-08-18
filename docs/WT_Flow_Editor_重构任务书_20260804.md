# WT_Flow_Editor.py — `FlowEditorApp` 类按职责拆分 任务书

- 日期：2026-08-04
- 状态：**规划中（未执行）** — 本任务书仅为后续执行的依据，读取本文件后再动手
- 目标文件：`WT_Flow_Editor.py`（9449 行）
- 重构对象：`FlowEditorApp` 类（**183 个方法，L4806-9369**）
- 前置已完成：`docs/` 同级代码已做过一轮 Dialog Tkinter 样板抽取（2026-08-04），顶部新增了 `_make_dialog_window`/`_grid_label_entry`/`_make_treeview`/`_make_button_row` 等模块级 UI 辅助函数，本任务可复用。

---

## 1. 目标与范围

### 1.1 目标
将 `FlowEditorApp` 这一巨型类按职责域拆分为若干 **mixin**（或组合对象），使：
- 单个文件职责清晰、方法可定位；
- 纯逻辑方法可脱离 Tk 独立单元测试；
- 后续扩展（新 tab、新采集方式）不必塞进同一个类。

### 1.2 范围（做）
- 拆分 `FlowEditorApp` 的 183 个方法到职责 mixin；
- 抽取可测试的纯数据/逻辑方法为独立类并补单元测试；
- 保持对外行为与 UI 布局**完全不变**。

### 1.3 非目标（不做）
- 不重构 Dialog 类（已完成）；
- 不改变 `wt_flow_executor.py` / `wt_flow_locator.py`；
- 不重排 UI 布局、不改视觉样式、不改字段名/默认值；
- 不做行为优化（拆分期间只搬家，不改逻辑）。

---

## 2. 现状：方法清单按职责域分组

> 行号基于 2026-08-04 的 `WT_Flow_Editor.py`。执行前先用
> `grep -n "class FlowEditorApp" WT_Flow_Editor.py` 核对实际行号。

### 域 A：初始化 / 视觉样式（8 个）
`__init__`、`_load_or_default_definition`、`_configure_visual_style`、`_create_action_button`、`_style_text_surface`、`_create_form_card`、`_on_ui_scale_changed`

### 域 B：主 UI 骨架构建（9 个）
`_build_ui`、`_build_scrollable_panel`、`_build_left_panel`、`_build_right_main`、`_build_center_panel`（最大，约 540 行）、`_build_ai_tab`、`_build_packages_tab`、`_build_config_tab`、`_build_help_tab`

### 域 C：AI/DSL 集成（9 个）
`_toggle_ai_key_visibility`、`_get_ai_config_dict`、`_save_ai_config`、`_load_ai_config`、`cmd_open_ai_tab`、`cmd_ai_test_connection`、`cmd_ai_convert`、`cmd_ai_insert_steps`、`_clear_ai_input`

### 域 D：运行参数 / 流程包表单（9 个）
`_load_runtime_config_into_form`、`_build_runtime_config_from_form`、`_load_flow_packages_into_form`、`_build_flow_packages_from_form`、`_load_flow_package_registry_payload`、`_get_flow_package_dialog_available_steps`、`_import_missing_registry_steps_for_packages`、`_format_json_text`、`_format_json_list_text`

### 域 E：流程包管理（28 个）
`_get_package_ref_choices`、`_refresh_package_ref_choices`、`_build_step_package_names_map`、`_refresh_flow_packages_view`、`cmd_reload_flow_packages_from_registry`、`_get_selected_package_index`、`_get_package_by_id`、`_update_step_scope_label`、`_get_visible_step_indexes`、`_apply_package_step_filter`、`cmd_clear_step_package_filter`、`_on_package_tree_select`、`_focus_step_by_id`、`cmd_focus_flow_package_steps`、`_open_flow_package_dialog`、`_rename_step_id_in_packages`、`_remove_step_id_from_packages`、`_rename_package_ref_in_steps`、`_clear_package_ref_in_steps`、`_generate_unique_step_id`、`cmd_add_flow_package`、`cmd_edit_flow_package`、`cmd_delete_flow_package`、`cmd_move_flow_package_up`、`cmd_move_flow_package_down`、`_select_package_by_index`

### 域 F：模板库（8 个）
`_get_selected_template_definition`、`_get_template_definition_by_id`、`_refresh_template_library`、`_build_step_from_template`、`_insert_step_from_template_definition`、`_show_quick_add_template_menu`、`cmd_insert_quick_template`、`cmd_insert_step_template`、`cmd_apply_step_template`

### 域 G：Action 配置编码/解码（**纯逻辑，最高优先级抽取**，13 个）
`_parse_json_dict_text`、`_parse_json_list_text`、`_parse_float_or_default`、`_parse_int_or_default`、`_extract_relative_region_action_config`、`_is_default_relative_region_step_name`、`_is_default_relative_region_description`、`_build_relative_region_step_name`、`_build_relative_region_description`、`_build_action_config_from_editor`、`_load_action_editor_from_config`、`_normalize_post_input_keys_value`、`_normalize_template_path_for_storage`

### 域 H：Action 表单 UI 联动（22 个）
`_grid_label_entry`、`_refresh_action_control_choices`、`_set_target_control_value`、`_set_continue_when_control_value`、`_get_target_control_id`、`_get_continue_when_control_id`、`_maybe_autoselect_target_control`、`_on_target_control_changed`、`_on_continue_when_control_changed`、`_refresh_action_schema_hint`、`_set_post_input_keys_value`、`_sync_post_input_controls`、`_on_post_input_keys_changed`、`_on_require_blur_submit_changed`、`cmd_choose_fallback_template`、`cmd_open_template_library`、`_set_widget_enabled`、`_show_widget`、`_on_action_type_changed`、`_on_action_changed`、`_update_action_editor_visibility`（约 116 行）

### 域 I：相对区域预览（9 个）
`_sync_action_config_preview_from_form`、`_refresh_relative_region_preview`、`_apply_relative_region_preview_height`、`_start_relative_region_preview_resize`、`_drag_relative_region_preview_resize`、`_finish_relative_region_preview_resize`、`_reset_relative_region_preview_height`、`cmd_import_relative_region_from_clipboard`、`cmd_clear_relative_region_fields`

### 域 J：步骤树显示（8 个）
`_build_step_tree_action_summary`、`_build_step_tree_target_summary`、`_refresh_steps_tree`、`_refresh_overview`、`_on_template_select`、`_on_tree_select`、`_get_selected_step_indexes`、`_select_step`

### 域 K：拖拽排序（4 个）
`_start_step_drag`、`_track_step_drag`、`_finish_step_drag`、`_move_step_to_position`

### 域 L：步骤表单加载/保存（5 个）
`_load_step_into_form`、`_build_step_from_form`、`cmd_apply_step`、`cmd_reload_step`、`_clear_step_form`

### 域 M：控件树与控件 CRUD（18 个）
`_refresh_controls_tree`、`_get_selected_control_index`、`_open_control_dialog`、`cmd_add_control`、`cmd_edit_control`、`cmd_delete_control`、`_append_controls_to_selected_step`、`_sync_controls_to_control_library`、`cmd_import_control_from_clipboard`、`cmd_open_semi_auto_collector`、`cmd_open_control_map_builder`、`cmd_open_control_locator_tester`、`cmd_import_control_from_control_map`、`_sync_control_to_step_hints_by_control`、`cmd_match_control_from_control_map`、`cmd_sync_control_to_step_hints`

### 域 N：文件 / 保存 / 新建（14 个）
`cmd_new_default`、`_load_definition_into_editor`、`_build_recorder_output_path`、`cmd_convert_recorder_script`、`cmd_open`、`cmd_save`、`cmd_save_as`、`_save_to`、`cmd_open_json_file`、`cmd_open_reference_project`、`_mark_dirty`、`_set_title`、`_on_close`

### 域 O：步骤 CRUD（6 个）
`cmd_add_step`、`cmd_duplicate_step`、`cmd_delete_step`、`cmd_move_up`、`cmd_move_down`、`cmd_renumber_step_ids`

### 域 P：并发采集模式（14 个）
`_toggle_concurrent_mode`、`_start_concurrent_mode`、`_is_ctrl_down`、`_point_in_editor`、`_poll_concurrent_events`、`_stop_concurrent_mode`、`_build_control_from_wrapper`、`_concurrent_capture_and_append`、`_show_toast_notification`、`_match_control_in_master_library`、`_infer_action_type`、`_build_step_from_control`、`_append_step_to_flow`

### 域 Q：杂项 helper（6 个）
`_split_lines`、`_get_text`、`_set_text`、`_open_control_library`、`_open_control_import_dialog`、`_open_control_map_builder`

---

## 3. 拆分方案（三阶段，每阶段独立可回滚）

### 总体原则
- 所有 mixin 都在**同一文件**内定义（`WT_Flow_Editor.py` 顶部、Dialog 类之前），**不新建文件**，避免导入顺序问题与打包遗漏。
- `class FlowEditorApp(FlowLifecycleMixin, FlowPanelBuildMixin, ..., object):` 顺序继承；mixin 只访问 `self.xxx` 属性/方法，不在 mixin 里定义 `__init__`（除生命周期 mixin 提供 `_init_xxx` 辅助方法外）。
- 拆分 = **剪切粘贴方法体 + 类声明**，**不改方法内部逻辑**；每阶段完成后跑验证清单。

### 阶段 1：纯逻辑抽取（先做，风险最低，可单测）
把**域 G（Action 配置编解码）**中不依赖 `self` 的方法抽成一个新类 `ActionConfigCodec`（放文件顶部）：

- 无 `self` 依赖：`_parse_float_or_default`、`_parse_int_or_default`、`_normalize_post_input_keys_value`、`_normalize_template_path_for_storage`、`_is_default_relative_region_step_name`、`_is_default_relative_region_description`、`_build_relative_region_step_name`、`_build_relative_region_description`、`_extract_relative_region_action_config`
- 有 `self` 依赖但可注入：`_build_action_config_from_editor`、`_load_action_editor_from_config`、`_parse_json_dict_text`、`_parse_json_list_text`

> `_build_action_config_from_editor`（约 95 行）是核心：它读取一串 `self.var_*` StringVar 组装 actionConfig。建议把它重构成**先收集成普通 dict、再调用 codec 方法**，从而剥离 UI 依赖。

**验收**：`tests/test_action_config_codec.py` 覆盖相对区域解析、JSON 文本解析、postInputKeys 归一化、模板路径归一化、stepName 生成。全量 `pytest` 通过；编辑器运行时冒烟通过（见 §5）。

### 阶段 2：UI 面板构建 mixin（中风险）
把**域 B（主 UI 骨架）+ 域 H + 域 I（表单联动/预览）**抽成 2 个 mixin：
- `FlowPanelsMixin`：`_build_ui`、`_build_left_panel`、`_build_right_main`、`_build_center_panel`、`_build_scrollable_panel` 及 `_build_ai_tab`/`_build_packages_tab`/`_build_config_tab`/`_build_help_tab`
- `FlowActionEditorMixin`：域 H + 域 I

> `_build_center_panel`（约 540 行）建议先内部按"基本信息 / Inspect / 相对区域"切成多个私有 `_build_xxx_section` 方法（只移动不改逻辑），再整体搬入 mixin。

### 阶段 3：业务管理 mixin（中高风险）
把**域 C（AI）、域 E（流程包）、域 F（模板）、域 D（表单）、域 M（控件）、域 J/K/L/O/P/N** 抽成对应 mixin：
- `FlowAiMixin`（C）
- `FlowPackageMixin`（D+E）
- `FlowTemplateMixin`（F）
- `FlowControlMixin`（M）
- `FlowStepMixin`（J+K+L+O）
- `FlowFileMixin`（N）
- `FlowConcurrentCaptureMixin`（P）

> 注意 `_build_control_from_wrapper`（P，约 140 行）与 `wt_flow_locator` 逻辑相近，先归入并发 mixin，后续再评估是否下沉到 locator。

---

## 4. 顺序与依赖

```
阶段1 ActionConfigCodec（纯逻辑）
  └─> 阶段2 面板/表单 mixin（依赖阶段1 的 codec 被 self 调用）
       └─> 阶段3 业务管理 mixin
```

- 每个阶段**独立提交**，可用 `git revert` 单阶段回滚。
- 建议每阶段控制在 1 次会话内完成并跑完验证，不要跨阶段混提。

---

## 5. 验证方式（每阶段必做）

1. `python -m py_compile WT_Flow_Editor.py`
2. 全量回归：`python -m pytest tests/ -q`（基线 263 passed）
3. **运行时冒烟**（关键，因为 Dialog/主类无单测）——用隐藏 Tk root 实例化并销毁全部 Dialog + `FlowEditorApp`，真实执行 UI 构建代码：
   ```python
   import tkinter as tk, WT_Flow_Editor as E
   root = tk.Tk(); root.withdraw()
   app = E.FlowEditorApp(root); app.root.update()   # 验证整个 UI 构建不抛错
   root.destroy()
   ```
   以及 7 个 Dialog：`ControlPickerDialog`/`ControlEditorDialog`/`SemiAutoInspectCollectorDialog`/`ControlEditDialog`/`ControlLocatorTesterDialog`/`ControlMapImportDialog`/`FlowPackageDialog`
4. 阶段 1 额外：新增 `tests/test_action_config_codec.py`，用 `unittest`/`pytest` 覆盖 codec 纯逻辑。
5. `git diff --stat` 确认每个阶段只搬动方法、无重复定义（`grep -o "^    def " WT_Flow_Editor.py | sort | uniq -d` 应为空）。

---

## 6. 风险与规避

| 风险 | 规避 |
|---|---|
| **无 UI 单测**，运行时错误难发现 | 阶段 1 用单测锁纯逻辑；UI 阶段每次跑 §5.3 冒烟 |
| **mixin 间共享状态**（`self.var_*`/`self.step_tree` 等大量属性在 `__init__`/`_build_ui` 里创建） | mixin **不新增属性定义**，只读写在 `__init__`/`_build_ui` 已创建的属性；必要时把属性创建抽到 `_init_*` 辅助 |
| **方法间隐式依赖**（A 调 B 调 C） | 按域分组时先做引用分析：`grep -n "self\._build_action_config_from_editor"` 等，确认被调方与其调用方在同一 mixin 或主类 |
| **与其它 agent 并发编辑冲突**（此前发生过） | 执行前检查文件 mtime / `git status`；本任务书执行期间避免长时间占用 |
| 行号漂移 | 以方法名为锚点定位，不以行号；执行前重新核对 |

---

## 7. 完成标准（Definition of Done）

- [ ] `FlowEditorApp` 声明改为 `class FlowEditorApp(FlowXxxMixin, ..., object):`，主类仅保留 `__init__` 与少量入口方法
- [ ] 阶段 1 的 `ActionConfigCodec` 有独立单测文件且全绿
- [ ] `pytest` 全量通过（含新增测试）
- [ ] 运行时冒烟：`FlowEditorApp` + 全部 Dialog 可实例化/销毁
- [ ] 无方法重复定义；`git diff` 各阶段独立可回滚
- [ ] 视觉与行为无回归（人工抽查：打开编辑器、切换 tab、编辑 action、拖拽排序、导入控件库）
