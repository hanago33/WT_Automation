# encoding: utf-8
"""
单元测试：WT 空间计算域与参数注入模块 (tests/test_spatial_domain_calc.py)
"""

import os
import json
import math
import tempfile
import pytest

from wt_spatial_domain_calc import (
    SpatialPoint,
    parse_coordinate_file,
    parse_points_from_input_dir,
    calculate_spatial_domain,
    inject_into_flow_definitions,
    build_spatial_text_overrides,
    format_calculation_report
)


def test_spatial_point_and_extremes():
    pts = [
        SpatialPoint("P1", 100000.0, 1000000.0, 120.0, "JWD"),
        SpatialPoint("P2", 105000.0, 1003000.0, 120.0, "JWD"),
        SpatialPoint("P3", 102000.0, 1008000.0, 120.0, "CFT"),
    ]

    res = calculate_spatial_domain(pts, buffer_m=2500.0, round_radius_int=True)
    ext = res["extremes"]
    assert ext["west_min_x"]["name"] == "P1"
    assert ext["east_max_x"]["name"] == "P2"
    assert ext["south_min_y"]["name"] == "P1"
    assert ext["north_max_y"]["name"] == "P3"

    raw = res["raw_bounding_box"]
    assert raw["min_x"] == 100000.0
    assert raw["max_x"] == 105000.0
    assert raw["min_y"] == 1000000.0
    assert raw["max_y"] == 1008000.0
    assert raw["span_x"] == 5000.0
    assert raw["span_y"] == 8000.0

    sq = res["square_domain"]
    # center
    assert sq["center_x"] == 102500.0
    assert sq["center_y"] == 1004000.0

    # buffer = 2500m on both sides:
    # req_w = 5000 + 5000 = 10000
    # req_h = 8000 + 5000 = 13000
    # square_side = max(10000, 13000) = 13000
    assert sq["side_length"] == 13000.0

    # nw corner = (102500 - 6500, 1004000 + 6500) = (96000.0, 1010500.0)
    assert sq["nw_corner"]["x"] == 96000.0
    assert sq["nw_corner"]["y"] == 1010500.0

    # se corner = (102500 + 6500, 1004000 - 6500) = (109000.0, 997500.0)
    assert sq["se_corner"]["x"] == 109000.0
    assert sq["se_corner"]["y"] == 997500.0

    cir = res["circles"]
    expected_r = round((math.sqrt(2.0) / 2.0) * 13000.0)  # ~ 9192.388 -> 9192
    assert cir["inner_radius_R"] == expected_r
    expected_outer = round(1.2 * math.sqrt(2.0) * expected_r + 2000.0)
    assert cir["outer_radius_Router"] == expected_outer


def test_parse_coordinate_file():
    content = """
    # 测风塔及机位坐标测试数据
    CFT01 43555697.968 5133880.894 125
    JWD01 43557986.525 5136125.583 125
    JWD02 43547911.351 5144931.285 125
    """
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".txt") as f:
        f.write(content)
        temp_path = f.name

    try:
        pts = parse_coordinate_file(temp_path)
        assert len(pts) == 3
        assert pts[0].name == "CFT01"
        assert pts[0].x == pytest.approx(43555697.968)
        assert pts[0].y == pytest.approx(5133880.894)
        assert pts[0].z == 125.0
        assert pts[1].name == "JWD01"
        assert pts[2].name == "JWD02"
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def test_injection_into_flow_definitions():
    mock_import_flow = {
        "version": "1.0",
        "steps": [
            {
                "id": "step_10",
                "name": "键入-西北角-X",
                "actionConfig": {"action": "type_text", "text": "0.0"},
                "description": "为西北角X键入 0.0"
            },
            {
                "id": "step_11",
                "name": "键入-西北角-Y",
                "actionConfig": {"action": "type_text", "text": "0.0"},
                "description": "为西北角Y键入 0.0"
            },
            {
                "id": "step_12",
                "name": "键入-东南角-X",
                "actionConfig": {"action": "type_text", "text": "0.0"},
                "description": "为东南角X键入 0.0"
            },
            {
                "id": "step_13",
                "name": "键入-东南角-Y",
                "actionConfig": {"action": "type_text", "text": "0.0"},
                "description": "为东南角Y键入 0.0"
            },
        ]
    }

    mock_model_flow = {
        "version": "1.0",
        "steps": [
            {
                "id": "step_22",
                "name": "键入-经度X",
                "actionConfig": {"action": "type_text", "text": "0.0"}
            },
            {
                "id": "step_23",
                "name": "键入-维度Y",
                "actionConfig": {"action": "type_text", "text": "0.0"}
            },
            {
                "id": "step_24",
                "name": "键入-半径R",
                "actionConfig": {"action": "type_text", "text": "0"}
            },
        ]
    }

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as f1, \
         tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as f2:
        json.dump(mock_import_flow, f1, ensure_ascii=False)
        json.dump(mock_model_flow, f2, ensure_ascii=False)
        p1 = f1.name
        p2 = f2.name

    try:
        pts = [
            SpatialPoint("C1831", 43555697.968, 5133880.894, 125, "CFT"),
            SpatialPoint("JWD1", 43557986.525, 5136125.583, 125, "JWD"),
            SpatialPoint("JWD2", 43547911.351, 5144931.285, 125, "JWD"),
            SpatialPoint("JWD3", 43552426.181, 5156085.437, 125, "JWD"),
        ]
        res = calculate_spatial_domain(pts, buffer_m=2500.0)

        injected = inject_into_flow_definitions(res, p1, p2, in_place=True)
        assert injected["flow_import"] == p1
        assert injected["flow_create_model"] == p2

        with open(p1, "r", encoding="utf-8") as f:
            d1 = json.load(f)
        with open(p2, "r", encoding="utf-8") as f:
            d2 = json.load(f)

        s10 = next(s for s in d1["steps"] if s["id"] == "step_10")
        s11 = next(s for s in d1["steps"] if s["id"] == "step_11")
        s12 = next(s for s in d1["steps"] if s["id"] == "step_12")
        s13 = next(s for s in d1["steps"] if s["id"] == "step_13")

        assert s10["actionConfig"]["text"] == "43539346.7"
        assert s11["actionConfig"]["text"] == "5158585.4"
        assert s12["actionConfig"]["text"] == "43566551.2"
        assert s13["actionConfig"]["text"] == "5131380.9"

        s22 = next(s for s in d2["steps"] if s["id"] == "step_22")
        s23 = next(s for s in d2["steps"] if s["id"] == "step_23")
        s24 = next(s for s in d2["steps"] if s["id"] == "step_24")

        assert s22["actionConfig"]["text"] == "43552948.938"
        assert s23["actionConfig"]["text"] == "5144983.166"
        assert s24["actionConfig"]["text"] == "19237"

    finally:
        for p in (p1, p2):
            if os.path.exists(p):
                os.remove(p)


