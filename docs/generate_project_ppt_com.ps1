$ErrorActionPreference = "Stop"

$baseDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$outputPpt = Join-Path $baseDir "WT_Automation_项目讲解材料_更新版.pptx"
$outputMd = Join-Path $baseDir "WT_Automation_项目讲解材料_讲稿.md"

function Get-VersionedPath {
    param([string]$Path)
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $dir = Split-Path -Parent $Path
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $ext = [System.IO.Path]::GetExtension($Path)
    return (Join-Path $dir "${name}_${stamp}${ext}")
}

function Save-PresentationSafe {
    param($Presentation, [string]$Path)
    try {
        $Presentation.SaveAs($Path)
        return $Path
    } catch {
        $fallback = Get-VersionedPath $Path
        $Presentation.SaveAs($fallback)
        return $fallback
    }
}

function Write-TextSafe {
    param([string]$Path, [string]$Content)
    try {
        Set-Content -Path $Path -Value $Content -Encoding UTF8
        return $Path
    } catch {
        $fallback = Get-VersionedPath $Path
        Set-Content -Path $fallback -Value $Content -Encoding UTF8
        return $fallback
    }
}

function Set-TextStyle {
    param(
        $TextRange,
        [int]$Size = 18,
        [string]$FontName = "Microsoft YaHei",
        [bool]$Bold = $false,
        [int]$Color = 0x202020
    )
    $TextRange.Font.Name = $FontName
    $TextRange.Font.Size = $Size
    $TextRange.Font.Bold = [int]$Bold
    $TextRange.Font.Color.RGB = $Color
}

function Add-TitleBlock {
    param($Slide, [string]$Title, [string]$Subtitle)
    $shape = $Slide.Shapes.AddTextbox(1, 20, 15, 920, 70)
    $shape.TextFrame.TextRange.Text = $Title
    Set-TextStyle -TextRange $shape.TextFrame.TextRange -Size 26 -Bold $true -Color 0x28231F

    $sub = $Slide.Shapes.AddTextbox(1, 20, 70, 900, 26)
    $sub.TextFrame.TextRange.Text = $Subtitle
    Set-TextStyle -TextRange $sub.TextFrame.TextRange -Size 11 -Color 0x5A5959
}

function Add-Card {
    param(
        $Slide,
        [int]$Left,
        [int]$Top,
        [int]$Width,
        [int]$Height,
        [string]$Title,
        [string[]]$Bullets,
        [int]$TitleColor = 0xEB542F
    )
    $shape = $Slide.Shapes.AddShape(5, $Left, $Top, $Width, $Height)
    $shape.Fill.ForeColor.RGB = 0xFFFFFF
    $shape.Line.ForeColor.RGB = 0xE6E0DC

    $titleShape = $Slide.Shapes.AddTextbox(1, $Left + 12, $Top + 10, $Width - 24, 24)
    $titleShape.TextFrame.TextRange.Text = $Title
    Set-TextStyle -TextRange $titleShape.TextFrame.TextRange -Size 15 -Bold $true -Color $TitleColor

    $contentShape = $Slide.Shapes.AddTextbox(1, $Left + 14, $Top + 40, $Width - 28, $Height - 48)
    $contentShape.TextFrame.WordWrap = -1
    $text = [string]::Join("`r`n", ($Bullets | ForEach-Object { "• $_" }))
    $contentShape.TextFrame.TextRange.Text = $text
    Set-TextStyle -TextRange $contentShape.TextFrame.TextRange -Size 10 -Color 0x28231F
    $contentShape.TextFrame.TextRange.ParagraphFormat.SpaceAfter = 3
}

function Add-Kpi {
    param($Slide, [int]$Left, [int]$Top, [int]$Width, [string]$Title, [string]$Value, [int]$Fill)
    $shape = $Slide.Shapes.AddShape(5, $Left, $Top, $Width, 52)
    $shape.Fill.ForeColor.RGB = $Fill
    $shape.Line.ForeColor.RGB = $Fill
    $shape.TextFrame.TextRange.Text = "$Value`r`n$Title"
    Set-TextStyle -TextRange $shape.TextFrame.TextRange -Size 18 -Bold $true -Color 0xFFFFFF
    $shape.TextFrame.TextRange.Paragraphs(2).Font.Size = 9
    $shape.TextFrame.TextRange.ParagraphFormat.Alignment = 2
}

