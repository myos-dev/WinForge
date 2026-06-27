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

`provider` must be one of `wine`, `staging`, `proton`, or `proton-ge`. `version` is required. `source`, `channel`, and `digest` should pin provenance.

## Dependencies

Allowed kinds: `winetricks`, `font`, `directx`, `package`, `runtime-component`.

## Install steps

Allowed kinds: `msi`, `exe`, `portable`, `choco`, `script`. MSI/EXE/portable require `source`; script requires `command`.

## Filesystem mappings

Mappings copy declared source files into Windows-style targets under `drive_c`.

## Launch

`entrypoint` is required. `args`, `env`, and `workingDirectory` are optional.

## Bundle layout

A v0 bundle must contain `manifest.winforge.json`, `prefix/drive_c/`, `runtime/runtime.json`, `launch/entrypoint.json`, `metadata/provenance.json`, `build/build-plan.json`, and `logs/build.log`.

## OCI mapping

A bundle may be copied into an OCI image at `/opt/winforge/bundle`. OCI is a distribution wrapper, not the core artifact contract.
