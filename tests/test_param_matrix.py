# encoding: utf-8
"""参数矩阵（路线B · 非 Agent）离线单测。

验证阶段0能力增量：
1. 展开层：ParameterScanner.scan_from_flow 按参数表把含 ${stepParams.xxx}
   占位的模板展开为完整步骤序列（每步带 stepParams，id 唯一）。
2. 解析层：WT_AUT_recorded._resolve_dynamic_value 在执行期把
   ${stepParams.xxx} 替换为该步 stepParams 的实际值（纯函数，不依赖 LLM）。

其余步骤（真实点击/定位）不在此离线验证范围。
"""
import csv
import json
import os
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from WT_AUTOMATION_Agent.parameter_scan import ParameterScanner, StepModeFilterUnavailable
import WT_AUT_recorded
import wt_project_workdir_parser


TEMPLATE_STEPS = [
    {
        "id": "tpl_1",
        "name": "创建综合 - ${stepParams.synthesisName}",
        "controls": [],
        "actionConfig": {
            "action": "click",
            "text": "${stepParams.synthesisName}",
        },
    },
    {
        "id": "tpl_2",
        "name": "设置 Wohler 指数",
        "controls": [],
        "actionConfig": {
            "action": "set",
            "inputText": "${stepParams.wohler}",
        },
    },
]


class ParamMatrixExpandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="param_matrix_")
        self.flow_path = os.path.join(self.tmp, "flow_definition_test.json")
        self.csv_path = os.path.join(self.tmp, "params.csv")
        # 2 行参数：wohler=4 / 10
        with open(self.csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["synthesisName", "wohler"])
            w.writerow(["综合2", "4"])
            w.writerow(["综合3", "10"])
        # 模板 flow：顶层含 paramTable 字段
        flow = {
            "flowName": "测试流程",
            "paramTable": "params.csv",
            "flow": {"steps": TEMPLATE_STEPS},
        }
        with open(self.flow_path, "w", encoding="utf-8") as f:
            json.dump(flow, f, ensure_ascii=False)

    def tearDown(self):
        for name in ("flow_definition_test.json", "params.csv"):
            p = os.path.join(self.tmp, name)
            if os.path.exists(p):
                os.remove(p)
        if os.path.isdir(self.tmp):
            try:
                os.rmdir(self.tmp)
            except OSError:
                pass

    def test_scan_expands_rows_times_steps(self):
        scanned = ParameterScanner.scan(self.csv_path, template_steps=TEMPLATE_STEPS)
        steps = scanned.get("steps", [])
        # 2 行参数 × 2 个模板步 = 4 步（外加 2 个分隔步，见 scan 实现）
        self.assertTrue(len(steps) >= 4)

    def test_expanded_steps_carry_step_params(self):
        scanned = ParameterScanner.scan(self.csv_path, template_steps=TEMPLATE_STEPS)
        steps = scanned.get("steps", [])
        wohlers = [
            s.get("stepParams", {}).get("wohler")
            for s in steps
            if "tpl_2" in s.get("id", "")
        ]
        self.assertIn("4", wohlers)
        self.assertIn("10", wohlers)

    def test_expanded_step_ids_are_unique(self):
        scanned = ParameterScanner.scan(self.csv_path, template_steps=TEMPLATE_STEPS)
        ids = [s["id"] for s in scanned.get("steps", [])]
        self.assertEqual(len(ids), len(set(ids)))

    def test_no_param_table_is_noop_expansion(self):
        # paramTable 缺失时 _apply_param_table_expansion 应原样返回
        merged = {
            "runtimeConfig": {},
            "flowPackages": [],
            "steps": list(TEMPLATE_STEPS),
        }
        out = WT_AUT_recorded._apply_param_table_expansion(merged, {})
        self.assertIs(out, merged)

    def test_param_table_in_raw_payload_triggers_expansion(self):
        # 关键回归：paramTable 须在原始（未 normalize）payload 取到，
        # 因为 _normalize_payload 会丢弃未知字段。验证从原始 payload 触发展开。
        merged = {
            "runtimeConfig": {},
            "flowPackages": [],
            "steps": list(TEMPLATE_STEPS),
        }
        # 使用绝对路径，避免依赖 FLOW_DEFINITION_FILE 所在目录
        raw_target = {"paramTable": self.csv_path}
        out = WT_AUT_recorded._apply_param_table_expansion(merged, raw_target)
        # 展开后 steps 数 > 原始（含 2 行参数生成的副本 + 分隔步）
        self.assertTrue(len(out["steps"]) > len(TEMPLATE_STEPS))

    def test_normalize_payload_drops_param_table(self):
        # 记录性用例：确认 _normalize_payload 确实丢弃 paramTable（它是
        # _load_flow_payload 的内部嵌套函数，故此处仅文档化调用方须从
        # 原始 payload 取该字段，不可依赖 normalize 后的对象）。
        # 通过验证 _apply_param_table_expansion 从原始 payload 触发展开来覆盖。
        merged = {
            "runtimeConfig": {},
            "flowPackages": [],
            "steps": list(TEMPLATE_STEPS),
        }
        out = WT_AUT_recorded._apply_param_table_expansion(merged, {"paramTable": self.csv_path})
        self.assertTrue(len(out["steps"]) > len(TEMPLATE_STEPS))


