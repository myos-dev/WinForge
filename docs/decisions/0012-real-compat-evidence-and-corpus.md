# Decision 0012: Real Compatibility Evidence Modes and Seed Corpus

Date: 2026-07-01

Status: accepted

## Decision

`winforge compat test` now supports three evidence depths:

- `--mode dry-run`: source integrity, dry-run bundle, bundle verification, and run-plan evidence;
- `--mode build`: source integrity plus real container build evidence;
- `--mode run`: real build evidence plus bounded app launch evidence.

WinForge also packages a seed compatibility corpus exposed by `winforge compat corpus` as `winforge.compat-corpus/v0`.

## Reasoning

Harder Windows app images need evidence at multiple depths. A missing installer or bad hash should fail before Wine starts. A build failure should be distinguishable from a launch failure. A launch failure should preserve the run result instead of collapsing into an opaque CLI exit code.

A curated corpus gives the project a stable starting set for repeatable compatibility work without pretending to have automatic runtime selection or a complete compatibility database.

## Implemented behavior

`winforge.compat-test/v0` records the selected mode, manifest/application/runtime/compatibility metadata, source integrity, build evidence, bundle verification, run plan, optional run result, success, and classification.

Current classifications include:

- `dry-run-planned`
- `source-integrity-failed`
- `build-passed`
- `build-failed`
- `bundle-verification-failed`
- `run-passed`
- `run-failed`
- `harness-error`

Real build mode passes the selected workspace to the container executor so local recipe sources are mounted from the same workspace used by `sources verify`. The executor runs `/opt/winforge/build/run.sh` inside the mounted bundle, not the host-side script path.

The seed corpus includes starter entries such as Notepad++, 7-Zip, PuTTY, WinSCP, DB Browser for SQLite, synthetic .NET/COM fixtures, and blocked driver-required app classes.

## Boundary

This is not automatic runtime selection, not a global compatibility database, and not VM fallback. The corpus is curated input for evidence collection. Runtime recommendations should only come later after repeated evidence supports them.

## Review triggers

Review this decision when adding remote source fetching/cache, corpus result aggregation, runtime recommendation logic, VM fallback, or VIC-managed compatibility testing.
