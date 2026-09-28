# encoding: utf-8
"""日志查询与过滤：人读日志视图的共用逻辑。

职责
----
1. 按 **级别 / 关键字 / 步骤号** 过滤日志行 —— 纯逻辑，与 UI 无关，便于测试。
2. 统计各级别行数，生成「显示 N/M 行」这类状态文案。
3. 命令行入口，便于在终端或 Agent 里直接查日志，无需打开 GUI。

为什么需要它
------------
改造后主链路一次运行 111 步、近百行 INFO 日志，而总控台「运行日志」页只能看到
尾部若干行、且**无法按级别或步骤筛选**；「运行报告」页虽然列出每步结果，
却与日志视图互不相通 —— 定位「第一个出问题的步骤」只能靠肉眼滚屏。

本模块把「筛选 + 计数 + 状态文案」抽成纯函数，供 GUI 视图与命令行共用同一套语义。

设计约束
--------
- **不 import tkinter**：会被 CLI 与 headless 流程导入。
- 只依赖标准库与 ``wt_logging``。
- 过滤依据是行内的**显式级别 token**（``[WARN ]`` 等）；对改造前落盘的历史行，
  回退到 ``wt_logging.detect_level`` 的关键字推断，避免老日志被静默滤空。
"""

import argparse
import os
import sys

import wt_logging

# 统计与摘要里关心的级别（按严重度从高到低）
_SUMMARY_ORDER = (
    wt_logging.CRIT,
    wt_logging.ERROR,
    wt_logging.WARN,
    wt_logging.INFO,
    wt_logging.DEBUG,
)

# 预置的级别选项：(显示文案, 传给 LogFilter 的 min_level 值)
LEVEL_CHOICES = (
    ("全部级别", ""),
    ("WARN 及以上", wt_logging.WARN),
    ("ERROR 及以上", wt_logging.ERROR),
    ("仅 INFO 及以上", wt_logging.INFO),
    ("含 DEBUG", wt_logging.DEBUG),
)


def level_of(line):
    """取一行的级别：优先读显式 token，历史行回退关键字推断。"""
    text = str(line or "")
    explicit = wt_logging.level_from_line(text)
    if explicit is not None:
        return explicit
    return wt_logging.detect_level(text)


def level_counts(lines):
    """统计各级别行数，返回 ``{级别: 行数}``（不含 0 项）。"""
    counts = {}
    for line in lines:
        if not str(line or "").strip():
            continue
        level = level_of(line)
        counts[level] = counts.get(level, 0) + 1
    return counts


def summarize(lines):
    """生成一行式摘要，适合放在视图状态栏。

    形如：``共 134 行 · ERROR 2 · WARN 5 · INFO 95 · DEBUG 32``
    """
    counts = level_counts(lines)
    total = sum(counts.values())
    parts = ["共 %d 行" % total]
    for level in _SUMMARY_ORDER:
        count = counts.get(level, 0)
        if count:
            parts.append("%s %d" % (level, count))
    return " · ".join(parts)


class LogFilter(object):
    """日志过滤条件（级别 / 关键字 / 步骤号）。

    三个条件是「与」关系；都为空时视为未过滤。
    """

    def __init__(self, min_level=None, keyword="", step_id=""):
        self.min_level = (
            wt_logging.normalize_level(min_level, default=None) if min_level else None
        )
        self.keyword = str(keyword or "").strip()
        self.step_id = str(step_id or "").strip()

    def is_active(self):
        return bool(self.min_level or self.keyword or self.step_id)

    def matches(self, line):
        """判断一行是否满足全部条件。"""
        text = str(line or "")
        if not text.strip():
            return False
        if self.min_level is not None:
            if wt_logging.level_rank(level_of(text)) < wt_logging.level_rank(self.min_level):
                return False
        if self.keyword and self.keyword not in text:
            return False
        if self.step_id:
            # 精确匹配写入方声明的 stepId；不带 step= 的行（运行边界等）不计入，
            # 保证「只看某步骤」的结果可预期，不做模糊包含匹配。
            step, _ = wt_logging.extract_step_fields(text)
            if step != self.step_id:
                return False
        return True

    def apply(self, lines):
        """返回过滤后的行列表；未激活过滤时原样返回（保留原列表语义）。"""
        if not self.is_active():
            return list(lines)
        return [line for line in lines if self.matches(line)]

    def describe(self, total, shown):
        """生成状态文案，形如 ``过滤：WARN 及以上 · 显示 7/134 行``。"""
        if not self.is_active():
            return "未过滤 · 共 %d 行" % total
        bits = []
        if self.min_level:
            bits.append("%s 及以上" % self.min_level)
        if self.keyword:
            bits.append('关键字"%s"' % self.keyword)
        if self.step_id:
            bits.append("步骤 %s" % self.step_id)
        return "过滤：%s · 显示 %d/%d 行" % (" · ".join(bits), shown, total)

    def __repr__(self):
        return "LogFilter(min_level=%r, keyword=%r, step_id=%r)" % (
            self.min_level,
            self.keyword,
            self.step_id,
        )


def read_log_lines(path, tail=0):
    """读取日志文件为行列表；``tail > 0`` 时只取最后 N 行。

    读取失败返回空列表（调用方自行决定如何提示）。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as file_obj:
            lines = [line.rstrip("\n") for line in file_obj]
    except OSError:
        return []
    lines = [line for line in lines if line.strip()]
    if tail and tail > 0:
        lines = lines[-tail:]
    return lines


def query(path, log_filter=None, tail=0):
    """读取 + 过滤，返回 ``(过滤后行, 全部行, 状态文案)``。"""
    all_lines = read_log_lines(path, tail=tail)
    active = log_filter or LogFilter()
    shown = active.apply(all_lines)
    return shown, all_lines, active.describe(len(all_lines), len(shown))


def main(argv=None):
    """命令行入口：``python -m wt_log_query <日志> [--min-level WARN] [--step x]``。"""
    parser = argparse.ArgumentParser(
        prog="wt_log_query",
        description="WT 运行日志查询：按级别 / 关键字 / 步骤过滤，或只看统计摘要。",
    )
    parser.add_argument("path", help="日志文件路径，如 wt_automation.log")
    parser.add_argument("--min-level", default="", help="最低级别：DEBUG/INFO/WARN/ERROR/CRIT")
    parser.add_argument("--keyword", default="", help="关键字（子串匹配）")
    parser.add_argument("--step", default="", help="只看某步骤，如 step_12_scan2_12")
    parser.add_argument("--tail", type=int, default=0, help="只取最后 N 行再过滤（0=全部）")
    parser.add_argument("--summary", action="store_true", help="只输出统计摘要")
    parser.add_argument("--limit", type=int, default=0, help="最多输出 N 行（0=不限）")
    args = parser.parse_args(argv)

    if not os.path.exists(args.path):
        sys.stderr.write("日志文件不存在：%s\n" % args.path)
        return 2

    log_filter = LogFilter(
        min_level=args.min_level or None,
        keyword=args.keyword,
        step_id=args.step,
    )
    shown, all_lines, status = query(args.path, log_filter, tail=args.tail)

    if args.summary:
        print(summarize(all_lines))
        if log_filter.is_active():
            print(status)
        return 0

    print(status)
    print("-" * 72)
    output = shown
    if args.limit and args.limit > 0:
        output = shown[-args.limit:] if len(shown) > args.limit else shown
        if len(shown) > args.limit:
            print("（仅显示最后 %d 行）" % args.limit)
    for line in output:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
