# WinForge Architecture

WinForge compiles application recipes into immutable application artifacts for Wine/Proton-family runtimes.

## Component model

```text
application recipe or CLI input -> core/manifest + core/compatibility + core/sources -> compat/evidence -> runtime/providers -> builder/pipeline -> artifact/bundle -> artifact/index -> runtime/launcher -> artifact/oci -> artifact/kube
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

The primary shareable authoring format is strict YAML (`winforge.app/v0`). JSON remains supported for generated, normalized, test, and CLI-driven inputs. The recipe defines application identity, runtime provider, dependencies, install steps, filesystem mappings, compatibility policy, config, registry tweaks, launch entrypoint, state behavior, exports, hashes, and provenance fields.

### 3. Runtime abstraction layer

Providers are pluggable through `runtime/catalog.json`; active v0 providers are `wine`, `staging`, and `umu-proton-ge`. `wine` and `staging` launch directly with Wine; `umu-proton-ge` launches the GE-Proton runner through UMU (`umu-run`). The catalog distinguishes mutable aliases (`latest`, `previous`, etc.) from pinned runtime image versions and writes both requested/resolved versions into bundle metadata. Providers must not know about VIC, Kubernetes, or customer tenancy.

Downloadable runner archives are modeled separately from runtime image providers. `runtime.runner` can request a cacheable Wine runner alias such as `pol-8.2`; `runtime/runner_catalog.py` resolves that alias to a pinned PlayOnLinux/Phoenicis upstream Wine tarball URL/SHA-256, while `runtime/runner_cache.py` downloads, verifies, extracts, and diagnoses the local runner cache. The `pol-*` labels are not a separate PlayOnLinux provider.

### 4. Builder pipeline

The deterministic pipeline is `init-prefix`, `install-dependencies`, `install-apps`, `apply-layout-and-registry`, `validate`, and `seal-artifact`.

### 5. Compatibility policy layer

`core/compatibility.py` normalizes high-level Wine/Proton compatibility intent into `winforge.compatibility-policy/v0`. The builder applies WINEARCH, Windows version emulation, compatibility env, deterministic DLL override policy, and requested DXVK/vkd3d prefix backend installation. The runtime launcher and OCI app-image launcher re-export the same policy from `metadata/graph.json`, so build-time and run-time compatibility intent do not drift.

This layer intentionally avoids raw loader-order and trace-control schema. Those stay debug/research-only until hard app failures justify them.

### 6. Source integrity and compatibility evidence

`core/sources.py` verifies local source presence and sha256 values before real Wine work. It emits `winforge.source-integrity/v0`, resolving `file://` and relative paths against the workspace root used by the build container.

`compat/evidence.py` emits `winforge.compat-test/v0`: source integrity, bundle creation, bundle verification, run-plan evidence, and optionally real build/run execution evidence. `--mode dry-run` is dependency-light. `--mode build` runs the real container build after source integrity passes. `--mode run` adds bounded app launch evidence. The evidence envelope stays stable as execution depth increases.

`compat/corpus.py` and `compat/corpus/apps.json` provide the packaged `winforge.compat-corpus/v0` seed list for app testing. The corpus is a curation/input layer, not an automatic runtime selector.


### 7. BYO files and suite metadata

`core/manifest.py` now normalizes source declarations with explicit source `type` and legal/source `policy`. This lets recipes distinguish a customer-provided installer, licensed media/ISO, file tree, font pack, or future prefix archive without WinForge downloading or redistributing proprietary payloads.

`filesystem.mode: merge` is the first files-module primitive: it layers the contents of a user-provided directory into a Windows target directory, which is the reproducible path for pre-installed app directories such as Office `Program Files` trees. BYO prefix import remains a possible convenience path, but the architectural bias is toward reproducible installers/media/files.

Suite metadata (`entrypoints[]` and `fileAssociations[]`) records multi-entry app suites without requiring public app-specific recipes. `winforge run --entrypoint <id>` can select named entrypoints, and host file arguments are mounted read-only and routed into Wine as `Z:` paths. Office/customer/proprietary recipes belong in `vic-legacy` or customer/private repositories, not in public WinForge.

`core/profiles.py` expands reviewable named profiles into concrete compatibility/dependency policy. The initial `office-legacy-32bit` profile captures the current Office/Bottles evidence while preserving the expanded concrete policy in the manifest.

