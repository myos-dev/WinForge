"""Recipe profile expansion for WinForge.

Profiles are declarative shortcuts that expand into ordinary recipe fields. They
must not hide the final concrete compatibility/dependency policy: Manifest stores
both requested profile names and expanded concrete fields.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

PROFILE_EXPANSION_SCHEMA_VERSION = "winforge.profile-expansion/v0"

OFFICE_LEGACY_32BIT_VERBS = [
    "allfonts",
    "dotnet40",
    "gdiplus",
    # Gecko appears in upstream Bottles/Rustring evidence as a runtime
    # component, but current winetricks rejects it as an unknown verb.
    # Do not emit it in a winetricks verb list.
    "riched20",
    "msxml4",
    "mspatcha",
]

PROFILE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "office-legacy-32bit": {
        "description": "Legacy Microsoft Office 2007/2010/2013/2016 baseline from Bottles/PlayOnLinux compatibility evidence.",
        "compatibility": {
            "arch": "win32",
            "windowsVersion": "win7",
            "dllPolicy": {
                "gdiplus": "native,builtin",
                "riched20": "native,builtin",
            },
        },
        "dependencies": [
            {"kind": "winetricks", "verbs": OFFICE_LEGACY_32BIT_VERBS},
        ],
    },
}


class ProfileError(ValueError):
    pass


def apply_profiles(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of recipe data with requested profiles expanded.

    Explicit recipe fields win over profile defaults. For nested compatibility
    maps, profile defaults are merged first and explicit values override them;
    DLL policy keys merge independently so an explicit riched20 policy does not
    erase profile-provided gdiplus.
    """
    result = deepcopy(data)
    profiles = result.get("profiles", []) or []
    if not isinstance(profiles, list) or not all(isinstance(item, str) and item for item in profiles):
        raise ProfileError("profiles must be a list of non-empty strings")

    expansions: list[dict[str, Any]] = []
    for profile in profiles:
        definition = PROFILE_DEFINITIONS.get(profile)
        if definition is None:
            raise ProfileError(f"unknown profile: {profile}")
        result["compatibility"] = _merge_compatibility(
            deepcopy(definition.get("compatibility") or {}),
            result.get("compatibility") or {},
        )
        result["dependencies"] = _merge_dependencies(
            deepcopy(definition.get("dependencies") or []),
            result.get("dependencies") or [],
        )
        expansions.append({
            "profile": profile,
            "schemaVersion": PROFILE_EXPANSION_SCHEMA_VERSION,
            "description": definition.get("description", ""),
        })

    if expansions:
        provenance = result.setdefault("provenance", {})
        if isinstance(provenance, dict):
            provenance["profileExpansions"] = expansions
    return result


def _merge_compatibility(defaults: dict[str, Any], explicit: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(defaults)
    for key, value in explicit.items():
        if key == "dllPolicy" and isinstance(value, dict):
            base = dict(merged.get("dllPolicy") or {})
            base.update(value)
            merged["dllPolicy"] = base
        elif key == "graphics" and isinstance(value, dict):
            base = dict(merged.get("graphics") or {})
            base.update(value)
            merged["graphics"] = base
        elif key == "env" and isinstance(value, dict):
            base = dict(merged.get("env") or {})
            base.update(value)
            merged["env"] = base
        else:
            merged[key] = value
    return merged


def _merge_dependencies(defaults: list[dict[str, Any]], explicit: list[Any]) -> list[Any]:
    if not isinstance(explicit, list):
        raise ProfileError("dependencies must be a list")
    merged = deepcopy(defaults)
    for dep in explicit:
        merged.append(dep)
    return merged
