# Decision 0010: Compatibility Policy Layer

Date: 2026-07-01

Status: accepted

## Decision

WinForge supports a first-class `compatibility` recipe block for harder Wine/Proton application images. This block is a high-level app/runtime policy layer above Wine internals, not a raw Wine loader-control DSL.

Supported v0 fields:

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

Legacy `config.wine.arch`, `config.wine.windowsVersion`, `config.wine.dllOverrides`, `config.graphics`, and `config.env` are normalized into the same policy, but explicit `compatibility` fields override legacy config.

## Implemented behavior

WinForge now:

- normalizes compatibility policy into `schemaVersion: winforge.compatibility-policy/v0`;
- rejects unsupported graphics backends, DLL policy values, env names, and architecture/window-version values;
- compiles DLL policy to deterministic `WINEDLLOVERRIDES`;
- applies `WINEARCH`, compatibility env, and DLL overrides during prefix build;
- applies `winecfg -v <windowsVersion>` after prefix initialization;
- installs requested `dxvk` or `vkd3d` graphics backend into the prefix through winetricks during build;
- records the requested policy in `manifest.winforge.json`, `metadata/graph.json`, provenance, OCI artifact metadata, run plans, and the OCI app launcher path;
- propagates the same policy when running bundles and exported app images.

## Boundary

WinForge should expose compatibility intent, not raw implementation internals. `dllPolicy` supports stable native/builtin/disabled choices. Explicit DLL load ordering, loader traces, COM timing controls, per-function hooks, and automatic fixup engines remain advanced/debug/research territory until concrete app failures require them.

Graphics backend selection is now part of the artifact contract, but runtime-image capability metadata is still future work. For v0, `dxvk` and `vkd3d` use winetricks during prefix build; runtime images still need host/container graphics capability to execute those apps correctly.

## Reasoning

Founder/operator experience says harder Windows applications often need deliberate graphics backend and DLL override policy. Testing hard apps against a generic Wine image produces weak evidence; WinForge needs to compile declared compatibility policy into the artifact before collecting compatibility results.

## Rejected alternatives

- Keep compatibility controls as speculative future work.
- Expose raw DLL load-order lists as primary schema.
- Hide all policy in unstructured shell scripts.
- Treat graphics backend as runtime-only state instead of artifact metadata.

## Review triggers

Review this decision when adding runtime capability metadata, compatibility profiles, automatic runtime selection, COM/UIA automation bridges, prefix diffing, or VM fallback.
