# encoding: utf-8
"""WT Automation 统一日志基础设施（P0 立规）。

背景
----
改造前主链路（``wt_flow_locator`` / ``wt_flow_executor`` / ``WT_AUT_recorded``）没有日志级别
概念，所有行都是 ``[时间] 自由文本``；级别由 4 个各自独立的关键字分类器在 UI 侧猜测，
规则互不一致 —— 且 ``已剔除错误项=0`` 因含「错误」二字被误判为 error（红字）。

本模块是日志级别、行格式与配色语义的**单一事实来源**：

1. 五级枚举：``DEBUG / INFO / WARN / ERROR / CRIT``，定宽 5 字符，便于列对齐与 grep。
2. 统一行格式：``[YYYY-MM-DD HH:MM:SS.mmm] [LEVEL] [module] 消息``。
3. 单点分类 ``classify()``：写入方应显式传级别；对历史遗留纯文本行提供关键字兜底。
4. 级别开关：环境变量 ``WT_LOG_LEVEL``（默认 INFO）；``WT_DEBUG_EVENTS=1`` 亦视为 DEBUG，
   与既有的 ndjson 调试事件开关保持一致。
5. 色板：从 ``wt_theme`` 取（浅底/深底两组，语义色相一致、仅调亮度）。

设计约束
--------
- **不 import tkinter**：本模块会被 headless 流程、CLI 与队列服务导入。
- 颜色延迟查询，避免 import 期副作用。
- 仅依赖标准库，可在无第三方包环境下导入。
"""

import json
import os
import re
import threading
import time

# ── 级别枚举 ────────────────────────────────────────────────────────────────
DEBUG = "DEBUG"
INFO = "INFO"
WARN = "WARN"
ERROR = "ERROR"
CRIT = "CRIT"

LEVELS = (DEBUG, INFO, WARN, ERROR, CRIT)

# 级别权重（用于阈值比较）
_LEVEL_RANK = {DEBUG: 10, INFO: 20, WARN: 30, ERROR: 40, CRIT: 50}

# 输入别名 → 规范级别（兼容 WARNING / FATAL / CRITICAL 等常见写法）
_LEVEL_ALIASES = {
    "DEBUG": DEBUG,
    "TRACE": DEBUG,
    "INFO": INFO,
    "NOTICE": INFO,
    "WARN": WARN,
    "WARNING": WARN,
    "ERROR": ERROR,
    "ERR": ERROR,
    "CRIT": CRIT,
    "CRITICAL": CRIT,
    "FATAL": CRIT,
}

# 定宽 5 字符的级别标签（CRIT 取 syslog 惯例缩写，保证列对齐）
_LEVEL_LABELS = {
    DEBUG: "DEBUG",
    INFO: "INFO ",
    WARN: "WARN ",
    ERROR: "ERROR",
    CRIT: "CRIT ",
}

# 级别 → UI 标签（沿用既有分类器的标签集，替换后渲染行为不变）
_TAG_BY_LEVEL = {
    DEBUG: "debug",
    INFO: "info",
    WARN: "warning",
    ERROR: "error",
    CRIT: "error",
}

_ENV_LEVEL = "WT_LOG_LEVEL"
_ENV_DEBUG_EVENTS = "WT_DEBUG_EVENTS"

_min_level_cache = None


def normalize_level(level, default=INFO):
    """把任意写法规范成五级之一；无法识别时返回 ``default``。"""
    if level is None:
        return default
    token = str(level).strip().upper()
    if not token:
        return default
    return _LEVEL_ALIASES.get(token, default)


def level_rank(level):
    """返回级别权重，未知级别按 INFO 处理。"""
    return _LEVEL_RANK[normalize_level(level, default=INFO)]


def tag_for_level(level):
    """级别 → UI 标签（info / success / warning / error / debug / system）。"""
    return _TAG_BY_LEVEL.get(normalize_level(level, default=INFO), "info")


