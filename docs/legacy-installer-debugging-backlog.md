# Legacy installer debugging backlog

Status: proposed
Date: 2026-07-01
Source evidence: private BYO legacy-installer probes recorded outside public WinForge

## Context

Hard business installers need more than a generic `wine setup.exe` path. A private BYO Office 2010 probe in `vic-legacy` exposed generic WinForge platform gaps around media staging, prepared-prefix reuse, visible installer debugging, and failure summarization. This document turns those lessons into public WinForge development slices without adding proprietary recipes, payloads, activation flows, or customer-specific logic to WinForge.

## Boundaries

WinForge should implement reusable primitives only:

- source/media staging and policy preflight
- installer command construction and linting
- prepared-prefix checkpoint inspection/reuse
- visible Wine installer debugging
- redacted failure evidence collection
- runtime/profile matrix execution

WinForge must not ship Office recipes, Office containers, Office payloads, product keys, activated prefixes, KMS emulators, cracked/pre-activated media handling, or activation-bypass automation. Proprietary/customer recipes and evidence notes belong in `vic-legacy` or customer/private repositories.

## Proposed implementation slices

### Slice 1: media staging and source-policy preflight

Status: implemented in initial v0 on `hermes-0.1/legacy-installer-followup`.

Goal: make BYO ISO/archive/file media setup reproducible and safe before Wine execution.

Implemented CLI:

```bash
winforge media stage <source> --name <id> --workspace <dir>
winforge sources audit <recipe> --workspace <dir>
```

Touched files:

- `core/media.py`
- `core/sources.py`
- `winforge/cli.py`
- `tests/test_media_staging_and_policy.py`

Acceptance criteria:

- ISO/archive extraction normalizes ownership and user-writable modes in the staged copy.
- Staging produces a small metadata file with source path, staged path, file count, byte size, and hash data where available.
- Policy audit flags suspicious names/patterns such as activation/KMS/crack/bypass artifacts without executing them.
- Audit output is machine-readable and avoids reading source file contents into the report.
- Tests cover clean media, suspicious media, read-only copied media, missing source, and archive path traversal prevention.

### Slice 2: installer script linter

Goal: catch common Windows installer script hazards before a long Wine run.

Likely files:

- `core/manifest.py`
- `builder/pipeline.py`
- possibly new `core/install_lint.py`
- `winforge/cli.py`

Acceptance criteria:

- BAT/CMD install steps are scanned before execution.
- `start setup.exe ...` without `/wait` emits a warning that the build may exit early.
- Relative paths and missing `workingDirectory` produce actionable warnings for BAT/CMD steps.
- Warnings are attached to dry-run, inspect, and compat evidence output.
- No linter warning authorizes activation bypass or cracked/pre-activated flows.

### Slice 3: prepared-prefix checkpoint inspection and resume

Status: implemented in initial v0 on `hermes-0.1/legacy-installer-followup`.

Goal: make slow dependency/prefix prep reusable without manual path hunting.

Implemented CLI:

```bash
winforge debug checkpoint inspect <path>
winforge debug checkpoint resume <path> --output <dir> [--name <id>]
winforge compat test <recipe> --mode build --stop-before install-apps
winforge compat test <recipe> --mode build --resume-from-bundle <bundle-or-output-parent>
```

Touched files:

- `artifact/checkpoint.py`
- `compat/evidence.py`
- `builder/executor.py`
- `builder/pipeline.py`
- `winforge/cli.py`
- `tests/test_checkpoint_resume.py`

Acceptance criteria:

- Inspection distinguishes a compat-test output parent from the actual nested bundle root.
- A checkpoint is valid only when `prefix/drive_c`, manifest/runtime metadata, and logs/provenance are present.
- Resume copies the checkpoint into a fresh attempt directory before mutation.
- Compat evidence records the source checkpoint path and the new attempt path.
- Tests cover valid nested bundle, invalid parent directory, missing prefix, immutable-source copy behavior, CLI inspect/resume, compat resume evidence, and stop-before build-script generation.

### Slice 4: visible installer debug command

Goal: replace hand-written Podman/noVNC debug scripts with a supported path.

Candidate CLI:

```bash
winforge debug installer <bundle> \
  --media <path> \
  --command "setup.exe /config ProPlus.WW/config.xml" \
  --graphics vnc \
  --network bridge
```

Likely files:

- new `debug/installer.py` or `runtime/debug_installer.py`
- `runtime/launcher.py`
- `builder/executor.py`
- `winforge/cli.py`

Acceptance criteria:

- Starts VNC/noVNC with loopback-only ports by default.
- Prints the noVNC URL and the exact container name.
- Mounts bundle writable as attempt state and source media read-only.
- Captures x11vnc/websockify logs, installer stdout/stderr, and Wine temp logs.
- Handles nonzero installer exit codes without losing the return code to shell `errexit`/`pipefail` behavior.
- Supports optional runner-cache mounting.

### Slice 5: Windows installer failure analysis

Status: implemented in initial v0 on `hermes-0.1/legacy-installer-followup`.

Goal: turn large Wine/Windows installer logs into a concise, redacted failure report.

Implemented CLI:

```bash
winforge failure analyze <bundle-or-log-path>
```

Touched files:

- `compat/failure_analysis.py`
- `compat/evidence.py`
- `winforge/cli.py`
- `tests/test_failure_analysis.py`

Acceptance criteria:

- Collects logs from common Wine temp paths such as `drive_c/users/<user>/Temp` and `drive_c/windows/temp`.
- Summarizes top-level return code, first failed package, rollback packages, and installed executable presence.
- For MSI-style logs, prioritizes `Return value 3`, `MSI(ERROR)`, `ErrorCode`, and first failure windows over rollback spam.
- Redacts product-key-like tokens and common secret patterns in human summaries.
- Writes `metadata/failure-analysis.json` and `metadata/failure-summary.md` when a build/debug run fails.

### Slice 6: runtime/profile matrix runner

Goal: make hard compatibility research systematic instead of one-off shell retries.

Candidate CLI:

```bash
winforge compat matrix <recipe> \
  --runner pol-8.2,pol-4.3,pol-3.0.3 \
  --windows-version win7,win10 \
  --graphics headless \
  --output dist/matrix-<app>
```

Likely files:

- new `compat/matrix.py`
- `compat/evidence.py`
- `runtime/runner_cache.py`
- `builder/executor.py`
- `winforge/cli.py`

Acceptance criteria:

- Matrix dimensions are explicit and recorded in each run.
- Each cell gets a fresh attempt directory and independent evidence envelope.
- Failures are summarized consistently through the failure-analysis layer.
- Output includes a sortable comparison table with pass/fail/error/timeout and key failure signature.

## Recommended next development order

1. **Slice 3: checkpoint inspect/resume** — next recommended slice; makes slow dependency work reusable.
2. **Slice 4: visible installer debug command** — removes ad hoc noVNC scripts once the evidence/reporting contracts are in place.
3. **Slice 2: installer script linter** — small but useful guardrail; can be done earlier if touching install parsing.
4. **Slice 6: runtime/profile matrix runner** — best after failure summaries are stable.

## Review triggers

Create or update an ADR if any slice changes the public recipe schema, compatibility policy schema, artifact graph schema, or runtime/container trust boundary. Keep all Office/customer-specific evidence in private repositories.
