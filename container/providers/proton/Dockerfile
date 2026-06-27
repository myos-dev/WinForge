# syntax=docker/dockerfile:1
# WinForge Valve Proton Source OCI Seed
#
# Produces the winforge/proton:<version> image from Valve Proton's tagged
# source archive. Valve's GitHub releases do not publish prebuilt Proton
# runtime tarballs, so this image is a CI-buildable source-layout seed,
# not yet a runnable Valve Proton binary runtime.
#
# For a prebuilt runnable Proton-family runtime today, use the
# winforge/proton-ge:<tag> image.
#
# Build:
#   docker build \
#     --build-arg PROTON_VERSION=10.0-4 \
#     -t winforge/proton:10.0-4 \
#     -f container/providers/proton/Dockerfile .

ARG PROTON_VERSION=10.0-4
ARG PROTON_TAG=proton-${PROTON_VERSION}
ARG PROTON_SOURCE_URL=https://github.com/ValveSoftware/Proton/archive/refs/tags/${PROTON_TAG}.tar.gz
ARG PROTON_SHA256=unverified

FROM debian:bookworm-slim AS base

RUN set -eux; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        ca-certificates curl \
        xvfb x11-utils xauth \
        cabextract p7zip-full unzip bzip2 xz-utils \
        python3 \
    ; \
    rm -rf /var/lib/apt/lists/*

# ---- Stage 2: Download and verify Valve Proton source archive ----
FROM base AS proton-source
ARG PROTON_VERSION PROTON_TAG PROTON_SOURCE_URL PROTON_SHA256

WORKDIR /tmp
RUN set -eux; \
    echo "Downloading Valve Proton source ${PROTON_TAG} from ${PROTON_SOURCE_URL}..."; \
    curl -fsSL -o proton-source.tar.gz "${PROTON_SOURCE_URL}"; \
    if [ "${PROTON_SHA256}" != "unverified" ]; then \
        echo "${PROTON_SHA256}  proton-source.tar.gz" | sha256sum -c -; \
    else \
        echo "WARNING: Proton source SHA256 not provided — skipping integrity check"; \
    fi; \
    mkdir -p /opt/proton-source; \
    tar -xzf proton-source.tar.gz -C /opt/proton-source --strip-components=1; \
    rm proton-source.tar.gz; \
    test -f /opt/proton-source/proton

# ---- Stage 3: Final image ----
FROM proton-source
ARG PROTON_VERSION PROTON_TAG

ENV WINFORGE_ROOT=/opt/winforge \
    WINEPREFIX=/opt/winforge/prefix \
    STEAM_COMPAT_DATA_PATH=/opt/winforge/prefix \
    STEAM_COMPAT_CLIENT_INSTALL_PATH=/opt/winforge \
    PROTONPATH=/opt/proton-source \
    PROTON_VERSION=${PROTON_VERSION} \
    PROTON_TAG=${PROTON_TAG} \
    WINEDEBUG=-all \
    DISPLAY=:99

COPY container/common/xvfb-entrypoint.sh /usr/local/bin/xvfb-entrypoint.sh
RUN set -eux; \
    chmod +x /usr/local/bin/xvfb-entrypoint.sh; \
    printf '%s\n' \
      '#!/bin/sh' \
      'echo "WinForge Valve Proton image contains upstream source only; use winforge/proton-ge for a prebuilt Proton runtime." >&2' \
      'exit 64' \
      > /usr/local/bin/proton; \
    chmod +x /usr/local/bin/proton

WORKDIR /opt/winforge
ENTRYPOINT ["/usr/local/bin/xvfb-entrypoint.sh"]
CMD ["/bin/bash"]
