# -*- coding: utf-8 -*-

import os
import zipfile
from datetime import datetime, timezone
from xml.sax.saxutils import escape


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "WT_Automation_论文初稿_规范化修订版_v6.docx")


def versioned_output_path(path):
    root, ext = os.path.splitext(path)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{root}_{stamp}{ext}"


def build_blocks():
    return [
        ("title", "面向 WT 仿真软件的桌面自动化系统设计与实现"),
        ("subtitle", "规范化修订版论文初稿"),
        ("normal", "说明：本文档在原学术增强版基础上，进一步重写了摘要、补充了英文摘要，统一了正文格式，并将项目中的录制脚本转换与最小回归测试能力纳入论文描述，以提升整体完整性和正式程度。"),
        ("h1", "中文摘要"),
        ("normal", "针对 WT 仿真软件在参数配置、文件导入、字段映射与弹窗交互过程中存在操作链路长、重复性高、人工执行效率低且容易出错的问题，本文面向 Windows 桌面环境设计并实现了一套 WT 软件自动化系统。本文研究目标是构建一套兼顾稳定性、可维护性和可追踪性的自动化方案，使其能够在复杂 WPF/Win32 混合界面中完成典型业务流程的自动执行。"),
        ("normal", "在方法上，本文采用“流程定义驱动 + 结构化控件定位 + 父窗口相对区域交互 + 模板与 AI 分层兜底 + 结构化运行报告”的总体技术路线。系统将业务步骤抽象为可配置动作，并围绕 automation_id、name、class_name、control_type、framework_id 等属性构建多属性评分定位方法；针对空标题 WPF 弹窗、离屏文本层和输入区域难以稳定命中的情况，引入父窗口相对区域换算与前台窗口切换后的参考窗口重选与补点机制；当结构化路径失效时，系统先通过模板匹配恢复局部动作，再在必要时调用 AI 代理仅处理当前卡住步骤。"),
        ("normal", "在系统实现方面，本文完成了流程定义文件、动作执行器、控件定位器、可视化流程编辑器、控件资产自动沉淀、录制结果转换、统一校验和结构化运行报告等模块设计，并构建了覆盖参数抽取、校验规则、编辑器工具与报告输出的最小回归测试集合。以“气象数据录入”46 步完整流程包为核心实验对象的结果表明，系统已完成 5 次完整流程验证，其中 4 次标准参数运行和 1 次 Excel 导入变体与参数扰动实测均实现 46 步全部成功；标准参数运行平均耗时为 119.597 s，极差仅为 1.137 s，且除最早一次外其余 4 次运行均未触发 fallback。"),
        ("normal", "综上，本文所设计的 WT 自动化系统在复杂桌面软件场景下具有较好的工程可落地性。相关实现不仅验证了多策略融合自动化方法的有效性，也表明以结构化执行为主、模板与 AI 兜底为辅的技术路线能够兼顾效率、稳定性与可维护性，并为后续扩展到更大规模的 CFD 与仿真前处理业务流程提供了可复用的技术基础。"),
        ("normal", "关键词：WT 仿真软件；桌面自动化；流程编排；控件定位；相对区域交互；模板与 AI 兜底"),
        ("h1", "Abstract"),
        ("normal", "To address the problems of long operation chains, high repetition, low manual efficiency, and frequent human errors in WT simulation software, this paper designs and implements a desktop automation system for WT under the Windows environment. The objective is to build a practical automation solution that balances stability, maintainability, and traceability, and can execute typical WT business workflows in complex hybrid WPF and Win32 interfaces."),
        ("normal", "The proposed system adopts a technical route combining workflow definition, structured control localization, parent-window-relative interaction, layered template-and-AI fallback, and structured run reporting. Business steps are abstracted into configurable actions. A multi-attribute scoring method is introduced to match controls using automation_id, name, class_name, control_type, and framework_id. For empty-title WPF popups, off-screen text nodes, and unstable input targets, a parent-window-relative region strategy and a reference-window reselection mechanism after foreground-window switching are further introduced. When structured localization fails, template matching is first used to recover the local action, and an AI agent is invoked only when necessary to handle the currently blocked step."),
        ("normal", "The implementation includes workflow definition files, an action executor, a control locator, a visual workflow editor, automatic control asset persistence, recorder-to-flow conversion, unified validation, structured run reporting, and a minimal regression test suite. Using the 46-step meteorological-data-entry workflow package as the core experimental object, the system completed five full-process runs, including four standard-parameter runs and one Excel-import variant with parameter perturbation, all with 46 successful steps. The standard runs achieved an average total time of 119.597 s with a range of only 1.137 s, and the latest four full runs completed without triggering fallback."),
        ("normal", "In conclusion, the proposed WT automation system demonstrates strong engineering applicability for complex desktop software. The work validates the effectiveness of a multi-strategy desktop automation scheme and shows that a technical route centered on structured execution, assisted by template and AI fallback, can balance efficiency, stability, and maintainability while providing a reusable basis for larger-scale CFD and simulation pre-processing workflows."),
        ("normal", "Key words: WT simulation software; desktop automation; workflow orchestration; control localization; relative-region interaction; template-and-AI fallback"),
        ("h1", "第1章 绪论"),
        ("h2", "1.1 研究背景"),
        ("normal", "CFD 与风资源评估相关软件通常具有界面复杂、参数项众多、交互层级深和人工操作耗时长等特点。在实际业务中，工程人员需要反复执行对象创建、参数录入、文件导入、字段映射、结果确认等大量重复操作，这不仅降低了工作效率，也会因人工误操作带来结果偏差。尤其是在 WT 这类带有 WPF 富界面、标准 Win32 文件对话框和复杂弹出层混合存在的软件中，简单脚本很难稳定覆盖全部交互路径。"),
        ("h2", "1.2 研究意义"),
        ("normal", "构建一套稳定的 WT 桌面自动化系统，能够显著缩短重复录入时间，降低操作差错率，并为后续批量参数配置、自动化仿真和流程标准化提供技术支撑。与一次性脚本相比，工程化自动化平台更强调流程配置能力、控件资产复用能力和运行结果可追踪能力，因此本研究不仅服务于当前 WT 场景，也可为同类仿真软件自动化提供可借鉴的实现路径。"),
        ("h2", "1.3 国内外研究现状"),
        ("normal", "现有桌面自动化研究大致可分为三类。第一类是录制回放型方法，此类方法实现简单、落地快，但对坐标、窗口位置和操作顺序高度敏感，在界面变化时容易失效。第二类是基于 UI 树和控件属性的结构化自动化方法，该类方法利用 AutomationId、Name、ClassName、ControlType 等属性实现确定性定位，具有可解释性强、复现成本低和易于调试等优势，是工程桌面自动化的重要技术路线。第三类是基于视觉识别、OCR 或大模型智能体的图形界面自动化方法，该类方法具有较强泛化能力，但在长链路重复任务中往往存在执行成本高、稳定性波动和状态不连续等问题。"),
        ("normal", "从应用侧看，国外研究更加关注通用 GUI 测试框架、跨应用桌面代理和多模态界面理解；国内相关研究则更多聚焦于 RPA、业务系统表单录入和工程流程自动化。然而，对于 CFD、风资源评估和仿真前处理软件这类界面深度定制、WPF 与 Win32 混合且弹窗复杂的软件，现有方法往往难以直接复用。其根本原因在于：一方面，这类软件包含自定义控件、空标题弹出层和难以直接操作的文本层；另一方面，单一结构化定位或单一视觉点击都无法兼顾稳定性、效率和维护成本。"),
        ("normal", "因此，围绕复杂桌面软件的自动化研究正在呈现融合趋势，即在结构化定位、视觉兜底、参数化流程、可视化维护和运行回溯之间建立协同关系。本文所研究的 WT 自动化系统正是在这一背景下形成，以结构化流程和可解释控件定位为主，以相对区域交互和模板兜底为辅，从而兼顾方法合理性与工程落地性。"),
        ("h2", "1.4 现有方案存在的问题"),
        ("normal", "传统录制回放方案高度依赖固定坐标和固定顺序，一旦窗口大小、分辨率或控件层级发生变化，就会出现点击偏移或误命中。纯 AI 桌面智能体虽然具备一定泛化能力，但在长链路重复操作中容易出现速度慢、成本高和状态续接不稳定等问题。对于 WT 这类需要长期稳定运行的业务场景而言，仅依赖某一种自动化机制难以兼顾准确性、鲁棒性和维护成本。"),
        ("h2", "1.5 本文研究内容"),
        ("normal", "本文围绕 WT_Automation 项目展开，主要研究内容包括：分析 WT 自动化业务流程并提取典型场景；设计由流程定义、动作执行器、控件定位器、相对区域交互和模板兜底组成的总体架构；实现可视化流程编辑器与控件库自动沉淀机制；构建结构化运行报告与统一校验机制；结合真实运行结果分析关键问题与修复策略。"),
        ("h2", "1.6 论文结构安排"),
        ("normal", "全文共分为七章。第一章介绍研究背景、研究现状和研究内容；第二章阐述相关技术与理论基础；第三章分析 WT 自动化业务需求；第四章给出系统总体设计；第五章介绍关键技术与实现方法；第六章对典型实验场景进行验证与分析；第七章总结全文并展望后续工作。"),
        ("h1", "第2章 相关技术与理论基础"),
        ("h2", "2.1 Windows 桌面自动化技术"),
        ("normal", "桌面自动化的核心任务是感知界面元素、定位目标控件并驱动交互操作。在 Windows 环境下，常见实现路径包括基于 UI Automation 的结构化定位、基于图像模板的视觉匹配以及基于键鼠事件的模拟操作。结构化定位具备可解释性强、执行稳定和结果可校验等优点，但在复杂 WPF 桥接对象、离屏文本或自绘控件场景下会受到限制；视觉匹配具有界面无侵入优势，但容易受缩放、主题和遮挡影响。因此工程实践中更适合使用多策略融合方案。"),
        ("h2", "2.2 pywinauto 与 pyautogui"),
        ("normal", "本项目中，pywinauto 主要承担基于控件树的结构化搜索、属性读取、焦点设置与控件输入，适合处理 automation_id、窗口标题、类名和控件类型等可获取属性明确的目标；pyautogui 主要承担模板命中后的鼠标点击、滚轮与拖拽等补充操作，用于兜底处理常规结构化定位难以直接完成的步骤。二者结合后，可以在稳定性和通用性之间取得较好的平衡。"),
        ("h2", "2.3 Robot Framework 调度机制"),
        ("normal", "系统使用 Robot Framework 作为外层调度入口，通过资源文件统一读取项目配置与环境变量，再调用 Python 主流程脚本执行业务逻辑。该设计便于将自动化执行纳入标准测试或批处理管线中，也方便在工程场景下保留明确的执行入口、日志输出与错误返回码。"),
        ("h2", "2.4 流程编排与配置化执行"),
        ("normal", "为了避免将业务逻辑硬编码在脚本中，系统将流程描述抽象为 JSON 配置，步骤可声明为脚本步骤、动作步骤或流程包引用步骤。动作步骤进一步细分为点击、双击、文本输入、父窗口区域点击、父窗口区域输入、发送按键和等待控件等通用动作，使执行层具备解释型工作方式。该配置化设计显著提高了流程的可维护性与复用性。"),
        ("h1", "第3章 WT 自动化业务需求分析"),
        ("h2", "3.1 典型业务流程"),
        ("normal", "结合项目中的 flow_definition.json 可以看出，系统已经覆盖了 WT 业务中的多个典型场景，包括风机类型切换、WT 微尺度模拟入口点击、地理信息数据入口点击、气象对象新建、测风塔名称与海拔录入、访问级别下拉选择、经纬度录入、时间序列文件导入以及导入窗口中的字段与通道选择等。这些流程包含按钮点击、文本输入、下拉框选择、标准文件对话框输入和复杂 WPF 弹窗交互等多种操作类型，具有较强代表性。"),
        ("h2", "3.2 功能需求"),
        ("normal", "系统应满足以下功能需求：支持以流程定义文件描述步骤、控件、参数和执行策略；支持基于控件树的稳定定位与动作执行；支持在无法直接命中控件时采用父窗口相对区域方式进行输入与点击；支持失败后的模板兜底；支持可视化维护流程、步骤模板和控件信息；支持记录执行结果、失败原因和步骤耗时。"),
        ("h2", "3.3 非功能需求"),
        ("normal", "除功能正确外，系统还需要具备稳定性、可维护性、可扩展性和可追踪性。稳定性要求系统面对 WPF 与 Win32 混合界面时仍能维持较高命中率；可维护性要求新增步骤可通过编辑器和模板快速构建；可扩展性要求动作类型与流程包可持续扩充；可追踪性要求每次执行均保留结构化结果，以支持问题复现和方案优化。"),
        ("h2", "3.4 关键难点分析"),
        ("normal", "项目调试记录表明，WT 自动化中至少存在三类突出难点。其一，WPF 下拉弹出层可能出现顶层窗口标题为空、内部文字控件离屏或文本层不可点击的情况，导致常规基于文本的定位失效。其二，标准 Win32 文件对话框中标签与输入框可能名称相同，如果缺少 class_name 约束，容易误命中静态文本。其三，相对区域点击后，前台窗口有可能切换到更具体的真实对话框，若仍沿用初始窗口矩形，则会产生坐标偏移。这些问题共同决定了系统必须采用多策略融合与动态校正的设计思路。"),
        ("h1", "第4章 系统总体设计"),
        ("h2", "4.1 总体架构"),
        ("normal", "WT_Automation 系统总体上可分为调度层、执行层、定位层、配置层、资产层和支撑层。调度层由 Robot Framework 与总控台组成，负责参数装配与流程触发；执行层由 Python 主流程和动作执行器组成，负责解释步骤并驱动具体动作；定位层由控件定位器与窗口辅助模块组成，负责窗口筛选、控件搜索、评分与缓存；配置层包含流程定义、流程包和运行时参数；资产层包括控件库与模板库；支撑层则由编辑器、校验器、运行报告、录制转换器与调试日志构成。"),
        ("caption", "图4-1 系统总体架构示意图"),
        ("diagram", "┌──────────────┐\n│ 调度层        │  Robot Framework / WT_Launcher\n└──────┬───────┘\n       │\n┌──────▼───────┐\n│ 执行层        │  WT_AUT_recorded / wt_flow_executor\n└──────┬───────┘\n       │\n┌──────▼───────┐     ┌────────────────┐\n│ 定位层        │<--->│ 资产层          │\n│ wt_flow_locator│    │ control_maps   │\n│ window helpers │    │ image_templates│\n└──────┬───────┘     └────────────────┘\n       │\n┌──────▼───────┐     ┌────────────────┐\n│ 配置层        │<--->│ 支撑层          │\n│ flow_definition│    │ editor/validate│\n│ flow_packages  │    │ run_report/log │\n└──────────────┘     └────────────────┘"),
        ("normal", "图4-1 展示了系统的总体架构关系。调度层作为统一入口向执行层传递运行参数；执行层在运行过程中调用定位层识别控件或窗口；定位层与资产层中的控件库和模板库交互，以提升搜索效率和兜底能力；配置层为执行层提供数据驱动的流程描述；支撑层则为整个系统提供流程维护、校验与结果回溯能力。"),
        ("h2", "4.2 主要模块划分"),
        ("normal", "WT_AUT_recorded.py 是系统核心运行入口，负责读取项目设置、加载流程定义、装配运行时参数并统一编排执行链路；wt_flow_executor.py 负责解析动作步骤与流程包引用，执行通用动作和模板兜底逻辑；wt_flow_locator.py 负责窗口搜索、控件评分、缓存与相对区域换算；WT_Flow_Editor.py 提供可视化流程编辑能力，支持步骤模板、字段编辑、导入导出和控件库同步；flow_recorder_converter.py 负责将 recorder 结果转换为可维护的流程定义并抽取运行参数占位；wt_projection_helpers.py 和 wt_window_helpers.py 提供业务辅助与窗口辅助能力；wt_flow_validation.py 与 wt_action_schema.py 负责规则约束和表单校验；wt_run_reporting.py 负责生成结构化运行报告。"),
        ("h2", "4.3 数据组织设计"),
        ("normal", "流程定义文件 flow_definition.json 用于描述步骤、控件、窗口标题、动作参数和流程包关系，是系统运行时最核心的数据来源。control_maps 目录存放控件库文件，用于沉淀可复用的控件定义；image_templates 目录存放图像模板与索引，用于视觉兜底；logs/run_reports 目录存放每次运行的结果报告，记录步骤状态、耗时、错误和 fallback 信息；resources 目录则保存 Robot 资源配置与项目默认参数。通过上述文件组织，系统将“流程”“资产”“配置”“日志”四类数据解耦存放，便于长期维护。"),
        ("h2", "4.4 工作流程设计"),
        ("normal", "系统工作流程为：首先由 Robot 资源文件准备调度配置并读取环境变量；随后调用 Python 主程序，装配运行参数并加载流程定义；接着执行器按步骤遍历流程，对每个动作步骤调用定位器搜索目标控件或父窗口区域；若动作执行失败，则按配置判断是否进入模板匹配兜底；执行完成后，运行报告模块写入结构化 JSON 结果；若用户需要维护流程，则可通过编辑器继续调整步骤、控件或模板资产，形成调试到修复再到验证的闭环。"),
        ("caption", "图4-2 系统运行流程图"),
        ("diagram", "开始\n  ↓\n读取项目配置与环境变量\n  ↓\n加载 flow_definition 与流程包\n  ↓\n校验步骤配置是否合法\n  ↓\n按顺序执行步骤\n  ↓\n结构化定位成功？ ──否──> 触发模板兜底或继续策略\n  │是\n  ↓\n执行点击/输入/等待等动作\n  ↓\n记录步骤结果与耗时\n  ↓\n生成结构化运行报告\n  ↓\n结束"),
        ("normal", "图4-2 从运行时角度说明了系统的数据流与控制流关系。该流程强调“配置先行、执行居中、报告收尾”的三段式组织结构，这也是桌面自动化系统实现可维护闭环的重要基础。"),
        ("h1", "第5章 关键技术设计与实现"),
        ("h2", "5.1 流程定义与动作抽象"),
        ("normal", "系统将流程步骤抽象为 script、action 和 flow_ref 三类。其中 script 适合承载固定业务逻辑，action 用于表达可配置的标准交互动作，flow_ref 用于实现流程包复用。action 进一步抽象出 click、double_click、type_text、send_keys、click_relative_region、type_text_relative、wait_for_control 和 mouse_wheel 等动作类型，并为每种动作定义默认超时、等待时间和输入字段约束。通过动作抽象，业务流程从“写死在代码里的操作序列”转变为“由数据驱动的解释执行过程”，从而显著降低流程改造成本。"),
        ("h2", "5.2 多属性评分控件定位方法"),
        ("normal", "在结构化定位中，系统不会简单依赖单一属性，而是综合 automation_id、name、class_name、control_type、framework_id、窗口标题、祖先节点信息和子节点特征等多种信息构建候选集合并进行评分。对于 automation_id 明确的控件，系统优先使用 automation_id 与 control_type 联合匹配；当 automation_id 缺失时，退化为名称、控件类型、类名与框架特征组合；若目标控件窗口标题明确，则窗口标题匹配将获得更高权重。最终系统从候选集中选择评分最高的控件，并对窗口与控件结果进行缓存，提高重复步骤的检索效率。"),
        ("h2", "5.3 控件匹配评分算法表达"),
        ("normal", "为了更准确地描述系统中的控件匹配过程，可以将候选控件 c 对目标定义 d 的评分写为如下形式："),
        ("formula", "Score(c, d) = S_base(c, d) + S_attr(c, d) + S_aux(c, d) + S_tree(c, d) + S_window(c, d) + S_state(c)"),
        ("normal", "其中，S_base 表示由主定位器候选命中的基础分；S_attr 表示 automation_id、name、control_type、class_name、framework_id 等关键属性的匹配奖励；S_aux 表示辅助检查项，如 IsEnabled、IsOffscreen、HasKeyboardFocus 等的匹配奖励；S_tree 表示祖先节点和子节点语义关系的匹配得分；S_window 表示候选控件所属顶层窗口与目标窗口标题、框架标识的一致性得分；S_state 表示控件当前可见、可用状态的附加分。"),
        ("formula", "S_attr(c, d) = w1 * M_autoid + w2 * M_name + w3 * M_type + w4 * M_class + w5 * M_framework"),
        ("normal", "其中 M_autoid、M_name、M_type、M_class、M_framework 取值为 0 或 1，分别表示对应属性是否匹配，w1 至 w5 为预设权重。工程上，系统对 automation_id 和控件类型给予更高权重，对 class_name 与 framework_id 给予较低权重，以兼顾定位精度与泛化能力。当窗口标题显式不匹配时，系统将引入较大的负分以抑制误命中，这一做法对解决“虚假成功”问题尤其有效。"),
        ("caption", "图5-1 控件定位与评分流程示意图"),
        ("diagram", "输入：步骤定义 + 控件定义 + 窗口提示\n  ↓\n枚举候选窗口\n  ↓\n按 automation_id/name/class_name 构造快速查询\n  ↓\n得到候选控件集合\n  ↓\n计算基础分、属性分、树结构分、窗口分\n  ↓\n选择最高分候选\n  ↓\n分数满足阈值？ ──否──> 进入全量回退搜索或判定失败\n  │是\n  ↓\n缓存控件结果并返回"),
        ("normal", "图5-1 对应了系统中“快速查询 + 评分排序 + 阈值判定 + 缓存”的定位思路。该流程不是简单的单次遍历，而是先使用轻量查询缩小搜索空间，再用评分机制提升结果可靠性，最后通过缓存减少重复步骤的搜索成本。"),
        ("h2", "5.4 父窗口相对区域交互方法"),
        ("normal", "在复杂 WPF 场景中，常见问题是控件树可见但实际可点击目标并不稳定，或者真实输入区域没有清晰的 automation 属性。为解决该问题，系统引入父窗口相对区域交互方法：先通过窗口标题、类名和框架标识定位父窗口，再将归一化相对区域参数转换为屏幕绝对矩形，并依据锚点计算点击中心，最终执行点击或文本输入。该方法使系统可以绕过不稳定的中间控件层，直接以窗口为参考系完成交互。"),
        ("normal", "设父窗口矩形为 W = (L, T, Width, Height)，相对区域参数为 R = (x, y, w, h)，则区域的绝对左上角坐标和尺寸可表达为："),
        ("formula", "Left_abs = L + Width * x"),
        ("formula", "Top_abs = T + Height * y"),
        ("formula", "Width_abs = Width * w"),
        ("formula", "Height_abs = Height * h"),
        ("normal", "若锚点选择中心点，则最终点击点可表示为："),
        ("formula", "P_click = (Left_abs + Width_abs / 2, Top_abs + Height_abs / 2)"),
        ("caption", "图5-2 父窗口相对区域点击示意图"),
        ("diagram", "┌──────────────────────── 父窗口 W ────────────────────────┐\n│                                                          │\n│        ┌──────────── 相对区域 R ────────────┐            │\n│        │                                     │            │\n│        │                ● P_click            │            │\n│        │                                     │            │\n│        └─────────────────────────────────────┘            │\n│                                                          │\n└──────────────────────────────────────────────────────────┘"),
        ("normal", "图5-2 展示了父窗口与目标相对区域之间的空间关系。论文正式排版时可将其转绘为标准矢量示意图，并在图中标注父窗口矩形、相对区域比例参数和实际点击点坐标。"),
        ("h2", "5.5 前台窗口切换后的参考窗口重选与补点机制"),
        ("normal", "项目调试表明，相对区域点击时首次命中的父窗口并不总是最终承载目标控件的真实窗口。在 WPF 场景下，系统初次识别到的对象可能只是标题为空、面积较大或层级较泛化的中间容器；而在第一次点击之后，前台窗口可能切换为同进程下更具体的实际对话框。如果仍沿用初始窗口矩形作为参考系，后续点击或输入位置就会发生整体偏移。为此，系统在首次点击后重新检测前台窗口，并比较其进程归属、类名、框架标识及矩形大小；当新窗口与目标特征更一致且比原窗口更具体时，系统将其重选为新的参考窗口。"),
        ("normal", "在新的参考窗口确定后，系统会重新计算相对区域对应的绝对矩形和锚点坐标，并补做一次点击，使交互位置落到更准确的目标区域上。该机制的核心不是简单重复点击，而是在前台窗口发生切换后重新选择更合理的参考窗口，再据此重算坐标并完成补点。实践表明，该机制对 WPF 弹窗中的位置偏移、空标题容器干扰和中间层窗口误参照等问题具有明显修正作用。"),
        ("h2", "5.6 模板与 AI 兜底机制"),
        ("normal", "针对下拉项、图标按钮、特殊弹出层和部分控件树不稳定场景，系统在结构化执行主路径之外设计了分层兜底机制。当 action 步骤执行失败且 onError 配置为 fallback 时，执行器首先读取 fallbackTemplate，基于模板匹配获得屏幕中心点，并通过 pyautogui 完成点击、双击、右键、键入或滚轮等补充操作。这一层属于模板兜底，主要面向可通过局部视觉特征稳定识别的界面元素。"),
        ("normal", "若模板匹配仍无法恢复当前步骤，系统进一步触发 AI 介入机制。与模板兜底不同，AI 兜底并不负责从头重跑整个流程，而是结合当前失败步骤、原始错误原因、模板失败原因、最近步骤结果和近期日志，生成面向当前卡住步骤的最小化操作提示，并调用 UI-TARS 桌面代理只处理当前受阻环节。在投影配置和 DWG 投影确认等长链路场景中，系统还会向 AI 传递 resume_stage 等上下文，使其从指定阶段继续执行，避免重复已成功步骤。由此，系统形成了“结构化执行 - 模板兜底 - AI 介入”的三级恢复链路，在保证主路径可解释性的同时，提高了复杂界面场景下的流程完成率和系统鲁棒性。"),
        ("caption", "图5-3 动作执行与分层兜底策略流程图"),
        ("diagram", "开始执行 action\n  ↓\n尝试结构化控件定位与动作执行\n  ↓\n执行成功？ ──是──> 记录成功\n  │否\n  ↓\n检查是否允许 fallback\n  ↓\n允许？ ──否──> 记录失败并结束\n  │是\n  ↓\n执行模板匹配兜底\n  ↓\n模板兜底成功？ ──是──> 记录成功并标记 fallback\n  │否\n  ↓\n检查是否允许 AI 介入\n  ↓\n允许？ ──否──> 记录失败并结束\n  │是\n  ↓\n构造当前步骤上下文与失败原因\n  ↓\n调用 AI 代理处理当前卡住步骤\n  ↓\nAI 介入后续跑条件满足？ ──否──> 记录失败\n  │是\n  ↓\n记录成功并标记 AI intervention"),
        ("h2", "5.7 流程编辑器与控件库自动沉淀"),
        ("normal", "为了降低流程维护门槛，系统实现了可视化流程编辑器。编辑器支持新建步骤、选择动作类型、填写窗口与控件信息、导入 recorder 转换结果、导出 Excel、使用步骤模板快速创建动作，并提供父窗口相对区域取点助手。更重要的是，编辑器将新增或修改后的控件定义自动同步到 control_maps 目录，按窗口标题和框架类型分类保存，从而形成可复用的控件资产库。与传统依赖开发者手工维护脚本的方式相比，这一机制显著提升了系统的可维护性与复用性。"),
        ("h2", "5.8 运行报告与统一校验"),
        ("normal", "系统在执行前通过动作 schema 和流程校验器对步骤定义进行约束检查，包括目标控件是否存在、占位窗口标题是否替换、相对区域参数是否在合法范围内以及流程包引用是否有效。执行完成后，运行报告模块会生成包含 runId、步骤状态、动作类型、耗时、错误信息和 fallback 使用情况的结构化 JSON 结果。该设计使自动化系统不再只是“执行完或没执行完”的黑盒，而是能够输出清晰的可分析证据，为回归测试和性能评估提供基础。"),
        ("h2", "5.9 录制结果转换与最小回归测试支撑"),
        ("normal", "为了降低从录制结果到可维护流程定义之间的迁移成本，系统实现了 recorder 转换器。该模块能够将录制结果语义化转换为流程步骤，识别明显的文件路径常量并提升为运行参数占位，同时标记待复核步骤，从而减少纯手工整理流程定义的工作量。该能力使自动化系统形成了“录制 - 转换 - 校正 - 执行”的完整工程链路。"),
        ("normal", "除运行时校验外，项目还构建了最小回归测试集合，覆盖运行报告汇总、步骤校验规则、编辑器工具的 inspect 解析以及转换器运行参数抽取等关键能力。虽然测试规模仍然有限，但其意义在于：一方面为后续重构提供了基本安全网，另一方面也说明本系统已开始从单次可运行脚本向可持续演进的软件工程形态过渡。"),
        ("h1", "第6章 系统实验与结果分析"),
        ("h2", "6.1 实验环境与实验对象"),
        ("normal", "为验证本文所设计 WT 自动化系统在真实业务场景中的可执行性、稳定性与工程实用性，本文选取当前已经完整跑通的“气象数据录入”流程包作为核心实验对象。该流程包共包含 46 个步骤，覆盖主界面导航、气象对象创建、时间序列数据导入、统计数据导入、字段映射、数据校验、结果保存以及返回主页面等完整业务链路，能够较全面反映系统在 WT 场景下的端到端自动化能力。"),
        ("normal", "与前期仅针对单个步骤或局部子流程进行验证的方式相比，完整流程包实验更能反映系统在连续执行、多窗口切换、混合界面交互以及复杂状态保持条件下的实际表现。因此，本章不再以局部故障修复作为主要分析对象，而是以完整流程包的稳定运行结果为基础，对系统性能和关键机制进行综合分析。"),
        ("normal", "本次实验所依据的流程定义来源于 flow_definition.json，流程包注册信息来源于 flow_package_registry.json，执行结果依据 logs/run_reports 与 last_run_report.json 中的结构化 JSON 运行报告进行统计。实验运行环境为 Windows 桌面环境，执行层基于 Python 3.11，调度层采用 Robot Framework，结构化定位主要依赖 pywinauto，视觉补充操作依赖 pyautogui。"),
        ("normal", "本章实验目标主要包括三个方面：一是验证“气象数据录入”46 步流程包是否能够稳定完成端到端执行；二是分析完整流程中不同技术机制的承担比例及耗时分布；三是结合历史运行记录，对评分算法在复杂定位场景中的性能提升效果进行量化分析。"),
        ("caption", "表6-1 完整实验对象说明"),
        ("diagram", "┌──────────────┬────────┬──────────────────────────────────────┬────────────────────┐\n│ 流程包ID     │ 步骤数 │ 覆盖业务内容                         │ 实验目标           │\n├──────────────┼────────┼──────────────────────────────────────┼────────────────────┤\n│ 气象数据录入 │ 46     │ 对象创建、参数录入、数据导入、字段映射、校验与保存 │ 验证端到端稳定执行能力 │\n└──────────────┴────────┴──────────────────────────────────────┴────────────────────┘"),
        ("h2", "6.2 完整流程运行结果"),
        ("normal", "根据最新运行报告，当前“气象数据录入”流程包已经实现多次完整稳定运行。除前述标准流程外，用户进一步补充完成了 5 次完整流程测试，其中包括 4 次标准参数运行，以及 1 次基于 Excel 导入流程变体并调整测风塔信息后的实测运行。全部 5 次运行均达到 46 个请求步骤全部成功，流程总状态均为 success，说明系统已经能够支撑该业务场景下的完整自动化执行。"),
        ("normal", "从总耗时看，4 次标准参数运行的总耗时分别为 119.694 s、119.184 s、119.188 s 和 120.321 s，平均值为 119.597 s，极差仅为 1.137 s，说明在相同业务语义和相近输入条件下，系统具有较好的重复性与一致性。第 5 次运行采用 Excel 导入形成的流程变体，并对测风塔名称及默认高度等参数进行了调整，其中测风塔名称由 CFT01 改为 CFT02，多个高度输入由 125 改为 160，总耗时为 126.099 s。尽管该次运行耗时略高，但仍实现了 46 步全部成功，说明系统在流程来源变化和参数扰动条件下仍保持了较好的可执行性。"),
        ("normal", "因此，从论文实验设计角度看，当前结果已经不仅能够证明系统具备“单次完整跑通”的能力，还能证明其在重复运行和参数变化条件下具有一定稳定性与适应性。这使得第六章的实验结论可以建立在多次成功样本基础之上，而非建立在单次成功样本基础之上。"),
        ("caption", "表6-2 完整流程连续运行结果对比"),
        ("diagram", "┌──────────────┬────────┬────────────┬──────────┬──────────┬──────────────┬────────────────────────┐\n│ 报告编号     │ 总状态 │ 请求步骤数 │ 成功步骤数 │ 失败步骤数 │ 总耗时/s     │ 备注                   │\n├──────────────┼────────┼────────────┼──────────┼──────────┼──────────────┼────────────────────────┤\n│ 20260703_111329 │ 成功 │ 46         │ 46       │ 0        │ 119.694      │ 标准参数运行，1次fallback │\n│ 20260703_112548 │ 成功 │ 46         │ 46       │ 0        │ 119.184      │ 标准参数运行，0次fallback │\n│ 20260703_141616 │ 成功 │ 46         │ 46       │ 0        │ 119.188      │ 标准参数运行             │\n│ 20260703_142006 │ 成功 │ 46         │ 46       │ 0        │ 120.321      │ 标准参数运行             │\n│ 20260703_145924 │ 成功 │ 46         │ 46       │ 0        │ 126.099      │ Excel导入变体，参数扰动实测 │\n└──────────────┴────────┴────────────┴──────────┴──────────┴──────────────┴────────────────────────┘"),
        ("h2", "6.3 按业务阶段的流程结果分析"),
        ("normal", "为更清晰地展示完整流程的执行结构，本文依据业务语义将 46 步流程划分为四个阶段，分别为：界面导航与对象创建、时间序列数据导入与字段映射、统计数据 TI 导入、统计数据 TISD 导入与收尾。按阶段统计的耗时结果如表 6-3 所示。"),
        ("normal", "由统计结果可知，“时间序列数据导入与字段映射”阶段耗时最高，为 45.604 s，占总耗时的 38.3%。该阶段之所以耗时较高，主要是因为其包含文件路径输入、多个下拉项选择、默认高度录入、数据校验以及“添加到数据”等操作，需要跨越多个窗口状态，并涉及结构化定位、相对区域交互、运行时下拉评分与脚本辅助动作等多种机制。"),
        ("normal", "“界面导航与对象创建”阶段共 12 步，累计耗时 27.188 s，占总耗时 22.8%。尽管该阶段步骤数较多，但大部分交互集中在同一业务上下文中完成，因此整体耗时相对可控。"),
        ("normal", "“统计数据 TI 导入”阶段共 7 步，累计耗时 17.874 s，占比 15.0%，是四个阶段中耗时最少的部分。这一结果说明该阶段操作结构较为集中，界面切换复杂度相对较低。"),
        ("normal", "“统计数据 TISD 导入与收尾”阶段累计耗时 28.518 s，占比 23.9%，主要耗时集中在下拉项选择、“添加到数据”以及返回主页面等需要等待界面状态变化的步骤上。"),
        ("caption", "表6-3 完整流程按业务阶段的耗时分布"),
        ("diagram", "┌──────────────────────────┬────────┬──────────────┬──────────────┐\n│ 业务阶段                 │ 步骤数 │ 阶段耗时/s   │ 占总耗时比例 │\n├──────────────────────────┼────────┼──────────────┼──────────────┤\n│ 界面导航与对象创建       │ 12     │ 27.188       │ 22.8%        │\n│ 时间序列数据导入与字段映射 │ 18     │ 45.604       │ 38.3%        │\n│ 统计数据TI导入           │ 7      │ 17.874       │ 15.0%        │\n│ 统计数据TISD导入与收尾   │ 9      │ 28.518       │ 23.9%        │\n└──────────────────────────┴────────┴──────────────┴──────────────┘"),
        ("h2", "6.4 评分算法性能分析"),
        ("normal", "为验证多属性评分机制对系统执行性能的影响，本文基于历史运行报告对评分优化前后的典型步骤耗时进行了统计分析。评分逻辑主要体现在控件评分、窗口评分和运行时下拉候选排序三个层面。其核心思想是：先利用窗口标题、进程、类名和框架标识收缩候选窗口范围，再围绕 automation_id、name、control_type、class_name、framework_id、祖先节点特征和子节点特征对候选控件进行综合评分，最终优先返回高分候选。"),
        ("normal", "在当前完整流程中，评分算法对运行时下拉项选择步骤的性能提升最为明显。以 select_dropdown_item_4 和 select_dropdown_item_5 为例，优化前后平均耗时统计如表 6-4 所示。"),
        ("normal", "结果表明，select_dropdown_item_4 的平均耗时由 7.214 s 降低至 3.858 s，下降 46.5%；select_dropdown_item_5 的平均耗时由 7.186 s 降低至 4.013 s，下降 44.2%。同时，在最新完整流程运行中，这两类步骤的 dropdownRuntime.score 均稳定达到 192，说明运行时评分机制已经能够较快锁定目标弹层项，并减少无效候选遍历和重复等待。"),
        ("normal", "除下拉项选择外，step_8 也能够体现评分优化对复杂 WPF 场景的积极作用。该步骤早期平均耗时为 14.130 s；在引入窗口归属校验、候选排序收紧与控件评分增强后，平均耗时下降至 3.827 s，降幅达到 72.9%；后续由于该步骤进一步切换为相对区域点击方式，其平均耗时继续下降至 0.933 s。该结果说明，评分机制首先显著降低了结构化定位阶段的搜索成本，而在此基础上，结合更适合目标对象的交互方法，还可以进一步提升执行效率。"),
        ("normal", "从机理上看，评分机制带来的性能提升，并非来自单次属性比较速度的提升，而是来自搜索空间的有效压缩。未引入评分约束时，系统需要在较大的窗口和控件候选集合中进行遍历；而在引入窗口归属约束、进程一致性约束和结构特征奖励后，正确候选能够更早进入高分区间，从而减少无效比对次数。这也是本文所提出评分策略能够兼顾效率与可解释性的根本原因。"),
        ("normal", "结合新增的 5 次完整流程数据还可以看到，关键步骤在重复运行条件下也表现出较好的稳定性。例如标准参数运行中，step_14 的平均耗时约为 3.016 s，select_dropdown_item_4 的平均耗时约为 4.279 s，select_dropdown_item_5 的平均耗时约为 4.143 s，step_44 的平均耗时约为 5.568 s，step_46 的平均耗时约为 5.007 s。尽管不同运行间存在少量波动，但整体变化范围较小，说明评分机制和后续执行链路已能够在复杂桌面环境下维持相对稳定的步骤级性能。"),
        ("caption", "表6-4 运行时下拉评分机制优化前后耗时对比"),
        ("diagram", "┌──────────────────────┬──────────┬──────────────┬──────────┬──────────────┬──────────────┐\n│ 步骤ID               │ 优化前样本数 │ 优化前平均耗时/s │ 优化后样本数 │ 优化后平均耗时/s │ 降幅         │\n├──────────────────────┼──────────┼──────────────┼──────────┼──────────────┼──────────────┤\n│ select_dropdown_item_4 │ 2        │ 7.214        │ 3        │ 3.858        │ 46.5%        │\n│ select_dropdown_item_5 │ 4        │ 7.186        │ 2        │ 4.013        │ 44.2%        │\n└──────────────────────┴──────────┴──────────────┴──────────┴──────────────┴──────────────┘"),
        ("caption", "表6-5 step_8 步骤性能演化对比"),
        ("diagram", "┌────────────────────────────┬────────┬──────────────┬──────────────┐\n│ 阶段                       │ 样本数 │ 平均耗时/s   │ 相对初始降幅 │\n├────────────────────────────┼────────┼──────────────┼──────────────┤\n│ 初始结构化定位阶段         │ 10     │ 14.130       │ —            │\n│ 评分优化后结构化定位阶段   │ 6      │ 3.827        │ 72.9%        │\n│ 相对区域替代后的稳定阶段   │ 4      │ 0.933        │ 93.4%        │\n└────────────────────────────┴────────┴──────────────┴──────────────┘"),
        ("h2", "6.5 多策略协同效果分析"),
        ("normal", "完整 46 步流程能够稳定跑通，并不是依赖某一种单一定位方式，而是建立在多种技术机制协同工作的基础之上。结合最新运行报告，可以将完整流程中的执行方式划分为五类：结构化点击/输入、相对区域交互、运行时下拉评分、脚本辅助动作以及带续跑条件动作。各类机制在完整流程中的数量和耗时分布如表 6-6 所示。"),
        ("normal", "从统计结果看，相对区域交互承担了最多的步骤数和最大的累计耗时，共涉及 23 个步骤，累计耗时 50.090 s，占总耗时 42.0%。这说明在 WT 这类 WPF/Win32 混合界面环境中，相对区域方法已经不是临时补充策略，而是复杂表单输入、按钮触发和弹窗交互中的重要常规手段。"),
        ("normal", "运行时下拉评分虽然仅涉及 5 个步骤，但累计耗时达到 19.193 s，占总耗时 16.1%。这说明下拉弹层项选择虽数量不多，却是典型的复杂定位问题，也是评分算法价值体现最直接的场景。"),
        ("normal", "脚本辅助动作仅包含 3 个步骤，累计耗时 6.384 s，主要用于文件路径输入和部分脚本封装型动作。这类机制在总耗时中占比较小，但在业务链路中承担着连接文件对话框与后续业务窗口的重要作用。"),
        ("normal", "带续跑条件动作同样只有 3 个步骤，但累计耗时达到 12.177 s，占总耗时 10.2%。这说明续跑条件的主要价值不在于提速，而在于保证动作执行后界面状态确实发生了预期变化，从而避免“动作已发送但业务状态未真正更新”的虚假成功问题。"),
        ("normal", "从最新运行报告的前十耗时步骤来看，耗时较高的步骤主要集中在“添加到数据”“回到主页面”“导入统计数据”“下拉项选择”和“开始校验”等需要等待界面变化、执行状态确认或进行复杂候选筛选的操作上。这说明当前系统的主要性能瓶颈，已经不再是基础点击或文本输入，而更多来自界面切换等待、结果确认以及复杂交互环节的状态稳定过程。"),
        ("caption", "表6-6 不同执行机制在完整流程中的分布"),
        ("diagram", "┌──────────────────┬────────┬──────────────┬──────────────┐\n│ 执行机制         │ 步骤数 │ 累计耗时/s   │ 占总耗时比例 │\n├──────────────────┼────────┼──────────────┼──────────────┤\n│ 结构化点击/输入  │ 12     │ 31.340       │ 26.3%        │\n│ 相对区域交互     │ 23     │ 50.090       │ 42.0%        │\n│ 运行时下拉评分   │ 5      │ 19.193       │ 16.1%        │\n│ 脚本辅助动作     │ 3      │ 6.384        │ 5.4%         │\n│ 带续跑条件动作   │ 3      │ 12.177       │ 10.2%        │\n└──────────────────┴────────┴──────────────┴──────────────┘"),
        ("h2", "6.6 历史失败类型与修复收敛分析"),
        ("normal", "为了避免仅以当前成功结果作为结论依据，本文进一步回溯分析了 logs/run_reports 中累计保留的历史运行报告。统计结果表明，当前项目共保留 122 份运行报告，其中成功运行 46 份，失败运行 76 份。该结果说明，系统当前的稳定执行能力并非一次性获得，而是在大量失败复现、定位修复与重复验证基础上逐步形成的。"),
        ("normal", "进一步对失败报告中的主错误信息进行归类可以发现，系统早期失败主要集中在导入按钮未命中、下拉项运行时未命中、文件对话框输入框未命中以及复选框状态提交失败等几类问题，随后逐步过渡到文件对话框输入、复用步骤适配以及后置状态确认等更细粒度问题。这一变化说明系统故障重心已经从“能否找到目标对象”转移到“动作执行后界面状态是否符合预期”，体现出系统成熟度的提升。"),
        ("caption", "表6-7 历史失败类型分布及收敛情况"),
        ("diagram", "┌──────────────────────────┬────────────────────┬────────┬────────────────────────────┐\n│ 主要失败类型             │ 代表步骤           │ 次数   │ 当前状态                   │\n├──────────────────────────┼────────────────────┼────────┼────────────────────────────┤\n│ 导入按钮未命中           │ step_13            │ 19     │ 已通过控件修正与前台切换处理解决 │\n│ 下拉项运行时未命中       │ select_dropdown_item │ 10   │ 已通过运行时评分与候选筛选解决   │\n│ 文件名输入框未命中       │ step_14            │ 10     │ 已通过 Win32 Edit 约束解决      │\n│ 复选框提交失败           │ step_29            │ 6      │ 已通过目标区域修正与流程确认解决 │\n│ 复用文件名输入未命中     │ step_39            │ 5      │ 已通过复用链路修正解决          │\n│ 默认高度输入未命中       │ step_16            │ 4      │ 已通过相对区域与补点机制缓解    │\n│ 开始校验兜底不可执行     │ step_26            │ 4      │ 已通过主路径稳定执行替代        │\n│ 添加到数据续跑条件未满足 │ step_44            │ 3      │ 已通过后置条件修正缓解          │\n└──────────────────────────┴────────────────────┴────────┴────────────────────────────┘"),
        ("normal", "由表 6-7 可见，失败次数最多的几类问题均与复杂桌面界面的定位和窗口状态有关，尤其是 step_13、select_dropdown_item 和 step_14 所代表的“导入按钮触发 + 下拉弹层选择 + 文件对话框输入”链路，构成了项目中最主要的早期故障源。随着结构化控件定义收紧、运行时下拉评分增强、Win32 对话框 Edit 控件约束加入、窗口标题与前台窗口联合筛选以及相对区域参考窗口修正等措施的引入，这些高频失败类型逐渐被压缩并最终从完整流程运行中消失。"),
        ("normal", "从系统演进角度看，这一结果说明本文提出的自动化方案并非在静态环境下偶然命中，而是经过多轮真实失败样本驱动的优化过程后，逐步形成了对 WT 复杂界面的稳定适配能力。这种基于运行报告和失败收敛的分析方式，也为后续类似桌面自动化系统的工程评估提供了可参考路径。"),
        ("h2", "6.7 主路径稳定性与兜底依赖度分析"),
        ("normal", "在自动化系统设计中，模板兜底和 AI 介入的意义在于提升鲁棒性，但从工程角度看，理想状态并不是长期依赖兜底，而是逐步提升主执行路径的直接成功率。因此，仅统计“是否最终成功”仍然不足，还需要进一步分析系统对 fallback 的依赖程度。"),
        ("normal", "结合历史运行报告可知，在全部 122 份报告中，存在 fallback 的运行共有 11 份，说明项目在调试与修复过程中曾多次依赖兜底策略维持链路完整性。然而从最近 5 次完整 46 步流程运行结果来看，系统已经表现出明显的主路径增强趋势：20260703_111329 报告虽然完整成功，但仍包含 1 次 fallback；此后的 20260703_112548、20260703_141616、20260703_142006 和 20260703_145924 四份报告在 46 步全部成功的同时，fallbackCount 均降为 0。"),
        ("caption", "表6-8 主路径稳定性与兜底依赖度变化"),
        ("diagram", "┌──────────────────────┬────────┬────────────┬──────────────┬────────────────────────────┐\n│ 统计层面             │ 运行结果 │ fallback次数 │ 总耗时/s     │ 说明                       │\n├──────────────────────┼────────┼────────────┼──────────────┼────────────────────────────┤\n│ 历史全部运行         │ 122次    │ 11次运行存在fallback │ —        │ 调试期曾依赖兜底维持链路完整性 │\n│ 完整运行20260703_111329 │ 成功  │ 1          │ 119.694      │ 标准参数，仍有少量兜底       │\n│ 完整运行20260703_112548 │ 成功  │ 0          │ 119.184      │ 标准参数，主路径独立完成     │\n│ 完整运行20260703_141616 │ 成功  │ 0          │ 119.188      │ 标准参数，主路径独立完成     │\n│ 完整运行20260703_142006 │ 成功  │ 0          │ 120.321      │ 标准参数，主路径独立完成     │\n│ 完整运行20260703_145924 │ 成功  │ 0          │ 126.099      │ Excel变体，主路径独立完成    │\n└──────────────────────┴────────┴────────────┴──────────────┴────────────────────────────┘"),
        ("normal", "由表 6-8 可以看出，系统当前已经从“需要少量 fallback 接管才能完成完整链路”的阶段，进一步演进到“无需 fallback 也能完成完整流程”的阶段。这一变化具有重要实验意义：它表明模板兜底机制在项目中主要承担了调试和过渡期的恢复作用，而当前版本的结构化定位、相对区域交互、运行时下拉评分和后置状态确认机制，已经能够共同支撑主执行链路的稳定运行。新增的 5 次完整流程测试中，除最早一份标准运行仍记录 1 次 fallback 外，其余 4 次运行均未触发 fallback，进一步印证了主路径稳定性的提升。"),
        ("normal", "从论文表述上看，这一结果能够进一步强化本文的工程结论，即兜底机制虽然是系统鲁棒性设计的重要组成部分，但其目标并不是长期代替主执行路径，而是在复杂场景和修复阶段提供补偿。当主路径逐步稳定后，系统对兜底策略的依赖度会随之下降。当前最新完整流程的运行结果正验证了这一点。"),
        ("h2", "6.8 结果讨论与本章小结"),
        ("normal", "综合本章实验结果，可以得到以下几点结论。第一，当前 WT 自动化系统已经能够稳定完成“气象数据录入”46 步完整流程包的端到端执行，最近 5 次完整流程运行均达到 100% 成功率，其中 4 次为标准参数重复运行、1 次为 Excel 导入变体与参数扰动实测，说明系统已经具备较好的重复执行能力和工程可用性。"),
        ("normal", "第二，完整流程的成功并非建立在单一路径之上，而是建立在结构化定位、相对区域交互、运行时下拉评分、脚本辅助和续跑条件等多种机制的协同作用之上。其中，相对区域交互承担了最多的关键步骤，评分算法则显著提升了复杂下拉场景中的定位效率。"),
        ("normal", "第三，评分算法对降低复杂候选搜索开销具有明显效果。特别是在运行时下拉步骤中，优化后平均耗时下降约 44% 至 47%，说明“窗口先筛选、候选再评分、结果再缓存”的定位策略在实际桌面场景中是有效的。"),
        ("normal", "第四，历史失败类型统计表明，系统的主要故障点已经从早期的控件未命中和父窗口未命中，逐步收敛到后期的状态确认与链路收尾问题，反映出系统成熟度的持续提升。"),
        ("normal", "第五，主路径稳定性分析表明，系统已经从“偶尔依赖 fallback 维持完整链路”的阶段，演进到“无需 fallback 即可完成完整流程”的阶段。这说明兜底机制在当前系统中更多承担修复与过渡作用，而主执行路径本身已具备较高稳定性。"),
        ("normal", "第六，新增的 5 次完整流程测试进一步说明，系统不仅能够在标准参数条件下重复稳定运行，而且能够在流程来源变化和参数扰动条件下维持完整执行。其中，Excel 导入形成的流程变体在修改测风塔名称和默认高度后仍然实现 46 步全部成功，说明本文所设计的自动化系统对步骤来源和输入值变化具有一定适应能力。"),
        ("normal", "总体来看，本章实验已经不再停留在局部问题修复验证层面，而是基于完整流程包的稳定运行结果、历史失败收敛特征、兜底依赖度变化以及重复运行与参数扰动结果，对系统性能、机制分工和工程可用性进行了较系统的分析。实验结果表明，本文提出的多策略桌面自动化方案能够较好适应 WT 复杂界面场景，并为后续扩展到更多仿真业务流程提供了可靠的技术基础。"),
        ("h1", "第7章 总结与展望"),
        ("h2", "7.1 全文总结"),
        ("normal", "本文围绕 WT 仿真软件的桌面自动化需求，设计并实现了一套由流程定义、动作执行、控件定位、相对区域交互、模板兜底、可视化编辑和运行报告构成的工程化自动化系统。实践表明，该系统已经能够覆盖 WT 业务中的多个典型场景，并通过结构化定位与视觉兜底的融合方法，有效提高复杂界面下的执行稳定性。与一次性脚本相比，本系统更强调长期维护、资产复用和结果可追踪，具有明显工程价值。"),
        ("h2", "7.2 创新点总结"),
        ("normal", "本文工作的主要特点在于：提出了面向 WPF 与 Win32 混合界面的多属性评分定位方法；设计了基于父窗口相对区域的点击与输入机制，并通过前台窗口切换后的参考窗口重选与补点策略增强稳定性；构建了结构化定位失败后由模板匹配和 AI 介入共同接管的多层恢复链路；实现了可视化编辑器与控件资产自动沉淀机制；通过统一校验和结构化运行报告增强了系统的可调试性与可验证性。"),
        ("h2", "7.3 后续展望"),
        ("normal", "后续工作可从以下几个方面展开：继续扩展 WT 业务后半段流程，形成更完整的端到端自动化链路；增强步骤自适应与自动修复能力，在控件轻微变化时自动调整定位策略；引入更强的语义理解机制，辅助自动生成步骤定义和控件描述；结合更多运行报告数据，建立稳定性评估指标体系，为不同策略的选用提供量化依据。"),
        ("h1", "参考文献（待补）"),
        ("normal", "[1] Windows UI Automation 相关技术文献。"),
        ("normal", "[2] pywinauto 官方文档与桌面自动化工程实践资料。"),
        ("normal", "[3] Robot Framework 自动化测试与流程调度相关文献。"),
        ("normal", "[4] 图像模板匹配与人机交互自动化相关研究。"),
        ("normal", "[5] RPA、GUI Agent 与复杂桌面软件自动化相关研究。"),
        ("h1", "附录（建议后续补充）"),
        ("normal", "附录 A：项目目录结构与模块职责说明。"),
        ("normal", "附录 B：flow_definition.json 中典型步骤样例。"),
        ("normal", "附录 C：典型运行报告样例。"),
        ("normal", f"文档生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"),
    ]


