from datetime import datetime
from pathlib import Path

import pythoncom
import win32com.client


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_PPT = BASE_DIR / "WT_Automation_项目讲解材料_更新版.pptx"
OUTPUT_MD = BASE_DIR / "WT_Automation_项目讲解材料_讲稿_更新版.md"

ppLayoutBlank = 12
msoShapeRoundedRectangle = 5
msoTextOrientationHorizontal = 1
ppAlignCenter = 2

COLOR_TEXT = 0x28231F
COLOR_MUTED = 0x5A5959
COLOR_ACCENT = 0xEB542F
COLOR_ACCENT2 = 0x1880FF
COLOR_OK = 0x3C8E38
COLOR_WARN = 0xF09018
COLOR_WHITE = 0xFFFFFF
COLOR_BORDER = 0xE6E0DC


def versioned_path(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}_{stamp}{path.suffix}")


def save_text(path: Path, content: str) -> Path:
    try:
        path.write_text(content, encoding="utf-8")
        return path
    except PermissionError:
        fallback = versioned_path(path)
        fallback.write_text(content, encoding="utf-8")
        return fallback


def set_range_style(text_range, size=18, color=COLOR_TEXT, bold=False, font_name="Microsoft YaHei"):
    text_range.Font.Name = font_name
    text_range.Font.Size = size
    text_range.Font.Bold = -1 if bold else 0
    text_range.Font.Color.RGB = color


def add_textbox(slide, left, top, width, height, text, size=18, color=COLOR_TEXT, bold=False, center=False):
    shape = slide.Shapes.AddTextbox(msoTextOrientationHorizontal, left, top, width, height)
    shape.TextFrame.TextRange.Text = text
    set_range_style(shape.TextFrame.TextRange, size=size, color=color, bold=bold)
    if center:
        shape.TextFrame.TextRange.ParagraphFormat.Alignment = ppAlignCenter
    return shape


def add_title_block(slide, title, subtitle):
    add_textbox(slide, 20, 15, 920, 36, title, size=26, color=COLOR_TEXT, bold=True)
    add_textbox(slide, 20, 52, 900, 18, subtitle, size=11, color=COLOR_MUTED)


def add_card(slide, left, top, width, height, title, bullets, title_color=COLOR_ACCENT):
    shape = slide.Shapes.AddShape(msoShapeRoundedRectangle, left, top, width, height)
    shape.Fill.ForeColor.RGB = COLOR_WHITE
    shape.Line.ForeColor.RGB = COLOR_BORDER

    add_textbox(slide, left + 12, top + 10, width - 24, 22, title, size=15, color=title_color, bold=True)
    content = "\r\n".join(f"• {item}" for item in bullets)
    body = add_textbox(slide, left + 14, top + 38, width - 28, height - 46, content, size=10, color=COLOR_TEXT)
    body.TextFrame.WordWrap = -1
    body.TextFrame.TextRange.ParagraphFormat.SpaceAfter = 3


def add_kpi(slide, left, top, width, title, value, fill_color):
    shape = slide.Shapes.AddShape(msoShapeRoundedRectangle, left, top, width, 52)
    shape.Fill.ForeColor.RGB = fill_color
    shape.Line.ForeColor.RGB = fill_color
    shape.TextFrame.TextRange.Text = f"{value}\r\n{title}"
    set_range_style(shape.TextFrame.TextRange, size=18, color=COLOR_WHITE, bold=True)
    shape.TextFrame.TextRange.Paragraphs(2).Font.Size = 9
    shape.TextFrame.TextRange.ParagraphFormat.Alignment = ppAlignCenter


def add_chip(slide, left, top, width, text, fill_color):
    shape = slide.Shapes.AddShape(msoShapeRoundedRectangle, left, top, width, 22)
    shape.Fill.ForeColor.RGB = fill_color
    shape.Line.ForeColor.RGB = fill_color
    shape.TextFrame.TextRange.Text = text
    set_range_style(shape.TextFrame.TextRange, size=9, color=COLOR_WHITE, bold=True)
    shape.TextFrame.TextRange.ParagraphFormat.Alignment = ppAlignCenter


