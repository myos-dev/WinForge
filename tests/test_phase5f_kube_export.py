"""Tests for Phase 5F Kubernetes manifest export."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from copy import deepcopy

from artifact.bundle import create_bundle
from artifact.kube import (
    KUBE_EXPORT_SCHEMA_VERSION,
    KubeExportError,
    create_kube_export_plan,
    export_kube_manifest,
)
from core.manifest import Manifest

APP = {
    "schemaVersion": "winforge.app/v0",
    "name": "Kube Demo_App",
    "version": "2.1.0",
    "runtime": {"provider": "wine", "version": "latest"},
    "dependencies": [],
    "install": [],
    "filesystem": [],
    "launch": {
        "entrypoint": "C:/Program Files/KubeDemo/demo.exe",
        "workingDirectory": "C:/Program Files/KubeDemo",
    },
    "state": {"defaultPersistence": "persistent"},
    "exports": [{"path": "C:/Program Files/KubeDemo/output", "description": "demo exports"}],
    "provenance": {"sources": []},
}

DIGEST_IMAGE = "ghcr.io/acme/winforge-app-kube-demo@sha256:abcdef1234567890"
TAG_IMAGE = "ghcr.io/acme/winforge-app-kube-demo:2.1.0"


def _bundle(tmp: str | Path, *, network: str | None = None) -> Path:
    data = deepcopy(APP)
    if network is not None:
        data["runtime"]["network"] = network
    return create_bundle(Manifest.from_dict(data), Path(tmp), dry_run=True)


class KubeExportPlanTests(unittest.TestCase):
    def test_create_kube_export_plan_requires_digest_and_generates_deployment_and_pvcs(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            plan = create_kube_export_plan(
                bundle,
                image=DIGEST_IMAGE,
                namespace="winforge-apps",
                name="custom-demo",
                state_size="5Gi",
                exports_size="1Gi",
            )

        self.assertEqual(plan["schemaVersion"], KUBE_EXPORT_SCHEMA_VERSION)
        self.assertEqual(plan["bundle"].split("/")[-1], "Kube-Demo_App-2.1.0")
        self.assertEqual(plan["image"]["ref"], DIGEST_IMAGE)
        self.assertTrue(plan["image"]["digestPinned"])
        self.assertEqual(plan["namespace"], "winforge-apps")
        self.assertEqual(plan["name"], "custom-demo")
        self.assertEqual(plan["application"], {"name": "Kube Demo_App", "version": "2.1.0"})
        kinds = [(resource["kind"], resource["metadata"]["name"]) for resource in plan["resources"]]
        self.assertEqual(kinds, [
            ("PersistentVolumeClaim", "custom-demo-state"),
            ("PersistentVolumeClaim", "custom-demo-exports"),
            ("NetworkPolicy", "custom-demo-deny-egress"),
            ("Deployment", "custom-demo"),
        ])
        self.assertEqual(plan["network"]["mode"], "none")
        policy_index = next(i for i, resource in enumerate(plan["resources"]) if resource["kind"] == "NetworkPolicy")
        deployment_index = next(i for i, resource in enumerate(plan["resources"]) if resource["kind"] == "Deployment")
        self.assertLess(policy_index, deployment_index)
        deployment = next(resource for resource in plan["resources"] if resource["kind"] == "Deployment")
        policy = next(resource for resource in plan["resources"] if resource["kind"] == "NetworkPolicy")
        self.assertFalse(deployment["spec"]["template"]["spec"]["hostNetwork"])
        self.assertEqual(policy["spec"]["policyTypes"], ["Egress"])
        self.assertEqual(policy["spec"]["egress"], [])
        yaml_text = plan["manifestYaml"]
        self.assertIn("kind: Deployment", yaml_text)
        self.assertIn(f"image: {DIGEST_IMAGE}", yaml_text)
        self.assertIn("mountPath: /var/lib/winforge/state", yaml_text)
        self.assertIn("mountPath: /exports", yaml_text)
        self.assertIn("io.winforge.app.name: kube-demo-app", yaml_text)
        self.assertIn("annotations:", yaml_text)
        self.assertIn("io.winforge.app.raw-name: Kube Demo_App", yaml_text)
        self.assertIn("io.winforge.schema: winforge.artifact-image/v0", yaml_text)
        self.assertIn("storage: 5Gi", yaml_text)
        self.assertIn("storage: 1Gi", yaml_text)
        self.assertIn("hostNetwork: false", yaml_text)
        self.assertIn("kind: NetworkPolicy", yaml_text)
        self.assertIn("policyTypes:", yaml_text)
        self.assertIn("egress: []", yaml_text)


    def test_create_kube_export_plan_uses_host_network_when_bundle_requests_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp, network="host")
            plan = create_kube_export_plan(bundle, image=DIGEST_IMAGE)

        self.assertEqual(plan["network"]["mode"], "host")
        kinds = [resource["kind"] for resource in plan["resources"]]
        self.assertEqual(kinds, ["PersistentVolumeClaim", "PersistentVolumeClaim", "Deployment"])
        deployment = next(resource for resource in plan["resources"] if resource["kind"] == "Deployment")
        self.assertTrue(deployment["spec"]["template"]["spec"]["hostNetwork"])
        self.assertNotIn("NetworkPolicy", plan["manifestYaml"])
        self.assertIn("hostNetwork: true", plan["manifestYaml"])


    def test_create_kube_export_plan_uses_normal_pod_network_for_bridge_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp, network="bridge")
            plan = create_kube_export_plan(bundle, image=DIGEST_IMAGE)

        self.assertEqual(plan["network"], {"mode": "bridge", "hostNetwork": False, "denyEgress": False})
        kinds = [resource["kind"] for resource in plan["resources"]]
        self.assertEqual(kinds, ["PersistentVolumeClaim", "PersistentVolumeClaim", "Deployment"])
        deployment = next(resource for resource in plan["resources"] if resource["kind"] == "Deployment")
        self.assertFalse(deployment["spec"]["template"]["spec"]["hostNetwork"])
        self.assertNotIn("NetworkPolicy", plan["manifestYaml"])

    def test_create_kube_export_plan_rejects_invalid_network_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            graph_path = bundle / "metadata" / "graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["runnerRuntime"]["network"] = "internet"
            graph_path.write_text(json.dumps(graph), encoding="utf-8")

            with self.assertRaisesRegex(KubeExportError, "runnerRuntime.network"):
                create_kube_export_plan(bundle, image=DIGEST_IMAGE)

    def test_create_kube_export_plan_rejects_mutable_tag_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            with self.assertRaises(KubeExportError) as ctx:
                create_kube_export_plan(bundle, image=TAG_IMAGE)
        self.assertIn("digest-pinned", str(ctx.exception))

    def test_create_kube_export_plan_allows_mutable_tag_only_when_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            plan = create_kube_export_plan(
                bundle,
                image=TAG_IMAGE,
                allow_mutable_tag=True,
                no_pvc=True,
            )

        self.assertFalse(plan["image"]["digestPinned"])
        self.assertTrue(plan["image"]["mutableTagAllowed"])
        kinds = [resource["kind"] for resource in plan["resources"]]
        self.assertEqual(kinds, ["NetworkPolicy", "Deployment"])
        self.assertIn("emptyDir: {}", plan["manifestYaml"])
        self.assertNotIn("PersistentVolumeClaim", plan["manifestYaml"])

    def test_export_kube_manifest_writes_yaml_and_returns_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            output = Path(tmp) / "k8s" / "demo.yaml"
            result = export_kube_manifest(bundle, image=DIGEST_IMAGE, output_path=output)
            written = output.read_text(encoding="utf-8")

        self.assertEqual(result["schemaVersion"], KUBE_EXPORT_SCHEMA_VERSION)
        self.assertEqual(result["output"], str(output))
        self.assertIn("kind: Deployment", written)
        self.assertIn(f"image: {DIGEST_IMAGE}", written)


class KubeExportCLITests(unittest.TestCase):
    def test_cli_export_kube_dry_run_resolves_app_name_from_artifact_index(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "dist"
            build = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "build",
                    "examples/notepad-plus-plus.winforge.yaml",
                    "--dry-run",
                    "--output",
                    str(output_dir),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            export = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "export",
                    "kube",
                    "notepad-plus-plus",
                    "--artifact-index",
                    str(output_dir / ".winforge" / "artifacts.json"),
                    "--image",
                    "ghcr.io/acme/notepad@sha256:abc123",
                    "--namespace",
                    "winforge-apps",
                    "--dry-run",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(export.returncode, 0, export.stderr)
        payload = json.loads(export.stdout)
        self.assertEqual(payload["schemaVersion"], KUBE_EXPORT_SCHEMA_VERSION)
        self.assertEqual(payload["application"]["name"], "notepad-plus-plus")
        self.assertIn("kind: Deployment", payload["manifestYaml"])
        self.assertIn("ghcr.io/acme/notepad@sha256:abc123", payload["manifestYaml"])

    def test_cli_export_kube_output_writes_yaml_and_rejects_mutable_tag(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            bad = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "export",
                    "kube",
                    str(bundle),
                    "--image",
                    TAG_IMAGE,
                    "--dry-run",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn("digest-pinned", bad.stderr)

            output = Path(tmp) / "demo.yaml"
            good = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "export",
                    "kube",
                    str(bundle),
                    "--image",
                    DIGEST_IMAGE,
                    "--output",
                    str(output),
                    "--no-pvc",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            written = output.read_text(encoding="utf-8")

        self.assertEqual(good.returncode, 0, good.stderr)
        payload = json.loads(good.stdout)
        self.assertEqual(payload["output"], str(output))
        self.assertIn("kind: Deployment", written)
        self.assertIn("emptyDir: {}", written)


if __name__ == "__main__":
    unittest.main()