def paragraph_xml(text, style):
    text = str(text or "")
    if not text:
        return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr></w:p>'
    safe = escape(text).replace("\n", '</w:t><w:br/><w:t xml:space="preserve">')
    return f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p>'


def build_document_xml():
    style_map = {
        "title": "Title",
        "subtitle": "Subtitle",
        "h1": "Heading1",
        "h2": "Heading2",
        "normal": "Normal",
        "caption": "Caption",
        "diagram": "Diagram",
        "formula": "Formula",
    }
    paragraphs = []
    for kind, text in build_blocks():
        paragraphs.append(paragraph_xml(text, style_map.get(kind, "Normal")))
    paragraphs.append('<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1800" w:header="708" w:footer="708" w:gutter="0"/></w:sectPr>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
        'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
        'xmlns:o="urn:schemas-microsoft-com:office:office" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
        'xmlns:v="urn:schemas-microsoft-com:vml" '
        'xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" '
        'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
        'xmlns:w10="urn:schemas-microsoft-com:office:word" '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
        'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml" '
        'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
        'xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" '
        'xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" '
        'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
        'mc:Ignorable="w14 w15 wp14"><w:body>'
        + "".join(paragraphs)
        + "</w:body></w:document>"
    )


def build_styles_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults>
    <w:rPrDefault>
      <w:rPr>
        <w:rFonts w:ascii="Calibri" w:eastAsia="宋体" w:hAnsi="Calibri" w:cs="Calibri"/>
        <w:sz w:val="24"/>
        <w:szCs w:val="24"/>
      </w:rPr>
    </w:rPrDefault>
  </w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:qFormat/>
    <w:pPr>
      <w:jc w:val="both"/>
      <w:ind w:firstLine="420"/>
      <w:spacing w:line="420" w:lineRule="auto" w:after="0"/>
    </w:pPr>
    <w:rPr>
      <w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体" w:hAnsi="Times New Roman"/>
      <w:sz w:val="24"/>
    </w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:jc w:val="center"/><w:spacing w:before="240" w:after="240"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="黑体" w:hAnsi="Times New Roman"/><w:b/><w:sz w:val="36"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle">
    <w:name w:val="Subtitle"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:jc w:val="center"/><w:spacing w:after="240"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体" w:hAnsi="Times New Roman"/><w:sz w:val="24"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:keepNext/><w:outlineLvl w:val="0"/><w:spacing w:before="240" w:after="120"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="黑体" w:hAnsi="Times New Roman"/><w:b/><w:sz w:val="32"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading2">
    <w:name w:val="heading 2"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:keepNext/><w:outlineLvl w:val="1"/><w:spacing w:before="160" w:after="80"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="黑体" w:hAnsi="Times New Roman"/><w:b/><w:sz w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Caption">
    <w:name w:val="Caption"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:jc w:val="center"/><w:spacing w:before="120" w:after="80"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Times New Roman" w:eastAsia="宋体" w:hAnsi="Times New Roman"/><w:i/><w:sz w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Diagram">
    <w:name w:val="Diagram"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:ind w:firstLine="0"/><w:spacing w:before="60" w:after="120" w:line="280" w:lineRule="auto"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Courier New" w:eastAsia="等线" w:hAnsi="Courier New"/><w:sz w:val="18"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Formula">
    <w:name w:val="Formula"/>
    <w:basedOn w:val="Normal"/>
    <w:qFormat/>
    <w:pPr><w:jc w:val="center"/><w:ind w:firstLine="0"/><w:spacing w:before="80" w:after="80"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Cambria Math" w:eastAsia="Times New Roman" w:hAnsi="Cambria Math"/><w:sz w:val="22"/></w:rPr>
  </w:style>
