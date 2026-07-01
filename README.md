# WinForge

**Application-first packager and runner for Wine/Proton-family software.**

WinForge takes an application recipe and builds a reproducible application
artifact for Wine/Proton-family runtimes. Users should think “I am packaging
Notepad++,” not “I am building a Wine prefix.” Wine prefixes, runtime images,
launch scripts, bundle directories, and OCI layers are implementation details
behind a simple recipe → build → run workflow.

## Why WinForge?

Running Windows applications in containers today is ad-hoc: hand-written
Dockerfiles, copy-pasted winetricks commands, unversioned prefixes, no
provenance tracking. WinForge replaces that with:

- **Deterministic builds** — Same manifest + same runtime = same bundle
- **Immutable artifacts** — Sealed after construction, no drift
- **Provenance recording** — Sources, hashes, versions tracked in metadata
- **Runtime abstraction** — Swap Wine, Wine-Staging, or UMU-backed GE-Proton
  without changing the manifest
- **OCI-native direction** — Application artifacts can be distributed and deployed as OCI images

## What WinForge is Not

WinForge is **not** a Wine fork, Proton fork, container runtime, Kubernetes operator, GUI bottle manager, package manager for arbitrary Linux software, or tenant/policy/orchestration product layer.

## Architecture

```
application recipe (YAML, CLI-generated, or normalized JSON)
  │
  ▼
┌─────────────────┐       ┌──────────────────────────┐
│ Runtime Provider│──────▶│ Catalog Runtime Image     │
│ wine/staging/UMU│       │ ghcr.io/.../winforge-wine │
└──────┬──────────┘       └──────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────┐
│              Builder Pipeline                      │
│ resolve → install deps/app → config/registry → seal │
└──────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│ Application Artifact                           │
│ built prefix + launch contract + metadata      │
│ bundle dir today, runnable OCI image export   │
└──────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│ Runtime State / Exports                        │
│ persisted separately; never mutates artifact   │
└──────────────────────────────────────────────┘
```

## Installation

WinForge is installable as a Python command-line tool. The recommended myOS
installation path is `uv tool install`; `pipx install` should work anywhere
`pipx` is available.

```bash
# Preferred on myOS
uv tool install "git+ssh://git@github.com/myos-dev/WinForge.git"

# Alternative when pipx is installed
pipx install "git+ssh://git@github.com/myos-dev/WinForge.git"
```

If your machine uses a Git SSH host alias, substitute the host in the URL:

```bash
uv tool install "git+ssh://git@github-noahgiroux/myos-dev/WinForge.git"
pipx install "git+ssh://git@github-noahgiroux/myos-dev/WinForge.git"
```

Verify the installed console script:

```bash
winforge --help
```

If you are testing from a cloned repo, you can also inspect the included
example recipe:

```bash
winforge inspect examples/notepad-plus-plus.winforge.yaml
```

For repo-local development, the legacy script path remains available:

```bash
python3 cmd/winforge.py --help
python3 -m winforge --help
```

## Quick Start

```bash
# Build from the user/business-facing YAML recipe format
winforge build examples/notepad-plus-plus.winforge.yaml --dry-run

# JSON remains supported for generated or CLI-normalized inputs
winforge build examples/minimal.winforge.json --dry-run

# Inspect or verify the lower-level bundle when debugging/automating
winforge bundle inspect dist/notepad-plus-plus-8.6.0
winforge bundle verify dist/notepad-plus-plus-8.6.0

# Resolve built application artifacts by name from the local index
winforge artifacts list
winforge artifacts resolve notepad-plus-plus

# Preview and run the built application artifact by app name or bundle path
winforge run --dry-run --graphics headless notepad-plus-plus
winforge run --graphics headless notepad-plus-plus
winforge run --graphics vnc --vnc-port 5900 --novnc-port 6080 dist/notepad-plus-plus-8.6.0

# Export a runnable application OCI image by app name or bundle path
winforge export oci notepad-plus-plus \
  --tag ghcr.io/myos-dev/winforge-app-notepad-plus-plus:8.6.0 \
  --dry-run
winforge export oci dist/notepad-plus-plus-8.6.0 \
  --tag ghcr.io/myos-dev/winforge-app-notepad-plus-plus:8.6.0

# Emit Kubernetes YAML for a digest-pinned application image
winforge export kube notepad-plus-plus \
  --image ghcr.io/myos-dev/winforge-app-notepad-plus-plus@sha256:... \
  --namespace winforge-apps \
  --output k8s/notepad-plus-plus.yaml

# List available runtime providers
winforge providers
```

