"""Tests for Phase 5B/5C OCI application image export."""
from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from artifact.bundle import create_bundle
from artifact.oci import (
    ARTIFACT_IMAGE_SCHEMA_VERSION,
    OCI_EXPORT_PLAN_SCHEMA_VERSION,
    OCI_EXPORT_RESULT_SCHEMA_VERSION,
    create_oci_export_plan,
    OCIExportError,
    export_oci_image,
    prepare_oci_build_context,
)
from core.manifest import Manifest

APP = {
    "schemaVersion": "winforge.app/v0",
    "name": "oci-demo-app",
    "version": "1.2.3",
    "runtime": {"provider": "wine", "version": "latest"},
    "dependencies": [],
    "install": [],
    "filesystem": [],
    "launch": {
        "entrypoint": "C:/Program Files/OciDemo/demo.exe",
        "args": ["--safe"],
        "env": {"DEMO_MODE": "1"},
        "workingDirectory": "C:/Program Files/OciDemo",
    },
    "state": {"defaultPersistence": "persistent"},
    "exports": [{"name": "reports", "path": "C:/Reports"}],
    "provenance": {"sources": []},
}


def _bundle(tmp: str | Path) -> Path:
    return create_bundle(Manifest.from_dict(APP), Path(tmp), dry_run=True)


