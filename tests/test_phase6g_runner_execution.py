"""Tests for Phase 6G cached runner execution wiring."""
from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from artifact.bundle import create_bundle
from builder.executor import execute_inside_container
from builder.pipeline import generate_build_script
from core.manifest import Manifest
from runtime.launcher import RunError, build_run_plan


RUNNER_MANIFEST = {
    "schemaVersion": "winforge.app/v0",
    "name": "runner-mounted-app",
    "version": "1.0.0",
    "runtime": {"provider": "wine", "version": "9.0", "runner": "pol-4.3"},
    "launch": {"entrypoint": "C:/Program Files/App/App.exe"},
    "provenance": {"sources": []},
}


class RunnerExecutionBuildTests(unittest.TestCase):
    def test_build_script_uses_cached_runner_when_runner_bin_env_is_present(self):
        script = generate_build_script(Manifest.from_dict(RUNNER_MANIFEST))

        self.assertIn("WINFORGE_RUNNER_BIN", script)
        self.assertIn('export PATH="$WINFORGE_RUNNER_BIN:$PATH"', script)
        self.assertIn('export WINE="$WINFORGE_RUNNER_BIN/wine"', script)
        self.assertIn('Using cached Wine runner', script)

    def test_execute_inside_container_mounts_cached_runner_for_real_build(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            runner_dir = tmp / "cache" / "pol-4.3"
            (runner_dir / "bin").mkdir(parents=True)
            (runner_dir / "bin" / "wine").write_text("#!/bin/sh\n", encoding="utf-8")
            manifest = Manifest.from_dict(RUNNER_MANIFEST)
            bundle = create_bundle(manifest, tmp / "dist", dry_run=False)

            class Completed:
                returncode = 0
                stdout = "container ok"
                stderr = ""

            ensure_result = {
                "schemaVersion": "winforge.runner-cache/v0",
                "status": "present",
                "cacheDir": str(tmp / "cache"),
                "runnerDir": str(runner_dir),
                "winePath": str(runner_dir / "bin" / "wine"),
                "runner": {"id": "pol-4.3"},
                "diagnostic": {"status": "missing-elf-interpreter"},
            }
            with patch("builder.executor.ensure_runner", return_value=ensure_result) as ensure_runner:
                with patch("builder.executor._run_container_command", return_value=Completed()) as run, patch("sys.stderr", io.StringIO()):
                    result = execute_inside_container(
                        manifest,
                        bundle,
                        engine="podman",
                        image_ref="local/runtime:test",
                        timeout=5,
                        workspace=tmp,
                        runner_cache_dir=tmp / "cache",
                    )
                    script = (bundle / "build" / "run.sh").read_text(encoding="utf-8")

        self.assertTrue(result.success)
        ensure_runner.assert_called_once_with("pol-4.3", cache_dir=tmp / "cache")
        argv = run.call_args.args[0]
        self.assertIn(f"{runner_dir.resolve()}:/opt/winforge-runner:ro,z", argv)
        self.assertIn("WINFORGE_RUNNER_BIN=/opt/winforge-runner/bin", argv)
        self.assertIn("WINFORGE_RUNNER_ID=pol-4.3", argv)
        self.assertIn('export PATH="$WINFORGE_RUNNER_BIN:$PATH"', script)
        self.assertEqual(result.runner_cache["status"], "present")
        self.assertEqual(result.runner_cache["containerDir"], "/opt/winforge-runner")


class RunnerExecutionRunPlanTests(unittest.TestCase):
    def test_run_plan_mounts_cached_runner_and_exports_runner_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            runner_dir = tmp / "cache" / "pol-4.3"
            (runner_dir / "bin").mkdir(parents=True)
            (runner_dir / "bin" / "wine").write_text("#!/bin/sh\n", encoding="utf-8")
            bundle = create_bundle(Manifest.from_dict(RUNNER_MANIFEST), tmp / "dist", dry_run=True)

            plan = build_run_plan(
                bundle,
                graphics="headless",
                engine="podman",
                runner_cache_dir=tmp / "cache",
            )

        self.assertEqual(plan["runnerCache"]["status"], "present")
        self.assertEqual(plan["runnerCache"]["runnerId"], "pol-4.3")
        self.assertEqual(plan["runnerCache"]["containerDir"], "/opt/winforge-runner")
        self.assertIn(f"{runner_dir.resolve()}:/opt/winforge-runner:ro,z", plan["container"]["argv"])
        self.assertEqual(plan["container"]["environment"]["WINFORGE_RUNNER_BIN"], "/opt/winforge-runner/bin")
        self.assertEqual(plan["container"]["environment"]["WINFORGE_RUNNER_ID"], "pol-4.3")
        self.assertIn('export PATH="$WINFORGE_RUNNER_BIN:$PATH"', plan["container"]["script"])

    def test_podman_run_plan_mounts_include_selinux_shared_relabel_option(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            runner_dir = tmp / "cache" / "pol-4.3"
            (runner_dir / "bin").mkdir(parents=True)
            (runner_dir / "bin" / "wine").write_text("#!/bin/sh\n", encoding="utf-8")
            bundle = create_bundle(Manifest.from_dict(RUNNER_MANIFEST), tmp / "dist", dry_run=True)

            plan = build_run_plan(
                bundle,
                graphics="headless",
                engine="podman",
                runner_cache_dir=tmp / "cache",
            )

        self.assertEqual(plan["container"]["bundleMount"], f"{bundle.resolve()}:/opt/winforge/bundle:ro,z")
        self.assertIn(f"{bundle.resolve()}:/opt/winforge/bundle:ro,z", plan["container"]["argv"])
        self.assertIn(f"{runner_dir.resolve()}:/opt/winforge-runner:ro,z", plan["container"]["argv"])

    def test_docker_run_plan_mounts_do_not_include_podman_selinux_option(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            runner_dir = tmp / "cache" / "pol-4.3"
            (runner_dir / "bin").mkdir(parents=True)
            (runner_dir / "bin" / "wine").write_text("#!/bin/sh\n", encoding="utf-8")
            bundle = create_bundle(Manifest.from_dict(RUNNER_MANIFEST), tmp / "dist", dry_run=True)

            plan = build_run_plan(
                bundle,
                graphics="headless",
                engine="docker",
                runner_cache_dir=tmp / "cache",
            )

        self.assertEqual(plan["container"]["bundleMount"], f"{bundle.resolve()}:/opt/winforge/bundle:ro")
        self.assertIn(f"{runner_dir.resolve()}:/opt/winforge-runner:ro", plan["container"]["argv"])
        self.assertNotIn(f"{runner_dir.resolve()}:/opt/winforge-runner:ro,z", plan["container"]["argv"])


    def test_catalog_runner_label_is_not_treated_as_downloadable_cache(self):
        plain_manifest = dict(RUNNER_MANIFEST)
        plain_manifest["runtime"] = {"provider": "wine", "version": "latest"}
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bundle = create_bundle(Manifest.from_dict(plain_manifest), tmp / "dist", dry_run=True)
            plan = build_run_plan(
                bundle,
                graphics="headless",
                engine="podman",
                runner_cache_dir=tmp / "cache",
                require_runner=True,
            )

        self.assertIsNone(plan["runnerCache"])
        self.assertNotIn("WINFORGE_RUNNER_BIN", plan["container"]["environment"])

    def test_run_plan_reports_missing_cached_runner_and_can_require_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bundle = create_bundle(Manifest.from_dict(RUNNER_MANIFEST), tmp / "dist", dry_run=True)

            plan = build_run_plan(
                bundle,
                graphics="headless",
                engine="podman",
                runner_cache_dir=tmp / "cache",
            )
            with self.assertRaisesRegex(RunError, "cached runner is missing"):
                build_run_plan(
                    bundle,
                    graphics="headless",
                    engine="podman",
                    runner_cache_dir=tmp / "cache",
                    require_runner=True,
                )

        self.assertEqual(plan["runnerCache"]["status"], "missing")
        self.assertNotIn("WINFORGE_RUNNER_BIN", plan["container"]["environment"])


if __name__ == "__main__":
    unittest.main()
