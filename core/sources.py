"""Source integrity checks for WinForge recipes."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from core.manifest import Manifest

SOURCE_INTEGRITY_SCHEMA_VERSION = "winforge.source-integrity/v0"
_SHA256_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
_REMOTE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def verify_manifest_sources(manifest: Manifest, *, workspace: Path | str | None = None) -> dict[str, Any]:
    """Verify local source presence and hashes for a manifest.

    v0 build execution consumes local files from the workspace mount. Remote
    URLs are recorded as source declarations but are not downloaded here.
    """
    workspace_path = Path(workspace or Path.cwd()).resolve()
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []

    def add_item(
        *,
        location: str,
        usage: str,
        source: str | None,
        expected_sha256: str | None = None,
        require_local: bool = True,
    ) -> None:
        if not source:
            error = f"{location}: source is missing"
            item = {
                "location": location,
                "usage": usage,
                "status": "missing-source",
                "valid": False,
                "error": error,
            }
            items.append(item)
            errors.append(error)
            return

        item: dict[str, Any] = {
            "location": location,
            "usage": usage,
            "source": source,
            "valid": True,
        }
        sha_error = _validate_expected_sha(location, expected_sha256)
        if expected_sha256:
            item["expectedSha256"] = expected_sha256
        if sha_error:
            item["valid"] = False
            item["status"] = "invalid-sha256"
            item["error"] = sha_error
            items.append(item)
            errors.append(sha_error)
            return

        if is_remote_source(source):
            item["status"] = "remote"
            item["verified"] = False
            if require_local:
                error = f"{location}: remote source must be materialized locally for v0 build: {source}"
                item["valid"] = False
                item["error"] = error
                errors.append(error)
            else:
                warning = f"{location}: remote source was not fetched by source verifier: {source}"
                item["warning"] = warning
                warnings.append(warning)
            items.append(item)
            return

        resolved = resolve_source_path(source, workspace_path)
        item["resolvedPath"] = str(resolved)
        item["exists"] = resolved.exists()
        if not resolved.exists():
            error = f"{location}: missing local source: {resolved}"
            item["valid"] = False
            item["status"] = "missing"
            item["error"] = error
            items.append(item)
            errors.append(error)
            return

        if expected_sha256:
            if not resolved.is_file():
                error = f"{location}: sha256 verification requires a file source: {resolved}"
                item["valid"] = False
                item["status"] = "unsupported-directory-hash"
                item["error"] = error
                items.append(item)
                errors.append(error)
                return
            actual = sha256_file(resolved)
            item["sha256"] = actual
            if actual.lower() != expected_sha256.lower():
                error = f"{location}: sha256 mismatch for {resolved}: expected {expected_sha256}, got {actual}"
                item["valid"] = False
                item["status"] = "hash-mismatch"
                item["error"] = error
                items.append(item)
                errors.append(error)
                return
            item["status"] = "verified"
            item["verified"] = True
        else:
            item["status"] = "present"
            item["verified"] = False
            warning = f"{location}: source is present but no sha256 was declared"
            item["warning"] = warning
            warnings.append(warning)
        items.append(item)

    for index, source in enumerate(manifest.sources):
        if not isinstance(source, dict):
            error = f"sources[{index}]: source declaration must be an object"
            items.append({"location": f"sources[{index}]", "usage": "declared-source", "status": "invalid", "valid": False, "error": error})
            errors.append(error)
            continue
        ref = source.get("url") or source.get("source") or source.get("path")
        add_item(
            location=f"sources[{index}]",
            usage="declared-source",
            source=str(ref) if ref is not None else None,
            expected_sha256=source.get("sha256"),
            require_local=False,
        )

    for index, step in enumerate(manifest.install):
        if step.source:
            add_item(
                location=f"install[{index}].source",
                usage=f"install:{step.kind}",
                source=step.source,
                expected_sha256=step.sha256,
                require_local=True,
            )

    for index, mapping in enumerate(manifest.filesystem):
        add_item(
            location=f"filesystem[{index}].source",
            usage="filesystem",
            source=mapping.source,
            expected_sha256=mapping.sha256,
            require_local=True,
        )

    summary = {
        "checked": len(items),
        "local": sum(1 for item in items if item.get("resolvedPath")),
        "remote": sum(1 for item in items if item.get("status") == "remote"),
        "missing": sum(1 for item in items if item.get("status") == "missing"),
        "verified": sum(1 for item in items if item.get("status") == "verified"),
        "warnings": len(warnings),
        "errors": len(errors),
    }
    return {
        "schemaVersion": SOURCE_INTEGRITY_SCHEMA_VERSION,
        "workspace": str(workspace_path),
        "valid": not errors,
        "summary": summary,
        "items": items,
        "errors": errors,
        "warnings": warnings,
    }


def is_remote_source(source: str) -> bool:
    return bool(_REMOTE_RE.match(source)) and not source.startswith("file://")


def strip_file_scheme(source: str) -> str:
    return source[len("file://"):] if source.startswith("file://") else source


def resolve_source_path(source: str, workspace: Path | str | None = None) -> Path:
    raw = strip_file_scheme(source)
    path = Path(raw)
    if path.is_absolute():
        return path
    return Path(workspace or Path.cwd()).resolve() / path


def container_source_path(source: str, *, workspace_mount: str = "/workspace") -> str:
    """Return the path a source reference should use inside the build container."""
    if is_remote_source(source):
        return source
    raw = strip_file_scheme(source)
    if Path(raw).is_absolute():
        return raw
    return f"{workspace_mount.rstrip('/')}/{raw}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_expected_sha(location: str, expected_sha256: str | None) -> str | None:
    if expected_sha256 is None:
        return None
    if not isinstance(expected_sha256, str) or not _SHA256_RE.fullmatch(expected_sha256):
        return f"{location}: sha256 must be 64 hexadecimal characters"
    return None
