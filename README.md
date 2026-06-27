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
│  (sealed, read-only, deployable)              │
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

# Dry-run build (creates bundle contract without executing Wine)
winforge build examples/minimal.winforge.json --dry-run

# Build for real (requires a runtime container image)
winforge build examples/minimal.winforge.json

# List available runtime providers
winforge providers
```

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
│   ├── providers.py             # Runtime provider abstraction + OCI image binding
│   └── providers.py             # Catalog-backed provider resolution
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
