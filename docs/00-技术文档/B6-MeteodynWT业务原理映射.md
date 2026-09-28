# B6 · Meteodyn WT（MUP）业务原理映射

> 本篇回答：**被自动化的那个软件到底是做什么的、它的业务约束如何反向决定了自动化设计**。
> 读者：所有人。**写流程、写步骤、判结果的人必须读**——不懂业务的自动化只会把错误跑得更快。

---

## 1. MUP 平台与端到端工作流

Meteodyn WT 是 Meteodyn Universe Platform（MUP）的一个模块。一个完整的风资源评估作业：

```mermaid
flowchart LR
    A["① 数据导入"] --> B["② 项目定义"]
    B --> C["③ 微尺度建模 / CFD"]
    C --> D["④ 综合 Synthesis"]
    D --> E["⑤ 排布优化"]
    E --> F["⑥ 结果输出"]
```

| 阶段 | 内容 |
|---|---|
| **① 数据导入** | MUP 中的独立模块：**地理信息数据**（地形 + 粗糙度数据库/文件）、**气象数据**（测风塔 / 激光雷达 / 中尺度对象）、**风机类型**（功率曲线 + 推力系数曲线） |
| **② 项目定义** | 投影坐标系、位置、项目半径 R；挂接地理数据；**创建元素**（机位点、限制区、Post-CFD 点） |
| **③ 微尺度建模 / CFD** | 按风向逐个发送 CFD（网格 + 求解参数）；查看各风向结果；**验证收敛** |
| **④ 综合（Synthesis）** | 把 CFD 分风向结果 + 气象数据 + 风机类型合并，算单机与风场 **AEP**、容量系数、湍流、尾流损失 |
| **⑤ 排布优化** | 在间距/机型约束下最大化目标（AEP / 容量系数） |
| **⑥ 结果输出** | 图件、CSV 收敛数据、Surfer/Tecplot/ParaView、Flowres（windPRO）、`.xyh` 排布文件 |

---

## 2. 流程资产如何映射到这条链

`flow_packages/` 里的定义不是随意命名的，它们**一一对应上面的阶段**：

| 阶段 | 对应流程定义 |
|---|---|
| ① 数据导入 | `flow_definition_导入地形图.json`、`flow_definition_geo_import_data_file.json`、`flow_definition_导入测风塔、机位点.json`、`flow_definition_新建气象数据_内网/外网.json`、`flow_definition_新建风机类型.json`、`flow_definition_turbine_type_create_and_curves.json` |
| ② 项目定义 | `flow_definition_创建一个新建模.json`、`flow_definition_导入并配置元素.json`、`flow_definition_microscale_create_model.json` |
| ③ CFD | `flow_definition_发送CFD计算.json` |
| ④ 综合 | `flow_definition_发送综合计算.json`（配套 `param_table_发送综合计算.json|xlsx` 做参数矩阵） |
| ⑥ 输出 | `flow_definition_导出综合计算结果.json` |

**环境变体**是这套资产的一个特点：同一条流程有 `_内网` / `_外网` / `_配置导入` 三套（如 `新建气象数据`），因为目标机环境、数据来源不同，控件路径与对话框也可能不同。

> 📌 端到端的**作业 SOP** 在卷 D8；本篇只讲业务侧的原理与约束。

---

## 3. 七条领域硬约束（写流程前必须知道）

这些不是软件偏好，是**物理/数值约束**，违反会导致结果无效：

### ① 投影一致性（强制性）

> 地形、粗糙度、项目、导入数据必须**共享同一投影/基准面**。

- **GeoTIFF 内嵌的投影 WT 不读**——必须在项目信息里显式定义；
- 投影不一致会导致可视化错位与 CFD 失效。
- 自动化侧的对应：`wt_projection_helpers.py` 负责投影配置与验证；`mup_user_config.py` 从 `user.config` 提取**投影历史**供"设置投影"步骤使用。

### ② 数据覆盖必须超出计算域

- WT 只在边长 **`1.2 × 2 × R`**（R = 项目半径）的**正方形**内计算；
- 地形必须完整覆盖到 **`1.2 × √2 × R + 2000 m`**；
- 粗糙度可以更小，但缺失处精度下降。
- 自动化侧的对应：`wt_spatial_domain_calc.py` 负责空间计算域计算与参数注入。

