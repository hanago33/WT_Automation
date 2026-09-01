# Simple 模式「市场项目工作文件夹」自动解析键入值 — 改造方案设计

> 生成日期：2026-08-24（含用户校正 v2 + 真实参考目录样本）
> 关联文档：`simple_mode_param_inventory.md`（8 板块参数清单与判定）
> 目标：读取市场项目工作文件夹（含 `03-WT输入` / `04-WT输出`），自动解析 Simple 板块步骤所需的键入值、文件输入输出地址、候选项，减少手动改链路/Excel 的繁琐。

---

## 1. 现状与痛点

Simple 模式 8 个板块各自绑定一个 `flow_definition_*.json`（`WT_Launcher.SIMPLE_SECTIONS` + `simple_mode_flows`）。步骤可变参数两类承载：
1. **`runtimeConfig` 占位符**：步骤以 `${runtime.sourceFilePath}` / `${mastImportFilePath}` 等引用，运行时通过 `GM_RUNTIME_CONFIG_JSON` 环境变量注入子进程（L4051）。已用于地形图、气象数据、导入并配置元素、综合计算、导出。
2. **写死在 step `actionConfig.text`**：坐标、投影带、半径、CFD 网格、Cp 版本、50年风速、导出路径等直接写死，未用占位符。

用户只能编辑链路 json 或 Excel 导入/导出，不便捷。

---

## 2. 设计目标

- 新增「项目工作文件夹」选择入口；运行时自动遍历 `03-WT输入`、`04-WT输出` 按规则映射参数。
- 不改动链路文件本体（只读 + 运行期内存替换/环境变量注入）；不影响 Advanced 模式与 Excel 导入导出。
- **分类处理**：可自动解析的（03/04-WT输入解析）自动填；项目计算参数（需人工确认）由用户填写/确认后注入。
- 解析失败安全降级：保留用户已填值或标记"未解析"，不阻断运行。

---

## 3. 真实参考目录（解析规则样本）

`C:\Users\14830\Desktop\202608_Test`：
```
03-WT输入/
├─ 01-测风塔及机位点坐标/  CFT_Project_CGCS2000 43.txt, JWD_Project_CGCS2000 43.txt
├─ 02-地形图/              TEST1_Project_CGCS2000 43.tif
├─ 03-测风塔数据/          C1831\  (1831-...-tim.txt / -TI.txt / -TISD.txt)
└─ 04-功率曲线/            功率曲线.wtg, 功率曲线.txt
04-WT输出/
├─ m1\ (DEMO_S2.xlsx ...)  ├─ m4\ (空)  ├─ m10\ (空)
└─ DEMO_WRA2_*_Matrix.txt
```

---

## 4. 总体架构

```
用户选择「项目工作文件夹」(含 03-WT输入 / 04-WT输出)
        │
        ▼
[新增] ProjectWorkDirParser  ── 读 launcher_state.projectWorkDir
        ├─ 遍历 03-WT输入：地形tif/asc、机位点/测风塔坐标 txt、测风塔 tim/TI/TISD、.wtg、粗糙度/网格 txt
        ├─ 遍历 04-WT输出：m1/m4/m10 分目录
        ├─ 解析 投影缩写(CGCS2000 43)、UTM 坐标、项目名
        └─ 读取「项目计算参数」(人工确认项：半径/CFD网格/Cp版本/M1/50年风速)
        ▼
[产出] runtimeConfig 覆盖 + 扩展字段(utmX/utmY/projectionAbbrev/outputDir/mastImportFilePath/...)
        + project_overrides(写死 text 替换映射)
        │
        ▼
[改造点] _run_simple_mode 执行每板块前：
        ├─ 已用 ${runtime.xxx}：注入 GM_RUNTIME_CONFIG_JSON（现有机制）
        └─ 写死 text：内存 payload 做占位符化替换 → 注入
        ▼
AUTOMATION_SCRIPT 子进程拿到完整 runtimeConfig，正常执行
```

---

## 5. 参数解析规则（用户校正 v2）

### 5.1 项目名
- = 项目文件夹名（如 `202608_Test`）。用于板块1 step_5、板块5 step_5。

### 5.2 投影坐标系（从文件名缩写解析）
- 扫描 `02-地形图\*.tif` / `01-测风塔及机位点坐标\*.txt` 文件名，提取缩写如 `CGCS2000 43`。
- 设置投影步骤：以该缩写作为搜索词，在 MUP 投影下拉中匹配**全称**选项（替换现有写死的 "UTM"/"50N"）。
- 对应清单板块1 step_17/18、板块5 step_15/23。

### 5.3 `03-WT输入` 文件解析
```
01-测风塔及机位点坐标\
   *机位点*.txt / *CFT*.txt     → 板块3 机位点元素
   *测风塔*.txt / *JWD*.txt     → 板块3 测风塔元素 (或 03-测风塔数据)
02-地形图\*.tif | *.asc         → runtime.sourceFilePath；读 UTM 坐标(utmX/utmY)
03-测风塔数据\<编号>\
   *-tim.txt                    → 板块2 测风塔主数据 (mastImportFilePath)
   *-TI.txt                     → 板块2 TI
   *-TISD.txt                   → 板块2 TISD
04-功率曲线\功率曲线.wtg        → 板块3/4 风机文件 (turbineImportFilePath)
粗糙度/障碍物/网格 *.txt        → 板块3 对应元素（按文件名关键字匹配）
```
- UTM 坐标：优先从 tif/asc 元信息读取；无元信息则用坐标 txt 首行或"项目计算参数"补充。

