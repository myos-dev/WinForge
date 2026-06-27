# Reference Study

WinForge borrows from nearby systems without inheriting their wrong assumptions.

- Steam Runtime / pressure-vessel: execution envelope separation.
- UMU Launcher: Proton-style runtime selection outside Steam.
- umu-protonfixes: per-application fixups and Winetricks verbs, made declarative and auditable.
- Bottles: prefix lifecycle language, not GUI-first mutable bottle management.
- Lutris: declarative install ordering and runner configuration, not game-only mutable output.
- PlayOnLinux: Wine app scriptability, not undocumented scripts as the primary contract.
- wine-tkg-style tooling: pinned Wine/Proton runtime builds and provenance.
- ramalama: provider/driver selection pattern, applied to Wine-family runtimes.
- OCI images: distribution layer, not build-time dependency.
- Nix: reproducible inputs and immutable outputs.

Summary:

```text
declarative manifest -> deterministic prefix build -> sealed execution bundle -> optional OCI distribution -> downstream consumer execution
```
