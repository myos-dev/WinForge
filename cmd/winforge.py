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
from builder.executor import execute_inside_container
from builder.pipeline import build_plan
from container.manager import (
    build_container,
    list_definitions,
    get_image_ref,
)
from core.manifest import ManifestError, RuntimeSpec, load_manifest
from runtime.providers import list_providers, resolve_runtime


# ---- manifest commands ----


def cmd_inspect(args):
    manifest = load_manifest(Path(args.manifest))
    payload = manifest.to_dict()
    payload["resolvedRuntime"] = resolve_runtime(manifest.runtime).to_dict()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_plan(args):
    manifest = load_manifest(Path(args.manifest))
    result = {
        "manifest": manifest.name,
        "version": manifest.version,
        "runtimeProvider": manifest.runtime.provider,
        "runtimeVersion": manifest.runtime.version,
        "ociImage": get_image_ref(manifest.runtime.provider,
                                  manifest.runtime.version),
        "phases": build_plan(manifest),
    }
    print(json.dumps(result, indent=2))
    return 0


def cmd_build(args):
    manifest = load_manifest(Path(args.manifest))
    bundle_path = create_bundle(
        manifest, Path(args.output), dry_run=args.dry_run,
    )

    base_image = get_image_ref(manifest.runtime.provider,
                               manifest.runtime.version)

    if args.dry_run:
        oci = build_oci_image(bundle_path, base_image,
                              output_tag=args.image_tag)
        result = {
            "bundle": str(bundle_path),
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

    # container
    p = sub.add_parser("container", help="Manage WinForge runtime OCI containers")
    csub = p.add_subparsers(dest="container_command", required=True)

    cp = csub.add_parser("list", help="List available container build definitions")
    cp.set_defaults(func=cmd_container_list)

    cp = csub.add_parser("build", help="Build a runtime container image")
    cp.add_argument("provider",
                    choices=["wine", "staging", "proton", "proton-ge"],
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
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
