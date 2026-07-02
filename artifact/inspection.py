"""Inspect and verify WinForge execution bundles.

Phase 2 makes bundle inspection/verification a formal layer before
`winforge run`. It validates the Phase 1 graph contract without requiring
Wine, Docker, Podman, or OCI access.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GRAPH_SCHEMA_VERSION = "winforge.execution-graph/v0"
MANIFEST_SCHEMA_VERSIONS = {"winforge.app/v0", "winforge.dev/v0"}
INSPECTION_SCHEMA_VERSION = "winforge.bundle-inspection/v0"
VERIFICATION_SCHEMA_VERSION = "winforge.bundle-verification/v0"
SUPPORTED_NETWORK_MODES = {"none", "bridge", "host"}

REQUIRED_FILES = [
    "manifest.winforge.json",
    "prefix/drive_c",
    "runtime/runtime.json",
    "launch/entrypoint.json",
    "metadata/provenance.json",
    "metadata/graph.json",
    "build/build-plan.json",
    "logs/build.log",
]

JSON_FILES = [
    "manifest.winforge.json",
    "runtime/runtime.json",
    "launch/entrypoint.json",
    "metadata/provenance.json",
    "metadata/graph.json",
    "build/build-plan.json",
]


def inspect_bundle(bundle_path: Path | str) -> dict[str, Any]:
    """Return a structured summary for a valid-enough WinForge bundle."""
    bundle = Path(bundle_path)
    manifest = _load_json(bundle, "manifest.winforge.json")
    runtime = _load_json(bundle, "runtime/runtime.json")
    launch = _load_json(bundle, "launch/entrypoint.json")
    provenance = _load_json(bundle, "metadata/provenance.json")
    graph = _load_json(bundle, "metadata/graph.json")
    build_plan = _load_json(bundle, "build/build-plan.json")

    application = graph.get("application") or {
        "name": manifest.get("name"),
        "version": manifest.get("version"),
    }

    return {
        "schemaVersion": INSPECTION_SCHEMA_VERSION,
        "bundle": str(bundle),
        "application": application,
        "artifact": graph.get("artifact", {"kind": "winforge.bundle", "path": "."}),
        "runtime": {
            "manifest": runtime,
            "builder": graph.get("builderRuntime", {}),
            "runner": graph.get("runnerRuntime", {}),
        },
        "graphics": graph.get("graphics", {}),
        "launch": launch,
        "compatibility": graph.get("compatibility", {}),
        "graph": {
            "path": "metadata/graph.json",
            "schemaVersion": graph.get("schemaVersion"),
            "nodes": len(graph.get("nodes", [])),
            "edges": len(graph.get("edges", [])),
        },
        "build": {
            "planPath": "build/build-plan.json",
            "phaseCount": len(build_plan.get("phases", [])),
        },
        "provenance": {
            "path": "metadata/provenance.json",
            "schemaVersion": provenance.get("schemaVersion"),
            "dryRun": provenance.get("dryRun"),
            "createdAt": provenance.get("createdAt"),
        },
        "files": {rel: _file_summary(bundle / rel) for rel in REQUIRED_FILES},
    }


def verify_bundle(bundle_path: Path | str) -> dict[str, Any]:
    """Validate a WinForge bundle and return machine-readable results."""
    bundle = Path(bundle_path)
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    def add_check(check_id: str, ok: bool, message: str,
                  *, details: dict[str, Any] | None = None,
                  error: str | None = None,
                  warning: str | None = None) -> None:
        check: dict[str, Any] = {"id": check_id, "ok": ok, "message": message}
        if details:
            check["details"] = details
        checks.append(check)
        if not ok and error:
            errors.append(error)
        if warning:
            warnings.append(warning)

    bundle_is_dir = bundle.exists() and bundle.is_dir()
    add_check(
        "bundle-directory",
        bundle_is_dir,
        "bundle path exists and is a directory" if bundle_is_dir
        else "bundle path is missing or is not a directory",
        error=f"bundle path is missing or not a directory: {bundle}",
    )

    missing = [rel for rel in REQUIRED_FILES if not (bundle / rel).exists()]
    add_check(
        "required-files",
        not missing,
        "all required bundle files are present" if not missing
        else "bundle is missing required files",
        details={"missing": missing} if missing else None,
    )
    for rel in missing:
        errors.append(f"missing required file: {rel}")

    parsed: dict[str, Any] = {}
    json_errors: list[str] = []
    for rel in JSON_FILES:
        path = bundle / rel
        if not path.exists():
            continue
        try:
            parsed[rel] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            json_errors.append(f"invalid JSON in {rel}: {exc}")
    add_check(
        "json-readable",
        not json_errors,
        "bundle JSON files parse successfully" if not json_errors
        else "one or more bundle JSON files are invalid",
        details={"errors": json_errors} if json_errors else None,
    )
    errors.extend(json_errors)

    manifest = parsed.get("manifest.winforge.json", {})
    runtime = parsed.get("runtime/runtime.json", {})
    launch = parsed.get("launch/entrypoint.json", {})
    graph = parsed.get("metadata/graph.json", {})
    provenance = parsed.get("metadata/provenance.json", {})
    build_plan = parsed.get("build/build-plan.json", {})

    add_check(
        "manifest-schema",
        manifest.get("schemaVersion") in MANIFEST_SCHEMA_VERSIONS,
        "manifest schema is supported WinForge v0",
        error="manifest schemaVersion must be one of: " + ", ".join(sorted(MANIFEST_SCHEMA_VERSIONS)),
    )
    add_check(
        "provenance-schema",
        provenance.get("schemaVersion") == "winforge.bundle/v0",
        "provenance schema is winforge.bundle/v0",
        error="metadata/provenance.json schemaVersion must be winforge.bundle/v0",
    )
    add_check(
        "graph-schema",
        graph.get("schemaVersion") == GRAPH_SCHEMA_VERSION,
        f"graph schema is {GRAPH_SCHEMA_VERSION}",
        error=f"metadata/graph.json schemaVersion must be {GRAPH_SCHEMA_VERSION}",
    )
    add_check(
        "build-plan-phases",
        isinstance(build_plan.get("phases"), list) and bool(build_plan.get("phases")),
        "build plan contains phases",
        error="build/build-plan.json must contain a non-empty phases list",
    )

    expected_app = {
        "name": manifest.get("name"),
        "version": manifest.get("version"),
    }
    add_check(
        "graph-application-match",
        graph.get("application") == expected_app,
        "graph application matches manifest name/version",
        details={"expected": expected_app, "actual": graph.get("application")},
        error="graph application does not match manifest name/version",
    )

    graph_builder = graph.get("builderRuntime", {})
    graph_runner = graph.get("runnerRuntime", {})
    runtime_pair = {
        "provider": runtime.get("provider"),
        "version": runtime.get("version"),
    }
    builder_pair = {
        "provider": graph_builder.get("provider"),
        "version": graph_builder.get("version"),
    }
    runner_pair = {
        "provider": graph_runner.get("provider"),
        "version": graph_runner.get("version"),
    }
    runtime_match = runtime_pair == builder_pair == runner_pair
    add_check(
        "graph-runtime-match",
        runtime_match,
        "runtime.json matches graph builderRuntime and runnerRuntime",
        details={
            "runtime": runtime_pair,
            "builderRuntime": builder_pair,
            "runnerRuntime": runner_pair,
        },
        error="runtime.json must match graph builderRuntime and runnerRuntime provider/version",
    )

    manifest_runtime = manifest.get("runtime") if isinstance(manifest.get("runtime"), dict) else {}
    manifest_network = manifest_runtime["network"] if "network" in manifest_runtime else "none"
    runner_network = graph_runner["network"] if "network" in graph_runner else "none"
    network_ok = (
        isinstance(manifest_network, str)
        and isinstance(runner_network, str)
        and manifest_network in SUPPORTED_NETWORK_MODES
        and runner_network in SUPPORTED_NETWORK_MODES
        and manifest_network == runner_network
    )
    add_check(
        "runtime-network-match",
        network_ok,
        "manifest runtime.network matches graph runnerRuntime.network",
        details={
            "manifestRuntimeNetwork": manifest_network,
            "runnerRuntimeNetwork": runner_network,
            "allowed": sorted(SUPPORTED_NETWORK_MODES),
        },
        error="manifest runtime.network must match graph runnerRuntime.network and use a supported mode",
    )

    graph_launch = graph.get("launch", {})
    add_check(
        "launch-match",
        graph_launch.get("entrypoint") == launch.get("entrypoint"),
        "graph launch entrypoint matches launch/entrypoint.json",
        details={
            "launch": launch.get("entrypoint"),
            "graph": graph_launch.get("entrypoint"),
        },
        error="graph launch entrypoint does not match launch/entrypoint.json",
    )

    compatibility = graph.get("compatibility", {})
    add_check(
        "exact-runtime-policy",
        compatibility.get("requiresExactRuntime") is True,
        "graph requires exact runtime compatibility",
        error="graph compatibility.requiresExactRuntime must be true for v0 bundles",
    )

    graphics = graph.get("graphics", {})
    modes = graphics.get("supportedModes", [])
    graphics_ok = graphics.get("defaultMode") in modes and {"headless", "vnc"}.issubset(set(modes))
    add_check(
        "graphics-contract",
        graphics_ok,
        "graph supports headless and vnc graphics modes",
        details={"defaultMode": graphics.get("defaultMode"), "supportedModes": modes},
        error="graph graphics must include defaultMode and support headless and vnc",
    )

    node_ids = {node.get("id") for node in graph.get("nodes", [])}
    graph_nodes_ok = {"artifact:bundle", "launch:entrypoint", "prefix:wineprefix"}.issubset(node_ids)
    add_check(
        "graph-required-nodes",
        graph_nodes_ok,
        "graph contains required artifact, launch, and prefix nodes",
        details={"required": ["artifact:bundle", "launch:entrypoint", "prefix:wineprefix"]},
        error="graph is missing one or more required nodes",
    )

    valid = bool(checks) and all(check["ok"] for check in checks)
    return {
        "schemaVersion": VERIFICATION_SCHEMA_VERSION,
        "bundle": str(bundle),
        "valid": valid,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def _load_json(bundle: Path, rel: str) -> dict[str, Any]:
    return json.loads((bundle / rel).read_text(encoding="utf-8"))


def _file_summary(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "type": "directory" if exists and path.is_dir() else "file" if exists else None,
        "size": path.stat().st_size if exists and path.is_file() else None,
    }
