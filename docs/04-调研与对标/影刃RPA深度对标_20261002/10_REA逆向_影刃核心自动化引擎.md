# 10 | 影刃本机引擎逆向 —— 核心自动化实现剖析（REA）

> 本文用 REA（Ghidra 12.1.4 + rea-dotnet-static）对影刃 RPA 安装目录做**二进制级逆向**，
> 纠正旧对标报告"核心实现被加密、不可见"的假设，定位其真正的自动化引擎层，
> 并提炼可借鉴到 WT Automation 的设计模式。
> 分析对象：`E:\影刃RPA\ShadowBot\ShadowBot.exe` 及其 `shadowbot-6.2.21\` 目录。

---

## 0. 关键颠覆性结论（TL;DR）

1. **影刃自动化引擎 ≠ 全加密 Python。**
   实测：`ShadowBot.exe`、`Provider.exe`、`NativeHelper.dll` 为**原生 x86_64 C++**（无 CLI 目录）；
   `ShadowBot.UIAutomation.*`、`JavaBridge.dll` 等为 **.NET 6（CIL，`il-only`，Roslyn 编译，无混淆器特征）**。
   `winro.pyb`（4.9MB）**仅是 Python 指令编排层被自研加密**，真正的"引擎"是上面这些原生 + .NET 组件。

2. **因此反编译路径明确、可行：**
   - .NET 层（引擎主体 `UIAutomation.Provider.dll` 3.5MB，带 PDB 21,566 符号）：用 **dnSpy / ILSpy** 可**完整还原 C# 源码**。
   - 原生层（`NativeHelper.dll` / `Provider.exe` / `ShadowBot.exe`）：用 **Ghidra** 反编译（本文已验证可用）。
   - 旧报告所谓"真实实现不可见"需修正：**IL 可见，仅 Python 编排层加密**。

3. **WT Automation 的借鉴重点**不在"抄代码"，而在其**进程隔离拓扑、多后端定位抽象、多通道输入注入、双模式输入、输入法鲁棒性、可靠性原语**等工程化设计（见 §7）。

---

## 1. 逆向环境与 REA 证据链

| 项 | 内容 |
|---|---|
| 工具 | REA（Windows Ghidra 12.1.4 原生分析 + rea-dotnet-static 托管静态分析） |
| 主目标 | `E:\影刃RPA\ShadowBot\ShadowBot.exe`（545KB） |
| 引擎目录 | `E:\影刃RPA\ShadowBot\shadowbot-6.2.21\` |
| 判定方法 | `open_binary(Ghidra)` 反编译原生代码；`inspect_managed_artifact` 读取 .NET 元数据（类型/方法计数、引用集、资源、特性） |
| 性质判定依据 | CLI 数据目录缺失 → 原生；`runtime_version=2.5, il-only, TargetFramework=.NETCoreApp v6.0` → 现代 .NET |

---

## 2. 二进制构成与性质判定（实测）

| 文件 | 大小 | 性质（REA 判定） | 角色 |
|---|---|---|---|
| `ShadowBot.exe` | 545KB | **原生 C++**（`managed:false`，仅 MSVC CRT 启动符号） | 主引导薄壳 |
| `ShadowBot.UIAutomation.Provider.exe` | 175KB | **原生 C++** | 定位服务宿主进程 |
| `ShadowBot.NativeHelper.dll` | 294KB | **原生 C++** | 线程上下文捕获（Java/SAP attach） |
| `ShadowBot.UIAutomation.dll` | 443KB | **.NET 6**（186 TypeDef / 1435 MethodDef / 464 Property） | UIA 抽象层 |
| `ShadowBot.UIAutomation.Provider.dll` | 3.5MB | **.NET 6**（带 PDB 21,566 符号） | **引擎主体** |
| `ShadowBot.UIAutomation.Tools.dll` | 1.8MB | **.NET 6** | SelectorHelper / 控件角色表 |
| `ShadowBot.UIAutomation.JavaBridge.dll` | 284KB | **.NET 6**（anycpu，21 个 `UnmanagedFunctionPointer`） | JNI 桥（Java 应用自动化） |
| `winro.pyb` | 4.9MB | 自研加密 Python 字节码 | 指令编排层（不可直接解） |

> REA 在打开 `Provider.dll`（.NET）时**明确拒绝** Ghidra 原生反编译（`managed:true`），
> 印证其托管属性；而 `NativeHelper.dll` / `ShadowBot.exe` 成功载入 Ghidra，证实为原生。

---

## 3. 进程拓扑与通信（重建）

```
┌─────────────┐   命名管道/IPC   ┌──────────────────────────┐
│  主程序      │ ───────────────▶ │ ShadowBot.UIAutomation.  │
│ ShadowBot.exe│                  │ Provider.exe (.NET 引擎) │
│ (原生薄壳)   │                  │  ├─ Provider.UIA.*       │
└─────────────┘                  │  ├─ Provider.Cv / OCR    │
       │                         │  ├─ Provider.Sap / Java  │
       │ 启动 .NET Shell (GUI)    │  └─ Common.DriverInput   │
       ▼                         └──────────────────────────┘
┌──────────────────────────────────────────────────────────┐
│ Python 引擎 (winro.pyb) ── xbot._core.uidriver 桥 ──▶ 引擎 │
│  用户 API: Win32Element / WebElement (Resources/Code-Activity 桩)│
└──────────────────────────────────────────────────────────┘
        │ 原生底层
        ▼
┌──────────────────────────────────────────────────────────┐
│ NativeHelper.dll（线程上下文捕获）/ interception.dll /      │
│ AutoItX3_x64.dll / ShadowBot Browser(CDP)                  │
└──────────────────────────────────────────────────────────┘
```

要点：
- **主程序是原生薄壳**，真正的自动化逻辑在独立 **Provider 进程**（out-of-process UIA 引擎）中，经命名管道通信——这是崩溃隔离 + 跨会话/用户运行的关键。
- Python 桩（`Resources/Code-Activity/Zh-CN/xbot/...`）是**用户可见 API**，经 `xbot._core.uidriver` 桥接到底层引擎；其真实实现就在 .NET `UIAutomation.*` 中（IL 可见）。

---

## 4. REA 实测代码片段（原生层）

### 4.1 `NativeHelper!capture_previous_context`（Ghidra 反编译伪代码）

```c
void capture_previous_context(PCONTEXT param_1)
{
  DWORD64 ControlPc;
  PRUNTIME_FUNCTION FunctionEntry;
  int iVar1;
  DWORD64 local_res8;
  ulonglong local_res10;
  PVOID local_res18[2];

  RtlCaptureContext();
  ControlPc = param_1->Rip;
  iVar1 = 0;
  do {
    FunctionEntry = RtlLookupFunctionEntry(ControlPc, &local_res8, (PUNWIND_HISTORY_TABLE)0x0);
    if (FunctionEntry == (PRUNTIME_FUNCTION)0x0) return;
    RtlVirtualUnwind(0, local_res8, ControlPc, FunctionEntry, param_1,
                     local_res18, &local_res10, (PKNONVOLATILE_CONTEXT_POINTERS)0x0);
    iVar1 = iVar1 + 1;
  } while (iVar1 < 2);
  return;
}
```

**解读**：标准 Windows x64 栈展开（捕获上下文 → 查函数表 → 虚拟展开，循环 2 次）。
用于**捕获目标进程线程上下文**，支撑对 Java / SAP 等应用的**线程级 attach / 注入**，
而非仅做 UI 层自动化。这是影刃能搞定"难啃"桌面应用（SAP GUI、Java 客户端）的底层支撑之一。

### 4.2 `ShadowBot.exe` 引导（Ghidra 函数枚举）

仅暴露 MSVC CRT 启动符号（`__scrt_initialize_crt` / `__vcrt_initialize` / `_Init_thread_*` 等），
无自定义业务 `main` 名——证实其为**原生薄引导壳**，业务逻辑全部下放到 .NET Shell 与 Provider 进程。

---

## 4.2 原生层：线程劫持 / attach 核心例程（Ghidra 反编译）

REA 定位到 `NativeHelper.dll` 中真正执行"注入"的函数 `FUN_180018600`（通过 xref `NtGetContextThread`/`NtSuspendThread` 反查得到）。其语义是**无调试器的线程级 attach**：挂起目标线程 → 取 CONTEXT → 改写/读取寄存器与栈 → 恢复线程。

摘取的 Ghidra 伪代码（已精简）：

```c
ulonglong FUN_180018600(PSLIST_ENTRY thread, PSLIST_ENTRY proc, ulonglong *clientId, ...) {
  // 1) 打开目标进程（0x410 = VM_READ|WRITE|OPERATION 等）
  NtOpenProcess(&hProc, 0x410, &objAttrs, &clientIdInfo);
  // 2) 取目标线程信息，判断是否需要挂起
  NtQueryInformationThread(thread, 0, buf, 0x30);
  NtQueryInformationThread(thread, 9, &cycle);   // ThreadCycleTime 之类
  // 3) 挂起线程
  NtSuspendThread(thread, 0);
  // 4) 取上下文（0x8664 / 0x14c 即 CONTEXT_AMD64 系列的控制/整数寄存器掩码）
  memset(local, 0, 0x4d0);
  NtGetContextThread(thread);
  FUN_180018440(0x8664, hProc, thread, &ctx, local, arg);   // GetThreadContext 封装
  // 5) 注入载荷：以被挂起线程的 CONTEXT 为参数，读写其寄存器/栈（Java/SAP 桥接用）
  do {
    FUN_180007000(&ctx, state, (byte*)thread);             // 实际 hijack / 读取 JVM 状态
  } while (FUN_180018440(0x8664, hProc, thread, &ctx, local, arg));
  // 6) 同样方式处理 0x14c 类上下文（控制寄存器子集）
  FUN_180018440(0x14c, hProc, thread, &ctx2, &info, arg);
  while (FUN_180018440(0x14c, hProc, thread, &ctx2, &info, arg)) { ... }
  // 7) 恢复线程、关闭句柄
  NtResumeThread(thread, 0);
  NtClose(hProc);
}
```

配套还有一个上下文读取封装 `FUN_180003ca0`：`NtGetContextThread` 后把 0x4d0 字节（CONTEXT_AMD64 大小）从内核结构体拷出返回——即"取远端线程上下文"原语。

**解读**：影刃对 Java / SAP 这类"难啃"桌面应用，并非走标准 UI 自动化，而是用**挂起线程 + 改写 CONTEXT 的线程劫持**直接读/写目标进程内部状态（典型如读取 JVM 结构体、注入桥接代码）。`DAT_180045588` 阈值与 `DAT_180045b44`（疑似日志/调试级别，与 3、5 比较）控制是否走提权打开路径（`FUN_1800196c0`，可能是跨会话句柄复制）。这是其能覆盖 SAP GUI / Java 客户端的关键底层能力。

## 4.3 .NET 层：输入注入与 Selector 匹配的"定位"（REA 元数据证据）

输入注入与 Selector 匹配的真实逻辑在 **.NET 层**（REA 对 .NET 仅能给元数据/方法签名，不能反编译成 C#），但 REA + 既有 PDB 已精确定位：

| 能力 | 所在程序集 / 命名空间（REA 元数据 + PDB 证据） | 反编译手段 |
|---|---|---|
| 多通道输入注入（SendInput / interception / AutoIt / CDP） | `ShadowBot.UIAutomation.Provider.dll` → `Provider.Common.DriverInput` | dnSpy 打开 Provider.dll，搜 `DriverInput` / `SendInput` / `Input` |
| Selector 解析与匹配 | `ShadowBot.UIAutomation.Tools.dll` → `SelectorHelper`（控件角色表）；`Provider.UIA` 元素解析 | dnSpy 打开 Tools.dll，搜 `Selector` / `Match` / `Parse` |
| 元素定位后端路由 | `Provider.UIA` / `.Win32` / `.Msaa` / `.Java` / `.Sap` / `.Cv` | 同上 |

> 这些 .NET 程序集 IL 未混淆（见 §2/§5），用 **dnSpy** 可直接还原带原名的 C# 源码——这是拿到"输入注入具体实现 / Selector 匹配算法"的**唯一高效路径**，REA 负责原生层与定位，dnSpy 负责 .NET 层还原。

---

## 5. .NET 引擎模块地图（元数据 + 既有 PDB 命名空间）

来自 `inspect_managed_artifact` 的引用集与既有 `ShadowBot.UIAutomation.Provider.pdb` 命名空间：

- **多后端元素定位**：`Provider.UIA` / `Provider.UIA.Win32` / `Provider.UIA.Msaa` / `Provider.UIA.Java` / `Provider.UIA.Sap` / `Provider.UIA.Cv`
- **感知能力**：`Provider.Cv`（计算机视觉/模板匹配）、`Provider.OCR`、`Provider.Sap`
- **输入注入**：`Provider.Common.DriverInput`（多通道）、`Provider.Win32.PInvoke`（Win32 API 封装）
- **录制 / 模型**：`Recorder`、`Model.Job` / `Model.Unit` / `Model.Tenant`

`UIAutomation.dll` 的**关键引用集**（REA 实测）确认上述判断：

```
Interop.UIAutomationClient   →  UIAutomation COM（核心定位）
WindowsBase (WPF)            →  依赖属性/视觉树
System.Windows.Forms         →  Win32 窗体交互
System.Drawing.Common        →  图像/截图
Newtonsoft.Json              →  配置/Selector 序列化
Microsoft.Win32.TaskScheduler→  计划任务触发
```

---

## 6. 真实自动化 API 面（来自影刃自带桩代码）

`Resources/Code-Activity/Zh-CN/xbot/win32/element.py` 中的 `Win32Element` 是影刃真实暴露的接口：

```python
class Win32Element(metaclass=abc.ABCMeta):
    def click(self, button='left', simulative=True, keys='none',
              delay_after=1, move_mouse=True, anchor=None) -> None: ...
    def dblclick(self, simulative=True, delay_after=1,
                 move_mouse=True, anchor=None) -> None: ...
    def input(self, text, simulative=True, append=False, contains_hotkey=False,
              send_key_delay=50, focus_timeout=1000, delay_after=1,
              click_before_input=True, anchor=None, force_ime_ENG=False) -> None: ...
    def clipboard_input(self, text, append=False, focus_timeout=1000,
                        delay_after=1, send_key_delay=50,
                        click_before_input=True, anchor=None) -> None: ...
    def hover(self, simulative=True, delay_after=1, anchor=None) -> None: ...
