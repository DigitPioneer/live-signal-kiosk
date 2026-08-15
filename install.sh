#!/bin/bash
# LiveSignal Kiosk installer.
#
# Idempotent - safe to re-run. Works on Raspberry Pi OS Lite 64-bit
# (primary target) and generic Debian/Ubuntu (secondary target); Pi-only
# steps live in scripts/setup-lite-kiosk.sh and only run when Pi hardware
# is detected.

set -euo pipefail

# --- Require root, re-exec with sudo if needed ------------------------------

if [[ "${EUID}" -ne 0 ]]; then
  echo "install.sh: this script must run as root, re-executing with sudo..."
  exec sudo -E "$0" "$@"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
APP_DIR="${SCRIPT_DIR}"

KIOSK_USER="${KIOSK_USER:-kiosk}"

echo "install.sh: installing LiveSignal Kiosk from ${APP_DIR} (user: ${KIOSK_USER})"

# --- 1. Packages -------------------------------------------------------------

echo "install.sh: updating apt package lists..."
apt-get update -y

echo "install.sh: installing base packages..."
apt-get install -y \
  python3 \
  git \
  curl \
  xserver-xorg \
  xinit \
  x11-xserver-utils \
  openbox \
  mpv

# unclutter's package name / availability varies by distro - best effort.
if ! apt-get install -y unclutter; then
  echo "install.sh: warning - could not install unclutter, continuing without it"
fi

# Chromium's package name varies by distro/version - try both.
if ! apt-get install -y chromium-browser; then
  echo "install.sh: chromium-browser not available, trying chromium..."
  if ! apt-get install -y chromium; then
    echo "install.sh: warning - could not install a Chromium package, install one manually"
  fi
fi

# --- 2. yt-dlp standalone binary ---------------------------------------------

YTDLP_PATH="/usr/local/bin/yt-dlp"
echo "install.sh: installing/updating yt-dlp at ${YTDLP_PATH}..."
curl -fsSL -o "${YTDLP_PATH}.tmp" \
  "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
mv "${YTDLP_PATH}.tmp" "${YTDLP_PATH}"
chmod +x "${YTDLP_PATH}"

# --- 3. Kiosk system user ----------------------------------------------------

if id "${KIOSK_USER}" >/dev/null 2>&1; then
  echo "install.sh: user ${KIOSK_USER} already exists"
else
  echo "install.sh: creating user ${KIOSK_USER}..."
  useradd --create-home --shell /bin/bash "${KIOSK_USER}"
fi

for group in video render input tty; do
  if getent group "${group}" >/dev/null 2>&1; then
    usermod -aG "${group}" "${KIOSK_USER}"
  fi
done

# --- 4. Config file (never overwrite an existing install) -------------------

CONFIG_DIR="/etc/live-signal-kiosk"
CONFIG_FILE="${CONFIG_DIR}/config.env"

mkdir -p "${CONFIG_DIR}"
if [[ -f "${CONFIG_FILE}" ]]; then
  echo "install.sh: ${CONFIG_FILE} already exists, leaving it untouched"
else
  cp "${APP_DIR}/config.example.env" "${CONFIG_FILE}"
  echo "install.sh: installed default config to ${CONFIG_FILE}"
fi

# --- 5. systemd units -----------------------------------------------------

SERVICE_SRC="${APP_DIR}/scripts/kiosk.service"
SERVICE_DEST="/etc/systemd/system/kiosk.service"

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  -e "s#__KIOSK_USER__#${KIOSK_USER}#g" \
  "${SERVICE_SRC}" > "${SERVICE_DEST}"

echo "install.sh: installed ${SERVICE_DEST}"

# kiosk-editor.service is installed but deliberately NOT enabled/started -
# it's an optional, LAN-facing tool only needed while actively editing
# slides, so it shouldn't be an always-on open port by default.
EDITOR_SERVICE_SRC="${APP_DIR}/scripts/kiosk-editor.service"
EDITOR_SERVICE_DEST="/etc/systemd/system/kiosk-editor.service"

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  -e "s#__KIOSK_USER__#${KIOSK_USER}#g" \
  "${EDITOR_SERVICE_SRC}" > "${EDITOR_SERVICE_DEST}"

echo "install.sh: installed ${EDITOR_SERVICE_DEST} (not enabled - see summary below)"

chmod +x \
  "${APP_DIR}/scripts/start-kiosk.sh" \
  "${APP_DIR}/scripts/xsession.sh" \
  "${APP_DIR}/scripts/setup-lite-kiosk.sh" \
  "${APP_DIR}/scripts/silent-boot.sh" \
  "${APP_DIR}/src/editor.py" \
  "${APP_DIR}/install.sh" \
  "${APP_DIR}/update.sh"

systemctl daemon-reload
systemctl enable kiosk.service

# --- 6. Log directory ---------------------------------------------------------

LOG_DIR="/var/log/live-signal-kiosk"
mkdir -p "${LOG_DIR}"
chown "${KIOSK_USER}:${KIOSK_USER}" "${LOG_DIR}"

# --- 7. Raspberry Pi-specific firmware setup ----------------------------------

IS_PI=0
if [[ -f /proc/device-tree/model ]] && grep -qi "raspberry pi" /proc/device-tree/model 2>/dev/null; then
  IS_PI=1
elif [[ -f /boot/firmware/config.txt ]]; then
  IS_PI=1
fi

if [[ "${IS_PI}" -eq 1 ]]; then
  echo "install.sh: Raspberry Pi hardware detected, running setup-lite-kiosk.sh..."
  "${APP_DIR}/scripts/setup-lite-kiosk.sh"
else
  echo "install.sh: non-Pi hardware detected, skipping setup-lite-kiosk.sh"
fi

# --- 8. Summary ----------------------------------------------------------------

cat <<EOF

install.sh: done.

  Installed to:     ${APP_DIR}
  Config file:       ${CONFIG_FILE} (edit this to set your YouTube channel)
  Systemd service:   ${SERVICE_DEST}
  Logs:               ${LOG_DIR}/watcher.log  (also: journalctl -u kiosk -f)

Next steps:
  1. Edit ${CONFIG_FILE} and set CHANNEL_LIVE_URL to your channel.
  2. Replace ${APP_DIR}/web/assets/src-logo.png with your church's logo
     (keep the same filename).
  3. Edit ${APP_DIR}/web/slides.json with your announcements.
  4. Start it now with:   systemctl start kiosk
     ...or just reboot - the service is enabled and will start automatically.

Optional: run ${APP_DIR}/scripts/silent-boot.sh separately if you want to
hide most of the Linux boot text before the kiosk appears.

Optional: a browser-based slide editor is installed but OFF by default
(it's a LAN-facing tool with its own login, only needed while actively
editing - no reason to leave it running otherwise). Turn it on with:
    sudo systemctl enable --now kiosk-editor
...then browse to http://<this-device-ip>:8766 (default credentials are in
${CONFIG_FILE} as EDITOR_USERNAME/EDITOR_PASSWORD - change EDITOR_PASSWORD
from the default before exposing it on your network). Turn it back off with:
    sudo systemctl disable --now kiosk-editor
EOF
