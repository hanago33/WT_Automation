#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
txt2wtg_core.py

TXT -> WTG 转换核心模块（无 GUI 依赖，可被 CLI / GUI 共用）。

读取三列数据：风速(m/s)  功率(kW)  推力系数(Ct)
自动跳过表头/空行/注释行，并做基础数据校验，生成 WAsP / Meteodyn WT
兼容的 .wtg（XML）文件。
"""

import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import date


# ---------------------------------------------------------------------------
# 编码探测：依次尝试常见编码，避免 Windows 下 GBK/UTF-8 混用导致乱码或报错
# ---------------------------------------------------------------------------
def _read_text(path):
    last_err = None
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read(), enc
        except (UnicodeDecodeError, UnicodeError) as e:
            last_err = e
            continue
    # 兜底：二进制读取后忽略错误
    with open(path, "rb") as f:
        raw = f.read()
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


# ---------------------------------------------------------------------------
# 表头/分隔符智能识别
# ---------------------------------------------------------------------------
_HEADER_KEYWORDS = (
    "风速", "功率", "推力", "wind", "speed", "power", "thrust",
    "ws", "v(", "kw", "ct", "cp",
)


def _looks_like_header(parts):
    """通过首列是否为数字 + 是否含表头关键词，判断是否为表头行。"""
    if not parts:
        return True
    first = parts[0]
    try:
        float(first)
    except ValueError:
        # 首列不是数字：极可能是表头
        return True
    # 首列是数字，但同行包含明显表头关键词（如中文单位），仍视为表头
    lower = " ".join(parts).lower()
    if any(k in lower for k in _HEADER_KEYWORDS):
        return True
    return False


def _split_line(line):
    """支持空格/制表符/逗号分隔，返回字段列表。"""
    if "," in line and " " not in line and "\t" not in line:
        return [p.strip() for p in line.split(",") if p.strip() != ""]
    return line.split()


def parse_txt(filepath, verbose=True):
    """
    读取文本，返回数据点列表 [(ws, power, ct), ...]，并完成基础清洗。

    规则：
      1. 空行、纯分隔符行 -> 跳过
      2. 表头行（首列非数字或含关键词）-> 跳过
      3. 字段不足 3 个 -> 跳过（警告）
      4. 数值无法解析 -> 跳过（警告）
    """
    text, enc = _read_text(filepath)
    if verbose:
        print(f"  (编码识别为：{enc})")

    data = []
    skipped = []
    for line_num, line in enumerate(text.splitlines(), 1):
        raw = line
        line = line.strip()
        if not line:
            continue

        parts = _split_line(line)
        if len(parts) < 3:
            skipped.append((line_num, line, "字段数不足3个"))
            continue

        # 表头识别
        if _looks_like_header(parts):
            if verbose:
                print(f"信息：第{line_num}行被识别为表头/注释，已跳过 -> {line}")
            continue

        # 数值解析
        try:
            ws = float(parts[0])
            power = float(parts[1])
            ct = float(parts[2])
        except ValueError:
            skipped.append((line_num, line, "包含非数值"))
            continue

        data.append((ws, power, ct))

    if verbose and skipped:
        for ln, content, reason in skipped:
            print(f"警告：第{ln}行{reason}，已跳过 -> {content}")

    return data


# ---------------------------------------------------------------------------
# 数据校验：返回警告列表（不阻断生成，仅提示）
# ---------------------------------------------------------------------------
def validate_data(data, rated_power, cut_in, cut_out):
    warnings = []
    if not data:
        return ["没有可用数据点，无法生成。"]

    ws_list = [d[0] for d in data]
    # 1) 风速应单调不减
    for i in range(1, len(ws_list)):
        if ws_list[i] < ws_list[i - 1] - 1e-6:
            warnings.append(
                f"风速在第 {i + 1} 个点出现下降 "
                f"({ws_list[i - 1]:.1f} -> {ws_list[i]:.1f})，功率曲线应单调递增。"
            )
            break

    # 2) 重复风速
    seen = set()
    dups = [w for w in ws_list if w in seen or seen.add(w)]
    if dups:
        warnings.append(f"存在重复风速值：{sorted(set(dups))}。")

    # 3) 功率不应超过额定过多
    max_power = max(d[1] for d in data)
    if rated_power and max_power > rated_power * 1.05:
        warnings.append(
            f"数据最大功率 {max_power:.1f} kW 超过额定功率 "
            f"{rated_power:.1f} kW 的 5%，请核对。"
        )

    # 4) Ct 合理范围
    for ws, power, ct in data:
        if ct < 0 or ct > 2.0:
            warnings.append(f"风速 {ws:.1f} m/s 处 Ct={ct} 超出常见范围 [0, 2]。")
            break

    # 5) 切入/切出
    if cut_in is not None and cut_out is not None and cut_in > cut_out:
        warnings.append(f"切入风速({cut_in}) 大于切出风速({cut_out})，逻辑异常。")

    # 6) 零/负风速
    if any(w < 0 for w in ws_list):
        warnings.append("存在负风速值。")

    return warnings


# ---------------------------------------------------------------------------
# 生成 WTG (XML)
# ---------------------------------------------------------------------------
def generate_wtg(data, rotor_diameter, rated_power, cut_in, cut_out,
                 air_density=1.225, hub_height=120.0, manufacturer="User"):
    """生成符合 WAsP / Meteodyn WT 标准的 wtg XML 字符串。"""
    if not data:
        raise ValueError("没有可用数据点，无法生成 WTG。")

    root = ET.Element(
        "WindTurbineGenerator",
        FormatVersion="1.01",
        Description=f"{rated_power / 1000:.1f} MW / D{rotor_diameter:.0f}m",
        ManufacturerName=manufacturer,
        ReferenceURI="",
        RotorDiameter=f"{rotor_diameter:.1f}",
    )

    ET.SubElement(root, "Comments").text = (
        f"Generated by txt2wtg tool on {date.today().isoformat()}"
    )

    heights = ET.SubElement(root, "SuggestedHeights")
    ET.SubElement(heights, "Height").text = f"{hub_height:.1f}"

    stationary_ct = f"{data[-1][2]:.3f}"
    perf = ET.SubElement(
        root, "PerformanceTable",
        AirDensity=f"{air_density:.3f}",
        MaximumNoiseLevel="0.0",
        DataStatus="Unknown",
        DataSource="UserInput",
        ReleaseDate=date.today().isoformat(),
        ReferenceURI="None",
        StationaryThrustCoEfficient=stationary_ct,
    )

    sss = ET.SubElement(
        perf, "StartStopStrategy",
        LowSpeedCutOut=f"{cut_in:.1f}",
        LowSpeedCutIn=f"{cut_in:.1f}",
        HighSpeedCutIn=f"{cut_out:.1f}",
        HighSpeedCutOut=f"{cut_out:.1f}",
    )
    ET.SubElement(sss, "Comments").text = ""

    table = ET.SubElement(perf, "DataTable")
    for ws, power, ct in data:
        ET.SubElement(
            table, "DataPoint",
            WindSpeed=f"{ws:.1f}",
            PowerOutput=f"{power:.1f}",
            ThrustCoEfficient=f"{ct:.3f}",
        )

    # 美化并清理空行
    rough = ET.tostring(root, encoding="utf-8")
    reparsed = minidom.parseString(rough)
    pretty = reparsed.toprettyxml(indent="  ")
    lines = [ln for ln in pretty.split("\n") if ln.strip() != ""]
    if lines and lines[0].startswith("<?xml"):
        lines[0] = '<?xml version="1.0" encoding="UTF-8"?>'
        xml_str = "\n".join(lines)
    else:
        xml_str = '<?xml version="1.0" encoding="UTF-8"?>\n' + "\n".join(lines)
    return xml_str


# ---------------------------------------------------------------------------
# 对外便捷接口：解析 + 校验 + 写文件
# ---------------------------------------------------------------------------
def convert_file(input_path, output_path, rotor_diameter, rated_power,
                 cut_in=3.0, cut_out=25.0, air_density=1.225,
                 hub_height=120.0, manufacturer="User", verbose=True):
    """完整流程：解析 -> 校验 -> 生成 -> 写盘。返回 (output_path, data, warnings)。"""
    data = parse_txt(input_path, verbose=verbose)
    if not data:
        raise ValueError("未能读取到有效数据，请检查文件格式（三列数字：风速 功率 推力系数）。")

    warnings = validate_data(data, rated_power, cut_in, cut_out)
    xml_str = generate_wtg(
        data, rotor_diameter, rated_power, cut_in, cut_out,
        air_density, hub_height, manufacturer,
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml_str)
    return output_path, data, warnings
