"""Compatibility evidence harness for WinForge recipes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact.bundle import create_bundle
from artifact.index import default_index_path, register_bundle
from artifact.inspection import verify_bundle
from core.manifest import load_manifest
from core.sources import verify_manifest_sources
from runtime.launcher import build_run_plan
from runtime.providers import resolve_runtime

COMPAT_TEST_SCHEMA_VERSION = "winforge.compat-test/v0"


def run_compat_test(
    manifest_path: Path | str,
    *,
    output_dir: Path | str = "dist",
    workspace: Path | str | None = None,
    graphics: str = "headless",
    engine: str | None = None,
) -> dict[str, Any]:
    """Run a dependency-light compatibility evidence pass.

    Phase 6B intentionally performs no Wine/container execution. It verifies
    local sources, materializes a dry-run bundle, verifies that bundle, and
    emits a run plan carrying runtime/compatibility policy.
    """
    manifest_file = Path(manifest_path)
    workspace_path = Path(workspace or Path.cwd()).resolve()
    output_path = Path(output_dir)
    manifest = load_manifest(manifest_file)
    runtime = resolve_runtime(manifest.runtime)
    source_integrity = verify_manifest_sources(manifest, workspace=workspace_path)

    payload: dict[str, Any] = {
        "schemaVersion": COMPAT_TEST_SCHEMA_VERSION,
        "manifest": {
            "path": str(manifest_file),
            "schemaVersion": manifest.schema_version,
        },
        "workspace": str(workspace_path),
        "application": {
            "name": manifest.name,
            "version": manifest.version,
        },
        "runtime": runtime.to_dict(),
        "compatibility": manifest.compatibility,
        "sourceIntegrity": source_integrity,
        "build": {
            "mode": "dry-run",
            "attempted": False,
            "success": False,
        },
        "bundleVerification": None,
        "runPlan": None,
        "success": False,
        "classification": "not-run",
    }

    try:
        bundle = create_bundle(manifest, output_path, dry_run=True)
        artifact_entry = register_bundle(bundle, index_path=default_index_path(output_path))
        verification = verify_bundle(bundle)
        run_plan = build_run_plan(bundle, graphics=graphics, engine=engine)
        payload["build"] = {
            "mode": "dry-run",
            "attempted": True,
            "success": True,
            "bundle": str(bundle),
            "artifactIndex": artifact_entry["indexPath"],
            "artifact": artifact_entry,
        }
        payload["bundleVerification"] = verification
        payload["runPlan"] = run_plan
        payload["success"] = bool(source_integrity.get("valid")) and bool(verification.get("valid"))
        payload["classification"] = "dry-run-planned" if payload["success"] else "source-integrity-failed"
    except Exception as exc:  # pragma: no cover - exercised through CLI smoke/failures.
        payload["success"] = False
        payload["classification"] = "harness-error"
        payload["error"] = str(exc)

    return payload
