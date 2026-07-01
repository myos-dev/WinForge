"""Build phase planning and Wine build script generation for WinForge.

Converts a Manifest into executable build phases that run inside a
WinForge Wine/Proton OCI container.
"""
from __future__ import annotations
import json, shlex
from core.compatibility import compatibility_environment
from core.sources import container_source_path
from core.manifest import Manifest

PHASE_ORDER = [
    "init-prefix",
    "install-dependencies",
    "install-apps",
    "apply-layout-and-registry",
    "validate",
    "seal-artifact",
]


def build_plan(manifest: Manifest) -> list[dict[str, object]]:
    """Return structured phase breakdown for the *plan* CLI command."""
    return [
        {
            "phase": "init-prefix",
            "inputs": ["runtime", "manifest"],
            "actions": [
                "create empty WINEPREFIX directory",
                "initialize drive_c and registry hives",
                f"bind runtime provider {manifest.runtime.provider}:{manifest.runtime.version}",
            ],
        },
        {
            "phase": "install-dependencies",
            "inputs": ["dependencies"],
            "actions": [_plan_dep(d) for d in manifest.dependencies]
            or ["no dependencies declared"],
        },
        {
            "phase": "install-apps",
            "inputs": ["install"],
            "actions": [_plan_inst(s) for s in manifest.install]
            or ["no application install steps declared"],
        },
        {
            "phase": "apply-layout-and-registry",
            "inputs": ["filesystem", "registry/scripts"],
            "actions": [f"map {m.source} -> {m.target}" for m in manifest.filesystem]
            or ["no explicit filesystem mappings declared"],
        },
        {
            "phase": "validate",
            "inputs": ["launch", "runtime", "prefix"],
            "actions": [
                f"verify launch entrypoint exists at {manifest.launch.entrypoint}",
                "record dependency and source hashes",
                "emit build logs and normalized manifest",
            ],
        },
        {
            "phase": "seal-artifact",
            "inputs": ["prefix", "runtime binding", "manifest", "metadata"],
            "actions": [
                "mark bundle immutable",
                "write provenance metadata",
                "optionally map bundle into an OCI image layer layout",
            ],
        },
    ]


def _compatibility_policy_lines(manifest: Manifest) -> list[str]:
    """Return shell lines that export high-level compatibility policy."""
    policy = manifest.compatibility or {}
    if not policy:
        return []

    lines = [
        "### Compatibility policy #######################################",
        'echo "[winforge] Compatibility policy"',
    ]
    for key, value in compatibility_environment(policy).items():
        lines.append(f"export {key}={_shell_quote(str(value))}")
        if key == "WINEDLLOVERRIDES":
            lines.append('echo "  WINEDLLOVERRIDES=<compiled compatibility policy>"')
        elif key.startswith("WINFORGE_GRAPHICS_"):
            lines.append(f'echo "  {key}={value}"')
        elif key in {"WINEDEBUG", "DXVK_LOG_LEVEL"}:
            lines.append(f'echo "  env {key}=<set>"')
        else:
            lines.append(f'echo "  {key}={value}"')

    lines.extend(['echo ""', ""])
    return lines


def _compatibility_post_wineboot_lines(manifest: Manifest) -> list[str]:
    """Return shell lines that require an initialized Wine prefix."""
    policy = manifest.compatibility or {}
    if not policy:
        return []
    lines: list[str] = []
    windows_version = policy.get("windowsVersion")
    if windows_version:
        lines.extend([
            f'echo "[winforge]   Setting Windows version: {windows_version}"',
            f"winecfg -v {shlex.quote(str(windows_version))} 2>&1 | while IFS= read -r line; do echo \"  $line\"; done",
        ])

    backend = (policy.get("graphics") or {}).get("backend")
    if backend == "dxvk":
        lines.extend(_winetricks_backend_lines("dxvk", "DXVK"))
    elif backend in {"vkd3d", "vkd3d-proton"}:
        lines.extend(_winetricks_backend_lines("vkd3d", "vkd3d"))
    elif backend in {"wined3d", "none", "auto"}:
        lines.append(f'echo "[winforge]   Graphics backend {backend}: no prefix install step"')
    return lines


def _winetricks_backend_lines(verb: str, label: str) -> list[str]:
    return [
        f'echo "[winforge]   Installing graphics backend via winetricks: {label}"',
        f'winetricks -q {verb} 2>&1 | while IFS= read -r line; do echo "  $line"; done',
    ]


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _plan_dep(d):
    if d.kind == "winetricks":
        return "install winetricks verbs: " + ", ".join(d.verbs)
    return f"install {d.kind}: {d.name}" if d.name else f"install dependency kind={d.kind}"


def _plan_inst(s):
    if s.kind == "script":
        return f"run script command: {s.command}"
    target = f" into {s.target}" if s.target else ""
    return f"install {s.kind} from {s.source}{target}"


