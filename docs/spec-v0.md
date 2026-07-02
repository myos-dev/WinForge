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

`runtime.provider` must be one of `wine`, `staging`, or `umu-proton-ge`. `runtime.version` is required and may be either a pinned runtime image version or a catalog alias such as `latest`, `stable`, `previous`, `legacy`, or `baseline`. Provider/version are selected at build time and enforced at run time; changing providers should require rebuilding. For `umu-proton-ge`, the provider identifies the UMU-backed Proton-family stack while `runtime.version` selects or resolves to the GE-Proton runner tag. Resolved runtime metadata records both `requestedVersion` and `resolvedVersion`; future production artifacts should use the resolved image digest, not a mutable alias, as identity.

`runtime.runner` is optional and selects a downloadable runner archive alias within the provider. Phase 6F adds `pol-8.2`, `pol-4.3`, and `pol-3.0.3` as Wine runner aliases backed by PlayOnLinux/Phoenicis-hosted upstream Wine x86 tarballs. These are not a separate PlayOnLinux provider; they are cacheable Wine runner archives with pinned URL/SHA-256 provenance. Resolved runtime metadata records `runner`, `runnerVersion`, `runnerSource`, `runnerUrl`, `runnerSha256`, and `runnerArch` when a recipe requests a downloadable runner.

`runtime.network` is optional and defaults to `none`. Supported values are `none`, `bridge`, and `host`. This field records runtime network intent for the sealed application artifact, not build-container networking: build containers keep default networking so installers, Winetricks verbs, Chocolatey, Git, and other build-time tooling can download dependencies. The resolved execution graph records network intent under `runnerRuntime.network`, and `winforge run --network <mode>` can override it at operator run time.

`sources` records upstream/local source provenance plus BYO/legal source policy. Supported source `type` values include `installer`, `iso`, `archive`, `files`, `prefix`, `font`, and `other`. Supported source `policy` values include `bring-your-own-files`, `bring-your-own-installer`, `bring-your-own-licensed-media`, `bring-your-own-prefix`, `redistributable`, and fixture/external marker policies. v0 source integrity verifies local `file://` and relative workspace paths plus declared `sha256` values for file sources; remote URLs are recorded but not fetched by the dependency-light verifier.

`profiles` expands named, reviewable compatibility/dependency defaults into concrete recipe fields. The first implemented profile is `office-legacy-32bit`, which adds `win32`, `win7`, Office legacy DLL policy, and the Winetricks verbs from current Office/Bottles evidence. Explicit recipe fields override profile defaults.

`dependencies` supports build-time dependency installation. Allowed kinds: `winetricks`, `font`, `directx`, `package`, `runtime-component`.

`install` supports build-time application installation. Allowed kinds: `msi`, `exe`, `portable`, `choco`, `script`, `bat`, and `cmd`. MSI/EXE/portable/BAT/CMD steps require `source`; script requires `command`. BAT/CMD steps execute through `wine cmd /c`, may declare `workingDirectory`, and are intended for operator-provided installer scripts from legitimate BYO media. Recipes must not use BAT/CMD support to encode activation bypasses, cracked/pre-activated payload flows, or unauthorized licensing automation.

`filesystem` maps declared source files or directories into Windows-style targets under `drive_c`. `filesystem.mode: copy` is the default. `filesystem.mode: merge` copies the contents of a source directory into an existing target directory, enabling BlueBuild-style user-provided file trees such as `Program Files` overlays without nesting the source directory itself.

`config` remains supported for legacy/provider-level configuration. New harder-app recipes should prefer first-class `compatibility` policy for architecture, Windows version, graphics backend, DLL policy, and compatibility environment.

`registry` records build-time registry tweaks.

`launch.entrypoint` is required and remains the default app entrypoint. `launch.args`, `launch.env`, and `launch.workingDirectory` are optional.

`entrypoints` optionally records named suite entrypoints such as `word`, `excel`, and `powerpoint`. `fileAssociations` maps extensions/MIME types to those named entrypoints. v0 records this metadata for artifacts/evidence, and `winforge run <app> --entrypoint writer <file.docx>` routes host files as read-only `Z:` path arguments.

`state` describes runtime state behavior. The default direction is persistent runtime state separate from the immutable artifact.

`exports` describes user/application outputs such as reports, save exports, screenshots, generated documents, or other files that should be mounted or collected explicitly.


## BYO files, profiles, and suite metadata

For apps that are not cleanly installed from a public URL, recipes can model customer-provided material explicitly:

