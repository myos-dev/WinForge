#!/usr/bin/env bash
# WinForge Container Build Script
#
# Builds one or all WinForge Wine/Proton runtime OCI images from
# runtime/catalog.json.
#
# Usage:
#   ./container/build.sh                         # build all CI-enabled catalog entries
#   ./container/build.sh wine default            # build catalog default for wine
#   ./container/build.sh staging 9.0
#   ./container/build.sh proton-ge GE-Proton9-27

set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_CMD="${BUILD_CMD:-docker}"
REGISTRY="${REGISTRY:-}"
PUSH="${PUSH:-}"

build_entry() {
    local provider="$1" version="$2" tag="$3" local_image="$4" dockerfile="$5" build_arg="$6" image_name="$7"
    shift 7

    echo "=== Building ${local_image}:${tag} from catalog ${provider}:${version} ==="
    local cmd=(
        "$BUILD_CMD" build
        --build-arg "$build_arg"
        --tag "${local_image}:${tag}"
    )

    if [ -n "$REGISTRY" ]; then
        cmd+=(--tag "${REGISTRY}/${image_name}:${tag}")
    fi

    cmd+=( -f "$dockerfile" )
    cmd+=( "$@" )
    cmd+=( . )
    "${cmd[@]}"

    if [ -n "$PUSH" ] && [ -n "$REGISTRY" ]; then
        echo "=== Pushing ${REGISTRY}/${image_name}:${tag} ==="
        "$BUILD_CMD" push "${REGISTRY}/${image_name}:${tag}"
    fi
    echo ""
}

if [ $# -eq 0 ]; then
    while IFS=$'\t' read -r provider version tag local_image dockerfile build_arg image_name; do
        [ -n "$provider" ] || continue
        build_entry "$provider" "$version" "$tag" "$local_image" "$dockerfile" "$build_arg" "$image_name"
    done < <(python3 -m runtime.catalog --shell-build-list)
    echo "=== All catalog-enabled containers built ==="
elif [ $# -ge 2 ]; then
    provider="$1"; version="$2"; shift 2
    eval "$(python3 -m runtime.catalog --shell-build-entry "$provider" "$version")"
    build_entry "$CATALOG_PROVIDER" "$CATALOG_VERSION" "$CATALOG_TAG" \
        "$CATALOG_LOCAL_IMAGE" "$CATALOG_DOCKERFILE" \
        "$CATALOG_BUILD_ARG_LINE" "$CATALOG_PUBLISHED_IMAGE_NAME" "$@"
else
    echo "Usage: $0 [provider version]"
    echo "  provider: $(python3 - <<'PY'
from runtime.catalog import list_catalog_providers
print(' | '.join(list_catalog_providers()))
PY
)"
    exit 1
fi