class PlaceholderResolveTests(unittest.TestCase):
    def test_resolve_step_params_replaces_placeholders(self):
        # 模拟展开后某步的 stepParams
        step_params = {"synthesisName": "综合2", "wohler": "4"}
        step_id = "tpl_2_scan0_0"
        context = {"step_params": {step_id: step_params}}
        raw = {
            "action": "set",
            "text": "综合: ${stepParams.synthesisName}",
            "inputText": "${stepParams.wohler}",
        }
        resolved = WT_AUT_recorded._resolve_dynamic_value(raw, step_id, context)
        self.assertEqual(resolved["text"], "综合: 综合2")
        self.assertEqual(resolved["inputText"], "4")

    def test_resolve_keeps_unknown_placeholder_until_params_present(self):
        step_id = "x"
        # step_params 中没有该键 → 占位符保留（与既有行为一致）
        context = {"step_params": {step_id: {}}}
        resolved = WT_AUT_recorded._resolve_dynamic_value(
            "${stepParams.unknown}", step_id, context
        )
        self.assertIn("${stepParams.unknown}", resolved)


class StepModeFilterTests(unittest.TestCase):
    """阶段1：行级步骤过滤（stepMode + stepTags）。

    模板步通过 stepTags 声明所属模式：空=通用(所有行执行)；
    create / copy / copyfull 为具体模式。参数表 stepMode 列决定每行包含哪些步。
    """

    def _make_template(self):
        return [
            {"id": "a", "name": "通用步", "stepTags": [],
             "actionConfig": {"action": "click"}, "controls": []},
            {"id": "b", "name": "新建专属", "stepTags": ["create"],
             "actionConfig": {"action": "click"}, "controls": []},
            {"id": "c", "name": "复制组", "stepTags": ["copy", "copyfull"],
             "actionConfig": {"action": "click"}, "controls": []},
            {"id": "d", "name": "绘图组", "stepTags": ["copyfull"],
             "actionConfig": {"action": "click"}, "controls": []},
        ]

    def _write_csv(self, tmp_path, header_row, data_rows):
        p = os.path.join(tmp_path, "m.csv")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(header_row + "\n")
            for r in data_rows:
                f.write(r + "\n")
        return str(p)

    def _group_by_row(self, steps):
        rows = {}
        for s in steps:
            sid = s["id"]
            if "_scan" not in sid:
                continue
            idx = sid.split("_scan")[1].split("_")[0]
            rows.setdefault(idx, set()).add(sid.rsplit("_scan", 1)[0])
        return rows

    def test_step_mode_filters_steps_per_row(self):
        csv = self._write_csv(
            tempfile.mkdtemp(),
            "stepMode,wohler",
            ["create,1", "copy,4", "copyfull,10"],
        )
        result = ParameterScanner.scan(csv, template_steps=self._make_template())
        rows = self._group_by_row(result["steps"])
        self.assertEqual(len(rows), 3)
        keys = sorted(rows.keys(), key=int)
        self.assertEqual(rows[keys[0]], {"a", "b"})       # create: 通用 + 新建
        self.assertEqual(rows[keys[1]], {"a", "c"})       # copy: 通用 + 复制组
        self.assertEqual(rows[keys[2]], {"a", "c", "d"})  # copyfull: 通用 + 复制组 + 绘图组

    def test_no_step_mode_column_backward_compat(self):
        csv = self._write_csv(tempfile.mkdtemp(), "wohler", ["1", "4"])
        result = ParameterScanner.scan(csv, template_steps=self._make_template())
        rows = self._group_by_row(result["steps"])
        for bases in rows.values():
            # 无 stepMode 列 → 退化为旧行为：每行全步
            self.assertEqual(bases, {"a", "b", "c", "d"})

    def test_invalid_step_mode_falls_back_to_all(self):
        csv = self._write_csv(
            tempfile.mkdtemp(),
            "stepMode,wohler",
            ["create,1", "weird,4"],
        )
        result = ParameterScanner.scan(csv, template_steps=self._make_template())
        rows = self._group_by_row(result["steps"])
        keys = sorted(rows.keys(), key=int)
        self.assertEqual(rows[keys[0]], {"a", "b"})            # create 正常过滤
        self.assertEqual(rows[keys[1]], {"a", "b", "c", "d"})  # 非法值 → 全步

    def test_apply_param_table_expansion_respects_step_mode(self):
        # 端到端：_apply_param_table_expansion 走 scan，应同样尊重 stepMode
        merged = {
            "runtimeConfig": {},
            "flowPackages": [],
            "steps": self._make_template(),
        }
        csv = self._write_csv(
            tempfile.mkdtemp(),
            "stepMode,wohler",
            ["create,1", "copy,4"],
        )
        out = WT_AUT_recorded._apply_param_table_expansion(merged, {"paramTable": csv})
        rows = self._group_by_row(out["steps"])
        keys = sorted(rows.keys(), key=int)
        self.assertEqual(rows[keys[0]], {"a", "b"})
        self.assertEqual(rows[keys[1]], {"a", "c"})

    def test_normalize_step_tags(self):
        self.assertEqual(ParameterScanner._normalize_step_tags(None), set())
        self.assertEqual(ParameterScanner._normalize_step_tags([]), set())
        self.assertEqual(
            ParameterScanner._normalize_step_tags(["create", "Copy"]),
            {"create", "copy"},
        )
        self.assertEqual(
            ParameterScanner._normalize_step_tags("copy, copyfull"),
            {"copy", "copyfull"},
        )

    def test_expansion_maps_package_step_ids(self):
        # 展开后，flowPackages 中引用原模板 id 的包 stepIds 必须映射为展开后的 id，
        # 否则 validate_flow_definition 会报"引用了不存在的步骤"（真实流程校验失败的回归）。
        merged = {
            "runtimeConfig": {},
            "flowPackages": [{"id": "pkg1", "name": "pkg1", "stepIds": ["a", "b", "c"]}],
            "steps": self._make_template(),
        }
        csv = self._write_csv(tempfile.mkdtemp(), "stepMode,wohler", ["create,1", "copy,4"])
        out = WT_AUT_recorded._apply_param_table_expansion(merged, {"paramTable": csv})
        pkg = out["flowPackages"][0]
        expanded_ids = {s["id"] for s in out["steps"]}
        self.assertGreater(len(pkg["stepIds"]), 0)
        for pid in pkg["stepIds"]:
            self.assertIn(pid, expanded_ids, f"包引用了不存在的步骤 {pid}")
        # 模板步 a（通用）在两行都有展开实例
        a_ids = [pid for pid in pkg["stepIds"] if pid.startswith("a_scan")]
        self.assertEqual(len(a_ids), 2)

    def test_resolve_steps_to_run_maps_template_ids(self):
        # 参数表展开后步骤 id 带 _scan 后缀；--steps 传模板 id 必须映射，
        # 且保持展开顺序（每行整套线性执行，不能按模板 base 分组乱序）。
        merged = {
            "runtimeConfig": {},
            "flowPackages": [],
            "steps": self._make_template(),
        }
        csv = self._write_csv(tempfile.mkdtemp(), "stepMode,wohler", ["create,1", "copy,4"])
        out = WT_AUT_recorded._apply_param_table_expansion(merged, {"paramTable": csv})
        step_ids = [s["id"] for s in out["steps"]]
        template_ids = ["a", "b", "c", "d"]
        result = WT_AUT_recorded._resolve_steps_to_run(
            step_ids, steps_arg=",".join(template_ids)
        )
        expected = [sid for sid in step_ids if "_scan" in sid]
        self.assertEqual(result, expected, "模板 id 应映射为展开步骤且保持展开顺序")

    def test_resolve_steps_to_run_from_to_maps_template_ids(self):
        merged = {
            "runtimeConfig": {},
            "flowPackages": [],
            "steps": self._make_template(),
        }
        csv = self._write_csv(tempfile.mkdtemp(), "stepMode,wohler", ["create,1", "copy,4"])
        out = WT_AUT_recorded._apply_param_table_expansion(merged, {"paramTable": csv})
        step_ids = [s["id"] for s in out["steps"]]
        result = WT_AUT_recorded._resolve_steps_to_run(step_ids, from_step="b", to_step="c")
        self.assertTrue(result)
        self.assertEqual(result[0].rsplit("_scan", 1)[0], "b")
        self.assertEqual(result[-1].rsplit("_scan", 1)[0], "c")

    def _make_mt_template(self):
        # 阶段2 多塔模板：a通用 / b单塔 / c多塔 / d新建
        return [
            {"id": "a", "name": "通用", "stepTags": [], "towerTags": [],
             "actionConfig": {"action": "click"}, "controls": []},
            {"id": "b", "name": "单塔", "stepTags": ["create", "copyfull"], "towerTags": ["single"],
             "actionConfig": {"action": "click"}, "controls": []},
            {"id": "c", "name": "多塔", "stepTags": ["create", "copyfull"], "towerTags": ["multi"],
             "actionConfig": {"action": "click"}, "controls": []},
            {"id": "d", "name": "新建", "stepTags": ["create"], "towerTags": [],
             "actionConfig": {"action": "click"}, "controls": []},
        ]

    def test_tower_mode_two_dimension_filter(self):
        # stepMode × towerMode 双维 AND：single 行含单塔步、multi 行含多塔步
        csv = self._write_csv(
            tempfile.mkdtemp(),
            "stepMode,towerMode,wohler",
            ["create,single,1", "copyfull,multi,10"],
        )
        result = ParameterScanner.scan(csv, template_steps=self._make_mt_template())
        rows = self._group_by_row(result["steps"])
        keys = sorted(rows.keys(), key=int)
        self.assertEqual(rows[keys[0]], {"a", "b", "d"})   # create+single：无 c
        self.assertEqual(rows[keys[1]], {"a", "c"})        # copyfull+multi：无 b,d

    def test_no_tower_mode_column_defaults_single(self):
        # 无 towerMode 列 → 默认单塔：multi 步骤被排除，single 步骤执行（向后兼容）
        csv = self._write_csv(tempfile.mkdtemp(), "stepMode,wohler", ["create,1"])
        result = ParameterScanner.scan(csv, template_steps=self._make_mt_template())
        rows = self._group_by_row(result["steps"])
        keys = sorted(rows.keys(), key=int)
        self.assertEqual(rows[keys[0]], {"a", "b", "d"})   # 不含 c(multi)

    def test_step_mode_requests_missing_tags_raises(self):
        """stepMode 列存在且含模式的参数行、但模板零 stepTags → 硬失败，不得静默全跑。"""
        template = [{**s, "stepTags": None} for s in self._make_template()]
        csv = self._write_csv(
            tempfile.mkdtemp(), "stepMode,wohler", ["create,1", "copy,4"]
        )
        with self.assertRaises(StepModeFilterUnavailable):
            ParameterScanner.scan(csv, template_steps=template)

    def test_no_step_mode_column_no_tags_backward_compat(self):
        """无 stepMode 列时即使模板零 stepTags 也不抛，保持旧行为（每行全步）。"""
        template = [{**s, "stepTags": None} for s in self._make_template()]
        csv = self._write_csv(tempfile.mkdtemp(), "wohler", ["1", "4"])
        out = ParameterScanner.scan(csv, template_steps=template)
        rows = self._group_by_row(out["steps"])
        for bases in rows.values():
            self.assertEqual(bases, {"a", "b", "c", "d"})

    def test_apply_param_table_expansion_propagates_missing_tags(self):
        """展开入口不得把 StepModeFilterUnavailable 降级吞掉，要穿透为启动失败。"""
        template = [{**s, "stepTags": None} for s in self._make_template()]
        merged = {"runtimeConfig": {}, "flowPackages": [], "steps": template}
        csv = self._write_csv(
            tempfile.mkdtemp(), "stepMode,wohler", ["create,1"]
        )
        with self.assertRaises(RuntimeError):
            WT_AUT_recorded._apply_param_table_expansion(merged, {"paramTable": csv})


