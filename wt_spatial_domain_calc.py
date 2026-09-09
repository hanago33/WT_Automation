# encoding: utf-8
"""
WT 自动化空间计算域与参数注入模块 (wt_spatial_domain_calc.py)

功能职责：
  1. 解析测风塔 (CFT) 与机位点 (JWD) 投影平面坐标 (X, Y)；
  2. 提取空间四极极值元素（极西、极东、极南、极北）及原始包络；
  3. 计算外扩 2500m 并构建正方形计算域（西北角 NW 与东南角 SE）；
  4. 计算正方形外接圆（WT 内圆计算域半径 R）及 Meteodyn WT 规范外圆（Outer Circle 半径 Router）；
  5. 将计算参数注入到 WT 自动化流程 JSON 文件：
     - flow_definition_导入并配置元素.json (step_10 ~ step_13)
     - flow_definition_创建一个新建模.json (step_22 ~ step_24)
  6. 提供可直接接入 wt_project_workdir_parser 的精确文本替换映射（text_overrides）。
"""

import os
import re
import json
import math
import argparse
from typing import List, Dict, Tuple, Optional, Any


class SpatialPoint:
    """空间点位（机位点或测风塔）"""
    def __init__(self, name: str, x: float, y: float, z: float = 0.0, category: str = "WT"):
        self.name = str(name).strip()
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)
        self.category = category  # 'CFT' (测风塔) 或 'JWD' (机位点)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "category": self.category,
        }

    def __repr__(self) -> str:
        return f"<SpatialPoint {self.name} [{self.category}]: X={self.x:.3f}, Y={self.y:.3f}, Z={self.z:.1f}>"


def parse_coordinate_file(file_path: str, category: Optional[str] = None) -> List[SpatialPoint]:
    """
    解析 WT 标准坐标文件 (CFT_*.txt 或 JWD_*.txt)。
    支持制表符/多空格分隔，每行典型格式：
      名称  X坐标  Y坐标  高度(可选)
    """
    points: List[SpatialPoint] = []
    if not file_path or not os.path.isfile(file_path):
        return points

    cat = category
    if not cat:
        fname = os.path.basename(file_path).lower()
        cat = "CFT" if "cft" in fname or "mast" in fname else "JWD"

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = re.split(r"[\s,;]+", line)
            if len(parts) >= 3:
                name = parts[0]
                try:
                    x = float(parts[1])
                    y = float(parts[2])
                    z = float(parts[3]) if len(parts) > 3 else 0.0
                    # 校验 UTM / 高斯投影合理性：X 通常 >= 100000, Y 通常 >= 1000000
                    if x >= 100000.0 and y >= 1000000.0:
                        points.append(SpatialPoint(name, x, y, z, cat))
                except (ValueError, TypeError):
                    continue
    return points


def parse_points_from_input_dir(dir_path: str) -> List[SpatialPoint]:
    """
    从项目输入目录（如 03-WT输入/01-测风塔及机位点坐标）递归扫描并汇总所有 CFT 与 JWD 点位。
    """
    all_points: List[SpatialPoint] = []
    if not dir_path or not os.path.isdir(dir_path):
        return all_points

    for root, _dirs, files in os.walk(dir_path):
        for fname in sorted(files):
            low = fname.lower()
            if not low.endswith(".txt"):
                continue
            full_path = os.path.join(root, fname)
            if "cft" in low or "测风塔" in low:
                all_points.extend(parse_coordinate_file(full_path, category="CFT"))
            elif "jwd" in low or "机位" in low:
                all_points.extend(parse_coordinate_file(full_path, category="JWD"))
    return all_points


