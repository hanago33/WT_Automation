import os
import sys
import unittest
from unittest.mock import patch


TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator


class RelativeRegionWindowResolutionTests(unittest.TestCase):
    def _make_wrapper(self, title, class_name, framework_id, process_id, rect):
        return {
            "title": title,
            "className": class_name,
            "frameworkId": framework_id,
            "processId": process_id,
            "rect": rect,
        }

    def test_keep_explicit_titled_window_as_anchor(self):
        original_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "27920",
            {"left": 115, "top": 25, "right": 2444, "bottom": 1490, "width": 2329, "height": 1465},
        )
        focused_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "27920",
            {"left": 128, "top": 76, "right": 2432, "bottom": 1440, "width": 2304, "height": 1364},
        )
        parent_window = {"title": "导入时间序列文件", "className": "Window", "frameworkId": "WPF"}

        with patch.object(wt_flow_locator, "get_wrapper_text", side_effect=lambda wrapper: wrapper["title"]), patch.object(
            wt_flow_locator, "get_wrapper_class_name", side_effect=lambda wrapper: wrapper["className"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_framework_id", side_effect=lambda wrapper: wrapper["frameworkId"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_process_id", side_effect=lambda wrapper: wrapper["processId"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_rectangle", side_effect=lambda wrapper: wrapper["rect"]
        ), patch.object(
            wt_flow_locator, "get_foreground_window_handle", return_value=1
        ), patch.object(
            wt_flow_locator, "_try_get_window_by_handle", return_value=focused_window
        ):
            resolved_window = wt_flow_locator.resolve_effective_relative_region_window(
                original_window,
                parent_window,
            )

        self.assertIs(resolved_window, original_window)

    def test_upgrade_generic_window_to_focused_titled_window(self):
        original_window = self._make_wrapper(
            "",
            "Window",
            "WPF",
            "27920",
            {"left": 0, "top": 0, "right": 2560, "bottom": 1516, "width": 2560, "height": 1516},
        )
        focused_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "27920",
            {"left": 115, "top": 25, "right": 2444, "bottom": 1490, "width": 2329, "height": 1465},
        )
        parent_window = {"title": "导入时间序列文件", "className": "Window", "frameworkId": "WPF"}

        with patch.object(wt_flow_locator, "get_wrapper_text", side_effect=lambda wrapper: wrapper["title"]), patch.object(
            wt_flow_locator, "get_wrapper_class_name", side_effect=lambda wrapper: wrapper["className"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_framework_id", side_effect=lambda wrapper: wrapper["frameworkId"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_process_id", side_effect=lambda wrapper: wrapper["processId"]
        ), patch.object(
            wt_flow_locator, "get_wrapper_rectangle", side_effect=lambda wrapper: wrapper["rect"]
        ), patch.object(
            wt_flow_locator, "get_foreground_window_handle", return_value=1
        ), patch.object(
            wt_flow_locator, "_try_get_window_by_handle", return_value=focused_window
        ):
            resolved_window = wt_flow_locator.resolve_effective_relative_region_window(
                original_window,
                parent_window,
            )

        self.assertIs(resolved_window, focused_window)

    def test_replace_same_score_with_larger_explicit_wpf_window(self):
        smaller_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "36800",
            {"left": 128, "top": 76, "right": 2432, "bottom": 1440, "width": 2304, "height": 1364},
        )
        larger_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "36800",
            {"left": 115, "top": 25, "right": 2444, "bottom": 1490, "width": 2329, "height": 1465},
        )
        window_spec = {"title": "导入时间序列文件", "className": "Window", "frameworkId": "WPF"}

        with patch.object(wt_flow_locator, "get_wrapper_text", side_effect=lambda wrapper: wrapper["title"]), patch.object(
            wt_flow_locator, "get_wrapper_handle_text", side_effect=lambda wrapper: "0x1" if wrapper is smaller_window else "0x2"
        ), patch.object(
            wt_flow_locator, "get_wrapper_rectangle", side_effect=lambda wrapper: wrapper["rect"]
        ):
            should_replace = wt_flow_locator.should_replace_flow_window_candidate(
                larger_window,
                39,
                smaller_window,
                39,
                window_spec,
            )

        self.assertTrue(should_replace)

    def test_keep_same_score_when_larger_window_does_not_contain_current(self):
        current_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "36800",
            {"left": 128, "top": 76, "right": 2432, "bottom": 1440, "width": 2304, "height": 1364},
        )
        shifted_window = self._make_wrapper(
            "导入时间序列文件",
            "Window",
            "WPF",
            "36800",
            {"left": 60, "top": 60, "right": 2500, "bottom": 1400, "width": 2440, "height": 1340},
        )
        window_spec = {"title": "导入时间序列文件", "className": "Window", "frameworkId": "WPF"}

        with patch.object(wt_flow_locator, "get_wrapper_text", side_effect=lambda wrapper: wrapper["title"]), patch.object(
            wt_flow_locator, "get_wrapper_handle_text", side_effect=lambda wrapper: "0x2" if wrapper is shifted_window else "0x1"
        ), patch.object(
            wt_flow_locator, "get_wrapper_rectangle", side_effect=lambda wrapper: wrapper["rect"]
        ):
            should_replace = wt_flow_locator.should_replace_flow_window_candidate(
                shifted_window,
                39,
                current_window,
                39,
                window_spec,
            )

        self.assertFalse(should_replace)


if __name__ == "__main__":
    unittest.main()