class MastOverrideOrderTests(unittest.TestCase):
    """问题1 防回归：多塔"第二座塔"须按 mastEntries（CFT 行序）取，
    不能用 sorted(mastIds)[1]——排序会打乱 CFT 行序，曾把第二塔覆盖成第一塔的气象。
    参见 docs/发送综合计算多塔设置与匹配优化记录_20260827.md。"""

    def _run_expansion(self, runtime):
        tmp = tempfile.mkdtemp(prefix="param_matrix_")
        csv_path = os.path.join(tmp, "params.csv")
        template = [
            {
                "id": "tpl_mast",
                "name": "配塔 ${stepParams.refmast2}",
                "controls": [],
                "actionConfig": {"action": "click", "controlId": "x"},
                "stepParams": {},
            }
        ]
        merged = {"runtimeConfig": {}, "flowPackages": [], "steps": template}
        try:
            with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["refmast", "weathername", "mettowername", "refmast2", "refclimatology2"])
                w.writerow(["M1", "M1", "M1", "Mast1", "Mast1"])
            old = os.environ.get("GM_RUNTIME_CONFIG_JSON")
            os.environ["GM_RUNTIME_CONFIG_JSON"] = json.dumps(runtime, ensure_ascii=False)
            try:
                return WT_AUT_recorded._apply_param_table_expansion(merged, {"paramTable": csv_path})
            finally:
                if old is None:
                    os.environ.pop("GM_RUNTIME_CONFIG_JSON", None)
                else:
                    os.environ["GM_RUNTIME_CONFIG_JSON"] = old
        finally:
            try:
                os.remove(csv_path)
                os.rmdir(tmp)
            except OSError:
                pass

    def test_two_masts_uses_cft_order_not_sorted_mastids(self):
        # CFT 第一塔 W2512960、第二塔 C2536；mastIds 为 sorted(set) → ["C2536","W2512960"]。
        runtime = {
            "mastIds": ["C2536", "W2512960"],
            "mastName": "W2512960",
            "mastEntries": [
                {"mastName": "W2512960", "hubHeight": "140", "meteoName": "W2512960_140_Auto"},
                {"mastName": "C2536", "hubHeight": "140", "meteoName": "C2536_140_Auto"},
            ],
        }
        out = self._run_expansion(runtime)
        steps = [s for s in out["steps"] if s.get("id", "").startswith("tpl_mast")]
        self.assertTrue(steps)
        sp = steps[0].get("stepParams", {})
        self.assertEqual(sp.get("refmast"), "W2512960")             # 第一塔=参考点
        self.assertEqual(sp.get("weathername"), "W2512960_140_Auto")
        self.assertEqual(sp.get("refmast2"), "C2536")               # 第二塔=CFT 第二行
        self.assertEqual(sp.get("refclimatology2"), "C2536_140_Auto")

    def test_single_mast_keeps_second_tower_placeholder(self):
        runtime = {
            "mastIds": ["W2512960"],
            "mastName": "W2512960",
            "mastEntries": [
                {"mastName": "W2512960", "hubHeight": "140", "meteoName": "W2512960_140_Auto"},
            ],
        }
        out = self._run_expansion(runtime)
        steps = [s for s in out["steps"] if s.get("id", "").startswith("tpl_mast")]
        sp = steps[0].get("stepParams", {})
        self.assertEqual(sp.get("refmast2"), "Mast1")               # 单塔：不覆盖第二塔占位
        self.assertEqual(sp.get("refclimatology2"), "Mast1")


