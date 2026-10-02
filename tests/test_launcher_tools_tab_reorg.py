# -*- coding: utf-8 -*-
"""Unit tests for WT_Launcher Tab 2 tools reorganization, LAN service control card,
step selection inversion, and failure selection habit improvements.
"""
import unittest
from unittest.mock import patch, MagicMock
import tkinter as tk

import WT_Launcher
import wt_theme
import wt_queue_selfcheck
import text_tools


class TestLauncherToolsTabReorg(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
            cls.root.withdraw()
        except tk.TclError:
            cls.root = None

    @classmethod
    def tearDownClass(cls):
        if cls.root is not None:
            try:
                cls.root.destroy()
            except Exception:
                pass

    def test_text_data_studio_exports(self):
        """Ensure text_tools exports both App and aliases TextDataStudioApp/TextToolsApp."""
        self.assertTrue(hasattr(text_tools, "TextDataStudioApp"))
        self.assertTrue(hasattr(text_tools, "TextToolsApp"))
        self.assertIs(text_tools.TextDataStudioApp, text_tools.App)

    def test_launcher_methods_exist(self):
        """Verify launcher has the new consolidated methods."""
        app_cls = WT_Launcher.LauncherApp
        self.assertTrue(callable(getattr(app_cls, "open_text_data_tools", None)))
        self.assertTrue(callable(getattr(app_cls, "_build_lan_service_control_card", None)))
        self.assertTrue(callable(getattr(app_cls, "_refresh_lan_service_status", None)))
        self.assertTrue(callable(getattr(app_cls, "_invert_step_checks", None)))
        self.assertTrue(callable(getattr(app_cls, "_select_failed_step_checks", None)))
        self.assertTrue(callable(getattr(app_cls, "_update_step_selection_count", None)))

    def test_step_selection_invert_and_count(self):
        """Test inverting step checkboxes and updating selection count."""
        if self.root is None:
            self.skipTest("No Tk display available")

        app = object.__new__(WT_Launcher.LauncherApp)
        app.root = self.root
        app.step_check_vars = {
            "step_01": tk.BooleanVar(value=True),
            "step_02": tk.BooleanVar(value=False),
            "step_03": tk.BooleanVar(value=False),
        }
        app.step_selection_count_var = tk.StringVar(value="")

        # Initial count
        app._update_step_selection_count()
        self.assertEqual(app.step_selection_count_var.get(), "已选 1/3 步")

        # Invert checks
        app._invert_step_checks()
        self.assertFalse(app.step_check_vars["step_01"].get())
        self.assertTrue(app.step_check_vars["step_02"].get())
        self.assertTrue(app.step_check_vars["step_03"].get())
        self.assertEqual(app.step_selection_count_var.get(), "已选 2/3 步")

    def test_step_selection_failed_only(self):
        """Test selecting only failed steps based on last run report."""
        if self.root is None:
            self.skipTest("No Tk display available")

        app = object.__new__(WT_Launcher.LauncherApp)
        app.root = self.root
        app.step_check_vars = {
            "step_01": tk.BooleanVar(value=True),
            "step_02": tk.BooleanVar(value=True),
            "step_03": tk.BooleanVar(value=True),
        }
        app.step_selection_count_var = tk.StringVar(value="")
        app._append_log = MagicMock()

        # Mock last run report with step_02 failed
        mock_report = {
            "stepResults": [
                {"stepId": "step_01", "status": "success"},
                {"stepId": "step_02", "status": "failed"},
                {"stepId": "step_03", "status": "passed"},
            ]
        }
        app._load_last_run_report = MagicMock(return_value=(mock_report, "mock_path.json"))

        app._select_failed_step_checks()
        self.assertFalse(app.step_check_vars["step_01"].get())
        self.assertTrue(app.step_check_vars["step_02"].get())
        self.assertFalse(app.step_check_vars["step_03"].get())
        self.assertEqual(app.step_selection_count_var.get(), "已选 1/3 步")

    def test_lan_service_control_card_ui(self):
        """Test building the LAN service control card and refreshing status."""
        if self.root is None:
            self.skipTest("No Tk display available")

        app = object.__new__(WT_Launcher.LauncherApp)
        app.root = self.root
        app.theme = wt_theme.get_palette()

        frame = tk.Frame(self.root)
        try:
            with patch.object(wt_queue_selfcheck, "port_open", return_value=True):
                card = app._build_lan_service_control_card(frame)
                self.assertIsNotNone(card)
                self.assertTrue(hasattr(app, "lan_status_badge_queue"))
                self.assertTrue(hasattr(app, "lan_status_badge_monitor"))

                # Test probing synchronously with ports open
                app._refresh_lan_service_status(async_probe=False)
                self.assertIn("运行中", app.lan_status_badge_queue.cget("text"))
                self.assertIn("运行中", app.lan_status_badge_monitor.cget("text"))

            # Test probing synchronously with ports closed
            with patch.object(wt_queue_selfcheck, "port_open", return_value=False):
                app._refresh_lan_service_status(async_probe=False)
                self.assertIn("未启动", app.lan_status_badge_queue.cget("text"))
                self.assertIn("未启动", app.lan_status_badge_monitor.cget("text"))
        finally:
            frame.destroy()


if __name__ == "__main__":
    unittest.main()
