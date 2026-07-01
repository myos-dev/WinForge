"""Compatibility evidence harness for WinForge recipes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact.bundle import create_bundle
from artifact.index import default_index_path, register_bundle
from artifact.inspection import verify_bundle
from builder.executor import execute_inside_container
from core.manifest import load_manifest
from core.sources import verify_manifest_sources
from runtime.launcher import build_run_plan, execute_run_plan
from runtime.providers import resolve_runtime

COMPAT_TEST_SCHEMA_VERSION = "winforge.compat-test/v0"


def json_dumps(payload: object) -> str:
    import json
    return json.dumps(payload, indent=2, sort_keys=True)


def run_compat_test(
    manifest_path: Path | str,
    *,
    output_dir: Path | str = "dist",
    workspace: Path | str | None = None,
    graphics: str = "headless",
    engine: str | None = None,
    mode: str = "dry-run",
    build_timeout: int = 600,
    run_timeout: int | None = None,
) -> dict[str, Any]:
    """Run a compatibility evidence pass.

    Modes:
    - dry-run: source integrity, dry-run bundle, bundle verification, run plan.
    - build:   source integrity plus real container build evidence.
    - run:     real build evidence plus real app run evidence.
    """
    if mode not in {"dry-run", "build", "run"}:
        raise ValueError("mode must be one of: dry-run, build, run")

    manifest_file = Path(manifest_path)
    workspace_path = Path(workspace or Path.cwd()).resolve()
    output_path = Path(output_dir)
    manifest = load_manifest(manifest_file)
    runtime = resolve_runtime(manifest.runtime)
    source_integrity = verify_manifest_sources(manifest, workspace=workspace_path)

    payload: dict[str, Any] = {
        "schemaVersion": COMPAT_TEST_SCHEMA_VERSION,
        "mode": mode,
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
            "mode": "dry-run" if mode == "dry-run" else "real",
            "attempted": False,
            "success": False,
        },
        "bundleVerification": None,
        "runPlan": None,
        "run": {"attempted": False, "reason": f"mode={mode}"},
        "success": False,
        "classification": "not-run",
    }

    try:
        bundle = create_bundle(manifest, output_path, dry_run=(mode == "dry-run"))
        artifact_entry = None

        if mode == "dry-run":
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
            return payload

        if not source_integrity.get("valid"):
            payload["build"] = {
                "mode": "real",
                "attempted": False,
                "success": False,
                "bundle": str(bundle),
                "reason": "source-integrity-failed",
            }
            payload["bundleVerification"] = verify_bundle(bundle)
            payload["classification"] = "source-integrity-failed"
            return payload

        build_result = execute_inside_container(
            manifest,
            bundle,
            engine=engine,
            image_ref=runtime.oci_image,
            timeout=build_timeout,
            workspace=workspace_path,
        )
        execution = build_result.to_dict()
        (bundle / "metadata" / "execution-result.json").write_text(
            json_dumps(execution) + "\n",
            encoding="utf-8",
        )
        if build_result.success:
            artifact_entry = register_bundle(bundle, index_path=default_index_path(output_path))
        verification = verify_bundle(bundle)
        payload["build"] = {
            "mode": "real",
            "attempted": True,
            "success": bool(build_result.success),
            "bundle": str(bundle),
            "artifactIndex": artifact_entry["indexPath"] if artifact_entry else str(default_index_path(output_path)),
            "artifact": artifact_entry,
            "execution": execution,
        }
        payload["bundleVerification"] = verification
        payload["runPlan"] = build_run_plan(bundle, graphics=graphics, engine=engine)

        if not build_result.success:
            payload["classification"] = "build-failed"
            return payload
        if not verification.get("valid"):
            payload["classification"] = "bundle-verification-failed"
            return payload
        if mode == "build":
            payload["success"] = True
            payload["classification"] = "build-passed"
            return payload

        run_result = execute_run_plan(payload["runPlan"], timeout=run_timeout)
        payload["run"] = {
            "attempted": True,
            "success": bool(run_result.get("success")),
            "result": run_result,
        }
        payload["success"] = bool(run_result.get("success"))
        payload["classification"] = "run-passed" if payload["success"] else "run-failed"
    except Exception as exc:  # pragma: no cover - exercised through CLI smoke/failures.
        payload["success"] = False
        payload["classification"] = "harness-error"
        payload["error"] = str(exc)

    return payload
