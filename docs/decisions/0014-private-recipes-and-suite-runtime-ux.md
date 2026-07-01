# 0014: Private recipes and suite runtime UX

Status: accepted
Date: 2026-07-01

## Context

WinForge should provide reusable packaging/runtime primitives, but public WinForge should not ship Office containers or Office recipes. Proprietary/customer recipes need a private or customer-owned home. For Skylabs/VIC work, that home is `vic-legacy`.

Phase 6D added suite metadata. Phase 6E makes that metadata executable through the local run/evidence path without adding public Office recipes.

## Decision

- Public WinForge must not ship Office containers or Office recipe files.
- Office-shaped and proprietary/customer recipes should live under `vic-legacy` or customer/private repositories.
- `winforge run` supports `--entrypoint <id>` to select a named suite entrypoint.
- Host file arguments are mounted read-only under `/mnt/winforge-inputs/<n>` and passed into Wine as `Z:\mnt\winforge-inputs\<n>\<filename>`.
- `winforge compat test` supports repeatable `--entrypoint`, `--all-entrypoints`, and repeatable `--file` to collect per-entrypoint run-plan/run evidence.

## Consequences

WinForge remains publicly reusable and application-first while keeping private/proprietary recipes out of the public repo. VIC can maintain restricted recipe examples and customer-specific app material under `vic-legacy`.

The run-plan contract now includes selected suite entrypoint metadata, file-argument routing metadata, read-only input mounts, and per-entrypoint compatibility evidence.
