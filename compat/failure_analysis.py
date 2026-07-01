"""Failure-analysis reports for Windows/Wine installer logs."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

FAILURE_ANALYSIS_SCHEMA_VERSION = "winforge.failure-analysis/v0"

_LOG_SUFFIXES = {".log", ".txt"}
_RETURN_VALUE_3_RE = re.compile(r"Return value 3", re.IGNORECASE)
_MSI_ERROR_RE = re.compile(r"MSI\(ERROR\)", re.IGNORECASE)
_FAILED_PRODUCT_MARKER_RE = re.compile(r"Failed to install product", re.IGNORECASE)
_ERROR_CODE_MARKER_RE = re.compile(r"\bErrorCode:\s*\d+", re.IGNORECASE)
_GENERIC_ERROR_RE = re.compile(r"\bError\s+\d{3,5}\b", re.IGNORECASE)
_RETURN_CODE_RE = re.compile(r"\bReturn code:\s*(\d+)", re.IGNORECASE)
_ALT_RETURN_CODE_RE = re.compile(r"\b(?:exit code|exitCode|rc)\s*[=:]\s*(\d+)", re.IGNORECASE)
_CHAINED_PACKAGE_RE = re.compile(r"Executing chained package:\s*([^\s,]+)", re.IGNORECASE)
_FAILED_PRODUCT_RE = re.compile(r"Failed to install product:\s*(?P<path>.*?)(?:\s+ErrorCode:\s*(?P<code>\d+))?\s*$", re.IGNORECASE)
_ROLLBACK_RE = re.compile(r"rolled back install of package:\s*([^\s]+)", re.IGNORECASE)
_ERROR_CODE_RE = re.compile(r"\bErrorCode:\s*(\d+)", re.IGNORECASE)
_PIDKEY_RE = re.compile(r"[A-Z0-9]{5}(?:-?[A-Z0-9]{5}){4}", re.IGNORECASE)
_SECRET_ASSIGNMENT_RE = re.compile(r"(?i)\b([A-Za-z0-9_-]*(?:password|passwd|token|secret|api[_-]?key|pidkey)[A-Za-z0-9_-]*)\s*[:=]\s*[^\s,;]+")
_COMMON_EXE_NAMES = {
    "WINWORD.EXE",
    "EXCEL.EXE",
    "POWERPNT.EXE",
    "OUTLOOK.EXE",
    "MSACCESS.EXE",
    "MSPUB.EXE",
    "ONENOTE.EXE",
}


class FailureAnalysisError(ValueError):
    """Raised when a failure report cannot be written safely."""


def analyze_failure_path(path: Path | str, *, write: bool = False) -> dict[str, Any]:
    """Analyze a bundle/log directory/file and optionally write report files."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"failure analysis path does not exist: {root}")
    bundle_root = _bundle_root(root)
    log_files = _collect_log_files(root)
    execution_return_code = _execution_result_exit_code(bundle_root)
    return_codes: list[int] = []
    rollback_packages: list[str] = []
    failure_windows: list[dict[str, Any]] = []
    first_failed_package: dict[str, Any] | None = None

    for log_file in log_files:
        lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        current_package: str | None = None
        for index, line in enumerate(lines):
            chained = _CHAINED_PACKAGE_RE.search(line)
            if chained:
                current_package = chained.group(1)
            for match in _RETURN_CODE_RE.finditer(line):
                return_codes.append(int(match.group(1)))
            for match in _ALT_RETURN_CODE_RE.finditer(line):
                return_codes.append(int(match.group(1)))
            rollback = _ROLLBACK_RE.search(line)
            if rollback:
                package = _redact_text(rollback.group(1))
                if package not in rollback_packages:
                    rollback_packages.append(package)
            failed = _FAILED_PRODUCT_RE.search(line)
            if failed and first_failed_package is None:
                failed_path = failed.group("path").strip()
                package_name = current_package or _package_name_from_path(failed_path)
                first_failed_package = {
                    "name": _redact_text(package_name),
                    "path": _redact_text(failed_path),
                }
                code = failed.group("code") or _line_error_code(line)
                if code is not None:
                    first_failed_package["errorCode"] = int(code)
            priority = _failure_marker_priority(line)
            if priority is not None:
                _append_failure_window(failure_windows, log_file, lines, index, priority=priority, bundle_root=bundle_root)

    failure_windows.sort(key=lambda item: (item.get("priority", 99), item.get("source", ""), item.get("startLine", 0)))
    top_level_return_code = execution_return_code if execution_return_code is not None else (return_codes[-1] if return_codes else None)
    failure_detected = bool(first_failed_package or failure_windows or (top_level_return_code is not None and top_level_return_code != 0))
    installed_executables = _find_installed_executables(bundle_root)
    classification = "windows-installer-failed" if failure_detected else "no-failure-detected"

    result: dict[str, Any] = {
        "schemaVersion": FAILURE_ANALYSIS_SCHEMA_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "path": _redact_text(str(root)),
        "bundle": _redact_text(str(bundle_root)) if bundle_root else None,
        "failureDetected": failure_detected,
        "classification": classification,
        "topLevelReturnCode": top_level_return_code,
        "firstFailedPackage": first_failed_package,
        "rollbackPackages": rollback_packages,
        "installedExecutables": installed_executables,
        "failureWindows": failure_windows,
        "summary": {
            "logsScanned": len(log_files),
            "returnCodes": return_codes,
            "executionReturnCode": execution_return_code,
            "rollbackPackages": len(rollback_packages),
            "failureWindows": len(failure_windows),
            "installedExecutables": len(installed_executables),
        },
    }
    if write and bundle_root:
        write_failure_analysis(bundle_root, result)
    return result


