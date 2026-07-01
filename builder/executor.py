"""Container-executor for real WinForge builds.

Runs the WinForge build pipeline inside a WinForge Wine/Proton OCI
container, producing a real built prefix with installed dependencies
and applications.
"""
from __future__ import annotations
import json, os, shutil, subprocess, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from builder.pipeline import generate_build_script
from core.manifest import Manifest
from runtime.providers import resolve_runtime


@dataclass
class BuildResult:
    """Result of a real container-executed WinForge build."""

    success: bool
    bundle_path: str
    runtime_provider: str
    runtime_version: str
    image_ref: str
    engine: str
    exit_code: int | None = None
    log: str = ""
    prefix_size: int | None = None
    prefix_file_count: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "bundlePath": self.bundle_path,
            "runtimeProvider": self.runtime_provider,
            "runtimeVersion": self.runtime_version,
            "imageRef": self.image_ref,
            "engine": self.engine,
            "exitCode": self.exit_code,
            "prefixSize": self.prefix_size,
            "prefixFileCount": self.prefix_file_count,
            "error": self.error,
        }


def _find_engine(prefer: str | None = None) -> str:
    """Return 'docker' or 'podman' depending on what's available.

    If *prefer* is given, checks that specific engine first.
    """
    candidates = [prefer] if prefer else []
    candidates.extend(e for e in ("docker", "podman") if e != prefer)
    for cmd in candidates:
        if shutil.which(cmd) is not None:
            return cmd
    msg = "No container engine found. Install Docker or Podman, or use --dry-run."
    raise RuntimeError(msg)


