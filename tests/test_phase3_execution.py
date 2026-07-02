"""Tests for WinForge bundle runtime execution planning."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from artifact.bundle import create_bundle
from core.manifest import Manifest
from runtime.launcher import RunError, build_run_plan


VALID = {
    "schemaVersion": "winforge.dev/v0",
    "name": "sample",
    "version": "1.0.0",
    "runtime": {"provider": "wine", "version": "9.0"},
    "dependencies": [{"kind": "winetricks", "verbs": ["corefonts"]}],
    "install": [{
        "kind": "portable",
        "source": "file://app.zip",
        "target": "C:/Program Files/App",
    }],
    "filesystem": [{
        "source": "config.ini",
        "target": "C:/Program Files/App/config.ini",
    }],
    "launch": {
        "entrypoint": "C:/Program Files/App/App.exe",
        "args": ["--profile", "default"],
        "env": {"APP_ENV": "test"},
        "workingDirectory": "C:/Program Files/App",
    },
    "provenance": {"sources": []},
}


class Phase3ExecutionPlanTests(unittest.TestCase):

    def _bundle(self, tmp: str) -> Path:
        return create_bundle(Manifest.from_dict(VALID), Path(tmp), dry_run=True)

    def test_build_run_plan_uses_verified_graph_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(tmp)
            plan = build_run_plan(bundle, graphics="headless", engine="podman")

        self.assertEqual(plan["schemaVersion"], "winforge.run-plan/v0")
        self.assertEqual(plan["graphics"]["mode"], "headless")
        self.assertEqual(plan["runtime"]["provider"], "wine")
        self.assertEqual(plan["runtime"]["version"], "9.0")
        self.assertEqual(plan["runtime"]["image"], "ghcr.io/myos-dev/winforge-wine:9.0")
        self.assertEqual(plan["launch"]["entrypoint"], "C:/Program Files/App/App.exe")
        self.assertEqual(plan["container"]["engine"], "podman")
        self.assertIn("/opt/winforge/bundle/metadata/graph.json", plan["container"]["environment"]["WINFORGE_GRAPH"])
        self.assertIn("wine", plan["launchCommand"])
        self.assertIn("--profile", plan["launchCommand"])
        self.assertEqual(plan["verification"]["valid"], True)

    def test_build_run_plan_rejects_invalid_bundle_before_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(tmp)
            (bundle / "metadata" / "graph.json").unlink()
            with self.assertRaises(RunError) as cm:
                build_run_plan(bundle, graphics="headless", engine="podman")

        self.assertIn("missing required file: metadata/graph.json", str(cm.exception))

    def test_build_run_plan_rejects_invalid_graphics_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(tmp)
            with self.assertRaises(RunError) as cm:
                build_run_plan(bundle, graphics="wayland", engine="docker")

        self.assertIn("graphics mode 'wayland' must be one of", str(cm.exception))

    def test_build_run_plan_rejects_invalid_graphics_contract_before_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(tmp)
            graph_path = bundle / "metadata" / "graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["graphics"]["supportedModes"] = ["headless"]
            graph_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")
            with self.assertRaises(RunError) as cm:
                build_run_plan(bundle, graphics="vnc", engine="docker")

        self.assertIn("graph graphics must include defaultMode", str(cm.exception))

    def test_vnc_run_plan_publishes_loopback_vnc_and_novnc_ports(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(tmp)
            plan = build_run_plan(
                bundle,
                graphics="vnc",
                engine="docker",
                network="bridge",
                vnc_port=5901,
                novnc_port=6081,
            )

        argv = plan["container"]["argv"]
        self.assertIn("127.0.0.1:5901:5900", argv)
        self.assertIn("127.0.0.1:6081:6080", argv)
        self.assertIn("x11vnc", plan["container"]["script"])
        self.assertIn("websockify", plan["container"]["script"])

    def test_cli_run_dry_run_prints_run_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self._bundle(tmp)
            proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "run",
                    "--dry-run",
                    "--graphics",
                    "headless",
                    "--engine",
                    "podman",
                    str(bundle),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["schemaVersion"], "winforge.run-plan/v0")
        self.assertEqual(payload["graphics"]["mode"], "headless")
        self.assertEqual(payload["container"]["engine"], "podman")


    def test_umu_proton_ge_run_plan_uses_umu_launcher(self):
        data = dict(VALID)
        data["runtime"] = {"provider": "umu-proton-ge", "version": "GE-Proton9-27"}
        with tempfile.TemporaryDirectory() as tmp:
            bundle = create_bundle(Manifest.from_dict(data), Path(tmp), dry_run=True)
            plan = build_run_plan(bundle, graphics="headless", engine="podman")

        self.assertEqual(plan["runtime"]["provider"], "umu-proton-ge")
        self.assertEqual(plan["runtime"]["launcher"], "umu")
        self.assertEqual(plan["runtime"]["image"], "ghcr.io/myos-dev/winforge-umu-proton-ge:GE-Proton9-27")
        self.assertIn("umu-run", plan["launchCommand"])


    def test_umu_proton_ge_image_installs_umu_launcher(self):
        root = Path(__file__).resolve().parents[1]
        dockerfile = (root / "container/providers/umu-proton-ge/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("umu-launcher", dockerfile)
        self.assertIn("umu-run", dockerfile)
        self.assertIn("UMU_LAUNCHER_REF", dockerfile)
        self.assertIn("test -x /opt/umu/bin/umu-run", dockerfile)

    def test_runtime_container_images_include_vnc_helpers(self):
        root = Path(__file__).resolve().parents[1]
        dockerfiles = [
            "container/providers/wine/Dockerfile",
            "container/providers/wine-staging/Dockerfile",
            "container/providers/umu-proton-ge/Dockerfile",
        ]
        for rel in dockerfiles:
            with self.subTest(rel=rel):
                dockerfile = (root / rel).read_text(encoding="utf-8")
                self.assertIn("x11vnc", dockerfile)
                self.assertIn("websockify", dockerfile)


if __name__ == "__main__":
    unittest.main()
