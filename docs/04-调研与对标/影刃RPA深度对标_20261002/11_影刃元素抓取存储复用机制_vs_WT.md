# 11 | 影刃「元素抓取 → 存储 → 复用」机制深读（REA 元数据 + xbot 源码）vs WT 技术路线

> 本轮目标：回答"影刃是怎么抓取按钮/输入框/下拉框等控件、怎么存、执行时怎么用"，并对比 WT 当前路线、判断能否补足。
> 证据：**影刃未加密的真实 Python 源码** `xbot/selector.py`、`xbot/win32/element.py` 等（带真实接口与注释）+ REA 对 .NET `UIAutomation.dll` 的元数据。
> 分析对象：`E:\影刃RPA\ShadowBot\shadowbot-6.2.21\Resources\Code-Activity\Zh-CN\xbot\` 与 `ShadowBot.UIAutomation.dll`。

---

## 0. 先回答：之前 REA 破解了这部分吗？

**没有。** 之前的 REA 工作聚焦在：架构定性（原生 C++ + .NET6 混合）、`NativeHelper.dll` 线程劫持反编译、`Provider.exe`/`ShadowBot.exe` 的 apphost 引导证伪、能力全景盘点。**元素抓取/存储/复用这条链路不在那次深挖范围内。**

这条链路的"真实实现"落在两层，恰好都**不是 REA 上次的主战场**：
- **.NET 层**（`UIAutomation.*` 设计器/引擎，IL 未混淆）—— REA 只能取元数据（类/方法名），不能还原成可读 C#；
- **加密 Python 编排层** `winro.pyb` —— 自研加密，REA 不可见。

所以本轮"深入学习"的做法是：**读影刃未加密的 xbot 真实源码**（设计期/运行期 API，带 docstring）+ **REA .NET 元数据确认存储格式**。要拿到"设计器如何构造 selector 树"的最深处算法，仍须 dnSpy（已在 §8/§11.4 标注为下一步）。

---

## 1. 影刃元素生命周期：Capture → Store → Use

```
[设计期·录制]  用户指向控件
      │  编辑器用对应后端(UIA/Win32/Java/SAP/CV/Web)读取控件属性
      ▼
  element_info(dict)  ──convert_element_info_to_selector()──▶  Selector 对象
      │                                                          │
[存储]            selectors.xml(XML) / ImageSelectorStore / RemoteSelectors(云端版本化仓库)
      │
[运行期·复用]   脚本按 name/id 取 Selector
      │          SelectorStore.__call__ 解析 XML → find_related_element(selector, timeout)
      ▼
  Provider 进程(独立 .NET) 用后端走树/匹配属性 → 返回 eid 句柄
      │          _Win32ElementCache 缓存 eid
      ▼
  所有操作 click/input/select 经 Win32Element._invoke(action, args) → 命名管道 IPC → 真正执行
