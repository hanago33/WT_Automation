# 卷七 · API 层、断言体系与运行时工程机制

> 覆盖模块：core/api.py（781 行）、core/assertions.py（257 行）、utils/retry.py（63 行）、core/device.py（99 行）、utils/snippet.py（198 行，节选）。
> 本卷回答：Airtest 怎么把"算法 + 设备"组装成 20 个用户 API；断言体系如何与报告联动；重试怎么做。

## 1. API 层的三个组合模式（api.py）

### 1.1 目标多态：`Template | 坐标` 双类型入参

```python
def touch(v, times=1, **kwargs):                       # api.py:330-376
    if isinstance(v, Template):
        pos = loop_find(v, timeout=ST.FIND_TIMEOUT)    # 图像目标 → 先定位
    else:
        try_log_screen()                               # 坐标目标 → 只留证据
        pos = v
    for _ in range(times):
        pos = G.DEVICE.touch(pos, **kwargs) or pos
        time.sleep(0.05)                               # 连点多击间隔 50ms
    delay_after_operation()                            # OPDELAY 0.1s 全局节流
    return pos

click = touch                                          # api.py:378，别名即文档
```

所有交互 API（touch/double_click/swipe）共用这一模式：**"目标"是一个统一概念**——可以是 Template（先 loop_find 再操作）也可以是坐标（直接操作）。`swipe` 的两端还可以分别是 Template 和坐标（api.py:404-465）。

### 1.2 四态等待语义表（WT 统一目标抽象的字段级蓝本）

| API | 超时 | 找到时返回 | 未找到时 | 证据截图 |
|---|---|---|---|---|
| `touch(Template)` | `FIND_TIMEOUT`=20s | 最终点击坐标 | 抛 `TargetNotFoundError` | 成功存图 |
| `wait(v, timeout, interval, intervalfunc)` | 参数或 20s | 命中坐标 | 抛 `TargetNotFoundError` | 成功存图 |
| `exists(v)` | `FIND_TIMEOUT_TMP`=**3s** | 命中坐标 | 返回 **False**（不抛） | 成功存图 |
| `assert_exists(v)` | 20s | 命中坐标 | 转 `AssertionError`（报告判败） | 成功存图 |
| `assert_not_exists(v)` | 3s | — | 找到反而抛 `AssertionError` | — |

四态设计的关键区分：**touch/wait 用长超时+异常**（目标"应该"出现，不出现是故障）；**exists 用短超时+布尔**（目标"可能"出现，用于分支判断）；**assert 系列**把图像查找失败翻译成断言语义（`assertions.py:26-30`），且使用**专门的更严格阈值** `ST.THRESHOLD_STRICT`（settings.py:20，默认 None 时回退模板自身阈值）——"验证用阈值"与"操作用阈值"分离。

`exists` 的官方推荐双用模式（api.py:630-638 docstring）：

```python
pos = exists(Template(...))    # 3 秒内找一次
if pos:
    touch(pos)                 # 直接用返回的坐标，省掉第二次全图搜索
```

【WT 启示】WT 的 `wt_action_defaults` 已有等待默认值体系，但"操作/检查/断言"三种语义共用同一套阈值与异常行为。对照改造：`wait_template`（阶段 C）应同时提供 `wait_template`（长超时抛异常）与 `template_exists`（短超时返 bool，返回值带中心坐标）两个入口，断言类 action 用 `THRESHOLD_STRICT` 式独立阈值字段。

### 1.3 报告导向的防御性编码（api.py:439-445）

```python
except TargetNotFoundError:
    # 如果由图1滑向图2，图1找不到，会导致图2的文件路径未被初始化，
    # 可能在报告中不能正确显示
    if v2 and isinstance(v2, Template):
        v2.filepath          # 仅为了报告能渲染图2的路径
    raise
```

异常路径上专门补一行"只为报告好看"的代码——报告渲染所需的数据在**失败路径也要备齐**。WT 的 run report 若要记录"失败步骤当时的目标模板图"，需要同款考虑。

## 2. 连接与环境装配

- **URI 语法**（api.py:49-75）：`Windows:///`（桌面）、`Windows:///123456`（窗口句柄）、`windows:///?title_re='.*explorer.*'`（**标题正则连接**）、`Windows:///123456?foreground=False`（连接不抢前台）。`parse_device_uri`（snippet.py:158-176）解析 `平台://host:port/uuid?k=v&k2=v2`。
- **auto_setup**（api.py:117-145）：basedir（进 `G.BASEDIR` 图像解析链）+ logdir + devices + project_root + compress 五项一键装配。
- `set_current(idx)`（api.py:90-114）多设备切换（uuid 或序号）。

WT 已有总控台/队列体系管理环境，无需 URI 层；但 `title_re` 连接参数对 `wt_window_helpers` 的窗口查找参数化有参考价值。

