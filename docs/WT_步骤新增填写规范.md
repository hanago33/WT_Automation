# WT 步骤新增填写规范

## 目标

这份规范用于指导后续在 `WT_Flow_Editor.py` 中新增业务步骤时，如何更稳地命中目标控件，并尽量减少“日志成功但界面无反应”的假成功问题。

## 总原则

1. 先新增细分控件，再新增动作步骤。
2. 先选对控件类型，再设置动作。
3. `automationId` 有值时，优先使用 `automation_id`。
4. 只有 `name` 时，必须警惕同名标签、文字层、父容器误命中。
5. WPF 自绘控件或列表项不稳定时，优先改为“父窗口 + 相对区域”。

## 四种高频模板

### 1. 新增按钮

- 适用场景：导入按钮、确认按钮、普通功能按钮。
- 推荐动作：`click`
- 推荐定位：`automation_id,control_type`
- 推荐目标值：`按钮AutomationId,Button`

建议填写：

- `步骤名称`：如 `点击-导入时间序列数据`
- `目标窗口`：按钮所在窗口标题
- `targetMethod`：`automation_id,control_type`
- `targetValue`：如 `MUPClimatologyEditView_Button_ImportTimeSeriesFile,Button`

口诀：

- 按钮优先 `automation_id + Button`

### 2. 新增输入框

- 适用场景：文件名、路径、账号、数字输入框。
- 推荐动作：`type_text`
- 推荐定位：`name,class_name`
- 推荐目标值：`标签名,Edit`

建议填写：

- `步骤名称`：如 `键入-时间序列文件路径`
- `目标窗口`：如 `打开`
- `targetMethod`：`name,class_name`
- `targetValue`：如 `文件名(N):,Edit`
- `text`：直接填真实内容，不要手工包双引号

关键提醒：

- 不要只填 `name=文件名(N):`
- 因为同名的 `Static` 标签和 `Edit` 输入框可能同时存在

口诀：

- 输入框只写 `name` 不够，必须补 `Edit`

### 3. 新增下拉项

- 适用场景：访问级别、私有/公有、枚举值、列表项选择。
- 推荐动作：`click`
- 推荐定位：`name,class_name`
- 推荐目标值：`选项文本,ListBoxItem`

建议填写：

- `步骤名称`：如 `选择-私有分组`
- `目标窗口`：下拉所在窗口标题
- `targetMethod`：`name,class_name`
- `targetValue`：如 `私有,ListBoxItem`

关键提醒：

- 优先抓 `ListBoxItem`、`MenuItem` 之类的父容器
- 不要优先抓 `TextBlock`
- 如果只能抓到文字层，成功率通常会差

口诀：

- 下拉项先抓父容器，不要先抓文字层

### 4. 新增相对区域

- 适用场景：WPF 弹窗、自绘按钮、自绘输入框、Accessibility 信息差的控件。
- 推荐动作：
  - 点击：`click_relative_region`
  - 输入：`type_text_relative`

建议填写：

- `目标窗口`
- `parentWindow.title`
- `parentWindow.className`
- `parentWindow.frameworkId`
- `relativeRegion.x`
- `relativeRegion.y`
- `relativeRegion.width`
- `relativeRegion.height`

关键提醒：

- 先确保父窗口能稳定命中
- 再去微调相对区域
- 如果是输入动作，文本仍然直接填真实内容，不要再额外包引号

口诀：

- 相对区域先锁父窗口，再调矩形

## 新增步骤推荐流程

1. 先确定这一步属于：按钮、输入框、下拉项、相对区域中的哪一种。
2. 在左侧点击“模板新增”，优先选对应模板。
3. 修改模板中的特有字段：
   - 步骤名称
   - 目标窗口
   - 定位字段
   - 输入文本
4. 如果已有真实控件信息，再去“细分控件清单”中补齐或替换默认控件。
5. 每次新增后先单步验证，不要一上来跑整段流程。

## 常见误区

### 误区 1：只按名字定位

错误示例：

```json
"targetMethod": "name",
"targetValue": "文件名(N):"
```

风险：

- 容易命中左侧标签 `Static`
- 也容易命中文字层，而不是实际可编辑控件

### 误区 2：把输入步骤配成点击

错误现象：

- 日志显示控件已命中
- 实际没有写入内容

正确做法：

- 输入框用 `type_text`
- 只有真正的按钮或列表项才用 `click`

### 误区 3：WPF 下拉抓成 TextBlock

风险：

- 只能点到显示文字
- 不能真正选中列表项

正确做法：

- 优先抓 `ListBoxItem` / `MenuItem`
- 不行就切相对区域

## 单步验证建议

1. 新增完先只跑当前步骤。
2. 如果日志成功但界面没变化，优先检查：
   - 是否命中了同名标签
   - 是否抓到了文字层
   - 是否缺少 `Edit` / `Button` / `ListBoxItem` 这类类型约束
3. 如果 WPF 列表项或弹窗控件总是漂，优先改相对区域，不要持续硬顶纯控件定位。

## 当前编辑器中的快捷入口

现在编辑器左侧已新增 `模板新增` 按钮，内置 4 种高频模板：

- `新增按钮`
- `新增输入框`
- `新增下拉项`
- `新增相对区域`

建议默认优先从这些模板开始，再改少量特有参数。
