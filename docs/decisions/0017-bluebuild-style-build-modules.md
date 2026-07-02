# 0017. BlueBuild-style build modules

Status: accepted / implemented
Date: 2026-07-02
Owner: WinForge technical direction

## Decision

WinForge will model reusable build-time package-manager/tooling chains as top-level BlueBuild-style `modules[]` entries rather than primarily as raw `install[]` steps or profiles.

The first implemented module is Chocolatey:

```yaml
modules:
  - type: chocolatey
    install:
      packages:
        - firefox
        - 7zip.install
```

This deliberately mirrors the myOS/BlueBuild DNF pattern:

```yaml
modules:
  - type: dnf
    install:
      packages:
        - gcc
```

## Rationale

Chocolatey is not a single installer step. It needs a prerequisite chain inside the Wine prefix:

1. PowerShell Core via Winetricks `powershell_core`.
2. `powershell-wrapper-for-wine` so legacy `powershell.exe` invocations forward to `pwsh.exe`.
3. Chocolatey bootstrap through PowerShell.
4. Package install commands.

Putting that chain behind `modules[].type: chocolatey` keeps recipes declarative and lets WinForge own the setup/install logic. Raw `install.kind: choco` remains only the lowered internal build-step representation.

## Consequences

- Recipe schema now includes `modules[]`.
- Module expansion happens at manifest load/plan time and records `provenance.moduleExpansions`.
- The initial built-in module registry is Python-backed in `core/modules.py`.
- A future external registry can move definitions to `modules/<name>/module.yaml` without changing the user-facing YAML shape.
- Build containers need network access for Chocolatey; runtime containers remain air-gapped by default through `runtime.network: none`.

## Rejected alternatives

- **Profile-based Chocolatey**: profiles are static compatibility/dependency defaults and do not model parameterized package installation well.
- **User-authored `install.kind: choco` as the primary API**: this repeats setup concerns and is less like the myOS/BlueBuild module model.
- **Containerfile-only Chocolatey**: this hides Wine-prefix setup inside image layers and does not fit WinForge's application artifact build model.

## Review triggers

Revisit this decision if WinForge adds external module registries, module version pinning, offline package sources, or a pre-baked Chocolatey runtime/build image.
