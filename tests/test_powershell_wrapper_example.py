from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from core.manifest import load_manifest


class PowershellWrapperExampleTests(unittest.TestCase):
    def test_powershell_wrapper_example_loads_builds_and_plans_vnc_launch(self):
        repo = Path(__file__).resolve().parents[1]
        recipe = repo / "examples" / "powershell-wrapper-pwsh-vnc.winforge.yaml"
        manifest = load_manifest(recipe)

        self.assertEqual(manifest.name, "powershell-wrapper-pwsh-vnc")
        self.assertEqual([step.kind for step in manifest.install], ["script"])
        self.assertNotIn("winetricks -q powershell", manifest.install[0].command or "")
        self.assertEqual([entry.id for entry in manifest.entrypoints], ["pwsh", "powershell-wrapper"])

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dist"
            build_proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "build",
                    str(recipe),
                    "--dry-run",
                    "--output",
                    str(out),
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            )
            build_payload = json.loads(build_proc.stdout)
            bundle = Path(build_payload["bundle"])

            verify_proc = subprocess.run(
                [sys.executable, "cmd/winforge.py", "bundle", "verify", str(bundle)],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            )
            verify_payload = json.loads(verify_proc.stdout)
            self.assertTrue(verify_payload["valid"], verify_payload.get("errors"))

            run_proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "run",
                    str(bundle),
                    "--dry-run",
                    "--graphics",
                    "vnc",
                    "--engine",
                    "docker",
                    "--network",
                    "bridge",
                    "--vnc-port",
                    "5901",
                    "--novnc-port",
                    "6081",
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=True,
            )
            run_plan = json.loads(run_proc.stdout)

        self.assertEqual(run_plan["schemaVersion"], "winforge.run-plan/v0")
        self.assertEqual(run_plan["graphics"]["mode"], "vnc")
        self.assertEqual(run_plan["runtime"]["network"], "bridge")
        self.assertEqual(run_plan["selectedEntrypoint"]["id"], "default")
        self.assertEqual(
            run_plan["launchCommand"],
            ["wine", "C:/Program Files/PowerShell/7/pwsh.exe", "-NoLogo", "-NoExit"],
        )
