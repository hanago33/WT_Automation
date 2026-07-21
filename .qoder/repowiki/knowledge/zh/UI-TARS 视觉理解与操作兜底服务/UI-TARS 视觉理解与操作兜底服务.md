---
kind: external_dependency
name: UI-TARS 视觉理解与操作兜底服务
slug: ui-tars
category: external_dependency
category_hints:
    - vendor_identity
    - auth_protocol
scope:
    - '**'
---

### UI-TARS 视觉兜底能力集成

**角色定位**：当传统控件定位失败时，提供基于视觉理解的兜底解决方案，用于处理动态界面、复杂图表等场景。

**集成方式**：
- 环境变量注入：通过 `UI_TARS_API_KEY` 配置访问凭证
- 图像分析：接收应用截图，返回控件位置和操作建议
- 自动触发：执行器在控件定位失败时自动切换到视觉模式

**认证协议**：
- 使用 OpenAI 兼容的 HTTP API 协议
- 通过请求头携带 API Key 进行身份验证
- 支持自定义 base_url，可部署私有化版本

**使用场景**：
- 投影配置界面的复杂图表操作
- 动态生成的 UI 元素定位
- 非标准控件的识别和操作

verify exact API/params against official docs