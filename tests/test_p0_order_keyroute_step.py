# encoding: utf-8
"""P0 数据通路修复第一波 · 离线自检（tests/ 目录，可离线运行，无 GUI）。

覆盖三处修复（见 docs 落盘 _P0-order-keyroute.md）：
  A) ORDER-FIX   段序重排：SIMPLE_SECTIONS 即执行序驱动点，需符合依赖拓扑。
  B1) KEY-ROUTE  S4 导入槽：元素流程改投喂 mastCoordFilePath / turbineCoordFilePath
                 坐标文件（旧误用 mastImportFilePath=tim / turbineImportFilePath=wtg）。
  B2/B3) KEY-ROUTE  S1 项目/地形/投影键：projectName/terrainName/projectionAbbrev
                 在「创建一个新建模」流程中接上占位符消费者；投影选择 fail-closed。
"""
import json
import os
import sys
import tempfile

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_project_workdir_parser
from wt_flow_validation import validate_flow_definition


# ── A) 段序驱动机制真相 + 依赖拓扑 ──
EXPECTED_ORDER = ["terrain", "project", "weather", "turbine", "element", "cfd", "comprehensive", "export_result"]

def _launcher_simple_sections():
    """从 WT_Launcher 提取 SIMPLE_SECTIONS 的 key 序（不导入 Tk，纯 AST/正则可离线下读取）。"""
    launcher_src = open(os.path.join(PROJECT_DIR, "WT_Launcher.py"), encoding="utf-8").read()
    block_start = launcher_src.index("SIMPLE_SECTIONS = [")
    block_end = launcher_src.index("]", block_start)
    block = launcher_src[block_start:block_end]
    # SIMPLE_SECTIONS 字典字面量里，每项形如 {"key": "terrain", "title": ..., "icon": ...}
    # 直接按 "key": "xxx" 取（该字符串在 [ ... ] 封闭块内，不会误匹配其它内容）
    keys = __import__("re").findall(r'"key":\s*"([^"]+)"', block)
    return keys


def test_section_order_is_dependency_topology():
    """A) SIMPLE_SECTIONS 是真实执行序驱动点（_run_simple_mode 依此入队），且已按依赖拓扑重排。"""
    order = _launcher_simple_sections()
    assert order == EXPECTED_ORDER, (
        "SIMPLE_SECTIONS 执行序不符合目标拓扑：\n  got {}\n  exp {}".format(order, EXPECTED_ORDER)
    )
    # 依赖不变量：terrain 与 project 必须先于 weather/turbine/element
    idx = {k: i for i, k in enumerate(order)}
    assert idx["terrain"] < idx["project"] < idx["weather"]
    assert idx["weather"] < idx["turbine"] < idx["element"]
    assert idx["cfd"] < idx["comprehensive"] < idx["export_result"]


def test_section_order_has_explanatory_comments():
    """A) 段序四周须有中文注释说明依赖依据（防止后续被无注释重排回错乱序）。"""
    launcher_src = open(os.path.join(PROJECT_DIR, "WT_Launcher.py"), encoding="utf-8").read()
    block_start = launcher_src.index("# ── Simple 模式板块执行序")
    block_end = launcher_src.index("]", block_start)
    comment_block = launcher_src[block_start:block_end]
    for kw in ("terrain", "project", "weather", "element", "cfd"):
        assert kw in comment_block, "注释块缺少对 {} 的依赖说明".format(kw)


# ── B1) S4 导入槽：parser 产出坐标键 + 元素流程消费坐标键 ──
def _build_sample_workdir(work_dir):
    """构造样例项目目录：含 CFT(测风塔坐标)/JWD(机位坐标)/tim(时段)/wtg(功率曲线)。"""
    input_dir = os.path.join(work_dir, "03-WT输入")
    coord_dir = os.path.join(input_dir, "01-测风塔及机位点坐标")
    mast_dir = os.path.join(input_dir, "03-测风塔数据", "C1831")
    power_dir = os.path.join(input_dir, "04-功率曲线")
    for d in (coord_dir, mast_dir, power_dir):
        os.makedirs(d, exist_ok=True)
    # 测风塔坐标文件（CFT）与机位坐标文件（JWD）
    cft = os.path.join(coord_dir, "CFT_Project_CGCS2000 43.txt")
    jwd = os.path.join(coord_dir, "JWD_Project_CGCS2000 43.txt")
    with open(cft, "w", encoding="utf-8") as f:
        f.write("CFT01 43555697.968 5133880.894 125\n")
    with open(jwd, "w", encoding="utf-8") as f:
        f.write("JWD1 43557986.525 5136125.583 125\nJWD2 43547911.351 5144931.285 125\n")
    # 测风塔时段数据（*-tim.txt）与功率曲线（*.wtg）
    tim = os.path.join(mast_dir, "1831-C1831-tim.txt")
    with open(tim, "w", encoding="utf-8") as f:
        f.write("TIM\n1 2 3\n")
    wtg = os.path.join(power_dir, "WT6250D220.wtg")
    with open(wtg, "w", encoding="utf-8") as f:
        f.write("WIND TURBINE\n")
    return {"cft": cft, "jwd": jwd, "tim": tim, "wtg": wtg}


