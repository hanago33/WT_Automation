#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
txt2wtg.py  —— 命令行版 TXT -> WTG 转换工具

用法示例：
  python txt2wtg.py data.txt -d 220 -r 6250 -ci 3.0 -co 25.0
  python txt2wtg.py data.txt -d 220 -r 6250 -o MyTurbine.wtg
"""

import argparse
import os
import sys

from txt2wtg_core import convert_file


def build_parser():
    p = argparse.ArgumentParser(
        description="将 TXT 数据转换为 WAsP / Meteodyn WT 兼容的 .wtg 文件",
        epilog="示例: python txt2wtg.py data.txt -d 220 -r 6250 -ci 3.0 -co 25.0",
    )
    p.add_argument("input", help="输入 TXT 路径（三列：风速 功率 推力系数，支持表头）")
    p.add_argument("-o", "--output", help="输出 .wtg 路径（默认：输入文件名.wtg）")
    p.add_argument("-d", "--diameter", type=float, required=True, help="叶轮直径(米)")
    p.add_argument("-r", "--rated", type=float, required=True, help="额定功率(千瓦)")
    p.add_argument("-ci", "--cutin", type=float, default=3.0, help="切入风速(m/s)，默认3.0")
    p.add_argument("-co", "--cutout", type=float, default=25.0, help="切出风速(m/s)，默认25.0")
    p.add_argument("-rho", "--airdensity", type=float, default=1.225, help="空气密度(kg/m³)，默认1.225")
    p.add_argument("-hh", "--hubheight", type=float, default=120.0, help="轮毂高度(米)，默认120.0")
    p.add_argument("-m", "--manufacturer", type=str, default="User", help="制造商名称，默认User")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    if not os.path.isfile(args.input):
        print(f"错误：文件 {args.input} 不存在")
        return 1

    out_file = args.output or (os.path.splitext(args.input)[0] + ".wtg")

    print(f"正在读取文件：{args.input}")
    try:
        out_file, data, warnings = convert_file(
            args.input, out_file,
            args.diameter, args.rated,
            args.cutin, args.cutout,
            args.airdensity, args.hubheight, args.manufacturer,
            verbose=True,
        )
    except ValueError as e:
        print(f"错误：{e}")
        return 1

    print(f"[OK] 成功读取 {len(data)} 个数据点")
    if warnings:
        print("[!] 数据校验提示：")
        for w in warnings:
            print(f"   - {w}")
    print(f"[OK] 成功生成 {out_file}")
    print(f"   包含 {len(data)} 个功率曲线数据点")
    print(f"   额定功率：{args.rated / 1000:.2f} MW")
    print(f"   叶轮直径：{args.diameter:.1f} m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
