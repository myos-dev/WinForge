# WinForge Container Architecture

## Overview

WinForge runtime provider containers are the **OCI execution substrate** for Wine/Proton-family prefix construction. Each provider type maps to a Dockerfile that produces an OCI image containing:

- The Wine/Proton-family runtime binaries
- Xvfb for headless display emulation (required by many Windows installers)
- x11vnc, websockify, and noVNC assets for `winforge run --graphics vnc`
- Helper tools (winetricks, cabextract, 7zip, etc.)
- The WinForge entrypoint chain

These images are the **base layer** on which WinForge builds prefixes, installs dependencies, and seals the final execution bundle.

## Layered Model

```
┌──────────────────────────────────────────────┐
│              Application Layer                │  ← OCI layer added by
│  installed app + configured prefix            │    `winforge build`
├──────────────────────────────────────────────┤
│              Dependency Layer                 │  ← OCI layer added by
│  winetricks verbs, components, registry       │    builder pipeline
├──────────────────────────────────────────────┤
│              Prefix Foundation                │  ← OCI layer added by
│  wineboot init, drive_c, registry hive        │    builder pipeline
├──────────────────────────────────────────────┤
│          WinForge Runtime Base                │  ← This repo's container
│ Wine/UMU+GE-Proton + Xvfb/VNC + tools/entrypoints  │    (Dockerfiles)
├──────────────────────────────────────────────┤
│          Base OS Layer                        │  ← Debian Bookworm Slim
│  libc, libstdc++, basic runtime deps          │
└──────────────────────────────────────────────┘
```

## Runtime Catalog and Provider Images

`runtime/catalog.json` is the authoritative runtime catalog. It declares
which provider/version pairs WinForge supports, which Dockerfile/build arg
builds each base image, which local tag is used for development, and which
published GHCR image Forge should pull during normal builds.

GitHub Actions does **not** hardcode runtime versions. The workflow runs
`python3 -m runtime.catalog --ci-matrix` and builds every catalog entry
where `ciBuild` is true.

| Provider | Local Image | Published Image | Source | Build Arg |
|---|---|---|---|---|
| Wine Stable | `winforge/wine:<version>` | `ghcr.io/myos-dev/winforge-wine:<version>` | Pinned WineHQ apt (`.deb`) | `WINE_PACKAGE_VERSION` |
| Wine Staging | `winforge/wine-staging:<version>` | `ghcr.io/myos-dev/winforge-wine-staging:<version>` | Pinned WineHQ apt (`.deb`) | `WINE_PACKAGE_VERSION` |
| UMU + GE-Proton | `winforge/umu-proton-ge:<tag>` | `ghcr.io/myos-dev/winforge-umu-proton-ge:<tag>` | GE-Proton GitHub release + UMU launcher | `GE_PROTON_TAG` |


### Runner aliases and pinning

The catalog may accept aliases such as `latest`, `stable`, `previous`,
`legacy`, and `baseline`, but CI builds pinned versions only. Alias tags are
published only from the pinned matrix entry that owns the alias to avoid
parallel jobs racing on `:latest`. Resolved bundle metadata preserves both the
recipe request and the concrete runtime selection.

Current curated build matrix:

| Provider | Pinned versions | Alias tags |
| --- | --- | --- |
| `wine` | `11.0`, `10.0`, `9.0` | `latest`, `stable`, `previous`, `legacy` |
| `staging` | `11.10`, `11.9`, `11.0` | `latest`, `staging-latest`, `previous`, `baseline` |
| `umu-proton-ge` | `GE-Proton11-1`, `GE-Proton10-34`, `GE-Proton9-27` | `latest`, `previous`, `legacy` |

### Wine Stable / Staging

Built from official WineHQ Debian packages pinned by exact package version. Architecture: amd64 + i386 (via multiarch). Dockerfiles pin the WineHQ metapackage plus the matching root, amd64, and i386 packages so older exact package pins resolve under apt 3/buildx.

```
Dockerfile structure:
  Stage 1 (base)      — Debian Bookworm Slim + i386 multiarch
  Stage 2 (winehq)    — WineHQ repo + winehq-stable/staging
  Stage 3 (tools)     — winetricks, cabextract, 7zip, Xvfb
  Stage 4 (final)     — entrypoint, env vars, workdir
```

### UMU + GE-Proton

`winforge/umu-proton-ge` is the active Proton-family runtime today. It installs
UMU as the launcher (`umu-run`) and downloads the selected GE-Proton runner
release from GitHub into `/opt/proton-ge`. Valve Proton is intentionally not an
active v0 provider because upstream GitHub releases are source-only; add it
later only with a real runnable binary acquisition path.

