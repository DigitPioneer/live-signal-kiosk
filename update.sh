#!/bin/bash
# LiveSignal Kiosk updater.
#
# Pulls the latest code, updates yt-dlp, and restarts the kiosk service.
# Never touches /etc/live-signal-kiosk/config.env - your settings persist.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
APP_DIR="${SCRIPT_DIR}"

echo "update.sh: pulling latest code in ${APP_DIR}..."
git -C "${APP_DIR}" pull

YTDLP_PATH="/usr/local/bin/yt-dlp"
if [[ -x "${YTDLP_PATH}" ]]; then
  echo "update.sh: updating yt-dlp..."
  if ! "${YTDLP_PATH}" -U; then
    echo "update.sh: yt-dlp -U failed, re-downloading latest release..."
    curl -fsSL -o "${YTDLP_PATH}.tmp" \
      "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
    mv "${YTDLP_PATH}.tmp" "${YTDLP_PATH}"
    chmod +x "${YTDLP_PATH}"
  fi
else
  echo "update.sh: ${YTDLP_PATH} not found, skipping yt-dlp update"
fi

echo "update.sh: restarting kiosk.service (this will briefly interrupt the display)..."
systemctl restart kiosk.service

echo "update.sh: done."
