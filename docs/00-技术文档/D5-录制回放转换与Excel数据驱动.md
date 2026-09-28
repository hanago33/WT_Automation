# D5 · 录制回放 · 转换 · Excel 数据驱动

> 本篇回答：**怎么把"人在界面上做一遍"快速变成流程定义**，以及**怎么用 Excel 批量维护流程**。
> 读者：想快速起步的人、要批量改流程的人。
>
> 转换器内部机制在 C4 §3，Excel 模块在 C4 §4；本篇是**操作手册**。

---

## 1. 三条"不用手敲流程"的路径

| 路径 | 适合 | 产出 |
|---|---|---|
| **录制 → 转换**（本篇 §2–§4） | 从零开始、不知道控件属性 | 一份待复核的流程定义 |
| **蓝图 → 生成**（C3 §5） | 有 MUP 手册操作步骤 | 带 `PENDING_CAPTURE` 占位的流程 |
| **Excel 批量编辑**（本篇 §5） | 已有流程，要批量改参数/名单 | 回写后的流程定义 |

---

## 2. 录制

| 项 | 说明 |
|---|---|
| 启动 | 双击 `_launch_pywinauto_recorder.cmd` |
| 依赖 | `pywinauto_recorder` **不在 PyPI**，需从本地安装目录获取 |
| 未安装的影响 | **不影响** GUI / 半自动采集 / 控件库构建；只影响录制与回放 |
| 便携环境 | `player` 模块已包含在便携 Python 内；录制 GUI 需整目录拷贝 |
| 产物 | recorder Python 脚本 + 伴随截图（`recorder_captures/*.png`） |

**录制时的建议**

1. 动作放慢，每个关键操作之间停一下（便于分段与序号识别）；
2. 一次录**一个业务板块**，不要录整条长链（转换后难维护）；
3. 保留**伴随截图**（后面 `--screenshot-dir` 要用）。

---

## 3. 转换

```powershell
python flow_recorder_converter.py <script> [选项]
```

| 参数 | 说明 |
|---|---|
| `script`（位置参数） | 输入 recorder Python 文件路径 |
| `--output` | 输出 JSON 路径（默认 `DEFAULT_OUTPUT_JSON`） |
| `--control-map-dir` | 控件库目录，默认 `control_maps/` |
| `--screenshot-dir` | **可选**：录制截图目录。提供则在转换时关联截图 `templateKey`，供模板兜底使用 |
| `--previous` | **可选**：上次转换的 `flow_definition.json`。提供则启用**增量转换，保留用户手动修正** |

**推荐用法（带截图 + 增量）**：

```powershell
python flow_recorder_converter.py 录制脚本.py ^
  --output flow_packages/flow_definition_某板块.json ^
  --screenshot-dir image_templates/recorder_captures ^
  --previous flow_packages/flow_definition_某板块.json
```

> 💡 `--previous` 很重要：转换结果往往被人工修过（补消歧、改动作），重跑转换时**不带 `--previous` 会把修正冲掉**。

### 3.1 转换管线（了解它，才知道要复核什么）

```
AST 解析 → UIPath 处理（含 foundIndex 保留）
        → 目标方法/值构建 → 控件定义构建
        → 控件库匹配（os.walk 递归）
        → 动作语义映射 → 运行参数抽取
        → 降级链构建 → 待复核标记
```

| 自动做的事 | 说明 |
|---|---|
| `foundIndex` 保留 | UIPath 段尾 `#[范围,N]` 的序号会保留为 `inspectData.foundIndex` |
| 路径常量提升 | 明显的文件路径会被识别为运行参数占位 |
| 控件库匹配 | 用 `os.walk` 递归读控件库，**不受 P1 断点影响** |
| 候选留存 | 匹配不上的控件存进 `control_maps/_candidates/` |
| 降级链 | 自动构建 `fallbackChain` |

---

## 4. 转换后必做的三件事

| # | 事项 | 为什么 |
|---|---|---|
| 1 | **处理待复核项**（`_append_step_review_hint` 标记的） | 转换不确定处会打标记，不处理就是埋雷 |
| 2 | **补消歧字段** | 同名控件（共享 automationId）不加 `label_text` / `ui_path` 必误命中（B3 模式 N） |
| 3 | **补提交手段与状态校验** | `postInputKeys` + `continueWhen`——转换不会替你判断"输完要不要回车" |

**打开与校验**：用 `WT_Flow_Editor` 打开转换结果 → 保存时会自动校验并同步注册表（D3 §5）。

---

## 5. Excel 双向往返

### 5.1 导出

```powershell
python flow_excel_io.py export --json flow_packages/flow_definition_某板块.json --xlsx 输出.xlsx
```

### 5.2 导入

```powershell
python flow_excel_io.py import --xlsx 输出.xlsx --json flow_packages/flow_definition_某板块.json
```

| 参数 | 说明 |
|---|---|
| `export --json` / `--xlsx` | 输入 JSON / 输出 Excel |
| `import --xlsx` / `--json` | 输入 Excel / 输出 JSON |

### 5.3 Excel 里有什么

| Sheet | 内容 |
|---|---|
| 主表 | 步骤与字段，**带下拉校验、表头注释、图例行、自适应列宽** |
| 动作指引 | 19 种动作的填写说明 |
| 示例 | 填写范例 |
| 选项表 | 供下拉校验引用的枚举清单 |

> 💡 下拉校验（数据有效性）来自 `_apply_list_validation` + `_build_option_lists`，所以**改枚举白名单后要重导一份 Excel**。

### 5.4 ⚠️ 往返五处同步（新增字段必读）

新增一个字段要同时改：**列定义 / 规范化 / 写出 / 读入 / 清理**，缺一处就往返丢数据（C4 §4.4）。

**验证手段**：

```powershell
# 往返一致性审计（导出 → 导入 → 对比字段）
python -c "from flow_excel_io import audit_flow_excel_roundtrip; audit_flow_excel_roundtrip()"
```

---

## 6. 常见问题

| 症状 | 排查 |
|---|---|
| 转换后大量步骤"找不到控件" | 控件库里没有对应窗口 → 先采集（D4）；或确认 `--control-map-dir` 指向正确 |
| 转换把上次的修正冲掉了 | 没带 `--previous` |
| 模板兜底没生效 | 转换时没给 `--screenshot-dir`，`templateKey` 为空 |
| Excel 改了导入后没生效 | 大概率踩了"五处同步"缺失；跑一次往返审计 |
| Excel 打开是乱码/列宽很窄 | 用导出的那份 Excel 改，不要新建空表手写表头 |
| 序号消歧失效 | 确认 `inspectData.foundIndex` 在（录制时 UIPath 段尾要有 `#[范围,N]`） |

---

## 7. 相关文档

| 想做什么 | 去哪 |
|---|---|
| 动作字段怎么填 | E1 |
| 流程 JSON 结构 | E2 |
| 采控件 | D4 |
| 批量跑不同参数 | D6 |
| 一条完整作业 | D8 |

---

核验：2026-09-11 / 涉及：`flow_recorder_converter.py`(argparse + L52–1045)、`flow_excel_io.py`(argparse + L305–1488)、`_launch_pywinauto_recorder.cmd`、`WT_Flow_Editor.py`(L894)