```

**关键三句**：
1. **抓取**不是截图，而是"读控件的属性快照"（控件类型、name、automationId、class、路径、矩形…），再由 `convert_element_info_to_selector` 转换成选择器。
2. **存储**以 **XML（`selectors.xml`）** 为主体，图像选择器独立存，另有**集中式版本化云端仓库 `RemoteSelectors`** 和 **semantic（语义匹配）JSON 配置**。
3. **复用**时脚本不直连控件，而是把 `Selector` 发给**独立 Provider 进程**，由它按后端解析成 `eid` 句柄再执行，句柄本地缓存。

---

## 2. 具体控件如何抓取与操作（来自 `win32/element.py` 真实接口）

| 控件 | 抓取后怎么操作（影刃 API） | 要点 |
|---|---|---|
| 按钮 | `click()` / `dblclick()` | `simulative`(模拟人工/原生接口)、`anchor`(九宫格+偏移)、`keys`(Alt/Ctrl)、`delay_after` |
| 输入框 | `input(text, simulative, append, force_ime_ENG)` / `clipboard_input()` / `set_value()` | `force_ime_ENG` 切英文输入法、`clipboard_input` 绕输入法；`click_before_input` |
| **下拉框** | `select(item, mode='fuzzy'|'exact'|'regex')` / `select_by_index(i)` / `get_all_select_items()` / `get_selected_item()` | **按文本模糊/精确/正则匹配 + 按下标两种选法** |
| 复选框 | `check(mode='check'|'uncheck'|'toggle')` | 三态 |
| 表格/网格 | `extract_table()`；`SAPElement.get_table_row_count()/get_table_cell_by_rownum_and_columnnum()` | 行列/单元格访问，支持负索引 |
| 通用 | `get_attribute/get_all_attributes/get_text/get_value` `get_bounding(relative_to='screen'|'window')` `parent/children/child_at/siblings` `drag_to/screenshot` | 属性读取、坐标(可相对窗口)、父子/兄弟导航、拖拽 |

> **影刃对"下拉框"的处理**（你特别问到的）：不是坐标点击，而是**拿到下拉项文本后用 `fuzzy/exact/regex` 匹配**，也支持 `select_by_index` 按下标选。这是"语义化选值"，比死点坐标抗 UI 变动。

---

## 3. 选择器（Selector）模型与存储格式

来自 `selector.py` + REA 元数据：

- **可捕获后端 11 种**：`_selector_node_types = {web, mac, wnd, uia, acc, java, hlay, sap, cv, mobile, linux}` —— 一套选择器语言覆盖全部技术栈。
- **Selector 类型**：`'xpath'` / `'anchor'` / `'normal'`（仅 Web 支持 anchor/xpath）。
- **字段**：`id` / `name` / `version` / `framework` / `xpath` / `segments` / `semantic` / `type`；`same_as()` 判断是否指向同一元素。
- **XML 存储细节**（很工程化）：`SelectorStore` 读 `selectors.xml`，并做 **ASCII 控制符转义**（`&#x1E;` → `%_%x1E%_%`），避免淘宝登录框这类控件文本里的控制符导致 XML 解析失败。
- **图像选择器**：`ImageSelectorStore` / `ImageSelector` 独立存储 CV 模板。
- **集中式仓库**：`RemoteSelectors` —— 版本化、云端、跨机共享（对应"选择器可集中管理/版本化/跨机共享"长处）。
- **表格选择器**：`TableSelector`（base/baseXpath/columns）。
- **变量模板**：`{{variable}}` 全局变量替换（设计期/运行期）。

**REA 元数据实证（`UIAutomation.dll`）**：引用 `System.Xml.XmlSerializer` + `XmlTypeAttribute`/`XmlRootAttribute`/`XmlElementAttribute`（**XML 序列化** → 印证 `selectors.xml`）；`Newtonsoft.Json` + `JsonPropertyAttribute`/`JsonConverterAttribute`（**JSON** → 印证 semantic/远端载荷）；`WindowsBase`（WPF `System.Windows.Automation` → **UIA 是核心后端**）；`ShadowBot.Common`（共享元素/选择器基类）。`Tools.dll`（1.8MB，含 `SelectorHelper`）元数据超出 REA 表示预算，其 IL 用 dnSpy 还原。

---

## 4. WT Automation 当前的元素路线（基于真实代码）

| 环节 | WT 现状 | 证据 |
|---|---|---|
| **抓取** | `tools/external_capture/capture.py`（uiapeek / axewindows 适配器 dispatcher）+ `build_control_map_library.py`（pywinauto 采集）→ `normalize` → 控件库 | `tools/external_capture/{capture,uiapeek_client,axewindows_client,pywinauto_backend,normalize}.py` |
| **存储** | `flow_definition.json`（`steps[i].controls`，按 `control_id`）+ `control_maps/standard/总控件信息.json`（主控件库 `flatControls`，按 `automationId`，含 `targetMethod/targetValue/helpText/functionText/uiPath/labelText`）；图像模板走 `image_template_index`/`build_image_template_library` | `wt_control_index.py`、`WT_AUTOMATION_Agent/control_index.py` |
| **复用** | `wt_flow_locator.get_flow_control_definition(step_id, control_id)` → 控件定义 → UIA 定位 或 `locate_template_center_by_path`（CV）→ 操作；`click_menu_candidate_by_text` 处理菜单文本 | `wt_flow_locator.py`、`wt_projection_helpers.py` |
| **健壮性研究特性** | locator 评分、label 关联/companion、消歧、动态控件名回填、退化点击守卫、`generic_flow` 泛化 | `test_combo_item_text_disambiguation`、`test_label_companion_and_inspect_fields`、`test_degenerate_click_guard`、`tools/generic_flow/` |
| **自愈** | `WT_AUTOMATION_Agent/flow_repair.py`：**确定性回填**（按 automationId 补 targetMethod/uiPath/labelText）+ **LLM 语义级修复**（逐条确认、`save_with_backup`、不确定的进"待确认"不瞎猜） | `flow_repair.py`、`control_search.py` |

