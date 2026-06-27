"""Runtime catalog loader and matrix generator for WinForge.

The catalog is the single source of truth for supported runtime provider
versions, local image refs, published GHCR image refs, Dockerfiles, and
container build arguments.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import shlex
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).with_name("catalog.json")
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class CatalogVersion:
    provider: str
    version: str
    build_value: str
    tag: str
    channel: str | None
    ci_build: bool
    runtime_usable: bool
    launcher: str
    notes: str
    local_image: str
    published_image_name: str
    dockerfile: str
    build_arg: str
    default_registry: str

    @property
    def local_ref(self) -> str:
        return f"{self.local_image}:{self.tag}"

    @property
    def published_ref(self) -> str:
        return f"{self.default_registry}/{self.published_image_name}:{self.tag}"

    @property
    def dockerfile_path(self) -> Path:
        return ROOT / self.dockerfile

    def build_arg_line(self) -> str:
        return f"{self.build_arg}={self.build_value}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "version": self.version,
            "buildValue": self.build_value,
            "tag": self.tag,
            "channel": self.channel,
            "ciBuild": self.ci_build,
            "runtimeUsable": self.runtime_usable,
            "launcher": self.launcher,
            "notes": self.notes,
            "localImage": self.local_image,
            "localRef": self.local_ref,
            "publishedImageName": self.published_image_name,
            "publishedRef": self.published_ref,
            "dockerfile": self.dockerfile,
            "buildArg": self.build_arg,
            "buildArgLine": self.build_arg_line(),
        }


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schemaVersion") != "winforge.runtime-catalog/v0":
        raise ValueError("runtime catalog schemaVersion must be winforge.runtime-catalog/v0")
    if not isinstance(data.get("providers"), dict):
        raise ValueError("runtime catalog providers must be an object")
    return data


def list_catalog_providers() -> list[str]:
    return sorted(load_catalog()["providers"])


def get_provider(provider: str) -> dict[str, Any] | None:
    return load_catalog()["providers"].get(provider)


def resolve_catalog_version(provider: str, version: str | None = None,
                            channel: str | None = None) -> CatalogVersion | None:
    catalog = load_catalog()
    providers = catalog["providers"]
    pdata = providers.get(provider)
    if pdata is None:
        return None

    versions = pdata.get("versions", {})
    requested = version or "default"
    if requested in {"default", "latest"}:
        requested = pdata.get("defaultVersion")

    vdata = versions.get(requested)
    if vdata is None and channel:
        for candidate_version, candidate_data in versions.items():
            if candidate_data.get("channel") == channel:
                requested = candidate_version
                vdata = candidate_data
                break
    if vdata is None:
        return None

    return CatalogVersion(
        provider=provider,
        version=requested,
        build_value=str(vdata.get("buildValue", requested)),
        tag=str(vdata.get("tag", requested)),
        channel=vdata.get("channel"),
        ci_build=bool(vdata.get("ciBuild", False)),
        runtime_usable=bool(vdata.get("runtimeUsable", True)),
        launcher=str(pdata.get("launcher", "wine")),
        notes=str(pdata.get("notes", "")),
        local_image=str(pdata["localImage"]),
        published_image_name=str(pdata["publishedImageName"]),
        dockerfile=str(pdata["dockerfile"]),
        build_arg=str(pdata["buildArg"]),
        default_registry=str(catalog.get("defaultRegistry", "ghcr.io/myos-dev")),
    )


def ci_matrix() -> dict[str, list[dict[str, str]]]:
    include: list[dict[str, str]] = []
    for provider in list_catalog_providers():
        pdata = get_provider(provider) or {}
        for version in sorted((pdata.get("versions") or {}).keys()):
            entry = resolve_catalog_version(provider, version)
            if entry is None or not entry.ci_build:
                continue
            include.append({
                "provider": entry.provider,
                "version": entry.version,
                "tag": entry.tag,
                "dockerfile": entry.dockerfile,
                "build_arg": entry.build_arg_line(),
                "image_name": entry.published_image_name,
                "local_image": entry.local_image,
                "runtime_usable": str(entry.runtime_usable).lower(),
            })
    return {"include": include}


def shell_build_entry(provider: str, version: str) -> str:
    entry = resolve_catalog_version(provider, version)
    if entry is None:
        raise SystemExit(f"Unknown runtime catalog entry: {provider}:{version}")
    values = {
        "CATALOG_PROVIDER": entry.provider,
        "CATALOG_VERSION": entry.version,
        "CATALOG_TAG": entry.tag,
        "CATALOG_LOCAL_IMAGE": entry.local_image,
        "CATALOG_PUBLISHED_IMAGE_NAME": entry.published_image_name,
        "CATALOG_DOCKERFILE": entry.dockerfile,
        "CATALOG_BUILD_ARG_LINE": entry.build_arg_line(),
    }
    return "\n".join(f"{k}={shlex.quote(v)}" for k, v in values.items())


def shell_build_list() -> str:
    rows = []
    for item in ci_matrix()["include"]:
        rows.append("\t".join([
            item["provider"], item["version"], item["tag"],
            item["local_image"], item["dockerfile"], item["build_arg"],
            item["image_name"],
        ]))
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m runtime.catalog")
    parser.add_argument("--ci-matrix", action="store_true",
                        help="Print GitHub Actions matrix JSON")
    parser.add_argument("--shell-build-list", action="store_true",
                        help="Print tab-separated local build entries")
    parser.add_argument("--shell-build-entry", nargs=2,
                        metavar=("PROVIDER", "VERSION"),
                        help="Print shell assignments for one build entry")
    parser.add_argument("--list", action="store_true",
                        help="Print the normalized catalog entries as JSON")
    args = parser.parse_args(argv)

    if args.ci_matrix:
        print(json.dumps(ci_matrix(), separators=(",", ":")))
    elif args.shell_build_list:
        print(shell_build_list())
    elif args.shell_build_entry:
        print(shell_build_entry(args.shell_build_entry[0],
                                args.shell_build_entry[1]))
    elif args.list:
        entries = []
        for provider in list_catalog_providers():
            pdata = get_provider(provider) or {}
            for version in sorted((pdata.get("versions") or {}).keys()):
                entry = resolve_catalog_version(provider, version)
                if entry is not None:
                    entries.append(entry.to_dict())
        print(json.dumps(entries, indent=2, sort_keys=True))
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
