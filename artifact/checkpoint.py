"""Prepared-prefix checkpoint inspection and resume helpers."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
import shutil
from pathlib import Path
from typing import Any

CHECKPOINT_SCHEMA_VERSION = "winforge.checkpoint/v0"
CHECKPOINT_RESUME_SCHEMA_VERSION = "winforge.checkpoint-resume/v0"

REQUIRED_CHECKPOINT_FILES = [
    "prefix/drive_c",
    "manifest.winforge.json",
    "runtime/runtime.json",
    "metadata/provenance.json",
    "logs/build.log",
]


class CheckpointError(RuntimeError):
    """Raised when a checkpoint cannot be inspected or resumed safely."""


def inspect_checkpoint(path: Path | str) -> dict[str, Any]:
    """Inspect *path* as either a checkpoint bundle or an output parent.

    A prepared-prefix checkpoint is a normal WinForge bundle with enough state
    to seed a later attempt: prefix/drive_c plus manifest, runtime,
    provenance, and build log metadata. When the caller points at a compat-test
    output directory, this function locates the nested bundle root instead of
    pretending the parent is directly resumable.
    """
    input_path = Path(path).expanduser()
    try:
        _reject_symlinked_existing_components(input_path, label="checkpoint path")
    except CheckpointError as exc:
        return _invalid(input_path, [str(exc)])
    try:
        resolved = input_path.resolve()
    except OSError as exc:
        return _invalid(input_path, [f"checkpoint path cannot be resolved: {exc}"])

    if not resolved.exists() or not resolved.is_dir():
        return _invalid(resolved, [f"checkpoint path is missing or not a directory: {resolved}"])

    direct = _summarize_candidate(resolved, input_path=resolved, input_kind="bundle")
    if direct["valid"]:
        direct["candidates"] = [direct["bundle"]]
        return direct
    if _looks_like_checkpoint_bundle(resolved):
        direct["candidates"] = []
        return direct

    candidates = []
    for manifest_path in sorted(resolved.rglob("manifest.winforge.json")):
        candidate = manifest_path.parent
        if candidate == resolved or _has_symlink_ancestor(candidate, stop_at=resolved):
            continue
        summary = _summarize_candidate(candidate, input_path=resolved, input_kind="output-parent")
        if summary["valid"]:
            candidates.append(summary)

    if len(candidates) == 1:
        result = dict(candidates[0])
        result["inputPath"] = str(resolved)
        result["inputKind"] = "output-parent"
        result["candidates"] = [item["bundle"] for item in candidates]
        return result
    if len(candidates) > 1:
        return _invalid(
            resolved,
            ["multiple valid checkpoint bundles found; pass one bundle path explicitly"],
            input_kind="output-parent",
            candidates=[item["bundle"] for item in candidates],
        )
    return _invalid(resolved, ["no valid checkpoint bundle found"], input_kind="output-parent", candidates=[])


def resume_checkpoint(
    checkpoint_path: Path | str,
    *,
    output_dir: Path | str,
    name: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Copy a checkpoint bundle into a fresh mutable attempt directory."""
    source = _valid_checkpoint_bundle(checkpoint_path)
    output_input = Path(output_dir).expanduser()
    _reject_symlinked_existing_components(output_input)
    output = output_input.resolve()
    attempt_name = _safe_attempt_name(name or f"{source.name}-attempt")
    attempt_path = output / attempt_name
    if attempt_path.is_symlink():
        raise CheckpointError(f"attempt path must not be a symlink: {attempt_path}")
    attempt_resolved = attempt_path.resolve()
    _reject_attempt_outside_output(output, attempt_resolved)
    _reject_recursive_copy(source, attempt_resolved)
    output.mkdir(parents=True, exist_ok=True)
    if attempt_path.exists():
        if not overwrite:
            raise CheckpointError(f"attempt bundle already exists: {attempt_path}")
        _safe_rmtree(attempt_path)
    fresh_attempt = not attempt_path.exists()
    _copytree_preserving_symlinks(source, attempt_path)
    try:
        return _write_resume_metadata(source, attempt_path.resolve(), name=attempt_name, mode="bundle-copy")
    except CheckpointError:
        if fresh_attempt and attempt_path.exists() and not attempt_path.is_symlink():
            _safe_rmtree(attempt_path)
        raise