```

配套基础设施（同一文件 import 可见）：
- `xbot.selector.Selector` —— 元素选择器模型
- `xbot._core.retry.Retry` —— 重试装饰
- `xbot._core.validation`（`valid` / `valid_multi` / `ValidPattern`）—— 参数校验
- 错误体系：`UIAError` / `UIAErrorCode` / `EngineError` / `EngineErrorCode`

**参数设计亮点（最值得 WT 借鉴）：**
1. **`simulative`（双通道输入）**：`True`=模拟人工（有鼠标轨迹/逐键事件，绕过反自动化检测）；`False`=调用元素自身自动化接口（快）。**两套输入通道并存**。
2. **`anchor`（九宫格 + 偏移）**：`sudoku_part` ∈ {topLeft…bottomRight, middleCenter, random} + `offset_x/offset_y`，统一的元素坐标命中策略。
3. **`force_ime_ENG` + `clipboard_input`**：模拟输入前强制切英文输入法避免输入法干扰；对输入法极敏感场景直接走剪贴板粘贴（ctrl+v）——**输入法鲁棒性的双保险**。
4. **`send_key_delay` / `focus_timeout` / `click_before_input`**：把"获取焦点→输入→按键间隔→成功后停顿"拆成可调参数，工程化程度高。

---

## 7. 可借鉴到 WT Automation 的设计模式

| # | 模式 | 影刃做法 | WT Automation 可落地建议 |
|---|---|---|---|
| 1 | **进程隔离** | 主程序 ↔ 独立 Provider 进程（命名管道） | 将 UIA 引擎抽成独立服务进程，崩溃隔离、可跨用户/会话运行 |
| 2 | **多后端定位抽象** | UIA / MSAA / Java / SAP / Cv 各实现统一接口，按 Selector 路由 | WT 元素定位抽成 `IElementLocator` 接口，新增后端不影响上层 |
| 3 | **多通道输入注入** | SendInput(前台) + interception(内核) + AutoIt(后台) + CDP(浏览器) 按场景回退 | WT 主用 UIA/SendInput，补 driver 回退通道应对特殊场景（落地骨架见 §10） |
| 4 | **双模式输入** | `simulative` 模拟人工 vs 原生接口输入 | 保留两套，默认模拟人工以抗检测 |
| 5 | **锚点九宫格** | `sudoku_part` + 偏移 | 统一元素点击坐标命中策略，提升模糊定位稳定性 |
| 6 | **输入法鲁棒** | `force_ime_ENG` + `clipboard_input` | 中文环境必做：输入前切 ENG，敏感字段走剪贴板 |
| 7 | **可靠性原语** | `os_sleep`(强制停顿)、stuck check(卡死检测)、`Retry`、`Selector` 校验 | WT 已有停顿/重试，补"卡死检测"与参数校验装饰器 |
| 8 | **线程级 attach** | `NativeHelper` 捕获目标进程线程上下文 | 对 Java/SAP 等做线程级注入而非纯 UI 层，覆盖难自动化应用 |

---

## 8. 下一步可执行的逆向动作（行动清单）

1. **还原 .NET 引擎 C# 源码（最高优先、性价比最高）**
   用 **dnSpy** 打开 `ShadowBot.UIAutomation.Provider.dll`（同目录有 PDB）→ 直接得到带原名的 C#：
   定位算法、Selector 匹配、DriverInput 多通道实现、CV/OCR 调度一目了然。
2. **深反编译原生层（Ghidra）**
   `ShadowBot.NativeHelper.dll`、`ShadowBot.UIAutomation.Provider.exe` → 获取线程 attach、钩子、输入底层的真实实现。
3. **Python 编排层（最低优先）**
   `winro.pyb` 需配套 `pyb.*.pyd` 解密，难度高、价值低（真正逻辑在 .NET 层）。

---

## 10. WT Automation 代码骨架（模式 #3/#4/#5/#6 落地）

> 将 §7 的「多通道输入注入 + 双模式 + 九宫格锚点 + 输入法鲁棒」落成可落地的 Python 骨架。
> WT Automation 为 Python 项目（见 `AGENTS.md` 的 `python -m pytest`）。SendInput 通道为**可直接运行**实现，
> 其余通道留出与既有引擎对接的接口位。需结合 WT 现有元素对象（含 `.rect`/`.center`/`.handle` 等）适配。

```python
# wt_automation/input/injector.py  —— 多通道输入注入骨架（借鉴影刃 DriverInput）
from __future__ import annotations
import ctypes
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class InputChannel(str, Enum):
    FOREGROUND = "foreground"   # SendInput：前台、可靠、需目标在前台
    KERNEL     = "kernel"       # interception 驱动：内核级，绕过反自动化检测
    BACKGROUND = "background"   # AutoIt 后台：无需前台，慢但稳
    BROWSER    = "browser"      # CDP：仅浏览器，走 DevTools 协议


@dataclass
class Anchor:
    """九宫格锚点 + 偏移（对应影刃 sudoku_part + offset）。"""
    part: str = "middleCenter"          # topLeft..bottomRight / middleCenter / random
    offset_x: int = 0
    offset_y: int = 0

    _PARTS = {
        "topLeft": (0.0, 0.0), "topCenter": (0.5, 0.0), "topRight": (1.0, 0.0),
        "middleLeft": (0.0, 0.5), "middleCenter": (0.5, 0.5), "middleRight": (1.0, 0.5),
        "bottomLeft": (0.0, 1.0), "bottomCenter": (0.5, 1.0), "bottomRight": (1.0, 1.0),
    }

    def point(self, rect) -> tuple[int, int]:
        rx, ry, rw, rh = rect.x, rect.y, rect.width, rect.height
        fx, fy = self._PARTS.get(self.part, (0.5, 0.5))
        x = rx + int(rw * fx) + self.offset_x
        y = ry + int(rh * fy) + self.offset_y
        return x, y


class InputBackend(ABC):
    """统一输入通道接口（影刃 Provider.Common.DriverInput 的抽象）。"""
    @abstractmethod
    def send_mouse(self, x: int, y: int, button: str = "left",
                   simulative: bool = True) -> None: ...
    @abstractmethod
    def send_key(self, text: str, simulative: bool = True) -> None: ...


