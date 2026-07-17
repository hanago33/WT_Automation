# 外部控件采集适配器（uia-peek / axe-windows）

WT_Automation 现有 pywinauto 采集（`build_control_map_library.py`）的**可选补充**。
通过新接口调用两个开源项目，获取它们读取的控件信息，输出 `control_maps/` 兼容 JSON，
被现有 `tools/merge_standard_control_library.py` 无缝合并。**不改动任何现有采集/运行代码。**

## 为什么接入它们

现有 pywinauto 采集能覆盖大部分场景，但有两处短板，这两个项目正好补上：

| 短板 | 补充项目 | 能力 |
|---|---|---|
| 按坐标/焦点快速 peek 单个控件祖先链；实时录制键鼠+UI 上下文 | **uia-peek** | REST/SignalR，开箱即用 |
| 拿控件 Patterns（Invoke/Value/Toggle/Selection…），做控件模式校验 | **axe-windows** | AccessibilityInsights 底层，CLI + C# bridge |

## 目录结构

```
tools/external_capture/
├── __init__.py
├── uiapeek_client.py        UiaPeek HTTP 适配器（REST + 可选 SignalR 录制）
├── axewindows_client.py     Axe.Windows 适配器（CLI + bridge 模式）
├── capture.py               统一命令入口
├── axewindows_bridge/       C# JSON bridge 源码（需 .NET SDK 编译）
│   ├── Program.cs
│   └── AxeBridge.csproj
└── README.md                本文件

vendor/                      第三方项目源码（供参考/二开，已浅克隆）
├── uia-peek/                github.com/g4-api/uia-peek  (MIT)
└── axe-windows/             github.com/microsoft/axe-windows  (MIT)
```

## 一、uia-peek（主力补充，开箱即用）

### 运行前提（一次性）
1. 从 https://github.com/g4-api/uia-peek/releases 下载压缩包解压；
2. 以**管理员**身份运行 `UiaPeek.exe`（监听 `http://localhost:9955`，全局键鼠钩子需管理员）；
3. 验证：浏览器或 `curl http://localhost:9955/api/v4/g4/ping` 返回 `Pong`。

### 用法
```bash
# 健康检查
python -m tools.external_capture.uiapeek_client --ping

# peek 当前焦点元素（推荐：先在 WT 软件里聚焦目标控件）
python -m tools.external_capture.uiapeek_client --focused

# peek 指定屏幕坐标
python -m tools.external_capture.uiapeek_client --x 250 --y 300

# SignalR 录制 10 秒键鼠事件流（需 pip install signalrcore）
python -m tools.external_capture.uiapeek_client --record 10

# 不落盘仅打印
python -m tools.external_capture.uiapeek_client --focused --no-save
```

### 输出
`control_maps/YYYYMMDD_HHMMSS_<窗口标题>_uiapeek_control_map.json`，结构与现有
`*_control_map.json` 一致（`targetWindow` + `controlDefinitions[]` + `controlsTree`）。
chain 的祖先链上每个节点都会生成一个 control_definition，祖先也入库，定位更稳。

## 二、axe-windows（补充 Patterns，需额外配置）

### 两种模式

**A. CLI 模式**（开箱，信息有限）
需先安装 AxeWindowsCLI（MSI，见 `vendor/axe-windows/src/CLI/README.MD`）：
```bash
# 查找 CLI
python -m tools.external_capture.axewindows_client --find-cli

# 扫描进程（生成 .a11ytest 文件，可用 AccessibilityInsights 打开查看）
python -m tools.external_capture.axewindows_client --pid 1234
# 扫描指定窗口（HWND）+ 延迟 3 秒（便于捕获菜单/下拉）
python -m tools.external_capture.axewindows_client --pid 1234 --hwnd 333452 --delay 3
```
CLI 不直接输出结构化元素树，主要价值是生成 `.a11ytest` + 控制台摘要；
本适配器尽力解析 stdout，元素信息可能不全。

**B. Bridge 模式**（需 .NET 8 SDK，信息最全，含 Patterns）
```bash
# 编译并运行 C# bridge（首次会自动还原 NuGet）
python -m tools.external_capture.axewindows_client --pid 1234 --bridge
```
bridge 调用 Axe.Windows 官方 API 扫描进程，输出每个违规元素的 `Properties` + `Patterns`
的 JSON。Python 侧解析后，Patterns 写入 `inspectData.patterns`，正好支撑
"评分加入 Control Pattern 校验"优化项。

### 输出
`control_maps/YYYYMMDD_HHMMSS_pid<pid>_axewindows_control_map.json`。

## 三、统一入口

```bash
python tools/external_capture/capture.py uiapeek --focused
python tools/external_capture/capture.py axewindows --pid 1234 --bridge
```

## 四、与现有采集/运行的关系（重要）

- **完全独立**：不 import、不修改 `build_control_map_library.py` / `wt_flow_locator.py` /
  `wt_flow_executor.py` 等任何现有模块。现有 pywinauto 采集与流程运行**零影响**。
- **产出兼容**：输出的 JSON 落到 `control_maps/`，文件名匹配 `*_control_map.json`，
  现有 `tools/merge_standard_control_library.py` 会自动合并进 `standard_control_catalog.json`。
- **按需启用**：不启动 UiaPeek 服务、不装 axe-windows CLI 时，这些模块不会被任何
  现有流程调用，无副作用。

## 五、成熟度与注意事项

| 项 | 成熟度 | 说明 |
|---|---|---|
| uia-peek REST peek | 可用 | 主力补充，下载 release 即用 |
| uia-peek SignalR 录制 | 可用（需 signalrcore） | `pip install signalrcore` |
| axe-windows CLI | 可用 | 信息有限，主要拿 a11ytest |
| axe-windows bridge | 需编译 | 需 .NET 8 SDK；首次 `dotnet run` 自动还原 NuGet |
| C# bridge API | 参考实现 | 基于 axe-windows 2.4.2 官方 Automation API；若上游 API 变化，按 `vendor/axe-windows/src/Automation/Data/` 调整 |

> bridge 扫描返回的是"触发无障碍规则的元素"集合（非整树），但每个元素携带完整
> Properties + Patterns，足以支撑控件模式校验。整树采集请用 uia-peek 或现有 pywinauto。

## 六、许可证

两个上游项目均为 MIT（见 `vendor/*/LICENSE`）。本适配器代码随 WT_Automation 项目。