def calculate_spatial_domain(
    points: List[SpatialPoint],
    buffer_m: float = 2500.0,
    round_radius_int: bool = True
) -> Dict[str, Any]:
    """
    核心空间几何推导计算：
      1. 找出四极边界点与原始包络矩形；
      2. 基础外扩 buffer_m 并取长边构造完全正方形包络（保持中心对称）；
      3. 计算正方形西北角 (NW) 与东南角 (SE)；
      4. 计算正方形外接圆（WT 内圆计算域）半径 R = (√2 / 2) * L；
      5. 计算 Meteodyn WT 规范外圆半径 Router = 1.2 * √2 * R + 2000m。

    :param points: 包含机位点和测风塔的点位列表
    :param buffer_m: 外扩缓冲距离（默认 2500 米）
    :param round_radius_int: 是否对半径 R 与 Router 取整为整数米（默认 True）
    :return: 包含完整几何结果的字典
    """
    if not points:
        raise ValueError("输入点位列表为空，无法进行空间计算域推导！")

    # 1. 查找空间四极元素
    pt_west = min(points, key=lambda p: p.x)    # 最小 X (极西)
    pt_east = max(points, key=lambda p: p.x)    # 最大 X (极东)
    pt_south = min(points, key=lambda p: p.y)   # 最小 Y (极南)
    pt_north = max(points, key=lambda p: p.y)   # 最大 Y (极北)

    min_x, max_x = pt_west.x, pt_east.x
    min_y, max_y = pt_south.y, pt_north.y

    span_x = max_x - min_x
    span_y = max_y - min_y

    # 2. 计算中心点与正方形区域
    center_x = (min_x + max_x) / 2.0
    center_y = (min_y + max_y) / 2.0

    # 双向缓冲后的最小需求尺寸
    req_w = span_x + 2.0 * buffer_m
    req_h = span_y + 2.0 * buffer_m

    # 为确保是正方形且任一方向缓冲均不低于 buffer_m，取两者最大值作为正方形边长
    square_side = max(req_w, req_h)
    half_side = square_side / 2.0

    nw_x = center_x - half_side
    nw_y = center_y + half_side
    se_x = center_x + half_side
    se_y = center_y - half_side

    # 3. 计算双圆半径
    # 正方形外接圆半径 R（即 WT 内圆）
    raw_r = (math.sqrt(2.0) / 2.0) * square_side
    r = float(round(raw_r)) if round_radius_int else raw_r

    # Meteodyn WT 规范外圆半径：Router = 1.2 * √2 * R + 2000m
    raw_r_outer = 1.2 * math.sqrt(2.0) * r + 2000.0
    r_outer = float(round(raw_r_outer)) if round_radius_int else raw_r_outer

    return {
        "point_count": len(points),
        "buffer_m": buffer_m,
        "extremes": {
            "west_min_x": pt_west.to_dict(),
            "east_max_x": pt_east.to_dict(),
            "south_min_y": pt_south.to_dict(),
            "north_max_y": pt_north.to_dict(),
        },
        "raw_bounding_box": {
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
            "span_x": span_x,
            "span_y": span_y,
        },
        "square_domain": {
            "center_x": center_x,
            "center_y": center_y,
            "side_length": square_side,
            "nw_corner": {"x": nw_x, "y": nw_y},
            "se_corner": {"x": se_x, "y": se_y},
        },
        "circles": {
            "inner_radius_R": r,
            "raw_inner_radius_R": raw_r,
            "outer_radius_Router": r_outer,
            "raw_outer_radius_Router": raw_r_outer,
        }
    }


def inject_into_flow_definitions(
    calc_result: Dict[str, Any],
    flow_import_path: str,
    flow_create_model_path: str,
    in_place: bool = True
) -> Dict[str, str]:
    """
    将计算所得参数注入到 WT 自动化流程 JSON 文件中：
      1. flow_definition_导入并配置元素.json:
         - step_10 (键入-西北角-X)
         - step_11 (键入-西北角-Y)
         - step_12 (键入-东南角-X)
         - step_13 (键入-东南角-Y)
      2. flow_definition_创建一个新建模.json:
         - step_22 (键入-经度X)
         - step_23 (键入-维度Y)
         - step_24 (键入-半径R)

    :param calc_result: calculate_spatial_domain 返回的计算结果
    :param flow_import_path: 导入并配置元素.json 路径
    :param flow_create_model_path: 创建一个新建模.json 路径
    :param in_place: True 覆盖原文件，False 生成带 _injected.json 后缀的新文件
    :return: 写入的文件路径字典 {"flow_import": path, "flow_create_model": path}
    """
    sq = calc_result["square_domain"]
    circles = calc_result["circles"]

    # 格式化注入字符串
    # 西北角 / 东南角保留 1 位小数（与 WT 录制模板风格一致）
    nw_x_str = f"{sq['nw_corner']['x']:.1f}"
    nw_y_str = f"{sq['nw_corner']['y']:.1f}"
    se_x_str = f"{sq['se_corner']['x']:.1f}"
    se_y_str = f"{sq['se_corner']['y']:.1f}"

    # 中心坐标保留 3 位小数，半径为整数米
    center_x_str = f"{sq['center_x']:.3f}"
    center_y_str = f"{sq['center_y']:.3f}"
    radius_r_str = str(int(round(circles["inner_radius_R"])))

    injected = {}

    # 1. 注入 导入并配置元素.json
    if os.path.isfile(flow_import_path):
        with open(flow_import_path, "r", encoding="utf-8") as f:
            flow_data = json.load(f)

        import_step_map = {
            "step_10": nw_x_str,
            "step_11": nw_y_str,
            "step_12": se_x_str,
            "step_13": se_y_str,
        }

        for step in flow_data.get("steps", []):
            sid = step.get("id")
            if sid in import_step_map:
                new_val = import_step_map[sid]
                if "actionConfig" in step and isinstance(step["actionConfig"], dict):
                    step["actionConfig"]["text"] = new_val
                desc = step.get("description", "")
                if desc:
                    step["description"] = re.sub(r"键入\s*[\d\.\-]+", f"键入 {new_val}", desc)

        out_import = flow_import_path if in_place else flow_import_path.replace(".json", "_injected.json")
        with open(out_import, "w", encoding="utf-8") as f:
            json.dump(flow_data, f, ensure_ascii=False, indent=2)
        injected["flow_import"] = out_import

    # 2. 注入 创建一个新建模.json
    if os.path.isfile(flow_create_model_path):
        with open(flow_create_model_path, "r", encoding="utf-8") as f:
            model_data = json.load(f)

        model_step_map = {
            "step_22": center_x_str,
            "step_23": center_y_str,
            "step_24": radius_r_str,
        }

        for step in model_data.get("steps", []):
            sid = step.get("id")
            if sid in model_step_map:
                new_val = model_step_map[sid]
                if "actionConfig" in step and isinstance(step["actionConfig"], dict):
                    step["actionConfig"]["text"] = new_val

        out_model = flow_create_model_path if in_place else flow_create_model_path.replace(".json", "_injected.json")
        with open(out_model, "w", encoding="utf-8") as f:
            json.dump(model_data, f, ensure_ascii=False, indent=2)
        injected["flow_create_model"] = out_model

    return injected


