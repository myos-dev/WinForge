"""Local WinForge artifact index.

The v0 index is a small local cache that maps application names and versions to
verified bundle directories. It is intentionally local and filesystem-based: a
future registry/index can build on the same app-name resolution semantics.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from artifact.inspection import inspect_bundle, verify_bundle

ARTIFACT_INDEX_SCHEMA_VERSION = "winforge.artifact-index/v0"


class ArtifactIndexError(RuntimeError):
    """Raised when an artifact index operation cannot be completed."""


def default_index_path(output_dir: Path | str = "dist") -> Path:
    """Return the default artifact index path under an output directory."""
    return Path(output_dir) / ".winforge" / "artifacts.json"


def empty_index() -> dict[str, Any]:
    return {
        "schemaVersion": ARTIFACT_INDEX_SCHEMA_VERSION,
        "updatedAt": None,
        "latest": {},
        "artifacts": {},
    }


def list_artifacts(index_path: Path | str | None = None) -> dict[str, Any]:
    """Return the artifact index, or an empty index if it does not exist."""
    path = Path(index_path) if index_path is not None else default_index_path()
    if not path.exists():
        index = empty_index()
        index["indexPath"] = str(path)
        return index
    index = json.loads(path.read_text(encoding="utf-8"))
    if index.get("schemaVersion") != ARTIFACT_INDEX_SCHEMA_VERSION:
        raise ArtifactIndexError(
            f"artifact index schemaVersion must be {ARTIFACT_INDEX_SCHEMA_VERSION}: {path}"
        )
    index["indexPath"] = str(path)
    return index


def register_bundle(
    bundle_path: Path | str,
    *,
    index_path: Path | str | None = None,
) -> dict[str, Any]:
    """Register a verified bundle and return the stored index entry."""
    bundle = Path(bundle_path)
    path = Path(index_path) if index_path is not None else default_index_path(bundle.parent)
    verification = verify_bundle(bundle)
    if not verification.get("valid"):
        errors = "; ".join(str(error) for error in verification.get("errors", []))
        raise ArtifactIndexError(f"cannot index invalid bundle {bundle}: {errors}")

    summary = inspect_bundle(bundle)
    application = dict(summary.get("application") or {})
    name = application.get("name")
    version = application.get("version")
    if not name or not version:
        raise ArtifactIndexError(f"bundle is missing application name/version: {bundle}")

    index = list_artifacts(path)
    index.pop("indexPath", None)
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "application": {"name": name, "version": version},
        "bundle": str(bundle),
        "graph": str(bundle / "metadata" / "graph.json"),
        "runtime": summary.get("runtime", {}).get("runner", {}),
        "launch": summary.get("launch", {}),
        "provenance": summary.get("provenance", {}),
        "verification": {
            "schemaVersion": verification.get("schemaVersion"),
            "valid": verification.get("valid"),
            "warnings": verification.get("warnings", []),
        },
        "registeredAt": now,
    }

    artifacts = index.setdefault("artifacts", {})
    versions = artifacts.setdefault(str(name), {})
    versions[str(version)] = entry
    index.setdefault("latest", {})[str(name)] = str(version)
    index["updatedAt"] = now
    _write_json(path, index)

    returned = dict(entry)
    returned["indexPath"] = str(path)
    return returned


def resolve_artifact(
    ref: str,
    *,
    index_path: Path | str | None = None,
) -> dict[str, Any]:
    """Resolve an artifact reference from the local index.

    References are either `name` (latest registered version) or `name@version`.
    """
    name, version = _parse_ref(ref)
    path = Path(index_path) if index_path is not None else default_index_path()
    index = list_artifacts(path)
    artifacts = index.get("artifacts", {})
    versions = artifacts.get(name)
    if not versions:
        raise ArtifactIndexError(f"artifact is not registered: {name}")
    if version is None:
        version = index.get("latest", {}).get(name)
    if not version or version not in versions:
        available = ", ".join(sorted(versions)) or "none"
        raise ArtifactIndexError(
            f"artifact version is not registered: {name}@{version or 'latest'}; available: {available}"
        )
    entry = dict(versions[version])
    entry["indexPath"] = str(path)
    return entry


def resolve_bundle_reference(
    value: str,
    *,
    index_path: Path | str | None = None,
) -> Path:
    """Resolve either an existing bundle path or an indexed app reference."""
    candidate = Path(value)
    if candidate.exists():
        return candidate
    entry = resolve_artifact(value, index_path=index_path)
    return Path(entry["bundle"])


def _parse_ref(ref: str) -> tuple[str, str | None]:
    if "@" in ref:
        name, version = ref.rsplit("@", 1)
        if not name or not version:
            raise ArtifactIndexError(f"invalid artifact reference: {ref}")
        return name, version
    return ref, None


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
