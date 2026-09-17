# encoding: utf-8
"""控件库「删除选中控件」下标解析的回归测试。

背景（P0③ 的两次修复）：
  1. 列表视图的 Treeview iid 是「排序 + 筛选后的 0-based 显示位置」，而
     `_get_filtered_controls()` 三个分支都会排序、`var_sort` 默认就是「质量优先」，
     所以显示位置与原始下标通常不同 —— 直接用显示位置当原始下标会删错控件。
  2. `flatControls` 与 `controlDefinitions` 是**按同一原始下标平行的数组**
     （`_build_controls_from_payload` 按 index 配对构造；`edit_selected_control`
     也用同一个 source_index 回写）。因此两处删除必须共用同一组下标，
     只修其中一处会让两数组错位，后续控件元数据全部错配并写回文件。

本文件的核心断言就是这个不变量：删除后两个数组仍然平行、且删掉的是同一个控件。

被测对象是 `ControlMapImportDialog` 的真实方法（通过轻量宿主绑定），
不实例化对话框，无需 Tk 交互环境。
"""
import os
import sys
import unittest
from types import MethodType
from unittest.mock import patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import WT_Flow_Editor as E

#: 需要绑定到宿主上的实例方法
BOUND_METHODS = (
    "delete_selected_controls",
    "_get_selected_indexes",
    "_get_filtered_controls",
    "_build_controls_from_payload",
    "_merge_control_map_control_metadata",
    "_matches_time_filter",
    "_control_map_timestamp",
)

#: 静态方法需按原样绑定（不能再包 MethodType，否则会多传一个 self）
STATIC_METHODS = ("_resolve_control_source_indexes",)

#: 原始数组顺序（下标 0..3）：A / B / C / D
RAW_CONTROLS = (
    ("A", "待验证", 10),
    ("B", "推荐保留", 90),
    ("C", "待验证", 50),
    ("D", "推荐保留", 80),
)

#: 「质量优先」排序后的显示顺序（应与其原始下标不同，才能暴露错位）
EXPECTED_DISPLAY_ORDER = ["B", "D", "C", "A"]


class FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class FakeTree:
    def __init__(self, selected_iids=()):
        self._selected = tuple(selected_iids)

    def selection(self):
        return self._selected


class FakeListbox:
    def curselection(self):
        return (0,)


def make_payload():
    flat = [
        {
            "displayName": name, "name": name, "windowTitle": "W",
            "qualityTier": tier, "qualityReason": "", "locatorScore": score,
            "targetMethod": "automation_id", "targetValue": "id" + name,
        }
        for name, tier, score in RAW_CONTROLS
    ]
    defs = [
        {
            "id": "ctl_" + name, "name": name, "windowTitle": "W",
            "targetMethod": "automation_id", "targetValue": "id" + name,
        }
        for name, _tier, _score in RAW_CONTROLS
    ]
    return {
        "targetWindow": {"title": "W"},
        "scanMeta": {"scanTime": "2026-01-01T10:00:00", "totalControls": len(flat)},
        "flatControls": flat,
        "controlDefinitions": defs,
    }


class Harness:
    """仅提供被绑方法用到的属性。"""

    def __init__(self, payload, selected_iids=("0",)):
        self.current_payload = payload
        self._controls_cache_key = None
        self._controls_cache_value = []
        self.default_window_title = ""
        self.var_filter = FakeVar("")
        self.var_sort = FakeVar("质量优先")          # 默认值，排序生效
        self.var_time_filter = FakeVar("全部时间")
        self.var_file_scope = FakeVar("single")
        self.control_tree = FakeTree(selected_iids)
        self.file_listbox = FakeListbox()
        self.control_map_files = [{"path": "dummy_control_map.json"}]
        self.var_status = FakeVar("")
        self.window = object()
        self._refresh_file_list = lambda: None
        self.marked_keys = None

        def _mark(deleted_keys):
            self.marked_keys = set(deleted_keys)
            return 0

        self._mark_flow_controls_source_deleted = _mark

        for name in BOUND_METHODS:
            setattr(self, name, MethodType(getattr(E.ControlMapImportDialog, name), self))
        for name in STATIC_METHODS:
            setattr(self, name, getattr(E.ControlMapImportDialog, name))


def make_harness(payload=None, selected_iids=("0",)):
    return Harness(payload if payload is not None else make_payload(), selected_iids)


def names_of(container):
    return [str(item.get("name", "")) for item in container]


