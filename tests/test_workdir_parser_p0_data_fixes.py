# encoding: utf-8
"""P0 数据通路修复针对性测试：wt_project_workdir_parser 静默错数据 & 覆盖命中审计。

覆盖（对应逆向置信 v19 / R3 audit 发现）：
- A1  turbineIds 取首字符：不再取路径首字符（C:... 的 “C”），改读机位点坐标文件首列编号
- A2  全角逗号丢行：坐标/参数解析兼容半角/全角空格、半角/全角逗号分隔
- A3  mastIds 混入目录名：只收符合编号规则的塔 id，剔除容器/关键字子目录名
- A4  多高度 tim 选哪个：`_pick_mast_file` 低测量高度文件优先（惯例 10m 参考风速）
- B   覆盖命中审计：`build_text_overrides` 记录必需键命中次数，0 命中列入 zero_hit 诊断
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_project_workdir_parser as P


class TurbineIdsFirstCharTests(unittest.TestCase):
    """A1：turbineIds 不再取路径首字符。"""

    def _build(self, jwd_lines):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        input_dir = os.path.join(tmp, "03-WT输入", "01-测风塔及机位点坐标")
        os.makedirs(input_dir, exist_ok=True)
        path = os.path.join(input_dir, "JWD_Project 43.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(jwd_lines) + "\n")
        return tmp

    def test_turbine_ids_are_numbers_not_path_first_char(self):
        tmp = self._build(["JWD1 43555697.968 5133880.894 125", "JWD2 43557986.525 5136125.583 125"])
        res = P.parse_project_work_dir(tmp)
        self.assertIsNotNone(res)
        rc = res["runtime_config"]
        # 旧 bug：turbineIds=["J"]（路径首字符）。修复后应为机位点编号 JWD1/JWD2。
        self.assertNotIn("J", rc.get("turbineIds", []))
        self.assertEqual(rc.get("turbineIds"), ["JWD1", "JWD2"])
        self.assertEqual(rc.get("turbineIdsJoined"), "JWD1,JWD2")

    def test_turbine_ids_dedup_sorted(self):
        tmp = self._build(["J3 ..", "J1 43547911.351 5144931.285 125", "J3 ..", "J1 .."])
        res = P.parse_project_work_dir(tmp)
        rc = res["runtime_config"]
        self.assertEqual(rc.get("turbineIds"), ["J1", "J3"])


class FullWidthCommaToleranceTests(unittest.TestCase):
    """A2：全角逗号分隔不再吞行。"""

    def test_utm_pair_split_with_fullwidth_comma(self):
        # CFT 坐标行用全角逗号分隔：C1，43555697.968，5133880.894
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        input_dir = os.path.join(tmp, "03-WT输入", "01-测风塔及机位点坐标")
        os.makedirs(input_dir, exist_ok=True)
        with open(os.path.join(input_dir, "CFT_Project 43.txt"), "w", encoding="utf-8") as f:
            f.write("C1，43555697.968，5133880.894，125\n")
        res = P.parse_project_work_dir(tmp)
        self.assertIsNotNone(res)
        rc = res["runtime_config"]
        self.assertEqual(rc.get("utmX"), "43555697.968")
        self.assertEqual(rc.get("utmY"), "5133880.894")

    def test_utm_pair_split_with_mixed_full_and_halfwidth(self):
        self.assertTrue(P._FIELD_SEP_RE.split("43555697.968，5133880.894") == ["43555697.968", "5133880.894"])
        self.assertTrue(P._FIELD_SEP_RE.split("43555697.968,5133880.894") == ["43555697.968", "5133880.894"])

    def test_cft_info_fullwidth_comma_keeps_row(self):
        # CFT信息.txt 全角逗号分隔：行不再因"无半角分隔"被丢弃
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        input_dir = os.path.join(tmp, "03-WT输入", "01-测风塔及机位点坐标")
        os.makedirs(input_dir, exist_ok=True)
        info_path = os.path.join(input_dir, "CFT信息.txt")
        with open(info_path, "w", encoding="utf-8") as f:
            f.write("C1831，116.395645，39.929986，125，120.5，\n")
        entries = P._parse_cft_info_file(info_path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["mastName"], "C1831")
        self.assertEqual(entries[0]["longitude"], "116.395645")
        self.assertEqual(entries[0]["latitude"], "39.929986")

    def test_collect_name_tokens_fullwidth_comma(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        path = os.path.join(tmp, "JWD.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("JWD1，123，456\nJWD2，789，101\n")
        self.assertEqual(P._collect_name_tokens(path), ["JWD1", "JWD2"])


class MastIdsDirectoryNameTests(unittest.TestCase):
    """A3：mastIds 不混入测风塔数据容器/关键字子目录名。"""

    def test_container_and_keyword_dir_names_not_in_mast_ids(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        mast_root = os.path.join(tmp, "03-WT输入", "03-测风塔数据")
        os.makedirs(os.path.join(mast_root, "C1831", "TI"), exist_ok=True)
        os.makedirs(os.path.join(mast_root, "C1831", "TISD"), exist_ok=True)
        # 放一个 tim 文件，使解析器能产出 mastIds（否则无任何输入文件会返回 None）
        with open(os.path.join(mast_root, "C1831", "1831-tim.txt"), "w", encoding="utf-8") as f:
            f.write("")
        # 容器目录自身、关键字子目录名不应混入
        res = P.parse_project_work_dir(tmp)
        self.assertIsNotNone(res)
        rc = res["runtime_config"]
        mast_ids = rc.get("mastIds", [])
        for bad in ("03-测风塔数据", "TI", "TISD", "测风塔数据"):
            self.assertNotIn(bad, mast_ids)
        self.assertIn("C1831", mast_ids)

    def test_tim_files_directly_under_container_do_not_leak_container_name(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        mast_root = os.path.join(tmp, "03-WT输入", "03-测风塔数据")
        os.makedirs(mast_root, exist_ok=True)
        with open(os.path.join(mast_root, "1831-...-tim.txt"), "w", encoding="utf-8") as f:
            f.write("")
        # tim 父目录是容器名「03-测风塔数据」，不应被当塔 id；保证可稳定解析
        mast_ids = P._collect_mast_ids(tmp, [], P._scan_files(os.path.join(tmp, "03-WT输入"), (".txt",)))
        self.assertNotIn("03-测风塔数据", mast_ids)


class MultiHeightTimSelectionTests(unittest.TestCase):
    """A4：多高度 tim 优先低高度文件（10m 惯例参考风速）。"""

    def _dir_with_two_heights(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        mast_root = os.path.join(tmp, "03-WT输入", "03-测风塔数据", "C1831")
        os.makedirs(mast_root, exist_ok=True)
        high = os.path.join(mast_root, "1831-100m-tim.txt")
        low = os.path.join(mast_root, "1831-10m-tim.txt")
        for p in (high, low):
            with open(p, "w", encoding="utf-8") as f:
                f.write("")
        return tmp, high, low

    def test_lowest_height_file_preferred(self):
        tmp, high, low = self._dir_with_two_heights()
        picked = P._pick_mast_file(
            [high, low], "C1831", os.path.join(tmp, "03-WT输入")
        )
        self.assertEqual(os.path.basename(picked), "1831-10m-tim.txt")

    def test_single_height_keeps_first(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        mast_root = os.path.join(tmp, "03-WT输入", "03-测风塔数据", "C1831")
        os.makedirs(mast_root, exist_ok=True)
        only = os.path.join(mast_root, "1831-tim.txt")
        with open(only, "w", encoding="utf-8") as f:
            f.write("")
        picked = P._pick_mast_file([only], "C1831", os.path.join(tmp, "03-WT输入"))
        self.assertEqual(os.path.basename(picked), "1831-tim.txt")

    def test_path_height_extract(self):
        self.assertEqual(P._path_measure_height(r"C:\x\1831-10m-tim.txt"), 10.0)
        self.assertEqual(P._path_measure_height(r"C:\x\1831-100.5m-tim.txt"), 100.5)
        self.assertIsNone(P._path_measure_height(r"C:\x\1831-tim.txt"))


def _write_flow(path, texts):
    payload = {"steps": [{"id": "s_{}".format(i), "actionConfig": {"text": t}} for i, t in enumerate(texts)]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


class OverrideHitAuditTests(unittest.TestCase):
    """B：build_text_overrides 覆盖命中审计（杀静默假成功）。"""

    def _ovr(self, texts, runtime_config, basename="flow_definition_发送综合计算.json"):
        # 默认写"发送综合计算 "家族文件名，使 35.0/1.220 落入该家族纳入必需审计；
        # 覆盖/替换语义与家族判定无关，但必需命中判定依赖它（F1 修复表意）。
        fd, path = tempfile.mkstemp(prefix=basename.replace(".json", "_"), suffix=".json")
        os.close(fd)
        _write_flow(path, texts)
        try:
            return P.build_text_overrides(path, runtime_config)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_audit_key_attached_and_required_tracked(self):
        overrides = self._ovr(["35.0", "1.220"], {"wind50": "41.5", "airDensity": "1.15"})
        audit = overrides.get(P._OVERRIDE_AUDIT_KEY)
        self.assertIsNotNone(audit)
        self.assertIn("35.0", audit["required"])
        self.assertIn("1.220", audit["required"])
        self.assertEqual(audit["hits"]["35.0"], 1)
        self.assertNotIn("35.0", audit["zero_hit"])

    def test_required_key_with_zero_hit_is_reported(self):
        # 综合家族 flow：模板不含本家族必备 35.0/1.220（流程已改版）→ 0 命中必须被诊断而非静默
        overrides = self._ovr(["50.0"], {"wind50": "41.5", "airDensity": "1.15"})
        audit = overrides.get(P._OVERRIDE_AUDIT_KEY)
        # 35.0/1.220 属综合家族必需键：即使模板不含也登记为必需 → 0 命中硬失败
        self.assertIn("35.0", audit["required"])
        self.assertIn("1.220", audit["required"])
        self.assertIn("35.0", audit["zero_hit"])
        self.assertIn("1.220", audit["zero_hit"])
        # 空间域(5000)/meteo(125) 不属综合家族必备 → 不因缺失被拒启（F1 收窄核心）
        self.assertNotIn("5000", audit["required"])
        self.assertNotIn("125", audit["required"])
        self.assertEqual(len(audit["zero_hit"]), 2)

    def test_zero_hit_empty_when_all_required_hit(self):
        # 综合家族 flow：35.0/1.220 都命中 → zero_hit 空。空间域(5000)与 meteo(125) 旧文本
        # 虽实际命中并进入 overrides 做字面替换，但不属综合家族必备键，不得进 required（F1 收窄）。
        overrides = self._ovr(["35.0", "1.220", "5000", "125"],
                              {"wind50": "41.5", "airDensity": "1.15",
                               "domainRadiusR": "6000", "hubHeight": "140"})
        audit = overrides.get(P._OVERRIDE_AUDIT_KEY)
        self.assertEqual(sorted(audit["required"]), ["1.220", "35.0"])
        self.assertEqual(audit["zero_hit"], [])
        # 命中但非该家族必备：仍做字面替换，只是不纳入硬失败审计
        self.assertEqual(overrides.get("5000"), "6000")
        self.assertEqual(overrides.get("125"), "140")

    def test_audit_passthrough_on_missing_flow(self):
        overrides = P.build_text_overrides(os.path.join(tempfile.gettempdir(), "no_such_flow.json"), {})
        audit = overrides.get(P._OVERRIDE_AUDIT_KEY)
        self.assertIsNotNone(audit)
        self.assertEqual(audit["required"], [])
        self.assertEqual(audit["zero_hit"], [])


class FlowFamilyScopedOverrideTests(unittest.TestCase):
    """F1（T4 复核 P0 组合缺陷）：必需键按 flow 家族收窄，不再跨 flow 误伤。

    旧实现：只要项目参数提供新值，就把全部 meteo 字面量（CFT01/99/40.5/120.5/125）
    与空间键无条件加进每个 flow 的 required_keys。于是 Simple 队列每个板块（含气象板块）
    都会被 zero_hit=['CFT01','CFT1'] 拒启（CFT1 任何 flow 都不含；CFT01 仅新建气象数据含）。

    本回归用真实 flow 文件 + 合成 rc（测风塔/气象信息齐全）钉死处理后行为：
      - 非气象/非建模 flow（发送CFD/创建一个新建模/发送综合）不得被 meteo 字面量拒启；
      - CFT1 彻底移除，任何 flow 的 zero_hit 都不含它；
      - 气象 flow 缺必需键（如故意缺 meteoName 且 CFT01 不在其文本）仍 0 命中硬失败。
    """

    _METEO_RC = {
        "meteoName": "塔1_140_Auto",
        "mastName": "塔1",
        "elevation": "98",
        "latitude": "40.3",
        "longitude": "120.1",
        "hubHeight": "140",
        "wind50": "41.5",
        "airDensity": "1.19",
        "domainCenterX": "43558000",
        "domainCenterY": "5136200",
        "domainRadiusR": "6000",
        "domainNwX": "43547000",
        "domainNwY": "5152000",
        "domainSeX": "43560000",
        "domainSeY": "5139000",
    }

    _METEO_LITERALS = ["CFT01", "99", "40.5", "120.5", "125"]

    def _ovr_for(self, flow_basename):
        flow_path = os.path.join(PROJECT_DIR, "flow_packages", flow_basename)
        overrides = P.build_text_overrides(flow_path, dict(self._METEO_RC))
        return overrides.get(P._OVERRIDE_AUDIT_KEY)

    def test_meteo_literals_do_not_reject_send_cfd(self):
        audit = self._ovr_for("flow_definition_发送CFD计算.json")
        for lit in self._METEO_LITERALS:
            self.assertNotIn(lit, audit["required"], "发送CFD 不得被 meteo 字面量硬失败")
        self.assertEqual(audit["zero_hit"], [])

    def test_composite_flow_not_rejected_by_meteo_literals(self):
        # 发送综合计算属"综合"家族：只对 35.0/1.220 纳入必需，不含 CFT01/CFT1 等 meteo 键
        audit = self._ovr_for("flow_definition_发送综合计算.json")
        for lit in self._METEO_LITERALS:
            self.assertNotIn(lit, audit["required"])
        self.assertIn("35.0", audit["required"])
        self.assertEqual(audit["zero_hit"], [])

    def test_model_flow_not_rejected_by_meteo_literals(self):
        # 创建一个新建模 属"建模"家族：只对空间域 3 键纳入必需，不含 meteo 字面量
        audit = self._ovr_for("flow_definition_创建一个新建模.json")
        for lit in self._METEO_LITERALS:
            self.assertNotIn(lit, audit["required"])
        self.assertIn("43557986.525", audit["required"])
        self.assertEqual(audit["zero_hit"], [])

    def test_cft1_never_anywhere(self):
        # CFT1 纯噪音键已彻底移除：任何 flow 的 required / zero_hit 都不含它
        for flow in ("flow_definition_新建气象数据_内网.json",
                     "flow_definition_发送CFD计算.json",
                     "flow_definition_发送综合计算.json",
                     "flow_definition_创建一个新建模.json",
                     "flow_definition_导入并配置元素.json"):
            audit = self._ovr_for(flow)
            self.assertNotIn("CFT1", audit["required"], "CFT1 不应出现在任一 flow 的必需键")
            self.assertNotIn("CFT1", audit["zero_hit"])

    def test_meteo_flow_only_required_on_its_keys(self):
        # 新建气象数据 属气象家族：只对 meteo 键纳入必需；空间键不属其必备
        audit = self._ovr_for("flow_definition_新建气象数据_内网.json")
        for lit in self._METEO_LITERALS:
            self.assertIn(lit, audit["required"])
        self.assertNotIn("5000", audit["required"])
        self.assertEqual(audit["zero_hit"], [])

    def test_meteo_flow_missing_meteo_name_hard_fails(self):
        # 接受准则直译：一个气象 flow 故意缺 meteoName → CFT01 仍登记必需并列入 zero_hit
        # （项目没配该气象对象名，跑起来会用错数据，必须拒启；不因收窄而放松）。
        flow_path = os.path.join(PROJECT_DIR, "flow_packages", "flow_definition_新建气象数据_内网.json")
        rc = dict(self._METEO_RC)
        rc["meteoName"] = ""
        rc["mastName"] = ""       # 也无 mastName 兜底 → CFT01 无新值可替换
        overrides = P.build_text_overrides(flow_path, rc)
        audit = overrides.get(P._OVERRIDE_AUDIT_KEY)
        self.assertIn("CFT01", audit["required"])
        self.assertIn("CFT01", audit["zero_hit"])

    def test_meteo_flow_drift_literal_hard_fails(self):
        # 模板改版/旧值漂移：气象 flow 出现本家族必备 meteo 键但模板不再含该旧值 →
        # 仍登记为必需、列入 0 命中硬失败（保留 fail-closed，不因收窄而放松）。
        fd, path = tempfile.mkstemp(prefix="flow_definition_新建气象数据_drift_", suffix=".json")
        os.close(fd)
        _write_flow(path, ["CFT01", "99", "40.5", "120.5"])  # 缺 125
        try:
            overrides = P.build_text_overrides(path, dict(self._METEO_RC))
            audit = overrides.get(P._OVERRIDE_AUDIT_KEY)
            self.assertIn("125", audit["required"])
            self.assertIn("125", audit["zero_hit"])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass

    def test_meteo_flow_full_rc_does_not_reject(self):
        # 齐全 rc 下气象 flow 全部 meteo 键都命中、zero_hit 为空（不误拒）：
        # 这是 F1 的另一面——修好后气象板块本身也不应被自己所需但已完整配置的键拒启。
        audit = self._ovr_for("flow_definition_新建气象数据_内网.json")
        self.assertEqual(audit["zero_hit"], [])


if __name__ == "__main__":
    unittest.main()