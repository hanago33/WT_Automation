# 控件模板自动采集 / 关联 / 批量导入（Template Auto-Association）

> 从 P0/P1/P2 实战沉淀的图片模板体系标准流程。覆盖：执行中自动截图采集与替换更新、
> 生成/保存流程时自动关联 `fallbackTemplate`、存量录制截图批量归档、开关配置。可反复调用。

## 适用场景

- 需要给流程步骤配上模板兜底（图片匹配），但不想逐个手工截图、命名、导入
- 步骤执行失败想用"模板匹配"兜底（`fallbackMode=template_match` / `onError=fallback`）
- 拿到一批旧录制截图（`recorder_captures/*.png`）要并入统一索引
- 排查"为什么某步骤没有走模板兜底 / 模板未命中 / 模板被错误覆盖"

## 模板索引体系（image_template_index）

统一索引 `build_index()` 把四类来源收进 `{key: 绝对路径}`：

| 来源 | 目录 | 说明 |
|---|---|---|
| 采集器索引 | `image_templates/<子目录>/templates_index.json` | 手工/采集器维护，含 category/file_name/image_path |
| 录制伴随拾取 | `image_templates/recorder_captures/*.png` | pywinauto_recorder 自动截图（`step_NN_控件名.png`） |
| 执行中自动采集 | `image_templates/auto_captured/<窗口>/<控件名>.png` | P0/P1 运行时截图，按窗口分目录 |
| 顶层历史 | `image_templates/*.png` | 早期 `auto_cap_*` 实验产物 |

- key 兼容多种写法：`recorder_captures/xx.png`、`Icons/xx.png`、`image_templates/...`（相对项目根）、绝对路径
- `get_template_path(key)`：任意引用 → 真实文件路径（找不到返回空串）
- `images_are_similar(a, b)`：pHash + 灰度均值守卫的一致性对比（中文路径已用 imdecode 兼容）
- 新增/删除模板后调用 `reload()` 清缓存，下次扫描生效

## 自动关联机制（两层，互不依赖）

1. **运行时自动接线**（`wt_flow_executor._resolve_template_key_from_controls`）：
   步骤未配置 `fallbackTemplate` 时，执行时从 `controls[].templateKey` 自动解析模板。
2. **生成/保存时自动写入**（P2-1，`image_template_index.auto_associate_fallback_templates`）：
   编辑器 `WT_Flow_Editor._save_to` 与 Agent `/api/flow/save` 保存时，
   若步骤未显式配置 `fallbackTemplate` 且控件存在可解析 `templateKey`，自动写入
   相对项目根路径（`image_templates/...`），不覆盖用户已有配置（非破坏性）。

模板写入落盘后，执行器 `resolve_fallback_template_path` 按项目根拼接即可命中。

## 自动更新开关（GUI 勾选，无需手改配置）

- 总控台"测试"区域勾选 **自动更新控件模板** → 子进程环境变量 `WT_TEMPLATE_AUTO_UPDATE=true`
- 运行时（`WT_AUT_recorded`）：`templateAutoUpdate` 默认 **false**（优先级 环境变量 > runtimeConfig > 默认）
- 勾选后每次运行：步骤成功 / 模板兜底失败 → 自动截图 → 与上次模板 `images_are_similar` 对比，
  **一致保留（防止假成功截图覆盖）、不一致才替换** → 回写 `control["templateKey"]` → `reload()`
- 不勾选：完全不执行截图模板保存与兜底编辑

## 存量批量导入（recorder_captures → auto_captured）

```bash
python build_auto_capture_index.py            # 实际执行
python build_auto_capture_index.py --dry-run  # 仅预览
```

- 复制到 `auto_captured/recorder_legacy/<同名>.png`，**不删除原文件**（旧 flow 的 templateKey 引用仍有效）
- 目标已存在且一致 → 跳过；不一致 → 报告冲突不覆盖（防止破坏已有模板）

## 校验与调试锚点

- `image_template_index.resolve_fallback_template(step)`：单步骤能解析出哪个模板
- `image_template_index.get_template_path(key)`：某 key 是否真实存在
- `image_template_index.build_index()` / `summary()`：索引规模与根目录
- `wt_flow_executor.resolve_fallback_template_path(path)`：落盘路径能否被执行器解析
- `wt_flow_validation` / `flow_audit.audit_flow`：步骤配置最终校验

## 高频问题速查

| 现象 | 大概率原因 | 修复 |
|---|---|---|
| 步骤没走模板兜底 | `fallbackTemplate` 未写入 / key 解析不到文件 | 检查控件 `templateKey` 是否真实对应 png；保存时 `auto_associate_fallback_templates` |
| 模板优先命中失败回退主流程 | 截图与当前界面差异大 / 模板过旧 | 勾选"自动更新控件模板"重跑，自动替换新截图 |
| 模板被假成功覆盖 | 首次运行截图不准确 | 替换机制：不一致才替换（默认机制已防呆） |
| 中文文件名对比异常 | `cv2.imread` 中文路径不可靠 | 已改 `np.fromfile + imdecode`；复现时确认文件存在 |
| 重复导入全报"冲突" | 对比函数读图失败 | 确认修复后的 `images_are_similar`（WARN 日志不应再出现乱码路径） |

## 沉淀经验

- 模板路径写入 flow 文件统一用相对项目根（`image_templates/...`），保证文件可移植
- 自动采集永远"对比后替换"，宁可保留旧模板也不让错误截图破坏可用模板
- 老式语义 ID（`config_button` 等）不是文件路径，`resolve_fallback_template` 会安全跳过