## Application Recipes

WinForge accepts strict YAML application recipes as the primary shareable
authoring format for users and businesses. JSON remains valid for generated,
normalized, or CLI-driven workflows. YAML is intentionally strict: unknown
fields, duplicate keys, anchors, aliases, and merge keys are rejected so
recipes normalize into one clear object model.

A recipe describes an application: provider/version, dependencies, config,
registry tweaks, Wine config, launch command, state behavior, and exports. It
does not ask users to manage Wine prefixes directly.



## Runtime Providers and Runner Versions

`runtime.version` may be a pinned runner version or a mutable catalog alias.
Aliases are convenience inputs only; WinForge resolves them before writing bundle
metadata. For example:

```yaml
runtime:
  provider: wine
  version: latest
```

currently resolves to Wine `11.0`, and bundle metadata records both
`requestedVersion: latest` and `resolvedVersion: 11.0`. Use pinned versions or
resolved OCI digests for customer/production reproducibility.

Current curated runner set:

| Provider | Aliases | Pinned versions |
| --- | --- | --- |
| `wine` | `latest`/`stable` -> `11.0`; `previous` -> `10.0`; `legacy` -> `9.0` | `11.0`, `10.0`, `9.0` |
| `staging` | `latest`/`staging-latest` -> `11.10`; `previous` -> `11.9`; `baseline` -> `11.0` | `11.10`, `11.9`, `11.0` |
| `umu-proton-ge` | `latest` -> `GE-Proton11-1`; `previous` -> `GE-Proton10-34`; `legacy` -> `GE-Proton9-27` | `GE-Proton11-1`, `GE-Proton10-34`, `GE-Proton9-27` |

## Compatibility Policy

Harder Windows applications often need deliberate compatibility policy, not just a generic Wine image. WinForge supports a first-class `compatibility` block:

```yaml
compatibility:
  arch: win64
  windowsVersion: win10
  graphics:
    backend: dxvk        # wined3d, dxvk, vkd3d, vkd3d-proton, auto, none
    fallback: wined3d
  dllPolicy:
    d3d11: native,builtin
    d3dcompiler_47: native
    mscoree: disabled
    mshtml: disabled
  env:
    WINEDEBUG: "-all"
```

WinForge normalizes this to `winforge.compatibility-policy/v0`, records it in bundle graph/provenance/OCI metadata, applies `WINEARCH`, `winecfg -v <windowsVersion>`, compatibility env, and deterministic `WINEDLLOVERRIDES`, and installs requested `dxvk`/`vkd3d` prefix backends through winetricks. Legacy `config.wine.dllOverrides` is still normalized, but new recipes should prefer `compatibility`.

This is intentionally a high-level policy layer. Explicit loader ordering, COM timing controls, and trace/debug knobs are not primary schema.


## BYO Installers, BYO Files, and Suite Apps

Harder business apps often come from licensed customer-provided media, not public downloads. WinForge recipes can make that source policy explicit and can layer pre-installed file trees deterministically:

```yaml
sources:
  - id: suite-files
    type: files
    path: sources/vendor-suite/Program Files/Vendor Suite
    policy: bring-your-own-files

filesystem:
  - source: sources/vendor-suite/Program Files/Vendor Suite
    target: C:/Program Files/Vendor Suite
    mode: merge
```

`mode: merge` copies the contents of the source directory into the target directory. That supports BlueBuild-style customer-provided folders such as `Program Files` trees without treating an entire Wine prefix as the source of truth. Installers/ISOs remain modeled through `install[]` and `sources[]`; BYO prefix import is still possible later as a convenience path, but reproducibility should be proven from installers/media/files first.

Suite apps can also declare named entrypoints and file associations:

