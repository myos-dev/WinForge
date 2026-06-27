#!/usr/bin/env python3
"""WinForge CLI entrypoint."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from artifact.bundle import create_bundle
from builder.pipeline import build_plan
from core.manifest import ManifestError, load_manifest
from runtime.providers import resolve_runtime

def cmd_inspect(args):
    manifest = load_manifest(Path(args.manifest))
    payload = manifest.to_dict()
    payload["resolvedRuntime"] = resolve_runtime(manifest.runtime).to_dict()
    print(json.dumps(payload, indent=2, sort_keys=True)); return 0

def cmd_plan(args):
    manifest = load_manifest(Path(args.manifest))
    print(json.dumps({"manifest": manifest.name, "version": manifest.version, "phases": build_plan(manifest)}, indent=2)); return 0

def cmd_build(args):
    manifest = load_manifest(Path(args.manifest))
    bundle = create_bundle(manifest, Path(args.output), dry_run=args.dry_run)
    print(json.dumps({"bundle": str(bundle), "dryRun": args.dry_run}, indent=2)); return 0

def build_parser():
    parser = argparse.ArgumentParser(prog="winforge", description="Compile Wine/Proton environment manifests into immutable execution bundles.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("inspect", help="Validate and print normalized manifest with resolved runtime."); p.add_argument("manifest"); p.set_defaults(func=cmd_inspect)
    p = sub.add_parser("plan", help="Print deterministic builder phases."); p.add_argument("manifest"); p.set_defaults(func=cmd_plan)
    p = sub.add_parser("build", help="Create a dry-run immutable bundle directory."); p.add_argument("manifest"); p.add_argument("--output", default="dist"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(func=cmd_build)
    return parser

def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ManifestError as exc:
        print(f"winforge: manifest error: {exc}", file=sys.stderr); return 2
    except FileExistsError as exc:
        print(f"winforge: artifact exists: {exc}", file=sys.stderr); return 3
if __name__ == "__main__":
    raise SystemExit(main())
