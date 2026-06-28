# syntax=docker/dockerfile:1
# WinForge UMU + GE-Proton OCI Base
#
# Produces the winforge/umu-proton-ge:<tag> image — GloriousEggroll's
# GE-Proton runner stack, built from community releases and launched through the UMU capability contract.
#
# Build:
#   docker build \
#     --build-arg GE_PROTON_TAG=GE-Proton9-27 \
#     --build-arg GE_PROTON_SHA256=replace-with-actual-sha256 \
#     --build-arg UMU_LAUNCHER_REF=1.4.0 \
#     -t winforge/umu-proton-ge:GE-Proton9-27 \
#     -f container/providers/umu-proton-ge/Dockerfile .

ARG GE_PROTON_TAG=GE-Proton9-27
ARG GE_PROTON_URL=https://github.com/GloriousEggroll/proton-ge-custom/releases/download/${GE_PROTON_TAG}/${GE_PROTON_TAG}.tar.gz
ARG GE_PROTON_SHA256=unverified
ARG UMU_LAUNCHER_REF=1.4.0

FROM debian:bookworm-slim AS base

RUN set -eux; \
    dpkg --add-architecture i386; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        ca-certificates curl \
        xvfb x11-utils xauth x11vnc websockify novnc \
        cabextract p7zip-full unzip bzip2 xz-utils \
        git python3 python3-venv \
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
ARG UMU_LAUNCHER_REF
RUN set -eux; \
    python3 -m venv /opt/umu; \
    /opt/umu/bin/pip install --no-cache-dir --upgrade pip; \
    /opt/umu/bin/pip install --no-cache-dir "git+https://github.com/Open-Wine-Components/umu-launcher.git@${UMU_LAUNCHER_REF}"; \
    test -x /opt/umu/bin/umu-run; \
    ln -s /opt/umu/bin/umu-run /usr/local/bin/umu-run; \
    /usr/local/bin/umu-run --help >/tmp/umu-run-help.txt
ENV WINFORGE_ROOT=/opt/winforge WINEPREFIX=/opt/winforge/prefix \
    STEAM_COMPAT_DATA_PATH=/opt/winforge/prefix \
    STEAM_COMPAT_CLIENT_INSTALL_PATH=/opt/winforge \
    PROTONPATH=/opt/proton-ge \
    PATH=/opt/umu/bin:$PATH \
    WINEDEBUG=-all DISPLAY=:99
COPY container/common/xvfb-entrypoint.sh /usr/local/bin/xvfb-entrypoint.sh
RUN chmod +x /usr/local/bin/xvfb-entrypoint.sh
WORKDIR /opt/winforge
ENTRYPOINT ["/usr/local/bin/xvfb-entrypoint.sh"]
CMD ["/bin/bash"]
