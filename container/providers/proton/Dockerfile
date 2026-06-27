# syntax=docker/dockerfile:1
# WinForge Proton OCI Base
#
# Produces the winforge/proton:<version> image — Valve Proton runtime
# extracted from the Steam Linux compatibility tools distribution.
#
# Proton is distributed as a tarball by Valve.  This Dockerfile downloads
# a pinned release, verifies its integrity, and installs it as the
# WinForge runtime provider.
#
# Build:
#   docker build \
#     --build-arg PROTON_VERSION=9.0 \
#     --build-arg PROTON_SHA256=replace-with-actual-sha256 \
#     -t winforge/proton:9.0 \
#     -f container/providers/proton/Dockerfile .

ARG PROTON_VERSION=9.0
ARG PROTON_URL=https://github.com/ValveSoftware/Proton/releases/download/proton-${PROTON_VERSION}/proton-${PROTON_VERSION}.tar.gz
ARG PROTON_SHA256=unverified

FROM debian:bookworm-slim AS base

RUN set -eux; \
    dpkg --add-architecture i386; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        ca-certificates curl \
        xvfb x11-utils xauth \
        cabextract p7zip-full unzip bzip2 xz-utils \
        python3 \
    ; \
    rm -rf /var/lib/apt/lists/*

# ---- Stage 2: Download and verify Proton ----
FROM base AS proton-download
ARG PROTON_VERSION PROTON_URL PROTON_SHA256

WORKDIR /tmp
RUN set -eux; \
    echo "Downloading Proton ${PROTON_VERSION} from ${PROTON_URL}..."; \
    curl -fsSL -o proton.tar.gz "${PROTON_URL}"; \
    if [ "${PROTON_SHA256}" != "unverified" ]; then \
        echo "${PROTON_SHA256}  proton.tar.gz" | sha256sum -c -; \
    else \
        echo "WARNING: Proton SHA256 not provided — skipping integrity check"; \
    fi; \
    mkdir -p /opt/proton; \
    tar -xzf proton.tar.gz -C /opt/proton --strip-components=1; \
    rm proton.tar.gz

# ---- Stage 3: Final image ----
FROM proton-download

# Proton compatibility tool layout for Steam Runtime compatibility
ENV WINFORGE_ROOT=/opt/winforge \
    WINEPREFIX=/opt/winforge/prefix \
    STEAM_COMPAT_DATA_PATH=/opt/winforge/prefix \
    STEAM_COMPAT_CLIENT_INSTALL_PATH=/opt/winforge \
    PROTONPATH=/opt/proton \
    WINEDEBUG=-all \
    DISPLAY=:99

COPY container/common/xvfb-entrypoint.sh /usr/local/bin/xvfb-entrypoint.sh
RUN chmod +x /usr/local/bin/xvfb-entrypoint.sh

WORKDIR /opt/winforge
ENTRYPOINT ["/usr/local/bin/xvfb-entrypoint.sh"]
CMD ["/bin/bash"]
