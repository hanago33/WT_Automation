# WT_Automation V5.0.0 发布说明

发布日期：2026-07-30

## 版本亮点

V5.0.0 聚焦「控件采集 → 定位 → 执行」全链路的精度与鲁棒性升级，并扩展 AI Agent 的参数化批量能力。

### 🎯 控件采集与标准库（核心升级）

- **`build_control_map_library.py` 大规模增强（+3900 行）**：
  - Inspect 风格控件树增量补采与悬停跟踪补采
  - 标签→输入框关联链路（labelText 采集与 companion 字段）
  - RawViewWalker 树搜索采集，覆盖 ControlView 不可见的控件
  - 采集进度反馈与窗口置顶交互优化
- **`tools/merge_standard_control_library.py`**：标准控件库合并去重强化，含 optionValues 完整传递
- **`uia_tree_dumper`（.NET）**：RawView BFS 树遍历导出工具同步升级
- 新增/更新多份控件库资产（微尺度模拟、位置、地形、粗糙度、投影系等窗口）

### 🔍 运行时定位（wt_flow_locator +492 行）

- labelText 匹配策略与 RawView 兜底定位
- Tab 导航降级定位链（控件级优先 + 焦点校验，`tools/verify_tab_focus_chain.py`）
- 多维评分规范落地，同名控件误命中率下降

### 🤖 AI Agent 扩展

- **`add_parameter_scan` Function Calling 工具**：从 Excel/CSV 参数表批量生成参数化流程（`${stepParams.xxx}` 占位符 + 列头映射）
- `control_search.py` 控件检索增强（+657 行）
- CLI 参数扫描入口

### 🖥️ 编辑器与总控台

- `WT_Flow_Editor.py`（+504 行）：AI 助手 Tab 完善与控件维护体验优化
- `WT_Launcher.py`（+394 行）：启动与运行链路改进

### ✅ 测试

新增 7 个测试套件：采集覆盖率、标签关联、companion/inspect 字段、标准库合并、子树补采、Tab 导航降级、labelText 定位。

### 🔒 工程治理

- `.gitignore` 扩充：临时调试文件（`_tmp_*`、`*_diff.txt`、`build/` 等）与自愈存储不再入库
- Agent LLM 配置（含明文 Key）持续保持库外

## 升级说明

无破坏性变更。直接拉取后运行 `启动WT自动化总控台.bat` 或 `WT_AUTOMATION_Agent/启动WT_AI助手.bat` 即可。