function Add-Chip {
    param($Slide, [int]$Left, [int]$Top, [int]$Width, [string]$Text, [int]$Fill)
    $shape = $Slide.Shapes.AddShape(5, $Left, $Top, $Width, 22)
    $shape.Fill.ForeColor.RGB = $Fill
    $shape.Line.ForeColor.RGB = $Fill
    $shape.TextFrame.TextRange.Text = $Text
    Set-TextStyle -TextRange $shape.TextFrame.TextRange -Size 9 -Bold $true -Color 0xFFFFFF
    $shape.TextFrame.TextRange.ParagraphFormat.Alignment = 2
}

$notes = @(
    "# WT_Automation 项目讲解讲稿",
    "",
    "## 第1页 项目背景与目标",
    "- 这一页先把项目定位讲清楚：WT_Automation 不是单一脚本，而是面向 WT 仿真软件的桌面自动化平台。",
    "- 之所以要做这个项目，是因为 WT 界面流程长、参数录入多，而且混合了 WPF 和 Win32 两类窗口，人工操作重复度高、出错率也高。",
    "- 项目目标不是简单追求 AI 全自动，而是建设稳定、可维护、可复用的执行体系。",
    "- 当前已经从“先跑通”走到了“可配置、可兜底、可回溯、可迭代”的阶段。",
    "",
    "## 第2页 技术路线与关键机制",
    "- 项目最早尝试过全流程 AI，但很快发现成本高、速度慢，而且重复步骤会被反复识别。",
    "- 现在的核心路线是：结构化动作主执行，复杂场景用相对区域和模板补偿，实在恢复不了再由 AI 介入。",
    "- 这里有两个关键点要强调：第一，复杂 WPF 场景通过父窗口相对区域解决弱控件定位问题；第二，AI 只处理当前卡住步骤，而不是从头重跑。",
    "- 另外，首次点击如果导致前台窗口切换，系统会重选更具体的参考窗口并补点，这对弹窗场景很关键。",
    "",
    "## 第3页 总体架构与项目分层",
    "- 架构上，Robot Framework 负责总调度，Python 负责执行编排和能力集成。",
    "- 执行层里有 `wt_flow_executor.py` 和 `wt_flow_locator.py`，分别负责动作执行和控件定位。",
    "- 定义层有 `flow_definition.json`、`flow_packages/`、流程编辑器和录制转换器，支撑流程维护。",
    "- 资产层有 `control_maps/` 和 `image_templates/`，支撑控件复用和模板兜底。",
    "- 维护层则通过总控台、运行报告、调试截图和最小回归测试构成闭环。",
    "",
    "## 第4页 关键能力与使用入口",
    "- 日常运行入口是 `WT_Launcher` 总控台，可以运行流程、看日志、看步骤结果和打开工具。",
    "- 当结构化定位不稳定时，可以用模板采集器补模板，也可以让控件信息自动沉淀到控件库。",
    "- 当要新增或维护流程时，进入 `WT_Flow_Editor`，通过模板化步骤、控件搜索和相对区域配置快速编辑。",
    "- 这一页要重点说明：项目已经不靠纯代码手改，而是逐步形成了面向运行、维护和调试的可视化工具链。",
    "",
    "## 第5页 阶段成果、价值与下一步",
    "- 当前项目已经具备工程化雏形，主链路、编辑器、模板采集器、运行报告和回归测试都已经落地。",
    "- 对业务而言，它降低了人工操作成本；对技术而言，它比纯录制脚本更稳，比纯 AI 执行更可控。",
    "- 下一步重点是把流程编辑器和实际执行进一步联通，并补齐更多尾部链路的稳定性验证。",
    "- 同时也要持续优化复杂控件的命中率，并沉淀更多实验数据，让项目既能跑、也能讲、还能写进论文。"
) -join "`r`n"

