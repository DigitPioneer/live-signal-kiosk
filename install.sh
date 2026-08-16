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
  sudo \
  xserver-xorg \
  xinit \
  x11-xserver-utils \
  openbox \
  mpv

# unclutter's package name / availability varies by distro - best effort.
if ! apt-get install -y unclutter; then
  echo "install.sh: warning - could not install unclutter, continuing without it"
fi

# NetworkManager (nmcli) powers the admin panel's Wi-Fi status/scan/connect
# actions. Raspberry Pi OS Bullseye+ ships it by default; best-effort on
# other distros since some may intentionally use a different network stack.
if ! apt-get install -y network-manager; then
  echo "install.sh: warning - could not install network-manager, Wi-Fi actions in the admin panel won't work"
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

# --- 4b. Default slide content (never overwrite an existing install) -------
#
# web/slides.json and web/assets/* are edited live through the slide editor
# and are deliberately NOT tracked in git (see .gitignore) - so
# autoupdate.sh's rollback (`git reset --hard`) can never discard someone's
# slide edits along with reverting broken code, it only ever touches
# tracked code. That also means a fresh git clone no longer ships these
# files at all - seed sensible defaults here, exactly once, same
# never-overwrite pattern as config.env above. Owned by KIOSK_USER (not
# whoever ran `git clone`, which is typically a different, non-kiosk user)
# so the editor - which runs as KIOSK_USER - can actually write to them.

SLIDES_FILE="${APP_DIR}/web/slides.json"
if [[ -f "${SLIDES_FILE}" ]]; then
  echo "install.sh: ${SLIDES_FILE} already exists, leaving it untouched"
else
  cp "${APP_DIR}/web/slides.example.json" "${SLIDES_FILE}"
  echo "install.sh: seeded default slide content at ${SLIDES_FILE}"
fi

ASSETS_DIR="${APP_DIR}/web/assets"
mkdir -p "${ASSETS_DIR}"
LOGO_FILE="${ASSETS_DIR}/src-logo.png"
if [[ -f "${LOGO_FILE}" ]]; then
  echo "install.sh: ${LOGO_FILE} already exists, leaving it untouched"
else
  cp "${APP_DIR}/web/assets/src-logo.example.png" "${LOGO_FILE}"
  echo "install.sh: seeded placeholder logo at ${LOGO_FILE}"
fi

chown "${KIOSK_USER}:${KIOSK_USER}" "${SLIDES_FILE}"
chown -R "${KIOSK_USER}:${KIOSK_USER}" "${ASSETS_DIR}"

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

# kiosk-healthcheck.service verifies a post-auto-update boot and rolls
# back if needed - it's a no-op on every ordinary boot (see its
# ConditionPathExists guard), so it's always installed and enabled
# regardless of whether auto-update itself is turned on.
HEALTHCHECK_SERVICE_SRC="${APP_DIR}/scripts/kiosk-healthcheck.service"
HEALTHCHECK_SERVICE_DEST="/etc/systemd/system/kiosk-healthcheck.service"

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  "${HEALTHCHECK_SERVICE_SRC}" > "${HEALTHCHECK_SERVICE_DEST}"

echo "install.sh: installed ${HEALTHCHECK_SERVICE_DEST}"

AUTOUPDATE_SERVICE_SRC="${APP_DIR}/scripts/kiosk-autoupdate.service"
AUTOUPDATE_SERVICE_DEST="/etc/systemd/system/kiosk-autoupdate.service"

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  "${AUTOUPDATE_SERVICE_SRC}" > "${AUTOUPDATE_SERVICE_DEST}"

echo "install.sh: installed ${AUTOUPDATE_SERVICE_DEST}"

chmod +x \
  "${APP_DIR}/scripts/start-kiosk.sh" \
  "${APP_DIR}/scripts/xsession.sh" \
  "${APP_DIR}/scripts/setup-lite-kiosk.sh" \
  "${APP_DIR}/scripts/silent-boot.sh" \
  "${APP_DIR}/scripts/system-helper.sh" \
  "${APP_DIR}/scripts/toggle-admin-mode.sh" \
  "${APP_DIR}/scripts/autoupdate.sh" \
  "${APP_DIR}/scripts/healthcheck.sh" \
  "${APP_DIR}/src/editor.py" \
  "${APP_DIR}/install.sh" \
  "${APP_DIR}/update.sh"

systemctl daemon-reload
systemctl enable kiosk.service
systemctl enable kiosk-healthcheck.service