def seed_bundle_from_checkpoint(checkpoint_path: Path | str, attempt_bundle: Path | str) -> dict[str, Any]:
    """Seed an already-created attempt bundle with a checkpoint prefix.

    This preserves the attempt bundle's manifest/runtime/graph metadata while
    replacing its placeholder prefix with the prepared prefix from the source
    checkpoint. That lets `compat test --resume-from-bundle` use the current
    recipe metadata while reusing slow dependency/prefix work.
    """
    source = _valid_checkpoint_bundle(checkpoint_path)
    attempt_input = Path(attempt_bundle).expanduser()
    _reject_symlinked_existing_components(attempt_input, label="attempt bundle")
    if attempt_input.is_symlink():
        raise CheckpointError(f"attempt bundle root must not be a symlink: {attempt_input}")
    attempt = attempt_input.resolve()
    if not attempt.exists() or not attempt.is_dir():
        raise CheckpointError(f"attempt bundle is missing or not a directory: {attempt}")
    _reject_recursive_copy(source, attempt)
    source_prefix = source / "prefix"
    dest_prefix = attempt / "prefix"
    if not source_prefix.exists() or not source_prefix.is_dir():
        raise CheckpointError(f"checkpoint prefix is missing: {source_prefix}")
    _safe_rmtree(dest_prefix)
    _copytree_preserving_symlinks(source_prefix, dest_prefix)
    return _write_resume_metadata(source, attempt, name=attempt.name, mode="prefix-seed")


def _valid_checkpoint_bundle(path: Path | str) -> Path:
    result = inspect_checkpoint(path)
    if not result.get("valid"):
        raise CheckpointError("invalid checkpoint: " + "; ".join(result.get("errors") or []))
    return Path(str(result["bundle"])).resolve()


def _summarize_candidate(bundle: Path, *, input_path: Path, input_kind: str) -> dict[str, Any]:
    errors: list[str] = []
    files = {rel: _file_summary(bundle / rel) for rel in REQUIRED_CHECKPOINT_FILES}
    for rel, summary in files.items():
        if not summary["exists"]:
            errors.append(f"missing required checkpoint file: {rel}")
    structural_symlink_dirs = {
        structural_dir
        for structural_dir in ["prefix", "runtime", "metadata", "logs", "build", "launch"]
        if (bundle / structural_dir).is_symlink()
    }
    for structural_dir in sorted(structural_symlink_dirs):
        errors.append(f"checkpoint {structural_dir} must not be a symlink")
    if files["prefix/drive_c"]["type"] == "symlink":
        errors.append("checkpoint prefix/drive_c must not be a symlink")
    elif files["prefix/drive_c"]["exists"] and files["prefix/drive_c"]["type"] != "directory":
        errors.append("checkpoint prefix/drive_c must be a directory")
    for rel in ["manifest.winforge.json", "runtime/runtime.json", "metadata/provenance.json", "logs/build.log"]:
        if files[rel]["type"] == "symlink":
            errors.append(f"checkpoint {rel} must not be a symlink")
        elif files[rel]["exists"] and files[rel]["type"] != "file":
            errors.append(f"checkpoint {rel} must be a file")

    manifest: dict[str, Any] = {}
    runtime: dict[str, Any] = {}
    provenance: dict[str, Any] = {}
    json_errors: list[str] = []
    if files["manifest.winforge.json"]["type"] == "file":
        manifest, manifest_error = _load_json(bundle / "manifest.winforge.json")
        if manifest_error:
            json_errors.append(manifest_error)
    if files["runtime/runtime.json"]["type"] == "file" and "runtime" not in structural_symlink_dirs:
        runtime, runtime_error = _load_json(bundle / "runtime" / "runtime.json")
        if runtime_error:
            json_errors.append(runtime_error)
    if files["metadata/provenance.json"]["type"] == "file" and "metadata" not in structural_symlink_dirs:
        provenance, provenance_error = _load_json(bundle / "metadata" / "provenance.json")
        if provenance_error:
            json_errors.append(provenance_error)
    errors.extend(json_errors)

    return {
        "schemaVersion": CHECKPOINT_SCHEMA_VERSION,
        "inputPath": str(input_path),
        "inputKind": input_kind,
        "bundle": str(bundle.resolve()),
        "valid": not errors,
        "errors": errors,
        "warnings": [],
        "application": {
            "name": manifest.get("name"),
            "version": manifest.get("version"),
        },
        "runtime": runtime,
        "provenance": {
            "schemaVersion": provenance.get("schemaVersion"),
            "dryRun": provenance.get("dryRun"),
            "createdAt": provenance.get("createdAt"),
        },
        "files": files,
    }


def _invalid(
    path: Path,
    errors: list[str],
    *,
    input_kind: str = "invalid",
    candidates: list[str] | None = None,
) -> dict[str, Any]:
    resolved = str(path)
    return {
        "schemaVersion": CHECKPOINT_SCHEMA_VERSION,
        "inputPath": resolved,
        "inputKind": input_kind,
        "bundle": None,
        "valid": False,
        "errors": errors,
        "warnings": [],
        "application": {},
        "runtime": {},
        "provenance": {},
        "files": {},
        "candidates": candidates or [],
    }


