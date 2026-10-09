# 影刃RPA（ShadowBot 6.2.23）深度对标 · 卷六：IPC 协议、外围子系统与数据模型

> 本卷是卷一/卷二的纵向加深：把"进程之间怎么说话、定位子系统由什么组成、数据存在哪、会话怎么管"四件事挖到底。
> 证据类型：明文 Python 源码（utils 层）、.NET 元数据标识符（Protocol/Shell.Development/Runtime.Development/Tools）、SQLite 表结构（schema 实录）、目录实体清单。全部为产品自带可见信息。

---

## 1. IPC 协议层

### 1.1 管道传输（Python 侧，明文）

`utils\process\pipe\`：跨平台管道客户端四件套（`pipeclient_base / pipeclient_win / pipeclient_nix / pipeclient_mac / pipeclient_linux`），Win 实现导入 `json + struct`——**帧格式 = 长度前缀 + JSON 载荷**，接口仅 `connect(pipe_name) / write(obj) / read() / close()`。

引擎侧同时持有两条管道（`xbot_interpreter.py`）：
- `robot.connect(params.robot_pip())` —— 流程控制/日志/审计通道（连 Runtime）；
- `uidriver.connect(params.uia_pip())` —— 定位服务通道（连 UIA Provider）。

### 1.2 RPC 信封（Protocol.dll 元数据实测）

`ShadowBot.Components.Protocol.dll`（4,520 个标识符）中除云端 API 契约外，识别出本地 RPC 的信封形状：

```
RequestId / Method / Response / ErrorMessage / RequestBizId / RequestCost / ResponseCost
ClientMessage / NativeMessage / get_JsonRpc          ← JSON-RPC 风格信封
GenerateAnchorRequest / GenerateAnchorResponse        ← 捕获器"生成锚点"RPC
CreateSelectorStoreResponse                           ← 选择器库装载 RPC
```

即：**Python ↔ C# 之间是 JSON-RPC over 命名管道**，请求带 RequestId（支持超时与应答配对——对应 Python 侧 `execute(method, args, timeout=-1)` 签名），捕获器生成锚点、装载选择器库都有专用 RPC 方法。

### 1.3 其余对外通道（汇总）

| 通道 | 协议 | 用途 |
|---|---|---|
| 设计器 ↔ Shell | 本地 HTTP `:31500/api/v1` | 外部系统触发/查询 |
| 事件触发 | WebSocket（引擎内嵌 server） | 热键/文件/弹窗等触发器 |
| 云端 | REST（api.winrobot360.com）+ 协议 DLL 的 VO 契约 | 市场/账号/应用同步 |
| 浏览器 | CDP（chrome）+ Bridge（Shadowbot.Bridge 原生桥） | 网页自动化 |
| 目标进程 | DLL 注入（Hlay/JavaBridgeHook） | 自绘控件/Java 可访问性 |

---

## 2. 定位子系统的实体组成（support_x64 + Native\x64 目录实录）

影刃的定位能力不是一个大 DLL，而是**11 个可独立安装/运行的子系统**：

| 子系统 | 实体文件 | 机制解读 |
|---|---|---|
| **driver**（驱动级输入） | `ShadowBot.HardwareDriverInput.Installer.exe` + `InterceptorLib\` | interception 驱动**独立安装器**——用户显式启用才装内核驱动，合规与稳定性风险隔离 |
| **cv-engine** | `xbot_cv.py`（明文）+ `xbot_cv.pyb`（52KB 加密）+ site-packages | **CV 引擎又是一个内嵌 Python 包**（见 §2.1） |
| **credential** | `ShadowBotCredentialProviderInstaller.exe` + x64/x86 | **Windows 凭据提供程序（Credential Provider）**——注册进系统登录界面，`unlock_screen(user, password)` 的系统级实现：锁屏后可凭凭据自动解锁 |
| **Hlay** | `HLayDll.x64.dll / HLayDll.x86.dll / HLayFrontend.dll` | 注入目标进程、把**自绘 UI 层**暴露为可定位元素（对应选择器节点类型 `hlay`）——对付 Qt/自绘界面 |
| **java** | `ShadowBot.JavaBridge.Installer.exe` + `BridgeLib\` | Java Access Bridge 安装器；主目录另有 `ShadowBotJavaBridgeHook.dll` 注入钩子 |
| **chrome** | CDP 集成 | Chromium 内核浏览器自动化 |
| **console** | `ShadowBot.ConsoleUrlProtocol.Installer.exe` | 注册 URL 协议（shadowbot:// 链接拉起） |
| **Shadowbot.Bridge** | 原生桥 + PDB(4.7MB) | CEF/浏览器桥 |
| **ProcessLauncher** | 目录 | 受控进程启动 |
| **mobile / mobile_legacy** | 目录 | uiautomator2 两代实现 |
| **Native\x64** | `debugger.dll + debugger.host.exe`、`ShadowBot.Inspectors.dll`、`RpaBuilder.dll`、`qt4_support\`、`ShadowBotJavaBridgeHook.dll` | **原生调试器宿主**（流程断点的落点）、**Inspect 探测器实体**、流程构建器、Qt4 兼容 |

**模式总结：每个高风险/高耦合子系统都有独立 Installer + 独立进程/注入体，主产品保持干净**。安装器统一形态 `*Installer.exe + *.deps.json + dll.config`。

### 2.1 CV 引擎真相（重要）

`support_x64\cv-engine\xbot_cv.py`（明文，完整读过）暴露了 CV 引擎的真实能力来源：

```python
from xbot_uied.detect_text import ip_region_proposal as ip
# .\venv\Scripts\python.exe -m xbot_cv --img="....png"
parser.add_argument('--min-grad', type=int, default=3)
parser.add_argument('--ffl-block', type=int, default=5)
parser.add_argument('--min-ele-area', type=int, default=20)
parser.add_argument('--max-word-inline-gap', type=int, default=4)
parser.add_argument('--max-line-ingraph-gap', type=int, default=2)
parser.add_argument('--merge-contained-ele', type=bool, default=False)
parser.add_argument('--remove-top-bar', type=bool, default=True)
ip.compo_detection(args.img, uied_params, resize_by_height=None, show=...)
```

这组参数（min-grad / ffl-block / min-ele-area / max-word-inline-gap / merge-contained-ele）与开源项目 **UIED**（基于梯度的无监督 UI 组件检测 + 文本检测融合）完全一致。结论：

- 影刃的"图像"能力是**两层**：① 经典模板匹配（Emgu.CV，similarityThreshold 可调）；② **截图→全 UI 元素的无监督检测**（CV Spy / 图像元素库的底座）——从一张截图自动提出所有按钮/文本框/容器框，供人挑拣入库；
- CV 引擎以 Python 子模块实现、经管道被 Provider 调用，实现加密（xbot_cv.pyb）但入口与参数全明文。

### 2.2 控件语义角色词表（88 个，Provider.SelectorHelper 实测）

HTML 标签族（Div/Span/H1-H6/UL/OL/Table/Td/…）+ 语义控件族（Button/ComboBox/DatePicker/TimePicker/ProgressBar/RadioButtonGroup/ToggleButton/Upload/…）+ **容器语义族**（`AbsoluteContainer / LinearContainer / RelativeContainer / FrameContainer / TableContainer / ViewGroup / ViewBox / RecycleView`）。

用途推断：捕获器生成选择器时给元素打"角色"标签（用于生成可读名称、AI 描述、相似元素归并）。**WT 可直接借鉴这张词表**：control_maps 库的控件条目目前只有原始属性，加一列 `role`（用同款词表）可让控件库可读性、AI 描述质量、去重逻辑同时受益。

---

## 3. 数据模型（SQLite 表结构实录）

### 3.1 `%LOCALAPPDATA%\ShadowBot\recording.db3` —— 录屏台账

```sql
CREATE TABLE recording_data (
  "taskId" TEXT NOT NULL UNIQUE,
  "isAutoClear" INTEGER, "autoClearTime" TEXT,
  "isRecord" INTEGER,            -- 该任务是否录像
  "isSuccess" INTEGER, "error" TEXT, "errorTime" TEXT,
  "recordPath" TEXT,             -- 录像文件路径
  "needUpload" TEXT,             -- 是否需上传（云端排障）
  PRIMARY KEY("taskId"))
