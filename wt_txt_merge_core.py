# encoding: utf-8
"""WT 文本合并公共核心（待修改清单 #5：TXT 合并双实现合并）。

历史上 txt_merge_tool.py 与 text_tools.py 各持一份逐行等价的 merge_txt_files /
read_text_file 实现，唯一差异是 newline_between 默认值（True / False），且两个
UI 调用时均显式传参，默认值从不参与实际行为。本模块抽取唯一实现，两个 UI 层
只保留「调用 + 各自历史默认值」的差异适配。

纯函数模块：仅依赖标准库，不依赖 tkinter / pywin32，可在无显示环境下单测。
"""
import os

# 常见文本编码，按优先级尝试解码（与原两份实现逐项一致）
_ENCODINGS = ["utf-8", "gbk", "gb18030", "utf-16", "big5", "latin-1"]


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
):
    """按顺序合并多个 txt 文件到输出文件，保持内容原样。

    参数：
      newline_between    : True 时在每个文件之间插入一个换行（新起一行再接）
      add_headers        : True 时在每个文件内容前插入一行文件名作为章节标题
      separator          : 非空时在每个文件之间插入该自定义分隔行
      remove_empty_lines : True 时删除所有空行
      output_encoding    : 输出编码（utf-8-sig / gbk 等）

    返回 (合并文件数, 总字符数)。
    """
    parts = []
    for i, path in enumerate(file_paths):
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

    merged = "".join(parts)

    # 去重空行
    if remove_empty_lines:
        merged = "\n".join(line for line in merged.split("\n") if line.strip() != "")

    with open(output_path, "w", encoding=output_encoding, newline="") as f:
        f.write(merged)
    return len(file_paths), len(merged)
