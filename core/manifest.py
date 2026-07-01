"""Recipe/manifest schema and validation for WinForge v0."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from pathlib import Path
from typing import Any

from core.compatibility import CompatibilityPolicyError, normalize_compatibility_policy
from core.profiles import ProfileError, apply_profiles

SCHEMA_VERSION = "winforge.app/v0"
LEGACY_SCHEMA_VERSION = "winforge.dev/v0"
SUPPORTED_SCHEMA_VERSIONS = {SCHEMA_VERSION, LEGACY_SCHEMA_VERSION}

ALLOWED_RUNTIME_PROVIDERS = {"wine", "staging", "umu-proton-ge"}
ALLOWED_DEPENDENCY_KINDS = {"winetricks", "font", "directx", "package", "runtime-component"}
ALLOWED_INSTALL_KINDS = {"msi", "exe", "portable", "choco", "script"}

ROOT_FIELDS = {
    "schemaVersion",
    "name",
    "version",
    "runtime",
    "profiles",
    "sources",
    "dependencies",
    "install",
    "filesystem",
    "config",
    "compatibility",
    "registry",
    "launch",
    "entrypoints",
    "fileAssociations",
    "state",
    "exports",
    "provenance",
}
RUNTIME_FIELDS = {"provider", "version", "source", "channel", "digest"}
DEPENDENCY_FIELDS = {"kind", "verbs", "name", "version", "sha256"}
INSTALL_FIELDS = {"kind", "source", "sha256", "target", "command", "args"}
FILESYSTEM_FIELDS = {"source", "target", "sha256", "mode"}
LAUNCH_FIELDS = {"entrypoint", "args", "env", "workingDirectory"}
SOURCE_FIELDS = {"id", "name", "type", "policy", "url", "source", "path", "sha256", "description"}
ENTRYPOINT_FIELDS = {"id", "name", "executable", "args", "env", "workingDirectory"}
FILE_ASSOCIATION_FIELDS = {"entrypoint", "extensions", "mime"}
ALLOWED_SOURCE_TYPES = {"installer", "iso", "archive", "files", "prefix", "font", "other"}
ALLOWED_SOURCE_POLICIES = {"bring-your-own-files", "bring-your-own-installer", "bring-your-own-licensed-media", "bring-your-own-prefix", "redistributable", "synthetic-fixture", "external-local-file-required", "requires-local-installer-and-overlay", "class-marker-only"}
ALLOWED_FILE_MAPPING_MODES = {"copy", "merge"}


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class RuntimeSpec:
    provider: str
    version: str
    source: str | None = None
    channel: str | None = None
    digest: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]):
        _reject_unknown(data, RUNTIME_FIELDS, "runtime")
        provider = _required_str(data, "runtime.provider")
        version = _required_str(data, "runtime.version")
        if provider not in ALLOWED_RUNTIME_PROVIDERS:
            raise ManifestError("runtime.provider must be one of: " + ", ".join(sorted(ALLOWED_RUNTIME_PROVIDERS)))
        return cls(
            provider,
            version,
            _optional_str(data, "source"),
            _optional_str(data, "channel"),
            _optional_str(data, "digest"),
        )

    def to_dict(self):
        return _drop_none({
            "provider": self.provider,
            "version": self.version,
            "source": self.source,
            "channel": self.channel,
            "digest": self.digest,
        })


@dataclass(frozen=True)
class DependencySpec:
    kind: str
    verbs: list[str] = field(default_factory=list)
    name: str | None = None
    version: str | None = None
    sha256: str | None = None

    @classmethod
    def from_dict(cls, data, index):
        _reject_unknown(data, DEPENDENCY_FIELDS, f"dependencies[{index}]")
        kind = _required_str(data, f"dependencies[{index}].kind")
        if kind not in ALLOWED_DEPENDENCY_KINDS:
            raise ManifestError(
                f"dependencies[{index}].kind must be one of: " + ", ".join(sorted(ALLOWED_DEPENDENCY_KINDS))
            )
        verbs = data.get("verbs", []) or []
        if not isinstance(verbs, list) or not all(isinstance(x, str) and x for x in verbs):
            raise ManifestError(f"dependencies[{index}].verbs must be a list of non-empty strings")
        return cls(kind, verbs, _optional_str(data, "name"), _optional_str(data, "version"), _optional_str(data, "sha256"))

    def to_dict(self):
        return _drop_none({
            "kind": self.kind,
            "verbs": self.verbs,
            "name": self.name,
            "version": self.version,
            "sha256": self.sha256,
        })


@dataclass(frozen=True)
class InstallStep:
    kind: str
    source: str | None = None
    sha256: str | None = None
    target: str | None = None
    command: str | None = None
    args: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data, index):
        _reject_unknown(data, INSTALL_FIELDS, f"install[{index}]")
        kind = _required_str(data, f"install[{index}].kind")
        if kind not in ALLOWED_INSTALL_KINDS:
            raise ManifestError(f"install[{index}].kind must be one of: " + ", ".join(sorted(ALLOWED_INSTALL_KINDS)))
        args = data.get("args", []) or []
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ManifestError(f"install[{index}].args must be a list of strings")
        if kind in {"msi", "exe", "portable"} and not data.get("source"):
            raise ManifestError(f"install[{index}].source is required for {kind}")
        if kind == "script" and not data.get("command"):
            raise ManifestError(f"install[{index}].command is required for script")
        return cls(
            kind,
            _optional_str(data, "source"),
            _optional_str(data, "sha256"),
            _optional_str(data, "target"),
            _optional_str(data, "command"),
            args,
        )

    def to_dict(self):
        return _drop_none({
            "kind": self.kind,
            "source": self.source,
            "sha256": self.sha256,
            "target": self.target,
            "command": self.command,
            "args": self.args,
        })


@dataclass(frozen=True)
class FileMapping:
    source: str
    target: str
    sha256: str | None = None
    mode: str = "copy"

    @classmethod
    def from_dict(cls, data, index):
        _reject_unknown(data, FILESYSTEM_FIELDS, f"filesystem[{index}]")
        mode = _optional_str(data, "mode") or "copy"
        if mode not in ALLOWED_FILE_MAPPING_MODES:
            raise ManifestError(f"filesystem[{index}].mode must be one of: " + ", ".join(sorted(ALLOWED_FILE_MAPPING_MODES)))
        return cls(
            _required_str(data, f"filesystem[{index}].source"),
            _required_str(data, f"filesystem[{index}].target"),
            _optional_str(data, "sha256"),
            mode,
        )

    def to_dict(self):
        return _drop_none({"source": self.source, "target": self.target, "sha256": self.sha256, "mode": self.mode})


@dataclass(frozen=True)
class SourceDeclaration:
    id: str
    type: str
    policy: str
    url: str | None = None
    source: str | None = None
    path: str | None = None
    sha256: str | None = None
    description: str | None = None

    @classmethod
    def from_dict(cls, data, index):
        _reject_unknown(data, SOURCE_FIELDS, f"sources[{index}]")
        sid = _optional_str(data, "id") or _optional_str(data, "name")
        if not sid:
            raise ManifestError(f"sources[{index}].id is required")
        source_type = _optional_str(data, "type") or "other"
        if source_type not in ALLOWED_SOURCE_TYPES:
            raise ManifestError(f"sources[{index}].type must be one of: " + ", ".join(sorted(ALLOWED_SOURCE_TYPES)))
        policy = _optional_str(data, "policy") or "external-local-file-required"
        if policy not in ALLOWED_SOURCE_POLICIES:
            raise ManifestError(f"sources[{index}].policy must be one of: " + ", ".join(sorted(ALLOWED_SOURCE_POLICIES)))
        return cls(
            sid,
            source_type,
            policy,
            _optional_str(data, "url"),
            _optional_str(data, "source"),
            _optional_str(data, "path"),
            _optional_str(data, "sha256"),
            _optional_str(data, "description"),
        )

    @property
    def ref(self) -> str | None:
        return self.url or self.source or self.path

    def to_dict(self):
        return _drop_none({
            "id": self.id,
            "type": self.type,
            "policy": self.policy,
            "url": self.url,
            "source": self.source,
            "path": self.path,
            "sha256": self.sha256,
            "description": self.description,
        })

    def __getitem__(self, key: str) -> Any:
        # Backward-compatible read path for older code/tests that treated
        # manifest.sources entries as raw dictionaries. `name` aliases the new
        # normalized source id.
        data = self.to_dict()
        if key == "name":
            return self.id
        return data[key]

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


@dataclass(frozen=True)
class SuiteEntrypoint:
    id: str
    name: str
    executable: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    working_directory: str | None = None

    @classmethod
    def from_dict(cls, data, index):
        _reject_unknown(data, ENTRYPOINT_FIELDS, f"entrypoints[{index}]")
        args = data.get("args", []) or []
        env = data.get("env", {}) or {}
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ManifestError(f"entrypoints[{index}].args must be a list of strings")
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ManifestError(f"entrypoints[{index}].env must be an object with string keys and values")
        return cls(
            _required_str(data, f"entrypoints[{index}].id"),
            _required_str(data, f"entrypoints[{index}].name"),
            _required_str(data, f"entrypoints[{index}].executable"),
            args,
            env,
            _optional_str(data, "workingDirectory"),
        )

    def to_dict(self):
        return _drop_none({
            "id": self.id,
            "name": self.name,
            "executable": self.executable,
            "args": self.args,
            "env": self.env,
            "workingDirectory": self.working_directory,
        })


@dataclass(frozen=True)
class FileAssociation:
    entrypoint: str
    extensions: list[str] = field(default_factory=list)
    mime: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data, index):
        _reject_unknown(data, FILE_ASSOCIATION_FIELDS, f"fileAssociations[{index}]")
        extensions = data.get("extensions", []) or []
        mime = data.get("mime", []) or []
        if not isinstance(extensions, list) or not all(isinstance(x, str) and x.startswith(".") for x in extensions):
            raise ManifestError(f"fileAssociations[{index}].extensions must be a list of extensions like .docx")
        if not isinstance(mime, list) or not all(isinstance(x, str) and x for x in mime):
            raise ManifestError(f"fileAssociations[{index}].mime must be a list of MIME strings")
        return cls(_required_str(data, f"fileAssociations[{index}].entrypoint"), extensions, mime)

    def to_dict(self):
        return _drop_none({"entrypoint": self.entrypoint, "extensions": self.extensions, "mime": self.mime})


@dataclass(frozen=True)
class LaunchSpec:
    entrypoint: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    working_directory: str | None = None

    @classmethod
    def from_dict(cls, data):
        _reject_unknown(data, LAUNCH_FIELDS, "launch")
        args = data.get("args", []) or []
        env = data.get("env", {}) or {}
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args):
            raise ManifestError("launch.args must be a list of strings")
        if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise ManifestError("launch.env must be an object with string keys and values")
        return cls(_required_str(data, "launch.entrypoint"), args, env, _optional_str(data, "workingDirectory"))

    def to_dict(self):
        return _drop_none({
            "entrypoint": self.entrypoint,
            "args": self.args,
            "env": self.env,
            "workingDirectory": self.working_directory,
        })


@dataclass(frozen=True)
class Manifest:
    schema_version: str
    name: str
    version: str
    runtime: RuntimeSpec
    profiles: list[str]
    dependencies: list[DependencySpec]
    install: list[InstallStep]
    filesystem: list[FileMapping]
    launch: LaunchSpec
    provenance: dict[str, Any] = field(default_factory=dict)
    sources: list[SourceDeclaration] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)
    compatibility: dict[str, Any] = field(default_factory=dict)
    registry: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    exports: list[dict[str, Any]] = field(default_factory=list)
    entrypoints: list[SuiteEntrypoint] = field(default_factory=list)
    file_associations: list[FileAssociation] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ManifestError("manifest root must be an object")
        _reject_unknown(data, ROOT_FIELDS, "manifest")
        try:
            data = apply_profiles(data)
        except ProfileError as exc:
            raise ManifestError(str(exc)) from exc
        schema = _required_str(data, "schemaVersion")
        if schema not in SUPPORTED_SCHEMA_VERSIONS:
            raise ManifestError(
                "schemaVersion must be one of: " + ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
            )
        if not isinstance(data.get("runtime"), dict):
            raise ManifestError("runtime must be an object")
        if not isinstance(data.get("launch"), dict):
            raise ManifestError("launch must be an object")

        provenance = _object(data.get("provenance", {}) or {}, "provenance")
        config = _object(data.get("config", {}) or {}, "config")
        raw_compatibility = _object(data.get("compatibility", {}) or {}, "compatibility")
        try:
            compatibility = normalize_compatibility_policy(
                config=config,
                compatibility=raw_compatibility,
            )
        except CompatibilityPolicyError as exc:
            raise ManifestError(str(exc)) from exc
        state = _object(data.get("state", {}) or {}, "state")
        profiles = _string_list(data.get("profiles", []), "profiles")
        sources = [SourceDeclaration.from_dict(x, i) for i, x in enumerate(_list(data.get("sources", []), "sources"))]
        registry = _list(data.get("registry", []), "registry")
        exports = _list(data.get("exports", []), "exports")
        entrypoints = [SuiteEntrypoint.from_dict(x, i) for i, x in enumerate(_list(data.get("entrypoints", []), "entrypoints"))]
        _validate_entrypoint_ids(entrypoints)
        file_associations = [FileAssociation.from_dict(x, i) for i, x in enumerate(_list(data.get("fileAssociations", []), "fileAssociations"))]
        _validate_file_associations(file_associations, entrypoints)

        return cls(
            schema,
            _required_str(data, "name"),
            _required_str(data, "version"),
            RuntimeSpec.from_dict(data["runtime"]),
            profiles,
            [DependencySpec.from_dict(x, i) for i, x in enumerate(_list(data.get("dependencies", []), "dependencies"))],
            [InstallStep.from_dict(x, i) for i, x in enumerate(_list(data.get("install", []), "install"))],
            [FileMapping.from_dict(x, i) for i, x in enumerate(_list(data.get("filesystem", []), "filesystem"))],
            LaunchSpec.from_dict(data["launch"]),
            provenance,
            sources,
            config,
            compatibility,
            registry,
            state,
            exports,
            entrypoints,
            file_associations,
        )

    def to_dict(self):
        return _drop_none({
            "schemaVersion": self.schema_version,
            "name": self.name,
            "version": self.version,
            "runtime": self.runtime.to_dict(),
            "profiles": self.profiles,
            "sources": [x.to_dict() for x in self.sources],
            "dependencies": [x.to_dict() for x in self.dependencies],
            "install": [x.to_dict() for x in self.install],
            "filesystem": [x.to_dict() for x in self.filesystem],
            "config": self.config,
            "compatibility": self.compatibility,
            "registry": self.registry,
            "launch": self.launch.to_dict(),
            "entrypoints": [x.to_dict() for x in self.entrypoints],
            "fileAssociations": [x.to_dict() for x in self.file_associations],
            "state": self.state,
            "exports": self.exports,
            "provenance": self.provenance,
        })



def _validate_entrypoint_ids(entrypoints: list[SuiteEntrypoint]) -> None:
    seen: set[str] = set()
    for index, entrypoint in enumerate(entrypoints):
        if entrypoint.id in seen:
            raise ManifestError(f"entrypoints[{index}].id is duplicated: {entrypoint.id}")
        seen.add(entrypoint.id)


def _validate_file_associations(associations: list[FileAssociation], entrypoints: list[SuiteEntrypoint]) -> None:
    ids = {entrypoint.id for entrypoint in entrypoints}
    if not ids and associations:
        raise ManifestError("fileAssociations require entrypoints")
    for index, association in enumerate(associations):
        if association.entrypoint not in ids:
            raise ManifestError(f"fileAssociations[{index}].entrypoint references unknown entrypoint: {association.entrypoint}")


def load_manifest(path: Path):
    suffix = path.suffix.lower()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ManifestError(f"file not found: {path}") from exc

    if suffix in {".yaml", ".yml"}:
        data = _load_strict_yaml(text)
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ManifestError(f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc
    return Manifest.from_dict(data)


# ---- strict dependency-light YAML subset ---------------------------------

@dataclass(frozen=True)
class _Token:
    indent: int
    content: str
    line: int


def _load_strict_yaml(text: str) -> Any:
    tokens = _tokenize_yaml(text)
    if not tokens:
        raise ManifestError("YAML document is empty")
    value, index = _parse_yaml_block(tokens, 0, tokens[0].indent)
    if index != len(tokens):
        token = tokens[index]
        raise ManifestError(f"unexpected YAML content at line {token.line}: {token.content}")
    return value


def _tokenize_yaml(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw:
            raise ManifestError(f"tabs are not allowed in YAML indentation at line {line_no}")
        stripped_comment = _strip_yaml_comment(raw).rstrip()
        if not stripped_comment.strip():
            continue
        indent = len(stripped_comment) - len(stripped_comment.lstrip(" "))
        if indent % 2 != 0:
            raise ManifestError(f"YAML indentation must use multiples of two spaces at line {line_no}")
        content = stripped_comment.strip()
        if content in {"---", "..."}:
            continue
        _reject_yaml_anchors_aliases_merge(content, line_no)
        tokens.append(_Token(indent, content, line_no))
    return tokens


def _parse_yaml_block(tokens: list[_Token], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(tokens):
        return {}, index
    token = tokens[index]
    if token.indent < indent:
        return {}, index
    if token.indent != indent:
        raise ManifestError(f"unexpected YAML indentation at line {token.line}")
    if token.content.startswith("- ") or token.content == "-":
        return _parse_yaml_list(tokens, index, indent)
    return _parse_yaml_mapping(tokens, index, indent)


def _parse_yaml_mapping(tokens: list[_Token], index: int, indent: int) -> tuple[dict[str, Any], int]:
    result: dict[str, Any] = {}
    while index < len(tokens):
        token = tokens[index]
        if token.indent < indent:
            break
        if token.indent != indent:
            raise ManifestError(f"unexpected YAML indentation at line {token.line}")
        if token.content.startswith("- ") or token.content == "-":
            break
        key, raw_value = _split_yaml_key_value(token)
        if key in result:
            raise ManifestError(f"duplicate YAML key {key!r} at line {token.line}")
        index += 1
        if raw_value == "":
            if index < len(tokens) and tokens[index].indent > indent:
                value, index = _parse_yaml_block(tokens, index, tokens[index].indent)
            else:
                value = {}
        else:
            value = _parse_yaml_scalar(raw_value, token.line)
        result[key] = value
    return result, index


def _parse_yaml_list(tokens: list[_Token], index: int, indent: int) -> tuple[list[Any], int]:
    result: list[Any] = []
    while index < len(tokens):
        token = tokens[index]
        if token.indent < indent:
            break
        if token.indent != indent:
            raise ManifestError(f"unexpected YAML indentation at line {token.line}")
        if not (token.content.startswith("- ") or token.content == "-"):
            break

        rest = token.content[1:].strip()
        index += 1
        if rest == "":
            if index < len(tokens) and tokens[index].indent > indent:
                value, index = _parse_yaml_block(tokens, index, tokens[index].indent)
            else:
                value = None
            result.append(value)
            continue

        if _looks_like_yaml_key_value(rest):
            key, raw_value = _split_yaml_key_value(_Token(indent + 2, rest, token.line))
            item: dict[str, Any] = {}
            item[key] = _parse_yaml_scalar(raw_value, token.line) if raw_value else {}
            if index < len(tokens) and tokens[index].indent > indent:
                child, index = _parse_yaml_block(tokens, index, tokens[index].indent)
                if not isinstance(child, dict):
                    raise ManifestError(f"YAML list mapping item at line {token.line} must contain mapping children")
                for child_key, child_value in child.items():
                    if child_key in item:
                        raise ManifestError(f"duplicate YAML key {child_key!r} at line {token.line}")
                    item[child_key] = child_value
            result.append(item)
        else:
            result.append(_parse_yaml_scalar(rest, token.line))
            if index < len(tokens) and tokens[index].indent > indent:
                raise ManifestError(f"YAML scalar list item at line {token.line} cannot have nested children")
    return result, index


def _split_yaml_key_value(token: _Token) -> tuple[str, str]:
    if ":" not in token.content:
        raise ManifestError(f"expected YAML key/value pair at line {token.line}")
    key, raw_value = token.content.split(":", 1)
    key = key.strip()
    if not key:
        raise ManifestError(f"empty YAML key at line {token.line}")
    if key == "<<":
        raise ManifestError("YAML anchors, aliases, and merge keys are not supported")
    return key, raw_value.strip()


def _looks_like_yaml_key_value(value: str) -> bool:
    if value.startswith(("'", '"')):
        return False
    if ":" not in value:
        return False
    key, _ = value.split(":", 1)
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", key.strip()))


def _parse_yaml_scalar(value: str, line: int) -> Any:
    if value == "":
        return ""
    if value == "[]":
        return []
    if value == "{}":
        return {}
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_yaml_scalar(part.strip(), line) for part in _split_inline_csv(inner)]
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return _parse_quoted_yaml_scalar(value, line)
    if value.startswith(("&", "*")):
        raise ManifestError("YAML anchors, aliases, and merge keys are not supported")
    return value


def _parse_quoted_yaml_scalar(value: str, line: int) -> str:
    quote = value[0]
    body = value[1:-1]
    if quote == "'":
        return body.replace("''", "'")
    try:
        return bytes(body, "utf-8").decode("unicode_escape")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"invalid quoted YAML scalar at line {line}: {exc}") from exc


def _split_inline_csv(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False
    for char in value:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == "\\" and quote == '"':
            current.append(char)
            escape = True
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            current.append(char)
            continue
        if char == ",":
            parts.append("".join(current).strip())
            current = []
            continue
        current.append(char)
    if quote:
        raise ManifestError("unterminated quoted scalar in inline YAML list")
    parts.append("".join(current).strip())
    return parts


def _strip_yaml_comment(raw: str) -> str:
    quote: str | None = None
    escape = False
    result: list[str] = []
    for char in raw:
        if escape:
            result.append(char)
            escape = False
            continue
        if char == "\\" and quote == '"':
            result.append(char)
            escape = True
            continue
        if quote:
            result.append(char)
            if char == quote:
                quote = None
            continue
        if char in {'"', "'"}:
            quote = char
            result.append(char)
            continue
        if char == "#":
            break
        result.append(char)
    return "".join(result)


def _reject_yaml_anchors_aliases_merge(content: str, line: int) -> None:
    if content.startswith("<<:"):
        raise ManifestError("YAML anchors, aliases, and merge keys are not supported")
    # Reject YAML anchors and aliases without treating Windows paths or ordinary
    # words as special. This intentionally supports only a strict recipe subset.
    if re.search(r"(^|[\s:])([&*][A-Za-z0-9_-]+)(\s|$)", content):
        raise ManifestError("YAML anchors, aliases, and merge keys are not supported")


# ---- validation helpers ---------------------------------------------------

def _reject_unknown(data: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        label = "field" if len(unknown) == 1 else "fields"
        raise ManifestError(f"unknown manifest {label} at {location}: " + ", ".join(unknown))


def _required_str(data, key):
    value = data.get(key.split(".")[-1])
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key} must be a non-empty string")
    return value


def _optional_str(data, key):
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key} must be a non-empty string when present")
    return value


def _list(value, key):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, dict) for x in value):
        raise ManifestError(f"{key} must be a list of objects")
    return value


def _string_list(value, key):
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise ManifestError(f"{key} must be a list of non-empty strings")
    return value


def _object(value, key):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ManifestError(f"{key} must be an object")
    return value


def _drop_none(data):
    return {k: v for k, v in data.items() if v is not None and v != [] and v != {}}
