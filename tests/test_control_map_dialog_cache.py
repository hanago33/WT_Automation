# encoding: utf-8
"""控件库导入对话框 P0 性能修复的回归测试。

覆盖两个修复点：
  1. 控件列表构建缓存：缓存键改为 payload 对象身份（is 比较），
     原地回写 payload 的编辑/删除路径必须手动置 _controls_cache_key = None 失效。
  2. control_map_timestamp 模块级 lru_cache 时间戳解析：
     与修复前的实例方法实现逐值等价（含各异常分支）。

被测对象是 ControlMapImportDialog 的真实方法（通过轻量宿主绑定），
不实例化对话框，无需 Tk 交互环境。
"""
import inspect
import os
import sys
import unittest
from datetime import datetime
from types import MethodType

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Flow_Editor as E

BOUND_METHODS = (
    "_build_controls_from_payload",
    "_merge_control_map_control_metadata",
    "_get_filtered_controls",
    "_matches_time_filter",
    "_control_map_timestamp",
)


class FakeVar:
    """tkinter 变量的 get/set 替身。"""

    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class DialogHarness:
    """绑定真实方法的轻量宿主，仅提供被绑方法用到的属性。"""

    def __init__(self, payload):
        self.current_payload = payload
        self._controls_cache_key = None
        self._controls_cache_value = []
        self.default_window_title = ""
        self.var_filter = FakeVar("")
        self.var_sort = FakeVar("添加时间-新到旧")
        self.var_time_filter = FakeVar("全部时间")


def make_harness(payload):
    harness = DialogHarness(payload)
    for name in BOUND_METHODS:
        setattr(harness, name, MethodType(E.ControlMapImportDialog.__dict__[name], harness))
    return harness


def make_payload(with_definitions=True):
    flat = [
        {
            "displayName": "控件A", "name": "控件A", "windowTitle": "窗口A",
            "qualityTier": "推荐保留", "qualityReason": "", "locatorScore": 90,
            "targetMethod": "automation_id", "targetValue": "idA",
        },
        {
            "displayName": "控件B", "name": "控件B", "windowTitle": "窗口A",
            "qualityTier": "待验证", "qualityReason": "", "locatorScore": 40,
            "targetMethod": "name", "targetValue": "控件B",
        },
    ]
    payload = {
        "targetWindow": {"title": "窗口A"},
        "scanMeta": {"scanTime": "2026-01-01T10:00:00", "totalControls": 2},
        "flatControls": flat,
    }
    if with_definitions:
        payload["controlDefinitions"] = [
            {"id": "ctl_a", "name": "控件A", "windowTitle": "窗口A",
             "targetMethod": "automation_id", "targetValue": "idA"},
            {"id": "ctl_b", "name": "控件B", "windowTitle": "窗口A",
             "targetMethod": "name", "targetValue": "控件B"},
        ]
    return payload


class ControlsCacheTests(unittest.TestCase):
    """P0-1：缓存键用 payload 对象身份，命中/替换/原地回写三态。"""

    def test_second_call_returns_cached_list(self):
        harness = make_harness(make_payload())
        first = harness._build_controls_from_payload()
        second = harness._build_controls_from_payload()
        self.assertIs(second, first)
        self.assertEqual(len(first), 2)

    def test_payload_replacement_rebuilds(self):
        harness = make_harness(make_payload())
        first = harness._build_controls_from_payload()
        harness.current_payload = make_payload()  # 新对象，即使内容相同也要重建
        rebuilt = harness._build_controls_from_payload()
        self.assertIsNot(rebuilt, first)
        self.assertEqual(len(rebuilt), 2)

    def test_inplace_mutation_stale_until_manual_invalidation(self):
        """原地修改 payload 不会改变对象身份，缓存仍命中旧值——
        这正是编辑/删除回写点必须手动置 _controls_cache_key = None 的原因。"""
        harness = make_harness(make_payload())
        first = harness._build_controls_from_payload()
        self.assertEqual(first[0].get("_qualityTier"), "推荐保留")

        # 模拟回写：原地修改（编辑/删除保存前的同对象变更）
        harness.current_payload["flatControls"][0]["qualityTier"] = "建议优化"
        self.assertIs(harness._build_controls_from_payload(), first)
        self.assertEqual(first[0].get("_qualityTier"), "推荐保留")  # 旧缓存，符合契约

        # 回写点的失效语句生效后必须重建出新数据
        harness._controls_cache_key = None
        rebuilt = harness._build_controls_from_payload()
        self.assertIsNot(rebuilt, first)
        self.assertEqual(rebuilt[0].get("_qualityTier"), "建议优化")

    def test_flat_only_payload_builds_with_source_index(self):
        harness = make_harness(make_payload(with_definitions=False))
        controls = harness._build_controls_from_payload()
        self.assertEqual(len(controls), 2)
        self.assertEqual(controls[0].get("id"), "control_map_1")
        self.assertEqual([c.get("_sourceIndex") for c in controls], [0, 1])

    def test_definitions_payload_builds_with_source_index(self):
        harness = make_harness(make_payload())
        controls = harness._build_controls_from_payload()
        self.assertEqual([c.get("_sourceIndex") for c in controls], [0, 1])
        self.assertEqual(controls[0].get("name"), "控件A")


