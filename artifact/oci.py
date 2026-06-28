'''OCI application image export for WinForge bundles.

Phase 5B/5C turns a verified WinForge execution bundle into a runnable
application OCI image. The image remains application-first: it embeds the
sealed bundle under /opt/winforge/bundle and separates mutable runtime
state and exports under dedicated paths.
'''
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from artifact.inspection import verify_bundle

ARTIFACT_IMAGE_SCHEMA_VERSION = 'winforge.artifact-image/v0'
OCI_EXPORT_PLAN_SCHEMA_VERSION = 'winforge.oci-export-plan/v0'
OCI_EXPORT_RESULT_SCHEMA_VERSION = 'winforge.oci-export-result/v0'

BUNDLE_ROOT = '/opt/winforge/bundle'
STATE_ROOT = '/var/lib/winforge/state'
EXPORTS_ROOT = '/exports'
APP_LAUNCHER = '/usr/local/bin/winforge-app-launch'


class OCIExportError(RuntimeError):
    '''Raised when a bundle cannot be exported as an OCI application image.'''


def create_oci_export_plan(bundle_path: Path | str, *, tag: str) -> dict[str, Any]:
    '''Return a dry-run plan for exporting *bundle_path* as a runnable OCI image.'''
    bundle = Path(bundle_path)
    verification = verify_bundle(bundle)
    if not verification.get('valid'):
        raise OCIExportError('invalid WinForge bundle: ' + _verification_error_text(verification))

    manifest = _load_json(bundle, 'manifest.winforge.json')
    graph = _load_json(bundle, 'metadata/graph.json')
    runtime = dict(graph.get('runnerRuntime') or {})
    launch = dict(graph.get('launch') or {})
    application = dict(graph.get('application') or {
        'name': manifest.get('name'),
        'version': manifest.get('version'),
    })
    base_image = _runtime_image(runtime)
    artifact_metadata = _artifact_metadata(
        bundle=bundle,
        tag=tag,
        application=application,
        manifest=manifest,
        graph=graph,
        runtime=runtime,
        launch=launch,
        base_image=base_image,
    )
    labels = _oci_labels(application, runtime, base_image)
    containerfile = _containerfile(base_image, labels)

    return {
        'schemaVersion': OCI_EXPORT_PLAN_SCHEMA_VERSION,
        'imageType': 'runnable-application-image',
        'bundle': str(bundle),
        'tag': tag,
        'baseImage': base_image,
        'application': application,
        'runtime': _runtime_summary(runtime),
        'layout': {
            'bundle': BUNDLE_ROOT,
            'state': STATE_ROOT,
            'exports': EXPORTS_ROOT,
            'entrypoint': APP_LAUNCHER,
            'artifactMetadata': f'{BUNDLE_ROOT}/metadata/artifact.json',
        },
        'labels': labels,
        'artifactMetadata': artifact_metadata,
        'containerfile': {
            'path': 'Containerfile',
            'content': containerfile,
        },
        'launcher': {
            'path': 'winforge-app-launch',
            'containerPath': APP_LAUNCHER,
        },
        'verification': {
            'schemaVersion': verification.get('schemaVersion'),
            'valid': True,
            'warnings': verification.get('warnings', []),
        },
    }


def prepare_oci_build_context(
    bundle_path: Path | str,
    plan: dict[str, Any],
    context_dir: Path | str,
) -> Path:
    '''Stage a container build context for *plan* and return its path.'''
    bundle = Path(bundle_path)
    context = Path(context_dir)
    if context.exists():
        if any(context.iterdir()):
            raise OCIExportError(
                f'OCI build context already exists and is not empty: {context}'
            )
    else:
        context.mkdir(parents=True)

    staged_bundle = context / 'bundle'
    shutil.copytree(bundle, staged_bundle, symlinks=True)
    _write_json(staged_bundle / 'metadata/artifact.json', plan['artifactMetadata'])

    containerfile = context / plan['containerfile']['path']
    containerfile.write_text(plan['containerfile']['content'], encoding='utf-8')

    launcher = context / plan['launcher']['path']
    launcher.write_text(_launcher_script(), encoding='utf-8')
    launcher.chmod(0o755)
    return context