class ResolveSourceIndexTests(unittest.TestCase):
    """`_resolve_control_source_indexes` 纯逻辑。"""

    def resolve(self, selected, filtered):
        return E.ControlMapImportDialog._resolve_control_source_indexes(selected, filtered)

    def test_display_position_maps_to_source_index(self):
        filtered = [{"_sourceIndex": 1}, {"_sourceIndex": 3}, {"_sourceIndex": 2}, {"_sourceIndex": 0}]
        self.assertEqual(self.resolve([0], filtered), [1])
        self.assertEqual(self.resolve([0, 1], filtered), [1, 3])
        self.assertEqual(self.resolve([3], filtered), [0])

    def test_identity_when_not_sorted(self):
        filtered = [{"_sourceIndex": i} for i in range(4)]
        self.assertEqual(self.resolve([0, 1, 2, 3], filtered), [0, 1, 2, 3])

    def test_missing_source_index_falls_back_to_display_position(self):
        filtered = [{"_sourceIndex": 2}, {"name": "无标注"}]
        self.assertEqual(self.resolve([1], filtered), [1])

    def test_out_of_range_is_skipped(self):
        filtered = [{"_sourceIndex": 0}]
        self.assertEqual(self.resolve([5], filtered), [])
        self.assertEqual(self.resolve([-1], filtered), [])

    def test_non_numeric_and_none_are_skipped(self):
        filtered = [{"_sourceIndex": 0}]
        self.assertEqual(self.resolve(["hierarchy:3"], filtered), [])
        self.assertEqual(self.resolve([None], filtered), [])

    def test_duplicates_collapse(self):
        filtered = [{"_sourceIndex": 2}, {"_sourceIndex": 2}]
        self.assertEqual(self.resolve([0, 1], filtered), [2])

    def test_empty_inputs(self):
        self.assertEqual(self.resolve([], []), [])
        self.assertEqual(self.resolve(None, None), [])


class DeleteSelectedControlsTests(unittest.TestCase):
    """删除路径必须让两个平行数组共用同一组真实下标。"""

    def setUp(self):
        self._patchers = [
            patch.object(E.messagebox, "askyesno", return_value=True),
            patch.object(E.messagebox, "showinfo"),
            patch.object(E.messagebox, "showerror"),
            patch.object(E, "save_json_file"),
        ]
        self.mocks = [p.start() for p in self._patchers]
        self.addCleanup(lambda: [p.stop() for p in self._patchers])
        self.askyesno = self.mocks[0]

    def test_sorted_view_deletes_the_selected_control(self):
        """显示位置 0 在排序后指向原始下标 1（控件 B），删掉的必须是 B。"""
        harness = make_harness(selected_iids=("0",))
        # 先确认排序确实改变了顺序，否则这个用例没有意义
        self.assertEqual(names_of(harness._get_filtered_controls()),
                         EXPECTED_DISPLAY_ORDER)

        harness.delete_selected_controls()
        payload = harness.current_payload

        self.assertEqual(names_of(payload["flatControls"]), ["A", "C", "D"])
        self.assertEqual([d["id"] for d in payload["controlDefinitions"]],
                         ["ctl_A", "ctl_C", "ctl_D"])

    def test_two_arrays_stay_parallel(self):
        """核心不变量：删除后 flatControls 与 controlDefinitions 仍一一对应。"""
        harness = make_harness(selected_iids=("1",))   # 显示位置 1 → 原始下标 3（D）
        harness.delete_selected_controls()
        payload = harness.current_payload

        flat = payload["flatControls"]
        defs = payload["controlDefinitions"]
        self.assertEqual(len(flat), len(defs))
        self.assertEqual([f["displayName"] for f in flat], [d["name"] for d in defs])
        self.assertEqual(names_of(flat), ["A", "B", "C"])

    def test_multi_selection_keeps_parallel(self):
        harness = make_harness(selected_iids=("0", "2"))  # 原始下标 1(B) 与 2(C)
        harness.delete_selected_controls()
        payload = harness.current_payload

        self.assertEqual(names_of(payload["flatControls"]), ["A", "D"])
        self.assertEqual([d["name"] for d in payload["controlDefinitions"]], ["A", "D"])

    def test_confirm_dialog_names_match_deleted_controls(self):
        """确认框里显示的名字必须与实际被删的控件一致。"""
        harness = make_harness(selected_iids=("0",))
        harness.delete_selected_controls()
        text = self.askyesno.call_args[0][1]
        self.assertIn("B", text)
        self.assertNotIn("控件A", text)

    def test_flow_source_marking_uses_deleted_control_keys(self):
        """deleted_keys 必须来自被删的控件（用于流程来源联动标记）。"""
        harness = make_harness(selected_iids=("0",))
        harness.delete_selected_controls()
        self.assertIn("ctl_B", harness.marked_keys)
        self.assertNotIn("ctl_A", harness.marked_keys)

    def test_cancel_keeps_payload_intact(self):
        self.mocks[0].return_value = False
        harness = make_harness(selected_iids=("0",))
        harness.delete_selected_controls()
        self.assertEqual(names_of(harness.current_payload["flatControls"]),
                         ["A", "B", "C", "D"])
        self.assertEqual(len(harness.current_payload["controlDefinitions"]), 4)

    def test_derived_scope_is_rejected(self):
        harness = make_harness(selected_iids=("0",))
        harness.var_file_scope.set("catalog")
        harness.delete_selected_controls()
        self.assertEqual(len(harness.current_payload["flatControls"]), 4)
        self.assertFalse(self.askyesno.called)

    def test_unresolvable_selection_aborts_without_deleting(self):
        harness = make_harness(selected_iids=("99",))
        harness.delete_selected_controls()
        self.assertEqual(len(harness.current_payload["flatControls"]), 4)
        self.assertEqual(len(harness.current_payload["controlDefinitions"]), 4)

    def test_no_definitions_falls_back_to_flat_only(self):
        payload = make_payload()
        del payload["controlDefinitions"]
        harness = make_harness(payload, selected_iids=("0",))
        harness.delete_selected_controls()
        self.assertEqual(names_of(harness.current_payload["flatControls"]),
                         ["A", "C", "D"])


if __name__ == "__main__":
    unittest.main()
