# WinForge Container Architecture

## Overview

WinForge runtime provider containers are the **OCI execution substrate** for Wine/Proton prefix construction. Each provider type maps to a Dockerfile that produces an OCI image containing:

- The Wine/Proton runtime binaries
- Xvfb for headless display emulation (required by many Windows installers)
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
│  Wine/Proton + Xvfb + tools + entrypoints     │    (Dockerfiles)
├──────────────────────────────────────────────┤
│          Base OS Layer                        │  ← Debian Bookworm Slim
│  libc, libstdc++, basic runtime deps          │
└──────────────────────────────────────────────┘
```

## Provider Images

| Provider | Image Name | Source | Build Arg |
|---|---|---|---|
| Wine Stable | `winforge/wine:<version>` | WineHQ apt (`.deb`) | `WINE_VERSION` |
| Wine Staging | `winforge/wine-staging:<version>` | WineHQ apt (`.deb`) | `WINE_VERSION` |
| Valve Proton | `winforge/proton:<version>` | GitHub source archive (source seed) | `PROTON_VERSION` |
| GE-Proton | `winforge/proton-ge:<tag>` | GitHub releases (`.tar.gz`) | `GE_PROTON_TAG` |

### Wine Stable / Staging

Built from official WineHQ Debian packages. Architecture: amd64 + i386 (via multiarch).

```
Dockerfile structure:
  Stage 1 (base)      — Debian Bookworm Slim + i386 multiarch
  Stage 2 (winehq)    — WineHQ repo + winehq-stable/staging
  Stage 3 (tools)     — winetricks, cabextract, 7zip, Xvfb
  Stage 4 (final)     — entrypoint, env vars, workdir
```

### Proton / GE-Proton

Built by downloading release tarballs from GitHub, verifying SHA256 (when provided), and extracting to `/opt/proton` or `/opt/proton-ge`.

```
Dockerfile structure:
  Stage 1 (base)      — Debian Bookworm Slim + i386 multiarch
  Stage 2 (download)  — curl release tarball + sha256sum verify
  Stage 3 (extract)   — tar to /opt/proton or /opt/proton-ge
  Stage 4 (final)     — entrypoint, STEAM_COMPAT env, workdir
```

## Entrypoint Chain

The entrypoint in every image is `xvfb-entrypoint.sh`:

1. Start Xvfb on `:99` (configurable via `DISPLAY`)
2. Wait for X server readiness (up to 3 seconds)
3. Set `WINEPREFIX`, `WINEDLLOVERRIDES`, `WINEARCH`
4. Create prefix directory if `WINEFS=builder`
5. Execute the provided command (or keep Xvfb alive)

## CLI Integration

```bash
# List available build definitions
winforge container list

# Build a Wine Stable 9.0 container
winforge container build wine 9.0

# Build and push to registry
winforge container build wine 9.0 --engine docker --registry ghcr.io/myorg --push

# Get the OCI image reference for a provider+version
winforge container ref wine 9.0
# → winforge/wine:9.0

# Plan a build — includes resolved OCI image
winforge plan examples/minimal.winforge.json

# Build an execution bundle (dry-run)
winforge build examples/minimal.winforge.json --dry-run
```

## Runtime Binding

When a manifest is resolved, the `RuntimeBinding.oci_image` field contains the fully-qualified OCI image reference. This is produced by `runtime/providers.py` -> `resolve_oci_image()`.

The `plan` and `build` CLI commands automatically resolve the OCI image reference and include it in their output.

## Consumption by VIC (future)

When VIC consumes WinForge artifacts:

1. VIC pulls the `winforge/wine:<version>` base image
2. VIC pulls the WinForge-produced bundle OCI image (with prefix + app layer)
3. VIC launches the combined image with the VIC runtime contract
4. The container starts with Xvfb, enters the entrypoint, and VIC interacts via STDIO

WinForge produces sealed, immutable OCI artifacts. VIC handles orchestration and lifecycle.

## Building Without Docker

The container images are optional during development. The `winforge build --dry-run` mode creates the bundle contract without requiring any container runtime. Real prefix construction requires the container images to be built or pulled.

For CI environments without Docker, use `podman` (Docker-compatible CLI) or `buildah` for rootless builds.

See `container/build.sh` for the complete build automation script.
