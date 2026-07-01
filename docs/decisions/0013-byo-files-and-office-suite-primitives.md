# 0013: BYO files and Office suite primitives

Status: accepted
Date: 2026-07-01

## Context

Microsoft Office-class apps cannot be treated like ordinary public freeware downloads. Users and businesses may have licensed installers, ISOs, archives, or pre-installed application directories. A full BYO Wine prefix import may be convenient for Bottles/Crossover experiments, but it does not answer the core WinForge question: can the application artifact be reproduced from declared source material?

## Decision

WinForge now prioritizes reproducible BYO source materialization over prefix-first import:

- `sources[]` supports normalized source `type` and source/legal `policy` values.
- `filesystem.mode: merge` layers the contents of a customer-provided directory into a Windows target directory, enabling Program Files-style BYO file trees.
- `profiles[]` expands named compatibility/dependency defaults into concrete manifest fields; `office-legacy-32bit` is the first profile.
- `entrypoints[]` and `fileAssociations[]` record multi-entry suite metadata for apps such as Word, Excel, and PowerPoint.
- The compatibility corpus includes Office BYO installer/media and BYO files candidates.

## Consequences

WinForge can now model legal Office-shaped recipes without containing Office payloads. The artifact can declare whether its inputs are `bring-your-own-files`, `bring-your-own-licensed-media`, or another explicit policy.

`mode: merge` makes pre-installed file directories first-class without requiring users to provide a whole Wine prefix. BYO prefix import remains a possible future convenience path, especially for importing personal Bottles, but it is not the primary reproducibility model.

Suite metadata is recorded in manifests and artifacts, but v0 runtime selection still launches the default `launch.entrypoint`. A future phase should add `winforge run <app> --entrypoint <id>` and host-file routing using `fileAssociations`.

WinForge still must not download, encode, or redistribute cracked/pre-activated Office archives or activation bypasses.