```yaml
entrypoints:
  - id: writer
    name: Vendor Writer
    executable: C:/Program Files/Vendor Suite/Writer.exe
  - id: sheet
    name: Vendor Sheet
    executable: C:/Program Files/Vendor Suite/Sheet.exe

fileAssociations:
  - entrypoint: writer
    extensions:
      - .docx
    mime:
      - application/vnd.openxmlformats-officedocument.wordprocessingml.document
```

Application-specific or proprietary recipes, including Office-shaped recipes, belong in `vic-legacy` or customer/private repositories rather than public WinForge.

## Source Integrity and Compatibility Evidence

Before spending time in Wine, verify that recipe sources are actually present and hash-correct:

```bash
winforge sources verify examples/notepad-plus-plus.winforge.yaml --workspace .
```

The output is `schemaVersion: winforge.source-integrity/v0` and reports every declared source, install source, filesystem overlay, resolved local path, sha256 result, warning, and error. v0 builds consume local workspace files; remote URLs are recorded as provenance but must be materialized locally for install/filesystem steps.

For a compatibility evidence pass:

```bash
# Dependency-light planning evidence
winforge compat test examples/notepad-plus-plus.winforge.yaml \
  --workspace . \
  --output dist \
  --graphics headless \
  --engine docker \
  --mode dry-run

# Real container build evidence after local sources are present
winforge compat test examples/notepad-plus-plus.winforge.yaml \
  --workspace . \
  --output dist-real \
  --graphics headless \
  --engine docker \
  --mode build \
  --build-timeout 2400

# Real build plus bounded app launch evidence
winforge compat test examples/notepad-plus-plus.winforge.yaml \
  --workspace . \
  --output dist-run \
  --graphics headless \
  --engine docker \
  --mode run \
  --build-timeout 2400 \
  --run-timeout 60
```

The output is `schemaVersion: winforge.compat-test/v0`. `--mode dry-run` includes source integrity, dry-run bundle creation, bundle verification, and a `winforge.run-plan/v0` launch plan carrying runtime and compatibility policy. `--mode build` performs the real container build and records build execution evidence. `--mode run` records real build evidence plus `winforge.run-result/v0` app launch evidence.

The packaged seed corpus is available with:

```bash
winforge compat corpus
```

It emits `schemaVersion: winforge.compat-corpus/v0` with starter apps/tier labels such as Notepad++, 7-Zip, PuTTY, WinSCP, DB Browser for SQLite, .NET sample, COM sample, Office BYO installer/files candidates, and blocked driver-required app classes.

The bundled Notepad++ recipe remains a contract fixture until `sources/notepad-plus-plus.exe` and `overlays/notepad-plus-plus/config.xml` are provided. `sources verify` / `compat test` should report that clearly instead of failing later inside Wine.

## Local Artifact Index

`winforge build` registers materialized bundles in a local artifact index at:

```text
dist/.winforge/artifacts.json
```

The index uses `schemaVersion: winforge.artifact-index/v0` and maps app names
and versions to verified bundle directories. This lets normal run/export flows
use app references instead of requiring users to remember bundle paths:

```bash
winforge artifacts list
winforge artifacts resolve my-app
winforge artifacts resolve my-app@1.0.0

winforge run --dry-run --graphics headless my-app
winforge export oci my-app --tag ghcr.io/myos-dev/winforge-app-my-app:1.0.0 --dry-run
```

A bare app name resolves to the latest registered version for that application.
Use `name@version` when a specific version is required. Direct bundle paths such
as `dist/my-app-1.0.0` remain supported for debugging and automation.

## Running Built Artifacts

`winforge run` currently consumes a verified bundle, not the original manifest. The
command reads `metadata/graph.json`, verifies the bundle contract, selects the
graph-resolved `runnerRuntime.image`, and launches the graph-resolved entrypoint
inside the catalog runtime container.

```bash
# Machine-readable run plan only
winforge run --dry-run --graphics headless dist/my-app-1.0.0

# Headless execution through the runtime image's Xvfb entrypoint
winforge run --graphics headless dist/my-app-1.0.0

# Visible execution with loopback-only VNC and noVNC/websockify ports
winforge run --graphics vnc --vnc-port 5900 --novnc-port 6080 dist/my-app-1.0.0
```

