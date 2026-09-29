# encoding: utf-8
"""Tests for deploy_release.py."""

import os
import sys
import tempfile
import unittest
import zipfile
import tkinter as tk
from unittest.mock import patch

import deploy_release as dr
from _tk_support import shared_tk_root


class TestDeployReleaseLogic(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.target = os.path.join(self.temp_dir.name, "target")
        self.release_dir = os.path.join(self.temp_dir.name, "releases")
        os.makedirs(self.target, exist_ok=True)
        os.makedirs(self.release_dir, exist_ok=True)

        # Create sample existing target file
        with open(os.path.join(self.target, "sample.txt"), "w", encoding="utf-8") as f:
            f.write("old content")

        # Create sample release zip
        self.zip_path = os.path.join(self.release_dir, "发布包_v1.0.0.zip")
        with zipfile.ZipFile(self.zip_path, "w") as z:
            z.writestr("sample.txt", "new content in zip")
            z.writestr("new_file.txt", "brand new file")
            z.writestr("deploy_release.py", "tool itself to skip")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_list_and_plan_apply(self):
        zips = dr.list_release_zips(self.release_dir)
        self.assertEqual(len(zips), 1)
        self.assertEqual(zips[0]["name"], "发布包_v1.0.0.zip")

        # Test dry-run
        n_pkgs, total_files, bak = dr.plan_apply(zips, self.target, mode="all", dry_run=True)
        self.assertEqual(n_pkgs, 1)
        self.assertEqual(total_files, 2)  # sample.txt and new_file.txt (skip deploy_release.py)
        self.assertIsNone(bak)

        # Test real apply
        n_pkgs, total_files, bak = dr.plan_apply(zips, self.target, mode="all", dry_run=False, backup=True)
        self.assertEqual(n_pkgs, 1)
        self.assertEqual(total_files, 2)
        self.assertIsNotNone(bak)
        self.assertTrue(os.path.isfile(os.path.join(bak, "sample.txt")))

        with open(os.path.join(self.target, "sample.txt"), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "new content in zip")


class TestDeployReleaseAppHeadless(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 共享 Tk 根窗口（见 tests/_tk_support.py）：进程内只建一次、绝不 destroy，
        # 「创建→销毁→再创建」循环会让后续 Tk() 稳定失败（多文件子集运行必踩）
        cls.tk, cls.root = shared_tk_root()

    def setUp(self):
        self.app = dr.DeployApp(self.root)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_app_widgets_and_preview(self):
        self.assertIsNotNone(self.app.var_root)
        self.assertIsNotNone(self.app.var_release)
        self.assertIsNotNone(self.app.listbox)
        self.assertIsNotNone(self.app.log)

        target_dir = os.path.join(self.temp_dir.name, "target")
        rel_dir = os.path.join(self.temp_dir.name, "rel")
        os.makedirs(target_dir, exist_ok=True)
        os.makedirs(rel_dir, exist_ok=True)

        zip_p = os.path.join(rel_dir, "发布包_v2.0.0.zip")
        with zipfile.ZipFile(zip_p, "w") as z:
            z.writestr("test.txt", "content")

        self.app.var_root.set(target_dir)
        self.app.var_release.set(rel_dir)
        self.app._refresh_list()

        self.assertGreater(self.app.listbox.size(), 0)

        # Test preview
        self.app._preview()
        log_content = self.app.log.get("1.0", "end")
        self.assertIn("预览", log_content)


if __name__ == "__main__":
    unittest.main()
