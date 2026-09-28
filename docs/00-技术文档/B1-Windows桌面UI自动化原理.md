# B1 · Windows 桌面 UI 自动化原理

> 本篇回答：**这套系统到底是怎么"看见"和"操作"另一个 Windows 程序的**，以及这些底层机制带来了哪些不可逆的约束。
> 读者：二次开发者、控件库维护者、要排查"点了没反应/点了别的控件"的人。
>
> 本篇不写"怎么用"（那是卷 D），只写"为什么必须这样"。

---

## 1. 为什么必须先懂这一层

WT_Automation 的全部能力建立在**操作系统提供的界面可达性**之上。UIA / Win32 是它的眼睛和手；理解它们的边界，就能预判绝大多数失败：

| 现象 | 本质原因 | 对应章节 |
|---|---|---|
| 某些控件在运行时"找不到"，但 Inspect 里能看到 | 该宿主 `IsControlElement=False`，Control View 不可见 | §3.3 |
| 点击坐标系统性偏移 | `rectangle()` 基准不稳 / DPI 虚拟化 | §4、§5 |
| 点了没反应 | 命中了只读展示层，或输入未提交 | 卷 B5 |
| 界面不按脚本走，且进程无任何 Python traceback 就死了 | 原生堆损坏 / Tk 跨线程 | §8 |
| 内网机 worker 看不到 MUP 窗口 | 会话隔离 | §7 |
| 管理员程序不接受输入 | UIPI | §7 |

---

## 2. Windows 三套自动化接口谱系

| 接口 | 年代 | 核心 | 本项目的使用 |
|---|---|---|---|
| **Win32**（USER/GDI） | 最早 | `HWND` + 窗口消息 | **枚举顶层窗口**、取稳定矩形；pywinauto 的 `win32` 后端 |
| **MSAA**（IAccessible） | Win95+ | 简化的可访问对象模型 | 基本不用，仅为兼容链 |
| **UIA**（UI Automation） | Vista+ / .NET 3.0 | **树形控件模型 + 属性 + Control Pattern** | **主力**。pywinauto `uia` 后端全部由此驱动 |

**为什么选 UIA 作主力**：

| 能力 | Win32 | UIA |
|---|---|---|
| 拿到按钮里的文字 | 需 `WM_GETTEXT`，自定义控件常拿不到 | ✅ `Name` 属性 |
| 拿到开发者定义的稳定 ID | ❌ 只有 `HWND`（每次变化） | ✅ `AutomationId` |
| 知道控件"是按钮还是文本框" | 靠类名猜 | ✅ `ControlType`（Button/Edit/ComboBox/CheckBox…） |
| 知道控件"能做什么" | ❌ | ✅ **Control Pattern**（Invoke / Value / Toggle / Selection …） |
| 自定义 / WPF 控件 | 几乎不可达 | ✅（前提是开发者实现了 Provider） |

> 💡 `AutomationId` 是本项目**最有价值的属性**——它是开发者显式指定的稳定标识，不随语言、分辨率、序号变化。控件库的定位推荐把 `automation_id+control_type` 排在第一优先级（100 分），原因在此。见卷 B3 §3。

---

## 3. UIA 的核心概念与本系统的映射

### 3.1 控件树与三种视图

UIA 把界面表示为**一棵树**，但同一棵树有三种"视图"（过滤器）：

| 视图 | 含义 | 本项目谁在用 |
|---|---|---|
| **Raw View** | 原样，包含所有 UIA 元素 | `IUIA().iuia.RawViewWalker` + 手写 BFS（`wt_flow_locator.py`） |
| **Control View** | 只含 `IsControlElement=True` 的元素 | pywinauto 的 `children()` / `descendants()` / `FindAll` |
| **Content View** | 只含 `IsContentElement=True` 的元素 | 基本不用 |

### 3.2 属性模型

每个元素携带一组属性，本项目采集并用于定位的字段：