### 5.4 `04-WT输出` 解析
```
04-WT输出\m1\  m4\  m10\       → 板块8 导出路径：<根>\m<编号>\WRA_综合计算_<项目>.xlsx
```
- 综合命名规则（用户设想）：`综合1 Nomapping m1 6.25-220-125 Cp0.429` → 计算 m1/m4/m10 三个综合，导出分目录即 m1/m4/m10。
- 若分目录不存在，解析器自动创建（安全降级）。

### 5.5 项目计算参数（需人工确认，无法自动解析）
纳入"项目计算参数"面板/文件，人工填写后注入：
- 半径 `5000`、CFD 网格 `22.5/25/4/4`（板块5 step_20、板块6 step_5/8/11/13）。
- Cp 版本 `Cp0.429`（板块7 step_10）、测风对象 `M1`（板块7 step_24）、50年回归风速（板块7 step_25）。
- 建议存储：`<项目>\project.params.json`（可选），解析器读取；缺省提示人工确认。

---

## 6. 写死 text 值的占位符化策略（核心改造）

对写死在 `actionConfig.text` 的参数（UTM_X/Y、投影全称、半径、CFD 网格、Cp 版本、M1、50年风速、导出路径）：

**方案 A（推荐，零侵入）：运行期内存替换，原文件不动**
1. 解析器产出 `project_overrides` 映射，如 `{"utmX":"...","projectionFullName":"CGCS2000 43 ...","cfdGrid":"22.5","cpVersion":"Cp0.429",...}`。
2. `_run_simple_mode` 加载 flow payload 后、注入前，对内存 dict 做文本替换：将已登记为"写死可解析"的原值替换为 `${runtime.xxx}` 或解析值。
3. 序列化后经 `GM_RUNTIME_CONFIG_JSON` / `FLOW_DEFINITION_ENV_KEY` 传给子进程。
4. 原 `flow_definition_*.json` 保持只读。

**方案 B（长期）：模板化改造流程文件**
- 把写死值预先改为 `${runtime.xxx}` 占位符（一次性改造 8 文件），之后走占位符体系。
- 建议：先落地方案 A，稳定后再对高频参数做方案 B。

---

## 7. 关键对接点（已核实代码）

| 位置 | 作用 | 改造方式 |
|---|---|---|
| `load_flow_runtime_config` (L146) | 读 runtimeConfig + 默认回填 | 新增：若 `projectWorkDir` 已设，调用 parser 覆盖/补充 |
| `GM_RUNTIME_CONFIG_JSON` 注入 (L4051) | 子进程读取 runtimeConfig | 不变，parser 结果经此注入 |
| `_run_simple_mode` (L1976) | 顺序执行勾选板块 | 循环内、运行前插入 parser 解析 + 内存替换 |
| `SIMPLE_SECTIONS` (L1572) | 定义 8 板块 | 可加 `paramSpec` 声明本板块需解析键 |
| `launcher_state.simpleModeFlows` / `projectWorkDir` | 持久化 | 新增 `projectWorkDir` 字段 |
| Simple 工具栏 | 用户操作入口 | 新增「选择项目工作文件夹」+ 「项目计算参数」入口 + 路径回显 + 解析预览 |

> 解析器为**无 GUI 依赖的纯函数/类**（遵循 Model/View 分离），便于独立测试。

---

## 8. 持久化与 UI

- `launcher_state.json` 新增 `projectWorkDir`（绝对路径）、`projectParams`（项目计算参数字典，可选）。
- Simple 工具栏新增：
  - 「选择项目工作文件夹」按钮（`tk.filedialog.askdirectory`）+ 路径回显。
  - 「项目计算参数」按钮：打开表单填写半径/CFD网格/Cp版本/M1/50年风速（人工确认项）。
  - 可选「解析预览」：列出将从该文件夹推断的参数，供确认后再运行。
- 解析结果不写回链路文件，仅运行期使用。

---

## 9. 安全降级与校验

- 目录缺失 / 文件不匹配：parser 返回"未解析"标记，保留用户已填 runtimeConfig 值或默认常量。
- 多文件歧义（如多个 tif）：取首个或按命名规则，UI 提示确认。
- 投影缩写无匹配全称：提示人工选择，不强行填。
- 运行前复用 `validate_flow_definition` 校验 payload。
- 解析器单测：构造样例 `03-WT输入/04-WT输出` 目录断言映射正确（可用 `C:\Users\14830\Desktop\202608_Test` 作样本）。

---

## 10. 实现阶段任务拆解（待用户确认后进入）

1. 新增 `wt_project_workdir_parser.py`（纯函数：输入项目文件夹 + 项目计算参数 → 输出 runtimeConfig 覆盖 + overrides）。
2. 改造 `load_flow_runtime_config`：支持 projectWorkDir / projectParams 注入。
3. 改造 `_run_simple_mode`：每板块运行前内存占位符替换 + 注入。
4. `WT_Launcher` UI：新增"项目工作文件夹" + "项目计算参数"入口 + 持久化。
5. `launcher_state` 新增 `projectWorkDir` / `projectParams` 读写。
6. 单测 + 用 `202608_Test` 样本手动验证 8 板块跑通。

> 本设计为规划阶段，不改代码；待用户确认参数清单判定与 §5 解析规则后进入实现。
