# 内网便携 Python 环境维护手册（3.11 版）

> 建立于 2026-09-10（Python 3.8 → 3.11 升级会话）。
> 适用场景：外网机（有网）制作/维护便携运行环境，内网机（离线）部署、切换与排障。
> 姊妹文档：`内网同步说明.md`（代码增量发布流程）、`release_out/发布历史.md`（每次推送记录）。

---

## 一、环境全景（谁在跑什么）

### 1.1 外网机（开发机）解释器地图

| 解释器 | 路径 | 说明 |
|---|---|---|
| Python 3.8.10（系统默认） | `D:\Program Files\Python38\python.exe` | 直接敲 `python` / `python -m pytest` 时用的就是它 |
| Python 3.11.9（`py -3.11`） | `C:\Users\14830\AppData\Local\Programs\Python\Python311\python.exe` | `启动WT自动化总控台.bat` 优先选用（`pyw -3.11`） |
| Python 2.7（ArcGIS 附带） | `C:\Python27\ArcGIS10.8\python.exe` | 与本项目无关 |
| **便携 3.11.9 基准环境** | `D:\wt_python` | 2026-09-10 由 `D:\wt_python311` 切换而来；内网同款环境 |
| 便携 3.8.10 旧环境 | `D:\wt_python_38_backup` | 切换前环境，保留可回滚 |

> **多版本并存互不影响**：每个解释器有独立目录与独立 `site-packages`，可同时运行；
> 唯一注意：给某环境装包要用全路径（`D:\wt_python\python.exe -m pip install xxx`），不要裸敲 `pip`。

### 1.2 内网机（离线）

```
切换前（现状）              切换后
D:\wt_python     3.8.10     D:\wt_python            3.11.9
                            D:\wt_python_38_backup  3.8.10（自动备份，可回滚）
```

### 1.3 关键认知：代码只有一份，环境决定版本

- 项目代码唯一副本在仓库根目录（外网 `D:\My_RF_Project\WT_Automation`，内网 `D:\WT_Automation`）；
  **环境目录只含"解释器 + 依赖包"，不含项目代码**。
- 所有启动脚本写死的是**路径** `D:\wt_python\python.exe`（不是版本号）；切换脚本通过**目录改名**
  让同一路径指向新环境，因此 7 个 bat 一行都不用改。
- 项目内所有子进程（自动化执行、流程编辑器、任务服务、监控）统一用 `sys.executable` 启动，
  自动继承父进程解释器 —— 不存在硬编码其他 Python 的地方。
- **回滚窗口 = 代码仍兼容 3.8 的窗口**：新代码一旦使用 3.9+ 语法，回滚需要连同代码一起退，
  所以切换稳定前建议继续按 3.8 底线写代码。

---

## 二、本次交付物（2026-09-10）

| 文件 | 大小 | 说明 |
|---|---|---|
| `D:\wt_python311_portable.zip` | 297.4 MB | 便携 3.11.9 环境（131 个包，含 `_内网升级说明/`） |
| `D:\wt_python311_portable_packages.txt` | — | 交付时的包清单快照（131 项），供日后比对差异 |
| `release_out\发布包_20260910_155623_990b873.zip` | 28.2 MB | 代码增量包（应用前需处理 `deleted.txt`，见 §7.3） |

发布时点 SHA256（传输后建议校验）：

```
wt_python311_portable.zip            DF0EB4118159492F7ED9AC31CE7B216583DF24102539C37DD87556AD07C77A97
发布包_20260910_155623_990b873.zip   827269DAA04E70E59DE9396A22F0A13707E750CBA218CCE3A6E2FF5568C9C02D
```

校验命令（两机通用，Windows 自带）：

```bat
certutil -hashfile wt_python311_portable.zip SHA256
```

便携包内 `_内网升级说明/` 目录：

| 文件 | 作用 |
|---|---|
| `切换到311.bat` | 一键切换（版本检查 → 自检 → 备份 → 改名就位 → 复核，失败自动回滚） |
| `回滚到38.bat` | 一键回滚到 3.8 |
| `_selftest.py` | 环境自检脚本（版本 / 依赖 / GUI / 项目启动链） |
| `说明.md` | 给内网机操作者看的简明步骤 |

---

## 三、内网部署与切换（三步）

