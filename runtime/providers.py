"""Pluggable runtime provider abstraction."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Protocol
from core.manifest import ManifestError, RuntimeSpec
@dataclass(frozen=True)
class RuntimeBinding:
    provider: str; version: str; launcher: str; source: str|None=None; channel: str|None=None; digest: str|None=None; notes: str|None=None
    def to_dict(self): return {k:v for k,v in {"provider":self.provider,"version":self.version,"launcher":self.launcher,"source":self.source,"channel":self.channel,"digest":self.digest,"notes":self.notes}.items() if v is not None}
class RuntimeProvider(Protocol):
    name: str
    def resolve(self, spec: RuntimeSpec) -> RuntimeBinding: ...
class WineProvider:
    name="wine"
    def resolve(self, spec): return RuntimeBinding(spec.provider, spec.version, "wine", spec.source, spec.channel, spec.digest, "Wine Stable-compatible provider binding.")
class WineStagingProvider:
    name="staging"
    def resolve(self, spec): return RuntimeBinding(spec.provider, spec.version, "wine", spec.source, spec.channel, spec.digest, "Wine Staging provider binding; staging patches are part of runtime provenance.")
class ProtonProvider:
    name="proton"
    def resolve(self, spec): return RuntimeBinding(spec.provider, spec.version, "proton", spec.source, spec.channel, spec.digest, "Proton provider binding; execution envelope may require Proton-compatible environment variables.")
class ProtonGEProvider:
    name="proton-ge"
    def resolve(self, spec): return RuntimeBinding(spec.provider, spec.version, "proton", spec.source, spec.channel, spec.digest, "Proton-GE provider binding pinned by release artifact.")
_PROVIDERS = {p.name:p for p in [WineProvider(), WineStagingProvider(), ProtonProvider(), ProtonGEProvider()]}
def register_provider(provider: RuntimeProvider): _PROVIDERS[provider.name] = provider
def resolve_runtime(spec: RuntimeSpec):
    try: return _PROVIDERS[spec.provider].resolve(spec)
    except KeyError as exc: raise ManifestError(f"unsupported runtime provider: {spec.provider}") from exc