class OCIExportPlanTests(unittest.TestCase):
    def test_create_oci_export_plan_uses_verified_bundle_and_resolved_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            plan = create_oci_export_plan(
                bundle,
                tag="ghcr.io/myos-dev/winforge-app-oci-demo-app:1.2.3",
            )

        self.assertEqual(plan["schemaVersion"], OCI_EXPORT_PLAN_SCHEMA_VERSION)
        self.assertEqual(plan["imageType"], "runnable-application-image")
        self.assertEqual(plan["tag"], "ghcr.io/myos-dev/winforge-app-oci-demo-app:1.2.3")
        self.assertEqual(plan["baseImage"], "ghcr.io/myos-dev/winforge-wine:11.0")
        self.assertEqual(plan["application"], {"name": "oci-demo-app", "version": "1.2.3"})
        self.assertEqual(plan["runtime"]["provider"], "wine")
        self.assertEqual(plan["runtime"]["requestedVersion"], "latest")
        self.assertEqual(plan["runtime"]["resolvedVersion"], "11.0")
        self.assertEqual(plan["runtime"]["runner"], "winehq-stable")
        self.assertEqual(plan["runtime"]["launcher"], "wine")
        self.assertEqual(plan["layout"]["bundle"], "/opt/winforge/bundle")
        self.assertEqual(plan["layout"]["state"], "/var/lib/winforge/state")
        self.assertEqual(plan["layout"]["exports"], "/exports")
        self.assertEqual(plan["layout"]["entrypoint"], "/usr/local/bin/winforge-app-launch")
        self.assertEqual(plan["artifactMetadata"]["schemaVersion"], ARTIFACT_IMAGE_SCHEMA_VERSION)
        self.assertEqual(plan["artifactMetadata"]["runtime"]["resolvedVersion"], "11.0")
        self.assertEqual(plan["artifactMetadata"]["runtime"]["baseImage"], "ghcr.io/myos-dev/winforge-wine:11.0")
        self.assertEqual(plan["labels"]["io.winforge.schema"], ARTIFACT_IMAGE_SCHEMA_VERSION)
        self.assertEqual(plan["labels"]["io.winforge.runtime.resolvedVersion"], "11.0")
        self.assertEqual(plan["containerfile"]["path"], "Containerfile")
        self.assertIn("FROM ghcr.io/myos-dev/winforge-wine:11.0", plan["containerfile"]["content"])
        self.assertIn("COPY bundle /opt/winforge/bundle", plan["containerfile"]["content"])
        self.assertIn('ENTRYPOINT ["/usr/local/bin/winforge-app-launch"]', plan["containerfile"]["content"])

    def test_prepare_oci_build_context_writes_metadata_containerfile_and_launcher(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            plan = create_oci_export_plan(bundle, tag="oci-demo-app:1.2.3")
            context = prepare_oci_build_context(bundle, plan, Path(tmp) / "oci-context")

            artifact = json.loads(
                (context / "bundle/metadata/artifact.json").read_text(encoding="utf-8")
            )
            launcher = context / "winforge-app-launch"
            containerfile = context / "Containerfile"

            self.assertEqual(artifact["schemaVersion"], ARTIFACT_IMAGE_SCHEMA_VERSION)
            self.assertEqual(artifact["application"]["name"], "oci-demo-app")
            self.assertEqual(artifact["runtime"]["requestedVersion"], "latest")
            self.assertEqual(artifact["runtime"]["resolvedVersion"], "11.0")
            self.assertTrue(containerfile.exists())
            self.assertIn('LABEL io.winforge.schema="winforge.artifact-image/v0"', containerfile.read_text(encoding="utf-8"))
            self.assertTrue(launcher.exists())
            self.assertTrue(launcher.stat().st_mode & stat.S_IXUSR)
            launcher_text = launcher.read_text(encoding="utf-8")
            self.assertIn("/var/lib/winforge/state", launcher_text)
            self.assertIn("/exports", launcher_text)
            self.assertIn("wine", launcher_text)
            self.assertIn("umu-run", launcher_text)

    def test_prepare_oci_build_context_rejects_existing_non_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            plan = create_oci_export_plan(bundle, tag="oci-demo-app:1.2.3")
            context = Path(tmp) / "existing-context"
            context.mkdir()
            (context / "do-not-delete.txt").write_text("keep me", encoding="utf-8")

            with self.assertRaises(OCIExportError):
                prepare_oci_build_context(bundle, plan, context)

            self.assertEqual((context / "do-not-delete.txt").read_text(encoding="utf-8"), "keep me")

    def test_cli_export_oci_dry_run_returns_plan(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "export",
                    "oci",
                    str(bundle),
                    "--tag",
                    "oci-demo-app:1.2.3",
                    "--dry-run",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["schemaVersion"], OCI_EXPORT_PLAN_SCHEMA_VERSION)
        self.assertEqual(payload["tag"], "oci-demo-app:1.2.3")
        self.assertEqual(payload["baseImage"], "ghcr.io/myos-dev/winforge-wine:11.0")
        self.assertEqual(payload["runtime"]["resolvedVersion"], "11.0")


class OCIExportBuildTests(unittest.TestCase):
    def test_export_real_build_missing_engine_returns_structured_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            result = export_oci_image(
                bundle,
                tag="oci-demo-app:1.2.3",
                engine="definitely-not-a-container-engine",
                context_dir=Path(tmp) / "context",
            )

        self.assertEqual(result["schemaVersion"], OCI_EXPORT_RESULT_SCHEMA_VERSION)
        self.assertFalse(result["success"])
        self.assertEqual(result["engine"], "definitely-not-a-container-engine")
        self.assertIn("container build engine not found", result["error"])
        self.assertEqual(result["plan"]["runtime"]["resolvedVersion"], "11.0")

    def test_export_real_build_constructs_container_build_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            completed = subprocess.CompletedProcess(
                args=["docker"], returncode=0, stdout="built", stderr=""
            )
            with patch("artifact.oci.shutil.which", return_value="/usr/bin/docker"), \
                 patch("artifact.oci.subprocess.run", return_value=completed) as run:
                result = export_oci_image(
                    bundle,
                    tag="oci-demo-app:1.2.3",
                    engine="docker",
                    context_dir=Path(tmp) / "context",
                    timeout=123,
                )

        self.assertTrue(result["success"])
        self.assertEqual(result["schemaVersion"], OCI_EXPORT_RESULT_SCHEMA_VERSION)
        self.assertEqual(result["exitCode"], 0)
        self.assertEqual(result["stdout"], "built")
        command = result["command"]
        self.assertEqual(command[0:2], ["docker", "build"])
        self.assertIn("-f", command)
        self.assertIn("Containerfile", command)
        self.assertIn("-t", command)
        self.assertIn("oci-demo-app:1.2.3", command)
        self.assertTrue(command[-1].endswith("context"))
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["timeout"], 123)

    def test_cli_export_oci_real_build_failure_returns_json_and_nonzero(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _bundle(tmp)
            proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "export",
                    "oci",
                    str(bundle),
                    "--tag",
                    "oci-demo-app:1.2.3",
                    "--engine",
                    "definitely-not-a-container-engine",
                    "--context-dir",
                    str(Path(tmp) / "context"),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["schemaVersion"], OCI_EXPORT_RESULT_SCHEMA_VERSION)
        self.assertFalse(payload["success"])
        self.assertIn("container build engine not found", payload["error"])


if __name__ == "__main__":
    unittest.main()