1. **解压**：`wt_python311_portable.zip` → `D:\wt_python311`（解压后约 880 MB；
   连同旧环境备份，建议预留 2 GB 磁盘余量）。
2. **切换**：双击 `D:\wt_python311\_内网升级说明\切换到311.bat`。
   - 先关闭所有 WT 程序与命令行/资源管理器窗口（否则目录改名会因占用失败），建议**以管理员身份运行**；
   - 脚本自动完成：Windows 版本检查（< 8.1 拒绝执行）→ 新环境自检 → `D:\wt_python` 改名备份 →
     `D:\wt_python311` 改名就位 → 切换后复核；任一步失败**自动回滚**，不会留在半途。
3. **使用**：照常双击 `启动WT自动化总控台_内网.bat`（路径未变，无需改任何脚本）。

回滚：`D:\wt_python\_内网升级说明\回滚到38.bat`；确认稳定一段时间后可删除 `D:\wt_python_38_backup`（约 640 MB）。

> 切换脚本会先把自身复制到 `%TEMP%\wt_switch_311` 再执行 —— 这是为了避免"脚本占用即将改名的目录"。
> 若提示目录被占用：关掉所有 WT 程序、Python 进程与停留在该目录的窗口后重试。

---

## 四、验收基线

### 4.1 三级验证（日常排障也按这个顺序）

| 级别 | 方法 | 通过标准 |
|---|---|---|
| L1 环境自检 | `D:\wt_python\python.exe D:\wt_python\_内网升级说明\_selftest.py D:\WT_Automation 3.11` | `RESULT: PASS` |
| L2 项目测试 | `D:\wt_python\python.exe -m pytest tests/ -q` | 775 passed |
| L3 覆盖比对 | 与外网 `pip list --format=freeze` 逐包比对（见 §6.1） | 缺包数 = 0 |

### 4.2 关键版本基线（本次交付）

| 组件 | 版本 | 组件 | 版本 |
|---|---|---|---|
| Python | 3.11.9 | ttkbootstrap | 2.2.2 |
| pywinauto | 0.6.9 | pywinauto_recorder | 0.6.8 |
| pyautogui | 0.9.54 | pynput | 1.8.2 |
| opencv-python | 4.13.0.92 | numpy | 2.4.6 |
| pillow | 12.2.0 | openpyxl | 3.1.5 |
| requests | 2.34.2 | psutil | 7.2.2 |
| tkinterdnd2 | 0.6.2 | pandas | 3.0.5 |
| scipy | 1.17.1 | matplotlib | 3.11.1 |
| wxPython | 4.3.1 | robotframework | 7.4.2 |
| robotframework-ride | 2.2.4 | reportlab | 5.0.1 |
| sympy | 1.14.0 | pypdf | 6.18.0 |
| comtypes | 1.4.16 | pywin32 | 312 |

### 4.3 环境规模

- **131 个分发包**、21681 个文件、解压 879 MB、压缩包 297.4 MB；
- 与旧 3.8 环境（92 个包）逐包比对：**100% 覆盖、0 缺失**；
- 含少量随外网开发机带来的工具包（`graphifyy`、`tree-sitter*`、`aspose_slides`、`pdfplumber` 等），
  不影响运行；如需瘦身可另行卸载（卸载后请重跑 §5.6 验证）。

---

## 五、从零重做便携环境（外网机）

> 首选做法：直接 **fork 现有环境**（`robocopy D:\wt_python D:\wt_python311 /E`），保证基线一致。
> 以下为"纯净重建"流程，供换机器等场景使用。

### 5.1 复制基础解释器

```powershell
$src = "C:\Users\14830\AppData\Local\Programs\Python\Python311"
robocopy $src D:\wt_python311 /E /MT:16 /NFL /NDL /NJH /NJS /NP
# robocopy 退出码 < 8 视为成功
```

### 5.2 补齐依赖（联网）

外网 Python311 基础安装不含项目所需的部分包，需补装以下 **39 项**（版本以 §4.2 为准）：

```powershell
D:\wt_python311\python.exe -m pip install `
  psutil==7.2.2 tkinterdnd2==0.6.2 `
  chardet click contourpy cycler exceptiongroup fonttools formulas future `
  importlib-metadata importlib-resources isbinary kiwisolver markitdown==0.0.1a1 `
  matplotlib mpmath numexpr numpy-financial pandas pandastable pyparsing pypdf pypubsub `
  python-dateutil pytz regex reportlab robotframework-autobotlibrary robotframework-ride `
  schedula scipy sympy tomli tqdm tzdata wxpython xlrd zipp
```