</w:styles>
"""


def build_content_types_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>
"""


def build_root_rels_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>
"""


def build_document_rels_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>
"""


def build_app_xml():
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
            xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>Trae</Application>
  <DocSecurity>0</DocSecurity>
  <ScaleCrop>false</ScaleCrop>
  <Company></Company>
  <LinksUpToDate>false</LinksUpToDate>
  <SharedDoc>false</SharedDoc>
  <HyperlinksChanged>false</HyperlinksChanged>
  <AppVersion>1.0</AppVersion>
</Properties>
"""


def build_core_xml():
    now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
                   xmlns:dc="http://purl.org/dc/elements/1.1/"
                   xmlns:dcterms="http://purl.org/dc/terms/"
                   xmlns:dcmitype="http://purl.org/dc/dcmitype/"
                   xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:title>WT_Automation 论文初稿 规范化修订版</dc:title>
  <dc:creator>Trae</dc:creator>
  <cp:lastModifiedBy>Trae</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{now_iso}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{now_iso}</dcterms:modified>
</cp:coreProperties>
"""


def write_docx():
    os.makedirs(BASE_DIR, exist_ok=True)
    target_path = OUTPUT_FILE
    try:
        with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", build_content_types_xml())
            zf.writestr("_rels/.rels", build_root_rels_xml())
            zf.writestr("docProps/app.xml", build_app_xml())
            zf.writestr("docProps/core.xml", build_core_xml())
            zf.writestr("word/document.xml", build_document_xml())
            zf.writestr("word/styles.xml", build_styles_xml())
            zf.writestr("word/_rels/document.xml.rels", build_document_rels_xml())
        return target_path
    except PermissionError:
        target_path = versioned_output_path(OUTPUT_FILE)
        with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", build_content_types_xml())
            zf.writestr("_rels/.rels", build_root_rels_xml())
            zf.writestr("docProps/app.xml", build_app_xml())
            zf.writestr("docProps/core.xml", build_core_xml())
            zf.writestr("word/document.xml", build_document_xml())
            zf.writestr("word/styles.xml", build_styles_xml())
            zf.writestr("word/_rels/document.xml.rels", build_document_rels_xml())
        return target_path


if __name__ == "__main__":
    print(write_docx())
