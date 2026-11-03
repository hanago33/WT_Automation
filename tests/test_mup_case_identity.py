# encoding: utf-8
"""Case 身份解析（`mup_case_identity`）+ 真实 DEMO 17 文件族识别（U-11）自检。

两块：
  1. `resolve_case_identity` 四级链：GUID 直通 / `{site}_` mock / 注入的 DB 查询 /
     Vault 唯一 GUID 启发 / unresolved（多 GUID 不猜）。
  2. **U-11 裁决**（蓝图 §8）：用 `2026-11-02-demo-export-directfetch.md` §1 的
     真实 17 个 DEMO 文件名跑族识别——`.wrtd`、`timeserie{,park}.dat`、双 GUID
     `.wrm` 必须全部被 `is_vault_synthesis_product` 认出来，否则 optional 族不进
     `_family_hits`，failed(partial) 的活动判据失真。

全部零真实盘：身份解析注入假 listdir；U-11 只对常量文件名做纯函数断言。
"""
import os
import sys
import tempfile
import unittest

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import mup_case_identity as mci
import mup_vault_names as mvn

DEMO_CASE = "ea368c7f-9810-487d-be4c-bbcc9339c83e"
DEMO_ELEM = "2d459fd9-c918-418a-8e7f-52d9e7b4db8f"
OTHER_CASE = "11111111-2222-3333-4444-555555555555"

# 真实 DEMO SYNTHESIS_FILES 的 17 个文件（顺序与报告一致）
DEMO_FILES = (
    ("%s_synthesis_2.wri" % DEMO_CASE, 2, "synthesis", ".wri"),
    ("%s_synthesis_2.wrp" % DEMO_CASE, 2, "synthesis", ".wrp"),
    ("%s_synthesis_3.wri" % DEMO_CASE, 3, "synthesis", ".wri"),
    ("%s_synthesis_3.wrp" % DEMO_CASE, 3, "synthesis", ".wrp"),
    ("%s_%s_3.wrm" % (DEMO_CASE, DEMO_ELEM), 3, "map", ".wrm"),
    ("%s_synthesis_4.wri" % DEMO_CASE, 4, "synthesis", ".wri"),
    ("%s_synthesis_4.wrp" % DEMO_CASE, 4, "synthesis", ".wrp"),
    ("%s_synthesis_4.wrt" % DEMO_CASE, 4, "synthesis", ".wrt"),
    ("%s_synthesis_deviation_4.wrtd" % DEMO_CASE, 4, "wrtd", ".wrtd"),
    ("%s_synthesis_inflow_4.wrtd" % DEMO_CASE, 4, "wrtd", ".wrtd"),
    ("%s_synthesis_rewsshear_4.wrtd" % DEMO_CASE, 4, "wrtd", ".wrtd"),
    ("%s_synthesis_shear_4.wrtd" % DEMO_CASE, 4, "wrtd", ".wrtd"),
    ("%s_synthesis_speedup_4.wrtd" % DEMO_CASE, 4, "wrtd", ".wrtd"),
    ("%s_synthesis_turbulence_4.wrtd" % DEMO_CASE, 4, "wrtd", ".wrtd"),
    ("%s_synthesis_veer_4.wrtd" % DEMO_CASE, 4, "wrtd", ".wrtd"),
    ("%s_timeserie_4.dat" % DEMO_CASE, 4, "timeserie", ".dat"),
    ("%s_timeseriepark_4.dat" % DEMO_CASE, 4, "timeserie", ".dat"),
)


def _fake_vault(tmpdir, sub_files):
    """在临时目录造 `{sub: [names]}` 的 vault（只建目录与空文件，零内容）。"""
    for sub, names in sub_files.items():
        path = os.path.join(tmpdir, sub)
        os.makedirs(path, exist_ok=True)
        for name in names:
            open(os.path.join(path, name), "wb").close()
    return tmpdir