# ── 级别开关 ────────────────────────────────────────────────────────────────
def resolve_min_level():
    """解析当前生效的最低输出级别。

    优先级：``WT_LOG_LEVEL`` 显式指定 > ``WT_DEBUG_EVENTS=1`` 视为 DEBUG > 默认 INFO。
    结果会缓存，测试或运行时改环境后请调用 :func:`reset_min_level`。
    """
    global _min_level_cache
    if _min_level_cache is not None:
        return _min_level_cache
    raw = (os.environ.get(_ENV_LEVEL) or "").strip()
    resolved = normalize_level(raw, default=None) if raw else None
    if resolved is None:
        resolved = DEBUG if os.environ.get(_ENV_DEBUG_EVENTS) == "1" else INFO
    _min_level_cache = resolved
    return resolved


def set_min_level(level):
    """显式设置最低输出级别（供运行时/测试使用）。"""
    global _min_level_cache
    _min_level_cache = normalize_level(level, default=INFO)
    return _min_level_cache


def reset_min_level():
    """清除缓存，下次 :func:`resolve_min_level` 重新读环境变量。"""
    global _min_level_cache
    _min_level_cache = None


def is_enabled(level):
    """判断该级别在当前阈值下是否应输出。"""
    return level_rank(level) >= level_rank(resolve_min_level())


def is_debug_enabled():
    """DEBUG 是否开启（供高频诊断转储做门控）。"""
    return is_enabled(DEBUG)


# ── 运行标识（runId） ──────────────────────────────────────────────────────
# runId 由 wt_run_reporting.start_run_report() 生成（形如
# `wt_run_20260917_102341_123_001`），此处只做进程内登记，不重复生成。
_run_id = ""


def set_run_id(run_id):
    """登记本次运行的 runId（供 JSONL 记录与运行边界标记使用）。"""
    global _run_id
    _run_id = str(run_id or "")
    return _run_id


def get_run_id():
    """返回当前登记的 runId；未登记时为空串。"""
    return _run_id


def clear_run_id():
    global _run_id
    _run_id = ""


def format_run_banner(kind, run_id=None, step_count=None, status=None, elapsed_seconds=None, extra=None):
    """生成人读友好的运行边界标记。

    作用：`wt_automation.log` 是跨多次运行追加的单一文件，长跑后难以一眼分辨
    运行边界。边界标记给出可 grep 的分隔行（`运行开始` / `运行结束`），
    并把 runId 落在人读日志里，便于与运行报告、JSONL 旁路互相对照。

    刻意不重复时间戳（行首已有），避免视觉冗余。
    ``extra`` 供调用方补充一句摘要（如「成功 111 / 失败 0」）。
    """
    parts = []
    if kind == "start":
        parts.append("运行开始")
        rid = run_id if run_id is not None else _run_id
        if rid:
            parts.append("runId={}".format(rid))
        if step_count is not None:
            parts.append("待执行 {} 步".format(step_count))
    else:
        parts.append("运行结束")
        if status:
            parts.append("状态={}".format(status))
        if elapsed_seconds is not None:
            parts.append("用时 {:.1f}s".format(float(elapsed_seconds)))
        if step_count is not None:
            parts.append("步骤 {} 步".format(step_count))
    if extra:
        parts.append(str(extra))
    return "========== {} ==========".format(" · ".join(parts))


# ── JSONL 旁路（机读镜像） ──────────────────────────────────────────────────
# 设计：文本日志为人读而精简，JSONL 为机读而完整。两者同源同时写，
# 因此「精简文本」与「保留全量调试数据」不再互相冲突。
# 每次运行一个文件（logs/run_logs/<runId>.jsonl），天然按运行分片、无需轮转。
_jsonl_path = ""
_jsonl_lock = threading.Lock()

# 文本 → 结构化 的桥接：项目日志的消息格式统一为 `step=<id>, control=<id>`。
# 解析失败只是缺字段，不影响其它字段；需要可靠字段的新调用点应直接传
# step_id / control_id 给 log_event。
_STEP_FIELD_RE = re.compile(r"\bstep=([^,\s]+)")
_CONTROL_FIELD_RE = re.compile(r"\b(?:control|ctrl|anchor)=([^,\s]+)")


def start_jsonl(path):
    """开启 JSONL 旁路；返回实际生效的路径（不可用时为空串）。"""
    global _jsonl_path
    candidate = str(path or "")
    if candidate:
        try:
            parent = os.path.dirname(candidate)
            if parent:
                os.makedirs(parent, exist_ok=True)
        except Exception:
            candidate = ""
    _jsonl_path = candidate
    return _jsonl_path


