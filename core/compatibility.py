"""Compatibility policy normalization for WinForge recipes.

The compatibility policy is the stable layer above Wine internals. It records
application/runtime intent such as Windows version, WINEARCH, graphics backend,
DLL override policy, and environment variables without exposing raw loader-order
or trace internals as primary schema.
"""
from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = "winforge.compatibility-policy/v0"
ALLOWED_FIELDS = {"arch", "windowsVersion", "graphics", "dllPolicy", "env"}
ALLOWED_ARCHES = {"win32", "win64"}
ALLOWED_WINDOWS_VERSIONS = {
    "winxp", "winxp64", "win7", "win8", "win81", "win10", "win11",
}
ALLOWED_GRAPHICS_BACKENDS = {"auto", "wined3d", "dxvk", "vkd3d", "vkd3d-proton", "none"}
DLL_POLICY_ALIASES = {
    "disabled": "disabled",
    "disable": "disabled",
    "off": "disabled",
    "none": "disabled",
    "native": "native",
    "n": "native",
    "builtin": "builtin",
    "b": "builtin",
    "native,builtin": "native,builtin",
    "n,b": "native,builtin",
    "native-first": "native,builtin",
    "builtin,native": "builtin,native",
    "b,n": "builtin,native",
    "builtin-first": "builtin,native",
}
DLL_OVERRIDE_VALUES = {
    "disabled": "",
    "native": "n",
    "builtin": "b",
    "native,builtin": "n,b",
    "builtin,native": "b,n",
}
_DLL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CompatibilityPolicyError(ValueError):
    """Raised when a compatibility policy is invalid."""


