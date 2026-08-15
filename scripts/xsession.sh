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

# Seed Openbox's per-user config from our template on first run only (never
# overwrite it afterward, same one-time-copy pattern as config.example.env
# -> /etc/live-signal-kiosk/config.env) - this is what provides the
# admin-mode toggle keybinding (Ctrl+Alt+Escape, see scripts/openbox-rc.xml
# and scripts/toggle-admin-mode.sh).
OPENBOX_RC_DIR="${HOME}/.config/openbox"
OPENBOX_RC="${OPENBOX_RC_DIR}/rc.xml"
if [ ! -f "${OPENBOX_RC}" ]; then
  mkdir -p "${OPENBOX_RC_DIR}"
  sed "s#__APP_DIR__#${APP_DIR}#g" "${APP_DIR}/scripts/openbox-rc.xml" > "${OPENBOX_RC}"
fi

openbox-session &

exec python3 "${APP_DIR}/src/watcher.py"
