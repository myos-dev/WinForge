# WinForge Architecture

WinForge compiles application recipes into immutable application artifacts for Wine/Proton-family runtimes.

## Component model

```text
application recipe or CLI input -> core/manifest -> runtime/providers -> builder/pipeline -> artifact/bundle -> runtime/launcher -> artifact/oci
```

## Design decisions

### 1. Artifact model

WinForge is application-first. The user-facing artifact is an application artifact; the current v0 execution bundle is a sealed filesystem staging/debug representation:

```text
<name>-<version>/
  manifest.winforge.json
  prefix/drive_c/
  runtime/runtime.json
  launch/entrypoint.json
  metadata/provenance.json
  metadata/graph.json
  build/build-plan.json
  logs/build.log
```

It includes the built prefix, runtime binding, normalized recipe/manifest, launch definition, metadata, hashes/provenance, logs, and `metadata/graph.json` as build/provenance metadata. The canonical deployable direction is an OCI image digest containing this application artifact and embedded WinForge metadata.

### 2. Recipe schema v0

The primary shareable authoring format is strict YAML (`winforge.app/v0`). JSON remains supported for generated, normalized, test, and CLI-driven inputs. The recipe defines application identity, runtime provider, dependencies, install steps, filesystem mappings, config, registry tweaks, launch entrypoint, state behavior, exports, hashes, and provenance fields.

### 3. Runtime abstraction layer

Providers are pluggable through `runtime/catalog.json`; active v0 providers are `wine`, `staging`, and `umu-proton-ge`. `wine` and `staging` launch directly with Wine; `umu-proton-ge` launches the GE-Proton runner through UMU (`umu-run`). The catalog distinguishes mutable aliases (`latest`, `previous`, etc.) from pinned runner versions and writes both requested/resolved versions into bundle metadata. Providers must not know about VIC, Kubernetes, or customer tenancy.

### 4. Builder pipeline

The deterministic pipeline is `init-prefix`, `install-dependencies`, `install-apps`, `apply-layout-and-registry`, `validate`, and `seal-artifact`.

### 5. Execution graph

`metadata/graph.json` is first-class build/provenance output. It records runtime image selection, artifact identity, launch contract, graphics modes, build phase order, and exact-runtime compatibility. It should not become a general runtime scheduler; runtime execution should verify the artifact, prepare state, start display services if requested, and launch the application contract.

### 6. Bundle inspection and verification

`winforge bundle inspect` and `winforge bundle verify` form the validation layer between bundle creation and future `winforge run`. Verification consumes the bundle's manifest, runtime binding, launch contract, provenance, build plan, and `metadata/graph.json` without requiring container execution.

### 7. Run planning and execution

`runtime/launcher.py` implements the current `winforge run` path. It consumes verified bundle output, emits `winforge.run-plan/v0` for dry runs, and executes the plan with Podman/Docker when not in dry-run mode. Headless mode uses Xvfb without host ports; VNC mode exposes loopback-only VNC/noVNC ports and starts `x11vnc` plus `websockify` inside the runtime container. Bundles are mounted read-only and prefixes are copied before launch so runtime mutation affects state, not the sealed artifact.

### 8. OCI application export

`artifact/oci.py` implements `winforge export oci`. It consumes a verified bundle, emits `winforge.oci-export-plan/v0` in dry-run mode, stages a build context with a copied bundle plus `metadata/artifact.json`, generates a runnable app `Containerfile`, and builds with Podman/Docker when not in dry-run mode.

Exported images are based on the graph-resolved runtime image and embed the bundle at `/opt/winforge/bundle`. Runtime state and exports are separate at `/var/lib/winforge/state` and `/exports`.

### 9. Kubernetes integration

WinForge supports OCI output for distribution and Kubernetes execution as a downstream substrate, but WinForge must not depend on Kubernetes internally.

## Non-goals

Do not fork Wine/Proton, implement a general container runtime, include VIC policy/orchestration/product logic, expose Wine prefix construction as the primary user mental model, or make mutable GUI bottle workflows the artifact model.
