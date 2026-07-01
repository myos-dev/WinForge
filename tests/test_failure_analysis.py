"""Tests for Windows installer failure analysis reports."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from builder.executor import BuildResult
from compat.evidence import run_compat_test
from compat.failure_analysis import FAILURE_ANALYSIS_SCHEMA_VERSION, FailureAnalysisError, analyze_failure_path

PRODUCT_KEY = "ABCDE-FGHIJ-KLMNO-PQRST-UVWXY"


def _office_failure_log() -> str:
    return f"""2026-07-01 21:30:00 Executing chained package: WordMUI.en-us
2026-07-01 21:30:10 Successfully installed package: WordMUI.en-us
2026-07-01 21:31:00 Executing chained package: ProPlusWW
2026-07-01 21:31:10 MSI(INFO): Product key was {PRODUCT_KEY}
2026-07-01 21:31:11 MSI(ERROR): CustomAction RegisterProduct returned actual error code 1603
2026-07-01 21:31:12 Action ended 21:31:12: InstallFinalize. Return value 3.
2026-07-01 21:31:13 Failed to install product: C:\\MSOCache\\All Users\\{{90140000-0011-0000-0000-0000000FF1CE}}-C\\ProPlusWW.msi ErrorCode: 1603
2026-07-01 21:31:14 Rolling back chain
2026-07-01 21:31:20 Successfully rolled back install of package: OfficeMUI.en-us
2026-07-01 21:31:22 Successfully rolled back install of package: WordMUI.en-us
2026-07-01 21:31:30 Catalyst execution finished: Return code: 1603
"""


def _manifest_data() -> dict[str, object]:
    return {
        "schemaVersion": "winforge.app/v0",
        "name": "failure-demo",
        "version": "1.0.0",
        "runtime": {"provider": "wine", "version": "9.0"},
        "install": [{"kind": "exe", "source": "sources/setup.exe", "args": ["/S"]}],
        "launch": {"entrypoint": "C:/Program Files/Demo/Demo.exe"},
        "provenance": {"sources": []},
    }


def _write_bundle_with_logs(root: Path) -> Path:
    bundle = root / "failure-demo-1.0.0"
    (bundle / "metadata").mkdir(parents=True)
    (bundle / "logs").mkdir(parents=True)
    temp = bundle / "prefix" / "drive_c" / "users" / "root" / "Temp"
    temp.mkdir(parents=True)
    (temp / "SetupExe(20260701213100).log").write_text(_office_failure_log(), encoding="utf-8")
    return bundle


class FailureAnalysisTests(unittest.TestCase):
    def test_analyze_failure_path_prioritizes_first_msi_failure_and_redacts_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = _write_bundle_with_logs(root)

            result = analyze_failure_path(bundle, write=True)

            self.assertEqual(result["schemaVersion"], FAILURE_ANALYSIS_SCHEMA_VERSION)
            self.assertTrue(result["failureDetected"])
            self.assertEqual(result["classification"], "windows-installer-failed")
            self.assertEqual(result["topLevelReturnCode"], 1603)
            self.assertEqual(result["firstFailedPackage"]["name"], "ProPlusWW")
            self.assertEqual(result["firstFailedPackage"]["errorCode"], 1603)
            self.assertTrue(result["firstFailedPackage"]["path"].endswith("ProPlusWW.msi"))
            self.assertEqual(result["rollbackPackages"], ["OfficeMUI.en-us", "WordMUI.en-us"])
            self.assertEqual(result["summary"]["logsScanned"], 1)
            self.assertGreaterEqual(result["summary"]["failureWindows"], 1)
            excerpt = "\n".join(result["failureWindows"][0]["excerpt"])
            self.assertIn("Return value 3", excerpt)
            self.assertIn("[REDACTED-PIDKEY]", excerpt)
            self.assertNotIn(PRODUCT_KEY, excerpt)

            analysis_file = bundle / "metadata" / "failure-analysis.json"
            summary_file = bundle / "metadata" / "failure-summary.md"
            self.assertTrue(analysis_file.exists())
            self.assertTrue(summary_file.exists())
            self.assertIn("ProPlusWW", summary_file.read_text(encoding="utf-8"))
            self.assertNotIn(PRODUCT_KEY, summary_file.read_text(encoding="utf-8"))

    def test_analyze_failure_path_reports_installed_executable_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = _write_bundle_with_logs(root)
            exe = bundle / "prefix" / "drive_c" / "Program Files" / "Microsoft Office" / "Office14" / "WINWORD.EXE"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"fake exe")

            result = analyze_failure_path(bundle, write=False)

            self.assertEqual(result["installedExecutables"][0]["name"], "WINWORD.EXE")
            self.assertIn("Office14/WINWORD.EXE", result["installedExecutables"][0]["path"])

    def test_failure_report_rejects_metadata_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = _write_bundle_with_logs(root)
            outside = root / "outside"
            outside.mkdir()
            metadata = bundle / "metadata"
            for child in metadata.iterdir():
                child.unlink()
            metadata.rmdir()
            metadata.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(FailureAnalysisError, "metadata.*symlink"):
                analyze_failure_path(bundle, write=True)

            self.assertFalse((outside / "failure-analysis.json").exists())
            self.assertFalse((outside / "failure-summary.md").exists())

    def test_failure_analysis_redacts_all_emitted_string_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            keyed_root = root / PRODUCT_KEY
            bundle = keyed_root / "failure-demo-1.0.0"
            (bundle / "metadata").mkdir(parents=True)
            temp = bundle / "prefix" / "drive_c" / "users" / "root" / "Temp"
            temp.mkdir(parents=True)
            log = temp / f"Setup-{PRODUCT_KEY}.log"
            log.write_text(
                f"Executing chained package: {PRODUCT_KEY}\n"
                f"Failed to install product: C:\\MSOCache\\{PRODUCT_KEY}\\ProPlusWW.msi ErrorCode: 1603\n"
                f"Successfully rolled back install of package: {PRODUCT_KEY}\n"
                "Catalyst execution finished: Return code: 1603\n",
                encoding="utf-8",
            )

            result = analyze_failure_path(bundle, write=False)

            self.assertNotIn(PRODUCT_KEY, json.dumps(result))
            self.assertIn("[REDACTED-PIDKEY]", json.dumps(result))

    def test_failure_analysis_uses_execution_result_exit_code_when_logs_lack_return_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "failure-demo-1.0.0"
            (bundle / "metadata").mkdir(parents=True)
            (bundle / "logs").mkdir()
            (bundle / "logs" / "build.log").write_text("container stopped without setup return marker", encoding="utf-8")
            (bundle / "metadata" / "execution-result.json").write_text(json.dumps({"success": False, "exitCode": 1603}), encoding="utf-8")

            result = analyze_failure_path(bundle, write=False)

            self.assertTrue(result["failureDetected"])
            self.assertEqual(result["topLevelReturnCode"], 1603)
            self.assertEqual(result["classification"], "windows-installer-failed")

    def test_failure_windows_prioritize_first_real_msi_failure_over_rollback_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "failure-demo-1.0.0"
            (bundle / "metadata").mkdir(parents=True)
            temp = bundle / "prefix" / "drive_c" / "users" / "root" / "Temp"
            temp.mkdir(parents=True)
            (temp / "SetupExe.log").write_text(
                "Error 1907. Generic warning before install finalize\n"
                "Successfully rolled back install of package: EarlyRollback.noise\n"
                "Executing chained package: ProPlusWW\n"
                "MSI(INFO): doing real work\n"
                "Action ended 21:31:12: InstallFinalize. Return value 3.\n"
                "Failed to install product: C:\\MSOCache\\All Users\\x\\ProPlusWW.msi ErrorCode: 1603\n"
                "Catalyst execution finished: Return code: 1603\n",
                encoding="utf-8",
            )

            result = analyze_failure_path(bundle, write=False)
            first_window = "\n".join(result["failureWindows"][0]["excerpt"])

            self.assertIn("Return value 3", first_window)
            self.assertIn("ProPlusWW.msi", first_window)

    def test_failure_analysis_skips_symlinked_logs_outside_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "failure-demo-1.0.0"
            (bundle / "metadata").mkdir(parents=True)
            logs = bundle / "logs"
            logs.mkdir()
            outside = root / "outside.log"
            outside.write_text(_office_failure_log(), encoding="utf-8")
            (logs / "linked.log").symlink_to(outside)

            result = analyze_failure_path(bundle, write=False)

            self.assertEqual(result["summary"]["logsScanned"], 0)
            self.assertFalse(result["failureDetected"])
            self.assertEqual(result["failureWindows"], [])

    def test_failure_analysis_skips_logs_through_symlinked_prefix_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "failure-demo-1.0.0"
            (bundle / "metadata").mkdir(parents=True)
            outside_prefix = root / "outside-prefix"
            outside_temp = outside_prefix / "drive_c" / "users" / "root" / "Temp"
            outside_temp.mkdir(parents=True)
            (outside_temp / "outside.log").write_text(_office_failure_log(), encoding="utf-8")
            (bundle / "prefix").symlink_to(outside_prefix, target_is_directory=True)

            result = analyze_failure_path(bundle, write=False)

            self.assertEqual(result["summary"]["logsScanned"], 0)
            self.assertFalse(result["failureDetected"])
            self.assertEqual(result["failureWindows"], [])

    def test_failure_analysis_skips_installed_exes_through_symlinked_prefix_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "failure-demo-1.0.0"
            (bundle / "metadata").mkdir(parents=True)
            outside_prefix = root / "outside-prefix"
            outside_exe = outside_prefix / "drive_c" / "Program Files" / "Microsoft Office" / "Office14" / "WINWORD.EXE"
            outside_exe.parent.mkdir(parents=True)
            outside_exe.write_bytes(b"fake exe")
            (bundle / "prefix").symlink_to(outside_prefix, target_is_directory=True)

            result = analyze_failure_path(bundle, write=False)

            self.assertEqual(result["installedExecutables"], [])
            self.assertEqual(result["summary"]["installedExecutables"], 0)

    def test_failure_analysis_ignores_execution_result_through_symlinked_metadata_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "failure-demo-1.0.0"
            bundle.mkdir(parents=True)
            outside_metadata = root / "outside-metadata"
            outside_metadata.mkdir()
            (outside_metadata / "execution-result.json").write_text(json.dumps({"success": False, "exitCode": 1603}), encoding="utf-8")
            (bundle / "metadata").symlink_to(outside_metadata, target_is_directory=True)

            result = analyze_failure_path(bundle, write=False)

            self.assertFalse(result["failureDetected"])
            self.assertIsNone(result["topLevelReturnCode"])
            self.assertIsNone(result["summary"]["executionReturnCode"])

    def test_failure_analysis_redacts_compound_secret_assignments_in_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bundle = root / "failure-demo-1.0.0"
            (bundle / "metadata").mkdir(parents=True)
            logs = bundle / "logs"
            logs.mkdir()
            (logs / "build.log").write_text(
                "MSI(ERROR): access_token=tok123 client_secret=sec456 secret_key=key789\n"
                "Action ended 21:31:12: InstallFinalize. Return value 3.\n"
                "Catalyst execution finished: Return code: 1603\n",
                encoding="utf-8",
            )

            result = analyze_failure_path(bundle, write=True)
            report_json = json.dumps(result)
            report_md = (bundle / "metadata" / "failure-summary.md").read_text(encoding="utf-8")

            for secret in ("tok123", "sec456", "key789"):
                self.assertNotIn(secret, report_json)
                self.assertNotIn(secret, report_md)
            self.assertIn("access_token=[REDACTED]", report_json)
            self.assertIn("client_secret=[REDACTED]", report_json)
            self.assertIn("secret_key=[REDACTED]", report_json)

    def test_failure_analyze_cli_emits_json_and_writes_report_files(self):
        repo = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            bundle = _write_bundle_with_logs(Path(tmp))
            proc = subprocess.run(
                [sys.executable, "cmd/winforge.py", "failure", "analyze", str(bundle)],
                cwd=repo,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(payload["schemaVersion"], FAILURE_ANALYSIS_SCHEMA_VERSION)
            self.assertTrue(payload["failureDetected"])
            self.assertTrue((bundle / "metadata" / "failure-analysis.json").exists())
            self.assertTrue((bundle / "metadata" / "failure-summary.md").exists())

    def test_compat_build_failure_attaches_and_writes_failure_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "sources").mkdir()
            (root / "sources" / "setup.exe").write_bytes(b"fake setup")
            manifest = root / "failure-demo.winforge.json"
            manifest.write_text(json.dumps(_manifest_data(), indent=2), encoding="utf-8")

            def fake_execute(manifest_obj, bundle, **kwargs):
                (bundle / "logs").mkdir(parents=True, exist_ok=True)
                (bundle / "logs" / "build.log").write_text(_office_failure_log(), encoding="utf-8")
                return BuildResult(
                    success=False,
                    bundle_path=str(bundle),
                    runtime_provider="wine",
                    runtime_version="9.0",
                    image_ref="local/runtime:test",
                    engine="docker",
                    exit_code=1603,
                    error="container failed",
                )

            with patch("compat.evidence.execute_inside_container", side_effect=fake_execute):
                result = run_compat_test(
                    manifest,
                    output_dir=root / "dist",
                    workspace=root,
                    engine="docker",
                    mode="build",
                )

            bundle = Path(result["build"]["bundle"])
            self.assertFalse(result["success"])
            self.assertEqual(result["classification"], "build-failed")
            self.assertTrue(result["failureAnalysis"]["failureDetected"])
            self.assertEqual(result["failureAnalysis"]["firstFailedPackage"]["name"], "ProPlusWW")
            self.assertTrue((bundle / "metadata" / "failure-analysis.json").exists())
            self.assertTrue((bundle / "metadata" / "failure-summary.md").exists())


if __name__ == "__main__":
    unittest.main()
