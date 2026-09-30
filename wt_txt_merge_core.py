# encoding: utf-8
"""WT 文本合并公共核心（待修改清单 #5：TXT 合并双实现合并）。

历史上 txt_merge_tool.py 与 text_tools.py 各持一份逐行等价的 merge_txt_files /
read_text_file 实现，唯一差异是 newline_between 默认值（True / False），且两个
UI 调用时均显式传参，默认值从不参与实际行为。本模块抽取唯一实现，两个 UI 层
只保留「调用 + 各自历史默认值」的差异适配。

纯函数模块：仅依赖标准库，不依赖 tkinter / pywin32，可在无显示环境下单测。
"""
import os
import threading

# 常见文本编码，按优先级尝试解码（与原两份实现逐项一致）
_ENCODINGS = ["utf-8", "gbk", "gb18030", "utf-16", "big5", "latin-1"]


class MergeCancelled(Exception):
    """合并被 cancel_event 取消（在逐文件边界抛出，done=已完成文件数）。

    取消时不写输出文件：目标路径若已存在则保持原样。
    """

    def __init__(self, done=0):
        super().__init__("cancelled after {} file(s)".format(done))
        self.done = done


def read_text_file(path):
    """读取文本文件内容，自动探测编码，返回 (内容, 编码)。"""
    with open(path, "rb") as f:
        raw = f.read()
    # 优先尝试 utf-8-sig（兼容带 BOM 的 utf-8）
    for enc in ["utf-8-sig"] + _ENCODINGS:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    # 兜底：latin-1 永不失败，保证不丢字节
    return raw.decode("latin-1"), "latin-1"


def merge_txt_files(
    file_paths,
    output_path,
    newline_between=True,
    add_headers=False,
    separator="",
    remove_empty_lines=False,
    output_encoding="utf-8-sig",
    progress_callback=None,
    cancel_event=None,
):
    """按顺序合并多个 txt 文件到输出文件，保持内容原样。

    参数：
      newline_between    : True 时在每个文件之间插入一个换行（新起一行再接）
      add_headers        : True 时在每个文件内容前插入一行文件名作为章节标题
      separator          : 非空时在每个文件之间插入该自定义分隔行
      remove_empty_lines : True 时删除所有空行
      output_encoding    : 输出编码（utf-8-sig / gbk 等）
      progress_callback  : 可选，每合并完一个文件回调 progress_callback(done, total)
                           （待修改清单 #2：供 UI 线程外汇报进度）
      cancel_event       : 可选 threading.Event，逐文件边界检查；置位后抛
                           MergeCancelled(done=已完成数)，此时**不写输出文件**

    返回 (合并文件数, 总字符数)。
    """
    total = len(file_paths)
    parts = []
    done = 0
    for i, path in enumerate(file_paths):
        # 取消检查在逐文件边界（待修改清单 #2）
        if cancel_event is not None and cancel_event.is_set():
            raise MergeCancelled(done=done)

        content, _ = read_text_file(path)

        # 文件之间的衔接（换行 / 分隔符）
        if i > 0:
            if separator:
                parts.append(separator + "\n")
            elif newline_between:
                parts.append("\n")

        # 章节标题
        if add_headers:
            parts.append(os.path.basename(path) + "\n")

        parts.append(content)

        done = i + 1
        if progress_callback is not None:
            progress_callback(done, total)

    merged = "".join(parts)

    # 去重空行
    if remove_empty_lines:
        merged = "\n".join(line for line in merged.split("\n") if line.strip() != "")

    with open(output_path, "w", encoding=output_encoding, newline="") as f:
        f.write(merged)
    return len(file_paths), len(merged)