### ③ 收敛 ≠ "变绿"

> CFD 显示绿色**仍可能无效**。

- 主导风向需收敛度 **≥ 98%**；
- 非主导风向 **≥ 95%**；
- 必须查看收敛跟踪器（convergence tracker），不能只看状态灯。
- **自动化警示**：这也是"假成功"的领域版本——UI 上绿灯，数值上没收敛。**自动化能点完按钮，但不能替你判断收敛**, 产出后必须由工程师核对。

### ④ CFD 网格是风向对齐且自动加密的

- 网格**按风向重新生成**；
- 在结果点 / 关注区（interest zones）自动加密；
- **Post-CFD 元素不参与加密**。

### ⑤ 综合需要统计量（Weibull）

参与综合的气象对象**必须有风频（威布尔）分布**；纯时间序列对象必须先算出来。

**这条在自动化上的含义**：流程里"创建气象对象"之后、"发送综合计算"之前，**必须先完成风频分布的计算**。缺失时 UI 往往只在最后一步才报错——典型的"首因滞后"（卷 B5 §1.2）。

### ⑥ 尾流与机型数据耦合

- 计算尾流效应**必须有推力系数曲线**；
- 排布优化会挑机型以最大化目标函数；
- REWS 功率曲线会回落到**轮毂高度计算**以省时间。

### ⑦ 湍流是"修正"出来的，不只是算出来的

- CFD 得到的环境湍流强度可用实测的分风向 TI / TI 矩阵修正；
- 总湍流强度：`I_total = √(I_amb² + I_add²)`。

---

## 4. MUP 界面结构为什么难自动化

| 特性 | 给自动化带来的困难 | 本项目的对应能力 |
|---|---|---|
| **WPF + Telerik 控件** | 大量组合框、下拉项在展开前不存在；图标按钮的 UIA name 是 **SVG path** | `select_dropdown_item_runtime`（运行时枚举 Popup）；`click_relative_anchor` + `anchorAlign=right`（点右侧内部图标）；`automation_id` 优先（不被超长 SVG name 劫持） |
| **模板化复制的面板** | `InterestAreas_Button_Add/Edit/Delete` 在各节点共享同一 `automationId`，功能语义只体现在 `helpText` / 面板标题上 | 按**面板节点标题**消歧（`InterestAreasView_Tile_Header` 兄弟 + 三种去重模式追加 `ia:<节点>` 区分符） |
| **`PART_ContentHost` 等内部宿主** | Control View 不可见、control_type 不稳定（Pane/Edit 漂移） | Raw View 分支 + `PART_ContentHost` 特判（卷 B1 §3.3 / B3 §4.1） |
| **多选下拉里等级文本在子节点** | CheckBox 自身 UIA Name 为空，10 个同 automationId 的兄弟 | `_raw_element_child_text_matches` + `_match_child_text_block_label` + `label_text` 消歧 + `precondition` 幂等 |
| **粗糙度索引文件选项来自安装目录文件** | 下拉选项随安装内容变化 | `mup_assets.py` 从 `<安装目录>/Assets/CorrespondanceFiles/*.txt`（如 `ESA2020.txt`）读取**权威选项**；`mup_prod_config.py` 解析 `PROD_default_parameters.ini` 的官方"粗糙度源 ↔ 索引文件"映射（实测 8 组） |
| **产物按用户数据目录落盘** | 需要判断"这步到底有没有产出文件" | `mup_data_files.py` 读取 MUP 用户数据目录做产物落盘检测（如 `OROGRAPHY/<project>_<zone>.tif`） |
| **地形需外部工具预处理** | 依赖 Global Mapper（`${GM_EXE}`） | `wait_global_mapper_ready.py`；`fix_server_gm_exe.py`（把 `.lnk` 改成直接可执行 `.exe`，规避 UIPI 与可靠性差异） |

---

## 5. 为什么最终是 RPA，而不是 API

`tools/research/` 下有 20 余个探查脚本（`mup_mup_api_probe.py`、`mup_rest_probe*.py`、`mup_mup_rest.py`、`mup_prod_struct.py`、`mup_bin_license.py`、`mup_enum_windows.py` …），曾系统验证过 REST / COM / 命令行等可编程接口的可能性，**最终未形成可用于生产的接口**。