```yaml
sources:
  - id: suite-files
    type: files
    path: sources/vendor-suite/Program Files/Vendor Suite
    policy: bring-your-own-files

filesystem:
  - source: sources/vendor-suite/Program Files/Vendor Suite
    target: C:/Program Files/Vendor Suite
    mode: merge
```

This is the preferred direction for pre-installed file directories. BYO prefix import may be useful for Bottles/Crossover experiments later, but reproducible source materialization from installers/media/files is the core WinForge question. Proprietary app recipes should live in private/customer repositories such as `vic-legacy`, not in public WinForge.

Suite apps can declare multiple entrypoints and file associations:

```yaml
entrypoints:
  - id: writer
    name: Vendor Writer
    executable: C:/Program Files/Vendor Suite/Writer.exe
fileAssociations:
  - entrypoint: writer
    extensions:
      - .docx
    mime:
      - application/vnd.openxmlformats-officedocument.wordprocessingml.document
```

`winforge run` can select a suite entrypoint and pass host files into the container as read-only input mounts:

```bash
winforge run vendor-suite --entrypoint writer ./document.docx
```

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


## Downloadable runner cache

`winforge runners list` emits `winforge.runner-catalog/v0` and lists downloadable runner archive aliases. The initial aliases are:

| Alias | Source | Version | Arch | SHA-256 |
| --- | --- | --- | --- | --- |
| `pol-8.2` | PlayOnLinux/Phoenicis upstream Wine | `8.2` | `x86` | `d38ed5362564c0de73a6f4720a20cf6eece569d2455be2567ac41e1a8a5cb0d6` |
| `pol-4.3` | PlayOnLinux/Phoenicis upstream Wine | `4.3` | `x86` | `64f34fb79de3225bb541fcb8d8c57d0ecf9db2d404e57834096738680c95b29c` |
| `pol-3.0.3` | PlayOnLinux/Phoenicis upstream Wine | `3.0.3` | `x86` | `0b5d59ad852b87ffccf7a72066fd80cb0759647ebd952c2851ce2b5d76ba33c4` |

`winforge runners ensure <alias>` downloads, verifies, and extracts the runner under the local cache, emitting `winforge.runner-cache/v0`. `winforge runners diagnose <alias-or-path>` emits `winforge.runner-diagnostic/v0`; it detects common host/runtime blockers such as missing 32-bit ELF interpreters (`/lib/ld-linux.so.2`) before users get an opaque shell error like “cannot execute: required file not found”.

If a bundle graph contains `runnerRuntime.runner`, `winforge run --runner-cache-dir <dir>` reports `runnerCache` in the `winforge.run-plan/v0`. If the cached runner is present, the plan mounts the runner directory read-only at `/opt/winforge-runner`, exports `WINFORGE_RUNNER_ID` and `WINFORGE_RUNNER_BIN=/opt/winforge-runner/bin`, and prepends that bin directory to `PATH` before launching Wine. Dry-run planning may report `runnerCache.status: missing`; real run execution requires the runner cache to be present. Real build and compatibility evidence commands use the same cache directory to mount the selected runner into the runtime container.

## Source integrity and compatibility evidence

`winforge sources verify <manifest>` emits `schemaVersion: winforge.source-integrity/v0`. The report includes `valid`, `summary`, `items`, `errors`, and `warnings`. Each item records location (`sources[i]`, `install[i].source`, or `filesystem[i].source`), source reference, resolved local path when applicable, expected/actual sha256, and status (`verified`, `present`, `missing`, `hash-mismatch`, `remote`, etc.).

`file://relative/path` and bare relative paths resolve against the selected workspace root. Real v0 builds mount the workspace at `/workspace`, and generated build scripts now resolve relative install/filesystem sources under that mount.

`winforge compat test <manifest>` emits `schemaVersion: winforge.compat-test/v0`. It supports `--mode dry-run`, `--mode build`, and `--mode run`. Dry-run mode records source integrity, dry-run bundle materialization, bundle verification, and run-plan generation. Build mode performs the real container build after source integrity passes and records `metadata/execution-result.json` plus structured build evidence. Run mode adds bounded `winforge.run-result/v0` launch evidence.

`winforge compat test <manifest> --mode build --stop-before install-apps` runs dependency/prefix preparation, seals a checkpoint before application installers, and records the checkpoint in normal build evidence. `--resume-from-bundle <path>` accepts either a bundle root or a compat-output parent, locates the prepared checkpoint, seeds its `prefix/` into a fresh attempt bundle, and records `checkpoint.sourceBundle` plus `checkpoint.attemptBundle` in the evidence payload. `--stop-before` is intentionally limited to build/dry-run modes; run mode requires a full application bundle.

