"""WinForge Container Manager.

Uses runtime/catalog.json as the source of truth for available runtime
container builds and image references.
"""
from __future__ import annotations
import subprocess
from dataclasses import dataclass
from typing import Any

from runtime.catalog import (
    list_catalog_providers,
    resolve_catalog_version,
)


@dataclass
class ContainerBuildResult:
    provider: str
    tag: str
    image_ref: str
    dockerfile_path: str
    success: bool
    log: str = ""

    def to_dict(self):
        return {
            "provider": self.provider,
            "tag": self.tag,
            "imageRef": self.image_ref,
            "dockerfile": self.dockerfile_path,
            "success": self.success,
            "log": self.log,
        }


def list_definitions() -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for provider in list_catalog_providers():
        default_entry = resolve_catalog_version(provider, "default")
        if default_entry is None:
            continue
        definitions.append({
            "name": provider,
            "displayName": default_entry.provider,
            "defaultVersion": default_entry.version,
            "launcher": default_entry.launcher,
            "localImage": default_entry.local_image,
            "localRef": default_entry.local_ref,
            "publishedImageName": default_entry.published_image_name,
            "publishedRef": default_entry.published_ref,
            "buildArg": default_entry.build_arg,
            "dockerfile": str(default_entry.dockerfile_path),
            "runtimeUsable": default_entry.runtime_usable,
        })
    return definitions


def build_container(
    provider: str,
    version: str,
    *,
    registry: str | None = None,
    push: bool = False,
    build_cmd: str = "docker",
) -> ContainerBuildResult:
    entry = resolve_catalog_version(provider, version)
    if entry is None:
        msg = (f"Unknown provider/version: {provider}:{version}. "
               "Add it to runtime/catalog.json first.")
        return ContainerBuildResult(provider, version, "", "", False, msg)

    dockerfile = entry.dockerfile_path
    if not dockerfile.exists():
        return ContainerBuildResult(
            provider, entry.tag, "", str(dockerfile), False,
            f"Dockerfile not found: {dockerfile}",
        )

    local_tag = entry.local_ref
    publish_registry = registry or (entry.default_registry if push else None)
    published_tag = (
        f"{publish_registry}/{entry.published_image_name}:{entry.tag}"
        if publish_registry else ""
    )

    cmd = [
        build_cmd, "build",
        "--build-arg", entry.build_arg_line(),
        "-t", local_tag,
        "-f", str(dockerfile),
        str(dockerfile.parents[2].parent),
    ]
    if published_tag:
        cmd.extend(["-t", published_tag])

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
        )
        log = result.stdout + result.stderr
        if result.returncode != 0:
            return ContainerBuildResult(
                provider, entry.tag, local_tag, str(dockerfile), False,
                f"Build failed (exit {result.returncode}):\n{log[-2000:]}",
            )

        if push and published_tag:
            push_result = subprocess.run(
                [build_cmd, "push", published_tag],
                capture_output=True, text=True, timeout=300,
            )
            log += push_result.stdout + push_result.stderr
            if push_result.returncode != 0:
                return ContainerBuildResult(
                    provider, entry.tag, published_tag, str(dockerfile), False,
                    f"Push failed (exit {push_result.returncode}):\n{log[-2000:]}",
                )

        return ContainerBuildResult(
            provider, entry.tag, published_tag or local_tag,
            str(dockerfile), True, log,
        )

    except subprocess.TimeoutExpired:
        return ContainerBuildResult(
            provider, entry.tag, local_tag, str(dockerfile), False,
            "Build timed out after 600s",
        )
    except FileNotFoundError:
        return ContainerBuildResult(
            provider, entry.tag, local_tag, str(dockerfile), False,
            f"Command '{build_cmd}' not found. Install Docker or Podman.",
        )


def get_image_available(provider: str, version: str, *,
                        build_cmd: str = "docker",
                        published: bool = False) -> bool:
    image_ref = get_image_ref(provider, version, published=published)
    if not image_ref:
        return False
    try:
        result = subprocess.run(
            [build_cmd, "image", "inspect", image_ref],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_image_ref(provider: str, version: str,
                  *, published: bool = True) -> str:
    entry = resolve_catalog_version(provider, version)
    if entry:
        return entry.published_ref if published else entry.local_ref
    image_name = provider if provider.startswith("winforge-") else f"winforge-{provider}"
    if published:
        return f"ghcr.io/myos-dev/{image_name}:{version}"
    return f"winforge/{provider}:{version}"


def get_local_image_ref(provider: str, version: str) -> str:
    return get_image_ref(provider, version, published=False)
