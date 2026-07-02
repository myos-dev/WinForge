# WinForge Production Hardening Roadmap

Status: partially implemented — runtime network isolation implemented; Chocolatey/module work proposed
Date: 2026-07-02

## Objective

Move WinForge from "working tool" toward "production-ready platform" by hardening the networking model, introducing a module system for build-time tooling, and proving the architecture with Chocolatey as the first module.

These changes are independent of the [legacy-installer-debugging-backlog](legacy-installer-debugging-backlog.md) — they address the runtime security and build-automation layers, not individual installer debugging workflows.

---

## Theme 1: Runtime Network Isolation

### Problem

Without explicit runtime network isolation, a deployed Win32 application inside Wine can reach the internet, scan the LAN, or beacon out — undermining the air-gapped security model that makes legacy-Wine-in-containers attractive.

### Design

| Phase | Network default | Rationale |
|---|---|---|
| **Build** | Default bridge (unchanged) | Needs internet to git clone wrappers, run `choco install`, download winetricks verbs |
| **Runtime** | `--net=none` (new default) | Win32 app inside Wine should have zero network access |

### Escape hatch

Some runtime scenarios need local connectivity (host database, local printer service), or interactive VNC/noVNC access. Two mechanisms:

1. **CLI flag** — `winforge run my-app --network host|bridge|none`
2. **Manifest field** — `runtime.network` recorded in bundle graph metadata so intent survives packaging

The manifest field captures *intent*; the CLI flag allows the operator to *override* at deployment time for extra hardening. Bundle verification requires `manifest.runtime.network` to match `metadata/graph.json` `runnerRuntime.network` so graph tampering cannot silently escalate a default air-gapped bundle to host networking. Local VNC/noVNC runs are intentionally limited to `--network bridge` so Docker/Podman host-port publishing can bind access to loopback; the VNC helpers still listen inside the container, so bridge-mode VNC should not be attached to an untrusted/shared container network. `none` is non-interactive air-gap mode and `host` is rejected for VNC.

### Implemented changes

| File | Change |
|---|---|
| `core/manifest.py` | Add `network` field to `RuntimeSpec` (default: `"none"`) |
| `runtime/launcher.py` | `_container_argv()` emits `--net none` by default, reads overrides |
| `artifact/graph.py` | Carries `network` into `metadata/graph.json` under `runnerRuntime.network` |
| `artifact/kube.py` | Set `hostNetwork` or emit `NetworkPolicy` based on graph metadata |
| `winforge/cli.py` | `winforge run --network <mode>` flag |

### Implemented acceptance criteria

- `winforge run my-app` starts container with `--net none` by default
- `winforge run my-app --network host` uses host networking for headless runs
- `winforge run my-app --graphics vnc --network bridge` keeps host-published VNC/noVNC access loopback-bound
- VNC with `network: none` or `network: host` is rejected instead of producing a broken or exposed plan
- Bundle graph records `network: "none"` for default builds
- `winforge export kube` emits appropriate network config, with deny-egress policy for `network: none` when the cluster CNI enforces NetworkPolicy
- Existing build containers are unaffected (keep default networking)

---

## Theme 2: Chocolatey Integration

### Status

Implemented as a BlueBuild-style build-time module, patterned after myOS `type: dnf` layers.

### Problem

Chocolatey has a prerequisite chain: PowerShell Core, `powershell-wrapper-for-wine`, and the Chocolatey bootstrapper must exist inside the Wine prefix before package installation can work. Modeling that as hand-authored raw `install.kind: choco` steps makes recipe authors repeat setup logic and makes the YAML unlike the module-oriented myOS/BlueBuild recipe style.

### Implemented design: `modules[].type: chocolatey`

Recipes declare package-manager intent as a top-level module:

```yaml
schemaVersion: winforge.app/v0
name: my-app
version: "1.0.0"
runtime:
  provider: wine
  version: latest

modules:
  - type: chocolatey
    install:
      packages:
        - firefox
        - 7zip.install
```

This follows the same shape as myOS DNF layers:

```yaml
modules:
  - type: dnf
    install:
      packages:
        - gcc
```

The module resolver lowers the declaration into concrete build behavior:

