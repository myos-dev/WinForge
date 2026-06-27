# WinForge

WinForge is a reproducible build system for Wine-based Windows execution environments.

It is a deterministic **environment compiler**: it takes a declarative manifest and produces an immutable execution bundle containing a Wine prefix, runtime binding, launch definition, metadata, and provenance records for a Windows application running under Wine/Proton-class runtimes.

## What WinForge is

WinForge parses manifests, constructs deterministic Wine prefixes, installs declared dependency stacks and applications, binds Wine/Proton-family runtimes, emits immutable bundles/optional OCI mappings, and records provenance.

## What WinForge is not

WinForge is **not** a Wine fork, Proton fork, container runtime, Kubernetes operator, GUI bottle manager, VIC component, or tenant/policy/orchestration product layer.

## Relationship to VIC

VIC is a closed-source downstream consumer. VIC may consume WinForge bundles or OCI images, but WinForge must not contain VIC-specific logic.

```text
WinForge manifest -> WinForge builder -> immutable execution bundle / OCI image
                                                        |
                                                        v
                                      any downstream consumer, including VIC
                                                        |
                                                        v
                                  orchestration, policy, tenancy, product UX
```

VIC begins after the artifact exists. VIC must not reimplement prefix construction, dependency installation, runtime binding, or artifact sealing. See `docs/vic-boundary.md`.

## Architecture

```text
manifest v0 -> core/manifest -> runtime/providers -> builder/pipeline -> artifact/bundle -> optional OCI image
```

Directories: `cmd/`, `core/`, `runtime/`, `builder/`, `artifact/`, `examples/`, and `docs/`.

## Quickstart

```bash
python3 cmd/winforge.py inspect examples/minimal.winforge.json
python3 cmd/winforge.py plan examples/minimal.winforge.json
python3 cmd/winforge.py build examples/minimal.winforge.json --output dist --dry-run
```

The scaffold is dependency-free and currently loads normalized JSON manifests. YAML authoring is part of the v0 direction, but JSON keeps the first CLI runnable without external packages.

## Build pipeline

1. `init-prefix` — create an empty prefix, initialize `drive_c`, registry hives, and bind the selected runtime.
2. `install-dependencies` — install declared Winetricks verbs, runtime components, fonts, DirectX components, and packages.
3. `install-apps` — run declared MSI, EXE, portable, Chocolatey, or script install steps.
4. `apply-layout-and-registry` — copy filesystem overlays and apply fixups.
5. `validate` — verify launch entrypoints, hashes, runtime binding, and prefix state.
6. `seal-artifact` — write provenance, logs, normalized manifest, runtime binding, launch definition, and optional OCI mapping.

## Design inspiration

WinForge borrows concepts from ramalama, OCI images, Nix, Steam Runtime/pressure-vessel, UMU Launcher, umu-protonfixes, Bottles, Lutris, PlayOnLinux, and wine-tkg-style build tooling. See `docs/reference-study.md`.

## Current status

Initial scaffold: manifest inspection, deterministic planning, and dry-run bundle materialization work. Real Wine/Winetricks execution, OCI builds, and Kubernetes jobs are intentionally not implemented yet.