# --- 5b. sudoers grant for system-helper.sh (reboot/restart/Wi-Fi) ----------
#
# Narrow, validated grant: the kiosk user may run exactly one script as
# root with no password, and that script (scripts/system-helper.sh)
# whitelists its own arguments. Validated with `visudo -c` before install
# so a bad render can never corrupt sudo's config - if validation fails,
# the file is NOT installed and those admin-panel actions just won't work
# until it's fixed, rather than risking system sudo access.
SUDOERS_SRC="${APP_DIR}/scripts/system-helper.sudoers"
SUDOERS_DEST="/etc/sudoers.d/live-signal-kiosk"
SUDOERS_TMP="$(mktemp)"

sed \
  -e "s#__APP_DIR__#${APP_DIR}#g" \
  -e "s#__KIOSK_USER__#${KIOSK_USER}#g" \
  "${SUDOERS_SRC}" > "${SUDOERS_TMP}"

if visudo -c -f "${SUDOERS_TMP}" >/dev/null 2>&1; then
  install -m 0440 -o root -g root "${SUDOERS_TMP}" "${SUDOERS_DEST}"
  echo "install.sh: installed ${SUDOERS_DEST}"
else
  echo "install.sh: WARNING - generated sudoers file failed validation, NOT installed." >&2
  echo "install.sh: reboot/restart/Wi-Fi-connect actions in the admin panel will not work until this is fixed." >&2
fi
rm -f "${SUDOERS_TMP}"

# --- 5c. Auto-update timer (interval baked in from config.env) --------------
#
# systemd timers can't read config.env at runtime, so the check interval is
# rendered into the unit at install time - re-run install.sh after changing
# AUTOUPDATE_CHECK_INTERVAL_MINUTES to apply a new value. Read in a subshell
# so config.env's other values (KIOSK_USER, passwords, ...) never leak into
# this script's own environment.
AUTOUPDATE_CHECK_INTERVAL_MINUTES="$(. "${CONFIG_FILE}"; echo "${AUTOUPDATE_CHECK_INTERVAL_MINUTES:-60}")"

AUTOUPDATE_TIMER_SRC="${APP_DIR}/scripts/kiosk-autoupdate.timer"
AUTOUPDATE_TIMER_DEST="/etc/systemd/system/kiosk-autoupdate.timer"

sed \
  -e "s#__AUTOUPDATE_CHECK_INTERVAL_MINUTES__#${AUTOUPDATE_CHECK_INTERVAL_MINUTES}#g" \
  "${AUTOUPDATE_TIMER_SRC}" > "${AUTOUPDATE_TIMER_DEST}"

echo "install.sh: installed ${AUTOUPDATE_TIMER_DEST} (check interval: ${AUTOUPDATE_CHECK_INTERVAL_MINUTES}m)"

systemctl daemon-reload

# Only enabled if AUTOUPDATE_ENABLED=true in config.env at install time -
# flip that value and re-run install.sh to turn auto-update on or off; no
# manual systemctl enable/disable needed.
AUTOUPDATE_ENABLED_VALUE="$(. "${CONFIG_FILE}"; echo "${AUTOUPDATE_ENABLED:-false}")"
AUTOUPDATE_WINDOW_START_VALUE="$(. "${CONFIG_FILE}"; echo "${AUTOUPDATE_WINDOW_START:-2}")"
AUTOUPDATE_WINDOW_END_VALUE="$(. "${CONFIG_FILE}"; echo "${AUTOUPDATE_WINDOW_END:-4}")"
if [[ "${AUTOUPDATE_ENABLED_VALUE}" == "true" ]]; then
  systemctl enable --now kiosk-autoupdate.timer
  echo "install.sh: kiosk-autoupdate.timer ENABLED (AUTOUPDATE_ENABLED=true in ${CONFIG_FILE})"
else
  systemctl disable --now kiosk-autoupdate.timer >/dev/null 2>&1 || true
  echo "install.sh: kiosk-autoupdate.timer not enabled (AUTOUPDATE_ENABLED=${AUTOUPDATE_ENABLED_VALUE} in ${CONFIG_FILE})"
fi

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

With a keyboard plugged directly into this device, press Ctrl+Alt+Escape to
break out to a local admin session (same login as above) even with no
network - useful for fixing Wi-Fi on first setup. Press it again, or use the
"Return to Kiosk Display" button, to go back. See README.md for details.

Auto-update: kiosk-autoupdate.timer is $( [[ "${AUTOUPDATE_ENABLED_VALUE}" == "true" ]] && echo "ENABLED (checking every ${AUTOUPDATE_CHECK_INTERVAL_MINUTES}m, window ${AUTOUPDATE_WINDOW_START_VALUE}:00-${AUTOUPDATE_WINDOW_END_VALUE}:00)" || echo "off" ).
To turn it on/off, edit AUTOUPDATE_ENABLED in ${CONFIG_FILE} and re-run
install.sh. See README.md's "Auto-update" section before enabling this -
it reboots the device unattended, inside a configured maintenance window,
with automatic rollback if the update doesn't come up cleanly.
EOF