class MeteoTextOverrideTests(unittest.TestCase):
    """_meteo_map 数值项（35.0 极风 / 1.220 空气密度）：替换生效与守卫不误发。

    流程模板写死值（step_25=35.0 / step_21=1.220）→ 项目人工确认值。守卫链：
    旧值须存在于模板 actionConfig.text、新值非空、新旧不同——缺一不替换。
    """

    def _build_overrides(self, texts, runtime_config):
        payload = {
            "steps": [
                {"id": "s_{}".format(index), "actionConfig": {"action": "type_text", "text": text}}
                for index, text in enumerate(texts)
            ]
        }
        fd, path = tempfile.mkstemp(suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fobj:
                json.dump(payload, fobj, ensure_ascii=False)
            return wt_project_workdir_parser.build_text_overrides(path, runtime_config)
        finally:
            os.remove(path)

    def test_wind50_replaces_template_text(self):
        overrides = self._build_overrides(["35.0"], {"wind50": "41.5"})
        self.assertEqual(overrides.get("35.0"), "41.5")

    def test_air_density_replaces_template_text(self):
        overrides = self._build_overrides(["1.220"], {"airDensity": "1.15"})
        self.assertEqual(overrides.get("1.220"), "1.15")

    def test_missing_runtime_value_keeps_template_text(self):
        # 未指定项目参数时保持模板值（wind50 缺失不得产生覆盖，更不得残留占位符）
        overrides = self._build_overrides(["35.0", "1.220"], {})
        self.assertNotIn("35.0", overrides)
        self.assertNotIn("1.220", overrides)

    def test_aligned_default_value_no_override(self):
        # 弹窗预填默认值与模板对齐后，"保存未改动"必须零行为变化（防 35.0→50 污染回归）
        overrides = self._build_overrides(["35.0"], {"wind50": "35.0"})
        self.assertNotIn("35.0", overrides)


class ProjectParamsSyncTests(unittest.TestCase):
    """防回归：Launcher 项目参数默认值与 发送综合计算 流程模板写死值必须对齐。

    项目参数弹窗保存会把全部非空预填字段固化进 project_params；默认值与模板不一致时，
    _meteo_map 会把未人工确认的预填默认值静默替换进流程（事故：35.0→50）。
    """

    def test_launcher_defaults_align_with_flow_template(self):
        import WT_Launcher

        self.assertEqual(str(WT_Launcher.DEFAULT_PROJECT_PARAMS.get("wind50", "")), "35.0")
        self.assertEqual(str(WT_Launcher.DEFAULT_PROJECT_PARAMS.get("airDensity", "")), "1.220")

    def test_flow_template_values_unchanged(self):
        flow_path = os.path.join(
            PROJECT_DIR, "flow_packages", "flow_definition_发送综合计算.json"
        )
        with open(flow_path, encoding="utf-8") as fobj:
            payload = json.load(fobj)
        texts = {
            (step.get("id") or ""): (step.get("actionConfig") or {}).get("text")
            for step in (payload.get("steps") or [])
        }
        self.assertEqual(texts.get("step_25"), "35.0")
        self.assertEqual(texts.get("step_21"), "1.220")

    def test_wait_editor_timeout_covers_documented_hang(self):
        # 编辑器加载期 UIA 挂起实测 487-742s；等待超时须覆盖上限（COM 阻塞期超时不可执行，
        # 该值只约束"挂起自恢复后剩余轮询预算"，低于 742s 会让等待步骤白白失败）
        flow_path = os.path.join(
            PROJECT_DIR, "flow_packages", "flow_definition_发送综合计算.json"
        )
        with open(flow_path, encoding="utf-8") as fobj:
            payload = json.load(fobj)
        for step in payload.get("steps") or []:
            if (step.get("id") or "") == "step_copy_wait_editor":
                timeout = (step.get("actionConfig") or {}).get("timeoutSeconds")
                self.assertGreaterEqual(int(timeout or 0), 742)


if __name__ == "__main__":
    unittest.main(verbosity=2)