`automationId` / `name` / `className` / `controlType` / `localizedControlType` / `frameworkId` / `processId` / `helpText` / `boundingBox` / `isKeyboardFocusable` / `hasKeyboardFocus` / `isEnabled` / `isOffscreen` / `nativeWindowHandle`

> ⚠️ **字段对齐纪律**：三路采集适配器（自有 pywinauto 采集器 / uiapeek / axe-windows）必须输出同一套字段，否则合并进标准库时会**静默丢字段**。历史上 `localizedControlType`、`isKeyboardFocusable`、`hasKeyboardFocus` 就是 Axe 桥接漏提后补上的。

### 3.3 `IsControlElement=False` 的宿主——本项目最隐蔽的坑

WPF 的 `TextBox` 内部编辑宿主 **`PART_ContentHost`**、`ScrollViewer` 等装饰性容器，其 `IsControlElement=False`，意味着：

- ❌ Control View（即 `children()` / `descendants()` / `FindAll`）**看不见它**
- ✅ Raw View 能看到

本项目为此专门做了分支：`wt_flow_locator.control_definition_expects_raw_view()` 判定控件定义是否需要走 Raw View 链，触发后转 `iter_raw_view_fallback_candidates()`。

另一半问题更难：`PART_ContentHost` 在 Raw View 里采集时是 `Pane`，运行时 UIA 可能报成 `Edit`——**连 control_type 都不稳定**。因此评分里有一段专门的放宽逻辑（`wt_flow_locator.py` L3651–3660）：当 `automationId` 精确命中 `PART_ContentHost` 时，放宽 control_type 候选，改由 `labelText` / `uiPath` 消歧。

### 3.4 为什么用 RawViewWalker BFS，而不是 `wrapper.children()`

| 方案 | 问题 |
|---|---|
| `wrapper.children()` / `descendants()` | 走 Control View（漏 `PART_ContentHost` 等）；且一次性拉全树，巨大 WPF 窗口耗时 20–36 秒 |
| **手写 RawViewWalker BFS** | 可控预算（元素数上限 + 深度上限 + 时间预算）；能配降权过滤；可做引导式收敛 |

本项目有三档 Raw View 遍历，均用 `IUIA().iuia.RawViewWalker`：

| 函数 | 预算 | 用途 |
|---|---|---|
| `_iter_raw_view_findall_candidates` | `max_results=128` | 受限全树候选 |
| `_iter_raw_view_guided_candidates` | `max_depth=6, max_elements=800` | 引导式收敛（更快） |
| `iter_raw_view_fallback_candidates` | `max_elements=30000, budget_seconds=15.0` | 末级兜底 |

> 📌 **一个必须记住的写法约定**：RawViewWalker 遍历时必须把容器的**全部直接子**入栈（`GetFirstChildElement` + 依次 `GetNextSiblingElement`）。只入首子会导致"批量枚举只遍历第一棵子树"——本项目踩过（知识库模式 O）。
>
> 📌 **另一个**：所有"返回候选列表"的函数在异常/提前返回路径必须 `return []` 而非 `None`。返回 `None` 会让调用方 `for candidate in <None>` 抛 `TypeError`，导致整条定位链在该步骤**提前炸穿**（模式 K）。

---

## 4. 坐标、矩形与窗口查找

| 关注点 | 说明 |
|---|---|
| **矩形基准不稳** | `pywinauto` 的 `rectangle()` 返回值可能是**外框**也可能是**客户区**，不同场景不一致 → 相对区域点击整体偏移。修复范式：优先用原生窗口句柄 `GetWindowRect` 取稳定基准 |
| **窗口过滤** | `wrapper_matches_expected_window_title` + `get_wrapper_top_level_window`（向上找 `ControlType == Window`） |
| **标题通配** | `"*"` / `"__all__"` 表示不约束窗口标题；`uipath_is_main_window_root` 为真时自动放宽为 `*` |
| **句柄兼容性** | 句柄可能来自 `GetForegroundWindow`（int）或 Win32 枚举侧的 `"0x…"` 字符串 → 必须 `int(handle, 0)` 兼容十进制与 0x 前缀。曾因 `int("0x…")` 抛错被吞，导致"回退 MUP 可见窗口候选"分支恒为空（模式 K） |
| **主进程 filter** | 数字 PID 每次运行会变，**不能作硬过滤**，否则 MUP 重启后窗口全被过滤掉（`count=0`）。只保留非数字进程标识作候选 |

