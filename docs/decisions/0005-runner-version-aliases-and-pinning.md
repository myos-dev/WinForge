# Decision 0005: Runner Version Aliases and Pinned Runtime Images

Date: 2026-06-28

Status: accepted

## Decision

WinForge supports mutable runtime version aliases such as `latest`, `stable`, `previous`, `legacy`, and `baseline`, but aliases are not artifact identity. The runtime catalog resolves aliases to pinned runner versions before a bundle, graph, run plan, or future OCI application image is written.

Runtime metadata records both:

- `requestedVersion`: what the recipe or CLI asked for, such as `latest`
- `resolvedVersion`: the concrete runner version selected by the catalog, such as `11.0` or `GE-Proton11-1`

`version` in resolved runtime metadata is the concrete resolved version.

## Current curated runner set

WinForge does not mirror every upstream release. v0 carries a small curated support matrix:

| Provider | Alias policy | Pinned versions |
| --- | --- | --- |
| `wine` | `latest`/`stable` -> `11.0`; `previous` -> `10.0`; `legacy` -> `9.0` | `11.0`, `10.0`, `9.0` |
| `staging` | `latest`/`staging-latest` -> `11.10`; `previous` -> `11.9`; `baseline` -> `11.0` | `11.10`, `11.9`, `11.0` |
| `umu-proton-ge` | `latest` -> `GE-Proton11-1`; `previous` -> `GE-Proton10-34`; `legacy` -> `GE-Proton9-27` | `GE-Proton11-1`, `GE-Proton10-34`, `GE-Proton9-27` |

## Wine package pinning

Wine and Wine Staging images must install exact WineHQ package versions from the catalog. Image tags alone are not enough. The catalog passes `WINE_PACKAGE_VERSION`, and Dockerfiles install the WineHQ metapackage plus the exact root and architecture packages:

```bash
winehq-stable=${WINE_PACKAGE_VERSION}
wine-stable=${WINE_PACKAGE_VERSION}
wine-stable-amd64=${WINE_PACKAGE_VERSION}
wine-stable-i386:i386=${WINE_PACKAGE_VERSION}

winehq-staging=${WINE_PACKAGE_VERSION}
wine-staging=${WINE_PACKAGE_VERSION}
wine-staging-amd64=${WINE_PACKAGE_VERSION}
wine-staging-i386:i386=${WINE_PACKAGE_VERSION}
```

This prevents an image tagged `11.0` from accidentally installing whatever WineHQ currently serves as the default package. It also avoids apt 3 solver failures observed with older exact WineHQ metapackage pins, where `winehq-stable=10.0...` or `winehq-staging=11.9...` did not automatically select the matching exact root package candidate unless the root and arch packages were pinned explicitly.

## OCI tag policy

CI builds pinned versions only. Alias tags are published only by the pinned catalog entry that owns the alias. For example, the `wine` `11.0` matrix job may publish `:11.0`, `:latest`, and `:stable`, while the `10.0` job may publish `:10.0` and `:previous`.

CI must not have multiple parallel jobs racing to publish the same `latest` tag. Commit-SHA tags are disambiguated by runtime tag.

## Consequences

- Recipes may use `runtime.version: latest` for convenience.
- Bundles and run plans preserve the requested alias but execute with the resolved pinned image.
- Customer/production artifacts should depend on the resolved runtime image digest, not the alias tag.
- Future catalog refresh automation should propose alias moves in reviewable diffs rather than silently rewriting pinned artifacts.