class ResolveIdentityTests(unittest.TestCase):
    def test_guid_passthrough(self):
        ident = mci.resolve_case_identity(DEMO_CASE)
        self.assertEqual(ident.case_prefix, DEMO_CASE)
        self.assertEqual(ident.source, mci.SOURCE_GUID)
        self.assertEqual(ident.site_label, DEMO_CASE)

    def test_empty_site(self):
        ident = mci.resolve_case_identity("")
        self.assertEqual(ident.case_prefix, "")
        self.assertEqual(ident.source, mci.SOURCE_EMPTY)

    def test_mock_prefix_keeps_site(self):
        tmp = _fake_vault(tempfile.mkdtemp(), {"SYNTHESIS_FILES": ["DEMO_synthesis_1.wri"]})
        ident = mci.resolve_case_identity("DEMO", vault_root=tmp)
        self.assertEqual(ident.case_prefix, "DEMO")
        self.assertEqual(ident.source, mci.SOURCE_MOCK_PREFIX)

    def test_injected_db_wins_over_heuristic(self):
        tmp = _fake_vault(tempfile.mkdtemp(),
                          {"SYNTHESIS_FILES": ["%s_synthesis_2.wri" % OTHER_CASE]})
        ident = mci.resolve_case_identity(
            "DEMO", vault_root=tmp, query_case_id=lambda _s: DEMO_CASE)
        self.assertEqual(ident.case_prefix, DEMO_CASE)
        self.assertEqual(ident.source, mci.SOURCE_DB)

    def test_query_raises_falls_back_to_vault_heuristic(self):
        tmp = _fake_vault(tempfile.mkdtemp(), {"SYNTHESIS_FILES": DEMO_FILES_NAMES()})

        def boom(_site):
            raise RuntimeError("LocalDB 不可用")

        ident = mci.resolve_case_identity("DEMO", vault_root=tmp, query_case_id=boom)
        self.assertEqual(ident.case_prefix, DEMO_CASE)
        self.assertEqual(ident.source, mci.SOURCE_VAULT_SINGLE_GUID)

    def test_dual_guid_wrm_does_not_pollute_case_candidates(self):
        """双 GUID 地图只取首段：ElementGUID 不得被当成第二个 Case（否则永远 unresolved）。"""
        tmp = _fake_vault(tempfile.mkdtemp(),
                          {"SYNTHESIS_FILES": [name for name, *_ in DEMO_FILES]})
        ident = mci.resolve_case_identity("DEMO", vault_root=tmp)
        self.assertEqual(ident.case_prefix, DEMO_CASE)
        self.assertEqual(ident.candidates, (DEMO_CASE,))

    def test_two_case_guids_stay_unresolved(self):
        """多站/多 Case 共存是 Unknown（§8 U-8）：不猜，保留原串并交出候选集。"""
        tmp = _fake_vault(tempfile.mkdtemp(), {"SYNTHESIS_FILES": [
            "%s_synthesis_2.wri" % DEMO_CASE,
            "%s_synthesis_2.wri" % OTHER_CASE,
        ]})
        ident = mci.resolve_case_identity("DEMO", vault_root=tmp)
        self.assertEqual(ident.case_prefix, "DEMO")
        self.assertEqual(ident.source, mci.SOURCE_UNRESOLVED)
        self.assertEqual(set(ident.candidates), {DEMO_CASE, OTHER_CASE})

    def test_vault_missing_is_not_an_error(self):
        ident = mci.resolve_case_identity("DEMO", vault_root=r"X:\no_such_vault")
        self.assertEqual(ident.source, mci.SOURCE_UNRESOLVED)
        self.assertEqual(ident.case_prefix, "DEMO")


def DEMO_FILES_NAMES():
    return [name for name, *_ in DEMO_FILES]


class DemoInventoryU11Tests(unittest.TestCase):
    """U-11：真实 DEMO 17 个文件名必须全部被新族识别器认出来。"""

    def test_all_17_names_parse(self):
        for name, n, kind, ext in DEMO_FILES:
            parsed = mvn.parse_synthesis_filename(name)
            self.assertIsNotNone(parsed, name)
            self.assertEqual(parsed, (DEMO_CASE, n, kind, ext), name)

    def test_all_17_names_are_in_vault_family(self):
        for name, n, _kind, _ext in DEMO_FILES:
            self.assertTrue(
                mvn.is_vault_synthesis_product(name, DEMO_CASE, n), name)

    def test_family_is_scoped_to_case_prefix_and_n(self):
        name = "%s_synthesis_3.wri" % DEMO_CASE
        self.assertFalse(mvn.is_vault_synthesis_product(name, OTHER_CASE, 3))
        self.assertFalse(mvn.is_vault_synthesis_product(name, DEMO_CASE, 2))

    def test_mandatory_are_only_the_two_kernel_files(self):
        mand = mvn.synthesis_vault_mandatory_products(DEMO_CASE, 3)
        self.assertEqual(mand, ("%s_synthesis_3.wri" % DEMO_CASE,
                                "%s_synthesis_3.wrp" % DEMO_CASE))
        present = {name for name, n, _k, _e in DEMO_FILES if n == 3}
        self.assertTrue(set(mand).issubset(present))

    def test_n1_would_never_complete_for_real_demo(self):
        """U-10 关联事实：真实 DEMO 没有综合 1 产物，故 n=1 必出两件套必然缺。

        门层把 `expected_ns` 定成 `[1..N]` 会永远等 1；本用例只钉住"缺"这个事实，
        不改期望集合策略（§5.5 已把它列为发送前落账要求）。
        """
        names = {name for name, *_ in DEMO_FILES}
        self.assertNotIn("%s_synthesis_1.wri" % DEMO_CASE, names)
        self.assertNotIn("%s_synthesis_1.wrp" % DEMO_CASE, names)

    def test_legacy_products_are_not_vault_family(self):
        """真实 Vault 零命中：旧口径 xml/txt/matrix 不得再被当"已起算"。"""
        for name in ("%s_synthesis_2.xml" % DEMO_CASE,
                     "%s_synthesis_2.txt" % DEMO_CASE,
                     "%s_matrix_2.txt" % DEMO_CASE):
            self.assertIsNotNone(mvn.parse_synthesis_filename(name), name)  # 旧解析仍兼容
            self.assertFalse(mvn.is_vault_synthesis_product(name, DEMO_CASE, 2), name)


if __name__ == "__main__":
    unittest.main()
