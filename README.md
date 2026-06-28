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
│ wine/staging/GE │       │ ghcr.io/.../winforge-wine │
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
│ bundle dir today, OCI image digest direction   │
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

# Preview and run the built application artifact
winforge run --dry-run --graphics headless dist/notepad-plus-plus-8.6.0
winforge run --graphics headless dist/notepad-plus-plus-8.6.0
winforge run --graphics vnc --vnc-port 5900 --novnc-port 6080 dist/notepad-plus-plus-8.6.0

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

# Build a Wine Stable container from the catalog
winforge container build wine 9.0

# Build Wine Staging
winforge container build staging 9.0

# Build UMU + GE-Proton runtime
winforge container build umu-proton-ge GE-Proton9-27

# Get the published OCI image reference for a provider+version
winforge container ref wine 9.0
# → ghcr.io/myos-dev/winforge-wine:9.0

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
│   ├── oci.py                   # OCI image mapping & layering
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
