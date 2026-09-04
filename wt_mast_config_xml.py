# -*- coding: utf-8 -*-
"""
MUP 测风塔数据导入配置文件（XMLSettings）生成器 —— 固定模板注入式。

设计（2026-09-04 定稿）：
  - 以 C1831配置信息1.xml（用户手工导出的正确配置）为**固定模板**固化在模块内。
  - 生成 = 在模板上只注入「必要修改参数」，其余（Separator=4 / HeaderLineNumber=13 /
    FirstDataLineNumber=14 / SelectedEncoding=US-ASCII / DoesDateAndTimeShareColumn=true /
    DataSens 107·44·92·0 / 日期格式 / 列顺序结构）一律保持模板原文，绝不动态拼装。
  - 必要注入参数（随塔变化）：
      1) DefaultHeight           ← CFT 轮毂高度
      2) 风速列 Height(DataSens=44)、风向列 Height(DataSens=92)  ← CFT 轮毂高度
      3) 风速列 Header、风向列 Header  ← 该塔 tim 第 13 行的实际列名
    （同项目各塔列顺序一致：时间/风速/风向/忽略，仅列名随塔不同。）
  - 输出文件固定 <塔名>配置信息.xml；列头不可用 / tim 不可读时返回 None，
    宁可不生成也不产出坏配置。

用法（离线验证）：
    python wt_mast_config_xml.py <tim_path> <mast_name> <hub_height> [output_dir]
"""
import os
import re
import sys

# ── 固定模板：C1831配置信息1.xml（用户手工导出正确版，勿改结构/常量）──
_TEMPLATE_XML = """<?xml version="1.0"?>
<XMLSettings xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Separator>4</Separator>
  <HeaderLineNumber>13</HeaderLineNumber>
  <FirstDataLineNumber>14</FirstDataLineNumber>
  <SelectedEncoding>US-ASCII</SelectedEncoding>
  <DoesDateAndTimeShareColumn>true</DoesDateAndTimeShareColumn>
  <DefaultHeight>125</DefaultHeight>
  <SelectedCulture>zh</SelectedCulture>
  <DateTimeFormat>yyyy/MM/dd HH:mm</DateTimeFormat>
  <DateFormat>yyyy/M/d</DateFormat>
  <TimeFormat>H:mm</TimeFormat>
  <PressureUnit>1</PressureUnit>
  <TemperatureUnit>0</TemperatureUnit>
  <Columns>
    <ColumnsSettings>
      <Header>Date/Time</Header>
      <DataSens>107</DataSens>
      <Height />
    </ColumnsSettings>
    <ColumnsSettings>
      <Header>Ch2_Anem_180.00m_SE_Avg_m/s [m/s]</Header>
      <DataSens>44</DataSens>
      <Height>125</Height>
    </ColumnsSettings>
    <ColumnsSettings>
      <Header>Ch13_Vane_180.00m_N_Avg_Deg []</Header>
      <DataSens>92</DataSens>
      <Height>125</Height>
    </ColumnsSettings>
    <ColumnsSettings>
      <Header>_</Header>
      <DataSens>0</DataSens>
      <Height />
    </ColumnsSettings>
  </Columns>
</XMLSettings>"""

# 模板内风速/风向列 Header 锚点（用于按 DataSens 精确替换；模板内唯一）
_TEMPLATE_WIND_HEADER = "Ch2_Anem_180.00m_SE_Avg_m/s [m/s]"
_TEMPLATE_DIR_HEADER = "Ch13_Vane_180.00m_N_Avg_Deg []"

_HEADER_LINE_NUMBER = 13     # tim 列头行（固定）
_OUTPUT_SUFFIX = "配置信息.xml"

# 新建气象数据「配置导入」链路副本中"键入-配置文件路径"步骤的默认 text。
# 指向真实存在的正确 XML（链路未注入时也有默认可加载）；
# Simple 运行时由 Launcher 解析结果按塔覆盖为 <mast>配置信息.xml 的真实路径
# （text_overrides[DEFAULT_MAST_CONFIG_XML_TEXT] = mastXmlPath）。
DEFAULT_MAST_CONFIG_XML_TEXT = (
    r"C:\Users\14830\Desktop\202608_Test\03-WT输入\03-测风塔数据\C1831\C1831配置信息.xml"
)


def _read_text_head(tim_path, max_bytes=65536):
    """读取 tim 文件头部文本。优先严格编码，失败逐级回退；返回 (text, encoding)。"""
    if not tim_path or not os.path.isfile(tim_path):
        return "", ""
    try:
        raw = open(tim_path, "rb").read(max_bytes)
    except OSError:
        return "", ""
    for enc in ("utf-8-sig", "utf-8", "cp1252", "gbk", "latin-1"):
        try:
            return raw.decode(enc), enc
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1", errors="replace"), "latin-1"