def export_oci_image(
    bundle_path: Path | str,
    *,
    tag: str,
    engine: str | None = None,
    context_dir: Path | str | None = None,
    timeout: int = 600,
    push: bool = False,
) -> dict[str, Any]:
    '''Build a runnable application OCI image from a verified WinForge bundle.'''
    bundle = Path(bundle_path)
    plan = create_oci_export_plan(bundle, tag=tag)
    selected_engine = _select_engine(engine)
    if selected_engine is None:
        requested = engine or 'podman/docker'
        return _result(
            success=False,
            plan=plan,
            engine=requested,
            context=None,
            command=[],
            exit_code=None,
            stdout='',
            stderr='',
            error=f'container build engine not found: {requested}',
        )

    context = prepare_oci_build_context(
        bundle,
        plan,
        Path(context_dir) if context_dir is not None else Path(tempfile.mkdtemp(prefix='winforge-oci-')),
    )
    command = [selected_engine, 'build', '-f', 'Containerfile', '-t', tag, str(context)]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return _result(
            success=False,
            plan=plan,
            engine=selected_engine,
            context=context,
            command=command,
            exit_code=None,
            stdout='',
            stderr='',
            error=f'container build engine not found: {selected_engine}: {exc}',
        )
    except subprocess.TimeoutExpired as exc:
        return _result(
            success=False,
            plan=plan,
            engine=selected_engine,
            context=context,
            command=command,
            exit_code=None,
            stdout=exc.stdout or '',
            stderr=exc.stderr or '',
            error=f'OCI image build timed out after {timeout}s',
        )

    result = _result(
        success=proc.returncode == 0,
        plan=plan,
        engine=selected_engine,
        context=context,
        command=command,
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        error=None if proc.returncode == 0 else 'OCI image build failed',
    )
    if push and proc.returncode == 0:
        push_command = [selected_engine, 'push', tag]
        push_proc = subprocess.run(
            push_command,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        result['push'] = {
            'command': push_command,
            'exitCode': push_proc.returncode,
            'stdout': push_proc.stdout,
            'stderr': push_proc.stderr,
            'success': push_proc.returncode == 0,
        }
        result['success'] = result['success'] and push_proc.returncode == 0
        if push_proc.returncode != 0:
            result['error'] = 'OCI image push failed'
    return result


# Backward-compatible mapping helper used by the existing `winforge build` path.
def build_oci_image(
    bundle_path: Path,
    base_image: str,
    *,
    output_tag: str | None = None,
    build_cmd: str = 'docker',
) -> dict[str, Any]:
    dockerfile_content = (
        f'FROM {base_image}\n'
        '\n'
        'LABEL dev.winforge.artifact.kind=execution-bundle\n'
        'LABEL dev.winforge.artifact.version=v0\n'
        '\n'
        f'COPY {bundle_path.name} {BUNDLE_ROOT}\n'
    )
    return {
        'baseImage': base_image,
        'bundlePath': str(bundle_path),
        'outputTag': output_tag,
        'suggestedDockerfile': dockerfile_content,
    }


def _artifact_metadata(
    *,
    bundle: Path,
    tag: str,
    application: dict[str, Any],
    manifest: dict[str, Any],
    graph: dict[str, Any],
    runtime: dict[str, Any],
    launch: dict[str, Any],
    base_image: str,
) -> dict[str, Any]:
    return {
        'schemaVersion': ARTIFACT_IMAGE_SCHEMA_VERSION,
        'imageType': 'runnable-application-image',
        'application': application,
        'runtime': {
            **_runtime_summary(runtime),
            'baseImage': base_image,
            'localImage': runtime.get('localImage'),
        },
        'recipe': {
            'schemaVersion': manifest.get('schemaVersion'),
            'path': 'manifest.winforge.json',
        },
        'launch': launch,
        'graphics': graph.get('graphics', {}),
        'compatibility': graph.get('compatibility', {}),
        'state': {
            **(manifest.get('state') or {'defaultPersistence': 'persistent'}),
            'path': STATE_ROOT,
        },
        'exports': {
            'path': EXPORTS_ROOT,
            'declared': manifest.get('exports', []),
        },
        'layout': {
            'bundle': BUNDLE_ROOT,
            'state': STATE_ROOT,
            'exports': EXPORTS_ROOT,
            'entrypoint': APP_LAUNCHER,
        },
        'sourceBundle': {
            'path': str(bundle),
            'graph': 'metadata/graph.json',
            'provenance': 'metadata/provenance.json',
        },
        'targetImage': {
            'tag': tag,
        },
    }


def _runtime_summary(runtime: dict[str, Any]) -> dict[str, Any]:
    keys = [
        'provider', 'version', 'requestedVersion', 'resolvedVersion',
        'family', 'runner', 'runnerVersion', 'packageVersion',
        'launcher', 'launcherVersion', 'runtimeUsable',
    ]
    return {key: runtime[key] for key in keys if key in runtime and runtime[key] is not None}


def _oci_labels(application: dict[str, Any], runtime: dict[str, Any], base_image: str) -> dict[str, str]:
    return {
        'org.opencontainers.image.title': str(application.get('name', 'winforge-application')),
        'org.opencontainers.image.version': str(application.get('version', '')),
        'org.opencontainers.image.description': 'WinForge runnable application image',
        'io.winforge.schema': ARTIFACT_IMAGE_SCHEMA_VERSION,
        'io.winforge.app.name': str(application.get('name', '')),
        'io.winforge.app.version': str(application.get('version', '')),
        'io.winforge.runtime.provider': str(runtime.get('provider', '')),
        'io.winforge.runtime.requestedVersion': str(runtime.get('requestedVersion', runtime.get('version', ''))),
        'io.winforge.runtime.resolvedVersion': str(runtime.get('resolvedVersion', runtime.get('version', ''))),
        'io.winforge.runtime.baseImage': base_image,
        'io.winforge.runner': str(runtime.get('runner', '')),
        'io.winforge.launcher': str(runtime.get('launcher', '')),
    }


def _containerfile(base_image: str, labels: dict[str, str]) -> str:
    label_lines = '\n'.join(
        f'LABEL {key}={_docker_quote(value)}' for key, value in labels.items()
    )
    return (
        f'FROM {base_image}\n'
        '\n'
        f'{label_lines}\n'
        '\n'
        f'ENV WINFORGE_BUNDLE={BUNDLE_ROOT} \\\n'
        f'    WINFORGE_STATE={STATE_ROOT} \\\n'
        f'    WINFORGE_EXPORTS={EXPORTS_ROOT} \\\n'
        f'    WINEPREFIX={STATE_ROOT}/prefix \\\n'
        '    WINFORGE_GRAPHICS=headless\n'
        '\n'
        f'COPY bundle {BUNDLE_ROOT}\n'
        f'COPY winforge-app-launch {APP_LAUNCHER}\n'
        f'RUN chmod +x {APP_LAUNCHER} && mkdir -p {STATE_ROOT} {EXPORTS_ROOT}\n'
        f'VOLUME ["{STATE_ROOT}", "{EXPORTS_ROOT}"]\n'
        f'ENTRYPOINT ["{APP_LAUNCHER}"]\n'
    )


def _launcher_script() -> str:
    return f'''#!/usr/bin/env bash
set -euo pipefail

export WINFORGE_BUNDLE="${{WINFORGE_BUNDLE:-{BUNDLE_ROOT}}}"
export WINFORGE_STATE="${{WINFORGE_STATE:-{STATE_ROOT}}}"
export WINFORGE_EXPORTS="${{WINFORGE_EXPORTS:-{EXPORTS_ROOT}}}"
export WINEPREFIX="${{WINEPREFIX:-${{WINFORGE_STATE}}/prefix}}"
export WINFORGE_GRAPH="${{WINFORGE_GRAPH:-${{WINFORGE_BUNDLE}}/metadata/graph.json}}"
mkdir -p "$WINFORGE_STATE" "$WINFORGE_EXPORTS"

if [ ! -d "$WINEPREFIX/drive_c" ]; then
  rm -rf "$WINEPREFIX"
  mkdir -p "$(dirname "$WINEPREFIX")"
  cp -a "$WINFORGE_BUNDLE/prefix" "$WINEPREFIX"
fi

exec python3 - "$@" <<'PY'
import json
import os
import sys

bundle = os.environ.get("WINFORGE_BUNDLE", "{BUNDLE_ROOT}")
graph_path = os.environ.get("WINFORGE_GRAPH", os.path.join(bundle, "metadata", "graph.json"))
with open(graph_path, "r", encoding="utf-8") as handle:
    graph = json.load(handle)
runtime = graph.get("runnerRuntime", {{}})
launch = graph.get("launch", {{}})
launcher = runtime.get("launcher", "wine")
if launcher == "umu":
    executable = "umu-run"
elif launcher == "wine":
    executable = "wine"
else:
    raise SystemExit(f"unsupported WinForge launcher: {{launcher}}")
for key, value in (launch.get("env") or {{}}).items():
    os.environ[str(key)] = str(value)
command = [executable, launch.get("entrypoint", "")] + list(launch.get("args") or []) + sys.argv[1:]
if not command[1]:
    raise SystemExit("WinForge launch entrypoint is missing")
os.execvp(command[0], command)
PY
'''


def _select_engine(engine: str | None) -> str | None:
    candidates = [engine] if engine else ['podman', 'docker']
    for candidate in candidates:
        if candidate and shutil.which(candidate):
            return candidate
    return None


def _result(
    *,
    success: bool,
    plan: dict[str, Any],
    engine: str,
    context: Path | None,
    command: list[str],
    exit_code: int | None,
    stdout: str,
    stderr: str,
    error: str | None,
) -> dict[str, Any]:
    result = {
        'schemaVersion': OCI_EXPORT_RESULT_SCHEMA_VERSION,
        'success': success,
        'tag': plan.get('tag'),
        'engine': engine,
        'context': str(context) if context is not None else None,
        'command': command,
        'exitCode': exit_code,
        'stdout': stdout,
        'stderr': stderr,
        'plan': plan,
    }
    if error:
        result['error'] = error
    return result


def _runtime_image(runtime: dict[str, Any]) -> str:
    image = runtime.get('image') or runtime.get('ociImage')
    if not image:
        raise OCIExportError('bundle graph does not contain a runner runtime image')
    return str(image)


def _verification_error_text(verification: dict[str, Any]) -> str:
    errors = verification.get('errors') or []
    if errors:
        return '; '.join(str(error) for error in errors)
    return 'verification failed'


def _docker_quote(value: str) -> str:
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _load_json(bundle: Path, rel: str) -> dict[str, Any]:
    return json.loads((bundle / rel).read_text(encoding='utf-8'))


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
