# WinForge Manifest and Artifact Spec v0

Status: proposed initial scaffold.

## Required manifest fields

| Field | Type | Meaning |
| --- | --- | --- |
| `schemaVersion` | string | Must be `winforge.dev/v0` |
| `name` | string | Artifact/environment name |
| `version` | string | Artifact version |
| `runtime` | object | Runtime provider request |
| `launch` | object | Entrypoint definition |

## Runtime

`provider` must be one of `wine`, `staging`, or `proton-ge`. `version` is required. `source`, `channel`, and `digest` should pin provenance.

## Dependencies

Allowed kinds: `winetricks`, `font`, `directx`, `package`, `runtime-component`.

## Install steps

Allowed kinds: `msi`, `exe`, `portable`, `choco`, `script`. MSI/EXE/portable require `source`; script requires `command`.

## Filesystem mappings

Mappings copy declared source files into Windows-style targets under `drive_c`.

## Launch

`entrypoint` is required. `args`, `env`, and `workingDirectory` are optional.

## Bundle layout

A v0 bundle must contain `manifest.winforge.json`, `prefix/drive_c/`, `runtime/runtime.json`, `launch/entrypoint.json`, `metadata/provenance.json`, `metadata/graph.json`, `build/build-plan.json`, and `logs/build.log`.

## Execution graph

`metadata/graph.json` is the resolved execution contract. It records the application identity, resolved builder runtime, resolved runner runtime, supported graphics modes, launch contract, exact-runtime compatibility policy, and deterministic nodes/edges for the build phases and produced bundle artifact. The graph separates the runtime OCI image from the application/prefix artifact so future `winforge run` can pull a runtime image and a workload artifact independently.

## Bundle inspection and verification

`winforge bundle inspect <bundle>` prints a machine-readable summary of the bundle, including application identity, resolved builder/runner runtimes, graphics contract, launch contract, graph node/edge counts, provenance, and required file presence.

`winforge bundle verify <bundle>` validates the v0 bundle contract and exits `0` only when the bundle is valid. Verification checks required files, JSON parseability, manifest/provenance/graph schema versions, graph application identity, runtime consistency across `runtime/runtime.json`, `builderRuntime`, and `runnerRuntime`, launch consistency, exact-runtime compatibility policy, graphics support for `headless` and `vnc`, and required graph nodes.

## Bundle run contract

`winforge run <bundle>` consumes a verified bundle and must fail before container planning when `winforge bundle verify <bundle>` would fail. It does not reinterpret the original manifest as the source of truth; it reads `metadata/graph.json` for the resolved `runnerRuntime`, launch contract, graphics modes, and exact-runtime compatibility policy.

`winforge run --dry-run <bundle>` prints a `winforge.run-plan/v0` document containing the selected runtime image, graphics mode, launch command, container environment, and container argv without starting the container.

`--graphics headless` runs through the runtime image Xvfb entrypoint without publishing ports. `--graphics vnc` publishes loopback-only VNC and noVNC/websockify ports (`127.0.0.1:<vnc-port>:5900` and `127.0.0.1:<novnc-port>:6080`) and starts `x11vnc` plus `websockify` inside the container.

The v0 runner mounts the bundle read-only at `/opt/winforge/bundle`, copies `prefix/` to `/tmp/winforge-prefix`, sets `WINEPREFIX` to that copy, then launches the graph entrypoint. This preserves the sealed bundle while allowing Wine to mutate runtime prefix state.

## OCI mapping

A bundle may be copied into an OCI image at `/opt/winforge/bundle`. OCI is a distribution wrapper, not the core artifact contract.