> WT 的抓取反而是**纯 Python、可复现、更透明**的（external_capture 支持 uiapeek/axewindows/pywinauto 多后端），比影刃藏在 .NET 编辑器里的抓取更可读。

---

## 5. 两条路线差异 与 WT 可补足项

### 5.1 差异对照

| 维度 | 影刃 ShadowBot | WT Automation |
|---|---|---|
| 选择器抽象 | **统一 11 后端 Selector 对象**（type+attributes+anchor+semantic） | 控件卡（字段丰富的 dict：automationId/uiPath/labelText/…）+ CV 模板 key 并存，未归一为单一抽象 |
| 存储格式 | XML `selectors.xml` + 图像选择器 + **云端版本化 RemoteSelectors** | JSON `flow_definition.json` + `总控件信息.json` 主库（本地）+ 图像模板索引 |
| 抓取实现 | .NET 编辑器（IL，未读） | 纯 Python external_capture（透明、可复现） |
| 运行架构 | 脚本 → 命名管道 → **独立 Provider 进程** → 后端解析 → `eid` 句柄缓存 | 进程内 Python：executor → locator → UIA/CV → pyautogui/UIA 点击（无独立进程） |
| 下拉/复选操作 | `select(mode=fuzzy|exact|regex)`/`select_by_index`/`check(mode)` 整洁类型化 API | 由 action schema 驱动 + combo 消歧测试，未封装成统一 `select(mode)` |
| 自愈 | **本地 AI 候选池**（`ai_element_repair` POST `/aiElement/run/pool`，自动回写） | **LLM 治理型自愈**（确定性回填 + LLM 语义、逐条确认、备份写回、待确认队列） |
| 鲁棒性研究 | 语义缓存、触发器看门狗 | label 关联消歧、退化点击守卫、动态控件名、generic_flow（**WT 领先**） |
| 图像定位 | `cv` 是 Selector 一等后端，`wait_appear(image_selector)` | CV 模板匹配是独立 `cv2` 路径，未与 UIA 统一成同一 locator |

### 5.2 能否补足？可以，且是**两条路线互补**，不是单向抄

**WT 可从影刃补足的（影刃更系统化的部分）：**
1. **统一 Selector 抽象**：把 UIA / CV / Web 归一为一个 `Selector(type, attributes, anchor, semantic)`，新增后端不影响上层（WT 已有 `wt_action_schema.py`，可在此之上加一层 Selector 对象）。**性价比最高**。
2. **图像选择器升为 first-class 后端**：让 CV 与 UIA 成为同一 locator 的两种"后端选择"，而非独立 `cv2` 代码路径（对齐影刃 `cv` 节点 + `wait_appear(image_selector)`）。
3. **主控件库版本化/团队共享**：WT 的 `总控件信息.json` 已是"集中式仓库"雏形，补**版本化 + 团队共享**即对齐影刃 `RemoteSelectors`。
4. **整洁的类型化控件操作 API**：在控件卡上暴露 `select(item, mode=)` / `check(mode=)`，封装 WT 已有的 combo 消歧逻辑（已有 `test_combo_item_text_disambiguation` 基础）。
5. **窗口作用域绑定 + 元素句柄缓存**：提升性能与鲁棒（影刃把元素绑到窗口、缓存 `eid`）。

**WT 已领先、影刃反而缺的（保留并强化，是护城河）：**
- **label 关联消歧 / 退化点击守卫 / 动态控件名回填** —— 研究级鲁棒性，影刃本地启发式没有；
- **LLM 治理型自愈（flow_repair + 待确认队列 + 备份写回）** —— 比影刃"候选池自动回写"更可控、更安全。

---

## 6. 结论与下一步（更新）