---

## 5. DPI 虚拟化（为什么界面会"一边虚一边小"）

**问题**：总控台与流程编辑器都是 tkinter 程序。若进程不声明 DPI 感知，Windows 会对部分窗口做 **DPI 虚拟化位图拉伸**（看起来正常但发虚），另一些按原生高分渲染（内容显小），导致不同窗口缩放不一致；坐标换算也随之错位。

**`wt_dpi.py` 的解法**（四件事）：

1. 在创建 `tk.Tk()` **之前**调用 `enable_process_dpi_awareness()`，声明 Per-Monitor DPI Aware (V2)。
2. 按"物理分辨率 / 逻辑分辨率"算缩放系数，套到 Tk 的 `scaling`。
3. 支持手动档位：**自动 / 100% / 125% / 150% / 175% / 200%**，配置落在 `workspace/ui_scale.json`，**所有 tkinter 窗口共用一份**；首次启动默认 **150%**。
4. 对 `tk.Widget.geometry` 做**一次性 monkeypatch**——本进程内任何 `.geometry("WxH...")` 的尺寸自动按系数缩放，**屏幕坐标 +X+Y 保持不动**。因此只要独立入口在创建 `Tk()` 时调一次 `compute_scale()`，它里面**所有窗口（含 dialog / Toplevel）**都会自动套用，无需逐个改写几何字符串。

> 📌 用法顺序不能错：`enable_process_dpi_awareness()` → `tk.Tk()` → `compute_scale(root)` → 布局。

---

## 6. 输入注入：怎么"按"下去

本项目有三级，按可用性自动降级：

| 级别 | 机制 | 依赖 |
|---|---|---|
| 1 | 控件级 Pattern（`InvokePattern` / `TogglePattern` / `ValuePattern`）——**最稳** | UIA Provider 实现完整 |
| 2 | 坐标级鼠标事件（`pyautogui` / `pywinauto.click_input()`） | `pyautogui` |
| 3 | **半自动采集**的钩子节点：`pynput` 优先，缺失时降级到 `win32_input_hook.py`（纯 `ctypes` 的低级钩子，监听全局左键按下与 F8 热键） | `pynput`（可选）/ 零依赖兜底 |

**为什么要有第 3 级**：半自动采集需要"用户在真实界面上点一下，工具记坐标"。这与"执行时注入输入"是两条不同的技术路径，前者必须拿到真实全局鼠标事件。

> ⚠️ **可靠性提醒**：UIA 的 `Invoke()` 有时不触发某些自定义控件的视觉反馈。本项目 `check_all_toggles` 的做法值得借鉴——**先物理点击，未翻转再用程序化 `Toggle()` 收敛**（双保险）。

---

## 7. 会话隔离与 UIPI（内网部署的头号杀手）

| 概念 | 说明 | 后果 |
|---|---|---|
| **会话（Session）** | Windows 登录会话 ID。不同 Session 的进程互相 `EnumWindows` **看不到对方窗口** | worker 与 MUP 必须**同 SessionId**，否则"看得见进程、看不见窗口"。`diagnose_session.py` 专门诊断这个 |
| **UIPI** | User Interface Privilege Isolation。低完整性级别进程**不能**向高完整性级别进程发输入 / 查询其 UI | 以管理员运行的 MUP 需要自动化进程也提权；`.bat` 入口普遍做管理员提升，原因在此 |
| **RDP / 锁屏** | 断开远程桌面后 UI 树与渲染可能失效 | 无人值守机需保持会话存活（运维侧保证，卷 F2） |

