# WinForge

**Deterministic Wine/Proton-family environment compiler.**

WinForge takes a declarative manifest and compiles it into an immutable
execution bundle — a sealed Wine prefix with installed dependencies,
registry configuration, and application files, packaged for
containerized deployment.

## Why WinForge?

Running Windows applications in containers today is ad-hoc: hand-written
Dockerfiles, copy-pasted winetricks commands, unversioned prefixes, no
provenance tracking. WinForge replaces that with:

- **Deterministic builds** — Same manifest + same runtime = same bundle
- **Immutable artifacts** — Sealed after construction, no drift
- **Provenance recording** — Sources, hashes, versions tracked in metadata
- **Runtime abstraction** — Swap Wine, Wine-Staging, or GE-Proton
  without changing the manifest
- **OCI-native** — Bundles can be layered onto runtime container images

## What WinForge is Not

WinForge is **not** a Wine fork, Proton fork, container runtime, Kubernetes operator, GUI bottle manager, or tenant/policy/orchestration product layer.

## Architecture

```
manifest.winforge.json
  │
  ▼
┌─────────────────┐       ┌──────────────────────────┐
│  Runtime        │──────▶│  OCI Container Base       │
│  Provider       │       │  (winforge/wine:9.0, etc) │
│ (wine/proton-ge)│       └──────────────────────────┘
└──────┬──────────┘                    │
       │                               │
       ▼                               ▼
┌──────────────────────────────────────────────────┐
│              Builder Pipeline                      │
│  init-prefix → install-dependencies → install-apps │
│  apply-layout → validate → seal-artifact          │
└──────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│         Immutable Execution Bundle             │
│  prefix/ │ runtime/ │ launch/ │ metadata/     │
│  metadata/graph.json is the resolved run graph │
└──────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────┐
│  OCI Image Layer (on winforge/wine:base)     │
│  docker build -f- . < bundle-layer            │
└──────────────────────────────────────────────┘
```

## Quick Start

```bash
# Inspect a manifest
winforge inspect examples/minimal.winforge.json

# Print the build plan
winforge plan examples/minimal.winforge.json

# Dry-run build (creates bundle contract + metadata/graph.json without executing Wine)
winforge build examples/minimal.winforge.json --dry-run

# Build for real (requires a runtime container image)
winforge build examples/minimal.winforge.json

# Inspect a built bundle
winforge bundle inspect dist/notepad-plus-plus-portable-0.1.0

# Verify bundle contract + metadata/graph.json consistency
winforge bundle verify dist/notepad-plus-plus-portable-0.1.0

# Preview the container invocation without starting Wine
winforge run --dry-run --graphics headless dist/notepad-plus-plus-portable-0.1.0

# Run headless, or expose loopback VNC/noVNC for visible execution
winforge run --graphics headless dist/notepad-plus-plus-portable-0.1.0
winforge run --graphics vnc --vnc-port 5900 --novnc-port 6080 dist/notepad-plus-plus-portable-0.1.0

# List available runtime providers
winforge providers
```

## Running Bundles

`winforge run` consumes a verified bundle, not the original manifest. The
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

# Build GE-Proton prebuilt runtime
winforge container build proton-ge GE-Proton9-27

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
    └── proton-ge/Dockerfile          # GE-Proton (GitHub release)
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
├── cmd/winforge.py              # CLI entrypoint
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
└── examples/                    # Example manifests
```

## Development

```bash
# Run tests
python3 -m unittest discover

# Verify CLI works
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