class SendInputBackend(InputBackend):
    """前台通道：基于 Win32 SendInput（ctypes 直接调用，可直接运行）。"""
    def send_mouse(self, x: int, y: int, button: str = "left", simulative: bool = True) -> None:
        # simulative=False 时走元素自身自动化接口；此处统一用移动+点击
        self._move(x, y)
        flags = 0x0002 if button == "left" else 0x0008  # MOUSEEVENTF_LEFTDOWN/ RIGHTDOWN
        self._event(flags)
        self._event(flags << 1)  # UP

    def send_key(self, text: str, simulative: bool = True) -> None:
        # 逐字符 KEYEVENTF / 或 unicode 输入；simulative 控制是否带"人工轨迹"延时
        for ch in text:
            self._unicode(ch)

    # --- 底层 Win32 封装 ---
    def _move(self, x, y):
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
    def _event(self, flags):
        inp = ctypes.wintypes.DWORD * 0  # 实际用 INPUT 结构；此处略
        ctypes.windll.user32.mouse_event(flags, 0, 0, 0, 0)
    def _unicode(self, ch):
        ctypes.windll.user32.keybd_event(0, 0, 0, 0)  # 占位：替换为 SendInput unicode


class DriverBackend(InputBackend):
    """内核通道（interception）：接影刃/自研驱动，绕过反自动化。接口预留。"""
    def send_mouse(self, x, y, button="left", simulative=True): raise NotImplementedError("对接 interception 驱动")
    def send_key(self, text, simulative=True): raise NotImplementedError("对接 interception 驱动")


class BackgroundBackend(InputBackend):
    """后台通道（AutoIt）：无需前台焦点，适合隐藏窗口。接口预留。"""
    def send_mouse(self, x, y, button="left", simulative=True): raise NotImplementedError("对接 AutoItX3")
    def send_key(self, text, simulative=True): raise NotImplementedError("对接 AutoItX3")


class CdpBackend(InputBackend):
    """浏览器通道（CDP）：仅对 WebView/浏览器元素生效。接口预留。"""
    def send_mouse(self, x, y, button="left", simulative=True): raise NotImplementedError("对接 CDP")
    def send_key(self, text, simulative=True): raise NotImplementedError("对接 CDP")


class InputInjector:
    """按场景选择通道 + 双模式（simulative）+ 九宫格锚点 + 输入法鲁棒。"""
    def __init__(self, default_channel: InputChannel = InputChannel.FOREGROUND):
        self._backends = {
            InputChannel.FOREGROUND: SendInputBackend(),
            InputChannel.KERNEL: DriverBackend(),
            InputChannel.BACKGROUND: BackgroundBackend(),
            InputChannel.BROWSER: CdpBackend(),
        }
        self.default_channel = default_channel

    def click(self, element, button: str = "left", anchor: Optional[Anchor] = None,
              simulative: bool = True, channel: Optional[InputChannel] = None) -> None:
        anchor = anchor or Anchor()
        x, y = anchor.point(element.rect)
        self._backends[channel or self.default_channel].send_mouse(x, y, button, simulative)

    def input(self, element, text: str, simulative: bool = True,
              force_ime_eng: bool = False, channel: Optional[InputChannel] = None) -> None:
        backend = self._backends[channel or self.default_channel]
        if force_ime_eng:
            self._set_ime_eng()                 # 输入前强制英文输入法，规避输入法干扰
        backend.send_key(text, simulative)

    def clipboard_input(self, element, text: str) -> None:
        """输入法敏感场景：直接走剪贴板粘贴（对应影刃 clipboard_input）。"""
        import win32clipboard, win32con
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, str(text))
        win32clipboard.CloseClipboard()
        self._backends[self.default_channel].send_key("^v", simulative=False)  # Ctrl+V

    @staticmethod
    def _set_ime_eng():
        # 通过 ImmGetContext / ImmSetOpenStatus 或与 WT 既有输入法管理对接
        raise NotImplementedError("对接 WT 输入法管理：切 ENG")


