"""Tests for Phase 5A runner catalog aliases and resolved runtime metadata."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from artifact.bundle import create_bundle
from core.manifest import Manifest
from runtime.launcher import build_run_plan

LATEST_WINE = {
    "schemaVersion": "winforge.dev/v0",
    "name": "latest-wine-app",
    "version": "1.0.0",
    "runtime": {"provider": "wine", "version": "latest"},
    "dependencies": [],
    "install": [],
    "filesystem": [],
    "launch": {"entrypoint": "C:/App/App.exe"},
    "provenance": {"sources": []},
}

class Phase5ARunnerCatalogTests(unittest.TestCase):
    def test_bundle_records_requested_and_resolved_runtime_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = create_bundle(Manifest.from_dict(LATEST_WINE), Path(tmp), dry_run=True)
            runtime = json.loads((bundle / "runtime/runtime.json").read_text(encoding="utf-8"))
            graph = json.loads((bundle / "metadata/graph.json").read_text(encoding="utf-8"))
            provenance = json.loads((bundle / "metadata/provenance.json").read_text(encoding="utf-8"))

        self.assertEqual(runtime["provider"], "wine")
        self.assertEqual(runtime["requestedVersion"], "latest")
        self.assertEqual(runtime["resolvedVersion"], "11.0")
        self.assertEqual(runtime["version"], "11.0")
        self.assertEqual(runtime["runner"], "winehq-stable")
        self.assertEqual(runtime["packageVersion"], "11.0.0.0~bookworm-1")
        self.assertEqual(runtime["ociImage"], "ghcr.io/myos-dev/winforge-wine:11.0")
        self.assertEqual(graph["runnerRuntime"]["requestedVersion"], "latest")
        self.assertEqual(graph["runnerRuntime"]["resolvedVersion"], "11.0")
        self.assertEqual(graph["runnerRuntime"]["image"], "ghcr.io/myos-dev/winforge-wine:11.0")
        self.assertEqual(provenance["runtime"]["resolvedVersion"], "11.0")

    def test_run_plan_for_latest_uses_resolved_runtime_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = create_bundle(Manifest.from_dict(LATEST_WINE), Path(tmp), dry_run=True)
            plan = build_run_plan(bundle, graphics="headless", engine="podman")

        self.assertEqual(plan["runtime"]["provider"], "wine")
        self.assertEqual(plan["runtime"]["requestedVersion"], "latest")
        self.assertEqual(plan["runtime"]["resolvedVersion"], "11.0")
        self.assertEqual(plan["runtime"]["version"], "11.0")
        self.assertEqual(plan["runtime"]["image"], "ghcr.io/myos-dev/winforge-wine:11.0")

    def test_wine_dockerfiles_pin_package_versions_from_catalog_build_arg(self):
        root = Path(__file__).resolve().parents[1]
        wine = (root / "container/providers/wine/Dockerfile").read_text(encoding="utf-8")
        staging = (root / "container/providers/wine-staging/Dockerfile").read_text(encoding="utf-8")

        self.assertIn("ARG WINE_PACKAGE_VERSION=11.0.0.0~bookworm-1", wine)
        self.assertIn("winehq-stable=${WINE_PACKAGE_VERSION}", wine)
        self.assertIn("wine-stable=${WINE_PACKAGE_VERSION}", wine)
        self.assertIn("wine-stable-amd64=${WINE_PACKAGE_VERSION}", wine)
        self.assertIn("wine-stable-i386:i386=${WINE_PACKAGE_VERSION}", wine)
        self.assertIn("ARG WINE_PACKAGE_VERSION=11.10~bookworm-1", staging)
        self.assertIn("winehq-staging=${WINE_PACKAGE_VERSION}", staging)
        self.assertIn("wine-staging=${WINE_PACKAGE_VERSION}", staging)
        self.assertIn("wine-staging-amd64=${WINE_PACKAGE_VERSION}", staging)
        self.assertIn("wine-staging-i386:i386=${WINE_PACKAGE_VERSION}", staging)

if __name__ == "__main__":
    unittest.main()
