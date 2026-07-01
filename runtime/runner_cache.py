"""Download/cache/diagnose Wine runner archives for WinForge."""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from runtime.runner_catalog import RunnerSpec, resolve_runner_spec

RUNNER_CACHE_SCHEMA_VERSION = "winforge.runner-cache/v0"
RUNNER_DIAGNOSTIC_SCHEMA_VERSION = "winforge.runner-diagnostic/v0"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "winforge" / "runners"


class RunnerCacheError(RuntimeError):
    pass


def ensure_runner(spec_or_id: RunnerSpec | str, *, cache_dir: Path | str | None = None) -> dict[str, Any]:
    """Ensure a runner archive is extracted into the cache and return provenance."""
    spec = resolve_runner_spec(spec_or_id) if isinstance(spec_or_id, str) else spec_or_id
    root = Path(cache_dir or DEFAULT_CACHE_DIR).expanduser().resolve()
    runner_dir = root / spec.id
    archive_dir = root / "_archives"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / spec.filename

    status = "present" if (runner_dir / "bin" / "wine").exists() else "installed"
    downloaded = False
    if status == "installed":
        if not archive_path.exists():
            _download(spec.url, archive_path)
            downloaded = True
        actual_sha = sha256_file(archive_path)
        if spec.sha256 and actual_sha.lower() != spec.sha256.lower():
            archive_path.unlink(missing_ok=True)
            raise RunnerCacheError(
                f"sha256 mismatch for {spec.url}: expected {spec.sha256}, got {actual_sha}"
            )
        _extract_archive(archive_path, runner_dir, strip_components=spec.strip_components)
    else:
        actual_sha = sha256_file(archive_path) if archive_path.exists() else None

    diagnostic = diagnose_runner(runner_dir)
    archive_payload = {
        "sourceUrl": spec.url,
        "path": str(archive_path) if archive_path.exists() else None,
        "sha256": actual_sha,
        "bytes": archive_path.stat().st_size if archive_path.exists() else None,
        "downloaded": downloaded,
    }
    archive_payload = {k: v for k, v in archive_payload.items() if v is not None}
    return {
        "schemaVersion": RUNNER_CACHE_SCHEMA_VERSION,
        "status": status,
        "cacheDir": str(root),
        "runnerDir": str(runner_dir),
        "winePath": str(runner_dir / "bin" / "wine"),
        "runner": spec.to_dict(),
        "archive": archive_payload,
        "diagnostic": diagnostic,
    }


