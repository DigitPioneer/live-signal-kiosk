#!/bin/bash
# X client script run by `startx` (see start-kiosk.sh). Disables screen
# blanking, starts Openbox, then execs the watcher as the session's client
# process so its exit ends the X session (which the outer systemd layer
# then restarts).

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
APP_DIR="${APP_DIR:-$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)}"

# This display must never sleep or blank - it's a fixed kiosk appliance.
xset -dpms
xset s off
xset s noblank

if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.5 -root &
fi

openbox-session &

exec python3 "${APP_DIR}/src/watcher.py"