def stop_jsonl():
    global _jsonl_path
    _jsonl_path = ""


def jsonl_path():
    return _jsonl_path


def extract_step_fields(message):
    """从文本日志行里提取 ``(stepId, controlId)``；缺失返回空串。"""
    text = str(message or "")
    step_match = _STEP_FIELD_RE.search(text)
    control_match = _CONTROL_FIELD_RE.search(text)
    return (
        step_match.group(1) if step_match else "",
        control_match.group(1) if control_match else "",
    )


def _iso_millis(timestamp):
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(timestamp))
    millis = int((timestamp - int(timestamp)) * 1000)
    if millis < 0:
        millis = 0
    return "%s.%03d" % (stamp, millis)


def log_event(
    level,
    message,
    event=None,
    step_id=None,
    control_id=None,
    elapsed_ms=None,
    detail=None,
    module=None,
    run_id=None,
    timestamp=None,
):
    """向 JSONL 旁路追加一条结构化记录；未开启旁路时返回 None。

    字段契约（与既有规划中的 ``logs/locator_events.jsonl`` 保持命名一致，
    便于将来两者互相 join）：

    ``ts`` / ``runId`` / ``level`` / ``stepId`` / ``controlId`` /
    ``event`` / ``elapsedMs`` / ``message`` / ``detail``

    为保持文件可读，``stepId`` / ``controlId`` / ``event`` / ``elapsedMs`` /
    ``detail`` 为空时**整字段省略**（而非写 null），使常见行保持简短。

    字段顺序刻意把 ``message`` 放在最后：它是长度最不可控的字段，
    置末可让前面的标识类字段在纯文本浏览时保持列对齐、便于扫读。
    """
    path = _jsonl_path
    if not path:
        return None

    now = time.time() if timestamp is None else float(timestamp)
    if step_id is None or control_id is None:
        auto_step, auto_control = extract_step_fields(message)
        if step_id is None:
            step_id = auto_step
        if control_id is None:
            control_id = auto_control

    record = {"ts": _iso_millis(now), "level": normalize_level(level, default=INFO)}
    rid = run_id if run_id is not None else _run_id
    if rid:
        record["runId"] = str(rid)
    if step_id:
        record["stepId"] = str(step_id)
    if control_id:
        record["controlId"] = str(control_id)
    if event:
        record["event"] = str(event)
    if elapsed_ms is not None:
        record["elapsedMs"] = round(float(elapsed_ms), 2)
    if module:
        record["module"] = str(module)
    if detail:
        record["detail"] = detail
    record["message"] = str(message)

    line = json.dumps(record, ensure_ascii=False)
    with _jsonl_lock:
        try:
            with open(path, "a", encoding="utf-8") as file_obj:
                file_obj.write(line + "\n")
        except Exception:
            return None
    return record


# ── 日志文件轮转 ────────────────────────────────────────────────────────────
# wt_automation.log 是跨运行追加的单一文件，改造前没有任何轮转，会无限增长。
# 轮转策略：按大小切分为 path.1（最近）… path.N（最旧），超出份数的最旧备份
# 被覆盖丢弃 —— 标准 numbered rotation，不额外引入归档目录（那只是多一层
# 间接，收益不足以抵消复杂度）。
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
_rotate_lock = threading.Lock()


def should_rotate(path, max_bytes=None):
    """判断日志文件是否达到轮转阈值。"""
    limit = LOG_MAX_BYTES if max_bytes is None else int(max_bytes)
    try:
        return os.path.getsize(path) >= limit
    except OSError:
        return False


def rotate_log(path, max_bytes=None, backup_count=None):
    """按大小轮转日志文件；返回 True 表示本次发生了轮转。

    调用方在每次追加前调用即可：体积未达阈值时只是一次 ``getsize``（微秒级），
    远低于持有常驻文件句柄的代价 —— 后者在 Windows 上会导致外部删除/清屏
    （``WT_Launcher`` 的"清屏"会 ``os.remove(LOG_FILE)``）失败并留下失效句柄。
    """
    if not should_rotate(path, max_bytes):
        return False
    backups = LOG_BACKUP_COUNT if backup_count is None else int(backup_count)
    with _rotate_lock:
        # 双重检查：并发写入方可能已被前一个持锁者轮转过
        if not should_rotate(path, max_bytes):
            return False
        try:
            for index in range(backups - 1, 0, -1):
                src = "%s.%d" % (path, index)
                if os.path.exists(src):
                    os.replace(src, "%s.%d" % (path, index + 1))
            os.replace(path, "%s.1" % path)
        except OSError:
            return False
    return True