本项目的 UIPI 探测有 3 秒 TTL 缓存（`FLOW_UIPI_BLOCK_CACHE_TTL_SECONDS = 3.0`），避免每次都查。

---

## 8. 原生崩溃：为什么 Python 抓不住

这是本项目最难排查的一类问题，必须在设计层面理解。

**根因**：pywinauto/comtypes 持有 UIA 元素（COM 对象）。当目标程序的 UIA provider 已失效，而 Python **垃圾回收**触发 `__del__ → Release()` 释放那个失效指针时，会发生段错误 / 堆损坏（`0xc0000374` / `0xC0000005`）。

```
Garbage-collecting
  → comtypes/_post_coinit/unknwn.py: Release
  → __del__
  → pywinauto/uia_element_info.py: elements_from_uia_array
```

**关键性质**：Python 的 `try / except / finally` **完全无法捕获**，进程直接死亡，**没有任何 traceback**。

**本项目的三层防御**：

| 措施 | 位置 | 说明 |
|---|---|---|
| **UIA 遍历期间禁用 GC** | `WT_AUT_recorded.run_automation` 入口 `gc.disable()`；`wt_flow_locator.find_flow_control` 包装函数再加一层 | 覆盖定位/点击/键入/下拉**所有** UIA 操作，而不是逐个打补丁 |
| **隔离进程探针** | `control_locator_probe.py` | 校验定位放到独立子进程跑，崩了不拖垮主进程 |
| **faulthandler** | `WT_AUT_recorded.faulthandler.enable(file=sys.stderr, all_threads=True)` | 原生崩溃时 dump 调用栈 |

**两个容易忽略的推论**：

1. **finally 里不要 `gc.enable()`**。异常 unwind 后重新启用 GC，会在下一次 GC 时收集持有失效指针的对象 → 崩溃。本项目专用自动化子进程的做法是：**保持 GC 禁用直到 `os._exit()`**（H-3）。
2. **Tk 不能跨线程操作**。后台线程调 `root.update()` 会递归进入 Tcl 事件循环，两个线程同时用同一个 Tcl 解释器 → 原生崩溃。跨线程 COM 对象也有同样的**公寓（Apartment）**限制：在后台线程 `CoCreateInstance` 拿到 STA 对象指针、再回主线程 ctypes 直调，同样崩。统一范式是 `_ui_safe_call(cb)` → `root.after(0, cb)`（H-2）。

---

## 9. 这些约束如何决定了上层架构

| 底层约束 | 上层的应对 | 章节 |
|---|---|---|
| 控件属性会漂移 | 多策略组合定位 + 加权评分（而不是单一 selector） | B3 |
| Control View 有盲区 | Raw View 分支（`expects_raw_view`） | B3 §6 |
| 全树遍历很贵（20–36 s） | 分级候选 + 预算控制 + 多层缓存 | B3 §7 |
| 一切都可能在运行时失效 | 四级降级 + 反馈闭环 + 自愈 | B5 |
| 树走不通时还得干活 | 图像模板 / 亲戚区域 / OCR / AI 视觉兜底 | B4 |
| 会话与权限不可控 | 运维侧保证 + 一键会话修复脚本 | F2 |

---

核验：2026-09-11 / 涉及：`wt_flow_locator.py`（L25–33, L1012, L1518, L1832, L5086–5548, L3933–3983, L4905–4925, L4993–5010, L5624–5763, L7390–7431）、`wt_dpi.py`(L1–100)、`win32_input_hook.py`、`control_locator_probe.py`、`WT_AUT_recorded.py`、`docs/05-调试案例/典型问题记录/WT自动化_调试根因与工程改进知识库.md`（模式 C/D/H/H-2/H-3/K/O/P）