1. `winetricks powershell_core` prerequisite in the dependency phase.
2. idempotent setup script that builds `powershell-wrapper-for-wine` and bootstraps Chocolatey through `pwsh.exe`.
3. one internal `install.kind: choco` step per package, generated as `choco install <package> -y --no-progress`.

`install.kind: choco` is therefore supported as the internal lowered form, but the public recipe shape should prefer `modules: - type: chocolatey`.

### Implemented changes

| File | Change |
|---|---|
| `core/modules.py` | BlueBuild-style module parsing/expansion and Chocolatey package validation |
| `core/manifest.py` | Adds `modules[]`, preserves module declarations, records module expansions, and validates lowered `choco` steps |
| `builder/pipeline.py` | Generates safe PowerShell/Chocolatey build-script commands from lowered `choco` steps |
| `examples/chocolatey-firefox.winforge.yaml` | Public-safe module recipe example |
| `tests/test_chocolatey_module.py` | Schema, YAML, validation, and build-script coverage |

### Implemented acceptance criteria

- `modules: - type: chocolatey` loads from strict YAML.
- `modules[].install.packages` expands into `powershell_core`, wrapper/bootstrap setup, and package install steps.
- Package names are validated so shell-like strings are rejected before build-script generation.
- Lowered `choco` install steps generate PowerShell array invocation via `& choco @chocoArgs` instead of raw shell concatenation.
- Direct malformed `install.kind: choco` steps fail closed instead of silently doing nothing.
- Runtime containers remain network-isolated by default; Chocolatey is build-time only.

### Remaining proposed work

- First-class module registry files under `modules/<name>/module.yaml` instead of built-in Python definitions.
- Module version pinning and shared dependency deduplication.
- Offline/pre-cached Chocolatey package mode for environments that cannot allow networked builds.
- Pre-baked `winforge-wine-choco` runtime/build image with pwsh, wrapper, and Chocolatey already installed to avoid repeated Rust wrapper builds.

## Theme 3: End-to-End Production Architecture

Once Themes 1 and 2 are complete, WinForge's architecture matches the Gemini-described model:

```
┌─────────────────────────────────────────────────────────┐
│ BUILD PHASE (default networking)                        │
│                                                         │
│  1. Pull base Wine image (or pre-baked choco image)     │
│  2. Initialize Wine prefix                              │
│  3. Install pwsh + PowerShell wrapper for Wine          │
│  4. Bootstrap Chocolatey                                │
│  5. choco install <packages>                            │
│  6. Install application (exe/msi/portable)              │
│  7. Freeze → OCI image                                  │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────┐
│ RUNTIME PHASE (--net=none, air-gapped)                  │
│                                                         │
│  Ingress Pod → shared volume → WinForge App Container   │
│                                   (processes data       │
│                                    via Win32/Wine)      │
│  shared volume → Egress Pod                             │
│                                                         │
│  Application has zero network access.                   │
│  All data flow is through volume mounts.                │
└─────────────────────────────────────────────────────────┘
```

---

## Sequencing

| Order | Theme | Effort | Dependencies | Delivers |
|---|---|---|---|---|
| 1 | Runtime `--net none` default | Small (1–2 files + tests) | None | Implemented — immediate security hardening |
| 2 | BlueBuild-style Chocolatey module | Medium (module resolver + pipeline + tests) | None | Implemented — recipe authors declare `modules: - type: chocolatey` |
| 3 | Network escape hatch (manifest field + CLI flag) | Small (manifest + launcher + kube) | Theme 1 (parallel ok) | Implemented — overridable isolation |
| 4 | External module registry | Medium (module.yaml resolver) | Built-in Chocolatey module proves the pattern | Proposed — cleaner abstraction |
| 5 | Pre-baked chocolatey runtime image | Medium (Dockerfile + CI + GHCR push) | Theme 2 | Proposed — faster builds |

## Review triggers

Create or update an ADR if any theme changes:
- The recipe schema (new `modules[]` field, `runtime.network`)
- The runtime container trust boundary (`--net=none` default)
- The base runtime image set (new chocolatey-baked provider image)
- The build artifact contract (network mode in graph metadata)