```
Dockerfile structure:
  Stage 1 (base)      — Debian Bookworm Slim + i386 multiarch where needed
  Stage 2 (download)  — curl GE-Proton release tarball + optional checksum verify
  Stage 3 (extract)   — tar to /opt/proton-ge
  Stage 4 (UMU)       — install pinned umu-launcher and expose umu-run
  Stage 5 (final)     — entrypoint, STEAM_COMPAT env, workdir
```

## Entrypoint Chain

The entrypoint in every image is `xvfb-entrypoint.sh`:

1. Start Xvfb on `:99` (configurable via `DISPLAY`)
2. Wait for X server readiness (up to 3 seconds)
3. Set `WINEPREFIX`, `WINEDLLOVERRIDES`, `WINEARCH`
4. Create prefix directory if `WINEFS=builder`
5. Execute the provided command (builder or `winforge run` launcher script)

For `winforge run --graphics vnc`, the launcher script starts `x11vnc` against
the Xvfb display and starts `websockify` for browser/noVNC access. Ports are
published on host loopback only by default.

## CLI Integration

```bash
# List available build definitions
winforge container list

# Build current Wine Stable through the mutable alias
winforge container build wine latest

# Build and push a pinned Wine Stable runtime
winforge container build wine 11.0 --engine docker --registry ghcr.io/myorg --push

# Get the resolved published OCI image reference for a provider+alias
winforge container ref wine latest
# → ghcr.io/myos-dev/winforge-wine:11.0

# Plan a build — includes resolved OCI image
winforge plan examples/minimal.winforge.json

# Build an execution bundle (dry-run)
winforge build examples/minimal.winforge.json --dry-run

# Inspect and verify bundle contract before run/export/kube generation
winforge bundle inspect dist/notepad-plus-plus-portable-0.1.0
winforge bundle verify dist/notepad-plus-plus-portable-0.1.0

# Preview and execute a verified bundle
winforge run --dry-run --graphics headless dist/notepad-plus-plus-portable-0.1.0
winforge run --graphics headless dist/notepad-plus-plus-portable-0.1.0
winforge run --graphics vnc --vnc-port 5900 --novnc-port 6080 dist/notepad-plus-plus-portable-0.1.0

# Export the verified bundle as a runnable application OCI image
winforge export oci dist/notepad-plus-plus-portable-0.1.0 \
  --tag ghcr.io/myos-dev/winforge-app-notepad-plus-plus:0.1.0 \
  --dry-run
winforge export oci dist/notepad-plus-plus-portable-0.1.0 \
  --tag ghcr.io/myos-dev/winforge-app-notepad-plus-plus:0.1.0
```

## Runtime Binding

When a manifest is resolved, `RuntimeBinding.oci_image` contains the published GHCR image reference and `RuntimeBinding.local_oci_image` contains the local developer tag. Both are produced from `runtime/catalog.json` through `runtime/providers.py`.

The `plan` and `build` CLI commands automatically resolve the catalog-backed OCI image reference and include it in their output. `build` also writes `metadata/graph.json` so later `run`/OCI/kube commands can consume the resolved runtime and launch contract without reinterpreting the manifest. `winforge run` consumes that graph, verifies exact runtime consistency, mounts the bundle read-only, copies the prefix to an ephemeral runtime prefix, and launches through the catalog-resolved runtime image.

`winforge export oci` also consumes the graph. It uses `runnerRuntime.image` as the application image base, writes `metadata/artifact.json` into a staged bundle copy, adds `winforge-app-launch`, and builds a runnable image whose mutable paths are `/var/lib/winforge/state` and `/exports`.

## Consumption by VIC (future)

When VIC consumes WinForge artifacts:

1. VIC pulls the catalog-resolved published base image, e.g. `ghcr.io/myos-dev/winforge-wine:<resolved-version>`
2. VIC pulls the WinForge-produced bundle OCI image (with prefix + app layer)
3. VIC launches the combined image with the VIC runtime contract
4. The container starts with Xvfb, enters the entrypoint, and VIC interacts via STDIO

WinForge produces sealed, immutable OCI artifacts. VIC handles orchestration and lifecycle.

## Building Without Docker

The container images are optional during development. The `winforge build --dry-run` mode creates the bundle contract without requiring any container runtime. Real prefix construction requires the container images to be built or pulled.

For CI environments without Docker, use `podman` (Docker-compatible CLI) or `buildah` for rootless builds.

See `container/build.sh` for the complete build automation script.