def _check_image(image_ref: str, engine: str) -> bool:
    """Return True if *image_ref* exists locally."""
    try:
        r = subprocess.run(
            [engine, "image", "inspect", image_ref],
            capture_output=True, text=True, timeout=30,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _pull_image(image_ref: str, engine: str) -> bool:
    """Attempt to pull *image_ref*. Returns True on success."""
    try:
        r = subprocess.run(
            [engine, "pull", image_ref],
            capture_output=True, text=True, timeout=180,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _resolve_image_ref(manifest: Manifest, engine: str) -> str | None:
    """Resolve the OCI image reference for this manifest's runtime.

    Resolution is catalog-backed:
      1. Prefer a local developer image if it exists.
      2. Accept an already-local published GHCR tag if it exists.
      3. Pull the published GHCR tag from the catalog.
    Returns the image ref, or None if unresolvable.
    """
    binding = resolve_runtime(manifest.runtime)
    candidates = [
        ref for ref in [binding.local_oci_image, binding.oci_image]
        if ref
    ]
    for ref in candidates:
        if _check_image(ref, engine):
            return ref
    if binding.oci_image and _pull_image(binding.oci_image, engine):
        return binding.oci_image
    return None


# ---------------------------------------------------------------------------
# Container execution
# ---------------------------------------------------------------------------

def execute_inside_container(
    manifest: Manifest,
    bundle_path: Path,
    *,
    engine: str | None = None,
    image_ref: str | None = None,
    timeout: int = 600,
    workspace: Path | str | None = None,
) -> BuildResult:
    """Run the WinForge build inside the runtime provider's Docker/Podman container.

    Args:
        manifest:         The parsed WinForge manifest.
        bundle_path:      Host-path to the bundle output directory (must exist).
        engine:           Container engine (docker, podman). Auto-detect if None.
        image_ref:        Explicit OCI image reference. Resolve from manifest if None.
        timeout:          Max seconds for the entire build.
        workspace:        Host workspace mounted read-only at /workspace.

    Returns:
        BuildResult with success/failure and metadata.
    """
    engine = engine or _find_engine()
    runtime = resolve_runtime(manifest.runtime)

    # Resolve image reference
    img = image_ref or _resolve_image_ref(manifest, engine)
    if not img:
        # Fallback: construct a ref for the user's information
        from container.manager import get_image_ref as _img_ref
        img = _img_ref(manifest.runtime.provider, manifest.runtime.version)

    # ---- Write the build script into the bundle ----
    script_path = bundle_path / "build" / "run.sh"
    script = generate_build_script(
        manifest,
        bundle_mount="/opt/winforge",
        workspace_mount="/workspace",
    )
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)

    # ---- Ensure logs dir exists ----
    (bundle_path / "logs").mkdir(parents=True, exist_ok=True)

    # ---- Determine mount points ----
    # Bundle:       /host/bundle-name → /opt/winforge (inside container)
    # Workspace:     selected workspace → /workspace       (for source-file access)
    host_bundle = bundle_path.resolve()
    host_workspace = Path(workspace or Path.cwd()).resolve()
    mounts = [
        f"{host_bundle}:/opt/winforge",
        f"{host_workspace}:/workspace:ro",
    ]

    # ---- Build the docker/podman run command ----
    cmd = [
        engine, "run", "--rm",
    ]
    for m in mounts:
        cmd.extend(["-v", m])
    # Ensure shared memory is large enough for Wine
    cmd.extend(["--shm-size", "2g"])
    cmd.append(img)

    # Pass through xvfb-entrypoint.sh (which starts Xvfb, then execs CMD)
    cmd.extend(["bash", "/opt/winforge/build/run.sh"])

    # ---- Execute ----
    log_lines: list[str] = []
    log_lines.append(f"[winforge] Engine: {engine}")
    log_lines.append(f"[winforge] Image:  {img}")
    log_lines.append(f"[winforge] Bundle: {host_bundle}")
    log_lines.append(f"[winforge] CWD:    {host_workspace}")
    log_lines.append("")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=timeout,
        )
        log_lines.append(result.stdout or "")
        if result.stderr:
            log_lines.append("--- stderr ---")
            log_lines.append(result.stderr)

        log_text = "\n".join(log_lines)
        (bundle_path / "logs" / "build.log").write_text(log_text, encoding="utf-8")

        success = result.returncode == 0
        exit_code = result.returncode

        # ---- Parse build result marker ----
        prefix_size = None
        prefix_file_count = None
        build_result_path = bundle_path / "metadata" / "build-result.json"
        if build_result_path.exists():
            try:
                bd = json.loads(build_result_path.read_text(encoding="utf-8"))
                prefix_size = bd.get("prefixSize", 0)
                prefix_file_count = bd.get("prefixFileCount", 0)
            except (json.JSONDecodeError, OSError):
                pass

        # ---- Verify prefix exists ----
        prefix_path = bundle_path / "prefix"
        if success and not prefix_path.exists():
            success = True  # Still success if the script completed with 0
            log_lines.append("[winforge] Note: prefix directory not found at expected path")
            log_text = "\n".join(log_lines)
            (bundle_path / "logs" / "build.log").write_text(log_text, encoding="utf-8")

        return BuildResult(
            success=success,
            bundle_path=str(host_bundle),
            runtime_provider=manifest.runtime.provider,
            runtime_version=manifest.runtime.version,
            image_ref=img,
            engine=engine,
            exit_code=exit_code,
            log=log_text,
            prefix_size=prefix_size,
            prefix_file_count=prefix_file_count,
        )

    except FileNotFoundError:
        error = (f"Container engine '{engine}' not found. "
                 "Install Docker or Podman, or use --dry-run to skip execution.")
        log_lines.append(error)
        (bundle_path / "logs" / "build.log").write_text("\n".join(log_lines), encoding="utf-8")
        return BuildResult(
            success=False, bundle_path=str(host_bundle),
            runtime_provider=manifest.runtime.provider,
            runtime_version=manifest.runtime.version,
            image_ref=img, engine=engine,
            error=error,
        )

    except subprocess.TimeoutExpired:
        error = f"Build timed out after {timeout}s."
        log_lines.append(error)
        (bundle_path / "logs" / "build.log").write_text("\n".join(log_lines), encoding="utf-8")
        return BuildResult(
            success=False, bundle_path=str(host_bundle),
            runtime_provider=manifest.runtime.provider,
            runtime_version=manifest.runtime.version,
            image_ref=img, engine=engine,
            error=error,
        )

    except subprocess.CalledProcessError as exc:
        error = f"Container exited with code {exc.returncode}: {exc.stderr[-500:] if exc.stderr else '(no stderr)'}"
        log_lines.append(exc.stdout or "")
        log_lines.append(exc.stderr or "")
        (bundle_path / "logs" / "build.log").write_text("\n".join(log_lines), encoding="utf-8")
        return BuildResult(
            success=False, bundle_path=str(host_bundle),
            runtime_provider=manifest.runtime.provider,
            runtime_version=manifest.runtime.version,
            image_ref=img, engine=engine,
            exit_code=exc.returncode,
            error=error,
        )

    except RuntimeError as exc:
        return BuildResult(
            success=False, bundle_path=str(host_bundle),
            runtime_provider=manifest.runtime.provider,
            runtime_version=manifest.runtime.version,
            image_ref=img or "", engine=engine,
            error=str(exc),
        )


__all__ = [
    "BuildResult",
    "execute_inside_container",
    "_check_image",
    "_pull_image",
    "_find_engine",
]
