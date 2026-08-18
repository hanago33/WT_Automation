---
kind: frontend_style
name: 网站前端样式系统（纯 CSS + CSS 变量主题）
category: frontend_style
scope:
    - '**'
source_files:
    - website/styles.css
    - website/index.html
    - WT_AUTOMATION_Agent/gui.py
---

该仓库的前端样式仅存在于 `website/` 目录下的静态展示页面，采用**纯 CSS + CSS 自定义属性（CSS Variables）**的轻量级方案，未使用任何前端框架或构建工具。核心特点如下：

1. **样式体系与主题设计**
   - 所有颜色、阴影、圆角、渐变、过渡动画均通过 `:root` 中的 CSS 变量集中管理（如 `--color-primary: #D71921`、`--gradient-primary`、`--shadow-*`、`--radius-*`），形成统一的视觉令牌。
   - 主色调为 CRRC 红色（#D71921）搭配深蓝色（#1E3A8A），并定义了成功、警告、危险等语义色，以及明暗背景、文本层级、边框等完整调色板。
   - 字体采用 Google Fonts 的 Inter（正文）与 JetBrains Mono（代码块），通过 `<link>` 引入。

2. **CSS 组织方式**
   - 单一文件 `styles.css`（约 1500+ 行），按功能区块注释分段：Reset & Base → Navigation → Buttons → Hero Section → Cards → Architecture Diagram → Results → Quick Start → Releases → Contact。
   - 使用 BEM 风格的类名约定（如 `.nav`, `.nav-content`, `.logo`, `.hero`, `.section`, `.card`, `.feature-card` 等），无嵌套选择器，保持扁平化结构。
   - 响应式通过媒体查询（在文件后半部分）控制网格布局（`.grid-2`, `.grid-3`）和间距调整。

3. **组件化模式**
   - 按钮：`.btn` 基础类 + `.btn-primary` / `.btn-secondary` / `.btn-small` 变体。
   - 卡片：`.card` 基础类 + `.feature-card` / `.feature-card-large` / `.metric-card` / `.module-detail-card` 等业务变体。
   - 图标：内联 SVG + 渐变色背景容器（`.feature-icon`, `.icon-blue/purple/green/orange`）。
   - 代码块：`.code-window` / `.code-snippet` 模拟 IDE 外观，使用深色主题配色。

4. **交互与动效**
   - 统一使用 `--transition` 和 `--transition-fast` 变量控制过渡时长与缓动函数。
   - 关键帧动画：`pulse`（徽章点）、`bounce`（箭头下移）等。
   - 悬停效果：卡片上浮（`translateY(-4px)`）、按钮阴影增强、导航链接下划线动画。

5. **与 Python GUI 的关系**
   - 项目另有 `WT_AUTOMATION_Agent/gui.py` 提供 Web GUI 界面，但其样式通过内联 `style="..."` 直接注入 HTML 字符串，未复用 `website/styles.css`，属于独立的嵌入式样式方案。

6. **约束与限制**
   - 无 CSS 预处理器（Sass/Less）、无构建流程、无版本化工具。
   - 样式仅服务于静态文档展示页，非产品 UI 的一部分。
   - 未使用 Tailwind、Bootstrap、Ant Design 等第三方 UI 库。

该样式系统是一个典型的“单文件 CSS + CSS 变量”的轻量级站点风格方案，适合文档/展示类页面，但不具备可复用的组件库能力。