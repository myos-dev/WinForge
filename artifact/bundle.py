"""WinForge execution bundle writer."""
from __future__ import annotations
from datetime import datetime, timezone
import json, re
from pathlib import Path
from artifact.graph import build_execution_graph
from builder.pipeline import build_plan
from core.manifest import Manifest
from runtime.providers import resolve_runtime


def create_bundle(manifest: Manifest, output_dir: Path, *,
                  dry_run: bool) -> Path:
    bundle_path = output_dir / _safe_name(
        f"{manifest.name}-{manifest.version}")
    if bundle_path.exists():
        raise FileExistsError(bundle_path)
    for rel in ("prefix/drive_c", "runtime", "launch", "metadata",
                "build", "logs"):
        (bundle_path / rel).mkdir(parents=True, exist_ok=False)

    runtime = resolve_runtime(manifest.runtime)
    _write_json(bundle_path / "manifest.winforge.json",
                manifest.to_dict())
    _write_json(bundle_path / "runtime/runtime.json",
                runtime.to_dict())
    _write_json(bundle_path / "launch/entrypoint.json",
                manifest.launch.to_dict())
    _write_json(bundle_path / "build/build-plan.json",
                {"phases": build_plan(manifest)})
    _write_json(bundle_path / "metadata/graph.json",
                build_execution_graph(manifest))

    # Provenance differs for dry-run vs. real builds.
    if dry_run:
        notes = [
            "Dry-run bundle records the artifact contract but does "
            "not execute Wine/winetricks installers yet.",
        ]
        (bundle_path / "prefix/drive_c/.keep").write_text(
            "drive_c root placeholder for dry-run bundle\n",
            encoding="utf-8")
        (bundle_path / "logs/build.log").write_text(
            "dry-run bundle materialized; no Wine commands executed\n",
            encoding="utf-8")
    else:
        notes = [
            "Real build — Wine container execution populates the "
            "prefix directory and logs.",
        ]
        (bundle_path / "logs/build.log").write_text(
            "[winforge] Build starting — container execution in progress.\n",
            encoding="utf-8")

    _write_json(
        bundle_path / "metadata/provenance.json",
        {
            "schemaVersion": "winforge.bundle/v0",
            "createdAt": datetime.now(timezone.utc).isoformat(),
            "dryRun": dry_run,
            "builder": "winforge-scaffold",
            "manifest": {
                "name": manifest.name,
                "version": manifest.version,
            },
            "runtime": runtime.to_dict(),
            "compatibility": manifest.compatibility,
            "declaredProvenance": manifest.provenance,
            "notes": notes,
        })

    return bundle_path


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_") or "bundle"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