def diagnose_runner(runner_path: Path | str) -> dict[str, Any]:
    """Return a structured diagnostic for a runner directory or wine binary."""
    path = Path(runner_path).expanduser().resolve()
    wine = path if path.is_file() else path / "bin" / "wine"
    result: dict[str, Any] = {
        "schemaVersion": RUNNER_DIAGNOSTIC_SCHEMA_VERSION,
        "runnerPath": str(path),
        "winePath": str(wine),
        "exists": wine.exists(),
        "executable": False,
    }
    if not wine.exists():
        result.update({
            "status": "missing-wine-binary",
            "recommendation": "Ensure the runner archive was extracted with its bin/wine executable present.",
        })
        return result

    mode = wine.stat().st_mode
    result["mode"] = oct(stat.S_IMODE(mode))
    result["hasExecuteBit"] = bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
    if not result["hasExecuteBit"]:
        result.update({
            "status": "not-executable",
            "recommendation": "Set the executable bit on bin/wine or re-extract the runner archive.",
        })
        return result

    elf = read_elf_interpreter(wine)
    if elf is not None:
        result["elf"] = elf
        if elf.get("interpreter") and not elf.get("interpreterExists"):
            result.update({
                "status": "missing-elf-interpreter",
                "recommendation": (
                    "Install the host/runtime 32-bit compatibility libraries that provide "
                    f"{elf['interpreter']} (for example glibc.i686 on Fedora/myOS), or run this "
                    "runner inside a container image that carries the required 32-bit loader."
                ),
            })
            return result

    script_interpreter = read_script_interpreter(wine)
    if script_interpreter is not None:
        result["script"] = {
            "interpreter": script_interpreter,
            "interpreterExists": Path(script_interpreter).exists(),
        }
        if not Path(script_interpreter).exists():
            result.update({
                "status": "missing-script-interpreter",
                "recommendation": f"Install or correct the script interpreter: {script_interpreter}",
            })
            return result

    try:
        proc = subprocess.run(
            [str(wine), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except FileNotFoundError as exc:
        result.update({
            "status": "exec-file-not-found",
            "error": str(exc),
            "recommendation": (
                "The executable exists but the kernel could not start it. This usually means a "
                "missing ELF loader or script interpreter; run diagnostics inside the target runtime."
            ),
        })
        return result
    except OSError as exc:
        result.update({
            "status": "exec-error",
            "error": str(exc),
            "recommendation": "Install the runner's required host libraries or use a matching runtime container.",
        })
        return result
    except subprocess.TimeoutExpired:
        result.update({
            "status": "timeout",
            "recommendation": "The runner did not return from wine --version within 10 seconds.",
        })
        return result

    result["versionProbe"] = {
        "exitCode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }
    if proc.returncode == 0:
        result.update({"status": "ok", "executable": True})
    else:
        result.update({
            "status": "version-probe-failed",
            "recommendation": "Review stderr and install missing libraries before using this runner.",
        })
    return result


def read_script_interpreter(path: Path) -> str | None:
    with path.open("rb") as handle:
        first = handle.readline(512)
    if not first.startswith(b"#!"):
        return None
    line = first[2:].decode("utf-8", errors="replace").strip()
    return line.split()[0] if line else None


def read_elf_interpreter(path: Path) -> dict[str, Any] | None:
    with path.open("rb") as handle:
        ident = handle.read(16)
        if len(ident) < 16 or not ident.startswith(b"\x7fELF"):
            return None
        elf_class = ident[4]
        data_encoding = ident[5]
        endian = "<" if data_encoding == 1 else ">" if data_encoding == 2 else None
        if endian is None:
            return {"class": elf_class, "error": "unknown ELF data encoding"}
        if elf_class == 1:
            rest = handle.read(36)
            if len(rest) < 36:
                return {"class": "ELF32", "error": "truncated ELF header"}
            header = struct.unpack(endian + "HHIIIIIHHHHHH", rest)
            e_phoff = header[4]
            e_phentsize = header[8]
            e_phnum = header[9]
            ph_fmt = endian + "IIIIIIII"
            ph_size = struct.calcsize(ph_fmt)
            offset_index = 1
            filesz_index = 4
            elf_class_name = "ELF32"
        elif elf_class == 2:
            rest = handle.read(48)
            if len(rest) < 48:
                return {"class": "ELF64", "error": "truncated ELF header"}
            header = struct.unpack(endian + "HHIQQQIHHHHHH", rest)
            e_phoff = header[4]
            e_phentsize = header[8]
            e_phnum = header[9]
            ph_fmt = endian + "IIQQQQQQ"
            ph_size = struct.calcsize(ph_fmt)
            offset_index = 2
            filesz_index = 5
            elf_class_name = "ELF64"
        else:
            return {"class": elf_class, "error": "unsupported ELF class"}

        for index in range(e_phnum):
            handle.seek(e_phoff + index * e_phentsize)
            raw = handle.read(max(e_phentsize, ph_size))[:ph_size]
            if len(raw) < ph_size:
                break
            ph = struct.unpack(ph_fmt, raw)
            if ph[0] != 3:  # PT_INTERP
                continue
            interp_offset = int(ph[offset_index])
            interp_size = int(ph[filesz_index])
            handle.seek(interp_offset)
            data = handle.read(interp_size)
            interpreter = data.split(b"\x00", 1)[0].decode("utf-8", errors="replace")
            return {
                "class": elf_class_name,
                "interpreter": interpreter,
                "interpreterExists": Path(interpreter).exists() if interpreter else False,
            }
    return {"class": "ELF32" if elf_class == 1 else "ELF64", "interpreter": None, "interpreterExists": False}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(url)
    if parsed.scheme == "file":
        src = Path(parsed.path)
        shutil.copyfile(src, destination)
        return
    with urlopen(url, timeout=120) as response, destination.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def _extract_archive(archive_path: Path, runner_dir: Path, *, strip_components: int) -> None:
    runner_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=str(runner_dir.parent)) as tmpdir:
        tmp = Path(tmpdir) / "extract"
        tmp.mkdir()
        with tarfile.open(archive_path, "r:gz") as tar:
            for member in tar.getmembers():
                parts = _stripped_member_parts(member.name, strip_components)
                if not parts:
                    continue
                target = tmp.joinpath(*parts)
                try:
                    target.resolve().relative_to(tmp.resolve())
                except ValueError as exc:
                    raise RunnerCacheError(f"unsafe path in runner archive: {member.name}") from exc
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.issym():
                    link_target = Path(member.linkname)
                    if link_target.is_absolute() or ".." in link_target.parts:
                        raise RunnerCacheError(f"unsafe symlink in runner archive: {member.name} -> {member.linkname}")
                    target.unlink(missing_ok=True)
                    os.symlink(member.linkname, target)
                    continue
                if member.islnk():
                    link_parts = _stripped_member_parts(member.linkname, strip_components)
                    if not link_parts or ".." in link_parts:
                        raise RunnerCacheError(f"unsafe hardlink in runner archive: {member.name} -> {member.linkname}")
                    hardlink_target = tmp.joinpath(*link_parts)
                    if not hardlink_target.exists():
                        raise RunnerCacheError(f"hardlink target missing in runner archive: {member.name} -> {member.linkname}")
                    if hardlink_target.is_dir():
                        raise RunnerCacheError(f"hardlink target is a directory in runner archive: {member.name}")
                    shutil.copyfile(hardlink_target, target)
                    os.chmod(target, member.mode)
                    continue
                source = tar.extractfile(member)
                if source is None:
                    continue
                with source, target.open("wb") as handle:
                    shutil.copyfileobj(source, handle)
                os.chmod(target, member.mode)
        if runner_dir.exists():
            shutil.rmtree(runner_dir)
        shutil.move(str(tmp), str(runner_dir))


def _stripped_member_parts(name: str, strip_components: int) -> tuple[str, ...]:
    # GNU tar --strip-components counts a leading `.` component in paths like
    # `./bin/wine`. pathlib drops that component, so use string splitting here
    # to match tar semantics for PlayOnLinux/Phoenicis runner archives.
    raw = [part for part in name.split('/') if part != '']
    parts = raw[strip_components:]
    parts = [part for part in parts if part != '.']
    if any(part == '..' for part in parts):
        raise RunnerCacheError(f"unsafe path in runner archive: {name}")
    return tuple(parts)
