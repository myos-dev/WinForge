#!/usr/bin/env python3
"""WinForge CLI — compile Wine/Proton environment manifests into execution bundles."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from artifact.bundle import create_bundle
from artifact.oci import build_oci_image
from artifact.inspection import inspect_bundle, verify_bundle
from builder.executor import execute_inside_container
from builder.pipeline import build_plan
from container.manager import (
    build_container,
    list_definitions,
    get_image_ref,
)
from core.manifest import ManifestError, RuntimeSpec, load_manifest
from runtime.providers import list_providers, resolve_runtime
from runtime.launcher import RunError, build_run_plan, execute_run_plan


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
        "ociImage": binding.oci_image,
        "localOciImage": binding.local_oci_image,
        "runtimeUsable": binding.runtime_usable,
        "phases": build_plan(manifest),
    }
    print(json.dumps(result, indent=2))
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
        oci = build_oci_image(bundle_path, base_image,
                              output_tag=args.image_tag)
        result = {
            "bundle": str(bundle_path),
            "graph": str(bundle_path / "metadata" / "graph.json"),
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
    )

    # Write build result to bundle metadata
    (bundle_path / "metadata" / "execution-result.json").write_text(
        json.dumps(build_result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Write OCI mapping
    oci = build_oci_image(bundle_path, base_image,
                          output_tag=args.image_tag)

    result = {
        "bundle": str(bundle_path),
        "graph": str(bundle_path / "metadata" / "graph.json"),
        "dryRun": False,
        "baseImage": base_image,
        "ociImage": oci["outputTag"],
        "ociMapping": oci,
        "execution": build_result.to_dict(),
    }

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
    plan = build_run_plan(
        Path(args.bundle),
        graphics=args.graphics,
        engine=args.engine,
        vnc_port=args.vnc_port,
        novnc_port=args.novnc_port,
        container_name=args.name,
    )
    if args.dry_run:
        print(json.dumps(plan, indent=2, sort_keys=True))
        return 0

    result = execute_run_plan(plan, timeout=args.timeout)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("success") else int(result.get("exitCode") or 1)


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

# ---- bundle commands ----


def cmd_bundle_inspect(args):
    summary = inspect_bundle(Path(args.bundle))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def cmd_bundle_verify(args):
    result = verify_bundle(Path(args.bundle))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["valid"] else 1



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
                "ociImage": binding.oci_image,
                "launcher": binding.launcher,
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
        description="Compile Wine/Proton environment manifests into "
                    "immutable execution bundles.",
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
    p.set_defaults(func=cmd_build)

    # run
    p = sub.add_parser("run", help="Run a verified WinForge execution bundle")
    p.add_argument("bundle", help="Path to WinForge bundle directory")
    p.add_argument("--graphics", choices=["headless", "vnc"],
                   help="Graphics mode; defaults to metadata/graph.json defaultMode")
    p.add_argument("--engine", default=None,
                   help="Container engine (podman, docker). Auto-detect if omitted.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the run plan without starting the container")
    p.add_argument("--vnc-port", type=int, default=5900,
                   help="Host loopback VNC port for --graphics vnc")
    p.add_argument("--novnc-port", type=int, default=6080,
                   help="Host loopback noVNC/websockify port for --graphics vnc")
    p.add_argument("--name", help="Optional container name")
    p.add_argument("--timeout", type=int, default=None,
                   help="Optional max seconds for the run process")
    p.set_defaults(func=cmd_run)

    # container
    p = sub.add_parser("container", help="Manage WinForge runtime OCI containers")
    csub = p.add_subparsers(dest="container_command", required=True)

    cp = csub.add_parser("list", help="List available container build definitions")
    cp.set_defaults(func=cmd_container_list)

    cp = csub.add_parser("build", help="Build a runtime container image")
    cp.add_argument("provider",
                    choices=["wine", "staging", "proton-ge"],
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

    # bundle
    p = sub.add_parser("bundle", help="Inspect and verify WinForge execution bundles")
    bsub = p.add_subparsers(dest="bundle_command", required=True)

    bp = bsub.add_parser("inspect", help="Print bundle summary from metadata/graph.json")
    bp.add_argument("bundle", help="Path to WinForge bundle directory")
    bp.set_defaults(func=cmd_bundle_inspect)

    bp = bsub.add_parser("verify", help="Validate bundle contract and graph consistency")
    bp.add_argument("bundle", help="Path to WinForge bundle directory")
    bp.set_defaults(func=cmd_bundle_verify)

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
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
