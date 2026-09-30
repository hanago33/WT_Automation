---
kind: frontend_style
name: WT 自动化项目前端样式体系
category: frontend_style
scope:
    - '**'
source_files:
    - website/styles.css
    - WT_Flow_Editor.py
    - WT_AUTOMATION_Agent/gui.py
    - WT_AUTOMATION_Agent/parameter_scan.py
---

本仓库的前端样式由两套完全独立的视觉系统组成：面向外部展示的静态网站与面向内部使用的 Python GUI 编辑器，二者在技术栈、设计语言与组织方式上互不相关。

## 1. 展示型静态网站（website/）
- 技术栈：纯 HTML + CSS + JavaScript，无任何构建工具或框架依赖。
- 设计令牌：通过 :root CSS 自定义属性集中管理，包括主色 --color-primary: #D71921、辅助蓝 --color-secondary: #1E3A8A、成功/警告/危险语义色、背景/文本/边框色板、渐变、阴影、圆角与过渡时间等。
- CSS 方法论：采用命名空间类风格（如 .hero、.card、.btn-primary、.feature-card-large），配合 Grid/Flexbox 布局；大量使用伪元素与 @keyframes 实现动画效果。
- 字体与排版：正文使用 Inter 及系统回退字体，代码块使用 JetBrains Mono；标题字号从 1.15rem 到 3.25rem 分层。
- 响应式策略：未引入媒体查询断点，主要依靠 Flex/Grid 自适应与容器最大宽度 max-width: 1200px 控制。
- 关键文件：website/styles.css（约 1500 行）、website/index.html、website/script.js。

## 2. Python GUI 编辑器（WT_Flow_Editor.py）
- UI 框架：标准库 tkinter + ttk，无第三方主题引擎。
- 主题系统：通过模块级字典 EDITOR_THEME（第 57–69 行）集中定义颜色令牌，包含 bg、panel、toolbar、primary、success_soft、danger_soft、text、muted 等键值。
- 样式应用：_configure_visual_style() 方法中调用 style.theme_use("clam") 并批量 style.configure / style.map Treeview、Notebook、Combobox 等 ttk 控件；通过 root.option_add 全局选项注入默认前景/背景；字体统一设置为 Microsoft YaHei UI, size=10，加粗用于 Heading。
- 组件级样式封装：_create_action_button() 根据 tone 参数选择浅色系按钮调色板（primary/success/danger/accent）；_style_text_surface() 提供明/暗两种文本面版样式；_create_form_card() 封装带标题栏与描述区的卡片容器，支持 default/primary 两种色调。
- 交互反馈：hover 时改变背景色、选中态高亮、Tab 切换时 foreground 变为主题主色。

## 3. Agent 内嵌 Web 界面（WT_AUTOMATION_Agent/gui.py）
- 以字符串形式在 Python 中嵌入 <style> 与内联 style="..."，属于一次性脚本风格，未形成独立样式文件。
- 仅包含少量行内样式（margin、font-size、display:flex、gap 等），不具备可复用性。

## 4. Excel 输出样式（WT_AUTOMATION_Agent/parameter_scan.py）
- 使用 openpyxl.styles 的 Font、PatternFill、Alignment、Border、Side(style="thin", color="D9D9D9") 对导出的 Excel 单元格设置边框与对齐，属于数据报表样式，与 UI 无关。

## 开发者应遵循的规则
1. 新增网站页面：所有新样式必须追加到 website/styles.css，优先复用现有 CSS 变量与类名（如 .btn、.card、.section），避免硬编码颜色。
2. 修改 GUI 主题：只允许修改 WT_Flow_Editor.py 中的 EDITOR_THEME 字典与 _configure_visual_style()，不得在业务逻辑中散落 bg=/fg= 字面量。
3. 新增 GUI 组件：通过 _create_action_button、_create_form_card、_style_text_surface 等封装方法创建，保持统一的圆角、边框与 hover 行为。
4. Agent 内嵌界面：仅在必要时添加最小化内联样式，不要在此处建立新的样式规范。
5. Excel 导出：如需调整报表外观，在 parameter_scan.py 中使用 openpyxl.styles 常量，勿与 GUI 主题混用。