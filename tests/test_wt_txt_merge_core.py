# encoding: utf-8
"""wt_txt_merge_core 统一合并核心测试（待修改清单 #5）。

核心约定：
- 新核心的输出必须与两份历史实现**逐字节一致**（以 text_tools 旧实现原样拷贝为基准，
  8 组参数组合对照）；
- text_tools / txt_merge_tool 必须委托核心（不再各持一份实现）；
- 双方历史默认值差异（newline_between False / True）作为契约锁定。
"""
import inspect
import os
from unittest.mock import patch

import pytest

import text_tools
import txt_merge_tool
import wt_txt_merge_core


# ── 基准实现：text_tools.py 旧 merge/read 的原样拷贝（统一前立档） ──────────────

def _reference_read_text_file(path):
    with open(path, "rb") as f:
        raw = f.read()
    for enc in ["utf-8-sig"] + ["utf-8", "gbk", "gb18030", "utf-16", "big5", "latin-1"]:
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1"), "latin-1"


def _reference_merge_txt_files(
    file_paths,
    output_path,
    newline_between=False,
    add_headers=False,
    separator="",
    remove_empty_lines=False,
    output_encoding="utf-8-sig",
):
    parts = []
    for i, path in enumerate(file_paths):
        content, _ = _reference_read_text_file(path)
        if i > 0:
            if separator:
                parts.append(separator + "\n")
            elif newline_between:
                parts.append("\n")
        if add_headers:
            parts.append(os.path.basename(path) + "\n")
        parts.append(content)
    merged = "".join(parts)
    if remove_empty_lines:
        merged = "\n".join(line for line in merged.split("\n") if line.strip() != "")
    with open(output_path, "w", encoding=output_encoding, newline="") as f:
        f.write(merged)
    return len(file_paths), len(merged)


# ── 夹具：覆盖多编码/空行/BOM 的输入文件组 ──────────────────────────────────────

@pytest.fixture
def sample_files(tmp_path):
    f1 = tmp_path / "a.txt"            # utf-8，含空行
    f1.write_text("第一行 A\n\n第三行 A\n", encoding="utf-8")
    f2 = tmp_path / "b.txt"            # GBK
    f2.write_text("GBK 内容 B\n第二行 B\n", encoding="gbk")
    f3 = tmp_path / "c.txt"            # utf-8 带 BOM
    f3.write_bytes("\ufeffBOM 头 C\n".encode("utf-8"))
    return [str(f1), str(f2), str(f3)]


# ── #5 主对照：8 组参数组合，核心输出与历史实现逐字节一致 ────────────────────────

_PARAM_COMBOS = [
    dict(newline_between=False, add_headers=False),
    dict(newline_between=True, add_headers=False),
    dict(newline_between=False, add_headers=True),
    dict(newline_between=True, add_headers=True),
    dict(newline_between=False, add_headers=False, separator="--- SEP ---"),
    dict(newline_between=True, add_headers=False, separator="--- SEP ---",
         remove_empty_lines=True),
    dict(newline_between=True, add_headers=True, separator="=== ===",
         remove_empty_lines=True),
    dict(newline_between=False, add_headers=True, separator="S", remove_empty_lines=True,
         output_encoding="gbk"),
]


@pytest.mark.parametrize("combo", _PARAM_COMBOS)
def test_core_output_byte_identical_to_reference(sample_files, tmp_path, combo):
    ref_out = tmp_path / "ref_out.txt"
    core_out = tmp_path / "core_out.txt"

    ref_count, ref_chars = _reference_merge_txt_files(sample_files, str(ref_out), **combo)
    count, chars = wt_txt_merge_core.merge_txt_files(sample_files, str(core_out), **combo)

    assert (count, chars) == (ref_count, ref_chars)
    assert core_out.read_bytes() == ref_out.read_bytes()


def test_reference_baseline_differs_for_sanity(sample_files, tmp_path):
    """哨兵：确认对照组本身有能力检出差异（防止对照测试恒真）。"""
    out_a = tmp_path / "a.txt"
    out_b = tmp_path / "b.txt"
    _reference_merge_txt_files(sample_files, str(out_a), newline_between=False)
    _reference_merge_txt_files(sample_files, str(out_b), newline_between=True)
    assert out_a.read_bytes() != out_b.read_bytes()


# ── 委托关系：两个 UI 模块不再各持实现 ──────────────────────────────────────────

def test_txt_merge_tool_reexports_core():
    assert txt_merge_tool.merge_txt_files is wt_txt_merge_core.merge_txt_files
    assert txt_merge_tool.read_text_file is wt_txt_merge_core.read_text_file


def test_text_tools_merges_delegates_to_core(sample_files, tmp_path):
    out = tmp_path / "delegated.txt"
    sentinel = (99, 12345)
    with patch.object(wt_txt_merge_core, "merge_txt_files", return_value=sentinel) as m:
        result = text_tools.merge_txt_files(
            sample_files, str(out), newline_between=True, add_headers=True,
        )
    assert result == sentinel
    assert m.call_args.kwargs["newline_between"] is True
    assert m.call_args.kwargs["add_headers"] is True


def test_text_tools_read_text_file_delegates_to_core(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("委托读取", encoding="utf-8")
    with patch.object(wt_txt_merge_core, "read_text_file", return_value=("X", "enc")):
        assert text_tools.read_text_file(str(f)) == ("X", "enc")


# ── 默认值差异契约：text_tools=False，核心/txt_merge_tool=True ──────────────────

def test_default_value_difference_is_documented_contract():
    assert (inspect.signature(text_tools.merge_txt_files)
            .parameters["newline_between"].default is False)
    assert (inspect.signature(wt_txt_merge_core.merge_txt_files)
            .parameters["newline_between"].default is True)
    assert (inspect.signature(txt_merge_tool.merge_txt_files)
            .parameters["newline_between"].default is True)


# ── read_text_file 编码行为与历史实现一致 ────────────────────────────────────────

def test_read_text_file_encoding_chain(tmp_path):
    f_utf8 = tmp_path / "u.txt"
    f_utf8.write_text("纯 UTF-8", encoding="utf-8")
    f_bom = tmp_path / "b.txt"
    f_bom.write_bytes("\ufeff带 BOM".encode("utf-8"))
    f_gbk = tmp_path / "g.txt"
    f_gbk.write_text("国标内容", encoding="gbk")
    f_raw = tmp_path / "r.txt"
    # 奇数长度 + 无 BOM + 非法首字节：对编码链里每一种编码（含 utf-16 的偶数长度
    # 要求与 BOM 探测）都必须失败，才会落到 latin-1 兜底
    f_raw.write_bytes(b"\x80ab")

    # 无 BOM 的纯 UTF-8 会由链路第一环 utf-8-sig 成功解码（历史行为即如此）
    for path, expect_content in [
        (f_utf8, "纯 UTF-8"),
        (f_bom, "带 BOM"),
        (f_gbk, "国标内容"),
        (f_raw, _reference_read_text_file(str(f_raw))[0]),
    ]:
        content, enc = wt_txt_merge_core.read_text_file(str(path))
        assert content == expect_content
        # 与基准实现完全一致（内容 + 报告的编码）
        ref_content, ref_enc = _reference_read_text_file(str(path))
        assert (content, enc) == (ref_content, ref_enc)
    assert wt_txt_merge_core.read_text_file(str(f_raw))[1] == "latin-1"