离线变体（内网或无网环境）：

```powershell
# 第一步：在外网机生成 wheelhouse（务必带上 setuptools、wheel，否则 pandastable 源码构建会失败）
D:\wt_python311\python.exe -m pip download --disable-pip-version-check -d <wheelhouse> <上面同一清单> setuptools wheel
# 第二步：离线安装
D:\wt_python311\python.exe -m pip install --no-index --find-links <wheelhouse> <上面同一清单>
```

### 5.3 打包前清理

```powershell
# 1) 清 __pycache__ / .pyc（可省约 200 MB）
Get-ChildItem D:\wt_python311 -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
# 2) 清 pip 失败安装残留的 ~ 开头目录（会引发 "Ignoring invalid distribution" 告警）
Get-ChildItem D:\wt_python311\Lib\site-packages -Directory |
  Where-Object { $_.Name -match '^[~-]' } | Remove-Item -Recurse -Force
```

### 5.4 附带升级脚本（编码规范）

- `.bat` 必须保存为 **GBK + CRLF**（脚本内 `chcp 936`，GBK 才能正确显示中文）；
- `.py` / `.md` 用 **UTF-8 + CRLF**；
- 三个脚本（切换 / 回滚 / 自检）放入 `_内网升级说明/` 目录。

### 5.5 压缩

```powershell
D:\wt_python311\python.exe -c "import os,zipfile; src=r'D:\wt_python311'; out=r'D:\wt_python311_portable.zip'; z=zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=6); [z.write(os.path.join(r,f), os.path.relpath(os.path.join(r,f),src)) for r,ds,fs in os.walk(src) if '__pycache__' not in r for f in fs if not f.endswith('.pyc')]; z.close()"
```

### 5.6 出厂验证（六项，必须全过）

1. **条目数核对**：压缩包条目数 == 磁盘文件数（排除 `__pycache__`/`.pyc`）；
2. **CRC 全量校验**：`python -c "import zipfile; print(zipfile.ZipFile(r'D:\wt_python311_portable.zip').testzip())"` → 输出 `None` 即通过；
3. **解压副本自检**：解压到全新目录（如 `D:\wt_python311_verify`），运行 `_selftest.py` → `RESULT: PASS`；
4. **副本一致性**：解压副本 `pip list` 包数与源环境相同（当前 131）；
5. **全套测试**：`<环境>\python.exe -m pytest tests/ -q` → 775 passed；
6. **改名搬迁测试**：环境目录改名后 `python -c "import sys; print(sys.prefix)"` 自动跟随新路径、依赖导入正常。

---

## 六、依赖问题诊断手册

### 6.1 三段排查法："外网能跑、内网崩"

**第一段：确认是环境还是代码。** 内网机运行：

```bat
一键诊断启动问题.bat
:: 或（切换后路径；切换前把 D:\wt_python 换成 D:\wt_python311）
D:\wt_python\python.exe D:\wt_python\_内网升级说明\_selftest.py D:\WT_Automation 3.11
```

逐项 import，直接指出缺失/报错的模块。

**第二段：找出缺哪个包（环境差异比对）。** 用两边 `pip list` 相减：

```powershell
$base = & <外网基线python.exe> -m pip list --format=freeze
$site = & <目标环境python.exe> -m pip list --format=freeze
$b = $base | ForEach-Object { ($_ -split '==')[0].ToLower().Replace('_','-') }
$s = $site | ForEach-Object { ($_ -split '==')[0].ToLower().Replace('_','-') }
for ($i=0; $i -lt $base.Count; $i++) { if ($s -notcontains $b[$i]) { "缺失: " + $base[$i] } }
```

**第三段：确认代码是否用了新版本特性。** 三层手段：

| 层次 | 方法 | 抓什么 |
|---|---|---|
| 语法级 | `<目标python> -m compileall -q <仓库根>` | 3.9+ 语法（如新的模式匹配、类型参数语法） |
| API 级 | grep 关键词（见下） | 3.9+ 标准库新函数/新写法 |
| 运行时级 | `_selftest.py` 第 [4] 步（真实 import 启动链） | "注解在导入时求值"这类隐藏坑 |

