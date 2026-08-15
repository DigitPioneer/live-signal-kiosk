#!/bin/bash
# Raspberry Pi-specific firmware/config.txt tweaks. Run automatically by
# install.sh when Pi hardware is detected. Safe to re-run (idempotent).
#
# Handles things generic Debian/Ubuntu doesn't need:
#   - hdmi_force_hotplug=1: output works even if the TV is off/not detected
#     at boot, which matters for a device that boots before someone turns
#     the TV on.
#   - A sane GPU memory split for hardware video decode.
#   - disable_splash=1 if not already set.

set -euo pipefail

CONFIG_TXT=""
for candidate in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "${candidate}" ]]; then
    CONFIG_TXT="${candidate}"
    break
  fi
done

if [[ -z "${CONFIG_TXT}" ]]; then
  echo "setup-lite-kiosk.sh: no config.txt found, skipping Pi firmware setup" >&2
  exit 0
fi

echo "setup-lite-kiosk.sh: using ${CONFIG_TXT}"

backup_once() {
  local file="$1"
  local backup="${file}.bak.$(date +%Y%m%d%H%M%S)"
  cp -p "${file}" "${backup}"
  echo "setup-lite-kiosk.sh: backed up ${file} -> ${backup}"
}

ensure_kv() {
  # Ensure KEY=VALUE exists in the given file, adding it if missing and
  # updating it in place if a differing value is present. Idempotent.
  local file="$1"
  local key="$2"
  local value="$3"

  if grep -qE "^[[:space:]]*${key}=" "${file}"; then
    if grep -qE "^[[:space:]]*${key}=${value}[[:space:]]*$" "${file}"; then
      echo "setup-lite-kiosk.sh: ${key}=${value} already set"
      return
    fi
    sed -i -E "s/^[[:space:]]*${key}=.*/${key}=${value}/" "${file}"
    echo "setup-lite-kiosk.sh: updated ${key}=${value}"
  else
    echo "${key}=${value}" >> "${file}"
    echo "setup-lite-kiosk.sh: added ${key}=${value}"
  fi
}

NEEDS_CHANGE=0
for kv in "hdmi_force_hotplug=1" "gpu_mem=128" "disable_splash=1"; do
  key="${kv%%=*}"
  value="${kv#*=}"
  if ! grep -qE "^[[:space:]]*${key}=${value}[[:space:]]*$" "${CONFIG_TXT}"; then
    NEEDS_CHANGE=1
  fi
done

if [[ "${NEEDS_CHANGE}" -eq 1 ]]; then
  backup_once "${CONFIG_TXT}"
fi

ensure_kv "${CONFIG_TXT}" "hdmi_force_hotplug" "1"
ensure_kv "${CONFIG_TXT}" "gpu_mem" "128"
ensure_kv "${CONFIG_TXT}" "disable_splash" "1"

echo "setup-lite-kiosk.sh: done"
