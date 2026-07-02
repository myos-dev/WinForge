"""Tests for WinForge runtime network isolation and escape hatches."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from artifact.bundle import create_bundle
from artifact.graph import build_execution_graph
from artifact.inspection import verify_bundle
from artifact.kube import KubeExportError, create_kube_export_plan
from core.manifest import Manifest, ManifestError
from runtime.launcher import RunError, build_run_plan


APP = {
    "schemaVersion": "winforge.app/v0",
    "name": "network-demo",
    "version": "1.0.0",
    "runtime": {"provider": "wine", "version": "latest"},
    "dependencies": [],
    "install": [],
    "filesystem": [],
    "launch": {"entrypoint": "C:/Program Files/NetworkDemo/demo.exe"},
    "provenance": {"sources": []},
}


def _manifest_with_network(network: str | None = None) -> Manifest:
    data = copy.deepcopy(APP)
    if network is not None:
        data["runtime"]["network"] = network
    return Manifest.from_dict(data)


def _bundle(tmp: str | Path, network: str | None = None) -> Path:
    return create_bundle(_manifest_with_network(network), Path(tmp), dry_run=True)


def _tamper_graph_network(bundle: Path, network: str) -> None:
    graph_path = bundle / "metadata" / "graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    graph["runnerRuntime"]["network"] = network
    graph_path.write_text(json.dumps(graph, indent=2), encoding="utf-8")


class RuntimeNetworkManifestTests(unittest.TestCase):
    def test_runtime_network_defaults_to_none_and_serializes(self):
        manifest = _manifest_with_network()

        self.assertEqual(manifest.runtime.network, "none")
        self.assertEqual(manifest.to_dict()["runtime"]["network"], "none")

    def test_runtime_network_accepts_explicit_escape_hatch_values(self):
        for mode in ["none", "bridge", "host"]:
            with self.subTest(mode=mode):
                manifest = _manifest_with_network(mode)
                self.assertEqual(manifest.runtime.network, mode)
                self.assertEqual(manifest.to_dict()["runtime"]["network"], mode)

    def test_runtime_network_rejects_unknown_modes(self):
        data = copy.deepcopy(APP)
        data["runtime"]["network"] = "internet"

        with self.assertRaisesRegex(ManifestError, "runtime.network"):
            Manifest.from_dict(data)


class RuntimeNetworkGraphTests(unittest.TestCase):
    def test_graph_records_runtime_network_intent_only_for_runner_runtime(self):
        graph = build_execution_graph(_manifest_with_network("host"))

        self.assertNotIn("network", graph["builderRuntime"])
        self.assertEqual(graph["runnerRuntime"]["network"], "host")

    def test_graph_records_default_runtime_network_none(self):
        graph = build_execution_graph(_manifest_with_network())

        self.assertEqual(graph["runnerRuntime"]["network"], "none")


class RuntimeNetworkRunPlanTests(unittest.TestCase):
    def test_run_plan_air_gaps_runtime_container_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_run_plan(_bundle(tmp), graphics="headless", engine="docker")

        self.assertEqual(plan["runtime"]["network"], "none")
        self.assertEqual(plan["container"]["network"], "none")
        self.assertIn("--net", plan["container"]["argv"])
        net_index = plan["container"]["argv"].index("--net")
        self.assertEqual(plan["container"]["argv"][net_index + 1], "none")

    def test_run_plan_can_override_manifest_network_for_operator_escape_hatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_run_plan(
                _bundle(tmp, network="host"),
                graphics="headless",
                engine="docker",
                network="bridge",
            )

        self.assertEqual(plan["runtime"]["network"], "bridge")
        self.assertEqual(plan["container"]["network"], "bridge")
        net_index = plan["container"]["argv"].index("--net")
        self.assertEqual(plan["container"]["argv"][net_index + 1], "bridge")

    def test_run_plan_rejects_invalid_network_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            with self.assertRaisesRegex(RunError, "network mode"):
                build_run_plan(bundle, graphics="headless", engine="docker", network="internet")


    def test_vnc_requires_bridge_network_for_loopback_port_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            with self.assertRaisesRegex(RunError, "graphics vnc requires network bridge"):
                build_run_plan(bundle, graphics="vnc", engine="docker")

    def test_vnc_rejects_host_network_to_avoid_exposing_unauthenticated_listeners(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            with self.assertRaisesRegex(RunError, "graphics vnc requires network bridge"):
                build_run_plan(bundle, graphics="vnc", engine="docker", network="host")

    def test_vnc_with_bridge_network_keeps_loopback_port_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_run_plan(
                _bundle(tmp),
                graphics="vnc",
                engine="docker",
                network="bridge",
                vnc_port=5901,
                novnc_port=6081,
            )

        self.assertEqual(plan["runtime"]["network"], "bridge")
        argv = plan["container"]["argv"]
        net_index = argv.index("--net")
        self.assertEqual(argv[net_index + 1], "bridge")
        self.assertIn("127.0.0.1:5901:5900", argv)
        self.assertIn("127.0.0.1:6081:6080", argv)


    def test_vnc_uses_manifest_bridge_network_without_cli_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_run_plan(_bundle(tmp, network="bridge"), graphics="vnc", engine="docker")

        self.assertEqual(plan["runtime"]["network"], "bridge")
        argv = plan["container"]["argv"]
        net_index = argv.index("--net")
        self.assertEqual(argv[net_index + 1], "bridge")
        self.assertIn("127.0.0.1:5900:5900", argv)
        self.assertIn("127.0.0.1:6080:6080", argv)

    def test_bundle_verification_rejects_manifest_graph_network_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp, network="none")
            _tamper_graph_network(bundle, "host")
            result = verify_bundle(bundle)

        self.assertFalse(result["valid"])
        self.assertTrue(any(check["id"] == "runtime-network-match" and not check["ok"] for check in result["checks"]))
        self.assertIn("manifest runtime.network", "; ".join(result["errors"]))


    def test_bundle_verification_rejects_empty_manifest_network_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp, network="none")
            manifest_path = bundle / "manifest.winforge.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["runtime"]["network"] = ""
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            result = verify_bundle(bundle)

        self.assertFalse(result["valid"])
        self.assertTrue(any(check["id"] == "runtime-network-match" and not check["ok"] for check in result["checks"]))

    def test_bundle_verification_rejects_empty_graph_network_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp, network="none")
            _tamper_graph_network(bundle, "")
            result = verify_bundle(bundle)

        self.assertFalse(result["valid"])
        self.assertTrue(any(check["id"] == "runtime-network-match" and not check["ok"] for check in result["checks"]))

    def test_run_plan_rejects_network_mismatch_before_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp, network="none")
            _tamper_graph_network(bundle, "host")
            with self.assertRaisesRegex(RunError, "runtime.network"):
                build_run_plan(bundle, graphics="headless", engine="docker")

    def test_kube_export_rejects_network_mismatch_before_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp, network="none")
            _tamper_graph_network(bundle, "host")
            with self.assertRaisesRegex(KubeExportError, "runtime.network"):
                create_kube_export_plan(bundle, image="ghcr.io/acme/network-demo@sha256:abc123")

    def test_cli_run_dry_run_accepts_network_override(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "run",
                    "--dry-run",
                    "--graphics",
                    "headless",
                    "--engine",
                    "docker",
                    "--network",
                    "host",
                    str(bundle),
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["runtime"]["network"], "host")
        self.assertEqual(payload["container"]["network"], "host")
