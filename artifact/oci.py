"""OCI image mapping model for WinForge bundles."""
from __future__ import annotations
from dataclasses import dataclass
@dataclass(frozen=True)
class OCIImageMapping:
    bundle_root: str = "/opt/winforge/bundle"
    prefix_path: str = "/opt/winforge/bundle/prefix"
    manifest_path: str = "/opt/winforge/bundle/manifest.winforge.json"
    runtime_path: str = "/opt/winforge/bundle/runtime/runtime.json"
    entrypoint_path: str = "/opt/winforge/bundle/launch/entrypoint.json"
    def labels(self): return {"org.opencontainers.image.title":"WinForge execution bundle","dev.winforge.artifact.kind":"execution-bundle","dev.winforge.artifact.version":"v0"}
