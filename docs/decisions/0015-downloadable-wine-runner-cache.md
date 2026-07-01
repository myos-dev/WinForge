# 0015: Downloadable Wine runner cache

Date: 2026-07-01
Status: accepted

## Context

The Rustring/Bottles Office reference uses runner labels such as `pol-8.2`, `pol-4.3`, and `pol-3.0.3`. These labels are not a separate PlayOnLinux runtime provider. They are Bottles-compatible names for PlayOnLinux/Phoenicis-hosted upstream Wine x86 tarballs.

The first 7040 manual test downloaded and extracted those tarballs successfully, but each `bin/wine --version` failed with `cannot execute: required file not found`. That is the common Linux symptom for an executable whose interpreter is missing, especially older 32-bit Wine builds requiring `/lib/ld-linux.so.2` and related 32-bit libraries.

## Decision

WinForge models these as downloadable Wine runner archives, separate from runtime image providers:

- keep `runtime.provider: wine`;
- allow optional `runtime.runner: <alias>`;
- resolve runner aliases through `runtime/runner_catalog.py`;
- download, hash-verify, extract, and diagnose runners through `winforge runners`;
- record runner URL/SHA/source/arch metadata in resolved runtime metadata and execution graphs.

Initial built-in aliases:

| Alias | URL family | SHA-256 |
| --- | --- | --- |
| `pol-8.2` | `playonlinux.com/wine/binaries/phoenicis/upstream-linux-x86` | `d38ed5362564c0de73a6f4720a20cf6eece569d2455be2567ac41e1a8a5cb0d6` |
| `pol-4.3` | `playonlinux.com/wine/binaries/phoenicis/upstream-linux-x86` | `64f34fb79de3225bb541fcb8d8c57d0ecf9db2d404e57834096738680c95b29c` |
| `pol-3.0.3` | `playonlinux.com/wine/binaries/phoenicis/upstream-linux-x86` | `0b5d59ad852b87ffccf7a72066fd80cb0759647ebd952c2851ce2b5d76ba33c4` |

## Consequences

- WinForge does not need a `pol` provider.
- The runtime image catalog remains responsible for container images.
- The runner cache is responsible for archive acquisition/provenance/diagnostics.
- Office-shaped recipes remain private under `vic-legacy` or customer repositories; public WinForge only ships generic primitives.
- Real execution still needs a runtime/container environment that contains the 32-bit loader and libraries required by the selected runner.

## Review trigger

Revisit this decision if WinForge starts embedding downloadable runners directly into OCI runtime images, adds a signed runner mirror, or supports a non-Wine runner archive family whose acquisition semantics are materially different.