# ── 行格式 ──────────────────────────────────────────────────────────────────
def format_line(level, message, module=None, run_id=None, timestamp=None):
    """按统一格式组装一行日志。

    输出形如::

        [2026-09-17 09:30:12.345] [INFO ] [wt_flow_locator] 已通过流程链路匹配点击控件: ...

    ``module`` / ``run_id`` 为空时对应字段整段省略，避免出现空 ``[]``。
    ``run_id`` 只取前 8 位，保证列宽稳定。
    """
    now = time.time() if timestamp is None else float(timestamp)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    millis = int((now - int(now)) * 1000)
    if millis < 0:
        millis = 0
    label = _LEVEL_LABELS.get(normalize_level(level, default=INFO), "INFO ")

    parts = ["[%s.%03d]" % (stamp, millis), "[%s]" % label]
    if run_id:
        parts.append("[%s]" % str(run_id)[:8])
    if module:
        parts.append("[%s]" % str(module))
    parts.append(str(message))
    return " ".join(parts)


# ── 关键字兜底分类 ──────────────────────────────────────────────────────────
# 说明：新代码应显式传级别；本函数只用于「已经落盘的纯文本行」在 UI 侧着色，
# 以及尚未改造的旧调用点过渡期使用。
_CRIT_TOKENS = ("致命",)
_ERROR_TOKENS = ("失败", "错误", "异常", "Failed")
_WARN_TOKENS = ("警告", "超时", "耗时较长")
_SUCCESS_TOKENS = ("成功", "完成", "已通过")
_SYSTEM_TOKENS = ("启动", "开始", "状态", "队列")

# 已知「含错误关键字但语义无害」的上下文：命中则不作 error 判定。
# 典型来源：`下拉选项枚举进度: ... 已剔除错误项=0`（改造前被误判为 error）。
_BENIGN_ERROR_CONTEXT = (
    "错误项",
    "无错误",
    "零错误",
    "0 个错误",
    "no error",
    "0 error",
)

# 英文错误词（小写匹配，覆盖 status=failed / Failure / Exception 等）
_ERROR_TOKENS_EN = ("error", "traceback", "exception", "failed", "failure", "fatal")


def classify(line):
    """把一行纯文本归入 UI 标签（error / warning / success / system / info）。

    作为改造前 4 个独立分类器的统一替代实现，规则：
    1. 先判 error（含 CRITICAL/Traceback/Exception），但命中无害上下文时跳过；
    2. 再判 warning、success、system；
    3. 其余归 info。
    """
    text = str(line or "")
    lowered = text.lower()

    if "critical" in lowered or any(tok in text for tok in _CRIT_TOKENS):
        return "error"

    benign = any(ctx in text for ctx in _BENIGN_ERROR_CONTEXT)
    if not benign and (
        any(tok in text for tok in _ERROR_TOKENS)
        or any(tok in lowered for tok in _ERROR_TOKENS_EN)
    ):
        return "error"

    if any(tok in text for tok in _WARN_TOKENS) or "warn" in lowered:
        return "warning"

    if any(tok in text for tok in _SUCCESS_TOKENS) or "success" in lowered:
        return "success"

    if any(tok in text for tok in _SYSTEM_TOKENS):
        return "system"

    return "info"


def detect_level(line):
    """把一行纯文本推断成级别（供 UI 按新色板渲染历史日志）。"""
    tag = classify(line)
    if tag == "error":
        lowered = str(line or "").lower()
        if "critical" in lowered or "致命" in str(line or ""):
            return CRIT
        return ERROR
    if tag == "warning":
        return WARN
    if tag in ("success", "system"):
        return INFO
    return INFO