def build_spatial_text_overrides(
    calc_result: Dict[str, Any],
    flow_import_path: Optional[str] = None,
    flow_create_model_path: Optional[str] = None
) -> Dict[str, str]:
    """
    生成适用于 wt_project_workdir_parser.py 的 text_overrides 精确替换字典。
    收集旧流程文件里写死的目标数值，配对替换为当前计算值。
    """
    overrides: Dict[str, str] = {}
    sq = calc_result["square_domain"]
    circles = calc_result["circles"]

    nw_x_str = f"{sq['nw_corner']['x']:.1f}"
    nw_y_str = f"{sq['nw_corner']['y']:.1f}"
    se_x_str = f"{sq['se_corner']['x']:.1f}"
    se_y_str = f"{sq['se_corner']['y']:.1f}"

    center_x_str = f"{sq['center_x']:.3f}"
    center_y_str = f"{sq['center_y']:.3f}"
    radius_r_str = str(int(round(circles["inner_radius_R"])))

    # 1. 从 导入并配置元素 提取旧值
    if flow_import_path and os.path.isfile(flow_import_path):
        try:
            with open(flow_import_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            step_target = {
                "step_10": nw_x_str,
                "step_11": nw_y_str,
                "step_12": se_x_str,
                "step_13": se_y_str,
            }
            for s in d.get("steps", []):
                sid = s.get("id")
                if sid in step_target:
                    old_t = s.get("actionConfig", {}).get("text")
                    if old_t and str(old_t) != step_target[sid]:
                        overrides[str(old_t)] = step_target[sid]
        except Exception:
            pass

    # 2. 从 创建一个新建模 提取旧值
    if flow_create_model_path and os.path.isfile(flow_create_model_path):
        try:
            with open(flow_create_model_path, "r", encoding="utf-8") as f:
                d = json.load(f)
            model_target = {
                "step_22": center_x_str,
                "step_23": center_y_str,
                "step_24": radius_r_str,
            }
            for s in d.get("steps", []):
                sid = s.get("id")
                if sid in model_target:
                    old_t = s.get("actionConfig", {}).get("text")
                    if old_t and str(old_t) != model_target[sid]:
                        overrides[str(old_t)] = model_target[sid]
        except Exception:
            pass

    return overrides


def format_calculation_report(calc_result: Dict[str, Any]) -> str:
    """生成排版优美的计算与注入结果报告"""
    ext = calc_result["extremes"]
    raw = calc_result["raw_bounding_box"]
    sq = calc_result["square_domain"]
    cir = calc_result["circles"]

    lines = [
        "=" * 68,
        "          Meteodyn WT 空间计算域与几何包络计算报告",
        "=" * 68,
        f"输入有效要素点位总数: {calc_result['point_count']} 个",
        f"外扩缓冲边界距离 (Buffer): {calc_result['buffer_m']:.1f} m",
        "-" * 68,
        "【1. 四极边界要素识别】",
        f"  - 极西元素 (Min X): {ext['west_min_x']['name']} (X={ext['west_min_x']['x']:.3f}, Y={ext['west_min_x']['y']:.3f})",
        f"  - 极东元素 (Max X): {ext['east_max_x']['name']} (X={ext['east_max_x']['x']:.3f}, Y={ext['east_max_x']['y']:.3f})",
        f"  - 极南元素 (Min Y): {ext['south_min_y']['name']} (X={ext['south_min_y']['x']:.3f}, Y={ext['south_min_y']['y']:.3f})",
        f"  - 极北元素 (Max Y): {ext['north_max_y']['name']} (X={ext['north_max_y']['x']:.3f}, Y={ext['north_max_y']['y']:.3f})",
        f"  * 原始要素跨度: 东西向 ΔX={raw['span_x']:.2f} m, 南北向 ΔY={raw['span_y']:.2f} m",
        "-" * 68,
        "【2. 正方形计算域 (绘图元素 Interest Area)】",
        f"  - 计算域中心坐标 (Xc, Yc): ({sq['center_x']:.3f}, {sq['center_y']:.3f})",
        f"  - 正方形边长 (L): {sq['side_length']:.2f} m ({sq['side_length']/1000.0:.3f} km)",
        f"  - 西北角坐标 (NW): X={sq['nw_corner']['x']:.1f}, Y={sq['nw_corner']['y']:.1f}",
        f"  - 东南角坐标 (SE): X={sq['se_corner']['x']:.1f}, Y={sq['se_corner']['y']:.1f}",
        "-" * 68,
        "【3. WT 双圆半径】",
        f"  - 内圆半径 (R, 正方形外接圆): {cir['inner_radius_R']:.1f} m (整数米: {int(round(cir['inner_radius_R']))} m)",
        f"  - 外圆半径 (Router = 1.2*√2*R + 2000m): {cir['outer_radius_Router']:.1f} m",
        "-" * 68,
        "【4. 自动化流程注入靶点与数值对应表】",
        "  [流程 1] flow_definition_导入并配置元素.json:",
        f"     * step_10 (键入-西北角-X) -> {sq['nw_corner']['x']:.1f}",
        f"     * step_11 (键入-西北角-Y) -> {sq['nw_corner']['y']:.1f}",
        f"     * step_12 (键入-东南角-X) -> {sq['se_corner']['x']:.1f}",
        f"     * step_13 (键入-东南角-Y) -> {sq['se_corner']['y']:.1f}",
        "  [流程 2] flow_definition_创建一个新建模.json:",
        f"     * step_22 (键入-经度X)   -> {sq['center_x']:.3f}",
        f"     * step_23 (键入-维度Y)   -> {sq['center_y']:.3f}",
        f"     * step_24 (键入-半径R)   -> {int(round(cir['inner_radius_R']))}",
        "=" * 68,
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="WT 空间计算域计算与流程参数注入工具")
    parser.add_argument("--cft", help="测风塔坐标文件路径 (CFT_*.txt)")
    parser.add_argument("--jwd", help="风机机位点坐标文件路径 (JWD_*.txt)")
    parser.add_argument("--dir", help="包含坐标文件的输入目录 (03-WT输入/01-测风塔及机位点坐标)")
    parser.add_argument("--buffer", type=float, default=2500.0, help="外扩缓冲距离 (默认 2500m)")
    parser.add_argument("--inject", action="store_true", help="是否执行注入到流程定义 JSON 文件")
    parser.add_argument("--in-place", action="store_true", default=False, help="覆盖原流程文件 (默认生成 _injected 后缀文件)")
    parser.add_argument("--flow-import", default=r"flow_packages\flow_definition_导入并配置元素.json", help="导入并配置元素 JSON 路径")
    parser.add_argument("--flow-model", default=r"flow_packages\flow_definition_创建一个新建模.json", help="创建一个新建模 JSON 路径")

    args = parser.parse_args()

    points: List[SpatialPoint] = []
    if args.dir:
        points.extend(parse_points_from_input_dir(args.dir))
    if args.cft:
        points.extend(parse_coordinate_file(args.cft, category="CFT"))
    if args.jwd:
        points.extend(parse_coordinate_file(args.jwd, category="JWD"))

    if not points:
        default_dir = r"C:\Users\14830\Desktop\20241227黑龙江依兰泰霆项目\03-WT输入"
        if os.path.isdir(default_dir):
            points.extend(parse_points_from_input_dir(default_dir))

    if not points:
        print("[错误] 未能加载到有效的机位点或测风塔坐标。请指定 --cft, --jwd 或 --dir。")
        return

    result = calculate_spatial_domain(points, buffer_m=args.buffer)
    print(format_calculation_report(result))

    if args.inject:
        injected = inject_into_flow_definitions(
            result,
            flow_import_path=args.flow_import,
            flow_create_model_path=args.flow_model,
            in_place=args.in_place
        )
        print("\n[注入完成]")
        for k, v in injected.items():
            print(f"  {k} -> {v}")


if __name__ == "__main__":
    main()
