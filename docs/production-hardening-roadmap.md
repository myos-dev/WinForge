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

### Problem

Chocolatey is declared as an allowed install kind (`ALLOWED_INSTALL_KINDS` includes `"choco"`) but has zero implementation — the build script generator doesn't handle it. More broadly, every build-time tool with a prerequisite chain (pwsh, wrapper, chocolatey bootstrapper) forces recipe authors to wire up those deps manually.

### Design: Module System (Phase 1 — Profile-based)

Following [BlueBuild's module pattern](https://blue-build.org/reference/modules/), a module is a self-contained unit that knows its own prerequisites and can be declared in the recipe.

**Phase 1** uses the existing `profiles` system — low schema risk, no new resolver. A `chocolatey` profile injects pwsh + wrapper dependencies, and `kind: choco` install steps handle package installation.

```yaml
# Phase 1 recipe
schemaVersion: winforge.app/v0
name: my-app
version: "1.0.0"
runtime:
  provider: wine
  version: latest

profiles:
  - chocolatey

install:
  - kind: choco
    command: install firefox
  - kind: choco
    command: install 7zip.install
```

#### Prerequisite chain (handled by the profile)

1. `winetricks powershell_core` — installs pwsh.exe into the prefix
2. Build powershell-wrapper-for-wine from source (Rust cross-compile to `x86_64-pc-windows-gnu` target) — provides the `powershell.exe` shim that forwards to pwsh
3. Chocolatey bootstrap via PowerShell: `iex ((New-Object System.Net.WebClient).DownloadString('https://community.chocolatey.org/install.ps1'))`

Steps 1–2 are encoded by the `powershell-wrapper-pwsh-vnc.winforge.yaml` example recipe and are covered by strict load, dry-run bundle, bundle verification, and VNC run-plan tests. Step 3, Chocolatey bootstrap, remains proposed implementation work for the Chocolatey profile/module slice. A real Wine/container build remains live validation because it depends on network access, Winetricks, Git, Cargo, and the Rust Windows GNU target.

### Design: Module System (Phase 2 — First-class modules)

After proving the pattern, promote modules to a first-class manifest section:

```yaml
# Phase 2 recipe
schemaVersion: winforge.app/v0
name: my-app
version: "1.0.0"

modules:
  - name: chocolatey
    packages:
      - firefox
      - 7zip.install
```

A module resolver expands declarations into concrete dependencies and install steps, same as profiles but with richer semantics (params, lifecycle phases, version constraints).

### Design: Pre-baked runtime image (Phase 3)

Build a `winforge-wine-choco` runtime image on GHCR with pwsh + wrapper + chocolatey pre-installed. Eliminates the 2–5 minute wrapper build from every recipe's build time. The profile/module just selects the correct base image instead of building from source.

### Changes required

| Phase | File | Change |
|---|---|---|
| **1** | `core/profiles.py` | Add `chocolatey` profile definition (pwsh + wrapper deps) |
| **1** | `builder/pipeline.py` | Add `kind: choco` handler that runs `choco install` via pwsh in Wine |
| **1** | `core/manifest.py` | No change — `choco` is already an allowed kind |
| **1** | `tests/` | Profile expansion test + choco install script generation test |
| **2** | `core/manifest.py` | Add `modules[]` root field |
| **2** | new file | `core/modules.py` — module resolver/expander |
| **3** | `container/providers/` | New provider definition + Dockerfile for pre-baked chocolatey image |

### Acceptance criteria (Phase 1)

- `chocolatey` profile expands into pwsh + wrapper dependencies in the build plan
- `kind: choco` install step generates a valid `wine pwsh.exe -Command "choco install ..."` command in the build script
- Profile expansion is visible in `winforge inspect`, `winforge plan`
- Existing pwsh example recipe (`powershell-wrapper-pwsh-vnc`) continues to load, dry-run build, verify, and produce the expected VNC run plan
- No internet access is required from the runtime container (build phase only)

---

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
| 2 | Chocolatey profile + `kind: choco` handler | Medium (profiles + pipeline + tests) | None | Proposed — recipe authors can use choco today |
| 3 | Network escape hatch (manifest field + CLI flag) | Small (manifest + launcher + kube) | Theme 1 (parallel ok) | Implemented — overridable isolation |
| 4 | First-class module system | Medium (schema + resolver) | Theme 2 proves the pattern | Proposed — cleaner abstraction |
| 5 | Pre-baked chocolatey runtime image | Medium (Dockerfile + CI + GHCR push) | Theme 2 | Proposed — faster builds |

## Review triggers

Create or update an ADR if any theme changes:
- The recipe schema (new `modules[]` field, `runtime.network`)
- The runtime container trust boundary (`--net=none` default)
- The base runtime image set (new chocolatey-baked provider image)
- The build artifact contract (network mode in graph metadata)