For v0, the bundle is mounted read-only at `/opt/winforge/bundle`; the prefix
is copied to `/tmp/winforge-prefix` before launch so normal Wine runtime
mutation does not alter the sealed bundle artifact.

## OCI Application Image Export

`winforge export oci` consumes a verified bundle and turns it into a runnable
application OCI image. Dry-run mode prints the `winforge.oci-export-plan/v0`
contract without requiring Docker or Podman:

```bash
winforge export oci dist/my-app-1.0.0 \
  --tag ghcr.io/myos-dev/winforge-app-my-app:1.0.0 \
  --dry-run
```

Real export stages a build context, writes `metadata/artifact.json` into the
staged bundle copy, generates a `Containerfile`, adds
`/usr/local/bin/winforge-app-launch`, then runs the selected build engine:

```bash
winforge export oci dist/my-app-1.0.0 \
  --tag ghcr.io/myos-dev/winforge-app-my-app:1.0.0 \
  --engine docker

# Build, push, and record repo digest identity when available
winforge export oci my-app \
  --tag ghcr.io/myos-dev/winforge-app-my-app:1.0.0 \
  --engine docker \
  --push

# Verify OCI labels match embedded WinForge artifact metadata
winforge image verify ghcr.io/myos-dev/winforge-app-my-app:1.0.0 --engine docker
```

The source bundle is not mutated. The image layout is:

```text
/opt/winforge/bundle      immutable embedded bundle
/var/lib/winforge/state   mutable runtime state
/exports                  explicit app/user outputs
/usr/local/bin/winforge-app-launch
```

The embedded artifact metadata uses `schemaVersion: winforge.artifact-image/v0`
and records requested/resolved runtime versions, runner, launcher, base image,
launch contract, graphics contract, state path, and exports path.

## Kubernetes Manifest Export

`winforge export kube` consumes a verified bundle or app-name reference and emits
Kubernetes YAML for a previously built/pushed WinForge application image. It does
not call `kubectl`, create namespaces, or apply resources.

Digest-pinned image refs are required by default:

```bash
winforge export kube my-app \
  --image ghcr.io/myos-dev/winforge-app-my-app@sha256:... \
  --namespace winforge-apps \
  --output k8s/my-app.yaml
```

Dry-run mode prints the `winforge.kube-export/v0` plan, including generated YAML:

```bash
winforge export kube my-app \
  --image ghcr.io/myos-dev/winforge-app-my-app@sha256:... \
  --dry-run
```

By default the emitter creates a Deployment plus PVCs for runtime state and
exports. Use `--no-pvc` for smoke/demo manifests that use `emptyDir` volumes.
Mutable tags are rejected unless `--allow-mutable-tag` is supplied explicitly.
Kubernetes labels are normalized for selector/tooling safety; exact WinForge
metadata such as schema, raw app name, version, and image ref is preserved in
annotations.

## WinForge WINE Container

The runtime provider containers are the OCI execution substrate.
See [docs/container-architecture.md](docs/container-architecture.md).

`runtime/catalog.json` is the source of truth for supported runtime
provider versions, Dockerfiles, build args, local image refs, and
published GHCR image refs. CI generates its build matrix from this file,
and Forge resolves manifests through the same catalog.

```bash
# List available catalog-backed container build definitions
winforge container list

# Build the current Wine Stable runtime alias
winforge container build wine latest

# Build a pinned previous Wine Stable runtime
winforge container build wine 10.0

# Build current Wine Staging
winforge container build staging latest

# Build current UMU + GE-Proton runtime
winforge container build umu-proton-ge latest

# Get the resolved published OCI image reference for a provider+version alias
winforge container ref wine latest
# → ghcr.io/myos-dev/winforge-wine:11.0

# Build from Docker compose for local development
# (Compose is a dev convenience; runtime/catalog.json is authoritative.)
docker compose -f container/docker-compose.yml build wine
```

### Container Directory Layout

```
container/
├── build.sh                          # Build all providers
├── docker-compose.yml                # Local dev compose
├── common/
│   ├── xvfb-entrypoint.sh            # Xvfb init + headless Wine exec
│   └── wine-env.sh                   # Standard Wine environment
└── providers/
    ├── wine/Dockerfile               # Wine Stable (WineHQ apt)
    ├── wine-staging/Dockerfile       # Wine Staging (WineHQ apt)
    └── umu-proton-ge/Dockerfile      # UMU + GE-Proton stack
```