3.9+ API grep 关键词（对 `*.py` 全文搜索）：

```
.removeprefix( | .removesuffix( | zoneinfo | tomllib | graphlib |
itertools.pairwise | functools.cache | ast.unparse | randbytes | math.lcm
```

### 6.2 已知误区（别再重复追查）

- **`ThreadingHTTPServer` 不是 3.9 新增**，3.7 起就有（3.8.10 实测可用）。
  2026-09-10 内网启动失败曾被误判为它导致，实际根因是发布包缺文件。
- 自写扫描器报 `invalid character in identifier` 未必是真错误：
  用 `utf-8` 编码读取带 BOM 的 `.py` 会引入 `\ufeff` 字符，误伤 `compile()`。
  **用 `utf-8-sig` 读取，或直接使用 `compileall`（真实解释器读文件，BOM 处理正确）**。

### 6.3 类型注解与 3.9+ 语法的判定细则

| 写法 | 3.8 是否安全 | 原因 |
|---|---|---|
| 函数签名注解 / 类级 / 模块级注解 `list[str]` | ❌ 不安全 | def/赋值时立即求值；除非文件头有 `from __future__ import annotations` |
| 函数体内**局部变量**注解 `x: list[str] = []` | ✅ 安全 | 局部注解不参与运行时求值 |
| `X \| Y` 类型联合（注解位置） | ❌ 不安全（同上）；✅ 在 `isinstance()` 里 | 3.10 起才支持运行时联合 |
| `ast.unparse` 调用 | ⚠️ 降级可用 | 仅 `flow_recorder_converter.py` 使用，带 `try/except`，3.8 下返回空串 |

本仓库当前策略：仅 `WT_AUTOMATION_Agent/` 系列使用 `from __future__ import annotations`；
其余模块保持 3.8 兼容写法。

### 6.4 ttkbootstrap / 界面相关版本红线

| 环境 | 可用版本 | 说明 |
|---|---|---|
| Python 3.8 | **≤ 1.17.0** | 2.x 要求 Python ≥ 3.10 |
| Python 3.11 | 2.x（当前 2.2.2） | 正常 |

实测：把 2.2.2 的包直接塞进 3.8 环境，`import ttkbootstrap` 即抛
`TypeError: 'ABCMeta' object is not subscriptable`（运行时泛型注解引起），**无解，只能降版本**。

另注：`wt_theme.py` 对 ttkbootstrap 缺失有完整降级（自动退回标准库 clam 样式），
缺包不会崩溃，但界面会退到老式灰色风格 —— 3.8 环境若缺它，观感变差即属此例。

### 6.5 部署/切换后启动无反应 → 先重启电脑

**现象**：刚完成 3.11 环境切换（或应用发布包）后，双击 `启动WT自动化总控台_内网.bat`
只闪一下命令行窗口就没了，之后无任何反应；重启电脑后恢复，无需改任何东西。

**已记录实例**：2026-09-10 内网机切换 3.11 后即为此现象，**重启后即正常**。

**原因**：切换过程包含"解压约 880 MB 新环境 + 两个目录改名"，会产生一批
Windows 层面的陈旧状态，重启即可全部清除，常见来源：

| 来源 | 说明 |
|---|---|
| 陈旧文件句柄 | 切换前残留的 WT 进程 / 停在旧目录的窗口，仍持有目录或文件句柄 |
| 待处理文件系统操作 | 大量文件刚落地即被改名，元数据尚未稳定 |
| 首轮安全扫描 | 杀软/Defender 对新环境做首次全量扫描，首次启动极慢，表现为"卡死" |
| 提权会话状态 | 早前失败尝试遗留的提权/UAC 状态异常 |

**处置顺序**（三步，全部为无副作用操作）：

1. **重启电脑**（首选，一步解决绝大多数情况）；
2. 仍不行 → 双击 `调试启动_显示错误.bat`（绕过 pythonw 与提权，直接在控制台显示完整错误）；
3. 有报错信息 → 按 §6.1 三段排查法定位；同时查看 `logs\launcher_headless.log`
   （启动 bat 会把 pythonw 的 stderr 追加到这个文件，静默崩溃的堆栈通常在里面）。