def probe_tim_column_headers(tim_path):
    """从 tim 第 {HEADER_LINE_NUMBER} 行提取「风速列名/风向列名」。

    列顺序约定与模板一致：第1列 Date/Time、第2列风速、第3列风向、其后忽略。
    返回 {"wind_header": str, "dir_header": str}；tim 不可读/第13行无效返回 None。
    """
    text, _enc = _read_text_head(tim_path)
    if not text:
        return None
    lines = text.splitlines()
    if len(lines) < _HEADER_LINE_NUMBER:
        return None
    header_text = lines[_HEADER_LINE_NUMBER - 1]
    if "\t" not in header_text:
        return None
    cells = [c.strip() for c in header_text.split("\t")]
    if len(cells) < 3:
        return None
    first_cell_low = cells[0].lower()
    if ("date" not in first_cell_low) and ("time" not in first_cell_low):
        return None  # 首列不是日期时间 → 非标准布局，放弃
    return {"wind_header": cells[1], "dir_header": cells[2]}


def _esc(value):
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_mast_config_xml(tim_path, mast_name, hub_height, output_dir=None,
                          default_height=None, template_xml=None):
    """
    在固定模板上注入必要参数，生成某测风塔的导入配置文件（XMLSettings 文本）。

    参数：
        tim_path       : 该塔 tim 文件绝对路径（提供风速/风向列名）
        mast_name      : 测风塔名（CFT mastName，输出 <mast>配置信息.xml）
        hub_height     : 轮毂高度（int/str）→ DefaultHeight 与风速/风向列 Height
        output_dir     : 输出目录；缺省取 tim 所在目录
        default_height : 单独指定 DefaultHeight；缺省 = hub_height
        template_xml   : 自定义模板文本；缺省用内置 C1831配置信息1.xml 模板

    返回 {"xml_text", "output_path"}；列头不可用/tim 不可读返回 None（不出坏配置）。
    """
    headers = probe_tim_column_headers(tim_path)
    if not headers:
        return None
    hub = str(hub_height or "").strip()
    if not hub:
        return None
    default_h = str(default_height if default_height is not None else hub_height or "").strip()
    xml = template_xml if template_xml else _TEMPLATE_XML

    # 1) DefaultHeight → hub
    xml = re.sub(r"(<DefaultHeight>)[^<]*(</DefaultHeight>)",
                 r"\g<1>{0}\g<2>".format(default_h), xml)
    # 2) 有值 Height（模板中仅风速/风向两列带数字）→ hub；<Height /> 自闭合不动
    xml = re.sub(r"(<Height>)[^<]*(</Height>)",
                 r"\g<1>{0}\g<2>".format(hub), xml)
    # 3) 风速列 / 风向列 Header → 该塔 tim 实际列名
    xml = xml.replace(
        "<Header>{0}</Header>".format(_TEMPLATE_WIND_HEADER),
        "<Header>{0}</Header>".format(_esc(headers["wind_header"])),
    )
    xml = xml.replace(
        "<Header>{0}</Header>".format(_TEMPLATE_DIR_HEADER),
        "<Header>{0}</Header>".format(_esc(headers["dir_header"])),
    )

    out_dir = (output_dir or "").strip() or os.path.dirname(os.path.abspath(tim_path))
    safe = "".join(c for c in str(mast_name or "mast")
                   if c.isalnum() or c in ("-", "_", "（", "）", "(", ")")) or "mast"
    output_path = os.path.join(out_dir, "{0}{1}".format(safe, _OUTPUT_SUFFIX))
    return {"xml_text": xml, "output_path": output_path}


def save_mast_config_xml(tim_path, mast_name, hub_height, output_dir=None,
                         default_height=None, template_xml=None, overwrite=True):
    """
    生成并写入某测风塔的配置文件（覆盖语义：每次运行重新生成，与 CFT/tim 最新一致）。

    返回写入的绝对路径（<mast>配置信息.xml）；生成失败返回 ""。
    overwrite=False 时若目标已存在则不覆盖（保留手工文件）。
    """
    result = build_mast_config_xml(tim_path, mast_name, hub_height,
                                   output_dir=output_dir,
                                   default_height=default_height,
                                   template_xml=template_xml)
    if not result:
        return ""
    output_path = result["output_path"]
    if not overwrite and os.path.isfile(output_path):
        return output_path
    try:
        with open(output_path, "w", encoding="utf-8") as fobj:
            fobj.write(result["xml_text"])
        return output_path
    except OSError:
        return ""


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3:
        print(__doc__)
        return 2
    tim_path, mast_name, hub_height = argv[0], argv[1], argv[2]
    output_dir = argv[3] if len(argv) > 3 else ""
    result = build_mast_config_xml(tim_path, mast_name, hub_height, output_dir)
    if not result:
        print("生成失败：tim 不可读或第 {0} 行非标准列头（不生成，避免坏配置）: {1}".format(
            _HEADER_LINE_NUMBER, tim_path))
        return 1
    print("输出:", result["output_path"])
    print("-" * 40)
    print(result["xml_text"])
    try:
        with open(result["output_path"], "w", encoding="utf-8") as fobj:
            fobj.write(result["xml_text"])
        print("-" * 40)
        print("已写入:", result["output_path"])
    except OSError as exc:
        print("写入失败:", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
