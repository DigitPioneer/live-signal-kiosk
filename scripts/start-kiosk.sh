#!/bin/bash
# systemd ExecStart entrypoint for kiosk.service.
#
# Takes over tty1 directly (no getty, no display manager) and starts an X
# session running Openbox + the watcher. Restart is handled entirely by
# systemd (Restart=always on kiosk.service) - this script does not loop.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

export APP_DIR

# No -nocursor here: that disables the cursor at the X server level for
# every application, permanently, which would make a windowed admin
# browser (see the admin-mode breakout in the watcher) unusable with a
# mouse. Cursor-hiding during normal kiosk operation relies solely on
# unclutter's idle-based hiding (started in xsession.sh), which still
# hides the cursor exactly as before - it just isn't destroyed outright.
exec startx "${APP_DIR}/scripts/xsession.sh" -- vt1
