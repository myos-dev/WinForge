# WinForge Application Recipe and Artifact Spec v0

Status: proposed application-first v0 contract.

## Product model

WinForge packages applications, not user-facing Wine prefixes. The primary user/business-authored input is a strict YAML application recipe. JSON remains supported for generated, normalized, or CLI-driven inputs.

Long-term happy path:

```bash
winforge build notepad-plus-plus.winforge.yaml
winforge run notepad-plus-plus
```

The current bundle directory remains a lower-level internal/debug/staging artifact used for tests, inspection, verification, local runs, and future OCI export.

## Required recipe fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schemaVersion` | string | Prefer `winforge.app/v0`; legacy JSON may use `winforge.dev/v0` |
| `name` | string | Application artifact name |
| `version` | string | Application artifact version |
| `runtime` | object | Build-time runtime provider request |
| `launch` | object | Application launch definition |

## Supported authoring formats

YAML is the shareable recipe format for users and businesses. JSON remains valid for CLI-generated, normalized, test, and automation workflows.

Strict YAML rules:

- unknown fields are rejected
- duplicate keys are rejected
- anchors are rejected
- aliases are rejected
- merge keys are rejected
- parsed YAML must normalize into the same object model as JSON

## Application recipe fields

`runtime.provider` must be one of `wine`, `staging`, or `umu-proton-ge`. `runtime.version` is required and may be either a pinned runner version or a catalog alias such as `latest`, `stable`, `previous`, `legacy`, or `baseline`. Provider/version are selected at build time and enforced at run time; changing providers should require rebuilding. For `umu-proton-ge`, the provider identifies the UMU-backed Proton-family stack while `runtime.version` selects or resolves to the GE-Proton runner tag. Resolved runtime metadata records both `requestedVersion` and `resolvedVersion`; future production artifacts should use the resolved image digest, not a mutable alias, as identity.

`sources` records upstream/local source provenance. v0 source integrity verifies local `file://` and relative workspace paths plus declared `sha256` values; remote URLs are recorded but not fetched by the dependency-light verifier.

`dependencies` supports build-time dependency installation. Allowed kinds: `winetricks`, `font`, `directx`, `package`, `runtime-component`.

`install` supports build-time application installation. Allowed kinds: `msi`, `exe`, `portable`, `choco`, `script`. MSI/EXE/portable require `source`; script requires `command`.

`filesystem` maps declared source files into Windows-style targets under `drive_c`.

`config` remains supported for legacy/provider-level configuration. New harder-app recipes should prefer first-class `compatibility` policy for architecture, Windows version, graphics backend, DLL policy, and compatibility environment.

`registry` records build-time registry tweaks.

`launch.entrypoint` is required. `launch.args`, `launch.env`, and `launch.workingDirectory` are optional.

`state` describes runtime state behavior. The default direction is persistent runtime state separate from the immutable artifact.

`exports` describes user/application outputs such as reports, save exports, screenshots, generated documents, or other files that should be mounted or collected explicitly.

## Compatibility policy

`compatibility` is a high-level policy layer above Wine internals. It supports:

```yaml
compatibility:
  arch: win64
  windowsVersion: win10
  graphics:
    backend: dxvk
    fallback: wined3d
  dllPolicy:
    d3d11: native,builtin
    d3dcompiler_47: native
    mscoree: disabled
    mshtml: disabled
  env:
    WINEDEBUG: "-all"
```

`arch` currently accepts `win32` or `win64`. `windowsVersion` accepts common Wine version targets such as `win7`, `win10`, and `win11`. `graphics.backend` accepts `auto`, `wined3d`, `dxvk`, `vkd3d`, `vkd3d-proton`, or `none`. `dllPolicy` values normalize to Wine override modes: `disabled`, `native`, `builtin`, `native,builtin`, or `builtin,native`.

During build, WinForge exports the policy environment, applies `winecfg -v <windowsVersion>`, compiles `dllPolicy` into deterministic `WINEDLLOVERRIDES`, and installs `dxvk`/`vkd3d` prefix backends through winetricks when requested. During `run` and OCI app-image launch, WinForge re-exports the same compatibility environment from the embedded graph.

Legacy `config.wine.arch`, `config.wine.windowsVersion`, `config.wine.dllOverrides`, `config.graphics`, and `config.env` normalize into the same policy, but explicit `compatibility` fields override legacy config.

## Source integrity and compatibility evidence

`winforge sources verify <manifest>` emits `schemaVersion: winforge.source-integrity/v0`. The report includes `valid`, `summary`, `items`, `errors`, and `warnings`. Each item records location (`sources[i]`, `install[i].source`, or `filesystem[i].source`), source reference, resolved local path when applicable, expected/actual sha256, and status (`verified`, `present`, `missing`, `hash-mismatch`, `remote`, etc.).

`file://relative/path` and bare relative paths resolve against the selected workspace root. Real v0 builds mount the workspace at `/workspace`, and generated build scripts now resolve relative install/filesystem sources under that mount.

`winforge compat test <manifest>` emits `schemaVersion: winforge.compat-test/v0`. It performs a dependency-light evidence pass: source integrity, dry-run bundle materialization, bundle verification, and run-plan generation. It does not execute Wine or container builds yet; real build/run evidence should build on this report format.