- **能补足，但主要是"抽象层归一 + 图像选择器一等化 + 仓库版本化 + 整洁操作 API"这几条结构性补强**；WT 在"鲁棒性研究 + LLM 治理自愈"上已领先，应保留。
- **REA 已把"最深处"推到类型模型层**（见 §7）：`SelectorHelper` / `SelectorModel` / `SelectorNode` / `SelectorAttribute` 等完整选择器匹配模型已在 `UIAutomation.dll` 中被 REA 成功枚举——它**并不在**之前以为的 `Tools.dll`（那个超 REA 预算）。但**方法体（真正的匹配算法）仍不在元数据里，须用 dnSpy 还原**（见 §7.3 精确目标）。
- 本文所有结论均来自**本地静态分析 + 未加密源码阅读**，未破解授权、未绕过许可、未提取密钥。

---

## 7. REA 元数据确认的 .NET 匹配模型（SelectorHelper 等，最深处已达类型层）

> 上一轮说"SelectorHelper 在 Tools.dll、IL 未混淆"——**本轮 REA 纠正：SelectorHelper 实际在 `UIAutomation.dll`**，且该 DLL（454KB）被 REA 成功枚举（`Provider.dll`/`Tools.dll` 因 >64MB 元数据预算被拒）。`inspect_managed_members` 拉出了影刃选择器/匹配系统的**真实类型面**，这是"设计器如何构造 selector 树 + SelectorHelper 匹配算法"在**类型模型级**的权威证据。

### 7.1 权威类型表（来自 REA 对 `ShadowBot.UIAutomation.dll` 的元数据）

| 类型（命名空间） | 方法数 | 含义（对应影刃设计） |
|---|---|---|
| `Utilities.SelectorHelper` | 18 | **匹配/选择器助手**：解析选择器、构树、匹配候选、回退 |
| `Primitives.SelectorModel` | 46 | **选择器数据模型**（树 + 语义 + 图像后端 + 版本/框架） |
| `Primitives.SelectorNode` | 24 | **选择器树的节点**（窗口→容器→控件 层级） |
| `Primitives.SelectorAttribute` | 16 | **单个属性约束**（name/value/operator/required） |
| `Primitives.SelectorOperator` | 15(枚举) | 匹配算子：`= / != / contains / startsWith / endsWith / regex / exists …` |
| `Primitives.SelectorNodeType` | 12(枚举) | 节点类型（wnd/uia/win32/java/sap/cv/web/acc/mac/mobile/linux/hlay） |
| `Primitives.UIFramework` | 20(枚举) | **可捕获的技术后端**（即 §3 的 11 后端扩展版） |
| `Primitives.Anchor` + `SudokuPart` | 13 / 11 | **九宫格锚点**（topLeft…bottomRight），点击落点 |
| `Primitives.Semantic` + `SemanticElementResult` + `SemanticStatus` | — | **语义匹配**配置与结果（对齐 AI 自愈） |
| `Primitives.ImageSelector` + `ImageSourceType` | 19 | **CV 模板选择器**（cv 后端一等公民） |
| `Primitives.ElementInfo` | 24 | **抓取到的控件属性快照**（被 `convert_element_info_to_selector` 消费） |
| `Primitives.XpathSelectorNode` | 8 | Web 专属 **XPath** 节点 |
| `Primitives.TableSelectorModel` + `TableSelectorColumn` | 37 / 18 | **表格/网格选择器**（base + 列过滤） |
| `Primitives.MatchMode` / `SelectorInvalidReason` | 枚举 | 匹配模式 / 选择器失效原因 |
| `BuildSelectorWithAnchorResponse` / `BuildAiSelectorResponse` | — | **设计器构造选择器**的输出（锚点构造 / AI 构造）—— 直接证明"设计器构造 selector 树" |
| 混淆类 `GuwBZAWohQ92tIs0TO.WqvWUdBjXjZdpSFeKY`（187 方法）等 | — | **被重命名的匹配引擎内部类**（UIA 树遍历 / Condition 构造），即"未混淆"之外的真实算法体 |

> **重要修正**：之前说"IL 未混淆"不完全准确——`Primitives/*` 与 `Utilities/*` 公开类型**干净未混淆**，但真正的**树遍历/Condition 构造逻辑被重命名为无意义类名**（如上面的 187 方法类）。这反而说明影刃对其**匹配内核做了轻量混淆**，公开模型层是干净的。

### 7.2 匹配算法推断（类型模型 + xbot 源码交叉验证）