由此确定的技术路线（回到 A1 §1.2）：

> **外部 UI 自动化（RPA）**：以操作系统提供的 UIA / Win32 能力驱动界面，把人的操作固化为可复放的声明式流程。

**这条路线决定了本项目的全部性格**：

| RPA 的固有代价 | 因此必须有 |
|---|---|
| 控件属性随版本漂移 | 多策略评分定位（B3） |
| 界面可能不变，点了也白点 | `continueWhen` + 自动值断言（B5） |
| 树在某些容器里失效 | Raw View / 亲戚区域 / 图像模板 / AI（B1、B4） |
| 同一逻辑要跑几十上百次 | 参数矩阵 + 远程队列（D6、C5） |

> 📌 若 Meteodyn 官方将来开放可用 API，这条路线应重新评估——届时上层"流程定义 + 运行参数"的抽象仍可保留，只需替换动作执行层的实现。

---

## 6. 领域知识如何进入这套系统

自动化系统里"懂业务"的代码散在四处，维护时要注意同步：

| 通路 | 载体 | 说明 |
|---|---|---|
| **执行的业务步骤** | `wt_business_steps.py`、`wt_mast_config_xml.py`（测风塔 XML 配置注入）、`wt_spatial_domain_calc.py`（计算域） | 领域逻辑直接代码化 |
| **MUP 权威数据读取** | `mup_assets.py` / `mup_prod_config.py` / `mup_user_config.py` / `mup_data_files.py` | 不硬编码选项，而是从 MUP 安装/用户目录读真值 |
| **操作蓝图 → 流程包** | `WT_AUTOMATION_Agent/skills/` 与 `operation_blueprints/*.json` + `tools/generate_flow_package.py` | 合规操作步骤来自**手册**，`control` 用中文业务名占位，再经 `SYNONYMS` 词表映射到标准库 `automation_id`；未匹配的控件输出 `PENDING_CAPTURE` 占位步骤，由实时抓取回填 |
| **标准控件库的权威分级** | `control_maps/standard_control_catalog.json` | 由 `tools/merge_standard_control_library.py` 按 `authority`（high/medium/low）分级合并；另输出 `standard_catalog_mismatch_report.json` 列出"对不上"的低权威项（如 `frameworkId=unknown`、窗口标题为空） |

**人工知识入口**：

| 文档 | 用途 |
|---|---|
| `docs/09-规范与手册/WT_微尺度高级技术注释_中文版.md`（96 KB） | MUP 微尺度领域知识，写 CFD 相关步骤的必备参考 |
| `docs/07-外部参考资料/` | MUP 官方手册（中/英）、参考文献、控件信息 |
| `WT_AUTOMATION_Agent/skills/meteodyn_wt_domain.md` | Agent 可加载的 MUP 领域软知识 |

---

## 7. 给自动化使用者的三条业务纪律

1. **自动化只负责"把操作做对"，不负责"把项目做对"。** 投影一致性、数据覆盖、收敛度这些判断权在工程师手里。
2. **领域错误常表现为"最后一步报错"。** 气象对象缺 Weibull、粗糙度索引文件不对、Coverge 不够，往往要到"发送综合计算"才炸。排障时按 §3 逐条倒查，而不是在最后一步打补丁。
3. **选项类控件不要硬编码。** 粗糙度索引文件、投影列表这类随安装数据变化的选项，一律走 `mup_*` 模块读取真值。

---

核验：2026-09-11 / 涉及：`flow_packages/` 全量流程定义、`wt_business_steps.py`、`wt_mast_config_xml.py`、`wt_spatial_domain_calc.py`、`wt_projection_helpers.py`、`mup_assets.py`、`mup_prod_config.py`、`mup_user_config.py`、`mup_data_files.py`、`tools/research/`（接口探查探针）、`.codebuddy/skills/meteodyn-wt-knowledge/references/workflow_overview.md`、`WT_AUTOMATION_Agent/skills/meteodyn_wt_domain.md`、`docs/09-规范与手册/WT_微尺度高级技术注释_中文版.md`
