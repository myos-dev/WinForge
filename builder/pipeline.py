"""Build phase planning and Wine build script generation for WinForge.

Converts a Manifest into executable build phases that run inside a
WinForge Wine/Proton OCI container.
"""
from __future__ import annotations
import json, posixpath, shlex
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


def _runner_environment_lines(manifest: Manifest) -> list[str]:
    """Return shell lines that select a mounted Wine runner archive when present."""
    if not manifest.runtime.runner:
        return []
    return [
        "### Downloadable Wine runner ##################################",
        'if [ -n "${WINFORGE_RUNNER_BIN:-}" ]; then',
        '  echo "[winforge] Using cached Wine runner: ${WINFORGE_RUNNER_ID:-unknown} at $WINFORGE_RUNNER_BIN"',
        '  export PATH="$WINFORGE_RUNNER_BIN:$PATH"',
        '  export WINE="$WINFORGE_RUNNER_BIN/wine"',
        'fi',
        "echo ''",
        "",
    ]


def _compatibility_policy_lines(manifest: Manifest) -> list[str]:
    """Return shell lines that export compatibility env safe before wineboot."""
    policy = manifest.compatibility or {}
    if not policy:
        return []

    lines = [
        "### Compatibility policy #######################################",
        'echo "[winforge] Compatibility policy"',
    ]
    for key, value in compatibility_environment(policy).items():
        if key == "WINEDLLOVERRIDES":
            # DLL overrides are application/runtime policy, not prefix-creation
            # policy. Applying them before wineboot can perturb Wine's own
            # setupapi/appwiz/mono initialization path.
            continue
        lines.append(f"export {key}={_shell_quote(str(value))}")
        if key.startswith("WINFORGE_GRAPHICS_"):
            lines.append(f'echo "  {key}={value}"')
        elif key in {"WINEDEBUG", "DXVK_LOG_LEVEL"}:
            lines.append(f'echo "  env {key}=<set>"')
        else:
            lines.append(f'echo "  {key}={value}"')

    lines.extend(['echo ""', ""])
    return lines


def _compatibility_dll_override_lines(manifest: Manifest) -> list[str]:
    """Return shell lines that export DLL overrides after prefix initialization."""
    policy = manifest.compatibility or {}
    if not policy:
        return []
    overrides = compatibility_environment(policy).get("WINEDLLOVERRIDES")
    if not overrides:
        return []
    return [
        'echo "[winforge]   Applying DLL override policy after prefix initialization"',
        f"export WINEDLLOVERRIDES={_shell_quote(str(overrides))}",
        'echo "  WINEDLLOVERRIDES=<compiled compatibility policy>"',
    ]


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


def _wine_path_for_container_path(path: str) -> str:
    """Convert a Linux container path to Wine's Z: drive path."""
    normalized = path.replace("\\", "/")
    if normalized.startswith("/"):
        return "Z:" + normalized.replace("/", "\\")
    return normalized.replace("/", "\\")



def _ps_single_quote(value: str) -> str:
    """Quote a literal value for a PowerShell single-quoted string."""
    return "'" + value.replace("'", "''") + "'"


def _choco_command_script(command: str, args: list[str]) -> str:
    ps_args = ", ".join(_ps_single_quote(arg) for arg in [command, *args])
    return "$ErrorActionPreference = 'Stop'; $chocoArgs = @(" + ps_args + "); & choco @chocoArgs"

def _cmd_quote_arg(value: str) -> str:
    """Quote one argument for the Windows cmd.exe command line."""
    escaped = value.replace('"', '\"')
    if not escaped or any(ch.isspace() for ch in escaped) or any(ch in escaped for ch in "&()[]{}^=;!'+,`~"):
        return f'"{escaped}"'
    return escaped


def _plan_dep(d):
    if d.kind == "winetricks":
        return "install winetricks verbs: " + ", ".join(d.verbs)
    return f"install {d.kind}: {d.name}" if d.name else f"install dependency kind={d.kind}"


