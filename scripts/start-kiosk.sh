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

exec startx "${APP_DIR}/scripts/xsession.sh" -- vt1 -nocursor
