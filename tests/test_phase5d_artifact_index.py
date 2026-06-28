"""Tests for Phase 5D local artifact index and app-name resolution."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from artifact.bundle import create_bundle
from artifact.index import (
    ARTIFACT_INDEX_SCHEMA_VERSION,
    default_index_path,
    list_artifacts,
    register_bundle,
    resolve_artifact,
)
from core.manifest import Manifest

APP = {
    "schemaVersion": "winforge.app/v0",
    "name": "indexed-demo",
    "version": "1.2.3",
    "runtime": {"provider": "wine", "version": "latest"},
    "dependencies": [],
    "install": [],
    "filesystem": [],
    "launch": {
        "entrypoint": "C:/Program Files/IndexedDemo/demo.exe",
        "workingDirectory": "C:/Program Files/IndexedDemo",
    },
    "state": {"defaultPersistence": "persistent"},
    "exports": [],
    "provenance": {"sources": []},
}


def _make_bundle(tmp: str | Path) -> Path:
    return create_bundle(Manifest.from_dict(APP), Path(tmp), dry_run=True)


class ArtifactIndexModuleTests(unittest.TestCase):
    def test_register_bundle_writes_index_entry_with_latest_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _make_bundle(tmp)
            index_path = Path(tmp) / ".winforge" / "artifacts.json"

            entry = register_bundle(bundle, index_path=index_path)
            index = json.loads(index_path.read_text(encoding="utf-8"))

        self.assertEqual(entry["application"], {"name": "indexed-demo", "version": "1.2.3"})
        self.assertEqual(index["schemaVersion"], ARTIFACT_INDEX_SCHEMA_VERSION)
        self.assertEqual(index["latest"]["indexed-demo"], "1.2.3")
        self.assertEqual(index["artifacts"]["indexed-demo"]["1.2.3"]["bundle"], str(bundle))
        self.assertEqual(index["artifacts"]["indexed-demo"]["1.2.3"]["runtime"]["resolvedVersion"], "11.0")
        self.assertEqual(index["artifacts"]["indexed-demo"]["1.2.3"]["verification"]["valid"], True)

    def test_resolve_artifact_supports_latest_and_specific_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _make_bundle(tmp)
            index_path = register_bundle(bundle, index_path=Path(tmp) / ".winforge" / "artifacts.json")["indexPath"]

            latest = resolve_artifact("indexed-demo", index_path=index_path)
            versioned = resolve_artifact("indexed-demo@1.2.3", index_path=index_path)

        self.assertEqual(latest["bundle"], str(bundle))
        self.assertEqual(versioned["bundle"], str(bundle))
        self.assertEqual(versioned["application"]["version"], "1.2.3")

    def test_default_index_path_lives_under_output_directory(self):
        self.assertEqual(
            default_index_path(Path("dist")),
            Path("dist/.winforge/artifacts.json"),
        )


class ArtifactIndexCLITests(unittest.TestCase):
    def test_build_registers_bundle_and_artifacts_list_outputs_index(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dist"
            build = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "build",
                    "examples/notepad-plus-plus.winforge.yaml",
                    "--dry-run",
                    "--output",
                    str(output),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            build_payload = json.loads(build.stdout)
            index_path = output / ".winforge" / "artifacts.json"
            self.assertEqual(build_payload["artifactIndex"], str(index_path))
            self.assertTrue(index_path.exists())

            listed = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "artifacts",
                    "list",
                    "--index",
                    str(index_path),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(listed.returncode, 0, listed.stderr)
        payload = json.loads(listed.stdout)
        self.assertEqual(payload["schemaVersion"], ARTIFACT_INDEX_SCHEMA_VERSION)
        self.assertIn("notepad-plus-plus", payload["artifacts"])

    def test_cli_resolve_run_and_export_accept_app_name_from_index(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dist"
            build = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "build",
                    "examples/notepad-plus-plus.winforge.yaml",
                    "--dry-run",
                    "--output",
                    str(output),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            index_path = output / ".winforge" / "artifacts.json"

            resolved = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "artifacts",
                    "resolve",
                    "notepad-plus-plus",
                    "--index",
                    str(index_path),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            run = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "run",
                    "notepad-plus-plus",
                    "--artifact-index",
                    str(index_path),
                    "--dry-run",
                    "--graphics",
                    "headless",
                    "--engine",
                    "docker",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )
            export = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "export",
                    "oci",
                    "notepad-plus-plus",
                    "--artifact-index",
                    str(index_path),
                    "--tag",
                    "local/notepad:8.6.0",
                    "--dry-run",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(export.returncode, 0, export.stderr)
        self.assertTrue(json.loads(resolved.stdout)["bundle"].endswith("notepad-plus-plus-8.6.0"))
        self.assertEqual(json.loads(run.stdout)["schemaVersion"], "winforge.run-plan/v0")
        self.assertEqual(json.loads(export.stdout)["schemaVersion"], "winforge.oci-export-plan/v0")


if __name__ == "__main__":
    unittest.main()
