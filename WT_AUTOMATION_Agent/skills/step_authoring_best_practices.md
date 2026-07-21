# 步骤新增填写规范（Step Authoring Best Practices）

> 从项目实际经验沉淀的步骤填写最佳实践

## 总原则

1. 先新增细分控件，再新增动作步骤
2. 先选对控件类型，再设置动作
3. `automationId` 有值时，优先使用 `automation_id`
4. 只有 `name` 时，必须警惕同名标签、文字层、父容器误命中
5. WPF 自绘控件或列表项不稳定时，优先改为"父窗口 + 相对区域"

## 四种高频模板

### 1. 按钮 (click)
- 推荐定位：`automation_id,control_type`
- 推荐目标值：`按钮AutomationId,Button`
- 口诀：按钮优先 `automation_id + Button`

### 2. 输入框 (type_text)
- 推荐定位：`name,class_name`
- 推荐目标值：`标签名,Edit`
- 口诀：输入框只写 `name` 不够，必须补 `Edit`（防止同名 Static 标签误命中）

### 3. 下拉项 (select_dropdown_item_runtime / click)
- 推荐定位：`name,class_name`
- 推荐目标值：`选项文本,ListBoxItem` 或 `选项文本,MenuItem`
- 口诀：下拉项先抓父容器（ListBoxItem/MenuItem），不要先抓文字层（TextBlock）

### 4. 相对区域 (click_relative_region / type_text_relative)
- 适用：WPF 弹窗、自绘按钮、Accessibility 信息差的控件
- 先确保父窗口能稳定命中，再调相对区域
- 口诀：相对区域先锁父窗口，再调矩形

## 推荐流程

1. 确定步骤类型（按钮/输入框/下拉项/相对区域）
2. 编辑器左侧"模板新增"选对应模板
3. 修改特有字段：步骤名称、目标窗口、定位字段、输入文本
4. 已有真实控件信息的，去"细分控件清单"中补齐或替换默认控件
5. **每次新增后先单步验证**，不要一上来跑整段流程

## 常见误区（避坑指南）

| 误区 | 表现 | 正确做法 |
|------|------|---------|
| 只按 name 定位 | 命中同名 Static 标签而非实际控件 | 加 `control_type` 或 `class_name` 约束 |
| 输入步骤配成 click | 日志成功但无内容写入 | 输入框用 `type_text` |
| WPF 下拉抓成 TextBlock | 只能点到显示文字不能选中 | 抓 `ListBoxItem`/`MenuItem`，不行就切相对区域 |
| WPF 列表项总是漂移 | 相对区域偏移不稳 | 重新采集参考窗口，确保 parentWindow 锚点控件稳定 |

## 单步验证口诀

日志成功但界面没变化 → 检查：
1. 是否命中同名标签？
2. 是否抓到了文字层？
3. 是否缺少 Edit/Button/ListBoxItem 类型约束？
