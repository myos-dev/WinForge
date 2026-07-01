#!/usr/bin/env bash
# WinForge Xvfb Entrypoint
#
# Starts a virtual X server for headless Wine/Proton execution,
# then runs the provided command (typically a winforge builder step).
#
# Derived from the MTGO / videreproject headless Wine container pattern.
#
# Environment:
#   DISPLAY       - X display number (default: :99)
#   WINE_DISPLAY  - Resolution/bit depth (default: 1024x768x16)
#   WINEDEBUG     - Wine debug channels (default: -all)
#   WINEPREFIX    - Wine prefix path (default: /opt/winforge/prefix)
#   WINEFS        - WinForge execution phase (builder|launcher)

set -euo pipefail

: "${DISPLAY:=:99}"
: "${WINE_DISPLAY:=1024x768x16}"
: "${WINEDEBUG:=-all}"
: "${WINEPREFIX:=/opt/winforge/prefix}"
: "${WINEFS:=launcher}"

export DISPLAY WINEDEBUG WINEPREFIX

# Start virtual framebuffer
Xvfb "$DISPLAY" -screen 0 "$WINE_DISPLAY" -ac &
XVFB_PID=$!

# Wait for Xvfb to be ready
for i in $(seq 1 10); do
    if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
        break
    fi
    sleep 0.3
done

# Ensure prefix directory exists for builder phase
if [ "$WINEFS" = "builder" ]; then
    mkdir -p "$WINEPREFIX"
fi

# Execute the command (builder step or launcher command)
if [ $# -gt 0 ]; then
    exec "$@"
fi

# If no command, keep Xvfb alive (interactive container)
wait "$XVFB_PID"
