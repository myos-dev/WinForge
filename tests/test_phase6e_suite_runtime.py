"""Tests for Phase 6E suite entrypoint runtime UX."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from artifact.bundle import create_bundle
from compat.evidence import run_compat_test
from core.manifest import Manifest
from runtime.launcher import RunError, build_run_plan


def suite_manifest_data() -> dict:
    return {
        "schemaVersion": "winforge.app/v0",
        "name": "acme-document-suite",
        "version": "1.0.0",
        "runtime": {"provider": "wine", "version": "9.0"},
        "entrypoints": [
            {
                "id": "writer",
                "name": "Acme Writer",
                "executable": "C:/Program Files/Acme Suite/Writer.exe",
                "args": ["--safe-mode"],
                "workingDirectory": "C:/Program Files/Acme Suite",
            },
            {
                "id": "sheet",
                "name": "Acme Sheet",
                "executable": "C:/Program Files/Acme Suite/Sheet.exe",
            },
        ],
        "fileAssociations": [
            {
                "entrypoint": "writer",
                "extensions": [".docx"],
                "mime": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
            },
            {
                "entrypoint": "sheet",
                "extensions": [".xlsx", ".csv"],
                "mime": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "text/csv"],
            },
        ],
        "launch": {"entrypoint": "C:/Program Files/Acme Suite/Writer.exe"},
        "provenance": {"sources": []},
    }


def write_suite_manifest(path: Path) -> None:
    path.write_text(json.dumps(suite_manifest_data(), indent=2), encoding="utf-8")


class SuiteRunPlanTests(unittest.TestCase):
    def test_run_plan_selects_named_suite_entrypoint_and_routes_host_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bundle = create_bundle(Manifest.from_dict(suite_manifest_data()), tmp / "dist", dry_run=True)
            doc = tmp / "inputs" / "budget.xlsx"
            doc.parent.mkdir()
            doc.write_text("fake workbook", encoding="utf-8")

            plan = build_run_plan(
                bundle,
                graphics="headless",
                engine="docker",
                entrypoint="sheet",
                files=[doc],
            )

        self.assertEqual(plan["selectedEntrypoint"]["id"], "sheet")
        self.assertEqual(plan["launch"]["entrypoint"], "C:/Program Files/Acme Suite/Sheet.exe")
        self.assertIn("C:/Program Files/Acme Suite/Sheet.exe", plan["launchCommand"])
        self.assertIn("Z:\\mnt\\winforge-inputs\\0\\budget.xlsx", plan["launchCommand"])
        self.assertEqual(plan["fileArguments"][0]["hostPath"], str(doc.resolve()))
        self.assertEqual(plan["fileArguments"][0]["containerPath"], "/mnt/winforge-inputs/0/budget.xlsx")
        self.assertEqual(plan["fileArguments"][0]["winePath"], "Z:\\mnt\\winforge-inputs\\0\\budget.xlsx")
        self.assertIn(f"{doc.parent.resolve()}:/mnt/winforge-inputs/0:ro", plan["container"]["fileMounts"])
        self.assertIn(f"{doc.parent.resolve()}:/mnt/winforge-inputs/0:ro", plan["container"]["argv"])

    def test_run_plan_rejects_unknown_suite_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bundle = create_bundle(Manifest.from_dict(suite_manifest_data()), tmp / "dist", dry_run=True)
            with self.assertRaisesRegex(RunError, "unknown suite entrypoint"):
                build_run_plan(bundle, graphics="headless", engine="docker", entrypoint="publisher")

    def test_cli_run_accepts_entrypoint_and_file_arguments(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bundle = create_bundle(Manifest.from_dict(suite_manifest_data()), tmp / "dist", dry_run=True)
            doc = tmp / "inputs" / "notes.docx"
            doc.parent.mkdir()
            doc.write_text("fake doc", encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    "cmd/winforge.py",
                    "run",
                    str(bundle),
                    "--dry-run",
                    "--graphics",
                    "headless",
                    "--engine",
                    "docker",
                    "--entrypoint",
                    "writer",
                    str(doc),
                ],
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["selectedEntrypoint"]["id"], "writer")
        self.assertIn("--safe-mode", payload["launchCommand"])
        self.assertIn("Z:\\mnt\\winforge-inputs\\0\\notes.docx", payload["launchCommand"])


class SuiteCompatEvidenceTests(unittest.TestCase):
    def test_compat_dry_run_records_requested_suite_entrypoint_plans(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            manifest = tmp / "suite.winforge.json"
            write_suite_manifest(manifest)

            result = run_compat_test(
                manifest,
                output_dir=tmp / "dist",
                workspace=tmp,
                graphics="headless",
                engine="docker",
                mode="dry-run",
                entrypoints=["writer", "sheet"],
            )

        self.assertTrue(result["success"], result)
        self.assertEqual(result["classification"], "dry-run-planned")
        self.assertEqual([item["entrypoint"]["id"] for item in result["entrypointEvidence"]], ["writer", "sheet"])
        self.assertEqual(len(result["runPlans"]), 2)
        self.assertEqual(result["runPlans"][0]["selectedEntrypoint"]["id"], "writer")
        self.assertEqual(result["runPlans"][1]["selectedEntrypoint"]["id"], "sheet")

    def test_compat_all_entrypoints_uses_manifest_suite_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            manifest = tmp / "suite.winforge.json"
            write_suite_manifest(manifest)

            result = run_compat_test(
                manifest,
                output_dir=tmp / "dist",
                workspace=tmp,
                graphics="headless",
                engine="docker",
                mode="dry-run",
                all_entrypoints=True,
            )

        self.assertEqual([item["entrypoint"]["id"] for item in result["entrypointEvidence"]], ["writer", "sheet"])


if __name__ == "__main__":
    unittest.main()
