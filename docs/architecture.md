# WinForge Architecture

WinForge compiles declarative Wine/Proton-family execution environment manifests into immutable execution bundles.

## Component model

```text
manifest authoring -> core/manifest -> runtime/providers -> builder/pipeline -> artifact/bundle -> optional artifact/oci
```

## Design decisions

### 1. Artifact model

A v0 execution bundle is a sealed filesystem artifact:

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

It includes prefix, runtime binding, manifest, launch definition, metadata, hashes/provenance, logs, and `metadata/graph.json` as the resolved execution graph.

### 2. Manifest schema v0

The manifest defines runtime provider, dependencies, install steps, filesystem mappings, launch entrypoint, environment, hashes, and provenance fields.

### 3. Runtime abstraction layer

Providers are pluggable through `runtime/catalog.json`; active v0 providers are `wine`, `staging`, and `proton-ge`. Providers must not know about VIC, Kubernetes, or customer tenancy.

### 4. Builder pipeline

The deterministic pipeline is `init-prefix`, `install-dependencies`, `install-apps`, `apply-layout-and-registry`, `validate`, and `seal-artifact`.

### 5. Execution graph

`metadata/graph.json` is first-class bundle output. It is the bridge from manifest authoring to Ramalama-like `winforge run`: runtime image selection, bundle artifact identity, launch contract, graphics modes, and exact-runtime compatibility live in one deterministic graph.

### 6. Kubernetes / OCI integration

WinForge supports OCI output for distribution and Kubernetes execution as a downstream substrate, but WinForge must not depend on Kubernetes internally.

## Non-goals

Do not fork Wine/Proton, implement a general container runtime, include VIC policy/orchestration/product logic, or make mutable GUI bottle workflows the artifact model.
