# Decision 0004: `umu-proton-ge` Runtime Stack Naming

Date: 2026-06-28

Status: accepted

## Decision

Replace the active `proton-ge` provider ID with `umu-proton-ge`.

WinForge uses these terms:

- **provider ID**: the catalog-facing runtime stack selected by recipes/CLI (`wine`, `staging`, `umu-proton-ge`)
- **launcher**: how the runtime is invoked (`wine` or `umu`)
- **runner**: the concrete Wine/Proton build inside the image (Wine Stable, Wine Staging, GE-Proton)
- **runner version**: the concrete runner version/tag, such as `GE-Proton9-27`

For the Proton-family v0 stack, the provider is `umu-proton-ge`, the launcher is `umu`, and the runner/version is GE-Proton / `GE-Proton9-27`.

## Reasoning

`proton-ge` alone made the OCI image name sound like the launcher and runner were the same thing. In practice, non-Steam Proton execution should go through UMU, while GE-Proton is the runner being selected. The name `umu-proton-ge` makes the stack explicit without exposing users to all internal launch details.

Wine and Wine Staging remain direct Wine-launcher runners. UMU is not a generic replacement for every custom Wine runner; it is the launcher/capability layer for Proton-family execution outside Steam.

## Consequences

- Active provider IDs are now `wine`, `staging`, and `umu-proton-ge`.
- Legacy `proton-ge` is not an active provider alias in v0.
- Runtime images use `winforge/umu-proton-ge` locally and `ghcr.io/myos-dev/winforge-umu-proton-ge` when published.
- The UMU-backed image installs `umu-launcher` and exposes `umu-run`.
- Recipes still select an application runtime provider/version; future recipe/schema work can add more explicit `family`, `runner`, and `launcher` fields if needed.