class WriteBackInvalidationContractTests(unittest.TestCase):
    """编辑/删除回写是原地修改 payload（对象身份不变），必须包含失效语句。
    用源码断言钉住该契约，防止后续重构悄悄丢掉。"""

    def test_edit_write_back_invalidates_cache(self):
        source = inspect.getsource(E.ControlMapImportDialog.edit_selected_control)
        self.assertIn('payload["controlDefinitions"] = control_defs', source)
        self.assertIn("self._controls_cache_key = None", source)

    def test_delete_write_back_invalidates_cache(self):
        source = inspect.getsource(E.ControlMapImportDialog.delete_selected_controls)
        self.assertIn("self._controls_cache_key = None", source)


def _legacy_timestamp(text):
    """修复前的实现（含 str 前处理），用于逐值等价校验。"""
    text = str(text or "").strip()
    if not text:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        return 0.0


class TimestampParityTests(unittest.TestCase):
    """P0-2：模块级 lru_cache 版本与旧实例方法逐值等价（含异常分支）。"""

    CASES = (
        None, "", "   ",
        "2026-01-02T03:04:05",
        "2026-01-02 03:04:05",
        "  2026-01-02T03:04:05  ",
        "2026-01-02T03:04:05.123456",
        "2026-01-02T03:04:05+08:00",
        "垃圾数据", "2026-13-45T99:99:99",
        0, "0",
    )

    def test_matches_legacy_for_all_shapes(self):
        for value in self.CASES:
            with self.subTest(value=value):
                self.assertEqual(E.control_map_timestamp(value), _legacy_timestamp(value))

    def test_repeated_calls_agree(self):
        first = E.control_map_timestamp("2026-01-02T03:04:05")
        for _ in range(5):
            self.assertEqual(E.control_map_timestamp("2026-01-02T03:04:05"), first)
        self.assertEqual(first, datetime(2026, 1, 2, 3, 4, 5).timestamp())

    def test_instance_method_delegates_to_module_function(self):
        harness = make_harness(None)
        self.assertEqual(
            harness._control_map_timestamp("2026-01-02 03:04:05"),
            E.control_map_timestamp("2026-01-02 03:04:05"),
        )


class FilterSortTests(unittest.TestCase):
    """缓存修好后排序列表仍正确：同文件内 _addedAt 相同，按名称 tiebreak。"""

    def test_sort_new_to_old_name_tiebreak(self):
        harness = make_harness(make_payload())
        controls = harness._get_filtered_controls()
        names = [c.get("name") for c in controls]
        self.assertEqual(names, ["控件B", "控件A"])

    def test_filter_keyword_narrows(self):
        harness = make_harness(make_payload())
        harness.var_filter.set("控件a")  # haystack 小写匹配
        controls = harness._get_filtered_controls()
        self.assertEqual([c.get("name") for c in controls], ["控件A"])


if __name__ == "__main__":
    unittest.main()
