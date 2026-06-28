"""Tests for WinForge installability and console entrypoints."""
from __future__ import annotations

import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InstallPackagingTests(unittest.TestCase):

    def test_pyproject_defines_winforge_console_script(self):
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["name"], "winforge")
        self.assertEqual(data["project"]["scripts"]["winforge"], "winforge.cli:main")
        includes = set(data["tool"]["setuptools"]["packages"]["find"]["include"])
        for package in ["winforge*", "core*", "runtime*", "artifact*", "builder*", "container*"]:
            self.assertIn(package, includes)
        self.assertIn("catalog.json", data["tool"]["setuptools"]["package-data"]["runtime"])
        self.assertNotIn("License :: OSI Approved :: MIT License", data["project"].get("classifiers", []))

    def test_package_cli_module_is_importable(self):
        from winforge.cli import build_parser, main

        parser = build_parser()
        self.assertEqual(parser.prog, "winforge")
        self.assertTrue(callable(main))

    def test_python_module_entrypoint_prints_help(self):
        proc = subprocess.run(
            [sys.executable, "-m", "winforge", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage: winforge", proc.stdout)
        self.assertIn("bundle", proc.stdout)

    def test_dev_script_shim_still_prints_help(self):
        proc = subprocess.run(
            [sys.executable, "cmd/winforge.py", "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage: winforge", proc.stdout)


if __name__ == "__main__":
    unittest.main()
