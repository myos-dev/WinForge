"""Plan and execute WinForge bundles with catalog runtime images.

`winforge run` consumes a verified execution bundle rather than the original
manifest. The bundle's metadata/graph.json is the source of truth for the
runtime image, launch command, graphics mode contract, and exact-runtime
policy.
"""
from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from artifact.inspection import verify_bundle
from core.compatibility import compatibility_environment
from runtime.runner_cache import DEFAULT_CACHE_DIR, diagnose_runner

RUN_PLAN_SCHEMA_VERSION = "winforge.run-plan/v0"
RUN_RESULT_SCHEMA_VERSION = "winforge.run-result/v0"
BUNDLE_MOUNT = "/opt/winforge/bundle"
PREFIX_COPY = "/tmp/winforge-prefix"
FILE_INPUT_MOUNT_ROOT = "/mnt/winforge-inputs"
RUNNER_CONTAINER_DIR = "/opt/winforge-runner"
SUPPORTED_GRAPHICS = {"headless", "vnc"}
SUPPORTED_NETWORK_MODES = {"none", "bridge", "host"}
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class RunError(RuntimeError):
    """Raised when a bundle cannot be planned or launched."""


def build_run_plan(
    bundle_path: Path | str,
    *,
    graphics: str | None = None,
    engine: str | None = None,
    vnc_port: int = 5900,
    novnc_port: int = 6080,
    container_name: str | None = None,
    entrypoint: str | None = None,
    files: list[Path | str] | None = None,
    runner_cache_dir: Path | str | None = None,
    require_runner: bool = False,
    network: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic container run plan for a verified bundle."""
    bundle = Path(bundle_path)
    verification = verify_bundle(bundle)
    if not verification.get("valid"):
        raise RunError("invalid WinForge bundle: " + _verification_error_text(verification))

    graph = _load_json(bundle / "metadata" / "graph.json")
    runtime = dict(graph.get("runnerRuntime") or {})
    launch = dict(graph.get("launch") or {})
    suite_entrypoints = list(graph.get("entrypoints") or [])
    file_associations = list(graph.get("fileAssociations") or [])
    selected_entrypoint, launch = _select_launch(launch, suite_entrypoints, entrypoint)
    selected_engine = engine or _find_engine()
    file_arguments = _file_arguments(files or [], engine=selected_engine)
    graphics_contract = dict(graph.get("graphics") or {})
    compatibility_policy = dict((graph.get("compatibility") or {}).get("requestedPolicy") or {})
    compatibility_env = compatibility_environment(compatibility_policy)

    mode = graphics or str(graphics_contract.get("defaultMode") or "headless")
    supported_modes = list(graphics_contract.get("supportedModes") or [])
    if mode not in SUPPORTED_GRAPHICS:
        allowed = ", ".join(sorted(SUPPORTED_GRAPHICS))
        raise RunError(f"graphics mode {mode!r} must be one of: {allowed}")
    if mode not in supported_modes:
        raise RunError(
            f"graphics mode {mode!r} is not supported by bundle graph "
            f"(supported: {', '.join(supported_modes) or 'none'})"
        )

    graph_network = runtime["network"] if "network" in runtime else "none"
    selected_network = network if network is not None else graph_network
    _validate_network_mode(selected_network)
    _validate_graphics_network(mode, selected_network)

    image = _runtime_image(runtime)
    launch_command = _launch_command(runtime, launch, [item["winePath"] for item in file_arguments])
    runner_cache = _runner_cache_plan(runtime, runner_cache_dir, require_runner=require_runner, engine=selected_engine)
    environment = _container_environment(mode)
    environment.update(compatibility_env)
    if runner_cache and runner_cache.get("status") == "present":
        environment.update(runner_cache["environment"])
    script = _launch_script(
        mode,
        launch,
        launch_command,
        compatibility_env,
        runner_enabled=bool(runner_cache and runner_cache.get("status") == "present"),
    )
    argv = _container_argv(
        selected_engine,
        bundle,
        image,
        environment,
        script,
        graphics=mode,
        vnc_port=vnc_port,
        novnc_port=novnc_port,
        container_name=container_name,
        network=selected_network,
        file_mounts=[item["mount"] for item in file_arguments] + ([runner_cache["mount"]] if runner_cache and runner_cache.get("status") == "present" else []),
    )

    return {
        "schemaVersion": RUN_PLAN_SCHEMA_VERSION,
        "bundle": str(bundle),
        "verification": {
            "schemaVersion": verification.get("schemaVersion"),
            "valid": True,
            "warnings": verification.get("warnings", []),
        },
        "application": graph.get("application", {}),
        "runtime": {
            "provider": runtime.get("provider"),
            "version": runtime.get("version"),
            "requestedVersion": runtime.get("requestedVersion"),
            "resolvedVersion": runtime.get("resolvedVersion"),
            "family": runtime.get("family"),
            "runner": runtime.get("runner"),
            "runnerVersion": runtime.get("runnerVersion"),
            "runnerSource": runtime.get("runnerSource"),
            "runnerUrl": runtime.get("runnerUrl"),
            "runnerSha256": runtime.get("runnerSha256"),
            "runnerArch": runtime.get("runnerArch"),
            "packageVersion": runtime.get("packageVersion"),
            "launcher": runtime.get("launcher"),
            "launcherVersion": runtime.get("launcherVersion"),
            "network": selected_network,
            "image": image,
            "requiresExactRuntime": bool(
                (graph.get("compatibility") or {}).get("requiresExactRuntime")
            ),
        },
        "graphics": {
            "mode": mode,
            "supportedModes": supported_modes,
            "vncPort": vnc_port if mode == "vnc" else None,
            "noVncPort": novnc_port if mode == "vnc" else None,
        },
        "launch": launch,
        "entrypoints": suite_entrypoints,
        "fileAssociations": file_associations,
        "selectedEntrypoint": selected_entrypoint,
        "fileArguments": file_arguments,
        "runnerCache": runner_cache,
        "launchCommand": launch_command,
        "container": {
            "engine": selected_engine,
            "image": image,
            "bundleMount": _volume_mount(bundle, BUNDLE_MOUNT, engine=selected_engine, read_only=True),
            "fileMounts": [item["mount"] for item in file_arguments],
            "runnerMount": runner_cache["mount"] if runner_cache and runner_cache.get("status") == "present" else None,
            "environment": environment,
            "network": selected_network,
            "script": script,
            "argv": argv,
        },
    }


def execute_run_plan(plan: dict[str, Any], *, timeout: int | None = None) -> dict[str, Any]:
    """Execute a run plan and return a machine-readable process result."""
    argv = plan.get("container", {}).get("argv")
    if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
        raise RunError("run plan container.argv must be a list of strings")

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return {
            "schemaVersion": RUN_RESULT_SCHEMA_VERSION,
            "success": proc.returncode == 0,
            "exitCode": proc.returncode,
            "bundle": plan.get("bundle"),
            "graphics": plan.get("graphics", {}).get("mode"),
            "runtimeImage": plan.get("runtime", {}).get("image"),
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except FileNotFoundError as exc:
        raise RunError(
            f"container engine not found: {argv[0]}. Install Podman or Docker, "
            "or use --dry-run to inspect the planned command."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        return {
            "schemaVersion": RUN_RESULT_SCHEMA_VERSION,
            "success": False,
            "exitCode": None,
            "bundle": plan.get("bundle"),
            "graphics": plan.get("graphics", {}).get("mode"),
            "runtimeImage": plan.get("runtime", {}).get("image"),
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"winforge run timed out after {timeout}s",
            "error": f"timed out after {timeout}s",
        }



def _runner_cache_plan(
    runtime: dict[str, Any],
    cache_dir: Path | str | None,
    *,
    require_runner: bool,
    engine: str,
) -> dict[str, Any] | None:
    runner_id = runtime.get("runner")
    runner_source = runtime.get("runnerSource")
    runner_url = runtime.get("runnerUrl")
    if not isinstance(runner_id, str) or not runner_id:
        return None
    # Ordinary runtime catalog entries use labels such as winehq-stable as
    # provider-runner metadata. Only downloadable archive runners have source
    # and URL provenance and therefore participate in the host cache mount.
    if not runner_source or not runner_url:
        return None

    root = Path(cache_dir or DEFAULT_CACHE_DIR).expanduser().resolve()
    runner_dir = root / runner_id
    wine_path = runner_dir / "bin" / "wine"
    base = {
        "schemaVersion": "winforge.runner-mount/v0",
        "runnerId": runner_id,
        "cacheDir": str(root),
        "runnerDir": str(runner_dir),
        "winePath": str(wine_path),
        "containerDir": RUNNER_CONTAINER_DIR,
        "containerBin": f"{RUNNER_CONTAINER_DIR}/bin",
    }
    if not wine_path.exists():
        payload = {**base, "status": "missing"}
        if require_runner:
            raise RunError(
                f"cached runner is missing: {runner_id}. Run `winforge runners ensure {runner_id}` "
                "or pass --runner-cache-dir pointing at a populated cache."
            )
        return payload

    return {
        **base,
        "status": "present",
        "mount": _volume_mount(runner_dir, RUNNER_CONTAINER_DIR, engine=engine, read_only=True),
        "environment": {
            "WINFORGE_RUNNER_ID": runner_id,
            "WINFORGE_RUNNER_BIN": f"{RUNNER_CONTAINER_DIR}/bin",
        },
        "diagnostic": diagnose_runner(runner_dir),
    }


def _runner_launch_script_lines(enabled: bool) -> list[str]:
    if not enabled:
        return []
    return [
        'if [ -n "${WINFORGE_RUNNER_BIN:-}" ]; then',
        '  echo "[winforge] Using cached Wine runner: ${WINFORGE_RUNNER_ID:-unknown} at $WINFORGE_RUNNER_BIN"',
        '  export PATH="$WINFORGE_RUNNER_BIN:$PATH"',
        '  export WINE="$WINFORGE_RUNNER_BIN/wine"',
        'fi',
    ]


def _find_engine() -> str:
    """Prefer Podman for WinForge run, then fall back to Docker."""
    for candidate in ("podman", "docker"):
        if shutil.which(candidate):
            return candidate
    raise RunError("No container engine found. Install Podman or Docker, or use --dry-run.")


def _volume_mount(source: Path | str, target: str, *, engine: str, read_only: bool = False) -> str:
    """Return a Docker/Podman bind mount string.

    Rootless Podman on SELinux hosts needs a shared label option for bind
    mounts; Docker keeps the traditional mount syntax.
    """
    options: list[str] = []
    if read_only:
        options.append("ro")
    if engine == "podman":
        options.append("z")
    suffix = f":{','.join(options)}" if options else ""
    return f"{Path(source).resolve()}:{target}{suffix}"


def _container_argv(
    engine: str,
    bundle: Path,
    image: str,
    environment: dict[str, str],
    script: str,
    *,
    graphics: str,
    vnc_port: int,
    novnc_port: int,
    container_name: str | None,
    network: str,
    file_mounts: list[str] | None = None,
) -> list[str]:
    argv = [engine, "run", "--rm", "--net", network]
    if container_name:
        argv.extend(["--name", container_name])
    argv.extend(["-v", _volume_mount(bundle, BUNDLE_MOUNT, engine=engine, read_only=True)])
    for mount in file_mounts or []:
        argv.extend(["-v", mount])
    for key, value in environment.items():
        argv.extend(["-e", f"{key}={value}"])
    if graphics == "vnc":
        argv.extend(["-p", f"127.0.0.1:{vnc_port}:5900"])
        argv.extend(["-p", f"127.0.0.1:{novnc_port}:6080"])
    argv.extend([image, "bash", "-lc", script])
    return argv


def _validate_network_mode(network: str) -> None:
    if network not in SUPPORTED_NETWORK_MODES:
        allowed = ", ".join(sorted(SUPPORTED_NETWORK_MODES))
        raise RunError(f"network mode {network!r} must be one of: {allowed}")


def _validate_graphics_network(graphics: str, network: str) -> None:
    if graphics == "vnc" and network != "bridge":
        raise RunError(
            "graphics vnc requires network bridge for loopback-only VNC/noVNC "
            "port publishing; use --network bridge"
        )


def _container_environment(graphics: str) -> dict[str, str]:
    return {
        "WINFORGE_BUNDLE": BUNDLE_MOUNT,
        "WINFORGE_GRAPH": f"{BUNDLE_MOUNT}/metadata/graph.json",
        "WINFORGE_PREFIX_SOURCE": f"{BUNDLE_MOUNT}/prefix",
        "WINEPREFIX": PREFIX_COPY,
        "WINEFS": "launcher",
        "WINFORGE_GRAPHICS": graphics,
        "DISPLAY": ":99",
    }


def _launch_script(
    mode: str,
    launch: dict[str, Any],
    command: list[str],
    compatibility_env: dict[str, str] | None = None,
    *,
    runner_enabled: bool = False,
) -> str:
    lines = [
        "set -euo pipefail",
        f"export WINFORGE_BUNDLE={shlex.quote(BUNDLE_MOUNT)}",
        f"export WINFORGE_GRAPH={shlex.quote(BUNDLE_MOUNT + '/metadata/graph.json')}",
        f"export WINFORGE_PREFIX_SOURCE={shlex.quote(BUNDLE_MOUNT + '/prefix')}",
        f"export WINEPREFIX={shlex.quote(PREFIX_COPY)}",
        "export WINEFS=launcher",
        f"export WINFORGE_GRAPHICS={shlex.quote(mode)}",
        *_runner_launch_script_lines(runner_enabled),
        "rm -rf \"$WINEPREFIX\"",
        "cp -a \"$WINFORGE_PREFIX_SOURCE\" \"$WINEPREFIX\"",
    ]

    working_dir = launch.get("workingDirectory")
    if working_dir:
        lines.append(f"export WINFORGE_WORKING_DIRECTORY={shlex.quote(str(working_dir))}")

    for key, value in sorted((compatibility_env or {}).items()):
        if not _ENV_NAME.fullmatch(key):
            raise RunError(f"compatibility env key {key!r} is not a valid POSIX environment name")
        lines.append(f"export {key}={shlex.quote(str(value))}")

    for key, value in sorted((launch.get("env") or {}).items()):
        if not _ENV_NAME.fullmatch(key):
            raise RunError(f"launch.env key {key!r} is not a valid POSIX environment name")
        lines.append(f"export {key}={shlex.quote(str(value))}")

    if mode == "vnc":
        lines.extend([
            "if ! command -v x11vnc >/dev/null 2>&1; then echo 'x11vnc is required for WinForge vnc graphics' >&2; exit 70; fi",
            "if ! command -v websockify >/dev/null 2>&1; then echo 'websockify is required for WinForge vnc graphics' >&2; exit 70; fi",
            "x11vnc -display \"$DISPLAY\" -rfbport 5900 -forever -shared -nopw -listen 0.0.0.0 >/tmp/winforge-x11vnc.log 2>&1 &",
            "if [ -d /usr/share/novnc ]; then",
            "  websockify --web=/usr/share/novnc 0.0.0.0:6080 localhost:5900 >/tmp/winforge-websockify.log 2>&1 &",
            "else",
            "  websockify 0.0.0.0:6080 localhost:5900 >/tmp/winforge-websockify.log 2>&1 &",
            "fi",
        ])

    lines.append("exec " + " ".join(shlex.quote(part) for part in command))
    return "\n".join(lines) + "\n"


def _select_launch(
    default_launch: dict[str, Any],
    suite_entrypoints: list[Any],
    requested: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if requested is None:
        entrypoint = default_launch.get("entrypoint")
        selected = {
            "id": "default",
            "name": "Default launch entrypoint",
            "source": "launch",
            "executable": entrypoint,
        }
        return selected, dict(default_launch)

    for item in suite_entrypoints:
        if not isinstance(item, dict):
            continue
        if item.get("id") != requested:
            continue
        launch = {
            "entrypoint": item.get("executable"),
            "args": list(item.get("args") or []),
            "env": dict(item.get("env") or {}),
        }
        if item.get("workingDirectory"):
            launch["workingDirectory"] = item.get("workingDirectory")
        selected = {
            "id": item.get("id"),
            "name": item.get("name"),
            "source": "entrypoints",
            "executable": item.get("executable"),
        }
        return selected, launch

    available = [str(item.get("id")) for item in suite_entrypoints if isinstance(item, dict) and item.get("id")]
    suffix = f" (available: {', '.join(available)})" if available else " (no suite entrypoints declared)"
    raise RunError(f"unknown suite entrypoint: {requested}{suffix}")


def _file_arguments(files: list[Path | str], *, engine: str) -> list[dict[str, str]]:
    arguments: list[dict[str, str]] = []
    for index, raw in enumerate(files):
        host = Path(raw).expanduser().resolve()
        if not host.exists():
            raise RunError(f"input file does not exist: {host}")
        if not host.is_file():
            raise RunError(f"input path must be a file: {host}")
        container_dir = f"{FILE_INPUT_MOUNT_ROOT}/{index}"
        container_path = f"{container_dir}/{host.name}"
        wine_path = "Z:" + container_path.replace("/", "\\")
        arguments.append({
            "hostPath": str(host),
            "containerPath": container_path,
            "winePath": wine_path,
            "mount": _volume_mount(host.parent, container_dir, engine=engine, read_only=True),
        })
    return arguments


def _launch_command(runtime: dict[str, Any], launch: dict[str, Any], file_args: list[str] | None = None) -> list[str]:
    entrypoint = launch.get("entrypoint")
    if not isinstance(entrypoint, str) or not entrypoint:
        raise RunError("bundle graph launch.entrypoint must be a non-empty string")
    args = launch.get("args") or []
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise RunError("bundle graph launch.args must be a list of strings")

    launcher = str(runtime.get("launcher") or "wine")
    extra_args = list(file_args or [])
    if launcher == "proton":
        return ["/opt/proton-ge/proton", "run", entrypoint, *args, *extra_args]
    if launcher == "umu":
        return ["umu-run", entrypoint, *args, *extra_args]
    return [launcher, entrypoint, *args, *extra_args]


def _runtime_image(runtime: dict[str, Any]) -> str:
    image = runtime.get("image") or runtime.get("ociImage") or runtime.get("localImage")
    if not isinstance(image, str) or not image:
        raise RunError("bundle graph runnerRuntime must include an image")
    return image


def _verification_error_text(verification: dict[str, Any]) -> str:
    errors = verification.get("errors") or []
    if errors:
        return "; ".join(str(error) for error in errors)
    return "bundle verification failed"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["RunError", "build_run_plan", "execute_run_plan"]