def test_parser_produces_coord_keys_separate_from_data_keys():
    """B1) parser 应同时产出 mast/turbine 坐标键与数据键，且二者指向不同文件。"""
    with tempfile.TemporaryDirectory() as tmp:
        paths = _build_sample_workdir(tmp)
        res = wt_project_workdir_parser.parse_project_work_dir(tmp)
        assert res is not None
        rc = res["runtime_config"]
        # 坐标键指向真正的坐标文件
        assert os.path.basename(rc["mastCoordFilePath"]).lower().startswith("cft")
        assert os.path.basename(rc["turbineCoordFilePath"]).lower().startswith("jwd")
        # 数据键仍指向时段/功率曲线（与坐标键语义区分，不互换）
        assert rc["mastImportFilePath"].lower().endswith("-tim.txt")
        assert rc["turbineImportFilePath"].lower().endswith(".wtg")
        # 坐标键 ≠ 数据键（曾误喂对象）
        assert os.path.normcase(rc["mastCoordFilePath"]) != os.path.normcase(rc["mastImportFilePath"])
        assert os.path.normcase(rc["turbineCoordFilePath"]) != os.path.normcase(rc["turbineImportFilePath"])


def test_element_flow_consumes_coord_keys():
    """B1) 「导入并配置元素」(S4) 两个导入槽改用坐标键，不再引用 tim/wtg。"""
    flow = json.load(open(os.path.join(PROJECT_DIR, "flow_packages", "flow_definition_导入并配置元素.json"), encoding="utf-8"))
    texts = [s.get("actionConfig", {}).get("text", "") for s in flow.get("steps", []) if isinstance(s.get("actionConfig"), dict)]
    assert "${runtime.mastCoordFilePath}" in texts
    assert "${runtime.turbineCoordFilePath}" in texts
    # 旧误用键不应再出现在本导入流程的占位符里
    assert "${runtime.mastImportFilePath}" not in texts
    assert "${runtime.turbineImportFilePath}" not in texts


# ── B2/B3) S1 项目/地形/投影键消费 + 投影 fail-closed ──
def test_project_create_flow_consumes_s1_keys():
    """B2) 「创建一个新建模」流程用 projectName/terrainName/projectionAbbrev 占位符。"""
    flow = json.load(open(os.path.join(PROJECT_DIR, "flow_packages", "flow_definition_创建一个新建模.json"), encoding="utf-8"))
    steps = {s["id"]: s for s in flow.get("steps", []) if isinstance(s.get("actionConfig"), dict)}
    assert steps["step_3"]["actionConfig"]["text"] == "${runtime.projectName}"
    assert steps["step_14"]["actionConfig"]["text"] == "${runtime.terrainName}"
    assert steps["step_9"]["actionConfig"]["text"] == "${runtime.projectionAbbrev}"
    # 流程 runtimeConfig 提供兜底默认（未指定项目文件夹时保持原有行为）
    rc = flow.get("runtimeConfig", {})
    assert rc.get("projectName") and rc.get("terrainName") and rc.get("projectionAbbrev")


def test_projection_selection_fail_closed():
    """B3) 投影查找(step_9)与投影选择(step_10)对失败/选错 fail-closed（onError=stop）。"""
    flow = json.load(open(os.path.join(PROJECT_DIR, "flow_packages", "flow_definition_创建一个新建模.json"), encoding="utf-8"))
    steps = {s["id"]: s for s in flow.get("steps", []) if isinstance(s.get("actionConfig"), dict)}
    assert steps["step_9"]["actionConfig"]["onError"] == "stop"   # 查找投影键值；找不到即停
    assert steps["step_10"]["actionConfig"]["onError"] == "stop"  # 下拉无匹配项时点不到，即停


def test_edited_flows_still_validate():
    """编辑后的两份流程仍应通过 validate_flow_definition（不破坏 schema 契约）。"""
    for name in ("flow_definition_创建一个新建模.json", "flow_definition_导入并配置元素.json"):
        flow = json.load(open(os.path.join(PROJECT_DIR, "flow_packages", name), encoding="utf-8"))
        errs = validate_flow_definition(flow)
        assert not errs, "{} 校验失败：{}".format(name, errs)


if __name__ == "__main__":
    failures = []
    import traceback
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS  {}".format(name))
            except Exception:
                failures.append(name)
                print("FAIL  {}".format(name))
                traceback.print_exc()
    if failures:
        print("\n{} assertion failed: {}".format(len(failures), failures))
        sys.exit(1)
    print("\n全部离线自检通过")