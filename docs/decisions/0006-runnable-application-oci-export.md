# Decision 0006: Runnable Application OCI Export

Date: 2026-06-28

Status: accepted

## Decision

WinForge exports application bundles as **runnable application OCI images**. The image is based on the graph-resolved runtime image and embeds the verified bundle at `/opt/winforge/bundle`.

The exported image includes an application launcher at `/usr/local/bin/winforge-app-launch`, embedded WinForge artifact metadata at `/opt/winforge/bundle/metadata/artifact.json`, OCI labels that mirror core metadata, and explicit mutable paths for runtime state and exports:

```text
/opt/winforge/bundle      immutable embedded bundle
/var/lib/winforge/state   mutable runtime state / copied prefix
/exports                  explicit app/user output surface
/usr/local/bin/winforge-app-launch
```

`winforge export oci <bundle> --tag <image> --dry-run` emits `winforge.oci-export-plan/v0` without building. `winforge export oci <bundle> --tag <image>` stages a build context and runs `podman build` or `docker build`.

## Contract

The OCI export contract consumes a verified bundle. Export fails before planning/building if `winforge bundle verify <bundle>` would fail.

The plan and embedded artifact metadata record both the originally requested runtime version and the resolved pinned runtime version. Mutable aliases such as `latest` are never artifact identity.

The exported application image is runnable because it contains:

- the resolved runtime base image via `FROM <runnerRuntime.image>`;
- the sealed bundle copied under `/opt/winforge/bundle`;
- `metadata/artifact.json` with `schemaVersion: winforge.artifact-image/v0`;
- `winforge-app-launch`, which prepares mutable runtime state and launches through `wine` or `umu-run` according to graph runtime metadata.

## Reasoning

A runnable app image keeps the UX application-first and lets a single image tag/digest identify the deployable application artifact. OCI storage still deduplicates the runtime base layers, while WinForge metadata records the runtime provider, runner, launcher, and resolved version used to build the artifact.

Keeping runtime state outside `/opt/winforge/bundle` preserves the sealed artifact. Runtime mutation, saves, caches, first-launch changes, and user exports belong in `/var/lib/winforge/state` or `/exports`, not in the embedded bundle.

## Rejected alternatives

- Export artifact-only data images that require a separate runtime image plus init-copy glue at launch time.
- Treat `latest` as the artifact identity instead of resolving it to a pinned runner version.
- Mutate the source bundle directory by writing export-specific metadata into it. Export stages a build context and writes `artifact.json` into the staged copy instead.

## Review triggers

Review if WinForge starts publishing digests, adds registry-backed artifact indexes, verifies OCI label/metadata consistency after pull, or changes runtime-state snapshot/export semantics.
