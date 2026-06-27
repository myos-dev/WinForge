"""Build phase planning for WinForge bundles."""
from __future__ import annotations
from core.manifest import Manifest
from runtime.providers import resolve_runtime
PHASE_ORDER = ["init-prefix","install-dependencies","install-apps","apply-layout-and-registry","validate","seal-artifact"]
def build_plan(manifest: Manifest) -> list[dict[str, object]]:
    runtime = resolve_runtime(manifest.runtime)
    return [
        {"phase":"init-prefix","inputs":["runtime","manifest"],"actions":["create empty WINEPREFIX directory","initialize drive_c and registry hives",f"bind runtime provider {runtime.provider}:{runtime.version}"]},
        {"phase":"install-dependencies","inputs":["dependencies"],"actions":[_dep(d.kind,d.verbs,d.name) for d in manifest.dependencies] or ["no dependencies declared"]},
        {"phase":"install-apps","inputs":["install"],"actions":[_inst(s.kind,s.source,s.target,s.command) for s in manifest.install] or ["no application install steps declared"]},
        {"phase":"apply-layout-and-registry","inputs":["filesystem","registry/scripts"],"actions":[f"map {m.source} -> {m.target}" for m in manifest.filesystem] or ["no explicit filesystem mappings declared"]},
        {"phase":"validate","inputs":["launch","runtime","prefix"],"actions":[f"verify launch entrypoint exists at {manifest.launch.entrypoint}","record dependency and source hashes","emit build logs and normalized manifest"]},
        {"phase":"seal-artifact","inputs":["prefix","runtime binding","manifest","metadata"],"actions":["mark bundle immutable","write provenance metadata","optionally map bundle into an OCI image layer layout"]},
    ]
def _dep(kind, verbs, name):
    if kind == "winetricks": return "install winetricks verbs: " + ", ".join(verbs)
    return f"install {kind}: {name}" if name else f"install dependency kind={kind}"
def _inst(kind, source, target, command):
    if kind == "script": return f"run script command: {command}"
    return f"install {kind} from {source} into {target}" if target else f"install {kind} from {source}"