$ppt = New-Object -ComObject PowerPoint.Application
$ppt.Visible = -1
$presentation = $ppt.Presentations.Add()
$presentation.PageSetup.SlideWidth = 960
$presentation.PageSetup.SlideHeight = 540

# Slide 1
$slide = $presentation.Slides.Add(1, 12)
Add-TitleBlock $slide "WT_Automation 项目讲解" "面向 WT 仿真软件的工程化桌面自动化平台 | 5 页版"
Add-Chip $slide 36 84 84 "项目目标" 0xEB542F
Add-Kpi $slide 36 110 120 "页数控制" "5 页内" 0xEB542F
Add-Kpi $slide 170 110 120 "执行策略" "四层能力" 0xF09018
Add-Kpi $slide 304 110 120 "当前定位" "平台化建设" 0x3C8E38
Add-Card $slide 36 180 404 300 "项目背景与痛点" @(
    "WT 仿真软件界面链路长、窗口类型混合、人工录入参数多，重复劳动明显。",
    "复杂 WPF 弹窗、空标题容器、自绘控件和 Win32 对话框并存，导致单一定位方法不稳定。",
    "纯 AI 全流程可行但成本高、速度慢，对重复步骤存在反复判断和状态回退问题。",
    "项目目标不是一次性脚本，而是建设可维护、可复用、可扩展的自动化平台。",
    "当前已从“流程跑通”升级为“流程定义、控件资产、兜底恢复、结果回溯”闭环建设。"
) 0xEB542F
Add-Card $slide 468 180 456 300 "本阶段建设目标" @(
    "以 Robot Framework 为调度入口，以 Python 为执行核心，建立配置化的 WT 自动化主链路。",
    "把固定步骤沉淀为结构化 action/flow_ref，而不是继续堆积零散录制脚本。",
    "形成“结构化定位 + 相对区域交互 + 模板匹配 + AI 介入”的多层执行能力。",
    "建设总控台、流程编辑器、控件库和模板库，降低后续流程维护成本。",
    "通过运行报告、调试截图和最小回归测试提高问题定位效率与交付稳定性。",
    "将项目逐步沉淀为可复用的桌面自动化解决方案，而非单一业务 Demo。"
) 0xF09018

# Slide 2
$slide = $presentation.Slides.Add(2, 12)
Add-TitleBlock $slide "技术路线与关键机制" "从全流程 AI 试探转向以结构化执行为主、分层兜底为辅的工程化路线"
Add-Card $slide 36 110 276 155 "路线演进" @(
    "阶段1：全流程 AI，灵活但成本高、速度慢。",
    "阶段2：结构化动作主执行，承担稳定步骤。",
    "阶段3：模板匹配和相对区域补偿，处理复杂界面。",
    "阶段4：AI 只在必要时介入，承担当前卡住步骤的恢复。"
) 0xEB542F
Add-Card $slide 334 110 276 155 "路线调整原因" @(
    "固定步骤由结构化动作执行，速度更快、可解释性更强。",
    "WPF 弹窗、自绘按钮、离屏文本层等场景难以靠纯控件定位稳定命中。",
    "AI 不再从头操作全流程，而是仅处理当前卡住步骤，降低 token 成本和重复执行风险。"
) 0xF09018
Add-Card $slide 632 110 292 155 "关键机制" @(
    "控件定位采用多属性评分：综合 name、automation_id、class_name、framework_id 等信息筛选候选。",
    "复杂窗口优先锁定父窗口，再按相对区域换算点击/输入坐标，提升弱控件场景适应性。",
    "首次点击导致前台窗口切换时，重选更具体的参考窗口并补点，修正坐标偏移。"
) 0x3C8E38
Add-Card $slide 36 290 888 180 "分层恢复链路" @(
    "主路径：结构化控件定位与 action 执行。",
    "补偿层：结构化定位失败后，先使用模板匹配恢复局部动作。",
    "AI 层：模板也失败时，结合 resume_stage、fallback_stage、最近步骤结果和日志，仅处理当前受阻环节。",
    "工程价值：既保留主路径的稳定与可解释性，又让复杂界面具备恢复能力。"
) 0x1880FF
Add-Chip $slide 36 486 170 "亮点：AI 从重执行改为续跑" 0x3C8E38
Add-Chip $slide 224 486 210 "亮点：相对区域解决复杂 WPF 场景" 0x1880FF
Add-Chip $slide 456 486 250 "亮点：模板与 AI 构成双兜底链路" 0xEB542F