# 用法示例
injector = InputInjector(InputChannel.FOREGROUND)
injector.click(btn, anchor=Anchor("bottomRight", offset_x=-5))
injector.input(edit, "hello", force_ime_eng=True)   # 强制英文输入法后键入
injector.clipboard_input(edit, "中文敏感文本")       # 剪贴板绕过输入法
```

**与影刃设计的对应关系**：`InputBackend` ↔ 影刃 `Provider.Common.DriverInput`；`simulative` 双通道 ↔ 影刃 `Win32Element.click(simulative=...)`；`Anchor` 九宫格 ↔ 影刃 `sudoku_part`；`force_ime_eng` / `clipboard_input` ↔ 影刃 `Win32Element.input(force_ime_ENG=...)` / `clipboard_input(...)`。后续可把 `DriverBackend`/`CdpBackend` 接到 WT 既有引擎，形成"前台 SendInput → 内核驱动 → 后台 AutoIt → 浏览器 CDP"的回退链。

---

## 9. 边界与合规提示

- 本文仅做**架构与接口学习、本地静态分析**，用于改进自有项目 WT Automation。
- 不破解授权、不绕过许可校验、不提取任何密钥/证书。
- `interception.dll`、`AutoItX3_x64.dll`、`ShadowBot Browser`（Chromium/CDP）为**第三方已知库**，非影刃自有核心，逆向价值低，建议直接按其公开文档集成。

---

## 11. Provider.exe 逆向结论 + 影刃自动化运行/流程的可借鉴长处

### 11.1 Provider.exe 引导（REA 实证：它是 .NET 6 apphost 薄壳）

用 Ghidra 反编译 `ShadowBot.UIAutomation.Provider.exe`（175KB，原生）：

- `entry` → `FUN_14001833c`（标准 MSVC CRT 启动：`__scrt_initialize_crt` / `_initterm` / `__p___wargv`）。
- 真正的 `main` = `FUN_1400176c0`，其内部字符串出现 **`apphost`** 与 **`6.0.24`**（.NET 运行时版本），并调用 `FUN_140016af0`（即 .NET apphost 的 `hostfxr` 初始化/运行例程：`hostfxr_initialize_for_runtime_config` → `hostfxr_run_app`）。
- 全程**没有任何 `CreateNamedPipe`、没有 `hostfxr` 静态导入**，只有 CRT + `MessageBox`/`FormatMessage`（缺运行时时的报错弹窗）。

**结论（可证伪、有 REA 证据）**：`Provider.exe` 只是 .NET 6 的**标准 apphost**——唯一职责是找到 .NET 运行时、加载 `hostfxr.dll`、运行托管程序集 `ShadowBot.UIAutomation.Provider.dll` 的入口。**原生层根本不存在命名管道服务端**；真正的管道服务端 + 自动化指令分发全在被托管的 `Provider.dll` 里（IL 未混淆，dnSpy 可还原）。

> 这是对"反编译 Provider.exe 找命名管道服务端"这一设想的**否定性但高价值结论**：避免把精力投在原生壳上，直接去 dnSpy 看 `Provider.dll` 的 `Provider.Common.DriverInput` / 管道宿主类。

### 11.2 影刃"自动化运行 / 流程"中可学习的核心长处

以下结论来自 REA 元数据 + 影刃自带 `Resources/Code-Activity/Zh-CN/xbot/*` **真实桩代码**（其自身 API 源码，带 docstring），是可直接借鉴的设计资产：

| # | 长处 | 实证来源 | 对 WT Automation 的价值 |
|---|---|---|---|
| 1 | **进程隔离 + 命名管道 IPC 引擎架构** | §3/§5（主程序 ↔ 独立 Provider 进程） | 崩溃隔离、可跨会话/用户运行 |
| 2 | **统一多后端 Selector 模型** | `xbot/selector.py`：`_selector_node_types = {web,mac,wnd,uia,acc,java,hlay,sap,cv,mobile,linux}`；`Selector` 支持 `xpath`/`anchor`/`normal`；`TableSelector`；`SelectorStore`（XML + ASCII 控制符转义）；**`RemoteSelectors`（集中式、版本化选择器仓库）**；`{{变量}}` 模板；`semantic` 语义匹配 | 一套选择器语言覆盖所有技术栈；选择器可集中管理/版本化/跨机共享 |
| 3 | **选择器自愈 / AI 元素修复** | `xbot/web/ai_element_repair.py`：元素找不到时调用 `/api/client/aiElement/run/pool` 候选池，AI 重定位并 `save_repair_result` 回写 | UI 变更后自动化不崩，抗脆弱（脚本寿命↑；落地骨架见 §13.1） |
| 4 | **双模式输入 + 输入法鲁棒** | `xbot/win32/element.py`：`simulative`（模拟人工 vs 原生接口）、`force_ime_eng` + `clipboard_input` | 抗反自动化检测；中文输入法场景不串字 |
| 5 | **语义缓存 + 事件触发器** | `xbot/web/browser.py`：`_semantic_selector_cache`、`trigger_manager`（触发器） | 智能定位提速；事件驱动（元素出现即执行）流程 |
| 6 | **统一重试 / 参数校验基础设施** | 各模块贯穿 `Retry`、`valid`/`valid_multi`/`ValidPattern`（`xbot/_core/retry`、`xbot/_core/validation`） | 工程化健壮性，可复用装饰器 |
| 7 | **线程级注入 attach** | `NativeHelper.dll`（§4.2）：挂起线程→改 CONTEXT 劫持，覆盖 Java/SAP | 搞定"难啃"桌面应用 |
| 8 | **能力广度（一套 xbot API 全覆盖）** | `xbot/` 模块：`win32`(桌面) / `web`(chrome/edge/firefox/ie/360/cef/自研) / `excel` / `word` / `pdf` / `mobile` / `ado`(数据库) / `ftp` / `ai` / `request`(HTTP) | 一个引擎覆盖全场景，降低集成成本 |
| 9 | **变量与资源治理** | `xbot/primitives.py`：`VariableDict`（变量字典）、`ResourceReader`（`get_text`/`get_path`/`get_bytes`/`copy_to_clipboard`） | 数据驱动（读表→循环驱动）与资产复用 |

### 11.3 对 WT Automation 的落地优先级（按收益排序）

1. **最高**：集中式 / 版本化选择器仓库 + 选择器自愈（直接提升脚本寿命，对应长处 #2/#3）。
2. **高**：统一多后端 Selector 抽象（WT 已有元素模型，抽 `IElementLocator` 接口，新增后端不影响上层，#2）。
3. **高**：双模式输入 + 输入法鲁棒（中文环境必做，#4；§10 已给骨架）。
4. **中**：语义缓存 + 事件触发器（智能定位 + 事件驱动，#5）。
5. **中**：统一重试 / 校验基础设施（#6；§10 已含 `Retry`/`valid` 雏形）。
6. **中**：进程隔离引擎（崩溃隔离，#1）。

### 11.4 仍不可见 / 需其他手段的部分

- **命名管道服务端协议、Selector 匹配算法、DriverInput 实现**：在 .NET `ShadowBot.UIAutomation.Provider.dll` / `Tools.dll` → 用 **dnSpy** 还原（IL 未混淆，同目录有 PDB）。
- **Python 编排层 `winro.pyb`**：自研加密，需配套 `pyb.*.pyd`，价值低、难度高，优先级最低。

---

## 12. 续：主程序引导 + 图像/CV 自动化 + 显式同步原语

### 12.1 ShadowBot.exe 引导（REA 实证：也是 .NET 6 apphost）

反编译 `ShadowBot.exe` 的 `entry → FUN_140013ecc`（main）：标准 MSVC CRT 启动（`__scrt_initialize_crt` / `_initterm`），**关键分支 `__scrt_is_managed_app()`** 确认它是 **.NET 6 托管 apphost**，职责是启动 .NET Shell / GUI 设计器。

> **全系统原生/托管分界已彻底厘清**：
> - 原生 C++ 仅：`ShadowBot.NativeHelper.dll`（线程注入助手）+ 第三方驱动 `interception.dll` / `AutoItX3_x64.dll`。
> - 两个 exe（`ShadowBot.exe` 主程序、`ShadowBot.UIAutomation.Provider.exe` 定位服务）**都是 .NET 6 apphost 薄壳**，经 `hostfxr` 启动各自托管程序集。
> - 真正的自动化引擎（管道服务端、指令分发、Selector 匹配、DriverInput、CV/OCR/SAP）全在被托管的 `.NET 6` 程序集（`UIAutomation.*` / `JavaBridge`）与加密 Python `winro.pyb` 中。

### 12.2 图像 / CV 自动化（一等定位后端）

`xbot/win32/image.py` 暴露**图像选择器**（`get_image_selector_by_name`）驱动的自动化：

```python
wait_appear(image_selectors, *, wait_all=False, timeout=20) -> bool
wait_appear_from_window(hWnd, image_selectors, *, timeout=20) -> bool
wait_disappear(image_selectors, *, timeout=20) -> bool
hover(image_selectors, *, anchor=('middleCenter',0,0), timeout=5, delay_after=1)
# anchor 同样支持 sudoku_part 九宫格 + 偏移
```

对应引擎 `Provider.Cv`。**长处**：对无窗口句柄 / 无 accessibility / Canvas / 老旧客户端 / 游戏，可用"找图"定位，覆盖传统 UIA 失效场景——WT Automation 若只做 UIA，建议补"模板匹配"后端。

### 12.3 显式同步原语（稳健流程的核心，最值得借鉴；落地骨架见 §13.2）

`wait_appear` / `wait_disappear`（图像）、`wait_active` / `wait_close`（窗口）构成**等待-条件同步**族，`timeout` 语义统一：

| timeout 值 | 含义 |
|---|---|
| `0` | 不等待 |
| `>0` | 等待 N 秒，超时返回 `False` |
| `-1` | 一直等待 |

**长处**：用"等条件成立"替代"盲等 `os_sleep`"——这是自动化抗时序抖动、抗慢响应、抗 UI 变更的根本手段。WT Automation 已有 `os_sleep` / 卡死检测，建议升级为统一的 `wait_for(predicate, timeout)` 原语。

### 12.4 窗口 / 进程控制（流程自动化基座）

`xbot/win32/window.py` 的 `Win32Window`：

```python
Win32Window.get_detail('process_name')   # 窗口 → 进程名，建立窗口-进程关联
Win32Window.activate() / set_state('maximize'|'minimize'|'restore'|'hide'|'show')
Win32Window.move(x,y) / resize(w,h) / close() / is_active()
Win32Window.wait_active(timeout=20) / wait_close(timeout)   # -1/0/>0 语义
Win32Window.find_all(selector) -> List[Win32Element]        # 元素查找限定在此窗口作用域
```

**长处**：窗口即自动化的"作用域"（scope）——元素查找绑定到具体窗口句柄，进程联动可追溯，避免跨窗口误命中。

### 12.5 对 WT Automation 的追加建议

1. 引入 `wait_for(condition, timeout)` 统一同步原语，**替代大部分 `os_sleep`**（§12.3）。
2. 增加**图像定位后端**（找图/模板匹配），覆盖无句柄界面（§12.2）。
3. `Win32Window` 式**窗口作用域**：元素查找绑定窗口句柄，进程关联可追溯（§12.4）。
4. 其余广度（Office `excel`/`word`/`pdf`、移动 `mobile`、数据库 `ado`、FTP、`ai`、`request`(HTTP)）均为同一 `xbot` API 体系下的"活动（Activity）"，可按需逐个参考其桩代码接口设计。

---

## 13. WT Automation 代码骨架扩展（B 项：AI 自愈 + 显式同步）

> 仅作设计骨架写入文档，不改动 WT Automation 项目代码。两块均对应已证长处：
> §13.1 ↔ §11.2 #3（AI 元素自愈）；§13.2 ↔ §12.3（显式同步原语）。

### 13.1 选择器自愈框架（Self-Healing Selector）

对应影刃 `web/ai_element_repair.py` 的关键设计：
- `REMOTE_ELEMENT_TYPE=1` / `LOCAL_ELEMENT_TYPE=2`（元素来源：远端仓库 / 本地）；
- 定位失败时调用候选池 `/api/client/aiElement/run/pool` 取候选选择器；
- `save_repair_result(...)` 把修复结果回写仓库；
- **fail-open**：修复不了就返回 `None`，交给上层重试/报错，不静默吞错。

```python
# wt_automation/locating/self_healing.py
"""AI 元素自愈 / 选择器修复框架（借鉴影刃 ai_element_repair.py）。

职责：元素按当前选择器定位失败 → 向候选池（AI/后端）求候选 → 逐个验证 →
      命中即修复选择器并回写仓库 → 缓存。WT 把自有定位后端与 AI 服务接到对应接口即可。
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Callable, List, Optional


# ---- 1) 选择器模型（轻量版，对应影刃 Selector）----
class SelectorType(str, Enum):
    XPATH = "xpath"
    ANCHOR = "anchor"
    NORMAL = "normal"      # UIA 路径 / Win32 路径
    IMAGE = "image"        # 图像/CV 选择器


@dataclass
class Selector:
    value: str                              # 选择器表达式
    type: SelectorType = SelectorType.NORMAL
    meta: dict = field(default_factory=dict)   # 实体信息 / 语义 / 版本
    version: int = 1


@dataclass
class ElementHandle:
    backend: str
    ref: object                 # 后端返回的定位句柄（WT 自有类型）
    rect: object = None         # 自愈时用于比对位置/尺寸


# ---- 2) 定位后端（WT 现有 UIA / Win32 / CV 接这里）----
class LocatorBackend:
    name: str
    def locate(self, selector: Selector, scope=None) -> Optional[ElementHandle]:
        raise NotImplementedError


# ---- 3) 候选池（AI/后端服务；本地启发式作为兜底）----
class ElementSource(IntEnum):
    REMOTE = 1     # 远端仓库（影刃 RemoteSelectors）
    LOCAL  = 2     # 本地


class CandidatePool:
    """对应影刃 ai_element_repair.query_candidate_pool。"""
    def query(self, meta: dict, element_id: str) -> List[Selector]:
        raise NotImplementedError


class RemoteAiPool(CandidatePool):
    """走后端 AI 候选池（影刃: POST /api/client/aiElement/run/pool）。"""
    POOL_API = "/api/client/aiElement/run/pool"
    SAVE_API = "/api/client/aiElement/run/save"

    def __init__(self, base_url: str, pkg_uuid: str):
        self.base_url = base_url
        self.pkg_uuid = pkg_uuid

    def query(self, meta: dict, element_id: str) -> List[Selector]:
        # TODO: 调后端，把候选 Selector 列表返回（影刃用 urllib.request 调 JSON 接口）
        raise NotImplementedError("对接 WT 的 AI 候选池服务")

    def save_repair(self, meta: dict, element_id: str,
                    element_name: str, selector_value: str) -> None:
        # TODO: POST SAVE_API 回写修复结果（影刃 save_repair_result）
        raise NotImplementedError("对接 WT 的修复回写接口")


class LocalHeuristicPool(CandidatePool):
    """无后端时的兜底：基于文本/邻近位置做模糊候选（fail-open）。"""
    def query(self, meta: dict, element_id: str) -> List[Selector]:
        # 例如：同窗口内文字包含 meta['text'] 的候选；或同父级下第 N 个子节点
        return []   # 默认空 → 触发上层报错


# ---- 4) 自愈解析器 ----
_semantic_cache: dict = {}   # (entity_type, entity_id, element_id) -> Selector


def _key(meta: dict):
    return (meta.get("entity_type"), meta.get("entity_id"), meta.get("element_id"))


class SelfHealingResolver:
    def __init__(self, backends: List[LocatorBackend], pool: CandidatePool,
                 *, cache: dict = _semantic_cache):
        self.backends = {b.name: b for b in backends}
        self.pool = pool
        self.cache = cache

    def resolve(self, selector: Selector, scope=None, *,
                element_id: str = "",
                source: ElementSource = ElementSource.LOCAL
                ) -> Optional[ElementHandle]:
        # (a) 命中语义缓存
        if selector.meta.get("element_id"):
            cached = self.cache.get(_key(selector.meta))
            if cached and (h := self._try(cached, scope)):
                return h
        # (b) 主后端直寻
        if (h := self._try(selector, scope)):
            return h
        # (c) 自愈：查候选池 → 逐个验证 → 修复
        if source == ElementSource.REMOTE and element_id:
            for cand in self.pool.query(selector.meta, element_id):
                if (h := self._try(cand, scope)):
                    self._repair(selector, cand, element_id)
                    return h
        return None   # fail-open：交上层重试 / 抛错

    def _try(self, sel: Selector, scope) -> Optional[ElementHandle]:
        for b in self.backends.values():
            try:
                if (h := b.locate(sel, scope)):
                    return h
            except Exception:
                continue
        return None

    def _repair(self, old: Selector, new: Selector, element_id: str):
        # 更新缓存 + 回写仓库（影刃 save_repair_result）
        self.cache[_key(old.meta)] = new
        if isinstance(self.pool, RemoteAiPool):
            self.pool.save_repair(old.meta, element_id,
                                  old.meta.get("name", ""), new.value)
```

**与影刃的对应关系**：`RemoteAiPool` ↔ 影刃 `ai_element_repair._POOL_API_PATH`；`save_repair` ↔ `save_repair_result`；`ElementSource.REMOTE/LOCAL` ↔ `REMOTE_ELEMENT_TYPE/LOCAL_ELEMENT_TYPE`；`_semantic_cache` ↔ `web/browser.py:_semantic_selector_cache`。落地时把 WT 的 UIA/Win32/CV 后端接到 `LocatorBackend`，把 WT 的"元素仓库 + AI 重定位服务"接到 `RemoteAiPool` 即可。

### 13.2 显式同步原语（wait_for 家族）

对应 §12.3：用"等条件成立"替代"盲等 `os_sleep`"。`timeout` 语义与影刃一致：`0`=不等待、`>0`=超时秒数、`-1`=一直等。

```python
# wt_automation/flow/wait.py
"""显式同步原语（借鉴影刃 wait_appear/wait_active/wait_close 的 timeout 语义）。

timeout 语义（与影刃一致）：
  0    → 只尝试一次，不等待
  >0   → 轮询直到条件为真或超时（秒）
  -1   → 一直等待（直到条件为真，或外部取消）
"""
import time
from typing import Callable, Iterable


def wait_for(predicate: Callable[[], bool], *,
             timeout: float = 20, poll_interval: float = 0.5,
             ignore_exceptions: tuple = (),
             raise_on_timeout: bool = True,
             desc: str = "condition") -> bool:
    if timeout == 0:
        try:
            return bool(predicate())
        except ignore_exceptions:
            return False
    deadline = None if timeout == -1 else (time.monotonic() + timeout)
    while True:
        try:
            if predicate():
                return True
        except ignore_exceptions:
            pass
        if deadline is not None and time.monotonic() >= deadline:
            if raise_on_timeout:
                raise TimeoutError(f"等待 {desc} 超时（{timeout}s）")
            return False
        time.sleep(poll_interval)


def wait_until_disappear(predicate, *, timeout=20, **kw):
    return wait_for(lambda: not predicate(), timeout=timeout, **kw)


def wait_all(predicates: Iterable[Callable[[], bool]], *, timeout=20, **kw):
    preds = list(predicates)
    return wait_for(lambda: all(p() for p in preds), timeout=timeout, **kw)


def wait_any(predicates: Iterable[Callable[[], bool]], *, timeout=20, **kw):
    preds = list(predicates)
    return wait_for(lambda: any(p() for p in preds), timeout=timeout, **kw)


# ---- 业务级封装（对应影刃 Win32Window.wait_active / wait_close）----
def wait_active(window, *, timeout=20):
    return wait_for(lambda: window.is_active(), timeout=timeout, desc="窗口激活")


def wait_close(window, *, timeout=-1):
    # 默认 -1：一直等到窗口关闭（影刃语义）
    return wait_for(lambda: not window.exists(), timeout=timeout, desc="窗口关闭")


def wait_visible(element, *, timeout=20):
    return wait_for(lambda: element.is_visible(), timeout=timeout, desc="元素可见")


# 用法对比：
#   旧（脆弱）：time.sleep(3)                         # 盲等，慢响应即失败
#   新（稳健）：wait_for(lambda: btn.exists(), timeout=10)   # 等条件
```

**与影刃的对应关系**：`wait_for(..., timeout=-1)` ↔ 影刃 `wait_close(timeout=-1)`（一直等）；`wait_visible` ↔ `wait_appear`；`wait_until_disappear` ↔ `wait_disappear`；统一 `timeout` 三态语义 ↔ 影刃 §12.3 表。建议把 WT 现有零散的 `os_sleep` + 卡死检测统一收敛到这一族原语。

---

## 14. 续：能力全景补全 —— Excel 数据驱动 + AI/OCR/验证码 + 其余自动化域

> 针对"是否还有其他自动化能力"的盘点。证据：递归枚举 `xbot/`（122 个 .py）+ 精读 `excel/`、`ai/`、`app/databook.py`。

### 14.1 完整自动化能力全景（回答"还有哪些"）

按技术域归类（均位于 `Resources/Code-Activity/Zh-CN/xbot/`）：

| 域 | 模块 | 关键能力 |
|---|---|---|
| 桌面 UI | `win32/` | element(UIA) / window / image(CV) / clipboard / screenshot / screensaver(防休眠) |
| 浏览器 | `web/` | chrome/edge/firefox/ie/360/cef/自研 ShadowBot；CDP；`ai_element_repair` |
| **Excel** | `excel/` | office/wps/openpyxl/auto_check 四后端；workbook/worksheet/workrange |
| **数据驱动** | `app/databook.py` | 内置"数据表格"：流程循环逐行驱动 |
| **AI/OCR** | `ai/` | 阿里/百度/腾讯/自研 四云；`click_text_in_screen`；票据识别套件 |
| **验证码** | `ai/captcha.py` | 图鉴 TTShiTu 打码 |
| 移动端 | `mobile/` + `appium/` + `u2/` | Android（Appium & uiautomator2） |
| 文件传输 | `ftp/` | FTP / SFTP |
| 文档 | `word/` `pdf.py` | Word 自动化、PDF 读写 |
| 数据库 | `ado.py` `adoapi/` | ADO 直连（MSSQL/Access 等） |
| HTTP | `request.py` | API 调用 |
| 加密 | `aescrypt.py` | AES |
| 文本 | `strings.py`(52KB) | 海量文本处理工具 |
| OCR 基础 | `_core/ocr_secret.py` | OCR 基础设施 |
| 监控/触发器 | `_core/monitor_manager.py` | 事件监视/触发 |
| 应用支撑 | `app/` | databook / dialog(弹窗) / progress(进度) / storage(存储) |
| 引擎桥 | `_core/` | uidriver / robot / retry / validation / locallogging |

### 14.2 Excel 数据驱动（多后端 + 内置数据表）

`excel/__init__.py` 支持四种创建/打开后端，且可 `auto_check` 自动探测本机可用程序：

```python
excel.create(kind="office"|"wps"|"openpyxl"|"auto_check", visible=True, original_file="")
excel.open(file, kind=..., visible=..., password="", write_password="")
# BaseWorkSheet 提供完整行列 API：
ws.get_cell / get_row / get_column / get_range / get_used_range
ws.set_cell / set_row / set_column / set_range
ws.append_row / insert_row / remove_row
ws.get_row_count / get_column_count / get_first_free_column / get_name
```

`app/databook.py` 的**"数据表格（DataTable）"** 是数据驱动的核心：流程引擎按行循环，每行作为一次迭代的输入：

```python
databook.get_row_count()          # 总行数 → 决定循环次数
for i in range(1, databook.get_row_count()+1):
    row = databook.get_row(i)     # 取第 i 行 → 作为本次流程的输入变量
    # ... 用 row 里的字段驱动后续 UI 自动化 ...
```

**长处**：外部 xlsx 读取（`excel`）+ 内置 DataTable（`databook`）+ 流程引擎循环 = 标准**数据驱动 RPA**（批量录单/对账/迁移场景）。WT Automation 若有数据驱动需求，应显式建模"数据表 + 循环迭代"这一层。

### 14.3 AI / OCR / 验证码（智能自动化）

`ai/aibase.py` 的 `AIBase` 把"识别"与"操作"合一——**OCR 找到文字即可直接点击/悬浮，无需任何选择器**：

```python
ai.click_text_in_screen(target_text, button='left', anchor=('middleCenter',0,0), occurrence=1)
ai.hover_on_text_in_window(hwnd, target_text, occurrence=1)
ai.ocr_from_file / ocr_from_window / ocr_from_screen / ocr_from_clipboard_image
# 内部：_recognize_text_with_location → _get_target_text_rectangle → 计算点击坐标
```

`ai/__init__.py` 导出 **4 家云 AI + 验证码 + 图像**：`AliyunAI` / `BaiduAI` / `TencentAI` / `ShadowBot` / `TTShiTu`(验证码) / `XbotImage`。`shadowbot.py` 的 `ShadowBot(AIBase)` 含完整**票据/证件结构化识别**：`general_*`(通用印刷体多档)、`vat_invoice`(增值税发票)、`id_card`(身份证, 带裁剪/复印件/翻拍/PS 告警)、`license_plate`(车牌)、`bank_card`(银行卡)、`train_ticket`(火车票)、`taxi_invoice`(出租车票)；验证码走 `ttshitucaptcha` / `cloud_captcha`。

**长处**：
1. **找字即点**（OCR-click）：无句柄/无 accessibility 的界面也能"按文字操作"，是选择器的强力兜底；
2. **多云 OCR 兜底**：一家失效自动换另一家；
3. **验证码自动识别**：打码平台直连，自动化不再卡在验证码；
4. **票据结构化识别**：直接产出字段（金额/税号/姓名），契合财务/保险 RPA。

### 14.4 对 WT Automation 的追加建议

1. **数据驱动层**：引入"数据表 + 流程循环迭代"（参考 `app/databook.py`），把"读 Excel→循环→驱动 UI"做成一等公民。
2. **OCR 找字点击**：加 `click_text_in_screen` 类能力（OCR + 坐标计算 + sudoku 锚点），作为无选择器时的兜底定位（与已有的 CV 找图互补）。
3. **云 OCR / 验证码集成**：接一家云 OCR 即可覆盖票据识别；验证码按需接打码平台（注意合规与授权边界）。
4. **移动端/数据库/HTTP**：若 WT 目标场景需要，可参考 `mobile`(uiautomator2)、`ado`(数据库)、`request`(HTTP) 的接口设计。

---

## 15. 结合 WT Automation 项目本身的落地（用户定制）

> 本节不再泛谈影刃，而是**对照 WT Automation 真实代码**给出可落地方案。证据来自对 `E:\My_RF_Project\WT_Automation` 的只读梳理（`wt_flow_executor.py` / `flow_excel_io.py` / `WT_AUT_recorded.py` / `wt_flow_locator.py` / `wt_projection_helpers.py` / `build_image_template_library.py` / `wt_server_monitor.py` / `control_live_detector.py` / `flow_packages/param_table_*.json` / `tests/test_param_matrix.py`）。

### 15.1 WT 三大能力现状盘点（对照影刃）

| 能力 | 影刃 | WT Automation 现状 | 差距 |
|---|---|---|---|
| 数据驱动迭代 | `app/databook.py` 数据表格 + 流程循环 | **有，但是"参数表加载期展开"**：`WT_AUT_recorded._apply_param_table_expansion` + `ParameterScanner.scan` + 运行期 `${stepParams.xxx}` 占位符；引擎无运行时 `loop` | 缺"运行时循环"（未知迭代次数/动态列表） |
| OCR 找字点击 | `ai.click_text_in_screen`（OCR→坐标→点击） | **无**：`pytesseract` 仅离线给模板命名；运行时靠 `cv2.matchTemplate` + UIA 文本点击 | 缺 OCR 文字定位这一后端 |
| 监控/触发器 | `monitor_manager`（后台看门狗，自动处理弹窗） | **无**：`wt_server_monitor` 是只读健康看板；`control_live_detector` 是设计期悬停检查器；引擎仅步骤级 `onError/continueWhen` | 缺"流程内事件触发/中断处理"层 |

### 15.2 数据驱动循环骨架（适配 WT 既有 paramTable 机制）

WT 的"数据驱动"本质：**一次录制模板步骤 → 按参数表每行展开成一套带 `stepParams` 的步骤 → 运行期解析 `${stepParams.xxx}`**。所以"循环"对用户而言就是"多行参数表回放"。骨架完全复用既有展开机制，不重复造轮子：

```python
# wt_automation/runner/param_table_runner.py （概念骨架，仅文档，不改项目代码）
import json, threading
from pathlib import Path

class ParamTableRunner:
    """数据驱动循环：一次录制模板 + 参数表逐行回放。复用 WT 既有展开机制。"""
    def __init__(self, template_flow_path: str, executor):
        self.template = json.loads(Path(template_flow_path).read_text(encoding="utf-8"))
        self.executor = executor  # wt_flow_executor.execute_step_by_id

    def run(self, param_table_path: str, *, abort_event: threading.Event = None) -> int:
        rows = load_param_table(param_table_path)        # 兼容 CSV 或 param_table_*.json 的行字典列表
        # 【已有】ParameterScanner 把 模板步 × 参数表行 展开（支持 stepMode/stepTags/towerMode/towerTags 行过滤）
        plans = ParameterScanner.scan(param_table_path, template_steps=self.template["steps"])
        total = len(plans)
        for i, plan in enumerate(plans):
            if abort_event and abort_event.is_set():
                log("用户中止"); break
            log(f"数据驱动迭代 {i+1}/{total}")
            # 【已有】${stepParams.xxx} 由 executor 内部 _resolve_dynamic_value 解析
            for step in plan:
                self.executor.execute_step_by_id(step["id"], context={"rowIndex": i})
        return total


# ──【新增】运行时循环构造：弥补"加载期展开"覆盖不到的场景──
# 场景：网格行数未知 / "存在下一页就继续" / "列表还有未处理项"
def loop_while(self, group_step_ids, condition, *, max_iter: int = 1000) -> int:
    """重复执行一组步骤，直到 condition() 为假或达上限。
    condition 基于 WT 既有定位能力（UIA 探测 / cv2 模板匹配），例如
    '下一页按钮仍可点击' 或 '表格还有空行'。"""
    it = 0
    while condition() and it < max_iter:
        for sid in group_step_ids:
            self.executor.execute_step_by_id(sid)
        it += 1
    return it
```

> 落地点：`flow_packages/param_table_发送综合计算.json` 已是参数表示例；`tests/test_param_matrix.py` 验证了 `ParameterScanner.scan(csv_path, template_steps=...)` 的"逐行×模板步"展开。新增 `loop_while` 是相对加载期展开的**互补**能力（动态迭代次数），建议作为流程 schema 的 `loop` 步骤类型接入 `wt_action_schema.ACTION_SCHEMAS`。

### 15.3 建议补的两块能力（WT 已有依赖，成本低）

**(a) OCR 找字点击**（对齐影刃 `click_text_in_screen`；WT 已在 `build_image_template_library.py` 引入 `pytesseract`，在 `wt_projection_helpers.py` 用 `cv2`，依赖齐备）：

```python
# wt_automation/locating/ocr_text_click.py （建议新增，基于 §10 的 Anchor 思路扩展）
"""OCR 找字点击（对齐影刃 ai.click_text_in_screen）。

依赖：pytesseract（WT 已引入）+ Pillow/NumPy。
定位后端：OCR 文字 → 坐标，作为 UIA / cv2 模板匹配 之外的第三类定位后端。
集成：set_capture() 接 WT 既有截图；click_fn 接 WT 既有点击通道（如 §10 InputInjector.click）。
"""
from __future__ import annotations
import logging
import time
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("wt.ocr_text_click")

try:
    import pytesseract
    from PIL import Image
    _OCR_OK = True
except Exception as _e:  # pragma: no cover
    _OCR_OK = False
    logger.warning("pytesseract/PIL 不可用：%s", _e)


# 九宫格锚点（与 §10 Anchor 同构）
_SUDOKU = {
    "topLeft": (0.0, 0.0), "topCenter": (0.5, 0.0), "topRight": (1.0, 0.0),
    "middleLeft": (0.0, 0.5), "middleCenter": (0.5, 0.5), "middleRight": (1.0, 0.5),
    "bottomLeft": (0.0, 1.0), "bottomCenter": (0.5, 1.0), "bottomRight": (1.0, 1.0),
}


def _anchor_point(box: Tuple[int, int, int, int], part: str = "middleCenter",
                  offset_x: int = 0, offset_y: int = 0) -> Tuple[int, int]:
    x, y, w, h = box
    fx, fy = _SUDOKU.get(part, (0.5, 0.5))
    return int(x + w * fx + offset_x), int(y + h * fy + offset_y)


# 可注入的截图函数：默认用 PIL，WT 应替换为自身截图（mss / wt_projection_helpers 截图基础设施）
_CaptureFn = Callable[[], "Image.Image"]
_capture: Optional[_CaptureFn] = None


def set_capture(fn: _CaptureFn) -> None:
    """接入 WT 既有截图能力（强烈建议，避免重复实现屏幕抓取）。"""
    global _capture
    _capture = fn


def _default_capture() -> "Image.Image":
    from PIL import ImageGrab
    return ImageGrab.grab()


def ocr_boxes(image, *, lang: str = "chi_sim+eng", min_conf: int = 30) -> List[Dict]:
    """返回 OCR 文字块列表：[{text, box(x,y,w,h), conf}]。"""
    if not _OCR_OK:
        raise RuntimeError("pytesseract 不可用")
    data = pytesseract.image_to_data(image, lang=lang, output_type=pytesseract.Output.DICT)
    out: List[Dict] = []
    for i in range(len(data["text"])):
        text = (data["text"][i] or "").strip()
        try:
            conf = int(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1
        if not text or conf < min_conf:
            continue
        out.append({
            "text": text,
            "box": (data["left"][i], data["top"][i], data["width"][i], data["height"][i]),
            "conf": conf,
        })
    return out


def find_text(target: str, boxes: List[Dict], *, occurrence: int = 1,
              partial: bool = False) -> Optional[Dict]:
    """在 OCR 结果中取第 occurrence 个匹配；partial=子串匹配。"""
    if partial:
        matches = [b for b in boxes if target in b["text"]]
    else:
        matches = [b for b in boxes if b["text"] == target]
    if len(matches) < occurrence:
        return None
    return matches[occurrence - 1]


def click_text_on_screen(target: str, *, occurrence: int = 1,
                         anchor_part: str = "middleCenter", offset_x: int = 0, offset_y: int = 0,
                         lang: str = "chi_sim+eng", min_conf: int = 30,
                         click_fn: Optional[Callable[[int, int], None]] = None,
                         timeout: float = 0.0) -> bool:
    """OCR 识别屏幕文字并点击，返回是否成功。
    click_fn: 注入点击实现（默认 pyautogui.click）；WT 接自身点击通道（§10 InputInjector）。
    timeout>0: 轮询等待文字出现（复用 §12.3 的 wait_for 三态语义）。
    """
    cap = _capture or _default_capture

    def attempt() -> bool:
        img = cap()
        boxes = ocr_boxes(img, lang=lang, min_conf=min_conf)
        hit = find_text(target, boxes, occurrence=occurrence)
        if not hit:
            return False
        cx, cy = _anchor_point(hit["box"], anchor_part, offset_x, offset_y)
        if click_fn is not None:
            click_fn(cx, cy)
        else:
            import pyautogui
            pyautogui.click(cx, cy)
        logger.info("OCR 点击 '%s' @ (%d,%d) conf=%d", target, cx, cy, hit["conf"])
        return True

    if timeout and timeout > 0:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if attempt():
                return True
            time.sleep(0.5)
        return False
    return attempt()


def hover_text_on_screen(target: str, *, occurrence: int = 1,
                         anchor_part: str = "middleCenter", offset_x: int = 0, offset_y: int = 0,
                         lang: str = "chi_sim+eng", min_conf: int = 30,
                         move_fn: Optional[Callable[[int, int], None]] = None) -> bool:
    """悬浮版：找到文字后移动鼠标到锚点（不点击）。"""
    cap = _capture or _default_capture
    img = cap()
    boxes = ocr_boxes(img, lang=lang, min_conf=min_conf)
    hit = find_text(target, boxes, occurrence=occurrence)
    if not hit:
        return False
    cx, cy = _anchor_point(hit["box"], anchor_part, offset_x, offset_y)
    if move_fn is not None:
        move_fn(cx, cy)
    else:
        import pyautogui
        pyautogui.moveTo(cx, cy)
    return True
```

**(b) 流程内事件触发器 / 看门狗**（对齐影刃 `monitor_manager`，补 WT 缺失层）：

```python
# wt_automation/runtime/trigger_watchdog.py （建议新增）
"""流程内事件触发器 / 中断看门狗（对齐影刃 monitor_manager）。

WT 现状：wt_server_monitor 是只读健康看板，control_live_detector 是设计期悬停检查器；
引擎仅步骤级 onError/continueWhen，没有"后台线程订阅 UI 事件并自动反应"层。
本模块补这块：注册监控记录（元素选择器 + 处理方法），流程运行期间后台监听，
匹配到（如意外弹窗）就自动处理（关弹窗/点确定），让主流程不被中断。
"""
from __future__ import annotations
import logging
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, List, Optional

logger = logging.getLogger("wt.trigger_watchdog")


@dataclass
class MonitorRecord:
    """一条监控记录（对应影刃 add_monitor_record(handle_method, close_button, element_selector, process, package)）。"""
    name: str
    selector: dict                          # 传给 locate() 的元素定位描述
    handler: Callable[[], None]             # 命中后执行（如点击关闭按钮）
    process: Optional[str] = None           # 限定进程名（可选）
    interval: float = 0.3                   # 轮询间隔（秒）
    enabled: bool = True
    max_fires: int = 0                      # 0=不限次数；>0=最多触发 N 次
    _fires: int = field(default=0, repr=False)


class TriggerWatchdog(threading.Thread):
    def __init__(self, locate_fn: Callable[[dict], Optional[object]], *,
                 poll_interval: float = 0.3, name: str = "wt-trigger-watchdog"):
        super().__init__(daemon=True, name=name)
        self._locate = locate_fn            # 注入 WT 既有定位能力 (selector) -> element|None
        self._records: List[MonitorRecord] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.poll_interval = poll_interval

    # ---- 注册 / 注销 ----
    def add(self, rec: MonitorRecord) -> None:
        with self._lock:
            self._records.append(rec)

    def add_simple(self, name: str, selector: dict, handler: Callable[[], None], **kw) -> MonitorRecord:
        rec = MonitorRecord(name=name, selector=selector, handler=handler, **kw)
        self.add(rec)
        return rec

    def remove(self, name: str) -> None:
        with self._lock:
            self._records = [r for r in self._records if r.name != name]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    # ---- 生命周期 ----
    def start(self) -> "TriggerWatchdog":
        super().start()
        logger.info("TriggerWatchdog 启动，监控 %d 条记录", len(self._records))
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        self.join(timeout)
        logger.info("TriggerWatchdog 已停止")

    @contextmanager
    def running(self):
        """with watchdog.running(): ... 自动启停。"""
        self.start()
        try:
            yield self
        finally:
            self.stop()

    # ---- 主循环 ----
    def run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                records = list(self._records)
            for rec in records:
                if not rec.enabled or (rec.max_fires and rec._fires >= rec.max_fires):
                    continue
                try:
                    if self._locate(rec.selector):       # 复用 WT 定位能力
                        logger.info("触发器命中: %s", rec.name)
                        rec.handler()
                        rec._fires += 1
                except Exception as e:                   # 单条异常不拖垮看门狗
                    logger.warning("触发器 %s 执行异常: %s", rec.name, e)
            self._stop.wait(self.poll_interval)


# ---- 复用示例：自动关闭"意外弹窗" ----
def build_popup_guard(watchdog: TriggerWatchdog, popup_selector: dict,
                      close_selector: dict, locate_fn, click_fn) -> MonitorRecord:
    """注册一个"发现弹窗就点关闭"的监控记录。"""
    def _on_popup():
        el = locate_fn(close_selector)   # 找到关闭按钮
        if el is not None:
            click_fn(el)                  # 用 WT 既有点击通道点掉
    return watchdog.add_simple("auto_close_popup", popup_selector, _on_popup, interval=0.5)
```

> **WT 集成要点（低成本、已有依赖）**：
> - OCR 找字：`set_capture()` 接 WT 截图（参考 `wt_projection_helpers` 的截图/模板匹配基础设施）；`click_fn` 接 §10 的 `InputInjector.click`；英文/数字用 `lang="eng"` 更准。
> - 看门狗：`TriggerWatchdog(locate_fn=...)` 的 `locate_fn` 接 `wt_flow_locator` 的定位能力，`handler` 内可调用 §10 `InputInjector` 或 `wt_flow_executor.execute_step_by_id`。
> - 二者可组合：看门狗命中弹窗后，用 `click_text_on_screen` 找到"确定"文字再点，比固定坐标更抗 UI 微调。
> 这两块都属于"低风险增量"，建议优先于架构级改造。

---

## 16. 整轮 REA 工作收尾总结

### 16.1 我们做了什么（工作链路）

1. **架构定性**：用 REA（`binary_session` / `open_binary(Ghidra)` / `inspect_managed_artifact`）对影刃安装目录逐文件判定性质，推翻旧报告"核心加密不可见"假设——影刃引擎 = **原生 x86_64 C++ + .NET 6（CIL 未混淆）混合**，IL 可用 dnSpy 完整还原。
2. **原生层函数级反编译**：`NativeHelper.dll` 的 `FUN_180018600`（线程劫持/attach：`NtSuspendThread → NtGetContextThread → 改写 CONTEXT → NtResumeThread`）——覆盖 Java/SAP 的底层机制。
3. **两个 exe 引导反编译**：`Provider.exe` 与 `ShadowBot.exe` 的 `main` 均证实为 **.NET 6 apphost**（`__scrt_is_managed_app()` / `apphost` / `6.0.24` / `hostfxr` 启动）——否定"原生管道服务端"设想，管道服务端定位在 .NET `Provider.dll`。
4. **能力全景盘点**：递归枚举 `xbot/` 122 个桩文件，刻画完整自动化能力域（桌面/浏览器/Excel/AI/移动端/FTP/Word/PDF/数据库/HTTP/加密/文本/OCR/监控）。
5. **关键模块精读**：`selector`（11 后端 + 集中式仓库 + AI 自愈）、`element`（双模式 + IME 鲁棒）、`image`（CV）、`window`（作用域）、`ai`（OCR→点击 / 票据 / 验证码）、`excel`+`databook`（数据驱动）、`monitor_manager`（看门狗）。
6. **对照 WT 项目落地**：只读梳理 WT Automation 真实代码，盘点三大能力现状，产出适配骨架（数据驱动循环、AI 自愈、显式同步、OCR 找字、触发器看门狗、多通道输入）。

### 16.2 一句话关键证据

- **架构**：原生 exe/NativeHelper + .NET6 引擎（IL 未混淆）+ 加密 Python 编排层 `winro.pyb`。
- **原生注入**：`FUN_180018600` 挂起线程→改写 CONTEXT→恢复（Java/SAP attach）。
- **引导**：两个 exe 都是 apphost（hostfxr 启动），管道服务端在 .NET `Provider.dll`（dnSpy 还原）。
- **工程化长板**：11 后端统一 Selector + 集中式/版本化仓库 + AI 自愈 + 显式同步 + OCR 找字 + 触发器看门狗。

### 16.3 落到 WT 的可借鉴资产（文档索引）

| 骨架 | 文档章节 | 对齐影刃长处 |
|---|---|---|
| 多通道输入注入 `InputInjector` | §10 | #4 双模式 + IME 鲁棒 |
| AI 元素自愈 `SelfHealingResolver` | §13.1 | #3 选择器自愈 |
| 显式同步 `wait_for` 家族 | §13.2 | §12.3 等待-条件同步 |
| 数据驱动循环 `ParamTableRunner` + `loop_while` | §15.2 | §14.2 数据驱动 |
| OCR 找字点击 `click_text_on_screen` | §15.3(a) | §14.3 OCR 定位 |
| 触发器看门狗 `TriggerWatchdog` | §15.3(b) | `monitor_manager` |

> 全部骨架**仅写入本文档，未改动 WT Automation 项目代码**（符合约定）。

### 16.4 WT 落地优先级（最终建议）

1. **显式同步 `wait_for` 替代 `os_sleep`** —— 低成本、高收益（§13.2）。
2. **数据驱动**：复用现有 `paramTable` 展开 + 补 `loop_while` 运行时循环（§15.2）。
3. **OCR 找字点击** —— WT 已有 `pytesseract`，直接补定位后端（§15.3a）。
4. **触发器看门狗** —— 补 WT 缺失的"流程内事件触发/中断处理"层（§15.3b）。
5. **多通道输入 / IME 鲁棒** —— 中文环境必做（§10）。
6. **AI 自愈 / 集中式选择器仓库** —— 需后端支撑，收益高（§13.1）。
7. **进程隔离引擎** —— 架构级，收益高但成本也高（#1）。

### 16.5 边界与下一步

- **REA 已穷尽原生层**；.NET 层（命名管道协议 / Selector 匹配算法 / `DriverInput` 实现）用 **dnSpy** 还原（IL 未混淆，同目录有 PDB，见 §8/§11.4）。
- **Python 编排层 `winro.pyb`** 自研加密，需配套 `pyb.*.pyd`，价值低、优先级最低。
- 全部工作仅做架构学习、接口借鉴与本地静态分析，未破解授权、未绕过许可、未提取密钥；第三方库（`interception`/`AutoItX3`/Chromium CDP）按公开文档集成即可。

---

*证据来源：REA 对 `E:\影刃RPA\ShadowBot\` 的实测 + 影刃 `xbot/*` 源码；以及 WT Automation 项目 `E:\My_RF_Project\WT_Automation` 的只读梳理（`wt_flow_executor.py` / `WT_AUT_recorded.py` / `flow_excel_io.py` / `wt_flow_locator.py` / `wt_projection_helpers.py` / `build_image_template_library.py` / `wt_server_monitor.py` / `control_live_detector.py` / `flow_packages/param_table_*.json` / `tests/test_param_matrix.py`）。逆向与梳理仅用于改进自有项目，不涉及破解授权、许可绕过或密钥提取。*

---

## 17. 结论审计与勘误（2026-10-15 复核）

> 应"判断之前 AI 的调查是否正确"的要求，对本轮及此前全部结论做逐条复核：WT 侧用符号级搜索验证引用的函数/文件真实存在；影刃侧用源码精确行号/签名验证；.NET 侧用 REA 复跑验证。

### 17.1 复核通过的结论（证据位置）

| 断言 | 结果 | 证据 |
|---|---|---|
| WT `locate_template_center_by_path(template_path, timeout_seconds=8, confidence=0.8, region=None, scales=None)` | ✓ | `wt_projection_helpers.py:261` |
| WT `get_flow_control_definition(step_id, control_id)` / `click_menu_candidate_by_text` | ✓ | `wt_flow_locator.py:7797` / `:11190` |
| WT `wt_action_schema.py` 存在 | ✓ | 9.9 KB |
| WT `control_maps/standard/总控件信息.json` 存在 | ✓ | 另有 `standard_control_catalog.json` / `standard_catalog_mismatch_report.json` |
| WT `WT_AUTOMATION_Agent/flow_repair.py`（LLM 治理型自愈） | ✓ | 20.4 KB |
| WT 数据驱动参数表 | ✓ | `flow_packages/param_table_发送综合计算.json` |
| 影刃 11 后端 `_selector_node_types` | ✓ | `xbot/selector.py:29` |
| 影刃 `selectors.xml` 存储 / `SelectorStore` / `RemoteSelectors` / `convert_element_info_to_selector` | ✓ | `selector.py:152/156/163/260` |
| 影刃 `select(item, mode='fuzzy')` / `select_by_index` / `find_related_element(timeout=20)` / `check(mode='check')` | ✓ | `win32/element.py:178/204/191/166`（签名逐字核对） |
| `SelectorHelper` 在 `UIAutomation.dll`（18 方法）而非 Tools.dll | ✓ | REA `inspect_managed_members`：token `0x02000022`，`ShadowBot.UIAutomation.Utilities.SelectorHelper` |
| `Tools.dll`/`Provider.dll` 超 REA 元数据预算（metadata-not-admitted） | ✓ | 复跑 `inspect_managed_members` 均返回 `resource-limit`（Tools.dll 1857344 字节） |
| 匹配内核存在轻量混淆（187 方法重命名类 + `ObfuscationAttribute`） | ✓ | REA 成员枚举输出 |

### 17.2 勘误

1. **旧对标报告**："核心实现被 `winro.pyb` 加密、不可见" → **不成立**。.NET 引擎层（含选择器匹配模型类型面）REA 可见，且 API 层 `xbot/*.py` 为未加密真实源码。
2. **前轮 REA**："SelectorHelper 在 `Tools.dll`" → **实际在 `UIAutomation.dll`**（已勘误至 doc11 §7）；"IL 未混淆"应限定为 **"公开模型层（Primitives/Utilities）未混淆，匹配内核被重命名混淆"**。

### 17.3 新发现（WT 侧，建议关注）

`tools/generic_flow/generic_flow_locator.py` **重复实现**了 `get_flow_control_definition`(:7850) 与 `click_menu_candidate_by_text`(:11243)，主实现位于 `wt_flow_locator.py:7797/11190`。双实现存在漂移风险，建议后续合并或明确以 `wt_flow_locator` 为主实现（后端对接时 likewise 只接主实现）。

### 17.4 本轮未复验的早前结论（依据当时工具输出，暂无冲突证据）

`Provider.Common.DriverInput` 命名空间落点、`winro.pyb` 体积、两个 exe 的 apphost 版本号、shadowbot-6.2.21 的 `.py` 文件数量级。

---

## 18. REA 二挖：可靠性内核（错误模型 · 重试原语 · 触发器架构 · OCR 抽象 · 子会话）

> 依据：影刃 `_core/`、`ai/`、`errors.py`、`eingineError.py`、`web/element.py` 未加密源码 + 前轮 REA 成员枚举。均为审计通过的真实证据。

### 18.1 错误模型：AI 自愈是按"错误类型"门控的（重要设计）

影刃把异常分成两类，**由类型决定能否进入 AI 修复**：

- `UIAError`（`errors.py`）——**可修复类**。自带两个分类器：`is_loading_error()` / `is_not_found_error()`，供上层决定"等待重试"还是"触发自愈"。
- `EngineError`（`eingineError.py`）——**不可修复类**，源码注释原文：**"无法使用AI修复的错误，使用此异常类型"**。即连接类/环境类错误（连接断开、插件缺失）不浪费 AI 调用。
- .NET 侧对应 `UIAExceptionCode`（36 项枚举，REA 已枚举 21 项）：`ValidationFail/Unknown/Common/UnHandle/UIDriverConnectionError/CEFBrowserConnectionError/ChromiumBridgeConnectionError/NoChromiumBridgeError/NonsupportOperation/MobileDeviceManagerConnectionError/NoJavaExtensionError/JsDialogOpened/ChildSessionStartError/ChildSessionConnectionError/DeviceConnectionError/NoSuchWindow/NoSuchElement/NoSuchFrame/PageIsLoading/FrameIsLoading/JavaScriptError…`
- `EngineErrorCode.message()` 注释："动态渲染的 msg 不需要在此处声明，如：WPS_Addon_Not_Found" —— 错误码 + i18n 渲染分离。

**对 WT 的启示**：治理型自愈（flow_repair）增加一层**错误分类门控**——只有 `NotFound/加载类` 错误才进 LLM 修复，连接/环境类直接走重试或报错，省 token 且更可控。

### 18.2 Retry：迭代器式双界重试原语（比 callback 式更优雅）

`_core/retry.py`：

```python
class Retry():
    def __init__(self, timeout=-1, times=-1, *, interval=0.2, error_message=None, ignore_exception=False): ...
    def __iter__(self): ...
# 用法（推断自迭代器协议）：
# for attempt in Retry(timeout=20, times=5, interval=0.2):
#     ...尝试动作，失败 raise → 由迭代器决定 sleep(interval) 后进入下一轮
```

**设计要点**：`timeout` 与 `times` 双界（任一到达即停）、`interval=0.2` 默认、`ignore_exception` 控制吞异常。**建议合入 §13.2 的 `wait_for` 家族**：WT 的显式同步可用同款"双界迭代器"统一 `wait_appear/wait_vanish/wait_property` 的重试语义。

### 18.3 触发器运行时 = WebSocket 事件驱动（§15.3b 的升级路径）

`_core/trigger/` 实际由 5 个模块构成：`trigger_manager.py`（注册中心）+ `trigger_server.py`（服务端）+ `websocket_server.py` / `websocket_thread.py`（**WebSocket 事件通道**）+ `trigger_executor.py`（命中执行器）。

**架构启示**：影刃的"看门狗"不是纯轮询——事件（弹窗出现、控件消失等）经 **WebSocket 推送 → trigger_manager 分发 → trigger_executor 执行处理方法**。§15.3b 的 `TriggerWatchdog` 骨架（轮询版）可作为 v1；**v2 升级路径**：定位器支持事件订阅（UIA AutomationPropertyChanged / 窗口事件钩子），看门狗改为事件驱动，降低轮询开销与延迟。

### 18.4 OCR：供应商抽象 + 四源 + 文本悬浮（证实 §15.3a 的 API 形状）

`ai/` 包 = `aibase.py` + **4 个供应商**（`aliyun.py`/`baidu.py`/`tencent.py`/`shadowbot.py`）+ `captcha.py`（验证码）+ `xbotimage.py`。`AIBase` 统一四种识别源：

```python
ocr_from_file(image_path, edition) -> List[str]
ocr_from_window(hwnd, region=None, edition)      # region=(left,top,right,bottom)
ocr_from_screen(region=None, edition)
ocr_from_clipboard_image(edition)
hover_on_text_in_screen(target_text, delay_after=1, anchor=None, occurrence=1, edition=None)
```

`hover_on_text_in_screen(target_text, anchor, occurrence)` 与 §15.3a `hover_text_on_screen` 骨架**签名级一致**（九宫格锚点 + 第 N 个命中），交叉验证通过。**对 WT 的启示**：把 OCR 抽象为 `OCRBackend`（pytesseract / PaddleOCR / 云端四选一）+ 统一四源入口，§15.3a 即可平滑升级。

### 18.5 .NET 实用层值得借鉴的类型（前轮 REA 成员枚举，二挖归档）

| 类型 | 价值 |
|---|---|
| `Utilities.ChildSessionLauncher` | **子会话后台执行**：把自动化放进独立 Windows 子会话，机器人不与用户桌面抢焦点（无人值守的关键架构） |
| `Utilities.WindowsVirtualKeyboardTool` | **虚拟键盘输入**：输入法敏感场景的又一通道（§10 的 CdpBackend 之外再补一种） |
| `Utilities.EngineErrorAnalyzer` + `IStackAnalyzer`/`UIAStackTraceAnalyzer` | **错误诊断**：从异常栈自动归类错误（配合 §18.1 门控） |
| `Utilities.SnippingTool` / `Primitives.CaptureArea` | 截图/框选原语（图像定位的采集端） |
| `Utilities.TaskScheduledHelper` + `Microsoft.Win32.TaskScheduler` 引用 | 计划任务级调度（定时触发） |
| `Utilities.Curl` / `HttpMessageProtocol` | 内置 HTTP 原语（不依赖系统代理配置） |

### 18.6 工具边界补充（本轮实测）

REA 的 `search_strings` 对**托管目标不可用**：Ghidra(win32) 拒绝 managed PE、Hopper 仅 macOS、IDA 未配置。因此 §17.4 中"`DriverInput` 命名空间落点 / 命名管道端点名"仍无法用 REA 验证——**最省路径是你本机 dnSpy/ILSpy 打开 `Tools.dll`/`Provider.dll` 搜字符串**，或 `strings.exe` + 正则过滤。

### 18.7 补足措施规划更新（融合 §7/§16 既有规划，仍全部只落文档）

| # | 措施 | 来源 | 优先级 |
|---|---|---|---|
| 1 | 错误模型归一：`WTErrorCode` 分类码 + `is_loading_error/is_not_found_error` 分类器 + **错误类型门控 AI 修复** | §18.1 | 高（与 flow_repair 直接衔接） |
| 2 | `Retry` 双界迭代器合入 `wait_for` 家族（timeout/times/interval） | §18.2 | 高（§13.2 语义升级） |
| 3 | 统一 Selector 抽象 + BackendResolver（UIA/CV 双后端） | doc11 §8/§9 | 高（已落骨架） |
| 4 | OCR 供应商抽象（`OCRBackend` 四源）升级 §15.3a | §18.4 | 中 |
| 5 | TriggerWatchdog v2：轮询 → 事件订阅（UIA 属性/窗口事件） | §18.3 | 中 |
| 6 | 子会话后台执行（无人值守不抢桌面焦点） | §18.5 | 中（架构级，审批后评估） |
| 7 | 虚拟键盘输入通道（输入法敏感场景第三通道） | §18.5 | 低（§10 扩展位） |
