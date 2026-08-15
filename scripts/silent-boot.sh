#!/bin/bash
# Optional, purely cosmetic "hide most boot text" step for Raspberry Pi OS.
# NOT run automatically by install.sh - run it manually if you want a
# cleaner boot sequence.
#
# This hides MOST boot text but may not hide every early firmware message
# that prints before the kernel loads. It is easily reversible: both files
# this script touches are backed up with a timestamp before editing.

set -euo pipefail

CMDLINE_TXT=""
for candidate in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  if [[ -f "${candidate}" ]]; then
    CMDLINE_TXT="${candidate}"
    break
  fi
done

CONFIG_TXT=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "${candidate}" ]]; then
    CONFIG_TXT="${candidate}"
    break
  fi
done

if [[ -z "${CMDLINE_TXT}" || -z "${CONFIG_TXT}" ]]; then
  echo "silent-boot.sh: could not find cmdline.txt / config.txt, aborting" >&2
  exit 1
fi

backup_once() {
  local file="$1"
  local backup="${file}.bak.$(date +%Y%m%d%H%M%S)"
  cp -p "${file}" "${backup}"
  echo "silent-boot.sh: backed up ${file} -> ${backup}"
}

echo "silent-boot.sh: using ${CMDLINE_TXT} and ${CONFIG_TXT}"

# --- cmdline.txt: must stay exactly one line -------------------------------

# Read the existing single line of params.
existing_line="$(head -n 1 "${CMDLINE_TXT}")"

# Params we want present, in order. add_or_replace_param mutates
# `existing_line` in place via a temp variable.
declare -a wanted_params=(
  "quiet"
  "splash"
  "loglevel=0"
  "logo.nologo"
  "vt.global_cursor_default=0"
)

new_line="${existing_line}"

for param in "${wanted_params[@]}"; do
  key="${param%%=*}"
  if [[ "${param}" == *"="* ]]; then
    # key=value style param: replace an existing key=whatever, or append.
    if echo "${new_line}" | grep -qE "(^| )${key}=[^ ]*"; then
      new_line="$(echo "${new_line}" | sed -E "s/(^| )${key}=[^ ]*/\1${param}/")"
    else
      new_line="${new_line} ${param}"
    fi
  else
    # bare flag: append only if not already present.
    if ! echo "${new_line}" | grep -qE "(^| )${param}( |\$)"; then
      new_line="${new_line} ${param}"
    fi
  fi
done

# Collapse any accidental double spaces and trim, keep as ONE line.
new_line="$(echo "${new_line}" | tr -s ' ' | sed -E 's/^ +| +$//g')"

if [[ "${new_line}" == "${existing_line}" ]]; then
  echo "silent-boot.sh: ${CMDLINE_TXT} already up to date, leaving as-is"
else
  backup_once "${CMDLINE_TXT}"
  printf '%s' "${new_line}" > "${CMDLINE_TXT}"
  echo "silent-boot.sh: wrote ${CMDLINE_TXT} (single line, $(wc -c < "${CMDLINE_TXT}") bytes)"
fi

# --- config.txt: add disable_splash=1 if missing ---------------------------

if ! grep -qE "^[[:space:]]*disable_splash=1[[:space:]]*$" "${CONFIG_TXT}"; then
  backup_once "${CONFIG_TXT}"
  echo "disable_splash=1" >> "${CONFIG_TXT}"
  echo "silent-boot.sh: added disable_splash=1 to ${CONFIG_TXT}"
else
  echo "silent-boot.sh: disable_splash=1 already present in ${CONFIG_TXT}"
fi

echo ""
echo "silent-boot.sh: done."
echo "This hides MOST boot text, but some early firmware messages may still"
echo "print before the kernel loads. To revert, restore the .bak files this"
echo "script created next to ${CMDLINE_TXT} and ${CONFIG_TXT}."