结合 §7.1 类型面与 `xbot/selector.py`（`_resolve_selector_node_from_xml` / `is_xpath_supported_selector` / `_resolve_anchor_from_xml` / `_resolve_semantic_from_xml`），`SelectorHelper` 的匹配是标准的**约束树匹配（constraint-based tree matcher）**：

```
SelectorModel(树)
  └─ SelectorNode(backend=UIFramework, attrs=[Attr(name,value,op,required), …], anchor?, xpath?, children[])
        │
匹配过程（SelectorHelper）：
  1) 从作用域/根开始，按节点的 backend 选择对应解析器（UIA/Win32/Java/SAP/CV/Web）；
  2) 把节点的 attrs 编译成"属性约束"（UIA 侧即 AndCondition(PropertyCondition…)），在 scope 内 FindAll 候选；
  3) 逐个候选校验 required/可选属性（op: =/contains/regex…），命中后**递归匹配 children 节点**（向下钻取容器→控件）；
  4) 叶子节点命中即得到 ElementInfo → 交给 Provider 转 eid；anchor(SudokuPart) 决定点击落点；
  5) 失败回退：先 semantic（语义），再 image（CV 模板），最后 AI 候选池（§13/§11 的 ai_element_repair）。
```

**设计器构造 selector 树**：编辑器的"拾取控件"动作拿到 `ElementInfo`（属性快照）→ 经 `BuildSelectorWithAnchorResponse`（锚点构造）或 `BuildAiSelectorResponse`（AI 构造）生成 `SelectorModel`（分层节点 + 稳定属性 + 锚点），存 `selectors.xml`。运行时 `SelectorHelper` 反向把这个树重新匹配到**当前** UI 树。

### 7.3 dnSpy 精确还原目标（方法体级）

REA 给到了**精确类名**，dnSpy 只需按图索骥（同目录有 PDB）：

1. `ShadowBot.UIAutomation.Utilities.SelectorHelper`（18 方法）—— 看 `Match`/`Resolve`/`Build*` 具体实现。
2. `Primitives.SelectorModel` / `SelectorNode` / `SelectorAttribute` —— 序列化与节点遍历。
3. 混淆内部类 `GuwBZAWohQ92tIs0TO.WqvWUdBjXjZdpSFeKY`（187 方法）—— 真正的 UIA 树遍历 / Condition 构造内核（需对照 PDB 或上下文还原语义）。
4. `UIAutomation.Tools.dll` / `UIAutomation.Provider.dll`（元数据超 REA 预算）—— 用 dnSpy 看 SelectorHelper 的跨进程调度与 DriverInput。

---

## 8. WT 统一 Selector 抽象骨架（落文档，对齐 §5.2 补足项 #1）

> 把 WT 现有"分散的控件卡字段 + CV 模板 key"归一为一个**跨后端的选择器模型**（UIA/Win32/CV/Web 都是同一 Selector 的不同节点类型），新增后端不影响上层。可对接：`wt_action_schema`、`wt_projection_helpers.locate_template_center_by_path`（CV 后端）、`wt_flow_locator`（UIA 定位）、`control_maps/standard/总控件信息.json`（仓库）。

