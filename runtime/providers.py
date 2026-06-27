"""Pluggable runtime provider abstraction with OCI container image binding."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from core.manifest import ManifestError, RuntimeSpec

# Registry of known OCI image references per provider
OCI_IMAGE_MAP: dict[str, tuple[str, str]] = {
    "wine":       ("winforge/wine",        ""),
    "staging":    ("winforge/wine-staging", ""),
    "proton":     ("winforge/proton",      ""),
    "proton-ge":  ("winforge/proton-ge",   ""),
}


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

    def to_dict(self):
        d = {k: v for k, v in {
            "provider": self.provider, "version": self.version,
            "launcher": self.launcher, "source": self.source,
            "channel": self.channel, "digest": self.digest,
            "notes": self.notes, "ociImage": self.oci_image,
        }.items() if v is not None}
        return d


def resolve_oci_image(provider: str, version: str) -> str | None:
    entry = OCI_IMAGE_MAP.get(provider)
    if entry is None:
        return None
    repo, tag_prefix = entry
    tag = f"{tag_prefix}{version}" if tag_prefix else version
    return f"{repo}:{tag}"


class RuntimeProvider(Protocol):
    name: str
    def resolve(self, spec: RuntimeSpec) -> RuntimeBinding: ...


class WineProvider:
    name = "wine"
    def resolve(self, spec):
        oci = resolve_oci_image(spec.provider, spec.version)
        return RuntimeBinding(
            spec.provider, spec.version, "wine",
            spec.source, spec.channel, spec.digest,
            "Wine Stable OCI base — built from WineHQ packages.",
            oci_image=oci,
        )


class WineStagingProvider:
    name = "staging"
    def resolve(self, spec):
        oci = resolve_oci_image(spec.provider, spec.version)
        return RuntimeBinding(
            spec.provider, spec.version, "wine",
            spec.source, spec.channel, spec.digest,
            "Wine Staging OCI base — WineHQ staging packages.",
            oci_image=oci,
        )


class ProtonProvider:
    name = "proton"
    def resolve(self, spec):
        oci = resolve_oci_image(spec.provider, spec.version)
        return RuntimeBinding(
            spec.provider, spec.version, "proton",
            spec.source, spec.channel, spec.digest,
            "Valve Proton source OCI seed — GitHub source archive; use proton-ge for a prebuilt Proton runtime.",
            oci_image=oci,
        )


class ProtonGEProvider:
    name = "proton-ge"
    def resolve(self, spec):
        oci = resolve_oci_image(spec.provider, spec.version)
        return RuntimeBinding(
            spec.provider, spec.version, "proton",
            spec.source, spec.channel, spec.digest,
            "GE-Proton OCI base — GloriousEggroll releases.",
            oci_image=oci,
        )


_PROVIDERS = {
    p.name: p
    for p in [WineProvider(), WineStagingProvider(), ProtonProvider(), ProtonGEProvider()]
}


def register_provider(provider: RuntimeProvider):
    _PROVIDERS[provider.name] = provider
    if provider.name not in OCI_IMAGE_MAP:
        OCI_IMAGE_MAP[provider.name] = (f"winforge/{provider.name}", "")


def resolve_runtime(spec: RuntimeSpec) -> RuntimeBinding:
    try:
        return _PROVIDERS[spec.provider].resolve(spec)
    except KeyError as exc:
        raise ManifestError(f"unsupported runtime provider: {spec.provider}") from exc


def list_providers() -> list[str]:
    return sorted(_PROVIDERS)


def resolve_image(provider: str, version: str) -> str | None:
    oci = resolve_oci_image(provider, version)
    if oci:
        return oci
    return f"winforge/{provider}:{version}" if provider else None
