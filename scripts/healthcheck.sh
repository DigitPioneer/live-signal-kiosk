#!/bin/bash
# Post-boot update verification/rollback, run by kiosk-healthcheck.service
# after every boot. Runs as root.
#
# On an ordinary boot (no pending update to verify) this does nothing - the
# systemd unit's ConditionPathExists already skips running this script at
# all in that case; the marker check below is a second, explicit safeguard
# so the same guarantee holds even if this script is ever run by hand.
#
# Marker files (all under /etc/live-signal-kiosk/):
#   pending-update-verification - contains the commit hash currently being
#     verified. Written by autoupdate.sh right before it reboots into a
#     newly-installed commit.
#   rollback-attempted - present once a rollback has been performed this
#     cycle. Its mere existence is what caps automatic rollback at one
#     attempt: on the boot immediately after a rollback, this script sees
#     it and does NOT attempt another rollback no matter what happens -
#     see the "second boot" branch below. Do not remove or weaken this
#     check; it's the only thing standing between one bad update and an
#     endless reboot loop.
#
# Note on the two-boot handshake: the FIRST failure branch below deletes
# pending-update-verification (an update attempt is "resolved" once we've
# decided to roll back) but leaves rollback-attempted in place - that
# marker alone is what lets this script (and the systemd unit's own
# ConditionPathExists) still recognize the SECOND boot as one needing a
# look, without re-triggering another rollback.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

LKG_FILE="/etc/live-signal-kiosk/last-known-good-commit"
PENDING_MARKER="/etc/live-signal-kiosk/pending-update-verification"
ROLLBACK_MARKER="/etc/live-signal-kiosk/rollback-attempted"

TIMEOUT_SECONDS=90
POLL_INTERVAL_SECONDS=2

if [[ ! -f "${PENDING_MARKER}" && ! -f "${ROLLBACK_MARKER}" ]]; then
  exit 0
fi

echo "healthcheck.sh: post-update boot detected, waiting up to ${TIMEOUT_SECONDS}s for kiosk.service to become active"

elapsed=0
kiosk_active=0
while (( elapsed < TIMEOUT_SECONDS )); do
  if systemctl is-active --quiet kiosk.service; then
    kiosk_active=1
    break
  fi
  sleep "${POLL_INTERVAL_SECONDS}"
  elapsed=$(( elapsed + POLL_INTERVAL_SECONDS ))
done

# --- Second boot of a recovery cycle: the one-shot rollback already
# happened last boot. Cap enforced here - whatever the outcome now, we
# don't act again, we just report and clean up.

if [[ -f "${ROLLBACK_MARKER}" ]]; then
  if (( kiosk_active )); then
    echo "healthcheck.sh: rollback succeeded - kiosk is active again on the pre-update commit"
  else
    echo "healthcheck.sh: WARNING - the rollback attempt ALSO failed to bring the kiosk up."
    echo "healthcheck.sh: automatic recovery is capped at one attempt; this device needs manual attention."
    echo "healthcheck.sh: see docs/troubleshooting.md and the manual rollback instructions in README.md."
  fi
  rm -f "${PENDING_MARKER}" "${ROLLBACK_MARKER}"
  exit 0
fi

# --- First boot after an update attempt. ------------------------------------

if (( kiosk_active )); then
  pending_commit="$(cat "${PENDING_MARKER}" 2>/dev/null || true)"
  if [[ -n "${pending_commit}" ]]; then
    echo "${pending_commit}" > "${LKG_FILE}"
    echo "healthcheck.sh: update verified - kiosk is active, last-known-good-commit updated to ${pending_commit}"
  else
    echo "healthcheck.sh: update verified - kiosk is active, but the pending-verification marker was empty; last-known-good-commit left unchanged"
  fi
  rm -f "${PENDING_MARKER}"
  exit 0
fi

echo "healthcheck.sh: kiosk.service did not become active within ${TIMEOUT_SECONDS}s - update failed, rolling back"

if [[ ! -f "${LKG_FILE}" ]]; then
  echo "healthcheck.sh: ERROR - no last-known-good-commit recorded, cannot roll back automatically."
  echo "healthcheck.sh: manual intervention required. See docs/troubleshooting.md."
  rm -f "${PENDING_MARKER}"
  exit 1
fi

good_commit="$(cat "${LKG_FILE}")"
echo "healthcheck.sh: rolling back to ${good_commit}"

cd "${APP_DIR}"
git reset --hard "${good_commit}"
"${APP_DIR}/install.sh"

date +%s > "${ROLLBACK_MARKER}"
rm -f "${PENDING_MARKER}"

echo "healthcheck.sh: rebooting to verify the rollback"
reboot
