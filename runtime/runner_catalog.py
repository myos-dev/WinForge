"""Wine runner catalog for downloadable, cacheable runner archives.

Runtime provider images and downloadable Wine runner archives are separate
contracts. A recipe can still use provider ``wine`` while requesting a specific
runner archive such as ``pol-8.2``. The ``pol-*`` aliases are Bottles-compatible
labels for PlayOnLinux/Phoenicis-hosted upstream Wine tarballs, not a separate
PlayOnLinux provider.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

RUNNER_CATALOG_SCHEMA_VERSION = "winforge.runner-catalog/v0"
PLAYONLINUX_PHOENICIS_X86_BASE = "https://www.playonlinux.com/wine/binaries/phoenicis/upstream-linux-x86"


@dataclass(frozen=True)
class RunnerSpec:
    id: str
    provider: str
    version: str
    arch: str
    source: str
    url: str
    sha256: str | None = None
    strip_components: int = 1
    notes: str | None = None

    @property
    def filename(self) -> str:
        return self.url.rstrip('/').split('/')[-1]

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "provider": self.provider,
            "version": self.version,
            "arch": self.arch,
            "source": self.source,
            "url": self.url,
            "sha256": self.sha256,
            "stripComponents": self.strip_components,
            "filename": self.filename,
            "notes": self.notes,
        }
        return {k: v for k, v in payload.items() if v is not None}


def _pol_upstream(version: str, *, sha256: str | None = None, notes: str | None = None) -> RunnerSpec:
    return RunnerSpec(
        id=f"pol-{version}",
        provider="wine",
        version=version,
        arch="x86",
        source="playonlinux-phoenicis-upstream",
        url=f"{PLAYONLINUX_PHOENICIS_X86_BASE}/PlayOnLinux-wine-{version}-upstream-linux-x86.tar.gz",
        sha256=sha256,
        strip_components=1,
        notes=notes,
    )


_BUILTIN_RUNNERS: dict[str, RunnerSpec] = {
    "pol-8.2": _pol_upstream(
        "8.2",
        sha256="d38ed5362564c0de73a6f4720a20cf6eece569d2455be2567ac41e1a8a5cb0d6",
        notes="Office 2007/2010 initial runner from Rustring/Bottles reference.",
    ),
    "pol-4.3": _pol_upstream(
        "4.3",
        sha256="64f34fb79de3225bb541fcb8d8c57d0ecf9db2d404e57834096738680c95b29c",
        notes="Office 2013/2016 initial runner from Rustring/Bottles reference.",
    ),
    "pol-3.0.3": _pol_upstream(
        "3.0.3",
        sha256="0b5d59ad852b87ffccf7a72066fd80cb0759647ebd952c2851ce2b5d76ba33c4",
        notes="Office 2007/2010 fallback runner mentioned by Rustring; extract into pol-3.0.3, not the README's apparent pol-9.0 typo.",
    ),
}


class RunnerCatalogError(ValueError):
    pass


def list_runner_specs() -> list[RunnerSpec]:
    return [_BUILTIN_RUNNERS[key] for key in sorted(_BUILTIN_RUNNERS)]


def resolve_runner_spec(runner_id: str) -> RunnerSpec:
    try:
        return _BUILTIN_RUNNERS[runner_id]
    except KeyError as exc:
        known = ", ".join(sorted(_BUILTIN_RUNNERS))
        raise RunnerCatalogError(f"unknown runner alias: {runner_id}. Known aliases: {known}") from exc


def runner_catalog_payload() -> dict[str, Any]:
    return {
        "schemaVersion": RUNNER_CATALOG_SCHEMA_VERSION,
        "runners": [spec.to_dict() for spec in list_runner_specs()],
    }
