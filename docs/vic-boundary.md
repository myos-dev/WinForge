# VIC / WinForge Boundary

WinForge and VIC must stay separate.

## 1. What part of VIC interacts with WinForge?

Only the VIC artifact-consumption/runtime orchestration side should interact with WinForge outputs: registry integration, runtime job launcher, compatibility-pack catalog, or worker path that references a WinForge bundle/OCI image.

## 2. What artifacts does VIC consume?

Sealed execution bundles or OCI images, normalized manifest metadata, runtime binding metadata, launch definitions, provenance, and build logs.

## 3. Where does VIC begin and WinForge end?

WinForge ends when an artifact is sealed. VIC begins when that sealed artifact is selected, scheduled, governed, audited, and exposed through product workflows.

## 4. Why VIC must not contain WinForge logic

Reproducibility, open-source boundary, consumer neutrality, auditability, security, and maintainability all require the artifact to be buildable outside VIC.

## 5. What integration boundary exists?

Preferred boundaries: OCI image, bundle archive/directory, CLI contract, and possibly a future artifact build API. VIC should never depend on private modules inside WinForge.