def build_notes() -> str:
    return """# WT_Automation 项目讲解讲稿

## 第1页 项目背景与目标
- 这一页先把项目定位讲清楚：WT_Automation 不是单一脚本，而是面向 WT 仿真软件的桌面自动化平台。
- 之所以要做这个项目，是因为 WT 界面流程长、参数录入多，而且混合了 WPF 和 Win32 两类窗口，人工操作重复度高、出错率也高。
- 项目目标不是简单追求 AI 全自动，而是建设稳定、可维护、可复用的执行体系。
- 结合最新实验结果，当前 46 步“气象数据录入”流程已经完成 5 次完整运行，其中 1 次还是 Excel 导入变体和参数扰动实测。
- 所以这一页的落点要讲成：项目已经从“先跑通”走到了“可配置、可兜底、可回溯、可重复验证”的阶段。

## 第2页 技术路线与关键机制
- 项目最早尝试过全流程 AI，但很快发现成本高、速度慢，而且重复步骤会被反复识别。
- 现在的核心路线是：结构化动作主执行，复杂场景用相对区域和模板补偿，实在恢复不了再由 AI 介入。
- 这里有两个关键点要强调：第一，复杂 WPF 场景通过父窗口相对区域解决弱控件定位问题；第二，AI 只处理当前卡住步骤，而不是从头重跑。
- 另外，首次点击如果导致前台窗口切换，系统会重选更具体的参考窗口并补点，这对弹窗场景很关键。
- 最新完整流程结果也说明，这条路线已经从“依赖兜底保成功”逐步转向“主路径直接完成”，最近 5 次完整运行中有 4 次 fallback 为 0。

## 第3页 总体架构与项目分层
- 架构上，Robot Framework 负责总调度，Python 负责执行编排和能力集成。
- 执行层里有 `wt_flow_executor.py` 和 `wt_flow_locator.py`，分别负责动作执行和控件定位。
- 定义层有 `flow_definition.json`、`flow_packages/`、流程编辑器和录制转换器，支撑流程维护。
- 资产层有 `control_maps/` 和 `image_templates/`，支撑控件复用和模板兜底。
- 维护层则通过总控台、运行报告、调试截图和最小回归测试构成闭环。

## 第4页 关键能力与使用入口
- 日常运行入口是 `WT_Launcher` 总控台，可以运行流程、看日志、看步骤结果和打开工具。
- 当结构化定位不稳定时，可以用模板采集器补模板，也可以让控件信息自动沉淀到控件库。
- 当要新增或维护流程时，进入 `WT_Flow_Editor`，通过模板化步骤、控件搜索和相对区域配置快速编辑。
- 这一页要重点说明：项目已经不靠纯代码手改，而是逐步形成了面向运行、维护和调试的可视化工具链。

## 第5页 阶段成果、价值与下一步
- 当前项目已经具备工程化雏形，主链路、编辑器、模板采集器、运行报告和回归测试都已经落地。
- 最新实验口径要重点强调：46 步流程 5 次完整成功，标准参数 4 次运行平均耗时 119.597 秒、极差 1.137 秒。
- 对业务而言，它降低了人工操作成本；对技术而言，它比纯录制脚本更稳，比纯 AI 执行更可控。
- 下一步重点是把流程编辑器和实际执行进一步联通，并补齐更多尾部链路的稳定性验证。
- 同时也要持续优化复杂控件的命中率，并沉淀更多实验数据，让项目既能跑、也能讲、还能写进论文。
"""