```

**每个任务一行录像台账**：成败、错误时间、文件路径、保留策略、上传标记——与 §卷一 2.6 的 30s 分段环形缓冲配套。

### 3.2 `users\<id>\tasks.db3` —— 任务台账

```sql
CREATE TABLE tasks (
  "uuid" TEXT NOT NULL UNIQUE, "status" INTEGER,
  "jobId" TEXT, "jobName" TEXT, "appId" TEXT, "appName" TEXT,
  "createTime" TEXT, "error" TEXT, "description" TEXT, "sourceKind" INTEGER);
CREATE TABLE log_table_index (
  "taskId" TEXT NOT NULL UNIQUE, "logTableName" TEXT, PRIMARY KEY("taskId"));
```

**日志按任务分表**（log_table_index 记录每个任务的日志表名）——单任务日志可整体查询/清理，不与全局日志混写。WT 的任务队列（wt_task_queue 8 状态机）对表，可对比借鉴"按任务归档日志"的物理隔离。

### 3.3 `users\<id>\user.db3` —— 应用资产

```sql
development_apps(uuid, name, ownerName, icon, developRobotVersionUuid,
                 developTimestamp, createTime, modifyTime, isSynchronized,
                 releaseTimestamp, description)
developmentsync_apps_v2(..., robotType, isRecycleBin)   -- 回收站语义
subscribed_apps(uuid, release_uuid)                     -- 市场订阅
magicblock_chathistory_web(id, robot_uuid, prototype_name, role,
                 kind, content, create_time, is_valid, render_content, extra_info)
