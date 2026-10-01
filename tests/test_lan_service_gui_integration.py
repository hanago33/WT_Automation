import os
import unittest
from unittest.mock import patch, MagicMock
import tkinter as tk

import wt_queue_selfcheck
import wt_task_queue_window


class TestLanServiceGuiIntegration(unittest.TestCase):
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

    def test_lan_ip_detection(self):
        ips = wt_queue_selfcheck.get_lan_ip_list()
        self.assertIsInstance(ips, list)
        self.assertIn("127.0.0.1", ips)
        primary = wt_queue_selfcheck.get_primary_lan_ip()
        self.assertIsInstance(primary, str)
        self.assertTrue(len(primary) > 0)

    def test_derive_monitor_url(self):
        derive = wt_task_queue_window._derive_monitor_url
        self.assertEqual(derive("http://192.168.0.102:8768"), "http://192.168.0.102:8767")
        self.assertEqual(derive("192.168.0.102:8768"), "http://192.168.0.102:8767")
        self.assertEqual(derive("http://127.0.0.1:8768"), "http://127.0.0.1:8767")
        self.assertEqual(derive("http://10.0.0.5:9000"), "http://10.0.0.5:8767")

    def test_check_lan_connection_unreachable(self):
        with patch.object(wt_queue_selfcheck, "port_open", return_value=False):
            res = wt_queue_selfcheck.check_lan_connection("192.168.254.254", token="wt2026")
            self.assertFalse(res["ok"])
            self.assertFalse(res["port_open"])
            self.assertIn("TCP 端口不通", res["error"])
            self.assertIn("防火墙", res["diagnosis"])

    def test_check_lan_connection_auth_failure(self):
        with patch.object(wt_queue_selfcheck, "port_open", return_value=True):
            with patch.object(wt_queue_selfcheck, "wait_service", return_value="UNAUTHORIZED"):
                res = wt_queue_selfcheck.check_lan_connection("192.168.0.102", token="wrong_tok")
                self.assertFalse(res["ok"])
                self.assertTrue(res["port_open"])
                self.assertFalse(res["auth_ok"])
                self.assertIn("401", res["error"])
                self.assertIn("令牌", res["diagnosis"])

    def test_check_lan_connection_success(self):
        with patch.object(wt_queue_selfcheck, "port_open", return_value=True):
            def mock_wait(host, port, token, path, seconds=15):
                if port == wt_queue_selfcheck.PORT:
                    return {"service": "wt_task_server", "status": "ok"}
                return {"service": "wt_server_monitor", "status": "idle"}

            with patch.object(wt_queue_selfcheck, "wait_service", side_effect=mock_wait):
                res = wt_queue_selfcheck.check_lan_connection("192.168.0.102", token="wt2026")
                self.assertTrue(res["ok"])
                self.assertTrue(res["port_open"])
                self.assertTrue(res["auth_ok"])
                self.assertTrue(res["monitor_ok"])

    def test_history_urls_management(self):
        if self.root is None:
            self.skipTest("No Tk display available")

        history_saved = []

        def on_hist(urls):
            history_saved.clear()
            history_saved.extend(urls)

        win = wt_task_queue_window.TaskQueueWindow(
            self.root,
            initial_url="http://192.168.0.102:8768",
            initial_user="alice",
            initial_token="wt2026",
            history_urls=["http://10.0.0.1:8768"],
            on_history_urls_change=on_hist,
        )
        try:
            self.assertIn("http://192.168.0.102:8768", win.history_urls)
            self.assertIn("http://10.0.0.1:8768", win.history_urls)

            win._record_history_url("http://192.168.0.199:8768")
            self.assertEqual(win.history_urls[0], "http://192.168.0.199:8768")
            self.assertEqual(history_saved[0], "http://192.168.0.199:8768")
        finally:
            win.window.destroy()

    def test_standalone_network_diag_dialog(self):
        if self.root is None:
            self.skipTest("No Tk display available")

        applied = []
        diag_win = wt_task_queue_window.open_network_diag_dialog(
            self.root,
            default_target="http://192.168.0.102:8768",
            default_token="wt2026",
            history_urls=["http://192.168.0.102:8768"],
            on_apply=lambda u, t: applied.append((u, t)),
        )
        try:
            self.assertIsNotNone(diag_win)
            self.assertTrue(diag_win.winfo_exists())
        finally:
            diag_win.destroy()

    def test_launcher_lan_methods(self):
        import WT_Launcher
        self.assertTrue(hasattr(WT_Launcher.LauncherApp, "start_all_lan_services"))
        self.assertTrue(hasattr(WT_Launcher.LauncherApp, "stop_all_lan_services"))
        self.assertTrue(hasattr(WT_Launcher.LauncherApp, "open_lan_connection_diag"))
        self.assertTrue(hasattr(WT_Launcher.LauncherApp, "_save_history_server_urls"))


if __name__ == "__main__":
    unittest.main()
