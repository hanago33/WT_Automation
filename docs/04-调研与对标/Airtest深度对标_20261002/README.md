# Airtest（网易 1.4.3）深度对标与 WT_Automation 改进规划 · 导读

- **调研对象**：[AirtestProject/Airtest](https://github.com/AirtestProject/Airtest) 1.4.3 源码包（PyPI sdist，Apache-2.0，网易开源）
- **调研焦点**：Windows 桌面自动化中的**图像识别内核**（多尺度模板匹配/置信度/降级链）、设备输入层、等待与错误语义、执行证据链（日志与报告）
- **调研日期**：2026-10-02
- **调研方法**：下载 PyPI 源码包逐文件精读（非网页泛读），全部结论标注 `文件:行号` 证据；与 WT_Automation 现有实现逐项对照
- **性质**：纯调研与规划沉淀，**未修改 WT_Automation 任何功能代码**；文档内代码块仅供未来实施参考，落地时按 AGENTS.md 在 worktree 中重写并回归后提交审查
- **结论一句话**：Airtest 与 WT 同为「Python + OpenCV + Windows 桌面」技术栈，是全场竞品中**唯一可近乎直接移植**的参考实现；其图像识别内核的五个工程技巧（比例范围预测、搜索区裁剪、时间预算早退、灰度定位+彩色复验两段式置信度、结构化匹配结果）可直接修复 WT 目前「7 档盲扫 + 灰度单段置信度 + 参数写死」的全部痛点，预计 3 个阶段落地，不引入任何新依赖。

## 文档索引

| 文件 | 内容 |
|---|---|
| [01_Airtest项目全景与模块地图.md](01_Airtest项目全景与模块地图.md) | 版本/许可/生态定位；92 文件 16,995 行的模块地图与 LOC 统计；核心链路依赖关系；设计哲学五条 |
| [02_图像识别内核逐层拆解.md](02_图像识别内核逐层拆解.md) | **本套文档核心卷**：Template 描述符参数表、CVSTRATEGY 降级链调度、mstpl 多尺度匹配五技巧全解、cal_confidence 两个黑科技、tpl 单尺度、特征点链（KAZE/BRISK/ORB/SIFT）、Predictor 与分辨率适配、TargetPos 九宫格 |
| [03_设备层_输入通道_等待语义与执行证据链.md](03_设备层_输入通道_等待语义与执行证据链.md) | win.py 设备层（mss→GDI 双截图、虚拟键/扫描码双键盘通道、剪贴板锁重试、DPI 手工系数）；G 全局对象；loop_find 等待语义；错误分类学；logwrap 参数值审计 → JSONL → HTML 报告的「日志即报告」单一数据源；.air 脚本组织 |
| [04_WT逐项对照差距分析.md](04_WT逐项对照差距分析.md) | 21 维能力矩阵（✅🟡❌ 逐格对照）；WT 写死参数与调用点量化统计（15 处调用/4 处 0.8 写死/50 张模板）；性能复杂度模型对比；WT 已领先项；不建议照抄的 6 项论证 |
| [05_改进实施规划书.md](05_改进实施规划书.md) | **四阶段路线图（A 参数入库 → B 匹配内核 → C 等待与审计 → D 可选增强）**：每阶段目标/改动文件/Airtest 参照/参考代码/验收标准/工作量/风险与回滚 + 与影刃对标 P0 项的对应关系 + 测试计划 |
| [06_附录_参考实现代码与统计明细.md](06_附录_参考实现代码与统计明细.md) | 可直接落地的参考代码（两段式置信度移植、wt_image_matching 匹配内核、wait_template、templates_index.json schema 扩展、pytest 用例）；LOC 统计明细；证据索引（源文件:行号） |
| [07_卷七_API层_断言体系与运行时工程机制.md](07_卷七_API层_断言体系与运行时工程机制.md) | api.py 组合模式与 touch/wait/exists/assert **四态语义表**（操作/检查/断言三套超时与异常语义）；THRESHOLD_STRICT 断言独立阈值；exists 坐标复用；`retries` 指数退避装饰器（63 行）；MetaDevice 注册表 |
| [08_卷八_多后端协商_录屏线程模型与工程兼容模式.md](08_卷八_多后端协商_录屏线程模型与工程兼容模式.md) | **TouchProxy/ScreenProxy 自动协商三件套**（注册表+真实探测+万能兜底）——输入通道参数化的调度器蓝本；录屏双线程墙钟帧率对齐与错误帧兜底；deprecated property 平滑迁移手法 |
| [09_卷九_报告渲染管线全解.md](09_卷九_报告渲染管线全解.md) | LogToHtml：depth 重建调用树、**截图+匹配框+置信度叠加闭环**、traceback 即失败的判定契约、导出全相对路径；WT run report 可视化层的 4 条落地建议 |
| [10_卷十_实验验证与量化数据.md](10_卷十_实验验证与量化数据.md) | **本机实测**：同构异色下灰度单段满分误报（1.0）而 HSV 复验归零拒杀；预测管道提速 6.0x/细化 2.6x；三方中心偏差 ≤1px；对卷五验收标准的量化校准 |

## 最值得先看的十项发现（TL;DR）

1. **两段式置信度**：灰度图 `TM_CCOEFF_NORMED` 只负责"定位"，命中后从**原彩图**裁出目标区域缩放回模板尺寸，再用 HSV 三通道 `cal_rgb_confidence` **取三通道最小值复验**（`cal_confidence.py:27-50`）——专杀"形状相同颜色不同"的误报，这是 WT 目前最大的准确率风险点。
2. **比例范围预测**：多尺度搜索不是盲扫固定档位，而是用「模板捕获时分辨率 / 当前屏幕分辨率」的比值推导出缩放比上下限，步进 0.005 小步扫（`multiscale_template_matching.py:193-203`）——WT 的 `[1.0,1.1,0.9,1.2,0.8,1.35,0.7]` 是拍脑袋档位，DPI 漂移大时可能整段漏掉。
3. **搜索区裁剪**：按捕获时记录的控件区域预测目标位置，把屏幕裁剪到预测点周围（半径≥150px）再匹配（`multiscale_template_matching.py:213-227`）——匹配面积缩小一个数量级。**WT 的 `templates_index.json` 已经存了 `region` 和 `source_screenshot` 字段，原料齐全但从未用于匹配**。
4. **时间预算早退**：多尺度循环限时 1 秒，任一尺度分数达到阈值且复验通过立即返回（`:141-146`）；WT 现状是每轮轮询把 7 档全部扫完取"全局最优"，不仅慢，还可能在错误尺度上选中比正确尺度更高的分数。
5. **参数两级制**：全局 `ST.THRESHOLD=0.7` + 单模板 `Template(threshold=...)` 覆盖（`settings.py:19`、`cv.py:127-136`）；WT 是 4 处 `confidence=0.8` 写死、15 个调用点无法按模板调节。
6. **结构化匹配结果**：`{result 中心点, rectangle 四角坐标, confidence, time}`（`utils.py:29-34`）——置信度和耗时直接进运行报告；WT 只返回裸 `pyautogui.Point`，run report 无法回答"这次匹配有多准、用了哪种算法"。
7. **虚拟键/扫描码双键盘通道**：`keyevent`→pywinauto SendKeys（虚拟键）之外，还有 `key_press`→ctypes `SendInput` + `KEYEVENTF_SCANCODE` 扫描码直发，专治只认 DirectInput 扫描码的程序（`ctypesinput.py:180-257`）——WT 输入通道参数化（影刃对标 P0-1）的第二条实证路线。
8. **截图双后端**：默认 mss（快），`ScreenShotError` 时自动退回 GDI `BitBlt`（`win.py:172-181`、`screen.py:15-65`），且按窗口 rect 裁剪、双屏偏移修正、`focus_rect` 去边框——WT 用 pyautogui（PIL ImageGrab）全屏截图是整个图像链路里最慢的一环。
9. **失败也是证据**：`loop_find` 成功和失败都自动存带时间戳的截图（`try_log_screen`，`cv.py:86-112`），超时抛类型化 `TargetNotFoundError`；配合 `@logwrap` 把**函数调用参数值**记入 JSONL，一份 log.txt 同时是调试日志和 HTML 报告的数据源（`logwraper.py:56-80`）。
10. **中文路径 imread 与 WT 同解**：Airtest 在 PY3 下也用 `np.fromfile + cv2.imdecode`（`aircv.py:12-26`）——WT 现有方案与业界事实标准互相印证，无需改动。

## 对 WT 的最高优先级建议（详见卷五；实验校准见卷十）

| # | 建议 | 一句话理由 |
|---|---|---|
| A | 模板参数入库（threshold/rgb/scale/capture_resolution） | 复用 `templates_index.json` 既有结构，P0-4 的 Airtest 版答案 |
| B | 匹配内核升级：比例预测+区域裁剪+早退+**细化**+两段式置信度 | 实测提速 6.0x（细化默认开时 2.6x）、中心偏差 ≤1px、同构异色误报归零（卷十） |
| C | 统一等待语义 + intervalfunc 心跳钩子 + 类型化错误 + 失败自动截图 | 同时对接影刃对标 P0-3（重试升级）与 P0-5（防休眠心跳） |
| D（可选） | BRISK 特征点兜底 + mss 截图后端评估 | 观察前三阶段误报率数据后再决定 |

## 使用约定

- Airtest 侧一切事实均标注来源（`文件:行号`），源码包位于 `%TEMP%\airtest_src\airtest-1.4.3\`（临时目录，可重新下载：`pip download airtest==1.4.3 --no-deps`）；
- 卷五、卷六的"参考代码"是示意实现，**落地时必须**按 AGENTS.md 在 worktree 中重写并全量回归（`python -m pytest tests/ -q`）后提交审查；
- 许可提醒：Airtest 为 Apache-2.0，移植其代码段需在文件头保留出处与许可声明；`opencv-contrib-python` 的 SIFT/SURF 专利边界（cv2 ≥4.4 已免费）不影响本项目（WT 锁 opencv 版本自定）。