metadata(key, value)
```

AI"魔法指令"的对话历史也是结构化表（role/kind/render_content），可回放、可校验（is_valid）。

---

## 4. 会话管理（解锁/锁屏/远程会话，明文实现）

`utils\os\wts.py`（9.2KB，**非桩代码，真实现**，参考 debugpy 与 pyWinAPI）：

- 走**未公开 API** `winsta.dll!WinStationQueryInformationW`（而非常规 WTS API）；
- `WinStationIsLockedAndRemoted() -> (isLocked, isRemoted)`：
  - `WinStationInformation` 拿 `WINSTATIONINFORMATIONW.ConnectState`；
  - `!= WTSDisconnected` 时再查 `WinStationLockedState`；
  - `WinStationClient` 拿 `WINSTATIONCLIENT.ClientName` 判断是否远程会话；
- 配套常量全套：WTS_CONNECTSTATE_CLASS（Active/Connected/…/Init 10 态）、WTS_INFO_CLASS 30 项。

组合系统级能力：**凭据提供程序**（credential 子系统）+ 会话状态感知（wts.py）+ `lock_screen/unlock_screen/rdp_lock_screen` 指令 = "无人值守时自动解锁并恢复自动化"的完整闭环。另配套 `process_helper.py` 的**会话感知进程枚举**（`get_process_list(current_session=True)`，底层 `nt_get_process_list` NT API）——RDP 场景下只操作本会话进程，避免跨会话误伤。

**WT 借鉴点**：内网服务器自动化同样面临"RDP 断开→界面停摆"。至少可实现：① wts.py 同款会话状态检测（ctypes 直接可抄，源码已注明出处）；② 运行前检测 `isRemoted/isLocked` 并告警；③ 配合 P0-5 防休眠形成"会话保活三件套"。

---

## 5. 引擎可靠性补充（本轮新证）

| 机制 | 文件 | 新细节 |
|---|---|---|
| 执行采样 | `utils\process\trace.py` | `start_trace()` 起后台线程周期 `trace_frames()`——**持续记录调用栈样本**，卡死时不止"最后一帧"，有过程帧序列可回放 |
| 进程枚举 | `utils\process\process_helper.py` | `Process(pid, name, exe_path, session_id)`；`get_process_list / get_process_list_by_name / get_process(predicate)`；NT 层 `nt_get_process_list(current_session)` |
| 管道 | `utils\process\pipe\*` | 长度前缀 + JSON 帧；四平台实现 |
| 原生调试宿主 | `Native\x64\debugger.dll + debugger.host.exe` | 流程断点/单步落在独立原生宿主，调试崩溃不连累运行 |

---

## 6. AI 引擎抽象（多厂商同接口）

`xbot\ai\aibase.py`（明文桩，接口全录）：

```
AIBase
├── ocr_from_file(image_path, edition=None) -> List[str]
├── ocr_from_window(hwnd, region=None, edition=None)
├── ocr_from_screen(region=None, edition=None)
├── ocr_from_clipboard_image(edition=None)
├── hover_on_text_in_screen(target_text, delay_after=1, anchor=None, occurrence=1, edition=None)
├── hover_on_text_in_window(hwnd, target_text, ...)
└── click_text_in_screen(target_text, button, click_type, keys, ..., occurrence=1, edition=None)
```

三个设计细节值得记录：
1. **`occurrence` 参数**：识别到多个相同文本时按"先左后右、再从上至下"排序取第 N 个——OCR 定位的消歧语义官方化；
2. **`edition` 参数**：同一接口可切换引擎版本（识别精度/速度档位）；
3. **多厂商实现并列**：`aliyun.py / baidu.py / tencent.py / shadowbot.py(自家) ` 同继承 AIBase，`xbotimage.py` 统一图像源（磁盘/剪贴板/屏幕），`captcha.py` 单列验证码域。

叠加协议层的 `AiElement*`（`AiElementId / AppAiElementStatus / NeedRepairAiElement`）：**AI 元素是云端一等资产**，带修复状态标记——"元素坏了自己修"在影刃是一条完整的商业闭环（云端存储 AI 描述 → 运行时语义匹配 → 失效标记 → 云端重训/修复）。

---

## 7. 新增统计（第三轮）

| 指标 | 数值 | 来源 |
|---|---|---|
| Protocol.dll 标识符 | 4,520（唯一 4,296） | #Strings 堆 |
| Shell.Development 标识符 | 20,735 | #Strings 堆 |
| **指令处理器类** | **197 个**（IBlockHandler/BaseBlockHandler/DefaultBlockHandler + 各指令 Handler） | Runtime.Development #Strings |
| **设计器自定义面板域** | **31 个**命名空间（Element/Image/Win32/Workflow/Databook/ScreenSaver/Rdp/Nlp/Ocr/...） | Shell.Development BlockSettings.Controls |
| **控件语义角色词表** | **88 个** | Provider.SelectorHelper |
| support_x64 子系统 | 11 个 | 目录实体 |
| 独立安装器 | 4 个（驱动/Java/URL协议/凭据提供程序） | *Installer.exe |
| winro.pyb 魔数 | `E8 E8 C7 C7`（自定义加密容器，未解析） | 文件头 32 字节 |
| recording.db3 表 | 1（recording_data） | schema |
| tasks.db3 表 | 2（tasks + log_table_index） | schema |
| user.db3 表 | 6（development_apps / developmentsync_apps_v2 / subscribed_apps / magicblock_chathistory_web / metadata / sqlite_sequence） | schema |

---

## 8. 对 WT 的增量借鉴（在卷三 17 模式之上追加 6 条）

| # | 借鉴点 | 影刃证据 | WT 落点建议 |
|---|---|---|---|
| 18 | **UIED 式截图全元素检测** | cv-engine/xbot_cv.py（compo_detection 参数组） | 控件采集器增强：对任意窗口截图跑一次无监督元素检测，自动生成控件候选框（OpenCV 纯算法可实现：梯度+连通域+文本行合并），人工确认后入库——比现在逐个悬停捕获快一个量级 |
| 19 | **控件角色词表** | SelectorHelper 88 角色 | control_maps 条目加 `role` 字段；采集器按规则自动分类（Button/TextBox/ComboBox/Container…） |
| 20 | **会话状态感知** | wts.py WinStationIsLockedAndRemoted | 运行前置检查：isRemoted/isLocked 告警；ctypes 可直接移植（源码含出处注释） |
| 21 | **按任务分日志表** | tasks.db3 log_table_index | wt_task_queue 已有任务台账；日志查询支持"按任务归档"视图 |
| 22 | **录像台账** | recording.db3 recording_data | 若做 P2-3 录屏，同款表结构（成败/错误时间/路径/保留策略/上传标记）一步到位 |
| 23 | **高风险子系统独立安装器** | driver/java/credential/console 四个 Installer | WT 未来若引入驱动级输入或系统级集成，走"独立安装器+显式启用"，不塞进主安装 |

> 风险/合规备注：凭据提供程序（模式 19/credential）与未公开 API（winsta.dll）在 WT 的内网自用场景**不建议照搬**——前者需要数字签名与系统级安装，后者属未文档化接口。此处仅作机制记录。UIED 本身是 MIT 开源（Microsoft Research），直接采用无合规问题。

---

## 9. 本卷证据清单（增量）

| 证据 | 位置 |
|---|---|
| JSON-RPC 信封 / GenerateAnchor RPC / AiElement* / AgentToolRunner | `ShadowBot.Components.Protocol.dll` #Strings |
| 197 指令处理器 | `ShadowBot.Runtime.Development.dll` #Strings |
| 31 面板域 / 20,735 标识符 | `ShadowBot.Shell.Development.dll` #Strings |
| 88 角色词表 | `ShadowBot.UIAutomation.Provider` 标识符（SelectorHelper_*） |
| CV 引擎=UIED | `support_x64\cv-engine\xbot_cv.py`（全文） |
| 会话检测实现 | `resources\Code-Activity\Zh-CN\utils\os\wts.py`（全文） |
| 采样/进程枚举/管道 | `utils\process\{trace,process_helper}.py`、`pipe\*` |
| AI 引擎抽象 | `xbot\ai\aibase.py`（前 120 行 + 接口全录） |
| 数据模型 | `%LOCALAPPDATA%\ShadowBot\recording.db3`、`users\*\tasks.db3`、`user.db3`（schema 导出） |
| 子系统实体 | `support_x64\{driver,cv-engine,credential,Hlay,java,console,Shadowbot.Bridge}`、`Native\x64`（目录清单） |
| pyb 魔数 | `python310\winro.pyb` 头 32 字节 |

> 调研临时产物增量：`%TEMP%\sb_r3_{protocol,tools,shelldev,common2,runtime}.txt`（各 DLL 标识符全量）、`sb_sqlite_schemas.txt`（三库表结构）。
