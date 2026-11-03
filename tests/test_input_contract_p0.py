# encoding: utf-8
"""P0 输入契约离线回归：chain_manifest 绑定、地形 CRS 闸、曲线/配置/粗糙度槽位。

对应 2026-11-02-input-automation-blueprint §4：A1/A2/A3/A4/A5 + 粗糙度槽位。
全部用临时目录与合成 GeoTIFF，不启动 MUP、不读写真实项目。
"""
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import mup_input_contract as C
import wt_project_workdir_parser as P


def _write_geotiff(path, epsg, bigtiff=False):
    """写一个最小经典 GeoTIFF（只有 GeoKeyDirectory，无像素），用于 CRS 闸测试。"""
    if bigtiff:
        with open(path, "wb") as f:
            f.write(b"II" + struct.pack("<H", 43) + b"\x08\x00\x00\x00\x00\x00\x00\x00")
        return
    # GeoKeyDirectory: version/revision/minor/keyCount + 1 key(3072)
    keys = [1, 1, 0, 1, 3072, 0, 1, int(epsg)]
    geo = struct.pack("<{}H".format(len(keys)), *keys)
    ifd_offset = 8
    entry_count = 1
    data_offset = ifd_offset + 2 + entry_count * 12 + 4
    with open(path, "wb") as f:
        f.write(b"II" + struct.pack("<H", 42) + struct.pack("<I", ifd_offset))
        f.write(struct.pack("<H", entry_count))
        f.write(struct.pack("<HHII", 34735, 3, len(keys), data_offset))
        f.write(struct.pack("<I", 0))
        f.write(geo)


class GeoTiffCrsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_epsg_4531_passes(self):
        path = os.path.join(self.tmp, "ok.tif")
        _write_geotiff(path, 4531)
        info = C.read_geotiff_crs(path)
        self.assertTrue(info["ok"], info)
        self.assertEqual(info["epsg"], 4531)
        self.assertTrue(C.describe_terrain_crs(path)["ok"])

    def test_user_defined_32767_rejected_with_fix_hint(self):
        path = os.path.join(self.tmp, "bad.tif")
        _write_geotiff(path, 32767)
        verdict = C.describe_terrain_crs(path)
        self.assertFalse(verdict["ok"])
        self.assertIn("32767", verdict["message"])
        self.assertIn("gdal_translate -a_srs EPSG:4531", verdict["message"])
        self.assertIn("Key 3072", verdict["message"])

    def test_missing_geokeys_rejected(self):
        path = os.path.join(self.tmp, "plain.tif")
        with open(path, "wb") as f:
            f.write(b"II" + struct.pack("<H", 42) + struct.pack("<I", 8))
            f.write(struct.pack("<H", 0) + struct.pack("<I", 0))
        verdict = C.describe_terrain_crs(path)
        self.assertFalse(verdict["ok"])
        self.assertIn("GeoKeyDirectory", verdict["message"])

    def test_bigtiff_rejected_explicitly(self):
        path = os.path.join(self.tmp, "big.tif")
        _write_geotiff(path, 4531, bigtiff=True)
        info = C.read_geotiff_crs(path)
        self.assertFalse(info["ok"])
        self.assertTrue(info["bigtiff"])
        self.assertIn("BigTIFF", info["error"])


class ChainManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        os.makedirs(os.path.join(self.tmp, "flow_packages"), exist_ok=True)

    def _manifest(self):
        return {
            "version": 1,
            "sections": {
                "terrain": {
                    "flow": "flow_packages/terrain.json",
                    "flowPackageId": "地形导入",
                    "requiresRuntime": ["sourceFilePath"],
                }
            },
        }

    def test_wrong_binding_reports_package_id(self):
        json.dump(self._manifest(), open(os.path.join(self.tmp, "chain_manifest.json"), "w", encoding="utf-8"))
        json.dump(
            {"flowPackages": [{"id": "气象数据录入"}], "steps": [{"actionConfig": {"text": "${runtime.sourceFilePath}"}}]},
            open(os.path.join(self.tmp, "flow_packages", "terrain.json"), "w", encoding="utf-8"),
        )
        manifest, err = C.load_chain_manifest(self.tmp)
        self.assertEqual(err, "")
        problems = C.validate_chain_manifest(manifest, self.tmp, section_keys=["terrain"])
        self.assertTrue(any("不含 flowPackages.id=地形导入" in p for p in problems), problems)

    def test_bound_flow_override_is_validated(self):
        json.dump(self._manifest(), open(os.path.join(self.tmp, "chain_manifest.json"), "w", encoding="utf-8"))
        good = os.path.join(self.tmp, "flow_packages", "terrain.json")
        json.dump(
            {"flowPackages": [{"id": "地形导入"}], "steps": [{"actionConfig": {"text": "${runtime.sourceFilePath}"}}]},
            open(good, "w", encoding="utf-8"),
        )
        wrong = os.path.join(self.tmp, "flow_packages", "other.json")
        json.dump({"flowPackages": [{"id": "别的东西"}]}, open(wrong, "w", encoding="utf-8"))
        manifest, _ = C.load_chain_manifest(self.tmp)
        problems = C.validate_chain_manifest(
            manifest, self.tmp, section_keys=["terrain"], bound_flows={"terrain": wrong}
        )
        self.assertTrue(any("不含 flowPackages.id=地形导入" in p for p in problems), problems)

    def test_repo_manifest_valid_and_flows_consume_keys(self):
        manifest, err = C.load_chain_manifest(PROJECT_DIR)
        self.assertEqual(err, "")
        problems = C.validate_chain_manifest(
            manifest,
            PROJECT_DIR,
            section_keys=["terrain", "project", "weather", "turbine", "element", "cfd", "comprehensive", "export_result"],
        )
        self.assertEqual(problems, [])


class TerrainSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.input = os.path.join(self.tmp, "03-WT输入", "02-地形图")
        os.makedirs(self.input, exist_ok=True)

    def _terrain(self, name, epsg):
        path = os.path.join(self.input, name)
        _write_geotiff(path, epsg)
        return path

    def test_two_tifs_selects_the_one_with_epsg_4531(self):
        self._terrain("原件.tif", 32767)
        fixed = self._terrain("修复版.tif", 4531)
        res = P.parse_project_work_dir(self.tmp)
        rc = res["runtime_config"]
        self.assertEqual(rc.get("sourceFilePath"), fixed)
        self.assertEqual(rc.get("terrainSelection"), "crs_4531")
        self.assertNotIn("terrainAmbiguous", rc)

    def test_two_invalid_tifs_are_ambiguous_not_first(self):
        self._terrain("a.tif", 32767)
        self._terrain("b.tif", 32650)
        res = P.parse_project_work_dir(self.tmp)
        rc = res["runtime_config"]
        self.assertNotIn("sourceFilePath", rc)
        self.assertTrue(rc.get("terrainAmbiguous"))
        self.assertEqual(len(rc.get("terrainCandidates") or []), 2)
        problems = C.check_run_contract("terrain", C.section_spec(C.load_chain_manifest(PROJECT_DIR)[0], "terrain"), rc)
        self.assertTrue(any("不唯一" in p for p in problems), problems)

    def test_explicit_terrain_file_wins(self):
        self._terrain("a.tif", 32767)
        chosen = self._terrain("b.tif", 32767)
        res = P.parse_project_work_dir(self.tmp, {"terrainFile": os.path.basename(chosen)})
        self.assertEqual(res["runtime_config"].get("sourceFilePath"), chosen)


class SlotResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.input = os.path.join(self.tmp, "03-WT输入")
        os.makedirs(self.input, exist_ok=True)

    def test_power_curve_only_reports_missing_thrust(self):
        power = os.path.join(self.input, "04-功率曲线", "WT6250D220_A.4（华润Ⅰ类-0.429）.wtg")
        os.makedirs(os.path.dirname(power), exist_ok=True)
        open(power, "w", encoding="utf-8").write("<WindTurbineGenerator/>")
        res = P.parse_project_work_dir(self.tmp)
        rc = res["runtime_config"]
        self.assertEqual(rc.get("turbineImportFilePath"), power)
        self.assertNotIn("thrustCurveFilePath", rc)
        self.assertIn("thrust", rc.get("missingTurbineFiles") or [])
        manifest, _ = C.load_chain_manifest(PROJECT_DIR)
        problems = C.check_run_contract("turbine", C.section_spec(manifest, "turbine"), rc)
        self.assertTrue(any("推力曲线缺失" in p for p in problems), problems)

    def test_thrust_curve_is_not_substituted_by_wtg(self):
        power = os.path.join(self.input, "04-功率曲线", "WT6250D220.wtg")
        thrust = os.path.join(self.input, "04-功率曲线", "WT6250D220_推力系数.txt")
        os.makedirs(os.path.dirname(power), exist_ok=True)
        open(power, "w", encoding="utf-8").write("<WindTurbineGenerator/>")
        open(thrust, "w", encoding="utf-8").write("ct 0 1\n")
        res = P.parse_project_work_dir(self.tmp)
        rc = res["runtime_config"]
        self.assertEqual(rc.get("thrustCurveFilePath"), thrust)
        self.assertNotIn("missingTurbineFiles", rc)

    def _build_two_masts(self, with_config_for):
        coord = os.path.join(self.input, "01-测风塔及机位点坐标")
        os.makedirs(coord, exist_ok=True)
        with open(os.path.join(coord, "CFT信息.txt"), "w", encoding="utf-8") as fobj:
            fobj.write("C1831 120.1 40.1 100 125 43555697.968 5133880.894\n")
            fobj.write("C1832 120.2 40.2 101 120 43557986.525 5136125.583\n")
        for mast in ("C1831", "C1832"):
            mdir = os.path.join(self.input, "03-测风塔数据", mast)
            os.makedirs(mdir, exist_ok=True)
            open(os.path.join(mdir, f"{mast}-tim.txt"), "w", encoding="utf-8").write("TIM\n")
            open(os.path.join(mdir, f"{mast}-TI.txt"), "w", encoding="utf-8").write("TI\n")
            open(os.path.join(mdir, f"{mast}-TISD.txt"), "w", encoding="utf-8").write("TISD\n")
            if mast == with_config_for:
                open(os.path.join(mdir, f"{mast}配置信息.xml"), "w", encoding="utf-8").write("<config/>")

    def test_config_xml_is_resolved_per_mast(self):
        self._build_two_masts(with_config_for="C1832")
        parsed = P.parse_all_masts(self.tmp)
        by_mast = {p["runtime_config"]["mastName"]: p["runtime_config"] for p in parsed}
        self.assertEqual(by_mast["C1832"]["configSource"], "file")
        self.assertTrue(by_mast["C1832"]["configFilePath"].lower().endswith("c1832配置信息.xml"))
        self.assertEqual(by_mast["C1831"]["configSource"], "none")
        self.assertNotIn("configFilePath", by_mast["C1831"])

    def test_roughness_slot_defaults_to_builtin_and_can_be_overridden(self):
        open(os.path.join(self.input, "CFT.txt"), "w", encoding="utf-8").write("C1 1 1 1\n")
        rc = P.parse_project_work_dir(self.tmp)["runtime_config"]
        self.assertEqual(rc.get("roughnessSource"), "builtin")
        self.assertEqual(rc.get("roughnessIndexFile"), "ESA2020.txt")
        rc2 = P.parse_project_work_dir(self.tmp, {"roughnessIndexFile": "TiffESA2010.txt"})["runtime_config"]
        self.assertEqual(rc2.get("roughnessSource"), "explicit")
        self.assertEqual(rc2.get("roughnessIndexFile"), "TiffESA2010.txt")


class FlowContractTests(unittest.TestCase):
    def _flow(self, name):
        return json.load(open(os.path.join(PROJECT_DIR, "flow_packages", name), encoding="utf-8"))

    def test_terrain_flow_is_complete_and_runtime_driven(self):
        flow = self._flow("flow_definition_导入地形图.json")
        self.assertEqual([p["id"] for p in flow["flowPackages"]], ["地形导入"])
        texts = [s.get("actionConfig", {}).get("text", "") for s in flow["steps"]]
        self.assertIn("${runtime.sourceFilePath}", texts)
        actions = {s["id"]: s["actionConfig"] for s in flow["steps"]}
        self.assertEqual(actions["step_6_wait_apply"]["condition"], "enabled")
        self.assertEqual(actions["step_6_wait_apply"]["onError"], "stop")
        self.assertEqual(actions["step_7_apply"]["action"], "click")
        self.assertEqual(actions["step_7_apply"]["controlId"], "GeoData_Button_Apply_应用_Button")
        # 47 步误拼的气象链不得再留在地形流程里
        self.assertFalse(any((s.get("name") or "").startswith("点击-确认创建新建模") for s in flow["steps"]))

    def test_turbine_flow_uses_two_distinct_curve_slots(self):
        flow = self._flow("flow_definition_新建风机类型.json")
        texts = {s["id"]: s.get("actionConfig", {}).get("text", "") for s in flow["steps"]}
        self.assertEqual(texts["step_27"], "${runtime.turbineImportFilePath}")
        self.assertEqual(texts["step_32"], "${runtime.thrustCurveFilePath}")

    def test_config_import_flow_uses_per_mast_path(self):
        flow = self._flow("flow_definition_新建气象数据_内网_配置导入.json")
        texts = {s["id"]: s.get("actionConfig", {}).get("text", "") for s in flow["steps"]}
        self.assertEqual(texts["step_17"], "${runtime.configFilePath}")

    def test_roughness_step_uses_runtime_index(self):
        flow = self._flow("flow_definition_创建一个新建模.json")
        steps = {s["id"]: s for s in flow["steps"]}
        self.assertEqual(steps["step_20"]["actionConfig"]["value"], "${runtime.roughnessIndexFile}")
        self.assertNotIn("value", steps["step_19"]["actionConfig"])


if __name__ == "__main__":
    unittest.main()
