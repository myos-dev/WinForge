# syntax=docker/dockerfile:1
# WinForge Wine Stable OCI Base
#
# Produces the winforge/wine:<version> image — a headless Wine Stable
# runtime suitable for prefix construction and headless application
# execution inside the WinForge builder pipeline.
#
# Build:
#   docker build \
#     --build-arg WINE_PACKAGE_VERSION=11.0.0.0~bookworm-1 \
#     -t winforge/wine:11.0 \
#     -f container/providers/wine/Dockerfile .
#
# Architecture: amd64 (Wine Stable does not provide arm64 binaries)

ARG WINE_PACKAGE_VERSION=11.0.0.0~bookworm-1

# ---- Stage 1: Base system dependencies ----
FROM debian:bookworm-slim AS base

RUN set -eux; \
    dpkg --add-architecture i386; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        ca-certificates curl gnupg software-properties-common \
        xvfb x11-utils xauth x11vnc websockify novnc \
        cabextract p7zip-full unzip bzip2 xz-utils \
        fontconfig \
        # Build / scripting
        python3 git \
        # Rust toolchain for powershell-wrapper-for-wine
        cargo rustc \
    ; \
    rm -rf /var/lib/apt/lists/*

# ---- Stage 2: WineHQ repository + Wine install ----
FROM base AS wine-install
ARG WINE_PACKAGE_VERSION

RUN set -eux; \
    # Add WineHQ signing key and repo
    curl -fsSL https://dl.winehq.org/wine-builds/winehq.key | \
        gpg --dearmor -o /etc/apt/keyrings/winehq-archive.key; \
    DISTRO=$(dpkg-status -f '${VERSION_CODENAME}' /etc/os-release 2>/dev/null || echo "bookworm"); \
    curl -fsSL "https://dl.winehq.org/wine-builds/debian/dists/${DISTRO}/winehq-${DISTRO}.sources" \
        -o /etc/apt/sources.list.d/winehq.sources; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        "winehq-stable=${WINE_PACKAGE_VERSION}" \
        "wine-stable=${WINE_PACKAGE_VERSION}" \
        "wine-stable-amd64=${WINE_PACKAGE_VERSION}" \
        "wine-stable-i386:i386=${WINE_PACKAGE_VERSION}" \
    ; \
    # Install winetricks
    curl -fsSL "https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks" \
        -o /usr/local/bin/winetricks; \
    chmod +x /usr/local/bin/winetricks; \
    # Verify installations
    wine --version; \
    winetricks --version; \
    rm -rf /var/lib/apt/lists/*

# ---- Stage 3: Final image ----
FROM wine-install

# WinForge paths
ENV WINFORGE_ROOT=/opt/winforge \
    WINEPREFIX=/opt/winforge/prefix \
    WINEARCH=win64 \
    WINEDEBUG=-all \
    WINEDLLOVERRIDES="mscoree,mshtml=" \
    WINEDBG=-all \
    DISPLAY=:99

# Common scripts
COPY container/common/xvfb-entrypoint.sh /usr/local/bin/xvfb-entrypoint.sh
COPY container/common/wine-env.sh /opt/winforge/common/wine-env.sh
RUN chmod +x /usr/local/bin/xvfb-entrypoint.sh

# Minimal font cache
RUN fc-cache -fv 2>/dev/null || true

WORKDIR /opt/winforge

ENTRYPOINT ["/usr/local/bin/xvfb-entrypoint.sh"]
CMD ["/bin/bash"]