def build_ppt():
    pythoncom.CoInitialize()
    app = win32com.client.Dispatch("PowerPoint.Application")
    app.Visible = True
    presentation = app.Presentations.Add()
    presentation.PageSetup.SlideWidth = 960
    presentation.PageSetup.SlideHeight = 540

    # Slide 1
    slide = presentation.Slides.Add(1, ppLayoutBlank)
    add_title_block(slide, "WT_Automation 项目讲解", "面向 WT 仿真软件的工程化桌面自动化平台 | 5 页版")
    add_chip(slide, 36, 84, 84, "项目目标", COLOR_ACCENT)
    add_kpi(slide, 36, 110, 120, "页数控制", "5 页内", COLOR_ACCENT)
    add_kpi(slide, 170, 110, 120, "核心流程", "46 步", COLOR_WARN)
    add_kpi(slide, 304, 110, 120, "完整验证", "5 次", COLOR_OK)
    add_kpi(slide, 438, 110, 150, "当前定位", "平台化建设", COLOR_ACCENT2)
    add_card(slide, 36, 180, 404, 300, "项目背景与痛点", [
        "WT 仿真软件界面链路长、窗口类型混合、人工录入参数多，重复劳动明显。",
        "复杂 WPF 弹窗、空标题容器、自绘控件和 Win32 对话框并存，导致单一定位方法不稳定。",
        "纯 AI 全流程可行但成本高、速度慢，对重复步骤存在反复判断和状态回退问题。",
        "项目目标不是一次性脚本，而是建设可维护、可复用、可扩展的自动化平台。",
        "当前已从“流程跑通”升级为“流程定义、控件资产、兜底恢复、结果回溯、重复验证”闭环建设。",
    ], title_color=COLOR_ACCENT)
    add_card(slide, 468, 180, 456, 300, "本阶段建设目标", [
        "以 Robot Framework 为调度入口，以 Python 为执行核心，建立配置化的 WT 自动化主链路。",
        "把固定步骤沉淀为结构化 action/flow_ref，而不是继续堆积零散录制脚本。",
        "形成“结构化定位 + 相对区域交互 + 模板匹配 + AI 介入”的多层执行能力。",
        "建设总控台、流程编辑器、控件库和模板库，降低后续流程维护成本。",
        "通过运行报告、调试截图和最小回归测试提高问题定位效率与交付稳定性。",
        "以 46 步流程 5 次完整成功为依据，将项目逐步沉淀为可复用的桌面自动化解决方案，而非单一业务 Demo。",
    ], title_color=COLOR_WARN)

    # Slide 2
    slide = presentation.Slides.Add(2, ppLayoutBlank)
    add_title_block(slide, "技术路线与关键机制", "从全流程 AI 试探转向以结构化执行为主、分层兜底为辅的工程化路线")
    add_card(slide, 36, 110, 276, 155, "路线演进", [
        "阶段1：全流程 AI，灵活但成本高、速度慢。",
        "阶段2：结构化动作主执行，承担稳定步骤。",
        "阶段3：模板匹配和相对区域补偿，处理复杂界面。",
        "阶段4：AI 只在必要时介入，承担当前卡住步骤的恢复。",
    ], title_color=COLOR_ACCENT)
    add_card(slide, 334, 110, 276, 155, "路线调整原因", [
        "固定步骤由结构化动作执行，速度更快、可解释性更强。",
        "WPF 弹窗、自绘按钮、离屏文本层等场景难以靠纯控件定位稳定命中。",
        "AI 不再从头操作全流程，而是仅处理当前卡住步骤，降低 token 成本和重复执行风险。",
    ], title_color=COLOR_WARN)
    add_card(slide, 632, 110, 292, 155, "关键机制", [
        "控件定位采用多属性评分：综合 name、automation_id、class_name、framework_id 等信息筛选候选。",
        "复杂窗口优先锁定父窗口，再按相对区域换算点击/输入坐标，提升弱控件场景适应性。",
        "首次点击导致前台窗口切换时，重选更具体的参考窗口并补点，修正坐标偏移。",
    ], title_color=COLOR_OK)
    add_card(slide, 36, 290, 888, 180, "分层恢复链路", [
        "主路径：结构化控件定位与 action 执行。",
        "补偿层：结构化定位失败后，先使用模板匹配恢复局部动作。",
        "AI 层：模板也失败时，结合 resume_stage、fallback_stage、最近步骤结果和日志，仅处理当前受阻环节。",
        "工程价值：既保留主路径的稳定与可解释性，又让复杂界面具备恢复能力；最新 5 次完整运行中有 4 次未触发 fallback。",
    ], title_color=COLOR_ACCENT2)
    add_chip(slide, 36, 486, 170, "亮点：AI 从重执行改为续跑", COLOR_OK)
    add_chip(slide, 224, 486, 210, "亮点：相对区域解决复杂 WPF 场景", COLOR_ACCENT2)
    add_chip(slide, 456, 486, 250, "亮点：模板与 AI 构成双兜底链路", COLOR_ACCENT)

    # Slide 3
    slide = presentation.Slides.Add(3, ppLayoutBlank)
    add_title_block(slide, "总体架构与项目分层", "主调度、流程定义、执行定位、资产沉淀与结果回溯已经形成完整链路")
    add_kpi(slide, 36, 82, 100, "调度入口", "Robot", COLOR_ACCENT)
    add_kpi(slide, 146, 82, 100, "执行核心", "Python", COLOR_ACCENT2)
    add_kpi(slide, 256, 82, 100, "定义层", "JSON/Excel", COLOR_WARN)
    add_kpi(slide, 366, 82, 100, "智能介入", "UI-TARS", COLOR_OK)
    add_kpi(slide, 476, 82, 120, "维护入口", "WT_Launcher", COLOR_ACCENT)
    add_kpi(slide, 608, 82, 150, "定义入口", "WT_Flow_Editor", COLOR_ACCENT2)
    add_card(slide, 36, 160, 280, 300, "核心执行层", [
        "主入口 WT_AUT_recorded.py 负责读取配置、组装运行参数并调度流程。",
        "wt_flow_executor.py 统一解释 click、type_text、click_relative_region 等动作。",
        "wt_flow_locator.py 负责窗口筛选、控件评分、参考窗口判断与补点。",
        "业务辅助模块承接投影、窗口激活、文件对话框、业务步骤等专用逻辑。",
    ], title_color=COLOR_ACCENT)
    add_card(slide, 340, 160, 270, 300, "定义与资产层", [
        "flow_definition.json 与 flow_packages/ 负责流程编排和步骤组织。",
        "control_maps/ 按窗口和框架沉淀控件资产，支持从编辑器回写与复用。",
        "image_templates/ 保存模板图片，用于特殊按钮、图标和弹层的视觉匹配。",
        "录制结果可通过转换器提升为正式流程定义，并提取运行参数占位。",
    ], title_color=COLOR_ACCENT2)
    add_card(slide, 634, 160, 290, 300, "维护与质量层", [
        "WT_Launcher 提供总控台，支持运行、日志查看、模型配置和工具入口整合。",
        "WT_Flow_Editor 提供步骤模板、新增相对区域、控件搜索和控件库导入。",
        "wt_run_reporting.py 输出结构化运行报告，记录步骤结果、耗时、fallback 与失败原因。",
        "tests/ 中已建立最小回归测试，覆盖执行器、编辑器工具、运行报告和转换器。",
    ], title_color=COLOR_OK)

    # Slide 4
    slide = presentation.Slides.Add(4, ppLayoutBlank)
    add_title_block(slide, "关键能力与使用入口", "围绕日常运行、异常恢复、流程维护和资产沉淀提供可视化支撑")
    add_chip(slide, 36, 82, 240, "使用对象：运行人员 / 调试人员 / 维护人员", COLOR_ACCENT)
    add_chip(slide, 292, 82, 180, "使用方式：总控台统一进入", COLOR_ACCENT2)
    add_card(slide, 36, 112, 280, 150, "1. WT_Launcher 总控台", [
        "用于启动/停止主流程，实时查看日志、步骤状态和结构化运行报告。",
        "支持模型配置持久化、最近历史管理、环境检查和常用工具统一入口。",
        "运行报告界面可快速查看每个步骤的成功/失败、耗时、执行策略和错误信息。",
    ], title_color=COLOR_ACCENT)
    add_card(slide, 36, 280, 280, 180, "2. 模板采集与控件资产", [
        "模板采集器用于制作按钮、图标和特殊弹层模板，补足结构化定位盲区。",
        "控件信息可自动沉淀到 control_maps/，按窗口标题和框架类型分类保存。",
        "模板库和控件库共同承担资产层角色，既服务执行，也服务后续编辑与复用。",
    ], title_color=COLOR_WARN)
    add_card(slide, 336, 112, 300, 348, "3. WT_Flow_Editor 流程编辑器", [
        "支持步骤模板化新增，已预置按钮、输入框、下拉项和相对区域等高频模板。",
        "可维护 action 字段、控件信息、辅助判断、fallback 链路和运行参数占位。",
        "支持从 Inspect 文本解析、控件库导入、半自动采集和交互点击采集获取控件信息。",
        "新增父窗口相对区域动作配置，便于维护 WPF 弹窗、自绘输入框和难命中列表项。",
        "这是将零散脚本升级为可配置流程的关键入口，也是后续产品化基础。",
    ], title_color=COLOR_ACCENT2)
    add_card(slide, 656, 112, 268, 348, "4. 关键机制概览", [
        "主路径：优先走结构化控件定位和 action 执行。",
        "复杂 WPF 场景：锁定父窗口后按相对区域换算真实点击/输入位置。",
        "首次点击导致窗口切换时：重选更具体的参考窗口并执行补点，修正坐标偏移。",
        "模板兜底：结构化定位失败后，用模板匹配恢复局部动作。",
        "AI 介入：模板也失败时，仅针对当前卡住步骤续跑，不重做全流程。",
    ], title_color=COLOR_OK)

    # Slide 5
    slide = presentation.Slides.Add(5, ppLayoutBlank)
    add_title_block(slide, "阶段成果、价值与下一步", "项目已具备工程化雏形，最新实验结果可直接支撑答辩汇报与论文表述")
    add_kpi(slide, 36, 82, 100, "完整流程", "5/5 成功", COLOR_OK)
    add_kpi(slide, 146, 82, 110, "标准均值", "119.597s", COLOR_ACCENT)
    add_kpi(slide, 268, 82, 100, "波动极差", "1.137s", COLOR_ACCENT2)
    add_kpi(slide, 378, 82, 100, "主路径", "4 次零兜底", COLOR_WARN)
    add_kpi(slide, 488, 82, 100, "运行报告", "已接入", COLOR_OK)
    add_kpi(slide, 598, 82, 120, "后续重点", "平台联动", COLOR_ACCENT)
    add_card(slide, 36, 150, 280, 300, "当前成果", [
        "“气象数据录入”46 步流程已完成 5 次完整成功运行，包含 1 次 Excel 导入变体与参数扰动实测。",
        "标准参数 4 次重复运行平均耗时 119.597 s，极差 1.137 s，说明系统已具备较好的重复性。",
        "总控台、流程编辑器、模板采集器、运行报告和最小回归测试已形成配套工具链。",
        "流程定义、控件库和模板库开始持续沉淀，降低后续新链路接入和维护成本。",
        "项目已从单一脚本堆叠，发展为执行层、定义层、资产层、维护层协同的平台雏形。",
    ], title_color=COLOR_ACCENT)
    add_card(slide, 340, 150, 280, 300, "项目价值", [
        "相比纯人工操作，可显著减少重复录入和重复点击，提高业务执行效率。",
        "相比纯录制脚本，当前系统更适合处理 WPF/Win32 混合场景，具备更强可恢复能力。",
        "相比纯 AI 执行，现方案保留了稳定、低成本和可解释的主路径，更适合长期交付。",
        "最新 5 次完整运行中有 4 次 fallback 为 0，说明系统已从“依赖兜底保成功”转向“主路径可独立完成”。",
        "结构化运行报告和步骤级问题回溯也为论文实验和后续考核提供了数据基础。",
    ], title_color=COLOR_WARN)
    add_card(slide, 644, 150, 280, 300, "下一步计划", [
        "继续联通流程编辑器定义与实际执行，让配置修改更快回流到运行层。",
        "完善 AI 介入的实验结果沉淀，把模板兜底与 AI 续跑效果做量化对比。",
        "继续补齐覆盖区、格网、导出等尾部链路的稳定性验证和报告统计。",
        "优化复杂控件采集与定位，进一步提升特定菜单、小图标和树节点命中率。",
        "把项目沉淀为可迁移的桌面自动化方案模板，服务更多复杂 GUI 软件场景。",
    ], title_color=COLOR_OK)

    try:
        presentation.SaveAs(str(OUTPUT_PPT))
        saved_ppt = OUTPUT_PPT
    except Exception:
        saved_ppt = versioned_path(OUTPUT_PPT)
        presentation.SaveAs(str(saved_ppt))

    presentation.Close()
    app.Quit()
    pythoncom.CoUninitialize()
    return saved_ppt


if __name__ == "__main__":
    saved_ppt = build_ppt()
    saved_md = save_text(OUTPUT_MD, build_notes())
    print(f"generated_ppt={saved_ppt}")
    print(f"generated_md={saved_md}")