def _plan_inst(s):
    if s.kind == "script":
        return f"run script command: {s.command}"
    if s.kind in {"bat", "cmd"}:
        wd = f" from {s.working_directory}" if s.working_directory else ""
        return f"run Windows {s.kind} script {s.source}{wd}"
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
    stop_before: str | None = None,
) -> str:
    """Produce a bash script that runs the build inside a WinForge Wine container.

    The script is designed to execute after *xvfb-entrypoint.sh* has started
    the virtual X display.  It writes its output into *bundle_mount* (which
    corresponds to the host bundle directory via ``--volume``).
    """
    if stop_before not in {None, "install-apps"}:
        raise ValueError("stop_before must be one of: install-apps")
    prefix = "$WINEPREFIX"
    indent = "  "

    lines = [
        "#!/bin/bash",
        'set -euo pipefail',
        "",
        r"printf '[winforge] Starting real build for %s v%s\n' " + _shell_quote(manifest.name) + " " + _shell_quote(manifest.version),
        f'echo "[winforge] Prefix: {prefix}"',
        f'echo "[winforge] Bundle mount: {bundle_mount}"',
        f'echo "[winforge] Workspace mount: {workspace_mount}"',
        "echo ''",
        "",
        *_runner_environment_lines(manifest),
        *_compatibility_policy_lines(manifest),
        # ------------------------------------------------------------------
        "### Phase 1: init-prefix ##########################################",
        'echo "[winforge] Phase 1/6: Initializing Wine prefix"',
        f'echo "[winforge]   wineboot timeout: {timeout_per_phase}s"',
        f"timeout {timeout_per_phase}s wine wineboot --init 2>&1 | while IFS= read -r line; do echo \"{indent}$line\"; done",
        'echo "[winforge]   Prefix initialized successfully"',
        *_compatibility_dll_override_lines(manifest),
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

    if stop_before == "install-apps":
        prefix_filelist = (
            f"find \"{prefix}/drive_c\" -maxdepth 4 -type f 2>/dev/null | "
            f"awk 'NR <= 100 {{ print }}' > \"{bundle_mount}/logs/prefix-filelist.txt\""
        )
        result_path = f"{bundle_mount}/metadata/build-result.json"
        manifest_name_json = _shell_quote(json.dumps(manifest.name))
        manifest_version_json = _shell_quote(json.dumps(manifest.version))
        runtime_provider_json = _shell_quote(json.dumps(manifest.runtime.provider))
        runtime_version_json = _shell_quote(json.dumps(manifest.runtime.version))
        printf_lines = [
            "  printf '{\n'",
            "  printf '  " + '"build": "checkpoint",' + "\n'",
            "  printf '  " + '"manifestName": %s,' + "\n' " + manifest_name_json,
            "  printf '  " + '"manifestVersion": %s,' + "\n' " + manifest_version_json,
            "  printf '  " + '"runtimeProvider": %s,' + "\n' " + runtime_provider_json,
            "  printf '  " + '"runtimeVersion": %s,' + "\n' " + runtime_version_json,
            "  printf '  " + '"stoppedBefore": "install-apps",' + "\n'",
            "  printf '  " + '"prefixSize": %s,' + "\\n' " + '"$prefix_size"',
            "  printf '  " + '"prefixFileCount": %s,' + "\\n' " + '"$prefix_file_count"',
            "  printf '  " + '"buildTool": "winforge",' + "\n'",
            "  printf '  " + '"buildTimestamp": %s' + "\\n' " + '"\\"$build_timestamp\\""',
            "  printf '}\n'",
        ]
        lines.extend([
            'echo "[winforge] Stop requested before phase: install-apps"',
            'echo "[winforge]   Prepared prefix checkpoint will be sealed without running application installers"',
            prefix_filelist,
            f'du -sh "{prefix}" > "{bundle_mount}/logs/prefix-size.txt"',
            f'prefix_size=$(du -sb "{prefix}" 2>/dev/null | cut -f1 || echo 0)',
            f'prefix_file_count=$(find "{prefix}/drive_c" -type f 2>/dev/null | wc -l)',
            'build_timestamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)',
            f'build_result_path={_shell_quote(result_path)}',
            '{',
            *printf_lines,
            '} > "$build_result_path"',
            'echo "[winforge] === CHECKPOINT COMPLETE ==="',
            f'echo "[winforge] Bundle at {bundle_mount}"',
            "exit 0",
        ])
        return "\n".join(lines)

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
            default_args = ["/quiet"] if step.kind == "msi" else ["/S"]
            installer_args = step.args or default_args
            args_str = " ".join(shlex.quote(arg) for arg in installer_args)
            lines.append(
                f'echo "  Running installer: {abs_source}"'
            )
            lines.append(
                f'wine "{abs_source}" {args_str} 2>&1 | while IFS= read -r line; do echo "{indent}$line"; done'
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
        elif step.kind in ("bat", "cmd"):
            workdir = (
                container_source_path(step.working_directory, workspace_mount=workspace_mount)
                if step.working_directory
                else posixpath.dirname(abs_source) or workspace_mount
            )
            windows_source = _wine_path_for_container_path(abs_source)
            cmd_line = " ".join([_cmd_quote_arg(windows_source), *(_cmd_quote_arg(arg) for arg in step.args)])
            lines.append(f'echo "  Running Windows script: {abs_source}"')
            lines.append(f'pushd "{workdir}" >/dev/null')
            lines.append(f'wine cmd /c {_shell_quote(cmd_line)} 2>&1 | while IFS= read -r line; do echo "{indent}$line"; done')
            lines.append('popd >/dev/null')
        elif step.kind == "choco" and step.command:
            ps_command = _choco_command_script(step.command, step.args)
            pwsh_path = f"{prefix}/drive_c/Program Files/PowerShell/7/pwsh.exe"
            lines.append(f'echo "  Running Chocolatey command: {step.command} {" ".join(step.args)}"')
            lines.append(f'wine "{pwsh_path}" -NoLogo -NoProfile -ExecutionPolicy Bypass -Command {_shell_quote(ps_command)} 2>&1 | while IFS= read -r line; do echo "{indent}$line"; done')
        elif step.kind == "script" and step.command:
            lines.append('echo "  Running custom script command"')
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
        f"find \"{prefix}/drive_c\" -maxdepth 4 -type f 2>/dev/null | awk 'NR <= 100 {{ print }}' > \"{bundle_mount}/logs/prefix-filelist.txt\""
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
    lines.append(f'  "manifestName": {json.dumps(manifest.name)},')
    lines.append(f'  "manifestVersion": {json.dumps(manifest.version)},')
    lines.append(f'  "runtimeProvider": {json.dumps(manifest.runtime.provider)},')
    lines.append(f'  "runtimeVersion": {json.dumps(manifest.runtime.version)},')
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
