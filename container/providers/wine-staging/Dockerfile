# syntax=docker/dockerfile:1
# WinForge Wine Staging OCI Base
#
# Produces the winforge/wine-staging:<version> image — Wine Staging
# variant with the staging patchset applied on top of the stable release.
#
# Build:
#   docker build \
#     --build-arg WINE_VERSION=9.0 \
#     -t winforge/wine-staging:9.0 \
#     -f container/providers/wine-staging/Dockerfile .

ARG WINE_VERSION=9.0

# ---- Stage 1: Base system ----
FROM debian:bookworm-slim AS base

RUN set -eux; \
    dpkg --add-architecture i386; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        ca-certificates curl gnupg \
        xvfb x11-utils xauth x11vnc websockify novnc \
        cabextract p7zip-full unzip bzip2 xz-utils \
        fontconfig python3 \
    ; \
    rm -rf /var/lib/apt/lists/*

# ---- Stage 2: WineHQ staging install ----
FROM base AS wine-install
ARG WINE_VERSION

RUN set -eux; \
    curl -fsSL https://dl.winehq.org/wine-builds/winehq.key | \
        gpg --dearmor -o /etc/apt/keyrings/winehq-archive.key; \
    DISTRO=$(dpkg-status -f '${VERSION_CODENAME}' /etc/os-release 2>/dev/null || echo "bookworm"); \
    curl -fsSL "https://dl.winehq.org/wine-builds/debian/dists/${DISTRO}/winehq-${DISTRO}.sources" \
        -o /etc/apt/sources.list.d/winehq.sources; \
    apt-get update -qq; \
    apt-get install -y -qq --no-install-recommends \
        winehq-staging \
    ; \
    curl -fsSL "https://raw.githubusercontent.com/Winetricks/winetricks/master/src/winetricks" \
        -o /usr/local/bin/winetricks; \
    chmod +x /usr/local/bin/winetricks; \
    wine --version; \
    winetricks --version; \
    rm -rf /var/lib/apt/lists/*

FROM wine-install
ENV WINFORGE_ROOT=/opt/winforge WINEPREFIX=/opt/winforge/prefix WINEARCH=win64 \
    WINEDEBUG=-all WINEDLLOVERRIDES="mscoree,mshtml=" DISPLAY=:99
COPY container/common/xvfb-entrypoint.sh /usr/local/bin/xvfb-entrypoint.sh
COPY container/common/wine-env.sh /opt/winforge/common/wine-env.sh
RUN chmod +x /usr/local/bin/xvfb-entrypoint.sh && fc-cache -fv 2>/dev/null || true
WORKDIR /opt/winforge
ENTRYPOINT ["/usr/local/bin/xvfb-entrypoint.sh"]
CMD ["/bin/bash"]
