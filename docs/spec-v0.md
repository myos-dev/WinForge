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

## OCI mapping

A bundle may be copied into an OCI image at `/opt/winforge/bundle`. OCI is a distribution wrapper, not the core artifact contract.
