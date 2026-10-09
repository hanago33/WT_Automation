# 卷一 · Airtest 项目全景与模块地图

> 调研对象：Airtest 1.4.3（PyPI sdist，解包于 `%TEMP%\airtest_src\airtest-1.4.3\`）
> 所有行号均指该版本源码，可按卷六证据索引逐条复核。

## 1. 项目身份与生态定位

| 项目 | 事实 | 来源 |
|---|---|---|
| 名称 | Airtest，网易游戏开源 | setup.py / PyPI 元数据 |
| 版本 | 1.4.3（2026 年在售版本线，GitHub 9.6k★） | PyPI sdist |
| 许可 | Apache-2.0（可商用，移植需保留声明） | LICENSE |
| 平台 | Windows / Android / iOS / Linux 四端统一 API | core/{win,android,ios,linux} |
| 定位 | 游戏与 App 的图像识别自动化；跨平台 `touch/snapshot/swipe/text/exists/wait` 原语 | core/api.py（781 行用户 API） |
| 生态 | AirtestIDE（录制+可视化 IDE）、Poco（控件树自动化，移动端为主）、`airtest report`（HTML 报告） | 生态组件，不在本包内 |

**与 WT_Automation 的关系定位**：Airtest 的 Windows 端是「全屏图像匹配 + 前台输入」的纯视觉自动化，没有 UIA 控件定位、没有流程引擎、没有队列——这些恰是 WT 的强项；反过来 WT 的图像识别内核只是 pyautogui 上的薄封装（`wt_projection_helpers.py:261-354`），而这正是 Airtest 十年打磨的核心。**两者几乎是互补面，移植面清晰且无冲突。**

## 2. 量化统计

### 2.1 包规模

- 全包：**92 个 .py 文件，16,995 行**（`os.walk` 实测）
- 本调研精读的关键模块合计 **5,584 行**，明细见卷六统计表

### 2.2 与图像识别直接相关的代码量（移植评估基准）

| 模块 | 行数 | 作用 |
|---|---|---|
| aircv/multiscale_template_matching.py | 228 | mstpl/gmstpl 多尺度匹配 |
| aircv/template_matching.py | 119 | tpl 单尺度匹配（含 find_all） |
| aircv/cal_confidence.py | **50** | 两段式置信度计算（移植性价比之王） |
| aircv/keypoint_base.py | 291 | 特征点匹配基类（KAZE 系） |
| aircv/keypoint_matching.py | 53 | BRISK/AKAZE/ORB 免专利变体 |
| aircv/keypoint_matching_contrib.py | 137 | SIFT/SURF/BRIEF（需 contrib） |
| aircv/utils.py | 112 | generate_result/灰度转换/耗时装饰器 |
| aircv/aircv.py | 126 | imread(中文路径)/crop_image 公共 API |
| aircv/template.py、error.py、sift.py | 441 | Resolution 定义/算法错误/旧 SIFT |
| core/cv.py | 291 | **调度中枢**：loop_find + Template + CVSTRATEGY |
| core/settings.py | 28 | 全局参数 ST（28 行装下全部默认值） |
| **小计** | **~2,179** | WT 移植目标域 |

### 2.3 设备层与证据链相关

| 模块 | 行数 | 作用 |
|---|---|---|
| core/win/win.py | 739 | Windows 设备：截图/鼠标/键盘/剪贴板/录屏 |
| core/win/screen.py | 65 | GDI BitBlt 截图（mss 兜底） |
| core/win/ctypesinput.py | 256 | 扫描码 SendInput 键盘通道 |
| core/helper.py + utils/logwraper.py | 423 | G 全局对象 + 参数值审计 |
| report/report.py | 583 | JSONL → jinja2 HTML 报告 |
| utils/{resolution,transform,retry}.py | 159 | 分辨率策略/九宫格/重试 |

## 3. 模块地图（依赖方向：上依赖下）

```
用户 API 层    core/api.py（781 行：touch/exists/wait/assert_exists/sleep/...）
                │  全部经 @logwrap 审计、经 G.DEVICE 分发
