"""Compatibility evidence harness for WinForge recipes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from artifact.bundle import bundle_path_for, create_bundle
from artifact.checkpoint import CheckpointError, inspect_checkpoint, seed_bundle_from_checkpoint
from artifact.index import default_index_path, register_bundle
from artifact.inspection import verify_bundle
from builder.executor import execute_inside_container
from core.manifest import load_manifest
from core.sources import verify_manifest_sources
from compat.failure_analysis import analyze_failure_path
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
    entrypoints: list[str] | None = None,
    all_entrypoints: bool = False,
    run_files: list[Path | str] | None = None,
    runner_cache_dir: Path | str | None = None,
    resume_from_bundle: Path | str | None = None,
    stop_before: str | None = None,
) -> dict[str, Any]:
    """Run a compatibility evidence pass.

    Modes:
    - dry-run: source integrity, dry-run bundle, bundle verification, run plan.
    - build:   source integrity plus real container build evidence.
    - run:     real build evidence plus real app run evidence.
    """
    if mode not in {"dry-run", "build", "run"}:
        raise ValueError("mode must be one of: dry-run, build, run")
    if stop_before not in {None, "install-apps"}:
        raise ValueError("stop_before must be one of: install-apps")
    if stop_before and mode == "run":
        raise ValueError("stop_before is only supported with dry-run or build mode")

    manifest_file = Path(manifest_path)
    workspace_path = Path(workspace or Path.cwd()).resolve()
    output_path = Path(output_dir)
    manifest = load_manifest(manifest_file)
    runtime = resolve_runtime(manifest.runtime)
    source_integrity = verify_manifest_sources(manifest, workspace=workspace_path)
    requested_entrypoints = _requested_entrypoints(manifest, entrypoints or [], all_entrypoints)

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
        "runPlans": [],
        "entrypointEvidence": [],
        "run": {"attempted": False, "reason": f"mode={mode}"},
        "checkpoint": {
            "resumed": False,
            "resumeFromBundle": str(resume_from_bundle) if resume_from_bundle else None,
            "stopBefore": stop_before,
        },
        "success": False,
        "classification": "not-run",
    }

    try:
        resolved_resume_bundle = None
        if resume_from_bundle:
            resume_inspection = inspect_checkpoint(resume_from_bundle)
            if not resume_inspection.get("valid"):
                raise CheckpointError(
                    "invalid resume checkpoint: " + "; ".join(resume_inspection.get("errors") or [])
                )
            resolved_resume_bundle = Path(str(resume_inspection["bundle"]))

        prospective_bundle = bundle_path_for(manifest, output_path)
        if resolved_resume_bundle:
            _reject_symlinked_existing_components(output_path, label="output path")
            _reject_attempt_overlaps_resume_source(resolved_resume_bundle, prospective_bundle)
        bundle = create_bundle(manifest, output_path, dry_run=(mode == "dry-run"))
        artifact_entry = None
        if resolved_resume_bundle:
            checkpoint_resume = seed_bundle_from_checkpoint(resolved_resume_bundle, bundle)
            checkpoint_resume["resumed"] = True
            checkpoint_resume["stopBefore"] = stop_before
            payload["checkpoint"] = checkpoint_resume

        if mode == "dry-run":
            artifact_entry = register_bundle(bundle, index_path=default_index_path(output_path))
            verification = verify_bundle(bundle)
            run_plans = _build_run_plans(
                bundle,
                graphics=graphics,
                engine=engine,
                entrypoints=requested_entrypoints,
                run_files=run_files or [],
                runner_cache_dir=runner_cache_dir,
            )
            run_plan = run_plans[0]
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
            payload["runPlans"] = run_plans
            payload["entrypointEvidence"] = _entrypoint_evidence_from_plans(run_plans)
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

        build_kwargs = {
            "engine": engine,
            "image_ref": runtime.oci_image,
            "timeout": build_timeout,
            "workspace": workspace_path,
        }
        if manifest.runtime.runner or runner_cache_dir is not None:
            build_kwargs["runner_cache_dir"] = runner_cache_dir
        if stop_before:
            build_kwargs["stop_before"] = stop_before
        build_result = execute_inside_container(manifest, bundle, **build_kwargs)
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
            "stopBefore": stop_before,
        }
        payload["bundleVerification"] = verification
        run_plans = _build_run_plans(
            bundle,
            graphics=graphics,
            engine=engine,
            entrypoints=requested_entrypoints,
            run_files=run_files or [],
            runner_cache_dir=runner_cache_dir,
            require_runner=True,
        )
        payload["runPlan"] = run_plans[0]
        payload["runPlans"] = run_plans
        payload["entrypointEvidence"] = _entrypoint_evidence_from_plans(run_plans)

        if not build_result.success:
            failure_analysis = analyze_failure_path(bundle, write=True)
            payload["failureAnalysis"] = failure_analysis
            payload["classification"] = "build-failed"
            return payload
        if not verification.get("valid"):
            payload["classification"] = "bundle-verification-failed"
            return payload
        if mode == "build":
            payload["success"] = True
            payload["classification"] = "checkpoint-prepared" if stop_before else "build-passed"
            return payload

        run_results = []
        for plan in payload["runPlans"]:
            run_result = execute_run_plan(plan, timeout=run_timeout)
            run_results.append(run_result)
        payload["entrypointEvidence"] = _entrypoint_evidence_from_plans(payload["runPlans"], run_results)
        run_success = all(bool(result.get("success")) for result in run_results)
        payload["run"] = {
            "attempted": True,
            "success": run_success,
            "results": run_results,
            "entrypoints": [item["entrypoint"] for item in payload["entrypointEvidence"]],
        }
        if len(run_results) == 1:
            payload["run"]["result"] = run_results[0]
        payload["success"] = run_success
        payload["classification"] = "run-passed" if payload["success"] else "run-failed"
    except Exception as exc:  # pragma: no cover - exercised through CLI smoke/failures.
        payload["success"] = False
        payload["classification"] = "harness-error"
        payload["error"] = str(exc)

    return payload



def _requested_entrypoints(manifest, entrypoints: list[str], all_entrypoints: bool) -> list[str]:
    if all_entrypoints and entrypoints:
        raise ValueError("entrypoints and all_entrypoints are mutually exclusive")
    if all_entrypoints:
        return [entrypoint.id for entrypoint in manifest.entrypoints]
    return list(entrypoints)


def _build_run_plans(
    bundle: Path,
    *,
    graphics: str,
    engine: str | None,
    entrypoints: list[str],
    run_files: list[Path | str],
    runner_cache_dir: Path | str | None = None,
    require_runner: bool = False,
) -> list[dict[str, Any]]:
    if not entrypoints:
        return [build_run_plan(
            bundle,
            graphics=graphics,
            engine=engine,
            files=run_files,
            runner_cache_dir=runner_cache_dir,
            require_runner=require_runner,
        )]
    return [
        build_run_plan(
            bundle,
            graphics=graphics,
            engine=engine,
            entrypoint=entrypoint,
            files=run_files,
            runner_cache_dir=runner_cache_dir,
            require_runner=require_runner,
        )
        for entrypoint in entrypoints
    ]


def _entrypoint_evidence_from_plans(
    plans: list[dict[str, Any]],
    results: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    evidence = []
    results = results or []
    for index, plan in enumerate(plans):
        item = {
            "entrypoint": plan.get("selectedEntrypoint"),
            "runPlan": plan,
        }
        if index < len(results):
            item["run"] = results[index]
        evidence.append(item)
    return evidence


def _reject_attempt_overlaps_resume_source(source_bundle: Path, attempt_bundle: Path) -> None:
    source = source_bundle.resolve()
    attempt = attempt_bundle.resolve(strict=False)
    try:
        attempt.relative_to(source)
    except ValueError:
        pass
    else:
        raise CheckpointError(f"compat attempt bundle would be created inside the resume checkpoint: {attempt}")
    try:
        source.relative_to(attempt)
    except ValueError:
        pass
    else:
        raise CheckpointError(f"compat attempt bundle would contain the resume checkpoint: {attempt}")


def _reject_symlinked_existing_components(path: Path, *, label: str) -> None:
    candidates = [path, *path.parents]
    for candidate in candidates:
        if candidate.exists() or candidate.is_symlink():
            if candidate.is_symlink():
                raise CheckpointError(f"{label} must not contain symlink components: {candidate}")
            if not candidate.is_dir():
                raise CheckpointError(f"{label} must not contain non-directory components: {candidate}")