def normalize_compatibility_policy(
    *,
    config: dict[str, Any] | None = None,
    compatibility: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return normalized first-class compatibility policy.

    Legacy `config.wine`/`config.graphics` values are accepted and normalized,
    but explicit `compatibility` fields override them. Returns `{}` when no
    policy was supplied or derivable so older recipes remain compact.
    """
    config = _object(config or {}, "config")
    explicit = _object(compatibility or {}, "compatibility")
    _reject_unknown(explicit, ALLOWED_FIELDS, "compatibility")

    merged: dict[str, Any] = {}
    legacy = _legacy_policy_from_config(config)
    merged.update(legacy)

    # Explicit top-level fields override legacy config values. Nested objects
    # replace rather than deep-merge so recipe authors get deterministic intent.
    for key in ("arch", "windowsVersion", "graphics", "dllPolicy", "env"):
        if key in explicit:
            merged[key] = explicit[key]

    if not merged:
        return {}

    normalized: dict[str, Any] = {"schemaVersion": SCHEMA_VERSION}
    if "arch" in merged:
        normalized["arch"] = _arch(merged["arch"])
    if "windowsVersion" in merged:
        normalized["windowsVersion"] = _windows_version(merged["windowsVersion"])
    if "graphics" in merged:
        graphics = _graphics(merged["graphics"])
        if graphics:
            normalized["graphics"] = graphics
    if "dllPolicy" in merged:
        dll_policy = _dll_policy(merged["dllPolicy"])
        if dll_policy:
            normalized["dllPolicy"] = dll_policy
    if "env" in merged:
        env = _env(merged["env"])
        if env:
            normalized["env"] = env

    return normalized if len(normalized) > 1 else {}


def compatibility_environment(policy: dict[str, Any] | None) -> dict[str, str]:
    """Return runtime/build environment variables implied by normalized policy."""
    if not policy:
        return {}
    env: dict[str, str] = {}
    if arch := policy.get("arch"):
        env["WINEARCH"] = str(arch)
    graphics = policy.get("graphics") or {}
    if backend := graphics.get("backend"):
        env["WINFORGE_GRAPHICS_BACKEND"] = str(backend)
    if fallback := graphics.get("fallback"):
        env["WINFORGE_GRAPHICS_FALLBACK"] = str(fallback)
    for key, value in (policy.get("env") or {}).items():
        env[str(key)] = str(value)
    overrides = compile_wine_dll_overrides(policy.get("dllPolicy") or {})
    if overrides:
        env["WINEDLLOVERRIDES"] = overrides
    return {key: env[key] for key in sorted(env)}


def compile_wine_dll_overrides(policy: dict[str, str] | None) -> str:
    """Compile normalized DLL policy into deterministic WINEDLLOVERRIDES."""
    if not policy:
        return ""
    normalized = _dll_policy(policy)
    parts: list[str] = []
    for dll in sorted(normalized):
        parts.append(f"{dll}={DLL_OVERRIDE_VALUES[normalized[dll]]}")
    return ";".join(parts)


def _legacy_policy_from_config(config: dict[str, Any]) -> dict[str, Any]:
    policy: dict[str, Any] = {}
    wine = config.get("wine")
    if isinstance(wine, dict):
        if "arch" in wine:
            policy["arch"] = wine["arch"]
        if "windowsVersion" in wine:
            policy["windowsVersion"] = wine["windowsVersion"]
        if "dllOverrides" in wine:
            policy["dllPolicy"] = wine["dllOverrides"]
    elif wine is not None:
        raise CompatibilityPolicyError("config.wine must be an object when present")

    graphics = config.get("graphics")
    if graphics is not None:
        policy["graphics"] = graphics

    env = config.get("env")
    if env is not None:
        policy["env"] = env
    return policy


def _arch(value: Any) -> str:
    if not isinstance(value, str) or value not in ALLOWED_ARCHES:
        raise CompatibilityPolicyError("compatibility.arch must be one of: " + ", ".join(sorted(ALLOWED_ARCHES)))
    return value


def _windows_version(value: Any) -> str:
    if not isinstance(value, str) or value not in ALLOWED_WINDOWS_VERSIONS:
        raise CompatibilityPolicyError(
            "compatibility.windowsVersion must be one of: " + ", ".join(sorted(ALLOWED_WINDOWS_VERSIONS))
        )
    return value


def _graphics(value: Any) -> dict[str, str]:
    graphics = _object(value, "compatibility.graphics")
    _reject_unknown(graphics, {"backend", "fallback"}, "compatibility.graphics")
    normalized: dict[str, str] = {}
    for key in ("backend", "fallback"):
        if key not in graphics:
            continue
        backend = graphics[key]
        if not isinstance(backend, str) or backend not in ALLOWED_GRAPHICS_BACKENDS:
            raise CompatibilityPolicyError(
                f"compatibility.graphics.{key} must be one of: "
                + ", ".join(sorted(ALLOWED_GRAPHICS_BACKENDS))
            )
        normalized[key] = backend
    return normalized


def _dll_policy(value: Any) -> dict[str, str]:
    policy = _object(value, "compatibility.dllPolicy")
    normalized: dict[str, str] = {}
    for dll, raw_policy in policy.items():
        if not isinstance(dll, str) or not _DLL_NAME_RE.fullmatch(dll):
            raise CompatibilityPolicyError("compatibility.dllPolicy keys must be DLL/module names")
        if not isinstance(raw_policy, str):
            raise CompatibilityPolicyError(f"compatibility.dllPolicy.{dll} must be a string")
        key = raw_policy.strip().lower().replace(" ", "")
        if key not in DLL_POLICY_ALIASES:
            raise CompatibilityPolicyError(
                f"compatibility.dllPolicy.{dll} must be one of: "
                + ", ".join(sorted(set(DLL_POLICY_ALIASES)))
            )
        normalized[dll] = DLL_POLICY_ALIASES[key]
    return {dll: normalized[dll] for dll in sorted(normalized)}


def _env(value: Any) -> dict[str, str]:
    env = _object(value, "compatibility.env")
    normalized: dict[str, str] = {}
    for key, raw_value in env.items():
        if not isinstance(key, str) or not _ENV_NAME_RE.fullmatch(key):
            raise CompatibilityPolicyError("compatibility.env keys must be shell-safe environment variable names")
        if not isinstance(raw_value, str):
            raise CompatibilityPolicyError(f"compatibility.env.{key} must be a string")
        normalized[key] = raw_value
    return {key: normalized[key] for key in sorted(normalized)}


def _object(value: Any, location: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CompatibilityPolicyError(f"{location} must be an object")
    return value


def _reject_unknown(data: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        label = "field" if len(unknown) == 1 else "fields"
        raise CompatibilityPolicyError(f"unknown {label} at {location}: " + ", ".join(unknown))
