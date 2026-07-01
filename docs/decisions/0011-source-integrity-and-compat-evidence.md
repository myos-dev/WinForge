# Decision 0011: Source Integrity and Compatibility Evidence

Date: 2026-07-01

Status: accepted

## Decision

WinForge supports a dependency-light compatibility evidence loop before real Wine execution. The first implementation includes:

- `winforge sources verify <manifest>` emitting `winforge.source-integrity/v0`;
- `winforge compat test <manifest>` emitting `winforge.compat-test/v0`;
- generated build scripts resolving relative install/filesystem sources against the mounted workspace (`/workspace/...`) instead of the container process cwd.

## Implemented behavior

`winforge.source-integrity/v0` reports each declared source, install source, and filesystem source with resolved path, expected/actual sha256, status, warnings, and errors. Local `file://` and relative paths are verified. Remote URLs are recorded but not fetched by this verifier; v0 install/filesystem steps must be materialized locally before real build.

`winforge.compat-test/v0` records:

- manifest/application/runtime/compatibility metadata;
- source integrity result;
- dry-run bundle materialization;
- bundle verification;
- run-plan evidence with selected graphics mode, engine, runtime image, and compatibility environment.

The command does not execute Wine or containers yet. Real build/run evidence should extend this envelope rather than invent another result format.

## Reasoning

Hard-app compatibility work needs evidence, but early failures should identify cheap blockers first: missing installers, bad hashes, unresolved overlays, graph drift, or launch-policy drift. Running Wine before these checks wastes time and produces noisier failures.

## Boundary

This is an evidence harness, not automatic runtime selection and not a compatibility database. It does not fetch remote sources, install dependencies, run OCR/UI automation, or schedule Kubernetes workloads.

## Review triggers

Review this decision when adding real container execution to `compat test`, remote source fetching/cache, compatibility profiles, curated app corpus reporting, or automatic runtime recommendations.
