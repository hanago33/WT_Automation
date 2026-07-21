# WT_Automation Agent 技术架构（Agent Technical Architecture）

> 描述 DSL Agent 的内部架构、能力边界和使用技巧，帮助 Agent 更好地理解和生成自动化流程。

## 一、系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        DSL Agent                                 │
├──────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │   gui.py    │───▶│  agent.py   │───▶│   LLM API (外部)     │  │
│  │  (HTTP服务)  │    │  (核心逻辑)  │    │  DeepSeek/GPT/Qwen  │  │
│  └─────────────┘    └─────────────┘    └─────────────────────┘  │
│         │                  │                                       │
│         │          ┌──────┴──────┐                                │
│         │          ▼             ▼                                │
│         │    ┌───────────┐ ┌────────────┐                        │
│         │    │history_store│ │  skill.py  │                        │
│         │    │ (会话存储)   │ │ (知识加载)  │                        │
│         │    └───────────┘ └────────────┘                        │
│         │                  │                                       │
│         ▼                  ▼                                       │
│  ┌─────────────────────────────────────┐                          │
│  │        WT_Automation Core           │                          │
│  │  wt_action_schema.py / wt_business_steps.py                   │
│  └─────────────────────────────────────┘                          │
└──────────────────────────────────────────────────────────────────┘
```

## 二、核心组件

### 1. gui.py（HTTP 服务 + 前端）
- **端口**：8765
- **功能**：提供 Web UI + REST API
- **API 端点**：
  - `POST /api/chat` - 发送消息，获取 AI 响应
  - `GET /api/conversations` - 获取会话列表
  - `POST /api/conversations` - 创建新会话
  - `GET /api/conversations/{id}` - 获取会话详情
  - `PATCH /api/conversations/{id}` - 更新会话（重命名）
  - `DELETE /api/conversations/{id}` - 删除会话
  - `GET /api/schemas` - 获取所有 Action 类型定义
  - `GET /api/skills` - 获取已加载的 Skill 列表

### 2. agent.py（核心逻辑）
- **DslAgent 类**：处理对话和流程生成
- **DslAgentConfig 类**：配置管理
- **核心方法**：
  - `chat()` - 处理对话，返回 AI 响应
  - `create_conversation()` / `list_conversations()` / `load_conversation()` 等 - 会话管理
  - `generate_step()` - 生成单步流程
  - `generate_flow()` - 生成完整流程

### 3. history_store.py（会话存储）
- **存储位置**：`WT_AUTOMATION_Agent/history/`
- **文件格式**：JSON 文件，每个会话一个文件
- **字段**：
  ```json
  {
    "id": "uuid",
    "title": "会话标题",
    "created_at": "ISO时间",
    "updated_at": "ISO时间",
    "messages": [
      {"role": "user", "content": "...", "timestamp": "..."},
      {"role": "assistant", "content": "...", "extra": {...}}
    ]
  }
  ```

### 4. skill.py（知识管理）
- **位置**：`WT_AUTOMATION_Agent/skills/`
- **功能**：加载和检索 Skill/Markdown 知识文档
- **自动加载**：启动时自动加载所有 `.md` 文件作为上下文

## 三、Action Schema 系统

### 常用 Action 类型

| Action | 说明 | 关键参数 |
|--------|------|----------|
| `click` | 点击控件 | `targetMethod`, `uiPath`, `ancestors` |
| `type_text` | 输入文本 | `targetMethod`, `targetValue`, `text` |
| `select_dropdown` | 选择下拉项 | `targetMethod`, `targetValue` |
| `menu_select` | 选择菜单 | `targetValue` (格式: "菜单项,子菜单") |
| `click_relative_region` | 相对区域点击 | `relativeRegion`, `relativeRegionAnchor` |
| `wait_for_control` | 等待控件出现 | `targetMethod`, `timeout` |
| `validate_text` | 验证文本 | `targetMethod`, `expectedText` |
| `get_control_info` | 获取控件信息 | `targetMethod`, `propertyName` |

### targetMethod 定位语法

```
"Name"                    # 仅名称
"name,Edit"               # 名称 + 类型
"name,Edit,class_name"     # 名称 + 类型 + 类名
"automationId,Button"     # 自动化ID + 类型
```

### uiPath 路径语法

```
"窗口标题.控件名"                    # 简单路径
"窗口标题.容器.控件名"               # 带容器
"窗口标题.#Pane.控件名"             # 使用控件类型
```

## 四、Prompt 工程

### System Prompt 结构

```
你是 WT_Automation DSL Agent，专门帮助用户生成自动化流程。

## 可用 Skill
{skills 目录中的内容}

## 你的能力
1. 理解自然语言描述的操作步骤
2. 转换为 WT_Automation 流程步骤 JSON
3. 验证步骤的正确性和完整性