> **预防**：执行切换脚本前，先关闭所有 WT 程序、命令行窗口与停留在 `D:\wt_python*` 的
> 资源管理器窗口；切换完成后**重启一次**再投入使用，可避免此现象。

---

## 七、日常维护

### 7.1 新增第三方依赖

1. **外网机先装**（保持与内网基准一致）：`D:\wt_python\python.exe -m pip install <pkg>`；
2. 跑一次 `-m pytest tests/ -q` 确认无回归；
3. **内网补装（离线）**：传入对应 `.whl` 后
   `D:\wt_python\python.exe -m pip install --no-index --no-deps <pkg>.whl`；
   依赖关系复杂时，改用"重做便携包"（§5），避免版本漂移；
4. 若属项目正式依赖，同步更新 `requirements.txt`。

### 7.2 何时重做便携包

- 内网换新机器；
- 解释器大版本升级（如本次 3.8 → 3.11）；
- 依赖树变动较大 / `requirements.txt` 多项新增。

重做务必走完 §5 全流程并过 §5.6 六项验证；**不要把随手装过几个包的环境直接压缩发出**。

### 7.3 与代码发布包的关系（必读坑位）

- 发布包内 `deleted.txt` 是"要求内网删除的文件清单"，`deploy_release` / `apply_release` 会**真的执行删除**。
- 2026-09-10 的 `发布包_20260910_155623_990b873.zip` 中 `deleted.txt` 含 4 行会造成误删：
  `make_release.exe`、`deploy_release.exe`、`apply_release.exe`、`逐时功率计算模板.xlsx`。
  根因：三个 exe 已移出 git 跟踪，打包器将其视为"已删除文件"。
  **应用该包前先删掉这 4 行**（或应用后从外网机补拷）。
- 事故与包内容记录见 `release_out/发布历史.md`。

---

## 八、本次升级存档（2026-09-10）

- **触发排查**：内网点启动"只出现命令行无反应"。根因是**发布包基准错误、缺 46 个文件**
  （`WT_Launcher` 导入链断裂 → ImportError → pythonw 静默崩溃），**并非** Python 3.8 兼容问题
  （全仓库 225 个 `.py` 用 3.8 编译与导入实测全部通过）。
- **升级动机**：外网默认推荐 3.11、内网仅 3.8，版本漂移使新依赖无法在内网安装
  （如新版 ttkbootstrap 要求 ≥ 3.10），长期看属于结构性风险。
- **新环境来源**：复制外网 Python311（3.11.9）→ 补装 39 项对齐旧环境能力 → 打包。
- **验证数据**：775 passed（约 85~93 s）；自检 `RESULT: PASS`；整包 CRC 通过；
  条目数 21681 与磁盘一致；改名搬迁测试通过；解压副本与源环境逐文件清单零差异。
- **切换脚本实测**：在外网机完整走通"自检 → 备份 → 改名 → 复核"全流程。
- **外网机当前状态**：`D:\wt_python` 已是 3.11.9；旧环境在 `D:\wt_python_38_backup`。
- **内网机切换实录**：切换后首次启动出现"只闪命令行、无反应"，**重启电脑后恢复正常**，
  未改任何文件 —— 该现象的成因与处置已沉淀为 §6.5，并加入切换后使用建议。

---

## 九、相关文件索引

| 内容 | 位置 |
|---|---|
| 代码增量发布流程 | `内网同步说明.md` |
| 每次推送记录 / 事故说明 | `release_out/发布历史.md` |
| 环境自检脚本 | 便携包内 `_内网升级说明/_selftest.py` |
| 启动诊断脚本 | `诊断启动问题.py`、`一键诊断启动问题.bat` |
| 内网启动与运维脚本（7 个） | `启动WT自动化总控台_内网.bat`、`..._内网_无窗口.bat`、`start_queue_service.bat`、`check_queue_link.bat`、`服务器一键会话修复.bat`、`验证环境_内网.bat`、`调试启动_显示错误.bat` |
| 主题降级逻辑 | `wt_theme.py` |
| 兼容性判定依据（3.11 迁移时实测脚本） | 本手册 §6 |

---

## 附录 A：交付时包清单快照（131 项）

> 来源：`D:\wt_python311_portable_packages.txt`（交付时生成）。
> 日后排查"环境差异"时，用 §6.1 的命令与这份快照比对即可。
> 重新生成：`D:\wt_python\python.exe -m pip list --format=freeze > <输出文件>`

