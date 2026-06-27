# WinForge Architecture

WinForge compiles declarative Wine/Proton execution environment manifests into immutable execution bundles.

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
  build/build-plan.json
  logs/build.log
```

It includes prefix, runtime binding, manifest, launch definition, metadata, hashes/provenance, and logs.

### 2. Manifest schema v0

The manifest defines runtime provider, dependencies, install steps, filesystem mappings, launch entrypoint, environment, hashes, and provenance fields.

### 3. Runtime abstraction layer

Providers are pluggable: `wine`, `staging`, `proton`, and `proton-ge`. Providers must not know about VIC, Kubernetes, or customer tenancy.

### 4. Builder pipeline

The deterministic pipeline is `init-prefix`, `install-dependencies`, `install-apps`, `apply-layout-and-registry`, `validate`, and `seal-artifact`.

### 5. Kubernetes / OCI integration

WinForge supports OCI output for distribution and Kubernetes execution as a downstream substrate, but WinForge must not depend on Kubernetes internally.

## Non-goals

Do not fork Wine/Proton, implement a general container runtime, include VIC policy/orchestration/product logic, or make mutable GUI bottle workflows the artifact model.
