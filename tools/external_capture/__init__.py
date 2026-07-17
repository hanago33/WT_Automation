# encoding: utf-8
"""外部控件采集适配器（uia-peek / axe-windows）。

这些适配器是 WT_Automation 现有 pywinauto 采集链路的【可选补充】，
完全独立，不影响 build_control_map_library.py 的默认行为。

- uiapeek_client: 通过 HTTP 调用本地 UiaPeek 服务，按坐标/焦点 peek 控件祖先链。
- axewindows_client: 调用 AxeWindowsCLI 或 C# bridge，扫描进程拿元素属性/Patterns。

源码与说明见各模块；第三方项目源码位于 vendor/uia-peek 与 vendor/axe-windows。
"""