## Reference Repos

WinForge's design draws from the broader Wine/Proton ecosystem:

| Repo | What WinForge Takes |
|---|---|
| [Bottles](https://github.com/bottlesdevs/Bottles) | Wine command wrappers, dependency manager pattern, template-based prefix creation, registry rule management |
| [wine-utils](https://github.com/rmi1974/wine-utils) | Reproducible Wine builds from source, build provenance tracking, patch management |
| [umu-launcher](https://github.com/Open-Wine-Components/umu-launcher) | Runtime download/verify pipeline, SHA256 verification, file-locking pattern, Proton version management |
| [umu-protonfixes](https://github.com/Open-Wine-Components/umu-protonfixes) | Verb/component catalog (`*.verb`), game engine detection, store-agnostic fix layering |
| [Steam Runtime](https://github.com/valvesoftware/steam-runtime) | Layer composition model, build-runtime.py pattern, template-based manifest generation |
| [MTGOBot](https://github.com/videre-project/MTGOBot) | Headless Wine OCI container pattern (Xvfb entrypoint, DISPLAY=:99, wine --headless) |
| [docker-wine](https://github.com/scottyhardy/docker-wine) | Container ergonomics: UID/GID mapping, display modes, Xvfb/RDP/audio concerns; WinForge does not adopt its mutable desktop-container goal |
| [LSW](https://github.com/barrersoftware/lsw) | Foundation-first compatibility architecture and path/registry translation awareness; WinForge does not adopt its no-Wine/kernel/PE-loader goal |

Detailed analysis in [docs/reference-study.md](docs/reference-study.md).

## Project Structure

```
WinForge/
├── pyproject.toml               # Python packaging metadata and console script
├── winforge/                     # Installable CLI package (`winforge`)
├── cmd/winforge.py              # Repo-local development shim
├── core/
│   ├── manifest.py              # Manifest model, validation, loading
│   ├── prefix.py                # Prefix abstraction
│   └── provenance.py            # Provenance tracking
├── runtime/
│   ├── catalog.json             # Supported runtime catalog (CI + Forge source of truth)
│   ├── catalog.py               # Catalog loader + CI matrix generator
│   ├── launcher.py              # Verified bundle run planning/execution
│   └── providers.py             # Catalog-backed provider resolution + OCI image binding
├── builder/
│   ├── pipeline.py              # Build phase orchestration
│   └── installer.py             # Application installation steps
├── container/                   # OCI container build definitions
│   ├── build.sh                 # Build script for all providers
│   ├── docker-compose.yml       # Local dev compose
│   ├── common/                  # Shared scripts (xvfb-entrypoint, wine-env)
│   └── providers/               # Dockerfiles per runtime provider
├── artifact/
│   ├── bundle.py                # Bundle writer (sealed artifact)
│   ├── graph.py                 # Resolved execution graph writer
│   ├── inspection.py            # Bundle inspect/verify contract checks
│   ├── index.py                 # Local app-name artifact index
│   ├── oci.py                   # Runnable application OCI export
│   ├── kube.py                  # Kubernetes manifest export
│   └── exporter.py              # Bundle export utilities
├── tests/                       # Unit tests
├── docs/                        # Architecture docs, ADRs
└── examples/                    # Example recipes/manifests
```

## Development

```bash
# Run tests
python3 -m unittest discover

# Verify local tool installation
TMP_UV_HOME="$(mktemp -d)"
UV_LINK_MODE=copy UV_TOOL_DIR="$TMP_UV_HOME/tools" UV_TOOL_BIN_DIR="$TMP_UV_HOME/bin" \
  uv tool install --force --reinstall --refresh .
"$TMP_UV_HOME/bin/winforge" --help

# Verify installed/package CLI works
python3 -m winforge --help
python3 cmd/winforge.py --help

# Validate syntax of all Python files
python3 -m compileall .

# Build all containers (requires Docker)
bash container/build.sh

# Or build a specific container
bash container/build.sh wine default
```

## License

Open source — available under the MIT License.