## 输出格式
- 单步：JSON 对象
- 多步：JSON 数组
- 对话：Markdown 文本

## 约束
- 使用标准的 Action 类型
- 包含必要的 stepParams
- 保持步骤的可执行性
```

### Few-shot 示例

```json
// 示例：点击新建工程
{
  "stepParams": {},
  "actionConfig": {
    "action": "menu_select",
    "targetValue": "文件,新建工程"
  }
}

// 示例：输入项目名称
{
  "stepParams": {
    "projectName": "${runtime.projectName}"
  },
  "actionConfig": {
    "action": "type_text",
    "targetMethod": "name,Edit",
    "targetValue": "项目名称,Edit",
    "text": "${stepParams.projectName}"
  }
}
```

## 五、参数传递机制

### stepParams（步骤参数）
```json
{
  "stepParams": {
    "projectName": "MyProject",
    "outputPath": "D:/Projects"
  }
}
```

### 模板变量
| 变量 | 说明 | 示例 |
|------|------|------|
| `${stepParams.xxx}` | 步骤参数 | `${stepParams.projectName}` |
| `${runtime.xxx}` | 运行时变量 | `${runtime.outputDir}` |
| `${flowRefParams.xxx}` | 子流程参数 | `${flowRefParams.turbineModel}` |

### 绝对路径 vs 相对路径
- **绝对路径**：`D:/Projects/output` 或 `%USERPROFILE%/Documents`
- **相对路径**：`${runtime.outputDir}/results.csv`

## 六、最佳实践

### 1. 控件定位优先级
1. ✅ `automationId` - 最稳定（WPF 控件的固有 ID）
2. ✅ `name + control_type` - 如 `name,Edit`
3. ⚠️ `uiPath` - 方便但脆弱（依赖窗口层次）
4. ❌ `coordinates` - 避免使用绝对坐标

### 2. 步骤粒度
- **推荐**：每步一个明确操作
- **避免**：一个步骤做多件事
- **示例**：
  ```json
  // ✅ 好：一个步骤做一件事
  {"action": "click", "targetValue": "确定"}
  {"action": "type_text", "text": "内容"}

  // ❌ 不好：一个步骤做多件事
  {"action": "click_and_type", "target": "输入框", "text": "内容"}
  ```

### 3. 等待策略
```json
// 推荐：显式等待
{
  "action": "wait_for_control",
  "targetMethod": "name,Edit",
  "timeout": 5000
}

// 避免：无等待直接操作
```

### 4. 验证点设置
```json
// 推荐：关键操作后验证
{
  "action": "validate_text",
  "targetMethod": "name,Static",
  "expectedText": "工程创建成功"
}
```

## 七、错误处理

### 常见错误码

| 错误码 | 含义 | 解决方案 |
|--------|------|----------|
| `CONTROL_NOT_FOUND` | 控件未找到 | 检查 targetMethod 是否正确 |
| `WINDOW_NOT_FOUND` | 窗口未找到 | 检查窗口是否已打开 |
| `INPUT_FAILED` | 输入失败 | 检查焦点是否正确 |
| `TIMEOUT` | 操作超时 | 增加 timeout 或检查应用状态 |

### 调试技巧
1. 使用 `/schemas` 命令查看所有可用 Action
2. 先在 WT_Automation 中手动测试操作
3. 使用单步模式逐步验证
4. 查看 `control_maps/` 目录中的控件定义

## 八、与 WT_Automation 集成

### 数据流
```
用户对话 → Agent 解析 → 生成步骤 JSON
         ↓
   WT_Automation 执行
         ↓
   结果反馈给用户
```

### 流程包导出
Agent 生成的步骤可以：
1. **直接复制**：复制 JSON 到剪贴板
2. **导出 JSON**：保存为 `.json` 文件
3. **导入 Excel**：通过 `flow_excel_io.py` 导入到 Excel

### Flow Package 结构
```json
{
  "flowName": "新建工程流程",
  "version": "1.0",
  "steps": [
    {"stepParams": {}, "actionConfig": {...}},
    {"stepParams": {}, "actionConfig": {...}}
  ]
}
```

## 九、扩展开发

### 添加新 Action 类型
1. 在 `wt_action_schema.py` 中定义 Schema
2. 在 `wt_business_steps.py` 中实现执行逻辑
3. 在 Agent 的 System Prompt 中添加说明

### 添加新 Skill
1. 在 `WT_AUTOMATION_Agent/skills/` 创建 `.md` 文件
2. 使用标准格式（见现有文件）
3. 重启 Agent 服务自动加载

### 自定义 API 后端
修改 `DslAgentConfig` 中的 `base_url` 指向其他 LLM API：
- OpenAI Compatible API
- Ollama (本地模型)
- DeepSeek API
- 其他支持 function calling 的 API
