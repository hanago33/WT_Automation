---
kind: design
name: 使用 RawViewWalker BFS 替代 pywinauto wrapper.children() 采集 WPF 控件树
source: session
category: adr
---

# 使用 RawViewWalker BFS 替代 pywinauto wrapper.children() 采集 WPF 控件树

_来源：0b3926e → d3bbc69 提交周期内记录的编码计划——内容为规划时意图，实现可能滞后或有出入。_

**状态：** accepted

## 背景
原有基于 pywinauto 的 `_walk_wrapper` 递归 DFS 采集在 WPF 应用中仅能获取 19 个控件（实际画框有 46 个），根本原因是 `wrapper.children()` 调用 `FindAll(TreeScope_Children, TrueCondition)` 受限于 WPF 的 HWND 视角——UIA 树从窗口句柄往下往往只有 1-2 层 HwndSource 包装，深层 WPF 视觉树元素被漏掉。Inspect.exe 使用 `IUIAutomation::RawViewWalker` + 渐进展开策略能完整获取全部元素。

## 决策驱动
- WPF 控件完整性采集
- 性能优化（批量属性缓存）
- 用户体验（渐进式进度反馈）
- 向后兼容（不破坏下游处理逻辑）

## 备选方案
- **RawViewWalker BFS + BuildUpdatedCache 批量属性** — 优点：绕过 HWND 限制直接遍历 UIA 原始视图；BFS 保证低深度控件优先出现；一次 COM 调用获取 N 个属性而非 N 次独立调用；支持超时暂停和 checkpoint 恢复
- **保持现有 _walk_wrapper (DFS) 作为唯一方案** _（已否决）_ — 优点：无需改动，稳定；缺点：无法获取深层 WPF 控件，采集不完整
- **改用 Win32 API 枚举子窗口** _（已否决）_ — 优点：可能绕过 UIA 限制；缺点：丢失自动化语义信息，与现有流程不兼容

## 决策
在 `build_control_map_library.py` 中新增 `_walk_raw_view_bfs()` 函数，通过 COM 直接调用 `IUIAutomation::RawViewWalker` 的 `GetFirstChildElement`/`GetNextSiblingElement` 实现迭代 BFS 遍历，配合 `BuildUpdatedCache` 批量获取属性；主采集路径切换为新方法，原 `_walk_wrapper` 保留为降级兜底；输出格式与现有 `flat_controls` 完全兼容，下游后处理函数无需修改。

## 影响
解决了 WPF 深层控件采集不完整的问题；新增每 200 个元素的 checkpoint 机制支持超时暂停和续采；GUI 状态栏显示实时进度；需要维护两套遍历逻辑（新为主、旧为降级）；测试覆盖 BFS 遍历、深度限制、超时处理和输出格式兼容性。