### 8. Downloadable runner cache

`winforge runners list|ensure|diagnose` exposes the runner-cache lifecycle. Diagnostics parse ELF interpreters so old 32-bit Wine builds can fail with actionable evidence such as missing `/lib/ld-linux.so.2` instead of an opaque shell error. This is required for 7040/VIC Office evidence because the Rustring/Bottles reference depends on legacy x86 Wine builds hosted by PlayOnLinux/Phoenicis.

Real build/run execution keeps runner archives host-cached but container-executed: `runtime.runner` requests are mounted read-only at `/opt/winforge-runner`, `WINFORGE_RUNNER_BIN` is exported, and the mounted `bin/` is prepended to `PATH` inside the runtime image. Host diagnostics may show missing 32-bit loader support, but execution can still proceed inside Wine runtime images because those images install i386 Wine support.

### 9. Execution graph

`metadata/graph.json` is first-class build/provenance output. It records runtime image selection, artifact identity, launch contract, graphics modes, build phase order, and exact-runtime compatibility and requested compatibility policy. It should not become a general runtime scheduler; runtime execution should verify the artifact, prepare state, start display services if requested, and launch the application contract.

### 10. Bundle inspection and verification

`winforge bundle inspect` and `winforge bundle verify` form the validation layer between bundle creation and future `winforge run`. Verification consumes the bundle's manifest, runtime binding, launch contract, provenance, build plan, and `metadata/graph.json` without requiring container execution.

### 11. Local artifact index

`artifact/index.py` maintains the local `winforge.artifact-index/v0` cache at `dist/.winforge/artifacts.json` by default. `winforge build` registers verified bundles by app name and version. `winforge artifacts list` and `winforge artifacts resolve <name[@version]>` expose the index, and `winforge run` / `winforge export oci` accept either direct bundle paths or app references.

### 12. Run planning and execution

`runtime/launcher.py` implements the current `winforge run` path. It consumes verified bundle output, emits `winforge.run-plan/v0` for dry runs, and executes the plan with Podman/Docker when not in dry-run mode. Headless mode uses Xvfb without host ports; VNC mode requires `--network bridge`, uses Docker/Podman host port publishing for loopback-bound VNC/noVNC access, and starts `x11vnc` plus `websockify` inside the runtime container. Bundles are mounted read-only and prefixes are copied before launch so runtime mutation affects state, not the sealed artifact. If a graph requests `runtime.runner`, run planning records runner cache status and real execution requires a populated cache so the selected Wine runner can be mounted into the container.

### 13. OCI application export

`artifact/oci.py` implements `winforge export oci`. It consumes a verified bundle, emits `winforge.oci-export-plan/v0` in dry-run mode, stages a build context with a copied bundle plus `metadata/artifact.json`, generates a runnable app `Containerfile`, and builds with Podman/Docker when not in dry-run mode.

Exported images are based on the graph-resolved runtime image and embed the bundle at `/opt/winforge/bundle`. Runtime state and exports are separate at `/var/lib/winforge/state` and `/exports`.

When `--push` is used, export records repo digest identity from image inspection. `winforge image verify` then compares OCI labels to embedded `metadata/artifact.json` so registry/scheduler-visible labels cannot silently drift from WinForge artifact semantics.

### 14. Kubernetes manifest export

`artifact/kube.py` implements `winforge export kube`. It consumes a verified bundle or app-name reference and emits `winforge.kube-export/v0` plus Kubernetes YAML. The emitter requires digest-pinned image refs by default and creates a Deployment plus state/export PVCs unless `--no-pvc` is set. Labels are normalized for Kubernetes selectors, while exact WinForge artifact metadata is preserved in annotations.

WinForge supports OCI output for distribution and Kubernetes execution as a downstream substrate, but WinForge must not depend on Kubernetes internally. The Kubernetes path is manifest generation only: no namespace creation, no `kubectl apply`, no tenant/session policy, and no VIC production automation authority.

## Non-goals

Do not fork Wine/Proton, implement a general container runtime, include VIC policy/orchestration/product logic, expose Wine prefix construction as the primary user mental model, or make mutable GUI bottle workflows the artifact model.