## 3. 断言体系（assertions.py，257 行 / 18 个断言）

- 全部 `@logwrap` 装饰 → 每条断言自动进报告（参数值+结果+耗时）；
- `assert_exists`：`loop_find(timeout=FIND_TIMEOUT, threshold=ST.THRESHOLD_STRICT or v.threshold)`——**断言可配独立严格阈值**；
- `assert_not_exists` 的写法值得注意（assertions.py:47-51）：在 `try` 里找到就 `raise AssertionError`，靠 `except TargetNotFoundError: pass` 表达"没找到才对"——语义正确但可读性一般，落地时建议用 `exists()` 反写；
- 数值/身份类断言（assert_equal 等 14 个）就是 `assert` 语句 + `@logwrap` + msg——**报告价值完全来自装饰器**，本体一行。这个"业务逻辑一行、审计三件套免费"的模式是 `@logwrap` 设计威力的最好注脚。

【WT 启示】WT 流程编辑器若有"校验步骤"类 action，其判定结果与参数值应经同一审计管线落 run report——`@logwrap` 模式（`inspect.getcallargs`）在卷三已详述。

## 4. 重试装饰器（utils/retry.py，63 行）

```python
def retries(max_tries, delay=1, backoff=2, exceptions=(Exception,), hook=None):
    for tries_remaining in reversed(range(max_tries)):
        try:
            return func(*args, **kwargs)
        except exceptions as e:
            if tries_remaining > 0:
                hook(tries_remaining, e, mydelay)   # 可选：重试前回调（记日志/自救）
                time.sleep(mydelay)
                mydelay *= backoff                   # 指数退避
            else:
                raise
```

要点：①**按异常类型过滤**（`exceptions` 元组）——只有指定异常才重试，其他直接穿透；②指数退避 `delay×backoff^k`；③`hook(tries_remaining, e, delay)` 重试前回调。这与影刃对标 P0-3"重试升级"的需求完全同构，**63 行即可整体移植**（比影刀的实现更小更清晰），与 `TemplateNotFoundError` 组合即成图像等待的重试标准件：`@retries(3, delay=1, backoff=2, exceptions=(TemplateNotFoundError,))`。

## 5. 设备注册表（core/device.py，99 行）

```python
class MetaDevice(type):
    REGISTRY = {}
    def __new__(meta, name, bases, class_dict):
        cls = type.__new__(meta, name, bases, class_dict)
        meta.REGISTRY[name] = cls      # 每个 Device 子类定义即自动注册
        return cls
```

- 子类定义即注册（类名→类），`import_device_cls` 之外的另一条按名取类路径；
- `paste()` 的默认实现：`self.text(self.get_clipboard())`（:91-92）——**平台无关的粘贴语义**（Windows 端覆写为 `^v`，Android 端走 Yosemite IME）；
- 未实现的方法统一 `_raise_not_implemented_error()` 报"Method not implemented on <平台>"——错误信息自带平台上下文。

## 6. snippet.py 工具节选（198 行）

| 函数 | 作用 | WT 参考价值 |
|---|---|---|
| `reg_cleanup`（:46-59） | 注册进程退出清理回调（atexit 兜底） | 队列 worker 的资源清理可对照 |
| `kill_proc`（:69-88） | Windows taskkill /T /F 树杀（含等待重试） | 子进程（UI-TARS runner）超时后处理参考 |
| `parse_device_uri`（:158-176） | URI 解析 | — |
| `get_absolute_coordinate`（:191-198） | 相对坐标 (0-1 浮点)→像素（按设备当前分辨率） | WT 步骤参数若引入相对坐标可对照此语义 |
| `split_cmd`（:13-25） | Windows 命令行安全拆分 | subprocess 参数化参考 |

## 7. 本卷小结：WT 可吸收项

| # | 机制 | 证据 | WT 落点 |
|---|---|---|---|
| 1 | touch/wait/exists/assert 四态语义 | api.py:330-647, assertions.py:26-30 | 阶段 C 的 `wait_template`/`template_exists` 双入口 + 断言独立阈值 |
| 2 | exists 坐标复用省一次搜索 | api.py:630-638 | `template_exists` 返回 MatchResult 而非 bool |
| 3 | `retries` 装饰器（63 行） | retry.py 全文 | P0-3 重试升级的现成标准件 |
| 4 | 失败路径也备齐报告数据 | api.py:439-445 | run report 失败步骤记录目标模板 |
| 5 | `title_re` 式参数化窗口查找 | api.py:65, snippet.py:158 | wt_window_helpers 参数扩展 |
| 6 | MetaDevice 类注册表 | device.py:5-12 | 若引入"输入通道注册表"（卷八）可用同款 |