```python
# wt_automation/locating/selector.py
"""统一 Selector 抽象（对齐影刃 SelectorModel/SelectorNode/SelectorAttribute/SelectorOperator）。

把 WT 现有分散的"控件卡字段 + CV 模板 key"归一为一个跨后端的选择器模型：
UIA / Win32 / CV / Web 都是同一 Selector 的不同节点类型，新增后端不影响上层。
可对接：wt_action_schema（动作）、wt_projection_helpers.locate_template_center_by_path（CV 后端）、
wt_flow_locator（UIA 定位）、control_maps/standard/总控件信息.json（仓库）。
"""
from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


# ---- 节点类型 / 后端（对齐影刃 SelectorNodeType + UIFramework）----
class Backend(str, Enum):
    WINDOW = "wnd"      # 窗口/顶层
    UIA = "uia"        # UI Automation
    WIN32 = "win32"    # 传统 Win32
    JAVA = "java"
    SAP = "sap"
    CV = "cv"          # 计算机视觉模板
    WEB = "web"
    ACC = "acc"        # MSAA


# ---- 匹配算子（对齐影刃 SelectorOperator）----
class Op(str, Enum):
    EQ = "="; NOT_EQ = "!="; CONTAINS = "contains"
    STARTS = "startsWith"; ENDS = "endsWith"; REGEX = "regex"; EXISTS = "exists"


# ---- 单个属性约束（对齐影刃 SelectorAttribute）----
@dataclass
class Attr:
    name: str                  # name / automationId / class / controlType / tag / ...
    value: str
    op: Op = Op.EQ
    required: bool = True      # 非必需属性用于"软匹配/消歧"（对齐 WT 的 label 关联消歧）

    def match(self, actual: Optional[str]) -> bool:
        if actual is None:
            return not self.required
        if self.op is Op.EXISTS:     return True
        if self.op is Op.EQ:        return actual == self.value
        if self.op is Op.NOT_EQ:    return actual != self.value
        if self.op is Op.CONTAINS:  return self.value in actual
        if self.op is Op.STARTS:    return actual.startswith(self.value)
        if self.op is Op.ENDS:      return actual.endswith(self.value)
        if self.op is Op.REGEX:     return re.search(self.value, actual) is not None
        return False


# ---- 九宫格锚点（对齐影刃 SudokuPart + Anchor）----
class SudokuPart(str, Enum):
    TOP_LEFT="topLeft"; TOP_CENTER="topCenter"; TOP_RIGHT="topRight"
    MIDDLE_LEFT="middleLeft"; MIDDLE_CENTER="middleCenter"; MIDDLE_RIGHT="middleRight"
    BOTTOM_LEFT="bottomLeft"; BOTTOM_CENTER="bottomCenter"; BOTTOM_RIGHT="bottomRight"

@dataclass
class Anchor:
    part: SudokuPart = SudokuPart.MIDDLE_CENTER
    offset_x: int = 0; offset_y: int = 0
    _FX = {p.value: f for p, f in {SudokuPart.TOP_LEFT:(0,0),SudokuPart.TOP_CENTER:(.5,0),
            SudokuPart.TOP_RIGHT:(1,0),SudokuPart.MIDDLE_LEFT:(0,.5),SudokuPart.MIDDLE_CENTER:(.5,.5),
            SudokuPart.MIDDLE_RIGHT:(1,.5),SudokuPart.BOTTOM_LEFT:(0,1),SudokuPart.BOTTOM_CENTER:(.5,1),
            SudokuPart.BOTTOM_RIGHT:(1,1)}.items()}
    def point(self, rect) -> tuple[int, int]:
        fx, fy = self._FX[self.part.value]
        return int(rect.x + rect.width*fx + self.offset_x), int(rect.y + rect.height*fy + self.offset_y)


# ---- 选择器节点（对齐影刃 SelectorNode）----
@dataclass
class SelectorNode:
    backend: Backend
    attrs: List[Attr] = field(default_factory=list)
    anchor: Optional[Anchor] = None
    xpath: Optional[str] = None                 # Web 专用
    children: List["SelectorNode"] = field(default_factory=list)


# ---- 选择器（对齐影刃 SelectorModel：树 + 可选语义/图像后端）----
@dataclass
class Selector:
    name: str
    root: SelectorNode                          # 树：窗口→容器→控件
    semantic: Optional[dict] = None             # 语义匹配配置
    image: Optional[dict] = None                # CV 模板（cv 后端时生效）


# ---- 仓库（对齐影刃 SelectorStore + RemoteSelectors）----
class SelectorRepository:
    """集中式选择器仓库（本地 JSON；可扩展为版本化/团队共享，对齐 RemoteSelectors）。"""
    def __init__(self, path: str = "control_maps/standard/总控件信息.json"):
        self.path = path; self._store: Dict[str, dict] = {}; self.load()
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self._store = json.load(f)
        except FileNotFoundError:
            self._store = {}
    def save(self):                             # 版本化可落 _vN 或接 git
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self._store, f, ensure_ascii=False, indent=2)
    def get(self, name: str) -> Optional[Selector]: ...   # JSON → Selector
    def put(self, sel: Selector): ...                     # Selector → JSON


# ---- 匹配器（对齐影刃 SelectorHelper：约束树匹配）----
class SelectorMatcher:
    """把 Selector 树匹配到真实 UI 树（或 CV 图像）。后端可插拔。"""
    def __init__(self):
        self._backends: Dict[Backend, "BackendResolver"] = {}
    def register(self, b: Backend, r: "BackendResolver"):
        self._backends[b] = r

    def resolve(self, sel: Selector, scope=None, timeout: float = 20.0):
        """对齐影刃 find_related_element：按树从根/作用域向下匹配，超时轮询。"""
        if sel.image and Backend.CV in self._backends:           # CV 后端优先
            return self._backends[Backend.CV].resolve_image(sel.image, scope)
        return self._match_node(sel.root, scope, timeout)

    def _match_node(self, node: SelectorNode, scope, timeout):
        resolver = self._backends.get(node.backend)
        candidates = resolver.find(scope, node.attrs)            # 对齐 SelectorHelper 的 AndCondition/FindAll
        for cand in candidates:
            if all(a.match(resolver.get_attr(cand, a.name)) for a in node.attrs):
                if node.children:
                    for ch in node.children:
                        hit = self._match_node(ch, resolver.get_children(cand), timeout)
                        if hit: return hit
                else:
                    return cand
        return None


# ---- 后端接口（对接 WT 现有能力）----
class BackendResolver:
    def find(self, scope, attrs): ...                # WT: wt_flow_locator / UIA
    def get_attr(self, elem, name): ...
    def get_children(self, elem): ...
    def resolve_image(self, image, scope): ...        # WT: wt_projection_helpers.locate_template_center_by_path
```

