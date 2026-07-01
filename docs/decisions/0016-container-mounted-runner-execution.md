# 0016: Container-mounted runner execution

Date: 2026-07-01
Status: accepted

## Context

Phase 6F added downloadable Wine runner aliases such as `pol-4.3` and `pol-8.2`. On 7040, those PlayOnLinux/Phoenicis-hosted x86 Wine archives download and extract successfully, but running `bin/wine` directly on the host reports `cannot execute: required file not found`. WinForge diagnostics classify this as `missing-elf-interpreter` for `/lib/ld-linux.so.2`.

That host failure should not block VIC/WinForge evidence when the target runtime container carries the required 32-bit loader and libraries.

## Decision

WinForge will keep downloadable runner archives in a host cache, but execute them inside the selected runtime container:

- `winforge runners ensure <alias>` populates the local cache and records archive provenance.
- `winforge build --runner-cache-dir <dir>` mounts the selected runner read-only at `/opt/winforge-runner` during real container builds when `runtime.runner` is present.
- `winforge run --runner-cache-dir <dir>` does the same for real app launches from bundles.
- `winforge compat test --mode build|run --runner-cache-dir <dir>` threads the same cache through build and run evidence.
- Inside the container, WinForge exports `WINFORGE_RUNNER_ID`, `WINFORGE_RUNNER_BIN=/opt/winforge-runner/bin`, prepends that bin directory to `PATH`, and sets `WINE=$WINFORGE_RUNNER_BIN/wine`.

Dry-run planning reports `runnerCache.status` as `present` or `missing` without downloading archives. Real run execution requires the cached runner to be present. Real build execution may populate the cache through `ensure_runner` before mounting it.

## Consequences

- The host does not need to be able to execute old x86 Wine binaries directly.
- The runtime container must provide i386 loader/library support; the Wine and Wine-Staging runtime Dockerfiles already install i386 Wine packages from WineHQ.
- Runner archives stay separate from public runtime images unless a future decision embeds or mirrors them.
- Private Office evidence can use `runtime.runner` aliases without public WinForge shipping Office recipes, containers, or payloads.

## Review trigger

Revisit this if WinForge starts embedding runners into application OCI images, supporting per-runner container images, or scheduling runner caches through VIC-managed shared volumes.
