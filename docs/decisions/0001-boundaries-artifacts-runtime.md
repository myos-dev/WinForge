# Decision 0001: WinForge Boundary, Artifact Model, and Runtime Abstraction

Date: 2026-06-27

Status: accepted (amended by Decision 0002)

## Decision

WinForge is an open-source reproducible environment compiler for Wine-based Windows execution environments. It produces immutable execution bundles from declarative manifests. Runtime selection uses a pluggable provider abstraction for Wine Stable, Wine Staging, and Proton-GE. VIC is a downstream consumer and must not be a dependency or internal concern.

## Reasoning

This separates build-time environment construction from production orchestration and keeps WinForge useful outside VIC.

## Rejected alternatives

- Put WinForge logic directly inside VIC.
- Make Kubernetes the core builder substrate.
- Hardcode Wine/Proton variants in builder phases.

## Review triggers

Review if WinForge needs a remote build service API, OCI packaging becomes mandatory, VIC integration starts requiring builder internals, or a new runtime class needs first-class support.
