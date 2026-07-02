#!/usr/bin/env python3
"""WinForge CLI — package and run application recipes for Wine/Proton-family runtimes."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

from artifact.bundle import create_bundle
from artifact.checkpoint import CheckpointError, inspect_checkpoint, resume_checkpoint
from artifact.oci import (
    OCIExportError,
    build_oci_image,
    create_oci_export_plan,
    export_oci_image,
    verify_oci_image_metadata,
)
from artifact.inspection import inspect_bundle, verify_bundle
from artifact.index import (
    ArtifactIndexError,
    default_index_path,
    list_artifacts,
    register_bundle,
    resolve_artifact,
    resolve_bundle_reference,
)
from artifact.kube import (
    KubeExportError,
    create_kube_export_plan,
    export_kube_manifest,
)
from builder.executor import execute_inside_container
from builder.pipeline import build_plan
from container.manager import (
    build_container,
    list_definitions,
    get_image_ref,
)
from compat.corpus import load_default_corpus
from compat.evidence import run_compat_test
from compat.failure_analysis import FailureAnalysisError, analyze_failure_path
from core.manifest import ManifestError, RuntimeSpec, load_manifest
from core.sources import audit_manifest_sources, verify_manifest_sources
from core.media import MediaStageError, stage_media
from runtime.providers import list_providers, resolve_runtime
from runtime.launcher import RunError, build_run_plan, execute_run_plan
from runtime.runner_cache import RunnerCacheError, diagnose_runner, ensure_runner
from runtime.runner_catalog import RunnerCatalogError, RunnerSpec, resolve_runner_spec, runner_catalog_payload


# ---- manifest commands ----


def cmd_inspect(args):
    manifest = load_manifest(Path(args.manifest))
    payload = manifest.to_dict()
    payload["resolvedRuntime"] = resolve_runtime(manifest.runtime).to_dict()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_plan(args):
    manifest = load_manifest(Path(args.manifest))
    binding = resolve_runtime(manifest.runtime)
    result = {
        "manifest": manifest.name,
        "version": manifest.version,
        "runtimeProvider": binding.provider,
        "runtimeVersion": binding.version,
        "requestedRuntimeVersion": binding.requested_version,
        "resolvedRuntimeVersion": binding.resolved_version,
        "runner": binding.runner,
        "runnerVersion": binding.runner_version,
        "runnerSource": binding.runner_source,
        "runnerUrl": binding.runner_url,
        "runnerSha256": binding.runner_sha256,
        "runnerArch": binding.runner_arch,
        "packageVersion": binding.package_version,
        "launcher": binding.launcher,
        "launcherVersion": binding.launcher_version,
        "ociImage": binding.oci_image,
        "localOciImage": binding.local_oci_image,
        "runtimeUsable": binding.runtime_usable,
        "phases": build_plan(manifest),
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_sources_verify(args):
    manifest = load_manifest(Path(args.manifest))
    workspace = Path(args.workspace) if args.workspace else Path.cwd()
    result = verify_manifest_sources(manifest, workspace=workspace)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid") else 8


def cmd_sources_audit(args):
    manifest = load_manifest(Path(args.manifest))
    workspace = Path(args.workspace) if args.workspace else Path.cwd()
    result = audit_manifest_sources(manifest, workspace=workspace)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid") else 8


def cmd_media_stage(args):
    result = stage_media(
        Path(args.source),
        name=args.name,
        workspace=Path(args.workspace) if args.workspace else Path.cwd(),
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0




def cmd_debug_checkpoint_inspect(args):
    result = inspect_checkpoint(Path(args.path))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid") else 13


def cmd_debug_checkpoint_resume(args):
    result = resume_checkpoint(
        Path(args.path),
        output_dir=Path(args.output),
        name=args.name,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_failure_analyze(args):
    result = analyze_failure_path(Path(args.path), write=not args.no_write)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

def cmd_compat_test(args):
    if args.stop_before and args.mode == "run":
        print("winforge: compat error: --stop-before is only supported with --mode dry-run or --mode build", file=sys.stderr)
        return 2
    result = run_compat_test(
        Path(args.manifest),
        output_dir=Path(args.output),
        workspace=Path(args.workspace) if args.workspace else Path.cwd(),
        graphics=args.graphics,
        engine=args.engine,
        mode=args.mode,
        build_timeout=args.build_timeout,
        run_timeout=args.run_timeout,
        entrypoints=args.entrypoint,
        all_entrypoints=args.all_entrypoints,
        run_files=args.file,
        runner_cache_dir=Path(args.runner_cache_dir) if args.runner_cache_dir else None,
        resume_from_bundle=Path(args.resume_from_bundle) if args.resume_from_bundle else None,
        stop_before=args.stop_before,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("success") else 9



def cmd_compat_corpus(args):
    print(json.dumps(load_default_corpus(), indent=2, sort_keys=True))
    return 0


def cmd_build(args):
    manifest = load_manifest(Path(args.manifest))
    bundle_path = create_bundle(
        manifest, Path(args.output), dry_run=args.dry_run,
    )

    binding = resolve_runtime(manifest.runtime)
    base_image = binding.oci_image or get_image_ref(
        manifest.runtime.provider, manifest.runtime.version)

    if args.dry_run:
        artifact_entry = register_bundle(
            bundle_path,
            index_path=default_index_path(Path(args.output)),
        )
        oci = build_oci_image(bundle_path, base_image,
                              output_tag=args.image_tag)
        result = {
            "bundle": str(bundle_path),
            "graph": str(bundle_path / "metadata" / "graph.json"),
            "artifactIndex": artifact_entry["indexPath"],
            "artifact": artifact_entry,
            "dryRun": True,
            "baseImage": base_image,
            "ociImage": oci["outputTag"],
            "ociMapping": oci,
            "status": "dry-run — no Wine commands executed",
        }
        print(json.dumps(result, indent=2))
        return 0

    # ---- Real execution inside container ----
    print(f"[winforge] Starting real build in container ({base_image})...",
          file=sys.stderr)
    sys.stderr.flush()

    build_result = execute_inside_container(
        manifest,
        bundle_path,
        engine=args.engine,
        image_ref=base_image,
        timeout=args.build_timeout,
        runner_cache_dir=Path(args.runner_cache_dir) if args.runner_cache_dir else None,
    )

    # Write build result to bundle metadata
    (bundle_path / "metadata" / "execution-result.json").write_text(
        json.dumps(build_result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Write OCI mapping
    oci = build_oci_image(bundle_path, base_image,
                          output_tag=args.image_tag)
    artifact_entry = register_bundle(
        bundle_path,
        index_path=default_index_path(Path(args.output)),
    ) if build_result.success else None

    result = {
        "bundle": str(bundle_path),
        "graph": str(bundle_path / "metadata" / "graph.json"),
        "artifactIndex": artifact_entry["indexPath"] if artifact_entry else str(default_index_path(Path(args.output))),
        "artifact": artifact_entry,
        "dryRun": False,
        "baseImage": base_image,
        "ociImage": oci["outputTag"],
        "ociMapping": oci,
        "execution": build_result.to_dict(),
    }
    if not build_result.success:
        result["failureAnalysis"] = analyze_failure_path(bundle_path, write=True)

    print(json.dumps(result, indent=2))

    if build_result.success:
        print(f"\n[winforge] Build SUCCESS — bundle at {bundle_path}",
              file=sys.stderr)
        if build_result.prefix_size is not None:
            size_mb = build_result.prefix_size / (1024 * 1024)
            print(f"[winforge] Prefix: {size_mb:.1f} MB, "
                  f"{build_result.prefix_file_count} files",
                  file=sys.stderr)
    else:
        print(f"\n[winforge] Build FAILED — see {bundle_path}/logs/build.log",
              file=sys.stderr)
        return 1

    return 0


# ---- run command ----

def cmd_run(args):
    bundle = resolve_bundle_reference(args.bundle, index_path=args.artifact_index)
    plan = build_run_plan(
        bundle,
        graphics=args.graphics,
        engine=args.engine,
        vnc_port=args.vnc_port,
        novnc_port=args.novnc_port,
        container_name=args.name,
        entrypoint=args.entrypoint,
        files=args.files,
        runner_cache_dir=Path(args.runner_cache_dir) if args.runner_cache_dir else None,
        require_runner=not args.dry_run,
    )
    if args.dry_run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    result = execute_run_plan(plan, timeout=args.timeout)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("success") else int(result.get("exitCode") or 1)


# ---- image commands ----

def cmd_image_verify(args):
    result = verify_oci_image_metadata(
        args.image,
        engine=args.engine,
        timeout=args.timeout,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get('valid') else 1


# ---- container commands ----


def cmd_container_list(args):
    print(json.dumps(list_definitions(), indent=2))
    return 0


def cmd_container_build(args):
    result = build_container(
        args.provider, args.version,
        registry=args.registry, push=args.push,
        build_cmd=args.engine,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.success else 1


def cmd_container_ref(args):
    ref = get_image_ref(args.provider, args.version)
    print(ref)
    return 0

# ---- downloadable runner commands ----

def cmd_runners_list(args):
    print(json.dumps(runner_catalog_payload(), indent=2, sort_keys=True))
    return 0


def _runner_spec_from_args(args) -> RunnerSpec:
    if args.url:
        base = None
        if not args.provider and not args.version:
            try:
                base = resolve_runner_spec(args.runner)
            except RunnerCatalogError:
                base = None
        provider = args.provider or (base.provider if base else "wine")
        version = args.version or (base.version if base else args.runner.removeprefix("pol-"))
        arch = args.arch or (base.arch if base else "x86")
        source = args.source or (base.source if base else "manual")
        strip_components = args.strip_components if args.strip_components is not None else (base.strip_components if base else 1)
        return RunnerSpec(
            id=args.runner,
            provider=provider,
            version=version,
            arch=arch,
            source=source,
            url=args.url,
            sha256=args.sha256,
            strip_components=strip_components,
        )
    return resolve_runner_spec(args.runner)


def cmd_runners_ensure(args):
    spec = _runner_spec_from_args(args)
    result = ensure_runner(spec, cache_dir=Path(args.cache_dir) if args.cache_dir else None)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_runners_diagnose(args):
    candidate = Path(args.runner_or_path).expanduser()
    path = candidate if candidate.exists() else Path(args.cache_dir or "~/.cache/winforge/runners").expanduser() / args.runner_or_path
    result = diagnose_runner(path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0

# ---- bundle commands ----


def cmd_bundle_inspect(args):
    summary = inspect_bundle(Path(args.bundle))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def cmd_bundle_verify(args):
    result = verify_bundle(Path(args.bundle))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1


def cmd_export_oci(args):
    bundle = resolve_bundle_reference(args.bundle, index_path=args.artifact_index)
    if args.dry_run:
        plan = create_oci_export_plan(bundle, tag=args.tag)
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    result = export_oci_image(
        bundle,
        tag=args.tag,
        engine=args.engine,
        context_dir=Path(args.context_dir) if args.context_dir else None,
        timeout=args.timeout,
        push=args.push,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("success") else 1


def cmd_export_kube(args):
    bundle = resolve_bundle_reference(args.bundle, index_path=args.artifact_index)
    if args.dry_run:
        plan = create_kube_export_plan(
            bundle,
            image=args.image,
            namespace=args.namespace,
            name=args.name,
            state_size=args.state_size,
            exports_size=args.exports_size,
            no_pvc=args.no_pvc,
            replicas=args.replicas,
            graphics=args.graphics,
            allow_mutable_tag=args.allow_mutable_tag,
        )
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    if not args.output:
        raise KubeExportError('export kube requires --output unless --dry-run is used')
    result = export_kube_manifest(
        bundle,
        image=args.image,
        output_path=Path(args.output),
        namespace=args.namespace,
        name=args.name,
        state_size=args.state_size,
        exports_size=args.exports_size,
        no_pvc=args.no_pvc,
        replicas=args.replicas,
        graphics=args.graphics,
        allow_mutable_tag=args.allow_mutable_tag,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


# ---- artifact index commands ----

def cmd_artifacts_list(args):
    payload = list_artifacts(args.index)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_artifacts_resolve(args):
    payload = resolve_artifact(args.reference, index_path=args.index)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


# ---- provider commands ----


def cmd_providers(args):
    providers = list_providers()
    for name in providers:
        rs = RuntimeSpec(
            provider=name,
            version=args.version or "latest",
        )
        try:
            binding = resolve_runtime(rs)
            print(json.dumps({
                "provider": binding.provider,
                "version": binding.version,
                "requestedVersion": binding.requested_version,
                "resolvedVersion": binding.resolved_version,
                "runner": binding.runner,
                "runnerVersion": binding.runner_version,
                "runnerSource": binding.runner_source,
                "runnerUrl": binding.runner_url,
                "runnerSha256": binding.runner_sha256,
                "runnerArch": binding.runner_arch,
                "packageVersion": binding.package_version,
                "ociImage": binding.oci_image,
                "launcher": binding.launcher,
                "launcherVersion": binding.launcher_version,
                "notes": binding.notes,
            }, indent=2))
        except Exception as exc:
            print(json.dumps({
                "provider": name, "error": str(exc),
            }, indent=2))
    return 0


# ---- parser ----


def build_parser():
    parser = argparse.ArgumentParser(
        prog="winforge",
        description="Package and run application recipes for Wine/Proton-family runtimes.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # inspect
    p = sub.add_parser("inspect", help="Validate and print normalized manifest")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_inspect)

    # plan
    p = sub.add_parser("plan", help="Print deterministic builder phases "
                                    "with OCI image reference")
    p.add_argument("manifest")
    p.set_defaults(func=cmd_plan)

    # build
    p = sub.add_parser("build", help="Create an immutable execution bundle "
                                     "(dry-run or real via container)")
    p.add_argument("manifest")
    p.add_argument("--output", default="dist",
                   help="Output directory")
    p.add_argument("--dry-run", action="store_true",
                   help="Record contract without executing Wine commands")
    p.add_argument("--engine", default=None,
                   help="Container engine (docker, podman). "
                        "Auto-detect if omitted.")
    p.add_argument("--build-timeout", type=int, default=600,
                   help="Max seconds for container build (default: 600)")
    p.add_argument("--image-tag",
                   help="Optional OCI output tag (e.g. myapp:latest)")
    p.add_argument("--runner-cache-dir",
                   help="Runner cache directory for runtime.runner archives")
    p.set_defaults(func=cmd_build)

    # run
    p = sub.add_parser("run", help="Run a verified WinForge execution bundle")
    p.add_argument("bundle", help="Path to WinForge bundle directory or app name from artifact index")
    p.add_argument("--artifact-index", default=None,
                   help="Artifact index path for resolving app names (default: dist/.winforge/artifacts.json)")
    p.add_argument("--graphics", choices=["headless", "vnc"],
                   help="Graphics mode; defaults to metadata/graph.json defaultMode")
    p.add_argument("--engine", default=None,
                   help="Container engine (podman, docker). Auto-detect if omitted.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the run plan without starting the container")
    p.add_argument("--entrypoint", help="Named suite entrypoint id to run")
    p.add_argument("files", nargs="*", help="Host files to pass to the selected application entrypoint")
    p.add_argument("--vnc-port", type=int, default=5900,
                   help="Host loopback VNC port for --graphics vnc")
    p.add_argument("--novnc-port", type=int, default=6080,
                   help="Host loopback noVNC/websockify port for --graphics vnc")
    p.add_argument("--name", help="Optional container name")
    p.add_argument("--timeout", type=int, default=None,
                   help="Optional max seconds for the run process")
    p.add_argument("--runner-cache-dir",
                   help="Runner cache directory for runtime.runner archives")
    p.set_defaults(func=cmd_run)

    # image
    p = sub.add_parser("image", help="Inspect and verify WinForge application OCI images")
    isub = p.add_subparsers(dest="image_command", required=True)

    ip = isub.add_parser("verify", help="Verify OCI labels match embedded WinForge metadata")
    ip.add_argument("image", help="OCI image reference to verify")
    ip.add_argument("--engine", default=None,
                    help="Container engine (podman, docker). Auto-detect if omitted.")
    ip.add_argument("--timeout", type=int, default=60,
                    help="Max seconds for image inspect/read commands")
    ip.set_defaults(func=cmd_image_verify)

    # container
    p = sub.add_parser("container", help="Manage WinForge runtime OCI containers")
    csub = p.add_subparsers(dest="container_command", required=True)

    cp = csub.add_parser("list", help="List available container build definitions")
    cp.set_defaults(func=cmd_container_list)

    cp = csub.add_parser("build", help="Build a runtime container image")
    cp.add_argument("provider",
                    choices=["wine", "staging", "umu-proton-ge"],
                    help="Runtime provider name")
    cp.add_argument("version",
                    help="Version tag (e.g. 9.0, GE-Proton9-27)")
    cp.add_argument("--engine", default="docker",
                    help="Container build engine (docker/podman)")
    cp.add_argument("--registry",
                    help="Registry to tag and push to")
    cp.add_argument("--push", action="store_true",
                    help="Push after building")
    cp.set_defaults(func=cmd_container_build)

    cp = csub.add_parser("ref", help="Print OCI image reference")
    cp.add_argument("provider", help="Provider name")
    cp.add_argument("version", help="Version tag")
    cp.set_defaults(func=cmd_container_ref)

    # downloadable runners
    p = sub.add_parser("runners", help="Manage downloadable Wine runner archives")
    rsub = p.add_subparsers(dest="runners_command", required=True)

    rp = rsub.add_parser("list", help="List built-in downloadable runner aliases")
    rp.set_defaults(func=cmd_runners_list)

    rp = rsub.add_parser("ensure", help="Download/extract a runner into the local cache")
    rp.add_argument("runner", help="Runner alias such as pol-8.2, or custom id when --url is supplied")
    rp.add_argument("--cache-dir", help="Runner cache directory (default: ~/.cache/winforge/runners)")
    rp.add_argument("--url", help="Override/download URL for a custom runner archive")
    rp.add_argument("--sha256", help="Expected archive SHA-256 when --url is supplied")
    rp.add_argument("--provider", help="Provider for a custom --url runner (default: wine)")
    rp.add_argument("--version", help="Runner version for a custom --url runner")
    rp.add_argument("--arch", default=None, help="Runner architecture for a custom --url runner")
    rp.add_argument("--source", help="Runner source name for a custom --url runner")
    rp.add_argument("--strip-components", type=int, default=None, help="Tar path components to strip while extracting")
    rp.set_defaults(func=cmd_runners_ensure)

    rp = rsub.add_parser("diagnose", help="Diagnose a cached runner alias or runner directory")
    rp.add_argument("runner_or_path", help="Runner alias such as pol-8.2, or a runner directory/bin/wine path")
    rp.add_argument("--cache-dir", help="Runner cache directory (default: ~/.cache/winforge/runners)")
    rp.set_defaults(func=cmd_runners_diagnose)

    # bundle
    p = sub.add_parser("bundle", help="Inspect and verify WinForge execution bundles")
    bsub = p.add_subparsers(dest="bundle_command", required=True)

    bp = bsub.add_parser("inspect", help="Print bundle summary from metadata/graph.json")
    bp.add_argument("bundle", help="Path to WinForge bundle directory")
    bp.set_defaults(func=cmd_bundle_inspect)

    bp = bsub.add_parser("verify", help="Validate bundle contract and graph consistency")
    bp.add_argument("bundle", help="Path to WinForge bundle directory")
    bp.set_defaults(func=cmd_bundle_verify)

    # artifacts
    p = sub.add_parser("artifacts", help="List and resolve locally indexed WinForge artifacts")
    asub = p.add_subparsers(dest="artifacts_command", required=True)

    ap = asub.add_parser("list", help="Print the local artifact index")
    ap.add_argument("--index", default=None,
                    help="Artifact index path (default: dist/.winforge/artifacts.json)")
    ap.set_defaults(func=cmd_artifacts_list)

    ap = asub.add_parser("resolve", help="Resolve app or app@version to a bundle")
    ap.add_argument("reference", help="Artifact reference, e.g. notepad-plus-plus or notepad-plus-plus@8.6.0")
    ap.add_argument("--index", default=None,
                    help="Artifact index path (default: dist/.winforge/artifacts.json)")
    ap.set_defaults(func=cmd_artifacts_resolve)

    # sources
    p = sub.add_parser("sources", help="Verify recipe source files and hashes")
    ssub = p.add_subparsers(dest="sources_command", required=True)

    sp = ssub.add_parser("verify", help="Verify local recipe sources and sha256 values")
    sp.add_argument("manifest", help="Path to WinForge manifest")
    sp.add_argument("--workspace", help="Workspace root for relative local sources (default: cwd)")
    sp.set_defaults(func=cmd_sources_verify)

    sp = ssub.add_parser("audit", help="Audit local recipe source paths for blocked policy artifacts")
    sp.add_argument("manifest", help="Path to WinForge manifest")
    sp.add_argument("--workspace", help="Workspace root for relative local sources (default: cwd)")
    sp.set_defaults(func=cmd_sources_audit)

    # media
    p = sub.add_parser("media", help="Stage BYO media into the WinForge workspace")
    msub = p.add_subparsers(dest="media_command", required=True)

    mp = msub.add_parser("stage", help="Stage local BYO media under sources/<name>/media")
    mp.add_argument("source", help="Local directory, archive, ISO, or file to stage")
    mp.add_argument("--name", required=True, help="Safe source id/name for workspace staging")
    mp.add_argument("--workspace", help="Workspace root for staged media (default: cwd)")
    mp.add_argument("--overwrite", action="store_true", help="Replace an existing staged media directory")
    mp.set_defaults(func=cmd_media_stage)


    # debug helpers
    p = sub.add_parser("debug", help="Debug WinForge bundles and installer workflows")
    dsub = p.add_subparsers(dest="debug_command", required=True)

    dp = dsub.add_parser("checkpoint", help="Inspect or resume prepared-prefix checkpoints")
    cpsub = dp.add_subparsers(dest="checkpoint_command", required=True)

    cp = cpsub.add_parser("inspect", help="Locate and validate a checkpoint bundle or output parent")
    cp.add_argument("path", help="Checkpoint bundle path or compat-test output parent")
    cp.set_defaults(func=cmd_debug_checkpoint_inspect)

    cp = cpsub.add_parser("resume", help="Copy a checkpoint bundle into a fresh mutable attempt directory")
    cp.add_argument("path", help="Checkpoint bundle path or compat-test output parent")
    cp.add_argument("--output", required=True, help="Directory where the fresh attempt bundle will be copied")
    cp.add_argument("--name", help="Attempt bundle directory name")
    cp.add_argument("--overwrite", action="store_true", help="Replace an existing attempt bundle")
    cp.set_defaults(func=cmd_debug_checkpoint_resume)

    # failure analysis
    p = sub.add_parser("failure", help="Analyze Windows/Wine installer failure logs")
    fsub = p.add_subparsers(dest="failure_command", required=True)

    fp = fsub.add_parser("analyze", help="Analyze a WinForge bundle, log directory, or log file")
    fp.add_argument("path", help="Bundle directory, log directory, or log file to analyze")
    fp.add_argument("--no-write", action="store_true", help="Do not write metadata/failure-analysis.json or failure-summary.md")
    fp.set_defaults(func=cmd_failure_analyze)

    # compatibility evidence
    p = sub.add_parser("compat", help="Collect compatibility evidence for a recipe")
    csub = p.add_subparsers(dest="compat_command", required=True)

    cp = csub.add_parser("test", help="Run source/build/verify/run-plan compatibility evidence pass")
    cp.add_argument("manifest", help="Path to WinForge manifest")
    cp.add_argument("--workspace", help="Workspace root for relative local sources (default: cwd)")
    cp.add_argument("--output", default="dist", help="Output directory for evidence bundles")
    cp.add_argument("--graphics", choices=["headless", "vnc"], default="headless", help="Graphics mode for run-plan/run evidence")
    cp.add_argument("--engine", default=None, help="Container engine name to record/use in evidence")
    cp.add_argument("--mode", choices=["dry-run", "build", "run"], default="dry-run", help="Evidence mode: dry-run, real build, or real build+run")
    cp.add_argument("--build-timeout", type=int, default=600, help="Max seconds for real build mode")
    cp.add_argument("--run-timeout", type=int, default=None, help="Max seconds for real run mode")
    cp.add_argument("--entrypoint", action="append", default=[], help="Suite entrypoint id to include in run-plan/run evidence; repeatable")
    cp.add_argument("--all-entrypoints", action="store_true", help="Collect run-plan/run evidence for every manifest entrypoint")
    cp.add_argument("--file", action="append", default=[], help="Host file to pass to selected entrypoint(s); repeatable")
    cp.add_argument("--runner-cache-dir", help="Runner cache directory for runtime.runner archives")
    cp.add_argument("--resume-from-bundle", help="Prepared checkpoint bundle or output parent to seed into the new attempt")
    cp.add_argument("--stop-before", choices=["install-apps"], help="Stop real build before the selected phase and seal a checkpoint")
    cp.set_defaults(func=cmd_compat_test)

    cp = csub.add_parser("corpus", help="Print the default curated compatibility corpus")
    cp.set_defaults(func=cmd_compat_corpus)

    # export
    p = sub.add_parser("export", help="Export WinForge bundles to deployable artifacts")
    esub = p.add_subparsers(dest="export_command", required=True)

    ep = esub.add_parser("oci", help="Export a verified bundle as a runnable OCI application image")
    ep.add_argument("bundle", help="Path to WinForge bundle directory or app name from artifact index")
    ep.add_argument("--artifact-index", default=None,
                    help="Artifact index path for resolving app names (default: dist/.winforge/artifacts.json)")
    ep.add_argument("--tag", required=True, help="Output OCI image tag")
    ep.add_argument("--dry-run", action="store_true", help="Print the OCI export plan without building")
    ep.add_argument("--engine", default=None, help="Container build engine (podman, docker). Auto-detect if omitted.")
    ep.add_argument("--context-dir", help="Optional build context directory to materialize")
    ep.add_argument("--timeout", type=int, default=600, help="Max seconds for image build/push commands")
    ep.add_argument("--push", action="store_true", help="Push the image after a successful local build")
    ep.set_defaults(func=cmd_export_oci)

    ep = esub.add_parser("kube", help="Export Kubernetes manifests for a WinForge app image")
    ep.add_argument("bundle", help="Path to WinForge bundle directory or app name from artifact index")
    ep.add_argument("--artifact-index", default=None,
                    help="Artifact index path for resolving app names (default: dist/.winforge/artifacts.json)")
    ep.add_argument("--image", required=True, help="Digest-pinned OCI image ref, e.g. ghcr.io/org/app@sha256:...")
    ep.add_argument("--namespace", default="default", help="Kubernetes namespace for generated resources")
    ep.add_argument("--name", help="Kubernetes resource base name; defaults to sanitized app name")
    ep.add_argument("--state-size", default="10Gi", help="State PVC size when PVCs are enabled")
    ep.add_argument("--exports-size", default="10Gi", help="Exports PVC size when PVCs are enabled")
    ep.add_argument("--replicas", type=int, default=1, help="Deployment replica count")
    ep.add_argument("--graphics", choices=["headless", "vnc"], default="headless", help="WINFORGE_GRAPHICS value")
    ep.add_argument("--no-pvc", action="store_true", help="Use emptyDir volumes instead of PVCs")
    ep.add_argument("--allow-mutable-tag", action="store_true", help="Allow tag-only image refs instead of requiring @sha256")
    ep.add_argument("--output", help="Write Kubernetes YAML to this file; required unless --dry-run")
    ep.add_argument("--dry-run", action="store_true", help="Print the Kubernetes export plan without writing YAML")
    ep.set_defaults(func=cmd_export_kube)

    # providers
    p = sub.add_parser("providers", help="List available runtime providers")
    p.add_argument("--version", default="latest",
                   help="Version to resolve against")
    p.set_defaults(func=cmd_providers)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ManifestError as exc:
        print(f"winforge: manifest error: {exc}", file=sys.stderr)
        return 2
    except FileExistsError as exc:
        print(f"winforge: artifact exists: {exc}", file=sys.stderr)
        return 3
    except RunError as exc:
        print(f"winforge: run error: {exc}", file=sys.stderr)
        return 4
    except OCIExportError as exc:
        print(f"winforge: export error: {exc}", file=sys.stderr)
        return 5
    except KubeExportError as exc:
        print(f"winforge: kube export error: {exc}", file=sys.stderr)
        return 7
    except (RunnerCatalogError, RunnerCacheError) as exc:
        print(f"winforge: runner error: {exc}", file=sys.stderr)
        return 10
    except MediaStageError as exc:
        print(f"winforge: media error: {exc}", file=sys.stderr)
        return 11
    except FailureAnalysisError as exc:
        print(f"winforge: failure-analysis error: {exc}", file=sys.stderr)
        return 12
    except CheckpointError as exc:
        print(f"winforge: checkpoint error: {exc}", file=sys.stderr)
        return 13
    except ArtifactIndexError as exc:
        print(f"winforge: artifact index error: {exc}", file=sys.stderr)
        return 6
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
