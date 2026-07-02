"""Media staging and source-policy audit helpers for WinForge."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

MEDIA_STAGE_SCHEMA_VERSION = "winforge.media-stage/v0"
SOURCE_POLICY_SCHEMA_VERSION = "winforge.source-policy/v0"

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class MediaStageError(ValueError):
    """Raised when media staging cannot safely proceed."""


@dataclass(frozen=True)
class SourcePolicyRule:
    id: str
    pattern: re.Pattern[str]
    reason: str
    severity: str = "blocked"


SOURCE_POLICY_RULES: tuple[SourcePolicyRule, ...] = (
    SourcePolicyRule(
        "activation-artifact",
        re.compile(r"activat|pre.?activ|ospp\.vbs|slmgr|tokens\.dat|rearm", re.IGNORECASE),
        "activation-related artifact name",
    ),
    SourcePolicyRule(
        "kms-artifact",
        re.compile(r"\bkms\b|vlmcs|autokms|kmsauto|microsoft[ _-]*toolkit|ez[ _-]*activator", re.IGNORECASE),
        "KMS/emulator-related artifact name",
    ),
    SourcePolicyRule(
        "crack-or-bypass-artifact",
        re.compile(r"crack|keygen|loader|bypass|patch.*activation", re.IGNORECASE),
        "crack/bypass-related artifact name",
    ),
    SourcePolicyRule(
        "product-key-artifact",
        re.compile(r"pidkey|product.?key", re.IGNORECASE),
        "product-key-related artifact name",
    ),
)


def stage_media(
    source: Path | str,
    *,
    name: str,
    workspace: Path | str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Stage local BYO media under ``<workspace>/sources/<name>/media``.

    Directory sources are copied without preserving read-only source modes.
    ZIP/TAR archives are extracted with path traversal checks. ISO extraction is
    quarantined and copied through the same no-symlink/no-traversal path when a
    host extractor such as bsdtar or 7z is available.
    """
    source_path = Path(source).expanduser()
    if not source_path.exists():
        raise MediaStageError(f"media source does not exist: {source_path}")
    source_path = source_path.resolve()
    safe_name = _safe_stage_name(name)
    workspace_path = Path(workspace or Path.cwd()).expanduser().resolve()
    sources_root = workspace_path / "sources"
    stage_root = sources_root / safe_name
    media_dir = stage_root / "media"
    metadata_dir = stage_root / "metadata"
    metadata_file = metadata_dir / "media-stage.json"
    temp_media_dir = stage_root / ".media-stage.tmp"

    try:
        _reject_recursive_source(source_path, stage_root)
        _reject_symlinked_staging_paths(workspace_path, sources_root, stage_root, media_dir, metadata_dir, metadata_file, temp_media_dir)
        sources_root.mkdir(parents=True, exist_ok=True)
        _reject_symlinked_staging_paths(workspace_path, sources_root, stage_root, media_dir, metadata_dir, metadata_file, temp_media_dir)
        stage_root.mkdir(parents=True, exist_ok=True)
        _reject_symlinked_staging_paths(workspace_path, sources_root, stage_root, media_dir, metadata_dir, metadata_file, temp_media_dir)

        if media_dir.exists():
            _reject_symlinks_in_tree(media_dir)
            if not overwrite and any(media_dir.iterdir()):
                raise MediaStageError(f"staged media already exists: {media_dir}; use --overwrite to replace it")
        if temp_media_dir.exists():
            _reject_symlinks_in_tree(temp_media_dir)
            _make_tree_user_writable(temp_media_dir)
            shutil.rmtree(temp_media_dir)
        temp_media_dir.mkdir(parents=True, exist_ok=False)

        source_kind = _stage_source(source_path, temp_media_dir)
        _reject_symlinks_in_tree(temp_media_dir)
        _make_tree_user_writable(temp_media_dir)
        if media_dir.exists():
            _make_tree_user_writable(media_dir)
            shutil.rmtree(media_dir)
        temp_media_dir.rename(media_dir)
        summary = summarize_tree(media_dir)
        result: dict[str, Any] = {
            "schemaVersion": MEDIA_STAGE_SCHEMA_VERSION,
            "success": True,
            "sourcePath": str(source_path),
            "sourceKind": source_kind,
            "workspace": str(workspace_path),
            "name": safe_name,
            "stagedPath": str(media_dir),
            "metadataPath": str(metadata_file),
            "summary": summary,
            "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        if source_path.is_file():
            result["sourceSha256"] = sha256_file(source_path)
        _reject_symlinked_staging_paths(workspace_path, sources_root, stage_root, metadata_dir, metadata_file)
        if metadata_dir.exists():
            _reject_symlinks_in_tree(metadata_dir)
        metadata_dir.mkdir(parents=True, exist_ok=True)
        _write_json_no_symlink(metadata_file, result)
        return result
    except MediaStageError:
        raise
    except (OSError, RuntimeError, NotImplementedError, subprocess.SubprocessError, zipfile.BadZipFile, tarfile.TarError) as exc:
        raise MediaStageError(f"failed to stage media from {source_path}: {exc}") from exc
    finally:
        if temp_media_dir.exists():
            _make_tree_user_writable(temp_media_dir)
            shutil.rmtree(temp_media_dir, ignore_errors=True)


def audit_source_path(path: Path | str, *, location: str | None = None) -> dict[str, Any]:
    """Audit a local source path for disallowed/suspicious artifact names.

    The audit intentionally inspects paths and file names only. It does not read
    file contents, which avoids copying product keys or other local secrets into
    report output.
    """
    source = Path(path).expanduser()
    findings: list[dict[str, Any]] = []
    errors: list[str] = []
    if not source.exists():
        errors.append(f"missing source path: {source}")
        candidates: list[Path] = []
    else:
        try:
            candidates = list(_iter_policy_paths(source))
        except MediaStageError as exc:
            errors.append(str(exc))
            candidates = []

    base = source if source.is_dir() else source.parent
    for candidate in candidates:
        display_path = source.name if candidate == source else _display_relative(candidate, base)
        normalized = display_path.replace(os.sep, "/")
        matched = _match_policy_rule(normalized)
        if matched is None:
            continue
        findings.append({
            "ruleId": matched.id,
            "severity": matched.severity,
            "path": normalized,
            "reason": matched.reason,
            **({"location": location} if location else {}),
        })

    blocked = sum(1 for finding in findings if finding["severity"] == "blocked")
    return {
        "schemaVersion": SOURCE_POLICY_SCHEMA_VERSION,
        "path": str(source),
        **({"location": location} if location else {}),
        "valid": not errors and blocked == 0,
        "summary": {
            "checked": len(candidates),
            "findings": len(findings),
            "blocked": blocked,
            "errors": len(errors),
        },
        "findings": findings,
        "errors": errors,
    }


def summarize_tree(root: Path | str, *, preview_limit: int = 100) -> dict[str, Any]:
    root_path = Path(root)
    file_count = 0
    directory_count = 0
    byte_size = 0
    preview: list[str] = []
    for path in _walk_tree_entries(root_path):
        rel = path.relative_to(root_path).as_posix()
        if path.is_dir():
            directory_count += 1
            continue
        file_count += 1
        byte_size += path.stat().st_size
        if len(preview) < preview_limit:
            preview.append(rel)
    return {
        "fileCount": file_count,
        "directoryCount": directory_count,
        "byteSize": byte_size,
        "preview": preview,
    }


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_stage_name(name: str) -> str:
    if not isinstance(name, str) or not _SAFE_NAME_RE.fullmatch(name):
        raise MediaStageError("media stage name must contain only letters, digits, '.', '_' or '-' and may not contain path separators")
    if name in {".", ".."}:
        raise MediaStageError("media stage name may not be '.' or '..'")
    return name


def _stage_source(source: Path, media_dir: Path) -> str:
    if source.is_dir():
        _copy_tree_contents(source, media_dir)
        return "directory"
    if zipfile.is_zipfile(source):
        _extract_zip(source, media_dir)
        return "zip"
    if tarfile.is_tarfile(source):
        _extract_tar(source, media_dir)
        return "tar"
    if source.suffix.lower() == ".iso":
        _extract_iso(source, media_dir)
        return "iso"
    target = _safe_destination(media_dir, source.name)
    shutil.copyfile(source, target)
    return "file"


def _copy_tree_contents(source: Path, destination: Path) -> None:
    for child in _walk_tree_entries(source):
        rel = child.relative_to(source)
        target = _safe_destination(destination, rel.as_posix())
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(child, target, follow_symlinks=False)


def _extract_zip(source: Path, destination: Path) -> None:
    with zipfile.ZipFile(source) as zf:
        for member in zf.infolist():
            target = _safe_destination(destination, member.filename)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            mode = member.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise MediaStageError(f"unsafe symlink in archive: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _extract_tar(source: Path, destination: Path) -> None:
    with tarfile.open(source) as tf:
        for member in tf.getmembers():
            target = _safe_destination(destination, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.issym() or member.islnk():
                raise MediaStageError(f"unsafe link in archive: {member.name}")
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)


def _extract_iso(source: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="winforge-iso-extract-") as tmpdir:
        quarantine = Path(tmpdir)
        bsdtar = shutil.which("bsdtar")
        try:
            if bsdtar:
                subprocess.run([bsdtar, "-C", str(quarantine), "-xf", str(source)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            else:
                seven_zip = shutil.which("7z") or shutil.which("7zz")
                if not seven_zip:
                    raise MediaStageError("ISO staging requires bsdtar or 7z/7zz on PATH")
                subprocess.run([seven_zip, "x", f"-o{quarantine}", str(source)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as exc:
            raise MediaStageError(f"ISO extraction failed for {source}: exit code {exc.returncode}") from exc
        _reject_symlinks_in_tree(quarantine)
        _copy_tree_contents(quarantine, destination)


def _safe_destination(root: Path, member_name: str) -> Path:
    normalized_name = member_name.replace("\\", "/")
    member_path = Path(normalized_name)
    if member_path.is_absolute() or ".." in member_path.parts:
        raise MediaStageError(f"unsafe path in media archive/source: {member_name}")
    target = (root / member_path).resolve()
    root_resolved = root.resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise MediaStageError(f"unsafe path in media archive/source: {member_name}") from exc
    return target


def _make_tree_user_writable(root: Path) -> None:
    if not root.exists():
        return
    for path in [root, *_walk_tree_entries(root)]:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            continue
        user_bits = stat.S_IRUSR | stat.S_IWUSR
        if stat.S_ISDIR(mode):
            user_bits |= stat.S_IXUSR
        try:
            os.chmod(path, mode | user_bits)
        except PermissionError:
            pass


def _reject_recursive_source(source: Path, stage_root: Path) -> None:
    if not source.is_dir():
        return
    try:
        stage_root.relative_to(source)
    except ValueError:
        return
    raise MediaStageError(f"media source would contain staged output: {source} -> {stage_root}")


def _reject_symlinked_staging_paths(workspace: Path, *paths: Path) -> None:
    for path in paths:
        if path == workspace:
            continue
        if path.exists() or path.is_symlink():
            try:
                path.relative_to(workspace)
            except ValueError as exc:
                raise MediaStageError(f"staging path escapes workspace: {path}") from exc
            if path.is_symlink():
                raise MediaStageError(f"staging path may not be a symlink: {path}")


def _reject_symlinks_in_tree(root: Path) -> None:
    _assert_supported_entry(root)
    for _ in _walk_tree_entries(root):
        pass


def _walk_tree_entries(root: Path) -> Iterable[Path]:
    _assert_supported_entry(root)

    def onerror(exc: OSError) -> None:
        filename = getattr(exc, "filename", root)
        raise MediaStageError(f"cannot access media path: {filename}: {exc.strerror or exc}")

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        current = Path(dirpath)
        for dirname in sorted(list(dirnames)):
            path = current / dirname
            _assert_supported_entry(path)
            yield path
        for filename in sorted(filenames):
            path = current / filename
            _assert_supported_entry(path)
            yield path


def _assert_supported_entry(path: Path) -> None:
    try:
        mode = path.lstat().st_mode
    except OSError as exc:
        raise MediaStageError(f"cannot access media path: {path}: {exc}") from exc
    if stat.S_ISLNK(mode):
        raise MediaStageError(f"staged media may not contain symlink: {path}")
    if stat.S_ISDIR(mode) or stat.S_ISREG(mode):
        return
    raise MediaStageError(f"unsupported media source entry: {path}")


def _write_json_no_symlink(path: Path, payload: dict[str, Any]) -> None:
    if path.is_symlink():
        raise MediaStageError(f"metadata path may not be a symlink: {path}")
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists() or tmp.is_symlink():
        if tmp.is_symlink():
            raise MediaStageError(f"metadata temp path may not be a symlink: {tmp}")
        tmp.unlink()
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if path.is_symlink():
        tmp.unlink(missing_ok=True)
        raise MediaStageError(f"metadata path may not be a symlink: {path}")
    tmp.replace(path)


def _iter_policy_paths(source: Path) -> Iterable[Path]:
    mode = source.lstat().st_mode
    if stat.S_ISLNK(mode):
        raise MediaStageError(f"source path may not be a symlink: {source}")
    if stat.S_ISREG(mode):
        yield source
        return
    if not stat.S_ISDIR(mode):
        raise MediaStageError(f"unsupported source path: {source}")
    yield source
    for path in _walk_tree_entries(source):
        if path.is_file():
            yield path


def _display_relative(path: Path, base: Path) -> str:
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def _match_policy_rule(path: str) -> SourcePolicyRule | None:
    for rule in SOURCE_POLICY_RULES:
        if rule.pattern.search(path):
            return rule
    return None