def test_build_spatial_text_overrides():
    pts = [
        SpatialPoint("P1", 100000.0, 1000000.0, 120.0),
        SpatialPoint("P2", 110000.0, 1010000.0, 120.0),
    ]
    res = calculate_spatial_domain(pts, buffer_m=2500.0)

    # 模拟旧模板里的写死值
    mock_import = {
        "steps": [
            {"id": "step_10", "actionConfig": {"text": "OLD_NW_X"}},
            {"id": "step_11", "actionConfig": {"text": "OLD_NW_Y"}},
            {"id": "step_12", "actionConfig": {"text": "OLD_SE_X"}},
            {"id": "step_13", "actionConfig": {"text": "OLD_SE_Y"}},
        ]
    }
    mock_model = {
        "steps": [
            {"id": "step_22", "actionConfig": {"text": "OLD_CENTER_X"}},
            {"id": "step_23", "actionConfig": {"text": "OLD_CENTER_Y"}},
            {"id": "step_24", "actionConfig": {"text": "OLD_R"}},
        ]
    }

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as f1, \
         tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as f2:
        json.dump(mock_import, f1)
        json.dump(mock_model, f2)
        p1, p2 = f1.name, f2.name

    try:
        ovr = build_spatial_text_overrides(res, p1, p2)
        assert "OLD_NW_X" in ovr
        assert "OLD_NW_Y" in ovr
        assert "OLD_CENTER_X" in ovr
        assert "OLD_R" in ovr
    finally:
        for p in (p1, p2):
            if os.path.exists(p):
                os.remove(p)


def test_workdir_parser_spatial_integration():
    from wt_project_workdir_parser import parse_project_work_dir
    import tempfile, shutil

    temp_dir = tempfile.mkdtemp()
    try:
        input_dir = os.path.join(temp_dir, "03-WT输入", "01-测风塔及机位点坐标")
        os.makedirs(input_dir, exist_ok=True)

        cft_file = os.path.join(input_dir, "CFT_test.txt")
        jwd_file = os.path.join(input_dir, "JWD_test.txt")

        with open(cft_file, "w", encoding="utf-8") as f:
            f.write("C1 43555697.968 5133880.894 125\n")
        with open(jwd_file, "w", encoding="utf-8") as f:
            f.write("J1 43557986.525 5136125.583 125\nJ2 43547911.351 5144931.285 125\nJ3 43552426.181 5156085.437 125\n")

        res = parse_project_work_dir(temp_dir)
        assert res is not None
        rc = res["runtime_config"]
        assert rc["domainCenterX"] == "43552948.938"
        assert rc["domainCenterY"] == "5144983.166"
        assert rc["domainRadiusR"] == "19237"
        assert rc["domainNwX"] == "43539346.7"
        assert rc["domainNwY"] == "5158585.4"
        assert rc["domainSeX"] == "43566551.2"
        assert rc["domainSeY"] == "5131380.9"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
