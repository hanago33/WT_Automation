import os
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(TESTS_DIR)
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

import wt_flow_locator


class DropdownCandidateVerificationTests(unittest.TestCase):
    """候选点击后能读显示值时必须校验等于目标，读不到才保留点击证据。"""

    def _select(self, display_value):
        dropdown_wrapper = MagicMock()
        candidate = MagicMock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(wt_flow_locator, "_GET_STEP_DEFINITION", return_value={"id": "s1"}))
            stack.enter_context(patch.object(wt_flow_locator, "get_flow_control_definition", return_value={"id": "dd", "name": "Dropdown", "inspectData": {}}))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_target_texts", return_value=["Target"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_dropdown_runtime_expected_window_titles", return_value=["Window"]))
            stack.enter_context(patch.object(wt_flow_locator, "get_foreground_window_handle", return_value=123))
            stack.enter_context(patch.object(wt_flow_locator, "_try_get_window_by_handle", return_value=MagicMock()))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_process_id", return_value=0))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_text", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "_collect_dropdown_windows", return_value=[MagicMock()]))
            stack.enter_context(patch.object(wt_flow_locator, "find_flow_control", return_value=dropdown_wrapper))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_value", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_toggle_state", return_value="1"))
            stack.enter_context(patch.object(wt_flow_locator, "click_wrapper_center", return_value=(True, {})))
            stack.enter_context(patch.object(wt_flow_locator, "iter_dropdown_runtime_candidates", return_value=[candidate]))
            stack.enter_context(patch.object(wt_flow_locator, "_iter_dropdown_raw_view_candidates", return_value=[]))
            stack.enter_context(patch.object(wt_flow_locator, "score_dropdown_runtime_candidate", return_value=90))
            stack.enter_context(patch.object(wt_flow_locator, "click_dropdown_runtime_candidate", return_value=(True, {"method": "click_input"})))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_debug_snapshot", return_value={}))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_class_name", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_control_type", return_value=""))
            stack.enter_context(patch.object(wt_flow_locator, "get_wrapper_rectangle", return_value={}))
            stack.enter_context(patch.object(wt_flow_locator, "_read_dropdown_display_text", return_value=display_value))
            stack.enter_context(patch.object(wt_flow_locator, "normalize_match_text", side_effect=lambda value: str(value).lower()))
            stack.enter_context(patch.object(wt_flow_locator, "is_placeholder_text", return_value=False))
            stack.enter_context(patch.object(wt_flow_locator, "send_keys", side_effect=lambda *args, **kwargs: None))
            stack.enter_context(patch.object(wt_flow_locator.time, "sleep", return_value=None))
            stack.enter_context(patch.object(wt_flow_locator, "_LOG_STEP", side_effect=lambda *args, **kwargs: None))
            return wt_flow_locator.select_dropdown_item_runtime(
                "s1",
                "dd",
                timeout_seconds=0.3,
                window_title_hint="",
                target_option="Target",
            )

    def test_candidate_click_with_matching_display_value_succeeds(self):
        ok, meta = self._select("Target")
        self.assertTrue(ok)
        self.assertEqual(meta.get("valueVerified"), "Target")

    def test_candidate_click_with_mismatched_display_value_fails(self):
        ok, meta = self._select("Wrong")
        self.assertFalse(ok)
        self.assertNotIn("valueVerified", meta)


if __name__ == "__main__":
    unittest.main()