```text
altgraph==0.17.5
aspose_slides==26.6.0
cachetools==7.1.4
certifi==2026.7.22
cffi==2.1.0
chardet==4.0.0
charset-normalizer==3.4.9
click==8.5.0
colorama==0.4.6
comtypes==1.4.16
contourpy==1.3.3
cryptography==49.0.0
cycler==0.12.1
defusedxml==0.7.1
et_xmlfile==2.0.0
exceptiongroup==1.3.1
fonttools==4.64.0
formulas==1.3.4
future==1.0.0
graphifyy==0.9.31
idna==3.18
importlib_metadata==9.0.1
importlib_resources==7.1.0
iniconfig==2.3.0
isbinary==1.0.1
keyboard==0.13.5
kiwisolver==1.5.1
lxml==6.1.1
markitdown==0.0.1a1
matplotlib==3.11.1
mouse==0.7.1
MouseInfo==0.1.3
mpmath==1.3.0
msgpack==1.1.2
networkx==3.6.1
numexpr==2.14.2
numpy==2.4.6
numpy-financial==1.0.0
odfpy==1.4.1
opencv-python==4.13.0.92
openpyxl==3.1.5
overlay_arrows_and_more==0.5.1
packaging==26.2
pandas==3.0.5
pandastable==0.14.0
pdfminer.six==20260107
pdfplumber==0.11.10
pefile==2024.8.26
pillow==12.2.0
pip==26.1.2
pluggy==1.6.0
psutil==7.2.2
py-spy==0.4.2
PyAutoGUI==0.9.54
pycparser==3.0
PyGetWindow==0.0.9
Pygments==2.21.0
pyinstaller==6.21.0
pyinstaller-hooks-contrib==2026.6
PyMsgBox==2.0.1
pynput==1.8.2
pyparsing==3.3.2
pypdf==6.18.0
pypdfium2==5.12.1
pyperclip==1.11.0
Pypubsub==4.0.7
PyRect==0.2.0
PyScreeze==1.0.1
pytest==9.1.1
python-calamine==0.8.2
python-dateutil==2.9.0.post0
python-docx==1.2.0
python-pptx==1.0.2
pytweening==1.2.0
pytz==2026.3.post1
pywin32==312
pywin32-ctypes==0.2.3
pywinauto==0.6.9
pywinauto_recorder==0.6.8
pyxlsb==1.0.10
RapidFuzz==3.14.5
regex==2026.9.10
reportlab==5.0.1
requests==2.34.2
robotframework==7.4.2
robotframework-autobotlibrary==1.0.0
robotframework-ride==2.2.4
schedula==1.6.15
scipy==1.17.1
setuptools==65.5.0
signalrcore==1.0.2
six==1.17.0
sympy==1.14.0
thefuzz==0.22.1
tkinterdnd2==0.6.2
tomli==2.4.1
tqdm==4.70.0
tree-sitter==0.25.2
tree-sitter-bash==0.25.1
tree-sitter-c==0.24.2
tree-sitter-c-sharp==0.23.5
tree-sitter-cpp==0.23.4
tree-sitter-elixir==0.3.5
tree-sitter-fortran==0.6.0
tree-sitter-go==0.25.0
tree-sitter-groovy==0.1.2
tree-sitter-java==0.23.5
tree-sitter-javascript==0.25.0
tree-sitter-json==0.24.8
tree-sitter-julia==0.23.1
tree-sitter-kotlin==1.1.0
tree-sitter-lua==0.5.0
tree-sitter-objc==3.0.2
tree-sitter-php==0.24.1
tree-sitter-powershell==0.26.4
tree-sitter-python==0.25.0
tree-sitter-ruby==0.23.1
tree-sitter-rust==0.24.2
tree-sitter-scala==0.26.0
tree-sitter-swift==0.7.3
tree-sitter-typescript==0.23.2
tree-sitter-verilog==1.0.3
tree-sitter-zig==1.1.2
ttkbootstrap==2.2.2
typing_extensions==4.15.0
tzdata==2026.3
urllib3==2.7.0
wxPython==4.3.1
xlrd==2.0.2
xlsxwriter==3.2.9
zipp==4.1.0
```

---

*本手册由 2026-09-10 环境升级会话建立。后续环境变更请追加到 §8 存档并更新 §4 基线。*