def write_failure_analysis(bundle: Path | str, analysis: dict[str, Any]) -> None:
    bundle_path = Path(bundle)
    metadata = _safe_metadata_dir(bundle_path)
    _write_text_no_symlink(metadata / "failure-analysis.json", json.dumps(analysis, indent=2, sort_keys=True) + "\n")
    _write_text_no_symlink(metadata / "failure-summary.md", _render_summary(analysis))


def _safe_metadata_dir(bundle: Path) -> Path:
    if bundle.is_symlink():
        raise FailureAnalysisError(f"bundle path may not be a symlink: {bundle}")
    bundle_resolved = bundle.resolve()
    metadata = bundle / "metadata"
    if metadata.exists() and metadata.is_symlink():
        raise FailureAnalysisError(f"metadata path may not be a symlink: {metadata}")
    metadata.mkdir(parents=True, exist_ok=True)
    metadata_resolved = metadata.resolve()
    try:
        metadata_resolved.relative_to(bundle_resolved)
    except ValueError as exc:
        raise FailureAnalysisError(f"metadata path escapes bundle: {metadata}") from exc
    return metadata


def _write_text_no_symlink(path: Path, text: str) -> None:
    if path.is_symlink():
        raise FailureAnalysisError(f"report path may not be a symlink: {path}")
    tmp = path.with_name(f".{path.name}.tmp")
    if tmp.exists() or tmp.is_symlink():
        if tmp.is_symlink():
            raise FailureAnalysisError(f"temporary report path may not be a symlink: {tmp}")
        tmp.unlink()
    tmp.write_text(text, encoding="utf-8")
    if path.is_symlink():
        tmp.unlink(missing_ok=True)
        raise FailureAnalysisError(f"report path may not be a symlink: {path}")
    tmp.replace(path)


def _bundle_root(path: Path) -> Path | None:
    if path.is_file():
        candidates = [path.parent, *path.parents]
    else:
        candidates = [path, *path.parents]
    for candidate in candidates:
        if (candidate / "metadata").exists() or (candidate / "prefix").exists() or (candidate / "logs").exists():
            return candidate
    return path if path.is_dir() else None


def _collect_log_files(path: Path) -> list[Path]:
    bundle = _bundle_root(path)
    safe_root = bundle.resolve() if bundle else None
    if path.is_file():
        return [path] if _safe_log_candidate(path, safe_root) else []
    roots = [path]
    if bundle and bundle != path:
        roots.append(bundle)
    candidates: set[Path] = set()
    for root in roots:
        for sub in _candidate_log_roots(root, safe_root=safe_root):
            if not sub.exists() or not _safe_log_candidate(sub, safe_root):
                continue
            if sub.is_file() and sub.suffix.lower() in _LOG_SUFFIXES:
                candidates.add(sub)
            elif sub.is_dir():
                for item in sub.rglob("*"):
                    if _safe_log_candidate(item, safe_root) and item.is_file() and item.suffix.lower() in _LOG_SUFFIXES:
                        candidates.add(item)
    return sorted(candidates)


def _safe_log_candidate(path: Path, safe_root: Path | None) -> bool:
    if path.is_symlink():
        return False
    if safe_root is None:
        return True
    try:
        path.resolve().relative_to(safe_root)
    except (OSError, ValueError):
        return False
    return True


def _candidate_log_roots(root: Path, *, safe_root: Path | None = None) -> Iterable[Path]:
    yield root / "logs"
    yield root / "prefix" / "drive_c" / "windows" / "temp"
    users = root / "prefix" / "drive_c" / "users"
    if users.exists() and _safe_log_candidate(users, safe_root):
        for user in users.iterdir():
            if _safe_log_candidate(user, safe_root):
                yield user / "Temp"
    yield root


