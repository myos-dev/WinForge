"""Manifest schema and validation for WinForge v0."""
from __future__ import annotations
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any
SCHEMA_VERSION = "winforge.dev/v0"
ALLOWED_RUNTIME_PROVIDERS = {"wine", "staging", "proton-ge"}
ALLOWED_DEPENDENCY_KINDS = {"winetricks", "font", "directx", "package", "runtime-component"}
ALLOWED_INSTALL_KINDS = {"msi", "exe", "portable", "choco", "script"}
class ManifestError(ValueError): pass
@dataclass(frozen=True)
class RuntimeSpec:
    provider: str; version: str; source: str|None=None; channel: str|None=None; digest: str|None=None
    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        provider = _required_str(data, "runtime.provider"); version = _required_str(data, "runtime.version")
        if provider not in ALLOWED_RUNTIME_PROVIDERS:
            raise ManifestError("runtime.provider must be one of: " + ", ".join(sorted(ALLOWED_RUNTIME_PROVIDERS)))
        return cls(provider, version, _optional_str(data,"source"), _optional_str(data,"channel"), _optional_str(data,"digest"))
    def to_dict(self): return _drop_none({"provider":self.provider,"version":self.version,"source":self.source,"channel":self.channel,"digest":self.digest})
@dataclass(frozen=True)
class DependencySpec:
    kind: str; verbs: list[str]=field(default_factory=list); name: str|None=None; version: str|None=None; sha256: str|None=None
    @classmethod
    def from_dict(cls, data, index):
        kind = _required_str(data, f"dependencies[{index}].kind")
        if kind not in ALLOWED_DEPENDENCY_KINDS: raise ManifestError(f"dependencies[{index}].kind must be one of: " + ", ".join(sorted(ALLOWED_DEPENDENCY_KINDS)))
        verbs = data.get("verbs", []) or []
        if not isinstance(verbs, list) or not all(isinstance(x,str) and x for x in verbs): raise ManifestError(f"dependencies[{index}].verbs must be a list of non-empty strings")
        return cls(kind, verbs, _optional_str(data,"name"), _optional_str(data,"version"), _optional_str(data,"sha256"))
    def to_dict(self): return _drop_none({"kind":self.kind,"verbs":self.verbs,"name":self.name,"version":self.version,"sha256":self.sha256})
@dataclass(frozen=True)
class InstallStep:
    kind: str; source: str|None=None; sha256: str|None=None; target: str|None=None; command: str|None=None; args: list[str]=field(default_factory=list)
    @classmethod
    def from_dict(cls, data, index):
        kind = _required_str(data, f"install[{index}].kind")
        if kind not in ALLOWED_INSTALL_KINDS: raise ManifestError(f"install[{index}].kind must be one of: " + ", ".join(sorted(ALLOWED_INSTALL_KINDS)))
        args = data.get("args", []) or []
        if not isinstance(args, list) or not all(isinstance(x,str) for x in args): raise ManifestError(f"install[{index}].args must be a list of strings")
        if kind in {"msi","exe","portable"} and not data.get("source"): raise ManifestError(f"install[{index}].source is required for {kind}")
        if kind == "script" and not data.get("command"): raise ManifestError(f"install[{index}].command is required for script")
        return cls(kind, _optional_str(data,"source"), _optional_str(data,"sha256"), _optional_str(data,"target"), _optional_str(data,"command"), args)
    def to_dict(self): return _drop_none({"kind":self.kind,"source":self.source,"sha256":self.sha256,"target":self.target,"command":self.command,"args":self.args})
@dataclass(frozen=True)
class FileMapping:
    source: str; target: str; sha256: str|None=None
    @classmethod
    def from_dict(cls, data, index): return cls(_required_str(data,f"filesystem[{index}].source"), _required_str(data,f"filesystem[{index}].target"), _optional_str(data,"sha256"))
    def to_dict(self): return _drop_none({"source":self.source,"target":self.target,"sha256":self.sha256})
@dataclass(frozen=True)
class LaunchSpec:
    entrypoint: str; args: list[str]=field(default_factory=list); env: dict[str,str]=field(default_factory=dict); working_directory: str|None=None
    @classmethod
    def from_dict(cls, data):
        args = data.get("args", []) or []; env = data.get("env", {}) or {}
        if not isinstance(args, list) or not all(isinstance(x,str) for x in args): raise ManifestError("launch.args must be a list of strings")
        if not isinstance(env, dict) or not all(isinstance(k,str) and isinstance(v,str) for k,v in env.items()): raise ManifestError("launch.env must be an object with string keys and values")
        return cls(_required_str(data,"launch.entrypoint"), args, env, _optional_str(data,"workingDirectory"))
    def to_dict(self): return _drop_none({"entrypoint":self.entrypoint,"args":self.args,"env":self.env,"workingDirectory":self.working_directory})
@dataclass(frozen=True)
class Manifest:
    schema_version: str; name: str; version: str; runtime: RuntimeSpec; dependencies: list[DependencySpec]; install: list[InstallStep]; filesystem: list[FileMapping]; launch: LaunchSpec; provenance: dict[str,Any]=field(default_factory=dict)
    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict): raise ManifestError("manifest root must be an object")
        schema = _required_str(data, "schemaVersion")
        if schema != SCHEMA_VERSION: raise ManifestError(f"schemaVersion must be {SCHEMA_VERSION!r}")
        if not isinstance(data.get("runtime"), dict): raise ManifestError("runtime must be an object")
        if not isinstance(data.get("launch"), dict): raise ManifestError("launch must be an object")
        provenance = data.get("provenance", {}) or {}
        if not isinstance(provenance, dict): raise ManifestError("provenance must be an object")
        return cls(schema, _required_str(data,"name"), _required_str(data,"version"), RuntimeSpec.from_dict(data["runtime"]), [DependencySpec.from_dict(x,i) for i,x in enumerate(_list(data.get("dependencies",[]),"dependencies"))], [InstallStep.from_dict(x,i) for i,x in enumerate(_list(data.get("install",[]),"install"))], [FileMapping.from_dict(x,i) for i,x in enumerate(_list(data.get("filesystem",[]),"filesystem"))], LaunchSpec.from_dict(data["launch"]), provenance)
    def to_dict(self): return {"schemaVersion":self.schema_version,"name":self.name,"version":self.version,"runtime":self.runtime.to_dict(),"dependencies":[x.to_dict() for x in self.dependencies],"install":[x.to_dict() for x in self.install],"filesystem":[x.to_dict() for x in self.filesystem],"launch":self.launch.to_dict(),"provenance":self.provenance}
def load_manifest(path: Path):
    if path.suffix.lower() in {".yaml",".yml"}: raise ManifestError("YAML authoring is part of v0, but this dependency-free scaffold currently loads normalized .json manifests")
    try: data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc: raise ManifestError(f"file not found: {path}") from exc
    except json.JSONDecodeError as exc: raise ManifestError(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    return Manifest.from_dict(data)
def _required_str(data, key):
    value = data.get(key.split('.')[-1])
    if not isinstance(value, str) or not value.strip(): raise ManifestError(f"{key} must be a non-empty string")
    return value
def _optional_str(data, key):
    value = data.get(key)
    if value is None: return None
    if not isinstance(value, str) or not value.strip(): raise ManifestError(f"{key} must be a non-empty string when present")
    return value
def _list(value, key):
    if value is None: return []
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value): raise ManifestError(f"{key} must be a list of objects")
    return value
def _drop_none(data): return {k:v for k,v in data.items() if v is not None and v != [] and v != {}}
