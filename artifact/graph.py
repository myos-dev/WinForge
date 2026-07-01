"""Execution graph generation for WinForge bundles.

The execution graph is the resolved, machine-readable contract that sits
between a declarative manifest and a runnable WinForge bundle.  It keeps the
Ramalama-like concepts distinct:

- runtime OCI image selected from the catalog;
- application/prefix bundle as the workload artifact;
- launch contract and graphics/run policy;
- deterministic phase nodes/edges for build/run tooling.
"""
from __future__ import annotations
from typing import Any

from builder.pipeline import build_plan
from core.manifest import Manifest
from runtime.providers import RuntimeBinding, resolve_runtime

SCHEMA_VERSION = "winforge.execution-graph/v0"
DEFAULT_GRAPHICS_MODE = "headless"
SUPPORTED_GRAPHICS_MODES = ["headless", "vnc"]


def build_execution_graph(manifest: Manifest) -> dict[str, Any]:
    """Return a deterministic resolved execution graph for *manifest*."""
    runtime = resolve_runtime(manifest.runtime)
    runtime_node_id = _runtime_node_id(runtime)
    manifest_node_id = f"manifest:{manifest.name}:{manifest.version}"
    phase_plan = build_plan(manifest)
    phase_nodes = [_phase_node(phase) for phase in phase_plan]

    runtime_payload = _runtime_payload(runtime)

    nodes: list[dict[str, Any]] = [
        {
            "id": manifest_node_id,
            "kind": "manifest",
            "label": f"{manifest.name}:{manifest.version}",
            "application": {
                "name": manifest.name,
                "version": manifest.version,
            },
        },
        {
            "id": runtime_node_id,
            "kind": "runtime-image",
            "label": runtime_payload["image"],
            "runtime": dict(runtime_payload),
        },
        *phase_nodes,
        {
            "id": "prefix:wineprefix",
            "kind": "prefix",
            "label": "Wine prefix",
            "path": "prefix",
        },
        {
            "id": "launch:entrypoint",
            "kind": "launch",
            "label": manifest.launch.entrypoint,
            "launch": manifest.launch.to_dict(),
        },
        {
            "id": "artifact:bundle",
            "kind": "artifact",
            "label": f"{manifest.name}-{manifest.version}",
            "artifact": {
                "kind": "winforge.bundle",
                "path": ".",
            },
        },
    ]

    edges: list[dict[str, str]] = [
        {"from": manifest_node_id, "to": runtime_node_id, "type": "resolves"},
        {"from": manifest_node_id, "to": "phase:init-prefix", "type": "provides"},
        {"from": runtime_node_id, "to": "phase:init-prefix", "type": "executes"},
    ]
    for left, right in zip(phase_plan, phase_plan[1:]):
        edges.append({
            "from": f"phase:{left['phase']}",
            "to": f"phase:{right['phase']}",
            "type": "precedes",
        })
    edges.extend([
        {"from": "phase:init-prefix", "to": "prefix:wineprefix", "type": "creates"},
        {"from": "prefix:wineprefix", "to": "phase:install-dependencies", "type": "mutates"},
        {"from": "prefix:wineprefix", "to": "phase:install-apps", "type": "mutates"},
        {"from": "launch:entrypoint", "to": "phase:validate", "type": "validates"},
        {"from": "phase:seal-artifact", "to": "artifact:bundle", "type": "produces"},
    ])

    return {
        "schemaVersion": SCHEMA_VERSION,
        "application": {
            "name": manifest.name,
            "version": manifest.version,
        },
        "manifest": {
            "schemaVersion": manifest.schema_version,
            "path": "manifest.winforge.json",
        },
        "artifact": {
            "kind": "winforge.bundle",
            "path": ".",
        },
        "builderRuntime": dict(runtime_payload),
        "runnerRuntime": dict(runtime_payload),
        "graphics": {
            "defaultMode": DEFAULT_GRAPHICS_MODE,
            "supportedModes": SUPPORTED_GRAPHICS_MODES,
        },
        "launch": manifest.launch.to_dict(),
        "compatibility": {
            "requiresExactRuntime": True,
            "policy": "exact-provider-version",
            "requestedPolicy": manifest.compatibility,
            "reason": (
                "Wine prefixes are stateful runtime artifacts; v0 bundles "
                "must run with the same provider/version used to build them."
            ),
        },
        "nodes": nodes,
        "edges": edges,
    }


def _runtime_payload(runtime: RuntimeBinding) -> dict[str, Any]:
    image = runtime.oci_image or runtime.local_oci_image
    payload = runtime.to_dict()
    payload["image"] = image
    payload["localImage"] = runtime.local_oci_image
    return {k: v for k, v in payload.items() if v is not None}


def _runtime_node_id(runtime: RuntimeBinding) -> str:
    return f"runtime:{runtime.provider}:{runtime.version}"


def _phase_node(phase: dict[str, object]) -> dict[str, Any]:
    name = str(phase["phase"])
    return {
        "id": f"phase:{name}",
        "kind": "build-phase",
        "label": name,
        "phase": name,
        "inputs": list(phase.get("inputs", [])),
        "actions": list(phase.get("actions", [])),
    }