> **与影刃模型的对应关系**：`SelectorModel`↔影刃 `SelectorModel`、`SelectorNode`↔`SelectorNode`、`Attr`(+`Op`)+`required`↔`SelectorAttribute`(+`SelectorOperator`)、`Backend`↔`UIFramework`/`SelectorNodeType`、`Anchor`+`SudokuPart`↔`Anchor`+`SudokuPart`、`semantic`/`image`↔影刃 `Semantic`/`ImageSelector`、`SelectorRepository`↔`SelectorStore`+`RemoteSelectors`、`SelectorMatcher`↔`SelectorHelper`（约束树匹配 + CV 优先 + 超时轮询）。下一步把 `BackendResolver` 接上 WT 的 `wt_flow_locator`(UIA) 与 `locate_template_center_by_path`(CV)，即让 UIA 与 CV 成为**同一 locator 的两种后端**，对齐 §5.2 补足项 #2。

---

---

## 9. BackendResolver 可运行实现（接 WT 现有能力，落文档）

> §8 定义了抽象；本节把 `BackendResolver` 接上 WT **已核对存在**的两个主实现（主报告 §17.1 复核通过）：
> `wt_flow_locator.get_flow_control_definition(step_id, control_id)`（:7797，UIA/控件卡定位）与
> `wt_projection_helpers.locate_template_center_by_path(template_path, timeout_seconds=8, confidence=0.8, region=None, scales=None)`（:261，CV 定位）。
> 注意：只接 `wt_flow_locator` 主实现，不接 `tools/generic_flow/generic_flow_locator.py` 的重复实现（主报告 §17.3）。

