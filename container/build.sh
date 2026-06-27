#!/usr/bin/env bash
# WinForge Container Build Script
#
# Builds one or all WinForge Wine/Proton runtime OCI images.
#
# Usage:
#   ./container/build.sh                  # build all providers
#   ./container/build.sh wine 9.0         # build wine:9.0
#   ./container/build.sh wine-staging 9.0
#   ./container/build.sh proton 9.0 --push
#   ./container/build.sh proton-ge GE-Proton9-27

set -euo pipefail
cd "$(dirname "$0")/.."

BUILD_CMD="${BUILD_CMD:-docker}"
REGISTRY="${REGISTRY:-}"
PUSH="${PUSH:-}"

build_one() {
    local provider="$1" version="$2" dockerfile="$3" tag="$4"
    shift 4

    echo "=== Building winforge/${provider}:${tag} ==="
    $BUILD_CMD build \
        --build-arg "${version}" \
        ${REGISTRY:+--tag "${REGISTRY}/winforge/${provider}:${tag}"} \
        --tag "winforge/${provider}:${tag}" \
        -f "$dockerfile" \
        "$@" \
        .

    if [ -n "$PUSH" ] && [ -n "$REGISTRY" ]; then
        echo "=== Pushing ${REGISTRY}/winforge/${provider}:${tag} ==="
        $BUILD_CMD push "${REGISTRY}/winforge/${provider}:${tag}"
    fi
    echo ""
}

if [ $# -eq 0 ]; then
    # Build all providers with default versions
    build_one wine "WINE_VERSION=9.0" container/providers/wine/Dockerfile 9.0
    build_one wine-staging "WINE_VERSION=9.0" container/providers/wine-staging/Dockerfile 9.0
    build_one proton "PROTON_VERSION=10.0-4" container/providers/proton/Dockerfile 10.0-4
    build_one proton-ge "GE_PROTON_TAG=GE-Proton9-27" container/providers/proton-ge/Dockerfile GE-Proton9-27
    echo "=== All containers built ==="
elif [ $# -ge 2 ]; then
    provider="$1"; tag="$2"; shift 2
    case "$provider" in
        wine) build_one wine "WINE_VERSION=${tag}" "container/providers/wine/Dockerfile" "$tag" "$@" ;;
        wine-staging) build_one wine-staging "WINE_VERSION=${tag}" "container/providers/wine-staging/Dockerfile" "$tag" "$@" ;;
        proton) build_one proton "PROTON_VERSION=${tag}" "container/providers/proton/Dockerfile" "$tag" "$@" ;;
        proton-ge) build_one proton-ge "GE_PROTON_TAG=${tag}" "container/providers/proton-ge/Dockerfile" "$tag" "$@" ;;
        *) echo "Unknown provider: $provider"; echo "Valid: wine, wine-staging, proton, proton-ge"; exit 1 ;;
    esac
else
    echo "Usage: $0 [provider version]"
    echo "  provider: wine | wine-staging | proton | proton-ge"
    exit 1
fi
