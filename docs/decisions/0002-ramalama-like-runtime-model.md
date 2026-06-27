# Decision 0002: Ramalama-like Runtime Model and Active Providers

Date: 2026-06-27

Status: accepted

## Decision

WinForge follows a Ramalama-like model: user commands resolve a runtime provider from a catalog, pull the runtime OCI image, apply user-selected execution options, and run a separate application/prefix artifact. OCI is the normal transport and cache surface, but the execution graph and bundle remain the semantic source of truth.

WinForge has two distinct OCI roles:

1. **Runtime image** — e.g. `ghcr.io/myos-dev/winforge-wine:9.0`; provides Wine/Staging/GE-Proton runtime, tools, entrypoints, and graphics support.
2. **Application/prefix artifact** — the future OCI wrapper for a resolved WinForge execution bundle containing prefix state, launch contract, graph metadata, and provenance.

The active v0 runtime providers are:

- `wine`
- `staging`
- `proton-ge`

Valve Proton is removed as an active provider for now because upstream GitHub releases are source-only and do not provide a prebuilt runnable Proton runtime. Proton-GE remains the active Proton-family runtime. Other runners can be added later through the catalog.

## Scope

WinForge should be Podman-native in UX and support local build/test/deploy, OCI export/import, graphics modes (`headless` and visible/VNC), and Kubernetes manifest generation. WinForge should not become the full production orchestrator; advanced multi-tenant/session orchestration belongs to downstream systems such as VIC.

## Reasoning

The Ramalama analogy clarifies the separation of concerns:

- model weights/application bundle are not the runtime container;
- backend/runtime selection is catalog-driven;
- CLI flags choose execution behavior;
- OCI is transport/cache/deployment plumbing, not the semantic model.

For Wine, the divergence is that prefixes are stateful OS-like artifacts, so the execution graph must record builder runtime, runner runtime, graphics mode, launch contract, and compatibility policy.

## Rejected alternatives

- Treat each app environment as a single monolithic Docker image with runtime, prefix, app, and execution semantics baked together.
- Keep Valve Proton as an active runtime while it is only a source seed.
- Make WinForge responsible for production-grade orchestration rather than local/devops-oriented build, run, export, and manifest generation.

## Review triggers

Review this decision when WinForge adds a real Valve Proton binary acquisition path, introduces new non-GE Proton-family runners, or starts implementing production orchestration beyond local/devops/kube-manifest flows.
