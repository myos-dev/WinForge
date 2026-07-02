"""BlueBuild-style module expansion for WinForge manifests."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import re
from typing import Any

MODULE_EXPANSION_SCHEMA_VERSION = "winforge.module-expansion/v0"

MODULE_FIELDS = {"type", "install"}
CHOCOLATEY_INSTALL_FIELDS = {"packages"}
CHOCOLATEY_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]*$")

CHOCOLATEY_SETUP_COMMAND = (
    'set -eu; '
    'pwsh="$WINEPREFIX/drive_c/Program Files/PowerShell/7/pwsh.exe"; '
    'wrapper="$WINEPREFIX/drive_c/windows/system32/WindowsPowerShell/v1.0/powershell.exe"; '
    'choco="$WINEPREFIX/drive_c/ProgramData/chocolatey/bin/choco.exe"; '
    'if [ -f "$choco" ] && [ -f "$wrapper" ]; then exit 0; fi; '
    'if ! command -v git >/dev/null 2>&1; then '
    '  apt-get update -qq && apt-get install -y -qq --no-install-recommends git gcc libc-dev pkg-config gcc-mingw-w64-x86-64; '
    'fi; '
    'if ! command -v cargo >/dev/null 2>&1; then '
    '  curl -fsSL https://sh.rustup.rs | sh -s -- -y --default-toolchain stable --profile minimal -q 2>/dev/null; '
    '  . "$HOME/.cargo/env"; '
    'fi; '
    'if command -v rustup >/dev/null; then rustup target add x86_64-pc-windows-gnu; fi; '
    'repo="$WINEPREFIX/drive_c/winforge/powershell-wrapper-for-wine"; '
    'rm -rf "$repo"; '
    'mkdir -p "$(dirname "$repo")"; '
    'git clone --depth=1 https://codeberg.org/Synchro/powershell-wrapper-for-wine.git "$repo"; '
    '(cd "$repo" && cargo run --package xtask -- build --arch 64); '
    'mkdir -p "$(dirname "$wrapper")"; '
    'cp "$repo"/target/x86_64-pc-windows-gnu/release/*.exe "$wrapper"; '
    'wine "$pwsh" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command '
    '"iex ((New-Object System.Net.WebClient).DownloadString(\'https://community.chocolatey.org/install.ps1\'))"'
)


class ModuleError(ValueError):
    pass


@dataclass(frozen=True)
class ModuleSpec:
    type: str
    install: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Any, index: int) -> "ModuleSpec":
        if not isinstance(data, dict):
            raise ModuleError(f"modules[{index}] must be an object")
        _reject_unknown(data, MODULE_FIELDS, f"modules[{index}]")
        module_type = _required_str(data, "type", f"modules[{index}].type")
        if module_type != "chocolatey":
            raise ModuleError("modules[%d].type must be one of: chocolatey" % index)
        install = _object(data.get("install", {}) or {}, f"modules[{index}].install")
        _parse_chocolatey_packages(install, index)
        return cls(module_type, {"packages": list(install.get("packages", []))})

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "install": {"packages": list(self.install.get("packages", []))}}


def apply_modules(data: dict[str, Any]) -> dict[str, Any]:
    """Expand BlueBuild-style modules into concrete dependencies/install steps."""
    result = deepcopy(data)
    raw_modules = result.get("modules", []) or []
    if not isinstance(raw_modules, list):
        raise ModuleError("modules must be a list")

    injected_dependencies: list[dict[str, Any]] = []
    injected_install: list[dict[str, Any]] = []
    expansions: list[dict[str, Any]] = []

    for index, raw_module in enumerate(raw_modules):
        module = ModuleSpec.from_dict(raw_module, index)
        if module.type == "chocolatey":
            packages = list(module.install["packages"])
            injected_dependencies.append({"kind": "winetricks", "verbs": ["powershell_core"]})
            injected_install.append({"kind": "script", "command": CHOCOLATEY_SETUP_COMMAND})
            for package in packages:
                injected_install.append({"kind": "choco", "command": "install", "args": [package, "-y", "--no-progress"]})
            expansions.append({
                "schemaVersion": MODULE_EXPANSION_SCHEMA_VERSION,
                "type": "chocolatey",
                "install": {"packages": packages},
                "injectedDependencies": [{"kind": "winetricks", "verbs": ["powershell_core"]}],
                "injectedInstallStepCount": 1 + len(packages),
            })

    if injected_dependencies:
        dependencies = result.get("dependencies", []) or []
        if not isinstance(dependencies, list):
            raise ModuleError("dependencies must be a list")
        result["dependencies"] = injected_dependencies + dependencies

    if injected_install:
        install = result.get("install", []) or []
        if not isinstance(install, list):
            raise ModuleError("install must be a list")
        result["install"] = injected_install + install

    if expansions:
        provenance = result.setdefault("provenance", {})
        if isinstance(provenance, dict):
            provenance["moduleExpansions"] = expansions
    return result


def _parse_chocolatey_packages(install: dict[str, Any], index: int) -> list[str]:
    _reject_unknown(install, CHOCOLATEY_INSTALL_FIELDS, f"modules[{index}].install")
    packages = install.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ModuleError(f"modules[{index}].install.packages must be a non-empty list")
    for package_index, package in enumerate(packages):
        if not isinstance(package, str) or not package or not CHOCOLATEY_PACKAGE_RE.fullmatch(package):
            raise ModuleError(
                f"modules[{index}].install.packages[{package_index}] must be a package name using letters, numbers, dot, underscore, plus, or dash"
            )
    return packages


def _reject_unknown(data: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ModuleError(f"unknown manifest field at {location}: " + ", ".join(unknown))


def _object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ModuleError(f"{location} must be an object")
    return value


def _required_str(data: dict[str, Any], key: str, location: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ModuleError(f"{location} is required")
    return value