`winforge debug checkpoint inspect <path>` emits `schemaVersion: winforge.checkpoint/v0` and validates a prepared-prefix checkpoint. A checkpoint is valid only when it has `prefix/drive_c`, `manifest.winforge.json`, `runtime/runtime.json`, `metadata/provenance.json`, and `logs/build.log`. If `<path>` is a compat-test output parent, inspection locates a single nested valid bundle and reports that actual bundle root. `winforge debug checkpoint resume <path> --output <dir> [--name <id>]` copies the checkpoint bundle into a fresh mutable attempt directory and writes `metadata/checkpoint-resume.json` without mutating the source checkpoint.

`winforge compat corpus` emits `schemaVersion: winforge.compat-corpus/v0`, a packaged seed app corpus with tiers, statuses, source policies, and compatibility focus tags. The corpus is not an automatic compatibility database; it is the starter list for repeatable evidence collection.

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

`metadata/graph.json` is build/provenance/contract metadata. It records application identity, requested and resolved builder runtime, requested and resolved runner runtime, runner runtime network intent, supported graphics modes, launch contract, exact-runtime compatibility policy, requested compatibility policy, and deterministic build phase nodes/edges.

The graph should not become a general runtime scheduler. Runtime should be boring: verify artifact, prepare state, start display if requested, and launch the application contract.

## Bundle inspection and verification

`winforge bundle inspect <bundle>` prints a machine-readable summary of the bundle, including application identity, resolved builder/runner runtimes, graphics contract, launch contract, graph node/edge counts, provenance, and required file presence.

`winforge bundle verify <bundle>` validates the v0 bundle contract and exits `0` only when the bundle is valid. Verification checks required files, JSON parseability, supported manifest schema versions, provenance/graph schema versions, graph application identity, runtime consistency across `runtime/runtime.json`, `builderRuntime`, and `runnerRuntime`, launch consistency, exact-runtime compatibility policy, graphics support for `headless` and `vnc`, and required graph nodes.

## Bundle run contract

`winforge run <bundle-or-app-ref>` consumes a verified bundle, either directly by path or resolved through the local artifact index, and must fail before container planning when `winforge bundle verify <bundle>` would fail.

`winforge run --dry-run <bundle>` prints a `winforge.run-plan/v0` document containing the selected runtime image, runtime network mode, graphics mode, selected suite entrypoint, optional host-file routing, optional runner-cache mount, launch command, container environment, and container argv without starting the container. `--entrypoint <id>` selects a named `entrypoints[]` item. `--network none|bridge|host` overrides the bundle's `runnerRuntime.network` intent for that run; if the manifest does not declare network intent, the selected run default is `none`, which emits `--net none` in the container argv so Win32 applications are air-gapped by default. Additional positional file paths are mounted read-only under `/mnt/winforge-inputs/<n>` and passed to Wine as `Z:\mnt\winforge-inputs\<n>\<name>` arguments.

`--graphics headless` runs through the runtime image Xvfb entrypoint without publishing ports and is compatible with all network modes. `--graphics vnc` requires `--network bridge`; that is the only local container mode where Docker/Podman host port publishing can bind VNC/noVNC access to host loopback (`127.0.0.1:<vnc-port>:5900` and `127.0.0.1:<novnc-port>:6080`). The VNC helpers still listen inside the container, so bridge-mode VNC should not be attached to an untrusted/shared container network. VNC is rejected with `network: none` because the ports would be unusable, and with `network: host` because the container's unauthenticated `x11vnc`/`websockify` listeners could be exposed on host interfaces.

The v0 runner mounts the bundle read-only at `/opt/winforge/bundle`, copies `prefix/` to `/tmp/winforge-prefix`, sets `WINEPREFIX` to that copy, then launches the application entrypoint. When a cached runner is mounted, it lives at `/opt/winforge-runner` and is selected through `PATH`/`WINFORGE_RUNNER_BIN`. This preserves the sealed artifact while allowing Wine to mutate runtime state.

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

Kubernetes export reads `runnerRuntime.network` from the bundle graph. `network: none` emits `hostNetwork: false` plus a deny-all-egress `NetworkPolicy`; enforcement requires a NetworkPolicy-capable cluster CNI, and this policy only controls egress. `network: host` emits `hostNetwork: true` and no deny-egress policy. `network: bridge` maps to ordinary pod networking (`hostNetwork: false`) without the deny-egress policy. Kubernetes does not have a direct Docker/Podman `--net` flag, so these outputs are the closest v0 deployment intent mapping rather than a byte-for-byte runtime equivalent.