# Slide 3
$slide = $presentation.Slides.Add(3, 12)
Add-TitleBlock $slide "总体架构与项目分层" "主调度、流程定义、执行定位、资产沉淀与结果回溯已经形成完整链路"
Add-Kpi $slide 36 82 100 "调度入口" "Robot" 0xEB542F
Add-Kpi $slide 146 82 100 "执行核心" "Python" 0x1880FF
Add-Kpi $slide 256 82 100 "定义层" "JSON/Excel" 0xF09018
Add-Kpi $slide 366 82 100 "智能介入" "UI-TARS" 0x3C8E38
Add-Kpi $slide 476 82 120 "维护入口" "WT_Launcher" 0xEB542F
Add-Kpi $slide 608 82 150 "定义入口" "WT_Flow_Editor" 0x1880FF
Add-Card $slide 36 160 280 300 "核心执行层" @(
    "主入口 WT_AUT_recorded.py 负责读取配置、组装运行参数并调度流程。",
    "wt_flow_executor.py 统一解释 click、type_text、click_relative_region 等动作。",
    "wt_flow_locator.py 负责窗口筛选、控件评分、参考窗口判断与补点。",
    "业务辅助模块承接投影、窗口激活、文件对话框、业务步骤等专用逻辑。"
) 0xEB542F
Add-Card $slide 340 160 270 300 "定义与资产层" @(
    "flow_definition.json 与 flow_packages/ 负责流程编排和步骤组织。",
    "control_maps/ 按窗口和框架沉淀控件资产，支持从编辑器回写与复用。",
    "image_templates/ 保存模板图片，用于特殊按钮、图标和弹层的视觉匹配。",
    "录制结果可通过转换器提升为正式流程定义，并提取运行参数占位。"
) 0x1880FF
Add-Card $slide 634 160 290 300 "维护与质量层" @(
    "WT_Launcher 提供总控台，支持运行、日志查看、模型配置和工具入口整合。",
    "WT_Flow_Editor 提供步骤模板、新增相对区域、控件搜索和控件库导入。",
    "wt_run_reporting.py 输出结构化运行报告，记录步骤结果、耗时、fallback 与失败原因。",
    "tests/ 中已建立最小回归测试，覆盖执行器、编辑器工具、运行报告和转换器。"
) 0x3C8E38

# Slide 4
$slide = $presentation.Slides.Add(4, 12)
Add-TitleBlock $slide "关键能力与使用入口" "围绕日常运行、异常恢复、流程维护和资产沉淀提供可视化支撑"
Add-Chip $slide 36 82 240 "使用对象：运行人员 / 调试人员 / 维护人员" 0xEB542F
Add-Chip $slide 292 82 180 "使用方式：总控台统一进入" 0x1880FF
Add-Card $slide 36 112 280 150 "1. WT_Launcher 总控台" @(
    "用于启动/停止主流程，实时查看日志、步骤状态和结构化运行报告。",
    "支持模型配置持久化、最近历史管理、环境检查和常用工具统一入口。",
    "运行报告界面可快速查看每个步骤的成功/失败、耗时、执行策略和错误信息。"
) 0xEB542F
Add-Card $slide 36 280 280 180 "2. 模板采集与控件资产" @(
    "模板采集器用于制作按钮、图标和特殊弹层模板，补足结构化定位盲区。",
    "控件信息可自动沉淀到 control_maps/，按窗口标题和框架类型分类保存。",
    "模板库和控件库共同承担资产层角色，既服务执行，也服务后续编辑与复用。"
) 0xF09018
Add-Card $slide 336 112 300 348 "3. WT_Flow_Editor 流程编辑器" @(
    "支持步骤模板化新增，已预置按钮、输入框、下拉项和相对区域等高频模板。",
    "可维护 action 字段、控件信息、辅助判断、fallback 链路和运行参数占位。",
    "支持从 Inspect 文本解析、控件库导入、半自动采集和交互点击采集获取控件信息。",
    "新增父窗口相对区域动作配置，便于维护 WPF 弹窗、自绘输入框和难命中列表项。",
    "这是将零散脚本升级为可配置流程的关键入口，也是后续产品化基础。"
) 0x1880FF
Add-Card $slide 656 112 268 348 "4. 关键机制概览" @(
    "主路径：优先走结构化控件定位和 action 执行。",
    "复杂 WPF 场景：锁定父窗口后按相对区域换算真实点击/输入位置。",
    "首次点击导致窗口切换时：重选更具体的参考窗口并执行补点，修正坐标偏移。",
    "模板兜底：结构化定位失败后，用模板匹配恢复局部动作。",
    "AI 介入：模板也失败时，仅针对当前卡住步骤续跑，不重做全流程。"
) 0x3C8E38

