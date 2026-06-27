# syntax=docker/dockerfile:1
# WinForge GE-Proton OCI Base
#
# Produces the winforge/proton-ge:<tag> image — GloriousEggroll's
# Proton-GE runtime, built from community releases.
#
# Build:
#   docker build \
#     --build-arg GE_PROTON_TAG=GE-Proton9-27 \
#     --build-arg GE_PROTON_SHA256=replace-with-actual-sha256 \
#     -t winforge/proton-ge:GE-Proton9-27 \
#     -f container/providers/proton-ge/Dockerfile .

ARG GE_PROTON_TAG=GE-Proton9-27
ARG GE_PROTON_URL=https://github.com/GloriousEggroll/proton-ge-custom/releases/download/${GE_PROTON_TAG}/${GE_PROTON_TAG}.tar.gz
ARG GE_PROTON_SHA256=unverified

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

FROM base AS ge-download
ARG GE_PROTON_TAG GE_PROTON_URL GE_PROTON_SHA256

WORKDIR /tmp
RUN set -eux; \
    echo "Downloading ${GE_PROTON_TAG}..."; \
    curl -fsSL -o proton-ge.tar.gz "${GE_PROTON_URL}"; \
    if [ "${GE_PROTON_SHA256}" != "unverified" ]; then \
        echo "${GE_PROTON_SHA256}  proton-ge.tar.gz" | sha256sum -c -; \
    else \
        echo "WARNING: GE-Proton SHA256 not provided — skipping integrity check"; \
    fi; \
    mkdir -p /opt/proton-ge; \
    tar -xzf proton-ge.tar.gz -C /opt/proton-ge --strip-components=1; \
    rm proton-ge.tar.gz

FROM ge-download
ENV WINFORGE_ROOT=/opt/winforge WINEPREFIX=/opt/winforge/prefix \
    STEAM_COMPAT_DATA_PATH=/opt/winforge/prefix \
    STEAM_COMPAT_CLIENT_INSTALL_PATH=/opt/winforge \
    PROTONPATH=/opt/proton-ge \
    WINEDEBUG=-all DISPLAY=:99
COPY container/common/xvfb-entrypoint.sh /usr/local/bin/xvfb-entrypoint.sh
RUN chmod +x /usr/local/bin/xvfb-entrypoint.sh
WORKDIR /opt/winforge
ENTRYPOINT ["/usr/local/bin/xvfb-entrypoint.sh"]
CMD ["/bin/bash"]