def _execution_result_exit_code(bundle: Path | None) -> int | None:
    if not bundle:
        return None
    safe_root = bundle.resolve()
    metadata = bundle / "metadata"
    path = metadata / "execution-result.json"
    if metadata.is_symlink() or not _safe_log_candidate(metadata, safe_root):
        return None
    if not path.exists() or path.is_symlink() or not _safe_log_candidate(path, safe_root):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("exitCode")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _failure_marker_priority(line: str) -> int | None:
    if _FAILED_PRODUCT_MARKER_RE.search(line) or _RETURN_VALUE_3_RE.search(line) or _MSI_ERROR_RE.search(line):
        return 1
    if _ERROR_CODE_MARKER_RE.search(line):
        return 2
    if _GENERIC_ERROR_RE.search(line):
        return 3
    return None


def _append_failure_window(
    windows: list[dict[str, Any]],
    log_file: Path,
    lines: list[str],
    index: int,
    *,
    priority: int,
    bundle_root: Path | None,
) -> None:
    start = max(0, index - 5)
    end = min(len(lines), index + 7)
    source = _redact_text(_relative_or_name(log_file, bundle_root) if bundle_root else str(log_file))
    for window in windows:
        if (window["source"], window["startLine"], window["endLine"]) == (source, start + 1, end):
            window["priority"] = min(int(window.get("priority", priority)), priority)
            return
    windows.append({
        "source": source,
        "startLine": start + 1,
        "endLine": end,
        "priority": priority,
        "excerpt": [_redact_text(line) for line in lines[start:end]],
    })


def _line_error_code(line: str) -> str | None:
    match = _ERROR_CODE_RE.search(line)
    return match.group(1) if match else None


def _package_name_from_path(value: str) -> str:
    basename = value.replace("\\", "/").rstrip("/").split("/")[-1]
    if basename.lower().endswith(".msi"):
        return basename[:-4]
    return basename or value


def _find_installed_executables(bundle: Path | None) -> list[dict[str, str]]:
    if not bundle:
        return []
    safe_root = bundle.resolve()
    drive_c = bundle / "prefix" / "drive_c"
    if not drive_c.exists() or not _safe_log_candidate(drive_c, safe_root):
        return []
    results: list[dict[str, str]] = []
    for path in sorted(drive_c.rglob("*")):
        if not _safe_log_candidate(path, safe_root):
            continue
        if not path.is_file() or path.suffix.lower() != ".exe":
            continue
        if path.name.upper() not in _COMMON_EXE_NAMES:
            continue
        results.append({
            "name": _redact_text(path.name.upper()),
            "path": _redact_text(path.relative_to(drive_c).as_posix()),
        })
    return results


def _relative_or_name(path: Path, root: Path | None) -> str:
    if root:
        try:
            return path.relative_to(root).as_posix()
        except ValueError:
            pass
    return str(path)


def _redact_text(text: str) -> str:
    redacted = _PIDKEY_RE.sub("[REDACTED-PIDKEY]", text)
    return _SECRET_ASSIGNMENT_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", redacted)


def _render_summary(analysis: dict[str, Any]) -> str:
    failed = analysis.get("firstFailedPackage") or {}
    lines = [
        "# WinForge failure summary",
        "",
        f"Classification: `{analysis.get('classification')}`",
        f"Failure detected: `{analysis.get('failureDetected')}`",
        f"Top-level return code: `{analysis.get('topLevelReturnCode')}`",
        "",
        "## First failed package",
        "",
    ]
    if failed:
        lines.extend([
            f"- Name: `{_redact_text(str(failed.get('name')))}`",
            f"- Path: `{_redact_text(str(failed.get('path')))}`",
            f"- Error code: `{failed.get('errorCode')}`",
        ])
    else:
        lines.append("No failed package detected.")
    lines.extend(["", "## Rollback packages", ""])
    rollback = analysis.get("rollbackPackages") or []
    if rollback:
        lines.extend(f"- `{_redact_text(str(item))}`" for item in rollback)
    else:
        lines.append("None detected.")
    lines.extend(["", "## Failure windows", ""])
    for index, window in enumerate(analysis.get("failureWindows") or [], start=1):
        lines.append(f"### Window {index}: `{_redact_text(str(window.get('source')))}` lines {window.get('startLine')}-{window.get('endLine')}")
        lines.append("")
        lines.append("```text")
        lines.extend(_redact_text(str(line)) for line in window.get("excerpt") or [])
        lines.append("```")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "FAILURE_ANALYSIS_SCHEMA_VERSION",
    "FailureAnalysisError",
    "analyze_failure_path",
    "write_failure_analysis",
]
