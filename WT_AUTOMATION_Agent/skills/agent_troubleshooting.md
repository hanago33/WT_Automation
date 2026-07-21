# Agent 常见问题与解决方案（Agent Troubleshooting）

> 汇总 Agent 使用过程中的常见问题及其解决方案。

## 一、连接与配置问题

### 1.1 API 连接失败

**症状**：点击「连接」按钮后显示红色错误

**可能原因**：
1. Base URL 格式错误
2. API Key 无效或过期
3. 网络无法访问 API 服务器

**解决方案**：

```bash
# 检查 1：确认 URL 格式（必须包含协议和端口）
# ✅ 正确
https://api.deepseek.com/v1
http://localhost:11434/api

# ❌ 错误
api.deepseek.com          # 缺少协议
https://api.deepseek.com  # 缺少路径

# 检查 2：如果是 Ollama，确认服务已启动
ollama serve

# 检查 3：测试 API 连通性
curl http://localhost:11434/api/tags
```

### 1.2 模型不支持 Function Calling

**症状**：Agent 返回普通文本而不是结构化 JSON

**解决方案**：
1. 使用支持 function calling 的模型：
   - ✅ DeepSeek Chat（推荐）
   - ✅ GPT-4 / GPT-4 Turbo
   - ✅ Qwen 2.5 + Chat
   - ❌ Llama 2/3（不支持 function calling）

2. 在配置中指定支持的模型名称

### 1.3 响应速度慢

**可能原因**：
1. 网络延迟
2. API 服务器负载高
3. 模型过大

**解决方案**：
1. 使用本地 Ollama 模型（更快）
2. 选择更小的模型（如 qwen2.5:7b）
3. 检查网络连接

---

## 二、对话生成问题

### 2.1 生成的不是期望的 Action

**症状**：Agent 返回的 JSON 结构不对

**原因**：Prompt 表述不够精确

**解决方案**：
1. 明确指定 Action 类型：
   ```
   # ✅ 好：明确指定
   "生成一个 click 动作，点击 id 为 'btnConfirm' 的按钮"
   
   # ❌ 差：模糊描述
   "点击确定按钮"
   ```

2. 提供更多上下文：
   ```
   "在新建工程对话框中，点击确定按钮"
   ```

3. 使用示例引导：
   ```
   "像这样生成步骤：
   {\"action\": \"click\", \"targetValue\": \"确定\"}"
   ```

### 2.2 步骤定位不准确

**症状**：生成的 targetMethod 在实际运行中找不到控件

**常见错误**：
| 错误写法 | 正确写法 |
|----------|----------|
| `name` | `name,Edit` 或 `name,Button` |
| `"确定"` | `"确定,Button"` |
| `automationId: "btn"` | `automationId,Button` |

**解决方案**：
1. 使用 `/schemas` 查看正确的字段名
2. 在 WT_Automation 中用录制器获取准确的控件信息
3. 参考 `control_maps/` 中的已有定义

### 2.3 参数传递错误

**症状**：`${stepParams.xxx}` 没有被正确替换

**检查项**：
1. JSON 中使用双引号 `"${stepParams.xxx}"` 而不是单引号
2. 变量名拼写正确
3. stepParams 中已定义该参数

```json
// ✅ 正确
{
  "stepParams": {
    "projectName": "MyProject"
  },
  "actionConfig": {
    "action": "type_text",
    "text": "${stepParams.projectName}"
  }
}

// ❌ 错误
{
  "stepParams": {},
  "actionConfig": {
    "text": "${stepParams.projectName}"  // stepParams 中没有定义
  }
}
```

---

## 三、会话管理问题

### 3.1 会话丢失

**症状**：刷新页面后之前的对话不见了

**原因**：浏览器缓存或存储问题

**解决方案**：
1. 检查 `WT_AUTOMATION_Agent/history/` 目录是否存在
2. 确认有写入权限
3. 不要删除 history 目录

### 3.2 会话列表不刷新

**症状**：新建会话后列表没更新

**解决方案**：
1. 点击侧边栏的会话列表区域手动刷新
2. 刷新浏览器页面
3. 重启 Agent 服务

### 3.3 无法删除会话

**症状**：点击删除后会话仍然存在

**解决方案**：
1. 检查网络请求是否成功（F12 控制台）
2. 确认 API 端点工作正常
3. 重启服务后重试

---

## 四、输出格式问题

### 4.1 返回 Markdown 而不是 JSON

**原因**：Agent 判断不需要 JSON 输出

**解决方案**：
1. 明确要求 JSON：
   ```
   "请以 JSON 格式返回步骤定义"
   ```

2. 指定格式：
   ```
   "返回格式：{\"action\": \"xxx\", \"targetMethod\": \"xxx\"}"
   ```

### 4.2 JSON 格式错误

**症状**：返回的 JSON 无法解析

**常见错误**：
1. 使用了单引号 `'`
2. 注释没有去除
3. 尾逗号

**解决方案**：
1. 要求返回纯 JSON（不带 Markdown 代码块）
2. 使用 `/clear` 重新开始对话
3. 分步骤生成

### 4.3 输出被截断

**症状**：长输出只显示一部分

**解决方案**：
1. 分段生成：
   ```
   "先生成第一步"
   "再生成第二步"
   ```

2. 使用序列模式批量生成

---

## 五、性能问题

### 5.1 内存占用高

**症状**：长时间使用后浏览器变卡

**解决方案**：
1. 定期清空对话（`/clear`）
2. 删除不需要的会话
3. 限制会话历史长度

### 5.2 加载历史会话慢

**原因**：历史记录太多

**解决方案**：
1. 删除旧的会话
2. 减少保留的会话数量

---

## 六、自定义扩展问题

### 6.1 添加的 Skill 不生效

**检查项**：
1. 文件扩展名是 `.md`
2. 放在 `WT_AUTOMATION_Agent/skills/` 目录
3. 重启 Agent 服务

### 6.2 新 Action 类型不识别

**检查项**：
1. 已在 `wt_action_schema.py` 中定义
2. Schema 包含必要字段（action, label, description）
3. 重启 Agent 服务

### 6.3 自定义 API 后端不工作

**检查项**：
1. API 兼容 OpenAI Chat Completions 格式
2. 支持 function calling / tools
3. CORS 配置正确

---

## 七、日志与调试

### 7.1 查看服务端日志

Agent 服务会输出日志到控制台：
```bash
python -m WT_AUTOMATION_Agent.agent
```

### 7.2 浏览器开发者工具

按 F12 打开开发者工具：
- **Console**：查看 JavaScript 错误
- **Network**：查看 API 请求响应
- **Sources**：调试前端代码

### 7.3 常见错误码

| HTTP 状态码 | 含义 |
|-------------|------|
| 200 | 成功 |
| 400 | 请求格式错误 |
| 401 | API Key 无效 |
| 404 | 端点不存在 |
| 500 | 服务器内部错误 |
| 503 | 服务不可用 |

---

## 八、快速检查清单

使用 Agent 前确认：

- [ ] Base URL 正确（包含协议和路径）
- [ ] API Key 有效
- [ ] 网络可以访问 API
- [ ] 使用支持的模型
- [ ] 了解所需的 Action 类型
- [ ] 参考 `/schemas` 了解可用字段

---

## 九、联系与反馈

如果遇到无法解决的问题：

1. 查看 `WT_AUTOMATION_Agent/docs/` 目录的文档
2. 检查 `WT_AUTOMATION_Agent/skills/` 中的知识文档
3. 查看 `tests/` 目录的测试用例