# Slide 5
$slide = $presentation.Slides.Add(5, 12)
Add-TitleBlock $slide "阶段成果、价值与下一步" "项目已具备工程化雏形，后续重点转向能力联动、数据沉淀与稳定性深化"
Add-Kpi $slide 36 82 100 "路线状态" "已成型" 0x3C8E38
Add-Kpi $slide 146 82 100 "主控台" "已落地" 0xEB542F
Add-Kpi $slide 256 82 100 "流程编辑" "已可用" 0x1880FF
Add-Kpi $slide 366 82 100 "分层兜底" "已验证" 0xF09018
Add-Kpi $slide 476 82 100 "运行报告" "已接入" 0x3C8E38
Add-Kpi $slide 586 82 120 "后续重点" "平台联动" 0xEB542F
Add-Card $slide 36 150 280 300 "当前成果" @(
    "WT 主流程已能承接多类典型业务节点，技术路线已从试验阶段进入稳定打磨阶段。",
    "总控台、流程编辑器、模板采集器、运行报告和最小回归测试已形成配套工具链。",
    "流程定义、控件库和模板库开始持续沉淀，降低后续新链路接入和维护成本。",
    "项目已从单一脚本堆叠，发展为执行层、定义层、资产层、维护层协同的平台雏形。"
) 0xEB542F
Add-Card $slide 340 150 280 300 "项目价值" @(
    "相比纯人工操作，可显著减少重复录入和重复点击，提高业务执行效率。",
    "相比纯录制脚本，当前系统更适合处理 WPF/Win32 混合场景，具备更强可恢复能力。",
    "相比纯 AI 执行，现方案保留了稳定、低成本和可解释的主路径，更适合长期交付。",
    "结构化运行报告和步骤级问题回溯也为论文实验和后续考核提供了数据基础。"
) 0xF09018
Add-Card $slide 644 150 280 300 "下一步计划" @(
    "继续联通流程编辑器定义与实际执行，让配置修改更快回流到运行层。",
    "完善 AI 介入的实验结果沉淀，把模板兜底与 AI 续跑效果做量化对比。",
    "继续补齐覆盖区、格网、导出等尾部链路的稳定性验证和报告统计。",
    "优化复杂控件采集与定位，进一步提升特定菜单、小图标和树节点命中率。",
    "把项目沉淀为可迁移的桌面自动化方案模板，服务更多复杂 GUI 软件场景。"
) 0x3C8E38

$savedPpt = Save-PresentationSafe -Presentation $presentation -Path $outputPpt
$savedMd = Write-TextSafe -Path $outputMd -Content $notes

$presentation.Close()
$ppt.Quit()
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($presentation) | Out-Null
[System.Runtime.Interopservices.Marshal]::ReleaseComObject($ppt) | Out-Null
[GC]::Collect()
[GC]::WaitForPendingFinalizers()

Write-Output "generated_ppt=$savedPpt"
Write-Output "generated_md=$savedMd"