## Artifact model

A WinForge application artifact is the immutable output of a recipe-defined build. The canonical deployable direction is an OCI image digest containing the built application prefix, normalized recipe, metadata, provenance, launch contract, and runtime compatibility metadata.

The current v0 bundle directory contains:

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

This bundle is an internal/debug/staging representation, not the desired final user-facing artifact forever.

## Immutability and runtime state

The recipe-defined build output is immutable. Runtime execution may mutate application state, create save files, export CSV reports, generate caches, run first-launch setup, or let an application install user-managed additions. Those changes must not mutate the sealed artifact.

Runtime state is separate from artifact contents and should be persisted, discarded, exported, or rebuilt explicitly.

## Execution graph

`metadata/graph.json` is build/provenance/contract metadata. It records application identity, requested and resolved builder runtime, requested and resolved runner runtime, supported graphics modes, launch contract, exact-runtime compatibility policy, requested compatibility policy, and deterministic build phase nodes/edges.

The graph should not become a general runtime scheduler. Runtime should be boring: verify artifact, prepare state, start display if requested, and launch the application contract.

## Bundle inspection and verification

`winforge bundle inspect <bundle>` prints a machine-readable summary of the bundle, including application identity, resolved builder/runner runtimes, graphics contract, launch contract, graph node/edge counts, provenance, and required file presence.

`winforge bundle verify <bundle>` validates the v0 bundle contract and exits `0` only when the bundle is valid. Verification checks required files, JSON parseability, supported manifest schema versions, provenance/graph schema versions, graph application identity, runtime consistency across `runtime/runtime.json`, `builderRuntime`, and `runnerRuntime`, launch consistency, exact-runtime compatibility policy, graphics support for `headless` and `vnc`, and required graph nodes.

## Bundle run contract

`winforge run <bundle-or-app-ref>` consumes a verified bundle, either directly by path or resolved through the local artifact index, and must fail before container planning when `winforge bundle verify <bundle>` would fail.

`winforge run --dry-run <bundle>` prints a `winforge.run-plan/v0` document containing the selected runtime image, graphics mode, launch command, container environment, and container argv without starting the container.

`--graphics headless` runs through the runtime image Xvfb entrypoint without publishing ports. `--graphics vnc` publishes loopback-only VNC and noVNC/websockify ports (`127.0.0.1:<vnc-port>:5900` and `127.0.0.1:<novnc-port>:6080`) and starts `x11vnc` plus `websockify` inside the container.

The v0 runner mounts the bundle read-only at `/opt/winforge/bundle`, copies `prefix/` to `/tmp/winforge-prefix`, sets `WINEPREFIX` to that copy, then launches the application entrypoint. This preserves the sealed artifact while allowing Wine to mutate runtime state.

## OCI application image export

`winforge export oci <bundle-or-app-ref> --tag <image> --dry-run` consumes a verified bundle, either directly by path or resolved through the local artifact index, and emits a `winforge.oci-export-plan/v0` document. The plan includes the graph-resolved runtime base image, application identity, requested and resolved runtime versions, OCI labels, `metadata/artifact.json` content, image layout, and generated `Containerfile` content.

`winforge export oci <bundle-or-app-ref> --tag <image>` stages a build context and runs `podman build` or `docker build`. The source bundle is not mutated; export writes `metadata/artifact.json` into the staged copy.

The runnable application image layout is:

```text
/opt/winforge/bundle      immutable embedded bundle
/var/lib/winforge/state   mutable runtime state / copied prefix
/exports                  explicit app/user outputs
/usr/local/bin/winforge-app-launch
```

Embedded artifact metadata uses `schemaVersion: winforge.artifact-image/v0`. OCI labels mirror core metadata such as app name/version, runtime provider, requested/resolved runtime version, runner, launcher, and base image. OCI image digests are the deployable identity; embedded WinForge metadata describes artifact semantics.

`winforge export oci <bundle-or-app-ref> --tag <image> --push` pushes the image and records repo digest identity from image inspection when available. `winforge image verify <image>` emits `winforge.oci-image-verification/v0` and fails if OCI labels disagree with embedded `metadata/artifact.json`, if metadata cannot be read, or if the container engine cannot inspect the image.


## Kubernetes manifest export

`winforge export kube <bundle-or-app-ref> --image <image@sha256:...> --dry-run` consumes a verified bundle, either directly by path or resolved through the local artifact index, and emits `winforge.kube-export/v0`. The plan includes application identity, namespace, Kubernetes resource base name, PVC settings, generated resource objects, and rendered YAML.

`winforge export kube <bundle-or-app-ref> --image <image@sha256:...> --output <file>` writes Kubernetes YAML. It does not apply the manifest.

Digest-pinned image refs are required by default because Kubernetes deployment identity should not depend on mutable tags. `--allow-mutable-tag` exists only for explicit local/demo override.

The v0 manifest emitter creates a Deployment plus state/export PVCs by default. Runtime state mounts at `/var/lib/winforge/state`; exports mount at `/exports`. `--no-pvc` replaces the PVCs with `emptyDir` volumes for smoke/demo manifests. Kubernetes labels are normalized for selector/tooling safety; exact WinForge metadata such as schema, raw app name, app version, and image ref is preserved in annotations.