# 已格式化行形如：`[2026-09-17 09:30:12.345] [INFO ] 消息`
_LEVEL_TOKEN_RE = re.compile(r"^\[[^\]]*\]\s+\[(DEBUG|INFO|WARN|ERROR|CRIT)\s*\]")


def level_from_line(line):
    """从已格式化的日志行中提取写入方显式写入的级别；缺失时返回 None。"""
    match = _LEVEL_TOKEN_RE.match(str(line or "").lstrip())
    if not match:
        return None
    return normalize_level(match.group(1), default=None)


def tag_for_line(line):
    """把一行日志归入 UI 标签（error / warning / success / system / debug / info）。

    优先采用写入方显式写入的级别 token（log_step 会写 ``[INFO ]`` 等），
    仅在缺失时回退到关键字猜测 —— 避免 UI 侧二次猜测与写入方判断不一致
    （改造前 4 个分类器各猜一套，规则互不相同）。
    """
    level = level_from_line(line)
    if level is not None:
        return _TAG_BY_LEVEL[level]
    return classify(line)


# ── 配色（延迟从 wt_theme 取） ──────────────────────────────────────────────
# 兜底色值：仅当 wt_theme 不可用时使用，与 wt_theme.LOG_LEVEL_COLORS 保持一致。
_FALLBACK_COLORS = {
    "light": {
        "DEBUG": "#94a3b8",
        "INFO": "#0f172a",
        "WARN": "#b45309",
        "ERROR": "#dc2626",
        "CRIT": "#991b1b",
        "debug": "#94a3b8",
        "info": "#0f172a",
        "warning": "#b45309",
        "error": "#dc2626",
        "success": "#047857",
        "system": "#0369a1",
        "muted": "#64748b",
    },
    "dark": {
        "DEBUG": "#64748b",
        "INFO": "#e2e8f0",
        "WARN": "#fbbf24",
        "ERROR": "#f87171",
        "CRIT": "#fca5a5",
        "debug": "#64748b",
        "info": "#e2e8f0",
        "warning": "#fbbf24",
        "error": "#f87171",
        "success": "#34d399",
        "system": "#60a5fa",
        "muted": "#94a3b8",
    },
}


def level_colors(dark=False):
    """返回 ``{级别或标签: 颜色}``。优先取 ``wt_theme.LOG_LEVEL_COLORS``。"""
    key = "dark" if dark else "light"
    try:
        import wt_theme

        getter = getattr(wt_theme, "get_log_level_colors", None)
        if callable(getter):
            colors = getter(dark=dark)
            if colors:
                return dict(colors)
    except Exception:
        pass
    return dict(_FALLBACK_COLORS[key])


def tag_colors_for_widget(dark=False, tags=None):
    """按给定标签集返回 ``(tag, color)`` 序列，供 ``Text.tag_configure`` 直接迭代。

    传入 ``tags`` 可只取该控件实际使用的标签（如监视器窗口没有 system）。
    """
    colors = level_colors(dark=dark)
    ordered = tags if tags else ("debug", "info", "warning", "error", "success", "system")
    return [(tag, colors[tag]) for tag in ordered if tag in colors]


# 需要加粗强调的标签：错误必须在一屏几十行的密集日志里一眼可见。
EMPHASIS_TAGS = ("error",)


def configure_log_tags(widget, dark=False, tags=None, font_family=None, font_size=None):
    """为日志 Text 控件统一配置级别标签（颜色 + 错误加粗）。

    把「取色 + 遍历 tag_configure + 强调样式」收成一个调用，替代改造前
    每个日志区各写一遍的重复循环，保证四处日志区的级别呈现完全一致。

    **不 import tkinter**：只调用传入控件的 ``tag_configure``，
    因此本函数在 headless 侧（CLI / 队列服务）也能安全存在。

    ``font_family`` / ``font_size`` 用于生成加粗变体；缺省时只设颜色。
    """
    ordered = tags if tags else ("debug", "info", "warning", "error", "success", "system")
    colors = level_colors(dark=dark)
    for tag in ordered:
        color = colors.get(tag)
        if not color:
            continue
        options = {"foreground": color}
        if font_family and font_size and tag in EMPHASIS_TAGS:
            options["font"] = (font_family, font_size, "bold")
        widget.tag_configure(tag, **options)
    return list(ordered)