# ---------------------------------------------------------------------------
# Executable build-script generation (runs inside the Wine container)
# ---------------------------------------------------------------------------

def generate_build_script(
    manifest: Manifest,
    *,
    bundle_mount: str = "/opt/winforge",
    workspace_mount: str = "/workspace",
    timeout_per_phase: int = 300,
) -> str:
    """Produce a bash script that runs the build inside a WinForge Wine container.

    The script is designed to execute after *xvfb-entrypoint.sh* has started
    the virtual X display.  It writes its output into *bundle_mount* (which
    corresponds to the host bundle directory via ``--volume``).
    """
    prefix = "$WINEPREFIX"
    indent = "  "

    lines = [
        "#!/bin/bash",
        'set -euo pipefail',
        "",
        f'echo "[winforge] Starting real build for {manifest.name} v{manifest.version}"',
        f'echo "[winforge] Prefix: {prefix}"',
        f'echo "[winforge] Bundle mount: {bundle_mount}"',
        f'echo "[winforge] Workspace mount: {workspace_mount}"',
        "echo ''",
        "",
        *_compatibility_policy_lines(manifest),
        # ------------------------------------------------------------------
        "### Phase 1: init-prefix ##########################################",
        'echo "[winforge] Phase 1/6: Initializing Wine prefix"',
        f"wine wineboot --init 2>&1 | while IFS= read -r line; do echo \"{indent}$line\"; done",
        'echo "[winforge]   Prefix initialized successfully"',
        *_compatibility_post_wineboot_lines(manifest),
        "echo ''",
        "",
        # ------------------------------------------------------------------
        "### Phase 2: install-dependencies #################################",
        'echo "[winforge] Phase 2/6: Installing dependencies"',
    ]

    # --- winetricks verbs ---
    for dep in manifest.dependencies:
        if dep.kind == "winetricks" and dep.verbs:
            verbs_str = " ".join(shlex.quote(v) for v in dep.verbs)
            lines.append(
                f'echo "  winetricks: {verbs_str}"'
            )
            lines.append(
                f'winetricks -q {verbs_str} 2>&1 | while IFS= read -r line; do echo "{indent}$line"; done'
            )
            lines.append(f'echo "[winforge]   winetricks verbs installed"')
        elif dep.kind in ("font", "directx", "package", "runtime-component"):
            # Generic fallback: try winetricks with the dependency name
            name = dep.name or dep.kind
            if name:
                lines.append(
                    f'echo "  winetricks: {name}"'
                )
                lines.append(
                    f'winetricks -q {name} 2>&1 | while IFS= read -r line; do echo "{indent}$line"; done || echo "  [winforge] winetricks verb {name} not found — skipping"'
                )

    if not manifest.dependencies:
        lines.append('echo "  No dependencies declared — skipping"')

    lines.append("echo ''")
    lines.append("")

    # ------------------------------------------------------------------
    # Phase 3: apply-layout-and-registry (filesystem mappings)
    lines.append("### Phase 3: apply-layout-and-registry #######################")
    lines.append('echo "[winforge] Phase 3/6: Applying filesystem layout and registry changes"')

    for fm in manifest.filesystem:
        source = fm.source
        abs_source = container_source_path(source, workspace_mount=workspace_mount)

        # Convert Windows path target to Wine drive_c path
        target = fm.target.replace("C:/", "C:/").replace("\\", "/")
        # Map to drive_c
        if ":" in target:
            # Assume C:/path -> drive_c/path
            drive_letter = target[0].lower()
            rel_path = target[2:].lstrip("/")
            dest = f'{prefix}/drive_c/{rel_path}'
        else:
            dest = f'{prefix}/drive_c/{target.lstrip("/")}'

        if fm.mode == "merge":
            lines.append(f'echo "  Merge {abs_source} -> {dest}"')
            lines.append(f'mkdir -p "{dest}"')
            lines.append(f'cp -a "{abs_source}/." "{dest}/" 2>&1')
        else:
            lines.append(f'echo "  Copy {abs_source} -> {dest}"')
            lines.append(f'mkdir -p "$(dirname "{dest}")"')
            lines.append(f'cp -r "{abs_source}" "{dest}" 2>&1')

    if not manifest.filesystem:
        lines.append('echo "  No filesystem mappings declared"')

    lines.append("echo ''")
    lines.append("")

    # ------------------------------------------------------------------
    # Phase 4: install-apps
    lines.append("### Phase 4: install-apps ###################################")
    lines.append('echo "[winforge] Phase 4/6: Installing applications"')

    for step in manifest.install:
        source = step.source or ""
        abs_source = container_source_path(source, workspace_mount=workspace_mount) if source else ""

        if step.kind in ("exe", "msi"):
            quiet_args = "/quiet" if step.kind == "msi" else "/S"
            env_vars = ""
            if step.args:
                env_vars = f"WINEDLLOVERRIDES=\"${{WINEDLLOVERRIDES}}\" "
            lines.append(
                f'echo "  Running installer: {abs_source}"'
            )
            lines.append(
                f'{env_vars}wine "{abs_source}" {quiet_args} 2>&1 | while IFS= read -r line; do echo "{indent}$line"; done'
            )
        elif step.kind == "portable":
            # Extract to target directory inside prefix
            target_dir = step.target or "C:/"
            if ":" in target_dir:
                drive_letter = target_dir[0].lower()
                rel_path = target_dir[2:].lstrip("/")
                dest = f'{prefix}/drive_c/{rel_path}'
            else:
                dest = f'{prefix}/drive_c/{target_dir.lstrip("/")}'

            lines.append(f'echo "  Extracting portable: {abs_source} -> {dest}"')
            lines.append(f'mkdir -p "{dest}"')
            lines.append(
                f'unzip -o "{abs_source}" -d "{dest}" 2>&1 | while IFS= read -r line; do echo "{indent}$line"; done'
            )
        elif step.kind == "script" and step.command:
            lines.append(f'echo "  Running custom script command: {step.command}"')
            lines.append(f'eval {step.command} 2>&1 | while IFS= read -r line; do echo "{indent}$line"; done')

    if not manifest.install:
        lines.append('echo "  No application install steps declared"')

    lines.append("echo ''")
    lines.append("")

    # ------------------------------------------------------------------
    # Phase 5: validate
    lines.append("### Phase 5: validate #######################################")
    lines.append('echo "[winforge] Phase 5/6: Validating prefix"')
    lines.append(f'echo "  Entrypoint: {manifest.launch.entrypoint}"')

    # Resolve entrypoint path
    ep = manifest.launch.entrypoint
    if ":" in ep:
        drive_letter = ep[0].lower()
        rel_path = ep[2:].lstrip("/").replace("\\", "/")
        ep_path = f"{prefix}/drive_c/{rel_path}"
    else:
        ep_path = f"{prefix}/drive_c/{ep.lstrip('/').replace('\\', '/')}"

    lines.append(f'if [ -f "{ep_path}" ]; then')
    lines.append(f'  echo "  [OK] Entrypoint exists at {ep_path}"')
    lines.append("else")
    lines.append(f'  echo "  [WARN] Entrypoint not found at {ep_path} — build may be incomplete"')
    lines.append("fi")

    lines.append("")
    lines.append('echo "[winforge]   Recording prefix contents"')
    lines.append(
        f'find "{prefix}/drive_c" -maxdepth 4 -type f 2>/dev/null | head -100 > "{bundle_mount}/logs/prefix-filelist.txt"'
    )
    lines.append(
        f'du -sh "{prefix}" > "{bundle_mount}/logs/prefix-size.txt"'
    )

    lines.append("echo ''")
    lines.append("")

    # ------------------------------------------------------------------
    # Phase 6: seal-artifact
    lines.append("### Phase 6: seal-artifact ##################################")
    lines.append('echo "[winforge] Phase 6/6: Sealing bundle artifact"')

    # Write build completion marker
    lines.append("")
    lines.append(
        f'cat > "{bundle_mount}/metadata/build-result.json" << \'ENDMARKER\''
    )
    lines.append("{")
    lines.append('  "build": "complete",')
    lines.append(f'  "manifestName": "{manifest.name}",')
    lines.append(f'  "manifestVersion": "{manifest.version}",')
    lines.append(f'  "runtimeProvider": "{manifest.runtime.provider}",')
    lines.append(f'  "runtimeVersion": "{manifest.runtime.version}",')
    lines.append('  "dependencies": [')
    for i, d in enumerate(manifest.dependencies):
        comma = "," if i < len(manifest.dependencies) - 1 else ""
        if d.kind == "winetricks":
            lines.append(f'    {{"kind": "winetricks", "verbs": {json.dumps(d.verbs)}}}{comma}')
        else:
            lines.append(f'    {{"kind": "{d.kind}", "name": "{d.name or ""}"}}{comma}')
    lines.append('  ],')
    lines.append(f'  "prefixSize": $(du -sb "{prefix}" 2>/dev/null | cut -f1 || echo 0),')
    lines.append(f'  "prefixFileCount": $(find "{prefix}/drive_c" -type f 2>/dev/null | wc -l),')
    lines.append(f'  "buildTool": "winforge",')
    lines.append(f'  "buildTimestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)"')
    lines.append("}")
    lines.append("ENDMARKER")

    lines.append("")
    lines.append('echo ""')
    lines.append('echo "[winforge] === BUILD COMPLETE ==="')
    lines.append(f'echo "[winforge] Bundle at {bundle_mount}"')
    lines.append(f'echo "[winforge] Prefix size: $(du -sh "{prefix}" 2>/dev/null | cut -f1)"')
    lines.append("exit 0")

    return "\n".join(lines)