调度中枢层    core/cv.py（loop_find / Template / CVSTRATEGY 循环 / Predictor）
                │  ST 全局参数（settings.py 28 行）
算法库层      aircv/
                ├─ template_matching.py        单尺度 tpl
                ├─ multiscale_template_matching.py  多尺度 mstpl/gmstpl（默认首选）
                ├─ cal_confidence.py           置信度（灰度/HSV 彩色）
                ├─ keypoint_*.py               特征点：KAZE/BRISK/AKAZE/ORB/SIFT/SURF/BRIEF
                └─ utils.py / aircv.py / error.py / template.py
设备层        core/device.py（抽象）→ core/win/{win,screen,ctypesinput}.py
                pywinauto(mouse/keyboard/Application) + mss + GDI + SendInput(scancode)
证据链        utils/logwraper.py（@logwrap 参数值→JSONL）→ report/report.py（→HTML）
                副产品：aircv/screen_recorder.py（273 行，ffmpeg 录屏）
```

**依赖清单（requirements.txt 原文）**：`Jinja2 / Pillow / requests / six / mss==6.1.0 / numpy<2.0 / opencv-contrib-python>=4.4.0.46,<=4.6.0.66 / facebook-wda / pywinauto==0.6.3 / filelock / ffmpeg-python / tidevice / psutil / packaging`。
WT 侧移植相关度：`opencv`（已有）、`numpy`（已有）、`mss`（可选新增，轻量单文件级依赖）；`pywinauto` WT 已在用。**移植图像内核（卷二范围）不需要任何新依赖。**

## 4. 设计哲学五条（WT 视角的精华）

1. **算法可插拔 + 首个成功即胜**：`CVSTRATEGY = ["mstpl","tpl","sift","brisk"]` 是一个普通列表，用户可在运行时改写 `ST.CVSTRATEGY`；调度器对每个算法的异常**吞掉并降级**（`cv.py:187-199`），单算法崩溃不影响整体出结果。→ WT 的 fallbackChain 思想在图像域的同构实现，两者可合并成一条链。
2. **参数两级制**：全局默认（ST）+ 单模板覆盖（Template 构造参数），简单、无锁、够用。28 行的 settings.py 是"最小可用全局配置"的范本。
3. **一次截图多算法复用**：`loop_find` 每轮截屏一次，`_cv_match` 内所有算法共用同一张 `screen` 与同一次灰度转换，I/O 成本摊薄。
4. **结构化结果 + 耗时内省**：所有算法返回统一 dict（`generate_result`），`@print_run_time` 装饰器把耗时写进结果 dict（`utils.py:15-26`）——性能数据是匹配结果的一部分，不是旁路日志。
5. **执行即日志、日志即报告**：`@logwrap` 把每次 API 调用的函数名+参数值+返回值/异常+耗时写成 JSONL（log.txt），HTML 报告只是这份数据的渲染视图（report.py）。**运行报告没有第二个数据源**， WT 的 `wt_run_reporting` 可对照检查是否有双轨。

## 5. 版本与边界声明

- 本调研对应 1.4.3 sdist；Airtest 活跃迭代中，若后续大版本重构 CVSTRATEGY/置信度算法，本套结论需重新校验。
- `KEYPOINT_MATCHING_PREDICTION` 设置项（`settings.py:18`）在全包范围内**无消费方**（grep 仅 settings.py 一处引用），`cv.py:207-227` 的 `_find_keypoint_result_in_predict_area` 属遗留路径——借鉴时不要把它当成有效机制。
- Android/iOS 侧（yosemite/minicap/minitouch 等 4,000+ 行）与 WT 无关，仅在其输入通道设计（touch_proxy 抽象）层面于卷三提及。
