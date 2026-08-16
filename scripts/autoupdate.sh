#!/bin/bash
# Unattended check-and-update script, run periodically by
# kiosk-autoupdate.timer (see kiosk-autoupdate.service). Runs as root.
#
# Safety model (see README.md's "Auto-update" section for the full
# rationale):
#   - Never touches anything outside the configured maintenance window,
#     checked before anything else in this script.
#   - Never reboots into unvalidated code: sanity checks run on the pulled
#     commit BEFORE deciding to reboot. A failed check reverts the working
#     tree and leaves the device on its current, still-running commit.
#   - Always records a rollback target (last-known-good-commit) before
#     ever pulling, so kiosk-healthcheck.service can automatically recover
#     from a commit that passes these sanity checks but still fails at
#     actual runtime (the exact class of bug this system exists to catch -
#     see docs/troubleshooting.md's note on the WantedBy=graphical.target
#     incident).
#   - Never runs while a previous update's post-boot verification/rollback
#     cycle is still unresolved (see the marker-file guard below) - two
#     overlapping update attempts could corrupt that state machine.

set -uo pipefail
# Deliberately not `set -e`: failures are handled explicitly at each step
# (a failed `git pull` or sanity check must roll back cleanly, not abort
# the script mid-way through and leave things in an undefined state).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"

CONFIG_FILE="/etc/live-signal-kiosk/config.env"
LKG_FILE="/etc/live-signal-kiosk/last-known-good-commit"
PENDING_MARKER="/etc/live-signal-kiosk/pending-update-verification"
ROLLBACK_MARKER="/etc/live-signal-kiosk/rollback-attempted"

if [[ -f "${CONFIG_FILE}" ]]; then
  # config.env is plain KEY="value" shell-compatible syntax (the same file
  # install.sh renders from config.example.env) - safe to source directly
  # since it's a root-owned file under this device's own admin control,
  # and this script already runs as root.
  # shellcheck disable=SC1090
  source "${CONFIG_FILE}"
fi

# --- 1. Maintenance window check - first, before anything else -------------

WINDOW_START="${AUTOUPDATE_WINDOW_START:-2}"
WINDOW_END="${AUTOUPDATE_WINDOW_END:-4}"
current_hour=$((10#$(date +%H)))

in_window=0
if (( WINDOW_START <= WINDOW_END )); then
  if (( current_hour >= WINDOW_START && current_hour < WINDOW_END )); then
    in_window=1
  fi
else
  # Window wraps midnight (e.g. START=23 END=1).
  if (( current_hour >= WINDOW_START || current_hour < WINDOW_END )); then
    in_window=1
  fi
fi

if (( ! in_window )); then
  exit 0
fi

# --- 2. Enabled check ---------------------------------------------------------

if [[ "${AUTOUPDATE_ENABLED:-false}" != "true" ]]; then
  exit 0
fi

# --- Guard: refuse to run while a previous update's post-boot
# verification/rollback cycle hasn't resolved yet. Stacking a new update
# attempt on top of an unresolved one could corrupt that state machine
# (see scripts/healthcheck.sh).

if [[ -f "${PENDING_MARKER}" || -f "${ROLLBACK_MARKER}" ]]; then
  echo "autoupdate.sh: a previous update is still awaiting post-boot verification/rollback, skipping this cycle"
  exit 0
fi

# --- 3. Check for new commits -------------------------------------------------

cd "${APP_DIR}"

if ! branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"; then
  echo "autoupdate.sh: ${APP_DIR} is not a git repository, cannot auto-update"
  exit 1
fi
if [[ "${branch}" == "HEAD" ]]; then
  echo "autoupdate.sh: repo is in a detached HEAD state, refusing to auto-update"
  exit 1
fi

if ! git fetch --quiet origin "${branch}"; then
  echo "autoupdate.sh: git fetch failed, skipping this cycle"
  exit 1
fi

local_head="$(git rev-parse HEAD)"
remote_head="$(git rev-parse "origin/${branch}")"

if [[ "${local_head}" == "${remote_head}" ]]; then
  echo "autoupdate.sh: already up to date (${local_head}), nothing to do"
  exit 0
fi

echo "autoupdate.sh: new commit available (${local_head} -> ${remote_head}), updating"

# --- 4. Record the rollback target BEFORE touching anything else -----------
#
# This must be the most recent commit already confirmed to boot
# successfully - i.e. exactly what's checked out right now, since we just
# verified (above) that no previous cycle is still pending/mid-rollback.

echo "${local_head}" > "${LKG_FILE}"

# --- 5. Pull -------------------------------------------------------------------

if ! git pull --quiet origin "${branch}"; then
  echo "autoupdate.sh: git pull failed, resetting back to ${local_head}"
  git reset --hard "${local_head}"
  exit 1
fi

# --- 6. Sanity checks on the new code ------------------------------------------

sanity_failed=0
sanity_reason=""
sanity_log="$(mktemp)"

while IFS= read -r -d '' pyfile; do
  if ! python3 -m py_compile "${pyfile}" >"${sanity_log}" 2>&1; then
    sanity_failed=1
    sanity_reason="python3 -m py_compile failed on ${pyfile}: $(cat "${sanity_log}")"
    break
  fi
done < <(find "${APP_DIR}" -name "*.py" -not -path "*/.git/*" -print0)

if (( ! sanity_failed )); then
  while IFS= read -r -d '' shfile; do
    if ! bash -n "${shfile}" >"${sanity_log}" 2>&1; then
      sanity_failed=1
      sanity_reason="bash -n failed on ${shfile}: $(cat "${sanity_log}")"
      break
    fi
  done < <(find "${APP_DIR}" -name "*.sh" -not -path "*/.git/*" -print0)
fi

if (( ! sanity_failed )) \
   && git diff --name-only "${local_head}" "${remote_head}" | grep -qx "web/slides.json"; then
  if ! python3 -m json.tool "${APP_DIR}/web/slides.json" >"${sanity_log}" 2>&1; then
    sanity_failed=1
    sanity_reason="web/slides.json is not valid JSON after update: $(cat "${sanity_log}")"
  fi
fi

rm -f "${sanity_log}"

if (( sanity_failed )); then
  echo "autoupdate.sh: sanity check FAILED - ${sanity_reason}"
  echo "autoupdate.sh: reverting to ${local_head}, NOT rebooting"
  git reset --hard "${local_head}"
  exit 1
fi

echo "autoupdate.sh: sanity checks passed (py_compile, bash -n, slides.json)"

# --- 7. Install ------------------------------------------------------------------

if ! "${APP_DIR}/install.sh"; then
  echo "autoupdate.sh: install.sh failed on the new commit, reverting to ${local_head}, NOT rebooting"
  git reset --hard "${local_head}"
  "${APP_DIR}/install.sh" \
    || echo "autoupdate.sh: WARNING - install.sh also failed while reverting, manual check needed"
  exit 1
fi

# --- 8. Write the pending-verification marker -----------------------------------
#
# kiosk-healthcheck.service checks for this on the next boot to know
# whether it needs to actually verify anything.

echo "${remote_head}" > "${PENDING_MARKER}"

# --- 9. Reboot ---------------------------------------------------------------------

echo "autoupdate.sh: rebooting to verify ${remote_head}"
reboot
