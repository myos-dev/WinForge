"""Pluggable runtime provider abstraction with runtime catalog binding."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol

from core.manifest import ManifestError, RuntimeSpec
from runtime.catalog import (
    list_catalog_providers,
    resolve_catalog_version,
)


@dataclass(frozen=True)
class RuntimeBinding:
    provider: str
    version: str
    launcher: str
    source: str | None = None
    channel: str | None = None
    digest: str | None = None
    notes: str | None = None
    oci_image: str | None = None
    local_oci_image: str | None = None
    runtime_usable: bool | None = None

    def to_dict(self):
        d = {k: v for k, v in {
            "provider": self.provider,
            "version": self.version,
            "launcher": self.launcher,
            "source": self.source,
            "channel": self.channel,
            "digest": self.digest,
            "notes": self.notes,
            "ociImage": self.oci_image,
            "localOciImage": self.local_oci_image,
            "runtimeUsable": self.runtime_usable,
        }.items() if v is not None}
        return d


class RuntimeProvider(Protocol):
    name: str
    def resolve(self, spec: RuntimeSpec) -> RuntimeBinding: ...


_EXTRA_PROVIDERS: dict[str, RuntimeProvider] = {}


def resolve_oci_image(provider: str, version: str,
                      channel: str | None = None,
                      *, published: bool = True) -> str | None:
    """Resolve provider/version to an OCI image ref from the catalog.

    By default this returns the published GHCR ref, because that is what a
    normal Forge build should pull after CI publishes the runtime catalog.
    Pass ``published=False`` for the local developer tag.
    """
    entry = resolve_catalog_version(provider, version, channel)
    if entry is None:
        return None
    return entry.published_ref if published else entry.local_ref


def resolve_local_oci_image(provider: str, version: str,
                            channel: str | None = None) -> str | None:
    return resolve_oci_image(provider, version, channel, published=False)


def register_provider(provider: RuntimeProvider):
    _EXTRA_PROVIDERS[provider.name] = provider


def resolve_runtime(spec: RuntimeSpec) -> RuntimeBinding:
    if spec.provider in _EXTRA_PROVIDERS:
        return _EXTRA_PROVIDERS[spec.provider].resolve(spec)

    entry = resolve_catalog_version(spec.provider, spec.version, spec.channel)
    if entry is None:
        known = set(list_catalog_providers())
        if spec.provider not in known:
            raise ManifestError(f"unsupported runtime provider: {spec.provider}")
        raise ManifestError(
            f"unsupported runtime version for {spec.provider}: {spec.version}. "
            "Add it to runtime/catalog.json before building."
        )

    return RuntimeBinding(
        provider=spec.provider,
        version=entry.version,
        launcher=entry.launcher,
        source=spec.source,
        channel=entry.channel or spec.channel,
        digest=spec.digest,
        notes=entry.notes,
        oci_image=entry.published_ref,
        local_oci_image=entry.local_ref,
        runtime_usable=entry.runtime_usable,
    )


def list_providers() -> list[str]:
    return sorted(set(list_catalog_providers()) | set(_EXTRA_PROVIDERS))


def resolve_image(provider: str, version: str) -> str | None:
    return resolve_oci_image(provider, version)
