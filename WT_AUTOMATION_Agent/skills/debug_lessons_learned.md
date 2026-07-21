# 调试经验教训（Debug Lessons Learned）

> 从项目 debug-*.md 文件中提取的关键经验，按问题类型分类

## 一、控件定位与命中

### 假命中 (false-hit)
- **现象**：日志显示控件已找到并操作成功，但界面无反应
- **根因**：定位到了同名但非目标控件（如同名 Static 标签而非 Edit 输入框）
- **修复**：加强 `targetMethod` 中的类型约束（如 `name,class_name` ← `name,Edit`）
- **相关文件**：`debug-add-data-false-hit.md`、`debug-step37-add-data-miss.md`

### 误命中 (wrong-target)
- **现象**：点击到了相邻或重叠的其他控件
- **根因**：多个控件 `automationId` 相同或 `name` 相似
- **修复**：加 `control_type` 或 `ancestors` 约束做歧义消除

## 二、窗口与相对区域

### 窗口漂移 (window-drift)
- **现象**：相同的相对区域坐标，在不同运行中点击位置偏移
- **根因**：参考窗口（reference window）的尺寸/位置在不同条件下变化（如窗口最大化/还原、多监视器）
- **修复**：每次操作前重新选择参考窗口；使用 `parentWindow` 而非绝对坐标
- **相关文件**：`debug-cft02-step16-drift.md`

### 相对区域偏移 (relative-region-offset)
- **现象**：`click_relative_region` 的偏移量与预期不符
- **根因**：计算基点不正确（center vs left_center vs right_center 选择错误）
- **修复**：确认 `relativeRegionAnchor` 设置正确；必要时用 `click_relative_anchor` 替代
- **相关文件**：`debug-relative-region-offset.md`

## 三、输入相关

### 时序输入回归 (time-series-input-regression)
- **现象**：之前能正常输入的步骤更新后失败
- **根因**：输入法切换、焦点丢失、系统剪贴板冲突
- **修复**：确保输入前 `waitForControl` + `type_text` 使用 send_keys 后验证
- **相关文件**：`debug-time-series-input-regression.md`

### 默认高度相对输入 (default-height-relative-input)
- **现象**：父窗口区域输入的默认高度计算错误
- **根因**：未正确获取父窗口实际高度
- **修复**：运行时动态计算父窗口矩形，勿用硬编码高度
- **相关文件**：`debug-default-height-relative-input.md`

## 四、验证与回归

### 启动激活回归 (start-validation-regression)
- **现象**：新的修改导致之前能通过验证的步骤失败
- **根因**：修改了共享的窗口激活/验证逻辑
- **修复**：修改底层函数后运行全量回归测试
- **相关文件**：`debug-start-validation-regression.md`

## 五、通用教训

1. **每次修改组件后必须跑回归测试**（防止改了 A 坏了 B）
2. **优先用 automationId**：比 name/xpath 稳定得多（WPF 控件有稳定的 automationId）
3. **WPF 弹窗优先用相对区域**：自绘控件拿不到 UIA 信息时，切相对区域是最稳的
4. **输入框定位必须加类型约束**：`name,Edit` 而不是孤立的 `name`
5. **单步验证优先于全流程运行**：新增步骤后先跑单步，减少调试成本