def _looks_like_checkpoint_bundle(path: Path) -> bool:
    return any((path / rel).exists() for rel in REQUIRED_CHECKPOINT_FILES)


def _load_json(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists() or not path.is_file():
        return {}, None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return {}, f"invalid JSON in {path.relative_to(path.parents[1]) if len(path.parents) > 1 else path}: {exc}"
    if not isinstance(payload, dict):
        return {}, f"JSON file must contain an object: {path}"
    return payload, None


def _file_summary(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        return {"path": str(path), "exists": True, "type": "symlink", "size": None}
    exists = path.exists()
    if exists and path.is_dir():
        kind = "directory"
    elif exists and path.is_file():
        kind = "file"
    else:
        kind = None
    return {
        "path": str(path),
        "exists": exists,
        "type": kind,
        "size": path.stat().st_size if exists and path.is_file() else None,
    }


def _copytree_preserving_symlinks(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise CheckpointError(f"checkpoint path must not be a symlink: {source}")
    shutil.copytree(source, destination, symlinks=True)


def _has_symlink_ancestor(path: Path, *, stop_at: Path) -> bool:
    current = path
    stop = stop_at.resolve()
    while current != stop and current != current.parent:
        if current.is_symlink():
            return True
        current = current.parent
    return False


def _safe_rmtree(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink():
        raise CheckpointError(f"refusing to remove symlink path: {path}")
    if not path.is_dir():
        raise CheckpointError(f"refusing to remove non-directory attempt path: {path}")
    shutil.rmtree(path)


def _reject_recursive_copy(source: Path, attempt: Path) -> None:
    source = source.resolve()
    attempt = attempt.resolve()
    if source == attempt:
        raise CheckpointError("attempt path must be different from checkpoint source")
    if attempt.is_relative_to(source):
        raise CheckpointError("attempt path must not be inside checkpoint source")
    if source.is_relative_to(attempt):
        raise CheckpointError("checkpoint source must not be inside attempt path")


def _write_resume_metadata(source: Path, attempt: Path, *, name: str, mode: str) -> dict[str, Any]:
    metadata = {
        "schemaVersion": CHECKPOINT_RESUME_SCHEMA_VERSION,
        "name": name,
        "mode": mode,
        "sourceBundle": str(source.resolve()),
        "attemptBundle": str(attempt.resolve()),
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    metadata_dir = attempt / "metadata"
    if metadata_dir.is_symlink():
        raise CheckpointError(f"refusing to write through symlink metadata directory: {metadata_dir}")
    metadata_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = metadata_dir / "checkpoint-resume.json"
    if metadata_path.is_symlink():
        raise CheckpointError(f"refusing to overwrite symlink metadata path: {metadata_path}")
    if metadata_path.exists() and not metadata_path.is_file():
        raise CheckpointError(f"refusing to overwrite non-file metadata path: {metadata_path}")
    tmp = metadata_path.with_name(metadata_path.name + ".tmp")
    if tmp.is_symlink():
        raise CheckpointError(f"refusing to overwrite symlink metadata temp path: {tmp}")
    if tmp.exists() and not tmp.is_file():
        raise CheckpointError(f"refusing to overwrite non-file metadata temp path: {tmp}")
    tmp.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(metadata_path)
    return dict(metadata)


def _reject_symlinked_existing_components(path: Path, *, label: str = "output path") -> None:
    candidates = [path, *path.parents]
    for candidate in candidates:
        if candidate.exists() or candidate.is_symlink():
            if candidate.is_symlink():
                raise CheckpointError(f"{label} must not contain symlink components: {candidate}")
            if not candidate.is_dir():
                raise CheckpointError(f"{label} must not contain non-directory components: {candidate}")


def _reject_attempt_outside_output(output: Path, attempt: Path) -> None:
    output = output.resolve()
    attempt = attempt.resolve()
    if not attempt.is_relative_to(output) or attempt.parent != output:
        raise CheckpointError("attempt path must be a direct child of the output directory")


def _safe_attempt_name(value: str) -> str:
    name = _safe_name(value)
    if name in {".", ".."}:
        raise CheckpointError("attempt name must not be . or ..")
    if Path(name).is_absolute() or any(part in {".", ".."} for part in Path(name).parts):
        raise CheckpointError("attempt name must be a single safe path segment")
    return name


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-_") or "checkpoint-attempt"
