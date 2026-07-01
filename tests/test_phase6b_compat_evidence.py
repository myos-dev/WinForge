"""Tests for Phase 6B source integrity and compatibility evidence."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from builder.pipeline import generate_build_script
from compat.evidence import COMPAT_TEST_SCHEMA_VERSION, run_compat_test
from core.manifest import Manifest
from core.sources import SOURCE_INTEGRITY_SCHEMA_VERSION, verify_manifest_sources


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture_workspace(root: Path) -> dict[str, str]:
    (root / "sources").mkdir(parents=True)
    (root / "overlays/demo").mkdir(parents=True)
    installer = root / "sources/demo-installer.exe"
    config = root / "overlays/demo/config.ini"
    installer.write_bytes(b"fake windows installer\n")
    config.write_text("[demo]\nmode=test\n", encoding="utf-8")
    return {
        "installer": _sha256(installer),
        "config": _sha256(config),
    }


def _manifest_data(hashes: dict[str, str]) -> dict[str, object]:
    return {
        "schemaVersion": "winforge.app/v0",
        "name": "compat-demo",
        "version": "1.0.0",
        "runtime": {"provider": "wine", "version": "latest"},
        "sources": [
            {
                "name": "installer",
                "url": "file://sources/demo-installer.exe",
                "sha256": hashes["installer"],
            }
        ],
        "dependencies": [],
        "install": [
            {
                "kind": "exe",
                "source": "file://sources/demo-installer.exe",
                "sha256": hashes["installer"],
                "args": ["/S"],
            }
        ],
        "filesystem": [
            {
                "source": "overlays/demo/config.ini",
                "target": "C:/Program Files/Demo/config.ini",
                "sha256": hashes["config"],
            }
        ],
        "compatibility": {
            "arch": "win64",
            "windowsVersion": "win10",
            "graphics": {"backend": "wined3d"},
            "dllPolicy": {"mscoree": "disabled"},
        },
        "launch": {
            "entrypoint": "C:/Program Files/Demo/Demo.exe",
            "workingDirectory": "C:/Program Files/Demo",
        },
        "state": {"persistence": "persistent"},
        "exports": [],
        "provenance": {"sources": []},
    }


class SourceIntegrityTests(unittest.TestCase):
    def test_source_integrity_verifies_local_sources_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_fixture_workspace(root)
            manifest = Manifest.from_dict(_manifest_data(hashes))

            result = verify_manifest_sources(manifest, workspace=root)

        self.assertEqual(result["schemaVersion"], SOURCE_INTEGRITY_SCHEMA_VERSION)
        self.assertEqual(result["valid"], True)
        self.assertEqual(result["summary"]["checked"], 3)
        self.assertEqual(result["summary"]["verified"], 3)
        locations = {item["location"]: item for item in result["items"]}
        self.assertEqual(locations["install[0].source"]["status"], "verified")
        self.assertEqual(locations["filesystem[0].source"]["sha256"], hashes["config"])

    def test_source_integrity_reports_missing_files_and_hash_mismatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_fixture_workspace(root)
            data = _manifest_data(hashes)
            data["install"] = [
                {
                    "kind": "exe",
                    "source": "file://sources/missing.exe",
                    "sha256": "0" * 64,
                }
            ]
            data["filesystem"] = [
                {
                    "source": "overlays/demo/config.ini",
                    "target": "C:/Program Files/Demo/config.ini",
                    "sha256": "1" * 64,
                }
            ]
            manifest = Manifest.from_dict(data)

            result = verify_manifest_sources(manifest, workspace=root)

        self.assertEqual(result["valid"], False)
        self.assertEqual(result["summary"]["missing"], 1)
        joined_errors = "\n".join(result["errors"])
        self.assertIn("missing local source", joined_errors)
        self.assertIn("sha256 mismatch", joined_errors)

    def test_build_script_resolves_relative_sources_against_workspace_mount(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_fixture_workspace(root)
            manifest = Manifest.from_dict(_manifest_data(hashes))

            script = generate_build_script(manifest, workspace_mount="/workspace")

        self.assertIn('/workspace/sources/demo-installer.exe', script)
        self.assertIn('/workspace/overlays/demo/config.ini', script)


class CompatibilityEvidenceTests(unittest.TestCase):
    def test_compat_test_dry_run_records_source_build_verify_and_run_plan_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_fixture_workspace(root)
            manifest_path = root / "compat-demo.winforge.json"
            manifest_path.write_text(json.dumps(_manifest_data(hashes), indent=2), encoding="utf-8")

            result = run_compat_test(
                manifest_path,
                output_dir=root / "dist",
                workspace=root,
                graphics="headless",
                engine="docker",
            )

        self.assertEqual(result["schemaVersion"], COMPAT_TEST_SCHEMA_VERSION)
        self.assertEqual(result["success"], True)
        self.assertEqual(result["classification"], "dry-run-planned")
        self.assertEqual(result["sourceIntegrity"]["valid"], True)
        self.assertEqual(result["build"]["mode"], "dry-run")
        self.assertTrue(result["build"]["bundle"].endswith("compat-demo-1.0.0"))
        self.assertEqual(result["bundleVerification"]["valid"], True)
        self.assertEqual(result["runPlan"]["schemaVersion"], "winforge.run-plan/v0")
        self.assertEqual(result["runPlan"]["container"]["environment"]["WINEDLLOVERRIDES"], "mscoree=")

    def test_cli_sources_verify_and_compat_test_emit_machine_readable_results(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hashes = _write_fixture_workspace(root)
            manifest_path = root / "compat-demo.winforge.json"
            manifest_path.write_text(json.dumps(_manifest_data(hashes), indent=2), encoding="utf-8")
            verify = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "sources",
                    "verify",
                    str(manifest_path),
                    "--workspace",
                    str(root),
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )
            compat = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "compat",
                    "test",
                    str(manifest_path),
                    "--workspace",
                    str(root),
                    "--output",
                    str(root / "dist"),
                    "--graphics",
                    "headless",
                    "--engine",
                    "podman",
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(verify.returncode, 0, verify.stderr)
        self.assertEqual(json.loads(verify.stdout)["schemaVersion"], SOURCE_INTEGRITY_SCHEMA_VERSION)
        self.assertEqual(compat.returncode, 0, compat.stderr)
        payload = json.loads(compat.stdout)
        self.assertEqual(payload["schemaVersion"], COMPAT_TEST_SCHEMA_VERSION)
        self.assertEqual(payload["runPlan"]["container"]["engine"], "podman")


class PackagingTests(unittest.TestCase):
    def test_package_discovery_includes_compat_evidence_package(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        self.assertIn('"compat*"', text)


if __name__ == "__main__":
    unittest.main()
