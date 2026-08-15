#!/bin/bash
# Root-privileged helper invoked via passwordless sudo by the LiveSignal
# Kiosk editor (src/editor.py), for the small set of system actions the
# kiosk user isn't otherwise allowed to perform (reboot, restarting
# kiosk.service, connecting to Wi-Fi). This script is the ONLY thing
# granted NOPASSWD sudo access - see scripts/system-helper.sudoers,
# installed by install.sh - so it validates its own arguments rather than
# trusting the sudoers grant alone. Arguments are passed through to the
# underlying commands as argv, never interpolated into a shell string, so
# there's no shell-injection path through ssid/password values.

set -euo pipefail

usage() {
  echo "usage: $0 {reboot|restart-kiosk|wifi-connect <ssid> [password]}" >&2
  exit 2
}

if [[ $# -lt 1 ]]; then
  usage
fi

case "$1" in
  reboot)
    [[ $# -eq 1 ]] || usage
    exec systemctl reboot
    ;;
  restart-kiosk)
    [[ $# -eq 1 ]] || usage
    exec systemctl restart kiosk.service
    ;;
  wifi-connect)
    if [[ $# -lt 2 || $# -gt 3 ]]; then
      usage
    fi
    ssid="$2"
    password="${3:-}"
    if [[ -n "${password}" ]]; then
      exec nmcli device wifi connect "${ssid}" password "${password}"
    else
      exec nmcli device wifi connect "${ssid}"
    fi
    ;;
  *)
    usage
    ;;
esac
