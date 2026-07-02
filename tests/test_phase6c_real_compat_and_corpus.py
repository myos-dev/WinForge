"""Tests for Phase 6C real compatibility evidence and curated corpus."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from builder.executor import BuildResult
from compat.corpus import CORPUS_SCHEMA_VERSION, load_default_corpus
from compat.evidence import COMPAT_TEST_SCHEMA_VERSION, run_compat_test
from runtime.launcher import RUN_RESULT_SCHEMA_VERSION


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_fixture_workspace(root: Path) -> Path:
    (root / "sources").mkdir(parents=True)
    (root / "overlays/demo").mkdir(parents=True)
    installer = root / "sources/demo-installer.exe"
    config = root / "overlays/demo/config.ini"
    installer.write_bytes(b"fake windows installer\n")
    config.write_text("[demo]\nmode=test\n", encoding="utf-8")
    payload = {
        "schemaVersion": "winforge.app/v0",
        "name": "real-compat-demo",
        "version": "1.0.0",
        "runtime": {"provider": "wine", "version": "latest"},
        "sources": [
            {
                "name": "installer",
                "url": "file://sources/demo-installer.exe",
                "sha256": _sha256(installer),
            }
        ],
        "dependencies": [],
        "install": [
            {
                "kind": "exe",
                "source": "file://sources/demo-installer.exe",
                "sha256": _sha256(installer),
                "args": ["/S"],
            }
        ],
        "filesystem": [
            {
                "source": "overlays/demo/config.ini",
                "target": "C:/Program Files/Demo/config.ini",
                "sha256": _sha256(config),
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
    manifest_path = root / "real-compat-demo.winforge.json"
    manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return manifest_path


class RealCompatibilityEvidenceTests(unittest.TestCase):
    def test_build_mode_records_mocked_real_build_without_running_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = _write_fixture_workspace(root)

            def fake_build(manifest, bundle_path, *, engine, image_ref, timeout, workspace):
                self.assertEqual(Path(workspace), root.resolve())
                return BuildResult(
                    success=True,
                    bundle_path=str(bundle_path),
                    runtime_provider=manifest.runtime.provider,
                    runtime_version=manifest.runtime.version,
                    image_ref=image_ref,
                    engine=engine,
                    exit_code=0,
                    prefix_size=1234,
                    prefix_file_count=12,
                )

            with patch("compat.evidence.execute_inside_container", side_effect=fake_build) as build:
                result = run_compat_test(
                    manifest_path,
                    output_dir=root / "dist",
                    workspace=root,
                    graphics="headless",
                    engine="docker",
                    mode="build",
                    build_timeout=321,
                )

        self.assertEqual(result["schemaVersion"], COMPAT_TEST_SCHEMA_VERSION)
        self.assertEqual(result["success"], True)
        self.assertEqual(result["classification"], "build-passed")
        self.assertEqual(result["build"]["mode"], "real")
        self.assertEqual(result["build"]["execution"]["prefixFileCount"], 12)
        self.assertEqual(result["run"], {"attempted": False, "reason": "mode=build"})
        build.assert_called_once()

    def test_run_mode_records_mocked_run_failure_after_successful_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = _write_fixture_workspace(root)

            def fake_build(manifest, bundle_path, *, engine, image_ref, timeout, workspace):
                return BuildResult(
                    success=True,
                    bundle_path=str(bundle_path),
                    runtime_provider=manifest.runtime.provider,
                    runtime_version=manifest.runtime.version,
                    image_ref=image_ref,
                    engine=engine,
                    exit_code=0,
                )

            fake_run_result = {
                "schemaVersion": RUN_RESULT_SCHEMA_VERSION,
                "success": False,
                "exitCode": 42,
                "stdout": "",
                "stderr": "app failed",
            }

            with patch("compat.evidence.execute_inside_container", side_effect=fake_build), \
                 patch("compat.evidence.execute_run_plan", return_value=fake_run_result) as run:
                result = run_compat_test(
                    manifest_path,
                    output_dir=root / "dist",
                    workspace=root,
                    graphics="headless",
                    engine="podman",
                    mode="run",
                    run_timeout=44,
                )

        self.assertEqual(result["success"], False)
        self.assertEqual(result["classification"], "run-failed")
        self.assertEqual(result["run"]["attempted"], True)
        self.assertEqual(result["run"]["result"]["schemaVersion"], RUN_RESULT_SCHEMA_VERSION)
        self.assertEqual(result["run"]["result"]["exitCode"], 42)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs["timeout"], 44)

    def test_cli_accepts_mode_and_timeout_options_for_dry_run(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = _write_fixture_workspace(root)
            proc = subprocess.run(
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
                    "--mode",
                    "dry-run",
                    "--build-timeout",
                    "77",
                    "--run-timeout",
                    "22",
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["classification"], "dry-run-planned")
        self.assertEqual(payload["run"], {"attempted": False, "reason": "mode=dry-run"})


    def test_cli_compat_vnc_requires_and_accepts_bridge_network(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest_path = _write_fixture_workspace(root)
            proc = subprocess.run(
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
                    "--mode",
                    "dry-run",
                    "--graphics",
                    "vnc",
                    "--network",
                    "bridge",
                    "--engine",
                    "docker",
                ],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["classification"], "dry-run-planned")
        self.assertEqual(payload["runPlan"]["graphics"]["mode"], "vnc")
        self.assertEqual(payload["runPlan"]["runtime"]["network"], "bridge")
        self.assertEqual(payload["runPlan"]["container"]["network"], "bridge")


class CompatibilityCorpusTests(unittest.TestCase):
    def test_default_corpus_lists_seed_apps_with_tiers_and_statuses(self):
        corpus = load_default_corpus()
        self.assertEqual(corpus["schemaVersion"], CORPUS_SCHEMA_VERSION)
        slugs = {app["slug"] for app in corpus["apps"]}
        self.assertIn("notepad-plus-plus", slugs)
        self.assertIn("7zip", slugs)
        for app in corpus["apps"]:
            self.assertIn(app["tier"], [1, 2, 3, 4, 5])
            self.assertIn(app["status"], ["fixture", "candidate", "blocked"])
            self.assertIn("compatibilityFocus", app)

    def test_cli_compat_corpus_outputs_default_corpus(self):
        repo = Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, "cmd/winforge.py", "compat", "corpus"],
            cwd=repo,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["schemaVersion"], CORPUS_SCHEMA_VERSION)
        self.assertGreaterEqual(len(payload["apps"]), 5)


class PackagingTests(unittest.TestCase):
    def test_package_data_includes_default_corpus_json(self):
        pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
        text = pyproject.read_text(encoding="utf-8")
        self.assertIn('"compat.corpus" = ["*.json"]', text)


if __name__ == "__main__":
    unittest.main()