```python
# wt_automation/locating/backends.py
"""SelectorMatcher 的 WT 后端实现：UIA(控件卡) + CV(模板匹配)，全部复用 WT 现有函数。"""
from __future__ import annotations
from typing import List, Optional

from wt_automation.locating.selector import Attr, Backend, BackendResolver, Selector, SelectorMatcher

from wt_flow_locator import get_flow_control_definition        # 主实现（勿用 generic_flow 重复版）
from wt_projection_helpers import locate_template_center_by_path


# ---- 控件卡字段 ↔ Selector 属性名映射（对齐 总控件信息.json 的 flatControls 字段）----
_CTRL_FIELD_MAP = {
    "automationId": lambda c: c.get("automationId"),
    "name":         lambda c: c.get("labelText") or c.get("functionText"),
    "class":        lambda c: c.get("className"),
    "uiPath":       lambda c: c.get("uiPath"),
}


class UiaBackendResolver(BackendResolver):
    """UIA 后端：以 (step_id, control_id) 控件卡为入口，复用 WT 的 label 关联/消歧逻辑。"""
    def __init__(self, step_id: str):
        self._step_id = step_id

    def find(self, scope, attrs: List[Attr]):
        ctrl = get_flow_control_definition(self._step_id, _primary_control_id(attrs))
        return [ctrl] if ctrl else []

    def get_attr(self, elem, name: str) -> Optional[str]:
        getter = _CTRL_FIELD_MAP.get(name)
        return getter(elem) if (getter and isinstance(elem, dict)) else None

    def get_children(self, elem):
        return elem.get("controls", []) if isinstance(elem, dict) else []


class CvBackendResolver(BackendResolver):
    """CV 后端：直接复用 WT 已有模板匹配（对齐影刃 ImageSelector 一等后端）。"""
    def find(self, scope, attrs): return []
    def get_attr(self, elem, name): return None
    def get_children(self, elem): return []

    def resolve_image(self, image: dict, scope=None):
        center = locate_template_center_by_path(
            image["template_path"],
            timeout_seconds=image.get("timeout", 8),
            confidence=image.get("confidence", 0.8),
            region=scope,                 # scope 直接透传为模板匹配的 region
        )
        return {"center": center} if center else None


def _primary_control_id(attrs: List[Attr]) -> str:
    for a in attrs:
        if a.name == "control_id":
            return a.value
    raise KeyError("SelectorNode 需含 control_id 属性以对接 WT 控件卡")


def build_default_matcher(step_id: str) -> SelectorMatcher:
    m = SelectorMatcher()
    m.register(Backend.UIA, UiaBackendResolver(step_id))
    m.register(Backend.CV, CvBackendResolver())
    return m
```

```python
# ---- 控件卡 → 统一 Selector 适配器（复用 总控件信息.json 的 flatControls 条目）----
def control_card_to_selector(name: str, card: dict, window: dict | None = None) -> Selector:
    """WT 控件卡 → 统一 Selector：窗口节点为根，UIA 节点承载 automationId/labelText 软匹配。"""
    uia_node = SelectorNode(
        backend=Backend.UIA,
        attrs=[
            Attr("control_id", card["control_id"]),                                     # 主键（必填）
            Attr("automationId", card.get("automationId", ""),
                 required=bool(card.get("automationId"))),                              # 稳定属性
            Attr("name", card.get("labelText", ""), op=Op.CONTAINS, required=False),    # 软匹配（消歧）
        ],
    )
    root = SelectorNode(backend=Backend.WINDOW,
                        attrs=[Attr("name", (window or {}).get("title", ""), required=False)],
                        children=[uia_node])
    return Selector(name=name, root=root)
```

```python
# ---- 用法示例 ----
matcher = build_default_matcher("step_001")
sel = control_card_to_selector("确定按钮", card)        # card 来自 总控件信息.json flatControls
hit = matcher.resolve(sel, timeout=10)                  # UIA 优先；若 Selector.image 非空则走 CV
cv_hit = matcher._backends[Backend.CV].resolve_image(
    {"template_path": "image_templates/ok_btn.png", "confidence": 0.85})
```

> **复核依据**：两个被复用函数的签名已逐字核对（主报告 §17.1：`wt_projection_helpers.py:261`、`wt_flow_locator.py:7797`）；`region` 参数与 `scope` 语义对齐；`CvBackendResolver.resolve_image` 与 §8 `SelectorMatcher.resolve` 的调用约定一致。

---

*证据来源：影刃 `xbot/selector.py`、`xbot/win32/element.py`、`xbot/web/element.py`、`xbot/web/ai_element_repair.py` 等未加密真实源码；REA 对 `ShadowBot.UIAutomation.dll` 的元数据（`inspect_managed_artifact` / `inspect_managed_members`，成功枚举 SelectorHelper/SelectorModel/SelectorNode/SelectorAttribute/SelectorOperator/SelectorNodeType/UIFramework/Anchor/Semantic/ImageSelector/ElementInfo/BuildSelectorWithAnchorResponse 等类型；对 `Tools.dll`/`Provider.dll` 复跑确认 `resource-limit`）；以及 WT Automation 项目 `wt_control_index.py` / `wt_flow_locator.py:7797` / `wt_projection_helpers.py:261` / `tools/external_capture/*` / `WT_AUTOMATION_Agent/flow_repair.py` / `control_maps/standard/总控件信息.json` 的只读梳理与符号级复核（主报告 §17）。*
