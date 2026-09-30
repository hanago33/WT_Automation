---
kind: design
name: 采用 RawViewWalker BFS 替代 DFS 进行 WPF 控件采集
source: session
category: adr
---

# 采用 RawViewWalker BFS 替代 DFS 进行 WPF 控件采集

_来源：26033ab → 0b3926e 提交周期内记录的编码计划——内容为规划时意图，实现可能滞后或有出入。_

**状态：** accepted

## 背景
原有基于 pywinauto wrapper.children() 的 DFS 采集只能获取 19 个控件（实际画框 46），根本原因是 WPF 的 HWND 视角限制导致 UIA 树只有 1-2 层 HwndSource 包装，深层视觉树元素被遗漏。

## 决策驱动
- 完整采集 WPF 深层控件
- 性能优化（批量属性获取）
- 渐进式采集支持大窗口
- 向后兼容现有处理流程

## 备选方案
- **IUIAutomation::RawViewWalker + BFS + BuildUpdatedCache** — 优点：绕过 HWND 限制、不过滤 IsContentElement、BFS 按层遍历、一次 COM 调用获取 N 个属性、支持 checkpoint 续采
- **继续优化 _walk_wrapper DFS 路径** _（已否决）_ — 优点：改动最小、复用现有逻辑；缺点：无法解决 WPF 深层控件遗漏的根本问题、性能瓶颈仍在

## 决策
在 build_control_map_library.py 中新增 _walk_raw_view_bfs() 函数，直接调用 IUIAutomation::RawViewWalker 的 GetFirstChildElement/GetNextSiblingElement 进行 BFS 遍历，配合 CacheRequest 批量获取属性，主采集路径优先尝试 RawView BFS，失败时降级回原 _walk_wrapper。

## 影响
flat_controls 输出格式保持不变，下游所有后处理函数无需修改；新增 RawViewWalkBFSTests 测试类验证兼容性；支持超时中断和 checkpoint 续采；_walk_wrapper 保留作为降级兜底。